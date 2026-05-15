# Unbundled Aging

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

Posted transactions whose `bundle_id` is still NULL past their
rail's `max_unbundled_age` cap. An AggregatingRail's job is to pick
up these legs and group them into a Bundle; an unbundled leg older
than the rail's cadence means the bundler hasn't fired or is failing
to match.

Per validator R8, `max_unbundled_age` is only meaningful on rails
that appear in some AggregatingRail's `bundles_activity`. Rails
without the field are excluded by NULL cap.

??? example "Screenshot"
    ![Unbundled Aging](../screenshots/l1/l1-sheet-unbundled-aging.png)

## When to use it

End-of-day or end-of-week, depending on the typical bundling
cadence. If the bundler fires nightly, anything older than 1-2 days
is suspect. If it fires monthly (e.g., fee accrual), the threshold
shifts accordingly — the aging buckets reflect this.

## Visuals

- **Stuck Unbundled** (KPI) — count of Posted legs whose
  `bundle_id IS NULL` and live age has exceeded the rail's cap.
- **Stuck Unbundled by Age Bucket** (BarChart, horizontal) — 4
  number-prefixed buckets: `<1d`, `1-2d`, `2-7d`, `>7d`. Coarser
  than Pending Aging because `max_unbundled_age` is typically days
  rather than hours.
- **Stuck Unbundled Detail** (Table) — every stuck-Unbundled leg
  with rail / amount / posting / live age. `max_unbundled_age_seconds`
  is the rail's cap.

## Drills

- **Right-click any row → "View Transactions for this transfer"** —
  opens Transactions narrowed to the clicked `transfer_id`.

## Filters

- **Date From / Date To** — universal date-range pickers (on
  `posting`).
- **Account** — multi-select dropdown over `account_id`.
- **Transfer Type** — multi-select dropdown over `rail_name`.
- **Rail** — multi-select dropdown over `rail_name`.
