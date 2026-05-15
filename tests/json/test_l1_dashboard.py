"""Tests for the L1 Dashboard app — phase M.2a.

The L1 dashboard is the parallel-stack v6 app that consumes M.1a.7's L1
invariant views directly (no v5-idiom translation layer). M.2a.1 ships
the package skeleton + Analysis + Dashboard registration but no sheets;
M.2a.2-M.2a.6 add sheets one at a time, each tested at the substep
that introduces it.

Tests here cover:
- Build pipeline shape (cfg + l2_instance plumb through).
- Analysis + Dashboard emit cleanly.
- Dashboard ID + Analysis ID follow the `<l2_prefix>-l1-dashboard`
  convention so multi-instance deployments are distinguishable.
- Default L2 instance auto-loads the canonical Sasquatch fixture.
- M.2a.9 CLI smoke: `quicksight-gen generate l1-dashboard` writes the
  expected files.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from click.testing import CliRunner

from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
from quicksight_gen.apps.l1_dashboard.app import build_l1_dashboard_app
from quicksight_gen.cli import main
from tests._test_helpers import make_test_config
from quicksight_gen.common.l2 import L2Instance


# v6.0.1 hotfix regression — the bundled `_default_l2.yaml` next to
# `_l2.py` ships in the wheel via `pyproject.toml`'s package-data
# entry; if it drifts from `tests/l2/spec_example.yaml` the L1
# dashboard's "default L2" silently diverges from the test fixture.
def test_default_l2_yaml_is_byte_identical_to_test_fixture():
    pkg_yaml = (
        Path(__file__).parent.parent.parent
        / "src" / "quicksight_gen" / "apps" / "l1_dashboard"
        / "_default_l2.yaml"
    )
    test_yaml = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
    assert pkg_yaml.read_bytes() == test_yaml.read_bytes(), (
        f"Bundled L1 default L2 has drifted from test fixture. "
        f"Re-sync with:\n"
        f"  cp {test_yaml} {pkg_yaml}"
    )


_CFG = make_test_config()


def _sheet_by_name(app, name: str):
    """Look up a Sheet by display name. Position-agnostic — sheet order
    can be reshuffled without breaking these tests, per CLAUDE.md
    "key off stable analyst-facing identifiers" rule."""
    assert app.analysis is not None
    for s in app.analysis.sheets:
        if s.name == name:
            return s
    raise AssertionError(
        f"sheet {name!r} missing — found {[s.name for s in app.analysis.sheets]}"
    )


# -- Build pipeline -----------------------------------------------------------


def test_build_with_default_loads_spec_example() -> None:
    """No kwarg → auto-load the persona-neutral spec_example L2 fixture
    (M.3.2 repointed the default away from sasquatch_ar so production
    library code carries no implicit Sasquatch flavor)."""
    app = build_l1_dashboard_app(_CFG)
    assert app is not None
    assert app.name == "l1-dashboard"


def test_build_with_explicit_l2_instance_uses_caller_value() -> None:
    """Caller-supplied instance overrides the default."""
    explicit = default_l2_instance()
    app = build_l1_dashboard_app(_CFG, l2_instance=explicit)
    # Smoke; the deeper "instance was used for view targeting" assertions
    # land at M.2a.3+ when sheets actually consume views from the L2 prefix.
    assert app is not None


def test_build_signature_l2_instance_is_kwarg_only() -> None:
    """Same convention as build_account_recon_app: positional callers
    keep working without passing l2_instance; tests + alternative-persona
    deployments override via the kwarg."""
    sig = inspect.signature(build_l1_dashboard_app)
    p = sig.parameters.get("l2_instance")
    assert p is not None
    assert p.kind == inspect.Parameter.KEYWORD_ONLY
    assert p.default is None
    annot_str = str(p.annotation)
    assert "L2Instance" in annot_str


# -- Analysis + Dashboard registration ---------------------------------------


def test_analysis_registered_with_l2_aware_name() -> None:
    """The Analysis title surfaces the L2 prefix so multi-instance
    deployments are distinguishable in the QuickSight UI."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    assert "spec_example" in app.analysis.name


def test_dashboard_registered() -> None:
    app = build_l1_dashboard_app(_CFG)
    assert app.dashboard is not None


def test_twelve_sheets_after_m445() -> None:
    """M.2b.12 inserts Supersession Audit after Unbundled Aging.
    Sheet order: Getting Started → drift (today + over time) → other
    invariants (overdraft, limit breach, pending aging, unbundled
    aging) → audit → today's roll-up → per-account-day detail →
    raw legs."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    sheet_names = [s.name for s in app.analysis.sheets]
    assert sheet_names == [
        "Getting Started", "Drift", "Drift Timelines",
        "Overdraft", "Limit Breach",
        "Pending Aging", "Unbundled Aging", "Supersession Audit",
        "Today's Exceptions", "Daily Statement", "Transactions",
        "Info",  # M.4.4.5 — App Info canary, always last
    ]


# -- Getting Started — description-driven prose (M.2a.2) ---------------------


def test_getting_started_welcome_uses_l2_instance_description() -> None:
    """Core M.2a "description-driven prose" rule: the welcome body
    comes from `l2_instance.description`, NOT from a hardcoded persona
    string. Switching L2 instance switches the prose; M.7's render
    pipeline becomes "walk the L2 instance" instead of "substitute
    Sasquatch tokens".

    M.2a.7 added a second text box (L2 Coverage block) below the
    welcome — both are description-driven."""
    app = build_l1_dashboard_app(_CFG)
    gs = app.analysis.sheets[0]
    assert len(gs.text_boxes) == 2
    welcome_xml = gs.text_boxes[0].content
    # The fixture's top-level description string is the body source.
    # Default L2 instance is spec_example (M.3.2 repoint).
    assert "Generic SPEC-shaped instance" in welcome_xml


def test_getting_started_welcome_falls_back_when_l2_description_missing() -> None:
    """If the L2 instance has no top-level description, we surface a
    hint to fill it rather than a blank welcome — quicker debug."""
    from dataclasses import replace
    explicit = default_l2_instance()
    minimal = replace(explicit, description=None)
    app = build_l1_dashboard_app(_CFG, l2_instance=minimal)
    gs = app.analysis.sheets[0]
    welcome_xml = gs.text_boxes[0].content
    assert "L2 instance description missing" in welcome_xml


def test_getting_started_title_is_constant_ui_vocabulary() -> None:
    """The title 'L1 Reconciliation Dashboard' is constant UI vocabulary
    (NOT pulled from L2). Per the M.2a.4 design note: titles stay
    hardcoded, subtitles + bodies pull from L2 descriptions."""
    app = build_l1_dashboard_app(_CFG)
    gs = app.analysis.sheets[0]
    assert "L1 Reconciliation Dashboard" in gs.text_boxes[0].content


# -- Drift sheet (M.2a.3) ----------------------------------------------------


def test_drift_sheet_present_after_m2a3() -> None:
    """M.2a.3 lands the Drift sheet — second tab in display order."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    drift = app.analysis.sheets[1]
    assert drift.name == "Drift"
    assert drift.title == "Account Balance Drift"


def test_drift_sheet_has_two_kpis_and_two_tables() -> None:
    """Drift sheet structure: 2 KPIs side-by-side + leaf table + parent
    table. KPIs surface the "how many violations" headline; tables surface
    "which accounts on which days"."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    drift = app.analysis.sheets[1]
    titles = [v.title for v in drift.visuals]
    assert titles == [
        "Leaf Accounts in Drift",
        "Parent Accounts in Drift",
        "Leaf Account Drift",
        "Parent Account Drift",
    ]


def test_drift_datasets_registered_and_target_l1_views() -> None:
    """The L1 drift datasets must be registered on the App and their
    custom SQL must target the per-L2-instance L1 invariant views by
    prefix — that's the M.2a "L1 dashboard configured by L2" promise."""
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_DRIFT,
        DS_LEDGER_DRIFT,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_DRIFT in registered_ids
    assert DS_LEDGER_DRIFT in registered_ids


