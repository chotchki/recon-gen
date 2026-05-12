# X.2.j.0 — 4-way cross-tool agreement contract (spike)

**Status:** spike complete; the contract shape is locked. X.2.j.A–E
build on this. Sub-tasks in `PLAN.md` amended per the findings below.

## Problem

`tests/e2e/test_audit_dashboard_agreement.py` (phase U.8.b) holds a
3-way agreement gate per L1 invariant: `expected (scenario plants) ==
PDF (extractor) == QS dashboard (driver)`, over 6 invariants × {pg,
oracle}, gated `QS_GEN_E2E=1` + `pytest.mark.browser`. App 2 (the
self-hosted HTMX renderer) is now a first-class output of the same
`scenario → DB → output` pipeline, but it isn't in the contract — so
"all renderers individually passed but they disagree on a violation
row" is undetected. X.2.j adds the 4th leg.

`feedback_spike_before_locking_implementation`: the contract's *shape*
— count vs row-identity, which anchors, which dialects run where — is
a codebase-shape decision for the e2e layer, so this spike settles it
before X.2.j.B writes the implementation.

## What the spike ran

Throwaway 4-source comparison on the `drift` invariant against a local
PG container (`qs-x2u-spike-pg`, `postgresql://postgres:qs@localhost:55433/qsgen`):

1. Seeded `spec_example` via `apply_db_seed(conn, instance,
   mode="l1_invariants", today=date.today(), include_baseline=False)`
   — the exact path `seeded_audit` uses.
2. **Anchor: expected/plants** — `expected_audit_counts(scenario,
   PERIOD).drift_count` = **1**.
3. **Anchor: direct matview SQL** — `SELECT … FROM spec_example_drift`
   = **2 rows** (`cust-002` −1487.50 on `business_day_start
   2026-05-06`; `cust-001` +75 on `2026-05-07`). Both
   `business_day_start` values are **midnight-aligned**.
4. **Anchor: App2 dashboard** — built the L1 dashboard tree
   (`build_l1_dashboard_app(cfg, l2_instance=…)` + `build_all_l1_dashboard_datasets`),
   spun `App2Driver.serving(tree_app=…, sheet=first,
   data_fetcher=make_live_db_fetchers_for_app(tree_app=…, cfg=…)[0],
   options_fetcher=…)`, navigated `open("l1", sheet="Drift")`, read
   `table_rows("Leaf Account Drift")` = **2 rows** (matches the direct
   SQL exactly), `.table-pager-range` text = **"1–2 of 2"**. Applying
   the audit period via `set_date_range(PERIOD)` left it at **2** —
   no change.
5. PDF anchor not re-rendered — the existing 3-way test already
   establishes `pdf_count >= expected (1)` AND `pdf_count ==
   dashboard_count` for `drift`, so PDF = 2 is the established value.

## Findings → decisions

### 1. Count vs row-identity → **row-identity for the flat-shape invariants; count + `App2 == QS == direct-SQL` for the divergent-shape ones.**

`spec_example_drift` carries a natural key — `(account_id,
business_day_start)`. `drift` / `ledger_drift` / `overdraft` /
`limit_breach` all have the same `(account_id, business_day_start)`
shape and the dashboard table + PDF section + matview are the same
row-per-row. For these, the 4-way assert compares the **set of
`(account_id, business_day_start)` key tuples** across direct-SQL ==
PDF == QS == App2 — catching "same count, different rows", which the
count assert can't see.

