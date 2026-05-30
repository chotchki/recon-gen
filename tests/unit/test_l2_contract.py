"""Tests for ``common.l2.contract`` (BT.5 — column-contract derivation).

``derive_column_contracts`` is a pure function over an ``L2Instance``;
the tests below isolate one primitive at a time and assert the
resulting ``ColumnContracts`` shape, then a combined test against a
spec-style instance proves the derivation composes.

The spec_example + sasquatch_pr fixtures aren't loaded here — the
contract derivation is a stable transform of the typed primitives, so
hand-rolled minimal instances catch every codepath while staying
self-documenting. End-to-end coverage against the bundled fixtures
lives in the BT.4 surface tests once that lands.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from recon_gen.common.l2 import (
    Account,
    Chain,
    ChainChildSpec,
    ChainEdgeContract,
    ColumnContracts,
    ColumnPredicate,
    Identifier,
    L2Instance,
    LimitContract,
    LimitSchedule,
    RailContract,
    RailName,
    RowSelector,
    SingleLegRail,
    TemplateContract,
    TransferTemplate,
    TwoLegRail,
    derive_column_contracts,
)
from recon_gen.common.l2.primitives import Rail


def _make_instance(
    *,
    rails: tuple[Rail, ...] = (),
    transfer_templates: tuple[TransferTemplate, ...] = (),
    chains: tuple[Chain, ...] = (),
    limit_schedules: tuple[LimitSchedule, ...] = (),
) -> L2Instance:
    """Minimal L2Instance for isolating one contract source at a time."""
    return L2Instance(
        accounts=(
            Account(id=Identifier("a"), scope="internal", role=Identifier("A")),
            Account(id=Identifier("b"), scope="internal", role=Identifier("B")),
        ),
        account_templates=(),
        rails=rails,
        transfer_templates=transfer_templates,
        chains=chains,
        limit_schedules=limit_schedules,
    )


def _two_leg(name: str, **kwargs: Any) -> TwoLegRail:
    defaults: dict[str, Any] = dict(
        name=Identifier(name),
        metadata_keys=(),
        source_role=(Identifier("A"),),
        destination_role=(Identifier("B"),),
        origin="InternalInitiated",
        expected_net=Decimal("0"),
    )
    defaults.update(kwargs)
    return TwoLegRail(**defaults)


def _single_leg(name: str, **kwargs: Any) -> SingleLegRail:
    defaults: dict[str, Any] = dict(
        name=Identifier(name),
        metadata_keys=(),
        leg_role=(Identifier("A"),),
        leg_direction="Debit",
        origin="InternalInitiated",
    )
    defaults.update(kwargs)
    return SingleLegRail(**defaults)


# -- Empty instance ----------------------------------------------------------


def test_empty_instance_yields_empty_contract_collections() -> None:
    contracts = derive_column_contracts(_make_instance())
    assert isinstance(contracts, ColumnContracts)
    assert contracts.rails == ()
    assert contracts.templates == ()
    assert contracts.chain_edges == ()
    assert contracts.limits == ()


# -- TwoLegRail contracts ----------------------------------------------------


def test_two_leg_rail_emits_selector_and_role_predicate() -> None:
    inst = _make_instance(rails=(_two_leg("Wire"),))
    rc = derive_column_contracts(inst).rails[0]
    assert rc.rail_name == Identifier("Wire")
    assert rc.leg_kind == "two_leg"
    assert rc.selector == RowSelector(column="rail_name", equals="Wire")
    # source_role={A}, destination_role={B} — union {A, B}, sorted.
    assert rc.predicates == (
        ColumnPredicate(column="account_role", kind="one_of", expected=("A", "B")),
    )
    assert rc.editor_path == "/l2_shape/rail/Wire/edit"


def test_two_leg_rail_role_union_dedupes_overlapping_source_and_destination() -> None:
    rail = _two_leg(
        "InternalSwap",
        source_role=(Identifier("A"), Identifier("C")),
        destination_role=(Identifier("C"), Identifier("B")),
    )
    rc = derive_column_contracts(_make_instance(rails=(rail,))).rails[0]
    # {A,C} ∪ {C,B} = {A,B,C}, sorted; no duplicate C.
    role_preds = [p for p in rc.predicates if p.column == "account_role"]
    assert len(role_preds) == 1
    assert role_preds[0].expected == ("A", "B", "C")


def test_two_leg_rail_emits_metadata_not_null_per_key() -> None:
    rail = _two_leg(
        "ACH",
        metadata_keys=(Identifier("batch_id"), Identifier("trace_number")),
    )
    rc = derive_column_contracts(_make_instance(rails=(rail,))).rails[0]
    metadata_preds = [p for p in rc.predicates if p.column.startswith("metadata.")]
    assert metadata_preds == [
        ColumnPredicate(column="metadata.batch_id", kind="not_null", expected=None),
        ColumnPredicate(
            column="metadata.trace_number", kind="not_null", expected=None,
        ),
    ]


# -- SingleLegRail contracts -------------------------------------------------


def test_single_leg_rail_emits_role_and_direction_predicates() -> None:
    rail = _single_leg("ChargeOff", leg_direction="Debit")
    rc = derive_column_contracts(_make_instance(rails=(rail,))).rails[0]
    assert rc.leg_kind == "single_leg"
    assert rc.selector == RowSelector(column="rail_name", equals="ChargeOff")
    assert rc.predicates == (
        ColumnPredicate(column="account_role", kind="one_of", expected=("A",)),
        ColumnPredicate(column="amount_direction", kind="equals", expected="Debit"),
    )


def test_single_leg_rail_credit_direction_round_trips() -> None:
    rail = _single_leg("InboundACH", leg_direction="Credit")
    rc = derive_column_contracts(_make_instance(rails=(rail,))).rails[0]
    dir_preds = [p for p in rc.predicates if p.column == "amount_direction"]
    assert dir_preds == [
        ColumnPredicate(
            column="amount_direction", kind="equals", expected="Credit",
        ),
    ]


def test_single_leg_variable_direction_skips_direction_predicate() -> None:
    """Variable-direction = closing leg of a TransferTemplate. Direction is
    resolved at posting time by the template's ExpectedNet closure, so the
    contract MUST NOT pin a static Debit/Credit value."""
    rail = _single_leg("Closer", leg_direction="Variable")
    rc = derive_column_contracts(_make_instance(rails=(rail,))).rails[0]
    assert all(p.column != "amount_direction" for p in rc.predicates)
    # account_role + metadata predicates still present.
    assert any(p.column == "account_role" for p in rc.predicates)


def test_single_leg_role_union_dedupes_when_leg_role_lists_duplicates() -> None:
    rail = _single_leg(
        "MultiRoleLeg",
        leg_role=(Identifier("B"), Identifier("A"), Identifier("B")),
    )
    rc = derive_column_contracts(_make_instance(rails=(rail,))).rails[0]
    role_preds = [p for p in rc.predicates if p.column == "account_role"]
    assert role_preds[0].expected == ("A", "B")


# -- TransferTemplate contracts ----------------------------------------------


def test_transfer_template_emits_selector_rail_list_and_transfer_key_predicates() -> None:
    template = TransferTemplate(
        name=Identifier("MerchantSettlementCycle"),
        expected_net=Decimal("0"),
        transfer_key=(Identifier("merchant_id"), Identifier("settlement_period")),
        completion="business_day_end",
        leg_rails=(Identifier("Charge"), Identifier("Settlement")),
    )
    inst = _make_instance(
        rails=(_single_leg("Charge"), _single_leg("Settlement")),
        transfer_templates=(template,),
    )
    tc = derive_column_contracts(inst).templates[0]
    assert tc.template_name == Identifier("MerchantSettlementCycle")
    assert tc.selector == RowSelector(
        column="template_name", equals="MerchantSettlementCycle",
    )
    assert tc.leg_rail_names == (Identifier("Charge"), Identifier("Settlement"))
    assert tc.predicates == (
        ColumnPredicate(
            column="rail_name", kind="one_of",
            expected=("Charge", "Settlement"),
        ),
        ColumnPredicate(
            column="metadata.merchant_id", kind="not_null", expected=None,
        ),
        ColumnPredicate(
            column="metadata.settlement_period", kind="not_null", expected=None,
        ),
    )
    assert tc.editor_path == (
        "/l2_shape/transfer_template/MerchantSettlementCycle/edit"
    )


def test_transfer_template_with_empty_transfer_key_only_pins_rail_name_one_of() -> None:
    template = TransferTemplate(
        name=Identifier("Internal"),
        expected_net=Decimal("0"),
        transfer_key=(),
        completion="business_day_end",
        leg_rails=(Identifier("LegA"),),
    )
    inst = _make_instance(
        rails=(_single_leg("LegA"),),
        transfer_templates=(template,),
    )
    tc = derive_column_contracts(inst).templates[0]
    assert tc.predicates == (
        ColumnPredicate(
            column="rail_name", kind="one_of", expected=("LegA",),
        ),
    )


# -- Chain contracts ---------------------------------------------------------


def test_singleton_chain_emits_one_edge_with_transfer_parent_id_predicate() -> None:
    """Singleton-children chain encodes Z.A's 'required' semantics: the
    child always fires under the parent, so every child firing carries
    a transfer_parent_id link to the parent."""
    chain = Chain(
        parent=Identifier("OrderRail"),
        children=(ChainChildSpec(name=Identifier("ConfirmRail")),),
    )
    inst = _make_instance(
        rails=(_two_leg("OrderRail"), _two_leg("ConfirmRail")),
        chains=(chain,),
    )
    edges = derive_column_contracts(inst).chain_edges
    assert len(edges) == 1
    edge = edges[0]
    assert edge.parent == Identifier("OrderRail")
    assert edge.child == Identifier("ConfirmRail")
    assert edge.is_singleton is True
    assert edge.fan_in is False
    assert edge.expected_parent_count is None
    assert edge.predicates == (
        ColumnPredicate(
            column="transfer_parent_id", kind="not_null", expected=None,
        ),
    )
    # Composite-key composite path matches _studio_editor_routes._entity_id.
    assert edge.editor_path == "/l2_shape/chain/OrderRail::ConfirmRail/edit"


def test_xor_chain_emits_one_edge_per_child_without_transfer_parent_id_predicate() -> None:
    """Multi-children (XOR) chain — exactly one sibling fires per parent
    invocation, so transfer_parent_id is optional, not a hard requirement.
    The 'exactly one fired' check lives in the BS.5 _v_config_chain_children
    matview, not at the row level."""
    chain = Chain(
        parent=Identifier("PaymentRail"),
        children=(
            ChainChildSpec(name=Identifier("ACH")),
            ChainChildSpec(name=Identifier("Wire")),
            ChainChildSpec(name=Identifier("Check")),
        ),
    )
    inst = _make_instance(
        rails=(
            _two_leg("PaymentRail"),
            _two_leg("ACH"),
            _two_leg("Wire"),
            _two_leg("Check"),
        ),
        chains=(chain,),
    )
    edges = derive_column_contracts(inst).chain_edges
    assert len(edges) == 3
    for edge in edges:
        assert edge.is_singleton is False
        assert edge.predicates == ()
        # Composite key uses SORTED child CSV.
        assert edge.editor_path == "/l2_shape/chain/PaymentRail::ACH,Check,Wire/edit"
    assert {e.child for e in edges} == {
        Identifier("ACH"), Identifier("Wire"), Identifier("Check"),
    }


def test_fan_in_child_carries_through_to_contract() -> None:
    """AB.6: per-child fan_in + expected_parent_count surface on the edge
    contract so BT.4 can render N:1 expectations distinctly from 1:1."""
    chain = Chain(
        parent=Identifier("OrderRail"),
        children=(
            ChainChildSpec(
                name=Identifier("BatchedPayout"),
                fan_in=True,
                expected_parent_count=5,
            ),
        ),
    )
    inst = _make_instance(
        rails=(_two_leg("OrderRail"), _two_leg("BatchedPayout")),
        chains=(chain,),
    )
    edge = derive_column_contracts(inst).chain_edges[0]
    assert edge.fan_in is True
    assert edge.expected_parent_count == 5


# -- LimitSchedule contracts -------------------------------------------------


def test_limit_schedule_emits_one_contract_per_row_with_composite_editor_path() -> None:
    limit = LimitSchedule(
        parent_role=Identifier("Customer"),
        rail=RailName("ACH"),
        cap=Decimal("10000"),
        direction="Outbound",
    )
    inst = _make_instance(limit_schedules=(limit,))
    contracts = derive_column_contracts(inst).limits
    assert contracts == (
        LimitContract(
            parent_role=Identifier("Customer"),
            rail=RailName("ACH"),
            direction="Outbound",
            cap=Decimal("10000"),
            editor_path="/l2_shape/limit_schedule/Customer::ACH::Outbound/edit",
        ),
    )


def test_limit_schedule_inbound_direction_renders_in_composite_path() -> None:
    """AB.1 carry-over: same (parent_role, rail) may carry both Outbound +
    Inbound caps; the direction segment of the composite path keeps them
    addressable as distinct editor rows."""
    inbound = LimitSchedule(
        parent_role=Identifier("Customer"),
        rail=RailName("ACH"),
        cap=Decimal("25000"),
        direction="Inbound",
    )
    outbound = LimitSchedule(
        parent_role=Identifier("Customer"),
        rail=RailName("ACH"),
        cap=Decimal("10000"),
        direction="Outbound",
    )
    inst = _make_instance(limit_schedules=(inbound, outbound))
    edits = [c.editor_path for c in derive_column_contracts(inst).limits]
    assert edits == [
        "/l2_shape/limit_schedule/Customer::ACH::Inbound/edit",
        "/l2_shape/limit_schedule/Customer::ACH::Outbound/edit",
    ]


# -- Composition + determinism -----------------------------------------------


def test_combined_instance_composes_all_four_contract_kinds() -> None:
    """Spec-style instance: 2 rails (one TwoLeg, one Variable single-leg),
    1 template binding the variable closer, 1 singleton chain, 1 limit."""
    rail_open = _two_leg(
        "OpenLeg",
        metadata_keys=(Identifier("merchant_id"),),
    )
    rail_close = _single_leg(
        "CloseLeg",
        leg_direction="Variable",
        metadata_keys=(Identifier("merchant_id"),),
    )
    template = TransferTemplate(
        name=Identifier("MerchantCycle"),
        expected_net=Decimal("0"),
        transfer_key=(Identifier("merchant_id"),),
        completion="business_day_end",
        leg_rails=(Identifier("OpenLeg"), Identifier("CloseLeg")),
    )
    chain = Chain(
        parent=Identifier("OpenLeg"),
        children=(ChainChildSpec(name=Identifier("MerchantCycle")),),
    )
    limit = LimitSchedule(
        parent_role=Identifier("A"),
        rail=RailName("OpenLeg"),
        cap=Decimal("50000"),
        direction="Outbound",
    )
    inst = _make_instance(
        rails=(rail_open, rail_close),
        transfer_templates=(template,),
        chains=(chain,),
        limit_schedules=(limit,),
    )
    contracts = derive_column_contracts(inst)
    assert len(contracts.rails) == 2
    assert len(contracts.templates) == 1
    assert len(contracts.chain_edges) == 1
    assert len(contracts.limits) == 1
    # Rail contracts in instance order (OpenLeg first, CloseLeg second).
    assert [r.rail_name for r in contracts.rails] == [
        Identifier("OpenLeg"), Identifier("CloseLeg"),
    ]
    # CloseLeg is Variable-direction — no direction predicate.
    close = contracts.rails[1]
    assert all(p.column != "amount_direction" for p in close.predicates)
    # Chain edge is the singleton-required shape.
    edge = contracts.chain_edges[0]
    assert edge.is_singleton is True
    assert any(p.column == "transfer_parent_id" for p in edge.predicates)


def test_derivation_is_deterministic_across_repeated_calls() -> None:
    """Same instance → byte-identical ColumnContracts. Frozen + slotted +
    tuple-of-tuple shape keeps equality stable across hash-randomness."""
    rail = _two_leg("R", metadata_keys=(Identifier("k1"), Identifier("k2")))
    inst = _make_instance(rails=(rail,))
    a = derive_column_contracts(inst)
    b = derive_column_contracts(inst)
    assert a == b


def test_rail_contract_iteration_order_matches_l2_instance_order() -> None:
    """Tests asserting on rail[0] / rail[N] expect declaration-order
    iteration, not any sorted view."""
    rails = (
        _two_leg("Zeta"),
        _two_leg("Alpha"),
        _two_leg("Mu"),
    )
    inst = _make_instance(rails=rails)
    contracts = derive_column_contracts(inst)
    assert [r.rail_name for r in contracts.rails] == [
        Identifier("Zeta"), Identifier("Alpha"), Identifier("Mu"),
    ]


# -- Editor-path symmetry guard ----------------------------------------------


def test_chain_editor_path_matches_studio_editor_routes_composite_shape() -> None:
    """The composite chain entity_id must match `_studio_editor_routes._entity_id`'s
    shape (parent + '::' + sorted-children-csv). If editor route addressing
    changes, this test alerts so contract derivations don't drift to dead URLs."""
    from recon_gen.common.html._studio_editor_routes import _entity_id

    chain = Chain(
        parent=Identifier("PaymentRail"),
        children=(
            ChainChildSpec(name=Identifier("Zebra")),
            ChainChildSpec(name=Identifier("Apple")),
            ChainChildSpec(name=Identifier("Mango")),
        ),
    )
    expected_entity_id = _entity_id("chain", chain)
    inst = _make_instance(
        rails=(
            _two_leg("PaymentRail"),
            _two_leg("Zebra"),
            _two_leg("Apple"),
            _two_leg("Mango"),
        ),
        chains=(chain,),
    )
    edge = derive_column_contracts(inst).chain_edges[0]
    assert edge.editor_path == f"/l2_shape/chain/{expected_entity_id}/edit"


