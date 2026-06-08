# Completion expression

> **What this field controls.** The relative time window during which
> the L1 timeliness invariant expects every leg of the template's
> Transfer to be posted.

## What you're looking at

A single text input taking a relative-time expression — `business_day_end`
optionally plus or minus a duration. The expression is evaluated at
firing-time relative to the template's first leg firing.

## Grammar

```
completion ::= anchor [ ( "+" | "-" ) duration ]
anchor     ::= "business_day_end" | "settlement_day_end"
duration   ::= "<N>d" | "<N>h" | "<N>m"
```

Examples:

- `business_day_end` — every leg must post by the close of the same
  business day.
- `business_day_end+1d` — every leg must post by the close of T+1.
- `business_day_end+2d-1h` — close of T+2 minus one hour (rarely
  needed; useful for cutoff-driven settlement contracts).

The `business_day_end` anchor honors each account's
`business_day_offset` (CP), so a template that spans accounts with
different EOD cutoffs evaluates the anchor per-account.

## How L1 uses this

The L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
Timeliness matview emits one row per Transfer whose last leg posts
after the `completion` window. The Pending Aging sheet surfaces those
rows; the Daily Statement KPI strip rolls up the count.

`business_day_end+0d` (the trivial same-day shape) is the most common
choice for intraday rails. Multi-leg templates that span an ACH cycle
or a wire-and-back settlement typically pick `+1d` or `+2d`.

## Constraints

- The anchor is required. A bare duration (`+1d`) has no meaning
  without something to anchor to.
- Durations are signed integers with one of three units. Mixed-unit
  expressions (`+1d2h`) are not supported — pick the unit that makes
  the cutoff readable.
- A negative duration is rare but valid (cutoff before EOD).

The validator rejects unparseable expressions with a pointer at the
offending token.

## Related handbook pages

- [Chain children](chain-children.md) — the chain layer reuses the
  parent's `completion` window to gate child appearance.
- [Transfer key](transfer-key.md) — the join key the L1 layer uses
  to identify a Transfer before checking its completion window.

[Vocabulary](../_glossary.md)
