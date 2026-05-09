"""X.1.d.1 — shared Layer 1 (matview presence) query helpers.

Pattern: **before** asserting that a deployed dashboard renders a row,
query the matview directly via psycopg2 / oracledb and confirm the row
is actually there. When Layer 1 passes but Layer 2 fails, the bug is in
QS rendering; when Layer 1 fails, the bug is in seed / matview / refresh.

This module's helpers replaced the legacy harness's
``_harness_l1_assertions.py::assert_l1_matview_rows_present``
(deleted in Y.2.gate.f.9); per-test asserts now live inline in
``test_l1_*.py`` / ``test_inv_*.py`` / ``test_exec_*.py`` /
``test_l2ft_*.py`` using these primitives directly.

Why this matters for X.2: the multi-renderer thesis (QuickSight is one
dialect, audit PDF another, HTMX a third) requires the test suite to
isolate "data layer correct" from "renderer correct". Layer 1 + Layer 2
makes that distinction explicit and renderer-agnostic — Layer 2 can
swap to drive any renderer; Layer 1 stays the same.

Used by browser e2e tests (`test_l1_*.py`, `test_inv_*.py`, `test_exec_*.py`,
`test_l2ft_metadata_cascade.py`) to gate the existing render assertions
behind a fast matview check. Each helper here works equally well on
Postgres + Oracle by branching on the connection's dialect via the
caller-supplied ``dialect`` parameter.
"""

from __future__ import annotations

from typing import Any

from quicksight_gen.common.sql.dialect import Dialect


def _placeholder(dialect: Dialect, position: int) -> str:
    """Per-dialect bind-parameter placeholder.

    Postgres ``psycopg2`` uses ``%s`` (positional, all the same shape);
    Oracle ``oracledb`` uses ``:1``, ``:2``, ... (positional, 1-indexed);
    SQLite ``sqlite3`` uses ``?`` (positional, all the same shape).
    """
    if dialect is Dialect.ORACLE:
        return f":{position}"
    if dialect is Dialect.SQLITE:
        return "?"
    return "%s"


def _exec_one(db_conn: Any, sql: str, params: tuple[Any, ...]) -> Any:
    """Run a single SQL with params; return all rows.

    Wraps the ``cursor()`` lifecycle so callers don't have to. The
    sqlite3 ``Cursor`` doesn't implement the context-manager protocol
    that psycopg2 / oracledb do (see the X.3.f
    ``connect_and_apply`` SQLite cursor fix), so this helper uses
    explicit close instead of ``with``.
    """
    cur = db_conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()


def _exec_one_scalar(
    db_conn: Any, sql: str, params: tuple[Any, ...],
) -> Any:
    """Run a single SQL expected to return one row, return the first column."""
    cur = db_conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        cur.close()


def query_matview_rows(
    db_conn: Any,
    table: str,
    where: dict[str, Any] | None = None,
    *,
    dialect: Dialect = Dialect.POSTGRES,
    columns: list[str] | None = None,
    limit: int | None = None,
) -> list[tuple[Any, ...]]:
    """Run a parameterized SELECT against ``table`` and return all rows.

    Args:
        db_conn: live database connection (psycopg2 / oracledb).
        table: fully-qualified table or matview name (e.g.
            ``qs_ci_xxx_pg_drift``). Caller is responsible for the
            prefix join.
        where: optional ``{column: value}`` filter joined by AND. Pass
            ``None`` for an unfiltered SELECT.
        dialect: SQL dialect of the connection. Drives the placeholder
            syntax — Postgres uses ``%s``, Oracle uses ``:1``/``:2``/...
        columns: optional projection. ``None`` selects ``*``.
        limit: optional row cap. ``None`` returns all rows.

    Returns:
        List of row tuples, one per matched row. Empty list if no
        matches. Caller decides what to do (assertion / drill-down).
    """
    select_cols = "*" if not columns else ", ".join(columns)
    sql = f"SELECT {select_cols} FROM {table}"
    params: list[Any] = []
    if where:
        clauses: list[str] = []
        for i, (col, val) in enumerate(where.items(), start=1):
            clauses.append(f"{col} = {_placeholder(dialect, i)}")
            params.append(val)
        sql += " WHERE " + " AND ".join(clauses)
    if limit is not None:
        sql += (
            f" FETCH FIRST {int(limit)} ROWS ONLY"
            if dialect is Dialect.ORACLE
            else f" LIMIT {int(limit)}"
        )
    return list(_exec_one(db_conn, sql, tuple(params)))


