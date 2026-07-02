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
from recon_gen.common.db import SyncConnection
from dataclasses import dataclass, field
from datetime import date, datetime
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
    to_date,
    ts,
)
from recon_gen.common.spine.residuals import (
    BalanceRow,
    LegRow,
    ResidualState,
    drift_residual,
    ledger_drift_residual,
)
from recon_gen.common.spine.rng import scenario_rng
from recon_gen.common.spine.violation import (
    RuleViolation,
    Violation,
    identity_dollars,
)


def _posting_dt(day: date) -> datetime:
    """The residual-domain projection of ``ts(day)`` — parse the exact
    string the insert boundary writes, so the planned ``LegRow.posting``
    tracks the emit-side timestamp convention (noon default) by
    construction rather than by a copied constant."""
    return datetime.strptime(ts(day), "%Y-%m-%d %H:%M:%S")


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

    def detect(self, conn: SyncConnection) -> set[Violation]:
        rows = fetch_all(
            conn,
            f"SELECT account_id, business_day_start, drift "
            f"FROM {self.prefix}_drift",
        )
        # AO.1: matview math runs on BIGINT cents (integer-exact). Project
        # back to dollars at the detect boundary so violation identities
        # still round-trip against generators that author in dollars.
        return {
            RuleViolation.of(
                "drift",
                account_id=aid,
                business_day=to_date(bds),
                drift=round(float(Cents.from_db(int(d)).to_dollars()), 2),
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

    def detect(self, conn: SyncConnection) -> set[Violation]:
        rows = fetch_all(
            conn,
            f"SELECT account_id, business_day_start, drift "
            f"FROM {self.prefix}_ledger_drift",
        )
        # AO.1: see DriftInvariant.detect note on cents → dollars projection.
        return {
            RuleViolation.of(
                "ledger_drift",
                account_id=aid,
                business_day=to_date(bds),
                drift=round(float(Cents.from_db(int(d)).to_dollars()), 2),
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
    # AY.4.d — production callers thread cfg.db.table_prefix here.
    prefix: str = "spec_example"

    def _planned_child_balance(self) -> BalanceRow:
        """The child's stored balance — leg total shifted by
        ``magnitude`` (the drift). Cents conversion mirrors the insert
        boundary's ``Cents.from_dollars(str(value))`` exactly."""
        return BalanceRow(
            account_id=self.child_account_id,
            entry=0,
            day=self.anchor_day,
            money=Cents.from_dollars(str(self.leg_amount + self.magnitude)),
            account_role=self.child_role,
            account_parent_role=self.parent_role,
        )

    def _planned_clean_leg(self) -> LegRow:
        """The child's one non-zero Posted leg (the clean side of the
        drift). Id slug omits anchor_day — see emit()'s dedup note."""
        return LegRow(
            id=f"tx-drift-{self.child_role}-{self.child_account_id}-1",
            entry=0,
            account_id=self.child_account_id,
            amount=Cents.from_dollars(str(self.leg_amount)),
            status=POSTED_STATUS,
            posting=_posting_dt(self.anchor_day),
            transfer_id=(
                f"xfer-drift-{self.child_role}-{self.child_account_id}-1"
            ),
            rail_name="_spine_plant",
            account_role=self.child_role,
            account_parent_role=self.parent_role,
        )

    def _planned_child_marker(self) -> LegRow:
        """The DL.3.1 zero-amount drilldown marker on the child. Zero
        amount is load-bearing: it keeps the drift residual's Σ legs at
        exactly ``leg_amount`` (see emit()'s rationale comment)."""
        day_slug = self.anchor_day.isoformat()
        return LegRow(
            id=f"tx-drift-marker-child-{self.child_account_id}-{day_slug}",
            entry=0,
            account_id=self.child_account_id,
            amount=Cents.from_dollars(str(0.0)),
            status=POSTED_STATUS,
            posting=_posting_dt(self.anchor_day),
            transfer_id=(
                f"xfer-drift-marker-child-{self.child_account_id}-{day_slug}"
            ),
            rail_name="_spine_plant",
            account_role=self.child_role,
            account_parent_role=self.parent_role,
        )

    def _planned_parent_balance(self) -> BalanceRow | None:
        """The parent's stored balance — the CLEAN leg total, so the
        parent's ledger_drift is child-propagated (Σ child stored is
        inflated by ``magnitude``). None when the shape has no parent
        account — mirrors emit()'s parent-emission condition."""
        if self.parent_account_id is None or self.parent_account_role is None:
            return None
        return BalanceRow(
            account_id=self.parent_account_id,
            entry=0,
            day=self.anchor_day,
            money=Cents.from_dollars(str(self.leg_amount)),
            account_role=self.parent_account_role,
            account_parent_role=None,
        )

    def _planned_parent_marker(self) -> LegRow | None:
        """The DL.3.1 zero-amount marker on the parent. Zero keeps the
        ledger_drift residual's direct-postings term at exactly 0."""
        if self.parent_account_id is None or self.parent_account_role is None:
            return None
        day_slug = self.anchor_day.isoformat()
        return LegRow(
            id=f"tx-drift-marker-parent-{self.parent_account_id}-{day_slug}",
            entry=0,
            account_id=self.parent_account_id,
            amount=Cents.from_dollars(str(0.0)),
            status=POSTED_STATUS,
            posting=_posting_dt(self.anchor_day),
            transfer_id=(
                f"xfer-drift-marker-parent-{self.parent_account_id}-{day_slug}"
            ),
            rail_name="_spine_plant",
            account_role=self.parent_account_role,
            account_parent_role=None,
        )

    def _plan(self) -> ResidualState:
        """Every row ``emit()`` writes, projected into the residual
        domain — the SINGLE plan both ``emit()`` and the intended/edge
        properties read (DS.2: a separately-written expectation is the
        calibration-drift disease with a new name)."""
        legs: list[LegRow] = [
            self._planned_clean_leg(), self._planned_child_marker(),
        ]
        balances: list[BalanceRow] = [self._planned_child_balance()]
        parent_balance = self._planned_parent_balance()
        if parent_balance is not None:
            balances.append(parent_balance)
        parent_marker = self._planned_parent_marker()
        if parent_marker is not None:
            legs.append(parent_marker)
        return ResidualState(legs=tuple(legs), balances=tuple(balances))

    @property
    def intended(self) -> RuleViolation:
        # DS.2 — the magnitude comes from the drift RESIDUAL evaluated
        # over the planned rows, not hand-inlined `round(magnitude, 2)`:
        # if the law moves, this expectation moves with it.
        residual = drift_residual(
            self._plan(), self.child_account_id, self.anchor_day,
        )
        assert residual is not None  # child is a planted internal leaf
        return RuleViolation.of(
            "drift",
            account_id=self.child_account_id,
            business_day=self.anchor_day,
            drift=identity_dollars(residual),
        )

    @property
    def also_trips_ledger_drift(self) -> RuleViolation | None:
        """The secondary edge: when this generator's child-drift
        propagates up to the parent's `_ledger_drift`. `None` when no
        parent account is present in the shape (the L2 instance has no
        account with the child's `parent_role`).

        DS.2 — derived from ``ledger_drift_residual`` over the same
        planned rows emit() writes (child stored is `leg + magnitude`,
        parent stored is the clean `leg`, so the law yields
        −magnitude). The guard mirrors emit()'s parent-emission
        condition (BOTH id and role must be present) — the pre-DS.2
        id-only guard could claim a ledger_drift edge for a parent row
        emit() never writes.
        """
        if self.parent_account_id is None or self.parent_account_role is None:
            return None
        residual = ledger_drift_residual(
            self._plan(), self.parent_account_id, self.anchor_day,
        )
        assert residual is not None  # the plan carries the parent row
        return RuleViolation.of(
            "ledger_drift",
            account_id=self.parent_account_id,
            business_day=self.anchor_day,
            drift=identity_dollars(residual),
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
        conn: SyncConnection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        # CZ.2: unconditional source='training' stamp.
        metadata = scenario_metadata(
            scenario_id, generator="DriftGenerator",
        )
        start, end = day_bounds(self.anchor_day)
        # DS.2 — every money value + row id below reads off the SAME
        # plan the intended/edge properties evaluate residuals over.
        child_balance = self._planned_child_balance()
        clean_leg = self._planned_clean_leg()
        child_marker = self._planned_child_marker()
        # Child: clean balance == leg total. Stored is shifted by
        # `magnitude` → drift fires on the child.
        insert_balance(
            conn,
            prefix=self.prefix,
            account_id=child_balance.account_id,
            account_name=f"Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=child_balance.account_parent_role,
            business_day_start=start,
            business_day_end=end,
            money=float(child_balance.money.to_dollars()),
            metadata=metadata,
        )
        # The non-zero clean leg. ID slug intentionally OMITS anchor_day
        # so multiple DriftPlants on the same (child_account_id,
        # child_role) collapse to ONE surviving leg via
        # current_transactions's MAX(entry) dedup — preserves the
        # pre-DL.3.1 cumulative-Σ-of-legs shape that the drift
        # invariant math + semantic locks pin to. Per-day visibility
        # for the drill destination is the marker tx's job (below).
        insert_tx(
            conn,
            prefix=self.prefix,
            id=clean_leg.id,
            account_id=clean_leg.account_id,
            account_name=f"Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=clean_leg.account_parent_role,
            amount_money=float(clean_leg.amount.to_dollars()),
            amount_direction="Credit",
            status=POSTED_STATUS,
            posting=clean_leg.posting.strftime("%Y-%m-%d %H:%M:%S"),
            transfer_id=clean_leg.transfer_id,
            rail_name="_spine_plant",
            origin=ORIGIN_INTERNAL_INITIATED,
            metadata=metadata,
        )
        # DL.3.1 — zero-amount drilldown marker tx on the child so the
        # `Leaf Account Drift → Daily Statement for this account-day`
        # drill destination is non-empty for EVERY plant's anchor_day.
        # Anchor_day in the id slug uniquifies per-plant — multiple
        # DriftPlants on the same (child_account_id, child_role) all
        # survive current_transactions's MAX(entry) dedup since each
        # carries a distinct id.
        #
        # Zero amount keeps `computed_subledger`'s cumulative Σ legs
        # unchanged → drift = stored − Σ legs identity is preserved
        # bit-for-bit, so tests/data/_semantic_locks/* stays
        # byte-identical across the (drift invariant identity) shape.
        # Without the marker the only leg tx surviving dedup lands on
        # whichever plant emitted last (MAX(entry)); the 6 other
        # DriftPlants in the DM.1 helper's 7-plant pack render with
        # zero Posted Money Records on their drilled day.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=child_marker.id,
            account_id=child_marker.account_id,
            account_name=f"Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=child_marker.account_parent_role,
            amount_money=float(child_marker.amount.to_dollars()),
            amount_direction="Credit",
            status=POSTED_STATUS,
            posting=child_marker.posting.strftime("%Y-%m-%d %H:%M:%S"),
            transfer_id=child_marker.transfer_id,
            rail_name="_spine_plant",
            origin=ORIGIN_INTERNAL_INITIATED,
            metadata=metadata,
        )
        # Parent (when present in the shape): stored money equals the
        # CLEAN child leg total. With the child's stored inflated by
        # `magnitude`, the parent's computed (Σ child.money) is off by
        # `magnitude` too → ledger_drift fires on the parent.
        parent_balance = self._planned_parent_balance()
        parent_marker = self._planned_parent_marker()
        if parent_balance is not None and parent_marker is not None:
            insert_balance(
                conn,
                prefix=self.prefix,
                account_id=parent_balance.account_id,
                account_name=f"Drift Parent ({self.parent_account_role})",
                account_role=parent_balance.account_role,
                account_scope="internal",
                account_parent_role=None,
                business_day_start=start,
                business_day_end=end,
                money=float(parent_balance.money.to_dollars()),
                metadata=metadata,
            )
            # DL.3.1 — zero-amount drilldown marker tx so the `Parent
            # Account Drift → Daily Statement` drill destination
            # carries at least one Posted Money Record on the parent
            # account-day. The parent shows in `_drift` matview via
            # ledger_drift propagation (parent.stored − Σ child.stored =
            # −magnitude); the drill writes the parent's account_display
            # + business_day. Without this tx the Daily Statement
            # Transactions WHERE clause (account_display = pL1DsAccount
            # AND posting_day = pL1DsBalanceDate) returns 0 rows and the
            # DL.2 drill guardrail fails with destination-empty.
            #
            # Zero amount keeps `computed_ledger_balance` (Σ parent
            # direct postings) exactly 0 → ledger_drift's
            # `parent.stored − parent.computed` math is preserved, so
            # the violation identity in tests/data/_semantic_locks/*
            # stays byte-identical (verified by re-lock-emits-no-diff
            # post-change).
            insert_tx(
                conn,
                prefix=self.prefix,
                id=parent_marker.id,
                account_id=parent_marker.account_id,
                account_name=f"Drift Parent ({self.parent_account_role})",
                account_role=parent_marker.account_role,
                account_scope="internal",
                account_parent_role=None,
                amount_money=float(parent_marker.amount.to_dollars()),
                amount_direction="Credit",
                status=POSTED_STATUS,
                posting=parent_marker.posting.strftime("%Y-%m-%d %H:%M:%S"),
                transfer_id=parent_marker.transfer_id,
                rail_name="_spine_plant",
                origin=ORIGIN_INTERNAL_INITIATED,
                metadata=metadata,
            )


@dataclass
class LedgerDriftGenerator:
    """Plant a parent+child pair where the parent's stored money
    diverges from Σ child stored by ``delta``. Child is CLEAN (stored
    matches its single leg), so only ``ledger_drift`` fires — not
    ``drift``.

    Distinct from DriftGenerator (which fires BOTH drift on the child +
    ledger_drift on the parent as a coupled pair): this generator
    targets ledger_drift in isolation, modeling the real-world case
    where a control account is adjusted directly without touching the
    sub-ledger leaves.

    Uses a UNIQUE synthetic ``parent_role`` so the matview's
    ``computed_ledger_balance`` for our synthetic parent only sums OUR
    synthetic child (no bleed from baseline accounts that happen to
    share a real parent_role).
    """

    parent_account_id: str
    parent_role: str
    child_account_id: str
    child_role: str
    anchor_day: date
    delta: float
    rng: random.Random = field(default_factory=scenario_rng)
    leg_amount: float = 100.0
    prefix: str = "spec_example"

    def _planned_child_balance(self) -> BalanceRow:
        """The clean child: stored money == its single leg. Links to
        the synthetic parent_role so the residual's Σ-children term
        (like the matview's) sums exactly our child. Cents conversion
        mirrors the insert boundary's ``Cents.from_dollars(str(value))``
        exactly."""
        return BalanceRow(
            account_id=self.child_account_id,
            entry=0,
            day=self.anchor_day,
            money=Cents.from_dollars(str(self.leg_amount)),
            account_role=self.child_role,
            account_parent_role=self.parent_role,
        )

    def _planned_clean_leg(self) -> LegRow:
        """The child's matching credit leg — keeps the CHILD drift-free
        (stored == Σ legs), so only ledger_drift fires."""
        return LegRow(
            id=f"tx-ledger-drift-{self.parent_role}-1",
            entry=0,
            account_id=self.child_account_id,
            amount=Cents.from_dollars(str(self.leg_amount)),
            status=POSTED_STATUS,
            posting=_posting_dt(self.anchor_day),
            transfer_id=f"xfer-ledger-drift-{self.parent_role}-1",
            rail_name="_spine_plant",
            account_role=self.child_role,
            account_parent_role=self.parent_role,
        )

    def _planned_parent_balance(self) -> BalanceRow:
        """The parent's stored balance — Σ child stored shifted by
        ``delta`` (the ledger drift)."""
        return BalanceRow(
            account_id=self.parent_account_id,
            entry=0,
            day=self.anchor_day,
            money=Cents.from_dollars(str(self.leg_amount + self.delta)),
            account_role=self.parent_role,
            account_parent_role=None,
        )

    def _planned_parent_marker(self) -> LegRow:
        """The DL.3.1 zero-amount drilldown marker on the parent. Zero
        keeps the ledger_drift residual's direct-postings term at 0."""
        day_slug = self.anchor_day.isoformat()
        return LegRow(
            id=(
                f"tx-ledger-drift-marker-parent-"
                f"{self.parent_account_id}-{day_slug}"
            ),
            entry=0,
            account_id=self.parent_account_id,
            amount=Cents.from_dollars(str(0.0)),
            status=POSTED_STATUS,
            posting=_posting_dt(self.anchor_day),
            transfer_id=(
                f"xfer-ledger-drift-marker-parent-"
                f"{self.parent_account_id}-{day_slug}"
            ),
            rail_name="_spine_plant",
            account_role=self.parent_role,
            account_parent_role=None,
        )

    def _plan(self) -> ResidualState:
        """Every row ``emit()`` writes, projected into the residual
        domain — the SINGLE plan both ``emit()`` and ``intended`` read
        (DS.2)."""
        return ResidualState(
            legs=(self._planned_clean_leg(), self._planned_parent_marker()),
            balances=(
                self._planned_child_balance(),
                self._planned_parent_balance(),
            ),
        )

    @property
    def intended(self) -> RuleViolation:
        # DS.2 — the magnitude comes from the ledger_drift RESIDUAL
        # evaluated over the planned rows, not hand-inlined
        # `round(delta, 2)`: if the law moves, this expectation moves
        # with it.
        residual = ledger_drift_residual(
            self._plan(), self.parent_account_id, self.anchor_day,
        )
        assert residual is not None  # the plan carries the parent row
        return RuleViolation.of(
            "ledger_drift",
            account_id=self.parent_account_id,
            business_day=self.anchor_day,
            drift=identity_dollars(residual),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        return frozenset({self.parent_account_id, self.child_account_id})

    def emit(
        self,
        conn: SyncConnection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        # CZ.2: unconditional source='training' stamp.
        metadata = scenario_metadata(
            scenario_id, generator="LedgerDriftGenerator",
        )
        start, end = day_bounds(self.anchor_day)
        # DS.2 — every money value + row id below reads off the SAME
        # plan `intended` evaluates the ledger_drift residual over.
        child_balance = self._planned_child_balance()
        clean_leg = self._planned_clean_leg()
        parent_balance = self._planned_parent_balance()
        parent_marker = self._planned_parent_marker()
        # Child: stored money == leg_amount, with one matching credit
        # leg → child's computed_subledger = leg_amount → no drift on
        # child. Child's account_parent_role points at our SYNTHETIC
        # parent_role so the matview's EXISTS gate fires only for our
        # synthetic parent (no baseline pollution).
        insert_balance(
            conn,
            prefix=self.prefix,
            account_id=child_balance.account_id,
            account_name=f"Ledger Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=child_balance.account_parent_role,
            business_day_start=start,
            business_day_end=end,
            money=float(child_balance.money.to_dollars()),
            metadata=metadata,
        )
        # Child clean leg — id intentionally omits anchor_day so the
        # pre-DL.3.1 cumulative-Σ shape is preserved (see
        # DriftGenerator.emit's same-shape comment). Today's per-day-
        # unique parent_role keeps this id collision-safe across plants.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=clean_leg.id,
            account_id=clean_leg.account_id,
            account_name=f"Ledger Drift Child ({self.child_role})",
            account_role=self.child_role,
            account_scope="internal",
            account_parent_role=clean_leg.account_parent_role,
            amount_money=float(clean_leg.amount.to_dollars()),
            amount_direction="Credit",
            status=POSTED_STATUS,
            posting=clean_leg.posting.strftime("%Y-%m-%d %H:%M:%S"),
            transfer_id=clean_leg.transfer_id,
            rail_name="_spine_plant",
            origin=ORIGIN_INTERNAL_INITIATED,
            metadata=metadata,
        )
        # Parent: stored money = leg_amount + delta. Σ children for our
        # synthetic parent_role = leg_amount (just our child). drift =
        # (leg_amount + delta) - leg_amount = delta exactly.
        insert_balance(
            conn,
            prefix=self.prefix,
            account_id=parent_balance.account_id,
            account_name=f"Ledger Drift Parent ({self.parent_role})",
            account_role=parent_balance.account_role,
            account_scope="internal",
            account_parent_role=None,
            business_day_start=start,
            business_day_end=end,
            money=float(parent_balance.money.to_dollars()),
            metadata=metadata,
        )
        # DL.3.1 — zero-amount drilldown marker tx on the parent so the
        # `Parent Account Drift → Daily Statement for this account-day`
        # drill destination is non-empty. Mirrors DriftGenerator's
        # parent-side marker (see DriftGenerator.emit for rationale).
        # Zero amount preserves `computed_ledger_balance` (Σ parent
        # direct postings stays 0) → ledger_drift = parent.stored −
        # Σ child.stored = delta exactly — invariant identity locked
        # in tests/data/_semantic_locks/* stays byte-identical.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=parent_marker.id,
            account_id=parent_marker.account_id,
            account_name=f"Ledger Drift Parent ({self.parent_role})",
            account_role=parent_marker.account_role,
            account_scope="internal",
            account_parent_role=None,
            amount_money=float(parent_marker.amount.to_dollars()),
            amount_direction="Credit",
            status=POSTED_STATUS,
            posting=parent_marker.posting.strftime("%Y-%m-%d %H:%M:%S"),
            transfer_id=parent_marker.transfer_id,
            rail_name="_spine_plant",
            origin=ORIGIN_INTERNAL_INITIATED,
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
