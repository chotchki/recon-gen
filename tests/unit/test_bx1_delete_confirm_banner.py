"""BX.1 — In-place Delete button + reference-check + countdown.

Post-redesign (2026-06-11): replaces the top-of-page banner UX with
an in-place wrapper-swap. Three render-time states + one post-click
state, tested here at the unit level:

1. ``find_references`` walks the L2Instance for incoming refs and
   surfaces them as typed ``EntityRef`` tuples. Includes the
   account-as-parent_role case, the rail-as-leg case, and the
   unreferenced-leaf case (returns empty).
2. ``build_reference_graph`` precomputes ``find_references`` for
   every entity in one pass so a list-page render is O(N) not O(N²).
3. ``render_active_delete_button`` / ``render_refused_delete_button``
   / ``render_countdown_swap`` emit the three render-time states +
   the post-click swap body. State lives in ``data-delete-state``
   attribute (per ``[feedback_invariants_in_types]``); tooltip
   reason lives in ``title`` + ``data-delete-reason`` (per
   ``[feedback_browser_drivers_user_facing_locators]``).
4. ``verify_confirm_token`` enforces the 5-second window
   server-side.
5. Route-level flow: card Delete points at the in-place confirm
   endpoint with ``hx-target="closest [data-delete-wrapper]"``;
   the cancel endpoint restores the active button.
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
    COUNTDOWN_SECS,
    EntityRef,
    ReferenceGraph,
    build_reference_graph,
    find_references,
    make_confirm_token,
    render_active_delete_button,
    render_active_delete_button_inner,
    render_countdown_swap,
    render_refused_delete_button,
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
    cust_001_refs = [r for r in refs if r.referrer_id == "cust-001"]
    cust_002_refs = [r for r in refs if r.referrer_id == "cust-002"]
    assert cust_001_refs, "cust-001 references CustomerLedger as parent_role"
    assert cust_002_refs, "cust-002 references CustomerLedger as parent_role"
    assert all(r.via_field == "parent_role" for r in cust_001_refs)
    assert all(r.referrer_kind == "account" for r in cust_001_refs)
    template_refs = [
        r for r in refs if r.referrer_kind == "account_template"
    ]
    assert any(r.referrer_id == "CustomerSubledger" for r in template_refs)


def test_find_references_finds_template_referenced_rail(
    writable_l2_yaml: Path,
) -> None:
    """``ReconciliationLeg`` is in
    ``ExternalReconciliationCycle.leg_rails``."""
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
    accepts the delete. ``find_references`` MUST report no blockers."""
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


def test_find_references_returns_typed_entity_ref(
    writable_l2_yaml: Path,
) -> None:
    """``find_references`` returns ``tuple[EntityRef, ...]`` — typed
    dataclass, not raw dicts. Pin the class to prevent regression
    back to stringly-typed plumbing."""
    inst = load_instance(writable_l2_yaml)
    refs = find_references(inst, "account", "customer-ledger")
    assert isinstance(refs, tuple)
    for r in refs:
        assert isinstance(r, EntityRef)
        assert r.referrer_kind in (
            "account", "account_template", "rail",
            "transfer_template", "chain", "limit_schedule",
        )


# ---------------------------------------------------------------------------
# build_reference_graph — precompute, O(1) per-card lookup
# ---------------------------------------------------------------------------


def test_build_reference_graph_caches_every_kind(
    writable_l2_yaml: Path,
) -> None:
    """Graph is built ONCE per render; lookup returns the same
    refs as a fresh `find_references` call. Pin the equivalence so
    we don't silently miss a kind in the cache walk."""
    inst = load_instance(writable_l2_yaml)
    graph = build_reference_graph(inst)
    # Spot-check: every entity the fixture carries should resolve
    # through the graph to the same value find_references returns.
    for acc in inst.accounts:
        assert graph.refs_for("account", str(acc.id)) == find_references(
            inst, "account", str(acc.id),
        )
    for r in inst.rails:
        assert graph.refs_for("rail", str(r.name)) == find_references(
            inst, "rail", str(r.name),
        )


def test_build_reference_graph_missing_key_returns_empty_tuple() -> None:
    """A lookup for an entity not in the graph (stale URL / typo)
    returns the empty tuple — the active-state Delete renders and
    the DELETE handler still validates server-side."""
    inst = load_instance(_FIXTURES / "spec_example.yaml")
    graph = build_reference_graph(inst)
    refs = graph.refs_for("account", "no-such-account")
    assert refs == ()


# ---------------------------------------------------------------------------
# render_active_delete_button + render_refused_delete_button — render-time states
# ---------------------------------------------------------------------------


