"""`LedgerSimulation` — vector-state composition of `AccountSimulation`s
plus the AT.3 `Transfer` primitive for cross-account flow.

AS.4 landed the vector-of-scalar-folds shape: each `AccountSimulation`
steps its own balance forward independently, the cross-boundary
invariants (`ledger_drift`'s Σ child.money roll-up) emerge from the
matview SQL. AT.3 adds the `Transfer` primitive — multi-leg events that
share a `transfer_id` (and optionally chain via `parent_transfer_id`).

What lives here:

- `AccountSimulation` composition — the AS.4 surface; emit runs each
  account's fold, `violation_trajectory` carries the cross-account
  violation set as state day by day.
- `TransferLeg` + `Transfer` (AT.3) — the cross-account flow
  primitive. Each `Transfer` is one event (one `transfer_id`); legs
  are per-account amounts that sum to zero for a fully-balanced
  Posted transfer (the conservation law). Single-leg or unbalanced
  transfers are representable — useful for external arrivals and
  Pending entries.
- `LedgerSimulation.transfers` (AT.3) — emitted alongside (or instead
  of) account folds. Anomaly's pair-shaped plant is "transfers only,
  no folds" (no balance rows, single-edge to anomaly per AT.0's
  finding). Money_trail's recursive chain is "transfers with
  `parent_transfer_id` linkage".

What this deliberately does NOT do:

- Auto-route transfer legs into per-account `DayPlan.legs`. The two
  flow shapes — `AccountSimulation.plans` (account-as-source-of-truth)
  vs `Transfer.legs` (transfer-as-source-of-truth) — are kept
  side-by-side. A scenario that wants both (transfers + balance rows
  matching the transfer-induced sums) composes both. The matview SQL
  is the consistency contract, not the in-process emit logic.

State is the vector of per-account scalar balances PLUS the set of
emitted transfers; conservation laws apply per-transfer (legs sum to
zero across accounts) but are not enforced at construction — the matview
is the truth-source, the generator is honest.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from recon_gen.common.spine._emit_helpers import insert_tx, ts
from recon_gen.common.spine.account_simulation import AccountSimulation
from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.violation import Violation


# ---------------------------------------------------------------------------
# Transfer primitive (AT.3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransferLeg:
    """One leg of a `Transfer` — money in or out of one account.

    `amount > 0` = money IN (Credit), `< 0` = money OUT (Debit) — the
    project sign convention. The denormalized account-side fields
    (name, role, scope, parent_role) MUST be on each leg because
    `_transactions` is the source-of-truth table for the matviews; the
    matview SQL doesn't JOIN to a separate account dimension.
    """

    account_id: str
    amount: float
    account_name: str
    account_role: str
    account_scope: Literal["internal", "external"]
    account_parent_role: str | None = None


@dataclass(frozen=True)
class Transfer:
    """A multi-leg money-movement event sharing one ``transfer_id``.

    The double-entry invariant: ``sum(leg.amount for leg in legs) == 0``
    for a fully-balanced Posted transfer. NOT enforced at construction —
    Pending transfers commonly carry only one leg until they post; the
    matview SQL filters on Posted + matched legs, so an unbalanced
    transfer is harmless (it just doesn't surface). The
    `is_balanced` helper exposes the check for callers that want it.

    `parent_transfer_id` chains transfers into trails — money_trail's
    matview walks this recursively (each Posted multi-leg transfer
    becomes one edge per chain hop).
    """

    day: date
    transfer_id: str
    rail_name: str
    legs: tuple[TransferLeg, ...]
    status: Literal["Posted", "Pending"] = "Posted"
    parent_transfer_id: str | None = None
    origin: str = "etl"
    #: Hour of day used for the ``posting`` timestamp on each leg.
    #: Defaults to noon so each leg lands inside the day's balance
    #: window (00:00 ≤ posting < 24:00). Mirrors `ts()`'s convention.
    hour: int = 12

    def is_balanced(self) -> bool:
        """``True`` iff ``sum(leg.amount) == 0`` — the double-entry
        conservation law. Used by callers (incl. tests) that want to
        verify a transfer is fully-resolved; the constructor doesn't
        enforce it because Pending single-leg transfers are valid
        intermediate state."""
        return sum(leg.amount for leg in self.legs) == 0


@dataclass
class LedgerSimulation:
    """A vector of `AccountSimulation`s sharing one connection, plus an
    optional list of cross-account `Transfer`s.

    Each per-account fold is independent (scalar); the LEDGER's
    cross-account behavior emerges from the matview SQL — e.g.,
    `ledger_drift`'s `Σ child.money` reads every account's
    `_current_daily_balances` row. So vector state here is "many
    scalar folds emitted side by side"; the cross-boundary invariants
    pick up the structural property from the data.

    `transfers` (AT.3) is the cross-account FLOW dimension —
    transfer-shaped emissions (multi-leg, shared `transfer_id`, optional
    `parent_transfer_id` for chains). Anomaly's pair plant is "transfers
    only, no accounts" (no balance rows → single-edge to anomaly).
    Money_trail's recursive chain is "transfers with parent linkage".
    Drift-style scenarios continue to use the account-fold shape; a
    scenario that wants both composes both fields.

    Per the AS.1 RNG convention: each composed AccountSimulation
    carries its own seeded `rng`. LedgerSimulation doesn't override.
    """

    accounts: list[AccountSimulation] = field(default_factory=list[AccountSimulation])
    transfers: list[Transfer] = field(default_factory=list[Transfer])
    #: Prefix for the `<prefix>_transactions` table when emitting
    #: transfers. For account-only ledgers this is unused (each
    #: AccountSimulation carries its own prefix). For transfer-only
    #: ledgers (anomaly's shape) this is the source of truth.
    prefix: str = "spec_example"

    def emit(self, conn: sqlite3.Connection) -> None:
        """Write every account's full fold AND every transfer's legs.
        Commits to the caller — so a scenario can compose multiple
        LedgerSimulations against one connection and refresh once at
        the end (the AP.3 pattern)."""
        for acct in self.accounts:
            acct.emit(conn)
        for transfer in self.transfers:
            self._emit_transfer(conn, transfer)

    def _emit_transfer(
        self, conn: sqlite3.Connection, transfer: Transfer,
    ) -> None:
        """Write one transfer's legs as `_transactions` rows. Per-leg
        denormalized account fields come from `TransferLeg`; the
        transfer-level fields (transfer_id, parent, rail, status,
        posting) come from `Transfer`."""
        posting = ts(transfer.day, hour=transfer.hour)
        for i, leg in enumerate(transfer.legs):
            direction = "Credit" if leg.amount >= 0 else "Debit"
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-{transfer.transfer_id}-leg{i}",
                account_id=leg.account_id,
                account_name=leg.account_name,
                account_role=leg.account_role,
                account_scope=leg.account_scope,
                account_parent_role=leg.account_parent_role,
                amount_money=leg.amount,
                amount_direction=direction,
                status=transfer.status,
                posting=posting,
                transfer_id=transfer.transfer_id,
                transfer_parent_id=transfer.parent_transfer_id,
                rail_name=transfer.rail_name,
                origin=transfer.origin,
            )

    def violation_trajectory(
        self,
        invariant: Invariant,
        conn: sqlite3.Connection,
    ) -> list[set[Violation]]:
        """Per-day violation snapshots, like AccountSimulation's but
        emitting all accounts' rows for day i BEFORE refresh+detect.

        Assumes every AccountSimulation in the ledger has the SAME
        number of `plans` (one DayPlan per ledger-day). Heterogeneous
        timelines would need a different fold; AT.x extensions can add
        a sparser shape when there's a use case.
        """
        from recon_gen.common.l2.loader import load_instance
        from recon_gen.common.l2.schema import refresh_matviews_sql
        from recon_gen.common.sql import Dialect
        from recon_gen.common.db import execute_script
        from pathlib import Path

        if not self.accounts:
            return []

        n_days = len(self.accounts[0].plans)
        if any(len(a.plans) != n_days for a in self.accounts):
            raise ValueError(
                "LedgerSimulation.violation_trajectory requires every "
                "composed AccountSimulation to have the same number of "
                "plans (one DayPlan per ledger-day)"
            )

        # Fold each account's per-day emissions once; we'll interleave
        # them day-by-day below.
        per_account_emissions = [
            (acct, acct._fold()) for acct in self.accounts  # noqa: SLF001
        ]

        # Caller passes a connection already configured with the
        # schema for `self.accounts[0].prefix`'s L2 instance; we look
        # up the refresh SQL from `spec_example` for parity with
        # AccountSimulation's default. AT will pass an explicit
        # instance path when scoping out.
        repo_root = Path(__file__).resolve().parents[4]
        instance = load_instance(
            repo_root / "tests" / "l2" / "spec_example.yaml",
        )
        prefix = self.accounts[0].prefix

        snapshots: list[set[Violation]] = []
        for i in range(n_days):
            for acct, emissions in per_account_emissions:
                acct._emit_day(conn, emissions[i])  # noqa: SLF001
            conn.commit()
            cur = conn.cursor()
            execute_script(
                cur,
                refresh_matviews_sql(
                    instance, prefix=prefix, dialect=Dialect.SQLITE,
                ),
                dialect=Dialect.SQLITE,
            )
            conn.commit()
            snapshots.append(invariant.detect(conn))
        return snapshots
