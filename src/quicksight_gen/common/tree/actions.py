"""Typed drill action wrapper (L.1.10).

A ``Drill`` is one custom action attached to a typed Visual — left-click
or right-click on a data point fires it. The drill navigates to a
target sheet (same-sheet or cross-sheet) and optionally sets parameter
values from the clicked data point.

The L.1.10 typed wrapper keeps K.2's shape-validated parameter writes
(``DrillParam`` + ``DrillSourceField`` + ``ColumnShape``) and adds:

- ``target_sheet`` is a typed ``Sheet`` object ref, not a ``SheetId``
  string. The App's emit-time validation catches "drill into a sheet
  that isn't on this analysis" the same way the dataset and calc-field
  walks catch unregistered references.
- ``action_id`` is Optional — the App walker assigns
  ``act-s{sheet_idx}-v{visual_idx}-{action_idx}`` at emit time.

Visual subtypes (KPI / Table / BarChart / Sankey) accept a typed
``actions: list[Drill]`` field; their ``emit()`` passes the resolved
list into the underlying model's ``Actions`` slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from quicksight_gen.common.drill import (
    DrillResetSentinel,
    DrillSourceField,
    DrillStaticDateTime,
    cross_sheet_drill as _emit_cross_sheet_drill,
)
from quicksight_gen.common.drill import DrillParam as _DrillParam
from quicksight_gen.common.models import (
    CustomActionFilterOperation,
    FilterOperationSelectedFieldsConfiguration,
    FilterOperationTargetVisualsConfiguration,
    SameSheetTargetVisualConfiguration,
    VisualCustomAction,
    VisualCustomActionOperation,
)

from quicksight_gen.common.tree._helpers import AUTO, AutoResolved, _AutoSentinel
from quicksight_gen.common.tree.calc_fields import (
    calc_field_in,
    resolve_column,
)
from quicksight_gen.common.tree.fields import Dim, Measure
# Sheet is referenced via TYPE_CHECKING — same trick as filters.py
# uses for the FilterGroup → Sheet ref. Avoids circular import.
from typing import TYPE_CHECKING, Union
if TYPE_CHECKING:
    from quicksight_gen.common.tree.structure import Sheet


# Re-exports so user code only needs to import from quicksight_gen.common.tree:
DrillParam = _DrillParam  # K.2 shape-validated parameter spec — name + ColumnShape
__all__ = [
    "Action",
    "Drill",
    "DrillParam",
    "DrillSourceField",
    "DrillResetSentinel",
    "DrillStaticDateTime",
    "SameSheetFilter",
]


# A typed drill write — pairs a destination DrillParam with one of:
# - Dim / Measure object ref (the Sankey source column, the Table
#   counterparty calc-field column, etc.) — Drill.emit() resolves the
#   field_id + shape via the dataset contract at emit time.
# - DrillSourceField — explicit field_id + shape pair, kept as the
#   escape hatch when callers need to override the auto-resolved shape.
# - DrillResetSentinel — clears the parameter to PASS.
# - DrillStaticDateTime — writes a fixed ISO-8601 datetime literal
#   (e.g. wide-window date-range writes on cross-sheet drills into
#   universally-date-filtered destination sheets).
DrillWriteSource = Union[
    Dim, Measure, DrillSourceField, DrillResetSentinel, DrillStaticDateTime,
]
DrillWrite = tuple[DrillParam, DrillWriteSource]


def _resolve_drill_source(
    source: DrillWriteSource,
) -> Union[DrillSourceField, DrillResetSentinel, DrillStaticDateTime]:
    """Convert a tree-side drill write source into the K.2 type.

    Dim / Measure refs are translated:
    - field_id read off the (now-resolved) leaf
    - shape pulled from the dataset contract for real columns; from
      ``CalcField.shape`` for calc-field columns

    DrillSourceField / DrillResetSentinel / DrillStaticDateTime pass
    through unchanged — they're already terminal write values.
    """
    if isinstance(source, (DrillSourceField, DrillResetSentinel, DrillStaticDateTime)):
        return source
    # Dim / Measure path — resolve field_id + shape.
    leaf = source
    assert not isinstance(leaf.field_id, _AutoSentinel), (
        "Drill source field_id wasn't resolved — App.resolve_auto_ids() "
        "must run before Drill.emit()."
    )
    calc = calc_field_in(leaf.column)
    if calc is not None:
        if calc.shape is None:
            raise TypeError(
                f"Drill source {leaf.field_id!r} reads calc field "
                f"{calc.name!r} but the calc field has no ``shape`` tag — "
                f"set ``shape=ColumnShape.<X>`` on the CalcField so the "
                f"drill parameter binding can type-check."
            )
        return DrillSourceField(field_id=leaf.field_id, shape=calc.shape)
    # Real column path — derive shape from the dataset contract.
    from quicksight_gen.common.drill import field_source
    return field_source(
        field_id=leaf.field_id,
        dataset_id=leaf.dataset.identifier,
        column_name=resolve_column(leaf.column),
    )


@dataclass(eq=False)
class Drill:
    """One custom action on a Visual.

    ``target_sheet`` is the destination ``Sheet`` object ref. Two
    binding modes:

    - **Cross-sheet drill** — pass an explicit ``target_sheet=sheet``.
      The drill navigates to that sheet (and writes parameter values
      to it).
    - **Same-sheet drill** (the walk-the-flow / re-render-around-new-anchor
      pattern) — leave ``target_sheet`` as ``None``. ``App.resolve_auto_ids``
      back-fills the field with the sheet that owns the visual carrying
      the drill, so the author never types ``target_sheet=this_sheet``
      when wiring a drill inside the function that builds the sheet.
      Resolves the chicken-and-egg cycle (sheet doesn't exist yet when
      the drill is constructed inside ``Sheet.add_visual(...)``) without
      a back-fill loop at the call site.

    ``writes`` is a list of ``(DrillParam, DrillSourceField | DrillResetSentinel)``
    tuples — same shape K.2 introduced. The ``DrillParam`` carries
    its own ``ColumnShape``; ``DrillSourceField.shape`` must match
    or ``cross_sheet_drill`` raises (call-site shape validation).

    ``trigger`` picks the click semantic — ``DATA_POINT_CLICK`` for
    left-click, ``DATA_POINT_MENU`` for right-click context menu.

    ``action_id`` is Optional — the App walker assigns one at emit
    time when not specified.

    ``name`` is the visible label QuickSight shows in the right-click
    menu (for DATA_POINT_MENU triggers). For DATA_POINT_CLICK actions
    the name doesn't surface in the UI but is still required by the
    underlying model.
    """
    writes: list[DrillWrite]
    name: str
    trigger: Literal["DATA_POINT_CLICK", "DATA_POINT_MENU"] = "DATA_POINT_CLICK"
    action_id: str | AutoResolved = AUTO
    target_sheet: "Sheet | AutoResolved" = AUTO

    _AUTO_KIND: ClassVar[str] = "drill"

    def emit(self) -> VisualCustomAction:
        assert not isinstance(self.action_id, _AutoSentinel), (
            "action_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        assert not isinstance(self.target_sheet, _AutoSentinel), (
            "target_sheet wasn't resolved — App.resolve_auto_ids() must "
            "run before Drill.emit(). Same-sheet drills get back-filled "
            "with the owning sheet automatically."
        )
        resolved_writes = [
            (param, _resolve_drill_source(source))
            for param, source in self.writes
        ]
        return _emit_cross_sheet_drill(
            action_id=self.action_id,
            name=self.name,
            target_sheet=self.target_sheet.sheet_id,
            writes=resolved_writes,
            trigger=self.trigger,
        )


# Forward reference — VisualLike is in visuals.py; import via TYPE_CHECKING.
if TYPE_CHECKING:
    from quicksight_gen.common.tree.visuals import VisualLike


@dataclass(eq=False)
class SameSheetFilter:
    """A click action that filters target visuals on the same sheet via
    ALL_FIELDS — the click-a-bar-to-narrow-the-table pattern.

    ``target_visuals`` is a list of typed ``VisualLike`` object refs.
    The action's emitted ``TargetVisuals`` field reads each visual's
    (resolved) ``visual_id`` at emit time, so the action survives auto-
    ID resolution and refactors that rename a visual.

    Distinct from ``Drill`` — emits a ``FilterOperation`` rather than a
    ``NavigationOperation`` + ``SetParametersOperation`` pair. Doesn't
    cross sheets, doesn't write parameters.
    """
    target_visuals: list["VisualLike"]
    name: str
    trigger: Literal["DATA_POINT_CLICK", "DATA_POINT_MENU"] = "DATA_POINT_CLICK"
    action_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "filter"

    def emit(self) -> VisualCustomAction:
        assert not isinstance(self.action_id, _AutoSentinel), (
            "action_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        target_ids: list[str] = []
        for v in self.target_visuals:
            assert not isinstance(v.visual_id, _AutoSentinel), (
                f"SameSheetFilter target visual_id wasn't resolved — "
                f"App.resolve_auto_ids() must run before emit."
            )
            target_ids.append(v.visual_id)
        return VisualCustomAction(
            CustomActionId=self.action_id,
            Name=self.name,
            Trigger=self.trigger,
            ActionOperations=[
                VisualCustomActionOperation(
                    FilterOperation=CustomActionFilterOperation(
                        SelectedFieldsConfiguration=(
                            FilterOperationSelectedFieldsConfiguration(
                                SelectedFieldOptions="ALL_FIELDS",
                            )
                        ),
                        TargetVisualsConfiguration=(
                            FilterOperationTargetVisualsConfiguration(
                                SameSheetTargetVisualConfiguration=(
                                    SameSheetTargetVisualConfiguration(
                                        TargetVisuals=target_ids,
                                    )
                                ),
                            )
                        ),
                    ),
                ),
            ],
        )


# Discriminated union of every visual action type. Visual subtypes
# accept ``actions: list[Action]`` to mix Drills + filters in one list.
Action = Union[Drill, SameSheetFilter]
