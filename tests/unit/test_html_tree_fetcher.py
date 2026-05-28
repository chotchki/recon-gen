"""X.2.g.0 — generic per-tree DataFetcher tests.

Verifies the build-time visual indexing + the request-time
SQL-execute → shape pipeline. Uses an aiosqlite in-memory pool
fixture so tests run without psycopg / oracledb. The SQL registry
is populated via a tiny test-helper that mimics what
``build_dataset()`` does in production.

X.2.n.4: ``make_tree_db_fetcher`` now takes an
``AsyncConnectionPool`` and returns an async fetcher. Tests await
the fetcher directly via ``asyncio.run`` per call so the test
shape stays familiar.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from recon_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    Storage,
    register_contract,
    register_sql,
)
from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
from recon_gen.common.html._tree_fetcher import (
    _apply_cents_to_dollars,
    _find_visual_dataset_identifier,
    make_tree_db_fetcher,
)
from recon_gen.common.ids import SheetId, VisualId
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.tree.datasets import Dataset
from recon_gen.common.tree.structure import Analysis, App, Sheet
from recon_gen.common.tree.visuals import KPI, BarChart, Sankey, Table
from tests._test_helpers import make_test_config


_TEST_CFG = make_test_config()
_TEST_CFG_SQLITE = make_test_config(dialect=Dialect.SQLITE)


# The producer's ``DataFetcher`` is typed ``Callable[..., Awaitable[Any]]``,
# but ``asyncio.run`` needs ``Coroutine``. The fetcher IS an ``async def``
# at runtime — bridge through this thin coroutine helper so the test calls
# stay legible.
def _run_fetcher(
    fetcher: Any, visual_id: VisualId, params: Mapping[str, list[str]],
) -> dict[str, Any]:
    async def _go() -> dict[str, Any]:
        result: dict[str, Any] = await fetcher(visual_id, params)
        return result
    return asyncio.run(_go())


# Register contracts ONCE at module level — register_contract raises
# on a second call with a different DatasetContract instance for the
# same identifier, so each test creating a fresh contract would
# collide. The contracts here describe the shared SQLite fixture.
_X2G_TEST_CONTRACT = DatasetContract(columns=[
    ColumnSpec(name="status", type="STRING"),
    # BH.24.1 — `amount` is raw BIGINT cents (the aiosqlite fixture
    # seeds [100, 50, 200, 25] in cents per AO.1.impl). Renderers
    # consult ``storage=CENTS`` to decide whether to apply the /100
    # cents→dollars divide.
    ColumnSpec(
        name="amount", type="INTEGER",
        currency=True, storage=Storage.CENTS,
    ),
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
register_contract("x2g-sankey-ds", DatasetContract(columns=[
    ColumnSpec(name="source", type="STRING"),
    ColumnSpec(name="target", type="STRING"),
    ColumnSpec(name="amount", type="INTEGER"),
]))


# ---------------------------------------------------------------------------
# _find_visual_dataset_identifier — walks Visual → Dim/Measure → Dataset.id
# ---------------------------------------------------------------------------


def _ds(identifier: str = "test-ds") -> Dataset:
    return Dataset(identifier=identifier, arn=f"arn::{identifier}")


def test_find_dataset_id_via_kpi_values() -> None:
    ds = _ds("kpi-ds")
    visual = KPI(
        title="Open", subtitle="t", visual_id=VisualId("v-k"),
        values=[ds["count"].sum()],
    )
    assert _find_visual_dataset_identifier(visual) == "kpi-ds"


def test_find_dataset_id_via_bar_chart_category() -> None:
    ds = _ds("bar-ds")
    visual = BarChart(
        title="By status", subtitle="t", visual_id=VisualId("v-b"),
        category=[ds["status"].dim()],
        values=[ds["amount"].sum()],
    )
    assert _find_visual_dataset_identifier(visual) == "bar-ds"


def test_find_dataset_id_returns_none_for_visual_without_fields() -> None:
    """KPI with no measures / no fields → no dataset → None.
    Fetcher should treat this as "return empty payload"."""
    visual = KPI(
        title="Empty", subtitle="t", visual_id=VisualId("v-empty"),
    )
    assert _find_visual_dataset_identifier(visual) is None


# ---------------------------------------------------------------------------
# make_tree_db_fetcher — build-time indexing + request-time dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def aiosqlite_pool() -> Iterator[AsyncConnectionPool]:
    """File-backed temp SQLite seeded with a tiny test table.

    aiosqlite + ``:memory:`` would give each connection a fresh
    isolated DB (no shared state across the pool). Using a tempfile
    means every acquire sees the same seeded data, mirroring
    production semantics. Cleanup tears down the file at fixture
    exit.
    """
    import sqlite3
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    # Seed synchronously via stdlib sqlite3 — much simpler than
    # async setup and the fixture is sync.
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (status TEXT, amount INTEGER)")
    conn.executemany(
        "INSERT INTO t VALUES (?, ?)",
        [("open", 100), ("open", 50), ("closed", 200), ("pending", 25)],
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(
        dialect=Dialect.SQLITE,
        demo_database_url=path,
    )
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        yield pool
    finally:
        asyncio.run(pool.close())
        os.unlink(path)


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
            title="Total", subtitle="t", visual_id=VisualId("v-kpi"),
            values=[ds["amount"].sum()],
        ),
    )
    sheet.visuals.append(
        BarChart(
            title="By status", subtitle="t", visual_id=VisualId("v-bar"),
            category=[ds["status"].dim()],
            values=[ds["amount"].sum()],
        ),
    )
    sheet.visuals.append(
        Table(
            title="Rows", subtitle="t", visual_id=VisualId("v-tbl"),
            columns=[ds["status"].dim(), ds["amount"].dim()],
        ),
    )
    return app, ds


def test_make_tree_db_fetcher_dispatches_kpi(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    # X.2.g.1.c — dataset SQL is row-grain; the wrapper applies
    # SUM(amount) per the KPI's measure declaration.
    register_sql(
        "x2g-test-ds", "SELECT status, amount FROM t",
    )
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-kpi"), {})
    # KPI shape: {values: [{value, ...}]}
    assert "values" in out
    assert out["values"][0]["value"] == 375  # 100+50+200+25


def test_make_tree_db_fetcher_dispatches_bar_chart(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    # X.2.g.1.c — dataset SQL is row-grain; the wrapper produces
    # SELECT status, SUM(amount) FROM (...) GROUP BY status.
    register_sql(
        "x2g-test-ds", "SELECT status, amount FROM t",
    )
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-bar"), {})
    # BarChart shape: {categories, values, x_label, y_label}
    assert out["categories"] == ["closed", "open", "pending"]
    assert out["values"] == [200, 150, 25]


def test_make_tree_db_fetcher_paginates_table(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """X.2.g.5.followon — Table visuals page SERVER-side. The renderer
    sends ``?page_offset=N&page_size=M``; the fetcher LIMIT/OFFSETs the
    query and returns the full ``total_rows`` via ``COUNT(*) OVER ()``.
    A 68k-row table must NOT come back as 68k rows in one fragment
    (that froze the browser). The COUNT(*) column is stripped from the
    shaped output."""
    register_sql("x2g-test-ds", "SELECT status, amount FROM t")  # 4 rows
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    # No params → first page; default size > 4 ⇒ all 4 rows back.
    out = _run_fetcher(fetcher, VisualId("v-tbl"), {})
    assert len(out["rows"]) == 4
    assert out["total_rows"] == 4
    assert out["page_offset"] == 0
    # The COUNT(*) OVER () column didn't leak into columns/rows.
    assert [c["name"] for c in out["columns"]] == ["status", "amount"]
    assert all(len(r) == 2 for r in out["rows"])
    # Page 2 of size 2 ⇒ 2 rows, but total_rows stays 4.
    out2 = _run_fetcher(
        fetcher, VisualId("v-tbl"), {"page_size": ["2"], "page_offset": ["2"]},
    )
    assert len(out2["rows"]) == 2
    assert out2["total_rows"] == 4
    assert out2["page_offset"] == 2
    assert out2["page_size"] == 2
    # A crafted huge page_size is clamped (no OOM).
    out3 = _run_fetcher(fetcher, VisualId("v-tbl"), {"page_size": ["99999999999"]})
    assert out3["page_size"] == 10_000  # _TABLE_PAGE_SIZE_MAX
    assert len(out3["rows"]) == 4  # only 4 rows exist


def test_make_tree_db_fetcher_sorts_table(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """X.2.h.5 — the renderer sends ``sort_column=<col>:<asc|desc>`` on a
    header click; the fetcher applies ``ORDER BY <col> [DESC], 1`` and
    echoes the *resolved* sort back. A garbage / injection ``sort_column``
    falls back to ``ORDER BY 1`` and echoes ``""`` (and doesn't run the
    garbage)."""
    register_sql("x2g-test-ds", "SELECT status, amount FROM t")
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    desc = _run_fetcher(fetcher, VisualId("v-tbl"), {"sort_column": ["amount:desc"]})
    assert [r[1] for r in desc["rows"]] == [200, 100, 50, 25]
    assert desc["sort_column"] == "amount:desc"
    asc = _run_fetcher(fetcher, VisualId("v-tbl"), {"sort_column": ["amount:asc"]})
    assert [r[1] for r in asc["rows"]] == [25, 50, 100, 200]
    assert asc["sort_column"] == "amount:asc"
    # Injection attempt → bare-identifier guard rejects it → ORDER BY 1
    # (status asc), echo "" — and the DROP never reaches the DB.
    safe = _run_fetcher(
        fetcher, VisualId("v-tbl"), {"sort_column": ["amount); DROP TABLE t; --:desc"]},
    )
    assert safe["sort_column"] == ""
    assert [r[0] for r in safe["rows"]] == ["closed", "open", "open", "pending"]
    # Sanity: table still there.
    again = _run_fetcher(fetcher, VisualId("v-tbl"), {})
    assert again["total_rows"] == 4


def test_make_tree_db_fetcher_substitutes_filters(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """URL params with names referenced in the dataset SQL flow
    through to the bind dict (proves end-to-end X.2.d → X.2.f →
    X.2.g.0 round-trip)."""
    register_sql(
        "x2g-test-ds",
        "SELECT status, amount FROM t WHERE status = :param_status",
    )
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-kpi"), {"param_status": ["open"]})
    assert out["values"][0]["value"] == 150  # only "open" rows


def test_make_tree_db_fetcher_unknown_visual_id_returns_empty(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """Stale URLs (cached pages, swap-after-restart) shouldn't
    crash — empty payload renders an empty visual."""
    register_sql("x2g-test-ds", "SELECT status, amount FROM t")
    app, _ds_node = _build_app_with_visuals()
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-does-not-exist"), {})
    assert out == {}


def test_make_tree_db_fetcher_visual_without_dataset_returns_empty(
    aiosqlite_pool: AsyncConnectionPool,
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
        KPI(title="Empty", subtitle="t", visual_id=VisualId("v-x")),
    )
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-x"), {})
    assert out == {}


def test_make_tree_db_fetcher_fails_loudly_on_missing_sql(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
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
        KPI(title="X", subtitle="t", visual_id=VisualId("v-x"),
            values=[ds["a"].sum()]),
    )
    with pytest.raises(KeyError, match="No SQL registered"):
        make_tree_db_fetcher(
            app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
        )


def test_make_tree_db_fetcher_indexes_visuals_across_sheets(
    aiosqlite_pool: AsyncConnectionPool,
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
        title="A", subtitle="t", visual_id=VisualId("v-a"),
        values=[ds["amount"].sum()],
    ))
    s2 = analysis.add_sheet(Sheet(
        sheet_id=SheetId("drift"), name="Drift",  # typing-smell: ignore[no-inline-production-constants]: synthetic test-fixture sheet name; "Drift" coincides with L1 _DRIFT_NAME but is unrelated — this test exercises the fetcher's multi-sheet plumbing
        title="Drift", description="x",  # typing-smell: ignore[no-inline-production-constants]: synthetic test-fixture sheet title (mirrors name above)
    ))
    s2.visuals.append(KPI(
        title="B", subtitle="t", visual_id=VisualId("v-b"),
        values=[ds["amount"].sum()],
    ))
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    # Both visuals are reachable via the single fetcher.
    assert _run_fetcher(fetcher, VisualId("v-a"), {})["values"][0]["value"] == 375
    assert _run_fetcher(fetcher, VisualId("v-b"), {})["values"][0]["value"] == 375


# ---------------------------------------------------------------------------
# X.2.g.2.b — Sankey wrap: SELECT source, target, SUM(weight) GROUP BY ...
# ---------------------------------------------------------------------------


@pytest.fixture
def aiosqlite_sankey_pool() -> Iterator[AsyncConnectionPool]:
    """Tiny edges table for Sankey tests.

    Multiple rows per (source, target) pair so the GROUP BY actually
    has work to do — the wrap should sum them; otherwise the per-pair
    aggregation step is a silent no-op.
    """
    import sqlite3
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE edges (source TEXT, target TEXT, amount INTEGER)",
    )
    conn.executemany(
        "INSERT INTO edges VALUES (?, ?, ?)",
        [
            # (A → B) appears twice; should aggregate to 30.
            ("A", "B", 10),
            ("A", "B", 20),
            # (B → C) once.
            ("B", "C", 5),
            # (A → C) once.
            ("A", "C", 100),
        ],
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(
        dialect=Dialect.SQLITE,
        demo_database_url=path,
    )
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        yield pool
    finally:
        asyncio.run(pool.close())
        os.unlink(path)


def test_make_tree_db_fetcher_dispatches_sankey_with_aggregation(
    aiosqlite_sankey_pool: AsyncConnectionPool,
) -> None:
    """X.2.g.2.b — Sankey wrap projects (source, target, SUM(weight))
    + GROUP BY (source, target). Without the wrap the raw matview
    rows land in shape_sankey with the wrong column order; with it
    the d3-sankey ribbons render correctly.

    Asserts the (A → B) pair aggregates 10+20=30 (proves GROUP BY
    fired), the (B → C) and (A → C) pairs come through with their
    single-row weights, and shape_sankey's first-seen node ordering
    holds (A=0, B=1, C=2).
    """
    register_sql(
        "x2g-sankey-ds", "SELECT source, target, amount FROM edges",
    )
    app = App(name="x2g-sankey", cfg=_TEST_CFG_SQLITE)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="sankey", name="Sankey",
    ))
    ds = _ds("x2g-sankey-ds")
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("sk"), name="Sk",
        title="Sk", description="x",
    ))
    sheet.visuals.append(Sankey(
        title="Flow", subtitle="t", visual_id=VisualId("v-sk"),
        source=ds["source"].dim(),
        target=ds["target"].dim(),
        weight=ds["amount"].sum(),
    ))
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_sankey_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-sk"), {})
    # Sankey shape: {nodes, links}.
    assert "nodes" in out and "links" in out
    names = [n["name"] for n in out["nodes"]]
    # First-seen node ordering: A from row 0, B from row 0, C from row 2.
    assert names == ["A", "B", "C"]
    # Index links by (source_idx, target_idx) for assertion stability —
    # link order is dict-insertion which depends on aggregation key
    # iteration, but the per-pair values are deterministic.
    by_pair = {
        (link["source"], link["target"]): link["value"]
        for link in out["links"]
    }
    assert by_pair[(0, 1)] == 30  # A → B aggregated 10 + 20
    assert by_pair[(1, 2)] == 5   # B → C
    assert by_pair[(0, 2)] == 100 # A → C


def test_make_tree_db_fetcher_sankey_passthrough_without_fields(
    aiosqlite_sankey_pool: AsyncConnectionPool,
) -> None:
    """X.2.g.2.b — Sankey with missing field-well declarations falls
    through to passthrough. Defensive: a tree that constructs a
    Sankey without source/target/weight (e.g. mid-build, or via
    a future factory bug) should land empty rows in shape_sankey
    rather than producing malformed SQL."""
    register_sql(
        "x2g-sankey-ds", "SELECT source, target, amount FROM edges",
    )
    app = App(name="x2g-sankey-empty", cfg=_TEST_CFG_SQLITE)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="sankey-empty", name="Sankey Empty",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("ske"), name="Ske",
        title="Ske", description="x",
    ))
    # Sankey with a dataset reference (so it lands in the index) but
    # no field wells. Reference the dataset via an action's source
    # field is not available; use a Dim hand-set with no fields.
    # The simpler path: empty Sankey has no dataset_identifier so it
    # ends up in the visual_index with sql=None — same code path as
    # text boxes — which returns empty payload. Verify that.
    sheet.visuals.append(Sankey(
        title="Empty", subtitle="t", visual_id=VisualId("v-ske"),
    ))
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_sankey_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-ske"), {})
    # No fields → no dataset detected → empty payload.
    assert out == {}


# ---------------------------------------------------------------------------
# _apply_cents_to_dollars — AO.1.impl (Studio slice) row-side conversion
# ---------------------------------------------------------------------------


def test_apply_cents_to_dollars_converts_named_columns() -> None:
    """Columns named in ``money_columns`` are coerced via Cents.from_db /
    to_dollars; non-money columns pass through unchanged."""
    rows = [("a", 7500, "x"), ("b", -125_000, "y")]
    out = _apply_cents_to_dollars(
        rows, ["name", "drift", "tag"], frozenset({"drift"}),
    )
    assert out[0] == ("a", 75.0, "x")  # 7500 cents → $75.00
    assert out[1] == ("b", -1250.0, "y")  # -125000 cents → -$1,250.00


def test_apply_cents_to_dollars_case_insensitive_oracle_match() -> None:
    """Oracle's driver returns column names uppercased; the conversion
    matches case-insensitively so an Oracle Studio /dashboards/ render
    still drops the cents to dollars."""
    rows = [(7500, "x")]
    out = _apply_cents_to_dollars(
        rows, ["DRIFT", "TAG"], frozenset({"drift"}),
    )
    assert out[0] == (75.0, "x")


def test_apply_cents_to_dollars_none_passes_through() -> None:
    """NULL money columns stay NULL (e.g., expected_eod_balance can be
    NULL when no EOD invariant is set on the account)."""
    rows = [("a", None), ("b", 0)]
    out = _apply_cents_to_dollars(
        rows, ["name", "money"], frozenset({"money"}),
    )
    assert out[0] == ("a", None)
    assert out[1] == ("b", 0.0)  # 0 cents → $0.00


def test_apply_cents_to_dollars_no_money_columns_is_noop() -> None:
    """Visual without any currency fields → original rows returned (same
    list, no copy)."""
    rows = [("a", 7500)]
    out = _apply_cents_to_dollars(rows, ["name", "drift"], frozenset())
    assert out is rows  # same reference — short-circuit path


def test_apply_cents_to_dollars_unknown_money_column_is_noop() -> None:
    """money_columns names a column that doesn't appear in the result
    set → no conversion happens, no errors raised."""
    rows = [("a", 7500)]
    out = _apply_cents_to_dollars(
        rows, ["name", "amount"], frozenset({"drift"}),
    )
    assert out is rows


def test_table_fetcher_converts_currency_columns_to_dollars(
    aiosqlite_pool: AsyncConnectionPool,
) -> None:
    """End-to-end: a Table visual with a currency-flagged Dim returns
    rows projected as dollars (cents / 100.0), not raw cents. Mirrors
    the L1 drift table where ``drift`` is stored as BIGINT cents per
    AO.1; the dashboards panel must show ``$75.00`` not ``$7,500.00``.
    """
    register_sql("x2g-test-ds", "SELECT status, amount FROM t")
    app = App(name="x2g-currency-test", cfg=_TEST_CFG_SQLITE)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="x2g-currency-test-analysis",
        name="X.2.g Currency Test",
    ))
    ds = _ds("x2g-test-ds")
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("s-cur"), name="SCur",
        title="Currency Sheet", description="x",
    ))
    sheet.visuals.append(
        Table(
            title="Money rows", subtitle="t",
            visual_id=VisualId("v-tbl-cur"),
            columns=[ds["status"].dim(), ds["amount"].numerical(currency=True)],
        ),
    )
    fetcher = make_tree_db_fetcher(
        app, _TEST_CFG_SQLITE, pool=aiosqlite_pool,
    )
    out = _run_fetcher(fetcher, VisualId("v-tbl-cur"), {})
    # The aiosqlite fixture seeds amount values [100, 50, 200, 25] in
    # cents (BIGINT per AO.1). The currency-marked column converts to
    # dollars in the row output: 100 cents → $1.00, etc.
    cents_to_dollars = {100: 1.0, 50: 0.5, 200: 2.0, 25: 0.25}
    amount_col_idx = 1
    for row in out["rows"]:
        # row[0] is status, row[1] is amount (already converted)
        amount = row[amount_col_idx]
        # Source row's amount value is one of the seeded cents values;
        # after conversion it lands as the matching dollar float.
        assert amount in cents_to_dollars.values(), (
            f"amount {amount} not in {cents_to_dollars.values()}"
        )
    assert out["columns"][amount_col_idx].get("format") == "currency"


def test_apply_cents_to_dollars_pre_converted_values_pass_through() -> None:
    """Defensive: a value that's already a float / string (double-convert
    path, or SQLite TEXT-affinity fallback) survives without raising —
    int() conversion fails gracefully and the value stays as-is."""
    rows = [("a", "not-a-number"), ("b", 75.0)]
    out = _apply_cents_to_dollars(
        rows, ["name", "money"], frozenset({"money"}),
    )
    # int("not-a-number") raises ValueError → caught, value preserved.
    assert out[0] == ("a", "not-a-number")
    # int(75.0) succeeds → reconverts (treats 75 as 75 cents = $0.75).
    # This is a known limitation: callers must not double-convert.
    assert out[1] == ("b", 0.75)
