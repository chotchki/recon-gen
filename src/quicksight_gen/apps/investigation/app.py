"""Tree-based builder for the Investigation App (L.2 port).

Replaces the constant-heavy + manually-cross-referenced builders in
``apps/investigation/{analysis,filters,visuals}.py`` with the typed
tree primitives from ``common/tree/``. Sheets land one per L.2 sub-step:

- L.2.1 — Getting Started (text boxes only, app-level skeleton)
- L.2.2 — Recipient Fanout (3 KPIs + ranked table + threshold slider +
  date range filter)
- L.2.3 — Volume Anomalies
- L.2.4 — Money Trail
- L.2.5 — Account Network (already validated through L.0 + L.1.15)
- L.2.6 — App-level wiring: dashboard + dataset declarations
"""

from __future__ import annotations

from quicksight_gen.apps.investigation.constants import (
    CF_INV_ANETWORK_COUNTERPARTY_DISPLAY,
    CF_INV_ANETWORK_IS_ANCHOR_EDGE,
    CF_INV_ANETWORK_IS_INBOUND_EDGE,
    CF_INV_ANETWORK_IS_OUTBOUND_EDGE,
    CF_INV_FANOUT_DISTINCT_SENDERS,
    DS_INV_ACCOUNT_NETWORK,
    DS_INV_ANETWORK_ACCOUNTS,
    DS_INV_MONEY_TRAIL,
    DS_INV_RECIPIENT_FANOUT,
    DS_INV_VOLUME_ANOMALIES,
    FG_INV_ANETWORK_AMOUNT,
    FG_INV_ANETWORK_ANCHOR,
    FG_INV_ANETWORK_INBOUND,
    FG_INV_ANETWORK_OUTBOUND,
    FG_INV_ANOMALIES_SIGMA,
    FG_INV_ANOMALIES_WINDOW,
    FG_INV_FANOUT_THRESHOLD,
    FG_INV_FANOUT_WINDOW,
    FG_INV_MONEY_TRAIL_AMOUNT,
    FG_INV_MONEY_TRAIL_HOPS,
    FG_INV_MONEY_TRAIL_ROOT,
    FG_INV_MONEY_TRAIL_WINDOW,
    P_INV_ANETWORK_ANCHOR,
    P_INV_ANETWORK_MIN_AMOUNT,
    P_INV_ANOMALIES_SIGMA,
    P_INV_FANOUT_THRESHOLD,
    P_INV_MONEY_TRAIL_MAX_HOPS,
    P_INV_MONEY_TRAIL_MIN_AMOUNT,
    P_INV_MONEY_TRAIL_ROOT,
    SHEET_INV_ACCOUNT_NETWORK,
    SHEET_INV_ANOMALIES,
    SHEET_INV_APP_INFO,
    SHEET_INV_FANOUT,
    SHEET_INV_GETTING_STARTED,
    SHEET_INV_MONEY_TRAIL,
)
# Importing datasets registers each Investigation DatasetContract via its
# module-level register_contract() side effect — required so the L.1.17
# bare-string / unvalidated-Column emit-time validator can resolve every
# ds["col"] ref in the visuals below. Without this, build_investigation_app()
# would only work after some other module (CLI, test_investigation) had
# loaded datasets first.
from dataclasses import replace

from quicksight_gen.apps.investigation import datasets as _register_contracts  # noqa: F401
# N.3.f: Investigation reads the same default institution YAML as L1
# (per the N.2 audit's "one institution YAML drives all apps" framing).
# The default lives under apps/l1_dashboard/ for now because L1 was the
# first app L2-fed; the path will be neutralized when the spec/scenario
# YAML split lands (Phase O candidate).
from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
from quicksight_gen.common.dataset_contract import ColumnShape
from quicksight_gen.common import rich_text as rt
from quicksight_gen.common.config import Config
from quicksight_gen.common.l2 import L2Instance, ThemePreset
from quicksight_gen.common.sheets.app_info import (
    APP_INFO_SHEET_DESCRIPTION,
    APP_INFO_SHEET_NAME,
    APP_INFO_SHEET_TITLE,
    DS_APP_INFO_LIVENESS,
    DS_APP_INFO_MATVIEWS,
    build_liveness_dataset,
    build_matview_status_dataset,
    populate_app_info_sheet,
)
from quicksight_gen.common.theme import resolve_l2_theme
from quicksight_gen.common.models import Analysis as ModelAnalysis
from quicksight_gen.common.models import Dashboard as ModelDashboard
from quicksight_gen.common.tree import (
    KPI,
    Analysis,
    App,
    BarChart,
    CalcField,
    CategoryFilter,
    Dashboard,
    Dataset,
    Dim,
    Drill,
    DrillParam,
    FilterDateTimePicker,
    FilterGroup,
    IntegerParam,
    LinkedValues,
    Measure,
    NumericRangeFilter,
    ParameterBound,
    ParameterDropdown,
    ParameterSlider,
    Sankey,
    Sheet,
    StringParam,
    Table,
    TextBox,
    TimeRangeFilter,
)


# Layout constants mirror apps/investigation/analysis.py.
_FULL = 36
_THIRD = 12
_KPI_ROW_SPAN = 6
_TABLE_ROW_SPAN = 18


# Fanout-specific defaults (imperative builder mirrors these in filters.py).
_DEFAULT_FANOUT_THRESHOLD = 5
_FANOUT_SLIDER_MIN = 1
_FANOUT_SLIDER_MAX = 20

# Anomalies-specific defaults.
_DEFAULT_ANOMALIES_SIGMA = 2
_SIGMA_SLIDER_MIN = 1
_SIGMA_SLIDER_MAX = 4

