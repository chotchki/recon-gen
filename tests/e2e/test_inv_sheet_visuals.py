"""Browser test: walk every Investigation sheet, verify visuals render — both renderers.

Parametrized over ``[qs, app2]`` (X.2.u.2 — the ``inv_dashboard_driver``
fixture yields ``(driver, dashboard_arg)``: the deployed QS dashboard,
or a locally-spun App 2 server built from the same ``inv_app`` tree
reading the same DB). ``TreeValidator(inv_app, driver).validate_structure()``
walks every sheet, asserts each declared visual title + control label is
in the DOM; failures across sheets accumulate into one AssertionError.

The Account Network sheet's two side-by-side directional Sankeys remain
the load-bearing K.4.8 invariant — both must hydrate, with their
distinct directional titles ("Inbound — counterparties → anchor",
"Outbound — anchor → counterparties"), so an analyst can tell inbound
from outbound by geometry; the tree declares both and ``validate_structure``
asserts both render, so a regression that silently merged them surfaces
as a missing title — on either renderer.
"""

from __future__ import annotations

import pytest

from .tree_validator import TreeValidator


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def test_inv_dashboard_structure_matches_tree(inv_dashboard_driver, inv_app) -> None:
    driver, dashboard_arg = inv_dashboard_driver
    # App 2 is local + fast — see test_l1_sheet_visuals for the rationale.
    timeout_ms = 12_000 if driver.dialect == "app2" else 30_000
    driver.open(dashboard_arg)
    TreeValidator(inv_app, driver, timeout_ms=timeout_ms).validate_structure()
    driver.screenshot()
