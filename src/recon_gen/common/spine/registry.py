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
  `ALL_L1_GENERATORS` has an entry in `INVARIANT_GENERATOR_EDGES`.
  Catches "I promoted a new generator but forgot to register it."
- **Per-invariant-class coverage** — every class in `ALL_L1_INVARIANTS`
  is reached by at least one generator's edge set. Catches "I promoted
  a new invariant but forgot to wire any generator to it."
- **Empirical-edge contract** — every registered edge actually fires
  on emission (existing per-invariant tests cover this; AU.5's
  parametrized test consolidates).

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
INVARIANT_GENERATOR_EDGES: Final[
    dict[type[ViolationGenerator], tuple[type[Invariant], ...]]
] = {
    DriftGenerator: (DriftInvariant, LedgerDriftInvariant),
    OverdraftGenerator: (OverdraftInvariant, DriftInvariant),
    # AU.3.a — same empirical-edge shape as OverdraftGenerator: a leaf
    # plant trips drift (zero transactions ⇒ Σ legs ≠ planted stored),
    # a parent plant doesn't (matview's parent_role IS NOT NULL filter).
    ExpectedEodBalanceGenerator: (ExpectedEodBalanceInvariant, DriftInvariant),
    # AU.3.b — predicted single-edge: stuck_pending is transaction-based
    # (no balance row) AND Pending status (excluded from drift's computed
    # subledger balance which filters status='Posted'). Empirical edge
    # verification in `test_spine_stuck_pending.py`.
    StuckPendingGenerator: (StuckPendingInvariant,),
    # AU.3.c — single-edge prediction: stuck_unbundled is Posted but
    # bundle_id IS NULL. The Posted status puts it on drift's radar (Σ
    # legs at posted_at counted), so the leaf-account variant MIGHT trip
    # drift if a balance row isn't planted to match. Empirical edge
    # verification in `test_spine_stuck_unbundled.py` resolves whether
    # this is single-edge OR the OverdraftGenerator-style two-edge entry.
    StuckUnbundledGenerator: (StuckUnbundledInvariant,),
    # AU.4 — single-edge prediction (per AP.3 finding #4's from_instance
    # framing). Posted transaction with no balance row ⇒ no drift JOIN
    # match ⇒ no drift fire. Empirical verification in
    # `test_spine_limit_breach.py`.
    LimitBreachGenerator: (LimitBreachInvariant,),
}


# ---------------------------------------------------------------------------
# AU.5 sources of truth — explicit lists of "what's promoted to the L1
# spine" for the exhaustiveness gate. Hand-maintained per promotion;
# new invariants/generators get appended here as they land. The gate
# test in `tests/unit/test_spine_au5_exhaustiveness.py` asserts these
# lists stay in sync with `INVARIANT_GENERATOR_EDGES`.
#
# Why hand-listed (vs auto-derived from common.spine.__all__):
# explicit > implicit — the list IS the contract that "I intended to
# promote this." A class that's defined but accidentally missing from
# here triggers the gate's failure, NOT a silent omission. AT.5's L2
# exhaustiveness will mirror this shape with `ALL_L2_INVARIANTS` +
# `ALL_L2_GENERATORS`.
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
"""Every L1 `Invariant` class promoted to the spine. AU.5's coverage
gate asserts each is reached by ≥1 generator in
`INVARIANT_GENERATOR_EDGES`."""


ALL_L1_GENERATORS: Final[tuple[type[ViolationGenerator], ...]] = (
    DriftGenerator,
    OverdraftGenerator,
    ExpectedEodBalanceGenerator,
    StuckPendingGenerator,
    StuckUnbundledGenerator,
    LimitBreachGenerator,
)
"""Every L1 `ViolationGenerator` class promoted to the spine. AU.5's
registration gate asserts each is keyed in
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
