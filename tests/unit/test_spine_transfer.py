"""Unit tests for AT.3's `Transfer` / `TransferLeg` primitive on
`LedgerSimulation`. Pins the data shape + emit routing + the
double-entry conservation law (as a property, not as an enforced
construction-time check).

What's pinned:

1. TransferLeg + Transfer construct as frozen dataclasses with the
   documented field set + defaults.
2. `Transfer.is_balanced()` is True iff legs sum to zero — the
   double-entry conservation law. NOT enforced at construction (Pending
   single-leg transfers are valid intermediate state).
3. `LedgerSimulation.transfers` defaults to empty; coexists with
   `accounts`.
4. Emitting a Transfer writes one `_transactions` row per leg with the
   right transfer_id, parent_transfer_id, posting, status, account-side
   fields. Direction derives from amount sign.
5. Transfer-only ledgers (no accounts) emit without writing balance
   rows — the property anomaly's plant relies on (single-edge to
   anomaly, no drift).
6. Chains via `parent_transfer_id` survive the round-trip into
   `transactions.transfer_parent_id`.
7. Mixed (accounts + transfers) ledger emits both — drift-style folds
   stack with money-flow shape.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    AccountSimulation,
    DayPlan,
    LedgerSimulation,
    Transfer,
    TransferLeg,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}", l2_json=json.dumps({"rails": []}),
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    return conn


def _balanced_pair(
    *, day: date, transfer_id: str, amount: float = 100.0,
    parent: str | None = None,
) -> Transfer:
    """Build a Posted 2-leg balanced transfer (sender Debit, recipient
    Credit) for tests. Both accounts internal-leaf for matview eligibility."""
    return Transfer(
        day=day,
        transfer_id=transfer_id,
        rail_name="ach",
        status="Posted",
        parent_transfer_id=parent,
        legs=(
            TransferLeg(
                account_id="acct-src",
                amount=-amount,
                account_name="Source",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
            ),
            TransferLeg(
                account_id="acct-tgt",
                amount=amount,
                account_name="Target",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Construction + conservation law (the data-shape contract).
# ---------------------------------------------------------------------------


def test_transfer_leg_constructs_with_required_fields() -> None:
    leg = TransferLeg(
        account_id="a", amount=100.0,
        account_name="A", account_role="CustomerSubledger",
        account_scope="internal",
    )
    assert leg.account_id == "a"
    assert leg.account_parent_role is None  # documented default


def test_transfer_constructs_with_defaults() -> None:
    t = _balanced_pair(day=date(2030, 1, 1), transfer_id="x")
    assert t.status == "Posted"
    assert t.parent_transfer_id is None
    assert t.origin == "etl"
    assert t.hour == 12  # documented noon default


def test_is_balanced_true_when_legs_sum_to_zero() -> None:
    t = _balanced_pair(day=date(2030, 1, 1), transfer_id="x", amount=42.5)
    assert t.is_balanced()


def test_is_balanced_false_when_legs_dont_sum_to_zero() -> None:
    """Pending single-leg transfers are common intermediate state;
    constructor doesn't enforce — `is_balanced()` exposes the check."""
    t = Transfer(
        day=date(2030, 1, 1),
        transfer_id="x",
        rail_name="ach",
        status="Pending",
        legs=(
            TransferLeg(
                account_id="ext", amount=100.0,
                account_name="External", account_role="ExternalCounterparty",
                account_scope="external",
            ),
        ),
    )
    assert not t.is_balanced()


# ---------------------------------------------------------------------------
# LedgerSimulation.transfers — defaults, coexistence with accounts.
# ---------------------------------------------------------------------------


def test_ledger_simulation_transfers_defaults_to_empty() -> None:
    sim = LedgerSimulation()
    assert sim.transfers == []
    assert sim.accounts == []
    assert sim.prefix == "spec_example"


def test_ledger_simulation_with_transfers_only_no_accounts() -> None:
    """Anomaly's shape: transfers without account-folds. emit() should
    not raise on empty accounts list."""
    sim = LedgerSimulation(transfers=[
        _balanced_pair(day=date(2030, 1, 1), transfer_id="x"),
    ])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
        balance_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances",
        ).fetchone()[0]
    finally:
        conn.close()
    assert tx_count == 2  # one row per leg
    assert balance_count == 0  # NO balance rows — anomaly's single-edge property


# ---------------------------------------------------------------------------
# Per-leg INSERT shape — round-trip into _transactions columns.
# ---------------------------------------------------------------------------


def test_transfer_emit_writes_one_tx_row_per_leg() -> None:
    sim = LedgerSimulation(transfers=[
        _balanced_pair(day=date(2030, 1, 1), transfer_id="xfer-1", amount=250.0),
    ])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT transfer_id, account_id, amount_money, amount_direction, "
            f"status, rail_name "
            f"FROM {_PREFIX}_transactions ORDER BY amount_money",
        ).fetchall()
    finally:
        conn.close()
    # Two rows: source leg (-250, Debit), target leg (+250, Credit).
    assert len(rows) == 2
    src, tgt = rows[0], rows[1]
    assert src == ("xfer-1", "acct-src", -250.0, "Debit", "Posted", "ach")
    assert tgt == ("xfer-1", "acct-tgt", 250.0, "Credit", "Posted", "ach")


def test_transfer_emit_preserves_parent_transfer_id() -> None:
    """The chain linkage that money_trail's recursive matview walks."""
    sim = LedgerSimulation(transfers=[
        _balanced_pair(day=date(2030, 1, 1), transfer_id="root"),
        _balanced_pair(
            day=date(2030, 1, 2), transfer_id="child", parent="root",
        ),
    ])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        parent_links = conn.execute(
            f"SELECT DISTINCT transfer_id, transfer_parent_id "
            f"FROM {_PREFIX}_transactions ORDER BY transfer_id",
        ).fetchall()
    finally:
        conn.close()
    assert parent_links == [("child", "root"), ("root", None)]


