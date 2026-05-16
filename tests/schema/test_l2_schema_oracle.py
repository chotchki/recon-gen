"""End-to-end Oracle DDL emission tests for ``common.l2.schema``.

Phase P.3.d.6 — by this point ``emit_schema`` and ``refresh_matviews_sql``
both accept ``dialect=Dialect.ORACLE`` and produce Oracle-19c-compatible
DDL. This file complements ``test_l2_schema.py`` (Postgres-only byte
assertions) with the Oracle-side guard rails:

- No Postgres-isms leak into the emitted SQL.
- Every Oracle-specific replacement is present in the right shape.
- The script is internally self-terminated (no double-semicolons that
  Oracle's PL/SQL parser would reject).

We intentionally don't snapshot the full Oracle DDL byte-for-byte —
the helpers are already pinned by ``test_sql_dialect.py``. Tests here
target the wire-up of those helpers into the bigger templates.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from quicksight_gen.common.l2 import (
    Identifier,
    L2Instance,
    emit_schema,
    refresh_matviews_sql,
)
from quicksight_gen.common.l2.primitives import (
    LimitSchedule,
    RoleExpression,
    SingleLegRail,
)
from quicksight_gen.common.sql import Dialect


def _strip_comments(sql: str) -> str:
    """Drop ``--`` line comments so negative-pattern checks don't trip
    on explanatory prose in the generated DDL."""
    return "\n".join(
        line for line in sql.split("\n")
        if not line.lstrip().startswith("--")
    )


def _full_instance(prefix: str) -> L2Instance:
    """An L2 instance with a rail (so stuck_pending/stuck_unbundled get
    a CASE branch) and a limit schedule (so limit_breach gets a CASE
    branch). Negative branches are tested separately by passing an
    empty-rail instance.
    """
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(
            SingleLegRail(
                name=Identifier("SettlementRail"),
                description="Settlement rail with aging",
                metadata_keys=(),
                leg_role=RoleExpression(Identifier("gl_control")),
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
                rail=Identifier("SettlementRail"),
                cap=10000,
            ),
        ),
    )


# -- emit_schema(Oracle) end-to-end ------------------------------------------


@pytest.fixture(scope="module")
def oracle_sql() -> str:
    """One Oracle DDL emission per module — every assertion runs
    against the same string so pytest output stays small."""
    return emit_schema(_full_instance("orcl"), prefix="orcl", dialect=Dialect.ORACLE)


@pytest.fixture(scope="module")
def oracle_sql_nocomments(oracle_sql: str) -> str:
    return _strip_comments(oracle_sql)


class TestOracleNoPostgresIsms:
    """Negative-pattern checks: every Postgres-only construct from the
    legacy templates must be gone in the Oracle output. Comments are
    stripped first because some narrative comments mention these
    constructs by name."""

    @pytest.mark.parametrize("pattern", [
        "BIGSERIAL",
        "TIMESTAMPTZ",          # P.9a — both dialects now use plain TIMESTAMP
        "TIMESTAMP WITH TIME ZONE",  # P.9a — same
        "VARCHAR(",     # Oracle uses VARCHAR2(N)
        "DECIMAL(",     # Oracle uses NUMBER(p, s)
        "TEXT,",        # column type — Oracle CLOB
        "TEXT)",
        "DROP TABLE IF EXISTS",
        "DROP MATERIALIZED VIEW IF EXISTS",
        "DROP INDEX IF EXISTS",
        "EXTRACT(EPOCH FROM",
        "DATE_TRUNC('day',",
        "::date",
        "::TEXT",
        "::TIMESTAMP",
        "::NUMERIC",
        "::numeric",
        "::bigint",
        "INTERVAL '1 day'",  # PG form; Oracle is INTERVAL '1' DAY
        "WITH RECURSIVE",
        "WHERE bundle_id IS NULL",  # PG partial-index optimization
    ])
    def test_postgres_pattern_absent(
        self, oracle_sql_nocomments: str, pattern: str,
    ) -> None:
        assert pattern not in oracle_sql_nocomments, (
            f"PG-ism leaked into Oracle DDL: {pattern!r}"
        )


class TestOracleConstructsPresent:
    """Positive-pattern checks: every Oracle-specific replacement must
    show up in the right shape."""

    @pytest.mark.parametrize("pattern", [
        # Type names
        "NUMBER GENERATED ALWAYS AS IDENTITY",
        # P.9a — TZ-naive TIMESTAMP across both dialects, NO ``WITH
        # TIME ZONE`` qualifier. Anchored on the column-aligned
        # appearances in the rendered DDL (transactions.posting +
        # daily_balances.business_day_start) so a future regression
        # like ``TIMESTAMPTZ`` or ``TIMESTAMP WITH TIME ZONE`` would
        # break the match.
        "posting              TIMESTAMP    NOT NULL",
        "business_day_start     TIMESTAMP    NOT NULL",
        "VARCHAR2(",
        "NUMBER(20,2)",
        "CLOB",
        # Idempotent DROPs (PL/SQL block per drop)
        "BEGIN EXECUTE IMMEDIATE 'DROP TABLE",
        "BEGIN EXECUTE IMMEDIATE 'DROP MATERIALIZED VIEW",
        "BEGIN EXECUTE IMMEDIATE 'DROP INDEX",
        # PL/SQL exception swallows for the per-object-type SQLCODEs
        "SQLCODE != -942",    # ORA-00942 = table/view not found
        "SQLCODE != -12003",  # ORA-12003 = matview not found
        "SQLCODE != -1418",   # ORA-01418 = index not found
        # Matview options on every CREATE
        "BUILD IMMEDIATE REFRESH COMPLETE ON DEMAND",
        # Date/time arithmetic
        "EXTRACT(DAY FROM",        # epoch-equivalent (Oracle has no EPOCH)
        "EXTRACT(HOUR FROM",
        "EXTRACT(MINUTE FROM",
        "EXTRACT(SECOND FROM",
        "CAST(TRUNC(tx.posting) AS TIMESTAMP)",  # date_trunc_day(Oracle)
        "TRUNC(posting)",          # to_date(Oracle)
        "TRUNC(recipient.posting)",
        # Interval arithmetic
        "INTERVAL '1' DAY",        # interval_days(1, Oracle)
        # Casts
        "CAST(AVG(window_sum) AS NUMBER)",
        "CAST((pw.posted_day - 1) AS TIMESTAMP)",
        "CAST(pw.posted_day AS TIMESTAMP)",
    ])
    def test_oracle_pattern_present(
        self, oracle_sql: str, pattern: str,
    ) -> None:
        assert pattern in oracle_sql, (
            f"Expected Oracle construct missing: {pattern!r}"
        )


class TestOracleScriptShape:
    """Whole-script properties — termination, statement boundaries."""

    def test_no_double_semicolons(self, oracle_sql: str) -> None:
        """``END;;`` would crash Oracle's PL/SQL parser. Helpers return
        self-terminated statements (PG ``;``, Oracle ``END;``); callers
        must not append another ``;``."""
        if ";;" in oracle_sql:
            i = oracle_sql.index(";;")
            context = oracle_sql[max(0, i - 80):i + 30]
            pytest.fail(f"Found ;; in Oracle script. Context: ...{context}...")

    def test_recursive_cte_uses_with_keyword(
        self, oracle_sql_nocomments: str,
    ) -> None:
        """Oracle 19c infers recursion from CTE self-reference; we emit
        plain ``WITH`` (which works on every Oracle release that
        supports recursive subquery factoring)."""
        # The money_trail matview is the only recursive CTE in our
        # schema — locate its CREATE and assert the next non-blank
        # non-comment line starts with ``WITH `` (no RECURSIVE).
        i = oracle_sql_nocomments.index(
            "CREATE MATERIALIZED VIEW orcl_inv_money_trail_edges",
        )
        # Skip past CREATE … AS, find the WITH preamble.
        next_with = oracle_sql_nocomments.index("WITH", i)
        # The next 4 chars after WITH should NOT be " RECURSIVE".
        assert not oracle_sql_nocomments[next_with:].startswith(
            "WITH RECURSIVE"
        )
        assert oracle_sql_nocomments[next_with:next_with + 5] == "WITH\n"

    def test_partial_index_skipped_for_oracle(
        self, oracle_sql_nocomments: str,
    ) -> None:
        """Z.B (2026-05-15): the bundler-eligibility index's column list
        ``(rail_name, status)`` now matches the rail_status index. On
        dialects without partial-index support (Oracle, SQLite < 3.8)
        emitting both triggers ORA-01408 ("such column list already
        indexed"). Oracle skips the bundler CREATE INDEX entirely; the
        full rail_status index above covers the same lookup. No
        ``WHERE bundle_id IS NULL`` appears anywhere in the non-comment
        Oracle SQL either."""
        assert (
            "CREATE INDEX idx_orcl_transactions_bundler_eligibility"
            not in oracle_sql_nocomments
        )
        for line in oracle_sql_nocomments.split("\n"):
            assert "WHERE bundle_id IS NULL" not in line


# -- refresh_matviews_sql(Oracle) -------------------------------------------


def test_refresh_matviews_sql_oracle_uses_dbms_mview() -> None:
    """REFRESH MATERIALIZED VIEW (PG) translates to DBMS_MVIEW.REFRESH
    on Oracle, wrapped in a PL/SQL block that's safe to run from
    either oracledb's cursor.execute or SQL*Plus."""
    sql = refresh_matviews_sql(_full_instance("orcl"), prefix="orcl", dialect=Dialect.ORACLE)
    assert ";;" not in sql
    assert "REFRESH MATERIALIZED VIEW" not in sql  # the PG verb
    # 15 matviews refresh in dependency order: 2 current_* + 2 helpers
    # (computed_*) + 7 L1 invariants + 2 dashboard-shape (daily_statement /
    # todays_exceptions) + 2 Investigation matviews.
    assert sql.count("BEGIN DBMS_MVIEW.REFRESH(") == 15
    assert sql.count("BEGIN DBMS_STATS.GATHER_TABLE_STATS(") == 15
    # Per-instance prefix appears in every refresh + analyze call.
    assert "'orcl_current_transactions'" in sql
    assert "'orcl_inv_money_trail_edges'" in sql
