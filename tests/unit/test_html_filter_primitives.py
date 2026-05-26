"""X.2.d — filter-primitive renderer tests.

Three filter shapes wired to URL query params per X.2.b's
URL-as-state architecture:

    ParameterDropdown  → ?param_<name>=<value>
    CategoryFilter     → ?filter_<col>=v1,v2,v3
    NumericRange       → ?min_<col>=N&max_<col>=M

Server-side here verifies the HTML the renderer emits — the
HTMX wire format (``hx-include="#filter-form"`` serializes every
named input the form contains) means there's no separate "wire"
test on the server side: a TestClient round-trip just confirms
the form is in the page. The data fetcher already accepts
``dict[str, str]`` query params (X.2.b), so prefix-keyed entries
flow through unchanged.

JS-side behavior (CategoryFilter ``<select multiple>`` → hidden-input
sync) lives in ``tests/js/test_filter_primitives.py``; the fancy-widget
markup (``data-widget`` attrs, Flatpickr date range) is checked in
``tests/unit/test_html_filter_widgets.py``.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from recon_gen.common.html import (
    CategoryFilterSpec,
    NumericRangeSpec,
    ParameterDropdownSpec,
    ParameterMultiSelectSpec,
    emit_html,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.ids import SheetId, VisualId
from recon_gen.common.tree.structure import Analysis, App, Sheet
from recon_gen.common.tree.visuals import KPI
from tests._test_helpers import make_test_config


_TEST_CFG = make_test_config()


def _build_app() -> tuple[App, Sheet]:
    app = App(name="filters-test", cfg=_TEST_CFG)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="filters-test-analysis",
        name="Filters Test",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("filters-sheet"),
        name="Filters",
        title="Filters Sheet",
        description="x",
    ))
    sheet.visuals.append(
        KPI(title="K", subtitle="t", visual_id=VisualId("v-k")),
    )
    return app, sheet


# ---------------------------------------------------------------------------
# ParameterDropdown
# ---------------------------------------------------------------------------


def test_parameter_dropdown_emits_select_with_param_prefix() -> None:
    """``param_<name>`` is the URL key contract — the ``<select>``
    must carry that name so HTMX serializes it correctly."""
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(
        name="account_id",
        label="Account",
        options=("acct-1", "acct-2", "acct-3"),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<select name="param_account_id"' in out
    assert '<option value="acct-1">acct-1</option>' in out
    assert '<option value="acct-2">acct-2</option>' in out
    assert '<option value="acct-3">acct-3</option>' in out


def test_parameter_dropdown_includes_blank_leading_option() -> None:
    """Empty string round-trips as "no selection" — the leading blank
    lets the user clear without an explicit reset button."""
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(
        name="region", label="Region", options=("us-east", "us-west"),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<option value=""></option>' in out


def test_parameter_dropdown_label_appears_in_output() -> None:
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(
        name="x", label="Account ID", options=("a",),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert "Account ID" in out


def test_parameter_dropdown_escapes_options() -> None:
    """Defensive: option values may come from L2 / dataset and could
    contain HTML special characters."""
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(
        name="x", label="X", options=("<script>alert(1)</script>",),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_parameter_dropdown_marks_selected_option() -> None:
    """u.4.e.4 — ``selected`` (set by the server from a ``?param_<name>=<v>``
    page-URL key) pre-marks the matching ``<option>`` so the visuals'
    ``hx-include="#filter-form"`` load fetch is already narrowed."""
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(
        name="x", label="X", options=("a", "b", "c"), selected="b",
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<option value="b" selected>b</option>' in out
    assert '<option value="a">a</option>' in out
    assert '<option value="c">c</option>' in out
    # The blank "clear" option is still there.
    assert '<option value=""></option>' in out


def test_parameter_dropdown_selected_not_in_options_still_rendered() -> None:
    """A stale bookmark / sibling-dataset value the (resolved) option list
    doesn't contain is still emitted as a selected ``<option>`` so the form
    submits it (the data fetch narrows on it regardless)."""
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(
        name="x", label="X", options=("a", "b"), selected="zzz",
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<option value="zzz" selected>zzz</option>' in out


def test_parameter_dropdown_no_selected_attr_when_blank() -> None:
    """Default ``selected=""`` → no ``<option>`` carries the attribute;
    the blank leading option is what the form submits (= no narrowing)."""
    app, sheet = _build_app()
    spec = ParameterDropdownSpec(name="x", label="X", options=("a", "b"))
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    form_start = out.index('<form id="filter-form"')
    form_end = out.index('</form>', form_start)
    assert " selected>" not in out[form_start:form_end]


# ---------------------------------------------------------------------------
# CategoryFilter
# ---------------------------------------------------------------------------


def test_category_filter_emits_hidden_input_with_filter_prefix() -> None:
    """The hidden input is what HTMX actually serializes (the
    ``<select>`` is un-named). Hidden name carries the ``filter_<col>``
    URL key."""
    app, sheet = _build_app()
    spec = CategoryFilterSpec(
        column="status",
        label="Status",
        options=("open", "closed", "pending"),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<input type="hidden" name="filter_status" value="">' in out


def test_category_filter_emits_unnamed_select_multiple_with_options() -> None:
    """X.2.l.4: each option becomes an ``<option>`` inside an un-named
    ``<select multiple data-category-select>`` (Tom Select enhances it;
    ``wireCategoryFilters`` syncs the hidden input). The select carries
    no ``name=`` so HTMX won't serialize it directly."""
    app, sheet = _build_app()
    spec = CategoryFilterSpec(
        column="status",
        label="Status",
        options=("open", "closed"),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert "<select multiple " in out
    assert "data-category-select" in out
    assert '<option value="open">open</option>' in out
    assert '<option value="closed">closed</option>' in out
    # The <select> is un-named — only the hidden filter_<col> input is.
    assert 'name="open"' not in out
    assert 'name="closed"' not in out


def test_category_filter_wrapper_carries_class_for_js_hook() -> None:
    """``.category-filter`` is the JS selector the bootstrap script
    uses to find these wrappers. Without it the select→hidden sync
    wouldn't fire."""
    app, sheet = _build_app()
    spec = CategoryFilterSpec(
        column="status", label="Status", options=("open",),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert 'class="category-filter' in out


def test_category_filter_label_appears_in_output() -> None:
    app, sheet = _build_app()
    spec = CategoryFilterSpec(
        column="status", label="Account Status", options=("a",),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert "Account Status" in out


# ---------------------------------------------------------------------------
# NumericRange
# ---------------------------------------------------------------------------


def test_numeric_range_emits_min_and_max_inputs() -> None:
    """Two ``<input type="number">`` named ``min_<col>`` + ``max_<col>``."""
    app, sheet = _build_app()
    spec = NumericRangeSpec(column="amount", label="Amount")
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<input type="number"' in out
    assert 'name="min_amount"' in out
    assert 'name="max_amount"' in out


def test_numeric_range_inputs_accept_decimals() -> None:
    """``step="any"`` lets the browser accept arbitrary decimals
    without rejecting "12.34" as invalid."""
    app, sheet = _build_app()
    spec = NumericRangeSpec(column="amount", label="Amount")
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert 'step="any"' in out


def test_numeric_range_label_appears_in_output() -> None:
    app, sheet = _build_app()
    spec = NumericRangeSpec(column="amount", label="USD Amount")
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert "USD Amount" in out


# ---------------------------------------------------------------------------
# ParameterMultiSelect (Y.2.app2.cde.l2ft-wiring.b)
# ---------------------------------------------------------------------------


def test_parameter_multiselect_emits_select_multiple_with_param_prefix() -> None:
    """``<select multiple name="param_<name>">`` — the ``param_`` prefix
    is the URL-key contract the multi-valued dataset-param expander reads."""
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(
        name="pL2ftRail", label="Rail",
        options=("ach", "wire", "check"),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<select name="param_pL2ftRail" multiple' in out
    assert '<option value="ach">ach</option>' in out
    assert '<option value="wire">wire</option>' in out
    assert '<option value="check">check</option>' in out


def test_parameter_multiselect_has_no_blank_leading_option() -> None:
    """For a multi-select, "nothing selected" already means "all" (the
    executor's static-default fallback) — a blank option would be noise."""
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(name="pX", label="X", options=("a", "b"))
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    select_start = out.index('<select name="param_pX" multiple')
    select_end = out.index('</select>', select_start)
    assert '<option value="">' not in out[select_start:select_end]


def test_parameter_multiselect_label_appears_in_output() -> None:
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(name="pX", label="Rail Name", options=("a",))
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert "Rail Name" in out


def test_parameter_multiselect_escapes_options() -> None:
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(
        name="pX", label="X", options=("<script>alert(1)</script>",),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_parameter_multiselect_lives_inside_filter_form() -> None:
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(name="pX", label="X", options=("a",))
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    form_start = out.index('<form id="filter-form"')
    form_end = out.index('</form>', form_start)
    assert 'name="param_pX"' in out[form_start:form_end]


def test_parameter_multiselect_marks_selected_options() -> None:
    """u.4.e.4 — ``selected`` (filled by the server from the page URL's
    repeated ``?param_<name>=A&param_<name>=B`` keys) pre-marks those
    ``<option>``s; the unselected ones stay bare."""
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(
        name="pX", label="X", options=("a", "b", "c"), selected=("a", "c"),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<option value="a" selected>a</option>' in out
    assert '<option value="c" selected>c</option>' in out
    assert '<option value="b">b</option>' in out


def test_parameter_multiselect_selected_not_in_options_appended() -> None:
    """Selected values absent from the resolved option list are appended
    as selected ``<option>``s (stale-bookmark / sibling-dataset case)."""
    app, sheet = _build_app()
    spec = ParameterMultiSelectSpec(
        name="pX", label="X", options=("a", "b"), selected=("a", "zzz"),
    )
    out = emit_html(app, sheet, dashboard_id="x", filter_specs=[spec])
    assert '<option value="a" selected>a</option>' in out
    assert '<option value="zzz" selected>zzz</option>' in out


# ---------------------------------------------------------------------------
# Mixed + default
# ---------------------------------------------------------------------------


def test_filter_specs_default_to_empty_so_existing_callers_work() -> None:
    """Existing emit_html callers don't pass ``filter_specs`` — the
    default is the empty tuple, which leaves the form date-range only."""
    app, sheet = _build_app()
    out = emit_html(app, sheet, dashboard_id="x")
    # Date inputs still there.
    assert 'name="date_from"' in out
    assert 'name="date_to"' in out
    # No new filter primitives — check the rendered form, not the
    # inlined JS (which references the .category-filter selector).
    form_start = out.index('<form id="filter-form"')
    form_end = out.index('</form>', form_start)
    form_block = out[form_start:form_end]
    assert "category-filter" not in form_block
    assert 'name="param_' not in form_block
    assert 'name="min_' not in form_block


def test_multiple_filter_specs_render_in_order() -> None:
    """All three primitives + date range coexist in one form."""
    app, sheet = _build_app()
    out = emit_html(
        app, sheet, dashboard_id="x",
        filter_specs=[
            ParameterDropdownSpec(name="acct", label="Account", options=("a1",)),
            CategoryFilterSpec(
                column="status", label="Status", options=("open",),
            ),
            NumericRangeSpec(column="amount", label="Amount"),
        ],
    )
    assert 'name="date_from"' in out
    assert 'name="param_acct"' in out
    assert 'name="filter_status"' in out
    assert 'name="min_amount"' in out
    assert 'name="max_amount"' in out


def test_all_filter_inputs_live_inside_filter_form() -> None:
    """``hx-include="#filter-form"`` only catches inputs inside the
    form — verify all controls are children of ``<form id="filter-form">``."""
    app, sheet = _build_app()
    out = emit_html(
        app, sheet, dashboard_id="x",
        filter_specs=[
            ParameterDropdownSpec(name="x", label="X", options=("a",)),
            CategoryFilterSpec(column="y", label="Y", options=("b",)),
            NumericRangeSpec(column="z", label="Z"),
        ],
    )
    form_start = out.index('<form id="filter-form"')
    form_end = out.index('</form>', form_start)
    form_block = out[form_start:form_end]
    for needle in (
        'name="date_from"',
        'name="date_to"',
        'name="param_x"',
        'name="filter_y"',
        'name="min_z"',
        'name="max_z"',
    ):
        assert needle in form_block, f"{needle!r} should live inside filter form"


# ---------------------------------------------------------------------------
# Server round-trip — TestClient confirms params flow through to the fetcher
# ---------------------------------------------------------------------------


def test_server_passes_prefix_keyed_params_to_fetcher() -> None:
    """A GET to ``/visuals/<id>/data?param_X=...&filter_Y=...&min_Z=..&max_Z=..``
    delivers the entire query-param dict to the data fetcher. This is
    the server-side contract X.2.f's real fetcher will consume."""
    app, sheet = _build_app()
    seen: dict[str, dict[str, list[str]]] = {}

    def fetcher(
        visual_id: str, params: dict[str, list[str]],
    ) -> dict[str, list[float]]:
        seen[visual_id] = dict(params)
        return {"values": []}

    asgi = make_app(dashboards={
        "filters": ServedDashboard(
            tree_app=app, sheet=sheet,
            title="Filters", data_fetcher=fetcher,  # pyright: ignore[reportArgumentType]: inline fetcher closure; structural DataFetcher contract holds at runtime
        ),
    })
    client = TestClient(asgi)
    client.get(
        "/dashboards/filters/sheets/filters-sheet/visuals/v-k/data"
        "?param_account=acct-1&filter_status=open,closed"
        "&min_amount=10&max_amount=100",
    )
    assert seen["v-k"] == {
        "param_account": ["acct-1"],
        "filter_status": ["open,closed"],
        "min_amount": ["10"],
        "max_amount": ["100"],
    }


def test_emit_html_threads_filter_specs_from_kwarg() -> None:
    """Smoke that the kwarg actually reaches the form — independent
    of which primitive shape is used."""
    app, sheet = _build_app()
    out = emit_html(
        app, sheet, dashboard_id="x",
        filter_specs=[
            ParameterDropdownSpec(name="acct", label="Account", options=("a1",)),
        ],
    )
    assert 'name="param_acct"' in out
