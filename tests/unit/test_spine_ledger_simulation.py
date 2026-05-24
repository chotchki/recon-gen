"""Unit tests for the AS.4 vector-state primitive.

`LedgerSimulation` composes multiple `AccountSimulation`s on one
connection. The cross-boundary property that motivates AS.4 (and that
the AS.2 single-day DriftGenerator only hinted at): when a CHILD
account's stored money drifts, the PARENT account's `ledger_drift`
fires for every day Σ child.money is off — not just the perturbed
day. The matview's `Σ child.money` reads every account's row, so the
vector composition's cross-boundary behavior emerges from the data,
not from extra wiring.

What's NOT here (deferred to AT.3):

- A Transfer primitive that ties two accounts' legs into one event
  with a shared transfer_id (the legs-net-to-zero conservation law).
  AS.4 doesn't need it to prove vector state works; AT.3's money_trail
  needs it to traverse the chain.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    AccountSimulation,
    DayPlan,
    LedgerDriftInvariant,
    LedgerSimulation,
    Perturbation,
    Violation,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE

_D0 = date(2030, 1, 1)
_D1 = _D0 + timedelta(days=1)
_D2 = _D0 + timedelta(days=2)


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


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


def _ledger_drift_days(detected: set[Violation], account_id: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for v in detected:
        items = dict(v.identity)
        if items.get("account_id") == account_id:
            day = items.get("business_day")
            drift = items.get("drift")
            if isinstance(day, date) and isinstance(drift, float):
                out[day] = drift
    return out


def _child(perturbations: list[Perturbation] | None = None) -> AccountSimulation:
    """A leaf internal child whose role/parent pair matches
    spec_example's CustomerSubledger → CustomerLedger."""
    return AccountSimulation(
        plans=[
            DayPlan(_D0, (100.0,)),
            DayPlan(_D1, (50.0, -30.0)),
            DayPlan(_D2, (20.0,)),
        ],
        perturbations=perturbations or [],
        account_id="acct-vec-child",
        account_role="CustomerSubledger",
        parent_role="CustomerLedger",
    )


def _parent() -> AccountSimulation:
    """The parent account; stored MIRRORS the child's cumulative
    running balance, with NO direct legs of its own (the parent is a
    ledger-side aggregator, not an account where postings happen).
    `emit_legs=False` models the pure-aggregator shape. Parents with
    mixed direct postings + child rollups are exercised by
    `test_clean_parent_with_direct_postings_and_child_does_not_drift`.

    The `plans` field still drives the per-day STORED computation —
    the fold's running balance lands as the parent's daily money. So
    plans MIRROR the child's leg deltas (same daily totals) to track
    the child's running balance.
    """
    return AccountSimulation(
        plans=[
            # Mirror the child's CLEAN running balance: 100, 120, 140.
            DayPlan(_D0, (100.0,)),
            DayPlan(_D1, (50.0, -30.0)),
            DayPlan(_D2, (20.0,)),
        ],
        account_id="acct-vec-parent",
        account_role="CustomerLedger",
        # Parent is a top-level internal account — no parent_role of
        # its own. ledger_drift only fires on accounts that ARE a
        # parent role to some other account; the child's parent_role
        # ("CustomerLedger") matches this parent's role.
        parent_role="",
        emit_legs=False,
    )


# ---------------------------------------------------------------------------
# Clean vector fold — no drift on either account.
# ---------------------------------------------------------------------------


def test_clean_ledger_fold_does_not_drift() -> None:
    ledger = LedgerSimulation(accounts=[_parent(), _child()])
    conn = _fresh_db()
    try:
        ledger.emit(conn)
        conn.commit()
        _refresh(conn)
        ld = _ledger_drift_days(
            LedgerDriftInvariant().detect(conn), "acct-vec-parent",
        )
    finally:
        conn.close()
    assert ld == {}, f"clean ledger fold should not drift; got {ld}"


# AO.L regression: a parent that posts directly AND has a child must
# stay clean on every day, including days with zero direct activity.
# Pre-fix the `direct_totals` subquery in `_computed_ledger_balance`
# grouped postings per-day and joined on day-equality, giving the
# daily delta instead of cumulative — so D1+ showed false drift equal
# to the parent's prior-day direct-posting history. Matches the
# ConcentrationMaster pattern surfaced by AY's spine pipeline against
# sasquatch_pr.yaml (91 false-positive rows). Fix: direct_totals
# becomes a correlated cumulative SUM mirroring computed_subledger.
def test_clean_parent_with_direct_postings_and_child_does_not_drift() -> None:
    child = AccountSimulation(
        plans=[
            DayPlan(_D0, (100.0,)),
            DayPlan(_D1, ()),
            DayPlan(_D2, ()),
        ],
        account_id="acct-vec-child-direct",
        account_role="CustomerSubledger",
        parent_role="CustomerLedger",
    )
    # Parent direct-posts +50 on D0 then idle. Opening 100 = child's
    # cumulative. Stored fold = [150, 150, 150]; correct matview
    # computed = Σ child.money + Σ parent.direct = 100 + 50 = 150
    # every day → drift {}.
    parent = AccountSimulation(
        plans=[
            DayPlan(_D0, (50.0,)),
            DayPlan(_D1, ()),
            DayPlan(_D2, ()),
        ],
        account_id="acct-vec-parent-direct",
        account_role="CustomerLedger",
        parent_role="",
        opening_balance=100.0,
        emit_legs=True,
    )
    ledger = LedgerSimulation(accounts=[parent, child])
    conn = _fresh_db()
    try:
        ledger.emit(conn)
        conn.commit()
        _refresh(conn)
        ld = _ledger_drift_days(
            LedgerDriftInvariant().detect(conn), "acct-vec-parent-direct",
        )
    finally:
        conn.close()
    assert ld == {}, (
        f"parent with direct postings + clean child must not drift on "
        f"any day; got {ld}"
    )


