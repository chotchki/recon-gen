"""`AccountSimulation` — the AP.2 stateful-fold pattern, productionized.

A leaf internal account stepped forward day by day as a pure fold:
`State -> (flows, State')`. Each day's emitted stored balance IS the
running ``State'`` (Σ recorded legs so far). Concrete
`ViolationGenerator` impls compose `AccountSimulation` when they need
multi-day choices; the simple `DriftGenerator` (AS.2, single-day)
stays unchanged.

What AP.2 proved + this module locks in:

- The fold is PURE (`_fold()` returns per-day emissions; `emit(conn)`
  writes them). Separating the two lets the SAME fold drive an
  all-at-once write OR a day-by-day write with detect-between (the
  `violation_trajectory` carries the violation set as state).
- Non-violating = the same simulation with ``perturbations=()``.
  Conformance is flow/state AGREEMENT, not the absence of activity
  (AP.2 finding Q2).
- Propagation is governed by which side you break, predictable from
  the detector SQL: state-snapshot blip is LOCAL; unrecorded-flow
  PROPAGATES forward. Pinned by AP.2 + reusable here.
- Generators STACK: pass ``perturbations=(p1, p2, ...)``; each adds
  its own violation to the carried set; existing ones persist.

AS.4 generalizes the State from a scalar balance to a vector
`dict[account_id, balance]` (cross-account legs net to zero across
accounts). AS.3 is the scalar foundation.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.rng import scenario_rng
from recon_gen.common.spine.violation import Violation


_PerturbKind = Literal["none", "state_blip", "unrecorded_leg", "recorded_leg"]


@dataclass(frozen=True)
class DayPlan:
    """One day's intended activity — signed leg amounts. A clean fold
    sets ``State'.balance = State.balance + Σ legs`` and stores it."""

    day: date
    legs: tuple[float, ...]


@dataclass(frozen=True)
class Perturbation:
    """How a single day's step deviates from the clean fold. The knob
    AP.2 surfaced — one shape, three kinds + a correction.

    - ``"none"``: clean — never emits anything on this day (no-op).
    - ``"state_blip"``: corrupt ONLY the stored snapshot on
      ``day_index`` by ``amount`` (the running balance stays clean →
      drift is LOCAL to that day).
    - ``"unrecorded_leg"``: emit an extra leg on ``day_index`` that is
      NOT folded into stored (flow/state DISAGREEMENT → drift
      PROPAGATES forward; computed is cumulative, stored stayed on
      the clean fold). Optional ``correct_day_index`` books the leg
      into stored on a later day (the AN.1 supersession shape — closes
      the forward propagation; the historical breach remains).
    - ``"recorded_leg"``: emit an EXTRA real leg that DOES fold into
      state — a different-but-consistent history (conforming).
    """

    kind: _PerturbKind = "none"
    day_index: int = 0
    amount: float = 0.0
    correct_day_index: int | None = None


@dataclass(frozen=True)
class DayEmission:
    """The materialized result of one folded step: the legs to write
    and the stored balance that IS the running ``State'``. ``run``
    iterates these; ``violation_trajectory`` writes one at a time +
    refreshes + detects between."""

    day: date
    legs: tuple[tuple[str, float], ...]  # (tag, signed amount)
    stored: float


