"""Invariant ⋈ generator registry — many-to-many edge bookkeeping.

The audit's AP.3 finding (`docs/audits/date_range_model_audit.md` §5):
a `drift` `ViolationGenerator` trips BOTH `DriftInvariant` AND
`LedgerDriftInvariant`; the `xor_variant_*` generators both trip the
single `xor_group_violation` detector. The mapping is many-to-many,
not 1:1 — so a direct rename of `PlantKind` → `check_type` would lose
information. This module is the explicit edge table.

Shape: `INVARIANT_GENERATOR_EDGES` is `dict[type[ViolationGenerator],
tuple[type[Invariant], ...]]`. Reading: "instances of this generator
class, when emitted, trip detect() on at least these invariant classes."

What this enforces (via `tests/unit/test_spine_drift.py`):
- For every (generator_class, invariant_class) edge listed here, an
  emitted generator instance actually causes `invariant.detect()` to
  include the appropriate Violation. The empirical contract that
  "many-to-many" claims a generator's emission ACTUALLY trips that
  detector — not just that someone wrote it down.

What this enforces additionally (via `tests/unit/test_spine_au5_exhaustiveness.py`):

- **Per-generator-class exhaustiveness** — every class in
  `ALL_GENERATORS` has an entry in `INVARIANT_GENERATOR_EDGES`.
  Catches "I promoted a new generator but forgot to register it."
- **Per-invariant-class coverage** — every class in `ALL_INVARIANTS`
  is reached by at least one generator's edge set. Catches "I promoted
  a new invariant but forgot to wire any generator to it."
- **Empirical-edge contract** — every registered edge actually fires
  on emission (existing per-invariant tests cover this; AU.5's
  parametrized test consolidates).

AX.5 registry split (2026-05-23): the spine's coverage now spans
three invariant categories — L1 accounting (drift / overdraft /
expected_eod / stuck_* / limit_breach), L2-shape integrity
(chain_parent_disagreement / xor_group_violation /
fan_in_disagreement / multi_xor_violation — the ETL-side contracts
that the L2 yaml's declared chain/XOR/fan-in structure is honored),
and L2-investigation fraud/AML pattern (anomaly / money_trail). Each
category gets its own `ALL_<CATEGORY>_INVARIANTS` + `ALL_<CATEGORY>
_GENERATORS` tuple; `ALL_INVARIANTS` + `ALL_GENERATORS` are the
unified flat sequences AU.5's exhaustiveness gate walks.

What this does NOT enforce yet:
- View edges (`invariant → {Views}`); AT lands those when L2's
  Investigation surface comes online.
- Composition-induced edges (AU.2 finding: drift+parent-overdraft →
  ledger_drift on overdraft's parent). The registry stays
  per-generator-class; composition-induced behavior is documented +
  tested at the scenario level in
  `tests/unit/test_spine_au2_composition.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from recon_gen.common.spine.anomaly import (
    AnomalyGenerator,
    AnomalyInvariant,
)
from recon_gen.common.spine.chain_parent_disagreement import (
    ChainParentDisagreementGenerator,
    ChainParentDisagreementInvariant,
)
from recon_gen.common.spine.drift import (
    DriftGenerator,
    DriftInvariant,
    LedgerDriftInvariant,
)
from recon_gen.common.spine.expected_eod import (
    ExpectedEodBalanceGenerator,
    ExpectedEodBalanceInvariant,
)
from recon_gen.common.spine.fan_in_disagreement import (
    FanInChainGenerator,
    FanInDisagreementInvariant,
)
from recon_gen.common.spine.generator import ViolationGenerator
from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.limit_breach import (
    LimitBreachGenerator,
    LimitBreachInvariant,
)
from recon_gen.common.spine.money_trail import (
    MoneyTrailGenerator,
    MoneyTrailInvariant,
)
from recon_gen.common.spine.multi_xor_violation import (
    MultiXorMissedGenerator,
    MultiXorOverlapGenerator,
    MultiXorViolationInvariant,
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
from recon_gen.common.spine.xor_group_violation import (
    XorGroupMissedFiringGenerator,
    XorGroupOverlapGenerator,
    XorGroupViolationInvariant,
)

#: For every generator class, the invariant classes its emission trips.
#: Read as: "a single `emit()` from this generator + a refresh of the
#: matviews causes detect() on each listed invariant to include the
#: expected Violation."
#:
#: AU.0 finding (audit §5 "AU.0 result"): the OverdraftGenerator edge to
#: `DriftInvariant` is the empirical consequence of overlapping base-table
#: predicates between two independent matview SELECTs — not a structural
#: claim. An overdraft planted on a LEAF internal account satisfies
#: drift's matview filter (`parent_role IS NOT NULL` AND `stored ≠ Σ
#: legs`), so drift fires too. The registry records this; AU.2's
#: composition test holds it under multi-generator pressure.
#:
#: AX.5: the L2-shape generators (chain_parent_disagreement /
#: xor_group_violation / fan_in_disagreement / multi_xor_violation) all
#: register single-edge by default. Cross-class noise (e.g., an XOR
#: plant on a template that also parents a chain may also trip
#: multi_xor_violation) is tolerated per AS.5's "intended ⊆ detected"
#: contract — multi-edge entries land here only after per-invariant
#: empirical verification, mirroring the OverdraftGenerator pattern.
INVARIANT_GENERATOR_EDGES: Final[
    dict[type[ViolationGenerator], tuple[type[Invariant], ...]]
] = {
    DriftGenerator: (DriftInvariant, LedgerDriftInvariant),
    OverdraftGenerator: (OverdraftInvariant, DriftInvariant),
    # AU.3.a — same empirical-edge shape as OverdraftGenerator: a leaf
    # plant trips drift (zero transactions ⇒ Σ legs ≠ planted stored),
    # a parent plant doesn't (matview's parent_role IS NOT NULL filter).
    ExpectedEodBalanceGenerator: (ExpectedEodBalanceInvariant, DriftInvariant),
    # AU.3.b — single-edge: stuck_pending is transaction-based (no
    # balance row) AND Pending status (excluded from drift's computed
    # subledger balance which filters status='Posted').
    StuckPendingGenerator: (StuckPendingInvariant,),
    # AU.3.c — single-edge verified empirically: the bundle_id IS NULL
    # plant doesn't trip drift on this fresh DB.
    StuckUnbundledGenerator: (StuckUnbundledInvariant,),
    # AU.4 — single-edge: Posted transaction with no balance row ⇒
    # no drift JOIN match ⇒ no drift fire.
    LimitBreachGenerator: (LimitBreachInvariant,),
    # AX.1 — chain_parent_disagreement: transfers-only emit (no
    # balance rows) → single-edge to its own invariant.
    ChainParentDisagreementGenerator: (ChainParentDisagreementInvariant,),
    # AX.2 — xor_group_violation: two generators (missed + overlap)
    # both trip the single invariant. Per-test verified.
    XorGroupMissedFiringGenerator: (XorGroupViolationInvariant,),
    XorGroupOverlapGenerator: (XorGroupViolationInvariant,),
    # AX.3 — fan_in_disagreement: one generator (3 smart constructors;
    # parent_count knob). The 'healthy' shape's emit produces no row;
    # the registry edge represents the missing/extra/orphan cases.
    FanInChainGenerator: (FanInDisagreementInvariant,),
    # AX.4 — multi_xor_violation: two generators (missed + overlap)
    # both trip the single invariant.
    MultiXorMissedGenerator: (MultiXorViolationInvariant,),
    MultiXorOverlapGenerator: (MultiXorViolationInvariant,),
    # AT — L2 investigation invariants. Both are transfers-only emits
    # (LedgerSimulation pattern) → single-edge to their own invariant.
    AnomalyGenerator: (AnomalyInvariant,),
    MoneyTrailGenerator: (MoneyTrailInvariant,),
}


# ---------------------------------------------------------------------------
# AU.5 sources of truth — explicit lists of "what's promoted to the
# spine" for the exhaustiveness gate. Hand-maintained per promotion;
# new invariants/generators get appended here as they land. The gate
# test in `tests/unit/test_spine_au5_exhaustiveness.py` asserts these
# lists stay in sync with `INVARIANT_GENERATOR_EDGES`.
#
# Why hand-listed (vs auto-derived from common.spine.__all__):
# explicit > implicit — the list IS the contract that "I intended to
# promote this." A class that's defined but accidentally missing from
# here triggers the gate's failure, NOT a silent omission.
#
# AX.5 split: three categories, each with their own _INVARIANTS +
# _GENERATORS tuple; `ALL_INVARIANTS` + `ALL_GENERATORS` are the
# unified sequences for the AU.5 sweep.
# ---------------------------------------------------------------------------


ALL_L1_INVARIANTS: Final[tuple[type[Invariant], ...]] = (
    DriftInvariant,
    LedgerDriftInvariant,
    OverdraftInvariant,
    ExpectedEodBalanceInvariant,
    StuckPendingInvariant,
    StuckUnbundledInvariant,
    LimitBreachInvariant,
)
"""L1 accounting / audit-trail invariants — the regulator-facing
matview surface that the audit PDF covers."""


ALL_L2_SHAPE_INVARIANTS: Final[tuple[type[Invariant], ...]] = (
    ChainParentDisagreementInvariant,
    XorGroupViolationInvariant,
    FanInDisagreementInvariant,
    MultiXorViolationInvariant,
)
"""L2-shape integrity invariants (AX) — the ETL-side contracts that
the L2 yaml's declared chain/XOR/fan-in structure is honored at
data-emit time. Surface on the L2 Flow Tracing dashboard's Unified
L2 Exceptions matview."""


ALL_L2_INVESTIGATION_INVARIANTS: Final[tuple[type[Invariant], ...]] = (
    AnomalyInvariant,
    MoneyTrailInvariant,
)
"""L2 investigation invariants (AT) — fraud / AML pattern detection.
Surface on the Investigation dashboard (anomaly + money_trail
sheets)."""


ALL_INVARIANTS: Final[tuple[type[Invariant], ...]] = (
    *ALL_L1_INVARIANTS,
    *ALL_L2_SHAPE_INVARIANTS,
    *ALL_L2_INVESTIGATION_INVARIANTS,
)
"""Every `Invariant` class promoted to the spine, across all three
categories. AU.5's coverage gate asserts each is reached by ≥1
generator in `INVARIANT_GENERATOR_EDGES`."""


ALL_L1_GENERATORS: Final[tuple[type[ViolationGenerator], ...]] = (
    DriftGenerator,
    OverdraftGenerator,
    ExpectedEodBalanceGenerator,
    StuckPendingGenerator,
    StuckUnbundledGenerator,
    LimitBreachGenerator,
)
"""L1 accounting generators — match `ALL_L1_INVARIANTS`."""


ALL_L2_SHAPE_GENERATORS: Final[tuple[type[ViolationGenerator], ...]] = (
    ChainParentDisagreementGenerator,
    XorGroupMissedFiringGenerator,
    XorGroupOverlapGenerator,
    FanInChainGenerator,
    MultiXorMissedGenerator,
    MultiXorOverlapGenerator,
)
"""L2-shape integrity generators (AX) — 1 per single-variant
invariant + 2 each for the two-variant ones (XOR missed/overlap,
multi-XOR missed/overlap). 7 generators across 4 invariants."""


ALL_L2_INVESTIGATION_GENERATORS: Final[
    tuple[type[ViolationGenerator], ...]
] = (
    AnomalyGenerator,
    MoneyTrailGenerator,
)
"""L2 investigation generators (AT) — match
`ALL_L2_INVESTIGATION_INVARIANTS`."""


ALL_GENERATORS: Final[tuple[type[ViolationGenerator], ...]] = (
    *ALL_L1_GENERATORS,
    *ALL_L2_SHAPE_GENERATORS,
    *ALL_L2_INVESTIGATION_GENERATORS,
)
"""Every `ViolationGenerator` class promoted to the spine, across all
three categories. AU.5's registration gate asserts each is keyed in
`INVARIANT_GENERATOR_EDGES`."""


def invariants_for(
    generator_class: type[ViolationGenerator],
) -> tuple[type[Invariant], ...]:
    """The invariants a given generator's emission trips. Empty tuple
    when the generator class isn't registered (yet)."""
    return INVARIANT_GENERATOR_EDGES.get(generator_class, ())


def generators_for(
    invariant_class: type[Invariant],
) -> set[type[ViolationGenerator]]:
    """The generator classes whose emission trips this invariant.
    Reverse-lookup over the edge table."""
    return {
        gen_cls
        for gen_cls, inv_classes in INVARIANT_GENERATOR_EDGES.items()
        if invariant_class in inv_classes
    }


def iter_edges() -> Iterator[tuple[type[ViolationGenerator], type[Invariant]]]:
    """Every (generator_class, invariant_class) edge as a flat sequence.
    Convenient for parametrized property tests."""
    for gen_cls, inv_classes in INVARIANT_GENERATOR_EDGES.items():
        for inv_cls in inv_classes:
            yield (gen_cls, inv_cls)
