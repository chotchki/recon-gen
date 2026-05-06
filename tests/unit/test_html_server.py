"""Unit tests for the App2 Starlette HTML server.

X.2.b shape — all-GET data routes nested under ``/dashboards/.../
sheets/.../visuals/.../data``. POST is gone (except dev-log).

X.2.b.2: ``GET /`` redirects to the dashboards list;
``GET /dashboards`` lists dashboards; ``GET /dashboards/{id}``
renders the served Sheet.

Coverage:

1. ``GET /`` → 302 redirect to ``/dashboards``.
2. ``GET /dashboards`` returns 200 with a link to the wired
   dashboard.
3. ``GET /dashboards/{id}`` returns 200 with the full sheet HTML
   (HTMX + d3 script tags present, filter form present,
   swap-target div per visual).
4. ``GET /dashboards/{wrong-id}`` returns 404.
5. ``GET /dashboards/{id}/sheets/{s}/visuals/{v}/data`` returns
   the chart-data fragment.
6. Filter params from the query string land in the data fetcher's
   ``params`` dict.
7. Stale dashboard_id / sheet_id in the visual data URL 404s
   instead of silently mismatching.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.testclient import TestClient

from tests._test_helpers import make_test_config
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import Sankey


_DASHBOARD_ID = "test-dashboard"
_DASHBOARD_TITLE = "Test Dashboard Title"
_SHEET_ID = "test"
_VISUAL_ID = "v-sankey"
_DASHBOARD_PATH = f"/dashboards/{_DASHBOARD_ID}"
_VISUAL_DATA_PATH = (
    f"/dashboards/{_DASHBOARD_ID}/sheets/{_SHEET_ID}"
    f"/visuals/{_VISUAL_ID}/data"
)


def _build_app() -> tuple[App, Sheet]:
    cfg = make_test_config()
    app = App(name="server-test", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="server-test-analysis",
        name="Server Test",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId(_SHEET_ID),
        name="Test",
        title="Test Sheet",
        description="x",
    ))
    sheet.visuals.append(Sankey(
        title="Sankey",
        subtitle=None,
        visual_id=VisualId(_VISUAL_ID),
    ))
    return app, sheet


def _make_test_app(
    fetcher: Any = None, *, dev_log: bool = False,
) -> TestClient:
    tree_app, sheet = _build_app()
    asgi = make_app(
        dashboards={
            _DASHBOARD_ID: ServedDashboard(
                tree_app=tree_app,
                sheet=sheet,
                title=_DASHBOARD_TITLE,
                data_fetcher=fetcher or (lambda _v, _p: {}),
            ),
        },
        dev_log=dev_log,
    )
    return TestClient(asgi)


def test_get_root_redirects_to_dashboards_list() -> None:
    """``/`` is a convenience redirect; ``/dashboards`` is the
    canonical entry. 302 (not 301) since the future multi-tenant
    home could route per-user."""
    client = _make_test_app()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboards"


def test_get_dashboards_lists_wired_dashboard() -> None:
    """``GET /dashboards`` renders a landing page with one entry
    per dashboard the server is wired for. Today: one entry."""
    client = _make_test_app()
    resp = client.get("/dashboards")
    assert resp.status_code == 200
    body = resp.text
    assert "Dashboards" in body
    assert _DASHBOARD_TITLE in body
    assert f'href="{_DASHBOARD_PATH}"' in body


def test_get_dashboard_returns_full_sheet_html() -> None:
    """``GET /dashboards/{id}`` renders the page shell + the served
    Sheet inline (HTMX + d3 + d3-sankey scripts, filter form,
    one section per visual)."""
    client = _make_test_app()
    resp = client.get(_DASHBOARD_PATH)
    assert resp.status_code == 200
    body = resp.text
    assert "htmx.org" in body
    assert "d3@7" in body or "d3.min.js" in body
    assert "d3-sankey" in body
    assert 'id="filter-form"' in body
    assert 'name="date_from"' in body
    assert 'name="date_to"' in body
    assert f'id="visual-data-{_VISUAL_ID}"' in body
    assert "htmx:afterSwap" in body
    # X.2.g.1.d — single Refresh button broadcasts a custom event;
    # each visual section listens via hx-trigger="load, refresh from:body"
    # and uses its own hx-get for the per-visual data URL.
    assert f'hx-get="{_VISUAL_DATA_PATH}"' in body
    assert 'id="refresh-all"' in body
    assert "refresh from:body" in body
    # Each section carries data-fetch-url so d3 click handlers
    # can fire htmx.ajax against the right URL without
    # constructing it themselves.
    assert f'data-fetch-url="{_VISUAL_DATA_PATH}"' in body


def test_get_dashboard_with_wrong_id_returns_404() -> None:
    """A bookmarked URL for a since-renamed dashboard 404s rather
    than silently rendering the current dashboard's content."""
    client = _make_test_app()
    assert client.get("/dashboards/not-the-wired-id").status_code == 404


