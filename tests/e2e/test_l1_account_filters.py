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

from decimal import Decimal

import pytest

from recon_gen.apps.l1_dashboard.datasets import (
    build_daily_statement_summary_dataset,
)
from tests.e2e._daily_statement_pick import (
    find_account_day_with_data,
    find_two_days_for_same_account,
)
from tests.e2e._kpi_parse import parse_currency_kpi as _parse_currency_kpi


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _summary_sql_and_params(cfg, l2):  # type: ignore[no-untyped-def]: cfg/l2 are runtime fixture values — annotating would force imports here
    """Lift the Daily Statement Summary dataset's SQL + DatasetParameters
    by calling the production builder. BG.2's honest gate compares
    rendered KPI values to the SAME SQL the dashboard issues."""
    ds = build_daily_statement_summary_dataset(cfg, l2)
    sql_str = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
    return sql_str, list(ds.DatasetParameters)


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


# BG.2 — Daily Statement KPI honest gate -----------------------------------


_KPI_TO_COLUMN = {
    "Opening Balance": "opening_balance",
    "Debits": "total_debits",
    "Credits": "total_credits",
    "Closing Stored": "closing_balance_stored",
    "Drift": "drift",
}


def _read_kpis_as_decimals(driver) -> dict[str, Decimal]:  # type: ignore[no-untyped-def]: driver is a DashboardDriver — annotating would force the import at module scope
    return {
        title: _parse_currency_kpi(driver.kpi_value(title))
        for title in _KPI_TO_COLUMN
    }


def _expected_row_for(
    driver, *, sql: str, dataset_parameters, account_display: str, day_iso: str,  # type: ignore[no-untyped-def]: driver/dataset_parameters are runtime values — annotating cascades imports
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
    l1_dashboard_driver, cfg, l2,
):
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
    driver.open(dashboard_arg, sheet="Daily Statement")

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
    assert expected_day1["Drift"] == expected_drift_from_narrative, (
        f"day1={effective_day1!r} account={picked_account!r}: matview's "
        f"`drift` column ({expected_day1['Drift']}) doesn't equal "
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
        f"Drill into the flatpickr → hidden-input → form-refresh "
        f"chain; the SQL pushdown wire is intact (this same SQL + "
        f"binds returns distinct values for the two days)."
    )


def _row_for(
    driver, *, sql, dataset_parameters, account_display, day_iso,  # type: ignore[no-untyped-def]: driver / dataset_parameters are runtime values — annotating cascades imports
):
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


def _independent_net_flow_for(driver, *, cfg, account_id, day_iso) -> Decimal:  # type: ignore[no-untyped-def]: driver / cfg are runtime fixture values
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

    prefix = cfg.db_table_prefix
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


def _summary_default_day(dataset_parameters) -> str:  # type: ignore[no-untyped-def]: list of DatasetParameter — annotating would import the wrapper here
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