def test_active_button_carries_wrapper_and_state_attr() -> None:
    """No refs → active state → wrapper around an at-rest Delete
    anchor with ``data-delete-state="active"``. Wrapper exposes
    ``data-delete-wrapper`` so HTMX can target it via ``closest``."""
    html = render_active_delete_button(
        "account", "test-id", surface="card",
    )
    assert "data-delete-wrapper" in html
    assert 'data-delete-state="active"' in html
    assert 'data-role="card-delete"' in html
    # The Delete anchor fires hx-get against the in-place confirm URL.
    assert 'hx-get="/l2_shape/account/test-id/delete-confirm"' in html
    # Wrapper-targeted swap (closest [data-delete-wrapper]).
    assert 'hx-target="closest [data-delete-wrapper]"' in html
    assert 'hx-swap="innerHTML"' in html
    # No page-level slot referenced.
    assert "#delete-confirm-banner-slot" not in html
    # No browser-native modal — BX.1 lock.
    assert "hx-confirm=" not in html


def test_active_button_form_surface_carries_from_edit() -> None:
    """``surface="form"`` adds ``?from=edit`` so the post-DELETE
    handler HX-Redirects to the list page instead of leaving the
    operator on a stale edit form."""
    html = render_active_delete_button(
        "account", "test-id", surface="form",
    )
    assert (
        'hx-get="/l2_shape/account/test-id/delete-confirm?from=edit"'
        in html
    )
    assert 'data-role="form-delete"' in html


def test_refused_button_carries_disabled_attrs_and_reason() -> None:
    """Refs non-empty → refused state → anchor with
    ``aria-disabled="true"``, ``data-delete-state="refused"``, the
    reason in ``title`` AND ``data-delete-reason`` so the e2e
    driver can read the reason without triggering the tooltip
    render."""
    refs = (
        EntityRef(
            referrer_kind="transfer_template",
            referrer_id="ExternalReconciliationCycle",
            via_field="leg_rails",
            label="TransferTemplate ExternalReconciliationCycle (leg_rails)",
        ),
    )
    html = render_refused_delete_button(
        "rail", "ReconciliationLeg", refs, surface="card",
    )
    assert 'data-delete-state="refused"' in html
    assert 'aria-disabled="true"' in html
    assert 'data-delete-ref-count="1"' in html
    # Reason text reads banking-domain-readable per
    # `[project_design_north_stars]`. NOT "FK violation".
    assert 'data-delete-reason="' in html
    assert "TransferTemplate" in html
    assert "ExternalReconciliationCycle" in html
    assert "leg_rails" in html
    # Tooltip-friendly title carries the same text.
    assert 'title="Referenced by' in html
    # No hx-get on the refused anchor — clicking does nothing.
    assert "hx-get=" not in html
    # Wrapper still present so a future state change (operator
    # removes referrers + re-renders the card) lands on the same
    # swap boundary.
    assert "data-delete-wrapper" in html


def test_refused_button_caps_reason_at_three_referrers() -> None:
    """When more than 3 entities reference the target, the reason
    text caps the list at 3 + ``+N more`` so the title/tooltip stays
    readable. The full list lives in the operator's blocker-
    resolution path (clicking through to each referrer's edit page)."""
    refs = tuple(
        EntityRef(
            referrer_kind="transfer_template",
            referrer_id=f"TT_{i}",
            via_field="leg_rails",
            label=f"TransferTemplate TT_{i} (leg_rails)",
        )
        for i in range(5)
    )
    html = render_refused_delete_button(
        "rail", "TestRail", refs, surface="card",
    )
    assert "TT_0" in html
    assert "TT_1" in html
    assert "TT_2" in html
    # +2 more (refs 3, 4 not enumerated by name).
    assert "+2 more" in html
    assert 'data-delete-ref-count="5"' in html


# ---------------------------------------------------------------------------
# render_countdown_swap — post-click in-place body
# ---------------------------------------------------------------------------


