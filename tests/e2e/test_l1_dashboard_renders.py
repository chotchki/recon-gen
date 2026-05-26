"""Browser test: verify the deployed L1 dashboard loads.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright
in the test body; the `qs_driver` fixture lives in conftest). The
sheet-tab assertion derives the expected set from the tree
(`l1_app.analysis.sheets`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest



if TYPE_CHECKING:
    from recon_gen.common.tree import App
    from tests.e2e._drivers import QsEmbedDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def test_l1_dashboard_opens_and_screenshots(
    qs_driver: "QsEmbedDriver", l1_dashboard_id: str, l1_app: "App", tmp_path,
) -> None:
    """The deployed L1 dashboard loads, screenshots, and the Drift sheet
    renders visuals. (`open()` mints + uses the embed URL — its success
    is the "embed URL valid" check the old test had as a micro-test.)"""
    qs_driver.open(l1_dashboard_id)
    png = qs_driver.screenshot(tmp_path / "l1_initial.png")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    qs_driver.goto_sheet("Drift")
    assert qs_driver.visual_titles(), (
        "L1 dashboard 'Drift' sheet rendered no visual titles"
    )


def test_l1_dashboard_lists_all_sheet_tabs(
    qs_driver: "QsEmbedDriver", l1_dashboard_id: str, l1_app: "App",
) -> None:
    """Every sheet the tree declares shows up as a tab on the deployed
    dashboard. Switching the L2 instance changes the names but the
    assertion stays valid."""
    qs_driver.open(l1_dashboard_id)
    expected = {s.name for s in l1_app.analysis.sheets}
    tabs = set(qs_driver.sheet_names())
    missing = expected - tabs
    assert not missing, (
        f"Missing L1 dashboard sheet tabs: {missing}. Found: {sorted(tabs)}"
    )
