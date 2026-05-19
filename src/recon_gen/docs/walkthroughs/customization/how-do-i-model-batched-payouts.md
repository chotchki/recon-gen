# How do I model batched payouts?

*Customization walkthrough — Integrator. Modeling the N:1 chain
pattern where multiple parent firings share one child Transfer.*

## The story

Your operations team has flagged that the merchant-payout cycle
isn't 1:1 — every merchant receives N daily card-settlement
contributions over the course of a week, and those N daily
contributions aggregate into ONE weekly payout transfer at end of
week. Today, you'd model this as N separate transfer cycles (one
per day) with no L2-side hygiene check that the weekly batch
actually pulled in every day's contribution — a missing daily
settlement just silently doesn't show up in the batch, and a
duplicate or cross-cycle contamination doesn't fire any alert.

The institution wants:

- A way to declare "this child Transfer expects N parent
  contributions";
- An L1 hygiene check that fires when the batch is short
  ("missing contribution") OR when an extra parent shows up
  ("cross-cycle contamination");
- A demo dashboard surface where analysts can drill into the
  batch when either failure mode fires.

This is exactly the AB.4
[fan-in chain](../../concepts/l2/chain.md#fan-in-chains-ab4-n-parents-one-child-transfer)
feature. You declare a chain with ``fan_in: true`` + a fixed
``expected_parent_count: N``; the runtime aggregates N parent
firings into one child Transfer; the L1
``<prefix>_fan_in_disagreement`` matview flags batches whose actual
parent set diverges from the declared count.

## The question

"How do I declare that 5 daily MerchantDailySettleAggregator
firings should aggregate into 1 weekly MerchantWeeklyPayoutBatch
Transfer, and how do I catch the cases where the batch is missing
a daily contribution or carries an extra one?"

## Where to look

Three reference points:

- **[Chain (concept)](../../concepts/l2/chain.md)** — the fan-in
  semantics + the validator C8a-c rules.
- **`tests/l2/spec_example.yaml`** — the minimal fixture carries
  one fan-in chain (`BatchPayoutTrigger → BatchedPayoutBatch`,
  ``expected_parent_count: 2``), proving the shape round-trips
  through the loader / validator / matview / seed / dashboard.
- **`tests/l2/sasquatch_pr.yaml`** — the real-world example
  carries the 5-parent ``MerchantDailySettleAggregator →
  MerchantWeeklyPayoutBatch`` chain (gap doc §2's
  ``MerchantPayoutBatch`` shape). Search for ``fan_in: true`` to
  find both.

## The change

In your `run/<institution>.yaml`, three edits:

**1. Declare the parent rail.** Two-leg is easiest — it
self-reconciles (its own ``expected_net=0`` closes the legs)
without needing to appear in any template's ``leg_rails``.

```yaml
rails:
  # existing rails...

  - name: MerchantDailySettleAggregator
    source_role: MerchantPayableClearing
    destination_role: WireSettlementSuspense
    expected_net: 0
    origin: InternalInitiated
    metadata_keys: [merchant_id, payout_batch_id]
    description: |
      Daily settlement leg — legs out of MerchantPayableClearing
      into WireSettlementSuspense tagged with the batch id it'll
      contribute to. N firings of this rail aggregate into one
      MerchantWeeklyPayoutBatch via the fan-in chain.
```

The ``payout_batch_id`` metadata key is what ties N daily firings
into one logical batch (ETL writes this from the institution's
batching policy — every Monday a fresh batch id, every Friday's
settlements share it).

**2. Declare the child template + its single closure leg.** The
template needs at least one ``leg_rail``; the simplest is a
single-leg credit that posts once per batch:

```yaml
rails:
  # ... parent above ...

  - name: MerchantWeeklyBatchClose
    leg_role: WireSettlementSuspense
    leg_direction: Credit
    origin: InternalInitiated
    metadata_keys: [merchant_id, payout_batch_id]

transfer_templates:
  - name: MerchantWeeklyPayoutBatch
    expected_net: 0
    transfer_key: [merchant_id, payout_batch_id]
    completion: business_day_end+7d
    leg_rails: [MerchantWeeklyBatchClose]
```

The ``transfer_key`` of ``(merchant_id, payout_batch_id)`` means
every leg of this template sharing that pair joins one Transfer.
Combined with the chain below, N daily parent firings + 1 batch
closure leg all live in one Transfer.

**3. Declare the fan-in chain.**

```yaml
chains:
  # existing chains...

  - parent: MerchantDailySettleAggregator
    children:
      - MerchantWeeklyPayoutBatch
    fan_in: true
    expected_parent_count: 5
    description: |
      5 daily MerchantDailySettleAggregator firings aggregate into
      one weekly MerchantWeeklyPayoutBatch Transfer per merchant.
      The L1 fan_in_disagreement matview flags batches with too
      few (missing contribution) or too many (cross-batch
      contamination) parents.
```

The validator will:

- Check C8a — every child of a ``fan_in=true`` chain resolves to a
  TransferTemplate (passes — MerchantWeeklyPayoutBatch is one).
- Check C8b — ``expected_parent_count`` may only be set when
  ``fan_in=true`` (passes — both fields are set on this row).
- Check C8c — ``expected_parent_count >= 2`` when set (passes — 5
  ≥ 2).
- Accept the chain entry; auto-derive the implicit
  ``parent_transfer_id`` metadata requirement on every leg_rail of
  the child template (inherits AB.2's metadata-key auto-derivation).

## How to verify

Re-emit the L2-derived schema and seed against your demo DB:

```bash
recon-gen schema apply -c run/config.yaml --execute
recon-gen data apply -c run/config.yaml --execute
```

The first command rewrites the ``<prefix>_transfer_parents`` matview
(derives the multi-parent set per child Transfer) and the
``<prefix>_fan_in_disagreement`` matview (flags batches with the
wrong cardinality). The second command re-seeds the demo data —
``auto_scenario.py`` plants three batches per fan-in chain:

- **Healthy** (5 parents): the AB.4.7 matview reads
  ``parent_count=5 == expected_parent_count=5`` → emits no row.
- **Missing-parent** (4 parents): ``parent_count=4 < expected=5``
  → emits a row with ``disagreement_kind='missing'``.
- **Extra-parent** (6 parents): ``parent_count=6 > expected=5``
  → emits a row with ``disagreement_kind='extra'``.

Open the L1 Today's Exceptions sheet. You should see:

- One row with ``check_type='fan_in_disagreement'`` and ``magnitude=4``
  (the missing plant).
- One row with ``check_type='fan_in_disagreement'`` and ``magnitude=6``
  (the extra plant).
- The ``rail_name`` column on both rows shows the child template name
  (``MerchantWeeklyPayoutBatch``).
- Drilling from either row leads you to the Transactions sheet
  filtered to the conflicting batch's child Transfer id; you can
  see which daily contributions did (or didn't) post.

Open Studio's ``/diagram`` page. The fan-in chain renders with a
distinct visual treatment — a ``[fan-in 5→1]`` label annotation +
double arrowhead — so the topology reader sees the N:1 shape
without reading the yaml.

## What you should NOT do

- **Don't make a non-TransferTemplate child the fan-in target.**
  Validator C8a rejects this. Rail-as-child fan-in is undefined —
  a rail's per-Transfer parent is the canonical 1:1 shape; the
  AB.4 gap doc §2 footnote closes that door explicitly. If you
  need to fan multiple rails into one downstream rail, model it
  with a template wrapping the downstream rail.
- **Don't set ``expected_parent_count`` on a non-fan-in chain.**
  Validator C8b rejects this. The field carries no meaning under
  ``fan_in=false`` and would mislead operators reading the yaml.
- **Don't set ``expected_parent_count=1``.** Validator C8c
  rejects this. A 1-parent fan-in is degenerate — it's just a 1:1
  chain. If you want a 1:1 chain, drop ``fan_in: true`` entirely.
- **Don't leave ``expected_parent_count`` unset for fixed-size
  batches.** The matview falls back to orphan-only detection
  (parent_count < 2), so missing/extra cases never surface. Set
  the count when you know it; leave it unset only when batch
  size truly varies per firing (e.g., daily settlement counts
  vary week-to-week based on the merchant's volume).
- **Don't worry about the AB.2.3 chain_parent_disagreement
  matview false-positiving on fan-in firings.** AB.4.4 wired in
  a NOT IN filter that excludes fan_in template children from
  the chain_parent_disagreement violation set — they're
  legitimately multi-parent by design and shouldn't surface
  there. The fan-in violations live in the dedicated
  ``_fan_in_disagreement`` matview instead.

## Related

- [Chain (concept)](../../concepts/l2/chain.md) — full field-by-field
  semantics, including the fan-in section and the C8a-c validator
  rules.
- [L1 Invariants → Fan-In Disagreement](../../L1_Invariants.md#fan-in-disagreement)
  — the SHOULD-constraint the matview encodes ("every fan_in child
  Transfer's parent_count matches the chain's expected_parent_count").
- [Schema_v6 → Chain](../../Schema_v6.md) — the data contract for
  the matview's column shape (``child_transfer_id`` /
  ``chain_parent_name`` / ``child_template_name`` / ``parent_count``
  / ``expected_parent_count`` / ``disagreement_kind`` /
  ``business_day``).
- [How do I chain two templates?](how-do-i-chain-two-templates.md)
  — the sibling AB.2 walkthrough for cascading 1:1 flows
  (template-as-chain-child, the OTHER chain-shape extension).
