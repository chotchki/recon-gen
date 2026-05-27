# pyright: reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
# BF.4/F: Playwright expect_request lambda receives a Request whose .url is str;
# Playwright stubs leak Unknown through the lambda parameter.
"""HTML2 Money Trail layer-2 e2e tests.

Lifted from ``tests/spike/test_html_layer2.py`` (X.2.a.5) and re-pointed
to the X.2.b all-GET REST surface. Same App2-internal assertions:

- initial render — page loads → swap fires → d3 hydrates the Sankey
  with the right rect/path counts (Layer 1 ground truth shape)
- click pivots — Sankey rect click GETs the data URL with anchor in
  the query string → server returns new fragment → SVG redraws
- missing chart-data — swap fragment dropped → SVG never appears

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — driver owns the
server + page lifecycle via ``App2Driver.smoke()``). Three of these are
App2-internal Sankey-wire-shape assertions (rect/path SVG counts, click-
anchor URL flow, missing-fragment negative path) that don't translate
to renderer-agnostic verbs, so they reach for the ``driver.page``
escape hatch — same playwright primitives the original used, just sealed
behind the driver layer.
"""

from __future__ import annotations

import json as _json
from typing import Any

import pytest

from tests.e2e._drivers import App2Driver
from tests.e2e._harness_html2 import assert_layer2_sankey_shape, visual_svg


# Playwright still provides the TimeoutError type used by the negative-
# path assertion below (driver.page returns a Playwright Page, so its
# raised errors are Playwright errors). importorskip survives the AST
# lint — it's a Call, not an Import statement.
playwright_sync_api = pytest.importorskip("playwright.sync_api")


# Layer 1 ground truth: the stub fetcher returns a 5-node / 4-link Sankey
# regardless of seed (the values shift but the shape is stable — see
# ``stub_money_trail_fetcher``).
_EXPECTED_SANKEY_NODES = 5
_EXPECTED_SANKEY_LINKS = 4

# Playwright glob matches BOTH the X.2.b nested URL pattern and the
# per-test URL routes used to intercept it.
_DATA_URL_GLOB = "**/visuals/**/data*"

# X.2.b.2: ``/`` redirects to ``/dashboards``; the smoke dashboard lives
# at ``/dashboards/smoke``. App2Driver.smoke() wires that ID.
_DASHBOARD_PATH = "/dashboards/smoke"


def test_layer2_initial_load_renders_sankey() -> None:
    """Page loads → HTMX auto-fetch fires on DOMContentLoaded (the
    visual-data div carries ``hx-trigger="load"``) → swap fires → d3
    hydrates the Sankey from the swapped fragment. Layer 1 says 5 nodes
    / 4 links → Layer 2 asserts SVG has 5 rects / 4 paths."""
    with App2Driver.smoke() as driver:
        page = driver.page
        page.goto(driver.base_url + _DASHBOARD_PATH)
        page.wait_for_load_state("networkidle")
        sankey_svg = visual_svg(page, "Sankey")
        sankey_svg.wait_for(state="attached", timeout=5000)
        assert_layer2_sankey_shape(
            sankey_svg,
            expected_nodes=_EXPECTED_SANKEY_NODES,
            expected_links=_EXPECTED_SANKEY_LINKS,
        )


