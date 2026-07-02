"""DS.3.5 — the PROVEN-on-D enumeration gate.

For every database state in each detector's finite boundary-derived
domain (tests/enumeration/domains/), the REAL engine — unmodified
``emit_schema`` + ``refresh_matviews_sql``, real refresh order, real
UNIQUE-index contracts — must produce exactly the violation set
``{cells : residual(state) != 0}``, with the residuals being the DS.1
laws in ``recon_gen.common.spine.residuals`` (never re-implemented
here).

Gate pieces:

- per-detector engine==residual on the packed domains (12 detectors;
  ``anomaly`` excluded with rationale in domains/anomaly.py);
- the l1_exceptions rollup union (13th target);
- the packed-vs-isolated sampled lemma (the packing contracts'
  empirical check: a cell alone in a fresh DB behaves identically to
  its packed run);
- the BoundaryProfile coverage lint (every L2-RESOLVED cap's
  {c-1, c, c+1} neighborhood present in the domain that claims it —
  a SQL-text lint is vacuous for config-delivered caps);
- planted-boundary smoke per threshold detector (at-cap NOT stuck /
  breached, cap+1 stuck / breached — explicit witnesses independent
  of the residual plumbing);
- the statement-timeout guard's loud-fail path;
- the fan_in zero-parent blind-spot FINDING pin (a REAL
  engine-vs-law divergence, documented in domains/fan_in.py — this
  test asserts BOTH sides so the divergence stays executable until
  the production fix lands; it must not be "fixed" by weakening the
  law side).

Tier: RECON_GEN_ENUM_TIER (full default / quick narrows domains —
sample). Packed runs are built once per process and cached.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import duckdb
import pytest

from recon_gen.common.env_keys import RECON_GEN_FUZZ_SEED
from tests.enumeration import harness
from tests.enumeration.domains import (
    DETECTOR_DOMAINS,
    DOMAIN_BUILDERS,
    L1_EXCEPTIONS_DOMAINS,
    fan_in,
    l1_exceptions,
    limit_breach,
    stuck_pending,
    stuck_unbundled,
)
from tests.enumeration.domains._base import (
    SPEC_EXAMPLE,
    SPEC_PREFIX,
    spec_profile,
)
from tests.enumeration.harness import (
    EnumerationDB,
    EnumerationTimeout,
    PackedDomain,
    ViolationMap,
    build_packed_db,
    diff_violations,
    enum_tier,
    isolated_cell_diffs,
)

# The <30s-average unit-tier rule (DS.0 §6.5) as a loud regression
# tripwire per packed domain (build + engine reads). Generous against
# the measured ~2s worst domain, tight against a quadratic regression.
TIER_BUDGET_SECONDS = 30.0


@dataclass
class PackedRun:
    domain: PackedDomain
    db: EnumerationDB
    engine: dict[str, ViolationMap]
    timings: dict[str, float] = field(default_factory=dict[str, float])


_RUNS: dict[str, PackedRun] = {}


def _packed(name: str) -> PackedRun:
    run = _RUNS.get(name)
    if run is not None:
        return run
    t0 = time.perf_counter()
    domain = DOMAIN_BUILDERS[name]()
    t_build = time.perf_counter() - t0
    t0 = time.perf_counter()
    db = build_packed_db(domain)
    t_db = time.perf_counter() - t0
    t0 = time.perf_counter()
    engine = {
        check.detector: check.read_engine(db) for check in domain.checks
    }
    t_read = time.perf_counter() - t0
    run = PackedRun(
        domain=domain, db=db, engine=engine,
        timings={
            "domain_build": t_build, "db_insert_refresh": t_db,
            "engine_read": t_read,
        },
    )
    _RUNS[name] = run
    return run


def _lemma_sample_size() -> int:
    return 3 if enum_tier() == "quick" else 25


DETECTORS = tuple(DETECTOR_DOMAINS)


@pytest.mark.parametrize("detector", DETECTORS)
def test_engine_violations_equal_residual(detector: str) -> None:
    """PROVEN-on-D core: engine violation set == {cells: residual != 0}
    (keys AND residual values) on every packed domain that answers for
    the detector."""
    for domain_name in DETECTOR_DOMAINS[detector]:
        run = _packed(domain_name)
        diff = diff_violations(
            run.engine[detector],
            run.domain.expected_for(detector),
            label=f"{domain_name}/{detector} (tier={enum_tier()})",
        )
        assert not diff, diff


def test_l1_exceptions_rollup_union() -> None:
    """The 13th target: the rollup's row MULTISET equals the union of
    its source matviews' branch projections on every packed DB."""
    for domain_name in L1_EXCEPTIONS_DOMAINS:
        run = _packed(domain_name)
        engine, source_union = l1_exceptions.union_maps(run.db)
        diff = diff_violations(
            engine, source_union,
            label=f"{domain_name}/l1_exceptions rollup-vs-source-union",
        )
        assert not diff, diff


