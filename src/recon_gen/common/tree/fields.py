"""Field-well leaf nodes — ``Dim`` + ``Measure`` typed wrappers.

Every visual's field wells contain a mix of ``DimensionField`` and
``MeasureField`` entries (source / target columns, group-by fields,
aggregated values). These tree nodes wrap them with typed factories
(``Dim.date(...)``, ``Measure.sum(...)``) so construction-time typing
drives what the visual gets, rather than hand-wiring the underlying
models every time.

Auto field_id (L.1.16): both ``Dim`` and ``Measure`` accept an
optional ``field_id`` keyword. When omitted, the App walker assigns
``f-{visual_kind}-s{sheet_idx}-v{visual_idx}-{role}{slot_idx}`` at
emit time. Authors typically pass ``Dim(ds, "column_name")`` and
reference the leaf via Python variable for sort / drill plumbing
(both accept ``Dim`` / ``Measure`` object refs in addition to bare
field-id strings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from recon_gen.common.models import (
    CategoricalDimensionField,
    CategoricalMeasureField,
    ColumnIdentifier,
    CurrencyDisplayFormatConfiguration,
    DateDimensionField,
    DecimalPlacesConfiguration,
    DimensionField,
    MeasureField,
    NumberFormatConfiguration,
    NumericalAggregationFunction,
    NumericalDimensionField,
    NumericalMeasureField,
    NumericFormatConfiguration,
    SeparatorConfiguration,
    ThousandSeparatorOptions,
)
from recon_gen.common.tree._helpers import (
    AUTO,
    AutoResolved,
    TimeGranularity,
    _AutoSentinel,
)
from recon_gen.common.tree.calc_fields import (
    CalcField,
    ColumnRef,
    calc_field_in,
    resolve_column,
)
from recon_gen.common.tree.datasets import Dataset


DimKind = Literal["categorical", "date", "numerical"]


# BL.1 — kind="count" Measures emit ``NumericalMeasureField(SUM)`` over
# an auto-registered CalcField with ``Expression="1"`` (one per
# referenced ``Dataset``). The convention name carries the dataset
# identifier so two datasets in the same Analysis don't collide on the
# global ``Analysis.calc_fields`` name registry. ``App.resolve_auto_ids``
# is the registrar; ``Measure.emit`` reads through to the convention
# name.
#
# Why this shape rather than ``CategoricalMeasureField(COUNT)``: QS's
# CategoricalMeasureField COUNT silently renders DISTINCT when the
# column also appears as a Dim elsewhere on the same visual / sheet
# (BL.1 bug). NumericalMeasureField(SUM) over a literal-1 CalcField
# is a pure row count with no quirky distinct behavior. App2's
# ``_visual_sql`` translates ``kind="count"`` → ``SUM(1)``; the two
# renderers stay symmetric (both compute SUM(1) over the dataset).
ROW_ONE_CALC_PREFIX = "_row_one_"


def row_one_calc_name(dataset: Dataset) -> str:
    """Convention name for the literal-1 CalcField backing
    ``Measure.kind == "count"`` row-count semantics on ``dataset``.

    Returns ``"_row_one_<sanitized-dataset-id>"``. Dashes in the
    dataset identifier are replaced with underscores so the name is
    QS-safe (QS calc field names accept underscores; dashes are
    allowed too but underscores stay closer to convention).
    """
    return f"{ROW_ONE_CALC_PREFIX}{dataset.identifier.replace('-', '_')}"


@dataclass(eq=False)
class Dim:
    """One dimension field-well entry — typed wrapper that emits a
    ``DimensionField`` of the appropriate kind.

    ``dataset`` is a ``Dataset`` object ref — the locked L.1.7 hard
    switch. The dataset must be registered on the parent ``App`` (via
    ``app.add_dataset()``) for the analysis to emit.

    ``column`` accepts either a bare ``str`` (a real column on the
    dataset) or a ``CalcField`` object ref (an analysis-level
    calculated field). The CalcField ref carries the calc-field
    identity through the type checker — the App's emit-time
    validation catches references to unregistered calc fields.

    Default kind is ``categorical`` (the most common); use the
    ``date()`` / ``numerical()`` classmethods for the other variants.

    ``field_id`` is keyword-only and Optional (L.1.16 auto-ID). When
    omitted, the App walker assigns one based on the leaf's tree
    position. Pass an explicit ``field_id="..."`` only when external
    consumers (browser e2e selectors, etc.) need a stable id —
    cross-reference plumbing (sort_by, drill writes) accepts the
    leaf object directly.

    Identity-keyed (``eq=False``) so the auto-id resolver can mutate
    the field_id at emit time. Dim leaves stay hashable via the
    default object identity hash, which lets the dependency graph
    set-membership check work.
    """
    dataset: Dataset
    column: ColumnRef
    kind: DimKind = "categorical"
    date_granularity: TimeGranularity | None = field(default=None, kw_only=True)
    field_id: str | AutoResolved = field(default=AUTO, kw_only=True)
    # Q.1.a.7 — currency=True emits a USD CurrencyDisplayFormatConfiguration
    # on the underlying NumericalDimensionField (row-level money columns
    # in tables typically use Dim.numerical, not Measure.sum, since they
    # show the raw value rather than an aggregate). Only valid for
    # ``kind="numerical"`` — money never makes sense as a categorical
    # axis or a date axis. Asserted at emit time.
    currency: bool = field(default=False, kw_only=True)

    @classmethod
    def date(
        cls, dataset: Dataset, column: ColumnRef,
        *,
        date_granularity: TimeGranularity | None = "DAY",
        field_id: str | AutoResolved = AUTO,
    ) -> Dim:
        """Date dimension. ``date_granularity`` defaults to ``"DAY"`` —
        QuickSight's most common bucket for daily series. Pass ``None``
        to omit the granularity (the renderer falls back to its default,
        which can shift bucketing on day-vs-month dashboards)."""
        return cls(
            dataset=dataset, column=column, kind="date",
            date_granularity=date_granularity, field_id=field_id,
        )

    @classmethod
    def numerical(
        cls, dataset: Dataset, column: ColumnRef,
        *, field_id: str | AutoResolved = AUTO, currency: bool = False,
    ) -> Dim:
        return cls(
            dataset=dataset, column=column, kind="numerical",
            field_id=field_id, currency=currency,
        )

    def calc_field(self) -> CalcField | None:
        """The CalcField this Dim references, or None if it points at
        a real dataset column. Used by the dependency-graph walk."""
        return calc_field_in(self.column)

    def emit(self) -> DimensionField:
        assert not isinstance(self.field_id, _AutoSentinel), (
            "field_id wasn't resolved — App.resolve_auto_ids() must run "
            "before Dim.emit()."
        )
        col = ColumnIdentifier(
            DataSetIdentifier=self.dataset.identifier,
            ColumnName=resolve_column(self.column),
        )
        if self.kind == "date":
            return DimensionField(
                DateDimensionField=DateDimensionField(
                    FieldId=self.field_id, Column=col,
                    DateGranularity=self.date_granularity,
                ),
            )
        if self.kind == "numerical":
            return DimensionField(
                NumericalDimensionField=NumericalDimensionField(
                    FieldId=self.field_id, Column=col,
                    FormatConfiguration=_USD_FORMAT if self.currency else None,
                ),
            )
        assert not self.currency, (
            f"Dim(currency=True) is only valid for kind='numerical', not "
            f"{self.kind!r} — money values aren't categorical or date axes."
        )
        return DimensionField(
            CategoricalDimensionField=CategoricalDimensionField(
                FieldId=self.field_id, Column=col,
            ),
        )

    def emit_unaggregated_field(self) -> dict[str, object]:
        """Emit the raw ``UnaggregatedField`` dict shape used inside
        ``TableUnaggregatedFieldWells.Values``. The model layer types
        that field as ``list[dict[str, Any]]`` rather than a typed
        union, so the tree emits it as a dict directly.

        Q.1.a.7 — When ``currency=True`` is set on a numerical Dim, the
        same USD ``FormatConfiguration`` that ``emit()`` wires onto a
        NumericalDimensionField is also folded into the unaggregated
        field shape so table cells render with "$" + thousands
        separator + 2 decimals. Without this, currency=True only took
        effect when the Dim was used as a chart axis or KPI value, not
        when it was used as a table column (the by-far common case).
        """
        assert not isinstance(self.field_id, _AutoSentinel), (
            "field_id wasn't resolved — App.resolve_auto_ids() must run "
            "before Dim.emit_unaggregated_field()."
        )
        out: dict[str, object] = {
            "FieldId": self.field_id,
            "Column": {
                "DataSetIdentifier": self.dataset.identifier,
                "ColumnName": resolve_column(self.column),
            },
        }
        if self.currency:
            assert self.kind == "numerical", (
                f"Dim(currency=True) is only valid for kind='numerical', "
                f"not {self.kind!r} — money values aren't categorical or "
                f"date axes."
            )
            from dataclasses import asdict
            from recon_gen.common.models import _strip_nones
            # UnaggregatedField.FormatConfiguration is a discriminated
            # union of String/Number/DateTime — pick the NumberFormatConfiguration
            # branch and place the existing _USD_FORMAT shape under it.
            # (NumericalMeasureField's FormatConfiguration drops the
            # discriminator since the field type is already known to be
            # numeric; the unaggregated field stays generic over the
            # column type and so needs the extra level.)
            out["FormatConfiguration"] = {
                "NumberFormatConfiguration": _strip_nones(asdict(_USD_FORMAT)),
            }
        return out


# Aggregation kinds split into "categorical" (COUNT, DISTINCT_COUNT —
# read off any column type) and "numerical" (SUM, MAX, MIN, AVERAGE —
# require a numeric column). The split mirrors the underlying
# ``CategoricalMeasureField`` vs ``NumericalMeasureField`` distinction.
MeasureKind = Literal[
    "sum", "max", "min", "average",          # → NumericalMeasureField
    "count", "distinct_count",               # → CategoricalMeasureField
]


_NUMERICAL_AGG = {
    "sum": "SUM", "max": "MAX", "min": "MIN", "average": "AVERAGE",
}
_CATEGORICAL_AGG = {
    "count": "COUNT", "distinct_count": "DISTINCT_COUNT",
}


@dataclass(eq=False)
class Measure:
    """One value field-well entry — typed wrapper that emits a
    ``MeasureField`` with the appropriate aggregation shape.

    ``dataset`` is a ``Dataset`` object ref (L.1.7 hard switch). The
    dataset must be registered on the parent ``App`` for the analysis
    to emit.

    ``field_id`` is keyword-only and Optional (L.1.16 auto-ID). When
    omitted, the App walker assigns one based on the leaf's tree
    position.

    Use the classmethod factories for ergonomic construction:
    ``Measure.sum(...)``, ``Measure.distinct_count(...)``, etc.
    Aggregation kind determines which underlying model class is
    emitted (numerical aggregations on numeric columns,
    categorical on count-style aggregations).
    """
    dataset: Dataset
    column: ColumnRef
    kind: MeasureKind
    field_id: str | AutoResolved = field(default=AUTO, kw_only=True)
    # Q.1.a — currency=True emits a USD CurrencyDisplayFormatConfiguration
    # on the underlying NumericalMeasureField (2 decimal places, comma
    # thousands separator, "$" prefix per QS's USD rendering). Only
    # valid for numerical aggregations (sum/max/min/average) — count /
    # distinct_count don't aggregate money. The emit-time assert below
    # catches the misuse loud rather than silently dropping the format.
    currency: bool = field(default=False, kw_only=True)
    # v11.22.1 cold-read finding #18 (2026-05-26) — when QS sees an
    # AVERAGE aggregation with no FormatConfiguration it renders 3
    # decimals by default ("2.000"). For count-of-things averages
    # (Avg Daily Volume = avg(transfer_count_per_day)) the right
    # rendering is an integer or 1-decimal. Setting decimals=N on a
    # non-currency Measure emits a NumberDisplayFormatConfiguration with
    # DecimalPlaces=N + comma thousands separator. Mutually exclusive
    # with currency=True (currency already pins 2 decimals).
    decimals: int | None = field(default=None, kw_only=True)

    @classmethod
    def sum(
        cls, dataset: Dataset, column: ColumnRef,
        *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        return cls(
            dataset=dataset, column=column, kind="sum",
            field_id=field_id, currency=currency, decimals=decimals,
        )

    @classmethod
    def max(
        cls, dataset: Dataset, column: ColumnRef,
        *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        return cls(
            dataset=dataset, column=column, kind="max",
            field_id=field_id, currency=currency, decimals=decimals,
        )

    @classmethod
    def min(
        cls, dataset: Dataset, column: ColumnRef,
        *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        return cls(
            dataset=dataset, column=column, kind="min",
            field_id=field_id, currency=currency, decimals=decimals,
        )

    @classmethod
    def average(
        cls, dataset: Dataset, column: ColumnRef,
        *, field_id: str | AutoResolved = AUTO, currency: bool = False,
        decimals: int | None = None,
    ) -> Measure:
        return cls(
            dataset=dataset, column=column, kind="average",
            field_id=field_id, currency=currency, decimals=decimals,
        )

    @classmethod
    def count(
        cls, dataset: Dataset, column: ColumnRef,
        *, field_id: str | AutoResolved = AUTO,
    ) -> Measure:
        return cls(dataset=dataset, column=column, kind="count", field_id=field_id)

    @classmethod
    def distinct_count(
        cls, dataset: Dataset, column: ColumnRef,
        *, field_id: str | AutoResolved = AUTO,
    ) -> Measure:
        return cls(
            dataset=dataset, column=column, kind="distinct_count",
            field_id=field_id,
        )

    def calc_field(self) -> CalcField | None:
        """The CalcField this Measure references, or None if it points
        at a real dataset column."""
        return calc_field_in(self.column)

    def emit(self) -> MeasureField:
        assert not isinstance(self.field_id, _AutoSentinel), (
            "field_id wasn't resolved — App.resolve_auto_ids() must run "
            "before Measure.emit()."
        )
        col = ColumnIdentifier(
            DataSetIdentifier=self.dataset.identifier,
            ColumnName=resolve_column(self.column),
        )
        if self.kind == "count":
            # BL.1 — read through to the literal-1 CalcField. The
            # CalcField itself is registered on the Analysis by
            # ``App.resolve_auto_ids`` (one per ``Dataset`` referenced
            # by a count Measure); here we just emit the
            # NumericalMeasureField(SUM) pointing at that CalcField's
            # convention name.
            assert not self.currency, (
                f"Measure(currency=True) is only valid for numerical "
                f"aggregations (sum/max/min/average), not "
                f"{self.kind!r} — count returns row counts, never money."
            )
            row_one_col = ColumnIdentifier(
                DataSetIdentifier=self.dataset.identifier,
                ColumnName=row_one_calc_name(self.dataset),
            )
            return MeasureField(
                NumericalMeasureField=NumericalMeasureField(
                    FieldId=self.field_id,
                    Column=row_one_col,
                    AggregationFunction=NumericalAggregationFunction(
                        SimpleNumericalAggregation="SUM",
                    ),
                ),
            )
        if self.kind in _CATEGORICAL_AGG:
            assert not self.currency, (
                f"Measure(currency=True) is only valid for numerical "
                f"aggregations (sum/max/min/average), not "
                f"{self.kind!r} — count/distinct_count return row "
                f"counts, never money."
            )
            return MeasureField(
                CategoricalMeasureField=CategoricalMeasureField(
                    FieldId=self.field_id,
                    Column=col,
                    AggregationFunction=_CATEGORICAL_AGG[self.kind],
                ),
            )
        assert not (self.currency and self.decimals is not None), (
            "Measure cannot set both currency=True and decimals=N — "
            "currency already pins 2 decimals via _USD_FORMAT. Drop "
            "decimals= or drop currency=True."
        )
        fmt: NumberFormatConfiguration | None
        if self.currency:
            fmt = _USD_FORMAT
        elif self.decimals is not None:
            fmt = _integer_format(self.decimals)
        else:
            fmt = None
        return MeasureField(
            NumericalMeasureField=NumericalMeasureField(
                FieldId=self.field_id,
                Column=col,
                AggregationFunction=NumericalAggregationFunction(
                    SimpleNumericalAggregation=_NUMERICAL_AGG[self.kind],
                ),
                FormatConfiguration=fmt,
            ),
        )


# USD currency format — the only supported currency for now (Q.1.a).
# Extracted as a module-level constant so identity equality holds across
# every currency=True Measure (callers can compare-via-`is` if they need
# to detect "this measure was format-tagged"). When a future phase adds
# multi-currency support, swap this for a per-instance lookup.
_USD_FORMAT = NumberFormatConfiguration(
    FormatConfiguration=NumericFormatConfiguration(
        CurrencyDisplayFormatConfiguration=CurrencyDisplayFormatConfiguration(
            Symbol="USD",
            DecimalPlacesConfiguration=DecimalPlacesConfiguration(DecimalPlaces=2),
            SeparatorConfiguration=SeparatorConfiguration(
                ThousandsSeparator=ThousandSeparatorOptions(
                    Symbol="COMMA", Visibility="VISIBLE",
                ),
            ),
        ),
    ),
)


# v11.22.1 cold-read finding #18 (2026-05-26) — per-Measure integer /
# fixed-decimal format. Constructed per (decimals,) so the resulting
# wire shape is stable across emits and JSON pin tests don't churn.
# NumberDisplayFormatConfiguration is the QS NumericFormatConfiguration
# branch for plain numbers (vs the Currency / Percentage branches).
def _integer_format(decimals: int) -> NumberFormatConfiguration:
    assert decimals >= 0, (
        f"Measure.decimals must be >= 0, got {decimals!r}"
    )
    return NumberFormatConfiguration(
        FormatConfiguration=NumericFormatConfiguration(
            NumberDisplayFormatConfiguration={
                "DecimalPlacesConfiguration": {"DecimalPlaces": decimals},
                "SeparatorConfiguration": {
                    "ThousandsSeparator": {
                        "Symbol": "COMMA",
                        "Visibility": "VISIBLE",
                    },
                },
            },
        ),
    )


# Type alias used everywhere a sort/drill plumbing slot accepts either
# a leaf object ref or a bare field_id string. Object refs are the
# preferred form (the tree resolves the field_id at emit time so
# auto-IDed leaves work without exposing the synthesized id).
FieldRef = Dim | Measure | str


def resolve_field_id(ref: FieldRef) -> str:
    """Read the resolved field_id off a Dim / Measure / bare string."""
    if isinstance(ref, str):
        return ref
    assert not isinstance(ref.field_id, _AutoSentinel), (
        "field_id wasn't resolved — App.resolve_auto_ids() must run "
        "before resolve_field_id."
    )
    return ref.field_id
