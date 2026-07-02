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

from recon_gen.common.db import SyncConnection
from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar

from recon_gen.common.l2.primitives import (
    ORIGIN_INTERNAL_INITIATED,
    POSTED_STATUS,
    L2Instance,
)
from recon_gen.common.money import Cents
from recon_gen.common.spine._db import fetch_all
from recon_gen.common.spine._emit_helpers import (
    day_bounds,
    find_internal_with_role,
    insert_balance,
    insert_tx,
    load_spec_example,
    posting_dt,
    to_date,
)
from recon_gen.common.spine.residuals import (
    BalanceRow,
    LegRow,
    ResidualState,
    drift_residual,
    overdraft_residual,
)
from recon_gen.common.spine.violation import (
    RuleViolation,
    Violation,
    identity_dollars,
)


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

    def detect(self, conn: SyncConnection) -> set[Violation]:
        rows = fetch_all(
            conn,
            f"SELECT account_id, business_day_start, stored_balance "
            f"FROM {self.prefix}_overdraft",
        )
        # AO.1: stored_balance is BIGINT cents — project to dollars at
        # the detect boundary so violation identities still round-trip
        # against generators that author in dollars.
        return {
            RuleViolation.of(
                "overdraft",
                account_id=aid,
                business_day=to_date(bds),
                stored_balance=round(
                    float(Cents.from_db(int(sb)).to_dollars()), 2,
                ),
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
    # AY.4.d — production callers thread cfg.db.table_prefix here so
    # the emitted row lands on the right deployment's table; default
    # matches the in-process test harness shape.
    prefix: str = "spec_example"
    # DL.3.7 — when set, emit a drilldown marker tx on EVERY business
    # day from ``anchor_day`` through ``as_of_day`` (inclusive). The
    # ``<prefix>_overdraft`` matview reads from ``effective_balances``'s
    # CL.5 carry-forward, so a single plant emits a visible overdraft row
    # on every day from its emit day forward (until the next emit / the
    # window end). The DL.3.1 single-marker-on-anchor-day pattern leaves
    # the drill destination empty on every carry day. Sweeping markers
    # across the carry window fixes that. ``None`` (default) preserves
    # the DL.3.1 single-marker behavior — that's what the unit tests use.
    as_of_day: date | None = None

    def _planned_balance(self) -> BalanceRow:
        """The one balance row this plant emits, projected into the
        residual domain — the SINGLE plan both ``emit()`` and the
        intended/edge properties read (DS.2: a separately-written
        expectation is the calibration-drift disease with a new name).
        Cents conversion mirrors the insert boundary's
        ``Cents.from_dollars(str(value))`` exactly."""
        return BalanceRow(
            account_id=self.account_id,
            entry=0,
            day=self.anchor_day,
            money=Cents.from_dollars(str(-self.magnitude)),
            account_role=self.account_role,
            account_parent_role=self.account_parent_role,
        )

    def _planned_markers(self) -> tuple[LegRow, ...]:
        """The DL.3.1/DL.3.7 zero-amount drilldown marker sweep — one
        LegRow per coverage day from ``anchor_day`` through ``as_of_day``
        (inclusive); ``as_of_day=None`` is the single-marker unit-test
        shape. ``emit()`` writes exactly these rows. Zero amount is
        load-bearing: it keeps the drift residual's Σ legs at exactly 0
        (see emit()'s rationale comment).

        Id shape per coverage day (DL.3.7): the windowed form carries
        BOTH the plant anchor day AND the coverage day so overlapping
        plants on one account never PK-collide; the ``as_of_day=None``
        form preserves the DL.3.1 id byte-identically (unit tests pin
        the ``tx-overdraft-marker-<role>-<account>-<day>`` prefix +
        ``count == 1``)."""
        anchor_slug = self.anchor_day.isoformat()
        end_day = self.as_of_day if self.as_of_day is not None else self.anchor_day
        # Guard against ``as_of_day < anchor_day`` (degenerate input —
        # the plant is later than the audit window's end). Treat as
        # single-day emit so we don't silently skip the marker.
        if end_day < self.anchor_day:
            end_day = self.anchor_day
        markers: list[LegRow] = []
        cursor = self.anchor_day
        while cursor <= end_day:
            coverage_slug = cursor.isoformat()
            if self.as_of_day is None:
                tx_id = (
                    f"tx-overdraft-marker-{self.account_role}-"
                    f"{self.account_id}-{coverage_slug}"
                )
                xfer_id = (
                    f"xfer-overdraft-marker-{self.account_role}-"
                    f"{self.account_id}-{coverage_slug}"
                )
            else:
                tx_id = (
                    f"tx-overdraft-marker-{self.account_role}-"
                    f"{self.account_id}-anchor-{anchor_slug}-"
                    f"day-{coverage_slug}"
                )
                xfer_id = (
                    f"xfer-overdraft-marker-{self.account_role}-"
                    f"{self.account_id}-anchor-{anchor_slug}-"
                    f"day-{coverage_slug}"
                )
            markers.append(LegRow(
                id=tx_id,
                entry=0,
                account_id=self.account_id,
                amount=Cents.from_dollars(str(0.0)),
                status=POSTED_STATUS,
                posting=posting_dt(cursor),
                transfer_id=xfer_id,
                rail_name="_spine_plant",
                account_role=self.account_role,
                account_parent_role=self.account_parent_role,
            ))
            cursor = cursor + timedelta(days=1)
        return tuple(markers)

    def _plan(self) -> ResidualState:
        """Every row ``emit()`` writes, in the residual domain."""
        return ResidualState(
            legs=self._planned_markers(),
            balances=(self._planned_balance(),),
        )

    @property
    def intended(self) -> RuleViolation:
        # DS.2 — the magnitude comes from the overdraft RESIDUAL
        # evaluated over the planned rows, not hand-inlined
        # `round(-magnitude, 2)`: if the law moves, this expectation
        # moves with it. The residual already carries the matview's
        # negative form (`min(effective, 0)`), so it round-trips
        # against `detect()` while `magnitude` stays caller-facing
        # positive.
        residual = overdraft_residual(
            self._plan(), self.account_id, self.anchor_day,
        )
        assert residual is not None  # planted internal row ⇒ cell exists
        return RuleViolation.of(
            "overdraft",
            account_id=self.account_id,
            business_day=self.anchor_day,
            stored_balance=identity_dollars(residual),
        )

    @property
    def also_trips_drift(self) -> RuleViolation | None:
        """The empirical AU.0 edge: drift fires on the same account/day
        when the planted account is a LEAF (account_parent_role is set).
        Returns `None` when the planted account is NOT a leaf (drift's
        `parent_role IS NOT NULL` filter excludes it).

        DS.2 — derived from ``drift_residual`` over the same planned
        rows emit() writes (zero-amount markers ⇒ Σ legs = 0, so the
        planted stored IS the drift: −magnitude). The edge's magnitude
        now tracks the drift law automatically.
        """
        if self.account_parent_role is None:
            return None
        residual = drift_residual(
            self._plan(), self.account_id, self.anchor_day,
        )
        assert residual is not None  # leaf + emitted day ⇒ cell exists
        return RuleViolation.of(
            "drift",
            account_id=self.account_id,
            business_day=self.anchor_day,
            drift=identity_dollars(residual),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """The single account_id this plant overdrafts. AV.5."""
        return frozenset({self.account_id})

    def emit(
        self,
        conn: SyncConnection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        # CZ.2: unconditional source='training' stamp.
        metadata = scenario_metadata(
            scenario_id, generator="OverdraftGenerator",
        )
        start, end = day_bounds(self.anchor_day)
        # DS.2 — every money value + row id below reads off the SAME
        # plan the intended/edge properties evaluate residuals over.
        balance = self._planned_balance()
        insert_balance(
            conn,
            prefix=self.prefix,
            account_id=balance.account_id,
            account_name=f"Overdraft Acct ({self.account_role})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=balance.account_parent_role,
            business_day_start=start,
            business_day_end=end,
            money=float(balance.money.to_dollars()),
            metadata=metadata,
        )
        # DL.3.1 — zero-amount drilldown marker tx so the `Overdraft
        # Violations → Daily Statement for this account-day` drill
        # destination is non-empty. The matview shows
        # `Overdraft Acct ({role}) ({account_id})` for the synthetic
        # daily_balances row (MAX(entry) wins over the baseline customer
        # name when the plant lands on a template-instance account); the
        # drill writes that account_display + business_day. Without a
        # matching tx the Daily Statement Transactions WHERE returns 0
        # rows and the DL.2 drill guardrail fails with destination-empty.
        #
        # Zero amount preserves the overdraft invariant identity: the
        # matview reads `effective_money < 0` purely from
        # daily_balances; transactions don't enter the formula. Adding
        # this tx CAN nudge `computed_subledger` on subsequent days
        # (it's a cumulative sum), but at amount=0 the sum is
        # unchanged — drift values on this account stay byte-identical.
        #
        # DL.3.7 — extend the marker emit across CL.5 carry-forward days.
        # The ``<prefix>_overdraft`` matview reads from
        # ``effective_balances`` which carries forward a negative balance
        # on every day until the next emit (or window end), so a single
        # plant surfaces 1..N visible overdraft rows in the L1 dashboard
        # — one per carry day. The DL.3.1 single-marker-on-anchor pattern
        # left every carry day's drill destination empty. When
        # ``as_of_day`` is set (production seed pipeline via
        # ``_adapt_overdraft``), sweep one zero-amount marker per day
        # from ``anchor_day`` through ``as_of_day`` inclusive. ``None``
        # (default; unit-test path) preserves the single-marker
        # byte-identity. The sweep + per-day id shapes live in
        # ``_planned_markers`` (DS.2 — same plan the residuals read).
        for marker in self._planned_markers():
            insert_tx(
                conn,
                prefix=self.prefix,
                id=marker.id,
                account_id=marker.account_id,
                account_name=f"Overdraft Acct ({self.account_role})",
                account_role=self.account_role,
                account_scope="internal",
                account_parent_role=marker.account_parent_role,
                amount_money=float(marker.amount.to_dollars()),
                amount_direction="Debit",
                status=POSTED_STATUS,
                posting=marker.posting.strftime("%Y-%m-%d %H:%M:%S"),
                transfer_id=marker.transfer_id,
                rail_name="_spine_plant",
                origin=ORIGIN_INTERNAL_INITIATED,
                metadata=metadata,
            )


# Phase AU.3.d (2026-05-23): local helpers hoisted to
# `common/spine/_emit_helpers.py`. Per-invariant-shape helpers (none for
# overdraft) would stay here.