def test_drift_dataset_sql_targets_prefixed_l1_views() -> None:
    """SQL for each drift dataset must SELECT from the L2-prefixed L1
    invariant view emitted by M.1a.7. Switching L2 instance switches the
    view targets — the parallel-stack v6 promise."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        build_drift_dataset,
        build_ledger_drift_dataset,
    )

    instance = default_l2_instance()
    prefix = instance.instance

    drift_ds = build_drift_dataset(_CFG, instance)
    ledger_ds = build_ledger_drift_dataset(_CFG, instance)

    drift_sql = next(iter(drift_ds.PhysicalTableMap.values())).CustomSql
    ledger_sql = next(iter(ledger_ds.PhysicalTableMap.values())).CustomSql
    assert drift_sql is not None
    assert ledger_sql is not None
    # Y.2.g — SQL now also carries the per-sheet pushdown WHERE
    # (account_id sentinel-OR + account_role IN (...)).
    assert drift_sql.SqlQuery.startswith(f"SELECT * FROM {prefix}_drift")
    assert ledger_sql.SqlQuery.startswith(f"SELECT * FROM {prefix}_ledger_drift")


# -- Drift Timelines sheet (M.2b.6) ------------------------------------------


def test_drift_timelines_sheet_present_after_m2b6() -> None:
    """M.2b.6 lands the Drift Timelines sheet — line-chart trends
    that complement the per-violation Drift table sheet."""
    app = build_l1_dashboard_app(_CFG)
    timelines = _sheet_by_name(app, "Drift Timelines")
    assert timelines.title == "Drift Magnitude Over Time"


def test_drift_timelines_has_two_kpis_and_two_line_charts() -> None:
    """Drift Timelines structure: 2 KPIs (max single-day Σ ABS(drift) for
    leaf + parent) + 2 LineCharts (one per role, plotting per-day
    Σ ABS(drift) over the visible date range)."""
    from quicksight_gen.common.tree import KPI, LineChart

    app = build_l1_dashboard_app(_CFG)
    timelines = _sheet_by_name(app, "Drift Timelines")
    titles = [v.title for v in timelines.visuals]
    assert titles == [
        "Largest Leaf Drift Day",
        "Largest Parent Drift Day",
        "Leaf Account Drift Over Time",
        "Parent Account Drift Over Time",
    ]
    kinds = [type(v).__name__ for v in timelines.visuals]
    assert kinds == ["KPI", "KPI", "LineChart", "LineChart"]
    # Sanity-check the LineCharts each carry one category, value, color
    # since the LineChart primitive supports the multi-line-by-color shape.
    for visual in timelines.visuals:
        if isinstance(visual, LineChart):
            assert len(visual.category) == 1, (
                f"{visual.title!r}: line charts plot one category dim"
            )
            assert len(visual.values) == 1
            assert len(visual.colors) == 1, (
                f"{visual.title!r}: one line per color value (account_role)"
            )
        elif isinstance(visual, KPI):
            assert len(visual.values) == 1


def test_drift_timeline_datasets_registered_and_aggregate_in_sql() -> None:
    """Both timeline datasets must register on the App + their SQL must
    GROUP BY the (day, role) keys so each dataset row IS one (day, role)
    cell — otherwise the line chart would re-aggregate at render time."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_DRIFT_TIMELINE,
        DS_LEDGER_DRIFT_TIMELINE,
        build_drift_timeline_dataset,
        build_ledger_drift_timeline_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_DRIFT_TIMELINE in registered_ids
    assert DS_LEDGER_DRIFT_TIMELINE in registered_ids

    instance = default_l2_instance()
    prefix = instance.instance

    drift_tl_ds = build_drift_timeline_dataset(_CFG, instance)
    ledger_tl_ds = build_ledger_drift_timeline_dataset(_CFG, instance)
    drift_sql = next(iter(drift_tl_ds.PhysicalTableMap.values())).CustomSql
    ledger_sql = next(
        iter(ledger_tl_ds.PhysicalTableMap.values())
    ).CustomSql
    assert drift_sql is not None
    assert ledger_sql is not None
    # SQL must aggregate ABS(drift) by (day, role) on the prefixed
    # invariant matview — that's the L1-invariant promise + the
    # per-render-not-per-row efficiency win.
    assert f"FROM {prefix}_drift" in drift_sql.SqlQuery
    assert "SUM(ABS(drift))" in drift_sql.SqlQuery
    assert "GROUP BY business_day_end, account_role" in drift_sql.SqlQuery
    assert f"FROM {prefix}_ledger_drift" in ledger_sql.SqlQuery
    assert "SUM(ABS(drift))" in ledger_sql.SqlQuery
    assert "GROUP BY business_day_end, account_role" in ledger_sql.SqlQuery


# -- Overdraft sheet (M.2a.4) ------------------------------------------------


def test_overdraft_sheet_present_after_m2a4() -> None:
    """M.2a.4 lands the Overdraft sheet — referenced by name (M.2b.6
    inserted Drift Timelines so positional index would shift)."""
    app = build_l1_dashboard_app(_CFG)
    overdraft = _sheet_by_name(app, "Overdraft")
    assert overdraft.title == "Internal Account Overdrafts"


def test_overdraft_sheet_has_kpi_and_table() -> None:
    """Overdraft sheet structure: 1 KPI (count) + 1 violations table.
    Single-dataset sheet — every row in the table IS one violation."""
    app = build_l1_dashboard_app(_CFG)
    overdraft = _sheet_by_name(app, "Overdraft")
    titles = [v.title for v in overdraft.visuals]
    assert titles == [
        "Internal Accounts in Overdraft",
        "Overdraft Violations",
    ]


def test_overdraft_dataset_registered_and_targets_l1_view() -> None:
    """The L1 overdraft dataset must be registered + its SQL must point
    at the L2-prefixed `<prefix>_overdraft` invariant view."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_OVERDRAFT,
        build_overdraft_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_OVERDRAFT in registered_ids

    instance = default_l2_instance()
    overdraft_ds = build_overdraft_dataset(_CFG, instance)
    sql = next(iter(overdraft_ds.PhysicalTableMap.values())).CustomSql
    assert sql is not None
    # Y.2.g — SQL also carries the Account / Account-Role pushdown WHERE.
    assert sql.SqlQuery.startswith(f"SELECT * FROM {instance.instance}_overdraft")


# -- Limit Breach sheet (M.2a.5) ---------------------------------------------


def test_limit_breach_sheet_present_after_m2a5() -> None:
    """M.2a.5 lands the Limit Breach sheet — referenced by name."""
    app = build_l1_dashboard_app(_CFG)
    lb = _sheet_by_name(app, "Limit Breach")
    assert lb.title == "Outbound Transfer Limit Breaches"


def test_limit_breach_sheet_has_kpi_and_table() -> None:
    """Limit Breach sheet structure: 1 KPI (count of breach cells) +
    1 detail table that puts outbound_total + cap side-by-side."""
    app = build_l1_dashboard_app(_CFG)
    lb = _sheet_by_name(app, "Limit Breach")
    titles = [v.title for v in lb.visuals]
    assert titles == ["Limit Breach Cells", "Limit Breach Detail"]


def test_limit_breach_dataset_registered_and_targets_l1_view() -> None:
    """The L1 limit-breach dataset must be registered + its SQL must
    point at the L2-prefixed `<prefix>_limit_breach` invariant view."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_LIMIT_BREACH,
        build_limit_breach_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_LIMIT_BREACH in registered_ids

    instance = default_l2_instance()
    lb_ds = build_limit_breach_dataset(_CFG, instance)
    sql = next(iter(lb_ds.PhysicalTableMap.values())).CustomSql
    assert sql is not None
    assert sql.SqlQuery.startswith(f"SELECT * FROM {instance.instance}_limit_breach")


# -- Today's Exceptions sheet (M.2a.6) ---------------------------------------


def test_todays_exceptions_sheet_present_after_m2a6() -> None:
    """M.2a.6 lands the Today's Exceptions sheet — referenced by name."""
    app = build_l1_dashboard_app(_CFG)
    te = _sheet_by_name(app, "Today's Exceptions")
    assert te.title == "Today's Exceptions"


def test_todays_exceptions_sheet_has_kpi_bar_table() -> None:
    """Today's Exceptions structure: 1 KPI (count) + 1 BarChart by
    check_type + 1 detail table sorted by magnitude DESC."""
    app = build_l1_dashboard_app(_CFG)
    te = _sheet_by_name(app, "Today's Exceptions")
    titles = [v.title for v in te.visuals]
    assert titles == [
        "Open Exceptions",
        "Exceptions by Check Type",
        "Exception Detail",
    ]


