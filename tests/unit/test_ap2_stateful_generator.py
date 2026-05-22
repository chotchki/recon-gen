"""AP.2 spike — is a generator a stateful simulation, and is "non-violating"
the same generator with perturbation off?

Sibling to the AP.3 spike (`test_ap3_invariant_self_validation.py`). AP.3
proved the round-trip `detect(gen.emit()) ⊇ intended` on hand-set,
**single-day, stateless** data. It deliberately did NOT simulate state. AP.2
closes that gap — the generator side of the spine
(`docs/audits/date_range_model_audit.md` §5, "AP.3 extension"):

    a ViolationGenerator is not `rows`, nor `Stream -> Stream` of independent
    rows, but a STATE STEP `State -> (flows, State')` folded forward —
    clean for the baseline, perturbed for a violation.

THREE questions AP.2 must settle (per the audit's AP.3-extension §):

  Q1  Does the generator carry state? Is generation a fold
      `State -> (flows, State')` day by day, where the emitted daily balance
      IS the running State'?
  Q2  Is "non-violating" just the same generator with the perturbation off?
      (⇒ conformance is first-class — the clean run — not "everything we
      didn't break".)
  Q3  Does a violation PROPAGATE through state, and what governs it?

WHAT GROUNDS THE ANSWER — the REAL emitted `drift` detector. From
`schema.py::_L1_INVARIANT_VIEWS_TEMPLATE`, the detector computes, per day:

    computed_balance(D) = Σ posted legs WHERE posting <= business_day_end(D)
    drift(D)            = stored_money(D) − computed_balance(D)

`computed_balance` is **cumulative over the absolute leg stream** and is
re-derived independently for each day — there is NO recurrence on
`stored(D-1)`. That single structural fact decides Q3, and the spike VERIFIES
it against the real matview (no re-encoded detection logic — `detect()` reads
the matview output, exactly as AP.3):

  * STATE-snapshot perturbation (corrupt one day's stored balance, carry the
    clean running balance forward) → drift is LOCAL to that day. The detector
    is memoryless in `stored`; it re-derives `computed` from the leg stream.
  * UNRECORDED-flow perturbation (emit a leg, do NOT fold it into stored) →
    drift PROPAGATES forward to every later day. `computed` is cumulative, so
    every later day's sum carries the stray leg while `stored` followed the
    clean fold.
  * RECORDED extra flow (emit a leg AND fold it into stored) → CONFORMING,
    ∅ drift. A different-but-consistent history. The violation was never "a
    flow"; it is the DISAGREEMENT between flow and state.

The spine vocab stays LOCAL to the spike (not promoted to `src/`), same
discipline as AP.3 — the rollout decides the production home + shape.
FINDINGS are recorded inline + folded back into the audit §5.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


# ---------------------------------------------------------------------------
# In-process harness — real emitted schema + refresh, no DB server.
# (Re-stated locally; AP.2 reads as a standalone artifact, same as AP.3.)
# ---------------------------------------------------------------------------


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)  # STDDEV_SAMP for the windowed matview
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


_TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name", "origin",
)
_DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money",
)


def _insert_tx(conn: sqlite3.Connection, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _TX_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_transactions ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _TX_COLS],
    )


def _insert_balance(conn: sqlite3.Connection, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _DB_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_daily_balances ({', '.join(_DB_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _DB_COLS],
    )


def _ts(day: date, hour: int = 12) -> str:
    return datetime(day.year, day.month, day.day, hour).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )


# ---------------------------------------------------------------------------
# The detector — REAL emitted `drift` matview, read PER DAY.
#
# AP.3's DriftInvariant collapsed days (account_id, drift). AP.2's whole point
# is propagation, so detect() keys by business_day_start: which days drift,
# and by how much. Still a thin read of the matview output — the detection
# logic is the emitted SQL.
# ---------------------------------------------------------------------------


class DriftByDayInvariant:
    name = "drift"

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id

    def detect(self, conn: sqlite3.Connection) -> dict[date, float]:
        """day → drift, for this account, over every day the matview flags.
        Clean ⇒ {} (the matview only emits rows where stored <> computed)."""
        rows = conn.execute(
            f"SELECT business_day_start, drift FROM {_PREFIX}_drift "
            f"WHERE account_id = ?",
            (self.account_id,),
        ).fetchall()
        return {
            datetime.strptime(str(bds)[:10], "%Y-%m-%d").date(): round(float(d), 2)
            for bds, d in rows
        }


# ---------------------------------------------------------------------------
# The generator AS A STATE STEP — `State -> (flows, State')` folded forward.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayPlan:
    """One day's intended activity: a tuple of signed leg amounts. A clean
    fold sets State'.balance = State.balance + Σ legs and stores it."""

    day: date
    legs: tuple[float, ...]


