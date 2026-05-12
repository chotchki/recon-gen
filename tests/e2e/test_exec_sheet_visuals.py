"""Browser test: walk every Executives sheet, verify visuals render — both renderers.

Parametrized over ``[qs, app2]`` (X.2.u.2 — the ``exec_dashboard_driver``
fixture yields ``(driver, dashboard_arg)``: the deployed QS dashboard,
or a locally-spun App 2 server built from the same ``exec_app`` tree
reading the same DB). ``TreeValidator(exec_app, driver).validate_structure()``
walks every sheet, asserts each declared visual title + control label is
in the DOM; failures across sheets accumulate into one AssertionError.
"""

from __future__ import annotations

import pytest

from .tree_validator import TreeValidator


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def test_exec_dashboard_structure_matches_tree(exec_dashboard_driver, exec_app) -> None:
    driver, dashboard_arg = exec_dashboard_driver
    # App 2 is local + fast — see test_l1_sheet_visuals for the rationale.
    timeout_ms = 12_000 if driver.dialect == "app2" else 30_000
    driver.open(dashboard_arg)
    TreeValidator(exec_app, driver, timeout_ms=timeout_ms).validate_structure()
    driver.screenshot()
