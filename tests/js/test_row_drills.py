"""u.4.e.3 — Playwright unit tests for App 2's row-level drill wiring.

Drives ``wireRowDrills`` / ``rowDrillUrl`` against the bootstrap harness
fixture. Coverage:

- A table whose visual carries a ``DATA_POINT_MENU`` drill renders one
  trailing ``.row-drill-menu-btn`` per row + a ``.row-drill-col`` header
  cell; every ``<tr>`` becomes a left-click target (``data-row-drill``).
- A table with only a ``DATA_POINT_CLICK`` drill: rows are clickable but
  there's no "⋯" column (no menu drills).
- A table whose ``<section>`` has no ``data-row-drills`` attribute is
  left untouched (no decoration, no crash).
- ``rowDrillUrl`` resolves ``params`` against the row's cells (by column
  name, case-insensitive); a param whose source column isn't present is
  dropped; a drill with no resolvable params navigates to the bare
  ``target_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")


_FIXTURE = Path(__file__).parent / "fixtures" / "bootstrap_test_harness.html"


def _load_harness(page: Any) -> None:
    page.goto(f"file://{_FIXTURE.resolve()}")
    page.wait_for_function(
        "() => window.__bootstrap_internals__ != null", timeout=5000,
    )
    # d3 is loaded by the fixture HTML's own <script src> (the vendored
    # copy — X.2.p — not a CDN), ahead of bootstrap.js; this is just a
    # defensive wait that it landed.
    page.wait_for_function(
        "() => typeof window.d3 !== 'undefined'", timeout=5000,
    )


def _render(
    page: Any, data: dict[str, Any], row_drills: list[dict[str, Any]] | None,
    visual_id: str = "drill-vid",  # typing-smell: ignore[bare-str-id]: visual_id comes from callers as raw analyst string
) -> None:
    """Inject a ``<section data-row-drills=...>`` carrying a Table, run
    ``renderTable`` then ``wireRowDrills`` exactly as ``hydrateSection``
    does for a real swap."""
    import json

    page.evaluate(
        """({ data, rowDrillsJson, visualId }) => {
            var prev = document.getElementById('drill-host');
            if (prev) prev.remove();
            var section = document.createElement('section');
            section.id = 'drill-host';
            section.setAttribute('data-visual-kind', 'Table');
            section.setAttribute('data-visual-id', visualId);
            section.setAttribute(
                'data-fetch-url',
                '/dashboards/x/sheets/y/visuals/' + visualId + '/data',
            );
            if (rowDrillsJson !== null) {
                section.setAttribute('data-row-drills', rowDrillsJson);
            }
            var target = document.createElement('div');
            target.id = 'visual-data-' + visualId;
            target.classList.add('visual-data');
            section.appendChild(target);
            document.body.appendChild(section);
            window.__bootstrap_internals__.renderTable(target, data, visualId);
            window.__bootstrap_internals__.wireRowDrills(section, target, data);
        }""",
        {
            "data": data,
            "rowDrillsJson": json.dumps(row_drills) if row_drills is not None else None,
            "visualId": visual_id,
        },
    )


_DATA: dict[str, Any] = {
    "columns": [
        {"name": "transfer_id", "label": "Transfer"},
        {"name": "amount", "label": "Amount", "format": "currency"},
        {"name": "status", "label": "Status"},
    ],
    "rows": [
        ["xfr-1", 1234.5, "Pending"],
        ["xfr-2", 5678.0, "Pending"],
    ],
    "total_rows": 2,
    "page_offset": 0,
    "page_size": 50,
    "sort_column": "",
}

_MENU_DRILL = [{
    "label": "View Transactions for this transfer",
    "trigger": "DATA_POINT_MENU",
    "target_path": "/dashboards/d1/sheets/transactions",
    "params": [{"name": "pL1TxTransfer", "column": "transfer_id"}],
}]

_CLICK_DRILL = [{
    "label": "Walk to this counterparty",
    "trigger": "DATA_POINT_CLICK",
    "target_path": "/dashboards/d1/sheets/account-network",
    "params": [{"name": "pInvANetworkAnchor", "column": "transfer_id"}],
}]


def test_menu_drill_adds_ellipsis_button_per_row_and_header_cell() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render(page, _DATA, _MENU_DRILL)
        n_btns = page.locator("#drill-host tbody tr .row-drill-menu-btn").count()
        n_drillable = page.locator("#drill-host tbody tr[data-row-drill]").count()
        n_head_extra = page.locator("#drill-host thead th.row-drill-col").count()
        browser.close()
    assert n_btns == 2
    assert n_drillable == 2
    assert n_head_extra == 1


def test_click_only_drill_makes_rows_clickable_without_ellipsis_column() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render(page, _DATA, _CLICK_DRILL)
        n_drillable = page.locator("#drill-host tbody tr[data-row-drill]").count()
        n_btns = page.locator("#drill-host .row-drill-menu-btn").count()
        n_head_extra = page.locator("#drill-host thead th.row-drill-col").count()
        browser.close()
    assert n_drillable == 2
    assert n_btns == 0
    assert n_head_extra == 0


def test_table_without_row_drills_attr_is_untouched() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render(page, _DATA, None)
        n_drillable = page.locator("#drill-host tbody tr[data-row-drill]").count()
        n_btns = page.locator("#drill-host .row-drill-menu-btn").count()
        n_rows = page.locator("#drill-host tbody tr").count()
        browser.close()
    assert n_drillable == 0
    assert n_btns == 0
    assert n_rows == 2  # the table itself still rendered fine


def test_row_drill_url_resolves_params_against_row_cells() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        urls = cast(list[str], page.evaluate("""() => {
            var f = window.__bootstrap_internals__.rowDrillUrl;
            var colIndex = { transfer_id: 0, amount: 1, status: 2 };
            return [
                f({ target_path: '/d/s/t', params: [
                    { name: 'pL1TxTransfer', column: 'transfer_id' },
                ]}, ['xfr-1', 100, 'Pending'], colIndex),
                // column not in the row → param dropped → bare path
                f({ target_path: '/d/s/t', params: [
                    { name: 'pX', column: 'not_here' },
                ]}, ['xfr-1', 100, 'Pending'], colIndex),
                // no params at all → bare path
                f({ target_path: '/d/s/t', params: [] },
                  ['xfr-1', 100, 'Pending'], colIndex),
            ];
        }"""))
        browser.close()
    assert urls[0] == "/d/s/t?param_pL1TxTransfer=xfr-1"
    assert urls[1] == "/d/s/t"
    assert urls[2] == "/d/s/t"


def test_row_drill_url_column_match_is_case_insensitive() -> None:
    """Oracle returns uppercased column names; the client matches the
    drill's declared (lowercase tree) column against them case-folded."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        url = cast(str, page.evaluate("""() => {
            var f = window.__bootstrap_internals__.rowDrillUrl;
            // colIndex keys are already lower-cased by wireRowDrills, so
            // simulate that: an Oracle "TRANSFER_ID" column → "transfer_id".
            var colIndex = { transfer_id: 0 };
            return f({ target_path: '/d/s/t', params: [
                { name: 'pTx', column: 'TRANSFER_ID' },
            ]}, ['xfr-9'], colIndex);
        }"""))
        browser.close()
    assert url == "/d/s/t?param_pTx=xfr-9"
