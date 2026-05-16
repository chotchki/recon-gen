"""Targeted L2 primitive tests against ``tests/l2/sasquatch_pr.yaml`` (M.3.3).

The fixture exercises the four L2 primitives that historically lived
under "PR-only" in the SPEC stress-test framing — even though AR also
has TransferKey grouping + Variable closure (just not at the same
depth as PR). The tests below are *shape-level* assertions: they
verify properties of the loaded L2 instance that the cross-entity
validator doesn't catch on its own (richness, depth, behavior
semantics implied by the SPEC).

Each test names the SPEC primitive it exercises so a future SPEC
amendment that loosens a constraint surfaces here:

- **TransferKey grouping** — multi-firing aggregation; legs with
  matching transfer_key values join one shared Transfer.
- **Variable closure** — Variable-direction leg's amount + direction
  are derivable from sibling legs to satisfy ExpectedNet.
- **XOR group enforcement** — Chain entries sharing one xor_group
  represent "exactly one fires per parent" semantics.
- **AggregatingRail bundling** — bundles_activity scope determines
  what gets rolled up on the declared cadence.

All tests cache the instance via ``@functools.cache`` — the fixture
loads once per test session.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from quicksight_gen.common.l2 import (
    L2Instance,
    SingleLegRail,
    TwoLegRail,
    load_instance,
    posted_requirements_for,
)


YAML_PATH = Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"


@functools.cache
def _instance() -> L2Instance:
    return load_instance(YAML_PATH)


# ---------------------------------------------------------------------------
# 1. TransferKey grouping
# ---------------------------------------------------------------------------


class TestTransferKeyGrouping:
    """Multi-firing TransferKey grouping: many rail firings of the same
    leg_rail join ONE shared Transfer keyed by the metadata-derived
    transfer_key tuple. SPEC §TransferTemplate."""

    def test_merchant_settlement_cycle_has_multi_key_transfer_key(self) -> None:
        """The PR demo's TransferKey covers (merchant_id, settlement_period)
        — the canonical 'aggregate sales by merchant + period' pattern."""
        inst = _instance()
        msc = next(
            t for t in inst.transfer_templates
            if str(t.name) == "MerchantSettlementCycle"
        )
        assert len(msc.transfer_key) == 2, (
            "MerchantSettlementCycle must use a 2-key TransferKey for the "
            "(merchant_id, settlement_period) grouping pattern"
        )
        assert "merchant_id" in msc.transfer_key
        assert "settlement_period" in msc.transfer_key

    def test_transfer_key_fields_appear_in_resolved_posted_requirements(self) -> None:
        """Per the SPEC's TransferKey resolution: every leg_rail of a
        TransferKey-grouped template ends up with the key fields in its
        *resolved* PostedRequirements (via ``posted_requirements_for``).
        The Rail's raw ``posted_requirements`` is the integrator-declared
        subset; ``derived.posted_requirements_for`` unions in TransferKey
        fields automatically. This test confirms that derivation produces
        the expected union for every TransferKey-grouped template in
        sasquatch_pr.yaml."""
        inst = _instance()
        for tt in inst.transfer_templates:
            if not tt.transfer_key:
                continue
            for leg_rail_name in tt.leg_rails:
                resolved = {
                    str(k) for k in posted_requirements_for(inst, leg_rail_name)
                }
                missing = set(tt.transfer_key) - resolved
                assert not missing, (
                    f"TransferTemplate {tt.name!r} keys on {tt.transfer_key!r}, "
                    f"but leg rail {leg_rail_name!r} resolved PostedRequirements "
                    f"don't include {sorted(missing)!r} — derivation broke."
                )

    def test_merchant_card_sale_carries_grouping_keys(self) -> None:
        """Specifically: MerchantCardSale (the leg rail of
        MerchantSettlementCycle) must carry merchant_id + settlement_period
        in BOTH its metadata_keys AND its posted_requirements."""
        inst = _instance()
        sale = next(
            r for r in inst.rails if str(r.name) == "MerchantCardSale"
        )
        meta_keys = {str(k) for k in sale.metadata_keys}
        posted_reqs = {str(k) for k in sale.posted_requirements}
        assert {"merchant_id", "settlement_period"} <= meta_keys
        assert {"merchant_id", "settlement_period"} <= posted_reqs


# ---------------------------------------------------------------------------
# 2. Variable closure
# ---------------------------------------------------------------------------


class TestVariableClosure:
    """Variable-direction leg semantics: the leg's amount_money +
    amount_direction are computed at posting time so the bundle's net
    equals the template's ExpectedNet. SPEC §Single-leg variable-direction
    rail + §Transfer Templates."""

    def test_at_most_one_variable_leg_per_template(self) -> None:
        """C1 — already validator-enforced; mirror here so a SPEC
        loosening surfaces in this test file too (single source of truth
        for the runtime invariant)."""
        inst = _instance()
        rails_by_name = {str(r.name): r for r in inst.rails}
        for tt in inst.transfer_templates:
            variable_legs = [
                rails_by_name[str(n)]
                for n in tt.leg_rails
                if isinstance(rails_by_name.get(str(n)), SingleLegRail)
                and rails_by_name[str(n)].leg_direction == "Variable"  # type: ignore[union-attr]: narrowed by the prior isinstance(..., SingleLegRail) check
            ]
            assert len(variable_legs) <= 1, (
                f"TransferTemplate {tt.name!r}: more than one Variable "
                f"leg breaks closure (under-determined). Got {len(variable_legs)}."
            )

    def test_template_with_variable_leg_has_expected_net(self) -> None:
        """Variable closure depends on a target ExpectedNet to compute the
        closing leg's amount + direction. Without expected_net set, there's
        nothing to close to."""
        inst = _instance()
        rails_by_name = {str(r.name): r for r in inst.rails}
        for tt in inst.transfer_templates:
            has_variable = any(
                isinstance(rails_by_name.get(str(n)), SingleLegRail)
                and rails_by_name[str(n)].leg_direction == "Variable"  # type: ignore[union-attr]: narrowed by the prior isinstance(..., SingleLegRail) check
                for n in tt.leg_rails
            )
            if not has_variable:
                continue
            assert tt.expected_net is not None, (
                f"TransferTemplate {tt.name!r} has a Variable closing leg "
                f"but no expected_net — closure target is undefined"
            )

    def test_variable_leg_role_resolves_to_internal_singleton(self) -> None:
        """The Variable closing leg lands on a suspense GL — typically a
        singleton internal Account, not a template role. Templates can't
        host Variable closures because they'd need a per-instance suspense
        which the SPEC doesn't model."""
        inst = _instance()
        singleton_internal_roles = {
            str(a.role) for a in inst.accounts
            if a.scope == "internal" and a.role is not None
        }
        for r in inst.rails:
            if not isinstance(r, SingleLegRail):
                continue
            if r.leg_direction != "Variable":
                continue
            for role in r.leg_role:
                assert str(role) in singleton_internal_roles, (
                    f"Variable rail {r.name!r}.leg_role={role!r} doesn't "
                    f"resolve to a singleton internal Account; closure "
                    f"semantics expect a suspense GL"
                )


