# Supersession Audit

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

Every logical row whose append-only `entry` column has more than one
version. Each rewrite carries a `supersedes` reason from L1's v1
vocabulary:

- **Inflight** — the row was rewritten while still in flight
  (typical: status flip, late metadata enrichment).
- **BundleAssignment** — the row was rewritten when an AggregatingRail
  picked it up and assigned a `bundle_id`.
- **TechnicalCorrection** — the row was rewritten because the
  original posting was wrong (amount fix, account swap, etc).

Reads from the BASE tables (`{{ l2_instance_name }}_transactions`,
`{{ l2_instance_name }}_daily_balances`), not the Current* matviews — by
definition Current* hides the prior entries we want to audit here.

??? example "Screenshot"
    ![Supersession Audit](../screenshots/l1/l1-sheet-supersession-audit.png)

## When to use it

Diagnostic deep-dive. High `TechnicalCorrection` volume signals a
feed problem upstream; high `Inflight` is normal in a busy bundling
cadence. The audit is also useful for cross-system reconciliation
(does our rewrite count match the producer system's known correction
count?).

## Visuals

- **Logical Keys with Supersession** (KPI) — count of distinct
  `transaction_id` values whose append-only `entry` column has more
  than one row.
- **Transactions Audit** (Table) — every entry of every superseded
  logical transaction, ordered by `(transaction_id, entry)` so the
  audit trail reads top-down per logical row. The `supersedes`
  column on the higher-entry row tells you why it exists.
- **Daily Balances Audit** (Table) — same shape on the
  `(account_id, business_day_start)` composite key.

## Drills

None outbound. Supersession Audit is itself the diagnostic deep-dive
endpoint; the analyst's next step is typically a copy-paste of the
logical key into a producer-system case file.

## Filters

- **Supersedes Reason** — narrow the transactions table to one cause
  class (Inflight / BundleAssignment / TechnicalCorrection). The
  daily-balances table doesn't get a paired filter — supersession on
  daily_balances is rare enough that a second control would be noise.

No date filter — audits want the full historical horizon.