# A perturbation names HOW a single day's step deviates from the clean fold.
# This is the knob Q2 turns: kind="none" is the clean run; the others each
# break the flow/state agreement in one specific way. ``correct_day_index``
# (when set) is the day an unrecorded leg is finally BOOKED into stored — a
# correction that closes the forward propagation (the AN.1 supersession shape).
PerturbKind = Literal["none", "state_blip", "unrecorded_leg", "recorded_leg"]


@dataclass(frozen=True)
class Perturbation:
    kind: PerturbKind = "none"
    day_index: int = 0
    amount: float = 0.0
    correct_day_index: int | None = None


@dataclass(frozen=True)
class DayEmission:
    """The materialized result of one folded step: the legs to write and the
    stored balance that IS the running State'. The fold (pure) produces these;
    emission (IO) just writes them. Separating the two lets the SAME fold be
    written all-at-once (``run``) or day-by-day with detect-between
    (``violation_trajectory``)."""

    day: date
    legs: tuple[tuple[str, float], ...]  # (tag, signed amount)
    stored: float


@dataclass
class AccountSimulation:
    """A leaf internal account stepped forward day by day. The fold is the
    institution's conservation law made executable: each day's stored balance
    IS the running State' (Σ recorded legs so far). The same object produces
    the clean run (perturbation off) or a violation (perturbation on) —
    answering Q1 (state is carried) and Q2 (non-violating = perturb off) by
    construction.

    account_role / parent_role would be resolved from the shape via
    `Invariant.scenario_for(role)` (AP.3 extension); pinned here so AP.2 can
    focus on the temporal/state axis it exists to probe.
    """

    plans: list[DayPlan]
    perturb: Perturbation = field(default_factory=Perturbation)
    perturbs: list[Perturbation] = field(default_factory=list[Perturbation])
    account_id: str = "acct-sim"
    account_role: str = "CustomerSubledger"
    parent_role: str = "CustomerLedger"
    opening_balance: float = 0.0

    def _all_perturbs(self) -> list[Perturbation]:
        # A scenario STACKS perturbations (the temporal form of AP.3's
        # composition): the single ``perturb`` plus any in ``perturbs``. Each
        # contributes its own violation to the carried set.
        primary = [self.perturb] if self.perturb.kind != "none" else []
        return primary + self.perturbs

    def _fold(self) -> list[DayEmission]:
        """PURE state evolution: `State -> (flows, State')` over days. No IO.
        Stacks every perturbation: a recorded leg folds into the balance, an
        unrecorded leg does not (it propagates) until its ``correct_day_index``
        books it, and a state blip bends only that day's stored snapshot."""
        balance = self.opening_balance
        out: list[DayEmission] = []
        ps = self._all_perturbs()
        for i, plan in enumerate(self.plans):
            legs: list[tuple[str, float]] = [
                (f"d{i}-{j}", amt) for j, amt in enumerate(plan.legs)
            ]
            balance += sum(plan.legs)

            blip_total = 0.0
            for k, p in enumerate(ps):
                if p.day_index == i and p.kind == "recorded_leg":
                    # An EXTRA real leg that DOES fold into state — a
                    # different-but-consistent history (conforming).
                    legs.append((f"d{i}-extra{k}", p.amount))
                    balance += p.amount
                if p.day_index == i and p.kind == "unrecorded_leg":
                    # A leg in the stream NOT folded into stored — flow/state
                    # disagreement that propagates (computed is cumulative).
                    legs.append((f"d{i}-stray{k}", p.amount))
                    # balance deliberately NOT updated.
                if p.correct_day_index == i and p.kind == "unrecorded_leg":
                    # The missing leg is finally booked: stored catches up, so
                    # this day and every later day agree again. The historical
                    # breach (days before the correction) remains.
                    balance += p.amount
                if p.day_index == i and p.kind == "state_blip":
                    # Corrupt only THIS day's stored snapshot; the carried
                    # running balance stays clean → local, no propagation
                    # (detector is memoryless in stored). Multiple blips on a
                    # day stack.
                    blip_total += p.amount

            out.append(DayEmission(plan.day, tuple(legs), balance + blip_total))
        return out

    def _emit_day(self, conn: sqlite3.Connection, em: DayEmission) -> None:
        for tag, amount in em.legs:
            direction = "Credit" if amount >= 0 else "Debit"
            _insert_tx(
                conn, id=f"tx-{self.account_id}-{tag}",
                account_id=self.account_id, account_name="Sim Acct",
                account_role=self.account_role, account_scope="internal",
                account_parent_role=self.parent_role, amount_money=amount,
                amount_direction=direction, status="Posted", posting=_ts(em.day),
                transfer_id=f"xfer-{self.account_id}-{tag}", rail_name="ach",
                origin="etl",
            )
        start, end = _day_bounds(em.day)
        _insert_balance(
            conn, account_id=self.account_id, account_name="Sim Acct",
            account_role=self.account_role, account_scope="internal",
            account_parent_role=self.parent_role, business_day_start=start,
            business_day_end=end, money=em.stored,
        )

    def run(self, conn: sqlite3.Connection) -> None:
        for em in self._fold():
            self._emit_day(conn, em)

    def violation_trajectory(
        self, conn: sqlite3.Connection,
    ) -> list[dict[date, float]]:
        """Carry the VIOLATION SET as state through the fold: emit day i,
        refresh, detect, snapshot. The returned list is the active-violation
        set as the institution reaches each day — what lets a generator tell
        whether a step had an EFFECT (the delta between consecutive snapshots)
        rather than emit-and-hope. (Per-day refresh mirrors per-load ETL.)"""
        inv = DriftByDayInvariant(self.account_id)
        snapshots: list[dict[date, float]] = []
        for em in self._fold():
            self._emit_day(conn, em)
            conn.commit()
            _refresh(conn)
            snapshots.append(inv.detect(conn))
        return snapshots


