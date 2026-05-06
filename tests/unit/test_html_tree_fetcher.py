"""X.2.g.0 — generic per-tree DataFetcher tests.

Verifies the build-time visual indexing + the request-time
SQL-execute → shape pipeline. Uses an in-memory SQLite fixture
so tests run without psycopg2 / oracledb. The SQL registry is
populated via a tiny test-helper that mimics what
``build_dataset()`` does in production.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest

from quicksight_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    register_contract,
    register_sql,
)
from quicksight_gen.common.html._tree_fetcher import (
    _find_visual_dataset_identifier,
    make_tree_db_fetcher,
)
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.sql.dialect import Dialect
from quicksight_gen.common.tree.datasets import Dataset
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import KPI, BarChart, Table
from tests._test_helpers import make_test_config


_TEST_CFG = make_test_config()
_TEST_CFG_SQLITE = make_test_config(dialect=Dialect.SQLITE)


# Register contracts ONCE at module level — register_contract raises
# on a second call with a different DatasetContract instance for the
# same identifier, so each test creating a fresh contract would
# collide. The contracts here describe the shared SQLite fixture.
_X2G_TEST_CONTRACT = DatasetContract(columns=[
    ColumnSpec(name="status", type="STRING"),
    ColumnSpec(name="amount", type="INTEGER"),
])
register_contract("x2g-test-ds", _X2G_TEST_CONTRACT)
register_contract("x2g-multi-ds", _X2G_TEST_CONTRACT)
register_contract("kpi-ds", DatasetContract(columns=[
    ColumnSpec(name="count", type="INTEGER"),
]))
register_contract("bar-ds", _X2G_TEST_CONTRACT)
register_contract("x2g-loud-fail-ds", DatasetContract(columns=[
    ColumnSpec(name="a", type="INTEGER"),
]))


# ---------------------------------------------------------------------------
# _find_visual_dataset_identifier — walks Visual → Dim/Measure → Dataset.id
# ---------------------------------------------------------------------------


def _ds(identifier: str = "test-ds") -> Dataset:
    return Dataset(identifier=identifier, arn=f"arn::{identifier}")


def test_find_dataset_id_via_kpi_values() -> None:
    ds = _ds("kpi-ds")
    visual = KPI(
        title="Open", subtitle=None, visual_id=VisualId("v-k"),
        values=[ds["count"].sum()],
    )
    assert _find_visual_dataset_identifier(visual) == "kpi-ds"


def test_find_dataset_id_via_bar_chart_category() -> None:
    ds = _ds("bar-ds")
    visual = BarChart(
        title="By status", subtitle=None, visual_id=VisualId("v-b"),
        category=[ds["status"].dim()],
        values=[ds["amount"].sum()],
    )
    assert _find_visual_dataset_identifier(visual) == "bar-ds"


def test_find_dataset_id_returns_none_for_visual_without_fields() -> None:
    """KPI with no measures / no fields → no dataset → None.
    Fetcher should treat this as "return empty payload"."""
    visual = KPI(
        title="Empty", subtitle=None, visual_id=VisualId("v-empty"),
    )
    assert _find_visual_dataset_identifier(visual) is None


# ---------------------------------------------------------------------------
# make_tree_db_fetcher — build-time indexing + request-time dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_factory() -> Iterator[Any]:
    """In-memory SQLite seeded with a tiny test table."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (status TEXT, amount INTEGER)")
    conn.executemany(
        "INSERT INTO t VALUES (?, ?)",
        [("open", 100), ("open", 50), ("closed", 200), ("pending", 25)],
    )
    conn.commit()

    def factory() -> Any:
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


def _build_app_with_visuals() -> tuple[App, Dataset]:
    """Tiny App with one sheet + KPI + BarChart, both backed by
    the same dataset (`test-ds`). Caller registers the SQL +
    contract before building a fetcher."""
    app = App(name="x2g-test", cfg=_TEST_CFG_SQLITE)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="x2g-test-analysis",
        name="X.2.g Test",
    ))
    ds = _ds("x2g-test-ds")
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("s1"), name="S1",
        title="Sheet 1", description="x",
    ))
    sheet.visuals.append(
        KPI(
            title="Total", subtitle=None, visual_id=VisualId("v-kpi"),
            values=[ds["amount"].sum()],
        ),
    )
    sheet.visuals.append(
        BarChart(
            title="By status", subtitle=None, visual_id=VisualId("v-bar"),
            category=[ds["status"].dim()],
            values=[ds["amount"].sum()],
        ),
    )
    return app, ds


