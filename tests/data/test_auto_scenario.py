"""Tests for ``common.l2.auto_scenario`` (M.2d.6).

Two surfaces exercised here:

1. The pure auto-scenario derivation: given an L2 instance, return a
   ScenarioPlant covering each L1 invariant view AND a report of any
   plant kinds that couldn't be derived.
2. Determinism: the auto-scenario plus emit_seed produces a stable
   SHA256 against a fixed canonical date (the basis for the YAML's
   ``seed_hash`` field).

The CLI surface (``demo seed-l2``) has its own smoke tests in
``tests/test_cli_seed_l2.py``.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from recon_gen.common.l2 import Identifier, load_instance
from recon_gen.common.l2.auto_scenario import (
    AutoScenarioReport,
    default_scenario_for,
)
from recon_gen.common.l2.seed import emit_seed


SPEC_YAML = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
SASQUATCH_YAML = Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"
CANONICAL_TODAY = date(2030, 1, 1)


@pytest.fixture(scope="module")
def spec_instance():
    return load_instance(SPEC_YAML)


@pytest.fixture(scope="module")
def sasquatch_instance():
    return load_instance(SASQUATCH_YAML)


# -- Coverage --------------------------------------------------------------


def test_auto_scenario_against_spec_example_covers_all_six_plant_kinds(
    spec_instance,
) -> None:
    """spec_example.yaml is intentionally complete enough that the
    auto-scenario derives one of every L1-invariant plant kind. Its
    sole declared TransferTemplate (MerchantSettlementCycle) lists a
    SingleLegRail as its first leg_rail, which the M.3.10g first-cut
    TT picker can't handle — that's a known omission, not a bug."""
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    sc = report.scenario
    assert len(sc.template_instances) == 2
    assert len(sc.drift_plants) == 1
    assert len(sc.overdraft_plants) == 1
    assert len(sc.limit_breach_plants) == 1
    assert len(sc.inbound_cap_breach_plants) == 1  # AB.1.5.spec
    assert len(sc.stuck_pending_plants) == 1
    assert len(sc.stuck_unbundled_plants) == 1
    assert len(sc.supersession_plants) == 1
    assert len(sc.inv_fanout_plants) == 1
    # AB.2.6.spec — spec_example now carries one rail→template chain
    # (ReconciliationLeg → MerchantSettlementCycle), activating both
    # AB.2 plants (healthy + ETL-bug-disagreement).
    assert len(sc.two_template_chain_plants) == 1
    assert len(sc.chain_parent_disagreement_plants) == 1
    # AB.3.5: XorVariantMissedFiringPlant is omitted until AB.3.5.spec
    # lands an XOR-grouped template in spec_example. .spec will tighten
    # this to `len(sc.xor_variant_missed_firing_plants) == 1`.
    assert len(sc.xor_variant_missed_firing_plants) == 0
    # Permitted omissions: TT plants whose first leg_rail isn't TwoLeg,
    # plus the AB.3.5 XOR plant pending AB.3.5.spec.
    omitted_kinds = [kind for kind, _ in report.omitted]
    assert all(
        k.startswith("TransferTemplatePlant[") or k == "XorVariantMissedFiringPlant"
        for k in omitted_kinds
    ), f"Unexpected omissions: {report.omitted!r}"


def test_auto_scenario_against_sasquatch_pr_covers_all_six_plant_kinds(
    sasquatch_instance,
) -> None:
    """The full AR fixture also has enough surface for full coverage.
    Sasquatch's MerchantSettlementCycle's first leg_rail is a TwoLeg
    (MerchantCardSale), so the TT plant fires; InternalTransferCycle's
    first leg_rail is SingleLeg, so it's a known omission. M.4.2a moved
    `transfer_template_plants` from the L1 layer to the broad layer,
    so this 'full coverage' check uses ``mode='l1_plus_broad'`` to
    pick up both layers — the original 6 SHOULD-violation plants AND
    the broad-layer TT plants."""
    report = default_scenario_for(
        sasquatch_instance, today=CANONICAL_TODAY, mode="l1_plus_broad",
    )
    sc = report.scenario
    assert len(sc.drift_plants) >= 1
    assert len(sc.overdraft_plants) >= 1
    assert len(sc.limit_breach_plants) >= 1
    assert len(sc.stuck_pending_plants) >= 1
    assert len(sc.stuck_unbundled_plants) >= 1
    assert len(sc.supersession_plants) >= 1
    assert len(sc.inv_fanout_plants) >= 1
    assert len(sc.transfer_template_plants) >= 2  # 2 firings of one template
    # Sasquatch may or may not surface omissions depending on instance
    # shape — the key claim is that NO ALL-skip happens.
    omitted_kinds = [kind for kind, _ in report.omitted]
    assert "ALL" not in omitted_kinds


def test_auto_scenario_reports_omissions_for_minimal_yaml(tmp_path: Path) -> None:
    """An L2 missing LimitSchedules + aging-watch rails reports the
    derivable plants and lists the rest as omitted."""
    minimal = tmp_path / "minimal.yaml"
    minimal.write_text(
        "accounts:\n"
        "  - id: control\n"
        "    role: ControlAccount\n"
        "    scope: internal\n"
        "    expected_eod_balance: 0\n"
        "  - id: ext\n"
        "    role: ExternalParty\n"
        "    scope: external\n"
        "account_templates:\n"
        "  - role: CustomerSub\n"
        "    scope: internal\n"
        "    parent_role: ControlAccount\n"
        "rails:\n"
        # Two-leg inbound — supports drift + supersession picks
        "  - name: Inbound\n"
        "    source_role: ExternalParty\n"
        "    destination_role: CustomerSub\n"
        "    expected_net: 0\n"
        "    source_origin: ExternalForcePosted\n"
        "    destination_origin: InternalInitiated\n"
    )
    inst = load_instance(minimal)
    report = default_scenario_for(inst, today=CANONICAL_TODAY)
    omitted_kinds = {kind for kind, _ in report.omitted}
    # No LimitSchedule → no LimitBreachPlant
    # No max_pending_age → no StuckPendingPlant
    # No max_unbundled_age → no StuckUnbundledPlant
    assert {"LimitBreachPlant", "StuckPendingPlant",
            "StuckUnbundledPlant"} <= omitted_kinds
    # But drift + overdraft + supersession derive cleanly
    assert len(report.scenario.drift_plants) == 1
    assert len(report.scenario.overdraft_plants) == 1
    assert len(report.scenario.supersession_plants) == 1
    assert len(report.scenario.limit_breach_plants) == 0
    assert len(report.scenario.stuck_pending_plants) == 0
    assert len(report.scenario.stuck_unbundled_plants) == 0


def test_auto_scenario_with_no_template_omits_everything(tmp_path: Path) -> None:
    """An L2 with no AccountTemplate can't materialize customers — the
    auto-scenario reports 'ALL' as omitted and returns an empty plant."""
    bare = tmp_path / "bare.yaml"
    bare.write_text(
        "accounts:\n"
        "  - id: only\n"
        "    role: Only\n"
        "    scope: internal\n"
    )
    inst = load_instance(bare, validate=False)  # bare.yaml is intentionally minimal
    report = default_scenario_for(inst, today=CANONICAL_TODAY)
    assert report.scenario.template_instances == ()
    assert report.omitted == (("ALL", "no AccountTemplate declared in instance"),)


# -- Determinism (the basis for the locked-SQL files at
#    tests/data/_locked_seeds/) ----------------------------------------


def test_auto_scenario_emit_is_byte_deterministic(spec_instance) -> None:
    """Two runs of (default_scenario_for + emit_seed) on the same
    instance with the same canonical today produce byte-identical SQL."""
    report_a = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    report_b = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    sql_a = emit_seed(spec_instance, report_a.scenario, prefix="spec_example")
    sql_b = emit_seed(spec_instance, report_b.scenario, prefix="spec_example")
    assert sql_a == sql_b


# -- Persona-cleanliness (the M.2d.5 guard, applied to auto-scenario) ------


def test_auto_seed_against_spec_example_has_zero_persona_leaks(spec_instance) -> None:
    """The auto-scenario itself is persona-blind: against spec_example.yaml,
    the generated SQL contains no Sasquatch / SNB / FRB / etc. literals."""
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    sql = emit_seed(
        spec_instance, report.scenario, prefix="spec_example",
    ).lower()
    blocklist = ("sasquatch", "bigfoot", "yeti", "snb", "frb",
                 "cascadia", "juniper", "farmers exchange")
    leaks = [w for w in blocklist if w in sql]
    assert not leaks, (
        f"auto-scenario against spec_example.yaml leaked persona "
        f"literals: {leaks!r}"
    )


# -- Picker stability ------------------------------------------------------


def test_auto_scenario_drift_picks_external_counter_from_instance(
    spec_instance,
) -> None:
    """The drift plant's counter_account_id resolves to a real
    instance.accounts entry."""
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    drift = report.scenario.drift_plants[0]
    instance_account_ids = {a.id for a in spec_instance.accounts}
    assert drift.counter_account_id in instance_account_ids
    assert drift.rail_name == Identifier("ExternalRailInbound")


def test_auto_scenario_breach_amount_exceeds_cap(spec_instance) -> None:
    """The limit-breach plant's amount = cap * 1.5, guaranteed to breach."""
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    breach = report.scenario.limit_breach_plants[0]
    matching = next(
        ls for ls in spec_instance.limit_schedules
        if ls.rail == breach.rail_name
    )
    assert breach.amount > matching.cap


