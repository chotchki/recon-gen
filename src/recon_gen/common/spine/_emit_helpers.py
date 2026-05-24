"""Shared emit-side helpers across the spine's concrete invariant modules.

Hoisted from drift / overdraft / expected_eod / stuck_pending /
stuck_unbundled / limit_breach at the AU.3.d follow-on (each module
previously kept its own copy of these helpers; at 6 modules the
duplication was an obvious smell — promoting before AU.5's
exhaustiveness gate composes generators across the registry).

Module-private (leading underscore) — concrete invariant modules import
from here, but the spine's public surface (`common.spine.__init__`)
doesn't re-export. Callers outside the spine should not depend on
these helpers; their shape will follow the spine's needs.

What lives here:

- `TX_COLS`, `DB_COLS` — the column tuples for `_transactions` +
  `_daily_balances` INSERT statements (the subset every generator
  uses; ignores per-row supersession / metadata / template_name
  columns that no generator touches).
- `insert_tx`, `insert_balance` — INSERT-helper functions taking a
  prefix kwarg (default "spec_example" — the in-process harness
  shape). Production-deploy callers thread the deployment's prefix.
- `day_bounds`, `ts`, `to_date` — date/timestamp formatting helpers.
- `load_spec_example` — the bundled `tests/l2/spec_example.yaml` loader.
- `find_internal_with_role` — single L2 account finder, with a
  `must_be_leaf` kwarg covering both drift's "leaf account with parent"
  case and overdraft/expected_eod's "any internal account" case.

What does NOT live here:

- Per-invariant-shape finders (`find_rail_with_max_pending_age`,
  `find_limit_schedule`, `find_child_with_parent_role`). These stay
  in their owning module — single use site, no duplication.
- The TZ convention helpers (used by stuck_pending /
  stuck_unbundled / anomaly). They're wall-clock-specific; each
  caller wraps `datetime.now()` differently. See
  `[[project-local-tz-convention]]`.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import Account, L2Instance
from recon_gen.common.money import Cents

# Default prefix for the in-process test harness shape. Production
# callers thread their deployment's prefix via the kwarg.
DEFAULT_PREFIX = "spec_example"


def _placeholder_style(conn: object) -> str:
    """Detect the dbapi placeholder style for ``conn``.

    Returns one of:

    - ``"qmark"`` — SQLite (``?``). The in-process test shape.
    - ``"format"`` — psycopg / PG (``%s``). The deployed-DB shape that
      AT.5.b started exercising.
    - ``"numeric"`` — oracledb (``:1``, ``:2``, …). Oracle's deployed shape.

    Detected by module name rather than ``isinstance`` so the spine
    avoids hard import dependencies on psycopg / oracledb (they're
    optional extras). Falls through to ``"qmark"`` (SQLite) so the
    in-process test harness — which doesn't carry psycopg/oracledb in
    its baseline — stays byte-identical.

    AT.5.b (2026-05-23) added this so the spine generators emit
    directly into the deployed dialect — same generators, same SQL
    shape — rather than requiring a parallel hand-rolled plant path
    per dialect.
    """
    mod = type(conn).__module__
    if mod.startswith("psycopg"):
        return "format"
    if mod.startswith("oracledb"):
        return "numeric"
    return "qmark"


def _build_placeholders(style: str, n: int) -> str:
    """``", ".join(...)`` of ``n`` placeholders in the given style."""
    if style == "numeric":
        return ", ".join(f":{i + 1}" for i in range(n))
    if style == "format":
        return ", ".join("%s" for _ in range(n))
    return ", ".join("?" for _ in range(n))


def _coerce_to_cents_int(value: object) -> object:
    """Coerce a money kwarg to integer cents at the insert boundary.

    AO.1: the three money columns (amount_money / money /
    expected_eod_balance) store BIGINT cents on every dialect. Spine
    generators author in floats (``leg_amount: float = 100.0``) and
    Decimals (seed test fixtures); downstream parallel agents may
    pass already-converted ``Cents``. Coerce all three shapes at this
    one boundary so the wire path is uniform.

    None passes through (NULL column). ``Cents`` → its ``.value``.
    ``int`` passes through unchanged ONLY when already in cents shape
    is impossible to distinguish from "dollar int"; the spine never
    passes an int as a money kwarg today (always float / Decimal),
    so route ints through ``from_dollars`` for consistency. Bool is
    treated as int (defensive — Python's ``isinstance(True, int)``).
    """
    if value is None:
        return None
    if isinstance(value, Cents):
        return value.value
    if isinstance(value, bool):
        # Defensive — Python's bool is an int subclass; route through
        # from_dollars to keep the contract uniform (True→100, False→0).
        return Cents.from_dollars(int(value)).value
    if isinstance(value, (Decimal, int)):
        return Cents.from_dollars(value).value
    if isinstance(value, float):
        # str() avoids float-init Decimal drift (Decimal(0.1) !=
        # Decimal('0.1')) — same convention as Cents.from_dollars.
        return Cents.from_dollars(str(value)).value
    return value


# AO.1: money columns that need dollar→cents coercion at the insert
# boundary. Kept as module-level sets so the dispatch is a constant-time
# lookup per kwarg.
_TX_MONEY_COLS = frozenset({"amount_money"})
_DB_MONEY_COLS = frozenset({"money", "expected_eod_balance"})


TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)
"""Columns every generator writes to ``_transactions``. Excludes
``entry`` (auto-increment by the dialect), ``transfer_completion``
(optional), ``bundle_id`` (NULL by default — stuck_unbundled's plant
explicitly relies on this).

AV.5 added ``metadata``: ``insert_tx`` callers that thread
``ScenarioContext`` pass a JSON string carrying ``{"scenario_id": ...}``;
untagged callers pass nothing (vals.get(``metadata``) returns None →
SQL NULL — byte-identical to pre-AV.5).

AX.1 added ``template_name``: AX-promoted L2-shape generators
(chain_parent_disagreement / xor_group_violation /
fan_in_disagreement / multi_xor_violation) all key the matview GROUP
BY on ``template_name`` so the spine emit needs to set it.
Pre-AX callers (drift / overdraft / anomaly / etc.) pass nothing →
SQL NULL → byte-identical to pre-AX.

AY.2.b added ``supersedes``: SupersessionGenerator emits the
TechnicalCorrection row with ``supersedes='TechnicalCorrection'`` so
the M.2b.12 Supersession Audit dataset's
``COUNT(*) OVER (PARTITION BY id) > 1`` + ``supersedes IS NOT NULL``
filter catches the pair. Other callers pass nothing → SQL NULL."""


DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money", "metadata",
)
"""Columns every generator writes to ``_daily_balances``. Excludes
``entry`` (supersession), ``supersedes``. AV.5 added ``metadata`` (the
column was renamed from ``limits`` in AV.1; AV.5 made it a writable
slot for the spine generators alongside ``transactions.metadata``):
``insert_balance`` callers that thread ``ScenarioContext`` pass a JSON
string carrying ``{"scenario_id": ...}``; untagged callers pass
nothing (vals.get(``metadata``) returns None → SQL NULL — byte-
identical to pre-AV.5)."""


def insert_tx(
    conn: sqlite3.Connection,
    *,
    prefix: str = DEFAULT_PREFIX,
    **vals: object,
) -> None:
    """Insert one row into ``<prefix>_transactions``. Keyword args
    correspond to ``TX_COLS``; missing keys default to SQL NULL.

    `prefix` defaults to the in-process spec_example shape. Generators
    that get prefix-parametric (deploy-time use) will pass it through;
    AU.3.d kept default to preserve the AS-era call sites byte-stable.

    AT.5.b: dialect-aware placeholder style + ``cursor.execute`` path
    so the spine generators emit into deployed PG / Oracle DBs (not
    just the in-process SQLite harness). The annotation still reads
    ``sqlite3.Connection`` because that's the dominant call shape; in
    practice the function accepts any dbapi 2.0 connection (psycopg /
    oracledb / sqlite3) and dispatches via ``_placeholder_style``.
    """
    style = _placeholder_style(conn)
    placeholders = _build_placeholders(style, len(TX_COLS))
    table = f"{prefix}_transactions"
    sql = (
        f"INSERT INTO {table} ({', '.join(TX_COLS)}) "
        f"VALUES ({placeholders})"
    )
    # AO.1: amount_money is BIGINT cents — coerce dollar shapes at the
    # insert boundary so generators can keep authoring in float dollars.
    params = [
        _coerce_to_cents_int(vals.get(c)) if c in _TX_MONEY_COLS
        else vals.get(c)
        for c in TX_COLS
    ]
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
    finally:
        cur.close()


def insert_balance(
    conn: sqlite3.Connection,
    *,
    prefix: str = DEFAULT_PREFIX,
    **vals: object,
) -> None:
    """Insert one row into ``<prefix>_daily_balances``. Mirrors
    `insert_tx` for the balance table — same dialect dispatch."""
    style = _placeholder_style(conn)
    placeholders = _build_placeholders(style, len(DB_COLS))
    table = f"{prefix}_daily_balances"
    sql = (
        f"INSERT INTO {table} ({', '.join(DB_COLS)}) "
        f"VALUES ({placeholders})"
    )
    # AO.1: money + expected_eod_balance are BIGINT cents — coerce
    # dollar shapes at the insert boundary.
    params = [
        _coerce_to_cents_int(vals.get(c)) if c in _DB_MONEY_COLS
        else vals.get(c)
        for c in DB_COLS
    ]
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
    finally:
        cur.close()


def day_bounds(day: date) -> tuple[str, str]:
    """``(business_day_start, business_day_end)`` timestamp pair for a
    given calendar day — midnight-to-midnight UTC-like wall-clock
    formatting. See `[[project-local-tz-convention]]` — these are
    NAIVE timestamps interpreted in the DB's own TZ."""
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )


