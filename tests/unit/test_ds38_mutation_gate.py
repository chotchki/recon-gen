"""DS.3.8 — the mutation-score gate: domain adequacy MEASURED.

Every named mutant in ``tests/enumeration/mutations.py`` corrupts the
EMITTED SQL of one detector (or one shared-infrastructure layer) and
re-materializes it against the already-loaded packed enumeration
domain. The gate's whole point is the assertion at the end: EVERY
mutant is KILLED — by the violation-set comparator (keys or values)
or by a structural channel (an index-rebuild crash is a live
detection layer, the DS.0 mutant-C lesson). A surviving mutant is a
LOUD failure naming it: it means the enumeration domain cannot see a
real semantic corruption, i.e. a domain gap — the fix is widening the
domain EXPLICITLY (as the battery already forced for multi_xor's
Pending-child axis and fan_in's zero-claim shapes), never deleting
the mutant.

Baseline integrity: the original text is restored and the detector
re-read after every mutant; a baseline mismatch is a harness error,
not a verdict. A final sweep re-reads every detector on both packed
DBs against the pre-battery baselines.

Tier: unit (operator-decided at DS.0 sign-off, §7 — the battery
measured in low single-digit seconds; the tier-budget tripwire below
re-measures on every run, and demotion to agreement is a config
change, not a redesign).
"""
from __future__ import annotations

import time

from tests.enumeration.domains import DOMAIN_BUILDERS
from tests.enumeration.harness import (
    EnumerationDB,
    ViolationMap,
    build_packed_db,
)
from tests.enumeration.mutations import (
    BATTERY,
    CHECKS_BY_DETECTOR,
    MutantOutcome,
    battery_for,
    run_mutant,
    statement_units,
)

# The DS.0 §6.5 unit-tier rule as a per-domain regression tripwire
# (build + battery). The measured cost is a small fraction of this.
TIER_BUDGET_SECONDS = 30.0

_DOMAINS_UNDER_TEST = ("transfer_keyed", "locf")


class _Run:
    def __init__(self, name: str) -> None:
        t0 = time.perf_counter()
        self.domain = DOMAIN_BUILDERS[name]()
        self.db: EnumerationDB = build_packed_db(self.domain)
        self.units = statement_units(self.domain.artifacts)
        self.baselines: dict[str, ViolationMap] = {
            check.detector: check.read_engine(self.db)
            for check in self.domain.checks
        }
        self.build_seconds = time.perf_counter() - t0
        self.outcomes: list[MutantOutcome] = []


_RUNS: dict[str, _Run] = {}


def _run(name: str) -> _Run:
    run = _RUNS.get(name)
    if run is None:
        run = _Run(name)
        _RUNS[name] = run
    return run


def _outcome_table(outcomes: list[MutantOutcome]) -> str:
    lines = [
        f"{'mutant':8} {'family':13} {'detector':15} {'killed-by':12} "
        f"{'diffs':>6} {'secs':>6}  note / witness",
    ]
    for o in outcomes:
        channel = o.channel if o.killed else "SURVIVOR"
        lines.append(
            f"{o.mutation_id:8} {o.family:13} {o.detector:15} "
            f"{channel:12} {o.divergent_keys:6d} {o.elapsed_seconds:6.2f}"
            f"  {o.note}",
        )
        for witness in o.witnesses:
            lines.append(f"{'':66}{witness}")
        if o.detail:
            lines.append(f"{'':66}{o.detail}")
    return "\n".join(lines)


def _run_battery(domain_name: str) -> None:
    run = _run(domain_name)
    specs = battery_for(domain_name)
    assert specs, f"no mutants target the {domain_name} domain"
    t0 = time.perf_counter()
    for spec in specs:
        baseline = run.baselines[spec.detector]
        run.outcomes.append(run_mutant(run.db, run.units, spec, baseline))
    battery_seconds = time.perf_counter() - t0
    table = _outcome_table(run.outcomes)
    print(
        f"\n[ds38 {domain_name}] build={run.build_seconds:.2f}s "
        f"battery={battery_seconds:.2f}s "
        f"mutants={len(run.outcomes)}\n{table}",
    )
    survivors = [o for o in run.outcomes if not o.killed]
    assert not survivors, (
        f"SURVIVING mutants on {domain_name} — the packed domain cannot "
        f"see these semantic corruptions (a domain gap; widen the domain "
        f"explicitly, never delete the mutant):\n"
        + "\n".join(f"  {o.mutation_id}: {o.note}" for o in survivors)
        + f"\n\nfull battery:\n{table}"
    )
    total = run.build_seconds + battery_seconds
    assert total < TIER_BUDGET_SECONDS, (
        f"{domain_name} battery took {total:.1f}s — over the "
        f"{TIER_BUDGET_SECONDS}s unit-tier rule (DS.0 §6.5); re-measure "
        f"and re-tier (demotion is a config change)"
    )
    # Final integrity sweep: EVERY detector on the packed DB (not just
    # the mutated ones) still reads its pre-battery baseline, so no
    # mutant leaked state into the shared DB.
    for check in run.domain.checks:
        assert check.read_engine(run.db) == run.baselines[check.detector], (
            f"{domain_name}/{check.detector}: baseline drifted after "
            f"the battery — a mutant was not fully restored"
        )


def test_battery_covers_every_declared_family() -> None:
    """The battery's shape contract: every family the DS.3.8 brief
    names is present, and every spec targets a domain this gate
    actually runs."""
    families = {spec.family for spec in BATTERY}
    assert families == {"cardinality", "threshold", "money", "supersession"}
    domains = {spec.domain for spec in BATTERY}
    assert domains <= set(_DOMAINS_UNDER_TEST)
    ids = [spec.mutation_id for spec in BATTERY]
    assert len(ids) == len(set(ids)), "duplicate mutation ids"
    for spec in BATTERY:
        assert spec.detector in CHECKS_BY_DETECTOR, spec.mutation_id


def test_mutation_battery_transfer_keyed() -> None:
    """Cardinality + threshold + limit + the transactions-side
    supersession mutants against the packed transfer-keyed domain."""
    _run_battery("transfer_keyed")


def test_mutation_battery_locf() -> None:
    """Money-family + balance-side supersession mutants against the
    packed LOCF domain."""
    _run_battery("locf")
