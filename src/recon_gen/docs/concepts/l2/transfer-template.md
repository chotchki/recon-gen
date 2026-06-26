# Transfer template

A **transfer template** binds several [Rail](rail.md) firings into one
business-meaningful Transfer that MUST close out together. Classic case:
an "ACH origination cycle" fires three legs — debit the customer DDA,
credit the suspense holding GL, then debit suspense and credit the
Federal Reserve master account — and the template guarantees all three
post as a unit, against a deterministic ``expected_net`` close-out.

A template has:

- ``name`` — unique identifier. Any leg of a templated bundle carries it
  as ``Transaction.template_name``, so the leg traces back to its parent
  template.
- ``leg_rails`` — ordered list of Rail names declaring which rails fire
  in the template. Each leg posts via its named Rail.
- ``expected_net`` — the **L1 Conservation invariant** for the template:
  every non-Failed leg's signed ``amount_money`` MUST sum to this value.
  Most templates use ``0`` (a debit + credit pair nets out); a non-zero
  value covers the case where an external system posts the offsetting
  side (an ``ExternalForcePosted`` leg lands a credit the institution's
  books never record).
- ``leg_rail_xor_groups`` (optional) — mutually-exclusive subsets of
  ``leg_rails``. EXACTLY one member of each group fires per Transfer
  (the competing variants share one closure slot; the institution's
  runtime picks which fires per cycle). A multi-mode settlement is the
  classic shape: ``[SettlementAuto, SettlementStandard, SettlementSlow]``
  is one closure run at three possible cadences, picked per-merchant.
  Structural rules (validator C1a-d): each group needs ≥2 members; every
  member must live in ``leg_rails`` AND resolve to a Variable-direction
  SingleLegRail. The runtime "exactly-one-fires" check lands on the
  ``_xor_group_violation`` matview — 0 firings ⇒ missed (the template
  never closed); ≥2 firings ⇒ overlap (the runtime double-posted).

Templates are how the L1 dashboard knows a multi-leg cycle is "open" —
a leg fired but the close-out leg hasn't, so the running sum doesn't yet
equal ``expected_net``. A stuck template surfaces in the Pending Aging
or Unbundled Aging matviews, depending on which leg is late.

## Multi-mode templates (AB.3): one closure, several variants

The same merchant settlement might be authorized at several speeds —
auto (intraday sweep), standard (T+1), slow (weekly batch) — set by the
merchant's contract tier. The institution treats them as one closure
event (same ``expected_net``, same ``transfer_key`` merchant cycle), but
exactly ONE variant fires per cycle.

``leg_rail_xor_groups`` is how you express that. Declare each variant as
its own Variable-direction SingleLegRail (``SettlementAuto`` /
``SettlementStandard`` / ``SettlementSlow``), list them all in
``leg_rails``, then group the competitors:
``leg_rail_xor_groups: [[SettlementAuto, SettlementStandard,
SettlementSlow]]``. The runtime picks one per cycle and the
``_xor_group_violation`` matview flags the bug cases (no variant fired,
or ≥2 fired) — you never write that check by hand.

One template can carry several groups — say one for settlement timing
alongside one for fraud-review depth. The groups are independent; the
contract is "exactly one member of each group fires", and any rail
belongs to at most one group (validator C1c).

The L1 Pending Aging + Unbundled Aging bar charts stack their counts by
``rail_name``, so each variant becomes its own color band — "the slow
variant is dragging" reads straight off the chart. The topology diagram
renders the group as a nested sub-cluster inside the template cluster,
labeled "XOR group N (exactly 1 fires)", so the mutual-exclusion
contract shows on the diagram pane without leaving the topology.

> Don't reach for a Transfer Template just because two rails CAN fire
> together. Use it when the rails MUST fire together as one business
> event — the template's ``expected_net`` is the binding close-out
> invariant. Two independent rails are two rails with a
> [chain](chain.md) edge instead.

## Specific example for you

{{ l2_transfer_template_focus() }}
