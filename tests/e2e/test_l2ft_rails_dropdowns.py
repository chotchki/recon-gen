"""Browser test: L2FT Rails sheet dropdowns narrow the Transactions table.

X.1.g + Y.2.c regression guard — see ``_l2ft_dropdown_walk`` for the
shared mechanics and the failure modes. Parametrized over ``[qs, app2]``
(X.2.u.3) via ``l2ft_dashboard_driver``: the Rails dropdowns are
MULTI_SELECT StaticValues, so ``make_filter_specs_for_sheet`` renders
them in App2 too and the same ``<<$param>>`` SQL pushdown narrows both
renderers (Y.2's QS/App2 convergence).
"""

from __future__ import annotations

import pytest

from ._l2ft_dropdown_walk import walk_dropdown


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.parametrize("dropdown_title", ["Rail", "Status", "Bundle"])
def test_rails_dropdown_narrows_does_not_empty(
    l2ft_dashboard_driver, dropdown_title,
) -> None:
    """Picking a single Rail / Status / Bundle value must leave the
    Transactions table with > 0 rows — the X.1.g param-bound narrowing
    regression class. (Status's universe is the bounded
    Pending/Posted/Failed enum; every value should narrow to a proper
    non-empty subset on a populated demo.)"""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet="Rails")
    walk_dropdown(
        driver,
        sheet_label="Rails",
        dropdown_title=dropdown_title,
        table_title="Transactions",
    )
