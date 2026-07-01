"""DY.9 — real coverage for the self-hosted App2 screenshot capture.

Replaces the ``tests/unit/test_screenshot_placeholder.py`` pin (which
asserted ``capture_app_dashboards`` raised ``NotImplementedError``). The
QS embed-URL engine was removed in Phase DW; DY.9 rebuilt capture against
the self-hosted renderer — spin the real ``recon-gen dashboards``
Starlette stack on an ephemeral port, drive WebKit sheet-by-sheet,
screenshot each.

This drives the WHOLE path end-to-end against the seeded live DB: build
the app via the production ``build_real_app`` (registers datasets), serve
it, walk every sheet, and assert one NON-EMPTY PNG landed per sheet. The
executives app is the target — it's the smallest tree, so the walk stays
quick while still exercising the server-in-thread + pool-in-loop +
per-sheet navigation machinery.

Runs in the ``app2_browser`` layer (WebKit + seeded DB both present). The
capture spins its OWN uvicorn + pool, so it needs the seeded ``cfg`` but
NOT the ``DashboardDriver`` fixtures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._marks import Need, Tier, needs, tier

if TYPE_CHECKING:
    from pathlib import Path

    from recon_gen.common.config import Config


pytestmark = [
    pytest.mark.e2e,
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]


def test_capture_writes_one_non_empty_png_per_sheet(
    cfg: "Config", tmp_path: "Path",
) -> None:
    """``capture_app_dashboards`` produces exactly one non-empty PNG per
    sheet of the served app, keyed by Sheet object ref."""
    if not cfg.db.url:
        # Config-absence (non-suspect under the example_l2 gate): the
        # capture serves the live DB, same contract as the driver fixtures.
        pytest.skip("no cfg.db.url — the screenshot capture reads the live DB")

    from recon_gen.cli._html_serve import build_real_app
    from recon_gen.common.browser.screenshot import (  # typing-smell: ignore[no-playwright-leak]: capture_app_dashboards is the PRODUCTION capture engine under test, NOT a Playwright-driving helper — the test never touches Page/webkit_page, it asserts the emitted PNGs
        capture_app_dashboards,
    )
    from recon_gen.common.l2 import default_l2_instance
    from recon_gen.common.theme import resolve_l2_theme

    instance = default_l2_instance()
    tree_app, _landing = build_real_app("executives", cfg, instance)
    assert tree_app.analysis is not None  # build_real_app raises otherwise
    sheets = list(tree_app.analysis.sheets)

    results = capture_app_dashboards(
        tree_app,
        cfg=cfg,
        dashboard_id="executives",
        theme=resolve_l2_theme(instance),
        output_dir=tmp_path,
        # Tiny settles — App2's d3 hydration lands right after networkidle;
        # the QS-era 10s/8s defaults are wasted wall-clock in a test.
        viewport=(1000, 700),
        initial_settle_ms=300,
        per_sheet_settle_ms=150,
        headless=True,
    )

    # One PNG per sheet, keyed by the Sheet object refs we walked.
    assert set(results.keys()) == set(sheets), (
        "capture must return a Path for every sheet of the served app"
    )
    for sheet, path in results.items():
        assert path.exists(), f"sheet {sheet.name!r} → {path} was not written"
        assert path.stat().st_size > 0, (
            f"sheet {sheet.name!r} → {path} is a zero-byte PNG "
            f"(render produced no frame)"
        )
        assert path.name == f"{sheet.sheet_id}.png", (
            f"sheet {sheet.name!r} PNG should be named <sheet_id>.png, "
            f"got {path.name}"
        )
