"""Unit tests for ``common/db.py`` (P.9d).

Pure-function tests on ``oracle_dsn`` + ``split_oracle_script`` —
covering both the CLI's ``demo apply`` consumer + the e2e harness's
``apply_db_seed`` consumer with the same regression bar.

``connect_demo_db`` and ``execute_script`` are integration-tested via
the e2e harness fixtures (gated behind ``QS_GEN_E2E=1`` and a real DB);
the import-error branches in ``connect_demo_db`` are covered here with
``monkeypatch``-based stubs.
"""

from __future__ import annotations


import pytest

from quicksight_gen.common.config import Config
from tests._test_helpers import make_test_config
from quicksight_gen.common.db import (
    AsyncConnectionPool,
    connect_demo_db,
    execute_script,
    make_connection_pool,
    oracle_dsn,
    split_oracle_script,
    sqlite_path,
)
from quicksight_gen.common.sql import Dialect


# -- oracle_dsn --------------------------------------------------------------


class TestOracleDsn:
    def test_passes_through_native_oracle_dsn(self) -> None:
        url = "user/pass@host:1521/SERVICE"
        assert oracle_dsn(url) == url

    def test_translates_oracle_url_with_service_name_query(self) -> None:
        url = "oracle+oracledb://admin:secret@db.example.com:1521/?service_name=ORCL"
        assert oracle_dsn(url) == "admin/secret@db.example.com:1521/ORCL"

    def test_translates_oracle_url_with_service_in_path(self) -> None:
        url = "oracle://admin:secret@db.example.com:1521/ORCL"
        assert oracle_dsn(url) == "admin/secret@db.example.com:1521/ORCL"

    def test_defaults_port_when_missing(self) -> None:
        url = "oracle://admin:secret@db.example.com/ORCL"
        assert oracle_dsn(url) == "admin/secret@db.example.com:1521/ORCL"

    def test_defaults_service_when_missing(self) -> None:
        url = "oracle://admin:secret@db.example.com:1521/"
        # Falls back to FREEPDB1 (Oracle Free's default PDB).
        assert oracle_dsn(url) == "admin/secret@db.example.com:1521/FREEPDB1"


# -- split_oracle_script -----------------------------------------------------


class TestSplitOracleScript:
    def test_splits_plain_statements_on_semicolon(self) -> None:
        sql = "CREATE TABLE foo (id NUMBER);\nCREATE TABLE bar (id NUMBER);"
        statements = split_oracle_script(sql)
        assert len(statements) == 2
        # Trailing semicolons are stripped on plain SQL (oracledb rejects them).
        assert all(not s.rstrip().endswith(";") for s in statements)
        assert "CREATE TABLE foo" in statements[0]
        assert "CREATE TABLE bar" in statements[1]

    def test_keeps_plsql_block_intact(self) -> None:
        sql = (
            "BEGIN EXECUTE IMMEDIATE 'DROP TABLE foo'; "
            "EXCEPTION WHEN OTHERS THEN NULL; END;\n"
            "CREATE TABLE foo (id NUMBER);"
        )
        statements = split_oracle_script(sql)
        assert len(statements) == 2
        # PL/SQL block must keep its END; terminator (Oracle parser
        # rejects without it).
        assert statements[0].rstrip().upper().endswith("END;")
        # Plain CREATE drops trailing semicolon.
        assert not statements[1].rstrip().endswith(";")

    def test_ignores_semicolon_inside_line_comment(self) -> None:
        sql = (
            "CREATE TABLE foo (id NUMBER);  -- trailing ; in comment\n"
            "CREATE TABLE bar (id NUMBER);"
        )
        statements = split_oracle_script(sql)
        # The ``-- trailing ; in comment`` doesn't introduce a new
        # statement boundary.
        assert len(statements) == 2

    def test_skips_comment_only_buffers(self) -> None:
        # An all-comment region between statements shouldn't produce a
        # phantom empty statement (Oracle would ORA-00900).
        sql = (
            "-- a leading comment\n"
            "CREATE TABLE foo (id NUMBER);\n"
            "-- another comment\n"
            "CREATE TABLE bar (id NUMBER);"
        )
        statements = split_oracle_script(sql)
        assert len(statements) == 2

    def test_handles_declare_block(self) -> None:
        sql = (
            "DECLARE x NUMBER; BEGIN x := 1; END;\n"
            "CREATE TABLE foo (id NUMBER);"
        )
        statements = split_oracle_script(sql)
        assert len(statements) == 2
        assert statements[0].upper().startswith("DECLARE")
        assert statements[0].rstrip().upper().endswith("END;")


