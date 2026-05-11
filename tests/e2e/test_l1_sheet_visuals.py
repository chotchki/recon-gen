"""Browser test: walk every L1 sheet, verify visuals render.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright
in the test body; `qs_driver` from conftest, `TreeValidator` speaks the
driver). `TreeValidator(l1_app, qs_driver).validate_structure()` walks
every sheet, asserts each declared visual title + control label is in
the DOM; failures across sheets accumulate into one AssertionError.

The 90s per-visual budget: the L1 dashboard's KPI-heavy Daily Statement
(5 KPIs + 1 table, all backed by the multi-CTE summary SQL) consistently
takes longer than the 30s default after a *fresh* deploy — the
per-dataset query cache hasn't warmed yet, so each KPI's first SELECT
pays a cold-start tax. (`wait_loaded` scrolls each visual into view, so
the old tall-viewport hack is gone.)
"""

from __future__ import annotations

import pytest

from .tree_validator import TreeValidator


pytestmark = [pytest.mark.e2e, pytest.mark.browser]

L1_VISUAL_TIMEOUT_MS = 90_000


def test_l1_dashboard_structure_matches_tree(
    qs_driver, l1_dashboard_id, l1_app,
) -> None:
    qs_driver.open(l1_dashboard_id)
    TreeValidator(
        l1_app, qs_driver, timeout_ms=L1_VISUAL_TIMEOUT_MS,
    ).validate_structure()
    qs_driver.screenshot()
