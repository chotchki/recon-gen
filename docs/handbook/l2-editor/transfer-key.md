# Transfer key

> **What this field controls.** Which transaction metadata field names
> group the template's leg firings into one shared Transfer.

## What you're looking at

A textarea that takes one metadata key name per line (or
comma-separated). The names point at metadata-key entries declared on
the template's leg rails — values from those keys, taken together,
form the join key the L1 layer uses to assemble a Transfer from
multiple leg firings.

## What "transfer" means here

A Transfer is the unit the L1 conservation invariant runs on. For a
single-leg rail, a Transfer is just one transaction; the rail's
firing and the Transfer are the same row. For a transfer template
with multiple leg rails, a Transfer is the set of leg firings that
together produce the template's `expected_net`. The L1 layer needs
to know which leg firings belong to which Transfer — that's what
the transfer-key value is for.

Example: a `WireDisbursement` template with two leg rails
(`CustomerDebit`, `CounterpartyCredit`). Both rails declare
`disbursement_id` as a metadata key. The template declares
`transfer_key = disbursement_id`. Two leg firings with matching
`disbursement_id` values are one Transfer; the L1 conservation
invariant checks their `signed_amount` sum equals
`expected_net`.

## Constraints (validator R12)

Every transfer-key entry must also appear in EVERY leg rail's
`metadata_keys`. The library auto-derives transfer-key entries as
`PostedRequirements` on each leg rail — the L1 layer rejects any
leg firing missing the key from its metadata.

If the transfer-key is empty (the field is structurally optional —
the loader accepts an empty list — but R12 doesn't fire then), all
leg firings of the template's leg rails join into one single
Transfer. That's rarely what the L2 author wants; almost every real
template declares a non-empty key.

## Why textarea (not multi-select)

The option universe is "every metadata key declared on every leg
rail of this template", which the form would have to derive
dynamically. The textarea sidesteps the derivation — the operator
types the key names, validator R12 catches mismatches at save
time with a clear error pointing at the broken leg rail.

## Related handbook pages

- [Chain children](chain-children.md) — how the assembled Transfer
  participates in the chain layer.
- [Leg rail XOR groups](leg-rail-xor-groups.md) — how the transfer
  key combines with XOR groups to pick which rail fires per
  template instance.

[Vocabulary](../_glossary.md)
