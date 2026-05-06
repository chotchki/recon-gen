"""Unit tests for ``common.sql.dialect``.

Phase P.2 shipped every helper with a Postgres branch only; Phase
P.3 filled in the Oracle branches. Tests cover both — the Postgres
branch returns the canonical bytes, the Oracle branch returns the
Oracle 19c-compatible equivalent.
"""

from __future__ import annotations

import pytest

from quicksight_gen.common.sql import (
    Dialect,
    analyze_table,
    boolean_type,
    cast,
    create_matview,
    date_literal,
    date_minus_days,
    date_trunc_day,
    decimal_type,
    drop_index_if_exists,
    drop_matview_if_exists,
    drop_table_if_exists,
    drop_view_if_exists,
    epoch_seconds_between,
    interval_days,
    json_check,
    matview_options,
    refresh_matview,
    serial_type,
    text_type,
    timestamp_type,
    to_date,
    typed_null,
    varchar_type,
    with_recursive,
)


PG = Dialect.POSTGRES
ORA = Dialect.ORACLE
SQLITE = Dialect.SQLITE


# -- Postgres branches -------------------------------------------------------


class TestPostgresTypeNames:
    def test_serial_type(self):
        assert serial_type(PG) == "BIGSERIAL"

    def test_boolean_type(self):
        assert boolean_type(PG) == "BOOLEAN"

    def test_text_type(self):
        assert text_type(PG) == "TEXT"

    def test_timestamp_type(self):
        # P.9a — TZ-naive TIMESTAMP across both dialects.
        assert timestamp_type(PG) == "TIMESTAMP"

    def test_varchar_type(self):
        assert varchar_type(100, PG) == "VARCHAR(100)"

    def test_decimal_type(self):
        assert decimal_type(20, 2, PG) == "DECIMAL(20,2)"


class TestPostgresCasts:
    def test_cast(self):
        assert cast("col", "numeric", PG) == "col::numeric"
        assert cast("(a + b)", "bigint", PG) == "(a + b)::bigint"

    def test_typed_null(self):
        assert typed_null("numeric", PG) == "NULL::numeric"
        assert typed_null("bigint", PG) == "NULL::bigint"

    def test_to_date(self):
        assert to_date("posting", PG) == "posting::date"
        assert to_date("recipient.posting", PG) == "recipient.posting::date"


class TestPortableJson:
    def test_json_check_postgres(self):
        assert json_check("metadata", PG) == (
            "CHECK (metadata IS NULL OR metadata IS JSON)"
        )

    def test_json_check_oracle_identical(self):
        # Both dialects ship SQL/JSON-standard IS JSON since
        # Postgres 16+ / Oracle 12.2+ — bytes-identical output.
        assert json_check("metadata", ORA) == (
            "CHECK (metadata IS NULL OR metadata IS JSON)"
        )


class TestPostgresDateTime:
    def test_epoch_seconds_between(self):
        assert epoch_seconds_between(
            "CURRENT_TIMESTAMP", "ct.posting", PG,
        ) == "EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - ct.posting))"

    def test_interval_days(self):
        assert interval_days(1, PG) == "INTERVAL '1 day'"
        assert interval_days(7, PG) == "INTERVAL '7 day'"

    def test_date_minus_days(self):
        assert date_minus_days("pw.posted_day", 1, PG) == (
            "(pw.posted_day - INTERVAL '1 day')"
        )

    def test_date_trunc_day(self):
        # PG keeps the original timestamp shape — DATE_TRUNC('day', X)
        # returns the same type X has (TIMESTAMPTZ → TIMESTAMPTZ at
        # 00:00:00).
        assert date_trunc_day("tx.posting", PG) == (
            "DATE_TRUNC('day', tx.posting)"
        )

    def test_date_literal(self):
        # SQL-standard ``DATE 'YYYY-MM-DD'`` literal — accepted by both
        # Postgres and Oracle, byte-identical between them.
        assert date_literal("2030-01-01", PG) == "DATE '2030-01-01'"
        assert date_literal("2030-01-08", PG) == "DATE '2030-01-08'"


