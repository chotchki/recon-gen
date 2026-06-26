# Completion expression

> **What this field controls.** The relative time window during which
> the L1 timeliness invariant expects every leg of the template's
> Transfer to be posted.

## What you're looking at

A single text input taking a completion expression from a closed
vocabulary — `business_day_end`, an optional `+Nd` day shift on it,
`month_end` or a `metadata.<key>` reference. The window is evaluated
at firing-time relative to the template's first leg firing.

## Grammar

```
completion ::= "business_day_end"
             | "business_day_end" "+" N "d"
             | "month_end"
             | "metadata." key
N          ::= non-negative integer
key        ::= a metadata field name
```

Examples:

- `business_day_end` — every leg must post by the close of the same
  business day.
- `business_day_end+1d` — every leg must post by the close of T+1.
- `business_day_end+2d` — close of T+2.
- `month_end` — every leg must post by the close of the calendar month.
- `metadata.ach_settlement_date` — the deadline is read from a metadata
  field on the Transfer (e.g. an ACH settlement date the rail carries),
  not from a fixed business-day offset.

The `business_day_end` anchor honors each account's
`business_day_offset`, so a template that spans accounts with
different EOD cutoffs evaluates the anchor per-account.

## How L1 uses this

The L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
`<prefix>_stuck_pending` matview emits one row per Transfer leg
still in pending status after the `completion` window has closed.
The Pending Aging sheet surfaces those rows; L1 Exceptions rolls
them up under the `stuck_pending` check-type branch.

`business_day_end` (the same-day shape) is the most common choice for
intraday rails. Multi-leg templates that span an ACH cycle or a
wire-and-back settlement typically pick `+1d` or `+2d`.

## Constraints

- The `+Nd` shift attaches only to `business_day_end`. A bare `+1d`
  has nothing to anchor to, and `month_end` / `metadata.<key>` take no
  shift.
- The shift is a non-negative whole number of days. There is no `-`
  sign and no hour or minute unit — the v1 vocabulary is day-grained.
- A `metadata.<key>` deadline must name a metadata key the rail
  actually carries, or the Transfer has nothing to read the cutoff from.

The validator rejects expressions outside this vocabulary with a
pointer at the offending token.

## Related handbook pages

- [Chain children](chain-children.md) — the chain layer reuses the
  parent's `completion` window to gate child appearance.
- [Transfer key](transfer-key.md) — the join key the L1 layer uses
  to identify a Transfer before checking its completion window.

[Vocabulary](../_glossary.md)
