"""`LedgerSimulation` — vector-state composition of `AccountSimulation`s.

AS.4. The cross-account generalization AP.2's honest limit pointed at:
multiple accounts, each running their own scalar fold (AS.3), folded
forward together on the same connection so cross-boundary invariants
(`ledger_drift` rolls children into parents) can fire across days.

What this lands:

- `LedgerSimulation` — a vector of `AccountSimulation`s. `emit(conn)`
  runs every account's fold + writes; `violation_trajectory(invariant,
  conn)` carries the cross-account violation set as state day by day.
- A demonstration of cross-boundary propagation: when a child account's
  stored money drifts on day D, the parent account's `ledger_drift`
  fires on every day Σ child.money is off — exactly the
  multi-day extension AS.2's single-day DriftGenerator only hinted at.

What this deliberately does NOT land:

- The full `Transfer` primitive (legs net to zero across accounts via
  the conservation law; a shared transfer_id binding the legs into one
  event). That's AT.3's substrate for money_trail; it's the natural
  shape for cross-account flow but AS.4 doesn't need it to demonstrate
  vector state. When AT.3 lands, LedgerSimulation will likely grow a
  `transfers: list[Transfer]` field that auto-routes the legs into the
  right per-account `AccountSimulation.plans`.

State is the vector of per-account scalar balances; conservation laws
(legs net to zero) are AT.3's. AS.4 proves the vector composition's
the right shape by exercising it on the one cross-account invariant
the L1 surface already carries: `ledger_drift`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from recon_gen.common.spine.account_simulation import AccountSimulation
from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.violation import Violation


@dataclass
class LedgerSimulation:
    """A vector of `AccountSimulation`s sharing one connection.

    Each per-account fold is independent (scalar); the LEDGER's
    cross-account behavior emerges from the matview SQL — e.g.,
    `ledger_drift`'s `Σ child.money` reads every account's
    `_current_daily_balances` row. So vector state here is "many
    scalar folds emitted side by side"; the cross-boundary invariants
    pick up the structural property from the data.

    Per the AS.1 RNG convention: each composed AccountSimulation
    carries its own seeded `rng`. LedgerSimulation doesn't override.
    """

    accounts: list[AccountSimulation] = field(default_factory=list[AccountSimulation])

    def emit(self, conn: sqlite3.Connection) -> None:
        """Write every account's full fold. Commits to the caller — so
        a scenario can compose multiple LedgerSimulations against one
        connection and refresh once at the end (the AP.3 pattern)."""
        for acct in self.accounts:
            acct.emit(conn)

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
