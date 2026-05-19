"""Auto-derive a ``ScenarioPlant`` covering every L1 invariant from an L2 instance.

Companion to ``common.l2.seed`` — that module owns the typed plant
primitives + ``emit_seed`` machinery; this module knows how to walk an
arbitrary L2 instance and pick representative entities so an
integrator can run ``recon-gen demo seed-l2 myorg.yaml`` and get
a working seed without authoring scenarios in Python.

Heuristics (deterministic, sorted by stable keys at every choice point):

- **TemplateInstance**: materialize 2 synthetic instances under the
  first ``AccountTemplate`` (sorted by role name). Synthetic ids are
  ``cust-001`` / ``cust-002``, names ``Customer 1`` / ``Customer 2``.
  Persona-blind by construction.
- **DriftPlant**: pick the first 2-leg Rail (sorted by name) whose
  destination_role matches the template, AND has at least one
  external-scope Account whose role matches the source side. Use that
  external Account as the counter.
- **OverdraftPlant**: needs only a TemplateInstance — no rail. Plant
  on the second customer.
- **LimitBreachPlant**: first ``LimitSchedule`` (sorted by
  parent_role + transfer_type) whose transfer_type matches some
  outbound 2-leg Rail (source = template role, destination = external
  role). Plant amount = cap × 1.5 to guarantee breach.
- **StuckPendingPlant**: first Rail (sorted by name) with
  ``max_pending_age`` set.
- **StuckUnbundledPlant**: first Rail with ``max_unbundled_age`` set.
  Validator R8 guarantees such a rail is bundled by some aggregating
  rail, so the resulting Posted leg surfaces in
  ``<prefix>_stuck_unbundled``.
- **SupersessionPlant**: first single-leg Rail or any Rail with a
  customer-side leg.

Plants that can't be derived (e.g., no LimitSchedule declared, no
2-leg inbound rail) are omitted from the returned ``ScenarioPlant``.
The CLI surface logs a one-line warning per omission so the
integrator knows what's missing from their YAML for full coverage.

The auto-scenario deliberately does NOT try to produce byte-identical
output to the curated ``default_ar_scenario`` — the two are different
contracts. ``default_ar_scenario`` is the hash-locked canonical AR
fixture; this module produces a reasonable starting demo for ANY L2.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from .primitives import (
    Account,
    AccountTemplate,
    Identifier,
    L2Instance,
    LimitSchedule,
    Name,
    Rail,
    RoleExpression,
    SingleLegRail,
    TwoLegRail,
)
from .seed import (
    ChainParentDisagreementPlant,
    DriftPlant,
    FailedTransactionPlant,
    FanInChainExtraParentPlant,
    FanInChainMissingParentPlant,
    FanInChainPlant,
    MultiXorMissedPlant,
    MultiXorOverlapPlant,
    InboundCapBreachPlant,
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
    TwoTemplateChainPlant,
    XorVariantMissedFiringPlant,
    XorVariantOverlapPlant,
)


# ScenarioMode (M.4.2) — selects which plant kinds the auto-scenario
# emits. ``l1_invariants`` is the default + the original behavior; the
# broad modes layer in per-rail firings so the L2 Flow Tracing
# dashboard's Rails / Chains / Transfer Templates sheets show content
# beyond the few rails the L1 invariant picker chose.
ScenarioMode = Literal["l1_invariants", "broad", "l1_plus_broad"]


@dataclass(frozen=True, slots=True)
class AutoScenarioReport:
    """Describes which plants the auto-scenario emitted vs. omitted.

    The CLI prints this so the integrator knows what's missing from
    their YAML for full L1 coverage.
    """

    scenario: ScenarioPlant
    omitted: tuple[tuple[str, str], ...]   # (plant_kind, reason) pairs


def default_scenario_for(
    instance: L2Instance,
    *,
    today: date | None = None,
    mode: ScenarioMode = "l1_invariants",
    per_rail_firings: int = 3,
) -> AutoScenarioReport:
    """Walk ``instance`` and return an auto-derived ``ScenarioPlant``.

    Modes (M.4.2):

    - ``l1_invariants`` (default) — only L1 SHOULD-violation plants
      (drift, overdraft, limit-breach, stuck-pending, stuck-unbundled,
      supersession, transfer-template). The legacy / pre-M.4.2 shape;
      L2 Flow Tracing surfaces dead for any rail not picked.
    - ``broad`` — only ``RailFiringPlant`` rows: every declared rail
      whose role(s) resolve to a materialized account fires
      ``per_rail_firings`` times across stratified days. No L1
      invariant plants. Useful for visual verification of the L2
      surface in isolation.
    - ``l1_plus_broad`` — both layers. The harness (M.4.1.b) uses this
      so Playwright can assert both planted SHOULD violations AND
      planted-rail visibility on the same deploy.

    See module docstring for per-plant heuristics. Returns the
    scenario plus a report of any plant kinds that couldn't be
    materialized from this instance (e.g., no ``LimitSchedule``
    declared → no LimitBreachPlant).
    """
    today_ref = today or datetime.now(tz=timezone.utc).date()  # typing-smell: ignore[no-datetime-now]: ad-hoc-run fallback; tests + CLI always pass today=anchor (CLAUDE.md "Anchor pinned at date(2030, 1, 1)")
    omitted: list[tuple[str, str]] = []
    include_l1 = mode in ("l1_invariants", "l1_plus_broad")
    include_broad = mode in ("broad", "l1_plus_broad")

    # -- Pick template + materialize 2 customer instances ------------
    template = _pick_template(instance)
    if template is None:
        return AutoScenarioReport(
            scenario=ScenarioPlant(template_instances=(), today=today_ref),
            omitted=(("ALL", "no AccountTemplate declared in instance"),),
        )
    cust1, cust2 = _materialize_instances(template)

    # -- Pre-compute pickable structures ----------------------------
    drift_rail = _pick_inbound_2leg_rail(instance, template.role)
    if drift_rail is None:
        omitted.append(("DriftPlant",
                        "no 2-leg Rail with destination matching template role"))
    breach_picks = _pick_breach_inputs(instance, template.role)
    if breach_picks is None:
        omitted.append(("LimitBreachPlant",
                        "no Outbound LimitSchedule whose rail matches an "
                        "outbound 2-leg Rail with external counter"))
    inbound_breach_picks = _pick_inbound_breach_inputs(instance, template.role)
    if inbound_breach_picks is None:
        omitted.append(("InboundCapBreachPlant",
                        "no Inbound LimitSchedule whose rail matches an "
                        "inbound 2-leg Rail with external counter (AB.1)"))
    # AB.2.6 — two-template chain picker: finds any chain whose singleton
    # child is a TransferTemplate (template-as-chain-child case, gap doc §3).
    two_template_chain_pick = _pick_two_template_chain_inputs(instance)
    if two_template_chain_pick is None:
        omitted.append((
            "TwoTemplateChainPlant",
            "no Chain whose singleton child resolves to a TransferTemplate (AB.2)",
        ))
        omitted.append((
            "ChainParentDisagreementPlant",
            "no Chain whose singleton child resolves to a TransferTemplate (AB.2)",
        ))
    # AB.3.5 — XorVariantMissedFiringPlant picker: finds any
    # TransferTemplate with ≥1 XOR group AND a leg_rail outside that
    # group (the witness leg used to surface the Transfer without
    # firing any XOR-group member).
    xor_missed_pick = _pick_xor_missed_firing_inputs(instance)
    if xor_missed_pick is None:
        omitted.append((
            "XorVariantMissedFiringPlant",
            "no TransferTemplate declares leg_rail_xor_groups with a "
            "non-XOR-group leg_rail to use as witness (AB.3)",
        ))
    # AB.3.5b — XorVariantOverlapPlant picker: any XOR group with ≥2
    # members. Validator C1d enforces ≥2 at load time, so every
    # declared XOR group qualifies.
    xor_overlap_pick = _pick_xor_overlap_inputs(instance)
    if xor_overlap_pick is None:
        omitted.append((
            "XorVariantOverlapPlant",
            "no TransferTemplate declares leg_rail_xor_groups (AB.3)",
        ))
    # AB.4.5 — Fan-in chain plant picker: any chain with fan_in=True
    # whose parent is a Rail (so the plant can synthesize parent legs).
    fan_in_pick = _pick_fan_in_chain_inputs(instance)
    if fan_in_pick is None:
        omitted.append((
            "FanInChainPlant",
            "no Chain declares fan_in=True (AB.4)",
        ))
        omitted.append((
            "FanInChainMissingParentPlant",
            "no Chain declares fan_in=True (AB.4)",
        ))
        omitted.append((
            "FanInChainExtraParentPlant",
            "no Chain declares fan_in=True (AB.4)",
        ))
    # AB.6.6 — Multi-XOR chain plant picker: any chain with ≥2 non-fan_in
    # children and a Rail parent (so the plant can synthesize parent
    # firings). Plants exercise both 'missed' (count=0) and 'overlap'
    # (count≥2) branches of the AB.6.5 _multi_xor_violation matview.
    multi_xor_pick = _pick_multi_xor_chain_inputs(instance)
    if multi_xor_pick is None:
        omitted.append((
            "MultiXorMissedPlant",
            "no Chain declares ≥2 non-fan_in children with a Rail "
            "parent (AB.6)",
        ))
        omitted.append((
            "MultiXorOverlapPlant",
            "no Chain declares ≥2 non-fan_in children with a Rail "
            "parent (AB.6)",
        ))
    pending_rail = _pick_first_with(
        instance.rails, key=lambda r: r.max_pending_age is not None,
    )
    if pending_rail is None:
        omitted.append(("StuckPendingPlant",
                        "no Rail declares max_pending_age"))
    unbundled_rail = _pick_first_with(
        instance.rails, key=lambda r: r.max_unbundled_age is not None,
    )
    if unbundled_rail is None:
        omitted.append(("StuckUnbundledPlant",
                        "no Rail declares max_unbundled_age"))
    super_rail = _pick_supersession_rail(instance, template.role)
    if super_rail is None:
        omitted.append(("SupersessionPlant",
                        "no single-leg Rail with leg_role matching "
                        "template role"))
    inv_fanout_picks = _pick_inv_fanout_inputs(instance, template.role)
    if inv_fanout_picks is None:
        omitted.append(("InvFanoutPlant",
                        "no leaf-internal recipient template OR fewer than "
                        "2 distinct sender accounts available"))

    # External counter for drift + limit-breach plants. Falls back to
    # any external Account if the rail-aware lookup misses.
    drift_counter = (
        _pick_external_counter_for_rail(instance, drift_rail)
        if drift_rail is not None else None
    )

    # -- Assemble the scenario ---------------------------------------
    drift_plants: tuple[DriftPlant, ...] = ()
    if drift_rail is not None and drift_counter is not None:
        drift_plants = (
            DriftPlant(
                account_id=cust1.account_id,
                days_ago=5,
                delta_money=Decimal("75.00"),
                rail_name=drift_rail.name,
                counter_account_id=drift_counter.id,
            ),
        )
    elif drift_rail is not None and drift_counter is None:
        omitted.append(("DriftPlant",
                        f"rail {drift_rail.name!r} has no external Account "
                        f"matching its source role"))

    overdraft_plants = (
        OverdraftPlant(
            account_id=cust2.account_id,
            days_ago=6,
            money=Decimal("-1500.00"),
        ),
    )

    limit_breach_plants: tuple[LimitBreachPlant, ...] = ()
    if breach_picks is not None:
        ls, breach_rail, breach_counter = breach_picks
        # AB.5 (E7): cap-breach plants emit at cap * 1.5 by default
        # (violation MUST exceed cap). When the rail declares an
        # amount_typical_range AND cap * 1.5 exceeds range.max * 3,
        # _cap_breach_amount pins to range.max * 3 so the breach
        # amount stays in a realistic ballpark.
        breach_amount = _cap_breach_amount(ls.cap, breach_rail)
        limit_breach_plants = (
            LimitBreachPlant(
                account_id=cust1.account_id,
                days_ago=4,
                rail_name=breach_rail.name,
                amount=breach_amount,
                counter_account_id=breach_counter.id,
            ),
        )

    # AB.1 — Inbound cap breach mirrors Outbound. Different days_ago
    # (3 vs 4) to keep the planted rows visually distinct on the L1
    # Limit Breach sheet's date axis.
    inbound_cap_breach_plants: tuple[InboundCapBreachPlant, ...] = ()
    if inbound_breach_picks is not None:
        in_ls, in_rail, in_counter = inbound_breach_picks
        in_amount = _cap_breach_amount(in_ls.cap, in_rail)
        inbound_cap_breach_plants = (
            InboundCapBreachPlant(
                account_id=cust1.account_id,
                days_ago=3,
                rail_name=in_rail.name,
                amount=in_amount,
                counter_account_id=in_counter.id,
            ),
        )

    # AB.2.6 — TwoTemplateChainPlant + ChainParentDisagreementPlant.
    # Both gate on a two-template chain existing in the L2. Distinct
    # days_ago (2 vs 1) so planted rows are visually separable from
    # the limit-breach plants above.
    two_template_chain_plants: tuple[TwoTemplateChainPlant, ...] = ()
    chain_parent_disagreement_plants: tuple[ChainParentDisagreementPlant, ...] = ()
    if two_template_chain_pick is not None:
        chain_parent_rail, chain_child_template = two_template_chain_pick
        two_template_chain_plants = (
            TwoTemplateChainPlant(
                chain_parent_rail_name=chain_parent_rail,
                child_template_name=chain_child_template,
                days_ago=2,
            ),
        )
        chain_parent_disagreement_plants = (
            ChainParentDisagreementPlant(
                child_template_name=chain_child_template,
                days_ago=1,
                parent_a_transfer_id="tr-cpd-parent-a-0001",
                parent_b_transfer_id="tr-cpd-parent-b-0001",
            ),
        )

    # AB.3.5 — XorVariantMissedFiringPlant. Plants one Transfer tagged
    # with an XOR-grouped template where the target group has zero
    # firings. days_ago=0 places business_day=today so the row surfaces
    # on Today's Exceptions immediately.
    xor_variant_missed_firing_plants: tuple[XorVariantMissedFiringPlant, ...] = ()
    if xor_missed_pick is not None:
        xor_template_name, xor_group_idx, xor_witness = xor_missed_pick
        xor_variant_missed_firing_plants = (
            XorVariantMissedFiringPlant(
                template_name=xor_template_name,
                target_xor_group_index=xor_group_idx,
                days_ago=0,
                witness_rail_name=xor_witness,
            ),
        )

    # AB.3.5b — XorVariantOverlapPlant. Plants one Transfer tagged
    # with an XOR-grouped template where two members of the target
    # group both fire. days_ago=1 (yesterday) keeps it visually
    # separable from the AB.3.5 missed-firing plant (today).
    xor_variant_overlap_plants: tuple[XorVariantOverlapPlant, ...] = ()
    if xor_overlap_pick is not None:
        ov_template_name, ov_group_idx, ov_variant_a, ov_variant_b = xor_overlap_pick
        xor_variant_overlap_plants = (
            XorVariantOverlapPlant(
                template_name=ov_template_name,
                target_xor_group_index=ov_group_idx,
                days_ago=1,
                variant_a_rail_name=ov_variant_a,
                variant_b_rail_name=ov_variant_b,
            ),
        )

    # AB.4.5 — Fan-in chain plants. When a fan_in chain exists, plant
    # all three kinds (healthy + missing + extra) so the demo dashboard
    # surfaces every branch of the AB.4.7 matview's disagreement_kind
    # discriminator. Extra-parent plant only fires when the chain
    # declares expected_parent_count (otherwise the matview has no
    # upper bound to flag against — see AB.4.0 lock).
    fan_in_chain_plants: tuple[FanInChainPlant, ...] = ()
    fan_in_chain_missing_parent_plants: tuple[
        FanInChainMissingParentPlant, ...
    ] = ()
    fan_in_chain_extra_parent_plants: tuple[
        FanInChainExtraParentPlant, ...
    ] = ()
    if fan_in_pick is not None:
        fi_parent_rail, fi_child_template, fi_expected = fan_in_pick
        # Healthy plant: parent_count == expected (or 2 when unset).
        healthy_count = fi_expected if fi_expected is not None else 2
        fan_in_chain_plants = (
            FanInChainPlant(
                chain_parent_rail_name=fi_parent_rail,
                child_template_name=fi_child_template,
                days_ago=5,
                parent_count=healthy_count,
            ),
        )
        # Missing-parent plant: parent_count = expected - 1 (or 1 when
        # expected is unset — the orphan threshold).
        missing_count = max(1, healthy_count - 1)
        fan_in_chain_missing_parent_plants = (
            FanInChainMissingParentPlant(
                chain_parent_rail_name=fi_parent_rail,
                child_template_name=fi_child_template,
                days_ago=4,
                parent_count=missing_count,
            ),
        )
        # Extra-parent plant: only meaningful when expected is set
        # (otherwise no upper bound for the matview to flag).
        if fi_expected is not None:
            fan_in_chain_extra_parent_plants = (
                FanInChainExtraParentPlant(
                    chain_parent_rail_name=fi_parent_rail,
                    child_template_name=fi_child_template,
                    days_ago=3,
                    parent_count=fi_expected + 1,
                ),
            )
        else:
            omitted.append((
                "FanInChainExtraParentPlant",
                "fan_in chain has no expected_parent_count — extra "
                "parent count can't be flagged without an upper bound",
            ))

    # AB.6.6 — Multi-XOR plants. days_ago 6/5 separates from AB.4's
    # fan-in plants (days_ago 3/4/5) on the date axis so date-binned
    # rollups can tell them apart without filter narrowing.
    multi_xor_missed_plants: tuple[MultiXorMissedPlant, ...] = ()
    multi_xor_overlap_plants: tuple[MultiXorOverlapPlant, ...] = ()
    if multi_xor_pick is not None:
        mxc_parent, mxc_child_a, mxc_child_b = multi_xor_pick
        multi_xor_missed_plants = (
            MultiXorMissedPlant(
                chain_parent_rail_name=mxc_parent,
                days_ago=6,
            ),
        )
        multi_xor_overlap_plants = (
            MultiXorOverlapPlant(
                chain_parent_rail_name=mxc_parent,
                variant_a_child_name=mxc_child_a,
                variant_b_child_name=mxc_child_b,
                days_ago=5,
            ),
        )

    stuck_pending_plants: tuple[StuckPendingPlant, ...] = ()
    if pending_rail is not None:
        # max_pending_age caps vary widely (PT4H ↔ P7D in production
        # fixtures, longer in fuzz). Read the picked rail's cap and
        # plant comfortably past it (cap_days + 7) so the matview
        # surfaces the row regardless of which rail got chosen. Same
        # pattern as stuck_unbundled below — the original hardcoded
        # days_ago=2 silently failed for any rail with a cap >= 2
        # days (M.4.4.13).
        cap_days = max(
            1,
            int((pending_rail.max_pending_age or _zero_td()).total_seconds()
                // 86400) + 7,
        )
        stuck_pending_plants = (
            StuckPendingPlant(
                account_id=cust1.account_id,
                days_ago=cap_days,
                rail_name=pending_rail.name,
                amount=_plant_amount_for_rail(
                    pending_rail, Decimal("450.00"),
                ),
            ),
        )

    # X.1.i — plant a Failed leg so the L2FT Status='Other' dropdown
    # has matching seed data (the open-set status enum collapses every
    # status outside {Pending, Posted} to Other in the L2FT Rails
    # dataset SQL). Re-use the pending_rail pick — any non-aggregating
    # rail works since Failed legs have no counter-leg.
    failed_transaction_plants: tuple[FailedTransactionPlant, ...] = ()
    if pending_rail is not None:
        failed_transaction_plants = (
            FailedTransactionPlant(
                account_id=cust1.account_id,
                days_ago=2,
                rail_name=pending_rail.name,
                amount=_plant_amount_for_rail(
                    pending_rail, Decimal("75.00"),
                ),
            ),
        )
    else:
        omitted.append(("FailedTransactionPlant",
                        "no Rail declares max_pending_age (re-uses the "
                        "pending_rail pick)"))

    stuck_unbundled_plants: tuple[StuckUnbundledPlant, ...] = ()
    if unbundled_rail is not None:
        # max_unbundled_age caps vary widely (PT4H ↔ P31D); plant
        # comfortably past the cap by adding 7 days.
        cap_days = max(
            1,
            int((unbundled_rail.max_unbundled_age or _zero_td()).total_seconds()
                // 86400) + 7,
        )
        stuck_unbundled_plants = (
            StuckUnbundledPlant(
                account_id=cust2.account_id,
                days_ago=cap_days,
                rail_name=unbundled_rail.name,
                amount=_plant_amount_for_rail(
                    unbundled_rail, Decimal("12.50"),
                ),
            ),
        )

    supersession_plants: tuple[SupersessionPlant, ...] = ()
    if super_rail is not None:
        supersession_orig = _plant_amount_for_rail(
            super_rail, Decimal("250.00"),
        )
        # Corrected amount adds 10% (the "we got the amount wrong" pattern).
        supersession_corr = (
            supersession_orig * Decimal("1.10")
        ).quantize(Decimal("0.01"))
        supersession_plants = (
            SupersessionPlant(
                account_id=cust1.account_id,
                days_ago=3,
                rail_name=super_rail.name,
                original_amount=supersession_orig,
                corrected_amount=supersession_corr,
            ),
        )

    inv_fanout_plants: tuple[InvFanoutPlant, ...] = ()
    if inv_fanout_picks is not None:
        senders, fanout_rail = inv_fanout_picks
        inv_fanout_plants = (
            InvFanoutPlant(
                # cust1 is the materialized leaf-internal customer; its
                # template inherits parent_role from the picker — exactly
                # the shape the Inv matview filter requires.
                recipient_account_id=cust1.account_id,
                sender_account_ids=tuple(s.id for s in senders),
                days_ago=2,
                rail_name=fanout_rail.name,
                amount_per_transfer=Decimal("500.00"),
            ),
        )

    # M.3.10g + extension — TransferTemplate firings. For every
    # L2-declared template with ``expected_net=0`` AND a resolvable
    # first leg_rail, plant 3 firings (so three distinct shared
    # Transfers appear per template, exercising both the transfer_key
    # Metadata-grouping and the chain-completion variants).
    #
    # First leg_rail can be either ``TwoLegRail`` (debit + credit
    # balance to expected_net=0 in one firing) or ``SingleLegRail``
    # (one leg per firing; SQL completion_status surfaces these as
    # 'Imbalanced' when the leg's amount alone doesn't sum to the
    # template's expected_net — accurate L1 representation of a bare
    # single-leg cycle without its sibling/closing legs).
    tt_plants_list: list[TransferTemplatePlant] = []
    for tt in sorted(
        instance.transfer_templates, key=lambda t: str(t.name)
    ):
        if not tt.leg_rails:
            omitted.append((
                f"TransferTemplatePlant[{tt.name}]",
                "template has no leg_rails declared",
            ))
            continue
        first_rail = _resolve_rail_by_name(tt.leg_rails[0], instance)
        if tt.expected_net != Decimal("0"):
            omitted.append((
                f"TransferTemplatePlant[{tt.name}]",
                f"expected_net != 0 ({tt.expected_net}); "
                f"non-zero net plants deferred",
            ))
            continue
        if isinstance(first_rail, TwoLegRail):
            src_id = _pick_account_id_for_role_expr(
                first_rail.source_role, instance, template, cust1,
            )
            if src_id is None:
                omitted.append((
                    f"TransferTemplatePlant[{tt.name}]",
                    f"no Account or template-instance matching source_role "
                    f"{first_rail.source_role!r}",
                ))
                continue
            dst_id = _pick_account_id_for_role_expr(
                first_rail.destination_role, instance, template, cust1,
            )
            if dst_id is None:
                omitted.append((
                    f"TransferTemplatePlant[{tt.name}]",
                    f"no Account or template-instance matching destination_role "
                    f"{first_rail.destination_role!r}",
                ))
                continue
        else:
            # SingleLegRail — only the leg_role resolves; the emit reuses
            # ``source_account_id`` as the leg account and ignores
            # ``destination_account_id``.
            assert isinstance(first_rail, SingleLegRail)
            leg_id = _pick_account_id_for_role_expr(
                first_rail.leg_role, instance, template, cust1,
            )
            if leg_id is None:
                omitted.append((
                    f"TransferTemplatePlant[{tt.name}]",
                    f"no Account or template-instance matching leg_role "
                    f"{first_rail.leg_role!r}",
                ))
                continue
            src_id = leg_id
            dst_id = leg_id
        # Pre-resolve chain children for the firings of each template
        # (M.3.10h, expanded M.3.10j). Scan declared chains for entries
        # whose parent matches this template name; for each, resolve
        # the child rail + an account matching the child rail's role
        # expression. Three firings exercise three TT-instance
        # completion_status values:
        #
        #   firing 1: ALL declared children fire — XOR violation if the
        #             template's chain children are XOR-grouped (>1 in
        #             one group); shows 'Orphaned' on tt-instances.
        #   firing 2: NO chain children fire — orphan for every declared
        #             edge; shows 'Orphaned' on tt-instances.
        #   firing 3: ONLY the first declared chain child fires —
        #             satisfies XOR (exactly 1 fired) AND any single
        #             required child; shows 'Complete' on tt-instances
        #             (assuming the template has a single XOR group or
        #             a single required child as the first declared).
        all_chain_children = _pick_chain_children_for_template(
            tt.name, instance, template, cust1,
        )
        first_chain_child = all_chain_children[:1]
        for firing_seq in (1, 2, 3):
            if firing_seq == 1:
                children = all_chain_children
            elif firing_seq == 2:
                children = ()
            else:  # firing_seq == 3
                children = first_chain_child
            tt_plants_list.append(TransferTemplatePlant(
                template_name=tt.name,
                # Stagger days so the three firings spread across the
                # date window — gives the explorer something visual.
                days_ago=2 + firing_seq,
                amount=Decimal("125.00"),
                source_account_id=src_id,
                destination_account_id=dst_id,
                firing_seq=firing_seq,
                chain_children=children,
            ))
    transfer_template_plants = tuple(tt_plants_list)
    if not instance.transfer_templates:
        omitted.append((
            "TransferTemplatePlant",
            "no TransferTemplate declared in instance",
        ))

    # -- Broad-mode rail firings (M.4.2) -----------------------------
    if include_broad:
        rail_firing_plants, broad_omitted = _build_broad_rail_firings(
            instance, template, cust1,
            per_rail_firings=per_rail_firings,
        )
        omitted.extend(broad_omitted)
    else:
        rail_firing_plants = ()

    # -- Mode-aware plant assembly ----------------------------------
    # Broad-only mode zeros out the L1 SHOULD-violation tuples but keeps
    # the template instances + reference date — cust1/cust2 are still
    # the source of customer-side account ids the broad picker resolves
    # against, so they must stay in the ScenarioPlant either way.
    #
    # M.4.2a re-categorization: ``transfer_template_plants`` are
    # shape-driven ("populate the L2FT Transfer Templates sheet") not
    # invariant-violation-driven, so they belong with the broad layer
    # alongside ``rail_firing_plants``. Pure ``l1_invariants`` mode now
    # plants only the 7 SHOULD-violation kinds.
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        drift_plants=drift_plants if include_l1 else (),
        overdraft_plants=overdraft_plants if include_l1 else (),
        limit_breach_plants=limit_breach_plants if include_l1 else (),
        inbound_cap_breach_plants=inbound_cap_breach_plants if include_l1 else (),
        two_template_chain_plants=two_template_chain_plants if include_l1 else (),
        chain_parent_disagreement_plants=chain_parent_disagreement_plants if include_l1 else (),
        xor_variant_missed_firing_plants=xor_variant_missed_firing_plants if include_l1 else (),
        xor_variant_overlap_plants=xor_variant_overlap_plants if include_l1 else (),
        fan_in_chain_plants=fan_in_chain_plants if include_l1 else (),
        fan_in_chain_missing_parent_plants=fan_in_chain_missing_parent_plants if include_l1 else (),
        fan_in_chain_extra_parent_plants=fan_in_chain_extra_parent_plants if include_l1 else (),
        multi_xor_missed_plants=multi_xor_missed_plants if include_l1 else (),
        multi_xor_overlap_plants=multi_xor_overlap_plants if include_l1 else (),
        stuck_pending_plants=stuck_pending_plants if include_l1 else (),
        failed_transaction_plants=failed_transaction_plants if include_l1 else (),
        stuck_unbundled_plants=stuck_unbundled_plants if include_l1 else (),
        supersession_plants=supersession_plants if include_l1 else (),
        transfer_template_plants=transfer_template_plants if include_broad else (),
        rail_firing_plants=rail_firing_plants,
        inv_fanout_plants=inv_fanout_plants if include_l1 else (),
        today=today_ref,
    )
    return AutoScenarioReport(scenario=scenario, omitted=tuple(omitted))


# -- Phase R density tuning helpers ------------------------------------------


def densify_scenario(
    base: ScenarioPlant,
    *,
    factor: int = 5,
    day_stride: int = 7,
) -> ScenarioPlant:
    """Replicate per-kind plants across the window for visibility (R.3.b).

    The R.2 baseline puts ~60k legs per L2 instance into the window;
    a single drift / overdraft / etc plant gets lost in the noise.
    This helper takes a base ``ScenarioPlant`` (typically from
    ``default_scenario_for``) and replicates each plant kind by
    varying ``days_ago`` so each kind shows N rows on the dashboards
    instead of 1.

    For stuck-pending / stuck-unbundled, the days_ago stride keeps
    every replica well past the rail's max_*_age cap so all replicas
    surface. For drift / overdraft / breach / supersession, the
    stride spreads them across the window for visual diversity.

    ``inv_fanout_plants`` and ``transfer_template_plants`` are NOT
    replicated — the fanout already plants N senders per recipient
    (its own density), and TransferTemplate plants already produce 3
    firings per template (the Complete / Orphan / Required-met cases).
    """
    if factor <= 1:
        return base

    def replicate_drift(p: DriftPlant) -> tuple[DriftPlant, ...]:
        return tuple(
            DriftPlant(
                account_id=p.account_id,
                days_ago=p.days_ago + i * day_stride,
                delta_money=p.delta_money,
                rail_name=p.rail_name,
                counter_account_id=p.counter_account_id,
            )
            for i in range(factor)
        )

    def replicate_overdraft(p: OverdraftPlant) -> tuple[OverdraftPlant, ...]:
        return tuple(
            OverdraftPlant(
                account_id=p.account_id,
                days_ago=p.days_ago + i * day_stride,
                money=p.money,
            )
            for i in range(factor)
        )

    def replicate_breach(p: LimitBreachPlant) -> tuple[LimitBreachPlant, ...]:
        return tuple(
            LimitBreachPlant(
                account_id=p.account_id,
                days_ago=p.days_ago + i * day_stride,
                rail_name=p.rail_name,
                amount=p.amount,
                counter_account_id=p.counter_account_id,
            )
            for i in range(factor)
        )

    def replicate_inbound_breach(
        p: InboundCapBreachPlant,
    ) -> tuple[InboundCapBreachPlant, ...]:
        return tuple(
            InboundCapBreachPlant(
                account_id=p.account_id,
                days_ago=p.days_ago + i * day_stride,
                rail_name=p.rail_name,
                amount=p.amount,
                counter_account_id=p.counter_account_id,
            )
            for i in range(factor)
        )

    def replicate_pending(
        p: StuckPendingPlant,
    ) -> tuple[StuckPendingPlant, ...]:
        return tuple(
            StuckPendingPlant(
                account_id=p.account_id,
                days_ago=p.days_ago + i * day_stride,
                rail_name=p.rail_name,
                amount=p.amount,
            )
            for i in range(factor)
        )

    def replicate_unbundled(
        p: StuckUnbundledPlant,
    ) -> tuple[StuckUnbundledPlant, ...]:
        return tuple(
            StuckUnbundledPlant(
                account_id=p.account_id,
                days_ago=p.days_ago + i * day_stride,
                rail_name=p.rail_name,
                amount=p.amount,
            )
            for i in range(factor)
        )

    def replicate_super(
        p: SupersessionPlant,
    ) -> tuple[SupersessionPlant, ...]:
        return tuple(
            SupersessionPlant(
                account_id=p.account_id,
                days_ago=p.days_ago + i * day_stride,
                rail_name=p.rail_name,
                original_amount=p.original_amount,
                corrected_amount=p.corrected_amount,
            )
            for i in range(factor)
        )

    return ScenarioPlant(
        template_instances=base.template_instances,
        drift_plants=tuple(
            r for p in base.drift_plants for r in replicate_drift(p)
        ),
        overdraft_plants=tuple(
            r for p in base.overdraft_plants for r in replicate_overdraft(p)
        ),
        limit_breach_plants=tuple(
            r for p in base.limit_breach_plants for r in replicate_breach(p)
        ),
        inbound_cap_breach_plants=tuple(
            r for p in base.inbound_cap_breach_plants
            for r in replicate_inbound_breach(p)
        ),
        # AB.2.6 — chain plants pass through un-replicated. One healthy
        # + one disagreement row per L2 is enough for dashboard / matview
        # coverage; multiplying noise legs doesn't add new shape coverage.
        two_template_chain_plants=base.two_template_chain_plants,
        chain_parent_disagreement_plants=base.chain_parent_disagreement_plants,
        xor_variant_missed_firing_plants=base.xor_variant_missed_firing_plants,
        xor_variant_overlap_plants=base.xor_variant_overlap_plants,
        fan_in_chain_plants=base.fan_in_chain_plants,
        fan_in_chain_missing_parent_plants=base.fan_in_chain_missing_parent_plants,
        fan_in_chain_extra_parent_plants=base.fan_in_chain_extra_parent_plants,
        multi_xor_missed_plants=base.multi_xor_missed_plants,
        multi_xor_overlap_plants=base.multi_xor_overlap_plants,
        stuck_pending_plants=tuple(
            r for p in base.stuck_pending_plants for r in replicate_pending(p)
        ),
        # X.1.i — failed_transaction_plants pass through un-replicated
        # (one Failed leg per scenario is enough for the dropdown to
        # have data; multiplying noise legs across the window doesn't
        # add visibility on the same operator surface).
        failed_transaction_plants=base.failed_transaction_plants,
        stuck_unbundled_plants=tuple(
            r for p in base.stuck_unbundled_plants
            for r in replicate_unbundled(p)
        ),
        supersession_plants=tuple(
            r for p in base.supersession_plants for r in replicate_super(p)
        ),
        transfer_template_plants=base.transfer_template_plants,
        rail_firing_plants=base.rail_firing_plants,
        inv_fanout_plants=base.inv_fanout_plants,
        today=base.today,
    )


def boost_inv_fanout_plants(
    base: ScenarioPlant,
    *,
    amount_multiplier: int = 5,
    extra_recipient_count: int = 0,
) -> ScenarioPlant:
    """Tune Investigation fanout plants for visibility (R.3.d).

    The Phase R baseline puts ~600 customer-ACH transfers per day into
    the system at median ~$665 per transfer. The default
    ``InvFanoutPlant.amount_per_transfer = $500`` from the auto-scenario
    sits BELOW the baseline median — its cluster is structurally
    visible (12 senders → 1 recipient) but per-transfer amounts don't
    stand out.

    This helper bumps each inv_fanout plant's amount by
    ``amount_multiplier`` (5× default → $2,500 per transfer) so the
    cluster's aggregate inflow (~$30,000 across 12 senders in one day)
    stands out clearly on the Recipient Fanout sheet's Sankey + the
    Volume Anomalies sheet's z-score band.

    Optional ``extra_recipient_count``: synthesize N extra fanout
    plants targeting different recipients (cycles through the existing
    template instances) so multiple clusters appear on the dashboards.
    Out of scope for the first land — defaults to 0.
    """
    if not base.inv_fanout_plants or amount_multiplier <= 1:
        return base

    boosted = tuple(
        InvFanoutPlant(
            recipient_account_id=p.recipient_account_id,
            sender_account_ids=p.sender_account_ids,
            days_ago=p.days_ago,
            rail_name=p.rail_name,
            amount_per_transfer=p.amount_per_transfer * amount_multiplier,
        )
        for p in base.inv_fanout_plants
    )

    _ = extra_recipient_count  # reserved for future expansion

    return ScenarioPlant(
        template_instances=base.template_instances,
        drift_plants=base.drift_plants,
        overdraft_plants=base.overdraft_plants,
        limit_breach_plants=base.limit_breach_plants,
        inbound_cap_breach_plants=base.inbound_cap_breach_plants,
        two_template_chain_plants=base.two_template_chain_plants,
        chain_parent_disagreement_plants=base.chain_parent_disagreement_plants,
        xor_variant_missed_firing_plants=base.xor_variant_missed_firing_plants,
        xor_variant_overlap_plants=base.xor_variant_overlap_plants,
        fan_in_chain_plants=base.fan_in_chain_plants,
        fan_in_chain_missing_parent_plants=base.fan_in_chain_missing_parent_plants,
        fan_in_chain_extra_parent_plants=base.fan_in_chain_extra_parent_plants,
        multi_xor_missed_plants=base.multi_xor_missed_plants,
        multi_xor_overlap_plants=base.multi_xor_overlap_plants,
        stuck_pending_plants=base.stuck_pending_plants,
        failed_transaction_plants=base.failed_transaction_plants,
        stuck_unbundled_plants=base.stuck_unbundled_plants,
        supersession_plants=base.supersession_plants,
        transfer_template_plants=base.transfer_template_plants,
        rail_firing_plants=base.rail_firing_plants,
        inv_fanout_plants=boosted,
        today=base.today,
    )


def add_broken_rail_plants(
    base: ScenarioPlant,
    instance: L2Instance,
    *,
    broken_count: int = 15,
) -> ScenarioPlant:
    """Layer a single broken-Rail spike on top of an existing scenario (R.3.c).

    Picks one Rail with ``max_pending_age`` set + plants
    ``broken_count`` stuck_pending entries on it across the window.
    Today's Exceptions KPI then has a magnitude that matters; the
    L2 Exceptions sheet's bar chart shows the broken Rail spike
    immediately.

    Picker rule: deterministic — sorted by rail name, the FIRST rail
    with max_pending_age set. Different from
    ``default_scenario_for``'s pending_rail picker by intent — the
    broken rail is a separate concept; using the same picker would
    just stack plants on the existing stuck_pending row.

    No-op when no max_pending_age-eligible rail exists OR the picked
    rail's role doesn't resolve to any materialized account.
    """
    if broken_count <= 0:
        return base

    pending_rails = sorted(
        (r for r in instance.rails if r.max_pending_age is not None),
        key=lambda r: str(r.name),
    )
    if not pending_rails:
        return base
    broken_rail = pending_rails[0]

    # Pick a customer to plant on. Use the first template instance
    # whose role matches the rail's leg/source role.
    if not base.template_instances:
        return base
    target_account_id = base.template_instances[0].account_id

    # Days_ago stride: stagger across the window past the rail's cap.
    cap_days = max(
        1,
        int((broken_rail.max_pending_age or _zero_td()).total_seconds() // 86400) + 7,
    )

    extra_plants = tuple(
        StuckPendingPlant(
            account_id=base.template_instances[
                i % len(base.template_instances)
            ].account_id,
            days_ago=cap_days + (i * 2),  # spread across the window
            rail_name=broken_rail.name,
            amount=Decimal("450.00") + Decimal(str(i * 25)),
        )
        for i in range(broken_count)
    )
    _ = target_account_id  # shadowed by per-i picker below

    return ScenarioPlant(
        template_instances=base.template_instances,
        drift_plants=base.drift_plants,
        overdraft_plants=base.overdraft_plants,
        limit_breach_plants=base.limit_breach_plants,
        inbound_cap_breach_plants=base.inbound_cap_breach_plants,
        two_template_chain_plants=base.two_template_chain_plants,
        chain_parent_disagreement_plants=base.chain_parent_disagreement_plants,
        xor_variant_missed_firing_plants=base.xor_variant_missed_firing_plants,
        xor_variant_overlap_plants=base.xor_variant_overlap_plants,
        fan_in_chain_plants=base.fan_in_chain_plants,
        fan_in_chain_missing_parent_plants=base.fan_in_chain_missing_parent_plants,
        fan_in_chain_extra_parent_plants=base.fan_in_chain_extra_parent_plants,
        multi_xor_missed_plants=base.multi_xor_missed_plants,
        multi_xor_overlap_plants=base.multi_xor_overlap_plants,
        stuck_pending_plants=base.stuck_pending_plants + extra_plants,
        failed_transaction_plants=base.failed_transaction_plants,
        stuck_unbundled_plants=base.stuck_unbundled_plants,
        supersession_plants=base.supersession_plants,
        transfer_template_plants=base.transfer_template_plants,
        rail_firing_plants=base.rail_firing_plants,
        inv_fanout_plants=base.inv_fanout_plants,
        today=base.today,
    )


# -- X.4.h.0.a — Plant-kind filter (data-shaping panel knob) ----------------


def filter_scenario_plants(
    base: ScenarioPlant,
    kinds: tuple[str, ...] | None,
) -> ScenarioPlant:
    """Return a copy of ``base`` keeping only the requested L1 plant kinds.

    The data-shaping panel's plant-toggle checkboxes (X.4.h.2) write a
    subset of the ``PlantKind`` enum into ``cfg.test_generator.plants``;
    this is the projection that consumes that subset. Per SPEC's
    "Data-shaping model / plants" section: ``None`` or empty tuple
    ⇒ "all kinds" (today's behavior), so the absent-block case stays
    byte-identical to the locked seeds.

    Only the 6 L1-invariant plant kinds in ``PlantKind`` are gated:
    drift / overdraft / limit_breach / stuck_pending / stuck_unbundled
    / supersession. The other plant tuples on ``ScenarioPlant`` —
    ``failed_transaction_plants`` (X.1.i — separate Failed-status
    fixture), ``transfer_template_plants``, ``rail_firing_plants``,
    ``inv_fanout_plants`` — are L2-shape / Investigation fixtures, not
    L1 SHOULD-violations, and pass through unchanged. Same with
    ``template_instances`` (the customer materialization, needed by
    every plant kind that references customer-side accounts) and
    ``today`` (the reference date).
    """
    if not kinds:
        return base
    selected = frozenset(kinds)

    return ScenarioPlant(
        template_instances=base.template_instances,
        drift_plants=base.drift_plants if "drift" in selected else (),
        overdraft_plants=base.overdraft_plants if "overdraft" in selected else (),
        limit_breach_plants=base.limit_breach_plants if "limit_breach" in selected else (),
        inbound_cap_breach_plants=base.inbound_cap_breach_plants if "limit_breach" in selected else (),
        two_template_chain_plants=base.two_template_chain_plants if "chain_parent_disagreement" in selected else (),
        chain_parent_disagreement_plants=base.chain_parent_disagreement_plants if "chain_parent_disagreement" in selected else (),
        xor_variant_missed_firing_plants=base.xor_variant_missed_firing_plants if "xor_group_violation" in selected else (),
        xor_variant_overlap_plants=base.xor_variant_overlap_plants if "xor_group_violation" in selected else (),
        fan_in_chain_plants=base.fan_in_chain_plants if "fan_in_disagreement" in selected else (),
        fan_in_chain_missing_parent_plants=base.fan_in_chain_missing_parent_plants if "fan_in_disagreement" in selected else (),
        fan_in_chain_extra_parent_plants=base.fan_in_chain_extra_parent_plants if "fan_in_disagreement" in selected else (),
        multi_xor_missed_plants=base.multi_xor_missed_plants if "multi_xor_violation" in selected else (),
        multi_xor_overlap_plants=base.multi_xor_overlap_plants if "multi_xor_violation" in selected else (),
        stuck_pending_plants=base.stuck_pending_plants if "stuck_pending" in selected else (),
        failed_transaction_plants=base.failed_transaction_plants,
        stuck_unbundled_plants=base.stuck_unbundled_plants if "stuck_unbundled" in selected else (),
        supersession_plants=base.supersession_plants if "supersession" in selected else (),
        transfer_template_plants=base.transfer_template_plants,
        rail_firing_plants=base.rail_firing_plants,
        inv_fanout_plants=base.inv_fanout_plants,
        today=base.today,
    )


# -- Picker helpers ----------------------------------------------------------


def _build_broad_rail_firings(
    instance: L2Instance,
    template: AccountTemplate,
    customer_instance: TemplateInstance,
    *,
    per_rail_firings: int,
) -> tuple[tuple[RailFiringPlant, ...], list[tuple[str, str]]]:
    """Generate per-rail firings for every Rail with materialized accounts.

    M.4.2 broad-mode plant generator. Walks every declared Rail; for
    each, resolves source/destination/leg roles to materialized
    accounts (singletons OR template instances). Rails whose role(s)
    can't be resolved are SKIPPED (no synthetic-account fallback per
    PLAN's M.4.2 cleanups — production behavior should reflect what
    the L2 actually wires up).

    For each surviving rail, plants ``per_rail_firings`` firings, each
    on a distinct ``days_ago`` so timestamps spread across the date
    window — the L2 Flow Tracing Rails / Chains explorers look more
    realistic when activity isn't all stacked on one day.

    For Required chain entries, after generating parent firings, this
    helper also plants ONE child firing per chain entry whose
    ``transfer_parent_id`` references one of the parent's firings — so
    the L2 chain-orphan invariant view sees a matched pair on the L2
    Exceptions sheet's Chain Orphans check.

    Returns ``(rail_firing_plants, omitted_reasons)`` where
    ``omitted_reasons`` documents per-rail skip reasons for the
    AutoScenarioReport's diagnostics.
    """
    omitted: list[tuple[str, str]] = []
    plants: list[RailFiringPlant] = []
    rail_to_transfer_seq_starts: dict[Identifier, int] = {}

    seq_counter = 0
    for rail in sorted(instance.rails, key=lambda r: str(r.name)):
        # Pull leg roles per rail shape.
        if isinstance(rail, TwoLegRail):
            src_id = _pick_account_id_for_role_expr(
                rail.source_role, instance, template, customer_instance,
            )
            dst_id = _pick_account_id_for_role_expr(
                rail.destination_role, instance, template, customer_instance,
            )
            if src_id is None or dst_id is None:
                missing: list[str] = []
                if src_id is None:
                    missing.append(f"source_role={rail.source_role!r}")
                if dst_id is None:
                    missing.append(f"destination_role={rail.destination_role!r}")
                omitted.append((
                    f"RailFiringPlant[{rail.name}]",
                    f"no materialized account for {', '.join(missing)}",
                ))
                continue
            account_ids: tuple[Identifier, Identifier | None] = (src_id, dst_id)
        else:
            # SingleLegRail (the discriminated union's only other arm).
            leg_id = _pick_account_id_for_role_expr(
                rail.leg_role, instance, template, customer_instance,
            )
            if leg_id is None:
                omitted.append((
                    f"RailFiringPlant[{rail.name}]",
                    f"no materialized account for leg_role={rail.leg_role!r}",
                ))
                continue
            account_ids = (leg_id, None)

        # Resolve which TransferTemplate this rail belongs to (M.4.2a).
        # ``template_name`` lets the L2FT Transfer Templates sheet's
        # tt-instances + tt-legs datasets see broad rail firings of
        # leg_rails as ad-hoc legs of the template they belong to,
        # alongside the structured TransferTemplatePlant firings.
        # When a rail appears in multiple templates' leg_rails (rare
        # but legal per SPEC), pick the first by name for determinism.
        containing_templates = sorted(
            (
                tt.name for tt in instance.transfer_templates
                if rail.name in tt.leg_rails
            ),
            key=str,
        )
        template_name_for_rail: Identifier | None = (
            containing_templates[0] if containing_templates else None
        )

        # Build extra_metadata for non-TransferKey fields. Per PLAN
        # cleanup: respect the rail's declared metadata_keys; values
        # are per-(rail, firing) unique so the L2 Flow Tracing
        # metadata cascade reads distinct values.
        # TransferKey fields auto-derived inside the emit helper, so
        # don't double-populate here — exclude any key that's a
        # transfer_key field on a containing template.
        tt_keys: set[Identifier] = set()
        for tt in instance.transfer_templates:
            if rail.name in tt.leg_rails:
                tt_keys.update(tt.transfer_key)

        # Per-key example values (M.4.2b). When a rail's metadata_keys
        # entry has examples declared, the seed cycles through them
        # by firing seq. When absent, fall back to the synthetic
        # `<rail>-firing-<seq>` pattern so existing fixtures don't
        # drift unless they opt in.
        examples_by_key: dict[Identifier, tuple[str, ...]] = dict(
            rail.metadata_value_examples,
        )

        rail_to_transfer_seq_starts[rail.name] = seq_counter + 1
        for firing_seq in range(1, per_rail_firings + 1):
            seq_counter += 1
            extra: tuple[tuple[str, str], ...] = tuple(
                (
                    str(k),
                    _pick_metadata_value(
                        examples=examples_by_key.get(k),
                        rail_name=rail.name,
                        firing_seq=firing_seq,
                    ),
                )
                for k in rail.metadata_keys
                if k not in tt_keys
            )
            plants.append(RailFiringPlant(
                rail_name=rail.name,
                # Stratify days: firing 1 → days_ago=1, firing 2 → 2, …
                # within a 7-day window for realism. Wraps at 7 if
                # per_rail_firings exceeds the window.
                days_ago=1 + ((firing_seq - 1) % 7),
                firing_seq=firing_seq,
                amount=Decimal("100.00"),
                account_id_a=account_ids[0],
                account_id_b=account_ids[1],
                extra_metadata=extra,
                template_name=template_name_for_rail,
            ))

    # Required chain children — pair child firings to parent firings.
    # The picker walks chains in declaration order; for each
    # singleton-children chain (Z.A "required" semantics) whose parent
    # fired (rail_to_transfer_seq_starts has an entry) AND whose child
    # fired, plant ONE additional child firing whose
    # transfer_parent_id matches the FIRST parent firing's
    # transfer_id. The transfer_id pattern is ``tr-rail-<seq:04d>``
    # per the seed.py emit helper's convention. Multi-children
    # (XOR) chains are skipped here — the seed.py XOR picker chooses
    # one sibling per parent firing via hash, which the planted
    # plants can't pre-resolve deterministically without recomputing
    # that hash.
    chain_seq_offset = seq_counter
    chain_link_count = 0
    for chain in instance.chains:
        if len(chain.children) != 1:
            continue
        parent_starts = rail_to_transfer_seq_starts.get(
            Identifier(str(chain.parent)),
        )
        if parent_starts is None:
            continue  # parent didn't fire
        child_rail = _resolve_rail_by_name(
            Identifier(str(chain.children[0].name)), instance,
        )
        if child_rail is None or child_rail.aggregating:
            continue
        # Resolve child rail's accounts.
        if isinstance(child_rail, TwoLegRail):
            src_id = _pick_account_id_for_role_expr(
                child_rail.source_role, instance, template, customer_instance,
            )
            dst_id = _pick_account_id_for_role_expr(
                child_rail.destination_role, instance, template, customer_instance,
            )
            if src_id is None or dst_id is None:
                continue
            child_account_ids: tuple[Identifier, Identifier | None] = (src_id, dst_id)
        else:
            # SingleLegRail.
            leg_id = _pick_account_id_for_role_expr(
                child_rail.leg_role, instance, template, customer_instance,
            )
            if leg_id is None:
                continue
            child_account_ids = (leg_id, None)

        chain_seq_offset += 1
        chain_link_count += 1
        # The parent rail's first firing's transfer_id is at
        # tr-rail-<parent_starts:04d>. Bind the child to it via
        # the dedicated transfer_parent_id field on the plant.
        parent_transfer_id = f"tr-rail-{parent_starts:04d}"
        # Build extra_metadata for the child like above.
        child_tt_keys: set[Identifier] = set()
        for tt in instance.transfer_templates:
            if child_rail.name in tt.leg_rails:
                child_tt_keys.update(tt.transfer_key)
        child_extra: tuple[tuple[str, str], ...] = tuple(
            (str(k), f"{child_rail.name}-chained-{chain_link_count:04d}")
            for k in child_rail.metadata_keys
            if k not in child_tt_keys
        )
        plants.append(RailFiringPlant(
            rail_name=child_rail.name,
            # Place chain children one day before the chain reference
            # window so they sort after their parents in the date axis.
            days_ago=1,
            firing_seq=per_rail_firings + chain_link_count,
            amount=Decimal("100.00"),
            account_id_a=child_account_ids[0],
            account_id_b=child_account_ids[1],
            transfer_parent_id=parent_transfer_id,
            extra_metadata=child_extra,
        ))

    if not plants:
        omitted.append((
            "RailFiringPlant",
            "no rails resolved to materialized accounts",
        ))
    return tuple(plants), omitted


def _pick_template(instance: L2Instance) -> AccountTemplate | None:
    """First AccountTemplate sorted by role name; None if none declared."""
    if not instance.account_templates:
        return None
    return sorted(instance.account_templates, key=lambda t: str(t.role))[0]


_TEMPLATE_INSTANCE_NS: tuple[int, int] = (1, 2)
"""The ``n`` values the seed materializes per AccountTemplate.