def test_countdown_swap_carries_state_and_ready_after_ms() -> None:
    """Countdown swap body: Confirm anchor with
    ``data-delete-state="countdown"``, ``aria-disabled``, the
    ``data-ready-after-ms`` wall time + the visible label "Confirming…
    Ns". Sibling Cancel link points at the in-place cancel endpoint
    targeting ``closest [data-delete-wrapper]``."""
    html = render_countdown_swap(
        "account", "cust-001", "cust-001",
        countdown_secs=5.0,
    )
    assert 'data-delete-state="countdown"' in html
    assert 'data-role="card-delete-confirm"' in html
    assert 'aria-disabled="true"' in html
    # Countdown wall time.
    m = re.search(r'data-ready-after-ms="(\d+)"', html)
    assert m is not None, html
    ready_after_ms = int(m.group(1))
    assert ready_after_ms > (time.time() * 1000) + 4000
    # Visible countdown label.
    assert "Confirming… 5s" in html
    # hx-delete fires against the real DELETE endpoint with the
    # signed confirm token (verifier round-trip below).
    assert 'hx-delete="/l2_shape/account/cust-001?confirm_token=' in html
    assert 'hx-target="closest [data-delete-wrapper]"' in html
    # Cancel link → wrapper innerHTML restored to active state.
    assert 'data-role="card-delete-cancel"' in html
    assert 'hx-get="/l2_shape/account/cust-001/delete-cancel"' in html


def test_countdown_swap_zero_seconds_arrives_ready() -> None:
    """When ``countdown_secs=0`` (the test monkeypatch path), the
    swap body arrives already in the "ready" state — no
    ``aria-disabled``, no countdown ticker script. Tests that
    monkeypatch ``COUNTDOWN_SECS=0`` rely on this to fire DELETE
    immediately."""
    html = render_countdown_swap(
        "account", "cust-001", "cust-001",
        countdown_secs=0.0,
    )
    assert 'data-delete-state="ready"' in html
    assert "aria-disabled" not in html
    # Visible label is already "Confirm delete" (not "Confirming…").
    assert "Confirm delete" in html
    assert "Confirming…" not in html
    # No JS ticker needed when countdown <=0.
    assert "<script>" not in html


def test_countdown_swap_form_surface_carries_from_edit() -> None:
    """Form surface (edit page) appends ``&from=edit`` to the
    hx-delete URL + ``?from=edit`` to the cancel URL so the
    post-delete handler redirects to the list page."""
    html = render_countdown_swap(
        "account", "cust-001", "cust-001",
        countdown_secs=1.0, surface="form",
    )
    assert "&from=edit" in html
    assert (
        'hx-get="/l2_shape/account/cust-001/delete-cancel?from=edit"'
        in html
    )


def test_render_active_delete_button_inner_returns_no_wrapper() -> None:
    """The Cancel endpoint returns just the inner anchor (no
    wrapper open/close) so the existing wrapper survives the swap
    and only its innerHTML changes back to the active state."""
    html = render_active_delete_button_inner(
        "account", "cust-001", surface="card",
    )
    # No wrapper <span> emitted — the existing wrapper survives the
    # swap. The hx-target attr "closest [data-delete-wrapper]" still
    # contains the literal string; assert against the opening tag.
    assert "<span data-delete-wrapper" not in html
    assert 'data-delete-state="active"' in html
    assert 'hx-get="/l2_shape/account/cust-001/delete-confirm"' in html


# ---------------------------------------------------------------------------
# verify_confirm_token — server-side wall clock
# ---------------------------------------------------------------------------


def test_verify_token_ok_after_countdown() -> None:
    start_ts = time.time() - 6.0
    token = make_confirm_token("account", "x", start_ts)
    verify = verify_confirm_token(token, "account", "x", countdown_secs=5.0)
    assert verify.status == "ok", verify
    assert verify.elapsed >= 5.0


def test_verify_token_too_early_within_countdown() -> None:
    start_ts = time.time() - 1.0
    token = make_confirm_token("account", "x", start_ts)
    verify = verify_confirm_token(token, "account", "x", countdown_secs=5.0)
    assert verify.status == "too_early", verify
    assert 0.5 <= verify.elapsed < 5.0


def test_verify_token_invalid_when_tampered() -> None:
    start_ts = time.time() - 10.0
    token = make_confirm_token("account", "x", start_ts)
    tampered = token.rsplit(":", 1)[0] + ":deadbeef"
    verify = verify_confirm_token(tampered, "account", "x")
    assert verify.status == "invalid"


def test_verify_token_invalid_when_swapped_to_different_entity() -> None:
    """The signature covers (kind, entity_id, start_ts) — a token
    minted for one entity cannot delete another."""
    start_ts = time.time() - 10.0
    token = make_confirm_token("account", "cust-001", start_ts)
    verify = verify_confirm_token(token, "account", "cust-002")
    assert verify.status == "invalid"


# ---------------------------------------------------------------------------
# Route-level flow — confirm + cancel endpoints, DELETE rejection paths
# ---------------------------------------------------------------------------


