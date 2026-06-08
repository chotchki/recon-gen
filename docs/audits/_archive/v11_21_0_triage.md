# v11.21.0 cold-read findings — triage + assignment

Source: `docs/audits/v11_21_0_feedback.md` (21 findings + plant-coverage signal).
Each finding gets: **source-spot** (file:line where the bug likely lives), **class**
(filter-binding / measure-binding / SQL-correctness / copy / format / layout),
**fix shape** (one-line), **BG honest-gate cell** (which existing test would catch
the regression after BG.2-BG.6 tightening).

## Blockers

### #1 — Daily Statement per-Account Drift KPI ≠ formula

- **Source-spot**: `apps/l1_dashboard/datasets.py:679 build_drift_dataset` projects
  `drift` from `<prefix>_drift` matview (cents → dollars wrap). The Daily Statement
  KPI binding for Drift uses the `daily_statement` dataset, NOT the drift matview —
  the two compute drift via different formulas on different scopes. Find the actual
  KPI's `values=[ds_daily_statement["drift"]...]` wiring in `apps/l1_dashboard/app.py`.
- **Class**: measure-binding mismatch — the KPI binds a column whose meaning
  diverges from the narrative `Drift = Closing − (Opening + signed_net_flow)`.
- **Fix shape**: either (a) make the dataset's `drift` column compute the narrative
  formula exactly (recommended — single source of truth) or (b) rewrite the KPI
  subtitle to name the actual measure being shown.
- **BG cell**: BG.2 — Daily Statement honest test (`test_l1_account_filters.py`).
  Tighten the Drift KPI assertion to `assert kpi_drift == closing − (opening + net_flow)`
  computed from the queried rows.

### #2 — Daily Statement Business-Day picker non-functional

- **Source-spot**: SQL pushdown wire looks correct on inspection
  (`apps/l1_dashboard/datasets.py` Daily Statement WHERE includes
  `strftime('%Y-%m-%d', business_day_start) = strftime('%Y-%m-%d', <<$pL1DsBalanceDate>>)`;
  `common/html/render.py:675 _render_parameter_date` emits hidden `param_<name>`;
  `common/html/assets/js/bootstrap.js:1521 wireFlatpickrSingle` writes to hidden
  input + dispatches `change`; `wireFilterAutoRefresh:1410` debounces 300ms +
  fires `htmx.trigger(div, "refresh")` on every `.visual-data[hx-get]` inside the
  form). Curl with `?param_pL1DsAccount=Customer Number One (cust-001)&param_pL1DsBalanceDate=2026-05-07`
  returns distinct values vs `?param_pL1DsBalanceDate=2026-05-04`.
- **Class**: real-browser interaction defect (likely flatpickr change-event timing
  vs HTMX-refresh debounce race, OR form-include scope issue specific to a picker
  inside a sheet-tab swap).
- **Fix shape**: real-browser repro required to localize. BG.2 below tests
  what server returns for picked params — if BG.2 passes but cold-read still fails,
  the bug is in the browser-side wiring (look at how flatpickr's `onChange`
  interacts with `wireFilterAutoRefresh`'s `change`-event listener inside the
  daily-statement-specific sheet template).
- **BG cell**: BG.2 — Daily Statement honest test. The cell is the direct catch
  for this finding-shape.

### #3 — Daily Statement NEGATIVE Opening Balance for leaf cardholder

- **Source-spot**: `apps/l1_dashboard/datasets.py build_daily_statement_summary_dataset`
  (search line ~1100). Opening is queried from the prior-day `daily_balances` row
  for the picked `account_id`. Either: (a) the seed's spine for cardholder accounts
  legitimately produces negative balances (a seed defect — should never happen
  on a prepaid stored-value account) OR (b) the SQL pulls a wrong-class row.
- **Class**: data-correctness, likely seed-side.
- **Fix shape**: query the actual matview row: `SELECT stored_balance FROM <prefix>_daily_balances WHERE account_id=<cardholder> AND balance_date=<picked>`.
  If matview holds negative, fix the spine (cardholder class shouldn't be able to
  go negative — add an invariant). If matview holds positive, fix the KPI binding.
