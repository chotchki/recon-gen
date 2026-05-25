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

    # AI.2.d.2 piece-2 (2026-05-25, user): dev-iteration timeout — 30s
    # default kills iteration when fail-fast surfaces the actual error
    # via the rendered page. 10s is generous for any in-process click
    # / navigation; raise per-call when an expected operation legit
    # takes longer (e.g., dashboards mount under a future verb).
    DEFAULT_TIMEOUT_MS = 10_000

    def __init__(self, page: Any, base_url: str) -> None:  # typing-smell: ignore[explicit-any]: Playwright `Page` — kept Any to stay import-light at the test-file seam
        self._page = page
        self._base = base_url.rstrip("/")
        page.set_default_timeout(self.DEFAULT_TIMEOUT_MS)

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
        carry a link to /. On pages where home isn't linked (e.g.,
        the home page itself), use ``goto_home_via_back()`` instead.
        Most callers won't need this — every create-verb's 303
        leaves the browser at /."""
        self._page.click('a[href="/"]')

    def goto_account_list(self) -> None:
        """Click the home nav link to the account list page."""
        # First navigate home if not already there (the account list
        # link is on the home page's entity nav). The submit-success
        # 303 lands the browser at "/" so this is usually a no-op,
        # but the verb stays general for callers that arrive from
        # elsewhere.
        self._page.click('a[href="/l2_shape/account/"]')

    # -- form-fill helpers (transport-agnostic, page-driven) -------------

    def _apply_form_data(self, data: "dict[str, list[str]]") -> None:
        """Translate a `FormData` dict (the same shape
        ``create_form_data(kind, entity)`` builds for the HTTP
        driver) into Playwright form-fills on the currently-rendered
        page.

        Field-type dispatch reads the rendered DOM (`tagName` +
        `type`) rather than the FieldSpec — keeps the helper
        independent of the FieldSpec module and naturally tolerant
        of subtype-conditional rendering.

        Hidden markers (`__present`, `__num_groups`, `subtype`)
        are server-managed (set by the URL or pre-rendered as hidden
        inputs); the helper SKIPS them — the form already carries
        the correct values.
        """
        for name, values in data.items():
            if name == "subtype":
                # Set by the URL the rail subtype picker navigates to.
                continue
            if name.endswith("__present") or name.endswith("__num_groups"):
                # Server-rendered hidden marker — already present.
                continue
            locator = self._page.locator(f'[name="{name}"]')
            count = locator.count()
            if count == 0:
                if values:
                    raise AssertionError(
                        f"_apply_form_data: form has no field named "
                        f"{name!r} but caller supplied {values!r}. "
                        f"Either the form isn't rendering the field "
                        f"(UI gap) or the FieldSpec for this kind "
                        f"doesn't match the form's field-name."
                    )
                continue
            first = locator.first
            tag = first.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                self._page.select_option(f'select[name="{name}"]', values[0])
            elif tag == "textarea":
                self._page.fill(f'textarea[name="{name}"]', values[0])
            elif tag == "input":
                input_type = first.get_attribute("type") or "text"
                if input_type == "checkbox":
                    # Multi-select checkbox group. Check each matching
                    # value; the rest stay unchecked (form's initial
                    # state). Force=True bypasses Playwright's "visible
                    # + stable" actionability checks when the box is
                    # inside a fieldset that's `hidden` for the
                    # currently-inactive subtype.
                    for v in values:
                        self._page.check(
                            f'input[type=checkbox][name="{name}"][value="{v}"]',
                        )
                elif input_type == "hidden":
                    continue
                else:
                    self._page.fill(f'input[name="{name}"]', values[0])
            else:
                raise AssertionError(
                    f"_apply_form_data: unexpected tag {tag!r} for "
                    f"field {name!r}; fill strategy unknown."
                )

    # -- entity creation -------------------------------------------------

    def _submit_create_form(self, kind_label: str) -> None:
        """Click the create form's Submit button + assert the success
        303 lands us at home (`/`). On validation failure, the server
        re-renders the form at the POST target with a 400 + an inline
        error banner — fail FAST with that error visible in the message
        rather than time out waiting for a redirect that won't happen.

        ``kind_label`` is for the error message (e.g., "account",
        "rail") — surfaces which verb failed when the test harness
        prints the AssertionError.
        """
        # Wait for ANY navigation (success → /, failure → POST target
        # re-render). expect_navigation with no url= matches any.
        with self._page.expect_navigation():
            self._page.click('form.create-form button[type="submit"]')
        landed = self._page.url
        if landed.rstrip("/") == self._base:
            return  # success path: 303 → home
        # Failure path: extract the inline error the editor renders.
        error_locator = self._page.locator(".form-global-error")
        error_text = (
            error_locator.first.text_content() or ""
            if error_locator.count() > 0
            else "(no .form-global-error block on the rendered page)"
        )
        raise AssertionError(
            f"create {kind_label}: submit failed to redirect home. "
            f"Landed at {landed!r} (expected {self._base + '/'!r}); "
            f"editor's inline error: {error_text.strip()}"
        )

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
        self._submit_create_form("account")

    def create_rail(self, rail: object) -> None:
        """Fill + submit the rail-create form for ``rail``.

        Rails are a discriminated union (single_leg / two_leg). The
        editor's create flow is 2-step: click the home link to land
        on the subtype picker, then click the subtype-matching
        button to land on the actual form. Both navigations are
        link-clicks per the no-URL-editing constraint.

        Uses ``create_form_data("rail", rail)`` (the same encoder
        the HTTP driver uses) to build the FormData dict, then
        translates it into Playwright fills via ``_apply_form_data``.

        BB.1 reconciler gate: this verb does NOT yet attach a
        reconciler — non-aggregating single-leg rails will 400 at
        submit time. The BB.1/BB.2 reconciler-aware verb is piece-2b.

        Precondition: page is at the studio home (``/``). Every
        create verb's success path 303-redirects there, so chaining
        ``create_X`` → ``create_Y`` works without explicit
        navigation between.
        """
        from tests.e2e._drivers.studio_editor import (  # noqa: PLC0415 — lazy
            _rail_subtype_of, create_form_data,
        )

        subtype = _rail_subtype_of(rail)
        # Step 1: home → subtype picker (the click of rail/new link
        # on the home page).
        self._page.click('a[href="/l2_shape/rail/new"]')
        # Step 2: subtype picker → form. The picker renders both
        # subtype links; click the matching one.
        self._page.click(
            f'a[href="/l2_shape/rail/new?subtype={subtype}"]',
        )
        # Apply the FormData dict to the rendered form.
        data = create_form_data("rail", rail)
        self._apply_form_data(data)
        self._submit_create_form(f"rail {data.get('name', ['?'])[0]!r}")

    def create_rail_with_new_reconciler(
        self,
        rail: object,
        *,
        reconciler_kind: str,
        reconciler_new_name: str,
        reconciler_new_fields: "dict[str, str]",
    ) -> None:
        """AI.2.d.2 piece 2c — BB.2 create-new sub-form via the
        inline JS toggle.

        The form's default mode is "Attach to existing". The
        operator clicks the "Create new reconciler" radio → BB.2's
        inline JS toggles the attach-block hidden + the create-new
        block visible. Then the operator fills the create-new
        sub-form (name + kind + per-kind required fields like
        TT.expected_net + TT.completion).

        This is THE highest-value Playwright catch: the BB.2 inline
        JS toggle is the only renderer-layer behavior no HTTP-based
        layer can exercise. A regression to the JS (selector typo,
        event-name change, `[hidden]` attribute behavior drift) would
        leave operators with the create-new fields un-revealed →
        form un-fillable → composite atomic mutation un-invocable.

        ``reconciler_kind`` ∈ ``{transfer_template, aggregating_rail}``.
        ``reconciler_new_fields`` carries the per-kind required
        minima (e.g., for TT: ``{"reconciler_new_expected_net":
        "0.00", "reconciler_new_completion": "business_day_end"}``).

        Per AI.9 workaround: `rail` dataclass should have its
        `RoleExpression` fields as tuples even when single-role.
        """
        from tests.e2e._drivers.studio_editor import (  # noqa: PLC0415 — lazy
            _rail_subtype_of, create_form_data,
        )

        subtype = _rail_subtype_of(rail)
        # Step 1: home → subtype picker → form.
        self._page.click('a[href="/l2_shape/rail/new"]')
        self._page.click(
            f'a[href="/l2_shape/rail/new?subtype={subtype}"]',
        )
        # Fill the rail's own fields (subtype, name, role, etc.).
        data = create_form_data("rail", rail)
        self._apply_form_data(data)
        # BB.2 inline JS toggle: click the "Create new" radio. The
        # JS handler swaps the attach-block hidden + reveals the
        # create-new block. force=True bypasses Playwright
        # actionability — the radio sits inside a `<label>` and
        # WebKit can hit one or the other inconsistently.
        self._page.check(
            'input[name="reconciler_mode"][value="create_new"]',
        )
        # Pick the reconciler kind in the create-new sub-form's kind
        # dropdown (BB.2's `reconciler_new_kind`; the JS mirrors it
        # into `reconciler_kind` for the server's gate).
        self._page.select_option(
            'select[name="reconciler_new_kind"]', reconciler_kind,
        )
        # Fill the reconciler-new-name + kind-specific required
        # fields (caller supplies them).
        self._page.fill(
            'input[name="reconciler_new_name"]', reconciler_new_name,
        )
        # Also subtype if it's an aggregating_rail reconciler — fills
        # the aggregator's sub-subtype picker.
        for field_name, value in reconciler_new_fields.items():
            # All create-new sub-form fields are plain text inputs or
            # selects per BB.2's _render_reconciler_section.
            loc = self._page.locator(f'[name="{field_name}"]')
            if loc.count() == 0:
                raise AssertionError(
                    f"create-new sub-form has no field "
                    f"{field_name!r}; check the FieldSpec or the "
                    f"BB.2 sub-form rendering."
                )
            tag = loc.first.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                self._page.select_option(
                    f'select[name="{field_name}"]', value,
                )
            else:
                self._page.fill(f'input[name="{field_name}"]', value)
        # Submit + assert success (303 home).
        self._submit_create_form(
            f"rail {data.get('name', ['?'])[0]!r} with new reconciler",
        )

    def go_back(self) -> None:
        """Click the browser's back button — a real-operator action
        that, in the current editor, is the ONLY way to get from a
        sub-page (list / read-card / edit form) back to the studio
        home. AI.8 logs the editor-side gap that makes this verb
        necessary; once AI.8 lands a home-link in the sub-page
        chrome, this verb becomes redundant + can be retired."""
        self._page.go_back()

    def goto_rail_list(self) -> None:
        """Click home → rail list. Precondition: page is at home
        (`/`). Use `go_back()` to return home first if you're on a
        sub-page (AI.8 — no home link in sub-page chrome)."""
        self._page.click('a[href="/l2_shape/rail/"]')

    def goto_transfer_template_list(self) -> None:
        """Click home → transfer_template list. Same precondition
        as `goto_rail_list`."""
        self._page.click('a[href="/l2_shape/transfer_template/"]')

    def rail_list_contains(self, rail_name: str) -> bool:
        """True iff the rail-list page shows ``rail_name``."""
        return rail_name in self._page.content()

    def transfer_template_list_contains(self, tt_name: str) -> bool:
        """True iff the TT-list page shows ``tt_name``."""
        return tt_name in self._page.content()

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
