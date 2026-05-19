"""Tests for ``common.l2.auto_scenario`` — focused on the scenario
transforms (``filter_scenario_plants`` etc.) rather than the full
``default_scenario_for`` walker, which is exercised end-to-end through
the locked-seed determinism test."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from recon_gen.common.l2.auto_scenario import filter_scenario_plants
from recon_gen.common.l2.primitives import Identifier, Name
from recon_gen.common.l2.seed import (
    DriftPlant,
    FailedTransactionPlant,
    InvFanoutPlant,
    LimitBreachPlant,
    OverdraftPlant,
    RailFiringPlant,
    ScenarioPlant,
    StuckPendingPlant,
    StuckUnbundledPlant,
    SupersessionPlant,
    TemplateInstance,
    TransferTemplatePlant,
)


def _full_scenario() -> ScenarioPlant:
    """Build a minimal ScenarioPlant with one entry of each plant kind.

    Values are deliberately bare — these tests exercise the *filter*,
    not the plant constructors. The helper just walks the tuple fields
    by kind and either keeps or zeros each one.
    """
    return ScenarioPlant(
        template_instances=(
            TemplateInstance(
                template_role=Identifier("CustomerDDA"),
                account_id=Identifier("cust-001"),
                name=Name("Customer 1"),
            ),
        ),
        drift_plants=(
            DriftPlant(
                account_id=Identifier("cust-001"),
                days_ago=5,
                delta_money=Decimal("75.00"),
                rail_name=Identifier("CustomerInboundACH"),
                counter_account_id=Identifier("ext-001"),
            ),
        ),
        overdraft_plants=(
            OverdraftPlant(
                account_id=Identifier("cust-001"),
                days_ago=3,
                money=Decimal("-50.00"),
            ),
        ),
        limit_breach_plants=(
            LimitBreachPlant(
                account_id=Identifier("cust-001"),
                days_ago=2,
                rail_name=Identifier("CustomerOutboundACH"),
                amount=Decimal("18000.00"),
                counter_account_id=Identifier("ext-001"),
            ),
        ),
        stuck_pending_plants=(
            StuckPendingPlant(
                account_id=Identifier("cust-001"),
                days_ago=10,
                rail_name=Identifier("CustomerOutboundACH"),
                amount=Decimal("100.00"),
            ),
        ),
        failed_transaction_plants=(
            FailedTransactionPlant(
                account_id=Identifier("cust-001"),
                days_ago=4,
                rail_name=Identifier("CustomerOutboundACH"),
                amount=Decimal("25.00"),
            ),
        ),
        stuck_unbundled_plants=(
            StuckUnbundledPlant(
                account_id=Identifier("cust-001"),
                days_ago=8,
                rail_name=Identifier("MerchantCardSale"),
                amount=Decimal("250.00"),
            ),
        ),
        supersession_plants=(
            SupersessionPlant(
                account_id=Identifier("cust-001"),
                days_ago=1,
                rail_name=Identifier("CustomerOutboundACH"),
                original_amount=Decimal("100.00"),
                corrected_amount=Decimal("90.00"),
            ),
        ),
        transfer_template_plants=(),  # not in PlantKind enum — pass-through
        rail_firing_plants=(),  # not in PlantKind enum — pass-through
        inv_fanout_plants=(),  # not in PlantKind enum — pass-through
        today=date(2030, 1, 1),
    )


def test_filter_with_none_returns_input_unchanged() -> None:
    """``None`` ⇒ all kinds (locked-seed default; SPEC's
    "absent / empty = all kinds")."""
    base = _full_scenario()
    out = filter_scenario_plants(base, None)
    assert out is base  # identity — short-circuit, no copy


def test_filter_with_empty_tuple_returns_input_unchanged() -> None:
    """Empty tuple ⇒ all kinds (same as None per SPEC)."""
    base = _full_scenario()
    out = filter_scenario_plants(base, ())
    assert out is base  # identity — short-circuit, no copy


def test_filter_drift_only_keeps_drift_zeros_others() -> None:
    base = _full_scenario()
    out = filter_scenario_plants(base, ("drift",))
    assert out.drift_plants == base.drift_plants
    assert out.overdraft_plants == ()
    assert out.limit_breach_plants == ()
    assert out.stuck_pending_plants == ()
    assert out.stuck_unbundled_plants == ()
    assert out.supersession_plants == ()


def test_filter_two_kinds_keeps_both() -> None:
    base = _full_scenario()
    out = filter_scenario_plants(base, ("overdraft", "supersession"))
    assert out.drift_plants == ()
    assert out.overdraft_plants == base.overdraft_plants
    assert out.limit_breach_plants == ()
    assert out.stuck_pending_plants == ()
    assert out.stuck_unbundled_plants == ()
    assert out.supersession_plants == base.supersession_plants


def test_filter_passes_through_non_l1_fixtures() -> None:
    """The L2-shape and Investigation fixtures aren't L1 SHOULD
    violations and aren't gated by the plant-toggle UI — they always
    pass through unchanged. Same for ``failed_transaction_plants``
    (X.1.i — Failed-status fixture, not an exception kind), the
    ``template_instances`` (needed by every plant kind that references
    customer accounts), and the reference ``today`` date."""
    base = ScenarioPlant(
        template_instances=(
            TemplateInstance(
                template_role=Identifier("CustomerDDA"),
                account_id=Identifier("cust-001"),
                name=Name("Customer 1"),
            ),
        ),
        failed_transaction_plants=(
            FailedTransactionPlant(
                account_id=Identifier("cust-001"),
                days_ago=4,
                rail_name=Identifier("CustomerOutboundACH"),
                amount=Decimal("25.00"),
            ),
        ),
        transfer_template_plants=(
            TransferTemplatePlant(
                template_name=Identifier("InternalTransferCycle"),
                days_ago=2,
                amount=Decimal("50.00"),
                source_account_id=Identifier("cust-001"),
                destination_account_id=Identifier("cust-002"),
                firing_seq=1,
            ),
        ),
        rail_firing_plants=(
            RailFiringPlant(
                rail_name=Identifier("CustomerInboundACH"),
                days_ago=3,
                firing_seq=1,
                amount=Decimal("100.00"),
                account_id_a=Identifier("ext-001"),
            ),
        ),
        inv_fanout_plants=(
            InvFanoutPlant(
                recipient_account_id=Identifier("cust-001"),
                sender_account_ids=(
                    Identifier("cust-002"),
                    Identifier("cust-003"),
                ),
                days_ago=2,
                rail_name=Identifier("CustomerInboundACH"),
                amount_per_transfer=Decimal("100.00"),
            ),
        ),
        today=date(2030, 1, 1),
    )
    # Filter to a kind that isn't even present — should still pass
    # through every non-L1 fixture intact.
    out = filter_scenario_plants(base, ("drift",))
    assert out.template_instances == base.template_instances
    assert out.failed_transaction_plants == base.failed_transaction_plants
    assert out.transfer_template_plants == base.transfer_template_plants
    assert out.rail_firing_plants == base.rail_firing_plants
    assert out.inv_fanout_plants == base.inv_fanout_plants
    assert out.today == base.today


# -- AB.5 (E7): plant-amount + cap-breach helpers --------------------------


def _ranged_rail(lo: str, hi: str) -> object:
    """Build a minimal TwoLegRail with amount_typical_range set. Only
    the field the helper reads matters here; other rail fields stay at
    their dataclass defaults (the helpers only touch amount_typical_range)."""
    from recon_gen.common.l2.primitives import Money, TwoLegRail
    return TwoLegRail(
        name=Identifier("R"),
        metadata_keys=(Identifier("transfer_id"),),
        source_role=(Identifier("src"),),
        destination_role=(Identifier("dst"),),
        amount_typical_range=(Money(Decimal(lo)), Money(Decimal(hi))),
    )


def test_plant_amount_for_rail_uses_midpoint_when_range_set() -> None:
    """AB.5: a rail with declared range returns the cents-quantized
    midpoint regardless of the caller's default — keeps plants sized
    like ordinary firings on that rail."""
    from recon_gen.common.l2.auto_scenario import _plant_amount_for_rail

    rail = _ranged_rail("50", "5000")  # midpoint = 2525.00
    got = _plant_amount_for_rail(rail, Decimal("999.99"))
    assert got == Decimal("2525.00")


def test_plant_amount_for_rail_returns_default_when_range_unset() -> None:
    """Pre-AB.5 fixtures (no range): legacy hardcoded default preserved
    byte-equivalent."""
    from recon_gen.common.l2.auto_scenario import _plant_amount_for_rail

    got = _plant_amount_for_rail(None, Decimal("100.00"))
    assert got == Decimal("100.00")


def test_cap_breach_amount_unbounded_falls_through_to_cap_times_1_5() -> None:
    """Pre-AB.5: no range → plain ``cap * 1.5`` whole-dollar quantized."""
    from recon_gen.common.l2.auto_scenario import _cap_breach_amount

    # cap=1000, no range → 1500
    assert _cap_breach_amount(Decimal("1000"), None) == Decimal("1500")


def test_cap_breach_amount_clamps_to_range_max_times_3() -> None:
    """AB.5: when ``cap * 1.5`` exceeds ``range.max * 3``, the breach
    pins to ``range.max * 3`` so it stays in a realistic ballpark
    relative to the rail's typical volume (rather than blowing past
    the typical band by an absurd multiplier)."""
    from recon_gen.common.l2.auto_scenario import _cap_breach_amount

    # cap=100000, range=[5, 500] → cap*1.5=150000 vs range.max*3=1500
    # → clamped to 1500.
    rail = _ranged_rail("5", "500")
    assert _cap_breach_amount(Decimal("100000"), rail) == Decimal("1500")


def test_cap_breach_amount_uses_cap_when_below_range_cap() -> None:
    """AB.5: when ``cap * 1.5`` is BELOW ``range.max * 3``, the breach
    stays at ``cap * 1.5`` — the clamp is a ceiling, not a floor."""
    from recon_gen.common.l2.auto_scenario import _cap_breach_amount

    # cap=100, range=[5, 500] → cap*1.5=150 vs range.max*3=1500
    # → 150 wins (it's the smaller).
    rail = _ranged_rail("5", "500")
    assert _cap_breach_amount(Decimal("100"), rail) == Decimal("150")


# -- AB.6.6 _pick_multi_xor_chain_inputs picker contracts -------------------


def test_pick_multi_xor_chain_inputs_finds_eligible_chain() -> None:
    """AB.6.6: picker returns (parent, child_a, child_b) for the first
    chain (in deterministic order) with a Rail parent + ≥2 non-fan_in
    children. spec_example's BulkAccrualSettlement chain qualifies."""
    from pathlib import Path
    from recon_gen.common.l2.auto_scenario import _pick_multi_xor_chain_inputs
    from recon_gen.common.l2.loader import load_instance

    fx = Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"
    inst = load_instance(fx)
    pick = _pick_multi_xor_chain_inputs(inst)
    assert pick is not None, "AB.6.5.spec should land an eligible chain"
    parent, child_a, child_b = pick
    assert str(parent) == "BulkAccrualSettlement"
    # Children traversed in declared order; first two non-fan_in entries win.
    assert {str(child_a), str(child_b)} == {
        "BulkAccrualSettleACH", "BulkAccrualSettleWire",
    }