def test_todays_exceptions_dataset_reads_matview() -> None:
    """M.1a.9: the Today's Exceptions dataset SQL is a thin wrapper
    around `<prefix>_todays_exceptions` matview — the UNION ALL logic
    moved into the L1 schema in M.1a.9 so QS reads a precomputed
    table instead of re-running the 5-branch UNION per visual.
    """
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_TODAYS_EXCEPTIONS,
        build_todays_exceptions_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_TODAYS_EXCEPTIONS in registered_ids

    instance = default_l2_instance()
    te_ds = build_todays_exceptions_dataset(_CFG, instance)
    sql_obj = next(iter(te_ds.PhysicalTableMap.values())).CustomSql
    assert sql_obj is not None
    sql = sql_obj.SqlQuery
    # SQL is now just a SELECT * from the prefixed matview.
    assert sql.startswith(f"SELECT * FROM {instance.instance}_todays_exceptions")


# -- Transactions sheet (M.2b.5) ---------------------------------------------


def test_transactions_sheet_present_after_m2b5() -> None:
    """M.2b.5 lands the Transactions sheet — referenced by name."""
    app = build_l1_dashboard_app(_CFG)
    tx = _sheet_by_name(app, "Transactions")
    assert tx.title == "Posting Ledger"


def test_transactions_sheet_has_single_table() -> None:
    """Transactions has 1 detail table and no KPIs — its value is
    'show me every leg + filter'."""
    app = build_l1_dashboard_app(_CFG)
    tx = _sheet_by_name(app, "Transactions")
    titles = [v.title for v in tx.visuals]
    assert titles == ["Posting Ledger"]


def test_transactions_dataset_registered_and_targets_matview() -> None:
    """The new transactions dataset reads from the prefix's
    `<prefix>_current_transactions` matview (M.1a.9)."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_TRANSACTIONS,
        build_transactions_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_TRANSACTIONS in registered_ids

    instance = default_l2_instance()
    tx_ds = build_transactions_dataset(_CFG, instance)
    sql_obj = next(iter(tx_ds.PhysicalTableMap.values())).CustomSql
    assert sql_obj is not None
    assert (
        f"FROM {instance.instance}_current_transactions"
        in sql_obj.SqlQuery
    )


# -- Daily Statement sheet (M.2b.4) ------------------------------------------


def test_daily_statement_sheet_present_after_m2b4() -> None:
    """M.2b.4 lands the Daily Statement sheet — referenced by name."""
    app = build_l1_dashboard_app(_CFG)
    ds = _sheet_by_name(app, "Daily Statement")
    assert ds.title == "Per-Account Daily Statement"


def test_daily_statement_has_five_kpis_and_one_table() -> None:
    """Daily Statement structure: 5 KPIs side-by-side (Opening / Debits /
    Credits / Closing Stored / Drift) + 1 detail table."""
    app = build_l1_dashboard_app(_CFG)
    ds = _sheet_by_name(app, "Daily Statement")
    titles = [v.title for v in ds.visuals]
    assert titles == [
        "Opening Balance",
        "Debits",
        "Credits",
        "Closing Stored",
        "Drift",
        "Posted Money Records",
    ]


def test_daily_statement_parameters_and_controls() -> None:
    """M.2b.4: 2 new analysis-level parameters drive the sheet's
    per-account-day filter, surfaced as 2 sheet controls."""
    from quicksight_gen.apps.l1_dashboard.app import (
        P_L1_DS_ACCOUNT, P_L1_DS_BALANCE_DATE,
    )

    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    param_names = {p.name for p in app.analysis.parameters}
    assert P_L1_DS_ACCOUNT in param_names
    assert P_L1_DS_BALANCE_DATE in param_names

    ds = _sheet_by_name(app, "Daily Statement")
    control_titles = [
        c.title for c in ds.parameter_controls
        if hasattr(c, "title")
    ]
    assert "Account" in control_titles
    assert "Business Day" in control_titles


def test_daily_statement_filter_groups_target_correct_columns() -> None:
    """4 SINGLE_DATASET filter groups (2 datasets × 2 params), each
    column-specific: summary uses business_day_start; transactions
    uses business_day."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    fg_ids = {fg.filter_group_id for fg in app.analysis.filter_groups}
    expected = {
        # Y.2.g.9 — the account narrow now pushes into the dataset SQL
        # via the bridged pL1DsAccount param; only the date FilterGroups
        # remain analysis-level.
        "fg-l1-ds-summary-date",
        "fg-l1-ds-txn-date",
    }
    assert expected.issubset(fg_ids)


def test_daily_statement_datasets_registered() -> None:
    """Both new datasets register on the App tree + their SQL targets
    the prefixed L2 instance (mirrors the M.2a.3 pattern)."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_DAILY_STATEMENT_SUMMARY,
        DS_DAILY_STATEMENT_TRANSACTIONS,
        build_daily_statement_summary_dataset,
        build_daily_statement_transactions_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_DAILY_STATEMENT_SUMMARY in registered_ids
    assert DS_DAILY_STATEMENT_TRANSACTIONS in registered_ids

    instance = default_l2_instance()
    summary_ds = build_daily_statement_summary_dataset(_CFG, instance)
    txn_ds = build_daily_statement_transactions_dataset(_CFG, instance)

    summary_sql = next(
        iter(summary_ds.PhysicalTableMap.values())
    ).CustomSql
    txn_sql = next(iter(txn_ds.PhysicalTableMap.values())).CustomSql
    assert summary_sql is not None and txn_sql is not None
    # M.1a.9: summary reads from the daily_statement_summary matview
    # (the multi-CTE moved into the L1 schema). Transactions still
    # projects per-leg from current_transactions (which IS itself a
    # matview, so cheap).
    assert summary_sql.SqlQuery.startswith(
        f"SELECT * FROM {instance.instance}_daily_statement_summary"
    )
    assert f"FROM {instance.instance}_current_transactions" in txn_sql.SqlQuery


def test_daily_statement_transactions_business_day_is_dialect_aware() -> None:
    """P.4.a — the per-leg dataset's `business_day` column is built via
    ``date_trunc_day(dialect)`` so the projection stays a TIMESTAMP-
    shaped value across PG and Oracle. PG uses DATE_TRUNC; Oracle uses
    CAST(TRUNC(...) AS TIMESTAMP)."""
    from dataclasses import replace
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        build_daily_statement_transactions_dataset,
    )
    from quicksight_gen.common.sql import Dialect

    instance = default_l2_instance()
    cfg_pg = replace(_CFG, dialect=Dialect.POSTGRES)
    cfg_or = replace(_CFG, dialect=Dialect.ORACLE)

    sql_pg = next(iter(
        build_daily_statement_transactions_dataset(cfg_pg, instance)
        .PhysicalTableMap.values()
    )).CustomSql.SqlQuery
    sql_or = next(iter(
        build_daily_statement_transactions_dataset(cfg_or, instance)
        .PhysicalTableMap.values()
    )).CustomSql.SqlQuery

    assert "DATE_TRUNC('day', tx.posting) AS business_day" in sql_pg
    assert "CAST(TRUNC(tx.posting) AS TIMESTAMP) AS business_day" in sql_or
    # Oracle SQL must not carry the PG form anywhere.
    assert "DATE_TRUNC" not in sql_or


# -- Description-driven prose (M.2a.7) ---------------------------------------


def test_getting_started_coverage_lists_l2_inventory() -> None:
    """M.2a.7: Getting Started gets a second TextBox listing L2-derived
    inventory (account counts, rail counts, etc.) — switching L2
    instance changes the numbers, proving the seam."""
    app = build_l1_dashboard_app(_CFG)
    gs = app.analysis.sheets[0]
    assert len(gs.text_boxes) == 2
    coverage_xml = gs.text_boxes[1].content
    assert "L2 Coverage" in coverage_xml
    # Sasquatch fixture: 8 internal + 5 external accounts (per the M.2.1
    # hand-write). If the fixture changes, this test re-locks.
    assert "internal accounts" in coverage_xml
    assert "external accounts" in coverage_xml
    assert "rails" in coverage_xml
    assert "limit schedules" in coverage_xml


def test_drift_sheet_lists_internal_accounts_from_l2() -> None:
    """M.2a.7: Drift sheet's top TextBox enumerates internal accounts
    + roles from the L2 instance — analysts see the universe drift can
    surface against without leaving the sheet."""
    app = build_l1_dashboard_app(_CFG)
    drift = app.analysis.sheets[1]
    assert len(drift.text_boxes) == 1
    accounts_xml = drift.text_boxes[0].content
    assert "Internal Accounts in Scope" in accounts_xml
    # Sasquatch fixture has at least one GL control + one DDA template;
    # both should appear.
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    instance = default_l2_instance()
    internal_account_ids = [
        a.id for a in instance.accounts if a.scope == "internal"
    ]
    assert len(internal_account_ids) > 0, (
        "fixture must have internal accounts for this test to be meaningful"
    )
    # At least one internal account id appears in the rendered prose.
    assert any(aid in accounts_xml for aid in internal_account_ids)


def test_limit_breach_sheet_lists_l2_caps() -> None:
    """M.2a.7: Limit Breach sheet's top TextBox enumerates each L2
    LimitSchedule with its cap + L2-supplied prose. Analysts see "what's
    configured" before "what got breached"."""
    app = build_l1_dashboard_app(_CFG)
    lb = _sheet_by_name(app, "Limit Breach")
    assert len(lb.text_boxes) == 1
    config_xml = lb.text_boxes[0].content
    assert "Configured Caps" in config_xml
    # Each LimitSchedule renders a `parent_role × transfer_type: $cap`
    # line; the multiplication-sign separator is a structural marker
    # the test can key off.
    assert "×" in config_xml
    # Cap renders with $ prefix.
    assert "$" in config_xml


