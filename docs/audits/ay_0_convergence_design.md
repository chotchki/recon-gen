# AY.0 — Convergence design lock: `ScenarioPlant` → spine generators

**Status:** AY.0 complete (spike + design lock, revised) —
2026-05-23.
**Branch:** `ay-converge-seed-paths`.
**Output of:** the AY.0 inventory + design pass per PLAN.md Phase AY.

## Revision history

- **Initial draft (committed `c708902d`):** locked a hybrid path where
  7 "seed-color" plants stayed on the OLD `_emit_<plant>_rows`
  dispatch loops while 13 violation plants converged to the spine.
- **User feedback:** "your locks 1+2 seem to be deviating away from
  what we're trying to accomplish here ... we took Violation too
  strictly ... it should be more general but I support layering
  violations if it helps."
- **This revision:** `Violation` is the typed evidence currency for
  the spine — not specifically L1-rule-violations. ALL 20 plant
  kinds become spine `ViolationGenerator`s. A `severity` discriminator
  on `Violation` separates rule-violations from coverage observations
  + audit fixtures. The OLD dual-system architecture retires
  entirely.

---

## TL;DR

The production seed (`recon-gen data apply --execute`) currently
emits via the OLD `ScenarioPlant` + `_emit_<plant>_rows` chain in
`common/l2/seed.py`. The spine generators (`src/recon_gen/common/
spine/`) run only in tests + AT.5's e2e gate. AV.5's per-row
`metadata.scenario_id` tagging therefore never lands on production
seed rows.

**AY.0 lock — full convergence:**

1. **Generalize `Violation`.** Add a `severity: Literal['rule_violation',
   'coverage', 'audit_fixture'] = 'rule_violation'` field. The default
   value preserves backward compat for all 14 existing spine generators.
   `Violation` is re-documented as "typed evidence the spine
   emits/detects" — not just "rule violation".

2. **All 20 plants land on the spine.** Each gets a `ViolationGenerator`
   class; each generator's `intended` Violation carries the right
   severity. No `ScenarioPlant`-only "seed-color" path survives.

3. **`emit_full_seed` / `build_full_seed_sql` route ENTIRELY through
   `ScenarioContext.compose(*adapter(scenario, instance))`.** All 20
   `_emit_<plant>_rows` helpers retire in AY.6. Trainer +
   timeline (read-only consumers of `ScenarioPlant`) keep working
   — `ScenarioPlant` stays as a description/visualization carrier
   even after emit moves entirely to the spine.

---

## Generalized `Violation` shape

```python
@dataclass(frozen=True)
class Violation:
    """Typed evidence the spine emits or detects.

    Severity discriminator separates:
      - 'rule_violation' — L1/L2 matview detects a rule break (drift,
        chain_parent_disagreement, etc.). The default; preserves the
        AS.1–AX shape.
      - 'coverage' — seed presence claim (RailFiring, TransferTemplate,
        InvFanout, etc.). Coverage observations are the GOOD signal
        — their ABSENCE is the bug ("seed didn't emit the expected
        rail firings" trips a coverage-invariant).
      - 'audit_fixture' — auxiliary data the audit PDF consumes
        (SupersessionPlant). Not a rule violation; not a coverage
        invariant; just an audit-PDF-specific row presence claim.
    """

    invariant: str
    identity: frozenset[tuple[str, object]]
    severity: Literal[
        "rule_violation", "coverage", "audit_fixture",
    ] = "rule_violation"
```

**Backward compat** — `severity` defaults to `'rule_violation'`.
Every existing call site (`Violation.of(name, **identity)`) emits
the same shape it always did; the AS.5 semantic_lock pinning, the
AU.5 exhaustiveness gate, the AT.5 agreement test all keep working
byte-stable.

**Layering option flagged + declined for now.** Per the user's
"I support layering violations if it helps" — the layering shape
would be `Violation` base + `L1Violation` / `CoverageObservation` /
`AuditFixture` subtypes. A discriminator field is simpler + the type
narrowing is straightforward (`if v.severity == 'rule_violation':
...`). Promote to subtypes later if the type-system precision pays
off; the discriminator pattern is the reversible default.

---

## All 20 plant kinds on the spine

### Rule-violation plants → existing spine generators (13 kinds)

These are already promoted post-AS / AT / AU / AX. Adapter wires
them through unchanged; each generator's `intended` defaults to
`severity='rule_violation'`.

