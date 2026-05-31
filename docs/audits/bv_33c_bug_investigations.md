# BV.3.3.c — bug investigation notes (2026-05-31)

Live probing of the 3 plant-doesn't-surface bugs from BV.3.3.c's
12/15-pass full-registry walk. Each was probed against a fresh
qsgen_sqlite Studio after Session Start.

## bug1 — `ledger_drift` plant doesn't surface in v_ledger_drift matview

**Root cause:** `_invoke_ledger_drift_plant` is a thin alias for
`_invoke_drift_plant`. The DriftPlant inserts ONE transaction on a
leaf account, which trips the `drift` matview (leaf's `stored` !=
`computed_subledger`). But the `ledger_drift` matview tracks PARENT
accounts where `stored` != `computed_ledger` (which sums children +
parent's own postings). The plant doesn't touch the parent's stored
balance directly; depending on whether the seed's parent stored
ALREADY matched the pre-plant child sum, the plant may or may not
nudge the parent's stored ≠ Σchildren.

In the test it consistently doesn't fire — the matview stays at 0
post-plant.

**Fix shape:** Either
1. Author a `LedgerDriftPlant` that explicitly perturbs a parent's
   stored balance independently of any child plant. Add to seed.py +
   wire into `_invoke_ledger_drift_plant`. **Heaviest fix.**
2. Drop the `ledger_drift` registry entry as redundant with `drift`
   (the comment on line 444 acknowledges they're "the same
   invariant" at different roles — the trainer doesn't need two
   checkboxes that share a plant). **Cheapest fix.** Operator UX
   improves (one less duplicate card); BV.3.3.c walks 14 instead
   of 15 kinds; matview-shape coverage stays since `drift` already
   covers the leaf side.

Recommend option 2 unless you actually want to teach
`computed_ledger_balance` discrepancies (in which case option 1's
new plant is the right shape).

## bug2 — `stuck_unbundled` plant lands but isn't rendered

**Findings from probe:** When `stuck_unbundled` is the ONLY enabled
plant post-Session-Start, `v_stuck_unbundled` is EMPTY — the plant
didn't add anything the matview can see.

In the test it surfaced a signature (so the matview DID have a row
at that point), but that was test position #6 in the cumulative
walk — meaning some earlier plant created preconditions
`stuck_unbundled` needed to fire.

The matview's filter:
- Posted leg with `bundle_id IS NULL`
- `age >= rail.max_unbundled_age_seconds`

The `StuckUnbundledPlant` from `auto_scenario.py` is supposed to
emit a Posted leg with no bundle. If the seed already has bundling
behavior that swallows the planted leg (the L2 declares an
`AggregatingRail` that picks up `CustomerFeeAccrual`), the plant's
leg may get bundled.

**Fix shape:** Investigate whether the plant emits a leg with a
DIFFERENT (un-bundled) shape, OR whether the seed's bundler runs
across plant rows too. Read `add_stuck_unbundled_plant` (or its
emitter equivalent) + walk through the L2's `bundles_activity`
chain on `CustomerFeeAccrual`.

## bug3 — `expected_eod_balance_breach` plant lands but isn't rendered

**Findings from probe:** Similar shape to bug2. Planted on
`acct-eod-ACHOrigSettlement` on day -1; matview probably has the
row (test surfaced the signature), but dashboard render shows 0.

The `expected_eod_balance_breach` matview filters by
`expected_eod_balance IS NOT NULL` on the daily_balances row. The
planted row's `expected_eod_balance` may not be set if the plant
only writes `stored` and lets `expected_eod_balance` default to
NULL.

**Fix shape:** Read `ExpectedEodBalanceBreachPlant` adapter +
verify it sets both `stored_balance` AND `expected_eod_balance`
with a divergent pair. If only one is set, the JOIN against the
expected_eod column returns NULL → matview filter excludes it.

## bug4 — chain-coherence signature false-positive concern

**Findings:** 7 chain-coherence kinds (chain_parent_disagreement,
xor_group_*, fan_in_*, multi_xor_*) all matched on the same
`"2026-05-30 00:00:00"` signature element. That's just the anchor
date appearing in the rendered HTML — could be a true match (the
planted row's date IS rendered) OR a false positive (the date
appears for unrelated rows).

**Fix shape:** Strengthen `_v_matview_signatures` to require at
least one NON-date value in the picked-columns list. OR tighten
the surface assertion to require BOTH a date AND an id from the
sig appear in the HTML (more sensitive to false positives).

The cheap test to disambiguate: probe the rendered HTML for one of
the picked transfer_ids (e.g. `tt-xor-missed-...`); if it's there,
the chain-coherence pass is real. If only the date matches, we
need to fix the signature.

## Recommended landing order

1. **bug4 (false-positive strict-signature)** — purely test-side,
   prevents shipping silent-pass; can be done without DB work.
2. **bug1 option 2 (drop ledger_drift)** — small registry edit;
   removes a confusing duplicate.
3. **bug2 + bug3 (plant SPEC investigation)** — each needs SPEC
   reading + plant emitter triage; ~1-2 hr each.

Post-fix, BV.3.3.c should hit 13-14/14 on sqlite (BV.3.3.d/e the
dialect parity work).
