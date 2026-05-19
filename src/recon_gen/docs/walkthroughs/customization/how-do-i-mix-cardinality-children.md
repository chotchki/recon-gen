# How do I mix cardinality children?

*Customization walkthrough — Integrator. One chain that carries
both 1:1 XOR alternation children AND an N:1 fan-in child. The
canonical AB.6 motivation: cardinality is a per-child attribute,
not a chain-level flag.*

## The story

Your operations team has audited the merchant settlement flow.
Every settled `MerchantSettlementCycle` fires TWO downstream
events:

- A 1:1 XOR pick — exactly one of three payout-vehicle rails
  (ACH preferred, Wire when the merchant banks elsewhere,
  Check for legacy paper) fires per cycle. The L2 contract is
  "exactly one MUST fire" — zero fires (no vehicle picked) or
  multiple fires (alternation collapsed) are both ETL bugs that
  drop on the floor today.
- AND a contribution to the week's `MerchantWeeklyPayoutBatch`
  (N:1 fan-in) — the cycle's settled amount accumulates into a
  shared weekly batch transfer along with the other 4 daily
  cycles. Missing contributions and cross-batch contamination
  are separate ETL bugs.

Before AB.6, you'd model this as two SEPARATE chains. One chain
with the three XOR vehicles, another chain with the fan-in batch.
But that loses the structural fact: both downstream events are
triggered by the same parent firing. AB.6 lets you say so.

## The question

"How do I declare that every MerchantSettlementCycle firing
contributes to the weekly batch AND fires exactly one payout
vehicle, with both contracts enforced independently?"

## Where to look

Three reference points:

- **[Chain (concept)](../../concepts/l2/chain.md)** — the per-child
  shape (AB.6) + the two enforcement contracts.
- **`tests/l2/sasquatch_pr.yaml`** — the real-world MerchantSettlementCycle
  chain. Search for `MerchantSettlementCycle:` under `chains:`.
- **[How do I model batched payouts?](how-do-i-model-batched-payouts.md)**
  for the standalone fan-in story (the simpler case).

## The change

In your `run/<institution>.yaml`, one chain entry with mixed
children:

```yaml
chains:
  # existing chains...

  - parent: MerchantSettlementCycle
    children:
      # 1:1 XOR alternation — exactly one MUST fire per cycle.
      - MerchantPayoutACH
      - MerchantPayoutWire
      - MerchantPayoutCheck
      # N:1 fan-in — 5 daily cycles share one weekly batch.
      - name: MerchantWeeklyPayoutBatch
        fan_in: true
        expected_parent_count: 5
    description: |
      Every settled MerchantSettlementCycle fires exactly ONE
      payout vehicle (XOR across ACH/Wire/Check) AND contributes
      to the week's MerchantWeeklyPayoutBatch (5 daily cycles
      share one weekly batch transfer per merchant).
```

The validator runs four checks against this chain:

- **C8a** — `fan_in=True` requires every fan_in child to resolve
  to a TransferTemplate. `MerchantWeeklyPayoutBatch` is a template
  → passes. (If you accidentally marked `MerchantPayoutACH` as
  `fan_in: true`, this would fail — rails aren't valid fan_in
  targets.)
- **C8b** — `expected_parent_count` only on `fan_in=True` entries.
  Setting it on `MerchantPayoutACH` would fail.
- **C8c** — `expected_parent_count ≥ 2`. A 1-parent fan_in is
  degenerate (it's a 1:1 chain). Validator rejects with a "drop
  fan_in" hint.
- **R5/S4** — every child resolves to a Rail or Template; aggregating
  rails MUST NOT appear as children.

## How the runtime enforces

The L1 layer splits the work across two matviews:

- **`<prefix>_multi_xor_violation`** (AB.6.5) sees the 3 XOR
  payout vehicles as the declared alternation set (it filters
  out per-child fan_in entries — `MerchantWeeklyPayoutBatch` is
  excluded from this matview's CTE per AB.5 coupling). For each
  `MerchantSettlementCycle` firing, it LEFT JOINs against children
  with `transfer_parent_id = cycle.transfer_id` and counts how
  many of the 3 declared vehicles fired. Emits a row when count
  ≠ 1:
  - `disagreement_kind='missed'` (count=0): no vehicle fired — the
    cycle's payout was lost.
  - `disagreement_kind='overlap'` (count≥2): two vehicles fired —
    duplicate posting or XOR alternation collapsed.

- **`<prefix>_fan_in_disagreement`** (AB.4.7) sees only the
  `MerchantWeeklyPayoutBatch` fan_in child. For each
  `MerchantWeeklyPayoutBatch` Transfer, it derives `parent_count`
  from AB.4.3's `_transfer_parents` matview and compares against
  `expected_parent_count=5`. Emits a row when:
  - `disagreement_kind='missing'` (count<5): a daily cycle didn't
    contribute.
  - `disagreement_kind='extra'` (count>5): a foreign cycle slipped in.

## How to verify

Re-emit + reseed against your demo DB:

```bash
recon-gen schema apply -c run/config.yaml --execute
recon-gen data apply -c run/config.yaml --execute
```

Open the L1 Today's Exceptions sheet. Filter `check_type`:

- `multi_xor_violation` rows → planted XOR violations on the
  3 payout vehicles.
- `fan_in_disagreement` rows → planted fan-in violations on
  the weekly batch.

Both surface on the same sheet (Today's Exceptions); both drill
to Transactions filtered to the violating parent firing's
transfer_id. The two contracts enforce independently — a cycle
with the wrong payout-vehicle count BUT a healthy batch
contribution surfaces only on multi_xor_violation; a healthy
cycle picked exactly one vehicle BUT contributed to the wrong
batch surfaces only on fan_in_disagreement.

Open Studio's `/diagram` page. The chain renders with separate
edge styles per child:

- 3 XOR-styled edges (dashed) to the payout vehicles.
- 1 fan-in-styled edge (bold, with `[fan-in 5→1]` annotation)
  to `MerchantWeeklyPayoutBatch`.

## What you should NOT do

- **Don't split into two separate chains.** Pre-AB.6 you had to:
  one chain for the XOR vehicles, another chain for the fan-in
  batch. AB.6 lets you express both contracts in one chain row,
  which preserves the operational fact that the same parent
  firing triggers both downstream events. The diagram + drill
  paths are cleaner because both edges share the same parent
  node.
- **Don't set `fan_in: true` on a 1:1 XOR alternative.**
  Validator C8a rejects it (rail-as-child fan-in is undefined per
  gap doc §2 footnote). If you need an XOR alternative that's
  itself batched, model it as a wrapping TransferTemplate.
- **Don't expect the AB.4 chain-level `fan_in:` shape to work.**
  AB.6.0 hard-cut the chain-level keys (no deprecation grace).
  The loader rejects them with a per-child migration pointer.

## Cross-references

- [Chain (concept)](../../concepts/l2/chain.md) — full SPEC vocabulary.
- [How do I model batched payouts?](how-do-i-model-batched-payouts.md)
  — the simpler all-fan_in case.
- [L1 Invariants](../../L1_Invariants.md) — §10 fan_in_disagreement
  + §11 multi_xor_violation.
