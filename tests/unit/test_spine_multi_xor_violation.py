"""Unit tests for AX.4's multi_xor_violation spine promotion.

Single invariant + 2 generators (missed: parent fires zero XOR-
sibling children; overlap: parent fires ≥2 XOR-siblings). Identity
tuple: `(parent_transfer_id, disagreement_kind)`.

Mirrors AX.2 xor_group_violation's test shape — same matview-round-
trip + smart-constructor + claimed_accounts + tag-byte-stability.

Cross-class noise note: the multi_xor matview reads ALL non-fan_in
children of multi-children chains; for overlap, planted child rows
may also contribute to xor_group_violation if their template
declares XOR groups + the children's rail picks happen to be in a
group. Tests assert intended ⊆ detected, not equality.
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
    MultiXorMissedGenerator,
    MultiXorOverlapGenerator,
    MultiXorViolationInvariant,
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
    # BS.5 (2026-05-29): populate config_kv so the multi_xor_violation
    # matview's view-based multi_xor_chains CTE (sourced from
    # <prefix>_v_config_chain_children) sees the L2's declared chains.
    # Pre-BS.5 the matview baked instance.chains as SQL literals.
    import json as _json
    from datetime import datetime as _datetime

    import yaml as _yaml

    from recon_gen.common.l2.config_table import replace_config
    from recon_gen.common.l2.serializer import serialize_l2
    l2_dict = _yaml.safe_load(serialize_l2(instance))
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}",
        l2_json=_json.dumps(l2_dict, separators=(",", ":")),
        as_of=_datetime(2030, 1, 1, 12, 0, 0),
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
# Invariant — name + detect contract.
# ---------------------------------------------------------------------------


def test_invariant_carries_the_matview_name() -> None:
    assert MultiXorViolationInvariant().name == "multi_xor_violation"


def test_detect_returns_empty_set_on_empty_db() -> None:
    inv = MultiXorViolationInvariant()
    conn = _fresh_db()
    try:
        _refresh(conn)
        assert inv.detect(conn) == set()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Smart constructors — pick a multi-XOR chain.
# ---------------------------------------------------------------------------


def test_scenario_for_missed_picks_a_chain() -> None:
    gen = MultiXorViolationInvariant().scenario_for_missed()
    assert isinstance(gen, MultiXorMissedGenerator)
    assert gen.chain_parent_name


def test_scenario_for_overlap_picks_a_chain() -> None:
    gen = MultiXorViolationInvariant().scenario_for_overlap()
    assert isinstance(gen, MultiXorOverlapGenerator)
    assert gen.chain_parent_name
    assert gen.variant_a_child_name != gen.variant_b_child_name


def test_scenario_for_raises_on_empty_l2() -> None:
    from recon_gen.common.l2.primitives import L2Instance
    empty = L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
    )
    with pytest.raises(ValueError, match="cannot manufacture"):
        MultiXorViolationInvariant().scenario_for_missed(instance=empty)
    with pytest.raises(ValueError, match="cannot manufacture"):
        MultiXorViolationInvariant().scenario_for_overlap(instance=empty)


# ---------------------------------------------------------------------------
# Generators — claimed_accounts + intended + Protocol satisfaction.
# ---------------------------------------------------------------------------


def test_missed_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = MultiXorViolationInvariant().scenario_for_missed()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert len(gen.claimed_accounts) == 1


def test_overlap_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = MultiXorViolationInvariant().scenario_for_overlap()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert len(gen.claimed_accounts) == 1


def test_missed_intended_matches_natural_key() -> None:
    gen = MultiXorViolationInvariant().scenario_for_missed()
    intended = gen.intended
    items = dict(intended.identity)
    assert intended.invariant == "multi_xor_violation"
    assert items["parent_transfer_id"] == gen.parent_transfer_id
    assert items["disagreement_kind"] == "missed"


def test_overlap_intended_matches_natural_key() -> None:
    gen = MultiXorViolationInvariant().scenario_for_overlap()
    intended = gen.intended
    items = dict(intended.identity)
    assert intended.invariant == "multi_xor_violation"
    assert items["parent_transfer_id"] == gen.parent_transfer_id
    assert items["disagreement_kind"] == "overlap"


# ---------------------------------------------------------------------------
# Emit + detect round-trip — the AS.0/AU.0 contract.
# ---------------------------------------------------------------------------


def test_missed_emit_then_detect_returns_intended() -> None:
    inv = MultiXorViolationInvariant()
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
        f"missed intended {gen.intended} missing from {detected}"
    )


def test_overlap_emit_then_detect_returns_intended() -> None:
    inv = MultiXorViolationInvariant()
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


def test_missed_emit_writes_only_parent() -> None:
    """The missed shape: exactly 1 row (the parent firing), no child
    firings tagged with the parent's transfer_parent_id."""
    gen = MultiXorViolationInvariant().scenario_for_missed()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        n_parent = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ?",
            (gen.parent_transfer_id,),
        ).fetchone()[0]
        n_children = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE transfer_parent_id = ?",
            (gen.parent_transfer_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_parent == 1
    assert n_children == 0  # the missed-firing point


def test_overlap_emit_writes_parent_plus_two_children() -> None:
    gen = MultiXorViolationInvariant().scenario_for_overlap()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        n_parent = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ?",
            (gen.parent_transfer_id,),
        ).fetchone()[0]
        n_children = conn.execute(
            f"SELECT COUNT(DISTINCT transfer_id) FROM {_PREFIX}_transactions "
            f"WHERE transfer_parent_id = ?",
            (gen.parent_transfer_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_parent == 1
    assert n_children == 2  # the overlap point


# ---------------------------------------------------------------------------
# Single-edge property — no drift trip from transfers-only emit.
# ---------------------------------------------------------------------------


def test_missed_emit_does_not_trip_drift() -> None:
    gen = MultiXorViolationInvariant().scenario_for_missed()
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
    gen = MultiXorViolationInvariant().scenario_for_overlap()
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
    gen = MultiXorViolationInvariant().scenario_for_missed()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        values = conn.execute(
            f"SELECT DISTINCT metadata FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ?",
            (gen.parent_transfer_id,),
        ).fetchall()
    finally:
        conn.close()
    assert values == [(None,)]


def test_overlap_tagged_emit_writes_scenario_id_on_every_row() -> None:
    gen = MultiXorViolationInvariant().scenario_for_overlap()
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ax4-overlap")
        conn.commit()
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE account_id = ? "
            f"AND json_extract(metadata, '$.scenario_id') = ?",
            (gen.account_id, "test-ax4-overlap"),
        ).fetchone()[0]
        total = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE account_id = ?",
            (gen.account_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == total > 0


# ---------------------------------------------------------------------------
# AY.4.c.2 — account_id_override threads through claimed_accounts.
# ---------------------------------------------------------------------------


def test_multi_xor_missed_account_id_override_used_when_set() -> None:
    """Setting ``account_id_override`` short-circuits the
    derived-from-chain_parent_name default for the missed generator."""
    gen = MultiXorMissedGenerator(
        chain_parent_name="parent",
        anchor_day=date(2030, 1, 1),
        account_id_override="custom-account-x",
    )
    assert gen.account_id == "custom-account-x"
    assert gen.claimed_accounts == frozenset({"custom-account-x"})


def test_multi_xor_missed_default_derivation_preserved_when_unset() -> None:
    gen_a = MultiXorMissedGenerator(
        chain_parent_name="parent",
        anchor_day=date(2030, 1, 1),
    )
    gen_b = MultiXorMissedGenerator(
        chain_parent_name="parent",
        anchor_day=date(2030, 1, 1),
    )
    assert gen_a.account_id == gen_b.account_id
    assert gen_a.account_id == "acct-mxor-missed-parent"


def test_multi_xor_overlap_account_id_override_used_when_set() -> None:
    """Setting ``account_id_override`` short-circuits the
    derived-from-chain_parent_name default for the overlap generator."""
    gen = MultiXorOverlapGenerator(
        chain_parent_name="parent",
        variant_a_child_name="child-a",
        variant_b_child_name="child-b",
        anchor_day=date(2030, 1, 1),
        account_id_override="custom-account-y",
    )
    assert gen.account_id == "custom-account-y"
    assert gen.claimed_accounts == frozenset({"custom-account-y"})


def test_multi_xor_overlap_default_derivation_preserved_when_unset() -> None:
    gen_a = MultiXorOverlapGenerator(
        chain_parent_name="parent",
        variant_a_child_name="child-a",
        variant_b_child_name="child-b",
        anchor_day=date(2030, 1, 1),
    )
    gen_b = MultiXorOverlapGenerator(
        chain_parent_name="parent",
        variant_a_child_name="child-a",
        variant_b_child_name="child-b",
        anchor_day=date(2030, 1, 1),
    )
    assert gen_a.account_id == gen_b.account_id
    assert gen_a.account_id == "acct-mxor-overlap-parent"
