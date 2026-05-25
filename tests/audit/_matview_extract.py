"""Direct-SQL anchor for the 4-way agreement test (X.2.j.B.2).

The 5th anchor in the chain ``scenario_plants ⊆ direct_matview_query ==
PDF == QS == App2``. The L1-invariant matview is the *ground truth* all
three renderers should be showing; this module queries it straight off
the just-seeded DB so the test compares each renderer against the matview
(and the matview against the scenario plants), not just the renderers
against each other.

Two reads per invariant:

- ``l1_invariant_matview_row_keys`` — the set of natural-key tuples for
  the flat-shape invariants (drift / overdraft → ``(account_id, day)``;
  limit_breach → ``(account_id, day, rail_name)`` post-Z.B). The ``day``
  is the matview's day column (``business_day_start`` for drift/overdraft,
  ``business_day`` for limit_breach), date-truncated — matching the
  scenario's ``_eff(p)`` semantics and the audit's
  ``>= start AND < (end + 1 day)`` window.
- ``count_l1_invariant_matview_rows`` — a plain row count. Used for the
  divergent-shape invariants (stuck_* / supersession), whose matviews
  carry no date column (the row count is whatever the matview currently
  holds) and whose natural key (``transaction_id``) the scenario plants
  don't expose, so a count comparison among the renderers + the matview
  is the meaningful check there.

``conn`` is a live DB connection (psycopg / oracledb / sqlite3) — the
test opens its own (the ``seeded_audit`` fixture's conn is closed by the
time the asserts run). Date literals are inlined (test-controlled date
values, no injection surface) with a per-dialect form (Oracle needs
``TO_DATE(..., 'YYYY-MM-DD')`` — its session ``NLS_DATE_FORMAT`` rejects
ISO strings; PG / SQLite take the plain string).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from recon_gen.common.intervals import DateInterval
from recon_gen.common.sql import Dialect


L1Invariant = Literal[
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
]


# invariant → (matview suffix, day column or None, natural-key columns).
# day column None ⇒ no period filter (current-state matview).
#
# ``supersession`` is intentionally absent: there's no ``<prefix>_supersession``
# matview — the L1 dashboard's "Transactions Audit" table and the audit
# PDF's supersession section each query their own shape over the base
# ``<prefix>_transactions`` / ``<prefix>_daily_balances`` (the entry-trail of
# any logical key with a superseded entry; rows with ``supersedes IS NOT
# NULL`` per category; …). So supersession has no clean direct-SQL
# anchor — the 4-way test keeps it count-level (the two renderers must
# agree with each other) without it. ``MATVIEW_ANCHORED`` is the set of
# invariants this module can serve.
_MATVIEW: dict[L1Invariant, tuple[str, str | None, tuple[str, ...]]] = {
    "drift": ("drift", "business_day_start", ("account_id", "business_day_start")),
    "overdraft": (
        "overdraft", "business_day_start", ("account_id", "business_day_start"),
    ),
    "limit_breach": (
        # Z.B (2026-05-15) — ``transfer_type`` subsumed into ``rail_name`` on
        # the limit_breach matview (the cap view's CASE-WHEN now keys on
        # ``tx.rail_name`` per common/l2/schema.py::_render_limit_breach_cases).
        # The scenario-plant identity tuple is ``(account_id, day, rail_name)``
        # — see tests/audit/test_scenario_expectations.py::
        # test_limit_breach_carries_transfer_type_in_identity (kept the test
        # name from before the Z.B rename so the historical context survives).
        "limit_breach", "business_day",
        ("account_id", "business_day", "rail_name"),
    ),
    "stuck_pending": ("stuck_pending", None, ("transaction_id",)),
    "stuck_unbundled": ("stuck_unbundled", None, ("transaction_id",)),
}

MATVIEW_ANCHORED: frozenset[str] = frozenset(_MATVIEW)


def _date_literal(d: date, dialect: Dialect) -> str:
    """A SQL literal for ``d`` that compares correctly against the
    matview's day column on each dialect."""
    iso = d.isoformat()
    if dialect is Dialect.ORACLE:
        return f"TO_DATE('{iso}', 'YYYY-MM-DD')"
    # PG: implicit cast of the ISO string to timestamp; SQLite: lexical
    # comparison against the TEXT-stored ISO datetime.
    return f"'{iso}'"


