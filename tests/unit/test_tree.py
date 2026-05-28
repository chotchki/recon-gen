"""Unit tests for the L.1 tree primitives in ``common/tree.py``.

L.1.2 coverage: structural types (App / Dashboard / Analysis / Sheet),
GridSlot placement validation, emit() round-trip into models.py.

L.1.3+ coverage joins as each sub-step lands.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._test_helpers import make_test_config
from recon_gen.apps.l2_flow_tracing.datasets import META_VALUE_PLACEHOLDER_SENTINEL
from recon_gen.common.ids import (
    FilterGroupId,
    ParameterName,
    SheetId,
    VisualId,
)
from recon_gen.common.models import DateTimeDefaultValues
from recon_gen.common.tree import (
    AUTO,
    KPI,
    Analysis,
    App,
    BarChart,
    CalcField,
    CategoryFilter,
    Dataset,
    DateTimeParam,
    Dim,
    FilterGroup,
    FilterLike,
    IntegerParam,
    Measure,
    auto_id,
    NumericRangeFilter,
    ParameterBound,
    Sankey,
    Sheet,
    StaticBound,
    StringParam,
    Table,
    TimeRangeFilter,
    VisualLike,
)


# Module-level Dataset fixtures used across the L.1.3 / L.1.6 tests.
# Real apps use a per-app dataset registry on the App; tests use these
# stand-ins. The identifiers ("ds", "ds-foo", "ds-anomalies") match
# the strings the pre-L.1.7 tests passed.
_DS = Dataset(identifier="ds", arn="arn:aws:quicksight:::dataset/ds")
_DS_FOO = Dataset(identifier="ds-foo", arn="arn:aws:quicksight:::dataset/ds-foo")
_DS_ANOMALIES = Dataset(
    identifier="ds-anomalies", arn="arn:aws:quicksight:::dataset/ds-anomalies",
)


_TEST_CFG = make_test_config()


# ---------------------------------------------------------------------------
# Sheet
# ---------------------------------------------------------------------------

class TestSheet:
    def test_emits_minimal_sheet_definition(self):
        sheet = Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test",
            title="Test Sheet",
            description="Test sheet for unit tests.",
        )
        emitted = sheet.emit()
        assert emitted.SheetId == "sheet-test"
        assert emitted.Name == "Test"
        assert emitted.Title == "Test Sheet"
        assert emitted.Description == "Test sheet for unit tests."
        assert emitted.ContentType == "INTERACTIVE"
        assert emitted.Visuals is None
        assert emitted.ParameterControls is None
        assert emitted.FilterControls == []  # explicit empty for L.1.6 forward-compat

    def test_layout_row_emits_visuals_in_order(self):
        sheet = Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        )
        row = sheet.layout.row(height=6)
        row.add_kpi(width=12, visual_id=VisualId("v-a"), title="A", subtitle="t")
        row.add_kpi(width=12, visual_id=VisualId("v-b"), title="B", subtitle="t")
        emitted = sheet.emit()
        assert emitted.Visuals is not None
        visual_ids: list[str] = []
        for v in emitted.Visuals:
            assert v.KPIVisual is not None
            visual_ids.append(v.KPIVisual.VisualId)
        assert visual_ids == ["v-a", "v-b"]

    def test_emit_layout_references_visual_id_at_emit_time(self):
        """GridSlot stores an object ref; ElementId resolves to the
        referenced visual's id at emit time. Locked decision: object
        refs over string IDs."""
        sheet = Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        )
        sheet.layout.row(height=18).add_kpi(
            width=36, visual_id=VisualId("v-the-one"), title="One",
                subtitle="t",
        )
        emitted = sheet.emit()
        assert emitted.Layouts is not None
        layout = emitted.Layouts[0]
        assert layout.Configuration.GridLayout is not None
        elements = layout.Configuration.GridLayout.Elements
        assert len(elements) == 1
        assert elements[0].ElementId == "v-the-one"
        assert elements[0].ElementType == "VISUAL"
        assert elements[0].ColumnSpan == 36


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

class TestAnalysis:
    def test_add_sheet_rejects_duplicate_id(self):
        analysis = Analysis(analysis_id_suffix="test-analysis", name="Test")
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-dup"),
            name="A", title="A", description="test",
        ))
        with pytest.raises(ValueError, match="already on this Analysis"):
            analysis.add_sheet(Sheet(
                sheet_id=SheetId("sheet-dup"),
                name="B", title="B", description="test",
            ))

    def test_emit_definition_carries_sheets(self):
        analysis = Analysis(analysis_id_suffix="test-analysis", name="Test")
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-1"),
            name="A", title="A", description="test",
        ))
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-2"),
            name="B", title="B", description="test",
        ))
        defn = analysis.emit_definition(datasets=[])
        assert defn.Sheets is not None
        assert [s.SheetId for s in defn.Sheets] == ["sheet-1", "sheet-2"]

    def test_emit_definition_emits_dataset_declarations_from_dataset_refs(self):
        analysis = Analysis(analysis_id_suffix="test-analysis", name="Test")
        defn = analysis.emit_definition(datasets=[_DS_FOO])
        assert len(defn.DataSetIdentifierDeclarations) == 1
        assert defn.DataSetIdentifierDeclarations[0].Identifier == "ds-foo"
        assert defn.DataSetIdentifierDeclarations[0].DataSetArn == _DS_FOO.arn


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class TestApp:
    def _make_app_with_one_sheet(self) -> App:
        app = App(name="test-app", cfg=_TEST_CFG, allow_bare_strings=True)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="test-analysis",
            name="Test Analysis",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-1"),
            name="A", title="A", description="test",
        ))
        sheet.layout.row(height=18).add_kpi(
            width=36, visual_id=VisualId("v-1"), title="One",
                subtitle="t",
        )
        return app

    def test_emit_analysis_builds_model_analysis(self):
        app = self._make_app_with_one_sheet()
        analysis = app.emit_analysis()
        assert analysis.AwsAccountId == "111122223333"
        assert analysis.AnalysisId.startswith("recon-test-")
        assert analysis.AnalysisId.endswith("test-analysis")
        assert analysis.Name == "Test Analysis"
        assert analysis.ThemeArn  # non-empty
        assert analysis.Definition is not None
        assert analysis.Definition.Sheets is not None
        assert len(analysis.Definition.Sheets) == 1

    def test_emit_analysis_without_analysis_raises(self):
        app = App(name="test-app", cfg=_TEST_CFG, allow_bare_strings=True)
        with pytest.raises(ValueError, match="set_analysis"):
            app.emit_analysis()

    # L.1.21 — analysis-mismatch test deleted: app.create_dashboard()
    # uses the App's already-set analysis by construction, so the
    # mismatch bug class is structurally impossible.

    def test_create_dashboard_returns_registered_dashboard(self):
        app = self._make_app_with_one_sheet()
        ret = app.create_dashboard(
            dashboard_id_suffix="test-dashboard",
            name="Test Dashboard",
        )
        assert ret is app.dashboard

    def test_emit_dashboard_builds_model_dashboard(self):
        app = self._make_app_with_one_sheet()
        app.create_dashboard(
            dashboard_id_suffix="test-dashboard",
            name="Test Dashboard",
        )
        dashboard = app.emit_dashboard()
        assert dashboard.AwsAccountId == "111122223333"
        assert dashboard.DashboardId.startswith("recon-test-")
        assert dashboard.DashboardId.endswith("test-dashboard")
        assert dashboard.Name == "Test Dashboard"
        assert dashboard.Definition is not None
        assert dashboard.Definition.Sheets is not None
        # Same definition shape as the Analysis's
        assert len(dashboard.Definition.Sheets) == 1

    def test_emit_dashboard_without_dashboard_raises(self):
        app = self._make_app_with_one_sheet()
        with pytest.raises(ValueError, match="create_dashboard"):
            app.emit_dashboard()

    def test_emit_analysis_round_trips_through_to_aws_json(self):
        """The whole point — tree-built models.Analysis serializes
        cleanly through the existing to_aws_json path."""
        app = self._make_app_with_one_sheet()
        analysis = app.emit_analysis()
        j = analysis.to_aws_json()
        assert j["AwsAccountId"] == "111122223333"
        assert j["AnalysisId"].endswith("test-analysis")
        assert "Definition" in j
        assert len(j["Definition"]["Sheets"]) == 1
        assert j["Definition"]["Sheets"][0]["SheetId"] == "sheet-1"


# ---------------------------------------------------------------------------
# L.1.3 — Field-well wrappers (Dim, Measure)
# ---------------------------------------------------------------------------

class TestDim:
    def test_categorical_default(self):
        dim = Dim(dataset=_DS_FOO, field_id="f-1", column="col_a")
        emitted = dim.emit()
        assert emitted.CategoricalDimensionField is not None
        assert emitted.CategoricalDimensionField.FieldId == "f-1"
        assert emitted.CategoricalDimensionField.Column.ColumnName == "col_a"
        assert emitted.CategoricalDimensionField.Column.DataSetIdentifier == "ds-foo"

    def test_date_factory(self):
        dim = Dim.date(dataset=_DS_FOO, field_id="f-d", column="posted_at")
        emitted = dim.emit()
        assert emitted.DateDimensionField is not None
        assert emitted.CategoricalDimensionField is None

    def test_numerical_factory(self):
        dim = Dim.numerical(dataset=_DS_FOO, field_id="f-n", column="depth")
        emitted = dim.emit()
        assert emitted.NumericalDimensionField is not None

    # Q.1.a.7 — currency=True on a numerical Dim emits the same USD
    # CurrencyDisplayFormatConfiguration that Measure.currency uses, so
    # row-level money columns in tables format consistently with KPIs.
    def test_numerical_currency_flag_emits_usd_format_configuration(self):
        dim = Dim.numerical(
            dataset=_DS_FOO, field_id="f-money", column="amount", currency=True,
        )
        emitted = dim.emit()
        ndf = emitted.NumericalDimensionField
        assert ndf is not None
        assert ndf.FormatConfiguration is not None
        inner_fc = ndf.FormatConfiguration.FormatConfiguration
        assert inner_fc is not None
        cur = inner_fc.CurrencyDisplayFormatConfiguration
        assert cur is not None
        assert cur.Symbol == "USD"

    def test_numerical_currency_default_off(self):
        dim = Dim.numerical(dataset=_DS_FOO, field_id="f-d", column="depth")
        ndf = dim.emit().NumericalDimensionField
        assert ndf is not None
        assert ndf.FormatConfiguration is None

    def test_currency_rejects_categorical_dim(self):
        # Money never makes sense on a categorical or date axis — wiring
        # currency=True on a non-numerical Dim is a typo, not an
        # ergonomic shorthand. Fail loud at emit.
        dim = Dim(
            dataset=_DS_FOO, column="account_name", kind="categorical",
            field_id="f-bad", currency=True,
        )
        with pytest.raises(AssertionError, match="kind='numerical'"):
            dim.emit()


class TestMeasure:
    def test_sum_emits_numerical_field(self):
        m = Measure.sum(dataset=_DS_FOO, field_id="f-1", column="amount")
        emitted = m.emit()
        assert emitted.NumericalMeasureField is not None
        assert emitted.NumericalMeasureField.AggregationFunction is not None
        assert emitted.NumericalMeasureField.AggregationFunction.SimpleNumericalAggregation == "SUM"

    def test_max_min_average(self):
        for kind, expected in [("max", "MAX"), ("min", "MIN"), ("average", "AVERAGE")]:
            m = getattr(Measure, kind)(dataset=_DS, field_id=f"f-{kind}", column="amount")
            emitted = m.emit()
            assert emitted.NumericalMeasureField is not None
            assert emitted.NumericalMeasureField.AggregationFunction is not None
            assert emitted.NumericalMeasureField.AggregationFunction.SimpleNumericalAggregation == expected

    def test_count_emits_numerical_sum_over_row_one_calc_field(self):
        # BL.1 — kind="count" emits NumericalMeasureField(SUM) over the
        # auto-registered ``_row_one_<dataset_id>`` CalcField (literal
        # ``1`` per row). The original CategoricalMeasureField(COUNT)
        # wire silently rendered DISTINCT when QS saw the column as a
        # Dim elsewhere on the same visual; SUM-over-1 is a pure row
        # count with no quirky distinct behavior.
        m = Measure.count(dataset=_DS_FOO, field_id="f-1", column="account_id")
        emitted = m.emit()
        assert emitted.CategoricalMeasureField is None
        nmf = emitted.NumericalMeasureField
        assert nmf is not None
        assert nmf.AggregationFunction is not None
        assert nmf.AggregationFunction.SimpleNumericalAggregation == "SUM"
        # The Column ref points at the row-one CalcField, not the
        # original column. ``App.resolve_auto_ids`` registers the
        # matching CalcField on the Analysis.
        from recon_gen.common.tree.fields import row_one_calc_name
        assert nmf.Column.ColumnName == row_one_calc_name(_DS_FOO)
        assert nmf.Column.DataSetIdentifier == _DS_FOO.identifier

    def test_distinct_count_emits_categorical_field(self):
        m = Measure.distinct_count(dataset=_DS_FOO, field_id="f-1", column="account_id")
        emitted = m.emit()
        assert emitted.CategoricalMeasureField is not None
        assert emitted.CategoricalMeasureField.AggregationFunction == "DISTINCT_COUNT"

    # Q.1.a — currency=True wires a USD CurrencyDisplayFormatConfiguration
    # onto the underlying NumericalMeasureField. Default (no flag) emits no
    # FormatConfiguration at all so existing measures stay byte-identical.
    def test_currency_flag_emits_usd_format_configuration(self):
        m = Measure.sum(dataset=_DS_FOO, field_id="f-1", column="amount", currency=True)
        emitted = m.emit()
        nmf = emitted.NumericalMeasureField
        assert nmf is not None
        fc = nmf.FormatConfiguration
        assert fc is not None
        assert fc.FormatConfiguration is not None
        currency_cfg = fc.FormatConfiguration.CurrencyDisplayFormatConfiguration
        assert currency_cfg is not None
        assert currency_cfg.Symbol == "USD"
        assert currency_cfg.DecimalPlacesConfiguration is not None
        assert currency_cfg.DecimalPlacesConfiguration.DecimalPlaces == 2
        assert currency_cfg.SeparatorConfiguration is not None
        assert currency_cfg.SeparatorConfiguration.ThousandsSeparator is not None
        assert currency_cfg.SeparatorConfiguration.ThousandsSeparator.Symbol == "COMMA"

    def test_currency_default_off_leaves_format_configuration_unset(self):
        m = Measure.sum(dataset=_DS_FOO, field_id="f-1", column="amount")
        emitted = m.emit()
        assert emitted.NumericalMeasureField is not None
        assert emitted.NumericalMeasureField.FormatConfiguration is None

    def test_currency_works_on_max_min_average(self):
        for kind in ("max", "min", "average"):
            m = getattr(Measure, kind)(
                dataset=_DS_FOO, field_id=f"f-{kind}", column="amount", currency=True,
            )
            emitted = m.emit()
            assert emitted.NumericalMeasureField is not None
            assert (
                emitted.NumericalMeasureField.FormatConfiguration is not None
            ), f"{kind} should support currency=True"

    def test_currency_rejects_count_aggregations(self):
        """count / distinct_count are categorical (return row counts,
        never money) — currency=True is an author bug, fail loud."""
        import pytest as _pytest
        m = Measure(
            dataset=_DS_FOO, column="account_id", kind="count",
            field_id="f-1", currency=True,
        )
        with _pytest.raises(AssertionError, match="numerical aggregations"):
            m.emit()

    def test_v11_24_1_rejects_numerical_aggregation_over_datetime_column(self):
        """v11.24.1 regression guard: ``Measure.{sum,max,min,average}``
        over a DATETIME-declared column has to fail at emit time. QS
        rejects ``NumericalMeasureField`` over non-INTEGER/DECIMAL
        columns at analysis-create time; v11.24.0's BO.12 "Latest Leg"
        KPI bound ``postings["posting"].max()`` over a DATETIME column
        and took out the L2 Flow Tracing deploy in CI. The guard now
        fires at emit time so the same shape fails the unit + json
        layers before deploy ever runs.

        The check leans on a registered ``DatasetContract`` declaring
        the column's type; without that ground truth it stays
        permissive (CalcField refs, missing contracts, missing columns
        all skip). This test uses a fresh test-local identifier so the
        registration doesn't pollute other tests' ``ds-foo`` state."""
        import pytest as _pytest
        from recon_gen.common.dataset_contract import (
            ColumnSpec, DatasetContract, register_contract,
        )

        ds = Dataset(
            identifier="ds-v11241-guard-test",
            arn="arn:aws:quicksight:::dataset/ds-v11241-guard-test",
        )
        register_contract("ds-v11241-guard-test", DatasetContract(columns=[
            ColumnSpec("posting", "DATETIME"),
            ColumnSpec("amount", "DECIMAL"),
        ]))

        # The numeric column emits cleanly under every numerical kind.
        for kind in ("sum", "max", "min", "average"):
            ok = getattr(Measure, kind)(
                dataset=ds, field_id=f"f-ok-{kind}", column="amount",
            )
            assert ok.emit().NumericalMeasureField is not None

        # The DATETIME column trips the v11.24.1 guard on every
        # numerical kind. Pinning all four because QS rejects the same
        # field-well shape regardless of which aggregation it carries.
        for kind in ("sum", "max", "min", "average"):
            bad = getattr(Measure, kind)(
                dataset=ds, field_id=f"f-bad-{kind}", column="posting",
            )
            with _pytest.raises(
                AssertionError,
                match=r"INTEGER or DECIMAL columns, but 'posting' is declared",
            ):
                bad.emit()

        # distinct_count emits a CategoricalMeasureField — QS accepts
        # those over any column type — so the v11.24.1 guard MUST NOT
        # fire even when pointed at the DATETIME column.
        ok = Measure(
            dataset=ds, column="posting", kind="distinct_count",
            field_id="f-cat-distinct-count",
        )
        assert ok.emit().CategoricalMeasureField is not None


# ---------------------------------------------------------------------------
# L.1.3 — Typed Visual subtypes
# ---------------------------------------------------------------------------

class TestKPIVisual:
    def test_emits_kpi_visual(self):
        kpi = KPI(
            visual_id=VisualId("v-kpi"),
            title="Total",
            subtitle="Sum of amounts",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
        )
        emitted = kpi.emit()
        assert emitted.KPIVisual is not None
        assert emitted.KPIVisual.VisualId == "v-kpi"
        assert emitted.KPIVisual.Title is not None
        assert emitted.KPIVisual.Title.FormatText is not None
        assert emitted.KPIVisual.Title.FormatText["PlainText"] == "Total"
        assert emitted.KPIVisual.Subtitle is not None
        assert emitted.KPIVisual.Subtitle.FormatText is not None
        assert emitted.KPIVisual.Subtitle.FormatText["PlainText"] == "Sum of amounts"

    def test_subtitle_required_non_blank(self):
        # b.15.invariant.sheet-description: subtitle is required + non-blank.
        # The constructor catches both omission (TypeError from the
        # dataclass) and a blank string (ValueError from __post_init__).
        with pytest.raises(ValueError, match="subtitle is required"):
            KPI(
                visual_id=VisualId("v-kpi"),
                title="Total",
                subtitle="",
                values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
            )

    def test_satisfies_visual_like_protocol(self):
        kpi = KPI(visual_id=VisualId("v-kpi"), title="Test", subtitle="t")
        assert isinstance(kpi, VisualLike)


class TestTableVisual:
    def test_emits_table_with_group_by_and_values(self):
        table = Table(
            visual_id=VisualId("v-tbl"),
            title="Detail",
            group_by=[
                Dim(dataset=_DS, field_id="f-id", column="id"),
                Dim(dataset=_DS, field_id="f-name", column="name"),
            ],
            values=[Measure.sum(dataset=_DS, field_id="f-amt", column="amount")],
                subtitle="t",
        )
        emitted = table.emit()
        assert emitted.TableVisual is not None
        assert emitted.TableVisual.ChartConfiguration is not None
        assert emitted.TableVisual.ChartConfiguration.FieldWells is not None
        wells = emitted.TableVisual.ChartConfiguration.FieldWells.TableAggregatedFieldWells
        assert wells is not None
        assert wells.GroupBy is not None
        assert len(wells.GroupBy) == 2
        assert wells.Values is not None
        assert len(wells.Values) == 1

    def test_sort_by(self):
        table = Table(
            visual_id=VisualId("v-tbl"),
            title="Detail",
            sort_by=("f-amt", "DESC"),
                subtitle="t",
        )
        emitted = table.emit()
        assert emitted.TableVisual is not None
        assert emitted.TableVisual.ChartConfiguration is not None
        sort = emitted.TableVisual.ChartConfiguration.SortConfiguration
        assert sort is not None
        assert sort["RowSort"][0]["FieldSort"]["FieldId"] == "f-amt"
        assert sort["RowSort"][0]["FieldSort"]["Direction"] == "DESC"


class TestBarChartVisual:
    def test_emits_bar_with_category_and_values(self):
        bar = BarChart(
            visual_id=VisualId("v-bar"),
            title="By Bucket",
            category=[Dim(dataset=_DS, field_id="f-bucket", column="z_bucket")],
            values=[Measure.count(dataset=_DS, field_id="f-cnt", column="recipient_id")],
                subtitle="t",
        )
        emitted = bar.emit()
        assert emitted.BarChartVisual is not None
        assert emitted.BarChartVisual.ChartConfiguration is not None
        assert emitted.BarChartVisual.ChartConfiguration.FieldWells is not None
        wells = emitted.BarChartVisual.ChartConfiguration.FieldWells.BarChartAggregatedFieldWells
        assert wells is not None
        assert wells.Category is not None
        assert len(wells.Category) == 1
        assert wells.Values is not None
        assert len(wells.Values) == 1


class TestSankeyVisual:
    def test_emits_sankey_with_source_target_weight(self):
        sankey = Sankey(
            visual_id=VisualId("v-sankey"),
            title="Flow",
            source=Dim(dataset=_DS, field_id="f-src", column="source_display"),
            target=Dim(dataset=_DS, field_id="f-tgt", column="target_display"),
            weight=Measure.sum(dataset=_DS, field_id="f-wt", column="hop_amount"),
            items_limit=50,
                subtitle="t",
        )
        emitted = sankey.emit()
        assert emitted.SankeyDiagramVisual is not None
        assert emitted.SankeyDiagramVisual.ChartConfiguration is not None
        assert emitted.SankeyDiagramVisual.ChartConfiguration.FieldWells is not None
        wells = emitted.SankeyDiagramVisual.ChartConfiguration.FieldWells.SankeyDiagramAggregatedFieldWells
        assert wells is not None
        assert wells.Source is not None
        assert len(wells.Source) == 1
        source_cat = wells.Source[0].CategoricalDimensionField
        assert source_cat is not None
        assert source_cat.Column.ColumnName == "source_display"
        assert wells.Destination is not None
        assert len(wells.Destination) == 1
        dest_cat = wells.Destination[0].CategoricalDimensionField
        assert dest_cat is not None
        assert dest_cat.Column.ColumnName == "target_display"
        assert wells.Weight is not None
        assert len(wells.Weight) == 1

    def test_weight_drives_sort_desc(self):
        sankey = Sankey(
            visual_id=VisualId("v-sankey"),
            title="Flow",
            weight=Measure.sum(dataset=_DS, field_id="f-wt", column="hop_amount"),
                subtitle="t",
        )
        emitted = sankey.emit()
        assert emitted.SankeyDiagramVisual is not None
        assert emitted.SankeyDiagramVisual.ChartConfiguration is not None
        sort = emitted.SankeyDiagramVisual.ChartConfiguration.SortConfiguration
        assert sort is not None
        assert sort.WeightSort is not None
        assert sort.WeightSort[0]["FieldSort"]["FieldId"] == "f-wt"
        assert sort.WeightSort[0]["FieldSort"]["Direction"] == "DESC"

    def test_items_limit_caps_both_sides(self):
        sankey = Sankey(
            visual_id=VisualId("v-sankey"),
            title="Flow",
            items_limit=25,
                subtitle="t",
        )
        emitted = sankey.emit()
        assert emitted.SankeyDiagramVisual is not None
        assert emitted.SankeyDiagramVisual.ChartConfiguration is not None
        sort = emitted.SankeyDiagramVisual.ChartConfiguration.SortConfiguration
        assert sort is not None
        assert sort.SourceItemsLimit is not None
        assert sort.SourceItemsLimit["ItemsLimit"] == 25
        assert sort.DestinationItemsLimit is not None
        assert sort.DestinationItemsLimit["ItemsLimit"] == 25
        assert sort.SourceItemsLimit["OtherCategories"] == "INCLUDE"


class TestSheetAcceptsTypedVisuals:
    """Layout DSL constructors return typed visual subtypes (KPI / Table
    / BarChart / Sankey) — generic `add_*` preserves the concrete
    subtype, the visual is registered + placed atomically."""

    def test_add_kpi(self):
        sheet = Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        )
        sheet.layout.row(height=6).add_kpi(
            width=12,
            visual_id=VisualId("v-kpi"),
            title="Total",
            values=[Measure.sum(_DS, "amount", field_id="f")],
                subtitle="t",
        )
        emitted = sheet.emit()
        assert emitted.Visuals is not None
        assert emitted.Visuals[0].KPIVisual is not None
        assert emitted.Visuals[0].KPIVisual.VisualId == "v-kpi"
        assert emitted.Layouts is not None
        assert emitted.Layouts[0].Configuration.GridLayout is not None
        assert emitted.Layouts[0].Configuration.GridLayout.Elements[0].ElementId == "v-kpi"

    def test_layout_add_kpi_returns_concrete_subtype(self):
        """Layout DSL preserves the caller's concrete subtype — the
        returned ref still types as KPI, not the widened VisualLike
        Protocol."""
        sheet = Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        )
        kpi: KPI = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-kpi"), title="Test",
                subtitle="t",
        )
        # If the generic worked, kpi is still a KPI — accessing
        # KPI-only attributes shouldn't widen.
        assert kpi.title == "Test"


# ---------------------------------------------------------------------------
# L.1.4 — Parameter declarations
# ---------------------------------------------------------------------------

class TestStringParam:
    def test_emits_single_valued_string_param(self):
        p = StringParam(
            name=ParameterName("pTest"),
            default=["default-value"],
        )
        emitted = p.emit()
        assert emitted.StringParameterDeclaration is not None
        assert emitted.StringParameterDeclaration.Name == "pTest"
        assert emitted.StringParameterDeclaration.ParameterValueType == "SINGLE_VALUED"
        assert emitted.StringParameterDeclaration.DefaultValues == {"StaticValues": ["default-value"]}

    def test_no_default_emits_empty_static_values(self):
        """No-default pattern matches the existing
        ``DefaultValues={"StaticValues": []}`` shape used by the
        K.4.5 chain-root + K.4.8 anchor parameters (which rely on
        the SelectAll=HIDDEN dropdown to land on first row)."""
        p = StringParam(name=ParameterName("pNoDefault"))
        emitted = p.emit()
        assert emitted.StringParameterDeclaration is not None
        assert emitted.StringParameterDeclaration.DefaultValues == {"StaticValues": []}

    def test_multi_valued(self):
        p = StringParam(
            name=ParameterName("pMulti"),
            default=["a", "b"],
            multi_valued=True,
        )
        emitted = p.emit()
        assert emitted.StringParameterDeclaration is not None
        assert emitted.StringParameterDeclaration.ParameterValueType == "MULTI_VALUED"


class TestIntegerParam:
    def test_emits_integer_param_with_default(self):
        p = IntegerParam(
            name=ParameterName("pSigma"),
            default=[2],
        )
        emitted = p.emit()
        assert emitted.IntegerParameterDeclaration is not None
        assert emitted.IntegerParameterDeclaration.Name == "pSigma"
        assert emitted.IntegerParameterDeclaration.DefaultValues == {"StaticValues": [2]}


class TestDateTimeParam:
    def test_emits_datetime_param_with_rolling_default(self):
        """RollingDate pattern — same shape as AR's pArDsBalanceDate
        (P_AR_DS_BALANCE_DATE) which uses ``truncDate('DD', now())``
        for "today"."""
        p = DateTimeParam(
            name=ParameterName("pDate"),
            time_granularity="DAY",
            default=DateTimeDefaultValues(
                RollingDate={"Expression": "truncDate('DD', now())"},
            ),
        )
        emitted = p.emit()
        assert emitted.DateTimeParameterDeclaration is not None
        assert emitted.DateTimeParameterDeclaration.TimeGranularity == "DAY"
        assert emitted.DateTimeParameterDeclaration.DefaultValues is not None
        assert emitted.DateTimeParameterDeclaration.DefaultValues.RollingDate is not None

    def test_accepts_none_time_granularity(self):
        # time_granularity is optional; default is required (M.4.4.10d).
        p = DateTimeParam(
            name=ParameterName("pDate"),
            default=DateTimeDefaultValues(StaticValues=["2030-01-01"]),
        )
        assert p.time_granularity is None


class TestAnalysisAddParameter:
    def test_add_parameter_returns_concrete_subtype(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        sigma: IntegerParam = analysis.add_parameter(IntegerParam(
            name=ParameterName("pSigma"), default=[2],
        ))
        # Concrete subtype preserved through the generic.
        assert sigma.default == [2]

    def test_duplicate_parameter_name_raises(self):
        """Same-name shadow bug class: two declarations sharing a Name
        silently let one win at deploy time. Caught at construction."""
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        analysis.add_parameter(IntegerParam(name=ParameterName("pDup"), default=[1]))
        with pytest.raises(ValueError, match="already declared"):
            analysis.add_parameter(StringParam(name=ParameterName("pDup")))

    def test_emit_definition_carries_parameter_declarations(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        analysis.add_parameter(IntegerParam(
            name=ParameterName("pSigma"), default=[2],
        ))
        analysis.add_parameter(StringParam(
            name=ParameterName("pAnchor"),
        ))
        defn = analysis.emit_definition(datasets=[])
        assert defn.ParameterDeclarations is not None
        names: list[str] = []
        for pd in defn.ParameterDeclarations:
            if pd.IntegerParameterDeclaration:
                names.append(pd.IntegerParameterDeclaration.Name)
            elif pd.StringParameterDeclaration:
                names.append(pd.StringParameterDeclaration.Name)
        assert names == ["pSigma", "pAnchor"]

    def test_no_parameters_emits_none(self):
        """Analysis without any parameter declarations passes None to
        models.AnalysisDefinition (preserving the existing pattern that
        omits empty fields)."""
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        defn = analysis.emit_definition(datasets=[])
        assert defn.ParameterDeclarations is None


# ---------------------------------------------------------------------------
# L.1.5 — FilterGroup with object-ref scope + scope-on-same-sheet validation
# ---------------------------------------------------------------------------

def _category_filter(
    filter_id: str, dataset: Dataset, column: str,
) -> CategoryFilter:
    """Test-only typed CategoryFilter constructor — keeps the test
    focus on scope validation, not Filter construction details."""
    return CategoryFilter.with_values(
        filter_id=filter_id,
        dataset=dataset,
        column=column,
        values=["yes"],
    )


class TestFilterGroupScope:
    def _make_sheet_with_visuals(
        self, sheet_id: str, *visual_ids: str,  # typing-smell: ignore[bare-str-id]: sheet_id comes from callers as raw analyst string
    ) -> tuple[Sheet, list[KPI]]:
        sheet = Sheet(
            sheet_id=SheetId(sheet_id),
            name="Test", title="Test", description="test",
        )
        row = sheet.layout.row(height=6)
        visuals: list[KPI] = []
        for vid in visual_ids:
            v = row.add_kpi(width=6, visual_id=VisualId(vid), title=vid, subtitle="t")
            visuals.append(v)
        return sheet, visuals

    def test_scope_visuals_validates_visual_is_on_sheet(self):
        """Wrong-sheet bug: scope_visuals raises if any visual isn't
        registered on the given sheet. Catches the bug class at the
        wiring line."""
        _sheet_a, [v_a] = self._make_sheet_with_visuals("sheet-a", "v-a")
        sheet_b, [_v_b] = self._make_sheet_with_visuals("sheet-b", "v-b")

        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        with pytest.raises(ValueError, match="isn't registered on sheet"):
            # Trying to scope a visual from sheet-a onto sheet-b
            sheet_b.scope(fg, [v_a])

    def test_scope_visuals_with_correct_visuals_succeeds(self):
        sheet, [v1, v2] = self._make_sheet_with_visuals(
            "sheet-test", "v-1", "v-2",
        )
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        ret = sheet.scope(fg, [v1, v2])
        assert ret is fg  # chains
        assert len(fg._scope_entries) == 1

    def test_scope_visuals_emits_selected_visuals_configuration(self):
        sheet, [v1, v2] = self._make_sheet_with_visuals(
            "sheet-test", "v-1", "v-2",
        )
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        sheet.scope(fg, [v1, v2])
        emitted = fg.emit()
        assert emitted.ScopeConfiguration.SelectedSheets is not None
        configs = emitted.ScopeConfiguration.SelectedSheets.SheetVisualScopingConfigurations
        assert configs is not None
        assert len(configs) == 1
        assert configs[0].SheetId == "sheet-test"
        assert configs[0].Scope == "SELECTED_VISUALS"
        assert configs[0].VisualIds == ["v-1", "v-2"]

    def test_scope_sheet_emits_all_visuals_configuration(self):
        sheet, _ = self._make_sheet_with_visuals(
            "sheet-test", "v-1", "v-2",
        )
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        fg.scope_sheet(sheet)
        emitted = fg.emit()
        assert emitted.ScopeConfiguration.SelectedSheets is not None
        configs = emitted.ScopeConfiguration.SelectedSheets.SheetVisualScopingConfigurations
        assert configs is not None
        assert configs[0].SheetId == "sheet-test"
        assert configs[0].Scope == "ALL_VISUALS"
        assert configs[0].VisualIds is None

    def test_emit_without_scope_raises(self):
        """A FilterGroup with no scope configured wouldn't apply to
        anything at deploy — fail loud at construction rather than
        silently emitting an empty configuration."""
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        with pytest.raises(ValueError, match="has no scope"):
            fg.emit()

    def test_multiple_scope_entries(self):
        """A FilterGroup can scope to (visual subset on sheet A) plus
        (all visuals on sheet B). Each entry emits its own
        SheetVisualScopingConfiguration."""
        sheet_a, [v_a1, _v_a2] = self._make_sheet_with_visuals(
            "sheet-a", "v-a1", "v-a2",
        )
        sheet_b, _ = self._make_sheet_with_visuals(
            "sheet-b", "v-b1",
        )
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-multi"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        sheet_a.scope(fg, [v_a1])
        fg.scope_sheet(sheet_b)
        emitted = fg.emit()
        assert emitted.ScopeConfiguration.SelectedSheets is not None
        configs = emitted.ScopeConfiguration.SelectedSheets.SheetVisualScopingConfigurations
        assert configs is not None
        assert len(configs) == 2
        assert configs[0].SheetId == "sheet-a"
        assert configs[0].Scope == "SELECTED_VISUALS"
        assert configs[0].VisualIds == ["v-a1"]
        assert configs[1].SheetId == "sheet-b"
        assert configs[1].Scope == "ALL_VISUALS"

    def test_emit_carries_filters_through(self):
        """Each typed FilterLike's emit() runs at FilterGroup.emit() time —
        the emitted Filters list contains the corresponding models.Filter
        instances, not the typed wrappers themselves."""
        sheet, _ = self._make_sheet_with_visuals("sheet-test", "v-1")
        f = _category_filter("f-1", _DS_FOO, "col_a")
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[f],
        )
        fg.scope_sheet(sheet)
        emitted = fg.emit()
        assert len(emitted.Filters) == 1
        emitted_filter = emitted.Filters[0]
        assert emitted_filter.CategoryFilter is not None
        assert emitted_filter.CategoryFilter.FilterId == "f-1"

    def test_disabled_filter_group(self):
        sheet, _ = self._make_sheet_with_visuals("sheet-test", "v-1")
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
            enabled=False,
        )
        fg.scope_sheet(sheet)
        emitted = fg.emit()
        assert emitted.Status == "DISABLED"


class TestAnalysisAddFilterGroup:
    def test_add_filter_group_returns_ref(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-1"), title="Test",
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [kpi])
        assert fg in analysis.filter_groups

    def test_duplicate_filter_group_id_raises(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-dup"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        with pytest.raises(ValueError, match="already on this Analysis"):
            analysis.add_filter_group(FilterGroup(
                filter_group_id=FilterGroupId("fg-dup"),
                filters=[_category_filter("f-2", _DS_FOO, "col_b")],
            ))

    def test_emit_definition_carries_filter_groups(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-1"), title="Test",
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [kpi])
        defn = analysis.emit_definition(datasets=[])
        assert defn.FilterGroups is not None
        assert len(defn.FilterGroups) == 1
        assert defn.FilterGroups[0].FilterGroupId == "fg-test"

    def test_no_filter_groups_emits_none(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        defn = analysis.emit_definition(datasets=[])
        assert defn.FilterGroups is None


class TestFilterGroupCompositionWithApp:
    """Cross-check: the wrong-sheet bug class is caught even when
    FilterGroups go through the full App.emit_analysis path.

    The L.1.5 check-in moment — the load-bearing object-ref scope
    validation works end-to-end."""

    def test_wrong_sheet_visual_caught_at_scope_call(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="test", name="Test",
        ))
        sheet_a = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-a"),
            name="A", title="A", description="test",
        ))
        v_a = sheet_a.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-a"), title="A",
                subtitle="t",
        )
        sheet_b = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-b"),
            name="B", title="B", description="test",
        ))
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-cross"),
            filters=[_category_filter("f-1", _DS, "col")],
        ))
        # Try to scope sheet-A's visual onto sheet-B → caught here.
        with pytest.raises(ValueError, match="isn't registered on sheet"):
            sheet_b.scope(fg, [v_a])

# ---------------------------------------------------------------------------
# L.1.6 — Typed Filter wrappers
# ---------------------------------------------------------------------------

class TestTypedCategoryFilter:
    def test_with_values_emits_filter_list_configuration(self):
        f = CategoryFilter.with_values(
            filter_id="f-1",
            dataset=_DS_FOO,
            column="col_a",
            values=["yes", "maybe"],
        )
        emitted = f.emit()
        assert emitted.CategoryFilter is not None
        assert emitted.CategoryFilter.FilterId == "f-1"
        assert emitted.CategoryFilter.Column.DataSetIdentifier == "ds-foo"
        assert emitted.CategoryFilter.Column.ColumnName == "col_a"
        config = emitted.CategoryFilter.Configuration.FilterListConfiguration
        assert config is not None
        assert config["MatchOperator"] == "CONTAINS"
        assert config["CategoryValues"] == ["yes", "maybe"]

    def test_with_parameter_emits_custom_filter_configuration(self):
        anchor = StringParam(name=ParameterName("pAnchor"))
        f = CategoryFilter.with_parameter(
            filter_id="f-1", dataset=_DS, column="col_a",
            parameter=anchor,
        )
        emitted = f.emit()
        assert emitted.CategoryFilter is not None
        config = emitted.CategoryFilter.Configuration.CustomFilterConfiguration
        assert config is not None
        # with_parameter defaults match_operator to EQUALS — dropdowns
        # writing into a parameter typically narrow to a single value.
        assert config["MatchOperator"] == "EQUALS"
        assert config["ParameterName"] == "pAnchor"

    def test_match_operator_is_configurable(self):
        f = CategoryFilter.with_values(
            filter_id="f-1", dataset=_DS, column="col_a",
            values=["a"], match_operator="EQUALS",
        )
        emitted = f.emit()
        assert emitted.CategoryFilter is not None
        config = emitted.CategoryFilter.Configuration.FilterListConfiguration
        assert config is not None
        assert config["MatchOperator"] == "EQUALS"

    def test_satisfies_filter_like_protocol(self):
        f = CategoryFilter.with_values(
            filter_id="f-1", dataset=_DS, column="col_a", values=["x"],
        )
        assert isinstance(f, FilterLike)

    # L.1.22 — `test_neither_values_nor_parameter_rejected` and
    # `test_both_values_and_parameter_rejected` deleted: the discriminated
    # `binding` field is one of `_ValuesBinding` or `_ParameterBinding`,
    # so neither/both cases are structurally impossible.


class TestTypedNumericRangeFilter:
    def test_static_bounds(self):
        f = NumericRangeFilter(
            filter_id="f-1",
            dataset=_DS,
            column="amount",
            minimum=StaticBound(10.0),
            maximum=StaticBound(1000.0),
        )
        emitted = f.emit()
        assert emitted.NumericRangeFilter is not None
        assert emitted.NumericRangeFilter.RangeMinimum is not None
        assert emitted.NumericRangeFilter.RangeMinimum.StaticValue == 10.0
        assert emitted.NumericRangeFilter.RangeMaximum is not None
        assert emitted.NumericRangeFilter.RangeMaximum.StaticValue == 1000.0
        assert emitted.NumericRangeFilter.RangeMinimum.Parameter is None

    def test_parameter_bound_minimum(self):
        """The wiring catches "filter bound to a parameter that doesn't
        exist" — pass an actual ParameterDecl object, the type checker
        guarantees it has a .name. emit() reads param.name to populate
        NumericRangeFilterValue.Parameter."""
        sigma = IntegerParam(
            name=ParameterName("pSigma"), default=[2],
        )
        f = NumericRangeFilter(
            filter_id="f-sigma",
            dataset=_DS,
            column="z_score",
            minimum=ParameterBound(sigma),
        )
        emitted = f.emit()
        assert emitted.NumericRangeFilter is not None
        assert emitted.NumericRangeFilter.RangeMinimum is not None
        assert emitted.NumericRangeFilter.RangeMinimum.Parameter == "pSigma"
        assert emitted.NumericRangeFilter.RangeMinimum.StaticValue is None
        assert emitted.NumericRangeFilter.RangeMaximum is None

    # L.1.22 — `test_both_minimum_value_and_parameter_rejected` and
    # `test_both_maximum_value_and_parameter_rejected` deleted: each
    # `Bound` variant carries exactly one piece of data (a value OR a
    # parameter), so both-set cases are structurally impossible.

    def test_no_bounds_emits_filter_with_no_range(self):
        """A NumericRangeFilter with no min/max is unusual but allowed
        (matches the existing model behaviour where RangeMinimum /
        RangeMaximum are optional)."""
        f = NumericRangeFilter(
            filter_id="f-1", dataset=_DS, column="amount",
        )
        emitted = f.emit()
        assert emitted.NumericRangeFilter is not None
        assert emitted.NumericRangeFilter.RangeMinimum is None
        assert emitted.NumericRangeFilter.RangeMaximum is None

    def test_satisfies_filter_like_protocol(self):
        f = NumericRangeFilter(
            filter_id="f-1", dataset=_DS, column="amount",
        )
        assert isinstance(f, FilterLike)


class TestTypedTimeRangeFilter:
    def test_emits_with_min_max_passthrough(self):
        f = TimeRangeFilter(
            filter_id="f-1",
            dataset=_DS,
            column="posted_at",
            minimum={"StaticValue": "2026-01-01T00:00:00"},
            maximum={"StaticValue": "2026-12-31T23:59:59"},
            time_granularity="DAY",
        )
        emitted = f.emit()
        assert emitted.TimeRangeFilter is not None
        assert emitted.TimeRangeFilter.RangeMinimumValue == {"StaticValue": "2026-01-01T00:00:00"}
        assert emitted.TimeRangeFilter.TimeGranularity == "DAY"

    def test_satisfies_filter_like_protocol(self):
        f = TimeRangeFilter(
            filter_id="f-1", dataset=_DS, column="posted_at",
        )
        assert isinstance(f, FilterLike)

class TestFullEmitRoundTripWithTypedFilters:
    """Replaces the placeholder above; threads through App.emit_analysis
    to confirm typed Filter wrappers serialize cleanly end-to-end."""

    def test_full_emit_round_trip(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="test", name="Test",
        ))
        sigma = analysis.add_parameter(IntegerParam(
            name=ParameterName("pSigma"), default=[2],
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-test"), title="Test",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-sigma"),
            filters=[
                NumericRangeFilter(
                    filter_id="f-sigma",
                    dataset=_DS_FOO,
                    column="z_score",
                    minimum=ParameterBound(sigma),
                ),
            ],
        ))
        sheet.scope(fg, [kpi])
        m = app.emit_analysis()
        j = m.to_aws_json()
        fg_json = j["Definition"]["FilterGroups"][0]
        nrf = fg_json["Filters"][0]["NumericRangeFilter"]
        assert nrf["FilterId"] == "f-sigma"
        assert nrf["Column"]["ColumnName"] == "z_score"
        assert nrf["RangeMinimum"]["Parameter"] == "pSigma"
        # Static values not emitted when unset.
        assert "StaticValue" not in nrf["RangeMinimum"]


    def test_scoping_configuration_round_trips(self):
        """End-to-end: tree → FilterGroup with scope → App.emit_analysis →
        models.Analysis.to_aws_json carries the scoping configuration
        through to the emitted JSON. Carried over from the L.1.5
        composition tests; lives here now alongside the typed-filter
        round-trip."""
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="test", name="Test",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-test"), title="Test",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-scoped"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [kpi])
        m = app.emit_analysis()
        j = m.to_aws_json()
        fgs = j["Definition"]["FilterGroups"]
        assert len(fgs) == 1
        assert fgs[0]["FilterGroupId"] == "fg-scoped"
        configs = fgs[0]["ScopeConfiguration"]["SelectedSheets"]["SheetVisualScopingConfigurations"]
        assert configs[0]["SheetId"] == "sheet-test"
        assert configs[0]["Scope"] == "SELECTED_VISUALS"
        assert configs[0]["VisualIds"] == ["v-test"]


# ---------------------------------------------------------------------------
# L.1.7 — Dataset tree nodes + dependency graph
# ---------------------------------------------------------------------------

class TestDataset:
    def test_emit_declaration(self):
        ds = Dataset(identifier="ds-foo", arn="arn:aws:quicksight:::dataset/foo")
        decl = ds.emit_declaration()
        assert decl.Identifier == "ds-foo"
        assert decl.DataSetArn == "arn:aws:quicksight:::dataset/foo"

    def test_dataset_is_hashable(self):
        """Dataset is the dependency-graph KEY — must be hashable so
        visuals/filters' refs can be collected into set[Dataset]."""
        a = Dataset(identifier="a", arn="arn:a")
        b = Dataset(identifier="b", arn="arn:b")
        s = {a, b, a}
        assert len(s) == 2

    def test_dim_carries_dataset_ref(self):
        """Hard-switch confirmation: Dim's dataset is the Dataset object,
        not the identifier string."""
        ds = Dataset(identifier="ds-foo", arn="arn:foo")
        dim = Dim(dataset=ds, field_id="f-1", column="col_a")
        assert dim.dataset is ds
        # emit() reads the identifier off the Dataset
        emitted_dim = dim.emit()
        assert emitted_dim.CategoricalDimensionField is not None
        assert emitted_dim.CategoricalDimensionField.Column.DataSetIdentifier == "ds-foo"

    def test_measure_carries_dataset_ref(self):
        ds = Dataset(identifier="ds-foo", arn="arn:foo")
        m = Measure.sum(ds, "amount", field_id="f")
        assert m.dataset is ds
        emitted_m = m.emit()
        assert emitted_m.NumericalMeasureField is not None
        assert emitted_m.NumericalMeasureField.Column.DataSetIdentifier == "ds-foo"

    def test_getitem_unknown_column_raises(self):
        """L.1.18 — ``ds["typo"]`` against a contract-registered Dataset
        raises KeyError at the wiring site. The L.1.17 typed-Column path
        depends on this; without it, the typo would survive to the emit
        validator."""
        from recon_gen.common.dataset_contract import (
            ColumnSpec,
            DatasetContract,
            register_contract,
        )
        ds = Dataset(identifier="ds-with-contract", arn="arn:x")
        register_contract(ds.identifier, DatasetContract(columns=[
            ColumnSpec(name="amount", type="DECIMAL"),
        ]))
        # Known column passes through
        assert ds["amount"].name == "amount"
        # Unknown column raises at the wiring site
        with pytest.raises(KeyError, match="typo_column"):
            ds["typo_column"]


