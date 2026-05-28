"""Y.2.app2.cde.l2ft-wiring.b + X.2.u.4.e + AA.A.3 —
``make_filter_specs_for_sheet`` tests.

Locks the tree-walk → App2 ``FilterSpec`` derivation:

- a MULTI_SELECT ``ParameterDropdown`` with ``StaticValues`` →
  ``ParameterMultiSelectSpec`` (the pre-AA.A.3 L2FT Rail/Status/Bundle
  shape, kept for future multi-select keepers);
- a SINGLE_SELECT ``ParameterDropdown`` with ``StaticValues`` →
  ``ParameterDropdownSpec`` (AA.A.3 — post-flip enum dropdowns + the
  L2FT metadata-cascade key dropdowns);
- a ``ParameterSlider`` → ``ParameterNumberSpec`` (X.2.u.4.e —
  Investigation's σ / max-hops / min-amount knobs).

Pre-AA.A.3 the SINGLE_SELECT + StaticValues case was silently skipped
(justified for the metadata-cascade dropdowns); AA.A.3 added the
deriver case so the flipped enum dropdowns keep App2 widgets, and the
metadata-key dropdowns picked one up as a side benefit (the cascade
"half-truth" concern was already moot post-Y.1.m when the Value
dropdown became a text field).
"""

from __future__ import annotations

from recon_gen.common.html import (
    ParameterDropdownSpec,
    ParameterMultiSelectSpec,
    ParameterNumberSpec,
    make_filter_specs_for_sheet,
)
from recon_gen.common.html.render import (
    ParameterDateSpec,
    _render_filter_form,
)
from recon_gen.apps.l1_dashboard.app import _DAILY_STATEMENT_NAME, _DRILL_RESET_SENTINEL
from recon_gen.apps.l1_dashboard.datasets import P_L1_DS_BALANCE_DATE_DSP
from recon_gen.apps.l2_flow_tracing.app import _RAILS_NAME
from recon_gen.common.ids import ParameterName, SheetId
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.tree import (
    Analysis,
    App,
    IntegerParam,
    Sheet,
    StaticValues,
    StringParam,
)
from tests._test_helpers import make_test_config


def _sheet_with_controls() -> Sheet:
    app = App(name="ft-specs-test", cfg=make_test_config())
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="ft-specs-analysis", name="FT Specs",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("ft-specs-sheet"), name="FT", title="FT", description="x",
    ))
    # A MULTI_SELECT StaticValues dropdown — should become a spec.
    p_rail = analysis.add_parameter(StringParam(
        name=ParameterName("pL2ftRail"), multi_valued=True,
        default=["ach", "wire", "check"],
    ))
    sheet.add_parameter_dropdown(
        parameter=p_rail, title="Rail", type="MULTI_SELECT",
        selectable_values=StaticValues(values=["ach", "wire", "check"]),
    )
    # A SINGLE_SELECT StaticValues dropdown — AA.A.3 — should become a
    # ParameterDropdownSpec (was skipped pre-AA.A.3).
    p_key = analysis.add_parameter(StringParam(name=ParameterName("pKey")))
    sheet.add_parameter_dropdown(
        parameter=p_key, title="Metadata Key",
        selectable_values=StaticValues(values=[_DRILL_RESET_SENTINEL, "memo_kind"]),
    )
    # A slider — becomes a ParameterNumberSpec (X.2.u.4.e). Default [3]
    # so the derived spec carries default=3.0 (matches the dataset SQL's
    # static-default literal); minimum 1 is the no-narrowing position.
    p_n = analysis.add_parameter(IntegerParam(
        name=ParameterName("pThreshold"), default=[3],
    ))
    sheet.add_parameter_slider(
        parameter=p_n, title="Threshold",
        minimum_value=1, maximum_value=10, step_size=2,
    )
    return sheet


def test_make_filter_specs_returns_one_per_multiselect_static_dropdown() -> None:
    sheet = _sheet_with_controls()
    specs = make_filter_specs_for_sheet(sheet)
    multi = [s for s in specs if isinstance(s, ParameterMultiSelectSpec)]
    assert len(multi) == 1
    (spec,) = multi
    assert spec.name == "pL2ftRail"
    assert spec.label == "Rail"
    assert spec.options == ("ach", "wire", "check")


def test_make_filter_specs_derives_dropdown_for_single_select_static_dropdown() -> None:
    """AA.A.3 — SINGLE_SELECT + StaticValues → ``ParameterDropdownSpec``
    with inlined options. Pre-AA.A.3 these were skipped; the flip phase
    added the deriver case so the post-flip enum dropdowns keep widgets
    (and the metadata-cascade key dropdowns picked one up as a side
    benefit)."""
    sheet = _sheet_with_controls()
    specs = make_filter_specs_for_sheet(sheet)
    drops = [s for s in specs if isinstance(s, ParameterDropdownSpec)]
    assert len(drops) == 1
    (spec,) = drops
    assert spec.name == "pKey"
    assert spec.label == "Metadata Key"
    assert spec.options == (_DRILL_RESET_SENTINEL, "memo_kind")


def test_make_filter_specs_derives_parameter_number_for_slider() -> None:
    """A ``ParameterSlider`` → a ``ParameterNumberSpec`` carrying the
    slider bounds/step + the bound parameter's analysis-level default
    (X.2.u.4.e)."""
    sheet = _sheet_with_controls()
    specs = make_filter_specs_for_sheet(sheet)
    nums = [s for s in specs if isinstance(s, ParameterNumberSpec)]
    assert len(nums) == 1
    (n,) = nums
    assert n.name == "pThreshold"
    assert n.label == "Threshold"
    assert (n.minimum, n.maximum, n.step) == (1.0, 10.0, 2.0)
    assert n.default == 3.0


