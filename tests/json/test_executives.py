# pyright: reportOptionalIterable=false, reportOptionalMemberAccess=false
# BF.4/F: tests walk emitted-tree where Optional fields are populated by the
# builder. Suppressing the Optional family at file scope keeps the assertions
# readable without per-line asserts.
"""Unit tests for the Executives app.

Greenfield app built directly on the Phase L tree primitives — no
imperative builders to compare against, so tests walk the tree's
emitted JSON for structural checks and walk the tree refs directly
for invariant checks (dataset / filter / visual presence).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from recon_gen.apps.executives.app import (
    SHEET_EXEC_ACCOUNT_COVERAGE,
    SHEET_EXEC_GETTING_STARTED,
    SHEET_EXEC_MONEY_MOVED,
    SHEET_EXEC_PROGRAM_HEALTH,
    SHEET_EXEC_TRANSACTION_VOLUME,
    build_executives_app,
)
from recon_gen.apps.executives.datasets import (
    DS_EXEC_ACCOUNT_SUMMARY,
    EXEC_ACCOUNT_SUMMARY_CONTRACT,
    EXEC_TRANSACTION_SUMMARY_CONTRACT,
    build_all_datasets,
)
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests._test_helpers import make_test_config

if TYPE_CHECKING:
    from recon_gen.common.tree import Analysis as _TreeAnalysis
    from recon_gen.common.tree import App as _App
    from recon_gen.common.tree import Sheet as _TreeSheet


# N.4.b: Executives is now L2-fed and requires the cfg's db_table_prefix
# to render its dataset SQL. Z.C — db_table_prefix replaces the prior
# auto-stamped l2_instance_prefix; pin to spec_example since
# ``build_executives_app`` defaults to the spec_example L2 fixture.
_TEST_CFG = make_test_config(db_table_prefix=DEFAULT_PREFIX)


@pytest.fixture(scope="module")
def exec_app() -> "_App":
    """Tree-built Executives App (auto-IDs resolved).

    DW.1 — the tree IS the source of truth; resolve_auto_ids() stamps
    visual / drill IDs without round-tripping through the QS-API
    serializers (being deleted in DW.8)."""
    app = build_executives_app(_TEST_CFG)
    app.resolve_auto_ids()
    return app


@pytest.fixture(scope="module")
def exec_analysis(exec_app: "_App") -> "_TreeAnalysis":
    """The App's tree Analysis node — walked directly, never emitted."""
    assert exec_app.analysis is not None
    return exec_app.analysis


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------

def test_analysis_has_six_sheets_in_expected_order(exec_analysis: "_TreeAnalysis") -> None:
    """5 content sheets + the M.4.4.5 App Info ("i") sheet last.
    CF.2 inserted Program Health between Getting Started and Account
    Coverage so the board-cadence tripwire reads before the volume
    tabs."""
    from recon_gen.apps.executives.app import SHEET_EXEC_APP_INFO

    sheet_ids = [s.sheet_id for s in exec_analysis.sheets]
    assert sheet_ids == [
        SHEET_EXEC_GETTING_STARTED,
        SHEET_EXEC_PROGRAM_HEALTH,
        SHEET_EXEC_ACCOUNT_COVERAGE,
        SHEET_EXEC_TRANSACTION_VOLUME,
        SHEET_EXEC_MONEY_MOVED,
        SHEET_EXEC_APP_INFO,
    ]


def test_analysis_name_is_executives(exec_analysis: "_TreeAnalysis") -> None:
    # Z.C — every L2-fed app's analysis name follows the
    # ``Name (deployment_name)`` shape so multi-deploy QS accounts are
    # visually distinguishable in the dashboard list. Replaces the
    # prior ``(instance)`` shape (instance was auto-stamped from the
    # L2 yaml; now lives on cfg.aws.deployment_name).
    assert exec_analysis.name == f"Executives ({_TEST_CFG.aws.deployment_name})"


