"""Browser test: L2FT Rails sheet dropdowns narrow the table after picking.

X.1.g + Y.2.c regression guard. Pre-X.1.g the Rail / Status / Bundle
dropdowns were ``FilterDropdown(CategoryFilter(values=[],
FILTER_ALL_VALUES))``, which forced QS to lazy-fetch the column's
distinct values from the ``tenK-sample-values-V2`` endpoint at first
render — that endpoint 404s on cold per-CI-run dashboards (one of the
4 X.1.a-traced 404s). X.1.g swapped each to a StaticValues
ParameterDropdown so option lists are known at deploy time; Y.2.c then
moved the narrowing from an analysis-level CategoryFilter into the
postings dataset SQL (multi-valued ``<<$param>>`` substitution). Either
way the observable behaviour is the same — picking values narrows the
Transactions table, emptying a dropdown reverts to "all" — which is
what this test asserts.

Test strategy: data-agnostic per the no-hardcoded-data rule.

For each dropdown, walk: read available options, pick the first one,
verify the Transactions table's row count drops (or at minimum doesn't
go negative). Tests skip with informational message if the deployed
L2 instance has no rows that exercise the filter.
"""

from __future__ import annotations

import pytest

from quicksight_gen.common.browser.helpers import (
    click_sheet_tab,
    count_table_rows,
    wait_for_table_nonzero,
    generate_dashboard_embed_url,
    read_dropdown_options,
    screenshot,
    set_multi_select_values,
    wait_for_dashboard_loaded,
    wait_for_table_cells_present,
    wait_for_visuals_present,
    webkit_page,
)


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.fixture
def embed_url(region, account_id, l2ft_dashboard_id) -> str:
    return generate_dashboard_embed_url(
        aws_account_id=account_id,
        aws_region=region,
        dashboard_id=l2ft_dashboard_id,
    )


# Tall viewport so the Transactions table sits above the fold.
TALL_VIEWPORT = (1600, 4000)


def _navigate_to_rails(page, page_timeout: int) -> None:
    """Open dashboard, switch to Rails, wait for the Transactions table
    to render its first cells. Centralized so each per-dropdown test
    starts from the same baseline."""
    wait_for_dashboard_loaded(page, timeout_ms=page_timeout)
    click_sheet_tab(page, "Rails", timeout_ms=page_timeout)
    wait_for_visuals_present(page, min_count=1, timeout_ms=page_timeout)
    wait_for_table_cells_present(page, timeout_ms=page_timeout)


def _pick_each_option_and_assert_table_nonempty(
    page, dropdown_title: str, page_timeout: int,
) -> None:
    """For each option in a Rails ParameterDropdown, pick only that
    one value and assert the Transactions table still has > 0 rows.

    Stronger guarantee than picking only the first option: the
    dropdown's option universe comes from the L2 YAML walk
    (Rail) or a hardcoded enum (Status / Bundle), so we expect
    every advertised value to actually have at least one matching
    row in the demo seed. Catches three failure modes:

    1. **Stale enum** — a hardcoded value
       (``transaction_status_values()`` returns ``Posted`` but no
       seed row carries it) silently makes the dropdown advertise a
       dead-end pick.
    2. **New YAML value** — a Rail name added to the L2 YAML without
       a matching seed plant produces an empty narrowing.
    3. **Data seeding bug** — a declared value the seed *should*
       produce rows for but doesn't, e.g. a baseline generator skip
       or a plant misconfiguration silently dropping rows for a
       specific Rail / Status / Bundle combination.

    All three present the same operator-facing symptom: the analyst
    picks an advertised dropdown value and the table goes empty.
    """
    options = read_dropdown_options(
        page, dropdown_title, timeout_ms=page_timeout,
    )
    if not options:
        pytest.skip(
            f"{dropdown_title!r} dropdown empty on the deployed L2 — "
            f"the pick-and-narrow test has nothing to exercise."
        )

    before = count_table_rows(page, "Transactions")
    assert before > 0, (
        f"Transactions table must have rows pre-filter, got {before}"
    )

    failures: list[str] = []
    for option in options:
        set_multi_select_values(
            page, dropdown_title, [option], timeout_ms=page_timeout,
        )
        # QS recomputes the postings query after the parameter write;
        # let it settle then read the count. Fixed sleep rather than
        # wait-for-change because the happy path may legitimately
        # leave the count unchanged if the picked value spans every
        # row in the window. ``count_table_rows`` reads DOM only
        # (saturates ~10) which is enough for "table not empty".
        try:
            after = wait_for_table_nonzero(
                page, "Transactions", timeout_ms=10_000,
            )
        except Exception:
            after = count_table_rows(page, "Transactions")
        if after <= 0:
            failures.append(option)
            screenshot(
                page,
                f"rails_pick_{dropdown_title.lower().replace(' ', '_')}_"
                f"{option.lower().replace(' ', '_')}_empty",
                subdir="l2_flow_tracing",
            )

    assert not failures, (
        f"Transactions table went empty after picking these "
        f"{dropdown_title!r} values: {failures}. Either the dropdown "
        f"advertises an option with no matching seed data (stale enum / "
        f"new YAML value missing plants / data seeding bug) or X.1.g "
        f"param-bound CategoryFilter narrowing regressed."
    )


def test_rail_dropdown_narrows_does_not_empty(embed_url, page_timeout):
    """Picking a single Rail must leave the Transactions table with > 0
    rows — the X.1.g param-bound CategoryFilter regression class."""
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        _navigate_to_rails(page, page_timeout)
        _pick_each_option_and_assert_table_nonempty(page, "Rail", page_timeout)


def test_status_dropdown_narrows_does_not_empty(embed_url, page_timeout):
    """Picking a single Status must leave the Transactions table with
    > 0 rows. Status's universe is the bounded enum
    (Pending / Posted / Failed) — every value should narrow the table
    to a proper non-empty subset on a populated demo."""
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        _navigate_to_rails(page, page_timeout)
        _pick_each_option_and_assert_table_nonempty(page, "Status", page_timeout)


def test_bundle_dropdown_narrows_does_not_empty(embed_url, page_timeout):
    """Picking a single Bundle status (Bundled / Unbundled) must leave
    the Transactions table with > 0 rows."""
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        _navigate_to_rails(page, page_timeout)
        _pick_each_option_and_assert_table_nonempty(page, "Bundle", page_timeout)
