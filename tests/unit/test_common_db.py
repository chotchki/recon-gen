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


from typing import Any

import pytest

from recon_gen.common.config import Config
from tests._test_helpers import make_test_config
from recon_gen.common.db import (
    AsyncConnectionPool as AsyncConnectionPool,  # re-exported for protocol smoke
    connect_demo_db,
    make_connection_pool,
    oracle_dsn,
    split_oracle_script,
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
        aws_region="us-east-2", db_dialect=dialect, db_url=url,
    )


class TestConnectDemoDb:
    def test_raises_when_demo_database_url_unset(self) -> None:
        with pytest.raises(ValueError, match="cfg.db.url is unset"):
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

        called: dict[str, object] = {}

        stub = types.ModuleType("oracledb")

        # CB.14 — connect_demo_db now pins session NLS via
        # `conn.cursor().execute("ALTER SESSION SET NLS_DATE_FORMAT = ...")`
        # after oracledb.connect(). The fake conn needs a cursor() that
        # returns something with execute() + close(); we also record the
        # NLS statements so the test can assert the pin happened.
        nls_statements: list[str] = []

        class _FakeOraCursor:
            def execute(self, stmt: str) -> None:
                nls_statements.append(stmt)
            def close(self) -> None:
                pass

        class _FakeOraConn:
            def cursor(self) -> "_FakeOraCursor":
                return _FakeOraCursor()

        fake_conn = _FakeOraConn()

        def fake_connect(dsn: str) -> "_FakeOraConn":
            called["dsn"] = dsn
            return fake_conn

        stub.connect = fake_connect  # type: ignore[attr-defined]: monkey-patching the .connect attribute onto a fake module
        monkeypatch.setitem(sys.modules, "oracledb", stub)

        cfg = _cfg(
            dialect=Dialect.ORACLE,
            url="oracle://admin:secret@db.example.com:1521/ORCL",
        )
        conn = connect_demo_db(cfg)
        assert conn is fake_conn
        # The DSN was translated to oracledb's native shape.
        assert called["dsn"] == "admin/secret@db.example.com:1521/ORCL"
        # NLS pinned to ISO so spine-emitted date strings parse.
        assert any("NLS_DATE_FORMAT" in s and "YYYY-MM-DD" in s for s in nls_statements)
        assert any("NLS_TIMESTAMP_FORMAT" in s for s in nls_statements)

# -- execute_script SQLite branch (CB.8: deleted) ----------------------------
# `TestSqlitePath` + `TestExecuteScriptSqlite` removed in CB.8 along with the
# `Dialect.SQLITE` enum value. The DuckDB equivalent runs through
# `connect_demo_db` (covered by other tests in this file) — no separate
# duckdb-script test class needed; the execute_script path is dialect-
# agnostic per-statement once SQLite's special-case `executescript` arm
# went away.



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
            db_dialect=Dialect.DUCKDB,
            db_url=":memory:",
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
            db_dialect=Dialect.DUCKDB,
            db_url=None,
        )
        with pytest.raises(ValueError, match="cfg.db.url is unset"):
            asyncio.run(make_connection_pool(cfg))

    def test_make_pool_raises_on_unknown_dialect(self) -> None:
        import asyncio
        from unittest.mock import MagicMock

        # Construct a Config with a nonsense dialect — Config dataclass
        # validates via Literal so we use MagicMock instead of fighting
        # the type system.
        cfg = MagicMock()
        cfg.db.url = ":memory:"
        cfg.db.dialect = "snowflake"  # not in the Dialect enum
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
            db_dialect=Dialect.DUCKDB,
            db_url=":memory:",
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


# -- _apply_seed_via_duckdb_pyarrow regressions (CA.11) ---------------------

