# CE.0 — Trainer Session Start as session fixture spike

**Date:** 2026-06-04
**Branch:** `ce-0-trainer-session-fixture-spike`
**Driver:** Backlog #249 — trainer dogfood [pg]/[or] times out under CI's 16-worker xdist because every test re-runs Session Start (including `/etl/run`).

## Approach

`/training/reclone` (BV.4.9) calls the same `session_start(...)` orchestrator with `refresh_base=False` — skips the expensive `/etl/run` phase but still does the v-overlay drop/create/clone/matview-refresh dance. If reclone gives an equivalent ready-to-plant surface, we can split:

- **Session-scope fixture:** one Session Start per xdist worker (does `/etl/run` + initial v overlay creation)
- **Per-test fixture:** call reclone (cheap reset of v overlay; base prefix stays populated)

## Measurement harness

- PG 17 in fresh `postgres:17-alpine` docker container (port 5440, `pg_stat_statements` loaded).
- `sasquatch_pr` L2 (16 accounts, default seed density — no `--seed-density=5`).
- Direct invocation of `recon_gen.common.l2.v_overlay.session_start(cfg, l2, refresh_base=...)` from a probe script (bypasses HTTP/asyncio plumbing — measures the pure orchestration cost).
- Pass 1: full Session Start (`refresh_base=True`). Pass 2-4: reclones (`refresh_base=False`). Each measured wall-clock.

## Numbers

| Phase | Time | Notes |
|---|---:|---|
| Full Session Start | **32.84 s** | `/etl/run` = 19.5s; v overlay drop+create+clone+refresh = 13.3s |
| Reclone #1 | 13.48 s | v overlay only |
| Reclone #2 | 13.52 s | |
| Reclone #3 | 13.52 s | |
| **Reclone mean** | **13.51 s** | per-test cost under the fixture model |
| Speedup ratio | **2.4×** | per-test |

## Wall-clock projection

| Tests | Baseline (full per test) | Fixture (1 full + N reclone) | Saved |
|---:|---:|---:|---:|
| 2 | 65.7 s | 46.3 s | 19.3 s (29%) |
| 5 | 164.2 s | 86.9 s | 77.3 s (47%) |
| 10 | 328.4 s | 154.4 s | 174.0 s (53%) |
| 16 | 525.4 s | 235.4 s | 289.9 s (55%) |
| 7 (current trainer kinds) | 230 s | 114 s | 116 s (50%) |

**Break-even at 1.7 tests.** Even the smallest test set benefits.

## Oracle extrapolation (not measured locally — see "Risks")

The in-code estimate at `_studio_routes.py:479` puts `/etl/run` at ~10 min on Oracle. The v overlay clone path uses the same `<prefix>_*` table copy operations, which Oracle handles slower than PG but proportionally to the seed size — call it 1-2 min based on the schema-apply timings we've already observed at the CB.17.i / CB.17.k benchmarks.

| Tests | Baseline (10 min/test) | Fixture (1×10min + N×1-2min) |
|---:|---:|---:|
| 7 | **70 min** ❌ wire-blown | 10 + 6×1.5 = **19 min** ✅ |
| 16 | 160 min ❌ | 10 + 15×1.5 = **33 min** |

Confirms backlog #249's root cause and the fix's leverage.

## Does reclone yield a ready-to-plant surface?

Yes — BV.4.9 established the contract. Reclone:

1. `drop_v` — wipes the v overlay schema (idempotent)
2. `create_v` — recreates schema
3. `clone` — copies base → v overlay data tables
4. `refresh_matviews` — refreshes v overlay matviews

Step 4 is the key: matviews are live after reclone, so any plant that targets a v matview will find its surface. The probe's `session_start:refresh_matviews_done` event fires on every reclone — confirms.

## Risks / caveats

- **Oracle Reclone cost not measured locally.** Would need to spin Oracle locally (~3-min cold start) to confirm; the PG curve plus the in-code estimate gives high-confidence projection but a real number would tighten CE.4's "is it green?" decision.
- **Per-test state contamination on the same xdist worker.** `isolated_studio_cfg` already gives each test its own worker-suffix base prefix, so reclone-of-the-overlay should be sufficient — but worth confirming the fixture wires through correctly during CE.2/CE.3.
- **Studio-server lifecycle.** Each trainer test currently spawns its own `studio_server`. The session-scope fixture should keep that per-test (cheap, in-process) and only share the Session Start state at the DB level.
- **`/training/cleanup` vs `/training/reclone` semantics.** Cleanup drops v overlay without reclone; reclone drops + reclones. Reclone is the right choice for the per-test reset (leaves a populated overlay ready for plant); cleanup would leave nothing for the next test to plant against.

## Decision

**Proceed with CE.1-CE.4 per the plan.** PG numbers alone justify the refactor; Oracle is the bigger win and validates against the actual wire timeout once we unpin `RECON_GEN_TRAINER_DIALECTS=du` in CE.4.

## Spike infrastructure (cleanup)

- Container: `recon-ce0-pg` (port 5440) — `docker rm -f recon-ce0-pg`
- Cfg: `run/config.cd0-spike.yaml` (gitignored; reused from CD.0)
- Probe script: `/tmp/ce0_spike.py`
