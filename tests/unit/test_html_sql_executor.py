"""X.2.f — SQL executor + dialect placeholder rewrite tests.

The executor is the layer between a Visual's dataset SQL (with
``:name`` placeholders) and the per-renderer shape adapter. Two
concerns:

1. Placeholder dispatch — Postgres rewrites to ``%(name)s``;
   Oracle + SQLite keep ``:name``.
2. Bind-param collection — names referenced in SQL get pulled from
   ``url_params`` (default empty string when absent).

Tests cover both, plus a round-trip against in-memory SQLite to
prove the full executor path works without hitting PG / Oracle
infrastructure.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest

from quicksight_gen.common.db import AsyncConnectionPool, make_connection_pool
from quicksight_gen.common.html._sql_executor import (
    collect_bind_params,
    execute_visual_sql,
    execute_visual_sql_async,
    rewrite_placeholders_for_dialect,
)
from quicksight_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


# ---------------------------------------------------------------------------
# Placeholder rewrite
# ---------------------------------------------------------------------------


def test_rewrite_postgres_uses_pyformat_named_binds() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from AND y >= :amount"
    out = rewrite_placeholders_for_dialect(sql, Dialect.POSTGRES)
    assert "%(date_from)s" in out
    assert "%(amount)s" in out
    assert ":date_from" not in out


def test_rewrite_oracle_keeps_colon_named() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from"
    out = rewrite_placeholders_for_dialect(sql, Dialect.ORACLE)
    assert out == sql


def test_rewrite_sqlite_keeps_colon_named() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from"
    out = rewrite_placeholders_for_dialect(sql, Dialect.SQLITE)
    assert out == sql


def test_rewrite_postgres_preserves_double_colon_cast() -> None:
    """``::float`` is PG cast syntax — must survive the rewrite."""
    sql = "SELECT amount::float FROM t WHERE x = :date_from"
    out = rewrite_placeholders_for_dialect(sql, Dialect.POSTGRES)
    assert "amount::float" in out
    assert "%(date_from)s" in out


def test_rewrite_handles_multiple_placeholders() -> None:
    sql = "SELECT a, :x, :y, c FROM t WHERE z = :x"
    out = rewrite_placeholders_for_dialect(sql, Dialect.POSTGRES)
    # Both occurrences of :x get rewritten.
    assert out.count("%(x)s") == 2
    assert "%(y)s" in out


# ---------------------------------------------------------------------------
# Bind collection
# ---------------------------------------------------------------------------


def test_collect_bind_params_picks_referenced_names() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from AND y = :amount"
    url_params = {
        "date_from": "2030-01-01",
        "amount": "100",
        "filter_status": "open",  # not referenced — should be dropped
    }
    binds = collect_bind_params(sql, url_params)
    assert binds == {"date_from": "2030-01-01", "amount": "100"}


def test_collect_bind_params_defaults_missing_to_empty_string() -> None:
    """Dataset SQL author guards against empty filters; the executor
    just hands back ``""`` so the bind dict is complete."""
    sql = "SELECT * FROM t WHERE x = :date_from"
    binds = collect_bind_params(sql, {})  # nothing in URL
    assert binds == {"date_from": ""}


def test_collect_bind_params_drops_unreferenced_url_params() -> None:
    """Naive callers might pass the entire URL params dict; only
    the names actually in the SQL come through."""
    sql = "SELECT * FROM t"  # no placeholders
    binds = collect_bind_params(sql, {"foo": "1", "bar": "2"})
    assert binds == {}


# ---------------------------------------------------------------------------
# End-to-end executor against in-memory SQLite
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_factory() -> Iterator[Any]:
    """In-memory SQLite seeded with a tiny test table. Yields the
    factory the executor expects (returns a fresh connection per
    call); the fixture closes the underlying conn at teardown."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE t (id INTEGER, name TEXT, amount REAL)"
    )
    conn.executemany(
        "INSERT INTO t VALUES (?, ?, ?)",
        [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)],
    )
    conn.commit()

    def factory() -> Any:
        # Wrap the existing conn so close() is a no-op (executor
        # closes per call but the fixture owns the lifecycle).
        class _ConnWrapper:
            def cursor(self) -> Any:
                return conn.cursor()

            def close(self) -> None:
                pass

        return _ConnWrapper()

    try:
        yield factory
    finally:
        conn.close()


def test_execute_visual_sql_returns_rows_and_columns(sqlite_factory: Any) -> None:
    rows, cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id, name, amount FROM t ORDER BY id",
        {},
        dialect=Dialect.SQLITE,
    )
    assert rows == [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)]
    assert cols == ["id", "name", "amount"]


def test_execute_visual_sql_substitutes_named_filter(sqlite_factory: Any) -> None:
    """``:min_amount`` from URL params lands as a bind value, not
    string-formatted into the SQL — proves the parameterized path."""
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id, name FROM t WHERE amount >= :min_amount ORDER BY id",
        {"min_amount": "20"},
        dialect=Dialect.SQLITE,
    )
    assert [r[1] for r in rows] == ["beta", "gamma"]


