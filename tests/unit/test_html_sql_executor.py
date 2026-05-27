"""X.2.f — SQL executor + dialect placeholder rewrite tests.

The executor is the layer between a Visual's dataset SQL (with
``:name`` placeholders) and the per-renderer shape adapter. Two
concerns:

1. Placeholder dispatch — Postgres rewrites to ``%(name)s``;
   Oracle + SQLite keep ``:name``.
2. Bind-param collection — names referenced in SQL get pulled from
   ``url_params`` (default empty string when absent).

Tests cover both, plus a round-trip against in-memory SQLite to
prove the full executor path works without hitting PG / Oracle
infrastructure.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from recon_gen.apps.l1_dashboard.app import _DRILL_RESET_SENTINEL
from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
from recon_gen.common.html._sql_executor import (
    apply_dataset_param_defaults,
    collect_bind_params,
    execute_visual_sql,
    execute_visual_sql_async,
    expand_multivalued_dataset_params,
    rewrite_placeholders_for_dialect,
    translate_qs_dataset_params,
)
from recon_gen.common.models import (
    DatasetParameter,
    IntegerDatasetParameter,
    IntegerDatasetParameterDefaultValues,
    StringDatasetParameter,
    StringDatasetParameterDefaultValues,
)
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


def _string_dsp(
    name: str, *, value_type: str, defaults: list[str] | None
) -> DatasetParameter:
    return DatasetParameter(
        StringDatasetParameter=StringDatasetParameter(
            Id=f"id-{name}",
            Name=name,
            ValueType=value_type,
            DefaultValues=(
                StringDatasetParameterDefaultValues(StaticValues=defaults)
                if defaults is not None
                else None
            ),
        )
    )


def _int_dsp(
    name: str, *, value_type: str, defaults: list[int] | None
) -> DatasetParameter:
    return DatasetParameter(
        IntegerDatasetParameter=IntegerDatasetParameter(
            Id=f"id-{name}",
            Name=name,
            ValueType=value_type,
            DefaultValues=(
                IntegerDatasetParameterDefaultValues(StaticValues=defaults)
                if defaults is not None
                else None
            ),
        )
    )


# ---------------------------------------------------------------------------
# Y.1.e — QS dataset-parameter placeholder translation
# ---------------------------------------------------------------------------


def test_translate_unquoted_qs_placeholder_to_bind() -> None:
    """Numeric param convention: ``<<$pName>>`` (unquoted in QS SQL)
    becomes ``:param_pName`` (bind variable, App2 driver-quoted)."""
    sql = "SELECT * FROM t WHERE z_score >= <<$pInvAnomaliesSigma>>"
    out = translate_qs_dataset_params(sql)
    assert out == "SELECT * FROM t WHERE z_score >= :param_pInvAnomaliesSigma"


def test_translate_quoted_qs_placeholder_strips_outer_quotes() -> None:
    """String param convention: ``'<<$pName>>'`` (QS author wraps in
    quotes so substitution produces a valid SQL string literal). The
    bind variable doesn't need quoting — the driver quotes for us —
    so the translator strips the surrounding quotes."""
    sql = "SELECT * FROM t WHERE source_display = '<<$pAnchor>>'"
    out = translate_qs_dataset_params(sql)
    assert out == "SELECT * FROM t WHERE source_display = :param_pAnchor"
    assert "'<<$" not in out
    assert "'>" not in out


def test_translate_handles_multiple_placeholders() -> None:
    sql = (
        "SELECT * FROM t WHERE z_score >= <<$pSigma>> "
        "AND root_transfer_id = '<<$pRoot>>' "
        "AND depth <= <<$pMaxHops>>"
    )
    out = translate_qs_dataset_params(sql)
    assert out.count(":param_pSigma") == 1
    assert out.count(":param_pRoot") == 1
    assert out.count(":param_pMaxHops") == 1
    assert "<<$" not in out


def test_translate_passthrough_when_no_qs_placeholders() -> None:
    """Idempotent for SQL with no ``<<$>>`` markers — vanilla
    ``:name`` binds + plain SQL pass through unchanged."""
    sql = "SELECT * FROM t WHERE x = :date_from"
    out = translate_qs_dataset_params(sql)
    assert out == sql


def test_translate_then_rewrite_pg_yields_pyformat_binds() -> None:
    """Composition with rewrite_placeholders_for_dialect: the QS
    translator runs first (``<<$pName>>`` → ``:param_pName``),
    then PG rewrite turns the bind into ``%(param_pName)s``."""
    sql = "SELECT * FROM t WHERE z_score >= <<$pSigma>>"
    out = rewrite_placeholders_for_dialect(
        translate_qs_dataset_params(sql), Dialect.POSTGRES,
    )
    assert "%(param_pSigma)s" in out
    assert "<<$" not in out
    assert ":param_" not in out


# ---------------------------------------------------------------------------
# Y.2.app2.cde — dataset-parameter static-default substitution
# ---------------------------------------------------------------------------


def test_apply_defaults_single_string_default_splices_quoted_literal() -> None:
    sql = "SELECT * FROM t WHERE k = '<<$pKey>>'"
    params = [_string_dsp("pKey", value_type="SINGLE_VALUED", defaults=[_DRILL_RESET_SENTINEL])]
    out = apply_dataset_param_defaults(sql, params, {})
    assert out == f"SELECT * FROM t WHERE k = '{_DRILL_RESET_SENTINEL}'"


def test_apply_defaults_bare_string_default_splices_quoted_literal() -> None:
    """A bare ``<<$pName>>`` (no author quotes) for a string param —
    QS would substitute a quoted literal there too."""
    sql = "SELECT * FROM t WHERE k = <<$pKey>>"
    params = [_string_dsp("pKey", value_type="SINGLE_VALUED", defaults=["abc"])]
    out = apply_dataset_param_defaults(sql, params, {})
    assert out == "SELECT * FROM t WHERE k = 'abc'"


def test_apply_defaults_multi_string_default_comma_quoted_list() -> None:
    sql = "SELECT * FROM t WHERE rail IN (<<$pRail>>)"
    params = [
        _string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A", "B", "C"]),
    ]
    out = apply_dataset_param_defaults(sql, params, {})
    assert out == "SELECT * FROM t WHERE rail IN ('A','B','C')"


def test_apply_defaults_string_default_escapes_embedded_quote() -> None:
    sql = "SELECT * FROM t WHERE n = '<<$pName>>'"
    params = [_string_dsp("pName", value_type="SINGLE_VALUED", defaults=["O'Brien"])]
    out = apply_dataset_param_defaults(sql, params, {})
    assert out == "SELECT * FROM t WHERE n = 'O''Brien'"


def test_apply_defaults_single_integer_default_unquoted() -> None:
    sql = "SELECT * FROM t WHERE z_score >= <<$pSigma>>"
    params = [_int_dsp("pSigma", value_type="SINGLE_VALUED", defaults=[0])]
    out = apply_dataset_param_defaults(sql, params, {})
    assert out == "SELECT * FROM t WHERE z_score >= 0"


def test_apply_defaults_url_param_overrides_default_leaves_placeholder() -> None:
    """When the URL supplies ``?param_pRail=...`` the placeholder is
    LEFT for the bind path (``translate_qs_dataset_params`` →
    ``:param_pRail``) — never string-spliced from untrusted input."""
    sql = "SELECT * FROM t WHERE rail IN (<<$pRail>>)"
    params = [
        _string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A", "B"]),
    ]
    out = apply_dataset_param_defaults(sql, params, {"param_pRail": ["X"]})
    assert out == sql


def test_apply_defaults_undeclared_param_left_untouched() -> None:
    """A ``<<$pOther>>`` placeholder for a param not declared on this
    dataset is left as-is (some other layer's concern)."""
    sql = "SELECT * FROM t WHERE k = '<<$pOther>>'"
    params = [_string_dsp("pKey", value_type="SINGLE_VALUED", defaults=["X"])]
    out = apply_dataset_param_defaults(sql, params, {})
    assert out == sql


def test_apply_defaults_empty_static_default_left_untouched() -> None:
    """An empty StaticValues list = nothing to splice — leave the
    placeholder (avoids ``IN ()``; the dataset author handles this
    via a sentinel default)."""
    sql = "SELECT * FROM t WHERE rail IN (<<$pRail>>)"
    params = [_string_dsp("pRail", value_type="MULTI_VALUED", defaults=[])]
    out = apply_dataset_param_defaults(sql, params, {})
    assert out == sql


def test_apply_defaults_no_params_passes_through() -> None:
    sql = "SELECT * FROM t WHERE rail IN (<<$pRail>>)"
    assert apply_dataset_param_defaults(sql, [], {}) == sql


def test_apply_defaults_no_placeholders_passes_through() -> None:
    sql = "SELECT id, name FROM t ORDER BY id"
    params = [_string_dsp("pKey", value_type="SINGLE_VALUED", defaults=["X"])]
    assert apply_dataset_param_defaults(sql, params, {}) == sql


def test_apply_defaults_mixed_url_and_default_in_one_query() -> None:
    """Two placeholders, one supplied via URL and one not — the
    supplied one stays a placeholder, the other gets its default."""
    sql = (
        "SELECT * FROM t "
        "WHERE rail IN (<<$pRail>>) AND status IN (<<$pStatus>>)"
    )
    params = [
        _string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A"]),
        _string_dsp("pStatus", value_type="MULTI_VALUED", defaults=["Pending", "Posted"]),
    ]
    out = apply_dataset_param_defaults(sql, params, {"param_pRail": ["A"]})
    assert out == (
        "SELECT * FROM t "
        "WHERE rail IN (<<$pRail>>) AND status IN ('Pending','Posted')"
    )


def test_apply_defaults_then_translate_yields_clean_bound_sql() -> None:
    """End-to-end: default-substitute (no URL params) then translate —
    the result has no ``<<$>>`` markers and no leftover binds because
    every placeholder resolved to a literal."""
    sql = (
        "SELECT * FROM t WHERE k = '<<$pKey>>' "
        "AND rail IN (<<$pRail>>) AND z >= <<$pSigma>>"
    )
    params = [
        _string_dsp("pKey", value_type="SINGLE_VALUED", defaults=[_DRILL_RESET_SENTINEL]),
        _string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A", "B"]),
        _int_dsp("pSigma", value_type="SINGLE_VALUED", defaults=[2]),
    ]
    out = translate_qs_dataset_params(apply_dataset_param_defaults(sql, params, {}))
    assert out == (
        f"SELECT * FROM t WHERE k = '{_DRILL_RESET_SENTINEL}' "
        "AND rail IN ('A','B') AND z >= 2"
    )


# ---------------------------------------------------------------------------
# Y.2.app2.cde.multivalued — multi-valued IN-list bind expansion
# ---------------------------------------------------------------------------


def test_expand_multivalued_two_values_becomes_indexed_binds() -> None:
    sql = "SELECT * FROM t WHERE rail IN (<<$pRail>>)"
    params = [_string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A"])]
    out_sql, extra = expand_multivalued_dataset_params(
        sql, params, {"param_pRail": ["A", "B"]},
    )
    assert out_sql == "SELECT * FROM t WHERE rail IN (:param_pRail_0, :param_pRail_1)"
    assert extra == {"param_pRail_0": "A", "param_pRail_1": "B"}


def test_expand_multivalued_single_value_left_for_normal_bind() -> None:
    """One URL value → leave ``<<$pRail>>`` so the normal translate /
    collect path binds it as ``:param_pRail`` (works fine in ``IN (...)``)."""
    sql = "SELECT * FROM t WHERE rail IN (<<$pRail>>)"
    params = [_string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A"])]
    out_sql, extra = expand_multivalued_dataset_params(
        sql, params, {"param_pRail": ["A"]},
    )
    assert out_sql == sql
    assert extra == {}


def test_expand_multivalued_empty_or_absent_left_alone() -> None:
    """0 URL values (absent or emptied multi-select) → leave it; the
    static-default substitution (`apply_dataset_param_defaults`, which
    runs first) is responsible for that case."""
    sql = "SELECT * FROM t WHERE rail IN (<<$pRail>>)"
    params = [_string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A"])]
    assert expand_multivalued_dataset_params(sql, params, {}) == (sql, {})
    assert expand_multivalued_dataset_params(
        sql, params, {"param_pRail": [""]},
    ) == (sql, {})
    assert expand_multivalued_dataset_params(
        sql, params, {"param_pRail": []},
    ) == (sql, {})


def test_expand_multivalued_ignores_single_valued_and_undeclared_params() -> None:
    sql = (
        "SELECT * FROM t WHERE k = <<$pKey>> AND rail IN (<<$pOther>>)"
    )
    params = [_string_dsp("pKey", value_type="SINGLE_VALUED", defaults=["x"])]
    out_sql, extra = expand_multivalued_dataset_params(
        sql, params, {"param_pKey": ["a", "b"], "param_pOther": ["c", "d"]},
    )
    # pKey is SINGLE_VALUED (last-wins bind handles it); pOther is not
    # declared on this dataset — neither expands.
    assert out_sql == sql
    assert extra == {}


def test_expand_multivalued_quoted_form_drops_author_quotes() -> None:
    sql = "SELECT * FROM t WHERE rail IN ('<<$pRail>>')"
    params = [_string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A"])]
    out_sql, extra = expand_multivalued_dataset_params(
        sql, params, {"param_pRail": ["A", "B"]},
    )
    assert out_sql == "SELECT * FROM t WHERE rail IN (:param_pRail_0, :param_pRail_1)"
    assert extra == {"param_pRail_0": "A", "param_pRail_1": "B"}


def test_execute_visual_sql_expands_multivalued_in_list_against_db(
    sqlite_factory: Callable[[], Any],
) -> None:
    """End-to-end: a MULTI_VALUED ``<<$pName>>`` in an ``IN (...)`` with
    2 URL values filters the DB to exactly those rows — proving the
    expansion → translate → bind → execute pipeline (and that the
    values bind, never string-splice)."""
    params = [_string_dsp("pName", value_type="MULTI_VALUED", defaults=["alpha"])]
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id FROM t WHERE name IN (<<$pName>>) ORDER BY id",
        {"param_pName": ["alpha", "gamma"]},
        dialect=Dialect.SQLITE,
        dataset_parameters=params,
    )
    assert [r[0] for r in rows] == [1, 3]


def test_execute_visual_sql_multivalued_single_value_against_db(
    sqlite_factory: Callable[[], Any],
) -> None:
    """One URL value through the same pipeline — the non-expanded
    ``:param_pName`` bind path still produces ``IN ('beta')``."""
    params = [_string_dsp("pName", value_type="MULTI_VALUED", defaults=["alpha"])]
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id FROM t WHERE name IN (<<$pName>>)",
        {"param_pName": ["beta"]},
        dialect=Dialect.SQLITE,
        dataset_parameters=params,
    )
    assert [r[0] for r in rows] == [2]


def test_execute_visual_sql_multivalued_emptied_falls_to_default_against_db(
    sqlite_factory: Callable[[], Any],
) -> None:
    """Emptied multi-select (``?param_pName=``) → static default applies
    (QS reverts there too) → ``IN ('alpha')`` → only the alpha row."""
    params = [_string_dsp("pName", value_type="MULTI_VALUED", defaults=["alpha"])]
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id FROM t WHERE name IN (<<$pName>>)",
        {"param_pName": [""]},
        dialect=Dialect.SQLITE,
        dataset_parameters=params,
    )
    assert [r[0] for r in rows] == [1]


def test_execute_visual_sql_pg_multivalued_pyformat_binds() -> None:
    """Postgres rewrite: the expanded ``:param_pName_i`` binds become
    ``%(param_pName_i)s`` and the cursor receives both bind values."""
    received: dict[str, Any] = {}

    class _SnoopCursor:
        description = [("id",)]

        def execute(self, sql: str, params: Any = None) -> None:
            received["sql"] = sql
            received["params"] = params

        def fetchall(self) -> list[Any]:
            return []

        def close(self) -> None:
            pass

    class _SnoopConn:
        def cursor(self) -> Any:
            return _SnoopCursor()

        def close(self) -> None:
            pass

    ds_params = [_string_dsp("pRail", value_type="MULTI_VALUED", defaults=["A"])]
    execute_visual_sql(
        lambda: _SnoopConn(),
        "SELECT id FROM t WHERE rail IN (<<$pRail>>)",
        {"param_pRail": ["A", "B"]},
        dialect=Dialect.POSTGRES,
        dataset_parameters=ds_params,
    )
    assert "IN (%(param_pRail_0)s, %(param_pRail_1)s)" in received["sql"]
    assert received["params"] == {"param_pRail_0": "A", "param_pRail_1": "B"}


# ---------------------------------------------------------------------------
# Placeholder rewrite
# ---------------------------------------------------------------------------


def test_rewrite_postgres_uses_pyformat_named_binds() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from AND y >= :amount"
    out = rewrite_placeholders_for_dialect(sql, Dialect.POSTGRES)
    assert "%(date_from)s" in out
    assert "%(amount)s" in out
    assert ":date_from" not in out


def test_rewrite_oracle_keeps_colon_named() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from"
    out = rewrite_placeholders_for_dialect(sql, Dialect.ORACLE)
    assert out == sql


def test_rewrite_sqlite_keeps_colon_named() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from"
    out = rewrite_placeholders_for_dialect(sql, Dialect.SQLITE)
    assert out == sql


def test_rewrite_postgres_preserves_double_colon_cast() -> None:
    """``::float`` is PG cast syntax — must survive the rewrite."""
    sql = "SELECT amount::float FROM t WHERE x = :date_from"
    out = rewrite_placeholders_for_dialect(sql, Dialect.POSTGRES)
    assert "amount::float" in out
    assert "%(date_from)s" in out


def test_rewrite_handles_multiple_placeholders() -> None:
    sql = "SELECT a, :x, :y, c FROM t WHERE z = :x"
    out = rewrite_placeholders_for_dialect(sql, Dialect.POSTGRES)
    # Both occurrences of :x get rewritten.
    assert out.count("%(x)s") == 2
    assert "%(y)s" in out


# ---------------------------------------------------------------------------
# Bind collection
# ---------------------------------------------------------------------------


def test_collect_bind_params_picks_referenced_names() -> None:
    sql = "SELECT * FROM t WHERE x = :date_from AND y = :amount"
    url_params = {
        "date_from": ["2030-01-01"],
        "amount": ["100"],
        "filter_status": ["open"],  # not referenced — should be dropped
    }
    binds = collect_bind_params(sql, url_params)
    assert binds == {"date_from": "2030-01-01", "amount": "100"}


def test_collect_bind_params_coerces_integer_param_to_int() -> None:
    # AO.R.4 — an integer dataset param's URL value binds as an int, not
    # a string, so ``z >= :param_pSigma`` is a numeric (not text)
    # comparison. A string bind made SQLite's affinity match 0 rows (the
    # moved-σ-slider "0 flagged" bug; the default-substitution path
    # splices the int literal and worked, hiding it until a control moved).
    sql = "SELECT * FROM t WHERE z >= :param_pSigma"
    dps = [_int_dsp("pSigma", value_type="SINGLE_VALUED", defaults=[2])]
    binds = collect_bind_params(sql, {"param_pSigma": ["3"]}, dps)
    assert binds["param_pSigma"] == 3
    assert isinstance(binds["param_pSigma"], int)


def test_collect_bind_params_non_numeric_integer_value_falls_back_to_str() -> None:
    # A blank / non-numeric value for an integer param stays a string so
    # the dataset SQL author's empty-guard (``OR :p = ''``) still fires.
    sql = "SELECT * FROM t WHERE z >= :param_pSigma"
    dps = [_int_dsp("pSigma", value_type="SINGLE_VALUED", defaults=[2])]
    binds = collect_bind_params(sql, {"param_pSigma": [""]}, dps)
    assert binds["param_pSigma"] == ""


def test_collect_bind_params_takes_last_value_for_repeated_key() -> None:
    """A repeated query key (``?x=a&x=b``) collapses to its last
    value for a single ``:name`` bind — mirrors the old
    ``query_params.items()`` last-wins behavior."""
    sql = "SELECT * FROM t WHERE x = :x"
    binds = collect_bind_params(sql, {"x": ["a", "b"]})
    assert binds == {"x": "b"}


def test_collect_bind_params_defaults_missing_to_empty_string() -> None:
    """Dataset SQL author guards against empty filters; the executor
    just hands back ``""`` so the bind dict is complete. An empty
    value list (key present, no values) is treated the same."""
    sql = "SELECT * FROM t WHERE x = :date_from"
    assert collect_bind_params(sql, {}) == {"date_from": ""}
    assert collect_bind_params(sql, {"date_from": []}) == {"date_from": ""}


def test_collect_bind_params_drops_unreferenced_url_params() -> None:
    """Naive callers might pass the entire URL params dict; only
    the names actually in the SQL come through."""
    sql = "SELECT * FROM t"  # no placeholders
    binds = collect_bind_params(sql, {"foo": ["1"], "bar": ["2"]})
    assert binds == {}


# ---------------------------------------------------------------------------
# End-to-end executor against in-memory SQLite
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_factory() -> Iterator[Callable[[], Any]]:
    """In-memory SQLite seeded with a tiny test table. Yields the
    factory the executor expects (returns a fresh connection per
    call); the fixture closes the underlying conn at teardown."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE t (id INTEGER, name TEXT, amount REAL)"
    )
    conn.executemany(
        "INSERT INTO t VALUES (?, ?, ?)",
        [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)],
    )
    conn.commit()

    def factory() -> Any:
        # Wrap the existing conn so close() is a no-op (executor
        # closes per call but the fixture owns the lifecycle).
        class _ConnWrapper:
            def cursor(self) -> Any:
                return conn.cursor()

            def close(self) -> None:
                pass

        return _ConnWrapper()

    try:
        yield factory
    finally:
        conn.close()


def test_execute_visual_sql_returns_rows_and_columns(sqlite_factory: Callable[[], Any]) -> None:
    rows, cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id, name, amount FROM t ORDER BY id",
        {},
        dialect=Dialect.SQLITE,
    )
    assert rows == [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)]
    assert cols == ["id", "name", "amount"]


def test_execute_visual_sql_substitutes_named_filter(sqlite_factory: Callable[[], Any]) -> None:
    """``:min_amount`` from URL params lands as a bind value, not
    string-formatted into the SQL — proves the parameterized path."""
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id, name FROM t WHERE amount >= :min_amount ORDER BY id",
        {"min_amount": ["20"]},
        dialect=Dialect.SQLITE,
    )
    assert [r[1] for r in rows] == ["beta", "gamma"]


def test_execute_visual_sql_handles_multiple_filters(sqlite_factory: Callable[[], Any]) -> None:
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        (
            "SELECT id, name FROM t "
            "WHERE amount >= :min_amount AND amount <= :max_amount "
            "ORDER BY id"
        ),
        {"min_amount": ["15"], "max_amount": ["25"]},
        dialect=Dialect.SQLITE,
    )
    assert [r[1] for r in rows] == ["beta"]


def test_execute_visual_sql_unreferenced_url_params_dont_break_execution(
    sqlite_factory: Callable[[], Any],
) -> None:
    """The form serializes every input on every Refresh — extra
    params for filters this visual doesn't use must be silently
    dropped, not raised as 'too many parameters'."""
    rows, _cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id FROM t WHERE amount >= :min_amount",
        {
            "min_amount": ["15"],
            "filter_status": ["open"],      # unreferenced
            "param_view": ["summary"],      # unreferenced
            "date_from": ["2030-01-01"],    # unreferenced
        },
        dialect=Dialect.SQLITE,
    )
    assert {r[0] for r in rows} == {2, 3}


def test_execute_visual_sql_empty_result_set(sqlite_factory: Callable[[], Any]) -> None:
    rows, cols = execute_visual_sql(
        sqlite_factory,
        "SELECT id, name FROM t WHERE amount > :min_amount",
        {"min_amount": ["9999"]},
        dialect=Dialect.SQLITE,
    )
    assert rows == []
    assert cols == ["id", "name"]


# ---------------------------------------------------------------------------
# Postgres dispatch (rewrite verified end-to-end via a fake cursor)
# ---------------------------------------------------------------------------


def test_execute_visual_sql_passes_pg_pyformat_to_cursor() -> None:
    """For Postgres, the cursor must receive ``%(name)s``-form SQL
    plus the bind dict. Validates the rewrite happens inside
    execute_visual_sql, not just at the caller."""
    received: dict[str, Any] = {}

    class _SnoopCursor:
        description = [("col",)]

        def execute(self, sql: str, params: Any = None) -> None:
            received["sql"] = sql
            received["params"] = params

        def fetchall(self) -> list[Any]:
            return []

        def close(self) -> None:
            pass

    class _SnoopConn:
        def cursor(self) -> Any:
            return _SnoopCursor()

        def close(self) -> None:
            pass

    execute_visual_sql(
        lambda: _SnoopConn(),
        "SELECT col FROM t WHERE x = :date_from",
        {"date_from": ["2030-01-01"]},
        dialect=Dialect.POSTGRES,
    )
    assert "%(date_from)s" in received["sql"]
    assert ":date_from" not in received["sql"]
    assert received["params"] == {"date_from": "2030-01-01"}


# ---------------------------------------------------------------------------
# X.2.n.3 — Async executor against aiosqlite pool
# ---------------------------------------------------------------------------


@pytest.fixture
def aiosqlite_pool() -> Iterator[AsyncConnectionPool]:
    """File-backed aiosqlite pool seeded with the same tiny table.

    aiosqlite's ``:memory:`` mode gives each new connection a fresh
    isolated DB — the shared-pool tests need a tempfile so every
    acquire sees the seeded data.
    """
    import asyncio
    import os
    import sqlite3
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT, amount REAL)")
    conn.executemany(
        "INSERT INTO t VALUES (?, ?, ?)",
        [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)],
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        yield pool
    finally:
        asyncio.run(pool.close())
        os.unlink(path)


def test_execute_visual_sql_async_returns_rows_and_columns(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    import asyncio

    rows, cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id, name, amount FROM t ORDER BY id",
        {},
        dialect=Dialect.SQLITE,
    ))
    assert rows == [(1, "alpha", 10.0), (2, "beta", 20.0), (3, "gamma", 30.0)]
    assert cols == ["id", "name", "amount"]


def test_execute_visual_sql_async_substitutes_named_filter(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    import asyncio

    rows, _cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id, name FROM t WHERE amount >= :min_amount ORDER BY id",
        {"min_amount": ["20"]},
        dialect=Dialect.SQLITE,
    ))
    assert [r[1] for r in rows] == ["beta", "gamma"]


def test_execute_visual_sql_async_unreferenced_url_params_dont_break(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """Same ignore-unreferenced behavior as the sync version — extra
    URL params for filters this visual doesn't use must be silently
    dropped from the bind dict."""
    import asyncio

    rows, _cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id FROM t WHERE amount >= :min_amount",
        {
            "min_amount": ["15"],
            "filter_status": ["open"],
            "param_view": ["summary"],
            "date_from": ["2030-01-01"],
        },
        dialect=Dialect.SQLITE,
    ))
    assert {r[0] for r in rows} == {2, 3}


def test_execute_visual_sql_async_empty_result_set(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    import asyncio

    rows, cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id, name FROM t WHERE amount > :min_amount",
        {"min_amount": ["9999"]},
        dialect=Dialect.SQLITE,
    ))
    assert rows == []
    assert cols == ["id", "name"]


# ---------------------------------------------------------------------------
# Y.1.e — End-to-end QS placeholder round-trip via real SQLite driver
# ---------------------------------------------------------------------------


def test_async_qs_placeholder_translates_and_filters_at_db(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """The full Y.1 contract: dataset SQL with ``<<$pName>>``
    placeholder + URL param ``param_pName=<value>`` produces the
    correct filtered rows because the executor translates QS →
    bind, the driver substitutes the bind value, and SQLite filters
    in the database.

    This test is the spike's load-bearing assertion: prove that the
    end-to-end pipeline (QS-shaped SQL → App2 executor → real DB
    driver) works without the dataset author writing two SQLs.
    """
    import asyncio

    rows, cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id, name, amount FROM t WHERE amount >= <<$pMinAmount>>",
        {"param_pMinAmount": ["20"]},
        dialect=Dialect.SQLITE,
    ))
    # Threshold 20 → only beta (20.0) and gamma (30.0) match.
    assert {r[0] for r in rows} == {2, 3}
    assert cols == ["id", "name", "amount"]


def test_async_qs_quoted_placeholder_string_round_trip(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """QS string-param convention: ``'<<$pName>>'`` (author-quoted
    in the SQL because QS substitutes the literal value as a SQL
    string literal). Translator strips the surrounding quotes; the
    bind variable's driver-quoting picks up where QS's literal
    quoting left off."""
    import asyncio

    rows, _cols = asyncio.run(execute_visual_sql_async(
        aiosqlite_pool,
        "SELECT id FROM t WHERE name = '<<$pName>>'",
        {"param_pName": ["beta"]},
        dialect=Dialect.SQLITE,
    ))
    assert rows == [(2,)]
