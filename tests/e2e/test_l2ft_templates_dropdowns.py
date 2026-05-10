"""Browser test: L2FT Transfer Templates dropdowns narrow the table.

X.1.g regression guard. Pre-X.1.g the Template / Completion dropdowns
were ``FilterDropdown(CategoryFilter(values=[], FILTER_ALL_VALUES))``,
which forced QS to lazy-fetch the column's distinct values from the
``tenK-sample-values-V2`` endpoint at first render — that endpoint
404s on cold per-CI-run dashboards. The X.1.g rewrite swapped each
to a parameter-bound CategoryFilter sourced from a StaticValues
ParameterDropdown so option lists are known at deploy time and no
runtime fetch happens.

Templates is the ``cross_dataset="ALL_DATASETS"`` case — one
parameter narrows BOTH the Sankey (built from tt-legs) and the
Template Instances table (built from tt-instances). The test asserts
the Table doesn't go empty (Sankey has no row-count primitive to
assert against; the Table is the more sensitive instrument).

Test strategy: data-agnostic per the no-hardcoded-data rule.
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


@pytest.fixture(autouse=True)
def _require_templates(l2ft_l2_instance) -> None:
    """Skip when the deployed L2 declares no transfer templates.

    Same rationale as ``test_l2ft_chains_dropdowns._require_chains``: the
    "Template dropdown narrow doesn't empty" guard exercises the deployed
    Template Instances matview rows; a no-templates L2 is a valid config
    (a fuzz seed without one) with nothing to exercise, and the Transfer
    Templates sheet rendering clean for an empty L2 is covered by the
    render tests. Without this skip the test instead times out in
    ``_navigate_to_templates`` waiting on table cells that never appear.
    """
    from quicksight_gen.apps.l2_flow_tracing.datasets import (
        declared_template_names,
    )
    if not declared_template_names(l2ft_l2_instance):
        pytest.skip(
            "deployed L2 declares no transfer templates — the Template "
            "narrow-doesn't-empty guard has nothing to exercise (Transfer "
            "Templates sheet rendering clean for an empty L2 is covered by "
            "the render tests)."
        )


@pytest.fixture
def embed_url(region, account_id, l2ft_dashboard_id) -> str:
    return generate_dashboard_embed_url(
        aws_account_id=account_id,
        aws_region=region,
        dashboard_id=l2ft_dashboard_id,
    )


# Tall viewport so both Sankey + Template Instances table land above
# the fold during the walk.
TALL_VIEWPORT = (1600, 4000)


def _navigate_to_templates(page, page_timeout: int) -> None:
    """Open dashboard, switch to Transfer Templates, wait for the
    Template Instances table to render its first cells."""
    wait_for_dashboard_loaded(page, timeout_ms=page_timeout)
    click_sheet_tab(page, "Transfer Templates", timeout_ms=page_timeout)
    # Templates sheet has 2 visuals — the Sankey + the Table. Asserting
    # min_count >= 2 confirms both rendered at least the chrome.
    wait_for_visuals_present(page, min_count=2, timeout_ms=page_timeout)
    wait_for_table_cells_present(page, timeout_ms=page_timeout)


def _pick_each_option_and_assert_table_nonempty(
    page, dropdown_title: str, page_timeout: int,
) -> None:
    """For each option in a Templates ParameterDropdown, pick only that
    one value and assert the Template Instances table still has > 0
    rows.

    Stronger guarantee than picking only the first option: the
    dropdown's option universe comes from the L2 YAML walk (Template)
    or a hardcoded enum (Completion), so we expect every advertised
    value to actually have at least one matching matview row. Catches
    three failure modes:

    1. **Stale enum** — a hardcoded value
       (``tt_completion_status_values()`` returns a status no template
       firing produces) silently advertises a dead-end pick.
    2. **New YAML value** — a TransferTemplate added to the L2 YAML
       without a matching seed plant produces an empty narrowing.
    3. **Data seeding bug** — a declared template the seed *should*
       fire but doesn't, e.g. a baseline / plant misconfiguration
       silently dropping rows for that template.
    """
    options = read_dropdown_options(
        page, dropdown_title, timeout_ms=page_timeout,
    )
    if not options:
        pytest.skip(
            f"{dropdown_title!r} dropdown empty on the deployed L2 — "
            f"the X.1.g pick-and-narrow test has nothing to exercise."
        )

    before = count_table_rows(page, "Template Instances")
    if before <= 0:
        pytest.skip(
            "Template Instances table starts empty — Templates sheet "
            "has no matview rows for the deployed L2 to exercise the "
            "dropdown narrowing against."
        )

    failures: list[str] = []
    for option in options:
        set_multi_select_values(
            page, dropdown_title, [option], timeout_ms=page_timeout,
        )
        try:
            after = wait_for_table_nonzero(
                page, "Template Instances", timeout_ms=10_000,
            )
        except Exception:
            after = count_table_rows(page, "Template Instances")
        if after <= 0:
            failures.append(option)
            screenshot(
                page,
                f"templates_pick_{dropdown_title.lower().replace(' ', '_')}_"
                f"{option.lower().replace(' ', '_')}_empty",
                subdir="l2_flow_tracing",
            )

    assert not failures, (
        f"Template Instances table went empty after picking these "
        f"{dropdown_title!r} values: {failures}. Either the dropdown "
        f"advertises an option with no matching seed data (stale enum / "
        f"new YAML value missing plants / data seeding bug) or X.1.g "
        f"param-bound CategoryFilter narrowing across ALL_DATASETS "
        f"scope regressed."
    )


def test_template_dropdown_narrows_does_not_empty(embed_url, page_timeout):
    """Each declared Template name must leave the Template Instances
    table with > 0 rows when picked alone."""
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        _navigate_to_templates(page, page_timeout)
        _pick_each_option_and_assert_table_nonempty(
            page, "Template", page_timeout,
        )


def test_completion_dropdown_narrows_does_not_empty(embed_url, page_timeout):
    """Each Completion status (Complete / Imbalanced / Orphaned) must
    leave the Template Instances table with > 0 rows when picked
    alone."""
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        _navigate_to_templates(page, page_timeout)
        _pick_each_option_and_assert_table_nonempty(
            page, "Completion", page_timeout,
        )
