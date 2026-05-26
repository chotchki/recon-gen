"""Class-level test: every BarChart emits plain-English axis labels.

Pre-v8.5.5 ``BarChart`` accepted optional ``category_label`` /
``value_label`` / ``color_label`` overrides but defaulted them to
None — sites that didn't pass an explicit override emitted no
``CategoryLabelOptions`` at all, and QuickSight rendered the raw
snake_case column name as the axis title (``account_id``,
``signed_amount``).

v8.5.5 wires ``BarChart.emit()`` to fall back to the first leaf's
``human_name`` when the author didn't override — same pipeline
Table column headers (v8.5.0) use, via the shared ``_field_label``
helper. Authors retain full control by passing an explicit
``category_label="..."`` / ``value_label="..."`` / ``color_label="..."``.

This walker builds every shipped app's analysis JSON, finds every
``BarChartVisual`` in any sheet, and asserts:

1. Every populated axis emits ``CategoryLabelOptions`` /
   ``ValueLabelOptions`` / ``ColorLabelOptions`` (whichever
   correspond to non-empty field-well lists).
2. None of the emitted ``CustomLabel`` strings survive in raw
   snake_case form. A regression here means a new BarChart slipped
   through without going through the human_name pipeline OR a
   column was added with an explicit ``display_name`` set to a
   snake_case string.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

import pytest

from tests._test_helpers import make_test_config


_CFG = make_test_config()

# Type aliases — AWS QS JSON dicts have heterogeneous nested shapes; using
# Any here matches the dynamic-dict walk these tests perform.
_JsonDict = dict[str, Any]


# Same shape as ``test_table_column_headers.py``: all-lowercase with
# at least one underscore. The smart-title pass always produces at
# least one uppercase letter (the leading word).
_SNAKE_CASE_LABEL = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")


def _all_bar_chart_visuals(
    emitted: _JsonDict,
) -> Iterator[tuple[str, str, _JsonDict]]:
    """Yield ``(sheet_id, visual_id, bar_chart_visual_dict)`` for every
    BarChartVisual in the emitted analysis."""
    definition: _JsonDict = emitted.get("Definition") or {}
    sheets: list[_JsonDict] = definition.get("Sheets") or []
    for sheet in sheets:
        sheet_id: str = sheet.get("SheetId", "<unknown>")
        visuals: list[_JsonDict] = sheet.get("Visuals") or []
        for v in visuals:
            bv: _JsonDict | None = v.get("BarChartVisual")
            if bv is not None:
                yield sheet_id, bv.get("VisualId", "<unknown>"), bv


def _build_all_apps() -> Iterator[tuple[str, _JsonDict]]:
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.apps.l2_flow_tracing.app import (
        build_l2_flow_tracing_app,
    )
    from recon_gen.apps.investigation.app import build_investigation_app
    from recon_gen.apps.executives.app import build_executives_app

    builders = [
        ("l1_dashboard", build_l1_dashboard_app),
        ("l2_flow_tracing", build_l2_flow_tracing_app),
        ("investigation", build_investigation_app),
        ("executives", build_executives_app),
    ]
    for name, build in builders:
        app = build(_CFG)
        emitted = app.emit_analysis().to_aws_json()
        yield name, emitted


def _axes_present(bv: _JsonDict) -> dict[str, list[_JsonDict]]:
    """Return ``{axis_name: well_list}`` for every populated field well
    on a BarChartVisual. ``axis_name`` is one of ``"Category"`` /
    ``"Values"`` / ``"Colors"``; ``well_list`` is the raw list of leaves."""
    chart: _JsonDict = bv.get("ChartConfiguration") or {}
    field_wells: _JsonDict = chart.get("FieldWells") or {}
    agg: _JsonDict = field_wells.get("BarChartAggregatedFieldWells") or {}
    out: dict[str, list[_JsonDict]] = {}
    for axis_name in ("Category", "Values", "Colors"):
        wells: list[_JsonDict] = agg.get(axis_name) or []
        if wells:
            out[axis_name] = wells
    return out


# Map BarChartAggregatedFieldWells axis names to the corresponding
# ChartConfiguration label-options key.
_AXIS_TO_LABEL_OPT = {
    "Category": "CategoryLabelOptions",
    "Values": "ValueLabelOptions",
    "Colors": "ColorLabelOptions",
}


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_every_populated_bar_chart_axis_emits_label_options(
    app_name: str, emitted: _JsonDict,
) -> None:
    """Class regression: every populated BarChart axis must emit a
    label-options entry so QuickSight has an explicit axis title
    instead of falling back to the raw column name."""
    bad: list[str] = []
    for sheet_id, visual_id, bv in _all_bar_chart_visuals(emitted):
        chart: _JsonDict = bv.get("ChartConfiguration") or {}
        present = _axes_present(bv)
        for axis_name in present:
            opt_key = _AXIS_TO_LABEL_OPT[axis_name]
            if chart.get(opt_key) is None:
                bad.append(
                    f"  sheet={sheet_id!r} visual={visual_id!r} "
                    f"axis={axis_name} missing={opt_key}"
                )
    assert not bad, (
        f"App {app_name!r} has BarChart axes without label options — "
        f"QuickSight will fall back to the raw snake_case column "
        f"name as the axis title:\n" + "\n".join(bad)
    )


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_no_bar_chart_axis_label_renders_as_snake_case(
    app_name: str, emitted: _JsonDict,
) -> None:
    """Class regression: no axis label on any BarChart survives in raw
    snake_case form (e.g. ``account_id``, ``signed_amount``).

    A failure here means either:
    1. A new BarChart was added that didn't go through the v8.5.5
       _field_label pipeline, OR
    2. A column was added to a contract with an explicit
       ``display_name`` set to a snake_case string (don't do that —
       use the plain-English form).
    """
    bad: list[str] = []
    for sheet_id, visual_id, bv in _all_bar_chart_visuals(emitted):
        chart: _JsonDict = bv.get("ChartConfiguration") or {}
        for opt_key in _AXIS_TO_LABEL_OPT.values():
            opts: _JsonDict = chart.get(opt_key) or {}
            entries: list[_JsonDict] = opts.get("AxisLabelOptions") or []
            for entry in entries:
                label = entry.get("CustomLabel", "")
                if label and _SNAKE_CASE_LABEL.match(label):
                    bad.append(
                        f"  sheet={sheet_id!r} visual={visual_id!r} "
                        f"opt={opt_key} label={label!r}"
                    )
    assert not bad, (
        f"App {app_name!r} has BarChart axis labels in raw "
        f"snake_case form. Either the column needs a "
        f"``display_name`` override on its ``ColumnSpec``, or the "
        f"BarChart needs an explicit ``category_label`` / "
        f"``value_label`` / ``color_label`` override:\n"
        + "\n".join(bad)
    )


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_every_bar_chart_axis_label_carries_apply_to(
    app_name: str, emitted: _JsonDict,
) -> None:
    """Class regression (v8.6.1): every ``CustomLabel`` must carry an
    ``ApplyTo`` ref binding it to the field-well leaf.

    Without ``ApplyTo`` (FieldId + ColumnIdentifier), QuickSight
    silently ignores the override and the chart renders the raw
    column name on the axis. Pre-v8.6.1 the emit set ``CustomLabel``
    only — the v8.5.5 "labels keep not landing" symptom. Table
    column headers always land because they bind via
    ``TableFieldOption.FieldId``; chart axes need the equivalent
    binding via ``AxisLabelOptions.ApplyTo``.
    """
    bad: list[str] = []
    for sheet_id, visual_id, bv in _all_bar_chart_visuals(emitted):
        chart: _JsonDict = bv.get("ChartConfiguration") or {}
        for opt_key in _AXIS_TO_LABEL_OPT.values():
            opts: _JsonDict = chart.get(opt_key) or {}
            entries: list[_JsonDict] = opts.get("AxisLabelOptions") or []
            for entry in entries:
                if entry.get("CustomLabel") and not entry.get("ApplyTo"):
                    bad.append(
                        f"  sheet={sheet_id!r} visual={visual_id!r} "
                        f"opt={opt_key} label="
                        f"{entry.get('CustomLabel')!r} missing ApplyTo"
                    )
    assert not bad, (
        f"App {app_name!r} has BarChart axis labels with CustomLabel "
        f"but no ApplyTo ref. QuickSight silently ignores the label "
        f"without ApplyTo — the axis will render the raw column "
        f"name:\n" + "\n".join(bad)
    )
