"""Browser test: L2FT Transfer Templates dropdowns narrow the table.

X.1.g regression guard. Templates is the ``cross_dataset="ALL_DATASETS"``
case — one parameter narrows BOTH the Sankey (built from tt-legs) and
the Template Instances table (built from tt-instances); the table is the
more sensitive instrument (the Sankey has no row-count primitive), so
that's what ``walk_dropdown`` asserts on. See ``_l2ft_dropdown_walk``
for the shared mechanics. Parametrized over ``[qs, app2]`` (X.2.u.3) via
``l2ft_dashboard_driver``. (spec_example declares one template but the
seed fires no instances, so ``walk_dropdown``'s "table started empty →
skip" is the real guard there.)
"""

from __future__ import annotations

import pytest

from ._l2ft_dropdown_walk import walk_dropdown


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.fixture(autouse=True)
def _require_templates(l2ft_l2_instance) -> None:
    # Fast-exit when the deployed L2 declares zero transfer templates —
    # see `conftest.require_l2ft_feature`. spec_example declares one but the
    # seed fires no instances, so `walk_dropdown`'s "table started empty →
    # skip" is the real guard for that case.
    from tests.e2e.conftest import require_l2ft_feature
    require_l2ft_feature(l2ft_l2_instance, "templates")


@pytest.mark.parametrize("dropdown_title", ["Template", "Completion"])
def test_templates_dropdown_narrows_does_not_empty(
    l2ft_dashboard_driver, dropdown_title,
) -> None:
    """Each declared Template name — and each Completion status
    (Complete / Imbalanced / Orphaned) — must leave the Template
    Instances table with > 0 rows when picked alone."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet="Transfer Templates")
    walk_dropdown(
        driver,
        sheet_label="Transfer Templates",
        dropdown_title=dropdown_title,
        table_title="Template Instances",
        # "Completion" is a universal-outcome enum (Complete / Imbalanced /
        # Orphaned) — which occur depends on the template's structure (a
        # SingleLegRail-first template only ever fires 'Imbalanced'); require
        # ≥1, not all. "Template" is an L2-declared name → strict.
        require_all_advertised=(dropdown_title != "Completion"),
    )
