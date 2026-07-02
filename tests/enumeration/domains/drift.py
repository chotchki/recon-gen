"""Drift (law 1) — the money-family cell grids + engine reader.

Three sub-grids, all riding the shared LOCF packing contract (the
overdraft / expected_eod detectors read the SAME cells — DS.0 §3:
they "ride the money domain"):

- PRIMARY (the DS.0 §6.1 CI domain): per day, balance option in
  ``{absent} ∪ {(money, expected)}`` for money in {-1, 0, 1} cents ×
  expected in {None, 0}, crossed with leg multisets of size ≤2 over
  (amount {-1, 0, 1} × status {Posted, Pending}) posted at noon. Two
  days cross-multiplied. Small integer amounts are sufficient: the
  detector SQL is linear in amounts, and the ∀-ℤ magnitude quantifier
  is DST.1's job, not enumeration's.
- BOUNDARY (the mutant-B lesson — a noon-only domain MISSES a
  ``<=`` → ``<`` mutation on ``posting <= business_day_end``): legs
  AT the day-end cutoff (23:59:59, the ``c`` witness) and one second
  before it (23:59:58, the ``c-1`` witness); the ``c+1`` side is the
  next-day-midnight leg the 2-day cross product already carries.
- SUPERSESSION (the DS.0 §5 48-cell lemma domain): 1-2 balance emits
  on one (account, day) — later entry wins — crossed with one
  optional Posted leg; exercises the Current* argmax under drift.

The nightly tier widens the window to 3 days and adds a reduced-axis
3-day grid (full 3-day cross of the 196 day-options is ~7.5M cells —
deliberately out of scope; the reduced grid keeps per-day options at
28 so the 3-day cross stays enumerable).
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

# (amount, status, time-of-day) leg atoms.
type LegAtom = tuple[int, str, dt.time]
# Balance option: None (no emit) or (money, expected_eod).
type BalOpt = tuple[int, int | None] | None
# One day's state: (balance option, leg multiset).
type DayOpt = tuple[BalOpt, tuple[LegAtom, ...]]

_NOON: Final = dt.time(12, 0)

PRIMARY_BAL_OPTS: Final[tuple[BalOpt, ...]] = (None,) + tuple(
    (m, e) for m in (-1, 0, 1) for e in (None, 0)
)
PRIMARY_LEG_ATOMS: Final[tuple[LegAtom, ...]] = tuple(
    (a, s, _NOON) for a in (-1, 0, 1) for s in (POSTED_STATUS, "Pending")
)

BOUNDARY_BAL_OPTS: Final[tuple[BalOpt, ...]] = (None,) + tuple(
    (m, None) for m in (-1, 0, 1)
)
BOUNDARY_LEG_ATOMS: Final[tuple[LegAtom, ...]] = tuple(
    (a, POSTED_STATUS, t)
    for a in (-1, 1)
    for t in (dt.time(23, 59, 58), dt.time(23, 59, 59))
)

NIGHTLY_LEG_MAX: Final = 1


def _multisets[T](atoms: tuple[T, ...], max_n: int) -> list[tuple[T, ...]]:
    out: list[tuple[T, ...]] = [()]
    for n in range(1, max_n + 1):
        out.extend(itertools.combinations_with_replacement(atoms, n))
    return out


def _day_opts(
    bal_opts: tuple[BalOpt, ...],
    leg_atoms: tuple[LegAtom, ...],
    *,
    leg_max: int = 2,
) -> list[DayOpt]:
    return [
        (bal, legs)
        for bal in bal_opts
        for legs in _multisets(leg_atoms, leg_max)
    ]


def _money_cell(
    tag: str,
    index: int,
    per_day: tuple[DayOpt, ...],
    days: tuple[dt.date, ...],
    window: tuple[dt.date, ...],
) -> PackedCell:
    account = f"{tag}{index:06d}"
    b = CellBuilder()
    for di, (bal, legs) in enumerate(per_day):
        day = days[di]
        if bal is not None:
            money, expected = bal
            b.balance(account=account, day=day, money=money,
                      expected_eod=expected)
        for li, (amount, status, tod) in enumerate(legs):
            leg_id = f"{account}x{di}{li}"
            b.leg(
                id=leg_id, account=account, amount=amount, status=status,
                posting=dt.datetime.combine(day, tod), transfer=leg_id,
            )
    return PackedCell(
        *b.rows(),
        prefixes=(account,),
        expected=_locf.money_family_expected(b.state(), (account,), window),
    )


def _grid_cells(
    tag: str,
    day_opts: list[DayOpt],
    days: tuple[dt.date, ...],
    window: tuple[dt.date, ...],
) -> list[PackedCell]:
    return [
        _money_cell(tag, i, per_day, days, window)
        for i, per_day in enumerate(
            itertools.product(day_opts, repeat=len(days)),
        )
    ]


def _supersession_cells(window: tuple[dt.date, ...]) -> list[PackedCell]:
    """The 48-cell supersession lemma domain (DS.0 §5): emit sequences
    of length 1-2 on day 0 (later entry supersedes) × one optional
    Posted leg. Evaluated over the whole window so the superseded
    claim also carries forward."""
    day = window[0]
    seqs: list[tuple[int, ...]] = [(m,) for m in (-1, 0, 1)]
    seqs += [(m1, m2) for m1 in (-1, 0, 1) for m2 in (-1, 0, 1)]
    cells: list[PackedCell] = []
    index = 0
    for seq in seqs:
        for leg_amount in (None, -1, 0, 1):
            account = f"sp{index:06d}"
            b = CellBuilder()
            for money in seq:
                b.balance(account=account, day=day, money=money)
            if leg_amount is not None:
                leg_id = f"{account}x00"
                b.leg(
                    id=leg_id, account=account, amount=leg_amount,
                    status=POSTED_STATUS,
                    posting=dt.datetime.combine(day, _NOON), transfer=leg_id,
                )
            cells.append(PackedCell(
                *b.rows(),
                prefixes=(account,),
                expected=_locf.money_family_expected(
                    b.state(), (account,), window,
                ),
            ))
            index += 1
    return cells


def money_cells(
    window: tuple[dt.date, ...], *, nightly: bool,
) -> list[PackedCell]:
    """All money-grid cells for the tier. Grids enumerate over the
    first two window days; every cell is EVALUATED over the full
    window (a 3-day nightly window turns day 3 into a carried-day
    probe for every 2-day cell for free)."""
    two_days = window[:2]
    cells = _grid_cells(
        "en", _day_opts(PRIMARY_BAL_OPTS, PRIMARY_LEG_ATOMS), two_days, window,
    )
    cells += _grid_cells(
        "bd", _day_opts(BOUNDARY_BAL_OPTS, BOUNDARY_LEG_ATOMS), two_days,
        window,
    )
    cells += _supersession_cells(window)
    if nightly and len(window) >= 3:
        cells += _grid_cells(
            "n3",
            _day_opts(
                BOUNDARY_BAL_OPTS, PRIMARY_LEG_ATOMS, leg_max=NIGHTLY_LEG_MAX,
            ),
            window[:3],
            window,
        )
    return cells


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT account_id, business_day_start, drift FROM {db.prefix}_drift",
    )
    return {
        (as_str(row[0]), as_date(row[1])): as_int(row[2])
        for row in rows
    }


CHECK: Final = DetectorCheck(detector="drift", read_engine=_read_engine)
