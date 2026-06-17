"""Browser tests: AA.B (Daily Statement Role cascade) + AA.E (account
search-by-name-AND-id) — parametrized over ``[qs, app2]`` via
``l1_dashboard_driver``.

Pairs naturally with ``test_l1_filters.py`` (which covers the universal
date filter + the L1 Exceptions Check Type dropdown). This file
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

from typing import TYPE_CHECKING, Any

from decimal import Decimal

import pytest

from tests._marks import Need, Tier, needs, tier

from recon_gen.apps.l1_dashboard.app import (
    _DAILY_STATEMENT_NAME,
    _DRIFT_NAME,
    _LIMIT_BREACH_NAME,
    _OVERDRAFT_NAME,
    _L1_EXCEPTIONS_NAME,
    _TRANSACTIONS_NAME,
)
from recon_gen.apps.l1_dashboard.datasets import (
    build_daily_statement_summary_dataset,
)
from recon_gen.common.l2.primitives import CREDIT
from tests.e2e._daily_statement_pick import (
    find_account_day_with_data,
    find_one_account_day_per_role,
    find_two_days_for_same_account,
)
from tests.e2e._kpi_parse import parse_currency_kpi as _parse_currency_kpi
from recon_gen.common.config import Config



if TYPE_CHECKING:
    from recon_gen.common.l2 import L2Instance
    from recon_gen.common.models import DatasetParameter
    from tests.e2e._drivers import DashboardDriver

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    tier(Tier.QS_BROWSER),
    needs(Need.AWS_QS, Need.PLAYWRIGHT),
]


def _summary_sql_and_params(
    cfg: Config, l2: "L2Instance",
) -> tuple[str, list["DatasetParameter"]]:
    """Lift the Daily Statement Summary dataset's SQL + DatasetParameters
    by calling the production builder. BG.2's honest gate compares
    rendered KPI values to the SAME SQL the dashboard issues."""
    ds = build_daily_statement_summary_dataset(cfg, l2)
    physical = next(iter(ds.PhysicalTableMap.values()))
    assert physical.CustomSql is not None, "Dataset missing CustomSql"
    sql_str = physical.CustomSql.SqlQuery
    return sql_str, list(ds.DatasetParameters or [])


# CQ.4 — Daily Statement Account picker (post-Role-cascade-drop) ----------


def test_daily_statement_account_populates_table(
    l1_dashboard_driver: tuple["DashboardDriver", str], cfg: Config,
) -> None:
    """CQ.4.a workflow — picking an Account renders the Posted Money
    Records table populated for that account.

    Pre-CQ.4 was ``test_daily_statement_role_then_account_populates_table``
    (the workflow picked Role THEN Account because the Role dropdown
    bridged into the Account picker via the pL1DsRole cascade). The
    Role cascade is dropped per operator lock 2026-06-08 ("ALL internal
    accounts should be searchable") — the Account picker now sources
    every ``scope = 'internal'`` account directly. This test pins that
    the picked Account + Business Day combination still produces a
    populated detail table on both renderers.
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=_DAILY_STATEMENT_NAME)
    target_visual = "Posted Money Records"
    driver.wait_loaded(target_visual)

    picked_account, _picked_role, picked_day = find_account_day_with_data(cfg)

    driver.pick_filter("Account", [picked_account])
    # No-op on App2 (date picker not rendered there; dataset SQL
    # already returns all rows since date narrowing is QS-only).
    driver.set_date("Business Day", picked_day)
    driver.wait_loaded(target_visual)
    rows = driver.table_rows(target_visual)
    driver.screenshot()
    assert len(rows) > 0, (
        f"After Account={picked_account!r} + Business Day={picked_day!r}, "
        f"Posted Money Records should render ≥1 row. Got {len(rows)}. "
        f"The picked account is one the helper found with ≥1 row in "
        f"the deployed transactions matview — a zero-row outcome here "
        f"means the per-(account, day) filter on the matview broke."
    )


