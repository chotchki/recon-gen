# Drift

> **What this sheet teaches.** Sub-ledger drift — the disagreement
> between an account's *stored* end-of-day balance (what the institution
> reported) and its *computed* balance (the cumulative net of every
> posted transaction through that day). Every row on this sheet is
> one violation of an L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
> SHOULD-constraint: the ledger does not agree with the postings that
> produced it.

## What you're looking at

The sheet opens on a strip of four KPIs across the top — *Leaf Account-Days
in Drift* (count of leaf account-day rows where stored ≠ computed), *Largest
Leaf Drift (anywhere in window)* (the peak single-row |drift| magnitude),
*Parent Account-Days in Drift* (count of parent account-day rows where stored
≠ sum of children) and *Largest Parent Drift (anywhere in window)* (the peak
single parent-account-day magnitude). Below the strip sit two side-by-side tables:
*Leaf Account Drift* ([individual posting accounts](../_glossary.md#parent--leaf-accounts)
— customer DDAs, merchant DDAs and other non-aggregate positions where stored
balance comes from direct feed emission) and *Parent Account Drift* (rollup
accounts — GL controls and pool masters whose stored balance is defined as the
sum of children). Filters across the top let you narrow by account and by
[account role](../_glossary.md#account-role).

## How to read the numbers

Both tables read from the L1 invariant [matviews](../_glossary.md#matview--materialized-view)
`<prefix>_drift` (leaf) and `<prefix>_ledger_drift` (parent). Each matview joins
the [carry-forward](../_glossary.md#carry-forward--sparse-cadence) stored balance
from `effective_balances` (the source of truth for "what the account said it was
at end-of-day", including carried-forward days when no balance was emitted) against
a `computed_*` balance and emits only rows where they disagree.

The columns are the same on both tables:

- `account_id`, `account_name`, `account_role` — identifying the cell
- `business_day_start` — the business day the imbalance is reported for, at the
  account's own timezone offset
- `stored_balance` — `effective_balances.effective_money` (shown in dollars;
  the matview stores it as cents, converted at the dataset read boundary)
- `computed_balance` —
  - For the leaf table: Σ `signed_amount` of every posted Money record whose
    `account_id = sb.account_id` through `business_day_start`
  - For the parent table: Σ `stored_balance` of all children of this parent
    account on the same `business_day_start`
- `drift` — `stored_balance − computed_balance` (signed; positive means the
  stored balance is HIGHER than computed)
- `source` — whether the stored value came from an emitted daily balance or was
  carried forward from a prior day

Both matviews filter `WHERE stored_balance <> computed_balance` and
[`account_scope = 'internal'`](../_glossary.md#account-scope-internal-vs-external),
so external accounts (banks, payment networks) never appear here — banks may
overdraft us but we MUST NOT misreport our own books.

The *Leaf Account-Days in Drift* KPI counts rows in the leaf table within the
current date filter. The *Largest Leaf Drift* KPI is the maximum `ABS(drift)`
value in the leaf table — paired with the count to prevent SUM-based aggregation
from masking individual drifts. *Parent Account-Days in Drift* and *Largest Parent
Drift* are the same measures over the parent table.

## Common patterns

### Single big magnitude, single account, single day

One leaf row, large `ABS(drift)`, recent `business_day_start`. Usually a
**posting was rejected at the boundary but the stored balance update
fired anyway** — the upstream system thinks it sent the leg, the
ledger thinks it didn't. Right-click the row → *View Daily Statement for this
account-day* and look for a `status='Failed'` or `status='Rejected'` leg with the
matching amount near the same posting timestamp. Loop the upstream team in
immediately — this is the kind of drift that compounds if it persists.

### Same account, same magnitude, many consecutive days

The drift never gets larger, never gets smaller, never goes away. This
is a **carry-forward of a prior incident that was never reconciled**
— a leg got dropped or duplicated weeks ago and nobody adjusted the
stored balance. The cumulative net of postings still says what it always
said and the stored balance still says what IT always said — the difference
is locked in.

Filter the *Account* dropdown to that account and sort by `business_day_start`
ascending to find the first day the drift appears. Look at the postings around
that date for an entry whose `supersedes` is set — chances are the system tried
to correct itself but the correction didn't replay through balance emission.
Cross to *Supersession Audit* with the same account filter to confirm.

### Wave across many accounts of one role

Use the *Account Role* filter to isolate the role; the leaf table shows tens or
hundreds of rows clustered on the same `business_day_start`. This is a **feed-wide
failure** for that role — a batch process for a whole family of accounts either
re-ran with stale input or skipped postings. The fix is upstream; the dashboard's
job here is to show the operator the SCOPE so they can target the remediation
correctly.

### Parent drift without leaf drift

The *Parent Account Drift* table has rows but the *Leaf Account Drift* table is
empty (or the matching accounts are clean). The leaf accounts agree with their own
postings, but their parent's stored rollup does NOT sum to the children's stored
balances. This is a **parent-rollup emission bug** — the leaf feeds posted correctly
but the parent control account's daily balance was computed from a stale snapshot.
This pattern is rarer than leaf drift but more painful, because the top-of-house
report (what executives see) is wrong while the underlying ledgers are right.

### Drift on `source='carried'` rows only

The leaf table has rows where `source` is `'carried'`, not `'emitted'`. The stored
balance for that day was [carried forward](../_glossary.md#carry-forward--sparse-cadence)
from the prior emit because no daily-balance row was reported. The carried value
disagrees with the computed value of postings that DID fire that day. This is the
**sparse-cadence shape** — the institution doesn't report a balance for that account
every day, so a posting on a non-emit day creates apparent drift against the carried
prior-day balance. Cross to *L1 Exceptions* and filter to the balance-cadence-gap
check to see whether the operator's expected emit cadence matches what they're sending.

## What "no rows" means

A clean drift sheet means EVERY [internal](../_glossary.md#account-scope-internal-vs-external)
account's stored balance agrees with its postings on every day in the current window.
That is the steady-state expectation, not an edge case: drift is a violation catcher,
not a metric to be trended. If you see zero rows:

- **Confirm the matview is fresh.** Cross to *App Info* and check the *Matview Status*
  table's `drift` and `ledger_drift` rows against the base-table rows. If a matview's
  `latest_date` LAGS the base tables' `latest_date`, the base tables moved forward but
  the matview didn't get refreshed since the last ETL load — the data is clean *as of
  the matview's `latest_date`* but stale relative to what landed after. The institution
  refreshes matviews on every ETL load; ad-hoc dashboard hits don't trigger one.
- **Check the date filter.** A very narrow date window can show zero on a day with no
  postings. Widen to the trailing 7 days; if you still get zero, the system is
  genuinely clean.
- **Don't celebrate yet.** Drift is one of twelve L1 invariants. A clean Drift sheet
  next to a populated *Overdraft* or *Limit Breach* sheet means THAT invariant
  has work — check those sheets next.

If *App Info* shows the matview row count as zero across the board, the L1 invariant
pipeline didn't run. That's an ops alert, not a "clean" signal. A NULL `latest_date`
is NOT that signal — it just means a matview with no natural date dimension (or a
custom matview added without a date column), so don't read it as staleness.

## Cross-sheet drills

- **Leaf Account Drift table row → Daily Statement** (right-click → *View Daily
  Statement for this account-day*). Lands you on a per-account-per-day narrative
  of every posting and balance event with running computed balance — the canonical
  place to see WHICH leg disagreed with the stored number.
- **Parent Account Drift table row → Daily Statement** (right-click → *View Daily
  Statement for this account-day*). Same as leaf, but for the parent account.

## Related handbook pages

- [Drift Timelines](drift-timelines.md) — the time-series companion; use it when
  you've established WHAT'S drifting and want to see HOW OFTEN.
- [Daily Statement](daily-statement.md) — per-account narrative; the destination
  of the row-level drill from this sheet.
- [Overdraft](overdraft.md) — the orthogonal SHOULD-constraint (`stored < 0`);
  a chronically-negative-but-internally-consistent account appears there but NOT
  here.
- [Supersession Audit](supersession-audit.md) — when the drift signature looks
  like a botched correction.

## QS parity notes

- **Count-distinct quirk.** Pre-v8 QuickSight rendered the "Distinct Accounts" KPI
  (row-count distinct-by-the-dimension) instead of the COUNT(DISTINCT account_id)
  the schema specified. App2 emits this correctly via `Measure.kind == "count"`
  SUM-of-1 workaround. See [quirks log §count-distinct-quirk-bl1](../../reference/quicksight-quirks.md).
- **URL-driven account dropdown.** When you arrive at this sheet via a drill from
  another app (e.g. Investigation's Money Trail), QS may filter the data correctly
  but show the *Account* dropdown still at *All* — the data is right, the control
  is lying. App2 doesn't have this defect. See [quirks log §dependent-dropdown-no-refresh](../../reference/quicksight-quirks.md).

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`,
`account_role`, `carry-forward` and the other project-specific terms.*
