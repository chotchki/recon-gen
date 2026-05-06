"""HTML2 Money Trail layer-2 e2e tests.

Lifted from ``tests/spike/test_html_layer2.py`` (X.2.a.5) and
re-pointed to the X.2.b all-GET REST surface. Same assertions:

- initial render — page loads → Refresh → swap fires → d3 hydrates
- click pivots — Sankey rect click GETs the data URL with anchor
  in the query string → server returns new fragment → SVG redraws
- missing chart-data — swap fragment dropped → SVG never appears

Server lifecycle is owned by the ``html2_server`` context manager
in ``_harness_html2`` (matches the QS harness ``deploy`` fixture
shape). DOM assertions go through ``assert_layer2_sankey_shape``.

Gated by ``QS_GEN_E2E=1`` like every other e2e test (no AWS, but
``conftest.py`` matches on path). Run alongside the QS dialect
tests so the dialect-comparison thesis stays a one-command verify.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterator
from typing import Any

import pytest

from tests._test_helpers import make_test_config
from tests.e2e._harness_html2 import (
    assert_layer2_sankey_shape,
    html2_server,
    trigger_initial_swap,
    visual_svg,
)
from quicksight_gen.common.html._smoke_app import (
    build_smoke_app,
    stub_money_trail_fetcher,
)


playwright_sync_api = pytest.importorskip("playwright.sync_api")


# Layer 1 ground truth: the stub fetcher returns a 5-node / 4-link
# Sankey regardless of seed (the values shift but the shape is
# stable — see ``stub_money_trail_fetcher``).
_EXPECTED_SANKEY_NODES = 5
_EXPECTED_SANKEY_LINKS = 4

# Playwright glob matches BOTH the X.2.b nested URL pattern and
# the per-test URL routes used to intercept it.
_DATA_URL_GLOB = "**/visuals/**/data*"


@pytest.fixture
def server_url() -> Iterator[str]:
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    with html2_server(
        tree_app=tree_app, sheet=sheet,
        data_fetcher=stub_money_trail_fetcher,
    ) as url:
        yield url


def test_layer2_initial_load_renders_sankey(server_url: str) -> None:
    """Page loads → click Refresh → swap fires → d3 hydrates the
    Sankey from the swapped fragment. Layer 1 says 5 nodes / 4
    links → Layer 2 asserts SVG has 5 rects / 4 paths."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        page.goto(server_url)
        trigger_initial_swap(page)
        sankey_svg = visual_svg(page, "Sankey")
        sankey_svg.wait_for(state="attached", timeout=5000)
        assert_layer2_sankey_shape(
            sankey_svg,
            expected_nodes=_EXPECTED_SANKEY_NODES,
            expected_links=_EXPECTED_SANKEY_LINKS,
        )
        browser.close()


def test_layer2_click_pivots_sankey(server_url: str) -> None:
    """Click a node rect → d3 click handler fires htmx.ajax with
    ``anchor`` in the query string → server returns a new fragment
    → SVG re-renders.

    Override the server's response via Playwright route — without
    anchor: 2-link payload; with anchor: 4-link payload. The link
    COUNT change makes the assertion robust against d3-sankey's
    relative-width scaling quirks. With X.2.b's GET surface,
    anchor lands in ``?anchor=`` not the POST body."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()

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

        page.goto(server_url)
        with page.expect_response(_DATA_URL_GLOB) as init_resp:
            trigger_initial_swap(page)
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
        browser.close()

    assert len(captured_urls) >= 2, (
        f"Expected ≥2 GETs, saw {len(captured_urls)}: "
        f"{captured_urls}"
    )
    assert "anchor=" in captured_urls[1], (
        f"Second GET didn't include anchor in URL. "
        f"URL: {captured_urls[1]!r}. fireAnchorRequest in the "
        f"bootstrap JS isn't merging anchor into the query string, "
        f"OR the click reached the wrong handler."
    )
    assert before_paths == 2, (
        f"Initial render expected 2 paths (Layer 1 unanchored), "
        f"got {before_paths}."
    )
    assert after_paths == 4, (
        f"Post-click render expected 4 paths (Layer 1 anchored), "
        f"got {after_paths}. Click fired (anchor in URL) but d3 "
        f"didn't re-render the new link set, OR the response wasn't "
        f"swapped into the visual-data div."
    )


def test_layer2_catches_missing_chart_data_bug(server_url: str) -> None:
    """Negative parity check — if the swap fragment dropped the
    chart-data script (regression case for the wrapper-div /
    fragment-shape bugs found in spike.2), Layer 2 catches it: the
    SVG never appears.

    Demonstrates the dialect-comparison thesis: same Layer 2 shape
    that gates QS render bugs gates HTMX render bugs."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()

        def intercept(route: Any) -> None:
            route.fulfill(status=200, body="")

        page.route(_DATA_URL_GLOB, intercept)
        page.goto(server_url)
        trigger_initial_swap(page)

        sankey_svg = visual_svg(page, "Sankey")
        with pytest.raises(playwright_sync_api.TimeoutError):
            sankey_svg.wait_for(state="attached", timeout=2000)
        browser.close()
