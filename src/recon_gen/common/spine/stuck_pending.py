"""Stuck-Pending family — first transaction-based + instance-coupled
spine invariant.

`StuckPendingInvariant` fires when a Pending transaction's posting age
exceeds the rail's configured `max_pending_age`. This is the first
spine invariant that:

1. **Reads transactions, not balances** — every prior promotion
   (DriftGenerator, OverdraftGenerator, ExpectedEodBalanceGenerator)
   emits daily_balances rows. StuckPendingGenerator emits a Pending
   transaction with no balance row.
2. **Couples to the L2 instance shape** — `scenario_for(rail_name)`
   reads the rail's `max_pending_age` from L2 to plant a transaction
   whose age overshoots it. The shape-resolution discipline AS.2's
   `DriftInvariant.scenario_for(role)` started extends here: the
   invariant owns L2 resolution, fails loud at the request site.
3. **Uses wall-clock time** — the matview computes `age_seconds =
   CURRENT_TIMESTAMP - posting`. The plant computes posting via
   `datetime.now() - (max_pending_age + overshoot)` so that at refresh
   time, age_seconds > max_pending_age_seconds → fires. Identity is
   `(transaction_id, rail_name)` — stable across refreshes; age value
   itself is NOT in the identity (it drifts with wall-clock).

Per AU.0/AU.2 lessons: the empirical edge check is mandatory. Prediction
(written before the test): stuck_pending trips ONLY itself. Pending
transactions don't contribute to drift's computed_subledger_balance
(filters status='Posted'); no balance row emitted ⇒ no overdraft, no
expected_eod, no ledger_drift, no drift. Single-edge registry entry
expected. The test verifies empirically — if it surfaces an unexpected
edge, the AU.x cadence's stop-and-evaluate catches it.

`overshoot_seconds=0` is the non-violating shape (age == threshold;
matview's `>` filter excludes). Same AP.2 convention adapted to the
seconds-unit knob.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance, SingleLegRail, TwoLegRail
from recon_gen.common.spine.violation import Violation

# Either rail subtype is acceptable — both carry `max_pending_age`.
_RailWithPendingAge = TwoLegRail | SingleLegRail


@dataclass(frozen=True)
class StuckPendingInvariant:
    """Stuck-Pending detector. The matview gates on
    ``status = 'Pending'`` AND ``age_seconds > max_pending_age_seconds``
    (per-rail cap from L2). Identity is `(transaction_id, rail_name)` —
    `transaction_id` alone is PK-unique, but `rail_name` rounds out the
    analyst-facing identity for diff readability."""

    name: ClassVar[str] = "stuck_pending"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT transaction_id, rail_name "
            f"FROM {self.prefix}_stuck_pending",
        ).fetchall()
        return {
            Violation.of(
                "stuck_pending",
                transaction_id=str(tid),
                rail_name=str(rn),
            )
            for tid, rn in rows
        }

    def scenario_for(
        self,
        rail_name: str,
        *,
        overshoot_seconds: int = 60,
        account_role: str = "CustomerSubledger",
        instance: L2Instance | None = None,
    ) -> "StuckPendingGenerator":
        """Resolve `rail_name` against the shape; return a generator
        that plants a Pending transaction on a `account_role` account
        with `posting = now() − (rail.max_pending_age + overshoot)`.

        `overshoot_seconds=0` ⇒ age == threshold ⇒ matview's `>` filter
        excludes ⇒ no fire (AP.2 non-violating convention adapted).
        Positive overshoot fires loud; negative also non-violating
        (age < threshold).

        Raises `ValueError` if the L2 has no rail with `rail_name`,
        OR if that rail has no `max_pending_age` set (stuck_pending's
        matview filter excludes rails without one — manufacturing a
        scenario against an uncovered rail would silently emit an inert
        row, which we refuse).
        """
        inst = instance if instance is not None else _spec_example()
        rail = _find_rail_with_max_pending_age(inst, rail_name)
        # `_find_rail_with_max_pending_age` raises if `max_pending_age`
        # is None — pyright doesn't narrow the union member, so help it.
        assert rail.max_pending_age is not None
        acct = _find_internal_with_role(inst, account_role)
        return StuckPendingGenerator(
            transaction_id=f"tx-stuck-pending-{rail_name}",
            transfer_id=f"xfer-stuck-pending-{rail_name}",
            rail_name=rail_name,
            account_id=f"acct-stuck-pending-{rail_name}",
            account_role=account_role,
            account_parent_role=(
                str(getattr(acct, "parent_role"))
                if getattr(acct, "parent_role", None) is not None
                else None
            ),
            max_pending_age_seconds=int(rail.max_pending_age.total_seconds()),
            overshoot_seconds=overshoot_seconds,
        )


@dataclass
class StuckPendingGenerator:
    """Emit a single Pending transaction whose `posting` is in the past
    by `max_pending_age_seconds + overshoot_seconds`. NO balance row,
    NO related Posted transactions — the stuck_pending matview reads
    only the transactions table, so the plant is single-row.

    Wall-clock time interaction: `now()` is captured at `emit()` time;
    the matview's `age_seconds` is computed at refresh time (slightly
    later). For positive `overshoot_seconds` the gap is comfortably
    over the cap; identity stays stable since `transaction_id` doesn't
    depend on wall-clock.
    """

    transaction_id: str
    transfer_id: str
    rail_name: str
    account_id: str
    account_role: str
    account_parent_role: str | None
    max_pending_age_seconds: int
    overshoot_seconds: int

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "stuck_pending",
            transaction_id=self.transaction_id,
            rail_name=self.rail_name,
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        # Plant `posting` far enough in the past that the matview's
        # `age_seconds > max_pending_age_seconds` filter fires. Use a
        # Credit posting (sign-direction CHECK constraint requires
        # money>=0 for Credit; arbitrary positive value).
        #
        # TIMEZONE: uses naive LOCAL `datetime.now()` to match the rest
        # of the application's local-TZ convention (Oracle's TIMESTAMP
        # lacks proper TZ-WITH-TIME-ZONE semantics; the codebase treats
        # all stored timestamps as bare wall-clock interpreted in the
        # DB's own TZ). Tests on SQLite (whose CURRENT_TIMESTAMP returns
        # UTC) will see an inflated `age_seconds` by the system's TZ
        # offset — callers MUST pick `overshoot_seconds` with enough
        # margin to absorb ±12h of TZ skew (`tests/unit/test_spine_
        # stuck_pending.py` does so).
        age_back = self.max_pending_age_seconds + self.overshoot_seconds
        posting_dt = datetime.now() - timedelta(seconds=age_back)
        _insert_tx(
            conn,
            id=self.transaction_id,
            account_id=self.account_id,
            account_name=f"Stuck Pending ({self.rail_name})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=self.account_parent_role,
            amount_money=100.0,
            amount_direction="Credit",
            status="Pending",
            posting=posting_dt.strftime("%Y-%m-%d %H:%M:%S"),
            transfer_id=self.transfer_id,
            rail_name=self.rail_name,
            origin="etl",
        )


# ---------------------------------------------------------------------------
# Helpers — module-private (same discipline as drift.py / overdraft.py /
# expected_eod.py). AU.3.d (queued) hoists once all three AU.3 invariants
# land and the 4-copy duplication justifies the refactor.
# ---------------------------------------------------------------------------


def _spec_example() -> L2Instance:
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[4]
    return load_instance(repo_root / "tests" / "l2" / "spec_example.yaml")


def _find_rail_with_max_pending_age(
    instance: L2Instance, rail_name: str,
) -> _RailWithPendingAge:
    """Return the L2 rail with the given `name` AND a non-None
    `max_pending_age`. Raises ValueError if either condition fails —
    the matview excludes rails without `max_pending_age` (`pending_age_
    cases` → NULL → outer WHERE filters), so a scenario against an
    uncovered rail would silently inert; we refuse instead.

    Returns the concrete rail type (TwoLegRail | SingleLegRail) so the
    caller's `.max_pending_age.total_seconds()` typechecks without
    runtime introspection."""
    for r in instance.rails:
        if r.name == rail_name:
            if r.max_pending_age is None:
                raise ValueError(
                    f"rail {rail_name!r} has no max_pending_age set; "
                    f"stuck_pending's matview excludes it. Cannot "
                    f"manufacture a stuck_pending scenario against this rail."
                )
            return r
    raise ValueError(
        f"shape has no rail named {rail_name!r}; cannot manufacture "
        f"a stuck_pending scenario"
    )


def _find_internal_with_role(instance: L2Instance, role: str) -> object:
    for a in instance.accounts:
        if (
            getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
        ):
            return a
    raise ValueError(
        f"shape has no internal account with role {role!r}"
    )


_TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name", "origin",
)


def _insert_tx(conn: sqlite3.Connection, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _TX_COLS)
    table = f"{_PREFIX}_transactions"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _TX_COLS],
    )


_PREFIX = "spec_example"
