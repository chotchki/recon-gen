# BX — SQLite matview emulation performance spike

**Status:** spike for sign-off — no implementation; numbers + candidates only.
**Date:** 2026-06-01.
**Prompted by:** integrator-persona feedback that SQLite matview refresh is "very painful" on real-L2-sized data. SQLite has no native matviews — the production refresh path drops + re-creates every matview-as-table from scratch on every call.

---

## Why this spike

Studio's offline-iteration story rests on SQLite as the frictionless local DB: Postgres needs a Docker container, Oracle needs a beefier one, but SQLite is "open the wheel, edit, refresh, see the dashboard update." That loop only works if **refresh is fast enough that the integrator doesn't context-switch**.

App2 (the self-hosted HTMX renderer) is Studio's editor + ETL offline-iteration path; it must demonstrably match QuickSight because cold-read validations of new L2 shapes happen against App2 on SQLite. If a real-customer-sized L2 produces a 30s+ refresh latency, the demo / spike loop breaks: the integrator either waits, or skips refreshes, or moves to a heavier DB. Each of those is a credibility hit relative to "just use the local SQLite, it's instant."

Real customer L2s carry hundreds of thousands to millions of `<prefix>_transactions` rows. The numbers below quantify how badly the current path scales — and where in the matview graph the cost lives.

## Methodology

### What was built

A single benchmark script: `spike/bx_sqlite_matview_perf/benchmark.py`. The harness:

1. Loads `tests/l2/sasquatch_pr.yaml` (the richer of the bundled L2s — gives the realistic matview shape; spec_example would understate cost on the helpers).
2. Emits the production schema DDL (`emit_schema`) + populates `<prefix>_config_kv` (`build_config_populate_sql`) against a fresh `/tmp/bx-spike-*/demo.sqlite`.
3. Emits + applies the production seed pipeline (`build_default_scenario` × density + plants → `emit_full_seed`), with `baseline_window_days` overridden to scale the row count (densify multiplier hits plant-rows only — see "scale lever" below).
4. Runs `refresh_matviews_sql(..., dialect=Dialect.SQLITE)` once cold, full bundle, captures the wallclock — this is the integrator-visible refresh latency.
5. Re-runs each matview's group in isolation (its own DROP + CREATE AS + indexes + ANALYZE), with the rest of the matview graph already built, to identify which matview's CREATE AS SELECT dominates.

### What was measured

- Bundled-refresh wallclock (cold; what the integrator sees).
- Per-matview selective-rebuild wallclock (warm; hot-spot identification).
- Per-matview output row counts.
- SQLite DB file size.

### What wasn't measured

- **Postgres baseline at the same row counts.** Tangential to this spike's question (we know native matviews are faster; the question is the SQLite-only delta).
- **Cold-OS-page-cache behavior.** Reboot-between-runs measurement would change absolute numbers but not the ratios. SQLite is mmap'd; first cold run after process start has page-fault cost.
- **Concurrent reads.** SQLite is a single-writer DB and the refresh holds the write lock; we measure only the refresh in isolation.
- **WAL / journaling tradeoffs.** We left SQLite defaults (rollback journal). WAL mode would change the commit cost but not the matview compute cost, and the integrator uses default mode.

### Scale lever

Density (the `--seed-density` knob on `data apply`) only scales plant-row counts; the 90-day baseline (~125k rows for sasquatch_pr) dominates the total and is fixed. So the right lever for "1M-row real-customer L2" is `baseline_window_days` — extending the rolling window proportionally. At ~1,400 rows/day for sasquatch_pr's shape, the harness picks `days = ceil(target / 1400)`.

This is a slight approximation of "what a real customer's data looks like" — a customer has more accounts AND a longer history, not just longer history. But the shape of the work the matviews do (correlated subqueries, window functions, recursive CTEs) is the same; what varies is the input cardinality. The harness measures cardinality-sensitivity; the L2's `accounts` / `rails` / `templates` count drives a separate constant factor that this spike doesn't probe.

## Results

All three scales ran clean on the local Mac. No timeouts, no errors. Anchor pinned at `date(2030, 1, 1)` for determinism. Numbers per scale below.

