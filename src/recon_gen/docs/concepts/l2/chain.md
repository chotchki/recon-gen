# Chain

A **chain row** declares "when ``parent`` fires, one of these
``children`` SHOULD also fire". Each chain row is one piece of L2
hygiene the system checks against runtime data: did the expected
child actually fire when the parent did?

Each chain row has:

- ``parent`` — references either a [Rail](rail.md) name or a
  [Transfer Template](transfer-template.md) name.
- ``children`` — a list of one or more rail or template names. The
  shape of the list encodes the firing semantics:
    - **One child** = required. Every parent firing MUST invoke that
      child; a parent firing without it surfaces as a Chain Orphan.
    - **Two or more children** = XOR alternation. Exactly one of the
      listed children MUST fire per parent invocation. Used for
      branching cycles (e.g. an ACH return MUST fire as one of "NSF",
      "stop-pay", "duplicate" — not zero, not two; or a merchant
      payout MUST take exactly one of three vehicles: ACH, wire,
      check).

Endpoints can mix-and-match: rail → rail, rail → template, template
→ rail, template → template.

Chains are the modeling tool for "this rail's firing has downstream
consequences" without forcing those downstream firings to be inside
the same atomic Transfer Template. Use a Transfer Template when the
legs MUST be one transaction; use a chain when they can be separate
transactions but you want hygiene to check the second one happened.

> Chain Orphans rolls into the L2 Flow Tracing app's L2 Exceptions
> sheet under ``check_type='Chain Orphans'``. A required-but-missing
> child firing surfaces with the parent firing's id + timestamp so
> you can investigate why the chain broke (rail SQL error, missing
> data, manual posting that bypassed automation, etc).

## Template-as-chain-child (AB.2)

When a chain row's ``children`` entry resolves to a TransferTemplate
(rather than a Rail), the firing semantic shifts in two ways:

1. **First-firing-wins.** The first leg_rail firing of the child
   template establishes the shared Transfer's ``parent_transfer_id`` —
   subsequent leg_rail firings reuse that same value. All legs of the
   child template aggregate into ONE child Transfer per chain
   invocation.
2. **Chain Parent Disagreement.** When subsequent leg_rail firings
   claim a *different* ``parent_transfer_id`` than the first-firing
   established (typically an ETL bug — stale parent reference,
   cross-cycle contamination, race condition), the L1
   ``<prefix>_chain_parent_disagreement`` matview surfaces the
   conflict on Today's Exceptions under
   ``check_type='chain_parent_disagreement'``.

The validator auto-derives the implicit ``parent_transfer_id`` posted
metadata requirement for every leg_rail of a chain-child template — no
operator-explicit YAML declaration needed (the chain relationship is
the single source of truth, per AB.2.0 design lock).

> See [How do I chain two templates?](../../walkthroughs/customization/how-do-i-chain-two-templates.md)
> for a worked example.

## Specific example for you

{{ l2_chain_focus() }}
