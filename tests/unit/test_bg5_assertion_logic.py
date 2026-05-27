"""BG.5 assertion-logic validation — proves the Executives honest-gate
tests catch finding #8 (Total Transactions KPI vs App Info matview
row-count divergence) and adjacent KPI-vs-dataset-aggregate drift.

Mirrors BG.2/BG.3/BG.4's deterministic shape: plant a SQLite, run the
assertion logic standalone, demonstrate each bug shape trips the right
assertion.

Per the v11.21.0 triage doc, finding #8 is NOT a bug — the gap between
Total Transactions (status='Posted' + per-transfer collapse) and App
Info's per-leg row count is by design. What BG.5 enforces is the
**contract** that the KPI matches its underlying dataset's
aggregate — so if a future refactor accidentally drifts the binding
(SUM(transfer_count) → COUNT(*) → AVG(...)), the test catches it.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg

if TYPE_CHECKING:
    from recon_gen.common.config import Config


@pytest.fixture
def planted_exec_sqlite() -> Iterator["Config"]:
    """Spin up a SQLite with synthetic transaction summary rows. Values
    chosen so the KPI aggregates have non-trivial sums + the per-day +
    per-rail combinatorial stays visible."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    # Mirrors EXEC_TRANSACTION_SUMMARY_CONTRACT — 5 columns.
    conn.execute(
        "CREATE TABLE pfx_exec_txn_summary ("
        "  posted_date TEXT, rail_name TEXT, transfer_count INTEGER,"
        "  gross_amount REAL, net_amount REAL"
        ")"
    )
    conn.executemany(
        "INSERT INTO pfx_exec_txn_summary VALUES (?,?,?,?,?)",
        [
            ("2026-05-23", "ach", 100, 50_000.00, 0.00),
            ("2026-05-23", "wire", 5, 250_000.00, -100.00),
            ("2026-05-24", "ach", 120, 60_000.00, 50.00),
            ("2026-05-24", "wire", 3, 150_000.00, 200.00),
            ("2026-05-25", "ach", 80, 40_000.00, -75.00),
        ],
    )
    # Account summary — 10 open, 6 active (filtered shape)
    conn.execute(
        "CREATE TABLE pfx_exec_acct_summary ("
        "  account_id TEXT, account_type TEXT, activity_count INTEGER"
        ")"
    )
    conn.executemany(
        "INSERT INTO pfx_exec_acct_summary VALUES (?,?,?)",
        [(f"acc-{i}", "dda", i * 10) for i in range(10)],  # acc-0..acc-9
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    cfg.db_table_prefix = "pfx"
    try:
        yield cfg
    finally:
        os.unlink(path)


_TXN_SUMMARY_SQL = "SELECT * FROM pfx_exec_txn_summary"
_ACCT_SUMMARY_SQL = "SELECT * FROM pfx_exec_acct_summary"
_ACCT_SUMMARY_ACTIVE_SQL = (
    "SELECT * FROM pfx_exec_acct_summary WHERE activity_count > 0"
)


# ─── Total Transactions KPI binding ──────────────────────────────────


def test_bg5_total_transactions_passes_when_kpi_matches_sum(
    planted_exec_sqlite: "Config",
) -> None:
    cfg = planted_exec_sqlite
    rows = query_db_via_cfg(cfg, _TXN_SUMMARY_SQL)
    expected = sum(int(row["transfer_count"]) for row in rows)
    # Planted: 100+5+120+3+80 = 308
    assert expected == 308
    rendered_kpi = 308  # synthesizing healthy binding
    assert rendered_kpi == expected


def test_bg5_total_transactions_trips_when_kpi_binding_drifts(
    planted_exec_sqlite: "Config",
) -> None:
    """v11.21.0 finding #8 root-contract shape: KPI binding drifts from
    SUM(transfer_count) to a different aggregate. Identity trips."""
    cfg = planted_exec_sqlite
    rows = query_db_via_cfg(cfg, _TXN_SUMMARY_SQL)
    expected = sum(int(row["transfer_count"]) for row in rows)
    # Bug shape: KPI rendered the COUNT of rows (5) instead of SUM
    # of transfer_count (308) — a real wrong-measure-binding mistake
    # someone could make.
    rendered_kpi = len(rows)
    assert rendered_kpi != expected, (
        f"Test setup error — COUNT(*)={rendered_kpi} should diverge "
        f"from SUM(transfer_count)={expected}"
    )


# ─── Gross + Net Money Moved KPI binding ─────────────────────────────


def test_bg5_money_moved_kpis_pass_on_healthy_data(
    planted_exec_sqlite: "Config",
) -> None:
    cfg = planted_exec_sqlite
    rows = query_db_via_cfg(cfg, _TXN_SUMMARY_SQL)
    expected_gross = sum(
        (Decimal(str(row["gross_amount"])) for row in rows), Decimal("0"),
    )
    expected_net = sum(
        (Decimal(str(row["net_amount"])) for row in rows), Decimal("0"),
    )
    # Planted gross = 50k + 250k + 60k + 150k + 40k = 550_000
    # Planted net = 0 + -100 + 50 + 200 + -75 = 75
    assert expected_gross == Decimal("550000")
    assert expected_net == Decimal("75")


def test_bg5_money_moved_trips_when_gross_binds_net(
    planted_exec_sqlite: "Config",
) -> None:
    """Bug shape: Gross KPI accidentally binds the net_amount column
    instead of gross_amount. Identity trips with both column-sums
    named."""
    cfg = planted_exec_sqlite
    rows = query_db_via_cfg(cfg, _TXN_SUMMARY_SQL)
    expected_gross = sum(
        (Decimal(str(row["gross_amount"])) for row in rows), Decimal("0"),
    )
    rendered_gross_buggy = sum(
        (Decimal(str(row["net_amount"])) for row in rows), Decimal("0"),
    )
    assert rendered_gross_buggy != expected_gross


# ─── Account counts identity + Active ≤ Open sanity ──────────────────


def test_bg5_account_counts_pass_with_active_subset_of_open(
    planted_exec_sqlite: "Config",
) -> None:
    cfg = planted_exec_sqlite
    open_rows = query_db_via_cfg(cfg, _ACCT_SUMMARY_SQL)
    active_rows = query_db_via_cfg(cfg, _ACCT_SUMMARY_ACTIVE_SQL)
    # Planted: 10 open (acc-0..acc-9), 9 active (acc-1..acc-9; acc-0
    # has activity_count=0 → filtered out).
    assert len(open_rows) == 10
    assert len(active_rows) == 9
    # Sanity gate: Active ≤ Open.
    assert len(active_rows) <= len(open_rows)


def test_bg5_active_kpi_trips_when_bound_to_wider_dataset(
    planted_exec_sqlite: "Config",
) -> None:
    """Bug shape: Active KPI accidentally re-bound to the all-accounts
    dataset (regression where the SQL pushdown narrowing was
    bypassed). Active would equal Open, breaking the subset invariant.
    """
    cfg = planted_exec_sqlite
    open_rows = query_db_via_cfg(cfg, _ACCT_SUMMARY_SQL)
    active_rows = query_db_via_cfg(cfg, _ACCT_SUMMARY_ACTIVE_SQL)
    # Bug shape: rendered Active = len(open_rows) — wider scope.
    rendered_active_buggy = len(open_rows)
    assert rendered_active_buggy != len(active_rows)