def test_limit_editor_path_matches_studio_editor_routes_composite_shape() -> None:
    from recon_gen.common.html._studio_editor_routes import _entity_id

    limit = LimitSchedule(
        parent_role=Identifier("Customer"),
        rail=RailName("ACH"),
        cap=Decimal("10000"),
        direction="Inbound",
    )
    expected_entity_id = _entity_id("limit_schedule", limit)
    inst = _make_instance(limit_schedules=(limit,))
    lc = derive_column_contracts(inst).limits[0]
    assert lc.editor_path == f"/l2_shape/limit_schedule/{expected_entity_id}/edit"


# -- ChainEdgeContract surface guard ------------------------------------------


def test_chain_edge_contract_is_frozen_and_hashable() -> None:
    """ChainEdgeContract is frozen + slotted; same input gives equal +
    hashable instances. Pinning so BT.4 can use them as dict keys."""
    contract = ChainEdgeContract(
        parent=Identifier("P"),
        child=Identifier("C"),
        is_singleton=True,
        fan_in=False,
        expected_parent_count=None,
        predicates=(),
        editor_path="/l2_shape/chain/P::C/edit",
    )
    s = {contract}
    assert contract in s


def test_rail_contract_kind_discriminator_pyright_friendly() -> None:
    """leg_kind discriminator narrows: 'two_leg' for TwoLegRail, 'single_leg'
    for SingleLegRail. Pin so any future addition of a third rail kind
    fails this gate first."""
    inst = _make_instance(rails=(_two_leg("T"), _single_leg("S")))
    contracts = derive_column_contracts(inst)
    rail_kinds: dict[str, set[str]] = {
        "two_leg": {str(c.rail_name) for c in contracts.rails if c.leg_kind == "two_leg"},
        "single_leg": {
            str(c.rail_name) for c in contracts.rails if c.leg_kind == "single_leg"
        },
    }
    assert rail_kinds == {"two_leg": {"T"}, "single_leg": {"S"}}


