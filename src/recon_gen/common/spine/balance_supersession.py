"""Balance-supersession family — spine generator only (no Invariant).

DY.7.1 sibling of ``supersession.py``. Where ``SupersessionGenerator``
supersedes a TRANSACTION (two ``_transactions`` rows sharing one logical
``id``), this supersedes a DAILY BALANCE — two ``_daily_balances`` rows
for one ``(account_id, business_day_start)``, the second carrying
``supersedes='TechnicalCorrection'`` + the restated ``money``. That's the
exact shape the M.2b.12 Supersession Audit's ``Daily Balances Audit``
table detects (``COUNT(*) OVER (PARTITION BY account_id, business_day_
start) > 1`` + ``has_supersede``); NO generator emitted it before, so the
audit table was empty on every seed and the DL.2 cross-sheet-drill
guardrail could never exercise the balances-audit → daily-statement drill.

Drift-safe by construction. The generator emits its OWN posted
transaction for the account-day, and the CORRECTION balance's ``money``
equals that transaction's amount — so ``current_daily_balances`` (which
picks MAX(entry) = the correction) matches ``computed_subledger_balance``
(the SUM of the account's own postings) and drift is ZERO. The ORIGINAL
(superseded, lower-entry) row carries the WRONG ``money`` but never
reaches ``current_daily_balances``, so no invariant sees it.

Self-contained + isolated: it plants a DEDICATED account_id (not one of
the scenario's cust1/cust2 invariant targets), so it perturbs no existing
plant. The account is a leaf (``account_parent_role`` set) whose balance
matches its subledger → drift 0; sparse cadence with activity + a balance
row on the same day → no cadence gap; positive balance → no overdraft;
``expected_eod_balance = money`` → no EOD breach. It surfaces in exactly
one place: the Daily Balances Audit trail.

Registers in ``INVARIANT_GENERATOR_EDGES`` + ``ALL_AUDIT_FIXTURE_
GENERATORS`` with an empty invariant tuple — same AY.2.b audit-fixture
treatment as ``SupersessionGenerator`` (no matview surfaces it as a
violation; the ``intended`` ``AuditFixture`` is the gate discriminator).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from recon_gen.common.db import SyncConnection
from recon_gen.common.l2.primitives import (
    ORIGIN_INTERNAL_INITIATED,
    POSTED_STATUS,
    SUPERSEDE_TECHNICAL_CORRECTION,
    Scope,
)
from recon_gen.common.spine._emit_helpers import (
    DB_COLS,
    day_bounds,
    insert_balance,
    insert_tx,
    ts,
)
from recon_gen.common.spine.violation import AuditFixture


@dataclass
class BalanceSupersessionGenerator:
    """Plant a superseded DAILY BALANCE — two ``_daily_balances`` rows for
    one ``(account_id, business_day_start)`` plus the posting that makes
    the correction drift-free.

    ``corrected_money`` is the restated (correct) end-of-day balance AND
    the amount of the single posting the generator emits, so the account's
    subledger equals the correction → drift 0. ``original_money`` is the
    superseded (wrong) balance the correction replaces.
    """

    account_id: str
    account_name: str
    account_role: str
    account_scope: Scope
    account_parent_role: str | None
    rail_name: str
    original_money: float
    corrected_money: float
    anchor_day: date
    prefix: str = "spec_example"

    @property
    def transfer_id(self) -> str:
        """Day-scoped id for the posting leg — each densify replica is its
        own event (mirrors ``SupersessionGenerator.transfer_id``, DR.7.a)."""
        return f"tr-bal-supersedes-{self.account_id}-{self.anchor_day:%Y%m%d}"

    @property
    def transaction_id(self) -> str:
        return f"tx-bal-supersedes-{self.account_id}-{self.anchor_day:%Y%m%d}"

    @property
    def intended(self) -> AuditFixture:
        """The audit-PDF balance-supersession entry's identity — keyed on
        the account + day + the corrected balance (the "current" value)."""
        return AuditFixture.of(
            "balance_supersession",
            account_id=self.account_id,
            business_day=self.anchor_day.isoformat(),
            corrected_money=round(self.corrected_money, 2),
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
            scenario_id, generator="BalanceSupersessionGenerator",
        )
        bstart, bend = day_bounds(self.anchor_day)

        # (1) The posting that makes the correction drift-free — one
        # Posted Credit for `corrected_money` so the account's subledger
        # (SUM of its own postings) equals the corrected balance. Also
        # gives the Daily-Statement drill destination a row to render.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=self.transaction_id,
            account_id=self.account_id,
            account_name=self.account_name,
            account_role=self.account_role,
            account_scope=self.account_scope,
            account_parent_role=self.account_parent_role,
            amount_money=self.corrected_money,
            amount_direction="Credit",
            status=POSTED_STATUS,
            posting=ts(self.anchor_day, hour=9),
            transfer_id=self.transfer_id,
            rail_name=self.rail_name,
            origin=ORIGIN_INTERNAL_INITIATED,
            metadata=metadata,
        )

        # (2) + (3) The superseded balance trail. Single-row insert_balance
        # with the DB_COLS + ('supersedes',) column shape (the fixed DB_COLS
        # excludes supersedes) — single-row `execute` so it works against
        # the dry-run capture cursor the byte-identical seed path uses
        # (bulk_insert_balance's PG/Oracle path calls executemany, absent on
        # that cursor). The ORIGINAL carries the WRONG money + no supersede
        # reason; the CORRECTION carries the right money (= subledger) +
        # supersedes='TechnicalCorrection' and lands at a higher `entry`
        # (inserted second), so current_daily_balances picks it → drift 0.
        cols = (*DB_COLS, "supersedes")

        def _emit_balance(money: float, supersedes: str | None) -> None:
            insert_balance(
                conn,
                prefix=self.prefix,
                columns=cols,
                account_id=self.account_id,
                account_name=self.account_name,
                account_role=self.account_role,
                account_scope=self.account_scope,
                account_parent_role=self.account_parent_role,
                expected_eod_balance=self.corrected_money,
                business_day_start=bstart,
                business_day_end=bend,
                money=money,
                metadata=metadata,
                supersedes=supersedes,
            )

        _emit_balance(self.original_money, None)
        _emit_balance(self.corrected_money, SUPERSEDE_TECHNICAL_CORRECTION)
