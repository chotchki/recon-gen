"""BX.1 — Delete-confirm inline banner with reference-check + 5s countdown.

Replaces the previous browser-native ``hx-confirm="Delete this entity?"``
modal with an inline banner that:

1. **References-first.** Walks the L2Instance for every entity that
   references the deletion target (Rail.source_role pointing at the
   account's role, TransferTemplate.leg_rails containing the rail's
   name, Chain.children naming this rail/template, etc.). Renders a
   "referenced by" listing.

   When references are present → the banner renders in **blocked**
   mode: Confirm button is absent. The operator must remove the
   references first. Matches the SPEC's "structural break = reject,
   don't auto-cascade" rule the existing DELETE handler enforces via
   ``validate()``, but surfaces the same information **before** the
   destructive click so the operator doesn't have to learn it through
   the post-hoc 400.

2. **5-second countdown.** When the entity is unreferenced, the
   Confirm button renders disabled with ``data-ready-after="<ts>"``.
   A small inline ``<script>`` re-enables it once ``Date.now() >=
   ready_after``. The text counter decrements visibly so the
   countdown is obvious.

3. **Cancel kills it.** Cancel is an ``hx-get`` that replaces the
   banner with an empty fragment — the existing card / edit form
   stays untouched.

4. **Server-signed token.** The Confirm button posts to
   ``DELETE /l2_shape/<kind>/<id>?confirm_token=<sig>``. The token is
   an HMAC over ``(kind, entity_id, start_ts)`` keyed by a per-process
   secret. The DELETE handler verifies the signature AND enforces
   ``now - start_ts >= countdown_secs`` server-side — a client that
   tried to disable the disabled-attribute via DevTools still hits
   the server clock check.

The banner is the same inline-aside shape as ``_plant_banner.py``
(BTa.2-era): no modal chrome, ``role="status"`` alert, lives in the
DOM where the card/edit-form sat.

Per ``[feedback_invariants_in_types]``: typed dataclasses for refs +
typed verify result. Per ``[feedback_browser_drivers_user_facing_locators]``:
``data-*`` test hooks, no Tailwind classes as locators.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Iterator
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
# server invalidates outstanding banners — acceptable for a single-user
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


@dataclass(frozen=True, slots=True)
class EntityRef:
    """One reference from another entity to the deletion target.

    Typed so the renderer + tests can introspect each field without
    parsing a string. ``referrer_kind`` + ``referrer_id`` address the
    referring entity (the one the operator must edit / delete first);
    ``via_field`` names the field carrying the reference; ``label`` is
    an operator-readable summary the banner displays.
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
# Banner rendering
# ---------------------------------------------------------------------------


# The DOM id of the banner container — the cancel/replace HTMX target
# hooks key off this. Module-level so tests can pin the same string.
BANNER_DOM_ID = "delete-confirm-banner"


def render_delete_confirm_banner(
    kind: EntityKind,
    entity_id: str,
    refs: tuple[EntityRef, ...],
    *,
    now: float | None = None,
    countdown_secs: float = COUNTDOWN_SECS,
) -> str:
    """Render the inline confirm banner.

    Two visual modes:

    - ``blocked`` — references exist → red banner, listing each
      referrer; Confirm button absent. Operator must Cancel + remove
      the references first.
    - ``armed`` — no references → amber banner, countdown timer,
      Confirm button disabled until ``ready_after`` server time
      passes. Cancel always present.

    The Cancel button is an HTMX ``hx-get`` against a no-op endpoint
    that returns empty HTML — replaces the banner with nothing.
    """
    start_ts = time.time() if now is None else now
    blocked = len(refs) > 0
    if blocked:
        return _render_blocked(kind, entity_id, refs)
    token = make_confirm_token(kind, entity_id, start_ts)
    return _render_armed(
        kind, entity_id,
        start_ts=start_ts,
        countdown_secs=countdown_secs,
        token=token,
    )


def _render_blocked(
    kind: EntityKind, entity_id: str, refs: tuple[EntityRef, ...],
) -> str:
    """References present — delete blocked. Lists the referrers with
    edit-links so the operator can navigate-and-fix in one click."""
    ref_items: list[str] = []
    for r in refs:
        edit_url = _referrer_edit_url(r)
        ref_items.append(
            f'<li data-test-delete-ref '
            f'data-test-ref-kind="{escape(r.referrer_kind)}" '
            f'data-test-ref-id="{escape(r.referrer_id)}" '
            f'data-test-ref-field="{escape(r.via_field)}">'
            f'<a class="text-accent no-underline hover:underline" '
            f'href="{escape(edit_url)}">'
            f'{escape(r.label)}</a></li>'
        )
    items_html = "".join(ref_items)
    cancel_btn = (
        f'<button type="button" '
        f'class="inline-flex items-center px-3 py-1 text-xs font-semibold '
        f'border border-surface-border text-primary-fg bg-white rounded-sm '
        f'cursor-pointer hover:bg-surface-bg" '
        f'data-test-delete-cancel '
        f'hx-get="/l2_shape/_delete_cancel" '
        f'hx-target="#{BANNER_DOM_ID}" hx-swap="outerHTML">'
        f'Cancel</button>'
    )
    return (
        f'<aside id="{BANNER_DOM_ID}" '
        f'class="mx-0 mt-4 mb-4 bg-red-50 border border-danger '
        f'rounded-md px-4 py-3 text-sm" '
        f'role="alert" '
        f'data-test-delete-banner '
        f'data-test-delete-state="blocked" '
        f'data-test-delete-kind="{escape(kind)}" '
        f'data-test-delete-entity-id="{escape(entity_id)}" '
        f'data-test-delete-ref-count="{len(refs)}">'
        f'<strong class="text-danger">Delete blocked.</strong> '
        f'<span class="text-secondary-fg">'
        f'{escape(kind)} <code>{escape(entity_id)}</code> is '
        f'referenced by the following — edit or delete those first, '
        f'then try again.'
        f'</span>'
        f'<ul class="mt-2 mb-3 pl-6 list-disc text-sm text-primary-fg" '
        f'data-test-delete-ref-list>'
        f'{items_html}'
        f'</ul>'
        f'<div class="flex items-center gap-2">{cancel_btn}</div>'
        f'</aside>'
    )


