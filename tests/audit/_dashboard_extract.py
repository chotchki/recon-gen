"""Extract per-invariant table row counts from the deployed L1 dashboard (U.8.b.2).

Counterpart to ``_pdf_extract.count_invariant_table_rows``. U.8.b's
three-way agreement assert needs the dashboard-side row count to compare
against the PDF count + the scenario-derived expected count:
``expected == PDF == dashboard``.

Speaks the X.2.q ``DashboardDriver`` protocol — driver verbs handle QS's
quirks (vertical virtualization via the page-size-bump path that's
sealed inside ``QsEmbedDriver.table_row_count``; param-write settle
behind ``set_date_range``). This module owns the per-invariant
sheet/visual mapping + the date-filter application.

For time-series invariants (drift / overdraft / limit_breach) the period
is applied via the universal date filter (``set_date_range``); matches
the M.2b.1 universal date-range shape. Current-state invariants
(stuck_pending / stuck_unbundled / supersession) ignore the period —
their matview has no date filter, the sheet has no date pickers, and
the row count is whatever the matview currently holds.

Caller is responsible for ``driver.open(dashboard_id)`` before calling.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from tests.e2e._drivers import DashboardDriver


L1Invariant = Literal[
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
]


# Maps invariant key → (sheet_name, table_visual_title, has_date_filter).
#
# Visual titles match what ``apps/l1_dashboard/app.py`` declares (the
# customer-facing strings; never the auto-derived internal IDs).
# ``has_date_filter`` mirrors the audit's _EXCEPTION_INVARIANTS shape:
# time-series matviews (drift / overdraft / limit_breach) get the
# period applied; current-state matviews (stuck_* / supersession) don't.
#
# For drift, "Leaf Account Drift" is the customer-DDA per-row table —
# the apples-to-apples shape vs the audit PDF's drift section, which
# also surfaces every drift matview row in one combined table. The
# Parent Account Drift table on the same sheet stays out of the count;
# adding it would mix layers that the audit collapses into one.
_DASHBOARD_LAYOUT: dict[L1Invariant, tuple[str, str, bool]] = {
    "drift": ("Drift", "Leaf Account Drift", True),
    "overdraft": ("Overdraft", "Overdraft Violations", True),
    "limit_breach": ("Limit Breach", "Limit Breach Detail", True),
    "stuck_pending": ("Pending Aging", "Stuck Pending Detail", False),
    "stuck_unbundled": (
        "Unbundled Aging", "Stuck Unbundled Detail", False,
    ),
    # Supersession's "Transactions Audit" table is the per-row detail
    # surface, matching the audit PDF's transaction-detail sub-table
    # row-for-row. The sheet's "Logical Keys with Supersession" KPI is
    # a count, not a table — pointing the row-counter at it returns 0.
    "supersession": (
        "Supersession Audit", "Transactions Audit", False,
    ),
}


def count_l1_invariant_rows(
    driver: DashboardDriver,
    invariant: L1Invariant,
    period: tuple[date, date] | None,
) -> int:
    """Count rows in the named invariant's L1 dashboard table.

    Switches to the matching sheet, applies the period filter when the
    invariant is time-series, then returns the post-filter total via
    ``driver.table_row_count``.

    ``period=None`` skips the date-filter step regardless of whether the
    invariant supports one — useful when the caller wants to leave
    whatever filter state is already on the sheet (e.g., a test that's
    exercising default-period behavior).
    """
    sheet_name, table_title, has_date_filter = _DASHBOARD_LAYOUT[invariant]

    driver.goto_sheet(sheet_name)
    if has_date_filter and period is not None:
        # ``set_date_range`` blocks on the QS settle (per X.2.q's
        # ``_settle_after_param_change``) so the row count below sees
        # the post-filter state, not the spinner gap.
        driver.set_date_range(period[0].isoformat(), period[1].isoformat())
    return driver.table_row_count(table_title)
