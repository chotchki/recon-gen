"""Typed ``Visual`` subtypes — one per visual kind in active use.

L.1.1 catalog: KPI ×29, Table ×22, BarChart ×13, Sankey ×2 across
the three apps. Each subtype owns its field-well shape and emits the
corresponding ``models.py`` ``Visual`` instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from quicksight_gen.common.ids import VisualId
from quicksight_gen.common.models import (
    AxisLabelOptions,
    AxisLabelReferenceOptions,
    BarChartAggregatedFieldWells,
    BarChartConfiguration,
    BarChartFieldWells,
    BarChartSortConfiguration,
    BarChartVisual,
    ChartAxisLabelOptions,
    ColumnIdentifier,
    KPIConfiguration,
    KPIFieldWells,
    KPIOptions,
    KPIVisual,
    LineChartAggregatedFieldWells,
    LineChartConfiguration,
    LineChartFieldWells,
    LineChartSortConfiguration,
    LineChartVisual,
    SankeyDiagramAggregatedFieldWells,
    SankeyDiagramChartConfiguration,
    SankeyDiagramFieldWells,
    SankeyDiagramSortConfiguration,
    SankeyDiagramVisual,
    TableAggregatedFieldWells,
    TableUnaggregatedFieldWells,
    TableConfiguration,
    TableFieldOption,
    TableFieldOptions,
    TableFieldWells,
    TableVisual,
    Visual,
)

from quicksight_gen.common.tree._helpers import (
    AUTO,
    AutoResolved,
    GridLayoutElementType,
    _AutoSentinel,
    subtitle_label,
    title_label,
)
from quicksight_gen.common.tree.actions import Action
from quicksight_gen.common.tree.formatting import CellFormat
from quicksight_gen.common.tree.calc_fields import CalcField
from quicksight_gen.common.tree.datasets import Dataset
from quicksight_gen.common.tree.fields import Dim, FieldRef, Measure, resolve_field_id


def _field_label(leaf: Dim | Measure) -> str:
    """Plain-English header label for a Dim / Measure leaf (v8.5.0).

    Looks up the underlying ``Column``'s human_name from the
    contract registry. Falls back to a title-cased ``CalcField`` name
    when the leaf references a calc field instead of a real column.
    """
    from quicksight_gen.common.dataset_contract import _smart_title
    from quicksight_gen.common.tree.calc_fields import CalcField as _CF
    from quicksight_gen.common.tree.datasets import Column

    col = leaf.column
    if isinstance(col, Column):
        return col.human_name
    if isinstance(col, _CF):
        # CalcField.name is auto-resolved at emit time, so by the
        # time _field_label runs it's a real string. Belt-check via
        # ``str()`` so pyright doesn't complain about the
        # auto-sentinel union.
        return _smart_title(str(col.name))
    # Bare-string fallback (allow_bare_strings escape hatch).
    return _smart_title(str(col))


def _axis_label_apply_to(leaf: Dim | Measure) -> AxisLabelReferenceOptions:
    """Build the ``ApplyTo`` ref binding a chart axis label to a leaf.

    AWS QuickSight requires this binding (FieldId + dataset
    column) for ``AxisLabelOptions.CustomLabel`` to render on the
    axis. Without it, the override is silently ignored — the chart
    parses cleanly but the axis still shows the raw column name. The
    pre-v8.6.1 emit only set ``CustomLabel``, which was the v8.5.5
    "axis labels keep not landing" symptom.

    See `quicksight-quirks.md` 4.5 (axis label needs ApplyTo).
    """
    from quicksight_gen.common.tree.calc_fields import CalcField as _CF
    from quicksight_gen.common.tree.datasets import Column

    col = leaf.column
    if isinstance(col, Column):
        column_name = col.name
    elif isinstance(col, _CF):
        column_name = str(col.name)
    else:
        column_name = str(col)
    return AxisLabelReferenceOptions(
        FieldId=resolve_field_id(leaf),
        Column=ColumnIdentifier(
            DataSetIdentifier=leaf.dataset.identifier,
            ColumnName=column_name,
        ),
    )


@runtime_checkable
class VisualLike(Protocol):
    """Structural type for tree-level visual nodes.

    Typed subtypes (``KPI`` / ``Table`` / ``BarChart`` / ``Sankey``)
    satisfy this Protocol — duck-typed so subtypes don't have to
    inherit from a base class. Subtypes contribute to the L.1.7
    dependency-graph walk via ``datasets()`` / ``calc_fields()``.

    All visual nodes also satisfy ``LayoutNode`` (in ``structure.py``)
    via ``element_id`` + ``element_type`` so they can be placed in a
    sheet's grid layout (``sheet.layout.row(...).add_<kind>(...)``).

    ``visual_id`` is ``VisualId | AutoResolved`` — typed subtypes default
    to ``AUTO`` and ``App.resolve_auto_ids`` replaces it with the
    derived id before emit. The walker / emit assert via ``isinstance``
    narrowing.
    """
    visual_id: VisualId | AutoResolved

    def emit(self) -> Visual: ...

    def datasets(self) -> set[Dataset]: ...

    def calc_fields(self) -> set[CalcField]: ...


def _visual_element_id(node: VisualLike) -> str:
    """LayoutNode.element_id implementation shared by every visual subtype.
    Resolves to ``visual_id`` (the visual's element id is the same id
    QuickSight uses for the visual itself); asserts auto-IDs are
    resolved before access."""
    assert not isinstance(node.visual_id, _AutoSentinel), (
        "visual_id wasn't resolved — App.resolve_auto_ids() must run "
        "before LayoutNode.element_id access."
    )
    return node.visual_id


@dataclass(eq=False)
class KPI:
    """KPI visual — single number per ``values`` entry, no grouping.

    Field-well shape: ``Values=[Measure, ...]``. Most KPIs use one
    measure; multiple are allowed and render as side-by-side numbers.

    ``visual_id`` is optional (L.1.8.5 auto-ID). When omitted, the
    App's tree walker assigns ``v-kpi-s{sheet_idx}-{visual_idx}`` at
    emit time. Pass an explicit ``VisualId(...)`` to override.
    """
    title: str
    subtitle: str | None = None
    values: list[Measure] = field(default_factory=list[Measure])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "kpi"

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return {m.dataset for m in self.values}

    def calc_fields(self) -> set[CalcField]:
        """CalcFields this visual references via its field-well leaves."""
        return {cf for m in self.values if (cf := m.calc_field()) is not None}

    def emit(self) -> Visual:
        assert not isinstance(self.visual_id, _AutoSentinel), (
            "visual_id wasn't resolved — App.resolve_auto_ids() must run "
            "before Visual.emit(). This shouldn't happen via App.emit_*()."
        )
        # KPI doesn't carry Actions per the QuickSight model — KPIs aren't
        # data-point-clickable. If we ever need drill on a KPI, switch to
        # a different visual type.
        return Visual(
            KPIVisual=KPIVisual(
                VisualId=self.visual_id,
                Title=title_label(self.title),
                Subtitle=subtitle_label(self.subtitle) if self.subtitle else None,
                ChartConfiguration=KPIConfiguration(
                    FieldWells=KPIFieldWells(
                        Values=[m.emit() for m in self.values] if self.values else None,
                        # Hand-built KPIs emit explicit empty lists for
                        # TargetValues + TrendGroups; without them, QS
                        # rejects KPIOptions with the "Only
                        # PrimaryValueFontSize display property..."
                        # error. Apparently QS treats missing different
                        # from empty even though docs imply both are
                        # "empty". (M.4.4.8)
                        TargetValues=[],
                        TrendGroups=[],
                    ),
                    # M.4.4.8 — Without a fully-populated KPIOptions
                    # block QS silently renders the visual blank
                    # (verified against a hand-built control KPI on
                    # 2026-04-29; QS-docs-claim-optional but in
                    # practice the UI always produces this shape and
                    # rejects partial shapes at CreateAnalysis time).
                    # Mirror exactly what QS UI defaults to.
                    KPIOptions=KPIOptions(
                        Comparison={"ComparisonMethod": "PERCENT_DIFFERENCE"},
                        PrimaryValueDisplayType="ACTUAL",
                        SecondaryValueFontConfiguration={
                            "FontSize": {"Relative": "EXTRA_LARGE"},
                        },
                        Sparkline={"Visibility": "VISIBLE", "Type": "AREA"},
                        VisualLayoutOptions={
                            "StandardLayout": {"Type": "VERTICAL"},
                        },
                    ),
                ),
            ),
        )


@dataclass(eq=False)
class Table:
    """Table visual — two field-well shapes:

    - **Aggregated** (default): ``group_by=[Dim, ...]`` +
      ``values=[Measure, ...]``. One row per distinct ``group_by``
      combination, aggregated by ``values``. Emits
      ``TableAggregatedFieldWells``.
    - **Unaggregated**: pass ``columns=[Dim, ...]`` (and leave
      ``group_by`` / ``values`` empty). Each cell shows the raw column
      value — no aggregation, one row per source row. Emits
      ``TableUnaggregatedFieldWells``. Use this for detail/drill-source
      tables (AR Balances, AR Daily Statement transaction list).

    Optional ``sort_by`` is a ``(field_ref, direction)`` tuple —
    direction is ``"ASC"`` or ``"DESC"``.

    Optional ``conditional_formatting`` passes through to the model's
    raw dict (see ``common/clickability.py`` for the standard
    accent-text and tint-background helpers).

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str | None = None
    group_by: list[Dim] = field(default_factory=list[Dim])
    values: list[Measure] = field(default_factory=list[Measure])
    columns: list[Dim] = field(default_factory=list[Dim])
    sort_by: (
        tuple[FieldRef, Literal["ASC", "DESC"]]
        | list[tuple[FieldRef, Literal["ASC", "DESC"]]]
        | None
    ) = None
    actions: list[Action] = field(default_factory=list[Action])
    conditional_formatting: list[CellFormat] | None = None
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "table"

    def __post_init__(self) -> None:
        # Unaggregated and aggregated modes are mutually exclusive: if
        # `columns` is set, `group_by` and `values` must be empty (and
        # vice versa). This is the same pattern as the model's
        # `TableFieldWells` — exactly one of `TableAggregatedFieldWells`
        # / `TableUnaggregatedFieldWells` is set.
        if self.columns and (self.group_by or self.values):
            raise ValueError(
                "Table: `columns` (unaggregated mode) cannot be combined "
                "with `group_by` / `values` (aggregated mode). Pick one."
            )

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return (
            {d.dataset for d in self.group_by}
            | {m.dataset for m in self.values}
            | {d.dataset for d in self.columns}
        )

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for d in self.group_by:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        for m in self.values:
            if (cf := m.calc_field()) is not None:
                deps.add(cf)
        for d in self.columns:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        return deps

    def emit(self) -> Visual:
        assert not isinstance(self.visual_id, _AutoSentinel), (
            "visual_id wasn't resolved — see KPI.emit assertion."
        )
        sort_config: Any = None
        if self.sort_by is not None:
            sort_specs = (
                self.sort_by if isinstance(self.sort_by, list)
                else [self.sort_by]
            )
            sort_config = {
                "RowSort": [
                    {"FieldSort": {
                        "FieldId": resolve_field_id(ref),
                        "Direction": direction,
                    }}
                    for ref, direction in sort_specs
                ],
            }
        if self.columns:
            field_wells = TableFieldWells(
                TableUnaggregatedFieldWells=TableUnaggregatedFieldWells(
                    Values=[d.emit_unaggregated_field() for d in self.columns],
                ),
            )
        else:
            field_wells = TableFieldWells(
                TableAggregatedFieldWells=TableAggregatedFieldWells(
                    GroupBy=[d.emit() for d in self.group_by] if self.group_by else None,
                    Values=[m.emit() for m in self.values] if self.values else None,
                ),
            )
        # v8.5.0 — every column gets a CustomLabel header derived from
        # the column's contract spec (display_name override or
        # title-cased snake_case fallback). Without this QuickSight
        # renders the raw snake_case column name as the table header,
        # which reads poorly to non-technical analysts. ``_field_label``
        # handles both Column refs (look up contract) and CalcField
        # refs (use the calc-field name as-is).
        field_options = TableFieldOptions(
            SelectedFieldOptions=[
                TableFieldOption(
                    FieldId=resolve_field_id(leaf),
                    CustomLabel=_field_label(leaf),
                )
                for leaf in self._all_leaves()
            ],
        )
        return Visual(
            TableVisual=TableVisual(
                VisualId=self.visual_id,
                Title=title_label(self.title),
                Subtitle=subtitle_label(self.subtitle) if self.subtitle else None,
                ChartConfiguration=TableConfiguration(
                    FieldWells=field_wells,
                    SortConfiguration=sort_config,
                    FieldOptions=field_options,
                ),
                Actions=[a.emit() for a in self.actions] if self.actions else None,
                ConditionalFormatting=(
                    {"ConditionalFormattingOptions": [
                        cf.emit() for cf in self.conditional_formatting
                    ]}
                    if self.conditional_formatting else None
                ),
            ),
        )

    def _all_leaves(self) -> list[Dim | Measure]:
        """All Dim/Measure leaves on this Table in field-well order.

        Order matters for QuickSight: the SelectedFieldOptions list
        determines the column order in the rendered table when the
        underlying field-well order is the default. Match the same
        order we emit field wells in (``columns`` for unaggregated,
        ``group_by`` then ``values`` for aggregated)."""
        if self.columns:
            return list(self.columns)
        leaves: list[Dim | Measure] = list(self.group_by)
        leaves.extend(self.values)
        return leaves