class TestPostgresDdlIdempotency:
    # P.3.d.2 — DDL idempotency + statement-runner helpers return
    # **fully terminated** statements so the Oracle PL/SQL ``END;`` and
    # the Postgres trailing ``;`` share one convention. Callers
    # concatenate without appending ``;``.

    def test_drop_table(self):
        assert drop_table_if_exists("foo", PG) == (
            "DROP TABLE IF EXISTS foo CASCADE;"
        )

    def test_drop_matview(self):
        assert drop_matview_if_exists("p_drift", PG) == (
            "DROP MATERIALIZED VIEW IF EXISTS p_drift;"
        )

    def test_drop_index(self):
        assert drop_index_if_exists("idx_foo", PG) == (
            "DROP INDEX IF EXISTS idx_foo;"
        )

    def test_drop_view(self):
        assert drop_view_if_exists("v_foo", PG) == "DROP VIEW IF EXISTS v_foo;"


class TestPostgresMatviews:
    def test_matview_options(self):
        # Postgres takes no options on CREATE MATERIALIZED VIEW.
        assert matview_options(PG) == ""

    def test_create_matview(self):
        # ``create_matview`` is the only matview helper that does NOT
        # carry its own terminator — its caller wraps the whole
        # CREATE in a ; or stitches it inline in a template.
        result = create_matview("p_drift", "SELECT 1", PG)
        assert result == "CREATE MATERIALIZED VIEW p_drift AS SELECT 1"

    def test_refresh_matview(self):
        assert refresh_matview("p_drift", PG) == (
            "REFRESH MATERIALIZED VIEW p_drift;"
        )

    def test_analyze_table(self):
        assert analyze_table("p_drift", PG) == "ANALYZE p_drift;"


class TestPostgresRecursiveCte:
    def test_with_recursive(self):
        assert with_recursive(PG) == "WITH RECURSIVE"


# -- Oracle branches ---------------------------------------------------------


class TestOracleTypeNames:
    def test_serial_type(self):
        assert serial_type(ORA) == "NUMBER GENERATED ALWAYS AS IDENTITY"

    def test_boolean_type(self):
        # Oracle 19c has no native BOOLEAN; canonical encoding is
        # NUMBER(1). Caller composes the CHECK (col IN (0,1)).
        assert boolean_type(ORA) == "NUMBER(1)"

    def test_text_type(self):
        assert text_type(ORA) == "CLOB"

    def test_timestamp_type(self):
        # P.9a — TZ-naive TIMESTAMP across both dialects.
        assert timestamp_type(ORA) == "TIMESTAMP"

    def test_varchar_type(self):
        assert varchar_type(100, ORA) == "VARCHAR2(100)"

    def test_decimal_type(self):
        assert decimal_type(20, 2, ORA) == "NUMBER(20,2)"


class TestOracleCasts:
    def test_cast_numeric_aliases_to_number(self):
        # Postgres-shape "numeric" → Oracle "NUMBER".
        assert cast("col", "numeric", ORA) == "CAST(col AS NUMBER)"

    def test_cast_bigint_aliases_to_number_19(self):
        assert cast("(a + b)", "bigint", ORA) == "CAST((a + b) AS NUMBER(19))"

    def test_cast_unaliased_type_passes_through(self):
        # Type names not in the Postgres-alias table pass through verbatim.
        assert cast("col", "VARCHAR2(50)", ORA) == "CAST(col AS VARCHAR2(50))"

    def test_typed_null_numeric(self):
        assert typed_null("numeric", ORA) == "CAST(NULL AS NUMBER)"

    def test_typed_null_bigint(self):
        assert typed_null("bigint", ORA) == "CAST(NULL AS NUMBER(19))"

    def test_to_date(self):
        assert to_date("posting", ORA) == "TRUNC(posting)"