class TestApplySeedViaDuckdbPyarrow:
    """Regression cases against the CA.11 fast path.

    Two correctness bugs surfaced post-initial-CA.11 land — both worth
    a permanent regression:

    1. **Ordering bug**: residual DDL that appeared BEFORE an INSERT in
       the script was queued for end-of-function execution; the INSERT
       then flushed against a missing table. Fix: process residual
       between each INSERT match boundary, before the next batch.

    2. **Quote-aware scanner**: the naive `[^;]+` body capture
       truncated when the seed text embedded ``;`` inside a string
       literal (operator-authored config descriptions). Fix: body
       alternation matches single-quoted strings OR non-`;` chars.

    Tests intentionally hit the function directly via the in-process
    `duckdb.connect(":memory:")` to keep the regression cheap
    (~milliseconds) and dialect-isolated.
    """

    def test_ddl_before_insert_runs_in_order(self) -> None:
        import duckdb

        from recon_gen.common.db import _apply_seed_via_duckdb_pyarrow

        con = duckdb.connect(":memory:")
        try:
            _apply_seed_via_duckdb_pyarrow(
                con,
                "CREATE TABLE foo (id INTEGER, name VARCHAR);\n"
                "INSERT INTO foo (id, name) VALUES (1, 'a');\n"
                "INSERT INTO foo (id, name) VALUES (2, 'b');\n",
            )
            assert con.execute(
                "SELECT id, name FROM foo ORDER BY id"
            ).fetchall() == [(1, "a"), (2, "b")]
        finally:
            con.close()

    def test_ddl_between_two_insert_groups_runs_in_order(self) -> None:
        """CREATE TABLE bar between an INSERT-into-foo and INSERT-into-bar
        must run BEFORE the bar INSERT lands (group boundary triggers
        both flush + residual execution)."""
        import duckdb

        from recon_gen.common.db import _apply_seed_via_duckdb_pyarrow

        con = duckdb.connect(":memory:")
        try:
            _apply_seed_via_duckdb_pyarrow(
                con,
                "CREATE TABLE foo (id INTEGER);\n"
                "INSERT INTO foo (id) VALUES (1);\n"
                "CREATE TABLE bar (id INTEGER);\n"
                "INSERT INTO bar (id) VALUES (10);\n"
                "INSERT INTO foo (id) VALUES (2);\n",
            )
            assert con.execute("SELECT id FROM foo ORDER BY id").fetchall() == [(1,), (2,)]
            assert con.execute("SELECT id FROM bar").fetchall() == [(10,)]
        finally:
            con.close()

    def test_semicolon_inside_string_literal(self) -> None:
        """Quote-aware body scanner: a `;` inside a VALUES string literal
        must NOT be treated as a statement terminator. Repros the second
        config-populate failure shape (operator-authored description text
        containing punctuation)."""
        import duckdb

        from recon_gen.common.db import _apply_seed_via_duckdb_pyarrow

        con = duckdb.connect(":memory:")
        try:
            _apply_seed_via_duckdb_pyarrow(
                con,
                "CREATE TABLE foo (id INTEGER, descr VARCHAR);\n"
                "INSERT INTO foo (id, descr) VALUES (1, "
                "'Inbound ACH credit; force-posts via the rail');\n"
                "INSERT INTO foo (id, descr) VALUES (2, 'plain text');\n",
            )
            rows = con.execute(
                "SELECT id, descr FROM foo ORDER BY id"
            ).fetchall()
            assert rows == [
                (1, "Inbound ACH credit; force-posts via the rail"),
                (2, "plain text"),
            ]
        finally:
            con.close()

    def test_comment_only_residual_does_not_choke_duckdb(self) -> None:
        """The seed's `-- SHA256: ...` header lands as residual between
        the script start and the first INSERT. Comment-only residual
        must be stripped (DuckDB chokes on a bare `;` left over from
        the comment-line strip)."""
        import duckdb

        from recon_gen.common.db import _apply_seed_via_duckdb_pyarrow

        con = duckdb.connect(":memory:")
        try:
            _apply_seed_via_duckdb_pyarrow(
                con,
                "-- SHA256: fake-hash-header\n"
                "-- another comment line\n"
                "CREATE TABLE foo (id INTEGER);\n"
                "-- mid-script comment\n"
                "INSERT INTO foo (id) VALUES (1);\n"
                "-- trailing comment\n",
            )
            assert con.execute(
                "SELECT id FROM foo"
            ).fetchall() == [(1,)]
        finally:
            con.close()
