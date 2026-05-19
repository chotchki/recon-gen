# How do I add multi-mode settlement to a template?

*Customization walkthrough — Integrator. Modeling templates where
the same business event can fire one of several variants.*

## The story

Your operations team has flagged that the merchant-settlement
cycle isn't one cadence — some merchants are on intraday auto-sweep,
some on T+1 standard, some on weekly slow batches. They're all the
same business event (settle the merchant's daily card volume from
the suspense account to the merchant DDA), they all share the same
``transfer_key`` (the merchant cycle ID), they all have the same
``expected_net`` (the suspense balance nets to zero). Only the
*cadence* differs — and the institution's runtime picks which
variant fires per merchant per cycle.

Today, you'd model this with three separate templates and a chain
XOR row to alternate between them. That works but the model is
noisy: three templates with the same closure semantic, three
``expected_net`` declarations, three ``transfer_key`` declarations,
and any "did this merchant settle today?" query has to UNION
across all three matview branches.

This is exactly the AB.3
[XOR groups](../../concepts/l2/transfer-template.md#multi-mode-templates-ab3-one-closure-several-variants)
feature. You declare ONE template with the variants as Variable-direction
SingleLegRails inside its ``leg_rails``, and group the competing
variants in ``leg_rail_xor_groups``. The runtime picks one per
cycle; the ``_xor_group_violation`` matview catches missed firings
(no variant fired, the cycle didn't close) and overlaps (≥2
variants fired, the runtime double-posted).

## The question

"How do I declare that ``MerchantSettlementCycle`` can fire one of
three settlement timing variants — auto / standard / slow — and how
do I catch the case where ETL double-posts or fails to post the
variant leg?"

## Where to look

Three reference points:

- **[Transfer template (concept)](../../concepts/l2/transfer-template.md)**
  — the multi-mode template semantic + the validator C1a-d rules.
- **`tests/l2/spec_example.yaml`** — the minimal fixture carries
  one XOR-grouped template (``SettlementTimingCycle`` with the
  ``[SettlementAuto, SettlementStandard]`` group + ``SettlementSlow``
  as the lone non-grouped Variable witness), proving the shape
  round-trips through the loader / validator / matview / seed /
  dashboard.
- **`tests/l2/sasquatch_pr.yaml`** — the real-world example
  carries ``MerchantSettlementCycle`` with TWO XOR groups
  (settlement-timing trio + fraud-review trio, 6 variants total).
  Search for ``leg_rail_xor_groups`` to find them.

## The change

In your `run/<institution>.yaml`, three edits:

**1. Declare each variant as a Variable-direction SingleLegRail.**

```yaml
rails:
  # existing rails...

  - !SingleLegRail
    name: SettlementAuto
    leg_role: [ClearingSuspense]
    leg_direction: Variable
    origin: InternalInitiated
    metadata_keys: [settlement_cycle_id]
    max_pending_age: PT4H        # auto = intraday SLA
    description: Auto-sweep variant — fires within 4 hours of cycle close.

  - !SingleLegRail
    name: SettlementStandard
    leg_role: [ClearingSuspense]
    leg_direction: Variable
    origin: InternalInitiated
    metadata_keys: [settlement_cycle_id]
    max_pending_age: P1D         # T+1 cadence
    description: Standard variant — fires by end of next business day.

  - !SingleLegRail
    name: SettlementSlow
    leg_role: [ClearingSuspense]
    leg_direction: Variable
    origin: InternalInitiated
    metadata_keys: [settlement_cycle_id]
    max_pending_age: P7D         # weekly batch
    description: Slow variant — fires on the weekly batch sweep.
```

All three share ``leg_role``, ``leg_direction=Variable``,
``origin``, and ``metadata_keys`` — they're the same closure leg,
just with different cadence SLAs.

**2. Add the variants to the template's `leg_rails` AND list the
group in `leg_rail_xor_groups`.**

```yaml
transfer_templates:
  - name: MerchantSettlementCycle
    expected_net: "0"
    transfer_key: [settlement_cycle_id]
    completion: business_day_end+1d
    leg_rails:
      - MerchantCardSale         # existing two-leg
      - SettlementAuto           # NEW — auto variant
      - SettlementStandard       # NEW — standard variant
      - SettlementSlow           # NEW — slow variant
    leg_rail_xor_groups:
      - [SettlementAuto, SettlementStandard, SettlementSlow]
```

The validator will:

- Check C1a (every group member is in `leg_rails`) — passes.
- Check C1b (every group member is Variable-direction SingleLegRail) —
  passes.
- Check C1c (no rail in two groups) — passes (one group).
- Check C1d (≥2 members per group) — passes (3 members).
- Check C1 (≤1 non-grouped Variable per template) — passes (all 3
  Variables are grouped; non-Variable rails don't count).

**3. (Optional) Add a second group on the same template.**

If your settlement also has independently-varying fraud-review
depth (no review / standard review / enhanced review), declare
those as a SECOND group on the same template — disjoint rail
pools so C1c (no overlap) holds:

```yaml
    leg_rails:
      - MerchantCardSale
      - SettlementAuto
      - SettlementStandard
      - SettlementSlow
      - NoFraudReview            # NEW
      - StandardFraudReview      # NEW
      - EnhancedFraudReview      # NEW
    leg_rail_xor_groups:
      - [SettlementAuto, SettlementStandard, SettlementSlow]
      - [NoFraudReview, StandardFraudReview, EnhancedFraudReview]
```

Each group is independent — "exactly one settlement variant +
exactly one fraud-review variant per cycle". The runtime picks
both per merchant per cycle.

## How to verify

Re-emit the L2-derived schema and seed against your demo DB:

```bash
recon-gen schema apply -c run/config.yaml --execute
recon-gen data apply -c run/config.yaml --execute
```

The first command rewrites the `<prefix>_xor_group_violation`
matview against the new XOR-grouped templates (the matview body
inlines the group membership rows from your L2 yaml). The second
command re-seeds the demo data — `auto_scenario.py` plants ONE
``XorVariantMissedFiringPlant`` (a Transfer where the group has
zero firings ⇒ ``firing_count=0``, ``fired_rails=''``) AND ONE
``XorVariantOverlapPlant`` (a Transfer where two variants fired ⇒
``firing_count=2``, ``fired_rails='<a>,<b>'``).

Open the L1 Today's Exceptions sheet. You should see:

- One row with `check_type='xor_group_violation'` and `magnitude=0`
  (the missed-firing plant — the template fired but no group
  variant did).
- One row with `check_type='xor_group_violation'` and `magnitude=2`
  (the overlap plant — two variants fired for the same cycle).
- The `rail_name` column on both rows shows the template name
  (e.g. `MerchantSettlementCycle`); the violation is per-template,
  not per-variant.
- Drilling from the row leads you to the Transactions sheet
  filtered to the Transfer's id; you can see which variants did
  (or didn't) post.

Open the L1 Pending Aging sheet. The bar chart at the top stacks
its bucket counts by `rail_name` — the variants render as distinct
color bands so you can see "auto is healthy but standard is
stuck" at a glance. The topology diagram (Studio's `/diagram`
page) renders the XOR group as a nested sub-cluster inside the
template cluster, labeled "XOR group 1 (exactly 1 fires)".

## What you should NOT do

- **Don't declare three separate templates with a chain XOR.**
  The whole point of XOR groups is the shared closure: same
  ``expected_net``, same ``transfer_key``, one template-level
  Conservation invariant. Three templates ⇒ three close-out
  checks that don't talk to each other, and the dashboard can't
  show "this cycle's variant set fired correctly" as a single row.
- **Don't put a Debit/Credit single-leg in an XOR group.** The
  validator C1b rejects this. XOR group semantics ("exactly one
  fires per Transfer") only make sense for Variable-direction
  closure legs — a Debit rail always fires when its trigger does;
  putting it in an XOR group is a category error.
- **Don't put a TwoLegRail in an XOR group.** Same C1b rejection.
  The mutual-exclusion contract is per-closure-leg, not per-leg-pair.
  If you need alternating two-leg flows, model them as separate
  templates with a chain XOR (Z.A multi-children grammar).
- **Don't make a 1-member group.** A 1-member group means "the
  rail always fires", which is what `leg_rails` already says.
  Validator C1d rejects singletons. If you genuinely have only one
  variant today and want to leave space for adding more later,
  don't declare the group field yet — add it when the second
  variant lands.
- **Don't overlap groups.** C1c forbids a rail being in two
  groups. If two variants compete in two different dimensions
  (timing + fraud-review), use two disjoint groups, not one
  group with shared members.

## Related

- [Transfer template (concept)](../../concepts/l2/transfer-template.md)
  — full field-by-field semantics, including the multi-mode
  template section and the C1a-d validator rules.
- [L1 Invariants → XOR group violation](../../L1_Invariants.md#xor-group-violation)
  — the SHOULD-constraint the matview encodes ("exactly one
  member of each leg_rail_xor_groups entry fires per Transfer").
- [Schema_v6 → TransferTemplate](../../Schema_v6.md) — the data
  contract for the matview's column shape (
  ``transfer_id`` / ``template_name`` / ``xor_group_index`` /
  ``firing_count`` / ``fired_rails`` / ``business_day``).
- [How do I chain two templates?](how-do-i-chain-two-templates.md)
  — the sibling AB.2 walkthrough for cascading multi-leg flows
  (the OTHER closure-shape extension landed alongside AB.3).
