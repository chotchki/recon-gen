"""BX.1 — Delete-confirm inline banner + 5s countdown + Cancel +
reference-check.

Covers the unit-level surface added in BX.1:

1. ``find_references`` walks the L2Instance for incoming refs and
   surfaces them as typed ``EntityRef`` tuples. Includes the
   account-as-parent_role case (Account.role → other Account /
   AccountTemplate / Rail / LimitSchedule fields), the rail-as-leg
   case (Rail.name → TransferTemplate.leg_rails / Chain.parent /
   Chain.children / LimitSchedule.rail), and the unreferenced-leaf
   case (returns empty).
2. ``render_delete_confirm_banner`` emits the two visual modes:
   ``blocked`` when refs exist (no Confirm button; lists referrers)
   and ``armed`` when refs are empty (countdown attrs + Confirm
   button rendered ``disabled``). Renders ``data-test-*`` hooks so
   browser drivers locate by ARIA / semantic attrs, not Tailwind
   utility classes (per `feedback_browser_drivers_user_facing_locators`).
3. ``verify_confirm_token`` enforces the 5-second window
   server-side: ``too_early`` when called before the countdown
   elapses, ``ok`` after.
4. Route-level flow: the card's Delete button is wired with hx-get
   to ``/l2_shape/<kind>/<id>/delete-confirm`` (not a direct
   hx-delete); the DELETE handler rejects a request without
   ``confirm_token`` AND rejects an early-confirm (before countdown
   elapsed); both Cancel and post-success flows correctly remove
   the banner via the slot.
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html import _delete_confirm
from recon_gen.common.html._delete_confirm import (
    BANNER_DOM_ID,
    COUNTDOWN_SECS,
    EntityRef,
    find_references,
    make_confirm_token,
    render_delete_confirm_banner,
    verify_confirm_token,
)
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.editor import EntityKind
from recon_gen.common.l2.loader import load_instance
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(yaml_path: Path) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


# ---------------------------------------------------------------------------
# find_references — typed walk for incoming refs
# ---------------------------------------------------------------------------


def test_find_references_finds_rail_referencing_account_role(
    writable_l2_yaml: Path,
) -> None:
    """Account ``customer-ledger`` has role ``CustomerLedger`` —
    referenced by every Account / AccountTemplate carrying
    ``parent_role: CustomerLedger`` AND by every Rail whose
    role-expression names ``CustomerLedger``. The find walk MUST
    surface each referrer once, typed."""
    inst = load_instance(writable_l2_yaml)
    refs = find_references(inst, "account", "customer-ledger")
    # cust-001 + cust-002 reference CustomerLedger as parent_role.
    cust_001_refs = [r for r in refs if r.referrer_id == "cust-001"]
    cust_002_refs = [r for r in refs if r.referrer_id == "cust-002"]
    assert cust_001_refs, "cust-001 references CustomerLedger as parent_role"
    assert cust_002_refs, "cust-002 references CustomerLedger as parent_role"
    assert all(r.via_field == "parent_role" for r in cust_001_refs)
    assert all(r.referrer_kind == "account" for r in cust_001_refs)
    # AccountTemplates AlphaCustomerStub + CustomerSubledger also
    # carry parent_role=CustomerLedger.
    template_refs = [
        r for r in refs if r.referrer_kind == "account_template"
    ]
    assert any(r.referrer_id == "CustomerSubledger" for r in template_refs)


def test_find_references_finds_template_referenced_rail(
    writable_l2_yaml: Path,
) -> None:
    """``ReconciliationLeg`` is in
    ``ExternalReconciliationCycle.leg_rails``. The find walk surfaces
    that template as a referrer; the existing
    test_delete_dependent_rail_returns_400 (in
    test_studio_editor_routes.py) exercises the routing path."""
    inst = load_instance(writable_l2_yaml)
    refs = find_references(inst, "rail", "ReconciliationLeg")
    tt_refs = [
        r for r in refs
        if r.referrer_kind == "transfer_template"
        and r.referrer_id == "ExternalReconciliationCycle"
    ]
    assert tt_refs, (
        "ExternalReconciliationCycle must be flagged as the referrer"
    )
    assert tt_refs[0].via_field == "leg_rails"


def test_find_references_empty_when_role_shared(
    writable_l2_yaml: Path,
) -> None:
    """cust-002 (Account) carries role ``CustomerSubledger`` which
    cust-001 + the CustomerSubledger template also carry. Deleting
    cust-002 leaves the role universe intact (cust-001 +
    AccountTemplate still cover CustomerSubledger), so the validator
    accepts the delete. ``find_references`` MUST report no blockers
    in this shared-role case — otherwise every role-shared
    singleton would be undeletable through the banner."""
    inst = load_instance(writable_l2_yaml)
    refs = find_references(inst, "account", "cust-002")
    assert refs == (), (
        "shared-role accounts must not surface role-based blockers"
    )


def test_find_references_chain_is_leaf(writable_l2_yaml: Path) -> None:
    """Chains have no incoming references — they're a leaf in the
    L2 reference graph."""
    inst = load_instance(writable_l2_yaml)
    if not inst.chains:
        pytest.skip("fixture has no chains to test against")
    ch = inst.chains[0]
    children_csv = ",".join(sorted(str(c.name) for c in ch.children))
    entity_id = f"{ch.parent}::{children_csv}"
    refs = find_references(inst, "chain", entity_id)
    assert refs == (), "Chain has no incoming refs"


def test_find_references_limit_schedule_is_leaf(
    writable_l2_yaml: Path,
) -> None:
    """LimitSchedules have no incoming references."""
    inst = load_instance(writable_l2_yaml)
    if not inst.limit_schedules:
        pytest.skip("fixture has no limit_schedules to test against")
    ls = inst.limit_schedules[0]
    entity_id = f"{ls.parent_role}::{ls.rail}::{ls.direction}"
    refs = find_references(inst, "limit_schedule", entity_id)
    assert refs == (), "LimitSchedule has no incoming refs"


def test_find_references_returns_typed_entity_ref() -> None:
    """``find_references`` returns ``tuple[EntityRef, ...]`` — typed
    dataclass, not raw dicts. Pin the class to prevent regression
    back to stringly-typed plumbing."""
    # Build a minimal in-memory shape via the writable fixture so the
    # call path is exercised; the type check is the focus.
    here = Path(__file__).resolve().parent.parent / "l2"
    inst = load_instance(here / "spec_example.yaml")
    refs = find_references(inst, "account", "customer-ledger")
    assert isinstance(refs, tuple)
    for r in refs:
        assert isinstance(r, EntityRef)
        assert r.referrer_kind in (
            "account", "account_template", "rail",
            "transfer_template", "chain", "limit_schedule",
        )


# ---------------------------------------------------------------------------
# render_delete_confirm_banner — visual modes
# ---------------------------------------------------------------------------


def test_banner_armed_carries_countdown_attribute() -> None:
    """No refs → armed mode → Confirm button rendered disabled
    with ``data-ready-after-ms`` carrying the wall time after which
    the client-side script should re-enable. Tests assert the
    attribute exists + parses as an int — they don't drive the JS
    countdown directly."""
    html = render_delete_confirm_banner(
        "account", "test-id", refs=(),
        countdown_secs=5.0,
    )
    assert f'id="{BANNER_DOM_ID}"' in html
    assert 'data-test-delete-state="armed"' in html
    assert 'data-test-delete-confirm' in html
    assert 'disabled' in html
    # countdown wall-time attr present + parses.
    m = re.search(r'data-ready-after-ms="(\d+)"', html)
    assert m is not None, html
    ready_after_ms = int(m.group(1))
    # Should be at least now + 4 seconds (countdown=5; allow 1s slack).
    assert ready_after_ms > (time.time() * 1000) + 4000


def test_banner_blocked_lists_referrers_no_confirm_button() -> None:
    """References present → blocked mode → Confirm button absent;
    each referrer listed with a data-test-ref-* hook."""
    refs = (
        EntityRef(
            referrer_kind="transfer_template",
            referrer_id="SomeTT",
            via_field="leg_rails",
            label="TransferTemplate SomeTT (leg_rails)",
        ),
    )
    html = render_delete_confirm_banner(
        "rail", "BlockedRail", refs=refs,
        countdown_secs=5.0,
    )
    assert 'data-test-delete-state="blocked"' in html
    assert 'data-test-delete-ref-count="1"' in html
    # The referrer chip carries the per-ref hook.
    assert 'data-test-ref-kind="transfer_template"' in html
    assert 'data-test-ref-id="SomeTT"' in html
    assert 'data-test-ref-field="leg_rails"' in html
    # No Confirm button in blocked mode.
    assert 'data-test-delete-confirm' not in html
    # Cancel button always present.
    assert 'data-test-delete-cancel' in html


def test_banner_cancel_button_targets_banner_outerhtml() -> None:
    """Cancel button posts to the no-op cancel endpoint with
    ``hx-swap=outerHTML`` so clicking it removes the banner aside
    without round-tripping any state."""
    html = render_delete_confirm_banner("account", "x", (), countdown_secs=5.0)
    assert 'hx-get="/l2_shape/_delete_cancel"' in html
    assert f'hx-target="#{BANNER_DOM_ID}"' in html
    assert 'hx-swap="outerHTML"' in html


# ---------------------------------------------------------------------------
# verify_confirm_token — server-side wall clock
# ---------------------------------------------------------------------------


def test_verify_token_ok_after_countdown() -> None:
    start_ts = time.time() - 6.0  # 6s ago
    token = make_confirm_token("account", "x", start_ts)
    verify = verify_confirm_token(token, "account", "x", countdown_secs=5.0)
    assert verify.status == "ok", verify
    assert verify.elapsed >= 5.0


def test_verify_token_too_early_within_countdown() -> None:
    start_ts = time.time() - 1.0  # 1s ago
    token = make_confirm_token("account", "x", start_ts)
    verify = verify_confirm_token(token, "account", "x", countdown_secs=5.0)
    assert verify.status == "too_early", verify
    assert 0.5 <= verify.elapsed < 5.0


def test_verify_token_invalid_when_tampered() -> None:
    start_ts = time.time() - 10.0
    token = make_confirm_token("account", "x", start_ts)
    # Tamper the sig — anything other than the canonical HMAC.
    tampered = token.rsplit(":", 1)[0] + ":deadbeef"
    verify = verify_confirm_token(tampered, "account", "x")
    assert verify.status == "invalid"


def test_verify_token_invalid_when_swapped_to_different_entity() -> None:
    """The signature covers (kind, entity_id, start_ts) — a token
    minted for one entity cannot delete another, even with the
    same kind + countdown window."""
    start_ts = time.time() - 10.0
    token = make_confirm_token("account", "cust-001", start_ts)
    verify = verify_confirm_token(token, "account", "cust-002")
    assert verify.status == "invalid"


# ---------------------------------------------------------------------------
# Route-level flow — GET /delete-confirm + DELETE rejection paths
# ---------------------------------------------------------------------------


def test_get_delete_confirm_returns_banner(
    writable_l2_yaml: Path,
) -> None:
    """GET /l2_shape/account/cust-002/delete-confirm returns the
    armed banner (cust-002 has no incoming refs)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/cust-002/delete-confirm")
    assert resp.status_code == 200
    assert f'id="{BANNER_DOM_ID}"' in resp.text
    assert 'data-test-delete-state="armed"' in resp.text