Hardcoded at 2 instances (n=1, n=2) since M.4.2b. Validator U7 walks
the same range to enumerate template-generated IDs — keeping both
consumers reading this constant prevents the validator from missing
collisions if the seed ever expands the range.
"""


def template_instance_ids(template: AccountTemplate) -> tuple[str, ...]:
    """The account_ids this template will materialize during seed.

    Public surface for the validator (U7 — collision with singleton
    Account.id) so the validator never replicates the rendering rule.
    Resolves the same template-rendering path the seed uses inside
    ``_materialize_instances`` — change one, the other follows.
    """
    return tuple(
        _render_template_field(
            template.instance_id_template,
            fallback=f"cust-{n:03d}",
            template=template,
            n=n,
        )
        for n in _TEMPLATE_INSTANCE_NS
    )


def _materialize_instances(
    template: AccountTemplate,
) -> tuple[TemplateInstance, TemplateInstance]:
    """Synthesize 2 customer instances under the template.

    M.4.2b: when the template declares ``instance_id_template`` /
    ``instance_name_template`` (both optional), the seed uses those
    format strings to render persona-aware identifiers. When unset,
    falls back to the legacy synthetic patterns ``cust-{n:03d}`` +
    ``Customer {n}`` so existing L2 fixtures don't drift their
    seed_hash.

    IDs come from :func:`template_instance_ids` so the validator's
    U7 collision check sees exactly what the seed will plant.
    """
    ids = template_instance_ids(template)
    return tuple(
        TemplateInstance(
            template_role=template.role,
            account_id=Identifier(account_id),
            name=Name(_render_template_field(
                template.instance_name_template,
                fallback=f"Customer {n}",
                template=template,
                n=n,
            )),
        )
        for n, account_id in zip(_TEMPLATE_INSTANCE_NS, ids, strict=True)
    )  # type: ignore[return-value]: tuple-of-2 narrowed at runtime; declared return is tuple[T, T]


def _render_template_field(
    fmt: str | None,
    *,
    fallback: str,
    template: AccountTemplate,
    n: int,
) -> str:
    """Apply an instance display template, or use the fallback (M.4.2b).

    The format string is loader-validated to reference only ``{role}``
    and ``{n}``; any KeyError here would be a loader bug.
    """
    if fmt is None:
        return fallback
    return fmt.format(role=str(template.role), n=n)


def _pick_metadata_value(
    *,
    examples: tuple[str, ...] | None,
    rail_name: Identifier,
    firing_seq: int,
) -> str:
    """Pick a metadata value for a (rail, key, firing) triple (M.4.2b).

    When ``examples`` is set, cycle through them by ``firing_seq``
    (modular indexing so per_rail_firings can exceed list length
    without IndexError). When unset, fall back to the original
    synthetic ``<rail>-firing-<seq>`` pattern so existing fixtures
    don't drift unless they opt into example values.
    """
    if examples:
        return examples[(firing_seq - 1) % len(examples)]
    return f"{rail_name}-firing-{firing_seq:04d}"


def _pick_inbound_2leg_rail(
    instance: L2Instance, template_role: Identifier,
) -> TwoLegRail | None:
    """First TwoLegRail (sorted by name) whose destination_role includes
    the template role — i.e., money flows INTO the customer."""
    candidates = [
        r for r in instance.rails
        if isinstance(r, TwoLegRail)
        and template_role in r.destination_role
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: str(r.name))[0]


def _pick_outbound_2leg_rail(
    instance: L2Instance,
    template_role: Identifier,
    rail_name: Identifier,
) -> TwoLegRail | None:
    """First TwoLegRail (sorted by name) with source_role=template AND
    matching rail name AND a destination role that resolves to an
    external Account.

    Z.B (2026-05-15): formerly matched on transfer_type; under the
    symmetric collapse a rail's name IS its type identifier.
    """
    external_roles = {a.role for a in instance.accounts if a.scope == "external"}
    for r in sorted(instance.rails, key=lambda r: str(r.name)):
        if not isinstance(r, TwoLegRail):
            continue
        if r.name != rail_name:
            continue
        if template_role not in r.source_role:
            continue
        if any(role in external_roles for role in r.destination_role):
            return r
    return None


def _pick_external_counter_for_rail(
    instance: L2Instance, rail: TwoLegRail,
) -> Account | None:
    """Find an external-scope Account whose role appears in the rail's
    counter side. For inbound rails, counter = source. Sorted by id."""
    candidate_roles = set(rail.source_role)
    candidates = [
        a for a in instance.accounts
        if a.scope == "external" and a.role in candidate_roles
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda a: str(a.id))[0]


def _pick_external_counter_for_outbound(
    instance: L2Instance, rail: TwoLegRail,
) -> Account | None:
    """For outbound rails, counter = destination."""
    candidate_roles = set(rail.destination_role)
    candidates = [
        a for a in instance.accounts
        if a.scope == "external" and a.role in candidate_roles
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda a: str(a.id))[0]


def _resolve_rail_by_name(
    rail_name: Identifier, instance: L2Instance,
) -> Rail | None:
    """Find the L2-declared Rail by name; None on miss. Used by the TT
    picker when validating a template's first leg_rail is a TwoLegRail.
    """
    for r in instance.rails:
        if r.name == rail_name:
            return r
    return None


def _pick_chain_children_for_template(
    template_name: Identifier,
    instance: L2Instance,
    template: AccountTemplate,
    customer_instance: TemplateInstance,
) -> tuple[tuple[Identifier, Identifier], ...]:
    """Pre-resolve chain-child (rail_name, account_id) pairs for a TT
    plant's first firing (M.3.10h).

    For each declared Chain row whose parent matches the template
    name, resolve every child rail in ``chain.children`` (must exist
    in instance.rails) and pick an account by the child rail's role
    expression. Aggregating rails are skipped — they don't have
    per-Transfer parents.

    Returns the pairs in declaration order; entries that can't resolve
    to a rail or an account are silently skipped (the chain
    detection just doesn't see a matched child for them, which
    naturally surfaces as an orphan in the dashboard).
    """
    pairs: list[tuple[Identifier, Identifier]] = []
    for chain in instance.chains:
        if chain.parent != template_name:
            continue
        # Z.A: walk every child in the row (singleton OR XOR sibling).
        # The chain-detection SQL just checks rail_name + matching
        # transfer_parent_id, so over-listing XOR siblings is fine —
        # the assertion is "any one of these landed", not "exactly
        # this one".
        for child_spec in chain.children:
            child_name = child_spec.name
            child_rail = _resolve_rail_by_name(child_name, instance)
            if child_rail is None:
                continue
            # Aggregating rails sweep on cadence, not per-Transfer —
            # they MUST NOT appear as chain children per SPEC. The
            # validator enforces this at L2 load time, but a
            # defensive skip here also avoids planting a chain child
            # that can't legitimately exist in the data.
            if child_rail.aggregating:
                continue
            # Pick the role expression from the child rail's leg side
            # most likely to surface in the data. For a TwoLegRail use
            # destination_role (where money lands — the receiving
            # party's account); for a SingleLegRail use leg_role.
            if isinstance(child_rail, TwoLegRail):
                role_expr = child_rail.destination_role
            else:
                role_expr = child_rail.leg_role
            account_id = _pick_account_id_for_role_expr(
                role_expr, instance, template, customer_instance,
            )
            if account_id is None:
                # Fallback: child rail's role might be an
                # unmaterialized account-template role (e.g.
                # MerchantDDA when only CustomerDDA is materialized).
                # The chain detection SQL only checks rail_name +
                # transfer_parent_id, not account roles, so any
                # account works for the test. Land the leg on the
                # customer instance so it's at least observable in
                # the data.
                account_id = customer_instance.account_id
            pairs.append((child_name, account_id))
    return tuple(pairs)


def _pick_account_id_for_role_expr(
    role_expr: RoleExpression,
    instance: L2Instance,
    template: AccountTemplate,
    customer_instance: TemplateInstance,
) -> Identifier | None:
    """Pick an account_id whose role matches one of the role expression's
    members (M.3.10g TT plant picker).

    Resolution order — first match wins:

    1. If the role matches the customer template's role, use the
       materialized customer (so a CustomerDDA-side leg lands on a real
       customer instance, not a synthetic singleton).
    2. Any L2 Account whose role appears in the role expression,
       sorted by id for determinism.

    Returns ``None`` if no candidate exists. The caller treats that
    as "omit this plant".
    """
    candidate_roles = set(role_expr)
    if template.role in candidate_roles:
        return customer_instance.account_id
    candidates = [
        a for a in instance.accounts if a.role in candidate_roles
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda a: str(a.id))[0].id


def _pick_breach_inputs(
    instance: L2Instance, template_role: Identifier,
) -> tuple[LimitSchedule, TwoLegRail, Account] | None:
    """Find a (LimitSchedule, outbound Rail, external Account) triple
    suitable for a LimitBreachPlant. Only considers Outbound-direction
    LimitSchedules. Sorted by LimitSchedule key."""
    for ls in sorted(
        (ls for ls in instance.limit_schedules if ls.direction == "Outbound"),
        key=lambda ls: (str(ls.parent_role), str(ls.rail)),
    ):
        rail = _pick_outbound_2leg_rail(instance, template_role, Identifier(str(ls.rail)))
        if rail is None:
            continue
        counter = _pick_external_counter_for_outbound(instance, rail)
        if counter is None:
            continue
        return (ls, rail, counter)
    return None


def _pick_inbound_breach_inputs(
    instance: L2Instance, template_role: Identifier,
) -> tuple[LimitSchedule, TwoLegRail, Account] | None:
    """AB.1 mirror of :func:`_pick_breach_inputs`: find a
    (LimitSchedule, inbound Rail, external Account) triple suitable for
    an InboundCapBreachPlant. Only considers Inbound-direction
    LimitSchedules. Sorted by LimitSchedule key for determinism.

    Inbound rail = TwoLegRail whose ``destination_role`` includes the
    customer template_role (money flows IN to the customer). Counter =
    external Account on the rail's ``source_role`` side (the funds
    source).
    """
    for ls in sorted(
        (ls for ls in instance.limit_schedules if ls.direction == "Inbound"),
        key=lambda ls: (str(ls.parent_role), str(ls.rail)),
    ):
        # Inbound: match rail by name AND destination includes template.
        rail_target = Identifier(str(ls.rail))
        candidates = [
            r for r in instance.rails
            if isinstance(r, TwoLegRail)
            and r.name == rail_target
            and template_role in r.destination_role
        ]
        if not candidates:
            continue
        rail = sorted(candidates, key=lambda r: str(r.name))[0]
        counter = _pick_external_counter_for_rail(instance, rail)
        if counter is None:
            continue
        return (ls, rail, counter)
    return None


def _pick_first_with(
    items: Iterable[Rail], *, key: Callable[[Rail], bool],
) -> Rail | None:
    """First Rail satisfying ``key(rail)``; sorted by name for determinism."""
    matching = [r for r in items if key(r)]
    if not matching:
        return None
    return sorted(matching, key=lambda r: str(r.name))[0]


def _pick_inv_fanout_inputs(
    instance: L2Instance, template_role: Identifier,
) -> tuple[tuple[Account, ...], Rail] | None:
    """Pick (senders, rail) for an Investigation fanout plant.

    Strategy: senders are the first 3 (sorted by id) Accounts that AREN'T
    the customer template's role — every fanout edge must have a distinct
    src/dst, otherwise the matview's pair-rolling aggregation conflates
    self-edges. The rail is the first 2-leg inbound rail (same one drift
    uses) — its transfer_type tags the planted legs so a Volume Anomalies
    sheet filtered by transfer_type still sees them.

    Returns None when fewer than 2 sender candidates exist OR no inbound
    2-leg rail is declared — the picker omits the plant rather than emit
    a degenerate fanout.
    """
    fanout_rail = _pick_inbound_2leg_rail(instance, template_role)
    if fanout_rail is None:
        return None
    candidates = sorted(
        (a for a in instance.accounts if a.role != template_role),
        key=lambda a: str(a.id),
    )
    if len(candidates) < 2:
        return None
    senders = tuple(candidates[:3])
    return (senders, fanout_rail)


def _pick_supersession_rail(
    instance: L2Instance, template_role: Identifier,
) -> Rail | None:
    """A rail whose customer-side leg is the template role.

    Single-leg rails: leg_role = template. Two-leg rails: source or
    destination = template. First by name.
    """
    for r in sorted(instance.rails, key=lambda r: str(r.name)):
        if isinstance(r, SingleLegRail) and template_role in r.leg_role:
            return r
        if isinstance(r, TwoLegRail) and (
            template_role in r.source_role
            or template_role in r.destination_role
        ):
            return r
    return None


def _pick_two_template_chain_inputs(
    instance: L2Instance,
) -> tuple[Identifier, Identifier] | None:
    """AB.2.6 picker: find any Chain whose singleton child is a
    TransferTemplate (the template-as-chain-child shape, gap doc §3).

    Returns ``(parent_rail_name, child_template_name)`` if found, else
    ``None``. Restricts to singleton-children chains because Z.A's XOR
    semantics on multi-children chains make parent_transfer_id optional
    per leg (which would make the AB.2.6 plant probabilistic rather
    than deterministic). The parent MUST resolve to a Rail (not a
    TransferTemplate) — two-template chains where BOTH ends are
    templates are valid but produce nested-firing semantics out of
    scope for the AB.2 plant scaffold.
    """
    template_names = {t.name for t in instance.transfer_templates}
    rail_names = {r.name for r in instance.rails}
    for c in sorted(
        instance.chains,
        key=lambda ch: (
            str(ch.parent),
            ",".join(sorted(str(d.name) for d in ch.children)),
        ),
    ):
        if len(c.children) != 1:
            continue
        if c.parent not in rail_names:
            continue
        child = c.children[0].name
        if child in template_names:
            return (c.parent, child)
    return None


def _pick_xor_missed_firing_inputs(
    instance: L2Instance,
) -> tuple[Identifier, int, Identifier] | None:
    """AB.3.5 picker: find any TransferTemplate that declares at least
    one ``leg_rail_xor_groups`` entry AND has at least one leg_rail
    OUTSIDE that group (the witness leg used by the missed-firing
    plant to surface the Transfer without firing any XOR-group member).

    Returns ``(template_name, target_xor_group_index, witness_rail_name)``
    or ``None``. Iterates templates in deterministic name order; for
    each template tries each XOR group in declared order; for each
    group picks the first leg_rail not in the group. The first
    matching triple wins.

    Why the constraint: the plant needs to emit a row with
    ``template_name`` set (so the matview's ``template_transfers``
    CTE includes it) without firing any member of the targeted XOR
    group (else firing_count >= 1 and the missed branch never
    surfaces). The witness must therefore be a leg_rail of the
    template (so it carries the right template_name in the matview's
    sense) but NOT a member of the target group.
    """
    for t in sorted(instance.transfer_templates, key=lambda x: str(x.name)):
        if not t.leg_rail_xor_groups:
            continue
        for gi, group in enumerate(t.leg_rail_xor_groups):
            group_members = set(group)
            for leg in t.leg_rails:
                if leg not in group_members:
                    return (t.name, gi, leg)
    return None


def _pick_xor_overlap_inputs(
    instance: L2Instance,
) -> tuple[Identifier, int, Identifier, Identifier] | None:
    """AB.3.5b picker: find any TransferTemplate XOR group with ≥2
    members. Validator C1d enforces ≥2 at load time, so every declared
    XOR group qualifies — picks the first group in name + index order
    and returns its first two members in declared order.

    Returns ``(template_name, target_xor_group_index, variant_a,
    variant_b)`` or ``None``. Determinism: templates traversed in
    sorted name order, groups in declared order, members in declared
    order. The same picker output for any given L2 across runs.
    """
    for t in sorted(instance.transfer_templates, key=lambda x: str(x.name)):
        if not t.leg_rail_xor_groups:
            continue
        for gi, group in enumerate(t.leg_rail_xor_groups):
            if len(group) >= 2:
                return (t.name, gi, group[0], group[1])
    return None


def _pick_fan_in_chain_inputs(
    instance: L2Instance,
) -> tuple[Identifier, Identifier, int | None] | None:
    """AB.4.5 picker: find any Chain that declares ``fan_in=True``.

    Returns ``(chain_parent_rail_name, child_template_name,
    expected_parent_count_or_None)`` if found, else ``None``.
    Iterates chains in deterministic order (sorted by parent + sorted-
    children CSV); takes the first child of the first fan_in chain
    (validator C8a guarantees every fan_in child is a TransferTemplate,
    so the first child resolves cleanly). The picker also restricts
    to chains whose parent resolves to a Rail (not a TransferTemplate)
    so the plant emitter can synthesize parent legs without needing
    nested-firing logic — same restriction AB.2.6 carries.

    ``expected_parent_count`` is passed through verbatim (None ⇒
    variable-batch-flow; the matview falls back to orphan-only
    detection and the FanInChainExtraParentPlant is dropped from the
    scenario since there's no upper bound to flag).
    """
    rail_names = {r.name for r in instance.rails}
    for c in sorted(
        instance.chains,
        key=lambda ch: (
            str(ch.parent),
            ",".join(sorted(str(d.name) for d in ch.children)),
        ),
    ):
        fan_in_child = next((ch for ch in c.children if ch.fan_in), None)
        if fan_in_child is None:
            continue
        if c.parent not in rail_names:
            continue
        return (c.parent, fan_in_child.name, fan_in_child.expected_parent_count)
    return None


def _pick_multi_xor_chain_inputs(
    instance: L2Instance,
) -> tuple[Identifier, Identifier, Identifier] | None:
    """AB.6.6 picker: find any Chain with ≥2 non-fan_in children whose
    parent resolves to a Rail (not a TransferTemplate).

    Returns ``(chain_parent_rail_name, child_a_name, child_b_name)``
    or ``None``. Skips per-child fan_in entries entirely (AB.5
    coupling — their cardinality is _fan_in_disagreement's job, not
    _multi_xor_violation's). Mixed-cardinality chains qualify as long
    as ≥2 non-fan_in children remain.

    The parent-rail restriction mirrors AB.2.6 / AB.4.5: the plant
    emitter synthesizes parent firings via the chain.parent rail
    name, which only makes sense when parent is a Rail (a
    TransferTemplate parent would require nested-firing logic the
    AB.6.6 plant scaffold doesn't carry).

    Determinism: chains traversed in (parent-name, sorted-children-
    CSV) order — same key the seed loop uses; children traversed in
    declared order; the first two non-fan_in children win.
    """
    rail_names = {r.name for r in instance.rails}
    for c in sorted(
        instance.chains,
        key=lambda ch: (
            str(ch.parent),
            ",".join(sorted(str(d.name) for d in ch.children)),
        ),
    ):
        if c.parent not in rail_names:
            continue
        non_fan_in = [child for child in c.children if not child.fan_in]
        if len(non_fan_in) < 2:
            continue
        return (c.parent, non_fan_in[0].name, non_fan_in[1].name)
    return None


def _zero_td():
    """Convenience for the unbundled-rail cap fallback (shouldn't fire
    in practice — the picker only returns rails with the field set)."""
    from datetime import timedelta
    return timedelta(0)


def _plant_amount_for_rail(
    rail: TwoLegRail | SingleLegRail | None, default: Decimal,
) -> Decimal:
    """AB.5 (E7): when the rail declares ``amount_typical_range``,
    return the range midpoint (cents-quantized) so the planted row
    fits realistic banking magnitudes; else return the hardcoded
    default. Pre-AB.5 fixtures (no ranges declared) keep the legacy
    default amounts byte-equivalent.

    Why the midpoint: plants are positive demo coverage, not violations
    sized to break invariants — landing them squarely in the middle of
    the typical band makes them look like ordinary firings (just at
    the boundary that triggers the SHOULD-constraint).
    """
    if rail is None or rail.amount_typical_range is None:
        return default
    lo, hi = rail.amount_typical_range
    midpoint = (lo + hi) / Decimal(2)
    return midpoint.quantize(Decimal("0.01"))


def _cap_breach_amount(
    cap: Decimal, rail: TwoLegRail | SingleLegRail | None,
) -> Decimal:
    """AB.5 (E7): cap-breach plants emit at ``cap * 1.5`` by default
    (the violation MUST exceed the cap). When the rail's
    ``amount_typical_range`` is declared AND ``cap * 1.5`` exceeds
    ``range.max * 3``, pin to ``range.max * 3`` so the breach amount
    stays in a realistic ballpark relative to the rail's typical
    volume. Pre-AB.5 rails (no range): unchanged ``cap * 1.5``.
    """
    breach = cap * Decimal("1.5")
    if rail is None or rail.amount_typical_range is None:
        return breach.quantize(Decimal("1"))
    _, hi = rail.amount_typical_range
    ceiling = hi * Decimal("3")
    return min(breach, ceiling).quantize(Decimal("1"))
