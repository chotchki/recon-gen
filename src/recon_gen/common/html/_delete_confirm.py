"""BX.1 — In-place Delete button + reference-check + countdown.

Post-redesign (2026-06-11 operator dogfood): the prior top-of-page
banner UX failed for tall list pages — operator clicks Delete on a
card scrolled-down, banner appears at the top out of view, operator
concludes nothing happened. Replaced with an in-place button swap.

The Delete UI has three render-time states + one post-click state:

1. **active** (render time, unreferenced entity) — Delete anchor
   renders enabled, sitting in the card / form action row exactly
   where the old "Delete" button did. Clicking it ``hx-get``s the
   confirm endpoint which swaps the wrapper innerHTML to ``countdown``.

2. **refused** (render time, entity has incoming references) — Delete
   anchor renders DISABLED with ``aria-disabled="true"`` and a
   ``title`` carrying the operator-readable reason ("Referenced by
   Rail: customer_ach_inbound …"). No click action; the operator
   must remove the referrers first. The reason ALSO lives in
   ``data-delete-reason`` so the e2e driver can read it without
   triggering tooltip rendering.

3. **countdown** (post-click swap response) — anchor text counts
   "Confirming… 5s" → 4 → 3 → 2 → 1, then changes to "Confirm delete"
   and becomes clickable. No Cancel link: per the 2026-06-11 operator
   polish, if the operator changes their mind they navigate away or
   reload — Cancel was noise. The anchor fires ``hx-delete`` to the
   real DELETE endpoint with a signed confirm token.

4. **ready** (countdown reached zero, JS flips state) — same DOM
   element as countdown; only ``data-delete-state`` changes from
   ``"countdown"`` to ``"ready"`` and the ``aria-disabled`` attribute
   is removed. Tests + drivers can wait on the state attr instead of
   timing the JS.

The countdown's wall time is HMAC-signed server-side via
``make_confirm_token`` / ``verify_confirm_token`` — a client that
disables the disabled attr via DevTools still hits the server clock
check.

Per ``[feedback_invariants_in_types]``: typed dataclasses for refs +
typed verify result; state lives in ``data-delete-state`` attribute
NOT scattered template conditionals.

Per ``[feedback_browser_drivers_user_facing_locators]``: ``data-*``
hooks + ``aria-disabled`` / ``title`` for refused tooltip; never
Tailwind utility classes as test locators.

Per ``[project_design_north_stars]``: reason text reads like
"Referenced by Rail: customer_ach_inbound (leg_rails)" — banking-
domain readable, never "FK constraint violation".
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from typing import Any, Literal, TypeAlias

from recon_gen.common.l2.editor import EntityKind
from recon_gen.common.l2.primitives import (
    Chain,
    L2Instance,
    LimitSchedule,
    SingleLegRail,
    TwoLegRail,
)


# Per-process secret for HMAC-signing confirm tokens. Restarting the
# server invalidates outstanding tokens — acceptable for a single-user
# studio; the operator just clicks Delete again.
_TOKEN_SECRET: bytes = secrets.token_bytes(32)


# The countdown window in seconds. 5s per BX.1 operator lock. Held as
# a module-level constant so tests can monkeypatch (only place this
# number appears).
COUNTDOWN_SECS: float = 5.0


RefKind: TypeAlias = Literal[
    "account",
    "account_template",
    "rail",
    "transfer_template",
    "chain",
    "limit_schedule",
]


# Typed surface of the four UI states. Closed Literal so callers branch
# at the type level (per `[feedback_invariants_in_types]`).
DeleteState: TypeAlias = Literal["active", "refused", "countdown", "ready"]


@dataclass(frozen=True, slots=True)
class EntityRef:
    """One reference from another entity to the deletion target.

    Typed so the renderer + tests can introspect each field without
    parsing a string. ``referrer_kind`` + ``referrer_id`` address the
    referring entity (the one the operator must edit / delete first);
    ``via_field`` names the field carrying the reference; ``label`` is
    an operator-readable summary the refused tooltip surfaces.
    """

    referrer_kind: RefKind
    referrer_id: str
    via_field: str
    label: str


def find_references(
    instance: L2Instance,
    kind: EntityKind,
    entity_id: str,
) -> tuple[EntityRef, ...]:
    """Walk ``instance`` for every entity that references ``(kind,
    entity_id)``.

    Reference shapes per kind:

    - **account** — references its ``role`` via Rail role expressions
      (source_role / destination_role / leg_role), Account.parent_role
      / AccountTemplate.parent_role, LimitSchedule.parent_role.
      Account.id is addressing-only; no incoming references to it.
    - **account_template** — same as account (the ``role`` field is
      the cross-cutting identifier).
    - **rail** — references its ``name`` via TransferTemplate.leg_rails,
      Chain.parent / Chain.children, LimitSchedule.rail,
      Rail.bundles_activity (aggregating sweep).
    - **transfer_template** — references its ``name`` via Chain.parent
      / Chain.children.
    - **chain** / **limit_schedule** — leaf consumers. No incoming
      references. Returns empty tuple.

    Returns the references in declaration-order (accounts → templates
    → rails → transfer_templates → chains → limit_schedules) so the
    rendered listing is stable across runs.
    """
    target = _resolve_target(instance, kind, entity_id)
    if target is None:
        return ()

    refs: list[EntityRef] = []
    if kind == "account":
        # Accounts may share a role (cust-001 + cust-002 both
        # carry CustomerSubledger by SPEC design). Only count
        # role-references as a *blocker* when this account is the
        # LAST carrier — otherwise the role survives and the
        # validator accepts the delete. Account.id is addressing-
        # only, never referenced.
        role = str(getattr(target, "role"))
        other_role_carriers = sum(
            1 for a in instance.accounts
            if str(a.role) == role and str(a.id) != entity_id
        )
        # AccountTemplate.role is unique per validator U2, so a
        # template carrying the same role string is a SEPARATE
        # carrier the operator must explicitly delete.
        other_role_carriers += sum(
            1 for t in instance.account_templates
            if str(t.role) == role
        )
        if other_role_carriers == 0:
            refs.extend(_refs_to_role(instance, role))
    elif kind == "account_template":
        # Template roles are unique by validator U2; ANY role-ref
        # is a real blocker. (A singleton Account may carry the
        # same role string but that's a separate carrier; same
        # check as above for symmetry.)
        role = str(getattr(target, "role"))
        other_role_carriers = sum(
            1 for a in instance.accounts if str(a.role) == role
        )
        if other_role_carriers == 0:
            refs.extend(_refs_to_role(instance, role))
    elif kind == "rail":
        # Rail.name is unique — any incoming ref blocks the delete.
        name = str(getattr(target, "name"))
        refs.extend(_refs_to_rail(instance, name))
    elif kind == "transfer_template":
        # Template.name is unique — same logic.
        name = str(getattr(target, "name"))
        refs.extend(_refs_to_template(instance, name))
    # chain + limit_schedule are leaves — no incoming references.

    # Self-references are dropped (a Rail can't reference its own name
    # via bundles_activity in practice, but defense-in-depth).
    return tuple(
        r for r in refs
        if not (r.referrer_kind == kind and r.referrer_id == entity_id)
    )


# ---------------------------------------------------------------------------
# Reference graph — precompute once per render so an N-card list page
# answers find_references in O(1) per card instead of O(N) per card.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReferenceGraph:
    """Precomputed incoming-reference map keyed by ``(kind, entity_id)``.

    Built once per page render in ``build_reference_graph`` so the
    per-card Delete-button rendering can ask ``graph.refs_for(kind, id)``
    in O(1) instead of re-walking the L2Instance N times for an N-card
    list page.
    """

    _by_target: Mapping[tuple[EntityKind, str], tuple[EntityRef, ...]]

    def refs_for(
        self, kind: EntityKind, entity_id: str,
    ) -> tuple[EntityRef, ...]:
        """O(1) lookup. Returns the empty tuple when no refs exist."""
        return self._by_target.get((kind, entity_id), ())


def build_reference_graph(instance: L2Instance) -> ReferenceGraph:
    """Walk the L2Instance and cache ``find_references`` for every
    target entity. List-page render then asks ``graph.refs_for(kind, id)``
    per card in O(1) instead of paying the full walk N times.

    The cache key is ``(kind, entity_id)``. Composite-keyed entities
    use their composite-id form (matching ``_entity_id`` in the editor
    module). Leaf kinds (chain / limit_schedule) always resolve to
    ``()`` — they're cached for shape parity with the lookup branch.
    """
    cache: dict[tuple[EntityKind, str], tuple[EntityRef, ...]] = {}
    for acc in instance.accounts:
        key: tuple[EntityKind, str] = ("account", str(acc.id))
        cache[key] = find_references(instance, "account", str(acc.id))
    for tmpl in instance.account_templates:
        key = ("account_template", str(tmpl.role))
        cache[key] = find_references(
            instance, "account_template", str(tmpl.role),
        )
    for rail in instance.rails:
        key = ("rail", str(rail.name))
        cache[key] = find_references(instance, "rail", str(rail.name))
    for tt in instance.transfer_templates:
        key = ("transfer_template", str(tt.name))
        cache[key] = find_references(
            instance, "transfer_template", str(tt.name),
        )
    for ch in instance.chains:
        key = ("chain", _chain_id(ch))
        cache[key] = ()
    for ls in instance.limit_schedules:
        key = ("limit_schedule", _limit_id(ls))
        cache[key] = ()
    return ReferenceGraph(_by_target=cache)


def _resolve_target(
    instance: L2Instance, kind: EntityKind, entity_id: str,
) -> object | None:
    """Locate the target entity. Mirrors editor._find_entity but
    returns None on miss (instead of KeyError) so the caller can
    decide how to render a delete request against a stale id."""
    if kind == "account":
        return next(
            (a for a in instance.accounts if str(a.id) == entity_id), None,
        )
    if kind == "account_template":
        return next(
            (t for t in instance.account_templates if str(t.role) == entity_id),
            None,
        )
    if kind == "rail":
        return next(
            (r for r in instance.rails if str(r.name) == entity_id), None,
        )
    if kind == "transfer_template":
        return next(
            (
                tt for tt in instance.transfer_templates
                if str(tt.name) == entity_id
            ),
            None,
        )
    if kind == "chain":
        return next(
            (
                ch for ch in instance.chains
                if _chain_id(ch) == entity_id
            ),
            None,
        )
    if kind == "limit_schedule":
        return next(
            (
                ls for ls in instance.limit_schedules
                if _limit_id(ls) == entity_id
            ),
            None,
        )
    return None


def _chain_id(ch: Chain) -> str:
    """Mirror editor._find_entity's chain composite key."""
    children_csv = ",".join(sorted(str(c.name) for c in ch.children))
    return f"{ch.parent}::{children_csv}"


