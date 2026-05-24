"""AY.2.b — unit tests for FailedTransactionGenerator.

Audit-fixture generator: emits ONE Posted leg with status='Failed'.
No matching Invariant on the spine (the L2FT Postings dataset reads
the row via its CASE projection; not a matview violation). `intended`
returns an `AuditFixture` per the AY.2.a evidence-currency layering.
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
    AuditFixture,
    ClaimedAccountsGenerator,
    FailedTransactionGenerator,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()
    replace_config(
        conn, prefix=_PREFIX, cfg_json="{}",
        l2_json=json.dumps({"rails": []}),
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    return conn


def _build_gen(account_id: str = "acct-failed-cust") -> FailedTransactionGenerator:
    return FailedTransactionGenerator(
        account_id=account_id,
        account_role="CustomerSubledger",
        account_scope="internal",
        account_parent_role="CustomerLedger",
        rail_name="ach",
        amount=100.0,
        anchor_day=date(2030, 1, 1),
    )


# ---------------------------------------------------------------------------
# Generator — protocol satisfaction + intended subtype.
# ---------------------------------------------------------------------------


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = _build_gen()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert gen.claimed_accounts == frozenset({"acct-failed-cust"})


def test_intended_is_an_audit_fixture() -> None:
    """The AY.2.a evidence-currency contract: this generator's
    intended subtype must be `AuditFixture` (NOT `RuleViolation`).
    pyright + isinstance both narrow correctly."""
    gen = _build_gen()
    intended = gen.intended
    assert isinstance(intended, AuditFixture), (
        f"FailedTransactionGenerator's intended should be an "
        f"AuditFixture; got {type(intended).__name__}"
    )
    items = dict(intended.identity)
    assert intended.invariant == "failed_transaction"
    assert items["transaction_id"] == gen.transaction_id
    assert items["account_id"] == "acct-failed-cust"


def test_intended_is_deterministic_on_account_id() -> None:
    """Same construction args → same intended Violation (the
    semantic_lock contract relies on this for byte-stable locks)."""
    assert _build_gen().intended == _build_gen().intended


# ---------------------------------------------------------------------------
# Emit row shape.
# ---------------------------------------------------------------------------


def test_emit_writes_one_row_with_status_failed() -> None:
    """The dropdown-coverage contract: a single Debit row with
    status='Failed' lands in transactions."""
    gen = _build_gen()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT account_id, status, amount_direction, rail_name "
            f"FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("acct-failed-cust", "Failed", "Debit", "ach")]


# ---------------------------------------------------------------------------
# AV.5 metadata tagging.
# ---------------------------------------------------------------------------


def test_untagged_emit_writes_null_metadata() -> None:
    gen = _build_gen()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        metadata = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert metadata is None


def test_tagged_emit_writes_scenario_id() -> None:
    gen = _build_gen()
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ay2b-failed")
        conn.commit()
        sid = conn.execute(
            f"SELECT json_extract(metadata, '$.scenario_id') "
            f"FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert sid == "test-ay2b-failed"
