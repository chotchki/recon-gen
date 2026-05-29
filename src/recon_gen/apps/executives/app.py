"""Tree-based builder for the Executives App (L.6 — greenfield).

First app built directly against the Phase L tree primitives instead
of being ported from imperative builders. The L.6 acceptance is that
a first-time author can wire a 4-sheet app without touching
``constants.py`` (the tree carries internal IDs); URL-facing sheet
IDs are still explicit but live inline here, not in a sibling
``constants.py`` module.

Sheets land per L.6 sub-step:

- L.6.2 — Skeleton + Getting Started shell (this commit).
- L.6.3 — Dataset SQL (`datasets.py`).
- L.6.4 — Account Coverage sheet.
- L.6.5 — Transaction Volume Over Time sheet.
- L.6.6 — Money Moved sheet.
- L.6.7 — Cross-app drills into AR Transactions.
- L.6.8 — Theme: reuse `default` preset. (Per N.1.g the `sasquatch-bank` preset moved to L2 YAML; Executives stays on `default` until N.4 makes it L2-fed.)
- L.6.9 — Unit tests (mirror the per-app shape).
- L.6.10 — CLI wiring (`generate executives`, `--all` includes it).
- L.6.11 — Confirm Executives reads existing PR + AR + Investigation
  seeds without needing its own demo SQL.
- L.6.12 — Iteration gate: surface any L.1 friction.
"""

from __future__ import annotations

# Importing datasets registers each Executives DatasetContract via its
# module-level register_contract() side effect — required so the L.1.17
# bare-string / unvalidated-Column emit-time validator can resolve
# every ds["col"] ref in the visuals below.
from recon_gen.apps.executives import datasets as _register_contracts  # noqa: F401  # pyright: ignore[reportUnusedImport]: import-for-side-effect (register_contract calls)
# N.4.b: Executives reads the same default institution YAML as L1
# (per the N.2 audit's "one institution YAML drives all apps" framing).
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common import rich_text as rt
from recon_gen.common.config import Config
from recon_gen.common.ids import ParameterName, SheetId, VisualId
from recon_gen.common.l2 import L2Instance, ThemePreset
from recon_gen.common.models import Analysis as ModelAnalysis
from recon_gen.common.models import Dashboard as ModelDashboard
from recon_gen.common.sheets.app_info import (
    APP_INFO_SHEET_DESCRIPTION,
    APP_INFO_SHEET_NAME,
    APP_INFO_SHEET_TITLE,
    app_info_liveness_id,
    app_info_matviews_id,
    populate_app_info_sheet,
)

# BO.5 — per-app App Info dataset identifiers; see l1_dashboard/app.py.
_DS_APP_INFO_LIVENESS = app_info_liveness_id("exec")
_DS_APP_INFO_MATVIEWS = app_info_matviews_id("exec")
from recon_gen.common.theme import resolve_l2_theme
from recon_gen.common.tree import (
    Analysis,
    App,
    Dataset,
    DateTimeParam,
    DateView,
    KPIValueSignIndicator,
    Sheet,
    TextBox,
)

from recon_gen.apps.executives.datasets import (
    DS_EXEC_ACCOUNT_SUMMARY,
    DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
    DS_EXEC_TRANSACTION_DAILY,
    DS_EXEC_TRANSACTION_LEGS,
    DS_EXEC_TRANSACTION_SUMMARY,
    P_EXEC_DATE_END as _P_EXEC_DATE_END,
    P_EXEC_DATE_START as _P_EXEC_DATE_START,
    build_all_datasets,
)


# Layout constants — same pattern as PR/AR/Inv app.py modules.
_FULL = 36
_HALF = 18
_KPI_ROW_SPAN = 6
_CHART_ROW_SPAN = 12
_TABLE_ROW_SPAN = 18


# URL-facing sheet IDs — these need to be stable across deploys and
# embed-link rebuilds, so they stay explicit (per the URL-facing
# vs internal-IDs convention from L.1.16). Internal visual / action /
# layout IDs auto-resolve at emit time.
SHEET_EXEC_GETTING_STARTED = SheetId("exec-sheet-getting-started")
SHEET_EXEC_ACCOUNT_COVERAGE = SheetId("exec-sheet-account-coverage")
SHEET_EXEC_TRANSACTION_VOLUME = SheetId("exec-sheet-transaction-volume")
SHEET_EXEC_MONEY_MOVED = SheetId("exec-sheet-money-moved")
SHEET_EXEC_APP_INFO = SheetId("exec-sheet-app-info")  # M.4.4.5