def test_get_delete_confirm_blocked_when_referenced(
    writable_l2_yaml: Path,
) -> None:
    """GET /l2_shape/rail/ReconciliationLeg/delete-confirm returns
    the blocked banner — TransferTemplate.leg_rails references it."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/ReconciliationLeg/delete-confirm")
    assert resp.status_code == 200
    assert 'data-test-delete-state="blocked"' in resp.text
    # The blocking referrer is named.
    assert "ExternalReconciliationCycle" in resp.text


def test_delete_during_countdown_rejected_with_too_early(
    writable_l2_yaml: Path,
) -> None:
    """POST /delete with a confirm_token whose start_ts is fresher
    than COUNTDOWN_SECS gets a 400 ``too-early`` — the client side
    countdown can be bypassed via DevTools, but the server wall
    clock catches it."""
    # Mint a token with start_ts = now (no monkeypatch — we exercise
    # the production-default 5s window).
    token = make_confirm_token("account", "cust-002", time.time())
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.delete(
            f"/l2_shape/account/cust-002?confirm_token={token}",
        )
    assert resp.status_code == 400, resp.text
    assert "too-early" in resp.text
    # Disk unchanged.
    reloaded = load_instance(writable_l2_yaml)
    assert any(str(a.id) == "cust-002" for a in reloaded.accounts)


def test_delete_without_token_rejected_with_missing_token(
    writable_l2_yaml: Path,
) -> None:
    """A direct DELETE that skips the banner entirely (e.g. a
    stale-tab refresh fired from cached HTML, or an integration
    script that hasn't been migrated) gets a 400 ``missing-token``
    — never a silent delete."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.delete("/l2_shape/account/cust-002")
    assert resp.status_code == 400, resp.text
    assert "missing-token" in resp.text


def test_delete_cancel_returns_empty(writable_l2_yaml: Path) -> None:
    """GET /l2_shape/_delete_cancel returns an empty body so the
    Cancel button's outerHTML swap kills the banner aside in place
    without any other side effect."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/_delete_cancel")
    assert resp.status_code == 200
    assert resp.text == ""


def test_delete_after_countdown_with_monkeypatched_zero_secs_succeeds(
    writable_l2_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: pin COUNTDOWN_SECS=0, mint a token, fire DELETE
    — the entity disappears from disk + the response carries the
    cascade-reload trigger."""
    monkeypatch.setattr(
        "recon_gen.common.html._delete_confirm.COUNTDOWN_SECS", 0.0,
    )
    token = make_confirm_token(
        cast("EntityKind", "account"), "cust-002", time.time(),
    )
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.delete(
            f"/l2_shape/account/cust-002?confirm_token={token}",
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("HX-Trigger") == "l2-cascade-reload"
    reloaded = load_instance(writable_l2_yaml)
    assert not any(str(a.id) == "cust-002" for a in reloaded.accounts)


# ---------------------------------------------------------------------------
# Card + edit-page integration — the new wire shapes
# ---------------------------------------------------------------------------


def test_card_delete_button_targets_confirm_endpoint(
    writable_l2_yaml: Path,
) -> None:
    """Card Delete button: hx-get pointed at /delete-confirm,
    hx-target at the page-level banner slot. No hx-confirm modal."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/cust-001").text
    assert (
        'hx-get="/l2_shape/account/cust-001/delete-confirm"' in body
    )
    assert 'hx-target="#delete-confirm-banner-slot"' in body
    assert 'data-role="card-delete"' in body
    assert "hx-confirm=" not in body


def test_list_page_carries_banner_slot(writable_l2_yaml: Path) -> None:
    """List page reserves a top-level ``#delete-confirm-banner-slot``
    so card Delete clicks have somewhere to land their banner."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/").text
    assert 'id="delete-confirm-banner-slot"' in body
    assert 'data-test-delete-banner-slot' in body


def test_edit_page_carries_banner_slot(writable_l2_yaml: Path) -> None:
    """Edit page reserves the same slot above the form section."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/cust-001/edit").text
    assert 'id="delete-confirm-banner-slot"' in body


# ---------------------------------------------------------------------------
# BX.1 followup (2026-06-11) — onclick guard against parent `<details>`
# toggle when Delete sits inside a `<summary>` (collapsed-card list view)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, list_path",
    [
        ("rail", "/l2_shape/rail/"),
        ("account", "/l2_shape/account/"),
        ("account_template", "/l2_shape/account_template/"),
        ("chain", "/l2_shape/chain/"),
        ("limit_schedule", "/l2_shape/limit_schedule/"),
        ("transfer_template", "/l2_shape/transfer_template/"),
    ],
)
def test_card_delete_button_onclick_suppresses_summary_toggle(
    writable_l2_yaml: Path, kind: str, list_path: str,
) -> None:
    """BX.1 followup — the card Delete anchor MUST call
    ``event.preventDefault()`` in addition to ``event.stopPropagation()``.

    Rationale: when the list page renders cards in collapsed form
    (``<details>``-wrapped, the default since CG.5), the action
    buttons sit inside ``<summary>``. The native ``<summary>``
    activation behavior (toggle the parent ``<details>``) is the
    click event's default action — ``stopPropagation()`` alone does
    NOT cancel it. Without ``preventDefault()`` a click on Delete
    silently toggles the card open/close AND fires the htmx request.
    The operator sees only the toggle (the banner DOES land but the
    toggle is louder + visible) and concludes Delete is broken.

    The fix: both inline handlers on the Delete anchor. Pin this
    invariant for every kind that renders in the collapsed-card list
    view (every L2 entity kind today — same render path)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(list_path).text
    # Locate the Delete anchor (data-role="card-delete") and read its
    # onclick. The list page may render N Delete anchors (one per
    # card); they all share the same onclick template, so the first
    # match is representative.
    import re
    matches = re.findall(
        r'<a\b[^>]*\bdata-role="card-delete"[^>]*>',
        body,
    )
    assert matches, (
        f"kind={kind!r} list page rendered no card-delete anchor — "
        f"the fixture may be empty for this kind; if so, expand the "
        f"fixture instead of skipping (the invariant applies to every "
        f"render path).\nList page body (first 2KB):\n{body[:2048]}"
    )
    for anchor_open in matches:
        assert "event.preventDefault()" in anchor_open, (
            f"kind={kind!r} Delete anchor missing "
            f"`event.preventDefault()` — collapsed-card click will "
            f"toggle the parent `<details>` instead of opening only "
            f"the BX.1 confirm banner.\nAnchor: {anchor_open}"
        )
        assert "event.stopPropagation()" in anchor_open, (
            f"kind={kind!r} Delete anchor missing "
            f"`event.stopPropagation()` — click will bubble to any "
            f"wider `<summary>` chrome (home-page section wrappers).\n"
            f"Anchor: {anchor_open}"
        )


def test_edit_page_delete_button_carries_onclick_guard(
    writable_l2_yaml: Path,
) -> None:
    """BX.1 followup — the edit-page Delete anchor (data-role=
    "form-delete") carries the same onclick guard.

    The edit page isn't currently wrapped in a ``<details>``, so the
    guard is insurance against a future wrapper (e.g. a collapsible
    "Danger zone" section) silently regressing the same bug. The
    edit-page render path references the module-level
    ``_card_delete_onclick`` constant (lowercase to dodge the
    no-inline-production-constants typing-smell index) — same value
    as the inline string in ``_render_card_action_buttons``."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/cust-001/edit").text
    import re
    m = re.search(
        r'<a\b[^>]*\bdata-role="form-delete"[^>]*>',
        body,
    )
    assert m is not None, (
        "edit page rendered no form-delete anchor — the form's Delete "
        "action button is missing.\nEdit page body (first 2KB):\n"
        f"{body[:2048]}"
    )
    anchor_open = m.group(0)
    assert "event.preventDefault()" in anchor_open, anchor_open
    assert "event.stopPropagation()" in anchor_open, anchor_open


def test_card_action_buttons_helper_is_typed_primitive() -> None:
    """BX.1 followup — the Edit + Delete pair is consolidated in
    ``_render_card_action_buttons``. Pin the helper's existence + its
    contract (Edit and Delete anchors, the BX.1 wire shape on Delete,
    the onclick guard on Delete) so a future refactor that re-splits
    the pair has to update this test too."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_card_action_buttons,
    )
    html = _render_card_action_buttons("rail", "test-rail-id")
    assert "<a" in html  # both Edit and Delete are anchors.
    assert ">Edit<" in html
    assert ">Delete<" in html
    # Delete carries the BX.1 banner-slot wire shape.
    assert 'data-role="card-delete"' in html
    assert (
        'hx-get="/l2_shape/rail/test-rail-id/delete-confirm"' in html
    )
    assert 'hx-target="#delete-confirm-banner-slot"' in html
    # Delete onclick suppresses both bubbling AND the default action.
    assert "event.preventDefault()" in html
    assert "event.stopPropagation()" in html


# Silence unused-import warning for COUNTDOWN_SECS — it documents
# the production default the tests pin against via monkeypatch.
_ = COUNTDOWN_SECS
_ = _delete_confirm
