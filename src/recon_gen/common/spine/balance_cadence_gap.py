"""Balance-cadence-gap family — the DS.5.1 completeness closure.

``balance_cadence_gap`` was inventory row 14: an emitted L1 matview
(CL.6) with a live ``l1_exceptions`` union arm, but NO ``Invariant``
class in the spine registry and no generator — the audit-PDF /
dashboard / direct-DB agreement gate walks ``ALL_INVARIANTS``, so a
matview outside the registry was outside the cross-check. DS.0 named it
the annotation gate's born-failing canary; DS.5.1 resolves the canary
by giving it a residual (``cadence_gap_residual``, authored from the
CL.0 lock) AND this detector + generator so the registry finally
covers it.

The matview fires at (account, day) with no current balance claim when
the institution declared ``explicit_daily`` cadence (must report every
day) or ``sparse`` + activity that day (something moved with no close
to reconcile against). Two firing modes, one ``gap_kind`` column:
``declared_daily_missing`` / ``sparse_with_activity``. Identity is
(account_id, business_day, gap_kind).

**DS.5.1 engine fix (red-first, DS.3.3c precedent):** the matview's
account universe came from balance rows alone, so a declared
``explicit_daily`` account with ZERO rows anywhere, and a
transactions-only sparse account with activity, were both invisible —
all-missing silent while one-missing alarmed. The universe now unions
balance-observed, non-failed-leg-observed and L2-declared-cadence
singletons, and the in-scope-day frame opens from either feed. Witnesses
CG2 / CG3 in ``tests/data/kats/cadence_gap.json``; the enumeration
domain (``domains/cadence_gap.py``) proves engine == residual on the
real engine.

The generator plants against the ONE declared ``explicit_daily``
account on the instance (``ClearingSuspense`` on spec_example): balance
rows on the frame's flanks with one business day skipped in the middle,
so the middle day fires ``declared_daily_missing``. Single-account,
balance-only — no transaction legs, so it trips no other detector
(same single-edge discipline as ``StuckPendingGenerator``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import ClassVar

from recon_gen.common.db import SyncConnection
from recon_gen.common.l2.primitives import BalanceCadence, L2Instance
from recon_gen.common.spine._db import fetch_all
from recon_gen.common.spine._emit_helpers import insert_balance, load_spec_example
from recon_gen.common.spine.violation import RuleViolation, Violation
from recon_gen.common.spine.math_invariant import math_invariant
from recon_gen.common.spine.residuals import (
    MathKind,
    cadence_gap_residual,
)

_EXPLICIT_DAILY: BalanceCadence = "explicit_daily"


@math_invariant(
    matview="balance_cadence_gap",
    kind=MathKind.DERIVATION,
    residual=cadence_gap_residual,
    kat_file="tests/data/kats/cadence_gap.json",
)
@dataclass(frozen=True)
class BalanceCadenceGapInvariant:
    """Balance-cadence-gap detector. Reads ``<prefix>_balance_cadence_gap``
    and projects every (account_id, business_day, gap_kind) row — both
    firing modes share the matview, so the detector is mode-agnostic
    (the analyst-facing ``gap_kind`` rounds out the identity)."""

    name: ClassVar[str] = "balance_cadence_gap"
    prefix: str = "spec_example"

    def detect(self, conn: SyncConnection) -> set[Violation]:
        rows = fetch_all(
            conn,
            f"SELECT account_id, business_day_start, gap_kind "
            f"FROM {self.prefix}_balance_cadence_gap",
        )
        return {
            RuleViolation.of(
                "balance_cadence_gap",
                account_id=str(account_id),
                business_day=str(business_day),
                gap_kind=str(gap_kind),
            )
            for account_id, business_day, gap_kind in rows
        }

    def scenario_for(
        self,
        *,
        anchor_day: date,
        instance: L2Instance | None = None,
        account_id: str | None = None,
    ) -> "BalanceCadenceGapGenerator":
        """Resolve the instance's declared ``explicit_daily`` account and
        return a generator that plants a middle-day gap for it.

        Raises ``ValueError`` when the instance declares no
        ``explicit_daily`` singleton — the ``declared_daily_missing``
        mode is unrepresentable without one, and manufacturing a scenario
        that emits no row would silently under-cover the gate (the
        CL.7-plant refusal precedent).
        """
        inst = instance if instance is not None else load_spec_example()
        declared = [
            a for a in inst.accounts
            if a.balance_cadence == _EXPLICIT_DAILY and a.scope == "internal"
        ]
        if not declared:
            raise ValueError(
                "instance declares no internal explicit_daily account; "
                "cannot manufacture a balance_cadence_gap "
                "declared_daily_missing scenario",
            )
        account = declared[0]
        return BalanceCadenceGapGenerator(
            account_id=account_id or str(account.id),
            account_name=str(account.name),
            account_role=str(account.role),
            account_parent_role=(
                None if account.parent_role is None
                else str(account.parent_role)
            ),
            anchor_day=anchor_day,
            prefix=self.prefix,
        )


@dataclass
class BalanceCadenceGapGenerator:
    """Plant a ``declared_daily_missing`` gap: three consecutive business
    days, balance rows emitted on the first + third, the MIDDLE day
    skipped. The flanking rows open the in-scope frame and mark the
    account present those days; the missing middle day fires. Balance-
    only (no legs) so it trips no other detector."""

    account_id: str
    account_name: str
    account_role: str
    account_parent_role: str | None
    anchor_day: date
    prefix: str = "spec_example"
    #: The flanking days carry these rows; the middle day is the gap.
    _present_days: tuple[int, ...] = field(default=(0, 2), init=False)
    _gap_day_offset: int = field(default=1, init=False)

    @property
    def gap_day(self) -> date:
        return self.anchor_day + timedelta(days=self._gap_day_offset)

    @property
    def intended(self) -> RuleViolation:
        return RuleViolation.of(
            "balance_cadence_gap",
            account_id=self.account_id,
            business_day=str(self.gap_day),
            gap_kind="declared_daily_missing",
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        return frozenset({self.account_id})

    def emit(
        self,
        conn: SyncConnection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        metadata = scenario_metadata(
            scenario_id, generator="BalanceCadenceGapGenerator",
        )
        for offset in self._present_days:
            day = self.anchor_day + timedelta(days=offset)
            insert_balance(
                conn,
                prefix=self.prefix,
                account_id=self.account_id,
                account_name=self.account_name,
                account_role=self.account_role,
                account_scope="internal",
                account_parent_role=self.account_parent_role,
                expected_eod_balance=0.0,
                business_day_start=f"{day} 00:00:00",
                business_day_end=f"{day} 23:59:59",
                money=0.0,
                metadata=metadata,
            )
