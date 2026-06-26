# Drift Timelines

> **What this sheet teaches.** Drift over time — the accumulated magnitude
> of sub-ledger disagreements per business day, broken down by account role,
> so you can spot whether a drift violation is a one-off event or a
> recurring failure in a particular family of accounts. Every row in the
> underlying dataset is one (day, role) bucket carrying Σ |drift| for all
> accounts in that role on that day.

## What you're looking at

A strip of two KPIs runs across the top — *Largest Leaf
Drift Day (peak business day)* and *Largest Parent Drift Day (peak
business day)*. Below sit two side-by-side line charts: *Leaf Account
Drift Over Time* (showing Σ |drift| per business day for leaf [accounts](../_glossary.md#parent--leaf-accounts),
with one line per `account_role`) and *Parent Account Drift Over Time*
(the same shape for parent accounts). A universal date-range filter
(shared across the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
app) and an *Account Role* dropdown let you narrow by date window and role. Healthy days sit
on the zero baseline; spikes mark when a role's feed or parent rollup diverged.

## How to read the numbers

Both charts read from the L1 invariant [matviews](../_glossary.md#matview--materialized-view)
`<prefix>_drift` (leaf) and `<prefix>_ledger_drift` (parent). The
datasets pre-aggregate via `GROUP BY business_day_end, account_role`,
producing one row per (day, role) pair carrying `SUM(ABS(drift))` (in
dollars, converted from the matview's native cents).

The *Largest Leaf Drift Day* KPI computes `MAX(abs_drift)` over all
leaf-account (day, role) buckets in the current date window — the worst
single business day for leaf violations. The *Largest Parent Drift Day*
KPI does the same for parent-account (`business_day_end, account_role`)
buckets in the parent matview.

Each line chart plots `business_day_end` (x-axis, date grain) against
`SUM(ABS(drift))` per (day, role), with one line per distinct
`account_role` value. The Y-axis is currency (`$`). The `account_role`
dropdown narrows both charts before aggregation, so selecting a role
filters both timelines to that role only. A zero line across the window
means that role's stored balances agreed with postings every day in the window.

Both matviews filter `WHERE stored_balance <> computed_balance` and
[`account_scope = 'internal'`](../_glossary.md#account-scope-internal-vs-external),
so external accounts (banks, payment networks) never appear — the
institution only audits its own books' integrity.

## Common patterns

### Recurring spike on the same day of week

A role's line spikes every Monday but not other days. This is a **weekly
batch process running into trouble** — a feed that emits balances on
Mondays only is hitting a carry-forward mismatch or a processing order
bug. The Drift sheet (sister to this one) breaks the same timeframe by
account, so narrow the Drift sheet to the same role + day and drill
into individual accounts to see which leaf or parent is actually
drifting.

### Single isolated spike

One line spikes sharply once, then returns to zero. This is a **one-off
event** — a posting rejection, a balance-emission skip or a deploy-time
correction. Unlike the recurring spike (which signals an ongoing feed
problem), a single spike may be expected if you know a data correction
happened that day. Check the posting timestamps and the Daily Statement
for that role / account to confirm the cause.

### Persistent non-zero line

One role's line sits above zero across the entire window, never dropping.
This is a **carry-forward of a prior incident** — a posting was missed
weeks ago, the stored balance was never adjusted and the mismatch
locked in. The magnitude may be constant or grow if more postings land
on non-emit days. Cross to the Drift sheet with the same role and date
filters to find the specific account, then drill to *Supersession Audit*
to look for a `supersedes` metadata tag that signals an attempted
correction.

### Divergence between leaf and parent

The leaf chart is clean (zero across the window) but the parent chart
spikes. Leaf accounts agree with their postings, but the parent control
account's stored rollup does not sum to the children. This is a
**parent-rollup emission bug** — a batch process that emits parent
balances did not run correctly, or ran before the leaves were ready. The
children's feeds are honest; the problem is in the consolidation layer.

### Both leaf and parent spike together

Both charts show identical spike patterns on the same day. This usually
means the same **feed or posting source diverged at multiple levels** —
a batch job that writes both child and parent postings either re-ran
with stale input or dropped entries that should have fired on both sides.

## What "no rows" means

A clean drift timeline means *every* internal account's stored balance
agreed with its postings on every day in the current window. That is the
steady-state expectation. If you see zero baseline across the window:

- **Confirm the matviews are fresh.** Cross to *App Info* and check the
  *Matview Status* table's `drift` and `ledger_drift` rows. If
  `last_refresh_at` is older than the most recent posting + a few
  minutes, the data may be clean *as of the last refresh* but stale.
- **Check the date filter and role filter.** A very narrow window or a
  single-role selection can show zero on days when that role had no
  violations. Widen the window and select *All* roles to confirm the
  system is genuinely clean.
- **Don't stop here.** Drift is one of twelve L1 invariants. A clean
  Drift Timelines sheet next to a populated *Overdraft* or *Limit
  Breach* sheet means *that* invariant has work — `?` those sheets
  next.

If *App Info* shows `last_refresh_at` as null or the matview row
count as zero across the board, the L1 invariant pipeline didn't run.
That's an ops alert, not a "clean" signal.

## Cross-sheet drills

The Drift Timelines sheet has no cross-sheet drills. Use the companion
*Drift* sheet (which breaks violations by individual account-day) to
narrow to a specific account and drill from there into *Daily Statement*
or *Transactions*.

## Related handbook pages

- [Drift](drift.md) — the account-day detail view; use it when you want
  to see *which* specific accounts are drifting.
- [Daily Statement](daily-statement.md) — per-account narrative showing
  every posting and balance event with running computed balance; the
  destination of row-level drills from the Drift sheet.
- [Overdraft](overdraft.md) — the orthogonal SHOULD-constraint
  (`stored < 0`); a chronically-negative but internally-consistent
  account appears there but NOT in Drift.
- [Supersession Audit](supersession-audit.md) — when the drift
  signature looks like a botched correction attempt.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`,
`matview`, `account_role` and the other project-specific terms.*
