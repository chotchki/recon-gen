"""AI.2.d.2 — Studio editor driver over real Playwright/WebKit.

Sibling to ``StudioHttpEditorDriver`` (`tests/e2e/_drivers/studio_editor.py`
— uses Starlette ``TestClient``); same conceptual contract (drive the
editor verb-by-verb to recreate an L2), different wire (real browser
clicking real HTML forms).

**Two operator-fidelity constraints** (user 2026-05-25):

1. **Discovery-only.** Helpers may take the reference L2 to know
   what to type / what entity-name to invent (a real operator would
   know what they want to build); the test ITSELF asserts via
   rendered DOM, not cache reads. If a form needs a value the
   operator must SELECT from a list of existing entities (rails,
   roles, etc.), the driver reads the rendered options from the
   page — never the cache.

2. **No URL editing past entry.** The driver's `open()` issues the
   single `page.goto(base_url)` for the studio root; every other
   navigation happens via clicking a rendered link or submitting a
   form. A future verb that does `page.goto("/some/path")` would
   prove a route works in isolation but not that the operator can
   discover it through the studio's nav.

Verb sets land piece-by-piece per user 2026-05-25:

- **Piece 1 (now)** — `open()`, `create_account()`,
  `goto_account_list()`, `account_list_contains()`. Proves the
  end-to-end wire on the simplest entity.
- **Piece 2** — extend to rail (multi-select roles), reconciler
  create-new sub-form (BB.2 inline JS toggle), XOR groups.
- **Piece 3** — full `create_l2(reference)` walk + the AI.4/AI.5
  equivalence assertions on the saved YAML.

Per X.2.q's no-playwright-leak lint, this is where Playwright lives;
the test file consumes verbs.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any


class StudioBrowserEditorDriver:
    """Verb protocol over a real WebKit-driven Studio editor."""

    def __init__(self, page: Any, base_url: str) -> None:  # typing-smell: ignore[explicit-any]: Playwright `Page` — kept Any to stay import-light at the test-file seam
        self._page = page
        self._base = base_url.rstrip("/")

    @classmethod
    @contextmanager
    def serving(
        cls, asgi_app: object, *, headless: bool = True,
    ) -> Iterator["StudioBrowserEditorDriver"]:
        """Spin a uvicorn server on an ephemeral port + open a WebKit
        page + land on the studio home (the single `goto` for the
        whole test lifecycle).

        ``asgi_app`` is the Starlette app the test built — typically
        wrapping ``make_studio_routes(cache)`` directly so the test
        exercises the same routes the operator hits.
        """
        from recon_gen.common.browser.helpers import webkit_page  # noqa: PLC0415 — lazy
        from tests.e2e._harness_studio_editor import studio_editor_server  # noqa: PLC0415 — lazy

        with (
            studio_editor_server(asgi_app) as base_url,
            webkit_page(headless=headless) as page,
        ):
            driver = cls(page, base_url)
            driver.open()
            yield driver

    # -- navigation ------------------------------------------------------

    def open(self) -> None:
        """The single URL entry of the test lifecycle. Subsequent
        navigation is link-click or form-submit."""
        self._page.goto(f"{self._base}/")

    def goto_account_create_form(self) -> None:
        """Click the home nav link to the account-create form."""
        self._page.click('a[href="/l2_shape/account/new"]')

    def goto_home(self) -> None:
        """Click any rendered home link. Studio's nav menu / breadcrumbs
        carry a link to /."""
        self._page.click('a[href="/"]')

    def goto_account_list(self) -> None:
        """Click the home nav link to the account list page."""
        # First navigate home if not already there (the account list
        # link is on the home page's entity nav). The submit-success
        # 303 lands the browser at "/" so this is usually a no-op,
        # but the verb stays general for callers that arrive from
        # elsewhere.
        self._page.click('a[href="/l2_shape/account/"]')

    # -- entity creation -------------------------------------------------

    def create_account(
        self, *, account_id: str, role: str, scope: str = "internal",
    ) -> None:
        """Fill + submit the account-create form. Waits for the
        server's 303 redirect back home before returning so the
        caller can chain another verb without racing the commit.

        ``account_id`` / ``role`` are operator-fresh strings the
        caller invents (per the discovery-only contract — these
        aren't read from any cache; the caller is recreating a
        known L2 and types each identifier explicitly).
        """
        self.goto_account_create_form()
        self._page.fill('input[name="id"]', account_id)
        self._page.fill('input[name="role"]', role)
        self._page.select_option('select[name="scope"]', scope)
        with self._page.expect_navigation(url=f"{self._base}/"):
            self._page.click('form.create-form button[type="submit"]')

    # -- DOM queries (the test's assertion seam) -------------------------

    def account_list_contains(self, account_id: str) -> bool:
        """True iff the rendered account-list page shows ``account_id``.

        Sole-source-of-info: reads the DOM, not the cache. If the
        editor lost the account on commit OR rendered the list view
        without it, this returns False — the test sees what the
        operator sees."""
        body = self._page.content()
        return account_id in body

    def page_body(self) -> str:
        """Return the current page's rendered HTML. The verb-of-last-
        resort — when an assertion needs raw HTML for a focused diff
        message. Prefer the typed verbs above when one fits."""
        return str(self._page.content())
