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


# Natural-key columns per invariant — the same row-identity the scenario
# plants expose (``_scenario_expectations.ExpectedAuditCounts``): flat-shape
# invariants key on ``(account_id, day)`` (limit_breach adds ``rail_name``);
# the divergent-shape ones (stuck_* / supersession) key on ``transaction_id``.
# Day column matches the dashboard table's column name. Used by
# ``l1_invariant_row_keys`` for the X.2.j 4-way agreement test's
# row-identity asserts (flat-shape) — count-only suffices for the rest.
#
# Z.B (2026-05-15) subsumed ``transfer_type`` into the rail; the limit_breach
# matview projects ``rail_name`` from the Z.B rewrite of the cap view.
# The dashboard's Limit Breach Detail table reads
# ``ds_lb["rail_name"].dim()`` (apps/l1_dashboard/app.py:1043), so the
# row-identity key must match.
_KEY_COLS: dict[L1Invariant, tuple[str, ...]] = {
    "drift": ("account_id", "business_day_start"),
    "overdraft": ("account_id", "business_day_start"),
    "limit_breach": ("account_id", "business_day", "rail_name"),
    "stuck_pending": ("transaction_id",),
    "stuck_unbundled": ("transaction_id",),
    "supersession": ("transaction_id",),
}

_DAY_COLS = frozenset(
    {"business_day_start", "business_day_end", "business_day"}
)


def key_columns_for(invariant: L1Invariant) -> tuple[str, ...]:
    """The natural-key column names for ``invariant`` (see ``_KEY_COLS``)."""
    return _KEY_COLS[invariant]


def _go_to_invariant_sheet(
    driver: DashboardDriver,
    invariant: L1Invariant,
    period: tuple[date, date] | None,
) -> str:
    """Switch to the invariant's sheet + apply the period filter (when
    the invariant is time-series and ``period`` is set). Returns the
    detail-table visual title."""
    sheet_name, table_title, has_date_filter = _DASHBOARD_LAYOUT[invariant]
    driver.goto_sheet(sheet_name)
    if has_date_filter and period is not None:
        # ``set_date_range`` blocks on the QS settle (per X.2.q's
        # ``_settle_after_param_change``) so the read below sees the
        # post-filter state, not the spinner gap.
        driver.set_date_range(period[0].isoformat(), period[1].isoformat())
    return table_title


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
    table_title = _go_to_invariant_sheet(driver, invariant, period)
    return driver.table_row_count(table_title)


def l1_invariant_row_keys(
    driver: DashboardDriver,
    invariant: L1Invariant,
    period: tuple[date, date] | None,
) -> set[tuple[str | date, ...]]:
    """The set of natural-key tuples shown in the named invariant's L1
    dashboard table — for the X.2.j 4-way agreement test's row-identity
    asserts.

    Switches to the sheet + applies the period filter (like
    ``count_l1_invariant_rows``), then reads ``driver.table_rows`` and
    extracts the ``_KEY_COLS[invariant]`` columns from each row dict. Day
    cells (ISO strings — QS ``2026-05-07T00:00:00`` / App2
    ``2026-05-07 00:00:00``) are parsed to a ``date`` so they compare
    against the matview / scenario keys.

    **Caller's responsibility**: ``table_rows`` returns the renderer's
    DOM-visible window — QS virtualizes (~10 rows), App2 pages (50/page).
    For the row-identity comparison to be complete, the table must fit in
    one window; the caller asserts ``len(rows) == expected_total`` first
    so a truncated window fails loudly rather than passing a partial set.
    The agreement test's plants-only seed keeps these tables tiny (≤ a
    handful of rows), so this holds; if a denser seed grows them past the
    window, that assert catches it.
    """
    table_title = _go_to_invariant_sheet(driver, invariant, period)
    rows = driver.table_rows(table_title)
    key_cols = _KEY_COLS[invariant]
    out: set[tuple[str | date, ...]] = set()
    for r in rows:
        key: list[str | date] = []
        for col in key_cols:
            cell = r[col].strip()
            key.append(date.fromisoformat(cell[:10]) if col in _DAY_COLS else cell)
        out.add(tuple(key))
    return out


def l1_invariant_rows_seen(
    driver: DashboardDriver,
    invariant: L1Invariant,
    period: tuple[date, date] | None,
) -> int:
    """How many rows ``table_rows`` actually returned (the DOM window) —
    distinct from ``count_l1_invariant_rows`` (the page-size-bump *total*).
    The row-identity caller compares this against the total to confirm the
    window wasn't truncated before trusting ``l1_invariant_row_keys``."""
    table_title = _go_to_invariant_sheet(driver, invariant, period)
    return len(driver.table_rows(table_title))
