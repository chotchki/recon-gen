"""``recon-gen audit`` — PDF reconciliation report.

Three operations:

  apply  — emit Markdown source for the report (default), or
           ``--execute`` to write a PDF via reportlab.
  clean  — list the report file that would be removed (default), or
           ``--execute`` to delete it.
  test   — pytest the audit module + pyright.

The report is a regulator-ready PDF generated **directly from the
database**, querying the per-prefix L1 invariant matviews + base
tables. Same emit-vs-execute pattern as the other artifact groups —
no ``--execute`` means the integrator can review the rendered
Markdown / page outline before committing to a real PDF write.

Phase U.2 ships the **executive summary page** on top of the U.1
cover: per-period totals (transaction count, transfer count, dollar
volume gross/net) + L1 invariant exception counts (drift, ledger
drift, overdraft, limit breach, stuck pending, stuck unbundled,
supersession). Real numbers when ``demo_database_url`` is configured;
graceful "—" placeholders + a notice when it isn't, so the layout
stays previewable without a live DB.

Page footer carries a provenance-fingerprint placeholder (real
fingerprint lands in U.7). Per-invariant violation tables, the
per-account-day Daily Statement walk, and the sign-off block land
in U.3+.

Period default: ``trailing:7`` (a 7-day window ending yesterday).
Override with ``--period <shape>`` — see ``--help`` for the full
accepted-shapes list (``trailing:N``, ``today``, ``yesterday``,
``YYYY-MM-DD..YYYY-MM-DD``, or single ``YYYY-MM-DD``).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import click

from recon_gen.cli.audit._period import period_option
from recon_gen.common.as_of_frame import AsOfFrame
from recon_gen.common.intervals import DateInterval
from recon_gen.cli._helpers import (
    config_option,
    execute_option,
    l2_instance_option,
    resolve_l2_for_demo,
)
from recon_gen.common.money import Cents
from recon_gen.common.pdf.audit_chrome import (
    BookmarkedDocTemplate,
    bookmarked_h1,
    bookmarked_h3,
    make_footer_drawer,
)
from recon_gen.common.provenance import (
    ProvenanceFingerprint,
    compute_provenance,
    hash_table_rows,
    l2_fingerprint_placeholder,
    l2_yaml_sha256,
    recon_gen_code_identity,
)
from recon_gen.common.sql.dialect import date_literal
from recon_gen.common.theme import DEFAULT_PRESET, resolve_l2_theme


def _cents_to_dollars(raw: object) -> Decimal:
    """Read-boundary cents→dollars projection (AO.1 audit slice).

    Money columns in the DB are BIGINT integer cents post-AO.1
    foundation. The audit's renderers (pdf.py / markdown.py) format
    Decimal dollars (``f"${v:,.2f}"``). Wrap every money-cell fetch
    with this helper so the dataclass instances populated here carry
    dollars — the renderers stay unchanged.

    Tolerates ``None`` / ``0`` for matview NULLs (Decimal(0) for
    the missing-data render path). Foreign types (float dust from a
    pre-migration column) would smell loud — Cents.from_db's
    ``int()`` coerce raises TypeError on non-numeric input.
    """
    if raw is None:
        return Decimal(0)
    return Cents.from_db(int(raw)).to_dollars()


def _coerce_to_date(v: object) -> date:
    """Normalize a DB-returned date-ish value to a ``datetime.date``.

    Each dialect's DB-API driver returns DATE columns differently:
    - psycopg (PG) → ``datetime.date``
    - oracledb (Oracle) → ``datetime.datetime`` (always carries a time)
    - sqlite3 (SQLite) → ``str`` ISO-format (no ``detect_types``;
      ``connect_demo_db`` opens SQLite plainly so DATEs come back as text)

    Downstream code calls ``.toordinal()`` / ``+ timedelta`` /
    ``.isoformat()`` on the result, all of which need a real ``date``.
    The pre-2026-05-09 helper only handled the datetime→date case
    (``v.date() if hasattr(v, "date") else v``) — a SQLite ``str`` fell
    through unchanged and blew up in the daily-statement walk sort.
    """
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        # ISO date or datetime prefix — `date.fromisoformat` accepts
        # "YYYY-MM-DD"; for "YYYY-MM-DD HH:MM:SS" take the date part.
        return date.fromisoformat(v[:10])
    raise TypeError(f"cannot coerce {type(v).__name__} ({v!r}) to date")


def _coerce_to_datetime(v: object) -> datetime:
    """Normalize a DB-returned timestamp-ish value to a ``datetime``.

    Same dialect divergence as ``_coerce_to_date`` — SQLite returns
    TIMESTAMP columns as ISO ``str`` (``connect_demo_db`` opens SQLite
    without ``detect_types``) while PG/Oracle return real ``datetime``.
    Downstream code calls ``.strftime(...)`` on these (the daily-
    statement / transaction-walk tables in ``audit/markdown.py`` +
    ``audit/pdf.py``), which needs a real ``datetime``.

    Accepts ``datetime`` (passthrough), ``date`` (→ midnight), and ISO
    strings (``datetime.fromisoformat``; tolerates a trailing ``Z``).
    """
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str):
        s = v.rstrip("Z").strip()
        # `datetime.fromisoformat` handles "YYYY-MM-DD HH:MM:SS[.ffffff]"
        # and a bare "YYYY-MM-DD" (→ midnight). SQLite's CURRENT_TIMESTAMP
        # produces "YYYY-MM-DD HH:MM:SS" which is ISO-compatible.
        return datetime.fromisoformat(s)
    raise TypeError(f"cannot coerce {type(v).__name__} ({v!r}) to datetime")


@click.group()
def audit() -> None:
    """Per-instance PDF reconciliation report (cover, summary, exceptions)."""


def _resolve_period(
    period: DateInterval | None,
    *, today: date | None = None,  # typing-smell: ignore[no-raw-temporal-args]: test-override hook; the staged no-raw-temporal-args lint will allowlist this seam pre-enable
) -> DateInterval:
    """Resolve the report period.

    When ``period`` is supplied (operator passed ``--period <shape>``),
    return it as-is. Otherwise default to ``trailing:7`` — a 7-day
    window ending yesterday (``[today-7, today-1]`` closed-closed). The
    ``today`` arg is a test-override hook; production callers leave it
    None so the default routes through ``AsOfFrame.live()`` (the
    canonical blessed wall-clock seam per AQ.3).
    """
    if period is not None:
        return period
    anchor = today or AsOfFrame.live().as_of
    return DateInterval.trailing_days_ending_yesterday(anchor, 7)


def _institution_name(instance, cfg) -> str:  # type: ignore[no-untyped-def]: instance is L2Instance, cfg is Config — untyped pending audit-CLI sweep
    """Pull the institution display name from the L2 persona block.

    Falls back to the cfg's deployment name when no persona block is
    declared — the report still renders cleanly against any L2 YAML.
    """
    persona = getattr(instance, "persona", None)
    if persona is not None and persona.institution:
        return str(persona.institution[0])
    return str(cfg.deployment_name)


def _singleton_account_ids(instance) -> set[str]:  # type: ignore[no-untyped-def]: instance is L2Instance, untyped pending audit-CLI sweep
    """IDs of L2 ``Account`` singletons (the N-N "shared" accounts).

    Used by the U.3 per-invariant tables to split rows: account_ids
    in this set get rendered as per-account aggregate summaries (a
    GL clearing or concentration account that violates daily would
    otherwise balloon the report); account_ids NOT in this set
    are template-materialized (1-1, customer-owned) and get per-row
    detail.
    """
    return {str(a.id) for a in instance.accounts}


def _internal_singleton_account_ids(
    instance,  # type: ignore[no-untyped-def]: instance is L2Instance, untyped pending audit-CLI sweep
) -> set[str]:
    """IDs of internal-scope L2 ``Account`` singletons only.

    Used by U.4's parent-always-render rule: external counterparty
    singletons (``scope="external"``) are out of the operator's
    books and not in scope for reconciliation walks, so they get
    excluded. Internal-scope singletons (GL clearing, concentration,
    ZBA master) DO get a per-day walk page even when drift is zero.
    """
    return {
        str(a.id) for a in instance.accounts
        if a.scope == "internal"
    }


# -- Executive summary (U.2) --------------------------------------------------


@dataclass(frozen=True)
class ExecSummary:
    """Totals rendered on the executive summary page.

    All counts are inclusive of both period endpoints. Dollar volume
    follows the dashboards' per-transfer aggregation convention
    (``MAX(ABS(amount_money))`` for gross, ``SUM(amount_money)`` for
    net) so a multi-leg transfer counts once, not once per leg.
    """
    transactions_count: int
    transfers_count: int
    dollar_volume_gross: Decimal
    dollar_volume_net: Decimal
    # Ordered (label, count) pairs — preserves render order.
    exception_counts: list[tuple[str, int]]


# (display label, matview suffix, date column for period filter — None
# means "current-state" matview: count all rows regardless of posting
# date, per the L1 dashboard's stuck_* convention).
_EXCEPTION_INVARIANTS: list[tuple[str, str, str | None]] = [
    ("Drift", "drift", "business_day_start"),
    ("Ledger drift", "ledger_drift", "business_day_start"),
    ("Overdraft", "overdraft", "business_day_start"),
    ("Limit breach", "limit_breach", "business_day"),
    ("Stuck pending", "stuck_pending", None),
    ("Stuck unbundled", "stuck_unbundled", None),
]


def _query_executive_summary(
    cfg, instance, period: DateInterval,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> ExecSummary | None:
    """Aggregate the executive-summary totals against the demo DB.

    Returns None when ``cfg.demo_database_url`` is unset — the
    renderers fall back to "—" placeholders so the layout stays
    previewable without a live connection.

    Date literals are emitted via ``date_literal(value, dialect)`` from
    ``common/sql/dialect.py`` — Postgres + Oracle get the SQL-standard
    ``DATE 'YYYY-MM-DD'`` form (which both accept); SQLite gets the
    plain quoted string (SQLite has no native DATE type, stores ISO
    dates as TEXT, and ``CAST('YYYY-MM-DD' AS DATE)`` would coerce to
    INTEGER 2030 — wrong for comparison). The inclusive end is
    enforced via ``< end + 1 day`` so end-of-period TIMESTAMPs are
    caught.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    start, end = period.start, period.end
    start_lit = date_literal(start.isoformat(), cfg.dialect)
    end_excl_lit = date_literal(
        (end + timedelta(days=1)).isoformat(), cfg.dialect,
    )

    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()

        cur.execute(
            f"SELECT COUNT(*),"
            f" COUNT(DISTINCT transfer_id)"
            f" FROM {prefix}_transactions"
            f" WHERE status = 'Posted'"
            f"   AND posting >= {start_lit}"
            f"   AND posting < {end_excl_lit}"
        )
        leg_count, transfer_count = cur.fetchone()

        cur.execute(
            f"SELECT COALESCE(SUM(transfer_gross), 0),"
            f" COALESCE(SUM(transfer_net), 0)"
            f" FROM ("
            f"   SELECT MAX(ABS(amount_money)) AS transfer_gross,"
            f"          SUM(amount_money) AS transfer_net"
            f"   FROM {prefix}_transactions"
            f"   WHERE status = 'Posted'"
            f"     AND posting >= {start_lit}"
            f"     AND posting < {end_excl_lit}"
            f"   GROUP BY transfer_id"
            f" ) per_transfer"
        )
        gross, net = cur.fetchone()

        exception_counts: list[tuple[str, int]] = []
        for label, suffix, date_col in _EXCEPTION_INVARIANTS:
            if date_col is None:
                # Current-state matview (stuck_*): count all rows
                # regardless of posting date. Mirrors the L1 dashboard
                # which shows all currently-stuck without date filter.
                sql = f"SELECT COUNT(*) FROM {prefix}_{suffix}"
            else:
                sql = (
                    f"SELECT COUNT(*) FROM {prefix}_{suffix}"
                    f" WHERE {date_col} >= {start_lit}"
                    f"   AND {date_col} < {end_excl_lit}"
                )
            cur.execute(sql)
            (count,) = cur.fetchone()
            # Mark current-state labels with "*" so the renderer's
            # footnote attaches correctly.
            display_label = f"{label}*" if date_col is None else label
            exception_counts.append((display_label, int(count or 0)))

        # Supersession: count correcting entries (supersedes IS NOT NULL)
        # across BOTH base tables — current-state, no period filter.
        # The originals they supersede are not counted (they're not
        # the events; the corrections are). U.3.f breaks this down by
        # (base_table, supersedes_category) with both total + in-period
        # counts. Asterisk lines up with the stuck_* current-state
        # footnote.
        total_supersession = 0
        for table_name in ("transactions", "daily_balances"):
            cur.execute(
                f"SELECT COUNT(*) FROM {prefix}_{table_name}"
                f" WHERE supersedes IS NOT NULL"
            )
            (count,) = cur.fetchone()
            total_supersession += int(count or 0)
        exception_counts.append(("Supersession*", total_supersession))

        return ExecSummary(
            transactions_count=int(leg_count or 0),
            transfers_count=int(transfer_count or 0),
            # AO.1 audit slice: gross/net sum cents-typed amount_money;
            # _cents_to_dollars projects to Decimal dollars for the
            # renderers' ``${v:,.2f}`` format strings.
            dollar_volume_gross=_cents_to_dollars(gross),
            dollar_volume_net=_cents_to_dollars(net),
            exception_counts=exception_counts,
        )
    finally:
        conn.close()


