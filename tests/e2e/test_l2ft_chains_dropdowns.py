"""Browser test: L2FT Chains sheet dropdowns narrow the table after picking.

X.1.g regression guard. Pre-X.1.g the Chain / Completion dropdowns
were ``FilterDropdown(CategoryFilter(values=[], FILTER_ALL_VALUES))``,
which forced QS to lazy-fetch the column's distinct values from the
``tenK-sample-values-V2`` endpoint at first render — that endpoint
404s on cold per-CI-run dashboards. The X.1.g rewrite swapped each
to a parameter-bound CategoryFilter sourced from a StaticValues
ParameterDropdown so option lists are known at deploy time and no
runtime fetch happens.

Test strategy: data-agnostic per the no-hardcoded-data rule. Pick the
first selectable value from each dropdown; assert the Chain Instances
table doesn't go empty.
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
def _require_chains(l2ft_l2_instance) -> None:
    # Fast-exit when the deployed L2 declares zero chains (spec_example) —
    # see `conftest.require_l2ft_feature` for the rationale + the
    # "declared ≠ instantiated" caveat the `before <= 0` skip below covers.
    from tests.e2e.conftest import require_l2ft_feature
    require_l2ft_feature(l2ft_l2_instance, "chains")


@pytest.fixture
def embed_url(region, account_id, l2ft_dashboard_id) -> str:
    return generate_dashboard_embed_url(
        aws_account_id=account_id,
        aws_region=region,
        dashboard_id=l2ft_dashboard_id,
    )


# Tall viewport so the Chain Instances table sits above the fold.
TALL_VIEWPORT = (1600, 4000)


def _navigate_to_chains(page, page_timeout: int) -> None:
    """Open dashboard, switch to Chains, wait for the Chain Instances
    table to render its first cells (best-effort — see below)."""
    wait_for_dashboard_loaded(page, timeout_ms=page_timeout)
    click_sheet_tab(page, "Chains", timeout_ms=page_timeout)
    wait_for_visuals_present(page, min_count=1, timeout_ms=page_timeout)
    # The Chain Instances matview is empty when the deployed L2 declares no
    # chains (spec_example) OR declares them but the seed fires no instances
    # (a non-zero `declared_chain_parents` is necessary but not sufficient).
    # `sn-table-cell-0-0` then never appears — don't burn `page_timeout` on
    # it; bound the wait and let the caller's `before <= 0` skip handle the
    # empty case. A populated table renders cells in ~1-3s, so 12s is ample.
    try:
        wait_for_table_cells_present(page, timeout_ms=12_000)
    except Exception:  # empty matview → caller skips on before<=0; no cells to wait for
        pass


def _pick_each_option_and_assert_table_nonempty(
    page, dropdown_title: str, page_timeout: int,
) -> None:
    """For each option in a Chains ParameterDropdown, pick only that
    one value and assert the Chain Instances table still has > 0 rows.

    Stronger guarantee than picking only the first option: the
    dropdown's option universe comes from the L2 YAML walk (Chain) or
    a hardcoded enum (Completion), so we expect every advertised value
    to actually have at least one matching matview row. Catches three
    failure modes:

    1. **Stale enum** — a hardcoded value
       (``chain_completion_status_values()`` returns a status no
       chain firing produces) silently advertises a dead-end pick.
    2. **New YAML value** — a chain parent added to the L2 YAML
       without a matching seed plant produces an empty narrowing.
    3. **Data seeding bug** — a declared chain the seed *should*
       fire but doesn't, e.g. a baseline / plant misconfiguration
       silently dropping rows for that parent.
    """
    options = read_dropdown_options(
        page, dropdown_title, timeout_ms=page_timeout,
    )
    if not options:
        pytest.skip(
            f"{dropdown_title!r} dropdown empty on the deployed L2 — "
            f"the X.1.g pick-and-narrow test has nothing to exercise."
        )

    before = count_table_rows(page, "Chain Instances")
    if before <= 0:
        pytest.skip(
            "Chain Instances table starts empty — Chains sheet has no "
            "matview rows for the deployed L2 to exercise the dropdown "
            "narrowing against."
        )

    failures: list[str] = []
    for option in options:
        set_multi_select_values(
            page, dropdown_title, [option], timeout_ms=page_timeout,
        )
        # Poll until the table is non-empty (or timeout). Replaces a
        # blind 5s sleep — fast on the happy path, fails fast on a
        # real "narrowing emptied the table" regression.
        try:
            after = wait_for_table_nonzero(
                page, "Chain Instances", timeout_ms=10_000,
            )
        except Exception:
            after = count_table_rows(page, "Chain Instances")
        if after <= 0:
            failures.append(option)
            screenshot(
                page,
                f"chains_pick_{dropdown_title.lower().replace(' ', '_')}_"
                f"{option.lower().replace(' ', '_')}_empty",
                subdir="l2_flow_tracing",
            )

    assert not failures, (
        f"Chain Instances table went empty after picking these "
        f"{dropdown_title!r} values: {failures}. Either the dropdown "
        f"advertises an option with no matching seed data (stale enum / "
        f"new YAML value missing plants / data seeding bug) or X.1.g "
        f"param-bound CategoryFilter narrowing regressed."
    )


def test_chain_dropdown_narrows_does_not_empty(embed_url, page_timeout):
    """Each declared Chain parent must leave the Chain Instances table
    with > 0 rows when picked alone."""
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        _navigate_to_chains(page, page_timeout)
        _pick_each_option_and_assert_table_nonempty(page, "Chain", page_timeout)


def test_completion_dropdown_narrows_does_not_empty(embed_url, page_timeout):
    """Each Completion status (Completed / Incomplete / No Required
    Children) must leave the Chain Instances table with > 0 rows when
    picked alone."""
    with webkit_page(headless=True, viewport=TALL_VIEWPORT) as page:
        page.goto(embed_url, timeout=page_timeout)
        _navigate_to_chains(page, page_timeout)
        _pick_each_option_and_assert_table_nonempty(
            page, "Completion", page_timeout,
        )