def _limit_id(ls: LimitSchedule) -> str:
    """Mirror editor._find_entity's limit_schedule composite key."""
    return f"{ls.parent_role}::{ls.rail}::{ls.direction}"


def _refs_to_role(instance: L2Instance, role: str) -> Iterator[EntityRef]:
    """Yield every reference whose target is the role string."""
    # Account.parent_role / AccountTemplate.parent_role.
    for acc in instance.accounts:
        if acc.parent_role is not None and str(acc.parent_role) == role:
            yield EntityRef(
                referrer_kind="account",
                referrer_id=str(acc.id),
                via_field="parent_role",
                label=f"Account {acc.id} (parent_role)",
            )
    for tmpl in instance.account_templates:
        if tmpl.parent_role is not None and str(tmpl.parent_role) == role:
            yield EntityRef(
                referrer_kind="account_template",
                referrer_id=str(tmpl.role),
                via_field="parent_role",
                label=f"AccountTemplate {tmpl.role} (parent_role)",
            )
    # Rail role expressions.
    for rail in instance.rails:
        for field, atoms in _rail_role_fields(rail):
            if any(str(a) == role for a in atoms):
                yield EntityRef(
                    referrer_kind="rail",
                    referrer_id=str(rail.name),
                    via_field=field,
                    label=f"Rail {rail.name} ({field})",
                )
    # LimitSchedule.parent_role.
    for ls in instance.limit_schedules:
        if str(ls.parent_role) == role:
            yield EntityRef(
                referrer_kind="limit_schedule",
                referrer_id=_limit_id(ls),
                via_field="parent_role",
                label=f"LimitSchedule {ls.parent_role}→{ls.rail} (parent_role)",
            )