# -- Drift violations (U.3.a) -------------------------------------------------


@dataclass(frozen=True)
class DriftViolation:
    """One row of the ``<prefix>_drift`` matview, audit-shaped.

    ``business_day`` carries ``business_day_end`` from the matview —
    the day the discrepancy was observed at end-of-day. Mirrors the
    L1 dashboard's "Leaf Account Drift" table for column choice.
    """
    account_id: str
    account_name: str
    account_role: str
    account_parent_role: str
    business_day: date
    stored_balance: Decimal
    computed_balance: Decimal
    drift: Decimal


def _query_drift_violations(
    cfg, instance, period: DateInterval,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> list[DriftViolation] | None:
    """Pull drift rows whose business day falls in the period.

    Returns None when no DB is configured (renders the placeholder
    section). An empty list means the DB is healthy and zero drifts
    fired in the period — that's a good-news render, not a missing
    section.

    Sort: most-recent day first, then biggest absolute drift, then
    account_id for stable order. Auditor wants to see the freshest
    + biggest discrepancies on top.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    start, end = period.start, period.end
    start_lit = date_literal(start.isoformat(), cfg.dialect)
    end_excl_lit = date_literal(
        (end + timedelta(days=1)).isoformat(), cfg.dialect,
    )

    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT account_id, account_name, account_role,"
            f"       account_parent_role, business_day_end,"
            f"       stored_balance, computed_balance, drift"
            f"  FROM {prefix}_drift"
            f" WHERE business_day_start >= {start_lit}"
            f"   AND business_day_start < {end_excl_lit}"
            f" ORDER BY business_day_end DESC, ABS(drift) DESC, account_id"
        )
        rows = cur.fetchall()
        return [
            DriftViolation(
                account_id=str(r[0]),
                account_name=str(r[1] or ""),
                account_role=str(r[2] or ""),
                account_parent_role=str(r[3] or ""),
                business_day=(
                    _coerce_to_date(r[4])
                ),
                # AO.1: stored_balance / computed_balance / drift arrive
                # as BIGINT cents from the drift matview — project to
                # dollars at the cursor boundary.
                stored_balance=_cents_to_dollars(r[5]),
                computed_balance=_cents_to_dollars(r[6]),
                drift=_cents_to_dollars(r[7]),
            )
            for r in rows
        ]
    finally:
        conn.close()


# -- Overdraft violations (U.3.b) ---------------------------------------------


@dataclass(frozen=True)
class OverdraftViolation:
    """One row of the ``<prefix>_overdraft`` matview, audit-shaped.

    The matview only stores rows where stored_balance < 0, so the
    violation IS the negative balance — no computed/drift columns
    needed (the OVERDRAFT_CONTRACT comment in datasets.py says the
    same).
    """
    account_id: str
    account_name: str
    account_role: str
    account_parent_role: str  # empty string when account has no parent
    business_day: date
    stored_balance: Decimal


def _query_overdraft_violations(
    cfg, instance, period: DateInterval,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> list[OverdraftViolation] | None:
    """Pull overdraft rows whose business day falls in the period.

    Returns None when no DB is configured. Empty list = DB healthy
    with zero overdrafts (good-news render).

    Sort: most-recent day first, then biggest absolute balance
    (i.e. deepest underwater), then account_id.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    start, end = period.start, period.end
    start_lit = date_literal(start.isoformat(), cfg.dialect)
    end_excl_lit = date_literal(
        (end + timedelta(days=1)).isoformat(), cfg.dialect,
    )

    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT account_id, account_name, account_role,"
            f"       account_parent_role, business_day_end,"
            f"       stored_balance"
            f"  FROM {prefix}_overdraft"
            f" WHERE business_day_start >= {start_lit}"
            f"   AND business_day_start < {end_excl_lit}"
            f" ORDER BY business_day_end DESC,"
            f"          ABS(stored_balance) DESC, account_id"
        )
        return [
            OverdraftViolation(
                account_id=str(r[0]),
                account_name=str(r[1] or ""),
                account_role=str(r[2] or ""),
                account_parent_role=str(r[3] or ""),
                business_day=(
                    _coerce_to_date(r[4])
                ),
                # AO.1: stored_balance is BIGINT cents from the overdraft
                # matview — project to dollars at the boundary.
                stored_balance=_cents_to_dollars(r[5]),
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


@dataclass(frozen=True)
class OverdraftChildGroupSummary:
    """Per parent-role roll-up of overdrawn child (template) accounts.

    Children share a parent role in the L2 hierarchy. Routine
    customer-account overdrafts roll up into one row per parent role
    showing distinct-children-negative + summed peak-negative — keeps
    the audit page skimmable while preserving total dollar exposure.
    A specific child's per-day detail is recoverable from the
    underlying matview if the auditor wants to drill in.
    """
    parent_role: str
    distinct_children_negative: int
    total_peak_negative: Decimal


def _split_overdraft_by_account_class(
    rows: list[OverdraftViolation],
    singleton_ids: set[str],
) -> tuple[list[OverdraftViolation], list[OverdraftChildGroupSummary]]:
    """Bucket rows into (parent per-row detail, child rolled up by parent role).

    Parents (L2 ``Account`` singletons): every occurrence emits a
    detail row — a parent itself going negative is a systemic issue
    each instance of which is independently worth surfacing.

    Children (template-materialized): grouped by ``account_parent_role``
    so each parent role gets one summary row carrying how many
    distinct children went negative in the period and the sum of
    each child's peak negative balance.
    """
    parent_rows: list[OverdraftViolation] = []
    by_parent: dict[str, dict[str, list[OverdraftViolation]]] = {}
    for r in rows:
        if r.account_id in singleton_ids:
            parent_rows.append(r)
        else:
            key = r.account_parent_role or "(no parent)"
            by_parent.setdefault(key, {}).setdefault(
                r.account_id, [],
            ).append(r)
    child_summaries = sorted(
        (
            OverdraftChildGroupSummary(
                parent_role=parent_role,
                distinct_children_negative=len(children),
                total_peak_negative=sum(
                    (
                        min(r.stored_balance for r in child_rows)
                        for child_rows in children.values()
                    ),
                    start=Decimal(0),
                ),
            )
            for parent_role, children in by_parent.items()
        ),
        # Most-negative total first (worst exposure on top).
        key=lambda s: (s.total_peak_negative, s.parent_role),
    )
    return parent_rows, child_summaries


# -- Limit breach violations (U.3.c) ------------------------------------------


@dataclass(frozen=True)
class LimitBreachViolation:
    """One row of the ``<prefix>_limit_breach`` matview, audit-shaped.

    Each row is one (account, day, rail_name, direction) cell where
    the cumulative flow exceeded the L2-configured cap. Magnitude =
    ``outbound_total - cap`` (always positive). AB.1 (2026-05-19):
    added ``direction`` — 'Outbound' (classic per-rail send cap) or
    'Inbound' (AML / structuring threshold on inbound volume).
    """
    account_id: str
    account_name: str
    account_role: str
    account_parent_role: str
    business_day: date
    rail_name: str
    direction: str
    outbound_total: Decimal
    cap: Decimal

    @property
    def overshoot(self) -> Decimal:
        return self.outbound_total - self.cap


def _query_limit_breach_violations(
    cfg, instance, period: DateInterval,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> list[LimitBreachViolation] | None:
    """Pull limit_breach rows whose business day falls in the period.

    Sort: most-recent day first, then biggest overshoot, then
    account_id — auditor sees the freshest + biggest cap-busts first.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    start, end = period.start, period.end
    start_lit = date_literal(start.isoformat(), cfg.dialect)
    end_excl_lit = date_literal(
        (end + timedelta(days=1)).isoformat(), cfg.dialect,
    )

    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT account_id, account_name, account_role,"
            f"       account_parent_role, business_day,"
            f"       rail_name, direction, outbound_total, cap"
            f"  FROM {prefix}_limit_breach"
            f" WHERE business_day >= {start_lit}"
            f"   AND business_day < {end_excl_lit}"
            f" ORDER BY business_day DESC,"
            f"          (outbound_total - cap) DESC, account_id"
        )
        return [
            LimitBreachViolation(
                account_id=str(r[0]),
                account_name=str(r[1] or ""),
                account_role=str(r[2] or ""),
                account_parent_role=str(r[3] or ""),
                business_day=(
                    _coerce_to_date(r[4])
                ),
                rail_name=str(r[5] or ""),
                direction=str(r[6] or "Outbound"),
                # AO.1: outbound_total / cap are BIGINT cents from the
                # limit_breach matview — project to dollars at the
                # boundary. ``overshoot`` (a @property) inherits the
                # dollar typing for free since both inputs are dollars.
                outbound_total=_cents_to_dollars(r[7]),
                cap=_cents_to_dollars(r[8]),
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


@dataclass(frozen=True)
class LimitBreachChildGroupSummary:
    """Per (parent_role, rail_name) roll-up of breaching children.

    LimitSchedule caps are keyed on (parent_role, rail_name) per
    SPEC, so the natural child summary keys both too. Auditor sees:
    "5 customers breached the ACH outbound cap under DDAControl this
    period, total overshoot $X".
    """
    parent_role: str
    rail_name: str
    distinct_children_breaching: int
    total_overshoot: Decimal


def _split_limit_breach_by_account_class(
    rows: list[LimitBreachViolation],
    singleton_ids: set[str],
) -> tuple[
    list[LimitBreachViolation],
    list[LimitBreachChildGroupSummary],
]:
    """Bucket rows into (parent per-row, child grouped by parent+type).

    Children grouped by (parent_role, rail_name) since that's
    the cap dimension; total_overshoot sums each child's worst-day
    overshoot in the period.
    """
    parent_rows: list[LimitBreachViolation] = []
    by_group: dict[
        tuple[str, str], dict[str, list[LimitBreachViolation]],
    ] = {}
    for r in rows:
        if r.account_id in singleton_ids:
            parent_rows.append(r)
        else:
            key = (
                r.account_parent_role or "(no parent)",
                r.rail_name,
            )
            by_group.setdefault(key, {}).setdefault(
                r.account_id, [],
            ).append(r)
    child_summaries = sorted(
        (
            LimitBreachChildGroupSummary(
                parent_role=key[0],
                rail_name=key[1],
                distinct_children_breaching=len(children),
                total_overshoot=sum(
                    (
                        max(rr.overshoot for rr in child_rows)
                        for child_rows in children.values()
                    ),
                    start=Decimal(0),
                ),
            )
            for key, children in by_group.items()
        ),
        # Biggest overshoot first.
        key=lambda s: (-s.total_overshoot, s.parent_role, s.rail_name),
    )
    return parent_rows, child_summaries


# -- Stuck pending violations (U.3.d) -----------------------------------------


@dataclass(frozen=True)
class StuckPendingViolation:
    """One row of the ``<prefix>_stuck_pending`` matview, audit-shaped.

    Each row is one transaction whose age exceeds the L2-configured
    ``max_pending_age_seconds`` cap. Magnitude = the age itself
    (seconds past posting that the transaction has been stuck in
    Pending status).
    """
    account_id: str
    account_name: str
    account_role: str
    account_parent_role: str
    transaction_id: str
    rail_name: str
    posting: datetime
    amount_money: Decimal
    age_seconds: Decimal
    max_pending_age_seconds: int


def _query_stuck_pending_violations(
    cfg, instance,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> list[StuckPendingViolation] | None:
    """Pull all rows from the ``<prefix>_stuck_pending`` matview.

    No date filter: stuck_pending is a current-state matview per the
    L1 dashboard convention. Auditor sees every transaction currently
    stuck in Pending past its aging cap, regardless of when posted.
    Sort: oldest stuck first (biggest age_seconds), then account_id.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT account_id, account_name, account_role,"
            f"       account_parent_role, transaction_id,"
            f"       rail_name, posting, amount_money,"
            f"       age_seconds, max_pending_age_seconds"
            f"  FROM {prefix}_stuck_pending"
            f" ORDER BY age_seconds DESC, account_id"
        )
        return [
            StuckPendingViolation(
                account_id=str(r[0]),
                account_name=str(r[1] or ""),
                account_role=str(r[2] or ""),
                account_parent_role=str(r[3] or ""),
                transaction_id=str(r[4]),
                rail_name=str(r[5] or ""),
                posting=_coerce_to_datetime(r[6]),
                # AO.1: amount_money is BIGINT cents — project to dollars.
                # age_seconds stays in seconds (not money) — bare Decimal.
                amount_money=_cents_to_dollars(r[7]),
                age_seconds=Decimal(r[8] or 0),
                max_pending_age_seconds=int(r[9] or 0),
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


@dataclass(frozen=True)
class StuckPendingChildGroupSummary:
    """Per (parent_role, rail_name) roll-up of stuck child txns.

    Counts both distinct affected accounts and total stuck txns:
    "5 customers under DDAControl have 12 stuck wire_concentration
    pendings totaling $X". The transaction count drives operational
    workload (12 manual interventions); the account count is the
    spread.
    """
    parent_role: str
    rail_name: str
    distinct_children_affected: int
    stuck_transaction_count: int
    total_stuck_amount: Decimal


def _split_stuck_pending_by_account_class(
    rows: list[StuckPendingViolation],
    singleton_ids: set[str],
) -> tuple[
    list[StuckPendingViolation],
    list[StuckPendingChildGroupSummary],
]:
    """Bucket rows into (parent per-row, child grouped by parent+type)."""
    parent_rows: list[StuckPendingViolation] = []
    by_group: dict[
        tuple[str, str], list[StuckPendingViolation],
    ] = {}
    for r in rows:
        if r.account_id in singleton_ids:
            parent_rows.append(r)
        else:
            key = (
                r.account_parent_role or "(no parent)",
                r.rail_name,
            )
            by_group.setdefault(key, []).append(r)
    child_summaries = sorted(
        (
            StuckPendingChildGroupSummary(
                parent_role=key[0],
                rail_name=key[1],
                distinct_children_affected=len({r.account_id for r in group}),
                stuck_transaction_count=len(group),
                total_stuck_amount=sum(
                    (abs(r.amount_money) for r in group),
                    start=Decimal(0),
                ),
            )
            for key, group in by_group.items()
        ),
        # Biggest dollar pile first.
        key=lambda s: (
            -s.total_stuck_amount, s.parent_role, s.rail_name,
        ),
    )
    return parent_rows, child_summaries


def _format_age(seconds: Decimal | int) -> str:
    """Human-readable age — days at one-decimal precision.

    Stuck-aging caps in the L2 are typically expressed in days
    (86400s, 172800s); rendering as 'N.Nd' lines up with how
    auditors talk about pending backlog.
    """
    days = float(seconds) / 86400.0
    return f"{days:.1f}d"


# -- Stuck unbundled violations (U.3.e) ---------------------------------------


@dataclass(frozen=True)
class StuckUnbundledViolation:
    """One row of the ``<prefix>_stuck_unbundled`` matview, audit-shaped.

    Same shape as StuckPendingViolation but the cap is
    ``max_unbundled_age_seconds`` (Posted-but-not-yet-bundled aging
    rather than Pending aging). Each row is one transaction past
    its bundling cap.
    """
    account_id: str
    account_name: str
    account_role: str
    account_parent_role: str
    transaction_id: str
    rail_name: str
    posting: datetime
    amount_money: Decimal
    age_seconds: Decimal
    max_unbundled_age_seconds: int


def _query_stuck_unbundled_violations(
    cfg, instance,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> list[StuckUnbundledViolation] | None:
    """Pull all rows from the ``<prefix>_stuck_unbundled`` matview.

    Same current-state semantics as stuck_pending — no date filter,
    show every Posted transaction past its bundling cap regardless
    of when posted.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT account_id, account_name, account_role,"
            f"       account_parent_role, transaction_id,"
            f"       rail_name, posting, amount_money,"
            f"       age_seconds, max_unbundled_age_seconds"
            f"  FROM {prefix}_stuck_unbundled"
            f" ORDER BY age_seconds DESC, account_id"
        )
        return [
            StuckUnbundledViolation(
                account_id=str(r[0]),
                account_name=str(r[1] or ""),
                account_role=str(r[2] or ""),
                account_parent_role=str(r[3] or ""),
                transaction_id=str(r[4]),
                rail_name=str(r[5] or ""),
                posting=_coerce_to_datetime(r[6]),
                # AO.1: same shape as stuck_pending — amount_money in
                # cents → dollars; age_seconds stays seconds.
                amount_money=_cents_to_dollars(r[7]),
                age_seconds=Decimal(r[8] or 0),
                max_unbundled_age_seconds=int(r[9] or 0),
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


@dataclass(frozen=True)
class StuckUnbundledChildGroupSummary:
    """Per (parent_role, rail_name) roll-up of unbundled child txns."""
    parent_role: str
    rail_name: str
    distinct_children_affected: int
    stuck_transaction_count: int
    total_stuck_amount: Decimal


def _split_stuck_unbundled_by_account_class(
    rows: list[StuckUnbundledViolation],
    singleton_ids: set[str],
) -> tuple[
    list[StuckUnbundledViolation],
    list[StuckUnbundledChildGroupSummary],
]:
    """Bucket rows into (parent per-row, child grouped by parent+type)."""
    parent_rows: list[StuckUnbundledViolation] = []
    by_group: dict[
        tuple[str, str], list[StuckUnbundledViolation],
    ] = {}
    for r in rows:
        if r.account_id in singleton_ids:
            parent_rows.append(r)
        else:
            key = (
                r.account_parent_role or "(no parent)",
                r.rail_name,
            )
            by_group.setdefault(key, []).append(r)
    child_summaries = sorted(
        (
            StuckUnbundledChildGroupSummary(
                parent_role=key[0],
                rail_name=key[1],
                distinct_children_affected=len({r.account_id for r in group}),
                stuck_transaction_count=len(group),
                total_stuck_amount=sum(
                    (abs(r.amount_money) for r in group),
                    start=Decimal(0),
                ),
            )
            for key, group in by_group.items()
        ),
        key=lambda s: (
            -s.total_stuck_amount, s.parent_role, s.rail_name,
        ),
    )
    return parent_rows, child_summaries


# -- Supersession audit (U.3.f) -----------------------------------------------


@dataclass(frozen=True)
class SupersessionAggregate:
    """Per (base_table, category) total count, all-time.

    Counts of correcting entries (rows with supersedes = category) —
    the originals they correct are not double-counted. ``total_count``
    is across all of history; ``new_in_period_count`` is the subset
    whose date column falls in the report window.
    """
    base_table: str
    supersedes_category: str
    total_count: int
    new_in_period_count: int


@dataclass(frozen=True)
class SupersessionTransactionDetail:
    """One in-window correcting entry from ``<prefix>_transactions``."""
    transaction_id: str
    supersedes_category: str
    account_id: str
    account_name: str
    posting: datetime
    amount_money: Decimal


@dataclass(frozen=True)
class SupersessionDailyBalanceDetail:
    """One in-window correcting entry from ``<prefix>_daily_balances``."""
    account_id: str
    account_name: str
    business_day: date
    supersedes_category: str
    money: Decimal


@dataclass(frozen=True)
class SupersessionAuditData:
    """All-table supersession audit data: aggregates + in-window details."""
    aggregates: list[SupersessionAggregate]
    transaction_details: list[SupersessionTransactionDetail]
    daily_balance_details: list[SupersessionDailyBalanceDetail]


def _query_supersession(
    cfg, instance, period: DateInterval,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> SupersessionAuditData | None:
    """Aggregate supersession counts + in-window detail rows.

    Per the user's design (U.3.f): aggregate counts are over the
    ENTIRE dataset (no date filter — supersession history accumulates
    indefinitely); detail rows are limited to the report window so the
    audit page stays bounded in size while still surfacing every
    correcting entry the auditor needs to investigate this period.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    start, end = period.start, period.end
    start_lit = date_literal(start.isoformat(), cfg.dialect)
    end_excl_lit = date_literal(
        (end + timedelta(days=1)).isoformat(), cfg.dialect,
    )

    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        aggregates: list[SupersessionAggregate] = []
        for table_name, date_col in (
            ("transactions", "posting"),
            ("daily_balances", "business_day_start"),
        ):
            cur.execute(
                f"SELECT supersedes, COUNT(*) AS total,"
                f" SUM(CASE WHEN {date_col} >= {start_lit}"
                f"          AND {date_col} < {end_excl_lit}"
                f"          THEN 1 ELSE 0 END) AS new_in_period"
                f" FROM {prefix}_{table_name}"
                f" WHERE supersedes IS NOT NULL"
                f" GROUP BY supersedes"
                f" ORDER BY supersedes"
            )
            for cat, total, new_in in cur.fetchall():
                aggregates.append(SupersessionAggregate(
                    base_table=table_name,
                    supersedes_category=str(cat),
                    total_count=int(total or 0),
                    new_in_period_count=int(new_in or 0),
                ))

        cur.execute(
            f"SELECT id, supersedes, account_id, account_name,"
            f"       posting, amount_money"
            f"  FROM {prefix}_transactions"
            f" WHERE supersedes IS NOT NULL"
            f"   AND posting >= {start_lit}"
            f"   AND posting < {end_excl_lit}"
            f" ORDER BY posting DESC, id"
        )
        transaction_details = [
            SupersessionTransactionDetail(
                transaction_id=str(r[0]),
                supersedes_category=str(r[1]),
                account_id=str(r[2]),
                account_name=str(r[3] or ""),
                posting=_coerce_to_datetime(r[4]),
                # AO.1: amount_money from transactions table — BIGINT
                # cents → dollars.
                amount_money=_cents_to_dollars(r[5]),
            )
            for r in cur.fetchall()
        ]

        cur.execute(
            f"SELECT account_id, account_name, business_day_start,"
            f"       supersedes, money"
            f"  FROM {prefix}_daily_balances"
            f" WHERE supersedes IS NOT NULL"
            f"   AND business_day_start >= {start_lit}"
            f"   AND business_day_start < {end_excl_lit}"
            f" ORDER BY business_day_start DESC, account_id"
        )
        daily_balance_details = [
            SupersessionDailyBalanceDetail(
                account_id=str(r[0]),
                account_name=str(r[1] or ""),
                business_day=(
                    _coerce_to_date(r[2])
                ),
                supersedes_category=str(r[3]),
                # AO.1: ``money`` column on daily_balances is BIGINT
                # cents → dollars at the boundary.
                money=_cents_to_dollars(r[4]),
            )
            for r in cur.fetchall()
        ]

        return SupersessionAuditData(
            aggregates=aggregates,
            transaction_details=transaction_details,
            daily_balance_details=daily_balance_details,
        )
    finally:
        conn.close()


# -- Daily Statement walks (U.4) ----------------------------------------------


@dataclass(frozen=True)
class DailyStatementTransaction:
    """One Posted-Money record on a Daily Statement walk page.

    Mirrors the column shape of the L1 dashboard's Daily Statement
    detail table (``DAILY_STATEMENT_TRANSACTIONS_CONTRACT``) so the
    audit and dashboard agree row-for-row on the day's activity.
    """
    transaction_id: str
    transfer_id: str
    rail_name: str
    amount_money: Decimal
    amount_direction: str
    status: str
    posting: datetime


@dataclass(frozen=True)
class DailyStatementWalk:
    """One per-(account, business_day) Daily Statement page.

    KPIs are read from ``<prefix>_daily_statement_summary`` (the same
    matview the dashboard's Daily Statement sheet reads). The
    ``drift`` here is the **per-day** drift (closing stored − closing
    recomputed-from-day's-flow); the cumulative drift surfaced in
    U.3.a is computed differently (stored − sum of all transactions
    ever) and the two can differ when daily_balances are sparse.
    """
    account_id: str
    account_name: str
    account_role: str
    business_day_start: date
    business_day_end: date
    opening_balance: Decimal
    total_debits: Decimal
    total_credits: Decimal
    closing_balance_stored: Decimal
    closing_balance_recomputed: Decimal
    drift: Decimal
    transactions: list[DailyStatementTransaction]


def _query_daily_statement_walks(
    cfg, instance,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
    period: DateInterval,
    singleton_ids: set[str],
) -> list[DailyStatementWalk] | None:
    """Pull a Daily Statement walk for every drifted (account, day) pair.

    Walks are emitted for the union of:
      1. Every (account, day) row in ``<prefix>_drift`` for the period
         (any account that actually drifted), and
      2. Every (parent_account, day) row in
         ``<prefix>_daily_statement_summary`` for the period — parent
         accounts (L2 ``Account`` singletons: GL clearing,
         concentration, ZBA master) always render even when drift is
         zero, because their day-by-day balance walk is itself
         auditor-relevant; a clean walk is evidence of correctness.

    Each walk = ``daily_statement_summary`` KPIs (5 numbers) + the
    day's transactions from ``current_transactions``.

    Sort: most-recent business_day_end first, then biggest |drift|
    first (so drifted rows sort ahead of clean parents within a day),
    then account_id.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    start, end = period.start, period.end
    start_lit = date_literal(start.isoformat(), cfg.dialect)
    end_excl_lit = date_literal(
        (end + timedelta(days=1)).isoformat(), cfg.dialect,
    )

    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        # 1) Drift matview: every (account, day) that actually drifted.
        cur.execute(
            f"SELECT account_id, business_day_start, business_day_end, drift"
            f"  FROM {prefix}_drift"
            f" WHERE business_day_start >= {start_lit}"
            f"   AND business_day_start < {end_excl_lit}"
        )
        drift_rows = cur.fetchall()
        # 2) Parent (singleton) accounts: every (parent, day) in the
        # period from daily_statement_summary, even with zero drift.
        parent_rows: list = []
        if singleton_ids:
            cur.execute(
                f"SELECT account_id, business_day_start, business_day_end,"
                f"       drift"
                f"  FROM {prefix}_daily_statement_summary"
                f" WHERE business_day_start >= {start_lit}"
                f"   AND business_day_start < {end_excl_lit}"
            )
            parent_rows = [
                r for r in cur.fetchall()
                if str(r[0]) in singleton_ids
            ]
        # Dedupe on (account_id, business_day_start). When the same
        # (account, day) shows up in both queries, prefer the drift
        # row's drift value (already non-zero by definition).
        pair_map: dict[tuple[str, object], tuple[object, object, Decimal]] = {}
        for r in parent_rows:
            key = (str(r[0]), r[1])
            pair_map[key] = (r[1], r[2], Decimal(r[3] or 0))
        for r in drift_rows:
            key = (str(r[0]), r[1])
            pair_map[key] = (r[1], r[2], Decimal(r[3] or 0))
        if not pair_map:
            return []

        # Sort: business_day_end DESC, |drift| DESC, account_id ASC.
        sorted_pairs = sorted(
            pair_map.items(),
            key=lambda kv: (
                -_coerce_to_date(kv[1][1]).toordinal(),
                -abs(kv[1][2]),
                kv[0][0],
            ),
        )

        walks: list[DailyStatementWalk] = []
        for (account_id, day_start), (_d_start, _day_end, _drift) in (
            sorted_pairs
        ):
            day_start_date = (
                _coerce_to_date(day_start)
            )
            day_start_lit = date_literal(
                day_start_date.isoformat(), cfg.dialect,
            )
            # Next-day date computed in Python (avoids dialect-specific
            # INTERVAL syntax: PG = ``+ INTERVAL '1 day'``,
            # Oracle = ``+ INTERVAL '1' DAY``).
            day_end_excl_lit = date_literal(
                (day_start_date + timedelta(days=1)).isoformat(), cfg.dialect,
            )

            # 2) Daily statement summary: 5 KPIs precomputed.
            cur.execute(
                f"SELECT account_name, account_role,"
                f"       business_day_start, business_day_end,"
                f"       opening_balance, total_debits, total_credits,"
                f"       closing_balance_stored, closing_balance_recomputed,"
                f"       drift"
                f"  FROM {prefix}_daily_statement_summary"
                f" WHERE account_id = '{account_id}'"
                f"   AND business_day_start = {day_start_lit}"
            )
            summary = cur.fetchone()
            if summary is None:
                # Drift exists but no summary row — feed/matview drift.
                # Surface the drift row alone with placeholder KPIs.
                continue
            (
                account_name, account_role,
                bd_start, bd_end,
                opening, debits, credits,
                closing_stored, closing_recomp, day_drift,
            ) = summary

            # 3) Day's transactions from current_transactions matview.
            cur.execute(
                f"SELECT id, transfer_id, rail_name,"
                f"       amount_money, amount_direction, status, posting"
                f"  FROM {prefix}_current_transactions"
                f" WHERE account_id = '{account_id}'"
                f"   AND posting >= {day_start_lit}"
                f"   AND posting < {day_end_excl_lit}"
                f" ORDER BY posting"
            )
            transactions = [
                DailyStatementTransaction(
                    transaction_id=str(r[0]),
                    transfer_id=str(r[1] or ""),
                    rail_name=str(r[2] or ""),
                    # AO.1: per-transaction amount_money is BIGINT cents
                    # → dollars at boundary.
                    amount_money=_cents_to_dollars(r[3]),
                    amount_direction=str(r[4] or ""),
                    status=str(r[5] or ""),
                    posting=_coerce_to_datetime(r[6]),
                )
                for r in cur.fetchall()
            ]

            walks.append(DailyStatementWalk(
                account_id=account_id,
                account_name=str(account_name or ""),
                account_role=str(account_role or ""),
                business_day_start=(
                    _coerce_to_date(bd_start)
                ),
                business_day_end=(
                    _coerce_to_date(bd_end)
                ),
                # AO.1: every daily_statement_summary KPI column is
                # BIGINT cents (opening / debits / credits / closing_*
                # / drift) — boundary-project to dollars so the
                # ${v:,.2f} formatters in pdf.py / markdown.py emit
                # the right shape.
                opening_balance=_cents_to_dollars(opening),
                total_debits=_cents_to_dollars(debits),
                total_credits=_cents_to_dollars(credits),
                closing_balance_stored=_cents_to_dollars(closing_stored),
                closing_balance_recomputed=_cents_to_dollars(closing_recomp),
                drift=_cents_to_dollars(day_drift),
                transactions=transactions,
            ))
        return walks
    finally:
        conn.close()


# -- Provenance appendix matview evidence (U.7.c) ------------------------------


@dataclass(frozen=True)
class MatviewEvidence:
    """One matview's row count + SHA256 for the provenance appendix.

    Distinct from the authoritative composite fingerprint (which
    covers the base tables, NOT matviews — matviews are derived
    data and their hash drifting from a recompute is a *technical*
    signal, not a data-binding problem). Listed in the appendix as
    sidecar evidence so a regulator can independently verify
    matview consistency with the base tables.
    """
    matview: str       # un-prefixed name shown to the auditor
    row_count: int
    sha256: str


# Matviews surfaced in the provenance appendix. Listed unprefixed
# (the per-instance prefix is added at query time). Order is the
# same order they're rendered in the appendix table.
_APPENDIX_MATVIEWS: tuple[str, ...] = (
    "current_transactions",
    "current_daily_balances",
    "drift",
    "ledger_drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "daily_statement_summary",
)


def _query_matview_evidence(
    cfg, instance,  # type: ignore[no-untyped-def]: cfg/instance untyped pending audit-CLI sweep
) -> list[MatviewEvidence] | None:
    """Hash every matview the appendix advertises (U.7.c).

    Returns ``None`` when ``demo_database_url`` is absent (skeleton
    mode — no DB queries, no matviews to hash). Otherwise returns
    one entry per matview in ``_APPENDIX_MATVIEWS``, in order. Uses
    ``hash_matview_rows`` for canonical-byte hashing identical to
    the base-table fingerprint — same recipe a verifier would
    follow if recomputing manually.
    """
    if cfg.demo_database_url is None:
        return None

    from recon_gen.common.db import connect_demo_db
    from recon_gen.common.provenance import hash_matview_rows

    prefix = cfg.db_table_prefix
    out: list[MatviewEvidence] = []
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        for matview in _APPENDIX_MATVIEWS:
            qualified = f"{prefix}_{matview}"
            row_count, sha = hash_matview_rows(cur, matview=qualified)
            out.append(MatviewEvidence(
                matview=matview,
                row_count=row_count,
                sha256=sha,
            ))
    finally:
        conn.close()
    return out


@audit.command("apply")
@l2_instance_option()
@config_option(required_for_dialect_only=True)
@period_option()
@click.option(
    "-o", "--output", "output",
    type=click.Path(), default=None,
    help=(
        "Output path. Without --execute: Markdown source destination "
        "(default: stdout). With --execute: PDF destination "
        "(default: report.pdf)."
    ),
)
@execute_option()
def audit_apply(
    l2_instance_path: str | None,
    config: str,
    period: DateInterval | None,
    output: str | None,
    execute: bool,
) -> None:
    """Emit the audit report's Markdown source (or ``--execute`` to write a PDF).

    Default: print the Markdown rendering of the report (cover +
    section outline) to stdout. Pass ``-o FILE`` to write to a file.
    Useful for review before committing to a PDF.

    Pass ``--execute`` to render the report as a PDF via reportlab.
    Default destination is ``report.pdf`` in the current working
    directory; override with ``-o FILE``.
    """
    from recon_gen import __version__ as _qsg_version

    _cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    resolved_period = _resolve_period(period)
    institution = _institution_name(instance, _cfg)
    generated_at = datetime.now()
    l2_label = (
        Path(l2_instance_path).name
        if l2_instance_path is not None
        else f"{_cfg.deployment_name} (bundled)"
    )
    exec_summary = _query_executive_summary(_cfg, instance, resolved_period)
    drift_rows = _query_drift_violations(_cfg, instance, resolved_period)
    overdraft_rows = _query_overdraft_violations(_cfg, instance, resolved_period)
    limit_breach_rows = _query_limit_breach_violations(
        _cfg, instance, resolved_period,
    )
    stuck_pending_rows = _query_stuck_pending_violations(_cfg, instance)
    stuck_unbundled_rows = _query_stuck_unbundled_violations(_cfg, instance)
    supersession_data = _query_supersession(_cfg, instance, resolved_period)
    singleton_ids = _singleton_account_ids(instance)
    internal_singleton_ids = _internal_singleton_account_ids(instance)
    daily_statement_walks = _query_daily_statement_walks(
        _cfg, instance, resolved_period, internal_singleton_ids,
    )
    provenance = compute_provenance(
        _cfg, instance,
        l2_instance_path=l2_instance_path,
        version=_qsg_version,
    )
    matview_evidence = _query_matview_evidence(_cfg, instance)
    # Resolve once + thread through so the audit PDF picks up the L2's
    # branded palette (or DEFAULT_PRESET when no theme override). Per
    # CLAUDE.md: never hardcode hex colors in render code.
    theme = resolve_l2_theme(instance) or DEFAULT_PRESET

    from recon_gen.cli.audit.pdf import _write_audit_pdf  # avoid cycle
    from recon_gen.cli.audit.markdown import _render_audit_markdown
    if execute:
        out_path = Path(output) if output is not None else Path("report.pdf")
        _write_audit_pdf(
            out_path,
            institution=institution,
            period=resolved_period,
            generated_at=generated_at,
            exec_summary=exec_summary,
            drift_rows=drift_rows,
            overdraft_rows=overdraft_rows,
            limit_breach_rows=limit_breach_rows,
            stuck_pending_rows=stuck_pending_rows,
            stuck_unbundled_rows=stuck_unbundled_rows,
            supersession_data=supersession_data,
            daily_statement_walks=daily_statement_walks,
            singleton_ids=singleton_ids,
            theme=theme,
            version=_qsg_version,
            l2_label=l2_label,
            provenance=provenance,
            matview_evidence=matview_evidence,
            l2_instance_path=l2_instance_path,
        )
        # U.7.b — auto-sign the PDF if config.yaml carries signing material.
        if _cfg.signing is not None:
            from recon_gen.common.pdf.signing import sign_pdf_in_place
            sign_pdf_in_place(out_path, _cfg.signing)
            click.echo(
                f"Applied digital signature "
                f"({_cfg.signing.signer_name or 'cert CN'}) to {out_path}.",
                err=True,
            )
        click.echo(
            f"Wrote audit report to {out_path} "
            f"(institution={institution}, "
            f"period={resolved_period.start}–{resolved_period.end})."
        )
        return

    markdown = _render_audit_markdown(
        institution=institution,
        period=resolved_period,
        generated_at=generated_at,
        exec_summary=exec_summary,
        drift_rows=drift_rows,
        overdraft_rows=overdraft_rows,
        limit_breach_rows=limit_breach_rows,
        stuck_pending_rows=stuck_pending_rows,
        stuck_unbundled_rows=stuck_unbundled_rows,
        supersession_data=supersession_data,
        daily_statement_walks=daily_statement_walks,
        singleton_ids=singleton_ids,
        version=_qsg_version,
        l2_label=l2_label,
        provenance=provenance,
        matview_evidence=matview_evidence,
        l2_instance_path=l2_instance_path,
    )
    if output is None:
        click.echo(markdown, nl=False)
        return
    Path(output).write_text(markdown, encoding="utf-8")
    click.echo(
        f"Wrote audit Markdown source to {output} "
        f"({len(markdown)} bytes).",
        err=True,
    )


@audit.command("clean")
@click.option(
    "-o", "--output", "output",
    type=click.Path(), default="report.pdf",
    help="PDF path to remove (default: report.pdf).",
)
@execute_option()
def audit_clean(output: str, execute: bool) -> None:
    """Print or remove the generated report file.

    Default: print the path that would be deleted (no side effect).
    Pass ``--execute`` to actually unlink it.
    """
    target = Path(output)
    if not target.exists():
        click.echo(f"{target} doesn't exist; nothing to clean.")
        return
    if not execute:
        click.echo(f"Would delete: {target}")
        return
    target.unlink()
    click.echo(f"Removed {target}")


@audit.command("test")
@click.option(
    "--pytest-args", default="",
    help="Extra args passed verbatim to pytest (e.g. '-k smoke').",
)
def audit_test(pytest_args: str) -> None:
    """Run the audit test suite (pytest + pyright on the audit module).

    Targets ``tests/audit/`` for pytest — scenario expectations,
    PDF/dashboard extractors, PDF-matches-scenario, persona-clean,
    and smoke. Defers the browser matrix
    (``tests/e2e/test_audit_dashboard_agreement.py``) to
    ``RECON_GEN_E2E=1`` — not run here.

    Pyright covers the audit package (``cli/audit/``).
    """
    pytest_argv = (
        [sys.executable, "-m", "pytest", "tests/audit/", "-q"]
        + (pytest_args.split() if pytest_args else [])
    )
    pyright_argv = [
        sys.executable, "-m", "pyright",
        "src/recon_gen/cli/audit/",
    ]
    failed = []
    click.echo(f"$ {' '.join(pytest_argv)}")
    if subprocess.call(pytest_argv) != 0:
        failed.append("pytest")
    click.echo(f"$ {' '.join(pyright_argv)}")
    if subprocess.call(pyright_argv) != 0:
        failed.append("pyright")
    if failed:
        raise click.ClickException(f"audit test failed: {', '.join(failed)}")
    click.echo("audit test: OK")


@audit.command("verify")
@click.argument("pdf_path", type=click.Path(exists=True, dir_okay=False))
@l2_instance_option()
@config_option(required_for_dialect_only=True)
def audit_verify(
    pdf_path: str,
    l2_instance_path: str | None,
    config: str,
) -> None:
    """Verify an audit PDF's embedded provenance fingerprint.

    Extracts the ``ProvenanceFingerprint`` JSON embedded in the
    PDF's ``/Subject`` metadata, recomputes each input from current
    sources (DB rows up to the embedded high-water-mark, L2 yaml
    bytes on disk, current recon-gen code identity), and
    reports per-source matches/diffs.

    Recomputes against the EMBEDDED hwm (not current ``MAX(entry)``)
    so the verification reproduces the report's snapshot point —
    new rows added since report-generation time don't trigger a
    false diff. A diff fires only when bytes that the fingerprint
    actually covers have changed: a row at or below the embedded
    hwm was modified, the L2 yaml was edited, or the code identity
    changed.

    Exits 0 on full match, 1 with a per-source diff on mismatch.
    """
    from pypdf import PdfReader
    import json as _json

    from recon_gen import __version__ as _qsg_version

    reader = PdfReader(pdf_path)
    subject = reader.metadata.get("/Subject", "") if reader.metadata else ""
    if not subject:
        raise click.ClickException(
            f"{pdf_path} has no embedded provenance — was it "
            f"generated with --execute against a configured DB?"
        )
    try:
        embedded_dict = _json.loads(subject)
        embedded = ProvenanceFingerprint.from_dict(embedded_dict)
    except (ValueError, KeyError) as e:
        raise click.ClickException(
            f"Embedded provenance in {pdf_path} is unreadable: {e}"
        )

    cfg, instance = resolve_l2_for_demo(config, l2_instance_path)
    if cfg.demo_database_url is None:
        raise click.ClickException(
            "audit verify needs --config with demo_database_url set "
            "to recompute table hashes against the live DB."
        )

    from recon_gen.common.db import connect_demo_db

    prefix = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        # Sanity: embedded hwm must not exceed current MAX(entry) —
        # if it does, the table was truncated/replaced and the rows
        # the report bound to are gone.
        cur.execute(
            f"SELECT COALESCE(MAX(entry), 0) FROM {prefix}_transactions"
        )
        tx_max = int(cur.fetchone()[0] or 0)
        cur.execute(
            f"SELECT COALESCE(MAX(entry), 0) FROM {prefix}_daily_balances"
        )
        bal_max = int(cur.fetchone()[0] or 0)
        if tx_max < embedded.transactions_hwm:
            raise click.ClickException(
                f"transactions table MAX(entry)={tx_max} is below "
                f"embedded high-water-mark {embedded.transactions_hwm}; "
                f"rows the report bound to are gone."
            )
        if bal_max < embedded.balances_hwm:
            raise click.ClickException(
                f"daily_balances MAX(entry)={bal_max} is below "
                f"embedded high-water-mark {embedded.balances_hwm}; "
                f"rows the report bound to are gone."
            )
        tx_sha_now = hash_table_rows(
            cur, table=f"{prefix}_transactions",
            hwm=embedded.transactions_hwm,
        )
        bal_sha_now = hash_table_rows(
            cur, table=f"{prefix}_daily_balances",
            hwm=embedded.balances_hwm,
        )
    finally:
        conn.close()

    l2_sha_now = l2_yaml_sha256(l2_instance_path)
    code_now = recon_gen_code_identity(_qsg_version)

    diffs: list[tuple[str, str, str]] = []

    def _check(label: str, embedded_val: str, current_val: str) -> None:
        if embedded_val != current_val:
            diffs.append((label, embedded_val, current_val))

    _check("transactions_sha", embedded.transactions_sha, tx_sha_now)
    _check("balances_sha", embedded.balances_sha, bal_sha_now)
    _check("l2_yaml_sha", embedded.l2_yaml_sha, l2_sha_now)
    _check("code_identity", embedded.code_identity, code_now)

    short_was = embedded.composite_sha[:8]
    if not diffs:
        click.echo(f"OK: {pdf_path} verifies against current sources")
        click.echo(f"     composite = {embedded.composite_sha}")
        click.echo(
            f"     bound to  tx_hwm={embedded.transactions_hwm} "
            f"bal_hwm={embedded.balances_hwm}"
        )
        return
    click.echo(
        f"DIFF: {pdf_path} does not match current sources "
        f"(was {short_was}…)",
        err=True,
    )
    for label, was, now in diffs:
        click.echo(f"  {label}:", err=True)
        click.echo(f"    embedded: {was}", err=True)
        click.echo(f"    current:  {now}", err=True)
    raise click.ClickException(
        f"{len(diffs)} input(s) diverged — see per-source diff above"
    )


