# AY.0 — Convergence design lock: `ScenarioPlant` → spine generators

**Status:** AY.0 complete (spike + design lock) — 2026-05-23.
**Branch:** `ay-converge-seed-paths`.
**Output of:** the AY.0 inventory + design pass per PLAN.md Phase AY.

---

## TL;DR

The production seed (`recon-gen data apply --execute`) currently
emits via the OLD `ScenarioPlant` + `_emit_<plant>_rows` chain in
`common/l2/seed.py`. The spine generators (`src/recon_gen/common/
spine/`) run only in tests + AT.5's e2e gate. AV.5's per-row
`metadata.scenario_id` tagging therefore never lands on production
seed rows.

**AY.0 lock — adapter path:** introduce a new module
`src/recon_gen/common/spine/plant_adapter.py` that holds the
`ScenarioPlant` → `tuple[ViolationGenerator, ...]` mapping logic.
`emit_full_seed` + `build_full_seed_sql` delegate to
`ScenarioContext.compose(*adapter(scenario, instance))` for the
violation-plant emit; non-violation seed-color plants continue
through the existing path until they have a use case for the spine.

**Why a dedicated adapter module:** the spine doesn't import
`common/l2/seed.py` (would invert the layering: spine = currency
substrate, seed = consumer). `seed.py` doesn't import the spine
(seed is the legacy path, spine is the convergence target).
A new neutral adapter module imports both sides, holds the
field-mapping logic in one place, and gets dropped along with the
OLD plant dataclasses when AZ retires the byte-locked seeds. No new
coupling at either edge.

**Adapter coverage** — 13 violation plant kinds (6 L1 + 7 L2-shape)
map cleanly to existing spine generators. 7 non-violation
"seed-color" plant kinds stay outside the spine entirely; they
remain `ScenarioPlant` metadata carriers (trainer + audit PDF +
dashboard preseed consumers read them directly).

---

## Plant kind inventory

The 20 `ScenarioPlant` tuple fields split cleanly:

### Violation plants → spine generators (13 kinds, adapter covers)

| Plant kind | Spine generator | Notes |
|---|---|---|
| `DriftPlant` | `DriftGenerator` | AS.2 |
| `OverdraftPlant` | `OverdraftGenerator` | AU.1 |
| `LimitBreachPlant` | `LimitBreachGenerator(direction='Outbound')` | AU.4 |
| `InboundCapBreachPlant` | `LimitBreachGenerator(direction='Inbound')` | AU.4 supports both directions; no new generator needed |
| `StuckPendingPlant` | `StuckPendingGenerator` | AU.3.b |
| `StuckUnbundledPlant` | `StuckUnbundledGenerator` | AU.3.c |
| `ChainParentDisagreementPlant` | `ChainParentDisagreementGenerator` | AX.1 |
| `XorVariantMissedFiringPlant` | `XorGroupMissedFiringGenerator` | AX.2 |
| `XorVariantOverlapPlant` | `XorGroupOverlapGenerator` | AX.2 |
| `FanInChainMissingParentPlant` | `FanInChainGenerator(parent_count<expected)` | AX.3 |
| `FanInChainExtraParentPlant` | `FanInChainGenerator(parent_count>expected)` | AX.3 |
| `MultiXorMissedPlant` | `MultiXorMissedGenerator` | AX.4 |
| `MultiXorOverlapPlant` | `MultiXorOverlapGenerator` | AX.4 |

### Seed-color plants → stay outside spine (7 kinds)

| Plant kind | Consumer | Rationale |
|---|---|---|
| `TwoTemplateChainPlant` | L1 dashboard + audit PDF | Healthy cardinality (parent_count=1); the AB.2.3 matview's `> 1` HAVING filter excludes it. No violation = no invariant = no spine path. |
| `FanInChainPlant` | L1 dashboard + audit PDF | Healthy fan-in (parent_count=expected); the AB.4.7 matview's CASE expression produces no row. Symmetric with `FanInChainGenerator(expected_kind='healthy')` AX.3 already covers, but the OLD path's "healthy" plant carries different account-context fields. |
| `SupersessionPlant` | Audit PDF only (M.2b.12) | No L1 matview; audit PDF queries `_transactions` directly for `supersedes IS NOT NULL`. No spine `Invariant`. |
| `FailedTransactionPlant` | L2FT Postings dataset's "Other" status dropdown (X.1.g) | One row with `status='Failed'`. Not a SHOULD violation — a valid terminal state the dashboard must categorize. |
| `TransferTemplatePlant` | L2FT tt-instances + tt-legs datasets | Healthy Transfer firings tagged with `template_name`. Exercises L2FT visualization, not invariant detection. |
| `RailFiringPlant` | L2FT broad-mode dashboard fill (M.4.2) | Posted legs across every declared rail. Non-violation; ensures unviolated rails surface on the L2FT dropdowns. |
| `InvFanoutPlant` | Investigation matviews' raw input | N senders → 1 recipient seed for the anomaly + money_trail matviews. The AT.1/AT.3 spine generators emit their own scenario plants for detection; `InvFanoutPlant` is background data those detectors operate on. |

