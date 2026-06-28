"""Dataclasses for the dataset-parameter pushdown surface.

What survives here is the small set of dataclasses the per-app dataset
builders and App2's SQL executor still consume — the ``DatasetParameter``
family (string / integer / decimal / datetime variants plus their
default-value holders) and ``DateTimeDefaultValues``. The sprawling AWS
QuickSight emit graph (Theme / Analysis / Dashboard and the whole
Visual / Filter / Control / Layout tree, each of which serialized itself
to the QS API JSON) retired with the QS renderer in Phase DW — none of
that emit machinery survives in this codebase.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Dataset parameter models
# ---------------------------------------------------------------------------
# App2's _sql_executor reads these to resolve a `<<$paramName>>`
# placeholder's default when a URL omits that param; build_dataset
# registers them per visual_identifier.

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


# ---------------------------------------------------------------------------
# DateTime parameter default values
# ---------------------------------------------------------------------------
# Live via tree/date_view + tree/parameters + the l2ft app's date controls.
# The DateTimeParameterDeclaration that consumed it retired with the QS
# emit graph (DW.8.1.c).

@dataclass
class DateTimeDefaultValues:
    StaticValues: list[str] | None = None
    DynamicValue: dict[str, Any] | None = None
    RollingDate: dict[str, Any] | None = None
