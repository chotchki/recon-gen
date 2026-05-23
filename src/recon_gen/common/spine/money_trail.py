"""Money-trail family — recursive-graph L2 invariant.

`MoneyTrailInvariant` reads `<prefix>_inv_money_trail_edges` (the
WITH-RECURSIVE matview walking `transfer_parent_id` from each root
down through descendants). Each row is one edge in a money trail; the
detector projects every edge as a Violation with identity
`(root_transfer_id, transfer_id, depth)`.

What "violation" means here is context-dependent: a single-hop transfer
is a 1-row trail (depth=0), perfectly normal. A 5-hop chain (depth=4)
might be analytically interesting (concentrated value movement through
multiple layers) but not categorically a violation. The actual
threshold for "this trail is suspicious" lives on the View (mirrors
AnomalyInvariant's σ-threshold pattern — AP.3 finding #3). AT.1 ships
the detector returning ALL edges; downstream filtering is the View's
job.

The generator is AT.3 territory (the recursive parent-linked chain
emission — a 3-deep trail per AP.3's MoneyTrailGenerator spike). AT.1
lands just the detector shim. The pattern matches AT.0's anomaly
promotion: detector → empirical-edge discovery → generator promotion.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import ClassVar

from recon_gen.common.spine.violation import Violation


@dataclass(frozen=True)
class MoneyTrailInvariant:
    """Money-trail edge detector. Reads
    `<prefix>_inv_money_trail_edges` and projects every edge as a
    Violation with identity `(root_transfer_id, transfer_id, depth)`.

    Note: returning every edge is intentional — the detector is the
    READ surface; the View decides what's analytically interesting.
    AT.2's σ-threshold View pattern extends here as a depth-threshold
    or value-threshold View knob (AT.3 + AT.6 territory).
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


# AT.3 will land `MoneyTrailGenerator` — a recursive parent-linked
# chain emission (root → child → grandchild, each a 2-leg Posted
# transfer per AP.3's MoneyTrailGenerator spike). Cross-account by
# nature; AS.4's vector-state work is the structural fit.