def test_make_filter_specs_empty_for_sheet_with_no_parameter_controls() -> None:
    app = App(name="ft-empty-test", cfg=make_test_config())
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="ft-empty-analysis", name="FT Empty",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("ft-empty-sheet"), name="FT", title="FT", description="x",
    ))
    assert make_filter_specs_for_sheet(sheet) == []


# --- Integration: post-AA.A.3 L2FT Rails sheet auto-derives the dropdowns ---

def test_l2ft_rails_sheet_auto_derives_post_aa_a_3_pushdown_specs() -> None:
    """AA.A.3 — the Rails sheet's filter bar is Date From/To + Rail /
    Status / Bundle (SINGLE_SELECT pushdown dropdowns post-AA.A.3) +
    Metadata Key (SINGLE_SELECT cascade) + Metadata Value (text-field,
    skipped). Every dropdown auto-derives as a ``ParameterDropdownSpec``
    now; pre-AA.A.3 the pushdown trio derived as MULTI but the flip
    moved them to SINGLE."""
    from recon_gen.common.l2 import default_l2_instance
    from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_all_l2_flow_tracing_datasets,
    )

    inst = default_l2_instance()
    cfg = make_test_config(db_table_prefix=DEFAULT_PREFIX)
    build_all_l2_flow_tracing_datasets(cfg, inst)
    tree_app = build_l2_flow_tracing_app(cfg, l2_instance=inst)
    assert tree_app.analysis is not None
    rails_sheet = next(
        s for s in tree_app.analysis.sheets if s.name == _RAILS_NAME
    )
    specs = make_filter_specs_for_sheet(rails_sheet)
    names = [s.name for s in specs if isinstance(s, ParameterDropdownSpec)]
    # Rail / Status / Bundle (post-AA.A.3 pushdown SINGLE_SELECTs) + the
    # MetaKey cascade dropdown.
    assert "pL2ftRail" in names
    assert "pL2ftStatus" in names
    assert "pL2ftBundle" in names
    assert "pL2ftMetaKey" in names
    # No MULTI_SELECT specs left on the post-flip sheet.
    assert not [s for s in specs if isinstance(s, ParameterMultiSelectSpec)]
    # Each dropdown spec carries a non-empty option list.
    for spec in specs:
        if isinstance(spec, ParameterDropdownSpec):
            assert len(spec.options) >= 1


def test_date_control_shape_comes_from_the_tree() -> None:
    """Phase BM — emission is gated on the parameter's
    ``mapped_dataset_params``: a picker bound to a SQL-pushed-down
    dataset param (BM-shape — L1 Daily Statement's single Business
    Day picker, L1 universal Date From/To pair, Exec Date From/To
    pair, L2FT per-sheet Date From/To pairs) emits a
    ``ParameterDateSpec`` per picker. Pickers WITHOUT
    ``mapped_dataset_params`` don't emit — App2's SQL wouldn't bind
    the URL keys, so rendering the picker would be a UX lie.
    """
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets,
    )
    from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
    from recon_gen.apps.l2_flow_tracing.datasets import (
        P_L2FT_RAILS_DATE_END,
        P_L2FT_RAILS_DATE_START,
        build_all_l2_flow_tracing_datasets,
    )
    from recon_gen.common.l2 import default_l2_instance

    cfg = make_test_config(db_table_prefix=DEFAULT_PREFIX)
    inst = default_l2_instance()

    # BM-shape picker (L1 Daily Statement Business Day) → single date.
    build_all_l1_dashboard_datasets(cfg, inst)
    l1 = build_l1_dashboard_app(cfg, l2_instance=inst)
    assert l1.analysis is not None
    ds_sheet = next(s for s in l1.analysis.sheets if s.name == _DAILY_STATEMENT_NAME)
    ds_specs = make_filter_specs_for_sheet(ds_sheet)
    date_specs = [s for s in ds_specs if isinstance(s, ParameterDateSpec)]
    assert len(date_specs) == 1
    assert date_specs[0].name == P_L1_DS_BALANCE_DATE_DSP
    ds_form = _render_filter_form([], tuple(ds_specs))
    assert 'data-widget="flatpickr-single"' in ds_form
    assert 'data-widget="flatpickr-range"' not in ds_form

    # L2FT picker pair (post-BM) — narrows on the per-sheet dataset
    # via ``<<$pL2ftDate*>>`` pushdown; mapped_dataset_params bridges
    # the picker write through, so App2 emits the per-picker
    # ParameterDateSpec entries as expected.
    build_all_l2_flow_tracing_datasets(cfg, inst)
    l2ft = build_l2_flow_tracing_app(cfg, l2_instance=inst)
    assert l2ft.analysis is not None
    rails = next(s for s in l2ft.analysis.sheets if s.name == _RAILS_NAME)
    rails_specs = make_filter_specs_for_sheet(rails)
    rails_date_specs = [
        s for s in rails_specs if isinstance(s, ParameterDateSpec)
    ]
    rails_names = {s.name for s in rails_date_specs}
    assert P_L2FT_RAILS_DATE_START in rails_names
    assert P_L2FT_RAILS_DATE_END in rails_names