class TestAppDatasetRegistry:
    def test_add_dataset_returns_ref(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        ds = app.add_dataset(_DS_FOO)
        assert ds is _DS_FOO
        assert _DS_FOO in app.datasets

    def test_duplicate_dataset_identifier_rejected(self):
        """Same shadow-bug class as duplicate parameters: two registrations
        sharing an identifier silently let one win at deploy."""
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(Dataset(identifier="ds-x", arn="arn:1"))
        with pytest.raises(ValueError, match="already registered"):
            app.add_dataset(Dataset(identifier="ds-x", arn="arn:2"))


class TestAppDatasetDependencies:
    """Walking the tree to extract the precise dataset dependency graph
    is the L.1.7 deployment-side-effect payoff. Selective deploy +
    matview REFRESH ordering both consume this graph."""

    def test_empty_when_no_analysis(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        assert app.dataset_dependencies() == set()

    def test_collects_from_visuals(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        app.add_dataset(_DS_ANOMALIES)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-1"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        row.add_kpi(
            width=12, visual_id=VisualId("v-foo"), title="From foo",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        row.add_kpi(
            width=12, visual_id=VisualId("v-anom"), title="From anomalies",
            values=[Measure.count(_DS_ANOMALIES, "id")],
                subtitle="t",
        )
        deps = app.dataset_dependencies()
        assert deps == {_DS_FOO, _DS_ANOMALIES}

    def test_collects_from_filter_groups(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-1"), name="S", title="S", description="test",
        ))
        # No values; visual itself doesn't reference _DS_FOO.
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-1"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [kpi])
        # Dependency comes via the filter group, not the visual.
        assert app.dataset_dependencies() == {_DS_FOO}

    def test_emit_analysis_rejects_unregistered_dataset(self):
        """The load-bearing validation: if a visual or filter references
        a Dataset that wasn't registered on the App, emit_analysis raises
        with the offending identifier(s)."""
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        # _DS_FOO is NOT registered on this app
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        with pytest.raises(ValueError, match="references unregistered datasets"):
            app.emit_analysis()

    def test_emit_analysis_includes_only_referenced_datasets(self):
        """Selective-by-construction: registered-but-unreferenced datasets
        DO NOT show up in the emitted DataSetIdentifierDeclarations.
        Catches dataset bloat at the deploy boundary."""
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        app.add_dataset(_DS_ANOMALIES)  # registered but unreferenced
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        m = app.emit_analysis()
        decls = m.Definition.DataSetIdentifierDeclarations
        identifiers = {d.Identifier for d in decls}
        assert identifiers == {"ds-foo"}
        assert "ds-anomalies" not in identifiers

    def test_emit_dashboard_validates_references_too(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        app.create_dashboard(dashboard_id_suffix="d", name="D")
        with pytest.raises(ValueError, match="references unregistered datasets"):
            app.emit_dashboard()


class TestValidateFilterParamSettability:
    """Catches the v8.3.3 Daily Statement bug class at App.emit time:
    CategoryFilter / TimeEqualityFilter / NumericRangeFilter that bind
    a parameter the analyst can't set (no control + no default)."""

    def _scaffold(self, *, with_default: bool, with_control: bool) -> App:
        from recon_gen.common.tree import (
            FilterGroup, StaticValues,
        )
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        param = analysis.add_parameter(StringParam(
            name=ParameterName("pAccount"),
            default=["acct-1"] if with_default else [],
        ))
        if with_control:
            sheet.add_parameter_dropdown(
                parameter=param, title="Account",
                selectable_values=StaticValues(values=["acct-1", "acct-2"]),
            )
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-cat"),
            cross_dataset="SINGLE_DATASET",
            filters=[CategoryFilter.with_parameter(
                filter_id="filter-cat",
                dataset=_DS_FOO,
                column=_DS_FOO["account"],
                parameter=param,
            )],
        )).scope_sheet(sheet)
        return app

    def test_param_with_no_control_and_no_default_raises(self):
        """The v8.3.3 footgun: analyst sees an empty dropdown (or no
        widget at all), parameter stays unset, every visual on the
        sheet renders blank."""
        app = self._scaffold(with_default=False, with_control=False)
        with pytest.raises(ValueError, match="unsettable"):
            app.emit_analysis()

    def test_param_with_control_passes(self):
        """A dropdown — even just a static-values one — gives the
        analyst a way to pick. Settable, no error."""
        app = self._scaffold(with_default=False, with_control=True)
        app.emit_analysis()  # doesn't raise

    def test_param_with_default_only_passes(self):
        """Drill-target params (set programmatically by Drill writes,
        no UI control) lean on a default sentinel — that's a valid
        settable shape."""
        app = self._scaffold(with_default=True, with_control=False)
        app.emit_analysis()  # doesn't raise

    def test_dashboard_emit_validates_too(self):
        """The validator runs on emit_dashboard the same way."""
        app = self._scaffold(with_default=False, with_control=False)
        app.create_dashboard(dashboard_id_suffix="d", name="D")
        with pytest.raises(ValueError, match="unsettable"):
            app.emit_dashboard()


# ---------------------------------------------------------------------------
# L.1.8 — CalcField tree nodes
# ---------------------------------------------------------------------------

# Module-level CalcField fixture for the L.1.8 tests. Real apps construct
# CalcField nodes inside per-app builders; tests use a stand-in.
_CALC_IS_ANCHOR = None  # populated lazily inside tests so it can carry _DS_FOO


def _make_is_anchor() -> CalcField:
    """A test-only calc field on _DS_FOO."""
    return CalcField(
        name="is_anchor_edge",
        dataset=_DS_FOO,
        expression="ifelse({source} = ${pAnchor}, 'yes', 'no')",
    )


class TestCalcField:
    def test_emit_returns_dict(self):
        cf = CalcField(
            name="my_calc", dataset=_DS_FOO, expression="1 + 1",
        )
        d = cf.emit()
        assert d == {
            "Name": "my_calc",
            "DataSetIdentifier": "ds-foo",
            "Expression": "1 + 1",
        }

    def test_calc_field_is_hashable(self):
        a = CalcField(name="a", dataset=_DS_FOO, expression="1")
        b = CalcField(name="b", dataset=_DS_FOO, expression="2")
        assert len({a, b, a}) == 2


class TestColumnRefAcceptsCalcField:
    """Dim / Measure / CategoryFilter / NumericRangeFilter / TimeRangeFilter
    accept either a string column name OR a CalcField object ref. The
    CalcField ref carries the calc-field identity through the type
    checker — typos at the wiring site become compile-time errors
    (or test-time failures via the unregistered-calc-field check)."""

    def test_dim_accepts_calc_field(self):
        cf = _make_is_anchor()
        dim = Dim(dataset=_DS_FOO, field_id="f-1", column=cf)
        # emit reads name off the calc field
        emitted = dim.emit()
        assert emitted.CategoricalDimensionField is not None
        assert emitted.CategoricalDimensionField.Column.ColumnName == "is_anchor_edge"
        assert dim.calc_field() is cf

    def test_dim_accepts_bare_string(self):
        dim = Dim(dataset=_DS_FOO, field_id="f-1", column="real_column")
        emitted = dim.emit()
        assert emitted.CategoricalDimensionField is not None
        assert emitted.CategoricalDimensionField.Column.ColumnName == "real_column"
        assert dim.calc_field() is None

    def test_measure_accepts_calc_field(self):
        # BL.1 — kind="count" now emits NumericalMeasureField(SUM)
        # over a row-one CalcField (literal 1 per row) regardless of
        # the source column. The original column ref (CalcField or
        # real column) is still preserved on the Measure (via
        # ``m.calc_field()``) for the dependency walk — the CalcField
        # ref is what registers the underlying dataset as a dep — but
        # the emitted wire uses the literal-1 CalcField, not the
        # source column.
        from recon_gen.common.tree.fields import row_one_calc_name
        cf = _make_is_anchor()
        m = Measure.count(_DS_FOO, cf, field_id="f-1")
        emitted = m.emit()
        assert emitted.CategoricalMeasureField is None
        nmf = emitted.NumericalMeasureField
        assert nmf is not None
        assert nmf.Column.ColumnName == row_one_calc_name(_DS_FOO)
        assert m.calc_field() is cf  # source ref preserved on Measure

    def test_category_filter_accepts_calc_field(self):
        cf = _make_is_anchor()
        f = CategoryFilter.with_values(
            filter_id="f-1", dataset=_DS_FOO, column=cf, values=["yes"],
        )
        emitted = f.emit()
        assert emitted.CategoryFilter is not None
        assert emitted.CategoryFilter.Column.ColumnName == "is_anchor_edge"
        assert f.calc_field() is cf


class TestAnalysisAddCalcField:
    def test_add_calc_field_returns_ref(self):
        analysis = Analysis(analysis_id_suffix="t", name="T")
        cf = analysis.add_calc_field(CalcField(
            name="my_calc", dataset=_DS_FOO, expression="1 + 1",
        ))
        assert cf in analysis.calc_fields

    def test_duplicate_name_rejected(self):
        analysis = Analysis(analysis_id_suffix="t", name="T")
        analysis.add_calc_field(CalcField(
            name="dup", dataset=_DS_FOO, expression="1",
        ))
        with pytest.raises(ValueError, match="already on this Analysis"):
            analysis.add_calc_field(CalcField(
                name="dup", dataset=_DS_FOO, expression="2",
            ))

    def test_emit_definition_carries_calc_fields(self):
        analysis = Analysis(analysis_id_suffix="t", name="T")
        analysis.add_calc_field(CalcField(
            name="cf-1", dataset=_DS_FOO, expression="x",
        ))
        analysis.add_calc_field(CalcField(
            name="cf-2", dataset=_DS_FOO, expression="y",
        ))
        defn = analysis.emit_definition(datasets=[_DS_FOO])
        assert defn.CalculatedFields is not None
        assert len(defn.CalculatedFields) == 2
        assert defn.CalculatedFields[0]["Name"] == "cf-1"

    def test_no_calc_fields_emits_none(self):
        analysis = Analysis(analysis_id_suffix="t", name="T")
        defn = analysis.emit_definition(datasets=[])
        assert defn.CalculatedFields is None


class TestAppCalcFieldDependencies:
    """The L.1.8 dependency-graph extension: walk the tree to find
    every CalcField a visual or filter actually references."""

    def test_calc_fields_referenced_includes_visual_refs(self):
        cf = _make_is_anchor()
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        analysis.add_calc_field(cf)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.count(_DS_FOO, cf)],
                subtitle="t",
        )
        # Tree walks the visual and finds the calc field ref.
        assert analysis.calc_fields_referenced() == {cf}

    def test_calc_fields_referenced_includes_filter_refs(self):
        cf = _make_is_anchor()
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        analysis.add_calc_field(cf)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
                subtitle="t",
        )
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg"),
            filters=[CategoryFilter.with_values(
                filter_id="f-1", dataset=_DS_FOO, column=cf, values=["yes"],
            )],
        ))
        sheet.scope(analysis.filter_groups[-1], [kpi])
        assert analysis.calc_fields_referenced() == {cf}

    def test_emit_analysis_rejects_unregistered_calc_field(self):
        """The wrong-calc-field bug class — passing a CalcField that
        isn't registered on the Analysis. emit_analysis raises with
        the offending name."""
        cf = _make_is_anchor()  # NOT registered on the analysis
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        # Skip add_calc_field — the calc field is referenced but unregistered.
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.count(_DS_FOO, cf)],
                subtitle="t",
        )
        with pytest.raises(ValueError, match="references unregistered calc fields"):
            app.emit_analysis()

    def test_calc_field_dataset_in_dependency_graph(self):
        """A registered CalcField's Dataset participates in the App's
        dataset_dependencies — declaring a calc field on dataset D
        establishes D as a dep even when no visual touches D's columns."""
        cf = CalcField(
            name="standalone_calc", dataset=_DS_ANOMALIES, expression="1",
        )
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        app.add_dataset(_DS_ANOMALIES)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        analysis.add_calc_field(cf)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        # KPI references _DS_FOO directly; calc field references _DS_ANOMALIES
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        deps = app.dataset_dependencies()
        # Both datasets show up — _DS_FOO from the visual, _DS_ANOMALIES
        # from the registered calc field.
        assert deps == {_DS_FOO, _DS_ANOMALIES}


