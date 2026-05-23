"""Unit tests for the AS.5 semantic-lock mechanism.

The replacement for byte-identity locked seeds: lock against the
VIOLATION SET that detectors produce, not the SQL bytes that built it.
Implementation churn that preserves violations passes the semantic
lock; byte-locked breaks (over-strict). The lock guards INTENT, not
implementation.

What's pinned here:

1. `apply_scenario` + `semantic_lock` give a stable, frozen dict on
   re-runs of the same scenario.
2. Equality on the lock dict is the new gate (no SQL diff).
3. Different generator IMPLEMENTATIONS that produce the same
   intended violations land on the same lock — the flexibility claim.
4. Different intended violations land on DIFFERENT locks — the
   strictness claim (the gate has teeth).
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    AccountSimulation,
    DayPlan,
    DriftInvariant,
    LedgerDriftInvariant,
    LedgerSimulation,
    Perturbation,
    Violation,
    apply_scenario,
    semantic_lock,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"


def _fresh_db() -> sqlite3.Connection:
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
    return conn


_D0 = date(2030, 1, 1)
_D1 = _D0 + timedelta(days=1)
_D2 = _D0 + timedelta(days=2)


def _baseline_plans() -> list[DayPlan]:
    return [
        DayPlan(_D0, (100.0,)),
        DayPlan(_D1, (50.0, -30.0)),
        DayPlan(_D2, (20.0,)),
    ]


# ---------------------------------------------------------------------------
# Stability — two runs of the same scenario give the same lock.
# ---------------------------------------------------------------------------


def test_semantic_lock_is_stable_across_runs() -> None:
    # Two independent fresh DBs running the same scenario produce
    # equal locks. The byte-stable property that lets a lock dict be
    # frozen in a test or written to disk.
    inv = [DriftInvariant(), LedgerDriftInvariant()]

    def _run() -> dict[str, frozenset[Violation]]:
        sim = AccountSimulation(
            plans=_baseline_plans(),
            perturbations=[
                Perturbation(kind="state_blip", day_index=1, amount=7.0),
            ],
            account_id="acct-stable",
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, sim, prefix=_PREFIX)
            return semantic_lock(conn, inv)
        finally:
            conn.close()

    assert _run() == _run()


def test_semantic_lock_returns_per_invariant_sets() -> None:
    # The lock is keyed by invariant NAME (matching the matview suffix
    # the detector reads). Tests can assert per-invariant entries
    # without coupling to the invariant class identity.
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=0, amount=5.0),
        ],
        account_id="acct-shape",
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, sim, prefix=_PREFIX)
        lock = semantic_lock(conn, [DriftInvariant(), LedgerDriftInvariant()])
    finally:
        conn.close()
    assert set(lock.keys()) == {"drift", "ledger_drift"}
    assert isinstance(lock["drift"], frozenset)
    assert isinstance(lock["ledger_drift"], frozenset)
    # The drift set has the planted D0 violation; the ledger_drift set
    # is empty (no parent account in this single-account scenario).
    assert any(
        v.invariant == "drift" for v in lock["drift"]
    ), f"expected drift in lock; got {lock['drift']}"
    assert lock["ledger_drift"] == frozenset()


# ---------------------------------------------------------------------------
# Flexibility — different IMPLEMENTATIONS producing the same intended
# violations land on the SAME lock. Byte-locked SQL would diff;
# semantic lock matches. This is the "retirement" payoff.
# ---------------------------------------------------------------------------


def test_implementation_churn_preserving_violations_keeps_the_lock() -> None:
    # Two scenarios with DIFFERENT internal structure but the same
    # intended violations. Key: both must produce the same per-day
    # Σ legs ≤ day_end (the matview's `computed_balance`); opening
    # balances must match too since the matview doesn't account for
    # opening (it sums actual posted legs).
    #
    # sim_a: D0 leg (+100), D1 legs (+50, -30). D0 Σ legs = 100,
    #        D1 Σ legs cumulative = 120.
    # sim_b: D0 leg (+100), D1 leg (+20). D0 Σ legs = 100,
    #        D1 Σ legs cumulative = 120.
    # Both reach balance=120 at end of D1; both blip +5 → stored=125;
    # both produce drift=5 on D1 for the same account_id. Different
    # SQL bytes (different number of legs); same violation set.
    inv = [DriftInvariant()]

    sim_a = AccountSimulation(
        plans=[DayPlan(_D0, (100.0,)), DayPlan(_D1, (50.0, -30.0))],
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=5.0),
        ],
        account_id="acct-same",
    )
    sim_b = AccountSimulation(
        plans=[DayPlan(_D0, (100.0,)), DayPlan(_D1, (20.0,))],
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=5.0),
        ],
        account_id="acct-same",  # MUST be the same account_id —
        # the Violation identity carries account_id.
    )

    def _lock_for(sim: AccountSimulation) -> dict[str, frozenset[Violation]]:
        conn = _fresh_db()
        try:
            apply_scenario(conn, sim, prefix=_PREFIX)
            return semantic_lock(conn, inv)
        finally:
            conn.close()

    lock_a = _lock_for(sim_a)
    lock_b = _lock_for(sim_b)
    # Same intended drift (day=_D1, drift=5.0, account_id="acct-same")
    # → equal locks despite the different internal plan structure.
    assert lock_a == lock_b, (
        f"lock should be implementation-agnostic; got\n"
        f"  a: {lock_a}\n  b: {lock_b}"
    )


# ---------------------------------------------------------------------------
# Strictness — different intended violations land on DIFFERENT locks.
# Without this the gate would be vacuous.
# ---------------------------------------------------------------------------


def test_different_intended_violations_yield_different_locks() -> None:
    inv = [DriftInvariant()]

    sim_5 = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=5.0),
        ],
        account_id="acct-strict",
    )
    sim_7 = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=7.0),
        ],
        account_id="acct-strict",
    )

    def _lock_for(sim: AccountSimulation) -> dict[str, frozenset[Violation]]:
        conn = _fresh_db()
        try:
            apply_scenario(conn, sim, prefix=_PREFIX)
            return semantic_lock(conn, inv)
        finally:
            conn.close()

    assert _lock_for(sim_5) != _lock_for(sim_7), (
        "the lock must distinguish different intended violations — "
        "otherwise the gate has no teeth"
    )


# ---------------------------------------------------------------------------
# Composition — apply_scenario takes multiple emitters (varargs); the
# resulting lock reflects every emitter's contribution.
# ---------------------------------------------------------------------------


def test_apply_scenario_composes_multiple_emitters() -> None:
    # Two AccountSimulations on different accounts — emitted into one
    # connection in one apply_scenario call. The lock picks up
    # violations from both. This is the building block AU's
    # composition test (DriftGenerator + OverdraftGenerator) builds on.
    inv = [DriftInvariant()]
    sim_a = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=7.0),
        ],
        account_id="acct-compose-A",
    )
    sim_b = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=2, amount=9.0),
        ],
        account_id="acct-compose-B",
    )

    conn = _fresh_db()
    try:
        apply_scenario(conn, sim_a, sim_b, prefix=_PREFIX)
        lock = semantic_lock(conn, inv)
    finally:
        conn.close()

    # Both account_ids are in the lock; both magnitudes are present.
    account_ids = {
        dict(v.identity).get("account_id")
        for v in lock["drift"]
    }
    assert "acct-compose-A" in account_ids
    assert "acct-compose-B" in account_ids


def test_apply_scenario_works_with_ledger_simulation() -> None:
    # LedgerSimulation (AS.4) is also an `_Emitter` — has `.emit(conn)`
    # — so it composes with the semantic-lock pipeline.
    parent = AccountSimulation(
        plans=_baseline_plans(),
        account_id="acct-lock-parent",
        account_role="CustomerLedger",
        parent_role="",
        emit_legs=False,
    )
    child = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=7.0),
        ],
        account_id="acct-lock-child",
        account_role="CustomerSubledger",
        parent_role="CustomerLedger",
    )
    ledger = LedgerSimulation(accounts=[parent, child])

    inv = [DriftInvariant(), LedgerDriftInvariant()]
    conn = _fresh_db()
    try:
        apply_scenario(conn, ledger, prefix=_PREFIX)
        lock = semantic_lock(conn, inv)
    finally:
        conn.close()

    # Both invariants fire on the same day — drift on child, ledger_drift
    # on parent. The lock captures both per-invariant sets in one shot.
    assert lock["drift"]
    assert lock["ledger_drift"]