def _render_armed(
    kind: EntityKind,
    entity_id: str,
    *,
    start_ts: float,
    countdown_secs: float,
    token: str,
) -> str:
    """No references — countdown then Confirm. The Confirm button
    renders disabled with ``data-ready-after-ms`` carrying the wall
    time when it should re-enable. A small inline script flips
    ``disabled`` and updates the visible counter."""
    ready_after_ms = int((start_ts + countdown_secs) * 1000)
    countdown_int = int(countdown_secs)
    # The confirm button posts (via htmx hx-delete) to the existing
    # DELETE route + the signed confirm token query param. On success
    # the existing handler returns the same HX-Trigger / HX-Redirect
    # shape it always has.
    delete_url = (
        f"/l2_shape/{escape(kind)}/{escape(entity_id)}"
        f"?confirm_token={escape(token)}"
    )
    confirm_btn = (
        f'<button type="button" '
        f'class="inline-flex items-center px-3 py-1 text-xs font-semibold '
        f'border border-danger text-white bg-danger rounded-sm '
        f'cursor-not-allowed opacity-60" '
        f'data-test-delete-confirm '
        f'data-ready-after-ms="{ready_after_ms}" '
        f'data-countdown-secs="{countdown_int}" '
        f'disabled '
        f'hx-delete="{delete_url}" '
        f'hx-target="#{BANNER_DOM_ID}" hx-swap="outerHTML">'
        f'<span data-test-delete-confirm-label>'
        f'Confirm delete in {countdown_int}s'
        f'</span></button>'
    )
    cancel_btn = (
        f'<button type="button" '
        f'class="inline-flex items-center px-3 py-1 text-xs font-semibold '
        f'border border-surface-border text-primary-fg bg-white rounded-sm '
        f'cursor-pointer hover:bg-surface-bg" '
        f'data-test-delete-cancel '
        f'hx-get="/l2_shape/_delete_cancel" '
        f'hx-target="#{BANNER_DOM_ID}" hx-swap="outerHTML">'
        f'Cancel</button>'
    )
    # Inline script: counts down on the button, flips disabled + tw
    # classes at zero. Scoped to this banner by id so multiple banners
    # don't cross-trigger. Note the script uses string concatenation
    # (not f-string at JS level) — Python f-string parses { and } as
    # placeholders; the JS uses a template literal which would clash.
    script_block = (
        '<script>(function(){'
        f"var el=document.querySelector('#{BANNER_DOM_ID} "
        "[data-test-delete-confirm]');"
        "if(!el)return;"
        "var label=el.querySelector('[data-test-delete-confirm-label]');"
        "var readyAt=parseInt(el.getAttribute('data-ready-after-ms'),10);"
        "function tick(){"
        "var now=Date.now();"
        "var remainingMs=readyAt-now;"
        "if(remainingMs<=0){"
        "el.removeAttribute('disabled');"
        "el.className='inline-flex items-center px-3 py-1 text-xs "
        "font-semibold border border-danger text-white bg-danger "
        "rounded-sm cursor-pointer hover:bg-red-700';"
        "if(label)label.textContent='Confirm delete';"
        "return;"
        "}"
        "var remainingSec=Math.ceil(remainingMs/1000);"
        "if(label)label.textContent='Confirm delete in '+remainingSec+'s';"
        "setTimeout(tick,100);"
        "}tick();"
        "})();</script>"
    )
    return (
        f'<aside id="{BANNER_DOM_ID}" '
        f'class="mx-0 mt-4 mb-4 bg-warning/5 border border-warning '
        f'rounded-md px-4 py-3 text-sm" '
        f'role="alert" '
        f'data-test-delete-banner '
        f'data-test-delete-state="armed" '
        f'data-test-delete-kind="{escape(kind)}" '
        f'data-test-delete-entity-id="{escape(entity_id)}" '
        f'data-test-delete-ref-count="0" '
        f'data-test-delete-ready-after-ms="{ready_after_ms}">'
        f'<strong class="text-warning">Confirm delete.</strong> '
        f'<span class="text-secondary-fg">'
        f'No other entities reference {escape(kind)} '
        f'<code>{escape(entity_id)}</code>. Wait {countdown_int}s, '
        f'then click Confirm. Cancel anytime.'
        f'</span>'
        f'<div class="flex items-center gap-2 mt-3">'
        f'{confirm_btn}{cancel_btn}'
        f'</div>'
        f'{script_block}'
        f'</aside>'
    )


def _referrer_edit_url(ref: EntityRef) -> str:
    """Build the edit URL for a referrer so the operator can click
    through to fix it. Uses ``?from=`` so the back-breadcrumb returns
    them to wherever they triggered the delete from (BTa.2 P1.5)."""
    return (
        f"/l2_shape/{ref.referrer_kind}/{ref.referrer_id}/edit"
        f"?from=delete-blocked"
    )