def test_get_visual_data_returns_swap_fragment() -> None:
    """GET /dashboards/.../visuals/.../data returns a bare
    ``<script type="application/json" class="chart-data">`` tag
    with the d3 chart data."""
    client = _make_test_app(
        lambda _vid, _params: {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "links": [{"source": 0, "target": 1, "value": 5}],
        },
    )
    resp = client.get(
        _VISUAL_DATA_PATH,
        params={"date_from": "2026-01-01", "date_to": "2026-05-05"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert body.startswith("<script")
    assert 'type="application/json"' in body
    assert 'class="chart-data"' in body
    assert '"nodes"' in body
    assert '"links"' in body
    assert "<div" not in body


def test_filter_params_land_in_fetcher() -> None:
    """The query-string date params are passed to the fetcher
    callable as a flat ``dict[str, str]``."""
    captured: dict[str, dict[str, str]] = {}

    def capture(_vid: str, params: dict[str, str]) -> Any:
        captured["params"] = params
        return {"nodes": [], "links": []}

    client = _make_test_app(capture)
    resp = client.get(
        _VISUAL_DATA_PATH,
        params={"date_from": "2026-01-01", "date_to": "2026-05-05"},
    )
    assert resp.status_code == 200
    assert captured["params"] == {
        "date_from": "2026-01-01",
        "date_to": "2026-05-05",
    }


def test_visual_id_routing_passes_correct_id_to_fetcher() -> None:
    """The visual_id from the URL path lands in the fetcher's first
    arg — no swapping with a query-string field, no shadowing."""
    captured_ids: list[str] = []

    def capture(visual_id: str, _params: dict[str, str]) -> Any:
        captured_ids.append(visual_id)
        return {}

    client = _make_test_app(capture)
    client.get(_VISUAL_DATA_PATH)
    other_path = (
        f"/dashboards/{_DASHBOARD_ID}/sheets/{_SHEET_ID}"
        f"/visuals/some-other-id/data"
    )
    client.get(other_path)
    assert captured_ids == [_VISUAL_ID, "some-other-id"]


def test_stale_dashboard_id_in_visual_data_returns_404() -> None:
    """Same defense as the dashboard view — wrong dashboard_id in
    the visual data path returns 404 instead of silently invoking
    the fetcher with a stale slug."""
    client = _make_test_app()
    bad_path = (
        f"/dashboards/wrong-dashboard/sheets/{_SHEET_ID}"
        f"/visuals/{_VISUAL_ID}/data"
    )
    assert client.get(bad_path).status_code == 404


def test_stale_sheet_id_returns_404() -> None:
    """Wrong sheet in the URL means stale bookmark; return 404."""
    client = _make_test_app()
    bad_path = (
        f"/dashboards/{_DASHBOARD_ID}/sheets/wrong-sheet"
        f"/visuals/{_VISUAL_ID}/data"
    )
    assert client.get(bad_path).status_code == 404


def test_dev_log_off_by_default() -> None:
    """Without ``dev_log=True``, the dashboard page carries no
    ``meta name="dev-log"`` and the ``/log`` route 404s —
    production deploys stay silent and zero-overhead."""
    client = _make_test_app()
    page = client.get(_DASHBOARD_PATH).text
    assert 'meta name="dev-log"' not in page
    assert client.post("/log", json={"event": "x"}).status_code == 404


def test_dev_log_on_enables_meta_and_log_endpoint() -> None:
    """``dev_log=True`` injects the meta tag (which the forwarder JS
    checks for) AND registers ``POST /log``. Server returns 204 on
    valid events."""
    client = _make_test_app(dev_log=True)
    page = client.get(_DASHBOARD_PATH).text
    assert 'meta name="dev-log"' in page
    resp = client.post("/log", json={"event": "htmx:beforeRequest"})
    assert resp.status_code == 204


def test_response_payload_round_trips_through_json() -> None:
    """Whatever the fetcher returns must JSON-encode losslessly into
    the swap fragment (the bootstrap parses it back via
    ``JSON.parse``)."""
    payload = {"nodes": [{"name": "X"}], "links": [], "extra": 42}

    client = _make_test_app(lambda _v, _p: payload)
    body = client.get(_VISUAL_DATA_PATH).text
    start = body.index(">") + 1
    end = body.index("</script>", start)
    parsed = json.loads(body[start:end])
    assert parsed == payload


def test_multi_dashboard_listing_and_per_dashboard_routing() -> None:
    """Two dashboards on one server: each gets its own listing
    entry, its own ``/dashboards/{id}`` page, and its own fetcher
    is invoked when its data path is hit. X.2.b.3 architecture
    proof — the multi-dashboard wiring is structural, X.2.g uses
    it to wire the four real apps."""
    cfg = make_test_config()

    def _build(name_suffix: str) -> tuple[App, Sheet]:
        app = App(name=f"server-test-{name_suffix}", cfg=cfg)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix=f"server-test-{name_suffix}-analysis",
            name=f"Server Test {name_suffix}",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId(f"sheet-{name_suffix}"),
            name=name_suffix,
            title=f"Sheet {name_suffix}",
            description="x",
        ))
        sheet.visuals.append(Sankey(
            title=f"Sankey {name_suffix}",
            subtitle=None,
            visual_id=VisualId(f"v-sankey-{name_suffix}"),
        ))
        return app, sheet

    app_a, sheet_a = _build("a")
    app_b, sheet_b = _build("b")

    fetcher_calls: dict[str, list[str]] = {"a": [], "b": []}

    def make_fetcher(label: str):  # type: ignore[no-untyped-def]
        def fetch(visual_id: str, _params: dict[str, str]) -> Any:
            fetcher_calls[label].append(visual_id)
            return {"label": label}
        return fetch

    asgi = make_app(
        dashboards={
            "alpha": ServedDashboard(
                tree_app=app_a, sheet=sheet_a,
                title="Alpha App", data_fetcher=make_fetcher("a"),
            ),
            "beta": ServedDashboard(
                tree_app=app_b, sheet=sheet_b,
                title="Beta App", data_fetcher=make_fetcher("b"),
            ),
        },
    )
    client = TestClient(asgi)

    # Listing carries both dashboards.
    listing = client.get("/dashboards").text
    assert "Alpha App" in listing
    assert "Beta App" in listing
    assert 'href="/dashboards/alpha"' in listing
    assert 'href="/dashboards/beta"' in listing

    # Each dashboard renders its own sheet.
    assert "Sheet a" in client.get("/dashboards/alpha").text
    assert "Sheet b" in client.get("/dashboards/beta").text

    # Per-dashboard data fetcher routing — alpha's fetcher only
    # sees alpha's visual, never beta's.
    client.get(
        "/dashboards/alpha/sheets/sheet-a/visuals/v-sankey-a/data",
    )
    client.get(
        "/dashboards/beta/sheets/sheet-b/visuals/v-sankey-b/data",
    )
    assert fetcher_calls == {
        "a": ["v-sankey-a"],
        "b": ["v-sankey-b"],
    }


