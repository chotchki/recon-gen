"""L.1.10.6 — Kitchen-sink app that uses every typed L.1 primitive.

Persona-agnostic minimal App for testing the tree itself (vs. testing
PR / AR / Investigation scenarios). Sheets are deliberately generic:

- Sheet 1 (Visuals Showcase): one of each typed Visual subtype
  (KPI / Table / BarChart / Sankey).
- Sheet 2 (Filters & Controls): every typed Filter wrapper +
  Parameter / Filter control variant. CategoryFilter binds to a
  CalcField; NumericRangeFilter is parameter-bound.
- Sheet 3 (Drill Target): single Table that's the destination of
  drill actions wired from Sheet 1's BarChart and Table.

Every typed primitive appears at least once. New typed primitives
we add later should add a usage here so the kitchen-sink stays
"complete coverage" by definition.

Used by:
- ``tests/test_kitchen_app.py`` — unit tests that build + emit the
  app and verify the resulting JSON contains every primitive.
- ``tests/e2e/test_tree_primitives.py`` (future, post-L.2) — e2e
  test that deploys + browser-validates via ``TreeValidator``. Needs
  the L.2 tree-to-files bridging plumbing to deploy through the
  existing CLI; until then the app is unit-test-only.
"""

from __future__ import annotations

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import ColumnShape
from recon_gen.common.models import DateTimeDefaultValues
from recon_gen.common.ids import (
    FilterGroupId as FilterGroupId,
    ParameterName,
    SheetId,
    VisualId as VisualId,
)
from recon_gen.common.tree import (
    Analysis,
    App,
    BarChart as BarChart,
    CalcField,
    CategoryFilter,
    Dashboard as Dashboard,
    Dataset,
    DateTimeParam,
    Dim,
    Drill,
    DrillParam,
    DrillSourceField,
    FilterCrossSheet as FilterCrossSheet,
    FilterDateTimePicker as FilterDateTimePicker,
    FilterDropdown as FilterDropdown,
    FilterGroup,
    FilterSlider as FilterSlider,
    IntegerParam,
    KPI as KPI,
    LinkedValues,
    Measure,
    NumericRangeFilter,
    ParameterBound,
    ParameterDateTimePicker as ParameterDateTimePicker,
    ParameterDropdown as ParameterDropdown,
    ParameterSlider as ParameterSlider,
    Sankey as Sankey,
    Sheet,
    StaticValues,
    StringParam,
    Table as Table,
    TimeRangeFilter,
)


