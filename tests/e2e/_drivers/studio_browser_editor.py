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
from collections.abc import Generator
from pathlib import Path
from typing import Any

from tests.e2e._drivers.studio_editor import _BaseStudioEditorDriver


class StudioBrowserEditorDriver(_BaseStudioEditorDriver):
    """Verb protocol over a real WebKit-driven Studio editor.

    Inherits ``create_l2(reference)`` (the wave-structured bulk
    walk: accounts → templates → wave 3a non-agg rails → wave 3b
    agg rails → TTs → reorder pass → max_unbundled_age edits →
    chains → limit_schedules → instance settings) from
    ``_BaseStudioEditorDriver``. Each per-kind verb below is the
    browser implementation of the same Protocol the HTTP driver
    implements; ``create_l2`` resolves verb dispatch statically and
    works for both transports."""

    # AI.2.d.2 piece-2 (2026-05-25, user): dev-iteration timeout — 30s
    # default kills iteration when fail-fast surfaces the actual error
    # via the rendered page. 10s is generous for any in-process click
    # / navigation; raise per-call when an expected operation legit
    # takes longer (e.g., dashboards mount under a future verb).
    DEFAULT_TIMEOUT_MS = 10_000

    def __init__(
        self, page: Any, base_url: str,  # typing-smell: ignore[explicit-any]: Playwright `Page` — kept Any to stay import-light at the test-file seam
        l2_path: Path | None = None,
    ) -> None:
        self._page = page
        self._base = base_url.rstrip("/")
        # AI.2.d.2 piece 3 — `l2_path` is the cache's bound yaml file
        # (save-on-mutate flushes there on every successful commit).
        # ``save_l2_to_path`` copies it elsewhere if asked. Optional
        # to keep piece-1/2 smoke tests un-coupled.
        self._l2_path = l2_path
        page.set_default_timeout(self.DEFAULT_TIMEOUT_MS)

    @classmethod
    @contextmanager
    def serving(
        cls, asgi_app: object, *, headless: bool = True,
        l2_path: Path | None = None,
    ) -> Generator["StudioBrowserEditorDriver", None, None]:
        """Spin a uvicorn server on an ephemeral port + open a WebKit
        page + land on the studio home (the single `goto` for the
        whole test lifecycle).

        ``asgi_app`` is the Starlette app the test built — typically
        wrapping ``make_studio_routes(cache)`` directly so the test
        exercises the same routes the operator hits.

        ``l2_path`` (piece 3) is the cache's bound yaml file. When
        supplied, ``save_l2_to_path(dest)`` copies it (or returns the
        path unchanged when ``dest == l2_path``). The cache is
        save-on-mutate so the file is already current; the verb is
        a thin confirmation hook.
        """
        from recon_gen.common.browser.helpers import webkit_page  # noqa: PLC0415 — lazy
        from tests.e2e._harness_studio_editor import studio_editor_server  # noqa: PLC0415 — lazy

        with (
            studio_editor_server(asgi_app) as base_url,
            webkit_page(headless=headless) as page,
        ):
            driver = cls(page, base_url, l2_path=l2_path)
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

    def _edit(
        self, kind: str, entity_id: str,
        data: "dict[str, list[str]]",
    ) -> None:
        """AI.2.d.2 piece 4 — partial edit verb. Mirrors the HTTP
        driver's `_edit`: navigates to the entity's edit page,
        applies the (partial) form data, submits. Wave 5 of the
        base `create_l2` walk uses this for TT.leg_rails /
        aggregator.bundles_activity reorder; wave 6 for
        max_unbundled_age fills.

        Navigation chain (all click-through, no URL editing per the
        operator-fidelity contract): home → list page → entity
        card's Edit link → form. Composite entity_ids (e.g. chain's
        `Parent::Child::...`) get URL-encoded via Playwright's
        href-selector machinery — passing the raw entity_id in the
        selector works because the editor's rendered link also
        carries the raw `::` (no URL encoding on the editor side
        per AI.2.d/X.4.f.7 design).
        """
        # Step 1: home → list (assumes browser is at home; every verb
        # leaves it there via the 303 redirect).
        self._page.click(f'a[href="/l2_shape/{kind}/"]')
        # Step 2: find the entity's card by its visible heading
        # (the operator's mental anchor — they SEE the entity_id as
        # the card title) and click the "Edit" link inside it. Pure
        # user-facing locator: scoped by visible text, not CSS class
        # or href shape. AM.1 step 6 (2026-05-25) retired the
        # `.edit-link` semantic class; switching to label/role here
        # also pre-empts brittleness from URL-encoding tweaks.
        card = self._page.locator(
            f'article:has(h3:has-text("{entity_id}"))',
        ).first
        card.get_by_role("link", name="Edit").click()
        # Step 3: apply the partial form data + submit.
        self._apply_form_data(data)
        self._submit_create_form(f"edit {kind} {entity_id!r}")

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
        # AM.1 step 5 (2026-05-25) retired `.form-global-error` in
        # favor of the ARIA `role="alert"` semantic marker — locate
        # via the role (what assistive tech reads) rather than the
        # styling-utility class.
        error_locator = self._page.get_by_role("alert")
        error_text = (
            error_locator.first.text_content() or ""
            if error_locator.count() > 0
            else "(no inline alert block on the rendered page)"
        )
        raise AssertionError(
            f"create {kind_label}: submit failed to redirect home. "
            f"Landed at {landed!r} (expected {self._base + '/'!r}); "
            f"editor's inline error: {error_text.strip()}"
        )

    def _create_simple(
        self, kind: str, entity: object, kind_label: str | None = None,
    ) -> None:
        """Generic create flow for non-discriminated entity kinds.

        Click home → `/l2_shape/<kind>/new` link → apply form data
        → submit. Used by ``create_account_template`` /
        ``create_transfer_template`` / ``create_chain`` /
        ``create_limit_schedule``. Rails go through ``create_rail``
        (subtype picker click-through) + reconciler handling.
        """
        from tests.e2e._drivers.studio_editor import (  # noqa: PLC0415 — lazy
            create_form_data,
        )

        self._page.click(f'a[href="/l2_shape/{kind}/new"]')
        data = create_form_data(kind, entity)  # type: ignore[arg-type]: EntityKind literal narrows from kind str at the seam
        self._apply_form_data(data)
        self._submit_create_form(kind_label or kind)

    def create_account(self, account: object | None = None, **kwargs: object) -> None:
        """Fill + submit the account-create form. Two surfaces:

        - ``create_account(account_object)`` — Protocol-shape (matches
          ``StudioEditorDriver``); reuses ``create_form_data`` for
          full-fidelity field-fill (description, expected_eod_balance,
          etc.).
        - ``create_account(account_id=..., role=..., scope=...)`` —
          piece-1 quick form for smoke tests; fills the 3 required
          fields directly.

        Discovery-only contract: operator-fresh strings for new
        identifiers; any role/parent_role referenced must already
        exist on the page (recreate dependencies first).
        """
        if account is not None:
            self._create_simple("account", account, kind_label="account")
            return
        self.goto_account_create_form()
        self._page.fill('input[name="id"]', str(kwargs["account_id"]))
        self._page.fill('input[name="role"]', str(kwargs["role"]))
        self._page.select_option(
            'select[name="scope"]', str(kwargs.get("scope", "internal")),
        )
        self._submit_create_form("account")

    def create_account_template(self, template: object) -> None:
        """Create an AccountTemplate via the dedicated form."""
        self._create_simple("account_template", template)

    def create_transfer_template(self, template: object) -> None:
        """Create a TransferTemplate via the dedicated form."""
        self._create_simple("transfer_template", template)

    def create_chain(self, chain: object) -> None:
        """Create a Chain via the dedicated form. The chain_children
        widget encodes children + per-child fan_in / epc — handled
        by the shared `create_form_data` encoder + `_apply_form_data`
        translator (chain_children renders as a multi-select
        checkbox group keyed `children`)."""
        self._create_simple("chain", chain)

    def create_limit_schedule(self, schedule: object) -> None:
        """Create a LimitSchedule via the dedicated form."""
        self._create_simple("limit_schedule", schedule)

    def set_instance_settings(
        self,
        *,
        description: str | None,
        role_business_day_offsets: "dict[str, int] | None",
    ) -> None:
        """Fill + submit the singleton instance form (description +
        role_business_day_offsets). The editor renders this as a
        YAML-block textarea named `yaml`; submit POSTs to
        `/l2_shape/instance/` (with `_method=PUT` hidden) and
        303s home on success.

        No-op when both args are None (no settings to set; preserves
        the empty-block default)."""
        from tests.e2e._drivers.studio_editor import (  # noqa: PLC0415 — lazy
            instance_yaml_text,
        )

        yaml_text = instance_yaml_text(description, role_business_day_offsets)
        if not yaml_text:
            return
        # Navigate to the instance singleton form via the home link.
        self._page.click('a[href="/l2_shape/instance/"]')
        self._page.fill('textarea[name="yaml"]', yaml_text)
        self._submit_create_form("instance settings")

    def save_l2_to_path(self, path: Path) -> Path:
        """Confirm the rebuilt L2 lands at ``path``.

        Save-on-mutate already flushed every successful commit to
        the cache's bound ``l2_path``; if ``path != l2_path``, copy.
        Requires ``l2_path`` to be set on the driver (see
        ``serving(l2_path=...)``).
        """
        path = Path(path)
        if self._l2_path is None:
            raise RuntimeError(
                "save_l2_to_path: driver wasn't constructed with "
                "an l2_path; pass `serving(l2_path=cache_path)` "
                "to enable the save-confirmation verb."
            )
        if path != self._l2_path:
            import shutil  # noqa: PLC0415 — lazy
            shutil.copyfile(self._l2_path, path)
        return path

    def create_rail(
        self,
        rail: object,
        *,
        reconciler: "tuple[str, tuple[str, str]] | None" = None,
        reference: "object | None" = None,
        partial_xor_groups: "tuple[tuple[str, ...], ...] | None" = None,
    ) -> None:
        """BB.3 — thread the reconciler payload + optional xor_groups
        update through the browser form.

        ``reconciler`` is ``(mode, (rec_kind, rec_name))`` where
        ``mode`` ∈ ``{"attach", "create_new"}``. For "create_new",
        ``reference`` (L2Instance) is required so the driver can
        look up the new reconciler's fields from the reference
        (the same shape the HTTP driver uses).

        Per-kind UI:
        - ``attach``: select reconciler_kind + reconciler_name
          dropdowns inside the (default-visible) attach-block
        - ``create_new``: click the "Create new" radio (BB.2 inline
          JS toggle reveals the create-new block) + fill the kind +
          name + per-kind required fields read from the reference
          entity via ``create_form_data``
        """
        from tests.e2e._drivers.studio_editor import (  # noqa: PLC0415 — lazy
            _find_reconciler_in_reference,
            _rail_subtype_of,
            _strip_rail_lists,
            create_form_data,
        )

        subtype = _rail_subtype_of(rail)
        # Step 1+2: home → subtype picker → form.
        self._page.click('a[href="/l2_shape/rail/new"]')
        self._page.click(
            f'a[href="/l2_shape/rail/new?subtype={subtype}"]',
        )
        # Apply the rail's own fields.
        data = create_form_data("rail", rail)
        self._apply_form_data(data)
        # Handle the reconciler (BB.1 attach / BB.2 create-new).
        if reconciler is not None:
            mode, (rec_kind, rec_name) = reconciler
            self._page.check(
                f'input[name="reconciler_mode"][value="{mode}"]',
            )
            if mode == "attach":
                self._page.select_option(
                    'select[name="reconciler_kind"]', rec_kind,
                )
                self._page.select_option(
                    'select[name="reconciler_name"]', rec_name,
                )
            else:  # "create_new"
                if reference is None:
                    raise ValueError(
                        "create_rail(reconciler=('create_new', ...)) "
                        "requires `reference` (L2Instance) to look up "
                        "the new reconciler's fields"
                    )
                reconciler_entity = _find_reconciler_in_reference(
                    reference, rec_kind, rec_name,
                )
                stripped = _strip_rail_lists(reconciler_entity, rec_kind)
                rec_form_kind = (
                    "transfer_template"
                    if rec_kind == "transfer_template" else "rail"
                )
                rec_form = create_form_data(rec_form_kind, stripped)  # type: ignore[arg-type]: EntityKind narrows from local literal
                # Prefix every reconciler-new field. The BB.2 sub-form
                # uses `reconciler_new_<orig_name>` keys. Hidden
                # markers (__present, __num_groups, subtype) stay
                # un-prefixed; skip them.
                self._page.locator(
                    'select[name="reconciler_new_kind"]',
                ).select_option(rec_kind, force=True)
                for key, values in rec_form.items():
                    if key == "subtype":
                        # Aggregator rail subtype — pick via the
                        # BB.2 reconciler_new_subtype select.
                        self._page.locator(
                            'select[name="reconciler_new_subtype"]',
                        ).select_option(values[0], force=True)
                        continue
                    if (
                        key.endswith("__present")
                        or key.endswith("__num_groups")
                    ):
                        continue
                    field_name = f"reconciler_new_{key}"
                    loc = self._page.locator(f'[name="{field_name}"]')
                    count = loc.count()
                    if count == 0:
                        # Field isn't in the create-new sub-form
                        # (e.g., metadata_keys for aggregator —
                        # not part of the minimum required set).
                        # Skip; the editor's validator runs on
                        # submit + surfaces any real omissions.
                        continue
                    # AI.2.d.2 piece 3 + AI.13 — pick the VISIBLE
                    # element when multiple inputs share a name
                    # (`reconciler_new_expected_net` appears in BOTH
                    # the TT-kind block AND the aggregator-two-leg
                    # block; `loc.first` would silently target the
                    # hidden TT one). Iterate + pick the first
                    # visible match.
                    target = None
                    for i in range(count):
                        candidate = loc.nth(i)
                        if candidate.is_visible():
                            target = candidate
                            break
                    if target is None:
                        # All same-name inputs hidden — server-managed
                        # state, no field to fill.
                        continue
                    # `force=True` still used because the per-kind
                    # block's `hidden` toggle races the radio-click
                    # → kind-select → field-reveal chain in WebKit;
                    # visible-but-not-yet-actionable is a real
                    # race. Operator-fidelity-equivalent.
                    tag = target.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        target.select_option(values[0], force=True)
                    elif tag == "input":
                        input_type = target.get_attribute("type") or "text"
                        if input_type == "checkbox":
                            for v in values:
                                self._page.locator(
                                    f'input[type=checkbox][name="{field_name}"][value="{v}"]',
                                ).check(force=True)
                        elif input_type != "hidden":
                            target.fill(values[0], force=True)
                    else:
                        target.fill(values[0], force=True)
        # AI.10 (2026-05-25) — partial_xor_groups via the
        # `leg_rail_xor_groups_text` textarea (BB-style form-pairing).
        # Server parses one-group-per-line / comma-separated rails
        # and rewrites into the BB.3 wire shape. Driver writes the
        # textarea when partial_xor_groups is supplied.
        if partial_xor_groups:
            text_value = "\n".join(
                ", ".join(rail for rail in group)
                for group in partial_xor_groups
            )
            self._page.fill(
                'textarea[name="leg_rail_xor_groups_text"]', text_value,
            )
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
