# How do I chain two templates?

*Customization walkthrough — Integrator. Modeling cascading
multi-leg flows.*

## The story

Your operations team has flagged that the bank's customer-fee
accrual cycle should trigger a downstream internal transfer cycle:
every accrual ought to be followed by an `InternalTransferCycle` that
moves the accrued amount from the customer's DDA to the fee revenue
GL. Today, the second cycle just kind-of happens — there's no
hygiene check that catches the case where the fee accrual fires but
the downstream transfer never does, or worse, fires against the wrong
parent (stale reference, cross-cycle contamination, race condition).

This is exactly the AB.2
[template-as-chain-child](../../concepts/l2/chain.md#template-as-chain-child-ab2)
feature. You declare a chain where:

- `parent` = the rail (or template) that kicks off the cascade
- `children` = a list with one TransferTemplate name (a singleton
  child encodes "required" semantics — every parent firing MUST
  invoke this template)

The L2 grammar handles the rest: the validator auto-derives the
`parent_transfer_id` posted-metadata requirement on every leg_rail of
the child template, the seed emits chain firings where all leg_rails
share one Transfer with one shared parent_transfer_id, and the L1
`chain_parent_disagreement` matview catches any leg that claims a
different parent.

## The question

"How do I declare that every `CustomerFeeAccrual` firing should
trigger an `InternalTransferCycle`, and how do I catch the case
where ETL writes leg rows that disagree on which parent fee accrual
they belong to?"

## Where to look

Three reference points:

- **[Chain (concept)](../../concepts/l2/chain.md)** — the template
  -as-chain-child semantic + the first-firing-wins / disagreement
  rules.
- **`tests/l2/spec_example.yaml`** — the minimal fixture carries one
  rail→template chain (`ReconciliationLeg → MerchantSettlementCycle`),
  proving the shape round-trips through the loader / validator /
  matview / seed / dashboard.
- **`tests/l2/sasquatch_pr.yaml`** — the real-world example carries
  `CustomerFeeAccrual → InternalTransferCycle`. Search for
  `parent: CustomerFeeAccrual` to find it.

## The change

In your `run/<institution>.yaml`, find the `chains:` block and add a
new entry whose `children` list names a TransferTemplate:

```yaml
chains:
  # existing chains...

  - parent: CustomerFeeAccrual
    children:
      - InternalTransferCycle   # TransferTemplate name, not a rail
    description: |
      Template-as-chain-child shape. Every CustomerFeeAccrual SHOULD
      trigger an InternalTransferCycle to move the accrued fee from
      the customer's DDA to the fee revenue GL. All three leg_rails
      of the child template (InternalTransferDebit / Credit /
      SuspenseClose) share one child Transfer and one
      `parent_transfer_id` (first-firing-wins). ETL bugs that
      disagree on which parent the legs belong to surface on Today's
      Exceptions under `check_type='chain_parent_disagreement'`.
```

The validator will:

- Accept the chain entry (R5: `chain.children` may be a Rail OR a
  TransferTemplate).
- Auto-derive the `parent_transfer_id` posted-metadata requirement
  on every leg_rail of `InternalTransferCycle` — no operator yaml
  change needed for the metadata_keys allowlists.

## How to verify

Re-emit the L2-derived schema and seed against your demo DB:

```bash
recon-gen schema apply -c run/config.yaml --execute
recon-gen data apply -c run/config.yaml --execute
```

The first command rewrites the `<prefix>_chain_parent_disagreement`
matview against the new chain shape (the matview itself doesn't change
— it groups by `(transfer_id, template_name)` regardless of which
templates exist). The second one re-seeds the demo data —
`auto_scenario.py` plants a `TwoTemplateChainPlant` (healthy, no
violation) AND a `ChainParentDisagreementPlant` (synthetic ETL bug
with conflicting parent_transfer_ids).

Open the L1 Today's Exceptions sheet. You should see:

- One row with `check_type='chain_parent_disagreement'` and a
  `rail_name` column showing `InternalTransferCycle` (the template
  name surfaces in the rail_name slot for this row category).
- The `magnitude` column reads `2` (= cardinality of the conflicting
  parent_transfer_id set).
- Drilling from the row leads you to the Transactions sheet
  filtered to the conflicting Transfer's id.

## What you should NOT do

- **Don't add a new matview just for two-template chains** — the
  existing `chain_parent_disagreement` matview already handles both
  the healthy case (cardinality=1, no row emitted) and the violation
  case (cardinality≥2, row surfaces).
- **Don't declare `parent_transfer_id` in the child template's
  leg_rails' `metadata_keys`** — the validator auto-derives the
  requirement from the chain relationship per AB.2.0 design lock.
  Adding it to yaml is redundant and creates two sources of truth
  for one fact.
- **Don't use a multi-children chain (`children: [a, b]`) when the
  semantic is "always fires"** — multi-children encodes XOR
  alternation (exactly one fires per parent). For "every parent must
  invoke this template", use a singleton list.

## Related

- [Chain (concept)](../../concepts/l2/chain.md) — full field-by-field
  semantics, including the rail/template endpoint matrix.
- [L1 Invariants → Chain Parent Disagreement](../../L1_Invariants.md#chain-parent-disagreement)
  — the SHOULD-constraint the matview encodes, with the
  first-firing-wins theorem.
- [Schema_v6 → Chain](../../Schema_v6.md) — the data contract for
  the matview's column shape.
