"""Browser test: L2FT Transfer Templates dropdowns narrow the table.

X.1.g regression guard. Templates is the ``cross_dataset="ALL_DATASETS"``
case — one parameter narrows BOTH the Sankey (built from tt-legs) and
the Template Instances table (built from tt-instances); the table is the
more sensitive instrument (the Sankey has no row-count primitive), so
that's what ``walk_dropdown`` asserts on. See ``_l2ft_dropdown_walk``
for the shared mechanics. Parametrized over ``[qs, app2]`` (X.2.u.3) via
``l2ft_dashboard_driver``. spec_example declares two templates — one
SingleLegRail-first (every firing 'Imbalanced') and one TwoLegRail-first
chain-parent (firings 'Complete'/'Orphaned') — so the auto-scenario fires
template instances covering all three Completion outcomes (X.2.u.3.fix.demo).
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
def _require_templates(l2ft_l2_instance: "L2Instance") -> None:
    # Fast-exit when the deployed L2 declares zero transfer templates —
    # see `conftest.require_l2ft_feature`. (A fuzz seed or operator-supplied
    # L2 may declare none; spec_example declares two.)
    from tests.e2e.conftest import require_l2ft_feature
    require_l2ft_feature(l2ft_l2_instance, "templates")


@pytest.mark.parametrize("dropdown_title", ["Template", "Completion"])
def test_templates_dropdown_narrows_does_not_empty(
    l2ft_dashboard_driver: tuple["DashboardDriver", str], dropdown_title,
) -> None:
    """Each declared Template name — and each Completion status
    (Complete / Imbalanced / Orphaned) — must leave the Template
    Instances table with > 0 rows when picked alone.

    Strict (``require_all_advertised`` default ``True``): spec_example's
    pair of templates is designed to exercise every Completion outcome
    (X.2.u.3.fix.demo). A value that empties the table is a regression —
    stale enum, missing plants, or a pushdown break."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet="Transfer Templates")
    walk_dropdown(
        driver,
        sheet_label="Transfer Templates",
        dropdown_title=dropdown_title,
        table_title="Template Instances",
    )