@dataclass(eq=False)
class BarChart:
    """Bar chart visual — one bar per distinct ``category``, height by
    ``values``.

    Field-well shape: ``Category=[Dim, ...]`` + ``Values=[Measure, ...]``.

    ``orientation`` (``"VERTICAL"`` or ``"HORIZONTAL"``) and
    ``bars_arrangement`` (``"CLUSTERED"`` / ``"STACKED"`` /
    ``"STACKED_PERCENT"``) pass through to the underlying
    ``BarChartConfiguration``. ``sort_by`` is a ``(field_id, direction)``
    tuple — direction ``"ASC"`` or ``"DESC"`` — and emits a
    ``CategorySort`` entry. All three default to ``None`` so the
    QuickSight defaults apply when not specified.

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str | None = None
    category: list[Dim] = field(default_factory=list[Dim])
    values: list[Measure] = field(default_factory=list[Measure])
    colors: list[Dim] = field(default_factory=list[Dim])
    orientation: Literal["HORIZONTAL", "VERTICAL"] | None = None
    bars_arrangement: Literal[
        "CLUSTERED", "STACKED", "STACKED_PERCENT",
    ] | None = None
    category_label: str | None = None
    value_label: str | None = None
    color_label: str | None = None
    sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None
    actions: list[Action] = field(default_factory=list[Action])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "bar"

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return ({d.dataset for d in self.category}
                | {m.dataset for m in self.values}
                | {d.dataset for d in self.colors})

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for d in self.category:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        for m in self.values:
            if (cf := m.calc_field()) is not None:
                deps.add(cf)
        for d in self.colors:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        return deps

    def emit(self) -> Visual:
        assert not isinstance(self.visual_id, _AutoSentinel), (
            "visual_id wasn't resolved — see KPI.emit assertion."
        )
        sort_config: BarChartSortConfiguration | None = None
        if self.sort_by is not None:
            ref, direction = self.sort_by
            sort_config = BarChartSortConfiguration(
                CategorySort=[
                    {"FieldSort": {
                        "FieldId": resolve_field_id(ref),
                        "Direction": direction,
                    }},
                ],
            )
        # v8.5.5 — auto-derive plain-English axis labels from the
        # first leaf of each well when the author didn't pass an
        # explicit override. ``_field_label`` runs the same
        # human_name / smart_title cascade Table headers use
        # (v8.5.0). Author-supplied labels still win — e.g., a chart
        # that needs "$ Limit Cap (per day)" instead of the
        # auto-derived "Cap" overrides via ``value_label="..."``.
        category_label = self.category_label
        if category_label is None and self.category:
            category_label = _field_label(self.category[0])
        value_label = self.value_label
        if value_label is None and self.values:
            value_label = _field_label(self.values[0])
        color_label = self.color_label
        if color_label is None and self.colors:
            color_label = _field_label(self.colors[0])
        return Visual(
            BarChartVisual=BarChartVisual(
                VisualId=self.visual_id,
                Title=title_label(self.title),
                Subtitle=subtitle_label(self.subtitle) if self.subtitle else None,
                ChartConfiguration=BarChartConfiguration(
                    FieldWells=BarChartFieldWells(
                        BarChartAggregatedFieldWells=BarChartAggregatedFieldWells(
                            Category=[d.emit() for d in self.category] if self.category else None,
                            Values=[m.emit() for m in self.values] if self.values else None,
                            Colors=[d.emit() for d in self.colors] if self.colors else None,
                        ),
                    ),
                    Orientation=self.orientation,
                    BarsArrangement=self.bars_arrangement,
                    CategoryLabelOptions=(
                        ChartAxisLabelOptions(AxisLabelOptions=[
                            AxisLabelOptions(
                                CustomLabel=category_label,
                                ApplyTo=_axis_label_apply_to(self.category[0]),
                            ),
                        ])
                        if category_label is not None and self.category else None
                    ),
                    ValueLabelOptions=(
                        ChartAxisLabelOptions(AxisLabelOptions=[
                            AxisLabelOptions(
                                CustomLabel=value_label,
                                ApplyTo=_axis_label_apply_to(self.values[0]),
                            ),
                        ])
                        if value_label is not None and self.values else None
                    ),
                    ColorLabelOptions=(
                        ChartAxisLabelOptions(AxisLabelOptions=[
                            AxisLabelOptions(
                                CustomLabel=color_label,
                                ApplyTo=_axis_label_apply_to(self.colors[0]),
                            ),
                        ])
                        if color_label is not None and self.colors else None
                    ),
                    SortConfiguration=sort_config,
                ),
                Actions=[a.emit() for a in self.actions] if self.actions else None,
            ),
        )


@dataclass(eq=False)
class LineChart:
    """Line chart visual — one line per distinct ``colors`` value,
    plotted across ``category`` (x-axis) with height by ``values``
    (y-axis).

    Field-well shape: ``Category=[Dim, ...]`` + ``Values=[Measure, ...]``
    + ``Colors=[Dim, ...]``.

    ``chart_type`` selects ``LINE`` (default), ``AREA``, or
    ``STACKED_AREA``. ``sort_by`` is a ``(field_id, direction)`` tuple
    — direction ``"ASC"`` or ``"DESC"`` — and emits a ``CategorySort``
    entry. All optional fields default to ``None`` so the QuickSight
    defaults apply when not specified.

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str | None = None
    category: list[Dim] = field(default_factory=list[Dim])
    values: list[Measure] = field(default_factory=list[Measure])
    colors: list[Dim] = field(default_factory=list[Dim])
    chart_type: Literal["LINE", "AREA", "STACKED_AREA"] | None = None
    category_label: str | None = None
    value_label: str | None = None
    sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None
    actions: list[Action] = field(default_factory=list[Action])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "line"

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return ({d.dataset for d in self.category}
                | {m.dataset for m in self.values}
                | {d.dataset for d in self.colors})

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for d in self.category:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        for m in self.values:
            if (cf := m.calc_field()) is not None:
                deps.add(cf)
        for d in self.colors:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        return deps

    def emit(self) -> Visual:
        assert not isinstance(self.visual_id, _AutoSentinel), (
            "visual_id wasn't resolved — see KPI.emit assertion."
        )
        sort_config: LineChartSortConfiguration | None = None
        if self.sort_by is not None:
            ref, direction = self.sort_by
            sort_config = LineChartSortConfiguration(
                CategorySort=[
                    {"FieldSort": {
                        "FieldId": resolve_field_id(ref),
                        "Direction": direction,
                    }},
                ],
            )
        # v8.6.1 — match BarChart's auto-derive cascade. Author-supplied
        # labels still win.
        category_label = self.category_label
        if category_label is None and self.category:
            category_label = _field_label(self.category[0])
        value_label = self.value_label
        if value_label is None and self.values:
            value_label = _field_label(self.values[0])
        return Visual(
            LineChartVisual=LineChartVisual(
                VisualId=self.visual_id,
                Title=title_label(self.title),
                Subtitle=subtitle_label(self.subtitle) if self.subtitle else None,
                ChartConfiguration=LineChartConfiguration(
                    FieldWells=LineChartFieldWells(
                        LineChartAggregatedFieldWells=LineChartAggregatedFieldWells(
                            Category=[d.emit() for d in self.category] if self.category else None,
                            Values=[m.emit() for m in self.values] if self.values else None,
                            Colors=[d.emit() for d in self.colors] if self.colors else None,
                        ),
                    ),
                    Type=self.chart_type,
                    SortConfiguration=sort_config,
                    XAxisLabelOptions=(
                        ChartAxisLabelOptions(AxisLabelOptions=[
                            AxisLabelOptions(
                                CustomLabel=category_label,
                                ApplyTo=_axis_label_apply_to(self.category[0]),
                            ),
                        ])
                        if category_label is not None and self.category else None
                    ),
                    PrimaryYAxisLabelOptions=(
                        ChartAxisLabelOptions(AxisLabelOptions=[
                            AxisLabelOptions(
                                CustomLabel=value_label,
                                ApplyTo=_axis_label_apply_to(self.values[0]),
                            ),
                        ])
                        if self.value_label is not None else None
                    ),
                ),
                Actions=[a.emit() for a in self.actions] if self.actions else None,
            ),
        )


