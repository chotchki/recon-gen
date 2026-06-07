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


# Phase CP (2026-06-06) — the two tests that lived here
# (test_fuzzer_emits_role_business_day_offsets +
# test_fuzzer_seed_emits_distinct_business_day_offsets_for_at_least_two_roles)
# were deleted with the top-level role_business_day_offsets field.
# Offsets now live per-Account / per-AccountTemplate; CP.7 / CP.8 cover
# the new per-entity bookkeeping under the generalized fuzz coverage
# gate.


# ---------------------------------------------------------------------------
# CP.8 — generalized fuzz-coverage anti-drift test
# ---------------------------------------------------------------------------
#
# Sample N=100 fuzz seeds; for every optional + collection field on every
# L2 primitive walked, assert across the sample:
#   (a) at least one seed produces a non-None value — anti-drift against
#       the fuzz generator forgetting an optional field.
#   (b) at least one seed produces None — anti-regression against
#       "fuzz always populates everything" (real customer L2s have
#       mixed coverage; the fuzzer should mirror that).
#   (c) at least one collection field has len > 0.
#   (d) at least one collection field has len == 0 — same anti-regression
#       shape, on the collection axis.
#
# CARVE-OUTS (each names the rule that puts the field in "won't ever
# hit this half" territory). Carve-outs are escape hatches against the
# gate, not freebies: each one cites the validator rule / SPEC clause
# / fuzz design choice that makes the half unreachable, so reviewers
# can challenge a carve-out the same way they'd challenge a `# noqa`.
# When the underlying constraint relaxes (e.g. SPEC allows empty
# Chain.children), the carve-out should be deleted, not extended.
#
# Each tuple is (cls_name, field_name, axis): axis in {"a","b","c","d"}.
_CP_8_CARVE_OUTS: frozenset[tuple[str, str, str]] = frozenset({
    # Chain.children — SPEC requires at least one child (singleton ⇒
    # required, multi ⇒ XOR; empty has no firing semantic). Validator
    # rejects empty children.
    ("Chain", "children", "d"),
    # TransferTemplate.transfer_key + leg_rails — validator-required
    # non-empty (S5 / S6: a template with no transfer_key has no
    # grouping shape; a template with no leg_rails has no legs).
    ("TransferTemplate", "transfer_key", "d"),
    ("TransferTemplate", "leg_rails", "d"),
    # TwoLegRail.source_role / destination_role + SingleLegRail.leg_role
    # — RoleExpression is always non-empty (the validator's
    # role-resolution rule walks each element; an empty role-tuple is
    # an unrenderable rail).
    ("TwoLegRail", "source_role", "d"),
    ("TwoLegRail", "destination_role", "d"),
    ("SingleLegRail", "leg_role", "d"),
    # FiringsTypicalPerPeriod.count_range — fixed-arity tuple[int, int];
    # never empty by construction (validator W1a-c: min <= max).
    ("FiringsTypicalPerPeriod", "count_range", "d"),
    # --- fuzz-design carve-outs (NOT validator-required; the fuzz
    # generator just doesn't widen here yet) -----------------------------
    # Account.name — fuzz always sets a name (`f"Internal Account {i:02d}"`).
    # Widening fuzz to sometimes omit name is a follow-up; carve out (b)
    # so this gate stays green until that widen lands.
    ("Account", "name", "b"),
    # Account.parent_role — fuzz only sets parent_role on
    # AccountTemplates, never on root Accounts. (R2 allows account
    # parent_role to point at any role; fuzz doesn't exercise that.)
    ("Account", "parent_role", "a"),
    # AccountTemplate.parent_role — fuzz always sets it (R3 makes the
    # template's parent_role useful for instance materialization, even
    # though the loader allows None at the type level). Widening fuzz
    # to sometimes omit it is a follow-up.
    ("AccountTemplate", "parent_role", "b"),
    # AccountTemplate.expected_eod_balance / instance_id_template /
    # instance_name_template — fuzz doesn't set these today. Widening
    # is a follow-up.
    ("AccountTemplate", "expected_eod_balance", "a"),
    ("AccountTemplate", "instance_id_template", "a"),
    ("AccountTemplate", "instance_name_template", "a"),
    # TwoLegRail.posted_requirements / metadata_value_examples — fuzz
    # never emits non-empty values here.
    ("TwoLegRail", "posted_requirements", "c"),
    ("TwoLegRail", "metadata_value_examples", "c"),
    # SingleLegRail — same shape as TwoLegRail for these.
    ("SingleLegRail", "origin", "b"),
    ("SingleLegRail", "posted_requirements", "c"),
    ("SingleLegRail", "bundles_activity", "c"),
    ("SingleLegRail", "cadence", "a"),
    ("SingleLegRail", "metadata_value_examples", "c"),
    # TransferTemplate.firings_typical_per_period — fuzz never sets it
    # on TTs (set on rails by _build_rails, not on the TT shape).
    ("TransferTemplate", "firings_typical_per_period", "a"),
    # Chain.description — fuzz never sets a chain description.
    ("Chain", "description", "a"),
})


def _is_collection_origin(origin: object) -> bool:
    """Per CP.8 spec — origin is a collection if it's tuple / list /
    set / frozenset / dict. Note: this returns True even for fixed-
    arity tuple shapes like `tuple[int, int]`; carve-outs on (d)
    handle the "never empty by construction" cases explicitly.
    """
    # origin comes from typing.get_origin which returns object|None per
    # the stubs; identity-compare against the concrete builtin types.
    return (
        origin is tuple
        or origin is list
        or origin is set
        or origin is frozenset
        or origin is dict
    )