# Money Trail defaults. Max hops 5 covers the 4-hop PR chain
# (`external_txn → payment → settlement → sale`) with one hop of
# headroom; >10 means the matview's recursive walk went pathological
# and the analyst should be looking at data integrity, not the trail.
_DEFAULT_MONEY_TRAIL_MAX_HOPS = 5
_HOPS_SLIDER_MIN = 1
_HOPS_SLIDER_MAX = 10
_DEFAULT_MONEY_TRAIL_MIN_AMOUNT = 0
_AMOUNT_SLIDER_MIN = 0
_AMOUNT_SLIDER_MAX = 1000

# Sankey items-limit shape: cap distinct source / destination nodes the
# diagram renders. Set generously here — the chain root filter narrows
# to one chain, so the realistic cap is "chain depth" not "every account
# in the system".
_SANKEY_NODE_CAP = 50


# ---------------------------------------------------------------------------
# Sheet descriptions (shared with imperative side — byte-identity only
# cares about the string content, not where it's constructed).
# ---------------------------------------------------------------------------

_FANOUT_DESCRIPTION = (
    "Who is receiving money from an unusual number of distinct senders? "
    "Drag the slider to set the minimum sender count; the table ranks "
    "qualifying recipients by funnel width."
)

_ANOMALY_DESCRIPTION = (
    "Which sender → recipient pair just spiked above its baseline? "
    "Rolling 2-day SUM per pair vs. the population mean + standard "
    "deviation. Drag the σ slider to flag the tail. The distribution "
    "chart shows the full population — your slider cutoff against that "
    "shape — while the KPI + table show only flagged windows."
)

_MONEY_TRAIL_DESCRIPTION = (
    "Where did this transfer actually originate, and where does it go? "
    "Pick a chain root from the dropdown — the Sankey renders that "
    "chain's source-to-target ribbons, and the hop-by-hop table beside "
    "it lists every edge ordered by depth. Single-leg transfers (sales, "
    "raw external arrivals) appear as chain members but don't contribute "
    "Sankey ribbons."
)

_ACCOUNT_NETWORK_DESCRIPTION = (
    "Who does this account exchange money with? Pick an anchor account "
    "from the dropdown — the LEFT Sankey shows counterparties sending "
    "money INTO the anchor; the RIGHT Sankey shows the anchor sending "
    "money OUT to counterparties; the anchor visually meets in the "
    "middle. The table below lists every touching edge ordered by "
    "amount. Right-click any row and pick \"Walk to other account on "
    "this edge\" — the anchor moves to the counterparty and the chart "
    "re-renders. The dropdown widget above may briefly lag behind a "
    "walk; trust the chart, not the control text. Same matview as "
    "Money Trail, viewed account-centrically rather than chain-"
    "centrically."
)


# ---------------------------------------------------------------------------
# Getting Started (L.2.1)
# ---------------------------------------------------------------------------

def _build_getting_started_sheet(
    cfg: Config, analysis: Analysis, *, theme: ThemePreset,
) -> Sheet:
    """Getting Started — landing page with welcome + roadmap text boxes.

    Two full-width text boxes stacked top-to-bottom. No visuals,
    no controls, no filters. The simplest sheet on Investigation —
    its job in L.2.1 is to land the app-level skeleton (App + Analysis +
    text-box layout slot support) so subsequent sheet ports snap in.

    N.3.g: ``theme`` is the L2-resolved theme.
    """
    accent = theme.accent

    sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_INV_GETTING_STARTED,
        name="Getting Started",
        title="Getting Started",
        description=(
            "Landing page — summarises each tab in this dashboard. "
            "No filters or visuals."
        ),
    ))

    sheet.layout.row(height=5).add_text_box(
        TextBox(
            text_box_id="inv-gs-welcome",
            content=rt.text_box(
                rt.inline(
                    "Investigation Dashboard",
                    font_size="36px",
                    color=accent,
                ),
                rt.BR,
                rt.BR,
                rt.markdown(
                    "Compliance / AML triage surface for the Sasquatch "
                    "National Bank shared base ledger. Three question-shaped "
                    "sheets — recipient fanout, volume anomalies, and money "
                    "trail — each one drilling back into Account "
                    "Reconciliation or Payment Reconciliation for the row "
                    "evidence."
                ),
            ),
        ),
        width=_FULL,
    )
    sheet.layout.row(height=6).add_text_box(
        TextBox(
            text_box_id="inv-gs-roadmap",
            content=rt.text_box(
                rt.heading("Sheets in this dashboard", color=accent),
                rt.BR,
                rt.BR,
                rt.bullets([
                    "Recipient Fanout — who is receiving money from too many "
                    "distinct senders? (live)",
                    "Volume Anomalies — which sender → recipient pair just "
                    "spiked above the rolling baseline? (live)",
                    "Money Trail — where did this transfer originate and "
                    "where does it go? (live)",
                    "Account Network — who does this account exchange money "
                    "with, on either side? (live)",
                ]),
            ),
        ),
        width=_FULL,
    )

    return sheet


# ---------------------------------------------------------------------------
# Recipient Fanout (L.2.2)
# ---------------------------------------------------------------------------

