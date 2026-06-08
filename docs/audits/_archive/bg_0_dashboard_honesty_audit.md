# BG.0 — Dashboard browser test honesty audit

**Scope**: every existing `tests/e2e/test_(l1|l2ft|inv|exec)_*.py` file.
For each: classify the strongest assertion, judge whether it would catch
the v11.21.0 cold-read defect shapes, and call out the specific
tightening BG.2-BG.6 should land.

## File-by-file inventory

| File | LOC | Category | Strongest assertion | Honest? |
|---|---:|---|---|---|
| `test_l1_dashboard_renders.py` | 44 | smoke | sheet tab appears, no JS error | n/a (smoke) |
| `test_l1_dashboard_structure.py` | 181 | structure | apps → dashboards graph matches expected shape | n/a (structure) |
| `test_l1_deployed_resources.py` | 70 | deploy | AWS resources exist + tagged | n/a (deploy) |
| `test_l1_sheet_visuals.py` | 41 | render | visual titles present | n/a (render) |
| `test_l1_filters.py` | 84 | filter | `after < before` after future-date pick (1 test); `len(options) >= 1` (1 test) | **weak — count-delta only; no value identity** |
| `test_l1_account_filters.py` | 225 | filter | `len(rows) > 0` after Role+Account+Date pick; `" (" in option` substring; `picked_account in options` | **weak — row-presence only; no row-content identity, no date delta** |
| `test_l1_additive_pickers.py` | 544 | filter | anchor-row-survives all-pickers + `find_row(visual, {col: matching}) is None` after dropdown toggle to non-matching + restore round-trips count | **partial — row identity on dropdown column ONLY; date pickers anchored not delta-checked; v2 defers slider/date inversion; no KPI bindings touched** |
| `test_l1_cross_sheet_drill_date_widening.py` | 91 | drill | `post_drill_rows > 0` (xfail QS) | **weak — drill landing presence only** |
| `test_l2ft_rails_dropdowns.py` | 37 | filter | every advertised value keeps table non-empty when picked alone (`walk_dropdown`) | **weak — non-empty only; no row identity vs SQL** |
| `test_l2ft_chains_dropdowns.py` | 52 | filter | same shape | **weak — same as above** |
| `test_l2ft_templates_dropdowns.py` | 53 | filter | same shape | **weak — same as above** |
| `test_l2ft_metadata_cascade.py` | 72 | filter | currently `@pytest.mark.skip` (X.1.b replaced dropdown with text-field — test obsolete) | **dead — rewrite or delete in BG.6** |
| `test_l2ft_additive_pickers.py` | 319 | filter | same as L1 additive_pickers; `_anchor_or_skip` skips on empty matview | **partial — same gap as L1** |
| `test_inv_dashboard_renders.py` | 42 | smoke | sheet tab appears | n/a (smoke) |
| `test_inv_dashboard_structure.py` | 219 | structure | analysis graph | n/a (structure) |
| `test_inv_deployed_resources.py` | 60 | deploy | AWS resources | n/a (deploy) |
| `test_inv_sheet_visuals.py` | 35 | render | visual titles present | n/a (render) |
| `test_inv_filters.py` | 97 | filter | `kpi != prior_kpi` after σ=4 slider; `after < before` after min-hop slider; both `pytest.skip` if start-state is 0/empty | **weak — count delta only, plus skips the very seed shape that masks finding #5** |
| `test_inv_drilldown.py` | 82 | drill | `@pytest.mark.skip` (anchor non-determinism + verb mismatch) | **dead — re-enable as part of BG.4** |
| `test_inv_dashboard_agreement.py` | 664 | agreement | spine + matview + App2 + QS counts/keys agree per invariant | **HONEST — this is the bar BG.X should reach** |
| `test_exec_dashboard_renders.py` | 45 | smoke | sheet tab appears | n/a (smoke) |
| `test_exec_dashboard_structure.py` | 170 | structure | dashboard graph | n/a (structure) |
| `test_exec_deployed_resources.py` | 66 | deploy | AWS resources | n/a (deploy) |
| `test_exec_sheet_visuals.py` | 27 | render | titles | n/a (render) |

**Tally** — 24 files total, ~3,320 LOC:

- **8 smoke / structure / render / deploy** — out of BG scope.
- **1 already honest** (`test_inv_dashboard_agreement.py`, 664 LOC) — the bar.
- **2 partial** (`test_l1_additive_pickers.py`, `test_l2ft_additive_pickers.py`,
  863 LOC combined) — anchor-row survival + dropdown inverse-exclusion ARE
  identity-shaped (`find_row` ≠ None / round-trip count), but date/slider
  inversion deferred + no KPI bindings + no row-content-vs-SQL comparison.
- **9 weak filter/drill** — only `len(rows) > 0` / `count != prior` /
  `len(options) >= 1`. Total LOC: ~720.
- **2 dead** (`test_l2ft_metadata_cascade.py`, `test_inv_drilldown.py`) —
  currently `@pytest.mark.skip`; BG cell rewrites or removes.

