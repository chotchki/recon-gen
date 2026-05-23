"""`Invariant` Protocol — the rule + detector.

An `Invariant` owns its own `name` (matching the production matview
suffix) and a `detect()` method that reads that matview's output and
projects each row to a `Violation`. The detection LOGIC stays in the
matview SQL (`common/l2/schema.py`); `detect()` is a thin read of the
already-computed rows, not a re-encoded copy of the detector.

What is NOT in the Protocol (intentionally):

- `scenario_for(...)` — the smart constructor that resolves a shape
  selector to a `ViolationGenerator`. Each concrete invariant defines
  its own with the right kwargs (drift takes `role`; limit_breach takes
  `(role, rail, direction)`; anomaly takes window + spike-magnitude).
  AS.2 promotes the concrete invariants with their per-shape
  `scenario_for` signatures.

That keeps the Protocol minimal — every L1 + L2 invariant can implement
it without a kwargs-mismatch dance. The smart constructor varies by
shape; the spine link (`detect`) does not.

Promoted from `tests/unit/test_as0_drift_full_spine.py` by AS.1.
"""

from __future__ import annotations

import sqlite3
from typing import ClassVar, Protocol, runtime_checkable

from recon_gen.common.spine.violation import Violation


@runtime_checkable
class Invariant(Protocol):
    """A rule + detector. Concrete invariants implement this via:

    1. A `name` `ClassVar[str]` matching the production matview suffix —
       ``"drift"`` reads from ``<prefix>_drift``, etc. ClassVar here so
       concrete impls (frozen dataclasses) declare ``name: ClassVar[str]
       = "..."`` without ever shadowing it as an instance attribute. The
       Protocol-variance dance pyright cares about: ClassVar on both
       sides keeps the read-only contract honest.
    2. A `detect(conn)` method returning the breaches currently in the
       data, as a `set[Violation]`.

    `runtime_checkable` so `isinstance(x, Invariant)` works for the
    taxonomy bookkeeping in AS.2 — the `invariant → {generators, views}`
    map needs runtime lookup, not just static type checking.
    """

    name: ClassVar[str]

    def detect(self, conn: sqlite3.Connection) -> set[Violation]: ...