def test_pick_multi_xor_chain_inputs_skips_template_parent() -> None:
    """AB.6.6: picker restricts to Rail parents (mirrors AB.2.6 /
    AB.4.5). spec_example's ReconciliationLeg → MerchantSettlementCycle
    chain has a template parent — the picker MUST NOT return it.
    Sasquatch's MerchantSettlementCycle chain also has a template
    parent (the template itself), so it's also skipped.

    Verified by inspecting the pick: must be a Rail-parented chain.
    """
    from pathlib import Path
    from recon_gen.common.l2.auto_scenario import _pick_multi_xor_chain_inputs
    from recon_gen.common.l2.loader import load_instance

    fx = (
        Path(__file__).resolve().parent.parent / "l2" / "sasquatch_pr.yaml"
    )
    inst = load_instance(fx)
    pick = _pick_multi_xor_chain_inputs(inst)
    # sasquatch_pr's MerchantSettlementCycle chain is mixed-cardinality
    # but its parent is a TransferTemplate (the template itself). The
    # picker should skip it. sasquatch carries no Rail-parented
    # multi-XOR chain, so the picker returns None.
    if pick is None:
        return
    parent, _, _ = pick
    rail_names = {r.name for r in inst.rails}
    assert parent in rail_names, (
        f"picker returned non-Rail parent {parent!r}; "
        f"AB.6.6 picker MUST gate on Rail parents only"
    )


