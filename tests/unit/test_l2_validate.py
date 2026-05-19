"""Cross-entity validation tests for ``common.l2.validate`` (M.1.3).

One rejection test per rule (per L.1.18 + M.1's testing principle). Each
test starts from the ``_baseline_instance()`` fixture (a known-valid
instance using every primitive) and mutates one field to trigger exactly
one rule.

Rule numbering matches ``validate.py``'s docstring (U1-U4 / R1-R6 /
C1-C2 / S1-S6 / V1-V2).
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from recon_gen.common.l2 import (
    Account,
    AccountTemplate,
    Chain,
    Identifier,
    L2Instance,
    L2ValidationError,
    LimitSchedule,
    Name,
    RailName,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
    validate,
)


# -- Baseline ----------------------------------------------------------------


def _baseline_instance() -> L2Instance:
    """A known-valid L2Instance covering every primitive shape.

    Every test mutates exactly one field of this instance to trigger
    exactly one rule. The baseline itself MUST pass ``validate()`` —
    a regression on the baseline means the validator drifted.
    """
    return L2Instance(
        accounts=(
            Account(
                id=Identifier("gl-control"),
                scope="internal",
                name=Name("Control Account"),
                role=Identifier("ControlAccount"),
            ),
            Account(
                id=Identifier("ext-counter"),
                scope="external",
                role=Identifier("ExternalCounterparty"),
            ),
        ),
        account_templates=(
            AccountTemplate(
                role=Identifier("CustomerSubledger"),
                scope="internal",
                parent_role=Identifier("ControlAccount"),
            ),
        ),
        rails=(
            # Standalone two-leg with expected_net (S1).
            TwoLegRail(
                name=Identifier("ExtInbound"),
                origin="ExternalForcePosted",
                metadata_keys=(Identifier("external_reference"),),
                source_role=(Identifier("ExternalCounterparty"),),
                destination_role=(Identifier("ControlAccount"),),
                expected_net=Decimal("0"),
            ),
            # Single-leg, reconciled by the TransferTemplate below (S3).
            SingleLegRail(
                name=Identifier("SubledgerCharge"),
                origin="InternalInitiated",
                metadata_keys=(
                    Identifier("merchant_id"),
                    Identifier("settlement_period"),
                ),
                leg_role=(Identifier("CustomerSubledger"),),
                leg_direction="Debit",
            ),
            # Aggregating rail (two-leg) with cadence + bundles_activity (S5).
            TwoLegRail(
                name=Identifier("PoolBalancing"),
                origin="InternalInitiated",
                metadata_keys=(),
                source_role=(Identifier("ControlAccount"),),
                destination_role=(Identifier("ControlAccount"),),
                expected_net=Decimal("0"),
                aggregating=True,
                bundles_activity=(Identifier("ExtInbound"),),
                cadence="intraday-2h",
            ),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("MerchantSettlementCycle"),
                expected_net=Decimal("0"),
                transfer_key=(
                    Identifier("merchant_id"),
                    Identifier("settlement_period"),
                ),
                completion="metadata.settlement_period_end",
                leg_rails=(Identifier("SubledgerCharge"),),
            ),
        ),
        chains=(),
        limit_schedules=(
            LimitSchedule(
                parent_role=Identifier("ControlAccount"),
                rail=RailName("ExtInbound"),
                cap=Decimal("5000.00"),
            ),
        ),
    )


def test_baseline_passes_validation() -> None:
    """Sanity guard: every test below assumes the baseline passes."""
    validate(_baseline_instance())


def _replace(inst: L2Instance, **changes) -> L2Instance:
    return dataclasses.replace(inst, **changes)


# -- Uniqueness (U1-U4) ------------------------------------------------------


def test_u1_duplicate_account_id_rejected() -> None:
    inst = _baseline_instance()
    dup = dataclasses.replace(inst.accounts[1], id=inst.accounts[0].id)
    bad = _replace(inst, accounts=(inst.accounts[0], dup))
    with pytest.raises(L2ValidationError, match="duplicate Account.id"):
        validate(bad)


def test_u2_duplicate_account_template_role_rejected() -> None:
    inst = _baseline_instance()
    dup = AccountTemplate(
        role=inst.account_templates[0].role,
        scope="internal",
        parent_role=Identifier("ControlAccount"),
    )
    bad = _replace(inst, account_templates=(*inst.account_templates, dup))
    with pytest.raises(L2ValidationError, match="duplicate AccountTemplate.role"):
        validate(bad)


def test_u3_duplicate_rail_name_rejected() -> None:
    inst = _baseline_instance()
    dup = dataclasses.replace(inst.rails[0], name=Identifier("PoolBalancing"))
    bad = _replace(inst, rails=(*inst.rails, dup))
    with pytest.raises(L2ValidationError, match="duplicate Rail.name"):
        validate(bad)


def test_u4_duplicate_transfer_template_name_rejected() -> None:
    inst = _baseline_instance()
    dup = dataclasses.replace(
        inst.transfer_templates[0], name=Identifier("MerchantSettlementCycle"),
    )
    bad = _replace(inst, transfer_templates=(*inst.transfer_templates, dup))
    with pytest.raises(
        L2ValidationError, match="duplicate TransferTemplate.name",
    ):
        validate(bad)


def test_u7_template_id_collides_with_singleton_rejected() -> None:
    """U7 — the singleton/template ID-collision surfaced by AA.A.6.bug
    (spec_example.yaml had ``cust-001`` as both a declared singleton and
    a template-generated instance, producing duplicate account_name
    rows in the seed and silently breaking L1 dashboard narrowing).

    Reproducer: add a singleton ``cust-001`` with the
    ``CustomerSubledger`` role. The baseline's CustomerSubledger
    template uses fallback ``cust-{n:03d}`` for n in (1, 2), so it
    will also generate ``cust-001`` — collision.
    """
    inst = _baseline_instance()
    colliding_singleton = Account(
        id=Identifier("cust-001"),  # what the fallback renders for n=1
        scope="internal",
        name=Name("Customer Number One"),
        role=Identifier("CustomerSubledger"),
        parent_role=Identifier("ControlAccount"),
    )
    bad = _replace(inst, accounts=(*inst.accounts, colliding_singleton))
    with pytest.raises(
        L2ValidationError,
        match=r"materializes account_id 'cust-001' which is already declared",
    ):
        validate(bad)


# -- Reference resolution (R1-R6) --------------------------------------------


def test_r1_rail_references_undeclared_role_rejected() -> None:
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(
        inst.rails[0],
        source_role=(Identifier("UndeclaredRole"),),
    )
    bad = _replace(inst, rails=(bad_rail, *inst.rails[1:]))
    with pytest.raises(L2ValidationError, match="ExtInbound.*UndeclaredRole"):
        validate(bad)


def test_r2_account_parent_role_resolves() -> None:
    inst = _baseline_instance()
    bad_acc = dataclasses.replace(
        inst.accounts[0], parent_role=Identifier("UndeclaredRole"),
    )
    bad = _replace(inst, accounts=(bad_acc, *inst.accounts[1:]))
    with pytest.raises(L2ValidationError, match="gl-control.*parent_role"):
        validate(bad)


def test_r3_account_template_parent_role_must_be_singleton() -> None:
    """Template-under-template parent reference is rejected per F1."""
    inst = _baseline_instance()
    # Add another template; first template references the second (NOT a singleton).
    second_template = AccountTemplate(
        role=Identifier("MerchantLedger"),
        scope="internal",
        parent_role=Identifier("ControlAccount"),
    )
    bad_template = dataclasses.replace(
        inst.account_templates[0],
        parent_role=Identifier("MerchantLedger"),
    )
    bad = _replace(
        inst,
        account_templates=(bad_template, second_template),
    )
    with pytest.raises(
        L2ValidationError, match="resolves to another AccountTemplate",
    ):
        validate(bad)


def test_r3_account_template_parent_role_undeclared_rejected() -> None:
    inst = _baseline_instance()
    bad_template = dataclasses.replace(
        inst.account_templates[0],
        parent_role=Identifier("UndeclaredRole"),
    )
    bad = _replace(inst, account_templates=(bad_template,))
    with pytest.raises(
        L2ValidationError, match="not declared on any Account",
    ):
        validate(bad)


def test_r4_template_leg_rails_must_exist() -> None:
    inst = _baseline_instance()
    bad_template = dataclasses.replace(
        inst.transfer_templates[0],
        leg_rails=(Identifier("NonexistentRail"),),
    )
    bad = _replace(inst, transfer_templates=(bad_template,))
    with pytest.raises(
        L2ValidationError, match="MerchantSettlementCycle.*NonexistentRail",
    ):
        validate(bad)


def test_r5_chain_endpoints_must_exist() -> None:
    inst = _baseline_instance()
    bad_chain = Chain(
        parent=Identifier("MerchantSettlementCycle"),
        children=(Identifier("NonexistentRail"),),
    )
    bad = _replace(inst, chains=(bad_chain,))
    with pytest.raises(L2ValidationError, match=r"chains\[0\]\.children\[0\]"):
        validate(bad)


def test_r6_limit_schedule_parent_role_must_resolve() -> None:
    inst = _baseline_instance()
    bad_limit = LimitSchedule(
        parent_role=Identifier("UndeclaredRole"),
        rail=RailName("ExtInbound"),
        cap=Decimal("100"),
    )
    bad = _replace(inst, limit_schedules=(bad_limit,))
    with pytest.raises(L2ValidationError, match="limit_schedules\\[0\\]"):
        validate(bad)


# -- Cardinality (C1-C2) -----------------------------------------------------


def test_c1_at_most_one_variable_leg_per_template() -> None:
    inst = _baseline_instance()
    # Add a second SingleLegRail with Variable direction; both go in the
    # template's leg_rails, triggering > 1 Variable legs. R12 requires
    # the template's transfer_key fields appear in every leg_rail's
    # metadata_keys, so both Variable legs carry merchant_id +
    # settlement_period.
    second_var = SingleLegRail(
        name=Identifier("SettlementCloseB"),
        origin="InternalInitiated",
        metadata_keys=(
            Identifier("merchant_id"),
            Identifier("settlement_period"),
        ),
        leg_role=(Identifier("ControlAccount"),),
        leg_direction="Variable",
    )
    first_var = dataclasses.replace(
        inst.rails[1],  # SubledgerCharge
        leg_direction="Variable",
    )
    bad_template = dataclasses.replace(
        inst.transfer_templates[0],
        leg_rails=(first_var.name, second_var.name),
    )
    bad = _replace(
        inst,
        rails=(inst.rails[0], first_var, inst.rails[2], second_var),
        transfer_templates=(bad_template,),
    )
    with pytest.raises(
        L2ValidationError,
        match="contains 2 non-grouped Variable-direction legs",
    ):
        validate(bad)


# C2 (xor_group members share parent) is gone under Z.A — every Chain
# row IS one parent, so the cross-parent failure mode is unrepresentable
# in the new grammar.


# -- State-dependent (S1-S6) -------------------------------------------------


def test_s1_standalone_two_leg_requires_expected_net() -> None:
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(inst.rails[0], expected_net=None)
    bad = _replace(inst, rails=(bad_rail, *inst.rails[1:]))
    with pytest.raises(
        L2ValidationError, match="standalone two-leg rail.*MUST declare expected_net",
    ):
        validate(bad)


def test_s2_template_leg_must_not_have_expected_net() -> None:
    inst = _baseline_instance()
    # Add a two-leg rail that's listed in the template's leg_rails AND
    # carries expected_net. The baseline's template only has the
    # SubledgerCharge single-leg; add a two-leg "ClosingLeg" so we can
    # exercise this rule. R12 requires the template's transfer_key
    # fields appear in every leg_rail's metadata_keys.
    closing = TwoLegRail(
        name=Identifier("ClosingLeg"),
        origin="InternalInitiated",
        metadata_keys=(
            Identifier("merchant_id"),
            Identifier("settlement_period"),
        ),
        source_role=(Identifier("ControlAccount"),),
        destination_role=(Identifier("ControlAccount"),),
        expected_net=Decimal("0"),  # Wrong: rail is in leg_rails so this is forbidden.
    )
    bad_template = dataclasses.replace(
        inst.transfer_templates[0],
        leg_rails=(*inst.transfer_templates[0].leg_rails, Identifier("ClosingLeg")),
    )
    bad = _replace(
        inst,
        rails=(*inst.rails, closing),
        transfer_templates=(bad_template,),
    )
    with pytest.raises(
        L2ValidationError,
        match="ClosingLeg.*appears in a TransferTemplate.*MUST NOT carry one",
    ):
        validate(bad)


def test_s3_aggregating_single_leg_exempt_from_reconciliation() -> None:
    """Per SPEC: aggregating single-leg rails ARE the reconciliation mechanism.

    They drift their leg into an external counterparty by design (sweep
    pattern) — they don't themselves need to be reconciled by another
    rail or template. Surfaced by M.1.8 kitchen-sink fixture.
    """
    inst = _baseline_instance()
    sweep = SingleLegRail(
        name=Identifier("DailySweepToExternal"),
        origin="InternalInitiated",
        metadata_keys=(),
        leg_role=(Identifier("ExternalCounterparty"),),
        leg_direction="Credit",
        aggregating=True,
        bundles_activity=(Identifier("ExtInbound"),),
        cadence="daily-eod",
    )
    # NOT in any TransferTemplate.leg_rails AND not in any other
    # aggregating rail's bundles_activity — but its own aggregating=True
    # means it self-reconciles.
    validate(_replace(inst, rails=(*inst.rails, sweep)))


def test_s3_unreconciled_single_leg_rejected() -> None:
    inst = _baseline_instance()
    # Add a SingleLegRail not in any template + not matched by any
    # aggregating bundles_activity.
    orphan = SingleLegRail(
        name=Identifier("OrphanLeg"),
        origin="InternalInitiated",
        metadata_keys=(),
        leg_role=(Identifier("ControlAccount"),),
        leg_direction="Debit",
    )
    bad = _replace(inst, rails=(*inst.rails, orphan))
    with pytest.raises(
        L2ValidationError,
        match="OrphanLeg.*single-leg rail is not reconciled",
    ):
        validate(bad)


def test_s4_aggregating_rail_rejected_as_chain_child() -> None:
    inst = _baseline_instance()
    bad_chain = Chain(
        parent=Identifier("MerchantSettlementCycle"),
        children=(Identifier("PoolBalancing"),),  # aggregating rail
    )
    bad = _replace(inst, chains=(bad_chain,))
    with pytest.raises(
        L2ValidationError,
        match="aggregating Rails MUST NOT appear in Chain.children",
    ):
        validate(bad)


def test_s5_aggregating_rail_requires_cadence() -> None:
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(inst.rails[2], cadence=None)
    bad = _replace(inst, rails=(*inst.rails[:2], bad_rail))
    with pytest.raises(
        L2ValidationError, match="PoolBalancing.*requires cadence",
    ):
        validate(bad)


def test_s5_aggregating_rail_requires_bundles_activity() -> None:
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(inst.rails[2], bundles_activity=())
    bad = _replace(inst, rails=(*inst.rails[:2], bad_rail))
    with pytest.raises(
        L2ValidationError, match="requires bundles_activity",
    ):
        validate(bad)


def test_s6_non_aggregating_rail_rejects_cadence() -> None:
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(inst.rails[0], cadence="daily-eod")
    bad = _replace(inst, rails=(bad_rail, *inst.rails[1:]))
    with pytest.raises(
        L2ValidationError, match="cadence is only meaningful when aggregating",
    ):
        validate(bad)


def test_s6_non_aggregating_rail_rejects_bundles_activity() -> None:
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(
        inst.rails[0], bundles_activity=(Identifier("ach"),),
    )
    bad = _replace(inst, rails=(bad_rail, *inst.rails[1:]))
    with pytest.raises(
        L2ValidationError, match="bundles_activity is only meaningful",
    ):
        validate(bad)


# -- Vocabulary (V1-V2) ------------------------------------------------------


@pytest.mark.parametrize("good_completion", [
    "business_day_end",
    "business_day_end+3d",
    "business_day_end+30d",
    "month_end",
    "metadata.settlement_period_end",
    "metadata.deadline",
])
def test_v1_completion_vocabulary_accepts_valid(good_completion: str) -> None:
    inst = _baseline_instance()
    good_template = dataclasses.replace(
        inst.transfer_templates[0], completion=good_completion,
    )
    validate(_replace(inst, transfer_templates=(good_template,)))


@pytest.mark.parametrize("bad_completion", [
    "tomorrow",
    "business_day_end+3w",         # weeks not supported
    "metadata.",                    # empty key
    "Metadata.deadline",            # capital M
    "business_day_end+",            # missing N
])
def test_v1_completion_vocabulary_rejects_invalid(bad_completion: str) -> None:
    inst = _baseline_instance()
    bad_template = dataclasses.replace(
        inst.transfer_templates[0], completion=bad_completion,
    )
    bad = _replace(inst, transfer_templates=(bad_template,))
    with pytest.raises(L2ValidationError, match="not a v1 CompletionExpression"):
        validate(bad)


@pytest.mark.parametrize("good_cadence", [
    "intraday-1h",
    "intraday-12h",
    "daily-eod",
    "daily-bod",
    "weekly-mon",
    "weekly-sun",
    "monthly-eom",
    "monthly-bom",
    "monthly-1",
    "monthly-15",
    "monthly-31",
])
def test_v2_cadence_vocabulary_accepts_valid(good_cadence: str) -> None:
    inst = _baseline_instance()
    good_rail = dataclasses.replace(inst.rails[2], cadence=good_cadence)
    validate(_replace(inst, rails=(*inst.rails[:2], good_rail)))


@pytest.mark.parametrize("bad_cadence", [
    "every-other-friday",
    "intraday-2",                # missing 'h'
    "weekly-funday",             # not a real weekday
    "monthly-32",                # day 32
    "monthly-0",                 # day 0
    "annual-jan",                # not a v1 cadence
])
def test_v2_cadence_vocabulary_rejects_invalid(bad_cadence: str) -> None:
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(inst.rails[2], cadence=bad_cadence)
    bad = _replace(inst, rails=(*inst.rails[:2], bad_rail))
    with pytest.raises(L2ValidationError, match="not a v1 CadenceExpression"):
        validate(bad)


# -- M.1a.3: New SPEC rules (U5, R7, R8, R9, O1) ----------------------------


def test_u5_duplicate_limit_schedule_combination_rejected() -> None:
    """U5: (parent_role, rail, direction) triple MUST be unique across
    LimitSchedule.

    Z.B (2026-05-15): keyed on the rail name now (was transfer_type).
    AB.1 (2026-05-19): triple now includes direction.
    """
    inst = _baseline_instance()
    dup = LimitSchedule(
        parent_role=inst.limit_schedules[0].parent_role,
        rail=inst.limit_schedules[0].rail,
        cap=Decimal("999.00"),
        # Direction defaults to Outbound — same triple as the base entry.
    )
    bad = _replace(inst, limit_schedules=(*inst.limit_schedules, dup))
    with pytest.raises(L2ValidationError, match="duplicate"):
        validate(bad)


def test_u5_same_role_different_rail_allowed() -> None:
    """U5 negative: same parent_role with different rail is fine."""
    inst = _baseline_instance()
    extra = LimitSchedule(
        parent_role=inst.limit_schedules[0].parent_role,  # same role
        rail=RailName("SubledgerCharge"),  # different rail
        cap=Decimal("100.00"),
    )
    ok = _replace(inst, limit_schedules=(*inst.limit_schedules, extra))
    validate(ok)


def test_u5_same_parent_rail_different_direction_allowed() -> None:
    """AB.1: (parent_role, rail) may carry both an Outbound + an Inbound
    LimitSchedule simultaneously — they're separate U5 triples.
    """
    inst = _baseline_instance()
    inbound_sibling = LimitSchedule(
        parent_role=inst.limit_schedules[0].parent_role,
        rail=inst.limit_schedules[0].rail,
        cap=Decimal("250.00"),
        direction="Inbound",
    )
    ok = _replace(
        inst, limit_schedules=(*inst.limit_schedules, inbound_sibling),
    )
    validate(ok)


def test_u5_same_triple_inbound_dup_rejected() -> None:
    """AB.1: two Inbound caps on the same (parent, rail) still rejected
    — uniqueness holds at the triple level, not just per-direction.
    """
    inst = _baseline_instance()
    a = LimitSchedule(
        parent_role=inst.limit_schedules[0].parent_role,
        rail=inst.limit_schedules[0].rail,
        cap=Decimal("100.00"),
        direction="Inbound",
    )
    b = LimitSchedule(
        parent_role=inst.limit_schedules[0].parent_role,
        rail=inst.limit_schedules[0].rail,
        cap=Decimal("200.00"),
        direction="Inbound",
    )
    bad = _replace(inst, limit_schedules=(*inst.limit_schedules, a, b))
    with pytest.raises(
        L2ValidationError, match="duplicate.*direction='Inbound'",
    ):
        validate(bad)


# Z.B (2026-05-15): U6 (Rail discriminator uniqueness on (transfer_type,
# role) tuples) is GONE under the symmetric collapse — `transfer_type` no
# longer exists, and U3 already enforces `Rail.name` uniqueness across the
# instance, which is the single discriminator now. The legacy U6
# collisions (two rails with same transfer_type sharing a role) cannot
# be expressed in the new grammar.


def test_r7_template_leg_rails_must_be_non_aggregating() -> None:
    """R7: TransferTemplate.leg_rails entries MUST NOT reference aggregating rails."""
    inst = _baseline_instance()
    # Add an aggregating rail and reference it from the template's leg_rails.
    agg = SingleLegRail(
        name=Identifier("AggLeg"),
        origin="InternalInitiated",
        metadata_keys=(),
        leg_role=(Identifier("ControlAccount"),),
        leg_direction="Variable",
        aggregating=True,
        bundles_activity=(Identifier("SubledgerCharge"),),
        cadence="daily-eod",
    )
    bad_template = dataclasses.replace(
        inst.transfer_templates[0],
        leg_rails=(*inst.transfer_templates[0].leg_rails, Identifier("AggLeg")),
    )
    bad = _replace(
        inst,
        rails=(*inst.rails, agg),
        transfer_templates=(bad_template,),
    )
    with pytest.raises(
        L2ValidationError,
        match=r"leg_rails: rail 'AggLeg' is aggregating",
    ):
        validate(bad)


def test_r8_max_unbundled_age_requires_a_bundling_rail() -> None:
    """R8: max_unbundled_age set on a Rail nothing bundles is rejected."""
    inst = _baseline_instance()
    # SubledgerCharge has no aggregating rail bundling it (the baseline's
    # PoolBalancing bundles "ach", not "charge"). Set max_unbundled_age to
    # trigger the rule.
    from datetime import timedelta
    bad_rail = dataclasses.replace(
        inst.rails[1],
        max_unbundled_age=timedelta(hours=4),
    )
    bad = _replace(
        inst, rails=(inst.rails[0], bad_rail, inst.rails[2]),
    )
    with pytest.raises(
        L2ValidationError,
        match=r"max_unbundled_age is set but no aggregating Rail bundles",
    ):
        validate(bad)


def test_r8_max_unbundled_age_satisfied_by_rail_name_match() -> None:
    """R8 negative: a bare selector matching the rail's name satisfies the watch.

    Z.B (2026-05-15): bundles_activity entries match Rail.name (the
    transfer_type fallback was dropped with the symmetric collapse).
    The baseline's PoolBalancing bundles ``ExtInbound`` by name; setting
    ``max_unbundled_age`` on ExtInbound itself validates cleanly because
    its name appears in some aggregating rail's bundles_activity.
    """
    inst = _baseline_instance()
    from datetime import timedelta
    ok_rail = dataclasses.replace(
        inst.rails[0],
        max_unbundled_age=timedelta(hours=4),
    )
    ok = _replace(inst, rails=(ok_rail, *inst.rails[1:]))
    validate(ok)


def test_r9_dotted_bundle_selector_unknown_template_rejected() -> None:
    """R9: dotted-form selector with unknown template name is rejected."""
    inst = _baseline_instance()
    bad_agg = dataclasses.replace(
        inst.rails[2],
        bundles_activity=(Identifier("UnknownTemplate.SubledgerCharge"),),
    )
    bad = _replace(inst, rails=(*inst.rails[:2], bad_agg))
    with pytest.raises(
        L2ValidationError,
        match=r"references TransferTemplate 'UnknownTemplate' which is not declared",
    ):
        validate(bad)


def test_r9_dotted_bundle_selector_unknown_leg_rejected() -> None:
    """R9: dotted-form selector where leg-rail isn't actually in template's leg_rails."""
    inst = _baseline_instance()
    bad_agg = dataclasses.replace(
        inst.rails[2],
        bundles_activity=(
            Identifier("MerchantSettlementCycle.NotAlegRail"),
        ),
    )
    bad = _replace(inst, rails=(*inst.rails[:2], bad_agg))
    with pytest.raises(
        L2ValidationError,
        match=r"references rail 'NotAlegRail' which is not in TransferTemplate",
    ):
        validate(bad)


def test_r9_dotted_bundle_selector_valid_pair_accepted() -> None:
    """R9 negative: a real Template.LegRail pair validates cleanly."""
    inst = _baseline_instance()
    ok_agg = dataclasses.replace(
        inst.rails[2],
        bundles_activity=(
            Identifier("MerchantSettlementCycle.SubledgerCharge"),
        ),
    )
    ok = _replace(inst, rails=(*inst.rails[:2], ok_agg))
    validate(ok)


def test_o1_single_leg_rail_without_origin_rejected() -> None:
    """O1: single-leg rail with no origin set is rejected."""
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(inst.rails[1], origin=None)
    bad = _replace(inst, rails=(inst.rails[0], bad_rail, inst.rails[2]))
    with pytest.raises(
        L2ValidationError,
        match=r"single-leg rail MUST set origin",
    ):
        validate(bad)


def test_o1_two_leg_rail_with_no_origin_anywhere_rejected() -> None:
    """O1: two-leg rail with no origin AND no per-leg overrides is rejected."""
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(inst.rails[0], origin=None)
    bad = _replace(inst, rails=(bad_rail, *inst.rails[1:]))
    with pytest.raises(
        L2ValidationError,
        match=r"two-leg rail's source leg has no resolved Origin",
    ):
        validate(bad)


def test_o1_two_leg_rail_one_override_no_fallback_rejected() -> None:
    """O1: one per-leg override + no rail-level origin = the OTHER leg unresolved."""
    inst = _baseline_instance()
    bad_rail = dataclasses.replace(
        inst.rails[0],
        origin=None,
        source_origin="ExternalForcePosted",
        # destination_origin still None; rail-level origin still None
    )
    bad = _replace(inst, rails=(bad_rail, *inst.rails[1:]))
    with pytest.raises(
        L2ValidationError,
        match=r"two-leg rail's destination leg has no resolved Origin",
    ):
        validate(bad)


def test_o1_two_leg_rail_one_override_plus_rail_origin_accepted() -> None:
    """O1 negative: one per-leg override + rail-level origin fallback is valid."""
    inst = _baseline_instance()
    ok_rail = dataclasses.replace(
        inst.rails[0],
        origin="InternalInitiated",  # fallback
        source_origin="ExternalForcePosted",  # override on source
        # destination falls back to rail-level "InternalInitiated"
    )
    ok = _replace(inst, rails=(ok_rail, *inst.rails[1:]))
    validate(ok)


def test_o1_two_leg_rail_both_per_leg_overrides_accepted() -> None:
    """O1 negative: both per-leg overrides cover both legs without rail-level origin."""
    inst = _baseline_instance()
    ok_rail = dataclasses.replace(
        inst.rails[0],
        origin=None,
        source_origin="ExternalForcePosted",
        destination_origin="InternalInitiated",
    )
    ok = _replace(inst, rails=(ok_rail, *inst.rails[1:]))
    validate(ok)


# -- M.2d.1: New SPEC rules (R10, R11) --------------------------------------


def test_r10_limit_schedule_rail_must_match_some_rail() -> None:
    """R10: a LimitSchedule.rail with no matching Rail.name is rejected.

    Z.B (2026-05-15): the cap binds directly to a rail name now (was
    transfer_type). A cap on rail='WireOutbound' (no Rail with that
    name) would silently never fire — load-time error.
    """
    inst = _baseline_instance()
    bad = LimitSchedule(
        parent_role=Identifier("ControlAccount"),
        rail=RailName("WireOutbound"),  # no Rail with this name
        cap=Decimal("1000.00"),
    )
    inst = _replace(inst, limit_schedules=(*inst.limit_schedules, bad))
    with pytest.raises(L2ValidationError, match="no declared Rail with this name"):
        validate(inst)


def test_r10_typo_in_existing_rail_name_rejected() -> None:
    """R10: typo'd rail name ('ExtInboundd' for 'ExtInbound') is the canonical bug."""
    inst = _baseline_instance()
    typo = dataclasses.replace(inst.limit_schedules[0], rail=RailName("ExtInboundd"))
    inst = _replace(inst, limit_schedules=(typo,))
    with pytest.raises(L2ValidationError, match=r"rail='ExtInboundd'"):
        validate(inst)