# ---------------------------------------------------------------------------
# 3. XOR group enforcement
# ---------------------------------------------------------------------------


class TestXorGroupEnforcement:
    """Chain XOR semantics under Z.A: a multi-children Chain row
    encodes 'exactly one fires per parent firing.' Each row IS one
    parent, so cross-parent contradictions are unrepresentable in the
    new grammar."""

    def test_multi_children_rows_are_non_aggregating(self) -> None:
        """S4: aggregating rails MUST NOT appear in any Chain.children
        (they sweep on a cadence, not on per-Transfer parent triggers).
        Multi-children (XOR) rows are the most common case — they MUST
        target non-aggregating rails or templates."""
        inst = _instance()
        aggregating_names = {str(r.name) for r in inst.rails if r.aggregating}
        for c in inst.chains:
            if len(c.children) < 2:
                continue
            for child in c.children:
                assert str(child) not in aggregating_names, (
                    f"Chain row parent={c.parent!r} children={list(c.children)!r}: "
                    f"{child!r} is aggregating — XOR groups can only target "
                    f"non-aggregating rails or templates."
                )

    def test_payout_vehicle_xor_chain_has_three_alternatives(self) -> None:
        """sasquatch_pr's merchant payout Chain row should list exactly
        the three documented payout alternatives (ACH, Wire, Check) as
        XOR siblings under MerchantSettlementCycle."""
        inst = _instance()
        payout = next(
            (
                c for c in inst.chains
                if str(c.parent) == "MerchantSettlementCycle"
                and len(c.children) >= 2
            ),
            None,
        )
        assert payout is not None, (
            "Expected the merchant payout XOR row under MerchantSettlementCycle"
        )
        children = sorted(str(ch) for ch in payout.children)
        assert children == [
            "MerchantPayoutACH", "MerchantPayoutCheck", "MerchantPayoutWire",
        ]