@dataclass(eq=False)
class Sankey:
    """Sankey diagram visual — flows from ``source`` nodes to
    ``target`` nodes, ribbon thickness by ``weight``.

    Field-well shape: each of ``source`` / ``target`` / ``weight`` is
    a single ``Dim`` / ``Measure`` (the underlying model expects
    lists, but every usage today has exactly one entry; emit wraps).

    ``items_limit`` caps the number of source / destination nodes
    rendered (matches the ``ItemsLimit`` shape on the underlying
    sort configuration). ``OtherCategories`` defaults to ``"INCLUDE"``
    so capped flows roll into a "(others)" bucket rather than being
    dropped silently.

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str | None = None
    source: Dim | None = None
    target: Dim | None = None
    weight: Measure | None = None
    items_limit: int | None = None
    actions: list[Action] = field(default_factory=list[Action])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "sankey"

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        deps: set[Dataset] = set()
        if self.source is not None:
            deps.add(self.source.dataset)
        if self.target is not None:
            deps.add(self.target.dataset)
        if self.weight is not None:
            deps.add(self.weight.dataset)
        return deps

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for leaf in (self.source, self.target, self.weight):
            if leaf is None:
                continue
            if (cf := leaf.calc_field()) is not None:
                deps.add(cf)
        return deps

    def emit(self) -> Visual:
        assert not isinstance(self.visual_id, _AutoSentinel), (
            "visual_id wasn't resolved — see KPI.emit assertion."
        )
        sort_config: Any = None
        if self.weight is not None or self.items_limit is not None:
            sort_config_kwargs: dict[str, Any] = {}
            if self.weight is not None:
                sort_config_kwargs["WeightSort"] = [
                    {
                        "FieldSort": {
                            "FieldId": resolve_field_id(self.weight),
                            "Direction": "DESC",
                        },
                    },
                ]
            if self.items_limit is not None:
                limit_block = {
                    "ItemsLimit": self.items_limit,
                    "OtherCategories": "INCLUDE",
                }
                sort_config_kwargs["SourceItemsLimit"] = limit_block
                sort_config_kwargs["DestinationItemsLimit"] = limit_block
            sort_config = SankeyDiagramSortConfiguration(**sort_config_kwargs)
        return Visual(
            SankeyDiagramVisual=SankeyDiagramVisual(
                VisualId=self.visual_id,
                Title=title_label(self.title),
                Subtitle=subtitle_label(self.subtitle) if self.subtitle else None,
                ChartConfiguration=SankeyDiagramChartConfiguration(
                    FieldWells=SankeyDiagramFieldWells(
                        SankeyDiagramAggregatedFieldWells=SankeyDiagramAggregatedFieldWells(
                            Source=[self.source.emit()] if self.source else None,
                            Destination=[self.target.emit()] if self.target else None,
                            Weight=[self.weight.emit()] if self.weight else None,
                        ),
                    ),
                    SortConfiguration=sort_config,
                ),
                Actions=[a.emit() for a in self.actions] if self.actions else None,
            ),
        )


@dataclass(eq=False)
class ForceGraph:
    """Force-directed network visual — HTMX-dialect only (X.2 spike
    capability test for X.4).

    QuickSight's standard visual library doesn't include a force
    layout (only hierarchical ``SankeyDiagramVisual``), so this
    primitive exists to prove the L1 tree primitives can host a
    visual kind that no QS dialect emit knows how to render. The
    HTMX renderer's bootstrap dispatches to ``renderForceGraph``
    via d3-force; ``emit()`` raises because the QS pipeline
    intentionally has no path for this kind.

    Phase.1 design call: either keep this HTMX-only or wire a
    custom-visual emitter for QS. The capability test is the
    artifact; the layering decision is downstream.

    No field-well slots — the visual's data shape (nodes + links)
    flows through the data fetcher directly, not through QS field
    wells. ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str | None = None
    actions: list[Action] = field(default_factory=list[Action])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "force-graph"

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return set()

    def calc_fields(self) -> set[CalcField]:
        return set()

    def emit(self) -> Visual:
        raise NotImplementedError(
            "ForceGraph is an HTMX-dialect-only visual (X.2 spike "
            "capability test). QS has no force-layout visual; this "
            "is intentionally not wired to the QS emit path."
        )