# Sheet descriptions — single source of truth, also surfaced in the
# Getting Started bullet blocks so each sheet's description matches
# the summary on the landing page.
_ACCOUNT_COVERAGE_DESCRIPTION = (
    "How many accounts the bank has on its books and how many of them "
    "have actually moved money in the selected period. Counts split "
    "by account type so you can see the shape of the deposit base "
    "next to the GL control accounts that drive operations."
)

_TRANSACTION_VOLUME_DESCRIPTION = (
    # C20 (cold-read v11.26.1) — sheet renders a stacked bar by rail,
    # not a line chart. Copy synced to the rendered visual.
    "Transaction throughput over time, sliced by rail_name so you "
    "can see which rails are growing or contracting. The stacked bar "
    "is the daily-rail trend; the clustered bar is the period total "
    "per type."
)

_MONEY_MOVED_DESCRIPTION = (
    "Dollar volume moving across the bank, by rail, over time. Net "
    "(signed sum — flows into the bank are positive) and gross (sum of "
    "absolute values — total handle, regardless of direction) live "
    "side by side; the per-rail bar shows where the volume is coming "
    "from."
)


_ACCOUNT_COVERAGE_BULLETS = [
    "KPIs: total open accounts + active accounts in the period",
    "Bar charts: open + active counts by account type",
    "Detail table: per-account last activity date and count",
]

_TRANSACTION_VOLUME_BULLETS = [
    "KPIs: total transactions + average daily volume",
    "Daily transaction count, coloured by rail_name",
    "Period total per rail_name",
]

_MONEY_MOVED_BULLETS = [
    "KPIs: net money moved (Σ signed) + gross money moved (Σ |signed|)",
    "Daily gross money moved, coloured by rail_name",
    "Period total per rail_name",
]


# ---------------------------------------------------------------------------
# Getting Started (L.6.2)
# ---------------------------------------------------------------------------

def _section_box_content(
    title: str, body: str, bullet_items: list[str], accent: str,
) -> str:
    return rt.text_box(
        rt.heading(title, color=accent),
        rt.BR,
        rt.BR,
        rt.markdown(body),
        rt.BR,
        rt.bullets(bullet_items),
    )


def _populate_getting_started(
    cfg: Config, sheet: Sheet, *, theme: ThemePreset,
) -> None:
    """Getting Started — landing page for the Executives app.

    N.4.c: ``theme`` is the L2-resolved theme.
    """
    accent = theme.accent

    sheet.layout.row(height=4).add_text_box(
        TextBox(
            text_box_id="exec-gs-welcome",
            content=rt.text_box(
                rt.inline(
                    "Executives Dashboard",
                    font_size="36px",
                    color=accent,
                ),
                rt.BR,
                rt.BR,
                rt.markdown(
                    "Board-cadence view of the bank's transaction "
                    "throughput, money movement, and account coverage. "
                    "Scan for trends; click any row or bar to drill into "
                    "the operational sheets for the underlying "
                    "transactions."
                ),
            ),
        ),
        width=_FULL,
    )

    sheet.layout.row(height=6).add_text_box(
        TextBox(
            text_box_id="exec-gs-clickability-legend",
            content=rt.text_box(
                rt.heading("Clickable cells", color=accent),
                rt.BR,
                rt.BR,
                rt.markdown(
                    "Cells rendered in the theme accent color are "
                    "interactive:"
                ),
                rt.bullets_raw([
                    "Plain accent-coloured text — left-click drills to "
                    "an operational sheet (Account Reconciliation's "
                    "Transactions tab) filtered to the row's "
                    "account / transfer type",
                    rt.inline(
                        "Heads-up: drill-down filters stick after you "
                        "switch tabs. Refresh the dashboard to clear "
                        "them.",
                        color=accent,
                    ),
                ]),
            ),
        ),
        width=_FULL,
    )

    sheet_blocks = [
        (
            "exec-gs-account-coverage", "Account Coverage",
            _ACCOUNT_COVERAGE_DESCRIPTION, _ACCOUNT_COVERAGE_BULLETS,
        ),
        (
            "exec-gs-transaction-volume", "Transaction Volume Over Time",
            _TRANSACTION_VOLUME_DESCRIPTION, _TRANSACTION_VOLUME_BULLETS,
        ),
        (
            "exec-gs-money-moved", "Money Moved",
            _MONEY_MOVED_DESCRIPTION, _MONEY_MOVED_BULLETS,
        ),
    ]
    for box_id, title, body_text, bullet_items in sheet_blocks:
        sheet.layout.row(height=7).add_text_box(
            TextBox(
                text_box_id=box_id,
                content=_section_box_content(
                    title, body_text, bullet_items, accent,
                ),
            ),
            width=_FULL,
        )


