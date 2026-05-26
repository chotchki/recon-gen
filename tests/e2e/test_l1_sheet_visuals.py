"""Browser test: walk every L1 sheet, verify visuals render — both renderers.

Parametrized over ``[qs, app2]`` (X.2.u.2 — the ``l1_dashboard_driver``
fixture yields ``(driver, dashboard_arg)``: the deployed QS dashboard,
or a locally-spun App 2 server built from the same ``l1_app`` tree
reading the same DB). ``TreeValidator(l1_app, driver).validate_structure()``
walks every sheet, asserts each declared visual title + control label is
in the DOM; failures across sheets accumulate into one AssertionError.
Running it against App 2 too surfaces any renderer gap (a tree visual
kind App 2 doesn't render → the validator fails on the ``app2`` param).

The 90 s per-visual budget: the L1 dashboard's KPI-heavy Daily Statement
(5 KPIs + 1 table, all backed by the multi-CTE summary SQL) consistently
takes longer than the 30 s default after a *fresh* QS deploy — the
per-dataset query cache hasn't warmed yet, so each KPI's first SELECT
pays a cold-start tax. (Generous for App 2 too; it's a max, not a floor.
``wait_loaded`` scrolls each visual into view, so the old tall-viewport
hack is gone.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .tree_validator import TreeValidator



if TYPE_CHECKING:
    from recon_gen.common.tree import App
    from tests.e2e._drivers import DashboardDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]

L1_VISUAL_TIMEOUT_MS = 90_000


def test_l1_dashboard_structure_matches_tree(l1_dashboard_driver: tuple["DashboardDriver", str], l1_app: "App") -> None:
    driver, dashboard_arg = l1_dashboard_driver
    # App 2 is a local server — anything not loaded in ~12 s is a 500 /
    # broken visual, not a cold-start; the 90 s budget is QS-deploy-fresh
    # only (a failing app2 cell otherwise burns 90 s × every broken visual).
    timeout_ms = 12_000 if driver.dialect == "app2" else L1_VISUAL_TIMEOUT_MS
    driver.open(dashboard_arg)
    TreeValidator(l1_app, driver, timeout_ms=timeout_ms).validate_structure()
    driver.screenshot()
