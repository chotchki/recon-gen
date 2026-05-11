"""Browser tests: L1 dashboard filter controls actually narrow the data.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright in
the test body; ``qs_driver`` from conftest speaks the driver). Both
tests stay data-agnostic per the no-hardcoded-data rule:

- **Date-range narrow** is verified on a per-invariant sheet (Drift),
  NOT Today's Exceptions. The Today's Exceptions UNION SQL pre-filters
  to ``MAX(business_day_start)`` from current_daily_balances by design,
  so the dashboard's date picker is a structural no-op there. The
  per-invariant sheets have no SQL pre-filter, so the date filter on the
  dashboard layer narrows their tables. A future window (2099) empties
  the table — works regardless of what the seed plants.

- **Dropdown shape** is verified by reading the dropdown's advertised
  options and confirming it exposes ≥1 selectable value (data-derived —
  we don't hardcode which values appear). Full "select-narrows-data"
  needs the demo to plant enough diverse data that any single value-pick
  reliably drops the row count; that's the per-instance seed's job, not
  this test's.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.xfail(
    reason=(
        "Sasquatch L1 dashboard render flake (task backlog #466). The "
        "Leaf Account Drift table intermittently renders zero rows on the "
        "first browser-layer run after a fresh deploy even though the drift "
        "matview + L1 data are present (db smoke + api layer pass) — a "
        "QS-side render/timing issue, not a data issue."
    ),
    strict=False,
)
def test_date_range_filter_narrows_drift_sheet(qs_driver, l1_dashboard_id):
    """Setting the date range to a 2099 future window must empty (or at
    least shrink) the Leaf Account Drift table — no L2 instance plants
    drift in 2099.

    Verifies the M.2b.1 parameter-bound TimeRangeFilter actually
    cascades from the date pickers through the params into the dataset
    query.
    """
    qs_driver.open(l1_dashboard_id, sheet="Drift")
    qs_driver.wait_loaded("Leaf Account Drift")
    before = len(qs_driver.table_rows("Leaf Account Drift"))
    assert before > 0, (
        f"Leaf Account Drift must have data pre-filter, got {before}"
    )

    qs_driver.set_date_range("2099-01-01", "2099-12-31")
    qs_driver.wait_loaded("Leaf Account Drift")
    after = len(qs_driver.table_rows("Leaf Account Drift"))

    qs_driver.screenshot()
    assert after < before, (
        f"Leaf Account Drift should narrow with a future date range; "
        f"before={before}, after={after}"
    )


def test_check_type_dropdown_exposes_options(qs_driver, l1_dashboard_id):
    """The Check Type dropdown on Today's Exceptions exposes the L1
    invariant view names (drift / ledger_drift / overdraft / …) as
    selectable values. The option universe comes from the data — we
    only assert the dropdown is populated, not which values appear.
    """
    qs_driver.open(l1_dashboard_id, sheet="Today's Exceptions")
    options = qs_driver.filter_options("Check Type")
    assert len(options) >= 1, (
        f"Check Type dropdown should expose ≥1 value, got {options}"
    )