def ts(day: date, hour: int = 12) -> str:
    """Generator-friendly timestamp formatter — defaults to noon so
    `business_day_start ≤ posting < business_day_end` always holds
    for a given anchor day."""
    return datetime(day.year, day.month, day.day, hour).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


def to_date(bd: object) -> date:
    """Parse a matview-output date string back to ``datetime.date``.
    Tolerates ISO timestamps with trailing time component by truncating
    to the date prefix."""
    return datetime.strptime(str(bd)[:10], "%Y-%m-%d").date()


def load_spec_example() -> L2Instance:
    """Load the bundled ``tests/l2/spec_example.yaml`` — the in-process
    harness shape that the L1 spine generators default to. Production
    callers thread an explicit `instance` kwarg through scenario_for
    and skip this helper."""
    repo_root = Path(__file__).resolve().parents[4]
    return load_instance(repo_root / "tests" / "l2" / "spec_example.yaml")


def find_internal_with_role(
    instance: L2Instance,
    role: str,
    *,
    must_be_leaf: bool = False,
    error_kind: str = "scenario",
) -> Account:
    """Return the first ``Account`` matching ``role`` with
    ``scope='internal'``.

    `must_be_leaf=True` additionally requires ``parent_role IS NOT NULL``
    — drift's smart constructor uses this (drift's matview filters
    parent_role IS NOT NULL). overdraft / expected_eod /
    stuck_pending / stuck_unbundled all accept either leaf or parent.

    Raises `ValueError` with a `error_kind`-flavored message so the
    caller's smart-constructor error text reads naturally
    ("no overdraft-eligible internal account with role ...", etc.)."""
    for a in instance.accounts:
        if a.role != role or a.scope != "internal":
            continue
        if must_be_leaf and a.parent_role is None:
            continue
        return a
    leaf_phrase = " leaf" if must_be_leaf else ""
    raise ValueError(
        f"shape has no {error_kind}-eligible{leaf_phrase} internal "
        f"account with role {role!r}; cannot manufacture a scenario"
    )
