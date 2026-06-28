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

from tests._marks import Need, Tier, needs, tier

from recon_gen.apps.l2_flow_tracing.app import _TRANSFER_TEMPLATES_NAME

from ._l2ft_dropdown_walk import walk_dropdown



if TYPE_CHECKING:
    from recon_gen.common.l2 import L2Instance
    from tests.e2e._drivers import DashboardDriver

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]


@pytest.fixture(autouse=True)
def _require_templates(l2ft_l2_instance: "L2Instance") -> None:  # pyright: ignore[reportUnusedFunction]: pytest autouse fixture
    # Fast-exit when the deployed L2 declares zero transfer templates —
    # see `conftest.require_l2ft_feature`. (A fuzz seed or operator-supplied
    # L2 may declare none; spec_example declares two.)
    from tests.e2e.conftest import require_l2ft_feature
    require_l2ft_feature(l2ft_l2_instance, "templates")


@pytest.mark.parametrize("dropdown_title", ["Template", "Completion"])
def test_templates_dropdown_narrows_does_not_empty(
    l2ft_dashboard_driver: tuple["DashboardDriver", str], dropdown_title: str,
) -> None:
    """Each declared Template name — and each Completion status
    (Complete / Imbalanced / Orphaned) — must leave the Template
    Instances table with > 0 rows when picked alone.

    ``Template``: strict (require_all_advertised=True). Each declared
    template name must have ≥1 instance — a missing one is a stale enum,
    missing plants, or pushdown break.

    ``Completion``: relaxed (require_all_advertised=False). The enum is
    universal-outcome (Complete / Imbalanced / Orphaned) — not every
    deployed L2 will have plants in every outcome (e.g. sasquatch_pr's
    seed may lack Complete/Orphaned instances; CB.17.c1). The relaxed
    mode asserts ≥1 advertised value keeps the table non-empty, which
    is sufficient to prove the binding works at all."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet=_TRANSFER_TEMPLATES_NAME)
    walk_dropdown(
        driver,
        sheet_label=_TRANSFER_TEMPLATES_NAME,
        dropdown_title=dropdown_title,
        table_title="Template Instances",
        require_all_advertised=(dropdown_title != "Completion"),
    )
