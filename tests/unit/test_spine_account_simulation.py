"""Unit tests for the AS.3 stateful-fold primitive.

`AccountSimulation` productionizes AP.2's spike pattern. Tests pin the
same four behavioral predictions the spike empirically verified
against the REAL emitted `drift` matview:

1. Clean fold self-validates every day (Q1: state is carried).
2. State-snapshot blip is LOCAL (detector memoryless in stored).
3. Unrecorded flow PROPAGATES forward (cumulative computed).
4. Recorded extra flow is CONFORMING (Q2: non-violating = perturbation
   off; flow/state AGREEMENT, not the absence of activity).

Plus the AP.2 refinements:
5. Correction (correct_day_index) closes forward propagation.
6. Stacked perturbations accumulate in the carried violation set.

Plus a property test for the violation-trajectory's "carried state"
contract — the snapshot list IS the active-violation-set evolving
through the fold.
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
    DriftInvariant,
    Perturbation,
    Violation,
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


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


_D0 = date(2030, 1, 1)
_D1 = _D0 + timedelta(days=1)
_D2 = _D0 + timedelta(days=2)


def _baseline_plans() -> list[DayPlan]:
    # Three days, balance runs 100 → 120 → 140.
    return [
        DayPlan(_D0, (100.0,)),
        DayPlan(_D1, (50.0, -30.0)),
        DayPlan(_D2, (20.0,)),
    ]


def _drift_for(account_id: str) -> DriftInvariant:
    # The drift invariant scopes to all internal-leaf accounts; we
    # filter the resulting set per-test for the specific account.
    return DriftInvariant()


def _drift_days_for(detected: set[Violation], account_id: str) -> dict[date, float]:
    """Project drift Violations to {day: drift_value} for one account."""
    out: dict[date, float] = {}
    for v in detected:
        items = dict(v.identity)
        if items.get("account_id") == account_id:
            day = items.get("business_day")
            drift = items.get("drift")
            if isinstance(day, date) and isinstance(drift, float):
                out[day] = drift
    return out


# ---------------------------------------------------------------------------
# Q1 — state is carried; clean fold self-validates every day.
# ---------------------------------------------------------------------------


def test_clean_simulation_self_validates_every_day() -> None:
    sim = AccountSimulation(
        plans=_baseline_plans(), account_id="acct-clean",
    )
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        _refresh(conn)
        drift = _drift_days_for(
            _drift_for("acct-clean").detect(conn), "acct-clean",
        )
    finally:
        conn.close()
    assert drift == {}, f"clean fold should not drift; got {drift}"


# ---------------------------------------------------------------------------
# Q3 — propagation is governed by which side you break.
# ---------------------------------------------------------------------------


def test_state_snapshot_blip_is_local() -> None:
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=7.0),
        ],
        account_id="acct-blip",
    )
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        _refresh(conn)
        drift = _drift_days_for(
            _drift_for("acct-blip").detect(conn), "acct-blip",
        )
    finally:
        conn.close()
    assert drift == {_D1: 7.0}, (
        f"a one-day stored blip must stay LOCAL (detector memoryless "
        f"in stored); got {drift}"
    )


def test_unrecorded_flow_propagates_forward() -> None:
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="unrecorded_leg", day_index=1, amount=13.0),
        ],
        account_id="acct-stray",
    )
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        _refresh(conn)
        drift = _drift_days_for(
            _drift_for("acct-stray").detect(conn), "acct-stray",
        )
    finally:
        conn.close()
    assert drift == {_D1: -13.0, _D2: -13.0}, (
        f"an unrecorded flow must PROPAGATE forward from its day; "
        f"got {drift}"
    )


# ---------------------------------------------------------------------------
# Q2 — non-violating = same simulation with perturbation off, AND a
# recorded extra flow is equally non-violating.
# ---------------------------------------------------------------------------


def test_recorded_extra_flow_is_conforming() -> None:
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="recorded_leg", day_index=1, amount=500.0),
        ],
        account_id="acct-recorded",
    )
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        _refresh(conn)
        drift = _drift_days_for(
            _drift_for("acct-recorded").detect(conn), "acct-recorded",
        )
    finally:
        conn.close()
    assert drift == {}, (
        f"a recorded extra flow must conform (flow/state agreement); "
        f"got {drift}"
    )


# ---------------------------------------------------------------------------
# AP.2 refinement — correction closes forward propagation.
# ---------------------------------------------------------------------------


def test_correction_closes_forward_propagation() -> None:
    # Unrecorded leg on day 1; booked on day 2 (the AN.1 supersession
    # shape). D1's historical breach remains; D2's forward propagation
    # is stopped because stored catches up.
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(
                kind="unrecorded_leg", day_index=1, amount=13.0,
                correct_day_index=2,
            ),
        ],
        account_id="acct-corrected",
    )
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        _refresh(conn)
        drift = _drift_days_for(
            _drift_for("acct-corrected").detect(conn), "acct-corrected",
        )
    finally:
        conn.close()
    assert drift == {_D1: -13.0}, (
        f"correction must leave only the past breach (D1) and stop "
        f"forward propagation (D2 clean); got {drift}"
    )


# ---------------------------------------------------------------------------
# AP.2 refinement — stacked perturbations accumulate.
# ---------------------------------------------------------------------------


def test_stacked_violations_accumulate_without_interference() -> None:
    # Two independent local blips on different days. Each adds its own
    # violation to the carried set; existing ones persist.
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="state_blip", day_index=1, amount=7.0),
            Perturbation(kind="state_blip", day_index=2, amount=9.0),
        ],
        account_id="acct-stacked",
    )
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        _refresh(conn)
        drift = _drift_days_for(
            _drift_for("acct-stacked").detect(conn), "acct-stacked",
        )
    finally:
        conn.close()
    assert drift == {_D1: 7.0, _D2: 9.0}, (
        f"stacked violations should accumulate, each keeping identity; "
        f"got {drift}"
    )


# ---------------------------------------------------------------------------
# `violation_trajectory` — the carried-violation-set state across days.
# Refreshes + detects after each day; the snapshot list IS the active
# violation set as the institution reaches each day.
# ---------------------------------------------------------------------------


def test_violation_trajectory_tracks_carried_state() -> None:
    # Unrecorded leg on day 1 propagates to day 2: snapshots evolve
    # {} → {D1} → {D1, D2}. The per-step delta IS the step's effect.
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturbations=[
            Perturbation(kind="unrecorded_leg", day_index=1, amount=13.0),
        ],
        account_id="acct-traj",
    )
    conn = _fresh_db()
    try:
        traj = sim.violation_trajectory(DriftInvariant(), conn)
    finally:
        conn.close()

    # Project each snapshot to {day: drift} for this account.
    days_at = [
        _drift_days_for(s, "acct-traj") for s in traj
    ]
    assert days_at == [
        {},
        {_D1: -13.0},
        {_D1: -13.0, _D2: -13.0},
    ], f"trajectory should evolve clean → D1 → D1+D2; got {days_at}"