def build_kitchen_app(cfg: Config) -> App:
    """Construct the kitchen-sink App.

    Returns the App ready for ``app.emit_analysis()`` /
    ``app.emit_dashboard()``. Caller may register additional datasets
    or modify before emitting; the default returned shape is
    self-contained and exercises every primitive at least once.
    """
    # Kitchen sink doesn't register a DatasetContract for its datasets,
    # so ds["col"] can't validate. Opt into the bare-string escape
    # hatch so the existing Dim(ds, "col") form survives.
    app = App(name="tree-kitchen", cfg=cfg, allow_bare_strings=True)

    # ------ Datasets -------------------------------------------------
    # Two datasets — one for visual data, one for the dropdown
    # LinkedValues column. Real apps deploy these as actual QuickSight
    # DataSets; the unit tests just confirm they appear in
    # DataSetIdentifierDeclarations.
    ds_main = app.add_dataset(Dataset(
        identifier="kitchen-main-ds",
        arn="arn:aws:quicksight:::dataset/kitchen-main",
    ))
    ds_categories = app.add_dataset(Dataset(
        identifier="kitchen-categories-ds",
        arn="arn:aws:quicksight:::dataset/kitchen-categories",
    ))

    # ------ Analysis -------------------------------------------------
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="tree-kitchen-analysis",
        name="Tree Kitchen Sink",
    ))

    # ------ Parameters (all three variants) --------------------------
    p_category = analysis.add_parameter(StringParam(
        name=ParameterName("pKitchenCategory"),
    ))
    p_threshold = analysis.add_parameter(IntegerParam(
        name=ParameterName("pKitchenThreshold"),
        default=[10],
    ))
    p_date = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pKitchenDate"),
        default=DateTimeDefaultValues(
            RollingDate={"Expression": "truncDate('DD', now())"},
        ),
    ))

    # ------ Calc field -----------------------------------------------
    is_above_threshold = analysis.add_calc_field(CalcField(
        name="is_above_threshold",
        dataset=ds_main,
        expression=(
            "ifelse({amount} > ${pKitchenThreshold}, 'yes', 'no')"
        ),
    ))

    # ================================================================
    # Sheet 1 — Visuals Showcase (one of each typed Visual kind)
    # ================================================================
    showcase = analysis.add_sheet(Sheet(
        sheet_id=SheetId("kitchen-sheet-showcase"),
        name="Visuals Showcase",
        title="Visuals Showcase",
        description="One of every typed Visual subtype.",
    ))

    # Row 1: KPI ⅓ + detail table ⅔.
    # Tree-level vars for the leaves so drills + sort_by reference
    # them by object ref (no string field_ids needed for routing).
    # Drill-source leaves keep an explicit field_id only because the
    # kitchen sink isn't registered with a dataset contract — the
    # drill resolver can't auto-derive ColumnShape, so the kitchen-sink
    # uses the explicit DrillSourceField escape-hatch path.
    tbl_name_dim = Dim(ds_main, "name", field_id="kitchen-tbl-name")
    tbl_amount_measure = Measure.sum(ds_main, "amount")
    showcase_row1 = showcase.layout.row(height=6)
    showcase_row1.add_kpi(
        width=8,
        title="Total Amount",
        subtitle="SUM of amount",
        values=[Measure.sum(ds_main, "amount")],
    )
    table = showcase_row1.add_table(
        width=28,
        title="Detail Table",
        subtitle="GroupBy + Values",
        group_by=[
            Dim(ds_main, "id"),
            tbl_name_dim,
            # Calc-field reference (ColumnRef union)
            Dim(ds_main, is_above_threshold),
        ],
        values=[tbl_amount_measure],
        sort_by=(tbl_amount_measure, "DESC"),
    )

    # Row 2: bar chart ½ + sankey ½.
    bar_cat_dim = Dim(ds_main, "category", field_id="kitchen-bar-cat")
    sankey_source_dim = Dim(
        ds_main, "source_account", field_id="kitchen-sk-source",
    )
    showcase_row2 = showcase.layout.row(height=12)
    bar = showcase_row2.add_bar_chart(
        width=18,
        title="By Category",
        subtitle="Counts per category",
        category=[bar_cat_dim],
        values=[Measure.count(ds_main, "id")],
    )
    sankey = showcase_row2.add_sankey(
        width=18,
        title="Flow",
        subtitle="Source → Target by amount",
        source=sankey_source_dim,
        target=Dim(ds_main, "target_account"),
        weight=Measure.sum(ds_main, "amount"),
        items_limit=25,
    )

    # ================================================================
    # Sheet 2 — Filters & Controls (every Filter wrapper + control
    # variant)
    # ================================================================
    filters_sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("kitchen-sheet-filters"),
        name="Filters and Controls",
        title="Filters and Controls",
        description="Every typed Filter + Control variant.",
    ))

    # A target visual for the filter group scope.
    filtered_table = filters_sheet.layout.row(height=18).add_table(
        width=36,
        title="Filtered Detail",
        group_by=[Dim(ds_main, "id")],
        values=[Measure.sum(ds_main, "amount")],
        subtitle="t",
    )

    # Filter wrappers — one of each kind.
    cat_filter = CategoryFilter.with_values(
        dataset=ds_main, column="category",
        values=["a", "b", "c"], match_operator="CONTAINS",
    )
    num_filter = NumericRangeFilter(
        dataset=ds_main, column="amount",
        minimum=ParameterBound(p_threshold),  # parameter-bound
    )
    time_filter = TimeRangeFilter(
        dataset=ds_main, column="posted_at",
    )
    # Calc-field-backed CategoryFilter — same pattern as
    # is_anchor_edge in Investigation.
    calc_filter = CategoryFilter.with_values(
        dataset=ds_main, column=is_above_threshold,  # CalcField ref
        values=["yes"],
    )

    filters_sheet.scope(
        analysis.add_filter_group(FilterGroup(
            filters=[cat_filter, num_filter, time_filter, calc_filter],
        )),
        [filtered_table],
    )

    # Parameter controls — one of each kind.
    filters_sheet.add_parameter_dropdown(
        parameter=p_category,
        title="Category (Static)",
        type="MULTI_SELECT",
        selectable_values=StaticValues(values=["a", "b", "c"]),
    )
    filters_sheet.add_parameter_dropdown(
        parameter=p_category,
        title="Category (Linked)",
        type="SINGLE_SELECT",
        selectable_values=LinkedValues.from_string(
            dataset=ds_categories, column_name="category",
        ),
        hidden_select_all=True,
    )
    filters_sheet.add_parameter_slider(
        parameter=p_threshold,
        title="Threshold",
        minimum_value=0, maximum_value=1000, step_size=10,
    )
    filters_sheet.add_parameter_datetime_picker(
        parameter=p_date,
        title="Date",
    )

    # Filter controls — one of each kind.
    filters_sheet.add_filter_dropdown(
        filter=cat_filter,
        title="Category Filter",
        type="MULTI_SELECT",
    )
    filters_sheet.add_filter_slider(
        filter=num_filter,
        title="Amount Range",
        minimum_value=0, maximum_value=1000, step_size=10,
        type="RANGE",
    )
    filters_sheet.add_filter_datetime_picker(
        filter=time_filter,
        title="Date Range",
        type="DATE_RANGE",
    )
    filters_sheet.add_filter_cross_sheet(filter=cat_filter)

    # ================================================================
    # Sheet 3 — Drill Target (drill destination from Sheet 1 visuals)
    # ================================================================
    drill_target = analysis.add_sheet(Sheet(
        sheet_id=SheetId("kitchen-sheet-drill-target"),
        name="Drill Target",
        title="Drill Target",
        description="Destination for drill actions from Visuals Showcase.",
    ))

    drill_target.layout.row(height=18).add_table(
        width=36,
        title="Drill Destination",
        group_by=[Dim(ds_main, "id")],
        values=[Measure.sum(ds_main, "amount")],
        subtitle="t",
    )

    # ------ Drill actions -------------------------------------------
    # Wire drill actions from Sheet 1 visuals to Sheet 3.
    # BarChart, Table, Sankey support Actions; KPI doesn't (per the
    # QuickSight model).
    drill_param = DrillParam(
        ParameterName("pKitchenCategory"), ColumnShape.ACCOUNT_DISPLAY,
    )

    bar.actions.append(Drill(
        target_sheet=drill_target,
        writes=[(drill_param, DrillSourceField(
            field_id="kitchen-bar-cat", shape=ColumnShape.ACCOUNT_DISPLAY,
        ))],
        name="Drill into category",
        trigger="DATA_POINT_MENU",
    ))

    table.actions.append(Drill(
        target_sheet=drill_target,
        writes=[(drill_param, DrillSourceField(
            field_id="kitchen-tbl-name", shape=ColumnShape.ACCOUNT_DISPLAY,
        ))],
        name="Drill into name",
        trigger="DATA_POINT_CLICK",
    ))

    sankey.actions.append(Drill(
        target_sheet=drill_target,
        writes=[(drill_param, DrillSourceField(
            field_id="kitchen-sk-source", shape=ColumnShape.ACCOUNT_DISPLAY,
        ))],
        name="Drill from source",
        trigger="DATA_POINT_CLICK",
    ))

    # ------ Dashboard -----------------------------------------------
    app.create_dashboard(
        dashboard_id_suffix="tree-kitchen-dashboard",
        name="Tree Kitchen Sink",
    )

    return app
