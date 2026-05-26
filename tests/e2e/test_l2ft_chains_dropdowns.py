"""Browser test: L2FT Chains sheet dropdowns narrow the Chain Instances table.

X.1.g regression guard — see ``_l2ft_dropdown_walk`` for the shared
mechanics and the failure modes. Parametrized over ``[qs, app2]``
(X.2.u.3) via ``l2ft_dashboard_driver``. spec_example declares one chain
(``ExternalReconciliationCycle → ReconciliationClosing``, Required) whose
auto-scenario firings exercise both completion outcomes — firing 2 (the
closing leg doesn't fire) is 'Incomplete', firings 1 & 3 are 'Completed'
(X.2.u.3.fix.demo). A fuzz seed or operator-supplied L2 declaring zero
chains makes the ``_require_chains`` autouse fixture skip both legs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ._l2ft_dropdown_walk import walk_dropdown



if TYPE_CHECKING:
    from recon_gen.common.l2 import L2Instance
    from tests.e2e._drivers import DashboardDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


@pytest.fixture(autouse=True)
def _require_chains(l2ft_l2_instance: "L2Instance") -> None:
    # Fast-exit when the deployed L2 declares zero chains — see
    # `conftest.require_l2ft_feature`. A non-zero `declared_chain_parents`
    # is necessary but not sufficient (a fuzz seed may fire no instances),
    # so `walk_dropdown`'s "table started empty → skip" covers the rest.
    from tests.e2e.conftest import require_l2ft_feature
    require_l2ft_feature(l2ft_l2_instance, "chains")


@pytest.mark.parametrize("dropdown_title", ["Chain", "Completion"])
def test_chains_dropdown_narrows_does_not_empty(
    l2ft_dashboard_driver: tuple["DashboardDriver", str], dropdown_title,
) -> None:
    """Each declared Chain parent — and each Completion status
    (Completed / Incomplete) — must leave the Chain Instances table
    with > 0 rows when picked alone.

    Strict (``require_all_advertised`` default ``True``): spec_example's
    chain is designed so its firings cover both outcomes
    (X.2.u.3.fix.demo). A value that empties the table is a regression —
    stale enum, missing chain-child plants, or a pushdown break."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet="Chains")
    walk_dropdown(
        driver,
        sheet_label="Chains",
        dropdown_title=dropdown_title,
        table_title="Chain Instances",
    )
