"""Unit tests for ``common.sql.app2_filters.app2_date_filter``.

Y.3.f.alt.4a: dialect-aware date filter. Oracle's default
``NLS_DATE_FORMAT`` rejects ISO-8601 strings via ``CAST(... AS DATE)``
(``ORA-01847``); use ``TO_DATE(..., 'YYYY-MM-DD')`` instead. PG's
``CAST`` parses ISO natively; SQLite stores dates as TEXT and
compares lexicographically with no cast needed.
"""

from __future__ import annotations

from quicksight_gen.common.sql import Dialect, app2_date_filter


class TestPostgres:
    def test_uses_cast_as_date(self):
        sql = app2_date_filter("t.posting", Dialect.POSTGRES)
        assert "CAST(" in sql
        assert " AS DATE)" in sql
        assert "TO_DATE" not in sql

    def test_includes_both_bind_placeholders(self):
        sql = app2_date_filter("t.posting", Dialect.POSTGRES)
        assert ":date_from" in sql
        assert ":date_to" in sql

    def test_uses_sentinel_dates(self):
        sql = app2_date_filter("t.posting", Dialect.POSTGRES)
        assert "1900-01-01" in sql
        assert "9999-12-30" in sql

    def test_leading_AND(self):
        sql = app2_date_filter("t.posting", Dialect.POSTGRES)
        assert sql.startswith("AND ")


class TestOracle:
    def test_uses_to_date_with_format_string(self):
        """Oracle: must use TO_DATE with explicit format, not CAST.
        CAST honors session NLS_DATE_FORMAT (default DD-MON-RR) and
        rejects ISO strings with ORA-01847."""
        sql = app2_date_filter("t.posting", Dialect.ORACLE)
        assert "TO_DATE(" in sql
        assert "'YYYY-MM-DD'" in sql
        # No bare CAST(... AS DATE) on Oracle
        assert " AS DATE)" not in sql

    def test_includes_both_bind_placeholders(self):
        sql = app2_date_filter("t.posting", Dialect.ORACLE)
        assert ":date_from" in sql
        assert ":date_to" in sql

    def test_uses_sentinel_dates(self):
        sql = app2_date_filter("t.posting", Dialect.ORACLE)
        assert "1900-01-01" in sql
        assert "9999-12-30" in sql


class TestSQLite:
    def test_no_cast_needed(self):
        """SQLite has no native DATE type; ISO-8601 TEXT comparisons
        work lexicographically. No CAST or TO_DATE call."""
        sql = app2_date_filter("t.posting", Dialect.SQLITE)
        assert "CAST" not in sql
        assert "TO_DATE" not in sql

    def test_includes_both_bind_placeholders(self):
        sql = app2_date_filter("t.posting", Dialect.SQLITE)
        assert ":date_from" in sql
        assert ":date_to" in sql

    def test_uses_sentinel_dates(self):
        sql = app2_date_filter("t.posting", Dialect.SQLITE)
        assert "1900-01-01" in sql
        assert "9999-12-30" in sql


class TestColumnInterpolation:
    def test_column_name_appears_per_dialect(self):
        for dialect in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.SQLITE):
            sql = app2_date_filter("t.posting", dialect)
            assert "t.posting" in sql
            assert sql.count("t.posting") == 2  # >= lower and < upper clauses


class TestDayInclusiveUpperBound:
    """X.2.j.dateparity — the upper bound is exclusive-of-the-next-day
    (``column < date_to + 1 day``), not ``column <= date_to``, so a
    same-day non-midnight ``TIMESTAMP`` row is included — matching
    QuickSight's DAY-granularity ``TimeRangeFilter``. Regression guard
    against re-introducing the ``<=`` form."""

    def test_upper_bound_is_strict_less_than(self):
        for dialect in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.SQLITE):
            sql = app2_date_filter("t.posting", dialect)
            assert "t.posting <" in sql, sql
            # The old buggy form.
            assert "t.posting <=" not in sql, sql

    def test_postgres_adds_one_day_interval(self):
        sql = app2_date_filter("t.posting", Dialect.POSTGRES)
        assert "+ INTERVAL '1 day'" in sql

    def test_oracle_adds_one(self):
        sql = app2_date_filter("t.posting", Dialect.ORACLE)
        # ``TO_DATE(...) + 1`` adds a day on Oracle DATE arithmetic.
        assert "'YYYY-MM-DD') + 1" in sql

    def test_sqlite_uses_date_plus_one_day(self):
        sql = app2_date_filter("t.posting", Dialect.SQLITE)
        assert "date(" in sql
        assert "'+1 day'" in sql