def test_todays_exceptions_footer_carries_l2_description() -> None:
    """M.2a.7: Today's Exceptions ends with a TextBox carrying the L2
    instance's top-level description — same prose as the Getting Started
    welcome, anchored at the bottom of the unified-view landing page."""
    app = build_l1_dashboard_app(_CFG)
    te = _sheet_by_name(app, "Today's Exceptions")
    assert len(te.text_boxes) == 1
    footer_xml = te.text_boxes[0].content
    assert "Institution Context" in footer_xml
    # Same fixture string the Getting Started welcome uses (M.3.2:
    # default L2 instance is spec_example).
    assert "Generic SPEC-shaped instance" in footer_xml


# -- Per-sheet filter controls (M.2b.3) --------------------------------------


def test_per_sheet_filter_dropdowns() -> None:
    """M.2b.3 + M.2b.5 + M.2b.6: each data-bearing sheet carries the
    right filter dropdowns.

    - Drift: Account + Account Role
    - Drift Timelines: Account Role
    - Overdraft: Account + Account Role
    - Limit Breach: Account + Transfer Type
    - Today's Exceptions: Check Type + Account + Transfer Type
    - Transactions: Account + Transfer + Status + Origin + Transfer Type

    Plus the date-range pickers from M.2b.1 (Date From / Date To).

    Y.2.g — these dropdowns are now parameter-backed (bridged to dataset
    pushdown params), so they live in ``parameter_controls`` rather than
    ``filter_controls``; this collects titles from both."""
    app = build_l1_dashboard_app(_CFG)

    def _filter_titles(sheet_name: str) -> set[str]:
        sheet = _sheet_by_name(app, sheet_name)
        return {
            ctrl.title
            for ctrl in (*sheet.filter_controls, *sheet.parameter_controls)
            if hasattr(ctrl, "title")
        }

    assert {"Account", "Account Role"}.issubset(_filter_titles("Drift"))
    assert {"Account Role"}.issubset(_filter_titles("Drift Timelines"))
    assert {"Account", "Account Role"}.issubset(_filter_titles("Overdraft"))
    assert {"Account", "Transfer Type"}.issubset(_filter_titles("Limit Breach"))
    assert {"Check Type", "Account", "Transfer Type"}.issubset(
        _filter_titles("Today's Exceptions"),
    )
    assert {"Account", "Transfer", "Status", "Origin", "Transfer Type"}.issubset(
        _filter_titles("Transactions"),
    )


# -- Theme + styling consistency audit (M.2b.13) -----------------------------


def test_no_hardcoded_hex_in_l1_dashboard_code() -> None:
    """M.2b.13 invariant: every color reference in the L1 dashboard app
    code resolves from the L2-instance theme — never a hardcoded
    `#xxxxxx` literal. A drift in this invariant means a future theme
    switch silently fails to repaint that visual.

    Scans both `app.py` and `datasets.py` for hex-color patterns
    (`#abc`, `#abcdef`, `#abcdef12`). Comments / docstrings allowed
    to mention hex strings as examples; this regex catches actual
    string literals only when they look like a CSS hex.
    """
    import re
    from pathlib import Path

    pkg_dir = (
        Path(__file__).parent.parent.parent
        / "src" / "quicksight_gen" / "apps" / "l1_dashboard"
    )
    hex_re = re.compile(r"['\"]#[0-9A-Fa-f]{3,8}['\"]")
    offenders: list[str] = []
    for py in pkg_dir.glob("*.py"):
        for n, line in enumerate(py.read_text().splitlines(), start=1):
            if hex_re.search(line):
                offenders.append(f"{py.name}:{n}: {line.strip()}")
    assert not offenders, (
        "L1 dashboard code MUST NOT contain hardcoded hex colors — "
        "resolve from theme.accent / .primary_fg / etc. instead. "
        f"Found:\n  " + "\n  ".join(offenders)
    )


# -- Conditional formatting on tables (M.2b.2) -------------------------------


def test_account_id_link_tints_on_every_table_with_account_id() -> None:
    """M.2b.2: every L1 dashboard table that exposes `account_id` tints
    it with the theme accent — visual cue that the column will become
    a drill source at M.2b.7. Theme accent is resolved from cfg, never
    hardcoded.

    Tables that don't expose `account_id` (e.g., Daily Statement's
    Posted Money Records, which is pre-filtered to one account by the
    sheet's parameter binding) are not required to carry the tint —
    there's nothing to drill from. The assertion walks each table's
    actual columns to decide whether the tint is required."""
    from quicksight_gen.common.theme import DEFAULT_PRESET
    from quicksight_gen.common.tree import CellAccentText, Table

    accent = DEFAULT_PRESET.accent
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None

    tinted_tables = 0
    for sheet in app.analysis.sheets:
        if sheet.name == "Getting Started":
            continue
        for visual in sheet.visuals:
            if not isinstance(visual, Table):
                continue
            col_names = {
                c.column.name for c in visual.columns
                if hasattr(c, "column") and hasattr(c.column, "name")
            }
            if "account_id" not in col_names:
                continue
            cf = visual.conditional_formatting or []
            tints = [
                f for f in cf
                if isinstance(f, CellAccentText) and f.color == accent
            ]
            assert len(tints) >= 1, (
                f"sheet {sheet.name!r} table {visual.title!r} exposes "
                f"account_id but is missing the theme-accent link tint"
            )
            tinted_tables += 1
    # 5 tables (drift leaf + drift parent + overdraft + limit breach +
    # today's exceptions) carry account_id and so should be tinted.
    assert tinted_tables >= 5, (
        f"expected at least 5 tables with account_id+tint, saw "
        f"{tinted_tables}"
    )


# -- Universal date-range filter (M.2b.1) ------------------------------------


def test_date_range_parameters_registered() -> None:
    """M.2b.1: P_L1_DATE_START + P_L1_DATE_END land on the analysis with
    rolling-date defaults."""
    from quicksight_gen.apps.l1_dashboard.app import (
        P_L1_DATE_END, P_L1_DATE_START,
    )

    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    param_names = {p.name for p in app.analysis.parameters}
    assert P_L1_DATE_START in param_names
    assert P_L1_DATE_END in param_names


