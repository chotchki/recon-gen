"""X.2.m — JS unit test for the htmx:responseError toaster.

Loads the bootstrap test harness, fires a synthetic
``htmx:responseError`` (or ``htmx:sendError``) event with the
shape HTMX would produce, and asserts:

- A ``.toast`` element appears inside ``#htmx-error-toaster``.
- The toast carries the ``bg-danger`` / ``text-accent-fg`` semantic
  tokens (theme-aware).
- The HTTP status code from the synthetic event lands in the
  toast text.
- Multiple errors stack instead of replacing.
- The toast's role attribute is ``status`` (a11y).
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


def _fire_response_error(page: Any, status: int) -> None:
    """Synthesize an htmx:responseError event with the same detail
    shape HTMX produces (``detail.xhr.status`` is the field the
    listener reads). CustomEvent is enough — the listener doesn't
    depend on it being a real XHR object."""
    page.evaluate(
        """(status) => {
            var evt = new CustomEvent('htmx:responseError', {
                detail: { xhr: { status: status } },
            });
            document.body.dispatchEvent(evt);
        }""",
        status,
    )


def _fire_send_error(page: Any) -> None:
    page.evaluate("""() => {
        var evt = new CustomEvent('htmx:sendError', { detail: {} });
        document.body.dispatchEvent(evt);
    }""")


def test_response_error_surfaces_a_toast() -> None:
    """A synthetic 500 response should add one ``.toast`` element to
    the DOM, inside the toaster container."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _fire_response_error(page, 500)
        toasts = page.locator("#htmx-error-toaster .toast").count()
        text = page.locator("#htmx-error-toaster .toast").first.text_content()
        browser.close()
    assert toasts == 1
    assert text is not None
    assert "Couldn't load" in text
    # Status is mentioned so the user / dev can correlate with the
    # network panel.
    assert "500" in text


def test_toast_uses_semantic_theme_tokens() -> None:
    """Toast carries ``bg-danger`` + ``text-accent-fg`` so it picks
    up the per-instance L2 theme via the same CSS-var injection
    every other surface uses."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _fire_response_error(page, 503)
        cls = page.locator(
            "#htmx-error-toaster .toast",
        ).first.get_attribute("class")
        browser.close()
    assert cls is not None
    assert "bg-danger" in cls
    assert "text-accent-fg" in cls


def test_multiple_errors_stack_in_the_toaster() -> None:
    """Three errors in quick succession → three toasts stacked
    vertically, not replacing each other. Container is the parent;
    each toast is one child element."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _fire_response_error(page, 500)
        _fire_response_error(page, 502)
        _fire_response_error(page, 504)
        count = page.locator("#htmx-error-toaster .toast").count()
        browser.close()
    assert count == 3


def test_toast_carries_status_role_for_a11y() -> None:
    """``role="status"`` tells assistive tech the element is a
    short status message — appropriate for a transient toast."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _fire_response_error(page, 500)
        role = page.locator(
            "#htmx-error-toaster .toast",
        ).first.get_attribute("role")
        browser.close()
    assert role == "status"


def test_send_error_surfaces_network_message() -> None:
    """``htmx:sendError`` (network down before any response comes
    back) gets a different message than a response error — the
    server didn't return X, the request never made it."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _fire_send_error(page)
        text = page.locator("#htmx-error-toaster .toast").first.text_content()
        browser.close()
    assert text is not None
    assert "Network error" in text


def test_toaster_container_only_created_once() -> None:
    """``ensureToastContainer`` should be idempotent — firing many
    errors doesn't append duplicate ``#htmx-error-toaster`` divs."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        _fire_response_error(page, 500)
        _fire_response_error(page, 500)
        _fire_response_error(page, 500)
        containers = page.locator("#htmx-error-toaster").count()
        browser.close()
    assert containers == 1


def test_show_error_toast_returns_appended_node() -> None:
    """The IIFE-exposed showErrorToast hook is what a future
    page-shell-level error surface (e.g. dev-log forwarder) could
    call directly. Confirm the export is reachable + returns the
    DOM node it appended (so callers can attach extra event
    handlers if needed)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        _load_harness(page)
        # Bypass the event system — call the export directly.
        result = cast(
            dict[str, Any],
            page.evaluate(
                "() => {"
                "  var node = window.__bootstrap_internals__"
                "    .showErrorToast('Hello', 418);"
                "  return {"
                "    text: node.textContent,"
                "    inContainer: node.parentNode &&"
                "      node.parentNode.id === 'htmx-error-toaster',"
                "    cls: node.className"
                "  };"
                "}",
            ),
        )
        browser.close()
    assert result["inContainer"] is True
    assert result["text"] is not None
    assert "Hello" in result["text"]
    assert "418" in result["text"]
    assert "bg-danger" in result["cls"]
