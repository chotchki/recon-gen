"""AX.6 — semantic_lock extends from L1 (AS.5) + L2-investigation
(AT.4) to L2-shape invariants (chain_parent_disagreement +
xor_group_violation + fan_in_disagreement + multi_xor_violation).

The AS.5 mechanism (`apply_scenario` + `semantic_lock`) is invariant-
agnostic: it composes anything with `.emit(conn)` and returns
`{invariant.name: frozenset(detected_violations)}`. AX.6 proves the
4 AX-promoted L2-shape generators slot into the same flow.

What's pinned (mirrors AT.4's shape for L2-shape):

1. **Stability** — repeated runs of the same scenario produce equal
   lock dicts (the byte-stable property that lets a lock be checked
   into the test).
2. **Per-invariant keying** — the lock dict's keys match the invariant
   `name` (matview suffix), not the class identity.
3. **Cross-class composition** — apply_scenario(chain_parent, xor)
   feeds BOTH detectors; each invariant's violations land in its own
   lock entry, neither masks the other.
4. **Gate has teeth** — different scenarios produce different locks.
5. **L2-shape generators compose with L1 generators** — chain_parent
   + drift in one scenario fires both classes without interference.
6. **Single-edge property preserved** — each L2-shape generator alone
   fires ONLY its own invariant (no drift/ledger_drift trip, since
   no balance rows; same shape as AT.4 confirmed for L2-investigation).

The 'healthy' variant of fan_in is intentionally OMITTED from the
per-generator stability tests: per AP.2 convention it produces no
violation (intended is None), so its lock entry is always the empty
frozenset — there's nothing to pin beyond what AX.3's unit test
already covers.
"""

from __future__ import annotations

from typing import Any

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    ChainParentDisagreementInvariant,
    DriftInvariant,
    FanInDisagreementInvariant,
    LedgerDriftInvariant,
    MultiXorViolationInvariant,
    XorGroupViolationInvariant,
    apply_scenario,
    semantic_lock,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE
_ANCHOR = date(2030, 1, 1)


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
# 1. Stability — semantic_lock is deterministic on the same scenario.
# ---------------------------------------------------------------------------


