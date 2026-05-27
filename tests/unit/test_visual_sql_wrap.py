"""Unit tests for ``common.html._visual_sql.wrap_for_visual``.

Y.3.f.alt.3: snapshot the wrapper output to lock the dialect-portable
quoting invariant. App2's ``wrap_for_visual`` references the
dataset-side ``_oracle_lowercase_alias_wrapper``'s case-preserved
lowercase aliases. On Oracle, an unquoted ref case-folds to UPPERCASE
and fails to find the wrapper's quoted-lowercase alias (the m.5.d
``ORA-00904`` failure). Quoting (``"col"`` instead of ``col``) preserves
case on Oracle, matches lowercase-stored DDL on PG, is harmless on
SQLite (case-insensitive identifiers).
"""

from __future__ import annotations

from dataclasses import dataclass, field


from recon_gen.common.dataset_contract import ColumnSpec, DatasetContract, Storage
from recon_gen.common.html._visual_sql import wrap_for_visual


# BH.24.6 — wrap_for_visual's `contract` arg is now required by
# `_wants_cents_divide` whenever a currency=True Measure asks for
# the /100 divide. These tests synthesize a CENTS-storage stub
# contract for every column name they use in currency=True
# Measures; non-currency tests can pass `contract=None` (the
# `_wants_cents_divide` path doesn't fire without is_currency).
_CENTS_CONTRACT = DatasetContract(columns=[
    ColumnSpec(
        name="amount_money", type="INTEGER",
        currency=True, storage=Storage.CENTS,
    ),
    ColumnSpec(
        name="abs_drift", type="INTEGER",
        currency=True, storage=Storage.CENTS,
    ),
    ColumnSpec(
        name="hop_amount", type="INTEGER",
        currency=True, storage=Storage.CENTS,
    ),
    # Non-currency column for the count-currency=True nonsense test
    # — kind="count" never divides regardless of storage, but
    # _wants_cents_divide still gets called (and would raise on a
    # missing column), so declare it.
    ColumnSpec(
        name="account_id", type="STRING",
        # Not money, so storage irrelevant; default DOLLARS is fine.
    ),
])


@dataclass
class _StubColumn:
    name: str


@dataclass
class _StubDim:
    column: _StubColumn


@dataclass
class _StubMeasure:
    kind: str
    column: _StubColumn
    currency: bool = False


# wrap_for_visual dispatches on `type(visual).__name__` so the stub
# class names must match the real Visual subtype names (KPI, BarChart,
# LineChart, Sankey, Table) — duck typing on attributes alone isn't
# enough. We could import the real tree types instead but the stubs
# isolate the SQL-string wrap from the Visual constructor's validation.
@dataclass
class KPI:
    values: list[_StubMeasure]


@dataclass
class BarChart:
    category: list[_StubDim]
    values: list[_StubMeasure]
    colors: list[_StubDim] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]: colors list from QS theme dict walk


@dataclass
class LineChart:
    category: list[_StubDim]
    values: list[_StubMeasure]


@dataclass
class Sankey:
    source: _StubDim
    target: _StubDim
    weight: _StubMeasure


@dataclass
class Table:
    pass


_BASE = "SELECT * FROM probe_dataset"


class TestKPIWrap:
    def test_count_quotes_column_ref(self):
        kpi = KPI(values=[
            _StubMeasure(kind="count", column=_StubColumn("account_id")),
        ])
        wrapped = wrap_for_visual(_BASE, kpi)
        assert 'COUNT("account_id")' in wrapped
        assert "COUNT(account_id)" not in wrapped  # unquoted form gone

    def test_distinct_count_quotes_column_ref(self):
        kpi = KPI(values=[
            _StubMeasure(kind="distinct_count", column=_StubColumn("transfer_id")),
        ])
        wrapped = wrap_for_visual(_BASE, kpi)
        assert 'COUNT(DISTINCT "transfer_id")' in wrapped

    def test_sum_quotes_column_ref(self):
        kpi = KPI(values=[
            _StubMeasure(kind="sum", column=_StubColumn("amount")),
        ])
        wrapped = wrap_for_visual(_BASE, kpi)
        assert 'SUM("amount")' in wrapped


class TestBarChartWrap:
    def test_quotes_category_and_measure(self):
        bar = BarChart(
            category=[_StubDim(column=_StubColumn("transfer_type"))],
            values=[_StubMeasure(kind="sum", column=_StubColumn("amount"))],
        )
        wrapped = wrap_for_visual(_BASE, bar)
        assert '"transfer_type"' in wrapped
        assert 'SUM("amount")' in wrapped
        assert "GROUP BY" in wrapped
        # Unquoted forms gone
        assert " transfer_type FROM" not in wrapped
        assert " transfer_type GROUP" not in wrapped

    def test_projects_and_groups_colors_dim(self):
        # AO.R.2 — a BarChart with a `colors` (series) dim must project +
        # group it between category and value so the per-series breakdown
        # survives to shape_bar_chart (stacked / multi-series).
        bar = BarChart(
            category=[_StubDim(column=_StubColumn("posted_date"))],
            values=[_StubMeasure(kind="sum", column=_StubColumn("amount"))],
            colors=[_StubDim(column=_StubColumn("rail_name"))],
        )
        wrapped = wrap_for_visual(_BASE, bar)
        assert 'SELECT "posted_date", "rail_name", SUM("amount")' in wrapped
        assert 'GROUP BY "posted_date", "rail_name"' in wrapped