Honest-gate coverage today: **1 / 12 in-scope files** (the
Investigation 3-way agreement gate). Everything else is at most "didn't
blow up" / "anchor survived narrowing" / "count moved in the right
direction." All 13 cold-read findings of bug-class type (filter-binding,
measure-binding, SQL-correctness) sit in the gap.

## Cold-read finding → existing test → tightening

| Finding | Already-existing test that SHOULD catch | What it asserts today | What BG.X adds |
|---|---|---|---|
| #1 Daily Statement Drift KPI ≠ formula | `test_l1_account_filters.py::test_daily_statement_picked_account_narrows_table` | `len(rows) > 0` | BG.2: `kpi("Drift") == closing - (opening + net_flow)` computed from the picked-account-day row; KPI value identity against `BG.1 query_db(daily_statement_sql, account=…, date=…)`. |
| #2 Daily Statement date picker ignored | `test_l1_account_filters.py::test_daily_statement_picked_account_narrows_table` + `test_l1_additive_pickers.py` (Transactions sheet date_from/date_to anchors) | row-survival only (anchor or date doesn't matter — if date is broken, the anchor row was always there) | BG.2: **delta** assertion — call same view with `picked_day=anchor_day`, capture rows; call again with `picked_day=anchor_day - 7d`, assert `rows != prior_rows` AND `all rendered rows have business_day == picked_day`. The delta + content check together fail unconditionally if date is no-op. |
| #3 Negative Opening Balance for cardholder | `test_l1_account_filters.py` Daily Statement tests | row-survival only | BG.2: assert `kpi("Opening Balance") == BG.1 query_db("SELECT stored_balance FROM <prefix>_daily_balances WHERE account_id=? AND balance_date=?", account, prior_day)`. Whichever side is wrong (KPI or matview) the assertion localizes immediately. |
| #4 Latest Snapshot Drift SUM-cancellation | (no current test on Drift sheet KPI) — would need new assertion inside `test_l1_filters.py` or `test_l1_additive_pickers.py::Drift spec` | drift sheet has `before > 0` precondition only | BG.3: assert `kpi("Latest Snapshot Drift") == BG.1 query_db("SELECT SUM(ABS(drift)) FROM <prefix>_drift WHERE business_day = (SELECT MAX(business_day) FROM <prefix>_drift)")`. Pins the SUM(ABS) measure expression at the runtime level. |
| #5 Volume Anomalies KPI=0 vs populated chart | `test_inv_filters.py::test_min_sigma_slider_shrinks_anomalies_kpi` | `after != before`, with `pytest.skip` when `before_count == 0` | BG.4: **stop skipping the zero case** — when the KPI is 0, the chart's σ-bucket SQL is also queried via BG.1 and the assertion becomes `kpi == 0 AND chart_total > 0 AND subtitle_contains("Zero at default σ")`. Documents the intentional gap as a positive contract, not a silent skip. Plus: identity check `kpi == BG.1 query_db(anomalies_sql, sigma_default)`. |
| #6 Leaf timeline flat constant | (no current test on Drift Timelines chart) | not covered | BG.3: extend Drift Timelines coverage — query the line series' values via BG.1; assert `len(set(values)) > 1` (delta) AND `series == BG.1 query_db(ledger_drift_timeline_sql, leaf=true)` (identity). Constant-line fails delta. |
| #7 Fanout double-count | (no current test on Recipient Fanout) | not covered | BG.4: `kpi("Total Inbound") == BG.1 query_db("SELECT SUM(amount) FROM (inflows-only CTE) WHERE recipient_account_id IN (qualifying)")`. The cartesian inflation in current SQL will fail identity vs the inflows-only ground truth. |
| #8 Total Transactions vs App Info | (no current test on Executives sheet values) | not covered | BG.5: `kpi("Total Transactions") == BG.1 query_db("SELECT COUNT(DISTINCT transfer_id) FROM <prefix>_transactions WHERE status='Posted'")`. Documents the predicate in code; sheet copy can then mirror it. |
| #9 Today's Exceptions presentation | n/a — presentation, not value-correctness | n/a | not BG scope |
| #10 Executives legend overwhelm | n/a — presentation | n/a | not BG scope |
| #11 L2 Exceptions KPI/table units mismatch | (no current test on L2 Exceptions sheet) | not covered | BG.6: `kpi("Open L2 Violations") == BG.1 query_db("SELECT COUNT(DISTINCT check_type) FROM ...") AND sum(table["Count"]) == BG.1 query_db("SELECT SUM(violations_per_row) FROM ...")`. The unit divergence becomes loud + named. |
| #12 Internal Overdraft KPI=0 | `test_l1_additive_pickers.py::Overdraft spec` | anchor-row-survives (row-level) | BG.3: KPI-level assertion: `kpi("Internal Accounts in Overdraft") == BG.1 query_db("SELECT COUNT(*) FROM <prefix>_overdraft")`. If `.count()` resolves to COUNT-DISTINCT on a column with repeats, this fails immediately. |
| #13 Pending Aging chart bar vs KPI/table | (no current test on Pending Aging chart values) | row-survival only via additive_pickers | BG.6: triple assertion — `kpi == table_row_count == sum(chart_bar_heights)`. All three sourced from BG.1 for the same picked filter state. |
| Plant `ach` mystery | (plant-coverage test currently opt-in) | already finds it, just isn't gated | promote `phase2_coverage_tests.py::unmatched_rail_names` from opt-in to layer-2 (data layer). Not a BG cell, but a parallel guardrail BG.7 wires up. |

