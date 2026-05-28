"""BO.3 — Playwright unit test for the Sankey d3 renderer's empty-state.

Same fixture pattern as test_render_barchart / test_render_kpi.
Covers the empty-data branch added in BO.3: when ``data.nodes`` or
``data.links`` is empty, renderSankey emits a centered "no flows match"
message inside the target instead of an empty SVG. Pre-BO.3 d3-sankey on
an empty graph rendered a blank white card that read as a broken visual
(cold-read F3 flagged exactly that on the L2FT Multi-Leg Flow Sankey).

Populated-Sankey coverage stays in the e2e layer (real DB + d3-sankey
layout exercises the bidirectional / fan-in / fan-out shapes); this file
only pins the empty-state contract, which is the one the cold-read
flagged.
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


def _render_empty(page: Any) -> None:
    """Append a target div + call renderSankey on an empty payload.

    Mirrors the bootstrap.js dispatch path: a Section with the visual_id
    attribute already exists in the harness; this just injects the
    inner ``<div>`` the renderer paints into.
    """
    page.evaluate(
        """() => {
            const section = document.querySelector(
                'section[data-visual-id="viz-money-trail"]',
            );
            const target = document.createElement('div');
            target.id = 'sankey-target';
            section.appendChild(target);
            window.__bootstrap_internals__.renderSankey(
                target, { nodes: [], links: [] }, 'viz-money-trail',
            );
        }""",
    )


def test_sankey_empty_nodes_renders_empty_state_message() -> None:
    """``{nodes: [], links: []}`` paints an explicit empty-state message,
    not an empty SVG. Pinning the marker class + message text so the
    e2e layer's freshness oracle (which the operator sees) can pick it
    up if it ever has to render against zero rows."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_empty(page)
        empty_count = page.locator(
            "#sankey-target .sankey-empty-state",
        ).count()
        svg_count = page.locator("#sankey-target svg").count()
        message = cast(str, page.evaluate(
            """() => document.querySelector(
                '#sankey-target .sankey-empty-state',
            )?.textContent || ''""",
        ))
        browser.close()
    assert empty_count == 1
    assert svg_count == 0
    assert "No flows match" in message


def test_sankey_empty_links_renders_empty_state_message() -> None:
    """``{nodes: [{name: 'A'}], links: []}`` also paints the empty-state
    message. A graph with nodes but no edges is degenerate (d3-sankey
    can't lay it out) and visually indistinguishable from a render bug;
    the empty-state copy disambiguates."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        page.evaluate(
            """() => {
                const section = document.querySelector(
                    'section[data-visual-id="viz-money-trail"]',
                );
                const target = document.createElement('div');
                target.id = 'sankey-target';
                section.appendChild(target);
                window.__bootstrap_internals__.renderSankey(
                    target,
                    { nodes: [{name: 'A'}], links: [] },
                    'viz-money-trail',
                );
            }""",
        )
        empty_count = page.locator(
            "#sankey-target .sankey-empty-state",
        ).count()
        browser.close()
    assert empty_count == 1
