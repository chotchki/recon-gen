"""Ledger drift (law 2) — the parent/child topology axis (DS.0 §3).

Each cell is one parent account with a per-cell-unique role (children
bind to the parent through ``account_parent_role == parent.role``, so
role disjointness IS the packing key for this detector) plus 1-2 child
accounts. Axes:

- parent 2-day emit pattern over {absent, -1, 0, 1} cents (absent
  days exercise the parent-side LOCF carry);
- per-child 2-day emit pattern over {(0, absent), (absent, 0), (1, 1)}
  (the middle one exercises the child-side carry INTO the parent sum:
  a quiet child still holds its position);
- parent direct legs in {none, +1 Posted, +1 Pending} (the direct-
  postings term of the signed law; Pending proves the money status
  law on the parent's own ledger).

Children carry no legs, so a child emitting 1 with no postings is
ALSO a drift violation — the shared evaluator credits those to the
drift detector's expected set (cross-detector packing, deliberate).

The AU.2 parent≡Σ-children composition at unbounded magnitudes is the
∀-ℤ side (DST.2), not enumeration's.
"""
from __future__ import annotations

import datetime as dt
import itertools
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from tests.enumeration.domains import _locf
from tests.enumeration.domains._base import as_date, as_int, as_str
from tests.enumeration.harness import (
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    PackedCell,
    ViolationMap,
)

type EmitPattern = tuple[int | None, ...]

_PARENT_DAY_OPTS: Final[tuple[int | None, ...]] = (None, -1, 0, 1)
_CHILD_PATTERNS: tuple[EmitPattern, ...] = (
    (0, None), (None, 0), (1, 1),
)
_PARENT_LEGS: Final[tuple[tuple[tuple[int, str], ...], ...]] = (
    (), ((1, POSTED_STATUS),), ((1, "Pending"),),
)


def topology_cells(window: tuple[dt.date, ...]) -> list[PackedCell]:
    two_days = window[:2]
    parent_patterns: list[EmitPattern] = [
        pattern
        for pattern in itertools.product(_PARENT_DAY_OPTS, repeat=2)
    ]
    child_configs: list[tuple[EmitPattern, ...]] = [
        (p,) for p in _CHILD_PATTERNS
    ] + [
        (p1, p2)
        for p1 in _CHILD_PATTERNS
        for p2 in _CHILD_PATTERNS
    ]
    cells: list[PackedCell] = []
    index = 0
    for parent_pattern in parent_patterns:
        for children in child_configs:
            for legs in _PARENT_LEGS:
                prefix = f"lg{index:06d}"
                parent_account = f"{prefix}p"
                parent_role = f"lgr{index:06d}"
                b = CellBuilder()
                accounts = [parent_account]
                for di, money in enumerate(parent_pattern):
                    if money is not None:
                        b.balance(
                            account=parent_account, day=two_days[di],
                            money=money, role=parent_role, parent_role=None,
                        )
                for li, (amount, status) in enumerate(legs):
                    leg_id = f"{prefix}x{li}"
                    b.leg(
                        id=leg_id, account=parent_account, amount=amount,
                        status=status,
                        posting=dt.datetime.combine(
                            two_days[0], dt.time(12, 0),
                        ),
                        transfer=leg_id, role=parent_role, parent_role=None,
                    )
                for ci, child_pattern in enumerate(children):
                    child_account = f"{prefix}c{ci}"
                    accounts.append(child_account)
                    for di, money in enumerate(child_pattern):
                        if money is not None:
                            b.balance(
                                account=child_account, day=two_days[di],
                                money=money, parent_role=parent_role,
                            )
                cells.append(PackedCell(
                    *b.rows(),
                    prefixes=(prefix,),
                    expected=_locf.money_family_expected(
                        b.state(), accounts, window,
                    ),
                ))
                index += 1
    return cells


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT account_id, business_day_start, drift "
        f"FROM {db.prefix}_ledger_drift",
    )
    return {
        (as_str(row[0]), as_date(row[1])): as_int(row[2])
        for row in rows
    }


CHECK: Final = DetectorCheck(
    detector="ledger_drift", read_engine=_read_engine,
)
