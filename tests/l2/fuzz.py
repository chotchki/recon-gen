"""Random L2 YAML fuzzer (M.2d.9.1).

Public surface: ``random_l2_yaml(seed: int) -> str`` produces a
deterministic random valid L2 instance YAML. Same seed = byte-identical
output. The output passes ``load_instance`` + cross-entity ``validate``
without raising — every constraint (U1-U5, R1-R11, C1-C2, S1-S6,
V1-V2, O1) is satisfied at construction time, not via reject-and-retry.

Design notes:

- The generator is *layered*: roles → accounts/templates → rails →
  transfer_templates → chains → limit_schedules. Each layer can only
  reference identifiers declared by an earlier layer. This makes
  cross-entity validity a structural property of the generation
  algorithm rather than a post-hoc filter.

- A single ``random.Random(seed)`` is threaded through every helper.
  No ``os.urandom`` / ``time`` reads. Variation knobs sample at the
  top of each builder so the seed→output mapping is stable across
  refactors that don't touch the call ordering.

- YAML output is via ``yaml.safe_dump`` with ``sort_keys=False`` so
  insertion order survives — humans triaging a fuzz failure read it
  in the order the generator built it.

- The generator deliberately declines to fuzz some primitives that
  the SPEC permits but make construction-time validity hard:
  Variable-direction single-leg rails (need template + last-leg-posted
  semantics — overhead vs payoff is poor for an in-process generator).
  Add them in a later substep if real-world PR shapes need it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def random_l2_yaml(seed: int) -> str:
    """Generate a deterministic random valid L2 YAML for the given seed."""
    rng = Random(seed)
    plan = _sample_plan(rng, seed)
    inst = _build_instance(rng, plan)
    return yaml.safe_dump(
        inst, sort_keys=False, default_flow_style=False, width=120,
    )


# ---------------------------------------------------------------------------
# The plan: per-seed-sampled counts + ratios + flags
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FuzzPlan:
    """Top-level variation knobs sampled once per seed."""

    seed: int
    n_singleton_internal: int          # internal singleton accounts (2-6)
    n_singleton_external: int          # external singleton accounts (1-3)
    n_templates: int                   # AccountTemplates (1-3)
    n_rails: int                       # Rails (3-8)
    n_transfer_templates: int          # TransferTemplates (0-2)
    n_chains: int                      # Chain entries (0-3)
    n_limit_schedules: int             # LimitSchedule entries (0-4)
    two_leg_ratio: float               # fraction of rails that are 2-leg (0.4-0.8)
    aggregating_count: int             # of n_rails, how many aggregating (0-2)
    pending_age_probability: float     # P(rail gets max_pending_age) (0.0-0.5)
    description_probability: float     # P(primitive gets a description) (0.5-1.0)


def _sample_plan(rng: Random, seed: int) -> _FuzzPlan:
    n_rails = rng.randint(3, 8)
    return _FuzzPlan(
        seed=seed,
        n_singleton_internal=rng.randint(2, 6),
        n_singleton_external=rng.randint(1, 3),
        n_templates=rng.randint(1, 3),
        n_rails=n_rails,
        n_transfer_templates=rng.randint(0, 2),
        n_chains=rng.randint(0, 3),
        n_limit_schedules=rng.randint(0, 4),
        two_leg_ratio=rng.uniform(0.4, 0.8),
        aggregating_count=rng.randint(0, min(2, max(0, n_rails - 2))),
        pending_age_probability=rng.uniform(0.0, 0.5),
        description_probability=rng.uniform(0.5, 1.0),
    )


# ---------------------------------------------------------------------------
# State threaded through builders
# ---------------------------------------------------------------------------


@dataclass
class _BuildState:
    """Identifier pools + flags accumulated as layers build.

    Each layer reads from the pools its predecessors populated and
    writes any new identifiers it declares. This is the data
    structure that makes cross-entity validity a structural property.
    """

    plan: _FuzzPlan
    # Singleton-account roles: parent-template candidates (R3).
    singleton_internal_roles: list[str] = field(default_factory=list)
    singleton_external_roles: list[str] = field(default_factory=list)
    # Template roles: child-leaf roles. Disjoint from singletons.
    template_roles: list[str] = field(default_factory=list)
    # Rails by name; we track which are aggregating + their leg
    # categorization for downstream constraint satisfaction.
    rail_names: list[str] = field(default_factory=list)
    # Z.B (2026-05-15): rail.transfer_type collapsed; rail names alone identify rails.
    aggregating_rail_names: set[str] = field(default_factory=set)
    non_aggregating_rail_names: list[str] = field(default_factory=list)
    single_leg_rail_names: list[str] = field(default_factory=list)
    two_leg_rail_names: list[str] = field(default_factory=list)
    # Rails that need reconciliation (S3): non-aggregating single-leg
    # rails not yet in any TransferTemplate.leg_rails AND not yet in
    # any aggregating rail's bundles_activity.
    needs_reconciliation: set[str] = field(default_factory=set)
    # Rails that have been added to some TransferTemplate.leg_rails
    # (S2: must NOT carry expected_net).
    template_leg_rail_names: set[str] = field(default_factory=set)
    # TransferTemplates: name + leg_rails for chain target sampling.
    transfer_template_names: list[str] = field(default_factory=list)
    # All declared role names (singleton ∪ template) for role-reference
    # sampling (R1, R6).
    all_role_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level instance builder
# ---------------------------------------------------------------------------


def _build_instance(rng: Random, plan: _FuzzPlan) -> dict[str, Any]:
    state = _BuildState(plan=plan)

    # Z.C (2026-05-15) — the legacy `instance:` YAML key is gone; the
    # DB-table prefix lives on cfg.db_table_prefix. The seed-derived
    # identity (when needed for triage) lives in the YAML *filename*
    # written by the caller, not inside the YAML body.

    accounts = _build_accounts(rng, state)
    account_templates = _build_account_templates(rng, state)
    rails = _build_rails(rng, state)

    # After rails exist we can finalize R8 (max_unbundled_age) — the
    # bundling layer below.
    _wire_bundles_activity(rng, state, rails)

    transfer_templates = _build_transfer_templates(rng, state, rails)

    # Now reconcile any leftover single-leg non-aggregating rails (S3):
    # add them to an aggregating rail's bundles_activity. If no
    # aggregating rail exists, fold them into a transfer_template.
    _ensure_single_leg_reconciliation(rng, state, rails, transfer_templates)

    # max_unbundled_age can only land on rails confirmed bundled (R8).
    _wire_max_unbundled_age(rng, state, rails)

    # S1/S2 finalization: walk two-leg rails and set expected_net only
    # on standalone rails (not template legs).
    _finalize_two_leg_expected_net(state, rails)

    chains = _build_chains(rng, state)
    limit_schedules = _build_limit_schedules(rng, state)
    role_business_day_offsets = _build_role_business_day_offsets(rng, state)

    out: dict[str, Any] = {
        "description": _maybe_description(rng, state, "fuzz instance"),
        "accounts": accounts,
        "account_templates": account_templates,
        "rails": rails,
    }
    if transfer_templates:
        out["transfer_templates"] = transfer_templates
    if chains:
        out["chains"] = chains
    if limit_schedules:
        out["limit_schedules"] = limit_schedules
    if role_business_day_offsets:
        out["role_business_day_offsets"] = role_business_day_offsets
    # Drop None descriptions for cleanliness.
    if out["description"] is None:
        del out["description"]
    return out


# Hour-of-day choices for per-role business-day offsets (M.4.4.14).
# Mix of midnight (0), early-morning, midday, evening, near-midnight to
# guarantee tests that depend on distinct (start, end) tuples per role
# see meaningful spread.
_BUSINESS_DAY_OFFSET_CHOICES = (0, 5, 9, 14, 17, 23)


def _build_role_business_day_offsets(
    rng: Random, state: _BuildState,
) -> dict[str, int]:
    """Pick a deterministic per-role business-day offset (M.4.4.14).

    Every declared role (singleton + template) gets one hour-offset from
    ``_BUSINESS_DAY_OFFSET_CHOICES``. Sample with replacement so multiple
    roles can share an offset, but the L2 instance as a whole carries
    enough variation to expose any future view that depends on per-role
    business-day boundaries differing.

    Returns ``{}`` when no roles exist (defensive — production paths
    always declare at least one). The dict is wrapped under
    ``role_business_day_offsets`` in the emitted YAML; the loader and
    seed honor it (M.4.4.14).
    """
    if not state.all_role_names:
        return {}
    return {
        role: rng.choice(_BUSINESS_DAY_OFFSET_CHOICES)
        for role in sorted(state.all_role_names)
    }


# ---------------------------------------------------------------------------
# Layer 1 — Accounts
# ---------------------------------------------------------------------------


def _build_accounts(rng: Random, state: _BuildState) -> list[dict[str, Any]]:
    """Singleton accounts + their roles."""
    accounts: list[dict[str, Any]] = []

    for i in range(state.plan.n_singleton_internal):
        role = f"InternalRole_{i:02d}"
        state.singleton_internal_roles.append(role)
        state.all_role_names.append(role)
        a: dict[str, Any] = {
            "id": f"int-acct-{i:03d}",
            "name": f"Internal Account {i:02d}",
            "role": role,
            "scope": "internal",
        }
        # Some accounts get an expected_eod_balance.
        if rng.random() < 0.3:
            a["expected_eod_balance"] = 0
        d = _maybe_description(rng, state, f"internal account {i}")
        if d is not None:
            a["description"] = d
        accounts.append(a)

    for i in range(state.plan.n_singleton_external):
        role = f"ExternalRole_{i:02d}"
        state.singleton_external_roles.append(role)
        state.all_role_names.append(role)
        a: dict[str, Any] = {
            "id": f"ext-acct-{i:03d}",
            "name": f"External Counterparty {i:02d}",
            "role": role,
            "scope": "external",
        }
        d = _maybe_description(rng, state, f"external account {i}")
        if d is not None:
            a["description"] = d
        accounts.append(a)

    return accounts


# ---------------------------------------------------------------------------
# Layer 2 — Account Templates
# ---------------------------------------------------------------------------


def _build_account_templates(
    rng: Random, state: _BuildState,
) -> list[dict[str, Any]]:
    """AccountTemplates with parent_role → singleton internal role (R3)."""
    templates: list[dict[str, Any]] = []
    if not state.singleton_internal_roles:
        return templates  # R3 — can't materialize templates without parents

    for i in range(state.plan.n_templates):
        role = f"TemplateRole_{i:02d}"
        state.template_roles.append(role)
        state.all_role_names.append(role)
        parent_role = rng.choice(state.singleton_internal_roles)
        t: dict[str, Any] = {
            "role": role,
            "scope": "internal",
            "parent_role": parent_role,
        }
        d = _maybe_description(rng, state, f"template {i}")
        if d is not None:
            t["description"] = d
        templates.append(t)
    return templates


# ---------------------------------------------------------------------------
# Layer 3 — Rails (the big one)
# ---------------------------------------------------------------------------

_ORIGINS = ("InternalInitiated", "ExternalForcePosted", "ExternalAggregated")
_METADATA_KEY_BANK = (
    "external_reference", "originator_id", "merchant_id",
    "settlement_period", "customer_segment", "batch_id",
    "card_brand", "memo_code", "business_day", "channel",
)
_CADENCE_VOCAB = (
    "intraday-2h", "intraday-4h", "intraday-6h",
    "daily-eod", "daily-bod",
    "weekly-mon", "weekly-fri",
    "monthly-eom", "monthly-bom", "monthly-15",
)


def _build_rails(rng: Random, state: _BuildState) -> list[dict[str, Any]]:
    rails: list[dict[str, Any]] = []
    if not state.all_role_names:
        return rails  # nothing to reference

    # Decide aggregating rails up-front so we can mark them while
    # building. Aggregating rails are always two-leg with internal
    # source AND internal destination (sweep-shaped); non-aggregating
    # mix freely.
    n = state.plan.n_rails
    agg_indices = set(rng.sample(range(n), state.plan.aggregating_count))

    for i in range(n):
        is_aggregating = i in agg_indices
        is_two_leg = (
            is_aggregating  # aggregating rails are always two-leg in this fuzzer
            or rng.random() < state.plan.two_leg_ratio
        )
        name = f"Rail_{i:02d}"
        state.rail_names.append(name)
        if is_aggregating:
            state.aggregating_rail_names.add(name)
        else:
            state.non_aggregating_rail_names.append(name)

        rail: dict[str, Any] = {
            "name": name,
        }

        # Metadata keys: random subset of the bank.
        n_meta = rng.randint(0, 3)
        if n_meta:
            rail["metadata_keys"] = sorted(
                rng.sample(_METADATA_KEY_BANK, n_meta),
            )

        if is_two_leg:
            state.two_leg_rail_names.append(name)
            _populate_two_leg_rail(rng, state, rail, is_aggregating)
        else:
            state.single_leg_rail_names.append(name)
            state.needs_reconciliation.add(name)
            _populate_single_leg_rail(rng, state, rail)

        # max_pending_age: per-rail probability.
        if rng.random() < state.plan.pending_age_probability:
            rail["max_pending_age"] = _random_iso_duration(rng, kind="pending")

        d = _maybe_description(rng, state, f"rail {i}")
        if d is not None:
            rail["description"] = d

        rails.append(rail)

    # Z.B (2026-05-15): U6 (per-leg discriminator uniqueness) is gone.
    # Rail.name uniqueness (U3) is enforced by the f"Rail_{i:02d}" pattern.
    return rails


def _populate_two_leg_rail(
    rng: Random,
    state: _BuildState,
    rail: dict[str, Any],
    is_aggregating: bool,
) -> None:
    """Set source_role + destination_role + origin pattern + aggregating fields."""
    if is_aggregating:
        # Aggregating: both legs internal — mirrors the SPEC's
        # PoolBalancingNorthToSouth shape.
        if len(state.singleton_internal_roles) >= 2:
            sample = rng.sample(state.singleton_internal_roles, 2)
            src, dst = sample[0], sample[1]
        else:
            # Degenerate: only one internal role. Reuse it on both
            # sides — still valid per R1.
            src = dst = state.singleton_internal_roles[0]
        rail["source_role"] = src
        rail["destination_role"] = dst
        rail["expected_net"] = 0
        rail["origin"] = rng.choice(_ORIGINS)
        rail["aggregating"] = True
        rail["cadence"] = rng.choice(_CADENCE_VOCAB)
        # bundles_activity gets wired in _wire_bundles_activity once
        # we know which non-aggregating rails exist; placeholder for now.
        rail["bundles_activity"] = []
    else:
        # Non-aggregating two-leg: source and destination from any
        # declared role (singleton or template).
        src = rng.choice(state.all_role_names)
        dst = rng.choice(state.all_role_names)
        rail["source_role"] = src
        rail["destination_role"] = dst
        # expected_net is set in _finalize_two_leg_expected_net (S1/S2
        # depend on whether this rail ends up as a template leg).

        # Origin pattern: 50% rail-level, 30% per-leg, 20% mixed.
        roll = rng.random()
        if roll < 0.5:
            rail["origin"] = rng.choice(_ORIGINS)
        elif roll < 0.8:
            rail["source_origin"] = rng.choice(_ORIGINS)
            rail["destination_origin"] = rng.choice(_ORIGINS)
        else:
            rail["origin"] = rng.choice(_ORIGINS)
            rail["destination_origin"] = rng.choice(_ORIGINS)


def _populate_single_leg_rail(
    rng: Random, state: _BuildState, rail: dict[str, Any],
) -> None:
    """Single-leg: leg_role + leg_direction + rail-level origin."""
    rail["leg_role"] = rng.choice(state.all_role_names)
    # Variable direction is intentionally NOT generated — see module
    # docstring. Stick to Debit/Credit which compose freely.
    rail["leg_direction"] = rng.choice(("Debit", "Credit"))
    rail["origin"] = rng.choice(_ORIGINS)


def _random_iso_duration(rng: Random, *, kind: str) -> str:
    """ISO 8601 duration in the loader-accepted vocabulary."""
    # Pending caps are typically short (hours / days).
    if kind == "pending":
        choices = ["PT4H", "PT12H", "PT24H", "P1D", "P2D", "P7D"]
    else:  # unbundled — tend to be longer
        choices = ["PT4H", "P1D", "P7D", "P14D", "P31D"]
    return rng.choice(choices)


# ---------------------------------------------------------------------------
# Layer 3.5 — Wire bundles_activity (R8 setup)
# ---------------------------------------------------------------------------


def _wire_bundles_activity(
    rng: Random, state: _BuildState, rails: list[dict[str, Any]],
) -> None:
    """Populate each aggregating rail's bundles_activity with bare-form
    selectors that resolve (R11) to declared rail names or transfer_types.

    The non-aggregating rails listed here become "bundled" — eligible
    to carry max_unbundled_age (R8).
    """
    if not state.non_aggregating_rail_names:
        return  # no non-aggregating rails → aggregating rails bundle nothing
    for rail in rails:
        if rail["name"] not in state.aggregating_rail_names:
            continue
        # Pick 1-3 non-aggregating rail names; mark them as needing
        # reconciliation-via-bundling.
        n = rng.randint(1, min(3, len(state.non_aggregating_rail_names)))
        bundled = sorted(rng.sample(state.non_aggregating_rail_names, n))
        rail["bundles_activity"] = bundled
        # Anything bundled here counts as reconciled (S3) for single-leg
        # rails AND becomes eligible for max_unbundled_age (R8).
        for b in bundled:
            state.needs_reconciliation.discard(b)


# ---------------------------------------------------------------------------
# Layer 4 — Transfer Templates
# ---------------------------------------------------------------------------


def _build_transfer_templates(
    rng: Random, state: _BuildState, rails: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """TransferTemplates with leg_rails sampled from non-aggregating rails."""
    templates: list[dict[str, Any]] = []
    eligible = state.non_aggregating_rail_names
    if not eligible:
        return templates  # R7 — no non-aggregating rails to use as legs

    for i in range(state.plan.n_transfer_templates):
        # Pick 1-3 leg rails from non-aggregating set.
        n_legs = rng.randint(1, min(3, len(eligible)))
        legs = sorted(rng.sample(eligible, n_legs))
        # Mark each leg as reconciled (S3) and as a template leg (S2).
        for leg in legs:
            state.needs_reconciliation.discard(leg)
            state.template_leg_rail_names.add(leg)

        name = f"TransferTemplate_{i:02d}"
        state.transfer_template_names.append(name)
        # transfer_key needs at least one non-empty identifier.
        n_keys = rng.randint(1, 2)
        keys = [f"key_{j}" for j in range(n_keys)]
        # Validator R12: every TransferKey field MUST appear in
        # metadata_keys of every leg_rail. The fuzzer picks legs from
        # the rail pool with random metadata_keys that don't intersect
        # the synthetic `key_<N>` template keys, so without this graft
        # the rails can't legitimately reach Posted (R12 fires at load).
        _extend_metadata_keys(rails, legs, keys)

        tt: dict[str, Any] = {
            "name": name,
            "expected_net": 0,
            "transfer_key": keys,
            "completion": _random_completion(rng),
            "leg_rails": legs,
        }
        d = _maybe_description(rng, state, f"transfer template {i}")
        if d is not None:
            tt["description"] = d
        templates.append(tt)
    return templates


def _random_completion(rng: Random) -> str:
    """V1 vocabulary: business_day_end, business_day_end+Nd, month_end,
    metadata.<key>."""
    options = [
        "business_day_end",
        f"business_day_end+{rng.randint(1, 7)}d",
        "month_end",
        f"metadata.{rng.choice(_METADATA_KEY_BANK)}",
    ]
    return rng.choice(options)


# ---------------------------------------------------------------------------
# S3 reconciliation — close any leftover single-leg rails
# ---------------------------------------------------------------------------


def _ensure_single_leg_reconciliation(
    rng: Random,
    state: _BuildState,
    rails: list[dict[str, Any]],
    transfer_templates: list[dict[str, Any]],
) -> None:
    """Any non-aggregating single-leg rail not yet reconciled (S3) gets
    added to an aggregating rail's bundles_activity OR fed into a
    transfer_template's leg_rails."""
    if not state.needs_reconciliation:
        return

    # First-pass: add to aggregating rails if any exist.
    aggs = [r for r in rails if r["name"] in state.aggregating_rail_names]
    if aggs:
        # Pick the first aggregating rail by sorted name for determinism.
        target = sorted(aggs, key=lambda r: r["name"])[0]
        existing = list(target.get("bundles_activity", []))
        for n in sorted(state.needs_reconciliation):
            if n not in existing:
                existing.append(n)
        target["bundles_activity"] = sorted(set(existing))
        state.needs_reconciliation.clear()
        return

    # Second-pass: no aggregating rail. Fold remaining unreconciled
    # rails into a transfer_template's leg_rails.
    if transfer_templates:
        target_tt = transfer_templates[0]
        existing = list(target_tt["leg_rails"])
        new_legs: list[str] = []
        for n in sorted(state.needs_reconciliation):
            if n not in existing:
                existing.append(n)
                new_legs.append(n)
                state.template_leg_rail_names.add(n)
        target_tt["leg_rails"] = sorted(set(existing))
        # R12: the template's existing transfer_key fields must appear
        # in the newly-folded legs' metadata_keys.
        _extend_metadata_keys(rails, new_legs, target_tt["transfer_key"])
        state.needs_reconciliation.clear()
        return

    # Third-pass: no aggregating rail AND no template. Synthesize a
    # minimal transfer_template to absorb the unreconciled rails.
    name = "TransferTemplate_FuzzReconcile"
    state.transfer_template_names.append(name)
    legs = sorted(state.needs_reconciliation)
    for n in legs:
        state.template_leg_rail_names.add(n)
    fallback_keys = ["fuzz_reconcile_key"]
    transfer_templates.append({
        "name": name,
        "expected_net": 0,
        "transfer_key": fallback_keys,
        "completion": "business_day_end",
        "leg_rails": legs,
    })
    # R12 graft for the synthetic template's leg_rails.
    _extend_metadata_keys(rails, legs, fallback_keys)
    state.needs_reconciliation.clear()


