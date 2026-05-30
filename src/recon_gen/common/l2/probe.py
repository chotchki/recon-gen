"""Probe fetcher — BT.2's observed-row side.

``fetch_probe_rows`` queries ``<prefix>_transactions`` filtered to one
L2 slice (rail / template / chain parent) within an operator-selected
date window. Returns a ``ProbeResult`` with up to ``limit`` rows
ordered by posting DESC + the total matching row count so the page
can render "Showing 10 of 1,247".

Three slice kinds:

- **rail** — narrows on ``rail_name = <name>``. The most common case;
  every transaction carries a rail_name.
- **transfer_template** — narrows on ``template_name = <name>``. Only
  template-bundled transfers carry a non-NULL template_name; standalone
  rail firings have NULL there.
- **chain** — narrows on rows whose ``rail_name`` OR ``template_name``
  matches the chain's parent. Picks up parent firings on either
  side (parents can be either a Rail or a TransferTemplate per SPEC).

The fetcher is pure SQL — no contract evaluation, no per-cell ✓/✗
decoration. The companion ``evaluate_predicate`` helper applies a
single ``ColumnPredicate`` to a ``ProbeRow`` so the render layer can
paint each cell; both directions stay decoupled so a CLI / Triage
surface can reuse the same primitives.

Severability: imports the SQL dialect + the async pool protocol +
the BT.5 contract types. No html/render dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, TypeAlias, cast

from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.l2.contract import ColumnPredicate
from recon_gen.common.sql.dialect import Dialect, column_name


ProbeKind: TypeAlias = Literal["rail", "transfer_template", "chain"]


@dataclass(frozen=True, slots=True)
class ProbeRow:
    """One observed transaction row.

    Columns mirror ``<prefix>_transactions``'s contract-relevant subset
    (the columns BT.5's predicates reference). ``metadata`` is the raw
    JSON text — predicate evaluation does the per-key extraction.
    """

    transaction_id: str
    rail_name: str | None
    template_name: str | None
    account_role: str | None
    amount_direction: str
    transfer_parent_id: str | None
    posting: datetime
    metadata: str | None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Probe page payload: up to ``limit`` rows + total matching count.

    ``rows`` is ordered by ``posting DESC`` so the most recent activity
    is visible first (matches the operator's mental model when
    debugging "did the ETL just run"). ``total_count`` is the
    pre-limit COUNT(*) so the page can render "Showing N of M" without
    a second round-trip.
    """

    rows: tuple[ProbeRow, ...]
    total_count: int


async def fetch_probe_rows(
    pool: AsyncConnectionPool,
    prefix: str,
    *,
    kind: ProbeKind,
    name: str,
    date_from: date,
    date_to: date,
    dialect: Dialect,
    limit: int = 25,
) -> ProbeResult:
    """Fetch observed transactions matching the slice + window.

    Args:
      pool: AsyncConnectionPool against the demo DB.
      prefix: L2 instance prefix.
      kind: Slice discriminator.
      name: The L2-declared identifier (rail name / template name /
        chain parent name).
      date_from: Inclusive window start (posting >= date_from 00:00).
      date_to: Inclusive window end (posting < date_to 23:59:59.999...).
        Passed as ``posting <= <date_to> 23:59:59`` to dodge timezone /
        sub-second-precision footguns on Oracle.
      dialect: SQL dialect; drives column-name case folding.
      limit: Row cap; default 25 (the page table size).

    Returns:
      ``ProbeResult`` with rows ordered by posting DESC + the
      pre-limit total count.
    """
    txns = f"{prefix}_transactions"
    where_clause, params = _where_for_kind(kind, name, date_from, date_to, dialect)

    cols = ", ".join(_select_columns(dialect))
    select_sql = (
        f"SELECT {cols} FROM {txns} "
        f"WHERE {where_clause} "
        f"ORDER BY {column_name('posting', dialect)} DESC "
        f"{_limit_clause(dialect, limit)}"
    )
    count_sql = (
        f"SELECT COUNT(*) FROM {txns} WHERE {where_clause}"
    )

    async with pool.acquire() as conn:
        rows = await _execute_fetchall(conn, select_sql, params, dialect)
        count_rows = await _execute_fetchall(conn, count_sql, params, dialect)

    total = int(count_rows[0][0]) if count_rows else 0
    return ProbeResult(
        rows=tuple(_row_from_tuple(r) for r in rows),
        total_count=total,
    )


def evaluate_predicate(predicate: ColumnPredicate, row: ProbeRow) -> bool | None:
    """Apply one ``ColumnPredicate`` to a ``ProbeRow``.

    Returns:
      - ``True`` if the predicate holds on the row.
      - ``False`` if the predicate is contradicted.
      - ``None`` if the predicate's column has no value to evaluate
        against (e.g. a NULL ``account_role`` on a row whose contract
        expects an ``account_role IN {…}`` membership — not a violation
        per se, but also not a confirmation; render with "—").

    Metadata keys (``column = "metadata.<key>"``) are evaluated by a
    naive JSON-text search for the key — the same shape SPEC §F4's
    SQL/JSON path expressions use. Robust enough for the probe surface;
    Triage (BT.4) does proper JSON_VALUE extraction via SQL.
    """
    if predicate.column.startswith("metadata."):
        return _evaluate_metadata_predicate(predicate, row)

    value: object
    if predicate.column == "rail_name":
        value = row.rail_name
    elif predicate.column == "template_name":
        value = row.template_name
    elif predicate.column == "account_role":
        value = row.account_role
    elif predicate.column == "amount_direction":
        value = row.amount_direction
    elif predicate.column == "transfer_parent_id":
        value = row.transfer_parent_id
    else:
        # Unrecognized contract column — treat as inconclusive rather
        # than fail loudly. The contract module is BT.5's surface; if
        # BT.5 grows a new predicate column, this branch returning
        # None means the Probe shows it as "—" instead of crashing.
        return None

    if predicate.kind == "not_null":
        return value is not None
    if value is None:
        return None
    if predicate.kind == "equals":
        return value == predicate.expected
    if predicate.kind == "one_of":
        expected = cast(tuple[str, ...], predicate.expected)
        return value in expected
    return None


def _evaluate_metadata_predicate(
    predicate: ColumnPredicate, row: ProbeRow,
) -> bool | None:
    """Per-key JSON metadata presence check via substring scan.

    The contract surface today only emits ``metadata.<key>`` predicates
    of kind ``not_null`` (per BT.5's derivation). A non-NULL row whose
    metadata JSON contains the quoted key returns True; missing key
    returns False; NULL metadata returns None.
    """
    if row.metadata is None:
        return None
    # The metadata column is JSON text; presence-of-key is good enough
    # for probe-level rendering. False positives on a key-named-as-a-
    # value are vanishingly rare under realistic metadata shapes; the
    # cost of a false positive is a green ✓ in the probe view that
    # Triage would flag as a real gap on its more careful SQL/JSON
    # extraction.
    key = predicate.column[len("metadata."):]
    return f'"{key}"' in row.metadata


# -- SQL composition (private) -----------------------------------------------


def _where_for_kind(
    kind: ProbeKind, name: str, date_from: date, date_to: date, dialect: Dialect,
) -> tuple[str, list[Any]]:  # typing-smell: ignore[explicit-any]: per-dialect bind value union (str/datetime/int) widens to Any
    """Build the WHERE clause + bind list for one slice kind.

    Date window is inclusive both ends; clamped to whole-day boundaries
    so a posting at 23:59:59 on the end date matches.
    """
    posting_col = column_name("posting", dialect)
    start = datetime(date_from.year, date_from.month, date_from.day, 0, 0, 0)
    end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
    ph = _placeholder(dialect)

    if kind == "rail":
        col = column_name("rail_name", dialect)
        return (
            f"{col} = {ph(0)} AND {posting_col} BETWEEN {ph(1)} AND {ph(2)}",
            [name, start, end],
        )
    if kind == "transfer_template":
        col = column_name("template_name", dialect)
        return (
            f"{col} = {ph(0)} AND {posting_col} BETWEEN {ph(1)} AND {ph(2)}",
            [name, start, end],
        )
    # chain — match rows whose rail_name OR template_name is the parent.
    rail_col = column_name("rail_name", dialect)
    tmpl_col = column_name("template_name", dialect)
    return (
        f"({rail_col} = {ph(0)} OR {tmpl_col} = {ph(1)}) "
        f"AND {posting_col} BETWEEN {ph(2)} AND {ph(3)}",
        [name, name, start, end],
    )


def _placeholder(dialect: Dialect) -> Callable[[int], str]:
    """Driver-specific positional placeholder.

    psycopg / aiosqlite use ``%s`` / ``?``; Oracle binds positional via
    ``:1`` / ``:2`` / etc. The factory returns ``"?"`` / ``"%s"`` for
    indexed PG/SQLite (ignores the index since they're positional) and
    ``f":{i+1}"`` for Oracle.
    """
    if dialect is Dialect.ORACLE:
        return lambda i: f":{i + 1}"
    if dialect is Dialect.SQLITE:
        return lambda _i: "?"
    return lambda _i: "%s"


def _select_columns(dialect: Dialect) -> tuple[str, ...]:
    """The contract-relevant transactions columns the probe reads back.

    Names cased per dialect via ``column_name`` so Oracle's
    UPPER-cased identifier convention doesn't leak.
    """
    return (
        column_name("id", dialect),
        column_name("rail_name", dialect),
        column_name("template_name", dialect),
        column_name("account_role", dialect),
        column_name("amount_direction", dialect),
        column_name("transfer_parent_id", dialect),
        column_name("posting", dialect),
        column_name("metadata", dialect),
    )


def _row_from_tuple(row: tuple[Any, ...]) -> ProbeRow:  # typing-smell: ignore[explicit-any]: row tuples are driver-returned heterogeneous Any
    """Coerce a raw cursor tuple to a ``ProbeRow``."""
    return ProbeRow(
        transaction_id=str(row[0]),
        rail_name=None if row[1] is None else str(row[1]),
        template_name=None if row[2] is None else str(row[2]),
        account_role=None if row[3] is None else str(row[3]),
        amount_direction=str(row[4]),
        transfer_parent_id=None if row[5] is None else str(row[5]),
        posting=cast(datetime, row[6]),
        metadata=None if row[7] is None else str(row[7]),
    )


def _limit_clause(dialect: Dialect, limit: int) -> str:
    """Per-dialect LIMIT clause.

    PG + SQLite use ``LIMIT N``; Oracle 12c+ uses ``FETCH FIRST N
    ROWS ONLY``.
    """
    if dialect is Dialect.ORACLE:
        return f"FETCH FIRST {int(limit)} ROWS ONLY"
    return f"LIMIT {int(limit)}"


async def _execute_fetchall(
    conn: object, sql: str, params: list[Any], dialect: Dialect,  # typing-smell: ignore[explicit-any]: params is heterogeneous bind list (str/datetime/int) — driver union widens to Any
) -> list[tuple[Any, ...]]:  # typing-smell: ignore[explicit-any]: row tuples are driver-typed; per-call shape lives in the SELECT contract
    """Driver-uniform execute + fetchall.

    Mirrors the cursor-handling shape in ``coverage.py::_fetch_count_map``
    and ``_sql_executor.execute_visual_sql_async``: Oracle needs an
    explicit cursor open; psycopg / aiosqlite return a cursor from
    ``await conn.execute(...)`` directly.
    """
    if dialect is Dialect.ORACLE:
        cur: Any = cast(Any, conn).cursor()  # typing-smell: ignore[explicit-any]: per-driver cursor union has no shared Protocol
        await cur.execute(sql, params)
    else:
        cur = await cast(Any, conn).execute(sql, params)  # typing-smell: ignore[explicit-any]: psycopg / aiosqlite cursor types not unified by a single Protocol
    try:
        rows: list[Any] = await cur.fetchall()  # typing-smell: ignore[explicit-any]: driver-typed row union widens to Any after Any cursor
    finally:
        close = getattr(cur, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
    return [tuple(r) for r in rows]
