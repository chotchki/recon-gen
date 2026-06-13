# DG.3 — qs_browser failure triage

**Date:** 2026-06-13
**Phase:** DG.3
**Status:** In progress — waiting for local `./run_tests.sh up_to=qs_browser` to complete.

## Baseline: 12 failures from v13.15.1 release-gate CI run

`gh run 27454069073` (sha `f4768ebd`, the DB.3 followup push before DG hygiene work shipped):

| # | Test | Failure shape | Likely class |
|---|---|---|---|
| 1 | `test_inv_filters.py::test_bg4_recipient_fanout_kpis_match_inflows_only_truth[qs]` | `psycopg.errors.DiskFull: could not resize shared memory segment ... No space left on device` | **Infra** — `/dev/shm` exhausted from cumulative debris (root cause of the DG phase) |
| 2 | `test_inv_filters.py::test_bg4_recipient_fanout_kpis_match_inflows_only_truth[app2]` | Same `DiskFull` | **Infra** — same root |
| 3 | `test_l2ft_exceptions.py::test_bg6_l2ft_exceptions_table_count_column_sums_to_dataset_total[app2]` | Timeout waiting for `L2 Violation Detail` `.visual-data:not(:has(.visual-loading))` | **Likely cascade** — DB under DiskFull pressure → matview reads stall → App2 visual never paints |
| 4 | `test_l2ft_exceptions.py::test_bg6_l2ft_exceptions_kpi_matches_dataset_distinct_check_types[app2]` | Timeout on `Distinct Exception Types Open` KPI | **Likely cascade** — same shape as #3 |
| 5 | `test_l2ft_cross_sheet_drill.py::test_l2_exceptions_view_in_chains_narrows_destination[app2]` | Timeout on `L2 Violation Detail` | **Likely cascade** |
| 6 | `test_l2ft_additive_pickers.py::test_l2ft_additive_pickers_keep_anchor_row[app2-Rails]` | `Page.wait_for_function: Timeout 15000ms exceeded` | **Likely cascade** |
| 7 | `test_l2ft_additive_pickers.py::test_l2ft_dropdown_pickers_inverse_excludes_anchor[qs-Rails]` | `QuickSight visual 'Transactions' rendered with error overlay: Show details` | **Likely cascade** — QS visual error overlay typical of DB-backed-fetch failure |
| 8 | `test_l2ft_rails_dropdowns.py::test_rails_dropdown_narrows_does_not_empty[qs-Status]` | Same QS error overlay on `Transactions` | **Likely cascade** |
| 9 | `test_cq_picker_search_and_find.py::test_cq_4_e_l1_picker_finds_known_value[qs-Transactions-Transfer]` | `Page.wait_for_selector: Timeout 2000ms exceeded` on QS dropdown | **Possibly flake** — 2 s timeout is tight; could be QS dropdown lazy-load slipping |
| 10 | `test_inv_drilldown.py::test_account_network_table_walk_rerenders_table[qs]` | `Page.wait_for_selector: Timeout 30000ms exceeded` waiting for `Anchor` filter | **Likely cascade** — Anchor filter doesn't paint because backing visuals are wedged |
| 11 | **`test_l1_account_filters.py::test_bo_1_daily_statement_picks_reconcile_per_role[qs]`** | `ZBASubAccount` + `WireSettlementSuspense` accounts missing from Account dropdown (shows 5 unrelated control-tier accounts instead) | **Looks REAL** — picker source narrowed to control-tier accounts only; ZBA + WireSuspense roles have no `internal-scope` accounts with balance rows. Pre-existing, not DG-related. |
| 12 | **`test_inv_sheet_visuals.py::test_inv_dashboard_structure_matches_tree[qs]`** | `Recipient Fanout` sheet missing 3 KPI titles (`Distinct Senders (Union)`, `Qualifying Recipients`, `Total Inbound`); only the `Recipient Fanout — Ranked` table renders | **Looks REAL** — QS rendering 1 of 4 visuals on Recipient Fanout. Suspects: KPI visuals timing out individually on first paint, or QS rejecting the KPI configurations during analysis create. Pre-existing. |

## Categories

