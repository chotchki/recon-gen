"""Tree primitives for App / Dashboard / Analysis / Sheet construction.

Replaces the constant-heavy + manually-cross-referenced builders in
``apps/{payment_recon,account_recon,investigation}/{analysis,filters,
visuals}.py``. Authors construct apps as trees of typed nodes; the
tree walks itself at emit time to produce the existing ``models.py``
dataclasses, which serialize through the same ``to_aws_json()`` path
the deploy pipeline uses.

**Validation rules** (catch these at construction or emit time):

Construction-time (raise immediately):

- ``sheet.layout.row(height=H).add_<kind>(width=W, ...)`` —
  constructs + registers + places a visual atomically; refuses widths
  that overflow the 36-col grid. The duplicate-placement bug class is
  structurally impossible (no separate ``place`` step).
- ``sheet.scope(filter_group, [visuals])`` — every scoped visual must
  be on this sheet (catches the wrong-sheet bug).
- ``Analysis.add_sheet / add_parameter / add_filter_group /
  add_calc_field`` — rejects duplicate IDs (shadow-bug class).
- ``App.add_dataset`` — rejects duplicate dataset identifiers.
- ``App.create_dashboard(...)`` — requires ``set_analysis`` already ran;
  the analysis-mismatch bug class is structurally impossible (no
  opening to pass a different Analysis).
- ``NumericRangeFilter.__post_init__`` — rejects setting both
  ``minimum_value`` and ``minimum_parameter`` (or both on the
  maximum side).

Emit-time (validated by ``App.resolve_auto_ids`` + the
``_validate_*`` methods, all run from ``emit_analysis`` /
``emit_dashboard``):

- Auto-IDs resolve for any node that didn't carry an explicit ID.
- ``_validate_dataset_references`` — every typed Dataset ref in the
  tree must be registered on the App.
- ``_validate_calc_field_references`` — every typed CalcField ref
  must be registered on the analysis.
- ``_validate_parameter_references`` — every typed ParameterDeclLike
  ref (in controls + NumericRangeFilter parameter bounds) must be
  registered on the analysis.
- ``_validate_drill_destinations`` — every Drill action's
  ``target_sheet`` must be a registered Sheet on the analysis.
- ``FilterGroup.emit`` — refuses an unscoped FilterGroup.
- ``cross_sheet_drill`` (K.2) — Drill ``DrillParam`` shape must
  match the source field's ``ColumnShape``.

Known follow-up: ``DrillParam`` (in ``common/drill.py``) takes a
string ``ParameterName`` rather than a typed ``ParameterDeclLike``
ref. That string isn't validated against the analysis registry —
typos in DrillParam.name flow to deploy. Closing the gap requires
threading a typed parameter ref through ``DrillParam`` →
``cross_sheet_drill`` → emission.

**Locked decisions** (see PLAN.md Phase L):

- Cross-references are object refs, not string IDs. ``GridSlot.element``
  takes any ``LayoutNode`` (typed visuals + ``TextBox``);
  ``FilterGroup.scope_visuals`` takes ``(sheet, [visual, ...])``;
  drill destinations take ``Sheet`` refs.
- IDs appear once — at the constructor of the node that owns them.
  Per-app ``constants.py`` modules collapse: every other reference
  is the local Python variable holding the node ref.
- ``emit()`` per node is the universal interface; trees walk
  recursively to produce ``models.py`` instances.
- Visual subtypes are typed per kind (KPI, Table, Bar, Sankey).
  Same names as ``models.py`` where they exist; tree types alias
  models on import inside their own submodules to keep user-facing
  imports clean (``from recon_gen.common.tree import KPI,
  Sankey, FilterGroup`` etc.).

**Visual kind catalog** (L.1.1 finding, used in active codebase):
KPIVisual ×29, TableVisual ×22, BarChartVisual ×13,
SankeyDiagramVisual ×2. PieChartVisual is modeled but unused.

**Module organization:**

- ``_helpers`` — AUTO sentinel + label builders + permissions actions
- ``fields`` — ``Dim`` / ``Measure`` field-well leaf nodes
- ``parameters`` — ``ParameterDeclLike`` Protocol + ``StringParam``
  / ``IntegerParam`` / ``DateTimeParam``
- ``visuals`` — ``VisualLike`` Protocol + ``KPI`` / ``Table`` /
  ``BarChart`` / ``Sankey``
- ``filters`` — ``FilterGroup`` + typed Filter wrappers (CategoryFilter /
  NumericRangeFilter / TimeRangeFilter)
- ``controls`` — typed parameter / filter control variants
- ``structure`` — ``GridSlot`` / ``Sheet`` / ``Analysis`` / ``Dashboard``
  / ``App`` plus the ``SheetLayout`` / ``Row`` / ``AbsoluteSlot``
  layout DSL
"""