# ---------------------------------------------------------------------------
# 4. AggregatingRail bundling
# ---------------------------------------------------------------------------


class TestAggregatingRailBundling:
    """Bundling semantics: an aggregating rail rolls up activity matching
    bundles_activity selectors on its declared cadence. SPEC §Aggregating
    Rails."""

    def test_aggregating_rails_have_cadence_and_bundles_activity(self) -> None:
        """S5 — already validator-enforced; mirror here so a SPEC
        loosening surfaces in this test file too."""
        inst = _instance()
        for r in inst.rails:
            if not r.aggregating:
                continue
            assert r.cadence is not None, (
                f"Aggregating rail {r.name!r} missing cadence"
            )
            assert r.bundles_activity, (
                f"Aggregating rail {r.name!r} missing bundles_activity"
            )

    def test_max_unbundled_age_only_on_bundled_rails(self) -> None:
        """R8 — already validator-enforced; mirror here. Walks every rail
        with max_unbundled_age set and confirms it's referenced by some
        aggregating rail's bundles_activity (by name OR by transfer_type)."""
        inst = _instance()
        bundled_names: set[str] = set()
        bundled_transfer_types: set[str] = set()
        for r in inst.rails:
            if not r.aggregating:
                continue
            for sel in r.bundles_activity:
                sel_str = str(sel)
                if "." in sel_str:
                    # Dotted form (Template.LegRail).
                    _, _, leg = sel_str.partition(".")
                    bundled_names.add(leg)
                else:
                    bundled_names.add(sel_str)
                    bundled_transfer_types.add(sel_str)
        for r in inst.rails:
            if r.max_unbundled_age is None:
                continue
            assert (
                str(r.name) in bundled_names
                or r.name in bundled_transfer_types
            ), (
                f"Rail {r.name!r} has max_unbundled_age but isn't bundled by "
                f"any aggregating rail; the watch will never fire"
            )

    def test_external_card_settlement_bundles_merchant_card_sale(self) -> None:
        """Specifically: ExternalCardSettlement (PR-side single-leg
        external aggregator) bundles MerchantCardSale activity. The
        max_unbundled_age on MerchantCardSale is the L1 invariant
        surfacing the unbundled-stuck exception."""
        inst = _instance()
        ecs = next(
            r for r in inst.rails if str(r.name) == "ExternalCardSettlement"
        )
        assert ecs.aggregating
        assert "MerchantCardSale" in ecs.bundles_activity
        sale = next(r for r in inst.rails if str(r.name) == "MerchantCardSale")
        assert sale.max_unbundled_age is not None, (
            "MerchantCardSale must have max_unbundled_age for the "
            "L1 stuck_unbundled invariant to fire on aged un-bundled rows"
        )

    def test_aggregating_rail_cadence_uses_v1_vocabulary(self) -> None:
        """V2 — already validator-enforced; mirror as a guard. Each
        aggregating rail's cadence string matches the SPEC's v1
        CadenceExpression vocabulary."""
        import re
        v1_patterns = (
            re.compile(r"^intraday-\d+h$"),
            re.compile(r"^daily-eod$"),
            re.compile(r"^daily-bod$"),
            re.compile(r"^weekly-(mon|tue|wed|thu|fri|sat|sun)$"),
            re.compile(r"^monthly-eom$"),
            re.compile(r"^monthly-bom$"),
            re.compile(r"^monthly-\d+$"),
        )
        inst = _instance()
        for r in inst.rails:
            if not r.aggregating or r.cadence is None:
                continue
            assert any(p.match(r.cadence) for p in v1_patterns), (
                f"Aggregating rail {r.name!r}.cadence={r.cadence!r} doesn't "
                f"match any v1 CadenceExpression pattern"
            )
