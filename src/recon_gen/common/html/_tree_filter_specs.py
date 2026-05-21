"""Y.2.app2.cde.l2ft-wiring.b + AA.A.3 — derive App2 filter-form specs
from a tree ``Sheet``'s parameter-control nodes.

App2's filter form (``render._render_filter_form``) renders the universal
date pickers plus an explicit ``FilterSpec`` list. Until now every tree app
passed ``filter_specs=()``, so a sheet's QuickSight ``ParameterDropdown``
controls rendered as nothing in App2 — the dataset SQL still applied the
declared-value default (Y.2.app2.cde.core), so visuals showed every row,
but the analyst couldn't narrow.

This walk closes that gap for four control shapes:

- **MULTI_SELECT + StaticValues** → ``ParameterMultiSelectSpec`` → a
  ``<select multiple name="param_<name>">``. The selected options
  serialise as repeated ``?param_<name>=A&param_<name>=B`` query keys,
  which is exactly the shape
  ``_sql_executor.expand_multivalued_dataset_params`` consumes (it reads
  ``url_params.get(f"param_{name}")`` as a list and expands
  ``<<$name>>`` to ``:param_name_0, :param_name_1, …``). Nothing
  selected → no key → the executor's static-default fallback kicks in
  (= no narrowing), mirroring QuickSight's "empty the dropdown reverts
  to default" behaviour.
- **SINGLE_SELECT + StaticValues** → ``ParameterDropdownSpec`` → a
  ``<select name="param_<name>">`` with a blank leading option. Single
  value submits as ``?param_<name>=<v>``, which ``_sql_executor``
  translates to a scalar ``:param_<name>`` bind for the dataset SQL's
  ``<<$pName>>`` placeholder (the same narrowing QS does). AA.A.3
  flipped every L1 + L2FT pushdown dropdown to this shape per the
  drill-to-one default; the deriver case was added in the same phase so
  App2 keeps the filter widgets it gained in Y.2.app2.cde.l2ft-wiring.b.
- **SINGLE_SELECT + LinkedValues** → ``ParameterDropdownSpec`` with
  ``options_dataset`` / ``options_column`` (X.2.u.4.b — Daily
  Statement's Account picker, L1 Account / Transfer / Status / Origin
  data-value dropdowns post-AA.A.3); the server resolves the option
  list by querying the source dataset before rendering.
- **``ParameterSlider``** → ``ParameterNumberSpec`` → an ``<input
  type="number" name="param_<name>">`` + a one-handle noUiSlider
  (single-value scalar bind, the same ``<<$param>>`` narrowing QS does).

``ParameterDateTimePicker`` → ``ParameterDateSpec`` (AO.2 — a single-date
Flatpickr control, e.g. the Daily Statement's Business Day; it also
suppresses the universal date-RANGE on its sheet).

Out of scope: ``ParameterTextField`` controls (a different shape —
text-field parity not yet needed). Those are silently skipped — the form
just won't carry a widget for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from recon_gen.common.html.render import (
    FilterSpec,
    ParameterDateSpec,
    ParameterDropdownSpec,
    ParameterMultiSelectSpec,
    ParameterNumberSpec,
)
from recon_gen.common.tree import (
    LinkedValues,
    ParameterDateTimePicker,
    ParameterDropdown,
    ParameterSlider,
    StaticValues,
)

if TYPE_CHECKING:
    from recon_gen.common.tree import Sheet


def make_filter_specs_for_sheet(sheet: "Sheet") -> list[FilterSpec]:
    """Return the App2 filter-form specs auto-derived from ``sheet``'s
    parameter-control nodes.

    Order follows the sheet's ``parameter_controls`` order so the filter
    bar matches the QuickSight control layout. Sheets with no such control
    return ``[]`` (the form is then date-pickers-only, and is suppressed
    entirely for text-box-only sheets per ``render``'s existing logic).

    Coverage:

    - **MULTI_SELECT + StaticValues** → ``ParameterMultiSelectSpec`` with
      inlined ``options`` (Y.2.app2.cde.l2ft-wiring.b — pre-AA.A.3 L2FT
      Rail / Status / Bundle + L1 Account-Role / Transfer-Type / Rail
      enums; AA.A.3 flipped these to SINGLE_SELECT, so the MULTI case
      handles only future genuinely-multi keepers).
    - **MULTI_SELECT + LinkedValues** → ``ParameterMultiSelectSpec`` carrying
      ``options_dataset`` / ``options_column`` (X.2.u.4.b — pre-AA.A.3 L1
      Account / Transfer / Status / Origin data-value dropdowns; same
      AA.A.3 flip caveat as above).
    - **SINGLE_SELECT + StaticValues** → ``ParameterDropdownSpec`` with
      inlined ``options`` (AA.A.3 — post-flip L1 + L2FT enum dropdowns:
      Rail / Status / Bundle / Completion / Transfer-Type / Account-Role /
      Check-Type / Supersede-Reason; also the L2FT metadata-cascade key
      dropdowns picked up a widget in App2 as a side benefit).
    - **SINGLE_SELECT + LinkedValues** → ``ParameterDropdownSpec`` likewise
      (X.2.u.4.b — Daily Statement's Account picker; AA.A.3 — post-flip
      L1 data-value dropdowns for Account / Transfer / Status / Origin).
    - **``ParameterSlider``** → ``ParameterNumberSpec`` (X.2.u.4.e —
      Investigation's σ / max-hops / min-amount threshold knobs); the
      number input is the wire element, with a one-handle noUiSlider over
      it. Initial value = the bound parameter's analysis-level default if
      declared (so it matches the dataset SQL's static-default literal),
      else the slider minimum.

    Still skipped: ``ParameterTextField`` controls (a different shape —
    text-field parity not yet needed); ``add_parameter_datetime_picker``
    controls (date-control parity is the universal date range, handled
    separately). Skipped controls just don't get a widget.
    """
    specs: list[FilterSpec] = []
    # AO.2 — a LONE datetime picker is a single-date sheet (Daily
    # Statement's Business Day); a From/To PAIR is a date RANGE (L2FT /
    # Executives / L1 invariant sheets). Only the lone case becomes a
    # single-date control + suppresses the universal range; the pair stays
    # out-of-scope so the universal date-range form keeps driving it.
    n_date_pickers = sum(
        isinstance(c, ParameterDateTimePicker)
        for c in sheet.parameter_controls
    )
    for ctrl in sheet.parameter_controls:
        if isinstance(ctrl, ParameterSlider):
            # X.2.u.4.e — a slider bound to a numeric parameter
            # (Investigation's σ / max-hops / min-amount knobs). App2
            # renders an <input type="number" name="param_<name>"> plus a
            # one-handle noUiSlider; the value submits as a single
            # ?param_<name>=<v> key (scalar bind for the dataset SQL's
            # <<$param>> placeholder — the same narrowing QS does).
            # Initial value = the bound parameter's analysis-level default
            # if it declares one (== the SQL static-default), else the
            # slider minimum.
            default_vals = getattr(ctrl.parameter, "default", None) or []
            specs.append(ParameterNumberSpec(
                name=str(ctrl.parameter.name),
                label=ctrl.title,
                minimum=float(ctrl.minimum_value),
                maximum=float(ctrl.maximum_value),
                step=float(ctrl.step_size),
                default=float(default_vals[0]) if default_vals else None,
            ))
            continue
        if isinstance(ctrl, ParameterDateTimePicker):
            # AO.2 — a LONE datetime picker is a single date (Daily
            # Statement's Business Day, bound to a SQL-pushed-down param):
            # render a Flatpickr single-date input → ``?param_<name>=
            # YYYY-MM-DD`` (the same ``<<$param>>`` narrowing QS does via
            # the bridged dataset param), and the resulting spec suppresses
            # the universal date-RANGE on the sheet. A From/To PAIR (count
            # > 1) is a range — leave those out-of-scope so the universal
            # date-range form keeps driving them (pre-AO.2 behaviour).
            if n_date_pickers == 1:
                specs.append(ParameterDateSpec(
                    name=str(ctrl.parameter.name), label=ctrl.title,
                ))
            continue
        if not isinstance(ctrl, ParameterDropdown):
            continue
        sv = ctrl.selectable_values
        name = str(ctrl.parameter.name)
        if ctrl.type == "MULTI_SELECT" and isinstance(sv, StaticValues):
            specs.append(ParameterMultiSelectSpec(
                name=name, label=ctrl.title, options=tuple(sv.values),
            ))
        elif ctrl.type == "MULTI_SELECT" and isinstance(sv, LinkedValues):
            specs.append(ParameterMultiSelectSpec(
                name=name, label=ctrl.title, options=(),
                options_dataset=sv.dataset.identifier,
                options_column=sv.column_name,
            ))
        elif ctrl.type == "SINGLE_SELECT" and isinstance(sv, StaticValues):
            # AA.A.3 — the post-flip enum dropdowns (Rail / Status /
            # Bundle / Completion / Transfer-Type / Account-Role / …).
            # Inlined options come from the control's StaticValues; the
            # blank leading option (rendered by _render_parameter_dropdown)
            # is the "clear" affordance — empty submit reverts the bridged
            # dataset param to its sentinel default (= all rows).
            specs.append(ParameterDropdownSpec(
                name=name, label=ctrl.title, options=tuple(sv.values),
            ))
        elif ctrl.type == "SINGLE_SELECT" and isinstance(sv, LinkedValues):
            specs.append(ParameterDropdownSpec(
                name=name, label=ctrl.title, options=(),
                options_dataset=sv.dataset.identifier,
                options_column=sv.column_name,
            ))
    return specs
