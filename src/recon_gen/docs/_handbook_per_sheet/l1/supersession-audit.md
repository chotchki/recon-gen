# Supersession Audit

> **What this sheet teaches.** Every logical transaction or balance whose append-only `entry` column has been rewritten — the audit trail for corrected postings and balance re-statements. These systems admit revision-and-audit rather than deletion-and-rewrite, so the trail records exactly what changed and why.

## What you're looking at

The sheet opens on a strip of three KPIs across the top — *Logical Keys (Transactions) with Supersession* (count of distinct transaction logical keys with more than one entry), *Supersession $ Exposure* (the dollar magnitude of the audit surface), and *Supersession Rows with No Reason* (higher-entry rows whose `supersedes` reason is blank, target = 0). Below the strip sit two side-by-side tables: *Transactions Audit* (every entry of every logical transaction with multiple versions) and *Daily Balances Audit* (every version of every account-day cell that was re-stated). A row of controls above the tables lets you filter the transactions audit by `supersedes` reason, narrow to a single transaction via the Transaction ID picker or isolate the no-reason rows.

## How to read the numbers

Both tables read from the BASE tables — `<prefix>_transactions` and `<prefix>_daily_balances` — not the Current* views, because the whole point is to audit the prior entries that Current* hides by design.

**Transactions Audit table:**

Reads from `<prefix>_transactions` filtered to logical rows (keyed by `id`, which becomes `transaction_id` in the dataset) where `COUNT(*) OVER (PARTITION BY id) > 1`. Each row carries:

- `entry` — the append-only version number; higher entries supersede lower ones
- `transaction_id` — the logical transaction ID (immutable identifier for the entry chain)
- `supersedes` — the reason why this entry exists: Inflight (a pending leg was updated during bundling), BundleAssignment (a posted leg was assigned a bundle_id) or TechnicalCorrection (a correction to a prior error). NULL on entry-1 rows (no reason needed for the first entry)
- `account_id`, `account_name`, `transfer_id`, `rail_name`, `amount_money` (in dollars), `amount_direction`, `status`, `posting`, `bundle_id` — the standard transaction metadata

The `l1_supersession_no_reason` derived column flags each higher-entry row where `supersedes IS NULL`, which the right-hand KPI counts (target = 0 per the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) SPEC).

**Daily Balances Audit table:**

Reads from `<prefix>_daily_balances` filtered to logical keys (keyed by `account_id, business_day_start`) where `COUNT(*) OVER (PARTITION BY account_id, business_day_start) > 1`. Each row carries:

