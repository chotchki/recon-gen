"""Dataclass models mapping to AWS QuickSight API JSON structures.

Each top-level model (Theme, DataSet, Analysis) has a `to_aws_json()` method
that returns the exact dict shape expected by the corresponding AWS CLI command
(create-theme, create-data-set, create-analysis).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, ClassVar, Literal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_nones(obj: Any) -> Any:
    """Recursively remove keys with None values from dicts.

    The shape is necessarily ``Any`` — this walks arbitrary
    QuickSight-API JSON structures (asdict() output for any model).
    Pyright would otherwise demand a recursive ``JsonValue`` alias
    that doesn't buy us anything for an internal helper.
    """
    if isinstance(obj, dict):
        return {  # pyright: ignore[reportUnknownVariableType]: walks arbitrary QS-API JSON, recursive Any by design
            k: _strip_nones(v)  # pyright: ignore[reportUnknownArgumentType]: walks arbitrary QS-API JSON, recursive Any by design
            for k, v in obj.items()  # pyright: ignore[reportUnknownVariableType]: walks arbitrary QS-API JSON, recursive Any by design
            if v is not None
        }
    if isinstance(obj, list):
        return [
            _strip_nones(v)  # pyright: ignore[reportUnknownArgumentType]: walks arbitrary QS-API JSON, recursive Any by design
            for v in obj  # pyright: ignore[reportUnknownVariableType]: walks arbitrary QS-API JSON, recursive Any by design
        ]
    if isinstance(obj, Enum):
        return obj.value
    return obj


# ---------------------------------------------------------------------------
# Common / shared types
# ---------------------------------------------------------------------------

@dataclass
class ColumnIdentifier:
    """References a column on a specific dataset by identifier.

    Reused across many AWS QuickSight shapes — every typed field-well
    leaf (CategoricalDimensionField, DateDimensionField, etc.) and
    cascading control source carries one.
    """
    DataSetIdentifier: str
    ColumnName: str


@dataclass
class CategoricalDimensionField:
    FieldId: str
    Column: ColumnIdentifier
    HierarchyId: str | None = None


@dataclass
class DateDimensionField:
    FieldId: str
    Column: ColumnIdentifier
    DateGranularity: str | None = None  # YEAR|QUARTER|MONTH|WEEK|DAY|HOUR|...
    HierarchyId: str | None = None


@dataclass
class NumericalDimensionField:
    FieldId: str
    Column: ColumnIdentifier
    HierarchyId: str | None = None
    FormatConfiguration: NumberFormatConfiguration | None = None


@dataclass
class DimensionField:
    """Union type — set exactly one."""
    CategoricalDimensionField: CategoricalDimensionField | None = None
    DateDimensionField: DateDimensionField | None = None
    NumericalDimensionField: NumericalDimensionField | None = None


@dataclass
class NumericalAggregationFunction:
    SimpleNumericalAggregation: str | None = None  # SUM|COUNT|AVG|MIN|MAX


@dataclass
class DecimalPlacesConfiguration:
    """Wire shape for the per-field decimal-places setting."""
    DecimalPlaces: int


@dataclass
class ThousandSeparatorOptions:
    Symbol: str  # COMMA | DOT | SPACE
    Visibility: str  # VISIBLE | HIDDEN


@dataclass
class SeparatorConfiguration:
    DecimalSeparator: str | None = None  # COMMA | DOT | SPACE
    ThousandsSeparator: ThousandSeparatorOptions | None = None


@dataclass
class CurrencyDisplayFormatConfiguration:
    """Currency-format wire shape. ``Symbol`` is an ISO 4217 code
    (``"USD"``); QuickSight renders the appropriate symbol prefix
    (``$`` for USD, etc.) automatically."""
    Symbol: str | None = None
    DecimalPlacesConfiguration: DecimalPlacesConfiguration | None = None
    SeparatorConfiguration: SeparatorConfiguration | None = None


@dataclass
class NumericFormatConfiguration:
    """Discriminated union — exactly one of the three sub-format
    configurations is set."""
    NumberDisplayFormatConfiguration: dict[str, Any] | None = None
    CurrencyDisplayFormatConfiguration: CurrencyDisplayFormatConfiguration | None = None
    PercentageDisplayFormatConfiguration: dict[str, Any] | None = None


@dataclass
class NumberFormatConfiguration:
    """Wrapper carrying the actual format inside its
    ``FormatConfiguration`` slot — this two-level nesting matches the
    AWS QuickSight wire schema."""
    FormatConfiguration: NumericFormatConfiguration | None = None


@dataclass
class NumericalMeasureField:
    FieldId: str
    Column: ColumnIdentifier
    AggregationFunction: NumericalAggregationFunction | None = None
    FormatConfiguration: NumberFormatConfiguration | None = None


@dataclass
class CategoricalMeasureField:
    FieldId: str
    Column: ColumnIdentifier
    AggregationFunction: str | None = None  # COUNT|DISTINCT_COUNT


@dataclass
class DateMeasureField:
    FieldId: str
    Column: ColumnIdentifier
    AggregationFunction: str | None = None  # COUNT|DISTINCT_COUNT|MIN|MAX


@dataclass
class MeasureField:
    """Union type — set exactly one."""
    NumericalMeasureField: NumericalMeasureField | None = None
    CategoricalMeasureField: CategoricalMeasureField | None = None
    DateMeasureField: DateMeasureField | None = None


@dataclass
class VisualTitleLabelOptions:
    Visibility: str = "VISIBLE"  # VISIBLE|HIDDEN
    FormatText: dict[str, str] | None = None  # {"PlainText": "..."}


@dataclass
class VisualSubtitleLabelOptions:
    Visibility: str = "VISIBLE"
    FormatText: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Theme models
# ---------------------------------------------------------------------------

@dataclass
class DataColorPalette:
    Colors: list[str] | None = None
    EmptyFillColor: str | None = None
    MinMaxGradient: list[str] | None = None


@dataclass
class UIColorPalette:
    PrimaryBackground: str | None = None
    PrimaryForeground: str | None = None
    SecondaryBackground: str | None = None
    SecondaryForeground: str | None = None
    Accent: str | None = None
    AccentForeground: str | None = None
    Danger: str | None = None
    DangerForeground: str | None = None
    Warning: str | None = None
    WarningForeground: str | None = None
    Success: str | None = None
    SuccessForeground: str | None = None
    Dimension: str | None = None
    DimensionForeground: str | None = None
    Measure: str | None = None
    MeasureForeground: str | None = None


@dataclass
class TileBorder:
    Show: bool | None = None


@dataclass
class Tile:
    Border: TileBorder | None = None


@dataclass
class Gutter:
    Show: bool | None = None


@dataclass
class Margin:
    Show: bool | None = None


@dataclass
class TileLayout:
    Gutter: Gutter | None = None
    Margin: Margin | None = None


@dataclass
class SheetStyle:
    Tile: Tile | None = None
    TileLayout: TileLayout | None = None


@dataclass
class FontFamily:
    FontFamily: str


@dataclass
class Typography:
    FontFamilies: list[FontFamily] | None = None


@dataclass
class ThemeConfiguration:
    DataColorPalette: DataColorPalette | None = None
    UIColorPalette: UIColorPalette | None = None
    Sheet: SheetStyle | None = None
    Typography: Typography | None = None


@dataclass
class Tag:
    Key: str
    Value: str


@dataclass
class ResourcePermission:
    Principal: str
    Actions: list[str]


@dataclass
class Theme:
    AwsAccountId: str
    ThemeId: str
    Name: str
    BaseThemeId: str
    Configuration: ThemeConfiguration
    Permissions: list[ResourcePermission] | None = None
    Tags: list[Tag] | None = None
    VersionDescription: str | None = None

    def to_aws_json(self) -> dict[str, Any]:
        return _strip_nones(asdict(self))

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_aws_json(), indent=indent)


# ---------------------------------------------------------------------------
# DataSource models
# ---------------------------------------------------------------------------

@dataclass
class PostgreSqlParameters:
    Host: str
    Port: int
    Database: str


@dataclass
class OracleParameters:
    """QuickSight OracleParameters shape (boto3 quicksight create-data-source).

    Per the AWS QuickSight API:
    - ``Host`` — RDS Oracle endpoint hostname.
    - ``Port`` — listener port (defaults 1521).
    - ``Database`` — SID or service name (e.g. ``ORCL``).
    - ``UseServiceName`` — when True, ``Database`` is treated as a
      service name; when False (AWS default), as a SID.

    ``UseServiceName`` defaults True on this dataclass because RDS
    Oracle endpoints (and Aurora Oracle) connect via service name —
    the SID-style interpretation only matches older self-managed
    Oracle installs. Override at construction if needed.
    """

    Host: str
    Port: int
    Database: str
    UseServiceName: bool = True


@dataclass
class DataSourceParameters:
    PostgreSqlParameters: PostgreSqlParameters | None = None
    OracleParameters: OracleParameters | None = None


@dataclass
class CredentialPair:
    Username: str
    Password: str


@dataclass
class DataSourceCredentials:
    CredentialPair: CredentialPair | None = None


@dataclass
class SslProperties:
    DisableSsl: bool = False


@dataclass
class DataSource:
    AwsAccountId: str
    DataSourceId: str
    Name: str
    Type: str  # POSTGRESQL, MYSQL, etc.
    DataSourceParameters: DataSourceParameters
    Credentials: DataSourceCredentials | None = None
    SslProperties: SslProperties | None = None
    Permissions: list[ResourcePermission] | None = None
    Tags: list[Tag] | None = None

    def to_aws_json(self) -> dict[str, Any]:
        return _strip_nones(asdict(self))

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_aws_json(), indent=indent)


# ---------------------------------------------------------------------------
# DataSet models
# ---------------------------------------------------------------------------

@dataclass
class InputColumn:
    Name: str
    Type: str  # STRING|INTEGER|DECIMAL|DATETIME|BIT
    SubType: str | None = None


@dataclass
class CustomSql:
    Name: str
    DataSourceArn: str
    SqlQuery: str
    Columns: list[InputColumn]


@dataclass
class PhysicalTable:
    """Union type — set exactly one."""
    CustomSql: CustomSql | None = None


@dataclass
class LogicalTableSource:
    PhysicalTableId: str | None = None
    DataSetArn: str | None = None


@dataclass
class LogicalTable:
    Alias: str
    Source: LogicalTableSource
    DataTransforms: list[dict[str, Any]] | None = None


@dataclass
class DataSetUsageConfiguration:
    DisableUseAsDirectQuerySource: bool = False
    DisableUseAsImportedSource: bool = False


# AWS QuickSight `create-data-set` rejects a dataset parameter whose
# `DefaultValues.StaticValues` list has > 32 elements ("member must have
# length less than or equal to 32" — the array length, not per-string
# length). For a dropdown whose value universe is unbounded (rail / chain
# / template / transfer_type / role names — an institution may declare
# >32 of any), the default must be a short sentinel + a match-all SQL
# guard (`apps/l1_dashboard`'s `_data_value_clause`, `apps/l2_flow_tracing`'s
# `_match_all_in_clause`), NOT the value list (X.2.t.2). This is checked
# at construction so it fails at the buggy emit line, not 10 min into a
# deploy.
_DATASET_PARAM_STATIC_VALUES_CAP = 32


def _check_static_values_cap(
    values: Sequence[object] | None, kind: str,
) -> None:
    if values is not None and len(values) > _DATASET_PARAM_STATIC_VALUES_CAP:
        raise ValueError(
            f"{kind}.DefaultValues.StaticValues has {len(values)} elements; "
            f"AWS QuickSight caps it at {_DATASET_PARAM_STATIC_VALUES_CAP}. "
            f"Use a 1-element sentinel default + a match-all SQL guard for an "
            f"unbounded value universe (see X.2.t.2 in PLAN.md)."
        )


@dataclass
class StringDatasetParameterDefaultValues:
    StaticValues: list[str] | None = None

    def __post_init__(self) -> None:
        _check_static_values_cap(self.StaticValues, "StringDatasetParameter")


@dataclass
class IntegerDatasetParameterDefaultValues:
    StaticValues: list[int] | None = None

    def __post_init__(self) -> None:
        _check_static_values_cap(self.StaticValues, "IntegerDatasetParameter")


@dataclass
class DecimalDatasetParameterDefaultValues:
    StaticValues: list[float] | None = None

    def __post_init__(self) -> None:
        _check_static_values_cap(self.StaticValues, "DecimalDatasetParameter")


@dataclass
class DateTimeDatasetParameterDefaultValues:
    StaticValues: list[str] | None = None  # ISO8601 datetime strings

    def __post_init__(self) -> None:
        _check_static_values_cap(self.StaticValues, "DateTimeDatasetParameter")


@dataclass(kw_only=True)
class StringDatasetParameter:
    # AK.1 — build_dataset assigns a deterministic, dataset-scoped UUID
    # (``auto_id(f"{dataset_id}:dsparam:{Name}")``). Construction sites do
    # NOT set this; "" is the unset marker the remap fills. Keeping it
    # app-un-settable makes a colliding hand-picked Id unrepresentable.
    Id: str = ""
    Name: str
    ValueType: str  # SINGLE_VALUED|MULTI_VALUED
    DefaultValues: StringDatasetParameterDefaultValues | None = None


@dataclass(kw_only=True)
class IntegerDatasetParameter:
    Id: str = ""  # AK.1 — see StringDatasetParameter.Id
    Name: str
    ValueType: str  # SINGLE_VALUED|MULTI_VALUED
    DefaultValues: IntegerDatasetParameterDefaultValues | None = None


@dataclass(kw_only=True)
class DecimalDatasetParameter:
    Id: str = ""  # AK.1 — see StringDatasetParameter.Id
    Name: str
    ValueType: str  # SINGLE_VALUED|MULTI_VALUED
    DefaultValues: DecimalDatasetParameterDefaultValues | None = None


@dataclass(kw_only=True)
class DateTimeDatasetParameter:
    Id: str = ""  # AK.1 — see StringDatasetParameter.Id
    Name: str
    ValueType: str  # SINGLE_VALUED|MULTI_VALUED
    TimeGranularity: str | None = None
    DefaultValues: DateTimeDatasetParameterDefaultValues | None = None


@dataclass
class DatasetParameter:
    """Discriminated union — set exactly one variant."""
    StringDatasetParameter: StringDatasetParameter | None = None
    IntegerDatasetParameter: IntegerDatasetParameter | None = None
    DecimalDatasetParameter: DecimalDatasetParameter | None = None
    DateTimeDatasetParameter: DateTimeDatasetParameter | None = None


@dataclass
class DataSet:
    AwsAccountId: str
    DataSetId: str
    Name: str
    PhysicalTableMap: dict[str, PhysicalTable]
    ImportMode: str = "DIRECT_QUERY"  # DIRECT_QUERY|SPICE
    LogicalTableMap: dict[str, LogicalTable] | None = None
    DataSetUsageConfiguration: DataSetUsageConfiguration | None = None
    Permissions: list[ResourcePermission] | None = None
    Tags: list[Tag] | None = None
    # Dataset-level parameters substituted into CustomSql via the
    # ``<<$paramName>>`` syntax. Bridge analysis params via
    # ``MappedDataSetParameters`` on each ParameterDeclaration variant.
    DatasetParameters: list[DatasetParameter] | None = None

    def to_aws_json(self) -> dict[str, Any]:
        return _strip_nones(asdict(self))

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_aws_json(), indent=indent)


# ---------------------------------------------------------------------------
# Analysis models — Visuals
# ---------------------------------------------------------------------------

# -- Axis labels (shared by bar, pie, etc.) --

@dataclass
class AxisLabelReferenceOptions:
    """Bind an ``AxisLabelOptions.CustomLabel`` to a specific field-well
    leaf. AWS QuickSight requires the ``ApplyTo`` reference for the
    custom label to render on the axis — without it, ``CustomLabel`` is
    silently ignored (chart axis renders the raw column name).

    See `quicksight-quirks.md` entry on chart axis labels.
    """
    FieldId: str
    Column: ColumnIdentifier


@dataclass
class AxisLabelOptions:
    """Label override for a single axis field.

    ``ApplyTo`` is the binding to a specific ``FieldId`` + dataset
    column. AWS QuickSight requires it for ``CustomLabel`` to render —
    a label without ``ApplyTo`` is parsed cleanly but produces a chart
    that still shows the raw column name on the axis (silent
    no-op). v8.6.1 made the field required at the type level after
    v8.5.5's auto-derive-but-no-render bug.
    """
    CustomLabel: str | None = None
    ApplyTo: AxisLabelReferenceOptions | None = None


@dataclass
class ChartAxisLabelOptions:
    """Axis label options — list of per-field overrides plus visibility."""
    Visibility: str = "VISIBLE"  # VISIBLE|HIDDEN
    AxisLabelOptions: list[AxisLabelOptions] | None = None


# -- Bar chart --

@dataclass
class BarChartAggregatedFieldWells:
    Category: list[DimensionField] | None = None
    Values: list[MeasureField] | None = None
    Colors: list[DimensionField] | None = None


@dataclass
class BarChartFieldWells:
    BarChartAggregatedFieldWells: BarChartAggregatedFieldWells | None = None


@dataclass
class BarChartSortConfiguration:
    CategorySort: list[dict[str, Any]] | None = None


@dataclass
class BarChartConfiguration:
    FieldWells: BarChartFieldWells | None = None
    Orientation: str | None = None  # HORIZONTAL|VERTICAL
    BarsArrangement: str | None = None  # CLUSTERED|STACKED|STACKED_PERCENT
    SortConfiguration: BarChartSortConfiguration | None = None
    CategoryLabelOptions: ChartAxisLabelOptions | None = None
    ValueLabelOptions: ChartAxisLabelOptions | None = None
    ColorLabelOptions: ChartAxisLabelOptions | None = None


@dataclass
class BarChartVisual:
    VisualId: str
    Title: VisualTitleLabelOptions | None = None
    Subtitle: VisualSubtitleLabelOptions | None = None
    ChartConfiguration: BarChartConfiguration | None = None
    Actions: list[VisualCustomAction] | None = None


# -- Line chart --

@dataclass
class LineChartAggregatedFieldWells:
    Category: list[DimensionField] | None = None  # x-axis
    Values: list[MeasureField] | None = None       # y-axis
    Colors: list[DimensionField] | None = None     # one line per color value


@dataclass
class LineChartFieldWells:
    LineChartAggregatedFieldWells: LineChartAggregatedFieldWells | None = None


@dataclass
class LineChartSortConfiguration:
    CategorySort: list[dict[str, Any]] | None = None


@dataclass
class LineChartConfiguration:
    FieldWells: LineChartFieldWells | None = None
    Type: str | None = None  # LINE | AREA | STACKED_AREA — default LINE
    SortConfiguration: LineChartSortConfiguration | None = None
    XAxisLabelOptions: ChartAxisLabelOptions | None = None
    PrimaryYAxisLabelOptions: ChartAxisLabelOptions | None = None


@dataclass
class LineChartVisual:
    VisualId: str
    Title: VisualTitleLabelOptions | None = None
    Subtitle: VisualSubtitleLabelOptions | None = None
    ChartConfiguration: LineChartConfiguration | None = None
    Actions: list[VisualCustomAction] | None = None


# -- Pie chart --

@dataclass
class PieChartAggregatedFieldWells:
    Category: list[DimensionField] | None = None
    Values: list[MeasureField] | None = None


@dataclass
class PieChartFieldWells:
    PieChartAggregatedFieldWells: PieChartAggregatedFieldWells | None = None


@dataclass
class DonutOptions:
    ArcOptions: dict[str, str] | None = None  # {"ArcThickness": "MEDIUM"}


@dataclass
class PieChartConfiguration:
    FieldWells: PieChartFieldWells | None = None
    DonutOptions: DonutOptions | None = None
    CategoryLabelOptions: ChartAxisLabelOptions | None = None
    ValueLabelOptions: ChartAxisLabelOptions | None = None


@dataclass
class PieChartVisual:
    VisualId: str
    Title: VisualTitleLabelOptions | None = None
    Subtitle: VisualSubtitleLabelOptions | None = None
    ChartConfiguration: PieChartConfiguration | None = None
    Actions: list[VisualCustomAction] | None = None


# -- KPI --

@dataclass
class KPIFieldWells:
    Values: list[MeasureField] | None = None
    TargetValues: list[MeasureField] | None = None
    TrendGroups: list[DimensionField] | None = None


@dataclass
class KPIOptions:
    """KPI display options.

    QS rejects partial KPIOptions when the field-well has no
    TargetValue/TrendGroup (verified against hand-built control
    KPI on 2026-04-29 — `KPIOptions(PrimaryValueDisplayType="ACTUAL")`
    alone got rejected with "Only PrimaryValueFontSize display
    property can be defined..."). The hand-built shape — Comparison +
    PrimaryValueDisplayType + SecondaryValueFontConfiguration +
    Sparkline + VisualLayoutOptions — is the smallest set QS UI
    produces and accepts. Tree's KPI.emit() defaults to that shape.
    """
    Comparison: dict[str, Any] | None = None
    PrimaryValueDisplayType: str | None = None  # HIDDEN|COMPARISON|ACTUAL
    SecondaryValueFontConfiguration: dict[str, Any] | None = None
    Sparkline: dict[str, Any] | None = None
    VisualLayoutOptions: dict[str, Any] | None = None


@dataclass
class KPIConfiguration:
    FieldWells: KPIFieldWells | None = None
    KPIOptions: KPIOptions | None = None


@dataclass
class KPIVisual:
    VisualId: str
    Title: VisualTitleLabelOptions | None = None
    Subtitle: VisualSubtitleLabelOptions | None = None
    ChartConfiguration: KPIConfiguration | None = None
    # BK.2 — KPIConditionalFormatting free-form dict (QS's KPI
    # conditional-formatting shape is a typed nested-dict of options;
    # the tree's KPIValueZeroIndicator emits the canonical pair-of-
    # CHECKMARK/X options). Same pass-through pattern Table's
    # ConditionalFormatting follows.
    ConditionalFormatting: dict[str, Any] | None = None


# -- Table --

@dataclass
class TableAggregatedFieldWells:
    GroupBy: list[DimensionField] | None = None
    Values: list[MeasureField] | None = None


@dataclass
class TableUnaggregatedFieldWells:
    Values: list[dict[str, Any]] | None = None  # UnaggregatedField list


@dataclass
class TableFieldWells:
    TableAggregatedFieldWells: TableAggregatedFieldWells | None = None
    TableUnaggregatedFieldWells: TableUnaggregatedFieldWells | None = None


@dataclass
class TableOptions:
    HeaderStyle: dict[str, Any] | None = None
    CellStyle: dict[str, Any] | None = None


@dataclass
class TableFieldOption:
    """Per-column header override for a Table visual (v8.5.0).

    ``FieldId`` references one of the Table's field-well leaves
    (Dim or Measure). ``CustomLabel`` overrides the header text
    QuickSight would otherwise auto-derive from the column name.
    ``Visibility`` defaults to ``"VISIBLE"``.
    """
    FieldId: str
    CustomLabel: str | None = None
    Visibility: str = "VISIBLE"  # VISIBLE | HIDDEN


@dataclass
class TableFieldOptions:
    """Container for per-column table header overrides (v8.5.0)."""
    SelectedFieldOptions: list[TableFieldOption] | None = None


@dataclass
class TableConfiguration:
    FieldWells: TableFieldWells | None = None
    SortConfiguration: dict[str, Any] | None = None
    TableOptions: TableOptions | None = None
    FieldOptions: TableFieldOptions | None = None


@dataclass
class TableVisual:
    VisualId: str
    Title: VisualTitleLabelOptions | None = None
    Subtitle: VisualSubtitleLabelOptions | None = None
    ChartConfiguration: TableConfiguration | None = None
    Actions: list[VisualCustomAction] | None = None
    ConditionalFormatting: dict[str, Any] | None = None


# -- Sankey diagram --

@dataclass
class SankeyDiagramAggregatedFieldWells:
    Source: list[DimensionField] | None = None
    Destination: list[DimensionField] | None = None
    Weight: list[MeasureField] | None = None


@dataclass
class SankeyDiagramFieldWells:
    SankeyDiagramAggregatedFieldWells: SankeyDiagramAggregatedFieldWells | None = None


@dataclass
class SankeyDiagramSortConfiguration:
    # ItemsLimitConfiguration shape: {"ItemsLimit": int, "OtherCategories": "INCLUDE"|"EXCLUDE"}.
    # Caps how many distinct source / destination nodes the diagram
    # renders; over-cap entries roll up into "Other" or get dropped.
    WeightSort: list[dict[str, Any]] | None = None
    SourceItemsLimit: dict[str, Any] | None = None
    DestinationItemsLimit: dict[str, Any] | None = None


@dataclass
class SankeyDiagramChartConfiguration:
    FieldWells: SankeyDiagramFieldWells | None = None
    SortConfiguration: SankeyDiagramSortConfiguration | None = None
    DataLabels: dict[str, Any] | None = None


@dataclass
class SankeyDiagramVisual:
    VisualId: str
    Title: VisualTitleLabelOptions | None = None
    Subtitle: VisualSubtitleLabelOptions | None = None
    ChartConfiguration: SankeyDiagramChartConfiguration | None = None
    Actions: list[VisualCustomAction] | None = None


# -- Custom actions (drill-down navigation, filtering) --

@dataclass
class LocalNavigationConfiguration:
    TargetSheetId: str


@dataclass
class CustomActionNavigationOperation:
    LocalNavigationConfiguration: LocalNavigationConfiguration


@dataclass
class CustomActionSetParametersOperation:
    ParameterValueConfigurations: list[dict[str, Any]]


@dataclass
class SameSheetTargetVisualConfiguration:
    TargetVisualOptions: str | None = None  # ALL_VISUALS
    TargetVisuals: list[str] | None = None


@dataclass
class FilterOperationTargetVisualsConfiguration:
    SameSheetTargetVisualConfiguration: SameSheetTargetVisualConfiguration | None = None


@dataclass
class FilterOperationSelectedFieldsConfiguration:
    SelectedFieldOptions: str | None = None  # ALL_FIELDS
    SelectedFields: list[str] | None = None
    SelectedColumns: list[ColumnIdentifier] | None = None


@dataclass
class CustomActionFilterOperation:
    SelectedFieldsConfiguration: FilterOperationSelectedFieldsConfiguration
    TargetVisualsConfiguration: FilterOperationTargetVisualsConfiguration


@dataclass
class VisualCustomActionOperation:
    """Union type — set exactly one."""
    NavigationOperation: CustomActionNavigationOperation | None = None
    SetParametersOperation: CustomActionSetParametersOperation | None = None
    FilterOperation: CustomActionFilterOperation | None = None


@dataclass
class VisualCustomAction:
    # Trigger constants — prefer VisualCustomAction.DATA_POINT_CLICK
    # over the bare string literal at call sites.
    DATA_POINT_CLICK: ClassVar[Literal["DATA_POINT_CLICK"]] = "DATA_POINT_CLICK"
    DATA_POINT_MENU: ClassVar[Literal["DATA_POINT_MENU"]] = "DATA_POINT_MENU"
    # Status constants.
    ENABLED: ClassVar[Literal["ENABLED"]] = "ENABLED"
    DISABLED: ClassVar[Literal["DISABLED"]] = "DISABLED"

    CustomActionId: str
    Name: str
    Trigger: Literal["DATA_POINT_CLICK", "DATA_POINT_MENU"]
    ActionOperations: list[VisualCustomActionOperation]
    Status: Literal["ENABLED", "DISABLED"] = "ENABLED"


# -- Visual union --

@dataclass
class Visual:
    """Union type — set exactly one."""
    BarChartVisual: BarChartVisual | None = None
    LineChartVisual: LineChartVisual | None = None
    PieChartVisual: PieChartVisual | None = None
    KPIVisual: KPIVisual | None = None
    TableVisual: TableVisual | None = None
    SankeyDiagramVisual: SankeyDiagramVisual | None = None


# ---------------------------------------------------------------------------
# Analysis models — Filters
# ---------------------------------------------------------------------------

@dataclass
class DefaultDateTimePickerControlOptions:
    Type: str = "DATE_RANGE"  # SINGLE_VALUED|DATE_RANGE
    CommitMode: str | None = None  # AUTO|MANUAL


@dataclass
class DefaultDropdownControlOptions:
    Type: str = "MULTI_SELECT"  # MULTI_SELECT|SINGLE_SELECT
    CommitMode: str | None = None  # AUTO|MANUAL


@dataclass
class DefaultSliderControlOptions:
    MaximumValue: float
    MinimumValue: float
    StepSize: float
    Type: str = "SINGLE_POINT"  # SINGLE_POINT|RANGE


@dataclass
class DefaultFilterControlOptions:
    """Union type — set exactly one."""
    DefaultDateTimePickerOptions: DefaultDateTimePickerControlOptions | None = None
    DefaultDropdownOptions: DefaultDropdownControlOptions | None = None
    DefaultSliderOptions: DefaultSliderControlOptions | None = None


@dataclass
class DefaultFilterControlConfiguration:
    Title: str
    ControlOptions: DefaultFilterControlOptions


@dataclass
class CategoryFilterConfiguration:
    FilterListConfiguration: dict[str, Any] | None = None
    CustomFilterListConfiguration: dict[str, Any] | None = None
    CustomFilterConfiguration: dict[str, Any] | None = None


@dataclass
class CategoryFilter:
    FilterId: str
    Column: ColumnIdentifier
    Configuration: CategoryFilterConfiguration
    DefaultFilterControlConfiguration: DefaultFilterControlConfiguration | None = None


@dataclass
class TimeRangeFilter:
    FilterId: str
    Column: ColumnIdentifier
    NullOption: str = "NON_NULLS_ONLY"  # ALL_VALUES|NULLS_ONLY|NON_NULLS_ONLY
    TimeGranularity: str | None = None
    RangeMinimumValue: dict[str, Any] | None = None
    RangeMaximumValue: dict[str, Any] | None = None
    IncludeMinimum: bool | None = None
    IncludeMaximum: bool | None = None
    DefaultFilterControlConfiguration: DefaultFilterControlConfiguration | None = None


@dataclass
class TimeEqualityFilter:
    FilterId: str
    Column: ColumnIdentifier
    Value: str | None = None  # ISO datetime; pair with TimeGranularity
    ParameterName: str | None = None
    TimeGranularity: str | None = None
    RollingDate: dict[str, Any] | None = None
    DefaultFilterControlConfiguration: DefaultFilterControlConfiguration | None = None


@dataclass
class NumericRangeFilterValue:
    """Set exactly one — a literal bound or a parameter binding."""
    StaticValue: float | None = None
    Parameter: str | None = None  # name of an IntegerParameter / DecimalParameter


@dataclass
class NumericRangeFilter:
    FilterId: str
    Column: ColumnIdentifier
    NullOption: str = "NON_NULLS_ONLY"
    RangeMinimum: NumericRangeFilterValue | None = None
    RangeMaximum: NumericRangeFilterValue | None = None
    IncludeMinimum: bool | None = None
    IncludeMaximum: bool | None = None
    DefaultFilterControlConfiguration: DefaultFilterControlConfiguration | None = None


@dataclass
class Filter:
    """Union type — set exactly one."""
    CategoryFilter: CategoryFilter | None = None
    TimeRangeFilter: TimeRangeFilter | None = None
    TimeEqualityFilter: TimeEqualityFilter | None = None
    NumericRangeFilter: NumericRangeFilter | None = None


@dataclass
class SheetVisualScopingConfiguration:
    # Scope constants — prefer SheetVisualScopingConfiguration.ALL_VISUALS
    # over the bare string literal at call sites.
    ALL_VISUALS: ClassVar[Literal["ALL_VISUALS"]] = "ALL_VISUALS"
    SELECTED_VISUALS: ClassVar[Literal["SELECTED_VISUALS"]] = "SELECTED_VISUALS"

    SheetId: str
    Scope: Literal["ALL_VISUALS", "SELECTED_VISUALS"]
    VisualIds: list[str] | None = None


@dataclass
class SelectedSheetsFilterScopeConfiguration:
    SheetVisualScopingConfigurations: list[SheetVisualScopingConfiguration] | None = None


@dataclass
class AllSheetsFilterScopeConfiguration:
    pass  # empty object — presence alone means "all sheets"


@dataclass
class FilterScopeConfiguration:
    """Union type — set exactly one."""
    AllSheets: AllSheetsFilterScopeConfiguration | None = None
    SelectedSheets: SelectedSheetsFilterScopeConfiguration | None = None


@dataclass
class FilterGroup:
    # CrossDataset constants — prefer FilterGroup.SINGLE_DATASET over the
    # bare string literal at call sites.
    SINGLE_DATASET: ClassVar[Literal["SINGLE_DATASET"]] = "SINGLE_DATASET"
    ALL_DATASETS: ClassVar[Literal["ALL_DATASETS"]] = "ALL_DATASETS"
    # Status constants.
    ENABLED: ClassVar[Literal["ENABLED"]] = "ENABLED"
    DISABLED: ClassVar[Literal["DISABLED"]] = "DISABLED"

    FilterGroupId: str
    Filters: list[Filter]
    ScopeConfiguration: FilterScopeConfiguration
    CrossDataset: Literal["SINGLE_DATASET", "ALL_DATASETS"] = "SINGLE_DATASET"
    Status: Literal["ENABLED", "DISABLED"] | None = None


# ---------------------------------------------------------------------------
# Analysis models — Filter controls
# ---------------------------------------------------------------------------

@dataclass
class FilterDropDownControl:
    FilterControlId: str
    Title: str
    SourceFilterId: str
    Type: str | None = None  # MULTI_SELECT|SINGLE_SELECT
    # FilterSelectableValues shape: {"Values": [str, ...]}. Restricts the
    # dropdown menu to a fixed list of options instead of auto-populating
    # from the column. Useful for toggle-like controls where only one
    # option (e.g. "Unsettled") should be pickable.
    SelectableValues: dict[str, Any] | None = None


@dataclass
class FilterDateTimePickerControl:
    FilterControlId: str
    Title: str
    SourceFilterId: str
    Type: str | None = None  # SINGLE_VALUED|DATE_RANGE


@dataclass
class FilterSliderControl:
    FilterControlId: str
    Title: str
    SourceFilterId: str
    MaximumValue: float
    MinimumValue: float
    StepSize: float
    Type: str | None = None  # SINGLE_POINT|RANGE


@dataclass
class FilterCrossSheetControl:
    FilterControlId: str
    SourceFilterId: str


@dataclass
class FilterControl:
    """Union type — set exactly one."""
    Dropdown: FilterDropDownControl | None = None
    DateTimePicker: FilterDateTimePickerControl | None = None
    Slider: FilterSliderControl | None = None
    CrossSheet: FilterCrossSheetControl | None = None


# ---------------------------------------------------------------------------
# Analysis models — Parameter controls
#
# QuickSight disables a regular FilterControl whose backing filter is
# parameter-bound (CustomFilterConfiguration with ParameterName) — the UI
# shows "this control was disabled because the filter is using
# parameters". The right widget for that case is a ParameterControl
# bound directly to the parameter; the parameter-bound filter then
# responds to the parameter value the control writes.
# ---------------------------------------------------------------------------

@dataclass
class ParameterDropDownControl:
    ParameterControlId: str
    Title: str
    SourceParameterName: str
    Type: str | None = None  # SINGLE_SELECT|MULTI_SELECT
    # ParameterSelectableValues shape: either {"Values": [str, ...]}
    # for a static list or {"LinkToDataSetColumn": {"DataSetIdentifier",
    # "ColumnName"}} for an auto-populated list. The link query bypasses
    # the sheet's parameter-bound filter so users see every available
    # option, not the filtered slice.
    SelectableValues: dict[str, Any] | None = None
    # DropDownControlDisplayOptions shape: e.g.
    #   {"SelectAllOptions": {"Visibility": "HIDDEN"}}
    # to suppress the "Select all" entry. Useful for SINGLE_SELECT
    # dropdowns where empty/All semantics don't apply (e.g., a Sankey
    # anchor that needs exactly one value to render).
    DisplayOptions: dict[str, Any] | None = None
    # UI-level cascade wiring: when any control listed in
    # SourceControls changes, refresh THIS control's options. Without
    # this, dataset-parameter-bridged cascades (MappedDataSetParameters)
    # don't trigger dropdown refresh — the dataset re-queries on
    # parameter change but the control's cached snapshot of options
    # stays stale.
    CascadingControlConfiguration: CascadingControlConfiguration | None = None


@dataclass
class ParameterDateTimePickerControl:
    ParameterControlId: str
    Title: str
    SourceParameterName: str


@dataclass
class ParameterSliderControl:
    ParameterControlId: str
    Title: str
    SourceParameterName: str
    MinimumValue: float
    MaximumValue: float
    StepSize: float


@dataclass
class ParameterTextFieldControl:
    """Free-text input bound to a string parameter. The analyst types a
    value; QS sets the bound parameter to that value (no enumeration).

    Right shape for parameters whose option universe is unbounded /
    unknown at deploy time, or where the LinkedValues / StaticValues
    sample-fetch path is misbehaving (the X.1.b L2FT cascade Value
    dropdown ran into ``Sample values not found`` from QS's lazy
    sample-values fetch on cold per-CI-run dashboards; text input has
    no equivalent fetch path).
    """
    ParameterControlId: str
    Title: str
    SourceParameterName: str


@dataclass
class ParameterControl:
    """Union type — set exactly one."""
    Dropdown: ParameterDropDownControl | None = None
    DateTimePicker: ParameterDateTimePickerControl | None = None
    Slider: ParameterSliderControl | None = None
    TextField: ParameterTextFieldControl | None = None


# ---------------------------------------------------------------------------
# Analysis models — Sheet & Layout
# ---------------------------------------------------------------------------

@dataclass
class FreeFormLayoutElement:
    # ElementType constants — prefer FreeFormLayoutElement.VISUAL over
    # the bare string literal at call sites.
    VISUAL: ClassVar[Literal["VISUAL"]] = "VISUAL"
    FILTER_CONTROL: ClassVar[Literal["FILTER_CONTROL"]] = "FILTER_CONTROL"
    PARAMETER_CONTROL: ClassVar[Literal["PARAMETER_CONTROL"]] = "PARAMETER_CONTROL"
    TEXT_BOX: ClassVar[Literal["TEXT_BOX"]] = "TEXT_BOX"
    IMAGE: ClassVar[Literal["IMAGE"]] = "IMAGE"

    ElementId: str
    ElementType: Literal[
        "VISUAL", "FILTER_CONTROL", "PARAMETER_CONTROL", "TEXT_BOX", "IMAGE"
    ]
    XAxisLocation: str  # pixels as string
    YAxisLocation: str
    Width: str
    Height: str
    Visibility: str = "VISIBLE"


@dataclass
class FreeFormLayoutConfiguration:
    Elements: list[FreeFormLayoutElement]


@dataclass
class GridLayoutElement:
    # ElementType constants — prefer GridLayoutElement.VISUAL over the
    # bare string literal at call sites.
    VISUAL: ClassVar[Literal["VISUAL"]] = "VISUAL"
    FILTER_CONTROL: ClassVar[Literal["FILTER_CONTROL"]] = "FILTER_CONTROL"
    PARAMETER_CONTROL: ClassVar[Literal["PARAMETER_CONTROL"]] = "PARAMETER_CONTROL"
    TEXT_BOX: ClassVar[Literal["TEXT_BOX"]] = "TEXT_BOX"
    IMAGE: ClassVar[Literal["IMAGE"]] = "IMAGE"

    ElementId: str
    ElementType: Literal[
        "VISUAL", "FILTER_CONTROL", "PARAMETER_CONTROL", "TEXT_BOX", "IMAGE"
    ]
    ColumnSpan: int
    RowSpan: int
    ColumnIndex: int | None = None
    RowIndex: int | None = None
    # v8.6.9 — QS UI calls this "Card layout padding"; CSS-shaped string
    # like ``"12px"``. Applies to any grid element type (VISUAL / TEXT_BOX
    # / etc.) and gives the rendered card interior breathing room. None
    # falls back to QS's bare default (no padding).
    Padding: str | None = None


@dataclass
class GridLayoutConfiguration:
    Elements: list[GridLayoutElement]
    # M.4.4.10ab — QS UI emits CanvasSizeOptions inside every GridLayout.
    # Without it the editor fails when adding visuals (verified against
    # hand-built control on 2026-04-29). dict[str, Any] for now —
    # the shape is `{ScreenCanvasSizeOptions: {ResizeOption: FIXED,
    # OptimizedViewPortWidth: "1600px"}}`. Promote to typed dataclass
    # if a third call site needs it.
    CanvasSizeOptions: dict[str, Any] | None = None


@dataclass
class LayoutConfiguration:
    """Union type — set exactly one."""
    GridLayout: GridLayoutConfiguration | None = None
    FreeFormLayout: FreeFormLayoutConfiguration | None = None


@dataclass
class Layout:
    Configuration: LayoutConfiguration


@dataclass
class SheetTextBox:
    SheetTextBoxId: str
    Content: str  # rich-text HTML


@dataclass
class SheetDefinition:
    SheetId: str
    Name: str | None = None
    Title: str | None = None
    Description: str | None = None
    ContentType: str = "INTERACTIVE"  # INTERACTIVE|PAGINATED
    Visuals: list[Visual] | None = None
    FilterControls: list[FilterControl] | None = None
    ParameterControls: list[ParameterControl] | None = None
    Layouts: list[Layout] | None = None
    TextBoxes: list[SheetTextBox] | None = None


# ---------------------------------------------------------------------------
# Analysis models — Top-level
# ---------------------------------------------------------------------------

@dataclass
class DataSetIdentifierDeclaration:
    Identifier: str
    DataSetArn: str


@dataclass
class MappedDataSetParameter:
    """Bridges an analysis-level parameter to a dataset-level parameter.

    When the analysis parameter changes, QuickSight pushes the value
    into the named dataset parameter — which then substitutes into the
    dataset's CustomSql via ``<<$paramName>>`` at query time. The
    mapping list lives on the analysis ParameterDeclaration variant
    (StringParameterDeclaration, etc.).
    """
    DataSetIdentifier: str
    DataSetParameterName: str


@dataclass
class CascadingControlSource:
    """One source control in a CascadingControlConfiguration.

    ``SourceSheetControlId`` is the upstream control's ID; QS refreshes
    THIS control whenever that source control's value changes.
    ``ColumnToMatch`` is the documented value-match hint (used by
    column-on-column cascades); for parameter-bridged cascades it can
    be the dependent control's own source column — it doubles as a
    "this column is the one that varies with the source" marker.
    """
    SourceSheetControlId: str | None = None
    ColumnToMatch: ColumnIdentifier | None = None


@dataclass
class CascadingControlConfiguration:
    """UI-level cascade wiring: tells QS to refresh THIS control's
    options when any of the listed source controls change.

    Required for the M.3.10c metadata cascade — without this, even
    with `MappedDataSetParameters` correctly bridging analysis params
    to dataset params, QS won't refresh the dependent dropdown's
    options when the source dropdown changes (the dataset query
    substitution stays "pending" until something else triggers a
    refresh, like a sheet reload).
    """
    SourceControls: list[CascadingControlSource] | None = None


@dataclass
class StringParameterDeclaration:
    ParameterValueType: str  # SINGLE_VALUED|MULTI_VALUED
    Name: str
    DefaultValues: dict[str, Any]
    MappedDataSetParameters: list[MappedDataSetParameter] | None = None


@dataclass
class IntegerParameterDeclaration:
    ParameterValueType: str  # SINGLE_VALUED|MULTI_VALUED
    Name: str
    DefaultValues: dict[str, Any]  # {"StaticValues": [int]}
    MappedDataSetParameters: list[MappedDataSetParameter] | None = None


@dataclass
class DateTimeDefaultValues:
    StaticValues: list[str] | None = None
    DynamicValue: dict[str, Any] | None = None
    RollingDate: dict[str, Any] | None = None


@dataclass
class DateTimeParameterDeclaration:
    Name: str
    TimeGranularity: str | None = None
    DefaultValues: DateTimeDefaultValues | None = None
    ValueWhenUnset: dict[str, Any] | None = None
    MappedDataSetParameters: list[MappedDataSetParameter] | None = None


@dataclass
class ParameterDeclaration:
    """Union type — set exactly one."""
    StringParameterDeclaration: StringParameterDeclaration | None = None
    IntegerParameterDeclaration: IntegerParameterDeclaration | None = None
    DateTimeParameterDeclaration: DateTimeParameterDeclaration | None = None


@dataclass
class AnalysisDefinition:
    DataSetIdentifierDeclarations: list[DataSetIdentifierDeclaration]
    Sheets: list[SheetDefinition] | None = None
    FilterGroups: list[FilterGroup] | None = None
    ParameterDeclarations: list[ParameterDeclaration] | None = None
    CalculatedFields: list[dict[str, Any]] | None = None
    # M.4.4.10ab — three top-level fields QS UI emits + appears to
    # require for editor compat. Without them the analysis loads but
    # adding visuals/sheets through the editor fails (verified against
    # hand-built control on 2026-04-29). dict[str, Any] until a typed
    # API materializes; defaults match QS UI's outputs exactly.
    Options: dict[str, Any] | None = None
    AnalysisDefaults: dict[str, Any] | None = None
    QueryExecutionOptions: dict[str, Any] | None = None


@dataclass
class Analysis:
    AwsAccountId: str
    AnalysisId: str
    Name: str
    Definition: AnalysisDefinition
    ThemeArn: str | None = None
    Permissions: list[ResourcePermission] | None = None
    Tags: list[Tag] | None = None

    def to_aws_json(self) -> dict[str, Any]:
        return _strip_nones(asdict(self))

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_aws_json(), indent=indent)


# ---------------------------------------------------------------------------
# Dashboard models
# ---------------------------------------------------------------------------

@dataclass
class DashboardPublishOptions:
    AdHocFilteringOption: dict[str, str] | None = None
    ExportToCSVOption: dict[str, str] | None = None
    SheetControlsOption: dict[str, str] | None = None


@dataclass
class LinkSharingConfiguration:
    Permissions: list[ResourcePermission] | None = None


@dataclass
class Dashboard:
    AwsAccountId: str
    DashboardId: str
    Name: str
    Definition: AnalysisDefinition
    ThemeArn: str | None = None
    Permissions: list[ResourcePermission] | None = None
    Tags: list[Tag] | None = None
    VersionDescription: str | None = None
    DashboardPublishOptions: DashboardPublishOptions | None = None
    LinkSharingConfiguration: LinkSharingConfiguration | None = None

    def to_aws_json(self) -> dict[str, Any]:
        return _strip_nones(asdict(self))

    def to_json_string(self, indent: int = 2) -> str:
        return json.dumps(self.to_aws_json(), indent=indent)