def test_r11_bare_bundles_activity_selector_resolving_by_rail_name_accepted() -> None:
    """R11 negative: a bare selector matching a Rail.name is fine.

    Z.B (2026-05-15): only Rail.name matches now (the transfer_type
    fallback was dropped with the symmetric collapse). The baseline's
    PoolBalancing bundles 'ExtInbound' which IS a declared Rail.name.
    """
    validate(_baseline_instance())


def test_r11_unresolvable_bare_bundles_activity_selector_rejected() -> None:
    """R11: a bare selector matching no declared Rail.name rejects.

    'CustomerOutboundACHTypo' isn't a declared rail name — bundler
    would silently match nothing.
    """
    inst = _baseline_instance()
    typo = dataclasses.replace(
        inst.rails[2],
        bundles_activity=(Identifier("CustomerOutboundACHTypo"),),
    )
    inst = _replace(inst, rails=(*inst.rails[:2], typo))
    with pytest.raises(
        L2ValidationError,
        match=r"bare selector 'CustomerOutboundACHTypo' resolves to no declared Rail.name",
    ):
        validate(inst)


def test_r11_dotted_form_selector_unaffected() -> None:
    """R11: dotted-form selectors are R9's job; R11 skips them.

    Build a fixture with a dotted selector that R9 would accept (the
    template + leg both exist) and confirm R11 doesn't second-guess it.
    """
    inst = _baseline_instance()
    pool = dataclasses.replace(
        inst.rails[2],
        bundles_activity=(Identifier("MerchantSettlementCycle.SubledgerCharge"),),
    )
    inst = _replace(inst, rails=(*inst.rails[:2], pool))
    validate(inst)