def _build_recipient_fanout_sheet(
    cfg: Config, app: App, analysis: Analysis,
) -> Sheet:
    """Recipient Fanout — 3 KPIs + ranked table.

    Registers the fanout dataset + integer parameter + analysis-level
    calc field that backs the threshold filter. Builds 3 KPIs
    (qualifying recipients / distinct senders / total inbound) plus a
    recipient-grain ranked table. Wires the threshold slider (parameter
    control) + date range picker (filter control). Scopes both filter
    groups to this sheet.

    Layout: 3 KPIs across Row 1 (each ⅓ width), table full-width on
    Row 2.
    """
    del cfg  # reserved for theme-driven styling in later sub-steps

    ds_fanout = app.add_dataset(Dataset(
        identifier=DS_INV_RECIPIENT_FANOUT,
        arn=app.cfg.dataset_arn(app.cfg.prefixed("inv-recipient-fanout-dataset")),
    ))

    threshold_param = analysis.add_parameter(IntegerParam(
        name=P_INV_FANOUT_THRESHOLD,
        default=[_DEFAULT_FANOUT_THRESHOLD],
    ))

    # Calc field name kept explicit (CF_INV_FANOUT_DISTINCT_SENDERS)
    # because analysts see the column name in the table header.
    distinct_senders_calc = analysis.add_calc_field(CalcField(
        name=CF_INV_FANOUT_DISTINCT_SENDERS,
        dataset=ds_fanout,
        expression=(
            "distinct_count({sender_account_id}, "
            "[{recipient_account_id}])"
        ),
    ))

    sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_INV_FANOUT,
        name="Recipient Fanout",
        title="Recipient Fanout",
        description=_FANOUT_DESCRIPTION,
    ))

    # Row 1: 3 KPIs each ⅓ width.
    kpi_row = sheet.layout.row(height=_KPI_ROW_SPAN)
    kpi_row.add_kpi(
        width=_THIRD,
        title="Qualifying Recipients",
        subtitle="Distinct recipients meeting the fanout threshold.",
        values=[ds_fanout["recipient_account_id"].distinct_count()],
    )
    kpi_row.add_kpi(
        width=_THIRD,
        title="Distinct Senders",
        subtitle=(
            "Distinct sender accounts feeding the qualifying recipients."
        ),
        values=[ds_fanout["sender_account_id"].distinct_count()],
    )
    kpi_row.add_kpi(
        width=_THIRD,
        title="Total Inbound",
        subtitle=(
            "Sum of inbound amounts across qualifying recipient legs."
        ),
        values=[ds_fanout["amount"].sum(currency=True)],
    )

    # Row 2: ranked table full-width.
    distinct_senders_value = Measure.max(ds_fanout, distinct_senders_calc)
    sheet.layout.row(height=_TABLE_ROW_SPAN).add_table(
        width=_FULL,
        title="Recipient Fanout — Ranked",
        subtitle=(
            "One row per recipient. Ranked by distinct sender count "
            "(highest = widest funnel)."
        ),
        group_by=[
            ds_fanout["recipient_account_id"].dim(),
            ds_fanout["recipient_account_name"].dim(),
            ds_fanout["recipient_account_type"].dim(),
        ],
        values=[
            distinct_senders_value,
            ds_fanout["transfer_id"].distinct_count(),
            ds_fanout["amount"].sum(currency=True),
        ],
        sort_by=(distinct_senders_value, "DESC"),
    )

    # Date-range window on posted_at — ALL visuals on this sheet. Narrow
    # scope: fanout sheet only, not cross-sheet.
    window_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_FANOUT_WINDOW,
        filters=[TimeRangeFilter(
            filter_id="filter-inv-fanout-window",
            dataset=ds_fanout,
            column=ds_fanout["posted_at"],
            null_option="NON_NULLS_ONLY",
            time_granularity="DAY",
        )],
    ))
    window_fg.scope_sheet(sheet)

    # Threshold on the distinct-senders calc field, min-only, parameter-bound.
    # IncludeMinimum=True matches "slider value = visible cutoff".
    threshold_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_FANOUT_THRESHOLD,
        filters=[NumericRangeFilter(
            filter_id="filter-inv-fanout-threshold",
            dataset=ds_fanout,
            column=distinct_senders_calc,
            minimum=ParameterBound(threshold_param),
            null_option="NON_NULLS_ONLY",
            include_minimum=True,
        )],
    ))
    threshold_fg.scope_sheet(sheet)

    # Sheet controls: date range picker + threshold slider.
    sheet.add_filter_datetime_picker(
        filter=window_fg.filters[0],
        title="Date Range",
        type="DATE_RANGE",
        control_id="ctrl-inv-fanout-window",
    )
    sheet.add_parameter_slider(
        parameter=threshold_param,
        title="Min distinct senders",
        minimum_value=_FANOUT_SLIDER_MIN,
        maximum_value=_FANOUT_SLIDER_MAX,
        step_size=1,
        control_id="ctrl-inv-fanout-threshold",
    )

    return sheet


# ---------------------------------------------------------------------------
# Volume Anomalies (L.2.3)
# ---------------------------------------------------------------------------

