"""X.2.l.4.a — filter-widget infrastructure (page-shell CDN tags +
``wireFilterWidgets`` init shim).

This stage is infra-only: the renderer doesn't yet emit any
``data-widget``-tagged markup (that's X.2.l.4.b), so these tests
verify the *plumbing* —

  * the page shell pulls Tom Select / Flatpickr / noUiSlider from
    the CDN (CSS + JS), in the right cascade position (vendor CSS
    after ``output.css`` but before the per-instance ``:root`` theme
    ``<style>`` so the theme override still wins; vendor JS at the
    bottom of ``<head>`` next to htmx / d3);
  * ``bootstrap.js`` defines ``wireFilterWidgets``, calls it from the
    page-load + ``htmx:afterSwap`` hooks, and exposes it on the
    test-mode internals export.

Behavioural round-trips (open the widget, pick a value, assert the
fetch re-fires with the right query param) live in the X.2.l.4.d
Playwright suite.
"""

from __future__ import annotations

from pathlib import Path

from tests._test_helpers import make_test_config
from quicksight_gen.common.html.render import emit_dashboards_list, emit_html
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import KPI


_BOOTSTRAP_JS_PATH = (
    Path(__file__).parents[2]
    / "src" / "quicksight_gen" / "common" / "html" / "assets" / "js"
    / "bootstrap.js"
)

_VENDOR_CSS_MARKERS = (
    "tom-select@2.3.1/dist/css/tom-select.min.css",
    "flatpickr@4.6.13/dist/flatpickr.min.css",
    "nouislider@15.7.1/dist/nouislider.min.css",
)
_VENDOR_JS_MARKERS = (
    "tom-select@2.3.1/dist/js/tom-select.complete.min.js",
    "flatpickr@4.6.13/dist/flatpickr.min.js",
    "nouislider@15.7.1/dist/nouislider.min.js",
)


def _build_app() -> tuple[App, Sheet]:
    app = App(name="fw-test", cfg=make_test_config())
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="fw-test-analysis", name="FW Test",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("fw-sheet"), name="FW",
        title="FW Sheet", description="x",
    ))
    sheet.visuals.append(
        KPI(title="K", subtitle="t", visual_id=VisualId("v-k")),
    )
    return app, sheet


def _shells() -> list[str]:
    app, sheet = _build_app()
    return [
        emit_html(app, sheet, dashboard_id="x"),
        emit_dashboards_list([("x", "X")]),
    ]


# ---------------------------------------------------------------------------
# Page shell carries the vendor assets
# ---------------------------------------------------------------------------


def test_page_shell_pulls_filter_widget_css() -> None:
    for shell in _shells():
        for marker in _VENDOR_CSS_MARKERS:
            assert marker in shell, marker


def test_page_shell_pulls_filter_widget_js() -> None:
    for shell in _shells():
        for marker in _VENDOR_JS_MARKERS:
            assert marker in shell, marker


def test_existing_cdn_libs_still_present() -> None:
    """Folding htmx / d3 / d3-sankey into the vendor-JS block must not
    drop any of them."""
    for shell in _shells():
        assert "htmx.org@2.0.3" in shell
        assert "d3@7.9.0/dist/d3.min.js" in shell
        assert "d3-sankey@0.12.3" in shell


def test_vendor_css_before_theme_style_before_vendor_js() -> None:
    """Cascade order: ``output.css`` → vendor widget CSS → per-instance
    ``:root`` theme override → vendor widget JS. The widget CSS reads
    the ``--color-*`` vars, so the runtime ``<style>`` override (which
    the X.2.l.4.c sheet maps the libs' own hooks onto) must follow it."""
    for shell in _shells():
        i_tailwind = shell.index('href="/static/output.css"')
        i_vendor_css = shell.index("tom-select.min.css")
        i_theme = shell.index(":root {")
        i_vendor_js = shell.index("tom-select.complete.min.js")
        assert i_tailwind < i_vendor_css < i_theme < i_vendor_js


# ---------------------------------------------------------------------------
# bootstrap.js wires the init shim
# ---------------------------------------------------------------------------


def test_bootstrap_defines_and_exports_wire_filter_widgets() -> None:
    src = _BOOTSTRAP_JS_PATH.read_text(encoding="utf-8")
    assert "function wireFilterWidgets(" in src
    # Called on initial load and after every HTMX swap (idempotent).
    assert src.count("wireFilterWidgets(") >= 3  # def + 2 call sites
    # Exposed on the test-mode internals so the X.2.l.4.d Playwright
    # suite can drive it directly.
    assert "wireFilterWidgets: wireFilterWidgets," in src


def test_bootstrap_filter_widgets_degrade_when_lib_absent() -> None:
    """Each widget branch is guarded by a ``typeof X === "undefined"``
    check so a missing CDN lib (offline / blocked) leaves the plain
    ``<select>`` / ``<input>`` in place rather than throwing — the
    filter still works, just without the chrome."""
    src = _BOOTSTRAP_JS_PATH.read_text(encoding="utf-8")
    assert 'typeof TomSelect === "undefined"' in src
    assert 'typeof flatpickr === "undefined"' in src
    assert 'typeof noUiSlider === "undefined"' in src