## What's already honest — preserve, don't rewrite

- **`test_inv_dashboard_agreement.py`** is the load-bearing canonical
  honest-gate test in the suite today. It compares spine + matview + App2 + QS
  for L2 invariants (anomaly + money_trail). The BG.X tightening should adopt
  its assertion style: a per-row `seen ⊆ keys` set comparison + a count
  comparison, both against direct-DB ground truth. Don't reinvent.
- **`test_audit_dashboard_agreement.py`** (referenced from agreement.py
  module docstring; lives under `tests/audit/`) is the 4-way L1 gate
  (matview + QS + App2 + PDF). Same shape; BG.2-BG.3 can lift its KPI /
  table-row identity helpers (`tests/audit/_inv_dashboard_extract.py`
  has the dataset → renderer projection helpers).
- The **anchor-row-survival contract** in `_picker_anchor.py` is solid
  (DB-direct anchor → drive all pickers → assert anchor survives).
  BG.2-BG.6 extends it, doesn't replace it: the `fetch_anchor_row`
  helper + `apply_anchor_to_pickers` stay the entry point; what gets
  added is the BG.1 `query_db()` verb + per-test KPI / row-content /
  delta assertions.

## Highest-leverage targets (BG.X priority order)

1. **BG.2 — `test_l1_account_filters.py::test_daily_statement_picked_account_narrows_table`**.
   Direct catch for #2 (the v11.21.0 blocker) + #1 + #3. Three findings
   in one file. Smallest tightening (single test body) for largest
   coverage gain.

2. **BG.1 — `DashboardDriver.query_db()` verb**. Prereq for every other
   honest assertion. Wraps `_sql_executor.translate_qs_dataset_params`
   + `connect_demo_db(cfg)`. Single addition to `_drivers/base.py` +
   both impls — `QsEmbedDriver` and `App2Driver` delegate to the SAME
   shared helper (so identity is true identity, not "QS round-trip vs
   App2 round-trip"). Per memory `feedback_build_verbs_not_skip`:
   implement on both renderers, don't bolt a one-off helper.

3. **BG.3 — `test_l1_additive_pickers.py::Drift spec` + new Drift KPI
   assertion + Drift Timelines coverage extension**. Catches #4, #6,
   #12 in one tightening pass. Drift Timelines currently uncovered;
   adding a line-series extraction verb is the only new driver work.

4. **BG.4 — Investigation: tighten `test_inv_filters.py` (stop skipping
   the zero case) + un-skip `test_inv_drilldown.py` per its docstring
   plan + add Fanout coverage**. Catches #5, #7, plus brings #drilldown
   back from dead. Use `test_inv_dashboard_agreement.py`'s `seen/keys`
   pattern.

5. **BG.5 — Executives**. Add a single `test_exec_filters.py` mirroring
   `test_l1_filters.py`'s shape with BG.1 identity assertions on Total
   Transactions and Money Moved KPIs. Catches #8. No existing weak test
   to retrofit — Executives only has render + structure today.

6. **BG.6 — L2FT + Pending Aging + Today's Exceptions**. Catches #11
   (L2 Exceptions units), #13 (Pending Aging triple-mismatch). Includes
   delete-or-rewrite of `test_l2ft_metadata_cascade.py` (currently
   dead). Lowest urgency — these are majors, not blockers, and the
   dead-test cleanup can ride along.

## Risks + non-goals

- **Not adding tests for #9, #10, #14-#21**. Those are presentation /
  copy / format defects; BG's honest-gate is about value-correctness.
  They land as standalone follow-up subtasks per the triage doc.
- **Not rewriting the additive_pickers test.** It works; its row-level
  inverse-exclusion is genuinely honest for dropdown-column-vs-row
  identity. BG extends it (KPIs, delta assertions, date inversion)
  rather than replacing.
- **BG.1 wraps `connect_demo_db(cfg)`**. That connection is per-test
  (already standard in `test_inv_dashboard_agreement.py`). No
  module-scoped seeded DB churn introduced; honest assertions piggyback
  on existing fixtures.
- **App2 vs QS parity stays a contract.** BG.1 deliberately runs the
  same SQL through `_sql_executor` (which both renderers use under
  pushdown), so `query_db()` returns ONE result both drivers compare
  against. Parity is already maintained — not a separate axis.