# -- M.3.13: New SPEC rules (R12, C3, C4) -----------------------------------


def test_r12_transfer_key_field_missing_from_leg_rail_metadata_keys_rejected() -> None:
    """R12: a TransferKey field absent from a leg_rail's metadata_keys
    is rejected.

    The library auto-derives every TransferKey field as a
    PostedRequirement on each leg_rail. If the rail's metadata_keys
    doesn't declare the field, ETL has no legitimate place to populate
    it — the leg can never reach Status=Posted. Caught at load.
    """
    inst = _baseline_instance()
    # Drop merchant_id from SubledgerCharge.metadata_keys; the template's
    # transfer_key still demands (merchant_id, settlement_period).
    bad_rail = dataclasses.replace(
        inst.rails[1],  # SubledgerCharge
        metadata_keys=(Identifier("settlement_period"),),  # missing merchant_id
    )
    bad = _replace(inst, rails=(inst.rails[0], bad_rail, inst.rails[2]))
    with pytest.raises(
        L2ValidationError,
        match=r"missing TransferKey field\(s\) \['merchant_id'\]",
    ):
        validate(bad)


def test_r12_passes_when_every_leg_rail_carries_every_transfer_key_field() -> None:
    """R12 negative: the baseline already satisfies R12 (SubledgerCharge
    declares both merchant_id + settlement_period). Sanity guard."""
    validate(_baseline_instance())


