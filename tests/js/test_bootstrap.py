"""Pilot Playwright unit test for bootstrap.js (X.2.a.2 + X.2.b.1).

Establishes the JS-unit pattern for the X.2 HTMX renderer:

- A small static HTML fixture under ``tests/js/fixtures/`` sets
  ``window.__test_mode__ = true`` BEFORE loading the JS-under-test,
  so ``bootstrap.js``'s IIFE exports internals on
  ``window.__bootstrap_internals__``.
- The fixture mocks ``window.htmx`` with a recording stub and
  attaches a ``sankey:click`` listener that captures event detail.
- A ``<section data-fetch-url=...>`` stands in for the page-shell
  section render.py emits — fireAnchorRequest reads the URL from
  it.
- The test loads the fixture via ``file://``, drives the JS via
  ``page.evaluate``, then reads the recorded state back.

Why a static fixture (no server): keeps the JS unit test free of
the Starlette stack — the fetcher / route / DOM hydration paths
are exercised by the layer-2 e2e test under
``tests/e2e/test_html2_money_trail.py``. This pilot covers ONE
function (fireAnchorRequest) to lock the contract: section's
data-fetch-url + form values + anchor → htmx.ajax GET call shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")


_FIXTURE = Path(__file__).parent / "fixtures" / "bootstrap_test_harness.html"
_FETCH_URL = (
    "/dashboards/test/sheets/money-trail/visuals/viz-money-trail/data"
)


def test_fire_anchor_request_gets_data_url_from_section() -> None:
    """``fireAnchorRequest(visualId, anchor)`` should:

    1. Dispatch a ``sankey:click`` CustomEvent on the body with the
       visualId + anchor in detail (so dev-log can capture intent).
    2. Look up ``data-fetch-url`` on the section matching
       ``visualId``.
    3. Call ``htmx.ajax('GET', <fetch_url>, { target, swap, values })``
       where ``values`` carries the anchor merged with the current
       ``#filter-form`` values.

    Locks the bootstrap.js click → request contract in a place
    that fails fast (JS unit) instead of the layer-2 e2e test
    (which would only catch breakage as a wrong-DOM symptom).
    """
    fixture_url = f"file://{_FIXTURE.resolve()}"

    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        page.goto(fixture_url)
        page.wait_for_function(
            "() => window.__bootstrap_internals__ != null",
            timeout=5000,
        )

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
    assert call["verb"] == "GET"
    assert call["url"] == _FETCH_URL, (
        f"URL should come from section's data-fetch-url, got "
        f"{call['url']!r}"
    )
    opts = call["opts"]
    assert opts["target"] == "#visual-data-viz-money-trail"
    assert opts["swap"] == "innerHTML"
    # values must merge form fields + the anchor (htmx serializes
    # them into the GET query string).
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
    """If ``#filter-form`` is missing, fireAnchorRequest still GETs
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
        page.evaluate("() => document.querySelector('#filter-form').remove()")
        page.evaluate(
            "() => window.__bootstrap_internals__.fireAnchorRequest("
            "'viz-money-trail', 'NodeA')",
        )

        htmx_calls = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__htmx_calls__"),
        )
        browser.close()

    assert len(htmx_calls) == 1
    assert htmx_calls[0]["verb"] == "GET"
    assert htmx_calls[0]["opts"]["values"] == {"anchor": "NodeA"}


def test_fire_anchor_request_logs_when_section_missing() -> None:
    """If the section disappeared from the DOM (or the page shell
    never rendered with X.2.b's data-fetch-url attribute),
    fireAnchorRequest should log + bail rather than constructing
    a URL silently or firing against an undefined endpoint."""
    fixture_url = f"file://{_FIXTURE.resolve()}"

    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        # Capture console errors before navigation so the page's
        # console.error during fireAnchorRequest is recorded.
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error" else None,
        )
        page.goto(fixture_url)
        page.wait_for_function(
            "() => window.__bootstrap_internals__ != null",
            timeout=5000,
        )
        # Drop the section — simulates the missing-attribute case.
        page.evaluate(
            "() => document.querySelector("
            "'section[data-visual-id=\"viz-money-trail\"]').remove()",
        )
        page.evaluate(
            "() => window.__bootstrap_internals__.fireAnchorRequest("
            "'viz-money-trail', 'X')",
        )

        htmx_calls = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__htmx_calls__"),
        )
        browser.close()

    assert htmx_calls == [], (
        f"Expected no htmx.ajax calls when section is missing, "
        f"got: {htmx_calls}"
    )
    assert any(
        "fireAnchorRequest" in e for e in console_errors
    ), (
        f"Expected a console.error mentioning fireAnchorRequest; "
        f"got: {console_errors}"
    )
