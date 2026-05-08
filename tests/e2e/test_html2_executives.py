"""X.2.h.1 — Executives Layer-2 e2e against the HTMX dialect.

Builds the real Executives tree, plugs in a stub fetcher that
returns deterministic data per visual_id, spins the App2 Starlette
server in a thread, and drives Playwright (WebKit, headless)
against ``/dashboards/exec``.

Stub fetcher (not live PG) keeps the test fast + DB-free. The
live-PG variant lands as a CI matrix entry alongside the existing
``e2e-pg-browser`` job — same test shape, fetcher swapped for
``make_tree_db_fetcher(tree_app, cfg)`` with cfg pointing at the
seeded PG.

Asserts on:

- Sheet tabs render (Getting Started / Account Coverage / etc.)
- Visuals auto-load on DOMContentLoaded (X.2.g.1.a polish — no
  Refresh click required)
- KPI cards land in the DOM with the stub fetcher's values
- TextBox rendering (X.2.g.1.a polish — Getting Started sheet
  shows its rich-text welcome content, not blank)
- Filter changes refetch (date_from in URL params lands in the
  fetcher's call list)

Gated by ``QS_GEN_E2E=1`` like every other tests/e2e/ file.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from quicksight_gen.apps.executives.app import build_executives_app
from quicksight_gen.apps.executives.datasets import build_all_datasets
from quicksight_gen.common.browser.helpers import webkit_page
from tests._test_helpers import make_test_config
from tests.e2e._harness_html2 import html2_server


# Y.2.gate.c.11.app2 — `webkit_page` provides Playwright tracing +
# console/network capture on failure (same lifecycle as the QS tests).
# `pytest.importorskip` gate stays so the module skips cleanly when
# Playwright isn't installed.
playwright_sync_api = pytest.importorskip("playwright.sync_api")


# Test cfg with the L2 prefix set explicitly (matches what
# resolve_l2_for_demo would do at CLI time).
_TEST_CFG = make_test_config().with_l2_instance_prefix("spec_example")
_DASHBOARD_ID = "exec"


# Deterministic per-visual stub data — visual_id → response. Tests
# don't have to write a fetcher inline; just look up by id.
def _exec_stub_fetcher(visual_id: str, params: dict[str, str]) -> dict[str, Any]:
    """Stub fetcher matching the shape adapters in ``_data_shape``.

    Returns enough data per Executives visual_id that the d3
    hydrators paint something the test can assert on. Records each
    call into ``_calls_log`` so filter-substitution assertions can
    inspect what URL params landed.
    """
    _calls_log.append((visual_id, dict(params)))
    if "kpi" in visual_id:
        return {"values": [
            {"value": 47, "label": "Open Accounts", "format": "number"},
        ]}
    if "table" in visual_id:
        return {
            "columns": ["account_id", "transfers"],
            "rows": [["acct-A", 10], ["acct-B", 5]],
            "page_offset": 0, "page_size": 2, "total_rows": 2,
        }
    if "bar" in visual_id or "chart" in visual_id:
        return {
            "categories": ["ACH", "Wire", "Check"],
            "values": [100, 200, 50],
            "x_label": "Type", "y_label": "Count",
        }
    # Empty / unknown → empty payload (renders as blank visual).
    return {}


_calls_log: list[tuple[str, dict[str, str]]] = []


@pytest.fixture
def exec_server() -> Iterator[str]:
    """Spin the App2 server with the real Executives tree + the
    stub fetcher. Yields the bound base URL."""
    _calls_log.clear()
    build_all_datasets(_TEST_CFG)  # populate the SQL registry (unused by stub)
    tree_app = build_executives_app(_TEST_CFG)
    assert tree_app.analysis is not None
    primary_sheet = tree_app.analysis.sheets[0]
    with html2_server(
        tree_app=tree_app,
        sheet=primary_sheet,
        data_fetcher=_exec_stub_fetcher,
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="Executives",
    ) as base_url:
        yield base_url


def test_dashboard_landing_renders_with_sheet_tabs(exec_server: str) -> None:
    """Default landing (``/dashboards/exec``) shows tab strip with
    every analysis sheet — proves X.2.e tabs render for a real
    multi-sheet app."""
    with webkit_page() as page:
        page.goto(f"{exec_server}/dashboards/{_DASHBOARD_ID}")
        page.wait_for_load_state("networkidle")
        # Tab strip exists with each sheet's name.
        nav_html = page.locator("nav").inner_html()
        for expected in ("Getting Started", "Account Coverage", "Money Moved"):
            assert expected in nav_html, (
                f"Sheet tab {expected!r} missing from nav — got: {nav_html[:200]}"
            )


def test_getting_started_sheet_renders_text_boxes(exec_server: str) -> None:
    """X.2.g.1.a polish: TextBoxes render via _qs_richtext_to_html.
    Getting Started has 3 text boxes; the page should show non-empty
    content (not blank)."""
    with webkit_page() as page:
        # Default landing IS the Getting Started sheet (first in
        # the analysis order per executives/app.py).
        page.goto(f"{exec_server}/dashboards/{_DASHBOARD_ID}")
        page.wait_for_load_state("networkidle")
        # The page body should contain at least one text-box section
        # with rendered content (spans, anchors, etc. — not just empty).
        body_text = page.locator("body").inner_text()
        assert len(body_text) > 200, (
            f"Getting Started body too thin ({len(body_text)} chars) — "
            f"text boxes likely not rendered. Body preview: "
            f"{body_text[:200]!r}"
        )


def test_account_coverage_visuals_auto_load(exec_server: str) -> None:
    """X.2.g.1.a polish: visuals fetch on DOMContentLoaded — no
    Refresh click required for the initial paint. Asserts the
    KPI's value appears in the DOM after the page loads."""
    with webkit_page() as page:
        page.goto(
            f"{exec_server}/dashboards/{_DASHBOARD_ID}"
            f"/sheets/exec-sheet-account-coverage"
        )
        # Wait for HTMX swap + d3 hydration.
        page.wait_for_function(
            "() => document.querySelector('.kpi-value') !== null",
            timeout=10000,
        )
        kpi_text = page.locator(".kpi-value").first.inner_text()
    # Stub fetcher returns 47 for any visual_id containing "kpi".
    assert "47" in kpi_text, (
        f"KPI didn't render the stub value — got {kpi_text!r}. "
        f"Auto-load may not be firing."
    )


