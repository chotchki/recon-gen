"""Playwright unit tests for bootstrap.js's wireDataGenerationPoller (X.4.g.12.c).

The poller reads the server's render-time data_generation_id from a
``<meta name="data-generation-id">`` tag, polls
``GET /data_generation_id``, and reloads the page when the server's
value advances past the captured baseline.

Tests stub ``window.fetch`` (record + control response) and
``window.location.reload`` (record only — actual reload would tear
the page down) so each scenario runs in isolation under controlled
state. The poller's setInterval timer is left running; tests assert
behavior by directly invoking ``wireDataGenerationPoller()`` again
after swapping the stub response — that's faster than waiting on
the 3s interval AND deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")


_FIXTURE = (
    Path(__file__).parent / "fixtures" / "data_generation_poller_harness.html"
)


def _open_fixture(p: Any) -> tuple[Any, Any]:
    browser = p.webkit.launch(headless=True)
    page = browser.new_page()
    page.goto(f"file://{_FIXTURE.resolve()}")
    page.wait_for_function(
        "() => window.__bootstrap_internals__ != null",
        timeout=5000,
    )
    return browser, page


def test_poller_polls_data_generation_id_endpoint() -> None:
    """``wireDataGenerationPoller`` calls ``fetch('/data_generation_id')``
    on each pollOnce() — that's how it learns the server's current
    counter. The DOMContentLoaded path fires once; the meta-tag
    presence is what gates whether a fetch happens at all."""
    with playwright_sync_api.sync_playwright() as p:
        browser, page = _open_fixture(p)
        # DOMContentLoaded already fired pollOnce once; let the
        # microtask queue drain so the fetch promise resolves.
        page.wait_for_function(
            "() => window.__fetch_calls__.length >= 1",
            timeout=5000,
        )
        calls = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__fetch_calls__"),
        )
        browser.close()

    assert len(calls) >= 1
    assert calls[0]["url"] == "/data_generation_id"
    # cache: no-store so a stale CDN can't mask a deploy.
    assert calls[0]["opts"]["cache"] == "no-store"


def test_poller_does_not_reload_when_counter_stationary() -> None:
    """Server reports the same counter as the page's baseline (3 == 3)
    — poller MUST NOT reload. Otherwise every dashboard tab would
    flap on every poll cycle whether or not a deploy happened.

    Detection: count framenavigated events fired AFTER the initial
    goto. A stationary counter ⇒ 0 reloads ⇒ 0 navigations.
    """
    nav_count = 0

    def _on_framenavigated(_frame: Any) -> None:
        nonlocal nav_count
        nav_count += 1

    with playwright_sync_api.sync_playwright() as p:
        browser, page = _open_fixture(p)
        page.on("framenavigated", _on_framenavigated)
        # Wait for the initial pollOnce + a beat for promises to flush.
        page.wait_for_function(
            "() => window.__fetch_calls__.length >= 1",
            timeout=5000,
        )
        page.evaluate("() => new Promise(r => setTimeout(r, 200))")
        browser.close()

    assert nav_count == 0, (
        f"Counter stationary at 3 — reload MUST NOT fire "
        f"(got {nav_count} framenavigated events after initial load)"
    )


def test_poller_reloads_when_counter_advances() -> None:
    """Server reports a higher counter (5) than the baseline (3) —
    poller fires location.reload(). This is the X.4.g.12 contract:
    Studio's POST /deploy bumps the counter, every open dashboard
    picks it up + re-renders against the fresh data on the next
    poll cycle.

    Detection: ``Location.reload`` is non-configurable in WebKit
    (per spec — Object.defineProperty is silently rejected on it),
    so we can't stub it directly. Instead, count framenavigated
    events: the initial page load counts as 1; a reload bumps it to
    ≥2. After the reload, ``__fetch_calls__`` resets to whatever
    the freshly-loaded fixture sets it to, which is also detectable.
    """
    nav_count = 0

    def _on_framenavigated(_frame: Any) -> None:
        nonlocal nav_count
        nav_count += 1

    with playwright_sync_api.sync_playwright() as p:
        browser, page = _open_fixture(p)
        page.on("framenavigated", _on_framenavigated)
        # Drain the initial DOMContentLoaded poll (response = baseline
        # 3, no reload). nav_count was incremented during goto() above
        # but is captured into the listener AFTER goto, so it should
        # be 0 at this point — the listener only sees reloads from
        # this point forward.
        page.wait_for_function(
            "() => window.__fetch_calls__.length >= 1",
            timeout=5000,
        )
        page.evaluate("() => new Promise(r => setTimeout(r, 50))")
        assert nav_count == 0, (
            f"No reload should have fired yet (counter stationary at "
            f"3 == 3); got {nav_count} navigations"
        )

        # Now flip the server response to a higher value + manually
        # invoke the poller again. The fresh closure captures
        # baseline=3 (re-reads the meta), then sees fetch returning 5,
        # 5 !== 3 ⇒ location.reload() fires.
        page.evaluate(
            "() => { window.__next_fetch_body__ = "
            "{ data_generation_id: 5 }; "
            "window.__bootstrap_internals__.wireDataGenerationPoller(); }",
        )
        # Wait for the reload to actually navigate (Playwright fires
        # framenavigated when the navigation completes). 5s is plenty
        # for a file:// reload.
        page.wait_for_load_state("domcontentloaded", timeout=5000)
        page.wait_for_function(
            "() => window.__bootstrap_internals__ != null",
            timeout=5000,
        )
        browser.close()

    assert nav_count >= 1, (
        "Counter advanced 3 → 5 — poller MUST trigger a reload "
        f"(got {nav_count} framenavigated events after the bump)"
    )


def test_poller_noops_when_meta_absent() -> None:
    """A page without the ``<meta name="data-generation-id">`` tag —
    the dashboards listing page, error pages, future Studio pages —
    MUST NOT poll. The bootstrap.js poller bundle is still loaded
    (it's one big JS) but the function bails immediately when the
    meta isn't present."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        # Strip the meta + override fetch BEFORE bootstrap.js loads.
        page.add_init_script(
            "window.__test_mode__ = true; "
            "window.__fetch_calls__ = []; "
            "window.fetch = (url) => { "
            "  window.__fetch_calls__.push({url: url}); "
            "  return Promise.resolve({"
            "    ok: true, json: () => Promise.resolve({}), "
            "  }); "
            "};",
        )
        page.goto(f"file://{_FIXTURE.resolve()}")
        # Erase the meta tag immediately + manually invoke the poller
        # to prove it bails.
        page.evaluate(
            "() => { "
            "document.querySelector('meta[name=\"data-generation-id\"]').remove(); "
            "window.__fetch_calls__ = []; "
            "window.__bootstrap_internals__.wireDataGenerationPoller(); "
            "}",
        )
        # Give microtasks a beat — even if the poller DID fetch, we
        # want to give the promise a chance to push into the recorder
        # before we check.
        page.evaluate("() => new Promise(r => setTimeout(r, 50))")
        calls = cast(
            list[dict[str, Any]],
            page.evaluate("() => window.__fetch_calls__"),
        )
        browser.close()

    assert calls == [], (
        "Without a baseline meta tag, poller MUST NOT fetch "
        f"(got calls: {calls})"
    )