def _rail_role_fields(
    rail: TwoLegRail | SingleLegRail,
) -> Iterator[tuple[str, tuple[Any, ...]]]:
    """Yield ``(field_name, role_atoms_tuple)`` for each role-expression
    field on the rail. Branches on the discriminated Rail union so
    pyright sees the field set per subtype."""
    if isinstance(rail, TwoLegRail):
        yield "source_role", tuple(rail.source_role)
        yield "destination_role", tuple(rail.destination_role)
    else:
        # SingleLegRail
        yield "leg_role", tuple(rail.leg_role)


def _refs_to_rail(instance: L2Instance, rail_name: str) -> Iterator[EntityRef]:
    """Yield every reference whose target is the rail name."""
    # TransferTemplate.leg_rails.
    for tt in instance.transfer_templates:
        if any(str(lr) == rail_name for lr in tt.leg_rails):
            yield EntityRef(
                referrer_kind="transfer_template",
                referrer_id=str(tt.name),
                via_field="leg_rails",
                label=f"TransferTemplate {tt.name} (leg_rails)",
            )
    # Rail.bundles_activity (aggregating rails reference siblings).
    for r in instance.rails:
        bundles = getattr(r, "bundles_activity", ())
        if any(str(b) == rail_name for b in bundles):
            yield EntityRef(
                referrer_kind="rail",
                referrer_id=str(r.name),
                via_field="bundles_activity",
                label=f"Rail {r.name} (bundles_activity)",
            )
    # Chain.parent / Chain.children.
    for ch in instance.chains:
        if str(ch.parent) == rail_name:
            yield EntityRef(
                referrer_kind="chain",
                referrer_id=_chain_id(ch),
                via_field="parent",
                label=f"Chain (parent={ch.parent})",
            )
        if any(str(c.name) == rail_name for c in ch.children):
            yield EntityRef(
                referrer_kind="chain",
                referrer_id=_chain_id(ch),
                via_field="children",
                label=f"Chain {ch.parent}→… (children)",
            )
    # LimitSchedule.rail.
    for ls in instance.limit_schedules:
        if str(ls.rail) == rail_name:
            yield EntityRef(
                referrer_kind="limit_schedule",
                referrer_id=_limit_id(ls),
                via_field="rail",
                label=f"LimitSchedule {ls.parent_role}→{ls.rail} (rail)",
            )