def _build_volume_anomalies_sheet(
    cfg: Config, app: App, analysis: Analysis,
) -> Sheet:
    """Volume Anomalies — KPI flagged-count + σ distribution + ranked table.

    Load-bearing case for the tree's scope API: the σ filter scopes
    SELECTED_VISUALS (KPI + table only) so the distribution bar chart
    keeps rendering the full population. The chart's job is the
    reference frame — see where 2σ vs. 4σ falls in the overall shape
    before deciding where to set the slider.

    Layout:
      * Row 1: KPI flagged count (⅓ width) + distribution bar chart
        (⅔ width, 2× row span so it has room for the buckets).
      * Row 2: full-width flagged table sorted by z_score desc.
    """
    del cfg

    ds_anomalies = app.add_dataset(Dataset(
        identifier=DS_INV_VOLUME_ANOMALIES,
        arn=app.cfg.dataset_arn(app.cfg.prefixed("inv-volume-anomalies-dataset")),
    ))

    sigma_param = analysis.add_parameter(IntegerParam(
        name=P_INV_ANOMALIES_SIGMA,
        default=[_DEFAULT_ANOMALIES_SIGMA],
    ))

    sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_INV_ANOMALIES,
        name="Volume Anomalies",
        title="Volume Anomalies",
        description=_ANOMALY_DESCRIPTION,
    ))

    # Row 1: KPI ⅓ + σ distribution ⅔. Distribution is taller (bucket
    # bars need the extra vertical space); the row band is sized to fit
    # the chart, KPI cell expands to match the row height.
    row1 = sheet.layout.row(height=_KPI_ROW_SPAN * 2)
    kpi_flagged = row1.add_kpi(
        width=_THIRD,
        title="Flagged Pair-Windows",
        subtitle=(
            "Pair-windows whose 2-day rolling SUM clears the σ threshold."
        ),
        values=[ds_anomalies["recipient_account_id"].count()],
    )
    dist_bucket_dim = ds_anomalies["z_bucket"].dim()
    row1.add_bar_chart(
        width=_THIRD * 2,
        title="Pair-Window σ Distribution",
        subtitle=(
            "Pair-windows bucketed by |z-score| against the population "
            "mean. Chart is intentionally NOT filtered by the σ slider."
        ),
        category=[dist_bucket_dim],
        values=[ds_anomalies["recipient_account_id"].count()],
        category_label="Sigma Bucket",
        value_label="Pair-Windows",
        orientation="VERTICAL",
        bars_arrangement="CLUSTERED",
        sort_by=(dist_bucket_dim, "ASC"),
    )

    # Row 2: ranked table full-width.
    z_score_max = ds_anomalies["z_score"].max()
    table = sheet.layout.row(height=_TABLE_ROW_SPAN).add_table(
        width=_FULL,
        title="Flagged Pair-Windows — Ranked",
        subtitle=(
            "One row per flagged 2-day window. Ranked by z-score "
            "(highest = furthest from the population mean)."
        ),
        group_by=[
            ds_anomalies["recipient_account_id"].dim(),
            ds_anomalies["recipient_account_name"].dim(),
            ds_anomalies["sender_account_id"].dim(),
            ds_anomalies["sender_account_name"].dim(),
            ds_anomalies["window_end"].date(),
        ],
        values=[
            z_score_max,
            ds_anomalies["window_sum"].max(currency=True),
            ds_anomalies["transfer_count"].max(),
        ],
        sort_by=(z_score_max, "DESC"),
    )

    # Window date-range filter: ALL visuals on this sheet (chart + KPI +
    # table all narrow with the date range so the chart's shape stays
    # tied to what the analyst is investigating).
    window_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_ANOMALIES_WINDOW,
        filters=[TimeRangeFilter(
            filter_id="filter-inv-anomalies-window",
            dataset=ds_anomalies,
            column=ds_anomalies["window_end"],
            null_option="NON_NULLS_ONLY",
            time_granularity="DAY",
        )],
    ))
    window_fg.scope_sheet(sheet)

    # σ threshold: SELECTED_VISUALS — KPI + table only. The distribution
    # chart stays unfiltered so it can show the full population shape.
    # This is the load-bearing scope-by-visuals case for L.2.3.
    sigma_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_ANOMALIES_SIGMA,
        filters=[NumericRangeFilter(
            filter_id="filter-inv-anomalies-sigma",
            dataset=ds_anomalies,
            column=ds_anomalies["z_score"],
            minimum=ParameterBound(sigma_param),
            null_option="NON_NULLS_ONLY",
            include_minimum=True,
        )],
    ))
    sheet.scope(sigma_fg, [kpi_flagged, table])

    sheet.add_filter_datetime_picker(
        filter=window_fg.filters[0],
        title="Window End Date",
        type="DATE_RANGE",
        control_id="ctrl-inv-anomalies-window",
    )
    sheet.add_parameter_slider(
        parameter=sigma_param,
        title="Min sigma",
        minimum_value=_SIGMA_SLIDER_MIN,
        maximum_value=_SIGMA_SLIDER_MAX,
        step_size=1,
        control_id="ctrl-inv-anomalies-sigma",
    )

    return sheet


# ---------------------------------------------------------------------------
# Money Trail (L.2.4)
# ---------------------------------------------------------------------------