# ---------------------------------------------------------------------------
# Datasets — registered on the App in build_executives_app; populators
# reference them by Python variable. Same shape as AR/PR's _datasets()
# helpers.
# ---------------------------------------------------------------------------

def _datasets(cfg: Config) -> dict[str, Dataset]:
    """Map each Executives logical dataset identifier to a typed `Dataset` ref.

    M.4.4.5 — last 2 entries are the App Info datasets, mirroring the
    order in `build_all_datasets`.
    """
    built = build_all_datasets(cfg)
    names = [
        DS_EXEC_TRANSACTION_SUMMARY,
        # AO.5 — per-active-day rollup for the avg-daily KPI.
        DS_EXEC_TRANSACTION_DAILY,
        # BH.8 follow-up — per-leg / all-status counter for the
        # sibling "Transfer Legs (all statuses)" KPI.
        DS_EXEC_TRANSACTION_LEGS,
        DS_EXEC_ACCOUNT_SUMMARY,
        # Y.2.h — second account dataset; same shape, baked WHERE.
        DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
        _DS_APP_INFO_LIVENESS,
        _DS_APP_INFO_MATVIEWS,
    ]
    return {
        name: Dataset(identifier=name, arn=cfg.dataset_arn(ds.DataSetId))
        for name, ds in zip(names, built)
    }


# ---------------------------------------------------------------------------
# Account Coverage (L.6.4) — 2 KPIs (open / active) + 2 bar charts
# (open and active counts grouped by account_type) + detail table.
#
# Y.2.h: the "Active accounts" KPI + bar source from a second dataset
# (DS_EXEC_ACCOUNT_SUMMARY_ACTIVE) whose SQL bakes in
# ``WHERE COALESCE(activity_count, 0) > 0`` plus the date-range filter
# via dual-SQL — replaces the prior visual-pinned NumericRangeFilter
# which narrowed in QS but not in App2.
# ---------------------------------------------------------------------------

# Q.1.b — Universal date-range filter parameter names. Phase BM
# pushed the narrowing into dataset SQL; the per-dataset FilterGroupIds
# that lived here dissolved with the analysis-level TimeRangeFilters.
# The underlying string literals live in ``datasets.py`` so the
# dataset-side ``<<$pExecDate*>>`` placeholders bridge to the same NAME
# the analysis-side declares (one source).
P_EXEC_DATE_START = ParameterName(_P_EXEC_DATE_START)
P_EXEC_DATE_END = ParameterName(_P_EXEC_DATE_END)

# AR.4 — Exec sheets show daily-grain summaries rather than per-leg
# detail, so 30 days reads as one trend page (vs L1's 7-day operator
# window). The pre-AR.4 RollingDate exprs are gone; the range is a
# DateView constructed from cfg.test_generator.as_of_frame(window_days=30).


