# How do I set typical firing counts on a rail?

*Customization walkthrough — Integrator / Trainer. Shaping the demo.*

## The story

You set [amount_typical_range](how-do-i-set-typical-amount-ranges.md)
last week and the per-swipe numbers look right — $5 to $500, clustered
low, exactly like a coffee-shop card book. But the demo went sideways
again: the L1 Daily Statement top-line shows **$2.5M in card sales per
day**. The auditor — who knows this is a 200-customer community bank —
raises an eyebrow. "You process two and a half million dollars a day in
card volume?"

You don't. The per-firing amount is realistic, but the *generator fired
50,000 card sales a day* — the per-kind firing-count heuristic scaled up
on customer count and produced an institution-wide volume an order of
magnitude too large. Per-firing realism alone doesn't fix the top-line;
**count × amount = aggregate**, and the count half was unbounded.

This is the AF [firings_typical_per_period](../../concepts/l2/rail.md#optional-typical-firing-count-range-af)
feature — the complement to ``amount_typical_range``. You declare how
many times the rail typically fires per period, and the generator
samples within that band instead of the heuristic.

## The question

"How do I make MerchantCardSale fire ~50-500 times per business day
(realistic for a small bank) instead of the heuristic's 50,000?"

## Where to look

Three reference points:

- **[Rail (concept) → Optional: typical firing-count range](../../concepts/l2/rail.md#optional-typical-firing-count-range-af)**
  — the field semantics: the two YAML shapes, how the per-period
  sampler scales to the window, what the W1a-c validator rules
  enforce, and how it composes with ``amount_typical_range``.
- **`tests/l2/spec_example.yaml`** — the minimal fixture carries the
  compact form (``ExternalRailInbound: [20, 50]``), the mapping form
  (``SubledgerCharge: {period: month, range: [60, 90]}``), and the
  field on a TransferTemplate (``SettlementTimingCycle:
  {period: week, range: [3, 8]}``). Search for
  ``firings_typical_per_period``.
- **`run/sasquatch_pr.yaml`** (or your own L2 yaml under `run/`) — the
  real-world example carries per-business-day counts on
  ``MerchantCardSale [50, 500]``, ``CustomerInboundACH [50, 200]``,
  and ``InternalTransferDebit [200, 500]``.

## The change

In your `run/<institution>.yaml`, find the rail and add
``firings_typical_per_period``. Compact form (defaults to per business
day):

```yaml
rails:
  - name: MerchantCardSale
    source_role: ExternalCardNetwork
    destination_role: MerchantPayableClearing
    metadata_keys: [merchant_id, settlement_period, card_network_ref]
    amount_typical_range: ["5.00", "500.00"]
    firings_typical_per_period: [50, 500]   # 50-500 swipes / business day
```

For a non-daily cadence, use the full form with an explicit period
(``business_day | pay_period | week | month``):

```yaml
  - name: SomeMonthlyRail
    leg_role: CustomerSubledger
    leg_direction: Debit
    firings_typical_per_period:
      period: month
      range: [80, 120]
```

Two notes on shape:

- Counts are integers. ``min`` MUST be ``<= max`` (W1a) — equal
  endpoints are fine (``[1, 1]`` = "exactly one per period"). Both MUST
  be ``>= 0`` (W1b) — zero is allowed (a rail that some periods doesn't
  fire at all).
- The field is **forbidden on rails with ``aggregating: true``** (W1c).
  An aggregating rail's ``cadence`` already governs how often it fires;
  set the count band on the child rails it bundles instead.

## How to verify

Re-seed the demo:

```bash
recon-gen data apply -c run/config.yaml --execute
```

The seed regenerates demo Transactions with the per-period sampler
honoring your band. Open the L1 dashboard and read the Daily Statement
top-line — the card-sales aggregate should now be (50-500 swipes/day) ×
(\$5-\$500/swipe) ≈ a few thousand to a couple hundred thousand dollars
a day, the realistic range for a small bank, instead of the $2.5M the
heuristic produced.

To confirm the count specifically, filter the L1 transactions view to
``rail_name = MerchantCardSale`` and group by ``balance_date`` — the
per-day row count should land inside your declared band.

## What you should NOT do

- **Don't set ``firings_typical_per_period`` on an aggregating rail.**
  Validator W1c rejects this at load time. The ``cadence`` field
  already encodes the aggregator's firing frequency (one firing per
  cadence-period). Set the band on the child rails instead.
- **Don't set ``min > max``** (W1a rejects). Equal is fine; descending
  is operator confusion.
- **Don't set negative counts** (W1b rejects). Zero is allowed.
- **Don't expect the count to be exact per day.** The band is a
  *per-period* target; the generator spreads it across the period's
  business days with its existing Poisson distribution, so individual
  days vary around the average. The aggregate-per-period is what lands
  inside the band.
- **Don't expect runtime enforcement yet.** Like
  ``amount_typical_range``, this is a generator-shaping hint (and a
  future ``_volume_anomaly`` matview hook) — not a hard constraint on
  real data. Real-world periods whose count falls outside the band will
  surface in the follow-on matview when that lands.

## Related

- [How do I set typical amount ranges?](how-do-i-set-typical-amount-ranges.md)
  — the per-firing magnitude bound. Compose the two: realistic amounts
  × realistic counts = a realistic per-period aggregate.
- [Rail (concept)](../../concepts/l2/rail.md) — field-by-field
  semantics, the W1a-c validator rules, and the period-to-window
  conversion ratios.
- [Schema_v6 → Volume as data](../../Schema_v6.md) — the data contract
  for the firing-count band.
