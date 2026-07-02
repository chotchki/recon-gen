"""Multi-XOR (law 11) — the DS.0 §6.2 exhaustive domain, ported.

Two chain kinds (config-injection lemma B: the parent can be a RAIL
or a TEMPLATE, and the emitted parent_firings UNION reads both arms):

- rail-parent: BulkAccrualSettlement → {SettleACH, SettleWire};
- template-parent: DisbursementCycle (fired via its
  DisbursementAccrual leg rail) → {SettleACH, SettleCheck}.

Cell = parent-leg multiset ≤2 over (status × day) — the day axis is
the DS.3.3a day-multiplication witness class: a parent whose legs
straddle midnight must count each fired sibling name ONCE — crossed
with per-sibling child multisets ≤2 over {Posted, Failed, Zq9x}
(each element = one child transfer, single leg on that sibling rail)
and an optional non-member extra child whose rail is deliberately the
OTHER chain's declared sibling (probes the correlated-EXISTS
membership check for cross-chain precision).

The spike measured 27,000 cells; its spec-reading residual diverged
on 4,914 of them against the pre-DS.3.3a SQL. With the fix landed,
the canonical ``multi_xor_residual`` must match everywhere.
"""
from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.spine.residuals import multi_xor_residual
from tests.enumeration.domains._base import (
    WINDOW_START,
    BoundaryProfile,
    as_int,
    as_str,
)
from tests.enumeration.harness import (
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    PackedCell,
    ViolationMap,
)

_STATUSES: Final = (POSTED_STATUS, "Pending", "Failed", "Zq9x")
_CHILD_STATUSES: Final = (POSTED_STATUS, "Failed", "Zq9x")
_DAYS: Final = (WINDOW_START, WINDOW_START + dt.timedelta(days=1))

type ParentAtom = tuple[str, int]


@dataclass(frozen=True, slots=True)
class _ChainKind:
    parent_name: str
    parent_rail: str
    parent_template: str | None
    siblings: tuple[str, str]
    non_member: str


_KINDS: Final[tuple[_ChainKind, ...]] = (
    _ChainKind(
        parent_name="BulkAccrualSettlement",
        parent_rail="BulkAccrualSettlement", parent_template=None,
        siblings=("BulkAccrualSettleACH", "BulkAccrualSettleWire"),
        non_member="DisbursementSettleACH",
    ),
    _ChainKind(
        parent_name="DisbursementCycle",
        parent_rail="DisbursementAccrual",
        parent_template="DisbursementCycle",
        siblings=("DisbursementSettleACH", "DisbursementSettleCheck"),
        non_member="BulkAccrualSettleWire",
    ),
)


def _multisets[T](atoms: tuple[T, ...], max_n: int) -> list[tuple[T, ...]]:
    out: list[tuple[T, ...]] = [()]
    for n in range(1, max_n + 1):
        out.extend(itertools.combinations_with_replacement(atoms, n))
    return out


def _posting(day_index: int) -> dt.datetime:
    return dt.datetime.combine(_DAYS[day_index], dt.time(12, 0))


def cells(profile: BoundaryProfile) -> list[PackedCell]:
    for kind in _KINDS:
        declared = profile.multi_xor_children.get(kind.parent_name)
        assert declared == frozenset(kind.siblings), (
            f"spec_example's multi-xor chain {kind.parent_name} moved: "
            f"resolved {declared!r}, domain expects {kind.siblings!r}"
        )
    parent_atoms: tuple[ParentAtom, ...] = tuple(
        (status, day) for status in _STATUSES for day in (0, 1)
    )
    parent_multisets = _multisets(parent_atoms, 2)
    child_multisets = _multisets(_CHILD_STATUSES, 2)
    out: list[PackedCell] = []
    index = 0
    for kind in _KINDS:
        for parent_legs in parent_multisets:
            for legs_a in child_multisets:
                for legs_b in child_multisets:
                    for extra in (None, POSTED_STATUS, "Failed"):
                        prefix = f"mx{index:06d}"
                        account = f"{prefix}a"
                        b = CellBuilder()
                        for n, (status, day) in enumerate(parent_legs):
                            b.leg(
                                id=f"{prefix}pL{n}", account=account,
                                amount=0, status=status,
                                posting=_posting(day), transfer=prefix,
                                rail=kind.parent_rail,
                                template=kind.parent_template,
                                parent_role=None,
                            )
                        for slot, (rail, statuses) in enumerate(
                            zip(kind.siblings, (legs_a, legs_b)),
                        ):
                            for j, status in enumerate(statuses):
                                child = f"{prefix}c{slot}{j}"
                                b.leg(
                                    id=f"{child}L0", account=account,
                                    amount=0, status=status,
                                    posting=_posting(0), transfer=child,
                                    parent=prefix, rail=rail,
                                    parent_role=None,
                                )
                        if extra is not None:
                            child = f"{prefix}cx"
                            b.leg(
                                id=f"{child}L0", account=account, amount=0,
                                status=extra, posting=_posting(0),
                                transfer=child, parent=prefix,
                                rail=kind.non_member, parent_role=None,
                            )
                        residual = multi_xor_residual(
                            b.state(), prefix, kind.parent_name,
                            frozenset(kind.siblings),
                        )
                        expected: ViolationMap = {}
                        if residual is not None and residual != 0:
                            expected[(prefix, kind.parent_name)] = residual
                        out.append(PackedCell(
                            *b.rows(), prefixes=(prefix,),
                            expected={"multi_xor": expected},
                        ))
                        index += 1
    return out


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT parent_transfer_id, parent_rail_or_template_name, "
        f"child_count - 1 FROM {db.prefix}_multi_xor_violation",
    )
    return {
        (as_str(row[0]), as_str(row[1])): as_int(row[2])
        for row in rows
    }


CHECK: Final = DetectorCheck(detector="multi_xor", read_engine=_read_engine)