These 7 kinds survive on the OLD `_emit_<plant>_rows` path. The
adapter doesn't touch them; they continue contributing rows to the
DB via the existing dispatch loops in `emit_seed`. Trainer +
dashboard consumers (`plants_per_node`, `compute_plant_timeline`,
L2FT datasets, audit PDF) read them as before.

**Why we don't promote these** — promotion requires an `Invariant`
to be the receiving end of the contract; for seed-color plants there
IS no invariant (intentionally). Promoting them would mean inventing
a no-op `Invariant` whose `detect()` always returns the empty set,
which is busywork — `claimed_accounts` doesn't help (no scenario
composition collides with a non-violation plant) and `metadata.scenario_id`
tagging is what AV.5 wanted for cleanup attribution. The
non-violation rows can still get the AV.5 tag via the OLD path's
emit (the per-plant `_emit_*_rows` helpers all take a `metadata`
kwarg post-AV; just thread it through the adapter's call site).

---

## Adapter module shape (`common/spine/plant_adapter.py`)

```python
"""ScenarioPlant ⋈ spine generators adapter (AY.0 lock).

The neutral middle layer between common/l2/seed.py's `ScenarioPlant`
and common/spine/'s `ViolationGenerator` classes. The spine doesn't
import seed.py; seed.py doesn't import spine. The adapter imports
both and holds the field-mapping logic. Gets dropped alongside the
OLD plant dataclasses when AZ retires the byte-locked seeds.
"""

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.seed import ScenarioPlant  # the only seed.py read
from recon_gen.common.spine import (
    ChainParentDisagreementGenerator, DriftGenerator,
    ExpectedEodBalanceGenerator, FanInChainGenerator,
    LimitBreachGenerator, MoneyTrailGenerator,
    MultiXorMissedGenerator, MultiXorOverlapGenerator,
    OverdraftGenerator, StuckPendingGenerator,
    StuckUnbundledGenerator, ViolationGenerator,
    XorGroupMissedFiringGenerator, XorGroupOverlapGenerator,
)


def scenario_to_generators(
    scenario: ScenarioPlant,
    instance: L2Instance,
) -> tuple[ViolationGenerator, ...]:
    """Convert violation plants to spine generators.

    Non-violation plants (TwoTemplateChain / FanInChain healthy /
    Supersession / FailedTransaction / TransferTemplate /
    RailFiring / InvFanoutPlant) are intentionally ignored — they
    have no `Invariant` counterpart on the spine and continue
    through the OLD emit path until AZ retires it.

    Per-plant field mapping rationale (deferred per-call detail to
    docstrings on each helper below): the OLD plant dataclasses
    carry construction context (account_id strings, days_ago offsets,
    magnitudes) that the spine's `scenario_for()` smart constructors
    auto-derive from the L2. The adapter unifies — for plants that
    carry the SAME fields the spine accepts, it constructs the
    generator directly with the plant's pre-resolved values; for
    plants whose construction model differs (DriftPlant carries
    child_account_id explicitly; DriftGenerator's scenario_for
    derives it from a role), the adapter resolves the L2 to fill
    the spine's expected shape.
    """
    gens: list[ViolationGenerator] = []
    for p in scenario.drift_plants:
        gens.append(_drift_from_plant(p, instance, anchor=scenario.today))
    for p in scenario.overdraft_plants:
        gens.append(_overdraft_from_plant(p, instance, anchor=scenario.today))
    # ... etc, 13 kinds total ...
    return tuple(gens)


def _drift_from_plant(p, instance, *, anchor):
    """DriftPlant carries (child_account_id, child_role, days_ago,
    magnitude). DriftGenerator carries (child_account_id, child_role,
    parent_role, parent_account_id, parent_account_role, anchor_day,
    magnitude, rng, leg_amount). Adapter resolves the parent fields
    from the L2's role topology + computes anchor_day from days_ago."""
    # ... per-plant logic ...
```

**Where it gets called:**

```python
# common/l2/seed.py::emit_seed(...) [REFACTORED in AY.4]
from recon_gen.common.spine import ScenarioContext
from recon_gen.common.spine.plant_adapter import scenario_to_generators

def emit_seed(instance, scenario, *, prefix, dialect, scenario_id=None):
    # AY.4 — generator-based emit for violation plants.
    generators = scenario_to_generators(scenario, instance)
    ctx = ScenarioContext(
        scenario_id=scenario_id or "auto-data-apply",
        prefix=prefix,
        dialect=dialect,
    )
    # The compose call handles emit + commit + cleanup attribution.
    # But: compose() needs a live conn; emit_seed returns SQL string.
    # See "open challenge" below.

    # Non-violation plants continue through the OLD dispatch loops
    # (the _emit_<plant>_rows helpers stay until AZ).
    non_violation_sql = _emit_non_violation_plants(
        instance, scenario, prefix=prefix, dialect=dialect,
    )
    return non_violation_sql  # + violation_sql via adapter
```

**Open challenge — SQL string vs live connection:**
`emit_seed` returns a SQL STRING today (`recon-gen data apply` emits
+ inspects the SQL before applying via `--execute`). `ScenarioContext.
compose(conn, ...)` writes directly to a live connection. AY.4 must
resolve this.

Two options:
- **(a)** Add a `dry_run=True` mode to `ScenarioContext.compose` that
  collects the SQL strings instead of executing. Same compose-time
  checks fire; the rows + matview-refresh land as a script.
- **(b)** Switch `recon-gen data apply` to a live-emit path (no
  intermediate string). The `--execute=False` (dry-run) mode runs the
  emit against an in-memory SQLite for inspection.

Recommended: **(a)** — preserves the operator's existing inspect-
before-apply workflow. The adapter's compose-time checks (collision
detection, cross-scenario interference) still fire on the dry-run
path; the script that gets written + applied is identical to the
old shape.