def _refs_to_template(
    instance: L2Instance, template_name: str,
) -> Iterator[EntityRef]:
    """Yield every reference whose target is the transfer-template name."""
    for ch in instance.chains:
        if str(ch.parent) == template_name:
            yield EntityRef(
                referrer_kind="chain",
                referrer_id=_chain_id(ch),
                via_field="parent",
                label=f"Chain (parent={ch.parent})",
            )
        if any(str(c.name) == template_name for c in ch.children):
            yield EntityRef(
                referrer_kind="chain",
                referrer_id=_chain_id(ch),
                via_field="children",
                label=f"Chain {ch.parent}→… (children)",
            )


# ---------------------------------------------------------------------------
# Confirm-token signing
# ---------------------------------------------------------------------------


def make_confirm_token(
    kind: EntityKind, entity_id: str, start_ts: float,
) -> str:
    """HMAC-sign ``(kind, entity_id, start_ts)`` with the per-process
    secret. The token rides the URL on the Confirm button's hx-delete.

    Format: ``<start_ts>:<hex-sig>``. ``start_ts`` is repeated in the
    token (not just signed) so the verifier can read it without
    needing a server-side map of pending deletes. Restarting the
    server invalidates all outstanding tokens (the secret bytes
    rotate)."""
    payload = f"{kind}|{entity_id}|{start_ts:.6f}".encode("utf-8")
    sig = hmac.new(_TOKEN_SECRET, payload, sha256).hexdigest()
    return f"{start_ts:.6f}:{sig}"


@dataclass(frozen=True, slots=True)
class TokenVerifyResult:
    """Typed verify outcome — distinguishes ``ok`` from ``too_early``
    from ``invalid``. Callers branch on this enum instead of a bare
    bool so the 400-message can be specific."""

    status: Literal["ok", "too_early", "invalid"]
    elapsed: float