# ---------------------------------------------------------------------------
# A 3-day clean baseline both the clean run and every perturbation share.
# Running balance: 100, 120, 140.
# ---------------------------------------------------------------------------

_D0 = date(2030, 3, 15)
_D1 = date(2030, 3, 16)
_D2 = date(2030, 3, 17)


def _baseline_plans() -> list[DayPlan]:
    return [
        DayPlan(_D0, (100.0,)),
        DayPlan(_D1, (50.0, -30.0)),
        DayPlan(_D2, (20.0,)),
    ]


def _run_and_detect(sim: AccountSimulation) -> dict[date, float]:
    conn = _fresh_db()
    try:
        sim.run(conn)
        conn.commit()
        _refresh(conn)
        return DriftByDayInvariant(sim.account_id).detect(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Q1 — the generator carries state; the clean fold self-validates every day.
# ---------------------------------------------------------------------------


def test_clean_simulation_self_validates_every_day() -> None:
    # The fold (stored(D) = Σ legs ≤ D) satisfies the conservation law on
    # every one of the three days. detect == {} confirms it against the REAL
    # matview — the simulation runs clean. This is Q1 (state carried: each
    # stored IS the running balance) + the baseline for Q2.
    drift = _run_and_detect(AccountSimulation(plans=_baseline_plans()))
    assert drift == {}, f"clean multi-day fold should not drift; got {drift}"


# ---------------------------------------------------------------------------
# Q2 — "non-violating" is the same generator with the perturbation off, and
# a RECORDED extra flow is equally non-violating (conformance = flow/state
# agree, not "no activity").
# ---------------------------------------------------------------------------


def test_recorded_extra_flow_is_conforming() -> None:
    # Same generator, a real extra +500 leg on day 1 that DOES fold into
    # stored. A different history, still consistent → ∅ drift. So the
    # violation is never "a flow"; it is the flow/state DISAGREEMENT. This is
    # the affirmative answer to Q2: perturbation "off" includes any step
    # where the flow is recorded.
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturb=Perturbation(kind="recorded_leg", day_index=1, amount=500.0),
    )
    assert _run_and_detect(sim) == {}, "a recorded extra flow must conform"


# ---------------------------------------------------------------------------
# Q3 — propagation is governed by WHAT you perturb (state snapshot vs flow),
# and the spike VERIFIES the prediction from the detector SQL.
# ---------------------------------------------------------------------------


def test_state_snapshot_blip_is_local() -> None:
    # Corrupt ONLY day-1's stored balance by +7 (a snapshot typo); the
    # carried running balance stays clean, so day 2 re-derives correctly.
    # PREDICTION (detector memoryless in stored): drift on day 1 only.
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturb=Perturbation(kind="state_blip", day_index=1, amount=7.0),
    )
    drift = _run_and_detect(sim)
    assert drift == {_D1: 7.0}, (
        f"a one-day stored blip must stay LOCAL (detector re-derives computed "
        f"from the leg stream each day); got {drift}"
    )


