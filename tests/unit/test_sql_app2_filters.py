"""Unit tests for
``common.sql.app2_filters.universal_date_range_clause``.

Phase BM dissolved the pre-BM ``app2_date_filter`` helper (which
emitted dialect-specific ``:date_from`` / ``:date_to`` bind clauses
for the App2 leg of the dual-SQL date-pushdown form). Its replacement
emits a single ``<<$pXxxDateStart>>`` / ``<<$pXxxDateEnd>>`` predicate
fragment that both renderers consume identically — QS substitutes
the parameter literal, App2 binds via ``:param_pXxxDate*``. These
tests pin the per-dialect shape so a future cast / format change is
caught at the smell-test layer.
"""

from __future__ import annotations

from recon_gen.apps.executives.datasets import (
    P_EXEC_DATE_END,
    P_EXEC_DATE_START,
)
from recon_gen.apps.l1_dashboard.datasets import P_L1_DATE_END, P_L1_DATE_START
from recon_gen.common.sql import Dialect, universal_date_range_clause


_START = P_L1_DATE_START
_END = P_L1_DATE_END


def _clause(dialect: Dialect, column: str = "business_day_start") -> str:
    return universal_date_range_clause(
        column, start_param=_START, end_param=_END, dialect=dialect,
    )


class TestPostgres:
    def test_uses_cast_as_timestamp(self) -> None:
        sql = _clause(Dialect.POSTGRES)
        assert "CAST(" in sql
        assert " AS TIMESTAMP)" in sql
        assert "TO_DATE" not in sql

    def test_includes_both_param_placeholders(self) -> None:
        sql = _clause(Dialect.POSTGRES)
        assert f"<<${_START}>>" in sql
        assert f"<<${_END}>>" in sql

    def test_upper_bound_expands_by_one_day(self) -> None:
        """The end bound is day-inclusive: ``< end + 1 day`` so
        same-day non-midnight TIMESTAMP rows on the end day are
        included."""
        sql = _clause(Dialect.POSTGRES)
        assert "+ INTERVAL '1 day'" in sql

    def test_no_leading_AND(self) -> None:
        """Phase BM dropped the leading ``AND`` — callers compose the
        clause via explicit ``WHERE ... AND <clause>``."""
        sql = _clause(Dialect.POSTGRES)
        assert not sql.startswith("AND ")


class TestOracle:
    def test_uses_to_date_with_substr_and_colon_free_format(self) -> None:
        """Oracle's default NLS_DATE_FORMAT doesn't parse ISO-T
        strings via bare CAST, so the helper routes through TO_DATE.

        The format string is ``'YYYY-MM-DD'`` (NOT the time-tokened
        ``'YYYY-MM-DD"T"HH24:MI:SS'``) because oracledb's pre-execution
        bind-name scanner trips on ``:MI`` / ``:SS`` inside string
        literals (DPY-4008). SUBSTR(p, 1, 10) chops both the bare
        ``YYYY-MM-DD`` and the ``YYYY-MM-DDTHH:MM:SS`` input shapes
        to the date prefix.
        """
        sql = _clause(Dialect.ORACLE)
        assert "TO_DATE(" in sql
        assert "SUBSTR(" in sql
        assert "'YYYY-MM-DD'" in sql
        # The ":MI" / ":SS" tokens must NOT appear — they break
        # oracledb's bind scanner.
        assert ":MI" not in sql
        assert ":SS" not in sql
        assert "CAST" not in sql

    def test_includes_both_param_placeholders(self) -> None:
        sql = _clause(Dialect.ORACLE)
        assert f"<<${_START}>>" in sql
        assert f"<<${_END}>>" in sql

    def test_upper_bound_adds_one_day(self) -> None:
        """Oracle DATE arithmetic: ``+ 1`` adds one day to the
        right-hand TO_DATE call. Day-inclusive semantics same as PG."""
        sql = _clause(Dialect.ORACLE)
        assert "') + 1" in sql


class TestSqlite:
    def test_uses_datetime_function(self) -> None:
        """SQLite has no native DATE type — ``datetime(...)``
        normalizes ISO TEXT inputs + supports the ``'+1 day'``
        modifier for the day-inclusive upper bound."""
        sql = _clause(Dialect.SQLITE)
        assert "datetime(" in sql
        assert "'+1 day'" in sql

    def test_includes_both_param_placeholders(self) -> None:
        sql = _clause(Dialect.SQLITE)
        assert f"<<${_START}>>" in sql
        assert f"<<${_END}>>" in sql


class TestColumnSubstitution:
    def test_column_name_appears_verbatim(self) -> None:
        sql = _clause(Dialect.POSTGRES, column="t.posting")
        # Once on the lower bound, once on the upper.
        assert sql.count("t.posting") == 2

    def test_param_names_are_caller_specified(self) -> None:
        sql = universal_date_range_clause(
            "posted_date",
            start_param=P_EXEC_DATE_START,
            end_param=P_EXEC_DATE_END,
            dialect=Dialect.POSTGRES,
        )
        assert f"<<${P_EXEC_DATE_START}>>" in sql
        assert f"<<${P_EXEC_DATE_END}>>" in sql
        # No leakage of the L1 names from the helper.
        assert P_L1_DATE_START not in sql
