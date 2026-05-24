"""FailedTransaction family — spine generator only (no Invariant).

AY.2.b promotion of the OLD `FailedTransactionPlant` /
`_emit_failed_transaction_rows`. The plant emits ONE Debit leg with
``status='Failed'`` per scenario; drives the L2FT Postings dataset's
``Status='Other'`` dropdown coverage (X.1.g e2e test). The L1 schema
treats ``status`` as an open enum — Pending / Posted are the
tracked terminal states, anything else (Failed, Cancelled, …)
collapses to ``Other`` in the L2FT dataset's CASE projection.

Not a violation — the existence of a Failed leg is a valid terminal
state. The generator's `intended` returns an `AuditFixture` (the
AY.2.a evidence-currency subtype for audit-PDF / dropdown input
markers; no matview surfaces these). No Invariant matches; the
generator registers in `INVARIANT_GENERATOR_EDGES` with an empty
tuple of invariants (the AY.2.b widening that lets coverage /
audit-fixture generators stay on the spine without inventing
no-op detectors).

Single-edge property: transfers-only emit, no balance row, no
drift trip.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from recon_gen.common.spine._emit_helpers import insert_tx, ts
from recon_gen.common.spine.violation import AuditFixture


@dataclass
class FailedTransactionGenerator:
    """Plant ONE Debit leg with ``status='Failed'``.

    Single-leg, no counter-leg: a Failed transaction never settled,
    so the rail's other side wasn't created. The leg posts at a
    normal Debit shape with ``status='Failed'`` so the L2FT
    postings dataset's ``CASE WHEN status IN ('Pending','Posted')
    THEN status ELSE 'Other' END`` collapses it to ``Other``.

    Account context (role / scope / parent_role) arrives as
    construction args; the AY.4 adapter resolves them from the
    OLD plant's referenced template instance.
    """

    account_id: str
    account_role: str
    account_scope: str
    account_parent_role: str | None
    rail_name: str
    amount: float
    anchor_day: date
    prefix: str = "spec_example"

    @property
    def transaction_id(self) -> str:
        """Deterministic id from the account_id — claimed_accounts
        + transaction_id naming both derive purely from construction
        fields so the AV.5 ScenarioContext collision check fires
        consistently."""
        return f"tx-failed-{self.account_id}"

    @property
    def intended(self) -> AuditFixture:
        """The Failed-status row's identity — the dropdown-coverage
        claim the X.1.g e2e test reads back."""
        return AuditFixture.of(
            "failed_transaction",
            transaction_id=self.transaction_id,
            account_id=self.account_id,
            rail_name=self.rail_name,
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
                scenario_id, generator="FailedTransactionGenerator",
            )
            if scenario_id is not None else None
        )
        insert_tx(
            conn,
            prefix=self.prefix,
            id=self.transaction_id,
            account_id=self.account_id,
            account_name=f"Failed Plant ({self.account_id})",
            account_role=self.account_role,
            account_scope=self.account_scope,
            account_parent_role=self.account_parent_role,
            amount_money=-self.amount,  # Debit attempt
            amount_direction="Debit",
            status="Failed",
            posting=ts(self.anchor_day),
            transfer_id=f"tr-failed-{self.account_id}",
            rail_name=self.rail_name,
            origin="InternalInitiated",
            metadata=metadata,
        )
