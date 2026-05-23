"""Unit tests for the AU.4 limit-breach family + registry edge.

`LimitBreachInvariant` is the deepest L2-coupled L1 spine invariant:
its smart constructor reads the L2's `LimitSchedule.cap` AS a
load-bearing input to the plant's amount (cap + overshoot). AP.3
finding #4 — the `from_instance` disproof of the "blind generator"
hypothesis — applies here.

Empirical-edge prediction (per registry comment): single-edge.
Posted transaction with no balance row ⇒ no drift JOIN match ⇒
no drift fire. This test verifies; if surprising edges show up, the
AU.x cadence's stop-and-evaluate catches them.

Three scenario_for guards:
1. Unknown (parent_role, rail, direction) triple → ValueError
2. Triple with no LimitSchedule → ValueError
3. parent_role with no child accounts → ValueError

Both directions exercised: spec_example has
(CustomerLedger, ExternalRailOutbound, Outbound, cap=5000) and
(CustomerLedger, ExternalRailInbound, Inbound, cap=3000).
"""

from __future__ import annotations

import sqlite3
from datetime import date
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
    LimitBreachGenerator,
    LimitBreachInvariant,
    OverdraftInvariant,
    StuckPendingInvariant,
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

_PARENT = "CustomerLedger"
_OUTBOUND_RAIL = "ExternalRailOutbound"
_INBOUND_RAIL = "ExternalRailInbound"
_OUTBOUND_CAP = 5000.0
_INBOUND_CAP = 3000.0


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
    # AW.4 bridge: matview's `cap` reads from `<prefix>_config.l2_yaml`
    # via LEFT JOIN on `$.limit_schedules`. Seed the config row with
    # the LimitSchedules the matview iterates. Matches spec_example's
    # declared schedules — the same Outbound+Inbound caps the tests
    # exercise against (5000 and 3000 respectively).
    import json
    from datetime import datetime
    from recon_gen.common.l2.config_table import replace_config
    l2_for_config = json.dumps({
        "limit_schedules": [
            {
                "parent_role": "CustomerLedger",
                "rail": "ExternalRailOutbound",
                "direction": "Outbound",
                "cap": 5000,
            },
            {
                "parent_role": "CustomerLedger",
                "rail": "ExternalRailInbound",
                "direction": "Inbound",
                "cap": 3000,
            },
        ],
    })
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}", l2_json=l2_for_config,
        as_of=datetime(2030, 1, 1, 12, 0, 0),  # arbitrary; limit_breach is wall-clock-agnostic
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
# LimitBreachInvariant — detect + scenario_for + smart-constructor guards.
# ---------------------------------------------------------------------------


def test_invariant_carries_the_matview_name() -> None:
    assert LimitBreachInvariant().name == "limit_breach"


def test_scenario_for_outbound_resolves_cap_from_l2() -> None:
    gen = LimitBreachInvariant().scenario_for(
        _PARENT, _OUTBOUND_RAIL, direction="Outbound",
    )
    assert gen.cap == _OUTBOUND_CAP
    assert gen.direction == "Outbound"
    assert gen.account_parent_role == _PARENT


def test_scenario_for_inbound_resolves_cap_from_l2() -> None:
    gen = LimitBreachInvariant().scenario_for(
        _PARENT, _INBOUND_RAIL, direction="Inbound",
    )
    assert gen.cap == _INBOUND_CAP
    assert gen.direction == "Inbound"


def test_scenario_for_unknown_parent_role_fails_loud() -> None:
    with pytest.raises(ValueError, match="no LimitSchedule matches"):
        LimitBreachInvariant().scenario_for("NoSuchParent", _OUTBOUND_RAIL)


def test_scenario_for_unknown_rail_fails_loud() -> None:
    with pytest.raises(ValueError, match="no LimitSchedule matches"):
        LimitBreachInvariant().scenario_for(_PARENT, "NoSuchRail")


def test_scenario_for_wrong_direction_fails_loud() -> None:
    # ExternalRailInbound has an Inbound LimitSchedule but not Outbound;
    # asking for Outbound on it should fail loud.
    with pytest.raises(ValueError, match="no LimitSchedule matches"):
        LimitBreachInvariant().scenario_for(
            _PARENT, _INBOUND_RAIL, direction="Outbound",
        )


# ---------------------------------------------------------------------------
# Emission round-trips — both directions fire on plant.
# ---------------------------------------------------------------------------