def _populate_account_coverage(
    cfg: Config,
    sheet: Sheet,
    *,
    datasets: dict[str, Dataset],
) -> None:
    del cfg
    ds_acct = datasets[DS_EXEC_ACCOUNT_SUMMARY]
    # Y.2.h — Active KPI + bar source from the narrowed dataset where
    # `WHERE COALESCE(activity_count, 0) > 0` is baked into the SQL,
    # so both QS + App2 narrow correctly without a visual-pinned filter.
    ds_acct_active = datasets[DS_EXEC_ACCOUNT_SUMMARY_ACTIVE]

    # Row 1: two KPIs side-by-side.
    kpi_row = sheet.layout.row(height=_KPI_ROW_SPAN)
    kpi_row.add_kpi(
        width=_HALF,
        visual_id=VisualId("exec-account-kpi-open"),
        title="Total Open Accounts",
        subtitle=(
            "Every account that has ever appeared in daily_balances. "
            "**When this equals Active Accounts (right) every open "
            "account had at least one transaction in the window** — a "
            "healthy fully-utilized state. A larger gap = idle accounts "
            "worth follow-up."
        ),
        values=[ds_acct["account_id"].count(field_id="exec-acct-open-count")],
    )
    kpi_row.add_kpi(
        width=_HALF,
        visual_id=VisualId("exec-account-kpi-active"),
        title="Active Accounts (this window)",
        subtitle=(
            "Subset of Open Accounts (left) with ≥1 successful "
            "transaction in the selected date window. Always ≤ Total "
            "Open. Equality means every open account transacted."
        ),
        values=[
            ds_acct_active["account_id"].count(
                field_id="exec-acct-active-count",
            ),
        ],
    )

    # Row 2: two horizontal bar charts side-by-side. Open count on
    # the left, active count on the right — both grouped by
    # account_type so the deposit base shape vs the operational GL
    # shape pop visually.
    chart_row = sheet.layout.row(height=_CHART_ROW_SPAN)
    chart_row.add_bar_chart(
        width=_HALF,
        visual_id=VisualId("exec-account-bar-open-by-type"),
        title="Open Accounts by Type",
        subtitle="Total open-account count grouped by account_type",
        category=[
            ds_acct["account_type"].dim(field_id="exec-acct-open-type-dim"),
        ],
        values=[ds_acct["account_id"].count(
            field_id="exec-acct-open-type-count",
        )],
        orientation="HORIZONTAL",
        bars_arrangement="CLUSTERED",
        category_label="Account Type",
        value_label="Open Accounts",
    )
    chart_row.add_bar_chart(
        width=_HALF,
        visual_id=VisualId("exec-account-bar-active-by-type"),
        title="Active Accounts by Type",
        subtitle=(
            "Accounts with activity in the selected window, grouped by account_type"
        ),
        category=[
            ds_acct_active["account_type"].dim(
                field_id="exec-acct-active-type-dim",
            ),
        ],
        values=[ds_acct_active["account_id"].count(
            field_id="exec-acct-active-type-count",
        )],
        orientation="HORIZONTAL",
        bars_arrangement="CLUSTERED",
        category_label="Account Type",
        value_label="Active Accounts",
    )

    # Row 3: full-width unaggregated detail table — one row per
    # account, sorted by activity_count DESC so the busiest accounts
    # surface at the top.
    sheet.layout.row(height=_TABLE_ROW_SPAN).add_table(
        width=_FULL,
        visual_id=VisualId("exec-account-detail-table"),
        title="Account Detail",
        subtitle=(
            "Per-account row with last activity date and total count "
            "of successful transaction legs"
        ),
        columns=[
            ds_acct["account_id"].dim(field_id="exec-acct-tbl-id"),
            ds_acct["account_name"].dim(field_id="exec-acct-tbl-name"),
            ds_acct["account_type"].dim(field_id="exec-acct-tbl-type"),
            ds_acct["last_activity_date"].dim(
                field_id="exec-acct-tbl-last-activity",
            ),
            ds_acct["activity_count"].dim(field_id="exec-acct-tbl-count"),
        ],
        sort_by=("exec-acct-tbl-count", "DESC"),
    )


# ---------------------------------------------------------------------------
# Transaction Volume Over Time (L.6.5) — 2 KPIs (period total +
# average daily count) + daily stacked bar by rail_name +
# per-type clustered bar.
#
# **L.6.12 friction note** — original design called for a line chart
# of daily counts coloured by rail_name. The tree has no
# LineChart primitive yet (PR/AR/Inv didn't need one). Substituted
# a vertical STACKED BarChart with `posted_date` on Category +
# `transfer_count` on Values + `rail_name` on Colors — same
# trend signal, different visual shape. Adding a `LineChart` typed
# Visual subtype + an `add_line_chart` layout DSL helper is queued
# for the L.6.12 iteration gate.
# ---------------------------------------------------------------------------