def test_analysis_id_and_sheet_count(exec_analysis: "_TreeAnalysis") -> None:
    """DW.1 — the QS-API serialization round-trip is gone (the emitter
    is being deleted in DW.8); keep the two structural checks it carried:
    the analysis-id suffix reconstructs the emitted AnalysisId, and the
    six-sheet count holds — both walked off the tree, no emit."""
    assert _TEST_CFG.aws.prefixed(exec_analysis.analysis_id_suffix) == (
        _TEST_CFG.aws.prefixed("executives-analysis")
    )
    assert len(exec_analysis.sheets) == 6


def test_dashboard_mirrors_analysis(exec_app: "_App") -> None:
    """The Dashboard tree node publishes the SAME Analysis the App owns
    (object ref, not a re-emitted copy), so it mirrors by construction.
    Tree-walk: the DashboardId reconstructs via cfg.aws.prefixed and the
    dashboard points back at the App's analysis — no emit."""
    assert exec_app.dashboard is not None
    assert exec_app.analysis is not None
    dashboard_id = exec_app.cfg.aws.prefixed(
        exec_app.dashboard.dashboard_id_suffix,
    )
    assert dashboard_id == _TEST_CFG.aws.prefixed("executives-dashboard")
    assert exec_app.dashboard.analysis is exec_app.analysis


def test_every_sheet_has_a_description(exec_analysis: "_TreeAnalysis") -> None:
    for sheet in exec_analysis.sheets:
        assert sheet.description, (
            f"{sheet.sheet_id} is missing a description"
        )


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def test_datasets_in_expected_order():
    """6 content datasets (CF.2 added program-health rollup; BH.8 added
    transaction-legs per-leg / all-status counter; Y.2.h split account
    into base + active; AO.5 added daily rollup) + 3 M.4.4.5 App Info
    datasets (DK.5.kpi added latest-balance-day), in order."""
    datasets = build_all_datasets(_TEST_CFG)
    assert len(datasets) == 9
    assert datasets[0].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-transaction-summary-dataset",
    )
    assert datasets[1].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-transaction-daily-dataset",
    )
    assert datasets[2].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-transaction-legs-dataset",
    )
    assert datasets[3].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-account-summary-dataset",
    )
    assert datasets[4].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-account-summary-active-dataset",
    )
    assert datasets[5].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-program-health-dataset",
    )
    assert datasets[6].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-app-info-liveness-dataset",
    )
    assert datasets[7].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-app-info-matviews-dataset",
    )
    assert datasets[8].DataSetId == _TEST_CFG.aws.prefixed(
        "exec-app-info-latest-balance-day-dataset",
    )


def test_datasets_declared_in_analysis(exec_app: "_App") -> None:
    """6 content datasets (CF.2 added program-health rollup; BH.8
    added transaction-legs; Y.2.h split account into base + active;
    AO.5 added daily rollup) + the 2 M.4.4.5 App Info datasets.

    DW.1 — the emitted ``DataSetIdentifierDeclarations`` were exactly
    ``[d for d in app.datasets if d in app.dataset_dependencies()]``
    (registration order, filtered to referenced); reproduce that off the
    tree directly."""
    from recon_gen.apps.executives.datasets import (
        DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
        DS_EXEC_PROGRAM_HEALTH,
        DS_EXEC_TRANSACTION_DAILY,
        DS_EXEC_TRANSACTION_LEGS,
        DS_EXEC_TRANSACTION_SUMMARY,
    )
    from recon_gen.common.sheets.app_info import (
        app_info_latest_balance_day_id,
        app_info_liveness_id, app_info_matviews_id,
    )

    deps = exec_app.dataset_dependencies()
    declared = [ds.identifier for ds in exec_app.datasets if ds in deps]
    # BO.5 — App Info dataset identifiers are per-app-segmented now (the
    # process-global App2 SQL registry would otherwise collide across the
    # four-app server). DK.5.kpi added latest_balance_day as the third
    # App Info dataset.
    assert declared == [
        DS_EXEC_TRANSACTION_SUMMARY,
        DS_EXEC_TRANSACTION_DAILY,
        DS_EXEC_TRANSACTION_LEGS,
        DS_EXEC_ACCOUNT_SUMMARY,
        DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
        DS_EXEC_PROGRAM_HEALTH,
        app_info_liveness_id("exec"),
        app_info_matviews_id("exec"),
        app_info_latest_balance_day_id("exec"),
    ]