def test_get_delete_confirm_returns_countdown_swap(
    writable_l2_yaml: Path,
) -> None:
    """GET /l2_shape/account/cust-002/delete-confirm returns the
    countdown swap body (cust-002 has no incoming refs). NOT a
    full-banner aside."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        resp = c.get("/l2_shape/account/cust-002/delete-confirm")
    assert resp.status_code == 200
    assert 'data-delete-state="countdown"' in resp.text
    # Cancel sibling link.
    assert 'data-role="card-delete-cancel"' in resp.text
    # NO page-level banner shape.
    assert 'data-test-delete-banner' not in resp.text
    assert "#delete-confirm-banner-slot" not in resp.text


def test_get_delete_confirm_returns_refused_when_stale_tab_referenced(
    writable_l2_yaml: Path,
) -> None:
    """Defense-in-depth: if the operator's tab is stale and the
    entity gained references since the render, the confirm endpoint
    returns the REFUSED inner so a click that bypassed the
    render-time check still surfaces the blocker.

    Picks ``ReconciliationLeg`` — referenced by
    ``ExternalReconciliationCycle.leg_rails`` in the fixture."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        resp = c.get("/l2_shape/rail/ReconciliationLeg/delete-confirm")
    assert resp.status_code == 200
    assert 'data-delete-state="refused"' in resp.text
    assert "ExternalReconciliationCycle" in resp.text
    # No countdown / confirm anchor leaked.
    assert 'data-delete-state="countdown"' not in resp.text


def test_get_delete_cancel_returns_active_button(
    writable_l2_yaml: Path,
) -> None:
    """GET /l2_shape/account/cust-002/delete-cancel returns the
    active-state Delete anchor (no wrapper — wrapper-innerHTML swap
    preserves the existing wrapper). The hx-target attribute carries
    "closest [data-delete-wrapper]" so the literal string still
    appears in the response; assert against the opening tag instead."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        resp = c.get("/l2_shape/account/cust-002/delete-cancel")
    assert resp.status_code == 200
    assert 'data-delete-state="active"' in resp.text
    assert "<span data-delete-wrapper" not in resp.text


def test_delete_during_countdown_rejected_with_too_early(
    writable_l2_yaml: Path,
) -> None:
    """Server wall-clock check: a token whose start_ts is fresher
    than COUNTDOWN_SECS gets a 400 ``too-early``."""
    token = make_confirm_token("account", "cust-002", time.time())
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        resp = c.delete(
            f"/l2_shape/account/cust-002?confirm_token={token}",
        )
    assert resp.status_code == 400, resp.text
    assert "too-early" in resp.text
    reloaded = load_instance(writable_l2_yaml)
    assert any(str(a.id) == "cust-002" for a in reloaded.accounts)


def test_delete_without_token_rejected_with_missing_token(
    writable_l2_yaml: Path,
) -> None:
    """A direct DELETE that skips the confirm flow entirely gets a
    400 ``missing-token`` — never a silent delete."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        resp = c.delete("/l2_shape/account/cust-002")
    assert resp.status_code == 400, resp.text
    assert "missing-token" in resp.text