def _extend_metadata_keys(
    rails: list[dict[str, Any]],
    leg_names: list[str],
    transfer_key_fields: list[str],
) -> None:
    """Validator R12: extend each named rail's `metadata_keys` to include
    every TransferKey field of its containing template.

    Mutates the rail dicts in-place; idempotent (set-union dedupes).
    """
    for n in leg_names:
        for r in rails:
            if r["name"] != n:
                continue
            existing = list(r.get("metadata_keys", []))
            r["metadata_keys"] = sorted(
                set(existing) | set(transfer_key_fields)
            )
            break


# ---------------------------------------------------------------------------
# Layer 4.5 — max_unbundled_age (R8 finalization)
# ---------------------------------------------------------------------------


def _wire_max_unbundled_age(
    rng: Random, state: _BuildState, rails: list[dict[str, Any]],
) -> None:
    """Set max_unbundled_age only on rails confirmed to be in some
    aggregating rail's bundles_activity (R8)."""
    bundled_rail_names: set[str] = set()
    for r in rails:
        if r["name"] not in state.aggregating_rail_names:
            continue
        for sel in r.get("bundles_activity", []):
            bundled_rail_names.add(sel)
    for r in rails:
        if r["name"] not in bundled_rail_names:
            continue
        # Deterministic dice roll per rail.
        if rng.random() < 0.4:
            r["max_unbundled_age"] = _random_iso_duration(rng, kind="unbundled")