def test_make_tree_db_fetcher_dispatches_kpi(sqlite_factory: Any) -> None:
    # X.2.g.1.c — dataset SQL is row-grain; the wrapper applies
    # SUM(amount) per the KPI's measure declaration.
    register_sql(
        "x2g-test-ds", "SELECT status, amount FROM t",
    )
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, connection_factory=sqlite_factory,
    )
    out = fetcher("v-kpi", {})
    # KPI shape: {values: [{value, ...}]}
    assert "values" in out
    assert out["values"][0]["value"] == 375  # 100+50+200+25


def test_make_tree_db_fetcher_dispatches_bar_chart(sqlite_factory: Any) -> None:
    # X.2.g.1.c — dataset SQL is row-grain; the wrapper produces
    # SELECT status, SUM(amount) FROM (...) GROUP BY status.
    register_sql(
        "x2g-test-ds", "SELECT status, amount FROM t",
    )
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, connection_factory=sqlite_factory,
    )
    out = fetcher("v-bar", {})
    # BarChart shape: {categories, values, x_label, y_label}
    assert out["categories"] == ["closed", "open", "pending"]
    assert out["values"] == [200, 150, 25]


def test_make_tree_db_fetcher_substitutes_filters(sqlite_factory: Any) -> None:
    """URL params with names referenced in the dataset SQL flow
    through to the bind dict (proves end-to-end X.2.d → X.2.f →
    X.2.g.0 round-trip)."""
    register_sql(
        "x2g-test-ds",
        "SELECT status, amount FROM t WHERE status = :param_status",
    )
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, connection_factory=sqlite_factory,
    )
    out = fetcher("v-kpi", {"param_status": "open"})
    assert out["values"][0]["value"] == 150  # only "open" rows


def test_make_tree_db_fetcher_unknown_visual_id_returns_empty(
    sqlite_factory: Any,
) -> None:
    """Stale URLs (cached pages, swap-after-restart) shouldn't
    crash — empty payload renders an empty visual."""
    register_sql("x2g-test-ds", "SELECT status, amount FROM t")
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, connection_factory=sqlite_factory,
    )
    out = fetcher("v-does-not-exist", {})
    assert out == {}


def test_make_tree_db_fetcher_visual_without_dataset_returns_empty(
    sqlite_factory: Any,
) -> None:
    """A KPI with no measures (no dataset reference) gets an empty
    payload; doesn't fail at fetcher build time."""
    app = App(name="x2g-empty-test", cfg=_TEST_CFG_SQLITE)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="empty-test", name="Empty",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("s"), name="S", title="S", description="x",
    ))
    sheet.visuals.append(
        KPI(title="Empty", subtitle=None, visual_id=VisualId("v-x")),
    )
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, connection_factory=sqlite_factory,
    )
    out = fetcher("v-x", {})
    assert out == {}


def test_make_tree_db_fetcher_fails_loudly_on_missing_sql() -> None:
    """If the registry doesn't have SQL for a referenced dataset,
    failure happens at build time (not buried inside a swap)."""
    # Use an identifier we DON'T register SQL for (contract registered at module top).
    unique = "x2g-loud-fail-ds"
    app = App(name="x2g-loud-test", cfg=_TEST_CFG_SQLITE)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="loud", name="Loud",
    ))
    ds = _ds(unique)
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("s"), name="S", title="S", description="x",
    ))
    sheet.visuals.append(
        KPI(title="X", subtitle=None, visual_id=VisualId("v-x"),
            values=[ds["a"].sum()]),
    )
    with pytest.raises(KeyError, match="No SQL registered"):
        make_tree_db_fetcher(
            app, _TEST_CFG_SQLITE,
            connection_factory=lambda: None,  # never reached
        )


def test_make_tree_db_fetcher_indexes_visuals_across_sheets(
    sqlite_factory: Any,
) -> None:
    """Multi-sheet App: every analysis sheet's visuals are
    addressable through the same fetcher."""
    register_sql("x2g-multi-ds", "SELECT status, amount FROM t")
    app = App(name="x2g-multi", cfg=_TEST_CFG_SQLITE)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="multi", name="Multi",
    ))
    ds = _ds("x2g-multi-ds")
    s1 = analysis.add_sheet(Sheet(
        sheet_id=SheetId("home"), name="Home",
        title="Home", description="x",
    ))
    s1.visuals.append(KPI(
        title="A", subtitle=None, visual_id=VisualId("v-a"),
        values=[ds["amount"].sum()],
    ))
    s2 = analysis.add_sheet(Sheet(
        sheet_id=SheetId("drift"), name="Drift",
        title="Drift", description="x",
    ))
    s2.visuals.append(KPI(
        title="B", subtitle=None, visual_id=VisualId("v-b"),
        values=[ds["amount"].sum()],
    ))
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, connection_factory=sqlite_factory,
    )
    # Both visuals are reachable via the single fetcher.
    assert fetcher("v-a", {})["values"][0]["value"] == 375
    assert fetcher("v-b", {})["values"][0]["value"] == 375
