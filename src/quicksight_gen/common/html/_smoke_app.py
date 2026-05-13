"""X.2.a — smoke App2 builder + stub Money-Trail fetcher.

Lifted out of ``__main__.py`` so both the direct ``python -m`` smoke
runner and the ``quicksight-gen dashboards`` CLI can share one
implementation. X.2.a.4 will introduce a real config + L2 driven
builder; until then the stub ships the same Money-Trail-shaped tree
both entry points used.

The fetcher stays a deterministic stub responsive to ``date_from``,
``date_to`` and ``anchor`` params — proves the swap pipeline without
a database. X.2.a.4 swaps the stub for a real ``DataFetcher``
factory keyed off the L2 instance + dialect.

Showcase sheet (X.2.d / X.2.e demo)
-----------------------------------

A second sheet ``"showcase"`` exercises every filter primitive
landed in X.2.d (ParameterDropdown / CategoryFilter / NumericRange)
plus the d3 renderers from X.2.c (KPI / Table / BarChart /
LineChart). It exists so ``quicksight-gen dashboards --stub``
shows off the full feature surface — sheet tabs (X.2.e), themed
chrome (X.2.l), filter form (X.2.d), and the renderer set — in one
launch. The stub fetcher echoes filter values into the visual data
so the round-trip is visible without a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quicksight_gen.common.config import Config
from quicksight_gen.common.html.render import (
    CategoryFilterSpec,
    FilterSpec,
    NumericRangeSpec,
    ParameterDropdownSpec,
    ParameterMultiSelectSpec,
)
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import (
    BarChart,
    ForceGraph,
    KPI,
    LineChart,
    Sankey,
    Table,
)


# Filter specs surfaced on every sheet of the smoke app — the form
# is page-level, so all sheets see the same controls. Values flow
# through the URL per X.2.b's URL-as-state contract; the stub
# fetcher echoes them in visible labels so the user sees the
# round-trip succeed without a database. One of each kind so the
# smoke page doubles as the visual showcase for the X.2.l.4 widgets
# (Tom Select single + multi, noUiSlider, plus the page-level
# Flatpickr date range that every sheet gets).
SMOKE_FILTER_SPECS: tuple[FilterSpec, ...] = (
    ParameterDropdownSpec(
        name="view",
        label="View",
        options=("summary", "detail", "drill"),
    ),
    ParameterMultiSelectSpec(
        name="rails",
        label="Rails",
        options=("ach", "wire", "check", "internal", "zba"),
    ),
    CategoryFilterSpec(
        column="status",
        label="Status",
        options=("open", "closed", "pending", "failed"),
    ),
    NumericRangeSpec(
        column="amount", label="Amount", lo=0, hi=100_000, step=500,
    ),
)


def build_smoke_app(cfg: Config) -> tuple[App, Sheet]:
    """Build a two-sheet App2 with the spike's Money Trail Sankey
    plus a Showcase sheet exercising every visual primitive + filter
    primitive that landed through X.2.e.

    Stays L1-pure: persona-blind labels, no DB lookups. The cfg is
    threaded through so the App carries the same per-instance prefix
    the QuickSight builders use — once X.2.f plugs in a real
    fetcher, the same config picks the dialect / connection.

    Returns ``(app, primary_sheet)`` — the primary sheet is the one
    rendered at ``/dashboards/smoke`` (the default landing). The
    server discovers the second sheet via ``app.analysis.sheets``
    and renders the tab strip across the top.
    """
    app = App(name="x2-app2-smoke", cfg=cfg)
    analysis = app.set_analysis(
        Analysis(
            analysis_id_suffix="smoke-analysis",
            name="App2 Smoke",
        )
    )
    primary = analysis.add_sheet(
        Sheet(
            sheet_id=SheetId("money-trail"),
            name="MoneyTrail",
            title="Money Trail",
            description=(
                "Pick a date range and watch the Sankey re-hydrate via "
                "HTMX swap + d3 from the swapped fragment."
            ),
        )
    )
    primary.visuals.append(
        Sankey(
            title="Money Trail — Chain Sankey",
            subtitle=(
                "Stub data; X.2.f will wire this to the real "
                "<prefix>_inv_money_trail_edges matview."
            ),
            visual_id=VisualId("smoke-sankey"),
        )
    )
    primary.visuals.append(
        ForceGraph(
            title="Rails & Accounts — Force Layout",
            subtitle=(
                "X.4 capability test: d3-force renders an account "
                "topology like the existing graphviz pipeline does for "
                "docs. Click a node to anchor; drag-to-position is a "
                "follow-on."
            ),
            visual_id=VisualId("smoke-force"),
        )
    )

    # Second sheet — exercises every renderer + every filter primitive.
    # Click "Refresh" on any visual after changing a filter and the
    # echoed labels show the URL-as-state round-trip succeeded.
    showcase = analysis.add_sheet(
        Sheet(
            sheet_id=SheetId("showcase"),
            name="Showcase",
            title="X.2 Showcase",
            description=(
                "Every X.2 primitive in one place. Pick filter values "
                "above, hit Refresh on any visual, and the stub fetcher "
                "echoes the URL params back into the rendered data — so "
                "you can see the round-trip work without a database."
            ),
        )
    )
    # All six renderers in one place — "showcase" should mean *every*
    # primitive, so the network graphs (Sankey + ForceGraph) live here
    # too, not only on the Money Trail sheet.
    showcase.visuals.append(
        Sankey(
            title="Chain Sankey",
            subtitle=(
                "Stub Sankey; link weights seed off the current "
                "date_from / date_to so a date change reshapes it."
            ),
            visual_id=VisualId("showcase-sankey"),
        )
    )
    showcase.visuals.append(
        ForceGraph(
            title="Account Topology — Force Layout",
            subtitle=(
                "Stub d3-force graph (accounts as nodes, rails as edges). "
                "Click a node to anchor it."
            ),
            visual_id=VisualId("showcase-force"),
        )
    )
    showcase.visuals.append(
        KPI(
            title="Open Exceptions",
            subtitle=(
                "Stub KPI; the value is derived from the current filter "
                "params so changing a filter visibly shifts the number."
            ),
            visual_id=VisualId("showcase-kpi"),
        )
    )
    showcase.visuals.append(
        BarChart(
            title="Activity by Status",
            subtitle=(
                "Stub bar chart; bars echo whichever statuses are checked "
                "in the Status filter (or all four when nothing's checked)."
            ),
            visual_id=VisualId("showcase-bar"),
        )
    )
    showcase.visuals.append(
        LineChart(
            title="Daily Volume",
            subtitle=(
                "Stub line chart; the y-axis multiplier seeds off the "
                "current date_from / date_to values."
            ),
            visual_id=VisualId("showcase-line"),
        )
    )
    showcase.visuals.append(
        Table(
            title="Account Balances",
            subtitle=(
                "Stub table — 25 rows, 10 per page. Click a column header "
                "to sort (▲/▼ cycles asc → desc → off); use the pager at "
                "the bottom. Demonstrates the sortable-header + pagination "
                "round-trip (each click refetches with sort_column / "
                "page_offset query params)."
            ),
            visual_id=VisualId("showcase-table"),
        )
    )
    return app, primary


def _stub_rails_accounts() -> dict[str, Any]:
    """Stub topology shaped like ``common/l2/topology.py`` projects.

    Accounts as nodes (typed by ``account_type``), rails as undirected
    edges between them. Persona-blind labels — the X.2.f real
    fetcher pulls names from the L2 instance's persona block.
    """
    return {
        "nodes": [
            {"id": "ext_acquirer",      "label": "External Acquirer",      "group": "external_counter"},
            {"id": "customer_dda_a",    "label": "Customer DDA A",         "group": "dda"},
            {"id": "customer_dda_b",    "label": "Customer DDA B",         "group": "dda"},
            {"id": "merchant_dda",      "label": "Merchant DDA",           "group": "merchant_dda"},
            {"id": "gl_control",        "label": "GL Control",             "group": "gl_control"},
            {"id": "concentration",     "label": "Concentration Master",   "group": "concentration_master"},
            {"id": "funds_pool",        "label": "Funds Pool",             "group": "funds_pool"},
        ],
        "links": [
            {"source": "ext_acquirer",   "target": "customer_dda_a"},
            {"source": "ext_acquirer",   "target": "customer_dda_b"},
            {"source": "customer_dda_a", "target": "merchant_dda"},
            {"source": "customer_dda_b", "target": "merchant_dda"},
            {"source": "merchant_dda",   "target": "gl_control"},
            {"source": "gl_control",     "target": "concentration"},
            {"source": "concentration",  "target": "funds_pool"},
            {"source": "customer_dda_a", "target": "gl_control"},
        ],
    }


def _showcase_kpi(params: dict[str, str]) -> dict[str, Any]:
    """Stub KPI. Value is derived from the param state so the user
    can see filter changes flow through to the visual."""
    base = 47
    bonus = sum(ord(c) for c in params.get("param_view", "")) % 50
    delta_seed = sum(ord(c) for c in params.get("filter_status", ""))
    return {
        "values": [
            {
                "value": base + bonus,
                "label": "Open Exceptions",
                "format": "number",
                "delta": (delta_seed % 21) - 10,
            },
        ],
    }


def _showcase_bar(params: dict[str, str]) -> dict[str, Any]:
    """Stub bar chart. Categories track the filter_status URL key —
    if the user checks specific statuses, only those bars render
    (with stable seeded heights). Empty filter shows all four."""
    selected = params.get("filter_status", "")
    if selected:
        cats = [c.strip() for c in selected.split(",") if c.strip()]
    else:
        cats = ["open", "closed", "pending", "failed"]
    return {
        "categories": cats,
        "values": [(sum(ord(c) for c in cat) * 7) % 100 + 5 for cat in cats],
        "x_label": "Status",
        "y_label": "Count",
    }


def _showcase_line(params: dict[str, str]) -> dict[str, Any]:
    """Stub line chart. Series amplitude seeds off date_from + date_to
    so a date change visibly reshapes the curve. Output shape matches
    renderLineChart / _data_shape.shape_line_chart: parallel
    ``x_values`` + ``series[].values`` (index-aligned), NOT ``points``.
    """
    seed = sum(
        ord(c) for c in (params.get("date_from", "") + params.get("date_to", ""))
    ) or 13
    x_values = [f"2030-01-{i + 1:02d}" for i in range(7)]
    values = [(seed * (i + 1) * 3) % 80 + 20 for i in range(7)]
    return {
        "x_values": x_values,
        "series": [{"name": "Daily volume", "values": values}],
        "x_label": "Date",
        "y_label": "Volume",
    }


_SHOWCASE_TABLE_COLUMNS = ["account_id", "account_name", "balance", "status"]
_SHOWCASE_TABLE_STATUSES = ("open", "closed", "pending", "failed")


def _showcase_table_rows() -> list[list[str]]:
    """25 deterministic rows — enough to demo pagination (10/page) +
    sortable headers. Persona-blind sample data."""
    rows: list[list[str]] = []
    for i in range(1, 26):
        # Pseudo-random-but-deterministic balance so a sort visibly
        # reorders (not already in account_id order).
        bal = ((i * 37 + 11) % 100) * 1000 + (i * 7) % 1000
        rows.append([
            f"acct-{i:03d}",
            f"Account {chr(64 + (i % 26 or 26))}-{i}",
            f"{bal:.2f}",
            _SHOWCASE_TABLE_STATUSES[i % len(_SHOWCASE_TABLE_STATUSES)],
        ])
    return rows


def _showcase_demo_table(params: dict[str, str]) -> dict[str, Any]:
    """Stub table demoing the Table renderer's sortable headers +
    pagination. Honors the ``sort_column`` (``"<col>:asc"`` /
    ``"<col>:desc"``), ``page_offset``, and ``page_size`` query params
    the renderer sends on a header / pager click, so the demo is
    interactive — not a static snapshot.
    """
    rows: list[list[str]] = _showcase_table_rows()
    total = len(rows)

    sort_column = params.get("sort_column", "")
    col_name, _, direction = sort_column.partition(":")
    if col_name in _SHOWCASE_TABLE_COLUMNS and direction in ("asc", "desc"):
        ci = _SHOWCASE_TABLE_COLUMNS.index(col_name)
        desc = direction == "desc"
        if col_name == "balance":
            rows = sorted(rows, key=lambda r: float(r[ci]), reverse=desc)
        else:
            rows = sorted(rows, key=lambda r: r[ci], reverse=desc)

    def _int(key: str, default: int) -> int:
        raw = params.get(key, "")
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    page_size = max(1, _int("page_size", 10))
    page_offset = max(0, _int("page_offset", 0))
    page = rows[page_offset:page_offset + page_size]
    return {
        # renderTable wants column *objects* ({name, label?, format?}),
        # matching _data_shape.shape_table — the header label comes from
        # col.name.
        "columns": [{"name": c} for c in _SHOWCASE_TABLE_COLUMNS],
        "rows": page,
        "page_offset": page_offset,
        "page_size": page_size,
        "total_rows": total,
        # Echo the sort back so the header badge (▲/▼) renders.
        "sort_column": sort_column,
    }


def stub_money_trail_fetcher(
    visual_id: str, params_multi: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Deterministic stub responsive to filter params.

    Three groups of visual_ids:

    - **smoke-sankey** — the spike's Money Trail Sankey. Reacts to
      date_from / date_to (link multipliers seed off them) and
      ``anchor`` (clicked node, applies a per-link factor). Labels
      echo the seed + anchor so a glance confirms the round-trip.
    - **smoke-force** — the rails+accounts topology. Static today.
    - **showcase-***  — the X.2 showcase sheet's KPI / Table /
      BarChart / LineChart. Each one echoes the relevant URL params
      back into its data so the new filter primitives (X.2.d) and
      sheet structure (X.2.e) are visibly working without needing
      a real database.

    Both seed and anchor are echoed into the first node label so a
    glance at any swap confirms the round-trip ran with the
    expected params (decouples "did the swap fire?" from "did the
    Sankey shape change?").
    """
    # Collapse the URL multi-dict to scalar last-values — the stub
    # only reads single-valued filters.
    params: dict[str, str] = {k: v[-1] for k, v in params_multi.items() if v}
    if visual_id in ("smoke-force", "showcase-force"):
        return _stub_rails_accounts()
    if visual_id == "showcase-kpi":
        return _showcase_kpi(params)
    if visual_id == "showcase-bar":
        return _showcase_bar(params)
    if visual_id == "showcase-line":
        return _showcase_line(params)
    if visual_id == "showcase-table":
        return _showcase_demo_table(params)
    # smoke-sankey + showcase-sankey both fall through to the
    # date-seeded Money-Trail Sankey below.
    seed = sum(
        ord(c)
        for c in (params.get("date_from", "") + params.get("date_to", ""))
    )
    anchor = params.get("anchor", "")
    anchor_factor = (sum(ord(c) for c in anchor) % 5 + 1) if anchor else 1
    label = f"seed={seed}, anchor={anchor or 'none'}"
    return {
        "nodes": [
            {"name": f"External Acquirer ({label})"},
            {"name": "Customer DDA"},
            {"name": "GL Control"},
            {"name": "Concentration"},
            {"name": "Funds Pool"},
        ],
        "links": [
            {"source": 0, "target": 1,
             "value": max(10, (seed * 7 * anchor_factor) % 100 + 10)},
            {"source": 1, "target": 2,
             "value": max(10, (seed * 11 * anchor_factor) % 100 + 10)},
            {"source": 2, "target": 3,
             "value": max(10, (seed * 13 * anchor_factor) % 100 + 10)},
            {"source": 3, "target": 4,
             "value": max(10, (seed * 17 * anchor_factor) % 100 + 10)},
        ],
    }