def _populate_transaction_volume(
    cfg: Config,
    sheet: Sheet,
    *,
    datasets: dict[str, Dataset],
) -> None:
    del cfg
    ds_txn = datasets[DS_EXEC_TRANSACTION_SUMMARY]
    # AO.5 — Avg Daily Volume uses the per-active-day rollup so its
    # AVG denominator is days-with-activity, not days × rails.
    ds_daily = datasets[DS_EXEC_TRANSACTION_DAILY]
    ds_legs = datasets[DS_EXEC_TRANSACTION_LEGS]

    # Row 1: three KPIs (BH.8 follow-up 2026-05-26 after v11.22.1
    # cold-read). Was two KPIs (Total Transactions + Avg Daily Volume);
    # the cold-read agent flagged the Total-vs-App-Info-row-count gap
    # AGAIN despite v11.22.0's subtitle-only fix. The subtitle wasn't
    # enough — operators see two numbers, can't tell why they differ.
    # New sibling "Transfer Legs (all statuses)" KPI exposes the
    # per-leg / all-status count that App Info reports, side-by-side
    # with the deduped Posted count, so the predicate gap is VISIBLE
    # not mysterious.
    third = _FULL // 3
    kpi_row = sheet.layout.row(height=_KPI_ROW_SPAN)
    kpi_row.add_kpi(
        width=third,
        visual_id=VisualId("exec-txn-kpi-total"),
        title="Total Transactions (Posted, per-transfer)",
        subtitle=(
            "Count of distinct **Posted** transfers — multi-leg "
            "transfers (a wire + its receiver leg, an ACH batch + its "
            "individual credits) count ONCE per transfer_id. The "
            "metric leadership asks about. Sibling **Transfer Legs** "
            "KPI exposes the raw matview row count (all statuses, per "
            "leg) — the gap is the documented predicate scope, not a "
            "defect."
        ),
        values=[ds_txn["transfer_count"].sum(field_id="exec-txn-total-count")],
    )
    kpi_row.add_kpi(
        width=third,
        visual_id=VisualId("exec-txn-kpi-legs"),
        title="Transfer Legs (all statuses)",
        subtitle=(
            "Raw count of all transaction legs in the period — "
            "per-leg (not per-transfer), all statuses (incl. failed). "
            "Matches the **App Info sheet's `<prefix>_transactions` "
            "row_count** exactly. Headline pair (this + Total "
            "Transactions) makes the documented predicate-scope gap "
            "visible."
        ),
        values=[ds_legs["leg_count"].sum(field_id="exec-txn-legs-count")],
    )
    kpi_row.add_kpi(
        width=third,
        visual_id=VisualId("exec-txn-kpi-avg-daily"),
        title="Average Daily Volume",
        subtitle=(
            "Total transfer count per active business day. Averaged "
            "over days that had any activity; zero-volume days don't "
            "surface in the underlying dataset. Rendered as integer "
            "because the underlying datum is a count."
        ),
        values=[ds_daily["daily_transfer_count"].average(
            field_id="exec-txn-avg-daily-count",
            decimals=0,
        )],
    )

    # Row 2: full-width vertical STACKED bar — daily transfer count,
    # stacked by rail_name. The trend over time PLUS the
    # composition signal in one visual.
    sheet.layout.row(height=_CHART_ROW_SPAN).add_bar_chart(
        width=_FULL,
        visual_id=VisualId("exec-txn-bar-daily-stacked"),
        title="Daily Transaction Count by Type",
        subtitle=(
            "Each day's transfer count, stacked by rail_name so "
            "rail composition + trend show together. "
            "**Demo-data caveat:** apparent multi-week empty stretches + "
            "weekend gaps reflect the bundled demo's short seed window "
            "(90 days) + non-business-day cadence — not data outages. "
            "Real deploys with full-history ETL won't show them."
        ),
        category=[ds_txn["posted_date"].date(field_id="exec-txn-daily-date")],
        values=[ds_txn["transfer_count"].sum(
            field_id="exec-txn-daily-count",
        )],
        colors=[ds_txn["rail_name"].dim(field_id="exec-txn-daily-type")],
        orientation="VERTICAL",
        bars_arrangement="STACKED",
        category_label="Posted Date",
        value_label="Transactions",
        color_label="Transfer Type",
    )

    # Row 3: full-width vertical clustered bar — period total per
    # rail_name. Reads the same dataset, aggregates across the
    # date axis to give the "where did the volume come from" snapshot.
    # BQ.5 (cold-read F7): with 60-80 rail_names, one rail dominates
    # at the period level and the rest read as microscopic bars;
    # log_scale keeps every rail readable for the executive skim.
    sheet.layout.row(height=_CHART_ROW_SPAN).add_bar_chart(
        width=_FULL,
        visual_id=VisualId("exec-txn-bar-by-type"),
        title="Period Total by Type",
        subtitle=(
            "Total transfer count over the selected period, per "
            "rail_name. **Log-scale Y axis** — one rail typically "
            "dominates an executive period; log scale makes the rest "
            "still readable at the same glance."
        ),
        category=[
            ds_txn["rail_name"].dim(field_id="exec-txn-type-dim"),
        ],
        values=[ds_txn["transfer_count"].sum(field_id="exec-txn-type-count")],
        orientation="VERTICAL",
        bars_arrangement="CLUSTERED",
        category_label="Transfer Type",
        value_label="Transactions (log)",
        log_scale=True,
    )


# ---------------------------------------------------------------------------
# Money Moved (L.6.6) — same shape as Transaction Volume but with
# gross + net dollars instead of count.
# ---------------------------------------------------------------------------

