"""X.2.c.2 — Playwright unit tests for the Table d3 renderer.

Drives ``renderTable`` against the bootstrap harness fixture +
asserts on the rendered DOM. Coverage:

- One ``<tr>`` per row, one ``<th>`` per column.
- Header label uses ``column.label`` (falls back to ``column.name``).
- Currency / number cells get ``tabular-nums text-right``.
- Pager renders ``"1–N of TOTAL"`` (1-based human counting; the
  ``page_offset`` URL param stays 0-based).
- Sort badges (▲ / ▼ / blank) match the current ``sort_column``.
- Sort link URLs cycle asc → desc → off.
- Pager Prev / Next URLs increment / decrement page_offset by
  page_size; disabled at boundaries.
- Empty rows render a 0-of-0 pager without crashing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")


_FIXTURE = (
    Path(__file__).parent / "fixtures" / "bootstrap_test_harness.html"
)


def _load_harness(page: Any) -> None:
    """Same shape as test_render_kpi._load_harness — load fixture +
    wait for the test-mode export. d3 is loaded by the fixture HTML's
    own <script src> (the vendored copy — X.2.p — not a CDN)."""
    page.goto(f"file://{_FIXTURE.resolve()}")
    page.wait_for_function(
        "() => window.__bootstrap_internals__ != null", timeout=5000,
    )
    page.wait_for_function(
        "() => typeof window.d3 !== 'undefined'", timeout=5000,
    )


def _render_into_target(
    page: Any, data: dict[str, Any], visual_id: str = "test-vid",  # typing-smell: ignore[bare-str-id]: visual_id comes from callers as raw analyst string
    fetch_url: str | None = (
        "/dashboards/x/sheets/y/visuals/test-vid/data"
    ),
) -> None:
    """Inject a ``<section>`` with ``data-fetch-url`` so renderTable
    finds it when building sort/pager URLs. The section also
    holds the ``.visual-data`` div renderTable paints into."""
    page.evaluate(
        """({ data, visualId, fetchUrl }) => {
            var prev = document.getElementById('table-host');
            if (prev) prev.remove();
            var section = document.createElement('section');
            section.id = 'table-host';
            section.setAttribute('data-visual-kind', 'Table');
            section.setAttribute('data-visual-id', visualId);
            if (fetchUrl !== null) {
                section.setAttribute('data-fetch-url', fetchUrl);
            }
            var target = document.createElement('div');
            target.id = 'visual-data-' + visualId;
            target.classList.add('visual-data');
            section.appendChild(target);
            document.body.appendChild(section);
            window.__bootstrap_internals__.renderTable(target, data, visualId);
        }""",
        {"data": data, "visualId": visual_id, "fetchUrl": fetch_url},
    )


_FIXTURE_DATA: dict[str, Any] = {
    "columns": [
        {"name": "id", "label": "ID"},
        {"name": "amount", "label": "Amount", "format": "currency"},
        {"name": "status", "label": "Status"},
    ],
    "rows": [
        ["acct-1", 1234.50, "Open"],
        ["acct-2", 5678.00, "Pending"],
        ["acct-3", 99.99, "Closed"],
    ],
    "total_rows": 1247,
    "page_offset": 0,
    "page_size": 50,
    "sort_column": "",
}


def test_table_renders_one_row_per_data_row_and_th_per_column() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _FIXTURE_DATA)
        thead_count = page.locator("#table-host table.table-data thead th").count()
        tbody_rows = page.locator("#table-host table.table-data tbody tr").count()
        browser.close()
    assert thead_count == 3
    assert tbody_rows == 3


def test_table_uses_column_label_in_header() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _FIXTURE_DATA)
        header_text = page.locator(
            "#table-host table.table-data thead th",
        ).first.inner_text()
        browser.close()
    assert "ID" in header_text


def test_table_currency_cells_get_tabular_nums_class() -> None:
    """Currency-formatted columns right-align + use tabular-nums so
    digit columns line up across rows."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _FIXTURE_DATA)
        # 2nd column (amount, currency) of 1st row.
        amount_cell = page.locator(
            "#table-host table.table-data tbody tr",
        ).first.locator("td").nth(1)
        cell_class = amount_cell.get_attribute("class") or ""
        cell_text = amount_cell.inner_text()
        browser.close()
    assert "tabular-nums" in cell_class
    assert "text-right" in cell_class
    assert cell_text.startswith("$")
    assert "1,234.50" in cell_text


def test_table_pager_renders_human_range() -> None:
    """1-based human counting: 'page_offset=0, page_size=50,
    total_rows=1247' → '1–50 of 1247'."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _FIXTURE_DATA)
        range_text = page.locator(
            "#table-host .table-pager-range",
        ).first.inner_text()
        browser.close()
    assert range_text == "1–50 of 1247"


def test_table_pager_handles_partial_last_page() -> None:
    """Last page shows the partial range correctly (e.g. offset=1200,
    size=50, total=1247 → '1201–1247 of 1247')."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        last_page = dict(_FIXTURE_DATA)
        last_page["page_offset"] = 1200
        _render_into_target(page, last_page)
        range_text = page.locator(
            "#table-host .table-pager-range",
        ).first.inner_text()
        browser.close()
    assert range_text == "1201–1247 of 1247"


def test_table_pager_prev_disabled_on_first_page() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _FIXTURE_DATA)
        prev = page.locator("#table-host .table-pager-prev").first
        prev_aria = prev.get_attribute("aria-disabled")
        prev_class = prev.get_attribute("class") or ""
        browser.close()
    assert prev_aria == "true"
    assert "cursor-not-allowed" in prev_class


