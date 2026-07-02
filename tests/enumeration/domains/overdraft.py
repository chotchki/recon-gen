"""Overdraft (law 3) — rides the money domain (DS.0 §3 row 3).

No cells of its own: every money-grid / supersession / ledger-topology
cell in the LOCF packed domain is also an overdraft cell (the residual
is a predicate over the SAME effective balance the drift law reads,
and CL.5 rewired the detector onto carried days — which is exactly
what the window-aligned evaluation exercises: a cell whose last emit
is negative must fire on every later window day). The expected side is
computed uniformly by ``_locf.money_family_expected``; this module
owns the engine reader.

Value compared: the engine's ``stored_balance`` equals the residual
(``when(stored < 0, stored, ZERO)`` is the stored value on every
violation row).
"""
from __future__ import annotations

from typing import Final

from tests.enumeration.domains._base import as_date, as_int, as_str
from tests.enumeration.harness import (
    DetectorCheck,
    EnumerationDB,
    ViolationMap,
)


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT account_id, business_day_start, stored_balance "
        f"FROM {db.prefix}_overdraft",
    )
    return {
        (as_str(row[0]), as_date(row[1])): as_int(row[2])
        for row in rows
    }


CHECK: Final = DetectorCheck(detector="overdraft", read_engine=_read_engine)
