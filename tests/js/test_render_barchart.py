"""X.2.c.3 — Playwright unit tests for the BarChart d3 renderer.

Same fixture pattern as test_render_kpi / test_render_table.
Covers: rect-per-(category × series), single-series shorthand,
axis label rendering, currency formatting on the y-axis tick
labels, multi-series fan-out, empty-input no-crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from recon_gen.apps.l1_dashboard.app import _DRIFT_NAME, _OVERDRAFT_NAME


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
            var prev = document.getElementById('barchart-target');
            if (prev) prev.remove();
            var t = document.createElement('div');
            t.id = 'barchart-target';
            t.style.width = '800px';
            document.body.appendChild(t);
            window.__bootstrap_internals__.renderBarChart(t, data, 'test-vid');
        }""",
        data,
    )


def test_barchart_renders_one_rect_per_category_single_series() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": [_DRIFT_NAME, _OVERDRAFT_NAME, "Limit"],
            "series": [{"name": "count", "values": [12, 7, 3]}],
        })
        bars = page.locator("#barchart-target svg rect.barchart-bar").count()
        browser.close()
    assert bars == 3


def test_barchart_renders_rect_per_category_x_series() -> None:
    """Multi-series → rects = categories × series; each series gets a
    legend entry. (AO.R.2 flattened the per-series ``g.barchart-series``
    wrappers into one rect set + a color legend so stacking works.)"""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": ["Q1", "Q2", "Q3", "Q4"],
            "series": [
                {"name": "Revenue", "values": [100, 120, 140, 160]},
                {"name": "Cost", "values": [50, 55, 60, 65]},
            ],
        })
        bars = page.locator("#barchart-target svg rect.barchart-bar").count()
        legend = page.locator(
            "#barchart-target svg g.barchart-legend",
        ).count()
        browser.close()
    assert bars == 8
    assert legend == 2  # one legend row per series


def test_barchart_stacked_renders_segments_and_legend() -> None:
    """AO.R.2 — ``stacked=true`` with a series stacks one rect per
    (category × series) and shows the per-series legend. Segments in a
    category share the category's x (stacked, not clustered)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": ["Q1", "Q2"],
            "series": [
                {"name": "ach", "values": [5, 7]},
                {"name": "wire", "values": [2, 3]},
            ],
            "stacked": True,
        })
        bars = page.locator("#barchart-target svg rect.barchart-bar").count()
        legend = page.locator(
            "#barchart-target svg g.barchart-legend",
        ).count()
        # Distinct x positions == category count (segments stack, not
        # cluster side-by-side).
        distinct_xs = cast(int, page.evaluate(
            """() => new Set(
                Array.from(
                  document.querySelectorAll('#barchart-target rect.barchart-bar'),
                ).map((r) => r.getAttribute('x')),
            ).size""",
        ))
        browser.close()
    assert bars == 4  # 2 categories × 2 series
    assert legend == 2
    assert distinct_xs == 2  # stacked → one x per category


def test_barchart_accepts_single_series_shorthand() -> None:
    """``{categories, values, label}`` (no ``series`` wrapper) still
    renders one rect per category — convenience for fetchers that
    return one number per category. Single-series → no legend."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": ["A", "B"],
            "values": [10, 20],
            "label": "count",
        })
        bars = page.locator("#barchart-target svg rect.barchart-bar").count()
        legend = page.locator(
            "#barchart-target svg g.barchart-legend",
        ).count()
        browser.close()
    assert bars == 2
    assert legend == 0  # single-series needs no legend


def test_barchart_renders_x_and_y_axis_labels() -> None:
    """Axis labels (Q.1.a.3 plain English) carry from the tree
    through the data shape into the SVG."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": [_DRIFT_NAME, _OVERDRAFT_NAME],
            "series": [{"name": "count", "values": [12, 7]}],
            "x_label": "Invariant",
            "y_label": "Violations",
        })
        # SVG <text> nodes aren't HTMLElements — use text_content,
        # not inner_text.
        x_label = page.locator(
            "#barchart-target svg .barchart-x-label",
        ).text_content()
        y_label = page.locator(
            "#barchart-target svg .barchart-y-label",
        ).text_content()
        browser.close()
    assert x_label == "Invariant"
    assert y_label == "Violations"


def test_barchart_omits_axis_label_nodes_when_not_provided() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": ["A"],
            "series": [{"values": [1]}],
        })
        x_count = page.locator(
            "#barchart-target svg .barchart-x-label",
        ).count()
        y_count = page.locator(
            "#barchart-target svg .barchart-y-label",
        ).count()
        browser.close()
    assert x_count == 0
    assert y_count == 0


def test_barchart_renders_x_axis_category_ticks() -> None:
    """The x axis renders ticks with the category names — proves the
    axis is wired to the band scale, not just an empty <g>."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        # Use neutral strings (not the dashboard's sheet names) so the
        # BE.2 cross-corpus lint doesn't false-positive on coincidental
        # value match against `_DRIFT_NAME` / `_OVERDRAFT_NAME` — the
        # chart-renderer round-trip test isn't asserting anything about
        # the L1 dashboard, just that an arbitrary category label
        # survives render-to-SVG-text. Cf. BE.4 Phase C sweep.
        _render_into_target(page, {
            "categories": ["Cat A", "Cat B", "Cat C"],
            "series": [{"values": [1, 2, 3]}],
        })
        ticks = cast(list[str], page.evaluate(
            """() => Array.from(
                document.querySelectorAll('#barchart-target .barchart-x-axis text'),
            ).map((t) => t.textContent || '')""",
        ))
        browser.close()
    assert "Cat A" in ticks
    assert "Cat B" in ticks
    assert "Cat C" in ticks


def test_barchart_currency_format_applied_to_y_ticks() -> None:
    """y-axis ticks get formatKPIValue with the currency format —
    so a $100M chart reads "$100,000,000.00" not "100000000".
    Tests pass a max value high enough that d3's nice() produces
    a rounded tick at a known number ($150)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": ["A"],
            "series": [{"values": [100]}],
            "format": "currency",
        })
        y_ticks = cast(list[str], page.evaluate(
            """() => Array.from(
                document.querySelectorAll('#barchart-target .barchart-y-axis text'),
            ).map((t) => t.textContent || '')""",
        ))
        browser.close()
    # Every y tick should be currency-formatted (start with $).
    assert all(t.startswith("$") for t in y_ticks if t)


def test_barchart_handles_empty_categories_without_crashing() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": [],
            "series": [{"values": []}],
        })
        bars = page.locator("#barchart-target svg rect.barchart-bar").count()
        svg_count = page.locator("#barchart-target svg").count()
        browser.close()
    # No bars but the SVG itself is rendered — empty-data state
    # shows axes-only chart (intentional; communicates "no data"
    # rather than blank space).
    assert bars == 0
    assert svg_count == 1


def test_barchart_skips_non_numeric_values_safely() -> None:
    """``null`` / ``undefined`` values render as zero-height bars
    rather than crashing the d3 selection."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "categories": ["A", "B", "C"],
            "series": [{"values": [10, None, 30]}],
        })
        bars = page.locator("#barchart-target svg rect.barchart-bar").count()
        browser.close()
    assert bars == 3