def test_dn5_posted_money_records_running_balance(
    l1_dashboard_driver: tuple["DashboardDriver", str], cfg: Config,
) -> None:
    """DN.5 (iv) — the Posted Money Records table renders a "Running
    Balance" column (DN.1/DN.2) on BOTH renderers, and its values are
    the cumulative running sum of the per-leg signed amount in display
    order.

    Parametrized over ``[qs, app2]`` via ``l1_dashboard_driver``; both
    renderers issue the same window-function dataset SQL, so a column-
    missing or value-mismatch outcome here is a real renderer wiring
    divergence. The unit-tier ``test_dn5_running_balance`` proves the SQL
    produces the right sequence against DuckDB; this proves the rendered
    table surfaces it correctly. NEEDS a qs_browser run to verify
    behaviorally (not exercised in the unit/audit run that authored it).
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=_DAILY_STATEMENT_NAME)
    target_visual = "Posted Money Records"
    driver.wait_loaded(target_visual)

    picked_account, _picked_role, picked_day = find_account_day_with_data(cfg)
    driver.pick_filter("Account", [picked_account])
    driver.set_date("Business Day", picked_day)
    driver.wait_loaded(target_visual)

    rows = driver.table_rows(target_visual)
    driver.screenshot()
    assert len(rows) > 0, (
        f"After Account={picked_account!r} + Business Day={picked_day!r}, "
        f"Posted Money Records should render ≥1 row; got {len(rows)}."
    )
    # Column present (header derives from `running_balance` →
    # `_smart_title` → "Running Balance").
    assert "Running Balance" in rows[0], (
        f"Posted Money Records must render a 'Running Balance' column "
        f"(DN.1/DN.2); headers seen: {sorted(rows[0])}"
    )

    # Arithmetic (DN-followup 2026-06-16): the running balance is
    # OPENING-ANCHORED — opening_balance + the cumulative signed
    # `amount_money` in posting order (the actual account balance after
    # each posting, landing on the day's posting-implied closing).
    # `Amount Money` is already SIGNED at source (Credit ≥ 0 / Debit ≤ 0
    # per the storage CHECK), so it's summed directly.
    #
    # Verified ORDER-INDEPENDENTLY: the table may render newest-first and
    # the `Posting` display format differs by renderer (QS vs App2), so we
    # don't sort on it. Instead use the chain identity — each posting's
    # PRE-balance (running_balance − its amount) equals the PRIOR
    # posting's running balance, or the opening for the first posting; the
    # last posting's running balance (= opening + Σ amounts, the closing)
    # is no posting's pre-balance. So as a multiset:
    #   {running_balance − amount : each row}  ==  {opening} ∪ {running_balance} − {closing}
    opening_str = driver.kpi_value("Opening Balance")
    assert opening_str is not None, (
        "Daily Statement should render an 'Opening Balance' KPI to anchor "
        "the running balance against."
    )
    opening = _parse_currency_kpi(opening_str)
    amounts = [_parse_currency_kpi(r["Amount Money"]) for r in rows]
    balances = [_parse_currency_kpi(r["Running Balance"]) for r in rows]
    # Sanity: the signed Amount Money agrees with the Direction column.
    for i, row in enumerate(rows):
        amt = amounts[i]
        if row["Amount Direction"] == CREDIT:
            assert amt >= 0, f"row {i}: Credit Amount Money should be ≥0, got {amt}"
        else:
            assert amt <= 0, f"row {i}: Debit Amount Money should be ≤0, got {amt}"
    closing = opening + sum(amounts, Decimal("0"))
    expected_pre = sorted([opening, *balances])
    assert closing in expected_pre, (
        f"day's posting-implied closing {closing} (opening + Σ amounts) "
        f"is not among the rendered running balances {sorted(balances)}"
    )
    expected_pre.remove(closing)
    actual_pre = sorted(b - a for b, a in zip(balances, amounts))
    assert actual_pre == expected_pre, (
        f"running-balance chain is not an opening-anchored cumulative:\n"
        f"  pre-balances (running_balance − amount): {actual_pre}\n"
        f"  expected (opening + prior running balances): {expected_pre}\n"
        f"  opening={opening}, closing={closing}"
    )


def test_bo_1_daily_statement_picks_reconcile_per_role(
    l1_dashboard_driver: tuple["DashboardDriver", str], cfg: Config,
) -> None:
    """BO.1 contract (v11.23.0 cold-read F1, triple-convergent) — for
    every ``account_role`` that has ≥1 row in
    ``<prefix>_current_daily_balances``: at least one of its accounts
    is in the Daily Statement Account dropdown, AND picking that
    account + a transactions-bearing day produces a non-blank KPI row
    (Opening + Closing both render parseable currency values).

    Pre-BO.1 the picker source (``DS_L1_ACCOUNTS``) UNIONed three
    matviews including ``current_transactions`` + ``l1_exceptions``,
    so the dropdown advertised owner-rollup IDs that had NO
    daily_balances row. Pre-BM the filter was a no-op so the FK gap
    was invisible; BM made the picker strict, so cardholder-rollup
    picks went blank (five empty KPI cards, 0 rows). The
    triple-convergent cold-read NEW top blocker.

    BO.1 split the picker source: Daily Statement uses
    ``DS_L1_DS_ACCOUNTS`` (sourced from
    ``<prefix>_current_daily_balances`` only); the 7 other L1 sheets
    keep using ``DS_L1_ACCOUNTS`` for the BL.3-wider universe their
    Pending-only / spine-planted accounts need.

    CQ.4.a — the Role cascade is dropped; this test no longer picks
    Role first. Per-role coverage is still valuable as a *seed-shape*
    enumeration (every role has at least one pickable account with a
    balance row) — the BO.1 regression mode is per-role-specific even
    though there's now no UI-level role filter.
    """
    driver, dashboard_arg = l1_dashboard_driver

    triples = find_one_account_day_per_role(cfg)
    assert triples, (
        "Daily Statement seed should expose ≥1 role with rows; "
        "deploy / refresh skipped or wrong prefix."
    )

    failures: list[str] = []
    for account_display, role, day in triples:
        driver.open(dashboard_arg, sheet=_DAILY_STATEMENT_NAME)
        driver.wait_loaded("Opening Balance")

        # CQ.4.a — the Account dropdown advertises every internal-scope
        # account directly; no Role pick step. The contract is still
        # "this role's accounts are pickable" — that requires the
        # account to be in the dropdown at all.
        #
        # DG.3 — the bare ``filter_options("Account")`` only mounts
        # MUI Autocomplete's virtualized window (~12 alphabetical
        # options), so accounts deep in the alphabet (ZBA*, Wire*)
        # silently fell out of the membership check even though they
        # WERE picker-reachable via the operator's typeahead flow.
        # Switch to ``typeahead_filter`` which types the account
        # display string + reads the server-narrowed result — same
        # shape an operator hits.
        account_opts = driver.typeahead_filter("Account", account_display)
        if account_display not in account_opts:
            failures.append(
                f"role {role!r}: account {account_display!r} not in "
                f"Account dropdown after typeahead. Got: "
                f"{sorted(account_opts)[:5]}... — picker source "
                f"narrowed too tightly or this role has no internal-"
                f"scope account with a balance row."
            )
            continue
        driver.pick_filter("Account", [account_display])
        driver.set_date("Business Day", day)
        driver.wait_loaded("Opening Balance")

        # Reconciliation contract — Opening + Closing both render
        # as parseable currency. ``parse_currency_kpi`` raises on
        # blank/missing/non-currency text, surfacing the "five blank
        # KPI cards" failure shape as a clear assertion-error message.
        try:
            opening = _parse_currency_kpi(driver.kpi_value("Opening Balance"))
            closing = _parse_currency_kpi(driver.kpi_value("Closing Stored"))
        except Exception as exc:  # noqa: BLE001
            failures.append(
                f"role {role!r}, account {account_display!r}, "
                f"day {day!r}: KPI parse failed — {exc!r}. This is "
                f"the cold-read F1 failure shape (five blank KPI "
                f"cards on a picked rollup-only account)."
            )
            continue
        # Belt + suspenders — both KPIs rendered SOMETHING numeric.
        # Don't pin specific values (per-role seeds vary); the
        # ``parse_currency_kpi`` succeeding IS the contract.
        del opening, closing

    driver.screenshot()
    assert not failures, (
        f"BO.1 per-role contract failed for {len(failures)} of "
        f"{len(triples)} roles:\n  " + "\n  ".join(failures)
    )


# AA.E — Account dropdown shows "name (id)" form ---------------------------


@pytest.mark.parametrize("sheet_name", [
    _DRIFT_NAME,
    _OVERDRAFT_NAME,
    _LIMIT_BREACH_NAME,
    _L1_EXCEPTIONS_NAME,
    _DAILY_STATEMENT_NAME,
    _TRANSACTIONS_NAME,
])
def test_account_dropdown_shows_display_form(
    l1_dashboard_driver: tuple["DashboardDriver", str], sheet_name: str,
) -> None:
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
    l1_dashboard_driver: tuple["DashboardDriver", str], cfg: Config,
) -> None:
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
    driver.open(dashboard_arg, sheet=_DAILY_STATEMENT_NAME)

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


# BG.2 — Daily Statement KPI honest gate -----------------------------------


_KPI_TO_COLUMN = {
    "Opening Balance": "opening_balance",
    "Debits (signed)": "total_debits",
    "Credits (signed)": "total_credits",
    "Closing Stored": "closing_balance_stored",
    # BO.6 (project_drift_vocabulary) renamed the Daily Statement
    # drift KPI from "Drift" → "Posting Drift" to disambiguate from
    # the L1 Drift sheet's leaf/parent drift KPIs. Test wasn't
    # synced — kpi_value("Drift") returned None on v11.25.0 CI
    # because that title no longer renders.
    "Posting Drift": "drift",
}


def _read_kpis_as_decimals(driver: "DashboardDriver") -> dict[str, Decimal]:
    return {
        title: _parse_currency_kpi(driver.kpi_value(title))
        for title in _KPI_TO_COLUMN
    }


def _expected_row_for(
    driver: "DashboardDriver",
    *,
    sql: str,
    dataset_parameters: list["DatasetParameter"],
    account_display: str,
    day_iso: str,
) -> dict[str, Decimal]:
    """Issue the same Daily Statement Summary SQL the visual would, with
    the picker-derived binds, via ``driver.query_db``. Returns each KPI
    title → Decimal (matview-projected dollar value).

    BG.2's ground truth: the matview is the source of fact; the KPI
    binding either matches it or doesn't. Identity assertions compare
    parsed-KPI ↔ this dict; the cold-read findings #1 / #3 surface as
    column-vs-rendered mismatches on the Drift / Opening Balance rows.
    """
    rows = driver.query_db(
        sql,
        binds={
            "param_pL1DsAccount": account_display,
            "param_pL1DsBalanceDate": day_iso,
        },
        dataset_parameters=dataset_parameters,
    )
    assert len(rows) == 1, (
        f"Daily Statement Summary SQL returned {len(rows)} rows for "
        f"({account_display!r}, {day_iso!r}); expected exactly 1. "
        f"Helper picked a (account, day) without a matview row, or the "
        f"matview is stale."
    )
    row = rows[0]
    return {
        title: Decimal(str(row[col]))
        for title, col in _KPI_TO_COLUMN.items()
    }


def test_bg2_daily_statement_kpis_match_summary_matview(
    l1_dashboard_driver: tuple["DashboardDriver", str], cfg: Config, l2: "L2Instance",
) -> None:
    """BG.2 — honest gate for the 5 Daily Statement KPIs.

    For the renderer that DOES bind the Business Day picker to the SQL
    (the QS leg via the analysis-side ``pL1DsBalanceDate`` param;
    Y.2.f/g + AR.2 narrowed it to strict day equality at the dataset
    layer), pick (account, day1), read each KPI, query the same SQL
    against the deployed DB through ``driver.query_db``, assert
    KPI[title] == row[column] for all 5 KPIs.

    Then pick day2 (different business day, SAME account): re-read +
    re-assert identity, AND assert the new KPI set differs from day1's
    (delta — proves the picker actually narrows). The v11.21.0
    cold-read's finding #2 (date picker non-functional → byte-identical
    KPIs across days) trips on the delta assertion when the wiring is
    broken; finding #1 (Drift KPI ≠ formula) and finding #3 (negative
    Opening Balance on a class-restricted role) trip on the identity
    assertion's per-column comparison.

    App2 leg: the single-value ``ParameterDateTimePicker`` for Business
    Day is skipped during App2's filter-spec derivation
    (``add_parameter_datetime_picker`` is App2-no-op today —
    ``tests/e2e/_drivers/base.py::set_date`` doc), so the dataset binds
    the param's default (the as_of anchor). The identity assertion
    still runs on App2 — it just compares against the anchor-day
    matview row instead of the picked-day row. The delta block runs
    only on the QS leg.
    """
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=_DAILY_STATEMENT_NAME)

    picked_account, _picked_role, day1, day2 = (
        find_two_days_for_same_account(cfg)
    )

    # Sanity: Account dropdown advertises the helper's pick (the AA.E.2
    # display-form binding contract); fail loud if not — the BG.2
    # KPI assertions below would otherwise read pre-pick state.
    options = driver.filter_options("Account")
    assert picked_account in options, (
        f"Helper picked {picked_account!r} but Account dropdown options "
        f"don't include it (first 5: {options[:5]}). AA.E.2 binding "
        f"likely out of sync with the dataset's WHERE clause."
    )
    driver.pick_filter("Account", [picked_account])

    sql, dataset_parameters = _summary_sql_and_params(cfg, l2)

    # Identity — day1. BG.7 strengthening 2026-05-25: App2 now drives
    # the flatpickr-single picker (was a no-op), so both renderers
    # narrow to the same picked day. effective_day1 == day1 on both.
    driver.set_date("Business Day", day1)
    driver.wait_loaded("Opening Balance")
    rendered_day1 = _read_kpis_as_decimals(driver)
    effective_day1 = day1
    expected_day1 = _expected_row_for(
        driver, sql=sql, dataset_parameters=dataset_parameters,
        account_display=picked_account, day_iso=effective_day1,
    )
    driver.screenshot()
    for title in _KPI_TO_COLUMN:
        assert rendered_day1[title] == expected_day1[title], (
            f"day1={effective_day1!r} KPI mismatch for {title!r}: "
            f"rendered={rendered_day1[title]} vs "
            f"summary-matview={expected_day1[title]}. The KPI is "
            f"binding a column whose value doesn't match what the "
            f"deployed matview holds. (For finding-shape disambiguation: "
            f"if THIS fails, the bug is in the KPI binding; if this "
            f"PASSES but the narrative-formula assertion below fails, "
            f"the bug is in the matview's `drift` column definition vs "
            f"the sheet's stated formula — cold-read finding #1.)"
        )

    # Narrative-formula invariant against INDEPENDENT ground truth
    # (cold-read finding #1, BH.0 post-share strengthening 2026-05-25).
    #
    # The Daily Statement sheet narrates
    #   Drift = Closing Stored − (Opening + signed_net_flow)
    # The matview's `drift` column MUST equal that formula. **Critical
    # subtlety**: we must NOT pull `net_flow` from the SAME matview row
    # we're checking — that's tautological (matview.drift was BUILT as
    # closing − (opening + matview.net_flow) so they'll agree even if
    # matview.net_flow itself computes something wrong, which is
    # exactly v11.21.0 finding #1's root cause: the matview's net_flow
    # formula at `schema.py:2502-2504` uses
    # `SUM(CASE WHEN Credit THEN amount_money ELSE -amount_money END)`
    # which assumed v5's unsigned amount; v6 made amount_money already
    # signed → the -amount_money for Debit rows over-flips → net_flow
    # = credits + abs(debits) = gross magnitude not signed net.
    #
    # Ground truth: SUM(amount_money) from the base transactions table
    # for the same (account_id, business_day) — bypasses the matview's
    # CASE-expression bug entirely. In v6 amount_money is signed
    # (Credit positive, Debit negative); plain SUM gives signed net.
    matview_account_id = str(_row_for(
        driver, sql=sql, dataset_parameters=dataset_parameters,
        account_display=picked_account, day_iso=effective_day1,
    )["account_id"])
    independent_net_flow = _independent_net_flow_for(
        driver, cfg=cfg, account_id=matview_account_id,
        day_iso=effective_day1,
    )
    expected_drift_from_narrative = (
        expected_day1["Closing Stored"]
        - (expected_day1["Opening Balance"] + independent_net_flow)
    )
    assert expected_day1["Posting Drift"] == expected_drift_from_narrative, (  # typing-smell: ignore[no-inline-production-constants]: Daily Statement KPI column dict key; coincidentally matches _DRIFT_NAME (sheet name) — column title is local to the KPI, not the sheet
        f"day1={effective_day1!r} account={picked_account!r}: matview's "
        f"`drift` column ({expected_day1['Drift']}) doesn't equal "  # typing-smell: ignore[no-inline-production-constants]: f-string interpolation re-reads the same column key as line 406 — same Daily Statement KPI; not coupled to _DRIFT_NAME
        f"closing − (opening + INDEPENDENT_signed_net_flow) = "
        f"{expected_day1['Closing Stored']} − "
        f"({expected_day1['Opening Balance']} + "
        f"{independent_net_flow}) = "
        f"{expected_drift_from_narrative}. v11.21.0 cold-read finding "
        f"#1: matview's `net_flow` formula at `schema.py:2502-2504` "
        f"uses `CASE WHEN Credit THEN amount_money ELSE -amount_money` "
        f"which assumed v5's unsigned amount; v6 made amount_money "
        f"already-signed so -amount_money for Debit rows over-flips → "
        f"matview's net_flow becomes gross magnitude (credits + "
        f"abs(debits)), not signed net. Fix: drop the CASE → "
        f"`SUM(tx.amount_money) AS net_flow`. (If matview's net_flow "
        f"differs from independent SUM, the matview's drift is also "
        f"wrong — fixing net_flow fixes drift by construction.)"
    )

    # Delta runs on BOTH legs (BG.7 strengthening 2026-05-25 per user
    # + feedback_build_verbs_not_skip): App2's `set_date` now drives
    # the rendered flatpickr-single widget (see _drivers/app2.py).
    # Both renderers bind the picked date through the dataset SQL
    # pushdown (`pL1DsBalanceDate` → `<<$pL1DsBalanceDate>>` on QS,
    # `:param_pL1DsBalanceDate` on App2), so day1 ≠ day2 must produce
    # distinct KPI sets on either leg. The cold-read's finding #2
    # ("byte-identical KPIs regardless of picked day") trips here on
    # both renderers when the wire is broken.
    driver.set_date("Business Day", day2)
    driver.wait_loaded("Opening Balance")
    rendered_day2 = _read_kpis_as_decimals(driver)
    expected_day2 = _expected_row_for(
        driver, sql=sql, dataset_parameters=dataset_parameters,
        account_display=picked_account, day_iso=day2,
    )
    driver.screenshot()
    for title in _KPI_TO_COLUMN:
        assert rendered_day2[title] == expected_day2[title], (
            f"day2={day2!r} KPI mismatch for {title!r}: "
            f"rendered={rendered_day2[title]} vs "
            f"summary-matview={expected_day2[title]}."
        )
    # The narrative invariant: every KPI MAY equal (e.g. zero rows on
    # both days), but the rendered SET must change in at least ONE
    # KPI between day1 and day2. Byte-identical KPIs across two
    # known-distinct-data days is the v11.21.0 cold-read finding #2
    # signature.
    assert rendered_day1 != rendered_day2, (
        f"Business Day picker is a no-op on this leg: day1={day1!r} "
        f"and day2={day2!r} produced byte-identical KPI sets "
        f"({rendered_day1!r}). v11.21.0 cold-read finding #2 — the "
        f"picker's value isn't reaching the dataset's WHERE clause. "
        f"Drill into the flatpickr → form-refresh chain (BH.2 closed "
        f"this class via App2's set_date driver verb); the SQL "
        f"pushdown wire is intact (this same SQL + binds returns "
        f"distinct values for the two days)."
    )


def _row_for(
    driver: "DashboardDriver",
    *,
    sql: str,
    dataset_parameters: list["DatasetParameter"],
    account_display: str,
    day_iso: str,
) -> dict[str, Any]:
    """Pull the matview row for the picked (account, day). Used to
    extract the matview's `account_id` (the dataset filters on
    `(name || ' (' || id || ')') = pL1DsAccount`, so the row carries
    the raw id we need for the independent ground-truth query)."""
    rows = driver.query_db(
        sql,
        binds={
            "param_pL1DsAccount": account_display,
            "param_pL1DsBalanceDate": day_iso,
        },
        dataset_parameters=dataset_parameters,
    )
    assert len(rows) == 1
    return rows[0]


def _independent_net_flow_for(
    driver: "DashboardDriver", *, cfg: Config, account_id: str, day_iso: str,
) -> Decimal:
    """Compute the day's signed net flow DIRECTLY from
    ``<prefix>_current_transactions``, bypassing the
    `daily_statement_summary` matview's `net_flow` column entirely.

    Why bypass: the matview's `net_flow` formula
    (`schema.py:2502-2504`) carries a v5→v6 sign-convention regression
    (`CASE WHEN Credit THEN amount_money ELSE -amount_money END`
    over-negates Debit rows because v6's `amount_money` is already
    signed). Pulling `net_flow` from the matview to validate the
    narrative formula `drift = closing − (opening + signed_net_flow)`
    is tautological — the same wrong formula appears on both sides
    and the assertion silently passes. Pulling the ground truth from
    the base transactions table with a plain `SUM(amount_money)`
    gives the true signed net (in v6 amount_money is signed: Credit
    positive, Debit negative; SUM is signed net by construction).

    Day boundary: posting ranges from start-of-day to start-of-next-
    day. `business_day_start` truncation in the matview matches this
    half-open interval. ``status != 'Failed'`` mirrors the matview's
    today_flows CTE filter.
    """
    from datetime import date, timedelta

    prefix = cfg.db.table_prefix
    day = date.fromisoformat(day_iso)
    next_day = day + timedelta(days=1)
    sql = (
        f"SELECT COALESCE(SUM(amount_money), 0) AS net_cents "
        f"FROM {prefix}_current_transactions "
        f"WHERE account_id = :account_id "
        f"  AND posting >= :day_start "
        f"  AND posting < :day_end "
        f"  AND status <> 'Failed'"
    )
    rows = driver.query_db(
        sql,
        binds={
            "account_id": account_id,
            "day_start": day.isoformat() + " 00:00:00",
            "day_end": next_day.isoformat() + " 00:00:00",
        },
    )
    assert len(rows) == 1
    return Decimal(str(rows[0]["net_cents"])) / Decimal("100")


def _summary_default_day(dataset_parameters: list["DatasetParameter"]) -> str:  # pyright: ignore[reportUnusedFunction]: helper for App2 leg defaults, kept for symmetry with cross-app callers
    """Return the YYYY-MM-DD default static value declared on the
    ``pL1DsBalanceDate`` dataset parameter. App2's leg binds this when
    no URL param is supplied (since the date picker isn't rendered)."""
    for dp in dataset_parameters:
        dt = dp.DateTimeDatasetParameter
        if dt is None or str(dt.Name) != "pL1DsBalanceDate":
            continue
        defaults = dt.DefaultValues
        if defaults is None or not defaults.StaticValues:
            raise RuntimeError(
                "pL1DsBalanceDate DatasetParameter has no static default; "
                "App2 leg can't compute the bound day."
            )
        raw = str(defaults.StaticValues[0])
        # QS DateTime defaults serialize as ISO timestamps; take the
        # leading day-shape.
        return raw[:10]
    raise RuntimeError(
        "pL1DsBalanceDate DatasetParameter not found on the summary "
        "dataset; production builder shape changed."
    )
