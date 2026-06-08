"""CQ.2.b/g — picker-search SQL builders + matview-hint registry.

Pin the SQL shapes the typeahead fetcher emits on both paths:
- WRAP path (default) — wraps the dataset's CustomSql; for pickers
  whose universe is a multi-matview UNION (DS_L1_ACCOUNTS).
- MATVIEW-DIRECT path (CQ.2.g) — queries the matview directly; for
  pickers whose universe IS one single matview.

Plus the seed-page variants (empty query, no ILIKE filter) for the
Tom Select ``preload: 'focus'`` first-focus fetch.
"""

from __future__ import annotations

import pytest

from recon_gen.common.html._tree_fetcher import (
    PICKER_PAGE_SIZE,
    PickerMatviewHint,
    _picker_search_sql_matview_direct,
    _picker_search_sql_wrap,
    _picker_seed_sql_matview_direct,
    _picker_seed_sql_wrap,
    get_picker_matview_hint,
    register_picker_matview_hint,
)
from recon_gen.common.sql import Dialect


_BASE_SQL = (
    "SELECT DISTINCT account_id, account_role, account_name, "
    "COALESCE(account_name, account_id) || ' (' || account_id || ')' "
    "AS account_display FROM recon_current_daily_balances"
)
_MV_HINT = PickerMatviewHint(
    matview="recon_current_daily_balances",
    select_expr=(
        "(COALESCE(account_name, account_id) || ' ('"
        " || account_id || ')')"
    ),
)


class TestPickerSearchSqlWrap:
    def test_pg_uses_ilike(self) -> None:
        sql = _picker_search_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.POSTGRES,
        )
        assert "ILIKE" in sql
        assert ":q" in sql
        assert "LIMIT 100" in sql
        assert "ESCAPE '\\'" in sql

    def test_duckdb_uses_ilike(self) -> None:
        sql = _picker_search_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.DUCKDB,
        )
        assert "ILIKE" in sql
        assert "LIMIT 100" in sql

    def test_oracle_uses_upper_and_fetch_first(self) -> None:
        sql = _picker_search_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.ORACLE,
        )
        assert "UPPER" in sql
        assert "FETCH FIRST 100 ROWS ONLY" in sql
        assert "LIMIT" not in sql

    def test_wraps_base_sql_in_subquery(self) -> None:
        sql = _picker_search_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.POSTGRES,
        )
        assert f"({_BASE_SQL}) opt_src" in sql

    def test_dialect_column_naturalization(self) -> None:
        """Oracle gets UPPER-folded column name to dodge case-fold
        confusion at the planner."""
        sql = _picker_search_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.ORACLE,
        )
        assert "ACCOUNT_DISPLAY" in sql

    def test_custom_limit_honored(self) -> None:
        sql = _picker_search_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.POSTGRES,
            limit=42,
        )
        assert "LIMIT 42" in sql


class TestPickerSearchSqlMatviewDirect:
    def test_pg_uses_ilike_against_matview(self) -> None:
        sql = _picker_search_sql_matview_direct(
            _MV_HINT, dialect=Dialect.POSTGRES,
        )
        assert "FROM recon_current_daily_balances" in sql
        assert " opt_src" not in sql  # NOT wrapped
        assert "ILIKE" in sql
        assert ":q" in sql

    def test_matview_direct_does_NOT_wrap(self) -> None:
        """The whole point of matview-direct — no wrapping subquery."""
        sql = _picker_search_sql_matview_direct(
            _MV_HINT, dialect=Dialect.POSTGRES,
        )
        assert "FROM (" not in sql

    def test_oracle_upper_against_matview(self) -> None:
        sql = _picker_search_sql_matview_direct(
            _MV_HINT, dialect=Dialect.ORACLE,
        )
        assert "UPPER" in sql
        assert "FROM recon_current_daily_balances" in sql

    def test_select_expr_used_for_both_select_and_where(self) -> None:
        """Both the SELECT projection and the WHERE filter use the
        hint's ``select_expr`` — must match the dataset's wrap so the
        universe is identical."""
        sql = _picker_search_sql_matview_direct(
            _MV_HINT, dialect=Dialect.POSTGRES,
        )
        # SELECT side
        assert f"SELECT DISTINCT {_MV_HINT.select_expr} AS opt" in sql
        # WHERE side
        assert f"{_MV_HINT.select_expr} IS NOT NULL" in sql


class TestPickerSeedSql:
    """Empty-query seed: same shape as search but NO ILIKE clause."""

    def test_wrap_seed_has_no_ilike(self) -> None:
        sql = _picker_seed_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.POSTGRES,
        )
        assert "ILIKE" not in sql
        assert "UPPER" not in sql
        assert "LIMIT 100" in sql

    def test_matview_direct_seed_has_no_ilike(self) -> None:
        sql = _picker_seed_sql_matview_direct(
            _MV_HINT, dialect=Dialect.POSTGRES,
        )
        assert "ILIKE" not in sql
        assert "FROM recon_current_daily_balances" in sql

    def test_seed_returns_top_n_alphabetical(self) -> None:
        sql = _picker_seed_sql_wrap(
            _BASE_SQL, "account_display", dialect=Dialect.POSTGRES,
        )
        assert "ORDER BY 1" in sql


class TestPickerMatviewHintRegistry:
    def test_register_and_lookup_round_trip(self) -> None:
        register_picker_matview_hint("test-cq2-dataset-x", _MV_HINT)
        assert get_picker_matview_hint("test-cq2-dataset-x") == _MV_HINT

    def test_unregistered_returns_None(self) -> None:
        assert (
            get_picker_matview_hint("test-cq2-never-registered") is None
        )

    def test_overwrite_on_re_register(self) -> None:
        """Re-register under the same identifier overwrites — same
        semantics as register_sql (rebuilding for a new dialect
        replaces the prior entry).
        """
        register_picker_matview_hint("test-cq2-dataset-y", _MV_HINT)
        other = PickerMatviewHint(
            matview="other_matview", select_expr="other_expr",
        )
        register_picker_matview_hint("test-cq2-dataset-y", other)
        assert get_picker_matview_hint("test-cq2-dataset-y") == other


class TestPickerPageSizeConstant:
    def test_page_size_is_100(self) -> None:
        """100 is dropdown-fits-screen — NOT a truncation surrogate.
        Search results that hit this size are the first page of
        matches; the operator narrows further by typing more chars."""
        assert PICKER_PAGE_SIZE == 100

    @pytest.mark.parametrize("dialect", list(Dialect))
    def test_constant_threads_through_default_limit(
        self, dialect: Dialect,
    ) -> None:
        sql = _picker_search_sql_wrap(
            _BASE_SQL, "account_display", dialect=dialect,
        )
        limit_clause = (
            "FETCH FIRST 100 ROWS ONLY"
            if dialect is Dialect.ORACLE
            else "LIMIT 100"
        )
        assert limit_clause in sql
