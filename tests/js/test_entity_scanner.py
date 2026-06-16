"""Real-browser detection tests for the DK.11 literal-HTML-entity scanner
(``assert_no_literal_html_entities``).

The Python raise/return contract is covered by
``tests/unit/test_browser_helpers.py`` with a fake page; these run the
actual JS body in WebKit so the DOM-walk + skip-tag + quote-family logic is
exercised against real rendered HTML.

Operator 2026-06-16: the scanner was blind to a double-escaped quote entity
inside a markdown ``code`` span — the L1 Pending Aging description's
``` `status='Pending'` ``` rendered a literal ``&#x27;`` because
``html.escape(quote=True)`` over-escaped the apostrophe and python-markdown
re-escaped the ``&`` inside the code span. ``<code>``/``<pre>`` are skipped
for the broad entity set (doc pages render ``&amp;`` / ``&lt;``
legitimately), so the bug slipped through. These pin that the quote family
now pierces that skip while the broad set still does not.
"""

from __future__ import annotations

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")

from recon_gen.common.browser.helpers import (  # noqa: E402
    assert_no_literal_html_entities,
)


def test_scanner_hits_quote_entity_inside_code_span() -> None:
    """A double-escaped quote entity inside a ``<code>`` span (the Pending
    Aging ``status='Pending'`` case) must be flagged even though ``<code>``
    is in the skip set — quote-family entities are never legitimate as
    visible text."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        # Source HTML carries ``&amp;#x27;`` (double-encoded); the browser
        # decodes one layer so textContent is the literal ``&#x27;`` an
        # operator actually sees — exactly what the App2 bug produced.
        page.set_content(
            "<body><p class='subtitle'>Transactions stuck in "
            "<code>status=&amp;#x27;Pending&amp;#x27;</code></p></body>"
        )
        with pytest.raises(RuntimeError) as exc:
            assert_no_literal_html_entities(
                page, context="wait_loaded('Pending Aging')",
            )
        browser.close()
    msg = str(exc.value)
    assert "&#x27;" in msg
    # The offending element (a skipped tag the quote family pierces) is
    # surfaced so the failure is actionable.
    assert "CODE" in msg
    assert "wait_loaded('Pending Aging')" in msg


def test_scanner_still_skips_nonquote_entity_in_code() -> None:
    """The ``<code>``/``<pre>`` skip still holds for the broad entity
    family: a doc block rendering a literal ``&lt;`` (a page explaining
    HTML) must not trip the scan — only the quote family pierces the
    skip."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        # ``&amp;lt;`` decodes one layer to the literal ``&lt;`` — a doc
        # page legitimately showing the less-than entity inside <pre>.
        page.set_content(
            "<body><pre>In HTML, &amp;lt; renders as a less-than sign</pre>"
            "</body>"
        )
        # Must NOT raise — non-quote entity inside a skipped subtree.
        assert_no_literal_html_entities(page, context="open")
        browser.close()
