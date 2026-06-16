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
    "params": [{"name": "pL1TxTransferId", "column": "transfer_id"}],
}]

_CLICK_DRILL = [{
    "label": "Walk to this counterparty",
    "trigger": "DATA_POINT_CLICK",
    "target_path": "/dashboards/d1/sheets/account-network",
    "params": [{"name": "pInvANetworkAnchor", "column": "transfer_id"}],
}]


def test_menu_drill_adds_ellipsis_button_per_row_and_header_cell() -> None:
    """A MENU-only Table exposes its drill VIA the trailing ⋯ button per
    row (+ a corresponding header column), NOT via a row-wide left-click
    handler. The row stays non-clickable: no ``data-row-drill`` attribute,
    no cursor: pointer, no navigation on row click.

    2026-06-12 — operator dogfood (L1 Overdraft → Daily Statement): the
    whole row was a click target despite being declared as MENU-only.
    Root cause was a ``clickDrill = drills[0]`` fallback in wireRowDrills
    that promoted MENU drills to whole-row left-click. Now removed: a
    MENU drill stays MENU.
    """
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render(page, _DATA, _MENU_DRILL)
        n_btns = page.locator("#drill-host tbody tr .row-drill-menu-btn").count()
        n_drillable = page.locator("#drill-host tbody tr[data-row-drill]").count()
        n_head_extra = page.locator("#drill-host thead th.row-drill-col").count()
        # CSS guard — no cursor: pointer on MENU-only rows.
        first_row_cursor = page.locator("#drill-host tbody tr").first.evaluate(
            "(el) => getComputedStyle(el).cursor"
        )
        browser.close()
    assert n_btns == 2
    # MENU-only drill must NOT make rows left-clickable.
    assert n_drillable == 0
    assert first_row_cursor != "pointer"
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
                    { name: 'pL1TxTransferId', column: 'transfer_id' },
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
    assert urls[0] == "/d/s/t?param_pL1TxTransferId=xfr-1"
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


# -- CY.6 — metadata-popup ctxmenu entry ---------------------------------


_METADATA_DATA: dict[str, Any] = {
    # Columns include hidden `metadata` and `transaction_id` per CY.4.
    "columns": [
        {"name": "transfer_id", "label": "Transfer"},
        {"name": "transaction_id", "label": "Txn", "hidden": True},
        {"name": "amount", "label": "Amount", "format": "currency"},
        {"name": "metadata", "label": "Metadata", "hidden": True},
    ],
    "rows": [
        ["xfr-1", "txn-aaa", 1234.5, '{"k": "v"}'],
        ["xfr-2", "txn-bbb", 5678.0, ""],  # empty metadata → entry suppressed
    ],
    "total_rows": 2,
    "page_offset": 0,
    "page_size": 50,
    "sort_column": "",
}


def _render_with_metadata_popup(
    page: Any,
    data: dict[str, Any],
    row_drills: list[dict[str, Any]] | None,
    *,
    metadata_popup: bool,
    fetch_url: str = (
        "/dashboards/d-meta/sheets/s-meta/visuals/v-meta/data"
    ),
    visual_id: str = "v-meta",  # typing-smell: ignore[bare-str-id]: matches sibling _render fixture's analyst-string convention at line 48
) -> None:
    """Variant of `_render` that lets the test toggle the
    ``data-metadata-popup`` section attribute + a non-default fetch
    URL so the dashboard / sheet IDs landing in the htmx.ajax call
    can be asserted."""
    import json

    page.evaluate(
        """({ data, rowDrillsJson, metadataPopup, fetchUrl, visualId }) => {
            var prev = document.getElementById('drill-host');
            if (prev) prev.remove();
            var section = document.createElement('section');
            section.id = 'drill-host';
            section.setAttribute('data-visual-kind', 'Table');
            section.setAttribute('data-visual-id', visualId);
            section.setAttribute('data-fetch-url', fetchUrl);
            if (rowDrillsJson !== null) {
                section.setAttribute('data-row-drills', rowDrillsJson);
            }
            if (metadataPopup) {
                section.setAttribute('data-metadata-popup', '1');
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
            "rowDrillsJson": (
                json.dumps(row_drills) if row_drills is not None else None
            ),
            "metadataPopup": metadata_popup,
            "fetchUrl": fetch_url,
            "visualId": visual_id,
        },
    )


def test_metadata_popup_alone_wires_ellipsis_column() -> None:
    """A Table with no `data-row-drills` but `data-metadata-popup="1"`
    still gets the ⋯ menu column — the synthetic "{} View metadata"
    entry is the only menu item; rows are NOT left-click drillable."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_with_metadata_popup(
            page, _METADATA_DATA, None, metadata_popup=True,
        )
        n_btns = page.locator(
            "#drill-host tbody tr .row-drill-menu-btn",
        ).count()
        n_drillable = page.locator(
            "#drill-host tbody tr[data-row-drill]",
        ).count()
        n_head_extra = page.locator(
            "#drill-host thead th.row-drill-col",
        ).count()
        browser.close()
    assert n_btns == 2
    # metadata-popup-only: rows are NOT left-click navigable.
    assert n_drillable == 0
    assert n_head_extra == 1