`stuck_pending` / `stuck_unbundled` / `supersession` are
**divergent-shape**: the PDF aggregates (parent-per-row + child-grouped
roll-ups; supersession is count-by-table+category, 3 rows for the spec
scenario) while QS *and* App2 both render raw matview rows. So for
these: keep `pdf_count >= expected` (the producer-side check), and add
`App2_rows == QS_rows == direct_matview_rows` (the two renderers read
the same matview, so they must agree row-for-row even though the PDF
doesn't). Row-identity here keys on the matview's natural key
(`transaction_id` for supersession's Transactions Audit table, the
stuck-* matviews' equivalent).

### 2. Which anchors → **add the 5th anchor: a direct `SELECT` against the L1-invariant matview. It is the ground truth.**

The spike showed it concretely: `expected.drift_count = 1` but the
matview holds **2 rows** — `cust-001 +75` is an incidental drift cell
the planted scenario produces as a side-effect, not an explicit plant.
The existing 3-way test only survives this because it asserts `pdf >=
expected` / `dashboard >= expected` (lower bound) + `dashboard == pdf`
(both happen to be 2). `expected` is a **lower bound (`⊆`)**, not the
exact set. The chain is therefore:

```
scenario_plants  ⊆  direct_matview_query  ==  PDF*  ==  QS  ==  App2
```

(`* PDF ==` only for the flat-shape invariants; for the divergent-shape
ones the PDF roll-up shape diverges by design — see #1.) The
direct-SQL anchor is cheap (one query against the just-seeded DB,
already connected in `seeded_audit`), dialect-correct (`column_name(…,
dialect)` for Oracle column refs; `CAST(:date_to AS DATE)` /
`TO_DATE` per dialect — mirror the period predicate that
`_dashboard_extract` / the audit query already apply), and it's the
actual value all three renderers *should* be showing.

### 3. App2 leg mechanics → **`App2Driver.serving(... data_fetcher=make_live_db_fetchers_for_app(...))` works against any connection string; the pager-range text carries the true row total.**

Confirmed live against the local PG. `make_live_db_fetchers_for_app`
(note the **plural** — returns `(visual_fetcher, options_fetcher)`;
the L1 dashboard's dataset-sourced dropdowns need the options half)
lazily opens its `AsyncConnectionPool` inside uvicorn's loop on the
first request, against `cfg.demo_database_url`. An Aurora cfg is just
a cfg whose `demo_database_url` points at Aurora — no code difference
from local containers. The X.2.g.2.d live verify + `test_html2_executives_live.py`
already prove the path; the spike confirms it specifically for the L1
dashboard tree.

**One real gotcha: App2's Table renderer is server-side paginated
(`_TABLE_PAGE_SIZE = 50`).** `App2Driver.table_row_count` /
`table_rows` read the rendered DOM page — fine for the spec scenario
(2 < 50) but **under-counts when a table has > 50 rows**. The driver
docstring's "App2 renders every row in DOM (no virtualization), so the
window IS the full count" is stale post-X.2.g.5.followon. The full
total *is* in the DOM: `.table-pager-range` reads `"X–Y of M"` (e.g.
`"1–2 of 2"`). **X.2.j.B sub-task:** make `App2Driver.table_row_count`
pagination-aware — parse `M` out of `.table-pager-range` (falling back
to `len(table_rows())` only when there's no pager, e.g. a 0-row
table). Benefits every App2 e2e test, not just the 4-way one. The
3-way's QS leg already does the analogous page-size-bump dance inside
`QsEmbedDriver.table_row_count`; this brings App2 to parity.

App2's `ServedDashboard(sheet=…)` takes one sheet but `make_app`
routes `/dashboards/{id}/sheets/{sheet_id}` for **every** sheet in
`tree_app.analysis.sheets` — `goto_sheet(name)` re-navigates by URL and
works across all 12 L1 sheets. So one `App2Driver.serving(...)` per
dialect cell covers all 6 invariant sheets, exactly like the QS embed
driver does — no need for 6 separate server instances.

### 4. Dialect coverage → **CI gate = PG 4-way; runner `lo` cells = {pg 4-way, oracle 4-way, sqlite 3-way (no QS)}; runner `aw` cells = {pg 4-way, oracle 4-way}.**

`e2e-against-testpypi` deploys the `rel_<tag>` instance (= `spec_example`
shape) on PG only, so its 4-way leg parametrizes to `{pg}` there —
the App2 leg, the QS leg, the PDF leg, the direct-SQL leg all on PG.
`e2e.yml::e2e-pg-browser` (push:main + nightly cron) — same, PG only.
The runner's variant matrix already parametrizes `dialect_cfg` over
`["postgres", "oracle"]` and skips per-cell on the runner's
`QS_GEN_DEMO_DATABASE_URL` / `QS_GEN_CONFIG` dialect-mismatch
machinery (already in the test). SQLite: no QS leg (`QsEmbedDriver`
can't reach a sqlite tempfile from QuickSight), so a sqlite cell runs
the 3-way `expected ⊆ direct-SQL == PDF* == App2` — App2 *is*
sqlite-portable (`make_live_db_fetchers_for_app` → `_sql_executor`
placeholder rewriting handles `:name` for sqlite). The existing test
already skips the QS leg cleanly when the dashboard isn't deployed
(`per_dialect_qs_driver` → `describe_dashboard` → `pytest.skip`); for
sqlite that skip fires by construction. **No new dialect-coverage
machinery — the 4-way is additive on top of the 3-way's existing
parametrization + skips.**

### 5. Reuses the seed? → **yes — one module-scoped App2 driver fixture rides the existing `seeded_audit` fixture; no re-seed.**

`seeded_audit` is module-scoped (seeds the DB + renders the PDF once
per dialect cell). Add a sibling module-scoped `per_dialect_app2_driver`
fixture: build the L1 tree from `_l2_yaml_for_test()` + `per_dialect_cfg`,
`with App2Driver.serving(tree_app=…, sheet=first,
data_fetcher=make_live_db_fetchers_for_app(tree_app=…, cfg=per_dialect_cfg)[0],
options_fetcher=…, …) as d: yield d`. The fetcher only **reads** — no
schema/seed touch. The `App2Driver.serving` contextmanager owns the
uvicorn thread + WebKit page; module-scoped + contextmanager fixture is
fine (same shape as `per_dialect_qs_driver`, just module- not
function-scoped — App2 embed URLs aren't single-use, so it can be
shared across the 6 invariant tests in the module). Confirmed: the
spike spun the server mid-script against the already-seeded DB and the
fetcher pulled the 2 drift rows on first request.

### 6. dateparity repro → **no divergence for `spec_example` / `sasquatch_pr` (midnight-aligned `business_day_start`); BUT `app2_date_filter`'s `<= date_to` is genuinely wrong for non-midnight TIMESTAMP columns — fix it anyway (X.2.j.dateparity stays a real fix, not a downgrade).**

The drift / overdraft / limit_breach matviews key on
`business_day_start`, a TIMESTAMP. `_sql_daily_balance_row` writes
midnight (`offset_hours=0`) **unless** the L2 instance configures
`role_business_day_offsets` — and neither `spec_example` nor
`sasquatch_pr` does. So in the CI gate (`spec_example`), App2's
`business_day_start <= CAST(:date_to AS DATE)` and QS's DAY-granularity
`TimeRangeFilter` give the same answer (spike: `App2-style = QS-style =
2`). The stuck_* / supersession sheets have no date filter at all.

But the bug is real:

- **A fuzz cell** of the 4-way test (`fuzz:N` scenarios — the runner's
  `f<seed>_<dialect>_<target>` cells) *can* synthesize an L2 with
  `role_business_day_offsets` set (M.4.4.14 — the fuzzer produces
  per-role business-day boundaries). A drift/overdraft/limit_breach
  row at the period's upper edge with a non-midnight `business_day_start`
  (e.g. `2026-05-11 17:00:00` with `:date_to = '2026-05-11'`) →
  App2's `<=` **excludes** it, QS's DAY filter **includes** it →
  the 4-way test fails. That's a correct failure surfacing a real
  bug — good.
- **`posting` (a genuinely non-midnight TIMESTAMP)** drives the
  Transactions sheet's date filter and the Executives / Investigation
  `app2_date_filter` usages. Those are silently dropping rows at the
  period's last day. Never explicitly verified.

**Fix (X.2.j.dateparity, done as part of X.2.j.B):** widen
`app2_date_filter`'s upper bound from `column <= CAST(:date_to AS DATE)`
to `column < CAST(:date_to AS DATE) + INTERVAL '1 day'` (PG) /
`< TO_DATE(:date_to, 'YYYY-MM-DD') + 1` (Oracle) / lexical equivalent
(SQLite — date+1 string). Cleanest: keeps the column untruncated so an
index stays usable; matches QS DAY-granularity inclusivity. Applies
retroactively to every `app2_date_filter` call site (L1 Drift /
Overdraft / Limit Breach / Transactions, Executives, Investigation).
Spike confirmed `column < date_to + 1 day` gives the same `2` for the
midnight case — no regression. Add a regression-guard assertion in the
4-way test (a planted non-midnight upper-edge row, asserted visible in
App2) so a future `<=` re-introduction fails loudly. **Not deferred —
flagged here per `feedback_no_silent_defer`; it's in scope for
X.2.j.B.**

## Locked contract (the shape X.2.j.B implements)

For each L1 invariant × dialect cell, against the same `seeded_audit`
DB:

| invariant kind | invariants | the assert |
|---|---|---|
| flat-shape | drift, ledger_drift*, overdraft, limit_breach | `set of (account_id, business_day_start) keys`: `scenario_plants ⊆ direct_SQL`; `direct_SQL == PDF == QS == App2` |
| divergent-shape | stuck_pending, stuck_unbundled, supersession | `PDF_count >= expected`; `direct_SQL_rows == QS_rows == App2_rows` (natural key); plus the existing 3-way's `pdf >= expected` / `dashboard >= expected` lower bounds stay |

(*`ledger_drift` is on the matview but not on the dashboard's invariant
sheets / audit PDF sections — keep it out of the 4-way unless a future
sheet surfaces it; not in scope for X.2.j.*)

Existing 3-way assertions stay; the App2 leg + the direct-SQL anchor +
the row-identity tightening are additive. CI gate = PG. Runner = {pg,
oracle, sqlite-3way}. One module-scoped `per_dialect_app2_driver`
fixture; `App2Driver.table_row_count` made pagination-aware first.

## Notes for the implementer

- `make_live_db_fetchers_for_app` is the **plural** one
  (`tests/e2e/_harness_html2.py`) — returns `(visual_fetcher,
  options_fetcher)`. The L1 dashboard has dataset-sourced dropdowns, so
  pass both into `App2Driver.serving(data_fetcher=…, options_fetcher=…)`.
  The singular `make_live_db_fetcher_for_app` is the visual-only
  convenience and would 500 on the dropdown option fetch.
- The `_dashboard_extract._DASHBOARD_LAYOUT` map (sheet name → table
  visual title → has_date_filter) is renderer-agnostic — both
  `count_l1_invariant_rows(qs_driver, …)` and the new
  `count_l1_invariant_rows(app2_driver, …)` use it unchanged (both
  speak `DashboardDriver`). The new App2 extraction is "feed an
  `App2Driver` to the existing function" — no parallel layout map.
- The local PG container `qs-x2u-spike-pg` was left up with the
  `spec_example` L1-invariant scenario seeded (TODAY-anchored) — handy
  for X.2.j.B iteration; `docker rm -f qs-x2u-spike-pg` when done. Not
  load-bearing.
