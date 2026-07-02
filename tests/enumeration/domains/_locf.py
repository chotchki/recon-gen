"""Shared LOCF-family machinery: the window-aligned packing contract.

``effective_balances`` builds a FLEET-WIDE calendar-day spine
(MIN..MAX over every emitted internal balance day), so co-packed LOCF
cells interact through the spine even with disjoint account keys
(DS.0 doc §5). The contract implemented here:

- one ANCHOR account emits a zero balance on the window's first and
  last day, pinning the spine to the full window for every packed
  subset (and for every isolated per-cell DB, which carries the same
  anchors);
- the residual side evaluates every cell over exactly that window, so
  ``engine == residual`` is quantified over the states actually
  executed.

The anchor is violation-inert by construction: money 0 (no drift /
overdraft / eod cell — role and parent_role are NULL so it is neither
a leaf nor a ledger parent), no legs (no cadence gap), no expectation.

``money_family_expected`` is the single residual-evaluation adapter
for the four money detectors — it calls the DS.1 laws in
``recon_gen.common.spine.residuals`` (never re-implements them) for
every account of a cell over every window day; the residuals' own
scope guards decide cell existence.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from recon_gen.common.spine.residuals import (
    ZERO,
    ResidualState,
    drift_residual,
    expected_eod_residual,
    ledger_drift_residual,
    overdraft_residual,
)
from tests.enumeration.harness import BalRow, CellBuilder, TxRow, ViolationMap

ANCHOR_ACCOUNT = "zzanchor"


def anchor_rows(
    days: tuple[dt.date, ...],
) -> tuple[tuple[TxRow, ...], tuple[BalRow, ...]]:
    b = CellBuilder()
    for day in (days[0], days[-1]):
        b.balance(
            account=ANCHOR_ACCOUNT, day=day, money=0,
            role=None, parent_role=None,
        )
    return b.rows()


def money_family_expected(
    state: ResidualState,
    accounts: Iterable[str],
    days: tuple[dt.date, ...],
) -> dict[str, ViolationMap]:
    """Expected violation maps for the four money detectors, from the
    canonical residuals. Keys are ``(account_id, day)``; values the
    Cents residual as int."""
    out: dict[str, ViolationMap] = {
        "drift": {}, "ledger_drift": {}, "overdraft": {}, "expected_eod": {},
    }
    for account in accounts:
        for day in days:
            drift = drift_residual(state, account, day)
            if drift is not None and drift != ZERO:
                out["drift"][(account, day)] = drift.value
            ledger = ledger_drift_residual(state, account, day)
            if ledger is not None and ledger != ZERO:
                out["ledger_drift"][(account, day)] = ledger.value
            over = overdraft_residual(state, account, day)
            if over is not None and over != ZERO:
                out["overdraft"][(account, day)] = over.value
            eod = expected_eod_residual(state, account, day)
            if eod is not None and eod != ZERO:
                out["expected_eod"][(account, day)] = eod.value
    return out