def test_filter_change_refetches_visuals(exec_server: str) -> None:
    """Changing the date filter + clicking Refresh fires a new
    swap with date_from / date_to in the query string. Verifies
    the X.2.d filter form → visual data fetch round-trip."""
    with webkit_page() as page:
        page.goto(
            f"{exec_server}/dashboards/{_DASHBOARD_ID}"
            f"/sheets/exec-sheet-account-coverage"
        )
        page.wait_for_function(
            "() => document.querySelector('.kpi-value') !== null",
            timeout=10000,
        )
        _calls_log.clear()
        # Set a date and click Refresh on the first visual.
        page.fill('input[name="date_from"]', "2030-02-01")
        # X.2.g.1.e — auto-refresh: filling the input triggers a
        # 'change' event that the form's debounced listener catches
        # and broadcasts as 'refresh'. No button click needed.
        # Wait past the 300ms debounce + swap settle.
        page.wait_for_timeout(800)
    # The fetcher should have been called with date_from set.
    assert any(
        params.get("date_from") == "2030-02-01"
        for _vid, params in _calls_log
    ), (
        f"No fetch saw date_from=2030-02-01. Calls: "
        f"{[(vid, dict(p)) for vid, p in _calls_log[:5]]}"
    )


def test_text_box_only_sheet_does_not_emit_filter_form(
    exec_server: str,
) -> None:
    """X.2.g.1.a polish: Getting Started has no data visuals so
    the filter form (date pickers) should be suppressed. Without
    this, users see a vestigial date picker that does nothing."""
    with webkit_page() as page:
        page.goto(f"{exec_server}/dashboards/{_DASHBOARD_ID}")
        page.wait_for_load_state("networkidle")
        # No filter form on Getting Started.
        form_count = page.locator('form#filter-form').count()
    assert form_count == 0, (
        "Filter form should not render on a text-box-only sheet"
    )


def test_account_coverage_sheet_does_emit_filter_form(
    exec_server: str,
) -> None:
    """Inverse of the previous test: sheets WITH data visuals get
    the form. Pins the suppression to the empty-visuals case
    specifically."""
    with webkit_page() as page:
        page.goto(
            f"{exec_server}/dashboards/{_DASHBOARD_ID}"
            f"/sheets/exec-sheet-account-coverage"
        )
        page.wait_for_load_state("networkidle")
        form_count = page.locator('form#filter-form').count()
    assert form_count == 1