def test_table_pager_next_disabled_on_last_page() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        last_page = dict(_FIXTURE_DATA)
        last_page["page_offset"] = 1200
        _render_into_target(page, last_page)
        nxt = page.locator("#table-host .table-pager-next").first
        nxt_aria = nxt.get_attribute("aria-disabled")
        nxt_class = nxt.get_attribute("class") or ""
        browser.close()
    assert nxt_aria == "true"
    assert "cursor-not-allowed" in nxt_class


def test_table_pager_next_url_increments_page_offset() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, _FIXTURE_DATA)
        nxt = page.locator("#table-host .table-pager-next").first
        href = nxt.get_attribute("href") or ""
        hxget = nxt.get_attribute("hx-get") or ""
        browser.close()
    assert "page_offset=50" in href
    assert "page_size=50" in href
    # hx-get should match href so click fires the same swap.
    assert hxget == href


def test_table_sort_badge_reflects_current_sort() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        sorted_data = dict(_FIXTURE_DATA)
        sorted_data["sort_column"] = "amount:desc"
        _render_into_target(page, sorted_data)
        badges = cast(list[str], page.evaluate("""() => Array.from(
            document.querySelectorAll('#table-host thead th .table-sort-badge'),
        ).map((b) => b.textContent || '')"""))
        browser.close()
    # 3 columns; 2nd is "amount" with desc sort → ▼.
    assert badges[0] == ""
    assert badges[1] == "▼"
    assert badges[2] == ""


def test_table_sort_link_cycles_asc_to_desc() -> None:
    """Click cycles: unsorted → asc → desc → unsorted (off)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        # Currently sorted asc on "amount" — next click should give desc.
        sorted_data = dict(_FIXTURE_DATA)
        sorted_data["sort_column"] = "amount:asc"
        _render_into_target(page, sorted_data)
        amount_link = page.locator(
            "#table-host thead th",
        ).nth(1).locator("a")
        href = amount_link.get_attribute("href") or ""
        browser.close()
    assert "sort_column=amount%3Adesc" in href or "sort_column=amount:desc" in href


def test_table_sort_link_cycles_desc_to_off() -> None:
    """desc-sorted column's next click drops the sort (off)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        sorted_data = dict(_FIXTURE_DATA)
        sorted_data["sort_column"] = "amount:desc"
        _render_into_target(page, sorted_data)
        amount_link = page.locator(
            "#table-host thead th",
        ).nth(1).locator("a")
        href = amount_link.get_attribute("href") or ""
        browser.close()
    # sort_column should be absent (or empty) on the resulting URL.
    assert "sort_column=" not in href


def test_table_renders_zero_rows_without_crashing() -> None:
    """BQ.1 — empty result set renders the empty-state banner (no
    tbody rows, no pager). Pre-BQ.1 the empty case rendered just the
    header + a "0 of 0" pager — visually a still-loading state.
    The "no crash" intent still holds; visual flipped."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        empty = dict(_FIXTURE_DATA)
        empty["rows"] = []
        empty["total_rows"] = 0
        _render_into_target(page, empty)
        body_rows = page.locator("#table-host tbody tr").count()
        empty_count = page.locator("#table-host .table-empty-state").count()
        browser.close()
    assert body_rows == 0
    assert empty_count == 1


def test_next_sort_direction_cycle() -> None:
    """The exposed nextSortDirection helper cycles correctly across
    the three states (off → asc → desc → off)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        results = cast(list[str], page.evaluate("""() => {
            var f = window.__bootstrap_internals__.nextSortDirection;
            return [
                f('amount', ''),
                f('amount', 'amount:asc'),
                f('amount', 'amount:desc'),
                f('amount', 'other:asc'),
            ];
        }"""))
        browser.close()
    assert results == [
        "amount:asc",      # off → asc
        "amount:desc",     # asc → desc
        "",                # desc → off
        "amount:asc",      # other column sorted → start fresh
    ]


def test_build_table_url_drops_empty_params() -> None:
    """Empty / null / undefined values are dropped from the URL —
    keeps the URL canonical for caching."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        url = cast(str, page.evaluate("""() => {
            return window.__bootstrap_internals__.buildTableUrl(
                '/dashboards/x/sheets/y/visuals/v/data',
                { sort_column: '', page_offset: 0, page_size: 50 },
            );
        }"""))
        browser.close()
    # sort_column was empty → dropped; page_offset 0 + page_size 50
    # both kept (zero is a valid value, not "empty").
    assert "sort_column" not in url
    assert "page_offset=0" in url
    assert "page_size=50" in url


def test_table_empty_rows_renders_empty_state_banner() -> None:
    """BQ.1 — when ``total_rows === 0``, renderTable paints the
    empty-state banner instead of just the sticky header row over
    a blank body. Pre-BQ.1 an empty table looked indistinguishable
    from a still-loading state.
    """
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _render_into_target(page, {
            "columns": [{"name": "id"}, {"name": "amount"}],
            "rows": [],
            "page_offset": 0,
            "page_size": 50,
            "total_rows": 0,
            "sort_column": "",
        })
        empty_count = page.locator(
            "#visual-data-test-vid .table-empty-state",
        ).count()
        table_count = page.locator("#visual-data-test-vid table").count()
        message = cast(str, page.evaluate(
            """() => document.querySelector(
                '#visual-data-test-vid .table-empty-state',
            )?.textContent || ''""",
        ))
        browser.close()
    assert empty_count == 1
    assert table_count == 0
    assert "No rows match" in message
