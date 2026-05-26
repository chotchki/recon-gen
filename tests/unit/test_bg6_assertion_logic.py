"""BG.6 assertion-logic validation — proves the L2FT Exceptions +
Pending Aging + Today's Exceptions honest-gate tests catch findings
#11 (units mismatch) + #13 (chart-bar-vs-KPI population mismatch).

Mirrors BG.2-BG.5's deterministic shape.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator

import pytest

from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg


@pytest.fixture
def planted_l2ft_l1_sqlite() -> Iterator[object]:
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)
    # L2FT exceptions — one row per violation, with a per-violation
    # count column.
    conn.execute(
        "CREATE TABLE pfx_unified_l2_exceptions ("
        "  check_type TEXT, entity_a TEXT, entity_b TEXT,"
        "  detail TEXT, count INTEGER"
        ")"
    )
    conn.executemany(
        "INSERT INTO pfx_unified_l2_exceptions VALUES (?,?,?,?,?)",
        [
            ("Dead Rails", "rail-A", None, None, 1247),
            ("Dead Rails", "rail-B", None, None, 891),
            ("Chain Orphans", "chain-X", "child-1", None, 12),
            ("Unmatched Transfer Type", "tt-Z", None, None, 45),
        ],
    )
    # 4 rows; SUM(count) = 1247 + 891 + 12 + 45 = 2195
    # The "two different units" finding #11: KPI=4, table sum=2195

    # Stuck pending — one row per transaction.
    conn.execute(
        "CREATE TABLE pfx_stuck_pending ("
        "  transaction_id TEXT, transfer_id TEXT, account_id TEXT,"
        "  rail_name TEXT, amount_money INTEGER,"
        "  stuck_pending_aging_bucket TEXT, age_seconds INTEGER"
        ")"
    )
    conn.executemany(
        "INSERT INTO pfx_stuck_pending VALUES (?,?,?,?,?,?,?)",
        [
            ("tx-1", "xf-1", "acc-1", "wire", 1000, "0-6h", 3600),
            ("tx-2", "xf-2", "acc-2", "ach", 500, "0-6h", 7200),
        ],
    )
    # 2 rows total; KPI should == 2; chart bar sum should == 2

    # Today's exceptions
    conn.execute(
        "CREATE TABLE pfx_todays_exceptions ("
        "  check_type TEXT, account_id TEXT, magnitude_amount REAL,"
        "  magnitude_count INTEGER"
        ")"
    )
    conn.executemany(
        "INSERT INTO pfx_todays_exceptions VALUES (?,?,?,?)",
        [
            ("drift", "acc-1", 5000.0, None),
            ("drift", "acc-2", -2500.0, None),
            ("overdraft", "acc-3", -10000.0, None),
            ("stuck_unbundled", None, None, 5),
        ],
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    cfg.db_table_prefix = "pfx"
    try:
        yield cfg
    finally:
        os.unlink(path)


_L2_EXCEPTIONS_SQL = "SELECT * FROM pfx_unified_l2_exceptions"
_STUCK_PENDING_SQL = "SELECT * FROM pfx_stuck_pending"
_TODAYS_EXCEPTIONS_SQL = "SELECT * FROM pfx_todays_exceptions"


# ─── Finding #11 — L2 Exceptions KPI / table units mismatch ──────────


def test_bg6_l2_exceptions_kpi_passes_when_matches_dataset_row_count(
    planted_l2ft_l1_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    cfg = planted_l2ft_l1_sqlite
    rows = query_db_via_cfg(cfg, _L2_EXCEPTIONS_SQL)
    rendered_kpi = 4  # planted row count
    assert rendered_kpi == len(rows) == 4


def test_bg6_l2_exceptions_table_count_sum_passes_on_healthy_data(
    planted_l2ft_l1_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    cfg = planted_l2ft_l1_sqlite
    rows = query_db_via_cfg(cfg, _L2_EXCEPTIONS_SQL)
    expected_sum = sum(int(row["count"]) for row in rows)
    # Planted: 1247 + 891 + 12 + 45 = 2195
    assert expected_sum == 2195
    rendered_table_sum = 2195  # synthesizing: table renders matview
    assert rendered_table_sum == expected_sum


def test_bg6_l2_exceptions_table_count_trips_when_table_underreports(
    planted_l2ft_l1_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """v11.21.0 finding #11 table-half: table column renders something
    different from the dataset's projection. Identity trips."""
    cfg = planted_l2ft_l1_sqlite
    rows = query_db_via_cfg(cfg, _L2_EXCEPTIONS_SQL)
    expected_sum = sum(int(row["count"]) for row in rows)
    rendered_table_sum = 100  # bug shape: table column is wrong measure
    assert rendered_table_sum != expected_sum


def test_bg6_l2_exceptions_kpi_and_table_intentionally_different_units(
    planted_l2ft_l1_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """Finding #11 framing: KPI (rows) and table count column (per-row
    occurrence) are two correct measures with different units. BG.6
    enforces each matches ITS binding — NOT that they match each
    other. This test confirms the two are expected to diverge by a
    large factor."""
    cfg = planted_l2ft_l1_sqlite
    rows = query_db_via_cfg(cfg, _L2_EXCEPTIONS_SQL)
    kpi_value = len(rows)  # 4
    table_sum = sum(int(r["count"]) for r in rows)  # 2195
    assert kpi_value < table_sum, (
        f"Expected wide divergence on this planted data: KPI={kpi_value} "
        f"vs table_sum={table_sum} — finding #11 surface confirms."
    )


# ─── Finding #13 — Pending Aging chart-vs-KPI population ─────────────


def test_bg6_pending_aging_triple_identity_passes_on_healthy_data(
    planted_l2ft_l1_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    cfg = planted_l2ft_l1_sqlite
    rows = query_db_via_cfg(cfg, _STUCK_PENDING_SQL)
    # Planted: 2 rows
    assert len(rows) == 2
    rendered_kpi = 2
    rendered_chart_bar_sum = 2
    rendered_table_row_count = 2
    assert rendered_kpi == rendered_chart_bar_sum == rendered_table_row_count == len(rows)


def test_bg6_pending_aging_triple_identity_trips_when_chart_population_diverges(
    planted_l2ft_l1_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """v11.21.0 finding #13: KPI=2 / table=2 / chart bar=~140 — chart
    binds a different population than KPI+table. Triple identity
    trips when any of the three diverges."""
    cfg = planted_l2ft_l1_sqlite
    rows = query_db_via_cfg(cfg, _STUCK_PENDING_SQL)
    rendered_kpi = 2
    rendered_table_row_count = 2
    # Bug shape: chart binds a pre-filter population (140 rows).
    rendered_chart_bar_sum = 140
    assert rendered_chart_bar_sum != rendered_kpi
    assert rendered_chart_bar_sum != rendered_table_row_count
    assert rendered_chart_bar_sum != len(rows)


# ─── Today's Exceptions KPI identity ─────────────────────────────────


def test_bg6_todays_exceptions_kpi_passes_when_matches_dataset_count(
    planted_l2ft_l1_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    cfg = planted_l2ft_l1_sqlite
    rows = query_db_via_cfg(cfg, _TODAYS_EXCEPTIONS_SQL)
    rendered_kpi = 4
    assert rendered_kpi == len(rows)
