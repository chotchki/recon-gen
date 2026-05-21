"""Studio editor route integration tests (X.4.e + X.4.f).

Drives the full HTMX cascade flow via Starlette's TestClient against
a tempfile-backed L2InstanceCache. The flow exercised:

1. GET /l2_shape/account/ — list page renders all accounts.
2. GET /l2_shape/account/<id> — read card returns the entity row.
3. GET /l2_shape/account/<id>/edit — editable form fragment.
4. PUT /l2_shape/account/<id> — save → mutate + validate + persist;
   response carries the read fragment + HX-Trigger: l2-cascade-reload.
5. PUT with invalid value → 400 + form fragment with the error inline.
6. DELETE /l2_shape/account/<id> with a dependent → 400 (validator
   structural-break).

Cache + disk persistence verified via a fresh ``load_instance`` after
the PUT lands.
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
from recon_gen.common.l2.loader import load_instance
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml to a tempfile so PUT/DELETE writes don't
    mutate the bundled fixture."""
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(yaml_path: Path) -> object:
    """Studio app with editor routes mounted, no DB pool needed."""
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
# Read-side: list / card / edit form (X.4.e.2/3)
# ---------------------------------------------------------------------------


def test_list_view_renders_every_account(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any: ASGI app — TestClient stubs accept it
        resp = c.get("/l2_shape/account/")
        assert resp.status_code == 200
        body = resp.text
        # spec_example has 7 accounts; their ids should all appear.
        for acct_id in (
            "clearing-suspense", "north-pool", "south-pool",
            "customer-ledger", "external-counterparty-one",
            "cust-001", "cust-002",
        ):
            assert acct_id in body


def test_read_card_returns_fragment(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/cust-001")
        assert resp.status_code == 200
        # Read card renders the dl with the per-field rows.
        assert "<dl>" in resp.text
        assert "Customer Number One" in resp.text


def test_edit_form_is_full_page_with_post_form(writable_l2_yaml: Path) -> None:
    """AI.2.e — GET /edit is a dedicated full-page screen (symmetric with the
    create page), not an inline hx-swap fragment. The form is a plain POST
    back to the entity's save route."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/cust-001/edit")
        assert resp.status_code == 200
        body = resp.text
        # Full page (doctype + chrome), not a bare fragment.
        assert "<!doctype html>" in body
        assert "← back to Studio" in body
        # Plain POST form targeting the entity's save route.
        assert 'method="post"' in body
        assert 'action="/l2_shape/account/cust-001"' in body


def test_unknown_kind_returns_404(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/not-a-kind/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Write-side: PUT save (X.4.e.4)
# ---------------------------------------------------------------------------


def test_put_account_persists_to_disk_and_redirects_home(
    writable_l2_yaml: Path,
) -> None:
    """AI.2.e save flow: POST/PUT → mutate → validate → cache.save (atomic
    write) → 303-redirect home (symmetric with the create POST). The mutation
    persists to the --l2 file on disk.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Edit cust-001's name + description.
        resp = c.put(
            "/l2_shape/account/cust-001",
            data={
                "id": "cust-001",  # unchanged
                "scope": "internal",
                "name": "Customer One Edited",
                "role": "CustomerSubledger",
                "parent_role": "CustomerLedger",
                "description": "Edited via Studio.",
            },
            follow_redirects=False,
        )
        # Dedicated-screen flow: 303 back to home, not an inline fragment.
        assert resp.status_code == 303, resp.text
        assert resp.headers.get("location") == "/"

    # Disk persistence — re-load and confirm.
    reloaded = load_instance(writable_l2_yaml)
    cust = next(a for a in reloaded.accounts if str(a.id) == "cust-001")
    assert cust.name == "Customer One Edited"
    assert cust.description == "Edited via Studio."


def test_put_invalid_value_returns_400_with_inline_error(
    writable_l2_yaml: Path,
) -> None:
    """Validation-failure path (X.4.e.5): bad PUT returns 400 + the
    edit form fragment with the validator error rendered inline.
    User's typed-but-invalid content is preserved so they can fix it.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Validator R2: Account.parent_role MUST resolve to some
        # Account.role. Setting it to a non-existent role triggers
        # the validator's reference-resolution check.
        resp = c.put(
            "/l2_shape/account/cust-001",
            data={
                "id": "cust-001",
                "scope": "internal",
                "name": "Customer Number One",
                "role": "CustomerSubledger",
                "parent_role": "DanglingParentRole",
            },
        )
        assert resp.status_code == 400, resp.text
        # Form is re-rendered (the user's typed value is preserved
        # so they can fix it).
        assert "<form" in resp.text
        assert 'value="DanglingParentRole"' in resp.text
        # The validator's error is surfaced in the global-error block.
        assert "form-global-error" in resp.text

    # Disk untouched — the validation rejection happened before save.
    reloaded = load_instance(writable_l2_yaml)
    cust = next(a for a in reloaded.accounts if str(a.id) == "cust-001")
    assert str(cust.parent_role) == "CustomerLedger"  # unchanged


# ---------------------------------------------------------------------------
# DELETE (X.4.e.6 + structural-break rejection)
# ---------------------------------------------------------------------------


def test_delete_dependent_rail_returns_400(writable_l2_yaml: Path) -> None:
    """SPEC's structural-break rule: deleting a Rail that a
    TransferTemplate.leg_rails still references returns 400 with the
    validator's error inline; the disk file is untouched."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # ReconciliationLeg is in ExternalReconciliationCycle.leg_rails.
        resp = c.delete("/l2_shape/rail/ReconciliationLeg")
        assert resp.status_code == 400, resp.text
        assert "form-global-error" in resp.text

    # Disk untouched.
    reloaded = load_instance(writable_l2_yaml)
    assert any(
        str(r.name) == "ReconciliationLeg" for r in reloaded.rails
    )


def test_parent_role_renders_as_role_dropdown_for_child(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f role dropdown — Account.parent_role is a <select> populated
    from the union of Account.role + AccountTemplate.role values, with
    an empty option for clearing. The current value renders selected;
    typing a free-form invalid role is no longer possible (the input
    type is select, not text). Only renders when the account is NOT
    already a parent (two-layer rule — see the parent test below)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/cust-001/edit")
        assert resp.status_code == 200, resp.text
        body = resp.text

    # cust-001 is a child (parent_role=CustomerLedger), not a parent —
    # the dropdown renders.
    assert 'name="parent_role"' in body
    sel_start = body.index('<select id="field-parent_role"')
    sel_end = body.index("</select>", sel_start) + len("</select>")
    block = body[sel_start:sel_end]
    assert "— none —" in block
    # CustomerLedger is the existing parent_role on cust-001 — selected.
    assert 'value="CustomerLedger" selected' in block
    # CustomerSubledger is another role in the instance — must appear
    # as an option even if not currently selected.
    assert 'value="CustomerSubledger"' in block


def test_parent_role_hidden_when_account_is_already_a_parent(
    writable_l2_yaml: Path,
) -> None:
    """Two-layer rule (X.4.f) — if an Account.role is referenced as some
    other Account.parent_role (or AccountTemplate.parent_role), THIS
    account is already a parent and giving it its own parent_role
    would create a 3-layer hierarchy. Hide the field entirely (both
    in the edit form and the read card) — there's no "clear" or
    "select" UI to confuse the operator."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # customer-ledger is referenced by cust-001 / cust-002 as
        # parent_role=CustomerLedger.
        edit_resp = c.get("/l2_shape/account/customer-ledger/edit")
        assert edit_resp.status_code == 200
        # No parent_role input rendered at all on the edit form.
        assert 'name="parent_role"' not in edit_resp.text

        # Same omission on the read card — the row would otherwise show
        # "—" and confuse the operator about whether they should pick one.
        card_resp = c.get("/l2_shape/account/customer-ledger")
        assert card_resp.status_code == 200
        assert "Parent role" not in card_resp.text


def test_card_renders_delete_link_with_confirm(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.9 — every read card carries a Delete link wired to the
    DELETE route, with hx-confirm so a stray click can't wipe an
    entity."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/account/cust-001").text
    assert 'class="delete-link"' in body
    assert 'hx-delete="/l2_shape/account/cust-001"' in body
    assert "hx-confirm=" in body


def test_get_new_form_returns_full_page_with_intro_prose(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.9.create-page — GET /l2_shape/<kind>/new returns a full
    HTML page (chrome + back link + per-kind intro prose explaining
    what this entity is) wrapping the form. The form posts plain
    HTML (not htmx) so the browser navigates after submit."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/new")
    assert resp.status_code == 200
    body = resp.text
    # Full HTML page chrome.
    assert "<!doctype" in body.lower()
    assert "<html" in body
    assert 'class="studio-header"' in body
    # Back nav to home.
    assert 'href="/"' in body
    # Per-kind intro prose explaining what an Account is.
    assert "An Account" in body
    assert "chart of accounts" in body
    # Form is plain HTML POST (no hx-post, no hx-target).
    assert 'method="post"' in body
    assert 'action="/l2_shape/account/"' in body
    assert "hx-post" not in body
    # No prefilled values on a blank form.
    assert 'value=""' in body


def test_post_create_account_redirects_to_home_on_success(
    writable_l2_yaml: Path,
) -> None:
    """Successful create returns 303 → /; the operator's browser
    navigates back to home where the new entity appears in its
    section. (TestClient default doesn't follow redirects.)"""
    app = _build_app(writable_l2_yaml)
    with TestClient(app, follow_redirects=False) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(
            "/l2_shape/account/",
            data={
                "id": "cust-999-new",
                "scope": "internal",
                "name": "Brand new customer",
                "role": "CustomerSubledger",
                "parent_role": "CustomerLedger",
            },
        )
    assert resp.status_code == 303, resp.text
    assert resp.headers.get("location") == "/"

    reloaded = load_instance(writable_l2_yaml)
    assert any(str(a.id) == "cust-999-new" for a in reloaded.accounts)


def test_post_create_with_duplicate_id_returns_400_full_page(
    writable_l2_yaml: Path,
) -> None:
    """ID collision → 400 + the full create page re-rendered with the
    error inline + the operator's typed values preserved (so they
    can fix the id without losing input)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(
            "/l2_shape/account/",
            data={
                "id": "cust-001",  # already exists
                "scope": "internal",
                "name": "Conflicting",
                "role": "CustomerSubledger",
            },
        )
    assert resp.status_code == 400, resp.text
    body = resp.text
    assert "already exists" in body
    # Full page re-rendered (chrome + intro stays).
    assert 'class="studio-header"' in body
    assert "An Account" in body
    # User's typed name preserved so they can fix just the id.
    assert 'value="Conflicting"' in body


def test_put_account_role_rename_cascades_to_rails_and_templates(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.7.cascade — renaming Account.role rewrites every reference
    (Rail.source/destination/leg_role, AccountTemplate.role +
    parent_role, LimitSchedule.parent_role) so the post-PUT model
    validates without dangling-role errors. Without the cascade the
    validator rejects with "roles ['CustomerSubledger'] are not
    declared on any Account or AccountTemplate"."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # cust-001 plays role=CustomerSubledger; that role is also
        # held by cust-002 + an AccountTemplate, and is the
        # source/destination/leg_role on three Rails. Renaming it
        # via the editor should cascade everywhere.
        resp = c.put(
            "/l2_shape/account/cust-001",
            data={
                "id": "cust-001",
                "scope": "internal",
                "name": "Customer Number One",
                "role": "CustomerSubledgerV2",  # the rename
                "parent_role": "CustomerLedger",
            },
            follow_redirects=False,
        )
        # AI.2.e — save 303-redirects home; the cascade (below) still ran.
        assert resp.status_code == 303, resp.text
        assert resp.headers.get("location") == "/"

    reloaded = load_instance(writable_l2_yaml)
    # Both Accounts that played the role get the new value (rename is
    # role-scoped, not account-scoped — multiple instances of one role
    # all carry the renamed value).
    cust1 = next(a for a in reloaded.accounts if str(a.id) == "cust-001")
    cust2 = next(a for a in reloaded.accounts if str(a.id) == "cust-002")
    assert str(cust1.role) == "CustomerSubledgerV2"
    assert str(cust2.role) == "CustomerSubledgerV2"
    # AccountTemplate.role rewritten too.
    tpl = next(
        t for t in reloaded.account_templates
        if str(t.role) == "CustomerSubledgerV2"
    )
    assert tpl is not None
    # No template still references the old role.
    assert not any(
        str(t.role) == "CustomerSubledger"
        for t in reloaded.account_templates
    )
    # Rail role references rewritten — pick one of each shape.
    # source_role/destination_role/leg_role are RoleExpression
    # (tuple[Identifier, ...]); flatten for the comparison.
    rails_by_name = {str(r.name): r for r in reloaded.rails}
    inbound = rails_by_name["ExternalRailInbound"]
    outbound = rails_by_name["ExternalRailOutbound"]
    charge = rails_by_name["SubledgerCharge"]
    assert [str(x) for x in getattr(inbound, "destination_role")] == [
        "CustomerSubledgerV2",
    ]
    assert [str(x) for x in getattr(outbound, "source_role")] == [
        "CustomerSubledgerV2",
    ]
    assert [str(x) for x in getattr(charge, "leg_role")] == [
        "CustomerSubledgerV2",
    ]
    # The unrelated CustomerLedger / NorthPool / SouthPool roles
    # untouched (cascade is precise).
    customer_ledger = next(
        a for a in reloaded.accounts if str(a.id) == "customer-ledger"
    )
    assert str(customer_ledger.role) == "CustomerLedger"


def test_put_rail_name_rename_cascades_to_templates_and_chains(
    writable_l2_yaml: Path,
) -> None:
    """Rail.name rename cascades to TransferTemplate.leg_rails,
    Rail.bundles_activity, Chain.parent / Chain.children entries."""
    app = _build_app(writable_l2_yaml)
    # Find a rail that's referenced by some chain or template.
    pre = load_instance(writable_l2_yaml)
    referenced_rail_name: str | None = None
    for ce in pre.chains:
        candidates = [ce.parent, *ce.children]
        for cand in candidates:
            if any(str(r.name) == str(cand) for r in pre.rails):
                referenced_rail_name = str(cand)
                break
        if referenced_rail_name:
            break
    assert referenced_rail_name is not None, (
        "spec_example should have at least one chain referencing a rail"
    )

    new_name = f"{referenced_rail_name}_RENAMED"
    # Pull the existing rail's other fields to round-trip cleanly.
    pre_rail = next(r for r in pre.rails if str(r.name) == referenced_rail_name)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(
            f"/l2_shape/rail/{referenced_rail_name}",
            data={
                "name": new_name,
            },
            follow_redirects=False,
        )
        # AI.2.e — save 303-redirects home; the cascade (below) still ran.
        assert resp.status_code == 303, resp.text
        assert resp.headers.get("location") == "/"

    reloaded = load_instance(writable_l2_yaml)
    assert any(str(r.name) == new_name for r in reloaded.rails)
    assert not any(
        str(r.name) == referenced_rail_name for r in reloaded.rails
    )
    # No chain still points at the old name.
    for ce in reloaded.chains:
        assert str(ce.parent) != referenced_rail_name
        for child in ce.children:
            assert str(child) != referenced_rail_name


def test_put_account_id_rename_does_not_cascade(
    writable_l2_yaml: Path,
) -> None:
    """Account.id is addressing-only — nothing in the L2 model
    references an Account by id. So a PUT that changes id should
    succeed without invoking the role-cascade walker. (The id rename
    itself is mutate_l2's job; rename_identifier is a no-op on id.)
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # external-counterparty-one's role is referenced by rails; if
        # the cascade fired on id, those role refs would be broken or
        # misrewritten. We're checking they STAY put.
        resp = c.put(
            "/l2_shape/account/external-counterparty-one",
            data={
                "id": "external-counterparty-renamed",
                "scope": "external",
                "name": "External Counterparty One",
                "role": "ExternalCounterparty",
            },
        )
        assert resp.status_code == 200, resp.text

    reloaded = load_instance(writable_l2_yaml)
    # The Account moved.
    assert any(
        str(a.id) == "external-counterparty-renamed"
        for a in reloaded.accounts
    )
    assert not any(
        str(a.id) == "external-counterparty-one" for a in reloaded.accounts
    )
    # The role is unchanged → rails still resolve.
    rails_by_name = {str(r.name): r for r in reloaded.rails}
    inbound = rails_by_name["ExternalRailInbound"]
    assert [str(x) for x in getattr(inbound, "source_role")] == [
        "ExternalCounterparty",
    ]


def test_transfer_template_edit_form_renders_leg_rails_checkbox_group(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.10 — TransferTemplate edit form exposes leg_rails as a
    checkbox group (one <input type=checkbox> per available rail),
    not a <select multiple> — easier to use than Cmd/Ctrl-click."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    if not pre.transfer_templates:
        return  # spec_example has at least one
    tmpl = pre.transfer_templates[0]
    tmpl_name = str(tmpl.name)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/transfer_template/{tmpl_name}/edit")
    assert resp.status_code == 200
    body = resp.text
    # Checkbox-group container present.
    assert 'class="multi-select-group"' in body
    # NO <select multiple> for leg_rails (the old shape we replaced).
    assert 'name="leg_rails" multiple' not in body
    # Each currently-attached leg_rail is checked.
    for rn in tmpl.leg_rails:
        assert (
            f'value="{escape_html(str(rn))}" checked' in body
        )
    # Hidden marker so the save handler can distinguish "field rendered
    # with empty selection" from "field absent".
    assert 'name="leg_rails__present"' in body


def test_put_transfer_template_updates_leg_rails(
    writable_l2_yaml: Path,
) -> None:
    """PUT with a leg_rails selection routes through the multi_select
    coerce path and writes a tuple of rail names back. We round-trip
    the existing leg_rails selection (rather than picking a random
    new one) so the test asserts the wire shape without tripping the
    unrelated metadata_keys / TransferKey validator rules — the
    invariant "leg_rails arrives intact through PUT" is the part
    that matters here."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    if not pre.transfer_templates:
        return
    tmpl = pre.transfer_templates[0]
    tmpl_name = str(tmpl.name)
    current_leg_rails = [str(rn) for rn in tmpl.leg_rails]
    # httpx encodes a dict-with-list value as repeated form keys,
    # which is what the browser submits for <select multiple>.
    data = {
        "name": tmpl_name,
        "expected_net": str(tmpl.expected_net),
        "completion": str(tmpl.completion),
        "leg_rails__present": "1",
        "leg_rails": current_leg_rails,
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/transfer_template/{tmpl_name}", data=data)
    assert resp.status_code == 200, resp.text

    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        t for t in reloaded.transfer_templates if str(t.name) == tmpl_name
    )
    assert [str(r) for r in saved.leg_rails] == current_leg_rails


def test_put_transfer_template_round_trips_transfer_key(
    writable_l2_yaml: Path,
) -> None:
    """AI.2.b — adding the transfer_key FieldSpec means edit-PUT now flows
    transfer_key through the form (previously it was preserved untouched
    because no field existed at all). Round-trip the existing value — same
    shape as the leg_rails test — to assert the new textarea coerce delivers
    it back intact rather than dropping or mangling it. (R12 holds because
    the value already satisfied it on load.)"""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    tmpl = next(
        (t for t in pre.transfer_templates if t.transfer_key), None,
    )
    if tmpl is None:
        return
    tmpl_name = str(tmpl.name)
    original_key = [str(k) for k in tmpl.transfer_key]
    data = {
        "name": tmpl_name,
        "expected_net": str(tmpl.expected_net),
        "completion": str(tmpl.completion),
        "leg_rails__present": "1",
        "leg_rails": [str(rn) for rn in tmpl.leg_rails],
        "transfer_key": ", ".join(original_key),
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/transfer_template/{tmpl_name}", data=data)
    assert resp.status_code == 200, resp.text

    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        t for t in reloaded.transfer_templates if str(t.name) == tmpl_name
    )
    assert [str(k) for k in saved.transfer_key] == original_key


def test_put_transfer_template_with_empty_leg_rails_returns_400(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.10 — empty leg_rails would leave a TransferTemplate with
    no member rails (validator rejects with R-something). The save
    handler returns 400 + the form re-rendered."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    if not pre.transfer_templates:
        return
    tmpl = pre.transfer_templates[0]
    tmpl_name = str(tmpl.name)
    # Marker present, no leg_rails key at all → browser sends nothing
    # for an empty <select multiple>, but the hidden marker tells the
    # save handler this is an intentional clear (vs "field absent").
    data = {
        "name": tmpl_name,
        "expected_net": str(tmpl.expected_net),
        "completion": str(tmpl.completion),
        "leg_rails__present": "1",
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/transfer_template/{tmpl_name}", data=data)
    assert resp.status_code == 400, resp.text
    # Form re-rendered with the validator's error inline.
    assert "form-global-error" in resp.text
    assert "<form" in resp.text

    # Disk unchanged — original leg_rails still there.
    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        t for t in reloaded.transfer_templates if str(t.name) == tmpl_name
    )
    assert saved.leg_rails == tmpl.leg_rails


# ---------------------------------------------------------------------------
# AB.3.7 — TransferTemplate.leg_rail_xor_groups UI (list of multi_selects,
# each sourcing the template's own leg_rails)
# ---------------------------------------------------------------------------


def test_xor_groups_edit_form_renders_existing_groups_plus_blank_row(
    writable_l2_yaml: Path,
) -> None:
    """AB.3.7 — TransferTemplate edit form exposes ``leg_rail_xor_groups``
    as a stack of fieldset checkbox groups. Each existing group is one
    row pre-checked with its members; a trailing always-empty "Add
    new XOR group" row lets the operator author a new group without
    JS. spec_example's ``SettlementTimingCycle`` carries one group
    [Auto, Standard]; the trailing slot is the second fieldset."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    tmpl = next(
        (t for t in pre.transfer_templates if t.leg_rail_xor_groups),
        None,
    )
    if tmpl is None:
        return  # AB.3.5.spec wires this; defensive skip
    tmpl_name = str(tmpl.name)
    group0 = tmpl.leg_rail_xor_groups[0]
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/transfer_template/{tmpl_name}/edit")
    assert resp.status_code == 200
    body = resp.text
    # Field label + container present.
    assert "Variable rail XOR groups" in body
    assert 'class="multi-select-groups"' in body
    # First group's members are checked under name="leg_rail_xor_groups_0".
    for member in group0:
        assert (
            f'name="leg_rail_xor_groups_0" value="{escape_html(str(member))}"'
            f' checked'
        ) in body
    # Trailing blank row is rendered (legend = "Add new XOR group").
    assert "Add new XOR group" in body
    # Hidden control inputs.
    assert 'name="leg_rail_xor_groups__present"' in body
    assert 'name="leg_rail_xor_groups__num_groups"' in body


def test_xor_groups_create_form_omits_field(writable_l2_yaml: Path) -> None:
    """AB.3.7 — the create page filters ``edit_only=True`` fields. The
    operator authors ``leg_rails`` first, saves, then edits to add
    XOR groups. Two-step UX is acceptable; the field references
    sibling state that doesn't exist yet on create."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/transfer_template/new")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # ``leg_rails`` checkbox group present, ``leg_rail_xor_groups``
    # field-row absent.
    assert 'name="leg_rails"' in body
    assert "leg_rail_xor_groups" not in body
    assert "Variable rail XOR groups" not in body


def test_put_transfer_template_round_trips_existing_xor_groups(
    writable_l2_yaml: Path,
) -> None:
    """AB.3.7 — wire-shape round-trip: PUT the existing groups +
    one trailing-blank slot (the server-rendered render shape) and
    confirm the persisted tuple-of-tuples is byte-identical to the
    pre-state. Mirrors the existing leg_rails round-trip pattern —
    asserts the form wire passes the nested-tuple shape through
    ``_coerce_form`` cleanly without depending on validator
    cross-rule traversal."""
    pre = load_instance(writable_l2_yaml)
    tmpl = next(
        (t for t in pre.transfer_templates if t.leg_rail_xor_groups),
        None,
    )
    if tmpl is None:
        return
    tmpl_name = str(tmpl.name)
    n_existing = len(tmpl.leg_rail_xor_groups)
    app = _build_app(writable_l2_yaml)
    data: dict[str, object] = {
        "name": tmpl_name,
        "expected_net": str(tmpl.expected_net),
        "completion": str(tmpl.completion),
        "leg_rails__present": "1",
        "leg_rails": [str(r) for r in tmpl.leg_rails],
        "leg_rail_xor_groups__present": "1",
        # Render emits N existing slots + 1 trailing blank.
        "leg_rail_xor_groups__num_groups": str(n_existing + 1),
    }
    for i, group in enumerate(tmpl.leg_rail_xor_groups):
        data[f"leg_rail_xor_groups_{i}"] = [str(r) for r in group]
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/transfer_template/{tmpl_name}", data=data)
    assert resp.status_code == 200, resp.text

    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        t for t in reloaded.transfer_templates if str(t.name) == tmpl_name
    )
    assert saved.leg_rail_xor_groups == tmpl.leg_rail_xor_groups


def test_put_transfer_template_xor_groups_validator_rejects_invalid_shape(
    writable_l2_yaml: Path,
) -> None:
    """AB.3.7 — submitting a shape the validator rejects (C1: clearing
    the only XOR group when ≥2 Variable rails would be left
    non-grouped) returns 400 + inline error message. Round-trip-side
    invariant: the operator's typed selection is preserved in the
    re-rendered form (X.4.e.5 pattern). Disk unchanged."""
    pre = load_instance(writable_l2_yaml)
    tmpl = next(
        (t for t in pre.transfer_templates if t.leg_rail_xor_groups),
        None,
    )
    if tmpl is None:
        return
    # AB.3.5.spec SettlementTimingCycle has 3 Variable rails + 1 XOR
    # group of [Auto, Standard]; clearing the group ⇒ 3 non-grouped
    # Variables ⇒ C1 rejection (max 1 non-grouped).
    if len(tmpl.leg_rail_xor_groups) != 1:
        return
    tmpl_name = str(tmpl.name)
    app = _build_app(writable_l2_yaml)
    data: dict[str, object] = {
        "name": tmpl_name,
        "expected_net": str(tmpl.expected_net),
        "completion": str(tmpl.completion),
        "leg_rails__present": "1",
        "leg_rails": [str(r) for r in tmpl.leg_rails],
        "leg_rail_xor_groups__present": "1",
        # All slots empty → tuple coerces to () → validator C1 fires.
        "leg_rail_xor_groups__num_groups": "2",
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/transfer_template/{tmpl_name}", data=data)
    assert resp.status_code == 400, resp.text
    assert "form-global-error" in resp.text
    # Validator's exact phrasing (rewritten in AB.3.2).
    assert "non-grouped Variable" in resp.text
    # Disk unchanged.
    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        t for t in reloaded.transfer_templates if str(t.name) == tmpl_name
    )
    assert saved.leg_rail_xor_groups == tmpl.leg_rail_xor_groups


def test_coerce_form_multi_select_groups_filters_empty_groups(
) -> None:
    """AB.3.7 — direct coerce-form unit test (no Starlette / no disk
    persistence) so the validator-bound "removes the only XOR group"
    case can be exercised on its own. Empty groups (operator
    unchecked every box in a row) are filtered server-side; mixed
    populated + empty groups preserve order of the populated ones."""
    from recon_gen.common.html._studio_editor_routes import _coerce_form
    from recon_gen.common.l2.primitives import Identifier

    class _StubForm:
        """Minimal FormData duck-type for _coerce_form's call shape."""

        def __init__(
            self,
            kv: dict[str, object],
            lists: dict[str, list[str]],
        ) -> None:
            self._kv = kv
            self._lists = lists

        def __contains__(self, key: str) -> bool:
            return key in self._kv

        def __getitem__(self, key: str) -> object:
            return self._kv[key]

        def get(self, key: str, default: object = None) -> object:
            return self._kv.get(key, default)

        def getlist(self, key: str) -> list[str]:
            return list(self._lists.get(key, ()))

    # Three slots; middle one empty (operator unchecked all boxes).
    form = _StubForm(
        kv={
            "name": "T",
            "expected_net": "0",
            "completion": "business_day_end+1d",
            "leg_rail_xor_groups__present": "1",
            "leg_rail_xor_groups__num_groups": "3",
        },
        lists={
            "leg_rails": [],
            "leg_rail_xor_groups_0": ["RailA", "RailB"],
            "leg_rail_xor_groups_1": [],
            "leg_rail_xor_groups_2": ["RailC", "RailD"],
        },
    )
    fields, overrides = _coerce_form("transfer_template", form)
    assert fields["leg_rail_xor_groups"] == (
        (Identifier("RailA"), Identifier("RailB")),
        (Identifier("RailC"), Identifier("RailD")),
    )
    # Overrides preserve the operator's submission for re-render on
    # validator-rejected POSTs.
    assert overrides["leg_rail_xor_groups"] == (
        ("RailA", "RailB"),
        ("RailC", "RailD"),
    )


def test_coerce_form_multi_select_groups_all_empty_yields_empty_tuple(
) -> None:
    """AB.3.7 — operator unchecks every checkbox in every group row →
    final value is ``()``. Validator may reject this depending on the
    template (C1: at most 1 non-grouped Variable allowed); coerce
    returns the empty tuple regardless and lets the validator decide."""
    from recon_gen.common.html._studio_editor_routes import _coerce_form

    class _StubForm:
        def __init__(
            self,
            kv: dict[str, object],
            lists: dict[str, list[str]],
        ) -> None:
            self._kv = kv
            self._lists = lists

        def __contains__(self, key: str) -> bool:
            return key in self._kv

        def __getitem__(self, key: str) -> object:
            return self._kv[key]

        def get(self, key: str, default: object = None) -> object:
            return self._kv.get(key, default)

        def getlist(self, key: str) -> list[str]:
            return list(self._lists.get(key, ()))

    form = _StubForm(
        kv={
            "name": "T",
            "expected_net": "0",
            "completion": "business_day_end+1d",
            "leg_rail_xor_groups__present": "1",
            "leg_rail_xor_groups__num_groups": "2",
        },
        lists={"leg_rails": []},
    )
    fields, _ = _coerce_form("transfer_template", form)
    assert fields["leg_rail_xor_groups"] == ()


def test_coerce_form_transfer_key_splits_newline_and_comma() -> None:
    """AI.2.b — transfer_key is a textarea FieldSpec; the operator types
    one key per line (or comma-separated), same shape as Rail.metadata_keys.
    _coerce_field splits on both \\n and , and coerces to tuple[Identifier].
    """
    from recon_gen.common.html._studio_editor_routes import _coerce_form
    from recon_gen.common.l2.primitives import Identifier

    class _StubForm:
        def __init__(self, kv: dict[str, object]) -> None:
            self._kv = kv

        def __contains__(self, key: str) -> bool:
            return key in self._kv

        def __getitem__(self, key: str) -> object:
            return self._kv[key]

        def get(self, key: str, default: object = None) -> object:
            return self._kv.get(key, default)

        def getlist(self, key: str) -> list[str]:
            return []

    form = _StubForm(
        kv={
            "name": "T",
            "expected_net": "0",
            "completion": "business_day_end+1d",
            "transfer_key": "disbursement_id\nbatch_id, settlement_id",
        },
    )
    fields, overrides = _coerce_form("transfer_template", form)
    assert fields["transfer_key"] == (
        Identifier("disbursement_id"),
        Identifier("batch_id"),
        Identifier("settlement_id"),
    )
    # Raw override preserved verbatim for the validator-reject re-render.
    assert overrides["transfer_key"] == (
        "disbursement_id\nbatch_id, settlement_id"
    )


def test_xor_groups_read_card_renders_groups_as_bullets(
    writable_l2_yaml: Path,
) -> None:
    """AB.3.7 — read-only card displays ``leg_rail_xor_groups`` as a
    bullet list ("group 1: A, B", "group 2: C, D") rather than the
    flat-tuple stringifier's noisy ``('A', 'B'), ('C', 'D')`` shape."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    tmpl = next(
        (t for t in pre.transfer_templates if t.leg_rail_xor_groups),
        None,
    )
    if tmpl is None:
        return
    tmpl_name = str(tmpl.name)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/transfer_template/{tmpl_name}")
    assert resp.status_code == 200
    body = resp.text
    # Bullet-list rendering, not the flat-tuple repr.
    assert 'class="xor-group-list"' in body
    members = ", ".join(str(r) for r in tmpl.leg_rail_xor_groups[0])
    assert f"group 1: {members}" in body
    # The flat-tuple repr (parens + comma) should NOT appear.
    assert f"(&#x27;{tmpl.leg_rail_xor_groups[0][0]}&#x27;" not in body


def test_chain_card_id_replaces_double_colon_to_avoid_css_pseudo_element(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.10.fix — chain composite addressing uses ``::`` which CSS
    parses as pseudo-element syntax in selectors. ``hx-target`` runs
    through CSS-selector parsing, so an id ``entity-chain-Foo::Bar``
    can't be targeted (selector ``#entity-chain-Foo::Bar`` interprets
    ``::Bar`` as a pseudo-element). The card's HTML id swaps ``::``
    for ``__`` while the URL-side path keeps ``::`` (matches the L2
    API key contract). This was the actual root cause of "chain edit
    doesn't work" — Edit click → broken hx-target → silent swap miss."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    if not pre.chains:
        return
    chain = pre.chains[0]
    # Z.A: composite key = "parent::sorted-children-csv".
    # AB.6 (per-child): children entries are now ChainChildSpec.
    children_csv = ",".join(sorted(str(ch.name) for ch in chain.children))
    composite = f"{chain.parent}::{children_csv}"
    slug = composite.replace("::", "__")
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(f"/l2_shape/chain/{composite}").text
    # CSS-safe id.
    assert f'id="entity-chain-{slug}"' in body
    # NO raw ``::`` in the id attr.
    assert f'id="entity-chain-{composite}"' not in body
    # hx-target (the Delete link's outerHTML swap) uses the slug too.
    assert f'hx-target="#entity-chain-{slug}"' in body
    # URL path KEEPS the original ``::``. The Edit link is now a plain
    # navigation to the dedicated edit screen (AI.2.e); card id + delete
    # hx-target use the CSS-safe slug.
    assert f'href="/l2_shape/chain/{composite}/edit"' in body


def test_put_chain_edit_renders_card_after_save(
    writable_l2_yaml: Path,
) -> None:
    """Z.A.f1 — chain PUT/save round-trips a `parent` + multi-valued
    `children` form payload through the editor's multi_select coerce
    path and writes the resulting Chain row back to disk. Mirrors the
    leg_rails round-trip pattern: re-post the existing values so the
    invariant tested here is wire shape, not validator-traversal logic.
    """
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    if not pre.chains:
        return  # spec_example has at least one
    chain = pre.chains[0]
    children = [str(c.name) for c in chain.children]
    children_csv = ",".join(sorted(children))
    composite = f"{chain.parent}::{children_csv}"
    data = {
        "parent": str(chain.parent),
        "children__present": "1",
        "children": children,
        "description": str(chain.description or ""),
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/chain/{composite}", data=data)
    assert resp.status_code == 200, resp.text

    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        ch for ch in reloaded.chains
        if str(ch.parent) == str(chain.parent)
        and sorted(str(c.name) for c in ch.children) == sorted(children)
    )
    assert sorted(str(c.name) for c in saved.children) == sorted(children)


def test_chain_create_form_renders_parent_child_dropdowns(
    writable_l2_yaml: Path,
) -> None:
    """Z.A.f1 + AB.6.7 — chain create form renders `parent` as a
    single-select and `children` as a chain_children checkbox group
    (one input per available rail/template). NO legacy `child` /
    `required` / `xor_group` fields."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/chain/new")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert '<select id="field-parent" name="parent">' in body
    # AB.6.7 — chain_children kind replaces multi_select for chain.children.
    assert 'class="chain-children-group"' in body
    assert 'name="children__present"' in body
    assert 'name="child"' not in body
    assert 'name="required"' not in body
    assert 'name="xor_group"' not in body


def test_chain_create_form_has_per_child_fan_in_sub_inputs(
    writable_l2_yaml: Path,
) -> None:
    """AB.6.7 — chain create form exposes per-child fan_in checkbox +
    expected-parent-count text input alongside each candidate child.
    The shape replaces AB.4.9's chain-level fan_in/expected_parent_count
    fields (AB.6.0 Lock 2 hard cut). Operator can mix fan_in flags
    per child."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/chain/new")
    body = resp.text
    # No chain-level fan_in select (removed at AB.6.0 Lock 2).
    assert '<select id="field-fan_in" name="fan_in">' not in body
    assert 'id="field-expected_parent_count"' not in body
    # Per-child fan_in checkbox + epc input for ≥1 candidate child
    # (option set from rails_or_templates).
    assert 'name="fan_in_' in body
    assert 'name="epc_' in body


def test_chain_card_renders_per_child_fan_in_on_existing_chain(
    writable_l2_yaml: Path,
) -> None:
    """AB.6.7 — when the L2 has a chain with a per-child fan_in entry
    (AB.4.5.spec's BatchPayoutTrigger → BatchedPayoutBatch), the chain
    card edit form pre-checks the per-child fan_in box + pre-fills the
    expected-parent-count input for that specific child."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    fan_in_chain = next(
        (c for c in pre.chains if any(ch.fan_in for ch in c.children)),
        None,
    )
    if fan_in_chain is None:
        return  # AB.4.5.spec hasn't activated; defensive skip
    children_csv = ",".join(sorted(str(ch.name) for ch in fan_in_chain.children))
    composite = f"{fan_in_chain.parent}::{children_csv}"
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(f"/l2_shape/chain/{composite}/edit")
    assert resp.status_code == 200, resp.text
    body = resp.text
    fan_in_child = next(ch for ch in fan_in_chain.children if ch.fan_in)
    name = str(fan_in_child.name)
    # Per-child fan_in checkbox pre-checked.
    assert (
        f'name="fan_in_{name}" value="true" checked' in body
    ), f"expected pre-checked fan_in checkbox for {name!r} in body"
    # Per-child epc text input carries the value when set.
    if fan_in_child.expected_parent_count is not None:
        assert (
            f'name="epc_{name}" '
            f'value="{fan_in_child.expected_parent_count}"'
        ) in body


def _escape_html_attr(s: str) -> str:
    """Tiny helper for asserting against escape()'d attribute values."""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


# Used inside the leg_rails test above — keep right next to it.
escape_html = _escape_html_attr


def test_two_leg_rail_edit_form_renders_subtype_fields(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.11 — a TwoLegRail's edit form surfaces source_role +
    destination_role + aggregating, and HIDES the SingleLegRail-only
    leg_role + leg_direction fields. ExternalRailInbound is a TwoLeg
    in spec_example."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/ExternalRailInbound/edit")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # TwoLeg-only fields render.
    assert 'name="source_role"' in body, "source_role multi-select missing"
    assert 'name="destination_role"' in body, "destination_role missing"
    # The currently-set source_role value is checked in the multi-select.
    assert 'value="ExternalCounterparty" checked' in body
    # aggregating select renders (both subtypes).
    assert '<select id="field-aggregating" name="aggregating">' in body
    # SingleLeg-only fields are filtered out.
    assert 'name="leg_role"' not in body
    assert 'name="leg_direction"' not in body


def test_rail_edit_form_renders_amount_typical_range_field(
    writable_l2_yaml: Path,
) -> None:
    """AB.5 (E7) — Rail edit form exposes amount_typical_range as a
    text input. spec_example's ExternalRailInbound declares
    [50.00, 5000.00] (AB.5.6.spec) so the value pre-fills."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/ExternalRailInbound/edit")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert 'name="amount_typical_range"' in body
    # Pre-fills with the existing value (comma-separated Money tuple).
    # The default tuple-stringification produces "50.00, 5000.00".
    assert "50.00, 5000.00" in body


def test_rail_amount_typical_range_coerce_round_trip() -> None:
    """AB.5 (E7) — _coerce_field parses `min, max` into
    tuple[Money, Money]. Invalid shapes raise ValueError that the
    form re-renders with inline error."""
    from decimal import Decimal

    from recon_gen.common.html._studio_editor_routes import (
        FieldSpec,
        _coerce_field,
    )
    from recon_gen.common.l2.primitives import Money

    spec = FieldSpec(
        name="amount_typical_range",
        label="Range",
        helper="",
        kind="text",
    )
    out = _coerce_field(spec, "5.00, 500.00", "rail")
    assert out == (Money(Decimal("5.00")), Money(Decimal("500.00")))
    # Bad shape — 1 value, not 2.
    with pytest.raises(ValueError, match="comma-separated"):
        _coerce_field(spec, "5.00", "rail")
    # Bad value — non-numeric.
    with pytest.raises(ValueError, match="numeric values"):
        _coerce_field(spec, "five, ten", "rail")


def test_rail_edit_form_renders_firings_typical_per_period_field(
    writable_l2_yaml: Path,
) -> None:
    """AF (E8) — Rail edit form exposes firings_typical_per_period as a
    text input. spec_example's ExternalRailInbound declares [20, 50]
    (AF.5.spec, business_day compact) so the value pre-fills as the
    bare `20, 50` shape; SubledgerCharge declares {month, [60,90]} so it
    pre-fills as `month: 60, 90`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/ExternalRailInbound/edit")
        assert resp.status_code == 200, resp.text
        assert 'name="firings_typical_per_period"' in resp.text
        assert "20, 50" in resp.text
        resp2 = c.get("/l2_shape/rail/SubledgerCharge/edit")
        assert resp2.status_code == 200, resp2.text
        assert "month: 60, 90" in resp2.text


def test_firings_typical_per_period_coerce_round_trip() -> None:
    """AF (E8) — _coerce_field parses the composite text shape into a
    FiringsTypicalPerPeriod for both rail + template kinds. Bad shapes
    raise ValueError that the form re-renders with an inline error."""
    from recon_gen.common.html._studio_editor_routes import (
        FieldSpec,
        _coerce_field,
    )
    from recon_gen.common.l2.primitives import FiringsTypicalPerPeriod

    spec = FieldSpec(
        name="firings_typical_per_period",
        label="Firings",
        helper="",
        kind="text",
    )
    # Compact form — period defaults business_day.
    assert _coerce_field(spec, "50, 500", "rail") == FiringsTypicalPerPeriod(
        period="business_day", count_range=(50, 500),
    )
    # Mapping form — explicit period.
    assert _coerce_field(spec, "month: 80, 120", "rail") == FiringsTypicalPerPeriod(
        period="month", count_range=(80, 120),
    )
    # Template kind coerces identically (same field name).
    assert _coerce_field(
        spec, "week: 3, 8", "transfer_template",
    ) == FiringsTypicalPerPeriod(period="week", count_range=(3, 8))
    # Empty → None (optional field).
    assert _coerce_field(spec, "", "rail") is None
    # Bad: wrong element count.
    with pytest.raises(ValueError, match="min, max"):
        _coerce_field(spec, "50", "rail")
    # Bad: non-integer count.
    with pytest.raises(ValueError, match="integers"):
        _coerce_field(spec, "5.5, 10", "rail")
    # Bad: unknown period.
    with pytest.raises(ValueError, match="period must be one of"):
        _coerce_field(spec, "fortnight: 1, 2", "rail")


def test_single_leg_rail_edit_form_renders_subtype_fields(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.11 — a SingleLegRail's edit form surfaces leg_role +
    leg_direction (with the Debit/Credit/Variable enum) + aggregating,
    and HIDES the TwoLegRail-only source_role / destination_role
    fields. SubledgerCharge is a SingleLeg in spec_example."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/SubledgerCharge/edit")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # SingleLeg-only fields render.
    assert 'name="leg_role"' in body, "leg_role multi-select missing"
    assert '<select id="field-leg_direction" name="leg_direction">' in body
    # The Debit/Credit/Variable enum options surface.
    assert 'value="Debit"' in body
    assert 'value="Credit"' in body
    assert 'value="Variable"' in body
    # aggregating select renders (both subtypes).
    assert '<select id="field-aggregating" name="aggregating">' in body
    # TwoLeg-only fields are filtered out.
    assert 'name="source_role"' not in body
    assert 'name="destination_role"' not in body


def test_two_leg_rail_read_card_renders_subtype_fields(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.11 — TwoLegRail read card shows the source_role +
    destination_role values (and not the SingleLeg-only fields)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/ExternalRailInbound")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # source_role / destination_role labels surface in the read card.
    assert "Source role" in body
    assert "Destination role" in body
    # Single-leg-only labels are hidden.
    assert "Leg role" not in body
    assert "Leg direction" not in body


def test_put_two_leg_rail_round_trips_subtype_fields(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.11.2 — full PUT round-trip for TwoLegRail's source_role +
    destination_role. Submits the same values back (rather than
    changing them) so the test asserts the wire shape — multi_select
    coerce + Identifier wrap + dataclasses.replace round-trip — without
    tripping cross-entity validator rules that constrain which role a
    template's leg_rails can hit. The invariant "subtype fields arrive
    intact through PUT" is what matters here."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    rail = next(
        r for r in pre.rails if str(r.name) == "ExternalRailInbound"
    )
    src_roles = [str(x) for x in getattr(rail, "source_role")]
    dst_roles = [str(x) for x in getattr(rail, "destination_role")]
    data = {
        "name": str(rail.name),
        "source_role__present": "1",
        "source_role": src_roles,
        "destination_role__present": "1",
        "destination_role": dst_roles,
        "aggregating": "true" if getattr(rail, "aggregating") else "false",
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/rail/{rail.name}", data=data)
    assert resp.status_code == 200, resp.text

    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        r for r in reloaded.rails if str(r.name) == str(rail.name)
    )
    assert [str(x) for x in getattr(saved, "source_role")] == src_roles
    assert [str(x) for x in getattr(saved, "destination_role")] == dst_roles


def test_rail_metadata_value_examples_yaml_block_round_trip(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.11.6.5 — Tier-3 yaml_block FieldKind. Operator types a
    YAML map; coerce parses + wraps to tuple-of-tuples; PUT persists;
    re-load round-trips the same shape. ExternalRailInbound is a
    TwoLeg in spec_example."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    rail = next(
        r for r in pre.rails if str(r.name) == "ExternalRailInbound"
    )
    # Use a metadata_key already declared on this rail (validator R13:
    # metadata_value_examples keys must be a subset of metadata_keys).
    # ExternalRailInbound declares metadata_keys=['external_reference'].
    yaml_block = (
        "external_reference:\n"
        "  - 'EXT-12345-001'\n"
        "  - 'EXT-12345-002'\n"
        "  - 'EXT-12345-003'\n"
    )
    src_roles = [str(x) for x in getattr(rail, "source_role")]
    dst_roles = [str(x) for x in getattr(rail, "destination_role")]
    data = {
        "name": str(rail.name),
        "source_role__present": "1",
        "source_role": src_roles,
        "destination_role__present": "1",
        "destination_role": dst_roles,
        "aggregating": "true" if getattr(rail, "aggregating") else "false",
        "metadata_value_examples": yaml_block,
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/rail/{rail.name}", data=data)
    assert resp.status_code == 200, resp.text

    reloaded = load_instance(writable_l2_yaml)
    saved = next(
        r for r in reloaded.rails if str(r.name) == str(rail.name)
    )
    examples = getattr(saved, "metadata_value_examples")
    keys_to_values = {str(k): list(v) for k, v in examples}
    assert keys_to_values == {
        "external_reference": ["EXT-12345-001", "EXT-12345-002", "EXT-12345-003"],
    }


def test_rail_metadata_value_examples_bad_yaml_returns_400(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.11.6.5 — bad YAML in the yaml_block triggers a 400 +
    form re-render with the error inline + the typed content
    preserved (for the operator to fix)."""
    app = _build_app(writable_l2_yaml)
    pre = load_instance(writable_l2_yaml)
    rail = next(
        r for r in pre.rails if str(r.name) == "ExternalRailInbound"
    )
    bad_yaml = "ach_trace_number:\n  - '12345-001\n   missing-quote"
    src_roles = [str(x) for x in getattr(rail, "source_role")]
    dst_roles = [str(x) for x in getattr(rail, "destination_role")]
    data = {
        "name": str(rail.name),
        "source_role__present": "1",
        "source_role": src_roles,
        "destination_role__present": "1",
        "destination_role": dst_roles,
        "aggregating": "true" if getattr(rail, "aggregating") else "false",
        "metadata_value_examples": bad_yaml,
    }
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(f"/l2_shape/rail/{rail.name}", data=data)
    assert resp.status_code == 400, resp.text
    assert "Invalid YAML" in resp.text or "Field coercion failed" in resp.text


def test_singleton_theme_get_renders_yaml_block(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.12 — GET /l2_shape/theme/ renders the singleton edit page
    with the existing theme dumped as YAML in the textarea (or empty
    when the L2 has no theme block declared)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/theme/")
    assert resp.status_code == 200, resp.text
    assert 'name="yaml"' in resp.text
    assert "yaml-block" in resp.text


def test_singleton_persona_save_round_trips(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.12 — POST /l2_shape/persona/ with a YAML map updates
    L2Instance.persona and the round-trip survives reload. Spec_example
    has no persona by default, so this exercises the create path."""
    app = _build_app(writable_l2_yaml)
    yaml_text = (
        "institution:\n"
        "  - 'Test Bank'\n"
        "  - 'TB'\n"
        "stakeholders:\n"
        "  - 'Federal Reserve Bank'\n"
        "merchants:\n"
        "  - 'Acme Coffee'\n"
        "  - 'Beta Bakery'\n"
    )
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(
            "/l2_shape/persona/",
            data={"yaml": yaml_text},
            follow_redirects=False,
        )
    assert resp.status_code == 303, resp.text

    reloaded = load_instance(writable_l2_yaml)
    persona = reloaded.persona
    assert persona is not None
    assert list(persona.institution) == ["Test Bank", "TB"]
    assert list(persona.stakeholders) == ["Federal Reserve Bank"]
    assert list(persona.merchants) == ["Acme Coffee", "Beta Bakery"]


def test_singleton_theme_bad_yaml_returns_400(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.12 — bad YAML in the singleton form returns 400 + the
    operator's typed content preserved + the validator error inline."""
    app = _build_app(writable_l2_yaml)
    bad_yaml = "theme_name: 'unclosed\n  data_colors:\n    - '#abc'"
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(
            "/l2_shape/theme/",
            data={"yaml": bad_yaml},
            follow_redirects=False,
        )
    assert resp.status_code == 400, resp.text
    # The form re-renders with a global-error block (either "Invalid
    # YAML" if the parser choked, or a loader/validator error if the
    # YAML parsed but the shape is wrong).
    assert 'class="form-global-error"' in resp.text


def test_delete_unreferenced_account_persists(writable_l2_yaml: Path) -> None:
    """Deleting cust-002 succeeds — no rail / template references it
    by id; the role CustomerSubledger is still satisfied by cust-001."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.delete("/l2_shape/account/cust-002")
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("HX-Trigger") == "l2-cascade-reload"

    reloaded = load_instance(writable_l2_yaml)
    assert not any(str(a.id) == "cust-002" for a in reloaded.accounts)