def test_auto_scenario_stuck_pending_age_exceeds_picked_rail_cap(
    spec_instance,
) -> None:
    """The stuck_pending plant's days_ago must exceed the picked rail's
    `max_pending_age` (in days) so the matview surfaces the row.

    Regression for M.4.4.13 — original code hardcoded `days_ago=2`,
    which silently failed for any picked rail with a cap >= 2 days
    (e.g., fuzz seed 227844959 picked a Rail_00 with `max_pending_age=P7D`).
    """
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    pending = report.scenario.stuck_pending_plants[0]
    matching = next(
        r for r in spec_instance.rails if r.name == pending.rail_name
    )
    assert matching.max_pending_age is not None, (
        "auto-scenario must only pick a rail with max_pending_age set"
    )
    cap_days = matching.max_pending_age.total_seconds() / 86400
    assert pending.days_ago > cap_days, (
        f"stuck_pending plant days_ago={pending.days_ago} doesn't exceed "
        f"picked rail {pending.rail_name!r}'s max_pending_age "
        f"({cap_days} days) — matview won't surface the row"
    )


def test_auto_scenario_stuck_unbundled_age_exceeds_picked_rail_cap(
    spec_instance,
) -> None:
    """Sister test of stuck_pending — the unbundled plant must
    similarly clear the picked rail's `max_unbundled_age` cap."""
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    unbundled = report.scenario.stuck_unbundled_plants[0]
    matching = next(
        r for r in spec_instance.rails if r.name == unbundled.rail_name
    )
    assert matching.max_unbundled_age is not None
    cap_days = matching.max_unbundled_age.total_seconds() / 86400
    assert unbundled.days_ago > cap_days, (
        f"stuck_unbundled plant days_ago={unbundled.days_ago} doesn't "
        f"exceed picked rail {unbundled.rail_name!r}'s max_unbundled_age "
        f"({cap_days} days)"
    )


