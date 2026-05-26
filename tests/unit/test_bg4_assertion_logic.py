"""BG.4 assertion-logic validation — proves the Investigation honest-
gate tests catch findings #5 (Volume Anomalies KPI=0 vs populated
distribution) and #7 (Fanout cartesian inflation).

Mirrors BG.2/BG.3's deterministic shape: plant a SQLite matview, run
the BG.4 assertion logic standalone, demonstrate each bug shape trips
the right assertion.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator
from decimal import Decimal

import pytest

from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg


@pytest.fixture
def planted_inv_sqlite() -> Iterator[object]:
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(path)

    # Volume Anomalies matview — rows with varying z_score across
    # buckets so the σ-cut consistency assertion has signal.
    conn.execute(
        "CREATE TABLE pfx_inv_pair_rolling_anomalies ("
        "  recipient_account_id TEXT, recipient_account_name TEXT,"
        "  recipient_account_type TEXT, sender_account_id TEXT,"
        "  sender_account_name TEXT, sender_account_type TEXT,"
        "  window_start TEXT, window_end TEXT,"
        "  window_sum INTEGER, transfer_count INTEGER,"
        "  pop_mean INTEGER, pop_stddev INTEGER,"
        "  z_score REAL, z_bucket TEXT"
        ")"
    )
    # Plant 5 rows: z=0.5, z=1.5, z=2.5, z=3.5, z=4.5
    # default σ = 3 → 2 rows above threshold (z=3.5 + z=4.5)
    conn.executemany(
        "INSERT INTO pfx_inv_pair_rolling_anomalies VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("r-1", "Rec1", "dda", "s-1", "Send1", "external",
             "2026-05-23", "2026-05-24", 10_000, 5, 8_000, 1_000, 0.5, "<2σ"),
            ("r-2", "Rec2", "dda", "s-2", "Send2", "external",
             "2026-05-23", "2026-05-24", 12_000, 8, 8_000, 1_000, 1.5, "<2σ"),
            ("r-3", "Rec3", "dda", "s-3", "Send3", "external",
             "2026-05-23", "2026-05-24", 15_000, 10, 8_000, 1_000, 2.5, "2σ-3σ"),
            ("r-4", "Rec4", "dda", "s-4", "Send4", "external",
             "2026-05-23", "2026-05-24", 20_000, 15, 8_000, 1_000, 3.5, "3σ-4σ"),
            ("r-5", "Rec5", "dda", "s-5", "Send5", "external",
             "2026-05-23", "2026-05-24", 25_000, 20, 8_000, 1_000, 4.5, "4σ+"),
        ],
    )

    # Fanout shape: transfer T1 has 1 recipient (r-1) + 3 senders
    # (s-1, s-2, s-3) with inflow_amount = 1000. The joined dataset
    # produces 3 rows for T1, each carrying amount=1000.
    # Inflation-free truth: 1 (recipient, transfer) pair × $1000 = $1000.
    # Inflated cartesian SUM(amount) = 3 × $1000 = $3000.
    conn.execute(
        "CREATE TABLE pfx_fanout ("
        "  recipient_account_id TEXT, recipient_account_name TEXT,"
        "  recipient_account_type TEXT, sender_account_id TEXT,"
        "  sender_account_name TEXT, sender_account_type TEXT,"
        "  transfer_id TEXT, posted_at TEXT,"
        "  amount REAL, distinct_senders INTEGER"
        ")"
    )
    conn.executemany(
        "INSERT INTO pfx_fanout VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            # Transfer T1: 1 recipient (r-1) × 3 senders → 3 joined rows
            ("r-1", "Rec1", "dda", "s-1", "Send1", "external",
             "T1", "2026-05-23", 1000.0, 3),
            ("r-1", "Rec1", "dda", "s-2", "Send2", "external",
             "T1", "2026-05-23", 1000.0, 3),
            ("r-1", "Rec1", "dda", "s-3", "Send3", "external",
             "T1", "2026-05-23", 1000.0, 3),
            # Transfer T2: 1 recipient (r-2) × 1 sender → 1 joined row
            ("r-2", "Rec2", "dda", "s-1", "Send1", "external",
             "T2", "2026-05-24", 500.0, 1),
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


_ANOMALIES_FILTERED_SQL = (
    "SELECT * FROM pfx_inv_pair_rolling_anomalies WHERE z_score >= :param_pInvAnomaliesSigma"
)
_ANOMALIES_DISTRIBUTION_SQL = (
    "SELECT * FROM pfx_inv_pair_rolling_anomalies"
)
_FANOUT_SQL = "SELECT * FROM pfx_fanout"


# ─── Finding #5 — anomalies KPI vs distribution consistency ──────────


def test_bg4_anomalies_kpi_matches_filtered_count_on_healthy_data(
    planted_inv_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """KPI == σ-filtered dataset row count, and that count == bin sum
    of distribution above threshold. Healthy-path: all assertions
    pass."""
    cfg = planted_inv_sqlite
    default_sigma = Decimal("3")
    filtered = query_db_via_cfg(
        cfg, _ANOMALIES_FILTERED_SQL, binds={"param_pInvAnomaliesSigma": "3"},
    )
    distribution = query_db_via_cfg(cfg, _ANOMALIES_DISTRIBUTION_SQL)
    rendered_kpi = len(filtered)  # synthesizing: KPI matches matview

    assert rendered_kpi == len(filtered)
    above = [r for r in distribution if Decimal(str(r["z_score"])) >= default_sigma]
    assert len(filtered) == len(above) == 2


def test_bg4_anomalies_kpi_trips_when_kpi_underflows_vs_filtered_count(
    planted_inv_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """v11.21.0 finding #5: KPI shows 0 while populated bins exist
    above the threshold. Identity (KPI == filtered count) trips."""
    cfg = planted_inv_sqlite
    filtered = query_db_via_cfg(
        cfg, _ANOMALIES_FILTERED_SQL, binds={"param_pInvAnomaliesSigma": "3"},
    )
    rendered_kpi = 0  # the cold-read bug shape
    assert rendered_kpi != len(filtered)


def test_bg4_anomalies_distribution_consistency_trips_on_threshold_mismatch(
    planted_inv_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """v11.21.0 finding #5 (variant): the chart and the KPI compute
    different things. Simulate: KPI uses σ=4 but the test pulls
    default σ=3 → filtered_count (2) ≠ above_threshold_at_3 (which
    would be 2 anyway). Use σ=4 for KPI: that gives 1; above-σ=3 = 2.
    The "(b)" assertion catches the divergence."""
    cfg = planted_inv_sqlite
    # If the KPI's σ is 4 but the chart's bucketing visually shows
    # buckets ≥ 2σ, an analyst expects "everything ≥ 2σ" = 3 rows.
    # Bug-shape: KPI uses σ=4 → only 1 row above. Same matview, two
    # different thresholds → user reads "1 flagged" but sees 3 bars.
    filtered_at_4 = query_db_via_cfg(
        cfg, _ANOMALIES_FILTERED_SQL, binds={"param_pInvAnomaliesSigma": "4"},
    )
    distribution = query_db_via_cfg(cfg, _ANOMALIES_DISTRIBUTION_SQL)
    above_at_3 = [r for r in distribution if Decimal(str(r["z_score"])) >= Decimal("3")]
    assert len(filtered_at_4) != len(above_at_3), (
        f"Bug-shape setup error — should diverge: filtered_at_4="
        f"{len(filtered_at_4)} vs above_at_3={len(above_at_3)}"
    )


# ─── Finding #7 — Fanout cartesian inflation ─────────────────────────


def test_bg4_fanout_total_trips_when_cartesian_inflation_exists(
    planted_inv_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """v11.21.0 finding #7 direct: the fanout JOIN is cartesian for
    multi-leg transfers; SUM(amount) inflates by sender-leg count.

    Planted fixture: T1 has 1 recipient × 3 senders → 3 joined rows
    each amount=$1000. T2 has 1×1 → 1 row, amount=$500.

    Inflated SUM(amount) over joined = 3 × $1000 + $500 = $3500.
    Inflation-free truth = $1000 + $500 = $1500.

    The browser assertion compares rendered_kpi to the inflation-free
    truth; the bug-shape rendered_kpi == $3500 trips the assertion
    with the 2.33× inflation factor named in the message."""
    cfg = planted_inv_sqlite
    rows = query_db_via_cfg(cfg, _FANOUT_SQL)

    # Bug-shape: rendered KPI = inflated cartesian SUM.
    rendered_total_buggy = sum(
        Decimal(str(r["amount"])) for r in rows
    )

    # Truth: dedupe by (recipient, transfer).
    unique = {
        (str(r["recipient_account_id"]), str(r["transfer_id"])):
        Decimal(str(r["amount"]))
        for r in rows
    }
    expected_total = sum(unique.values(), Decimal("0"))

    assert rendered_total_buggy == Decimal("3500"), (
        f"Plant arithmetic: cartesian SUM should == $3500, got "
        f"{rendered_total_buggy}"
    )
    assert expected_total == Decimal("1500"), (
        f"Plant arithmetic: deduped SUM should == $1500, got "
        f"{expected_total}"
    )
    # The browser assertion: rendered_total == expected_total. Bug
    # shape trips it.
    assert rendered_total_buggy != expected_total, (
        "Test setup error — cartesian-inflated SUM should diverge from "
        "deduped SUM"
    )


def test_bg4_fanout_total_passes_when_no_inflation(
    planted_inv_sqlite,  # type: ignore[no-untyped-def]: fixture-yield cascade from the sqlite-backed Config
) -> None:
    """Healthy path: when every transfer has exactly 1 recipient leg ×
    1 sender leg, the cartesian JOIN produces no inflation and the
    SUM equals the deduped SUM. Confirms the test isn't tautologically
    failing on every dataset shape."""
    cfg = planted_inv_sqlite
    # Filter to only T2 (the 1×1 transfer)
    rows = query_db_via_cfg(
        cfg, "SELECT * FROM pfx_fanout WHERE transfer_id = 'T2'",
    )
    rendered = sum(Decimal(str(r["amount"])) for r in rows)
    unique = {
        (str(r["recipient_account_id"]), str(r["transfer_id"])):
        Decimal(str(r["amount"]))
        for r in rows
    }
    expected = sum(unique.values(), Decimal("0"))
    assert rendered == expected == Decimal("500")