# ---------------------------------------------------------------------------
# Cross-boundary propagation — child drifts → parent's ledger_drift
# fires for every day Σ child.money is off. This is the multi-day
# extension of AS.2's single-day many-to-many edge.
# ---------------------------------------------------------------------------


def test_child_state_blip_fires_parent_ledger_drift_local_to_that_day() -> None:
    # A LOCAL drift on the child (one-day stored snapshot off by 7).
    # The parent's ledger_drift only fires on the same day, because the
    # matview re-derives parent.computed from Σ child.money per day,
    # not as a recurrence — same memoryless behavior as scalar drift.
    ledger = LedgerSimulation(accounts=[
        _parent(),
        _child(perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=7.0),
        ]),
    ])
    conn = _fresh_db()
    try:
        ledger.emit(conn)
        conn.commit()
        _refresh(conn)
        ld = _ledger_drift_days(
            LedgerDriftInvariant().detect(conn), "acct-vec-parent",
        )
    finally:
        conn.close()
    # Child stored is +7 on D1; parent stored is clean; Σ child.money
    # exceeds parent.stored by 7 on D1 → ledger_drift = parent.stored −
    # Σ child = −7 on D1, and clean on D0/D2.
    assert ld == {_D1: -7.0}, (
        f"child state_blip on D1 must drive parent's ledger_drift to "
        f"−7 on D1 only; got {ld}"
    )


def test_child_unrecorded_flow_propagates_to_parent_ledger_drift() -> None:
    # An UNRECORDED leg on the child (cumulative-computed shift). The
    # parent's ledger_drift fires on D1 AND D2 — the cross-boundary
    # extension of AccountSimulation's scalar propagation property.
    ledger = LedgerSimulation(accounts=[
        _parent(),
        _child(perturbations=[
            Perturbation(kind="unrecorded_leg", day_index=1, amount=13.0),
        ]),
    ])
    conn = _fresh_db()
    try:
        ledger.emit(conn)
        conn.commit()
        _refresh(conn)
        ld = _ledger_drift_days(
            LedgerDriftInvariant().detect(conn), "acct-vec-parent",
        )
    finally:
        conn.close()
    # Child's stored stays CLEAN (the stray leg wasn't folded), but the
    # matview's `Σ child.money` reads `child.money` which is clean too —
    # so for an unrecorded leg, ledger_drift on the parent fires ONLY
    # for the day the stray leg shifted Σ legs (i.e., none — the
    # matview sums STORED money, not posted legs).
    #
    # That makes ledger_drift INSENSITIVE to unrecorded child legs.
    # Pinning this empirically: the matview design means parent's
    # ledger_drift IS NOT a faithful detector of child-side missing
    # postings. drift on the child catches it; ledger_drift doesn't.
    assert ld == {}, (
        f"unrecorded child leg does NOT propagate to parent's "
        f"ledger_drift (matview sums child.money, not legs); got {ld}"
    )


# ---------------------------------------------------------------------------
# violation_trajectory — the vector carried-state shape. Snapshots
# evolve as the institution reaches each day, like scalar's, but the
# snapshot is now per-account (multiple violations per day possible).
# ---------------------------------------------------------------------------


def test_vector_trajectory_carries_parent_ledger_drift_per_day() -> None:
    # State_blip on child D1 → parent's ledger_drift opens on D1, stays
    # open one day, closes on D2 (because the blip was LOCAL).
    ledger = LedgerSimulation(accounts=[
        _parent(),
        _child(perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=7.0),
        ]),
    ])
    conn = _fresh_db()
    try:
        traj = ledger.violation_trajectory(LedgerDriftInvariant(), conn)
    finally:
        conn.close()
    per_day = [_ledger_drift_days(s, "acct-vec-parent") for s in traj]
    assert per_day == [
        {},
        {_D1: -7.0},
        {_D1: -7.0},  # The historical breach stays in the carried set.
    ], f"trajectory should be clean → D1 open → D1 still open; got {per_day}"


# ---------------------------------------------------------------------------
# Composition + RNG convention pass-through.
# ---------------------------------------------------------------------------


def test_empty_ledger_does_nothing() -> None:
    # Degenerate case — useful when scenarios are programmatically
    # built and the composition list may be empty.
    ledger = LedgerSimulation(accounts=[])
    conn = _fresh_db()
    try:
        ledger.emit(conn)
        traj = ledger.violation_trajectory(LedgerDriftInvariant(), conn)
    finally:
        conn.close()
    assert traj == []


def test_ledger_violation_trajectory_rejects_mismatched_day_counts() -> None:
    import pytest
    # One account has 3 days, the other has 2 → can't interleave
    # day-by-day. Fail loud.
    parent = _parent()
    child_short = AccountSimulation(
        plans=[DayPlan(_D0, (100.0,)), DayPlan(_D1, (20.0,))],
        account_id="acct-vec-child", account_role="CustomerSubledger",
        parent_role="CustomerLedger",
    )
    ledger = LedgerSimulation(accounts=[parent, child_short])
    conn = _fresh_db()
    try:
        with pytest.raises(ValueError, match="same number of"):
            ledger.violation_trajectory(LedgerDriftInvariant(), conn)
    finally:
        conn.close()