@pytest.mark.parametrize("domain_name", sorted(DOMAIN_BUILDERS))
def test_packed_vs_isolated_lemma(domain_name: str) -> None:
    """Sampled packing-independence: a cell alone in a fresh DB (plus
    the domain's anchors — they are part of the packing contract)
    produces exactly the packed run's rows restricted to the cell's
    id prefixes. Seeded deterministically; RECON_GEN_FUZZ_SEED
    overrides for repro."""
    run = _packed(domain_name)
    seed = RECON_GEN_FUZZ_SEED.get_or_none() or 42
    rng = random.Random(seed)
    cell_count = len(run.domain.cells)
    size = min(_lemma_sample_size(), cell_count)
    sample = rng.sample(range(cell_count), size)
    problems: list[str] = []
    for index in sample:
        problems.extend(isolated_cell_diffs(run.domain, run.engine, index))
    assert not problems, (
        f"packing lemma failed (seed={seed}, sample={sample}):\n"
        + "\n".join(problems)
    )


def test_boundary_profile_coverage_lint() -> None:
    """Every L2-RESOLVED comparison value's {c-1, c, c+1} neighborhood
    must appear in the domain that claims to cover it. Derived from
    the RESOLVED instance — the DS.0 attack finding: a SQL-text lint
    passes vacuously on the four config-cap detectors."""
    profile = spec_profile()
    assert profile.limit_caps_cents, "no limit schedules resolved"
    assert profile.pending_age_caps, "no pending-age caps resolved"
    assert profile.unbundled_age_caps, "no unbundled-age caps resolved"
    assert profile.fan_in_expected, "no fan_in expectations resolved"

    flows = limit_breach.covered_flows(profile)
    for key, cap in profile.limit_caps_cents.items():
        needed = {cap - 1, cap, cap + 1}
        assert needed <= flows.get(key, frozenset()), (
            f"limit_breach domain misses boundary flows for {key}: "
            f"cap={cap}, covered={sorted(flows.get(key, frozenset()))}"
        )
    pending_ages = stuck_pending.covered_ages(profile)
    for rail, cap in profile.pending_age_caps.items():
        assert {cap - 1, cap, cap + 1} <= pending_ages.get(rail, frozenset())
    unbundled_ages = stuck_unbundled.covered_ages(profile)
    for rail, cap in profile.unbundled_age_caps.items():
        assert {cap - 1, cap, cap + 1} <= unbundled_ages.get(rail, frozenset())
    counts = fan_in.covered_parent_counts(profile)
    for key, expected in profile.fan_in_expected.items():
        boundary = 2 if expected is None else expected
        assert {boundary - 1, boundary, boundary + 1} <= counts.get(
            key, frozenset(),
        )


