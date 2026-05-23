"""AU.2 — multi-generator composition: drift + overdraft side by side.

The structural validation the user explicitly asked for: does the spine
SCALE past one invariant? AS proved one invariant (drift) end-to-end;
AU.1 promoted a second (overdraft). AU.2 composes them in one scenario
+ asserts every Invariant fires its own violations + the registry's
many-to-many edges hold under composition + the AU.0 parent-role
overdraft honest-limit closes (ledger_drift fires on a parent-role
overdraft plant).

What this test pins:

1. **Composition is `apply_scenario(*emitters)`.** AS.5's existing
   primitive accepts any `_Emitter` (`.emit(conn)`). Both DriftGenerator
   and OverdraftGenerator satisfy it without specialization. The PLAN's
   "in one `LedgerSimulation`" was imprecise — LedgerSimulation
   composes AccountSimulations (the AS.4 vector-state shape);
   apply_scenario composes anything with `.emit(conn)`. AU.2 uses the
   broader primitive.

2. **Leaf-overdraft + drift composition: THREE invariants fire.**
   - overdraft on the overdraft plant's leaf
   - drift on the drift plant's child AND the overdraft plant's leaf
     (the AU.0 empirical edge holds under composition)
   - ledger_drift on the drift plant's parent (with the overdraft
     plant's leaf shifting the Σ children → parent's ledger_drift
     magnitude DIFFERS from drift's own plant's expectation, which IS
     the composition story)

3. **Lone parent-overdraft variant: ONE invariant fires (NOT TWO).**
   - overdraft on the parent
   - ledger_drift does NOT fire (the AU.2 finding that corrected the
     AU.0 honest-limit prediction). Mechanism: ledger_drift's matview
     joins `_computed_ledger_balance`, which gates on `EXISTS (SELECT 1
     FROM ... child2 WHERE child2.account_parent_role = parent.account_
     role)`. A lone parent emission has no children in the DB → no
     computed_balance row → no ledger_drift candidate row. Production-
     honest: ledger_drift only makes sense when there ARE children to
     sum.
   - drift does NOT fire (parent has no parent_role; drift's matview
     filter excludes parent accounts).

4. **Composition-induced ledger_drift: drift+parent-overdraft.** Drift's
   plant ALSO emits a CustomerSubledger child; with that child in the
   DB, `_computed_ledger_balance`'s EXISTS gate is satisfied for the
   CustomerLedger role; ledger_drift then fires on BOTH parents — drift's
   own parent AND the overdraft parent. This is the **composition-
   induced edge** — overdraft → ledger_drift only manifests when another
   generator provides the prerequisite child rows.

5. **Registry stays per-generator (AU.1's two-edge entry is correct).**
   The composition-induced edge is a property of the SCENARIO, not the
   generator class. The registry records what a SINGLE-generator
   emission can trip; composition surfaces emergent edges. AU.5's
   exhaustiveness gate will need to consider both — but AU.2's
   conclusion is that the simple per-generator registry semantics hold.

6. **AU.0 honest-limit revised:** the original "parent-role overdraft
   trips ledger_drift" claim was WRONG. The corrected claim:
   "composition of drift + parent-role overdraft trips ledger_drift on
   BOTH parents." AU.2's tests pin both directions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    INVARIANT_GENERATOR_EDGES,
    DriftInvariant,
    LedgerDriftInvariant,
    OverdraftGenerator,
    OverdraftInvariant,
    apply_scenario,
    semantic_lock,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Composition A — drift + overdraft on a LEAF account
# ---------------------------------------------------------------------------


def test_drift_and_leaf_overdraft_compose_three_invariants_fire() -> None:
    """The headline AU.2 composition case. Drift plants a child+parent
    pair; overdraft plants a separate leaf account. THREE invariants
    fire — overdraft + drift (on TWO accounts: drift's child AND
    overdraft's leaf) + ledger_drift on drift's parent."""
    drift_gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    overdraft_gen = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=7.0,
    )
    invariants = [
        OverdraftInvariant(), DriftInvariant(), LedgerDriftInvariant(),
    ]

    conn = _fresh_db()
    try:
        apply_scenario(conn, drift_gen, overdraft_gen, prefix=_PREFIX)
        lock = semantic_lock(conn, invariants)
    finally:
        conn.close()

    # All three invariants fire — the spine scales past ONE invariant.
    assert lock["overdraft"], (
        f"overdraft should fire on the overdraft plant; lock={lock}"
    )
    assert lock["drift"], (
        f"drift should fire on the drift plant's child; lock={lock}"
    )
    assert lock["ledger_drift"], (
        f"ledger_drift should fire on the drift plant's parent; lock={lock}"
    )

    # Per-Invariant identity contracts — the carried set tracks BOTH
    # generators' contributions without one masking the other.
    overdraft_account_ids = {
        dict(v.identity).get("account_id") for v in lock["overdraft"]
    }
    assert overdraft_account_ids == {overdraft_gen.account_id}, (
        f"overdraft must fire on exactly the overdraft plant; "
        f"got {overdraft_account_ids}"
    )
    drift_account_ids = {
        dict(v.identity).get("account_id") for v in lock["drift"]
    }
    # Drift fires on BOTH the drift plant's child AND the overdraft
    # plant's leaf (the AU.0 empirical edge), so two account_ids.
    assert drift_account_ids == {
        drift_gen.child_account_id, overdraft_gen.account_id,
    }, (
        f"drift should fire on BOTH the drift child AND the overdraft "
        f"leaf (per the AU.0 empirical edge); got {drift_account_ids}"
    )


def test_composition_does_not_lose_drift_intended_violation() -> None:
    """The drift plant's intended Violation must survive composition —
    overdraft's row doesn't mask it (different account_id; the matview
    keeps them as distinct rows). Pins that composition doesn't break
    the per-generator intended contract."""
    drift_gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    overdraft_gen = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=7.0,
    )

    conn = _fresh_db()
    try:
        apply_scenario(conn, drift_gen, overdraft_gen, prefix=_PREFIX)
        detected_drift = DriftInvariant().detect(conn)
    finally:
        conn.close()
    assert drift_gen.intended in detected_drift


def test_composition_does_not_lose_overdraft_intended_violation() -> None:
    """Mirror: the overdraft plant's intended Violation survives drift's
    composition. The two generators' identities are isolated."""
    drift_gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    overdraft_gen = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=7.0,
    )

    conn = _fresh_db()
    try:
        apply_scenario(conn, drift_gen, overdraft_gen, prefix=_PREFIX)
        detected_overdraft = OverdraftInvariant().detect(conn)
    finally:
        conn.close()
    assert overdraft_gen.intended in detected_overdraft