def test_fuzzer_populates_every_optional_field(tmp_path: "Path") -> None:
    """CP.8 — sample N=100 fuzz seeds; assert (a)/(b)/(c)/(d) holds for
    every optional + collection field on every L2 primitive walked,
    modulo the _CP_8_CARVE_OUTS escape hatches.

    Walks the L2Instance dataclass tree recursively — every Rail
    subtype, every ChainChildSpec, every FiringsTypicalPerPeriod.
    For each (cls, field) seen across the 100 seeds, records:
      - saw_non_none: at least one observed value was non-None
      - saw_none:     at least one observed value was None
      - saw_nonempty: at least one observed collection had len > 0
      - saw_empty:    at least one observed collection had len == 0

    The assertion message names the specific field + which half failed
    so a regression triages without re-reading this comment block.
    """
    import dataclasses as _dc  # noqa: PLC0415 — lazy
    from collections import defaultdict  # noqa: PLC0415 — lazy
    from typing import (  # noqa: PLC0415 — lazy
        get_args, get_origin, get_type_hints,
    )

    saw_non_none: dict[tuple[str, str], bool] = defaultdict(bool)
    saw_none: dict[tuple[str, str], bool] = defaultdict(bool)
    saw_nonempty: dict[tuple[str, str], bool] = defaultdict(bool)
    saw_empty: dict[tuple[str, str], bool] = defaultdict(bool)
    classes: dict[str, type] = {}

    def _walk(obj: object) -> None:
        if not _dc.is_dataclass(obj) or isinstance(obj, type):
            return
        cls = type(obj)
        classes[cls.__name__] = cls
        for f in _dc.fields(cls):  # pyright: ignore[reportArgumentType]: narrowed by is_dataclass + not-isinstance(type) above; pyright's protocol union doesn't preserve the narrowing
            value: object = getattr(obj, f.name)
            key = (cls.__name__, f.name)
            if value is None:
                saw_none[key] = True
            else:
                saw_non_none[key] = True
            if isinstance(value, (tuple, list, set, frozenset)):
                items: "list[object]" = list(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]: collection element type is `object`; the walk doesn't care
                if items:
                    saw_nonempty[key] = True
                    for item in items:
                        _walk(item)
                else:
                    saw_empty[key] = True
            elif isinstance(value, dict):
                vals: "list[object]" = list(value.values())  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]: value type is `object` here
                if vals:
                    saw_nonempty[key] = True
                    for item in vals:
                        _walk(item)
                else:
                    saw_empty[key] = True
            elif _dc.is_dataclass(value):
                _walk(value)

    for seed in META_GUARD_SEEDS:
        yaml_text = random_l2_yaml(seed)
        p = tmp_path / f"fuzz_{seed}.yaml"
        p.write_text(yaml_text)
        inst = load_instance(p)
        for collection in (
            inst.accounts, inst.account_templates, inst.rails,
            inst.transfer_templates, inst.chains, inst.limit_schedules,
        ):
            for entity in collection:
                _walk(entity)

    failures: list[str] = []
    for cname, cls in classes.items():
        try:
            hints = get_type_hints(cls)
        except Exception:  # noqa: BLE001 — type-hint resolution failure shouldn't crash the gate
            hints = {}
        for f in _dc.fields(cls):
            hint = hints.get(f.name, f.type)
            args = get_args(hint)
            origin = get_origin(hint)
            is_optional = type(None) in args
            is_collection = _is_collection_origin(origin)
            key = (cname, f.name)
            if is_optional:
                if (cname, f.name, "a") not in _CP_8_CARVE_OUTS and not saw_non_none[key]:
                    failures.append(
                        f"{cname}.{f.name}: (a) failed — fuzz generator "
                        f"never populated this field across N={len(META_GUARD_SEEDS)} "
                        f"seeds. Either widen the fuzzer to emit this "
                        f"field, or add an (a)-carve-out citing the rule."
                    )
                if (cname, f.name, "b") not in _CP_8_CARVE_OUTS and not saw_none[key]:
                    failures.append(
                        f"{cname}.{f.name}: (b) failed — fuzz generator "
                        f"always emitted a value (None never observed) "
                        f"across N={len(META_GUARD_SEEDS)} seeds. Real "
                        f"customer L2s have mixed coverage — widen the "
                        f"fuzzer to sometimes omit, or add a (b)-carve-out."
                    )
            if is_collection:
                if (cname, f.name, "c") not in _CP_8_CARVE_OUTS and not saw_nonempty[key]:
                    failures.append(
                        f"{cname}.{f.name}: (c) failed — collection "
                        f"field was always empty across N={len(META_GUARD_SEEDS)} "
                        f"seeds. Widen the fuzzer or add a (c)-carve-out."
                    )
                if (cname, f.name, "d") not in _CP_8_CARVE_OUTS and not saw_empty[key]:
                    failures.append(
                        f"{cname}.{f.name}: (d) failed — collection "
                        f"field was never empty across N={len(META_GUARD_SEEDS)} "
                        f"seeds (always had at least one element). Widen "
                        f"the fuzzer to sometimes emit empty, or add a "
                        f"(d)-carve-out citing the constraint."
                    )

    assert not failures, (
        f"CP.8 fuzz-coverage gate detected {len(failures)} field(s) "
        f"with under-exercised optional/collection coverage:\n  - "
        + "\n  - ".join(failures)
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
