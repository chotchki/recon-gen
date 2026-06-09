# Pending Aging

> **What this sheet teaches.** Pending transactions that have exceeded their
> rail's configured aging threshold — a time-based L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
> SHOULD-constraint violation indicating a posting stuck in an intermediate state past its
> expected settlement window.

## What you're looking at

A single KPI strip showing the total count of stuck-Pending transactions.
Below that sits *Stuck Pending by Age Bucket*, a horizontal bar chart
breaking the population across five age bands (0–6h, 6–24h, 1–3d, 3–7d, >7d),
stacked by rail so you can see which transfer pattern each band contains.
Below the chart is the *Stuck Pending Detail* table listing every stuck
transaction with account, transfer, rail, amount, and age in seconds. Filters
at the top let you narrow by account, transfer type, and rail.

## How to read the numbers

Each row on this sheet reads from the L1 invariant [matview](../_glossary.md#matview--materialized-view)
`<prefix>_stuck_pending` (a [matview](../_glossary.md#matview--materialized-view) of
the L1 account-integrity invariants). The matview joins `<prefix>_current_transactions` against the L2
instance's per-[rail](../_glossary.md#rail) `max_pending_age` configuration
and emits only rows where `status='Pending'` AND `age_seconds >
max_pending_age_seconds`.

The columns are:

- `account_id`, `account_name`, `account_role` — identifying the posting
  account
- `transfer_id` — which logical transfer this leg belongs to
- `rail_name` — which [rail](../_glossary.md#rail) (ACH, wire, check, etc.)
  governs this leg's aging cap
- `amount_money`, `amount_direction` — the leg's signed value and direction
  (Debit / Credit)
- `posting` — when the leg entered the system in Pending state
- `max_pending_age_seconds` — the [rail](../_glossary.md#rail)'s configured
  cap (embedded at schema-emit time from the L2 instance)
- `age_seconds` — the live age in seconds as of the last matview refresh
- `stuck_pending_aging_bucket` — a CASE-computed band label (1: 0-6h, 2:
  6-24h, 3: 1-3d, 4: 3-7d, 5: >7d) for charting and analysis

The *Stuck Pending* KPI counts `transaction_id` rows; a spike at 0–6h means
a recent batch of legs posted but haven't cleared. A right-skewed distribution
(most rows in the >7d bucket) signals a slow drift — legs are aging without
clearing, likely because the downstream clearing rail isn't firing or is
matching slowly.

## Common patterns

### Spike at 0–6h bucket only

Recent spike, typically 10–50 stuck legs all posted within the last few hours
on a single [rail](../_glossary.md#rail). Usually a **batch posting that
hasn't cleared yet** — the leg is still in-flight and not yet a failure
condition. Cross to the *Transactions* sheet (right-click → *View
Transactions*) to see the full legs of the transfers involved and confirm
they're all from the same originating batch.

### Skew toward >7d bucket

Majority of rows are 7+ days old, same accounts, same [rail](../_glossary.md#rail).
This is a **slow-drift pattern** — legs that posted correctly but the
downstream clearing process (typically an aggregator [rail](../_glossary.md#rail)
or a bank settlement window) isn't picking them up. The matview's live age
calculation means they're aging in real time; if the oldest row shows 30 days,
the leg is 30 days old and stuck. Cross to the *Transactions* sheet and look
for a `status='Pending'` leg with no corresponding `status='Posted'` sibling
on the clearing [rail](../_glossary.md#rail) — that's the missing settlement
leg.

### Same transfer across multiple age buckets

A single `transfer_id` appears in the table multiple times with different age
values — typically 2–4 rows spanning 0–6h and 6–24h. This is a **multi-leg
transfer where different legs are aging at different rates** — one leg
cleared quickly, another is still stuck. This is usually a cross-[rail](../_glossary.md#rail)
transfer where one [rail](../_glossary.md#rail)'s settlement cycle is longer
than the other. Right-click one row → *View Transactions* to see the entire
transfer's leg roster and spot which [rail](../_glossary.md#rail) is lagging.

### Wave across one role

Multiple accounts in one `account_role` (e.g., all CustomerDDA roles) with
stuck legs on the same [rail](../_glossary.md#rail), same age bucket. This is
a **feed-wide aging failure** for that role on that [rail](../_glossary.md#rail).
The fix is typically upstream — a posting batch didn't fire, or the aggregator
[rail](../_glossary.md#rail) skipped a cycle. Check with the ops team
responsible for that [rail](../_glossary.md#rail) to see if the posting or
bundling process is stalled.

## What "no rows" means

A clean Pending Aging sheet — zero rows — means *every* Pending leg in the
system is younger than its [rail](../_glossary.md#rail)'s `max_pending_age`
cap. This is the steady-state expectation: Pending transactions are
intermediate states meant to be temporary. If you see zero rows:

- **Confirm the matview is fresh.** Cross to *App Info* and check the
  *Matview Status* table's `stuck_pending` row. If `last_refresh_at` is more
  than a few minutes old AND new postings landed since, the data may be clean
  *as of the last refresh* but stale. The institution refreshes matviews on
  every ETL load; ad-hoc dashboard hits don't trigger one.
- **Check the filter windows.** A very narrow account, transfer type, or [rail](../_glossary.md#rail)
  filter can show zero on a day with stuck legs outside your filter. Widen to
  "All Accounts", "All Transfer Types", and "All Rails" to see the full picture.
- **Don't assume all-clear.** Pending Aging is one of twelve L1 invariants. A
  clean Pending Aging sheet next to a populated *Unbundled Aging* or *Drift*
  sheet means the *other* invariants have open violations — `?` those sheets
  next.

If *App Info* shows `last_refresh_at` as null or the matview row count as
zero across the board, the L1 invariant pipeline didn't run. That's an ops
alert, not a "clean" signal.

## Cross-sheet drills

- **Stuck Pending Detail table row → Transactions** (right-click → *View
  Transactions for this transfer*). Lands you on the raw posting ledger
  filtered to every leg of the stuck transfer — you can see the Pending leg
  alongside any Posted clearing legs to spot which [rail](../_glossary.md#rail)
  is lagging.

## Related handbook pages

- [Unbundled Aging](unbundled-aging.md) — the sibling time-based invariant
  (Posted legs without a bundle); use when stuck legs have moved past Pending
  and are now stuck in the aggregation step.
- [Drift](drift.md) — the reconciliation invariant (stored vs computed balance);
  a stuck Pending leg that never clears will eventually produce drift if the
  stored balance update already fired.
- [Transactions](transactions.md) — the raw posting ledger; the drill destination
  to see the full transfer roster for any stuck leg.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `rail`,
`matview`, and the other project-specific terms.*
