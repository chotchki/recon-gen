# Metadata keys

> **What this field controls.** Which metadata field names the rail's
> transactions carry, and (via the embedded value-examples picker)
> what sample values the demo seed generator cycles through.

## What you're looking at

A chip-list picker. Each chip is one declared metadata key. The
typeahead below the chip list pulls from the L2-wide union of every
rail's `metadata_keys` plus a canonical fallback set
(`ach_trace_number`, `wire_imad`, `swift_uetr`, `disbursement_id`,
etc.). You can also type a brand-new key — the chip accepts
free-form input.

Inside each chip is a small inline text input for the
`metadata_value_examples` for that key. Enter values
comma-separated; the demo seed cycles through them when emitting
transactions on this rail.

## How L1 + the demo seed use this

The L1 layer treats `metadata_keys` as the rail's required-metadata
contract: every transaction posted to this rail must carry every
declared key in its `metadata` JSON. Missing keys surface as L1
PostedRequirements breaches.

The demo seed generator uses the per-key
`metadata_value_examples` to populate the JSON realistically. Empty
examples fall back to a synthetic-per-rail pattern
(`<rail>-firing-<seq>`); declaring real-shaped sample values
(`abc12345, def67890` for an `ach_trace_number`) makes the demo
output read like the real system.

## Interaction with transfer-key

If this rail is a leg of a transfer template, the template's
`transfer_key` names which metadata keys group leg firings into the
shared Transfer. Every key listed in the template's `transfer_key`
MUST appear in this rail's `metadata_keys` (validator R12 catches
mismatches at save time). The library auto-derives transfer-key
entries as `PostedRequirements` on every leg rail.

## Why a value-examples picker per chip

The earlier form had two separate fields — a textarea of metadata
keys and a parallel textarea of values. Operators routinely got the
two out of sync (added a key without examples, deleted a key while
its examples lingered). The per-chip embedded picker means the two
fields can never disagree by construction.

## Related handbook pages

- [Transfer key](transfer-key.md) — the metadata-key fields that
  drive Transfer-grouping; must be a subset of every leg rail's
  metadata-keys.
- [Bundles activity](bundles-activity.md) — aggregating rails use
  their bundled children's metadata-keys to derive the cadence
  attribution.

[Vocabulary](../_glossary.md)
