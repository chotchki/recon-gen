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
    _L2_EXCEPTIONS_NAME,
    _TRANSFER_TEMPLATES_NAME,
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
    Post-DW.6 App2 is the sole renderer, so the capture lands under
    ``app2.png``."""
    del short  # dashboard-id stub; renderer slug is fixed post-DW.6
    renderer = "app2"
    out_dir = _SNAP_ROOT / sheet_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{renderer}.png"
    driver.screenshot(path=out)


def _wait_or_snap(
    driver: "DashboardDriver", sheet_slug: str, short: str, *titles: str,
) -> None:
    """``wait_loaded(title)`` then snap. Falls back through ``titles``
    in order until one matches, then captures.

    QS embed first-paint on a cold cell can run 20-30s; pass 45s
    per-title timeout so the cold-read snap doesn't fire mid-load
    (operator saw spinner-state captures for Pending Aging + L2
    Exceptions when the helper defaulted to 10s).

    After a title matches, sleep 2s to let sibling visuals on the
    same sheet finish painting too — ``wait_loaded`` is per-visual,
    but the snap captures the whole page."""
    # typing-smell: ignore[no-playwright-leak]: the helper has to catch
    # Playwright's TimeoutError to keep capture flowing on stale titles —
    # the rest of the test still talks DashboardDriver verbs only.
    import playwright.sync_api as _pw  # typing-smell: ignore[no-playwright-leak]: see helper docstring
    matched = False
    for title in titles:
        try:
            driver.wait_loaded(title, timeout_ms=45_000)
            matched = True
            break
        except _pw.TimeoutError:
            continue
        except RuntimeError:
            # AA.A.8 — visual rendered with an error overlay. We still
            # want to capture that state for operator review (the snap
            # IS the diagnostic). Don't let it abort the capture pass.
            matched = True
            break
    if matched:
        # Let sibling visuals on the page finish painting before snap.
        # ``wait_loaded`` is per-visual but the snap captures the whole
        # page — without a settle wait, QS sheets snap with the matched
        # visual painted + neighbors mid-spin (operator caught this on
        # Pending Aging + L2 Exceptions cold reads). 2s is empirical:
        # QS embed sheets settle ~1.5s after the last STOP_VIS frame.
        import time as _t
        _t.sleep(2.0)
    _snap(driver, sheet_slug, short)


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
    _wait_or_snap(driver, "l1-exceptions-horizontal-bar", dashboard_id, "Open Exceptions", "Exception Detail")


def test_db3_snap_l1_pending_aging_stacked_horizontal(
    l1_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """L1 Pending Aging — stacked horizontal bars with color_label='Rail'.
    Exercises orientation=HORIZONTAL + bars_arrangement=STACKED +
    color_label in one capture."""
    driver, dashboard_id = l1_dashboard_driver
    driver.open(dashboard_id, sheet=_PENDING_AGING_NAME)
    _wait_or_snap(driver, "l1-pending-aging-stacked-horizontal", dashboard_id, "Stuck Pending by Age Bucket", "Stuck Pending")


def test_db3_snap_l1_unbundled_aging_stacked_horizontal(
    l1_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Same shape as Pending Aging — sibling sheet, same color_label
    pattern. Both renderers should show the 'Rail' header above the
    legend."""
    driver, dashboard_id = l1_dashboard_driver
    driver.open(dashboard_id, sheet=_UNBUNDLED_AGING_NAME)
    _wait_or_snap(driver, "l1-unbundled-aging-stacked-horizontal", dashboard_id, "Stuck Unbundled by Age Bucket", "Stuck Unbundled")


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
    _wait_or_snap(driver, "exec-program-health", dashboard_id, "Open L1 Invariant Violations", "Total Open Accounts")


def test_db3_snap_exec_money_moved(
    exec_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Exec Money Moved — stacked horizontal w/ color_label='Transfer
    Type'. Triple-cover: orientation, bars_arrangement, color_label."""
    driver, dashboard_id = exec_dashboard_driver
    driver.open(dashboard_id, sheet="Money Moved")
    _wait_or_snap(driver, "exec-money-moved", dashboard_id, "Net Money Moved", "Gross Money Moved", "Total Money Moved")


# ---------------------------------------------------------------------------
# Investigation — Sankey items_limit + (others) rollup (DB.1.2)
# ---------------------------------------------------------------------------


def _pick_first_non_all(driver: "DashboardDriver", label: str) -> bool:
    """Pick the first non-"All" option from the named dropdown. Both
    Investigation Sankey sheets default to "All" which renders empty
    — for parity verification we need an actual anchor selected."""
    try:
        options = driver.filter_options(label)
    except Exception:  # noqa: BLE001 — picker discovery failure shouldn't abort capture
        return False
    candidates = [o for o in options if o and o.strip().lower() not in {"all", "__all__"}]
    if not candidates:
        return False
    try:
        driver.pick_filter(label, [candidates[0]])
    except Exception:  # noqa: BLE001 — same rationale as above
        return False
    return True


def test_db3_snap_inv_money_trail_sankey(
    inv_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Investigation Money Trail — Sankey with items_limit cap. QS
    caps + rolls into (others); pre-DB.1.2 App2 showed the full
    universe, which made dense-instance dashboards unreadable.

    The Money Trail Sankey only renders when a chain root transfer is
    selected — defaults to "All" → empty Sankey. Pick the first
    available chain root before snapping."""
    driver, dashboard_id = inv_dashboard_driver
    driver.open(dashboard_id, sheet="Money Trail")
    _pick_first_non_all(driver, "Chain root transfer")
    _wait_or_snap(
        driver, "inv-money-trail-sankey", dashboard_id,
        "Money Trail — Chain Sankey", "Money Trail",
    )


def test_db3_snap_inv_account_network_sankey(
    inv_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """Investigation Account Network — touching-edges table + Sankeys
    underneath. Both sankeys carry items_limit but only render when
    an anchor account is picked (defaults to "All" → empty)."""
    driver, dashboard_id = inv_dashboard_driver
    driver.open(dashboard_id, sheet="Account Network")
    _pick_first_non_all(driver, "Anchor account")
    _wait_or_snap(
        driver, "inv-account-network-sankey", dashboard_id,
        "Account Network — Touching Edges", "Account Network",
    )


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
    _wait_or_snap(driver, "l2ft-l2-exceptions", dashboard_id, "L2 Violation Detail")


def test_db3_snap_l2ft_transfer_templates_sankey(
    l2ft_dashboard_driver: "tuple[DashboardDriver, str]",
) -> None:
    """L2FT Transfer Templates — multi-leg flow Sankey with
    items_limit=50. Larger cap than Investigation; the (others)
    bucket should still appear when the L2 instance has >50 transfer
    templates. (The Sankey lives on Transfer Templates, NOT Chains —
    Chains is a per-instance Table-only explorer; runtime causality
    is the wrong shape for Sankey.)"""
    driver, dashboard_id = l2ft_dashboard_driver
    driver.open(dashboard_id, sheet=_TRANSFER_TEMPLATES_NAME)
    _wait_or_snap(
        driver, "l2ft-transfer-templates-sankey", dashboard_id,
        "Multi-Leg Flow — Account → Template → Account",
        "Template Instances",
    )


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
    _wait_or_snap(driver, "l1-drift-kpis", dashboard_id, "Leaf Account-Days in Drift", "Leaf Account Drift")
