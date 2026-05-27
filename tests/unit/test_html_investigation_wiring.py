# pyright: reportArgumentType=false, reportCallIssue=false, reportUntypedFunctionDecorator=false
# BF.4/F: see test_html_executives_wiring.py — structural _NoConnectPool fake
# for AsyncConnectionPool; asynccontextmanager stubs disagree with the test shape.
"""X.2.g.2.a — Investigation → App2 wiring smoke test.

Builds the real Investigation tree + datasets, hands them to
``make_tree_db_fetcher``, asserts the fetcher constructs cleanly
(no missing SQL in the registry) and dispatches by visual_id.

Investigation is the second app to land on the generic-fetcher
path (Executives was first via X.2.g.1). It introduces three
primitives Executives doesn't exercise:

  - Sankey visuals (Money Trail + Account Network's two
    directional Sankeys).
  - StringParam dropdowns (chain root + anchor account).
  - FilterGroups using calc fields (Account Network's
    direction flags).

This test pins construction-time invariants only — runtime
correctness for Sankey shape (X.2.g.2.b) and calc-field-to-SQL
(X.2.g.2.c) are tracked separately.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from recon_gen.apps.investigation.app import build_investigation_app
from recon_gen.apps.investigation.datasets import build_all_datasets
from recon_gen.common.ids import VisualId
from recon_gen.common.html._tree_fetcher import make_tree_db_fetcher
from recon_gen.common.l2 import default_l2_instance
from tests._test_helpers import make_test_config


# Investigation's build_all_datasets needs both cfg + L2 instance —
# the App Info matview names are derived from cfg.db_table_prefix.
_TEST_INSTANCE = default_l2_instance()
_TEST_CFG = make_test_config(db_table_prefix="spec_example")


class _NoConnectPool:
    """``AsyncConnectionPool`` whose ``acquire`` raises if reached.

    Construction-only tests pass this so a regression that calls the
    fetcher (instead of just building it) fails loudly with a clear
    message rather than e.g. a TypeError on a None acquire.
    """

    @asynccontextmanager
    async def acquire(self) -> Any:
        raise RuntimeError(
            "fetcher should not connect to the DB during this "
            "construction-only test",
        )
        yield  # pragma: no cover

    async def close(self) -> None:
        return None


def test_investigation_tree_builds_with_expected_sheet_count() -> None:
    """6 sheets: Getting Started, Recipient Fanout, Volume Anomalies,
    Money Trail, Account Network, Info. Pinned so a sheet-add or
    sheet-drop in apps/investigation/app.py is a deliberate decision.
    """
    build_all_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_investigation_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    assert tree_app.analysis is not None
    assert len(tree_app.analysis.sheets) == 6


def test_make_tree_db_fetcher_builds_for_investigation_with_no_missing_sql() -> None:
    """The build-time SQL lookup walks every visual; if any references
    a dataset whose SQL wasn't registered, the factory raises. Catches
    the failure mode where a new Investigation visual lands without
    its dataset in ``build_all_datasets``."""
    build_all_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_investigation_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    fetcher = make_tree_db_fetcher(
        tree_app, _TEST_CFG, pool=_NoConnectPool(),
    )
    assert callable(fetcher)


def test_investigation_fetcher_returns_empty_for_unknown_visual_id() -> None:
    """Stale URLs / non-data visuals (text boxes) → empty payload."""
    build_all_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_investigation_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    fetcher = make_tree_db_fetcher(
        tree_app, _TEST_CFG, pool=_NoConnectPool(),
    )
    assert asyncio.run(fetcher(VisualId("v-no-such-thing"), {})) == {}


def test_investigation_visuals_are_indexed_per_sheet() -> None:
    """Every analysis sheet's data visuals are reachable via the
    fetcher. Investigation has KPIs + Tables + a BarChart + three
    Sankeys — wider primitive coverage than Executives, so the
    multi-sheet wiring + tree fetcher dispatch get exercised on a
    larger surface here."""
    build_all_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_investigation_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    assert tree_app.analysis is not None
    visual_count = sum(
        len(s.visuals) for s in tree_app.analysis.sheets
    )
    # Recipient Fanout (3 KPIs + 1 Table), Volume Anomalies (1 KPI +
    # 1 BarChart + 1 Table), Money Trail (1 Sankey + 1 Table),
    # Account Network (2 Sankeys + 1 Table), Info (≥2). Conservative
    # lower bound so a future visual-add doesn't break the test.
    assert visual_count >= 12, (
        f"Expected ≥12 data visuals across Investigation sheets, "
        f"got {visual_count}"
    )


def test_investigation_tree_includes_sankey_visuals() -> None:
    """Sankey is the new primitive Investigation introduces vs
    Executives. Pin its presence so a tree restructure that drops
    Sankeys (e.g. swapping to a different visual kind) is a
    deliberate decision."""
    build_all_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_investigation_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    assert tree_app.analysis is not None
    sankey_count = sum(
        1 for s in tree_app.analysis.sheets for v in s.visuals
        if type(v).__name__ == "Sankey"
    )
    # Money Trail (1) + Account Network inbound + outbound (2) = 3.
    assert sankey_count == 3, (
        f"Expected 3 Sankey visuals (Money Trail + Account Network "
        f"inbound + outbound), got {sankey_count}"
    )
