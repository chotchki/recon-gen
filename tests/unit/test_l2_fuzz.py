"""Fuzzer meta-guard (M.2d.9.2).

This file is the validation FOR the validator: it runs the
``random_l2_yaml(seed)`` fuzzer across many seeds and asserts that
every emitted YAML loads + cross-entity-validates without raising.
A regression here means the fuzzer itself produces invalid YAML —
catch it before the M.2d.8 contract matrix tries (and gives an
opaque rail-resolution failure instead of "fuzzer produces invalid
output").

Three properties asserted:

1. **Validity** — every seed in ``range(100)`` produces YAML that
   ``load_instance`` accepts (which transitively runs cross-entity
   ``validate``).
2. **Determinism** — same seed = byte-identical output across calls.
3. **Coverage** — across 100 seeds, the fuzzer exercises every
   primitive kind (account, account_template, rail, transfer_template,
   chain, limit_schedule) at least once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.l2 import load_instance

from tests.l2.fuzz import FuzzPlan, random_l2_yaml, random_l2_yaml_from_plan


# 100 seeds covers a lot of variation while keeping wall time low
# (current per-seed cost: well under 10ms).
META_GUARD_SEEDS = list(range(100))


@pytest.mark.parametrize("seed", META_GUARD_SEEDS)
def test_fuzzer_output_loads_and_validates(seed: int, tmp_path: Path) -> None:
    """Every seed produces YAML that ``load_instance`` accepts."""
    yaml_text = random_l2_yaml(seed)
    yaml_path = tmp_path / f"fuzz_{seed}.yaml"
    yaml_path.write_text(yaml_text)
    # load_instance(validate=True) by default — so a single call
    # exercises both the loader's per-entity rules AND the
    # cross-entity validator.
    inst = load_instance(yaml_path)
    # Z.C: the L2 yaml no longer carries an instance: key; per-seed
    # identity (when needed) lives in the caller-chosen yaml basename.
    assert inst.accounts, f"seed={seed}: fuzzer emitted no accounts"


@pytest.mark.parametrize("seed", [0, 7, 42, 999, 12345])
def test_fuzzer_is_byte_deterministic(seed: int) -> None:
    """Same seed = byte-identical YAML across calls."""
    a = random_l2_yaml(seed)
    b = random_l2_yaml(seed)
    assert a == b, (
        f"seed={seed}: fuzzer is not deterministic — output differs "
        f"between calls. (likely an unseeded random source somewhere)"
    )


def test_fuzzer_exercises_every_primitive_kind_across_seeds(
    tmp_path: Path,
) -> None:
    """Across 100 seeds, the fuzzer produces at least one of every
    primitive kind. If this fails, the fuzzer's variation surface has
    a hole — some primitive never gets generated.
    """
    saw = {
        "accounts": False,
        "account_templates": False,
        "rails": False,
        "transfer_templates": False,
        "chains": False,
        "limit_schedules": False,
        # Specific shapes worth checking too:
        "two_leg_rail": False,
        "single_leg_rail": False,
        "aggregating_rail": False,
        "rail_with_max_pending_age": False,
        "rail_with_max_unbundled_age": False,
        "chain_with_multiple_children": False,
        # AB.2.6.fuzz — confirm the fuzzer emits at least one chain whose
        # singleton child is a TransferTemplate (template-as-chain-child
        # shape, gap doc §3). Without this, the AB.2.3
        # chain_parent_disagreement matview + the AB.2 plant scaffold
        # ship un-exercised under the fuzz axis.
        "chain_with_template_child": False,
        # AB.3.5.fuzz — confirm the fuzzer emits at least one
        # TransferTemplate carrying a non-empty leg_rail_xor_groups
        # entry. Without this, the C1 rewrite + AB.3.3 xor_group_violation
        # matview + AB.3.4 picker + AB.3.5/.5b plant scaffolds ship
        # un-exercised under the fuzz axis. _maybe_inject_xor_template
        # fires with ~50% probability per seed so this lands in roughly
        # half of META_GUARD_SEEDS.
        "xor_grouped_template": False,
        # AB.4.5.fuzz — confirm the fuzzer emits at least one chain
        # with fan_in=True. _build_chains gates fan_in on template-child
        # singleton chains at ~20% probability, so this lands in
        # roughly 20% × (template-child rate) × META_GUARD_SEEDS — sized
        # to comfortably cover within the seed pool.
        "fan_in_chain": False,
        # AB.5.5.fuzz — confirm the fuzzer emits at least one rail
        # with amount_typical_range. _build_rails gates at ~30%
        # probability on non-aggregating rails so this comfortably
        # lands within META_GUARD_SEEDS.
        "rail_with_amount_typical_range": False,
        # AF.5.fuzz — confirm the fuzzer emits at least one rail with
        # firings_typical_per_period. _build_rails gates at ~30%
        # probability on non-aggregating rails (independent of the
        # amount_typical_range roll), so this lands comfortably within
        # META_GUARD_SEEDS.
        "rail_with_firings_typical_per_period": False,
    }
    for seed in META_GUARD_SEEDS:
        yaml_text = random_l2_yaml(seed)
        p = tmp_path / f"fuzz_{seed}.yaml"
        p.write_text(yaml_text)
        inst = load_instance(p)
        if inst.accounts:
            saw["accounts"] = True
        if inst.account_templates:
            saw["account_templates"] = True
        if inst.rails:
            saw["rails"] = True
        if inst.transfer_templates:
            saw["transfer_templates"] = True
        if inst.chains:
            saw["chains"] = True
        if inst.limit_schedules:
            saw["limit_schedules"] = True
        for r in inst.rails:
            from recon_gen.common.l2 import SingleLegRail, TwoLegRail
            if isinstance(r, TwoLegRail):
                saw["two_leg_rail"] = True
            if isinstance(r, SingleLegRail):
                saw["single_leg_rail"] = True
            if r.aggregating:
                saw["aggregating_rail"] = True
            if r.max_pending_age is not None:
                saw["rail_with_max_pending_age"] = True
            if r.max_unbundled_age is not None:
                saw["rail_with_max_unbundled_age"] = True
            # AB.5.5.fuzz — rail with amount_typical_range.
            if r.amount_typical_range is not None:
                saw["rail_with_amount_typical_range"] = True
            # AF.5.fuzz — rail with firings_typical_per_period.
            if r.firings_typical_per_period is not None:
                saw["rail_with_firings_typical_per_period"] = True
        template_name_set = {t.name for t in inst.transfer_templates}
        for c in inst.chains:
            # Z.A: a multi-children Chain row encodes XOR alternation.
            if len(c.children) >= 2:
                saw["chain_with_multiple_children"] = True
            # AB.2.6.fuzz — singleton child resolving to a template.
            if len(c.children) == 1 and c.children[0].name in template_name_set:
                saw["chain_with_template_child"] = True
            # AB.4.5.fuzz — chain with any per-child fan_in entry.
            if any(child.fan_in for child in c.children):
                saw["fan_in_chain"] = True
        # AB.3.5.fuzz — TT with non-empty leg_rail_xor_groups.
        for t in inst.transfer_templates:
            if t.leg_rail_xor_groups:
                saw["xor_grouped_template"] = True
        if all(saw.values()):
            return  # short-circuit on full coverage
    missing = [k for k, v in saw.items() if not v]
    assert not missing, (
        f"After {len(META_GUARD_SEEDS)} seeds the fuzzer never produced: "
        f"{missing!r}. Either widen the variation surface in fuzz.py OR "
        f"explicitly accept the gap with a comment in this test."
    )


@pytest.mark.parametrize("seed", [0, 7, 42, 999, 12345, 227844959])
def test_fuzzer_emits_role_business_day_offsets(
    seed: int, tmp_path: Path,
) -> None:
    """Every fuzz instance carries `role_business_day_offsets` covering
    every declared role (M.4.4.14). The fuzzer is the consumer of this
    capability — production fixtures stay midnight-aligned. The map's
    presence is what guarantees the fuzz matrix exercises any future
    L1 view that depends on per-role business-day boundaries differing.
    """
    yaml_text = random_l2_yaml(seed)
    yaml_path = tmp_path / f"fuzz_{seed}.yaml"
    yaml_path.write_text(yaml_text)
    inst = load_instance(yaml_path)

    offsets = inst.role_business_day_offsets
    assert offsets is not None and offsets, (
        f"seed={seed}: fuzzer must emit role_business_day_offsets so the "
        f"emitted seed exercises per-role business-day variation"
    )

    declared_roles = (
        {str(a.role) for a in inst.accounts}
        | {str(t.role) for t in inst.account_templates}
    )
    assert set(offsets.keys()) == declared_roles, (
        f"seed={seed}: role_business_day_offsets keys {set(offsets.keys())!r} "
        f"don't match declared roles {declared_roles!r} — should cover every "
        f"role exactly once"
    )
    for role, hours in offsets.items():
        assert 0 <= hours < 24, (
            f"seed={seed}: role {role!r} offset {hours} out of [0, 24)"
        )


def test_fuzzer_seed_emits_distinct_business_day_offsets_for_at_least_two_roles() -> None:
    """Across the offset matrix the fuzzer picks from
    ``_BUSINESS_DAY_OFFSET_CHOICES`` (0, 5, 9, 14, 17, 23). Any seed
    with >=2 declared roles should typically produce >=2 distinct hour
    values. Hitting all-same is statistically possible (p = 1/6 per
    additional role) but vanishingly unlikely across 100 seeds — assert
    that AT LEAST one fuzz instance in our standard 100-seed matrix
    produces non-uniform offsets, otherwise the variation is broken.
    """
    saw_distinct = False
    for seed in META_GUARD_SEEDS:
        yaml_text = random_l2_yaml(seed)
        from io import StringIO
        import yaml as _yaml
        parsed = _yaml.safe_load(StringIO(yaml_text))
        offsets = parsed.get("role_business_day_offsets", {})
        if len(set(offsets.values())) >= 2:
            saw_distinct = True
            break
    assert saw_distinct, (
        f"After {len(META_GUARD_SEEDS)} seeds the fuzzer never produced a "
        f"role_business_day_offsets dict with >=2 distinct hour values. "
        f"Variation across roles is the whole point of M.4.4.14 — check "
        f"_BUSINESS_DAY_OFFSET_CHOICES + _build_role_business_day_offsets."
    )


# ---------------------------------------------------------------------------
# CF.3.spike — public ``random_l2_yaml_from_plan(plan)`` entry
# ---------------------------------------------------------------------------


def test_random_l2_yaml_from_plan_heavy_density(tmp_path: Path) -> None:
    """`random_l2_yaml_from_plan(plan)` accepts plans outside the default
    ranges sampled by `random_l2_yaml(seed)` — heavy density (100 rails /
    30 templates / 12 chains) loads + validates without raising.

    This is the CF.3.spike harness's entry point. The default
    `random_l2_yaml` knob ranges cap at 8 rails / 3 templates / 3 chains;
    `_build_instance` was untested at the heavy end before the spike,
    so this guards against future regressions in any layer that scales
    super-linearly on the heavy axes (transfer template reconciliation,
    chain anchoring, leg_rail_xor_groups partitioning).
    """
    plan = FuzzPlan(
        seed=42,
        n_singleton_internal=8,
        n_singleton_external=20,
        n_templates=4,
        n_rails=100,
        n_transfer_templates=30,
        n_chains=12,
        n_limit_schedules=6,
        two_leg_ratio=0.6,
        aggregating_count=2,
        pending_age_probability=0.3,
        description_probability=0.7,
    )
    yaml_text = random_l2_yaml_from_plan(plan)
    p = tmp_path / "heavy.yaml"
    p.write_text(yaml_text)
    inst = load_instance(p)
    # _build_instance may round up by 1-3 entities in each axis as it
    # reconciles constraints (e.g. XOR template injection); assert at
    # least the requested counts, not exact equality.
    assert len(inst.rails) >= plan.n_rails, (
        f"requested {plan.n_rails} rails, got {len(inst.rails)} — "
        f"heavy plan was clamped or pruned somewhere downstream"
    )
    assert len(inst.transfer_templates) >= plan.n_transfer_templates
    assert len(inst.chains) == plan.n_chains
    assert len(inst.limit_schedules) == plan.n_limit_schedules


def test_random_l2_yaml_from_plan_is_byte_deterministic() -> None:
    """Same plan = byte-identical YAML across calls.

    `random_l2_yaml(seed)` already pins this for the sampling path;
    `random_l2_yaml_from_plan(plan)` reuses the same _build_instance
    pipeline so it should hold too. Cheap check; expensive to debug
    if it ever stops being true.
    """
    plan = FuzzPlan(
        seed=2026,
        n_singleton_internal=4,
        n_singleton_external=5,
        n_templates=2,
        n_rails=20,
        n_transfer_templates=5,
        n_chains=3,
        n_limit_schedules=2,
        two_leg_ratio=0.5,
        aggregating_count=1,
        pending_age_probability=0.25,
        description_probability=0.5,
    )
    a = random_l2_yaml_from_plan(plan)
    b = random_l2_yaml_from_plan(plan)
    assert a == b
