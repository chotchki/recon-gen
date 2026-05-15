# Seed Generator

*Reference for the demo seed pipeline — `emit_full_seed` and the per-Rail
baseline + plant overlay shape it produces. Currently rendered against
**{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

This page is durable reference for integrators who want to understand
**what the demo data looks like and why** — the volume, amount,
time-of-day, and chain-completion shapes the per-rail loop emits, and
the deterministic plant overlays layered on top. If you're planning to
swap to your own L2 instance and want to predict what the dashboards
will surface before you load real data, start here. The headline
numbers below describe the generator as it stands; the SHA256 hash-lock
tests in `tests/data/test_l2_baseline_seed.py` keep them from drifting
silently.

The pipeline lives in `src/quicksight_gen/common/l2/seed.py`
(baseline + composer) plus
`src/quicksight_gen/common/l2/auto_scenario.py` (plant overlays). The
CLI `data apply` calls `build_full_seed_sql(cfg, instance)` in
`src/quicksight_gen/cli/_helpers.py`, which composes the four-stage
pipeline:

```
default_scenario_for(instance)        # the auto-derived plant scenario
  → densify_scenario(factor=5)        # per-kind plant replication
  → add_broken_rail_plants(15)        # one visibly-broken rail
  → boost_inv_fanout_plants(×5)       # Investigation cluster amount bump
  → emit_full_seed(instance, ...)     # baseline + plants → SQL
```

`emit_full_seed` is the public entry point. It calls
`emit_baseline_seed` for the 90-day healthy baseline, then concatenates
the legacy `emit_seed` plant SQL on top. Both halves target the same
`{{ l2_instance_name }}_transactions` and `{{ l2_instance_name }}_daily_balances` tables. Plant
account ids and baseline account ids live in disjoint pools
(plants on `cust-0001`–`cust-0010`, baseline on `cust-0011`+) so the
`(account_id, business_day)` PK on `daily_balances` never collides.

## Window and anchor

The baseline emits a **90-day rolling window** ending on the
`anchor` date (defaults to UTC `datetime.now().date()` at call time).
Every `posted_at` and `business_day` literal in the generated SQL is
computed against this anchor, not against `now()` at apply time, so
the SHA256 hash-lock stays deterministic for a fixed anchor. The
canonical anchor for hash-lock tests is `date(2030, 1, 1)`.

Re-running on a different day produces a different rolling window —
same anchor convention as the legacy plant emitter.

## Volume per Rail — `target_leg_count(rail, window_days=90)`

Each Rail is classified into one of 12 `_RailKind` values by
`_classify_rail(rail)`, which inspects the rail's `aggregating` flag,
`cadence`, and `rail_name` substring. The classifier is heuristic
(falls back to `OTHER` for anything novel) — calibrated against the
bundled L2 instances ({{ l2_instance_name }} included).

The table below shows the **per-business-day firing target** per kind,
and the rough scaling rule. Each firing emits 1 or 2 legs depending on
rail shape (single-leg vs two-leg vs aggregating-with-children).

| Rail kind | Daily target / unit | Scales by |
|---|---:|---|
| Customer-facing inbound (`ach_inbound`, `wire_inbound`, `cash_deposit`) | 4.0 | customer-account count |
| Customer-facing outbound (`ach_outbound`, `wire_outbound`, `cash_withdrawal`) | 2.0 | customer-account count |
| Customer fee (monthly) | ~0.045 (1/22) | customer-account count |
| Internal transfer (debit / credit / suspense) | 1.0 | system |
| Aggregating daily (ZBA sweep, ACH origination sweep) | 1.0 | system |
| Aggregating intraday | 4.0 | system |
| Aggregating monthly (fee batch) | 1.0 (last business day only) | system |
| Concentration sweep (`wire_concentration`) | 1.0 | system |
| Card sale (merchant-acquiring) | 8.0 | merchant-account count |
| Merchant payout (`payout_*`) | 1.0 | merchant-account count |
| External card settlement | 1.0 | system |
| ACH return (NSF, stop-pay) | 0.2 | customer-account count |
| Other (fallback) | 1.0 | system |

`_baseline_target_leg_count(rail, kind, customer_count, merchant_count, business_day_count)`
multiplies the daily target by the relevant scaling unit and the
business-day count, then rounds to an integer. Per-day variance is
introduced by the per-Rail RNG sub-stream (Poisson-style sampling
inside the leg loop), so two runs against the same anchor produce
byte-identical SQL but per-day counts naturally fluctuate.

Headline volumes scale with the account universe of the L2 instance.
For an instance with the typical mix of customer + merchant accounts
the bundled fixtures use, the baseline produces on the order of
**5,000 to 65,000 transaction rows** over the 90-day window — a
small reference instance lands at the low end, an instance with
enough customer accounts to feel like a working bank lands at the
high end (~47 MB of generated SQL).

## Amount distribution — per-Rail-kind lognormal `(mu, sigma)`

All amounts in USD. Sample shape: `exp(Normal(mu, sigma))`, quantized
to cents. Bounded above by `LimitSchedule.cap` when one applies
(clamp+resample, not truncate, so the distribution shape stays clean).
Lognormal was picked deliberately so the long right tail produces the
natural outliers Investigation Volume Anomalies needs to compute
meaningful z-scores against.

| Rail kind | mu | sigma | Median $ | 99th pct $ |
|---|---:|---:|---:|---:|
| Customer ACH (in/out) | 6.5 | 1.2 | $665 | ~$11,000 |
| Customer fee accrual | 2.5 | 0.4 | $12 | ~$31 |
| Internal transfer | 8.0 | 1.5 | ~$2,980 | ~$96,000 |
| Aggregating daily (sweep) | 11.0 | 0.8 | ~$59,800 | ~$385,000 |
| Aggregating intraday | 10.5 | 0.8 | ~$36,000 | ~$230,000 |
| Aggregating monthly (fee batch) | 9.5 | 0.5 | ~$13,400 | ~$43,000 |
| Concentration sweep | 12.0 | 0.7 | ~$163,000 | ~$830,000 |
| Card sale | 4.5 | 0.9 | ~$90 | ~$720 |
| Merchant payout | 9.0 | 1.1 | ~$8,100 | ~$105,000 |
| External card settlement | 11.5 | 0.6 | ~$98,700 | ~$400,000 |
| ACH return | 6.5 | 1.2 | ~$665 | ~$11,000 |
| Other (fallback) | 7.0 | 1.0 | ~$1,100 | ~$11,000 |

`(mu, sigma)` lives on `_RailKindParams` in
`src/quicksight_gen/common/l2/seed.py`; per-kind defaults at
`_RAIL_KIND_PARAMS`.

## Time distribution

- **Day-of-week.** Weekends (Sat/Sun) drop to **0 firings** for all
  rails. US bank holidays drop to **0 firings** for all rails.
  Holiday calendar uses the `holidays` package when available, falling
  back to a hard-coded list of fixed-date federal holidays.
- **Day-of-month.** Rails with `aggregating: monthly_eom` fire **only
  on the last business day of each month** (3 firings over a 90-day
  window). Rails with `aggregating: daily_eod` fire on every business
  day. Non-aggregating rails fire uniformly across business days.
- **Time-of-day.** Per-kind time bands on `_RailKindParams.time_band`,
  uniform-sampled within the band:
    - Customer inbound + merchant payout: 09:00–15:00 ET (banking hours)
    - Customer outbound + customer fee + internal transfer + ACH return + other: 09:00–17:00 ET
    - Card sale: 10:00–22:00 ET (extended retail hours)
    - Aggregating daily (parent post): 17:00–19:00 ET (after children)
    - Aggregating monthly (parent post): 17:00–19:00 ET on the last business day
    - Aggregating intraday: 09:00–17:00 ET
    - Concentration sweep + external card settlement: 15:00–17:00 ET

Posting timestamps drive the daily-statement KPI shape — the EOD
aggregating-rail parent is what bundles the day's child legs into a
single transfer.

## RNG sub-stream layout — `_seed_for_rail(rail_name)`

```python
_BASELINE_BASE_SEED = 42  # legacy hash continuity

def _seed_for_rail(rail_name: str) -> int:
    return _BASELINE_BASE_SEED ^ (
        zlib.crc32(str(rail_name).encode("utf-8")) & 0xFFFFFFFF
    )
```

Each Rail gets one `random.Random(_seed_for_rail(rail.name))` instance.
That instance is threaded through every helper that touches that Rail —
leg loop, amount sampler, time-of-day sampler, account picker. The XOR
guarantees each Rail's stream is independent of every other Rail's
even when one Rail is renamed; iterating on one Rail's plant doesn't
perturb other Rails' baselines.

Cross-Rail randomness (account picks, starting balances) uses a
separate `random.Random(_BASELINE_BASE_SEED)` instance, so per-Rail
isolation holds end-to-end.

**Concurrency is not a concern**: the seed generator runs
single-threaded (one Python process emits SQL); the DB apply runs
single-threaded (one cursor, sequential statements); the e2e harness
runs per-test under pytest-xdist but each worker is its own process
with its own seed import — no cross-process shared state.

## Starting balances — per-account-role kind

Per `account_role` lookup, sampled per-account at generator init via
`_initialize_starting_balances`. Role-name substring matching
classifies each account into one of six kinds (see `_classify_role` in
`seed.py`); the kind picks a lognormal `(mu, sigma)` for the starting
balance distribution.

| Role kind | (mu, sigma) | Median balance |
|---|---|---:|
| `customer_dda` (customer DDA control) | (11.0, 0.5) | ~$60,000 |
| `merchant_dda` (merchant DDA control) | (12.5, 0.5) | ~$268,000 |
| `internal_gl` (cash, settlement, due-from, clearing, ACH origination) | (17.5, 0.3) | ~$40M |
| `concentration` (ConcentrationMaster) | (17.5, 0.3) | ~$40M |
| `internal_suspense` (suspense, recon, ZBA sub-accounts) | (13.5, 0.5) | ~$1M |
| `external` (FRB Master, processors, card networks) | None | $0 |
| `other` (fallback) | None | $0 |

The internal-GL and concentration medians are sized to comfortably
absorb one window's worth of cascading sweep activity without
accidentally tripping the overdraft invariant — the realistic baseline
is meant to look like a healthy bank with occasional planted
exceptions, not a bank where every internal account is constantly
overdrawn. External counterparty balances aren't tracked (the external
side of a force-posted Fed leg has no `daily_balances` row).