@dataclass
class AccountSimulation:
    """A leaf internal account stepped forward day by day.

    Authoring shape mirrors the AP.2 spike: ``plans`` declare per-day
    flows, ``perturbations`` declare the AP.2 perturbation knobs. The
    fold is PURE (``_fold()`` is side-effect free); ``run(conn)``
    writes the rows; ``violation_trajectory(inv, conn)`` carries the
    violation set as state day by day.

    Per the AS.1 RNG convention: every concrete generator that
    composes AccountSimulation passes an ``rng`` (seeded via
    `scenario_rng`); AS.3 itself doesn't randomize anything (the legs
    are author-declared), but the field carries forward for compose-
    time choices in AT's anomaly/money_trail.
    """

    plans: list[DayPlan]
    perturbations: list[Perturbation] = field(default_factory=list[Perturbation])
    account_id: str = "acct-sim"
    account_role: str = "CustomerSubledger"
    parent_role: str = "CustomerLedger"
    opening_balance: float = 0.0
    prefix: str = "spec_example"
    rng: random.Random = field(default_factory=scenario_rng)
    #: AS.4 — when False, the fold still computes per-day stored via
    #: `Σ legs`, but does NOT insert leg rows into `_transactions`.
    #: Right for parent-style ledger accounts that MIRROR the
    #: children's cumulative balance without owning direct legs of
    #: their own. The ledger_drift matview's per-day direct_totals
    #: then stays zero on this account, so the clean fold's
    #: `parent.stored = Σ child.stored` agreement gives drift = 0.
    #: Default True keeps every existing leaf-style use site unchanged.
    emit_legs: bool = True

    # ---- The pure fold (no IO) -------------------------------------------

    def _fold(self) -> list[DayEmission]:
        """`State -> (flows, State')` over days. No IO. Stacks every
        perturbation on its `day_index`. State-blip corrupts the
        stored snapshot only; unrecorded_leg emits a flow without
        folding it into balance (propagates); recorded_leg folds in
        (conforming); correction at `correct_day_index` books a prior
        unrecorded leg's amount into balance (closes forward drift)."""
        balance = self.opening_balance
        out: list[DayEmission] = []
        for i, plan in enumerate(self.plans):
            legs: list[tuple[str, float]] = [
                (f"d{i}-{j}", amt) for j, amt in enumerate(plan.legs)
            ]
            balance += sum(plan.legs)

            blip_total = 0.0
            for k, p in enumerate(self.perturbations):
                if p.day_index == i and p.kind == "recorded_leg":
                    legs.append((f"d{i}-extra{k}", p.amount))
                    balance += p.amount
                if p.day_index == i and p.kind == "unrecorded_leg":
                    legs.append((f"d{i}-stray{k}", p.amount))
                    # balance NOT updated — the propagation knob.
                if p.correct_day_index == i and p.kind == "unrecorded_leg":
                    # Book the missing leg now → forward propagation
                    # stops here; the historical breach (days before
                    # the correction) remains.
                    balance += p.amount
                if p.day_index == i and p.kind == "state_blip":
                    blip_total += p.amount

            out.append(DayEmission(plan.day, tuple(legs), balance + blip_total))
        return out

    # ---- IO (run + trajectory) -------------------------------------------

    def emit(self, conn: sqlite3.Connection) -> None:
        """Write the full fold to the connection in one pass."""
        for em in self._fold():
            self._emit_day(conn, em)

    def violation_trajectory(
        self, invariant: Invariant, conn: sqlite3.Connection,
    ) -> list[set[Violation]]:
        """Run the fold day by day, refresh + detect after each day,
        return the per-day violation set. The carried-state shape
        from AP.2: each snapshot is the active violations as the
        institution reaches that day. The delta between consecutive
        snapshots IS each step's effect (opened / closed / inert)."""
        from recon_gen.common.l2.loader import load_instance
        from recon_gen.common.l2.schema import refresh_matviews_sql
        from recon_gen.common.sql import Dialect
        from recon_gen.common.db import execute_script

        # Caller passes a connection already configured with the
        # schema + L2 instance; we read the matview-refresh SQL for
        # that instance from `load_instance(spec_example)` for now
        # (AT's AccountSimulation users will pass an explicit instance
        # path; AS.3 keeps the spec_example default for parity with
        # the existing AP.x spike pattern).
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[4]
        instance = load_instance(
            repo_root / "tests" / "l2" / "spec_example.yaml",
        )

        snapshots: list[set[Violation]] = []
        for em in self._fold():
            self._emit_day(conn, em)
            conn.commit()
            cur = conn.cursor()
            execute_script(
                cur,
                refresh_matviews_sql(
                    instance, prefix=self.prefix, dialect=Dialect.SQLITE,
                ),
                dialect=Dialect.SQLITE,
            )
            conn.commit()
            snapshots.append(invariant.detect(conn))
        return snapshots

    # ---- Per-day write — shared by emit + violation_trajectory ----------

    def _emit_day(
        self, conn: sqlite3.Connection, em: DayEmission,
    ) -> None:
        if self.emit_legs:
            for tag, amount in em.legs:
                direction = "Credit" if amount >= 0 else "Debit"
                _insert_tx(
                    conn, prefix=self.prefix,
                    id=f"tx-{self.account_id}-{tag}",
                    account_id=self.account_id,
                    account_name=f"Sim Acct ({self.account_role})",
                    account_role=self.account_role,
                    account_scope="internal",
                    account_parent_role=self.parent_role,
                    amount_money=amount, amount_direction=direction,
                    status="Posted", posting=_ts(em.day),
                    transfer_id=f"xfer-{self.account_id}-{tag}",
                    rail_name="ach", origin="etl",
                )
        start, end = _day_bounds(em.day)
        _insert_balance(
            conn, prefix=self.prefix,
            account_id=self.account_id,
            account_name=f"Sim Acct ({self.account_role})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=self.parent_role,
            business_day_start=start, business_day_end=end,
            money=em.stored,
        )


# ---------------------------------------------------------------------------
# Insert helpers — module-private, parameterized on prefix. AS.4's
# cross-account version will reuse these by emitting per-account rows in
# a vector loop.
# ---------------------------------------------------------------------------


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


def _insert_tx(conn: sqlite3.Connection, *, prefix: str, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _TX_COLS)
    conn.execute(
        f"INSERT INTO {prefix}_transactions ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _TX_COLS],
    )


def _insert_balance(
    conn: sqlite3.Connection, *, prefix: str, **vals: object,
) -> None:
    placeholders = ", ".join("?" for _ in _DB_COLS)
    conn.execute(
        f"INSERT INTO {prefix}_daily_balances ({', '.join(_DB_COLS)}) "
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
