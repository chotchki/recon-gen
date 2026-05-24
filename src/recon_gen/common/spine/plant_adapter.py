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
from recon_gen.common.spine.chain_completion import ChainCompletionGenerator
from recon_gen.common.spine.chain_parent_disagreement import (
    ChainParentDisagreementGenerator,
)
from recon_gen.common.spine.drift import DriftGenerator
from recon_gen.common.spine.failed_transaction import FailedTransactionGenerator
from recon_gen.common.spine.fan_in_disagreement import FanInChainGenerator
from recon_gen.common.spine.generator import ViolationGenerator
from recon_gen.common.spine.inv_fanout import InvFanoutGenerator
from recon_gen.common.spine.limit_breach import LimitBreachGenerator
from recon_gen.common.spine.multi_xor_violation import (
    MultiXorMissedGenerator,
    MultiXorOverlapGenerator,
)
from recon_gen.common.spine.overdraft import OverdraftGenerator
from recon_gen.common.spine.rail_firing import RailFiringGenerator
from recon_gen.common.spine.rng import scenario_rng
from recon_gen.common.spine.stuck_pending import StuckPendingGenerator
from recon_gen.common.spine.stuck_unbundled import StuckUnbundledGenerator
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
    prefix: str = "spec_example",
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

    `prefix` defaults to "spec_example" (the in-process test harness
    shape). Production callers (AY.4.d `build_full_seed_sql`) pass
    `cfg.db_table_prefix`; every constructed generator inherits
    `self.prefix = prefix` so `insert_tx` / `insert_balance` writes
    to the correctly-prefixed tables.
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
        lb_gen = _adapt_limit_breach(
            lp, instance, scenarios, anchor_day, direction="Outbound",
        )
        out.append(lb_gen)
        out.extend(_chain_completion_for_rail(
            lb_gen, instance, anchor_day,  # type: ignore[arg-type]: LimitBreachGenerator has rail-keyed transfer_id; structural narrowing not inferred
        ))
    for icp in scenarios.inbound_cap_breach_plants:
        ib_gen = _adapt_inbound_cap_breach(
            icp, instance, scenarios, anchor_day,
        )
        out.append(ib_gen)
        out.extend(_chain_completion_for_rail(
            ib_gen, instance, anchor_day,  # type: ignore[arg-type]: LimitBreachGenerator has rail-keyed transfer_id; structural narrowing not inferred
        ))

    # L2-shape plants — direct construct + L2 resolution.
    for ttp in scenarios.two_template_chain_plants:
        out.append(_adapt_two_template_chain(ttp, instance, anchor_day))
    for cpd in scenarios.chain_parent_disagreement_plants:
        out.append(_adapt_chain_parent_disagreement(cpd, anchor_day))
    for xm in scenarios.xor_variant_missed_firing_plants:
        xor_gen = _adapt_xor_missed(xm, anchor_day)
        out.append(xor_gen)
        # AY.4.g — if the template is a chain parent, co-plant the
        # child completion so multi_xor_violation doesn't false-
        # positive on the XOR firing's chain-parent-but-no-child shape.
        out.extend(_chain_completion_for_template(
            xor_gen, instance, anchor_day,  # type: ignore[arg-type]: XorGroupMissedFiringGenerator has transfer_id/account_id; structural narrowing not inferred
        ))
    for xo in scenarios.xor_variant_overlap_plants:
        xor_gen = _adapt_xor_overlap(xo, anchor_day)
        out.append(xor_gen)
        out.extend(_chain_completion_for_template(
            xor_gen, instance, anchor_day,  # type: ignore[arg-type]: XorGroupOverlapGenerator has transfer_id/account_id; structural narrowing not inferred
        ))
    for fp in scenarios.fan_in_chain_plants:
        out.append(_adapt_fan_in_healthy(fp, instance, anchor_day))
    for fmp in scenarios.fan_in_chain_missing_parent_plants:
        out.append(_adapt_fan_in_missing(fmp, instance, anchor_day))
    for fxp in scenarios.fan_in_chain_extra_parent_plants:
        out.append(_adapt_fan_in_extra(fxp, instance, anchor_day))
    for mxm in scenarios.multi_xor_missed_plants:
        out.append(_adapt_multi_xor_missed(mxm, instance, anchor_day))
    for mxo in scenarios.multi_xor_overlap_plants:
        out.append(_adapt_multi_xor_overlap(mxo, instance, anchor_day))

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

    # Thread `prefix` to every generator post-construction. Every
    # spine generator carries a `prefix: str = "spec_example"` field;
    # the adapter helpers build with that default to keep their
    # construction signatures narrow. Production callers thread
    # `cfg.db_table_prefix` here so the emit writes to the
    # correctly-prefixed tables.
    if prefix != "spec_example":
        for gen in out:
            # Spine generators are @dataclass (mutable); mutating
            # `prefix` directly is cleaner than dataclasses.replace
            # (which would lose the per-generator subtype). All
            # generators have a `prefix` attribute by spine convention.
            setattr(gen, "prefix", prefix)

    return tuple(out)


