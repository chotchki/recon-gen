"""Browser test: L2FT metadata cascade narrows but does not empty the table.

Regression guard for v8.6.5. Pre-v8.6.5 the Metadata Value dropdown
on Rails / Chains / Transfer Templates carried a ``cascade_source``
binding that, combined with ``LinkedValues`` + ``MULTI_SELECT``,
killed parameter write-back: picking a value left ``pMetaValue`` at
its placeholder sentinel, so the postings WHERE filtered to zero
rows and the Transactions table went empty.

Test strategy: data-agnostic per the no-hardcoded-data rule. Read
whatever Metadata Key options the deployed L2 instance declares,
pick the first one, read the resulting Metadata Value options, pick
the first, and assert the Transactions table still has > 0 rows.
"""

from __future__ import annotations

import time

import pytest

from quicksight_gen.common.browser.helpers import (
    click_sheet_tab,
    count_table_total_rows,
    generate_dashboard_embed_url,
    read_dropdown_options,
    screenshot,
    set_dropdown_value,
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


# Tall viewport so the Transactions table sits above the fold during the walk.
TALL_VIEWPORT = (1600, 4000)


# Re-skipped after X.1.b experiment cycle. Findings:
#   - ``Sample values not found`` JS error fires 4× on cold per-CI-run
#     dashboards. Network trace (X.1.a v3) showed all 4 are 404s on
#     ``tenK-sample-values-V2`` for the visual's filter dropdowns +
#     ``GetThemeForDashboard``.
#   - Replacing the Metadata Value LinkedValues dropdown with a
#     ParameterTextField (this commit ships that change) eliminated
#     1 of the 4. Visual is still empty.
#   - The remaining 3 sample-values 404s are the Rail / Status /
#     Bundle CategoryFilter dropdowns. Static-encoding via
#     ``add_filter_dropdown(selectable_values=...)`` was attempted +
#     reverted: AWS rejects the combo. Proper fix is the bigger
#     ParameterDropdown restructure queued under X.1.g.
#   - The 4th 404 (theme) needs separate investigation — X.1.f.
@pytest.mark.skip(
    reason=(
        "L2FT cascade test reads 0 rows in CI — root cause partially "
        "diagnosed (multiple QS lazy 'tenK-sample-values-V2' 404s on "
        "cold per-CI-run dashboards, 1 of 4 eliminated). Full fix "
        "queued under PLAN X.1.f (theme 404) + X.1.g "
        "(CategoryFilter → ParameterDropdown restructure)."
    ),
)
def test_metadata_value_pick_does_not_empty_transactions_table(
    embed_url, page_timeout,
):
    """Picking a (Key, Value) pair must leave the Transactions table
    with > 0 rows — the v8.6.5 cascade-source regression class.

    The cascade-removal made the Value dropdown's options wider than
    strictly relevant for the picked Key (every declared metadata
    value across all keys appears, not just values present for the
    chosen key). To make the value-pick deterministic, the test picks
    a Key that has at least one matching Value in the dropdown — if
    no such pair exists for the deployed L2, it skips with an
    informational message rather than failing.
    """
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        wait_for_dashboard_loaded(page, timeout_ms=page_timeout)
        click_sheet_tab(page, "Rails", timeout_ms=page_timeout)
        # Rails has exactly one analysis_visual (the Transactions table) —
        # the dropdowns and date pickers are sheet filter controls, not
        # visual containers. Asserting min_count >= 1 is the correct
        # "Rails sheet has rendered its visual" gate.
        wait_for_visuals_present(
            page, min_count=1, timeout_ms=page_timeout,
        )
        wait_for_table_cells_present(page, timeout_ms=page_timeout)

        key_options = read_dropdown_options(
            page, "Metadata Key", timeout_ms=page_timeout,
        )
        if not key_options:
            pytest.skip(
                "Deployed L2 instance declares no metadata keys — "
                "the cascade test has nothing to exercise."
            )

        before = count_table_total_rows(
            page, "Transactions", timeout_ms=page_timeout,
        )
        assert before > 0, (
            f"Transactions table must have rows pre-filter, got {before}"
        )

        chosen_key = key_options[0]
        set_dropdown_value(
            page, "Metadata Key", chosen_key, timeout_ms=page_timeout,
        )

        # Cascade query refreshes the Value dropdown options based on
        # pMetaKey; give QS a beat to re-fetch before reading options.
        time.sleep(2)

        value_options = read_dropdown_options(
            page, "Metadata Value", timeout_ms=page_timeout,
        )
        if not value_options:
            pytest.skip(
                f"Metadata Value dropdown empty after picking key "
                f"{chosen_key!r} — no values declared for this key in "
                f"the deployed L2."
            )

        chosen_value = value_options[0]
        set_multi_select_values(
            page,
            "Metadata Value",
            [chosen_value],
            timeout_ms=page_timeout,
        )

        # QS recomputes the postings query after the parameter write;
        # let it settle then read the count. Using a fixed sleep rather
        # than wait-for-change because the regression we're guarding
        # (table → 0 rows) IS a count change, but the happy path may
        # leave the count unchanged if the picked value happens to
        # appear on every leg in the window — wait-for-change would
        # then hit a misleading TimeoutError on the passing case.
        time.sleep(5)
        after = count_table_total_rows(
            page, "Transactions", timeout_ms=page_timeout,
        )
        assert after > 0, (
            f"Transactions table emptied after picking "
            f"({chosen_key}={chosen_value}); regression of v8.6.5 "
            f"cascade-source write-back bug. before={before}, after={after}"
        )
        screenshot(
            page, "metadata_cascade_value_pick", subdir="l2_flow_tracing",
        )
