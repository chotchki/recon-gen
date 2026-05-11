"""Browser test: L2FT Rails sheet dropdowns narrow the Transactions table.

X.1.g + Y.2.c regression guard — see ``_l2ft_dropdown_walk`` for the
shared mechanics and the failure modes. Ported onto the
``DashboardDriver`` protocol (X.2.q.3 — ``qs_driver`` from conftest).
"""

from __future__ import annotations

import pytest

from ._l2ft_dropdown_walk import walk_dropdown


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.mark.parametrize("dropdown_title", ["Rail", "Status", "Bundle"])
def test_rails_dropdown_narrows_does_not_empty(
    qs_driver, l2ft_dashboard_id, dropdown_title,
) -> None:
    """Picking a single Rail / Status / Bundle value must leave the
    Transactions table with > 0 rows — the X.1.g param-bound narrowing
    regression class. (Status's universe is the bounded
    Pending/Posted/Failed enum; every value should narrow to a proper
    non-empty subset on a populated demo.)"""
    qs_driver.open(l2ft_dashboard_id, sheet="Rails")
    walk_dropdown(
        qs_driver,
        sheet_label="Rails",
        dropdown_title=dropdown_title,
        table_title="Transactions",
    )
