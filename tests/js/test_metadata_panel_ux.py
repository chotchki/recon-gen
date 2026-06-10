"""CY.6 — Playwright unit tests for the metadata side-panel UX hooks
in bootstrap.js: Copy button, Expand all / Collapse all buttons, and
the Ctrl+E / Ctrl+Shift+E keyboard shortcuts scoped to
``#side-panel-body``.

The fragment shape under test matches what
``recon_gen.common.html._side_panel.render_metadata_panel`` emits.
Coverage:

- Copy button reads ``[data-metadata-raw]`` (the hidden textarea),
  writes via ``navigator.clipboard.writeText`` (stubbed), and
  flashes the button label to "Copied!" for 1.5s on success.
- Expand all sets the ``open`` attribute on every
  ``[data-json-node]`` inside ``#side-panel-body``.
- Collapse all clears the ``open`` attribute on every
  ``[data-json-node]`` inside ``#side-panel-body``.
- Ctrl+E inside the panel triggers Expand all; outside the panel
  it is a no-op (doesn't hijack global shortcuts).
- Ctrl+Shift+E inside the panel triggers Collapse all.
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


_PANEL_FRAGMENT = """
<div id="side-panel-body">
  <div class="metadata-panel">
    <header>
      <h3>Row metadata · txn-1</h3>
      <div>
        <button type="button" data-metadata-copy>Copy</button>
        <span data-metadata-copy-live aria-live="polite"></span>
        <button type="button" data-metadata-expand-all>Expand all</button>
        <button type="button" data-metadata-collapse-all>Collapse all</button>
      </div>
    </header>
    <textarea data-metadata-raw hidden>{"k1": "v1", "nested": {"a": 1}}</textarea>
    <div class="metadata-tree">
      <details data-json-node open><summary>"k1"</summary><div>v1</div></details>
      <details data-json-node open><summary>"k2"</summary>
        <div>
          <details data-json-node open><summary>"a"</summary><div>1</div></details>
          <details data-json-node><summary>"b"</summary><div>2</div></details>
        </div>
      </details>
      <details data-json-node><summary>"k3"</summary><div>3</div></details>
    </div>
  </div>
</div>
"""


def _inject_panel(page: Any) -> None:
    """Drop the metadata-panel fragment into the page so the delegated
    bootstrap.js click + keydown listeners (wired at DOMContentLoaded)
    pick it up."""
    page.evaluate(
        """(fragment) => {
            var prev = document.getElementById('side-panel-body');
            if (prev) prev.remove();
            var wrapper = document.createElement('div');
            wrapper.innerHTML = fragment;
            document.body.appendChild(wrapper.firstElementChild);
        }""",
        _PANEL_FRAGMENT,
    )


def test_copy_button_writes_textarea_value_to_clipboard_and_flashes() -> None:
    """Click the Copy button → bootstrap calls
    ``navigator.clipboard.writeText`` with the textarea's value, then
    the button label briefly flashes "Copied!"."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _inject_panel(page)
        # Stub navigator.clipboard so the test runs without a
        # real OS-level clipboard.
        page.evaluate("""() => {
            window.__clipboard_writes__ = [];
            Object.defineProperty(navigator, 'clipboard', {
                configurable: true,
                value: {
                    writeText: (txt) => {
                        window.__clipboard_writes__.push(txt);
                        return Promise.resolve();
                    },
                },
            });
        }""")
        page.locator("[data-metadata-copy]").click()
        # The flash is in a microtask after the writeText promise
        # resolves; give the page a tick to apply.
        page.wait_for_function(
            "() => document.querySelector("
            "'[data-metadata-copy]').textContent === 'Copied!'",
            timeout=2000,
        )
        flashed = cast(
            str,
            page.evaluate(
                "() => document.querySelector("
                "'[data-metadata-copy]').textContent",
            ),
        )
        writes = cast(
            list[str],
            page.evaluate("() => window.__clipboard_writes__"),
        )
        live = cast(
            str,
            page.evaluate(
                "() => document.querySelector("
                "'[data-metadata-copy-live]').textContent",
            ),
        )
        browser.close()
    assert flashed == "Copied!"
    assert writes == ['{"k1": "v1", "nested": {"a": 1}}']
    # aria-live region carries the flashed message too.
    assert live == "Copied!"


