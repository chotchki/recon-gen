# DK.0 — Data-anchor refactor (design lock)

**Date:** 2026-06-15
**Phase:** DK.0
**Status:** Locked. Operator-confirmed in PLAN.md `## Phase DK` block on 2026-06-15. Ready for DK.1.

## Problem statement

Operator observation 2026-06-15 — when `cfg.test.generator.end_date` is unset and the most recent emitted transactions / balances are from a prior month, dashboards render blank for the default `[as_of - N, as_of]` date window. The mechanic:

1. `cfg.test.generator.as_of_frame()` falls through to `AsOfFrame.live()` when `end_date` is `None` → `as_of = date.today()`.
2. Date-scoped dataset filters resolve to `posting BETWEEN today - N AND today`.
3. The `<prefix>_effective_balances` matview's calendar-day spine is bounded by `MIN/MAX(business_day_start) FROM <prefix>_current_daily_balances WHERE account_scope='internal'` (`src/recon_gen/common/l2/schema.py:979` — `in_scope_calendar_days_cte`).
4. With feed last-loaded month ago: spine ends month ago; window asks for "today"; no rows; carry-forward via LAST_VALUE inside the spine cannot reach past `MAX(business_day_start)`.
5. Operator sees blank dashboards, no error, no banner — silent footgun.

The wall-clock fallback is the root cause: real-world feeds always lag wall-clock by some interval, so the fallback's "today" is structurally wrong for prod.

## Locks (operator-confirmed 2026-06-15)

- **Persistence: singleton matview `<prefix>_data_anchor`.** One row, one TS column. Computed once per `refresh_matviews_sql` invocation. Snapshotter regenerates matviews from base tables on restore, so the anchor is always fresh relative to the post-restore row set.
- **Reject `config_kv` persistence.** The snapshotter captures `config_kv` row-by-row (`src/recon_gen/common/snapshotter.py:70` — base table). On restore the snapshotted `data_anchor` value comes back as-is even though the post-restore world may have new transactions planted by the test — anchor staleness vs current data. Tests like trainer-dogfood would render against a stale anchor.
- **Reject `StaticValues` bake-at-deploy.** Would require daily re-deploy to update the anchor — operationally awkward; QS dataset-parameter cache invalidation surface; defeats the point of "always current".
- **Computation:** `GREATEST(MAX(current_transactions.posting), MAX(current_daily_balances.business_day_end) WHERE account_scope='internal')`. `business_day_end` (not `business_day_start`) so the anchor is the inclusive close of the latest balance day. Portable across PG / DuckDB / Oracle (`GREATEST` is supported on all three).
- **`live(wall-clock)` removed from `as_of_frame()` resolution paths.** New resolution:
  1. `end_date == LOCKED_ANCHOR` → `AsOfFrame.locked()` (demo determinism).
  2. `end_date` set → explicit operator pin (test-determinism OR end-of-period freeze).
  3. `end_date` unset → data-derived branch (queries `<prefix>_data_anchor`).
  4. ~~`live (wall-clock)`~~ — removed. No more wall-clock fallback anywhere in the dashboards/data path.
- **`RECON_GEN_AS_OF_ANCHOR` env stays** — chain-wide override for test determinism. Otherwise the data-derived path could drift relative to locked-seed semantic-lock fixtures.
- **No threshold-styled banner or alarm formatting.** Data lag is normal in real systems; alarm fatigue would desensitize. Info-sheet surface is observability (deploy stamp bullets + Latest Balance Day KPI), not alarm. Operator reads the as_of source + the latest balance day as neutral facts.
- **`cfg.test.generator.end_date` keeps its current path location.** Pre-DK it was a misnomer because it was load-bearing for prod (the only knob). Post-DK its purpose is correctly test-determinism + optional operator end-of-period pin — the `test.generator` namespace name now reads true.
- **One commit per leaf** (bisect-friendly); release ships as part of v14.4.0.

## Persistence: rejected alternatives + rationale

| Option | Pro | Con | Verdict |
|---|---|---|---|
| **A. Singleton matview** `<prefix>_data_anchor` | Snapshotter regenerates matviews from base on restore → anchor always tracks current row set. One-line addition to existing matview dependency list + V_OVERLAY_MATVIEW_SUFFIXES tuple. Cost: one MAX subquery per refresh, ~negligible. | Adds one matview to schema. Per-dialect refresh path (PG CONCURRENTLY / Oracle DBMS_MVIEW.REFRESH / DuckDB CREATE TABLE AS) — but those branches already exist. | **Chosen.** Fits existing patterns; testing-safe. |
| **B. `config_kv` entry** | Reuses existing config_kv table; no new schema. | Snapshotter copies config_kv row-by-row → snapshotted anchor stales relative to fresh plants. Boundary blur — config_kv was "configuration" semantically, this would inject "derived data". | **Rejected.** Snapshotter staleness is a correctness bug, not a cosmetic concern. |
| **C. Bake at deploy** (StaticValues on DateTimeDatasetParameter) | Dataset params stay literal-static; QS caches behave well. | Anchor only updates on `recon-gen json apply --execute` → requires daily re-deploy in prod. Awkward operationally; couples ETL cadence to deploy cadence. | **Rejected.** Operational friction is the wrong place to push the problem. |

## as_of_frame() resolution paths — pre and post

**Pre-DK** (`src/recon_gen/common/config.py::TestGeneratorConfig.as_of_frame`, current 2026-06-15):