# -- connect_demo_db ---------------------------------------------------------


def _cfg(*, dialect: Dialect, url: str | None) -> Config:
    return make_test_config(
        aws_region="us-east-2", dialect=dialect, demo_database_url=url,
    )


class TestConnectDemoDb:
    def test_raises_when_demo_database_url_unset(self) -> None:
        with pytest.raises(ValueError, match="demo_database_url is unset"):
            connect_demo_db(_cfg(dialect=Dialect.POSTGRES, url=None))

    def test_postgres_branch_invokes_psycopg(self, monkeypatch) -> None:
        # Stub psycopg so we don't need an actual DB. Verifies the
        # POSTGRES branch routes to ``psycopg.connect`` with the
        # raw URL (no DSN translation).
        import sys
        import types

        called: dict[str, str] = {}

        stub = types.ModuleType("psycopg")

        def fake_connect(url: str) -> str:
            called["url"] = url
            return "fake_pg_conn"

        stub.connect = fake_connect  # type: ignore[attr-defined]: monkey-patching the .connect attribute onto a fake module
        monkeypatch.setitem(sys.modules, "psycopg", stub)

        cfg = _cfg(
            dialect=Dialect.POSTGRES,
            url="postgresql://user:pw@host:5432/db",
        )
        conn = connect_demo_db(cfg)
        assert conn == "fake_pg_conn"
        assert called["url"] == "postgresql://user:pw@host:5432/db"

    def test_oracle_branch_invokes_oracledb_with_translated_dsn(
        self, monkeypatch,
    ) -> None:
        # Stub oracledb. Verifies the ORACLE branch routes through
        # ``oracle_dsn`` so SQLAlchemy-style URLs translate before
        # hitting ``oracledb.connect``.
        import sys
        import types

        called: dict[str, str] = {}

        stub = types.ModuleType("oracledb")

        def fake_connect(dsn: str) -> str:
            called["dsn"] = dsn
            return "fake_ora_conn"

        stub.connect = fake_connect  # type: ignore[attr-defined]: monkey-patching the .connect attribute onto a fake module
        monkeypatch.setitem(sys.modules, "oracledb", stub)

        cfg = _cfg(
            dialect=Dialect.ORACLE,
            url="oracle://admin:secret@db.example.com:1521/ORCL",
        )
        conn = connect_demo_db(cfg)
        assert conn == "fake_ora_conn"
        # The DSN was translated to oracledb's native shape.
        assert called["dsn"] == "admin/secret@db.example.com:1521/ORCL"

    def test_sqlite_branch_opens_inmemory(self) -> None:
        # X.3.a — SQLite uses stdlib sqlite3 with no extra. ``:memory:``
        # is the canonical in-memory DB string; SQLAlchemy-style URL
        # form parses to the same path via ``sqlite_path``.
        cfg = _cfg(dialect=Dialect.SQLITE, url="sqlite://:memory:")
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
        finally:
            conn.close()

    def test_sqlite_branch_opens_file(self, tmp_path) -> None:
        # SQLAlchemy-style ``sqlite:///path`` translates to the file
        # path correctly. Round-trip a CREATE/INSERT/SELECT to confirm
        # the connection is a real DB-API 2.0 sqlite3.Connection.
        db_file = tmp_path / "demo.sqlite"
        cfg = _cfg(dialect=Dialect.SQLITE, url=f"sqlite:///{db_file}")
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            cur.execute("CREATE TABLE t (a INTEGER)")
            cur.execute("INSERT INTO t VALUES (42)")
            cur.execute("SELECT a FROM t")
            assert cur.fetchone() == (42,)
        finally:
            conn.close()
        assert db_file.exists()


# -- sqlite_path -------------------------------------------------------------


