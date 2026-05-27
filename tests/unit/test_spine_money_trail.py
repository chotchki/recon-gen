"""Unit tests for `MoneyTrailInvariant` (AT.1) + `MoneyTrailGenerator`
+ `MoneyTrailView` (AT.3).

AT.1 landed the detector shim. AT.3 added the recursive parent-linked
chain generator on the `Transfer` / `LedgerSimulation` primitive plus
the `MoneyTrailView` depth-threshold knob (mirrors AnomalyView's σ
pattern).

What's pinned:

1. MoneyTrailInvariant satisfies the Invariant Protocol.
2. detect() reads `<prefix>_inv_money_trail_edges` and projects every
   edge as a Violation with identity (root_transfer_id, transfer_id,
   depth).
3. Substitution-path absence (AR.5 lesson).
4. The detector correctly handles empty-matview state (no transfers
   ⇒ no edges ⇒ empty Violation set).
5. A hand-emitted parent-child transfer pair produces 2 trail edges
   (root at depth 0, child at depth 1); detect surfaces both.
6. AT.3 `MoneyTrailGenerator` satisfies the ViolationGenerator
   Protocol; emits a chain_length-deep chain with parent linkage.
7. The chain's deepest edge (depth = chain_length - 1) is the
   generator's `intended` Violation.
8. Every chain hop's recipient is the next hop's sender (money walks).
9. scenario_for resolves the hop role + fails loud on missing role.
10. `MoneyTrailView(min_depth=N)` slices the detector's output by
    depth; default 0 returns everything; raising min_depth drops
    shallow edges monotonically.
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
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    Invariant,
    MoneyTrailInvariant,
    MoneyTrailView,
    Violation,
    ViolationGenerator,
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


# ===========================================================================
# AT.3 — MoneyTrailGenerator + MoneyTrailView.
# ===========================================================================


# ---- Generator: Protocol + smart constructor ------------------------------


def test_money_trail_generator_satisfies_violation_generator_protocol() -> None:
    gen = MoneyTrailInvariant().scenario_for("CustomerSubledger")
    assert isinstance(gen, ViolationGenerator)


def test_scenario_for_resolves_hop_role() -> None:
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=4, amount=250.0,
    )
    assert gen.hop_account_role == "CustomerSubledger"
    assert gen.hop_account_parent_role == "CustomerLedger"
    assert gen.chain_length == 4
    assert gen.amount == 250.0


def test_scenario_for_unknown_role_fails_loud() -> None:
    with pytest.raises(
        ValueError, match="no money-trail hop-eligible leaf",
    ):
        MoneyTrailInvariant().scenario_for("NoSuchRole")


def test_scenario_for_rejects_zero_chain_length() -> None:
    with pytest.raises(ValueError, match="chain_length must be"):
        MoneyTrailInvariant().scenario_for(
            "CustomerSubledger", chain_length=0,
        )


# ---- Emit + detect round-trip (the headline AT.3 contract) ----------------


@pytest.mark.parametrize("chain_length", [1, 2, 3, 5])
def test_chain_emits_one_edge_per_hop(chain_length: int) -> None:
    """`chain_length` transfers → `chain_length` matview edges
    (one source-leg × one target-leg per Posted balanced transfer)."""
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=chain_length)

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    chain_violations = {
        v for v in detected
        if dict(v.identity).get("root_transfer_id") == "xfer-money-trail-0"
    }
    assert len(chain_violations) == chain_length, (
        f"chain_length={chain_length} should yield {chain_length} edges; "
        f"got {len(chain_violations)} from detected={detected}"
    )


def test_chain_root_carries_depth_zero() -> None:
    """The root transfer (no parent) has depth=0."""
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=3)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        depths_by_xfer = {
            dict(v.identity)["transfer_id"]: dict(v.identity)["depth"]
            for v in inv.detect(conn)
        }
    finally:
        conn.close()
    assert depths_by_xfer["xfer-money-trail-0"] == 0
    assert depths_by_xfer["xfer-money-trail-1"] == 1
    assert depths_by_xfer["xfer-money-trail-2"] == 2


def test_chain_shares_one_root_across_every_hop() -> None:
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=4)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        roots = {
            dict(v.identity)["root_transfer_id"] for v in inv.detect(conn)
        }
    finally:
        conn.close()
    assert roots == {"xfer-money-trail-0"}


def test_recipient_of_hop_n_is_sender_of_hop_n_plus_one() -> None:
    """Money walks: hop[i].recipient = hop[i+1].sender. Pinned by
    inspecting the `_transactions` legs directly."""
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=3,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT transfer_id, account_id, amount_money "
            f"FROM {_PREFIX}_transactions "
            f"ORDER BY transfer_id, amount_money",
        ).fetchall()
    finally:
        conn.close()
    # Each transfer has 2 legs: sorted by amount, [0]=sender (Debit, -),
    # [1]=recipient (Credit, +). Walk pairs.
    by_xfer: dict[str, list[tuple[str, float]]] = {}
    for tid, aid, amt in rows:
        by_xfer.setdefault(tid, []).append((aid, amt))
    # hop[0].recipient == hop[1].sender
    hop0_recipient = by_xfer["xfer-money-trail-0"][1][0]
    hop1_sender = by_xfer["xfer-money-trail-1"][0][0]
    assert hop0_recipient == hop1_sender == "acct-money-trail-hop-1"
    # hop[1].recipient == hop[2].sender
    hop1_recipient = by_xfer["xfer-money-trail-1"][1][0]
    hop2_sender = by_xfer["xfer-money-trail-2"][0][0]
    assert hop1_recipient == hop2_sender == "acct-money-trail-hop-2"


def test_generator_emit_writes_no_balance_rows() -> None:
    """Single-edge property: transfers-only ledger → no balance rows
    → no drift trip. Mirrors AnomalyGenerator's AT.0 finding."""
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=3,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        balance_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances",
        ).fetchone()[0]
    finally:
        conn.close()
    assert balance_count == 0


