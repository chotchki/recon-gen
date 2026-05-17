"""Y.2.app2.cde.l2ft-wiring.c — L2 Flow Tracing Layer-2 e2e against the
HTMX dialect.

Builds the real L2FT tree, plugs in a stub fetcher returning deterministic
data per visual_id, spins the App2 Starlette server via
``App2Driver.serving(...)``, and drives Playwright (WebKit, headless)
against ``/dashboards/l2ft``.

Asserts on:

- Sheet tabs render (Getting Started / Rails / Chains / Transfer Templates / …)
- The Rails sheet's filter bar carries the three MULTI_SELECT pushdown
  dropdowns auto-derived from the tree (``<select multiple
  name="param_pL2ftRail">`` + ``pL2ftStatus`` + ``pL2ftBundle``) — i.e.
  ``make_filter_specs_for_sheet`` (Y.2.app2.cde.l2ft-wiring.b) fired and
  the route rendered the specs.
- The Chains sheet carries its own dropdowns (``pL2ftChainsChain`` /
  ``pL2ftChainsCompletion``) — even if vacuous for the spec_example L2.
- Selecting a value in the rail multi-select re-fetches the sheet's
  visuals with ``param_pL2ftRail`` in the query string — the repeated-key
  shape ``_sql_executor``'s multi-valued expansion consumes.

Ported onto ``DashboardDriver`` (X.2.q.3) — driver verbs handle navigation
+ filter writes; ``driver.page`` is the escape hatch for App2-internal
wire-shape assertions (param-name attributes, fetcher's calls log).

Stub fetcher (not live PG) keeps the test fast + DB-free, same shape as
``test_html2_executives.py``. The live-PG variant is the ``app2`` chain
layer (``./run_tests.sh up_to=app2 …``) which runs this file with
``RECON_GEN_E2E=1`` against a seeded container.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from recon_gen.apps.l1_dashboard._l2 import default_l2_instance
from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
from recon_gen.apps.l2_flow_tracing.datasets import (
    build_all_l2_flow_tracing_datasets,
)
from tests._test_helpers import make_test_config
from tests.e2e._drivers import App2Driver


_TEST_INSTANCE = default_l2_instance()
# Z.C — db_table_prefix is required on cfg. Pin to "spec_example" since
# default_l2_instance() returns the spec_example fixture.
_TEST_CFG = make_test_config(db_table_prefix="spec_example")
_DASHBOARD_ID = "l2ft"


_calls_log: list[tuple[str, dict[str, list[str]]]] = []


def _l2ft_stub_fetcher(
    visual_id: str, params: dict[str, list[str]],
) -> dict[str, Any]:
    """Deterministic per-visual-kind stub. Records every call into
    ``_calls_log`` so the dropdown-selection assertion can inspect what
    URL params landed. ``params`` is the URL multi-dict."""
    _calls_log.append((visual_id, dict(params)))
    vid = visual_id.lower()
    if "kpi" in vid:
        return {"values": [
            {"value": 12, "label": "Transactions", "format": "number"},
        ]}
    if "table" in vid:
        return {
            "columns": ["transaction_id", "rail_name", "status"],
            "rows": [["tx-1", "rail-a", "posted"], ["tx-2", "rail-b", "pending"]],
            "page_offset": 0, "page_size": 2, "total_rows": 2,
        }
    if "bar" in vid or "chart" in vid:
        return {
            "categories": ["rail-a", "rail-b"], "values": [3, 5],
            "x_label": "Rail", "y_label": "Count",
        }
    if "sankey" in vid:
        return {"nodes": [], "links": []}
    return {}



@pytest.fixture
def l2ft_driver() -> Iterator[App2Driver]:
    """``App2Driver`` aimed at the L2FT app."""
    _calls_log.clear()
    build_all_l2_flow_tracing_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_l2_flow_tracing_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    assert tree_app.analysis is not None
    landing_sheet = tree_app.analysis.sheets[0]  # Getting Started
    with App2Driver.serving(
        tree_app=tree_app, sheet=landing_sheet,
        data_fetcher=_l2ft_stub_fetcher,
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="L2 Flow Tracing",
    ) as driver:
        yield driver


def test_l2ft_dashboard_landing_renders_with_sheet_tabs(
    l2ft_driver: App2Driver,
) -> None:
    driver = l2ft_driver
    driver.open(_DASHBOARD_ID)
    names = driver.sheet_names()
    for expected in ("Getting Started", "Rails", "Chains", "Transfer Templates"):
        assert expected in names, (
            f"Sheet tab {expected!r} missing from sheet_names() — got {names}"
        )


def test_l2ft_rails_sheet_renders_three_single_select_dropdowns(
    l2ft_driver: App2Driver,
) -> None:
    """AA.A.3 — the Rails sheet's filter bar carries the rail / status /
    bundle SINGLE_SELECT dropdowns the tree-walk auto-derived, each
    rendered as a ``<select>`` (no ``multiple`` attr) with options.

    Pre-AA.A these were multi-select on the back of X.2.t.2's
    sentinel-guard pattern (forced by AWS's 32-element default cap);
    AA.A.3 flipped them to scalar-default + ``= <<$p>>`` push down,
    so the App2 widget renders single-select."""
    driver = l2ft_driver
    driver.open(_DASHBOARD_ID, sheet="Rails")
    page = driver.page
    for param in ("pL2ftRail", "pL2ftStatus", "pL2ftBundle"):
        sel = page.locator(f'select[name="param_{param}"]')
        assert sel.count() == 1, f"missing <select name=param_{param}>"
        assert sel.first.evaluate("el => el.multiple") is False, (
            f"param_{param} should be a single-select post-AA.A.3"
        )
        assert sel.locator("option").count() >= 1, (
            f"param_{param} has no options"
        )


def test_l2ft_chains_sheet_renders_its_dropdowns(
    l2ft_driver: App2Driver,
) -> None:
    """The Chains sheet carries its own auto-derived dropdowns. spec_example
    declares no chains, so the option lists may be empty — what matters is
    the ``<select>`` widgets are present (wiring proof) and rendered as
    single-select post-AA.A.3."""
    driver = l2ft_driver
    driver.open(_DASHBOARD_ID, sheet="Chains")
    page = driver.page
    for param in ("pL2ftChainsChain", "pL2ftChainsCompletion"):
        sel = page.locator(f'select[name="param_{param}"]')
        assert sel.count() == 1, f"missing <select name=param_{param}>"
        assert sel.first.evaluate("el => el.multiple") is False


def test_l2ft_rail_dropdown_selection_refetches_with_param(
    l2ft_driver: App2Driver,
) -> None:
    """Selecting a value in the rail single-select fires a debounced
    refresh that re-fetches the sheet's visuals with ``param_pL2ftRail``
    in the query string — post-AA.A.3 the wire shape is a SINGLE value
    (not the repeated-key list form the multi-valued executor consumed).

    Drives the single-select via ``driver.page.select_option`` (the
    ``param_X`` attr-name shape isn't reachable via
    ``driver.pick_filter(label, ...)``); asserts on ``_calls_log`` (the
    fetcher's recorded URL params) for the wire-shape proof."""
    driver = l2ft_driver
    driver.open(_DASHBOARD_ID, sheet="Rails")
    page = driver.page
    # Wait past the initial auto-load fetch before clearing the log.
    page.wait_for_timeout(400)
    _calls_log.clear()
    # Select the first non-default option. ``select_option`` fires a
    # change event that the form's debounced listener broadcasts as
    # refresh.
    page.select_option('select[name="param_pL2ftRail"]', index=1)
    page.wait_for_timeout(900)  # 300ms debounce + swap settle
    saw_rail_param = [
        params for _vid, params in _calls_log
        if params.get("param_pL2ftRail")
    ]
    assert saw_rail_param, (
        f"no fetch carried param_pL2ftRail after selecting a rail. "
        f"Calls: {[(v, dict(p)) for v, p in _calls_log[:8]]}"
    )
    # Post-AA.A.3 the wire still carries a list (URL params always
    # parse as lists), but with exactly one element — the picked value.
    assert all(len(p["param_pL2ftRail"]) == 1 for p in saw_rail_param)
