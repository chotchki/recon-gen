"""X.2.e — sheet structure + cross-sheet/cross-app navigation tests.

This phase ships sheet tabs at the top of each dashboard plus a new
``/dashboards/:d/sheets/:s`` route so each analysis sheet is
addressable. Cross-sheet drill rendering (per-row anchors derived
from the tree's ``Drill`` primitive) is left to a follow-on
(``X.2.e.2``) — it requires touching the d3 renderers per visual
kind, which is more involved than the tab/route plumbing.

Tab strip + sheet route are the smallest meaningful ship:

- Plain ``<a href>`` per sheet (no HTMX swap — full page reload is
  cheap once Tailwind/HTMX/d3 are cached, and URL stays trivially
  bookmarkable).
- The active sheet's tab carries the accent background.
- Single-sheet analyses get an empty tab strip (suppressed).
- Visual-data fetch accepts any analysis sheet's id, not just the
  served (default landing) sheet's.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from quicksight_gen.common.html import emit_html
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import KPI
from tests._test_helpers import make_test_config


_TEST_CFG = make_test_config()


def _build_app_with_sheets(sheet_specs: list[tuple[str, str]]) -> tuple[App, list[Sheet]]:
    """Build an App with multiple sheets attached to its analysis.

    ``sheet_specs`` is a list of ``(sheet_id, name)`` tuples.
    """
    app = App(name="nav-test", cfg=_TEST_CFG)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="nav-test-analysis",
        name="Nav Test",
    ))
    sheets: list[Sheet] = []
    for sid, name in sheet_specs:
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId(sid),
            name=name,
            title=f"{name} title",
            description="x",
        ))
        sheet.visuals.append(
            KPI(title=f"K-{sid}", subtitle="t", visual_id=VisualId(f"v-{sid}")),
        )
        sheets.append(sheet)
    return app, sheets


# ---------------------------------------------------------------------------
# emit_html with all_sheets
# ---------------------------------------------------------------------------


def test_emit_html_renders_no_tabs_when_all_sheets_omitted() -> None:
    """Default ``all_sheets=()`` keeps the existing single-sheet
    behavior — no *tab strip*, callers that haven't migrated stay green.

    The page still emits the global ``← Dashboards`` back-link nav
    (X.4.j post-merge, see test_html_render.py); this test only cares
    that the per-sheet tab nav doesn't appear when ``all_sheets`` is
    empty."""
    app, sheets = _build_app_with_sheets([("s1", "Solo")])
    out = emit_html(app, sheets[0], dashboard_id="x")
    # Tab links carry the unique ``rounded-t-md`` class (per
    # _render_sheet_tabs); nothing else on the page uses it.
    assert "rounded-t-md" not in out


def test_emit_html_renders_no_tabs_for_single_sheet_analysis() -> None:
    """Single-sheet dashboards don't need a tab strip — suppressing
    it keeps the page chrome cleaner. (Same suppression even when
    ``all_sheets`` is explicitly passed.)"""
    app, sheets = _build_app_with_sheets([("s1", "Solo")])
    out = emit_html(
        app, sheets[0], dashboard_id="x", all_sheets=sheets,
    )
    # Tab links carry the unique ``rounded-t-md`` class (per
    # _render_sheet_tabs); nothing else on the page uses it. Absence
    # proves the tab strip didn't render. The always-on ``← Dashboards``
    # back-link nav uses different classes, so it doesn't trip this.
    assert "rounded-t-md" not in out


def test_emit_html_renders_tab_per_sheet_when_multiple() -> None:
    app, sheets = _build_app_with_sheets([
        ("home", "Home"), ("drift", "Drift"), ("audit", "Audit"),
    ])
    out = emit_html(
        app, sheets[0], dashboard_id="x", all_sheets=sheets,
    )
    # Each sheet name appears as a tab (also appears as the rendered
    # sheet's title for the active one — count >= 1 each).
    assert ">Home</a>" in out
    assert ">Drift</a>" in out
    assert ">Audit</a>" in out


def test_emit_html_active_tab_uses_accent_background() -> None:
    """The active sheet's tab carries ``bg-accent`` — the user can
    see where they are in the tab strip."""
    app, sheets = _build_app_with_sheets([
        ("home", "Home"), ("drift", "Drift"),
    ])
    out = emit_html(
        app, sheets[1], dashboard_id="x", all_sheets=sheets,
    )
    # Find the Drift tab's anchor — it should carry bg-accent.
    drift_tab_idx = out.index('>Drift</a>')
    # Walk back to find the <a tag's start.
    a_start = out.rindex('<a ', 0, drift_tab_idx)
    drift_anchor = out[a_start:drift_tab_idx]
    assert 'bg-accent' in drift_anchor

    home_tab_idx = out.index('>Home</a>')
    a_start_home = out.rindex('<a ', 0, home_tab_idx)
    home_anchor = out[a_start_home:home_tab_idx]
    # The inactive tab should NOT carry bg-accent.
    assert 'bg-accent' not in home_anchor


def test_emit_html_tab_hrefs_use_sheets_route() -> None:
    """Tab anchors target ``/dashboards/:d/sheets/:s`` so they round-
    trip via the new sheet_view route."""
    app, sheets = _build_app_with_sheets([
        ("home", "Home"), ("drift", "Drift"),
    ])
    out = emit_html(
        app, sheets[0], dashboard_id="L1-dash", all_sheets=sheets,
    )
    assert 'href="/dashboards/L1-dash/sheets/home"' in out
    assert 'href="/dashboards/L1-dash/sheets/drift"' in out


def test_emit_html_tabs_appear_above_filter_form() -> None:
    """Tabs should sit above the filter form so the user picks the
    sheet first, then narrows by filter."""
    app, sheets = _build_app_with_sheets([
        ("home", "Home"), ("drift", "Drift"),
    ])
    out = emit_html(
        app, sheets[0], dashboard_id="x", all_sheets=sheets,
    )
    nav_pos = out.index("<nav")
    form_pos = out.index('id="filter-form"')
    assert nav_pos < form_pos


# ---------------------------------------------------------------------------
# Server end-to-end — sheet route + multi-sheet wiring
# ---------------------------------------------------------------------------


def test_dashboard_view_renders_tabs_when_analysis_has_multiple_sheets() -> None:
    """The default landing page (``/dashboards/:d``) shows the tab
    strip when its analysis has more than one sheet."""
    app, sheets = _build_app_with_sheets([
        ("home", "Home"), ("drift", "Drift"),
    ])
    asgi = make_app(dashboards={
        "L1": ServedDashboard(
            tree_app=app, sheet=sheets[0],
            title="L1", data_fetcher=lambda _v, _p: {},
        ),
    })
    client = TestClient(asgi)
    out = client.get("/dashboards/L1").text
    assert ">Home</a>" in out
    assert ">Drift</a>" in out


def test_sheet_view_renders_a_specific_sheet() -> None:
    app, sheets = _build_app_with_sheets([
        ("home", "Home"), ("drift", "Drift"),
    ])
    asgi = make_app(dashboards={
        "L1": ServedDashboard(
            tree_app=app, sheet=sheets[0],
            title="L1", data_fetcher=lambda _v, _p: {},
        ),
    })
    client = TestClient(asgi)
    out = client.get("/dashboards/L1/sheets/drift").text
    # The Drift sheet's title appears (proves it's the active sheet).
    assert "Drift title" in out
    # Tab strip carries Drift as active (bg-accent).
    drift_tab_idx = out.index('>Drift</a>')
    a_start = out.rindex('<a ', 0, drift_tab_idx)
    assert 'bg-accent' in out[a_start:drift_tab_idx]


def test_sheet_view_404s_on_unknown_sheet() -> None:
    """The themed 404 handler kicks in (X.2.m) — themed page, not
    Starlette's default plain text."""
    app, sheets = _build_app_with_sheets([("home", "Home")])
    asgi = make_app(dashboards={
        "L1": ServedDashboard(
            tree_app=app, sheet=sheets[0],
            title="L1", data_fetcher=lambda _v, _p: {},
        ),
    })
    client = TestClient(asgi)
    response = client.get("/dashboards/L1/sheets/no-such-sheet")
    assert response.status_code == 404