AY.3's `Dialect.SQLITE` lift is the prerequisite — `apply_scenario`
needs to refresh matviews per-dialect for the dry-run script to
work on PG / Oracle.

---

## Sequencing within AY (locked from AY.0)

1. **AY.1 (precondition gate)** — equivalence test:
   `tests/unit/test_spine_scenario_plant_equivalence.py` parametrizes
   over every (violation plant kind, spine generator) pair and
   asserts that emitting both paths against a fresh SQLite produces
   the SAME matview row set. Catches "the adapter's field mapping
   silently differs from the OLD path's emit" before AY.4's reroute
   fires.

2. **AY.2 (no spine work needed)** — `InboundCapBreachGenerator`
   already exists (via `LimitBreachGenerator(direction='Inbound')`);
   `SupersessionGenerator` deferred (no matview, no invariant);
   broad-mode plants (RailFiring / TransferTemplate / InvFanout)
   stay seed-color per the inventory above. AY.2 reduces to a
   no-op verification commit confirming the adapter coverage is
   complete.

3. **AY.3** — `apply_scenario`'s `Dialect.SQLITE` hardcode lifted.
   Thread `dialect: Dialect = Dialect.SQLITE` kwarg through; pass to
   `refresh_matviews_sql`. Generators already accept any dbapi
   connection post-AT.5.b.

4. **AY.4** — `plant_adapter.scenario_to_generators` lands.
   `emit_seed` adds a `dry_run=True` ScenarioContext.compose mode
   that collects SQL strings. `build_full_seed_sql` calls
   `scenario_to_generators(scenario, instance)` + composes via
   ScenarioContext for the violation plants; non-violation plants
   continue through the OLD `_emit_*_rows` dispatch loops.

5. **AY.5** — re-lock byte seeds (`tests/data/_locked_seeds/*.sql`).
   Spine emit should produce byte-stable SQL post-refactor; AY.1's
   equivalence gate ensures the row sets match, but the SQL FORMATTING
   (column order, NULL serialization) may drift. Document the diff in
   the commit if any.

6. **AY.6** — retire the OLD per-plant emitter functions for the 13
   converged plant kinds. The 7 seed-color emitters (
   `_emit_two_template_chain_rows`,
   `_emit_fan_in_chain_plant_rows` for healthy,
   `_emit_supersession_rows`, `_emit_failed_transaction_rows`,
   `_emit_transfer_template_rows`, `_emit_rail_firing_rows`,
   `_emit_inv_fanout_rows`) survive — they're the seed-color path
   the adapter ignores.

7. **AY.7** — trainer + Studio dogfood smoke: `recon-gen studio`
   loads; per-node badges populate; plant timeline renders. The
   trainer reads `ScenarioPlant` for visualization and is read-only
   wrt emit, so AY's refactor should be transparent.

8. **AY.8** — bump v11.15.0 + release notes + merge + push.

---

## Verification plan for AY.0

This audit doc IS the AY.0 verification. Subsequent leaves (AY.1+)
each carry their own verification (per-leaf test additions + the
byte-locked seed gate as the safety net). AY.0 doesn't introduce
new code; it locks the convergence shape that AY.1-8 implement.

**Test the design holds:**
- The 13 plant-kind → generator mapping is exhaustive (every
  violation plant has a spine equivalent post-AX) — confirmed by
  the inventory table above.
- The 7 seed-color plant kinds have no spine equivalent — confirmed
  by the inventory column "Spine path" in the explorer report.
- `ScenarioPlant` survives unchanged as a trainer / dashboard data
  carrier — confirmed by `plants_per_node` + `compute_plant_timeline`
  being read-only consumers.
- The adapter doesn't introduce a layering cycle — confirmed by the
  module-import topology: `plant_adapter.py` imports both
  `seed.ScenarioPlant` and `spine.<Generators>`; neither side
  imports the adapter.
