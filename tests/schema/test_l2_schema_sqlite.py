"""End-to-end SQLite DDL emission tests for ``common.l2.schema`` (X.3.b).

Mirrors ``test_l2_schema_oracle.py`` for the SQLite dialect arm. Pins:

- The emitted DDL parses + executes against an in-memory sqlite3
  connection (the strongest possible "does this work?" assertion at
  unit-test scope; SQLite ships with stdlib so no driver install needed).
- No PG / Oracle constructs leak into the SQLite output.
- Every SQLite-specific replacement appears in the right shape
  (matviews-as-tables, json_valid() instead of IS JSON, INTEGER
  PRIMARY KEY AUTOINCREMENT for entry, julianday() in window frames).
- Refresh script also executes cleanly post-create.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from recon_gen.common.db import _register_sqlite_aggregates
from decimal import Decimal

from recon_gen.common.l2 import (
    Identifier,
    L2Instance,
    Money,
    RailName,
    emit_schema,
    refresh_matviews_sql,
)
from recon_gen.common.l2.primitives import (
    LimitSchedule,
    SingleLegRail,
)
from recon_gen.common.sql import Dialect


def _strip_comments(sql: str) -> str:
    """Drop ``--`` line comments so negative-pattern checks don't trip
    on explanatory prose in the generated DDL."""
    return "\n".join(
        line for line in sql.split("\n")
        if not line.lstrip().startswith("--")
    )


def _full_instance(prefix: str) -> L2Instance:
    """An L2 instance with a rail + a limit schedule so every L1
    invariant view's CASE branches get populated."""
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(
            SingleLegRail(
                name=Identifier("SettlementRail"),
                description="Settlement rail with aging",
                metadata_keys=(),
                leg_role=(Identifier("gl_control"),),
                leg_direction="Debit",
                posted_requirements=(),
                max_pending_age=timedelta(days=1),
                max_unbundled_age=timedelta(days=2),
            ),
        ),
        transfer_templates=(),
        chains=(),
        limit_schedules=(
            LimitSchedule(
                description="cap on settle from gl_control",
                parent_role=Identifier("gl_control"),
                rail=RailName("SettlementRail"),
                cap=Money(Decimal(10000)),
            ),
        ),
    )


@pytest.fixture(scope="module")
def sqlite_sql() -> str:
    """One SQLite DDL emission per module — every assertion runs
    against the same string."""
    return emit_schema(_full_instance("sqlt"), prefix="sqlt", dialect=Dialect.SQLITE)


@pytest.fixture(scope="module")
def sqlite_sql_nocomments(sqlite_sql: str) -> str:
    return _strip_comments(sqlite_sql)


# -- Negative patterns: PG / Oracle constructs MUST be gone -----------------


class TestSqliteNoPostgresOrOracleIsms:
    @pytest.mark.parametrize("pattern", [
        # PG-only constructs
        "BIGSERIAL",
        "::date",
        "::TEXT",
        "::TIMESTAMP",
        "::NUMERIC",
        "::numeric",
        "::bigint",
        "DATE_TRUNC('day',",
        "EXTRACT(EPOCH FROM",
        "WHERE bundle_id IS NULL",  # PG partial-index optimization
        # Oracle-only constructs
        "NUMBER GENERATED ALWAYS AS IDENTITY",
        "VARCHAR2(",
        "CLOB",
        "TRUNC(posting)",
        "TRUNC(recipient.posting)",
        "BUILD IMMEDIATE REFRESH COMPLETE ON DEMAND",
        "EXTRACT(DAY FROM",
        "EXTRACT(HOUR FROM",
        "INTERVAL '1' DAY",
        "INTERVAL '1 day'",
        "BEGIN EXECUTE IMMEDIATE",
        "DBMS_MVIEW.REFRESH",
        # SQL/JSON IS JSON predicate (PG / Oracle have it; SQLite uses
        # json_valid() instead).
        "IS JSON",
        # Materialized views — SQLite doesn't support them, all matviews
        # land as plain tables.
        "CREATE MATERIALIZED VIEW",
        "DROP MATERIALIZED VIEW",
    ])
    def test_no_other_dialect_pattern_present(
        self, sqlite_sql_nocomments: str, pattern: str,
    ) -> None:
        assert pattern not in sqlite_sql_nocomments, (
            f"Non-SQLite construct leaked into SQLite DDL: {pattern!r}"
        )


# -- Positive patterns: SQLite-specific shapes MUST be present --------------


