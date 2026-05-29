"""Typed ``ParameterDecl`` subtypes — one per ``models.py`` declaration variant.

``StringParam`` / ``IntegerParam`` / ``DateTimeParam`` map to the
three declaration types in ``models.py``. Each carries its own
``ParameterName`` at the constructor (single construction site);
controls and filter parameter bindings reference the parameter by
object ref.

Each variant optionally carries a list of ``(Dataset, str)`` tuples in
``mapped_dataset_params``. Each tuple binds the analysis-level
parameter to a dataset-level parameter declared inside the
referenced Dataset's CustomSql (substituted via ``<<$paramName>>``
at QS query time). Use this to bridge an analysis param into one or
more parameterized datasets — the cascading-filter pattern that
M.3.10c established.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from recon_gen.common.ids import ParameterName
from recon_gen.common.models import (
    DateTimeDefaultValues,
    DateTimeParameterDeclaration,
    IntegerParameterDeclaration,
    MappedDataSetParameter,
    ParameterDeclaration,
    StringParameterDeclaration,
)
from recon_gen.common.tree._helpers import TimeGranularity

if TYPE_CHECKING:
    from recon_gen.common.tree.datasets import Dataset


# (Dataset object, dataset-parameter-name) pair. Both pieces are
# required so the binding stays object-typed (catches "param mapped to
# a dataset that doesn't actually exist on the App") at the wiring site.
DatasetParamMapping = tuple["Dataset", str]


def _emit_mappings(
    pairs: list[DatasetParamMapping] | None,
) -> list[MappedDataSetParameter] | None:
    """Translate a list of (Dataset, name) pairs into the AWS-shape
    list. Returns None when the input is empty or None so the emitted
    JSON omits the field cleanly."""
    if not pairs:
        return None
    return [
        MappedDataSetParameter(
            DataSetIdentifier=ds.identifier,
            DataSetParameterName=name,
        )
        for ds, name in pairs
    ]


@runtime_checkable
class ParameterDeclLike(Protocol):
    """Structural type for parameter declaration tree nodes."""
    name: ParameterName

    def emit(self) -> ParameterDeclaration: ...


@dataclass(eq=False)
class StringParam:
    """String-valued parameter declaration.

    Default values are passed as a list — single-valued parameters
    use ``[]`` for "no default" or ``["value"]`` for one default;
    multi-valued use ``["a", "b", "c"]``.

    ``mapped_dataset_params``: optional list of ``(Dataset, name)``
    pairs binding this analysis parameter to one or more dataset-level
    parameters substituted via ``<<$name>>`` in the dataset's
    CustomSql.

    ``value_when_unset``: optional string value QS substitutes when
    this parameter is "unset" at evaluation time. REQUIRED when this
    parameter feeds a ``CascadingControlConfiguration`` — the cascade
    machinery substitutes this value into its internal match
    expression; the QS UI default of NULL makes the expression
    invalid ("calculated field has invalid syntax" tooltip on the
    target control). Set to the same sentinel used for the dataset's
    default so cascade-without-pick is a no-op narrowing instead of
    a NULL comparison. See ``docs/reference/quicksight-quirks.md``.
    """
    name: ParameterName
    default: list[str] = field(default_factory=list[str])
    multi_valued: bool = False
    mapped_dataset_params: list[DatasetParamMapping] | None = None
    value_when_unset: str | None = None

    def emit(self) -> ParameterDeclaration:
        return ParameterDeclaration(
            StringParameterDeclaration=StringParameterDeclaration(
                ParameterValueType=(
                    "MULTI_VALUED" if self.multi_valued else "SINGLE_VALUED"
                ),
                Name=self.name,
                DefaultValues={"StaticValues": self.default},
                MappedDataSetParameters=_emit_mappings(
                    self.mapped_dataset_params,
                ),
                # QS API constraint: ValueWhenUnsetOption (NULL|
                # RECOMMENDED_VALUE) is mutually exclusive with
                # CustomValue. For a string custom value, emit
                # ``CustomValue`` alone — no ValueWhenUnsetOption.
                ValueWhenUnset=(
                    {"CustomValue": self.value_when_unset}
                    if self.value_when_unset is not None else None
                ),
            ),
        )


@dataclass(eq=False)
class IntegerParam:
    """Integer-valued parameter declaration."""
    name: ParameterName
    default: list[int] = field(default_factory=list[int])
    multi_valued: bool = False
    mapped_dataset_params: list[DatasetParamMapping] | None = None

    def emit(self) -> ParameterDeclaration:
        return ParameterDeclaration(
            IntegerParameterDeclaration=IntegerParameterDeclaration(
                ParameterValueType=(
                    "MULTI_VALUED" if self.multi_valued else "SINGLE_VALUED"
                ),
                Name=self.name,
                DefaultValues={"StaticValues": self.default},
                MappedDataSetParameters=_emit_mappings(
                    self.mapped_dataset_params,
                ),
            ),
        )


@dataclass(eq=False)
class DateTimeParam:
    """DateTime parameter declaration.

    Pass ``time_granularity="DAY" | "HOUR" | "MINUTE" | …`` to bound
    the picker's resolution. ``default`` is **required** —
    DateTimeDefaultValues with one of StaticValues / DynamicValue /
    RollingDate populated. Common rolling default for "today":
    ``DateTimeDefaultValues(RollingDate={"Expression":
    "truncDate('DD', now())"})``.

    Why default is required (M.4.4.10d): without one, QS UI's date
    picker initializes with no value and crashes on editor open with
    "epochMilliseconds must be a number, you gave: null". Making
    the field type-required prevents the bug class from recurring at
    the wiring site.
    """
    name: ParameterName
    default: DateTimeDefaultValues
    time_granularity: TimeGranularity | None = None
    mapped_dataset_params: list[DatasetParamMapping] | None = None

    def emit(self) -> ParameterDeclaration:
        return ParameterDeclaration(
            DateTimeParameterDeclaration=DateTimeParameterDeclaration(
                Name=self.name,
                TimeGranularity=self.time_granularity,
                DefaultValues=self.default,
                MappedDataSetParameters=_emit_mappings(
                    self.mapped_dataset_params,
                ),
            ),
        )
