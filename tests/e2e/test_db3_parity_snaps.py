"""Phase DB.3 — cold-read parity verify via the e2e browser harness.

For each Visual touched by DB.1 (BarChart orientation/color_label,
Sankey items_limit, LineChart Type, KPI Sparkline hidden), drive the
parametrized ``[qs, app2]`` dashboard driver to the host sheet,
capture a full-page screenshot, and write it to
``docs/audits/db_3_parity_verify/<sheet>/<renderer>.png``.

Operator's verification flow:

  ./run_tests.sh up_to=qs_browser  --variants=sp_pg_aw  -k db3_parity

…produces a side-by-side grid the operator can flip through visually.
The ``qs`` leg auto-skips when the dashboard isn't deployed (no live
analysis to embed); the ``app2`` leg always runs against the live DB
backed server.

These tests don't assert pixel parity — they're a curated CAPTURE
pass, not a regression gate. The DA-shape parity gate at DB.2 catches
structural drift; DB.3 catches the visual taste / pixel-painting drift
that no type-system check can encode.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from recon_gen.apps.l1_dashboard.app import (
    _DRIFT_NAME,
    _L1_EXCEPTIONS_NAME,
    _PENDING_AGING_NAME,
    _UNBUNDLED_AGING_NAME,
)
from recon_gen.apps.l2_flow_tracing.app import (
    _CHAINS_NAME,
    _L2_EXCEPTIONS_NAME,
)
from tests._marks import Need, Tier, needs, tier

if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    tier(Tier.QS_BROWSER),
    needs(Need.AWS_QS, Need.PLAYWRIGHT),
]


# docs/audits/db_3_parity_verify/<sheet_slug>/<renderer>.png — checked
# in alongside the doc so reviewers see the captures without rerunning.
_SNAP_ROOT = (
    Path(__file__).resolve().parents[2]
    / "docs" / "audits" / "db_3_parity_verify"
)


def _snap(driver: "DashboardDriver", sheet_slug: str, short: str) -> None:
    """Write the driver's current page to
    ``<repo>/docs/audits/db_3_parity_verify/<sheet>/<renderer>.png``.
    ``short`` is the dashboard-id stub from the parametrized fixture
    (``"qs"`` / ``"app2"`` after the driver class name resolves)."""
    renderer = (
        "qs" if driver.__class__.__name__ == "QsEmbedDriver" else "app2"
    )
    out_dir = _SNAP_ROOT / sheet_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{renderer}.png"
    driver.screenshot(path=out)


# ---------------------------------------------------------------------------
# L1 Dashboard — BarChart orientation + color_label parity (DB.1.1)
# ---------------------------------------------------------------------------


def test_db3_snap_l1_exceptions_horizontal_bar(
    l1_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """L1 Exceptions — 'Exceptions by Check Type' is a horizontal bar
    chart (orientation=HORIZONTAL). Pre-DB.1.1 App2 rendered vertical
    despite the QS-side orientation. Capture both."""
    driver, dashboard_id = l1_dashboard_driver
    driver.open(dashboard_id, sheet=_L1_EXCEPTIONS_NAME)
    driver.wait_loaded("Open Exceptions")
    _snap(driver, "l1-exceptions-horizontal-bar", dashboard_id)


def test_db3_snap_l1_pending_aging_stacked_horizontal(
    l1_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """L1 Pending Aging — stacked horizontal bars with color_label='Rail'.
    Exercises orientation=HORIZONTAL + bars_arrangement=STACKED +
    color_label in one capture."""
    driver, dashboard_id = l1_dashboard_driver
    driver.open(dashboard_id, sheet=_PENDING_AGING_NAME)
    driver.wait_loaded("Stuck Pending by Age Bucket")
    _snap(driver, "l1-pending-aging-stacked-horizontal", dashboard_id)


def test_db3_snap_l1_unbundled_aging_stacked_horizontal(
    l1_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Same shape as Pending Aging — sibling sheet, same color_label
    pattern. Both renderers should show the 'Rail' header above the
    legend."""
    driver, dashboard_id = l1_dashboard_driver
    driver.open(dashboard_id, sheet=_UNBUNDLED_AGING_NAME)
    driver.wait_loaded("Stuck Unbundled by Age Bucket")
    _snap(driver, "l1-unbundled-aging-stacked-horizontal", dashboard_id)


