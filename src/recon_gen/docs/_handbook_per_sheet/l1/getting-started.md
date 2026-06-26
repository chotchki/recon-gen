# L1 Reconciliation Dashboard

> **What this sheet teaches.** The L1 reconciliation dashboard answers a single foundational question: is each account's stored balance honest? This Getting Started sheet is the welcome page — it explains the scope of accounts and infrastructure the dashboard monitors, then points you to the right per-invariant tab.

## What you're looking at

Getting Started opens with a welcome text box (supplied by the L2 instance's description) and an *L2 Coverage* inventory block listing the dashboard's configured scope: how many internal and external accounts, how many account templates (role classes), how many rails (transfer families), transfer templates (multi-leg shared transfers), chains (ordered transfer-of-transfers sequences) and limit schedules (daily outbound caps). The numbers live-count from the L2 instance at each deployment, so switching L2 instances (different banks, different regulatory postures) re-scopes the dashboard without code changes.

## How to read the numbers

L2 Coverage displays six inventory measures, all live-populated from the configured L2 instance:

- **Internal / external accounts** — the institution's own ledgers vs counterparty books (banks, payment networks, the fed). L1 invariants apply to internal accounts only.
- **Account templates** — semantic role classes (like CustomerSubledger, MerchantDDA) that bind to specific accounts at posting time.
- **Rails** — named transfer families (ACH, wire, check, on-us internal) with their own age caps and metadata requirements.
- **Transfer templates** — blueprints for multi-leg shared transfer metadata + structure.
- **Chains** — declared sequences of leg patterns that particular kinds of transfers are supposed to fire through.
- **Limit schedules** — daily outbound debit caps by parent account role and transfer type.

## Common workflows on this dashboard

### "The books don't reconcile — where do I start?"

Click the *L1 Exceptions* tab — it's the roll-up of every open L1 SHOULD-constraint violation across all invariant checks (drift, ledger drift, overdraft, limit breach, expected-EOD-balance breach, balance-cadence gap, stuck pending, stuck unbundled, chain-parent disagreement, XOR-group violation, fan-in disagreement, multi-XOR violation), scoped to the date window you select via the picker at the top. If your problem shows up there, the detail row tells you the violation type and the account-day it touches.

### "Drift spiked last Tuesday. Is it recurring?"

Click the *Drift Timelines* tab. It shows Σ |drift| per business day, one line per account role. If the line for your role spiked once and dropped back to zero, it was an isolated event. If the same role spikes every Monday, it's a recurring pattern — your institution's batch processor for that account family likely misfires weekly. Drill down a spike to the *Drift* tab to see which specific accounts are drifting.

### "I need to audit a specific account's reconciliation for the past 30 days."

Click the *Daily Statement* tab. Pick the account and set the date range. You'll see the per-day walk: opening balance, debits in, credits in, closing balance and the drift (should be zero). If a day shows non-zero drift, click that row's right-click menu → *View Transactions* to see every Money record (leg) that posted that day.

### "We're seeing pending transactions age out. Which ones?"

Click the *Pending Aging* tab. It shows every Money record with `status='Pending'` past its rail's configured `max_pending_age` cap. The detail table is sortable by account, by rail, by age bucket. Right-click any row → *View Transactions* to see the full transfer (all legs, including Posted and Failed siblings).

## Where to start

If you land on *Getting Started* and see the L2 Coverage block populated (accounts, roles, rails, templates, chains, limits all showing non-zero counts), the dashboard is healthy and connected to its data source. If the numbers look wrong — for example, 0 accounts when you know the institution has 50 — the matview pipeline didn't run or the L2 instance's schema is out of date. Cross to the *Info* sheet (the last tab) and check the *Matview Status* table. If a matview's `latest_date` lags the base tables' (the data moved forward but that matview stayed behind), ask the ops team to trigger a refresh. If the row count is zero across the board, the L1 invariant SQL didn't execute — that's an infrastructure alert, not a dashboard problem.

## Cross-sheet drills

The Getting Started sheet itself contains no drillable rows — it's a landing page of reference material. Every L1 invariant tab (Drift, Overdraft, Limit Breach, Pending Aging, Unbundled Aging, Supersession Audit, L1 Exceptions, Daily Statement, Transactions) carries its own drill actions. Use the `?` button on each sheet for detailed drill documentation.

## Related handbook pages

- [L1 Exceptions](exceptions.md) — the unified roll-up of all twelve invariant checks; your starting point when you know something is broken but not which invariant.
- [Drift](drift.md) — the detailed guide for balance-disagreement violations.
- [Daily Statement](daily-statement.md) — per-account-day narrative; the destination of row-level drills.
- [Info](../_shared/app-info.md) — matview health canary; check here if the dashboard looks empty.

## Vocabulary

First time here? The L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) dashboard uses several project-specific terms: [matview](../_glossary.md#matview--materialized-view) (a refreshed SQL view), [account_role](../_glossary.md#account-role) (a semantic grouping of accounts), [rail](../_glossary.md#rail) (a named transfer family), [chain](../_glossary.md#chain) (a declared multi-leg transfer sequence) and [template](../_glossary.md#template) (a blueprint for transfer metadata). See the [Vocabulary](../_glossary.md) for expanded definitions.