def test_auto_scenario_inv_fanout_recipient_is_leaf_internal(
    spec_instance,
) -> None:
    """The InvFanoutPlant recipient MUST resolve to a leaf-internal
    account: its template_role is the materialized customer template's
    role, and the template carries scope=internal + non-NULL
    parent_role. This is the shape the
    ``<prefix>_inv_pair_rolling_anomalies`` matview filter requires
    (account_scope='internal' AND account_parent_role IS NOT NULL).
    Without this guarantee the fanout plant emits rows that get
    filtered out at matview time and the harness Layer 1b' assertion
    would fire on every run."""
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    plant = report.scenario.inv_fanout_plants[0]
    template_instance = next(
        ti for ti in report.scenario.template_instances
        if ti.account_id == plant.recipient_account_id
    )
    template = next(
        t for t in spec_instance.account_templates
        if t.role == template_instance.template_role
    )
    assert template.scope == "internal"
    assert template.parent_role is not None


def test_auto_scenario_inv_fanout_has_at_least_two_distinct_senders(
    spec_instance,
) -> None:
    """A fanout with one sender is structurally a degenerate single
    edge — the picker omits the plant rather than emit one. spec_example
    has multiple external + internal accounts so the picker grabs ≥ 2;
    confirm via the plant shape."""
    report = default_scenario_for(spec_instance, today=CANONICAL_TODAY)
    plant = report.scenario.inv_fanout_plants[0]
    assert len(plant.sender_account_ids) >= 2
    # No sender is the recipient (self-edges break the pair-rolling
    # window's cardinality assumption).
    assert plant.recipient_account_id not in plant.sender_account_ids
