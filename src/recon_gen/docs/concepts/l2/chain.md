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

## Fan-in chains (AB.4): N parents → one child Transfer

The default chain semantic is 1:1 — one parent firing invokes one
child firing (or one of N XOR alternatives). The **fan-in** flag
inverts the cardinality: N parent firings share ONE child Transfer.

The canonical example is the batched-payout pattern. A merchant
receives N daily settlements; the institution aggregates them into
ONE weekly payout transfer at the end of the week. Each daily
settlement is a "parent firing"; the weekly payout is the shared
"child Transfer". All N parent firings tag the child Transfer with
their own ``parent_transfer_id`` — the L1
``<prefix>_transfer_parents`` matview derives the multi-parent set
per child via DISTINCT, and the L1 ``<prefix>_fan_in_disagreement``
matview flags batches whose actual parent count doesn't match the
declared expected count (missing or extra contribution).

A fan-in entry rides on a specific child in the ``children:`` list
via the mapping form (AB.6 2026-05-19 — per-child shape; chain-level
``fan_in`` / ``expected_parent_count`` were retired so mixed-cardinality
chains compose naturally — some 1:1 children + some N:1 children
under one chain). Each child entry may carry two optional fields:

- ``fan_in: true`` — declares the N:1 inversion for THIS child.
  Default ``false`` (every bare-Identifier child stays byte-equivalent).
  Validator C8a requires the child to resolve to a TransferTemplate
  when ``fan_in=true`` (rail-as-child fan-in is undefined per gap doc
  §2 footnote — close that door at load time).
- ``expected_parent_count: N`` (optional, int ≥2) — the contract
  strength. When set, the L1 matview flags batches where
  ``parent_count != expected`` (kind ``'missing'`` for too few,
  ``'extra'`` for too many). When unset (variable-batch flows
  where N varies per firing), the L1 matview falls back to
  orphan-only detection: flags batches with ``parent_count < 2``
  (kind ``'orphan'``). Validator C8c requires ≥2 when set
  (a 1-parent fan-in is degenerate — it's just a 1:1 chain).
  Validator C8b requires the field to be unset when ``fan_in=false``
  (the field carries meaning only under fan-in).

YAML shape — a bare child (no flags) lowers to defaults:

```yaml
chains:
  - parent: ParentRail
    children:
      - SimpleChild              # bare Identifier ⇒ fan_in=false
      - name: BatchedChild       # mapping form opts in to fan_in
        fan_in: true
        expected_parent_count: 3
```

The L1 ``<prefix>_fan_in_disagreement`` matview surfaces violations
on Today's Exceptions under ``check_type='fan_in_disagreement'``;
the ``magnitude`` column carries the actual parent_count and the
``rail_name`` slot carries the child template name. The
``<prefix>_chain_parent_disagreement`` matview (AB.2.3) automatically
excludes fan_in template children — they're legitimately multi-
parent by design, so the AB.2 cardinality-1 check would
false-positive every fan-in firing.

> See [How do I model batched payouts?](../../walkthroughs/customization/how-do-i-model-batched-payouts.md)
> for a worked example.

## Multi-XOR runtime enforcement (AB.6.5)

A multi-children chain (≥2 children) encodes the "exactly one MUST
fire" XOR contract: every parent firing should be followed by exactly
ONE of the declared children. AB.6.5 adds the runtime side of that
contract: the L1 ``<prefix>_multi_xor_violation`` matview flags
parent firings where:

- **missed** (`child_count = 0`) — none of the declared children
  fired (the operator's intent was lost between parent firing and
  child posting); OR
- **overlap** (`child_count ≥ 2`) — two or more children fired,
  collapsing the alternation contract.

The matview's CTE skips per-child ``fan_in=True`` entries (AB.5
coupling — their cardinality is enforced by
``<prefix>_fan_in_disagreement`` instead). Mixed-cardinality
chains (one fan_in child + two or more 1:1 XOR siblings)
contribute only the non-fan_in siblings to the multi-XOR matview.

## Mixed-cardinality chains (AB.6 per-child shape)

AB.6 (2026-05-19) moved ``fan_in`` from chain-level to per-child
so one chain can carry both:

- **1:1 XOR alternation children** — every parent firing picks
  exactly ONE; enforced by ``_multi_xor_violation``.
- **N:1 fan-in children** — N parent firings batch into ONE shared
  child Transfer; enforced by ``_fan_in_disagreement``.

Both buckets emit independently from one parent firing — a single
parent contributes to ONE XOR child AND contributes to EVERY
fan_in child's batch.

YAML shape:

```yaml
chains:
  - parent: MerchantSettlementCycle
    children:
      - MerchantPayoutACH       # 1:1 XOR alternative
      - MerchantPayoutWire      # 1:1 XOR alternative
      - MerchantPayoutCheck     # 1:1 XOR alternative
      - name: MerchantWeeklyPayoutBatch  # N:1 fan-in entry
        fan_in: true
        expected_parent_count: 5
```

That's sasquatch's canonical demo: every settled cycle picks ONE
of three payout vehicles AND contributes to the week's batched
payout. The two enforcement matviews split the work:
`_multi_xor_violation` flags XOR violations on the 3 alternatives;
`_fan_in_disagreement` flags batches whose `parent_count ≠ 5` on
`MerchantWeeklyPayoutBatch`.

See [How do I mix cardinality children?](../../walkthroughs/customization/how-do-i-mix-cardinality-children.md)
for a worked example.

## Specific example for you

{{ l2_chain_focus() }}
