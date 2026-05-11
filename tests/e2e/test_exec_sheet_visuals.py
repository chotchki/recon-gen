"""Browser test: walk every Executives sheet, verify visuals render.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright
in the test body; `qs_driver` from conftest, `TreeValidator` speaks the
driver). `TreeValidator(exec_app, qs_driver).validate_structure()` walks
every sheet, asserts each declared visual title + control label is in
the DOM; failures across sheets accumulate into one AssertionError.
"""

from __future__ import annotations

import pytest

from .tree_validator import TreeValidator


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def test_exec_dashboard_structure_matches_tree(
    qs_driver, exec_dashboard_id, exec_app,
) -> None:
    qs_driver.open(exec_dashboard_id)
    TreeValidator(exec_app, qs_driver).validate_structure()
    qs_driver.screenshot()
