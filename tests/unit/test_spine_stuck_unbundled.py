"""Unit tests for the AU.3.c stuck-unbundled family + registry edges.

Twin of `test_spine_stuck_pending.py`. The disjoint conditions vs
stuck_pending are encoded by the matview:
- `status = 'Posted'` (vs 'Pending')
- `bundle_id IS NULL`
- Per-rail `max_unbundled_age` (vs `max_pending_age`)

Empirical-edge question (the AU.3.c finding to surface): does Posted +
bundle_id IS NULL on a LEAF account trip drift? The matview's
computed_subledger_balance sums Posted legs grouped by account_id +
business_day — a single Posted leg's amount goes into Σ legs. Drift
fires if stored ≠ computed. We don't emit a balance row, so Σ legs is
positive but stored is missing... actually wait — drift's matview JOINs
current_daily_balances to computed_subledger_balance. If no balance row
exists for the account+day, no drift row materializes. So even though
the Posted leg is "seen" by computed_subledger_balance, the missing
balance row prevents drift from firing.

Prediction: single-edge. The test re-derives empirically.
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
    StuckPendingInvariant,
    StuckUnbundledGenerator,
    StuckUnbundledInvariant,
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

# spec_example has SubledgerCharge with max_unbundled_age=PT4H.
_UNBUNDLED_RAIL = "SubledgerCharge"
# TZ-skew-resistant overshoots (see `test_spine_stuck_pending.py` for
# rationale + `[[project-local-tz-convention]]`).
_TZ_SAFE_OVERSHOOT_FIRES = 50_000
_TZ_SAFE_OVERSHOOT_NON_FIRING = -50_000


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
    # AW.2 + AW.3 bridge — see stuck_pending's _fresh_db for rationale.
    import json
    from datetime import datetime
    from recon_gen.common.l2.config_table import replace_config
    l2_for_config = json.dumps({
        "rails": [
            {"name": "ExternalRailInbound", "max_pending_age_seconds": 86400},
            {"name": "SubledgerCharge", "max_unbundled_age_seconds": 14400},
        ]
    })
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}", l2_json=l2_for_config,
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
# StuckUnbundledInvariant — detect + scenario_for + smart constructor.
# ---------------------------------------------------------------------------


def test_invariant_carries_the_matview_name() -> None:
    assert StuckUnbundledInvariant().name == "stuck_unbundled"


def test_scenario_for_resolves_rail_against_the_shape() -> None:
    gen = StuckUnbundledInvariant().scenario_for(_UNBUNDLED_RAIL)
    assert gen.rail_name == _UNBUNDLED_RAIL
    # SubledgerCharge has max_unbundled_age = PT4H = 14400 seconds.
    assert gen.max_unbundled_age_seconds == 14400


def test_scenario_for_unknown_rail_fails_loud() -> None:
    with pytest.raises(ValueError, match="no rail named"):
        StuckUnbundledInvariant().scenario_for("NoSuchRail")


def test_scenario_for_rail_without_max_unbundled_age_fails_loud() -> None:
    # ExternalRailInbound has max_pending_age but no max_unbundled_age.
    with pytest.raises(ValueError, match="no max_unbundled_age"):
        StuckUnbundledInvariant().scenario_for("ExternalRailInbound")


# ---------------------------------------------------------------------------
# Emission round-trips.
# ---------------------------------------------------------------------------


def test_generator_trips_invariant() -> None:
    inv = StuckUnbundledInvariant()
    gen = inv.scenario_for(
        _UNBUNDLED_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
    )
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
        f"StuckUnbundledInvariant did not fire.\n"
        f"  intended: {intended}\n  detected: {detected}"
    )


def test_negative_overshoot_does_not_fire() -> None:
    inv = StuckUnbundledInvariant()
    clean = inv.scenario_for(
        _UNBUNDLED_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_NON_FIRING,
    )
    dirty = inv.scenario_for(
        _UNBUNDLED_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
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
    gen = StuckUnbundledInvariant().scenario_for(
        _UNBUNDLED_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
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


def test_emission_leaves_bundle_id_null() -> None:
    # The matview gates on bundle_id IS NULL — verify the plant matches.
    # _TX_COLS excludes bundle_id, so the INSERT leaves it NULL by
    # default; this test pins that contract so a future _TX_COLS edit
    # can't silently break stuck_unbundled.
    gen = StuckUnbundledInvariant().scenario_for(
        _UNBUNDLED_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        row = conn.execute(
            f"SELECT bundle_id, status FROM {_PREFIX}_transactions "
            f"WHERE id = ?",
            (gen.transaction_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    bundle_id, status = row
    assert bundle_id is None, (
        f"stuck_unbundled plant must leave bundle_id NULL; got {bundle_id!r}"
    )
    assert status == "Posted", (
        f"stuck_unbundled plant must be Posted (vs stuck_pending's "
        f"Pending); got {status!r}"
    )


# ---------------------------------------------------------------------------
# Empirical-edge check — the AU.3.c question: does Posted-but-unbundled
# trip drift on a leaf account?
# ---------------------------------------------------------------------------


def test_stuck_unbundled_emission_trips_only_itself() -> None:
    # Empirical: Posted leg with NO matching balance row.
    # `_drift` JOINs `current_daily_balances` to `_computed_subledger_
    # balance` on `(account_id, business_day_start)`. No balance row
    # means no JOIN match means no drift row materializes, even though
    # the Posted leg IS aggregated by computed_subledger_balance.
    gen = StuckUnbundledInvariant().scenario_for(
        _UNBUNDLED_RAIL, overshoot_seconds=_TZ_SAFE_OVERSHOOT_FIRES,
    )
    candidate_invariants: tuple[Invariant, ...] = (
        StuckUnbundledInvariant(),
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
            if inv.detect(conn):
                fired.add(type(inv))
    finally:
        conn.close()

    registered = set(INVARIANT_GENERATOR_EDGES[StuckUnbundledGenerator])
    assert fired == registered, (
        f"StuckUnbundledGenerator's empirical edges don't match the "
        f"registry.\n"
        f"  fired: {sorted(c.__name__ for c in fired)}\n"
        f"  registered: {sorted(c.__name__ for c in registered)}"
    )


def test_invariants_for_returns_single_edge() -> None:
    assert invariants_for(StuckUnbundledGenerator) == (
        StuckUnbundledInvariant,
    )


def test_generators_for_stuck_unbundled_invariant() -> None:
    assert generators_for(StuckUnbundledInvariant) == {
        StuckUnbundledGenerator,
    }


def test_iter_edges_includes_stuck_unbundled_edge() -> None:
    edges = list(iter_edges())
    assert (StuckUnbundledGenerator, StuckUnbundledInvariant) in edges


# ---------------------------------------------------------------------------
# Substitution-path property + identity round-trip.
# ---------------------------------------------------------------------------


def test_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    inv = StuckUnbundledInvariant()
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
            f"stuck_unbundled detector crossed a SQL-pushdown surface; "
            f"AR.5-style substitution-path test required.\n  sql: {sql!r}"
        )


def test_violation_identity_matches_detect_projection() -> None:
    gen = StuckUnbundledInvariant().scenario_for(_UNBUNDLED_RAIL)
    expected = Violation.of(
        "stuck_unbundled",
        transaction_id=gen.transaction_id,
        rail_name=gen.rail_name,
    )
    assert gen.intended == expected
