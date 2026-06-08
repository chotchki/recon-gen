# Getting Started

> **What this dashboard is for.** The L1 reconciliation dashboard answers a single foundational question: is each account's stored balance honest? You're looking at a dashboard of account-integrity checkpoints — SHOULD-constraints that hold at every account every end-of-day, regardless of what money flows through.

## What you're looking at

You open the L1 app to one discovery page followed by nine per-invariant tabs. This *Getting Started* sheet is the welcome — it explains what the dashboard reconciles (in terms of the configured accounts, roles, and transfer patterns) and points you to the tab matching your question. The nine tabs that follow each query one L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) invariant view directly: *Drift* for ledger disagreements, *Overdraft* for negative balances, *Limit Breach* for transfer caps, and so on. Every row on every invariant sheet is one SHOULD-constraint violation — the dashboard doesn't show you healthy cases, only problems to fix.

## What this dashboard reconciles

The dashboard covers a configured universe of accounts, roles, transfer templates, and rails ([account_role](../_glossary.md#account-role), [rail](../_glossary.md#rail), [chain](../_glossary.md#chain), [template](../_glossary.md#template)) supplied by the L2 instance. A matview ([materialized view](../_glossary.md#matview--materialized-view)) under each tab ingests the configured schema at refresh time, so switching L2 instances (different banks, different regulatory postures, different reconciliation footprints) re-scopes the entire dashboard without code changes. The *Getting Started* sheet displays a live count: how many internal and external accounts sit in scope, how many roles group them, how many rails and transfer chains the institution declared, and what limit schedules gate outbound transfers.

## Common workflows on this dashboard

### "The books don't reconcile — where do I start?"

Your manager says "we got a reconciliation audit exception" or "the daily close report disagrees with our ledger." Click the *L1 Exceptions* tab first — it's the 9am roll-up of every open violation across all ten invariant checks, sorted by dollar magnitude. If your problem shows up there, the detail row tells you the violation type (`drift` / `overdraft` / `chain_parent_disagreement` / etc.) and the account-day it touches. Click that row's right-click menu → *View Daily Statement* to see the per-account narrative for that day — postings in, balance out, and the running reconciliation.

### "Drift spiked last Tuesday. Is it recurring?"

Click the *Drift Timelines* tab. It shows Σ |drift| per business day, one line per account role. If the line for your role spiked once and dropped back to zero, it was an isolated event (a feed re-run, a data correction that already landed). If the same role spikes every Monday, it's a recurring pattern — your institution's batch processor for that account family runs on Sunday night and misfires weekly. Different diagnosis tools. Drill down a spike to the *Drift* tab to see which specific accounts are drifting.

### "I need to audit a specific account's reconciliation for the past 30 days."

Click the *Daily Statement* tab. Pick the account and set the date range. You'll see the per-day walk: opening balance, debits in, credits in, closing balance, and the drift (should be zero). If a day shows non-zero drift, click that row's right-click menu → *View Transactions* to see every Money record (leg) that posted that day. That's where you'll spot the missing posting or the duplicate.

### "We're seeing pending transactions age out. Which ones?"

Click the *Pending Aging* tab. It shows every Money record with `status='Pending'` past its rail's configured `max_pending_age` cap. The detail table is sortable by account, by rail, by age bucket. Right-click any row → *View Transactions* to see the full transfer (all legs, including Posted and Failed siblings). Then loop in the rail owner to confirm the leg should have posted or if the batch needs to retry.

## Where to start

If you land on *Getting Started* and see the L2 Coverage block (accounts, roles, rails, etc.), the dashboard is healthy and connected to its data source. If the numbers look wrong (say, 0 accounts when you know the institution has 50), the matview pipeline didn't run or the L2 instance's schema is out of date — cross to the *App Info* sheet (last tab) and check the *Matview Status* table. If `last_refresh_at` is older than the most recent data load, ask the ops team to trigger a refresh. If the row count is zero across the board, the L1 invariant SQL didn't execute — that's an infrastructure alert, not a dashboard problem.

## Cross-sheet drills

- **Drift** — where you investigate stored vs computed balance disagreements. Leaf table covers individual accounts; parent table covers aggregate rollups. Use this when a row on *L1 Exceptions* flags a drift violation.
- **Drift Timelines** — time-series view of drift magnitude per business day, one line per account role. Answers "is this recurring?" Use this when you've spotted drift and need to understand if it's a recurring pattern or a one-off.
- **Overdraft** — accounts holding negative money at end-of-day. Orthogonal to Drift: an account can be overdrafted (stored < 0) but NOT drifted (if its postings have always summed to that same negative). Use this when the audit flags "negative balance" violations.
- **Limit Breach** — per-account, per-day, per-transfer-type cells where cumulative outbound debit exceeded the L2-configured cap. Use this when transfers are getting rejected due to daily limits.
- **Pending Aging** — transactions stuck in `status='Pending'` past their rail's max pending age. Use this when you're investigating stuck-leg complaints.
- **Unbundled Aging** — Posted legs still waiting to be bundled past their rail's max unbundled age. Use this when the aggregator appears to have stalled.
- **Supersession Audit** — every logical row whose `entry` column has multiple versions. Use this when investigating whether a correction was applied correctly.
- **L1 Exceptions** — the 9am scan; every open violation across all 10 invariant checks for the most recent business day. Use this as your landing page when you know something is broken but not which invariant.
- **Daily Statement** — per-account, per-day narrative: opening + flow + closing + drift. Use this when you need to walk a single account-day step-by-step.
- **Transactions** — the raw posting ledger; one row per Money record (leg). Filter by account, transfer ID, status, or origin. Use this when you're drilling into a specific transfer's anatomy.

## Related handbook pages

- [Drift](drift.md) — the detailed invariant guide for balance disagreements.
- [Daily Statement](daily-statement.md) — the per-account narrative drill destination.
- [App Info](app-info.md) — matview health canary; check here if the dashboard looks empty.

## Vocabulary

First time here? The L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) dashboard uses several project-specific terms: [matview](../_glossary.md#matview--materialized-view) (a refreshed SQL view), [account_role](../_glossary.md#account-role) (a semantic grouping of accounts), [rail](../_glossary.md#rail) (a named transfer family), [chain](../_glossary.md#chain) (a declared multi-leg transfer sequence), and [template](../_glossary.md#template) (a blueprint for transfer metadata). See the [Vocabulary](../_glossary.md) for expanded definitions.