### Headline: cold bundled refresh

| target_rows | actual base_tx_rows | baseline_window_days | seed apply (ms) | **bundled refresh (ms)** | DB file size |
|---:|---:|---:|---:|---:|---:|
| 50,000  | 127,554   |  90 |  6,355 |  **11,323** |   195 MB |
| 250,000 | 248,913   | 177 | 12,669 |  **29,022** |   380 MB |
| 1,000,000 | 986,433 | 714 | 50,956 | **252,906** | 1,506 MB |

The 50k target floored to 127k because sasquatch_pr's 90-day baseline doesn't go lower — that's the realistic minimum for this L2. The integrator gets 11s for "small," 29s for "median customer," and **4.2 minutes for "real customer with a year+ of history."**

Refresh latency growth from 127k → 1M is **~22×** for **~7.7×** input — clearly super-linear. That super-linearity is concentrated in **one matview**, see below.

### Per-matview hot-spot map (selective rebuild, warm)

Times in ms; rows are output rows post-rebuild. Bold = >1s at the 1M scale.

| matview | 127k | 249k | 986k | 1M / 127k ratio |
|---|---:|---:|---:|---:|
| `current_transactions`        | 488 | 1,001 | **4,165** |  8.5× |
| `current_daily_balances`      |   7 |    12 |     46 |  6.6× |
| **`computed_subledger_balance`** | **1,704** | **7,043** | **132,712** | **78×** |
| `computed_ledger_balance`     |   6 |    13 |    126 | 21× |
| `drift`                       |   3 |     4 |     11 |  3.7× |
| `ledger_drift`                |   2 |     2 |      5 |  2.5× |
| `overdraft`                   |   4 |     5 |     16 |  4.0× |
| `expected_eod_balance_breach` |   1 |     2 |      3 |  3.0× |
| **`limit_breach`**            | 232 |   461 | **2,045** |  8.8× |
| `stuck_pending`               |   3 |     3 |      3 |  1.0× |
| `stuck_unbundled`             |  45 |    86 |    345 |  7.7× |
| `chain_parent_disagreement`   |   3 |     3 |      4 |  1.3× |
| `xor_group_violation`         |  24 |    44 |    176 |  7.3× |
| `transfer_parents`            |   9 |    16 |     75 |  8.3× |
| `fan_in_disagreement`         |   3 |     4 |      7 |  2.3× |
| `multi_xor_violation`         |  59 |   119 |    478 |  8.1× |
| **`daily_statement_summary`** | 164 |   380 | **2,641** | 16× |
| `l1_exceptions`               |   4 |     6 |     17 |  4.3× |
| **`inv_pair_rolling_anomalies`** | 130 |   267 | **1,180** |  9.1× |
| **`inv_money_trail_edges`**   | 224 |   464 | **1,949** |  8.7× |

The selective-rebuild totals (3.1s / 9.9s / 146s) don't sum to the bundled cold cost (11s / 29s / 253s) because the bundled run pays for cold OS page cache + serial index rebuilds across all matviews; per-matview reruns benefit from a warm cache and already-built dependent indexes. Both numbers are honest — bundled is "what the integrator sees on first refresh after seed," selective is "which CREATE AS SELECT body is expensive."

## Hot spots

### #1 — `computed_subledger_balance` is the smoking gun (~52% of bundled-refresh wallclock at 1M)

Body (paraphrased):

```sql
CREATE TABLE <p>_computed_subledger_balance AS
SELECT sb.account_id, sb.business_day_start, sb.business_day_end,
       sb.account_parent_role,
       COALESCE((SELECT SUM(tx.amount_money)
                 FROM <p>_current_transactions tx
                 WHERE tx.account_id = sb.account_id
                   AND tx.status = 'Posted'
                   AND tx.posting <= sb.business_day_end), 0)
           AS computed_balance
FROM <p>_current_daily_balances sb
WHERE sb.account_scope = 'internal'
  AND sb.account_parent_role IS NOT NULL;
```

