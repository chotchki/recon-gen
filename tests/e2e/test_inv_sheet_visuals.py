"""Browser test: walk every Investigation sheet, verify visuals render.

Ported onto the ``DashboardDriver`` protocol (X.2.q.3 — no Playwright
in the test body; `qs_driver` from conftest, `TreeValidator` speaks the
driver). `TreeValidator(inv_app, qs_driver).validate_structure()` walks
every sheet, asserts each declared visual title + control label is in
the DOM; failures across sheets accumulate into one AssertionError.

The Account Network sheet's two side-by-side directional Sankeys remain
the load-bearing K.4.8 invariant — both must hydrate, with their
distinct directional titles ("Inbound — counterparties → anchor",
"Outbound — anchor → counterparties"), so an analyst can tell inbound
from outbound by geometry; the tree declares both and `validate_structure`
asserts both render, so a regression that silently merged them surfaces
as a missing title.
"""

from __future__ import annotations

import pytest

from .tree_validator import TreeValidator


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def test_inv_dashboard_structure_matches_tree(
    qs_driver, inv_dashboard_id, inv_app,
) -> None:
    qs_driver.open(inv_dashboard_id)
    TreeValidator(inv_app, qs_driver).validate_structure()
    qs_driver.screenshot()