# ---------------------------------------------------------------------------
# Composition B — lone parent-overdraft. The AU.0 honest-limit prediction
# REVISED by AU.2: a lone parent-overdraft does NOT trip ledger_drift,
# because the ledger_drift matview requires children to exist for the
# `_computed_ledger_balance` row to materialize.
# ---------------------------------------------------------------------------


def test_lone_parent_overdraft_trips_only_overdraft() -> None:
    """AU.2 finding: a lone parent-role overdraft plant trips ONLY
    overdraft — not ledger_drift, not drift.

    The mechanism: `_computed_ledger_balance` (which `_ledger_drift`
    joins) gates on
    ``EXISTS (SELECT 1 FROM ... child2 WHERE child2.account_parent_role
    = parent.account_role)``. A lone parent emission has no children →
    no computed_balance row → no ledger_drift candidate. Production-
    honest: ledger_drift only makes sense when there ARE children.

    This REVISES the AU.0 honest-limit prediction ("parent-overdraft
    trips ledger_drift") — that prediction was wrong; the corrected
    claim is "composition of drift + parent-overdraft trips ledger_drift
    on BOTH parents" (next test).
    """
    overdraft_gen = OverdraftInvariant().scenario_for(
        "CustomerLedger", magnitude=10.0,
    )
    assert overdraft_gen.account_parent_role is None, (
        "spec_example's CustomerLedger should be a parent (no parent_role)"
    )

    conn = _fresh_db()
    try:
        apply_scenario(conn, overdraft_gen, prefix=_PREFIX)
        overdraft_detected = OverdraftInvariant().detect(conn)
        drift_detected = DriftInvariant().detect(conn)
        ledger_drift_detected = LedgerDriftInvariant().detect(conn)
    finally:
        conn.close()

    # overdraft fires.
    assert overdraft_gen.intended in overdraft_detected

    # drift does NOT fire — parent has no parent_role; matview excludes.
    assert not any(
        dict(v.identity).get("account_id") == overdraft_gen.account_id
        for v in drift_detected
    )

    # ledger_drift does NOT fire — no children of CustomerLedger exist,
    # so `_computed_ledger_balance` has no row for this account.
    assert ledger_drift_detected == set(), (
        f"a lone parent-overdraft must NOT trip ledger_drift; the matview "
        f"requires children for `_computed_ledger_balance` to materialize. "
        f"Got {ledger_drift_detected}"
    )


