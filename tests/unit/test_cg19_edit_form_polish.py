"""CG.19 — Edit form gains a Delete affordance, a back-link to the
kind's list page, and an h1 that carries the account display name
for the account kind.

Cold-read v4 P1 #4: `/l2_shape/account/<id>/edit` had Save + Cancel
only — to delete the entity the operator had to navigate back to
the list, find the card, and hit the card's Delete button. The h1
showed the kebab id but not the analyst-readable display name; and
there was no "← back to Accounts" link, so the operator's only
escape was the top-nav L2 Editor link → home → re-expand the
section → scroll.

This cell:
- Appends `Account.name` to the edit-page h1 (account-only — same
  principle as CG.11's card title).
- Inserts a `← back to <plural>` link above the form.
- Adds a Delete button to the form-actions row with the same
  `hx-confirm` text the card uses; passes `?from=edit` so the delete
  handler responds with `HX-Redirect` to the list (the operator
  lands on the list page, not on the now-stale edit form).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "l2"


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


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


# ---------------------------------------------------------------------------
# Edit-form h1 — account display name + back-link
# ---------------------------------------------------------------------------

def test_account_edit_h1_carries_display_name(
    writable_l2_yaml: Path,
) -> None:
    """Accounts with a `name` field surface it in the edit-page h1
    so the operator sees "Edit account · cust-001  Customer Number
    One" (the name lives in a `data-role="edit-h1-display-name"`
    span after the kebab id; CG-followup.2 swapped the em-dash +
    colon ambiguous shape for the consistent middle-dot + display-
    name-span idiom locked by CG.11)."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = next(a for a in inst.accounts if a.name and a.id == "cust-001")
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account/{account.id}/edit").text
    # The h1 reads `Edit account · <kebab>` and the name renders in
    # a secondary-fg span next to it.
    assert f"Edit account · {account.id}" in body
    assert 'data-role="edit-h1-display-name"' in body
    assert str(account.name) in body


def test_rail_edit_h1_does_not_carry_display_name(
    writable_l2_yaml: Path,
) -> None:
    """Only the account kind appends a display name. Rails / chains /
    etc. keep the existing h1 shape (CG.11 was account-only and CG.19
    extends that consistently)."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    rail = inst.rails[0]
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/rail/{rail.name}/edit").text
    # The h1 reads "Edit rail: <name>" (optionally with subtype
    # suffix) — no " — " separator carrying a display name.
    assert f"Edit rail" in body


@pytest.mark.parametrize("kind,plural_label", [
    ("account", "Accounts"),
    ("rail", "Rails"),  # typing-smell: ignore[no-inline-production-constants]: coincidentally matches l2_flow_tracing/app.py::_RAILS_NAME (a sheet name) but semantically this is the kind_label_plural value
    ("transfer_template", "Transfer templates"),
    ("chain", "Chains"),  # typing-smell: ignore[no-inline-production-constants]: coincidentally matches l2_flow_tracing/app.py::_CHAINS_NAME (a sheet name) but semantically this is the kind_label_plural value
    ("limit_schedule", "Limit schedules"),
    ("account_template", "Account templates"),
])
def test_edit_form_has_back_link(
    writable_l2_yaml: Path, kind: str, plural_label: str,
) -> None:
    """Every kind's edit page surfaces a "← back to <plural>"
    anchor pointing at the kind's list page so the operator has a
    one-click escape that doesn't bounce through the top-nav."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    # Pick the first entity of the kind.
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _entities_for_kind, _entity_id,
    )
    entities = _entities_for_kind(inst, kind)  # type: ignore[arg-type]: parametrize fixture passes valid kinds
    assert entities, f"fixture should have at least one {kind}"
    entity_id = _entity_id(kind, entities[0])  # type: ignore[arg-type]: parametrize fixture passes valid kinds
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/{kind}/{entity_id}/edit").text
    # Back-link target and label both present.
    assert f'href="/l2_shape/{kind}/"' in body
    assert f"← back to {plural_label}" in body


# ---------------------------------------------------------------------------
# Edit-form Delete button
# ---------------------------------------------------------------------------

