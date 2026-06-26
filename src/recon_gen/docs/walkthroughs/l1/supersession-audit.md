# Supersession Audit

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

Every logical row whose append-only `entry` column carries more than one
version. Each rewrite stamps a `supersedes` reason from L1's v1
vocabulary:

- **Inflight** — rewritten while still in flight (typical: status flip
  or late metadata enrichment).
- **BundleAssignment** — rewritten when an AggregatingRail picked the row
  up and assigned a `bundle_id`.
- **TechnicalCorrection** — rewritten because the original posting was
  wrong (amount fix, account swap, etc).

Reads from the BASE tables (`{{ l2_instance_name }}_transactions`,
`{{ l2_instance_name }}_daily_balances`), NOT the Current* matviews — by
definition Current* hides the prior entries we want to audit here.

??? example "Screenshot"
    ![Supersession Audit](../screenshots/l1/l1-sheet-supersession-audit.png)

See it live: https://recon-gen-spec.hotchkiss.io/

## When to use it

Diagnostic deep-dive. High `TechnicalCorrection` volume signals a feed
problem upstream; high `Inflight` is normal in a busy bundling cadence.
Also useful for cross-system reconciliation — does our rewrite count
match the producer system's known correction count?

## Visuals

Three KPIs across the top, then two audit tables.

- **Logical Keys (Transactions) with Supersession** (KPI) — count of
  distinct `transaction_id` values whose append-only `entry` column has
  more than one row. Unit is distinct KEYS.
- **Supersession $ Exposure** (KPI) — sum of `|amount|` across superseded
  transaction entries. The dollar magnitude of the audit surface; counts
  alone leave "how much money do these revisions move?" open.
- **Supersession Rows with No Reason** (KPI) — count of higher-`entry`
  rows whose `supersedes` reason is blank. Unit is ROWS not keys (one key
  may hold several no-reason rows). Target is 0 — every supersession
  SHOULD declare its cause per the L1 SPEC.
- **Transactions Audit** (Table) — every entry of every superseded
  logical transaction, ordered by `(transaction_id, entry)` so the audit
  trail reads top-down per logical row. The `supersedes` column on the
  higher-entry row tells you why it exists.
- **Daily Balances Audit** (Table) — same shape on the
  `(account_id, business_day_start)` composite key. The `money` column
  changing across entries is the trail for an end-of-day re-statement.

## Drills

Three outbound, all on the table cells:

- **Transactions Audit, transaction_id (left-click)** — same-sheet
  self-filter: focus the audit on that one transaction's full entry
  trail. It's a control-write, not a URL nav, so it dodges the QS
  URL-param-no-control-sync quirk. Clear it via the Transaction ID
  dropdown to return to all.
- **Transactions Audit, transfer_id (right-click)** — cross-sheet to the
  Transactions sheet's legs-of-this-transfer view. The Current* sheet is
  max-entry-only, so a `transaction_id` lands on a single useless row
  there — hence `transfer_id` for that hop.
- **Daily Balances Audit, account_id (right-click)** — cross-sheet to the
  Daily Statement for that account-day.

## Filters

- **Supersedes Reason** — narrow the transactions table to one cause
  class (Inflight / BundleAssignment / TechnicalCorrection).
- **Reason Provided** — isolate the policy-violation rows (a higher-entry
  supersession lacking a reason) from rows that carry one. A distinct
  axis from Supersedes Reason: this narrows by PRESENCE, not by cause
  class.
- **Transaction ID** — pick one superseded transaction to focus its
  trail. Shares the param the `transaction_id` cell drill writes — the
  drill focuses, this dropdown is the discoverable picker plus the clear
  affordance.

The daily-balances table gets NO paired filter — supersession on
daily_balances is rare enough that a second control would be noise.

No date filter — audits want the full historical horizon.
