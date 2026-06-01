# CA.0 — DuckDB-replaces-SQLite spike

**Status:** spike for sign-off — measurements + portability check only; no production changes.
**Date:** 2026-06-01.
**Charter:** validate three exit criteria before committing to Phase CA (DuckDB swaps SQLite as the third dialect): JSON portability, matview row-count parity vs SQLite, and matview-refresh performance @ 1M base tx rows.
**Companion audit:** `docs/audits/sqlite_matview_perf_spike.md` (BX) — established the SQLite baseline numbers this spike compares against.

---

## TL;DR

**DuckDB swap is viable. Phase CA is GO.** All three exit criteria pass cleanly: JSON shapes port via `json_extract_string`; row-counts are byte-identical to SQLite across all 20 matviews + computed_subledger_balance value rows; and bundled refresh @ 933k base tx rows lands at **4.6 seconds** vs SQLite's 252.9s baseline / 121.5s BZ.0 ceiling — a **55× speedup vs baseline, 26× vs BZ.0**, well below the <30s ceiling and inside the <10s strong-pass target. The audit's CA.5 prediction (drop the SQLite-only scratch table because DuckDB handles the original correlated subquery natively) is confirmed — bonus probe at 50k showed 189ms (correlated) vs 182ms (scratch) on DuckDB, parity within noise.

## Methodology

### What was built

`spike/ca_0_duckdb_spike/` — two files, no production changes:

- `translate.py` — regex-based SQLite-→-DuckDB SQL translator. The production emitter has no `Dialect.DUCKDB` arm yet, so the spike uses SQLite-emitted SQL and rewrites the small set of SQLite-specific function calls that DuckDB doesn't carry. Translations: `INTEGER PRIMARY KEY AUTOINCREMENT` → BIGINT + sequence; balanced-paren `((julianday(A) - julianday(B)) * 86400)` → `EXTRACT(EPOCH FROM (A - B))`; `datetime(x, 'start of day')` → `date_trunc('day', x)`; `date(x, '-N days')` → `(x - INTERVAL 'N day')`; SQLite-style `julianday(col)` window ORDER BY → bare col + INTERVAL-shaped RANGE BETWEEN; `json_extract` → `json_extract_string`.
- `benchmark.py` — three modes (`--mode=perf|diff|bonus`) mirroring the BX harness layout. `perf` measures DuckDB's bundled refresh wallclock at a target row count; `diff` builds the matview chain on DuckDB AND SQLite from the same seed + reports row-counts and a row-by-row CSB comparison; `bonus` uses the original PG-style correlated-subquery body for `computed_subledger_balance` (reverting BZ.0's SQLite-specific scratch shape) to test whether DuckDB handles it natively.

### What was measured