class TestOracleDateTime:
    def test_epoch_seconds_between(self):
        # Oracle has no EPOCH unit; replicate via DAY*86400 +
        # HOUR*3600 + MINUTE*60 + SECOND on the INTERVAL DAY TO SECOND
        # result.
        result = epoch_seconds_between("CURRENT_TIMESTAMP", "ct.posting", ORA)
        assert "EXTRACT(DAY FROM " in result
        assert "* 86400" in result
        assert "EXTRACT(SECOND FROM " in result

    def test_interval_days(self):
        assert interval_days(1, ORA) == "INTERVAL '1' DAY"
        assert interval_days(7, ORA) == "INTERVAL '7' DAY"

    def test_date_minus_days(self):
        # Oracle DATE arithmetic interprets "date - n" as N days.
        assert date_minus_days("pw.posted_day", 1, ORA) == "(pw.posted_day - 1)"

    def test_date_trunc_day(self):
        # Oracle TRUNC(timestamp) returns DATE; CAST back to TIMESTAMP
        # so JOIN comparisons against TIMESTAMPTZ columns don't fall
        # through implicit conversion.
        assert date_trunc_day("tx.posting", ORA) == (
            "CAST(TRUNC(tx.posting) AS TIMESTAMP)"
        )

    def test_date_literal_identical_to_postgres(self):
        # Oracle accepts the same SQL-standard DATE literal as Postgres.
        # ``date_literal`` returns the same bytes for both dialects so
        # the audit f-string SQL is symmetric across them.
        assert date_literal("2030-01-01", ORA) == "DATE '2030-01-01'"
        assert date_literal("2030-01-08", ORA) == "DATE '2030-01-08'"


class TestOracleDdlIdempotency:
    def test_drop_table_wraps_in_plsql_block(self):
        sql = drop_table_if_exists("foo", ORA)
        assert sql.startswith("BEGIN EXECUTE IMMEDIATE 'DROP TABLE foo CASCADE CONSTRAINTS'")
        assert "EXCEPTION" in sql
        assert "SQLCODE != -942" in sql
        assert sql.endswith("END;")

    def test_drop_matview_swallows_two_codes(self):
        sql = drop_matview_if_exists("p_drift", ORA)
        # ORA-12003 (matview) AND ORA-942 (table-or-view, in case the
        # object has been recreated as a regular table) both ignored.
        assert "SQLCODE != -12003" in sql
        assert "SQLCODE != -942" in sql

    def test_drop_index_swallows_1418(self):
        sql = drop_index_if_exists("idx_foo", ORA)
        assert "SQLCODE != -1418" in sql

    def test_drop_view_swallows_942(self):
        sql = drop_view_if_exists("v_foo", ORA)
        assert "SQLCODE != -942" in sql


class TestOracleMatviews:
    def test_matview_options_includes_build_immediate(self):
        # Oracle matviews need explicit BUILD IMMEDIATE so the matview
        # is populated at create time (not deferred).
        opts = matview_options(ORA)
        assert "BUILD IMMEDIATE" in opts
        assert "REFRESH COMPLETE ON DEMAND" in opts
        # Has a leading space so it splices cleanly into "CREATE
        # MATERIALIZED VIEW <name>{matview_options} AS body".
        assert opts.startswith(" ")

    def test_create_matview_emits_build_immediate(self):
        sql = create_matview("p_drift", "SELECT 1", ORA)
        assert "BUILD IMMEDIATE" in sql
        assert "REFRESH COMPLETE ON DEMAND" in sql
        assert sql.endswith("AS SELECT 1")

    def test_refresh_matview_uses_dbms_mview(self):
        assert refresh_matview("p_drift", ORA) == (
            "BEGIN DBMS_MVIEW.REFRESH('p_drift', method => 'C'); END;"
        )

    def test_analyze_table_uses_dbms_stats(self):
        assert analyze_table("p_drift", ORA) == (
            "BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, 'p_drift'); END;"
        )


class TestOracleRecursiveCte:
    def test_with_recursive_drops_keyword(self):
        # Oracle 19c infers recursion from self-reference; "WITH" alone.
        assert with_recursive(ORA) == "WITH"


# -- Dialect enum ------------------------------------------------------------


