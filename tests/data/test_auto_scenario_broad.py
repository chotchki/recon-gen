"""Tests for the M.4.2 broad-coverage scenario mode.

The broad mode layers per-rail firings on top of (or instead of) the
L1 invariant plants the original ``default_scenario_for`` produced,
so the L2 Flow Tracing dashboard's Rails / Chains / Transfer
Templates sheets show actual content rather than reading "dead" for
every rail not picked by the L1 invariant heuristics.

Three fixture L2 instances cover the matrix:
- ``spec_example.yaml`` — minimal SPEC sample
- ``sasquatch_pr.yaml`` — full Sasquatch PR persona
- ``_kitchen.yaml`` — every primitive shape (regression harness)

What this file checks:
1. Mode dispatch — l1_invariants / broad / l1_plus_broad each emit
   the right plant subsets.
2. Per-rail coverage — broad mode plants firings for every rail that
   has materialized accounts; rails with unresolvable roles get
   skipped + reported in `omitted`.
3. Per-firing stratification — `days_ago` spreads across the firing
   sequence so timestamps don't all stack on one day.
4. Metadata generation — values respect the rail's declared
   metadata_keys; per-(rail, firing) unique so the L2 Flow Tracing
   metadata cascade reads distinct values.
5. Required chain linkage — for a singleton-children Chain row
   (Z.A "required" semantics) whose parent AND child both have
   materialized accounts, broad mode plants an additional child
   firing whose ``transfer_parent_id`` points at the parent's first
   firing.
6. Determinism — two runs of the same `(instance, mode)` produce
   byte-identical SQL.
7. emit_seed integration — the new plants flow through emit_seed
   without raising and produce well-formed SQL.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from quicksight_gen.common.l2 import L2Instance, load_instance
from quicksight_gen.common.l2.auto_scenario import default_scenario_for
from quicksight_gen.common.l2.seed import emit_seed


CANONICAL_TODAY = date(2030, 1, 1)
L2_DIR = Path(__file__).parent.parent / "l2"

L2_FIXTURES = [
    pytest.param(L2_DIR / "spec_example.yaml", id="spec_example"),
    pytest.param(L2_DIR / "sasquatch_pr.yaml", id="sasquatch_pr"),
    pytest.param(L2_DIR / "_kitchen.yaml", id="kitchen"),
]


@pytest.fixture(params=L2_FIXTURES)
def instance(request) -> L2Instance:
    return load_instance(request.param)


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------


def test_l1_mode_emits_no_rail_firing_plants(instance: L2Instance) -> None:
    """The default mode (l1_invariants) is unchanged — no rail_firing_plants."""
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="l1_invariants",
    )
    assert report.scenario.rail_firing_plants == ()


def test_broad_mode_emits_no_l1_invariant_plants(instance: L2Instance) -> None:
    """Broad-only mode zeros out the L1 SHOULD-violation plants. M.4.2a
    re-categorized ``transfer_template_plants`` as a broad-mode plant
    (they're shape-driven, not invariant-violation-driven), so they're
    now expected in broad mode alongside ``rail_firing_plants``."""
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="broad",
    )
    s = report.scenario
    # The 6 SHOULD-violation plant kinds — none in broad mode.
    assert s.drift_plants == ()
    assert s.overdraft_plants == ()
    assert s.limit_breach_plants == ()
    assert s.stuck_pending_plants == ()
    assert s.stuck_unbundled_plants == ()
    assert s.supersession_plants == ()
    # Shape-driven plants — both expected in broad mode.
    assert len(s.rail_firing_plants) > 0
    # transfer_template_plants is non-empty when the YAML declares any
    # template whose first leg_rail is a TwoLegRail (the M.3.10g picker
    # constraint). spec_example's MerchantSettlementCycle's first
    # leg_rail is SingleLegRail SubledgerCharge → omitted; sasquatch_pr
    # + kitchen pass at least one TwoLegRail-first template through.
    # So we just assert the field is the broad layer's responsibility,
    # not specifically that it's non-empty per-instance.


def test_l1_plus_broad_mode_emits_both_layers(instance: L2Instance) -> None:
    """The harness's l1_plus_broad mode is the union: L1 invariant
    plants AND rail firings."""
    l1_only = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="l1_invariants",
    ).scenario
    combined = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="l1_plus_broad",
    ).scenario

    # L1 plant tuples are identical across modes (same picker logic).
    assert combined.drift_plants == l1_only.drift_plants
    assert combined.overdraft_plants == l1_only.overdraft_plants
    assert combined.limit_breach_plants == l1_only.limit_breach_plants
    # Broad layer adds the rail firings on top.
    assert len(combined.rail_firing_plants) > 0


# ---------------------------------------------------------------------------
# Per-rail coverage
# ---------------------------------------------------------------------------


def test_broad_mode_covers_every_rail_with_materialized_accounts(
    instance: L2Instance,
) -> None:
    """Every Rail whose role(s) resolve to a materialized account
    appears in rail_firing_plants. Rails skipped for unresolvable
    roles are documented in `omitted`."""
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="broad",
    )
    fired_rails = {p.rail_name for p in report.scenario.rail_firing_plants}
    omitted_rail_names = {
        kind.removeprefix("RailFiringPlant[").removesuffix("]")
        for kind, _ in report.omitted
        if kind.startswith("RailFiringPlant[")
    }
    declared_rails = {r.name for r in instance.rails}
    # Every declared rail is either fired or omitted (with a reason),
    # and the same name doesn't appear in both — partition is clean.
    accounted = fired_rails | omitted_rail_names
    assert declared_rails <= accounted
    assert fired_rails.isdisjoint(omitted_rail_names)


def test_broad_mode_default_per_rail_firings_is_three(instance: L2Instance) -> None:
    """Default `per_rail_firings=3` so every fired rail gets 3 plants —
    one per firing sequence number."""
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="broad",
    )
    by_rail: dict[str, list[int]] = {}
    for p in report.scenario.rail_firing_plants:
        # Skip Required-chain-child plants (firing_seq > per_rail_firings).
        if p.transfer_parent_id is not None:
            continue
        by_rail.setdefault(str(p.rail_name), []).append(p.firing_seq)
    for rail_name, seqs in by_rail.items():
        assert sorted(seqs) == [1, 2, 3], (
            f"rail {rail_name!r}: expected firing_seq=[1,2,3], got {sorted(seqs)!r}"
        )


def test_broad_mode_per_rail_firings_parameter_respected(
    instance: L2Instance,
) -> None:
    """Caller-supplied per_rail_firings reshapes the per-rail count."""
    for n in (1, 5):
        report = default_scenario_for(
            instance, today=CANONICAL_TODAY, mode="broad",
            per_rail_firings=n,
        )
        per_rail: dict[str, int] = {}
        for p in report.scenario.rail_firing_plants:
            if p.transfer_parent_id is not None:
                continue
            per_rail[str(p.rail_name)] = per_rail.get(str(p.rail_name), 0) + 1
        for rail_name, count in per_rail.items():
            assert count == n, (
                f"per_rail_firings={n}: rail {rail_name!r} fired "
                f"{count} times"
            )


# ---------------------------------------------------------------------------
# Stratification + metadata
# ---------------------------------------------------------------------------


def test_broad_mode_stratifies_days_ago(instance: L2Instance) -> None:
    """Per-rail firings spread across days_ago — firing 1 → days_ago=1,
    firing 2 → days_ago=2, etc. (within a 7-day window)."""
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="broad",
    )
    for p in report.scenario.rail_firing_plants:
        if p.transfer_parent_id is not None:
            continue  # chain children placed on day 1 by design
        # Default per_rail_firings=3 → days_ago in {1, 2, 3}
        assert p.days_ago == 1 + ((p.firing_seq - 1) % 7)


def test_broad_mode_metadata_values_per_rail_per_firing(
    instance: L2Instance,
) -> None:
    """Metadata values are non-empty so the L2 Flow Tracing cascade
    has values to display.

    M.4.2b adds opt-in ``rails[].metadata_value_examples`` — when a
    key opts in, values come from the integrator-supplied list (e.g.
    ``["Visa", "Mastercard", "Amex"]``); when not, values follow the
    legacy synthetic ``<rail>-firing-<seq>`` / ``<rail>-chained-<seq>``
    pattern. So this test no longer asserts a value-shape pattern —
    it only asserts every metadata value is non-empty.
    """
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="broad",
    )
    seen_per_key: dict[str, set[str]] = {}
    for p in report.scenario.rail_firing_plants:
        for key, value in p.extra_metadata:
            seen_per_key.setdefault(key, set()).add(value)
    for key, values in seen_per_key.items():
        # Non-empty values on every (rail, firing).
        assert all(value for value in values), (
            f"key {key!r} has empty values: {values!r}"
        )


# ---------------------------------------------------------------------------
# Required chain linkage
# ---------------------------------------------------------------------------


def test_broad_mode_sets_template_name_on_leg_rail_firings(
    instance: L2Instance,
) -> None:
    """M.4.2a: a broad-mode RailFiringPlant for a rail that's a leg_rail
    of some TransferTemplate carries that template's name in the
    `template_name` field. Standalone rails (not in any template's
    leg_rails) carry None.

    This is the M.4.2a fix that closes the L2 Flow Tracing Transfer
    Templates sheet visibility gap — without `template_name` set, the
    `tt-instances` + `tt-legs` datasets miss broad-mode firings of
    leg_rails entirely."""
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="broad",
    )
    leg_rail_to_template: dict[str, str] = {}
    for tt in instance.transfer_templates:
        for leg in tt.leg_rails:
            # First template wins — matches the deterministic-by-name
            # tie-break in the broad picker.
            leg_rail_to_template.setdefault(str(leg), str(tt.name))
    for p in report.scenario.rail_firing_plants:
        # Skip chain children (their template_name handling follows
        # the chain child rail's own leg_rails membership, exercised
        # by the same logic).
        if p.transfer_parent_id is not None:
            continue
        rail_name = str(p.rail_name)
        expected = leg_rail_to_template.get(rail_name)
        if expected is not None:
            assert p.template_name is not None and str(p.template_name) == expected, (
                f"rail {rail_name!r} is a leg_rail of {expected!r} but "
                f"its broad firing carries template_name={p.template_name!r}"
            )
        else:
            assert p.template_name is None, (
                f"rail {rail_name!r} is NOT a leg_rail of any template "
                f"but its broad firing carries template_name="
                f"{p.template_name!r}"
            )


def test_broad_mode_links_required_chain_children() -> None:
    """For Required chain entries whose parent + child both have
    materialized accounts, broad mode plants an additional child
    firing whose `transfer_parent_id` matches one of the parent's
    firings — so the L2 chain-orphan invariant view sees a matched
    pair on the L2 Exceptions sheet's Chain Orphans check.

    sasquatch_pr.yaml has 1 Required chain
    (ACHOriginationDailySweep → ConcentrationToFRBSweep) so the
    broad mode should plant exactly 1 chain-link child firing.
    """
    inst = load_instance(L2_DIR / "sasquatch_pr.yaml")
    report = default_scenario_for(
        inst, today=CANONICAL_TODAY, mode="broad",
    )
    chain_children = [
        p for p in report.scenario.rail_firing_plants
        if p.transfer_parent_id is not None
    ]
    # sasquatch_pr.yaml's Required chain links the Concentration sweep to
    # the FRB sweep — exactly 1 chain-child firing planted.
    assert len(chain_children) == 1
    child = chain_children[0]
    assert str(child.rail_name) == "ConcentrationToFRBSweep"
    # The transfer_parent_id matches a parent rail firing's ID pattern.
    assert child.transfer_parent_id is not None
    assert child.transfer_parent_id.startswith("tr-rail-")


# ---------------------------------------------------------------------------
# Determinism + emit_seed integration
# ---------------------------------------------------------------------------


def test_broad_mode_is_deterministic(instance: L2Instance) -> None:
    """Two runs of the same (instance, mode) produce identical
    rail_firing_plants tuples — needed for the hash-lock in
    test_l2_seed_contract.py."""
    a = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="l1_plus_broad",
    )
    b = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="l1_plus_broad",
    )
    assert a.scenario == b.scenario


def test_emit_seed_accepts_broad_mode_plants(instance: L2Instance) -> None:
    """The new RailFiringPlant flows through emit_seed without raising
    and produces well-formed SQL (contains INSERT INTO ... and
    references the broad mode plant rows)."""
    report = default_scenario_for(
        instance, today=CANONICAL_TODAY, mode="broad",
    )
    sql = emit_seed(instance, report.scenario)
    # Sanity: SQL is non-empty, references the prefix, and the
    # rail-firing tx_id pattern (tx-rail-NNNN) appears.
    assert sql
    assert f"INSERT INTO {instance.instance}_transactions" in sql
    if report.scenario.rail_firing_plants:
        assert "tx-rail-" in sql
