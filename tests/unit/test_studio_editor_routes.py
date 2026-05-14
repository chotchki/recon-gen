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

from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.loader import load_instance
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


def test_edit_form_returns_form_fragment(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/cust-001/edit")
        assert resp.status_code == 200
        body = resp.text
        assert "<form" in body
        # PUT target points back at the same id.
        assert 'hx-put="/l2_shape/account/cust-001"' in body


def test_unknown_kind_returns_404(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/not-a-kind/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Write-side: PUT save (X.4.e.4)
# ---------------------------------------------------------------------------


def test_put_account_persists_to_disk_and_triggers_cascade(
    writable_l2_yaml: Path,
) -> None:
    """Save flow: PUT → mutate → validate → cache.save (atomic write
    + cache.replace) → respond with read card + HX-Trigger header.
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
        )
        assert resp.status_code == 200, resp.text
        # Cascade trigger lets the diagram + entity list hx-get
        # themselves to pick up the change.
        assert resp.headers.get("HX-Trigger") == "l2-cascade-reload"
        # Response body carries the new read card.
        assert "Customer One Edited" in resp.text

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
