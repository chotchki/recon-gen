"""Class-level test: every Table visual emits plain-English column headers.

Pre-v8.5.0 Table visuals rendered the raw snake_case column name as
the header (``account_id``, ``business_day_start``, etc.). v8.5.0 wires
``ColumnSpec.human_name`` through to ``TableConfiguration.FieldOptions``
so every column header is title-cased by default (with smart
initialism handling: ``id`` → ``ID``, ``eod`` → ``EOD``).

This walker builds every shipped app's analysis JSON, finds every
``TableVisual`` in any sheet, and asserts:

1. ``ChartConfiguration.FieldOptions`` exists.
2. The ``SelectedFieldOptions`` list has one entry per field-well
   leaf (group_by + values for aggregated tables, columns for
   unaggregated).
3. No CustomLabel survives in raw snake_case form (i.e. lowercase
   with underscores). A regression here means a new Table visual
   slipped through without going through the human_name pipeline.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

import pytest

from tests._test_helpers import make_test_config


_CFG = make_test_config()

# Type alias — AWS QS JSON dicts have heterogeneous nested shapes; using
# Any matches the dynamic-dict walk these tests perform.
_JsonDict = dict[str, Any]


# A label that looks like a raw snake_case column name: all lowercase,
# at least one underscore. The smart-title pipeline always emits at
# least one uppercase letter (the leading word) and replaces every
# underscore with a space, so any match is a regression.
_SNAKE_CASE_LABEL = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")


def _all_table_visuals(
    emitted: _JsonDict,
) -> Iterator[tuple[str, str, _JsonDict]]:
    """Yield ``(sheet_id, visual_id, table_visual_dict)`` for every
    Table visual in the emitted analysis."""
    definition: _JsonDict = emitted.get("Definition") or {}
    sheets: list[_JsonDict] = definition.get("Sheets") or []
    for sheet in sheets:
        sheet_id: str = sheet.get("SheetId", "<unknown>")
        visuals: list[_JsonDict] = sheet.get("Visuals") or []
        for v in visuals:
            tv: _JsonDict | None = v.get("TableVisual")
            if tv is not None:
                yield sheet_id, tv.get("VisualId", "<unknown>"), tv


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


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_every_table_visual_has_field_options(
    app_name: str, emitted: _JsonDict,
) -> None:
    """Class regression: every Table visual must carry
    ``ChartConfiguration.FieldOptions`` so QuickSight has explicit
    per-column header overrides instead of falling back to the raw
    column name."""
    bad: list[str] = []
    for sheet_id, visual_id, tv in _all_table_visuals(emitted):
        chart: _JsonDict = tv.get("ChartConfiguration") or {}
        field_options: _JsonDict | None = chart.get("FieldOptions")
        if field_options is None:
            bad.append(f"  sheet={sheet_id!r} visual={visual_id!r}")
    assert not bad, (
        f"App {app_name!r} has Table visuals without FieldOptions — "
        f"the column headers will fall back to the raw snake_case "
        f"column name in QuickSight:\n" + "\n".join(bad)
    )


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_no_table_column_header_renders_as_snake_case(
    app_name: str, emitted: _JsonDict,
) -> None:
    """Class regression: no CustomLabel on any Table column survives
    in raw snake_case form (e.g. ``account_id``, ``business_day_start``).

    A failure here means either:
    1. A new Table visual was added that didn't go through the
       v8.5.0 human_name pipeline, OR
    2. A column was added to a contract with an explicit
       ``display_name`` set to a snake_case string (don't do that —
       use the plain-English form).
    """
    bad: list[str] = []
    for sheet_id, visual_id, tv in _all_table_visuals(emitted):
        chart: _JsonDict = tv.get("ChartConfiguration") or {}
        field_options: _JsonDict = chart.get("FieldOptions") or {}
        opts: list[_JsonDict] = field_options.get("SelectedFieldOptions") or []
        for opt in opts:
            label: str = opt.get("CustomLabel", "")
            if label and _SNAKE_CASE_LABEL.match(label):
                bad.append(
                    f"  sheet={sheet_id!r} visual={visual_id!r} "
                    f"field={opt.get('FieldId')!r} label={label!r}"
                )
    assert not bad, (
        f"App {app_name!r} has Table column headers in raw "
        f"snake_case form. Either the column needs a "
        f"``display_name`` override on its ``ColumnSpec``, or its "
        f"name shouldn't have been left as snake_case in the first "
        f"place:\n" + "\n".join(bad)
    )


@pytest.mark.parametrize("app_name,emitted", list(_build_all_apps()))
def test_field_options_count_matches_field_well_leaves(
    app_name: str, emitted: _JsonDict,
) -> None:
    """Class regression: the SelectedFieldOptions list must have
    exactly one entry per field-well leaf (Dim or Measure). A
    mismatch means the FieldOptions and the actual field wells got
    out of sync — QuickSight will show some columns with raw
    snake_case headers and others with the override."""
    bad: list[str] = []
    for sheet_id, visual_id, tv in _all_table_visuals(emitted):
        chart: _JsonDict = tv.get("ChartConfiguration") or {}
        field_wells: _JsonDict = chart.get("FieldWells") or {}
        field_options: _JsonDict = chart.get("FieldOptions") or {}

        # Count leaves across both well shapes.
        leaf_count = 0
        agg: _JsonDict | None = field_wells.get("TableAggregatedFieldWells")
        if agg is not None:
            leaf_count += len(agg.get("GroupBy") or [])
            leaf_count += len(agg.get("Values") or [])
        unagg: _JsonDict | None = field_wells.get("TableUnaggregatedFieldWells")
        if unagg is not None:
            leaf_count += len(unagg.get("Values") or [])

        opts_count = len(field_options.get("SelectedFieldOptions") or [])
        if leaf_count != opts_count:
            bad.append(
                f"  sheet={sheet_id!r} visual={visual_id!r} "
                f"leaves={leaf_count} opts={opts_count}"
            )
    assert not bad, (
        f"App {app_name!r} has Table visuals where FieldOptions "
        f"count doesn't match field-well leaf count:\n"
        + "\n".join(bad)
    )