def test_expand_all_opens_every_json_node() -> None:
    """Click Expand all → every ``[data-json-node]`` inside
    ``#side-panel-body`` gains the ``open`` attribute."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _inject_panel(page)
        # Start state: 1 closed node (k3 + nested b).
        before = page.locator(
            "#side-panel-body [data-json-node][open]",
        ).count()
        page.locator("[data-metadata-expand-all]").click()
        # Bulk toggle batches in rAF — wait for the mutation to apply.
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll("
            "'#side-panel-body [data-json-node]'"
            ")).every(n => n.hasAttribute('open'))",
            timeout=2000,
        )
        after = page.locator(
            "#side-panel-body [data-json-node][open]",
        ).count()
        total = page.locator(
            "#side-panel-body [data-json-node]",
        ).count()
        browser.close()
    assert before < total
    assert after == total
    assert total == 5  # k1, k2 (parent), k2.a, k2.b, k3


def test_collapse_all_closes_every_json_node() -> None:
    """Click Collapse all → every ``[data-json-node]`` inside
    ``#side-panel-body`` loses the ``open`` attribute."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _inject_panel(page)
        page.locator("[data-metadata-collapse-all]").click()
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll("
            "'#side-panel-body [data-json-node]'"
            ")).every(n => !n.hasAttribute('open'))",
            timeout=2000,
        )
        open_count = page.locator(
            "#side-panel-body [data-json-node][open]",
        ).count()
        browser.close()
    assert open_count == 0


def test_ctrl_e_inside_panel_expands_all() -> None:
    """Ctrl+E with focus inside ``#side-panel-body`` fires Expand all."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _inject_panel(page)
        page.evaluate("""() => {
            // Focus the Copy button — sits inside #side-panel-body.
            document.querySelector('[data-metadata-copy]').focus();
            // Synthesize Ctrl+E.
            var ev = new KeyboardEvent('keydown', {
                key: 'e',
                ctrlKey: true,
                bubbles: true,
                cancelable: true,
            });
            document.dispatchEvent(ev);
        }""")
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll("
            "'#side-panel-body [data-json-node]'"
            ")).every(n => n.hasAttribute('open'))",
            timeout=2000,
        )
        total_open = page.locator(
            "#side-panel-body [data-json-node][open]",
        ).count()
        browser.close()
    assert total_open == 5


def test_ctrl_shift_e_inside_panel_collapses_all() -> None:
    """Ctrl+Shift+E with focus inside ``#side-panel-body`` fires
    Collapse all."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _inject_panel(page)
        page.evaluate("""() => {
            document.querySelector('[data-metadata-copy]').focus();
            var ev = new KeyboardEvent('keydown', {
                key: 'E',
                ctrlKey: true,
                shiftKey: true,
                bubbles: true,
                cancelable: true,
            });
            document.dispatchEvent(ev);
        }""")
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll("
            "'#side-panel-body [data-json-node]'"
            ")).every(n => !n.hasAttribute('open'))",
            timeout=2000,
        )
        open_count = page.locator(
            "#side-panel-body [data-json-node][open]",
        ).count()
        browser.close()
    assert open_count == 0


def test_ctrl_e_outside_panel_is_noop() -> None:
    """Ctrl+E with focus OUTSIDE ``#side-panel-body`` does not change
    any ``data-json-node`` state — the shortcut never hijacks global
    keyboard input."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _inject_panel(page)
        # Add an unrelated focusable element OUTSIDE the panel.
        page.evaluate("""() => {
            var input = document.createElement('input');
            input.id = 'outside-input';
            document.body.appendChild(input);
            input.focus();
        }""")
        # Snapshot the initial open counts.
        before_open = page.locator(
            "#side-panel-body [data-json-node][open]",
        ).count()
        page.evaluate("""() => {
            var ev = new KeyboardEvent('keydown', {
                key: 'e',
                ctrlKey: true,
                bubbles: true,
                cancelable: true,
            });
            document.dispatchEvent(ev);
        }""")
        # No rAF to wait for — assert no change after a brief settle.
        page.wait_for_timeout(50)
        after_open = page.locator(
            "#side-panel-body [data-json-node][open]",
        ).count()
        browser.close()
    assert before_open == after_open
