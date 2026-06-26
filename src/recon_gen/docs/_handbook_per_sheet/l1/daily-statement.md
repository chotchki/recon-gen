# Per-Account Daily Statement

> **What this sheet teaches.** Per-account, per-day posting reconciliation — the walk from opening balance through debits and credits to closing balance and the drift (difference between stored closing and computed closing). Every row on this sheet is one Money record (leg); the KPI strip surfaces the 5-number summary for the picked account-day so you can spot in one glance whether that day's postings reconcile.

## What you're looking at

You're looking at a per-account, per-day narrative. Pick an account using the *Account* dropdown (the full internal-account universe) and a business day using the *Business Day* picker; the five KPIs across the top show you the opening balance, debits posted, credits posted, stored closing balance and posting drift for that day. Below, the *Posted Money Records* table lists every Money record (leg) that posted that day on the picked account. The *Control Accounts* reference table at the bottom lists the L2-declared singleton control accounts (one row per `account_role` whose template materializes to exactly one account) — use it to cross-check that your picked account is the canonical control for its role, not one of many DDA-like rows in the same role.

## How to read the numbers

The sheet reads from two datasets derived from the `<prefix>_daily_statement_summary` [matview](../_glossary.md#matview--materialized-view):

**KPI calculations:**
- *Opening Balance* — the prior business day's closing balance (carried forward if no balance was emitted that day).
- *Debits (signed)* — sum of `amount_money` over all Debit-direction legs posted today, already signed negative per v6 convention (Debit = negative money).
- *Credits (signed)* — sum of `amount_money` over all Credit-direction legs posted today, already signed positive per v6 convention (Credit = positive money).
- *Closing Stored* — the feed's reported end-of-day balance for this account on this day (or carried forward if no balance row arrived).
- *Posting Drift* — `closing_balance_stored − (opening_balance + net_flow)`. The formula is: starting balance + signed net of today's legs = what the ledger should say; if the stored closing disagrees, drift is non-zero. Zero drift means the day reconciles; non-zero drift signals a posting was rejected at the boundary, a balance update misfired or (on sparse-cadence accounts) a posting landed on a non-emit day and the carried balance doesn't account for it.

**The KPI formula note:**
Opening + Credits + Debits = Closing Stored on a healthy day. The signs are already baked in (Credit = positive, Debit = negative), so you add all three; don't subtract the debits.

**Posted Money Records table:**
Each row is one Money record from `<prefix>_current_transactions`, filtered to the picked account and business day (determined by `date_trunc_day(posting)`). Columns are `transaction_id`, `transfer_id`, `rail_name`, `amount_money`, `amount_direction` (Debit or Credit), `running_balance` (cumulative signed balance after this leg, in posting order within the account-day), `status` (Pending / Posted / Failed), `origin` (InternalInitiated / ExternalForcePosted / ExternalAggregated) and `posting` timestamp.

## Common patterns

### Posting Drift = $0.00 (green checkmark)

*Posting Drift* KPI shows $0.00 with a green checkmark. The day reconciles: opening + signed net flow = stored closing. Every posting that left the institution's systems arrived in the ledger with the correct amount on the correct day. This is the healthy steady-state expectation. Verify you're looking at today's date in your timezone (the *Business Day* picker defaults to the dashboard's frame date, not wall-clock today), then move to another sheet or another account-day to find the discrepancies.

### Posting Drift non-zero (red X)

*Posting Drift* shows a non-zero amount with a red X. Something disagrees between what the institution reports and what the postings sum to. Scroll through the *Posted Money Records* table to look for unusual entries: a `status='Failed'` leg that shouldn't be there, a `status='Pending'` leg stuck for days or an `origin='ExternalForcePosted'` entry that the upstream system didn't actually send. If you spot a culprit, drill to the *Transactions* sheet (right-click on the row → *View Transactions for this transfer*) to see the full transfer's legs across all accounts.

### Stored closing is carried from a prior day

The KPI shows "Closing Stored" but the matview's `closing_balance_source` enum (visible on the App Info sheet's matview-status drill) reads `carried_no_activity` or `carried_with_activity_gap`. This account didn't emit a balance row on this day. On sparse-cadence feeds, the institution only reports a balance when something changed; on non-emit days, the dashboard carries the prior-emit value forward. A `carried_with_activity_gap` signals postings landed that day but the balance row wasn't re-emitted — so the carried value can't account for today's legs. Check the *Posting Drift* KPI: if it's non-zero and the day has legs, this is likely why.

### Posting drift on a day with no legs

*Posted Money Records* table is empty (zero rows) but *Posting Drift* is non-zero. The institution reported a closing balance change on a day when no postings arrived. This usually means the balance was restated (a correction from a prior day re-posted) or the stored balance emitted incorrectly. Cross to the *Drift* sheet (sibling handbook page) and filter to the same account and date range to see if the cumulative *Leaf Account Drift* table shows history of this account's drift — if so, the pattern may explain what happened.

## What "no rows" means

A blank *Posted Money Records* table on a day when *Posting Drift* is $0.00 means no Money records posted that day AND the stored closing balanced to opening without them — normal on slow days or weekend days. The account is clean for that day.

A blank table with non-zero *Posting Drift* means the day has no postings but the balance changed, which is unusual and warrants investigation (see "Posting drift on a day with no legs" above).

If the whole sheet shows blank KPIs and an empty table, the picked account or date may not exist in the feed. Check the *Control Accounts* reference table at the bottom: it lists every L2-declared 1:1 singleton account, so you can confirm the account name spelled in the picker matches what the L2 declares. If the account is multi-instance (DDA-like, one row per customer), the picker's account universe is the full set of internal-scope accounts from the matview; if your account doesn't appear in the typeahead, it's not in the current matview snapshot — re-run the matview refresh or widen the date range. If the KPIs stay blank for an account that DOES appear in the picker, the account-day pair exists in the balance feed but had no balance row on that day and no postings — a pure carry-forward day with no activity. This is healthy; adjust your date picker to a day with postings or balance activity.

## Cross-sheet drills

- **Posted Money Records table row → Transactions** (right-click → *View Transactions for this transfer*). Lands you on the per-leg Transactions sheet narrowed to all legs of that transfer. Useful when you spot a suspicious leg and want to see whether the other legs of the same transfer agree or disagree.

## Related handbook pages

- [Drift](drift.md) — the cumulative stored-vs-computed balance disagreement across the entire history of an account; use this when you've spotted drift on one day and want to see the pattern over time.
- [Drift Timelines](drift-timelines.md) — drift by account role over time; use this to see whether a role is chronically drifting or spiking once.
- [Transactions](transactions.md) — the raw posting ledger; drill here to see every leg across all accounts and transfer IDs.
- [Metadata Popup](metadata-popup.md) — the `{} View metadata` entry on the row-drill `⋯` menu; opens a side panel with the row's pretty-printed JSON metadata (App2-only).

## QS parity notes

CQ.4 dropped the Role → Account cascade entirely on both renderers. The *Account* picker now serves the full internal-account universe (scope=internal) on QS and App2 alike, so picker behavior matches across renderers. The pre-CQ.4 QS-vs-App2 cascade differential (App2 narrowed; QS didn't) is closed by removing the cascade rather than fixing the QS side.

---

*First time here? See the [Vocabulary](../_glossary.md) for L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)), [matview](../_glossary.md#matview--materialized-view), [account_role](../_glossary.md#account-role), [carry-forward](../_glossary.md#carry-forward--sparse-cadence), and related terms.*