def test_make_app_rejects_empty_dashboards() -> None:
    """A server with zero dashboards has nothing to serve — fail
    fast at construction so misconfigured CLIs surface the bug
    early instead of returning 404 on every route."""
    import pytest

    with pytest.raises(ValueError, match="at least one dashboard"):
        make_app(dashboards={})


def test_visual_data_response_has_cache_control_header() -> None:
    """X.2.b.4 — URL == cache key. Visual data responses carry a
    ``Cache-Control: public, max-age=N`` header so edge / browser
    caches can keep them. Default max-age is 60s; production
    dials it up via the ``visual_data_cache_max_age_s`` kwarg."""
    client = _make_test_app()
    resp = client.get(_VISUAL_DATA_PATH)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=60"


def test_visual_data_cache_max_age_is_configurable() -> None:
    """Production deploys with slower ETL cycles (matviews refresh
    every hour, say) want longer cache; pass the value in."""
    cfg = make_test_config()
    tree_app, sheet = _build_app()
    asgi = make_app(
        dashboards={
            _DASHBOARD_ID: ServedDashboard(
                tree_app=tree_app,
                sheet=sheet,
                title=_DASHBOARD_TITLE,
                data_fetcher=lambda _v, _p: {},
            ),
        },
        visual_data_cache_max_age_s=3600,
    )
    client = TestClient(asgi)
    resp = client.get(_VISUAL_DATA_PATH)
    assert resp.headers["cache-control"] == "public, max-age=3600"
    del cfg


