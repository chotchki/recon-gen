"""Browser tests: AA.B (Daily Statement Role cascade) + AA.E (account
search-by-name-AND-id) — parametrized over ``[qs, app2]`` via
``l1_dashboard_driver``.

Pairs naturally with ``test_l1_filters.py`` (which covers the universal
date filter + the Today's Exceptions Check Type dropdown). This file
exists separately so the Daily Statement / Account-display contracts
can be triaged independently — the Daily Statement Account dropdown
silently broke between AA.E.2 and AA.E.3 because the AA.E.2 sweep
missed the direct ``add_parameter_dropdown`` callsite (the JSON pin
``test_aa_e_2_daily_statement_account_dropdown_binds_display_column``
catches the wiring; this file catches the runtime symptom — picked
account → table renders rows).

Test shapes follow the X.2.q DashboardDriver protocol; both renderers
exercise the same SQL pushdown (``DS_L1_ACCOUNTS`` cascade for the
Role dropdown; ``_account_display_clause`` for the display-format
WHERE), so a parity gap = a real wiring divergence, not a flavour
choice.
"""

from __future__ import annotations

import pytest

from tests.e2e._daily_statement_pick import find_account_day_with_data


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


# AA.B — Daily Statement Role cascade --------------------------------------


def test_daily_statement_role_then_account_populates_table(
    l1_dashboard_driver, cfg,
):
    """AA.B.1 workflow — picking a Role THEN an Account renders the
    Posted Money Records table populated for that account.

    Pair to ``test_daily_statement_picked_account_narrows_table``: that
    test covers the direct-pick path (only Account is picked); this
    one covers the cascade path the operator typically follows ("filter
    by my team's role, then drill to one account"). Both must end at
    the same outcome: the per-account-day detail table renders ≥1 row
    for the picked Account.

    Why this shape (and not "Role narrows the dropdown options"). The
    cascade-narrows-dropdown claim was originally asserted by
    ``test_daily_statement_role_narrows_posted_money_records_table``,
    but the AA.B.4.followon investigation found that's not deliverable
    on either renderer — QS dropdown option lists are snapshot at
    dashboard load (snapshot-not-live, standing quirk family
    ``project_qs_url_parameter_no_control_sync``) and App2's filter
    widgets render once per sheet GET. So a "Role narrows Account
    options" assertion fails on both legs regardless of seed shape.
    What the operator CAN do — and what AA.B.1's wiring genuinely
    supports — is pick both params in sequence and read the result.
    This test pins that the picked Account survives the combined
    Role+Account filter and the table populates. JSON pin
    ``test_aa_b_1_l1_accounts_dataset_is_role_cascaded`` separately
    guards the SQL substitution itself as a structural regression.

    AA.B.5.followon — the picked ``(role, account, day)`` triple comes
    from the deployed DB so we don't depend on QS's "yesterday" date
    default landing on a day with rows for the first-alphabetical
    account. Was clock-flaky pre-fix: when the chain crossed UTC
    midnight, "yesterday" shifted to a thinner day and the test failed
    on calendar luck (cust-011 had 0 tx on 2026-05-16 even though it
    had 349 lifetime). Now: query DB for the most-recent (account,
    day) pair with rows, drive all three pickers to those values.
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet="Daily Statement")
    target_visual = "Posted Money Records"
    driver.wait_loaded(target_visual)

    picked_account, picked_role, picked_day = find_account_day_with_data(cfg)

    # Sanity: the Role dropdown should advertise the role we just
    # picked. If it doesn't, the cascade SQL is out of sync with the
    # deployed data — surface that as the failure shape rather than
    # silently moving on.
    role_options = driver.filter_options("Role")
    assert picked_role in role_options, (
        f"Helper picked role {picked_role!r} for account "
        f"{picked_account!r} but Role dropdown advertises "
        f"{role_options}. Cascade SQL out of sync with deployed data."
    )

    driver.pick_filter("Role", [picked_role])
    driver.pick_filter("Account", [picked_account])
    # No-op on App2 (date picker not rendered there; dataset SQL
    # already returns all rows since date narrowing is QS-only).
    driver.set_date("Business Day", picked_day)
    driver.wait_loaded(target_visual)
    rows = driver.table_rows(target_visual)
    driver.screenshot()
    assert len(rows) > 0, (
        f"After Role={picked_role!r} + Account={picked_account!r} + "
        f"Business Day={picked_day!r}, Posted Money Records should "
        f"render ≥1 row. Got {len(rows)}. AA.B.1 SQL cascade likely "
        f"broke the combined-filter shape — the Account's row should "
        f"survive both the role-narrowed accounts dataset AND the "
        f"per-account-day matview's Account WHERE clause."
    )


# AA.E — Account dropdown shows "name (id)" form ---------------------------


@pytest.mark.parametrize("sheet_name", [
    "Drift",
    "Overdraft",
    "Limit Breach",
    "Today's Exceptions",
    "Daily Statement",
    "Transactions",
])
def test_account_dropdown_shows_display_form(
    l1_dashboard_driver, sheet_name: str,
):
    """AA.E.2 — every L1 Account dropdown advertises options in the
    ``"<name> (<id>)"`` display form (substring-searchable by either
    name or id), not the bare-id form.

    Detect the shape by reading the options and asserting ≥1 option
    matches the ``"... (...)"`` pattern — a parenthesized suffix that
    the bare-id form ('account-001', 'merchant-12') doesn't carry.

    Mirrors AA.E.1's hybrid decision (concat in dropdowns, two-column
    in tables). The 6 sheets parametrized here are the 6 L1 sheets
    that carry an Account picker (Pending Aging + Unbundled Aging are
    structurally identical to the others and excluded for runtime
    parsimony — the same ``options_column="account_display"`` flip
    applies, pinned at JSON level by AA.E.2's unit tests).
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=sheet_name)

    options = driver.filter_options("Account")
    assert options, (
        f"{sheet_name!r}: Account dropdown returned no options. "
        f"Companion dataset (DS_L1_ACCOUNTS) is empty? Sentinel "
        f"semantics broken?"
    )
    # Match the "Name (id)" shape: at least one option must contain
    # " (" followed by ")" at the end. Bare-id options ("external-001",
    # "merchant-12") have no parens.
    display_form = [o for o in options if " (" in o and o.endswith(")")]
    assert display_form, (
        f"{sheet_name!r}: Account dropdown options don't carry the "
        f"display form '<name> (<id>)' — AA.E.2 regression. "
        f"First 3 options: {options[:3]}"
    )


