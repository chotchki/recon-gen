"""Unit tests for the AS.2 drift family + registry.

Two layers of assertion:
1. DriftInvariant + LedgerDriftInvariant + DriftGenerator each behave
   as designed against the real emitted matview SQL (in-process
   SQLite, AP.3 harness pattern).
2. The `INVARIANT_GENERATOR_EDGES` registry's claim — that a
   `DriftGenerator` emission trips BOTH `DriftInvariant` AND
   `LedgerDriftInvariant` — is empirical, not just written down: the
   test re-derives the edge set from actual detect() calls and asserts
   the registry matches.
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
    DriftGenerator,
    DriftInvariant,
    Invariant,
    LedgerDriftInvariant,
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
# DriftInvariant — detect from `<prefix>_drift`, scenario_for resolves shape.
# ---------------------------------------------------------------------------


def test_drift_invariant_carries_the_matview_name() -> None:
    # The spine link: `Invariant.name` matches the matview suffix the
    # detector reads. AS.2's contract for promoted invariants.
    assert DriftInvariant().name == "drift"
    assert LedgerDriftInvariant().name == "ledger_drift"


def test_drift_scenario_for_resolves_role_against_the_shape() -> None:
    # The smart constructor: a shape selector (role name) in, concrete
    # coordinates (account_id, parent_role, parent_account_id) out.
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    assert gen.child_role == "CustomerSubledger"
    assert gen.parent_role == "CustomerLedger"
    # The parent account is resolved when the L2 has an account at the
    # child's parent_role — for spec_example's CustomerSubledger →
    # CustomerLedger pair, there IS a parent account, so the ledger
    # edge is live.
    assert gen.parent_account_id is not None
    assert gen.parent_account_role == "CustomerLedger"


def test_drift_scenario_for_unknown_role_fails_loud() -> None:
    # AP.3's finding promoted into the production type: a role that
    # isn't in the shape can't manufacture a scenario.
    with pytest.raises(ValueError, match="no drift-eligible"):
        DriftInvariant().scenario_for("NoSuchRole", magnitude=5.0)


# ---------------------------------------------------------------------------
# DriftGenerator — emission trips DriftInvariant on the child.
# ---------------------------------------------------------------------------


def test_drift_generator_trips_drift_invariant_on_the_child() -> None:
    inv = DriftInvariant()
    gen = inv.scenario_for("CustomerSubledger", magnitude=5.0)
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
        f"DriftInvariant.detect did not include the intended violation.\n"
        f"  intended: {intended}\n"
        f"  detected: {detected}"
    )


# ---------------------------------------------------------------------------
# The many-to-many edge: one emission, two detectors fire.
# ---------------------------------------------------------------------------


def test_drift_generator_also_trips_ledger_drift_on_the_parent() -> None:
    # The AP.3 finding's concrete manifestation: ledger_drift fires on
    # the PARENT account because Σ(child.money) ≠ parent.stored. One
    # emission, two detectors — the many-to-many edge that motivates
    # the registry.
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    ledger_intended = gen.also_trips_ledger_drift
    assert ledger_intended is not None, (
        "spec_example must have a CustomerLedger parent for this test"
    )
    ledger_inv = LedgerDriftInvariant()

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = ledger_inv.detect(conn)
    finally:
        conn.close()

    assert ledger_intended in detected, (
        f"LedgerDriftInvariant did not fire on the parent.\n"
        f"  intended: {ledger_intended}\n"
        f"  detected: {detected}"
    )


def test_drift_generator_emission_fires_exactly_the_registered_edges() -> None:
    # Empirical edge contract: re-derive the (generator → {invariants})
    # map from actual detect() calls after emission, then assert the
    # registry matches. If a future change adds a detect path that
    # fires on drift emission (e.g., a new drift-related matview), this
    # assertion forces the registry update — invariants stay structural,
    # not memory.
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    candidate_invariants: tuple[Invariant, ...] = (
        DriftInvariant(), LedgerDriftInvariant(),
    )
    fired: set[type[Invariant]] = set()

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        for inv in candidate_invariants:
            if inv.detect(conn):
                fired.add(type(inv))
    finally:
        conn.close()

    registered = set(INVARIANT_GENERATOR_EDGES[DriftGenerator])
    assert fired == registered, (
        f"DriftGenerator's empirical edges don't match the registry.\n"
        f"  fired (empirical): {sorted(c.__name__ for c in fired)}\n"
        f"  registered: {sorted(c.__name__ for c in registered)}"
    )


# ---------------------------------------------------------------------------
# Registry helpers — lookups + reverse-lookups + flat edge iteration.
# ---------------------------------------------------------------------------


def test_invariants_for_returns_the_registered_tuple() -> None:
    edges = invariants_for(DriftGenerator)
    assert edges == (DriftInvariant, LedgerDriftInvariant)


def test_invariants_for_unregistered_generator_is_empty() -> None:
    class _UnknownGenerator:  # not registered
        pass
    assert invariants_for(_UnknownGenerator) == ()  # type: ignore[arg-type]: deliberately passing a non-registered class to assert the empty-tuple fallback


def test_generators_for_reverse_lookup() -> None:
    # Both invariants in the drift family are tripped by DriftGenerator —
    # the reverse-lookup confirms the many-to-many shape.
    #
    # AU.1 update: DriftInvariant is now tripped by TWO generators —
    # DriftGenerator (the canonical drift plant) AND OverdraftGenerator
    # (per the AU.0 empirical edge: an overdraft plant on a LEAF account
    # satisfies drift's matview filter, so drift fires too). The reverse-
    # lookup grows accordingly. LedgerDriftInvariant stays single-source
    # (DriftGenerator only) because overdraft plants on a parent-role
    # account aren't part of the OverdraftGenerator default; that variant
    # lands as a composition shape in AU.2.
    from recon_gen.common.spine import OverdraftGenerator
    assert generators_for(DriftInvariant) == {DriftGenerator, OverdraftGenerator}
    assert generators_for(LedgerDriftInvariant) == {DriftGenerator}


def test_iter_edges_yields_every_pair() -> None:
    edges = list(iter_edges())
    assert (DriftGenerator, DriftInvariant) in edges
    assert (DriftGenerator, LedgerDriftInvariant) in edges
    # Edge count matches the sum of tuple lengths across the registry.
    assert len(edges) == sum(
        len(invs) for invs in INVARIANT_GENERATOR_EDGES.values()
    )


# ---------------------------------------------------------------------------
# RNG convention plumbed through `scenario_for`.
# ---------------------------------------------------------------------------


def test_scenario_for_threads_seed_into_generator_rng() -> None:
    # The AS.1 RNG convention: scenario_for takes a seed; the generator
    # carries `rng: random.Random` constructed via `scenario_rng(seed)`.
    # Two generators built with the same seed get equivalent rng streams.
    a = DriftInvariant().scenario_for("CustomerSubledger", seed=42)
    b = DriftInvariant().scenario_for("CustomerSubledger", seed=42)
    assert [a.rng.random() for _ in range(4)] == [
        b.rng.random() for _ in range(4)
    ]


def test_scenario_for_default_seed_is_deterministic() -> None:
    a = DriftInvariant().scenario_for("CustomerSubledger")
    b = DriftInvariant().scenario_for("CustomerSubledger")
    assert [a.rng.random() for _ in range(4)] == [
        b.rng.random() for _ in range(4)
    ]
