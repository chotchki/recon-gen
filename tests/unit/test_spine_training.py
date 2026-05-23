"""Unit tests for the AS.7 self-validating training scenario.

`TrainingScenario.self_validate(conn)` asserts every claimed Violation
fires after the scenario emits. A scenario whose docs claim a drift
but whose emitter doesn't actually plant one FAILS the test loud —
docs can't silently lie.

Four properties pinned:

1. A truthful scenario passes — `intended` ⊆ `detected`.
2. A lying scenario fails — `intended` includes Violations the
   emitter doesn't produce; the missing diff is in the error.
3. Extra detected Violations (e.g., the ledger_drift secondary edge
   that drift's emission also trips) are FINE — the contract is
   subset, not equality.
4. `validate_all` batches a list of scenarios, each against a fresh
   DB (no bleed across scenarios).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    DriftInvariant,
    LedgerDriftInvariant,
    TrainingScenario,
    Violation,
    validate_all,
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
    return conn


# ---------------------------------------------------------------------------
# Truthful scenario — passes.
# ---------------------------------------------------------------------------


def test_truthful_scenario_self_validates() -> None:
    # The canonical L1 drift demo: drift the CustomerSubledger child
    # by 5; the docs prose says "drift fires on the child for 5.00";
    # the assertion holds because the generator actually plants it.
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    scenario = TrainingScenario(
        name="L1 drift demo",
        description=(
            "Drift the child account by $5; the L1 dashboard's drift "
            "row shows account_id=acct-drift-child-CustomerSubledger, "
            "day=2030-01-01, drift=$5."
        ),
        emitters=(gen,),
        invariants=(DriftInvariant(), LedgerDriftInvariant()),
        intended=frozenset({gen.intended}),
    )
    conn = _fresh_db()
    try:
        scenario.self_validate(conn)  # raises AssertionError on failure
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Lying scenario — fails. The whole point of AS.7.
# ---------------------------------------------------------------------------


def test_lying_scenario_fails_loud() -> None:
    # Generator with magnitude=0 (no actual drift); docs CLAIM drift=5
    # fires. The mismatch IS the lie. self_validate must raise.
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=0.0)
    claimed = Violation.of(
        "drift",
        account_id="acct-drift-child-CustomerSubledger",
        business_day=date(2030, 1, 1),
        drift=5.0,
    )
    scenario = TrainingScenario(
        name="lying drift demo",
        description="Docs claim drift=$5 — but the emitter plants 0.",
        emitters=(gen,),
        invariants=(DriftInvariant(),),
        intended=frozenset({claimed}),
    )
    conn = _fresh_db()
    try:
        with pytest.raises(AssertionError, match="don't fire"):
            scenario.self_validate(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Extra detected Violations are FINE — the contract is subset, not equality.
# ---------------------------------------------------------------------------


def test_extra_violations_dont_break_self_validation() -> None:
    # Drift generator emits BOTH a drift on the child AND a
    # secondary ledger_drift on the parent (the AS.2 many-to-many
    # edge). If the docs only claim the PRIMARY drift, the extra
    # ledger_drift is OK — subset contract.
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    scenario = TrainingScenario(
        name="primary-only drift demo",
        description="Docs only mention the primary drift; secondary ledger_drift OK.",
        emitters=(gen,),
        invariants=(DriftInvariant(), LedgerDriftInvariant()),
        intended=frozenset({gen.intended}),  # ONLY the primary
    )
    conn = _fresh_db()
    try:
        scenario.self_validate(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# validate_all — batch validation across multiple scenarios.
# ---------------------------------------------------------------------------


def test_validate_all_passes_a_list_of_truthful_scenarios() -> None:
    gen_a = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    gen_b = DriftInvariant().scenario_for("CustomerSubledger", magnitude=7.0)
    scenarios = [
        TrainingScenario(
            name="drift-5", description="...",
            emitters=(gen_a,),
            invariants=(DriftInvariant(),),
            intended=frozenset({gen_a.intended}),
        ),
        TrainingScenario(
            name="drift-7", description="...",
            emitters=(gen_b,),
            invariants=(DriftInvariant(),),
            intended=frozenset({gen_b.intended}),
        ),
    ]
    # Each scenario gets its OWN fresh DB — no bleed between them
    # (gen_b uses the same account_id as gen_a so a shared DB would
    # collide on PK; the per-scenario fresh_db prevents that).
    validate_all(scenarios, _fresh_db)


def test_validate_all_halts_on_the_first_lying_scenario() -> None:
    gen_true = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    gen_zero = DriftInvariant().scenario_for("CustomerSubledger", magnitude=0.0)
    scenarios = [
        TrainingScenario(
            name="truthful", description="...",
            emitters=(gen_true,),
            invariants=(DriftInvariant(),),
            intended=frozenset({gen_true.intended}),
        ),
        TrainingScenario(
            name="lying", description="...",
            emitters=(gen_zero,),
            invariants=(DriftInvariant(),),
            intended=frozenset({
                Violation.of(
                    "drift",
                    account_id="acct-drift-child-CustomerSubledger",
                    business_day=date(2030, 1, 1),
                    drift=99.0,
                ),
            }),
        ),
    ]
    with pytest.raises(AssertionError, match="lying"):
        validate_all(scenarios, _fresh_db)


# ---------------------------------------------------------------------------
# Empty-intended scenarios — sanity (no claims to validate ⇒ vacuously OK).
# ---------------------------------------------------------------------------


def test_empty_intended_scenario_passes_vacuously() -> None:
    scenario = TrainingScenario(
        name="empty",
        description="No claims — sanity case.",
        emitters=(),
        invariants=(),
        intended=frozenset(),
    )
    conn = _fresh_db()
    try:
        scenario.self_validate(conn)
    finally:
        conn.close()