def test_pick_multi_xor_chain_inputs_skips_per_child_fan_in_entries() -> None:
    """AB.6.6 + AB.5 coupling: when a chain has both fan_in and
    non-fan_in children, the picker counts only the non-fan_in subset.
    A chain like [ChildA, ChildB(fan_in)] has only 1 non-fan_in →
    skipped. A chain like [ChildA, ChildB, ChildC(fan_in)] has 2
    non-fan_in → qualifies (returns A, B).
    """
    from recon_gen.common.l2.auto_scenario import _pick_multi_xor_chain_inputs
    from recon_gen.common.l2.primitives import (
        Account,
        Chain,
        ChainChildSpec,
        Identifier,
        L2Instance,
        LegDirection,
        Money,
        Name,
        SingleLegRail,
        TransferTemplate,
    )

    parent_rail = SingleLegRail(
        name=Identifier("Trigger"),
        metadata_keys=(),
        leg_role=Identifier("ParentRole"),
        leg_direction="Credit",
        origin="InternalInitiated",
    )
    a_rail = SingleLegRail(
        name=Identifier("ChildA"),
        metadata_keys=(),
        leg_role=Identifier("ParentRole"),
        leg_direction="Credit",
        origin="InternalInitiated",
    )
    b_rail = SingleLegRail(
        name=Identifier("ChildB"),
        metadata_keys=(),
        leg_role=Identifier("ParentRole"),
        leg_direction="Credit",
        origin="InternalInitiated",
    )
    fan_in_tpl = TransferTemplate(
        name=Identifier("FanInChild"),
        expected_net=Money(Decimal("0")),
        transfer_key=(Identifier("batch_id"),),
        completion="business_day_end",
        leg_rails=(Identifier("ChildA"),),
    )
    inst = L2Instance(
        accounts=(
            Account(
                id=Identifier("acct"),
                role=Identifier("ParentRole"),
                scope="internal",
                name=Name("Parent Account"),
            ),
        ),
        account_templates=(),
        rails=(parent_rail, a_rail, b_rail),
        transfer_templates=(fan_in_tpl,),
        chains=(
            Chain(
                parent=Identifier("Trigger"),
                children=(
                    ChainChildSpec(name=Identifier("ChildA")),
                    ChainChildSpec(name=Identifier("ChildB")),
                    ChainChildSpec(
                        name=Identifier("FanInChild"),
                        fan_in=True,
                        expected_parent_count=3,
                    ),
                ),
            ),
        ),
        limit_schedules=(),
    )
    pick = _pick_multi_xor_chain_inputs(inst)
    assert pick is not None
    parent, child_a, child_b = pick
    assert str(parent) == "Trigger"
    # Fan_in child is SKIPPED — picker returns the 2 non-fan_in children.
    assert {str(child_a), str(child_b)} == {"ChildA", "ChildB"}