def _build_money_trail_sheet(
    cfg: Config, app: App, analysis: Analysis,
) -> Sheet:
    """Money Trail — Sankey + hop-by-hop detail table side-by-side.

    Three parameter-bound filter groups all scope ALL_VISUALS so the
    Sankey + table share one chain selection:

    - Chain root dropdown (parameter-bound `CategoryFilter` with
      `MatchOperator=EQUALS`, populated from the matview's distinct
      `root_transfer_id` values via `LinkedValues`).
    - Max-hops slider (`NumericRangeFilter` max-bound, parameter-bound).
    - Min-hop-amount slider (`NumericRangeFilter` min-bound,
      parameter-bound).

    Layout:
      * Row 1: Sankey (⅔ width) + table (⅓ width), both `_TABLE_ROW_SPAN`
        tall. Sankey is the headline; table is reference for edges the
        diagram hides plus the future drill surface (K.4.7).
    """
    del cfg

    ds_money_trail = app.add_dataset(Dataset(
        identifier=DS_INV_MONEY_TRAIL,
        arn=app.cfg.dataset_arn(app.cfg.prefixed("inv-money-trail-dataset")),
    ))

    root_param = analysis.add_parameter(StringParam(
        name=P_INV_MONEY_TRAIL_ROOT,
        # No default — dropdown auto-populates and SelectAll=HIDDEN
        # forces QuickSight to land on the first available chain on
        # first paint instead of an empty "All" state.
        default=[],
    ))
    max_hops_param = analysis.add_parameter(IntegerParam(
        name=P_INV_MONEY_TRAIL_MAX_HOPS,
        default=[_DEFAULT_MONEY_TRAIL_MAX_HOPS],
    ))
    min_amount_param = analysis.add_parameter(IntegerParam(
        name=P_INV_MONEY_TRAIL_MIN_AMOUNT,
        default=[_DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
    ))

    sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_INV_MONEY_TRAIL,
        name="Money Trail",
        title="Money Trail",
        description=_MONEY_TRAIL_DESCRIPTION,
    ))

    # Layout: Sankey ⅔ width on the left, hop-by-hop table ⅓ width on
    # the right. Both span the full table row height.
    main_row = sheet.layout.row(height=_TABLE_ROW_SPAN)
    main_row.add_sankey(
        width=_THIRD * 2,
        title="Money Trail — Chain Sankey",
        subtitle=(
            "Source account → target account ribbons for the selected "
            "chain. Ribbon thickness = SUM(hop_amount). Single-leg "
            "transfers don't render here — see the detail table for "
            "every chain member."
        ),
        source=ds_money_trail["source_account_name"].dim(),
        target=ds_money_trail["target_account_name"].dim(),
        weight=ds_money_trail["hop_amount"].sum(currency=True),
        items_limit=_SANKEY_NODE_CAP,
    )
    depth_dim = ds_money_trail["depth"].numerical()
    main_row.add_table(
        width=_THIRD,
        title="Money Trail — Hop-by-Hop",
        subtitle=(
            "Every edge in the selected chain, ordered root → leaf "
            "by depth."
        ),
        group_by=[
            depth_dim,
            ds_money_trail["transfer_id"].dim(),
            ds_money_trail["transfer_type"].dim(),
            ds_money_trail["source_account_name"].dim(),
            ds_money_trail["target_account_name"].dim(),
            ds_money_trail["posted_at"].date(),
        ],
        values=[ds_money_trail["hop_amount"].sum(currency=True)],
        sort_by=(depth_dim, "ASC"),
    )

    # Chain root: parameter-bound CategoryFilter — narrows to one chain.
    root_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_MONEY_TRAIL_ROOT,
        filters=[CategoryFilter.with_parameter(
            filter_id="filter-inv-money-trail-root",
            dataset=ds_money_trail,
            column=ds_money_trail["root_transfer_id"],
            parameter=root_param,
            match_operator="EQUALS",
            null_option="NON_NULLS_ONLY",
        )],
    ))
    root_fg.scope_sheet(sheet)

    # Max hops: max-bound numeric filter — IncludeMaximum so slider
    # value 5 means "depth ≤ 5 surfaces".
    hops_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_MONEY_TRAIL_HOPS,
        filters=[NumericRangeFilter(
            filter_id="filter-inv-money-trail-hops",
            dataset=ds_money_trail,
            column=ds_money_trail["depth"],
            maximum=ParameterBound(max_hops_param),
            null_option="NON_NULLS_ONLY",
            include_maximum=True,
        )],
    ))
    hops_fg.scope_sheet(sheet)

    # Min hop amount: min-bound numeric filter.
    amount_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_MONEY_TRAIL_AMOUNT,
        filters=[NumericRangeFilter(
            filter_id="filter-inv-money-trail-amount",
            dataset=ds_money_trail,
            column=ds_money_trail["hop_amount"],
            minimum=ParameterBound(min_amount_param),
            null_option="NON_NULLS_ONLY",
            include_minimum=True,
        )],
    ))
    amount_fg.scope_sheet(sheet)

    # Q.1.b — Window date-range filter on `posted_at`. Same shape as
    # Recipient Fanout / Volume Anomalies (filter-bound DATE_RANGE
    # picker, scope_sheet narrow). Money Trail's matview can grow
    # unbounded over time; this gives the analyst a knob to narrow
    # the chain set without rebuilding.
    window_fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_MONEY_TRAIL_WINDOW,
        filters=[TimeRangeFilter(
            filter_id="filter-inv-money-trail-window",
            dataset=ds_money_trail,
            column=ds_money_trail["posted_at"],
            null_option="NON_NULLS_ONLY",
            time_granularity="DAY",
        )],
    ))
    window_fg.scope_sheet(sheet)

    # Controls — three parameter-driven plus the new date-range picker.
    sheet.add_parameter_dropdown(
        parameter=root_param,
        title="Chain root transfer",
        type="SINGLE_SELECT",
        selectable_values=LinkedValues.from_column(ds_money_trail["root_transfer_id"]),
        hidden_select_all=True,
        control_id="ctrl-inv-money-trail-root",
    )
    sheet.add_parameter_slider(
        parameter=max_hops_param,
        title="Max hops",
        minimum_value=_HOPS_SLIDER_MIN,
        maximum_value=_HOPS_SLIDER_MAX,
        step_size=1,
        control_id="ctrl-inv-money-trail-hops",
    )
    sheet.add_parameter_slider(
        parameter=min_amount_param,
        title="Min hop amount ($)",
        minimum_value=_AMOUNT_SLIDER_MIN,
        maximum_value=_AMOUNT_SLIDER_MAX,
        step_size=10,
        control_id="ctrl-inv-money-trail-amount",
    )
    sheet.add_filter_datetime_picker(
        filter=window_fg.filters[0],
        title="Date Range",
        type="DATE_RANGE",
        control_id="ctrl-inv-money-trail-window",
    )

    return sheet


# ---------------------------------------------------------------------------
# Account Network (L.2.5 — re-port of L.1.15 spike inside the full app)
# ---------------------------------------------------------------------------