def test_unrecorded_flow_propagates_forward() -> None:
    # Emit a +13 leg on day 1 that is NOT folded into stored (a posting the
    # ledger missed). PREDICTION (computed is cumulative over the leg stream):
    # day 1 AND day 2 drift by −13; day 0 (before the stray leg) is clean.
    sim = AccountSimulation(
        plans=_baseline_plans(),
        perturb=Perturbation(kind="unrecorded_leg", day_index=1, amount=13.0),
    )
    drift = _run_and_detect(sim)
    assert drift == {_D1: -13.0, _D2: -13.0}, (
        f"an unrecorded flow must PROPAGATE forward from its day (cumulative "
        f"computed carries the stray leg; stored stayed on the clean fold), "
        f"and must NOT touch earlier days; got {drift}"
    )


# ---------------------------------------------------------------------------
# The shape decision finding #4 left open: uniform state-step, NOT
# kind-indexed `()->rows | Stream->Stream`. The "minimal standalone witness"
# (the docs/teaching payoff finding #4 feared losing) is recovered as a
# DEGENERATE one-day simulation — the same generator type, a short fold.
# ---------------------------------------------------------------------------


def test_minimal_witness_is_a_one_day_simulation() -> None:
    # AP.3's "drift in 2 rows" minimal witness == AccountSimulation with a
    # single DayPlan + a state blip. No second generator shape needed; the
    # uniform `State -> (flows, State')` subsumes the Local minimal witness as
    # its shortest fold. Settles finding #4's open DECISION.
    sim = AccountSimulation(
        plans=[DayPlan(_D0, (100.0,))],
        perturb=Perturbation(kind="state_blip", day_index=0, amount=5.0),
    )
    assert _run_and_detect(sim) == {_D0: 5.0}


# ---------------------------------------------------------------------------
# Carried violation set (the user's AP.2 refinement): the generator carries
# not just the balance but the ACTIVE VIOLATION SET, so it can tell whether a
# step had an effect — and so a scenario can STACK violations.
# ---------------------------------------------------------------------------


def test_carried_violations_track_a_steps_effect() -> None:
    # Refresh+detect after each day == the violation set carried as state
    # through the fold. An unrecorded leg on day 1 OPENS a drift that then
    # PERSISTS as carried state into day 2 (it doesn't self-clear — that is
    # what "carry it" buys: the generator sees the breach is still live).
    conn = _fresh_db()
    try:
        sim = AccountSimulation(
            plans=_baseline_plans(),
            perturb=Perturbation(kind="unrecorded_leg", day_index=1, amount=13.0),
        )
        traj = sim.violation_trajectory(conn)
    finally:
        conn.close()
    assert traj == [{}, {_D1: -13.0}, {_D1: -13.0, _D2: -13.0}], (
        f"carried set should be clean, then open at D1, then propagate to D2; "
        f"got {traj}"
    )
    # The EFFECT of each step = the delta between consecutive snapshots:
    assert traj[1].keys() - traj[0].keys() == {_D1}, "day-1 step opened D1"
    assert traj[2].keys() - traj[1].keys() == {_D2}, "day-2 step propagated to D2"


def test_correction_closes_forward_propagation() -> None:
    # Same unrecorded leg on day 1, but BOOKED on day 2 (a posted correction).
    # Carrying the violation set lets the generator confirm the correction had
    # the intended effect: D2 never opens (vs the uncorrected run), while the
    # historical D1 breach correctly remains.
    corrected = AccountSimulation(
        plans=_baseline_plans(),
        perturb=Perturbation(
            kind="unrecorded_leg", day_index=1, amount=13.0, correct_day_index=2,
        ),
    )
    uncorrected = AccountSimulation(
        plans=_baseline_plans(),
        perturb=Perturbation(kind="unrecorded_leg", day_index=1, amount=13.0),
    )
    after_correct = _run_and_detect(corrected)
    after_none = _run_and_detect(uncorrected)
    assert after_correct == {_D1: -13.0}, "correction must leave only the past breach"
    assert after_none == {_D1: -13.0, _D2: -13.0}
    # The correction's measurable effect = it closed the forward propagation.
    assert after_none.keys() - after_correct.keys() == {_D2}


def test_stacked_violations_accumulate_without_interference() -> None:
    # "So you could stack additional violations." Two independent local blips
    # on different days; the carried set GROWS by exactly one each step and
    # each retains its own identity (a later plant doesn't mask an earlier
    # one). This is AP.3's scenario composition in the temporal/state form.
    conn = _fresh_db()
    try:
        sim = AccountSimulation(
            plans=_baseline_plans(),
            perturbs=[
                Perturbation(kind="state_blip", day_index=1, amount=7.0),
                Perturbation(kind="state_blip", day_index=2, amount=9.0),
            ],
        )
        traj = sim.violation_trajectory(conn)
    finally:
        conn.close()
    assert traj == [{}, {_D1: 7.0}, {_D1: 7.0, _D2: 9.0}], (
        f"stacked violations should accumulate, each keeping identity; got {traj}"
    )