## Multi-leg + chain ordering

- **Single-leg rail.** One row per firing with the leg posted at the
  sampled time-of-day.
- **Two-leg rail.** Two rows per firing with shared `transfer_id`,
  `signed_amount` summing to zero, both posted at the same time-of-day
  (within ms).
- **Aggregating rail (children-first).** Child legs accumulate
  throughout the day at sampled times-of-day. The EOD (or EOM) parent
  posts at 17:00–19:00 ET as a higher-Entry row with
  `Supersedes = BundleAssignment` that retroactively assigns the day's
  children to its `transfer_id`. Stuck-unbundled-aging plants are the
  few children that **never** get a parent assignment.
- **Non-aggregating chain (parent → child).** Parent fires first;
  child fires synchronously (same business day, child time-of-day =
  parent time-of-day + small delay). Required-completion vs
  Optional-completion respects the Chain's declared `Required` flag:
  Required ≈ **95%** completion rate, Optional ≈ **50%**. The
  remaining percentage shows up as orphan-chain rows on the L2
  Exceptions sheet.

## Daily-balance materialization

For every `(account, business_day)` in the window, the
`_emit_baseline_daily_balances` pass walks the per-account leg log
(populated during the leg loop) and computes the EOD balance as
`starting_balance + cumulative SUM(signed_amount)`. The drift matview
computes `stored - SUM(signed_amount)` over the same data, so baseline
rows must keep that at zero — `_BaselineState.eod_balances` is the
single source of truth for both halves.