# ---------------------------------------------------------------------------
# S1/S2 finalization
# ---------------------------------------------------------------------------


def _finalize_two_leg_expected_net(
    state: _BuildState, rails: list[dict[str, Any]],
) -> None:
    """S1: two-leg standalone (not in any TransferTemplate.leg_rails)
    MUST have expected_net set. S2: two-leg template-leg MUST NOT have
    expected_net.
    """
    for r in rails:
        if r["name"] not in state.two_leg_rail_names:
            continue
        if r["name"] in state.aggregating_rail_names:
            continue  # aggregating two-leg already has expected_net=0
        if r["name"] in state.template_leg_rail_names:
            # S2: ensure NOT set.
            r.pop("expected_net", None)
        else:
            # S1: ensure set.
            r.setdefault("expected_net", 0)


# ---------------------------------------------------------------------------
# Layer 5 — Chains
# ---------------------------------------------------------------------------


def _build_chains(rng: Random, state: _BuildState) -> list[dict[str, Any]]:
    """Chain rows: each row is one parent + a children list (Z.A
    grammar collapse). Singleton-children rows encode "required";
    multi-children rows encode XOR alternation. Aggregating rails are
    excluded from the children pool (S4).

    The fuzzer mixes both shapes so the meta-guard surfaces both
    kinds across the seed pool — without that variety, downstream
    XOR-handling code goes untested.
    """
    chains: list[dict[str, Any]] = []
    valid_endpoints = state.rail_names + state.transfer_template_names
    valid_children = [
        n for n in valid_endpoints if n not in state.aggregating_rail_names
    ]
    if not valid_endpoints or not valid_children:
        return chains

    # n_chains rows total. Reserve up to 1 multi-children (XOR) row
    # when the children pool is wide enough. C6 forbids any child
    # appearing in two rows under the same parent — easiest way to
    # uphold this is "every parent gets at most one row this pass".
    want_xor = rng.randint(0, 1) == 1 and len(valid_children) >= 2
    used_parents: set[str] = set()

    # Singleton-children (required) rows.
    n_singleton = state.plan.n_chains
    if want_xor:
        n_singleton = max(0, n_singleton - 1)  # XOR row consumes one slot

    for _ in range(n_singleton):
        # Pick a fresh parent so we don't collide with later rows.
        candidates = [p for p in valid_endpoints if p not in used_parents]
        if not candidates:
            break
        parent = rng.choice(candidates)
        used_parents.add(parent)
        child = rng.choice(valid_children)
        chains.append({"parent": parent, "children": [child]})

    # Multi-children (XOR) row.
    if want_xor:
        candidates = [p for p in valid_endpoints if p not in used_parents]
        if candidates:
            parent = rng.choice(candidates)
            used_parents.add(parent)
            n_children = rng.randint(2, min(3, len(valid_children)))
            children = rng.sample(valid_children, n_children)
            chains.append({"parent": parent, "children": children})
    return chains