# ---------------------------------------------------------------------------
# Per-plant adapters
# ---------------------------------------------------------------------------


def _adapt_drift(
    plant: DriftPlant, instance: L2Instance, scenarios: ScenarioPlant,
    anchor_day: date,
) -> ViolationGenerator:
    """AY.4.f — direct-construct DriftGenerator. Bypasses the
    factory's `scenario_for(role)` which raises on roles only present
    via AccountTemplate (sasquatch's CustomerDDA). The adapter
    already has the plant's specific account_id; resolve role +
    parent triple via `_resolve_account_triple` (which handles both
    L2-singleton + template-instance accounts), then look up the
    parent account by role IF the L2 declares one as a singleton
    (template-instance parents aren't typically materialized for
    drift; the OLD path tolerated the None case)."""
    role, _scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    parent_account_id: str | None = None
    parent_account_role: str | None = None
    if parent_role is not None:
        # Try L2 singletons first; fall back to None when no parent
        # singleton exists for this role (the spine emit handles
        # parent_account_id=None — no parent balance row written).
        for a in instance.accounts:
            if str(a.role) == parent_role and str(a.scope) == "internal":
                parent_account_id = str(a.id)
                parent_account_role = parent_role
                break
    return DriftGenerator(
        child_account_id=str(plant.account_id),
        child_role=role,
        parent_role=parent_role or "",
        parent_account_id=parent_account_id,
        parent_account_role=parent_account_role,
        anchor_day=anchor_day,
        magnitude=float(abs(plant.delta_money)),
        rng=scenario_rng(),
    )


def _adapt_overdraft(
    plant: OverdraftPlant, instance: L2Instance, scenarios: ScenarioPlant,
    anchor_day: date,
) -> ViolationGenerator:
    role, _scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    return OverdraftGenerator(
        account_id=str(plant.account_id),
        account_role=role,
        account_parent_role=parent_role,
        anchor_day=anchor_day,
        magnitude=float(abs(plant.money)),
    )


def _adapt_limit_breach(
    plant: LimitBreachPlant, instance: L2Instance, scenarios: ScenarioPlant,
    anchor_day: date, *, direction: str = "Outbound",
) -> ViolationGenerator:
    """AY.4.f — direct-construct LimitBreachGenerator. Looks up the
    LimitSchedule's cap by (parent_role, rail, direction) from the
    L2 instance."""
    role, _scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    if parent_role is None:
        raise ValueError(
            f"plant adapter: limit_breach plant on account "
            f"{plant.account_id!r} has no parent_role; "
            f"LimitSchedule lookup needs parent_role"
        )
    cap = _resolve_limit_schedule_cap(
        instance, parent_role, str(plant.rail_name), direction,
    )
    return LimitBreachGenerator(
        account_id=str(plant.account_id),
        account_role=role,
        account_parent_role=parent_role,
        rail_name=str(plant.rail_name),
        direction=direction,  # type: ignore[arg-type]: LimitDirection literal accepts validated str at runtime
        cap=cap,
        overshoot=float(plant.amount) - cap,
        anchor_day=anchor_day,
    )


