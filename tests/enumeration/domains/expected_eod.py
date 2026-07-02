"""Expected-EOD breach (law 4) — rides the money domain (DS.0 §3).

No cells of its own: the money grid's balance axis carries
``expected_eod ∈ {None, 0}`` (the DS.0 sketch's expected axis), so
every emitted-balance cell doubles as an expected-eod cell. Unlike
drift/overdraft the law binds ONLY the emit day (no carry — the
inventory reads ``current_daily_balances``, not the effective view);
the carried-day evaluations the shared adapter performs return None
there by construction, which is itself part of what the comparator
verifies (no phantom carried-day breach rows).

Value compared: engine ``variance`` == residual (``money − expected``).
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
        f"SELECT account_id, business_day_start, variance "
        f"FROM {db.prefix}_expected_eod_balance_breach",
    )
    return {
        (as_str(row[0]), as_date(row[1])): as_int(row[2])
        for row in rows
    }


CHECK: Final = DetectorCheck(
    detector="expected_eod", read_engine=_read_engine,
)
