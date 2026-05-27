"""X.2.g.1 — Executives → App2 wiring smoke test.

Builds the real Executives tree + datasets, hands them to
``make_tree_db_fetcher``, asserts the fetcher constructs cleanly
(no missing SQL in the registry) and dispatches by visual_id.

The construction-time invariants (every visual's dataset SQL is
in the registry) are what this test pins; runtime DB execution
is covered by the live PG verification step the operator runs
manually before X.2.g.{2,3,4} land.

X.2.n.4: ``make_tree_db_fetcher`` now takes an
``AsyncConnectionPool``. We pass a stub pool whose ``acquire`` is
never reached during the construction-only assertions.
"""

# pyright: reportArgumentType=false, reportCallIssue=false, reportUntypedFunctionDecorator=false
# BF.4/F: _NoConnectPool is a structural fake substituting for AsyncConnectionPool;
# asynccontextmanager + AsyncConnectionPool stubs disagree on the protocol shape,
# the runtime contract is satisfied.
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from recon_gen.apps.executives.app import build_executives_app
from recon_gen.apps.executives.datasets import build_all_datasets
from recon_gen.common.html._tree_fetcher import make_tree_db_fetcher
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests._test_helpers import make_test_config


# build_all_datasets requires the DB-table prefix to be set on cfg.
# The CLI threads this in via cfg yaml; the unit test sets it explicitly
# so the test doesn't depend on disk-resident config files.
_TEST_CFG = make_test_config(db_table_prefix=DEFAULT_PREFIX)


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
        yield  # pragma: no cover  # required for asynccontextmanager shape

    async def close(self) -> None:
        return None


def test_executives_tree_builds_with_expected_sheet_count() -> None:
    """5 sheets: Getting Started, Account Coverage, Transaction Volume,
    Money Moved, Info. Pinned so a sheet-add or sheet-drop in
    apps/executives/app.py is a deliberate decision, not a silent shift."""
    build_all_datasets(_TEST_CFG)
    tree_app = build_executives_app(_TEST_CFG)
    assert tree_app.analysis is not None
    assert len(tree_app.analysis.sheets) == 5


def test_make_tree_db_fetcher_builds_for_executives_with_no_missing_sql() -> None:
    """The build-time SQL lookup walks every visual; if any
    references a dataset whose SQL wasn't registered, the factory
    raises. This test catches the failure mode where a new
    Executives visual lands without its dataset in
    ``build_all_datasets``."""
    build_all_datasets(_TEST_CFG)
    tree_app = build_executives_app(_TEST_CFG)
    # No raise == every visual's dataset SQL was found.
    fetcher = make_tree_db_fetcher(
        tree_app, _TEST_CFG, pool=_NoConnectPool(),
    )
    assert callable(fetcher)


def test_executives_fetcher_returns_empty_for_unknown_visual_id() -> None:
    """Stale URLs / non-data visuals (text boxes) → empty payload.
    The render layer treats empty as a blank visual."""
    build_all_datasets(_TEST_CFG)
    tree_app = build_executives_app(_TEST_CFG)
    fetcher = make_tree_db_fetcher(
        tree_app, _TEST_CFG, pool=_NoConnectPool(),
    )
    # Doesn't connect because the visual isn't in the index.
    assert asyncio.run(fetcher("v-no-such-thing", {})) == {}


def test_executives_visuals_are_indexed_per_sheet() -> None:
    """Every analysis sheet's data visuals are reachable via the
    fetcher. Smoke that the multi-sheet wiring (X.2.e) + tree
    fetcher dispatch (X.2.g.0) compose correctly for a real app.
    Don't fetch — just confirm the visuals exist in the tree."""
    build_all_datasets(_TEST_CFG)
    tree_app = build_executives_app(_TEST_CFG)
    assert tree_app.analysis is not None
    visual_count = sum(
        len(s.visuals) for s in tree_app.analysis.sheets
    )
    # Sheet visuals only — text boxes (Getting Started) live on
    # sheet.text_boxes, not sheet.visuals.
    assert visual_count >= 5, (
        f"Expected ≥5 data visuals across Executives sheets, "
        f"got {visual_count}"
    )