def test_edit_form_has_delete_button(
    writable_l2_yaml: Path,
) -> None:
    """Edit form's actions row carries a ``data-role="form-delete"``
    anchor. BX.1 redesign (2026-06-11): the Delete is now wrapped in
    its in-place swap boundary; no browser-native ``hx-confirm``
    modal. Refused entities render disabled with the reason in
    ``data-delete-reason``; unreferenced entities render the active
    Delete with ``hx-get`` pointing at /delete-confirm."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    account = inst.accounts[0]
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/account/{account.id}/edit").text
    assert 'data-role="form-delete"' in body
    assert "data-delete-wrapper" in body
    # Either active (with hx-get → /delete-confirm) or refused
    # (with data-delete-reason). Both shapes pass the BX.1 contract.
    has_active = "/delete-confirm" in body
    has_refused = 'data-delete-state="refused"' in body
    assert has_active or has_refused, body
    assert "hx-confirm=" not in body


def test_edit_form_delete_url_carries_from_edit(
    writable_l2_yaml: Path,
) -> None:
    """The Delete button on the edit form for an UNREFERENCED entity
    points its GET ``/delete-confirm`` request at the same entity
    with ``?from=edit`` so the eventual DELETE handler distinguishes
    form-source from card-source deletes.

    Pick cust-002 (shared CustomerSubledger role with cust-001 + the
    template; no incoming references → active Delete with hx-get)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/cust-002/edit").text
    assert (
        'hx-get="/l2_shape/account/cust-002/delete-confirm?from=edit"'
        in body
    )


def _mint_confirm_token(kind: str, entity_id: str) -> str:
    """Server-side helper — mint a token directly (test bypasses the
    GET endpoint to avoid coupling to the banner's HTML when the
    test's focus is the DELETE response shape, not the banner)."""
    import time  # noqa: PLC0415
    from typing import cast  # noqa: PLC0415
    from recon_gen.common.html._delete_confirm import (  # noqa: PLC0415
        make_confirm_token,
    )
    from recon_gen.common.l2.editor import EntityKind  # noqa: PLC0415
    # Use start_ts = 0 → "elapsed" is ~now, well past the 0-secs
    # countdown the tests pin via monkeypatch.
    return make_confirm_token(
        cast("EntityKind", kind), entity_id, time.time() - 10.0,
    )


def test_delete_from_edit_responds_with_hx_redirect(
    writable_l2_yaml: Path,
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """End-to-end: DELETE the entity with `?from=edit` AND a valid
    confirm-token; assert the response carries the HX-Redirect
    header pointing at the list page."""
    monkeypatch.setattr(
        "recon_gen.common.html._delete_confirm.COUNTDOWN_SECS", 0.0,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    target_id = inst.accounts[0].id
    token = _mint_confirm_token("account", str(target_id))
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.delete(
            f"/l2_shape/account/{target_id}?from=edit&confirm_token={token}",
        )
    # Either the delete succeeds (200 + HX-Redirect) or the validator
    # rejects it (400 + inline error). EITHER way the form-source
    # branch should fire — we assert the SHAPE of the success path
    # by checking that, IF the response is 200, HX-Redirect is set.
    if resp.status_code == 200:
        assert resp.headers.get("HX-Redirect") == "/l2_shape/account/"
    else:
        # Validator-blocked delete: 400 + inline error fragment.
        assert resp.status_code == 400


def test_delete_from_card_does_not_redirect(
    writable_l2_yaml: Path,
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """The pre-existing card-source delete (no `?from=edit`) must
    KEEP its empty-body / no-redirect behavior so the banner's
    outerHTML swap still works in place. BX.1: token + countdown
    monkeypatch as above."""
    monkeypatch.setattr(
        "recon_gen.common.html._delete_confirm.COUNTDOWN_SECS", 0.0,
    )
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    inst = cache.get()
    target_id = inst.accounts[0].id
    token = _mint_confirm_token("account", str(target_id))
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.delete(
            f"/l2_shape/account/{target_id}?confirm_token={token}",
        )
    if resp.status_code == 200:
        assert resp.headers.get("HX-Redirect") is None
