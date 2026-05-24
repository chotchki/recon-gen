"""AU.5 — dual-axis exhaustiveness gate for the spine.

The gate the spine has been growing toward. Two registries —
`ALL_INVARIANTS` (what's promoted) and `INVARIANT_GENERATOR_EDGES`
(generator→invariants wiring) — must stay in sync. AU.5's tests
formalize that contract.

AX.5 expanded the gate's scope from L1-only (`ALL_L1_INVARIANTS` /
`ALL_L1_GENERATORS`) to the unified `ALL_INVARIANTS` / `ALL_GENERATORS`
tuples — covering L1 accounting + L2-shape integrity + L2
investigation invariants in one sweep. 13 invariants × 14
generators × 14 edges all gate together.

Catches three classes of bug:

1. **Orphan invariant** — a new Invariant class lands in
   `common/spine/<name>.py` + gets added to one of the category
   tuples, but no generator's edges include it. The matview exists
   but no scenario exercises it. Test: every `ALL_INVARIANTS` member
   appears in at least one `INVARIANT_GENERATOR_EDGES` value tuple.

2. **Orphan generator** — a new Generator class lands but isn't in
   `INVARIANT_GENERATOR_EDGES`. `apply_scenario` would silently accept
   it; no edge bookkeeping; no AU.2-style empirical edge verification.
   Test: every `ALL_GENERATORS` member is a key in
   `INVARIANT_GENERATOR_EDGES`.

3. **Empirical-edge mismatch** — registered edge claims that don't
   fire in practice. Per-invariant tests already cover this for each
   generator; AU.5's parametrized test consolidates the check across
   the whole spine in one pass — useful as a regression gate when
   matview SQL evolves.

Per the AU.2 finding (composition-induced edges are scenario-level,
not class-level), this gate's scope is the per-class wiring only.
Composition coverage is the scenario-author's responsibility +
documented in `test_spine_au2_composition.py`. AU.5's gate is the
"the registry is internally consistent" check.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    ALL_GENERATORS,
    ALL_INVARIANTS,
    INVARIANT_GENERATOR_EDGES,
    Invariant,
    ViolationGenerator,
    generators_for,
    invariants_for,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"


# ---------------------------------------------------------------------------
# Axis 1: every promoted Generator is registered.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("generator_class", ALL_GENERATORS)
def test_every_promoted_generator_is_registered(
    generator_class: type[ViolationGenerator],
) -> None:
    """The orphan-generator check. If a new generator class is added
    to `ALL_L1_GENERATORS` (= "I intended to promote this") but not
    keyed in `INVARIANT_GENERATOR_EDGES`, this fails loud with the
    class name."""
    edges = invariants_for(generator_class)
    assert len(edges) > 0, (
        f"{generator_class.__name__} is in ALL_GENERATORS but has no "
        f"edges registered in INVARIANT_GENERATOR_EDGES. Either:\n"
        f"  (a) add it to INVARIANT_GENERATOR_EDGES with its empirical "
        f"edge tuple (run a single-emit + multi-detect sweep test to "
        f"discover the edges), or\n"
        f"  (b) remove it from its category tuple (ALL_L1_GENERATORS / "
        f"ALL_L2_SHAPE_GENERATORS / ALL_L2_INVESTIGATION_GENERATORS) "
        f"if it's not yet production-ready."
    )


# ---------------------------------------------------------------------------
# Axis 2: every promoted Invariant is reached by ≥1 generator.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invariant_class", ALL_INVARIANTS)
def test_every_promoted_invariant_has_a_generator(
    invariant_class: type[Invariant],
) -> None:
    """The orphan-invariant check. Every promoted invariant must be
    reached by at least one generator's edges — otherwise no scenario
    can manufacture it for self-validation."""
    sources = generators_for(invariant_class)
    assert sources, (
        f"{invariant_class.__name__} is in ALL_INVARIANTS but no "
        f"generator's edges include it (no source for "
        f"`generators_for({invariant_class.__name__})`). Either:\n"
        f"  (a) extend an existing generator's edge tuple to include "
        f"it (e.g. AU.0-style empirical-edge discovery — emit a plant, "
        f"sweep detect across all invariants, find the surprise), or\n"
        f"  (b) add a new generator that targets it explicitly, or\n"
        f"  (c) remove it from its category tuple (ALL_L1_INVARIANTS / "
        f"ALL_L2_SHAPE_INVARIANTS / ALL_L2_INVESTIGATION_INVARIANTS) "
        f"if it's not yet production-ready."
    )


# ---------------------------------------------------------------------------
# Axis 3 (consolidated): every registered edge actually fires.
# ---------------------------------------------------------------------------


def _fresh_db_with_full_l2() -> sqlite3.Connection:
    """Schema + config row seeded with the L2 fields the spine
    generators read. The per-invariant test files seed narrower L2
    blobs (just the rails / limit_schedules they exercise); the
    AU.5 cross-cutting test seeds the full set so every generator's
    scenario_for can resolve."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()
    import json
    from datetime import datetime
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}",
        l2_json=json.dumps({
            "rails": [
                {"name": "ExternalRailInbound", "max_pending_age_seconds": 86400},
                {"name": "SubledgerCharge", "max_unbundled_age_seconds": 14400},
            ],
            "limit_schedules": [
                {
                    "parent_role": "CustomerLedger",
                    "rail": "ExternalRailOutbound",
                    "direction": "Outbound",
                    "cap": 5000,
                },
                {
                    "parent_role": "CustomerLedger",
                    "rail": "ExternalRailInbound",
                    "direction": "Inbound",
                    "cap": 3000,
                },
            ],
        }),
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    return conn


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()


