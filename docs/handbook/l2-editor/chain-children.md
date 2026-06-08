# Chain children

> **What this field controls.** Which rails or transfer templates may
> follow the chain's parent, and — for each — whether the child is a
> per-firing one-to-one follower or a many-to-one fan-in follower.

## What you're looking at

A chip-list picker. Each chip is one selected child (rail or template),
with a `fan-in` checkbox and an `N` integer slot next to it. The chip
order is the canonical sequence the L1 invariants walk.

## How children compose

The Z.A chain grammar reads the selection set:

- **Exactly one chip selected.** The child is *required*: every parent
  firing MUST be followed by an instance of this child within the
  parent's `completion` window. Missing follow-ups surface on the
  L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants))
  Exceptions sheet under the `chain_parent_disagreement` check-type
  branch.
- **Two or more chips selected.** The selection is an *XOR alternation*:
  exactly one of the selected children fires per parent firing. Any
  parent firing that matches more than one alternative, or matches
  zero, surfaces under the `xor_group_violation` or
  `multi_xor_violation` check-type branches on L1 Exceptions.

Empty selection is rejected — a chain with no children is meaningless.

## Per-child fan-in (`fan_in` + `expected_parent_count`)

By default a chain child is one-to-one with its parent. Tick the
**fan-in** checkbox on a chip when the child Transfer aggregates
multiple parent firings — the canonical example is a merchant settlement
cycle, where many transactions get netted into one settlement transfer.

The `N` slot to the right of the checkbox is the
*expected parent count* (`epc`) — how many parent firings the validator
expects per child Transfer. The L1 invariant `fan_in_disagreement`
flags any child Transfer whose actual parent count doesn't match `N`.

Validator constraints:

- `fan_in=true` is only valid on TransferTemplate children. Rails fail
  validator C8a (`TT-not-Rail` gate).
- `expected_parent_count` must be ≥ 2 when `fan_in=true` (validator
  C8b). A fan-in of 1 is just one-to-one with a longer name.
- Mixed-cardinality is fine. Inside one XOR alternation, one child
  can be fan-in while the siblings stay one-to-one. The sasquatch
  `MerchantSettlementCycle` chain is the canonical demo.

## When to reach for this

The chip-list version of this field exists because the validator's
fan-in / epc rules are not derivable from the rest of the L2. The
operator needs to declare them, and the form needs to keep them
attached to the child chip — so unchecking fan-in surfaces a clean
"this child is one-to-one" state instead of a stale `epc=5` sitting
under the surface.

## Related handbook pages

- [Transfer key](transfer-key.md) — how leg firings group into the
  shared parent Transfer that the chain points at.
- [Completion expression](completion-expression.md) — the window
  during which the L1 layer expects the child to appear.

[Vocabulary](../_glossary.md)