def test_r12_template_with_no_transfer_key_skips_check() -> None:
    """R12: a TransferTemplate with empty transfer_key has no field
    requirements, so the rule is vacuously satisfied for its leg_rails.
    """
    inst = _baseline_instance()
    # Empty transfer_key — leg_rail's metadata_keys can be anything.
    no_key_template = dataclasses.replace(
        inst.transfer_templates[0],
        transfer_key=(),
    )
    bare_rail = dataclasses.replace(
        inst.rails[1], metadata_keys=(),  # empty metadata_keys
    )
    ok = _replace(
        inst,
        rails=(inst.rails[0], bare_rail, inst.rails[2]),
        transfer_templates=(no_key_template,),
    )
    validate(ok)


def test_c3_variable_single_leg_not_in_any_template_rejected() -> None:
    """C3: a Variable single-leg rail not in any TransferTemplate.leg_rails
    is rejected.

    Variable closure semantics require a containing template's
    ExpectedNet to compute the leg's amount + direction at posting
    time. A Variable rail reconciled only via aggregating-bundling has
    no closure target — the bundler computes its own amount, not a
    closure. Configuration is meaningless.
    """
    inst = _baseline_instance()
    # Add a Variable single-leg rail that's bundled by the existing
    # PoolBalancing aggregator (so S3 reconciliation passes), but NOT
    # in any TransferTemplate.leg_rails — should trip C3.
    var_rail = SingleLegRail(
        name=Identifier("OrphanVariable"),
        origin="InternalInitiated",
        metadata_keys=(),
        leg_role=(Identifier("CustomerSubledger"),),
        leg_direction="Variable",
    )
    # PoolBalancing now bundles "OrphanVariable" by name so S3 path B
    # holds; without this the rail also trips S3-unreconciled before
    # C3 fires.
    bundler = dataclasses.replace(
        inst.rails[2],
        bundles_activity=(Identifier("OrphanVariable"),),
    )
    inst = _replace(inst, rails=(*inst.rails[:2], bundler))
    bad = _replace(inst, rails=(*inst.rails, var_rail))
    with pytest.raises(
        L2ValidationError,
        match=r"OrphanVariable.*Variable-direction.*not in any TransferTemplate",
    ):
        validate(bad)


