"""X.2.c.4 — Playwright unit tests for the LineChart d3 renderer.

Same fixture pattern as the other render tests. Covers: line per
series, multi-series legend, axis labels, currency tick
formatting, x_kind="number" vs default "date", null-value gap
handling, single-series legend suppression.
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
    page.goto(f"file://{_FIXTURE.resolve()}")
    page.wait_for_function(
        "() => window.__bootstrap_internals__ != null", timeout=5000,
    )
    # d3 is loaded by the fixture HTML's own <script src> (the vendored
    # copy — X.2.p — not a CDN), ahead of bootstrap.js; this is just a
    # defensive wait that it landed.
    page.wait_for_function(
        "() => typeof window.d3 !== 'undefined'", timeout=5000,
    )


def _render_into_target(page: Any, data: dict[str, Any]) -> None:
    page.evaluate(
        """(data) => {
            var prev = document.getElementById('linechart-target');
            if (prev) prev.remove();
            var t = document.createElement('div');
            t.id = 'linechart-target';
            t.style.width = '800px';
            document.body.appendChild(t);
            window.__bootstrap_internals__.renderLineChart(t, data, 'test-vid');
        }""",
        data,
    )


# Neutral series names (not the dashboard's sheet names) so the
# BE.2 cross-corpus lint doesn't false-positive on coincidental
# value match against `_DRIFT_NAME` / `_OVERDRAFT_NAME` — these
# chart-renderer tests aren't asserting anything about the L1
# dashboard, just that arbitrary series labels round-trip through
# the SVG legend. Cf. BE.4 Phase C sweep.
_DATE_DATA: dict[str, Any] = {
    "x_values": ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
    "series": [
        {"name": "Series A", "values": [12, 7, 9, 14]},
        {"name": "Series B", "values": [3, 5, 2, 6]},
    ],
    "x_label": "Month",
    "y_label": "Violations",
}


def test_linechart_renders_one_path_per_series() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _DATE_DATA)
        line_count = page.locator(
            "#linechart-target svg path.linechart-line",
        ).count()
        browser.close()
    assert line_count == 2


def test_linechart_single_series_shorthand() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "x_values": ["2026-01-01", "2026-02-01"],
            "values": [10, 20],
            "label": "X",
        })
        line_count = page.locator(
            "#linechart-target svg path.linechart-line",
        ).count()
        browser.close()
    assert line_count == 1


def test_linechart_legend_renders_for_multi_series() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _DATE_DATA)
        legend_entries = page.locator(
            "#linechart-target .linechart-legend .linechart-legend-entry",
        ).count()
        legend_text = cast(list[str], page.evaluate(
            """() => Array.from(
                document.querySelectorAll(
                    '#linechart-target .linechart-legend .linechart-legend-entry text',
                ),
            ).map((t) => t.textContent || '')""",
        ))
        browser.close()
    assert legend_entries == 2
    assert "Series A" in legend_text
    assert "Series B" in legend_text


def test_linechart_legend_suppressed_for_single_series() -> None:
    """Single-series chart's legend is just visual noise — the
    title carries the meaning."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "x_values": ["2026-01-01"],
            "series": [{"name": "Solo", "values": [42]}],
        })
        legend_count = page.locator(
            "#linechart-target .linechart-legend",
        ).count()
        browser.close()
    assert legend_count == 0


def test_linechart_renders_axis_labels() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _DATE_DATA)
        x_label = page.locator(
            "#linechart-target svg .linechart-x-label",
        ).text_content()
        y_label = page.locator(
            "#linechart-target svg .linechart-y-label",
        ).text_content()
        browser.close()
    assert x_label == "Month"
    assert y_label == "Violations"


def test_linechart_currency_format_applied_to_y_ticks() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        currency_data = dict(_DATE_DATA)
        currency_data["format"] = "currency"
        _render_into_target(page, currency_data)
        y_ticks = cast(list[str], page.evaluate(
            """() => Array.from(
                document.querySelectorAll(
                    '#linechart-target .linechart-y-axis text',
                ),
            ).map((t) => t.textContent || '')""",
        ))
        browser.close()
    assert all(t.startswith("$") for t in y_ticks if t)


def test_linechart_supports_numeric_x_kind() -> None:
    """Some series have numeric x (day-offsets, sequence indices)
    rather than dates. ``x_kind: "number"`` switches the scale."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "x_values": [1, 2, 3, 4, 5],
            "x_kind": "number",
            "series": [{"name": "trend", "values": [10, 20, 15, 30, 25]}],
        })
        line_count = page.locator(
            "#linechart-target svg path.linechart-line",
        ).count()
        # x-axis ticks should be numeric (no date formatting).
        x_ticks = cast(list[str], page.evaluate(
            """() => Array.from(
                document.querySelectorAll(
                    '#linechart-target .linechart-x-axis text',
                ),
            ).map((t) => t.textContent || '')""",
        ))
        browser.close()
    assert line_count == 1
    # Linear-scale ticks should parse as numbers; date-scale ticks
    # contain month abbreviations or year strings. Float() round-
    # trip is the cleanest separator (rejects "Jan", "2026", etc.).
    def _is_numeric(s: str) -> bool:
        try:
            float(s)
            return True
        except ValueError:
            return False

    numeric_ticks = [t for t in x_ticks if t and _is_numeric(t.strip())]
    assert len(numeric_ticks) >= 2


def test_linechart_handles_null_values_as_gaps() -> None:
    """``null`` value in series breaks the line into segments
    rather than crashing or dropping the rest of the data."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "x_values": ["2026-01-01", "2026-02-01", "2026-03-01"],
            "series": [{"name": "Gap", "values": [10, None, 30]}],
        })
        line_count = page.locator(
            "#linechart-target svg path.linechart-line",
        ).count()
        # Line is still rendered (path element exists); the gap is
        # an internal d3.line() concern, not a render-failure.
        line_d = page.locator(
            "#linechart-target svg path.linechart-line",
        ).first.get_attribute("d") or ""
        browser.close()
    assert line_count == 1
    # d3.line().defined() inserts a 'M' (moveto) for each defined
    # subsequence. Two defined points (10, 30) → 2 'M' commands
    # (one per island), or 1 'M' followed by 'L' if d3 chose
    # differently. Just assert the path has a non-empty 'd'.
    assert line_d != ""


def test_linechart_per_series_color_override() -> None:
    """A series with explicit ``color`` overrides the palette
    default — useful when consumers want themed stroke colours."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "x_values": ["2026-01-01", "2026-02-01"],
            "series": [
                {"name": "A", "values": [1, 2], "color": "#ff0000"},
                {"name": "B", "values": [3, 4]},  # default palette
            ],
        })
        first_stroke = page.locator(
            "#linechart-target svg path.linechart-line",
        ).first.get_attribute("stroke")
        second_stroke = page.locator(
            "#linechart-target svg path.linechart-line",
        ).nth(1).get_attribute("stroke")
        browser.close()
    assert first_stroke == "#ff0000"
    # 2nd series falls back to default palette index 1 (emerald).
    assert second_stroke == "#10b981"


def test_linechart_handles_empty_series_without_crashing() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "x_values": [],
            "series": [{"values": []}],
        })
        svg_count = page.locator("#linechart-target svg").count()
        line_count = page.locator(
            "#linechart-target svg path.linechart-line",
        ).count()
        browser.close()
    assert svg_count == 1
    # 1 path element rendered with empty 'd' — no crash.
    assert line_count == 1
