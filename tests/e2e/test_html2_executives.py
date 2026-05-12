"""X.2.h.1 — Executives Layer-2 e2e against the HTMX dialect.

Builds the real Executives tree, plugs in a stub fetcher that returns
deterministic data per visual_id, spins the App2 Starlette server via
``App2Driver.serving(...)``, and drives Playwright (WebKit, headless)
against ``/dashboards/exec``.

Stub fetcher (not live PG) keeps the test fast + DB-free. The live-PG
variant is ``test_html2_executives_live.py`` — same shape with
``make_live_db_fetcher_for_app`` plumbed in.

Assertions split between ``DashboardDriver`` verbs (``sheet_names`` for
the per-app tab strip) and ``driver.page`` for App2-internal wire shape
(filter-form emit/suppress per-sheet, fetcher's calls log carrying
``date_from`` after a refresh, the dev-log POST → server-log capture
path). The KPI-auto-load smoke check that lived here is dropped — it's
covered by ``test_dashboard_driver::test_showcase_kpi_renders_a_value``
against the smoke app, same shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from quicksight_gen.apps.executives.app import build_executives_app
from quicksight_gen.apps.executives.datasets import build_all_datasets
from quicksight_gen.common.env_keys import QS_GEN_RUN_DIR
from tests._test_helpers import make_test_config
from tests.e2e._drivers import App2Driver


# Test cfg with the L2 prefix set explicitly (matches what
# resolve_l2_for_demo would do at CLI time).
_TEST_CFG = make_test_config().with_l2_instance_prefix("spec_example")
_DASHBOARD_ID = "exec"


# Deterministic per-visual stub data — visual_id → response. Tests don't
# have to write a fetcher inline; just look up by id.
def _exec_stub_fetcher(
    visual_id: str, params: dict[str, list[str]],
) -> dict[str, Any]:
    """Stub fetcher matching the shape adapters in ``_data_shape``.

    Returns enough data per Executives visual_id that the d3 hydrators
    paint something the test can assert on. Records each call into
    ``_calls_log`` so filter-substitution assertions can inspect what
    URL params landed. ``params`` is the URL multi-dict (a key can
    repeat); the assertions below collapse to scalar.
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


_calls_log: list[tuple[str, dict[str, list[str]]]] = []


@pytest.fixture
def exec_driver() -> Iterator[App2Driver]:
    """``App2Driver`` aimed at the real Executives tree + the stub
    fetcher."""
    _calls_log.clear()
    build_all_datasets(_TEST_CFG)  # populate the SQL registry (unused by stub)
    tree_app = build_executives_app(_TEST_CFG)
    assert tree_app.analysis is not None
    primary_sheet = tree_app.analysis.sheets[0]
    with App2Driver.serving(
        tree_app=tree_app, sheet=primary_sheet,
        data_fetcher=_exec_stub_fetcher,
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="Executives",
    ) as driver:
        yield driver


def test_dashboard_landing_renders_with_sheet_tabs(
    exec_driver: App2Driver,
) -> None:
    """Default landing (``/dashboards/exec``) shows tab strip with every
    analysis sheet — proves X.2.e tabs render for a real multi-sheet
    app, named the way the executives tree declares them."""
    exec_driver.open(_DASHBOARD_ID)
    names = exec_driver.sheet_names()
    for expected in ("Getting Started", "Account Coverage", "Money Moved"):
        assert expected in names, (
            f"Sheet tab {expected!r} missing — got {names}"
        )


def test_getting_started_sheet_renders_text_boxes(
    exec_driver: App2Driver,
) -> None:
    """X.2.g.1.a polish: TextBoxes render via _qs_richtext_to_html.
    Getting Started has 3 text boxes; the page should show non-empty
    content (not blank).

    App2-internal: ``driver.page`` for the body-text inspection — there's
    no driver verb for "is the rendered prose non-trivial"."""
    # Default landing IS the Getting Started sheet (first in the analysis
    # order per executives/app.py).
    exec_driver.open(_DASHBOARD_ID)
    body_text = exec_driver.page.locator("body").inner_text()
    assert len(body_text) > 200, (
        f"Getting Started body too thin ({len(body_text)} chars) — text "
        f"boxes likely not rendered. Body preview: {body_text[:200]!r}"
    )


