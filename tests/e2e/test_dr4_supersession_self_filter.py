"""Browser e2e: DR.4 same-sheet transaction self-filter on the
Supersession Audit.

Parametrized over ``[qs, app2]`` via ``l1_dashboard_driver``. The
self-filter is a ``DATA_POINT_CLICK`` drill (``drill_from_first_row``)
that writes the ``pL1SaTransaction`` pushdown param and re-renders the
SAME sheet — narrowing the Transactions Audit table to one logical
transaction's full entry trail. Because it's a control-write (not a
cross-sheet URL nav) it sidesteps the QS URL-param-no-control-sync quirk,
so both renderers narrow identically.

The ``Clear transaction filter`` right-click action
(``drill_from_first_row_via_menu``) resets the param to its show-all
sentinel — the standalone "back to all" affordance.

Data-agnostic: asserts the filter narrows to whatever transaction the
first row carries (no hard-coded id), and skips cleanly if the seeded DB
has no superseded rows to drill from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._marks import Need, Tier, needs, tier

from recon_gen.apps.l1_dashboard.app import _SUPERSESSION_AUDIT_NAME

if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    tier(Tier.QS_BROWSER),
    needs(Need.AWS_QS, Need.PLAYWRIGHT),
]

_AUDIT_TABLE = "Transactions Audit"


def test_supersession_audit_self_filter_narrows_to_one_transaction(
    l1_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    """Left-clicking a transaction_id cell on the Supersession Audit
    narrows the Transactions Audit table to exactly that transaction's
    trail; the Clear action restores the full audit."""
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=_SUPERSESSION_AUDIT_NAME)
    driver.wait_loaded(_AUDIT_TABLE)

    pre_rows = driver.table_rows(_AUDIT_TABLE, columns=["transaction_id"])
    if not pre_rows:
        pytest.skip(
            "Supersession Audit has no superseded rows in the seeded DB — "
            "nothing to drill from. (CI's auto-scenario plants supersession "
            "trails; a thin local seed may not.)"
        )
    target = pre_rows[0]["transaction_id"]
    full_count = driver.table_row_count(_AUDIT_TABLE)

    # DATA_POINT_CLICK self-filter → narrows the SAME sheet to `target`.
    driver.drill_from_first_row(_AUDIT_TABLE)
    driver.wait_loaded(_AUDIT_TABLE)

    post_rows = driver.table_rows(_AUDIT_TABLE, columns=["transaction_id"])
    assert post_rows, (
        "DR.4 self-filter emptied the audit table — the pushdown param "
        "narrowed to zero rows instead of the clicked transaction's trail. "
        "Check `pL1SaTransaction` bridges into "
        "`build_supersession_transactions_dataset`'s WHERE on both renderers."
    )
    assert all(r["transaction_id"] == target for r in post_rows), (
        f"DR.4 self-filter must narrow to exactly the clicked transaction "
        f"{target!r}; saw "
        f"{sorted({r['transaction_id'] for r in post_rows})}."
    )
    focus_count = driver.table_row_count(_AUDIT_TABLE)
    assert focus_count <= full_count, (
        "The self-filter must not grow the table beyond the unfiltered audit."
    )

    # Clear → the param returns to its show-all sentinel (full audit back).
    driver.drill_from_first_row_via_menu(_AUDIT_TABLE, "Clear transaction filter")
    driver.wait_loaded(_AUDIT_TABLE)
    cleared_count = driver.table_row_count(_AUDIT_TABLE)
    assert cleared_count >= focus_count, (
        f"Clear transaction filter must restore the full audit "
        f"({full_count} rows), but the table still shows {cleared_count} "
        f"(focused was {focus_count}) — the reset write didn't land."
    )
