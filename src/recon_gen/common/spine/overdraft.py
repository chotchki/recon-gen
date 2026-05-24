"""Overdraft family — concrete `Invariant` + `ViolationGenerator` impls.

`OverdraftInvariant` fires when an internal account's stored balance
goes negative. The matview is a one-line filter on
``<prefix>_current_daily_balances`` — no leg arithmetic, no parent
dependency, no role join. Structurally the simplest L1 invariant after
drift.

The AU.0 spike (``tests/unit/test_au0_overdraft_full_spine.py``) caught
a real finding: an overdraft planted on a LEAF internal account ALSO
trips `DriftInvariant`. Mechanism — drift's matview filter is
``parent_role IS NOT NULL`` AND ``stored ≠ Σ posted legs``. The
overdraft plant satisfies both (the leaf has a parent_role; the plant
emits stored=−magnitude with ZERO transactions, so Σ legs = 0 ≠
−magnitude). The edge falls out of overlapping base-table predicates
between two independent matview SELECTs — it's not drift-specific
exotica.

So AU.1's `INVARIANT_GENERATOR_EDGES` entry for `OverdraftGenerator` is
``(OverdraftInvariant, DriftInvariant)``: two edges, same shape as
drift's `(DriftInvariant, LedgerDriftInvariant)`.

What this module deliberately does NOT carry:

- An `rng` field on `OverdraftGenerator`. Overdraft's emission is fully
  determined by construction params (one balance row, magnitude scalar);
  no randomization surface. Drift accepts `rng` for structural
  uniformity across the spine; overdraft has no use for it. AT's anomaly
  generator will actually use the RNG.
- A stateful day-by-day fold. Overdraft is a single-row witness; no
  carried state across days; the `AccountSimulation` AS.3 base class is
  for invariants with running balance.
- Cross-account composition (AS.4's `LedgerSimulation`). Overdraft is
  per-account; AU.2's composition test wires it into a LedgerSimulation
  alongside DriftGenerator for the spine-scales-past-one-invariant gate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import (
    day_bounds,
    find_internal_with_role,
    insert_balance,
    load_spec_example,
    to_date,
)
from recon_gen.common.spine.violation import RuleViolation, Violation


@dataclass(frozen=True)
class OverdraftInvariant:
    """Non-negative-stored-balance detector. Persona-blind — the matview
    SQL is `WHERE money < 0` on every internal account, no role join.
    `scenario_for(role)` filters the L2 by role only; ANY scope=internal
    account qualifies (no parent_role requirement that drift carries)."""

    # `name` is class-level — matches the production matview suffix.
    # ClassVar keeps it out of the dataclass field set so the Invariant
    # Protocol's read-only `name` attribute is satisfied without variance
    # fuss. Mirrors `DriftInvariant`'s shape.
    name: ClassVar[str] = "overdraft"
    #: Prefix of the deployed L2 instance's matviews. Same default +
    #: per-call override pattern drift uses.
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, business_day_start, stored_balance "
            f"FROM {self.prefix}_overdraft",
        ).fetchall()
        return {
            RuleViolation.of(
                "overdraft",
                account_id=aid,
                business_day=to_date(bds),
                stored_balance=round(float(sb), 2),
            )
            for aid, bds, sb in rows
        }

    def scenario_for(
        self,
        role: str,
        *,
        magnitude: float = 5.0,
        instance: L2Instance | None = None,
        account_id: str | None = None,
    ) -> "OverdraftGenerator":
        """Resolve a role against the shape; return a generator that
        manufactures a stored-balance overdraft on the first internal
        account with that role.

        `magnitude` is caller-facing ("how far below zero the planted
        stored is" — positive). `magnitude=0.0` plants stored=0 which is
        NOT < 0, so overdraft does NOT fire — AP.2's non-violating
        convention promoted to overdraft.

        Raises `ValueError` if the L2 has no internal account with the
        requested role. Smart-constructor discipline matching drift's:
        the invariant owns shape resolution, fails loud at the request
        site, never silently emits inert rows.

        `instance=None` loads the bundled `spec_example` — production
        callers (deploy-time, e2e fixtures) thread the real L2.

        AY.4.c — `account_id` overrides the default synthetic ID. The
        plant adapter (AY.4.c.3) threads OLD `OverdraftPlant.account_id`
        through this kwarg so N overdraft plants on the same role
        produce N distinct generators (the default
        `f"acct-overdraft-{role}"` derivation would collide). Existing
        test callers can pass nothing → preserves the synthetic default
        byte-stable.
        """
        inst = instance if instance is not None else load_spec_example()
        acct = find_internal_with_role(inst, role, error_kind="overdraft")
        return OverdraftGenerator(
            account_id=account_id or f"acct-overdraft-{role}",
            account_role=role,
            account_parent_role=acct.parent_role,
            anchor_day=date(2030, 1, 1),
            magnitude=magnitude,
        )


@dataclass
class OverdraftGenerator:
    """Emit a daily_balances row whose `money` is below zero by
    `magnitude`. NO transactions — overdraft's matview reads
    `current_daily_balances` directly; only the balance row is needed.

    Per the AP.2 convention: `magnitude=0.0` means the perturbation is
    OFF; the emitted row has money=0, which is NOT < 0, so overdraft
    does NOT fire. The non-violating shape is the same generator with
    the knob off.

    AU.0 finding: on a LEAF internal account (account_parent_role !=
    None), this emission ALSO trips `DriftInvariant` because drift's
    matview filter `parent_role IS NOT NULL AND stored ≠ Σ legs` is
    satisfied (no transactions emitted ⇒ Σ legs = 0 ≠ −magnitude). The
    registry records the two-edge entry.
    """

    account_id: str
    account_role: str
    account_parent_role: str | None
    anchor_day: date
    magnitude: float
    # AY.4.d — production callers thread cfg.db_table_prefix here so
    # the emitted row lands on the right deployment's table; default
    # matches the in-process test harness shape.
    prefix: str = "spec_example"

    @property
    def intended(self) -> RuleViolation:
        # `stored_balance` is the actual matview value (negative).
        # `magnitude` is caller-facing positive; the identity carries the
        # negative form so it round-trips against `detect()`.
        return RuleViolation.of(
            "overdraft",
            account_id=self.account_id,
            business_day=self.anchor_day,
            stored_balance=round(-self.magnitude, 2),
        )

    @property
    def also_trips_drift(self) -> RuleViolation | None:
        """The empirical AU.0 edge: drift fires on the same account/day
        when the planted account is a LEAF (account_parent_role is set).
        Returns `None` when the planted account is NOT a leaf (drift's
        `parent_role IS NOT NULL` filter excludes it).

        Magnitude sign: drift = stored − Σ legs = −magnitude − 0 =
        −magnitude.
        """
        if self.account_parent_role is None:
            return None
        return RuleViolation.of(
            "drift",
            account_id=self.account_id,
            business_day=self.anchor_day,
            drift=round(-self.magnitude, 2),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """The single account_id this plant overdrafts. AV.5."""
        return frozenset({self.account_id})

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        metadata = (
            scenario_metadata(scenario_id, generator="OverdraftGenerator")
            if scenario_id is not None else None
        )
        start, end = day_bounds(self.anchor_day)
        insert_balance(
            conn,
            prefix=self.prefix,
            account_id=self.account_id,
            account_name=f"Overdraft Acct ({self.account_role})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=self.account_parent_role,
            business_day_start=start,
            business_day_end=end,
            money=-self.magnitude,
            metadata=metadata,
        )


# Phase AU.3.d (2026-05-23): local helpers hoisted to
# `common/spine/_emit_helpers.py`. Per-invariant-shape helpers (none for
# overdraft) would stay here.
