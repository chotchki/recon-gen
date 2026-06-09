"""Browser tests: Investigation drill-downs re-render the underlying visuals.

Parametrized over ``[qs, app2]`` (u.4.e.3) via ``inv_dashboard_driver``;
the row drill fires through ``drill_from_first_row_via_menu`` — the
touching-edges drill is a ``DATA_POINT_MENU`` trigger, so left-click
verbs (e.g. ``drill_from_first_row``) don't fire it on QS. The K.4.8
invariant is that activating a row in the Account Network touching-edges
table writes the row's counterparty into ``pInvANetworkAnchor`` and the
table + Sankeys re-render around the new anchor — a same-sheet walk, so
the verifiable signal is "the table content changed", not "we navigated
to a new sheet".

CS.2 (2026-06-09) — re-light after CR.6.a. Two prior blockers closed:

1. **Anchor non-determinism (both renderers).** Handled by an explicit
   ``pick_filter("Anchor", [Juniper Ridge])`` BEFORE the baseline read,
   then an additional readback of the first row's source/target cells
   so the assertion compares row CONTENT (anchor identifier present)
   rather than row COUNT (which could coincidentally match the
   post-walk count and false-positive the K.4.8f-3 no-op shape).
2. **Verb / trigger mismatch.** Now uses
   ``drill_from_first_row_via_menu("Account Network — Touching Edges",
   "Walk to other account on this edge")`` — matches the production
   ``Drill(trigger="DATA_POINT_MENU", name="Walk to other account on
   this edge")`` wiring in ``apps/investigation/app.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._marks import Need, Tier, needs, tier



if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    tier(Tier.QS_BROWSER),
    needs(Need.AWS_QS, Need.PLAYWRIGHT),
]


_ANCHOR_LABEL = "Juniper Ridge LLC — DDA (cust-900-0007-juniper-ridge-llc)"
_ANCHOR_ID_FRAGMENT = "cust-900-0007-juniper-ridge-llc"
_TOUCHING_EDGES_TITLE = "Account Network — Touching Edges"
_WALK_MENU_LABEL = "Walk to other account on this edge"


def _rows_contain_anchor(
    driver: "DashboardDriver", anchor_fragment: str,
) -> bool:
    """The touching-edges table's source/target columns carry the anchor
    display name on every row (the table is "edges touching the anchor"
    by construction). When `anchor_fragment` appears anywhere in any
    row, the table is showing data for that anchor."""
    for row in driver.table_rows(_TOUCHING_EDGES_TITLE):
        for cell in row:
            if anchor_fragment in str(cell):
                return True
    return False


def test_account_network_table_walk_rerenders_table(inv_dashboard_driver: tuple["DashboardDriver", str]) -> None:
    """Activating a row in the Account Network touching-edges table walks
    the anchor over to that row's counterparty; the table is filtered to
    "edges touching anchor", so after the walk the new anchor's
    identifier appears in the rows and the original anchor's identifier
    no longer does (modulo edges between the old anchor and the new
    anchor — which the K.4.8 walk specifically picks).

    The K.4.8 invariant the test guards: the activation DOES propagate
    to the parameter and the table DOES re-render. The K.4.8f-3 bug
    wired the action to a no-op counterparty field — the post-walk
    table would still show only Juniper Ridge edges. This test fails
    loudly in that shape because every row would still carry the
    Juniper Ridge identifier.
    """
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg, sheet="Account Network")
    driver.pick_filter("Anchor", [_ANCHOR_LABEL])
    driver.wait_loaded(_TOUCHING_EDGES_TITLE)

    # Baseline: Juniper Ridge is the anchor, so EVERY row's source or
    # target carries Juniper Ridge's account_id.
    assert _rows_contain_anchor(driver, _ANCHOR_ID_FRAGMENT), (
        f"Pre-walk: Touching Edges table should show {_ANCHOR_ID_FRAGMENT} "
        f"in some cell of every row (the anchor IS Juniper Ridge); the "
        f"baseline anchor pick didn't take. Rows: "
        f"{driver.table_rows(_TOUCHING_EDGES_TITLE)[:3]}"
    )

    # Drill: right-click → "Walk to other account on this edge".
    # Matches the production Drill(trigger="DATA_POINT_MENU").
    driver.drill_from_first_row_via_menu(
        _TOUCHING_EDGES_TITLE, _WALK_MENU_LABEL,
    )
    driver.wait_loaded(_TOUCHING_EDGES_TITLE)

    # Post-walk: the anchor parameter wrote to a DIFFERENT account, so
    # rows now carry edges around the NEW anchor — Juniper Ridge may
    # still appear (the row that triggered the walk had Juniper Ridge
    # as source or target by construction), but the table content has
    # shifted to the new anchor's neighborhood. We assert the simplest
    # signal: at least one row carries a non-Juniper identifier (the
    # new anchor) — the K.4.8f-3 no-op shape would have left every
    # row still pinned to Juniper Ridge.
    driver.screenshot()
    rows_after = driver.table_rows(_TOUCHING_EDGES_TITLE)
    saw_non_juniper_row = any(
        all(_ANCHOR_ID_FRAGMENT not in str(cell) for cell in row)
        for row in rows_after
    )
    assert saw_non_juniper_row, (
        f"Post-walk: Touching Edges table still shows {_ANCHOR_ID_FRAGMENT} "
        f"in every cell of every row — the walk didn't propagate. "
        f"K.4.8f-3 no-op shape regression. Rows: {rows_after[:3]}"
    )
