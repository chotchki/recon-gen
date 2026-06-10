# CV.0 — BU.1.11 calibration findings + design pivot

**Filed 2026-06-09.** Output of two parallel investigations: (1) my in-line empirical sweep on both L2s under TRAINER_CLEAN, and (2) workflow `wf_b8472fa4-466` (14 agents, 1.2M tokens) which did a comprehensive formula+magnitude sweep with adversarial verify. Conclusion: **the matview math cannot be fixed by formula choice alone — the AnomalyGenerator's plant emission shape is the load-bearing change.**

## Empirical baseline measurements (both L2s under TRAINER_CLEAN)

| L2 | `pop_mean` | `pop_stddev` | top `window_sum` | top z (current) | n≥4 | n≥3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| spec_example | $5,136 | $13,535 | $121,794 | **8.62** | 20 | 27 |
| sasquatch_pr | $7,173 | $14,094 | $371,337 | **25.84** | 52 | 70 |

Where the 20 high-z rows on spec come from: all 12 high-z pairs sit in the >10-active-days bucket (max is 65 active days — the BUSIEST pair). All target `external-counterparty-one → tmpl-cust-*` payday-cluster spikes Mar 10-11. **Opposite of the BU.1.11 leaf's "sparse-rail variance" hypothesis.**

## Why the synth's chosen "CoV floor K=6 + spike 100K→500K" fails on sasquatch_pr

I empirically swept K ∈ {4, 5, 6, 7, 8, 10}:

| K | spec top z (post-floor) | sasq top z (post-floor) | both ok? |
| ---: | ---: | ---: | --- |
| 4 | 5.68 | 12.69 | ✗ |
| 5 | 4.54 | 10.15 | ✗ |
| 6 | **3.79** | 8.46 | ✗ |
| 7 | 3.24 | 7.25 | ✗ |
| 8 | 2.84 | 6.35 | ✗ |
| 10 | 2.27 | 5.08 | ✗ |

K=13 would clear sasq, but at K=13 the spike-magnitude to hit z≥4 is ~$300K cents → unrealistic plant geometry.

## Alternative formulas (TRAINER_CLEAN baseline only)

| Formula | spec top z / n≥4 | sasq top z / n≥4 |
| --- | --- | --- |
| F1 current (global stddev) | 8.62 / 20 | 25.84 / 52 |
| F2 log-z (global) | 2.92 / 0 | 3.88 / 0 |
| F3 per-pair z | 6.25 / 32 | 6.05 / 35 |
| F4 per-pair-log | 2.90 / 0 | 2.95 / 0 |
| C MAD z | 48.45 / broken | 90.70 / broken |

**F2 log-z and F4 per-pair-log both kill baseline false positives**, but workflow #2's empirical sweep added the critical missing piece: **what happens to PLANTS under each formula**.

## The plant-detection probe (LOCKED_SEED, baseline + L1 plants)

| Formula | spec plant top z | sasq plant top z | plant detectable above noise? |
| --- | --- | --- | --- |
| F1 current | 8.85 (same as baseline) | 25.92 (same) | NO — plant indistinguishable |
| F2 log-z | 2.99 | 3.88 | NO — z<4 even with plant |
| F3 per-pair | **6.25 → equals baseline** | 6.05 → equals baseline | NO — see below |
| F4 per-pair-log | 2.90 → equals baseline | 2.95 → equals baseline | NO — see below |

## The structural revelation that locks the direction

Workflow #2's empirical phase surfaced **why F3/F4 (per-pair) gave zero signal**: `AnomalyGenerator` emits the spike between `sender_account_id`/`recipient_account_id` IDs that have **no prior history**. The matview's per-pair `STDDEV_SAMP` requires ≥2 historical samples; one-shot synthetic spike pairs get `stddev=NULL → CASE→0 → z=0`. Plants vanish entirely under per-pair.

**This is structural, not a coding bug.** Per-pair z is the semantically correct formula (matches the matview's "this pair moved enough money in a 2-day window that, compared to *its own past*, this one is N standard deviations out" docstring intent), but it requires plants that emit per-pair history.

## Other revelations from workflow #2

1. **The matview is L2 Investigation (AML/fraud), NOT regulator-facing L1.** AT.5.d lock: *"the audit PDF stops at L1 ... anomaly + money_trail surface only on the Investigation dashboard. Different audience by design."* BU.1.11 isn't an audit-compliance issue.

2. **σ=2 dashboard default has never been calibrated.** Spine convention is σ=3.0 (`AnomalyView.sigma_threshold = 3.0`). v11_21_0_triage explicit: *"the default σ is uncalibrated and the seed cluster shape is uncalibrated, we picked the cheapest workaround [accept-zero + KPI rename in BH.5]."*

3. **InvFanoutGenerator boost is checksum-grade.** Per `inv_fanout.py`: *"NOT registered for AnomalyInvariant: the rolling-window z-score is probabilistic. The boost is deliberately multiplied to push it over."* The fanout plant's primary surface is the Recipient Fanout sheet — anomaly trip is a side effect.

## Operator decision 2026-06-09

**Direction: pivot to AnomalyGenerator restructure.** The per-pair z path (F3/F4) is semantically correct but requires plants that emit per-pair historical baseline. Restructure `AnomalyGenerator` to:

1. For each spike pair, emit **N historical windows** (≥5 for sensible `STDDEV_SAMP`) over the past M days at small-amount baseline.
2. Then emit the spike on `anchor_day`.
3. The per-pair stddev now has data to work against; the spike's z-score is meaningful.

Switch the matview formula to per-pair PARTITION BY (F3 with min-n floor). The dashboard threshold stays at σ=2 (acceptable post-fix because per-pair z is naturally scale-invariant — payday clusters no longer trip).

Phase CV restructured to track this work (see updated PLAN.md). Synth-estimated XL/9h; farmed to a worktree agent in parallel with Phase CW.