def test_transaction_summary_contract_columns():
    names = EXEC_TRANSACTION_SUMMARY_CONTRACT.column_names
    assert {
        "posted_date",
        "rail_name",
        "transfer_count",
        "gross_amount",
        "net_amount",
    } == set(names)


def test_account_summary_contract_columns():
    names = EXEC_ACCOUNT_SUMMARY_CONTRACT.column_names
    assert {
        "account_id",
        "account_name",
        "account_type",
        "last_activity_date",
        "activity_count",
    } == set(names)


def test_transaction_summary_sql_aggregates_per_transfer():
    """Per-transfer pre-aggregation is the load-bearing piece — without
    it, multi-leg transfers double-count `gross_amount` (e.g. a $100
    transfer's two $100 legs sum to $200). Guard that the WITH per_transfer
    CTE stays in the SQL."""
    datasets = build_all_datasets(_TEST_CFG)
    txn_ds = datasets[0]
    sql = txn_ds.sql
    assert "WITH per_transfer AS" in sql, (
        "exec_transaction_summary must aggregate per transfer_id first"
    )
    # N.4.a v6 column rename: amount → ABS(amount_money). The
    # ABS-then-MAX preserves the same per-transfer-handle semantic
    # (positive/negative legs share magnitude); MAX without ABS would
    # pick the credit leg over the debit leg arbitrarily.
    assert "MAX(ABS(t.amount_money))" in sql, (
        "MAX(ABS(amount_money)) collapses multi-leg transfers; loss → double-count"
    )


def test_account_summary_sql_left_joins_activity():
    """LEFT JOIN keeps zero-activity accounts visible (last_activity_date
    NULL, activity_count 0) — the active-only filter narrows the KPI
    while the open-side counts every row."""
    datasets = build_all_datasets(_TEST_CFG)
    # BH.8 follow-up shifted account_summary to index 3 (after the
    # transaction-legs dataset at index 2; transaction-daily is index 1).
    acct_ds = datasets[3]
    sql = acct_ds.sql
    assert "LEFT JOIN activity" in sql


def test_both_content_datasets_filter_to_status_posted():
    """Failed legs were recorded but didn't move money — including them
    pollutes executive trends with operational noise. Scoped to the
    transaction-summary + transaction-daily + 2 account-summary content
    datasets — **the BH.8 transaction-legs dataset deliberately does
    NOT filter** (its whole purpose is to surface the per-leg /
    all-status count matching App Info's row_count). M.4.4.5 App Info
    datasets read schema/matview metadata + don't carry a status
    column either."""
    skip_ids = {
        _TEST_CFG.aws.prefixed("exec-app-info-liveness-dataset"),
        _TEST_CFG.aws.prefixed("exec-app-info-matviews-dataset"),
        # DK.5.kpi — data_anchor singleton matview reads via SELECT
        # data_anchor; no transactions column so no status filter.
        _TEST_CFG.aws.prefixed("exec-app-info-latest-balance-day-dataset"),
        # BH.8 — transaction-legs deliberately skips the Posted filter
        # so its count matches App Info's per-leg / all-status row_count.
        _TEST_CFG.aws.prefixed("exec-transaction-legs-dataset"),
        # CF.2 — program-health rollup reads from <prefix>_l1_exceptions
        # (a matview that's already pre-filtered to violations); status
        # filter would be a no-op.
        _TEST_CFG.aws.prefixed("exec-program-health-dataset"),
    }
    for ds in build_all_datasets(_TEST_CFG):
        if ds.DataSetId in skip_ids:
            continue
        sql = ds.sql
        assert "status = 'Posted'" in sql, (
            f"{ds.DataSetId} must filter status='Posted'"
        )


