"""Unit tests for the App2 Starlette HTML server.

X.2.b shape — all-GET data routes nested under ``/dashboards/.../
sheets/.../visuals/.../data``. POST is gone (except dev-log).

Coverage:

1. ``GET /`` returns 200 with the full sheet HTML (HTMX + d3
   script tags present, filter form present, swap-target div per
   visual).
2. ``GET /dashboards/{d}/sheets/{s}/visuals/{v}/data`` returns
   200 with the JSON-in-script swap fragment shape the page-shell
   bootstrap expects.
3. Filter params from the query string land in the data fetcher's
   ``params`` dict.
4. visual_id routing — GETting one visual's endpoint doesn't call
   the fetcher with a different id.
5. Stale dashboard_id / sheet_id in the URL 404s instead of
   silently mismatching.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.testclient import TestClient

from tests._test_helpers import make_test_config
from quicksight_gen.common.html.server import make_app
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import Sankey


_DASHBOARD_ID = "test-dashboard"
_SHEET_ID = "test"
_VISUAL_ID = "v-sankey"
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
        tree_app=tree_app,
        sheet=sheet,
        dashboard_id=_DASHBOARD_ID,
        data_fetcher=fetcher or (lambda _v, _p: {}),
        dev_log=dev_log,
    )
    return TestClient(asgi)


def test_get_root_returns_full_sheet_html() -> None:
    """GET / returns the page shell: HTMX + d3 + d3-sankey scripts,
    filter form, one section per visual."""
    client = _make_test_app()
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # Page shell pulls HTMX + d3 + d3-sankey from CDN.
    assert "htmx.org" in body
    assert "d3@7" in body or "d3.min.js" in body
    assert "d3-sankey" in body
    # Date-range filter form.
    assert 'id="filter-form"' in body
    assert 'name="date_from"' in body
    assert 'name="date_to"' in body
    # One swap-target div per visual, keyed off visual_id.
    assert f'id="visual-data-{_VISUAL_ID}"' in body
    # Bootstrap script for d3 hydration on htmx:afterSwap.
    assert "htmx:afterSwap" in body
    # Each Refresh button uses hx-get + the nested REST URL.
    assert f'hx-get="{_VISUAL_DATA_PATH}"' in body
    assert 'hx-push-url="true"' in body
    # Each section carries data-fetch-url so d3 click handlers
    # can fire htmx.ajax against the right URL without
    # constructing it themselves.
    assert f'data-fetch-url="{_VISUAL_DATA_PATH}"' in body


def test_get_visual_data_returns_swap_fragment() -> None:
    """GET /dashboards/.../visuals/.../data returns a bare
    ``<script type="application/json" class="chart-data">`` tag
    with the d3 chart data. Default HTMX ``innerHTML`` swap drops
    it inside the page-shell ``visual-data-<id>`` placeholder. No
    wrapper div — wrapping in another div with the same id would
    create duplicate IDs after the swap."""
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


def test_stale_dashboard_id_returns_404() -> None:
    """A path with a dashboard_id this server isn't wired for
    returns 404 instead of silently invoking the fetcher.

    Catches the regression class where a future client bookmarks a
    URL against an old dashboard slug and gets back data for the
    current one — wrong by the cache-key contract (URL == cache
    key, X.2.b.4)."""
    client = _make_test_app()
    bad_path = (
        f"/dashboards/wrong-dashboard/sheets/{_SHEET_ID}"
        f"/visuals/{_VISUAL_ID}/data"
    )
    assert client.get(bad_path).status_code == 404


def test_stale_sheet_id_returns_404() -> None:
    """Same shape as the dashboard 404 — wrong sheet in the URL
    means stale bookmark, return 404 not silently-wrong data."""
    client = _make_test_app()
    bad_path = (
        f"/dashboards/{_DASHBOARD_ID}/sheets/wrong-sheet"
        f"/visuals/{_VISUAL_ID}/data"
    )
    assert client.get(bad_path).status_code == 404


def test_dev_log_off_by_default() -> None:
    """Without ``dev_log=True``, the page carries no ``meta name="dev-log"``
    and the ``/log`` route 404s — production deploys stay silent and
    zero-overhead."""
    client = _make_test_app()
    page = client.get("/").text
    assert 'meta name="dev-log"' not in page
    assert client.post("/log", json={"event": "x"}).status_code == 404


def test_dev_log_on_enables_meta_and_log_endpoint() -> None:
    """``dev_log=True`` injects the meta tag (which the forwarder JS
    checks for) AND registers ``POST /log``. Server returns 204 on
    valid events."""
    client = _make_test_app(dev_log=True)
    page = client.get("/").text
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
