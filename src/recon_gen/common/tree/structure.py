"""Structural tree types — ``GridSlot`` / ``Sheet`` / ``Analysis`` /
``Dashboard`` / ``App``.

The skeleton the rest of the tree hangs off. Authors construct an
``App``, attach an ``Analysis`` (which holds the sheet tree),
optionally attach a ``Dashboard``, and call ``app.emit_analysis()``
/ ``app.emit_dashboard()`` to get the ``models.py`` instances ready
for deploy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from typing import Protocol, runtime_checkable

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import get_contract
from recon_gen.common.ids import FilterGroupId, SheetId, VisualId
from recon_gen.common.models import (
    AnalysisDefinition,
    DashboardPublishOptions,
    GridLayoutConfiguration,
    GridLayoutElement,
    Layout,
    LayoutConfiguration,
    ResourcePermission,
    SheetDefinition,
)
from recon_gen.common.models import Analysis as ModelAnalysis
from recon_gen.common.models import Dashboard as ModelDashboard
from recon_gen.common.tree._helpers import (
    ANALYSIS_ACTIONS,
    AUTO,
    AutoResolved,
    DASHBOARD_ACTIONS,
    GridLayoutElementType,
    _AutoSentinel,
    auto_id,
)
from recon_gen.common.tree.actions import Action, Drill
from recon_gen.common.tree.formatting import CellFormat
from recon_gen.common.tree.calc_fields import CalcField
from recon_gen.common.tree.controls import (
    FilterControlLike,
    FilterCrossSheet,
    FilterDateTimePicker,
    FilterDropdown,
    FilterSlider,
    ParameterControlLike,
    ParameterDateTimePicker,
    ParameterDropdown,
    ParameterTextField,
    ParameterSlider,
    SelectableValues,
)
from recon_gen.common.tree.datasets import Column, Dataset
from recon_gen.common.tree.fields import Dim, FieldRef, Measure, row_one_calc_name
from recon_gen.common.tree.filters import FilterGroup, FilterLike
from recon_gen.common.tree.parameters import ParameterDeclLike
from recon_gen.common.tree.text_boxes import TextBox
from recon_gen.common.tree.visuals import (
    KPI,
    BarChart,
    LineChart,
    Sankey,
    Table,
    VisualLike,
)


# Field-well slot roles used by the auto-id resolver. The `role`
# letter goes into the auto-derived field_id so the synthesized id
# encodes which well a leaf came from.
_FIELD_SLOTS: tuple[tuple[str, str], ...] = (
    ("group_by", "g"),  # Table (aggregated)
    ("values", "v"),    # KPI / Table / BarChart
    ("columns", "u"),   # Table (unaggregated)
    ("category", "c"),  # BarChart
    ("colors", "k"),    # BarChart (color/group dim)
    ("source", "s"),    # Sankey
    ("target", "t"),    # Sankey
    ("weight", "w"),    # Sankey
)


def _resolve_field_ids(
    *, visual: VisualLike, visual_kind: str, sheet_idx: int, visual_idx: int,
) -> None:
    """Walk a visual's field-well slots and assign auto field_ids to
    any Dim/Measure leaves that left field_id unset.

    Iterates the fixed ``_FIELD_SLOTS`` table — each entry names an
    attribute and a one-letter role tag. Missing attributes (e.g. KPI
    has no ``group_by``) are skipped via ``getattr`` default ``None``.
    Slots may be a single leaf (Sankey ``source`` / ``target`` /
    ``weight``) or a list (KPI / Table / BarChart ``values``); both
    shapes are handled.
    """
    for attr, role in _FIELD_SLOTS:
        slot: object = getattr(visual, attr, None)
        if slot is None:
            continue
        leaves: list[object] = list(slot) if isinstance(slot, list) else [slot]  # type: ignore[arg-type]: list(object) is list of leaves; slot narrowed by isinstance
        for slot_idx, leaf in enumerate(leaves):
            if leaf is None:
                continue
            if isinstance(getattr(leaf, "field_id", None), _AutoSentinel):
                leaf.field_id = auto_id(  # type: ignore[attr-defined]: leaf is one of many typed visual-field shapes (CalcField/Aggregated/etc.); all carry field_id
                    f"f-{visual_kind}-s{sheet_idx}-v{visual_idx}-{role}{slot_idx}"
                )


# ---------------------------------------------------------------------------
# Layout — GridSlot references a LayoutNode by object (locked decision).
# LayoutNode hides QuickSight's split between Visuals and TextBoxes —
# both turn into a GridLayoutElement carrying an id + ElementType, but
# flow into different SheetDefinition fields at emit time.
# ---------------------------------------------------------------------------

@runtime_checkable
class LayoutNode(Protocol):
    """Anything placeable in a sheet's grid layout.

    Both typed visual subtypes (``KPI`` / ``Table`` / ``BarChart`` /
    ``Sankey``) and the typed ``TextBox`` wrapper satisfy this Protocol.
    Each exposes ``element_id`` (the layout slot's ``ElementId``) and
    ``element_type`` (``"VISUAL"`` or ``"TEXT_BOX"``) — the slot reads
    them off the node at emit time.

    The Protocol means ``Sheet.place(node, ...)`` accepts both visuals
    and text boxes uniformly; QuickSight's two-list split (Visuals vs
    TextBoxes in ``SheetDefinition``) stays an emit-time concern that
    callers never see.
    """
    @property
    def element_id(self) -> str: ...

    @property
    def element_type(self) -> GridLayoutElementType: ...


@dataclass(eq=False)
class GridSlot:
    """One placement in a sheet's grid layout.

    Holds an OBJECT reference to the placed ``LayoutNode``. The element
    id and element type are read off the node at emit time — the slot
    is agnostic about whether it carries a visual or a text box.
    """
    element: LayoutNode
    col_span: int
    row_span: int
    col_index: int
    row_index: int | None = None

    def emit(self) -> GridLayoutElement:
        # v8.6.9 — Card layout padding (12px) on TEXT_BOX elements so
        # rendered prose doesn't sit flush against the card edges.
        # Visuals get QS's bare default (no padding) — they self-render
        # their own internal padding via ChartConfiguration title /
        # subtitle / data-area styling.
        padding = "12px" if self.element.element_type == "TEXT_BOX" else None
        return GridLayoutElement(
            ElementId=self.element.element_id,
            ElementType=self.element.element_type,
            ColumnSpan=self.col_span,
            RowSpan=self.row_span,
            ColumnIndex=self.col_index,
            RowIndex=self.row_index,
            Padding=padding,
        )


# ---------------------------------------------------------------------------
# Sheet — child of Analysis.
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class Sheet:
    """Tree node for one sheet on an Analysis / Dashboard.

    Sheet has four concerns:

    1. **Metadata** — ``sheet_id``, ``name``, ``title``, ``description``
       set at construction.
    2. **Layout** — visuals + text boxes placed in a grid. Accessed via
       ``sheet.layout``: ``sheet.layout.row(height=...).add_kpi(width=,
       title=, values=, ...)`` for sequential rows, or ``sheet.layout.
       absolute(col_index=, row_index=, col_span=, row_span=).add_*(...)``
       for explicit positioning. The layout's row tracks the column
       cursor so call sites don't compute ``col_index`` arithmetic by
       hand.
    3. **Controls** — parameter / filter controls live above/aside the
       canvas (NOT in the grid). Added directly on Sheet via
       ``sheet.add_parameter_dropdown(...)`` / ``add_parameter_slider``
       / ``add_parameter_datetime_picker`` / ``add_filter_dropdown`` /
       ``add_filter_slider`` / ``add_filter_datetime_picker`` /
       ``add_filter_cross_sheet`` — one shortcut per control kind.
    4. **Scope wiring** — ``sheet.scope(filter_group, [v1, v2])`` scopes
       a filter group to specific visuals on this sheet.

    ``emit()`` returns the ``SheetDefinition`` ready to drop into
    ``AnalysisDefinition.Sheets``.
    """
    sheet_id: SheetId
    name: str
    title: str
    description: str
    visuals: list[VisualLike] = field(default_factory=list[VisualLike])
    parameter_controls: list[ParameterControlLike] = field(
        default_factory=list[ParameterControlLike],
    )
    filter_controls: list[FilterControlLike] = field(default_factory=list[FilterControlLike])
    text_boxes: list[TextBox] = field(default_factory=list[TextBox])
    grid_slots: list[GridSlot] = field(default_factory=list["GridSlot"])
    # Lazy layout — constructed on first ``sheet.layout`` access, then
    # cached so the row cursor advances across calls. Init=False keeps
    # it out of the dataclass constructor surface.
    _layout: "SheetLayout | None" = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # b.15.invariant.sheet-description: every Sheet carries a
        # plain-language description (CLAUDE.md tree convention).
        # Catching a blank/missing description at construction time
        # beats letting a silently-described sheet through to a
        # rendered dashboard where reviewers spot it later.
        if not self.description.strip():
            raise ValueError(
                f"Sheet(name={self.name!r}, sheet_id={self.sheet_id!r}): "
                f"description is required and must be non-blank — every "
                f"sheet carries a one-paragraph plain-language "
                f"description (CLAUDE.md tree convention). Got: "
                f"{self.description!r}"
            )

    @property
    def layout(self) -> "SheetLayout":
        """L.1.21 — Layout namespace for grid placement.

        Visuals + text boxes are added through the layout (one of:
        ``sheet.layout.row(height=...).add_kpi(width=..., ...)`` or
        ``sheet.layout.absolute(col_index=..., row_index=..., col_span=...,
        row_span=...).add_kpi(...)``). The Sheet itself coordinates four
        concerns; layout is one of them, factored out so the call sites
        for "place a visual" don't crowd against "register a control" or
        "scope a filter group".

        Lazily constructed; the same SheetLayout instance returns on
        subsequent accesses so the row cursor advances across calls.
        """
        if self._layout is None:
            self._layout = SheetLayout(sheet=self)
        return self._layout

    # -----------------------------------------------------------------------
    # Control + scope shortcuts (L.1.21). Sheet's other concerns beyond
    # layout: parameter / filter controls (NOT placed in grid — they live
    # above/aside the canvas) and filter-group scoping (acts on visuals
    # from this sheet).
    # -----------------------------------------------------------------------

    def add_parameter_dropdown(
        self,
        *,
        parameter: ParameterDeclLike,
        title: str,
        selectable_values: SelectableValues,
        type: Literal["SINGLE_SELECT", "MULTI_SELECT"] = "SINGLE_SELECT",
        hidden_select_all: bool = False,
        cascade_source: ParameterDropdown | None = None,
        cascade_match_column: Column | None = None,
        control_id: str | AutoResolved = AUTO,
    ) -> ParameterDropdown:
        """Construct + register a parameter dropdown control on this sheet.

        ``selectable_values`` is required: a parameter dropdown without
        a source list (StaticValues / LinkedValues) shows only the
        QuickSight empty-state "All" placeholder, so the user can't
        actually pick a value — the bound parameter stays unset and
        any CategoryFilter using it matches nothing. Caught the L1
        Daily Statement account-dropdown footgun (v8.3.3 hotfix); the
        type makes it unrepresentable going forward.
        """
        ctrl = ParameterDropdown(
            parameter=parameter, title=title, type=type,
            selectable_values=selectable_values,
            hidden_select_all=hidden_select_all,
            cascade_source=cascade_source,
            cascade_match_column=cascade_match_column,
            control_id=control_id,
        )
        self.parameter_controls.append(ctrl)
        return ctrl

    def add_parameter_slider(
        self,
        *,
        parameter: ParameterDeclLike,
        title: str,
        minimum_value: float,
        maximum_value: float,
        step_size: float,
        control_id: str | AutoResolved = AUTO,
    ) -> ParameterSlider:
        """Construct + register a parameter slider control on this sheet."""
        ctrl = ParameterSlider(
            parameter=parameter, title=title,
            minimum_value=minimum_value, maximum_value=maximum_value,
            step_size=step_size, control_id=control_id,
        )
        self.parameter_controls.append(ctrl)
        return ctrl

    def add_parameter_datetime_picker(
        self,
        *,
        parameter: ParameterDeclLike,
        title: str,
        control_id: str | AutoResolved = AUTO,
    ) -> ParameterDateTimePicker:
        """Construct + register a parameter datetime picker control."""
        ctrl = ParameterDateTimePicker(
            parameter=parameter, title=title, control_id=control_id,
        )
        self.parameter_controls.append(ctrl)
        return ctrl

    def add_parameter_text_field(
        self,
        *,
        parameter: ParameterDeclLike,
        title: str,
        control_id: str | AutoResolved = AUTO,
    ) -> ParameterTextField:
        """Construct + register a free-text parameter input control.

        Use when the parameter's option universe is unbounded / unknown
        at deploy time, or when LinkedValues / StaticValues paths fail
        (X.1.b L2FT cascade Value dropdown hit ``Sample values not
        found`` from QS's lazy sample-values fetch on cold per-CI-run
        dashboards). Text input has no equivalent fetch path.
        """
        ctrl = ParameterTextField(
            parameter=parameter, title=title, control_id=control_id,
        )
        self.parameter_controls.append(ctrl)
        return ctrl

    def add_filter_dropdown(
        self,
        *,
        filter: FilterLike,
        title: str,
        type: Literal["SINGLE_SELECT", "MULTI_SELECT"] = "MULTI_SELECT",
        selectable_values: SelectableValues | None = None,
        control_id: str | AutoResolved = AUTO,
    ) -> FilterDropdown:
        """Construct + register a filter dropdown control on this sheet."""
        ctrl = FilterDropdown(
            filter=filter, title=title, type=type,
            selectable_values=selectable_values, control_id=control_id,
        )
        self.filter_controls.append(ctrl)
        return ctrl

    def add_filter_slider(
        self,
        *,
        filter: FilterLike,
        title: str,
        minimum_value: float,
        maximum_value: float,
        step_size: float,
        type: Literal["SINGLE_POINT", "RANGE"] = "RANGE",
        control_id: str | AutoResolved = AUTO,
    ) -> FilterSlider:
        """Construct + register a filter slider control on this sheet."""
        ctrl = FilterSlider(
            filter=filter, title=title,
            minimum_value=minimum_value, maximum_value=maximum_value,
            step_size=step_size, type=type, control_id=control_id,
        )
        self.filter_controls.append(ctrl)
        return ctrl

    def add_filter_datetime_picker(
        self,
        *,
        filter: FilterLike,
        title: str,
        type: Literal["SINGLE_VALUED", "DATE_RANGE"] = "DATE_RANGE",
        control_id: str | AutoResolved = AUTO,
    ) -> FilterDateTimePicker:
        """Construct + register a filter datetime picker control."""
        ctrl = FilterDateTimePicker(
            filter=filter, title=title, type=type, control_id=control_id,
        )
        self.filter_controls.append(ctrl)
        return ctrl

    def add_filter_cross_sheet(
        self,
        *,
        filter: FilterLike,
        control_id: str | AutoResolved = AUTO,
    ) -> FilterCrossSheet:
        """Construct + register a cross-sheet filter control on this sheet."""
        ctrl = FilterCrossSheet(filter=filter, control_id=control_id)
        self.filter_controls.append(ctrl)
        return ctrl

    def scope(
        self, fg: FilterGroup, visuals: list[VisualLike],
    ) -> FilterGroup:
        """L.1.21 — scope a filter group to specific visuals on this sheet.

        Reads more naturally than ``fg.scope_visuals(sheet, visuals)`` since
        the sheet is the contextual subject. Runtime check stays —
        Python's type system can't track which ``Sheet`` instance a
        visual was registered on without dependent types.
        """
        return fg.scope_visuals(self, visuals)

    def find_visual(
        self,
        *,
        title: str | None = None,
        title_contains: str | None = None,
        visual_id: VisualId | str | None = None,
    ) -> VisualLike:
        """Look up a single visual on this sheet by title / partial title /
        visual id.

        Designed for e2e + introspection: pass any of the three lookup
        keys and get the matching node back. Raises if no match or
        multiple matches — the API forces unambiguity at the call
        site so tests can rely on the result.

        Auto-IDs (L.1.8.5) make this the right way to find a visual
        from outside the tree — IDs are not stable under tree
        restructuring, but titles + structural position are.
        """
        matches: list[VisualLike] = []
        for v in self.visuals:
            if visual_id is not None and v.visual_id == visual_id:
                matches.append(v)
                continue
            v_title = getattr(v, "title", None)
            if title is not None and v_title == title:
                matches.append(v)
                continue
            if title_contains is not None and v_title and title_contains in v_title:
                matches.append(v)
                continue
        if not matches:
            raise ValueError(
                f"No visual on sheet {self.sheet_id!r} matches "
                f"title={title!r} title_contains={title_contains!r} "
                f"visual_id={visual_id!r}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple visuals on sheet {self.sheet_id!r} match "
                f"title={title!r} title_contains={title_contains!r} "
                f"visual_id={visual_id!r} — got {len(matches)}; "
                f"narrow the criteria."
            )
        return matches[0]

    def emit(self) -> SheetDefinition:
        return SheetDefinition(
            SheetId=self.sheet_id,
            Name=self.name,
            Title=self.title,
            Description=self.description,
            ContentType="INTERACTIVE",
            Visuals=[v.emit() for v in self.visuals] if self.visuals else None,
            FilterControls=(
                [fc.emit() for fc in self.filter_controls]
                if self.filter_controls else []
            ),
            ParameterControls=(
                [c.emit() for c in self.parameter_controls]
                if self.parameter_controls else None
            ),
            TextBoxes=(
                [tb.emit() for tb in self.text_boxes]
                if self.text_boxes else None
            ),
            Layouts=[
                Layout(
                    Configuration=LayoutConfiguration(
                        GridLayout=GridLayoutConfiguration(
                            Elements=[s.emit() for s in self.grid_slots],
                            # M.4.4.10ab — QS UI emits this on every
                            # GridLayout; its absence breaks the editor.
                            CanvasSizeOptions={
                                "ScreenCanvasSizeOptions": {
                                    "ResizeOption": "FIXED",
                                    "OptimizedViewPortWidth": "1600px",
                                },
                            },
                        ),
                    ),
                ),
            ],
        )


# ---------------------------------------------------------------------------
# L.1.21 — Layout DSL. Sheet's grid layout factored into a separate
# ``SheetLayout`` namespace owned by Sheet. Two placement modes:
#
#   - Row-based: ``sheet.layout.row(height=H)`` opens a row at the
#     current vertical cursor; subsequent ``row.add_<kind>(width=W,
#     ...)`` calls advance a column cursor and place the visual.
#     Refuses widths that exceed the 36-col grid.
#   - Absolute: ``sheet.layout.absolute(col_index=, row_index=,
#     col_span=, row_span=).add_<kind>(...)`` for one-off explicit
#     positioning (overlapping visuals, asymmetric grids).
#
# Both modes construct + register + place the visual atomically on
# the parent Sheet — the analyst never holds a half-constructed visual
# ref or computes col_index arithmetic by hand.
# ---------------------------------------------------------------------------

# QuickSight grid is 36 columns wide. Hardcoded here because the QS
# model accepts it as a free int but the actual rendering breaks above
# 36 (visuals get clipped).
_GRID_WIDTH_COLS = 36


@dataclass(eq=False)
class Row:
    """One horizontal band in a Sheet's grid layout.

    Tracks the column cursor as visuals + text boxes are added; refuses
    widths that overflow the 36-column grid. ``height`` becomes every
    visual's ``row_span``; the column cursor advances by each visual's
    ``width`` (col_span). A new row from ``sheet.layout.row()`` lands
    below the previous row — the SheetLayout tracks the vertical cursor.
    """
    sheet: Sheet
    height: int
    row_index: int
    _col_cursor: int = field(default=0, init=False, repr=False)

    def _consume(self, width: int) -> int:
        """Allocate ``width`` columns at the current cursor; return
        the col_index for the slot. Raises if the row would overflow
        the 36-col grid."""
        if self._col_cursor + width > _GRID_WIDTH_COLS:
            raise ValueError(
                f"Row width exceeded: {self._col_cursor} + {width} > "
                f"{_GRID_WIDTH_COLS} cols. Open a new row via "
                f"sheet.layout.row(height=...) or reduce the visual width."
            )
        col_index = self._col_cursor
        self._col_cursor += width
        return col_index

    def add_kpi(
        self,
        *,
        width: int,
        title: str,
        values: list[Measure] | None = None,
        subtitle: str,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> KPI:
        """Construct + register + place a KPI in this row."""
        col_index = self._consume(width)
        kpi = KPI(
            title=title, subtitle=subtitle, values=values or [], visual_id=visual_id,
        )
        self.sheet.visuals.append(kpi)
        self.sheet.grid_slots.append(GridSlot(
            element=kpi,
            col_span=width, row_span=self.height,
            col_index=col_index, row_index=self.row_index,
        ))
        return kpi

    def add_table(
        self,
        *,
        width: int,
        title: str,
        group_by: list[Dim] | None = None,
        values: list[Measure] | None = None,
        columns: list[Dim] | None = None,
        subtitle: str,
        sort_by: (
            tuple[FieldRef, Literal["ASC", "DESC"]]
            | list[tuple[FieldRef, Literal["ASC", "DESC"]]]
            | None
        ) = None,
        actions: list[Action] | None = None,
        conditional_formatting: list[CellFormat] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> Table:
        """Construct + register + place a Table in this row.

        Aggregated mode: pass ``group_by`` + ``values``. Unaggregated
        mode (raw column display): pass ``columns``. The two modes are
        mutually exclusive (Table.__post_init__ enforces this)."""
        col_index = self._consume(width)
        table = Table(
            title=title, subtitle=subtitle,
            group_by=group_by or [], values=values or [],
            columns=columns or [],
            sort_by=sort_by, actions=actions or [],
            conditional_formatting=conditional_formatting,
            visual_id=visual_id,
        )
        self.sheet.visuals.append(table)
        self.sheet.grid_slots.append(GridSlot(
            element=table,
            col_span=width, row_span=self.height,
            col_index=col_index, row_index=self.row_index,
        ))
        return table

    def add_bar_chart(
        self,
        *,
        width: int,
        title: str,
        category: list[Dim] | None = None,
        values: list[Measure] | None = None,
        colors: list[Dim] | None = None,
        subtitle: str,
        orientation: Literal["HORIZONTAL", "VERTICAL"] | None = None,
        bars_arrangement: Literal[
            "CLUSTERED", "STACKED", "STACKED_PERCENT",
        ] | None = None,
        category_label: str | None = None,
        value_label: str | None = None,
        color_label: str | None = None,
        sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None,
        actions: list[Action] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> BarChart:
        """Construct + register + place a BarChart in this row."""
        col_index = self._consume(width)
        bar = BarChart(
            title=title, subtitle=subtitle,
            category=category or [], values=values or [],
            colors=colors or [],
            orientation=orientation, bars_arrangement=bars_arrangement,
            category_label=category_label, value_label=value_label,
            color_label=color_label,
            sort_by=sort_by, actions=actions or [], visual_id=visual_id,
        )
        self.sheet.visuals.append(bar)
        self.sheet.grid_slots.append(GridSlot(
            element=bar,
            col_span=width, row_span=self.height,
            col_index=col_index, row_index=self.row_index,
        ))
        return bar

    def add_line_chart(
        self,
        *,
        width: int,
        title: str,
        category: list[Dim] | None = None,
        values: list[Measure] | None = None,
        colors: list[Dim] | None = None,
        subtitle: str,
        chart_type: Literal["LINE", "AREA", "STACKED_AREA"] | None = None,
        category_label: str | None = None,
        value_label: str | None = None,
        sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None,
        actions: list[Action] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> LineChart:
        """Construct + register + place a LineChart in this row."""
        col_index = self._consume(width)
        line = LineChart(
            title=title, subtitle=subtitle,
            category=category or [], values=values or [],
            colors=colors or [],
            chart_type=chart_type,
            category_label=category_label, value_label=value_label,
            sort_by=sort_by, actions=actions or [], visual_id=visual_id,
        )
        self.sheet.visuals.append(line)
        self.sheet.grid_slots.append(GridSlot(
            element=line,
            col_span=width, row_span=self.height,
            col_index=col_index, row_index=self.row_index,
        ))
        return line

    def add_sankey(
        self,
        *,
        width: int,
        title: str,
        source: Dim,
        target: Dim,
        weight: Measure,
        subtitle: str,
        items_limit: int | None = None,
        actions: list[Action] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> Sankey:
        """Construct + register + place a Sankey in this row."""
        col_index = self._consume(width)
        sankey = Sankey(
            title=title, subtitle=subtitle, source=source, target=target,
            weight=weight, items_limit=items_limit, actions=actions or [],
            visual_id=visual_id,
        )
        self.sheet.visuals.append(sankey)
        self.sheet.grid_slots.append(GridSlot(
            element=sankey,
            col_span=width, row_span=self.height,
            col_index=col_index, row_index=self.row_index,
        ))
        return sankey

    def add_text_box(
        self,
        text_box: TextBox,
        *,
        width: int,
    ) -> TextBox:
        """Register + place a pre-constructed TextBox in this row.

        TextBox content is verbose XML (built via ``common/rich_text``),
        so the analyst constructs the TextBox separately and passes it
        here. Uniqueness is by object identity — placing the same
        TextBox in two rows is a programmer error caught by the row-
        cursor advance (you'd be calling _consume twice).
        """
        col_index = self._consume(width)
        if text_box not in self.sheet.text_boxes:
            self.sheet.text_boxes.append(text_box)
        self.sheet.grid_slots.append(GridSlot(
            element=text_box,
            col_span=width, row_span=self.height,
            col_index=col_index, row_index=self.row_index,
        ))
        return text_box


@dataclass(eq=False)
class AbsoluteSlot:
    """One explicit-position slot. Use for layouts that don't fit the
    row pattern — overlapping visuals, asymmetric grids, off-grid
    positioning. One-shot: construct via ``sheet.layout.absolute(
    col_index=, row_index=, col_span=, row_span=)`` then chain a single
    ``add_<kind>(...)`` to fill it. Re-using an AbsoluteSlot for
    multiple visuals would emit duplicate slot positions and isn't
    supported."""
    sheet: Sheet
    col_span: int
    row_span: int
    col_index: int
    row_index: int | None

    def _place(self, element: LayoutNode) -> None:
        self.sheet.grid_slots.append(GridSlot(
            element=element,
            col_span=self.col_span, row_span=self.row_span,
            col_index=self.col_index, row_index=self.row_index,
        ))

    def add_kpi(
        self,
        *,
        title: str,
        values: list[Measure] | None = None,
        subtitle: str,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> KPI:
        kpi = KPI(
            title=title, subtitle=subtitle, values=values or [], visual_id=visual_id,
        )
        self.sheet.visuals.append(kpi)
        self._place(kpi)
        return kpi

    def add_table(
        self,
        *,
        title: str,
        group_by: list[Dim] | None = None,
        values: list[Measure] | None = None,
        columns: list[Dim] | None = None,
        subtitle: str,
        sort_by: (
            tuple[FieldRef, Literal["ASC", "DESC"]]
            | list[tuple[FieldRef, Literal["ASC", "DESC"]]]
            | None
        ) = None,
        actions: list[Action] | None = None,
        conditional_formatting: list[CellFormat] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> Table:
        table = Table(
            title=title, subtitle=subtitle,
            group_by=group_by or [], values=values or [],
            columns=columns or [],
            sort_by=sort_by, actions=actions or [],
            conditional_formatting=conditional_formatting,
            visual_id=visual_id,
        )
        self.sheet.visuals.append(table)
        self._place(table)
        return table

    def add_bar_chart(
        self,
        *,
        title: str,
        category: list[Dim] | None = None,
        values: list[Measure] | None = None,
        subtitle: str,
        orientation: Literal["HORIZONTAL", "VERTICAL"] | None = None,
        bars_arrangement: Literal[
            "CLUSTERED", "STACKED", "STACKED_PERCENT",
        ] | None = None,
        sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None,
        actions: list[Action] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> BarChart:
        bar = BarChart(
            title=title, subtitle=subtitle,
            category=category or [], values=values or [],
            orientation=orientation, bars_arrangement=bars_arrangement,
            sort_by=sort_by, actions=actions or [], visual_id=visual_id,
        )
        self.sheet.visuals.append(bar)
        self._place(bar)
        return bar

    def add_line_chart(
        self,
        *,
        title: str,
        category: list[Dim] | None = None,
        values: list[Measure] | None = None,
        colors: list[Dim] | None = None,
        subtitle: str,
        chart_type: Literal["LINE", "AREA", "STACKED_AREA"] | None = None,
        category_label: str | None = None,
        value_label: str | None = None,
        sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None,
        actions: list[Action] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> LineChart:
        line = LineChart(
            title=title, subtitle=subtitle,
            category=category or [], values=values or [],
            colors=colors or [],
            chart_type=chart_type,
            category_label=category_label, value_label=value_label,
            sort_by=sort_by, actions=actions or [], visual_id=visual_id,
        )
        self.sheet.visuals.append(line)
        self._place(line)
        return line

    def add_sankey(
        self,
        *,
        title: str,
        source: Dim,
        target: Dim,
        weight: Measure,
        subtitle: str,
        items_limit: int | None = None,
        actions: list[Action] | None = None,
        visual_id: VisualId | AutoResolved = AUTO,
    ) -> Sankey:
        sankey = Sankey(
            title=title, subtitle=subtitle, source=source, target=target,
            weight=weight, items_limit=items_limit, actions=actions or [],
            visual_id=visual_id,
        )
        self.sheet.visuals.append(sankey)
        self._place(sankey)
        return sankey

    def add_text_box(self, text_box: TextBox) -> TextBox:
        if text_box not in self.sheet.text_boxes:
            self.sheet.text_boxes.append(text_box)
        self._place(text_box)
        return text_box


@dataclass(eq=False)
class SheetLayout:
    """Layout namespace on a Sheet — manages rows + absolute placements.

    Tracks a vertical row cursor: each ``row(height=H)`` opens a new row
    at the current cursor and advances it by ``H`` for the next row.
    Absolute placements don't advance the cursor (they're independent
    of row flow).
    """
    sheet: Sheet
    _row_cursor: int = field(default=0, init=False, repr=False)

    def row(self, *, height: int) -> Row:
        """Open a new row at the current vertical cursor with the given
        height. The cursor advances by ``height`` so the next ``row()``
        call lands below this one."""
        row = Row(sheet=self.sheet, height=height, row_index=self._row_cursor)
        self._row_cursor += height
        return row

    def absolute(
        self,
        *,
        col_index: int,
        row_index: int | None = None,
        col_span: int,
        row_span: int,
    ) -> AbsoluteSlot:
        """Open an explicit-position slot. Doesn't advance the row
        cursor — absolute placements are independent of row flow."""
        return AbsoluteSlot(
            sheet=self.sheet,
            col_span=col_span, row_span=row_span,
            col_index=col_index, row_index=row_index,
        )


# ---------------------------------------------------------------------------
# Analysis — owns the sheet tree; emits AnalysisDefinition. The wrapping
# (AnalysisId / AwsAccountId / Permissions / ThemeArn) is supplied by
# the App.
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class Analysis:
    """Tree node for the Analysis-level structure.

    ``analysis_id_suffix`` is the part the App's ``cfg.prefixed()``
    will prepend to (e.g. ``"investigation-analysis"`` becomes
    ``"qs-gen-investigation-analysis"``). Keeping the suffix on the
    tree node keeps the per-app naming under the tree's control while
    leaving the global resource-prefix in the Config.

    ``emit_definition()`` returns the ``models.AnalysisDefinition`` —
    the App combines this with metadata (``AwsAccountId``,
    ``ThemeArn``, ``Permissions``, dataset declarations) to produce
    the full ``models.Analysis`` ready for deploy.
    """
    analysis_id_suffix: str
    name: str
    sheets: list[Sheet] = field(default_factory=list["Sheet"])
    parameters: list[ParameterDeclLike] = field(default_factory=list[ParameterDeclLike])
    filter_groups: list[FilterGroup] = field(default_factory=list[FilterGroup])
    calc_fields: list[CalcField] = field(default_factory=list[CalcField])
    # Phase BM — the BL.2 ``default_universal_date_range`` field
    # dissolved when date pushdown moved into the dataset SQL itself
    # (every date-scoped dataset now declares its own DateTime dataset
    # params with `StaticValues` defaults; App2's substitution path
    # already picks those up at render time without any analysis-side
    # bridge).
    # DataSetIdentifierDeclarations come from the App at emit time

    def add_sheet(self, sheet: Sheet) -> Sheet:
        if any(s.sheet_id == sheet.sheet_id for s in self.sheets):
            raise ValueError(
                f"Sheet {sheet.sheet_id!r} is already on this Analysis"
            )
        self.sheets.append(sheet)
        return sheet

    def add_parameter[T: ParameterDeclLike](self, param: T) -> T:
        """Declare a parameter on this analysis.

        Construction-time check: parameter names are unique within
        the analysis. Catches the silent shadow bug where two declarations
        share a Name and only one wins at deploy time. Generic over
        the concrete subtype so the returned ref keeps its type
        (``StringParam`` / ``IntegerParam`` / ``DateTimeParam``) (PEP 695).
        """
        if any(p.name == param.name for p in self.parameters):
            raise ValueError(
                f"Parameter {param.name!r} is already declared on this Analysis"
            )
        self.parameters.append(param)
        return param

    def add_filter_group(self, fg: FilterGroup) -> FilterGroup:
        """Register a filter group on this analysis.

        Construction-time check: explicit filter group IDs are unique.
        (Auto-IDs are unique by construction — assigned from the
        index in the analysis's filter_groups list — so the check
        only applies when callers passed an explicit id.)
        """
        if not isinstance(fg.filter_group_id, _AutoSentinel) and any(
            existing.filter_group_id == fg.filter_group_id
            for existing in self.filter_groups
        ):
            raise ValueError(
                f"FilterGroup {fg.filter_group_id!r} is already on this Analysis"
            )
        self.filter_groups.append(fg)
        return fg

    def find_sheet(
        self,
        *,
        name: str | None = None,
        sheet_id: SheetId | str | None = None,
    ) -> Sheet:
        """Look up a single sheet on this analysis by name or sheet id.

        Raises on no-match or multi-match. Sheet IDs stay explicit
        (URL-facing per the L.1.8.5 mixed scheme) so passing
        ``sheet_id=`` is the most robust lookup; ``name=`` is the
        next-best for tests that don't want to hardcode IDs.
        """
        matches: list[Sheet] = []
        for s in self.sheets:
            if sheet_id is not None and s.sheet_id == sheet_id:
                matches.append(s)
                continue
            if name is not None and s.name == name:
                matches.append(s)
                continue
        if not matches:
            raise ValueError(
                f"No sheet on Analysis {self.name!r} matches "
                f"name={name!r} sheet_id={sheet_id!r}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple sheets on Analysis {self.name!r} match "
                f"name={name!r} sheet_id={sheet_id!r} — got {len(matches)}."
            )
        return matches[0]

    def find_filter_group(
        self,
        *,
        filter_group_id: FilterGroupId | str | None = None,
    ) -> FilterGroup:
        """Look up a single filter group by id (auto or explicit)."""
        matches = [
            fg for fg in self.filter_groups
            if fg.filter_group_id == filter_group_id
        ]
        if not matches:
            raise ValueError(
                f"No filter group on Analysis {self.name!r} with "
                f"filter_group_id={filter_group_id!r}"
            )
        return matches[0]

    def find_calc_field(self, *, name: str) -> CalcField:
        """Look up a single calc field by name."""
        matches = [c for c in self.calc_fields if c.name == name]
        if not matches:
            raise ValueError(
                f"No calc field on Analysis {self.name!r} named {name!r}"
            )
        return matches[0]

    def find_parameter(self, *, name: str) -> ParameterDeclLike:
        """Look up a single parameter declaration by name."""
        matches = [p for p in self.parameters if p.name == name]
        if not matches:
            raise ValueError(
                f"No parameter on Analysis {self.name!r} named {name!r}"
            )
        return matches[0]

    def add_calc_field(self, calc: CalcField) -> CalcField:
        """Register a calculated field on this analysis.

        Construction-time check: calc field names are unique within
        the analysis. Two calc fields sharing a Name silently let one
        win at deploy time — same shadow-bug class as parameters /
        filter groups / datasets.
        """
        if any(c.name == calc.name for c in self.calc_fields):
            raise ValueError(
                f"CalcField {calc.name!r} is already on this Analysis"
            )
        self.calc_fields.append(calc)
        return calc

    def datasets(self) -> set[Dataset]:
        """Walk the analysis tree and return every Dataset referenced
        by any visual, filter group, or registered calc field. Used by
        App.dataset_dependencies to derive the precise refresh set.

        Visuals using the spike-shape ``VisualNode`` factory wrapper
        don't expose their dataset refs (the factory hides them).
        Typed Visual subtypes (``KPI`` / ``Table`` / ``BarChart`` /
        ``Sankey``) all expose ``datasets()`` and contribute. The
        spike-shape gap closes once apps port to typed subtypes
        (L.2/L.3/L.4).

        Registered CalcFields contribute too — their ``Dataset`` ref
        becomes a dep even if no visual directly references the
        underlying columns.
        """
        deps: set[Dataset] = set()
        for sheet in self.sheets:
            for visual in sheet.visuals:
                deps.update(visual.datasets())
            # Parameter / filter controls with LinkedValues populate
            # from a Dataset — that's a dep too.
            for pctrl in sheet.parameter_controls:
                deps.update(pctrl.datasets())
            for fctrl in sheet.filter_controls:
                deps.update(fctrl.datasets())
        for fg in self.filter_groups:
            deps.update(fg.datasets())
        for calc in self.calc_fields:
            deps.add(calc.dataset)
        return deps

    def calc_fields_referenced(self) -> set[CalcField]:
        """Walk the analysis tree and return every CalcField referenced
        by any visual or filter group. Distinct from ``self.calc_fields``
        (the registry): this returns only the calc fields actually used.

        Catches "calc field declared but never used" (registered but
        not in this set) and "calc field used but not declared" (in
        this set but not in the registry — App._validate_calc_field_
        references raises on emit).
        """
        deps: set[CalcField] = set()
        for sheet in self.sheets:
            for visual in sheet.visuals:
                deps.update(visual.calc_fields())
        for fg in self.filter_groups:
            deps.update(fg.calc_fields())
        return deps

    def emit_definition(
        self,
        *,
        datasets: list[Dataset],
    ) -> AnalysisDefinition:
        return AnalysisDefinition(
            DataSetIdentifierDeclarations=[
                d.emit_declaration() for d in datasets
            ],
            Sheets=[s.emit() for s in self.sheets],
            FilterGroups=(
                [fg.emit() for fg in self.filter_groups]
                if self.filter_groups else None
            ),
            CalculatedFields=(
                [c.emit() for c in self.calc_fields]
                if self.calc_fields else None
            ),
            ParameterDeclarations=(
                [p.emit() for p in self.parameters]
                if self.parameters else None
            ),
            # M.4.4.10ab — three top-level fields QS UI populates on
            # every analysis. Their absence loads but breaks the editor
            # when adding visuals/sheets. Shapes mirror the QS-UI
            # control analysis verified against on 2026-04-29.
            Options={
                "WeekStart": "SUNDAY",
                "QBusinessInsightsStatus": "DISABLED",
                "ExcludedDataSetArns": [],
                "CustomActionDefaults": {
                    "highlightOperation": {
                        "Trigger": "DATA_POINT_CLICK",
                    },
                },
            },
            AnalysisDefaults={
                "DefaultNewSheetConfiguration": {
                    "InteractiveLayoutConfiguration": {
                        "Grid": {
                            "CanvasSizeOptions": {
                                "ScreenCanvasSizeOptions": {
                                    "ResizeOption": "FIXED",
                                    "OptimizedViewPortWidth": "1600px",
                                },
                            },
                        },
                    },
                    "SheetContentType": "INTERACTIVE",
                },
            },
            # QueryExecutionOptions: NOT EMITTED. boto3 1.42.97's model
            # claims the field is accepted on CreateAnalysis but the
            # serializer raises KeyError mid-serialize. Both with and
            # without parameter_validation=False. QS auto-fills the
            # field server-side to {QueryExecutionMode: AUTO} anyway —
            # the working hand-built control analysis showed the same
            # value on describe even though it was never sent.
        )


# ---------------------------------------------------------------------------
# Dashboard — references an Analysis (object ref) so they share the
# same definition.
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class Dashboard:
    """Tree node for a Dashboard.

    Carries an object reference to the ``Analysis`` whose definition
    this Dashboard publishes. ``dashboard_id_suffix`` follows the same
    pattern as ``Analysis.analysis_id_suffix`` — App's ``cfg.prefixed()``
    prepends the project resource prefix.

    ``analysis`` is the SAME tree node the App owns; the Dashboard
    re-emits the same definition the Analysis produces, which matches
    the existing ``build_dashboard(cfg)`` pattern in the per-app
    builders.
    """
    dashboard_id_suffix: str
    name: str
    analysis: Analysis


# ---------------------------------------------------------------------------
# App — top-level tree node.
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class App:
    """Top-level tree node — coordinates an Analysis + Dashboard plus
    the deploy-time context (theme, dataset arns, permissions) drawn
    from the Config.

    Authors construct an App, attach the Analysis (which holds the
    sheet tree), optionally attach the Dashboard (most apps do — they
    publish what they author), and call ``emit_analysis()`` /
    ``emit_dashboard()`` to get the ``models.py`` instances ready for
    deploy.

    Datasets are registered on the App via ``add_dataset()`` and
    referenced from visuals / filters by object ref. At emit time
    the App walks the tree's ``dataset_dependencies()`` and includes
    only the datasets actually used in the emitted
    ``DataSetIdentifierDeclarations`` — selective by construction.
    Validation: if a visual or filter references a Dataset that
    isn't registered on the App, ``emit_analysis`` raises with the
    offending identifiers.
    """
    name: str
    cfg: Config
    analysis: Analysis | None = None
    dashboard: Dashboard | None = None
    datasets: list[Dataset] = field(default_factory=list[Dataset])
    # Bare-string column refs (``Dim(ds, "amount")`` instead of
    # ``ds["amount"].dim()``) are typo-prone — they bypass the dataset
    # contract validation. ``emit_analysis`` raises on any bare-string
    # column ref unless this flag is set. Test fixtures + datasets
    # without a registered contract (kitchen-sink) opt in via
    # ``allow_bare_strings=True``.
    allow_bare_strings: bool = False

    def set_analysis(self, analysis: Analysis) -> Analysis:
        self.analysis = analysis
        return analysis

    def create_dashboard(
        self,
        *,
        dashboard_id_suffix: str,
        name: str,
    ) -> Dashboard:
        """Construct + register a Dashboard against the App's already-set
        Analysis.

        The App owns the Analysis already; this shortcut prevents the
        analysis-mismatch bug class by construction — there's no opening
        to pass a different Analysis.
        """
        if self.analysis is None:
            raise ValueError(
                "Cannot create_dashboard before set_analysis — "
                "the dashboard publishes the App's Analysis."
            )
        dashboard = Dashboard(
            dashboard_id_suffix=dashboard_id_suffix,
            name=name,
            analysis=self.analysis,
        )
        self.dashboard = dashboard
        return dashboard

    def add_dataset(self, dataset: Dataset) -> Dataset:
        """Register a Dataset on the App.

        Construction-time check: dataset identifiers are unique within
        the app. Catches the silent shadow bug where two registrations
        share an identifier and only one wins at deploy.
        """
        if any(d.identifier == dataset.identifier for d in self.datasets):
            raise ValueError(
                f"Dataset {dataset.identifier!r} is already registered on this App"
            )
        self.datasets.append(dataset)
        return dataset

    def dataset_dependencies(self) -> set[Dataset]:
        """The set of Datasets referenced anywhere in the App's tree.

        Walks the Analysis (sheets → visuals + filter_groups). Each
        typed Visual subtype + typed Filter wrapper exposes its own
        ``datasets()`` set; the App unions them.

        **Deployment side effect.** This set drives:
        - selective deploy (only re-create / refresh the datasets
          downstream of an actual change),
        - matview REFRESH ordering (REFRESH only the matviews backing
          datasets that the changed deploy surface depends on).

        Returns an empty set when the App has no Analysis.
        """
        if self.analysis is None:
            return set()
        return self.analysis.datasets()

    def find_sheet(
        self,
        *,
        name: str | None = None,
        sheet_id: SheetId | str | None = None,
    ) -> Sheet:
        """Convenience pass-through to ``app.analysis.find_sheet(...)``."""
        if self.analysis is None:
            raise ValueError(
                f"App {self.name!r} has no Analysis — can't find sheets."
            )
        return self.analysis.find_sheet(name=name, sheet_id=sheet_id)

    def resolve_auto_ids(self) -> None:
        """Walk the tree and assign auto-IDs to nodes that left their
        IDs unset. Called from emit_analysis / emit_dashboard before
        any validation or emission, and exposed publicly so non-QS
        renderers (HTML, future X.4 editor) can resolve IDs without
        going through the full QS emit path.

        Idempotent — re-runs are no-ops once IDs are filled in.

        Mixed scheme (L.1.8.5 + L.1.16): URL-facing IDs (``SheetId``,
        ``ParameterName``) and analyst-facing identifiers (``Dataset``
        identifier) stay explicit. Internal IDs the analyst never
        types — visual_id, filter_id, control_id, action_id, field_id,
        calc-field name — get tree-position-derived defaults when
        omitted.

        Auto-ID formats:
        - Visual: ``v-{kind}-s{sheet_idx}-{visual_idx}``
        - FilterGroup: ``fg-{idx}`` — analysis-scoped
        - Filter: ``f-{kind}-fg{fg_idx}-{filt_idx}``
        - ParameterControl: ``pc-{kind}-s{sheet_idx}-{ctrl_idx}``
        - FilterControl: ``fc-{kind}-s{sheet_idx}-{ctrl_idx}``
        - Drill action: ``act-s{sheet_idx}-v{visual_idx}-{action_idx}``
        - Field-well leaf (Dim/Measure): ``f-{visual_kind}-s{sheet_idx}
          -v{visual_idx}-{role}{slot_idx}`` where role tags the field
          well slot (``g`` group_by, ``v`` values, ``c`` category,
          ``s`` source, ``t`` target, ``w`` weight)
        - CalcField: ``calc-{idx}`` — analysis-scoped

        Same-sheet drills also get their target_sheet back-filled here
        (Drill.target_sheet=AUTO means "the sheet that owns me").
        """
        if self.analysis is None:
            return
        for sheet_idx, sheet in enumerate(self.analysis.sheets):
            for visual_idx, visual in enumerate(sheet.visuals):
                kind = getattr(visual, "_AUTO_KIND", None)
                current = getattr(visual, "visual_id", None)
                if kind is not None and isinstance(current, _AutoSentinel):
                    visual.visual_id = VisualId(
                        auto_id(f"v-{kind}-s{sheet_idx}-{visual_idx}"),
                    )
                # Field-well leaves — Dim/Measure get position-indexed
                # field_ids. Walk the slots that exist on this visual
                # type; missing attributes (e.g. KPI has no group_by)
                # are skipped via getattr default.
                _resolve_field_ids(
                    visual=visual,
                    visual_kind=kind or "v",
                    sheet_idx=sheet_idx,
                    visual_idx=visual_idx,
                )
                # Drill action IDs (sheet+visual scoped). Same-sheet
                # drills (target_sheet=AUTO at construction) get the
                # owning sheet back-filled here — the cycle closes the
                # same time IDs resolve.
                actions = getattr(visual, "actions", None)
                if actions:
                    for action_idx, action in enumerate(actions):
                        if isinstance(action.action_id, _AutoSentinel):
                            action.action_id = auto_id(
                                f"act-s{sheet_idx}-v{visual_idx}-{action_idx}"
                            )
                        if hasattr(action, "target_sheet") and isinstance(
                            action.target_sheet, _AutoSentinel,
                        ):
                            action.target_sheet = sheet
            # Parameter controls — auto-IDs scoped to the sheet.
            for ctrl_idx, ctrl in enumerate(sheet.parameter_controls):
                kind = getattr(ctrl, "_AUTO_KIND", None)
                if kind is not None and isinstance(
                    getattr(ctrl, "control_id", None), _AutoSentinel,
                ):
                    ctrl.control_id = auto_id(
                        f"pc-{kind}-s{sheet_idx}-{ctrl_idx}"
                    )
            # Filter controls — auto-IDs scoped to the sheet.
            for ctrl_idx, ctrl in enumerate(sheet.filter_controls):
                kind = getattr(ctrl, "_AUTO_KIND", None)
                if kind is not None and isinstance(
                    getattr(ctrl, "control_id", None), _AutoSentinel,
                ):
                    ctrl.control_id = auto_id(
                        f"fc-{kind}-s{sheet_idx}-{ctrl_idx}"
                    )
        for fg_idx, fg in enumerate(self.analysis.filter_groups):
            if isinstance(fg.filter_group_id, _AutoSentinel):
                fg.filter_group_id = FilterGroupId(auto_id(f"fg-{fg_idx}"))
            for filt_idx, filt in enumerate(fg.filters):
                kind = getattr(filt, "_AUTO_KIND", None)
                if kind is not None and isinstance(
                    getattr(filt, "filter_id", None), _AutoSentinel,
                ):
                    filt.filter_id = auto_id(
                        f"f-{kind}-fg{fg_idx}-{filt_idx}"
                    )
        # BL.1 — auto-register the literal-1 CalcField that backs
        # ``Measure.kind == "count"`` row-count semantics. One CalcField
        # per ``Dataset`` referenced by a count Measure. Runs BEFORE
        # the calc-field name resolver below so the auto-registered
        # CalcFields' explicit names survive (they don't pass through
        # the auto-name sentinel path). See
        # ``recon_gen.common.tree.fields.row_one_calc_name``.
        count_datasets: set[Dataset] = set()
        for sheet in self.analysis.sheets:
            for visual in sheet.visuals:
                for attr, _role in _FIELD_SLOTS:
                    slot: object = getattr(visual, attr, None)
                    if slot is None:
                        continue
                    leaves: list[object] = (
                        list(slot) if isinstance(slot, list) else [slot]  # type: ignore[arg-type]: list(object) is list of leaves; slot narrowed by isinstance
                    )
                    for leaf in leaves:
                        if isinstance(leaf, Measure) and leaf.kind == "count":
                            count_datasets.add(leaf.dataset)
        existing_calc_names = {
            c.name for c in self.analysis.calc_fields
            if not isinstance(c.name, _AutoSentinel)
        }
        for dataset in sorted(count_datasets, key=lambda d: d.identifier):
            name = row_one_calc_name(dataset)
            if name not in existing_calc_names:
                self.analysis.calc_fields.append(CalcField(
                    dataset=dataset, expression="1", name=name,
                ))
        # CalcField names — analysis-scoped position index. KEPT AS
        # SLUG: calc field names are analyst-facing (they show in the
        # field-well dropdowns and visual subtitles); UUIDs would be
        # unreadable. QS doesn't seem to require UUID-shape for these.
        for calc_idx, calc in enumerate(self.analysis.calc_fields):
            if isinstance(calc.name, _AutoSentinel):
                calc.name = f"calc-{calc_idx}"

    def _validate_dataset_references(self) -> None:
        """Raise if the tree references any Dataset not registered on
        this App. Catches "visual references undeclared dataset" at
        emit time, where the existing string-keyed pattern would let
        the mismatch flow through to deploy."""
        referenced = self.dataset_dependencies()
        registered = set(self.datasets)
        unregistered = referenced - registered
        if unregistered:
            ids = sorted(d.identifier for d in unregistered)
            raise ValueError(
                f"App {self.name!r} references unregistered datasets: "
                f"{ids} — register each via app.add_dataset() first."
            )

    def _validate_parameter_references(self) -> None:
        """Raise if any ParameterDeclLike reference in the tree
        (control bindings, NumericRangeFilter parameter bounds) points
        at a parameter that isn't registered on the analysis.

        Same shadow-bug class as datasets and calc fields: a typed
        parameter ref with .name set but never registered on the
        analysis would emit a SourceParameterName / Parameter binding
        that QuickSight resolves to "no such parameter" silently —
        controls don't drive their bound parameter, filters don't
        narrow.

        DrillParam (in K.2 ``common/drill.py``) takes a string
        ParameterName — those aren't validated here. Closing that
        gap requires a typed-parameter-ref refactor of DrillParam,
        queued as L.1.x follow-up.
        """
        if self.analysis is None:
            return
        registered_params = self.analysis.parameters
        bad: list[str] = []

        def _check(param: ParameterDeclLike | None, where: str) -> None:
            if param is None:
                return
            if not any(p is param for p in registered_params):
                bad.append(
                    f"{where} → parameter {param.name!r} not registered"
                )

        for sheet in self.analysis.sheets:
            for ctrl in sheet.parameter_controls:
                p = getattr(ctrl, "parameter", None)
                _check(p, f"sheet {sheet.sheet_id!r} parameter control")
        for fg in self.analysis.filter_groups:
            for f in fg.filters:
                _check(
                    getattr(f, "minimum_parameter", None),
                    f"filter {f.filter_id!r} minimum_parameter",
                )
                _check(
                    getattr(f, "maximum_parameter", None),
                    f"filter {f.filter_id!r} maximum_parameter",
                )
        if bad:
            raise ValueError(
                f"App {self.name!r} has parameter references that aren't "
                f"registered on the analysis: {bad} — call "
                f"analysis.add_parameter() first."
            )

    def _validate_filter_param_settability(self) -> None:
        """Raise if any filter binds a parameter the analyst can't set.

        A parameter-bound filter (CategoryFilter.with_parameter,
        TimeEqualityFilter, NumericRangeFilter with
        minimum_parameter/maximum_parameter) where the bound parameter
        has NO settable control AND NO non-empty default produces a
        WHERE clause that matches nothing at runtime. The visual
        renders with the dataset narrowed to zero rows — every KPI
        blank, every table empty, no error message anywhere.

        Settable means EITHER:

        - At least one ParameterControl on the analysis targets the
          parameter (any kind: Dropdown, Slider, DateTimePicker, etc.)
        - The parameter declaration carries a non-empty default
          (StringParam.default non-empty list, IntegerParam.default
          non-empty list, DateTimeParam.default always present)

        Caught the v8.3.3 Daily Statement bug class at the App.emit
        level (the ParameterDropdown type tightening catches the
        construction-time slice; this validator catches the
        structural slice — "no dropdown wired at all"). Pairs with
        the v8.3.3 type-system change as belt + suspenders.

        TimeRangeFilter's ``minimum``/``maximum`` dict-form
        ``{"Parameter": name}`` bindings are not walked here — those
        carry a string ParameterName, not a typed ParameterDeclLike,
        so the cross-reference would have to lookup-by-name. The
        existing shipped uses all bind to DateTimeParams with
        RollingDate defaults, so the gap is theoretical for now.
        """
        if self.analysis is None:
            return

        # Set of parameter NAMES that have at least one control on the analysis.
        controlled_param_names: set[str] = set()
        for sheet in self.analysis.sheets:
            for ctrl in sheet.parameter_controls:
                p = getattr(ctrl, "parameter", None)
                if p is not None:
                    controlled_param_names.add(str(p.name))

        # Set of parameter NAMES that carry a non-empty default.
        # StringParam / IntegerParam: default is a list — non-empty means
        # the user picked at least one value at parameter declaration.
        # DateTimeParam: default is always a non-None DateTimeDefaultValues
        # (M.4.4.10d type-required), so always counts as defaulted.
        defaulted_param_names: set[str] = set()
        for p in self.analysis.parameters:
            d: object = getattr(p, "default", None)
            if d is None:
                continue
            if isinstance(d, list) and not d:
                continue
            defaulted_param_names.add(str(p.name))

        # Walk every parameter-bound filter; collect any whose
        # parameter is neither controlled nor defaulted.
        bad: list[str] = []

        def _check(
            param: ParameterDeclLike | None, where: str,
        ) -> None:
            if param is None:
                return
            name = str(param.name)
            if name in controlled_param_names or name in defaulted_param_names:
                return
            bad.append(
                f"{where} → parameter {name!r} has no control on the "
                f"analysis AND no non-empty default — analyst can't set "
                f"it, so the filter matches nothing at runtime"
            )

        from recon_gen.common.tree.filters import _ParameterBinding

        for fg in self.analysis.filter_groups:
            for f in fg.filters:
                # CategoryFilter discriminates via .binding; only the
                # parameter-bound variant carries a ParameterDeclLike.
                binding = getattr(f, "binding", None)
                if isinstance(binding, _ParameterBinding):
                    _check(binding.parameter, f"filter {f.filter_id!r}")
                # TimeEqualityFilter + NumericRangeFilter expose
                # parameter / minimum_parameter / maximum_parameter directly.
                _check(
                    getattr(f, "parameter", None),
                    f"filter {f.filter_id!r} parameter",
                )
                _check(
                    getattr(f, "minimum_parameter", None),
                    f"filter {f.filter_id!r} minimum_parameter",
                )
                _check(
                    getattr(f, "maximum_parameter", None),
                    f"filter {f.filter_id!r} maximum_parameter",
                )

        if bad:
            raise ValueError(
                f"App {self.name!r} has parameter-bound filters whose "
                f"parameter is unsettable (no control + no default). "
                f"Either add a parameter control on a sheet, give the "
                f"parameter a non-empty default, or use a non-parameter "
                f"filter form (with_values / with_literal): {bad}"
            )

    def _validate_drill_destinations(self) -> None:
        """Raise if any Drill action targets a Sheet that isn't on
        this App's Analysis. Catches "drill into a sheet that doesn't
        exist" at emit time. The string-only ``target_sheet=SheetId(...)``
        pattern lets typos through to deploy where the click silently
        does nothing.

        Sheet identity check uses ``is`` rather than ``in``/``set``
        because Sheet's dataclass-generated ``__eq__`` compares fields
        and Sheet isn't hashable — but we want OBJECT identity here,
        not field equality.
        """
        if self.analysis is None:
            return
        registered_sheets = self.analysis.sheets
        bad: list[str] = []
        for sheet in registered_sheets:
            for visual in sheet.visuals:
                actions: list[Action] = getattr(visual, "actions", None) or []
                # Only Drill actions navigate to a sheet; SameSheetFilter
                # actions target visuals on the current sheet and don't
                # carry a target_sheet field.
                for action in actions:
                    if not isinstance(action, Drill):
                        continue
                    if not any(
                        action.target_sheet is s for s in registered_sheets
                    ):
                        target_sheet_id = (
                            action.target_sheet.sheet_id
                            if not isinstance(action.target_sheet, _AutoSentinel)
                            else "<unset>"
                        )
                        bad.append(
                            f"action {action.name!r} on visual "
                            f"{getattr(visual, 'visual_id', '?')!r} → sheet "
                            f"{target_sheet_id!r}"
                        )
        if bad:
            raise ValueError(
                f"App {self.name!r} has drill actions targeting sheets that "
                f"aren't registered on the analysis: {bad}"
            )

    def _validate_no_bare_string_columns(self) -> None:
        """Raise if any tree node uses an unvalidated column ref.

        Two unvalidated forms exist, both gated by ``allow_bare_strings``:

        - **Bare string**: ``Dim(ds, "amount")`` — a literal string
          that bypasses any contract check. Typo-prone.
        - **Unvalidated Column**: ``ds["amount"]`` against a dataset
          with no registered ``DatasetContract``. ``Dataset.__getitem__``
          can't validate when no contract exists, so it returns a
          Column without checking. The walker catches this here so
          the silent-pass path turns into a loud raise.

        The validated path: ``ds["amount"]`` against a dataset whose
        contract IS registered. ``Dataset.__getitem__`` raises
        ``KeyError`` at the wiring site on typo, so by the time the
        walker sees the Column, the column name is already known good.

        ``allow_bare_strings=True`` on the App opts out for test
        fixtures and datasets without a registered contract (the
        kitchen sink, which has no DatasetContract).
        """
        if self.allow_bare_strings or self.analysis is None:
            return
        bad: list[str] = []

        def _check(column: object, where: str) -> None:
            if isinstance(column, str):
                bad.append(f"{where} → bare string {column!r}")
                return
            if isinstance(column, Column):
                try:
                    get_contract(column.dataset.identifier)
                except KeyError:
                    bad.append(
                        f"{where} → ds[{column.name!r}] but dataset "
                        f"{column.dataset.identifier!r} has no registered "
                        f"DatasetContract — column couldn't be validated"
                    )

        for sheet in self.analysis.sheets:
            for visual in sheet.visuals:
                for attr, _role in _FIELD_SLOTS:
                    slot: object = getattr(visual, attr, None)
                    if slot is None:
                        continue
                    leaves: list[object] = (
                        list(slot) if isinstance(slot, list)  # type: ignore[arg-type]: list(object) is list of leaves; slot narrowed by isinstance
                        else [slot]
                    )
                    for leaf in leaves:
                        if leaf is None:
                            continue
                        _check(
                            getattr(leaf, "column", None),
                            f"sheet {sheet.sheet_id!r} visual "
                            f"{getattr(visual, 'visual_id', '?')!r} "
                            f"{attr}",
                        )
                # LinkedValues on parameter / filter controls hits the
                # same column-ref slot.
                for ctrl in (
                    *sheet.parameter_controls, *sheet.filter_controls,
                ):
                    sv = getattr(ctrl, "selectable_values", None)
                    if sv is not None:
                        _check(
                            getattr(sv, "column", None),
                            f"sheet {sheet.sheet_id!r} control "
                            f"{getattr(ctrl, 'control_id', '?')!r} "
                            f"selectable_values",
                        )
        for fg in self.analysis.filter_groups:
            for filt in fg.filters:
                _check(
                    getattr(filt, "column", None),
                    f"filter {getattr(filt, 'filter_id', '?')!r}",
                )
        if bad:
            raise ValueError(
                f"App {self.name!r} has unvalidated column refs "
                f"(typo-prone — they bypass the dataset contract):\n  "
                + "\n  ".join(bad)
                + "\n\nUse the typed form ds[\"column_name\"].dim() / "
                ".sum() / .date() / etc. against a dataset whose "
                "DatasetContract is registered — or pass "
                "``allow_bare_strings=True`` on the App when no "
                "dataset contract is registered (test fixtures)."
            )

    def _validate_calc_field_references(self) -> None:
        """Raise if the tree references any CalcField not registered on
        this App's Analysis. Catches "filter / visual references calc
        field that doesn't exist" at emit time. The string-only
        column pattern lets that bug flow through to deploy where it
        renders silently as an empty column."""
        if self.analysis is None:
            return
        referenced = self.analysis.calc_fields_referenced()
        registered = set(self.analysis.calc_fields)
        unregistered = referenced - registered
        if unregistered:
            # Names are populated by resolve_auto_ids before validation
            # runs (see emit_analysis); fall back to "<unnamed>" for
            # safety.
            names = sorted(
                "<unnamed>" if isinstance(c.name, _AutoSentinel) else c.name
                for c in unregistered
            )
            raise ValueError(
                f"App {self.name!r} references unregistered calc fields: "
                f"{names} — register each via "
                f"app.analysis.add_calc_field() first."
            )

    def _permissions(self, actions: list[str]) -> list[ResourcePermission] | None:
        if not self.cfg.principal_arns:
            return None
        return [
            ResourcePermission(Principal=arn, Actions=actions)
            for arn in self.cfg.principal_arns
        ]

    def _theme_arn(self) -> str:
        return self.cfg.theme_arn(self.cfg.prefixed("theme"))

    def _used_datasets(self) -> list[Dataset]:
        """Datasets the analysis emits declarations for — only those
        actually referenced by the tree, in registration order."""
        referenced = self.dataset_dependencies()
        return [d for d in self.datasets if d in referenced]

    def emit_analysis(self) -> ModelAnalysis:
        if self.analysis is None:
            raise ValueError(
                "App has no Analysis — call set_analysis() first."
            )
        self.resolve_auto_ids()
        self._validate_dataset_references()
        self._validate_calc_field_references()
        self._validate_parameter_references()
        self._validate_filter_param_settability()
        self._validate_drill_destinations()
        self._validate_no_bare_string_columns()
        return ModelAnalysis(
            AwsAccountId=self.cfg.aws_account_id,
            AnalysisId=self.cfg.prefixed(self.analysis.analysis_id_suffix),
            Name=self.analysis.name,
            ThemeArn=self._theme_arn(),
            Definition=self.analysis.emit_definition(
                datasets=self._used_datasets(),
            ),
            Permissions=self._permissions(ANALYSIS_ACTIONS),
            Tags=self.cfg.tags(),
        )

    def emit_dashboard(self) -> ModelDashboard:
        if self.dashboard is None:
            raise ValueError(
                "App has no Dashboard — call create_dashboard() first."
            )
        self.resolve_auto_ids()
        self._validate_dataset_references()
        self._validate_calc_field_references()
        self._validate_parameter_references()
        self._validate_filter_param_settability()
        self._validate_drill_destinations()
        self._validate_no_bare_string_columns()
        return ModelDashboard(
            AwsAccountId=self.cfg.aws_account_id,
            DashboardId=self.cfg.prefixed(self.dashboard.dashboard_id_suffix),
            Name=self.dashboard.name,
            ThemeArn=self._theme_arn(),
            Definition=self.dashboard.analysis.emit_definition(
                datasets=self._used_datasets(),
            ),
            Permissions=self._permissions(DASHBOARD_ACTIONS),
            Tags=self.cfg.tags(),
            VersionDescription="Generated by recon-gen",
            DashboardPublishOptions=DashboardPublishOptions(
                AdHocFilteringOption={"AvailabilityStatus": "ENABLED"},
                ExportToCSVOption={"AvailabilityStatus": "ENABLED"},
                SheetControlsOption={"VisibilityState": "EXPANDED"},
            ),
        )
