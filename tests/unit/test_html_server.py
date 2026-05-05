"""X.2.spike.2 — unit tests for the Starlette HTML server.

Verifies the route shape + filter-param plumbing without a database
or browser. Uses Starlette's ``TestClient`` (httpx under the hood);
the data fetcher is a stub so the spike's tests stay DB-free.

Coverage:

1. ``GET /`` returns 200 with the full sheet HTML (HTMX + d3 script
   tags present, filter form present, swap-target div per visual).
2. ``POST /visual/{id}/data`` returns 200 with the JSON-in-div swap
   fragment shape the page-shell bootstrap expects.
3. Filter params from the form land in the data fetcher's
   ``params`` dict.
4. Visual_id routing — POSTing to one visual's endpoint doesn't
   call the fetcher with a different id.
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


def _build_app() -> tuple[App, Sheet]:
    cfg = make_test_config()
    app = App(name="server-test", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="server-test-analysis",
        name="Server Test",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("test"),
        name="Test",
        title="Test Sheet",
        description="x",
    ))
    sheet.visuals.append(Sankey(
        title="Sankey",
        subtitle=None,
        visual_id=VisualId("v-sankey"),
    ))
    return app, sheet


def test_get_root_returns_full_sheet_html() -> None:
    """GET / returns the page shell: HTMX + d3 + d3-sankey scripts,
    filter form, one section per visual."""
    tree_app, sheet = _build_app()
    asgi = make_app(
        tree_app=tree_app,
        sheet=sheet,
        data_fetcher=lambda _vid, _params: {},
    )
    client = TestClient(asgi)
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
    assert 'id="visual-data-v-sankey"' in body
    # Bootstrap script for d3 hydration on htmx:afterSwap.
    assert "htmx:afterSwap" in body


def test_post_visual_data_returns_swap_fragment() -> None:
    """POST /visual/{id}/data returns a bare ``<script
    type="application/json" class="chart-data">`` tag with the d3
    chart data. Default HTMX ``innerHTML`` swap drops it inside the
    page-shell ``visual-data-<id>`` placeholder. No wrapper div —
    wrapping in another div with the same id would create duplicate
    IDs after the swap."""
    tree_app, sheet = _build_app()
    asgi = make_app(
        tree_app=tree_app,
        sheet=sheet,
        data_fetcher=lambda _vid, _params: {
            "nodes": [{"name": "A"}, {"name": "B"}],
            "links": [{"source": 0, "target": 1, "value": 5}],
        },
    )
    client = TestClient(asgi)
    resp = client.post(
        "/visual/v-sankey/data",
        data={"date_from": "2026-01-01", "date_to": "2026-05-05"},
    )
    assert resp.status_code == 200
    body = resp.text
    # Fragment is the script tag alone — no wrapper, no duplicate IDs.
    assert body.startswith("<script")
    assert 'type="application/json"' in body
    assert 'class="chart-data"' in body
    # Payload JSON is intact.
    assert '"nodes"' in body
    assert '"links"' in body
    # Sanity: no inner div re-wrap that would shadow #visual-data-X.
    assert "<div" not in body


def test_filter_params_land_in_fetcher() -> None:
    """The form-submitted date params are passed to the fetcher
    callable as a flat ``dict[str, str]``."""
    captured: dict[str, dict[str, str]] = {}

    def capture(_vid: str, params: dict[str, str]) -> Any:
        captured["params"] = params
        return {"nodes": [], "links": []}

    tree_app, sheet = _build_app()
    asgi = make_app(
        tree_app=tree_app, sheet=sheet, data_fetcher=capture,
    )
    client = TestClient(asgi)
    resp = client.post(
        "/visual/v-sankey/data",
        data={"date_from": "2026-01-01", "date_to": "2026-05-05"},
    )
    assert resp.status_code == 200
    assert captured["params"] == {
        "date_from": "2026-01-01",
        "date_to": "2026-05-05",
    }


def test_visual_id_routing_passes_correct_id_to_fetcher() -> None:
    """The visual_id from the URL path lands in the fetcher's first
    arg — no swapping with a body field, no shadowing by params."""
    captured_ids: list[str] = []

    def capture(visual_id: str, _params: dict[str, str]) -> Any:
        captured_ids.append(visual_id)
        return {}

    tree_app, sheet = _build_app()
    asgi = make_app(
        tree_app=tree_app, sheet=sheet, data_fetcher=capture,
    )
    client = TestClient(asgi)
    client.post("/visual/v-sankey/data", data={})
    client.post("/visual/some-other-id/data", data={})
    assert captured_ids == ["v-sankey", "some-other-id"]


def test_response_payload_round_trips_through_json() -> None:
    """Whatever the fetcher returns must JSON-encode losslessly into
    the swap fragment (the bootstrap parses it back via
    ``JSON.parse``)."""
    payload = {"nodes": [{"name": "X"}], "links": [], "extra": 42}

    tree_app, sheet = _build_app()
    asgi = make_app(
        tree_app=tree_app, sheet=sheet,
        data_fetcher=lambda _v, _p: payload,
    )
    client = TestClient(asgi)
    body = client.post("/visual/v-sankey/data", data={}).text
    # Extract the JSON between the <script> tags and parse.
    start = body.index(">") + 1
    end = body.index("</script>", start)
    parsed = json.loads(body[start:end])
    assert parsed == payload