def _populate_money_moved(
    cfg: Config,
    sheet: Sheet,
    *,
    datasets: dict[str, Dataset],
) -> None:
    del cfg
    ds_txn = datasets[DS_EXEC_TRANSACTION_SUMMARY]

    # Row 1: two KPIs.
    kpi_row = sheet.layout.row(height=_KPI_ROW_SPAN)
    kpi_row.add_kpi(
        width=_HALF,
        visual_id=VisualId("exec-money-kpi-net"),
        title="Net Money Moved",
        subtitle=(
            "Σ signed amount across the period. **Expected near zero** "
            "on a balanced book — every customer-to-external transfer "
            "is offset by the matching external-to-customer leg, so "
            "internal-only flows net out and only externally-cleared "
            "asymmetries surface. Large positive = net inflow to the "
            "bank's books (deposits exceeding payouts in the window); "
            "large negative = net outflow you should be able to "
            "explain. The ▲/▼ glyph next to the number is the "
            "accessible sign signal — green ▲ when net ≥ 0 (inflow), "
            "red ▼ when net < 0 (outflow). Compare against **Gross "
            "Money Moved** on the right for the per-period handle."
        ),
        values=[ds_txn["net_amount"].sum(
            field_id="exec-money-net-sum", currency=True,
        )],
        value_sign_indicator=KPIValueSignIndicator(),
    )
    kpi_row.add_kpi(
        width=_HALF,
        visual_id=VisualId("exec-money-kpi-gross"),
        title="Gross Money Moved",
        subtitle=(
            "Total handle — sum of per-transfer dollar magnitudes "
            "regardless of direction"
        ),
        values=[ds_txn["gross_amount"].sum(
            field_id="exec-money-gross-sum", currency=True,
        )],
    )

    # Row 2: full-width vertical stacked bar — daily gross dollars,
    # stacked by rail_name. Same friction note as L.6.5 — would
    # be a line chart if the tree had one.
    sheet.layout.row(height=_CHART_ROW_SPAN).add_bar_chart(
        width=_FULL,
        visual_id=VisualId("exec-money-bar-daily-stacked"),
        title="Daily Gross Dollars Moved by Type",
        subtitle=(
            "Each day's gross dollar volume, stacked by rail_name. "
            "**Demo-data caveat:** apparent multi-week empty stretches + "
            "weekend gaps reflect the bundled demo's short seed window "
            "(90 days) + non-business-day cadence — not data outages."
        ),
        category=[
            ds_txn["posted_date"].date(field_id="exec-money-daily-date"),
        ],
        values=[ds_txn["gross_amount"].sum(
            field_id="exec-money-daily-gross", currency=True,
        )],
        colors=[ds_txn["rail_name"].dim(
            field_id="exec-money-daily-type",
        )],
        orientation="VERTICAL",
        bars_arrangement="STACKED",
        category_label="Posted Date",
        value_label="Gross Dollars Moved",
        color_label="Transfer Type",
    )

    # Row 3: full-width vertical clustered bar — period total per
    # rail_name.
    # BQ.5 (cold-read F7): with 60-80 rail_names, one rail's gross
    # dollars typically swamp the rest — log_scale lets the long
    # tail stay readable for the executive skim.
    sheet.layout.row(height=_CHART_ROW_SPAN).add_bar_chart(
        width=_FULL,
        visual_id=VisualId("exec-money-bar-by-type"),
        title="Period Total Gross Dollars by Type",
        subtitle=(
            "Total gross dollar volume over the period, per "
            "rail_name. **Log-scale Y axis** — one rail typically "
            "dominates an executive period; log scale makes the rest "
            "still readable at the same glance."
        ),
        category=[
            ds_txn["rail_name"].dim(field_id="exec-money-type-dim"),
        ],
        values=[ds_txn["gross_amount"].sum(
            field_id="exec-money-type-gross", currency=True,
        )],
        orientation="VERTICAL",
        bars_arrangement="CLUSTERED",
        category_label="Transfer Type",
        value_label="Gross Dollars Moved (log)",
        log_scale=True,
    )