def test_chain_parent_disagreement_lock_stable_across_runs() -> None:
    def _run() -> dict[str, frozenset[Any]]:
        gen = ChainParentDisagreementInvariant().scenario_for(
            anchor_day=_ANCHOR,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(
                conn, [ChainParentDisagreementInvariant()],
            )
        finally:
            conn.close()

    assert _run() == _run()


def test_xor_group_missed_lock_stable_across_runs() -> None:
    def _run() -> dict[str, frozenset[Any]]:
        gen = XorGroupViolationInvariant().scenario_for_missed(
            anchor_day=_ANCHOR,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [XorGroupViolationInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


def test_xor_group_overlap_lock_stable_across_runs() -> None:
    def _run() -> dict[str, frozenset[Any]]:
        gen = XorGroupViolationInvariant().scenario_for_overlap(
            anchor_day=_ANCHOR,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [XorGroupViolationInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


def test_fan_in_missing_lock_stable_across_runs() -> None:
    def _run() -> dict[str, frozenset[Any]]:
        gen = FanInDisagreementInvariant().scenario_for_missing_parent(
            anchor_day=_ANCHOR,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [FanInDisagreementInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


def test_fan_in_extra_lock_stable_across_runs() -> None:
    def _run() -> dict[str, frozenset[Any]]:
        gen = FanInDisagreementInvariant().scenario_for_extra_parent(
            anchor_day=_ANCHOR,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [FanInDisagreementInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


def test_multi_xor_missed_lock_stable_across_runs() -> None:
    def _run() -> dict[str, frozenset[Any]]:
        gen = MultiXorViolationInvariant().scenario_for_missed(
            anchor_day=_ANCHOR,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [MultiXorViolationInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


def test_multi_xor_overlap_lock_stable_across_runs() -> None:
    def _run() -> dict[str, frozenset[Any]]:
        gen = MultiXorViolationInvariant().scenario_for_overlap(
            anchor_day=_ANCHOR,
        )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [MultiXorViolationInvariant()])
        finally:
            conn.close()

    assert _run() == _run()


# ---------------------------------------------------------------------------
# 2. Per-invariant keying.
# ---------------------------------------------------------------------------


def test_lock_keyed_by_l2_shape_invariant_names() -> None:
    """Lock dict keys match `Invariant.name` (the matview suffix), not
    class identity. Lets test code reference invariants by string."""
    gen = ChainParentDisagreementInvariant().scenario_for(
        anchor_day=_ANCHOR,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            ChainParentDisagreementInvariant(),
            XorGroupViolationInvariant(),
            FanInDisagreementInvariant(),
            MultiXorViolationInvariant(),
        ])
    finally:
        conn.close()
    assert set(lock.keys()) == {
        "chain_parent_disagreement",
        "xor_group_violation",
        "fan_in_disagreement",
        "multi_xor_violation",
    }


# ---------------------------------------------------------------------------
# 3. Cross-class composition — multiple AX generators in one scenario.
# ---------------------------------------------------------------------------


def test_chain_parent_and_fan_in_compose_both_fire() -> None:
    """The L2-shape cross-class composition case. apply_scenario(
    chain_parent, fan_in_missing) feeds BOTH detectors; each lands
    in its own lock entry. Neither masks the other."""
    chain_gen = ChainParentDisagreementInvariant().scenario_for(
        anchor_day=_ANCHOR,
    )
    fan_in_gen = FanInDisagreementInvariant().scenario_for_missing_parent(
        anchor_day=_ANCHOR,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, chain_gen, fan_in_gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            ChainParentDisagreementInvariant(),
            FanInDisagreementInvariant(),
        ])
    finally:
        conn.close()
    assert chain_gen.intended in lock["chain_parent_disagreement"]
    assert fan_in_gen.intended in lock["fan_in_disagreement"]


# ---------------------------------------------------------------------------
# 4. Gate has teeth — different scenarios produce different locks.
# ---------------------------------------------------------------------------


def test_xor_missed_and_overlap_produce_different_locks() -> None:
    """The missed + overlap variants land different Violations in the
    same lock dict key (`xor_group_violation`). Equality on the lock
    surfaces the difference."""
    def _lock_for(scenario_kind: str) -> frozenset[Any]:
        if scenario_kind == "missed":
            gen = XorGroupViolationInvariant().scenario_for_missed(
                anchor_day=_ANCHOR,
            )
        else:
            gen = XorGroupViolationInvariant().scenario_for_overlap(
                anchor_day=_ANCHOR,
            )
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            lock = semantic_lock(conn, [XorGroupViolationInvariant()])
        finally:
            conn.close()
        return lock["xor_group_violation"]

    assert _lock_for("missed") != _lock_for("overlap")


def test_fan_in_missing_and_extra_produce_different_locks() -> None:
    def _lock_for(scenario_kind: str) -> frozenset[Any]:
        inv = FanInDisagreementInvariant()
        if scenario_kind == "missing":
            gen = inv.scenario_for_missing_parent(anchor_day=_ANCHOR)
        else:
            gen = inv.scenario_for_extra_parent(anchor_day=_ANCHOR)
        conn = _fresh_db()
        try:
            apply_scenario(conn, gen, prefix=_PREFIX)
            return semantic_lock(conn, [inv])["fan_in_disagreement"]
        finally:
            conn.close()

    assert _lock_for("missing") != _lock_for("extra")


# ---------------------------------------------------------------------------
# 5. L2-shape composes with L1 — cross-layer composition.
# ---------------------------------------------------------------------------


def test_chain_parent_and_drift_compose_both_fire() -> None:
    """The cross-layer case: L2-shape (chain_parent) + L1 (drift).
    Substrate is invariant-agnostic; each lands in its own lock."""
    drift_gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    chain_gen = ChainParentDisagreementInvariant().scenario_for(
        anchor_day=_ANCHOR,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, drift_gen, chain_gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            DriftInvariant(),
            ChainParentDisagreementInvariant(),
        ])
    finally:
        conn.close()
    assert drift_gen.intended in lock["drift"]
    assert chain_gen.intended in lock["chain_parent_disagreement"]


# ---------------------------------------------------------------------------
# 6. Single-edge property — each L2-shape generator alone fires
# ONLY its own invariant (no drift/ledger_drift trip).
# ---------------------------------------------------------------------------


def test_chain_parent_alone_does_not_trip_drift() -> None:
    """transfers-only emit (no daily_balances rows) → no drift trip."""
    gen = ChainParentDisagreementInvariant().scenario_for(
        anchor_day=_ANCHOR,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            ChainParentDisagreementInvariant(),
            DriftInvariant(),
            LedgerDriftInvariant(),
        ])
    finally:
        conn.close()
    assert gen.intended in lock["chain_parent_disagreement"]
    assert lock["drift"] == frozenset()
    assert lock["ledger_drift"] == frozenset()


def test_multi_xor_overlap_alone_does_not_trip_drift() -> None:
    """Same single-edge property for multi-XOR overlap (the most
    leg-heavy AX plant — 1 parent + 2 children)."""
    gen = MultiXorViolationInvariant().scenario_for_overlap(
        anchor_day=_ANCHOR,
    )
    conn = _fresh_db()
    try:
        apply_scenario(conn, gen, prefix=_PREFIX)
        lock = semantic_lock(conn, [
            MultiXorViolationInvariant(),
            DriftInvariant(),
            LedgerDriftInvariant(),
        ])
    finally:
        conn.close()
    assert gen.intended in lock["multi_xor_violation"]
    assert lock["drift"] == frozenset()
    assert lock["ledger_drift"] == frozenset()
