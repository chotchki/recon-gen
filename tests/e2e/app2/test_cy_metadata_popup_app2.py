"""CY.9 — e2e for the row-metadata side panel (App2 renderer).

Drives the L1 Dashboard tree through ``App2Driver`` against a stub
fetcher that returns deterministic table rows with controlled
``metadata`` JSON, then exercises the CY.5/6 ``⋯`` → ``{} View metadata``
ctxmenu entry → side-panel drawer wiring.

Parametrized over the two sheets that carry ``metadata_popup=True``
tables — Transactions (Posting Ledger) and Daily Statement (Posted Money
Records). Visual titles + sheet names are read off ``apps/l1_dashboard/
app.py`` so a rename surfaces at sheet-discovery time, not as a
mid-flow Playwright timeout.

Stub fetcher (not live PG) by design — the contract here is the
DOM-wiring path (button → menu → panel drawer + JSON tree rendering),
NOT "did the DB-side metadata column round-trip cleanly?". The latter
is covered by ``test_audit_invariants_app2.py``'s live-DB harness.
Stubbing lets us pin the metadata payload (``{"plant_kind": ...,
"phantom_rail_count": ...}``) to known-bits the assertions can verify
without dragging in a DB seed cycle. We label the planted payload's
top-level discriminator ``plant_kind`` to match the wiring docstring
on the row drill — the field name a future live-DB run would write
through ``scenario_metadata`` adapters.

QS-leg test param skips with ``NotImplementedError`` from each verb
(operator lock 7: metadata popup is App2-only).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from recon_gen.apps.l1_dashboard.app import (
    _DAILY_STATEMENT_NAME,
    _TRANSACTIONS_NAME,
    build_l1_dashboard_app,
)
from recon_gen.apps.l1_dashboard.datasets import (
    build_all_l1_dashboard_datasets,
)
from recon_gen.common.ids import VisualId
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests._test_helpers import make_test_config
from tests.e2e._drivers import App2Driver, DashboardDriver, skips_if_unsupported


_DASHBOARD_ID = "l1"

# Per-sheet target table — confirmed against
# ``apps/l1_dashboard/app.py::_populate_transactions_sheet`` (line
# 1726, ``title="Posting Ledger"``) and
# ``_populate_daily_statement_sheet`` (line 1840, ``title="Posted
# Money Records"``).
_SHEETS: list[tuple[str, str]] = [
    (_TRANSACTIONS_NAME, "Posting Ledger"),
    (_DAILY_STATEMENT_NAME, "Posted Money Records"),
]

# Daily Statement's table contract is a strict subset of the
# Transactions table's — both include ``transaction_id`` + ``metadata``
# (the two columns bootstrap.js's ``openRowMenu`` reads for the popup
# wiring). Project the smaller contract on both for simplicity.
_TABLE_COLUMNS: list[str] = [
    "transaction_id",
    "account_id",
    "account_name",
    "transfer_id",
    "rail_name",
    "amount_money",
    "amount_direction",
    "status",
    "origin",
    "posting",
    "metadata",
]

# Row 0: a planted phantom_rail row — the "metadata is rich, popup
# shows tree" case. We tag the discriminator key ``plant_kind`` so
# the rendered JSON tree carries a token the test can grep for
# (matches the task wording: "a phantom_rail plant writes plant_kind
# to metadata"). Nested ``details`` to verify the depth-2 default-open
# rule: the top dict + the ``shape`` sub-dict open by default, the
# deeper ``raw`` sub-dict starts collapsed.
_PLANTED_METADATA: dict[str, Any] = {
    "plant_kind": "phantom_rail",
    "phantom_rail_count": 3,
    "scenario_id": "cy9-fixture",
    "shape": {
        "anchor_iso": "2030-01-01",
        "raw": {
            "deep_key_a": "deep_value_a",
            "deep_key_b": ["x", "y", "z"],
        },
    },
}
_PLANTED_METADATA_JSON: str = json.dumps(_PLANTED_METADATA)

# Row 1: empty-object metadata — the "no metadata for this row" case.
# bootstrap.js's openRowMenu only suppresses the synthetic entry on
# undefined/null/empty-string; ``"{}"`` is a valid (truthy) JSON
# string, so the menu entry surfaces. The route handler's parse →
# empty-state branch returns the operator-locked
# ``"No metadata for this row."`` fragment.
_EMPTY_METADATA_JSON: str = "{}"


def _row(
    *, txn_id: str, metadata_json: str,
) -> list[Any]:
    """Build one positional row matching ``_TABLE_COLUMNS``. All cells
    except the two the wiring reads (``transaction_id`` + ``metadata``)
    are deterministic placeholders — App2's table renderer paints
    them, but the popup only cares about the two we control.
    """
    return [
        txn_id,
        "acct-1",
        "Demo Account",
        f"xfer-{txn_id}",
        "demo_rail",
        100.0,
        "Credit",
        "Posted",
        "DemoOverlay",
        "2030-01-01T12:00:00",
        metadata_json,
    ]


def _table_payload() -> dict[str, Any]:
    """Two-row Table payload — row 0 planted (rich metadata), row 1
    empty (``{}``). The metadata column is marked ``hidden=True`` so
    the renderer omits its header / cells but the row payload still
    carries the value positionally (bootstrap.js's openRowMenu reads
    by ``colIndex.metadata``).
    """
    cols: list[dict[str, Any]] = []
    for name in _TABLE_COLUMNS:
        col: dict[str, Any] = {"name": name}
        if name == "metadata":
            col["hidden"] = True
        cols.append(col)
    rows: list[list[Any]] = [
        _row(txn_id="txn-planted", metadata_json=_PLANTED_METADATA_JSON),
        _row(txn_id="txn-empty", metadata_json=_EMPTY_METADATA_JSON),
    ]
    return {
        "columns": cols,
        "rows": rows,
        "page_offset": 0,
        "page_size": len(rows),
        "total_rows": len(rows),
        "sort_column": "",
    }


def _make_cfg() -> Any:
    """Z.C — build a test cfg with the L2's default prefix so the
    dataset SQL placeholders + tree resolution stay self-consistent.
    """
    return make_test_config(db_table_prefix=DEFAULT_PREFIX)


def _build_l1_app_with_stub() -> tuple[Any, dict[str, VisualId]]:
    """Build the L1 Dashboard tree + emit auto-IDs + map each target
    table's visual_id by title. Returns ``(tree_app, visual_ids_by_title)``.
    """
    cfg = _make_cfg()
    instance = default_l2_instance()
    build_all_l1_dashboard_datasets(cfg, instance)
    tree_app = build_l1_dashboard_app(cfg, l2_instance=instance)
    # emit_analysis() resolves auto-IDs (visual.visual_id = AUTO → a
    # concrete UUID) in addition to running validation walks; we need
    # the resolved form so the stub fetcher can key by visual_id.
    tree_app.emit_analysis()
    analysis = tree_app.analysis
    assert analysis is not None
    titles = {title for _, title in _SHEETS}
    by_title: dict[str, VisualId] = {}
    for sheet in analysis.sheets:
        for visual in sheet.visuals:
            t = getattr(visual, "title", None)
            if t in titles:
                # emit_analysis() above has run, so visual_id is the
                # resolved VisualId form, not the AUTO sentinel. The
                # ``isinstance(str, ...)`` narrow keeps pyright happy
                # without a brittle cast — and surfaces the resolve
                # failure cleanly when a future refactor breaks the
                # post-emit invariant.
                vid = visual.visual_id
                assert isinstance(vid, str), (
                    f"visual_id for {t!r} unresolved after "
                    f"emit_analysis — got {vid!r}"
                )
                by_title[t] = VisualId(vid)
    missing = titles - set(by_title)
    if missing:
        raise AssertionError(
            f"L1 Dashboard tree changed shape — couldn't find visual "
            f"title(s) {sorted(missing)!r}. Update _SHEETS in this test."
        )
    return tree_app, by_title


def _stub_fetcher_factory(
    visual_ids_by_title: dict[str, VisualId],
) -> Any:
    """Return a sync stub fetcher dispatching by visual_id: the two
    target Table visuals get the metadata-bearing payload; every other
    visual gets a benign default (empty KPI / empty table / empty
    chart). Keeps the page settling fast without DB plumbing.
    """
    target_ids = {str(vid) for vid in visual_ids_by_title.values()}

    def fetcher(visual_id: Any, params: Any) -> Any:  # noqa: ARG001
        _ = params
        if str(visual_id) in target_ids:
            return _table_payload()
        # Generic fall-through — emit_visual_data_fragment dispatches
        # by the response's keys, so an empty dict renders nothing
        # (no row drills, no tree). KPIs / Tables on other sheets
        # just paint their empty-state banner, which satisfies
        # wait_loaded's empty-state branch.
        return {}

    return fetcher


@pytest.fixture
def cy9_driver() -> Iterator[App2Driver]:
    """``App2Driver`` serving the L1 Dashboard + a stub fetcher that
    returns metadata-bearing rows for the two target tables.

    Lands on the Transactions sheet so the first visual section that
    auto-loads on ``open(...)`` is the one carrying the popup wiring
    — minimizes cross-sheet fetch noise.
    """
    cfg = _make_cfg()
    tree_app, by_title = _build_l1_app_with_stub()
    analysis = tree_app.analysis
    assert analysis is not None
    landing_sheet = next(
        s for s in analysis.sheets if s.name == _TRANSACTIONS_NAME
    )
    fetcher = _stub_fetcher_factory(by_title)
    with App2Driver.serving(
        cfg=cfg,
        tree_app=tree_app, sheet=landing_sheet,
        data_fetcher=fetcher,  # pyright: ignore[reportArgumentType]: structural DataFetcher contract holds at runtime
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="L1 Dashboard (CY.9 stub)",
    ) as driver:
        yield driver


def _open_planted_panel(
    driver: DashboardDriver, sheet_name: str, visual_title: str,
) -> None:
    """Common opening sequence: navigate to the sheet, wait for the
    target table to settle, then trigger the row-0 (planted) metadata
    popup. Used by every test body below so the orchestration stays
    in one place."""
    driver.open(_DASHBOARD_ID)
    driver.goto_sheet(sheet_name)
    driver.wait_loaded(visual_title)
    driver.open_metadata_panel(visual_title, row_index=0)


@pytest.mark.parametrize(("sheet_name", "visual_title"), _SHEETS)
def test_metadata_popup_renders_planted_payload(
    cy9_driver: App2Driver, sheet_name: str, visual_title: str,
) -> None:
    """Row 0's planted metadata surfaces in the raw textarea AND in the
    rendered ``<details>`` tree; the default-open count matches the
    depth-2 rule (``plant_kind`` and ``shape`` open, ``shape.raw``
    closed).
    """
    driver = cy9_driver
    _open_planted_panel(driver, sheet_name, visual_title)
    raw = driver.metadata_panel_text()
    assert "plant_kind" in raw, (
        f"raw textarea missing 'plant_kind' on sheet {sheet_name!r} "
        f"visual {visual_title!r} — got first 300 chars: {raw[:300]!r}"
    )
    assert "phantom_rail" in raw, (
        f"raw textarea missing planted value 'phantom_rail' on "
        f"sheet {sheet_name!r}"
    )
    # Depth-2 expectation per ``_render_json_node`` rule (line 477 of
    # _side_panel.py): ``open_attr = " open" if depth <= 2 else ""``.
    # Top-level dict entries render at depth=1, children at depth+1.
    #
    # For our planted payload's nesting:
    #   plant_kind (depth 1, leaf — not a <details>)
    #   phantom_rail_count (depth 1, leaf — not a <details>)
    #   scenario_id (depth 1, leaf — not a <details>)
    #   shape (depth 1, dict — <details OPEN>)
    #     anchor_iso (depth 2, leaf — not a <details>)
    #     raw (depth 2, dict — <details OPEN>)
    #       deep_key_a (depth 3, leaf — not a <details>)
    #       deep_key_b (depth 3, list — <details> not-open per rule)
    #
    # Open <details data-json-node>: ``shape`` + ``shape.raw`` = 2.
    expected_open = 2
    actual_open = driver.metadata_panel_open_details_count()
    assert actual_open == expected_open, (
        f"depth-2 open-by-default count mismatch on sheet "
        f"{sheet_name!r}: got {actual_open}, expected {expected_open}. "
        f"Either the depth rule shifted or the planted payload's "
        f"nested shape did."
    )
    driver.close_metadata_panel()


@pytest.mark.parametrize(("sheet_name", "visual_title"), _SHEETS)
def test_metadata_panel_collapse_then_expand_all_smoke(
    cy9_driver: App2Driver, sheet_name: str, visual_title: str,
) -> None:
    """Smoke the bulk-toggle buttons: collapse-all drops the open
    count to 0, expand-all flips every ``<details>`` node open
    (matches the count of nested-collection nodes in the planted
    payload — `shape`, `shape.raw`, and `shape.raw.deep_key_b` =
    3).
    """
    driver = cy9_driver
    _open_planted_panel(driver, sheet_name, visual_title)
    driver.metadata_panel_collapse_all()
    after_collapse = driver.metadata_panel_open_details_count()
    assert after_collapse == 0, (
        f"collapse-all left {after_collapse} <details open> nodes on "
        f"sheet {sheet_name!r}"
    )
    driver.metadata_panel_expand_all()
    after_expand = driver.metadata_panel_open_details_count()
    # The planted payload has 3 dict/list nodes that render as
    # ``<details>``: ``shape``, ``shape.raw``, ``shape.raw.deep_key_b``
    # (the list). expand-all opens all of them.
    assert after_expand == 3, (
        f"expand-all opened {after_expand} <details> nodes on sheet "
        f"{sheet_name!r}, expected 3"
    )
    driver.close_metadata_panel()


@pytest.mark.parametrize(("sheet_name", "visual_title"), _SHEETS)
def test_metadata_panel_escape_dismisses(
    cy9_driver: App2Driver, sheet_name: str, visual_title: str,
) -> None:
    """Escape closes the drawer (panel re-acquires
    ``translate-x-full``). Already covered transitively by every
    other test's teardown, but exercised explicitly here as the
    isolation contract: a stray Escape doesn't leave the panel half-
    open."""
    driver = cy9_driver
    _open_planted_panel(driver, sheet_name, visual_title)
    driver.close_metadata_panel()
    # Idempotency — calling close on an already-closed panel is a
    # no-op. The verb's wait_for_function asserts the closed-state
    # class; if we landed here without raising, we're closed.
    driver.close_metadata_panel()


@pytest.mark.parametrize(("sheet_name", "visual_title"), _SHEETS)
def test_metadata_panel_click_outside_dismisses(
    cy9_driver: App2Driver, sheet_name: str, visual_title: str,
) -> None:
    """Clicking the overlay (the dim layer behind the drawer) closes
    the panel — same UX path as Escape, different DOM event. The
    panel JS listens for ``evt.target === overlay`` in its document
    click handler (see ``_side_panel.py`` line 215).
    """
    driver = cy9_driver
    _open_planted_panel(driver, sheet_name, visual_title)
    # No renderer-agnostic verb exists for "click the overlay" —
    # this is App2-specific UI plumbing, so reach for ``driver.page``
    # per the App2Driver docstring's escape-hatch rule. Wait for the
    # closed-state class flip via the same idiom the close_metadata_panel
    # verb uses internally.
    driver.page.locator("#side-panel-overlay").click()
    driver.page.wait_for_function(
        "() => {"
        " const p = document.getElementById('side-panel');"
        " return p && p.classList.contains('translate-x-full');"
        "}",
        timeout=5_000,
    )


@pytest.mark.parametrize(("sheet_name", "visual_title"), _SHEETS)
def test_metadata_panel_row_with_empty_metadata_shows_empty_state(
    cy9_driver: App2Driver, sheet_name: str, visual_title: str,
) -> None:
    """Row 1's empty ``{}`` metadata renders the operator-locked
    empty-state fragment (``"No metadata for this row."``) — verifies
    the route handler's null-coalesce branch (``_side_panel.py`` line
    562) round-trips through the bootstrap.js wiring even though the
    raw value ``"{}"`` is truthy at the JS layer.
    """
    driver = cy9_driver
    driver.open(_DASHBOARD_ID)
    driver.goto_sheet(sheet_name)
    driver.wait_loaded(visual_title)
    driver.open_metadata_panel(visual_title, row_index=1)
    # The empty-state branch returns ONLY the italic paragraph —
    # no toolbar, no textarea, no tree. ``metadata_panel_text`` (which
    # reads ``[data-metadata-raw]``) returns "" because the textarea
    # isn't rendered.
    raw = driver.metadata_panel_text()
    assert raw == "", (
        f"empty-state branch should render no [data-metadata-raw], "
        f"got {raw!r} on sheet {sheet_name!r}"
    )
    # Confirm the empty-state copy lands in the panel body so a
    # future drift (e.g. a translation tweak or operator copy change)
    # surfaces here.
    body = driver.page.locator("#side-panel-body").first.inner_text()
    assert "No metadata for this row." in body, (
        f"empty-state fragment missing on sheet {sheet_name!r}; "
        f"panel body was: {body!r}"
    )
    # And no <details> nodes at all in the empty-state branch.
    open_count = driver.metadata_panel_open_details_count()
    assert open_count == 0, (
        f"empty-state should render no <details> nodes; got "
        f"{open_count} open on sheet {sheet_name!r}"
    )
    driver.close_metadata_panel()


# QS leg — placeholder param exists so the test file documents the
# protocol's App2-only scope at runtime. Driving the QS driver
# raises NotImplementedError per operator lock 7; `skips_if_unsupported`
# converts that to a clean skip. No QS leg fires data (no qs_driver
# fixture is used) — this is the "renderer-agnostic test with one
# renderer skipping" shape the protocol's `dialect` field documents.
def test_metadata_popup_qs_leg_skips() -> None:
    """The QS embed driver raises ``NotImplementedError`` from every
    metadata-popup verb (operator lock 7). Exercises the protocol's
    skip-path so a future "let's enable it on QS too" change has one
    place to flip + this test starts running."""
    from tests.e2e._drivers import QsEmbedDriver  # noqa: PLC0415

    # We don't need a real embed — the verb raises before touching
    # the page. Construct a thin instance directly.
    driver = QsEmbedDriver.__new__(QsEmbedDriver)
    with skips_if_unsupported():
        driver.open_metadata_panel("anything", 0)
    # If we get here the verb didn't raise — that's the regression
    # signal (operator lock 7 lifted unintentionally).
    pytest.fail(
        "QsEmbedDriver.open_metadata_panel didn't raise — operator "
        "lock 7 (metadata popup is App2-only) may have been lifted "
        "without updating this test."
    )