| Plant kind | Spine generator | Promoted in |
|---|---|---|
| `DriftPlant` | `DriftGenerator` | AS.2 |
| `OverdraftPlant` | `OverdraftGenerator` | AU.1 |
| `LimitBreachPlant` | `LimitBreachGenerator(direction='Outbound')` | AU.4 |
| `InboundCapBreachPlant` | `LimitBreachGenerator(direction='Inbound')` | AU.4 |
| `StuckPendingPlant` | `StuckPendingGenerator` | AU.3.b |
| `StuckUnbundledPlant` | `StuckUnbundledGenerator` | AU.3.c |
| `ChainParentDisagreementPlant` | `ChainParentDisagreementGenerator` | AX.1 |
| `XorVariantMissedFiringPlant` | `XorGroupMissedFiringGenerator` | AX.2 |
| `XorVariantOverlapPlant` | `XorGroupOverlapGenerator` | AX.2 |
| `FanInChainMissingParentPlant` | `FanInChainGenerator(parent_count<expected)` | AX.3 |
| `FanInChainExtraParentPlant` | `FanInChainGenerator(parent_count>expected)` | AX.3 |
| `MultiXorMissedPlant` | `MultiXorMissedGenerator` | AX.4 |
| `MultiXorOverlapPlant` | `MultiXorOverlapGenerator` | AX.4 |

### Coverage plants → NEW spine generators in AY.2 (5 kinds)

These ship as new `Generator` classes in `src/recon_gen/common/spine/`.
Each emits the row shape the OLD `_emit_<plant>_rows` helper does;
each `intended` returns a `Violation` with `severity='coverage'`.

| Plant kind | NEW spine generator | Severity |
|---|---|---|
| `TwoTemplateChainPlant` | `TwoTemplateChainGenerator` | `coverage` |
| `FanInChainPlant` (healthy) | already covered by `FanInChainGenerator(expected_kind='healthy')`; AY.2 just confirms `intended` returns a `severity='coverage'` Violation (was `None` post-AX.3 — change to a coverage-tagged Violation) | `coverage` |
| `TransferTemplatePlant` | `TransferTemplateGenerator` | `coverage` |
| `RailFiringPlant` | `RailFiringGenerator` | `coverage` |
| `InvFanoutPlant` | `InvFanoutGenerator` | `coverage` |

### Audit-fixture plants → NEW spine generators in AY.2 (2 kinds)

These emit rows the audit PDF consumes but no matview or coverage
detector reads. They get spine generators for substrate uniformity
(claimed_accounts collision check, AV.5 metadata tagging,
ScenarioContext cleanup attribution) but their `intended` Violation
carries `severity='audit_fixture'`.

| Plant kind | NEW spine generator | Severity |
|---|---|---|
| `SupersessionPlant` | `SupersessionGenerator` | `audit_fixture` |
| `FailedTransactionPlant` | `FailedTransactionGenerator` | `audit_fixture` |

### Coverage invariants — deferred per-plant

Each coverage generator's `intended` Violation can be "detected" by
a matching `Invariant` that reads back the row shape (e.g.,
`RailFiringInvariant.detect()` queries `current_transactions GROUP
BY rail_name`). AY.2 promotes the generators; the matching
detectors can land later if a real use case (regression gate on
"is the demo data complete?") materializes. Until then, coverage
generators emit Violations that nothing reads back — same shape as
`FanInChainGenerator(healthy).intended` returns `None` today.
Acceptable per the existing precedent.

`audit_fixture` Violations explicitly never need detectors —
they're audit-PDF input markers, not matview-detected observations.

---

## Adapter module shape (`common/spine/plant_adapter.py`)

```python
"""ScenarioPlant ⋈ spine generators adapter (AY.0 lock; revised).

The neutral middle layer between common/l2/seed.py's `ScenarioPlant`
and common/spine/'s `ViolationGenerator` classes. The spine doesn't
import seed.py; seed.py doesn't import spine. The adapter imports
both and holds the field-mapping logic. Gets dropped alongside the
OLD plant dataclasses when AZ retires the byte-locked seeds (or
earlier — once ScenarioPlant becomes a thin builder over the spine
generators, the adapter is trivially `[g.from_plant(p) for p in
plants]`).
"""

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.seed import ScenarioPlant
from recon_gen.common.spine import (
    ChainParentDisagreementGenerator, DriftGenerator,
    FanInChainGenerator, FailedTransactionGenerator,
    InvFanoutGenerator, LimitBreachGenerator,
    MultiXorMissedGenerator, MultiXorOverlapGenerator,
    OverdraftGenerator, RailFiringGenerator,
    StuckPendingGenerator, StuckUnbundledGenerator,
    SupersessionGenerator, TransferTemplateGenerator,
    TwoTemplateChainGenerator, ViolationGenerator,
    XorGroupMissedFiringGenerator, XorGroupOverlapGenerator,
)


def scenario_to_generators(
    scenario: ScenarioPlant,
    instance: L2Instance,
) -> tuple[ViolationGenerator, ...]:
    """Convert ALL 20 plant kinds to spine generators.

    Per the AY.0 generalization (severity discriminator on Violation):
    rule-violation, coverage, AND audit-fixture plants all go through
    the spine. No plant kind survives on the OLD `_emit_<plant>_rows`
    path.
    """
    # ... 20 per-plant adapter functions ...
```

---

## Sequencing within AY (locked from AY.0)