# ---------------------------------------------------------------------------
# L.1.8.5 — Auto-IDs for internal IDs + tree-query helpers
# ---------------------------------------------------------------------------

class TestAutoVisualIds:
    """L.1.8.5: typed Visual subtypes get auto-IDs from their position in
    the tree when the user doesn't pass one explicitly."""

    def test_kpi_without_visual_id_gets_auto_id_at_emit(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-test"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Flagged",
            values=[Measure.count(_DS_FOO, "id")],
                subtitle="t",
        )
        # visual_id defaults to AUTO until App.resolve_auto_ids() fills it
        assert kpi.visual_id is AUTO
        app.emit_analysis()
        # Now resolved
        assert kpi.visual_id == auto_id("v-kpi-s0-0")

    def test_explicit_visual_id_preserved(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            visual_id=VisualId("v-special"),
            title="Special",
                subtitle="t",
        )
        app.emit_analysis()
        assert kpi.visual_id == "v-special"

    def test_mixed_explicit_and_auto(self):
        """Explicit IDs interleave with auto-IDs without conflict —
        auto-IDs use the position-indexed scheme, explicit ones pass
        through unchanged."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        kpi_a = row.add_kpi(width=12, title="A", subtitle="t")
        kpi_b = row.add_kpi(width=12, title="B", visual_id=VisualId("v-special"), subtitle="t")
        kpi_c = row.add_kpi(width=12, title="C", subtitle="t")
        app.emit_analysis()
        assert kpi_a.visual_id == auto_id("v-kpi-s0-0")
        assert kpi_b.visual_id == "v-special"
        assert kpi_c.visual_id == auto_id("v-kpi-s0-2")

    def test_kind_prefix_distinguishes_visual_types(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        kpi = row.add_kpi(width=8, title="K", subtitle="t")
        table = row.add_table(width=8, title="T", group_by=[], values=[], subtitle="t")
        bar = row.add_bar_chart(width=8, title="B", category=[], values=[], subtitle="t")
        sankey = row.add_sankey(
            width=8, title="S",
            source=Dim(_DS_FOO, "src"),
            target=Dim(_DS_FOO, "tgt"),
            weight=Measure.sum(_DS_FOO, "amount"),
                subtitle="t",
        )
        app.emit_analysis()
        assert kpi.visual_id == auto_id("v-kpi-s0-0")
        assert table.visual_id == auto_id("v-table-s0-1")
        assert bar.visual_id == auto_id("v-bar-s0-2")
        assert sankey.visual_id == auto_id("v-sankey-s0-3")

    def test_visual_id_is_sheet_scoped(self):
        """First visual on first sheet vs first visual on second sheet —
        position resets per sheet, scope encoded in the ID prefix."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet_a = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-a"), name="A", title="A", description="test",
        ))
        sheet_b = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-b"), name="B", title="B", description="test",
        ))
        kpi_a = sheet_a.layout.row(height=6).add_kpi(width=12, title="A0", subtitle="t")
        kpi_b = sheet_b.layout.row(height=6).add_kpi(width=12, title="B0", subtitle="t")
        app.emit_analysis()
        assert kpi_a.visual_id == auto_id("v-kpi-s0-0")
        assert kpi_b.visual_id == auto_id("v-kpi-s1-0")


