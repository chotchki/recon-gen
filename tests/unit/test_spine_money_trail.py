"""Unit tests for AT.1's `MoneyTrailInvariant` detector promotion.

Detector-only at AT.1 (no scenario_for, no generator — AT.3 lands the
recursive parent-linked chain emission). What's pinned:

1. MoneyTrailInvariant satisfies the Invariant Protocol.
2. detect() reads `<prefix>_inv_money_trail_edges` and projects every
   edge as a Violation with identity (root_transfer_id, transfer_id,
   depth).
3. Substitution-path absence (AR.5 lesson).
4. The detector correctly handles empty-matview state (no transfers
   ⇒ no edges ⇒ empty Violation set).
5. A hand-emitted parent-child transfer pair (no generator yet)
   produces 2 trail edges (root at depth 0, child at depth 1); detect
   surfaces both.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    Invariant,
    MoneyTrailInvariant,
    Violation,
)
from recon_gen.common.spine._emit_helpers import insert_tx, ts
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


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Protocol satisfaction + matview name linkage.
# ---------------------------------------------------------------------------


def test_money_trail_invariant_carries_the_matview_name() -> None:
    assert MoneyTrailInvariant().name == "inv_money_trail_edges"


def test_money_trail_invariant_satisfies_invariant_protocol() -> None:
    assert isinstance(MoneyTrailInvariant(), Invariant)


# ---------------------------------------------------------------------------
# detect() behavior.
# ---------------------------------------------------------------------------


def test_empty_matview_returns_no_violations() -> None:
    inv = MoneyTrailInvariant()
    conn = _fresh_db()
    try:
        _refresh(conn)
        assert inv.detect(conn) == set()
    finally:
        conn.close()


def test_parent_child_transfer_pair_emits_both_trail_edges() -> None:
    """Hand-emit a 2-deep chain: ROOT (transfer_parent_id=NULL) +
    CHILD (transfer_parent_id=root.transfer_id). The money_trail
    matview's WITH RECURSIVE walks both — root at depth=0, child at
    depth=1. detect() surfaces both as Violations.

    AT.3 will land MoneyTrailGenerator that emits this shape
    programmatically (root + N descendants). AT.1 hand-builds to pin
    the detector behavior without the generator."""
    inv = MoneyTrailInvariant()
    conn = _fresh_db()
    try:
        # Root: 2-leg Posted transfer, no parent.
        insert_tx(
            conn, id="tx-root-src",
            account_id="acct-src", account_name="Src",
            account_role="ExternalCounterparty", account_scope="external",
            account_parent_role=None,
            amount_money=-100.0, amount_direction="Debit",
            status="Posted", posting=ts(datetime(2030, 1, 1).date()),
            transfer_id="xfer-root", rail_name="ach", origin="etl",
        )
        insert_tx(
            conn, id="tx-root-tgt",
            account_id="acct-mid", account_name="Mid",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=100.0, amount_direction="Credit",
            status="Posted", posting=ts(datetime(2030, 1, 1).date()),
            transfer_id="xfer-root", rail_name="ach", origin="etl",
        )
        # Child: 2-leg Posted, transfer_parent_id=xfer-root.
        insert_tx(
            conn, id="tx-child-src",
            account_id="acct-mid", account_name="Mid",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=-100.0, amount_direction="Debit",
            status="Posted", posting=ts(datetime(2030, 1, 2).date()),
            transfer_id="xfer-child", transfer_parent_id="xfer-root",
            rail_name="ach", origin="etl",
        )
        insert_tx(
            conn, id="tx-child-tgt",
            account_id="acct-end", account_name="End",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=100.0, amount_direction="Credit",
            status="Posted", posting=ts(datetime(2030, 1, 2).date()),
            transfer_id="xfer-child", transfer_parent_id="xfer-root",
            rail_name="ach", origin="etl",
        )
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    # The matview emits ONE row per edge per chain member. Root + child
    # = 2 chain members, each with one edge → 2 Violations. Both
    # share the same root_transfer_id.
    by_depth = {dict(v.identity).get("depth"): v for v in detected}
    assert 0 in by_depth, (
        f"expected a depth=0 (root) edge; got {detected}"
    )
    assert 1 in by_depth, (
        f"expected a depth=1 (child) edge; got {detected}"
    )
    # Both share the same root.
    assert (
        dict(by_depth[0].identity).get("root_transfer_id")
        == dict(by_depth[1].identity).get("root_transfer_id")
        == "xfer-root"
    )


# ---------------------------------------------------------------------------
# Substitution-path absence (AR.5 lesson).
# ---------------------------------------------------------------------------


def test_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    inv = MoneyTrailInvariant()
    conn = _fresh_db()
    try:
        captured: list[str] = []
        conn.set_trace_callback(captured.append)
        inv.detect(conn)
        conn.set_trace_callback(None)
    finally:
        conn.close()
    assert captured
    for sql in captured:
        assert "<<$" not in sql, (
            f"money_trail detector crossed a SQL-pushdown surface; "
            f"AR.5-style substitution-path test required.\n  sql: {sql!r}"
        )


def test_violation_identity_shape() -> None:
    """The detector's Violation identity shape is
    (root_transfer_id, transfer_id, depth) — pin it directly via
    Violation.of construction equality so AT.3's generator can build
    matching intendeds."""
    v = Violation.of(
        "inv_money_trail_edges",
        root_transfer_id="xfer-root",
        transfer_id="xfer-child",
        depth=1,
    )
    assert v.invariant == "inv_money_trail_edges"
    identity_keys = {k for k, _ in v.identity}
    assert identity_keys == {"root_transfer_id", "transfer_id", "depth"}
