# How do I set typical amount ranges on a rail?

*Customization walkthrough — Integrator / Trainer. Shaping the demo.*

## The story

A demo went sideways last week: the dashboard's Limit Breach sheet
surfaced a "$1,247,329 daily ACH outflow" row, the auditor stopped
the demo at sheet two and asked, "is that a real number?". It
isn't — the per-kind lognormal default in
``_baseline_amount_sample`` happens to roll a high-tail draw
occasionally, and the planted Outbound LimitBreachPlant was sized
``cap × 1.5`` regardless of what the rail's normal volume looks
like. The numbers are valid; they're just absurd for retail card
sales clearing through an in-house DDA.

You want every firing on your retail card-sale rail to land
between $5 and $500 (typical low-end clustering — single coffees
to high-end retail), and you want the planted scenarios to size to
the same band so plants look like ordinary firings (just at the
boundary that triggers the SHOULD-constraint).

This is the [amount_typical_range](../../concepts/l2/rail.md#optional-typical-amount-range-ab5)
feature: you declare a per-firing soft bound on the rail and both
the baseline emitter and the planted-scenario emitter respect it
without any schema migration or matview rewrite.

## The question

"How do I make demo amounts on the MerchantCardSale rail land
between $5 and $500 instead of the heavy-tailed lognormal default?"

## Where to look

Three reference points:

- **[Rail (concept) → Optional: typical amount range](../../concepts/l2/rail.md#optional-typical-amount-range-ab5)**
  — the field semantics: the log-uniform sampler, the cap
  interaction and the V1a-c validator rules.
- **`tests/l2/spec_example.yaml`** — the minimal fixture carries
  3 ranged rails (``ExternalRailInbound`` [50, 5000],
  ``ExternalRailOutbound`` [50, 10000], ``SubledgerCharge`` [1,
  100]). Search for ``amount_typical_range`` to find them.
- **`run/sasquatch_pr.yaml`** (or your own L2 yaml under `run/`)
  — the real-world example carries ranges on 6 representative
  rails, including ``MerchantCardSale [5, 500]`` and
  ``CustomerFeeAccrual [0.25, 25]``.

## The change

In your `run/<institution>.yaml`, find the rail you want to bound
and add ``amount_typical_range: [min, max]``:

```yaml
rails:
  # existing rails...
  - name: MerchantCardSale
    source_role: MerchantSettlement
    destination_role: MerchantDDA
    metadata_keys: [transfer_id, card_brand, terminal_id]
    amount_typical_range: [5, 500]
    description: |
      Retail card sale settling from the rail-side concentration
      account into the merchant's DDA. Single coffee to high-end
      retail; values cluster at the low end (log-uniform sampling).
```

Two notes on shape:

- ``min`` and ``max`` are dollar amounts (not cents). They accept
  the same shape the rest of the L2 grammar uses for Money —
  strings (``"5.00"``), bare ints (``5``) or floats (``5.00``).
- ``min`` MUST be strictly less than ``max`` (V1a). Both MUST be
  ``> 0`` (V1b). The field is FORBIDDEN on rails with
  ``aggregating: true`` (V1c) — aggregator amounts derive from
  bundled children, so set the range on the child rails instead.

## How to verify

Re-seed the demo, then refresh the matviews (``data apply`` does NOT
auto-refresh — the Limit Breach sheet reads a matview, so skip this
and it shows the OLD amounts):

```bash
recon-gen data apply -c run/config.yaml --execute
recon-gen data refresh -c run/config.yaml --execute
```

The seed regenerates the demo Transactions with the log-uniform
sampler honoring your declared range. Open the L1 dashboard and
filter to ``rail_name = MerchantCardSale`` — every firing should
land between $5 and $500, clustering at the low end.

See it live: https://recon-gen-spec.hotchkiss.io/ (the spec_example
fixture's ranged rails render there).

If your rail also carries a LimitSchedule cap, the cap-breach plant
amount is now ``max(min(cap × 1.5, range.max × 3), cap + 1)``. The
realism clamp pulls the breach down toward ``range.max × 3``; the
``cap + 1`` floor guarantees it still EXCEEDS the cap (without that
floor a high cap on a small-range rail could clamp BELOW the cap and
the violation would vanish — a real bug we hit).

So a $5000 cap on a rail with ``amount_typical_range: [5, 500]``:
``min(7500, 1500) = 1500`` falls below the cap, the floor wins, and
the plant breaches at $5001 — just over the cap instead of an absurd
$7500. When the range is wide enough that ``range.max × 3`` clears
the cap, the clamp lands the breach realistically on its own.

## What you should NOT do

- **Don't set ``amount_typical_range`` on an aggregating rail.**
  Validator V1c rejects this at load time. Aggregator amounts
  derive from bundled children; set the range on the child rails
  instead.
- **Don't set ``min == max``** (V1a rejects). If you want a fixed
  amount, write a direct seed via ``TransferTemplatePlant`` /
  ``RailFiringPlant`` instead — those are sized explicitly.
- **Don't set negative or zero values** (V1b rejects). The bound
  is on ``abs(amount)``; signed and zero values have no meaning.
- **Don't expect the validator to enforce the range at write
  time.** The bound is a generator-shaping hint AND a future
  runtime SHOULD-constraint matview hook — not a hard CHECK
  constraint on the transactions table. Real-world data that
  falls outside the band will surface in a follow-on
  ``_magnitude_anomaly`` matview when that lands.

## Related

- [Rail (concept)](../../concepts/l2/rail.md) — field-by-field
  semantics, including the V1a-c validator rules and log-uniform
  sampling shape.
- [Schema_v6 → Rail.amount_typical_range](../../Schema_v6.md) —
  the data contract for the soft-bound shape.
- [How do I add an AML inbound cap?](how-do-i-add-an-aml-inbound-cap.md)
  — the LimitSchedule cap that interacts with ``amount_typical_range``
  when both are declared.