class TestLineChartWrap:
    def test_quotes_and_orders(self):
        line = LineChart(
            category=[_StubDim(column=_StubColumn("posted_date"))],
            values=[_StubMeasure(kind="sum", column=_StubColumn("amount"))],
        )
        wrapped = wrap_for_visual(_BASE, line)
        assert '"posted_date"' in wrapped
        assert 'SUM("amount")' in wrapped
        assert 'ORDER BY "posted_date"' in wrapped


class TestSankeyWrap:
    def test_quotes_source_target_weight(self):
        sankey = Sankey(
            source=_StubDim(column=_StubColumn("source_display")),
            target=_StubDim(column=_StubColumn("target_display")),
            weight=_StubMeasure(kind="sum", column=_StubColumn("hop_amount")),
        )
        wrapped = wrap_for_visual(_BASE, sankey)
        assert '"source_display"' in wrapped
        assert '"target_display"' in wrapped
        assert 'SUM("hop_amount")' in wrapped
        assert 'GROUP BY "source_display", "target_display"' in wrapped


class TestTableWrap:
    def test_pass_through_unwrapped(self):
        """Tables paginate client-side — no SQL wrap."""
        result = wrap_for_visual(_BASE, Table())
        assert result == _BASE


class TestCurrencyMeasureDividesByHundred:
    """AO.1.impl (Studio slice) — money columns are BIGINT cents per the
    AO.1 storage contract. A Measure with ``currency=True`` divides the
    aggregate by 100.0 so App2 renders dollars (``$75.00``) not cents
    (``$7,500.00``). Mirrors ``cents_to_dollars_sql`` at the same
    boundary, just inside the visual's aggregation wrap."""

    def test_currency_sum_divides_by_hundred(self):
        kpi = KPI(values=[
            _StubMeasure(
                kind="sum",
                column=_StubColumn("amount_money"),
                currency=True,
            ),
        ])
        wrapped = wrap_for_visual(_BASE, kpi, contract=_CENTS_CONTRACT)
        assert '(SUM("amount_money") / 100.0)' in wrapped

    def test_non_currency_sum_unchanged(self):
        """Existing non-money aggregates stay unchanged — no spurious
        divide on count of transfers, etc."""
        kpi = KPI(values=[
            _StubMeasure(kind="sum", column=_StubColumn("transfer_count")),
        ])
        wrapped = wrap_for_visual(_BASE, kpi)
        assert 'SUM("transfer_count")' in wrapped
        assert "/ 100.0" not in wrapped

    def test_currency_count_does_not_divide(self):
        """COUNT / DISTINCT_COUNT aren't sums of cents; the divide
        skips them even when the flag is set (defensive — a counting
        currency measure is nonsense but the wrap shouldn't corrupt
        it into ``COUNT(...) / 100.0``)."""
        kpi = KPI(values=[
            _StubMeasure(
                kind="count",
                column=_StubColumn("account_id"),
                currency=True,
            ),
        ])
        wrapped = wrap_for_visual(_BASE, kpi, contract=_CENTS_CONTRACT)
        assert 'COUNT("account_id")' in wrapped
        assert "/ 100.0" not in wrapped

    def test_currency_max_divides(self):
        """MAX / MIN / AVG of cents still need conversion to dollars."""
        kpi = KPI(values=[
            _StubMeasure(
                kind="max",
                column=_StubColumn("abs_drift"),
                currency=True,
            ),
        ])
        wrapped = wrap_for_visual(_BASE, kpi, contract=_CENTS_CONTRACT)
        assert '(MAX("abs_drift") / 100.0)' in wrapped

    def test_sankey_weight_currency_divides(self):
        """The Sankey weight measure is what ribbon thickness reads.
        ``hop_amount`` is BIGINT cents per AO.1 — the wrap divides so
        the rendered ribbon labels show dollars."""
        sankey = Sankey(
            source=_StubDim(column=_StubColumn("source_display")),
            target=_StubDim(column=_StubColumn("target_display")),
            weight=_StubMeasure(
                kind="sum",
                column=_StubColumn("hop_amount"),
                currency=True,
            ),
        )
        wrapped = wrap_for_visual(_BASE, sankey, contract=_CENTS_CONTRACT)
        assert '(SUM("hop_amount") / 100.0)' in wrapped