def _period_where(
    day_col: str | None, period: DateInterval | None, dialect: Dialect,
) -> str:
    """The ``WHERE`` clause narrowing ``day_col`` to ``period`` — the
    audit's ``>= start AND < (end + 1 day)`` window, so a same-day
    non-midnight timestamp is included (matches QS DAY-granularity and
    the X.2.j.dateparity-fixed ``app2_date_filter``).

    Returns ``""`` when there's no day column or no period."""
    if day_col is None or period is None:
        return ""
    end_plus_one = period.end + timedelta(days=1)
    return (
        f" WHERE {day_col} >= {_date_literal(period.start, dialect)} "
        f"AND {day_col} < {_date_literal(end_plus_one, dialect)}"
    )


def count_l1_invariant_matview_rows(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union (psycopg/oracledb/sqlite3) has no shared Protocol
    prefix: str,
    invariant: L1Invariant,
    period: DateInterval | None,
    dialect: Dialect,
) -> int:
    """``SELECT count(*)`` from the named invariant's matview, narrowed
    to ``period`` when the matview has a day column (drift / overdraft /
    limit_breach) and unfiltered otherwise (stuck_* / supersession)."""
    suffix, day_col, _keys = _MATVIEW[invariant]
    sql = (
        f"SELECT count(*) FROM {prefix}_{suffix}"
        + _period_where(day_col, period, dialect)
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        return int(cur.fetchone()[0])


# =====================================================================
# L2 invariant anchors (AT.5.b — Investigation matviews)
# =====================================================================
#
# The L2 dashboard tables aren't simple flat row-per-matview-row shapes
# like the L1 ones; both filter the matview at the dataset SQL level
# via a parameter pushdown. So the "direct SELECT" anchor takes the
# same parameter value the dashboard's slider/dropdown is set to, and
# the test asserts apples-to-apples agreement against that filtered
# row set rather than the unfiltered matview total (which would only
# agree with the *distribution* visual, not the detail tables).
#
# - ``anomaly`` — ``<prefix>_inv_pair_rolling_anomalies`` filtered by
#   ``z_score >= <sigma>`` (matches build_volume_anomalies_dataset's
#   ``WHERE 1=1 AND z_score >= <<$pInvAnomaliesSigma>>``); natural key
#   tuple ``(sender_account_id, recipient_account_id, window_end)``
#   matches the "Flagged Pair-Windows — Ranked" table's group_by.
# - ``money_trail`` — ``<prefix>_inv_money_trail_edges`` filtered by
#   ``root_transfer_id = <root>`` (matches build_money_trail_dataset's
#   chain-root pushdown); natural key tuple ``(transfer_id, depth)``
#   matches the "Money Trail — Hop-by-Hop" table's edge identity.
L2Invariant = Literal["anomaly", "money_trail"]


_L2_MATVIEW_SUFFIX: dict[L2Invariant, str] = {
    "anomaly": "inv_pair_rolling_anomalies",
    "money_trail": "inv_money_trail_edges",
}


_L2_KEY_COLS: dict[L2Invariant, tuple[str, ...]] = {
    "anomaly": (
        "sender_account_id", "recipient_account_id", "window_end",
    ),
    "money_trail": ("transfer_id", "depth"),
}


def l2_key_columns_for(invariant: L2Invariant) -> tuple[str, ...]:
    """Natural-key columns for the L2 invariant's matview / detail table."""
    return _L2_KEY_COLS[invariant]


def _normalise_anomaly_cell(value: object, col_name: str) -> str | date:
    if col_name == "window_end":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])
    return str(value)


def _normalise_money_trail_cell(value: object, col_name: str) -> str | int:
    if col_name == "depth":
        return int(value)  # type: ignore[arg-type]: dbapi cursor returns Numeric for INTEGER; int() coerces
    return str(value)