# ---------------------------------------------------------------------------
# Account Coverage sheet
# ---------------------------------------------------------------------------

def _visual_ids(sheet: "_TreeSheet") -> list[str]:
    """Tree-walk: each typed Visual subtype carries its own
    ``visual_id`` (resolved by ``App.resolve_auto_ids()``). The
    Executives visuals all pin explicit IDs."""
    return [str(v.visual_id) for v in sheet.visuals]


def test_account_coverage_has_kpis_bars_and_table(exec_analysis: "_TreeAnalysis") -> None:
    sheet = next(
        s for s in exec_analysis.sheets
        if s.sheet_id == SHEET_EXEC_ACCOUNT_COVERAGE
    )
    expected = {
        "exec-account-kpi-open",
        "exec-account-kpi-active",
        "exec-account-bar-open-by-type",
        "exec-account-bar-active-by-type",
        "exec-account-detail-table",
    }
    assert set(_visual_ids(sheet)) == expected


def test_account_coverage_legacy_active_filter_dropped(exec_analysis: "_TreeAnalysis") -> None:
    """Y.2.h — the visual-pinned ``NumericRangeFilter`` that narrowed
    the Active KPI + bar to ``activity_count >= 1`` is gone, replaced
    by ``DS_EXEC_ACCOUNT_SUMMARY_ACTIVE`` whose SQL bakes the
    predicate in. The pinned filter narrowed in QS but not in App2;
    baking it into a second dataset fixes both renderers.
    """
    legacy_fg_ids = [
        fg.filter_group_id for fg in exec_analysis.filter_groups
        if fg.filter_group_id == "fg-exec-account-active-only"
    ]
    assert legacy_fg_ids == [], (
        "fg-exec-account-active-only should be gone after Y.2.h dataset split"
    )


def test_account_coverage_active_dataset_declared(exec_app: "_App") -> None:
    """The Y.2.h active-only dataset is declared on the Executives
    analysis (so the active KPI + bar can reference it). DW.1 — the
    emitted declarations are exactly the referenced datasets, so a
    membership check walks ``dataset_dependencies()`` off the tree."""
    from recon_gen.apps.executives.datasets import (
        DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
    )
    declared = {ds.identifier for ds in exec_app.dataset_dependencies()}
    assert DS_EXEC_ACCOUNT_SUMMARY_ACTIVE in declared


# ---------------------------------------------------------------------------
# Transaction Volume + Money Moved sheets
# ---------------------------------------------------------------------------

def test_transaction_volume_visuals(exec_analysis: "_TreeAnalysis") -> None:
    sheet = next(
        s for s in exec_analysis.sheets
        if s.sheet_id == SHEET_EXEC_TRANSACTION_VOLUME
    )
    expected = {
        "exec-txn-kpi-total",
        # BH.8 follow-up — sibling KPI added 2026-05-26.
        "exec-txn-kpi-legs",
        "exec-txn-kpi-avg-daily",
        "exec-txn-bar-daily-stacked",
        "exec-txn-bar-by-type",
    }
    assert set(_visual_ids(sheet)) == expected


def test_money_moved_visuals(exec_analysis: "_TreeAnalysis") -> None:
    sheet = next(
        s for s in exec_analysis.sheets
        if s.sheet_id == SHEET_EXEC_MONEY_MOVED
    )
    expected = {
        "exec-money-kpi-net",
        "exec-money-kpi-gross",
        "exec-money-bar-daily-stacked",
        "exec-money-bar-by-type",
    }
    assert set(_visual_ids(sheet)) == expected


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

