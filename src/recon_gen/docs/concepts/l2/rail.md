# Rail

A **rail** is the smallest indivisible money-movement primitive — one
"thing the institution does that posts to the ledger". Two shapes:

- **TwoLegRail** — posts a debit + a credit pair atomically.
  Declares ``source_role`` and ``destination_role``; every firing
  produces two Transaction rows that net to zero per the
  Conservation invariant.
- **SingleLegRail** — posts a single leg. The other side comes
  from elsewhere — either bundled into a multi-leg
  [Transfer Template](transfer-template.md), aggregated into a
  parent firing of an ``aggregating`` rail, or
  ``ExternalForcePosted`` (the institution's view doesn't include
  the offsetting side at all, like a Fed-side credit on an inbound
  wire).

Every Rail has:

- ``name`` — the rail's identifier. Under Z.B (2026-05-15), the
  rail's name IS the type identifier; posted Transactions carry the
  rail's name in their ``rail_name`` column to bind back to it
  (e.g. ``CustomerOutboundACH``, ``MerchantPayoutWire``).
- ``posted_requirements`` — optional list of metadata keys that MUST
  be populated on the Transaction (``card_brand``, ``cashier``, etc).
  L1 surfaces violations as posted-requirements drift.
- ``max_pending_age`` / ``max_unbundled_age`` — optional aging caps
  the Stuck Pending / Stuck Unbundled matviews use to surface legs
  that took too long to post or to bundle.
- ``aggregating`` — flag marking a rail as a bundler (an aggregating
  settlement rail, for example, picks up many sale-leg firings and
  emits one net-settled credit / debit pair).

## Optional: typical amount range (AB.5)

A rail can declare ``amount_typical_range: [min, max]`` to express
"every firing on this rail is normally between these two dollar
magnitudes". This is a **soft per-firing bound** — not a hard
constraint enforced at write time. Two ways it changes behavior:

- **Test-data generator (today).** Both the baseline emitter
  (``_baseline_amount_sample``) and the planted-scenario emitter
  (``_plant_amount_for_rail`` / ``_cap_breach_amount``) sample
  amounts log-uniformly within the declared range. So if a rail
  declares ``amount_typical_range: [5, 500]``, every demo firing on
  that rail lands between $5 and $500 with low-end clustering — the
  natural shape of retail card sales, not the heavy-tailed default
  the per-kind lognormal would produce. Plants size to the range
  midpoint so they look like ordinary firings (just at the boundary
  that triggers the SHOULD-constraint). Cap-breach plants on rails
  that *also* carry a ``LimitSchedule`` clamp to ``range.max × 3``
  so the breach amount stays in a realistic ballpark relative to
  the rail's typical volume.

- **Runtime SHOULD-constraint (follow-on).** A future
  ``<prefix>_magnitude_anomaly`` matview will surface posted
  Transactions that fall outside the declared range. Deferred from
  AB.5 per the gap doc's "generator-only first cut" — lands when an
  integrator asks for runtime anomaly surfacing.

**Validator rules (V1a-c):**

- **V1a** — ``min`` MUST be strictly less than ``max`` (degenerate
  single-point ranges rejected; if you want a fixed amount, write
  the seed directly).
- **V1b** — both ``min`` and ``max`` MUST be ``> 0``. The bound is
  on ``abs(amount)``, so signed and zero values have no meaning.
- **V1c** — forbidden on rails with ``aggregating: true``.
  Aggregator amounts derive from bundled children, so a per-firing
  band on the aggregator is fuzzy — set the range on the child
  rails instead.

**Sampling shape.** The generator samples log-uniformly:
``amount = exp(uniform(log(min), log(max)))``. This reproduces the
low-end clustering financial flows naturally show (most retail card
swipes are small; large ones are rare). For tightly-peaked flows
(e.g., a payroll rail where 90% of firings cluster within ±10% of
a single value) a follow-on ``amount_distribution: {median,
sigma_log}`` shape is planned — flagged in the L2 grammar evolution
queue, not landed yet.

> The Rail-to-Transaction binding is direct: a posted Transaction's
> ``rail_name`` column matches exactly one declared ``Rail.name``
> (the validator's R10 / U3 rules enforce uniqueness + resolution at
> L2 load time, so the binding can never be ambiguous).

## Optional: typical firing-count range (AF)

Where ``amount_typical_range`` bounds *how much* each firing moves,
``firings_typical_per_period`` bounds *how many times* the rail fires
per period. The two compose: realistic per-firing amounts × realistic
per-period counts = a realistic per-period aggregate — the
daily/monthly top-line operators scan first when judging whether a
demo's numbers are plausible. A $50 typical card sale fired 50,000
times a day implies $2.5M/day in card volume; for a small community
bank that's an order of magnitude too large no matter how realistic
the per-swipe amount is.

Two accepted YAML shapes:

```yaml
# Compact — period defaults to business_day.
- name: MerchantCardSale
  firings_typical_per_period: [50, 500]   # 50-500 swipes per business day

# Full — explicit period (business_day | pay_period | week | month).
- name: CustomerFeeMonthlySettlement_child
  firings_typical_per_period:
    period: month
    range: [80, 120]
```

- **Test-data generator (today).** When set, the baseline emitter
  (``_pick_firings_count``) samples a per-period count uniform-randomly
  from the band and scales by the number of periods in the window
  (count-per-period × periods = total firings over the window). When
  absent, it falls back to the per-kind firing-count heuristic —
  *without consuming any RNG state*, so pre-AF L2 instances stay
  byte-identical to their locked seeds. The per-day distribution is the
  generator's existing Poisson spread, so the declared band shows up as
  the aggregate-per-period the operator intended. Composes with
  ``amount_typical_range`` — count, then per-firing amount, fully
  independent.
- **Runtime SHOULD-constraint (follow-on).** A future
  ``<prefix>_volume_anomaly`` matview will surface periods whose actual
  firing count falls outside the declared band (early-warning
  surveillance: "today's transfer count is 10× yesterday — what
  changed?"). Deferred per the gap doc's "generator-only first cut".

**Validator rules (W1a-c):**

- **W1a** — ``min`` MUST be ``<= max``. Equal endpoints ARE allowed
  (``[1, 1]`` = "exactly one per period" — a legitimate fixed count,
  unlike AB.5's V1a which rejects degenerate amount ranges).
- **W1b** — both ``min`` and ``max`` MUST be ``>= 0``. Zero is allowed
  (a rail that typically fires zero times in some periods). Negative
  counts are rejected.
- **W1c** — forbidden on rails with ``aggregating: true``. An
  aggregating rail's ``cadence`` already governs its firing frequency
  (one firing per cadence-period), so a count band would conflict. Set
  the band on the child rails the aggregator bundles instead.

``firings_typical_per_period`` is also valid on a **TransferTemplate**
(W1a-b only — templates aren't aggregating rails). It drives a coupled
**unit firing**: every firing emits all the template's leg_rails together
as one balanced Transfer, at the declared per-period count. This works for
ANY template that declares it — chain parents (which already fire as a
unit via the chain machinery) AND standalone balanced multi-leg flows
(e.g. a card-load = cardholder-credit + clearing-debit pair). A
unit-firing template's leg_rails do NOT also fire independently in the
per-rail loop — that would double-emit and uncouple the legs, ignoring the
band and tripping false drift (Gap J).

Period-to-window conversion uses standard banking ratios: 5 business
days/week, 10/pay-period (bi-weekly), 21/month. A window shorter than
one period still emits one period's worth of firings.

## Specific example for you

{{ l2_rail_focus() }}
