"""AZ.1 — unit tests for `lock_to_json` (semantic lock serialization).

Pins the canonical JSON shape AZ.0 designed. The test gate at
AZ.2's `test_semantic_locks.py` relies on byte-stable
serialization: two runs of the same scenario through `lock_to_json`
must produce byte-identical output.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from recon_gen.common.spine import (
    AuditFixture,
    CoverageObservation,
    RuleViolation,
    lock_to_json,
)
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.sql import Dialect


def _v_rule(name: str, **identity: object) -> RuleViolation:
    return RuleViolation.of(name, **identity)


def _v_coverage(name: str, **identity: object) -> CoverageObservation:
    return CoverageObservation.of(name, **identity)


def _v_audit(name: str, **identity: object) -> AuditFixture:
    return AuditFixture.of(name, **identity)


# ---------------------------------------------------------------------------
# Top-level shape — scenario_fingerprint + violations + _comment.
# ---------------------------------------------------------------------------


def test_empty_lock_renders_top_level_shape() -> None:
    out = lock_to_json(
        {},
        instance=DEFAULT_PREFIX,
        dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    parsed = json.loads(out)
    assert set(parsed.keys()) == {"_comment", "scenario_fingerprint", "violations"}
    assert parsed["scenario_fingerprint"] == {
        "instance": DEFAULT_PREFIX,
        "dialect": "sqlite",
        "canonical_anchor": "2030-01-01",
        "schema_version": 1,
    }
    assert parsed["violations"] == {}
    assert "recon-gen data semantic-lock" in parsed["_comment"]


def test_terminating_newline_present() -> None:
    """File-on-disk convention: ends with `\\n`. Critical for
    text-mode editors not to add a sneaky trailing newline that
    would break byte-equality."""
    out = lock_to_json(
        {},
        instance="spec_example",
        dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    assert out.endswith("\n")


# ---------------------------------------------------------------------------
# Violation subtype → kind mapping.
# ---------------------------------------------------------------------------


def test_rule_violation_renders_as_rule_violation_kind() -> None:
    out = lock_to_json(
        {"drift": frozenset({_v_rule("drift", account_id="a", drift=5.0)})},
        instance="i", dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    parsed = json.loads(out)
    assert parsed["violations"]["drift"][0]["kind"] == "rule_violation"


def test_coverage_observation_renders_as_coverage_kind() -> None:
    out = lock_to_json(
        {"two_template_chain_healthy": frozenset({
            _v_coverage("two_template_chain_healthy", child_template_name="T"),
        })},
        instance="i", dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    parsed = json.loads(out)
    entries = parsed["violations"]["two_template_chain_healthy"]
    assert entries[0]["kind"] == "coverage"


def test_audit_fixture_renders_as_audit_fixture_kind() -> None:
    out = lock_to_json(
        {"supersession": frozenset({_v_audit("supersession", account_id="a")})},
        instance="i", dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    parsed = json.loads(out)
    assert parsed["violations"]["supersession"][0]["kind"] == "audit_fixture"


# ---------------------------------------------------------------------------
# Identity value type handling — JSON natives + date.
# ---------------------------------------------------------------------------


def test_date_identity_value_renders_as_iso_string() -> None:
    out = lock_to_json(
        {"drift": frozenset({_v_rule(
            "drift",
            account_id="acct-x",
            business_day=date(2030, 1, 2),
            drift=5.0,
        )})},
        instance="i", dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    parsed = json.loads(out)
    identity = parsed["violations"]["drift"][0]["identity"]
    assert identity["business_day"] == "2030-01-02"
    assert identity["account_id"] == "acct-x"
    assert identity["drift"] == 5.0


# ---------------------------------------------------------------------------
# Determinism — re-runs are byte-stable; sort order locked.
# ---------------------------------------------------------------------------


def test_two_runs_of_same_scenario_produce_byte_identical_output() -> None:
    """The core AZ contract: re-emit yields the same string. Re-runs
    of `semantic_lock(conn)` may produce frozensets with different
    iteration orders; `lock_to_json` must sort + render the same."""
    lock_a = {
        "drift": frozenset({
            _v_rule("drift", account_id="z", drift=3.0),
            _v_rule("drift", account_id="a", drift=5.0),
            _v_rule("drift", account_id="m", drift=4.0),
        }),
    }
    lock_b = {
        "drift": frozenset({
            _v_rule("drift", account_id="a", drift=5.0),
            _v_rule("drift", account_id="m", drift=4.0),
            _v_rule("drift", account_id="z", drift=3.0),
        }),
    }
    out_a = lock_to_json(
        lock_a, instance="i", dialect=Dialect.SQLITE,  # pyright: ignore[reportArgumentType]: RuleViolation is the test alias for Violation; same runtime shape
        canonical_anchor=date(2030, 1, 1),
    )
    out_b = lock_to_json(
        lock_b, instance="i", dialect=Dialect.SQLITE,  # pyright: ignore[reportArgumentType]: RuleViolation is the test alias for Violation; same runtime shape
        canonical_anchor=date(2030, 1, 1),
    )
    assert out_a == out_b


def test_invariant_names_sorted_alphabetically_in_output() -> None:
    out = lock_to_json(
        {
            "overdraft": frozenset(),
            "drift": frozenset(),
            "ledger_drift": frozenset(),
        },
        instance="i", dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    # Find the positions of the three keys in the output.
    drift_idx = out.index('"drift"')
    ledger_idx = out.index('"ledger_drift"')
    overdraft_idx = out.index('"overdraft"')
    assert drift_idx < ledger_idx < overdraft_idx


def test_identity_keys_sorted_alphabetically_in_output() -> None:
    out = lock_to_json(
        {"drift": frozenset({_v_rule(
            "drift",
            drift=5.0,
            account_id="acct-x",
            business_day=date(2030, 1, 2),
        )})},
        instance="i", dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    # Identity keys in the rendered output: account_id < business_day < drift.
    identity_str = out.split('"identity":')[1]
    acct_idx = identity_str.index('"account_id"')
    bd_idx = identity_str.index('"business_day"')
    drift_idx = identity_str.index('"drift"')
    assert acct_idx < bd_idx < drift_idx


def test_empty_invariants_kept_as_empty_arrays() -> None:
    """Per AZ.0 — empty invariants land as `[]` (not omitted) so
    diffs surface 'X used to fire, doesn't now' clearly."""
    out = lock_to_json(
        {"drift": frozenset(), "overdraft": frozenset()},
        instance="i", dialect=Dialect.SQLITE,
        canonical_anchor=date(2030, 1, 1),
    )
    parsed = json.loads(out)
    assert parsed["violations"]["drift"] == []
    assert parsed["violations"]["overdraft"] == []


# ---------------------------------------------------------------------------
# Dialect threading — SQLite / Postgres / Oracle round-trip.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", [
    Dialect.SQLITE, Dialect.POSTGRES, Dialect.ORACLE,
])
def test_dialect_value_round_trips_through_fingerprint(dialect: Dialect) -> None:
    out = lock_to_json(
        {}, instance="i", dialect=dialect,
        canonical_anchor=date(2030, 1, 1),
    )
    parsed = json.loads(out)
    assert parsed["scenario_fingerprint"]["dialect"] == dialect.value