def _build_account_network_sheet(
    cfg: Config, app: App, analysis: Analysis,
) -> Sheet:
    """Account Network — directional Sankeys + touching-edges table.

    The L.1.15 spike (`_account_network_full_port.py`) already proved
    byte-identity for this sheet via the typed primitives. L.2.5 folds
    that wiring into the main app builder so the full app emits one
    coherent Analysis, dropping the standalone spike fixture.

    Datasets: the matview wrapper (visuals + filters) plus the K.4.8k
    narrow accounts dataset (anchor dropdown). Two parameters
    (anchor + min amount), four analysis-level calc fields (the
    direction-specific edge-touching predicates plus the counterparty
    display picker), three drill actions (left-click on each Sankey
    walks the anchor; right-click on a table row walks via the
    counterparty calc field), four filter groups (anchor → table only,
    inbound direction → inbound Sankey only, outbound direction →
    outbound Sankey only, amount → all three).

    Layout: two Sankeys side-by-side on top (½ width each), full-width
    table below.

    App2 alpha gap (X.2.g.2.c, deferred from v8.8.0a1)
    --------------------------------------------------
    The QS dialect drives anchor + direction filtering through the
    ``pInvANetworkAnchor`` StringParam + four analysis-level calc
    fields, all evaluated inside the QuickSight engine at render
    time. App2 has no equivalent calc-field-to-SQL translator yet,
    so under App2 this sheet renders **all flows from the matview
    without anchor or direction filtering** — both directional
    Sankeys show identical content (the full edge set), the table
    is unfiltered, and the anchor dropdown's value is ignored.
    Basic Sankey rendering still works (X.2.g.2.b), the page is
    not broken — just less interactive than the QS view.

    Closing the gap before v8.8.0 stable means either: (1) building
    a calc-field-to-SQL emitter for the inbound / outbound direction
    flags and the anchor predicate, OR (2) templating the dataset
    SQL with App2-specific ``WHERE`` clauses bound to ``:param_*``
    placeholders (mirroring the X.2.g.1.b ``app2_date_filter``
    pattern). Tracked as task #646 / X.2.g.2.c follow-up.
    """
    del cfg

    ds_anet = app.add_dataset(Dataset(
        identifier=DS_INV_ACCOUNT_NETWORK,
        arn=app.cfg.dataset_arn(app.cfg.prefixed("inv-account-network-dataset")),
    ))
    ds_accounts = app.add_dataset(Dataset(
        identifier=DS_INV_ANETWORK_ACCOUNTS,
        arn=app.cfg.dataset_arn(app.cfg.prefixed("inv-anetwork-accounts-dataset")),
    ))

    anchor_param = analysis.add_parameter(StringParam(
        name=P_INV_ANETWORK_ANCHOR,
        # No default — SelectAll=HIDDEN forces dropdown to land on
        # first available anchor on first paint.
        default=[],
    ))
    min_amount_param = analysis.add_parameter(IntegerParam(
        name=P_INV_ANETWORK_MIN_AMOUNT,
        default=[_DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
    ))

    # Calc field names kept explicit because they're referenced by
    # the constants module + analysts read them as column headers.
    is_anchor_edge = analysis.add_calc_field(CalcField(
        name=CF_INV_ANETWORK_IS_ANCHOR_EDGE,
        dataset=ds_anet,
        expression=(
            "ifelse({source_display} = ${pInvANetworkAnchor} "
            "OR {target_display} = ${pInvANetworkAnchor}, "
            "'yes', 'no')"
        ),
    ))
    is_inbound_edge = analysis.add_calc_field(CalcField(
        name=CF_INV_ANETWORK_IS_INBOUND_EDGE,
        dataset=ds_anet,
        expression=(
            "ifelse({target_display} = ${pInvANetworkAnchor}, "
            "'yes', 'no')"
        ),
    ))
    is_outbound_edge = analysis.add_calc_field(CalcField(
        name=CF_INV_ANETWORK_IS_OUTBOUND_EDGE,
        dataset=ds_anet,
        expression=(
            "ifelse({source_display} = ${pInvANetworkAnchor}, "
            "'yes', 'no')"
        ),
    ))
    # counterparty_display is shape-tagged so the table's walk-the-flow
    # drill can derive the parameter shape from the calc-field ref.
    counterparty_display = analysis.add_calc_field(CalcField(
        name=CF_INV_ANETWORK_COUNTERPARTY_DISPLAY,
        dataset=ds_anet,
        expression=(
            "ifelse({source_display} = ${pInvANetworkAnchor}, "
            "{target_display}, {source_display})"
        ),
        shape=ColumnShape.ACCOUNT_DISPLAY,
    ))

    sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_INV_ACCOUNT_NETWORK,
        name="Account Network",
        title="Account Network",
        description=_ACCOUNT_NETWORK_DESCRIPTION,
    ))

    # All three Drills below are walk-the-flow (same-sheet) actions —
    # target_sheet auto-resolves to the owning sheet at emit time, and
    # the drill source is a Dim object ref (field_id + shape resolve
    # off the Dim's dataset contract / calc-field shape tag).
    anchor_param_drill = DrillParam(
        P_INV_ANETWORK_ANCHOR, ColumnShape.ACCOUNT_DISPLAY,
    )

    # Row 1: two Sankeys side-by-side (inbound on left, outbound on right).
    half_width = _FULL // 2
    sankey_row = sheet.layout.row(height=_TABLE_ROW_SPAN)
    inbound_source_dim = ds_anet["source_display"].dim()
    inbound_sankey = sankey_row.add_sankey(
        width=half_width,
        title="Inbound — counterparties → anchor",
        subtitle=(
            "Counterparties sending money INTO the anchor account. "
            "Ribbon thickness = SUM(hop_amount). Left-click any source "
            "node (or its ribbon) to walk the anchor over to that "
            "counterparty."
        ),
        source=inbound_source_dim,
        target=ds_anet["target_display"].dim(),
        weight=ds_anet["hop_amount"].sum(currency=True),
        items_limit=_SANKEY_NODE_CAP,
        actions=[Drill(
            writes=[(anchor_param_drill, inbound_source_dim)],
            name="Walk to this counterparty",
            trigger="DATA_POINT_CLICK",
            action_id="action-anetwork-sankey-inbound-walk",
        )],
    )
    outbound_target_dim = ds_anet["target_display"].dim()
    outbound_sankey = sankey_row.add_sankey(
        width=half_width,
        title="Outbound — anchor → counterparties",
        subtitle=(
            "Counterparties receiving money FROM the anchor account. "
            "Ribbon thickness = SUM(hop_amount). Left-click any target "
            "node (or its ribbon) to walk the anchor over to that "
            "counterparty."
        ),
        source=ds_anet["source_display"].dim(),
        target=outbound_target_dim,
        weight=ds_anet["hop_amount"].sum(currency=True),
        items_limit=_SANKEY_NODE_CAP,
        actions=[Drill(
            writes=[(anchor_param_drill, outbound_target_dim)],
            name="Walk to this counterparty",
            trigger="DATA_POINT_CLICK",
            action_id="action-anetwork-sankey-outbound-walk",
        )],
    )

    # Row 2: full-width touching-edges table.
    # counterparty_display is a CalcField — Dim(ds, calc_field_ref)
    # carries the calc-field identity through the resolver.
    counterparty_dim = Dim(ds_anet, counterparty_display)
    table_amount = ds_anet["hop_amount"].sum(currency=True)
    table = sheet.layout.row(height=_TABLE_ROW_SPAN).add_table(
        width=_FULL,
        title="Account Network — Touching Edges",
        subtitle=(
            "Every edge involving the anchor account in either "
            "direction, ordered by amount descending. The "
            "Counterparty column shows the side that isn't the "
            "current anchor — right-click any row and pick \"Walk "
            "to other account on this edge\" to make that "
            "counterparty the new anchor. The dropdown above may "
            "take a moment to catch up; trust the data, not the "
            "control text."
        ),
        group_by=[
            ds_anet["transfer_id"].dim(),
            ds_anet["transfer_type"].dim(),
            ds_anet["source_display"].dim(),
            ds_anet["target_display"].dim(),
            counterparty_dim,
            ds_anet["depth"].numerical(),
            ds_anet["posted_at"].date(),
        ],
        values=[table_amount],
        sort_by=(table_amount, "DESC"),
        actions=[Drill(
            writes=[(anchor_param_drill, counterparty_dim)],
            name="Walk to other account on this edge",
            trigger="DATA_POINT_MENU",
            action_id="action-anetwork-table-walk-counterparty",
        )],
    )

    # Anchor filter — table only (broader scope than the directional
    # Sankeys, which use direction-specific calc fields).
    sheet.scope(
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FG_INV_ANETWORK_ANCHOR,
            filters=[CategoryFilter.with_values(
                filter_id="filter-inv-anetwork-anchor",
                dataset=ds_anet,
                column=is_anchor_edge,
                values=["yes"],
                match_operator="CONTAINS",
            )],
        )),
        [table],
    )

    # Inbound direction filter — inbound Sankey only.
    sheet.scope(
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FG_INV_ANETWORK_INBOUND,
            filters=[CategoryFilter.with_values(
                filter_id="filter-inv-anetwork-inbound",
                dataset=ds_anet,
                column=is_inbound_edge,
                values=["yes"],
                match_operator="CONTAINS",
            )],
        )),
        [inbound_sankey],
    )

    # Outbound direction filter — outbound Sankey only.
    sheet.scope(
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FG_INV_ANETWORK_OUTBOUND,
            filters=[CategoryFilter.with_values(
                filter_id="filter-inv-anetwork-outbound",
                dataset=ds_anet,
                column=is_outbound_edge,
                values=["yes"],
                match_operator="CONTAINS",
            )],
        )),
        [outbound_sankey],
    )

    # Min-amount filter — all visuals on the sheet.
    analysis.add_filter_group(FilterGroup(
        filter_group_id=FG_INV_ANETWORK_AMOUNT,
        filters=[NumericRangeFilter(
            filter_id="filter-inv-anetwork-amount",
            dataset=ds_anet,
            column=ds_anet["hop_amount"],
            minimum=ParameterBound(min_amount_param),
            null_option="NON_NULLS_ONLY",
            include_minimum=True,
        )],
    )).scope_sheet(sheet)

    # Anchor dropdown reads the K.4.8k narrow accounts dataset (not the
    # main matview) to keep the dropdown's distinct-source-display query
    # cheap as the matview grows.
    sheet.add_parameter_dropdown(
        parameter=anchor_param,
        title="Anchor account",
        type="SINGLE_SELECT",
        selectable_values=LinkedValues.from_column(ds_accounts["source_display"]),
        hidden_select_all=True,
        control_id="ctrl-inv-anetwork-anchor",
    )
    sheet.add_parameter_slider(
        parameter=min_amount_param,
        title="Min hop amount ($)",
        minimum_value=_AMOUNT_SLIDER_MIN,
        maximum_value=_AMOUNT_SLIDER_MAX,
        step_size=10,
        control_id="ctrl-inv-anetwork-amount",
    )

    return sheet


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------

