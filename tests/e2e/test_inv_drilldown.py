"""Browser tests: Investigation drill-downs re-render the underlying visuals.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright
in the test body). The K.4.8 invariant is that clicking a row in the
Account Network touching-edges table writes the row's counterparty into
``pInvANetworkAnchor`` and the table + Sankeys re-render around the new
anchor — the drill stays on the same sheet, so the verifiable signal is
"the table contents changed", not "we navigated to a new sheet".

Stays ``@pytest.mark.skip`` for the same reason as before: the test
needs the Account Network's *initial* anchor to be deterministic so the
"row count changes" assertion has a known baseline. The dropdown auto-
picks if no anchor is set, racing the test, and the original
``#p.pInvANetworkAnchor=…`` URL-hash workaround broke embed loading.
Driver-level: needs a `pick_filter("Anchor", […])`-equivalent for the
``ParameterDropDownControl`` (works today via `pick_filter`), but the
real fix is the v8.5.7-class drill-doesn't-fire diagnostic — re-light
this test once we have a deterministic anchor seed.
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.skip(
    reason=(
        "Initial-anchor non-determinism — the Anchor dropdown auto-picks "
        "when no value is set, and the original '#p.pInvANetworkAnchor=…' "
        "URL-hash workaround breaks embed loading. Needs either a "
        "dropdown-pick to seed the anchor before the drill OR a more "
        "reliable witness for walk propagation than touching-edges row "
        "count. Tracked for K.4.9 follow-up."
    )
)
def test_account_network_table_walk_rerenders_table(
    qs_driver, inv_dashboard_id,
):
    """Clicking a row in the Account Network touching-edges table walks
    the anchor over to that row's counterparty; the table is filtered to
    "edges touching anchor", so the new anchor narrows it to a different
    set of rows — the row count changes (could be larger or smaller, since
    different anchors have different fanout). The K.4.8 invariant the
    test guards: the click DOES propagate to the parameter and the table
    DOES re-render. A regression that wired the action to a no-op
    counterparty field (the K.4.8f-3 bug) would leave it unchanged.
    """
    qs_driver.open(inv_dashboard_id, sheet="Account Network")
    qs_driver.pick_filter(
        "Anchor",
        ["Juniper Ridge LLC — DDA (cust-900-0007-juniper-ridge-llc)"],
    )
    qs_driver.wait_loaded("Account Network — Touching Edges")
    before = len(qs_driver.table_rows("Account Network — Touching Edges"))
    assert before > 1, (
        f"Account Network table should have multiple rows pre-walk, got {before}"
    )

    qs_driver.drill_from_first_row("Account Network — Touching Edges")
    qs_driver.wait_loaded("Account Network — Touching Edges")
    after = len(qs_driver.table_rows("Account Network — Touching Edges"))

    qs_driver.screenshot()
    assert after != before, (
        f"Account Network table should re-render with a different row "
        f"count after walking the anchor; before={before}, after={after}"
    )
