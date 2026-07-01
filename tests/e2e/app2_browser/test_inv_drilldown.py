"""Browser tests: Investigation drill-downs re-render the underlying visuals.

Drives App2 (u.4.e.3) via ``inv_dashboard_driver``;
the row drill fires through ``drill_from_first_row_via_menu`` — the
touching-edges drill is a ``DATA_POINT_MENU`` trigger, so left-click
verbs (e.g. ``drill_from_first_row``) don't fire it. The K.4.8
invariant is that activating a row in the Account Network touching-edges
table writes the row's counterparty into ``pInvANetworkAnchor`` and the
table + Sankeys re-render around the new anchor — a same-sheet walk, so
the verifiable signal is "the table content changed", not "we navigated
to a new sheet".

CS.2 (2026-06-09) — re-light after CR.6.a. Two prior blockers closed:

1. **Anchor non-determinism.** Handled by an explicit
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
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]


_TOUCHING_EDGES_TITLE = "Account Network — Touching Edges"
_WALK_MENU_LABEL = "Walk to other account on this edge"


def _id_fragment(display_label: str) -> str:
    """Account picker labels are ``"<name> (<account_id>)"`` — pull the id
    so the row-content assertions can match on the stable identifier
    rather than the display name."""
    lo, hi = display_label.rfind("("), display_label.rfind(")")
    return display_label[lo + 1 : hi] if 0 <= lo < hi else display_label


def _rows_contain_anchor(
    driver: "DashboardDriver", anchor_fragment: str,
) -> bool:
    """The touching-edges table's source/target columns carry the anchor
    display name on every row (the table is "edges touching the anchor"
    by construction). When `anchor_fragment` appears anywhere in any
    row, the table is showing data for that anchor.

    ``table_rows`` returns list[dict] (column-name → cell value), so match
    on the VALUES — iterating the row directly would walk the column
    NAMES (a latent bug the phantom-anchor pick failure used to mask)."""
    for row in driver.table_rows(_TOUCHING_EDGES_TITLE):
        for cell in row.values():
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
    table would still show only the original anchor's edges. This test
    fails loudly in that shape because every row would still carry the
    original anchor's identifier.

    DY.3 (2026-07-01) — the anchor is now DERIVED from what the picker
    actually offers (its seed page), NOT hardcoded. The prior hardcode
    ("Juniper Ridge LLC", a ``convergence_anchor``) was declared in
    sasquatch_pr.yaml but NEVER materialized in the seed — no
    transactions, no money-trail edges — so the picker could never
    advertise it and the pick could never land: the test pointed at a
    phantom account (it only ever passed on the now-deleted QS leg).
    Deriving from ``filter_options`` guarantees a real, pickable anchor
    by construction: the accounts dataset's registered SQL feeds BOTH
    the picker options and the touching-edges visual, so any offered
    label is a row the visual sees AND a value the dropdown can pick.
    """
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg, sheet="Account Network")
    # Real anchor from the picker's own seed page — same registered SQL
    # backs the options and the visual, so this is pickable + on-screen
    # by construction (no phantom-account hardcode).
    offered = driver.filter_options("Anchor account")
    assert offered, (
        "Anchor account picker advertised NO options — the accounts "
        "dataset returned an empty universe (seed / matview gap)."
    )
    anchor_label = offered[0]
    anchor_fragment = _id_fragment(anchor_label)
    driver.pick_filter("Anchor account", [anchor_label])
    driver.wait_loaded(_TOUCHING_EDGES_TITLE)

    # Baseline: the picked account is the anchor, so EVERY row's source
    # or target carries its account_id.
    assert _rows_contain_anchor(driver, anchor_fragment), (
        f"Pre-walk: Touching Edges table should show {anchor_fragment} "
        f"in some cell of every row (the anchor is {anchor_label!r}); the "
        f"baseline anchor pick didn't take. Rows: "
        f"{driver.table_rows(_TOUCHING_EDGES_TITLE)[:3]}"
    )

    # The menu drill writes the first row's counterparty (the "other
    # account on this edge") into pInvANetworkAnchor — capture it as the
    # walk target BEFORE drilling.
    pre_rows = driver.table_rows(_TOUCHING_EDGES_TITLE)
    walk_target = _id_fragment(str(pre_rows[0].get("Counterparty Display", "")))
    assert walk_target and walk_target != anchor_fragment, (
        f"First-row counterparty {walk_target!r} is empty or == the anchor — "
        f"nowhere to walk. Row: {pre_rows[0]}"
    )

    # Drill: right-click → "Walk to other account on this edge".
    # Matches the production Drill(trigger="DATA_POINT_MENU").
    driver.drill_from_first_row_via_menu(
        _TOUCHING_EDGES_TITLE, _WALK_MENU_LABEL,
    )
    driver.wait_loaded(_TOUCHING_EDGES_TITLE)
    driver.screenshot()

    # Post-walk: the anchor moved to the walk target, so touching-edges are
    # now computed relative to IT. The shared edge we walked across now
    # lists the ORIGINAL anchor as its counterparty — an identifier that can
    # NEVER appear in the counterparty column while the original IS the
    # anchor (counterparty = the OTHER end from the anchor). So the original
    # anchor surfacing as a counterparty is a crisp, sort-order-independent
    # proof the walk propagated; the K.4.8f-3 no-op shape (anchor unmoved)
    # could never surface it. (The naive "a row without the original anchor"
    # signal false-fails on tight 2-account sweeps where the shared edges
    # dominate both accounts' tables.)
    post_rows = driver.table_rows(_TOUCHING_EDGES_TITLE)
    assert post_rows, "post-walk touching-edges table is empty"
    post_counterparties = {
        _id_fragment(str(r.get("Counterparty Display", ""))) for r in post_rows
    }
    assert anchor_fragment in post_counterparties, (
        f"Post-walk: no row lists the original anchor {anchor_fragment} as its "
        f"Counterparty — the walk to {walk_target} didn't move the anchor "
        f"(K.4.8f-3 no-op). Counterparties seen: {sorted(post_counterparties)}"
    )
