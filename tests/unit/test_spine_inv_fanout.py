"""AY.2.b — unit tests for InvFanoutGenerator.

Seed-color coverage generator: emits N two-leg transfers (sender →
recipient) on the anchor day to populate the Investigation matviews
(inv_pair_rolling_anomalies + inv_money_trail_edges). `intended`
returns a `CoverageObservation`. Registered edge to
`MoneyTrailInvariant` documents the deterministic side-effect: each
transfer is a depth-0 (root) edge the recursive CTE surfaces.
"""

# pytest.approx() typeshed stubs are partial — kill the resulting noise here.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    ALL_COVERAGE_GENERATORS,
    ClaimedAccountsGenerator,
    CoverageObservation,
    INVARIANT_GENERATOR_EDGES,
    InvFanoutFactory,
    InvFanoutGenerator,
    MoneyTrailInvariant,
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


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur,
        refresh_matviews_sql(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Factory + intended subtype.
# ---------------------------------------------------------------------------


def test_factory_returns_generator_with_sender_count() -> None:
    gen = InvFanoutFactory().scenario_for_fanout(
        sender_count=3, anchor_day=date(2030, 1, 1),
    )
    assert isinstance(gen, InvFanoutGenerator)
    assert len(gen.sender_account_ids) == 3
    assert gen.recipient_account_id == "acct-inv-fanout-recipient"


def test_factory_rejects_zero_senders_loudly() -> None:
    with pytest.raises(ValueError, match="sender_count must be ≥1"):
        InvFanoutFactory().scenario_for_fanout(sender_count=0)


def test_intended_is_a_coverage_observation() -> None:
    gen = InvFanoutFactory().scenario_for_fanout(
        sender_count=4, anchor_day=date(2030, 1, 1), rail_name="wire",
    )
    intended = gen.intended
    assert isinstance(intended, CoverageObservation), (
        f"InvFanoutGenerator's intended should be a CoverageObservation; "
        f"got {type(intended).__name__}"
    )
    items = dict(intended.identity)
    assert intended.invariant == "inv_fanout"
    assert items["sender_count"] == 4
    assert items["recipient_account_id"] == gen.recipient_account_id
    assert items["rail_name"] == "wire"
    assert items["anchor_day"] == "2030-01-01"


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = InvFanoutFactory().scenario_for_fanout(sender_count=3)
    assert isinstance(gen, ClaimedAccountsGenerator)
    expected = {gen.recipient_account_id, *gen.sender_account_ids}
    assert gen.claimed_accounts == frozenset(expected)
    # Recipient + N senders = 1 + N distinct accounts.
    assert len(gen.claimed_accounts) == 4


# ---------------------------------------------------------------------------
# Emit shape — N transfers, each 2-leg + zero-sum.
# ---------------------------------------------------------------------------


def test_emit_writes_2n_legs_paired_by_transfer_id() -> None:
    """N=3 senders → 6 legs total → 3 pairs by transfer_id, each
    summing to zero (-amount + amount)."""
    gen = InvFanoutFactory().scenario_for_fanout(
        sender_count=3, amount_per_transfer=150.0,
        anchor_day=date(2030, 1, 1),
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT transfer_id, SUM(amount_money), COUNT(*) "
            f"FROM {_PREFIX}_transactions GROUP BY transfer_id "
            f"ORDER BY transfer_id",
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    assert all(float(r[1]) == pytest.approx(0.0) for r in rows)
    assert all(r[2] == 2 for r in rows)


def test_emit_credits_recipient_n_times() -> None:
    """The recipient receives N credit legs (one per sender). Each
    credit's account_role + account_parent_role satisfy the
    inv_pair_rolling_anomalies matview filter."""
    gen = InvFanoutFactory().scenario_for_fanout(sender_count=4)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        recipient_rows = conn.execute(
            f"SELECT amount_direction, account_role, account_scope, "
            f"account_parent_role "
            f"FROM {_PREFIX}_transactions WHERE account_id = ?",
            (gen.recipient_account_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(recipient_rows) == 4
    assert all(r[0] == "Credit" for r in recipient_rows)
    # The anomaly matview's filter: scope='internal' + parent_role IS NOT NULL.
    assert all(r[2] == "internal" for r in recipient_rows)
    assert all(r[3] is not None for r in recipient_rows)


def test_emit_metadata_carries_sender_recipient_pair() -> None:
    """The OLD plant emits metadata={sender_id, recipient_id} on
    every leg — downstream Investigation filters read these. The
    spine generator preserves the shape."""
    gen = InvFanoutFactory().scenario_for_fanout(sender_count=2)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT json_extract(metadata, '$.sender_id'), "
            f"json_extract(metadata, '$.recipient_id') "
            f"FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    # All 4 legs carry both keys; recipient_id is constant; sender_id
    # varies across the 2 sender accounts.
    assert all(r[0] is not None for r in rows)
    assert all(r[1] == gen.recipient_account_id for r in rows)
    assert len({r[0] for r in rows}) == 2


# ---------------------------------------------------------------------------
# Registered edge — MoneyTrailInvariant deterministically fires.
# ---------------------------------------------------------------------------


def test_inv_money_trail_edges_matview_surfaces_n_root_edges() -> None:
    """The registered edge: every emitted transfer is depth-0 (root)
    in the inv_money_trail_edges recursive CTE. N senders → N depth-0
    edges → MoneyTrailInvariant.detect() returns ≥N violations."""
    gen = InvFanoutFactory().scenario_for_fanout(
        sender_count=3, anchor_day=date(2030, 1, 1),
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        violations = MoneyTrailInvariant(prefix=_PREFIX).detect(conn)
    finally:
        conn.close()
    # The matview surfaces edges for OUR transfer_ids. Filter to those
    # to assert at least 3 (other matview rows may or may not be
    # present from other refreshes; we only care about ours).
    our_transfer_ids = set(gen.transfer_ids)
    our_edges = [
        v for v in violations
        if dict(v.identity).get("transfer_id") in our_transfer_ids
    ]
    assert len(our_edges) >= 3, (
        f"expected ≥3 money_trail edges for our 3 fanout transfers; "
        f"got {len(our_edges)} — matview shape may have drifted"
    )


# ---------------------------------------------------------------------------
# AY.2.b bucket + registry consistency.
# ---------------------------------------------------------------------------


def test_inv_fanout_is_in_coverage_bucket() -> None:
    """The primary intent is coverage; bucket membership must reflect
    that. Non-empty edges (the MoneyTrailInvariant side-effect) don't
    disqualify the coverage bucket — AU.5's gate permits either."""
    assert InvFanoutGenerator in ALL_COVERAGE_GENERATORS
    assert INVARIANT_GENERATOR_EDGES[InvFanoutGenerator] == (
        MoneyTrailInvariant,
    )


# ---------------------------------------------------------------------------
# AV.5 metadata tagging — merges with sender/recipient pair keys.
# ---------------------------------------------------------------------------


def test_tagged_emit_merges_scenario_id_with_pair_keys() -> None:
    """When ScenarioContext tags emit, the metadata column gets the
    union: scenario_id + sender_id + recipient_id, JSON-serialized."""
    gen = InvFanoutFactory().scenario_for_fanout(sender_count=2)
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ay2b-inv-fanout")
        conn.commit()
        rows = conn.execute(
            f"SELECT json_extract(metadata, '$.scenario_id'), "
            f"json_extract(metadata, '$.sender_id'), "
            f"json_extract(metadata, '$.recipient_id') "
            f"FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    # All 4 legs (2 senders × 2 legs) tagged.
    assert len(rows) == 4
    assert all(r[0] == "test-ay2b-inv-fanout" for r in rows)
    assert all(r[1] is not None for r in rows)
    assert all(r[2] == gen.recipient_account_id for r in rows)