def _adapt_inbound_cap_breach(
    plant: InboundCapBreachPlant, instance: L2Instance,
    scenarios: ScenarioPlant, anchor_day: date,
) -> ViolationGenerator:
    """Mirror of `_adapt_limit_breach` with `direction='Inbound'`."""
    role, _scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    if parent_role is None:
        raise ValueError(
            f"plant adapter: inbound_cap_breach plant on account "
            f"{plant.account_id!r} has no parent_role"
        )
    cap = _resolve_limit_schedule_cap(
        instance, parent_role, str(plant.rail_name), "Inbound",
    )
    return LimitBreachGenerator(
        account_id=str(plant.account_id),
        account_role=role,
        account_parent_role=parent_role,
        rail_name=str(plant.rail_name),
        direction="Inbound",
        cap=cap,
        overshoot=float(plant.amount) - cap,
        anchor_day=anchor_day,
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
    plant: MultiXorMissedPlant, instance: L2Instance, anchor_day: date,
) -> ViolationGenerator:
    return MultiXorMissedGenerator(
        chain_parent_name=str(plant.chain_parent_rail_name),
        anchor_day=anchor_day,
        instance=instance,
    )


def _adapt_multi_xor_overlap(
    plant: MultiXorOverlapPlant, instance: L2Instance, anchor_day: date,
) -> ViolationGenerator:
    return MultiXorOverlapGenerator(
        chain_parent_name=str(plant.chain_parent_rail_name),
        variant_a_child_name=str(plant.variant_a_child_name),
        variant_b_child_name=str(plant.variant_b_child_name),
        anchor_day=anchor_day,
        instance=instance,
    )


def _adapt_stuck_pending(
    plant: StuckPendingPlant, instance: L2Instance, scenarios: ScenarioPlant,
    as_of: datetime,
) -> ViolationGenerator:
    """AY.4.f — direct-construct StuckPendingGenerator. Looks up the
    rail's max_pending_age from the L2."""
    role, _scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    rail = _find_rail_or_raise(instance, str(plant.rail_name))
    if rail.max_pending_age is None:
        raise ValueError(
            f"plant adapter: stuck_pending plant on rail "
            f"{plant.rail_name!r} has no max_pending_age set on the "
            f"L2 (matview filter excludes; the plant would silently "
            f"emit an inert row)"
        )
    account_id = str(plant.account_id)
    return StuckPendingGenerator(
        transaction_id=f"tx-stuck-pending-{plant.rail_name}-{account_id}",
        transfer_id=f"xfer-stuck-pending-{plant.rail_name}-{account_id}",
        rail_name=str(plant.rail_name),
        account_id=account_id,
        account_role=role,
        account_parent_role=parent_role,
        max_pending_age_seconds=int(rail.max_pending_age.total_seconds()),
        overshoot_seconds=60,  # default from the factory's scenario_for
        as_of=as_of,
    )