def build_investigation_app(
    cfg: Config,
    *,
    l2_instance: L2Instance | None = None,
) -> App:
    """Build the Investigation App tree (N.3.f — L2-fed).

    Returns a fully-wired App ready for ``app.emit_analysis()`` /
    ``app.emit_dashboard()``. The CLI calls this via the
    ``build_analysis`` / ``build_investigation_dashboard`` shims below.

    Per the N.2 audit, Investigation is fed by the same institution
    YAML that drives L1 + L2FT. The L2 instance prefix is auto-derived
    from ``l2_instance.instance`` here so callers don't have to
    pre-stamp ``cfg.l2_instance_prefix``; if the caller HAS pre-set
    it (e.g. an integrator running a custom build), that value is
    preserved. Defaults to the persona-neutral ``spec_example``
    instance — the same default L1 uses.

    Investigation-specific tables read from ``<prefix>_inv_*``
    matviews (N.3.b); base-table reads use ``<prefix>_transactions``.
    """
    if l2_instance is None:
        l2_instance = default_l2_instance()

    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2_instance.instance))

    # N.3.g / N.4.k: theme from the L2 instance, coerced to the
    # registry default for in-canvas accent colors when the instance
    # declares no inline ``theme:`` block. The CLI uses the un-coerced
    # ``resolve_l2_theme`` return to decide whether to deploy a
    # custom Theme resource (silent-fallback to AWS CLASSIC).
    from quicksight_gen.common.theme import DEFAULT_PRESET
    theme = resolve_l2_theme(l2_instance) or DEFAULT_PRESET

    analysis_name = _analysis_name(l2_instance)
    app = App(name="investigation", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="investigation-analysis",
        name=analysis_name,
    ))
    _build_getting_started_sheet(cfg, analysis, theme=theme)
    _build_recipient_fanout_sheet(cfg, app, analysis)
    _build_volume_anomalies_sheet(cfg, app, analysis)
    _build_money_trail_sheet(cfg, app, analysis)
    _build_account_network_sheet(cfg, app, analysis)
    _build_app_info_sheet(cfg, app, analysis, l2_instance=l2_instance, theme=theme)
    app.create_dashboard(
        dashboard_id_suffix="investigation-dashboard",
        name=analysis_name,
    )
    return app