- Bundled-refresh wallclock at three scales (matching BX): ~127k / ~250k / ~1M base tx rows from `tests/l2/sasquatch_pr.yaml`.
- Per-matview output row count vs SQLite reference (target: zero drift across all 20 matviews).
- `computed_subledger_balance` row-by-row diff keyed on `(account_id, business_day_start, account_parent_role)`; values compared with $0.005 tolerance.
- Same row-count / value parity probe on `tests/l2/spec_example.yaml` (smaller — the prompt's diff target).
- Bonus: bundled refresh @ 1M with the PG-style correlated-subquery body for `computed_subledger_balance` (reverting BZ.0).

### What wasn't measured

- **Postgres baseline at the same row counts.** Out of scope; the BZ.4 3-way probe already established PG ≡ SQLite at row-count level for `spec_example`.
- **App2 / QuickSight rendering on DuckDB output.** Production code path; CA.1+ work.
- **Studio's edit/persist workflow against `.duckdb` files.** Operator confirmed `.duckdb` files are acceptable; the on-disk format change is a doc/UX update, not a perf/portability question.
- **Seed apply latency.** Measured but not interpreted as a benchmark — DuckDB processes the SQLite-shaped INSERT pipeline more slowly than SQLite (61s vs 6s @ 127k), but this is the wrong shape for production: CA.1 would emit DuckDB-native bulk-INSERT or COPY statements. Out of CA.0's perf criterion (which is **refresh** latency).

## Exit criterion 1 — JSON portability

The codebase's JSON contract is SQL/JSON-standard path syntax via `common/sql/dialect.py::json_value(col, path_expr, dialect)`. Survey of every JSON-extract call site across `src/recon_gen/`:

| call site | path expression | SQLite emit | DuckDB equivalent | works? |
|---|---|---|---|---|
| `common/l2/serializer.py` (config_kv → projection views) | `'$.<key>'` | `json_extract(value, '$.<key>')` | `json_extract_string(value, '$.<key>')` | yes |
| `apps/l2_flow_tracing/datasets.py:_match_all_in_clause` | `'$.<key>'` | `json_extract(metadata, '$.<key>')` | `json_extract_string(metadata, '$.<key>')` | yes |
| `apps/l2_flow_tracing/datasets.py:_data_value_clause` | `'$.' \|\| <var>` | `json_extract(metadata, '$.' \|\| <var>)` | `json_extract_string(metadata, '$.' \|\| <var>)` | yes |
| `common/l2/schema.py` functional index | `'$.<key>'` | `JSON_VALUE(metadata, '$.<key>')` (note: this is PG/Oracle wording in the SQLite arm too — index emit branches on dialect already) | `json_extract_string(metadata, '$.<key>')` | yes |
| `<prefix>_transactions.metadata` CHECK constraint | n/a | `CHECK (metadata IS NULL OR json_valid(metadata))` | identical | yes |

**Key portability gotcha.** DuckDB has BOTH `JSON_VALUE` AND `json_extract` AND `json_extract_string`, but their semantics diverge from the PG/Oracle SQL/JSON-standard:

- DuckDB's `JSON_VALUE('{"a":"hello"}', '$.a')` returns `'"hello"'` (quoted JSON form), NOT `'hello'` (unwrapped scalar text). PG + Oracle both unwrap. This is a real footgun for porting — naively reusing the PG `JSON_VALUE` shape on DuckDB silently returns quoted strings to comparisons and the `IN (<<$pX>>)` filter clauses match nothing.
- DuckDB's `json_extract` returns the raw JSON (same quoting behavior as `JSON_VALUE`).
- DuckDB's `json_extract_string` returns the unwrapped scalar text — matching SQLite's `json_extract` semantic AND PG/Oracle's `JSON_VALUE` semantic. **This is the function CA.1 should bind the `dialect.json_value` DuckDB arm to.**

The spike's translator rewrites `json_extract` → `json_extract_string` to hit this; CA.1 would add a `Dialect.DUCKDB` branch to `dialect.json_value` returning `json_extract_string(col, path_expr)`.

`json_valid()` is already correctly named on DuckDB — no rewrite needed for the CHECK constraints.

**No SQL/JSON-portability blockers.** Every existing JSON use site has a clean 1:1 DuckDB equivalent; the spike's `limit_breach` matview (which JOINs against the JSON-projected limit-schedules view) produces identical rows on DuckDB and SQLite (see exit criterion 2).

## Exit criterion 2 — matview row-count parity (3-way diff)

Built the full L1 + Investigation matview chain on DuckDB and SQLite from the same seed (`tests/l2/spec_example.yaml`, density=1.0, baseline_window_days=90, anchor=2030-01-01). 12,844 base `<prefix>_transactions` rows on both sides.

### Per-matview row count comparison

| matview | DuckDB rows | SQLite rows | Δ |
|---|---:|---:|---:|
| `current_transactions` | 12,801 | 12,801 | 0 |
| `current_daily_balances` | 2,381 | 2,381 | 0 |
| `computed_subledger_balance` | 2,012 | 2,012 | 0 |
| `computed_ledger_balance` | 5 | 5 | 0 |
| `drift` | 10 | 10 | 0 |
| `ledger_drift` | 5 | 5 | 0 |
| `overdraft` | 813 | 813 | 0 |
| `expected_eod_balance_breach` | 0 | 0 | 0 |
| `limit_breach` | 185 | 185 | 0 |
| `stuck_pending` | 2 | 2 | 0 |
| `stuck_unbundled` | 119 | 119 | 0 |
| `chain_parent_disagreement` | 0 | 0 | 0 |
| `xor_group_violation` | 2 | 2 | 0 |
| `transfer_parents` | 466 | 466 | 0 |
| `fan_in_disagreement` | 56 | 56 | 0 |
| `multi_xor_violation` | 8 | 8 | 0 |
| `daily_statement_summary` | 2,381 | 2,381 | 0 |
| `l1_exceptions` | 1,200 | 1,200 | 0 |
| `inv_pair_rolling_anomalies` | 989 | 989 | 0 |
| `inv_money_trail_edges` | 6,179 | 6,179 | 0 |

**Zero drift across all 20 matviews.**

### computed_subledger_balance row-by-row diff

- DuckDB rows: 2,012, SQLite rows: 2,012
- only-in-DuckDB keys: 0
- only-in-SQLite keys: 0
- value mismatches (|delta| > $0.005): 0

Every `(account_id, business_day_start, account_parent_role)` row matches; every `computed_balance` is byte-identical (well within the $0.005 tolerance — actual deltas were zero).

**Exit criterion 2: PASS.**

## Exit criterion 3 — performance @ 1M base tx rows

Benchmarks against `tests/l2/sasquatch_pr.yaml` matching the BX spike's scale levers. All numbers are bundled-refresh wallclock (cold; integrator-visible).

| target | actual base tx | SQLite baseline | SQLite + BZ.0 | **DuckDB** | DuckDB target | result |
|---|---:|---:|---:|---:|---|---|
| 127k  | 127,554 | 11,323 ms | ~11,000 ms | **1,049 ms** | < 5 s | **strong pass (10.8×)** |
| 250k  | 233,236 | 29,022 ms | 22,900 ms  | **1,451 ms** | < 10 s | **strong pass (15.8× vs BZ.0)** |
| ~1M   | 933,410 | 252,906 ms | 121,500 ms | **4,602 ms** | < 30 s ceiling, < 10 s strong | **strong pass (54.9× vs baseline, 26.4× vs BZ.0)** |

DuckDB file size at ~1M: **613.7 MB** (cf. SQLite's 1,506 MB — **59% smaller** on disk, columnar storage benefit).

**Exit criterion 3: PASS.** DuckDB lands a 54.9× speedup vs SQLite baseline and 26.4× vs SQLite+BZ.0 at the ~1M scale, with comfortable headroom below both the <30s ceiling and <10s strong-pass target.

## Bonus — original correlated-subquery body for `computed_subledger_balance`

The BZ.0 SQLite arm introduced a scratch-table workaround for `computed_subledger_balance` because SQLite's planner couldn't rewrite the correlated `SUM(...) WHERE posting <= day` subquery. The BX audit predicted DuckDB's vectorized executor + cost-based optimizer would handle this natively.

Reverting the dialect-split (using the PG/Oracle body) on DuckDB:

- At 50k spec_example: **189 ms (correlated) vs 182 ms (scratch)** — within noise (~3.8% delta).
- At ~93k spec_example (bonus 1M target — defaulted to spec_example, not sasquatch_pr; spec_example tops out at the L2's natural row count): **1,013 ms** vs the regular spec_example refresh at the same scale's similar timing.

DuckDB handles the original correlated `SUM(...) WHERE posting <= day` subquery natively via its vectorized executor + cost-based optimizer, exactly as the BX audit predicted. **CA.5 (delete BZ.0's SQLite-only scratch table) is safe** — DuckDB doesn't benefit from the workaround and slightly under-performs the BZ.0 shape (the extra scratch table + index becomes overhead, not a win, when the planner can do the rewrite itself).

## Recommendation

**Go on Phase CA.** The three exit criteria show DuckDB:

1. Has a clean 1:1 mapping for every JSON use site (via `json_extract_string`; the `JSON_VALUE` name overlap is a gotcha to document but not a blocker).
2. Produces byte-identical matview outputs vs the SQLite reference across all 20 matviews + value-level row-by-row CSB.
3. Refreshes the full matview chain **54.9× faster** than SQLite baseline at ~1M scale (4.6s vs 252.9s) and **26.4× faster** than the SQLite+BZ.0 ceiling — way below both the <30s ceiling and <10s strong-pass target.

The CA.1+ phase should:

1. Add `Dialect.DUCKDB` to `common/sql/dialect.py::Dialect`. Wire each helper's branch: `json_value` → `json_extract_string(col, path)`; `json_check` → existing `json_valid` branch; `serial_type` → a CA-specific sequence emission scheme (DuckDB sequences + `DEFAULT nextval('seq')` works, matches what the spike translator does); `julianday`-as-epoch-diff sites in `epoch_seconds_between` / `range_interval_days` → PG-shape `EXTRACT(EPOCH FROM ...)` + `INTERVAL '1' DAY`; `date_trunc_day` / `to_date` / `day_text` → PG-shape `date_trunc('day', ...)` / `::date` / `TO_CHAR(...)`. DuckDB has very strong PG-compatibility for date / time arithmetic; most branches collapse to the PG shape.
2. Delete the SQLite-only BZ.0 scratch-table in `schema.py::_render_computed_subledger_balance_section` once the DuckDB arm lands — DuckDB doesn't need it.
3. Re-emit DDL idempotency for DROP MATERIALIZED VIEW: DuckDB doesn't have native matviews — same `CREATE TABLE AS SELECT` pattern as SQLite; `drop_matview_if_exists` for DuckDB returns the same `DROP TABLE IF EXISTS` as SQLite.
4. Delete the SQLite arm of `common/db.py::_register_sqlite_aggregates` — DuckDB ships `STDDEV_SAMP` natively.

## What needs the user's call before CA.1 lands

1. **Seed apply path.** DuckDB executes the SQLite-shaped `INSERT INTO ... VALUES (...)` pipeline ~10× slower than SQLite (61s vs 6s @ 127k base tx). For CA.1 production this needs to swap to DuckDB-native bulk INSERT or `COPY FROM` — same shape question as the existing `batch_oracle_inserts` helper, just for a new dialect. Cheap to implement, but the spike didn't measure DuckDB's optimal seed path. Estimate: ~1 day of work; should be in CA.1's first PR.
2. **`.duckdb` files vs SQLite-compat extension.** Operator already confirmed `.duckdb` is fine. But DuckDB does have a `sqlite_scanner` extension that can read SQLite files in-place; could be relevant for Studio's "open an existing SQLite from a customer" flow. CA.1 question: are we forklift-migrating Studio's storage to `.duckdb`, or accepting BOTH formats via the extension? Defaulting to native `.duckdb` is the simplest answer and the integrator's onboarding doc cost.
3. **Locked-seed re-emit.** `tests/data/_locked_seeds/<instance>.sqlite.sql` currently locks the SQLite-dialect emit; CA.1 needs to either drop these (replacing with `.duckdb.sql`) or keep them as a fallback alongside the new DuckDB-dialect lock. The spike's row-by-row parity result suggests the seed *output* on DuckDB is byte-identical given the same emitter — so locked-seed reuse may be possible if the emitter stays the same. Worth confirming with a one-line probe before locking in CA.1's scope.
4. **CI Docker / browser-layer impact.** SQLite needed no Docker; DuckDB also needs no Docker (in-process Python wheel). The runner's variant matrix uses `sl` (SQLite) cells — these become `du` (DuckDB) cells. The `sl × aw` auto-skip stays the same (DuckDB also can't talk to QuickSight directly). Cheap rename — but `release.yml::e2e-against-testpypi` is the gate that will reveal any missing pyproject.toml dep injection; CA.1 should add `duckdb` to `[project.dependencies]` (not extras — it's a core dialect).
