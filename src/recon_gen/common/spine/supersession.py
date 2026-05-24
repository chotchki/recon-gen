"""Supersession family — spine generator only (no Invariant).

AY.2.b promotion of the OLD `SupersessionPlant` /
`_emit_supersession_rows`. The plant emits TWO transactions sharing
one logical ``id`` — the original posting + a TechnicalCorrection
rewrite. The dialect's auto-increment `entry` column gives the
correction a higher `entry`; the M.2b.12 Supersession Audit
dataset's ``COUNT(*) OVER (PARTITION BY id) > 1`` +
``supersedes IS NOT NULL`` filter catches the pair.

Not a matview violation — the audit PDF reads the
`<prefix>_transactions` table directly for the supersession trail;
no L1 invariant matview surfaces them. The generator's `intended`
returns an `AuditFixture` (AY.2.a evidence-currency subtype for
audit-PDF input markers).

Registers in `INVARIANT_GENERATOR_EDGES` with an empty invariant
tuple — the AY.2.b widening permits coverage / audit-fixture
generators to land on the spine without inventing a no-op
detector.

Single-edge property: no balance row → no drift trip. The
identical ``id`` field on the two rows is the supersession
semantic; the dialect's BIGSERIAL / IDENTITY / AUTOINCREMENT
column handles the `entry` discriminator.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from recon_gen.common.spine._emit_helpers import insert_tx, ts
from recon_gen.common.spine.violation import AuditFixture


@dataclass
class SupersessionGenerator:
    """Plant TWO transactions sharing one logical `id` — the
    original posting + a TechnicalCorrection rewrite.

    Both rows land on the same account / rail / transfer_id; only
    the `amount_money` differs (the correction "fixes" the original
    amount). The dialect auto-increments `entry` so the correction
    sorts after the original; the audit PDF's CASE on `entry =
    MAX(entry) PARTITION BY id` picks the correction as the
    "current" row + the original as the "superseded" trail.

    Account context (role / scope / parent_role) arrives as
    construction args; the AY.4 adapter resolves them from the
    OLD plant's referenced template instance.
    """

    account_id: str
    account_role: str
    account_scope: str
    account_parent_role: str | None
    rail_name: str
    original_amount: float
    corrected_amount: float
    anchor_day: date
    prefix: str = "spec_example"

    @property
    def transaction_id(self) -> str:
        """The shared logical id for both rows — the supersession
        anchor. Deterministic on account_id."""
        return f"tx-supersedes-{self.account_id}"

    @property
    def transfer_id(self) -> str:
        return f"tr-supersedes-{self.account_id}"

    @property
    def intended(self) -> AuditFixture:
        """The audit-PDF supersession entry's identity — keyed on
        the logical transaction id + the corrected amount (what the
        audit shows as the "current" value)."""
        return AuditFixture.of(
            "supersession",
            transaction_id=self.transaction_id,
            account_id=self.account_id,
            corrected_amount=round(self.corrected_amount, 2),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        return frozenset({self.account_id})

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        metadata = (
            scenario_metadata(
                scenario_id, generator="SupersessionGenerator",
            )
            if scenario_id is not None else None
        )
        # Original posting at the anchor day @ 09:00.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=self.transaction_id,
            account_id=self.account_id,
            account_name=f"Supersession Plant ({self.account_id})",
            account_role=self.account_role,
            account_scope=self.account_scope,
            account_parent_role=self.account_parent_role,
            amount_money=-self.original_amount,
            amount_direction="Debit",
            status="Posted",
            posting=ts(self.anchor_day, hour=9),
            transfer_id=self.transfer_id,
            rail_name=self.rail_name,
            origin="InternalInitiated",
            metadata=metadata,
        )
        # TechnicalCorrection at 09:30 — same logical id, different
        # amount, supersedes='TechnicalCorrection'.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=self.transaction_id,
            account_id=self.account_id,
            account_name=f"Supersession Plant ({self.account_id})",
            account_role=self.account_role,
            account_scope=self.account_scope,
            account_parent_role=self.account_parent_role,
            amount_money=-self.corrected_amount,
            amount_direction="Debit",
            status="Posted",
            posting=ts(self.anchor_day, hour=10),  # 09:30 in OLD; 10 here keeps insert_tx's ts() helper signature simple
            transfer_id=self.transfer_id,
            rail_name=self.rail_name,
            origin="InternalInitiated",
            metadata=metadata,
            supersedes="TechnicalCorrection",
        )