class TestAutoFilterGroupIds:
    def test_filter_group_without_id_gets_auto_id(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(
            filters=[_category_filter("f-1", _DS_FOO, "col")],
        ))
        sheet.scope(fg, [kpi])
        assert fg.filter_group_id is AUTO
        app.emit_analysis()
        assert fg.filter_group_id == auto_id("fg-0")

    def test_explicit_filter_group_id_preserved(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-special"),
            filters=[_category_filter("f-1", _DS_FOO, "col")],
        ))
        sheet.scope(fg, [kpi])
        app.emit_analysis()
        assert fg.filter_group_id == "fg-special"


class TestTreeQueryHelpers:
    """The L.1.8.5 introspection API. e2e tests + the dependency-graph
    walk consume these instead of importing per-app constants."""

    def _make_app(self) -> tuple[App, Sheet, KPI, Table, FilterGroup]:
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-anet"),
            name="Account Network", title="Account Network", description="test",
        ))
        row = sheet.layout.row(height=6)
        kpi = row.add_kpi(width=12, title="Flagged Pair-Windows", subtitle="t")
        table = row.add_table(
            width=24, title="Account Network — Touching Edges",
            group_by=[], values=[],
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-anchor"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [table])
        return app, sheet, kpi, table, fg

    def test_app_find_sheet_by_name(self):
        app, sheet, _, _, _ = self._make_app()
        found = app.find_sheet(name="Account Network")
        assert found is sheet

    def test_app_find_sheet_by_sheet_id(self):
        app, sheet, _, _, _ = self._make_app()
        found = app.find_sheet(sheet_id=SheetId("s-anet"))
        assert found is sheet

    def test_app_find_sheet_no_match_raises(self):
        app, _, _, _, _ = self._make_app()
        with pytest.raises(ValueError, match="No sheet"):
            app.find_sheet(name="Nonexistent")

    def test_sheet_find_visual_by_title(self):
        _app, sheet, kpi, _, _ = self._make_app()
        found = sheet.find_visual(title="Flagged Pair-Windows")
        assert found is kpi

    def test_sheet_find_visual_by_partial_title(self):
        _app, sheet, _, table, _ = self._make_app()
        found = sheet.find_visual(title_contains="Touching Edges")
        assert found is table

    def test_sheet_find_visual_no_match_raises(self):
        _app, sheet, _, _, _ = self._make_app()
        with pytest.raises(ValueError, match="No visual"):
            sheet.find_visual(title="Doesn't Exist")

    def test_sheet_find_visual_multiple_matches_raises(self):
        """When the criteria are ambiguous, the helper raises rather
        than returning a non-deterministic match."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        row.add_kpi(width=12, title="Same Title", subtitle="t")
        row.add_kpi(width=12, title="Same Title", subtitle="t")
        with pytest.raises(ValueError, match="Multiple visuals"):
            sheet.find_visual(title="Same Title")

    def test_analysis_find_filter_group_by_id(self):
        app, _, _, _, fg = self._make_app()
        assert app.analysis is not None
        found = app.analysis.find_filter_group(filter_group_id=FilterGroupId("fg-anchor"))
        assert found is fg

    def test_analysis_find_calc_field_by_name(self):
        cf = CalcField(name="my_calc", dataset=_DS_FOO, expression="1")
        analysis = Analysis(analysis_id_suffix="t", name="T")
        analysis.add_calc_field(cf)
        found = analysis.find_calc_field(name="my_calc")
        assert found is cf

    def test_analysis_find_filter_group_no_match_raises(self):
        """L.1.18 — finder raises rather than returning None on a miss."""
        app, _, _, _, _ = self._make_app()
        assert app.analysis is not None
        with pytest.raises(ValueError, match="No filter group"):
            app.analysis.find_filter_group(
                filter_group_id=FilterGroupId("fg-nonexistent"),
            )

    def test_analysis_find_calc_field_no_match_raises(self):
        """L.1.18 — finder raises rather than returning None on a miss."""
        analysis = Analysis(analysis_id_suffix="t", name="T")
        with pytest.raises(ValueError, match="No calc field"):
            analysis.find_calc_field(name="nonexistent")

    def test_analysis_find_sheet_multi_match_raises(self):
        """L.1.18 — when both name= and sheet_id= match a different sheet,
        the helper detects the ambiguous result rather than picking one."""
        analysis = Analysis(analysis_id_suffix="t", name="T")
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-1"), name="A", title="A", description="test",
        ))
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-2"), name="B", title="B", description="test",
        ))
        # name="A" matches s-1; sheet_id="s-2" matches s-2 → 2 matches.
        with pytest.raises(ValueError, match="Multiple sheets"):
            analysis.find_sheet(name="A", sheet_id=SheetId("s-2"))


# ---------------------------------------------------------------------------
# L.1.9 — Typed FilterControl + ParameterControl variants
# ---------------------------------------------------------------------------

from recon_gen.common.tree import (
    FilterCrossSheet,
    FilterDateTimePicker,
    FilterDropdown,
    FilterSlider,
    LinkedValues,
    ParameterDateTimePicker,
    ParameterDropdown,
    ParameterSlider,
    ParameterTextField,
    StaticValues,
)


class TestLinkedValues:
    """L.1.22 — factory methods normalize the two construction forms.
    The standalone constructor takes the canonical (dataset, column_name)
    pair; the dual-form `__post_init__` validation has been replaced by
    factory methods that produce the canonical pair."""

    def test_from_column_derives_dataset_from_column(self):
        from recon_gen.common.dataset_contract import (
            ColumnSpec,
            DatasetContract,
            register_contract,
        )
        ds_a = Dataset(identifier="lv-fromcol-a", arn="arn:a")
        register_contract(ds_a.identifier, DatasetContract(columns=[
            ColumnSpec(name="col", type="STRING"),
        ]))
        lv = LinkedValues.from_column(ds_a["col"])
        assert lv.dataset is ds_a
        assert lv.column_name == "col"

    def test_from_string_takes_explicit_dataset(self):
        ds = Dataset(identifier="lv-fromstr", arn="arn:s")
        lv = LinkedValues.from_string(dataset=ds, column_name="bare_col")
        assert lv.dataset is ds
        assert lv.column_name == "bare_col"


class TestParameterDropdown:
    def test_emits_with_static_values(self):
        sigma = IntegerParam(name=ParameterName("pSigma"), default=[2])
        ctrl = ParameterDropdown(
            parameter=sigma,
            title="σ Threshold",
            type="SINGLE_SELECT",
            selectable_values=StaticValues(values=["1", "2", "3", "4"]),
            control_id="pc-test",
        )
        emitted = ctrl.emit()
        assert emitted.Dropdown is not None
        assert emitted.Dropdown.SourceParameterName == "pSigma"
        assert emitted.Dropdown.Title == "σ Threshold"
        assert emitted.Dropdown.Type == "SINGLE_SELECT"
        assert emitted.Dropdown.SelectableValues == {"Values": ["1", "2", "3", "4"]}

    def test_emits_with_linked_values(self):
        anchor = StringParam(name=ParameterName("pAnchor"))
        ctrl = ParameterDropdown(
            parameter=anchor,
            title="Anchor account",
            selectable_values=LinkedValues.from_string(dataset=_DS_FOO, column_name="display"),
            hidden_select_all=True,
            control_id="pc-anchor",
        )
        emitted = ctrl.emit()
        assert emitted.Dropdown is not None
        sv = emitted.Dropdown.SelectableValues
        assert sv == {
            "LinkToDataSetColumn": {
                "DataSetIdentifier": "ds-foo",
                "ColumnName": "display",
            },
        }
        # SelectAll suppression encodes as the documented dict shape
        assert emitted.Dropdown.DisplayOptions == {
            "SelectAllOptions": {"Visibility": "HIDDEN"},
        }

    def test_linked_values_dataset_in_dependency_graph(self):
        """A ParameterDropdown's LinkedValues dataset must be registered
        on the App — same enforcement the visuals get."""
        anchor = StringParam(name=ParameterName("pAnchor"))
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        # Don't register _DS_FOO — should raise.
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        analysis.add_parameter(anchor)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.add_parameter_dropdown(parameter=anchor,
            title="Anchor",
            selectable_values=LinkedValues.from_string(dataset=_DS_FOO, column_name="d"),
        )
        with pytest.raises(ValueError, match="references unregistered datasets"):
            app.emit_analysis()


class TestParameterSlider:
    def test_emits(self):
        sigma = IntegerParam(name=ParameterName("pSigma"), default=[2])
        ctrl = ParameterSlider(
            parameter=sigma,
            title="σ",
            minimum_value=1, maximum_value=4, step_size=1,
            control_id="pc-test",
        )
        emitted = ctrl.emit()
        assert emitted.Slider is not None
        assert emitted.Slider.SourceParameterName == "pSigma"
        assert emitted.Slider.MinimumValue == 1
        assert emitted.Slider.MaximumValue == 4
        assert emitted.Slider.StepSize == 1


class TestParameterDateTimePicker:
    def test_emits(self):
        date_param = DateTimeParam(
            name=ParameterName("pDate"),
            default=DateTimeDefaultValues(StaticValues=["2030-01-01"]),
        )
        ctrl = ParameterDateTimePicker(
            parameter=date_param,
            title="Date",
            control_id="pc-date",
        )
        emitted = ctrl.emit()
        assert emitted.DateTimePicker is not None
        assert emitted.DateTimePicker.SourceParameterName == "pDate"
        assert emitted.DateTimePicker.Title == "Date"


class TestParameterTextField:
    def test_emits_when_bound_to_single_valued_param(self):
        p = StringParam(name=ParameterName("pSearch"), multi_valued=False)
        ctrl = ParameterTextField(
            parameter=p, title="Search", control_id="pc-test",
        )
        emitted = ctrl.emit()
        assert emitted.TextField is not None
        assert emitted.TextField.SourceParameterName == "pSearch"
        assert emitted.TextField.Title == "Search"

    def test_rejects_multi_valued_string_param(self):
        """Y.1.m: text-field bound to multi_valued=True is the broken
        L2FT cascade combination — silently reverts the parameter to
        default on non-empty commit. Construction must fail."""
        p = StringParam(
            name=ParameterName("pValues"),
            default=[META_VALUE_PLACEHOLDER_SENTINEL],
            multi_valued=True,
        )
        with pytest.raises(ValueError, match="multi-valued parameter"):
            ParameterTextField(
                parameter=p, title="Value", control_id="pc-test",
            )


class TestFilterDropdown:
    def test_emits_with_filter_id_resolved(self):
        f = CategoryFilter.with_values(
            filter_id="filter-anchor", dataset=_DS_FOO,
            column="col", values=["yes"],
        )
        ctrl = FilterDropdown(
            filter=f, title="Anchor",
            control_id="fc-anchor",
        )
        emitted = ctrl.emit()
        assert emitted.Dropdown is not None
        assert emitted.Dropdown.SourceFilterId == "filter-anchor"
        assert emitted.Dropdown.Title == "Anchor"

    def test_emits_with_auto_filter_id(self):
        """Filter wrapper's auto-ID resolves to a string — the dropdown
        reads it via the object ref. Tests the L.1.8.5 + L.1.9
        interaction."""
        f = CategoryFilter.with_values(
            dataset=_DS_FOO, column="col", values=["yes"],
        )  # no filter_id — auto
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(filters=[f]))
        sheet.scope(fg, [kpi])
        sheet.add_filter_dropdown(filter=f, title="A")
        app.emit_analysis()
        # Auto-IDs resolved
        assert f.filter_id == auto_id("f-category-fg0-0")
        # The dropdown picked it up
        ctrl_emitted = sheet.filter_controls[0].emit()
        assert ctrl_emitted.Dropdown is not None
        assert ctrl_emitted.Dropdown.SourceFilterId == auto_id("f-category-fg0-0")


class TestFilterSlider:
    def test_emits(self):
        sigma_param = IntegerParam(name=ParameterName("pSigma"), default=[2])
        f = NumericRangeFilter(
            filter_id="filter-sigma",
            dataset=_DS_FOO, column="z_score",
            minimum=ParameterBound(sigma_param),
        )
        ctrl = FilterSlider(
            filter=f, title="σ",
            minimum_value=1, maximum_value=4, step_size=1,
            control_id="fc-sigma",
        )
        emitted = ctrl.emit()
        assert emitted.Slider is not None
        assert emitted.Slider.SourceFilterId == "filter-sigma"


class TestFilterDateTimePicker:
    def test_emits(self):
        f = TimeRangeFilter(
            filter_id="filter-date",
            dataset=_DS_FOO, column="posted_at",
        )
        ctrl = FilterDateTimePicker(
            filter=f, title="Date Range",
            control_id="fc-date",
        )
        emitted = ctrl.emit()
        assert emitted.DateTimePicker is not None
        assert emitted.DateTimePicker.SourceFilterId == "filter-date"


class TestFilterCrossSheet:
    def test_emits_with_no_title(self):
        f = CategoryFilter.with_values(
            filter_id="filter-x", dataset=_DS_FOO,
            column="col", values=["yes"],
        )
        ctrl = FilterCrossSheet(filter=f, control_id="fc-x")
        emitted = ctrl.emit()
        assert emitted.CrossSheet is not None
        assert emitted.CrossSheet.SourceFilterId == "filter-x"


class TestControlAutoIds:
    """L.1.9 + L.1.8.5: control IDs auto-generate at emit time."""

    def test_parameter_control_auto_id(self):
        sigma = IntegerParam(name=ParameterName("pSigma"), default=[2])
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        analysis.add_parameter(sigma)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        ctrl = sheet.add_parameter_slider(parameter=sigma, title="σ",
            minimum_value=1, maximum_value=4, step_size=1,
        )
        assert ctrl.control_id is AUTO
        app.emit_analysis()
        assert ctrl.control_id == auto_id("pc-slider-s0-0")

    def test_filter_control_auto_id(self):
        f = CategoryFilter.with_values(
            filter_id="filter-x", dataset=_DS_FOO,
            column="col", values=["yes"],
        )
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(filters=[f]))
        sheet.scope(fg, [kpi])
        ctrl = sheet.add_filter_dropdown(filter=f, title="X")
        assert ctrl.control_id is AUTO
        app.emit_analysis()
        assert ctrl.control_id == auto_id("fc-dropdown-s0-0")


class TestSheetEmitsFilterControls:
    """SheetDefinition.FilterControls populated from sheet.filter_controls."""

    def test_filter_controls_appear_in_emitted_sheet(self):
        f = CategoryFilter.with_values(
            filter_id="filter-x", dataset=_DS_FOO,
            column="col", values=["yes"],
        )
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(filters=[f]))
        sheet.scope(fg, [kpi])
        sheet.add_filter_dropdown(filter=f, title="X", control_id="fc-x",
        )
        m = app.emit_analysis()
        assert m.Definition.Sheets is not None
        emitted_sheet = m.Definition.Sheets[0]
        assert emitted_sheet.FilterControls is not None
        assert len(emitted_sheet.FilterControls) == 1
        ctrl_emitted = emitted_sheet.FilterControls[0]
        assert ctrl_emitted.Dropdown is not None
        assert ctrl_emitted.Dropdown.FilterControlId == "fc-x"


# ---------------------------------------------------------------------------
# L.1.10 — Typed Drill action
# ---------------------------------------------------------------------------

from recon_gen.common.dataset_contract import ColumnShape
from recon_gen.common.tree import Drill as TreeDrill
from recon_gen.common.tree import (
    DrillParam as TreeDrillParam,
)
from recon_gen.common.tree import (
    DrillSourceField as TreeDrillSourceField,
)


class TestDrillEmit:
    def _setup(self) -> tuple[App, Sheet, Sheet, Table]:
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        analysis.add_parameter(StringParam(
            name=ParameterName("pAnchor"),
        ))
        src_sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-source"),
            name="Source", title="Source", description="test",
        ))
        dest_sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-dest"),
            name="Dest", title="Dest", description="test",
        ))
        # Set up a table on the source sheet that has a drill action
        # targeting the dest sheet.
        table = src_sheet.layout.row(height=18).add_table(
            width=36,
            title="Source Table",
            group_by=[Dim(dataset=_DS_FOO, field_id="f-acct", column="display")],
            values=[],
            actions=[TreeDrill(
                target_sheet=dest_sheet,  # OBJECT REF
                writes=[(
                    TreeDrillParam(ParameterName("pAnchor"), ColumnShape.ACCOUNT_DISPLAY),
                    TreeDrillSourceField(field_id="f-acct", shape=ColumnShape.ACCOUNT_DISPLAY),
                )],
                name="Walk to anchor",
                trigger="DATA_POINT_MENU",
            )],
                subtitle="t",
        )
        return app, src_sheet, dest_sheet, table

    def test_drill_emits_with_target_sheet_resolved(self):
        app, _, _dest_sheet, _table = self._setup()
        m = app.emit_analysis()
        # Find the source sheet in the emitted JSON
        assert m.Definition.Sheets is not None
        emitted_src = m.Definition.Sheets[0]
        assert emitted_src.Visuals is not None
        emitted_table = emitted_src.Visuals[0].TableVisual
        assert emitted_table is not None
        assert emitted_table.Actions is not None
        actions = emitted_table.Actions
        assert len(actions) == 1
        action = actions[0]
        assert action.Name == "Walk to anchor"
        assert action.Trigger == "DATA_POINT_MENU"
        # NavigationOperation should have the dest sheet's id
        nav = action.ActionOperations[0].NavigationOperation
        assert nav is not None
        assert nav.LocalNavigationConfiguration.TargetSheetId == "s-dest"

    def test_drill_action_id_auto_assigned(self):
        app, _, _, table = self._setup()
        action = table.actions[0]
        assert action.action_id is AUTO
        app.emit_analysis()
        # auto-IDed: act-s{sheet_idx}-v{visual_idx}-{action_idx}
        assert action.action_id == auto_id("act-s0-v0-0")

    def test_drill_target_sheet_must_be_registered(self):
        """Drill into a sheet that isn't on the analysis raises at
        emit time. Catches the wrong-sheet bug class — the typed
        ref means the Sheet must be a real, registered Sheet object."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        src_sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-src"),
            name="Source", title="Source", description="test",
        ))
        # An UNregistered sheet — never goes through analysis.add_sheet
        rogue_sheet = Sheet(
            sheet_id=SheetId("s-rogue"),
            name="Rogue", title="Rogue", description="test",
        )
        src_sheet.layout.row(height=18).add_table(
            width=36, title="X", group_by=[], values=[],
            actions=[TreeDrill(
                target_sheet=rogue_sheet,  # not on the analysis!
                writes=[(
                    TreeDrillParam(ParameterName("pX"), ColumnShape.ACCOUNT_ID),
                    TreeDrillSourceField(field_id="f", shape=ColumnShape.ACCOUNT_ID),
                )],
                name="Bad drill",
            )],
                subtitle="t",
        )
        with pytest.raises(ValueError, match="drill actions targeting sheets"):
            app.emit_analysis()

    def test_drill_source_calc_field_without_shape_raises(self):
        """L.1.18 — _resolve_drill_source raises TypeError when a Drill
        write reads a CalcField that has no ``shape`` tag. Catches the
        K.2-style "what shape is this column?" bug class for calc fields."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        # CalcField without a shape — drill source can't type-check.
        unshaped = analysis.add_calc_field(CalcField(
            name="counterparty", dataset=_DS_FOO, expression="ifelse(...)",
            # shape= intentionally omitted
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        # Same Dim instance is referenced in both group_by (so the
        # resolver assigns its field_id) and the drill's writes (so the
        # source-shape lookup hits the unshaped CalcField).
        unshaped_dim = Dim(_DS_FOO, unshaped)
        sheet.layout.row(height=18).add_table(
            width=36, title="X",
            group_by=[unshaped_dim],
            values=[],
            actions=[TreeDrill(
                target_sheet=sheet,  # same-sheet
                writes=[(
                    TreeDrillParam(ParameterName("pX"), ColumnShape.ACCOUNT_ID),
                    unshaped_dim,  # uses the shapeless calc
                )],
                name="Drill on calc",
            )],
                subtitle="t",
        )
        with pytest.raises(TypeError, match="has no ``shape`` tag"):
            app.emit_analysis()

    def test_explicit_action_id_preserved(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        src = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        table = src.layout.row(height=18).add_table(
            width=36, title="T", group_by=[], values=[],
            actions=[TreeDrill(
                target_sheet=src,  # same sheet — also valid
                writes=[(
                    TreeDrillParam(ParameterName("pX"), ColumnShape.ACCOUNT_ID),
                    TreeDrillSourceField(field_id="f", shape=ColumnShape.ACCOUNT_ID),
                )],
                name="Drill",
                action_id="my-explicit-id",
            )],
                subtitle="t",
        )
        app.emit_analysis()
        assert table.actions[0].action_id == "my-explicit-id"


# ---------------------------------------------------------------------------
# L.1.17 — unvalidated column refs raise unless explicitly allowed
# ---------------------------------------------------------------------------

class TestUnvalidatedColumnsRaiseByDefault:
    """``allow_bare_strings=False`` is the App's default. Two unvalidated
    column-ref forms raise at emit:

    1. **Bare string** — ``Dim(ds, "amount")`` — literal string that
       skips the contract check entirely.
    2. **Unvalidated Column** — ``ds["amount"]`` against a dataset
       with no registered ``DatasetContract``. ``Dataset.__getitem__``
       can't validate when no contract exists, so it returns a Column
       without checking. The walker catches this so the silent-pass
       path becomes a loud raise.

    The validated path: ``ds["amount"]`` against a dataset whose
    contract IS registered. ``Dataset.__getitem__`` raises ``KeyError``
    at the wiring site on typo.

    The escape hatch (``allow_bare_strings=True``) covers test fixtures
    that don't register a ``DatasetContract``.
    """

    def _build_app_with_bare_string_dim(self, **app_kwargs: Any) -> App:
        """Build a minimal App that references a column via a bare str."""
        app = App(name="t", cfg=_TEST_CFG, **app_kwargs)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            subtitle="t",
            values=[Measure.sum(_DS_FOO, "amount")],  # bare string
        )
        return app

    def test_default_app_raises_on_bare_string_column(self):
        app = self._build_app_with_bare_string_dim()  # default allow=False
        with pytest.raises(ValueError, match="unvalidated column refs"):
            app.emit_analysis()

    def test_default_app_raises_on_bare_string_column_in_dashboard(self):
        app = self._build_app_with_bare_string_dim()
        app.create_dashboard(dashboard_id_suffix="d", name="D")
        with pytest.raises(ValueError, match="unvalidated column refs"):
            app.emit_dashboard()

    def test_explicit_allow_bypasses_check(self):
        """Tests + datasets without a contract opt into the bare-string
        form via ``allow_bare_strings=True``."""
        app = self._build_app_with_bare_string_dim(allow_bare_strings=True)
        # Should not raise.
        app.emit_analysis()

    def test_error_message_lists_offending_column(self):
        app = self._build_app_with_bare_string_dim()
        with pytest.raises(ValueError) as exc_info:
            app.emit_analysis()
        message = str(exc_info.value)
        # The bad column name + the visual id appear in the message
        # so the developer can fix at the right call site.
        assert "amount" in message
        # Mentions the typed alternative the user should reach for.
        assert "ds[\"" in message

    def test_bare_string_in_filter_column_also_raises(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f")],
                subtitle="t",
        )
        analysis.add_filter_group(FilterGroup(
            filters=[CategoryFilter.with_values(
                dataset=_DS_FOO,
                column="category",  # bare string
                values=["a"],
            )],
        ))
        sheet.scope(analysis.filter_groups[-1], [kpi])
        # Flip to default-strict for the assertion run.
        app.allow_bare_strings = False
        with pytest.raises(ValueError, match="unvalidated column refs"):
            app.emit_analysis()

    def test_unvalidated_column_ref_raises(self):
        """``ds["col"]`` on a dataset without a registered DatasetContract
        is the OTHER escape hatch — Column produced but never validated.
        The walker catches it at emit unless ``allow_bare_strings=True``.
        """
        app = App(name="t", cfg=_TEST_CFG)  # default allow=False
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        # _DS_FOO has no registered DatasetContract. ds["amount"] returns
        # a Column without validation — the walker should catch it.
        sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[_DS_FOO["amount"].sum()],
                subtitle="t",
        )
        with pytest.raises(ValueError) as exc_info:
            app.emit_analysis()
        message = str(exc_info.value)
        assert "no registered DatasetContract" in message
        assert _DS_FOO.identifier in message
        assert "amount" in message

    def test_unvalidated_column_in_filter_also_raises(self):
        """The same gap applies to filter columns — ds["col"] on a
        contract-less dataset slips through unless caught here."""
        app = App(name="t", cfg=_TEST_CFG)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f")],
                subtitle="t",
        )
        # Wrap KPI in a FilterGroup to keep the visuals validation
        # path away from the value-leaf bare string.
        # The relevant unvalidated path is the filter's column.
        analysis.add_filter_group(FilterGroup(
            filters=[CategoryFilter.with_values(
                dataset=_DS_FOO,
                column=_DS_FOO["category"],  # unvalidated Column
                values=["a"],
            )],
        ))
        sheet.scope(analysis.filter_groups[-1], [kpi])
        # Allow bare strings so the KPI's bare-string measure column
        # doesn't trip the check; this isolates the filter's
        # unvalidated Column path. (In real production code both
        # checks should fire — the test isolates them so each path is
        # exercised independently.)
        app.allow_bare_strings = True
        # Filter's Column path bypasses the bare-string check too,
        # though — so we need the strict mode to catch it.
        app.allow_bare_strings = False
        # Drop the bare-string KPI value so only the filter path is bad.
        kpi.values = [_DS_FOO["amount"].sum()]
        with pytest.raises(ValueError) as exc_info:
            app.emit_analysis()
        message = str(exc_info.value)
        assert "no registered DatasetContract" in message
        # Both unvalidated columns surface (the KPI value AND the
        # filter column) — the message names both.
        assert "amount" in message
        assert "category" in message

    def test_explicit_allow_bypasses_unvalidated_column_too(self):
        """``allow_bare_strings=True`` covers BOTH unvalidated forms —
        the bare-string path and the contract-less Column path."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[_DS_FOO["amount"].sum()],
                subtitle="t",
        )
        # Should not raise.
        app.emit_analysis()
