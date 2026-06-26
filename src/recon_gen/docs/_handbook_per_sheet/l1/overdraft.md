# Overdraft

> **What this sheet teaches.** Internal account overdrafts — the disagreement
> with the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
> SHOULD-constraint that no internal account holds a negative stored balance at
> end-of-day. Every row on this sheet is one violation: an internal account that
> ended a business day with `stored_balance < 0`.

## What you're looking at

A single KPI — *Account-Days in Overdraft* — opens the sheet, counting the
internal-account day-rows that hold negative balances in the current date
window. Below it sits an *Overdraft Violations* table listing every account-day
where `stored_balance < 0`, with the account identifier, role, business day
and the negative amount. Filters across the top let you narrow by date range
(universal across the L1 app), by specific account and by account role.

## How to read the numbers

The table reads from the L1 invariant [matview](../_glossary.md#matview--materialized-view)
`<prefix>_overdraft`. The matview pulls data from `effective_balances` (the source
of truth for "what the account said it was at end-of-day", including [carried-forward](../_glossary.md#carry-forward--sparse-cadence)
days when no balance was emitted), and emits only rows where `account_scope = 'internal'`
and `effective_money < 0`.

The visible columns are:

- `account_id`, `account_name`, `account_role` — identifying the account cell
- `account_parent_role` — the parent [account_role](../_glossary.md#account-role)
  if this account is a leaf in a hierarchy
- `business_day_start` — the start of the business day window the overdraft is
  reported for, at the account's own offset (displayed at SECOND granularity to
  preserve the per-account boundary timestamp)
- `stored_balance` — `effective_balances.effective_money` converted to dollars
  (negative value; underlined on the sheet to signal the constraint violation)

The matview also tracks a `source` field (whether the balance was `emitted` or `carried` from a prior day) and a `business_day_end` column closing the window at day grain, but neither is displayed in the base table.

The *Account-Days in Overdraft* KPI counts rows in the matview within the current
date filter. External accounts (banks, payment networks) never appear here — the
matview filters to `account_scope = 'internal'` by construction, because the
institution's own accounts MUST NOT overdraft; external counterparties may.

## Common patterns

### Fresh overdraft on a single account, single day

One or two rows, recent `business_day_start`, a specific `account_id`, negative
magnitude. This is a **single-event overdraft** — the account dipped below zero
on one day. Right-click to *View Daily Statement for this account-day* to see
whether a debit posted without a matching inbound credit that day, or whether a
sweep / balance transfer failed to land. If the account is a sweep-target or a
liquidity hub, ping the Operations team to confirm the sweep ran; if it's a
customer DDA, check the upstream feed for a rejection on the expected inbound leg.

### Same account, multiple consecutive days, non-zero magnitude

The overdraft persists across several days with the same negative magnitude
(e.g., **−$50,000.00** on days 1, 2, 3). This is a **carry-forward of a prior
deficit** — the account went negative on day 1, and because it received no new
postings to correct it, the [carried-forward](../_glossary.md#carry-forward--sparse-cadence)
balance on days 2+ still shows the same deficit. The root cause happened on day 1
(or earlier). Find the first day the overdraft appears in the table; right-click
→ *View Daily Statement* for that day and look for a posting that should have
landed but didn't, or a rejected leg that left the balance in deficit.

### Many accounts of one role, all on the same day

The table has rows clustered on the same `business_day_start`, across many different
`account_id` values, all sharing the same `account_role`. This is a **feed-wide
liquidity event** — a whole role (e.g., all merchant DDAs, or all customer sweep
targets) went short on the same day. Usually means a high-volume transfer leg
(an aggregator [rail](../_glossary.md#rail) bundle, a wire sweep, a batch settlement)
posted without sufficient pre-funding. Check the upstream rail (ACH aggregation,
wire settlement, etc.) to confirm the inbound funding landed AFTER the outbound
batch fired; if timing is correct, loop the treasury team in to rebalance.

### Overdraft on sparse-emit account, many consecutive days

The `source` field is `carried` (not `emitted`), and the row repeats for many
days in a row — the account's last reported balance was negative, and no new
balance was emitted since, so every non-emit day carries forward the same deficit.
This is a **known-issue pattern on sparse-cadence accounts** — the institution
doesn't report balances daily, so a negative balance from a prior emit "sticks"
across all the days until the next emit. Right-click the FIRST row to
*View Daily Statement* for the first `business_day_start` on which `source='emitted'`
to see what actually happened; if the account has posted activity that would have
cured the deficit, but the institution didn't emit a new balance to capture it,
check the balance-emission cadence configuration for that account [role](../_glossary.md#account-role).

## What "no rows" means

A clean overdraft sheet means EVERY internal account's stored balance was
≥ $0.00 at the end of every business day in the current window. That is the
steady-state expectation, not an edge case: overdraft is a SHOULD-constraint
catcher, not a metric to be trended. If you see zero rows:

- **Confirm the matview is fresh.** Cross to *App Info* and check the *Matview
  Status* table's `overdraft` row. If `last_refresh_at` is more than a few
  minutes old AND new postings landed since, the data may be clean *as of the
  last refresh* but stale. The institution refreshes matviews on every ETL load;
  ad-hoc dashboard hits don't trigger one.
- **Check the date filter.** A very narrow date window can show zero on a day
  with no postings. Widen to the trailing 7 days; if you still get zero, no
  internal account was overdrafted in that window.
- **Don't celebrate yet.** Overdraft is one of twelve L1 invariants. A clean
  Overdraft sheet next to a populated *Drift* or *Limit Breach* sheet means
  THAT invariant has work — `?` those sheets next.

If *App Info* shows `last_refresh_at` as null or the matview row count as zero
across the board, the L1 invariant pipeline didn't run. That's an ops alert, not
a "clean" signal.

## Cross-sheet drills

- **Overdraft Violations table row → Daily Statement** (right-click → *View Daily
  Statement for this account-day*). Lands you on a per-account-per-day narrative
  of every posting and balance event with running balance — the place to see *why*
  the account went negative that day and what postings could have prevented it.

## Related handbook pages

- [Drift](drift.md) — the orthogonal SHOULD-constraint (does stored agree with
  cumulative postings?); a chronically-negative-but-internally-consistent account
  appears here but NOT on the Drift sheet.
- [Daily Statement](daily-statement.md) — per-account narrative; the destination
  of the row-level drill from this sheet.
- [Limit Breach](limit-breach.md) — another account-level constraint; overdraft
  and limit breach often co-occur on high-volume accounts.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`,
`account_role`, [rail](../_glossary.md#rail), [carry-forward](../_glossary.md#carry-forward--sparse-cadence)
and the other project-specific terms.*
