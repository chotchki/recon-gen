"""Money trail (law 13, derivation) — k-bounded chains + cycle classes.

Expected side is ``expected_trail_edges`` (the DS.1 law) run per cell;
the comparator is edge-SET equality against the engine's DISTINCT
(root, src_account, tgt_account, depth) projection — the symmetric
difference must be empty (``money_trail_residual`` is exactly that
symmetric difference).

This family gets its OWN packed DB: the law is a global walk over the
whole feed, so it packs only with cells that cannot mint edges; a
dedicated DB makes that guarantee structural instead of a scoping
convention (the cardinality cells' zero-amount legs would qualify,
but nothing enforces zero-amount over there).

Cycle classes (DS.0 finding 3, semantics landed at DS.3.1):

- a PURE cycle (no root anchor) never enters the walk — silently
  omitted on BOTH sides (KAT D3 pins this; the residual class is a
  DS-backlog candidate invariant). Packed here.
- a ROOT-REACHABLE cycle walks to MONEY_TRAIL_DEPTH_CAP and the
  refresh tripwire fails LOUDLY — it cannot share a packed refresh,
  and the loud-failure behavior is already pinned red-first by
  ``tests/unit/test_ds31_money_trail_guard.py`` (cross-reference, not
  duplicated here). The packed boundary witness is the depth-cap-
  minus-one chain: exactly MONEY_TRAIL_DEPTH_CAP transfers, deepest
  member at depth cap-1, refresh clean.
"""
from __future__ import annotations

import datetime as dt
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.l2.schema import MONEY_TRAIL_DEPTH_CAP
from recon_gen.common.spine.residuals import expected_trail_edges
from tests.enumeration.domains._base import (
    SPEC_EXAMPLE,
    SPEC_PREFIX,
    WINDOW_START,
    as_int,
    as_str,
)
from tests.enumeration.harness import (
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    PackedCell,
    PackedDomain,
    ViolationMap,
    artifacts_for,
)

_POSTING: Final = dt.datetime.combine(WINDOW_START, dt.time(12, 0))