def _wire_date_range_filter(
    analysis: Analysis,
    *,
    datasets: dict[str, Dataset],
    account_coverage_sheet: Sheet,
    transaction_volume_sheet: Sheet,
    money_moved_sheet: Sheet,
    exec_range_view: DateView,
) -> None:
    """Q.1.b — Universal date-range filter across the 3 data-bearing
    Exec sheets, mirroring L1's M.2b.1 pattern.

    Phase BM — the per-dataset ``TimeRangeFilter`` FilterGroups dissolved
    in favor of dataset-SQL pushdown via ``<<$pExecDateStart>>`` /
    ``<<$pExecDateEnd>>`` (see ``datasets.py::_exec_universal_range_params``).
    This wire now declares only:

    1. Two analysis-level ``DateTimeParam``s with ``mapped_dataset_params``
       bridging the 2 BM-shape datasets' ``pExecDateStart`` /
       ``pExecDateEnd`` (the active account-summary variant + the
       transaction summary). The base ``DS_EXEC_ACCOUNT_SUMMARY`` is
       date-INDEPENDENT (its semantic IS "every account that exists")
       and intentionally excluded from the bridge.
    2. Paired ``ParameterDateTimePicker`` controls on every data-bearing
       sheet so the analyst sets the window once and it propagates.
    """
    # Phase BM — bridge to the date-scoped datasets. The base
    # ``DS_EXEC_ACCOUNT_SUMMARY`` is intentionally excluded (date-
    # independent snapshot of every account — the "Total Open Accounts"
    # KPI binds it specifically because the all-time count is the
    # operator-facing semantic). The active variant + the transaction
    # summary + the daily rollup carry the BM-shape dataset params.
    #
    # BR.x — added ``ds_daily`` to the bridge list. ``DS_EXEC_TRANSACTION_DAILY``
    # declares ``pExecDateStart`` / ``pExecDateEnd`` (via
    # ``datasets.py::_exec_universal_range_params``) and its SQL
    # substitutes them. Pre-BR.x the analysis only bridged the two other
    # date-scoped datasets — QS flagged the daily rollup with "You have
    # an unmapped dataset parameter." on analysis-editor open. See
    # ``docs/reference/quicksight-quirks.md`` unmapped DatasetParameter
    # entry for the full failure shape.
    ds_acct_active = datasets[DS_EXEC_ACCOUNT_SUMMARY_ACTIVE]
    ds_txn = datasets[DS_EXEC_TRANSACTION_SUMMARY]
    ds_daily = datasets[DS_EXEC_TRANSACTION_DAILY]
    start_bridges = [
        (ds_acct_active, str(P_EXEC_DATE_START)),
        (ds_txn, str(P_EXEC_DATE_START)),
        (ds_daily, str(P_EXEC_DATE_START)),
    ]
    end_bridges = [
        (ds_acct_active, str(P_EXEC_DATE_END)),
        (ds_txn, str(P_EXEC_DATE_END)),
        (ds_daily, str(P_EXEC_DATE_END)),
    ]
    # AR.4 — 30-day window via DateView (pre-AR.4 RollingDate exprs gone).
    date_start = analysis.add_parameter(DateTimeParam(
        name=P_EXEC_DATE_START,
        time_granularity="DAY",
        default=exec_range_view.emit_qs_analysis_default_start(),
        mapped_dataset_params=start_bridges,
    ))
    date_end = analysis.add_parameter(DateTimeParam(
        name=P_EXEC_DATE_END,
        time_granularity="DAY",
        default=exec_range_view.emit_qs_analysis_default_end(),
        mapped_dataset_params=end_bridges,
    ))

    for sheet in (
        account_coverage_sheet, transaction_volume_sheet, money_moved_sheet,
    ):
        sheet.add_parameter_datetime_picker(
            parameter=date_start, title="Date From",
        )
        sheet.add_parameter_datetime_picker(
            parameter=date_end, title="Date To",
        )


# ---------------------------------------------------------------------------
# App entry points
# ---------------------------------------------------------------------------

def _analysis_name(cfg: Config) -> str:
    """Title shown in QuickSight — matches L1/L2FT's ``Name (deployment)``
    shape so multi-deployment runs are visually distinguishable in the
    dashboard list."""
    return f"Executives ({cfg.deployment_name})"


# Sheet display order. Pre-register-all-shells pattern (mirrors
# AR/PR L.x.2) so cross-sheet drills can target by ref before all
# populators have run. The L.6.2 commit only populates Getting
# Started; the other 3 sheets ship as bare shells (id + metadata
# only) until L.6.4 / L.6.5 / L.6.6 land.
_EXEC_SHEET_SPECS: tuple[tuple[SheetId, str, str, str], ...] = (
    (SHEET_EXEC_GETTING_STARTED, "Getting Started", "Getting Started",
     "Landing page — summarises each tab in this dashboard so readers "
     "know where to look first. No filters or visuals."),
    (SHEET_EXEC_ACCOUNT_COVERAGE, "Account Coverage", "Account Coverage",
     _ACCOUNT_COVERAGE_DESCRIPTION),
    (SHEET_EXEC_TRANSACTION_VOLUME, "Transaction Volume Over Time",
     "Transaction Volume Over Time", _TRANSACTION_VOLUME_DESCRIPTION),
    (SHEET_EXEC_MONEY_MOVED, "Money Moved", "Money Moved",
     _MONEY_MOVED_DESCRIPTION),
)


