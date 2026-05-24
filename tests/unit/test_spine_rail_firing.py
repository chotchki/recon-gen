"""AY.2.b — unit tests for RailFiringGenerator.

Seed-color coverage generator: emits ONE Posted firing of an L2-
declared Rail (M.4.2 broad-mode). Two-leg rails emit 2 legs summing
to zero; single-leg rails emit 1 leg in the rail's declared
leg_direction. No matching matview Invariant; `intended` returns a
`CoverageObservation` per the AY.2.a evidence-currency layering.
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
    ClaimedAccountsGenerator,
    CoverageObservation,
    RailFiringFactory,
    RailFiringGenerator,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
# spec_example.yaml rails picked for kind coverage. ExternalRailInbound
# is a two-leg rail (source_role + destination_role declared);
# SubledgerCharge is a single-leg rail (leg_direction='Debit').
_TWO_LEG_RAIL = "ExternalRailInbound"
_SINGLE_LEG_DEBIT_RAIL = "SubledgerCharge"
_SINGLE_LEG_CREDIT_RAIL = "BatchPayoutClose"


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


# ---------------------------------------------------------------------------
# Factory — rail-kind resolution.
# ---------------------------------------------------------------------------


def test_factory_resolves_two_leg_rail() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _TWO_LEG_RAIL, anchor_day=date(2030, 1, 1),
    )
    assert isinstance(gen, RailFiringGenerator)
    assert gen.is_two_leg is True
    assert gen.account_id_b is not None


def test_factory_resolves_single_leg_debit_rail() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _SINGLE_LEG_DEBIT_RAIL, anchor_day=date(2030, 1, 1),
    )
    assert gen.is_two_leg is False
    assert gen.account_id_b is None
    assert gen.single_leg_direction == "Debit"


def test_factory_resolves_single_leg_credit_rail() -> None:
    """SingleLegRail with leg_direction='Credit' resolves the right
    leg direction (not the Debit default)."""
    gen = RailFiringFactory().scenario_for_rail(
        _SINGLE_LEG_CREDIT_RAIL, anchor_day=date(2030, 1, 1),
    )
    assert gen.is_two_leg is False
    assert gen.single_leg_direction == "Credit"


def test_factory_rejects_unknown_rail_loudly() -> None:
    with pytest.raises(ValueError, match="not declared on the L2 instance"):
        RailFiringFactory().scenario_for_rail(
            "DefinitelyNotARailName",
        )


# ---------------------------------------------------------------------------
# intended subtype + identity.
# ---------------------------------------------------------------------------


def test_intended_is_a_coverage_observation() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _TWO_LEG_RAIL, anchor_day=date(2030, 1, 1),
    )
    intended = gen.intended
    assert isinstance(intended, CoverageObservation), (
        f"RailFiringGenerator's intended should be a CoverageObservation; "
        f"got {type(intended).__name__}"
    )
    items = dict(intended.identity)
    assert intended.invariant == "rail_firing"
    assert items["rail_name"] == _TWO_LEG_RAIL
    assert items["transfer_id"] == gen.transfer_id
    assert items["firing_seq"] == 1
    assert items["leg_count"] == 2


def test_intended_leg_count_matches_rail_kind() -> None:
    two = RailFiringFactory().scenario_for_rail(
        _TWO_LEG_RAIL, anchor_day=date(2030, 1, 1),
    )
    one = RailFiringFactory().scenario_for_rail(
        _SINGLE_LEG_DEBIT_RAIL, anchor_day=date(2030, 1, 1),
    )
    assert dict(two.intended.identity)["leg_count"] == 2
    assert dict(one.intended.identity)["leg_count"] == 1


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _TWO_LEG_RAIL, anchor_day=date(2030, 1, 1),
    )
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert gen.claimed_accounts == frozenset({
        gen.account_id_a, gen.account_id_b,
    })


def test_single_leg_claimed_accounts_just_one() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _SINGLE_LEG_DEBIT_RAIL, anchor_day=date(2030, 1, 1),
    )
    assert gen.claimed_accounts == frozenset({gen.account_id_a})


# ---------------------------------------------------------------------------
# Emit row shape.
# ---------------------------------------------------------------------------


def test_two_leg_emit_writes_balanced_pair_sharing_transfer_id() -> None:
    """Two-leg rail firing: 2 legs (debit + credit) summing to zero,
    sharing one transfer_id, both stamped with rail_name."""
    gen = RailFiringFactory().scenario_for_rail(
        _TWO_LEG_RAIL, anchor_day=date(2030, 1, 1), amount=250.0,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT transfer_id, amount_money, amount_direction, "
            f"rail_name, status "
            f"FROM {_PREFIX}_transactions ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert {r[0] for r in rows} == {gen.transfer_id}
    assert {r[3] for r in rows} == {_TWO_LEG_RAIL}
    assert {r[4] for r in rows} == {"Posted"}
    # Σ amount = 0 (the L1 conservation shape).
    assert sum(float(r[1]) for r in rows) == pytest.approx(0.0)
    assert {r[2] for r in rows} == {"Debit", "Credit"}


def test_single_leg_debit_emit_writes_one_negative_leg() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _SINGLE_LEG_DEBIT_RAIL, anchor_day=date(2030, 1, 1), amount=100.0,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT amount_money, amount_direction, rail_name "
            f"FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(-100.0, "Debit", _SINGLE_LEG_DEBIT_RAIL)]


def test_single_leg_credit_emit_writes_one_positive_leg() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _SINGLE_LEG_CREDIT_RAIL, anchor_day=date(2030, 1, 1), amount=75.0,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT amount_money, amount_direction, rail_name "
            f"FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(75.0, "Credit", _SINGLE_LEG_CREDIT_RAIL)]


# ---------------------------------------------------------------------------
# AV.5 metadata tagging.
# ---------------------------------------------------------------------------


def test_untagged_emit_writes_null_metadata() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _TWO_LEG_RAIL, anchor_day=date(2030, 1, 1),
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions",
        ).fetchall()
    finally:
        conn.close()
    assert all(r[0] is None for r in rows)


def test_tagged_emit_tags_every_leg() -> None:
    gen = RailFiringFactory().scenario_for_rail(
        _TWO_LEG_RAIL, anchor_day=date(2030, 1, 1),
    )
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ay2b-rail-firing")
        conn.commit()
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE json_extract(metadata, '$.scenario_id') = ?",
            ("test-ay2b-rail-firing",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == 2