def verify_confirm_token(
    token: str,
    kind: EntityKind,
    entity_id: str,
    *,
    now: float | None = None,
    countdown_secs: float = COUNTDOWN_SECS,
) -> TokenVerifyResult:
    """Verify a confirm token. Returns ``ok`` only when the HMAC
    matches AND ``now - start_ts >= countdown_secs``."""
    if ":" not in token:
        return TokenVerifyResult(status="invalid", elapsed=0.0)
    raw_ts, sig = token.split(":", 1)
    try:
        start_ts = float(raw_ts)
    except ValueError:
        return TokenVerifyResult(status="invalid", elapsed=0.0)
    expected = make_confirm_token(kind, entity_id, start_ts).split(":", 1)[1]
    # constant-time compare
    if not hmac.compare_digest(sig, expected):
        return TokenVerifyResult(status="invalid", elapsed=0.0)
    current = time.time() if now is None else now
    elapsed = current - start_ts
    if elapsed < countdown_secs:
        return TokenVerifyResult(status="too_early", elapsed=elapsed)
    return TokenVerifyResult(status="ok", elapsed=elapsed)


# ---------------------------------------------------------------------------
# In-place Delete button rendering — three states
# ---------------------------------------------------------------------------


# Onclick guard: prevent the click from bubbling to a parent
# ``<details>``/``<summary>`` (collapsed-card list view's toggle hazard)
# AND from triggering the summary's native open/close default action.
# Carries forward from the BX.1 followup (aab08a79).
_card_click_guard = "event.preventDefault(); event.stopPropagation()"


def _wrapper_open(kind: EntityKind, url_id: str) -> str:
    """Open the wrapper ``<span>`` that HTMX swaps via ``hx-target=
    "closest [data-delete-wrapper]"``. The wrapper is the stable
    swap boundary across all four states — only its innerHTML
    changes.
    """
    return (
        f'<span data-delete-wrapper '
        f'data-delete-kind="{escape(kind)}" '
        f'data-delete-url-id="{escape(url_id)}" '
        f'class="inline-flex items-center gap-2">'
    )


def _wrapper_close() -> str:
    return "</span>"


def _active_button_html(
    kind: EntityKind, url_id: str, *, surface: Literal["card", "form"],
) -> str:
    """Bare active-state Delete anchor — no wrapper. Used both inside
    the initial render path AND by the Cancel endpoint to restore the
    active state without re-emitting the wrapper itself."""
    data_role = "card-delete" if surface == "card" else "form-delete"
    confirm_url = f"/l2_shape/{escape(kind)}/{escape(url_id)}/delete-confirm"
    if surface == "form":
        confirm_url += "?from=edit"
    btn_cls = (
        # danger-outline at rest, fills on hover.
        "inline-flex items-center px-2 py-0.5 text-xs font-semibold "
        "border border-danger text-danger rounded-sm "
        "no-underline cursor-pointer "
        "hover:bg-danger hover:text-white"
    )
    return (
        f'<a class="{btn_cls}" '
        f'data-role="{data_role}" '
        f'data-delete-state="active" '
        f'hx-get="{confirm_url}" '
        f'hx-target="closest [data-delete-wrapper]" hx-swap="innerHTML" '
        f'onclick="{_card_click_guard}">Delete</a>'
    )


def render_active_delete_button(
    kind: EntityKind, url_id: str, *, surface: Literal["card", "form"],
) -> str:
    """Render the at-rest Delete button wrapped in its swap boundary.

    ``surface="card"`` for the read-card summary; ``surface="form"``
    for the edit page action row. Differs only in:

    - ``data-role`` value (kept for test continuity with the BX.1
      ``card-delete`` / ``form-delete`` locators).
    - The ``?from=edit`` query param on the confirm endpoint (form
      surface uses it so the post-delete handler can redirect to the
      list page instead of swapping into the now-stale edit form).
    """
    return (
        f"{_wrapper_open(kind, url_id)}"
        f"{_active_button_html(kind, url_id, surface=surface)}"
        f"{_wrapper_close()}"
    )


REFUSED_TOOLTIP_TEXT: str = "In use — linked to other entities"
"""Operator-facing terse tooltip for the refused Delete state.

Per the 2026-06-11 polish: the prior verbose "Referenced by
TransferTemplate: ExternalReconciliationCycle (leg_rails)" form was
noise in the hover surface. The terse text rides in ``title``; the
detailed referrer list still lives in ``data-delete-reason`` for
driver introspection / power users (matching
``[feedback_browser_drivers_user_facing_locators]``).
"""