class TestDialectEnum:
    def test_string_values(self):
        assert Dialect.POSTGRES.value == "postgres"
        assert Dialect.ORACLE.value == "oracle"
        assert Dialect.SQLITE.value == "sqlite"

    def test_round_trip_from_string(self):
        assert Dialect("postgres") is Dialect.POSTGRES
        assert Dialect("oracle") is Dialect.ORACLE
        assert Dialect("sqlite") is Dialect.SQLITE


# -- SQLite branches ---------------------------------------------------------


class TestSqliteTypeNames:
    """X.3.a — SQLite type names. SQLite is typeless internally so the
    affinity names are advisory; we prefer ``TEXT`` / ``INTEGER`` /
    ``NUMERIC`` over Postgres-shape names so the emitted DDL reads as
    SQLite-native rather than Postgres-with-a-different-engine."""

    def test_serial_type(self):
        # No INTEGER PRIMARY KEY AUTOINCREMENT — composite (id, entry)
        # PK can't use the auto-increment shortcut. Schema emit pairs
        # the bare INTEGER type with a BEFORE INSERT trigger that
        # computes entry per id.
        assert serial_type(SQLITE) == "INTEGER"

    def test_boolean_type(self):
        assert boolean_type(SQLITE) == "INTEGER"

    def test_text_type(self):
        assert text_type(SQLITE) == "TEXT"

    def test_timestamp_type(self):
        # TIMESTAMP across all 3 dialects (P.9a kept the unification).
        assert timestamp_type(SQLITE) == "TIMESTAMP"

    def test_varchar_type(self):
        # VARCHAR(N) collapses to TEXT — SQLite's VARCHAR length is
        # advisory so dropping the (N) keeps the DDL honest.
        assert varchar_type(100, SQLITE) == "TEXT"

    def test_decimal_type(self):
        assert decimal_type(20, 2, SQLITE) == "NUMERIC"


class TestSqliteCasts:
    def test_cast_numeric(self):
        assert cast("col", "numeric", SQLITE) == "CAST(col AS NUMERIC)"

    def test_cast_bigint(self):
        # bigint → INTEGER (SQLite has only one integer type internally).
        assert cast("(a + b)", "bigint", SQLITE) == "CAST((a + b) AS INTEGER)"

    def test_typed_null_numeric(self):
        assert typed_null("numeric", SQLITE) == "CAST(NULL AS NUMERIC)"

    def test_typed_null_bigint(self):
        assert typed_null("bigint", SQLITE) == "CAST(NULL AS INTEGER)"

    def test_to_date(self):
        assert to_date("posting", SQLITE) == "DATE(posting)"


class TestSqliteJsonCheck:
    def test_uses_json_valid(self):
        # SQLite's JSON1 extension ships ``json_valid()``; the SQL/JSON
        # standard ``IS JSON`` predicate isn't available.
        assert json_check("metadata", SQLITE) == (
            "CHECK (metadata IS NULL OR json_valid(metadata))"
        )


class TestSqliteDateTime:
    def test_epoch_seconds_between(self):
        # julianday(later) - julianday(earlier) returns fractional
        # days; * 86400 gives seconds.
        result = epoch_seconds_between("CURRENT_TIMESTAMP", "ct.posting", SQLITE)
        assert "julianday(CURRENT_TIMESTAMP)" in result
        assert "julianday(ct.posting)" in result
        assert "* 86400" in result

    def test_interval_days(self):
        # SQLite has no INTERVAL; emit as a string for use inside
        # date(expr, '<interval>') modifier.
        assert interval_days(1, SQLITE) == "'1 days'"
        assert interval_days(7, SQLITE) == "'7 days'"

    def test_date_minus_days(self):
        assert date_minus_days("pw.posted_day", 1, SQLITE) == (
            "date(pw.posted_day, '-1 days')"
        )

    def test_date_trunc_day(self):
        # datetime(expr, 'start of day') returns YYYY-MM-DD HH:MM:SS at
        # midnight — matches the timestamp-shape semantics PG/Oracle
        # deliver via DATE_TRUNC / CAST(TRUNC AS TIMESTAMP).
        assert date_trunc_day("tx.posting", SQLITE) == (
            "datetime(tx.posting, 'start of day')"
        )

    def test_date_literal_plain_text(self):
        # SQLite has no native DATE type and rejects ``DATE 'literal'``
        # as a column reference. ``CAST('YYYY-MM-DD' AS DATE)`` coerces
        # to INTEGER 2030 (NUMERIC affinity extracts the leading digits)
        # — silently wrong for comparisons against TEXT-stored ISO
        # dates. The plain quoted-string form sorts lexically and ISO
        # ordering matches date ordering, so comparisons are correct.
        assert date_literal("2030-01-01", SQLITE) == "'2030-01-01'"
        assert date_literal("2030-01-08", SQLITE) == "'2030-01-08'"


