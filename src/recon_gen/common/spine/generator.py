"""`ViolationGenerator` Protocol — the producer.

A `ViolationGenerator` claims to manufacture a specific `Violation` by
emitting base-table rows. Producer ≠ thing-produced: the generator is
not the violation; it CAUSES one. The spine link is

    Invariant[T].detect(ViolationGenerator[T].emit())  ⊇  {intended}

— pinned in-process by `tests/unit/test_ap3_invariant_self_validation.py`
and threaded end-to-end through the spine for drift by
`tests/unit/test_as0_drift_full_spine.py`.

The Protocol is minimal: `intended` (the Violation the generator claims
to cause) + `emit(conn)` (writing the rows). Concrete generators
specialize freely:

- Single-shot row emitters for trivial cases (the AP.3 spike's first
  pass — adequate for ad-hoc tests).
- Stateful folds (the AP.2 shape: `State -> (flows, State')` over days,
  carrying the running balance forward; AS.3 lands the base class for
  this).
- Cross-account vector folds (AS.4 — same Protocol, state generalizes
  from scalar balance to `dict[account_id, balance]`).

Promoted from `tests/unit/test_as0_drift_full_spine.py` by AS.1.
"""

from __future__ import annotations

import sqlite3
from typing import Protocol, runtime_checkable

from recon_gen.common.spine.violation import Violation


@runtime_checkable
class ViolationGenerator(Protocol):
    """A producer of base-table rows intended to manifest `intended`.

    `intended` is a `@property` (not a bare attribute) so concrete
    generators can derive it from their construction params — e.g., a
    `DriftGenerator` whose intended Violation includes its anchor day
    and resolved account_id, computed at access time.

    `emit(conn)` writes the rows. Generators MAY also commit or refresh
    matviews internally, but most leave that to the caller so a single
    scenario can compose multiple generators against ONE connection and
    refresh once at the end (the AP.3 `_assert_self_validates` pattern).
    """

    @property
    def intended(self) -> Violation: ...

    def emit(self, conn: sqlite3.Connection) -> None: ...
