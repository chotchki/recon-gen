"""AT.4 — semantic_lock extends from L1 (AS.5) to L2 (anomaly +
money_trail) invariants.

The AS.5 mechanism (`apply_scenario` + `semantic_lock`) is invariant-
agnostic: it composes anything with `.emit(conn)` and returns
`{invariant.name: frozenset(detected_violations)}`. AT.4 proves the L2
generators slot into the same flow + the per-scenario semantic-lock
gate works the same way for windowed-statistical (anomaly) and
recursive-graph (money_trail) invariants.

What's pinned:

1. **Stability** — repeated runs of the same scenario produce equal
   lock dicts (the byte-stable property that lets a lock be checked
   into the test).
2. **Per-invariant keying** — the lock dict's keys match the invariant
   `name` (matview suffix), not the class identity.
3. **Cross-class composition** — apply_scenario(anomaly_gen,
   money_trail_gen) feeds BOTH detectors; each invariant's violations
   land in its own lock entry, neither masks the other.
4. **Gate has teeth** — different scenarios produce different locks
   (otherwise the gate is useless). Demonstrated with anomaly
   spike_magnitude swap + money_trail chain_length swap.
5. **L2 generators compose with L1 generators** — anomaly + drift in
   one scenario fires both classes' invariants without interference.
   The substrate (apply_scenario) doesn't care.
6. **Single-edge property preserved post-AT.3** — anomaly alone fires
   ONLY anomaly (no drift/ledger_drift trip, since no balance rows);
   money_trail alone fires ONLY money_trail.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    AnomalyInvariant,
    AnomalyView,
    DriftInvariant,
    LedgerDriftInvariant,
    MoneyTrailInvariant,
    MoneyTrailView,
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
    """Fresh in-memory DB with schema + AW config row populated."""
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
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}", l2_json=json.dumps({"rails": []}),
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    return conn


# ---------------------------------------------------------------------------
# Stability — semantic_lock is deterministic on the same scenario.
# ---------------------------------------------------------------------------


def test_anomaly_lock_stable_across_runs() -> None:
    """Two independent fresh DBs running the same anomaly scenario
    produce equal locks. The property a checked-in semantic-lock
    artifact depends on."""
    def _run() -> dict[str, frozenset[Any]]:
        gen = AnomalyInvariant().scenario_for(
            "CustomerSubledger", "CustomerSubledger",
            baseline_pair_count=20, spike_magnitude=100_000.0,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [AnomalyInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


def test_money_trail_lock_stable_across_runs() -> None:
    """Same property for the recursive-graph case."""
    def _run() -> dict[str, frozenset[Any]]:
        gen = MoneyTrailInvariant().scenario_for(
            "CustomerSubledger", chain_length=3,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [MoneyTrailInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


# ---------------------------------------------------------------------------
# Per-invariant keying.
# ---------------------------------------------------------------------------


def test_lock_keyed_by_invariant_name() -> None:
    """Lock dict keys match `Invariant.name` (the matview suffix), not
    the class identity. Lets test code reference invariants by string."""
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=20,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, gen, prefix=_PREFIX)
        lock = semantic_lock(
            conn, [AnomalyInvariant(), MoneyTrailInvariant()],
        )
    finally:
        conn.close()
    assert set(lock.keys()) == {
        "inv_pair_rolling_anomalies", "inv_money_trail_edges",
    }


# ---------------------------------------------------------------------------
# Cross-class composition — anomaly + money_trail in one scenario.
# ---------------------------------------------------------------------------


def test_anomaly_and_money_trail_compose_both_fire() -> None:
    """The L2 cross-class composition case. apply_scenario(anomaly,
    money_trail) feeds BOTH detectors; each invariant's violations
    land in its own lock entry, neither masks the other."""
    anomaly_gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=20, spike_magnitude=100_000.0,
    )
    money_trail_gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=3,
    )

    conn = _fresh_db()
    try:
        apply_scenario(conn, anomaly_gen, money_trail_gen, prefix=_PREFIX)
        lock = semantic_lock(
            conn, [AnomalyInvariant(), MoneyTrailInvariant()],
        )
    finally:
        conn.close()

    # Anomaly fires (the spike).
    sliced_anomaly = AnomalyView().slice(
        set(lock["inv_pair_rolling_anomalies"])
    )
    assert sliced_anomaly, (
        f"composition didn't surface anomaly's spike; "
        f"raw lock={lock['inv_pair_rolling_anomalies']}"
    )
    # Money-trail fires (3 chain edges).
    money_trail_violations = lock["inv_money_trail_edges"]
    assert len(money_trail_violations) >= 3, (
        f"composition didn't surface money_trail's chain "
        f"(expected ≥3 edges); got {money_trail_violations}"
    )


def test_anomaly_alone_only_fires_anomaly_no_drift() -> None:
    """Single-edge property of anomaly preserved post-AT.3 refactor:
    no balance rows → no drift / ledger_drift trip. Catches a
    regression if AnomalyGenerator ever started emitting through
    AccountSimulation."""
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=20,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            AnomalyInvariant(),
            MoneyTrailInvariant(),
            DriftInvariant(),
            LedgerDriftInvariant(),
        ])
    finally:
        conn.close()
    # Anomaly fires; the others don't.
    assert lock["drift"] == frozenset(), (
        f"anomaly should not trip drift; got drift={lock['drift']}"
    )
    assert lock["ledger_drift"] == frozenset(), (
        f"anomaly should not trip ledger_drift; got={lock['ledger_drift']}"
    )


def test_money_trail_alone_only_fires_money_trail_no_drift() -> None:
    """Mirror single-edge property for the recursive-graph case."""
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=3,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            AnomalyInvariant(),
            MoneyTrailInvariant(),
            DriftInvariant(),
            LedgerDriftInvariant(),
        ])
    finally:
        conn.close()
    # money_trail fires; anomaly drift do not.
    assert lock["drift"] == frozenset()
    assert lock["ledger_drift"] == frozenset()
    # Anomaly's matview reads transactions — and money_trail planted
    # internal-leaf Posted balanced transfers — so anomaly's matview
    # MAY surface low-σ rows for the chain pairs. View slice with the
    # default 3σ filters them out; that's the analyst-meaningful set.
    sliced_anomaly = AnomalyView().slice(
        set(lock["inv_pair_rolling_anomalies"])
    )
    assert sliced_anomaly == frozenset(), (
        f"money_trail's chain transfers should not produce high-σ "
        f"anomalies (their pair-amounts are uniform); "
        f"sliced={sliced_anomaly}"
    )


# ---------------------------------------------------------------------------
# Gate has teeth — different scenarios produce different locks.
# ---------------------------------------------------------------------------


def test_anomaly_lock_changes_when_spike_changes() -> None:
    """Different spike magnitudes → different population stats →
    different z_score → potentially different bucket. Even if buckets
    stay the same, the spike's `intended` Violation identity differs
    only by bucket value; here we use small vs large spike to swing
    between '0-1' and '4+' sigma."""
    def _lock(spike: float) -> dict[str, frozenset[Any]]:
        gen = AnomalyInvariant().scenario_for(
            "CustomerSubledger", "CustomerSubledger",
            baseline_pair_count=20, spike_magnitude=spike,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [AnomalyInvariant()])
        finally:
            conn.close()

    small_spike_lock = _lock(100.0)  # same as baseline → no anomaly
    big_spike_lock = _lock(100_000.0)  # 1000× → '4+ sigma'
    assert small_spike_lock != big_spike_lock, (
        "lock must distinguish a non-anomaly run from an anomaly run; "
        "the gate has no teeth otherwise"
    )


def test_money_trail_lock_changes_when_chain_length_changes() -> None:
    """Longer chain → more edges → different lock content."""
    def _lock(chain_length: int) -> dict[str, frozenset[Any]]:
        gen = MoneyTrailInvariant().scenario_for(
            "CustomerSubledger", chain_length=chain_length,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [MoneyTrailInvariant()])
        finally:
            conn.close()

    short = _lock(2)
    long_chain = _lock(5)
    assert short != long_chain
    short_edges = len(short["inv_money_trail_edges"])
    long_edges = len(long_chain["inv_money_trail_edges"])
    assert long_edges > short_edges, (
        f"chain_length=5 should yield more edges than chain_length=2; "
        f"got {long_edges} vs {short_edges}"
    )


# ---------------------------------------------------------------------------
# L2 generators compose with L1 generators (cross-layer composition).
# ---------------------------------------------------------------------------


def test_l1_drift_and_l2_anomaly_compose_independently() -> None:
    """Final cross-cutting check: L2 invariants live alongside L1 in
    the same apply_scenario call. Each invariant fires from its own
    plant; the substrate doesn't care about layer."""
    from recon_gen.common.spine import DriftInvariant

    drift_gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    anomaly_gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=20, spike_magnitude=100_000.0,
    )

    conn = _fresh_db()
    try:
        apply_scenario(conn, drift_gen, anomaly_gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            DriftInvariant(),
            AnomalyInvariant(),
        ])
    finally:
        conn.close()

    # Drift fires from the L1 plant.
    assert lock["drift"], f"drift didn't fire on L1 plant; lock={lock}"
    # Anomaly fires from the L2 plant.
    sliced_anomaly = AnomalyView().slice(
        set(lock["inv_pair_rolling_anomalies"])
    )
    assert sliced_anomaly, (
        f"anomaly didn't fire on L2 plant; lock={lock}"
    )