def test_pick_multi_xor_chain_inputs_returns_none_for_singleton_chain() -> None:
    """AB.6.6: singleton-children chains (Z.A 'required' semantics)
    don't qualify — only multi-XOR chains do. A chain with 1 non-fan_in
    child returns None even if the chain has other (fan_in) entries.
    """
    from recon_gen.common.l2.auto_scenario import _pick_multi_xor_chain_inputs
    from recon_gen.common.l2.primitives import (
        Account,
        Chain,
        ChainChildSpec,
        Identifier,
        L2Instance,
        Money,
        Name,
        SingleLegRail,
    )

    rail = SingleLegRail(
        name=Identifier("P"),
        metadata_keys=(),
        leg_role=Identifier("R"),
        leg_direction="Credit",
        origin="InternalInitiated",
    )
    rail_c = SingleLegRail(
        name=Identifier("C"),
        metadata_keys=(),
        leg_role=Identifier("R"),
        leg_direction="Credit",
        origin="InternalInitiated",
    )
    inst = L2Instance(
        accounts=(
            Account(
                id=Identifier("a"), role=Identifier("R"), scope="internal",
                name=Name("Acct"),
            ),
        ),
        account_templates=(),
        rails=(rail, rail_c),
        transfer_templates=(),
        chains=(
            Chain(
                parent=Identifier("P"),
                children=(ChainChildSpec(name=Identifier("C")),),
            ),
        ),
        limit_schedules=(),
    )
    assert _pick_multi_xor_chain_inputs(inst) is None
