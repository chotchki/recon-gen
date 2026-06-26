# Overdraft

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

Internal accounts holding negative money at end-of-day. The L1
invariant: no internal account holds a negative balance. Every row is
one violation; healthy = empty.

External counterparty accounts are excluded by the underlying view
(filtered to `account_scope = 'internal'`). The asymmetry is
intentional — banks may legitimately overdraft US; we MUST NOT
overdraft THEM.

See it live: https://recon-gen-spec.hotchkiss.io/

??? example "Screenshot"
    ![Overdraft](../screenshots/l1/l1-sheet-overdraft.png)

## When to use it

Daily, right after Drift. Overdraft and drift are the two table-stakes
invariants — neither should ever fire on a healthy production feed.

## Visuals

- **Account-Days in Overdraft** (KPI) — count of (account, day) rows
  with stored balance < 0.
- **Overdraft Violations** (Table) — one row per overdrawn
  account-day. Carries `account_id`, `account_name`, `account_display`,
  `account_role`, `account_parent_role`, `business_day_start`,
  `stored_balance`. Negative magnitude indicates how far below zero the
  account ended the day.

## Drills

- **Right-click any row → "View Daily Statement for this account-day"**
  — opens Daily Statement filtered to the clicked account and
  `business_day_start` to see what postings drove the account negative.

## Filters

- **Date From / Date To** — universal date-range pickers.
- **Account** — multi-select dropdown over `account_id`.
- **Account Role** — multi-select dropdown over `account_role`.
