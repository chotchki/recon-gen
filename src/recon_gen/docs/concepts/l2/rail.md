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

## Specific example for you

{{ l2_rail_focus() }}
