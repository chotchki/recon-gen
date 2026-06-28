"""``capture_app_dashboards`` is a wired placeholder post-DW.

QuickSight removal (Phase DW) deleted the embed-URL screenshot capture
(``ScreenshotHarness`` / ``capture_deployed_app`` / the boto3 embed-URL
minting). The ``recon-gen docs screenshot`` command + its scaffolding
were KEPT so the self-hosted App2 capture slots straight in (operator
directive 2026-06-27). Until that build lands, the capture engine raises
a clear, actionable ``NotImplementedError`` — NOT a silent skip.

This test pins the placeholder behavior. When the App2 capture is built,
it FAILS (the call no longer raises) — that's the signal to replace it
with real capture coverage, so the placeholder can't quietly outlive the
gap it documents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.browser.screenshot import capture_app_dashboards
from recon_gen.common.tree import App
from tests._test_helpers import make_test_config


def test_capture_app_dashboards_is_placeholder(tmp_path: Path) -> None:
    app = App(name="test", cfg=make_test_config())
    with pytest.raises(NotImplementedError, match="not yet built"):
        capture_app_dashboards(app, output_dir=tmp_path)