def test_date_range_filter_groups_per_dataset() -> None:
    """One SINGLE_DATASET filter group lands per data-bearing dataset
    (5 total: drift, ledger_drift, overdraft, limit_breach, todays_exc)
    so the column-name mismatch (business_day_start vs business_day) is
    handled per-dataset rather than via cross-dataset matching."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    fg_ids = {fg.filter_group_id for fg in app.analysis.filter_groups}
    expected = {
        "fg-l1-date-drift",
        "fg-l1-date-ledger-drift",
        "fg-l1-date-overdraft",
        "fg-l1-date-limit-breach",
        "fg-l1-date-todays-exceptions",
    }
    assert expected.issubset(fg_ids)


def test_date_range_pickers_on_every_data_sheet() -> None:
    """Every data-bearing sheet (Drift, Drift Timelines, Overdraft,
    Limit Breach, Today's Exceptions) carries paired date pickers
    (Date From / Date To) bound to the shared params — controls sync
    via shared parameter binding so changing one moves all five."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    # Getting Started has no data → no date pickers.
    gs = _sheet_by_name(app, "Getting Started")
    assert len(gs.parameter_controls) == 0
    # Each of the 5 data-bearing universal-date sheets has 2 pickers.
    for sheet_name in (
        "Drift", "Drift Timelines", "Overdraft",
        "Limit Breach", "Today's Exceptions",
    ):
        sheet = _sheet_by_name(app, sheet_name)
        picker_titles = [
            ctrl.title for ctrl in sheet.parameter_controls
            if hasattr(ctrl, "title")
        ]
        assert "Date From" in picker_titles, (
            f"sheet {sheet.name!r} missing Date From picker"
        )
        assert "Date To" in picker_titles, (
            f"sheet {sheet.name!r} missing Date To picker"
        )


def test_date_range_filter_targets_correct_columns() -> None:
    """Per-dataset filter binding: drift/ledger_drift/overdraft target
    `business_day_start` (the daily-balance column); limit_breach +
    todays_exceptions target `business_day` (the truncated-posting
    column). The mismatch is what motivates per-dataset filter groups."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    by_id = {fg.filter_group_id: fg for fg in app.analysis.filter_groups}

    def _column_name(fg_id: str) -> str:
        fg = by_id[fg_id]
        flt = fg.filters[0]
        # TimeRangeFilter.column is a Column ref; .name exposes the col.
        return flt.column.name  # type: ignore[union-attr]: TimeRangeFilter always carries a Column by construction

    assert _column_name("fg-l1-date-drift") == "business_day_start"
    assert _column_name("fg-l1-date-ledger-drift") == "business_day_start"
    assert _column_name("fg-l1-date-overdraft") == "business_day_start"
    assert _column_name("fg-l1-date-limit-breach") == "business_day"
    assert _column_name("fg-l1-date-todays-exceptions") == "business_day"


# -- Pending Aging sheet (M.2b.10) -------------------------------------------


def test_pending_aging_sheet_present_after_m2b10() -> None:
    """M.2b.10 lands the Pending Aging sheet — referenced by name."""
    app = build_l1_dashboard_app(_CFG)
    pa = _sheet_by_name(app, "Pending Aging")
    assert pa.title == "Pending Transactions Aging Past Cap"


def test_pending_aging_sheet_has_kpi_bar_table() -> None:
    """Pending Aging structure: 1 KPI (count) + 1 horizontal BarChart
    (5 aging buckets) + 1 detail table sorted naturally by the
    number-prefixed bucket labels."""
    from quicksight_gen.common.tree import BarChart, KPI, Table

    app = build_l1_dashboard_app(_CFG)
    pa = _sheet_by_name(app, "Pending Aging")
    titles = [v.title for v in pa.visuals]
    assert titles == [
        "Stuck Pending",
        "Stuck Pending by Age Bucket",
        "Stuck Pending Detail",
    ]
    kinds = [type(v).__name__ for v in pa.visuals]
    assert kinds == ["KPI", "BarChart", "Table"]
    bar = next(v for v in pa.visuals if isinstance(v, BarChart))
    assert bar.orientation == "HORIZONTAL"


def test_pending_aging_buckets_computed_in_dataset_sql() -> None:
    """The 5 aging buckets are a portable ``CASE`` over ``age_seconds``
    in the dataset SQL aliased ``stuck_pending_aging_bucket`` (Y.3.e —
    the analysis-level CalcField was dropped when the buckets moved into
    the SQL). Number-prefixed labels keep the QS bar chart sort stable
    without an explicit sort_by override."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        build_stuck_pending_dataset,
    )

    sql = next(iter(
        build_stuck_pending_dataset(_CFG, default_l2_instance())
        .PhysicalTableMap.values()
    )).CustomSql.SqlQuery
    assert "CASE" in sql and "age_seconds" in sql
    assert "AS stuck_pending_aging_bucket" in sql
    for label in ("'1: 0-6h'", "'2: 6-24h'", "'3: 1-3d'",
                  "'4: 3-7d'", "'5: >7d'"):
        assert label in sql, f"missing bucket label {label}"

    # The bucket is no longer an analysis-level CalcField.
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    assert "stuck_pending_aging_bucket" not in {
        c.name for c in app.analysis.calc_fields
    }


def test_pending_aging_drill_to_transactions() -> None:
    """M.2b.7 drill plumbing — the detail table's right-click menu
    drills to Transactions and writes pL1TxTransfer."""
    from quicksight_gen.common.tree import Drill

    app = build_l1_dashboard_app(_CFG)
    pa = _sheet_by_name(app, "Pending Aging")
    table = next(v for v in pa.visuals if v.title == "Stuck Pending Detail")
    drills = [a for a in table.actions if isinstance(a, Drill)]
    assert len(drills) == 1
    drill = drills[0]
    assert drill.trigger == "DATA_POINT_MENU"
    assert drill.target_sheet.name == "Transactions"


def test_pending_aging_dataset_registered() -> None:
    """DS_STUCK_PENDING registers on the App tree + its SQL is the
    bucket-CASE SELECT over the prefixed `<prefix>_stuck_pending` matview
    with the Y.2.g pushdown WHERE (account_id data-value + transfer_type
    / rail_name enums via `<<$pL1Pending*>>`)."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_STUCK_PENDING,
        build_stuck_pending_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    assert DS_STUCK_PENDING in {ds.identifier for ds in app.datasets}

    instance = default_l2_instance()
    sp_ds = build_stuck_pending_dataset(_CFG, instance)
    sql_obj = next(iter(sp_ds.PhysicalTableMap.values())).CustomSql
    assert sql_obj is not None
    sql = sql_obj.SqlQuery
    assert sql.startswith("SELECT t.*,")
    assert f"FROM {instance.instance}_stuck_pending t" in sql
    assert "<<$pL1PendingType>>" in sql and "<<$pL1PendingRail>>" in sql
    assert sp_ds.DatasetParameters  # the pushdown dataset params are wired


# -- Unbundled Aging sheet (M.2b.11) -----------------------------------------


def test_unbundled_aging_sheet_present_after_m2b11() -> None:
    """M.2b.11 lands the Unbundled Aging sheet — referenced by name."""
    app = build_l1_dashboard_app(_CFG)
    ua = _sheet_by_name(app, "Unbundled Aging")
    assert ua.title == "Unbundled Posted Legs Aging Past Cap"


def test_unbundled_aging_sheet_has_kpi_bar_table() -> None:
    """Same KPI + horizontal BarChart + detail-table structure as
    Pending Aging, mirror but backed by stuck_unbundled."""
    from quicksight_gen.common.tree import BarChart, KPI, Table

    app = build_l1_dashboard_app(_CFG)
    ua = _sheet_by_name(app, "Unbundled Aging")
    titles = [v.title for v in ua.visuals]
    assert titles == [
        "Stuck Unbundled",
        "Stuck Unbundled by Age Bucket",
        "Stuck Unbundled Detail",
    ]
    kinds = [type(v).__name__ for v in ua.visuals]
    assert kinds == ["KPI", "BarChart", "Table"]
    bar = next(v for v in ua.visuals if isinstance(v, BarChart))
    assert bar.orientation == "HORIZONTAL"


def test_unbundled_aging_uses_4_buckets() -> None:
    """Aging buckets are coarser than Pending Aging (4 vs 5 bands) —
    `max_unbundled_age` is typically days, not hours. Same SQL-side
    CASE-over-`age_seconds` shape (Y.3.e), aliased
    `stuck_unbundled_aging_bucket`."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        build_stuck_unbundled_dataset,
    )

    sql = next(iter(
        build_stuck_unbundled_dataset(_CFG, default_l2_instance())
        .PhysicalTableMap.values()
    )).CustomSql.SqlQuery
    assert "CASE" in sql and "age_seconds" in sql
    assert "AS stuck_unbundled_aging_bucket" in sql
    for label in ("'1: <1d'", "'2: 1-2d'", "'3: 2-7d'", "'4: >7d'"):
        assert label in sql, f"missing bucket label {label}"
    # No 6h or 24h hour-grained buckets here (those are Pending Aging's).
    assert "0-6h" not in sql
    assert "6-24h" not in sql


