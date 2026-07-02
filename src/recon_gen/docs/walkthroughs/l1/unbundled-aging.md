# Unbundled Aging

*Per-sheet walkthrough — L1 Reconciliation Dashboard.*

## What the sheet shows

Posted transactions whose `bundle_id` is still NULL past their rail's
`max_unbundled_age` cap. An AggregatingRail's job is to pick these legs
up and group them into a Bundle; an unbundled leg older than the rail's
cadence means the bundler hasn't fired or isn't matching.

Per validator R8, `max_unbundled_age` ONLY matters on rails that appear
in some AggregatingRail's `bundles_activity`. A rail without the field
emits a NULL cap and drops out by construction.

??? example "Screenshot"
    ![Unbundled Aging](../screenshots/l1/l1-sheet-unbundled-aging.png)

[See it live](https://recon-gen-spec.hotchkiss.io/)

## When to use it

End-of-day or end-of-week, set by the bundling cadence. A nightly
bundler makes anything older than 1-2 days suspect; a monthly one (fee
accrual, say) pushes the threshold out, and the aging buckets move with
it.

## Visuals

- **Stuck Unbundled** (KPI) — count of Posted legs whose
  `bundle_id IS NULL` and live age has passed the rail's cap. Healthy =
  0. (The bundled demo plants stuck rows on purpose, so a demo deploy
  reads non-zero by design.)
- **Stuck Unbundled Exposure** (KPI) — SUM of `amount` across those
  stuck legs. The dollar side of the gap: how much money sits
  unrolled-up past its bundling cap.
- **Stuck Unbundled by Age Bucket** (BarChart, horizontal) — 4
  number-prefixed buckets (`<1d`, `1-2d`, `2-7d`, `>7d`), stacked by
  rail. Coarser than Pending Aging because `max_unbundled_age` runs in
  days, not hours. A right skew (`>2d`, `>7d`) means the bundler hasn't
  fired for those rails in a while.
- **Stuck Unbundled Detail** (Table) — every stuck-Unbundled leg with
  rail / amount / posting / live age. `max_unbundled_age_seconds` is the
  rail's cap, inlined at view-emit time from the L2.

## Drills

- **Right-click any row → "View Transactions for this transfer"** —
  opens Transactions narrowed to the clicked `transfer_id`. The drill
  widens the destination's date filter to all-time, since this sheet is
  a current-state view with no date picker of its own.

## Filters

- **Account** — multi-select dropdown over `account_id`.
- **Transfer Type** — multi-select dropdown over `rail_name`.
- **Rail** — multi-select dropdown over `rail_name`.