def _adapt_stuck_unbundled(
    plant: StuckUnbundledPlant, instance: L2Instance,
    scenarios: ScenarioPlant, as_of: datetime,
) -> ViolationGenerator:
    """AY.4.f — direct-construct StuckUnbundledGenerator. Looks up
    the rail's max_unbundled_age from the L2."""
    role, _scope, parent_role = _resolve_account_triple(
        instance, scenarios, plant.account_id,
    )
    rail = _find_rail_or_raise(instance, str(plant.rail_name))
    if rail.max_unbundled_age is None:
        raise ValueError(
            f"plant adapter: stuck_unbundled plant on rail "
            f"{plant.rail_name!r} has no max_unbundled_age set on "
            f"the L2"
        )
    account_id = str(plant.account_id)
    return StuckUnbundledGenerator(
        transaction_id=f"tx-stuck-unbundled-{plant.rail_name}-{account_id}",
        transfer_id=f"xfer-stuck-unbundled-{plant.rail_name}-{account_id}",
        rail_name=str(plant.rail_name),
        account_id=account_id,
        account_role=role,
        account_parent_role=parent_role,
        max_unbundled_age_seconds=int(
            rail.max_unbundled_age.total_seconds(),
        ),
        overshoot_seconds=60,
        as_of=as_of,
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
        # AY.6.b — thread per-firing metadata field values from the
        # OLD plant's `extra_metadata` tuple (populated by the picker
        # from `rail.metadata_value_examples`, cycling per firing_seq).
        # The spine generator merges these into the metadata JSON
        # alongside the AV.5 scenario_id stamp.
        metadata_extras=tuple(
            (str(k), str(v)) for k, v in plant.extra_metadata
        ),
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


# AY.4.f retired the `_resolve_account_role` + `_resolve_account_parent_role`
# short-form wrappers — every adapter now uses `_resolve_account_triple`
# directly so the unused-import lint stays clean. If a future adapter
# needs just one element, prefer destructuring the triple at the call
# site over re-introducing thin wrappers.


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


def _chain_completion_for_template(
    parent_gen: "XorGroupMissedFiringGenerator | XorGroupOverlapGenerator",
    instance: L2Instance, anchor_day: date,
) -> list[ChainCompletionGenerator]:
    """AY.4.g — emit chain completion when the XOR plant's
    ``template_name`` is also a chain parent. Returns empty list when
    the template doesn't parent any chain (the common case)."""
    parent_name = str(parent_gen.template_name)
    if not _is_chain_parent(instance, parent_name):
        return []
    return [ChainCompletionGenerator(
        parent_transfer_id=parent_gen.transfer_id,
        parent_name=parent_name,
        account_id=parent_gen.account_id,
        account_role="CustomerSubledger",  # XOR plants emit on synthetic account; role/scope/parent are synthetic too
        account_scope="internal",
        account_parent_role="CustomerLedger",
        anchor_day=anchor_day,
        instance=instance,
    )]


def _chain_completion_for_rail(
    parent_gen: "LimitBreachGenerator",
    instance: L2Instance, anchor_day: date,
) -> list[ChainCompletionGenerator]:
    """AY.4.g — emit chain completion when the LimitBreach plant's
    ``rail_name`` is also a chain parent. Returns empty list when the
    rail doesn't parent any chain (most rails)."""
    parent_name = str(parent_gen.rail_name)
    if not _is_chain_parent(instance, parent_name):
        return []
    return [ChainCompletionGenerator(
        # The OLD multi_xor matview keys on the chain-parent's
        # transfer_id. LimitBreachGenerator's transfer_id is
        # `xfer-limit-breach-{rail}-{direction}-{account}` — that's
        # what we thread.
        parent_transfer_id=(
            f"xfer-limit-breach-"
            f"{parent_gen.rail_name}-{parent_gen.direction}-"
            f"{parent_gen.account_id}"
        ),
        parent_name=parent_name,
        account_id=parent_gen.account_id,
        account_role=parent_gen.account_role,
        account_scope="internal",
        account_parent_role=parent_gen.account_parent_role,
        anchor_day=anchor_day,
        instance=instance,
    )]


def _is_chain_parent(instance: L2Instance, name: str) -> bool:
    """True when `name` matches any L2 chain's parent identifier."""
    return any(str(c.parent) == name for c in instance.chains)


def _resolve_limit_schedule_cap(
    instance: L2Instance, parent_role: str, rail_name: str,
    direction: str,
) -> float:
    """Look up the LimitSchedule.cap for the given (parent_role, rail,
    direction) triple. AY.4.f's direct-construct adapter needs the
    cap to compute the breach overshoot.

    Raises ValueError when no schedule matches — the plant references
    a (parent_role, rail, direction) the L2 doesn't declare a cap for.
    """
    for sched in instance.limit_schedules:
        if (
            str(sched.parent_role) == parent_role
            and str(sched.rail) == rail_name
            and str(sched.direction) == direction
        ):
            return float(sched.cap)
    raise ValueError(
        f"plant adapter: no LimitSchedule declared on the L2 for "
        f"parent_role={parent_role!r}, rail={rail_name!r}, "
        f"direction={direction!r}. Check the L2 instance's "
        f"limit_schedules block."
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
