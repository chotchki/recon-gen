"""Extract per-invariant table row counts from the deployed L1 dashboard (U.8.b.2).

Counterpart to ``_pdf_extract.count_invariant_table_rows``. U.8.b's
three-way agreement assert needs the dashboard-side row count to
compare against the PDF count + the scenario-derived expected
count: ``expected == PDF == dashboard``.

Wraps the existing browser helpers in ``common/browser/helpers.py``
(``count_table_total_rows`` already handles QuickSight's vertical
virtualization by bumping page size to 10000 + scroll-accumulating
the highest cell index seen). This module owns the per-invariant
sheet/visual mapping + the date-filter application — the bits that
are L1-dashboard-specific.

For time-series invariants (drift / overdraft / limit_breach) the
period is applied via the sheet's ``Date From`` / ``Date To``
ParameterDateTimePicker controls; matches the M.2b.1 universal
date-range filter shape. Current-state invariants (stuck_pending /
stuck_unbundled / supersession) ignore the period — their matview
has no date filter, the sheet has no date pickers, and the row
count is whatever the matview currently holds.

Caller is responsible for:
  - Opening the dashboard URL on the page (``page.goto(embed_url)``).
  - Waiting for the dashboard to finish loading
    (``wait_for_dashboard_loaded(page, timeout_ms)``).
"""

from __future__ import annotations

from datetime import date
from typing import Literal


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
# period applied; current-state matviews (stuck_* / supersession)
# don't.
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
    page,  # type: ignore[no-untyped-def]: playwright Page, untyped to keep pyright off the optional dep
    invariant: L1Invariant,
    period: tuple[date, date] | None,
    *,
    timeout_ms: int,
) -> int:
    """Count rows in the named invariant's L1 dashboard table.

    Switches to the matching sheet, applies the period filter when
    the invariant is time-series, then returns the post-filter total
    via ``count_table_total_rows``.

    ``period=None`` skips the date-filter step regardless of whether
    the invariant supports one — useful when the caller wants to
    leave whatever filter state is already on the sheet (e.g., a
    test that's exercising default-period behavior).
    """
    from quicksight_gen.common.browser.helpers import (
        click_sheet_tab,
        count_table_rows,
        count_table_total_rows,
        set_parameter_datetime_value,
        wait_for_visuals_present,
    )

    sheet_name, table_title, has_date_filter = _DASHBOARD_LAYOUT[invariant]

    click_sheet_tab(page, sheet_name, timeout_ms=timeout_ms)
    wait_for_visuals_present(page, min_count=1, timeout_ms=timeout_ms)
    # NOTE: deliberately NOT calling wait_for_table_cells_present here.
    # That helper waits for ANY ``[data-automation-id^="sn-table-cell-0-0"]``
    # globally — overly aggressive when the target table is below the
    # fold. ``count_table_total_rows`` below scrolls the named visual
    # into view, clicks its title to focus, and bumps page size, which
    # forces hydration on the table we actually care about. Tests
    # using this helper should pass ``viewport=(1600, 4000)`` to
    # ``webkit_page`` so stacked KPI + chart + table layouts (Pending
    # Aging / Unbundled Aging / Supersession Audit) keep the detail
    # table inside the initial render area.

    if has_date_filter and period is not None:
        start_str = period[0].strftime("%Y/%m/%d")
        end_str = period[1].strftime("%Y/%m/%d")
        set_parameter_datetime_value(
            page, "Date From", start_str, timeout_ms=timeout_ms,
        )
        set_parameter_datetime_value(
            page, "Date To", end_str, timeout_ms=timeout_ms,
        )
        # Brief settle so the table re-queries against the new
        # parameter values before we count rows.
        page.wait_for_timeout(1000)

    # Try the paginated counter first — handles large tables (>10
    # rows) by bumping page size and scroll-accumulating cell
    # indices. For small tables that QS renders without a paginated
    # ``.grid-container`` (Stuck Pending Detail / Stuck Unbundled
    # Detail / Logical Keys with Supersession render this way when
    # the underlying matview has only a handful of rows), the helper
    # returns -2; fall back to the simpler DOM-cell counter, which
    # works for any QS table but caps at the ~10 rows currently
    # mounted in the DOM. For our scenario expected counts (≤ 18
    # for overdraft, ≤ 5 elsewhere) the fallback is sufficient.
    total = count_table_total_rows(
        page, table_title, timeout_ms=timeout_ms,
    )
    if total == -2:
        return count_table_rows(page, table_title)
    return total