def test_delete_after_countdown_with_monkeypatched_zero_secs_succeeds(
    writable_l2_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: pin COUNTDOWN_SECS=0, mint a token, fire DELETE
    — the entity disappears from disk."""
    monkeypatch.setattr(
        "recon_gen.common.html._delete_confirm.COUNTDOWN_SECS", 0.0,
    )
    token = make_confirm_token(
        cast("EntityKind", "account"), "cust-002", time.time(),
    )
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
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


def test_card_delete_button_targets_wrapper_swap(
    writable_l2_yaml: Path,
) -> None:
    """Card Delete button: hx-get pointed at /delete-confirm,
    hx-target at ``closest [data-delete-wrapper]`` (NOT the old
    page-level slot). Wrapper present + active state for the
    unreferenced cust-001 card."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        body = c.get("/l2_shape/account/cust-001").text
    assert 'hx-get="/l2_shape/account/cust-001/delete-confirm"' in body
    assert 'hx-target="closest [data-delete-wrapper]"' in body
    assert 'data-role="card-delete"' in body
    assert "data-delete-wrapper" in body
    assert "hx-confirm=" not in body
    # The OLD page-level slot shape is gone.
    assert "#delete-confirm-banner-slot" not in body
    assert 'id="delete-confirm-banner-slot"' not in body


def test_card_delete_refused_when_referenced_renders_refused_state(
    writable_l2_yaml: Path,
) -> None:
    """A card whose entity has incoming refs renders Delete in the
    refused state at render time — operator sees the disabled
    button BEFORE clicking, with the tooltip reason in
    ``title``/``data-delete-reason``.

    Uses ReconciliationLeg (referenced by
    ExternalReconciliationCycle.leg_rails)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        body = c.get("/l2_shape/rail/ReconciliationLeg").text
    assert 'data-delete-state="refused"' in body
    assert 'aria-disabled="true"' in body
    assert "ExternalReconciliationCycle" in body
    assert 'data-delete-reason="Referenced by' in body


def test_list_page_has_no_banner_slot(writable_l2_yaml: Path) -> None:
    """BX.1 redesign — the page-level
    ``#delete-confirm-banner-slot`` is GONE. Each card carries its
    own in-place wrapper."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        body = c.get("/l2_shape/account/").text
    assert 'id="delete-confirm-banner-slot"' not in body
    assert 'data-test-delete-banner-slot' not in body
    # But each card DOES have a wrapper.
    assert "data-delete-wrapper" in body


def test_edit_page_has_no_banner_slot(writable_l2_yaml: Path) -> None:
    """Edit page — same redesign, no slot at the top. Form Delete
    is in-place in the action row."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        body = c.get("/l2_shape/account/cust-001/edit").text
    assert 'id="delete-confirm-banner-slot"' not in body
    assert 'data-test-delete-banner-slot' not in body
    # Form Delete present + wrapped.
    assert 'data-role="form-delete"' in body
    assert "data-delete-wrapper" in body
    assert (
        'hx-get="/l2_shape/account/cust-001/delete-confirm?from=edit"'
        in body
    )


# ---------------------------------------------------------------------------
# onclick guard parity — Delete inside <summary> must not toggle <details>
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
    """Cards render in collapsed form (``<details>``-wrapped); the
    Delete anchor MUST call both ``preventDefault()`` AND
    ``stopPropagation()`` so clicking it doesn't toggle the parent
    ``<details>`` open as a confusing side-effect."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        body = c.get(list_path).text
    matches = re.findall(
        r'<a\b[^>]*\bdata-role="card-delete"[^>]*>',
        body,
    )
    assert matches, (
        f"kind={kind!r} list page rendered no card-delete anchor.\n"
        f"List page body (first 2KB):\n{body[:2048]}"
    )
    for anchor_open in matches:
        assert "event.preventDefault()" in anchor_open, anchor_open
        assert "event.stopPropagation()" in anchor_open, anchor_open


def test_edit_page_delete_button_carries_onclick_guard(
    writable_l2_yaml: Path,
) -> None:
    """Edit page form-delete anchor — same guard for parity with
    the card path."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps
        body = c.get("/l2_shape/account/cust-001/edit").text
    m = re.search(
        r'<a\b[^>]*\bdata-role="form-delete"[^>]*>',
        body,
    )
    assert m is not None, (
        "edit page rendered no form-delete anchor."
        f"\nEdit page body (first 2KB):\n{body[:2048]}"
    )
    anchor_open = m.group(0)
    assert "event.preventDefault()" in anchor_open, anchor_open
    assert "event.stopPropagation()" in anchor_open, anchor_open


def test_card_action_buttons_helper_renders_state_aware_delete() -> None:
    """``_render_card_action_buttons`` consolidates the Edit + Delete
    pair and renders Delete in active OR refused state based on the
    ``refs`` argument."""
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_card_action_buttons,
    )
    # Empty refs → active.
    active_html = _render_card_action_buttons(
        "rail", "test-rail-id", refs=(),
    )
    assert ">Edit<" in active_html
    assert ">Delete<" in active_html
    assert 'data-delete-state="active"' in active_html
    assert "data-delete-wrapper" in active_html
    assert 'hx-get="/l2_shape/rail/test-rail-id/delete-confirm"' in active_html
    assert 'hx-target="closest [data-delete-wrapper]"' in active_html

    # Non-empty refs → refused.
    refs = (
        EntityRef(
            referrer_kind="transfer_template",
            referrer_id="TT_x",
            via_field="leg_rails",
            label="TransferTemplate TT_x (leg_rails)",
        ),
    )
    refused_html = _render_card_action_buttons(
        "rail", "test-rail-id", refs=refs,
    )
    assert 'data-delete-state="refused"' in refused_html
    assert 'aria-disabled="true"' in refused_html
    assert 'data-delete-reason="Referenced by' in refused_html


# Silence unused-import warning for COUNTDOWN_SECS — it documents
# the production default the tests pin against via monkeypatch.
_ = COUNTDOWN_SECS
_ = _delete_confirm
_ = ReferenceGraph
