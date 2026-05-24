"""AY.4.c.3 — `scenario_to_generators(plants, instance, anchor)`.

Walks a `ScenarioPlant` (the OLD per-plant-kind aggregate from
`common/l2/seed.py`) and materializes one spine `ViolationGenerator`
per plant. The AY.4.d `build_full_seed_sql` rewrite composes the
returned tuple via `ScenarioContext.compose(dry_run=True)` + the
AY.4.b renderer to produce the production seed SQL.

## Mapping table — one branch per plant kind

| Plant                            | Generator                          | Notes                                              |
|----------------------------------|------------------------------------|----------------------------------------------------|
| DriftPlant                       | DriftGenerator                     | factory + child_account_id override                |
| OverdraftPlant                   | OverdraftGenerator                 | factory + account_id override                      |
| LimitBreachPlant                 | LimitBreachGenerator (Outbound)    | factory + account_id override                      |
| InboundCapBreachPlant            | LimitBreachGenerator (Inbound)     | factory + account_id override                      |
| StuckPendingPlant                | StuckPendingGenerator              | factory + account_id override                      |
| StuckUnbundledPlant              | StuckUnbundledGenerator            | factory + account_id override                      |
| FailedTransactionPlant           | FailedTransactionGenerator         | direct construct (no factory yet)                  |
| SupersessionPlant                | SupersessionGenerator              | direct construct                                   |
| ChainParentDisagreementPlant     | ChainParentDisagreementGenerator   | direct + account_id_override                       |
| TwoTemplateChainPlant            | TwoTemplateChainGenerator          | direct + L2 resolution + account_id_override       |
| XorVariantMissedFiringPlant      | XorGroupMissedFiringGenerator      | direct + account_id_override                       |
| XorVariantOverlapPlant           | XorGroupOverlapGenerator           | direct + account_id_override                       |
| FanInChainPlant (healthy)        | FanInChainGenerator(healthy)       | direct + account_id_override                       |
| FanInChainMissingParentPlant     | FanInChainGenerator(missing)       | direct + account_id_override                       |
| FanInChainExtraParentPlant       | FanInChainGenerator(extra)         | direct + account_id_override                       |
| MultiXorMissedPlant              | MultiXorMissedGenerator            | direct + account_id_override                       |
| MultiXorOverlapPlant             | MultiXorOverlapGenerator           | direct + account_id_override                       |
| TransferTemplatePlant            | TransferTemplateGenerator          | direct + L2 resolution (kind from first leg_rail)  |
| RailFiringPlant                  | RailFiringGenerator                | direct + L2 resolution (rail kind)                 |
| InvFanoutPlant                   | InvFanoutGenerator                 | direct construct                                   |

## What the adapter does NOT do

- **Chain-completion side-effects.** The OLD `_emit_rail_firing_rows`
  + `_emit_transfer_template_rows` plant `_emit_plant_chain_completion`
  child rows so the chain integrity matview doesn't false-positive.
  The spine generators omit these. AY.5's re-lock pass either accepts
  the drift (matview rows differ) or a dedicated `ChainCompletionGenerator`
  lands as a separate concern.
- **`transfer_key` metadata cascade.** The OLD path stamps containing
  TransferTemplate `transfer_key` field values on every leg the
  template's leg_rails fire. Spine generators emit a minimal metadata
  payload. Same drift-acceptance contract.

These omissions are documented in each spine generator module's
"What this does NOT do yet" section + are the source of expected
byte drift at AY.5.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from recon_gen.common.l2.primitives import (
    L2Instance,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)
from recon_gen.common.l2.seed import (
    ChainParentDisagreementPlant,
    DriftPlant,
    FailedTransactionPlant,
    FanInChainExtraParentPlant,
    FanInChainMissingParentPlant,
    FanInChainPlant,
    InboundCapBreachPlant,
    InvFanoutPlant,
    LimitBreachPlant,
    MultiXorMissedPlant,
    MultiXorOverlapPlant,
    OverdraftPlant,
    RailFiringPlant,
    ScenarioPlant,
    StuckPendingPlant,
    StuckUnbundledPlant,
    SupersessionPlant,
    TransferTemplatePlant,
    TwoTemplateChainPlant,
    XorVariantMissedFiringPlant,
    XorVariantOverlapPlant,
)
from recon_gen.common.spine.chain_parent_disagreement import (
    ChainParentDisagreementGenerator,
)
from recon_gen.common.spine.drift import DriftInvariant
from recon_gen.common.spine.failed_transaction import FailedTransactionGenerator
from recon_gen.common.spine.fan_in_disagreement import FanInChainGenerator
from recon_gen.common.spine.generator import ViolationGenerator
from recon_gen.common.spine.inv_fanout import InvFanoutGenerator
from recon_gen.common.spine.limit_breach import LimitBreachInvariant
from recon_gen.common.spine.multi_xor_violation import (
    MultiXorMissedGenerator,
    MultiXorOverlapGenerator,
)
from recon_gen.common.spine.overdraft import OverdraftInvariant
from recon_gen.common.spine.rail_firing import RailFiringGenerator
from recon_gen.common.spine.stuck_pending import StuckPendingInvariant
from recon_gen.common.spine.stuck_unbundled import StuckUnbundledInvariant
from recon_gen.common.spine.supersession import SupersessionGenerator
from recon_gen.common.spine.transfer_template import TransferTemplateGenerator
from recon_gen.common.spine.two_template_chain import TwoTemplateChainGenerator
from recon_gen.common.spine.xor_group_violation import (
    XorGroupMissedFiringGenerator,
    XorGroupOverlapGenerator,
)


def scenario_to_generators(
    scenarios: ScenarioPlant,
    instance: L2Instance,
    *,
    anchor: date | None = None,
    as_of: datetime | None = None,
) -> tuple[ViolationGenerator, ...]:
    """Walk every plant collection on `scenarios` + return the
    matching spine generator per plant. Order matches the OLD
    `emit_seed`'s per-kind dispatch (drift → overdraft → limit →
    inbound cap → two-template → ... → inv-fanout) so debug output
    stays diff-friendly.

    Anchor defaults: `anchor` defaults to `scenarios.today` (the
    plant collection's pinned reference date); `as_of` defaults to
    `anchor` at noon (StuckPending / StuckUnbundled generators need
    a wall-clock).
    """
    anchor_day = anchor if anchor is not None else scenarios.today
    wall_clock = as_of if as_of is not None else datetime(
        anchor_day.year, anchor_day.month, anchor_day.day, 12, 0, 0,
    )

    out: list[ViolationGenerator] = []

    # L1 accounting plants — straightforward 1:1 maps via factories.
    for dp in scenarios.drift_plants:
        out.append(_adapt_drift(dp, instance, scenarios, anchor_day))
    for op in scenarios.overdraft_plants:
        out.append(_adapt_overdraft(op, instance, scenarios, anchor_day))
    for lp in scenarios.limit_breach_plants:
        out.append(_adapt_limit_breach(
            lp, instance, scenarios, anchor_day, direction="Outbound",
        ))
    for icp in scenarios.inbound_cap_breach_plants:
        out.append(_adapt_inbound_cap_breach(
            icp, instance, scenarios, anchor_day,
        ))

    # L2-shape plants — direct construct + L2 resolution.
    for ttp in scenarios.two_template_chain_plants:
        out.append(_adapt_two_template_chain(ttp, instance, anchor_day))
    for cpd in scenarios.chain_parent_disagreement_plants:
        out.append(_adapt_chain_parent_disagreement(cpd, anchor_day))
    for xm in scenarios.xor_variant_missed_firing_plants:
        out.append(_adapt_xor_missed(xm, anchor_day))
    for xo in scenarios.xor_variant_overlap_plants:
        out.append(_adapt_xor_overlap(xo, anchor_day))
    for fp in scenarios.fan_in_chain_plants:
        out.append(_adapt_fan_in_healthy(fp, instance, anchor_day))
    for fmp in scenarios.fan_in_chain_missing_parent_plants:
        out.append(_adapt_fan_in_missing(fmp, instance, anchor_day))
    for fxp in scenarios.fan_in_chain_extra_parent_plants:
        out.append(_adapt_fan_in_extra(fxp, instance, anchor_day))
    for mxm in scenarios.multi_xor_missed_plants:
        out.append(_adapt_multi_xor_missed(mxm, anchor_day))
    for mxo in scenarios.multi_xor_overlap_plants:
        out.append(_adapt_multi_xor_overlap(mxo, anchor_day))

    # Aging plants — need wall-clock `as_of`.
    for sp in scenarios.stuck_pending_plants:
        out.append(_adapt_stuck_pending(sp, instance, scenarios, wall_clock))
    for sup in scenarios.stuck_unbundled_plants:
        out.append(_adapt_stuck_unbundled(sup, instance, scenarios, wall_clock))

    # Audit-fixture plants — direct construct, AuditFixture evidence.
    for ftp in scenarios.failed_transaction_plants:
        out.append(_adapt_failed_transaction(ftp, instance, scenarios, anchor_day))
    for spp in scenarios.supersession_plants:
        out.append(_adapt_supersession(spp, instance, scenarios, anchor_day))

    # Seed-color (broad-mode) plants — CoverageObservation evidence.
    for tp in scenarios.transfer_template_plants:
        out.append(_adapt_transfer_template(tp, instance, anchor_day))
    for rp in scenarios.rail_firing_plants:
        out.append(_adapt_rail_firing(rp, instance, anchor_day))
    for ifp in scenarios.inv_fanout_plants:
        out.append(_adapt_inv_fanout(ifp, anchor_day))

    return tuple(out)


# ---------------------------------------------------------------------------
# Per-plant adapters
# ---------------------------------------------------------------------------


def _adapt_drift(
    plant: DriftPlant, instance: L2Instance, scenarios: ScenarioPlant,
    anchor_day: date,
) -> ViolationGenerator:
    role = _resolve_account_role(instance, scenarios, plant.account_id)
    return DriftInvariant().scenario_for(
        role,
        magnitude=float(abs(plant.delta_money)),
        instance=instance,
        child_account_id=str(plant.account_id),
    )


def _adapt_overdraft(
    plant: OverdraftPlant, instance: L2Instance, scenarios: ScenarioPlant,
    anchor_day: date,
) -> ViolationGenerator:
    role = _resolve_account_role(instance, scenarios, plant.account_id)
    return OverdraftInvariant().scenario_for(
        role,
        magnitude=float(abs(plant.money)),
        instance=instance,
        account_id=str(plant.account_id),
    )


def _adapt_limit_breach(
    plant: LimitBreachPlant, instance: L2Instance, scenarios: ScenarioPlant,
    anchor_day: date, *, direction: str = "Outbound",
) -> ViolationGenerator:
    parent_role = _resolve_account_parent_role(
        instance, scenarios, plant.account_id,
    )
    return LimitBreachInvariant().scenario_for(
        parent_role,
        str(plant.rail_name),
        direction=direction,  # type: ignore[arg-type]: LimitDirection literal, runtime str works
        overshoot=float(plant.amount),
        instance=instance,
        account_id=str(plant.account_id),
    )


def _adapt_inbound_cap_breach(
    plant: InboundCapBreachPlant, instance: L2Instance,
    scenarios: ScenarioPlant, anchor_day: date,
) -> ViolationGenerator:
    # Same shape as LimitBreachPlant but Inbound direction; the matview
    # surfaces both under LimitBreachInvariant with `direction='Inbound'`.
    parent_role = _resolve_account_parent_role(
        instance, scenarios, plant.account_id,
    )
    return LimitBreachInvariant().scenario_for(
        parent_role,
        str(plant.rail_name),
        direction="Inbound",
        overshoot=float(plant.amount),
        instance=instance,
        account_id=str(plant.account_id),
    )


def _adapt_two_template_chain(
    plant: TwoTemplateChainPlant, instance: L2Instance, anchor_day: date,
) -> ViolationGenerator:
    """Resolve chain_parent kind + child template's leg_rails + emit
    via direct construction (factory's smart constructor picks; the
    adapter has explicit fields)."""
    chain_parent = str(plant.chain_parent_rail_name)
    child_template = _find_template_or_raise(instance, str(plant.child_template_name))
    rail_names = {str(r.name) for r in instance.rails}
    if chain_parent in rail_names:
        parent_rail_name = chain_parent
        parent_template_name: str | None = None
    else:
        parent_template = _find_template_or_raise(instance, chain_parent)
        if not parent_template.leg_rails:
            raise ValueError(
                f"two_template_chain plant: parent template "
                f"{chain_parent!r} has no leg_rails"
            )
        parent_rail_name = str(parent_template.leg_rails[0])
        parent_template_name = str(parent_template.name)
    return TwoTemplateChainGenerator(
        chain_parent_name=chain_parent,
        parent_rail_name=parent_rail_name,
        parent_template_name=parent_template_name,
        child_template_name=str(child_template.name),
        child_leg_rails=tuple(str(r) for r in child_template.leg_rails),
        anchor_day=anchor_day,
    )


def _adapt_chain_parent_disagreement(
    plant: ChainParentDisagreementPlant, anchor_day: date,
) -> ViolationGenerator:
    return ChainParentDisagreementGenerator(
        child_template_name=str(plant.child_template_name),
        anchor_day=anchor_day,
        parent_a_transfer_id=plant.parent_a_transfer_id,
        parent_b_transfer_id=plant.parent_b_transfer_id,
    )


def _adapt_xor_missed(
    plant: XorVariantMissedFiringPlant, anchor_day: date,
) -> ViolationGenerator:
    return XorGroupMissedFiringGenerator(
        template_name=str(plant.template_name),
        xor_group_index=plant.target_xor_group_index,
        witness_rail_name=str(plant.witness_rail_name),
        anchor_day=anchor_day,
    )


def _adapt_xor_overlap(
    plant: XorVariantOverlapPlant, anchor_day: date,
) -> ViolationGenerator:
    return XorGroupOverlapGenerator(
        template_name=str(plant.template_name),
        xor_group_index=plant.target_xor_group_index,
        variant_a_rail_name=str(plant.variant_a_rail_name),
        variant_b_rail_name=str(plant.variant_b_rail_name),
        anchor_day=anchor_day,
    )


def _adapt_fan_in_healthy(
    plant: FanInChainPlant, instance: L2Instance, anchor_day: date,
) -> ViolationGenerator:
    expected_count = _resolve_fan_in_expected_count(
        instance, str(plant.chain_parent_rail_name),
        str(plant.child_template_name),
    )
    return FanInChainGenerator(
        chain_parent_name=str(plant.chain_parent_rail_name),
        child_template_name=str(plant.child_template_name),
        expected_parent_count=expected_count,
        parent_count=plant.parent_count,
        anchor_day=anchor_day,
        expected_kind="healthy",
    )


def _adapt_fan_in_missing(
    plant: FanInChainMissingParentPlant, instance: L2Instance,
    anchor_day: date,
) -> ViolationGenerator:
    expected_count = _resolve_fan_in_expected_count(
        instance, str(plant.chain_parent_rail_name),
        str(plant.child_template_name),
    )
    # OLD plant's parent_count < expected → matview reads 'missing'
    # OR 'orphan' (when expected is unset on the L2 + parent_count < 2).
    kind = "missing" if expected_count is not None else "orphan"
    return FanInChainGenerator(
        chain_parent_name=str(plant.chain_parent_rail_name),
        child_template_name=str(plant.child_template_name),
        expected_parent_count=expected_count,
        parent_count=plant.parent_count,
        anchor_day=anchor_day,
        expected_kind=kind,
    )


def _adapt_fan_in_extra(
    plant: FanInChainExtraParentPlant, instance: L2Instance,
    anchor_day: date,
) -> ViolationGenerator:
    expected_count = _resolve_fan_in_expected_count(
        instance, str(plant.chain_parent_rail_name),
        str(plant.child_template_name),
    )
    return FanInChainGenerator(
        chain_parent_name=str(plant.chain_parent_rail_name),
        child_template_name=str(plant.child_template_name),
        expected_parent_count=expected_count,
        parent_count=plant.parent_count,
        anchor_day=anchor_day,
        expected_kind="extra",
    )


def _adapt_multi_xor_missed(
    plant: MultiXorMissedPlant, anchor_day: date,
) -> ViolationGenerator:
    return MultiXorMissedGenerator(
        chain_parent_name=str(plant.chain_parent_rail_name),
        anchor_day=anchor_day,
    )


def _adapt_multi_xor_overlap(
    plant: MultiXorOverlapPlant, anchor_day: date,
) -> ViolationGenerator:
    return MultiXorOverlapGenerator(
        chain_parent_name=str(plant.chain_parent_rail_name),
        variant_a_child_name=str(plant.variant_a_child_name),
        variant_b_child_name=str(plant.variant_b_child_name),
        anchor_day=anchor_day,
    )


def _adapt_stuck_pending(
    plant: StuckPendingPlant, instance: L2Instance, scenarios: ScenarioPlant,
    as_of: datetime,
) -> ViolationGenerator:
    role = _resolve_account_role(instance, scenarios, plant.account_id)
    return StuckPendingInvariant().scenario_for(
        str(plant.rail_name),
        as_of=as_of,
        account_role=role,
        instance=instance,
        account_id=str(plant.account_id),
    )


def _adapt_stuck_unbundled(
    plant: StuckUnbundledPlant, instance: L2Instance,
    scenarios: ScenarioPlant, as_of: datetime,
) -> ViolationGenerator:
    role = _resolve_account_role(instance, scenarios, plant.account_id)
    return StuckUnbundledInvariant().scenario_for(
        str(plant.rail_name),
        as_of=as_of,
        account_role=role,
        instance=instance,
        account_id=str(plant.account_id),
    )


def _adapt_failed_transaction(
    plant: FailedTransactionPlant, instance: L2Instance,
    scenarios: ScenarioPlant, anchor_day: date,
) -> ViolationGenerator:
    role, scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    return FailedTransactionGenerator(
        account_id=str(plant.account_id),
        account_role=role,
        account_scope=scope,
        account_parent_role=parent_role,
        rail_name=str(plant.rail_name),
        amount=float(plant.amount),
        anchor_day=anchor_day,
    )


def _adapt_supersession(
    plant: SupersessionPlant, instance: L2Instance, scenarios: ScenarioPlant,
    anchor_day: date,
) -> ViolationGenerator:
    role, scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    return SupersessionGenerator(
        account_id=str(plant.account_id),
        account_role=role,
        account_scope=scope,
        account_parent_role=parent_role,
        rail_name=str(plant.rail_name),
        original_amount=float(plant.original_amount),
        corrected_amount=float(plant.corrected_amount),
        anchor_day=anchor_day,
    )


def _adapt_transfer_template(
    plant: TransferTemplatePlant, instance: L2Instance, anchor_day: date,
) -> ViolationGenerator:
    template = _find_template_or_raise(instance, str(plant.template_name))
    if not template.leg_rails:
        raise ValueError(
            f"transfer_template plant: template {plant.template_name!r} "
            f"has no leg_rails"
        )
    first_rail = _find_rail_or_raise(instance, str(template.leg_rails[0]))
    is_two_leg = isinstance(first_rail, TwoLegRail)
    src = str(plant.source_account_id)
    dst: str | None = (
        str(plant.destination_account_id) if is_two_leg else None
    )
    leg_direction: Any = "Debit"
    if (
        isinstance(first_rail, SingleLegRail)
        and first_rail.leg_direction == "Credit"
    ):
        leg_direction = "Credit"
    return TransferTemplateGenerator(
        template_name=str(template.name),
        rail_name=str(first_rail.name),
        is_two_leg=is_two_leg,
        source_account_id=src,
        destination_account_id=dst,
        amount=float(plant.amount),
        single_leg_direction=leg_direction,
        firing_seq=plant.firing_seq,
        anchor_day=anchor_day,
    )


def _adapt_rail_firing(
    plant: RailFiringPlant, instance: L2Instance, anchor_day: date,
) -> ViolationGenerator:
    rail = _find_rail_or_raise(instance, str(plant.rail_name))
    is_two_leg = isinstance(rail, TwoLegRail)
    acct_b: str | None = (
        str(plant.account_id_b)
        if (is_two_leg and plant.account_id_b is not None)
        else None
    )
    leg_direction: Any = "Debit"
    if isinstance(rail, SingleLegRail) and rail.leg_direction == "Credit":
        leg_direction = "Credit"
    return RailFiringGenerator(
        rail_name=str(rail.name),
        is_two_leg=is_two_leg,
        account_id_a=str(plant.account_id_a),
        account_id_b=acct_b,
        amount=float(plant.amount),
        single_leg_direction=leg_direction,
        firing_seq=plant.firing_seq,
        anchor_day=anchor_day,
    )


def _adapt_inv_fanout(
    plant: InvFanoutPlant, anchor_day: date,
) -> ViolationGenerator:
    return InvFanoutGenerator(
        recipient_account_id=str(plant.recipient_account_id),
        sender_account_ids=tuple(str(s) for s in plant.sender_account_ids),
        rail_name=str(plant.rail_name),
        amount_per_transfer=float(plant.amount_per_transfer),
        anchor_day=anchor_day,
    )


# ---------------------------------------------------------------------------
# L2 resolution helpers
# ---------------------------------------------------------------------------


def _resolve_account_triple(
    instance: L2Instance, scenarios: ScenarioPlant, account_id: object,
) -> tuple[str, str, str | None]:
    """Return `(role, scope, parent_role)` for an account_id referenced
    by a plant. Looks in two places:

    1. ``instance.accounts`` — the L2-declared accounts (singletons,
       external counterparties, the static graph).
    2. ``scenarios.template_instances`` — materialized customer
       accounts from ``AccountTemplate``s; their role/scope/parent_role
       come from the matching template definition.

    Raises ValueError when the id is in neither — the OLD plant
    emitter would have crashed on the same input; surface explicitly.
    """
    target = str(account_id)
    # (1) L2-declared accounts (singletons + externals).
    for a in instance.accounts:
        if str(a.id) == target:
            return (
                str(a.role),
                str(a.scope),
                str(a.parent_role) if a.parent_role is not None else None,
            )
    # (2) Materialized customer accounts from AccountTemplate instances.
    template_by_role = {
        str(t.role): t for t in instance.account_templates
    }
    for ti in scenarios.template_instances:
        if str(ti.account_id) == target:
            template = template_by_role.get(str(ti.template_role))
            if template is None:
                raise ValueError(
                    f"plant adapter: template_instance {target!r} "
                    f"references AccountTemplate "
                    f"{ti.template_role!r} which isn't declared on "
                    f"the L2 instance"
                )
            return (
                str(template.role),
                str(template.scope),
                (
                    str(template.parent_role)
                    if template.parent_role is not None
                    else None
                ),
            )
    raise ValueError(
        f"plant adapter: account_id {target!r} not declared on the L2 "
        f"instance (neither in instance.accounts nor materialized via "
        f"scenarios.template_instances); cannot adapt the plant. "
        f"Check the scenario builder for a stale account reference."
    )


def _resolve_account_role(
    instance: L2Instance, scenarios: ScenarioPlant, account_id: object,
) -> str:
    """Short-form wrapper returning just the role."""
    role, _scope, _parent = _resolve_account_triple(
        instance, scenarios, account_id,
    )
    return role


def _resolve_account_parent_role(
    instance: L2Instance, scenarios: ScenarioPlant, account_id: object,
) -> str:
    """Short-form wrapper returning just the parent_role; raises when
    the account has no parent_role (limit_breach plants need it to
    locate the matching LimitSchedule)."""
    _role, _scope, parent = _resolve_account_triple(
        instance, scenarios, account_id,
    )
    if parent is None:
        raise ValueError(
            f"plant adapter: account {account_id!r} has no parent_role; "
            f"cannot adapt as a limit_breach plant "
            f"(LimitBreachInvariant.scenario_for needs the parent role "
            f"to find the matching LimitSchedule)"
        )
    return parent


def _find_template_or_raise(
    instance: L2Instance, name: str,
) -> TransferTemplate:
    for t in instance.transfer_templates:
        if str(t.name) == name:
            return t
    raise ValueError(
        f"plant adapter: transfer_template {name!r} not declared on "
        f"the L2 instance"
    )


def _find_rail_or_raise(
    instance: L2Instance, name: str,
) -> TwoLegRail | SingleLegRail:
    """Find rail by name; spine generators only handle the
    TwoLegRail / SingleLegRail union (Rail typealias is exactly
    these two). AggregatingRail isn't a `Rail`-typed value in the
    L2 model so it's already excluded by the `instance.rails`
    iteration."""
    for r in instance.rails:
        if str(r.name) == name:
            return r
    raise ValueError(
        f"plant adapter: rail {name!r} not declared on the L2 instance"
    )


def _resolve_fan_in_expected_count(
    instance: L2Instance, chain_parent: str, child_template: str,
) -> int | None:
    """Look up `expected_parent_count` on the L2 chain whose parent
    is `chain_parent` + child resolves to `child_template`. Returns
    None when the chain leaves it unset (variable-batch flows)."""
    for chain in instance.chains:
        if str(chain.parent) != chain_parent:
            continue
        for child in chain.children:
            if str(child.name) != child_template:
                continue
            return child.expected_parent_count
    raise ValueError(
        f"plant adapter: no chain with parent={chain_parent!r} + "
        f"child={child_template!r} declared on the L2 instance"
    )
