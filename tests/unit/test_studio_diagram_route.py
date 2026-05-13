"""Studio /diagram route sanity (X.4.b.3 spike arm B).

Ensures the route + the two static mounts (/studio/static for the JS/CSS
shim, /studio/wasm-graphviz for the wasm renderer) all resolve and
deliver the expected payload shape. Doesn't try to render the SVG
client-side (that's the manual browser-drive's job) — just locks in
that the server-side wiring stays intact through future Studio changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# starlette is in [serve] extras; matches X.4.a.3 severability tests'
# import-skip pattern.
starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


FIXTURES = Path(__file__).parent.parent / "l2"


def _build_studio_app(l2_path: Path):
    """Construct a Studio app bound to the given L2 instance.

    Mirrors what ``cli.studio`` does at startup: build the cache, build
    the smoke dashboard for the Dashboards-side mount (any dashboard
    works for this test — we only hit Studio routes), splice Studio
    routes in.
    """
    cache = L2InstanceCache.from_path(l2_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app,
        sheet=sheet,
        title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


def test_studio_diagram_route_renders_with_dot_source() -> None:
    app = _build_studio_app(FIXTURES / "spec_example.yaml")
    with TestClient(app) as client:
        resp = client.get("/diagram")
        assert resp.status_code == 200
        body = resp.text
        # The page renders the chrome + the DOT template.
        assert "<title>Studio diagram — spec_example</title>" in body
        assert 'id="topology-dot"' in body
        assert 'id="diagram-target"' in body
        # The chrome carries all four entity-type checkboxes.
        for kind in ("toggle-role", "toggle-rail", "toggle-template", "toggle-chain"):
            assert f'id="{kind}"' in body, f"missing checkbox {kind}"
        # The engine knob hot-swap links are present.
        for engine in ("dot", "neato", "sfdp"):
            assert f"?engine={engine}" in body
        # The DOT source carries an L2 identifier (proves the typed
        # walk is running, not just an empty digraph).
        assert "ClearingSuspense" in body or "role__" in body


def test_studio_diagram_route_uses_sasquatch_pr_when_loaded() -> None:
    """sasquatch_pr is the meaty fixture the spike judges against.

    Confirms the typed walk produces a substantially larger DOT block
    than spec_example does (a quick proxy for "the meaty graph is
    actually being walked, not the bundled small one").
    """
    spec_app = _build_studio_app(FIXTURES / "spec_example.yaml")
    sas_app = _build_studio_app(FIXTURES / "sasquatch_pr.yaml")
    with TestClient(spec_app) as c1, TestClient(sas_app) as c2:
        spec_body = c1.get("/diagram").text
        sas_body = c2.get("/diagram").text
        # Sasquatch should carry roles spec_example doesn't.
        assert "CashDueFRB" in sas_body
        assert "CashDueFRB" not in spec_body
        # And the sasquatch DOT block should be substantially larger
        # (it has dozens more rails / templates / chains).
        assert len(sas_body) > len(spec_body) * 1.5


def test_studio_static_serves_diagram_js() -> None:
    app = _build_studio_app(FIXTURES / "spec_example.yaml")
    with TestClient(app) as client:
        resp = client.get("/studio/static/diagram.js")
        assert resp.status_code == 200
        body = resp.text
        # Sanity: the shim contract relied on by the page.
        assert "renderDiagram" in body
        assert "wasm-graphviz/index.js" in body


def test_studio_static_serves_diagram_css() -> None:
    app = _build_studio_app(FIXTURES / "spec_example.yaml")
    with TestClient(app) as client:
        resp = client.get("/studio/static/diagram.css")
        assert resp.status_code == 200
        body = resp.text
        # Sanity: the focus-mode dimming class the JS toggles.
        assert ".topology-svg.focused" in body
        assert ".dim" in body


def test_studio_wasm_graphviz_mount_serves_index_js() -> None:
    """The shared wasm-graphviz module is reachable at /studio/wasm-graphviz/index.js.

    For the spike phase we re-use the docs-vendored copy under
    ``docs/stylesheets/wasm-graphviz/`` rather than ship a second
    copy under ``assets/vendor/`` (decision recorded in
    ``_studio_routes.py``; revisit at X.4.c.1).
    """
    app = _build_studio_app(FIXTURES / "spec_example.yaml")
    with TestClient(app) as client:
        resp = client.get("/studio/wasm-graphviz/index.js")
        assert resp.status_code == 200
        # Don't snapshot the 800KB wasm-base64 module body; just sniff
        # that we got something substantial that looks like the module.
        assert len(resp.text) > 100_000
        assert "Graphviz" in resp.text
