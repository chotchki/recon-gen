# Today's Exceptions

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

The 9am roll-up. UNION ALL across all 5 L1 invariant views
(`{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_ledger_drift`, `{{ l2_instance_name }}_overdraft`,
`{{ l2_instance_name }}_limit_breach`, `{{ l2_instance_name }}_expected_eod_balance_breach`)
scoped to the most recent business day in the data. Materialized
at refresh time so each visual reads a small precomputed table
rather than re-running the 5-branch UNION live.

The `magnitude` column is normalized per branch to a positive number
(`ABS(drift)` for drift / ledger_drift / expected_eod_breach;
`ABS(stored_balance)` for overdraft; `outbound_total - cap` for
limit_breach) so a sort-by-magnitude reads consistently across
check kinds.

??? example "Screenshot"
    ![Today's Exceptions](../screenshots/l1/l1-sheet-todays-exceptions.png)

## When to use it

First sheet to open every morning. The KPI answers "did anything
break overnight?"; the bar chart shows the error class shape; the
detail table sorts biggest-first so the loudest violations are at
the top.

## Visuals

- **Open Exceptions** (KPI) — total count of L1 SHOULD-constraint
  violations on today's business day.
- **Exceptions by Check Type** (BarChart, horizontal) — count per
  check kind. Spikes in one kind point at a recurring error class
  to investigate first.
- **Exception Detail** (Table) — every violation on today, sorted
  by magnitude DESC. `rail_name` and `account_parent_role` are
  NULL for branches that don't carry them.
- **Institution Context** (TextBox) — footer with the L2 instance's
  top-level description. Mirrors the Getting Started welcome at the
  bottom of the unified-view landing page.

## Drills

- **Left-click any `account_id`** → narrows the **Drift** sheet to
  that account (`pL1FilterAccount` parameter). Per the CLAUDE.md
  drill-direction convention: left-click moves you upstream, toward
  the per-invariant source of the violation.
- **Right-click → "View Daily Statement for this account-day"** →
  opens **Daily Statement** filtered to the clicked
  `(account_id, business_day)` for the per-leg walk. Per convention:
  right-click moves you downstream, deeper into the investigation.

## Filters

- **Date From / Date To** — universal date-range pickers (scoped
  on `business_day`).
- **Check Type** — multi-select dropdown over the 5 check kinds.
- **Account** — multi-select dropdown over `account_id`.
- **Transfer Type** — multi-select dropdown over `rail_name`.
