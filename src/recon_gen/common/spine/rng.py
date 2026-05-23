"""Deterministic RNG factory for `ViolationGenerator` impls.

Every generator that makes a non-trivial choice — anomaly's 20 quiet
baseline pairs (which sender/recipient accounts, which amounts within
a quiet range), money_trail's chain of N hops (parent-linked transfer
IDs), AS.4's cross-account vector state (opening balances across many
accounts) — needs randomness. Naive `random.X()` calls would break
byte-identical replay across runs.

The convention this module locks in (matching the rest of the codebase
— `build_full_seed_sql(base_seed=…)`, `random.Random(seed=…)`):

  1. Each concrete `ViolationGenerator` that makes a random choice
     carries an `rng: random.Random` field on the dataclass.
  2. Each `Invariant.scenario_for(…)` accepts a `seed: int | None =
     None` kwarg; `None` resolves to `SCENARIO_BASE_SEED` (this
     module's default).
  3. The generator constructs its RNG via `scenario_rng(seed)` — never
     calls `random.Random()` directly.

The `tests/unit/test_typing_smells.py::determinism` AST lint already
rejects module-level `random.X()` calls in seed modules; this
convention extends it to spine generators by funneling every
construction site through one factory.

Composing multiple generators in one scenario: each gets its OWN rng,
seeded from a derived value so the order of generator construction
doesn't affect the rng stream a downstream generator sees. The
recommended derivation is `seed + i` where `i` is the generator's
position in the scenario. Locking the precedent here so AS.2 + AT
don't have to re-litigate.
"""

from __future__ import annotations

import random
from typing import Final

#: Default seed for generators when the caller doesn't pin one.
#: Hex-spelled "spine seed" — uncommitted to a specific calendar
#: date so swapping locked anchors doesn't drag this with it.
SCENARIO_BASE_SEED: Final[int] = 0x5_5e1d_5eed  # noqa: typing-smell-ignore


def scenario_rng(seed: int | None = None) -> random.Random:
    """Build a seeded `random.Random` for a `ViolationGenerator`.

    `seed=None` uses `SCENARIO_BASE_SEED` — every scenario_for call site
    that doesn't pin its own seed lands at the same starting point, so
    the locked-seed agreement (`tests/data/test_locked_seeds.py` shape)
    keeps holding.
    """
    return random.Random(SCENARIO_BASE_SEED if seed is None else seed)