class TestSqliteConstructsPresent:
    @pytest.mark.parametrize("pattern", [
        # Type names — SQLite is typeless internally so VARCHAR/TEXT/CLOB
        # all map to TEXT, DECIMAL maps to NUMERIC.
        "TEXT",
        "NUMERIC",
        # Auto-increment for the entry column — SQLite single-column
        # INTEGER PRIMARY KEY AUTOINCREMENT.
        "entry                INTEGER PRIMARY KEY AUTOINCREMENT",
        "entry                  INTEGER PRIMARY KEY AUTOINCREMENT",
        # Composite (id, entry) PG/Oracle PK becomes UNIQUE on SQLite
        # (entry already PRIMARY KEY).
        "UNIQUE (id, entry)",
        "UNIQUE (account_id, business_day_start, entry)",
        # JSON validity — SQLite json_valid() vs PG/Oracle IS JSON.
        # AV (2026-05-23): daily_balances.limits → daily_balances.metadata,
        # so both base tables now carry the same constraint shape; only
        # one literal asserts.
        "json_valid(metadata)",
        # Matviews are plain tables.
        "CREATE TABLE sqlt_current_transactions",
        "CREATE TABLE sqlt_drift",
        "CREATE TABLE sqlt_inv_pair_rolling_anomalies",
        # Date arithmetic — SQLite functions.
        "DATE(posting)",
        "datetime(tx.posting, 'start of day')",
        # Window function uses Julian-day form for the RANGE frame.
        "julianday(posted_day)",
        "RANGE BETWEEN 1 PRECEDING",
        # Recursive CTE — SQLite requires WITH RECURSIVE (same as PG).
        "WITH RECURSIVE",
        # Native idempotency — DROP TABLE/INDEX IF EXISTS
        "DROP TABLE IF EXISTS sqlt_transactions",
        "DROP INDEX IF EXISTS idx_sqlt_",
    ])
    def test_sqlite_pattern_present(
        self, sqlite_sql: str, pattern: str,
    ) -> None:
        assert pattern in sqlite_sql, (
            f"Expected SQLite construct missing: {pattern!r}"
        )


# -- Live execution against in-memory sqlite3 -------------------------------


class TestSqliteSchemaActuallyRuns:
    """The strongest possible test: run the emit against a real SQLite
    connection and verify the create succeeds + the expected tables /
    matview-tables exist."""

    def test_executes_cleanly(self, sqlite_sql: str) -> None:
        conn = sqlite3.connect(":memory:")
        _register_sqlite_aggregates(conn)
        try:
            conn.executescript(sqlite_sql)
        finally:
            conn.close()

    def test_creates_expected_tables(self, sqlite_sql: str) -> None:
        conn = sqlite3.connect(":memory:")
        _register_sqlite_aggregates(conn)
        try:
            conn.executescript(sqlite_sql)
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
            names = [n for (n,) in cur.fetchall()]
        finally:
            conn.close()
        # Base tables + every matview-as-table the schema declares.
        expected = {
            "sqlt_transactions",
            "sqlt_daily_balances",
            "sqlt_current_transactions",
            "sqlt_current_daily_balances",
            "sqlt_computed_subledger_balance",
            "sqlt_computed_ledger_balance",
            "sqlt_drift",
            "sqlt_ledger_drift",
            "sqlt_overdraft",
            "sqlt_expected_eod_balance_breach",
            "sqlt_limit_breach",
            "sqlt_stuck_pending",
            "sqlt_stuck_unbundled",
            "sqlt_daily_statement_summary",
            "sqlt_todays_exceptions",
            "sqlt_inv_pair_rolling_anomalies",
            "sqlt_inv_money_trail_edges",
        }
        actual = set(names)
        missing = expected - actual
        assert not missing, f"Missing tables/matviews: {missing}"

    def test_refresh_executes_cleanly(self, sqlite_sql: str) -> None:
        """X.3.c contract — refresh re-creates every matview-as-table."""
        conn = sqlite3.connect(":memory:")
        _register_sqlite_aggregates(conn)
        try:
            conn.executescript(sqlite_sql)
            refresh_sql = refresh_matviews_sql(_full_instance("sqlt"), prefix="sqlt", dialect=Dialect.SQLITE,
            )
            conn.executescript(refresh_sql)
        finally:
            conn.close()

    def test_inserts_round_trip(self, sqlite_sql: str) -> None:
        """Verify the entry AUTOINCREMENT actually works on INSERT —
        same shape the seed pipeline uses (no entry in column list)."""
        conn = sqlite3.connect(":memory:")
        _register_sqlite_aggregates(conn)
        try:
            conn.executescript(sqlite_sql)
            cur = conn.cursor()
            # Seed-style INSERT — no entry column.
            cur.execute(
                "INSERT INTO sqlt_transactions "
                "(id, account_id, account_scope, amount_money, "
                "amount_direction, status, posting, transfer_id, "
                "rail_name, origin) "
                "VALUES ('tx-1', 'acct-a', 'internal', 100.0, "
                "'Credit', 'Posted', '2030-01-01 00:00:00', 'tr-1', "
                "'SettlementRail', 'InternalInitiated')"
            )
            cur.execute("SELECT entry FROM sqlt_transactions WHERE id='tx-1'")
            entry, = cur.fetchone()
            assert entry == 1, f"Expected entry=1 for first INSERT, got {entry}"
        finally:
            conn.close()