def test_sheet_view_404s_on_unknown_dashboard() -> None:
    app, sheets = _build_app_with_sheets([("home", "Home")])
    asgi = make_app(dashboards={
        "L1": ServedDashboard(
            tree_app=app, sheet=sheets[0],
            title="L1", data_fetcher=lambda _v, _p: {},
        ),
    })
    client = TestClient(asgi)
    response = client.get("/dashboards/no-such/sheets/anything")
    assert response.status_code == 404


def test_visual_data_fetch_works_for_non_default_sheet() -> None:
    """A visual on the second sheet (not the served default) is
    fetchable via its own sheet_id in the URL. The X.2.b sheet_id
    validation has to accept any analysis sheet, not just the served
    one — otherwise tabs would render but visuals on those tabs
    wouldn't load."""
    app, sheets = _build_app_with_sheets([
        ("home", "Home"), ("drift", "Drift"),
    ])
    seen: dict[str, dict[str, str]] = {}

    def fetcher(visual_id: str, params: dict[str, str]) -> dict[str, list[float]]:
        seen[visual_id] = dict(params)
        return {"values": []}

    asgi = make_app(dashboards={
        "L1": ServedDashboard(
            tree_app=app, sheet=sheets[0],  # Default = home
            title="L1", data_fetcher=fetcher,
        ),
    })
    client = TestClient(asgi)
    # Fetch the drift sheet's visual.
    response = client.get(
        "/dashboards/L1/sheets/drift/visuals/v-drift/data",
    )
    assert response.status_code == 200
    assert "v-drift" in seen


def test_visual_data_fetch_404s_on_sheet_not_in_analysis() -> None:
    app, sheets = _build_app_with_sheets([("home", "Home")])
    asgi = make_app(dashboards={
        "L1": ServedDashboard(
            tree_app=app, sheet=sheets[0],
            title="L1", data_fetcher=lambda _v, _p: {},
        ),
    })
    client = TestClient(asgi)
    response = client.get(
        "/dashboards/L1/sheets/no-such-sheet/visuals/v-x/data",
    )
    assert response.status_code == 404


def test_dashboard_view_with_single_sheet_renders_no_tabs() -> None:
    """Backward compat — dashboards with one sheet (the spike model)
    don't get a tab strip."""
    app, sheets = _build_app_with_sheets([("home", "Home")])
    asgi = make_app(dashboards={
        "L1": ServedDashboard(
            tree_app=app, sheet=sheets[0],
            title="L1", data_fetcher=lambda _v, _p: {},
        ),
    })
    client = TestClient(asgi)
    out = client.get("/dashboards/L1").text
    # Tab links carry the unique ``rounded-t-md`` class (per
    # _render_sheet_tabs); nothing else on the page uses it. Absence
    # proves the tab strip didn't render. The always-on ``← Dashboards``
    # back-link nav uses different classes, so it doesn't trip this.
    assert "rounded-t-md" not in out