# ---------------------------------------------------------------------------
# Layer 6 — Limit Schedules
# ---------------------------------------------------------------------------


def _build_limit_schedules(
    rng: Random, state: _BuildState,
) -> list[dict[str, Any]]:
    """LimitSchedule entries — unique (parent_role, rail, direction)
    triples (U5) with rail sampled from declared Rail.name (R10).

    Z.B (2026-05-15): formerly sampled from rail_transfer_types; under
    the symmetric collapse the cap binds directly to a rail name.
    AB.1 (2026-05-19): each chosen entry gets a direction picked
    randomly — ~70% Outbound (default), ~30% Inbound. The fuzz matrix
    cells thus exercise both branches of the per-direction
    `<prefix>_limit_breach` matview UNION ALL across seeds. Default
    Outbound is omitted from the emitted YAML to keep pre-AB.1
    fuzz_failure fixtures byte-equivalent under the new serializer
    (which only emits ``direction:`` when non-default).
    """
    schedules: list[dict[str, Any]] = []
    if not state.all_role_names or not state.rail_names:
        return schedules

    # Build candidate pairs and sample without replacement (U5).
    # Per-pair uniqueness still holds — direction is a flavor knob
    # on the chosen pair, not a multiplier (so fuzz doesn't plant
    # both Outbound + Inbound on the same (parent, rail) in one
    # instance; that combination is exercised by hand-written unit
    # tests + AB.1.5.spec).
    candidate_pairs: list[tuple[str, str]] = []
    declared_rails = sorted(set(state.rail_names))
    for r in state.all_role_names:
        for rail in declared_rails:
            candidate_pairs.append((r, rail))
    if not candidate_pairs:
        return schedules

    n = min(state.plan.n_limit_schedules, len(candidate_pairs))
    chosen = rng.sample(candidate_pairs, n)
    for parent_role, rail in chosen:
        cap = rng.choice([1000, 5000, 10000, 50000, 100000])
        ls: dict[str, Any] = {
            "parent_role": parent_role,
            "rail": rail,
            "cap": cap,
        }
        # AB.1 direction draw — ~30% Inbound. Outbound is the default
        # so it's omitted (matches serializer's "non-default only" emit).
        if rng.random() < 0.30:
            ls["direction"] = "Inbound"
        d = _maybe_description(rng, state, "limit schedule")
        if d is not None:
            ls["description"] = d
        schedules.append(ls)
    return schedules


# ---------------------------------------------------------------------------
# Description helper
# ---------------------------------------------------------------------------


# Tiny prose bank — enough to surface the M.2a.7 description-driven prose
# seam without burning fuzz cycles on text generation.
_PROSE_FRAGMENTS = (
    "Generated by the M.2d.9 fuzzer.",
    "A randomly generated entity for contract-test exercising.",
    "Synthetic primitive — not a real-world counterparty.",
    "Auto-derived from the per-seed plan; safe to ignore in handbook prose.",
)


def _maybe_description(
    rng: Random, state: _BuildState, what: str,
) -> str | None:
    if rng.random() >= state.plan.description_probability:
        return None
    return f"{rng.choice(_PROSE_FRAGMENTS)} ({what})"
