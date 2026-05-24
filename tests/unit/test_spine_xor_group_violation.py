"""Unit tests for AX.2's xor_group_violation spine promotion.

Mirrors the AX.1 chain_parent_disagreement test shape — same
matview-round-trip + smart-constructor + claimed_accounts +
untagged-emit-byte-stability checks. Doubled because the invariant
ships TWO generators (missed-firing + overlap variants).

Cross-class noise note: the spec_example yaml's
SettlementTimingCycle template is BOTH an XOR-grouped template AND
a chain parent. A missed/overlap XOR plant on this template will
also trip multi_xor_violation (because the chain has expected XOR
children that didn't fire). The tests assert `intended ⊆ detected`
not equality — extras from cross-class composition are fine per
AS.5's contract (`semantic_lock` follows the same shape).
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
    ClaimedAccountsGenerator,
    DriftInvariant,
    LedgerDriftInvariant,
    Violation,
    XorGroupMissedFiringGenerator,
    XorGroupOverlapGenerator,
    XorGroupViolationInvariant,
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
    assert XorGroupViolationInvariant().name == "xor_group_violation"


def test_detect_returns_empty_set_on_empty_db() -> None:
    inv = XorGroupViolationInvariant()
    conn = _fresh_db()
    try:
        _refresh(conn)
        assert inv.detect(conn) == set()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# scenario_for_missed — picks an XOR template with a non-XOR witness leg.
# ---------------------------------------------------------------------------


def test_scenario_for_missed_picks_a_template() -> None:
    gen = XorGroupViolationInvariant().scenario_for_missed()
    assert isinstance(gen, XorGroupMissedFiringGenerator)
    assert gen.template_name
    assert gen.witness_rail_name


def test_scenario_for_missed_raises_on_empty_l2() -> None:
    from recon_gen.common.l2.primitives import L2Instance
    empty = L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
    )
    with pytest.raises(ValueError, match="cannot manufacture"):
        XorGroupViolationInvariant().scenario_for_missed(instance=empty)


# ---------------------------------------------------------------------------
# scenario_for_overlap — picks any XOR group with ≥2 members.
# ---------------------------------------------------------------------------


def test_scenario_for_overlap_picks_a_template() -> None:
    gen = XorGroupViolationInvariant().scenario_for_overlap()
    assert isinstance(gen, XorGroupOverlapGenerator)
    assert gen.template_name
    assert gen.variant_a_rail_name != gen.variant_b_rail_name


def test_scenario_for_overlap_raises_on_empty_l2() -> None:
    from recon_gen.common.l2.primitives import L2Instance
    empty = L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
    )
    with pytest.raises(ValueError, match="cannot manufacture"):
        XorGroupViolationInvariant().scenario_for_overlap(instance=empty)


# ---------------------------------------------------------------------------
# Generators — claimed_accounts + intended + Protocol satisfaction.
# ---------------------------------------------------------------------------


def test_missed_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = XorGroupViolationInvariant().scenario_for_missed()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert len(gen.claimed_accounts) == 1


def test_overlap_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = XorGroupViolationInvariant().scenario_for_overlap()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert len(gen.claimed_accounts) == 1


def test_missed_intended_matches_natural_key() -> None:
    gen = XorGroupViolationInvariant().scenario_for_missed()
    intended = gen.intended
    items = dict(intended.identity)
    assert intended.invariant == "xor_group_violation"
    assert items["transfer_id"] == gen.transfer_id
    assert items["template_name"] == gen.template_name
    assert items["xor_group_index"] == gen.xor_group_index


def test_overlap_intended_matches_natural_key() -> None:
    gen = XorGroupViolationInvariant().scenario_for_overlap()
    intended = gen.intended
    items = dict(intended.identity)
    assert intended.invariant == "xor_group_violation"
    assert items["transfer_id"] == gen.transfer_id
    assert items["template_name"] == gen.template_name
    assert items["xor_group_index"] == gen.xor_group_index


# ---------------------------------------------------------------------------
# Emit + detect round-trip — the AS.0/AU.0 contract.
# ---------------------------------------------------------------------------


def test_missed_emit_then_detect_returns_intended() -> None:
    inv = XorGroupViolationInvariant()
    gen = inv.scenario_for_missed()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()
    assert gen.intended in detected, (
        f"missed-firing intended {gen.intended} missing from {detected}"
    )


def test_overlap_emit_then_detect_returns_intended() -> None:
    inv = XorGroupViolationInvariant()
    gen = inv.scenario_for_overlap()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()
    assert gen.intended in detected, (
        f"overlap intended {gen.intended} missing from {detected}"
    )


def test_missed_emit_fires_with_zero_member_rails() -> None:
    """The matview's firing_count=0 path: witness leg fires but no
    member of the target XOR group does. Confirms the emitted row's
    rail_name is outside the target group."""
    gen = XorGroupViolationInvariant().scenario_for_missed()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = conn.execute(
            f"SELECT rail_name, template_name FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ?",
            (gen.transfer_id,),
        ).fetchall()
        # Only 1 row, on the witness rail
        assert rows == [(gen.witness_rail_name, gen.template_name)]
        # Refresh + check firing_count = 0 on the matview row
        _refresh(conn)
        firing_count = conn.execute(
            f"SELECT firing_count FROM {_PREFIX}_xor_group_violation "
            f"WHERE transfer_id = ? AND xor_group_index = ?",
            (gen.transfer_id, gen.xor_group_index),
        ).fetchone()
    finally:
        conn.close()
    assert firing_count == (0,)


def test_overlap_emit_fires_two_member_rails() -> None:
    """The matview's firing_count=2 path: 2 distinct member-rail legs
    on one Transfer."""
    gen = XorGroupViolationInvariant().scenario_for_overlap()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rails = conn.execute(
            f"SELECT rail_name FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ? ORDER BY rail_name",
            (gen.transfer_id,),
        ).fetchall()
        _refresh(conn)
        firing_count = conn.execute(
            f"SELECT firing_count FROM {_PREFIX}_xor_group_violation "
            f"WHERE transfer_id = ? AND xor_group_index = ?",
            (gen.transfer_id, gen.xor_group_index),
        ).fetchone()
    finally:
        conn.close()
    # 2 distinct member rails fired
    assert sorted({r[0] for r in rails}) == sorted(
        {gen.variant_a_rail_name, gen.variant_b_rail_name},
    )
    assert firing_count == (2,)


# ---------------------------------------------------------------------------
# Single-edge property — no drift trip from transfers-only emit.
# ---------------------------------------------------------------------------


def test_missed_emit_does_not_trip_drift() -> None:
    gen = XorGroupViolationInvariant().scenario_for_missed()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        assert DriftInvariant().detect(conn) == set()
        assert LedgerDriftInvariant().detect(conn) == set()
    finally:
        conn.close()


def test_overlap_emit_does_not_trip_drift() -> None:
    gen = XorGroupViolationInvariant().scenario_for_overlap()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        assert DriftInvariant().detect(conn) == set()
        assert LedgerDriftInvariant().detect(conn) == set()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AV.5 metadata tagging — both generators.
# ---------------------------------------------------------------------------


def test_missed_untagged_emit_writes_null_metadata() -> None:
    gen = XorGroupViolationInvariant().scenario_for_missed()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        values = conn.execute(
            f"SELECT DISTINCT metadata FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ?",
            (gen.transfer_id,),
        ).fetchall()
    finally:
        conn.close()
    assert values == [(None,)]


def test_overlap_tagged_emit_writes_scenario_id() -> None:
    gen = XorGroupViolationInvariant().scenario_for_overlap()
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ax2-overlap")
        conn.commit()
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ? "
            f"AND json_extract(metadata, '$.scenario_id') = ?",
            (gen.transfer_id, "test-ax2-overlap"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == 2  # both legs tagged


# ---------------------------------------------------------------------------
# AY.4.c.2 — account_id_override threads through claimed_accounts.
# ---------------------------------------------------------------------------


def test_xor_missed_account_id_override_used_when_set() -> None:
    """Setting ``account_id_override`` short-circuits the
    derived-from-template_name default for the missed-firing
    generator — the AY.4.c.3 adapter pins per-plant account ids."""
    gen = XorGroupMissedFiringGenerator(
        template_name="tmpl",
        xor_group_index=0,
        witness_rail_name="rail-w",
        anchor_day=date(2030, 1, 1),
        account_id_override="custom-account-x",
    )
    assert gen.account_id == "custom-account-x"
    assert gen.claimed_accounts == frozenset({"custom-account-x"})


def test_xor_missed_default_derivation_preserved_when_unset() -> None:
    gen_a = XorGroupMissedFiringGenerator(
        template_name="tmpl",
        xor_group_index=0,
        witness_rail_name="rail-w",
        anchor_day=date(2030, 1, 1),
    )
    gen_b = XorGroupMissedFiringGenerator(
        template_name="tmpl",
        xor_group_index=0,
        witness_rail_name="rail-w",
        anchor_day=date(2030, 1, 1),
    )
    assert gen_a.account_id == gen_b.account_id
    assert gen_a.account_id == "acct-xor-missed-tmpl"


def test_xor_overlap_account_id_override_used_when_set() -> None:
    """Setting ``account_id_override`` short-circuits the
    derived-from-template_name default for the overlap generator."""
    gen = XorGroupOverlapGenerator(
        template_name="tmpl",
        xor_group_index=0,
        variant_a_rail_name="rail-a",
        variant_b_rail_name="rail-b",
        anchor_day=date(2030, 1, 1),
        account_id_override="custom-account-y",
    )
    assert gen.account_id == "custom-account-y"
    assert gen.claimed_accounts == frozenset({"custom-account-y"})


def test_xor_overlap_default_derivation_preserved_when_unset() -> None:
    gen_a = XorGroupOverlapGenerator(
        template_name="tmpl",
        xor_group_index=0,
        variant_a_rail_name="rail-a",
        variant_b_rail_name="rail-b",
        anchor_day=date(2030, 1, 1),
    )
    gen_b = XorGroupOverlapGenerator(
        template_name="tmpl",
        xor_group_index=0,
        variant_a_rail_name="rail-a",
        variant_b_rail_name="rail-b",
        anchor_day=date(2030, 1, 1),
    )
    assert gen_a.account_id == gen_b.account_id
    assert gen_a.account_id == "acct-xor-overlap-tmpl"