class TestSqliteDdlIdempotency:
    """X.3.a — SQLite DDL idempotency uses native ``IF EXISTS``."""

    def test_drop_table(self):
        # No CASCADE keyword — SQLite enforces FKs via PRAGMA, not
        # CASCADE.
        assert drop_table_if_exists("foo", SQLITE) == "DROP TABLE IF EXISTS foo;"

    def test_drop_matview(self):
        # Matviews land as plain tables in SQLite — drop them as tables.
        assert drop_matview_if_exists("p_drift", SQLITE) == (
            "DROP TABLE IF EXISTS p_drift;"
        )

    def test_drop_index(self):
        assert drop_index_if_exists("idx_foo", SQLITE) == (
            "DROP INDEX IF EXISTS idx_foo;"
        )

    def test_drop_view(self):
        assert drop_view_if_exists("v_foo", SQLITE) == "DROP VIEW IF EXISTS v_foo;"


class TestSqliteMatviews:
    def test_matview_options_empty(self):
        # CREATE TABLE … AS — no per-keyword suffix.
        from quicksight_gen.common.sql import matview_create_keyword
        assert matview_options(SQLITE) == ""
        assert matview_create_keyword(SQLITE) == "CREATE TABLE"

    def test_create_matview_emits_create_table(self):
        sql = create_matview("p_drift", "SELECT 1", SQLITE)
        assert sql == "CREATE TABLE p_drift AS SELECT 1"

    def test_refresh_matview_raises(self):
        # SQLite's matview refresh is a DELETE + INSERT pair using the
        # matview body SELECT — which lives in the schema template,
        # not in this helper. The dialect helper raises so callers get
        # routed through ``refresh_matviews_sql`` in common.l2.schema.
        import pytest as _pytest
        with _pytest.raises(NotImplementedError, match="SQLite refresh"):
            refresh_matview("p_drift", SQLITE)

    def test_analyze_table(self):
        assert analyze_table("p_drift", SQLITE) == "ANALYZE p_drift;"


class TestSqliteRecursiveCte:
    def test_with_recursive(self):
        # SQLite requires the RECURSIVE keyword (same as PG).
        assert with_recursive(SQLITE) == "WITH RECURSIVE"


class TestSqliteDualFrom:
    def test_no_from_dual_clause(self):
        # SQLite accepts the bare ``SELECT 'x'`` form (like Postgres).
        from quicksight_gen.common.sql import dual_from
        assert dual_from(SQLITE) == ""


# -- Default-arg behavior ---------------------------------------------------


class TestDialectIsRequired:
    """P.3.e dropped the ``dialect: Dialect = Dialect.POSTGRES`` defaults
    from every helper — every call site must pass dialect explicitly.
    These tests pin that behavior so the defaults can't sneak back in
    without anyone noticing.
    """

    @pytest.mark.parametrize(
        "fn,args",
        [
            (serial_type, ()),
            (boolean_type, ()),
            (text_type, ()),
            (timestamp_type, ()),
            (varchar_type, (50,)),
            (decimal_type, (10, 2)),
            (typed_null, ("numeric",)),
            (interval_days, (1,)),
            (with_recursive, ()),
            (refresh_matview, ("foo",)),
            (analyze_table, ("foo",)),
        ],
    )
    def test_omitting_dialect_is_a_typeerror(self, fn, args):
        with pytest.raises(TypeError, match="missing.*positional"):
            fn(*args)