def test_unbundled_aging_drill_to_transactions() -> None:
    """M.2b.7 drill plumbing — same shape as Pending Aging."""
    from quicksight_gen.common.tree import Drill

    app = build_l1_dashboard_app(_CFG)
    ua = _sheet_by_name(app, "Unbundled Aging")
    table = next(v for v in ua.visuals if v.title == "Stuck Unbundled Detail")
    drills = [a for a in table.actions if isinstance(a, Drill)]
    assert len(drills) == 1
    drill = drills[0]
    assert drill.trigger == "DATA_POINT_MENU"
    assert drill.target_sheet.name == "Transactions"


def test_unbundled_aging_dataset_registered() -> None:
    """DS_STUCK_UNBUNDLED registers + its SQL is the bucket-CASE SELECT
    over the prefixed `<prefix>_stuck_unbundled` matview with the Y.2.g
    pushdown WHERE (`<<$pL1Unbundled*>>`)."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_STUCK_UNBUNDLED,
        build_stuck_unbundled_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    assert DS_STUCK_UNBUNDLED in {ds.identifier for ds in app.datasets}

    instance = default_l2_instance()
    su_ds = build_stuck_unbundled_dataset(_CFG, instance)
    sql_obj = next(iter(su_ds.PhysicalTableMap.values())).CustomSql
    assert sql_obj is not None
    sql = sql_obj.SqlQuery
    assert sql.startswith("SELECT t.*,")
    assert f"FROM {instance.instance}_stuck_unbundled t" in sql
    assert "<<$pL1UnbundledType>>" in sql and "<<$pL1UnbundledRail>>" in sql
    assert su_ds.DatasetParameters


# -- Supersession Audit sheet (M.2b.12) --------------------------------------


def test_supersession_audit_sheet_present_after_m2b12() -> None:
    """M.2b.12 lands the Supersession Audit sheet — referenced by name."""
    app = build_l1_dashboard_app(_CFG)
    sa = _sheet_by_name(app, "Supersession Audit")
    assert sa.title == "Supersession Audit Trail"


def test_supersession_audit_has_kpis_and_two_tables() -> None:
    """Supersession Audit structure (Q.1.c): 2 KPIs side-by-side
    (count of distinct logical keys + count of higher-Entry rows
    with no `supersedes` reason) + 1 transactions audit table +
    1 daily-balances audit table."""
    from quicksight_gen.common.tree import KPI, Table

    app = build_l1_dashboard_app(_CFG)
    sa = _sheet_by_name(app, "Supersession Audit")
    titles = [v.title for v in sa.visuals]
    assert titles == [
        "Logical Keys with Supersession",
        "Supersessions with No Reason",
        "Transactions Audit",
        "Daily Balances Audit",
    ]
    kinds = [type(v).__name__ for v in sa.visuals]
    assert kinds == ["KPI", "KPI", "Table", "Table"]


def test_supersession_datasets_registered_and_target_base_tables() -> None:
    """Both supersession datasets register on the App and read from
    the BASE tables (NOT Current*) — Current* hides superseded
    entries by design, but the audit specifically needs them."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_SUPERSESSION_DAILY_BALANCES,
        DS_SUPERSESSION_TRANSACTIONS,
        build_supersession_daily_balances_dataset,
        build_supersession_transactions_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered_ids = {ds.identifier for ds in app.datasets}
    assert DS_SUPERSESSION_TRANSACTIONS in registered_ids
    assert DS_SUPERSESSION_DAILY_BALANCES in registered_ids

    instance = default_l2_instance()
    prefix = instance.instance

    tx_ds = build_supersession_transactions_dataset(_CFG, instance)
    db_ds = build_supersession_daily_balances_dataset(_CFG, instance)
    tx_sql = next(iter(tx_ds.PhysicalTableMap.values())).CustomSql
    db_sql = next(iter(db_ds.PhysicalTableMap.values())).CustomSql
    assert tx_sql is not None
    assert db_sql is not None
    # Both target the BASE tables (no `current_` prefix).
    assert f" {prefix}_transactions" in tx_sql.SqlQuery
    assert f"{prefix}_current_transactions" not in tx_sql.SqlQuery
    assert f" {prefix}_daily_balances" in db_sql.SqlQuery
    assert f"{prefix}_current_daily_balances" not in db_sql.SqlQuery
    # Both surface only logical keys with multiple entries via window
    # function (window form survives QS dropdown query rewriting where
    # the IN-subquery + ORDER BY combo doesn't).
    assert "COUNT(*) OVER (PARTITION BY id)" in tx_sql.SqlQuery
    assert (
        "COUNT(*) OVER (PARTITION BY account_id, business_day_start)"
        in db_sql.SqlQuery
    )
    assert "entry_count > 1" in tx_sql.SqlQuery
    assert "entry_count > 1" in db_sql.SqlQuery


def test_supersession_audit_has_supersedes_filter() -> None:
    """Supersession Audit carries one dropdown: supersedes reason
    (Y.2.g — now a parameter-backed pushdown control). Daily-balances
    doesn't get a paired filter (low signal)."""
    app = build_l1_dashboard_app(_CFG)
    sa = _sheet_by_name(app, "Supersession Audit")
    titles = {
        ctrl.title
        for ctrl in (*sa.filter_controls, *sa.parameter_controls)
        if hasattr(ctrl, "title")
    }
    assert "Supersedes Reason" in titles


# -- Cross-sheet drill plumbing (M.2b.7) -------------------------------------


def test_drill_target_parameters_registered() -> None:
    """M.2b.7: 2 sentinel-pattern params land on the analysis with the
    "__ALL__" default. They never surface as sheet controls — drills
    set them, and the destination calc fields treat the sentinel as
    PASS so the un-drilled state shows everything."""
    from quicksight_gen.apps.l1_dashboard.app import (
        P_L1_FILTER_ACCOUNT, P_L1_TX_TRANSFER,
    )

    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    by_name = {p.name: p for p in app.analysis.parameters}
    assert P_L1_FILTER_ACCOUNT in by_name
    assert P_L1_TX_TRANSFER in by_name
    # Both default to the sentinel string.
    assert by_name[P_L1_FILTER_ACCOUNT].default == ["__ALL__"]
    assert by_name[P_L1_TX_TRANSFER].default == ["__ALL__"]


def test_drill_calc_fields_present() -> None:
    """M.2b.7: 5 sentinel-or-match calc fields, one per drill destination
    dataset (drift, ledger_drift, overdraft, limit_breach, transactions).
    Each calc field's expression compares its dataset's column against
    the sentinel-pattern parameter; PASS when the param is "__ALL__"
    OR the column matches; FAIL otherwise."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    calc_names = {c.name for c in app.analysis.calc_fields}
    expected = {
        "_drill_pass_pL1FilterAccount_on_drift",
        "_drill_pass_pL1FilterAccount_on_ledger_drift",
        "_drill_pass_pL1FilterAccount_on_overdraft",
        "_drill_pass_pL1FilterAccount_on_limit_breach",
        "_drill_pass_pL1TxTransfer_on_transactions",
    }
    assert expected.issubset(calc_names), (
        f"missing calc fields: {expected - calc_names}"
    )

    # Each expression is the sentinel-or-match shape.
    by_name = {c.name: c for c in app.analysis.calc_fields}
    for cf_name in expected:
        cf = by_name[cf_name]
        assert "'__ALL__'" in cf.expression, (
            f"{cf_name} missing sentinel guard"
        )
        assert "'PASS'" in cf.expression
        assert "'FAIL'" in cf.expression


def test_drill_filter_groups_present() -> None:
    """M.2b.7: 5 SINGLE_DATASET FilterGroups (one per destination)
    apply the calc-field PASS filter to scope each destination sheet's
    visuals when its sentinel-pattern param is set."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    fg_ids = {fg.filter_group_id for fg in app.analysis.filter_groups}
    expected = {
        "fg-l1-drill-account-on-drift",
        "fg-l1-drill-account-on-ledger-drift",
        "fg-l1-drill-account-on-overdraft",
        "fg-l1-drill-account-on-limit-breach",
        "fg-l1-drill-transfer-on-transactions",
    }
    assert expected.issubset(fg_ids), (
        f"missing filter groups: {expected - fg_ids}"
    )


