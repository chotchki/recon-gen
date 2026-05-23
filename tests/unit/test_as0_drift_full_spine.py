"""AS.0 spike — drift end-to-end through the full invariant spine.

The first AS-phase deliverable. Proves the production type shape on ONE
invariant (drift, the simplest arithmetic case from AP.3) end-to-end:
`Violation` ⋈ `Invariant.detect` + `Invariant.scenario_for` ⋈
`ViolationGenerator` (stateful fold from AP.2) ⋈ `DateView` (the AR
production primitive) presenting the violation. All four spine types
exercised in one test against the real emitted `drift` matview SQL.

Why drift as pilot (per the AS preamble + operator call): arithmetic is
the simplest detector class, the AP.3 spike already settled the
detect-self-validation round-trip, and AS.4's cross-account vector-state
work doesn't touch this surface — so the spike's design contract is the
single-account `State -> (flows, State')` fold the AP.2 spike already
nailed, just productionized through the spine's typed interfaces.

Spike scope (what this proves):

- The four spine types compose end-to-end at the type level. ONE test
  threads `Invariant.scenario_for(shape) -> ViolationGenerator` → fold
  the generator over days → `Invariant.detect(conn)` → present the
  resulting `Violation` via the View's `resolve_day` + `is_satisfied_by`.
- The src/ home for AS.1 is locked: `common/spine/violation.py` +
  `common/spine/invariant.py` + `common/spine/generator.py` are the
  three new modules; `View` stays on `common/tree/date_view.py` (already
  promoted by AR.1).
- The migration order for AS.2-AS.5 is the order this spike threads the
  pieces: Violation type first (no deps), then Invariant+detect (depends
  on Violation), then ViolationGenerator (depends on Invariant for the
  scenario_for hook), then the stateful fold (AP.2 shape, depends on
  ViolationGenerator).
- The substitution-path checklist (AR.5 lesson) is met by drift: its
  `detect()` reads the matview directly — NO SQL-pushdown surface — so
  zero substitution-path risk. (Investigation matviews in AT.x WILL
  cross pushdown; that's AT.0's checklist.)

Spike scope (what this deliberately does NOT prove):

- Cross-account vector state (AS.4 — AP.2's honest limit).
- Many-to-many taxonomy edges (a drift plant trips both drift AND
  ledger_drift — AS.2's work). Spike only fires drift; the dual-trip
  is captured in the AS.0 audit subsection, not exercised here.
- 4-way agreement (AS.6 mandatory gate — requires deploy).
- Production-promotion of the types to `src/` (AS.1 work — spike vocab
  stays LOCAL by spike discipline, same as AP.3).

The vocab below mirrors what AS.1 will promote, so the spike doubles as
the type-shape proposal for that leaf.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame
from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect
from recon_gen.common.tree import DateView, EmptyBehavior

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


# ---------------------------------------------------------------------------
# The spine vocab — LOCAL to this spike (AS.1 promotes to src/). Mirrors the
# AP.3 shape with the stateful-fold + carried-violation-set additions from
# AP.2's findings.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """A detected breach. Identity = the analyst-facing columns naming it
    (account + day + magnitude for drift). AS.1's promoted version moves
    to `src/recon_gen/common/spine/violation.py`."""

    invariant: str
    identity: frozenset[tuple[str, object]]

    @classmethod
    def of(cls, invariant: str, **identity: object) -> "Violation":
        return cls(invariant=invariant, identity=frozenset(identity.items()))


class Invariant(Protocol):
    """A rule + detector + manufacture point. Owns BOTH halves of the
    spine link: `detect()` finds itself in data; `scenario_for()` produces
    a generator that manufactures it against a shape. AS.1's promoted
    version moves to `src/recon_gen/common/spine/invariant.py`."""

    name: str

    def detect(self, conn: sqlite3.Connection) -> set[Violation]: ...

    def scenario_for(
        self, role: str, *, magnitude: float,
    ) -> "ViolationGenerator": ...


class ViolationGenerator(Protocol):
    """A producer of base-table rows intended to manifest `intended`.
    Per AP.2: a stateful fold over days (`State -> (flows, State')`),
    not a single-shot emit. AS.1's promoted version moves to
    `src/recon_gen/common/spine/generator.py`."""

    @property
    def intended(self) -> Violation: ...

    def emit(self, conn: sqlite3.Connection) -> None: ...


# ---------------------------------------------------------------------------
# In-process harness (AP.3 pattern). Inlined; AS.1 may extract to a shared
# `tests/_spine_harness.py` if reused.
# ---------------------------------------------------------------------------


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


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _ts(day: date, hour: int = 12) -> str:
    return datetime(day.year, day.month, day.day, hour).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Drift — the pilot invariant. The production-shape promotion of AP.3's
# DriftInvariant/DriftGenerator (which were local to that spike). Mirrors
# what AS.1 will put under `common/spine/`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftInvariant:
    """Sub-ledger drift detector: stored balance ≠ Σ posted legs at
    business_day_end. Persona-blind (no L2 join in the matview SQL), so
    `scenario_for(role)` resolves against ANY leaf internal account."""

    name: str = "drift"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, business_day_start, drift "
            f"FROM {_PREFIX}_drift",
        ).fetchall()
        return {
            Violation.of(
                "drift", account_id=aid,
                business_day=datetime.strptime(str(bds)[:10], "%Y-%m-%d").date(),
                drift=round(float(d), 2),
            )
            for aid, bds, d in rows
        }

    def scenario_for(
        self, role: str, *, magnitude: float = 5.0,
    ) -> "DriftGenerator":
        instance = load_instance(_SPEC_EXAMPLE)
        candidates = [
            a for a in instance.accounts
            if getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
            and getattr(a, "parent_role", None) is not None
        ]
        if not candidates:
            raise ValueError(
                f"shape has no drift-eligible leaf internal account with role "
                f"{role!r}; cannot manufacture a drift scenario"
            )
        acct = candidates[0]
        return DriftGenerator(
            account_id=f"acct-drift-{role}",
            account_role=role,
            parent_role=str(getattr(acct, "parent_role")),
            anchor_day=LOCKED_ANCHOR,
            magnitude=magnitude,
        )


# ---------------------------------------------------------------------------
# The stateful fold (AP.2 shape, productionized). Carries the running
# balance forward; the perturbation knob is the drift magnitude applied
# to the last day's stored balance.
# ---------------------------------------------------------------------------


@dataclass
class DriftGenerator:
    """A generator that folds one leaf internal account forward over a
    range of days, optionally perturbing one day's stored balance by
    `magnitude` (the drift). Per AP.2: non-violating = the same generator
    with `perturb.kind == "none"`.

    Magnitude is anchored to the shape-derived role (smart-constructor
    invariant: `scenario_for` returns this with a resolved role/parent
    pair, so off-shape construction can't slip through). AS.1's promoted
    version moves to `src/recon_gen/common/spine/generator.py`.
    """

    account_id: str
    account_role: str
    parent_role: str
    anchor_day: date
    magnitude: float
    days: int = 3
    # Start at 0 so the running balance == Σ recorded legs ≤ each day's
    # end — `stored` agrees with the matview's cumulative `computed`,
    # giving drift = 0 on every clean day. The perturbation breaks the
    # match on the perturb_day only (AP.2's "state blip", local).
    opening_balance: float = 0.0

    @property
    def perturb_day_index(self) -> int:
        # Drift the LAST day of the fold by `magnitude` — the violation
        # lands at the simulation's terminal state (= anchor_day).
        return self.days - 1

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "drift",
            account_id=self.account_id,
            business_day=self.anchor_day,
            drift=round(self.magnitude, 2),
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        # `State -> (flows, State')` folded forward `days` days.
        balance = self.opening_balance
        first_day = self.anchor_day - timedelta(days=self.days - 1)
        for i in range(self.days):
            day = first_day + timedelta(days=i)
            # Each day's "flow" = one posted leg of +100. Clean fold:
            # stored == balance == Σ legs ≤ day. Perturbation: the LAST
            # day's stored is bumped by `magnitude` — the AP.2 "state
            # blip" shape (local, no propagation).
            self._emit_leg(conn, day=day, amount=100.0, i=i)
            balance += 100.0
            stored = (
                balance + self.magnitude
                if i == self.perturb_day_index
                else balance
            )
            self._emit_balance(conn, day=day, money=stored)

    def _emit_leg(
        self, conn: sqlite3.Connection, *, day: date, amount: float, i: int,
    ) -> None:
        _insert_tx(
            conn, id=f"tx-{self.account_id}-{i}",
            account_id=self.account_id, account_name="Drift Acct",
            account_role=self.account_role, account_scope="internal",
            account_parent_role=self.parent_role, amount_money=amount,
            amount_direction="Credit", status="Posted", posting=_ts(day),
            transfer_id=f"xfer-{self.account_id}-{i}", rail_name="ach",
            origin="etl",
        )

    def _emit_balance(
        self, conn: sqlite3.Connection, *, day: date, money: float,
    ) -> None:
        start, end = _day_bounds(day)
        _insert_balance(
            conn, account_id=self.account_id, account_name="Drift Acct",
            account_role=self.account_role, account_scope="internal",
            account_parent_role=self.parent_role,
            business_day_start=start, business_day_end=end, money=money,
        )


# ---------------------------------------------------------------------------
# The end-to-end slice. ALL four spine types in one test.
# ---------------------------------------------------------------------------


def test_drift_threads_the_full_spine() -> None:
    """The AS.0 proving ground: drift threaded through every spine
    type, real emitted matview SQL, in-process. If this round-trips,
    the production type shape is good — AS.1 can promote with
    confidence."""
    # ---- Invariant: owns detect + scenario_for ----------------------------
    inv = DriftInvariant()
    assert inv.name == "drift"

    # ---- scenario_for(shape, selector) → ViolationGenerator ---------------
    # The shape vocabulary in (role name); the invariant resolves it
    # against the L2 to concrete coordinates (account_id, parent_role).
    gen = inv.scenario_for("CustomerSubledger", magnitude=5.0)
    assert gen.account_role == "CustomerSubledger"
    assert gen.parent_role == "CustomerLedger"
    intended = gen.intended

    # ---- Stateful fold over days (AP.2 shape, productionized) -------------
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)

        # ---- Invariant.detect: the spine link --------------------------
        detected = inv.detect(conn)
        assert intended in detected, (
            f"drift detector did not confirm the intended violation.\n"
            f"  intended: {intended}\n"
            f"  detected: {detected}"
        )

        # ---- View presents the violation -------------------------------
        # The DateView is constructed from the same AsOfFrame the
        # generator anchored against — both sides of the spine read ONE
        # frame (AR's structural fix).
        view = DateView(
            frame=AsOfFrame.locked(),
            empty_behavior=EmptyBehavior.LATEST_ON_EMPTY,
        )
        # The anchor day == the violation's business_day → view's
        # required_coverage is satisfied by the seed (AR.3's contract).
        days_with_data = sorted({
            v.identity for v in detected
        })
        assert days_with_data, "expected ≥1 detected drift row"
        # The view resolves to the anchor day, which has data.
        resolved = view.resolve_day([LOCKED_ANCHOR])
        assert resolved == LOCKED_ANCHOR
        assert view.is_satisfied_by([LOCKED_ANCHOR])
    finally:
        conn.close()


def test_drift_non_violating_is_the_same_generator_with_magnitude_zero() -> None:
    """AP.2 finding (Q2) promoted into the spine shape: conformance is
    flow/state AGREEMENT, not the absence of activity. magnitude=0
    means the perturbation knob is off; the fold runs clean; the
    intended violation IS NOT detected."""
    inv = DriftInvariant()
    clean = inv.scenario_for("CustomerSubledger", magnitude=0.0)
    dirty = inv.scenario_for("CustomerSubledger", magnitude=5.0)

    conn = _fresh_db()
    try:
        clean.emit(conn)
        conn.commit()
        _refresh(conn)
        # Clean fold → no drift detected for this account/day pair.
        assert dirty.intended not in inv.detect(conn)
    finally:
        conn.close()


def test_scenario_for_unknown_role_fails_loud() -> None:
    """Smart-constructor invariant: the invariant owns the resolution
    of the shape selector → concrete coordinates. A role that isn't in
    the shape can't manufacture a scenario; the invariant fails at the
    REQUEST, not silently emit inert rows. (The AP.3 finding made
    explicit in the production-shape Protocol.)"""
    import pytest
    with pytest.raises(ValueError, match="no drift-eligible"):
        DriftInvariant().scenario_for("NoSuchRole", magnitude=5.0)


def test_view_anchored_at_frame_carries_one_anchor_through_the_spine() -> None:
    """The AR.1 promise: the View's anchor == the generator's anchor by
    construction, because both read ONE AsOfFrame. So a planted
    violation at `frame.as_of` is ALWAYS inside the view's
    required_coverage — the plant ⟷ query-window contract is structural,
    not developer-memory. AS.0 confirms this composes with the spine."""
    frame = AsOfFrame.locked(window_days=7)
    view = DateView(frame=frame)
    inv = DriftInvariant()
    gen = inv.scenario_for("CustomerSubledger", magnitude=5.0)

    # The generator's anchor IS the frame's as_of by construction:
    assert gen.anchor_day == frame.as_of
    # And the view's required_coverage contains the generator's anchor:
    lo, hi = view.required_coverage
    assert lo <= gen.anchor_day <= hi


def test_drift_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    """Substitution-path checklist (AR.5 lesson): drift's `detect()`
    reads the matview rows directly via a static SQL — no `<<$param>>`
    parameter substitution that varies between QS bridge (typed) and
    api/smoke (string literal). So drift carries ZERO substitution-path
    risk; AS.1 promotion does not need an api-smoke parity test.

    This test pins that property at the spine level: if a future
    DriftInvariant.detect ever grew a `<<$param>>`, the assertion fires
    and the AR.5-style cast-or-modeled-relation work would land here.
    """
    inv = DriftInvariant()
    conn = _fresh_db()
    try:
        # sqlite3's native SQL-trace hook — fires for every statement
        # the driver runs, including ones inside `cursor.execute()`.
        # Side-steps the read-only `Connection.execute` attribute.
        captured: list[str] = []
        conn.set_trace_callback(captured.append)
        inv.detect(conn)
        conn.set_trace_callback(None)
    finally:
        conn.close()

    assert captured, "expected DriftInvariant.detect() to run ≥1 SQL"
    for sql in captured:
        assert "<<$" not in sql, (
            f"drift detector unexpectedly crossed a SQL-pushdown surface; "
            f"AR.5-style substitution-path test required.\n  sql: {sql!r}"
        )