def test_dev_log_disables_visual_data_cache() -> None:
    """Dev iteration needs fresh data on every reload — the cache
    would silently serve stale fragments and look like a bug.
    ``dev_log=True`` flips the cache to ``no-store``."""
    client = _make_test_app(dev_log=True)
    resp = client.get(_VISUAL_DATA_PATH)
    assert resp.headers["cache-control"] == "no-store"


def test_query_params_change_routes_to_different_cache_keys() -> None:
    """Different query strings produce different fetcher inputs.
    The URL (path + query string) is the cache key, so distinct
    query strings → distinct cached responses. This test asserts
    the fetcher actually sees the different params, not that the
    cache itself dedupes (HTTP caches do that work)."""
    captured_params: list[dict[str, str]] = []

    def capture(_vid: str, params: dict[str, str]) -> Any:
        captured_params.append(dict(params))
        return {}

    client = _make_test_app(capture)
    client.get(_VISUAL_DATA_PATH, params={"date_from": "2026-01-01"})
    client.get(_VISUAL_DATA_PATH, params={"date_from": "2026-02-01"})
    client.get(
        _VISUAL_DATA_PATH,
        params={"date_from": "2026-01-01", "anchor": "CustomerDDA"},
    )
    assert captured_params == [
        {"date_from": "2026-01-01"},
        {"date_from": "2026-02-01"},
        {"date_from": "2026-01-01", "anchor": "CustomerDDA"},
    ]


def test_dashboard_view_does_not_cache() -> None:
    """The dashboard chrome page is rendered fresh every request
    (no Cache-Control header set explicitly). Caching the chrome
    is a future X.2.l theme-injection concern — for now the
    chrome rebuilds per request, which is cheap (~ms)."""
    client = _make_test_app()
    resp = client.get(_DASHBOARD_PATH)
    # Starlette's HTMLResponse defaults to no Cache-Control. The
    # absence of an explicit cache directive is the assertion —
    # we don't want to be silently caching the chrome at the edge
    # before the X.2.l theme story lands.
    assert "cache-control" not in resp.headers