def test_threshold_planted_boundary_smoke() -> None:
    """Explicit at-cap / over-cap witnesses per threshold detector,
    asserted directly on the packed engine sets (independent of the
    residual plumbing): exactly-at-cap is NOT a violation (strict >),
    one unit over IS."""
    run = _packed("transfer_keyed")
    profile = spec_profile()
    for detector, at_key, over_key in (
        ("stuck_pending", stuck_pending.AT_CAP_KEY,
         stuck_pending.OVER_CAP_KEY),
        ("stuck_unbundled", stuck_unbundled.AT_CAP_KEY,
         stuck_unbundled.OVER_CAP_KEY),
        ("limit_breach", limit_breach.at_cap_key(profile),
         limit_breach.over_cap_key(profile)),
    ):
        engine = run.engine[detector]
        assert at_key not in engine, (
            f"{detector}: exactly-at-cap cell {at_key} fired — the "
            f"strict-> boundary moved"
        )
        assert over_key in engine, (
            f"{detector}: one-over-cap cell {over_key} did not fire"
        )
        assert engine[over_key] == 1, (
            f"{detector}: one-over-cap residual should be exactly 1, "
            f"got {engine[over_key]!r}"
        )


def test_fan_in_zero_parent_engine_blind_spot() -> None:
    """FOUND by the DS.3.5 gate, FIXED at DS.3.3c — kept as the
    permanent witness.

    A fan_in child transfer with firing legs but ZERO parent claims —
    the worst corruption shape, no contribution landed at all — was
    INVISIBLE pre-fix (the count spine built FROM the parent-claim
    table, so one-of-two-missing alarmed while all-missing produced
    no row). The spine now seeds from the child template's own
    firings; the engine emits ('missing', parent_count 0) and agrees
    with the signed law's residual on this cell.
    """
    cell, law_residual = fan_in.zero_parent_finding_rows()
    assert law_residual == -2, "the law side moved — re-derive the finding"
    db = EnumerationDB(
        harness.artifacts_for(SPEC_EXAMPLE, prefix=SPEC_PREFIX),
    )
    try:
        db.insert(cell.tx_rows, cell.bal_rows)
        db.refresh()
        engine = fan_in.read_engine(db)
        assert engine == cell.expected["fan_in"], (
            f"zero-parent fan_in cell diverged: engine {engine!r} vs "
            f"law {cell.expected['fan_in']!r}"
        )
    finally:
        db.close()


def test_statement_timeout_guard_fails_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DS.3.4 hang guard: a DB call outliving the watchdog is
    interrupted and surfaces as EnumerationTimeout naming the call —
    never a silent stall of the tier."""
    monkeypatch.setattr(harness, "STATEMENT_TIMEOUT_SECONDS", 0.05)
    conn = duckdb.connect(":memory:")
    db = object.__new__(EnumerationDB)
    db.prefix = "guard"
    db._conn = conn  # noqa: SLF001 — constructing the minimal guard harness
    db._refresh_sql = ""  # noqa: SLF001
    try:
        with pytest.raises(EnumerationTimeout, match="guard probe"):
            db._guarded(  # noqa: SLF001
                "guard probe",
                lambda: conn.execute(
                    "SELECT COUNT(*) FROM range(100000000) a, "
                    "range(100000) b",
                ).fetchall(),
            )
    finally:
        conn.close()


def test_zz_domain_timings_within_tier_budget() -> None:
    """Report per-domain wall costs and enforce the DS.0 §6.5 tier
    rule as a regression tripwire. Runs last (alphabetical zz) so it
    sees every packed run this worker built."""
    report: list[str] = []
    for name, run in sorted(_RUNS.items()):
        total = sum(run.timings.values())
        report.append(
            f"{name}: cells={len(run.domain.cells)} "
            + " ".join(f"{k}={v:.2f}s" for k, v in run.timings.items())
            + f" total={total:.2f}s"
        )
        assert total < TIER_BUDGET_SECONDS, (
            f"packed domain {name} took {total:.1f}s — over the "
            f"{TIER_BUDGET_SECONDS}s unit-tier rule (DS.0 §6.5); "
            f"re-measure and re-tier"
        )
    print("\n[ds35 enumeration timings]\n" + "\n".join(report))