def test_filter_change_refetches_visuals(
    exec_driver: App2Driver,
) -> None:
    """Setting the date filter fires an auto-refresh that re-fetches the
    sheet's visuals with ``date_from`` in the query string. Verifies the
    X.2.d filter form → visual data fetch round-trip.

    ``driver.set_date_range`` blocks on the App2 refetch (per the App2
    write-verb contract); the wire-shape assertion (URL key landed)
    needs the fetcher's ``_calls_log`` — App2-internal."""
    exec_driver.open(
        _DASHBOARD_ID, sheet="Account Coverage",
    )
    _calls_log.clear()
    exec_driver.set_date_range("2030-02-01", None)
    # The fetcher should have been called with date_from set.
    assert any(
        params.get("date_from") == ["2030-02-01"]
        for _vid, params in _calls_log
    ), (
        f"No fetch saw date_from=2030-02-01. Calls: "
        f"{[(vid, dict(p)) for vid, p in _calls_log[:5]]}"
    )


def test_text_box_only_sheet_does_not_emit_filter_form(
    exec_driver: App2Driver,
) -> None:
    """X.2.g.1.a polish: Getting Started has no data visuals so the
    filter form (date pickers) should be suppressed. Without this, users
    see a vestigial date picker that does nothing.

    App2-internal: filter-form emission is an App2 layout decision; no
    cross-renderer driver verb."""
    exec_driver.open(_DASHBOARD_ID)
    form_count = exec_driver.page.locator('form#filter-form').count()
    assert form_count == 0, (
        "Filter form should not render on a text-box-only sheet"
    )


def test_account_coverage_sheet_does_emit_filter_form(
    exec_driver: App2Driver,
) -> None:
    """Inverse of the previous test: sheets WITH data visuals get the
    form. Pins the suppression to the empty-visuals case specifically."""
    exec_driver.open(
        _DASHBOARD_ID, sheet="Account Coverage",
    )
    form_count = exec_driver.page.locator('form#filter-form').count()
    assert form_count == 1


# Y.2.gate.c.11.app2-server-logs — verify the full dev-log path:
# JS in browser → POST /log → server's _DEVLOG.info → uvicorn's logging
# chain → harness FileHandler → $QS_GEN_RUN_DIR/app2/server.log.

def test_dev_log_events_land_in_server_log() -> None:
    """Spin a separate App2 server (own driver) with `dev_log=True` so
    the page emits the `<meta name="dev-log">` tag that activates
    dev_log.js. The script POSTs `dev-log:ready` immediately on page
    load (and HTMX events thereafter). Assert the captured server log
    file contains the forwarded event.

    Skips when `QS_GEN_RUN_DIR` isn't set — there's no log file to
    assert against in legacy mode (direct pytest invocation). Runs
    under the runner (`./run_tests.sh up_to=app2 ...`).
    """
    from pathlib import Path
    run_dir_path = QS_GEN_RUN_DIR.get_or_none()
    if run_dir_path is None:
        pytest.skip(
            "QS_GEN_RUN_DIR unset — server.log capture is runner-mode only"
        )
    log_path = Path(run_dir_path) / "app2" / "server.log"

    build_all_datasets(_TEST_CFG)
    tree_app = build_executives_app(_TEST_CFG)
    assert tree_app.analysis is not None
    primary_sheet = tree_app.analysis.sheets[0]
    with App2Driver.serving(
        tree_app=tree_app, sheet=primary_sheet,
        data_fetcher=_exec_stub_fetcher,
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="Executives",
        dev_log=True,
    ) as driver:
        driver.open(_DASHBOARD_ID)
        # dev_log.js sends dev-log:ready synchronously on load, and the
        # keepalive flag means the fetch can outlive the navigation.
        # Give the server a moment to land the POST + flush through the
        # FileHandler.
        driver.page.wait_for_timeout(300)

    # Server context torn down → harness has detached + closed the
    # FileHandler. Read the log fresh.
    contents = log_path.read_text(encoding="utf-8")
    assert "DEV-LOG" in contents, (
        f"Expected 'DEV-LOG' in {log_path} — got {len(contents)} bytes; "
        f"first 500: {contents[:500]!r}"
    )
    assert "dev-log:ready" in contents, (
        f"Expected the dev_log.js initial 'dev-log:ready' event in "
        f"{log_path}; first 500: {contents[:500]!r}"
    )
