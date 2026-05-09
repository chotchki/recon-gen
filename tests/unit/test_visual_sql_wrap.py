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

from dataclasses import dataclass

import pytest

from quicksight_gen.common.html._visual_sql import wrap_for_visual


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