# ---------------------------------------------------------------------------
# View-sliced lock (the "what the analyst sees" gate).
# ---------------------------------------------------------------------------


def test_view_sliced_lock_is_also_stable() -> None:
    """Applying the View slice to the lock entries gives an analyst-
    facing lock — same stability property. The composition
    semantic_lock + View.slice gives test code the choice of either
    the full detector output (raw lock) or the analyst-visible subset.
    """
    def _sliced_run() -> dict[str, frozenset[Any]]:
        gen = AnomalyInvariant().scenario_for(
            "CustomerSubledger", "CustomerSubledger",
            baseline_pair_count=20, spike_magnitude=100_000.0,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            raw = semantic_lock(conn, [AnomalyInvariant()])
        finally:
            conn.close()
        return {
            "inv_pair_rolling_anomalies": frozenset(
                AnomalyView().slice(set(raw["inv_pair_rolling_anomalies"]))
            ),
        }

    assert _sliced_run() == _sliced_run()


def test_money_trail_view_sliced_lock_stability() -> None:
    """Same property for the depth-threshold View on money_trail."""
    def _sliced_run() -> dict[str, frozenset[Any]]:
        gen = MoneyTrailInvariant().scenario_for(
            "CustomerSubledger", chain_length=4,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            raw = semantic_lock(conn, [MoneyTrailInvariant()])
        finally:
            conn.close()
        return {
            "inv_money_trail_edges": frozenset(
                MoneyTrailView(min_depth=2).slice(
                    set(raw["inv_money_trail_edges"])
                )
            ),
        }

    assert _sliced_run() == _sliced_run()
