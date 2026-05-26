"""Browser tests: Investigation drill-downs re-render the underlying visuals.

Parametrized over ``[qs, app2]`` (u.4.e.3) via ``inv_dashboard_driver``;
the row drill fires through the renderer-agnostic ``drill_from_first_row``
verb. The K.4.8 invariant is that activating a row in the Account Network
touching-edges table writes the row's counterparty into
``pInvANetworkAnchor`` and the table + Sankeys re-render around the new
anchor — a same-sheet walk, so the verifiable signal is "the table
contents changed", not "we navigated to a new sheet".

Stays ``@pytest.mark.skip`` — two reasons (the third, the App 2
URL-param threading gap, was the original blocker for the app2 leg and
is now closed by u.4.e.4#1 — ``server.py::_apply_url_param_overrides``
threads ``?param_pInvANetworkAnchor=<row>`` into the destination sheet's
filter form, so the App 2 walk drill *does* re-render around the new
anchor; re-light the app2 leg once the remaining two are addressed):

1. **Anchor non-determinism (both renderers).** The test needs the
   Account Network's *initial* anchor to be deterministic so the "row
   count changes" assertion has a known baseline; the dropdown auto-picks
   when no anchor is set, racing the test. Needs a deterministic
   anchor seed (re-light once that lands).
2. **Verb / trigger mismatch.** The touching-edges drill is a
   ``DATA_POINT_MENU`` trigger but the test calls ``drill_from_first_row``
   — a left-click verb — which on QS doesn't fire a menu action. Switch to
   ``drill_from_first_row_via_menu("Account Network — Touching Edges",
   "Walk to other account on this edge")`` when re-lighting (the App 2
   renderer also makes the row left-clickable regardless of trigger, so
   either verb does something there).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest



if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.skip(
    reason=(
        "Initial-anchor non-determinism (the Anchor dropdown auto-picks "
        "when no value is set, so the 'row count changed' baseline races) "
        "+ verb/trigger mismatch (the touching-edges drill is "
        "DATA_POINT_MENU but the test uses the left-click drill_from_first_row "
        "verb — fire it via drill_from_first_row_via_menu instead). The App 2 "
        "URL-param threading gap that was the third blocker is now closed by "
        "u.4.e.4#1. Re-light once the remaining two are fixed; see module "
        "docstring."
    )
)
def test_account_network_table_walk_rerenders_table(inv_dashboard_driver: tuple["DashboardDriver", str]) -> None:
    """Activating a row in the Account Network touching-edges table walks
    the anchor over to that row's counterparty; the table is filtered to
    "edges touching anchor", so the new anchor narrows it to a different
    set of rows — the row count changes (could be larger or smaller,
    since different anchors have different fanout). The K.4.8 invariant
    the test guards: the activation DOES propagate to the parameter and
    the table DOES re-render. A regression that wired the action to a
    no-op counterparty field (the K.4.8f-3 bug) would leave it unchanged.
    """
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg, sheet="Account Network")
    driver.pick_filter(
        "Anchor",
        ["Juniper Ridge LLC — DDA (cust-900-0007-juniper-ridge-llc)"],
    )
    driver.wait_loaded("Account Network — Touching Edges")
    before = len(driver.table_rows("Account Network — Touching Edges"))
    assert before > 1, (
        f"Account Network table should have multiple rows pre-walk, got {before}"
    )

    driver.drill_from_first_row("Account Network — Touching Edges")
    driver.wait_loaded("Account Network — Touching Edges")
    after = len(driver.table_rows("Account Network — Touching Edges"))

    driver.screenshot()
    assert after != before, (
        f"Account Network table should re-render with a different row "
        f"count after walking the anchor; before={before}, after={after}"
    )