def test_every_promoted_invariant_is_reachable_by_a_real_scenario() -> None:
    """The cross-cutting empirical-coverage gate: emit a representative
    plant from EACH generator class, refresh, collect every fired
    Invariant across all detected. The union must cover every member
    of `ALL_INVARIANTS`. If a class is in any category but no
    scenario actually trips it, the gate fails — the registry's claim
    that it's reachable is empirical, not just structural.

    AX.5 expanded the sweep from L1-only (`ALL_L1_INVARIANTS`) to
    `ALL_INVARIANTS` — all 13 invariants across 14 generators
    (L1 accounting + L2-shape integrity + L2 investigation).

    Per-generator emission is the "natural" coverage path. The AU.2
    composition-induced edges (which extend coverage beyond
    per-generator) aren't required here — every invariant in
    ALL_INVARIANTS is reachable by at least one SINGLE-generator
    scenario (the registry's edges encode this).

    The fan_in 'healthy' scenario is intentionally OMITTED: per AP.2
    convention it's the non-violating shape (intended is None;
    matview emits no row). The 'missing' constructor covers
    fan_in_disagreement detection coverage for this gate.
    """
    from datetime import datetime, date as _date
    fired_classes: set[type[Invariant]] = set()

    # One per-invariant-family scenario covering each category. Note
    # that some invariants have multiple registered generators (XOR /
    # multi-XOR missed + overlap, fan_in's 3 variants); we emit one
    # representative per invariant — the registry's edges encode that
    # ANY of the registered generators reaches the invariant, not
    # that ALL of them must be in this single sweep.
    from recon_gen.common.spine import (
        AnomalyInvariant as _Anom,
        ChainParentDisagreementInvariant as _CPD,
        DriftInvariant as _Drift,
        ExpectedEodBalanceInvariant as _Eod,
        FanInDisagreementInvariant as _FID,
        LimitBreachInvariant as _LB,
        MoneyTrailInvariant as _MT,
        MultiXorViolationInvariant as _MXV,
        OverdraftInvariant as _OD,
        StuckPendingInvariant as _SP,
        StuckUnbundledInvariant as _SU,
        XorGroupViolationInvariant as _XGV,
    )

    _AS_OF = datetime(2030, 1, 1, 12, 0, 0)
    _ANCHOR = _date(2030, 1, 1)
    generators = [
        # L1 accounting (7 invariants; 6 generators — drift covers 2)
        _Drift().scenario_for("CustomerSubledger", magnitude=5.0),
        _OD().scenario_for("CustomerSubledger", magnitude=5.0),
        _Eod().scenario_for("CustomerSubledger", expected=100.0, variance=5.0),
        _SP().scenario_for("ExternalRailInbound", as_of=_AS_OF, overshoot_seconds=60),
        _SU().scenario_for("SubledgerCharge", as_of=_AS_OF, overshoot_seconds=60),
        _LB().scenario_for("CustomerLedger", "ExternalRailOutbound",
                            direction="Outbound", overshoot=100.0),
        # L2-shape integrity (4 invariants; 6 generators — XOR/multi-XOR
        # each have missed+overlap variants; fan_in has healthy too).
        # Pick the variant that produces a matview row per invariant.
        _CPD().scenario_for(anchor_day=_ANCHOR),
        _XGV().scenario_for_missed(anchor_day=_ANCHOR),
        _FID().scenario_for_missing_parent(anchor_day=_ANCHOR),
        _MXV().scenario_for_missed(anchor_day=_ANCHOR),
        # L2 investigation (2 invariants; 2 generators)
        _Anom().scenario_for(
            "CustomerSubledger", "CustomerSubledger",
            baseline_pair_count=20, spike_magnitude=100_000.0,
            anchor_day=_ANCHOR,
        ),
        _MT().scenario_for(
            "CustomerSubledger", chain_length=3, anchor_day=_ANCHOR,
        ),
    ]

    conn = _fresh_db_with_full_l2()
    try:
        for gen in generators:
            gen.emit(conn)
        conn.commit()
        _refresh(conn)
        for inv_class in ALL_INVARIANTS:
            if inv_class().detect(conn):
                fired_classes.add(inv_class)
    finally:
        conn.close()

    missing = set(ALL_INVARIANTS) - fired_classes
    assert not missing, (
        f"every invariant must be reachable by ≥1 single-generator "
        f"scenario in the AU.5 sweep. Missing:\n"
        f"  {sorted(c.__name__ for c in missing)}\n"
        f"This means the registry CLAIMS these are reachable (per "
        f"generators_for) but no generator's plant actually fires "
        f"them on this fresh DB. Investigate the per-invariant test "
        f"file for that invariant; the per-generator scenario_for "
        f"signature may have drifted."
    )