def matview_row_count(
    db_conn: Any,
    table: str,
    where: dict[str, Any] | None = None,
    *,
    dialect: Dialect = Dialect.POSTGRES,
) -> int:
    """Return the row count for ``table`` filtered by ``where``.

    Convenience wrapper around ``query_matview_rows`` that issues a
    ``SELECT COUNT(*)`` instead of materializing rows. Use when you
    only care whether a row class exists, not the row contents.
    """
    sql = f"SELECT COUNT(*) FROM {table}"
    params: list[Any] = []
    if where:
        clauses: list[str] = []
        for i, (col, val) in enumerate(where.items(), start=1):
            clauses.append(f"{col} = {_placeholder(dialect, i)}")
            params.append(val)
        sql += " WHERE " + " AND ".join(clauses)
    val = _exec_one_scalar(db_conn, sql, tuple(params))
    return int(val) if val is not None else 0


def assert_matview_has_row(
    db_conn: Any,
    table: str,
    where: dict[str, Any],
    *,
    dialect: Dialect = Dialect.POSTGRES,
    context: str | None = None,
) -> None:
    """Assert ``table`` has at least one row matching every (col, val)
    in ``where``. The Layer 1 primitive — call this before any Layer 2
    render assertion to verify the data layer holds the row the
    dashboard is supposed to surface.

    On failure, the AssertionError reports both the missed match AND
    the table's total row count, so the diagnostic ladder is one line:
    "row missing in a 0-row matview" → seed regression; "row missing
    in a 12000-row matview" → filter / column-name drift.

    Args:
        db_conn: live database connection.
        table: fully-qualified table or matview name.
        where: ``{column: value}`` filter — AT LEAST ONE row must match.
        dialect: SQL dialect of the connection.
        context: optional caller-supplied prose for the error message
            (e.g. "test_l1_filters: drift sheet narrowed to ACME").
            Helps when the assertion fires in CI and the test name
            alone doesn't disambiguate.
    """
    matched = matview_row_count(db_conn, table, where, dialect=dialect)
    if matched > 0:
        return
    total = matview_row_count(db_conn, table, dialect=dialect)
    prefix = f"{context}: " if context else ""
    raise AssertionError(
        f"{prefix}Layer 1 miss — matview {table!r} has no row matching "
        f"{where!r}. Total rows in matview: {total}. "
        f"{'Seed/refresh regression — the data layer is empty.' if total == 0 else 'Column drift or filter mismatch — rows exist but not for this query.'}"
    )


def assert_account_in_matview(
    db_conn: Any,
    matview: str,
    account_id: str,
    *,
    dialect: Dialect = Dialect.POSTGRES,
    context: str | None = None,
) -> None:
    """Convenience wrapper: assert a specific ``account_id`` appears
    in ``matview``. Wraps ``assert_matview_has_row`` with the common
    ``where={"account_id": account_id}`` shape.

    Use from browser e2e tests like::

        assert_account_in_matview(
            harness_db_conn, f"{prefix}_drift", "cust-0001-snb",
            dialect=cfg.dialect,
        )
        # ... then drive the dashboard render assertion ...
    """
    assert_matview_has_row(
        db_conn, matview, {"account_id": account_id},
        dialect=dialect, context=context,
    )
