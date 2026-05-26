"""Browser test: walk every Executives sheet, verify visuals render — both renderers.

Parametrized over ``[qs, app2]`` (X.2.u.2 — the ``exec_dashboard_driver``
fixture yields ``(driver, dashboard_arg)``: the deployed QS dashboard,
or a locally-spun App 2 server built from the same ``exec_app`` tree
reading the same DB). ``TreeValidator(exec_app, driver).validate_structure()``
walks every sheet, asserts each declared visual title + control label is
in the DOM; failures across sheets accumulate into one AssertionError.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datetime import date, datetime
from decimal import Decimal

import pytest

from recon_gen.apps.executives.datasets import (
    build_account_summary_active_dataset,
    build_account_summary_dataset,
    build_transaction_summary_dataset,
)
from tests.e2e._kpi_parse import parse_currency_kpi, parse_int_kpi

from .tree_validator import TreeValidator
from recon_gen.common.config import Config



if TYPE_CHECKING:
    from recon_gen.common.tree import App
    from tests.e2e._drivers import DashboardDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _as_date(value: object) -> date:
    """Coerce a DB cell value to ``date`` for window-membership checks.

    Postgres returns ``datetime``/``date`` natively; SQLite may return
    a string. Tests narrow rows by the analysis-layer date window
    (``frame.window.contains(...)``) — DateInterval expects a ``date``.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        # ISO-8601 either bare date or full timestamp; split at "T" so
        # we don't need a full parse.
        return date.fromisoformat(value.split("T", 1)[0].split(" ", 1)[0])
    raise TypeError(
        f"_as_date: cannot coerce {type(value).__name__} → date "
        f"(value={value!r})"
    )


def test_exec_dashboard_structure_matches_tree(exec_dashboard_driver: tuple["DashboardDriver", str], exec_app: "App") -> None:
    driver, dashboard_arg = exec_dashboard_driver
    # App 2 is local + fast — see test_l1_sheet_visuals for the rationale.
    timeout_ms = 12_000 if driver.dialect == "app2" else 30_000
    driver.open(dashboard_arg)
    TreeValidator(exec_app, driver, timeout_ms=timeout_ms).validate_structure()
    driver.screenshot()


# BG.5 — Executives KPI honest gates ---------------------------------------


def _sql_for(builder, *args):  # type: ignore[no-untyped-def]: builder takes (cfg) at runtime — annotating would force imports here
    ds = builder(*args)
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
    return sql, list(ds.DatasetParameters or ())


def test_bg5_transaction_volume_kpis_match_dataset_aggregates(
    exec_dashboard_driver: tuple["DashboardDriver", str], cfg: Config,
) -> None:
    """BG.5 — Transaction Volume sheet KPIs must equal aggregates over
    the production transaction summary dataset.

    Two assertions:

    1. **Total Transactions** == SUM(transfer_count) over the dataset
       — equivalent to ``COUNT(DISTINCT transfer_id) FROM
       <prefix>_transactions WHERE status='Posted'`` after the dataset's
       per-transfer collapse (GROUP BY transfer_id, rail_name).

    2. **Average Daily Volume** is sourced from a separate daily-rollup
       dataset (``ds_daily``) — see ``AO.5`` fix note. We just gate
       Total Transactions here; the daily-rollup KPI gets its own
       tightening when a finding surfaces.

    v11.21.0 finding #8 framing: "Total Transactions KPI = 2,403,163
    vs App Info matview row_count = 3,032,345". The gap is the
    status='Posted' filter + per-transfer collapse vs the App Info
    per-leg / all-status count. NOT a bug per the triage doc — but
    the KPI MUST match its underlying dataset's SUM(transfer_count),
    or the binding has drifted. This test enforces that contract.
    """
    driver, dashboard_arg = exec_dashboard_driver
    # Sheet renamed "Transaction Volume" → "Transaction Volume Over
    # Time" earlier in the v11.22.x cycle; test was missed.
    driver.open(dashboard_arg, sheet="Transaction Volume Over Time")
    driver.wait_loaded("Total Transactions (Posted, per-transfer)")

    sql, params = _sql_for(build_transaction_summary_dataset, cfg)
    rows = driver.query_db(sql, dataset_parameters=params)
    # The deployed KPI is scoped by the Exec dashboard's default 30-day
    # window (cfg.test_generator.as_of_frame(window_days=30); see exec/app.py:869). Our
    # direct DB query sees all 90 days; narrow rows to the same window
    # before summing or the assertion compares apples-to-oranges.
    frame = cfg.test_generator.as_of_frame(window_days=30)
    in_window = [
        r for r in rows
        if frame.window.contains(_as_date(r["posted_date"]))
    ]
    expected_total = sum(int(row["transfer_count"]) for row in in_window)
    rendered = parse_int_kpi(
        driver.kpi_value("Total Transactions (Posted, per-transfer)"),
    )
    assert rendered == expected_total, (
        f"Total Transactions: rendered {rendered} ≠ "
        f"SUM(transfer_count) over transaction-summary dataset = "
        f"{expected_total}. v11.21.0 cold-read finding #8 root contract: "
        f"the KPI must match its dataset's aggregate. If THIS trips, "
        f"the binding has drifted from SUM(transfer_count). The "
        f"narrative gap vs App Info's per-leg row count is documented "
        f"as a predicate-mismatch (status='Posted' + per-transfer "
        f"collapse) — not what THIS assertion catches; that's the "
        f"sheet's subtitle to clarify."
    )
    driver.screenshot()


