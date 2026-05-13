"""Browser e2e: cross-sheet drill into Transactions widens the date
range so the target transfer's row is visible.

Parametrized over ``[qs, app2]`` (u.4.e.3) via ``l1_dashboard_driver``;
the drill fires through the renderer-agnostic
``drill_from_first_row_via_menu`` verb — QS right-clicks the row →
context-menu item; App 2 clicks the row's "⋯" button → ``ctxmenu`` item.

v8.5.7 — bug class regression (QS side). Pre-v8.5.7 a drill from a
current-state sheet (Pending Aging — not in the universal date filter
scope) into the Transactions sheet (which IS scoped to a default 7-day
window) lost the target transfer's legs whenever the source row's
posting was older than 7 days. The drill wrote ``pL1TxTransfer`` but did
NOT write the date range params, leaving the Transactions sheet's
universal filter narrow. Fix: the QS drill now also writes
``pL1DateStart=1990-01-01`` / ``pL1DateEnd=2099-12-31`` via
``DrillStaticDateTime``.

App 2 has no equivalent — its date filter defaults to "all rows" (the
``date_from``/``date_to`` sentinels match everything), so the
date-widening write is a no-op there; the App 2 row drill just navigates
to the Transactions sheet, which already shows everything in scope. So
the App 2 leg is the *positive* signal — the "⋯ → menu item" path
actually works — and it xpasses where the QS leg still xfails on the
deployed-dashboard context-menu DOM issue (see the ``xfail`` reason).

Data-agnostic: doesn't assert any specific transfer_id value, only that
≥1 row survives the drill. Skips cleanly if the seeded DB has no
stuck-pending rows to drill from.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.xfail(
    reason=(
        "QS leg: the deployed Stuck Pending Detail table's right-click "
        "context menu doesn't appear ([role=menu] never shows; the helper "
        "times out) — pre-existing, surfaced by the X.2.q.3 port, triage "
        "candidates: stale deployed drill wiring vs. current src / QS-side "
        "context-menu DOM change / right-click dispatch shape. The App 2 "
        "leg drives the same verb via the row's '⋯' button + ctxmenu and "
        "xpasses (strict=False tolerates it) — a clean positive signal "
        "that the row-drill path works. Re-light the QS leg once the "
        "context-menu issue is fixed."
    ),
    strict=False,
)
def test_pending_aging_drill_to_transactions_shows_target(l1_dashboard_driver):
    """Drill from a Pending Aging row → "View Transactions for this
    transfer" must land on a Transactions sheet that actually shows the
    target transfer (≥1 row in the Posting Ledger table).

    The pre-v8.5.7 QS failure mode rendered an empty Transactions table
    because the drill didn't widen the universal date range — any
    stuck-pending leg older than the default 7-day window dropped out of
    view at the destination.
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet="Pending Aging")
    driver.wait_loaded("Stuck Pending Detail")
    pre_drill_rows = len(driver.table_rows("Stuck Pending Detail"))
    if pre_drill_rows == 0:
        pytest.skip(
            "Pending Aging detail table has no stuck rows in the seeded "
            "DB — nothing to drill from. (CI's auto-scenario plants "
            "stuck-pending rows; a thin local seed may not.)"
        )

    driver.drill_from_first_row_via_menu(
        "Stuck Pending Detail", "View Transactions for this transfer",
    )
    driver.wait_loaded("Posting Ledger")
    post_drill_rows = len(driver.table_rows("Posting Ledger"))

    if post_drill_rows == 0:
        driver.screenshot()
    assert post_drill_rows > 0, (
        f"Drill from Pending Aging → Transactions landed on an empty "
        f"Posting Ledger. On QS this is the v8.5.7 bug class — the drill "
        f"must widen the universal date range so the target transfer's "
        f"legs survive the destination's filter (check "
        f"``_populate_pending_aging_sheet``'s drill includes "
        f"``*_wide_date_writes()``). On App 2 the Transactions sheet "
        f"should already show everything in scope on default load."
    )