def _build_app_info_sheet(
    cfg: Config, app: App, analysis: Analysis,
    *, l2_instance: L2Instance, theme: ThemePreset,
) -> None:
    """M.4.4.5 — App Info ("i") sheet, ALWAYS LAST. Diagnostic canary;
    see common/sheets/app_info.py.

    Builds the App Info DataSets so the tree refs can derive ARNs from
    the IDs. ``build_all_datasets()`` ALSO calls these (so the JSON
    write step ships them on deploy) — identity-idempotent contract
    registration on the second call, identical DataSetIds, no harm.

    N.3.g: ``theme`` is the L2-resolved theme (coerced to the registry
    default for in-canvas accents when no L2 theme block is declared);
    populate_app_info_sheet accepts it directly.
    """
    from quicksight_gen.apps.investigation.datasets import inv_matview_specs

    # M.4.4.7 — per-app segment matches the inv-segmented call in
    # apps/investigation/datasets.py::build_all_datasets so the
    # contract-registry idempotence check sees the same DataSetIds.
    # P.9f.e — view names must carry the L2 prefix (``<prefix>_inv_*``)
    # so the matview lookup matches the actual table names emitted by
    # ``common.l2.schema``. Using the unprefixed bare names slipped past
    # all unit + integration tests because nothing actually executed
    # the dataset's CustomSQL until QS rendered the visual.
    liveness_aws = build_liveness_dataset(cfg, app_segment="inv")
    matviews_aws = build_matview_status_dataset(
        cfg, app_segment="inv", view_specs=inv_matview_specs(l2_instance),
    )
    liveness_ds = app.add_dataset(Dataset(
        identifier=DS_APP_INFO_LIVENESS,
        arn=cfg.dataset_arn(liveness_aws.DataSetId),
    ))
    matviews_ds = app.add_dataset(Dataset(
        identifier=DS_APP_INFO_MATVIEWS,
        arn=cfg.dataset_arn(matviews_aws.DataSetId),
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_INV_APP_INFO,
        name=APP_INFO_SHEET_NAME,
        title=APP_INFO_SHEET_TITLE,
        description=APP_INFO_SHEET_DESCRIPTION,
    ))
    populate_app_info_sheet(
        cfg, sheet,
        liveness_ds=liveness_ds, matview_status_ds=matviews_ds,
        theme=theme,
    )


def _analysis_name(l2_instance: L2Instance) -> str:
    """Title shown in QuickSight — matches L1/L2FT's ``Name (instance)``
    shape so multi-instance deployments are visually distinguishable
    in the dashboard list."""
    return f"Investigation ({l2_instance.instance})"


# ---------------------------------------------------------------------------
# Public CLI shims — drop-in replacements for the imperative
# ``apps.investigation.analysis.build_analysis`` /
# ``build_investigation_dashboard``. Same signatures, byte-identical
# JSON, just routed through the typed tree.
# ---------------------------------------------------------------------------

def build_analysis(
    cfg: Config, *, l2_instance: L2Instance | None = None,
) -> ModelAnalysis:
    """Tree-backed replacement for the imperative ``build_analysis``.

    Forwards ``l2_instance`` to ``build_investigation_app``; default
    is the persona-neutral spec_example.
    """
    return build_investigation_app(cfg, l2_instance=l2_instance).emit_analysis()


def build_investigation_dashboard(
    cfg: Config, *, l2_instance: L2Instance | None = None,
) -> ModelDashboard:
    """Tree-backed replacement for the imperative builder."""
    return build_investigation_app(
        cfg, l2_instance=l2_instance,
    ).emit_dashboard()
