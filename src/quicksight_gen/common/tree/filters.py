"""Filter primitives — typed Filter wrappers + ``FilterGroup``.

Filter groups carry their scope as object refs (``Sheet`` + ``[VisualLike]``)
and validate at the call site that scoped visuals belong to the
referenced sheet. Catches the wrong-sheet bug class — the type
checker carries the wiring; raise at construction confirms.

Typed Filter wrappers (``CategoryFilter`` / ``NumericRangeFilter`` /
``TimeRangeFilter``) sit alongside the FilterGroup. They share names
with the underlying ``models.py`` classes — models are aliased on
import so user-facing code reads cleanly:

    from quicksight_gen.common.tree import CategoryFilter, FilterGroup

The ``NumericRangeFilter``'s ``minimum_parameter`` /
``maximum_parameter`` fields take a ``ParameterDeclLike`` object ref —
the type checker catches "filter bound to undeclared parameter" at
the wiring site, where the existing string-keyed ``Parameter=name``
pattern lets typos through to deploy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol, runtime_checkable

from quicksight_gen.common.ids import FilterGroupId
from quicksight_gen.common.models import (
    CategoryFilterConfiguration,
    ColumnIdentifier,
    Filter,
    FilterScopeConfiguration,
    NumericRangeFilterValue,
    SelectedSheetsFilterScopeConfiguration,
    SheetVisualScopingConfiguration,
)
from quicksight_gen.common.models import CategoryFilter as ModelCategoryFilter
from quicksight_gen.common.models import TimeEqualityFilter as ModelTimeEqualityFilter
from quicksight_gen.common.models import (
    DefaultDateTimePickerControlOptions as ModelDefaultDateTimePickerControlOptions,
)
from quicksight_gen.common.models import (
    DefaultDropdownControlOptions as ModelDefaultDropdownControlOptions,
)
from quicksight_gen.common.models import (
    DefaultFilterControlConfiguration as ModelDefaultFilterControlConfiguration,
)
from quicksight_gen.common.models import (
    DefaultFilterControlOptions as ModelDefaultFilterControlOptions,
)
from quicksight_gen.common.models import (
    DefaultSliderControlOptions as ModelDefaultSliderControlOptions,
)
from quicksight_gen.common.models import FilterGroup as ModelFilterGroup
from quicksight_gen.common.models import NumericRangeFilter as ModelNumericRangeFilter
from quicksight_gen.common.models import TimeRangeFilter as ModelTimeRangeFilter

from quicksight_gen.common.tree._helpers import (
    AUTO,
    AutoResolved,
    TimeGranularity,
    _AutoSentinel,
)
from quicksight_gen.common.tree.calc_fields import (
    CalcField,
    ColumnRef,
    calc_field_in,
    resolve_column,
)
from quicksight_gen.common.tree.datasets import Dataset
from quicksight_gen.common.tree.parameters import ParameterDeclLike
from quicksight_gen.common.tree.visuals import VisualLike

if TYPE_CHECKING:
    from quicksight_gen.common.tree.structure import Sheet


# ---------------------------------------------------------------------------
# FilterLike Protocol — the type FilterGroup.filters accepts.
# ---------------------------------------------------------------------------

@runtime_checkable
class FilterLike(Protocol):
    """Structural type for tree-level filter nodes.

    Each typed wrapper (``CategoryFilter`` / ``NumericRangeFilter`` /
    ``TimeRangeFilter``) satisfies this Protocol — exposes a
    ``filter_id``, the underlying ``dataset`` (object ref), and emits
    a ``models.Filter``. The ``dataset`` field participates in the
    L.1.7 dependency-graph walk.

    ``filter_id`` is ``str | None`` because typed wrappers default to
    None and let ``App.resolve_auto_ids`` fill it. ``calc_field()``
    returns the CalcField the filter references (or None if it points
    at a real column) — used by the dependency-graph walk and by
    FilterControl wrappers that need the filter_id post-resolve.
    """
    dataset: Dataset
    filter_id: str | AutoResolved

    def emit(self) -> Filter: ...

    def calc_field(self) -> CalcField | None: ...


# ---------------------------------------------------------------------------
# Typed Filter wrappers
# ---------------------------------------------------------------------------

CategoryMatchOperator = Literal[
    "CONTAINS", "EQUALS", "DOES_NOT_EQUAL", "STARTS_WITH",
]
NullOption = Literal["NON_NULLS_ONLY", "ALL_VALUES", "NULLS_ONLY"]
SelectAllOptions = Literal["FILTER_ALL_VALUES"]


# ---------------------------------------------------------------------------
# DefaultFilterControl wrappers — typed default-widget specs for use on
# typed Filter wrappers. Multi-sheet filters need this so cross-sheet
# controls have a widget config to inherit; single-sheet filters can
# omit it (the sheet's own FilterControls list provides the widget
# directly). The AWS rule is mechanical — wrong choice rejects at
# deploy.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DefaultDateTimePickerControl:
    """Inline default widget config for ``TimeRangeFilter``."""
    title: str
    type: Literal["SINGLE_VALUED", "DATE_RANGE"] = "DATE_RANGE"


@dataclass(frozen=True)
class DefaultDropdownControl:
    """Inline default widget config for ``CategoryFilter`` (or any
    list/parameter-driven filter)."""
    title: str
    type: Literal["MULTI_SELECT", "SINGLE_SELECT"] = "MULTI_SELECT"


@dataclass(frozen=True)
class DefaultSliderControl:
    """Inline default widget config for ``NumericRangeFilter``."""
    title: str
    minimum_value: float
    maximum_value: float
    step_size: float
    type: Literal["SINGLE_POINT", "RANGE"] = "SINGLE_POINT"


DefaultControl = (
    DefaultDateTimePickerControl
    | DefaultDropdownControl
    | DefaultSliderControl
)


def _emit_default_control(
    ctrl: DefaultControl | None,
) -> ModelDefaultFilterControlConfiguration | None:
    """Translate a typed ``DefaultControl`` to the underlying
    ``DefaultFilterControlConfiguration`` model, or ``None`` for
    single-sheet filters that don't carry a default."""
    if ctrl is None:
        return None
    options: ModelDefaultFilterControlOptions
    match ctrl:
        case DefaultDateTimePickerControl(title=_, type=t):
            options = ModelDefaultFilterControlOptions(
                DefaultDateTimePickerOptions=ModelDefaultDateTimePickerControlOptions(
                    Type=t,
                ),
            )
        case DefaultDropdownControl(title=_, type=t):
            options = ModelDefaultFilterControlOptions(
                DefaultDropdownOptions=ModelDefaultDropdownControlOptions(
                    Type=t,
                ),
            )
        case DefaultSliderControl():
            options = ModelDefaultFilterControlOptions(
                DefaultSliderOptions=ModelDefaultSliderControlOptions(
                    MaximumValue=ctrl.maximum_value,
                    MinimumValue=ctrl.minimum_value,
                    StepSize=ctrl.step_size,
                    Type=ctrl.type,
                ),
            )
    return ModelDefaultFilterControlConfiguration(
        Title=ctrl.title,
        ControlOptions=options,
    )


