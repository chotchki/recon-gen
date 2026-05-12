"""Browser tests: Investigation drill-downs re-render the underlying visuals.

Parametrized over ``[qs, app2]`` (u.4.e.3) via ``inv_dashboard_driver``;
the row drill fires through the renderer-agnostic ``drill_from_first_row``
verb. The K.4.8 invariant is that activating a row in the Account Network
touching-edges table writes the row's counterparty into
``pInvANetworkAnchor`` and the table + Sankeys re-render around the new
anchor — a same-sheet walk, so the verifiable signal is "the table
contents changed", not "we navigated to a new sheet".

Stays ``@pytest.mark.skip`` — two reasons:

1. **Anchor non-determinism (both renderers).** The test needs the
   Account Network's *initial* anchor to be deterministic so the "row
   count changes" assertion has a known baseline; the dropdown auto-picks
   when no anchor is set, racing the test. Needs a deterministic
   anchor seed (re-light once that lands).
2. **App 2 URL-param threading gap (app2 leg).** The App 2 walk drill
   navigates to ``?param_pInvANetworkAnchor=<row>`` on the same sheet,
   but the sheet-page render doesn't yet thread query-string ``param_*``
   keys into the filter form's initial values, so the visuals re-fetch
   with the *default* anchor — no re-render. Threading that in is
   u.4.e.4-adjacent (the "thread the analysis param's default into
   ``make_filter_specs_for_sheet``" work generalizes to URL params); the
   app2 leg can't pass until then.

(There's also a third, pre-existing wart: the touching-edges drill is a
``DATA_POINT_MENU`` trigger but the test calls ``drill_from_first_row``
— a left-click verb — which on QS doesn't fire a menu action. The App 2
renderer makes the row left-clickable regardless of trigger, so the
verb at least *does* something there. Fold this into the rewrite when
re-lighting.)
"""

from __future__ import annotations

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.skip(
    reason=(
        "Initial-anchor non-determinism (the Anchor dropdown auto-picks "
        "when no value is set, so the 'row count changed' baseline races) "
        "+ App 2 URL-param threading gap (the walk navigates to "
        "?param_pInvANetworkAnchor=<row> but the sheet render doesn't yet "
        "feed query params into the filter form, so no re-render). "
        "Re-light once both are fixed; see module docstring."
    )
)
def test_account_network_table_walk_rerenders_table(inv_dashboard_driver):
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
