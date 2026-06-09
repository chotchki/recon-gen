# Unbundled Aging

> **What this sheet teaches.** Posted transactions stuck in a null `bundle_id` state past their rail's configured `max_unbundled_age` cap — a violation of the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) constraint that every Posted leg waiting for aggregation must eventually be picked up and grouped into a bundle.

## What you're looking at

The sheet opens on a pair of KPIs across the top — *Stuck Unbundled* (count of posted legs without a bundle) and *Stuck Unbundled Exposure* (the total dollar amount of those legs). Below sits a bar chart titled *Stuck Unbundled by Age Bucket* that groups the stuck legs across four age bands (1: <1d, 2: 1-2d, 3: 2-7d, 4: >7d) and stacks them by rail so you can see which payment rail is generating the backlog. A detail table lists every stuck unbundled leg with the account, transfer, rail, amount, posting timestamp, and live age in seconds. Filters across the top let you narrow by account, transfer type, and rail.

## How to read the numbers

The table reads from the L1 invariant [matview](../_glossary.md#matview--materialized-view) `<prefix>_stuck_unbundled`, which pulls every Posted transaction where `bundle_id IS NULL` and the age (in seconds since posting) exceeds the per-rail `max_unbundled_age_seconds` cap. The matview filters `WHERE ct.status = 'Posted'` (distinct from Pending Aging, which surfaces `status = 'Pending'` violations) because AggregatingRails bundle only posted legs — a Pending leg waiting to post isn't "stuck unbundled," it's just "stuck pending."

The columns are:

- `account_id`, `account_name`, `account_role` — the account that received or sent the leg; the [account_role](../_glossary.md#account-role) classifies what kind of account it is (CustomerSubledger, etc.)
- `account_parent_role` — the parent account's role, if this account is a child
- `transfer_id` — the transfer this leg belongs to
- `rail_name` — the [rail](../_glossary.md#rail) (ACH, wire, check, etc.) that fired this leg
- `amount_money` — leg amount in dollars (signed: positive in, negative out)
- `amount_direction` — Debit or Credit visual label
- `posting` — the timestamp when the leg was marked Posted
- `max_unbundled_age_seconds` — the cap the rail declared (inlined at matview-emit time from the L2 instance's per-rail configuration)
- `age_seconds` — current live age in seconds, recomputed on every query refresh so you can sort by staleness without re-opening the sheet
- `stuck_unbundled_aging_bucket` — four-band age classification (1: <1d, 2: 1-2d, 3: 2-7d, 4: >7d) used by the bar chart

The *Stuck Unbundled* KPI counts distinct `transaction_id` values in the filtered set. The *Stuck Unbundled Exposure* KPI sums `amount_money` across all rows — the dollar magnitude that the bundler hasn't yet rolled into a bundle. The bar chart groups by `stuck_unbundled_aging_bucket` and stacks by `rail_name` so you can spot which rail's bundler is lagging behind (rightmost bands = older legs).

## Common patterns

### All stuck legs in 1: <1d bucket, one rail

The bar chart shows most rows clustering in the "1: <1d" band for a single rail. This is typically **normal bundler cadence** — the rail's bundler hasn't yet fired, or just fired a moment ago. If the bars jump to the right edge (4: >7d) the next time you refresh, the bundler caught up. The *Stuck Unbundled Exposure* KPI will spike briefly then drop as the bundler closes the legs. No action needed unless the same rail's buckets stay >7d on refresh.

### Same rail, >7d bucket, persistent across refreshes

Legs in the "4: >7d" band that don't clear on the next refresh signal **bundler failure for that rail**. The aggregator isn't picking up these posted legs at all, or is picking them up but the bundle assignment isn't firing. The typical cause is a bundler process that crashed or a configuration mismatch where the rail's `max_unbundled_age` cap is shorter than the bundler's actual cadence. Cross-check the L2 instance's rail definitions (especially the AggregatingRail that's supposed to bundle this rail) with the actual bundler's interval in your deployment logs. Right-click any row → *View Transactions* to confirm all legs of that transfer are stuck, or if some have been bundled already.

### Many rails, multiple buckets mixed

The bar chart shows legs scattered across multiple rails in multiple age buckets with no clear clustering. This is usually a **system-wide bundler catch-up period** — postings came faster than the bundler could process them, but the system is clearing the backlog across multiple rails in parallel. The *Stuck Unbundled Exposure* KPI matters here: if it's thousands of dollars, operators may need to pace postings or increase bundler parallelism. If it's a few hundred dollars, it's noise in the normal cadence.

### Zero rows

An empty unbundled sheet means every posted leg has either been bundled (assigned a `bundle_id`) or hasn't yet aged past its rail's `max_unbundled_age` cap. This is the steady-state expectation. If you see zero rows:

- **Confirm the matview is fresh.** Cross to *App Info* and check the *Matview Status* table's `stuck_unbundled` row. If `last_refresh_at` is more than a few minutes old AND new postings landed since, the matview may be stale. The institution refreshes matviews on every ETL load; ad-hoc dashboard hits don't trigger a refresh.
- **Check the date filter and drill state.** If you drilled here from another sheet, the account or rail filter may be too narrow. Widen to "All Accounts" and "All Rails" to see the universe.
- **Don't celebrate yet.** Unbundled aging is one of twelve L1 invariants. A clean Unbundled Aging sheet next to a populated *Pending Aging* or *Overdraft* sheet means *that* invariant has work — `?` those sheets next.

If *App Info* shows `last_refresh_at` as null or the matview row count as zero across the board, the L1 invariant pipeline didn't run. That's an ops alert, not a "clean" signal.

## Cross-sheet drills

- **Stuck Unbundled Detail table row → Transactions** (right-click → *View Transactions for this transfer*). Shows every leg of that transfer (both bundled and unbundled) so you can see the full transfer topology and confirm the bundler's scope. Widens the universal date filter to "all time" on drill so the target transfer is in scope, since Unbundled Aging is a current-state view with no date picker.

## Related handbook pages

- [Pending Aging](pending-aging.md) — the sibling constraint for Pending legs waiting to post; same structure, different `status` filter.
- [Drift](drift.md) — the foundational balance-agreement check; aged-pending and aged-unbundled legs contribute to drift if they post but aren't accounted for.
- [Daily Statement](daily-statement.md) — per-account narrative where you can see this transfer's postings in context.
- [Transactions](transactions.md) — the raw posting ledger; the drill target from this sheet.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`, `account_role`, `rail`, and the other project-specific terms.*