- **Infra**: 2 (#1, #2)
- **Likely cascade from DiskFull**: 6 (#3, #4, #5, #6, #7, #8, #10)
- **Possibly flake**: 1 (#9)
- **Likely REAL**: 2 (#11, #12)

## Hypothesis after DG.1+DG.2

With hygiene fixed, the expected failure shape collapses to:

- 0 × DiskFull (sweep clears debris; `/dev/shm` doesn't saturate)
- 0 × cascade (cascade-driving DB pressure is gone)
- Possibly 1 × flake (#9 — QS dropdown lazy-load can still be slow on cold embed)
- 2 × REAL (#11, #12) — same bugs, not introduced by DG

The DG.1+DG.2 CI run (sha `1ce833a9`, run 27455693211) was **cancelled** mid-qs_browser at 28-min mark because the rerun storm pushed the chain past its time budget. The progress markers showed:
- `[ 32%]` at 04:02:41 — normal pace
- `[ 62%]` at 04:09:38 — 7 min for next 30%
- `[ 83%]` at 04:25:16 — 15 min for next 21% (rerun cycle dominating)

The `--reruns 2 --reruns-delay 60` config in `src/recon_gen/_dev/runner.py:908` is HOSTILE under a high-flake regime — each failing test burns up to 120 s of delay before final-failure. With ~15+ failing tests that's ~30 min of sleep alone.

## Action items (pending local run completion)

1. **Local qs_browser triage run** (`./run_tests.sh up_to=qs_browser --allow-dirty-deploy`, in progress). Capture full failure set; diff vs the 12 baseline. Expect 1-3 REAL bugs + flakes; confirm cascade is gone.
2. **Reduce rerun budget** during triage. Either (a) drop `--reruns` to 1 with 5 s delay temporarily until DG.3 closes, (b) override via env, or (c) hold the config at 2/60 but quarantine known-flaky tests so the rerun storm doesn't fire.
3. **Fix the REAL bugs** identified in #11, #12.
4. **Quarantine the flakes** with explicit `pytest.skip` + a backlog entry, OR fix them.
5. **Re-run CI**. With hygiene + triaged failures, the chain should finish in budget.

## Fixes shipped during triage (in flight)

While the local `up_to=qs_browser` run churned in background, two of the v13.15.1 failures have already been root-caused + fixed:

### #11 (REAL) — `test_bo_1_daily_statement_picks_reconcile_per_role[qs]` — virtualized-dropdown membership check

Root cause: the test called `driver.filter_options("Account")` then asserted `account_display in account_opts`. `filter_options` reads `[role="option"]` from the open dropdown listbox — but QS's MUI Autocomplete virtualizes at ~12 alphabetical options. Accounts deep in the alphabet (`SNB ZBA Sub-Account #011`, `Wire Settlement Suspense`) silently fell out of the mounted window, even though they WERE picker-reachable via the operator's typeahead flow.

Fix: swap to `driver.typeahead_filter("Account", account_display)` — types the account display string + reads the server-narrowed result. Same shape as a real operator interaction. `tests/e2e/test_l1_account_filters.py:163-185`.

### #9 (FLAKE) — `test_cq_4_e_l1_picker_finds_known_value[qs-Transactions-Transfer]` — 2 s inner timeout propagated

Root cause: `wait_for_dropdown_options_present` polls `read_dropdown_options` with a 2 s per-attempt budget inside a 15 s outer loop. The inner call's `playwright.TimeoutError` propagated unchecked, making the outer 15 s timeout illusory. Under cumulative CI load + cold-deploy first-paint of the 8k+-row `DS_L1_TX_IDS` picker, the first 2 s attempt frequently times out.

Fix: catch `TimeoutError` per inner attempt + retry inside the outer poll. `src/recon_gen/common/browser/helpers.py::wait_for_dropdown_options_present`. Regression-pinned in `tests/unit/test_dg_3_dropdown_retry.py` (4 tests — happy path / retry-through-timeout / outer-deadline-empty-fallback / non-TimeoutError-propagates).

### #12 (REAL, deferred) — `test_inv_dashboard_structure_matches_tree[qs]` — Recipient Fanout 3 KPIs missing

Diagnosis pending live QS data. The Table renders + the 3 KPIs share the same dataset, so it's not a dataset issue. Most likely candidates: QS-side render flake on cold deploy (visual frames mount async; 30 s wait may not catch all 3 KPI titles in DOM under load), or a deeper QS quirk where DISTINCT_COUNT KPIs paint slower than SUM/CategoricalMeasureField on the first sheet visit.

Deferring until the local run completes — if #12 reproduces locally cleanly, it's a real render-pace issue we can fix with a longer per-visual wait. If it only fails on CI, it's a CI-specific flake to quarantine + investigate as a follow-up.

## Pending — local-run failure summary

Local `./run_tests.sh up_to=qs_browser --allow-dirty-deploy` running in background. Will diff against the 12-failure baseline once it completes.
