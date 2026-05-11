"""X.2.d / X.2.l.4 — JS unit tests for the CategoryFilter
``<select multiple>`` → hidden-input sync.

``ParameterDropdown`` and ``ParameterMultiSelect`` are plain
``<select>`` elements that HTMX form-serialization carries straight
to the wire (Tom Select just enhances the chrome — the underlying
``<select>``'s ``selectedOptions`` are still what gets serialized).
``CategoryFilter`` is the one that needs JS glue: its visible widget
is an un-named ``<select multiple data-category-select>`` (so HTMX
won't serialize it) feeding a hidden ``filter_<col>`` input as a
comma-joined string. This file verifies that sync — ``wireCategoryFilters``
listens for ``change`` on the select and rewrites the hidden input.

Loads the bootstrap test harness, builds a ``.category-filter``
wrapper inline, calls ``wireCategoryFilters``, drives the select, and
asserts the hidden input follows.
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
    page.goto(f"file://{_FIXTURE.resolve()}")
    page.wait_for_function(
        "() => window.__bootstrap_internals__ != null", timeout=5000,
    )


def _build_wrapper(page: Any, options: list[str]) -> None:
    """Inject a ``.category-filter`` wrapper (hidden input + un-named
    ``<select multiple data-category-select>``) with the given options
    + call ``wireCategoryFilters`` so the listener gets attached."""
    page.evaluate(
        """(opts) => {
            var prev = document.getElementById('cf-target');
            if (prev) prev.remove();
            var div = document.createElement('div');
            div.id = 'cf-target';
            div.className = 'category-filter';
            div.setAttribute('data-filter-name', 'filter_status');
            var hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = 'filter_status';
            hidden.value = '';
            div.appendChild(hidden);
            var select = document.createElement('select');
            select.multiple = true;
            select.setAttribute('data-category-select', '');
            opts.forEach(function(opt) {
                var o = document.createElement('option');
                o.value = opt;
                o.textContent = opt;
                select.appendChild(o);
            });
            div.appendChild(select);
            document.body.appendChild(div);
            window.__bootstrap_internals__.wireCategoryFilters(document);
        }""",
        options,
    )


def _hidden_value(page: Any) -> str:
    return cast(str, page.evaluate(
        '() => document.querySelector("#cf-target input[type=\\"hidden\\"]").value',
    ))


def _set_selected(page: Any, value: str, selected: bool) -> None:
    """Toggle the ``<option>`` with the given value, dispatching the
    ``change`` event ``wireCategoryFilters`` subscribes to."""
    page.evaluate(
        """([v, sel]) => {
            var sl = document.querySelector('#cf-target select[data-category-select]');
            var opt = sl.querySelector('option[value="' + v + '"]');
            opt.selected = sel;
            sl.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        [value, selected],
    )


def test_selecting_one_option_sets_hidden_to_that_value() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _build_wrapper(page, ["open", "closed", "pending"])
        _set_selected(page, "open", True)
        result = _hidden_value(page)
        browser.close()
    assert result == "open"


def test_selecting_two_options_joins_with_comma() -> None:
    """The PLAN.md X.2.d URL contract is comma-joined values."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _build_wrapper(page, ["open", "closed", "pending"])
        _set_selected(page, "open", True)
        _set_selected(page, "closed", True)
        result = _hidden_value(page)
        browser.close()
    # ``selectedOptions`` is DOM order, not click order.
    assert result == "open,closed"


def test_deselecting_removes_value_from_join() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _build_wrapper(page, ["open", "closed"])
        _set_selected(page, "open", True)
        _set_selected(page, "closed", True)
        _set_selected(page, "open", False)
        result = _hidden_value(page)
        browser.close()
    assert result == "closed"


def test_nothing_selected_leaves_hidden_empty() -> None:
    """Empty value is the "all" semantic — server treats absent /
    empty as no filter."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _build_wrapper(page, ["open", "closed"])
        result = _hidden_value(page)  # initial state, no interaction
        browser.close()
    assert result == ""


def test_deselecting_all_returns_to_empty() -> None:
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _build_wrapper(page, ["open", "closed"])
        _set_selected(page, "open", True)
        _set_selected(page, "open", False)
        result = _hidden_value(page)
        browser.close()
    assert result == ""


def test_wire_is_idempotent_via_data_wired_flag() -> None:
    """Calling ``wireCategoryFilters`` twice on the same DOM shouldn't
    double-bind the change listener — the ``data-wired`` flag protects
    it. Playwright can't introspect listeners; the flag + a stable
    single-update result is the contract."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _build_wrapper(page, ["open"])
        page.evaluate(
            "() => window.__bootstrap_internals__.wireCategoryFilters(document)",
        )
        _set_selected(page, "open", True)
        wired_attr = cast(str, page.evaluate(
            '() => document.querySelector("#cf-target").dataset.wired',
        ))
        result = _hidden_value(page)
        browser.close()
    assert wired_attr == "1"
    assert result == "open"


def test_three_separate_filters_on_one_page_each_track_independently() -> None:
    """Multiple .category-filter wrappers on the same page (e.g. one
    for status + one for region) each have their own hidden input +
    select state."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        page.evaluate("""() => {
            ['status', 'region', 'tier'].forEach(function(col, i) {
                var div = document.createElement('div');
                div.id = 'cf-' + col;
                div.className = 'category-filter';
                var hidden = document.createElement('input');
                hidden.type = 'hidden';
                hidden.name = 'filter_' + col;
                hidden.value = '';
                div.appendChild(hidden);
                var select = document.createElement('select');
                select.multiple = true;
                select.setAttribute('data-category-select', '');
                var o = document.createElement('option');
                o.value = 'v' + i;
                o.textContent = 'v' + i;
                select.appendChild(o);
                div.appendChild(select);
                document.body.appendChild(div);
            });
            window.__bootstrap_internals__.wireCategoryFilters(document);
        }""")
        # Touch only the status filter's select.
        page.evaluate("""() => {
            var sl = document.querySelector('#cf-status select[data-category-select]');
            sl.options[0].selected = true;
            sl.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        status_v = cast(str, page.evaluate(
            '() => document.querySelector("#cf-status input[type=\\"hidden\\"]").value',
        ))
        region_v = cast(str, page.evaluate(
            '() => document.querySelector("#cf-region input[type=\\"hidden\\"]").value',
        ))
        browser.close()
    assert status_v == "v0"
    assert region_v == ""
