# Daily Statement

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

Per-(account, day) walk: opening balance + day's debits + day's
credits + closing balance + drift, plus every leg posted that day.
Pick one account and one business day via the sheet controls; KPIs
surface the 5-number summary and the detail table lists every Money
record posted.

Drift here = `closing_balance_stored − (opening_balance + Σ
signed_amount of today's posted legs)`. On a healthy feed it's
exactly zero. Non-zero drift on this sheet is the single visual cue
that the underlying ledger doesn't reconcile for that account-day.

??? example "Screenshot"
    ![Daily Statement](../screenshots/l1/l1-sheet-daily-statement.png)

## When to use it

Every analyst-facing investigation lands here. From L1 Exceptions
right-click → "View Daily Statement"; from any per-invariant detail
table same drill. The sheet is the per-account-day artifact the Data
Integration Team can screenshot and send to the producer system's
team for triage.

## Visuals

- **Opening Balance** (KPI) — end-of-prior-day stored balance for
  the picked account.
- **Debits** (KPI) — sum of Debit-direction Money records posted today.
- **Credits** (KPI) — sum of Credit-direction Money records posted today.
- **Closing Stored** (KPI) — the day's stored closing balance from
  the feed.
- **Drift** (KPI) — stored − recomputed. Non-zero ⇒ feed doesn't
  reconcile.
- **Posted Money Records** (Table) — every leg posted on the picked
  account-day. Direction shows Debit / Credit; status filters out
  Failed legs in the summary KPIs but not here.

## Drills

- **Right-click any leg → "View Transactions for this transfer"** →
  opens **Transactions** narrowed to the clicked `transfer_id` so the
  analyst can see every leg of the multi-leg transfer (typically the
  matching counterparty leg posted to a different account).

## Filters

- **Account** (ParameterDropdown) — single-value picker over
  `account_id`. Required — the sheet's KPIs don't render without
  one selected. Drill-targets auto-fill it.
- **Business Day** (ParameterDateTimePicker) — single-value picker
  over `business_day`. Same semantics. Drill-targets auto-fill it.

No universal date-range filter on this sheet — the Account + Business
Day pickers are stricter (single-value vs range), so the date-range
pickers would be redundant.
