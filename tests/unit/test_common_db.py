"""Unit tests for ``common/db.py`` (P.9d).

Pure-function tests on ``oracle_dsn`` + ``split_oracle_script`` —
covering both the CLI's ``demo apply`` consumer + the e2e harness's
``apply_db_seed`` consumer with the same regression bar.

``connect_demo_db`` and ``execute_script`` are integration-tested via
the e2e harness fixtures (gated behind ``RECON_GEN_E2E=1`` and a real DB);
the import-error branches in ``connect_demo_db`` are covered here with
``monkeypatch``-based stubs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from recon_gen.common.config import Config
from tests._test_helpers import make_test_config
from recon_gen.common.db import (
    AsyncConnectionPool as AsyncConnectionPool,  # re-exported for protocol smoke
    connect_demo_db,
    execute_script,
    make_connection_pool,
    oracle_dsn,
    split_oracle_script,
    sqlite_path,
)
from recon_gen.common.sql import Dialect


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

    def test_postgres_branch_invokes_psycopg(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        self, monkeypatch: pytest.MonkeyPatch,
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

    def test_sqlite_branch_opens_file(self, tmp_path: Path) -> None:
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

    def test_insert_with_columns_takes_bind_fast_path(self) -> None:
        """X.4.j.sqlite-binds: INSERT INTO foo (cols) VALUES (...) form
        gets coalesced into executemany. End-state must be identical to
        plain executescript."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.cursor()
            sql = (
                "CREATE TABLE t (id TEXT, n INTEGER, x REAL, j TEXT);\n"
                "INSERT INTO t (id, n, x, j) VALUES "
                "('a', 1, 1.5, '{\"k\": \"v\"}');\n"
                "INSERT INTO t (id, n, x, j) VALUES ('b', NULL, -2.0, NULL);\n"
                "INSERT INTO t (id, n, x, j) VALUES ('c', 999, 0.0, 'plain');"
            )
            execute_script(cur, sql, dialect=Dialect.SQLITE)
            cur.execute("SELECT id, n, x, j FROM t ORDER BY id")
            rows = cur.fetchall()
            assert rows == [
                ("a", 1, 1.5, '{"k": "v"}'),
                ("b", None, -2.0, None),
                ("c", 999, 0.0, "plain"),
            ]
        finally:
            conn.close()

    def test_grouping_change_flushes_buffer(self) -> None:
        """Mixed-table INSERTs flush the buffer on transition. Order
        within each table is preserved."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.cursor()
            sql = (
                "CREATE TABLE a (v INTEGER);\n"
                "CREATE TABLE b (v INTEGER);\n"
                "INSERT INTO a (v) VALUES (1);\n"
                "INSERT INTO a (v) VALUES (2);\n"
                "INSERT INTO b (v) VALUES (10);\n"
                "INSERT INTO a (v) VALUES (3);\n"  # transitions back
            )
            execute_script(cur, sql, dialect=Dialect.SQLITE)
            cur.execute("SELECT v FROM a ORDER BY v")
            assert [r[0] for r in cur.fetchall()] == [1, 2, 3]
            cur.execute("SELECT v FROM b ORDER BY v")
            assert [r[0] for r in cur.fetchall()] == [10]
        finally:
            conn.close()

    def test_non_insert_statements_pass_through(self) -> None:
        """DDL + DELETE between INSERTs run via per-statement cur.execute
        — buffer flushes before each non-conforming statement runs."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.cursor()
            sql = (
                "CREATE TABLE t (v INTEGER);\n"
                "INSERT INTO t (v) VALUES (1);\n"
                "INSERT INTO t (v) VALUES (2);\n"
                "DELETE FROM t WHERE v = 1;\n"
                "INSERT INTO t (v) VALUES (3);"
            )
            execute_script(cur, sql, dialect=Dialect.SQLITE)
            cur.execute("SELECT v FROM t ORDER BY v")
            assert [r[0] for r in cur.fetchall()] == [2, 3]
        finally:
            conn.close()

    def test_comments_dropped_not_executed(self) -> None:
        """Header comment lines (-- SHA256: ...) the seed emit prepends
        must not surface as bogus statements."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.cursor()
            sql = (
                "-- SHA256: deadbeef\n"
                "-- =====================================\n"
                "-- comment-only block before the inserts\n"
                "-- =====================================\n"
                "\n"
                "CREATE TABLE t (v INTEGER);\n"
                "INSERT INTO t (v) VALUES (1);\n"
                "INSERT INTO t (v) VALUES (2);"
            )
            execute_script(cur, sql, dialect=Dialect.SQLITE)
            cur.execute("SELECT COUNT(*) FROM t")
            assert cur.fetchone()[0] == 2
        finally:
            conn.close()

    def test_caller_owns_commit(self) -> None:
        """Helper does NOT commit on its own — caller controls
        transaction boundary. Verifies a rollback after the call wipes
        all the inserted rows."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.cursor()
            cur.execute("CREATE TABLE t (v INTEGER)")
            conn.commit()
            sql = (
                "INSERT INTO t (v) VALUES (1);\n"
                "INSERT INTO t (v) VALUES (2);\n"
                "INSERT INTO t (v) VALUES (3);"
            )
            execute_script(cur, sql, dialect=Dialect.SQLITE)
            # Pre-rollback rows visible to this connection (autocommit-ish view).
            cur.execute("SELECT COUNT(*) FROM t")
            assert cur.fetchone()[0] == 3
            conn.rollback()
            cur.execute("SELECT COUNT(*) FROM t")
            assert cur.fetchone()[0] == 0
        finally:
            conn.close()


