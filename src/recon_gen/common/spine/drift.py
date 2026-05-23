"""Drift family — concrete `Invariant` + `ViolationGenerator` impls.

Two L1 invariants share one generator (the many-to-many edge that
motivates the registry):

- `DriftInvariant` — sub-ledger drift. Fires when a LEAF internal
  account's stored balance ≠ Σ posted legs at business_day_end.
  Reads from `<prefix>_drift`.
- `LedgerDriftInvariant` — parent (ledger) drift. Fires when a PARENT
  internal account's stored balance ≠ Σ(child stored balances) +
  Σ(direct postings). Reads from `<prefix>_ledger_drift`.

`DriftGenerator` emits a child account with stored money OFF by
`magnitude` from its leg-total, AND a parent account with stored money
equal to the *clean* leg-total. So the child drifts (stored−computed =
magnitude) AND the parent drifts (parent.stored − Σ child.money is
also off, by ‑magnitude). One emission, two detectors fire — that's
the spine's many-to-many edge.

Per the AS.1 RNG convention: `scenario_for` accepts `seed`; the
generator carries `rng: random.Random`. Drift's emission itself is
deterministic by construction (one account, one day, one leg) — the
RNG hook is there for the convention's structural uniformity, not
because drift needs choice. Anomaly (AT.2) WILL use it.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import ClassVar

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine.rng import scenario_rng
from recon_gen.common.spine.violation import Violation


@dataclass(frozen=True)
class DriftInvariant:
    """Sub-ledger drift detector. Persona-blind (no L2 join in the
    matview SQL), so `scenario_for(role)` resolves against any leaf
    internal account with that role."""

    # `name` is class-level (matches the production matview suffix);
    # ClassVar keeps it out of the dataclass field set so the Invariant
    # Protocol's @property satisfies-check passes without variance fuss.
    name: ClassVar[str] = "drift"
    #: Prefix of the deployed L2 instance's matviews. Concrete invariants
    #: carry this so `detect()` can read the right matview; AS.1's
    #: Protocol stayed minimal (no prefix field) since not every
    #: invariant variant needs one.
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, business_day_start, drift "
            f"FROM {self.prefix}_drift",
        ).fetchall()
        return {
            Violation.of(
                "drift",
                account_id=aid,
                business_day=_to_date(bds),
                drift=round(float(d), 2),
            )
            for aid, bds, d in rows
        }

    def scenario_for(
        self,
        role: str,
        *,
        magnitude: float = 5.0,
        seed: int | None = None,
        instance: L2Instance | None = None,
    ) -> "DriftGenerator":
        """Resolve the role against the shape and return a generator
        that manufactures a drift breach on a leaf account of that role.

        `instance=None` loads the bundled `spec_example`; AS.x callers
        thread the real instance.
        """
        inst = instance if instance is not None else _spec_example()
        child = _find_leaf_internal_with_role(inst, role)
        parent = _find_internal_with_role(
            inst, str(getattr(child, "parent_role")),
        )
        return DriftGenerator(
            child_account_id=f"acct-drift-child-{role}",
            child_role=role,
            parent_role=str(getattr(child, "parent_role")),
            parent_account_id=(
                f"acct-drift-parent-{getattr(parent, 'role', 'unknown')}"
                if parent is not None
                else None
            ),
            parent_account_role=(
                str(getattr(parent, "role"))
                if parent is not None
                else None
            ),
            anchor_day=date(2030, 1, 1),
            magnitude=magnitude,
            rng=scenario_rng(seed),
        )


@dataclass(frozen=True)
class LedgerDriftInvariant:
    """Parent-ledger drift detector. Fires when a parent's stored money
    ≠ Σ(child stored) + Σ(direct legs). DriftGenerator's child-drift
    causes Σ(child stored) to shift — so this fires on the parent too."""

    name: ClassVar[str] = "ledger_drift"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, business_day_start, drift "
            f"FROM {self.prefix}_ledger_drift",
        ).fetchall()
        return {
            Violation.of(
                "ledger_drift",
                account_id=aid,
                business_day=_to_date(bds),
                drift=round(float(d), 2),
            )
            for aid, bds, d in rows
        }


@dataclass
class DriftGenerator:
    """Emit a child account whose stored money drifts from its leg-total
    by `magnitude`, plus a parent account whose stored money equals the
    CLEAN leg-total. Result: child drifts (stored−computed = magnitude)
    AND parent drifts (parent.stored − Σ child.money = −magnitude).

    The pre-AS.3 simple shape: one day, one account pair, one leg. AS.3
    promotes this to a stateful day-by-day fold; AS.4 generalizes to
    cross-account vector state.
    """

    child_account_id: str
    child_role: str
    parent_role: str
    parent_account_id: str | None
    parent_account_role: str | None
    anchor_day: date
    magnitude: float
    rng: random.Random = field(default_factory=scenario_rng)
    #: Clean leg amount; the child's stored money is this + magnitude.
    leg_amount: float = 100.0

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "drift",
            account_id=self.child_account_id,
            business_day=self.anchor_day,
            drift=round(self.magnitude, 2),
        )

    @property
    def also_trips_ledger_drift(self) -> Violation | None:
        """The secondary edge: when this generator's child-drift
        propagates up to the parent's `_ledger_drift`. `None` when no
        parent account is present in the shape (the L2 instance has no
        account with the child's `parent_role`)."""
        if self.parent_account_id is None:
            return None
        return Violation.of(
            "ledger_drift",
            account_id=self.parent_account_id,
            business_day=self.anchor_day,
            # Sign: child stored is `leg + magnitude`, parent computed
            # sums children, so parent.stored − parent.computed =
            # `leg − (leg + magnitude)` = −magnitude.
            drift=round(-self.magnitude, 2),
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        start, end = _day_bounds(self.anchor_day)
        # Child: clean balance == leg total. Stored is shifted by
        # `magnitude` → drift fires on the child.
        _insert_balance(
            conn,
            account_id=self.child_account_id,
            account_name=f"Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=self.parent_role,
            business_day_start=start,
            business_day_end=end,
            money=self.leg_amount + self.magnitude,
        )
        _insert_tx(
            conn,
            id=f"tx-drift-{self.child_role}-1",
            account_id=self.child_account_id,
            account_name=f"Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=self.parent_role,
            amount_money=self.leg_amount,
            amount_direction="Credit",
            status="Posted",
            posting=_ts(self.anchor_day),
            transfer_id=f"xfer-drift-{self.child_role}-1",
            rail_name="ach",
            origin="etl",
        )
        # Parent (when present in the shape): stored money equals the
        # CLEAN child leg total. With the child's stored inflated by
        # `magnitude`, the parent's computed (Σ child.money) is off by
        # `magnitude` too → ledger_drift fires on the parent.
        if self.parent_account_id is not None and self.parent_account_role is not None:
            _insert_balance(
                conn,
                account_id=self.parent_account_id,
                account_name=f"Drift Parent ({self.parent_account_role})",
                account_role=self.parent_account_role,
                account_scope="internal",
                account_parent_role=None,
                business_day_start=start,
                business_day_end=end,
                money=self.leg_amount,
            )


# ---------------------------------------------------------------------------
# Helpers — kept module-private so AS.2 stays the only owner of the drift
# emission shape. AS.3's stateful-fold base will absorb most of these.
# ---------------------------------------------------------------------------


def _spec_example() -> L2Instance:
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[4]
    return load_instance(repo_root / "tests" / "l2" / "spec_example.yaml")


def _find_leaf_internal_with_role(instance: L2Instance, role: str) -> object:
    """Return the first leaf internal account (scope=internal,
    parent_role IS NOT NULL) with the requested role."""
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
    return candidates[0]


def _find_internal_with_role(
    instance: L2Instance, role: str,
) -> object | None:
    """Return the first internal account with the requested role,
    irrespective of leaf/parent status. None if no such account."""
    for a in instance.accounts:
        if (
            getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
        ):
            return a
    return None


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
    # AS.3 hoists this into the shared stateful-fold base. AS.2 keeps a
    # local copy so the spine module is self-contained on day-1.
    placeholders = ", ".join("?" for _ in _TX_COLS)
    table = f"{_PREFIX}_transactions"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _TX_COLS],
    )


def _insert_balance(conn: sqlite3.Connection, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _DB_COLS)
    table = f"{_PREFIX}_daily_balances"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(_DB_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _DB_COLS],
    )


_PREFIX = "spec_example"


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


def _to_date(bds: object) -> date:
    return datetime.strptime(str(bds)[:10], "%Y-%m-%d").date()