class _TrailCell:
    """Tiny helper: transfers as (src legs, tgt legs) pairs with
    per-cell-prefixed ids."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.builder = CellBuilder()
        self._leg_n = 0

    def leg(
        self, transfer: str, account: str, amount: int,
        *, parent: str | None = None, status: str = POSTED_STATUS,
        superseded_to: str | None = None,
    ) -> None:
        leg_id = f"{self.prefix}L{self._leg_n}"
        self._leg_n += 1
        self.builder.leg(
            id=leg_id, account=f"{self.prefix}{account}", amount=amount,
            status=status, posting=_POSTING,
            transfer=f"{self.prefix}{transfer}",
            parent=None if parent is None else f"{self.prefix}{parent}",
            rail="TrailRail", parent_role=None,
        )
        if superseded_to is not None:
            self.builder.leg(
                id=leg_id, account=f"{self.prefix}{account}", amount=amount,
                status=superseded_to, posting=_POSTING,
                transfer=f"{self.prefix}{transfer}",
                parent=None if parent is None else f"{self.prefix}{parent}",
                rail="TrailRail", parent_role=None,
            )

    def hop(
        self, transfer: str, src: str, tgt: str,
        *, parent: str | None = None, amount: int = 100,
    ) -> None:
        self.leg(transfer, src, -amount, parent=parent)
        self.leg(transfer, tgt, amount, parent=parent)

    def cell(self) -> PackedCell:
        expected: ViolationMap = {
            (e.root_transfer_id, e.src_account_id, e.tgt_account_id, e.depth):
                None
            for e in expected_trail_edges(self.builder.state())
        }
        return PackedCell(
            *self.builder.rows(), prefixes=(self.prefix,),
            expected={"money_trail": expected},
        )


def _cells() -> list[PackedCell]:
    out: list[PackedCell] = []

    def new(prefix: str) -> _TrailCell:
        return _TrailCell(prefix)

    # Linear chains, length 1-3.
    for length in (1, 2, 3):
        c = new(f"mtl{length:02d}")
        for i in range(length):
            c.hop(f"t{i}", f"a{i}", f"a{i + 1}",
                  parent=None if i == 0 else f"t{i - 1}")
        out.append(c.cell())
    # Root-only single-leg transfer: no src x tgt pair, no edges.
    c = new("mtsl0")
    c.leg("t0", "a0", 100)
    out.append(c.cell())
    # Multi-src and multi-tgt cross products within one transfer.
    c = new("mtms0")
    c.leg("t0", "a0", -60)
    c.leg("t0", "a1", -40)
    c.leg("t0", "a2", 100)
    out.append(c.cell())
    c = new("mtmt0")
    c.leg("t0", "a0", -100)
    c.leg("t0", "a1", 60)
    c.leg("t0", "a2", 40)
    out.append(c.cell())
    # Non-Posted legs must not pair into edges (money moves on Posted).
    c = new("mtnp0")
    c.leg("t0", "a0", -100, status="Pending")
    c.leg("t0", "a1", 100)
    out.append(c.cell())
    c = new("mtnp1")
    c.leg("t0", "a0", -100)
    c.leg("t0", "a1", 100, status="Zq9x")
    out.append(c.cell())
    # Single-leg CHAIN MEMBER: contributes no edge but the walk passes
    # through it (its child still anchors to the root).
    c = new("mtpt0")
    c.hop("t0", "a0", "a1")
    c.leg("t1", "a1", 100, parent="t0")
    c.hop("t2", "a1", "a2", parent="t1")
    out.append(c.cell())
    # Fork: one root, two children.
    c = new("mtfk0")
    c.hop("t0", "a0", "a1")
    c.hop("t1", "a1", "a2", parent="t0")
    c.hop("t2", "a1", "a3", parent="t0")
    out.append(c.cell())
    # PURE 2-cycle + healthy root sibling: cycle members never enter
    # the walk (silent on both sides), the root stays intact.
    c = new("mtcy0")
    c.hop("t0", "a0", "a1")
    c.hop("p", "x0", "x1", parent="q")
    c.hop("q", "x1", "x0", parent="p")
    out.append(c.cell())
    # Self-cycle (transfer claiming itself as parent).
    c = new("mtcy1")
    c.hop("t0", "a0", "a1")
    c.hop("s", "x0", "x1", parent="s")
    out.append(c.cell())
    # Dangling parent reference: dropped from the walk on both sides.
    c = new("mtdg0")
    c.hop("t0", "a0", "a1", parent="ghost")
    out.append(c.cell())
    # Supersession (DS.3.3b): a child's only tgt leg corrected to
    # Failed — the CURRENT row wins, the stale edge must vanish.
    c = new("mtsu0")
    c.hop("t0", "a0", "a1")
    c.leg("t1", "a1", -100, parent="t0")
    c.leg("t1", "a2", 100, parent="t0", superseded_to="Failed")
    out.append(c.cell())
    # Depth-cap boundary: exactly MONEY_TRAIL_DEPTH_CAP transfers —
    # deepest member sits at depth cap-1, refresh must stay clean
    # (cap-exceeding chains trip the DS.3.1 tripwire, pinned in
    # test_ds31_money_trail_guard.py).
    c = new("mtdc0")
    for i in range(MONEY_TRAIL_DEPTH_CAP):
        c.hop(f"t{i:02d}", f"a{i:02d}", f"a{i + 1:02d}",
              parent=None if i == 0 else f"t{i - 1:02d}")
    out.append(c.cell())
    return out


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT DISTINCT root_transfer_id, source_account_id, "
        f"target_account_id, depth FROM {db.prefix}_inv_money_trail_edges",
    )
    return {
        (as_str(row[0]), as_str(row[1]), as_str(row[2]), as_int(row[3])):
            None
        for row in rows
    }


CHECK: Final = DetectorCheck(detector="money_trail", read_engine=_read_engine)


def build_money_trail_domain() -> PackedDomain:
    return PackedDomain(
        name="money_trail",
        artifacts=artifacts_for(SPEC_EXAMPLE, prefix=SPEC_PREFIX),
        cells=tuple(_cells()),
        checks=(CHECK,),
    )