# -- Oracle DDL lock-timeout retry ------------------------------------------


class _FakeOracleLockError(Exception):
    """Stand-in for oracledb.DatabaseError carrying an ORA-NNNNN code."""


class _RaiseThenSucceedCursor:
    """Mock cursor whose ``execute`` raises ``exc`` the first ``n`` calls
    then succeeds. Records the call count for assertions."""

    def __init__(self, *, fail_times: int, exc: Exception) -> None:
        self._fail_times = fail_times
        self._exc = exc
        self.calls = 0

    def execute(self, _stmt: str) -> None:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc


class TestExecuteOracleStmtLockRetry:
    """Y.2.gate-l follow-up — DDL retry on ORA-00054 / ORA-04021.

    Surfaced by the full-matrix run: sibling Oracle cells running
    ``schema apply`` against the same multi-tenant instance deadlock on
    the data-dictionary lock. The retry-with-backoff makes the
    transient case self-heal. ``time.sleep`` is patched out so the
    tests don't actually wait the 2s/4s/8s backoff.
    """

    def test_retries_then_succeeds_on_ora_04021(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from recon_gen.common import db as db_mod

        def _noop_sleep(_s: float) -> None:
            return None

        monkeypatch.setattr(db_mod.time, "sleep", _noop_sleep)
        cur = _RaiseThenSucceedCursor(
            fail_times=2,
            exc=_FakeOracleLockError(
                "ORA-04021: timeout occurred while waiting to lock object"
            ),
        )
        # Should NOT raise — third attempt succeeds.
        db_mod._execute_oracle_stmt_with_lock_retry(cur, "DROP TABLE foo")
        assert cur.calls == 3  # 1 initial + 2 retries

    def test_retries_then_succeeds_on_ora_00054(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from recon_gen.common import db as db_mod

        def _noop_sleep(_s: float) -> None:
            return None

        monkeypatch.setattr(db_mod.time, "sleep", _noop_sleep)
        cur = _RaiseThenSucceedCursor(
            fail_times=1,
            exc=_FakeOracleLockError(
                "ORA-00054: resource busy and acquire with NOWAIT specified"
            ),
        )
        db_mod._execute_oracle_stmt_with_lock_retry(cur, "ALTER TABLE foo ADD x INT")
        assert cur.calls == 2

    def test_exhausts_retries_then_reraises_lock_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from recon_gen.common import db as db_mod

        def _noop_sleep(_s: float) -> None:
            return None

        monkeypatch.setattr(db_mod.time, "sleep", _noop_sleep)
        cur = _RaiseThenSucceedCursor(
            fail_times=99,  # never recovers
            exc=_FakeOracleLockError("ORA-04021: timeout"),
        )
        with pytest.raises(_FakeOracleLockError, match="ORA-04021"):
            db_mod._execute_oracle_stmt_with_lock_retry(cur, "DROP TABLE foo")
        # 1 initial + one retry per backoff entry. Derived from the
        # tuple so it stays correct if the backoff schedule changes.
        assert cur.calls == len(db_mod._ORACLE_LOCK_RETRY_BACKOFF_S) + 1

    def test_non_lock_error_propagates_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from recon_gen.common import db as db_mod

        sleep_calls: list[float] = []

        def _record_sleep(s: float) -> None:
            sleep_calls.append(s)

        monkeypatch.setattr(db_mod.time, "sleep", _record_sleep)
        cur = _RaiseThenSucceedCursor(
            fail_times=99,
            exc=_FakeOracleLockError("ORA-00942: table or view does not exist"),
        )
        with pytest.raises(_FakeOracleLockError, match="ORA-00942"):
            db_mod._execute_oracle_stmt_with_lock_retry(cur, "DROP TABLE bogus")
        assert cur.calls == 1  # no retry
        assert sleep_calls == []  # never slept


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
                    # ``fetchone`` isn't on the AsyncCursor Protocol (only
                    # ``fetchall``); aiosqlite supports it at runtime, but
                    # pyright can't see through. Use ``fetchall`` to stay
                    # protocol-faithful.
                    rows: list[Any] = await cur.fetchall()
                    row: Any = rows[0]
                    # ``type(row)`` infers as ``type[Unknown]`` since row is
                    # Any; pyright-noise without value. We don't actually use
                    # the type at runtime beyond a not-None check.
                    return (type(row), int(row[0]))  # pyright: ignore[reportUnknownVariableType]: row is Any (aiosqlite Row), type() inference is partial
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