def test_c3_variable_single_leg_in_some_template_accepted() -> None:
    """C3 negative: a Variable single-leg rail listed in some
    TransferTemplate.leg_rails is fine."""
    inst = _baseline_instance()
    # Promote SubledgerCharge (already in MerchantSettlementCycle.leg_rails)
    # to Variable direction. Should validate cleanly.
    promoted = dataclasses.replace(
        inst.rails[1], leg_direction="Variable",
    )
    ok = _replace(inst, rails=(inst.rails[0], promoted, inst.rails[2]))
    validate(ok)


# C4 / C4.1 (xor_group ≥ 2 members; required + xor_group contradiction)
# are gone under Z.A — singleton-children encodes "required" and
# multi-children encodes "XOR" cleanly; the legacy contradictions
# can't be expressed.


def test_c5_chain_row_with_empty_children_rejected() -> None:
    """C5 (Z.A): a Chain row with an empty children list is degenerate —
    no firing rule. The loader rejects empty lists with a more
    actionable error; this validator rule is defense-in-depth for
    in-memory L2 instances built outside the loader.
    """
    inst = _baseline_instance()
    # Build a Chain row with no children. The dataclass accepts an
    # empty tuple; validate() should reject it.
    empty_chain = Chain(
        parent=Identifier("MerchantSettlementCycle"),
        children=(),
    )
    bad = _replace(inst, chains=(empty_chain,))
    with pytest.raises(L2ValidationError, match="children list is empty"):
        validate(bad)


def test_c6_duplicate_child_under_same_parent_rejected() -> None:
    """C6 (Z.A): for any given Chain parent, no child appears in two
    Chain rows. Catches a contradiction like "Foo is required" plus
    "Foo is one of [Foo, Bar]" — the two rows would either narrate
    the same firing twice or contradict each other.
    """
    inst = _baseline_instance()
    a = Chain(
        parent=Identifier("MerchantSettlementCycle"),
        children=(Identifier("ExtInbound"),),
    )
    # Same parent, ExtInbound listed in this row's XOR siblings as
    # well — duplicates the child reference under the same parent.
    b = Chain(
        parent=Identifier("MerchantSettlementCycle"),
        children=(Identifier("ExtInbound"), Identifier("SubledgerCharge")),
    )
    bad = _replace(inst, chains=(a, b))
    with pytest.raises(
        L2ValidationError,
        match=r"Chain parent 'MerchantSettlementCycle'.*ExtInbound",
    ):
        validate(bad)
