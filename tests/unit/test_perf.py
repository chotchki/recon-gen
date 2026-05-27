"""Unit tests for ``recon_gen._dev.perf`` (Y.2.gate.c.10).

Helpers were lifted from ``scripts/dump_top_queries.py`` (W.8a) so
both the standalone script and the new in-process e2e conftest
fixture share the same code path. These tests cover the pure
functions; the conftest fixture itself is exercised end-to-end by
the e2e layer (no DB → fixture writes "skipped" marker; with DB →
fixture writes the table).
"""

from __future__ import annotations

from recon_gen._dev.perf import (
    dialect_name,
    fetch_top_queries,
    format_skipped,
    format_top_queries_markdown,
)
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.sql import Dialect


# -- format_top_queries_markdown -------------------------------------------


def test_format_markdown_with_rows() -> None:
    md = format_top_queries_markdown(
        title="Top expensive queries (postgres)",
        dialect="postgres",
        like_pattern=DEFAULT_PREFIX,
        rows=[
            (10, 1234.5, 123.45, 5000, "SELECT * FROM spec_example_x"),
            (3, 99.0, 33.0, 12, "INSERT INTO spec_example_y VALUES (1)"),
        ],
    )
    assert md.startswith("# Top expensive queries (postgres)\n")
    assert "**Dialect:** postgres" in md
    assert "**Filter (LIKE):** `%spec_example%`" in md
    assert "**Rows returned:** 2" in md
    # Header + 2 data rows in the table
    assert "| Calls | Total (ms) | Mean (ms) | Rows | Query |" in md
    assert "| 10 | 1234.5 | 123.45 | 5000 |" in md
    assert "spec_example_x" in md


def test_format_markdown_with_empty_rows() -> None:
    md = format_top_queries_markdown(
        title="Top expensive queries (postgres)",
        dialect="postgres",
        like_pattern=DEFAULT_PREFIX,
        rows=[],
    )
    assert "**Rows returned:** 0" in md
    assert "_No matching rows._" in md
    # No table when no data.
    assert "| Calls |" not in md


def test_format_markdown_escapes_pipes_in_query_text() -> None:
    """Pipes inside the query column would break the markdown table
    rendering — they must be backslash-escaped."""
    md = format_top_queries_markdown(
        title="t", dialect="postgres", like_pattern="x",
        rows=[(1, 0.1, 0.1, 0, "SELECT a | b FROM t")],
    )
    assert "a \\| b" in md
    # The literal raw pipe inside backticks would still render OK in
    # most renderers but escaping makes the table well-formed.


def test_format_markdown_with_note() -> None:
    md = format_top_queries_markdown(
        title="t", dialect="oracle", like_pattern="x", rows=[],
        note="captured at session teardown",
    )
    assert "**Note:** captured at session teardown" in md


# -- format_skipped --------------------------------------------------------


def test_format_skipped_shape() -> None:
    md = format_skipped(
        title="Top expensive queries (sqlite)",
        dialect="sqlite",
        reason="SQLite has no equivalent of pg_stat_statements.",
    )
    assert md.startswith("# Top expensive queries (sqlite)\n")
    assert "**Status:** _skipped_" in md
    assert "**Reason:** SQLite has no equivalent" in md


# -- dialect_name ----------------------------------------------------------


def test_dialect_name_per_dialect() -> None:
    assert dialect_name(Dialect.POSTGRES) == "postgres"
    assert dialect_name(Dialect.ORACLE) == "oracle"
    assert dialect_name(Dialect.SQLITE) == "sqlite"


# -- fetch_top_queries -----------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(
        self, sql: str, params: tuple[object, ...] | None = None,
    ) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_fetch_top_queries_postgres_uses_pg_sql_and_substring_pattern() -> None:
    rows: list[tuple[object, ...]] = [(5, 100.0, 20.0, 50, "SELECT * FROM spec_example_t")]
    cur = _FakeCursor(rows)
    conn = _FakeConn(cur)

    out = fetch_top_queries(
        conn, Dialect.POSTGRES, like_pattern=DEFAULT_PREFIX, top=50,
    )

    assert out == rows
    # Two execute calls: CREATE EXTENSION + the SELECT.
    assert len(cur.executed) == 2
    assert "CREATE EXTENSION" in cur.executed[0][0]
    assert "pg_stat_statements" in cur.executed[1][0]
    assert cur.executed[1][1] == ("%spec_example%", 50)


def test_fetch_top_queries_oracle_uses_v_sqlstats_and_bind_params() -> None:
    rows: list[tuple[object, ...]] = [(3, 250.0, 83.3, 5, "SELECT * FROM spec_example_t")]
    cur = _FakeCursor(rows)
    conn = _FakeConn(cur)

    out = fetch_top_queries(
        conn, Dialect.ORACLE, like_pattern=DEFAULT_PREFIX, top=10,
    )

    assert out == rows
    assert len(cur.executed) == 1
    assert "v$sqlstats" in cur.executed[0][0]
    assert cur.executed[0][1] == ("%spec_example%", 10)


def test_fetch_top_queries_sqlite_raises_not_implemented() -> None:
    """SQLite has no stats view — the fetch function explicitly raises
    so the caller falls into its skipped-marker path. (The conftest
    fixture short-circuits before calling fetch for SQLite, but the
    function itself must not silently return empty.)"""
    import pytest

    cur = _FakeCursor([])
    conn = _FakeConn(cur)
    with pytest.raises(NotImplementedError, match="sqlite"):
        fetch_top_queries(
            conn, Dialect.SQLITE, like_pattern="x", top=1,
        )


def test_fetch_top_queries_oracle_reads_clob_on_last_column() -> None:
    """Oracle's v$sqlstats.sql_fulltext is a CLOB; oracledb returns
    LOB objects with a ``.read()`` method instead of plain strings.
    The fetch function unwraps the LOB so downstream formatters see
    plain str."""
    class _LOB:
        def __init__(self, value: str) -> None:
            self._value = value

        def read(self) -> str:
            return self._value

    rows: list[tuple[object, ...]] = [(1, 10.0, 10.0, 1, _LOB("SELECT 1 FROM dual"))]
    cur = _FakeCursor(rows)
    conn = _FakeConn(cur)

    out = fetch_top_queries(
        conn, Dialect.ORACLE, like_pattern="x", top=1,
    )

    # Last column is now a plain str, not a LOB.
    assert out[0][-1] == "SELECT 1 FROM dual"
