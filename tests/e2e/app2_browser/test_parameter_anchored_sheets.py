"""Browser e2e: parameter-anchored sheets surface their anchor control
correctly on App 2 (u.4.e.4).

Three sheets across two apps render *blank-until-you-pick* on default
load — each is driven by a value the analyst sets first:

- **Investigation Money Trail** — ``Chain root transfer`` (single-select
  dropdown, options from the chain-roots companion).
- **Investigation Account Network** — ``Anchor account`` (single-select
  dropdown, options from the narrow accounts dataset; the canonical
  "trust the chart, not the control" sheet).
- **L2 Flow Tracing Transfer Templates** — ``Template`` (multi-select
  dropdown over the L2's declared template names).

App 2 doesn't pre-pick a default — the bound analysis parameter
declares none — so the invariant this test guards is structural:
the anchor control is present in the filter bar with a non-empty option
universe, populated from its dataset (via the ``<app>_dashboard_driver``
fixtures).

**L1 Daily Statement** (``Account`` single-select, options from the
accounts companion) is the fourth such sheet but is *not* exercised here:
every visual on that sheet is account-AND-date scoped, so it renders
nothing on default load, and there's no always-rendering visual to
``wait_loaded`` on as a "the sheet is ready" signal (and getting a
non-blank render needs picking both an account and a known-good
``balance_date`` — out of scope for a structural parity test). Its
``Account`` control's shape — a ``ParameterDropdownSpec`` with
``options_dataset=DS_L1_ACCOUNTS[…]`` — is covered by ``test_tree_filter_specs``;
its render parity by ``test_l1_sheet_visuals::test_l1_dashboard_structure_matches_tree[app2]``.

What's *not* asserted here (verb / quirk gaps, covered elsewhere):

- "the widget shows no value on default load" — there's no
  ``DashboardDriver`` verb for a control's current selection; the
  blank-on-default behaviour is documented, not assayed.
- "``?param_<anchor>=<v>`` in the page URL narrows the destination" —
  the App 2 ``server.py`` route threads it (``_apply_url_param_overrides``,
  unit-tested in ``tests/unit/test_html_server.py``; the row-drill
  "walk" path exercises it end-to-end once ``test_inv_drilldown``
  re-lights), but QS's URL-param write doesn't sync its controls
  (``project_qs_url_parameter_no_control_sync``) and ``DashboardDriver.open``
  has no "open with a parameter pre-set" form — so there's nothing to
  parametrise over both renderers cleanly today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._marks import Need, Tier, needs, tier

from recon_gen.apps.l2_flow_tracing.app import _TRANSFER_TEMPLATES_NAME



if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver

pytestmark = [
    pytest.mark.e2e,
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]


def _assert_anchor_present_and_populated(
    driver: "DashboardDriver",
    dashboard_arg: str,
    *,
    sheet_name: str,
    anchor_label: str,
    visual_title: str,
) -> None:
    """Open ``sheet_name``, wait for ``visual_title`` to render, then
    assert a filter control labelled like ``anchor_label`` exists and is
    populated with options.

    DY.7.1 — the invariant is the anchor control's OPTION UNIVERSE, not
    the anchor-driven table. On App 2 that table is empty-on-default BY
    DESIGN: the bound analysis param holds a no-match sentinel and App 2
    does NOT auto-pick the first option the way QS's ``SelectAll=HIDDEN``
    did. The old ``table_row_count(visual)==0 ⇒ skip`` proxy therefore
    skipped UNCONDITIONALLY on App2 no matter how full the matview /
    dropdown were (verified: sasquatch_pr's money-trail matview has 48k
    edges / 43k roots, Account Network offers 139 anchors — all masked).
    Assert the dropdown universe directly; skip only when it's genuinely
    empty (e.g. an L2 that declares zero chains → empty money-trail edges
    → no roots to offer).
    """
    driver.open(dashboard_arg, sheet=sheet_name)
    driver.wait_loaded(visual_title)

    labels = driver.filter_labels()
    matched = next((lbl for lbl in labels if anchor_label in lbl), None)
    assert matched is not None, (
        f"{sheet_name!r}: expected an anchor control labelled like "
        f"{anchor_label!r} in the filter bar, got {labels!r}"
    )
    opts = driver.filter_options(matched)
    if not opts:
        pytest.skip(
            f"{sheet_name!r}: the {matched!r} control has an empty option "
            f"universe on the deployed L2 — its source dataset is empty "
            f"(e.g. an L2 declaring zero chains → empty money-trail edges). "
            f"Nothing to assert present+populated."
        )


@pytest.mark.parametrize(
    "sheet_name, anchor_label, visual_title",
    [
        ("Money Trail", "Chain root transfer", "Money Trail — Hop-by-Hop"),
        ("Account Network", "Anchor account",
         "Account Network — Touching Edges"),
    ],
)
def test_inv_anchor_control_present_and_populated(
    inv_dashboard_driver: tuple["DashboardDriver", str],
    sheet_name: str,
    anchor_label: str,
    visual_title: str,
) -> None:
    driver, dashboard_arg = inv_dashboard_driver
    _assert_anchor_present_and_populated(
        driver, dashboard_arg, sheet_name=sheet_name,
        anchor_label=anchor_label, visual_title=visual_title,
    )


def test_l2ft_transfer_templates_anchor_control_present_and_populated(
    l2ft_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    driver, dashboard_arg = l2ft_dashboard_driver
    _assert_anchor_present_and_populated(
        driver, dashboard_arg, sheet_name=_TRANSFER_TEMPLATES_NAME,
        anchor_label="Template", visual_title="Template Instances",
    )


def test_money_trail_anchor_pick_narrows_hop_by_hop_table(
    inv_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    """Picking a real Chain root transfer narrows the Hop-by-Hop table.

    Renderer-agnostic exercise of ``pick_filter`` against the Money
    Trail anchor dropdown — the QS-side flavour is the MUI Autocomplete
    (search-variant) that lazy-renders options and demands a typed
    query to surface them, behaviour first observed in AA.H.7 / driven
    by the driver in AA.H.8. The protocol verb encapsulates the
    typing + listbox-narrow dance so this test stays the same shape
    on both renderers: discover a real option via ``filter_options``,
    pick it with ``pick_filter``, assert the table re-fetches to
    non-empty hop rows for that root.

    Doubles as a regression guard for the AA.H.8 driver change — if a
    future refactor breaks the search-variant path on the QS leg
    (or the equivalent Tom Select widget on App2), this fails before
    it ships.
    """
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg, sheet="Money Trail")
    driver.wait_loaded("Money Trail — Hop-by-Hop")

    # DY.7.1 — the Hop-by-Hop table is empty-on-default BY DESIGN (App 2
    # holds the no-match root sentinel + doesn't auto-pick the first
    # option), which is exactly the pre-pick state this test then drives
    # OUT of. The old ``table_row_count==0 ⇒ skip`` fired on that expected
    # state before the pick ever ran. The only legit skip is a genuinely
    # empty root universe — guard on ``filter_options`` instead.
    options = driver.filter_options("Chain root transfer")
    if not options:
        pytest.skip(
            "Money Trail: the Chain-root-transfer dropdown has no options "
            "on the deployed L2 (empty money-trail matview — an L2 with "
            "zero chains). Nothing to pick+narrow against."
        )
    target_value = options[0]

    driver.pick_filter("Chain root transfer", [target_value])

    rows = driver.table_rows("Money Trail — Hop-by-Hop")
    assert len(rows) > 0, (
        f"Hop-by-Hop table empty after picking chain root "
        f"{target_value!r}; expected at least one hop row"
    )