from __future__ import annotations

from recon_gen.common.tree.actions import (
    Action,
    Drill,
    DrillParam,
    DrillResetSentinel,
    DrillSourceField,
    DrillStaticDateTime,
    SameSheetFilter,
)
from recon_gen.common.tree.calc_fields import CalcField, ColumnRef
from recon_gen.common.tree.controls import (
    FilterControlLike,
    FilterCrossSheet,
    FilterDateTimePicker,
    FilterDropdown,
    FilterSlider,
    LinkedValues,
    ParameterControlLike,
    ParameterDateTimePicker,
    ParameterDropdown,
    ParameterSlider,
    ParameterTextField,
    SelectableValues,
    StaticValues,
)
from recon_gen.common.tree.datasets import Column, Dataset
from recon_gen.common.tree.date_view import DateView, EmptyBehavior
from recon_gen.common.tree.fields import (
    Dim,
    DimKind,
    Measure,
    MeasureKind,
)
from recon_gen.common.tree.formatting import (
    CellAccentMenu,
    CellAccentText,
    CellFormat,
)
from recon_gen.common.tree.filters import (
    Bound,
    CategoryFilter,
    CategoryMatchOperator,
    DefaultControl,
    DefaultDateTimePickerControl,
    DefaultDropdownControl,
    DefaultSliderControl,
    FilterGroup,
    FilterLike,
    NullOption,
    NumericRangeFilter,
    ParameterBound,
    SelectAllOptions,
    StaticBound,
    TimeEqualityFilter,
    TimeRangeFilter,
)
from recon_gen.common.tree.parameters import (
    DateTimeParam,
    IntegerParam,
    ParameterDeclLike,
    StringParam,
)
from recon_gen.common.tree.structure import (
    AbsoluteSlot,
    Analysis,
    App,
    Dashboard,
    GridSlot,
    LayoutNode,
    Row,
    Sheet,
    SheetLayout,
)
from recon_gen.common.tree._helpers import AUTO, AutoResolved, auto_id
from recon_gen.common.tree.text_boxes import TextBox
from recon_gen.common.tree.visuals import (
    KPI,
    BarChart,
    KPIValueZeroIndicator,
    LineChart,
    Sankey,
    Table,
    VisualLike,
)

__all__ = [
    # AUTO sentinel (L.1.18 — fields the App walker fills in later)
    "AUTO", "AutoResolved",
    # auto_id helper — for tests asserting on tree-position-derived UUIDs
    "auto_id",
    # Datasets
    "Dataset", "Column",
    # Date view primitive (D5; AR.1)
    "DateView", "EmptyBehavior",
    # Calc fields
    "CalcField", "ColumnRef",
    # Field-well leaves
    "Dim", "DimKind", "Measure", "MeasureKind",
    # Parameters
    "ParameterDeclLike", "StringParam", "IntegerParam", "DateTimeParam",
    # Visuals
    "VisualLike", "KPI", "Table", "BarChart", "LineChart", "Sankey",
    # KPI conditional formatting (BK.2)
    "KPIValueZeroIndicator",
    # Text boxes (typed wrapper for landing-page rich text)
    "TextBox",
    # Layout
    "LayoutNode",
    # Filters
    "FilterGroup", "FilterLike",
    "CategoryFilter", "NumericRangeFilter", "TimeRangeFilter",
    "TimeEqualityFilter",
    "Bound", "StaticBound", "ParameterBound",
    "CategoryMatchOperator", "NullOption", "SelectAllOptions",
    "DefaultControl", "DefaultDateTimePickerControl",
    "DefaultDropdownControl", "DefaultSliderControl",
    # Controls (L.1.9)
    "ParameterControlLike", "FilterControlLike",
    "ParameterDropdown", "ParameterSlider", "ParameterDateTimePicker",
    "ParameterTextField",
    "FilterDropdown", "FilterSlider", "FilterDateTimePicker", "FilterCrossSheet",
    "StaticValues", "LinkedValues", "SelectableValues",
    # Drill actions (L.1.10)
    "Action", "Drill", "DrillParam", "DrillSourceField", "DrillResetSentinel",
    "DrillStaticDateTime", "SameSheetFilter",
    # Conditional formatting (L.3.7-followup)
    "CellAccentText", "CellAccentMenu", "CellFormat",
    # Structure
    "GridSlot", "Sheet", "Analysis", "Dashboard", "App",
    # Layout DSL (L.1.21)
    "SheetLayout", "Row", "AbsoluteSlot",
]