def render_refused_delete_button(
    kind: EntityKind,
    url_id: str,
    refs: tuple[EntityRef, ...],
    *,
    surface: Literal["card", "form"],
) -> str:
    """Render the refused Delete anchor (state="refused") wrapped.

    Refs are non-empty → anchor renders disabled with the terse
    ``REFUSED_TOOLTIP_TEXT`` in ``title`` (browser-native tooltip on
    hover/focus) AND the detailed referrer list in
    ``data-delete-reason`` (driver introspection / power users without
    cluttering the hover surface).

    Per ``[project_design_north_stars]`` the detailed reason still
    reads "Referenced by Rail: customer_ach_inbound (leg_rails); …" —
    list each referrer with kind: id, comma-separated; the via_field
    suffix shows WHICH field carries the reference (so the operator
    knows where to edit). Only the tooltip surface got terse.
    """
    data_role = "card-delete" if surface == "card" else "form-delete"
    reason = _format_refused_reason(refs)
    btn_cls = (
        # Disabled-look: same border / color tokens but at reduced
        # opacity + not-allowed cursor; no hover state. The aria
        # disabled + the data-delete-state attr are the test hooks;
        # the visual is informative only.
        "inline-flex items-center px-2 py-0.5 text-xs font-semibold "
        "border border-danger text-danger rounded-sm "
        "no-underline opacity-50 cursor-not-allowed"
    )
    btn = (
        f'<a class="{btn_cls}" '
        f'data-role="{data_role}" '
        f'data-delete-state="refused" '
        f'data-delete-reason="{escape(reason)}" '
        f'data-delete-ref-count="{len(refs)}" '
        f'aria-disabled="true" '
        f'title="{escape(REFUSED_TOOLTIP_TEXT)}" '
        # Keyboard-focusable for tooltip parity (focus reveals title
        # in most browsers via the accessibility tree).
        f'tabindex="0" '
        # No hx-get; the click guard exists only to prevent the
        # disabled-anchor click from bubbling to the parent
        # `<summary>` toggle hazard (collapsed-card list view).
        f'onclick="{_card_click_guard}">Delete</a>'
    )
    return f"{_wrapper_open(kind, url_id)}{btn}{_wrapper_close()}"


def _format_refused_reason(refs: tuple[EntityRef, ...]) -> str:
    """Banking-domain readable reason text per
    ``[project_design_north_stars]``.

    Shape: ``Referenced by Rail: customer_ach_inbound (leg_rails),
    LimitSchedule: DDAControl→…→Inbound (rail)``. One clause per
    referrer; kind label-cased + ":" + id; via_field in parens.
    Caps at 3 referrers + ``; +N more`` to keep the tooltip + the
    ``data-delete-reason`` attr readable; the full list is still
    browsable by clicking through to each referrer's edit page (the
    operator's blocker-resolution path).
    """
    if not refs:
        return "Referenced by other entities."
    parts: list[str] = []
    for r in refs[:3]:
        kind_label = _kind_display_label(r.referrer_kind)
        parts.append(f"{kind_label}: {r.referrer_id} ({r.via_field})")
    suffix = ""
    if len(refs) > 3:
        suffix = f"; +{len(refs) - 3} more"
    return "Referenced by " + ", ".join(parts) + suffix


def _kind_display_label(kind: RefKind) -> str:
    """Banking-domain CamelCase per kind. CPA-readable.
    Per ``[project_design_north_stars]``."""
    return {
        "account": "Account",
        "account_template": "AccountTemplate",
        "rail": "Rail",
        "transfer_template": "TransferTemplate",
        "chain": "Chain",
        "limit_schedule": "LimitSchedule",
    }[kind]


