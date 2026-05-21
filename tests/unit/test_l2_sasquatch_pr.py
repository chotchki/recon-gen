"""Smoke + structural tests for ``tests/l2/sasquatch_pr.yaml`` (M.3.1).

The canonical Sasquatch L2 instance — replaces sasquatch_ar.yaml in
M.3.2. This test suite asserts:

1. Loads + cross-entity validates clean (24 SPEC validation rules).
2. Top-level shape is pinned (account / template / rail counts).
3. **Encompasses sasquatch_ar's primitive coverage** — the central
   M.3.1 acceptance criterion. Walks the AR primitive catalog and
   asserts each is present in the PR YAML, so M.3.2's deletion of
   sasquatch_ar doesn't lose test coverage.
4. PR-specific primitives are present (TransferKey grouping, XOR
   `PayoutVehicle` chain, external card-network aggregating rail).
5. Every primitive carries a description (M.1a.6 + M.2a.7 prose
   seam — handbook + dashboard text boxes consume them).

Behavioral correctness (does drift / overdraft / limit-breach
actually surface on the deployed dashboard?) lives in M.4.1's
end-to-end harness against real Postgres + QuickSight.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from recon_gen.common.l2 import (
    L2Instance,
    SingleLegRail,
    TwoLegRail,
    load_instance,
    validate,
)


YAML_PATH = Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"


@functools.cache
def _instance() -> L2Instance:
    """Cached load — tests share one in-memory instance."""
    return load_instance(YAML_PATH)


# -- Loads + validates ------------------------------------------------------


def test_loads_and_validates_cleanly() -> None:
    """The fixture passes the full validator suite (every U/R/C/S/V/O rule)."""
    inst = _instance()
    validate(inst)


def test_top_level_description_present() -> None:
    """Top-level instance prose powers the M.7 handbook overview page."""
    inst = _instance()
    assert inst.description is not None
    assert "Sasquatch National Bank" in inst.description


# -- Pinned top-level shape -------------------------------------------------


def test_account_counts_pinned() -> None:
    """Concrete count guards against accidental drift in the fixture."""
    inst = _instance()
    internal = [a for a in inst.accounts if a.scope == "internal"]
    external = [a for a in inst.accounts if a.scope == "external"]
    assert len(internal) == 10, (
        f"sasquatch_pr should declare 10 internal singletons; "
        f"got {len(internal)}"
    )
    assert len(external) == 6, (
        f"sasquatch_pr should declare 6 external counterparties; "
        f"got {len(external)}"
    )


def test_template_counts_pinned() -> None:
    inst = _instance()
    assert len(inst.account_templates) == 3, (
        "expected: CustomerDDA, MerchantDDA, ZBASubAccount"
    )
    template_roles = {str(t.role) for t in inst.account_templates}
    assert template_roles == {"CustomerDDA", "MerchantDDA", "ZBASubAccount"}


def test_rail_counts_pinned() -> None:
    inst = _instance()
    # AB.3.6 (2026-05-19): +6 Variable-direction SingleLegRails for the
    # MerchantSettlementCycle XOR groups (settlement-timing trio +
    # fraud-review trio). Two-leg count unchanged; single_leg jumps
    # 6 → 12.
    # AB.4.6 (2026-05-19): +2 rails for the fan-in batch payout
    # (MerchantDailySettleAggregator two-leg + MerchantWeeklyBatchClose
    # single-leg). Total: 27 + 2 = 29; two_leg 15 → 16; single_leg
    # 12 → 13.
    # AJ.4b (2026-05-20): +1 two-leg label-only rail
    # (InternalBalanceMaintenance) carrying cascade/opening scaffolding.
    # Total 29 → 30; two_leg 16 → 17; single_leg unchanged.
    assert len(inst.rails) == 30
    two_leg = [r for r in inst.rails if isinstance(r, TwoLegRail)]
    single_leg = [r for r in inst.rails if isinstance(r, SingleLegRail)]
    aggregating = [r for r in inst.rails if r.aggregating]
    assert len(two_leg) == 17
    assert len(single_leg) == 13
    assert len(aggregating) == 3


def test_transfer_template_counts_pinned() -> None:
    inst = _instance()
    # AB.4.6 (2026-05-19): +1 template (MerchantWeeklyPayoutBatch)
    # for the fan-in batch payout demo. Total 2 → 3.
    assert len(inst.transfer_templates) == 3
    template_names = {str(t.name) for t in inst.transfer_templates}
    assert template_names == {
        "InternalTransferCycle",
        "MerchantSettlementCycle",
        "MerchantWeeklyPayoutBatch",
    }


def test_chain_counts_pinned() -> None:
    inst = _instance()
    # Z.A grammar: 1 singleton-children row (ACH→FRB sweep, required)
    # + 1 multi-children row (ACH return reasons, XOR) + 1 multi
    # (merchant payout vehicles, XOR) + 1 AB.2 template-as-child
    # (CustomerFeeAccrual → InternalTransferCycle) + 1 AB.4 fan-in
    # chain (MerchantDailySettleAggregator → MerchantWeeklyPayoutBatch).
    assert len(inst.chains) == 5
    # AB.2 — at least one chain has a TransferTemplate as its singleton child.
    template_names = {t.name for t in inst.transfer_templates}
    assert any(
        len(c.children) == 1 and c.children[0].name in template_names
        for c in inst.chains
    ), "AB.2 expected: sasquatch_pr carries at least one template-as-chain-child"


def test_limit_schedule_counts_pinned() -> None:
    inst = _instance()
    # 6 Outbound (the legacy AR + PR payout caps) + 1 Inbound
    # (CustomerInboundACH AML threshold, AB.1.6 2026-05-19).
    assert len(inst.limit_schedules) == 7
    outbound = [ls for ls in inst.limit_schedules if ls.direction == "Outbound"]
    inbound = [ls for ls in inst.limit_schedules if ls.direction == "Inbound"]
    assert len(outbound) == 6
    assert len(inbound) == 1


# -- Encompasses sasquatch_ar's primitive coverage -------------------------
#
# This is the central M.3.1 acceptance criterion. Each test below pins
# a category of primitive coverage that sasquatch_ar exercised. M.3.2
# deletes sasquatch_ar — so any AR-coverage hole opened by future PR
# YAML edits surfaces here, not silently in CI.


def test_encompasses_ar_transfer_type_families_via_rail_names() -> None:
    """All AR transfer-type families must remain represented in PR's rail set.

    Z.B (2026-05-15): Rail.name IS the type identifier under the
    symmetric collapse. The semantic coverage check now scans rail
    names for each AR family substring (case-insensitive) — rail
    names like ``CustomerOutboundACH`` cover the ``ach`` family,
    ``CustomerCashWithdrawal`` covers ``cash``, etc.
    """
    inst = _instance()
    declared_lower = {str(r.name).lower() for r in inst.rails}
    ar_families = ["ach", "wire", "cash", "internal", "fee", "settlement", "return"]
    missing_families = [
        fam for fam in ar_families
        if not any(fam in n for n in declared_lower)
    ]
    assert not missing_families, (
        f"sasquatch_pr must encompass sasquatch_ar's transfer-type families "
        f"(via rail-name substring match under Z.B); "
        f"missing: {sorted(missing_families)!r}; declared: {sorted(declared_lower)!r}"
    )


def test_encompasses_ar_variable_closure_template() -> None:
    """AR's `InternalTransferCycle` shape (3-leg with Variable-direction
    closing leg) must remain — exercises the SPEC's hardest TransferTemplate
    primitive."""
    inst = _instance()
    cycles = [t for t in inst.transfer_templates
              if str(t.name) == "InternalTransferCycle"]
    assert len(cycles) == 1, (
        "InternalTransferCycle template must be present (AR-encompassment)"
    )
    cycle = cycles[0]
    assert len(cycle.leg_rails) >= 3, (
        "InternalTransferCycle must be ≥3-leg"
    )
    # And one of those legs must be a Variable-direction single-leg rail.
    rails_by_name = {str(r.name): r for r in inst.rails}
    variable_legs = [
        rails_by_name[str(n)] for n in cycle.leg_rails
        if isinstance(rails_by_name.get(str(n)), SingleLegRail)
        and rails_by_name[str(n)].leg_direction == "Variable"  # type: ignore[union-attr]: narrowed by the prior isinstance(..., SingleLegRail) check
    ]
    assert len(variable_legs) == 1, (
        f"InternalTransferCycle must contain exactly one Variable-direction "
        f"leg; found {len(variable_legs)}"
    )


def test_encompasses_ar_inbound_outbound_external_rails() -> None:
    """AR exercised both inbound (external-source → internal-dest) and
    outbound (internal-source → external-dest) two-leg rails. PR must too."""
    inst = _instance()
    external_roles = {
        str(a.role) for a in inst.accounts if a.scope == "external"
    }
    inbound_count = 0
    outbound_count = 0
    for r in inst.rails:
        if not isinstance(r, TwoLegRail):
            continue
        # Inbound: source role lives in some external account.
        if any(role in external_roles for role in r.source_role):
            inbound_count += 1
        # Outbound: destination role lives in some external account.
        if any(role in external_roles for role in r.destination_role):
            outbound_count += 1
    assert inbound_count >= 3, (
        f"sasquatch_pr must declare ≥3 inbound 2-leg rails (AR had several); "
        f"got {inbound_count}"
    )
    assert outbound_count >= 3, (
        f"sasquatch_pr must declare ≥3 outbound 2-leg rails (AR had several); "
        f"got {outbound_count}"
    )


def test_encompasses_ar_aggregating_with_max_unbundled_age() -> None:
    """AR's `CustomerFeeAccrual` is bundled by `CustomerFeeMonthlySettlement`
    AND carries `max_unbundled_age` — the canonical R8-satisfying pattern.
    PR must keep this pattern (it surfaces stuck-unbundled exceptions on the
    L1 dashboard)."""
    inst = _instance()
    fee = next(
        (r for r in inst.rails if str(r.name) == "CustomerFeeAccrual"),
        None,
    )
    assert fee is not None, "CustomerFeeAccrual rail must remain (AR-encompassment)"
    assert fee.max_unbundled_age is not None, (
        "CustomerFeeAccrual must carry max_unbundled_age"
    )
    # Check there's an aggregating rail bundling it (R8 satisfaction).
    bundlers = [
        r for r in inst.rails
        if r.aggregating and "CustomerFeeAccrual" in r.bundles_activity
    ]
    assert bundlers, (
        "CustomerFeeAccrual must be bundled by some aggregating rail "
        "(satisfies R8 + matches AR's CustomerFeeMonthlySettlement pattern)"
    )


def test_encompasses_ar_limit_schedule_coverage() -> None:
    """AR's three DDAControl × {ACH, wire, cash} caps must remain (the L1
    Limit Breach invariant against customer-DDA outbound flow).

    Z.B (2026-05-15): Caps now reference rail names directly under the
    symmetric collapse (the rail's ``name`` IS the type identifier).
    """
    inst = _instance()
    pairs = {(str(ls.parent_role), str(ls.rail)) for ls in inst.limit_schedules}
    ar_required = {
        ("DDAControl", "CustomerOutboundACH"),
        ("DDAControl", "CustomerOutboundWire"),
        ("DDAControl", "CustomerCashWithdrawal"),
    }
    missing = ar_required - pairs
    assert not missing, (
        f"sasquatch_pr must keep AR's DDAControl outbound-cap LimitSchedules; "
        f"missing pairs: {sorted(missing)!r}; declared: {sorted(pairs)!r}"
    )


def test_encompasses_ar_per_leg_origin_overrides() -> None:
    """AR exercised TwoLegRails with per-leg origin overrides
    (source_origin + destination_origin set). PR must too."""
    inst = _instance()
    per_leg = [
        r for r in inst.rails
        if isinstance(r, TwoLegRail)
        and r.source_origin is not None
        and r.destination_origin is not None
    ]
    assert per_leg, (
        "sasquatch_pr must declare ≥1 TwoLegRail with both per-leg origin "
        "overrides set (AR-encompassment + O1 surface)"
    )


def test_encompasses_ar_union_leg_role_rail() -> None:
    """AR's `CustomerFeeAccrual` exercised the union-leg-role pattern
    (`leg_role: [CustomerDDA, InternalSuspenseRecon]`). PR keeps it."""
    inst = _instance()
    union_legged = [
        r for r in inst.rails
        if isinstance(r, SingleLegRail) and len(r.leg_role) >= 2
    ]
    assert union_legged, (
        "sasquatch_pr must declare ≥1 SingleLegRail with a union leg_role "
        "(AR-encompassment + R1 union-resolution surface)"
    )


# -- PR-specific primitives -------------------------------------------------


def test_pr_transfer_key_grouping_template_present() -> None:
    """PR adds `MerchantSettlementCycle` — TransferKey-grouped template
    where multiple rail firings join one shared Transfer."""
    inst = _instance()
    msc = next(
        (t for t in inst.transfer_templates
         if str(t.name) == "MerchantSettlementCycle"),
        None,
    )
    assert msc is not None
    assert len(msc.transfer_key) >= 2, (
        "MerchantSettlementCycle must have a multi-key TransferKey "
        "(merchant_id + settlement_period) for the M.3.3 grouping test"
    )
    assert "merchant_id" in msc.transfer_key
    assert "settlement_period" in msc.transfer_key


def test_pr_payout_vehicle_xor_chain_present() -> None:
    """PR adds the merchant payout XOR chain — three children under
    MerchantSettlementCycle in a single Z.A multi-children Chain row."""
    inst = _instance()
    # Locate the multi-children chain under MerchantSettlementCycle
    # whose children are the three payout vehicles.
    payout_chain = next(
        (
            c for c in inst.chains
            if str(c.parent) == "MerchantSettlementCycle"
            and len(c.children) >= 2
        ),
        None,
    )
    assert payout_chain is not None, (
        "Expected a multi-children Chain row under MerchantSettlementCycle"
    )
    # AB.6.6.sasq folded MerchantWeeklyPayoutBatch (fan_in) into this
    # chain as a 4th child — mixed-cardinality demo. The XOR-vehicle
    # alternation is still the three non-fan_in children.
    xor_children = {
        str(ch.name) for ch in payout_chain.children if not ch.fan_in
    }
    assert xor_children == {
        "MerchantPayoutACH", "MerchantPayoutWire", "MerchantPayoutCheck",
    }


def test_pr_external_aggregating_rail_present() -> None:
    """PR's `ExternalCardSettlement` is a single-leg aggregating rail
    that bundles MerchantCardSale activity. New shape vs AR's two-leg
    aggregator."""
    inst = _instance()
    ecs = next(
        (r for r in inst.rails if str(r.name) == "ExternalCardSettlement"),
        None,
    )
    assert ecs is not None
    assert ecs.aggregating
    assert ecs.cadence is not None
    assert "MerchantCardSale" in ecs.bundles_activity
    assert isinstance(ecs, SingleLegRail), (
        "ExternalCardSettlement is single-leg per the SPEC's "
        "'sweep that lands in External' pattern"
    )


# -- Description coverage (prose seam) -------------------------------------


def test_every_primitive_has_a_description() -> None:
    """M.1a.6 + M.2a.7: descriptions feed handbook + dashboard text
    boxes. Empty fields silently bypass the prose seam — guard against."""
    inst = _instance()
    missing: list[str] = []
    for a in inst.accounts:
        if not a.description:
            missing.append(f"account {a.id!r}")
    for t in inst.account_templates:
        if not t.description:
            missing.append(f"template role {t.role!r}")
    for r in inst.rails:
        if not r.description:
            missing.append(f"rail {r.name!r}")
    for tt in inst.transfer_templates:
        if not tt.description:
            missing.append(f"transfer_template {tt.name!r}")
    for c in inst.chains:
        if not c.description:
            children_str = ",".join(str(ch.name) for ch in c.children)
            missing.append(f"chain {c.parent}->[{children_str}]")
    for ls in inst.limit_schedules:
        if not ls.description:
            missing.append(
                f"limit_schedule ({ls.parent_role}, {ls.rail})"
            )
    assert not missing, (
        f"Primitives without description (breaks the prose seam):\n"
        + "\n".join(f"  - {m}" for m in missing)
    )
