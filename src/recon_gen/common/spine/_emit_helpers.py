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

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from recon_gen.common.db import SyncConnection
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import Account, Identifier, L2Instance
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
    Decimals (seed test fixtures); ETL integrators bulk-loading from
    CSV pass strings (``"100.50"``); downstream parallel agents may
    pass already-converted ``Cents``. Coerce all shapes at this one
    boundary so the wire path is uniform.

    None passes through (NULL column). ``Cents`` → its ``.value``.
    ``int`` passes through unchanged ONLY when already in cents shape
    is impossible to distinguish from "dollar int"; the spine never
    passes an int as a money kwarg today (always float / Decimal),
    so route ints through ``from_dollars`` for consistency. Bool is
    treated as int (defensive — Python's ``isinstance(True, int)``).
    ``str`` routes through ``from_dollars`` — CSV bulk loads land here.

    Raises ``TypeError`` on any unrecognized type. The previous silent
    passthrough surfaced as opaque downstream BIGINT INSERT failures
    (PG: "invalid input syntax for type bigint"; Oracle: ORA-01722)
    that gave no breadcrumb back to the bad caller — explicit error
    at the coerce boundary points at the offending value.
    """
    if value is None:
        return None
    if isinstance(value, Cents):
        return value.value
    if isinstance(value, bool):
        # Defensive — Python's bool is an int subclass; route through
        # from_dollars to keep the contract uniform (True→100, False→0).
        return Cents.from_dollars(int(value)).value
    if isinstance(value, (Decimal, int, str)):
        # Cents.from_dollars accepts ``Decimal | str | int`` directly;
        # str path unblocks CSV bulk loads where every column lands as
        # a string (the canonical csv.DictReader / pandas object dtype
        # shape).
        return Cents.from_dollars(value).value
    if isinstance(value, float):
        # str() avoids float-init Decimal drift (Decimal(0.1) !=
        # Decimal('0.1')) — same convention as Cents.from_dollars.
        return Cents.from_dollars(str(value)).value
    raise TypeError(
        f"_coerce_to_cents_int: unsupported money value type "
        f"{type(value).__name__} (got {value!r}); supported: None, "
        f"Cents, Decimal, int, str, float, bool"
    )


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
    conn: SyncConnection,
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
    just the in-process DuckDB harness). ``SyncConnection`` is a
    ``Protocol`` covering the DB-API 2.0 surface — psycopg / oracledb
    / sqlite3 / duckdb all match structurally; ``_placeholder_style``
    sniffs the connection's module to pick the placeholder syntax.
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
    conn: SyncConnection,
    *,
    prefix: str = DEFAULT_PREFIX,
    **vals: object,
) -> None:
    """Insert one row into ``<prefix>_daily_balances``. Mirrors
    `insert_tx` for the balance table — same dialect dispatch via the
    ``SyncConnection`` Protocol."""
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


# Bulk-load chunk size — bounds the SQL string size for the DuckDB
# multi-row VALUES path and caps per-call payload for psycopg
# executemany / oracledb executemany. Matches the 1000-row batch the
# CA.10 `_flush_duckdb_multivalues` coalescer uses.
_BULK_CHUNK = 1000


def _coerce_tuple_money(
    row: tuple[object, ...],
    cols: tuple[str, ...],
    money_cols: frozenset[str],
) -> tuple[object, ...]:
    """Return ``row`` with every money-column slot routed through
    ``_coerce_to_cents_int``. Pure (returns a new tuple); positional
    over the canonical ``cols`` ordering.
    """
    return tuple(
        _coerce_to_cents_int(v) if cols[i] in money_cols else v
        for i, v in enumerate(row)
    )


def _bulk_insert(
    conn: SyncConnection,
    table: str,
    cols: tuple[str, ...],
    rows: Sequence[tuple[object, ...]],
    money_cols: frozenset[str],
) -> None:
    """Shared dispatch: chunk the rows + delegate to the dialect's fast
    path.

    Per-dialect strategy:

    - **DuckDB** (qmark default — `_placeholder_style` returns "qmark"
      for duckdb because the connection module name doesn't match
      psycopg/oracledb): use the CA.10 multi-row ``VALUES (…),(…),…``
      coalescer (`_flush_duckdb_multivalues`). Measured 54× faster
      than DuckDB's executemany at 50k rows.
    - **PG / psycopg** ("format"): ``cursor.executemany`` is the real
      bulk path (psycopg pipelines the binds).
    - **Oracle / oracledb** ("numeric"): ``cursor.executemany`` —
      oracledb's executemany is also the real fast path and unlike
      `batch_oracle_inserts`'s `INSERT ALL` shape, each iteration
      gets its own IDENTITY value so composite (id, entry) PKs
      don't collide.

    Empty ``rows`` is a no-op — no cursor open, no SQL parse.
    """
    if not rows:
        return
    coerced: list[tuple[object, ...]] = [
        _coerce_tuple_money(r, cols, money_cols) for r in rows
    ]
    style = _placeholder_style(conn)
    cols_str = ", ".join(cols)
    if style == "qmark":
        # DuckDB — reuse the existing multi-row VALUES coalescer. It
        # batches internally at 1000 rows, so we hand it the full list.
        # Local import avoids re-routing the top-level module graph;
        # `_flush_duckdb_multivalues` is module-private under db.py but
        # the bulk helpers are the canonical low-level surface that
        # depends on it.
        from recon_gen.common.db import (  # noqa: PLC0415
            _flush_duckdb_multivalues,
        )
        cur = conn.cursor()
        try:
            _flush_duckdb_multivalues(cur, table, cols_str, coerced)
        finally:
            cur.close()
        return
    # PG / Oracle — executemany is the fast path on both. Placeholder
    # style differs (``%s`` vs ``:1, :2, …``) but the API call shape is
    # identical at PEP 249 level.
    placeholders = _build_placeholders(style, len(cols))
    sql = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders})"
    cur = conn.cursor()
    try:
        for start in range(0, len(coerced), _BULK_CHUNK):
            chunk = coerced[start:start + _BULK_CHUNK]
            cur.executemany(sql, chunk)
    finally:
        cur.close()


def bulk_insert_tx(
    conn: SyncConnection,
    rows: Sequence[tuple[object, ...]],
    *,
    prefix: str = DEFAULT_PREFIX,
    columns: Sequence[str] | None = None,
) -> None:
    """Bulk-load rows into ``<prefix>_transactions`` via the dialect's
    fast path.

    Public surface for `etl_hook` integrators: a single call takes a
    sequence of tuples (positional in column order) and lands them
    using ``_flush_duckdb_multivalues`` (DuckDB) / ``executemany``
    (PG / Oracle). Avoids the per-row ``cur.execute`` cost of
    `insert_tx` — sized for 10k+ row loads.

    Column shape:

    - **Default (``columns=None``)** uses ``TX_COLS`` — the canonical
      spine-author subset that excludes ``entry`` (auto-increment),
      ``transfer_completion`` (optional), and ``bundle_id`` (NULL by
      default — stuck_unbundled's plant relies on this). Plant
      generators byte-stable; all in-repo callers stay on this path.
    - **``columns=<tuple>``** loads any subset of the schema. Pass to
      include ``transfer_completion`` / ``bundle_id`` / any future
      column ETL integrators need. Tuple shape MUST match
      ``len(columns)`` and column ORDER. Money columns (``amount_money``)
      still auto-coerce regardless of position.

    Money columns auto-coerce dollar shapes (float / Decimal / str /
    int / Cents) to BIGINT cents via `_coerce_to_cents_int`. CSV bulk
    loads where every column is a string land cleanly here.

    Empty ``rows`` is a no-op (no cursor open, no SQL parsed).

    **Bulk helpers do not stamp ``metadata.source``** — integrators
    control the metadata column explicitly. For training / plant rows
    that need the `source='training'` stamp, build the metadata JSON
    via `recon_gen.common.spine.scenario_context.scenario_metadata(...)`
    and put the resulting string in the tuple's metadata slot. The
    low-level surface stays low-level on purpose: stamping at the bulk
    boundary would silently overwrite intentional `source='real'`
    rows that an integrator is loading.
    """
    cols = tuple(columns) if columns is not None else TX_COLS
    _bulk_insert(
        conn,
        f"{prefix}_transactions",
        cols,
        rows,
        _TX_MONEY_COLS,
    )


def bulk_insert_balance(
    conn: SyncConnection,
    rows: Sequence[tuple[object, ...]],
    *,
    prefix: str = DEFAULT_PREFIX,
    columns: Sequence[str] | None = None,
) -> None:
    """Bulk-load rows into ``<prefix>_daily_balances`` via the dialect's
    fast path.

    Mirrors `bulk_insert_tx` for the balance table — same positional
    tuple contract, same money coercion (``money`` +
    ``expected_eod_balance`` are BIGINT cents), same
    metadata-not-stamped contract (see `bulk_insert_tx` docstring).

    Column shape: ``columns=None`` → ``DB_COLS`` (spine-author subset);
    ``columns=<tuple>`` → arbitrary subset (e.g. add ``supersedes`` for
    technical-correction loads).
    """
    cols = tuple(columns) if columns is not None else DB_COLS
    _bulk_insert(
        conn,
        f"{prefix}_daily_balances",
        cols,
        rows,
        _DB_MONEY_COLS,
    )


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
    # DY.7.2 — fall back to account_templates. Template-driven L2s
    # (sasquatch_pr) declare their internal-LEAF accounts as templates that
    # materialize to concrete accounts (cust-NNNN) only at seed time, so
    # they're absent from ``instance.accounts`` and the loop above misses
    # them — which used to SKIP the anomaly / money-trail scenarios on
    # sasquatch entirely. The scenario builders read only ``role`` +
    # ``parent_role`` off this return (they mint their own synthetic
    # account_id), so a representative Account synthesized from the template
    # is sufficient; its id is a real template-materialized id for legibility.
    for t in instance.account_templates:
        if t.role != role or t.scope != "internal":
            continue
        if must_be_leaf and t.parent_role is None:
            continue
        from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
            template_instance_ids,
        )
        rep_ids = template_instance_ids(t)
        rep_id = rep_ids[0] if rep_ids else f"tmpl-{role}"
        return Account(
            id=Identifier(rep_id),
            scope=t.scope,
            role=t.role,
            parent_role=t.parent_role,
        )
    leaf_phrase = " leaf" if must_be_leaf else ""
    raise ValueError(
        f"shape has no {error_kind}-eligible{leaf_phrase} internal "
        f"account with role {role!r}; cannot manufacture a scenario"
    )