def test_metadata_popup_menu_entry_prepended_when_row_has_metadata() -> None:
    """Capture the items `openRowMenu` passes to `ctxmenu.show` and
    verify (1) the synthetic "{} View metadata" entry is prepended,
    (2) the entry carries the row's metadata + transaction_id, and
    (3) action() fires `htmx.ajax` with the per-route URL shape from
    the spec."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_with_metadata_popup(
            page, _METADATA_DATA, _MENU_DRILL, metadata_popup=True,
        )
        # Stub ctxmenu.show + htmx.ajax to capture invocations.
        page.evaluate("""() => {
            window.__ctx_items__ = null;
            window.ctxmenu = { show: (items, _el) => {
                window.__ctx_items__ = items;
            }};
            window.__ajax_calls__ = [];
            window.htmx = window.htmx || {};
            window.htmx.ajax = (verb, url, opts) => {
                window.__ajax_calls__.push({ verb: verb, url: url, opts: opts });
                return Promise.resolve();
            };
            // Fire the ⋯ button for the first row (metadata present).
            var btn = document.querySelector(
                '#drill-host tbody tr:nth-child(1) .row-drill-menu-btn',
            );
            btn.click();
        }""")
        items = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__ctx_items__"),
        )
        assert items is not None
        # First item is the metadata entry; second is the original drill.
        assert items[0]["text"] == "{} View metadata"
        assert items[0]["kind"] == "metadata_popup"
        assert items[0]["metadata"] == '{"k": "v"}'
        assert items[0]["transaction_id"] == "txn-aaa"
        assert items[1]["text"] == "View Transactions for this transfer"
        # Fire the action — captures the htmx call.
        page.evaluate("() => window.__ctx_items__[0].action()")
        calls = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__ajax_calls__"),
        )
        browser.close()
    assert len(calls) == 1
    assert calls[0]["verb"] == "GET"
    assert calls[0]["url"] == (
        "/dashboards/d-meta/sheets/s-meta/rows/metadata"
        "?metadata=%7B%22k%22%3A%20%22v%22%7D"
        "&transaction_id=txn-aaa"
    )
    assert calls[0]["opts"]["target"] == "#side-panel-body"
    assert calls[0]["opts"]["swap"] == "innerHTML"


def test_metadata_popup_entry_suppressed_when_row_metadata_empty() -> None:
    """Row 2 of `_METADATA_DATA` has metadata == ""; the synthetic
    entry should NOT appear in the menu — only the original drill."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_with_metadata_popup(
            page, _METADATA_DATA, _MENU_DRILL, metadata_popup=True,
        )
        page.evaluate("""() => {
            window.__ctx_items__ = null;
            window.ctxmenu = { show: (items) => {
                window.__ctx_items__ = items;
            }};
            var btn = document.querySelector(
                '#drill-host tbody tr:nth-child(2) .row-drill-menu-btn',
            );
            btn.click();
        }""")
        items = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__ctx_items__"),
        )
        browser.close()
    assert items is not None
    assert len(items) == 1
    assert items[0]["text"] == "View Transactions for this transfer"


def test_dash_sheet_from_fetch_url_parses_typical_url() -> None:
    """Unit-test the URL-segment parser used by openRowMenu to lift
    the dash + sheet ids out of `data-fetch-url`."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        result = cast(dict[str, Any], page.evaluate("""() => {
            var f = window.__bootstrap_internals__.dashSheetFromFetchUrl;
            return {
                good: f('/dashboards/d-1/sheets/s-1/visuals/v-1/data'),
                empty: f(''),
                wrong: f('/something/else'),
                null_arg: f(null),
            };
        }"""))
        browser.close()
    assert result["good"] == {"dash": "d-1", "sheet": "s-1"}
    assert result["empty"] is None
    assert result["wrong"] is None
    assert result["null_arg"] is None


# ---------------------------------------------------------------------------
# Phase DA — per-column drill decoration on Table cells
# ---------------------------------------------------------------------------


_DECO_DATA: dict[str, Any] = {
    "columns": [
        {"name": "account_id", "label": "Account", "decoration": "accent-menu"},
        {"name": "amount", "label": "Amount", "format": "currency"},
    ],
    "rows": [
        ["acct-1", 1234.5],
        ["acct-2", 5678.0],
    ],
    "total_rows": 2,
    "page_offset": 0,
    "page_size": 50,
    "sort_column": "",
}

_DECO_DATA_CLICK: dict[str, Any] = {
    "columns": [
        {"name": "account_id", "label": "Account", "decoration": "accent"},
        {"name": "amount", "label": "Amount", "format": "currency"},
    ],
    "rows": [
        ["acct-1", 1234.5],
    ],
    "total_rows": 1,
    "page_offset": 0,
    "page_size": 50,
    "sort_column": "",
}


def test_render_table_paints_cell_accent_menu_class() -> None:
    """Phase DA — `renderTable` reads `col.decoration` and adds the
    matching CSS class to every `<td>` in that column. `account_id`
    cells with `decoration: "accent-menu"` carry `cell-accent-menu`;
    the un-decorated `amount` column has neither class."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render(page, _DECO_DATA, _MENU_DRILL)
        n_menu_cells = page.locator(
            "#drill-host tbody td.cell-accent-menu"
        ).count()
        n_plain_cells = page.locator(
            "#drill-host tbody td.cell-accent"
        ).count()
        # Amount column carries no decoration → no class.
        amount_cell_classes = page.locator(
            "#drill-host tbody tr:nth-child(1) td:nth-child(2)"
        ).get_attribute("class")
        browser.close()
    # 2 rows × 1 menu-decorated column = 2 cells.
    assert n_menu_cells == 2
    # accent-only column not present in this fixture.
    assert n_plain_cells == 0
    assert amount_cell_classes is not None
    assert "cell-accent" not in amount_cell_classes


