"""X.2.c.1 — Playwright unit tests for the KPI d3 renderer.

Loads bootstrap.js in test mode against the same fixture used by
``test_bootstrap.py``, then drives ``renderKPI`` directly and
asserts on the rendered DOM. d3 is loaded from the CDN so the
selection / data-binding API is available.

The KPI shape contract:

    {
      "values": [
        { "value": 1234.56, "label": "Open", "format": "number"|"currency",
          "delta": -50? },
        ...
      ]
    }

Single-value shorthand also accepted: ``{value, label, format, delta}``
without the ``values`` wrapper. Each entry renders as a ``.kpi-card``
with a ``.kpi-value`` (big number), optional ``.kpi-delta``
(±arrow), and a ``.kpi-label`` (small text underneath).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")


_FIXTURE = (
    Path(__file__).parent / "fixtures" / "bootstrap_test_harness.html"
)


def _load_harness(page: Any) -> None:
    """Load the bootstrap harness fixture and wait for the test-mode
    export. d3 is bundled into the harness via the fixture's own
    script load (the CDN URLs are baked into the page shell, but
    the fixture is a plain file:// page — we need to ensure d3 is
    available)."""
    page.goto(f"file://{_FIXTURE.resolve()}")
    page.wait_for_function(
        "() => window.__bootstrap_internals__ != null", timeout=5000,
    )
    # The fixture doesn't load d3; renderKPI uses d3.select. Inject
    # a minimal d3 stub via CDN if not present. Easier: just inject
    # the real d3 script tag.
    page.evaluate("""() => {
        if (typeof window.d3 !== 'undefined') return null;
        return new Promise((resolve, reject) => {
            var s = document.createElement('script');
            s.src = 'https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js';
            s.onload = () => resolve(null);
            s.onerror = reject;
            document.head.appendChild(s);
        });
    }""")
    page.wait_for_function("() => typeof window.d3 !== 'undefined'", timeout=10000)


def _render_into_target(page: Any, data: dict[str, Any]) -> None:
    """Inject a fresh ``<div id='kpi-target'></div>`` and call
    renderKPI against it. Tests then query for ``.kpi-*`` selectors
    inside ``#kpi-target``."""
    page.evaluate(
        """(data) => {
            var prev = document.getElementById('kpi-target');
            if (prev) prev.remove();
            var t = document.createElement('div');
            t.id = 'kpi-target';
            document.body.appendChild(t);
            window.__bootstrap_internals__.renderKPI(t, data, 'test-vid');
        }""",
        data,
    )


def test_kpi_renders_one_card_per_value() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "values": [
                {"value": 47, "label": "Open", "format": "number"},
                {"value": 12, "label": "Closed", "format": "number"},
                {"value": 5, "label": "Pending", "format": "number"},
            ],
        })
        cards = page.locator("#kpi-target .kpi-card").count()
        browser.close()
    assert cards == 3


def test_kpi_renders_value_and_label_text() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "values": [
                {"value": 1234, "label": "Open Exceptions", "format": "number"},
            ],
        })
        value_text = page.locator("#kpi-target .kpi-value").first.inner_text()
        label_text = page.locator("#kpi-target .kpi-label").first.inner_text()
        browser.close()
    # Default formatting uses thousands separator (en-US locale).
    assert "1,234" in value_text
    assert label_text == "Open Exceptions"


def test_kpi_currency_format_prefixes_dollar_sign() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "values": [
                {"value": 12450.50, "label": "Volume", "format": "currency"},
            ],
        })
        value_text = page.locator("#kpi-target .kpi-value").first.inner_text()
        browser.close()
    assert value_text.startswith("$")
    assert "12,450.50" in value_text


def test_kpi_negative_delta_renders_red_with_down_arrow() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "values": [
                {"value": 100, "label": "X", "delta": -25},
            ],
        })
        delta_loc = page.locator("#kpi-target .kpi-delta").first
        delta_text = delta_loc.inner_text()
        delta_class = delta_loc.get_attribute("class")
        browser.close()
    assert "▼" in delta_text
    assert "25" in delta_text
    assert "text-danger" in (delta_class or "")


def test_kpi_positive_delta_renders_green_with_up_arrow() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "values": [
                {"value": 100, "label": "X", "delta": 12},
            ],
        })
        delta_loc = page.locator("#kpi-target .kpi-delta").first
        delta_text = delta_loc.inner_text()
        delta_class = delta_loc.get_attribute("class")
        browser.close()
    assert "▲" in delta_text
    assert "+12" in delta_text
    assert "text-success" in (delta_class or "")


def test_kpi_omits_delta_when_not_provided() -> None:
    """No delta field → no .kpi-delta DOM node. Avoids rendering a
    blank arrow row when the data fetcher chose not to compute one."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "values": [{"value": 47, "label": "Open"}],
        })
        delta_count = page.locator("#kpi-target .kpi-delta").count()
        browser.close()
    assert delta_count == 0


def test_kpi_accepts_single_value_shorthand() -> None:
    """``{value, label}`` without the ``values`` wrapper still
    renders one card — convenience for fetchers that return a
    single number."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {"value": 42, "label": "Solo"})
        card_count = page.locator("#kpi-target .kpi-card").count()
        value_text = page.locator("#kpi-target .kpi-value").first.inner_text()
        browser.close()
    assert card_count == 1
    assert "42" in value_text


def test_format_kpi_value_handles_non_numeric() -> None:
    """formatKPIValue must not crash on null / undefined / strings —
    fetcher might return a placeholder before data lands."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        results = cast(list[str], page.evaluate("""() => {
            var f = window.__bootstrap_internals__.formatKPIValue;
            return [f(null, 'number'), f(undefined, 'currency'), f('n/a', null)];
        }"""))
        browser.close()
    # null + undefined produce empty string (intentional — caller
    # passed missing data, render blank not "null"). Non-numeric
    # strings pass through unchanged.
    assert results == ["", "", "n/a"]