# -- RailContract type discriminator passthrough ------------------------------


def test_rail_contract_is_emitted_for_every_declared_rail_regardless_of_metadata() -> None:
    """Even a metadata-key-empty / posted-requirements-empty rail emits its
    bare contract (selector + role predicate). Absence of metadata predicates
    isn't a reason to drop the contract."""
    inst = _make_instance(
        rails=(_two_leg("A"), _two_leg("B"), _two_leg("C")),
    )
    contracts = derive_column_contracts(inst)
    assert len(contracts.rails) == 3
    for rc in contracts.rails:
        assert isinstance(rc, RailContract)
        assert rc.selector.column == "rail_name"


def test_template_contract_is_emitted_for_every_declared_template() -> None:
    t1 = TransferTemplate(
        name=Identifier("T1"),
        expected_net=Decimal("0"),
        transfer_key=(),
        completion="business_day_end",
        leg_rails=(Identifier("L"),),
    )
    t2 = TransferTemplate(
        name=Identifier("T2"),
        expected_net=Decimal("0"),
        transfer_key=(),
        completion="business_day_end",
        leg_rails=(Identifier("L"),),
    )
    inst = _make_instance(
        rails=(_single_leg("L"),),
        transfer_templates=(t1, t2),
    )
    contracts = derive_column_contracts(inst)
    assert [t.template_name for t in contracts.templates] == [
        Identifier("T1"), Identifier("T2"),
    ]
    for tc in contracts.templates:
        assert isinstance(tc, TemplateContract)