def test_daily_statement_picked_account_narrows_table(
    l1_dashboard_driver, cfg,
):
    """AA.E.2 fix + AA.B.4 — after picking an Account from the Daily
    Statement dropdown, the per-account-day Daily Statement table
    surfaces rows for that account.

    This was the silent symptom of the AA.E.2 miss: the dropdown
    bound bare ``account_id`` but the WHERE clause expected
    ``(account_name || ' (' || account_id || ')')`` — every pick
    resulted in an empty table. Test pins the fix end-to-end through
    both renderers.

    AA.B.5.followon — picks the (account, day) pair from the deployed
    DB so the test isn't clock-fragile. Pre-fix: picked
    ``options[0]`` (alphabetical first account) and inherited the
    Business Day picker's "yesterday" default. The combination broke
    on a chain that crossed UTC midnight — "yesterday" shifted to a
    thinner day where that account had zero transactions, and QS
    correctly rendered "No data found." Now: helper returns a known-
    good ``(account_display, business_day)`` pair, test drives both
    pickers to those values.
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet="Daily Statement")

    picked_account, _picked_role, picked_day = find_account_day_with_data(cfg)

    # Pre-condition sanity: the helper returned an account, and the
    # dropdown advertises that exact display string. If not, the
    # AA.E.2 ``account_display`` binding is out of sync with the
    # dataset's WHERE clause — surface that as the failure shape
    # rather than blaming the table assertion.
    options = driver.filter_options("Account")
    assert picked_account in options, (
        f"Helper picked {picked_account!r} but Account dropdown "
        f"options don't include it (first 5: {options[:5]}). "
        f"AA.E.2 binding likely out of sync — the dropdown's "
        f"``LinkedValues.from_column(..account_display)`` should "
        f"produce the same ``Name (id)`` shape the helper builds."
    )

    driver.pick_filter("Account", [picked_account])
    # No-op on App2 (date picker not rendered there; dataset SQL
    # already returns all rows since date narrowing is QS-only).
    driver.set_date("Business Day", picked_day)

    # "Posted Money Records" is the canonical per-account-day detail
    # table on Daily Statement (see `apps/l1_dashboard/app.py::
    # populate_daily_statement_sheet`). The sheet's 5 KPIs surface
    # the day's walk; this table is the row-by-row support. Original
    # version of this test looked for a visual literally titled
    # "Daily Statement" (the sheet name, NOT a visual title) and
    # fell back to `visual_titles()[0]` (an Opening Balance KPI) —
    # both wrong.
    target_visual = "Posted Money Records"
    driver.wait_loaded(target_visual)

    rows = driver.table_rows(target_visual)
    driver.screenshot()
    assert len(rows) > 0, (
        f"After picking Account={picked_account!r} + "
        f"Business Day={picked_day!r}, Posted Money Records should "
        f"render ≥1 row. Got {len(rows)}. This is the AA.E.2 silent-"
        f"empty regression — Daily Statement's Account dropdown must "
        f"bind to 'account_display' for the WHERE clause to match "
        f"(JSON pin: test_aa_e_2_daily_statement_account_dropdown_binds_display_column)."
    )