class TestSqlitePath:
    """X.3.a — URL-to-path translation for the sqlite3 connection."""

    def test_inmemory_url_form(self) -> None:
        assert sqlite_path("sqlite://:memory:") == ":memory:"

    def test_inmemory_bare(self) -> None:
        # Bare ``:memory:`` (no scheme) passes through unchanged for
        # ergonomics — the integrator can paste either form.
        assert sqlite_path(":memory:") == ":memory:"

    def test_triple_slash_absolute_path(self) -> None:
        # SQLAlchemy convention: three slashes for relative, four for
        # absolute. The path component is everything after the third
        # slash.
        assert sqlite_path("sqlite:///tmp/demo.sqlite") == "tmp/demo.sqlite"

    def test_triple_slash_keeps_leading_slash_on_quad(self) -> None:
        # ``sqlite:////tmp/demo.sqlite`` (four slashes) → absolute.
        assert sqlite_path("sqlite:////tmp/demo.sqlite") == "/tmp/demo.sqlite"

    def test_bare_path_passes_through(self) -> None:
        assert sqlite_path("/tmp/demo.sqlite") == "/tmp/demo.sqlite"
        assert sqlite_path("./relative.sqlite") == "./relative.sqlite"


# -- execute_script SQLite branch -------------------------------------------


class TestExecuteScriptSqlite:
    """X.3.a — multi-statement script execution against SQLite."""

    def test_executes_multi_statement_script(self) -> None:
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.cursor()
            sql = (
                "CREATE TABLE t (a INTEGER);\n"
                "INSERT INTO t VALUES (1);\n"
                "INSERT INTO t VALUES (2);\n"
                "INSERT INTO t VALUES (3);"
            )
            execute_script(cur, sql, dialect=Dialect.SQLITE)
            cur.execute("SELECT COUNT(*) FROM t")
            assert cur.fetchone()[0] == 3
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# X.2.n.2 — AsyncConnectionPool (SQLite path; PG/Oracle covered via live e2e)
# ---------------------------------------------------------------------------


class TestMakeConnectionPool:
    """Async connection pool — SQLite branch is the cheap test target.

    PG and Oracle branches require live drivers + reachable DBs and are
    covered by the e2e harness (X.2.n.7). SQLite uses aiosqlite +
    in-memory ``:memory:``, so the round-trip happens in-process with
    no setup.
    """

    def test_make_pool_sqlite_acquire_yields_aiosqlite_connection(self) -> None:
        import asyncio

        cfg = make_test_config(
            aws_region="us-east-2",
            dialect=Dialect.SQLITE,
            demo_database_url=":memory:",
        )

        async def run() -> tuple[type, int]:
            pool = await make_connection_pool(cfg, max_size=5)
            try:
                async with pool.acquire() as conn:
                    cur = await conn.execute("SELECT 1 AS n")
                    row = await cur.fetchone()
                    return (type(row), int(row[0]))
            finally:
                await pool.close()

        kind, value = asyncio.run(run())
        assert value == 1
        # aiosqlite returns a Row-like tuple; just confirm we got data
        # back via the async path (not None).
        assert kind is not type(None)

    def test_make_pool_raises_when_url_unset(self) -> None:
        import asyncio

        cfg = make_test_config(
            aws_region="us-east-2",
            dialect=Dialect.SQLITE,
            demo_database_url=None,
        )
        with pytest.raises(ValueError, match="demo_database_url is unset"):
            asyncio.run(make_connection_pool(cfg))

    def test_make_pool_raises_on_unknown_dialect(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        # Construct a Config with a nonsense dialect — Config dataclass
        # validates via Literal so we use MagicMock instead of fighting
        # the type system.
        cfg = MagicMock()
        cfg.demo_database_url = ":memory:"
        cfg.dialect = "snowflake"  # not in the Dialect enum
        with pytest.raises(ValueError, match="Unknown dialect"):
            asyncio.run(make_connection_pool(cfg))

    def test_pool_protocol_is_runtime_satisfied_by_sqlite_impl(self) -> None:
        # AsyncConnectionPool is a runtime-checkable Protocol (Protocol
        # in typing module is structural — instances satisfy it if they
        # have the right methods, regardless of inheritance). This test
        # protects future refactors that might accidentally drop the
        # ``acquire`` or ``close`` method from the SQLite impl.
        import asyncio

        cfg = make_test_config(
            aws_region="us-east-2",
            dialect=Dialect.SQLITE,
            demo_database_url=":memory:",
        )
        pool = asyncio.run(make_connection_pool(cfg))
        try:
            assert hasattr(pool, "acquire")
            assert hasattr(pool, "close")
            # Don't assert isinstance(pool, AsyncConnectionPool) directly
            # — Protocol isinstance checks need @runtime_checkable, and
            # we don't need the runtime cost. The duck-type check above
            # is enough to catch a missing method regression.
        finally:
            asyncio.run(pool.close())
