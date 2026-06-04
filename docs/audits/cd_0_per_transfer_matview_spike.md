# CD.0 — per_transfer matview spike

**Date:** 2026-06-04
**Branch:** `cd-0-per-transfer-matview-spike`
**Driver:** CI top-queries on run 26971887549 flagged the `per_transfer` CTE pattern as the largest aggregate-wall-time hot spot (~38s/run across 70+ Executives dataset calls).

## Measurement harness

- PG 17 in a fresh docker container (`postgres:17-alpine` + `pg_stat_statements`), single-writer (no xdist contention).
- `sasquatch_pr` seed via `recon-gen data apply`, then densified 10× by `INSERT...SELECT` with `transfer_id || '-r' || g.n` to reach ~128k transactions (matches CI's seed size per the `densify_scenario(factor=5)` defaults).
- 67,140 rows in `<prefix>_per_transfer_rollup` after refresh.
- Date window for the test query: 2025-09-01 → 2026-06-02 (full sasquatch_pr range).
- 3 warm executions per shape, `EXPLAIN (ANALYZE, BUFFERS)`.

## Numbers

| Shape | Warm mean | Speedup |
|---|---:|---:|
| Baseline (current CTE) | 195 ms | 1.0× |
| Matview-fed | 47 ms | 4.1× |
| Matview refresh | 200 ms (once per Session Start) | — |

CI extrapolation (~10 per_transfer-shape queries × ~870ms mean in production scale; ~10 isolated test prefixes per run):

- **Query savings:** ~29 s / run
- **Refresh cost:** ~2 s / run (10 prefixes × 200ms)
- **Net:** ~27 s / run wall-clock reduction

## Blocking finding — semantic divergence

The matview is **not** a drop-in replacement. Cross-checking `(date, rail, count, gross, net)` between baseline and matview at 128k rows produced 1032/1033 identical rows + 1 row of divergence:

```
posted_date | rail_name           | count | gross    | net (baseline) | net (matview)
2026-05-05  | ExternalRailInbound | 110   | 19056690 | 18000000       | 22500000
```

Same date, same rail, same transfer count, identical gross — only `net_amount` differs.

### Root cause

The baseline's date filter runs on `t.posting` **before** the per-transfer `GROUP BY`:

```sql
FROM <prefix>_transactions t
WHERE t.status = 'Posted' AND <date_clause on t.posting>
GROUP BY t.transfer_id, t.rail_name
```

For a transfer whose legs span the window boundary (one leg inside the window, another outside), the baseline aggregates **only the in-window legs**. `SUM(amount_money)` excludes the out-of-window legs.

The matview pre-aggregates **all legs of every transfer** at refresh time, then we filter by `MIN(posting)::date`. For the same straddling transfer, the matview's `transfer_net` is the SUM over ALL legs.

The two semantics diverge on transfers whose legs straddle the window boundary. Gross (`MAX(ABS)`) survives because absolute-value-max is dominated by the larger leg regardless of which side of the window it's on; net (`SUM`) is sensitive to which legs are included.

### Why count + gross still matched

- `count` is "number of distinct (transfer_id, rail) groups in the window" — both shapes pick up the same transfer when its first-by-date leg is in the window.
- `gross = SUM(MAX(ABS))` — the max absolute leg-magnitude per (transfer, rail) is the same value regardless of leg subset, because the dominant-magnitude leg happens to be the one inside the window for this particular transfer.

## Options for CD.1+

**A. Accept the semantic change.** Treat the matview as "per-transfer aggregations, anchored by initiation date." Most dashboards for transfer-event reporting want this semantic anyway — "transfers initiated in March" is more analyst-natural than "leg-counts trimmed to March." Risk: anyone today reading the Executives Rails sheet has been seeing the partial-aggregation numbers; switching changes their interpretation.

**B. Materialize at leg granularity.** Drop the per-transfer aggregation entirely; the matview becomes `(posted_date, transfer_id, rail_name, amount_money)` per leg with one row per leg. Downstream CTEs re-aggregate as today. This restores exact equivalence but the matview is the same size as base table — no perf win.

**C. Materialize per (leg_date, transfer_id, rail_name).** `GROUP BY posting::date, transfer_id, rail_name` instead of `GROUP BY transfer_id, rail_name`. Each transfer that spans N days appears in N rows. Downstream CTEs re-aggregate (transfer_id, rail_name) at query time, recovering exact baseline semantics. Matview size estimate: most transfers are single-day so size ~ same as option A (~67k); a transfer with multi-day legs adds a few extra rows. **Perf impact:** the downstream re-aggregation step partially undoes the win — still saves the date-window scan + the heaviest aggregation, but loses some.

## Recommendation

**Option C — measured and confirmed.**

Re-spike with Option C (`GROUP BY posted_date, transfer_id, rail_name` at refresh; re-aggregate per `(transfer_id, rail_name)` downstream of the date filter):

| Metric | Baseline | Option A (naive) | Option C |
|---|---:|---:|---:|
| Shape A warm (ms) | 195 | 47 | **69** |
| Shape B warm (ms) | 60 | n/m | **38** |
| Shape A speedup | 1.0× | 4.1× | **2.8×** |
| Shape B speedup | 1.0× | n/m | **1.6×** |
| Refresh cost (ms) | — | 200 | 178 |
| Matview row count | — | 67,140 | 67,440 |
| Row-equiv to baseline | — | ❌ (1 of 1033 diverges) | **✅ 1033 / 1033 match** |

Shape B equivalence also verified (63 / 63 rows match).

Option C costs ~22ms vs Option A on Shape A (extra downstream GROUP BY) but is byte-equivalent to baseline. Matview is only 300 rows larger (~0.4%) — multi-day transfers are rare in this seed. Net CI estimate: ~20-25 s/run saved (slightly less than Option A's 27s, but no behavior change).

**Going with Option C for CD.1.**

## Spike infrastructure (cleanup)

- Container: `recon-cd0-pg` (port 5440) — `docker rm -f recon-cd0-pg` to clean up.
- Cfg: `run/config.cd0-spike.yaml` (gitignored under `run/`) — safe to delete.
- Spike SQL: `/tmp/cd0_baseline.sql`, `/tmp/cd0_create_mv.sql`, `/tmp/cd0_matview_a.sql`, `/tmp/cd0_equiv.sql`, `/tmp/cd0_diff.sql`.
