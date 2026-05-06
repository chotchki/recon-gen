"""X.2.g.1 — Executives → App2 wiring smoke test.

Builds the real Executives tree + datasets, hands them to
``make_tree_db_fetcher``, asserts the fetcher constructs cleanly
(no missing SQL in the registry) and dispatches by visual_id.

Doesn't connect to a DB — the connection_factory raises if called.
The construction-time invariants (every visual's dataset SQL is
in the registry) are what this test pins; runtime DB execution
is covered by the live PG verification step the operator runs
manually before X.2.g.{2,3,4} land.
"""

from __future__ import annotations

import pytest

from quicksight_gen.apps.executives.app import build_executives_app
from quicksight_gen.apps.executives.datasets import build_all_datasets
from quicksight_gen.common.html._tree_fetcher import make_tree_db_fetcher
from tests._test_helpers import make_test_config


# build_all_datasets requires the L2 prefix to be set on cfg. The CLI
# does this via resolve_l2_for_demo; the unit test sets it explicitly
# so the test doesn't depend on disk-resident config files.
_TEST_CFG = make_test_config().with_l2_instance_prefix("spec_example")


def _no_connect_factory() -> object:
    raise RuntimeError(
        "fetcher should not connect to the DB during this construction-only test",
    )


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
        tree_app, _TEST_CFG, connection_factory=_no_connect_factory,
    )
    assert callable(fetcher)


def test_executives_fetcher_returns_empty_for_unknown_visual_id() -> None:
    """Stale URLs / non-data visuals (text boxes) → empty payload.
    The render layer treats empty as a blank visual."""
    build_all_datasets(_TEST_CFG)
    tree_app = build_executives_app(_TEST_CFG)
    fetcher = make_tree_db_fetcher(
        tree_app, _TEST_CFG, connection_factory=_no_connect_factory,
    )
    # Doesn't connect because the visual isn't in the index.
    assert fetcher("v-no-such-thing", {}) == {}


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