# Discriminated binding for CategoryFilter — exactly one of values vs
# parameter vs literal must be expressed, and the type system makes
# mixing them structurally impossible (a binding is one or the other;
# a CategoryFilter carries one binding).
@dataclass(frozen=True)
class _ValuesBinding:
    """Static-list binding — emits ``FilterListConfiguration``.
    Optional ``select_all_options`` adds the ``"FILTER_ALL_VALUES"``
    hint that tells QS to treat an empty selection as "all values"
    (the multi-select-with-empty-default-means-all pattern)."""
    values: list[str]
    select_all_options: SelectAllOptions | None = None


@dataclass(frozen=True)
class _ParameterBinding:
    """Parameter-bound binding — emits ``CustomFilterConfiguration``
    with ``ParameterName`` resolved from the parameter ref."""
    parameter: ParameterDeclLike


@dataclass(frozen=True)
class _LiteralBinding:
    """Single literal exact-match binding — emits
    ``CustomFilterConfiguration`` with a literal ``CategoryValue``.
    Used for the K.2 calc-field PASS pattern: a calc field returns
    ``"PASS"`` and the filter requires equality against that literal.
    The list-based ``FilterListConfiguration`` doesn't support EQUALS
    at the API (only CONTAINS / DOES_NOT_CONTAIN), so the
    Custom-with-literal shape is the only way to get an exact match
    against a single value."""
    value: str


