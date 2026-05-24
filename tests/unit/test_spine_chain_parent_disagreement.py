"""Unit tests for AX.1's chain_parent_disagreement spine promotion.

Mirrors the AU.3.a `test_spine_expected_eod.py` shape — same
matview-round-trip + smart-constructor + claimed_accounts +
untagged-emit-byte-stability checks.

The invariant differs from L1 in two important ways:

- **Identity is transfer/template-keyed** not account-keyed
  (the L2-shape matview GROUPs by `(transfer_id, template_name)`,
  not by account).
- **Single-edge** — the plant is transfers-only (no daily_balances
  rows), so it does NOT trip drift (matches the AT.3
  anomaly/money_trail shape).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    ChainParentDisagreementGenerator,
    ChainParentDisagreementInvariant,
    ClaimedAccountsGenerator,
    DriftInvariant,
    LedgerDriftInvariant,
    Violation,
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
    return conn


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Invariant — name + detect contract.
# ---------------------------------------------------------------------------


def test_invariant_carries_the_matview_name() -> None:
    assert (
        ChainParentDisagreementInvariant().name
        == "chain_parent_disagreement"
    )


def test_detect_returns_empty_set_on_empty_db() -> None:
    inv = ChainParentDisagreementInvariant()
    conn = _fresh_db()
    try:
        _refresh(conn)
        assert inv.detect(conn) == set()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# scenario_for — picks a two-template chain from the L2.
# ---------------------------------------------------------------------------


def test_scenario_for_picks_a_two_template_chain() -> None:
    """The spec_example yaml declares ≥1 chain whose singleton child
    resolves to a TransferTemplate (AB.2.6 picker requirement);
    scenario_for finds it and returns a generator pointed at it."""
    gen = ChainParentDisagreementInvariant().scenario_for()
    assert isinstance(gen, ChainParentDisagreementGenerator)
    assert gen.child_template_name  # picker returned a non-empty name
    # Deterministic transfer_id / account_id derivation.
    assert gen.transfer_id == f"tr-cpd-{gen.child_template_name}"
    assert gen.account_id == f"acct-cpd-{gen.child_template_name}"


def test_scenario_for_raises_on_l2_without_eligible_chain() -> None:
    """The picker rejects an L2 with no template-as-chain-child shape
    → scenario_for raises a clear error."""
    from recon_gen.common.l2.primitives import L2Instance
    empty = L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
    )
    with pytest.raises(ValueError, match="cannot manufacture"):
        ChainParentDisagreementInvariant().scenario_for(instance=empty)


# ---------------------------------------------------------------------------
# Generator — claimed_accounts + intended + Protocol satisfaction.
# ---------------------------------------------------------------------------


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = ChainParentDisagreementInvariant().scenario_for()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert len(gen.claimed_accounts) == 1
    assert next(iter(gen.claimed_accounts)) == gen.account_id


def test_intended_matches_natural_key() -> None:
    gen = ChainParentDisagreementInvariant().scenario_for()
    intended = gen.intended
    items = dict(intended.identity)
    assert intended.invariant == "chain_parent_disagreement"
    assert items["transfer_id"] == gen.transfer_id
    assert items["child_template_name"] == gen.child_template_name


# ---------------------------------------------------------------------------
# Emit + detect round-trip — the AS.0/AU.0 contract.
# ---------------------------------------------------------------------------


def test_emit_then_detect_returns_intended() -> None:
    """The core contract: detect(emit()) ⊇ intended."""
    inv = ChainParentDisagreementInvariant()
    gen = inv.scenario_for()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()
    assert gen.intended in detected, (
        f"intended {gen.intended} missing from {detected}"
    )


def test_emit_writes_two_legs_with_distinct_parent_ids() -> None:
    """The matview's `COUNT(DISTINCT transfer_parent_id) > 1` only
    fires when the 2 leg rows actually have different parent ids."""
    gen = ChainParentDisagreementInvariant().scenario_for()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT transfer_id, template_name, transfer_parent_id "
            f"FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ?",
            (gen.transfer_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    # Same transfer_id + template_name; different parents.
    transfer_ids = {r[0] for r in rows}
    templates = {r[1] for r in rows}
    parents = {r[2] for r in rows}
    assert transfer_ids == {gen.transfer_id}
    assert templates == {gen.child_template_name}
    assert len(parents) == 2  # the disagreement


def test_emit_does_not_trip_drift_single_edge_property() -> None:
    """Transfers-only plant ⇒ no daily_balances rows ⇒ drift +
    ledger_drift see nothing. Matches the AT.3 anomaly / money_trail
    single-edge property."""
    gen = ChainParentDisagreementInvariant().scenario_for()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        drift_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_drift",
        ).fetchone()[0]
        ledger_drift_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_ledger_drift",
        ).fetchone()[0]
        assert DriftInvariant().detect(conn) == set()
        assert LedgerDriftInvariant().detect(conn) == set()
    finally:
        conn.close()
    assert drift_count == 0
    assert ledger_drift_count == 0


# ---------------------------------------------------------------------------
# Untagged emit byte-stability — AV.5 contract.
# ---------------------------------------------------------------------------


def test_untagged_emit_writes_null_metadata() -> None:
    """`emit(conn)` without scenario_id leaves metadata=NULL —
    byte-identical to pre-AV.5 behavior. Critical for any existing
    test that calls gen.emit(conn) directly."""
    gen = ChainParentDisagreementInvariant().scenario_for()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        metadata_values = conn.execute(
            f"SELECT DISTINCT metadata FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    assert metadata_values == [(None,)], (
        f"Untagged emit must write metadata=NULL; got {metadata_values}"
    )


def test_tagged_emit_writes_scenario_id_in_metadata() -> None:
    """`emit(conn, scenario_id=...)` tags every row's metadata column
    with the AV.5 ScenarioContext scenario_id."""
    gen = ChainParentDisagreementInvariant().scenario_for()
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ax1")
        conn.commit()
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE json_extract(metadata, '$.scenario_id') = ?",
            ("test-ax1",),
        ).fetchone()[0]
        total = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == total > 0, (
        f"All {total} emitted rows must carry the scenario_id; "
        f"only {tagged} did"
    )
