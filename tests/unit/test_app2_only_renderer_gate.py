"""DM.0.5 — Renderer-gate primitive (``app2_only=True``) tree-walk unit test.

The DM design lock (`docs/audits/dm_0_daily_statement_app2_cascade.md`)
adds a typed ``app2_only: bool`` field to ``ParameterDropdown`` and
``ParameterDateTimePicker``. The QS emitter walk (``Sheet.emit()``)
skips controls flagged ``app2_only=True``; the App2 spec walker
(``make_filter_specs_for_sheet``) ignores the field and renders every
control.

This file locks the renderer-gate contract:

- A fresh ``ParameterDropdown`` defaults ``app2_only=False`` (today's
  both-renderer behavior; backward-compatible with the dozens of
  existing ``add_parameter_dropdown`` call sites).
- Flagging one control ``app2_only=True`` drops the QS-side parameter
  control count by exactly one; the App2-side count stays unchanged.
- The dataset-dependency walk still includes ``app2_only=True``
  controls' source datasets (App2 still queries them; the dataset
  must be deployed).

Same shape as the DL.1 ``iter_cross_sheet_drills`` and DA.5
``Table.__post_init__`` tree-walked-attribute precedent — invariants
encoded in the type system, asserted by walking the tree.
"""

from __future__ import annotations

from recon_gen.common.html import make_filter_specs_for_sheet
from recon_gen.common.ids import ParameterName, SheetId
from recon_gen.common.models import DateTimeDefaultValues
from recon_gen.common.tree import (
    Analysis,
    App,
    DateTimeParam,
    ParameterDateTimePicker,
    ParameterDropdown,
    Sheet,
    StaticValues,
    StringParam,
)
from tests._test_helpers import make_test_config


_DT_DEFAULT = DateTimeDefaultValues(StaticValues=["2030-01-01"])


def test_app2_only_defaults_false_on_parameter_dropdown() -> None:
    """A fresh ``ParameterDropdown`` without explicit ``app2_only`` is
    visible to both renderers — the existing call sites keep their
    current both-renderer behavior."""
    p = StringParam(name=ParameterName("p"))
    ctrl = ParameterDropdown(
        parameter=p, title="P",
        selectable_values=StaticValues(values=["x"]),
    )
    assert ctrl.app2_only is False


def test_app2_only_defaults_false_on_parameter_datetime_picker() -> None:
    """A fresh ``ParameterDateTimePicker`` without explicit
    ``app2_only`` is visible to both renderers."""
    p = DateTimeParam(name=ParameterName("p"), default=_DT_DEFAULT)
    ctrl = ParameterDateTimePicker(parameter=p, title="P")
    assert ctrl.app2_only is False


def test_qs_emit_skips_app2_only_controls_delta_matches_flag_count() -> None:
    """The QS emitter walk (``Sheet.emit()``) filters out controls
    flagged ``app2_only=True``; the emitted ``SheetDefinition.ParameterControls``
    list shrinks by exactly the count of flagged controls."""
    """Build the App locally so we can call ``resolve_auto_ids()`` and
    inspect the emitted ``SheetDefinition.ParameterControls`` list."""
    app = App(name="dm05-emit-test", cfg=make_test_config())
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="dm05-emit-analysis", name="DM.0.5 Emit",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("dm05-emit-sheet"), name="Gate",
        title="Gate", description="DM.0.5 renderer-gate fixture",
    ))
    p_account = analysis.add_parameter(StringParam(
        name=ParameterName("pAccount"), default=["__no_account__"],
    ))
    p_role = analysis.add_parameter(StringParam(
        name=ParameterName("pRole"), default=["__no_role__"],
    ))
    p_day = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pDay"), default=_DT_DEFAULT,
    ))
    sheet.add_parameter_dropdown(
        parameter=p_account, title="Account",
        selectable_values=StaticValues(values=["A", "B", "C"]),
    )
    sheet.add_parameter_dropdown(
        parameter=p_role, title="Role",
        selectable_values=StaticValues(values=["concentration", "dda"]),
        app2_only=True,
    )
    sheet.add_parameter_datetime_picker(
        parameter=p_day, title="Day", app2_only=True,
    )

    # Tree-side view: three controls total, two flagged app2_only.
    total_tree_controls = len(sheet.parameter_controls)
    flagged_count = sum(
        1 for c in sheet.parameter_controls
        if getattr(c, "app2_only", False)
    )
    assert total_tree_controls == 3
    assert flagged_count == 2

    # Resolve auto-IDs so emit() can fire.
    app.resolve_auto_ids()

    # QS emit: the emitted SheetDefinition only carries the
    # not-flagged controls (3 - 2 = 1).
    emitted = sheet.emit()
    emitted_qs_controls = emitted.ParameterControls or []
    assert len(emitted_qs_controls) == total_tree_controls - flagged_count
    assert len(emitted_qs_controls) == 1


def test_app2_side_count_unchanged_by_gate() -> None:
    """The App2 spec walker ignores ``app2_only`` — it renders every
    control it understands. Compare two sheets identical except for
    the ``app2_only`` flag and assert the App2-side filter-spec count
    is the same."""
    # Sheet A — Role dropdown is app2_only=True (DM-shape).
    app_a = App(name="dm05-app2-a", cfg=make_test_config())
    analysis_a = app_a.set_analysis(Analysis(
        analysis_id_suffix="a", name="A",
    ))
    sheet_a = analysis_a.add_sheet(Sheet(
        sheet_id=SheetId("a"), name="A", title="A", description="A",
    ))
    p_role_a = analysis_a.add_parameter(StringParam(
        name=ParameterName("pRoleA"), default=["__no_role__"],
    ))
    sheet_a.add_parameter_dropdown(
        parameter=p_role_a, title="Role",
        selectable_values=StaticValues(values=["concentration", "dda"]),
        app2_only=True,
    )
    # Sheet B — same Role dropdown, app2_only=False (default).
    app_b = App(name="dm05-app2-b", cfg=make_test_config())
    analysis_b = app_b.set_analysis(Analysis(
        analysis_id_suffix="b", name="B",
    ))
    sheet_b = analysis_b.add_sheet(Sheet(
        sheet_id=SheetId("b"), name="B", title="B", description="B",
    ))
    p_role_b = analysis_b.add_parameter(StringParam(
        name=ParameterName("pRoleB"), default=["__no_role__"],
    ))
    sheet_b.add_parameter_dropdown(
        parameter=p_role_b, title="Role",
        selectable_values=StaticValues(values=["concentration", "dda"]),
    )

    specs_a = make_filter_specs_for_sheet(sheet_a)
    specs_b = make_filter_specs_for_sheet(sheet_b)
    assert len(specs_a) == len(specs_b), (
        f"App2 spec walker should NOT honor app2_only; got "
        f"app2_only=True sheet={len(specs_a)} vs "
        f"app2_only=False sheet={len(specs_b)}"
    )
