"""X.2.a.2 — Pilot Playwright unit test for bootstrap.js.

Establishes the JS-unit pattern for the X.2 HTMX renderer:

- A small static HTML fixture under ``tests/js/fixtures/`` sets
  ``window.__test_mode__ = true`` BEFORE loading the JS-under-test,
  so ``bootstrap.js``'s IIFE exports internals on
  ``window.__bootstrap_internals__``.
- The fixture mocks ``window.htmx`` with a recording stub and
  attaches a ``sankey:click`` listener that captures event detail.
- The test loads the fixture via ``file://``, drives the JS via
  ``page.evaluate``, then reads the recorded state back.

Why a static fixture (no server): keeps the JS unit test free of
the Starlette stack — the fetcher / route / DOM hydration paths
are exercised by the spike-3 layer-2 test (and the X.2.a.5
harness lift). This pilot covers ONE function (fireAnchorRequest)
to lock the pattern; later tests add per-renderer coverage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")


_FIXTURE = Path(__file__).parent / "fixtures" / "bootstrap_test_harness.html"


def test_fire_anchor_request_posts_to_visual_data_endpoint() -> None:
    """``fireAnchorRequest(visualId, anchor)`` should:

    1. Dispatch a ``sankey:click`` CustomEvent on the body with the
       visualId + anchor in detail (so dev-log can capture intent).
    2. Call ``htmx.ajax('POST', '/visual/<id>/data', { target,
       swap, values })`` where ``values`` carries the anchor
       merged with the current ``#filter-form`` values.

    Locks the bootstrap.js click → request contract in a place
    that fails fast (JS unit) instead of in the spike-3 layer-2
    test (which would only catch breakage as a wrong-DOM symptom).
    """
    fixture_url = f"file://{_FIXTURE.resolve()}"

    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        page.goto(fixture_url)
        # Wait until the IIFE finished and exported its internals —
        # the script tag loads asynchronously after page load.
        page.wait_for_function(
            "() => window.__bootstrap_internals__ != null",
            timeout=5000,
        )

        # Drive the function under test from the test side.
        page.evaluate(
            "() => window.__bootstrap_internals__.fireAnchorRequest("
            "'viz-money-trail', 'CustomerDDA')",
        )

        htmx_calls = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__htmx_calls__"),
        )
        sankey_clicks = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__sankey_clicks__"),
        )
        browser.close()

    assert len(htmx_calls) == 1, (
        f"Expected exactly 1 htmx.ajax call, got {len(htmx_calls)}: "
        f"{htmx_calls}"
    )
    call = htmx_calls[0]
    assert call["verb"] == "POST"
    assert call["url"] == "/visual/viz-money-trail/data", (
        f"URL should embed visualId, got {call['url']!r}"
    )
    opts = call["opts"]
    assert opts["target"] == "#visual-data-viz-money-trail"
    assert opts["swap"] == "innerHTML"
    # values must merge form fields + the anchor.
    assert opts["values"] == {
        "anchor": "CustomerDDA",
        "date_from": "2024-01-01",
        "date_to": "2024-12-31",
    }, (
        f"values dict must merge form inputs with anchor; got "
        f"{opts['values']!r}"
    )

    assert len(sankey_clicks) == 1, (
        f"Expected exactly 1 sankey:click event, got "
        f"{len(sankey_clicks)}: {sankey_clicks}"
    )
    assert sankey_clicks[0] == {
        "visualId": "viz-money-trail",
        "anchor": "CustomerDDA",
    }


def test_fire_anchor_request_works_without_filter_form() -> None:
    """If ``#filter-form`` is missing, fireAnchorRequest still POSTs
    with just the anchor — defensive against pages that don't
    render the date filter (e.g. a single-visual embed)."""
    fixture_url = f"file://{_FIXTURE.resolve()}"

    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        page.goto(fixture_url)
        page.wait_for_function(
            "() => window.__bootstrap_internals__ != null",
            timeout=5000,
        )
        # Remove the form, then fire the request.
        page.evaluate("() => document.querySelector('#filter-form').remove()")
        page.evaluate(
            "() => window.__bootstrap_internals__.fireAnchorRequest("
            "'viz-X', 'NodeA')",
        )

        htmx_calls = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__htmx_calls__"),
        )
        browser.close()

    assert len(htmx_calls) == 1
    assert htmx_calls[0]["opts"]["values"] == {"anchor": "NodeA"}