# ---------------------------------------------------------------------------
# Executives — BarChart parity sites (DB.1.1)
# ---------------------------------------------------------------------------


def test_db3_snap_exec_program_health(
    exec_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Exec Program Health — horizontal bars + color_label for the
    rail/transfer-type breakdown. Pre-DB.1.1 the App2 side mis-rendered
    on both fronts."""
    driver, dashboard_id = exec_dashboard_driver
    driver.open(dashboard_id, sheet="Program Health")
    driver.wait_loaded("Total Open Accounts")
    _snap(driver, "exec-program-health", dashboard_id)


def test_db3_snap_exec_money_moved(
    exec_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Exec Money Moved — stacked horizontal w/ color_label='Transfer
    Type'. Triple-cover: orientation, bars_arrangement, color_label."""
    driver, dashboard_id = exec_dashboard_driver
    driver.open(dashboard_id, sheet="Money Moved")
    driver.wait_loaded("Total Money Moved")
    _snap(driver, "exec-money-moved", dashboard_id)


# ---------------------------------------------------------------------------
# Investigation — Sankey items_limit + (others) rollup (DB.1.2)
# ---------------------------------------------------------------------------


def test_db3_snap_inv_money_trail_sankey(
    inv_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Investigation Money Trail — Sankey with items_limit cap. QS
    caps + rolls into (others); pre-DB.1.2 App2 showed the full
    universe, which made dense-instance dashboards unreadable."""
    driver, dashboard_id = inv_dashboard_driver
    driver.open(dashboard_id, sheet="Money Trail")
    driver.wait_loaded("Money Trail")
    _snap(driver, "inv-money-trail-sankey", dashboard_id)


def test_db3_snap_inv_account_network_sankey(
    inv_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Investigation Account Network — two directional Sankeys + the
    touching-edges table. Both sankeys carry items_limit."""
    driver, dashboard_id = inv_dashboard_driver
    driver.open(dashboard_id, sheet="Account Network")
    driver.wait_loaded("Account Network")
    _snap(driver, "inv-account-network-sankey", dashboard_id)


# ---------------------------------------------------------------------------
# L2 Flow Tracing — Sankey items_limit + horizontal BarChart (DB.1.1+.2)
# ---------------------------------------------------------------------------


def test_db3_snap_l2ft_exceptions(
    l2ft_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """L2FT L2 Exceptions — horizontal bar chart by check_type.
    DB.1.1 site."""
    driver, dashboard_id = l2ft_dashboard_driver
    driver.open(dashboard_id, sheet=_L2_EXCEPTIONS_NAME)
    driver.wait_loaded("L2 Violation Detail")
    _snap(driver, "l2ft-l2-exceptions", dashboard_id)


def test_db3_snap_l2ft_chains_sankey(
    l2ft_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """L2FT Chains — Sankey with items_limit=50. Larger cap than
    Investigation; the (others) bucket should still appear when the
    L2 instance has >50 chain templates."""
    driver, dashboard_id = l2ft_dashboard_driver
    driver.open(dashboard_id, sheet=_CHAINS_NAME)
    driver.wait_loaded("Chain Templates")
    _snap(driver, "l2ft-chains-sankey", dashboard_id)


# ---------------------------------------------------------------------------
# KPI Sparkline HIDDEN (DB.1.3) — captures any KPI-heavy sheet to
# verify both renderers show just the big number (no empty sparkline
# placeholder reservation below).
# ---------------------------------------------------------------------------


def test_db3_snap_l1_drift_kpis_no_sparkline_placeholder(
    l1_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """L1 Drift — 4-up KPI row at the top. Operator-eye check: both
    renderers should show the value tightly; pre-DB.1.3 QS reserved
    UI space for a sparkline below each value that was always empty."""
    driver, dashboard_id = l1_dashboard_driver
    driver.open(dashboard_id, sheet=_DRIFT_NAME)
    driver.wait_loaded("Drifting Leaf Accounts")
    _snap(driver, "l1-drift-kpis", dashboard_id)
