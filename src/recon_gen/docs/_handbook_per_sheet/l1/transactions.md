# Transactions

> **What this sheet teaches.** The raw posting ledger — supersession-aware Money records (transaction legs) filtered by the analyst's criteria. Every row is one Money record *in its current state* — prior versions of overwritten entries don't appear.

## What you're looking at

A single table scrolls the entire posting ledger one leg per row. No KPIs headline the sheet — the value is "show me the legs I ask for." At the top sit six filter dropdowns: Account, Transfer, Transaction ID, Status, Origin and Transfer Type. Pick any combination and the table narrows to rows matching ALL filters (AND logic). The table shows each leg's account, transfer membership, direction (Debit / Credit), amount in dollars, status (Pending / Posted / Failed), origin (internal or external routing), rail name and posting timestamp. Sorting is by posting time descending, so the most recent activity reads at the top.

## How to read the numbers

The dataset reads directly from the `<prefix>_current_transactions` ([matview](../_glossary.md#matview--materialized-view) — a read-optimized materialized view) — a view of the `<prefix>_transactions` base table filtered to each Money record's `MAX(entry)` version only. Supersession is baked in: when an append-only entry gets rewritten, the current view skips the prior versions and shows only the latest. This is the truth the institution posted *now*, not an audit trail of corrections.

The SQL selects these columns from the matview:

- `id` (renamed to `transaction_id`) — unique identifier for this Money record
- `account_id`, `account_name`, `account_role` — identifying the account the leg posts to
- `account_parent_role` — the account's parent role if it belongs to an aggregate; NULL for leaf accounts
- `transfer_id`, `transfer_parent_id` — the transfer this leg belongs to and its parent if part of a meta-transfer
- `rail_name` — the named transfer family (ACH, Wire, Check, etc.)
- `amount_money` — the leg's amount in dollars (converted from cents at projection)
- `amount_direction` — Debit or Credit
- `status` — Pending / Posted / Failed
- `origin` — how the leg entered the system: InternalInitiated / ExternalForcePosted / ExternalAggregated
- `posting` — the timestamp when the leg recorded

Six dropdown filters push predicates into the SQL WHERE clause via dataset parameters. The `account_id` filter uses the `_account_display_clause()` pattern (showing accounts with stored daily balances only); the Transaction ID search filters on the matview's `id` column; the rest use `_data_value_clause()` with the sentinel-OR guard so "all" defaults to PASS. A universal date-range filter ([date start / date end](../_glossary.md#cross-cutting-concepts)) narrows by posting timestamp, with the upper bound expanded +1 day so same-day non-midnight rows on the end day are included.

## Common patterns

### Pending legs older than the rail's cap

Filter to `Status = Pending`. Look at `posting` timestamps and calculate the days elapsed. Cross-reference the rail (`rail_name` column) against the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) instance's configuration — each rail carries a `max_pending_age` threshold (visible on the Pending Aging sheet). A leg sitting in Pending past that cap is one symptom of a stuck-transfer pattern. The Daily Statement's detail table for that account-day may show whether other legs of the same transfer posted or failed.

### Failed legs next to Posted legs in a transfer

Filter to a specific `transfer_id` (visible once you click into one row). If you see both `status='Posted'` and `status='Failed'` on different legs of the same transfer, the transfer partially executed — the boundary rejected one leg but the institution's system recorded a balance change anyway. This is the classic boundary-rejection shape the Drift sheet's "Single big magnitude, single account, single day" pattern flags. Drill to Daily Statement for the account-day to see the cumulative impact.

### Unbundled Posted legs on an aggregating rail

Filter to `Transfer Type = <aggregator_rail>` and look for rows where `status='Posted'` but `posting` is older than the rail's `max_unbundled_age` cap. These legs made it to the settlement side but the bundler hasn't grouped them yet. The Unbundled Aging sheet surfaces the time-bucketed cohort; here you see the raw timestamps. Compare against the rail's expected cadence (typically a day or two for ACH/wire aggregators).

### Money flowing between internal and external counterparties

Look at rows where `account_role` indicates an external account (bank, payment network, the fed). The `origin` column tells you which direction the leg came from. Internal-initiated legs show as `InternalInitiated`; legs forced into the posting ledger by an external actor (ACH file from the Fed, wire settlement from the correspondent bank) show as `ExternalForcePosted` or `ExternalAggregated`. This flow pattern is how you spot when an external counterparty unilaterally posted something the institution didn't explicitly initiate.

## What "no rows" means

A blank Transactions sheet means no Money records match your filter criteria. This usually means:

- **Filter too narrow.** You picked a transfer ID that doesn't exist in the date window, or an account with no postings in the range. Widen the date picker or clear the Account / Transfer dropdowns to "All".
- **Date window misses the activity.** Transactions only shows postings within the universal date-range filter. A leg posted on 2026-05-01 won't appear if the picker is set to 2026-06-01 through 2026-06-07. Widen the range.
- **Matview is stale.** Cross to *App Info* and check the `<prefix>_current_transactions` row's `latest_date`. If it lags the base tables' most recent `latest_date`, the matview hasn't refreshed since postings arrived. Ad-hoc dashboard hits don't trigger a refresh — the institution's ETL pipeline does on its schedule.

If *App Info* shows that `latest_date` as null or the matview row count as zero across the board, the L1 transaction pipeline didn't run — that's an ops alert, not a "no data" state.

## Cross-sheet drills

- **Posting Ledger row, `account_id` column → Daily Statement** (right-click → *View Daily Statement for this account-day*). Lands on the per-account-per-day narrative for the leg's business day so you can read the full balance walk around that posting.
- **Inbound** — this sheet is the target of *View Transactions for this transfer* drills defined on Pending Aging, Unbundled Aging, Supersession Audit and Daily Statement's detail table (right-click a `transfer_id` cell there). Each narrows this sheet to only the legs of that transfer so you can inspect the full multi-leg structure.

## Related handbook pages

- [Daily Statement](daily-statement.md) — the per-account-per-day narrative; use it when you want to see posting + balance history for one account-day, not raw leg-by-leg data.
- [Pending Aging](pending-aging.md) — when you want to find stuck-Pending legs and their age bucket.
- [Unbundled Aging](unbundled-aging.md) — when you want to find Posted legs waiting for the aggregator and their age bucket.
- [Drift](drift.md) — the L1 integrity invariant that catches when postings disagree with stored balance; Transactions is the source-of-truth leg list for reconciling drift findings.
- [Metadata Popup](metadata-popup.md) — the `{} View metadata` entry on the row-drill `⋯` menu; opens a side panel with the row's pretty-printed JSON metadata (App2-only).

## QS parity notes

- **URL-driven transfer ID dropdown.** When you arrive at this sheet via a drill from another sheet (e.g., Daily Statement's "View Transactions for this transfer"), QS may filter the data correctly but show the *Transfer* dropdown still at *All* — the data is right, the control is lying. App2 doesn't have this defect. See [quirks log §dependent-dropdown-no-refresh](../../reference/quicksight-quirks.md).

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`, `rail`, `account_role` and the other project-specific terms.*
