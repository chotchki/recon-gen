# Transaction Volume Over Time

> **What this sheet teaches.** Daily transaction throughput and the composition of volume by transfer type (rail). Monitor this sheet to detect sudden drops in activity, shifts in which rails carry the load, or anomalous patterns that suggest upstream feed problems.

## What you're looking at

The sheet opens on a strip of three KPIs across the top — *Total Transactions (Posted, per-transfer)* (distinct transfers in the selected window), *Transfer Legs (all statuses)* (raw transaction-leg count including failed / pending legs), and *Average Daily Volume* (the mean daily transfer count across active business days). Below the strip sit two bar charts: *Daily Transaction Count by Type* (a vertical stacked bar showing each day's volume coloured by `rail_name`), and *Period Total by Type* (the cumulative volume per rail across the selected window, rendered on a log scale). Filters across the top let you narrow by date range (universal across the Executives app). An informational note explains the demo data's apparent weekend gaps.

## How to read the numbers

All visuals read from a custom SQL dataset — `exec-transaction-summary` — that aggregates the raw `<prefix>_transactions` base table. The dataset filters to `status = 'Posted'` (settled legs only, excluding Pending and Failed), and de-duplicates by `transfer_id` first so multi-leg transfers (e.g. a debit + credit pair, or an ACH batch with individual credits) count as a single transfer, not once per leg.

The columns are:

- `posted_date` — the calendar date (derived from each transaction's `posting` timestamp) when the legs settled
- `rail_name` — the transfer type or family (ACH, wire, check, on-us internal, etc.). The dataset rolls all rails ranked outside the top 20 by gross volume into a single `"Other"` bucket to keep the legend readable on executive dashboards with 60+ declared rails.
- `transfer_count` — the distinct count of `transfer_id` values for that (date, rail) pair, after filtering to Posted status
- `gross_amount` — the total dollar handle per (date, rail): `SUM(ABS(amount))` across all Posted legs
- `net_amount` — the net flow per (date, rail): `SUM(amount)` (signed) across all Posted legs; this is typically zero for balanced multi-leg transfers and non-zero for single-leg transfers or unbalanced flows

The three headline KPIs derive from this data:

- *Total Transactions (Posted, per-transfer)* — `SUM(transfer_count)` across the entire selected date window. This is the business metric leadership watches month-over-month.
- *Transfer Legs (all statuses)* — a separate, raw count of every row in `<prefix>_transactions` (all status values, all legs) as of the end-of-day refresh. This value matches the *App Info* sheet's `<prefix>_transactions` row count exactly. The sidebar note explains why this number differs from Total Transactions: the transaction summary filters to Posted only and groups by transfer, whereas App Info counts raw legs.
- *Average Daily Volume* — the mean transfer count across only those business days that had activity (zero-volume days excluded from the denominator). This uses a second dataset (`exec-transaction-daily`) that rolls up the summary to one row per day, so the average reflects "posted transfers per active day," not "per-rail per active day."

The *Daily Transaction Count by Type* bar uses the summary dataset with `posted_date` on the category axis and `transfer_count` on the value axis, stacked by `rail_name`. Each bar represents one calendar day; its height is the total Posted transfers for that day, subdivided by colour to show which rails contributed. The *Period Total by Type* bar aggregates across all dates in the selected window, rendering on a log scale so smaller rails (the long tail) remain readable when one dominant rail's volume would otherwise compress the rest to invisibility.

## Common patterns

### Sudden volume drop on a single day or weekend gap

The Daily Transaction Count chart shows a spike down to zero or near-zero over a day or weekend, then returns to normal on the following Monday. This is benign — the demo data seed window (90 days) doesn't include full business-day history, and non-business-day posting is sparse. On a production deploy with full-history ETL, these gaps won't appear.

### Consistent high-volume rail, low-volume long tail

The Period Total chart on log scale shows one rail dominating (often a high-frequency internal-transfer or batch-clearing rail), with 10–20 smaller rails occupying the remainder. This is normal distribution for a working bank — one or two rails carry the bulk of the standardized traffic. If the dominant rail suddenly drops off while others surge, suspect a feed integration failure upstream (the system may be routing around a jammed rail).

### Sharp day-to-day volume swings

The Daily Transaction Count chart shows a smooth trend one week, then a spike or cliff the next. Cross-reference against the Money Moved sheet — if *Gross Money Moved* trends smoothly while *transaction count* spikes, the volume spike is likely many small transfers; if both spike together, a high-value batch posted. If only *transaction count* spikes with Money Moved flat, investigate whether Pending legs are being re-posted or failed legs are finally clearing (check App Info's `<prefix>_transactions` row count — a jump there signals ETL reload, not new traffic).

### Missing data stretch (app info shows recent refresh, data is stale)

The Daily Transaction Count chart shows activity through a certain date, then nothing, even though you adjusted the date filter to include today. Cross to the *App Info* sheet and check the `<prefix>_transactions` row's `last_refresh_at` timestamp. If it's older than the most recent ETL load, the dataset is stale — the SQL ran against a snapshot that hasn't refreshed yet. The dashboard is not broken; the ETL is behind.

### Rail composition shifts dramatically

Comparing two historical periods (e.g. this month vs last month), the Period Total chart shows a rail that was in the top 5 last period now ranked 15th, or vice versa. This usually reflects a scheduled batch migration ("we switched ACH origination from rail A to rail B on the 15th") or a new customer onboarding routing through a new rail. Confirm with the operations calendar; if unplanned, escalate to the transfer-routing team.

## What "no rows" means

A zero-row Daily Transaction Count chart means no transfers posted in the selected date range. This can happen for three reasons:

- **The date window is too narrow and lands on non-business days.** Widen the filter to at least a 5-business-day span. If you still see zero, check App Info's `<prefix>_transactions` row count — if it's non-zero, the base table has data but no Posted legs exist in the window.
- **The institution had a genuine processing halt.** All status=Pending legs (operational noise) is one thing; zero Posted legs is a production alert. Check with the settlement team and the transfer rails' operational status pages.
- **The matview is stale.** Cross to *App Info* and verify `last_refresh_at` is recent. If it predates the most recent ETL load, re-run the refresh and return to this sheet.

If all three conditions are met (recent refresh, non-empty base table, zero Posted legs in the window), the window is clean — no settled transfers in that period.

## Cross-sheet drills

This sheet has no click-through drills defined in the current release. To inspect individual transfers or accounts in detail, navigate to the Account Reconciliation dashboard (the main dashboard in your QuickSight console) and open the Transactions sheet.

## Related handbook pages

- [Money Moved](money-moved.md) — companion sheet showing dollar volume (net and gross) by rail; use when you need to see magnitude alongside count.
- [Account Coverage](account-coverage.md) — account-level activity; use when asking "which accounts drove the volume spike?"
- [Program Health](program-health.md) — L1 invariant violations; if transaction volume looks healthy but L1 violations spike, the issue is data quality not throughput.

---

*First time here? See the [Vocabulary](../_glossary.md) for `rail`, `posted`, `transfer`, and the other project-specific terms.*