1. **AY.1 (precondition gate)** — equivalence test:
   `tests/unit/test_spine_scenario_plant_equivalence.py` parametrizes
   over every (plant kind, spine generator) pair (all 20) and
   asserts that emitting both paths against a fresh SQLite produces
   the same matview row set + same `<prefix>_transactions` /
   `<prefix>_daily_balances` contents. Catches adapter field-mapping
   drift before AY.4's reroute fires.

2. **AY.2** — Promote 7 NEW spine generators for the coverage +
   audit-fixture plant kinds (TwoTemplateChainGenerator,
   TransferTemplateGenerator, RailFiringGenerator, InvFanoutGenerator,
   SupersessionGenerator, FailedTransactionGenerator). Each ships
   with a `claimed_accounts` property + `severity` discriminator on
   its `intended`. `FanInChainGenerator(healthy)`'s `intended`
   flips from `None` to a `severity='coverage'` Violation. Plus add
   `severity` field to `Violation` (backward-compatible default).

3. **AY.3** — `apply_scenario`'s `Dialect.SQLITE` hardcode lifted.
   Thread `dialect: Dialect = Dialect.SQLITE` kwarg through; pass
   to `refresh_matviews_sql`. Generators already accept any dbapi
   connection post-AT.5.b.

4. **AY.4** — `plant_adapter.scenario_to_generators` lands. New
   `dry_run=True` mode on `ScenarioContext.compose` collects SQL
   strings instead of writing live. `build_full_seed_sql` calls
   `scenario_to_generators(scenario, instance)` + composes via
   ScenarioContext for ALL plants (no OLD-path branch).

5. **AY.5** — re-lock byte seeds. Spine emit should produce
   byte-stable SQL post-refactor; AY.1's equivalence gate ensures
   row sets match. SQL formatting drift (column order, NULL
   serialization) gets documented in the commit if any.

6. **AY.6** — retire ALL 20 OLD per-plant emitter functions. The
   `_emit_<plant>_rows` helpers all go. `emit_seed` /
   `emit_full_seed` keep their signature but the body becomes a
   thin wrapper over `scenario_to_generators` +
   `ScenarioContext.compose(dry_run=True)`.

7. **AY.7** — trainer + Studio dogfood smoke. `recon-gen studio`
   loads; per-node badges populate; plant timeline renders. Trainer
   reads `ScenarioPlant` for visualization + is read-only wrt emit;
   AY's refactor should be transparent.

8. **AY.8** — bump v11.15.0 + release notes + merge + push.

---

## Why this scope (not the smaller hybrid)

The user's pushback on the v1 lock: keeping 7 plants on the OLD path
is "deviating away from what we're trying to accomplish." The whole
point of AY is to end the dual-system architecture; a half-converged
result that says "13 plants on spine, 7 on OLD path" leaves the
post-AV retrospective gap open with different labels.

The cost of full convergence is concentrated in AY.2 (7 new
generator classes). Each is small (the OLD per-plant emitter
functions are 30-150 LOC each — the spine generator equivalents
follow the AX.1-4 template + are similarly sized). The benefit
compounds: ScenarioContext composition safety + per-row scenario
tagging + cleanup attribution all work uniformly across every plant
kind; AY.6 retires every `_emit_<plant>_rows` helper instead of
half of them; AZ's semantic-lock retirement doesn't carry forward
the dual-system asymmetry into the lock file shape.

---

## Open question for AY.2 implementation

Coverage / audit-fixture generators need to handle the same
construction-context fields the OLD plant emitters do (account
context derived from `_first_template_instance_or_skip(scenarios)`).
Some options for where this resolution lives:

- **(a)** Constructor takes the resolved context as kwargs
  (`TransferTemplateGenerator(template_instance: TemplateInstance,
  ...)`). Adapter resolves the context.
- **(b)** Smart constructor `scenario_for(instance, scenario)` does
  the L2 + scenario resolution. Adapter just calls it.
- **(c)** Both. Constructor is explicit; smart constructor sugar.

Defer the per-generator choice to AY.2; AX.1-4 used `scenario_for`
without a `scenario` arg because the picker resolved from the L2
alone. For coverage plants, the `_first_template_instance_or_skip`
pattern needs a `scenario` arg (the template instance is
materialized once per scenario, not per L2). Lean: option (b) with
the scenario arg added.

---

## Verification plan for AY.0 (revised)

This audit doc IS the AY.0 verification. Subsequent leaves carry
their own:
- AY.1 — equivalence gate for all 20 plant kinds (the safety
  precondition for AY.4)
- AY.2 — per-new-generator unit tests + the AU.5 exhaustiveness
  gate parametrize expansion (registry grows from 14 → 21
  generators; ALL_INVARIANTS may grow by 5-7 coverage invariants if
  any land)
- AY.5 — byte-lock byte-stability gate
- AY.7 — Studio dogfood + trainer regression
