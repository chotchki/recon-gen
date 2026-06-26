# L2 Hygiene Exceptions

> **What this sheet teaches.** The six L2 hygiene checks unified into one operator view. Each row is a declared rail, chain, metadata key, or limit schedule that doesn't match the live runtime data — a "declaration vs reality" mismatch that breaks L2 integrity without breaking L1 ledger integrity.

## What you're looking at

The sheet opens on a compact header row: a KPI showing *Distinct Exception Types Open* (how many of the six check kinds have at least one violation today) sits left; a bar chart breaks down *L2 Violations by Check Type* on the right so you see which kind dominates. Below sit a detail table, *L2 Violation Detail*, sorted by violation count descending so the worst offenders surface first. Each row names the check type plus the entity involved (`entity_a` / `entity_b` / `detail` columns vary by check kind — read them in the context of the `check_type` label). Filters for date range sit at the sheet top, though the L2 exceptions checks run against all-time runtime data by design.

## How to read the numbers

All six L2 checks are inlined into a single [matview](../_glossary.md#matview--materialized-view) UNIONing branches via `build_unified_l2_exceptions_dataset` in `datasets.py`. Each branch scans the `<prefix>_current_transactions` table against the L2 ([layer two](../_glossary.md#l2-flow-tracing--per-chain-transfer-integrity)) instance's declared rails, chains, templates, metadata keys and limit schedules. The result is one row per violation. The `check_type` column discriminates the six:

- **Chain Orphans** — a declared Required [chain](../_glossary.md#chain) whose parent [rail](../_glossary.md#rail) fired in the window but no matched child firing followed. `entity_a` = parent rail name, `entity_b` = child rail name, `count` = number of orphaned parent firings.
- **Unmatched Rail Name** — a posted transaction whose `rail_name` doesn't match any declared `Rail.name`. `entity_a` = the undeclared rail name, `entity_b` = NULL, `count` = posting count against that rail.
- **Dead Rails** — a declared rail with zero postings in the window. `entity_a` = rail name, `entity_b` = NULL, `detail` = the rail's `leg_shape` (SingleLeg / TwoLeg), `count` = always 1 (one dead declaration).
- **Dead Bundles Activity** — an `(aggregating_rail, bundle_target)` pair declared via `Rail.bundles_activity` with no matching postings. `entity_a` = aggregating rail, `entity_b` = bundle target rail, `count` = always 1.
- **Dead Metadata Declarations** — a `(rail, metadata_key)` pair declared via `Rail.metadata_keys` that no posting carries a non-null value for. `entity_a` = rail name, `entity_b` = metadata key, `count` = always 1.
- **Dead Limit Schedules** — a `(parent_role, rail_name)` cell declared via a LimitSchedule with zero outbound debit flow. `entity_a` = parent role, `entity_b` = rail name, `detail` = the cap, `count` = always 1.

The *Distinct Exception Types Open* KPI counts how many of the six check kinds have at least one open violation. The *L2 Violations by Check Type* bar chart shows the total violation count per kind. **Note:** the KPI counts TYPES (max 6), while the bar chart and detail table count OCCURRENCES PER TYPE — two different units, both correct. The detail table sorts by `count` DESC so large counts surface first.

## Common patterns

### All six check types clean (empty sheet)

Zero rows = every L2 declaration matches the runtime data. This is the healthy state — the integrator's ETL is firing all the declared rails, chains, metadata and limits they promised.

### One type dominates; others quiet

The bar chart shows *Unmatched Rail Name* with 50 violations but the others have 0–2 each. A single type spiking usually means a narrow ETL or declaration problem — the integrator added a new rail and forgot to update the L2 YAML, or the casing of a `rail_name` drifted between systems.

Drill from the *L2 Violations by Check Type* bar to the relevant sheet (Rails for Dead Metadata / Dead Rails, Chains for Chain Orphans) to see the full firing history.

### Chain Orphans with rising count

Parent rail fires consistently; child doesn't. The `orphan_count` column (called `count` in the unified table) climbs every day. This is a **causality break** — the L2 declared "child always fires after parent" but the runtime says the child is missing. Check the ETL: is the parent-to-child trigger wired? Does the child rail have a filter that's suppressing some parent-child pairs?

Right-click the *Chain Orphans* row and select *View in Chains (filter parent_chain_name to entity_a)* to see all parent firings and which ones lack matching children.

### Dead Rails that persist day after day

A declared rail never fires; the entry stays in the L2 YAML. Either the rail is genuinely unused (retire it from the declaration) or the ETL is broken and the operator needs to fix the routing. The `leg_shape` column (SingleLeg / TwoLeg) helps the integrator remember what this rail was supposed to do.

### Dead Metadata on a rail that otherwise fires

`entity_a` = a rail that DOES carry postings (so it's not in the Dead Rails check), but `entity_b` = a metadata key the L2 declared for that rail that none of the postings carry. This is a **data-collection miss** — the ETL should be writing the key into `transactions.metadata` but isn't. The Rail and Metadata Value dropdowns on the Rails sheet cascade off live metadata; dead declarations show empty option lists to operators.

## What "no rows" means

An empty L2 Exceptions sheet means every declared rail, chain, metadata key and limit schedule matches what the runtime is ACTUALLY doing. This is the steady-state expectation, not an edge case — L2 hygiene is a declaration-vs-reality sanity check, not a metric to trend.

If you see zero rows:

- **Confirm the matview is fresh.** Cross to *App Info* and check the *Matview Status* table — `view_name` / `row_count` / `latest_date` side by side. The L2 hygiene checks scan `<prefix>_current_transactions`, refreshed on every ETL load; a matview is STALE when its `latest_date` lags the base tables' `latest_date` (the base tables moved forward but the matview wasn't refreshed since the last load), so the sheet may be clean as of that lagging data-date but not reflecting today's postings. A NULL `latest_date` means a matview with no date dimension, not staleness.
- **Check that the L2 instance has declarations.** If the L2 YAML is empty (no rails, no chains, no limits), every check produces zero rows trivially — there's nothing to violate. This is valid but uninformative; cross to *Getting Started* to confirm the L2 instance loaded.
- **Don't celebrate yet.** L2 hygiene is one layer; L1 integrity (drift, overdraft, limit breach) is the ledger's health. An empty L2 Exceptions sheet next to violations on the *Drift* or *Overdraft* sheet (if exposed) means the declaration is sound but the ledger has problems — `?` those sheets next.

## Cross-sheet drills

- **L2 Violation Detail table row → Rails sheet** (right-click → *View in Rails (filter rail_name to entity_a)*). Filters the Rails *Transactions* table to postings on the named rail (works for Dead Rails, Dead Metadata, Dead Bundles; returns empty for the two check types whose `entity_a` isn't actually a rail name).
- **L2 Violation Detail table row → Chains sheet** (right-click → *View in Chains (filter parent_chain_name to entity_a)*). Filters the Chains *Chain Instances* table to firings of the named parent chain (works for Chain Orphans; returns empty for the other five check types).

## Related handbook pages

- [Rails](rails.md) — the transactions explorer; destination of the right-click drill from any Dead Rails / Dead Metadata row.
- [Chains](chains.md) — the per-instance explorer; destination of the drill from any Chain Orphans row.
- [L2 Flow Tracing](getting-started.md) — overview of the L2 instance declarations and what each dashboard tab covers.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L2`, `chain`, `rail`, `matview` and other project-specific terms.*