CategoryBinding = _ValuesBinding | _ParameterBinding | _LiteralBinding


@dataclass(eq=False)
class CategoryFilter:
    """Filter on a categorical (string) column or calc field.

    ``dataset`` is a ``Dataset`` object ref (L.1.7 hard switch).
    ``column`` may name a real dataset column or an analysis-level
    calc field — both resolve to a ``ColumnIdentifier`` against the
    given dataset.

    Construct via the factory methods (L.1.22 — the discriminated
    binding makes the "neither/both set" bug class structurally
    impossible):

    - ``CategoryFilter.with_values(dataset, column, values, ...)`` —
      static list. Emits ``FilterListConfiguration`` with
      ``CategoryValues``. Use for the calc-field ``'yes'`` sentinel
      pattern or a hardcoded include-list.
    - ``CategoryFilter.with_parameter(dataset, column, parameter, ...)`` —
      parameter-bound. Emits ``CustomFilterConfiguration`` with
      ``ParameterName`` from the param ref. Use when a dropdown writes
      a single value into a string parameter and the filter narrows to
      it (e.g. Money Trail's chain root selector).

    ``null_option`` only surfaces in the parameter-bound emit (the
    list-based ``FilterListConfiguration`` doesn't carry it).
    """
    dataset: Dataset
    column: ColumnRef
    binding: CategoryBinding
    match_operator: CategoryMatchOperator = "CONTAINS"
    null_option: NullOption = "ALL_VALUES"
    default_control: DefaultDropdownControl | None = None
    filter_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "category"

    @classmethod
    def with_values(
        cls,
        *,
        dataset: Dataset,
        column: ColumnRef,
        values: list[str],
        match_operator: CategoryMatchOperator = "CONTAINS",
        null_option: NullOption = "ALL_VALUES",
        select_all_options: SelectAllOptions | None = None,
        default_control: DefaultDropdownControl | None = None,
        filter_id: str | AutoResolved = AUTO,
    ) -> "CategoryFilter":
        """Static-list category filter — ``CategoryValues`` is the literal
        list of allowed values. Pass ``values=[]`` plus
        ``select_all_options="FILTER_ALL_VALUES"`` for the
        multi-select-with-all-default pattern: an empty values list
        means "every distinct column value is selected at runtime"."""
        return cls(
            dataset=dataset, column=column,
            binding=_ValuesBinding(
                values=values, select_all_options=select_all_options,
            ),
            match_operator=match_operator,
            null_option=null_option,
            default_control=default_control,
            filter_id=filter_id,
        )

    @classmethod
    def with_parameter(
        cls,
        *,
        dataset: Dataset,
        column: ColumnRef,
        parameter: ParameterDeclLike,
        match_operator: CategoryMatchOperator = "EQUALS",
        null_option: NullOption = "ALL_VALUES",
        default_control: DefaultDropdownControl | None = None,
        filter_id: str | AutoResolved = AUTO,
    ) -> "CategoryFilter":
        """Parameter-bound category filter — ``ParameterName`` is read
        from the parameter ref at emit time. Default ``match_operator``
        is ``EQUALS`` since dropdown-driven parameters typically write a
        single value."""
        return cls(
            dataset=dataset, column=column,
            binding=_ParameterBinding(parameter=parameter),
            match_operator=match_operator,
            null_option=null_option,
            default_control=default_control,
            filter_id=filter_id,
        )

    @classmethod
    def with_literal(
        cls,
        *,
        dataset: Dataset,
        column: ColumnRef,
        value: str,
        match_operator: CategoryMatchOperator = "EQUALS",
        null_option: NullOption = "NON_NULLS_ONLY",
        default_control: DefaultDropdownControl | None = None,
        filter_id: str | AutoResolved = AUTO,
    ) -> "CategoryFilter":
        """Single-literal exact-match category filter — emits
        ``CustomFilterConfiguration`` with a literal ``CategoryValue``.
        The list-based ``FilterListConfiguration`` rejects EQUALS at the
        API (only CONTAINS / DOES_NOT_CONTAIN), so this is the only
        shape that supports an exact-equality test against a single
        value. Used for the K.2 calc-field PASS pattern: a calc field
        returns ``"PASS"`` and the filter requires equality against
        that literal."""
        return cls(
            dataset=dataset, column=column,
            binding=_LiteralBinding(value=value),
            match_operator=match_operator,
            null_option=null_option,
            default_control=default_control,
            filter_id=filter_id,
        )

    def calc_field(self) -> CalcField | None:
        """The CalcField this filter references, or None if it points
        at a real dataset column."""
        return calc_field_in(self.column)

    def emit(self) -> Filter:
        assert not isinstance(self.filter_id, _AutoSentinel), (
            "filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        match self.binding:
            case _ParameterBinding(parameter=parameter):
                configuration = CategoryFilterConfiguration(
                    CustomFilterConfiguration={
                        "MatchOperator": self.match_operator,
                        "ParameterName": parameter.name,
                        "NullOption": self.null_option,
                    },
                )
            case _LiteralBinding(value=value):
                configuration = CategoryFilterConfiguration(
                    CustomFilterConfiguration={
                        "MatchOperator": self.match_operator,
                        "CategoryValue": value,
                        "NullOption": self.null_option,
                    },
                )
            case _ValuesBinding(values=values, select_all_options=sa_opts):
                list_config: dict[str, object] = {
                    "MatchOperator": self.match_operator,
                }
                if values:
                    list_config["CategoryValues"] = values
                if sa_opts is not None:
                    list_config["SelectAllOptions"] = sa_opts
                # Empty values + no select-all flag = the prior shape.
                if not values and sa_opts is None:
                    list_config["CategoryValues"] = values
                configuration = CategoryFilterConfiguration(
                    FilterListConfiguration=list_config,
                )
        return Filter(
            CategoryFilter=ModelCategoryFilter(
                FilterId=self.filter_id,
                Column=ColumnIdentifier(
                    DataSetIdentifier=self.dataset.identifier,
                    ColumnName=resolve_column(self.column),
                ),
                Configuration=configuration,
                DefaultFilterControlConfiguration=_emit_default_control(
                    self.default_control,
                ),
            ),
        )


# Discriminated bound for NumericRangeFilter — each Bound variant carries
# exactly one piece of data (a static value or a parameter ref), so the
# "both static_value and parameter set" bug class is structurally gone.
@dataclass(frozen=True)
class StaticBound:
    """A literal numeric bound — emits ``StaticValue`` in the range filter."""
    value: float


@dataclass(frozen=True)
class ParameterBound:
    """A parameter-driven bound — emits ``Parameter`` (resolved from
    ``parameter.name``) in the range filter."""
    parameter: ParameterDeclLike


Bound = StaticBound | ParameterBound


def _emit_bound(bound: Bound | None) -> NumericRangeFilterValue | None:
    """Emit a Bound variant to ``NumericRangeFilterValue``, or None."""
    match bound:
        case None:
            return None
        case StaticBound(value=value):
            return NumericRangeFilterValue(StaticValue=value)
        case ParameterBound(parameter=parameter):
            return NumericRangeFilterValue(Parameter=parameter.name)


def _bound_parameter(bound: Bound | None) -> ParameterDeclLike | None:
    """Pull a ParameterDeclLike out of a Bound, or None."""
    match bound:
        case ParameterBound(parameter=parameter):
            return parameter
        case _:
            return None


@dataclass(eq=False)
class NumericRangeFilter:
    """Filter on a numeric column. Range bounds are typed ``Bound``
    variants — ``StaticBound(value)`` for a literal, ``ParameterBound(
    parameter)`` for a parameter-driven bound. The parameter-binding
    object ref catches "bound to a parameter that doesn't exist" at
    the wiring site (the type checker resolves ``param.name``).

    L.1.22 — the discriminated ``Bound`` union makes the "both
    static_value and parameter set" bug class structurally impossible:
    a ``StaticBound`` carries a value but no parameter, and a
    ``ParameterBound`` carries a parameter but no value. Each side
    (min / max) is at most one ``Bound``.
    """
    dataset: Dataset
    column: ColumnRef
    minimum: Bound | None = None
    maximum: Bound | None = None
    null_option: NullOption = "NON_NULLS_ONLY"
    include_minimum: bool | None = None
    include_maximum: bool | None = None
    default_control: DefaultSliderControl | None = None
    filter_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "numeric"

    @property
    def minimum_parameter(self) -> ParameterDeclLike | None:
        """The parameter ref the minimum bound is bound to, or None.
        Used by the parameter-references validator walk."""
        return _bound_parameter(self.minimum)

    @property
    def maximum_parameter(self) -> ParameterDeclLike | None:
        """The parameter ref the maximum bound is bound to, or None."""
        return _bound_parameter(self.maximum)

    def calc_field(self) -> CalcField | None:
        return calc_field_in(self.column)

    def emit(self) -> Filter:
        assert not isinstance(self.filter_id, _AutoSentinel), (
            "filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return Filter(
            NumericRangeFilter=ModelNumericRangeFilter(
                FilterId=self.filter_id,
                Column=ColumnIdentifier(
                    DataSetIdentifier=self.dataset.identifier,
                    ColumnName=resolve_column(self.column),
                ),
                NullOption=self.null_option,
                RangeMinimum=_emit_bound(self.minimum),
                RangeMaximum=_emit_bound(self.maximum),
                IncludeMinimum=self.include_minimum,
                IncludeMaximum=self.include_maximum,
                DefaultFilterControlConfiguration=_emit_default_control(
                    self.default_control,
                ),
            ),
        )


@dataclass(eq=False)
class TimeRangeFilter:
    """Filter on a date / datetime column.

    ``dataset`` is a ``Dataset`` object ref (L.1.7 hard switch).
    ``column`` is a ``ColumnRef`` — a real column or a ``CalcField``.

    ``minimum`` and ``maximum`` are passthrough dicts for now (the
    existing usage takes a variety of shapes — RollingDate, StaticValue,
    Parameter — and lifting all of them under typed wrappers can wait
    for the L.2/L.3/L.4 ports to surface concrete needs).
    """
    dataset: Dataset
    column: ColumnRef
    minimum: dict[str, Any] | None = None
    maximum: dict[str, Any] | None = None
    null_option: NullOption = "NON_NULLS_ONLY"
    time_granularity: TimeGranularity | None = None
    include_minimum: bool | None = None
    include_maximum: bool | None = None
    default_control: DefaultDateTimePickerControl | None = None
    filter_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "time"

    def calc_field(self) -> CalcField | None:
        return calc_field_in(self.column)

    def emit(self) -> Filter:
        assert not isinstance(self.filter_id, _AutoSentinel), (
            "filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return Filter(
            TimeRangeFilter=ModelTimeRangeFilter(
                FilterId=self.filter_id,
                Column=ColumnIdentifier(
                    DataSetIdentifier=self.dataset.identifier,
                    ColumnName=resolve_column(self.column),
                ),
                NullOption=self.null_option,
                TimeGranularity=self.time_granularity,
                RangeMinimumValue=self.minimum,
                RangeMaximumValue=self.maximum,
                IncludeMinimum=self.include_minimum,
                IncludeMaximum=self.include_maximum,
                DefaultFilterControlConfiguration=_emit_default_control(
                    self.default_control,
                ),
            ),
        )


@dataclass(eq=False)
class TimeEqualityFilter:
    """Single-day equality filter on a date column.

    Used when paired with a SINGLE_VALUED date picker control —
    ``TimeRangeFilter`` renders broken in the QS UI when paired with
    a single-day picker; ``TimeEqualityFilter`` is the right shape
    for "show rows where the date column equals one specific day".

    ``parameter`` (a typed ``DateTimeParam`` ref) is the only binding
    mode currently exposed (the AR Daily Statement use case). Extend
    with ``rolling_date`` / ``static_value`` factories when other apps
    need them.
    """
    dataset: Dataset
    column: ColumnRef
    parameter: ParameterDeclLike
    time_granularity: TimeGranularity | None = None
    default_control: DefaultDateTimePickerControl | None = None
    filter_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "time_equality"

    def calc_field(self) -> CalcField | None:
        return calc_field_in(self.column)

    def emit(self) -> Filter:
        assert not isinstance(self.filter_id, _AutoSentinel), (
            "filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return Filter(
            TimeEqualityFilter=ModelTimeEqualityFilter(
                FilterId=self.filter_id,
                Column=ColumnIdentifier(
                    DataSetIdentifier=self.dataset.identifier,
                    ColumnName=resolve_column(self.column),
                ),
                ParameterName=self.parameter.name,
                TimeGranularity=self.time_granularity,
                DefaultFilterControlConfiguration=_emit_default_control(
                    self.default_control,
                ),
            ),
        )


@dataclass(eq=False)
class FilterGroup:
    """Tree node for one analysis-level filter group.

    Construct with ``FilterGroup(filter_group_id=..., filters=[...])``,
    then attach scope by chaining ``.scope_visuals(sheet, [v1, v2])``
    or ``.scope_sheet(sheet)``. Both call methods validate immediately:

    - ``scope_visuals`` raises if any visual isn't on the given sheet
      (catches the wrong-sheet bug at the call site).
    - ``scope_sheet`` is the all-visuals-on-sheet shortcut.

    Multiple scope entries are allowed — the same FilterGroup can
    apply to (visual subset on sheet A) plus (all visuals on sheet B).
    Each entry emits its own ``SheetVisualScopingConfiguration``.

    ``filters`` takes a list of typed ``FilterLike`` wrappers
    (``CategoryFilter`` / ``NumericRangeFilter`` / ``TimeRangeFilter``
    above). Each wrapper's ``emit()`` returns a ``models.Filter`` at
    emission time. Parameter-bound filters (NumericRangeFilter with
    ``minimum_parameter`` / ``maximum_parameter``) carry object refs
    to ``ParameterDeclLike`` nodes — the type checker catches
    "filter bound to undeclared parameter" at the wiring site.

    ``filter_group_id`` is optional (L.1.8.5 auto-ID). When omitted,
    the App's tree walker assigns ``fg-{n}`` at emit time based on
    the FilterGroup's index in the analysis's filter group list.
    """
    filters: list[FilterLike]
    cross_dataset: Literal["SINGLE_DATASET", "ALL_DATASETS"] = "SINGLE_DATASET"
    enabled: bool = True
    filter_group_id: FilterGroupId | AutoResolved = AUTO
    _scope_entries: list[tuple["Sheet", list[VisualLike] | None]] = field(
        default_factory=list[tuple["Sheet", list[VisualLike] | None]],
        init=False, repr=False,
    )

    def scope_visuals(
        self, sheet: "Sheet", visuals: list[VisualLike],
    ) -> FilterGroup:
        """Scope this filter to specific visuals on a sheet.

        Construction-time check: every visual must already be registered
        on the given sheet via ``sheet.add_visual()``. Cross-sheet
        wiring is the bug class this catches — without the check, a
        scope mixing visuals from sheet A with sheet B's identifier
        emits a SheetVisualScopingConfiguration that silently drops
        the off-sheet visual at deploy time.
        """
        for v in visuals:
            if v not in sheet.visuals:
                raise ValueError(
                    f"Visual {v.visual_id!r} isn't registered on sheet "
                    f"{sheet.sheet_id!r} — register it via "
                    f"sheet.add_visual() before scoping a FilterGroup to it."
                )
        self._scope_entries.append((sheet, list(visuals)))
        return self

    def datasets(self) -> set[Dataset]:
        """Datasets this group's filters reference (object refs)."""
        return {f.dataset for f in self.filters}

    def calc_fields(self) -> set[CalcField]:
        """CalcFields this group's filters reference."""
        deps: set[CalcField] = set()
        for f in self.filters:
            if (cf := f.calc_field()) is not None:
                deps.add(cf)
        return deps

    def scope_sheet(self, sheet: "Sheet") -> FilterGroup:
        """Scope this filter to ALL visuals on a sheet.

        Equivalent to the existing ``_selected_sheets_scope([sheet_id])``
        helper — emits ``Scope=ALL_VISUALS`` on the sheet's
        SheetVisualScopingConfiguration, no per-visual list.
        """
        self._scope_entries.append((sheet, None))
        return self

    def emit(self) -> ModelFilterGroup:
        assert not isinstance(self.filter_group_id, _AutoSentinel), (
            "filter_group_id wasn't resolved — App.resolve_auto_ids() "
            "must run before FilterGroup.emit()."
        )
        if not self._scope_entries:
            raise ValueError(
                f"FilterGroup {self.filter_group_id!r} has no scope — "
                f"call scope_visuals() or scope_sheet() before emitting."
            )
        configs: list[SheetVisualScopingConfiguration] = []
        for sheet, visuals in self._scope_entries:
            if visuals is None:
                configs.append(SheetVisualScopingConfiguration(
                    SheetId=sheet.sheet_id,
                    Scope=SheetVisualScopingConfiguration.ALL_VISUALS,
                ))
            else:
                # Visuals' visual_id is resolved by App.resolve_auto_ids
                # which runs before emit; the assert above guarantees
                # this code path only executes after resolution.
                visual_ids: list[str] = []
                for v in visuals:
                    assert not isinstance(v.visual_id, _AutoSentinel), (
                        "visual_id wasn't resolved — App.resolve_auto_ids() "
                        "must run before FilterGroup.emit()."
                    )
                    visual_ids.append(v.visual_id)
                configs.append(SheetVisualScopingConfiguration(
                    SheetId=sheet.sheet_id,
                    Scope=SheetVisualScopingConfiguration.SELECTED_VISUALS,
                    VisualIds=visual_ids,
                ))
        return ModelFilterGroup(
            FilterGroupId=self.filter_group_id,
            CrossDataset=(
                ModelFilterGroup.SINGLE_DATASET
                if self.cross_dataset == "SINGLE_DATASET"
                else ModelFilterGroup.ALL_DATASETS
            ),
            ScopeConfiguration=FilterScopeConfiguration(
                SelectedSheets=SelectedSheetsFilterScopeConfiguration(
                    SheetVisualScopingConfigurations=configs,
                ),
            ),
            Status=(
                ModelFilterGroup.ENABLED
                if self.enabled else ModelFilterGroup.DISABLED
            ),
            Filters=[f.emit() for f in self.filters],
        )