def test_bg5_money_moved_kpis_match_dataset_sums(exec_dashboard_driver: tuple["DashboardDriver", str], cfg: Config) -> None:
    """BG.5 — Money Moved sheet KPIs (Gross + Net) must equal sums
    over the production transaction summary dataset.

    - Gross Money Moved == SUM(gross_amount)
    - Net Money Moved == SUM(net_amount)

    Same KPI-binding-vs-dataset-aggregate contract as Total
    Transactions. Catches any wrong-measure or scope binding shift
    (e.g. SUM-over-status-Posted-only on one KPI but SUM-over-all
    on another)."""
    driver, dashboard_arg = exec_dashboard_driver
    driver.open(dashboard_arg, sheet="Money Moved")
    driver.wait_loaded("Gross Money Moved")

    sql, params = _sql_for(build_transaction_summary_dataset, cfg)
    rows = driver.query_db(sql, dataset_parameters=params)
    # Window-narrow per the Exec dashboard's default 30-day scope
    # (cfg.test_generator.as_of_frame(window_days=30); see exec/app.py:869). The
    # KPI sees this window; direct DB query sees all 90 days.
    frame = cfg.test_generator.as_of_frame(window_days=30)
    in_window = [
        r for r in rows
        if frame.window.contains(_as_date(r["posted_date"]))
    ]
    expected_gross = sum(
        (Decimal(str(row["gross_amount"])) for row in in_window),
        Decimal("0"),
    )
    expected_net = sum(
        (Decimal(str(row["net_amount"])) for row in in_window),
        Decimal("0"),
    )

    rendered_gross = parse_currency_kpi(driver.kpi_value("Gross Money Moved"))
    assert rendered_gross == expected_gross, (
        f"Gross Money Moved: rendered {rendered_gross} ≠ "
        f"SUM(gross_amount) = {expected_gross}."
    )
    rendered_net = parse_currency_kpi(driver.kpi_value("Net Money Moved"))
    assert rendered_net == expected_net, (
        f"Net Money Moved: rendered {rendered_net} ≠ "
        f"SUM(net_amount) = {expected_net}."
    )
    driver.screenshot()


def test_bg5_account_summary_kpis_match_dataset_counts(
    exec_dashboard_driver: tuple["DashboardDriver", str], cfg: Config,
) -> None:
    """BG.5 — Open Accounts sheet KPIs (Total Open / Active) must
    equal row counts of the production account summary datasets.

    Both bind ``.count()`` on ``account_id`` over their respective
    datasets. The Active KPI uses a separately-narrowed dataset
    (``ds_acct_active``) where ``COALESCE(activity_count, 0) > 0`` is
    baked into the SQL (Y.2.h) — so Active's count comes from a
    different dataset than Total Open's. Two-dataset identity catches
    a regression where the two KPIs accidentally re-merge onto the
    same dataset (Active would jump to match Total Open)."""
    driver, dashboard_arg = exec_dashboard_driver
    # Sheet renamed "Open Accounts" → "Account Coverage" earlier in
    # the v11.22.x cycle; test was missed.
    driver.open(dashboard_arg, sheet="Account Coverage")
    driver.wait_loaded("Total Open Accounts")

    all_sql, all_params = _sql_for(build_account_summary_dataset, cfg)
    active_sql, active_params = _sql_for(
        build_account_summary_active_dataset, cfg,
    )
    all_rows = driver.query_db(all_sql, dataset_parameters=all_params)
    active_rows = driver.query_db(active_sql, dataset_parameters=active_params)

    # Window-narrow Active accounts per the dashboard's 30-day scope
    # (cfg.test_generator.as_of_frame(window_days=30); see exec/app.py:869). Total
    # Open uses null_option=ALL_VALUES (FilterGroup keeps every
    # account regardless of last_activity_date), so its KPI count
    # matches the full dataset's row count without window narrowing.
    frame = cfg.test_generator.as_of_frame(window_days=30)
    active_in_window = [
        r for r in active_rows
        if r.get("last_activity_date") is not None
        and frame.window.contains(_as_date(r["last_activity_date"]))
    ]

    rendered_open = parse_int_kpi(driver.kpi_value("Total Open Accounts"))
    assert rendered_open == len(all_rows), (
        f"Total Open Accounts: rendered {rendered_open} ≠ "
        f"len(query_db(account_summary)) = {len(all_rows)}."
    )
    rendered_active = parse_int_kpi(
        driver.kpi_value("Active Accounts (this window)"),
    )
    assert rendered_active == len(active_in_window), (
        f"Active Accounts (this window): rendered {rendered_active} ≠ "
        f"len(window-filtered account_summary_active) = "
        f"{len(active_in_window)} (of {len(active_rows)} total)."
    )
    # Sanity gate: Active ≤ Total Open. A regression that
    # accidentally bound Active to the wider dataset would flip this.
    assert rendered_active <= rendered_open, (
        f"Active Accounts ({rendered_active}) > Total Open Accounts "
        f"({rendered_open}) — Active is a subset by definition."
    )
    driver.screenshot()