def build_executives_app(
    cfg: Config,
    *,
    l2_instance: L2Instance | None = None,
) -> App:
    """Construct the Executives App as a tree (N.4.b — L2-fed).

    Per the N.2 audit, Executives is fed by the same institution YAML
    that drives L1 / L2FT / Investigation. Z.C: the deployment +
    DB-table prefixes are required cfg fields — both come from
    ``cfg.deployment_name`` (QS-resource segment) and
    ``cfg.db_table_prefix`` (DB table-name prefix). Defaults to the
    persona-neutral ``spec_example`` L2 instance.

    Executives reads from ``<db_table_prefix>_transactions`` +
    ``<db_table_prefix>_daily_balances``. No app-specific matviews.
    """
    if l2_instance is None:
        l2_instance = default_l2_instance()

    # N.4.c / N.4.k: theme from the L2 instance, coerced to the
    # registry default for in-canvas accent colors when the instance
    # declares no inline ``theme:`` block. The CLI uses the un-coerced
    # ``resolve_l2_theme`` return to decide whether to deploy a
    # custom Theme resource (silent-fallback to AWS CLASSIC).
    from recon_gen.common.theme import DEFAULT_PRESET
    theme = resolve_l2_theme(l2_instance) or DEFAULT_PRESET

    analysis_name = _analysis_name(cfg)
    app = App(name="executives", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="executives-analysis",
        name=analysis_name,
    ))

    datasets = _datasets(cfg)
    for ds in datasets.values():
        app.add_dataset(ds)

    sheets: dict[str, Sheet] = {}
    for sheet_id, name, title, description in _EXEC_SHEET_SPECS:
        sheets[sheet_id] = analysis.add_sheet(Sheet(
            sheet_id=sheet_id,
            name=name,
            title=title,
            description=description,
        ))

    _populate_getting_started(
        cfg, sheets[SHEET_EXEC_GETTING_STARTED], theme=theme,
    )
    _populate_account_coverage(
        cfg, sheets[SHEET_EXEC_ACCOUNT_COVERAGE], datasets=datasets,
    )
    _populate_transaction_volume(
        cfg, sheets[SHEET_EXEC_TRANSACTION_VOLUME], datasets=datasets,
    )
    _populate_money_moved(
        cfg, sheets[SHEET_EXEC_MONEY_MOVED], datasets=datasets,
    )

    # Q.1.b — Universal date-range filter across all 3 data-bearing
    # sheets (mirrors L1's M.2b.1 pattern: shared analysis-level
    # DateTimeParams bridged into dataset-SQL pushdown).
    # AR.4 — 30-day window per the pre-AR.4 RollingDate defaults.
    # Phase BM — narrowing pushed into dataset SQL via
    # ``<<$pExecDate*>>``; analysis-level FilterGroups + BL.2's
    # default_universal_date_range bind-layer fallback both dissolved.
    _wire_date_range_filter(
        analysis,
        datasets=datasets,
        account_coverage_sheet=sheets[SHEET_EXEC_ACCOUNT_COVERAGE],
        transaction_volume_sheet=sheets[SHEET_EXEC_TRANSACTION_VOLUME],
        money_moved_sheet=sheets[SHEET_EXEC_MONEY_MOVED],
        exec_range_view=DateView(
            frame=cfg.test_generator.as_of_frame(window_days=30),
        ),
    )

    # M.4.4.5 — App Info ("i") sheet, ALWAYS LAST. Diagnostic canary;
    # see common/sheets/app_info.py. Datasets registered via
    # `_datasets` above (single source of truth across the tree-ref +
    # JSON-write flows). Executives reads base tables only — the
    # matview-status dataset emits its placeholder row.
    app_info_sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_EXEC_APP_INFO,
        name=APP_INFO_SHEET_NAME,
        title=APP_INFO_SHEET_TITLE,
        description=APP_INFO_SHEET_DESCRIPTION,
    ))
    populate_app_info_sheet(
        cfg, app_info_sheet,
        liveness_ds=datasets[_DS_APP_INFO_LIVENESS],
        matview_status_ds=datasets[_DS_APP_INFO_MATVIEWS],
        theme=theme,
    )

    app.create_dashboard(
        dashboard_id_suffix="executives-dashboard",
        name=analysis_name,
    )
    return app


# ---------------------------------------------------------------------------
# CLI / external-caller shims. Same shape as the other apps' shims.
# Wired into the CLI in L.6.10.
# ---------------------------------------------------------------------------

def build_analysis(
    cfg: Config, *, l2_instance: L2Instance | None = None,
) -> ModelAnalysis:
    """Build the complete Executives Analysis resource via the tree.

    Forwards ``l2_instance`` to ``build_executives_app``; default
    is the persona-neutral spec_example.
    """
    return build_executives_app(
        cfg, l2_instance=l2_instance,
    ).emit_analysis()


def build_executives_dashboard(
    cfg: Config, *, l2_instance: L2Instance | None = None,
) -> ModelDashboard:
    """Build the Executives Dashboard resource via the tree."""
    return build_executives_app(
        cfg, l2_instance=l2_instance,
    ).emit_dashboard()