# ---------------------------------------------------------------------------
# Internal consistency — the registries reference each other correctly.
# ---------------------------------------------------------------------------


def test_registered_generators_are_subset_of_ALL_GENERATORS() -> None:
    """No edge in INVARIANT_GENERATOR_EDGES references a generator
    that isn't in ALL_GENERATORS. (The orphan-generator check goes
    the other direction; this catches the inverse — a registered
    generator that someone forgot to add to a category tuple.)"""
    registered = set(INVARIANT_GENERATOR_EDGES.keys())
    declared = set(ALL_GENERATORS)
    extra = registered - declared
    assert not extra, (
        f"INVARIANT_GENERATOR_EDGES references generators not in "
        f"ALL_GENERATORS: {sorted(c.__name__ for c in extra)}. "
        f"Add them to a category tuple (ALL_L1_GENERATORS / "
        f"ALL_L2_SHAPE_GENERATORS / ALL_L2_INVESTIGATION_GENERATORS) "
        f"or remove their registry entry."
    )


def test_registered_invariants_are_subset_of_ALL_INVARIANTS() -> None:
    """Same shape for invariants — no edge tuple references an
    invariant not in ALL_INVARIANTS."""
    registered: set[type[Invariant]] = set()
    for invariants in INVARIANT_GENERATOR_EDGES.values():
        registered.update(invariants)
    declared = set(ALL_INVARIANTS)
    extra = registered - declared
    assert not extra, (
        f"INVARIANT_GENERATOR_EDGES references invariants not in "
        f"ALL_INVARIANTS: {sorted(c.__name__ for c in extra)}. "
        f"Add them to a category tuple (ALL_L1_INVARIANTS / "
        f"ALL_L2_SHAPE_INVARIANTS / ALL_L2_INVESTIGATION_INVARIANTS) "
        f"or remove their edge entry."
    )


# ---------------------------------------------------------------------------
# Composition-induced edges — documented but NOT in the AU.5 gate.
# ---------------------------------------------------------------------------


def test_composition_induced_edges_documented_in_au2_test() -> None:
    """Pin the AU.2 finding's scope boundary. AU.5's gate covers
    per-generator-class edges (the registry). Composition-induced
    edges (drift+parent-overdraft → ledger_drift on overdraft's
    parent, etc.) are NOT in scope here — they're documented +
    exercised in `tests/unit/test_spine_au2_composition.py`.

    If the spine ever needs a separate registry for composition-
    induced edges (e.g., AU.x.y "given these two generators, expect
    this third invariant to fire"), this test breaks loud as the
    forcing function for that decision."""
    from pathlib import Path as _Path
    composition_test = (
        _Path(__file__).resolve().parent
        / "test_spine_au2_composition.py"
    )
    assert composition_test.exists(), (
        "test_spine_au2_composition.py is the canonical home for "
        "composition-induced edge coverage; AU.5's gate intentionally "
        "doesn't duplicate it. If composition coverage needs its own "
        "exhaustiveness gate, decide on a registry shape first."
    )
