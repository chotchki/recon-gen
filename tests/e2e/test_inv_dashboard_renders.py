"""Browser test: verify the deployed Investigation dashboard loads.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright
in the test body; the `qs_driver` fixture lives in conftest). The
sheet-tab assertion derives the expected set from the tree
(`inv_app.analysis.sheets`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest



if TYPE_CHECKING:
    from recon_gen.common.tree import App
    from tests.e2e._drivers import QsEmbedDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def test_inv_dashboard_opens_and_screenshots(
    qs_driver: "QsEmbedDriver", inv_dashboard_id: str, inv_app: "App", tmp_path,
) -> None:
    """The deployed Investigation dashboard loads, screenshots, and a
    data sheet renders visuals. (`open()` mints + uses the embed URL.)"""
    qs_driver.open(inv_dashboard_id)
    png = qs_driver.screenshot(tmp_path / "investigation_initial.png")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    qs_driver.goto_sheet("Recipient Fanout")
    assert qs_driver.visual_titles(), (
        "Investigation 'Recipient Fanout' sheet rendered no visual titles"
    )


def test_inv_dashboard_lists_all_sheet_tabs(
    qs_driver: "QsEmbedDriver", inv_dashboard_id: str, inv_app: "App",
) -> None:
    """Every sheet the tree declares shows up as a tab on the deployed
    dashboard."""
    qs_driver.open(inv_dashboard_id)
    expected = {s.name for s in inv_app.analysis.sheets}
    tabs = set(qs_driver.sheet_names())
    missing = expected - tabs
    assert not missing, (
        f"Missing Investigation sheet tabs: {missing}. Found: {sorted(tabs)}"
    )