def test_render_table_paints_cell_accent_class_for_click_decoration() -> None:
    """Phase DA — `decoration: "accent"` (CLICK-only column) paints
    `cell-accent`, NOT `cell-accent-menu`. Distinct CSS hooks for the
    two click idioms."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render(page, _DECO_DATA_CLICK, _CLICK_DRILL)
        n_accent = page.locator(
            "#drill-host tbody td.cell-accent"
        ).count()
        # Don't confuse the substring match — assert the menu variant absent.
        n_menu = page.locator(
            "#drill-host tbody td.cell-accent-menu"
        ).count()
        browser.close()
    assert n_accent == 1
    assert n_menu == 0


def test_cell_accent_menu_left_click_opens_menu() -> None:
    """Phase DA — left-click on a `.cell-accent-menu` cell opens the row
    menu via the same `openRowMenu` code path the ⋯ button calls.
    Operator-locked exception to "left moves LEFT" because the explicit
    visual cue makes the affordance discoverable.

    `e.stopPropagation()` prevents the row-level click drill (if any)
    from firing — protects the Class B case where the same column carries
    both CLICK + MENU drills (without stopPropagation the row handler's
    navigation would preempt the menu paint)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render(page, _DECO_DATA, _MENU_DRILL)
        # Stub ctxmenu.show to capture the items the menu would render.
        page.evaluate("""() => {
            window.__ctx_items__ = null;
            window.ctxmenu = { show: (items) => {
                window.__ctx_items__ = items;
            }};
            var cell = document.querySelector(
                '#drill-host tbody tr:nth-child(1) td.cell-accent-menu',
            );
            cell.click();
        }""")
        items = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__ctx_items__"),
        )
        browser.close()
    # The menu opened with the MENU drill's label as the first (and only) item.
    assert items is not None
    assert len(items) == 1
    assert items[0]["text"] == "View Transactions for this transfer"


def test_cell_accent_menu_click_stops_propagation_to_row_handler() -> None:
    """Phase DA — when the Table also has a CLICK drill (Class B mix
    case — both CLICK + MENU write from the same column), cell-click on
    a menu-decorated cell opens the menu and STOPS propagation so the
    row-level click drill (`tr.addEventListener('click', navigate)`)
    does not fire.

    Verified by installing a sibling click listener on the row that
    increments a counter; the bootstrap's row handler and our test
    listener are both on the `<tr>`, so they both fire only if the
    click event bubbles up from the `<td>`. `stopPropagation()` on the
    cell means neither fires."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        # Both CLICK + MENU drills on the same column.
        drills = [
            {
                "label": "Walk to account",
                "trigger": "DATA_POINT_CLICK",
                "target_path": "/dashboards/d1/sheets/accounts",
                "params": [{"name": "pAcct", "column": "account_id"}],
            },
            {
                "label": "View transactions",
                "trigger": "DATA_POINT_MENU",
                "target_path": "/dashboards/d1/sheets/transactions",
                "params": [{"name": "pTxAcct", "column": "account_id"}],
            },
        ]
        _render(page, _DECO_DATA, drills)
        result = cast(dict[str, Any], page.evaluate("""() => {
            window.__ctx_items__ = null;
            window.ctxmenu = { show: (items) => {
                window.__ctx_items__ = items;
            }};
            var tr = document.querySelector('#drill-host tbody tr:nth-child(1)');
            // Sibling listener on the row — fires if the click bubbles.
            var rowBubbleCount = 0;
            tr.addEventListener('click', () => { rowBubbleCount++; });
            var cell = tr.querySelector('td.cell-accent-menu');
            cell.click();
            return {
                items: window.__ctx_items__,
                rowBubbleCount: rowBubbleCount,
            };
        }"""))
        browser.close()
    # Menu opened with both drills available.
    items = result["items"]
    assert items is not None
    assert len(items) == 2
    # The cell's stopPropagation prevented the click from reaching <tr>.
    assert result["rowBubbleCount"] == 0
