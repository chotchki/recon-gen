"""Structural regression guards on the L2 primitives package.

Comprehensive per-primitive coverage (load + emit + Current* projection)
lands in M.1.6 once the loader + emitter + Current* projector exist. This
file is the M.1.1 smoke surface — confirms the dataclasses construct
cleanly, the Rail discriminated union dispatches, and the frozen
contract holds.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from quicksight_gen.common.l2 import (
    Account,
    AccountTemplate,
    Chain,
    Identifier,
    L2Instance,
    LimitSchedule,
    RailName,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)


def _example_instance() -> L2Instance:
    """Build a minimal L2Instance using every primitive at least once."""
    return L2Instance(
        instance=Identifier("spk"),
        accounts=(
            Account(
                id=Identifier("int-001"),
                scope="internal",
                name="Internal Operations Account",
                role=Identifier("InternalDDA"),
            ),
        ),
        account_templates=(
            AccountTemplate(
                role=Identifier("CustomerSubledger"),
                scope="internal",
                parent_role=Identifier("SouthPool"),
            ),
        ),
        rails=(
            TwoLegRail(
                name=Identifier("ExtInbound"),
                origin="ExternalForcePosted",
                metadata_keys=(Identifier("external_reference"),),
                source_role=(Identifier("ExternalCounterparty"),),
                destination_role=(Identifier("InternalDDA"),),
                expected_net=Decimal("0"),
            ),
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
        chains=(
            Chain(
                parent=Identifier("MerchantSettlementCycle"),
                children=(Identifier("MerchantPayoutACH"),),
            ),
        ),
        limit_schedules=(
            LimitSchedule(
                parent_role=Identifier("SouthPool"),
                rail=RailName("SubledgerCharge"),
                cap=Decimal("5000.00"),
            ),
        ),
    )


def test_l2instance_constructs_with_every_primitive() -> None:
    """Every primitive is reachable from L2Instance and constructs cleanly."""
    inst = _example_instance()
    assert inst.instance == "spk"
    assert len(inst.accounts) == 1
    assert len(inst.account_templates) == 1
    assert len(inst.rails) == 2
    assert len(inst.transfer_templates) == 1
    assert len(inst.chains) == 1
    assert len(inst.limit_schedules) == 1


def test_rail_discriminated_union_dispatches_via_match() -> None:
    """Per F2: TwoLegRail / SingleLegRail dispatch via match without isinstance ladders."""
    inst = _example_instance()
    classified: list[tuple[str, str]] = []
    for r in inst.rails:
        match r:
            case TwoLegRail(name=n):
                classified.append((str(n), "two-leg"))
            case SingleLegRail(name=n):
                classified.append((str(n), "single-leg"))
    assert classified == [
        ("ExtInbound", "two-leg"),
        ("SubledgerCharge", "single-leg"),
    ]


def test_primitives_are_frozen() -> None:
    """Frozen dataclasses prevent accidental mutation of an L2 instance."""
    inst = _example_instance()
    with pytest.raises(Exception):
        inst.accounts[0].id = Identifier("oops")  # type: ignore[misc]: deliberate frozen-dataclass mutation for the negative-path test


def test_aggregating_flag_optional_on_both_rail_shapes() -> None:
    """Per SPEC: aggregating MAY be true on either two-leg or single-leg."""
    two = TwoLegRail(
        name=Identifier("PoolBalancing"),
        origin="InternalInitiated",
        metadata_keys=(),
        source_role=(Identifier("NorthPool"),),
        destination_role=(Identifier("SouthPool"),),
        expected_net=Decimal("0"),
        aggregating=True,
        bundles_activity=(Identifier("SubledgerCharge"),),
        cadence="intraday-2h",
    )
    assert two.aggregating is True
    assert two.cadence == "intraday-2h"

    single = SingleLegRail(
        name=Identifier("ExternalSweep"),
        origin="InternalInitiated",
        metadata_keys=(),
        leg_role=(Identifier("ExternalCounterparty"),),
        leg_direction="Credit",
        aggregating=True,
        bundles_activity=(Identifier("ach"),),
        cadence="daily-eod",
    )
    assert single.aggregating is True
    assert single.cadence == "daily-eod"


# -- M.1a.2: per-leg Origin, PostedRequirements, aging, Duration ------------


from datetime import timedelta  # noqa: E402  (test-local import for clarity)


def test_two_leg_rail_accepts_per_leg_origin_overrides() -> None:
    """Per SPEC: 2-leg rails MAY override Origin per leg (the leg touching
    an external counterparty often differs from its internal counterpart)."""
    rail = TwoLegRail(
        name=Identifier("ExtRailInbound"),
        metadata_keys=(),
        source_role=(Identifier("ExternalCounterparty"),),
        destination_role=(Identifier("ClearingSuspense"),),
        source_origin="ExternalForcePosted",
        destination_origin="InternalInitiated",
        expected_net=Decimal("0"),
    )
    assert rail.origin is None
    assert rail.source_origin == "ExternalForcePosted"
    assert rail.destination_origin == "InternalInitiated"


def test_two_leg_rail_origin_optional_when_per_leg_set() -> None:
    """Rail-level origin can be None when both per-leg overrides are set."""
    rail = TwoLegRail(
        name=Identifier("R"),
        metadata_keys=(),
        source_role=(Identifier("A"),),
        destination_role=(Identifier("B"),),
        source_origin="ExternalForcePosted",
        destination_origin="InternalInitiated",
        expected_net=Decimal("0"),
    )
    assert rail.origin is None


def test_single_leg_rail_has_no_per_leg_origin_fields() -> None:
    """Per-leg overrides are intentionally absent on SingleLegRail
    (type-level prevention; loader hard-errors if YAML supplies them)."""
    assert not hasattr(SingleLegRail, "source_origin")
    assert not hasattr(SingleLegRail, "destination_origin")


def test_rails_carry_posted_requirements() -> None:
    """Per SPEC: integrator-declared PostedRequirements list lives on the Rail."""
    rail = SingleLegRail(
        name=Identifier("ExtRail"),
        metadata_keys=(Identifier("external_reference"),),
        leg_role=(Identifier("InternalDDA"),),
        leg_direction="Credit",
        origin="ExternalForcePosted",
        posted_requirements=(Identifier("external_reference"),),
    )
    assert rail.posted_requirements == (Identifier("external_reference"),)


def test_rails_carry_aging_thresholds() -> None:
    """Per SPEC: max_pending_age + max_unbundled_age are Duration-typed."""
    rail = TwoLegRail(
        name=Identifier("R"),
        metadata_keys=(),
        source_role=(Identifier("A"),),
        destination_role=(Identifier("B"),),
        origin="InternalInitiated",
        expected_net=Decimal("0"),
        max_pending_age=timedelta(hours=24),
        max_unbundled_age=timedelta(hours=4),
    )
    assert rail.max_pending_age == timedelta(hours=24)
    assert rail.max_unbundled_age == timedelta(hours=4)


def test_rails_default_to_no_aging_or_posted_requirements() -> None:
    """All three new fields default to None / empty so existing callers
    that don't supply them keep working."""
    rail = SingleLegRail(
        name=Identifier("R"),
        metadata_keys=(),
        leg_role=(Identifier("A"),),
        leg_direction="Debit",
        origin="InternalInitiated",
    )
    assert rail.posted_requirements == ()
    assert rail.max_pending_age is None
    assert rail.max_unbundled_age is None


def test_supersede_reason_v1_set() -> None:
    """SPEC v1 SupersedeReason categories. The TypeAlias is open (Literal
    accepts the v1 set; storage column is open enum at the schema layer)."""
    from quicksight_gen.common.l2 import SupersedeReason
    # Smoke: the three v1 values type-check as SupersedeReason at runtime.
    inflight: SupersedeReason = "Inflight"
    bundle: SupersedeReason = "BundleAssignment"
    correction: SupersedeReason = "TechnicalCorrection"
    assert {inflight, bundle, correction} == {
        "Inflight", "BundleAssignment", "TechnicalCorrection",
    }


def test_duration_is_timedelta() -> None:
    """Duration is an alias for datetime.timedelta — same arithmetic + comparisons."""
    from quicksight_gen.common.l2 import Duration
    d: Duration = timedelta(hours=24)
    assert isinstance(d, timedelta)
    assert d == timedelta(days=1)
