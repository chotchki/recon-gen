"""DS.3.8 — the generalized emitter-mutation harness.

Port of the DS.0 cardinality spike's working design
(``docs/audits/ds_0_spike_evidence/cardinality/spike.py`` phase 4):
named (mutation, regex/replacement) specs applied to the EMITTED SQL
text, the target matview re-created per mutant against an
already-loaded packed domain, baseline restored and re-verified after
every mutant. What the gate buys: the enumeration domains' adequacy
is MEASURED, not assumed — every semantic corruption of a detector's
emitted SQL must be visible to the packed domains, either as a
violation-set diff (the comparator channel) or as a refresh crash
(the structural channel — a UNIQUE-index violation is a LIVE
detection layer, the DS.0 §6.1 mutant-C lesson).

Mechanics:

- Statements come from the REAL ``refresh_matviews_sql`` output via
  the same comment-aware splitter ``execute_script`` uses (a naive
  ``.*?;`` regex truncates at semicolons inside SQL comments — the
  spike got away with it on comment-free targets; this port doesn't
  gamble).
- A re-create unit is the target's CREATE TABLE statement PLUS its
  CREATE INDEX statements, so index-enforced contracts stay live
  under mutation (dropping the supersession filter must crash the
  unique-id index rebuild).
- Mutating an UPSTREAM matview (``computed_subledger_balance``)
  re-creates the declared downstream detectors from their ORIGINAL
  text before reading, so the read reflects the mutated input the way
  a real refresh would.
- Restore re-creates target + downstream from original text; the
  caller re-verifies the detector read equals the baseline before the
  next mutant runs.

Proven-equivalent exclusion (cited, not silent): the spike's MX6 —
``parent_firings`` ``UNION`` → ``UNION ALL`` — SURVIVED and was
proven equivalent by inspection: both arms are internally DISTINCT
and the downstream ``fired_children_distinct`` SELECT DISTINCT
re-collapses any cross-arm duplicate, so no violation set can change
(DS.0 §6.2 retired the UNION-dedup encoding worry on exactly this
result). It is excluded from the battery WITH this citation rather
than left in as a permanent known-survivor.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Final

import duckdb

from recon_gen.common.db import _split_sqlite_statements
from tests.enumeration.domains import (
    chain_parent,
    drift,
    expected_eod,
    fan_in,
    limit_breach,
    multi_xor,
    overdraft,
    stuck_pending,
    stuck_unbundled,
    xor_group,
)
from tests.enumeration.harness import (
    DetectorCheck,
    EnumerationDB,
    InstanceArtifacts,
    ViolationMap,
)

CHECKS_BY_DETECTOR: Final[dict[str, DetectorCheck]] = {
    check.detector: check
    for check in (
        drift.CHECK, overdraft.CHECK, expected_eod.CHECK,
        limit_breach.CHECK, stuck_pending.CHECK, stuck_unbundled.CHECK,
        chain_parent.CHECK, xor_group.CHECK, fan_in.CHECK, multi_xor.CHECK,
    )
}


@dataclass(frozen=True, slots=True)
class MutationSpec:
    """One named mutant. ``pattern`` / ``replacement`` may carry a
    ``{p}`` placeholder for the instance prefix; the pattern is a
    regex applied to the target's CREATE TABLE text only (statement
    scoping — the same literal elsewhere in the script is
    untouched)."""

    mutation_id: str
    family: str
    domain: str
    detector: str
    target_table: str
    pattern: str
    replacement: str
    note: str
    downstream: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MutantOutcome:
    mutation_id: str
    family: str
    detector: str
    killed: bool
    channel: str
    divergent_keys: int
    witnesses: tuple[str, ...]
    elapsed_seconds: float
    note: str
    detail: str = ""


class MutationHarnessError(Exception):
    """A harness-integrity failure (pattern no-op, missing unit,
    baseline not restored) — never a mutant verdict."""


def _strip_leading_comments(stmt: str) -> str:
    """The splitter attaches the comment block BETWEEN two statements
    to the following statement's text; drop those lines so the CREATE
    matchers can anchor at the start."""
    lines = stmt.splitlines()
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            start = i
            break
    return "\n".join(lines[start:]).strip()


def statement_units(artifacts: InstanceArtifacts) -> dict[str, tuple[str, ...]]:
    """table name -> (CREATE TABLE stmt, its CREATE INDEX stmts...)
    from the emitted refresh script, comment-aware."""
    units: dict[str, list[str]] = {}
    for stmt in _split_sqlite_statements(artifacts.refresh_sql):
        body = _strip_leading_comments(stmt)
        create = re.match(r"CREATE TABLE (\S+)", body)
        if create:
            table = create.group(1)
            if table in units:
                raise MutationHarnessError(
                    f"duplicate CREATE TABLE for {table} in refresh script",
                )
            units[table] = [body]
            continue
        index = re.match(r"CREATE (?:UNIQUE )?INDEX \S+\s+ON\s+(\S+)\s*\(", body)
        if index:
            table = index.group(1)
            if table in units:
                units[table].append(body)
    return {table: tuple(stmts) for table, stmts in units.items()}


def apply_mutation(create_stmt: str, pattern: str, replacement: str) -> str:
    """Regex-substitute; a no-op pattern is a harness bug (the emitted
    text moved out from under the battery), never a pass."""
    mutated = re.sub(pattern, lambda _: replacement, create_stmt)
    if mutated == create_stmt:
        raise MutationHarnessError(
            f"mutation pattern matched nothing: {pattern!r}",
        )
    return mutated


def _recreate(db: EnumerationDB, table: str, stmts: tuple[str, ...]) -> None:
    db.execute_statements(
        f"recreate {table}",
        (f"DROP TABLE IF EXISTS {table}", *stmts),
    )


def _diff_and_witnesses(
    engine: ViolationMap, baseline: ViolationMap,
) -> tuple[int, tuple[str, ...]]:
    engine_only = engine.keys() - baseline.keys()
    baseline_only = baseline.keys() - engine.keys()
    value_diff = [
        k for k in engine.keys() & baseline.keys()
        if engine[k] != baseline[k]
    ]
    total = len(engine_only) + len(baseline_only) + len(value_diff)
    witnesses: list[str] = []
    for name, keys in (
        ("mutant-only", engine_only), ("baseline-only", baseline_only),
    ):
        for key in sorted(keys, key=repr)[:1]:
            side = engine if name == "mutant-only" else baseline
            witnesses.append(f"{name} {key!r}: {side[key]!r}")
    for key in sorted(value_diff, key=repr)[:1]:
        witnesses.append(
            f"value-diff {key!r}: mutant={engine[key]!r} "
            f"baseline={baseline[key]!r}",
        )
    return total, tuple(witnesses)


def run_mutant(
    db: EnumerationDB,
    units: dict[str, tuple[str, ...]],
    spec: MutationSpec,
    baseline: ViolationMap,
) -> MutantOutcome:
    """Apply one mutant, read its detector, restore the original text
    and re-verify the baseline read. Returns the outcome; raises
    MutationHarnessError only for harness-integrity failures."""
    prefix = db.prefix
    target = f"{prefix}_{spec.target_table}"
    unit = units.get(target)
    if unit is None:
        raise MutationHarnessError(f"no CREATE TABLE unit for {target}")
    pattern = spec.pattern.format(p=re.escape(prefix))
    replacement = spec.replacement.format(p=prefix)
    mutated = (apply_mutation(unit[0], pattern, replacement), *unit[1:])
    reader = CHECKS_BY_DETECTOR[spec.detector].read_engine
    t0 = time.perf_counter()
    structural: str | None = None
    engine: ViolationMap = {}
    try:
        _recreate(db, target, mutated)
        for suffix in spec.downstream:
            table = f"{prefix}_{suffix}"
            _recreate(db, table, units[table])
        engine = reader(db)
    except (duckdb.Error, AssertionError) as exc:
        # The structural channel: the mutant crashed an index rebuild
        # / the refresh itself, or tripped a reader-side consistency
        # guard. Loud, valid kill — record which.
        structural = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - t0
    # Restore + re-verify, whatever the verdict.
    _recreate(db, target, unit)
    for suffix in spec.downstream:
        table = f"{prefix}_{suffix}"
        _recreate(db, table, units[table])
    restored = reader(db)
    if restored != baseline:
        raise MutationHarnessError(
            f"{spec.mutation_id}: baseline NOT restored for "
            f"{spec.detector} after mutant — harness bug, run invalid",
        )
    if structural is not None:
        return MutantOutcome(
            mutation_id=spec.mutation_id, family=spec.family,
            detector=spec.detector, killed=True, channel="structural",
            divergent_keys=0, witnesses=(), elapsed_seconds=elapsed,
            note=spec.note, detail=structural[:200],
        )
    divergent, witnesses = _diff_and_witnesses(engine, baseline)
    return MutantOutcome(
        mutation_id=spec.mutation_id, family=spec.family,
        detector=spec.detector, killed=divergent > 0,
        channel="comparator" if divergent else "",
        divergent_keys=divergent, witnesses=witnesses,
        elapsed_seconds=elapsed, note=spec.note,
    )


# ---------------------------------------------------------------------------
# The battery. Families mirror the DS.0 kit; every spec names the
# domain that must kill it. Drawn from the spike's proven kills plus
# the per-family shapes the DS.3.8 brief lists: comparison-operator
# flips, day-boundary <= -> <, status-filter narrowing AND widening
# (both directions of the DS.3.3 law), sign/direction flips,
# supersession drops, HAVING flips, COUNT-shape changes,
# membership-EXISTS always-true, join-key swaps, qualifier-threshold
# bumps.


BATTERY: Final[tuple[MutationSpec, ...]] = (
    # -- cardinality: xor_group (spike X1-X4 ported + existence arm) --
    MutationSpec(
        "XG1", "cardinality", "transfer_keyed", "xor_group",
        "xor_group_violation",
        r"HAVING COUNT\(tx\.transfer_id\) <> 1",
        "HAVING COUNT(tx.transfer_id) > 1",
        "HAVING flip: missed-firing (count 0) cells vanish",
    ),
    MutationSpec(
        "XG2", "cardinality", "transfer_keyed", "xor_group",
        "xor_group_violation",
        r"AND tx\.rail_name = g\.member_rail_name\n"
        r"  AND tx\.status IN \('Posted', 'Pending'\)",
        "AND tx.rail_name = g.member_rail_name\n"
        "  AND tx.status = 'Posted'",
        "count-arm status narrowing: Pending member legs uncounted",
    ),
    MutationSpec(
        "XG3", "cardinality", "transfer_keyed", "xor_group",
        "xor_group_violation",
        r"COUNT\(tx\.transfer_id\)",
        "COUNT(DISTINCT tx.rail_name)",
        "COUNT-shape: double-post on one rail collapses to count 1",
    ),
    MutationSpec(
        "XG4", "cardinality", "transfer_keyed", "xor_group",
        "xor_group_violation",
        r"  AND tx\.template_name = e\.template_name\n",
        "",
        "template-partition drop: NULL-template member-rail legs count",
    ),
    MutationSpec(
        "XG5", "cardinality", "transfer_keyed", "xor_group",
        "xor_group_violation",
        r"WHERE tx\.status IN \('Posted', 'Pending'\)\n"
        r"    AND tx\.template_name IN",
        "WHERE tx.status = 'Posted'\n"
        "    AND tx.template_name IN",
        "existence-arm narrowing: Pending-anchored transfers vanish",
    ),
    # -- cardinality: chain_parent --
    MutationSpec(
        "CP1", "cardinality", "transfer_keyed", "chain_parent",
        "chain_parent_disagreement",
        r"HAVING COUNT\(DISTINCT tx\.transfer_parent_id\) > 1",
        "HAVING COUNT(DISTINCT tx.transfer_parent_id) >= 1",
        "HAVING flip: every single-parent transfer alarms",
    ),
    MutationSpec(
        "CP2", "cardinality", "transfer_keyed", "chain_parent",
        "chain_parent_disagreement",
        r"AND tx\.status IN \('Posted', 'Pending'\)",
        "AND tx.status <> 'Failed'",
        "status widening (the pre-DS.3.3 regression): unknown-status "
        "claims counted again",
    ),
    # -- cardinality: fan_in + its transfer_parents feeder --
    MutationSpec(
        "FI1", "cardinality", "transfer_keyed", "fan_in",
        "fan_in_disagreement",
        r"  LEFT JOIN ",
        "  JOIN ",
        "LEFT JOIN drop: the zero-claim fired child (worst corruption, "
        "the DS.3.3c class) goes invisible again",
    ),
    MutationSpec(
        "FI2", "cardinality", "transfer_keyed", "fan_in",
        "fan_in_disagreement",
        r"AND cpc\.parent_count <> fic\.expected_parent_count",
        "AND cpc.parent_count < fic.expected_parent_count",
        "comparison flip: extra-parent cells vanish",
    ),
    MutationSpec(
        "TP1", "cardinality", "transfer_keyed", "fan_in",
        "transfer_parents",
        r"AND tx\.status IN \('Posted', 'Pending'\)",
        "AND tx.status <> 'Failed'",
        "feeder status widening: unknown-status parent claims "
        "materialize and move fan_in counts",
        downstream=("fan_in_disagreement",),
    ),
    # -- cardinality: multi_xor (spike MX1-MX5 ported + status arms) --
    MutationSpec(
        "MX1", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"HAVING COUNT\(fcd\.matched_child_name\) <> 1",
        "HAVING COUNT(fcd.matched_child_name) > 1",
        "HAVING flip: missed (count 0) cells vanish",
    ),
    MutationSpec(
        "MX2", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"AND ch\.status IN \('Posted', 'Pending'\)",
        "AND ch.status = 'Posted'",
        "child status narrowing: an in-flight (Pending) child stops "
        "counting as a rail choice — killed by the Pending-child "
        "witness cells this battery forced into the domain",
    ),
    MutationSpec(
        "MX3", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"AND ch\.status IN \('Posted', 'Pending'\)",
        "AND ch.status <> 'Failed'",
        "child status widening (pre-DS.3.3 regression): unknown-status "
        "children counted",
    ),
    MutationSpec(
        "MX4", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"AND m\.child_name = ch\.rail_name",
        "AND 1 = 1",
        "membership-EXISTS always-true: non-member children counted",
    ),
    MutationSpec(
        "MX5", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"ON ch\.transfer_parent_id = pf\.parent_transfer_id",
        "ON ch.transfer_id = pf.parent_transfer_id",
        "join-key swap: children never match their parent",
    ),
    MutationSpec(
        "MX6", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"HAVING COUNT\(\*\) >= 2",
        "HAVING COUNT(*) >= 3",
        "qualifier bump: both declared chains stop qualifying as "
        "multi-XOR",
    ),
    MutationSpec(
        "MX7", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"WHERE tx\.template_name IN \(SELECT name FROM parent_names\)\n"
        r"    AND tx\.status IN \('Posted', 'Pending'\)",
        "WHERE tx.template_name IN (SELECT name FROM parent_names)\n"
        "    AND tx.status = 'Posted'",
        "template-parent arm narrowing: Pending-fired template parents "
        "vanish",
    ),
    MutationSpec(
        "MX8", "cardinality", "transfer_keyed", "multi_xor",
        "multi_xor_violation",
        r"WHERE tx\.rail_name IN \(SELECT name FROM parent_names\)\n"
        r"    AND tx\.status IN \('Posted', 'Pending'\)",
        "WHERE tx.rail_name IN (SELECT name FROM parent_names)\n"
        "    AND tx.status = 'Posted'",
        "rail-parent arm narrowing: Pending-fired rail parents vanish",
    ),
    # -- threshold: stuck_pending / stuck_unbundled --
    MutationSpec(
        "SP1", "threshold", "transfer_keyed", "stuck_pending",
        "stuck_pending",
        r"AND tx\.age_seconds > tx\.max_pending_age_seconds",
        "AND tx.age_seconds >= tx.max_pending_age_seconds",
        "strict-> boundary bump: the exactly-at-cap witness alarms",
    ),
    MutationSpec(
        "SP2", "threshold", "transfer_keyed", "stuck_pending",
        "stuck_pending",
        r"FROM {p}_current_transactions ct",
        "FROM {p}_transactions ct",
        "supersession bypass: reading the raw table resurrects the "
        "corrected stuck-old leg — observed kill is STRUCTURAL (the "
        "matview's unique transaction_id index crashes on the "
        "duplicate logical id), the same live channel as CU1",
    ),
    MutationSpec(
        "SU1", "threshold", "transfer_keyed", "stuck_unbundled",
        "stuck_unbundled",
        r"WHERE ct\.bundle_id IS NULL",
        "WHERE ct.bundle_id IS NOT NULL",
        "predicate inversion: unbundled legs invisible, bundled alarm",
    ),
    MutationSpec(
        "SU2", "threshold", "transfer_keyed", "stuck_unbundled",
        "stuck_unbundled",
        r"AND tx\.age_seconds > tx\.max_unbundled_age_seconds",
        "AND tx.age_seconds >= tx.max_unbundled_age_seconds",
        "strict-> boundary bump: the exactly-at-cap witness alarms",
    ),
    # -- money: limit_breach --
    MutationSpec(
        "LB1", "money", "transfer_keyed", "limit_breach",
        "limit_breach",
        r"AND outbound_total > cap",
        "AND outbound_total >= cap",
        "strict-> boundary bump: the exactly-at-cap flow alarms",
    ),
    MutationSpec(
        "LB2", "money", "transfer_keyed", "limit_breach",
        "limit_breach",
        r"\(ls\.cap \* 100\)",
        "(ls.cap * 1)",
        "config-resolution corruption: dollars-vs-cents shift dropped, "
        "caps shrink a hundredfold",
    ),
    MutationSpec(
        "LB3", "money", "transfer_keyed", "limit_breach",
        "limit_breach",
        r"WHERE tx\.amount_direction = 'Debit'",
        "WHERE tx.amount_direction = 'Credit'",
        "sign/direction flip: outbound flow computed from credit legs",
    ),
    MutationSpec(
        "LB4", "money", "transfer_keyed", "limit_breach",
        "limit_breach",
        r"AND tx\.status = 'Posted'",
        "AND tx.status <> 'Failed'",
        "money-status widening: Pending / unknown legs move flow",
    ),
    # -- supersession (structural channel expected) --
    MutationSpec(
        "CU1", "supersession", "transfer_keyed", "stuck_pending",
        "current_transactions",
        r"WHERE tx\.entry = \(\n"
        r"    SELECT MAX\(entry\) FROM {p}_transactions WHERE id = tx\.id\n"
        r"\)",
        "WHERE 1 = 1",
        "supersession drop: duplicate logical ids must crash the "
        "unique-id index rebuild (the DS.0 mutant-C channel)",
    ),
    # -- money: drift / overdraft / expected_eod on the LOCF domain --
    MutationSpec(
        "DR1", "money", "locf", "drift",
        "drift",
        r"AND sb\.effective_money <> cb\.computed_balance",
        "AND sb.effective_money > cb.computed_balance",
        "comparison flip: negative drifts vanish",
    ),
    MutationSpec(
        "DR2", "money", "locf", "drift",
        "computed_subledger_balance",
        r"AND tx\.posting <= sb\.business_day_end",
        "AND tx.posting < sb.business_day_end",
        "day-boundary <= -> <: the at-cutoff leg drops out of the "
        "computed balance (the DS.0 mutant-B lesson — killed by the "
        "boundary sub-grid, a noon-only domain misses it)",
        downstream=("drift",),
    ),
    MutationSpec(
        "DR3", "money", "locf", "drift",
        "computed_subledger_balance",
        r"AND tx\.status = 'Posted'",
        "AND tx.status IN ('Posted', 'Pending')",
        "money-status widening: Pending legs move the computed balance",
        downstream=("drift",),
    ),
    MutationSpec(
        "DR4", "money", "locf", "drift",
        "drift",
        r"sb\.effective_money - cb\.computed_balance AS drift",
        "sb.effective_money + cb.computed_balance AS drift",
        "sign flip on the residual projection: killed by VALUE "
        "comparison (keys alone would miss it)",
    ),
    MutationSpec(
        "OV1", "money", "locf", "overdraft",
        "overdraft",
        r"AND sb\.effective_money < 0",
        "AND sb.effective_money <= 0",
        "strict-< boundary bump: zero-balance days alarm",
    ),
    MutationSpec(
        "EO1", "money", "locf", "expected_eod",
        "expected_eod_balance_breach",
        r"AND sb\.money <> sb\.expected_eod_balance",
        "AND sb.money < sb.expected_eod_balance",
        "comparison flip: over-expectation breaches vanish",
    ),
    MutationSpec(
        "CU2", "supersession", "locf", "drift",
        "current_daily_balances",
        r"WHERE sb\.entry = \(\n"
        r"    SELECT MAX\(entry\)\n"
        r"    FROM {p}_daily_balances\n"
        r"    WHERE account_id = sb\.account_id\n"
        r"      AND business_day_start = sb\.business_day_start\n"
        r"\)",
        "WHERE 1 = 1",
        "balance supersession drop: duplicate (account, day) claims "
        "must crash the unique index rebuild",
    ),
)


def battery_for(domain: str) -> tuple[MutationSpec, ...]:
    return tuple(spec for spec in BATTERY if spec.domain == domain)
