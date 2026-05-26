"""Unit tests for the Executives app.

Greenfield app built directly on the Phase L tree primitives — no
imperative builders to compare against, so tests walk the tree's
emitted JSON for structural checks and walk the tree refs directly
for invariant checks (dataset / filter / visual presence).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from recon_gen.apps.executives.app import (
    SHEET_EXEC_ACCOUNT_COVERAGE,
    SHEET_EXEC_GETTING_STARTED,
    SHEET_EXEC_MONEY_MOVED,
    SHEET_EXEC_TRANSACTION_VOLUME,
    build_executives_app,
    build_executives_dashboard,
)
from recon_gen.apps.executives.datasets import (
    DS_EXEC_ACCOUNT_SUMMARY,
    DS_EXEC_TRANSACTION_SUMMARY,
    EXEC_ACCOUNT_SUMMARY_CONTRACT,
    EXEC_TRANSACTION_SUMMARY_CONTRACT,
    build_all_datasets,
)
from recon_gen.cli import main
from tests._test_helpers import make_test_config

if TYPE_CHECKING:
    from recon_gen.common.models import Analysis as _ModelsAnalysis
    from recon_gen.common.models import SheetDefinition as _SheetDefinition
    from recon_gen.common.tree import App as _App


# N.4.b: Executives is now L2-fed and requires the cfg's db_table_prefix
# to render its dataset SQL. Z.C — db_table_prefix replaces the prior
# auto-stamped l2_instance_prefix; pin to spec_example since
# ``build_executives_app`` defaults to the spec_example L2 fixture.
_TEST_CFG = make_test_config(db_table_prefix="spec_example")


@pytest.fixture(scope="module")
def exec_app() -> "_App":
    """Tree-built Executives App (post-emit, auto-IDs resolved)."""
    app = build_executives_app(_TEST_CFG)
    app.emit_analysis()
    return app


@pytest.fixture(scope="module")
def exec_analysis(exec_app: "_App") -> "_ModelsAnalysis":
    return exec_app.emit_analysis()


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------

def test_analysis_has_five_sheets_in_expected_order(exec_analysis: "_ModelsAnalysis") -> None:
    """4 content sheets + the M.4.4.5 App Info ("i") sheet last."""
    from recon_gen.apps.executives.app import SHEET_EXEC_APP_INFO

    sheet_ids = [s.SheetId for s in exec_analysis.Definition.Sheets]
    assert sheet_ids == [
        SHEET_EXEC_GETTING_STARTED,
        SHEET_EXEC_ACCOUNT_COVERAGE,
        SHEET_EXEC_TRANSACTION_VOLUME,
        SHEET_EXEC_MONEY_MOVED,
        SHEET_EXEC_APP_INFO,
    ]


def test_analysis_name_is_executives(exec_analysis: "_ModelsAnalysis") -> None:
    # Z.C — every L2-fed app's analysis name follows the
    # ``Name (deployment_name)`` shape so multi-deploy QS accounts are
    # visually distinguishable in the dashboard list. Replaces the
    # prior ``(instance)`` shape (instance was auto-stamped from the
    # L2 yaml; now lives on cfg.deployment_name).
    assert exec_analysis.Name == f"Executives ({_TEST_CFG.deployment_name})"


def test_analysis_serializes_to_aws_json(exec_analysis: "_ModelsAnalysis") -> None:
    """to_aws_json() must succeed end-to-end — no None-strip crashes."""
    j = exec_analysis.to_aws_json()
    assert j["AnalysisId"] == _TEST_CFG.prefixed("executives-analysis")
    assert len(j["Definition"]["Sheets"]) == 5


def test_dashboard_mirrors_analysis(exec_app: "_App") -> None:
    dashboard = exec_app.emit_dashboard()
    assert dashboard.DashboardId == _TEST_CFG.prefixed(
        "executives-dashboard",
    )
    assert (
        len(dashboard.Definition.Sheets)
        == len(exec_app.analysis.sheets)
    )


def test_every_sheet_has_a_description(exec_analysis: "_ModelsAnalysis") -> None:
    for sheet in exec_analysis.Definition.Sheets:
        assert sheet.Description, (
            f"{sheet.SheetId} is missing a description"
        )


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def test_datasets_in_expected_order():
    """5 content datasets (BH.8 follow-up added the transaction-legs
    per-leg / all-status counter for the sibling KPI; Y.2.h split
    account into base + active; AO.5 added daily rollup) + 2 M.4.4.5
    App Info datasets, in order."""
    datasets = build_all_datasets(_TEST_CFG)
    assert len(datasets) == 7
    assert datasets[0].DataSetId == _TEST_CFG.prefixed(
        "exec-transaction-summary-dataset",
    )
    assert datasets[1].DataSetId == _TEST_CFG.prefixed(
        "exec-transaction-daily-dataset",
    )
    assert datasets[2].DataSetId == _TEST_CFG.prefixed(
        "exec-transaction-legs-dataset",
    )
    assert datasets[3].DataSetId == _TEST_CFG.prefixed(
        "exec-account-summary-dataset",
    )
    assert datasets[4].DataSetId == _TEST_CFG.prefixed(
        "exec-account-summary-active-dataset",
    )
    assert datasets[5].DataSetId == _TEST_CFG.prefixed(
        "exec-app-info-liveness-dataset",
    )
    assert datasets[6].DataSetId == _TEST_CFG.prefixed(
        "exec-app-info-matviews-dataset",
    )


def test_datasets_declared_in_analysis(exec_analysis: "_ModelsAnalysis") -> None:
    """5 content datasets (BH.8 added transaction-legs; Y.2.h split
    account into base + active; AO.5 added daily rollup) + the 2
    M.4.4.5 App Info datasets."""
    from recon_gen.apps.executives.datasets import (
        DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
        DS_EXEC_TRANSACTION_DAILY,
        DS_EXEC_TRANSACTION_LEGS,
    )
    from recon_gen.common.sheets.app_info import (
        DS_APP_INFO_LIVENESS, DS_APP_INFO_MATVIEWS,
    )

    decls = exec_analysis.Definition.DataSetIdentifierDeclarations
    assert [d.Identifier for d in decls] == [
        DS_EXEC_TRANSACTION_SUMMARY,
        DS_EXEC_TRANSACTION_DAILY,
        DS_EXEC_TRANSACTION_LEGS,
        DS_EXEC_ACCOUNT_SUMMARY,
        DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
        DS_APP_INFO_LIVENESS,
        DS_APP_INFO_MATVIEWS,
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
    sql = next(iter(txn_ds.PhysicalTableMap.values())).CustomSql.SqlQuery
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
    sql = next(iter(acct_ds.PhysicalTableMap.values())).CustomSql.SqlQuery
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
    from recon_gen.apps.executives.datasets import DS_EXEC_TRANSACTION_LEGS

    skip_ids = {
        _TEST_CFG.prefixed("exec-app-info-liveness-dataset"),
        _TEST_CFG.prefixed("exec-app-info-matviews-dataset"),
        # BH.8 — transaction-legs deliberately skips the Posted filter
        # so its count matches App Info's per-leg / all-status row_count.
        _TEST_CFG.prefixed("exec-transaction-legs-dataset"),
    }
    for ds in build_all_datasets(_TEST_CFG):
        if ds.DataSetId in skip_ids:
            continue
        sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
        assert "status = 'Posted'" in sql, (
            f"{ds.DataSetId} must filter status='Posted'"
        )


# ---------------------------------------------------------------------------
# Account Coverage sheet
# ---------------------------------------------------------------------------

def _visual_ids(sheet: "_SheetDefinition") -> list[str]:
    out: list[str] = []
    for v in sheet.Visuals or []:
        for body in vars(v).values():
            if body is not None and hasattr(body, "VisualId"):
                out.append(body.VisualId)
    return out


def test_account_coverage_has_kpis_bars_and_table(exec_analysis: "_ModelsAnalysis") -> None:
    sheet = next(
        s for s in exec_analysis.Definition.Sheets
        if s.SheetId == SHEET_EXEC_ACCOUNT_COVERAGE
    )
    expected = {
        "exec-account-kpi-open",
        "exec-account-kpi-active",
        "exec-account-bar-open-by-type",
        "exec-account-bar-active-by-type",
        "exec-account-detail-table",
    }
    assert set(_visual_ids(sheet)) == expected


def test_account_coverage_legacy_active_filter_dropped(exec_analysis: "_ModelsAnalysis") -> None:
    """Y.2.h — the visual-pinned ``NumericRangeFilter`` that narrowed
    the Active KPI + bar to ``activity_count >= 1`` is gone, replaced
    by ``DS_EXEC_ACCOUNT_SUMMARY_ACTIVE`` whose SQL bakes the
    predicate in. The pinned filter narrowed in QS but not in App2;
    baking it into a second dataset fixes both renderers.
    """
    legacy_fg_ids = [
        g.FilterGroupId
        for g in exec_analysis.Definition.FilterGroups
        if g.FilterGroupId == "fg-exec-account-active-only"
    ]
    assert legacy_fg_ids == [], (
        "fg-exec-account-active-only should be gone after Y.2.h dataset split"
    )


def test_account_coverage_active_dataset_declared(exec_analysis: "_ModelsAnalysis") -> None:
    """The Y.2.h active-only dataset is declared on the Executives
    analysis (so the active KPI + bar can reference it)."""
    from recon_gen.apps.executives.datasets import (
        DS_EXEC_ACCOUNT_SUMMARY_ACTIVE,
    )
    decls = {
        d.Identifier
        for d in exec_analysis.Definition.DataSetIdentifierDeclarations
    }
    assert DS_EXEC_ACCOUNT_SUMMARY_ACTIVE in decls


# ---------------------------------------------------------------------------
# Transaction Volume + Money Moved sheets
# ---------------------------------------------------------------------------

def test_transaction_volume_visuals(exec_analysis: "_ModelsAnalysis") -> None:
    sheet = next(
        s for s in exec_analysis.Definition.Sheets
        if s.SheetId == SHEET_EXEC_TRANSACTION_VOLUME
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


def test_money_moved_visuals(exec_analysis: "_ModelsAnalysis") -> None:
    sheet = next(
        s for s in exec_analysis.Definition.Sheets
        if s.SheetId == SHEET_EXEC_MONEY_MOVED
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

class TestCli:
    def _base_config(self, tmp_path: Path) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(
            "aws_account_id: '111122223333'\n"
            "aws_region: us-west-2\n"
            # Z.C — required cfg fields.
            "deployment_name: recon-exec-cli\n"
            "db_table_prefix: spec_example\n"
            "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
            ":datasource/ds\n"
        )
        return p

    def test_json_apply_writes_executives(self, tmp_path: Path):
        """Q.3.a: ``json apply`` always emits all four apps; verify
        the executives JSON files land in the output dir."""
        config = self._base_config(tmp_path)
        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["json", "apply", "-c", str(config), "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "executives-analysis.json").exists()
        assert (out / "executives-dashboard.json").exists()

    def test_json_apply_writes_all_apps(self, tmp_path: Path):
        """Q.3.a: ``json apply`` is the single bundled-emit verb;
        every app's analysis + dashboard JSON must show up."""
        config = self._base_config(tmp_path)
        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            main, ["json", "apply", "-c", str(config), "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        for stem in (
            "investigation",
            "executives",
            "l1-dashboard",
        ):
            assert (out / f"{stem}-analysis.json").exists()
            assert (out / f"{stem}-dashboard.json").exists()