def count_anomaly_matview_rows(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union has no shared Protocol
    prefix: str,
    *,
    sigma_threshold: float,
) -> int:
    """``SELECT count(*) FROM <prefix>_inv_pair_rolling_anomalies WHERE
    z_score >= <sigma>``. Matches the dataset SQL the dashboard runs
    when the σ slider is at ``sigma_threshold``."""
    suffix = _L2_MATVIEW_SUFFIX["anomaly"]
    sql = (
        f"SELECT count(*) FROM {prefix}_{suffix} "
        f"WHERE z_score >= {sigma_threshold}"
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        return int(cur.fetchone()[0])


def anomaly_matview_row_keys(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union has no shared Protocol
    prefix: str,
    *,
    sigma_threshold: float,
) -> set[tuple[str | date, ...]]:
    """Natural-key tuples ``(sender, recipient, window_end)`` for the
    σ-filtered matview row set. The dashboard's "Flagged Pair-Windows
    — Ranked" table group_bys on the same column set."""
    suffix = _L2_MATVIEW_SUFFIX["anomaly"]
    key_cols = _L2_KEY_COLS["anomaly"]
    select_cols = ", ".join(key_cols)
    sql = (
        f"SELECT {select_cols} FROM {prefix}_{suffix} "
        f"WHERE z_score >= {sigma_threshold}"
    )
    out: set[tuple[str | date, ...]] = set()
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            out.add(tuple(
                _normalise_anomaly_cell(v, c)
                for v, c in zip(row, key_cols, strict=True)
            ))
    return out


def count_money_trail_matview_rows(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union has no shared Protocol
    prefix: str,
    *,
    root_transfer_id: str,
) -> int:
    """``SELECT count(*) FROM <prefix>_inv_money_trail_edges WHERE
    root_transfer_id = <root>``. Matches the dataset SQL the dashboard
    runs when the chain-root dropdown is set to ``root_transfer_id``."""
    suffix = _L2_MATVIEW_SUFFIX["money_trail"]
    sql = (
        f"SELECT count(*) FROM {prefix}_{suffix} "
        f"WHERE root_transfer_id = '{root_transfer_id}'"
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        return int(cur.fetchone()[0])


def money_trail_matview_row_keys(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union has no shared Protocol
    prefix: str,
    *,
    root_transfer_id: str,
) -> set[tuple[str | int, ...]]:
    """Natural-key tuples ``(transfer_id, depth)`` for the root-filtered
    matview row set. The dashboard's "Money Trail — Hop-by-Hop" table
    surfaces one row per edge."""
    suffix = _L2_MATVIEW_SUFFIX["money_trail"]
    key_cols = _L2_KEY_COLS["money_trail"]
    select_cols = ", ".join(key_cols)
    sql = (
        f"SELECT {select_cols} FROM {prefix}_{suffix} "
        f"WHERE root_transfer_id = '{root_transfer_id}'"
    )
    out: set[tuple[str | int, ...]] = set()
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            out.add(tuple(
                _normalise_money_trail_cell(v, c)
                for v, c in zip(row, key_cols, strict=True)
            ))
    return out


def distinct_money_trail_roots(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union has no shared Protocol
    prefix: str,
) -> list[str]:
    """Every distinct ``root_transfer_id`` in the matview. The test uses
    this to discover a planted root to drive the dropdown with — the
    matview names the chains the dashboard would show."""
    suffix = _L2_MATVIEW_SUFFIX["money_trail"]
    sql = f"SELECT DISTINCT root_transfer_id FROM {prefix}_{suffix}"
    with conn.cursor() as cur:
        cur.execute(sql)
        return [str(row[0]) for row in cur.fetchall()]


def l1_invariant_matview_row_keys(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union has no shared Protocol
    prefix: str,
    invariant: L1Invariant,
    period: DateInterval | None,
    dialect: Dialect,
) -> set[tuple[str | date, ...]]:
    """The set of natural-key tuples in the named invariant's matview,
    narrowed to ``period`` (when applicable).

    Keys mirror the scenario's row-identity tuples: ``(account_id, day)``
    for drift / overdraft, ``(account_id, day, rail_name)`` for
    limit_breach (Z.B subsumed ``transfer_type`` into the rail),
    ``(transaction_id,)`` for the divergent-shape ones.
    Day values are normalised to a ``date`` (the cursor returns a
    ``datetime`` for PG/Oracle TIMESTAMP cols, a ``str`` for SQLite TEXT)
    so they compare against the scenario's ``date``-typed ``_eff(p)``.
    """
    suffix, day_col, key_cols = _MATVIEW[invariant]
    select_cols = ", ".join(key_cols)
    sql = (
        f"SELECT {select_cols} FROM {prefix}_{suffix}"
        + _period_where(day_col, period, dialect)
    )
    out: set[tuple[str | date, ...]] = set()
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            out.add(tuple(_normalise_key_cell(c, col) for c, col in zip(row, key_cols, strict=True)))
    return out


def _normalise_key_cell(value: object, col_name: str) -> str | date:
    """Normalise a cursor cell to ``date`` (for a day column) or ``str``.

    Day columns come back as ``datetime`` (PG/Oracle TIMESTAMP), ``date``,
    or an ISO ``str`` (SQLite TEXT). Everything else (account_id,
    rail_name, transaction_id) → ``str``."""
    if col_name in ("business_day_start", "business_day_end", "business_day"):
        if isinstance(value, datetime):  # check before date — datetime IS a date
            return value.date()
        if isinstance(value, date):
            return value
        # SQLite: an ISO string like '2026-05-07 00:00:00' or '2026-05-07'.
        return date.fromisoformat(str(value)[:10])
    return str(value)