The deferred-walk pattern (compute EOD balances only after every Rail
has emitted, not per-leg) avoids a subtle cross-Rail bug where Rail B's
day-1 leg snapshot would have over-written Rail A's day-1 cumulative
balance because rails iterate in name order across all days.

## Plant catalog

`default_scenario_for(instance)` walks the L2 declarations and tries
to materialize **one plant per kind** that the instance can support.
The picker is conservative: if any plant kind has no matching L2
primitive (no `LimitSchedule`, no `chains`, etc.) it gets skipped
with an `omitted` reason rather than raising. Each plant kind targets
a specific dashboard sheet, exists as a frozen dataclass in
`common/l2/seed.py`, and resolves into one or more transactions when
`emit_seed(instance, scenario)` runs.

| Plant | Surfaces on | Picker condition (when fires) |
| --- | --- | --- |
| `DriftPlant` | L1 Drift | At least one materialized customer DDA + a rail that lands on it. |
| `OverdraftPlant` | L1 Overdraft | A two-leg rail whose source role resolves to a non-`gl_control` account. |
| `LimitBreachPlant` | L1 Limit Breach | The L2 declares ≥1 `LimitSchedule` with a matching outbound rail. |
| `StuckPendingPlant` | L1 Pending Aging | A rail with `max_pending_age` declared. |
| `StuckUnbundledPlant` | L1 Unbundled Aging | A rail with `max_unbundled_age` declared AND that's named in some `bundles_activity`. |
| `SupersessionPlant` | L1 Supersession Audit | Always (writes a higher-`entry` row on existing `(account, day)`). |
| `InvFanoutPlant` | Investigation Recipient Fanout | At least one ACH-shaped rail and ≥12 customer DDAs to source senders from. |
| `TransferTemplatePlant` | L2 Flow Tracing — Transfer Templates | Per declared `transfer_templates` entry whose first `leg_rails` resolves and `expected_net == 0` (M.3.10g + v8.6.7 SingleLegRail extension). |
| `RailFiringPlant` | L2 Flow Tracing — Rails / Chains | **Broad mode only.** Per declared rail whose role(s) resolve to a materialized account. |

