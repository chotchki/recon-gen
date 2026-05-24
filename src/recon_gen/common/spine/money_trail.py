"""Money-trail family — recursive-graph L2 invariant + chain generator.

`MoneyTrailInvariant` reads `<prefix>_inv_money_trail_edges` (the
WITH-RECURSIVE matview walking `transfer_parent_id` from each root
down through descendants). Each row is one edge in a money trail; the
detector projects every edge as a Violation with identity
`(root_transfer_id, transfer_id, depth)`.

What "violation" means here is context-dependent: a single-hop transfer
is a 1-row trail (depth=0), perfectly normal. A 5-hop chain (depth=4)
might be analytically interesting (concentrated value movement through
multiple layers) but not categorically a violation. The actual
threshold for "this trail is suspicious" lives on the **View** (mirrors
`AnomalyView`'s σ-threshold pattern — AP.3 finding #3). `MoneyTrailView`
owns the `min_depth` knob; the detector returns ALL edges.

AT.3 lands `MoneyTrailGenerator` — a parent-linked chain emission
(root → child → grandchild, each a 2-leg Posted transfer per AP.3's
spike). Cross-account: each hop's recipient = next hop's sender, so
money walks through the chain. Built on the AT.3 `Transfer` /
`LedgerSimulation` primitive (same shape `AnomalyGenerator` refactored
onto).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import (
    find_internal_with_role,
    load_spec_example,
)
from recon_gen.common.spine.ledger_simulation import (
    LedgerSimulation,
    Transfer,
    TransferLeg,
)
from recon_gen.common.spine.violation import Violation


@dataclass(frozen=True)
class MoneyTrailInvariant:
    """Money-trail edge detector. Reads
    `<prefix>_inv_money_trail_edges` and projects every edge as a
    Violation with identity `(root_transfer_id, transfer_id, depth)`.

    The detector is bucket-agnostic — every edge is returned; the
    `MoneyTrailView` (this module) slices on `min_depth` for analyst-
    facing "suspicious chain" thresholds. Mirrors AT.2's
    `AnomalyInvariant` ⋈ `AnomalyView` shape.
    """

    name: ClassVar[str] = "inv_money_trail_edges"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT root_transfer_id, transfer_id, depth "
            f"FROM {self.prefix}_inv_money_trail_edges",
        ).fetchall()
        return {
            Violation.of(
                "inv_money_trail_edges",
                root_transfer_id=str(root),
                transfer_id=str(tid),
                depth=int(d),
            )
            for root, tid, d in rows
        }

    def scenario_for(
        self,
        hop_role: str,
        *,
        chain_length: int = 3,
        amount: float = 100.0,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
        chain_id_prefix: str | None = None,
    ) -> "MoneyTrailGenerator":
        """Resolve the chain's account role + return a generator that
        plants a `chain_length`-deep parent-linked chain. Every account
        in the chain uses `hop_role` (the role of the leaf-eligible
        internal accounts the matview walks).

        `chain_length` = number of transfers in the chain (depth runs
        0..N-1). chain_length=1 = single transfer (no parent linkage,
        depth=0 only). chain_length=3 = root → child → grandchild
        (depths 0/1/2).

        Each transfer's recipient leg becomes the next transfer's
        sender — money walks through a chain of `chain_length + 1`
        distinct accounts. The matview surfaces each transfer as one
        edge (one source-leg × one target-leg per transfer).

        AY.4.c — `chain_id_prefix` overrides the default synthetic ID
        prefix. Unlike the single-account spine factories, money_trail
        plants `chain_length + 1` account_ids + `chain_length`
        transfer_ids; the natural disambiguator is a chain-wide prefix
        feeding both `_account_id(i)` and `_transfer_id(i)`. The plant
        adapter (AY.4.c.3) threads OLD money-trail chain identifiers
        through this kwarg so N plants on the same `hop_role` produce
        N distinct generators (the default `"acct-money-trail-hop"` /
        `"xfer-money-trail"` derivations would collide). Existing test
        callers can pass nothing → preserves the synthetic defaults
        byte-stable.
        """
        if chain_length < 1:
            raise ValueError(
                f"chain_length must be ≥ 1; got {chain_length}",
            )
        inst = instance if instance is not None else load_spec_example()
        # Every account in the chain is the same role — the
        # matview's parent_role IS NOT NULL filter applies to recipient
        # legs, so we need leaf-eligible accounts throughout.
        acct = find_internal_with_role(
            inst, hop_role, must_be_leaf=True,
            error_kind="money-trail hop",
        )
        assert acct.parent_role is not None  # must_be_leaf guarantees
        return MoneyTrailGenerator(
            hop_account_role=hop_role,
            hop_account_parent_role=acct.parent_role,
            chain_length=chain_length,
            amount=amount,
            anchor_day=anchor_day,
            chain_id_prefix=chain_id_prefix or "money-trail",
        )


@dataclass(frozen=True)
class MoneyTrailView:
    """Analyst-facing slice over the money-trail detector's full output.

    Holds the `min_depth` knob for "include edges whose chain depth is
    at-or-above this value". `min_depth=0` (default) includes every
    edge (matches the detector's full return). `min_depth=1` drops the
    root edge of every chain; `min_depth=2` keeps only grandchild-
    and-deeper edges (the "this is a chain, not a one-off" view).

    Pure (no IO; deterministic on its inputs); the detector still does
    the SQL read. Mirrors `AnomalyView` — the View pattern is the same
    for every L2 invariant.
    """

    min_depth: int = 0

    def slice(self, violations: set[Violation]) -> set[Violation]:
        """Return the subset of `violations` whose `depth` is
        ≥ `min_depth`. Violations missing the `depth` key are dropped
        silently (defensive against cross-invariant mix)."""
        out: set[Violation] = set()
        for v in violations:
            depth = dict(v.identity).get("depth")
            if depth is None:
                continue
            if int(depth) >= self.min_depth:
                out.add(v)
        return out


@dataclass
class MoneyTrailGenerator:
    """Plant a parent-linked chain of `chain_length` Posted transfers.

    Each transfer is a 2-leg balanced send: account[i] → account[i+1].
    Transfer[i+1].parent_transfer_id = Transfer[i].transfer_id (the
    chain linkage the matview walks recursively). All transfers post
    on consecutive days from `anchor_day` (anchor_day, +1d, +2d, ...)
    so the matview's `posted_at` ordering matches chain depth.

    Per AP.3 finding #2 generalized: graph invariants are multi-row by
    nature — N transfers + N+1 accounts per chain. Single-row plant
    would yield depth=0 only (not a "chain" in any analytically
    meaningful sense). The generator's `emit()` writes ALL the rows in
    one call through a transfers-only `LedgerSimulation`.

    Single-edge property: no `AccountSimulation` folds → no balance
    rows → no drift trip from the chain. Matches `AnomalyGenerator`'s
    AT.0-finding.
    """

    hop_account_role: str
    hop_account_parent_role: str
    chain_length: int
    amount: float
    anchor_day: date
    prefix: str = "spec_example"
    #: AY.4.c — disambiguator threaded into `_account_id` /
    #: `_transfer_id` so N money-trail plants on the same hop_role
    #: produce N distinct chains. Defaults to the legacy
    #: `"money-trail"` value → existing test callers stay byte-stable.
    chain_id_prefix: str = "money-trail"

    @property
    def intended(self) -> Violation:
        """The deepest edge of the chain — the "story" of the trail.
        For chain_length=3, depth=2 (grandchild) is the most-removed-
        from-root edge, the analyst-meaningful endpoint.

        Violation identity also includes `transfer_id` + `root_transfer_id`,
        both deterministic from this generator's account/chain
        configuration."""
        leaf_transfer_id = self._transfer_id(self.chain_length - 1)
        root_transfer_id = self._transfer_id(0)
        return Violation.of(
            "inv_money_trail_edges",
            root_transfer_id=root_transfer_id,
            transfer_id=leaf_transfer_id,
            depth=self.chain_length - 1,
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """The chain_length+1 hop account_ids the chain walks through.
        ``account[0]`` is the chain's root sender; ``account[N]`` is
        the leaf recipient. Used by AV.5 ``ScenarioContext.compose``
        to catch cross-generator collisions at the wiring site."""
        return frozenset(
            self._account_id(i) for i in range(self.chain_length + 1)
        )

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        LedgerSimulation(
            transfers=list(self._transfers()),
            prefix=self.prefix,
        ).emit(conn, scenario_id=scenario_id)

    def _transfers(self) -> list[Transfer]:
        """Build the chain as `Transfer`s. Pure (no IO) — composable
        for callers that compose money_trail with other generators."""
        out: list[Transfer] = []
        for i in range(self.chain_length):
            sender_id = self._account_id(i)
            recipient_id = self._account_id(i + 1)
            parent = self._transfer_id(i - 1) if i > 0 else None
            day = self.anchor_day + timedelta(days=i)
            out.append(Transfer(
                day=day,
                transfer_id=self._transfer_id(i),
                rail_name="ach",
                status="Posted",
                parent_transfer_id=parent,
                legs=(
                    TransferLeg(
                        account_id=sender_id,
                        amount=-self.amount,
                        account_name=f"Money Trail Hop {i} Source",
                        account_role=self.hop_account_role,
                        account_scope="internal",
                        account_parent_role=self.hop_account_parent_role,
                    ),
                    TransferLeg(
                        account_id=recipient_id,
                        amount=self.amount,
                        account_name=f"Money Trail Hop {i} Target",
                        account_role=self.hop_account_role,
                        account_scope="internal",
                        account_parent_role=self.hop_account_parent_role,
                    ),
                ),
            ))
        return out

    def _account_id(self, index: int) -> str:
        return f"acct-{self.chain_id_prefix}-hop-{index}"

    def _transfer_id(self, index: int) -> str:
        return f"xfer-{self.chain_id_prefix}-{index}"
