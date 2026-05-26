# pyright: reportArgumentType=false, reportCallIssue=false, reportUntypedFunctionDecorator=false
# BF.4/F: see test_html_executives_wiring.py.
"""Y.2.app2.cde.l2ft-wiring — L2 Flow Tracing → App2 wiring smoke test.

Builds the real L2FT tree + datasets, hands them to
``make_tree_db_fetcher``, asserts the fetcher constructs cleanly
(no missing SQL in the registry) and dispatches by visual_id. Mirrors
``test_html_investigation_wiring.py``.

L2FT is the fourth app on the generic-fetcher path (after Executives,
Investigation; L1 still pending). The primitive it adds vs the others
is the **MULTI_SELECT pushdown dropdown** family — Rails (rail / status
/ bundle), Chains (chain / completion), Transfer Templates (template /
completion) — whose dataset SQL carries `<<$pL2ftRail>>`-style
placeholders the App2 executor resolves via the dataset params'
declared-value defaults (Y.2.app2.cde.core) + multi-valued ``IN``-list
expansion when the URL supplies 2+ values (Y.2.app2.cde.multivalued).

This test pins construction-time invariants only — the actual
dropdown round-trip in the browser is ``test_html2_l2ft*`` (later
sub-task), and the per-visual SQL-execute correctness is the ``db``
layer's dataset-SQL smoke verifier.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
from recon_gen.apps.l2_flow_tracing.datasets import (
    build_all_l2_flow_tracing_datasets,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.html._tree_fetcher import make_tree_db_fetcher
from recon_gen.common.ids import VisualId
from tests._test_helpers import make_test_config


# L2FT's build_all_l2_flow_tracing_datasets needs both cfg + L2 instance
# (the App Info matview names use cfg.db_table_prefix; the
# Rails/Chains/Templates pushdown params' declared-value defaults are
# L2-derived).
_TEST_INSTANCE = default_l2_instance()
_TEST_CFG = make_test_config(db_table_prefix="spec_example")


class _NoConnectPool:
    """``AsyncConnectionPool`` whose ``acquire`` raises if reached —
    construction-only tests pass this so a regression that *calls* the
    fetcher (instead of just building it) fails loudly."""

    @asynccontextmanager
    async def acquire(self) -> Any:
        raise RuntimeError(
            "fetcher should not connect to the DB during this "
            "construction-only test",
        )
        yield  # pragma: no cover

    async def close(self) -> None:
        return None


def test_l2ft_tree_builds_with_expected_sheet_count() -> None:
    """6 sheets: Getting Started, Rails, Chains, Transfer Templates,
    L2 Hygiene Exceptions, Info. Pinned so a sheet-add/-drop in
    apps/l2_flow_tracing/app.py is a deliberate decision."""
    build_all_l2_flow_tracing_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_l2_flow_tracing_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    assert tree_app.analysis is not None
    assert len(tree_app.analysis.sheets) == 6


def test_make_tree_db_fetcher_builds_for_l2ft_with_no_missing_sql() -> None:
    """The build-time SQL lookup walks every visual; if any references
    a dataset whose SQL wasn't registered, the factory raises. Catches
    a new L2FT visual landing without its dataset in
    ``build_all_l2_flow_tracing_datasets``."""
    build_all_l2_flow_tracing_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_l2_flow_tracing_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    fetcher = make_tree_db_fetcher(
        tree_app, _TEST_CFG, pool=_NoConnectPool(),
    )
    assert callable(fetcher)


def test_l2ft_fetcher_returns_empty_for_unknown_visual_id() -> None:
    """Stale URLs / non-data visuals (text boxes) → empty payload."""
    build_all_l2_flow_tracing_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_l2_flow_tracing_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    fetcher = make_tree_db_fetcher(
        tree_app, _TEST_CFG, pool=_NoConnectPool(),
    )
    assert asyncio.run(fetcher(VisualId("v-no-such-thing"), {})) == {}


def test_l2ft_visuals_are_indexed_per_sheet() -> None:
    """Every analysis sheet's data visuals are reachable via the
    fetcher. Conservative lower bound so a future visual-add doesn't
    break the test."""
    build_all_l2_flow_tracing_datasets(_TEST_CFG, _TEST_INSTANCE)
    tree_app = build_l2_flow_tracing_app(_TEST_CFG, l2_instance=_TEST_INSTANCE)
    assert tree_app.analysis is not None
    visual_count = sum(
        len(s.visuals) for s in tree_app.analysis.sheets
    )
    assert visual_count >= 4, (
        f"Expected ≥4 data visuals across L2FT sheets, got {visual_count}"
    )