- `entry` — append-only version number
- `account_id`, `account_name`, `account_role` ([account-role](../_glossary.md#account-role) — semantic label grouping accounts by what they do) — account identity
- `supersedes` — reason for restatement; for daily balances, this is typically TechnicalCorrection only (no bundling lifecycle on balance snapshots)
- `business_day_start`, `business_day_end` — the calendar day this balance covers (per account timezone)
- `money` — the stated balance in dollars; the primary thing that changes across entries is this column

**KPI semantics:**

- *Logical Keys* counts distinct `transaction_id` values (not row count). One logical transaction can have N entries; the left KPI answers "how many distinct transactions got revised?"
- *$ Exposure* is `SUM(|amount_money|)` across all superseded transaction entries — the dollar magnitude of the audit surface
- *Rows with No Reason* counts higher-entry rows where `supersedes IS NULL` (not distinct transactions). A single transaction_id can have multiple no-reason rows if multiple entries lack a reason; the right KPI counts ROWS, not keys, so the unit differs from the left KPI

## Common patterns

### High TechnicalCorrection volume on transactions

The *Transactions Audit* table, filtered by `supersedes = 'TechnicalCorrection'`, shows many rows. These are corrections to prior errors — either posting mistakes the ledger system caught and fixed, or feed-level bugs that the ETL layer rewrote. If the volume is large and clustered on recent postings, loop the upstream feed team in immediately; this is a symptom of a feed integration bug. If it's spread thinly over time, it's normal operational noise.

### Inflight entries cluster on bundling days

Filtering by `supersedes = 'Inflight'` shows entries with posting dates near the same day, all in Pending or Posted status, all on the same rail. This is normal in a busy aggregating-rail bundling cadence — a pending leg fires, the rail bundles it into a clearing-house batch, and emits a higher-entry row with `supersedes = 'Inflight'` to mark the mid-flight update. No action needed unless the Pending age cap (rail-specific; check the rail's `max_pending_age` config) is being violated.

### BundleAssignment entries on aggregating rails

Rows with `supersedes = 'BundleAssignment'` are the bundler's marking up of a Posted leg with a `bundle_id`. This is expected on aggregating rails (ACH, wire, check clearing) and abnormal on direct rails (on-us internal transfers). If you see high BundleAssignment volume on a non-aggregating rail, check the rail's bundler configuration — the rail shouldn't be trying to bundle.

### Unaccounted-for balance restatements

The *Daily Balances Audit* table has multiple entries for the same account-day, and the `money` column values differ. This is a re-statement of the account's end-of-day balance. If the `supersedes` reason is TechnicalCorrection, the institution caught a prior balance-reporting error and corrected it. If `supersedes` is NULL (blank), that's a data-quality violation — the system rewrote a balance without recording why. Cross to the *Drift* sheet ([Drift](drift.md)) with the same account filter to see whether this balance restatement correlates with a drift-resolution.

### No-reason rows (policy violation)

The right KPI, *Supersession Rows with No Reason*, counts higher-entry rows where `supersedes IS NULL`. This is a violation of the L1 SPEC — every rewrite should declare its cause. Drill into the *Transactions Audit* table and filter by the (blank) `supersedes` value; each row in the result set is a row that lacks a reason and needs investigation. Either the upstream feed forgot to set the reason (ETL writer bug), or the reason got lost in a system-to-system handoff (integration gap). Either way, this is a non-zero target and a remediation flag.

## What "no rows" means

A clean supersession sheet — zero rows in both audit tables — means every logical transaction and balance revision did NOT happen in the current window, OR the matview ([materialized-view](../_glossary.md#matview--materialized-view)) is stale. To distinguish:

- **Confirm the source tables are fresh.** Cross to *App Info* and check the *Matview Status* table. Both audit tables read straight from the base `<prefix>_transactions` and `<prefix>_daily_balances` tables (not a matview), so it's their `row_count` and `latest_date` columns that tell you whether fresh data has loaded since you opened the dashboard.
- **Check the date window.** The tables don't have a built-in date filter (they read from the BASE tables, which hold all history). Zero rows across a wide time window is the steady-state expectation for most institutions — supersessions are rare outside of bundling cadences and error-correction windows. Widen to trailing 30 days to verify the signal.
- **Distinguish "all clean" from "filter too narrow."** If the `supersedes` dropdown is set to a specific reason (not "All"), you may see zero rows for that reason even though other reasons exist. Click the dropdown and reset to "All" to see the full audit trail.

If *App Info* shows `latest_date` as null or the row counts as zero across the board, the ETL load didn't run. That's an ops alert, not a "clean" signal.

## Cross-sheet drills

You usually land here from another sheet (notably *Drift*, [Drift](drift.md)) and read the trail to understand what changed and why. Three row-level drills let you trace further:

- **Transactions Audit row → same sheet** (left-click → *Filter to this transaction*). Focuses the audit on that one transaction's full multi-entry trail. Clear the Transaction ID dropdown to return to the whole audit. This is a control-write, not a URL navigation, so it sidesteps the QS URL-parameter quirk.
- **Transactions Audit row → Transactions** (right-click → *View Transactions for this transfer*). Every leg of that transfer on the posting ledger; the destination window widens to all time so a recently-corrected leg doesn't fall outside its default range.
- **Daily Balances Audit row → Daily Statement** (right-click → *View Daily Statement for this account-day*). The per-account-day narrative behind the re-stated balance.

## Related handbook pages

- [Drift](drift.md) — when you see drift on an account and suspect a botched correction; the Drift handbook names Supersession Audit as the cross-check for carry-forward ([carry-forward](../_glossary.md#carry-forward--sparse-cadence)) patterns
- [Transactions](transactions.md) — the full posting ledger; use when you need to trace a no-reason row further into the system
- [Daily Statement](daily-statement.md) — per-account-day narrative; useful after you've identified a balance re-statement in the *Daily Balances Audit* table and want to see the postings behind it

## QS parity notes

No known rendering quirks on this sheet.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`, `account_role`, `carry-forward`, and other project-specific terms.*
