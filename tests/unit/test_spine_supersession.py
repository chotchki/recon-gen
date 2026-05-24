"""AY.2.b — unit tests for SupersessionGenerator.

Audit-fixture generator: emits TWO transactions sharing one logical
`id` — the original posting + a TechnicalCorrection rewrite at a
later `entry`. The M.2b.12 audit-PDF Supersession section reads
the pair via `COUNT(*) OVER (PARTITION BY id) > 1` + `supersedes IS
NOT NULL`. No matching matview Invariant on the spine. `intended`
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
    SupersessionGenerator,
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


def _build_gen(
    account_id: str = "acct-supersedes-cust",
    *,
    original: float = 250.0,
    corrected: float = 200.0,
) -> SupersessionGenerator:
    return SupersessionGenerator(
        account_id=account_id,
        account_role="CustomerSubledger",
        account_scope="internal",
        account_parent_role="CustomerLedger",
        rail_name="ach",
        original_amount=original,
        corrected_amount=corrected,
        anchor_day=date(2030, 1, 1),
    )


# ---------------------------------------------------------------------------
# Generator — protocol satisfaction + intended subtype.
# ---------------------------------------------------------------------------


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = _build_gen()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert gen.claimed_accounts == frozenset({"acct-supersedes-cust"})


def test_intended_is_an_audit_fixture() -> None:
    gen = _build_gen()
    intended = gen.intended
    assert isinstance(intended, AuditFixture)
    items = dict(intended.identity)
    assert intended.invariant == "supersession"
    assert items["transaction_id"] == gen.transaction_id
    assert items["account_id"] == "acct-supersedes-cust"
    assert items["corrected_amount"] == 200.0


# ---------------------------------------------------------------------------
# Emit row shape.
# ---------------------------------------------------------------------------


def test_emit_writes_two_rows_sharing_one_id() -> None:
    """The supersession contract: TWO transactions, same `id` field,
    different `entry` (auto-increment), different `amount_money`."""
    gen = _build_gen(original=250.0, corrected=200.0)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT id, entry, amount_money, supersedes "
            f"FROM {_PREFIX}_transactions ORDER BY entry",
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    # Same logical id on both rows.
    assert {r[0] for r in rows} == {gen.transaction_id}
    # Distinct entry values (auto-incremented by the dialect).
    assert rows[0][1] < rows[1][1]
    # Original (Debit -250) then correction (Debit -200).
    # AO.1: amount_money is BIGINT cents.
    assert rows[0][2] == -25000
    assert rows[1][2] == -20000
    # Original has no `supersedes`; correction tags 'TechnicalCorrection'.
    assert rows[0][3] is None
    assert rows[1][3] == "TechnicalCorrection"


def test_emit_supersession_pair_satisfies_audit_pdf_filter() -> None:
    """The M.2b.12 audit PDF reads
    `COUNT(*) OVER (PARTITION BY id) > 1` + `supersedes IS NOT NULL`.
    Both conditions must fire on the planted pair."""
    gen = _build_gen()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        n_in_partition = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions WHERE id = ?",
            (gen.transaction_id,),
        ).fetchone()[0]
        n_with_supersedes = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE id = ? AND supersedes IS NOT NULL",
            (gen.transaction_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_in_partition == 2  # > 1, the partition gate
    assert n_with_supersedes == 1  # the correction row


# ---------------------------------------------------------------------------
# AV.5 metadata tagging.
# ---------------------------------------------------------------------------


def test_tagged_emit_tags_both_rows() -> None:
    gen = _build_gen()
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ay2b-supersession")
        conn.commit()
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE json_extract(metadata, '$.scenario_id') = ?",
            ("test-ay2b-supersession",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == 2
