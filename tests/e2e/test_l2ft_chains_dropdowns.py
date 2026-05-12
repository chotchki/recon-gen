"""Browser test: L2FT Chains sheet dropdowns narrow the Chain Instances table.

X.1.g regression guard — see ``_l2ft_dropdown_walk`` for the shared
mechanics and the failure modes. Parametrized over ``[qs, app2]``
(X.2.u.3) via ``l2ft_dashboard_driver``. (spec_example declares zero
chains, so the ``_require_chains`` autouse fixture skips both legs there;
``walk_dropdown``'s "table started empty → skip" covers the seed-fires-no-
instances case.)
"""

from __future__ import annotations

import pytest

from ._l2ft_dropdown_walk import walk_dropdown


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.fixture(autouse=True)
def _require_chains(l2ft_l2_instance) -> None:
    # Fast-exit when the deployed L2 declares zero chains (spec_example) —
    # see `conftest.require_l2ft_feature`. A non-zero `declared_chain_parents`
    # is necessary but not sufficient (the seed may fire no instances), so
    # `walk_dropdown`'s "table started empty → skip" covers the rest.
    from tests.e2e.conftest import require_l2ft_feature
    require_l2ft_feature(l2ft_l2_instance, "chains")


@pytest.mark.parametrize("dropdown_title", ["Chain", "Completion"])
def test_chains_dropdown_narrows_does_not_empty(
    l2ft_dashboard_driver, dropdown_title,
) -> None:
    """Each declared Chain parent — and each Completion status
    (Completed / Incomplete / No Required Children) — must leave the
    Chain Instances table with > 0 rows when picked alone."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet="Chains")
    walk_dropdown(
        driver,
        sheet_label="Chains",
        dropdown_title=dropdown_title,
        table_title="Chain Instances",
        # "Completion" is a universal-outcome enum (Completed / Incomplete /
        # No Required Children) — which outcomes occur depends on the L2's
        # chain structure; require ≥1, not all. "Chain" is an L2-declared
        # parent name → every value must have data (strict).
        require_all_advertised=(dropdown_title != "Completion"),
    )
