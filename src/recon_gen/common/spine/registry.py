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

What this does NOT enforce yet (AS.2.x follow-on):
- Exhaustiveness across all PlantKind values (we'd need to promote
  every L1 invariant first; AS.2 lands drift + ledger_drift, the rest
  follow incrementally).
- View edges (`invariant → {Views}`); AT lands those when L2's
  Investigation surface comes online.
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
}


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