# ---------------------------------------------------------------------------
# Composition C — drift + parent-overdraft: composition-induced ledger_drift
# fires on BOTH parents (drift's plant supplies the prerequisite child row).
# ---------------------------------------------------------------------------


def test_drift_plus_parent_overdraft_fires_ledger_drift_on_both_parents() -> None:
    """The composition-induced edge: ledger_drift fires on the overdraft
    parent ONLY when another generator (drift here) supplies the
    prerequisite child rows.

    Specifically: drift plants a CustomerSubledger child (stored=105)
    and a CustomerLedger parent (acct-drift-parent-CustomerLedger,
    stored=100). Overdraft plants a SEPARATE CustomerLedger account
    (acct-overdraft-CustomerLedger, stored=-10).

    `_computed_ledger_balance` is `(parent_role, business_day) → Σ child
    money`, joined to parents by `parent_role = account_role`. Both
    CustomerLedger parents share the SAME computed_balance = 105 (just
    drift's child). So:

    - drift's parent: stored=100, computed=105, ledger_drift = -5
    - overdraft's parent: stored=-10, computed=105, ledger_drift = -115

    BOTH parents fire ledger_drift. This is the AU.2 composition-induced
    edge — the property the registry's per-generator semantics doesn't
    capture, but the composition test pins.
    """
    drift_gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    overdraft_gen = OverdraftInvariant().scenario_for(
        "CustomerLedger", magnitude=10.0,
    )

    conn = _fresh_db()
    try:
        apply_scenario(conn, drift_gen, overdraft_gen, prefix=_PREFIX)
        ledger_drift_detected = LedgerDriftInvariant().detect(conn)
    finally:
        conn.close()

    fired_parent_ids = {
        dict(v.identity).get("account_id") for v in ledger_drift_detected
    }
    assert drift_gen.parent_account_id in fired_parent_ids, (
        f"ledger_drift should fire on drift's parent; "
        f"fired={fired_parent_ids}"
    )
    assert overdraft_gen.account_id in fired_parent_ids, (
        f"ledger_drift should fire on overdraft's parent (composition-"
        f"induced edge — drift's child supplies the prerequisite); "
        f"fired={fired_parent_ids}"
    )


# ---------------------------------------------------------------------------
# Registry semantics — per-generator-class, single-generator scenario.
# Empirical UNION across LEAF + PARENT variants of OverdraftGenerator alone
# matches AU.1's two-edge entry; composition-induced edges are NOT promoted
# to the registry (they're a property of scenarios, not classes).
# ---------------------------------------------------------------------------


def test_overdraft_generator_registry_edges_match_single_generator_empirical() -> None:
    """The AU.2 registry contract (refined): single-generator emissions
    of `OverdraftGenerator` across all valid instance variants trip
    EXACTLY the registered edge set. The composition-induced edge to
    `LedgerDriftInvariant` (from `test_drift_plus_parent_overdraft_fires
    _ledger_drift_on_both_parents`) is NOT promoted to the registry —
    it's a scenario property, not a generator-class property.

    Empirical edges across variants, single-generator:
    - leaf overdraft → {Overdraft, Drift} (AU.0 finding)
    - parent overdraft → {Overdraft} (AU.2 finding above)
    UNION = {Overdraft, Drift} = AU.1's registry entry. CHECK.
    """
    leaf_gen = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    parent_gen = OverdraftInvariant().scenario_for(
        "CustomerLedger", magnitude=10.0,
    )

    fired_union: set[type] = set()
    for gen in (leaf_gen, parent_gen):
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            for inv in (
                OverdraftInvariant(), DriftInvariant(), LedgerDriftInvariant(),
            ):
                hits = {
                    v for v in inv.detect(conn)
                    if dict(v.identity).get("account_id") == gen.account_id
                }
                if hits:
                    fired_union.add(type(inv))
        finally:
            conn.close()

    registered = set(INVARIANT_GENERATOR_EDGES[OverdraftGenerator])
    assert fired_union == registered, (
        f"OverdraftGenerator's single-generator empirical edges UNION "
        f"across (leaf, parent) must match the registry.\n"
        f"  empirical UNION: {sorted(c.__name__ for c in fired_union)}\n"
        f"  registered: {sorted(c.__name__ for c in registered)}"
    )
