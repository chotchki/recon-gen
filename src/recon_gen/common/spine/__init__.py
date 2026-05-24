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
from recon_gen.common.spine.scenario_context import (
    ClaimedAccountsGenerator,
    ScenarioContext,
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
from recon_gen.common.spine.money_trail import (
    MoneyTrailGenerator,
    MoneyTrailInvariant,
    MoneyTrailView,
)
from recon_gen.common.spine.registry import (
    ALL_L1_GENERATORS,
    ALL_L1_INVARIANTS,
    INVARIANT_GENERATOR_EDGES,
    generators_for,
    invariants_for,
    iter_edges,
)
from recon_gen.common.spine.rng import SCENARIO_BASE_SEED, scenario_rng
from recon_gen.common.spine.violation import Violation

__all__ = [
    # Protocols + currency type (AS.1)
    "Violation",
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
    # Money-trail family — recursive-graph L2; AT.3 promoted generator
    # + View on top of LedgerSimulation.transfers (parent-linked chain).
    "MoneyTrailInvariant",
    "MoneyTrailGenerator",
    "MoneyTrailView",
    # Many-to-many registry (AS.2; AU.1 + AU.3.a/b/c + AU.4 add edges)
    "INVARIANT_GENERATOR_EDGES",
    "ALL_L1_INVARIANTS",
    "ALL_L1_GENERATORS",
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
    # Self-validating training/docs scenarios (AS.7; AT.6 reuses for L2)
    "TrainingScenario",
    "validate_all",
]