def test_layer2_click_pivots_sankey() -> None:
    """Click a node rect → d3 click handler fires htmx.ajax with
    ``anchor`` in the query string → server returns a new fragment →
    SVG re-renders.

    Override the server's response via Playwright route — without
    anchor: 2-link payload; with anchor: 4-link payload. The link COUNT
    change makes the assertion robust against d3-sankey's relative-width
    scaling quirks. With X.2.b's GET surface, anchor lands in
    ``?anchor=`` not the POST body."""
    with App2Driver.smoke() as driver:
        page = driver.page

        def anchor_aware_route(route: Any) -> None:
            url = route.request.url
            anchor_present = (
                "anchor=" in url and "anchor=&" not in url
                and not url.endswith("anchor=")
            )
            if anchor_present:
                payload: dict[str, Any] = {
                    "nodes": [
                        {"name": "ExternalAcquirer"},
                        {"name": "CustomerDDA"},
                        {"name": "GLControl"},
                        {"name": "Concentration"},
                        {"name": "FundsPool"},
                    ],
                    "links": [
                        {"source": 0, "target": 1, "value": 50},
                        {"source": 1, "target": 2, "value": 40},
                        {"source": 2, "target": 3, "value": 30},
                        {"source": 3, "target": 4, "value": 20},
                    ],
                }
            else:
                payload = {
                    "nodes": [
                        {"name": "ExternalAcquirer"},
                        {"name": "CustomerDDA"},
                        {"name": "GLControl"},
                    ],
                    "links": [
                        {"source": 0, "target": 1, "value": 5},
                        {"source": 1, "target": 2, "value": 5},
                    ],
                }
            fragment = (
                '<script type="application/json" class="chart-data">'
                + _json.dumps(payload) + "</script>"
            )
            route.fulfill(
                status=200, content_type="text/html", body=fragment,
            )

        page.route(_DATA_URL_GLOB, anchor_aware_route)

        captured_urls: list[str] = []
        page.on("request", lambda req: (
            captured_urls.append(req.url)
            if "/visuals/" in req.url and "/data" in req.url
            else None
        ))

        # X.2.g.1.a auto-load fires the data fetch as soon as the body
        # lands in the DOM; wrap the goto so expect_response catches that
        # initial fetch.
        with page.expect_response(_DATA_URL_GLOB) as init_resp:
            page.goto(driver.base_url + _DASHBOARD_PATH)
        assert init_resp.value.status == 200

        sankey_svg = visual_svg(page, "Sankey")
        sankey_svg.wait_for(state="attached", timeout=5000)
        before_paths = sankey_svg.locator("path").count()

        first_rect = sankey_svg.locator("rect").first
        with page.expect_response(_DATA_URL_GLOB) as click_resp:
            first_rect.click()
        assert click_resp.value.status == 200, (
            f"Click triggered a response with bad status. "
            f"URLs seen: {captured_urls}"
        )

        page.wait_for_function(
            "before => document.querySelectorAll("
            "'section[data-visual-kind=\"Sankey\"] svg path').length !== before",
            arg=before_paths,
            timeout=5000,
        )
        after_paths = sankey_svg.locator("path").count()

    # X.2.g.1.a auto-load: every visual on the sheet auto-fetches (here
    # both smoke-sankey AND smoke-force), so we can't assume
    # captured_urls[1] is the click pivot. Filter to Sankey-only +
    # require at least one with anchor= present.
    sankey_urls = [u for u in captured_urls if "/visuals/smoke-sankey/" in u]
    assert len(sankey_urls) >= 2, (
        f"Expected ≥2 Sankey GETs (initial + click pivot), saw "
        f"{len(sankey_urls)}: {sankey_urls}"
    )
    assert any("anchor=" in u for u in sankey_urls), (
        f"No Sankey GET included anchor in URL. Sankey URLs: {sankey_urls}. "
        f"fireAnchorRequest in the bootstrap JS isn't merging anchor "
        f"into the query string, OR the click reached the wrong handler."
    )
    assert before_paths == 2, (
        f"Initial render expected 2 paths (Layer 1 unanchored), "
        f"got {before_paths}."
    )
    assert after_paths == 4, (
        f"Post-click render expected 4 paths (Layer 1 anchored), "
        f"got {after_paths}. Click fired (anchor in URL) but d3 didn't "
        f"re-render the new link set, OR the response wasn't swapped "
        f"into the visual-data div."
    )


def test_layer2_catches_missing_chart_data_bug() -> None:
    """Negative parity check — if the swap fragment dropped the
    chart-data script (regression case for the wrapper-div / fragment-
    shape bugs found in spike.2), Layer 2 catches it: the SVG never
    appears.

    Demonstrates the dialect-comparison thesis: same Layer 2 shape that
    gates QS render bugs gates HTMX render bugs."""
    with App2Driver.smoke() as driver:
        page = driver.page

        def intercept(route: Any) -> None:
            route.fulfill(status=200, body="")

        page.route(_DATA_URL_GLOB, intercept)
        page.goto(driver.base_url + _DASHBOARD_PATH)
        # Auto-load fires the data fetch; intercept route returns an
        # empty body. SVG never appears because no chart-data script
        # lands in the DOM.
        sankey_svg = visual_svg(page, "Sankey")
        with pytest.raises(playwright_sync_api.TimeoutError):
            sankey_svg.wait_for(state="attached", timeout=2000)