def render_countdown_swap(
    kind: EntityKind,
    entity_id: str,
    url_id: str,
    *,
    now: float | None = None,
    countdown_secs: float = COUNTDOWN_SECS,
    surface: Literal["card", "form"] = "card",
) -> str:
    """Render the post-click swap body (state="countdown" → "ready").

    Returned by the ``/delete-confirm`` GET endpoint. The HX-swap
    replaces the wrapper's innerHTML so only this body changes (a
    single Confirm anchor — Cancel was dropped in the 2026-06-11
    polish); the wrapper itself stays in place. If the operator
    changes their mind during the countdown they navigate away or
    reload — no Cancel click target needed.

    ``entity_id`` is the L2-canonical composite key (used to sign the
    HMAC confirm token — the DELETE handler resolves the URL form
    back to the same composite and verifies against it). ``url_id``
    is the BX.10 URL-side form (opaque hash-slug for composite-keyed
    kinds, bare id otherwise) and feeds the DELETE URL the button
    fires; it MUST match the route Starlette registers.

    Wrapper is NOT emitted here — the parent wrapper survives the
    swap. Only the innerHTML changes.
    """
    start_ts = time.time() if now is None else now
    token = make_confirm_token(kind, entity_id, start_ts)
    delete_url = (
        f"/l2_shape/{escape(kind)}/{escape(url_id)}"
        f"?confirm_token={escape(token)}"
    )
    if surface == "form":
        # form surface — append ?from=edit so the post-DELETE
        # handler HX-Redirects to the list page (per BX.1).
        delete_url += "&from=edit"
    ready_after_ms = int((start_ts + countdown_secs) * 1000)
    countdown_int = max(int(countdown_secs), 0)
    # Per-swap unique id so the inline JS can grab the right button by
    # ID after HTMX evaluates the swapped script tag. start_ts at
    # microsecond resolution disambiguates rapid-fire repeats of the
    # same entity's Delete click.
    btn_id = f"delete-confirm-btn-{int(start_ts * 1_000_000):x}"
    btn_cls = (
        "inline-flex items-center px-2 py-0.5 text-xs font-semibold "
        "border border-danger text-danger rounded-sm "
        "no-underline cursor-not-allowed opacity-70"
    )
    countdown_label = (
        f"Confirming… {countdown_int}s" if countdown_int > 0
        else "Confirm delete"
    )
    initial_state: DeleteState = (
        "ready" if countdown_int <= 0 else "countdown"
    )
    # When countdown_secs=0 the button arrives already in the "ready"
    # state — tests that monkeypatch COUNTDOWN_SECS=0 want the confirm
    # URL clickable immediately (no JS tick needed to remove
    # aria-disabled).
    aria_disabled_attr = (
        'aria-disabled="true"' if countdown_int > 0 else ""
    )
    btn = (
        f'<a id="{btn_id}" class="{btn_cls}" '
        f'data-role="card-delete-confirm" '
        f'data-delete-state="{initial_state}" '
        f'data-ready-after-ms="{ready_after_ms}" '
        f'data-countdown-remaining="{countdown_int}" '
        f'{aria_disabled_attr} '
        f'hx-delete="{delete_url}" '
        f'hx-target="closest [data-delete-wrapper]" hx-swap="innerHTML">'
        f'<span data-delete-confirm-label>{escape(countdown_label)}</span>'
        f'</a>'
    )
    # Inline JS: countdown ticker, flips state attr to "ready" at 0.
    # Identified by the per-swap unique id so post-HTMX-eval the
    # script finds the button reliably. 250ms tick tolerates
    # background-tab throttling without falling far behind the wall
    # clock. Skip the script when countdown_secs <= 0 — the button
    # arrives already in the "ready" state (test monkeypatch path).
    if countdown_int > 0:
        script = (
            '<script>(function(){'
            f"var btn=document.getElementById('{btn_id}');"
            "if(!btn)return;"
            "var label=btn.querySelector('[data-delete-confirm-label]');"
            "var readyAt=parseInt(btn.getAttribute('data-ready-after-ms'),10);"
            "function tick(){"
            "var remainingMs=readyAt-Date.now();"
            "if(remainingMs<=0){"
            "btn.removeAttribute('aria-disabled');"
            "btn.setAttribute('data-delete-state','ready');"
            "btn.setAttribute('data-countdown-remaining','0');"
            "btn.className='inline-flex items-center px-2 py-0.5 text-xs "
            "font-semibold border border-danger text-white bg-danger "
            "rounded-sm no-underline cursor-pointer hover:bg-red-700';"
            "if(label)label.textContent='Confirm delete';"
            "return;"
            "}"
            "var remainingSec=Math.ceil(remainingMs/1000);"
            "btn.setAttribute('data-countdown-remaining',String(remainingSec));"
            "if(label)label.textContent='Confirming\\u2026 '+remainingSec+'s';"
            "setTimeout(tick,250);"
            "}tick();"
            "})();</script>"
        )
    else:
        script = ""
    return f"{btn}{script}"
