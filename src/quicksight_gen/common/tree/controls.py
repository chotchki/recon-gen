"""Typed Filter + Parameter control wrappers (L.1.9).

Each control binds to a typed source by object reference: parameter
controls take a ``ParameterDeclLike`` (the parameter declaration node
they read/write); filter controls take a ``FilterLike`` (the inner
filter their UI drives). At emit time the wrapper reads
``parameter.name`` or ``filter.filter_id`` to populate the underlying
``models.SourceParameterName`` / ``SourceFilterId``.

Naming convention: same as L.1.6 — tree wrappers use a clean,
unsuffixed name that doesn't collide with the underlying
``models.*Control`` classes (``Parameter*Control``, ``Filter*Control``).
User code reads:

    from quicksight_gen.common.tree import (
        ParameterDropdown, ParameterSlider, FilterDropdown, ...,
    )

``LinkedValues`` / ``StaticValues`` typed wrappers replace the
existing dict-shaped ``SelectableValues`` argument — ``LinkedValues``
takes a typed ``Dataset`` ref + column name (catches "dropdown
populated from undeclared dataset" via the App's dependency graph
walk).

Auto-IDs (L.1.8.5 extension): ``control_id`` fields are Optional;
the App walker assigns position-indexed IDs at emit time
(``pc-{kind}-s{sheet_idx}-{control_idx}`` for parameter controls,
``fc-{kind}-s{sheet_idx}-{control_idx}`` for filter controls).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from quicksight_gen.common.models import (
    FilterControl,
    ParameterControl,
)
from quicksight_gen.common.models import (
    FilterCrossSheetControl as ModelFilterCrossSheetControl,
)
from quicksight_gen.common.models import (
    FilterDateTimePickerControl as ModelFilterDateTimePickerControl,
)
from quicksight_gen.common.models import (
    FilterDropDownControl as ModelFilterDropDownControl,
)
from quicksight_gen.common.models import (
    FilterSliderControl as ModelFilterSliderControl,
)
from quicksight_gen.common.models import (
    ParameterDateTimePickerControl as ModelParameterDateTimePickerControl,
)
from quicksight_gen.common.models import (
    ParameterDropDownControl as ModelParameterDropDownControl,
)
from quicksight_gen.common.models import (
    ParameterSliderControl as ModelParameterSliderControl,
)

from quicksight_gen.common.tree._helpers import AUTO, AutoResolved, _AutoSentinel
from quicksight_gen.common.tree.datasets import Column, Dataset
from quicksight_gen.common.tree.filters import FilterLike
from quicksight_gen.common.tree.parameters import ParameterDeclLike


# ---------------------------------------------------------------------------
# Selectable values wrappers — typed alternatives to the dict-shaped
# SelectableValues argument used by the dropdown controls.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StaticValues:
    """Restrict the dropdown to a fixed list of options."""
    values: list[str]

    def emit(self) -> dict[str, Any]:
        return {"Values": list(self.values)}


@dataclass(frozen=True)
class LinkedValues:
    """Auto-populate the dropdown's options from a dataset column.

    Construct via the factory methods (L.1.22 — the canonical fields
    are ``dataset`` + ``column_name``; the factories normalize the two
    legitimate construction forms into that pair, eliminating the
    dual-form ``__post_init__`` validation):

    - ``LinkedValues.from_column(ds["col"])`` — typed Column form. The
      Column carries its own dataset, so the factory derives ``dataset``
      from the Column. Preferred — the contract validates the column
      name at the wiring site.
    - ``LinkedValues.from_string(dataset=ds, column_name="col")`` —
      bare-string escape hatch for datasets without a registered
      ``DatasetContract``. Dataset must be passed explicitly.

    The Dataset participates in the L.1.7 dependency-graph walk via
    the control's ``datasets()`` method.
    """
    dataset: Dataset
    column_name: str

    @classmethod
    def from_column(cls, column: Column) -> "LinkedValues":
        """Linked values pulled from a typed Column. The Column's
        dataset is the source dataset."""
        return cls(dataset=column.dataset, column_name=column.name)

    @classmethod
    def from_string(
        cls, *, dataset: Dataset, column_name: str,
    ) -> "LinkedValues":
        """Linked values pulled from a bare-string column name on the
        explicitly-passed dataset. Use when the dataset has no
        registered ``DatasetContract``."""
        return cls(dataset=dataset, column_name=column_name)

    def emit(self) -> dict[str, Any]:
        return {
            "LinkToDataSetColumn": {
                "DataSetIdentifier": self.dataset.identifier,
                "ColumnName": self.column_name,
            },
        }


SelectableValues = StaticValues | LinkedValues


# ---------------------------------------------------------------------------
# Control protocols — Sheet.add_parameter_control + add_filter_control
# accept any node satisfying these structural types.
# ---------------------------------------------------------------------------

@runtime_checkable
class ParameterControlLike(Protocol):
    """Tree-level parameter control nodes.

    ``datasets()`` participates in the L.1.7 dependency-graph walk —
    controls with ``LinkedValues`` populate from a ``Dataset``, and
    that's a dep. Controls with static values return an empty set.
    """
    control_id: str | AutoResolved

    def emit(self) -> ParameterControl: ...

    def datasets(self) -> set[Dataset]: ...


@runtime_checkable
class FilterControlLike(Protocol):
    """Tree-level filter control nodes.

    ``datasets()`` participates in the L.1.7 dependency-graph walk —
    same shape as ``ParameterControlLike.datasets()``.
    """
    control_id: str | AutoResolved

    def emit(self) -> FilterControl: ...

    def datasets(self) -> set[Dataset]: ...


# ---------------------------------------------------------------------------
# Parameter controls
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class ParameterDropdown:
    """Dropdown control bound to a ``ParameterDeclLike`` parameter.

    ``parameter`` is the typed parameter declaration the control
    reads/writes — at emit time, the control's ``SourceParameterName``
    becomes ``parameter.name``. The type checker catches "control
    bound to a parameter that doesn't exist" at the wiring site.

    ``selectable_values`` accepts a ``StaticValues(["a", "b"])`` for a
    fixed option list or ``LinkedValues(dataset, column)`` for an
    auto-populated list. The ``LinkedValues.dataset`` ref participates
    in the App's dependency graph.

    ``hidden_select_all=True`` suppresses the "Select all" entry —
    needed for SINGLE_SELECT dropdowns where empty/All semantics don't
    apply (e.g. a Sankey anchor that needs exactly one value).

    ``cascade_source`` makes this dropdown depend on another dropdown:
    when ``cascade_source`` changes value, QS refreshes THIS dropdown's
    options. Required for cascading filters even when the source
    dataset's params are bridged via ``MappedDataSetParameters`` —
    QS won't refresh the dropdown widget without explicit UI-level
    cascade wiring (M.3.10c finding).
    """
    parameter: ParameterDeclLike
    title: str
    selectable_values: SelectableValues
    type: Literal["SINGLE_SELECT", "MULTI_SELECT"] = "SINGLE_SELECT"
    hidden_select_all: bool = False
    cascade_source: "ParameterDropdown | None" = None
    # Column-match for the cascade: when the source control's value
    # changes, QS filters THIS dropdown's source dataset to rows
    # where this column equals the source value, then re-distincts
    # the dropdown's options. Required when cascade_source is set.
    cascade_match_column: "Column | None" = None
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "dropdown"

    def datasets(self) -> set[Dataset]:
        """Datasets this control references (via LinkedValues if any)."""
        if isinstance(self.selectable_values, LinkedValues):
            ds = self.selectable_values.dataset
            return {ds}
        return set()

    def emit(self) -> ParameterControl:
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        display_options: dict[str, Any] | None = None
        if self.hidden_select_all:
            display_options = {
                "SelectAllOptions": {"Visibility": "HIDDEN"},
            }

        cascading_config = None
        if self.cascade_source is not None:
            from quicksight_gen.common.models import (
                CascadingControlConfiguration,
                CascadingControlSource,
                ColumnIdentifier,
            )
            assert not isinstance(self.cascade_source.control_id, _AutoSentinel), (
                "cascade_source's control_id wasn't resolved before this "
                "control's emit — auto-ID resolution must visit the source "
                "control first."
            )
            assert self.cascade_match_column is not None, (
                "cascade_source set without cascade_match_column — QS "
                "needs to know which column on this dropdown's dataset "
                "to filter by the source control's selected value."
            )
            cascading_config = CascadingControlConfiguration(
                SourceControls=[CascadingControlSource(
                    SourceSheetControlId=self.cascade_source.control_id,
                    ColumnToMatch=ColumnIdentifier(
                        DataSetIdentifier=self.cascade_match_column.dataset.identifier,
                        ColumnName=self.cascade_match_column.name,
                    ),
                )],
            )

        return ParameterControl(
            Dropdown=ModelParameterDropDownControl(
                ParameterControlId=self.control_id,
                Title=self.title,
                SourceParameterName=self.parameter.name,
                Type=self.type,
                SelectableValues=self.selectable_values.emit(),
                DisplayOptions=display_options,
                CascadingControlConfiguration=cascading_config,
            ),
        )


@dataclass(eq=False)
class ParameterSlider:
    """Slider control bound to a numeric parameter."""
    parameter: ParameterDeclLike
    title: str
    minimum_value: float
    maximum_value: float
    step_size: float
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "slider"

    def datasets(self) -> set[Dataset]:
        return set()

    def emit(self) -> ParameterControl:
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return ParameterControl(
            Slider=ModelParameterSliderControl(
                ParameterControlId=self.control_id,
                Title=self.title,
                SourceParameterName=self.parameter.name,
                MinimumValue=self.minimum_value,
                MaximumValue=self.maximum_value,
                StepSize=self.step_size,
            ),
        )


@dataclass(eq=False)
class ParameterDateTimePicker:
    """Date/time picker control bound to a DateTime parameter."""
    parameter: ParameterDeclLike
    title: str
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "datetime"

    def datasets(self) -> set[Dataset]:
        return set()

    def emit(self) -> ParameterControl:
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return ParameterControl(
            DateTimePicker=ModelParameterDateTimePickerControl(
                ParameterControlId=self.control_id,
                Title=self.title,
                SourceParameterName=self.parameter.name,
            ),
        )


@dataclass(eq=False)
class ParameterTextField:
    """Free-text input control bound to a string parameter.

    Right shape when the parameter's option universe is unbounded /
    unknown at deploy time, or when the LinkedValues / StaticValues
    paths are unavailable. The analyst types a value; QS writes it to
    the bound parameter verbatim. No sample-values fetch — sidesteps
    the X.1.b ``Sample values not found`` failure mode entirely.
    """
    parameter: ParameterDeclLike
    title: str
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "text"

    def datasets(self) -> set[Dataset]:
        return set()

    def emit(self) -> ParameterControl:
        from quicksight_gen.common.models import ParameterTextFieldControl
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return ParameterControl(
            TextField=ParameterTextFieldControl(
                ParameterControlId=self.control_id,
                Title=self.title,
                SourceParameterName=self.parameter.name,
            ),
        )


# ---------------------------------------------------------------------------
# Filter controls
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class FilterDropdown:
    """Dropdown control bound to an inner filter (``CategoryFilter``).

    ``filter`` is the typed inner filter the dropdown drives — at
    emit time, the control's ``SourceFilterId`` becomes
    ``filter.filter_id``. The filter must be inside a ``FilterGroup``
    that's been registered on the analysis.
    """
    filter: FilterLike
    title: str
    type: Literal["SINGLE_SELECT", "MULTI_SELECT"] = "MULTI_SELECT"
    selectable_values: SelectableValues | None = None
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "dropdown"

    def datasets(self) -> set[Dataset]:
        if isinstance(self.selectable_values, LinkedValues):
            ds = self.selectable_values.dataset
            assert ds is not None  # LinkedValues.__post_init__ guarantees
            return {ds}
        return set()

    def emit(self) -> FilterControl:
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        assert not isinstance(self.filter.filter_id, _AutoSentinel), (
            "inner filter's filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return FilterControl(
            Dropdown=ModelFilterDropDownControl(
                FilterControlId=self.control_id,
                Title=self.title,
                SourceFilterId=self.filter.filter_id,
                Type=self.type,
                SelectableValues=(
                    self.selectable_values.emit()
                    if self.selectable_values is not None else None
                ),
            ),
        )


@dataclass(eq=False)
class FilterSlider:
    """Slider control bound to a NumericRangeFilter."""
    filter: FilterLike
    title: str
    minimum_value: float
    maximum_value: float
    step_size: float
    type: Literal["SINGLE_POINT", "RANGE"] = "RANGE"
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "slider"

    def datasets(self) -> set[Dataset]:
        return set()

    def emit(self) -> FilterControl:
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        assert not isinstance(self.filter.filter_id, _AutoSentinel), (
            "inner filter's filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return FilterControl(
            Slider=ModelFilterSliderControl(
                FilterControlId=self.control_id,
                Title=self.title,
                SourceFilterId=self.filter.filter_id,
                MinimumValue=self.minimum_value,
                MaximumValue=self.maximum_value,
                StepSize=self.step_size,
                Type=self.type,
            ),
        )


@dataclass(eq=False)
class FilterDateTimePicker:
    """Date/time picker control bound to a TimeRangeFilter."""
    filter: FilterLike
    title: str
    type: Literal["SINGLE_VALUED", "DATE_RANGE"] = "DATE_RANGE"
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "datetime"

    def datasets(self) -> set[Dataset]:
        return set()

    def emit(self) -> FilterControl:
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        assert not isinstance(self.filter.filter_id, _AutoSentinel), (
            "inner filter's filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return FilterControl(
            DateTimePicker=ModelFilterDateTimePickerControl(
                FilterControlId=self.control_id,
                Title=self.title,
                SourceFilterId=self.filter.filter_id,
                Type=self.type,
            ),
        )


@dataclass(eq=False)
class FilterCrossSheet:
    """Cross-sheet filter control — surfaces the filter on multiple
    sheets via the same bound filter.

    No title; the Cross-Sheet control inherits its UI from the
    underlying filter's primary control.
    """
    filter: FilterLike
    control_id: str | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "crosssheet"

    def datasets(self) -> set[Dataset]:
        return set()

    def emit(self) -> FilterControl:
        assert not isinstance(self.control_id, _AutoSentinel), (
            "control_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        assert not isinstance(self.filter.filter_id, _AutoSentinel), (
            "inner filter's filter_id wasn't resolved — App.resolve_auto_ids() must run."
        )
        return FilterControl(
            CrossSheet=ModelFilterCrossSheetControl(
                FilterControlId=self.control_id,
                SourceFilterId=self.filter.filter_id,
            ),
        )
