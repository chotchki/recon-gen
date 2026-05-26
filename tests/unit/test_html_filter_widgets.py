"""X.2.l.4.a + .b — fancy filter widgets (Tom Select / Flatpickr /
noUiSlider).

Covers the plumbing (a) and the markup (b):

  * the page shell pulls the three libs from the CDN (CSS + JS), in
    the right cascade position — vendor CSS after ``output.css`` but
    before the per-instance ``:root`` theme ``<style>`` so the theme
    override still wins; vendor JS at the bottom of ``<head>`` next to
    htmx / d3;
  * ``bootstrap.js`` defines ``wireFilterWidgets``, calls it from the
    page-load + ``htmx:afterSwap`` hooks, exposes it on the test-mode
    internals export, and guards each per-lib branch with a ``typeof``
    check so a missing CDN lib degrades to the plain control;
  * the renderer marks each filter control with a ``data-widget`` kind
    — ``tomselect`` on the dropdowns / multi-selects / the category
    ``<select multiple>``, ``flatpickr-range`` on the (un-named) date
    text input feeding the hidden ``date_from`` / ``date_to``,
    ``nouislider`` on the slider ``<div>`` over a bounded numeric range.

Behavioural round-trips (open the widget, pick a value, assert the
fetch re-fires with the right query param) live in the X.2.l.4.d
Playwright suite; the ``<select multiple>`` → hidden-input JS sync is
in ``tests/js/test_filter_primitives.py``.
"""

from __future__ import annotations

from pathlib import Path

from tests._test_helpers import make_test_config
from recon_gen.common.html import (
    CategoryFilterSpec,
    NumericRangeSpec,
    ParameterDropdownSpec,
    ParameterMultiSelectSpec,
    ParameterNumberSpec,
)
from recon_gen.common.html.render import (
    _D3_SANKEY_SRC,
    _D3_SRC,
    _HTMX_SRC,
    emit_dashboards_list,
    emit_html,
)
from recon_gen.common.ids import SheetId, VisualId
from recon_gen.common.tree.structure import Analysis, App, Sheet
from recon_gen.common.tree.visuals import KPI


_BOOTSTRAP_JS_PATH = (
    Path(__file__).parents[2]
    / "src" / "recon_gen" / "common" / "html" / "assets" / "js"
    / "bootstrap.js"
)