| # | Condition | Result |
|---|---|---|
| 1 | `end_date == LOCKED_ANCHOR` | `AsOfFrame.locked()` |
| 2 | `end_date is not None` | `AsOfFrame(as_of=end_date, …)` |
| 3 | `end_date is None` + `db_anchor` passed | `AsOfFrame(as_of=db_anchor, …)` — only audit CLI passes this today |
| 4 | `end_date is None` + `db_anchor=None` | `AsOfFrame.live()` — wall-clock fallback ⇐ footgun |

**Post-DK**:

| # | Condition | Result |
|---|---|---|
| 1 | `end_date == LOCKED_ANCHOR` | `AsOfFrame.locked()` (unchanged) |
| 2 | `end_date is not None` | `AsOfFrame(as_of=end_date, …)` (unchanged) |
| 3 | `end_date is None` | `AsOfFrame(as_of=<data_anchor>, …)` — data-derived from `<prefix>_data_anchor` matview, threaded via `db_anchor=`. Cold DB (empty matview) → raise loudly, do NOT fall back to wall-clock. |

Path 4 is gone. The data-derived branch is the only no-`end_date` path in prod.

## Threading: how the matview value reaches the dashboards

Each of the four apps' dataset-builder code already calls `cfg.test.generator.as_of_frame()` without a `db_anchor` arg. DK.4 changes those callsites to query the matview once at app-build time (during `json apply` or dashboards-server boot) and pass the value as `db_anchor=`. Touch points:

- `src/recon_gen/apps/l1_dashboard/datasets.py:757, 1153, 1275`
- `src/recon_gen/apps/l1_dashboard/app.py:2989, 3022`
- Plus matching callsites in `apps/executives/`, `apps/investigation/`, `apps/l2_flow_tracing/`.

Query is `SELECT data_anchor FROM <prefix>_data_anchor LIMIT 1` (singleton; row count is structurally 1). Empty result = cold DB = matviews never refreshed = "no data" state. The right response is loud-fail at deploy/build time, not silent wall-clock fallback at render time.

## Naming wart resolution

Pre-DK, `cfg.test.generator.end_date` was simultaneously:
- A test-fixture knob (controls the trailing-window end of generated test data).
- The only prod knob for pinning `as_of` (because the runtime alternative was a wall-clock fallback that's broken for prod).

Post-DK, prod's default path no longer touches `end_date` at all (data-derived from matview). `end_date` reverts to its name-true role: a test-determinism pin for fixture generation + an optional operator override for end-of-period freeze ("show me the dashboard as of 2026-05-31 for end-of-month reconciliation"). The earlier "rename `test.generator.end_date` for prod" follow-up that haunted the memory log can be dropped.

## Test impact

| Surface | Impact | Mitigation |
|---|---|---|
| `refresh_matviews_sql` callsites | New matview in dependency order | Centralized — one place to update |
| `_V_OVERLAY_MATVIEW_SUFFIXES` (snapshotter) | New tuple entry | One line; refresh post-restore handles it |
| Trainer-dogfood snapshot round-trip | Need to verify matview regenerates per-test (the test that validates the choice of matview over config_kv) | DK.7.snapshot_roundtrip — plant past-anchor row, restore, assert data_anchor caught up |
| Semantic-lock fixtures (`tests/data/_semantic_locks/<instance>.duckdb.json`) | New matview row contributes to the locked snapshot | DK.7.semantic_locks — re-run `recon-gen data semantic-lock --l2 <yaml>` per instance |
| Audit CLI `_query_max_balance_day` | Currently re-runs MAX(...) directly | DK.6 — migrate to `SELECT data_anchor FROM <prefix>_data_anchor` for single source of truth across dashboards + audit PDF |
| Info-sheet visual count | Adds Latest Balance Day KPI + 2 deploy stamp bullets | DK.5.kpi + DK.5.bullets — fits in existing layout, no row-count regression |

## Risks + open questions

- **Cold-DB deploy semantics.** First-ever `json apply` runs before any data exists. The matview's GREATEST(MAX, MAX) over empty tables returns NULL. Dataset queries with NULL `as_of` will likely surface as errors at first dashboard load. DK.4 should detect the NULL case and either (a) fall through to `AsOfFrame.locked()` for the demo/empty path, or (b) raise loudly with a "no data; load some" message. Pick during DK.4 implementation; document choice here in a follow-up.
- **Refresh ordering.** `<prefix>_data_anchor` depends on `current_transactions` + `current_daily_balances` views — those refresh first (leaves), then data_anchor as another leaf-level entry, then helpers + L1 matviews. Verify Oracle's CONCURRENTLY equivalent works on a singleton — DBMS_MVIEW.REFRESH should be fine for one row.
- **Time-zone semantics.** `MAX(posting)` returns a TIMESTAMP; `MAX(business_day_end)` is also TIMESTAMP. Dashboards consume DATE in most date-filter clauses. The matview should store TS but consumers must DATE-truncate where needed. Verify against existing `cents_to_dollars_sql`-style portable helpers in the codebase ([[project_local_tz_convention.md]] — TS columns store local-TZ for cross-dialect portability).
- **Live-resolution latency.** Each dashboard load runs `SELECT data_anchor FROM <prefix>_data_anchor` at app-build time. App-build is once per process, not once per request, so the cost is one-time per server boot. Confirm during DK.4 implementation.

## Cross-references

- Implementation tasks: PLAN.md `## Phase DK` block, leaves DK.1 through DK.9 (committed 2026-06-15).
- Related: [[project_date_model_audit]] — original audit that established `AsOfFrame` as the time-ownership primitive.
- Related: [[feedback_production_honest_invariants]] — same principle (real-world data shape, not test-shape-special-cased).
- Future memory: capture matview-vs-config_kv snapshotter trade-off as a project memory after DK.7.snapshot_roundtrip confirms the choice was right.
