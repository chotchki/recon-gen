# Drift

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

Stored vs computed balance disagreements at end-of-day. Two scopes:

- **Leaf account drift** — individual posting accounts whose stored
  balance disagrees with the cumulative net of every posted Money
  record through that BusinessDay.
- **Parent account drift** — parent accounts whose stored balance
  disagrees with the sum of their child accounts' stored balances.

Both tables only show rows where stored ≠ computed. Each row IS a
SHOULD-constraint violation; healthy = empty.

??? example "Screenshot"
    ![Drift](../screenshots/l1/l1-sheet-drift.png)

[See it live](https://recon-gen-spec.hotchkiss.io/)

## When to use it

Daily. A non-zero count on either KPI means the feed diverged from the
underlying ledger or a child posting didn't roll up correctly to its
parent.

## Visuals

- **Internal Accounts in Scope** — TextBox listing every internal
  account the L2 instance declares + its role + L2-supplied prose. Sets
  the universe drift can surface against.
- **Leaf Account-Days in Drift** (KPI) + **Parent Account-Days in
  Drift** (KPI) — count of distinct (account, day) violations per
  scope.
- **Largest Leaf Drift** (KPI) + **Largest Parent Drift** (KPI) —
  max |drift| anywhere in the window, each a currency sibling to its
  count. The count alone reads as "no problem" at zero (the cold-read
  footgun this pair exists to kill); the magnitude says HOW BAD the
  worst drift is even when the count is small.
- **Leaf Account Drift** (Table) — one row per (leaf account, day)
  cell where stored ≠ computed. Carries `account_id`, `account_name`,
  `account_display`, `account_role`, `account_parent_role`,
  `business_day_start`, `stored_balance`, `computed_balance`, `drift`.
- **Parent Account Drift** (Table) — same shape minus
  `account_parent_role` (parents ARE the parents).

## Drills

- **Right-click any row → "View Daily Statement for this account-day"**
  — opens Daily Statement filtered to the clicked
  `(account_display, business_day_start)` for the per-leg walk.

## Filters

- **Date From / Date To** — universal date-range pickers, default last
  7 days.
- **Account** — single-select dropdown over `account_display` (the
  `<name> (<id>)` value).
- **Account Role** — single-select dropdown over `account_role`. Both
  filters narrow both tables (`ALL_DATASETS` scope).
