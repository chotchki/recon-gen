"""Browser e2e — L2 Exceptions cross-sheet drills narrow the destination.

BS.3 follow-up (2026-05-30): right-clicking a row on the L2 Exceptions
detail table and picking "View in Rails (...)" / "View in Chains (...)"
must:

1. Navigate to the destination sheet (Rails / Chains).
2. Apply the drilled value as a filter on that sheet so the visible
   data narrows to the drilled rail / chain parent.

Pre-fix the drill wrote dedicated ``pL2ftRailDrill`` / ``pL2ftChainDrill``
parameters that fed a QS-side CalcField + FilterGroup — which worked on
QS but **silently no-op'd on App2** because the drill param never reached
a SQL ``<<$pL2ftRailDrill>>`` placeholder. The fix flipped the drill to
write the destination sheet's own user-facing picker parameter
(``pL2ftRail`` / ``pL2ftChainsChain``) so the existing dataset-param
pushdown narrows on both renderers.

Parametrized over ``[qs, app2]`` via ``l2ft_dashboard_driver``. Test
shape mirrors L1's cross-sheet drill coverage
(``test_l1_cross_sheet_drill_date_widening.py``) — driver verb is
``drill_from_first_row_via_menu`` (renderer-agnostic), assertion is
"every visible destination row is narrowed to the drilled value".

Data-agnostic: doesn't pin specific rail / chain names — reads the
source row's ``entity_a`` cell at test time and uses that as the
expected filter value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from recon_gen.apps.l2_flow_tracing.app import (
    _L2_EXCEPTIONS_NAME,
    _RAILS_TRANSACTIONS_TITLE,
)


if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


# Mapping from L2 Exceptions ``check_type`` value → which drill menu
# item to fire. Rail-targeted checks drill to the Rails sheet; chain-
# parent-targeted checks drill to the Chains sheet. Other check_types
# (Dead Limit Schedules, Dead Bundles Activity, Dead Metadata) have
# entity_a values that don't correspond to a rail or chain parent —
# the drill would land on an empty destination (the docstring on
# ``_populate_l2_exceptions_sheet`` acknowledges this), so this test
# skips them.
_RAIL_CHECK_TYPES = frozenset({
    "unmatched_rail_name",
    "dead_rails",
})
_CHAIN_CHECK_TYPES = frozenset({
    "chain_orphans",
})


def _first_row_with_check_type(
    rows: list[dict[str, str]],
    accepted: frozenset[str],
) -> dict[str, str] | None:
    """Return the first row whose ``check_type`` value is in ``accepted``.

    Header-cased so both renderers' column-name conventions resolve.
    """
    for row in rows:
        # Both renderers carry check_type — QS as "Check Type", App2 as
        # "check_type". Look up either form.
        ct = row.get("check_type") or row.get("Check Type")
        if ct in accepted:
            return row
    return None


def _entity_a(row: dict[str, str]) -> str:
    """Read the ``entity_a`` cell, tolerating QS's title-case headers."""
    ea = row.get("entity_a") or row.get("Entity A")
    assert ea, f"row missing entity_a/Entity A: keys={list(row.keys())}"
    return ea


