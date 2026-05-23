"""Unit tests for the AU.3.b stuck-pending family + registry.

First transaction-based + L2-coupled spine invariant — distinct from
the balance-only AU.1/AU.3.a shapes:

- `scenario_for(rail_name)` reads the rail's `max_pending_age` from L2;
  fails loud if the rail doesn't have one (uncovered rails can't host a
  scenario — the matview's `pending_age_cases` resolves NULL and the
  outer WHERE excludes them).
- Plant is a single Pending transaction with `posting = now() − (cap +
  overshoot_seconds)`; no balance row, no related Posted rows.
- `overshoot_seconds = 0` is the non-violating shape (matview filter is
  `age_seconds > cap`, so age == cap doesn't fire). AP.2 convention
  adapted to the seconds-unit knob.

Empirical-edge prediction (verified below): stuck_pending trips ONLY
itself. Pending transactions are excluded from drift's computed_subledger
balance (status='Posted' filter); no balance row ⇒ no overdraft / no
expected_eod / no ledger_drift / no drift. Single-edge registry entry.
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
    ExpectedEodBalanceInvariant,
    Invariant,
    LedgerDriftInvariant,
    OverdraftInvariant,
    StuckPendingGenerator,
    StuckPendingInvariant,
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

# spec_example has ExternalRailInbound with max_pending_age=PT24H, and
# SubledgerCharge with max_unbundled_age=PT4H. ExternalRailInbound is
# stuck_pending's covered rail.
_PENDING_RAIL = "ExternalRailInbound"

# Application convention is LOCAL TZ for stored timestamps (Oracle has
# no proper WITH-TIME-ZONE semantics). SQLite's CURRENT_TIMESTAMP returns
# UTC regardless of system TZ, so the in-process test sees `age_seconds`
# inflated by the system TZ offset. Pick overshoots well outside ±12h to
# stay TZ-skew-resistant — `12h*3600 = 43200s` safety margin.
_TZ_SAFE_OVERSHOOT_FIRES = 50_000  # ~14h past the cap; fires on any TZ
_TZ_SAFE_OVERSHOOT_NON_FIRING = -50_000  # ~14h under; doesn't fire on any TZ


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
    # AW.2 bridge: matview's age_seconds reads `(SELECT as_of FROM
    # <prefix>_config)`. Seed an initial row so refresh-time subquery
    # returns a value instead of NULL. AW.5 will retrofit the generator
    # to use this same as_of for plant timing → fully deterministic.
    # For now (bridge), match the generator's `datetime.now()`-derived
    # posting with `datetime.now()`-derived as_of so age computations
    # match the pre-AW.2 behavior.
    from datetime import datetime
    from recon_gen.common.l2.config_table import replace_config
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}", l2_json="{}",
        as_of=datetime.now(),  # typing-smell: ignore[no-datetime-now]: bridge test harness — AW.5 retrofits to pinned LOCKED_ANCHOR
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
# StuckPendingInvariant — detect + scenario_for + smart-constructor.
# ---------------------------------------------------------------------------


def test_stuck_pending_invariant_carries_the_matview_name() -> None:
    assert StuckPendingInvariant().name == "stuck_pending"


def test_scenario_for_resolves_rail_against_the_shape() -> None:
    gen = StuckPendingInvariant().scenario_for(_PENDING_RAIL)
    assert gen.rail_name == _PENDING_RAIL
    # ExternalRailInbound has max_pending_age = PT24H = 86400 seconds.
    assert gen.max_pending_age_seconds == 86400
    assert gen.overshoot_seconds == 60  # default


def test_scenario_for_unknown_rail_fails_loud() -> None:
    with pytest.raises(ValueError, match="no rail named"):
        StuckPendingInvariant().scenario_for("NoSuchRail")


def test_scenario_for_rail_without_max_pending_age_fails_loud() -> None:
    # spec_example has rails without max_pending_age (e.g. SubledgerCharge
    # has max_unbundled_age but no max_pending_age). The smart constructor
    # refuses — the matview would silently exclude such a plant.
    with pytest.raises(ValueError, match="no max_pending_age"):
        StuckPendingInvariant().scenario_for("SubledgerCharge")


# ---------------------------------------------------------------------------
# Emission round-trips — intended Violation surfaces in detect().
# ---------------------------------------------------------------------------


def test_generator_trips_invariant() -> None:
    inv = StuckPendingInvariant()
    gen = inv.scenario_for(_PENDING_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES)
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
        f"StuckPendingInvariant did not fire.\n"
        f"  intended: {intended}\n  detected: {detected}"
    )


def test_overshoot_zero_does_not_fire() -> None:
    # Matview filter is `age_seconds > max_pending_age_seconds` (strict
    # greater-than). Negative overshoot ⇒ age < cap ⇒ filter excludes.
    # Use the TZ-skew-resistant negative overshoot — SQLite's UTC vs
    # local skew can otherwise push age above cap unexpectedly.
    inv = StuckPendingInvariant()
    clean = inv.scenario_for(
        _PENDING_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_NON_FIRING,
    )
    dirty = inv.scenario_for(
        _PENDING_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
    )

    conn = _fresh_db()
    try:
        clean.emit(conn)
        conn.commit()
        _refresh(conn)
        assert dirty.intended not in inv.detect(conn)
    finally:
        conn.close()


def test_generator_emits_zero_balance_rows() -> None:
    # Transaction-based invariant — no daily_balances row should
    # materialize from a stuck_pending plant.
    gen = StuckPendingInvariant().scenario_for(
        _PENDING_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        balance_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances",
        ).fetchone()[0]
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert balance_count == 0
    assert tx_count == 1


# ---------------------------------------------------------------------------
# Single-edge registry — stuck_pending trips ONLY itself (the AU.3.b
# prediction, verified empirically).
# ---------------------------------------------------------------------------


def test_stuck_pending_emission_trips_only_itself() -> None:
    # Empirical verification: a stuck_pending plant doesn't trip any
    # other promoted invariant. Pending transactions don't contribute
    # to drift's Posted-filtered Σ legs; no balance row ⇒ no overdraft /
    # expected_eod / ledger_drift; only stuck_pending fires.
    gen = StuckPendingInvariant().scenario_for(
        _PENDING_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
    )
    candidate_invariants: tuple[Invariant, ...] = (
        StuckPendingInvariant(),
        DriftInvariant(),
        LedgerDriftInvariant(),
        OverdraftInvariant(),
        ExpectedEodBalanceInvariant(),
    )
    fired: set[type[Invariant]] = set()

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        for inv in candidate_invariants:
            # Stuck-pending identity uses transaction_id, not account_id;
            # the other invariants' identities use account_id. To detect
            # "fires" uniformly, just check non-empty.
            if inv.detect(conn):
                fired.add(type(inv))
    finally:
        conn.close()

    registered = set(INVARIANT_GENERATOR_EDGES[StuckPendingGenerator])
    assert fired == registered, (
        f"StuckPendingGenerator's empirical edges don't match registry.\n"
        f"  fired: {sorted(c.__name__ for c in fired)}\n"
        f"  registered: {sorted(c.__name__ for c in registered)}"
    )


def test_invariants_for_returns_single_edge() -> None:
    edges = invariants_for(StuckPendingGenerator)
    assert edges == (StuckPendingInvariant,)


def test_generators_for_stuck_pending_invariant() -> None:
    assert generators_for(StuckPendingInvariant) == {StuckPendingGenerator}


def test_iter_edges_includes_stuck_pending_edge() -> None:
    edges = list(iter_edges())
    assert (StuckPendingGenerator, StuckPendingInvariant) in edges


# ---------------------------------------------------------------------------
# Substitution-path property (AR.5 lesson codified for every detector).
# ---------------------------------------------------------------------------


def test_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    inv = StuckPendingInvariant()
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
            f"stuck_pending detector crossed a SQL-pushdown surface; "
            f"AR.5-style substitution-path test required.\n  sql: {sql!r}"
        )


# ---------------------------------------------------------------------------
# Violation identity round-trip + L2-coupling fidelity.
# ---------------------------------------------------------------------------


def test_violation_identity_matches_detect_projection() -> None:
    gen = StuckPendingInvariant().scenario_for(_PENDING_RAIL)
    expected = Violation.of(
        "stuck_pending",
        transaction_id=gen.transaction_id,
        rail_name=gen.rail_name,
    )
    assert gen.intended == expected