def test_intended_matches_deepest_edge() -> None:
    """`intended` returns the chain's leaf (depth = chain_length - 1)
    — the "story endpoint" of the trail."""
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=3,
    )
    expected = Violation.of(
        "inv_money_trail_edges",
        root_transfer_id="xfer-money-trail-0",
        transfer_id="xfer-money-trail-2",
        depth=2,
    )
    assert gen.intended == expected


def test_chain_length_one_emits_root_only() -> None:
    """chain_length=1 = no parent linkage; single transfer at depth=0.
    The "degenerate chain" case worth pinning as a boundary."""
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=1)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()
    assert len(detected) == 1
    only = next(iter(detected))
    assert dict(only.identity)["depth"] == 0


# ---- MoneyTrailView depth-threshold slice ---------------------------------


def test_money_trail_view_default_min_depth_is_zero() -> None:
    assert MoneyTrailView().min_depth == 0


def test_money_trail_view_default_returns_everything() -> None:
    """Default `min_depth=0` slice returns the full input set —
    matches the detector's all-edges return."""
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=3)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
        sliced = MoneyTrailView().slice(detected)
    finally:
        conn.close()
    assert sliced == detected


def test_money_trail_view_min_depth_drops_shallow_edges() -> None:
    """`min_depth=2` keeps only depth-≥-2 edges (grandchild + deeper)."""
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=4)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        sliced = MoneyTrailView(min_depth=2).slice(inv.detect(conn))
        depths = {dict(v.identity)["depth"] for v in sliced}
    finally:
        conn.close()
    assert depths == {2, 3}


def test_money_trail_view_threshold_above_max_returns_empty() -> None:
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=3)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        sliced = MoneyTrailView(min_depth=99).slice(inv.detect(conn))
    finally:
        conn.close()
    assert sliced == set()


def test_money_trail_view_is_monotonic_in_min_depth() -> None:
    """Raising min_depth can only shrink the slice; lowering can only
    grow it. Pure-function property the analyst surface depends on."""
    inv = MoneyTrailInvariant()
    gen = inv.scenario_for("CustomerSubledger", chain_length=5)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
        sizes = [
            len(MoneyTrailView(min_depth=d).slice(detected))
            for d in range(7)
        ]
    finally:
        conn.close()
    assert sizes == sorted(sizes, reverse=True), (
        f"slice sizes must be monotonically non-increasing in "
        f"min_depth; got {sizes}"
    )


def test_money_trail_view_drops_non_money_trail_violations() -> None:
    """Cross-invariant mix — a Violation without `depth` key is
    silently dropped. Defensive behaviour mirrors AnomalyView."""
    chain_viol = Violation.of(
        "inv_money_trail_edges",
        root_transfer_id="r", transfer_id="t", depth=1,
    )
    drift_viol = Violation.of(
        "drift",
        account_id="acct", balance_date=date(2030, 1, 1),
    )
    sliced = MoneyTrailView().slice({chain_viol, drift_viol})
    assert sliced == {chain_viol}
