"""Playwright JS unit tests for diagram.js coverage + trainer overlays
(X.4.c.7).

Pattern mirrors ``test_bootstrap.py`` (X.2.a.2): a static HTML fixture
sets ``window.__test_mode__`` BEFORE diagram.js loads, so the
test-mode-conditional export installs the per-feature helpers on
``window.__diagram_internals__``. Tests load the fixture via
``file://``, drive the helpers via ``page.evaluate``, then read the
mutated SVG state back.

Why a static fixture (no Studio server): keeps the JS-unit tier
free of the Starlette + DB stack — the route + chrome integration
is already covered by ``tests/unit/test_studio_diagram_coverage_route.py``.
This file locks the renderer-specific behavior of `_stampCoverage`
and `_stampTrainer` against a controlled SVG.
"""

from __future__ import annotations

from pathlib import Path

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")


_FIXTURE = Path(__file__).parent / "fixtures" / "diagram_test_harness.html"


def test_stamp_coverage_marks_present_node_with_data_attrs() -> None:
    """`_stampCoverage(svg, payload)` should stamp `data-presence='yes'`
    + `data-row-count='N'` per matched node, and inject a <title> with
    "<id> · 12,304 rows" for the native hover tooltip."""
    fixture_url = f"file://{_FIXTURE.resolve()}"
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(fixture_url)
            page.wait_for_function(
                "() => typeof window.__diagram_internals__ === 'object'",
                timeout=5000,
            )
            page.evaluate(
                """() => {
                const svg = document.getElementById('topology-svg');
                const cov = {
                    nodes: {
                        'role__CustomerLedger': {present: true, count: 12304},
                        'role__CustomerSubledger': {present: false, count: 0},
                    },
                    chain_edges: {},
                };
                window.__diagram_internals__._stampCoverage(svg, cov);
            }"""
            )

            cl = page.evaluate(
                """() => {
                const g = document.querySelector('g.node[data-id="role__CustomerLedger"]');
                return {
                    presence: g.getAttribute('data-presence'),
                    count: g.getAttribute('data-row-count'),
                    title: g.querySelector('title').textContent,
                };
            }"""
            )
            assert cl["presence"] == "yes"
            assert cl["count"] == "12304"
            assert "12,304 rows" in cl["title"]
            assert "CustomerLedger" in cl["title"]

            cs = page.evaluate(
                """() => {
                const g = document.querySelector('g.node[data-id="role__CustomerSubledger"]');
                return {
                    presence: g.getAttribute('data-presence'),
                    count: g.getAttribute('data-row-count'),
                    title: g.querySelector('title').textContent,
                };
            }"""
            )
            assert cs["presence"] == "no"
            assert cs["count"] == "0"
            assert "no data" in cs["title"]

            # Unmentioned node: untouched.
            rl_presence = page.evaluate(
                """() => document
                    .querySelector('g.node[data-id="rail__ExternalRailInbound"]')
                    .getAttribute('data-presence')"""
            )
            assert rl_presence is None
        finally:
            browser.close()


def test_stamp_trainer_marks_planted_nodes_and_appends_to_title() -> None:
    """`_stampTrainer(svg, payload)` should stamp `data-trainer-kinds`
    (comma-joined sorted kind list) and append `[plants: drift×2,
    overdraft×1]` to the existing <title>.
    """
    fixture_url = f"file://{_FIXTURE.resolve()}"
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(fixture_url)
            page.wait_for_function(
                "() => typeof window.__diagram_internals__ === 'object'",
                timeout=5000,
            )
            page.evaluate(
                """() => {
                const svg = document.getElementById('topology-svg');
                const tr = {
                    nodes: {
                        'role__CustomerSubledger': {drift: 2, overdraft: 1},
                        'rail__ExternalRailInbound': {drift: 1},
                    },
                };
                window.__diagram_internals__._stampTrainer(svg, tr);
            }"""
            )

            cs = page.evaluate(
                """() => {
                const g = document.querySelector('g.node[data-id="role__CustomerSubledger"]');
                return {
                    kinds: g.getAttribute('data-trainer-kinds'),
                    title: g.querySelector('title').textContent,
                };
            }"""
            )
            assert cs["kinds"] == "drift,overdraft"
            assert "[plants: drift×2, overdraft×1]" in cs["title"]
            assert "CustomerSubledger" in cs["title"]

            rl_kinds = page.evaluate(
                """() => document
                    .querySelector('g.node[data-id="rail__ExternalRailInbound"]')
                    .getAttribute('data-trainer-kinds')"""
            )
            assert rl_kinds == "drift"

            cl_kinds = page.evaluate(
                """() => document
                    .querySelector('g.node[data-id="role__CustomerLedger"]')
                    .getAttribute('data-trainer-kinds')"""
            )
            assert cl_kinds is None
        finally:
            browser.close()


def test_stamp_trainer_idempotent_does_not_double_append_title() -> None:
    """Re-applying `_stampTrainer` with the same payload should NOT
    double-append the [plants: ...] block to <title>. The handler
    checks for the marker before appending — important because the
    chrome's toggle-on/off cycle re-runs apply() on each toggle.
    """
    fixture_url = f"file://{_FIXTURE.resolve()}"
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(fixture_url)
            page.wait_for_function(
                "() => typeof window.__diagram_internals__ === 'object'",
                timeout=5000,
            )
            page.evaluate(
                """() => {
                const svg = document.getElementById('topology-svg');
                const tr = {nodes: {'role__CustomerLedger': {drift: 1}}};
                const fn = window.__diagram_internals__._stampTrainer;
                fn(svg, tr);
                fn(svg, tr);
                fn(svg, tr);
            }"""
            )

            title = page.evaluate(
                """() => document
                    .querySelector('g.node[data-id="role__CustomerLedger"] title')
                    .textContent"""
            )
            assert title.count("[plants:") == 1
        finally:
            browser.close()
