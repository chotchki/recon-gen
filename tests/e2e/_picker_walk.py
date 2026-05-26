"""AA.A.6 — tree-driven enumeration of sheets + their pickers for the
generic additive-pickers row-survival test.

Spike confirmed (audit: ``docs/audits/aa_a_6_picker_enumeration_spike.md``)
that path (2) is viable: pick each picker's first option (or slider
midpoint), assert rows survive individually + combined. The test does
NOT need a picker→filter-column map — it reads option values straight
from the live dropdown, which the renderer already populates from
whatever column the picker binds.

The list of (app, sheet) entries is derived from the tree at collection
time — never hardcoded. Adding a sheet to any of the 4 apps automatically
extends the test set. Adding a picker to a sheet automatically extends
the per-sheet picker loop.

Two factories:

- ``enumerate_picker_sheets()`` — builds all 4 apps against
  ``spec_example``, walks ``app.analysis.sheets[].parameter_controls``,
  yields one ``PickerSheet`` per (app_name, sheet_name) where ≥1
  picker is *exercisable* (dropdown w/ options, slider).
- ``PickerSheet.picker_specs`` — list of ``PickerSpec`` per sheet, each
  carrying the picker label + kind + a ``pick_first`` strategy the test
  body invokes via the driver.

Date pickers + text fields are *skipped*: date is covered by
``test_l1_filters.py::test_universal_date_filter_narrows_table``; text
fields (L2FT metadata value) need a coordinated dropdown pick to make
sense.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from recon_gen.common.config import Config
from recon_gen.common.l2.loader import load_instance as load_l2_instance
from recon_gen.common.tree.controls import (
    LinkedValues, ParameterDropdown, ParameterSlider, StaticValues,
)
from recon_gen.common.tree.structure import App, Sheet


# Repo root → tests/l2/spec_example.yaml — the canonical, smallest
# L2 with all primitives present (matches the audit doc's enumeration).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_L2 = _REPO_ROOT / "tests" / "l2" / "spec_example.yaml"


PickerKind = Literal["dropdown_static", "dropdown_linked", "slider"]


@dataclass(frozen=True)
class PickerSpec:
    """One exercisable picker on a sheet — what the test loop drives.

    ``label`` is the visible widget label the driver keys on
    (``driver.pick_filter(label, ...)`` / ``driver.set_slider(label,
    ...)``). ``param_name`` is the underlying parameter (URL key) for
    failure-message clarity — not used to drive the picker.
    ``kind`` branches the strategy the test applies.

    For sliders, ``slider_low`` / ``slider_high`` carry the spec's range
    bounds — the test picks a value in the middle for "permissive" and
    the maximum for "restrictive" / "non-default".
    """
    label: str
    param_name: str
    kind: PickerKind
    slider_low: float | None = None
    slider_high: float | None = None
    slider_step: float | None = None


@dataclass(frozen=True)
class PickerSheet:
    """One sheet's set of exercisable pickers — the parametrize unit."""
    app_name: str
    dashboard_id: str
    sheet_name: str
    picker_specs: tuple[PickerSpec, ...]

    @property
    def test_id(self) -> str:
        """Pytest-friendly id: ``L1 Dashboard::Daily Statement``."""
        return f"{self.app_name}::{self.sheet_name}"


def _picker_specs_for_sheet(sheet: Sheet) -> tuple[PickerSpec, ...]:
    """Extract the exercisable pickers from a Sheet's parameter_controls.

    Filters out ParameterDateTimePicker + ParameterTextField (covered
    elsewhere / not driven generically). For Dropdowns, branches by
    StaticValues vs LinkedValues only for the failure-message taxonomy
    — the live dropdown options come from the DOM at test time
    regardless.
    """
    specs: list[PickerSpec] = []
    for ctrl in sheet.parameter_controls:
        if isinstance(ctrl, ParameterDropdown):
            kind: PickerKind = (
                "dropdown_static"
                if isinstance(ctrl.selectable_values, StaticValues)
                else "dropdown_linked"
                if isinstance(ctrl.selectable_values, LinkedValues)
                else "dropdown_static"  # defensive: unknown variant
            )
            specs.append(PickerSpec(
                label=ctrl.title,
                param_name=ctrl.parameter.name,
                kind=kind,
            ))
        elif isinstance(ctrl, ParameterSlider):
            specs.append(PickerSpec(
                label=ctrl.title,
                param_name=ctrl.parameter.name,
                kind="slider",
                slider_low=ctrl.minimum_value,
                slider_high=ctrl.maximum_value,
                slider_step=ctrl.step_size,
            ))
        # Date / text are skipped — see module docstring.
    return tuple(specs)


def _build_apps(cfg: Config | None = None) -> Sequence[tuple[str, App]]:
    """Construct all 4 apps against the default L2 (``spec_example``).

    Cheap — no DB / AWS contact; the App build is a pure tree
    construction. ``emit_analysis()`` resolves auto-IDs so the tree's
    Sheet.parameter_controls walk gets stable identifiers.
    """
    # Late import — avoid circular at module-import time + keep the
    # heavy app modules off the path until a test actually needs them.
    from tests._test_helpers import make_test_config
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.apps.l2_flow_tracing.app import (
        build_l2_flow_tracing_app,
    )
    from recon_gen.apps.investigation.app import build_investigation_app
    from recon_gen.apps.executives.app import build_executives_app

    effective_cfg = cfg or make_test_config(
        default_l2_instance=_DEFAULT_L2,
    )
    l2 = load_l2_instance(_DEFAULT_L2)
    apps = [
        ("L1 Dashboard", build_l1_dashboard_app(
            effective_cfg, l2_instance=l2)),
        ("L2 Flow Tracing", build_l2_flow_tracing_app(
            effective_cfg, l2_instance=l2)),
        ("Investigation", build_investigation_app(
            effective_cfg, l2_instance=l2)),
        ("Executives", build_executives_app(
            effective_cfg, l2_instance=l2)),
    ]
    for _, app in apps:
        app.emit_analysis()  # resolves auto-IDs
    return apps


def enumerate_picker_sheets() -> list[PickerSheet]:
    """Yield one ``PickerSheet`` per (app, sheet) where ≥1 picker is
    exercisable. Date-only sheets (Executives' coverage / volume /
    money-moved) yield no PickerSheet — they have only date pickers
    which the universal-date test covers.

    Stable ordering (app-decl order, sheet-decl order) so pytest
    parametrize ids are reproducible across runs.
    """
    out: list[PickerSheet] = []
    for app_name, app in _build_apps():
        dashboard_id = str(app.name)
        assert app.analysis is not None
        for sheet in app.analysis.sheets:
            specs = _picker_specs_for_sheet(sheet)
            if not specs:
                continue
            out.append(PickerSheet(
                app_name=app_name,
                dashboard_id=dashboard_id,
                sheet_name=sheet.name,
                picker_specs=specs,
            ))
    return out
