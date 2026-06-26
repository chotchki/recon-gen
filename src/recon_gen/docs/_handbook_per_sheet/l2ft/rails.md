# Rails — Transactions Explorer

> **What this sheet teaches.** The L2 ([Flow Tracing](../_glossary.md#l2-flow-tracing--per-chain-transfer-integrity)) dashboard's Rails sheet is a transactions explorer — a ledger dump where you look up individual transfer legs by date, rail, status or embedded metadata. Every row is one posting; the sheet's filters push down into the SQL so you narrow to the exact slice you need.

## What you're looking at

A KPI row at the top orients you to the data window: *Legs in Window* (count of postings matching all current filters) and *Largest Leg* (the single biggest `amount_money` in that window). Below sit six filter controls: two date pickers (*Date From* / *Date To*), three categorical dropdowns (*Rail*, *Status*, *Bundle*) and a cascading metadata pair (*Metadata Key* / *Metadata Value*). The *Transactions* table below renders one row per leg, showing posting date, rail name, transfer ID, account, amount, direction (debit/credit), status, bundle status and parent transfer ID.

The metadata cascade lets you narrow by embedded leg properties: pick a *Metadata Key* (e.g., `customer_id`), then type the *Metadata Value* you want to filter by. With no key picked, every leg in the date window appears; with a key selected, only legs whose metadata carries that key=value pair show. This is the same cascade the Chains and Transfer Templates sheets use.

## How to read the numbers

The *Transactions* table reads from the `<prefix>_current_transactions` matview ([materialized view](../_glossary.md#matview--materialized-view)), parameterized on metadata key/value and date range. The KPIs and table filters all push down into the dataset SQL via parameter substitution so the ledger narrows at query time.

- `posting` — the timestamp the leg was recorded. Sortable by column header.
- `rail_name` — the declared [rail](../_glossary.md#rail) family this leg belongs to (ACH, wire, check, internal transfer, etc.). The *Rail* dropdown defaults to showing all declared rails; pick one to narrow the table.
- `transfer_id` — the logical event identifier linking all legs of one multi-leg transfer. Non-failed transfers' legs net to zero by construction.
- `account_name` — the account being debited or credited. Internal/external scope is in the `account_scope` column (not displayed, but filters upstream).
- `amount_money` — the signed posting amount in dollars (positive = money INTO the account, negative = OUT). Single column, not split debit/credit; the sign convention follows the source `amount_money` field on the base transactions table. The *Largest Leg* KPI picks the maximum `amount_money` in the current window.
- `amount_direction` — debit (−, money out of the account) or credit (+, money into the account) from that account's perspective.
- `status` — the transaction's state: *Pending* (not yet settled), *Posted* (completed) or *Other* (any terminal state like Failed, Rejected, Cancelled). The *Status* dropdown defaults to all three; pick one to narrow.
- `bundle_status` — derived from whether `bundle_id` is NULL. *Bundled* means the leg was collected into a batch for settlement; *Unbundled* means it's still individual. Calculated as `CASE WHEN bundle_id IS NULL THEN 'Unbundled' ELSE 'Bundled' END`.
- `transfer_parent_id` — if this leg is a child of a declared [chain](../_glossary.md#chain), this holds the parent's `transfer_id`. Useful for tracing up to the initiating transfer when you're deep in a multi-leg chain.

The *Legs in Window* KPI counts all rows passing the six filters. Note: this is a **windowed** count for the current date range only; the Executives dashboard's "Total Transactions" KPI and the App Info matview row count show all-history rows. The gap between this number and those is legs outside your date picker's window.

## Common patterns

### High volume on a single rail in a short window

The *Transactions* table shows hundreds of rows, all with the same `rail_name`, clustered on a single day or range. This is normal during batch posting windows — ACH originates hundreds of debit legs across customer DIAs in parallel. If the volume is unexpected, drill into the metadata: pick the rail + a specific `customer_id` or batch identifier to see whether one upstream system is over-firing or whether the load is distributed as expected.

### Legs with `status='Other'` that you don't recognize

The status column collapses the open-set of possible raw statuses (Failed, Rejected, Cancelled, etc.) into three buckets (Pending, Posted, Other). If you see a status that should have its own meaning — say, "Recall" is a distinct operational state on your wire rail — raise it with the L2 instance owner. The status mapping in `build_postings_dataset` is a constant, not data-driven, so adding a new first-class status requires a redeployment.

### Unbundled legs hanging around past their rail's aggregation window

Pick a rail from the *Rail* dropdown, then filter *Bundle* to *Unbundled*. Sort the table by *posting* date and look for Posted legs that are far older than a few minutes. The institution's aggregator should bundle these within its configured `max_unbundled_age` window; if older ones are showing as unbundled, either the aggregator is behind or the bundle-completion signal never fired. Cross-check the App Info sheet's *Matview Status* row to confirm the *current_transactions* view is fresh — if it's stale, the unbundled status may have been remedied since the last refresh but not yet visible.

### Looking for a specific transfer's legs

Paste the `transfer_id` into the table's search / filter row (most renderers support column-level text search). If the transfer belongs to a declared chain, the *transfer_parent_id* column will show you the initiating parent's ID.

## What "no rows" means

An empty *Transactions* table can happen for two reasons:

- **The filters are too narrow.** If you've picked a specific rail, status, metadata key/value or date range, try clearing some of them. Start by widening the date window to the trailing 7 days or "all time"; if you still see zero rows with no other constraints, the system is clean for that day range.
- **The matview is stale or the SQL hasn't run.** Cross to the *App Info* sheet and check the *Matview Status* table's `<prefix>_current_transactions` row. The `latest_date` column there is `MAX(posting)` for that matview; if it's null or older than your most recent ETL load, the matview hasn't refreshed. The institution refreshes matviews on every ETL load; ad-hoc dashboard hits do not trigger a refresh.
- **The metadata cascade is confusing you.** If you've picked a *Metadata Key* but entering a *Metadata Value* in the text field returns no rows, it means no legs in the current date window have that key with a value matching what you typed. Clear the key filter and start fresh.

If *App Info* shows `latest_date` as null across the board, the L2FT pipeline didn't run — that's an ops alert, not a "system is clean" signal.

## Cross-sheet drills

The *Transactions* table on this sheet does not have row-level right-click drills. Navigation to other sheets happens via the filter controls: pick a *Rail* and cross to the Chains sheet to see which parent transfers are firing that rail as a child, or navigate to Transfer Templates to see multi-leg flow.

If you arrived at this Rails sheet from a right-click on the L2 Exceptions sheet's table (via "View in Rails"), the source row's entity_a (a rail name) is pre-populated in the *Rail* filter, narrowing the table to legs on that rail.

## Related handbook pages

- [Chains — Per-Instance Explorer](../l2ft/chains.md) — the per-parent-firing chain view; use it when you want to see whether declared chain children actually fired.
- [Transfer Templates — Multi-Leg Flow](../l2ft/transfer-templates.md) — visualizes shared transfers' multi-leg topology; the Sankey shows the debit → template → credit flow shape.
- [L2 Exceptions](../l2ft/l2-exceptions.md) — the unified six-check hygiene violations view; right-click drills from there land on this sheet pre-filtered by rail name.
- [App Info](../l2ft/app-info.md) — matview freshness + row-count diagnostics; use it to verify the *current_transactions* table is up to date.

---

*First time here? See the [Vocabulary](../_glossary.md) for `rail`, `chain`, `matview`, `transfer` and other project-specific terms.*
