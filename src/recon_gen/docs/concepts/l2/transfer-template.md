# Transfer template

A **transfer template** chains multiple [Rail](rail.md) firings into one
business-meaningful Transfer. The classic example: an
"ACH origination cycle" template fires three legs — debit the
customer DDA, credit the suspense holding GL, then debit suspense and
credit the Federal Reserve master account — and the template
guarantees those three legs happen together, with a deterministic
``expected_net`` close-out.

A template has:

- ``name`` — unique identifier referenced from
  ``Transaction.template_name`` so any leg of a templated bundle
  can be traced back to its parent template.
- ``leg_rails`` — ordered list of Rail names declaring which rails
  fire as part of the template. Each leg of a firing posts via the
  named Rail.
- ``expected_net`` — the **L1 Conservation invariant** for this
  template: the sum of every non-Failed leg's signed ``amount_money``
  MUST equal this value. Most templates use ``0`` (debit + credit
  pair nets to zero); a few use a non-zero value when an external
  system contributes the offsetting side (e.g. an ``ExternalForcePosted``
  leg lands a credit the institution's books don't include).
- ``leg_rail_xor_groups`` (optional, AB.3) — declares
  mutually-exclusive subsets of ``leg_rails``. For each group,
  **exactly one** member fires per Transfer (the alternative
  variants share a closure slot but the institution's runtime
  picks one per cycle). The classic case is a multi-mode
  settlement: ``[SettlementAuto, SettlementStandard, SettlementSlow]``
  — same closure, three possible cadences picked per-merchant.
  Each group needs ≥2 members, all members must be in
  ``leg_rails``, all must be Variable-direction SingleLegRails
  (validator C1a-d). The "exactly-one-fires" runtime check
  surfaces on the ``_xor_group_violation`` matview: 0 firings
  ⇒ missed (the template didn't close); ≥2 firings ⇒ overlap
  (the runtime double-posted).

Templates are how the L1 dashboard knows a multi-leg cycle is "open"
— a leg has fired but the close-out leg hasn't yet, so the running
sum doesn't equal ``expected_net``. Stuck templates surface in the
Pending Aging + Unbundled Aging matviews depending on which leg's
late.

## Multi-mode templates (AB.3): one closure, several variants

Real-world banking is rarely uniform. The same merchant
settlement might be authorized at three speeds — auto (intraday
sweep), standard (T+1), slow (weekly batch) — driven by the
merchant's contract tier. The institution treats them as the
same closure event (the ``expected_net`` is the same; the
``transfer_key`` is the same merchant cycle), but exactly ONE
variant fires per cycle.

``leg_rail_xor_groups`` is how you express this. Declare each
variant as its own Variable-direction SingleLegRail
(``SettlementAuto`` / ``SettlementStandard`` / ``SettlementSlow``),
list them all in ``leg_rails``, then group the ones that compete:
``leg_rail_xor_groups: [[SettlementAuto, SettlementStandard,
SettlementSlow]]``. The runtime picks one per cycle; the
``_xor_group_violation`` matview flags the bug cases (no variant
fired, or ≥2 fired) without you having to write the check
manually.

You can declare multiple groups on one template — e.g. one group
for settlement timing + one group for fraud-review depth. Each
group is independent; the constraint is "exactly one member of
each group fires", and any rail can be in at most one group
(validator C1c).

The L1 Pending Aging + Unbundled Aging bar charts stack their
counts by ``rail_name`` so each variant becomes a distinct color
band — "the slow variant is dragging" reads off the chart
directly. The topology diagram renders the group as a nested
sub-cluster inside the template cluster, labeled "XOR group N
(exactly 1 fires)", so the mutual-exclusion contract is visible
on the diagram pane without leaving the topology.

> Don't reach for a Transfer Template just because two rails *can*
> fire together. Use it when the rails MUST fire together as one
> business event — the template's ``expected_net`` is the binding
> close-out invariant. If two rails are independent, model them as
> two rails with a [chain](chain.md) edge instead.

## Specific example for you

{{ l2_transfer_template_focus() }}