def test_todays_exceptions_table_carries_two_drills() -> None:
    """M.2b.7: Exception Detail table has 2 drill actions —
    DATA_POINT_CLICK → Drift (back-toward source per CLAUDE drill
    direction); DATA_POINT_MENU → Daily Statement (forward into the
    per-account-day investigation).
    """
    from quicksight_gen.common.tree import Drill

    app = build_l1_dashboard_app(_CFG)
    te = _sheet_by_name(app, "Today's Exceptions")
    detail = next(v for v in te.visuals if v.title == "Exception Detail")
    drills = [a for a in detail.actions if isinstance(a, Drill)]
    assert len(drills) == 2

    by_trigger = {d.trigger: d for d in drills}
    assert "DATA_POINT_CLICK" in by_trigger
    assert "DATA_POINT_MENU" in by_trigger

    # DATA_POINT_CLICK targets Drift (back-toward source).
    click = by_trigger["DATA_POINT_CLICK"]
    assert click.target_sheet.name == "Drift"
    # DATA_POINT_MENU targets Daily Statement (forward).
    menu = by_trigger["DATA_POINT_MENU"]
    assert menu.target_sheet.name == "Daily Statement"


def test_per_invariant_sheets_drill_to_daily_statement() -> None:
    """M.2b.7: each per-invariant detail table (Drift leaf + parent,
    Overdraft, Limit Breach) has a DATA_POINT_MENU drill into Daily
    Statement that writes (account, business_day) into the picker
    parameters."""
    from quicksight_gen.common.tree import Drill

    app = build_l1_dashboard_app(_CFG)
    expected: list[tuple[str, str]] = [
        ("Drift", "Leaf Account Drift"),
        ("Drift", "Parent Account Drift"),
        ("Overdraft", "Overdraft Violations"),
        ("Limit Breach", "Limit Breach Detail"),
    ]
    for sheet_name, table_title in expected:
        sheet = _sheet_by_name(app, sheet_name)
        table = next(v for v in sheet.visuals if v.title == table_title)
        drills = [a for a in table.actions if isinstance(a, Drill)]
        menu_drills = [d for d in drills if d.trigger == "DATA_POINT_MENU"]
        assert len(menu_drills) == 1, (
            f"{sheet_name}/{table_title}: expected 1 menu drill, got "
            f"{len(menu_drills)}"
        )
        assert menu_drills[0].target_sheet.name == "Daily Statement"


def test_daily_statement_drills_to_transactions() -> None:
    """M.2b.7: Daily Statement detail table has DATA_POINT_MENU drill
    into Transactions that writes transfer_id into pL1TxTransfer."""
    from quicksight_gen.common.tree import Drill

    app = build_l1_dashboard_app(_CFG)
    ds = _sheet_by_name(app, "Daily Statement")
    table = next(v for v in ds.visuals if v.title == "Posted Money Records")
    drills = [a for a in table.actions if isinstance(a, Drill)]
    assert len(drills) == 1
    drill = drills[0]
    assert drill.trigger == "DATA_POINT_MENU"
    assert drill.target_sheet.name == "Transactions"


def test_drill_emission_navigation_plus_set_parameters() -> None:
    """M.2b.7: every drill emits BOTH a NavigationOperation (target
    sheet) and a SetParametersOperation (writes + auto-reset). End-to-
    end check via to_aws_json walk."""
    app = build_l1_dashboard_app(_CFG)
    j = app.emit_analysis().to_aws_json()
    drill_count = 0
    for sheet in j["Definition"]["Sheets"]:
        for visual in sheet.get("Visuals") or []:
            for kind, body in visual.items():
                if not isinstance(body, dict):
                    continue
                for action in body.get("Actions") or []:
                    op_kinds = {
                        next(iter(op.keys()))
                        for op in action.get("ActionOperations") or []
                    }
                    assert "NavigationOperation" in op_kinds, (
                        f"action {action['Name']!r} missing nav op"
                    )
                    assert "SetParametersOperation" in op_kinds, (
                        f"action {action['Name']!r} missing set-params op"
                    )
                    drill_count += 1
    # 8 drill source sites: Today's Exc (2), Drift (2), Overdraft (1),
    # Limit Breach (1), Pending Aging (1), Unbundled Aging (1), Daily
    # Statement (1) = 9 total drills.
    assert drill_count == 9, (
        f"expected 9 drills total, saw {drill_count}"
    )


# -- Emit shape (substitutability with other apps) ---------------------------


def test_analysis_emits_with_expected_id_suffix() -> None:
    app = build_l1_dashboard_app(_CFG)
    analysis = app.emit_analysis()
    assert analysis.AnalysisId.endswith("-l1-dashboard-analysis")


def test_dashboard_emits_with_expected_id_suffix() -> None:
    """Per the M.2d.3 naming: `<resource_prefix>-<l2_prefix>-l1-dashboard`.

    The L2 instance prefix becomes the middle segment so multiple L2
    instances coexist in one QS account. The default L2 instance is
    `spec_example` (M.3.2 repointed from sasquatch_ar to a
    persona-neutral default for production library code), so the
    full DashboardId is `qs-gen-spec_example-l1-dashboard`.
    """
    app = build_l1_dashboard_app(_CFG)
    dashboard = app.emit_dashboard()
    assert dashboard.DashboardId.endswith("-l1-dashboard")
    assert dashboard.DashboardId == "qs-gen-spec_example-l1-dashboard"


# -- CLI smoke (M.2a.9) ------------------------------------------------------