When the picker can't materialize a plant, it appends an entry to
`AutoScenarioReport.omitted` — a tuple of `(plant_label, reason)`.
Surfacing an unexpected omitted reason is the fastest way to debug
"why is dashboard sheet X empty?" — see the live scenario section
below.

## Scenario modes

`default_scenario_for(instance, mode=...)` picks WHICH plant kinds
land in the returned `ScenarioPlant`. Three modes (M.4.2 +
v8.6.7 demo bump):

| Mode | L1 SHOULD plants (drift / overdraft / breach / stuck_* / supersession / inv_fanout) | Broad-shape plants (`TransferTemplatePlant`, `RailFiringPlant`) | Used by |
| --- | --- | --- | --- |
| `l1_invariants` (default) | ✓ | ✗ | M.4.1 harness Layer 1 (matview-row presence checks) |
| `broad` | ✗ | ✓ | M.4.1 harness — pure shape verification |
| `l1_plus_broad` | ✓ | ✓ | The CLI `data apply` demo path (`cli/_helpers.py::build_full_seed_sql`) — gives the demo BOTH planted SHOULD violations AND non-empty L2FT sheets. |

The CLI's choice (v8.6.7+) of `l1_plus_broad` is what makes the
demo's L2FT Transfer Templates / Rails / Chains sheets render
non-empty. Pre-v8.6.7 the demo ran in `l1_invariants` mode, so even
when `TransferTemplatePlant` rows existed in code, they got filtered
out before reaching `emit_full_seed`.

## Live scenario for the active L2

The block below is rendered at docs build time against the L2
instance you build with (`{{ l2_instance_name }}.yaml`), anchored
at canonical today (`2030-01-01`) for reproducibility:

{% set _ssum = scenario_summary("l1_plus_broad") %}
> **Mode:** `{{ _ssum.mode }}` · **Anchor:** `{{ _ssum.today }}` ·
> **Instance:** `{{ _ssum.instance }}`

{% for p in _ssum.plants %}
**{{ p.kind }}** — `{{ p.count }}` planted. {{ p.what }}
{% if p.samples %}
{% for s in p.samples %}
  - `{{ s }}`
{% endfor %}
{% endif %}

{% endfor %}
{% if _ssum.omitted %}

### Omitted plants

The picker skipped these for the active L2 — typically because the
L2 doesn't declare a matching primitive, or because the picker
doesn't yet handle a particular shape. Check this list first when a
dashboard sheet renders empty:

{% for o in _ssum.omitted %}
- **{{ o.plant }}** — {{ o.reason }}
{% endfor %}
{% endif %}

## Plant overlays

The baseline produces a **healthy** ledger — every L1 invariant clean,
every Chain mostly-complete, every TransferTemplate netting to zero.
Plant overlays then layer **intentional violations** on top so the
dashboards have signal to render. The overlay pipeline is three stages:

### 1. `densify_scenario(base, factor=5)`

The auto-derived `default_scenario_for(instance)` produces one plant
per L1 invariant kind (1 drift, 1 overdraft, 1 limit-breach, etc.).
At 60k baseline rows per instance, a single plant gets visually lost.
`densify_scenario` replicates each plant by varying `days_ago` so each
kind shows ~5 rows on the dashboards instead of 1. The default factor
is **5** (`factor=5`).

`inv_fanout_plants` and `transfer_template_plants` are NOT replicated:
the fanout already plants N senders per recipient (its own density),
and TransferTemplate plants already produce 3 firings per template
(the Complete / Orphan / Required-met cases).

### 2. `add_broken_rail_plants(scenario, instance, broken_count=15)`

For visual hierarchy: pick one Rail (deterministic — sorted by name,
the first rail with `max_pending_age` set) and plant **15** stuck-pending
entries on it across the window. Today's Exceptions KPI then has a
magnitude that matters; the L2 Exceptions sheet's bar chart shows the
broken Rail spike immediately as one tall bar against the baseline.

### 3. `boost_inv_fanout_plants(scenario, amount_multiplier=5)`

