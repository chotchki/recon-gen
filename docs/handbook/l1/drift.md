# Drift

> **What this sheet teaches.** Sub-ledger drift — the disagreement
> between an account's *stored* end-of-day balance (what the institution
> reported) and its *computed* balance (the cumulative net of every
> posted transaction through that day). Every row on this sheet is
> one violation of an L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
> SHOULD-constraint: the ledger does not agree with the postings that
> produced it.

## What you're looking at

The sheet opens on a strip of four KPIs across the top — *Open Drift
Count* (rows in the leaf table), *Open Drift Magnitude (Σ ABS)*, *Worst
Single Drift*, and *Distinct Accounts*. Below the strip sit two side-
by-side tables: *Sub-Ledger Drift* (leaf [accounts](../_glossary.md#parent--leaf-accounts)
— individual posting ledgers like a customer DDA or a vault) and
*Ledger Drift* (parent accounts — rollups like a customer-ledger
control account whose stored balance should equal the sum of its
children).

A *Drift Composition* bar chart on the right groups the violation
volume by `account_role` so you can see which family of accounts is
generating the problem. Filters across the top let you narrow by
date range (universal across the L1 app), by specific account, and by
role.

## How to read the numbers

Both tables read from the L1 invariant [matviews](../_glossary.md#matview--materialized-view)
`<prefix>_drift` (leaf) and `<prefix>_ledger_drift` (parent). Each matview joins the carry-
forward stored balance from `effective_balances` (the source of truth
for "what the account said it was at end-of-day", including carried-
forward days when no balance was emitted) against a `computed_*`
balance and emits only rows where they disagree.

The columns are the same on both tables:

- `account_id`, `account_name`, `account_role` — identifying the cell
- `business_day_start`, `business_day_end` — the day window the
  imbalance is reported for, at the account's own offset
- `stored_balance` — `effective_balances.effective_money` (in cents)
- `computed_balance` —
  - For the leaf table: `Σ signed_amount` of every Posted leg whose
    `account_id = sb.account_id` through `business_day_end`
  - For the ledger table: `Σ stored_balance` of all children of this
    parent at the same `business_day_start`
- `drift` — `stored_balance − computed_balance` (signed; positive
  means the stored balance is *higher* than the computed)
- `source` — whether the stored value came from an emitted daily
  balance or was carried forward from a prior day

Both matviews filter `WHERE stored_balance <> computed_balance` and
[`account_scope = 'internal'`](../_glossary.md#account-scope-internal-vs-external),
so external accounts (banks, payment networks) never appear here —
banks may overdraft us but we MUST NOT misreport our own books.

The *Open Drift Count* KPI counts rows in the leaf table within the
current date filter. The *Σ ABS* magnitude KPI is
`SUM(ABS(drift))` over the same set. *Worst Single Drift* is the
single row with the largest `ABS(drift)`. *Distinct Accounts* counts
unique `account_id` values across both tables — useful when the same
broken feed is producing drift across many days for the same handful
of accounts.

## Common patterns

### Single big magnitude, single account, single day

One leaf row, large `ABS(drift)`, recent `business_day_end`. Usually a
**posting was rejected at the boundary but the stored balance update
fired anyway** — the upstream system thinks it sent the leg, the
ledger thinks it didn't. Drill from the leaf row into the *Transactions*
sheet (right-click → *View Transactions for this transfer*) and
look for a `status='Failed'` or `status='Rejected'` leg with the
matching amount near the same posting timestamp. Loop the upstream
team in immediately — this is the kind of drift that compounds if
it persists.

### Same account, same magnitude, many consecutive days

The drift never gets larger, never gets smaller, never goes away. This
is a **carry-forward of a prior incident that was never reconciled**
— a leg got dropped or duplicated weeks ago and nobody adjusted the
stored balance. The cumulative net of postings still says what it
always said; the stored balance still says what *it* always said; the
difference is locked in.

Find the first day the drift appears (sort by `business_day_start` asc
with the account filter pinned). Look at the postings around that
date for an entry whose `supersedes` is set — chances are the system
tried to correct itself but the correction didn't replay through
balance emission. Cross to *Supersession Audit* with the same account
filter to confirm.

### Wave across many accounts of one role

Drift Composition chart shows one role dominating; *Distinct Accounts*
KPI is high; the leaf table has tens or hundreds of rows clustered
on the same `business_day_end`. This is a **feed-wide failure** for
that role — a batch process for a whole family of accounts either
re-ran with stale input or skipped postings. The fix is upstream;
the dashboard's job here is to show the operator the *scope* so they
can scope the remediation correctly.

### Ledger drift without sub-ledger drift

The *Ledger Drift* table has rows but the *Sub-Ledger Drift* table is
empty (or the matching accounts are clean). The leaf accounts agree
with their own postings, but their parent's stored rollup does *not*
sum to the children's stored balances. This is a **parent-rollup
emission bug** — the leaf feeds posted correctly but the parent
control account's daily balance was computed from a stale snapshot.
This pattern is rarer than leaf drift but more painful, because the
top-of-house report (what the executives see) is wrong while the
underlying ledgers are right.

### Drift on `source='carried'` rows only

The leaf table has rows where `source` is `carried`, not `emitted`.
The stored balance for that day was [carried forward](../_glossary.md#carry-forward--sparse-cadence)
from the prior emit because no daily-balance row was reported. The
carried value disagrees with the computed value of postings that
*did* fire that day. This is the **sparse-cadence shape** — the institution doesn't
report a balance for that account every day, so a posting on a
non-emit day creates apparent drift against the carried prior-day
balance. Cross to *Balance Cadence Gap* (the CL.6 invariant sheet, if
exposed on this L2) to see whether the operator's expected emit
cadence matches what they're sending.

## What "no rows" means

A clean drift sheet means *every* internal account's stored balance
agrees with its postings on every day in the current window. That is
the steady-state expectation, not an edge case: drift is a violation
catcher, not a metric to be trended. If you see zero rows:

- **Confirm the matview is fresh.** Cross to *App Info* and check the
  *Matview Status* table's `drift` row. If `last_refresh_at` is more
  than a few minutes old AND new postings landed since, the data
  may be clean *as of the last refresh* but stale. The institution
  refreshes matviews on every ETL load; ad-hoc dashboard hits don't
  trigger one.
- **Check the date filter.** A very narrow date window can show zero
  on a day with no postings. Widen to the trailing 7 days; if you
  still get zero, the system is genuinely clean.
- **Don't celebrate yet.** Drift is one of ten L1 invariants. A clean
  Drift sheet next to a populated *Overdraft* or *Limit Breach* sheet
  means *that* invariant has work — `?` those sheets next.

If *App Info* shows `last_refresh_at` as null or the matview row
count as zero across the board, the L1 invariant pipeline didn't
run. That's an ops alert, not a "clean" signal.

## Cross-sheet drills

- **Sub-Ledger Drift table row → Daily Statement** (right-click → *View
  Daily Statement for this account-day*). Lands you on a per-account-
  per-day narrative of every posting and balance event with running
  computed balance — the canonical place to see *which* leg disagreed
  with the stored number.
- **Sub-Ledger Drift table row → Transactions** (right-click → *View
  Transactions for this transfer*). For when you know which transfer
  to dig into.
- **Drift Composition bar → Drift Timelines** (left-click on a bar).
  Pivots from "what's broken now" to "is this role spiking recurringly
  or once?". The Drift Timelines sheet shows Σ ABS(drift) per day per
  role; recurring vs one-off has very different remediation paths.
- **Ledger Drift table row → Sub-Ledger Drift** (left-click). Narrows
  the leaf table to children of this parent account so you can see
  whether the parent rollup bug is masking a leaf-feed problem
  underneath.

## Related handbook pages

- [Drift Timelines](drift-timelines.md) — the time-series companion;
  use it when you've established *what's* drifting and want to see
  *how often*.
- [Daily Statement](daily-statement.md) — per-account narrative;
  the destination of the row-level drill from this sheet.
- [Overdraft](overdraft.md) — the orthogonal SHOULD-constraint
  (`stored < 0`); a chronically-negative-but-internally-consistent
  account appears there but NOT here.
- [Supersession Audit](supersession-audit.md) — when the drift
  signature looks like a botched correction.

## QS parity notes

- **Count-distinct quirk.** The *Distinct Accounts* KPI on a QS-rendered
  deploy renders the row-count distinct-by-the-dimension instead of
  the COUNT(DISTINCT account_id) you wrote. App2 emits this correctly
  via `Measure.kind == "count"` SUM-of-1 workaround. See [quirks log
  §count-distinct-quirk-bl1](../../reference/quicksight-quirks.md).
- **URL-driven account dropdown.** When you arrive at this sheet via a
  drill from another app (e.g. Investigation's Money Trail), QS may
  filter the data correctly but show the *Account* dropdown still at
  *All* — the data is right, the control is lying. App2 doesn't have
  this defect. See [quirks log §dependent-dropdown-no-refresh](../../reference/quicksight-quirks.md).

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`,
`matview`, `account_role`, and the other project-specific terms.*
