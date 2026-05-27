"""Unit tests for ``ScreenshotHarness``'s pure-Python parts.

Real screenshot capture requires a deployed dashboard + Playwright
Page; those land when L.2's tree-to-files plumbing exists. These
unit tests cover the file-naming + tree-walking + URL-fragment-
construction logic that doesn't need a real Page.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from recon_gen.common.ids import ParameterName, SheetId
from recon_gen.common.tree import (
    Analysis,
    App,
    IntegerParam,
    Sheet,
)
from recon_gen.common.browser import ScreenshotHarness
from tests._test_helpers import make_test_config


_CFG = make_test_config()


def _empty_app() -> App:
    app = App(name="test", cfg=_CFG)
    return app


class TestConstruction:
    def test_creates_output_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "screenshots"
        assert not out.exists()
        ScreenshotHarness(_empty_app(), page=MagicMock(), output_dir=out)
        assert out.exists()

    def test_existing_output_dir_ok(self, tmp_path: Path) -> None:
        out = tmp_path / "screenshots"
        out.mkdir()
        # No exception on double-create
        ScreenshotHarness(_empty_app(), page=MagicMock(), output_dir=out)

    def test_no_analysis_capture_all_sheets_raises(self, tmp_path: Path) -> None:
        h = ScreenshotHarness(
            _empty_app(), page=MagicMock(), output_dir=tmp_path,
        )
        with pytest.raises(ValueError, match="has no Analysis"):
            h.capture_all_sheets()

    def test_capture_with_state_requires_embed_url(self, tmp_path: Path) -> None:
        app = _empty_app()
        app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        h = ScreenshotHarness(
            app, page=MagicMock(), output_dir=tmp_path,
        )
        with pytest.raises(ValueError, match="embed_url"):
            h.capture_with_state(parameter_values={})


class TestSafeIdSanitization:
    def test_passes_through_kebab_case(self, tmp_path: Path) -> None:
        h = ScreenshotHarness(
            _empty_app(), page=MagicMock(), output_dir=tmp_path,
        )
        assert h._safe_id("inv-sheet-account-network") == "inv-sheet-account-network"

    def test_replaces_slashes(self, tmp_path: Path) -> None:
        h = ScreenshotHarness(
            _empty_app(), page=MagicMock(), output_dir=tmp_path,
        )
        assert h._safe_id("foo/bar") == "foo_bar"

    def test_replaces_colons(self, tmp_path: Path) -> None:
        h = ScreenshotHarness(
            _empty_app(), page=MagicMock(), output_dir=tmp_path,
        )
        assert h._safe_id("arn:aws:foo") == "arn_aws_foo"


class TestCaptureWithStateUrlConstruction:
    """The URL fragment construction is the load-bearing part of
    capture_with_state — verify parameter writes encode correctly.

    `capture_with_state` runs through `tests.e2e.browser_helpers`
    (`wait_for_dashboard_loaded`, `click_sheet_tab`), which lazy-import
    `playwright.sync_api`. CI's `[dev]` extras don't pull in playwright
    (it lives under `[e2e]`), so these tests skip cleanly when
    playwright isn't importable. Locally, install `[e2e]` to run them.
    """

    @pytest.fixture(autouse=True)
    def _require_playwright(self) -> None:
        pytest.importorskip("playwright")

    def test_url_fragment_built_from_parameter_object_refs(self, tmp_path: Path) -> None:
        app = _empty_app()
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        sigma = analysis.add_parameter(IntegerParam(
            name=ParameterName("pSigma"), default=[2],
        ))
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        page = MagicMock()
        h = ScreenshotHarness(
            app, page=page, output_dir=tmp_path,
            embed_url="https://test.example/dashboard",
        )
        # Run capture; the page mock just records what URL we hit.
        h.capture_with_state(parameter_values={sigma: 99}, suffix="hi")
        # First page.goto call carries the URL we built
        first_call = page.goto.call_args_list[0]
        url = first_call.args[0]
        assert url.startswith("https://test.example/dashboard#")
        assert "p.pSigma=99" in url

    def test_url_value_is_url_encoded(self, tmp_path: Path) -> None:
        app = _empty_app()
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        anchor = analysis.add_parameter(IntegerParam(
            name=ParameterName("pAnchor"),
        ))
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        page = MagicMock()
        h = ScreenshotHarness(
            app, page=page, output_dir=tmp_path,
            embed_url="https://test.example/dashboard",
        )
        # Pass a value with characters that need URL encoding
        h.capture_with_state(
            parameter_values={anchor: "Acme & Co (cust-12)"},
        )
        url = page.goto.call_args_list[0].args[0]
        # & and parens should be percent-encoded in the parameter
        # value to avoid breaking the fragment parser.
        assert "Acme%20%26%20Co" in url
