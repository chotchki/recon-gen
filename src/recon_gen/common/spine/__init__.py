"""The invariant spine (D6).

The typed source-of-truth for the project's invariant-violation model:
`Violation` is the currency that flows; `Invariant.detect()` produces
them, `ViolationGenerator.emit()` manufactures the rows that should
trip them. Promoted from the AP.3 + AS.0 spikes (`docs/audits/
date_range_model_audit.md` §5 "AS.0 result").

Module layout (AS.1):

- `violation.py` — `Violation` frozen dataclass + `Violation.of()` smart
  constructor. The currency type; no behaviour, just identity.
- `invariant.py` — `Invariant` Protocol. `name` + `detect(conn) ->
  set[Violation]`. Detectors are thin SQL reads of the existing matview
  output, NOT re-encoded matview logic.
- `generator.py` — `ViolationGenerator` Protocol. `intended` + `emit
  (conn) -> None`. Producer ≠ thing-produced; the generator claims to
  cause a violation, the violation is what `detect()` returns.

What is NOT here:

- Concrete invariants / generators (DriftInvariant etc.) — AS.2 lands
  those alongside the per-invariant smart constructors
  (`Invariant.scenario_for(...)`) that vary per shape selector.
- `View` — stays on `common/tree/date_view.py`; AR.1 already promoted
  it. The spine references it.
- The stateful-fold base for `ViolationGenerator` (the AP.2 shape) —
  AS.3 lands it; AS.1's Protocol is minimal so per-invariant generators
  can specialize freely.
"""

from __future__ import annotations

from recon_gen.common.spine.account_simulation import (
    AccountSimulation,
    DayEmission,
    DayPlan,
    Perturbation,
)
from recon_gen.common.spine.ledger_simulation import (
    LedgerSimulation,
    Transfer,
    TransferLeg,
)
from recon_gen.common.spine.dry_run_renderer import render_captured_sql
from recon_gen.common.spine.plant_adapter import scenario_to_generators
from recon_gen.common.spine.scenario_context import (
    ClaimedAccountsGenerator,
    ScenarioContext,
    dry_run_capture,
    scenario_metadata,
)
from recon_gen.common.spine.semantic_lock import apply_scenario, semantic_lock
from recon_gen.common.spine.training import TrainingScenario, validate_all
from recon_gen.common.spine.drift import (
    DriftGenerator,
    DriftInvariant,
    LedgerDriftInvariant,
)
from recon_gen.common.spine.expected_eod import (
    ExpectedEodBalanceGenerator,
    ExpectedEodBalanceInvariant,
)
from recon_gen.common.spine.generator import ViolationGenerator
from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.limit_breach import (
    LimitBreachGenerator,
    LimitBreachInvariant,
)
from recon_gen.common.spine.overdraft import OverdraftGenerator, OverdraftInvariant
from recon_gen.common.spine.stuck_pending import (
    StuckPendingGenerator,
    StuckPendingInvariant,
)
from recon_gen.common.spine.stuck_unbundled import (
    StuckUnbundledGenerator,
    StuckUnbundledInvariant,
)
from recon_gen.common.spine.anomaly import AnomalyGenerator, AnomalyInvariant
from recon_gen.common.spine.anomaly_view import BUCKET_LOWER_BOUNDS, AnomalyView
from recon_gen.common.spine.chain_parent_disagreement import (
    ChainParentDisagreementGenerator,
    ChainParentDisagreementInvariant,
)
from recon_gen.common.spine.failed_transaction import FailedTransactionGenerator
from recon_gen.common.spine.fan_in_disagreement import (
    FanInChainGenerator,
    FanInDisagreementInvariant,
)
from recon_gen.common.spine.inv_fanout import (
    InvFanoutFactory,
    InvFanoutGenerator,
)
from recon_gen.common.spine.rail_firing import (
    RailFiringFactory,
    RailFiringGenerator,
)
from recon_gen.common.spine.supersession import SupersessionGenerator
from recon_gen.common.spine.transfer_template import (
    TransferTemplateFactory,
    TransferTemplateGenerator,
)
from recon_gen.common.spine.two_template_chain import (
    TwoTemplateChainFactory,
    TwoTemplateChainGenerator,
)
from recon_gen.common.spine.multi_xor_violation import (
    MultiXorMissedGenerator,
    MultiXorOverlapGenerator,
    MultiXorViolationInvariant,
)
from recon_gen.common.spine.xor_group_violation import (
    XorGroupMissedFiringGenerator,
    XorGroupOverlapGenerator,
    XorGroupViolationInvariant,
)
from recon_gen.common.spine.money_trail import (
    MoneyTrailGenerator,
    MoneyTrailInvariant,
    MoneyTrailView,
)
from recon_gen.common.spine.registry import (
    ALL_AUDIT_FIXTURE_GENERATORS,
    ALL_COVERAGE_GENERATORS,
    ALL_GENERATORS,
    ALL_INVARIANTS,
    ALL_L1_GENERATORS,
    ALL_L1_INVARIANTS,
    ALL_L2_INVESTIGATION_GENERATORS,
    ALL_L2_INVESTIGATION_INVARIANTS,
    ALL_L2_SHAPE_GENERATORS,
    ALL_L2_SHAPE_INVARIANTS,
    INVARIANT_GENERATOR_EDGES,
    generators_for,
    invariants_for,
    iter_edges,
)
from recon_gen.common.spine.rng import SCENARIO_BASE_SEED, scenario_rng
from recon_gen.common.spine.violation import (
    AuditFixture,
    CoverageObservation,
    RuleViolation,
    Violation,
)