class TestCli:
    """`quicksight-gen generate l1-dashboard` writes the expected files
    + the L1 dashboard is included in the `--all` shortcut. Mirrors
    the shape of test_executives.py::TestCli."""

    def _base_config(self, tmp_path: Path) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(
            "aws_account_id: '111122223333'\n"
            "aws_region: us-west-2\n"
            "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
            ":datasource/ds\n"
        )
        return p

    def test_json_apply_writes_l1_dashboard(self, tmp_path: Path):
        """Q.3.a: ``json apply`` is the bundled emit verb; the L1
        dashboard JSON files are part of the output set."""
        config = self._base_config(tmp_path)
        out = tmp_path / "out"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["json", "apply", "-c", str(config), "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert (out / "l1-dashboard-analysis.json").exists()
        assert (out / "l1-dashboard-dashboard.json").exists()
        # Datasets land in out/datasets/ with the M.2d.3 L2-instance
        # middle segment in their IDs (`<resource_prefix>-<l2_prefix>-...`).
        ds_dir = out / "datasets"
        for name in (
            "qs-gen-spec_example-l1-drift-dataset.json",
            "qs-gen-spec_example-l1-ledger-drift-dataset.json",
            "qs-gen-spec_example-l1-overdraft-dataset.json",
            "qs-gen-spec_example-l1-limit-breach-dataset.json",
            "qs-gen-spec_example-l1-todays-exceptions-dataset.json",
            "qs-gen-spec_example-l1-daily-statement-summary-dataset.json",
            "qs-gen-spec_example-l1-daily-statement-transactions-dataset.json",
            "qs-gen-spec_example-l1-transactions-dataset.json",
        ):
            assert (ds_dir / name).exists(), f"missing {name}"



# -- Y.2.g — per-sheet categorical filter pushdown ---------------------------


def test_y2g_enum_value_helpers_reflect_l2_instance() -> None:
    """The enum-value helpers feeding the pushdown dropdowns return the
    L2-declared universe (transfer types / rails / account roles) or the
    fixed schema enums (supersede reasons / check types). These are the
    `StaticValues` defaults baked into the dataset params + dropdown
    options — switching L2 instance switches the dropdown contents."""
    from quicksight_gen.apps.l1_dashboard.datasets import (
        l1_account_role_values,
        l1_check_type_values,
        l1_rail_values,
        l1_supersede_reason_values,
        l1_transfer_type_values,
    )

    instance = default_l2_instance()

    rail_names = {str(r.name) for r in instance.rails}
    assert set(l1_rail_values(instance)) == rail_names
    assert l1_rail_values(instance) == sorted(rail_names)

    declared_types = {str(r.name) for r in instance.rails}
    declared_types |= {str(ls.rail) for ls in instance.limit_schedules}
    assert set(l1_transfer_type_values(instance)) == declared_types

    declared_roles = {str(a.role) for a in instance.accounts if a.role}
    declared_roles |= {str(t.role) for t in instance.account_templates}
    assert set(l1_account_role_values(instance)) == declared_roles

    # Fixed schema enums — L2-independent.
    assert l1_supersede_reason_values() == [
        "BundleAssignment", "Inflight", "TechnicalCorrection",
    ]
    assert l1_check_type_values() == [
        "drift", "expected_eod_balance_breach", "ledger_drift",
        "limit_breach", "overdraft", "stuck_pending", "stuck_unbundled",
    ]


def _dataset_param_names(ds) -> list[str]:
    """The ``Name`` of each StringDatasetParameter on a built DataSet."""
    out: list[str] = []
    for dp in ds.DatasetParameters or []:
        sdp = dp.StringDatasetParameter
        if sdp is not None:
            out.append(sdp.Name)
    return out


def test_y2g_datasets_declare_pushdown_params() -> None:
    """Every per-sheet-filtered L1 dataset declares the dataset-level
    parameters that its CustomSql substitutes (``<<$pX>>``) — otherwise
    the analysis-param → dataset-param bridge has no target and the
    dropdown is a no-op."""
    from quicksight_gen.apps.l1_dashboard.datasets import (
        build_drift_dataset,
        build_ledger_drift_dataset,
        build_drift_timeline_dataset,
        build_ledger_drift_timeline_dataset,
        build_overdraft_dataset,
        build_limit_breach_dataset,
        build_todays_exceptions_dataset,
        build_stuck_pending_dataset,
        build_stuck_unbundled_dataset,
        build_supersession_transactions_dataset,
        build_transactions_dataset,
        build_daily_statement_summary_dataset,
        build_daily_statement_transactions_dataset,
    )

    inst = default_l2_instance()
    cases = {
        build_drift_dataset: {"pL1DriftAccount", "pL1DriftRole"},
        build_ledger_drift_dataset: {"pL1DriftAccount", "pL1DriftRole"},
        build_drift_timeline_dataset: {"pL1DriftTlRole"},
        build_ledger_drift_timeline_dataset: {"pL1DriftTlRole"},
        build_overdraft_dataset: {"pL1OverdraftAccount", "pL1OverdraftRole"},
        build_limit_breach_dataset:
            {"pL1LimitBreachAccount", "pL1LimitBreachType"},
        build_todays_exceptions_dataset: {
            "pL1TodaysExcCheckType", "pL1TodaysExcAccount", "pL1TodaysExcType",
        },
        build_stuck_pending_dataset:
            {"pL1PendingAccount", "pL1PendingType", "pL1PendingRail"},
        build_stuck_unbundled_dataset:
            {"pL1UnbundledAccount", "pL1UnbundledType", "pL1UnbundledRail"},
        build_supersession_transactions_dataset: {"pL1SupersedeReason"},
        build_transactions_dataset: {
            "pL1TxAccount", "pL1TxTransferId", "pL1TxStatus",
            "pL1TxOrigin", "pL1TxType",
        },
        build_daily_statement_summary_dataset: {"pL1DsAccount"},
        build_daily_statement_transactions_dataset: {"pL1DsAccount"},
    }
    for builder, expected in cases.items():
        ds = builder(_CFG, inst)
        names = set(_dataset_param_names(ds))
        assert names == expected, f"{builder.__name__}: {names} != {expected}"
        sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
        for pn in expected:
            assert f"<<${pn}>>" in sql, f"{builder.__name__} SQL missing <<${pn}>>"


def test_y2g_companion_datasets_registered_and_unparameterized() -> None:
    """The three Y.2.g companion datasets (accounts / transfer ids /
    tx facets) register on the App + are themselves unparameterized
    DISTINCT projections — so the dropdowns reading their options via
    LinkedValues see the full universe, not a narrowed slice."""
    from quicksight_gen.apps.l1_dashboard.datasets import (
        DS_L1_ACCOUNTS, DS_L1_TX_FACETS, DS_L1_TX_IDS,
        build_l1_accounts_dataset, build_l1_tx_facets_dataset,
        build_l1_tx_ids_dataset,
    )

    app = build_l1_dashboard_app(_CFG)
    registered = {ds.identifier for ds in app.datasets}
    assert {DS_L1_ACCOUNTS, DS_L1_TX_IDS, DS_L1_TX_FACETS}.issubset(registered)

    inst = default_l2_instance()
    for builder, frag in (
        (build_l1_accounts_dataset, "SELECT DISTINCT account_id"),
        (build_l1_tx_ids_dataset, "SELECT DISTINCT transfer_id"),
        (build_l1_tx_facets_dataset, "SELECT DISTINCT status, origin"),
    ):
        ds = builder(_CFG, inst)
        assert not ds.DatasetParameters, f"{builder.__name__} should be unparameterized"
        sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
        assert sql.startswith(frag)
        assert "<<$" not in sql


def test_y2g_no_per_sheet_category_filter_groups_remain() -> None:
    """The X.1.g/M.2b.3 per-sheet ``fg-l1-<sheet>-<col>`` category-filter
    FilterGroups (the cold-fetch footgun source) are gone — the
    narrowing moved into dataset SQL. Only the date-range FilterGroups
    and the drill sentinel FilterGroups should remain on the analysis."""
    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    fg_ids = {fg.filter_group_id for fg in app.analysis.filter_groups}
    # None of the old per-sheet category dropdown FGs survive.
    stale = {
        "fg-l1-drift-account", "fg-l1-drift-role", "fg-l1-drift-tl-role",
        "fg-l1-overdraft-account", "fg-l1-overdraft-role",
        "fg-l1-limit-breach-account", "fg-l1-limit-breach-type",
        "fg-l1-pending-account", "fg-l1-pending-type", "fg-l1-pending-rail",
        "fg-l1-unbundled-account", "fg-l1-unbundled-type",
        "fg-l1-unbundled-rail", "fg-l1-supersession-reason",
        "fg-l1-todays-exc-check-type", "fg-l1-todays-exc-account",
        "fg-l1-todays-exc-type",
        "fg-l1-tx-account", "fg-l1-tx-transfer", "fg-l1-tx-status",
        "fg-l1-tx-origin", "fg-l1-tx-type",
        "fg-l1-ds-summary-account", "fg-l1-ds-txn-account",
    }
    assert fg_ids.isdisjoint(stale), f"stale per-sheet FGs left: {fg_ids & stale}"


def test_y2g_drift_dropdowns_bridge_to_both_drift_datasets() -> None:
    """The Drift sheet's Account + Account-Role dropdowns are
    cross-dataset (one control narrows both the leaf-drift and
    ledger-drift tables). After Y.2.g that means each analysis param
    bridges to a same-named dataset param on BOTH the drift and
    ledger-drift datasets."""
    from quicksight_gen.common.tree import StringParam

    app = build_l1_dashboard_app(_CFG)
    assert app.analysis is not None
    by_name = {p.name: p for p in app.analysis.parameters}
    for pname in ("pL1DriftAccount", "pL1DriftRole"):
        param = by_name[pname]
        assert isinstance(param, StringParam)
        assert param.multi_valued
        bridged_ds_ids = {ds.identifier for ds, _ in (param.mapped_dataset_params or [])}
        from quicksight_gen.apps.l1_dashboard.datasets import (
            DS_DRIFT, DS_LEDGER_DRIFT,
        )
        assert {DS_DRIFT, DS_LEDGER_DRIFT}.issubset(bridged_ds_ids)
