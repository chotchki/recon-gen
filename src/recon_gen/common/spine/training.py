"""Training/docs scenarios that self-validate.

The audit's "training scenarios become declarative (and can't lie)"
payoff (`docs/audits/date_range_model_audit.md`): today, docs prose
describes what a demo scenario shows, but the prose ⟷ data link is
developer-memory. If the seed changes shape and the planted drift
moves off the documented day, the docs silently lie. AS.7 closes that
gap.

`TrainingScenario` is the typed object docs can import + the test
suite can validate:

- `name` / `description` — author-facing prose; renderers (mkdocs,
  walkthroughs) read these.
- `emitters` — the `ViolationGenerator` / `LedgerSimulation` rows
  the scenario plants.
- `invariants` — the detectors the scenario claims will fire.
- `intended` — the specific `Violation`s docs say the analyst will
  see. The claim that has to hold.

`self_validate(conn)` emits the scenario, refreshes matviews, runs
detect across each invariant, and asserts `intended ⊆ detected`. If
the docs claim violations the data doesn't produce, the test fires
loud — the documented scenario can't silently fail to demonstrate.

What this REPLACES (eventually): hand-written prose in
`docs/handbook/` that lives parallel to the seed; the docs walk
ASSUMED to match the data. With `TrainingScenario`, the docs
mkdocs-macros can render `scenario.description` next to a
mkdocs-side `scenario.intended` summary, and a CI test runs
`scenario.self_validate(conn)` against the live seed every build.

AT.6 reuses this mechanism for L2's anomaly + money_trail scenarios
(parallel structure, different surface).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.semantic_lock import _Emitter, apply_scenario, semantic_lock
from recon_gen.common.spine.violation import Violation


@dataclass(frozen=True)
class TrainingScenario:
    """A docs-renderable, self-validating scenario.

    Authors construct one of these per documented case; docs read
    `name` + `description` (free prose); the test suite calls
    `self_validate(conn)` to make sure the data the scenario emits
    actually produces the violations the prose claims.

    `intended` is the load-bearing field — it's the claim. A
    `Violation` in this set says: "the analyst will see this row in
    the matview after the scenario applies." Implementation churn
    (different leg shapes, account IDs) is fine as long as the
    intended Violations still fire; that's the same flexibility AS.5's
    `semantic_lock` gives.
    """

    name: str
    description: str
    emitters: tuple[_Emitter, ...]
    invariants: tuple[Invariant, ...]
    intended: frozenset[Violation] = field(default_factory=frozenset[Violation])
    prefix: str = "spec_example"
    instance_path: Path | None = None

    def self_validate(self, conn: sqlite3.Connection) -> None:
        """Apply the scenario; assert every intended Violation fires.

        Raises `AssertionError` with the missing-Violation diff if the
        docs claim violations the data doesn't produce. ``intended ⊆
        detected`` is the contract — extra detected Violations (e.g.,
        the secondary ledger_drift edge from a drift plant) are fine;
        missing claimed Violations are the failure mode.
        """
        apply_scenario(
            conn, *self.emitters,
            prefix=self.prefix, instance_path=self.instance_path,
        )
        lock = semantic_lock(conn, self.invariants)
        detected: set[Violation] = set()
        for v_set in lock.values():
            detected |= v_set
        missing = self.intended - detected
        if missing:
            raise AssertionError(
                f"TrainingScenario {self.name!r} claims violations that "
                f"don't fire:\n"
                f"  missing: {sorted(missing, key=repr)}\n"
                f"  detected: {sorted(detected, key=repr)}"
            )


def validate_all(
    scenarios: Iterable[TrainingScenario],
    conn_factory: "ConnFactory",
) -> None:
    """Validate a batch of scenarios, each against its OWN fresh DB.

    `conn_factory` is a no-arg callable returning a fresh `sqlite3.
    Connection` with the schema already applied (the in-process
    harness pattern). Each scenario gets its own connection so prior
    emissions don't bleed into the next test's detect set.

    Useful in a docs-build hook: collect every registered
    `TrainingScenario` and validate them all in one shot before
    rendering the prose. A failure halts the build with the missing-
    violation diff.
    """
    for scenario in scenarios:
        conn = conn_factory()
        try:
            scenario.self_validate(conn)
        finally:
            conn.close()


# A `Callable[[], sqlite3.Connection]` alias kept module-local for the
# `validate_all` signature; pyright resolves it without a TypeAlias
# import dance.
from typing import Callable

ConnFactory = Callable[[], sqlite3.Connection]