def test_l2_exceptions_view_in_rails_narrows_destination(
    l2ft_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    """Drill "View in Rails (filter rail_name to entity_a)" → the
    Rails sheet's Transactions table narrows to rows where rail_name
    matches the drilled value."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet=_L2_EXCEPTIONS_NAME)
    driver.wait_loaded("L2 Violation Detail")

    rows = driver.table_rows(
        "L2 Violation Detail", columns=["check_type", "entity_a"],
    )
    target_row = _first_row_with_check_type(rows, _RAIL_CHECK_TYPES)
    if target_row is None:
        pytest.skip(
            "L2 Violation Detail has no rail-targeted rows "
            f"(check_type in {sorted(_RAIL_CHECK_TYPES)}) in the seeded "
            "DB — nothing to drill from. (Re-run with the auto-scenario "
            "plants that fire unmatched_rail_name / dead_rails.)"
        )
    drilled_rail = _entity_a(target_row)

    driver.drill_from_first_row_via_menu(
        "L2 Violation Detail",
        "View in Rails (filter rail_name to entity_a)",
    )
    driver.wait_loaded(_RAILS_TRANSACTIONS_TITLE)

    post_rows = driver.table_rows(_RAILS_TRANSACTIONS_TITLE, columns=["rail_name"])
    if len(post_rows) == 0:
        driver.screenshot()
        pytest.fail(
            f"Drill landed on Rails sheet but Transactions table is "
            f"empty — drilled rail {drilled_rail!r} has no postings in "
            f"the current window. (Either the drill isn't applying the "
            f"filter, or the drilled rail genuinely has zero rows; the "
            f"existing dropdown narrowing should bring at least the "
            f"sentinel-default 'all rows' result for an unfiltered rail "
            f"so empty here points at the drill, not the data.)"
        )
    mismatched: list[str] = []
    for r in post_rows:
        cell = r.get("rail_name") or r.get("Rail Name") or ""
        if cell != drilled_rail:
            mismatched.append(cell)
    assert not mismatched, (
        f"Drill 'View in Rails' fired for rail={drilled_rail!r} but "
        f"the Transactions table on the Rails sheet shows "
        f"{len(mismatched)} row(s) with a DIFFERENT rail_name: "
        f"{sorted(set(mismatched))[:5]} … This is the BS.3-follow-up "
        f"bug class — pre-fix the drill wrote pL2ftRailDrill (a "
        f"separate dedicated param) which fed a QS-only CalcField + "
        f"FilterGroup; App2 silently no-op'd because no SQL "
        f"<<$pL2ftRailDrill>> placeholder existed. The fix writes "
        f"pL2ftRail (the destination's own picker param) so both "
        f"renderers narrow via the shared dataset-param-pushdown path. "
        f"Sample row: {post_rows[0]!r}"
    )


def test_l2_exceptions_view_in_chains_narrows_destination(
    l2ft_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    """Drill "View in Chains (filter parent_chain_name to entity_a)" →
    the Chains sheet's Chain Instances table narrows to rows where
    parent_chain_name matches the drilled value."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet=_L2_EXCEPTIONS_NAME)
    driver.wait_loaded("L2 Violation Detail")

    rows = driver.table_rows(
        "L2 Violation Detail", columns=["check_type", "entity_a"],
    )
    target_row = _first_row_with_check_type(rows, _CHAIN_CHECK_TYPES)
    if target_row is None:
        pytest.skip(
            "L2 Violation Detail has no chain-targeted rows "
            f"(check_type in {sorted(_CHAIN_CHECK_TYPES)}) in the "
            "seeded DB — nothing to drill from."
        )
    drilled_chain = _entity_a(target_row)

    driver.drill_from_first_row_via_menu(
        "L2 Violation Detail",
        "View in Chains (filter parent_chain_name to entity_a)",
    )
    driver.wait_loaded("Chain Instances")

    post_rows = driver.table_rows(
        "Chain Instances", columns=["parent_chain_name"],
    )
    if len(post_rows) == 0:
        driver.screenshot()
        pytest.fail(
            f"Drill landed on Chains sheet but Chain Instances table "
            f"is empty — drilled chain {drilled_chain!r} has no "
            f"firings in the current window."
        )
    mismatched: list[str] = []
    for r in post_rows:
        cell = r.get("parent_chain_name") or r.get("Parent Chain Name") or ""
        if cell != drilled_chain:
            mismatched.append(cell)
    assert not mismatched, (
        f"Drill 'View in Chains' fired for chain={drilled_chain!r} but "
        f"the Chain Instances table shows {len(mismatched)} row(s) "
        f"with a DIFFERENT parent_chain_name: "
        f"{sorted(set(mismatched))[:5]} … Same BS.3-follow-up bug "
        f"class as the rails drill — picker-param-direct write fixed "
        f"the App2 narrowing. Sample row: {post_rows[0]!r}"
    )