def test_transfer_emit_account_denorm_fields_round_trip() -> None:
    """The matview JOINs on account_role / account_scope / account_parent_
    role — the legs MUST carry these denormalized fields all the way
    through. Pin that they survive the INSERT."""
    sim = LedgerSimulation(transfers=[
        _balanced_pair(day=date(2030, 1, 1), transfer_id="xfer-A"),
    ])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT account_role, account_scope, account_parent_role "
            f"FROM {_PREFIX}_transactions ORDER BY account_id",
        ).fetchall()
    finally:
        conn.close()
    # Both legs are internal CustomerSubledger under CustomerLedger.
    for r in rows:
        assert r == ("CustomerSubledger", "internal", "CustomerLedger")


def test_transfer_posting_hour_defaults_to_noon() -> None:
    """The default `hour=12` places the timestamp safely inside the
    business day's bounds (00:00-24:00)."""
    sim = LedgerSimulation(transfers=[
        _balanced_pair(day=date(2030, 1, 15), transfer_id="xfer-noon"),
    ])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        postings = conn.execute(
            f"SELECT DISTINCT posting FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    assert postings == [("2030-01-15 12:00:00",)]


# ---------------------------------------------------------------------------
# Composition with AccountSimulation — both flow shapes coexist.
# ---------------------------------------------------------------------------


def test_mixed_accounts_and_transfers_both_emit() -> None:
    """A scenario that wants drift-style stored-balance folds AND
    transfer-shape money flow composes both fields. Both produce rows."""
    acct = AccountSimulation(
        plans=[DayPlan(day=date(2030, 1, 1), legs=(100.0,))],
        account_id="acct-fold", account_role="CustomerSubledger",
        parent_role="CustomerLedger", prefix=_PREFIX,
    )
    sim = LedgerSimulation(
        accounts=[acct],
        transfers=[
            _balanced_pair(day=date(2030, 1, 1), transfer_id="xfer-flow"),
        ],
    )
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
        balance_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances",
        ).fetchone()[0]
    finally:
        conn.close()
    # Account fold: 1 leg tx + 1 balance row. Transfer: 2 legs. Total: 3 tx + 1 balance.
    assert tx_count == 3
    assert balance_count == 1


def test_pending_transfer_marked_pending_in_db() -> None:
    """Status field round-trips. Matview filtering on Posted depends
    on this — `_inv_pair_rolling_anomalies` filters status='Posted',
    so a Pending transfer should NOT contribute to anomaly's
    population."""
    sim = LedgerSimulation(transfers=[
        Transfer(
            day=date(2030, 1, 1),
            transfer_id="xfer-pending",
            rail_name="ach",
            status="Pending",
            legs=(
                TransferLeg(
                    account_id="a", amount=-50.0,
                    account_name="A", account_role="CustomerSubledger",
                    account_scope="internal",
                    account_parent_role="CustomerLedger",
                ),
                TransferLeg(
                    account_id="b", amount=50.0,
                    account_name="B", account_role="CustomerSubledger",
                    account_scope="internal",
                    account_parent_role="CustomerLedger",
                ),
            ),
        ),
    ])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        statuses = conn.execute(
            f"SELECT DISTINCT status FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    assert statuses == [("Pending",)]


def test_multi_leg_unbalanced_transfer_is_representable() -> None:
    """The constructor doesn't enforce balance — single-leg or
    intentionally-unbalanced transfers are allowed for representing
    intermediate state. Pin with a 3-leg transfer that doesn't net
    to zero."""
    t = Transfer(
        day=date(2030, 1, 1),
        transfer_id="xfer-skewed",
        rail_name="wire",
        legs=(
            TransferLeg(
                account_id="a", amount=-100.0,
                account_name="A", account_role="CustomerSubledger",
                account_scope="internal", account_parent_role="CustomerLedger",
            ),
            TransferLeg(
                account_id="b", amount=40.0,
                account_name="B", account_role="CustomerSubledger",
                account_scope="internal", account_parent_role="CustomerLedger",
            ),
            TransferLeg(
                account_id="c", amount=50.0,
                account_name="C", account_role="CustomerSubledger",
                account_scope="internal", account_parent_role="CustomerLedger",
            ),
        ),
    )
    assert not t.is_balanced()
    # And it can still emit — useful for "fee leg" / "tax leg" tests
    # where the imbalance is the point.
    sim = LedgerSimulation(transfers=[t])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        n = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 3


# ---------------------------------------------------------------------------
# Sign-direction routing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount,expected_direction",
    [(100.0, "Credit"), (-100.0, "Debit"), (0.0, "Credit")],
)
def test_amount_sign_routes_direction(
    amount: float, expected_direction: str,
) -> None:
    """Positive (and zero, the documented boundary) maps to Credit;
    negative to Debit. Zero-amount transfers are degenerate but
    representable — match the AS.3 AccountSimulation sign convention."""
    sim = LedgerSimulation(transfers=[
        Transfer(
            day=date(2030, 1, 1),
            transfer_id="xfer-sign",
            rail_name="ach",
            legs=(
                TransferLeg(
                    account_id="a", amount=amount,
                    account_name="A", account_role="CustomerSubledger",
                    account_scope="internal",
                    account_parent_role="CustomerLedger",
                ),
            ),
        ),
    ])
    conn = _fresh_db()
    try:
        sim.emit(conn)
        conn.commit()
        d = conn.execute(
            f"SELECT amount_direction FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert d == expected_direction