- **BG cell**: BG.2 — identity assertion compares KPI to direct matview SELECT.
  Catches "sign convention inverted" + "wrong account class bound" silently.

### #4 — L1 Drift "Latest Snapshot Drift" hides via SUM-cancellation

- **Source-spot**: `apps/l1_dashboard/app.py` Drift-sheet headline KPI. Almost
  certainly bound to `ds_drift["drift"].sum()` (a signed SUM). The L1 invariant
  matview's `drift` column is signed (positive for over-stored, negative for
  under-stored), so per-account drifts net to ~0 across the bank.
- **Class**: measure-binding correctness. The narrative ("the single visual cue
  the underlying ledger doesn't reconcile") wants `SUM(ABS(drift))` or
  `MAX(ABS(drift))`.
- **Fix shape**: change the headline KPI binding to `ds_drift["drift"].sum_abs()`
  (add a `.sum_abs()` helper on the dim wrapper if absent — keeps the call site
  expressive vs hand-rolling a CalcField). Add a unit test on the dataset_contract
  asserting `latest_snapshot_drift_kpi` measure expr is `SUM(ABS(drift))`.
- **BG cell**: BG.3 — L1 Drift honest test. Identity assertion `kpi == SUM(ABS(drift))
  FROM <prefix>_drift WHERE business_day = (SELECT MAX business_day)`.

### #5 — Investigation Volume Anomalies KPI=0 vs populated distribution chart

- **Source-spot**: `apps/investigation/app.py:490-501`. KPI uses `ds_anomalies`
  (σ-prefiltered: only rows where |z| ≥ threshold); chart uses
  `ds_anomalies_distribution` (full population, all buckets). The KPI subtitle
  already says "**Zero at default σ** means no pair currently exceeds the bar".
- **Class**: design intent vs operator-readability tension — the KPI IS correctly
  zero by design at this seed's σ distribution. Operator confusion is real
  ("looks broken").
- **Fix shape**: either (a) lower default σ in the seed-anomalies matview query
  so the seed produces non-empty flagged rows (a seed-side fix, NOT a matview
  filter — see `feedback_production_honest_invariants`), OR (b) the seed itself
  needs more high-z planted anomalies via `boost_inv_fanout_plants` /
  `add_anomaly_plants`, OR (c) accept "zero at default" + rename the KPI to
  "Flagged at current σ" + add a top-N "Highest σ even if below threshold"
  callout to give operators something concrete to read.
- **BG cell**: BG.4 — Investigation honest test. Identity assertion forces
  `kpi == SELECT COUNT(*) FROM <prefix>_inv_pair_rolling_anomalies WHERE z_bucket NOT IN ('<2σ','2σ-3σ')`.
  Catches "the KPI is binding the wrong σ-cut" if it ever drifts vs the chart.

### #6 — L1 Drift Timelines "Leaf Account Drift" perfectly flat at $15

- **Source-spot**: `apps/l1_dashboard/datasets.py:1241 build_ledger_drift_timeline_dataset`
  + the leaf timeline visual binding in `apps/l1_dashboard/app.py` Drift-Timelines
  sheet. Constant $15 across all days = either (a) only one leaf account has a
  drift row and it's stuck at $15, OR (b) a stale-snapshot binding (the timeline's
  binding query has a stuck `WHERE business_day_start = '2026-05-01'` that ignores
  the line's day dimension).
- **Class**: dataset SQL likely-correctness defect; could also be a tree wiring
  bug (the timeline's day-dimension is a different column than what the dataset
  groups by).
- **Fix shape**: dump the actual SQL the timeline sends + the raw matview rows
  for `(business_day, account_id, drift)` filtered to `account_parent_role IS NULL`
  (the leaf filter). If matview varies but chart is flat, the binding is wrong.
- **BG cell**: BG.3 — L1 Drift honest test. Delta assertion: pick day1, capture
  series; pick day2, series must differ. Constant-line shape fails delta.

## Majors

### #7 — Investigation Recipient Fanout double-counts

- **Source-spot**: `apps/investigation/datasets.py:276 build_recipient_fanout_dataset`.
  The CTE pattern (inflows × outflows JOIN ON transfer_id) is **cartesian per
  multi-leg transfer** — one transfer with N sender legs + M recipient legs
  produces N×M joined rows, each carrying the full inflow amount. SUM(amount)
  across joined rows inflates by N (the sender-leg count).
- **Class**: SQL correctness — cartesian inflation.
- **Fix shape**: aggregate at the inflow level FIRST (one row per recipient leg
  per transfer with the correct amount), THEN join to outflows for the sender-set
  enumeration. SUM-over-joined-rows of `amount` should equal
  `SUM(amount) FROM inflows` per recipient.
- **BG cell**: BG.4 — Investigation honest test. Identity assertion against the
  inflows-only SUM:
  `kpi_fanout_total == SELECT SUM(amount_money/100.0) FROM inflows WHERE recipient_account_id IN (qualifying)`.

### #8 — Executives Total Transactions ≠ App Info matview count

- **Source-spot**: `apps/executives/datasets.py:120 build_transaction_summary_dataset`
  filters `t.status = 'Posted'` AND collapses to per-transfer (GROUP BY transfer_id,
  rail_name → COUNT(*) becomes per-transfer count, not per-leg). App Info reports
  `COUNT(*) FROM <prefix>_transactions` (per-leg, all statuses). The 21% gap is
  the failed-status legs + per-leg-vs-per-transfer collapse combined.
- **Class**: predicate-mismatch + unit-mismatch. NOT a bug; the KPI's underlying
  measure is correct ("posted transfers, deduped to one per transfer_id").
  Sheet's narrative just doesn't say so.
- **Fix shape**: extend KPI subtitle to spell out the predicate:
  `"Count of distinct Posted transfers (dedupes multi-leg). App Info row count
  reports per-leg, all-status."` OR add a small "All legs, all statuses" twin
  KPI matching App Info exactly.
- **BG cell**: BG.5 — Executives honest test. Identity:
  `kpi_total_transactions == SELECT COUNT(DISTINCT transfer_id) FROM <prefix>_transactions WHERE status='Posted'`.
  Documents the predicate in code; regression-proofs it.

### #9 — Today's Exceptions dominated by one bar

- **Source-spot**: `apps/l1_dashboard/app.py` Today's-Exceptions sheet. The
  `Exceptions by Type` bar chart binds raw `COUNT(*)` per `check_type`. The
  user confirmed this is a real ETL signal (`stuck_unbundled` dominates
  legitimately).
- **Class**: presentation. Not a correctness bug.
- **Fix shape**: pick one — (a) log-scale Y-axis on the bar chart, (b) top-N + Other
  bucket, (c) "click-to-drill into check_type" left-click drill action.
  Recommendation: (c) — it's the consistent pattern with the rest of the L1
  dashboard's drill-affordance vocabulary.
- **BG cell**: not a BG.X target (presentation, not value-correctness).
  Owns its own follow-up subtask.

### #10 — Executives legend overwhelm + outlier bars

- **Source-spot**: `apps/executives/app.py:452 add_bar_chart` "exec-txn-bar-daily-stacked".
  Stacked-by-rail_name with no top-N bucketing → ~60-80-entry legend.
- **Class**: presentation. Not a correctness bug.
- **Fix shape**: top-N rails + "Other" bucket at the dataset SQL layer (limits
  what reaches the chart engine, also reduces wire bytes). Default N=10.
- **BG cell**: not a BG.X target. Follow-up subtask.

### #11 — L2 Exceptions KPI vs detail-table units mismatch

- **Source-spot**: `apps/l2_flow_tracing/app.py` l2-exceptions sheet. KPI binds
  count of distinct check_types vs table's per-row violation magnitudes.
- **Class**: unit-mismatch / explainer gap.
- **Fix shape**: rename KPI to `"Distinct Exception Types Open"` AND rename
  detail-table `Count` column to `Violations per Type`. Or pick one unit and
  use it for both. Recommendation: rename, no SQL change.
- **BG cell**: BG.6 — L2FT honest test. Identity assertion makes the unit
  divergence loud (KPI = `COUNT(DISTINCT check_type)` vs sum of detail rows).

### #12 — L1 Drift "Internal Accounts in Overdraft" KPI=0 vs populated table

- **Source-spot**: `apps/l1_dashboard/app.py:837` —
  `values=[ds_overdraft["account_id"].count()]`. The `.count()` may default to
  COUNT-DISTINCT in QS but COUNT in App2 — which would explain App2 vs QS divergence
  but not App2 KPI=0 with table populated. If the dataset's prefiltered to "only
  internal accounts" and the table joins through to ALL accounts, the discrepancy
  is at the dataset level (KPI dataset != table dataset).
- **Class**: measure-binding / scope-mismatch.
- **Fix shape**: investigate `.count()` resolution in App2 vs QS for COUNT-vs-DISTINCT-COUNT;
  verify KPI + table bind the SAME dataset (`ds_overdraft`); if `.count()` returns
  COUNT-DISTINCT on a column with NULLs or repeats, the KPI underflows.
- **BG cell**: BG.3 — L1 Drift honest test. Identity assertion:
  `kpi == SELECT COUNT(*) FROM <prefix>_overdraft`.

### #13 — Pending Aging chart bar disagrees with KPI/table

- **Source-spot**: `apps/l1_dashboard/app.py` Pending-Aging sheet. The 0-2h
  bucket bar at ~140 vs KPI=2 + footer=2 says the chart binds a different
  population than the KPI/table. Either chart binds the pre-filter "all
  ageing rows" dataset while KPI/table bind a filtered subset, or chart's
  Y-axis is mislabeled (count vs sum).
- **Class**: dataset / scope mismatch.
- **Fix shape**: unify the chart + KPI + table on one dataset; if separate
  datasets are required (because chart wants "all" + KPI wants "open only"),
  add explicit "All ageing pendings" vs "Open ageing pendings" subtitles.
- **BG cell**: BG.6 — L2FT/Aging honest test. Three identity assertions
  (KPI vs SQL, table row-count vs KPI, chart bar height vs SQL bucket count)
  on the same picked filter state.

## Polish

### #14 — 3-decimal currency formatting inconsistent

- **Source-spot**: `common/dataset_contract.py` `currency=True` flag emits
  the format string; check each callsite for which sheets use the `.numerical(currency=True)`
  wrapper vs raw `.numerical()` and whether the format-string applies 2 or 3 decimals.
- **Fix shape**: standardize on 2-decimal at the format-string source. Audit:
  `grep -rn "currency=True" src/recon_gen/apps/` + verify each format applies
  `$#,##0.00` not `$#,##0.000`.
- **Subtask**: BF.10 follow-up — currency-format standardization sweep.

### #15 — Empty-state discipline gap

- **Source-spot**: Sheets named in the finding (Daily Statement, Money Trail,
  Account Network, Recipient Fanout). The L2FT Transfer Templates sheet
  already has the right pattern (`apps/l2_flow_tracing/app.py` — search
  `"no chains selected"` / `"no template matched"`).
- **Fix shape**: extract the L2FT empty-state banner into a `common/tree/` helper
  (`add_empty_state_banner(condition_sql, message)`) + apply to all picker-required
  sheets.
- **Subtask**: BF.11 follow-up — empty-state helper + apply sweep.

### #16 — L1 getting-started: typo + leaked seed-config + Part I/II refs

- **Source-spot**: `apps/l1_dashboard/sheets/getting_started.py` (or the
  equivalent intro-textbox content emitter). "CELIBERATE" search:
- **Fix shape**: copy edits + remove "Part I / Part II" + strip seed-config tokens
  (90-day window, 1.5 ledgers/cardholder, 4 per location).
- **Subtask**: copy edit, one-line PR per typo + remove block.

### #17 — Executives help-text leaks `(vL1.5.21*)` build-note

- **Source-spot**: `apps/executives/app.py` exec-money-moved + exec-transaction-volume
  sheet subtitles or KPI subtitles. Grep:
- **Fix shape**: delete the `(vL1.5.21*)` parenthetical from those subtitles.
- **Subtask**: copy edit.

### #18 — App Info Matview Status only shows 2 matviews

- **Source-spot**: `common/sheets/app_info.py::populate_app_info_sheet`. The
  matview-status table is a per-matview row-count; if it's hardcoded to 2 entries
  it should walk the full set declared in `<prefix>_*` schema.
- **Fix shape**: query the SQLite/Postgres/Oracle catalog for matview names
  matching `<prefix>_%`; render one row per. (Sounds like the panel was
  scope-narrowed to "foundational tables only" and the comment got lost.)
- **Subtask**: standalone fix; not BF/BG.

### #19 — Daily Statement KPI tile text-clipping

- **Source-spot**: KPI tile container styling. Almost certainly in `common/html/`
  KPI renderer — width-fixed at ~140px with no overflow handling. Numbers like
  `-24,120.18` overflow at typical font scale.
- **Fix shape**: size-to-content + abbreviation when narrow (already mentioned in
  the cold-read).
- **Subtask**: AM-followup (the Tailwind chrome migration could include
  responsive KPI sizing).

### #20 — Unbundled Aging KPI label rendering

- **Source-spot**: `apps/l1_dashboard/app.py` unbundled-aging sheet — the KPI
  title concatenation likely puts `$` glyph into the label string instead of
  applying it as a format prefix.
- **Fix shape**: rewrite the KPI as `title="Stuck Unbundled Exposure"` +
  `currency=True` on the value (which prefixes `$` correctly).
- **Subtask**: standalone fix; one-line.

### #21 — App Info Deploy Stamp shows `dialect: sqlite` in prod

- **Source-spot**: `common/sheets/app_info.py` — the deploy-stamp builder
  embeds `cfg.dialect` directly.
- **Fix shape**: hide the dialect token unless `cfg.show_dev_affordances`
  (introduce a config flag) OR always show + add a "dev build" pill when
  not production.
- **Subtask**: standalone; tied to a config-shape decision (so this isn't a
  one-liner — needs the flag).

## Plant-coverage finding — `ach` mystery rail

- **Source-spot**: confirmed hardcoded `rail_name="ach"` literals scattered
  across 6 spine modules:
  - `common/spine/chain_parent_disagreement.py:170`
  - `common/spine/anomaly.py:268`
  - `common/spine/drift.py:268`
  - `common/spine/money_trail.py:257`
  - `common/spine/account_simulation.py:252`
  - `common/spine/inv_fanout.py:69`
- **Class**: seed-emitter not dogfooding L2 — hardcoded rail names emit into
  transactions, which then fail the plant-coverage `rail_name` ⊆ L2-declared-rails
  invariant for any L2 instance that doesn't happen to declare a rail named "ach".
- **Fix shape**: thread a `rail_name` parameter through each spine module's
  emitter from `default_scenario_for(l2_instance)`, which can pick a real rail
  off the L2 instance. Default-arg approach (current) silently passes for
  L2 instances that include "ach" + silently breaks for ones that don't.
- **BG cell**: not a BG.X target (data-correctness, caught by plant-coverage
  test). But the existing plant-coverage test IS the honest gate here — it
  flagged the bug. Add an assertion that this set is empty (`unmatched_rail_names == []`)
  as part of the L1/L2FT test suite, not opt-in.

## Sweep summary

- **Blockers → BG.2/BG.3/BG.4** — identity+delta gates catch all 6 once
  retrofitted; #2 also needs a real-browser repro pass independent of the SQL
  pushdown.
- **Majors → BG.3/BG.4/BG.5/BG.6** — same identity gates catch #7/#8/#11/#12/#13;
  #9/#10 are presentation, follow-up subtasks not in BG scope.
- **Polish → standalone fixes** — none require BG; queue as one-PR each in a
  "v11.21.0 cold-read polish sweep" branch.
- **Plant-coverage → spine fix** — `rail_name="ach"` defaults purged + plumbed
  from L2 in one PR; plant-coverage test promoted from opt-in to layer-2.
