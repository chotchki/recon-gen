# Limit Breach

> **What this sheet teaches.** Transfer limit violations in either
> direction — the cells where an account's cumulative daily outbound
> OR inbound flow exceeded the institution's per-account, per-rail
> limit cap. A `direction` column on each row tells you which side
> tripped.

## What you're looking at

A single KPI across the top — *Breaches in Window* — sits above a reference
section called *Configured Caps*, which lists the per-rail daily limits that
the view below enforces. The detail table, *Limit Breach Detail*, shows every
(account, day, rail, direction) cell where the cumulative flow on the
breaching side exceeded the cap. Columns pair `outbound_total` and `cap`
side-by-side so you can read the magnitude of each breach in-line. A
universal date-range filter across the top narrows all rows to the selected
window.

## How to read the numbers

The sheet reads from the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
invariant [matview](../_glossary.md#matview--materialized-view)
`<prefix>_limit_breach`. The matview joins current-state postings against
the L2 instance's declared [LimitSchedules](../_glossary.md#account-role)
(per-[rail](../_glossary.md#rail), per-parent-[account_role](../_glossary.md#account-role)
daily caps, embedded inline at schema-emit time) and emits only rows where
the cumulative flow breached the cap.

The matview runs two parallel UNION branches:

- **Outbound branch** — sums `ABS(amount_money)` where `amount_direction = 'Debit'`
  (classic per-rail send cap) and `status = 'Posted'`, grouped by
  `account_id`, `business_day` (the trading day the transaction posted),
  `rail_name`, and `account_parent_role`. Only internal accounts (rows where
  `account_scope = 'internal'`) surface here.
- **Inbound branch** — sums `ABS(amount_money)` where `amount_direction = 'Credit'`
  (receive-side volume threshold, typically for AML / structuring detection
  per AB.1) and `status = 'Posted'`, grouped identically to Outbound.

Both branches carry:

- `outbound_total` — cumulative `SUM(ABS(amount_money))` for all legs on the
  breaching side during that day (in dollars; converted from the matview's
  integer cents). Despite the name, this column holds either outbound or
  inbound totals depending on the `direction` value.
- `cap` — the daily limit (in dollars, resolved from L2's LimitSchedules at
  schema-emit time; inlined as CASE branches in the matview so no JSON-path
  lookups happen at query time)
- `direction` — literal string `'Outbound'` or `'Inbound'`, set by the branch

The outer filter `WHERE cap IS NOT NULL AND outbound_total > cap` ensures only
breaches surface — cells where the configured cap exists AND was exceeded.

The *Breaches in Window* KPI counts the rows in the detail table within the
current date filter. Zero means no rule violations in the visible date range —
the unambiguous healthy state.

## Common patterns

### Single high outbound day on one account

One row, one account, one rail, one business day, `outbound_total` substantially
above the `cap`. This is a **legitimate transfer spike that tripped the limit**
— a customer moved more money than usual on that day. The breach itself is
honest (the limit worked); the question is whether the account's configuration
is too tight or whether the spike was an anomaly. Cross to *Daily Statement*
(right-click → *View Daily Statement for this account-day*) and scan that
day's postings to see whether the spike was planned (e.g., a monthly payroll
run) or unexpected.

### Many accounts on same day, same rail, same direction

Multiple rows, same `business_day`, same `rail_name`, same `direction`, but
different `account_id` values. This is a **bulk event on that rail** —
possibly a batch process, a settlement window, or a scheduled transfer run that
pushed multiple customers over their caps simultaneously. The breaches are
likely correlated. Check whether the L2 instance's limit caps are correctly
tuned for the expected transaction cadence on that rail, or whether there's a
systemic overage in that traffic class.

### Inbound breaches only (direction='Inbound')

The table shows only `direction = 'Inbound'` rows; `direction = 'Outbound'` is
clean. This is an AML / structuring threshold violation, not a send-cap breach.
The `cap` is an inbound volume threshold (per AB.1) configured on the account's
parent role for the rail. Inbound breaches are typically investigated by
compliance rather than operations — loop in your AML team.

### Zero rows with non-zero cap configuration

The *Configured Caps* reference box shows active limit schedules but the detail
table is empty. This is the **healthy state** — every account stayed within its
cap on every day in the window. Don't celebrate yet; check the matview
freshness on *App Info* to confirm `last_refresh_at` is recent (within the
last few minutes of the most recent ETL load). If the matview is fresh and
the caps are configured, zero rows means genuine compliance with the limits.

## What "no rows" means

An empty Limit Breach sheet means every internal account on every rail stayed
within its configured daily cap throughout the window. That is the steady-state
expectation, not an edge case:

- **Confirm the matview is fresh.** Cross to *App Info* and check the
  *Matview Status* table's `limit_breach` row. If `last_refresh_at` is more
  than a few minutes old AND new postings landed since, the data may be clean
  *as of the last refresh* but stale. Matviews refresh on every ETL load;
  ad-hoc dashboard hits do not trigger one.
- **Check the date filter.** A very narrow date window can show zero on a day
  with light traffic. Widen to the trailing 7 days; if you still get zero, the
  system is genuinely compliant.
- **Verify the caps are configured.** Scroll up to the *Configured Caps*
  section. If it lists no schedules or says "No limit schedules configured,"
  the matview returns zero rows by construction — there are no caps to breach.

If *App Info* shows `last_refresh_at` as null or the matview row count as zero
across the board, the L1 invariant pipeline didn't run. That's an ops alert,
not a "clean" signal.

## Cross-sheet drills

- **Limit Breach Detail table row → Daily Statement** (right-click → *View
  Daily Statement for this account-day*). Lands you on the per-account-day
  narrative showing every posting that day with running balances — the place
  to inspect all legs that contributed to the breach on that specific day.

## Related handbook pages

- [Daily Statement](daily-statement.md) — per-account-day narrative; the
  destination of the drill from this sheet.
- [Overdraft](overdraft.md) — a related L1 SHOULD-constraint (stored balance
  sign check) orthogonal to limit caps.
- [L1 Exceptions](exceptions.md) — the unified roll-up of all 10 L1
  invariant violations; limit breach is one of the check types surfaced there.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`,
`account_role`, `rail`, and the other project-specific terms.*
