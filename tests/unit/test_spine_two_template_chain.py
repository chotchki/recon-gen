"""AY.2.b — unit tests for TwoTemplateChainGenerator.

Seed-color coverage generator: emits one parent leg + N child template
legs all sharing one child Transfer + agreeing on
`transfer_parent_id`. The chain_parent_disagreement matview reads
`COUNT(DISTINCT transfer_parent_id) > 1` GROUP BY (transfer_id,
template_name) — this plant produces COUNT=1, so the matview branch
never fires (NO violation row). `intended` returns a
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
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    ChainParentDisagreementInvariant,
    ClaimedAccountsGenerator,
    CoverageObservation,
    TwoTemplateChainFactory,
    TwoTemplateChainGenerator,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"


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


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur,
        refresh_matviews_sql(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Factory — picker resolution + smart constructor.
# ---------------------------------------------------------------------------


def test_scenario_for_healthy_returns_a_generator_against_spec_example() -> None:
    """The AB.2.6 picker against `spec_example.yaml` must find a
    chain whose singleton child resolves to a TransferTemplate — if
    not, the spec_example fixture has shifted and the factory rejects
    loudly."""
    factory = TwoTemplateChainFactory()
    gen = factory.scenario_for_healthy(anchor_day=date(2030, 1, 1))
    assert isinstance(gen, TwoTemplateChainGenerator)
    # Picker selected a real chain — the names match real L2 entities.
    assert gen.chain_parent_name
    assert gen.child_template_name
    assert len(gen.child_leg_rails) >= 1, (
        "child template must have ≥1 leg_rail (picker should reject "
        "empty-leg-rail templates upstream)"
    )


def test_scenario_for_healthy_uses_picker_deterministically() -> None:
    """Two factory invocations on the same L2 → same generator
    arguments (deterministic picker → byte-stable seed contract)."""
    factory = TwoTemplateChainFactory()
    a = factory.scenario_for_healthy(anchor_day=date(2030, 1, 1))
    b = factory.scenario_for_healthy(anchor_day=date(2030, 1, 1))
    assert a == b


# ---------------------------------------------------------------------------
# intended subtype + identity.
# ---------------------------------------------------------------------------


def test_intended_is_a_coverage_observation() -> None:
    """The AY.2.a evidence-currency contract: this generator's
    intended must be a `CoverageObservation` (NOT `RuleViolation`,
    NOT `None`, NOT `AuditFixture`). The seed claims "I planted a
    healthy 2-template chain firing"; the coverage layer round-trips
    it."""
    gen = TwoTemplateChainFactory().scenario_for_healthy(
        anchor_day=date(2030, 1, 1),
    )
    intended = gen.intended
    assert isinstance(intended, CoverageObservation), (
        f"TwoTemplateChainGenerator's intended should be a "
        f"CoverageObservation; got {type(intended).__name__}"
    )
    items = dict(intended.identity)
    assert intended.invariant == "two_template_chain_healthy"
    assert items["child_transfer_id"] == gen.child_transfer_id
    assert items["chain_parent_name"] == gen.chain_parent_name
    assert items["child_template_name"] == gen.child_template_name
    assert items["child_leg_count"] == len(gen.child_leg_rails)


def test_generator_satisfies_claimed_accounts_protocol() -> None:
    """AV.5 contract: the generator's `claimed_accounts` must be a
    non-empty frozenset so ScenarioContext can detect cross-generator
    account collisions."""
    gen = TwoTemplateChainFactory().scenario_for_healthy(
        anchor_day=date(2030, 1, 1),
    )
    assert isinstance(gen, ClaimedAccountsGenerator)
    assert gen.claimed_accounts == frozenset({gen.account_id})


# ---------------------------------------------------------------------------
# Emit shape — non-violating chain_parent_disagreement.
# ---------------------------------------------------------------------------


def test_emit_writes_parent_plus_n_child_legs_sharing_transfer_id() -> None:
    """The healthy-chain emit contract: ONE parent leg + N child legs
    (N = len(child_leg_rails)). All child legs share one
    child_transfer_id and all carry the SAME parent_transfer_id."""
    gen = TwoTemplateChainFactory().scenario_for_healthy(
        anchor_day=date(2030, 1, 1),
    )
    expected_child_leg_count = len(gen.child_leg_rails)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        all_rows = conn.execute(
            f"SELECT transfer_id, transfer_parent_id, template_name, "
            f"amount_direction, status "
            f"FROM {_PREFIX}_transactions ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    assert len(all_rows) == 1 + expected_child_leg_count
    parent_rows = [r for r in all_rows if r[0] == gen.parent_transfer_id]
    child_rows = [r for r in all_rows if r[0] == gen.child_transfer_id]
    assert len(parent_rows) == 1
    assert len(child_rows) == expected_child_leg_count
    # All child legs carry the same parent_transfer_id.
    assert {r[1] for r in child_rows} == {gen.parent_transfer_id}
    # All child legs stamp the child template name.
    assert {r[2] for r in child_rows} == {gen.child_template_name}
    # All rows are Posted; child legs Credit, parent Debit.
    assert all(r[4] == "Posted" for r in all_rows)
    assert parent_rows[0][3] == "Debit"
    assert all(r[3] == "Credit" for r in child_rows)


def test_emit_does_not_trip_chain_parent_disagreement_matview() -> None:
    """The non-violating contract: a healthy 2-template chain firing
    has COUNT(DISTINCT transfer_parent_id) = 1 per (transfer_id,
    template_name), so the matview's HAVING > 1 branch never fires.

    This is the AP.2 'positive-control' shape — proves the matview
    correctly distinguishes healthy from violating chains."""
    gen = TwoTemplateChainFactory().scenario_for_healthy(
        anchor_day=date(2030, 1, 1),
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        violations = ChainParentDisagreementInvariant(
            prefix=_PREFIX,
        ).detect(conn)
    finally:
        conn.close()
    # No violation row for this child transfer — chain agreement holds.
    assert all(
        dict(v.identity).get("transfer_id") != gen.child_transfer_id
        for v in violations
    ), (
        f"healthy 2-template chain plant should NOT trip "
        f"chain_parent_disagreement for transfer_id={gen.child_transfer_id}; "
        f"got violations: {violations}"
    )


# ---------------------------------------------------------------------------
# AV.5 metadata tagging.
# ---------------------------------------------------------------------------


def test_untagged_emit_writes_null_metadata() -> None:
    gen = TwoTemplateChainFactory().scenario_for_healthy(
        anchor_day=date(2030, 1, 1),
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


def test_tagged_emit_tags_every_row() -> None:
    """AV.5 contract: every row this generator writes carries
    metadata.scenario_id when emitted under a ScenarioContext."""
    gen = TwoTemplateChainFactory().scenario_for_healthy(
        anchor_day=date(2030, 1, 1),
    )
    expected_row_count = 1 + len(gen.child_leg_rails)
    conn = _fresh_db()
    try:
        gen.emit(conn, scenario_id="test-ay2b-two-template-chain")
        conn.commit()
        tagged = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE json_extract(metadata, '$.scenario_id') = ?",
            ("test-ay2b-two-template-chain",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert tagged == expected_row_count