__all__ = [
    # Typed evidence currency (AS.1; layered into subtypes by AY.2.a)
    "Violation",  # abstract base
    "RuleViolation",  # matview-detected rule break (the AS post-shape)
    "CoverageObservation",  # seed-color presence claim (AY.2.b plants)
    "AuditFixture",  # audit-PDF input marker (AY.2.b plants)
    # Spine Protocols (AS.1)
    "Invariant",
    "ViolationGenerator",
    # Deterministic RNG factory (AS.1 follow-on)
    "scenario_rng",
    "SCENARIO_BASE_SEED",
    # Drift family — concrete invariants + generator (AS.2)
    "DriftInvariant",
    "LedgerDriftInvariant",
    "DriftGenerator",
    # Overdraft family — concrete invariant + generator (AU.1)
    "OverdraftInvariant",
    "OverdraftGenerator",
    # Expected-EOD-balance family — concrete invariant + generator (AU.3.a)
    "ExpectedEodBalanceInvariant",
    "ExpectedEodBalanceGenerator",
    # Stuck-Pending family — first transaction-based + L2-coupled (AU.3.b)
    "StuckPendingInvariant",
    "StuckPendingGenerator",
    # Stuck-Unbundled family — twin of stuck_pending (AU.3.c)
    "StuckUnbundledInvariant",
    "StuckUnbundledGenerator",
    # Limit-Breach family — deepest L2 coupling, from_instance (AU.4)
    "LimitBreachInvariant",
    "LimitBreachGenerator",
    # Anomaly family — windowed-statistical L2 (AT.1; AT.3 refactors generator
    # onto LedgerSimulation.transfers per the AT.2 decomposition decision)
    "AnomalyInvariant",
    "AnomalyGenerator",
    # Anomaly View — σ-threshold slice over the detector's output (AT.2)
    "AnomalyView",
    "BUCKET_LOWER_BOUNDS",
    # Chain-parent-disagreement family — L2-shape integrity invariant
    # promoted from common/l2/seed.py's ChainParentDisagreementPlant
    # (AB.2.6) onto the spine in AX.1.
    "ChainParentDisagreementInvariant",
    "ChainParentDisagreementGenerator",
    # XOR-group-violation family — single invariant; 2 generators for
    # the missed-firing + overlap variants (AB.3.5 / AB.3.5b);
    # promoted in AX.2.
    "XorGroupViolationInvariant",
    "XorGroupMissedFiringGenerator",
    "XorGroupOverlapGenerator",
    # Fan-in-disagreement family — single invariant; 1 generator with
    # 3 smart constructors for healthy/missing/extra/orphan variants
    # (AB.4.5 family); promoted in AX.3. parent_count knob differen-
    # tiates the variant; the matview derives disagreement_kind by
    # comparing against the L2 chain's expected_parent_count.
    "FanInDisagreementInvariant",
    "FanInChainGenerator",
    # Multi-XOR-violation family — single invariant; 2 generators for
    # the missed + overlap variants (AB.6.6); promoted in AX.4. Parent
    # firing of a multi-XOR-child chain that fires 0 (missed) or ≥2
    # (overlap) of its declared XOR-sibling children.
    "MultiXorViolationInvariant",
    "MultiXorMissedGenerator",
    "MultiXorOverlapGenerator",
    # Audit-fixture generators (AY.2.b) — emit rows the audit PDF
    # reads directly; no matview, no invariant. `intended` returns
    # an AuditFixture (the AY.2.a evidence-currency subtype).
    "FailedTransactionGenerator",
    "SupersessionGenerator",
    # Seed-color coverage generators (AY.2.b) — emit CoverageObservation
    # evidence the L1 PostedRequirements panel + audit-PDF coverage
    # sections read directly. Non-violating by construction. The
    # factories pick + resolve from the L2 instance; the generators
    # are kind-discriminated internally (TwoLegRail vs SingleLegRail).
    "TwoTemplateChainFactory",
    "TwoTemplateChainGenerator",
    "RailFiringFactory",
    "RailFiringGenerator",
    "TransferTemplateFactory",
    "TransferTemplateGenerator",
    "InvFanoutFactory",
    "InvFanoutGenerator",
    # Money-trail family — recursive-graph L2; AT.3 promoted generator
    # + View on top of LedgerSimulation.transfers (parent-linked chain).
    "MoneyTrailInvariant",
    "MoneyTrailGenerator",
    "MoneyTrailView",
    # Many-to-many registry (AS.2; AU.1 + AU.3.a/b/c + AU.4 add edges;
    # AX.5 splits into 3 category-scoped tuples + unified ALL_*;
    # AY.2.b adds the audit-fixture category)
    "INVARIANT_GENERATOR_EDGES",
    "ALL_L1_INVARIANTS",
    "ALL_L1_GENERATORS",
    "ALL_L2_SHAPE_INVARIANTS",
    "ALL_L2_SHAPE_GENERATORS",
    "ALL_L2_INVESTIGATION_INVARIANTS",
    "ALL_L2_INVESTIGATION_GENERATORS",
    "ALL_AUDIT_FIXTURE_GENERATORS",
    "ALL_COVERAGE_GENERATORS",
    "ALL_INVARIANTS",
    "ALL_GENERATORS",
    "invariants_for",
    "generators_for",
    "iter_edges",
    # Stateful-fold primitive (AS.3) — scalar-account; AS.4 vector
    "AccountSimulation",
    "DayPlan",
    "Perturbation",
    "DayEmission",
    # Vector-state composition (AS.4) — many AccountSimulations side by side
    "LedgerSimulation",
    # Transfer primitive (AT.3) — cross-account flow on LedgerSimulation
    "Transfer",
    "TransferLeg",
    # Semantic-lock mechanism (AS.5) — replaces SQL-byte-identity locks
    "apply_scenario",
    "semantic_lock",
    # ScenarioContext composition safety + per-row scenario tagging (AV.5)
    "ScenarioContext",
    "ClaimedAccountsGenerator",
    "scenario_metadata",
    # AY.4.a — dry-run SQL capture for the production-seed reroute.
    "dry_run_capture",
    # AY.4.b — render captured (sql, params) as static SQL text.
    "render_captured_sql",
    # AY.4.c.3 — ScenarioPlant → ViolationGenerator tuple adapter.
    "scenario_to_generators",
    # Self-validating training/docs scenarios (AS.7; AT.6 reuses for L2)
    "TrainingScenario",
    "validate_all",
]