The default `InvFanoutPlant.amount_per_transfer` from the auto-scenario
sits below the customer-ACH baseline median. Without a boost, the
12-sender → 1-recipient cluster is structurally visible on the
Recipient Fanout sheet but per-transfer amounts don't stand out.
This stage bumps each fanout plant's amount by **5×** so the cluster's
aggregate inflow stands out clearly on the Recipient Fanout sheet's
Sankey and Volume Anomalies' z-score band.

## L2 coverage assertion set

A separate harness file `_harness_l2_coverage_assertions.py`
(referenced from the broader e2e harness) cross-checks every L2
declaration against runtime evidence. The assertion set is the
contract Phase R locked in alongside the realistic baseline:

- **Per Rail.** `assert N_legs(rail) >= max(target_leg_count(rail) * 0.5, 5)` — at
  least half the heuristic target landed (Poisson variance can shave the
  actual count) AND no rail produces fewer than 5 legs (proves the rail
  isn't dead).
- **Per Chain.** `assert N_completed_pairs(chain) >= 1` — every declared
  chain has at least one parent + child fire. For singleton-children
  chains (the required case) additionally:
  `assert completion_rate(chain) >= 0.80` (allowing some exception
  slack from plant overlays). Multi-children (XOR) chains check that
  exactly one of the listed children fired per parent invocation.
- **Per TransferTemplate.** `assert sum(actual_net) == declared expected_net`
  for ≥ 80% of template instances (some plants intentionally violate
  to surface on the L2 Exceptions sheet).
- **Per LimitSchedule.** `assert max_daily_outbound(parent_role, rail_name) <= cap`
  for the baseline (plants intentionally breach to populate the Limit
  Breach sheet).
- **Volume Anomalies signal.** `assert z_score(planted_spike, baseline) >= 3.0` —
  the Investigation matview's rolling-2-day-stddev produces a planted-
  spike z-score in the dashboard's "high" anomaly bar coloring band.

The assertion set runs after `data apply` against the live demo DB
(see `tests/test_l2_runtime_assertions.py` for the public surface).
Skip cleanly when no demo DB URL is configured.

## Out of spec / open

- **Per-Rail YAML overrides** (`seed_amount`, `seed_volume_multiplier`)
  are out of scope. The baseline heuristic is enough for the bundled L2
  instances; add the override hook if a third instance needs a per-rail
  tweak the kind-classifier can't infer.
- **Cross-currency.** All amounts USD. No FX rails declared in the
  bundled instances.
- **Memo / metadata-payload realism.** The generator emits valid
  metadata structures (the L2's declared keys with random plausible
  values) but doesn't aim for narrative realism. The Investigation
  walkthroughs reference specific amounts and counterparties; those
  land via plant overlays, not the baseline.
- **Causal cascade ordering.** The current leg loop emits rails in name
  order, not in causal cascade order — a few intermediate clearing
  accounts therefore swing into negative under realistic ETL timing.
  See the `Phase V` backlog for the leg-loop refactor.

## Determinism and the hash lock

A pair of SHA256 hash-lock tests in
`tests/data/test_l2_baseline_seed.py` (one per bundled L2 fixture)
pin the full pipeline output byte-for-byte against canonical anchor
`date(2030, 1, 1)`. Any generator change that shifts a single byte
fails the hash-lock loudly — re-lock by pasting the new SHA into the
test when the change is intentional.

The hash-lock is the proof that the pipeline is fully deterministic:
fixed anchor → fixed bytes. Every random source above (per-Rail RNG,
account picks, starting balances) seeds off `_BASELINE_BASE_SEED = 42`
or a CRC32-derived per-Rail offset.

## Reference

- [Schema v6 — Data Feed Contract](../Schema_v6.md) — the column
  contract the seed populates against. The two-table base (`transactions`
  + `daily_balances`) is the same shape your production ETL writes.
- [L1 Invariants](../L1_Invariants.md) — what each `{{ l2_instance_name }}_*`
  invariant view returns. The plant overlays are designed so each L1
  invariant has at least one violating row to render.
- [L1 Reconciliation Dashboard](l1.md) — the operator-facing
  visualization the seed feeds.
- [L2 Flow Tracing Dashboard](l2_flow_tracing.md) — surfaces dead
  Rails / Chains / Templates / LimitSchedules; the L2 coverage
  assertion set is its runtime backbone.
- [Data Integration Handbook](etl.md) — the ETL-engineer view of the
  same two tables. Useful when comparing "what the demo seed produces"
  against "what your production feed should produce".
