# Bundles activity

> **What this field controls.** Which rails or templates this
> *aggregating* rail bundles into its periodic sweep.

## What you're looking at

A chip-list multi-select whose option universe is every rail + every
transfer template in the L2 instance. Type to filter, click to add,
drag to reorder, `×` to remove. The selection is the canonical
membership set — the order shapes how the L1 invariants walk per-firing
attribution but doesn't change correctness.

## When this field applies

Bundles-activity only renders when the rail's `aggregating` flag is
`true`. An aggregating rail fires on a cadence (intraday-2h, daily-eod,
weekly-monday, etc.) and represents a sweep that nets activity from
the bundled-children rails into a single transfer.

The canonical example is an ACH origination sweep: many individual
customer-debit firings on `CustomerACHDebit` get bundled into one
daily settlement firing on `ACHOriginationSettlement`. The settlement
rail's `bundles_activity` is `[CustomerACHDebit]`; its cadence is
`daily-eod`.

## How L1 uses this

The `<prefix>_stuck_unbundled` matview consumes bundles-activity:
for every firing on a bundled-child rail older than `max_unbundled_age`
and without a matching aggregating-rail firing in the cadence window,
the matview emits a row. The L1 Unbundled Aging sheet renders that
row set; L1 Exceptions rolls it up under the `stuck_unbundled`
check-type branch.

## Constraints

- Every member must be a non-aggregating rail OR a transfer template.
  Aggregating-bundles-aggregating chains are not supported (validator
  V3a — the cadence layer wouldn't compose).
- The L2 layer doesn't enforce a maximum count; in practice an
  aggregating rail bundles 1–3 children.
- Empty `bundles_activity` on an aggregating rail is rejected by
  validator V3b — the rail has nothing to sweep.

## Related handbook pages

- [Max unbundled age](max-unbundled-age.md) — the per-rail aging
  window the Unbundled Aging matview compares against.

[Vocabulary](../_glossary.md)