This is the classic running-sum-via-correlated-subquery shape. The outer scan walks N_days × N_accounts rows; for each, a covered index lookup on `(account_id, posting)` plus a range sum over `amount_money` for everything posted up to that day. The cardinality is roughly:

- N_days × N_accounts outer rows
- avg ~N_tx / N_accounts inner-sum candidates per outer row, of which roughly half are filtered by the `posting <= sb.business_day_end` cutoff (each cut-point is one day, so a 7-month window means each row sums ~3.5 months of that account's history on average)

The growth pattern matches what we'd expect for **O(N_days × N_accounts × avg_tx_per_account_lifetime)** — basically N_days² × constant. Going from 90 → 714 days (8×) → cost 78×. That's nearly `days^2` with a small extra factor from the larger amount_money payload moving through SUM.

The PG matview equivalent doesn't have this footgun: PG's planner can detect the correlated subquery pattern and rewrite it as a hash-grouped join + window prefix-sum. SQLite's planner does not. Verified by reading the schema — same body emits on both dialects, with the same indexes.

**A native running-sum reshape would collapse this to one O(N_tx × log N_tx) pass:**

```sql
CREATE TABLE ... AS
WITH per_tx_running AS (
  SELECT account_id, posting,
         SUM(amount_money) OVER (
           PARTITION BY account_id
           ORDER BY posting
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
         ) AS rs
  FROM <p>_current_transactions
  WHERE status = 'Posted'
)
SELECT sb.account_id, sb.business_day_start, sb.business_day_end,
       sb.account_parent_role,
       COALESCE((SELECT rs FROM per_tx_running r
                 WHERE r.account_id = sb.account_id
                   AND r.posting <= sb.business_day_end
                 ORDER BY r.posting DESC
                 LIMIT 1), 0) AS computed_balance
FROM <p>_current_daily_balances sb
WHERE ...
```

SQLite 3.38+ supports window functions; this would change the cost shape from quadratic-ish to linear-ish (single ordered scan + per-day binary search). Worth a follow-up spike. The same reshape would also help Postgres at scale.

### #2 — `daily_statement_summary` (~13% of bundled at 1M)

Body uses `LAG(...) OVER (PARTITION BY account_id ORDER BY business_day_start)` + `LEFT JOIN` against a GROUP BY of `current_transactions`. This is O(N log N) territory — better-shaped than #1 but still super-linear from cache pressure at large window sizes.

### #3 — `current_transactions` rebuild (~3% at 1M)

```sql
CREATE TABLE <p>_current_transactions AS
SELECT * FROM <p>_transactions tx
WHERE tx.entry = (SELECT MAX(entry) FROM <p>_transactions WHERE id = tx.id);
```

Another correlated subquery. At 1M base rows this is 4.2s alone. SQLite's planner is doing a row-by-row index lookup against `id`. A rewrite to `WHERE (id, entry) IN (SELECT id, MAX(entry) ... GROUP BY id)` or equivalent window-function shape would help.

### #4 — `limit_breach` (~1% at 1M)

Less critical at this scale, but the 8.8× growth ratio for 7.7× input suggests there's a similar correlated-subquery shape worth checking if scale climbs.

### #5 — `inv_pair_rolling_anomalies` + `inv_money_trail_edges` (~2.5% combined at 1M)

Investigation matviews — the recursive CTE in `inv_money_trail_edges` and the rolling-window STDDEV in `inv_pair_rolling_anomalies`. Both scale roughly linearly with cardinality (~9× cost for 8× input). Not currently a bottleneck.

**Net:** **one matview accounts for >50% of the cost; the top three account for ~70%.** Any optimization story for SQLite that doesn't target `computed_subledger_balance` first is leaving most of the gain on the table.

## Candidate approaches

### Candidate A — Fingerprint-skip (cache invalidation)

**Idea.** Hash the input row-counts + max(entry) of `<prefix>_transactions` and `<prefix>_daily_balances`. Store the hash in a sidecar table. On refresh, compare to the prior hash; if unchanged, skip the entire refresh.

**Effort.** Small. ~1 day. New `<prefix>_refresh_meta` table + a hash query + a short-circuit branch in `refresh_matviews_sql`.

**Expected speedup at 1M.** Best case: refresh becomes ~10ms (just the hash check) when data hasn't changed. Average case (Studio iteration loop where the operator IS changing data): zero speedup — the hash differs, full refresh runs anyway.

**Risks.**
- Studio's data-shaping panel mutates data on every nudge. Cache hit rate in the dominant offline-iteration workflow is roughly zero.
- Hash false-negatives (e.g. operator drops + reinserts the same rows producing the same max(entry)) → stale matviews. Need to also hash content or rely on monotone `entry`.
- Doesn't help the first-time refresh — exactly the moment the integrator is most likely to walk away frustrated.

**What invalidates this.** It doesn't address the actual compute cost — it just dodges it when nothing changed. If the integrator's iteration loop involves any data changes (which it does, by design), this buys nothing.

**Verdict: complementary at best, not a primary fix.** Cheap to add as a backstop later.

### Candidate B — Selective refresh (per-matview lazy invalidation)

**Idea.** Track which matviews depend on which base-table mutations. On refresh, only rebuild matviews whose upstream changed. Detect changes via per-table `max(entry)` watermarks.

**Effort.** Medium-large. ~3-5 days. Need a dependency graph (we have one implicitly from the refresh order, but it's not declared); per-matview watermark tracking; per-matview "do I need to rebuild?" check that's faster than the rebuild itself.

**Expected speedup at 1M.** Best case: same as full refresh if all matviews are stale. Realistic case (one account's daily_balances changed): bypass everything except the L1 chain → savings only on Inv matviews + `daily_statement_summary` → maybe 20% of bundled cost. Still doesn't help `computed_subledger_balance` if any transaction changed.

**Risks.**
- The dominant cost is `computed_subledger_balance` and it depends on `<prefix>_current_transactions`, which depends on `<prefix>_transactions`. **Any** data change triggers the expensive path.
- Adds operational complexity: dependency declarations that can drift from the SQL shape. A future matview body that reads from `<prefix>_transactions` instead of `<prefix>_current_transactions` would silently bypass the cache.
- Same false-negative risk as A — operator drops/reinserts at the same entry watermark → stale matview.

**What invalidates this.** The hot-spot map. `computed_subledger_balance` is in the unavoidable dependency chain from base-table mutations. Selective refresh helps the cheap matviews and not the expensive one.

**Verdict: not a primary fix.** Same trap as A — sidesteps the work instead of doing it cheaper.

### Candidate C — DuckDB swap (REPLACES SQLite, doesn't add a 4th dialect)

**Idea.** Replace SQLite entirely with DuckDB. DuckDB is in-process (same offline-iteration story as SQLite), has a vectorized executor + cost-based optimizer (handles correlated subqueries gracefully), and reads/writes SQLite files natively via the `sqlite` extension or through its own format. Net result: still 3 dialects (DuckDB, PG, Oracle), no SQLite-specific workarounds like the BZ.0 scratch table.

**Effort.** Medium-large. ~1-2 weeks. Drop the `Dialect.SQLITE` enum value + rename to `Dialect.DUCKDB` (or add the new one and remove SQLite once the swap holds); rewrite the dialect-specific arms in 20+ schema functions that branch on `is SQLITE`; replace the SQLite-specific helpers (the BZ.0 scratch-table, the `_setup_local_sqlite` runner fixture, the `tests/data/_locked_seeds/<instance>.sqlite.sql` re-lock); re-test the four bundled QS apps' dataset SQL against DuckDB. JSON path syntax is one of the bigger surprises: DuckDB uses `->` and `->>`, not SQL/JSON `JSON_VALUE` — the codebase's "portable SQL/JSON path" decision was specifically about SQLite + Oracle + PG, so the DuckDB swap requires re-emitting JSON access through new helpers.

**Expected speedup at 1M.** Likely **5-20× on the bundled refresh.** DuckDB's optimizer will detect the correlated-subquery pattern in `computed_subledger_balance` and rewrite it as a join/window-aggregate; the parallel scan + vectorized SUM should also help. Hard to predict exactly without measuring, but a vectorized executor on a 1M-row pass should comfortably fit in a few seconds rather than minutes.

**Risks.**
- **DuckDB on-disk format vs. SQLite.** DuckDB's native `.duckdb` files aren't SQLite — Studio's "open the wheel, point at a SQLite file" loop changes. The `sqlite` extension can read/write SQLite, but write performance through that extension is reportedly slower than native. Need a spike on whether Studio's "edit and persist" model works against DuckDB's SQLite-extension path, OR commit to the native `.duckdb` format and let integrators export-to-SQLite from there.
- **Sneak risk: SQL/JSON compat.** The codebase already burned a phase (BC.12) on Oracle 19c+ rejecting matviews over JSON_TABLE-of-CLOB. DuckDB has its own JSON dialect quirks. Swapping without a hands-on JSON-path-portability test is a trap.
- **DuckDB-as-library is C++, not Rust** — close enough to the `[feedback_rust_influenced_tool_preferences]` posture (vectorized, single Python wheel, cargo-equivalent build hygiene) but not strictly Rust.

**What invalidates this.** If Studio's on-disk story can't be made to work cleanly with DuckDB-via-SQLite-extension (acceptable write latency, no format conversion step), the swap fails the offline-iteration test. Need a 1-day spike on `DuckDB.read_sqlite + write_sqlite` against a 1M-row matview workload.

**Verdict: highest ceiling, highest cost.** Right answer if SQLite-native fixes (D) can't reach the credibility bar; wrong answer if D suffices.

### Candidate D — Use Postgres above N rows

**Idea.** Document that SQLite is for "small/iteration" data sizes (sub-100k rows or so); above that, point integrators at the local-pg container.

**Effort.** Trivial — a doc change.

**Expected speedup at 1M.** N/A — it's a workflow change, not a perf change.

**Risks.**
- **Direct credibility hit** on the "SQLite is your offline path" story. The whole point of the SQLite arm is that integrators don't have to spin up Docker for local iteration. "Sorry, your data is too big" is a downgrade.
- The integrator's L2 size is fixed by their business, not a knob they can dial down. "Use Postgres" is the same as "use the persona we wanted you to escape."
- Aligns badly with `[project_app2_parity_for_offline_iteration]` — App2's whole job is to be the offline-iteration path. Telling integrators "switch to Postgres at N rows" means App2 only works on toy data.

**What invalidates this.** It's already invalidated by the spike's premise — the user flagged this as a credibility problem to *fix*, not a workflow constraint to *document*.

**Verdict: not a fix.** Document as a fallback ("if you're stuck and need this fast right now"), but it's not the answer.

### Candidate E (added; missing from the original four) — Native SQL reshape

**Idea.** Rewrite `computed_subledger_balance` (and the smaller correlated-subquery offenders #2, #3) to use SQLite-supported window functions + grouped joins instead of correlated subqueries. Same SQL ships to all three dialects (PG, Oracle, SQLite) — no dialect proliferation. The reshape almost certainly also speeds up the Postgres + Oracle paths.

**Effort.** Medium. ~3-7 days per offending matview. Need to:
- Rewrite the SQL body to a window-function shape.
- Verify the rewrite is byte-for-byte equivalent on output (test against current locked-seed expected outputs).
- Re-time on all three dialects to confirm the rewrite isn't a PG regression.

**Expected speedup at 1M.**
- `computed_subledger_balance`: estimated **20-50× speedup** (133s → 3-7s). A window-function ordered scan + per-day prefix-sum lookup is ~O(N log N) instead of ~O(N²).
- `current_transactions`: estimated **3-10× speedup** (4.2s → 0.5-1s) by rewriting `WHERE entry = MAX(entry)` to `ROW_NUMBER() OVER (...) = 1` or a grouped JOIN.
- `daily_statement_summary`: already uses LAG; less room here, but cache-pressure improvements from the smaller computed_subledger_balance refresh free up resources.

**Aggregate expected speedup on the bundled refresh at 1M:** ~3-5× (253s → 50-80s). Still slow at 1M but firmly in "wait a minute, not five minutes" territory.

**Risks.**
- Rewriting matview bodies risks output divergence vs. the existing tests (the locked-seed test + every `tests/json/_locked/*.json` that derives from these matviews). Need careful diff-checking against the current outputs.
- SQLite's window-function support is recent (3.38+ baseline is fine, but edge cases in `ROWS BETWEEN UNBOUNDED PRECEDING ...` semantics could differ from PG).
- Touches production code path on all three dialects. Slow, deliberate rollout — one matview at a time, locked-seed re-validation after each.

**What invalidates this.** If the rewrites can't reach byte-equivalent output on all three dialects, the rollout cost balloons (per-dialect divergence). One-day spike on `computed_subledger_balance` would validate the approach before scaling out.

**Verdict: the right primary fix.** Highest expected value per unit of effort, and it improves PG + Oracle too. Doesn't introduce new dialects or new operational complexity.

## Recommendation

**Lead with Candidate E — rewrite the correlated subqueries.** Specifically:

1. **Spike: rewrite `computed_subledger_balance` to a window-function shape** (1-2 days). Re-run this benchmark against the spike. Target: ≤5s for the 1M-row case on SQLite (vs. 133s today). Lock the rewrite against current expected outputs.
2. **If (1) works, apply the same pattern to `current_transactions` and `daily_statement_summary`** (3-5 more days). Re-measure. Target for bundled refresh at 1M: under 60s.
3. **If after (1)+(2) we're still above ~60s on the integrator's typical scale**, then revisit Candidate C (DuckDB swap) with the now-much-narrower question "do we need vectorized execution on top of clean SQL, or is clean SQL on SQLite enough?"

Reasoning:

- Candidate E is the **only** option that fixes the actual problem (compute cost on the dominant matview) without adding architectural surface (new dialect / new operational tracking). The user's `[feedback_invariants_in_types]` posture — fix the wrong shape at the wiring site, not the symptom downstream — applies cleanly: the correlated subquery IS the wrong shape, and we should fix it.
- Candidates A (fingerprint-skip) and B (selective refresh) sidestep the work but don't make the work cheaper. They might be useful complementary backstops later, but they leave the integrator's first-refresh pain unresolved — exactly the moment when credibility is most at stake.
- Candidate C (DuckDB swap — REPLACES SQLite) is appealing on principle (vectorized exec, in-process, columnar storage, native matviews). It has the highest absolute ceiling AND doesn't add dialect surface (still 3 dialects, just trades SQLite for DuckDB). The right time to consider it is **after** the SQL is clean — if cleanly-written SQL on SQLite still isn't fast enough, then DuckDB's vectorized executor is genuinely the missing piece, not a workaround for unoptimized SQL. JSON-path-portability remains the sneak risk (BC.12 burnt phase).
- Candidate D (use Postgres above N rows) is a documentation-only retreat from the credibility commitment the SQLite arm makes. It can be a fallback footnote, but not the primary answer.

## What needs the user's call before BX promotes a concrete fix

1. **Is 60-80s bundled refresh at 1M acceptable?** That's our realistic target for the SQL-reshape (E) path. If "good enough" is sub-10s at 1M, DuckDB becomes mandatory and the calculus changes.
2. **Locked-seed contract during rewrite phase.** Candidate E will rewrite matview bodies in ways that may not be byte-for-byte equivalent (the SUM-ordering and floating-point intermediate state could diverge in edge cases). Are we OK with re-locking the seeds during the rewrite phase, or do we need a side-by-side validation gate against the current bodies first?
3. **Should the spike survey beyond `computed_subledger_balance`?** The 80/20 rule says one matview = 50%+ of cost. But there are 3-5 more correlated-subquery shapes in the schema that will eventually need the same treatment if the integrator scales further. Pre-commit to fixing all of them, or do them lazily as new hot spots emerge?
4. **Density vs. window as the canonical "real-customer" scale lever.** This spike treated `baseline_window_days` as the scale axis. Real customers also have more accounts/rails/templates than sasquatch_pr — a separate axis we didn't probe. Is a follow-up spike on "wider L2 shape" warranted, or is "long history" a good enough stand-in?

The first answer (1) is the one that determines whether E is the destination or just a way-station before C.
