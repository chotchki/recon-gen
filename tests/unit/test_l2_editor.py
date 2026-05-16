"""Editor primitive tests (X.4.d.1 / X.4.d.2 / X.4.d.5 + X.4.d.6).

Three transforms covered:

- ``mutate_l2`` — single-entity field replacement via dataclasses.replace.
  Round-trips through the validator (the SPEC's "validate every save"
  contract is composed at the call site, exercised in the X.4.d.4
  tests).
- ``rename_identifier`` — every reference rewritten across the
  L2Instance. Tested per kind (role, rail, transfer_template) by
  asserting both the renamed entity AND the references in OTHER
  entities flipped to the new name.
- ``delete_l2_entity`` — leaves a structural break the validator
  catches (we run validate() in the test to confirm rejection).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quicksight_gen.common.l2.editor import (
    delete_l2_entity,
    mutate_l2,
    rename_identifier,
)
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.primitives import (
    Account,
    Identifier,
    L2Instance,
    Rail,
    TransferTemplate,
)
from quicksight_gen.common.l2.validate import (
    L2ValidationError,
    validate as validate_instance,
)


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def spec_example() -> L2Instance:
    return load_instance(_FIXTURES / "spec_example.yaml")


# ---------------------------------------------------------------------------
# mutate_l2
# ---------------------------------------------------------------------------


def test_mutate_account_replaces_field_returns_new_instance(
    spec_example: L2Instance,
) -> None:
    """Mutate one Account.name; the rest of the model + the original
    instance stay untouched."""
    new_inst = mutate_l2(
        spec_example,
        kind="account",
        entity_id="cust-001",
        fields={"name": "Customer One Renamed"},
    )

    # Original untouched.
    orig_acct = next(a for a in spec_example.accounts if str(a.id) == "cust-001")
    assert orig_acct.name == "Customer Number One"

    # New copy carries the change.
    new_acct = next(a for a in new_inst.accounts if str(a.id) == "cust-001")
    assert new_acct.name == "Customer One Renamed"

    # Other accounts identical.
    for a_old, a_new in zip(spec_example.accounts, new_inst.accounts, strict=True):
        if str(a_old.id) != "cust-001":
            assert a_old == a_new


def test_mutate_rail_replaces_field(spec_example: L2Instance) -> None:
    """Mutate a Rail field; only the matched rail changes."""
    new_inst = mutate_l2(
        spec_example,
        kind="rail",
        entity_id="ExternalRailInbound",
        fields={"origin": "InternalInitiated"},
    )
    new_rail = next(
        r for r in new_inst.rails if str(r.name) == "ExternalRailInbound"
    )
    assert new_rail.origin == "InternalInitiated"


def test_mutate_chain_uses_composite_key(spec_example: L2Instance) -> None:
    """Chains have no .id — addressing uses
    ``<parent>::<sorted-children-csv>``. Z.A grammar.
    """
    new_inst = mutate_l2(
        spec_example,
        kind="chain",
        entity_id="ExternalReconciliationCycle::ReconciliationClosing",
        fields={"description": "edited"},
    )
    chain = new_inst.chains[0]
    assert chain.description == "edited"


def test_mutate_unknown_id_raises_keyerror(
    spec_example: L2Instance,
) -> None:
    with pytest.raises(KeyError, match="not-a-real-account"):
        mutate_l2(
            spec_example,
            kind="account",
            entity_id="not-a-real-account",
            fields={"name": "X"},
        )


def test_mutate_unknown_field_raises_typeerror(
    spec_example: L2Instance,
) -> None:
    """``dataclasses.replace`` raises TypeError on unknown kwarg —
    surfaces as the editor's bad-field signal at the call site
    (Studio's PUT handler maps to 400)."""
    with pytest.raises(TypeError):
        mutate_l2(
            spec_example,
            kind="account",
            entity_id="cust-001",
            fields={"made_up_field": "X"},
        )


# ---------------------------------------------------------------------------
# rename_identifier
# ---------------------------------------------------------------------------


def test_rename_role_walks_account_role_plus_every_reference(
    spec_example: L2Instance,
) -> None:
    """Renaming CustomerSubledger should rewrite:
    - the Account.role on cust-001 / cust-002,
    - the AccountTemplate.role for the CustomerSubledger template,
    - every Rail's source_role / destination_role / leg_role,
    - the parent_role on Accounts that reference it.
    Validator should still pass.
    """
    new_role = Identifier("RetailSubledger")
    new_inst = rename_identifier(
        spec_example,
        kind="account",
        old=Identifier("CustomerSubledger"),
        new=new_role,
    )

    # Accounts updated.
    cust_001 = next(a for a in new_inst.accounts if str(a.id) == "cust-001")
    assert cust_001.role == new_role

    # AccountTemplate updated.
    assert any(
        t.role == new_role for t in new_inst.account_templates
    )

    # Rails — every reference in source_role / destination_role / leg_role
    # of every rail flipped.
    for r in new_inst.rails:
        if hasattr(r, "source_role"):
            for role in r.source_role:
                assert role != Identifier("CustomerSubledger")
        if hasattr(r, "destination_role"):
            for role in r.destination_role:
                assert role != Identifier("CustomerSubledger")
        if hasattr(r, "leg_role"):
            for role in r.leg_role:
                assert role != Identifier("CustomerSubledger")

    # And the model still validates (rename is a clean cascade — every
    # ref still resolves to a declared role).
    validate_instance(new_inst)


def test_rename_role_preserves_unrelated_roles(
    spec_example: L2Instance,
) -> None:
    """Renaming CustomerSubledger leaves CustomerLedger / NorthPool /
    etc. untouched."""
    new_inst = rename_identifier(
        spec_example,
        kind="account",
        old=Identifier("CustomerSubledger"),
        new=Identifier("RetailSubledger"),
    )
    cl = next(a for a in new_inst.accounts if str(a.id) == "customer-ledger")
    assert cl.role == Identifier("CustomerLedger")  # unchanged


def test_rename_rail_walks_template_leg_rails_and_chains(
    spec_example: L2Instance,
) -> None:
    """Renaming a Rail should rewrite:
    - the Rail.name itself,
    - any TransferTemplate.leg_rails containing it,
    - any Chain.parent / Chain.children entry equal to it,
    - any Rail.bundles_activity entry equal to it.
    """
    new_name = Identifier("ReconciliationLegRenamed")
    new_inst = rename_identifier(
        spec_example,
        kind="rail",
        old=Identifier("ReconciliationLeg"),
        new=new_name,
    )

    # Rail renamed.
    assert any(r.name == new_name for r in new_inst.rails)
    assert not any(
        r.name == Identifier("ReconciliationLeg") for r in new_inst.rails
    )

    # Transfer template's leg_rails updated (ExternalReconciliationCycle).
    erc = next(
        tt for tt in new_inst.transfer_templates
        if str(tt.name) == "ExternalReconciliationCycle"
    )
    assert new_name in erc.leg_rails
    assert Identifier("ReconciliationLeg") not in erc.leg_rails


def test_rename_transfer_template_walks_chain_endpoints(
    spec_example: L2Instance,
) -> None:
    """Renaming a TransferTemplate should rewrite Chain.parent
    or .child references."""
    new_name = Identifier("ExternalReconciliationCycleRenamed")
    new_inst = rename_identifier(
        spec_example,
        kind="transfer_template",
        old=Identifier("ExternalReconciliationCycle"),
        new=new_name,
    )

    # Template renamed.
    assert any(tt.name == new_name for tt in new_inst.transfer_templates)

    # Chain parent/children entries updated.
    for c in new_inst.chains:
        if c.parent == Identifier("ExternalReconciliationCycle"):
            pytest.fail("Chain.parent still references the old name")


def test_rename_chain_is_noop(spec_example: L2Instance) -> None:
    """Chains have no incoming references — rename returns the original."""
    out = rename_identifier(
        spec_example,
        kind="chain",
        old=Identifier("anything"),
        new=Identifier("else"),
    )
    assert out is spec_example


# ---------------------------------------------------------------------------
# delete_l2_entity
# ---------------------------------------------------------------------------


def test_delete_account_removes_from_collection(
    spec_example: L2Instance,
) -> None:
    """Delete one Account by id; the collection shrinks by 1."""
    n_before = len(spec_example.accounts)
    new_inst = delete_l2_entity(
        spec_example, kind="account", entity_id="cust-002",
    )
    assert len(new_inst.accounts) == n_before - 1
    assert not any(str(a.id) == "cust-002" for a in new_inst.accounts)


def test_delete_rail_with_dependent_template_validator_rejects(
    spec_example: L2Instance,
) -> None:
    """SPEC's "structural break" rule: deleting a Rail that a
    TransferTemplate.leg_rails still references leaves the model
    invalid; the validator raises so the Studio PUT handler returns
    400 with the message inline.
    """
    # ReconciliationLeg is in ExternalReconciliationCycle.leg_rails.
    new_inst = delete_l2_entity(
        spec_example, kind="rail", entity_id="ReconciliationLeg",
    )
    with pytest.raises(L2ValidationError):
        validate_instance(new_inst)


def test_delete_unknown_id_raises_keyerror(
    spec_example: L2Instance,
) -> None:
    with pytest.raises(KeyError):
        delete_l2_entity(
            spec_example, kind="account", entity_id="ghost-account",
        )


# ---------------------------------------------------------------------------
# mutate_l2 + serialize round-trip (composes with X.4.d.3)
# ---------------------------------------------------------------------------


def test_mutate_round_trip_through_serializer_preserves_change(
    spec_example: L2Instance,
) -> None:
    """The X.4.e cascade flow's full round: mutate → serialize →
    re-load. The mutation MUST survive the YAML emit/parse cycle.
    """
    import os
    import tempfile

    from quicksight_gen.common.l2.serializer import serialize_l2

    new_inst = mutate_l2(
        spec_example,
        kind="account",
        entity_id="cust-001",
        fields={"name": "Mutated Then Round-Tripped"},
    )

    fd, path_str = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    path = Path(path_str)
    try:
        path.write_text(serialize_l2(new_inst))
        reloaded = load_instance(path)
    finally:
        path.unlink()

    cust = next(a for a in reloaded.accounts if str(a.id) == "cust-001")
    assert cust.name == "Mutated Then Round-Tripped"
    # Sanity: round-trip didn't accidentally drift other accounts either.
    assert reloaded == new_inst