def test_execute_visual_sql_handles_multiple_filters(sqlite_factory: Any) -> None:
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        (
            "SELECT id, name FROM t "
            "WHERE amount >= :min_amount AND amount <= :max_amount "
            "ORDER BY id"
        ),
        {"min_amount": "15", "max_amount": "25"},
        dialect=Dialect.SQLITE,
    )
    assert [r[1] for r in rows] == ["beta"]


def test_execute_visual_sql_unreferenced_url_params_dont_break_execution(
    sqlite_factory: Any,
) -> None:
    """The form serializes every input on every Refresh — extra
    params for filters this visual doesn't use must be silently
    dropped, not raised as 'too many parameters'."""
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id FROM t WHERE amount >= :min_amount",
        {
            "min_amount": "15",
            "filter_status": "open",      # unreferenced
            "param_view": "summary",      # unreferenced
            "date_from": "2030-01-01",    # unreferenced
        },
        dialect=Dialect.SQLITE,
    )
    assert {r[0] for r in rows} == {2, 3}


def test_execute_visual_sql_empty_result_set(sqlite_factory: Any) -> None:
    rows, cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id, name FROM t WHERE amount > :min_amount",
        {"min_amount": "9999"},
        dialect=Dialect.SQLITE,
    )
    assert rows == []
    assert cols == ["id", "name"]


# ---------------------------------------------------------------------------
# Postgres dispatch (rewrite verified end-to-end via a fake cursor)
# ---------------------------------------------------------------------------


def test_execute_visual_sql_passes_pg_pyformat_to_cursor() -> None:
    """For Postgres, the cursor must receive ``%(name)s``-form SQL
    plus the bind dict. Validates the rewrite happens inside
    execute_visual_sql, not just at the caller."""
    received: dict[str, Any] = {}

    class _SnoopCursor:
        description = [("col",)]

        def execute(self, sql: str, params: Any = None) -> None:
            received["sql"] = sql
            received["params"] = params

        def fetchall(self) -> list[Any]:
            return []

        def close(self) -> None:
            pass

    class _SnoopConn:
        def cursor(self) -> Any:
            return _SnoopCursor()

        def close(self) -> None:
            pass

    execute_visual_sql(
        lambda: _SnoopConn(),
        "SELECT col FROM t WHERE x = :date_from",
        {"date_from": "2030-01-01"},
        dialect=Dialect.POSTGRES,
    )
    assert "%(date_from)s" in received["sql"]
    assert ":date_from" not in received["sql"]
    assert received["params"] == {"date_from": "2030-01-01"}


# ---------------------------------------------------------------------------
# X.2.n.3 — Async executor against aiosqlite pool
# ---------------------------------------------------------------------------


@pytest.fixture
def aiosqlite_pool() -> Iterator[AsyncConnectionPool]:
    """File-backed aiosqlite pool seeded with the same tiny table.

    aiosqlite's ``:memory:`` mode gives each new connection a fresh
    isolated DB — the shared-pool tests need a tempfile so every
    acquire sees the seeded data.
    """
    import asyncio
    import os
    import sqlite3
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT, amount REAL)")
    conn.executemany(
        "INSERT INTO t VALUES (?, ?, ?)",
        [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)],
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        yield pool
    finally:
        asyncio.run(pool.close())
        os.unlink(path)


def test_execute_visual_sql_async_returns_rows_and_columns(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    import asyncio

    rows, cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id, name, amount FROM t ORDER BY id",
        {},
        dialect=Dialect.SQLITE,
    ))
    assert rows == [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)]
    assert cols == ["id", "name", "amount"]


def test_execute_visual_sql_async_substitutes_named_filter(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    import asyncio

    rows, _cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id, name FROM t WHERE amount >= :min_amount ORDER BY id",
        {"min_amount": "20"},
        dialect=Dialect.SQLITE,
    ))
    assert [r[1] for r in rows] == ["beta", "gamma"]


def test_execute_visual_sql_async_unreferenced_url_params_dont_break(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """Same ignore-unreferenced behavior as the sync version — extra
    URL params for filters this visual doesn't use must be silently
    dropped from the bind dict."""
    import asyncio

    rows, _cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id FROM t WHERE amount >= :min_amount",
        {
            "min_amount": "15",
            "filter_status": "open",
            "param_view": "summary",
            "date_from": "2030-01-01",
        },
        dialect=Dialect.SQLITE,
    ))
    assert {r[0] for r in rows} == {2, 3}


def test_execute_visual_sql_async_empty_result_set(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    import asyncio

    rows, cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id, name FROM t WHERE amount > :min_amount",
        {"min_amount": "9999"},
        dialect=Dialect.SQLITE,
    ))
    assert rows == []
    assert cols == ["id", "name"]
