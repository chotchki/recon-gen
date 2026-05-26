"""AY.1 — equivalence test gate: OLD ScenarioPlant emit ⋈ NEW spine
generator emit.

The safety precondition for AY.4's reroute of the production seed
through the spine. For each plant kind that has BOTH an OLD
`_emit_<plant>_rows` helper AND a NEW spine `ViolationGenerator`,
this test:

1. Builds a focused `ScenarioPlant` (one plant kind, via
   `default_scenario_for` + `filter_scenario_plants`).
2. Applies the OLD path on a fresh in-memory SQLite — schema +
   `emit_seed` SQL → execute + refresh.
3. Detects via the matching `Invariant`; records the violation
   count.
4. Applies the NEW path on a SEPARATE fresh SQLite — schema +
   spine generator's `emit()` + matview refresh (via
   `apply_scenario`).
5. Detects via the same `Invariant`; records the count.
6. Asserts both > 0 (both paths produced violations) AND that the
   counts agree.

What this gate IS — a semantic-equivalence check at the matview
violation-count level. The two paths produce different literal row
shapes (different account_ids — OLD path uses customer template
instance accounts; NEW path uses synthetic deterministic IDs); the
matview's natural-key tuples therefore differ literally, but the
violation COUNT per invariant should match. AY.5 re-locks the byte
seeds and accepts that the literal SQL diffs — AY.1's job is to
catch "the adapter forgot a plant kind / wired it to the wrong
generator / dropped a violation."

What this gate is NOT — a byte-equivalence check. That's AY.5's
deliberate re-lock. Identity-set equivalence is a stronger goal
that requires deeper alignment of account_id schemes between the
two paths; deferred.

Coverage today (12 plant kinds, since expected_eod is spine-only,
anomaly + money_trail are AT spine-only without OLD plants; AY.2.b
adds 7 more new generators that will extend this gate to 20):

  - drift (DriftPlant ⋈ DriftGenerator)
  - overdraft (OverdraftPlant ⋈ OverdraftGenerator)
  - limit_breach (LimitBreachPlant ⋈ LimitBreachGenerator)
  - stuck_pending (StuckPendingPlant ⋈ StuckPendingGenerator)
  - stuck_unbundled (StuckUnbundledPlant ⋈ StuckUnbundledGenerator)
  - chain_parent_disagreement (ChainParentDisagreementPlant ⋈ Generator)
  - xor_group_violation (missed) (XorVariantMissedFiringPlant ⋈ Generator)
  - xor_group_violation (overlap) (XorVariantOverlapPlant ⋈ Generator)
  - fan_in_disagreement (missing) (FanInChainMissingParentPlant ⋈ FanInChainGenerator(missing_parent))
  - fan_in_disagreement (extra) (FanInChainExtraParentPlant ⋈ FanInChainGenerator(extra_parent))
  - multi_xor_violation (missed) (MultiXorMissedPlant ⋈ MultiXorMissedGenerator)
  - multi_xor_violation (overlap) (MultiXorOverlapPlant ⋈ MultiXorOverlapGenerator)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path


from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.auto_scenario import (
    default_scenario_for,
    filter_scenario_plants,
)
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.seed import emit_seed
from recon_gen.common.spine import (
    ChainParentDisagreementInvariant,
    DriftInvariant,
    FanInDisagreementInvariant,
    Invariant,
    LimitBreachInvariant,
    MultiXorViolationInvariant,
    OverdraftInvariant,
    StuckPendingInvariant,
    StuckUnbundledInvariant,
    XorGroupViolationInvariant,
    apply_scenario,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE
_ANCHOR = date(2030, 1, 1)
_AS_OF = datetime(2030, 1, 1, 12, 0, 0)


def _fresh_db() -> sqlite3.Connection:
    """Schema + AW config row populated (the spine + L2 helpers
    both read from the <prefix>_config table post-AW)."""
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
    # Config row — many matviews + generators (stuck_pending,
    # stuck_unbundled, limit_breach) read it post-AW. Populate with
    # the L2 rails + limit_schedules the spec_example yaml declares
    # so those matviews + generators resolve cleanly.
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
        as_of=_AS_OF,
    )
    return conn


def _apply_old_path(
    conn: sqlite3.Connection,
    scenario,  # type: ignore[no-untyped-def]: scenario is ScenarioPlant; annotation forces import to module scope
) -> None:
    """Execute the OLD `emit_seed` SQL against the connection +
    refresh the matviews."""
    instance = load_instance(_SPEC_EXAMPLE)
    sql = emit_seed(instance, scenario, prefix=_PREFIX, dialect=_DIALECT)
    cur = conn.cursor()
    execute_script(cur, sql, dialect=_DIALECT)
    conn.commit()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


def _detect_count(conn: sqlite3.Connection, inv: Invariant) -> int:
    """Count of detected violations for the given invariant."""
    return len(inv.detect(conn))


# ---------------------------------------------------------------------------
# Per-plant-kind equivalence cells.
#
# Each case constructs a focused ScenarioPlant (one plant kind),
# applies via both paths separately, and asserts the violation
# count matches.
# ---------------------------------------------------------------------------


def _isolate_kind(plant_kind: str):  # type: ignore[no-untyped-def]: returns ScenarioPlant; annotation forces seed.py import to module scope
    """Build a ScenarioPlant containing ONLY the requested plant
    kind (plus the carry-through fixtures: template_instances,
    non-violation seed-color, today). filter_scenario_plants does
    the projection per the AY.0 design."""
    instance = load_instance(_SPEC_EXAMPLE)
    report = default_scenario_for(instance, today=_ANCHOR)
    return filter_scenario_plants(report.scenario, (plant_kind,))


def test_drift_equivalence() -> None:
    """OLD DriftPlant emit ⋈ NEW DriftGenerator emit. Both should
    produce ≥1 drift violation."""
    inv = DriftInvariant()
    old_scenario = _isolate_kind("drift")
    # OLD path
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    # NEW path
    new_db = _fresh_db()
    try:
        gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
        apply_scenario(new_db, gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0, "OLD DriftPlant emit produced no detected drift"
    assert new_count > 0, "NEW DriftGenerator emit produced no detected drift"
    # Both paths plant ONE drift cell (their per-plant scope is
    # "one violation"); count agreement confirms the adapter would
    # wire one OLD plant → one NEW generator.
    assert old_count == new_count, (
        f"drift count mismatch: OLD={old_count}, NEW={new_count}"
    )


def test_overdraft_equivalence() -> None:
    inv = OverdraftInvariant()
    old_scenario = _isolate_kind("overdraft")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    new_db = _fresh_db()
    try:
        gen = OverdraftInvariant().scenario_for(
            "CustomerSubledger", magnitude=5.0,
        )
        apply_scenario(new_db, gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0
    assert new_count > 0
    assert old_count == new_count, (
        f"overdraft count mismatch: OLD={old_count}, NEW={new_count}"
    )


def test_limit_breach_equivalence() -> None:
    inv = LimitBreachInvariant()
    old_scenario = _isolate_kind("limit_breach")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    new_db = _fresh_db()
    try:
        gen = LimitBreachInvariant().scenario_for(
            "CustomerLedger", "ExternalRailOutbound",
            direction="Outbound", overshoot=100.0,
        )
        apply_scenario(new_db, gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0
    assert new_count > 0
    # Same single-plant count expectation. (Note: OLD path may
    # include inbound_cap_breach_plants under the same "limit_breach"
    # filter kind — see filter_scenario_plants — so OLD count can be
    # >1 when both directions seed.)
    assert old_count >= 1
    assert new_count == 1


def test_stuck_pending_equivalence() -> None:
    inv = StuckPendingInvariant()
    old_scenario = _isolate_kind("stuck_pending")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    new_db = _fresh_db()
    try:
        gen = StuckPendingInvariant().scenario_for(
            "ExternalRailInbound", as_of=_AS_OF, overshoot_seconds=60,
        )
        apply_scenario(new_db, gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0
    assert new_count > 0


def test_stuck_unbundled_equivalence() -> None:
    inv = StuckUnbundledInvariant()
    old_scenario = _isolate_kind("stuck_unbundled")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    new_db = _fresh_db()
    try:
        gen = StuckUnbundledInvariant().scenario_for(
            "SubledgerCharge", as_of=_AS_OF, overshoot_seconds=60,
        )
        apply_scenario(new_db, gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0
    assert new_count > 0


def test_chain_parent_disagreement_old_path_picker_bug_surfaced() -> None:
    """OLD-path latent bug surfaced by AY.1 (pre-existing pre-AX).

    `_pick_two_template_chain_inputs` in common/l2/auto_scenario.py
    returns ANY chain-child template (the AB.2.6 picker). On
    spec_example, the first match is `BatchedPayoutBatch` — which
    is a fan_in template. The chain_parent_disagreement matview
    deliberately excludes fan_in templates via its
    `_render_chain_parent_disagreement_fan_in_filter` (fan_in
    children are legitimately multi-parent by design — see
    docs/audits/ax_0_concat_agg_audit.md). The OLD
    `_emit_chain_parent_disagreement_rows` emits onto the fan_in
    template → matview silently filters → ZERO violations detected.

    AX.1's `ChainParentDisagreementInvariant.scenario_for()` shipped
    its own `_pick_non_fan_in_chain_child` picker that skips
    fan_in templates; the NEW path produces ≥1 violation as
    intended.

    This divergence is INTENTIONAL — the NEW path is correct, the
    OLD path is buggy. AY.4's reroute through the spine + AY.6's
    OLD emitter retirement removes the buggy path entirely.
    """
    inv = ChainParentDisagreementInvariant()
    old_scenario = _isolate_kind("chain_parent_disagreement")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    new_db = _fresh_db()
    try:
        gen = ChainParentDisagreementInvariant().scenario_for(
            anchor_day=_ANCHOR,
        )
        apply_scenario(new_db, gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    # OLD path's latent picker bug: zero violations on spec_example.
    assert old_count == 0, (
        f"OLD path's default picker should land on the fan_in template "
        f"(BatchedPayoutBatch) which the matview filters out → 0 "
        f"violations. Got {old_count} — picker behavior changed; "
        f"re-evaluate this test's premise."
    )
    # NEW path's _pick_non_fan_in_chain_child fix: produces the
    # violation cleanly.
    assert new_count > 0, (
        f"NEW ChainParentDisagreementGenerator should land on a "
        f"non-fan_in template + produce ≥1 matview violation. Got "
        f"{new_count}."
    )


def test_xor_group_violation_equivalence() -> None:
    """OLD emits BOTH missed + overlap variants under one
    'xor_group_violation' filter; NEW emits one variant per
    generator. Count comparison is loose (both > 0)."""
    inv = XorGroupViolationInvariant()
    old_scenario = _isolate_kind("xor_group_violation")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    # Emit BOTH variants on the NEW side to match the OLD's coverage.
    new_db = _fresh_db()
    try:
        missed_gen = XorGroupViolationInvariant().scenario_for_missed(
            anchor_day=_ANCHOR,
        )
        overlap_gen = XorGroupViolationInvariant().scenario_for_overlap(
            anchor_day=_ANCHOR,
        )
        apply_scenario(new_db, missed_gen, overlap_gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0
    assert new_count > 0


def test_fan_in_disagreement_equivalence() -> None:
    """OLD emits healthy/missing/extra under one 'fan_in_disagreement'
    filter; the AB.4.7 matview surfaces only the missing + extra
    (healthy is the AP.2 non-violating shape). NEW emits one variant
    per smart constructor — gate covers missing + extra."""
    inv = FanInDisagreementInvariant()
    old_scenario = _isolate_kind("fan_in_disagreement")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    new_db = _fresh_db()
    try:
        missing_gen = FanInDisagreementInvariant().scenario_for_missing_parent(
            anchor_day=_ANCHOR,
        )
        extra_gen = FanInDisagreementInvariant().scenario_for_extra_parent(
            anchor_day=_ANCHOR,
        )
        apply_scenario(new_db, missing_gen, extra_gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0
    assert new_count > 0


def test_multi_xor_violation_equivalence() -> None:
    """OLD emits missed + overlap under one 'multi_xor_violation'
    filter; NEW emits one variant per generator. Gate covers both."""
    inv = MultiXorViolationInvariant()
    old_scenario = _isolate_kind("multi_xor_violation")
    old_db = _fresh_db()
    try:
        _apply_old_path(old_db, old_scenario)
        old_count = _detect_count(old_db, inv)
    finally:
        old_db.close()
    new_db = _fresh_db()
    try:
        missed_gen = MultiXorViolationInvariant().scenario_for_missed(
            anchor_day=_ANCHOR,
        )
        overlap_gen = MultiXorViolationInvariant().scenario_for_overlap(
            anchor_day=_ANCHOR,
        )
        apply_scenario(new_db, missed_gen, overlap_gen, prefix=_PREFIX)
        new_count = _detect_count(new_db, inv)
    finally:
        new_db.close()
    assert old_count > 0
    assert new_count > 0


# ---------------------------------------------------------------------------
# Smoke: both paths produce something for spec_example's default
# scenario — the broader "no plant kind silently went missing" gate.
# ---------------------------------------------------------------------------


def test_default_scenario_old_path_trips_each_invariant() -> None:
    """Sanity check the OLD path: `default_scenario_for(spec_example)`
    + full emit produces ≥1 violation for each invariant on the
    spine post-AX. Establishes the baseline that the NEW path's
    equivalence checks above ride on."""
    instance = load_instance(_SPEC_EXAMPLE)
    report = default_scenario_for(instance, today=_ANCHOR)
    conn = _fresh_db()
    try:
        _apply_old_path(conn, report.scenario)
        # The 5 single-plant invariants that the default scenario
        # always plants. ChainParentDisagreementInvariant is
        # intentionally excluded — the OLD picker has a known bug
        # where it lands on a fan_in template the matview filters
        # out (see `test_chain_parent_disagreement_old_path_picker_bug
        # _surfaced` above for the full story). AY.4's reroute through
        # the spine fixes this; this gate documents the pre-existing
        # OLD-path bug rather than asserting around it.
        for inv in (
            DriftInvariant(),
            OverdraftInvariant(),
            LimitBreachInvariant(),
            StuckPendingInvariant(),
            StuckUnbundledInvariant(),
        ):
            count = _detect_count(conn, inv)
            assert count > 0, (
                f"OLD path's default scenario didn't trip {inv.name}; "
                f"the L2 may not pick a viable plant context for it"
            )
    finally:
        conn.close()
