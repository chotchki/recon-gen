# Money Moved

> **What this sheet teaches.** Dollar volume flowing across the bank's transfer rails, separated by direction (net inflow vs outflow) and aggregated by transfer type, so you can see where the operational handle is concentrated and whether the flow is balanced.

## What you're looking at

The sheet opens on a strip of two KPIs across the top — *Net Money Moved* (Σ signed amount, with an up/down indicator glyph showing direction) and *Gross Money Moved* (Σ absolute values of all transfers). Below sits a full-width daily stacked bar chart titled *Daily Gross Dollars Moved by Type*, where each bar is one business day and the stack bands show which transfer rail ([`rail_name`](../_glossary.md#rail)) contributed to that day's total. A second full-width chart below that, *Period Total Gross Dollars by Type*, rolls up the same data into a single snapshot per rail — the "where does the volume come from" summary for the whole window. Date-range filters across the top (same picker as the other executive sheets) let you narrow by posting date.

## How to read the numbers

Both visuals bind to the `exec-transaction-summary` dataset, which reads from the `<prefix>_transactions` base table. The dataset pre-aggregates per `transfer_id` (so multi-leg transfers are counted once, not once per leg), then rolls up by `posted_date` and `rail_name`.

The dataset schema and column definitions:

- `posted_date` — the business day the transfer posted, extracted from the `posting` timestamp column in the base table
- `rail_name` — the transfer's declared family (ACH, wire, check, merchant card, on-us internal transfer, etc.). Note: due to executive visibility constraints and legend size, any `rail_name` outside the top 20 by gross-dollar volume is collapsed into a string literal `"Other"`
- `transfer_count` — count of distinct `transfer_id` values with `status = 'Posted'` on that (date, rail) tuple
- `gross_amount` — sum of per-transfer handle: for each transfer, the `MAX(ABS(amount_money))` (magnitude regardless of direction) across all legs, then summed across all transfers in the bucket, converted to dollars
- `net_amount` — sum of per-transfer net flow: for each transfer, the `SUM(amount_money)` (signed sum of legs), then summed across all transfers in the bucket, converted to dollars

The *Net Money Moved* KPI is `SUM(net_amount)` over the date window — the algebraic net of all movement. The *Gross Money Moved* KPI is `SUM(gross_amount)` — the total handle regardless of direction. Note: the dataset filters to `status = 'Posted'` only, so Pending and Failed legs are excluded (they don't represent settled money moved).

## Common patterns

### Net near zero, gross large

*Net Money Moved* is close to $0 while *Gross Money Moved* is a large number. This is the healthy steady state on a balanced book — every customer-to-external transfer is offset by the matching external-to-customer leg, so the internal-only flows net out and only externally-cleared asymmetries surface. Compare the daily stacked bar pattern to your institution's seasonal activity rhythm. No action needed unless the pattern deviates sharply from expectations.

### Net significantly positive (large inflow)

*Net Money Moved* shows a large positive value (the green ▲ glyph appears next to the number). This means deposits or inbound transfers from external counterparties exceeded outbound transfers in the window. Confirm this aligns with your month's funding narrative — expected seasonal deposit surge, or an unusual promotion / rate move. If the window was supposed to be balanced and it isn't, check whether a bulk transfer or reversal landed late in the period.

### Net significantly negative (large outflow)

*Net Money Moved* shows a large negative value (the red ▼ glyph appears next to the number). Outbound transfers exceeded inbound on the period. Verify this against your expected payout schedule (payroll, vendor payments, etc.). If the magnitude is surprising, drill into the daily stacked bar to identify which day or rail drove the outlier.

### One rail dominates gross volume

The *Period Total Gross Dollars by Type* chart shows one rail's bar much taller than the others (the Y-axis is log-scaled specifically to keep the long tail visible). This is usually correct — one rail typically handles the bulk of executive-period volume. Compare against the daily stacked-bar pattern: if the period total came from a single spike day rather than steady activity, investigate that day's posting batches. If the pattern is consistent across many days, it reflects the institution's ordinary architecture.

### Rail composition shifted across the window

The daily stacked bar shows different proportions on different days — one rail dominated early in the window, a different rail later on. This can signal a feed migration, a batch-job cadence change, or a shift in customer behavior (e.g., seasonal move from ACH to wires for larger transfers). Cross-reference against your infrastructure change log. If unplanned, work backward through the daily view to Account Reconciliation's Transaction Volume sheet (an operational sibling) to see the transfer-count trend on the same rails.

## What "no rows" means

A blank sheet means zero Posted transactions fell within the date window — all pending, all failed, or the window excludes all posting dates. Confirm:

- **Check the date range.** A very narrow window can show zero if no transfers posted on those days. Widen to at least 7 days; if you still see zero, check App Info to confirm the `<prefix>_transactions` table has rows at all.
- **Confirm the matview is fresh.** Cross to App Info and check the *Matview Status* table's `<prefix>_transactions` row. If `last_refresh_at` is more than a few minutes old AND new postings landed since, the data may be stale. The institution refreshes matviews on every ETL load; ad-hoc dashboard hits don't trigger one.
- **Check status filter.** The dataset filters to `status = 'Posted'` only. If you have transfers in the window but they're all Pending or Failed, this sheet will be empty — check your ETL flow or upstream feed.

## Cross-sheet drills

No cross-sheet drill actions are wired on this sheet. Use the date-range filters and the visual composition to identify trends, then navigate to Account Reconciliation's Transaction Volume or Account Coverage sheets for detailed investigation.

## Related handbook pages

- [Transaction Volume Over Time](../executives/transaction-volume.md) — the companion sheet showing transfer count (not dollars) by rail and date; use it to understand transfer velocity when gross dollars spike.
- [Account Coverage](../executives/account-coverage.md) — the deposit-base and GL snapshot; use it to triangulate whether the net flow makes sense given which accounts are active.
- [Program Health](../executives/program-health.md) — the L1 invariant health tripwire; if your net flow is unexpected, check Program Health to rule out a ledger integrity issue upstream.

---

*First time here? See the [Vocabulary](../_glossary.md) for `rail`, `transfer`, and the other project-specific terms.*
