"""Unit tests for the AU.3.a expected-EOD-balance family + registry.

Mirrors `test_spine_overdraft.py`'s shape — same balance-only invariant
class, same two-edge registry entry, same AU.0/AU.2 empirical-edge
patterns. The differences from overdraft:

- The matview gates on `expected_eod_balance IS NOT NULL` (per-row
  column) instead of `money < 0` (universal filter).
- Identity carries `variance` (money − expected) instead of stored_balance.
- `variance=0.0` ⇒ stored == expected ⇒ no row materializes (matches
  overdraft's magnitude=0 non-violating convention).

Layers (mirroring test_spine_overdraft.py's two-layer assertion model):

1. ExpectedEodBalanceInvariant + ExpectedEodBalanceGenerator each behave
   as designed against the real emitted matview SQL.
2. The INVARIANT_GENERATOR_EDGES registry's two-edge entry is empirical
   (re-derive from detect() calls, assert == registered).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    INVARIANT_GENERATOR_EDGES,
    DriftInvariant,
    ExpectedEodBalanceGenerator,
    ExpectedEodBalanceInvariant,
    Invariant,
    LedgerDriftInvariant,
    Violation,
    generators_for,
    invariants_for,
    iter_edges,
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
# ExpectedEodBalanceInvariant — detect + scenario_for + smart-constructor.
# ---------------------------------------------------------------------------


def test_expected_eod_invariant_carries_the_matview_name() -> None:
    assert (
        ExpectedEodBalanceInvariant().name == "expected_eod_balance_breach"
    )


def test_scenario_for_resolves_leaf_role() -> None:
    gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerSubledger", expected=200.0, variance=5.0,
    )
    assert gen.account_role == "CustomerSubledger"
    assert gen.account_parent_role == "CustomerLedger"
    assert gen.expected == 200.0
    assert gen.variance == 5.0


def test_scenario_for_resolves_parent_role() -> None:
    gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerLedger", expected=100.0, variance=3.0,
    )
    assert gen.account_role == "CustomerLedger"
    assert gen.account_parent_role is None


def test_scenario_for_unknown_role_fails_loud() -> None:
    with pytest.raises(ValueError, match="no expected-EOD-eligible"):
        ExpectedEodBalanceInvariant().scenario_for("NoSuchRole", variance=5.0)


# ---------------------------------------------------------------------------
# Emission round-trips — intended Violation surfaces in detect().
# ---------------------------------------------------------------------------


def test_generator_trips_invariant() -> None:
    inv = ExpectedEodBalanceInvariant()
    gen = inv.scenario_for("CustomerSubledger", expected=100.0, variance=5.0)
    intended = gen.intended

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    assert intended in detected, (
        f"ExpectedEodBalanceInvariant did not fire.\n"
        f"  intended: {intended}\n  detected: {detected}"
    )


def test_variance_zero_does_not_fire() -> None:
    # AP.2 non-violating: variance=0 ⇒ money == expected ⇒ matview
    # filters the row out.
    inv = ExpectedEodBalanceInvariant()
    clean = inv.scenario_for("CustomerSubledger", expected=100.0, variance=0.0)
    dirty = inv.scenario_for("CustomerSubledger", expected=100.0, variance=5.0)

    conn = _fresh_db()
    try:
        clean.emit(conn)
        conn.commit()
        _refresh(conn)
        assert dirty.intended not in inv.detect(conn)
    finally:
        conn.close()


def test_generator_emits_zero_transactions() -> None:
    # Balance-only invariant — same shape as overdraft. No leg arithmetic.
    gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerSubledger", expected=100.0, variance=5.0,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert tx_count == 0


# ---------------------------------------------------------------------------
# AU.0-style empirical edge: leaf plant also trips drift.
# ---------------------------------------------------------------------------


def test_leaf_plant_also_trips_drift() -> None:
    # The AU.0 empirical pattern: balance-only invariant plant on a leaf
    # account with zero transactions ⇒ drift's matview filter is
    # satisfied (parent_role IS NOT NULL; stored ≠ Σ legs = 0).
    gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerSubledger", expected=100.0, variance=5.0,
    )
    drift_intended = gen.also_trips_drift
    assert drift_intended is not None, (
        "spec_example's CustomerSubledger is a leaf; expected-EOD-leaf "
        "plant MUST advertise the drift edge"
    )
    drift_inv = DriftInvariant()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = drift_inv.detect(conn)
    finally:
        conn.close()
    assert drift_intended in detected, (
        f"DriftInvariant did not fire on the expected-EOD leaf plant.\n"
        f"  intended: {drift_intended}\n  detected: {detected}"
    )


def test_parent_plant_does_not_advertise_drift_edge() -> None:
    gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerLedger", expected=100.0, variance=5.0,
    )
    assert gen.account_parent_role is None
    assert gen.also_trips_drift is None
    # Confirm at the matview level.
    drift_inv = DriftInvariant()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = drift_inv.detect(conn)
        assert not any(
            dict(v.identity).get("account_id") == gen.account_id
            for v in detected
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Registry — empirical-edge contract for the two-edge entry.
# ---------------------------------------------------------------------------


def test_generator_emission_fires_exactly_the_registered_edges() -> None:
    # UNION across leaf + parent variants matches registered.
    leaf_gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerSubledger", expected=100.0, variance=5.0,
    )
    parent_gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerLedger", expected=100.0, variance=5.0,
    )
    candidate_invariants: tuple[Invariant, ...] = (
        ExpectedEodBalanceInvariant(),
        DriftInvariant(),
        LedgerDriftInvariant(),
    )
    fired: set[type[Invariant]] = set()
    for gen in (leaf_gen, parent_gen):
        conn = _fresh_db()
        try:
            gen.emit(conn)
            conn.commit()
            _refresh(conn)
            for inv in candidate_invariants:
                hits = {
                    v for v in inv.detect(conn)
                    if dict(v.identity).get("account_id") == gen.account_id
                }
                if hits:
                    fired.add(type(inv))
        finally:
            conn.close()

    registered = set(INVARIANT_GENERATOR_EDGES[ExpectedEodBalanceGenerator])
    assert fired == registered, (
        f"ExpectedEodBalanceGenerator's empirical edges UNION across "
        f"(leaf, parent) must match the registry.\n"
        f"  empirical UNION: {sorted(c.__name__ for c in fired)}\n"
        f"  registered: {sorted(c.__name__ for c in registered)}"
    )


def test_invariants_for_returns_two_edges() -> None:
    edges = invariants_for(ExpectedEodBalanceGenerator)
    assert edges == (ExpectedEodBalanceInvariant, DriftInvariant)


def test_generators_for_expected_eod_invariant_returns_its_generator() -> None:
    assert generators_for(ExpectedEodBalanceInvariant) == {
        ExpectedEodBalanceGenerator,
    }


def test_iter_edges_includes_expected_eod_edges() -> None:
    edges = list(iter_edges())
    assert (
        ExpectedEodBalanceGenerator, ExpectedEodBalanceInvariant,
    ) in edges
    assert (ExpectedEodBalanceGenerator, DriftInvariant) in edges


# ---------------------------------------------------------------------------
# Substitution-path property (AR.5 lesson codified for every detector).
# ---------------------------------------------------------------------------


def test_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    inv = ExpectedEodBalanceInvariant()
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
            f"expected-EOD detector crossed a SQL-pushdown surface; "
            f"AR.5-style substitution-path test required.\n  sql: {sql!r}"
        )


# ---------------------------------------------------------------------------
# Violation identity round-trip vs detect projection.
# ---------------------------------------------------------------------------


def test_violation_identity_matches_detect_projection() -> None:
    gen = ExpectedEodBalanceInvariant().scenario_for(
        "CustomerSubledger", expected=100.0, variance=5.0,
    )
    expected = Violation.of(
        "expected_eod_balance_breach",
        account_id=gen.account_id,
        business_day=gen.anchor_day,
        variance=5.0,
    )
    assert gen.intended == expected
