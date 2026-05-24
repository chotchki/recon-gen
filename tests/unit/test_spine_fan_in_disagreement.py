"""Unit tests for AX.3's fan_in_disagreement spine promotion.

The invariant has 3 smart constructors (healthy / missing_parent /
extra_parent) on a single generator class. Identity tuple:
`(child_transfer_id, disagreement_kind)` where disagreement_kind ∈
{'orphan', 'missing', 'extra'}; the 'healthy' shape produces NO
matview row (the AP.2 non-violating convention).

The spec_example yaml's fan_in chain (BatchPayoutTrigger →
BatchedPayoutBatch) declares `expected_parent_count=2`, so:
  - healthy → parent_count=2 → no matview row
  - missing → parent_count=1 → matview row, kind='missing'
  - extra   → parent_count=3 → matview row, kind='extra'

(The 'orphan' kind only fires when the L2 chain has no expected
count set — variable-batch case — and parent_count < 2. Spec_example
sets expected so 'missing' is what fires for parent_count=1; orphan
is covered by the missing_parent smart-constructor when the L2's
expected is None.)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    ClaimedAccountsGenerator,
    DriftInvariant,
    FanInChainGenerator,
    FanInDisagreementInvariant,
    LedgerDriftInvariant,
    Violation,
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
    assert FanInDisagreementInvariant().name == "fan_in_disagreement"


def test_detect_returns_empty_set_on_empty_db() -> None:
    inv = FanInDisagreementInvariant()
    conn = _fresh_db()
    try:
        _refresh(conn)
        assert inv.detect(conn) == set()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Smart constructors — each picks the same L2 chain, sets parent_count
# per its variant + records the expected disagreement_kind.
# ---------------------------------------------------------------------------


def test_scenario_for_healthy_sets_parent_count_to_expected() -> None:
    """The healthy generator matches expected_parent_count exactly →
    no matview row. expected_kind tracks 'healthy' so intended is None."""
    gen = FanInDisagreementInvariant().scenario_for_healthy()
    assert isinstance(gen, FanInChainGenerator)
    assert gen.expected_kind == "healthy"
    if gen.expected_parent_count is not None:
        assert gen.parent_count == gen.expected_parent_count
    else:
        assert gen.parent_count == 2  # variable-batch default


def test_scenario_for_missing_sets_parent_count_below_expected() -> None:
    gen = FanInDisagreementInvariant().scenario_for_missing_parent()
    if gen.expected_parent_count is not None:
        assert gen.parent_count == gen.expected_parent_count - 1
        assert gen.expected_kind == "missing"
    else:
        assert gen.parent_count == 1
        assert gen.expected_kind == "orphan"


def test_scenario_for_extra_sets_parent_count_above_expected() -> None:
    gen = FanInDisagreementInvariant().scenario_for_extra_parent()
    assert gen.expected_parent_count is not None
    assert gen.parent_count == gen.expected_parent_count + 1
    assert gen.expected_kind == "extra"


def test_scenario_for_raises_on_empty_l2() -> None:
    from recon_gen.common.l2.primitives import L2Instance
    empty = L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
    )
    with pytest.raises(ValueError, match="cannot manufacture"):
        FanInDisagreementInvariant().scenario_for_healthy(instance=empty)


# ---------------------------------------------------------------------------
# Generator — claimed_accounts + intended + Protocol satisfaction.
# ---------------------------------------------------------------------------


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    gen = FanInDisagreementInvariant().scenario_for_missing_parent()
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert len(gen.claimed_accounts) == 1


def test_healthy_intended_is_none() -> None:
    """Per AP.2 non-violating convention, the healthy emit produces
    no matview row → intended is None."""
    gen = FanInDisagreementInvariant().scenario_for_healthy()
    assert gen.intended is None


def test_missing_intended_matches_natural_key() -> None:
    gen = FanInDisagreementInvariant().scenario_for_missing_parent()
    intended = gen.intended
    assert intended is not None
    items = dict(intended.identity)
    assert intended.invariant == "fan_in_disagreement"
    assert items["child_transfer_id"] == gen.child_transfer_id
    assert items["disagreement_kind"] in {"missing", "orphan"}


def test_extra_intended_matches_natural_key() -> None:
    gen = FanInDisagreementInvariant().scenario_for_extra_parent()
    intended = gen.intended
    assert intended is not None
    items = dict(intended.identity)
    assert intended.invariant == "fan_in_disagreement"
    assert items["child_transfer_id"] == gen.child_transfer_id
    assert items["disagreement_kind"] == "extra"


# ---------------------------------------------------------------------------
# Emit + detect round-trip — the AS.0/AU.0 contract.
# ---------------------------------------------------------------------------


def test_healthy_emit_produces_no_matview_row() -> None:
    """The healthy plant emits parent_count == expected → matview's
    CASE expression sees no mismatch → no disagreement row. The AP.2
    non-violating positive control."""
    inv = FanInDisagreementInvariant()
    gen = inv.scenario_for_healthy()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()
    # No detection for the healthy plant.
    plant_keys = {
        (str(dict(v.identity)["child_transfer_id"]), str(dict(v.identity)["disagreement_kind"]))
        for v in detected
    }
    assert (gen.child_transfer_id, "missing") not in plant_keys
    assert (gen.child_transfer_id, "extra") not in plant_keys
    assert (gen.child_transfer_id, "orphan") not in plant_keys


def test_missing_emit_then_detect_returns_intended() -> None:
    inv = FanInDisagreementInvariant()
    gen = inv.scenario_for_missing_parent()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()
    assert gen.intended in detected, (
        f"missing-parent intended {gen.intended} missing from {detected}"
    )


def test_extra_emit_then_detect_returns_intended() -> None:
    inv = FanInDisagreementInvariant()
    gen = inv.scenario_for_extra_parent()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()
    assert gen.intended in detected, (
        f"extra-parent intended {gen.intended} missing from {detected}"
    )


def test_missing_emit_writes_one_less_parent_than_expected() -> None:
    """Confirm the row shape: parent_count = expected - 1 in the
    actual emit; matview reads that count."""
    gen = FanInDisagreementInvariant().scenario_for_missing_parent()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        # Count distinct parent_transfer_ids on the child Transfer.
        n_parents = conn.execute(
            f"SELECT COUNT(DISTINCT transfer_parent_id) "
            f"FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ? AND transfer_parent_id IS NOT NULL",
            (gen.child_transfer_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_parents == gen.parent_count
    if gen.expected_parent_count is not None:
        assert n_parents == gen.expected_parent_count - 1


def test_extra_emit_writes_one_more_parent_than_expected() -> None:
    gen = FanInDisagreementInvariant().scenario_for_extra_parent()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        n_parents = conn.execute(
            f"SELECT COUNT(DISTINCT transfer_parent_id) "
            f"FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ? AND transfer_parent_id IS NOT NULL",
            (gen.child_transfer_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert gen.expected_parent_count is not None
    assert n_parents == gen.expected_parent_count + 1


# ---------------------------------------------------------------------------
# Single-edge property — no drift trip from transfers-only emit.
# ---------------------------------------------------------------------------


def test_missing_emit_does_not_trip_drift() -> None:
    gen = FanInDisagreementInvariant().scenario_for_missing_parent()
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
# AV.5 metadata tagging.
# ---------------------------------------------------------------------------


def test_untagged_emit_writes_null_metadata() -> None:
    gen = FanInDisagreementInvariant().scenario_for_missing_parent()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        values = conn.execute(
            f"SELECT DISTINCT metadata FROM {_PREFIX}_transactions "
            f"WHERE transfer_id = ? OR account_id = ?",
            (gen.child_transfer_id, gen.account_id),
        ).fetchall()
    finally:
        conn.close()
    assert values == [(None,)]


def test_tagged_emit_writes_scenario_id_on_every_row() -> None:
    gen = FanInDisagreementInvariant().scenario_for_extra_parent()
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ax3-extra")
        conn.commit()
        # Every parent + child leg should carry the tag.
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE account_id = ? "
            f"AND json_extract(metadata, '$.scenario_id') = ?",
            (gen.account_id, "test-ax3-extra"),
        ).fetchone()[0]
        total = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE account_id = ?",
            (gen.account_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == total > 0