# -- Live-fixture smoke (sasquatch_pr) ----------------------------------------


def test_derives_against_sasquatch_pr_without_error_and_returns_nonempty_counts() -> None:
    """Per [[feedback_spec_example_seed_thin_for_validation]]: live-data
    smoke against the sasquatch_pr fixture catches real-shape bugs that
    hand-rolled minimal instances can't surface. Doesn't pin exact counts
    (the L2 evolves); pins that every primitive kind emits at least one
    contract and every editor_path is non-empty + URL-shaped."""
    import functools
    from pathlib import Path

    from recon_gen.common.l2 import load_instance

    yaml_path = Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"

    @functools.cache
    def _instance() -> L2Instance:
        return load_instance(yaml_path)

    contracts = derive_column_contracts(_instance())
    assert len(contracts.rails) > 0
    assert len(contracts.templates) > 0
    assert len(contracts.chain_edges) > 0
    assert len(contracts.limits) > 0
    # Every editor_path starts with the /l2_shape/ prefix (the route mount).
    for rc in contracts.rails:
        assert rc.editor_path.startswith("/l2_shape/rail/")
        assert rc.editor_path.endswith("/edit")
    for tc in contracts.templates:
        assert tc.editor_path.startswith("/l2_shape/transfer_template/")
    for edge in contracts.chain_edges:
        assert edge.editor_path.startswith("/l2_shape/chain/")
    for lc in contracts.limits:
        assert lc.editor_path.startswith("/l2_shape/limit_schedule/")