# X.2.p — the widget libs are served from /static/vendor/... (committed
# dist files), not a CDN. (Provenance: assets/vendor/vendor.lock;
# offline-contract guard: tests/unit/test_vendor_assets.py.)
_VENDOR_CSS_MARKERS = (
    "/static/vendor/css/tom-select.min.css",
    "/static/vendor/css/flatpickr.min.css",
    "/static/vendor/css/nouislider.min.css",
)
_VENDOR_JS_MARKERS = (
    "/static/vendor/js/tom-select.complete.min.js",
    "/static/vendor/js/flatpickr.min.js",
    "/static/vendor/js/nouislider.min.js",
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


def test_existing_runtime_libs_still_present() -> None:
    """htmx / d3 / d3-sankey are in the vendor-JS block too (X.2.p:
    served from /static/vendor/, not a CDN — must not drop any)."""
    for shell in _shells():
        assert _HTMX_SRC in shell
        assert _D3_SRC in shell
        assert _D3_SANKEY_SRC in shell


def test_vendor_css_before_theme_style_before_vendor_js() -> None:
    """Cascade order: ``output.css`` → vendor widget CSS →
    ``widgets-theme.css`` (the X.2.l.4.c override that re-colours the
    libs in terms of the ``--color-*`` tokens — must follow their base
    CSS) → per-instance ``:root`` theme override → vendor widget JS."""
    for shell in _shells():
        i_tailwind = shell.index('href="/static/output.css"')
        i_vendor_css = shell.index("tom-select.min.css")
        i_widget_theme = shell.index("/static/widgets-theme.css")
        i_theme = shell.index(":root {")
        i_vendor_js = shell.index("tom-select.complete.min.js")
        assert (
            i_tailwind < i_vendor_css < i_widget_theme < i_theme < i_vendor_js
        )


def test_widget_theme_override_sheet_is_linked() -> None:
    """The X.2.l.4.c override sheet (plain CSS, served from ``assets/``)
    is linked so the widgets pick up the per-instance ``--color-*``
    tokens; it ships with the package (``common/html/assets/*.css``)."""
    for shell in _shells():
        assert '<link rel="stylesheet" href="/static/widgets-theme.css">' in shell
    css_path = (
        Path(__file__).parents[2]
        / "src" / "recon_gen" / "common" / "html" / "assets"
        / "widgets-theme.css"
    )
    body = css_path.read_text(encoding="utf-8")
    # Re-colours the libs via the semantic tokens, not fixed colours.
    assert ".ts-control" in body and "var(--color-surface)" in body
    assert ".flatpickr-day.selected" in body and "var(--color-accent)" in body
    assert ".noUi-connect" in body and "var(--color-accent)" in body


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


# ---------------------------------------------------------------------------
# X.2.l.4.b — renderer marks each filter control with a data-widget kind
# ---------------------------------------------------------------------------


def _filter_form(out: str) -> str:
    """Slice out just the ``<form id="filter-form">...</form>`` block —
    the inlined bootstrap.js carries a markup-contract comment that
    mentions every ``data-widget`` kind, so a whole-page substring
    search would pass even if the renderer emitted nothing."""
    start = out.index('<form id="filter-form"')
    end = out.index("</form>", start) + len("</form>")
    return out[start:end]


def test_date_range_is_flatpickr_with_hidden_from_to_inputs() -> None:
    """The visible date control is one Flatpickr-range text input (no
    ``name`` — Flatpickr writes the range string into it); the two
    hidden ``date_from`` / ``date_to`` inputs are the wire elements,
    synced by ``wireFlatpickrRange``."""
    app, sheet = _build_app()
    form = _filter_form(emit_html(app, sheet, dashboard_id="x"))
    assert 'data-widget="flatpickr-range"' in form
    assert '<input type="hidden" name="date_from" value="">' in form
    assert '<input type="hidden" name="date_to" value="">' in form
    # No more native <input type="date" name="date_from"> — Flatpickr
    # owns the picker now.
    assert 'type="date" name="date_from"' not in form


def test_parameter_dropdown_is_tomselect() -> None:
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(
        name="account_id", label="Account", options=("a-1", "a-2"),
    )
    form = _filter_form(
        emit_html(app, sheet, dashboard_id="x", filter_specs=[spec]),
    )
    assert '<select name="param_account_id"' in form
    assert 'data-widget="tomselect"' in form


def test_parameter_multiselect_is_tomselect_no_size() -> None:
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(
        name="pRail", label="Rail", options=("ach", "wire", "check"),
    )
    form = _filter_form(
        emit_html(app, sheet, dashboard_id="x", filter_specs=[spec]),
    )
    assert '<select name="param_pRail" multiple' in form
    assert 'data-widget="tomselect"' in form
    # Tom Select replaces the listbox — no `size=` cap needed.
    assert "multiple size=" not in form


def test_category_filter_is_tomselect_select() -> None:
    app, sheet = _build_app()
    spec = CategoryFilterSpec(
        column="status", label="Status", options=("open", "closed"),
    )
    form = _filter_form(
        emit_html(app, sheet, dashboard_id="x", filter_specs=[spec]),
    )
    assert "data-category-select" in form
    assert 'data-widget="tomselect"' in form
    assert '<input type="hidden" name="filter_status" value="">' in form


def test_numeric_range_without_bounds_is_inputs_only() -> None:
    """No ``lo``/``hi`` → can't size a slider, so it's the two number
    inputs only (no ``data-widget``)."""
    app, sheet = _build_app()
    spec = NumericRangeSpec(column="amount", label="Amount")
    form = _filter_form(
        emit_html(app, sheet, dashboard_id="x", filter_specs=[spec]),
    )
    assert 'name="min_amount"' in form
    assert 'name="max_amount"' in form
    assert 'data-widget="nouislider"' not in form


def test_numeric_range_with_bounds_emits_nouislider() -> None:
    """``lo`` + ``hi`` → a noUiSlider ``<div>`` over the number inputs,
    carrying the bounds + the names of the inputs to keep in sync."""
    app, sheet = _build_app()
    spec = NumericRangeSpec(
        column="amount", label="Amount", lo=0, hi=1000, step=10,
    )
    form = _filter_form(
        emit_html(app, sheet, dashboard_id="x", filter_specs=[spec]),
    )
    assert 'data-widget="nouislider"' in form
    assert 'data-min="0"' in form
    assert 'data-max="1000"' in form
    assert 'data-step="10"' in form
    assert 'data-min-input="min_amount"' in form
    assert 'data-max-input="max_amount"' in form
    # The number inputs are still there (typed-entry fallback + wire).
    assert 'name="min_amount"' in form
    assert 'name="max_amount"' in form


def test_parameter_number_emits_single_handle_nouislider() -> None:
    """X.2.u.4.e — a ``ParameterNumberSpec`` (from a tree
    ``ParameterSlider``) → ``<input type="number" name="param_<name>">``
    + a one-handle noUiSlider over it (``data-value-input``), with the
    initial value from ``default`` and integer bounds rendered without a
    trailing ``.0``."""
    app, sheet = _build_app()
    spec = ParameterNumberSpec(
        name="pInvAnomaliesSigma", label="Min sigma",
        minimum=1, maximum=4, step=1, default=2.0,
    )
    form = _filter_form(
        emit_html(app, sheet, dashboard_id="x", filter_specs=[spec]),
    )
    assert 'name="param_pInvAnomaliesSigma"' in form
    assert 'type="number"' in form
    assert 'data-widget="nouislider"' in form
    assert 'data-value-input="param_pInvAnomaliesSigma"' in form
    assert 'data-min="1"' in form
    assert 'data-max="4"' in form
    assert 'data-start-min="2"' in form
    # No two-handle wiring on a parameter slider.
    assert 'data-min-input=' not in form
    assert 'data-max-input=' not in form


def test_parameter_number_without_default_starts_at_minimum() -> None:
    app, sheet = _build_app()
    spec = ParameterNumberSpec(
        name="pThr", label="Threshold", minimum=2, maximum=20, step=2,
    )
    form = _filter_form(
        emit_html(app, sheet, dashboard_id="x", filter_specs=[spec]),
    )
    assert 'value="2"' in form
    assert 'data-start-min="2"' in form