def test_outbound_plant_trips_invariant() -> None:
    inv = LimitBreachInvariant()
    gen = inv.scenario_for(
        _PARENT, _OUTBOUND_RAIL, direction="Outbound", overshoot=100.0,
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
        f"LimitBreachInvariant did not fire on Outbound plant.\n"
        f"  intended: {intended}\n  detected: {detected}"
    )


def test_inbound_plant_trips_invariant() -> None:
    inv = LimitBreachInvariant()
    gen = inv.scenario_for(
        _PARENT, _INBOUND_RAIL, direction="Inbound", overshoot=100.0,
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

    assert intended in detected


def test_overshoot_zero_does_not_fire() -> None:
    # AP.2 non-violating: matview filter is strict `>`; overshoot=0 ⇒
    # SUM = cap ⇒ filter excludes ⇒ no fire.
    inv = LimitBreachInvariant()
    clean = inv.scenario_for(
        _PARENT, _OUTBOUND_RAIL, direction="Outbound", overshoot=0.0,
    )
    dirty = inv.scenario_for(
        _PARENT, _OUTBOUND_RAIL, direction="Outbound", overshoot=100.0,
    )

    conn = _fresh_db()
    try:
        clean.emit(conn)
        conn.commit()
        _refresh(conn)
        assert dirty.intended not in inv.detect(conn)
    finally:
        conn.close()


def test_plant_amount_money_sign_matches_direction() -> None:
    # CHECK constraint: Debit ⇒ money ≤ 0; Credit ⇒ money ≥ 0.
    # Verify the plant respects the sign for both directions.
    outbound_gen = LimitBreachInvariant().scenario_for(
        _PARENT, _OUTBOUND_RAIL, direction="Outbound", overshoot=100.0,
    )
    inbound_gen = LimitBreachInvariant().scenario_for(
        _PARENT, _INBOUND_RAIL, direction="Inbound", overshoot=100.0,
    )
    conn = _fresh_db()
    try:
        outbound_gen.emit(conn)
        inbound_gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT amount_direction, amount_money "
            f"FROM {_PREFIX}_transactions ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    by_direction = {d: m for d, m in rows}
    # Outbound = Debit ⇒ money ≤ 0
    assert by_direction["Debit"] < 0
    assert abs(by_direction["Debit"]) == _OUTBOUND_CAP + 100.0
    # Inbound = Credit ⇒ money ≥ 0
    assert by_direction["Credit"] > 0
    assert by_direction["Credit"] == _INBOUND_CAP + 100.0


# ---------------------------------------------------------------------------
# Empirical edge — single-edge contract (Posted leg, no balance row).
# ---------------------------------------------------------------------------


def test_limit_breach_emission_trips_only_itself() -> None:
    gen = LimitBreachInvariant().scenario_for(
        _PARENT, _OUTBOUND_RAIL, direction="Outbound", overshoot=100.0,
    )
    candidate_invariants: tuple[Invariant, ...] = (
        LimitBreachInvariant(),
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

    registered = set(INVARIANT_GENERATOR_EDGES[LimitBreachGenerator])
    assert fired == registered, (
        f"LimitBreachGenerator's empirical edges don't match registry.\n"
        f"  fired: {sorted(c.__name__ for c in fired)}\n"
        f"  registered: {sorted(c.__name__ for c in registered)}"
    )


def test_invariants_for_returns_single_edge() -> None:
    assert invariants_for(LimitBreachGenerator) == (LimitBreachInvariant,)


def test_generators_for_limit_breach_invariant() -> None:
    assert generators_for(LimitBreachInvariant) == {LimitBreachGenerator}


def test_iter_edges_includes_limit_breach_edge() -> None:
    edges = list(iter_edges())
    assert (LimitBreachGenerator, LimitBreachInvariant) in edges


# ---------------------------------------------------------------------------
# Substitution-path + identity round-trip.
# ---------------------------------------------------------------------------


def test_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    inv = LimitBreachInvariant()
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
            f"limit_breach detector crossed a SQL-pushdown surface; "
            f"AR.5-style substitution-path test required.\n  sql: {sql!r}"
        )


def test_violation_identity_matches_detect_projection() -> None:
    gen = LimitBreachInvariant().scenario_for(
        _PARENT, _OUTBOUND_RAIL, direction="Outbound",
    )
    expected = Violation.of(
        "limit_breach",
        account_id=gen.account_id,
        business_day=date(2030, 1, 1),
        rail_name=_OUTBOUND_RAIL,
        direction="Outbound",
    )
    assert gen.intended == expected
