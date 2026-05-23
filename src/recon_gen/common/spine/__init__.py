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

from recon_gen.common.spine.generator import ViolationGenerator
from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.violation import Violation

__all__ = ["Violation", "Invariant", "ViolationGenerator"]
