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

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
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
        # The chrome carries the visibility checkboxes (X.4.b.3 chrome iteration:
        # roles split into internal/external + edge-label sub-toggles).
        for kind in (
            "toggle-role-internal", "toggle-role-external",
            "toggle-rail", "toggle-template", "toggle-chain",
            "toggle-edge-label-rail_bundle",
            "toggle-edge-label-self_loop",
            "toggle-edge-label-chain",
        ):
            assert f'id="{kind}"' in body, f"missing checkbox {kind}"
        # Layer stepper landed (the mode dropdown + engine pills were
        # dropped in X.4.b.cleanup once dot won the spike). AM.2 step
        # 3 (2026-05-25): `.layer-btn` semantic class retired in favor
        # of `chrome_button_classes()` utilities; the layer-link href
        # shape is the stable identity (each layer link sets
        # `?layer=N`).
        assert 'href="?layer=1"' in body
        assert 'href="?layer=2"' in body
        assert 'href="?layer=3"' in body
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
        # (it has dozens more rails / templates / chains). Ratio
        # softened post-AM.2 step 3 (2026-05-25) — page chrome
        # migrated to raw Tailwind utility classes, which inflates
        # spec_example's fixed overhead enough that the 1.3x ratio
        # no longer holds (sas/spec ≈ 1.24x today, still proves the
        # DOT block grows with the L2's size but the noise floor is
        # higher). The CashDueFRB / not-in-spec_body assertion above
        # is the load-bearing identity check.
        assert len(sas_body) > len(spec_body) * 1.2


def test_studio_static_serves_diagram_js() -> None:
    app = _build_studio_app(FIXTURES / "spec_example.yaml")
    with TestClient(app) as client:
        resp = client.get("/studio/static/diagram.js")
        assert resp.status_code == 200
        body = resp.text
        # Sanity: the shim contract relied on by the page.
        assert "renderDiagram" in body
        assert "wasm-graphviz/index.js" in body


def test_studio_static_serves_diagram_svg_css() -> None:
    """AM.4 (2026-05-25) renamed diagram.css → diagram-svg.css after
    AM.2's chrome migration left only the SVG-attribute-selector
    rules. The chrome-specific assertions (`.diagram-chrome` /
    `.layer-btn`) were retired alongside the rules they pinned —
    those classes no longer exist anywhere in the source. SVG rules
    pin the served file's identity now."""
    app = _build_studio_app(FIXTURES / "spec_example.yaml")
    with TestClient(app) as client:
        resp = client.get("/studio/static/diagram-svg.css")
        assert resp.status_code == 200
        body = resp.text
        # SVG-attribute-selector rules — the irreducible non-utility
        # remainder per AM.0 lock L4 (they bind to graphviz-emitted
        # `[data-kind]` / `[data-presence]` attributes on the SVG).
        assert ".topology-svg" in body
        assert "[data-kind=" in body


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
