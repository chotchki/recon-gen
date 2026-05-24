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
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import (
    day_bounds,
    find_internal_with_role,
    insert_balance,
    insert_tx,
    load_spec_example,
    to_date,
    ts,
)
from recon_gen.common.spine.rng import scenario_rng
from recon_gen.common.spine.violation import RuleViolation, Violation


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
            RuleViolation.of(
                "drift",
                account_id=aid,
                business_day=to_date(bds),
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
        child_account_id: str | None = None,
        parent_account_id: str | None = None,
    ) -> "DriftGenerator":
        """Resolve the role against the shape and return a generator
        that manufactures a drift breach on a leaf account of that role.

        `instance=None` loads the bundled `spec_example`; AS.x callers
        thread the real instance.

        AY.4.c — `child_account_id` / `parent_account_id` override the
        default synthetic IDs. The plant adapter (AY.4.c.3) threads
        OLD `DriftPlant.account_id` through these kwargs so N drift
        plants on the same role produce N distinct generators (the
        default `f"acct-drift-child-{role}"` derivation would collide).
        Existing test callers can pass nothing → preserves the synthetic
        defaults byte-stable.
        """
        inst = instance if instance is not None else load_spec_example()
        child = find_internal_with_role(
            inst, role, must_be_leaf=True, error_kind="drift",
        )
        parent = _find_internal_with_role_or_none(
            inst, str(getattr(child, "parent_role")),
        )
        return DriftGenerator(
            child_account_id=(
                child_account_id or f"acct-drift-child-{role}"
            ),
            child_role=role,
            parent_role=str(getattr(child, "parent_role")),
            parent_account_id=(
                parent_account_id
                if parent_account_id is not None
                else f"acct-drift-parent-{getattr(parent, 'role', 'unknown')}"
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
            RuleViolation.of(
                "ledger_drift",
                account_id=aid,
                business_day=to_date(bds),
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
    # AY.4.d — production callers thread cfg.db_table_prefix here.
    prefix: str = "spec_example"

    @property
    def intended(self) -> RuleViolation:
        return RuleViolation.of(
            "drift",
            account_id=self.child_account_id,
            business_day=self.anchor_day,
            drift=round(self.magnitude, 2),
        )

    @property
    def also_trips_ledger_drift(self) -> RuleViolation | None:
        """The secondary edge: when this generator's child-drift
        propagates up to the parent's `_ledger_drift`. `None` when no
        parent account is present in the shape (the L2 instance has no
        account with the child's `parent_role`)."""
        if self.parent_account_id is None:
            return None
        return RuleViolation.of(
            "ledger_drift",
            account_id=self.parent_account_id,
            business_day=self.anchor_day,
            # Sign: child stored is `leg + magnitude`, parent computed
            # sums children, so parent.stored − parent.computed =
            # `leg − (leg + magnitude)` = −magnitude.
            drift=round(-self.magnitude, 2),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """The child account_id this plant drifts + the parent
        account_id when the shape has one (ledger_drift edge fires on
        the parent). AV.5."""
        out = {self.child_account_id}
        if self.parent_account_id is not None:
            out.add(self.parent_account_id)
        return frozenset(out)

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        metadata = (
            scenario_metadata(scenario_id, generator="DriftGenerator")
            if scenario_id is not None else None
        )
        start, end = day_bounds(self.anchor_day)
        # Child: clean balance == leg total. Stored is shifted by
        # `magnitude` → drift fires on the child.
        insert_balance(
            conn,
            prefix=self.prefix,
            account_id=self.child_account_id,
            account_name=f"Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=self.parent_role,
            business_day_start=start,
            business_day_end=end,
            money=self.leg_amount + self.magnitude,
            metadata=metadata,
        )
        insert_tx(
            conn,
            prefix=self.prefix,
            id=f"tx-drift-{self.child_role}-{self.child_account_id}-1",
            account_id=self.child_account_id,
            account_name=f"Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=self.parent_role,
            amount_money=self.leg_amount,
            amount_direction="Credit",
            status="Posted",
            posting=ts(self.anchor_day),
            transfer_id=f"xfer-drift-{self.child_role}-{self.child_account_id}-1",
            rail_name="ach",
            origin="etl",
            metadata=metadata,
        )
        # Parent (when present in the shape): stored money equals the
        # CLEAN child leg total. With the child's stored inflated by
        # `magnitude`, the parent's computed (Σ child.money) is off by
        # `magnitude` too → ledger_drift fires on the parent.
        if self.parent_account_id is not None and self.parent_account_role is not None:
            insert_balance(
                conn,
                prefix=self.prefix,
                account_id=self.parent_account_id,
                account_name=f"Drift Parent ({self.parent_account_role})",
                account_role=self.parent_account_role,
                account_scope="internal",
                account_parent_role=None,
                business_day_start=start,
                business_day_end=end,
                money=self.leg_amount,
                metadata=metadata,
            )


# ---------------------------------------------------------------------------
# Drift-specific finder — kept here because it returns None rather than
# raising. The shared `find_internal_with_role` raises on "not found";
# drift needs "find the parent IF it exists, otherwise the parent edge
# is just inactive" — different semantics.
# ---------------------------------------------------------------------------


def _find_internal_with_role_or_none(
    instance: L2Instance, role: str,
) -> object | None:
    """Return the first internal account with the requested role,
    irrespective of leaf/parent status. None if no such account —
    drift's parent account is OPTIONAL (the ledger_drift edge is
    inactive when the shape has no account at the child's parent_role)."""
    for a in instance.accounts:
        if a.role == role and a.scope == "internal":
            return a
    return None
