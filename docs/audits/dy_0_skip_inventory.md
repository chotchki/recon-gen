# DY.0 — skip / xfail / tier-divergence inventory

**Date:** 2026-06-28
**Phase:** DY.0 (ground-truth before the fixes)
**Method:** 4 parallel read-only audits over `tests/` + `src/recon_gen/_dev/runner.py` + `tests/conftest.py`. Nothing edited.
**Posture:** fix-and-enable — every actionable finding defaults to MAKE IT RUN FOR REAL, delete/retire only when genuinely meaningless ([[feedback_dy_more_testing_fix_and_enable]]). Expect re-enabling to surface real bugs; those are in-scope, not deferred.

## The spine — tests that run NOWHERE (the unfinished CB.6 `-m mark` → `--tier` migration)

Four of five runner layers select by DIRECTORY (`unit` = non-e2e dirs; `db` = `tests/e2e/db/`; `app2` = `tests/e2e/app2/`; `agreement` = the three-dir set filtered by `--tier=agreement`, the DW.3 "done" template). **Only `app2_browser` still selects the ROOT `tests/e2e/` dir by the legacy `-m browser` mark** (with `--ignore=tests/e2e/{app2,db,agreement}`). The tier source-of-truth (`@tier(Tier.APP2)`, auto-applied to all 22 root files) and that selector drifted — a root file with the tier but no hand-applied `browser` mark is collected by no dir layer and deselected by `-m browser`, so it runs in **no layer, with no error** (the missing-`@tier` collection guard never fires because the tier IS present).

**Confirmed orphans (11 tests, dark since the CB.5 tier-dir migration):**
- `tests/e2e/test_dashboard_driver.py` — **9** App2 driver smoke tests (`test_showcase_*`, `test_app2_*`). `tier(Tier.APP2)` + `needs(PLAYWRIGHT)`, zero `mark.browser`.
- `tests/e2e/test_studio_deploy_browser.py` — **2** tests (`test_deploy_button_drives_pipeline_and_dashboards_render`, `test_dashboard_auto_reloads_when_data_generation_id_bumps`). Same shape — and this is a Studio Deploy-button → full-pipeline → dashboards-render integration test, real coverage gone dark. Ironically named `_browser`.
- **Systemic:** the trap reopens on every new root `tests/e2e/test_*.py` that forgets the mark. `tests/_marks.py:54-56` even codifies the fragile "browser tests carry the tier AND `@pytest.mark.browser`" contract — a hand-applied mark gating selection is exactly what bit the agreement gate pre-DW.3.
- `tests/e2e/qs_api/` — empty dead dir (qs_api/qs_browser tiers deleted in DW.5.2); remove so the tier-dir set is exactly `{db, app2, agreement, app2_browser}`.

**The fix (mirror DW.3, then go one step further to dir-driven):** create `tests/e2e/app2_browser/` with an auto-marking conftest (adds `tier(APP2)` + `needs(PLAYWRIGHT)` to every item — adding a browser test = `touch` a file, no mark to forget); move all 22 root files in; rewrite the selector to `pytest tests/e2e/app2_browser/ -q` (+ the `-n`/`--reruns`/page-timeout knobs); delete every `pytest.mark.browser` (root + the 6 redundant app2/ files), the three `--ignore` hacks, the empty `qs_api/`, and the `browser` marker registration.

**The reconciliation gate (the invariant-in-process — make "runs nowhere" unrepresentable):** a UNIT-tier test that drives `runner._layer_command` directly so it can't drift from the real runner. For each e2e layer L: `expected = collect-only(tier-dir-for-L)`; `actual = collect-only(L's real argv)` (same dirs + `-m`/`--tier`/`--ignore`, capturing post-deselection nodeids); assert `expected == actual` and print the difference set on failure. Plus a global completeness assert: `union(actual over all e2e layers) == collect-only(tests/e2e/)` so no test can hide at root or in a stray dir. **Run against today's runner this gate FAILS, naming the 11 orphans** — that's the red that the migration turns green. Collect-only is cheap (no DB/browser/Docker) → belongs in the `unit` prelude, gates every push.

## always_skip (12 — POLICY-2 loose ends)

- **10 spine `test_*_detect_does_not_cross_a_sql_pushdown_surface`** — `test_spine_money_trail.py:198`, `test_spine_stuck_unbundled.py:292`, `test_au0_overdraft_full_spine.py:390`, `test_spine_expected_eod.py:288`, `test_spine_limit_breach.py:319`, `test_as0_drift_full_spine.py:447`, `test_spine_anomaly.py:353`, `test_spine_stuck_pending.py:270`, `test_spine_overdraft.py:452`, `test_at0_anomaly_full_spine.py:581`. All share the verbatim reason "set_trace_callback was SQLite-only; DuckDB has no equivalent. CB.8 backlog #set_trace." Removed-SQLite-infra debris on a never-closed backlog. **Fix-and-enable:** find a DuckDB-equivalent way to assert the detector doesn't cross a SQL-pushdown surface (query-introspection / EXPLAIN / a recording cursor wrapper), or re-express the invariant. Not QS debris.
- **#331 `test_inv_drilldown.py:96 test_account_network_table_walk_rerenders_table`** — the worst offender: `if driver.__class__.__name__ == "App2Driver"` is now ALWAYS true (App2 is the sole renderer), so it skips unconditionally, AND its docstring still claims "the [qs] variant covers the same K.4.8 invariant" — that leg is deleted, so K.4.8 (Anchor-pick → bound-table re-fetch) has **zero** live coverage. **Fix-and-enable:** fix the App2 Anchor-param refetch so the test runs and passes.

## xfail (5)

**Stale (2 — strip the marker; QS-removal debris):** both are `strict=False` xfails that only ever applied to the now-deleted QS leg, whose reasons explicitly say the App2 leg XPASSes — so they XPASS every run now, the marker is a lie:
- `test_l1_cross_sheet_drill_date_widening.py:58 test_pending_aging_drill_to_transactions_shows_target`.
- `test_l1_filters.py:90 test_date_range_filter_narrows_drift_sheet`.

**Real App2 bugs parked behind xfail (3 — fix the bug, not the marker):**
- `test_l1_additive_pickers.py:446 + :543` — App2 picker race on the L1 Exceptions sheet (visual data responses fire before `expect_response` sets up its listener; SQL is fast, not a perf issue). Imperative `pytest.xfail` gated on the Exceptions sheet.
- `test_html2_executives_live.py:339 test_date_filter_narrows_every_date_sensitive_count_kpi` — `strict=False`, live-DB: the date filter doesn't narrow Active Accounts / Net Money Moved (bind not reaching, OR matviews encode as-of-now vs as-of-:window). May be silently XPASSing. Triage the date-window KPI semantics.

(`test_drill_guardrail.py:330` is a legit defensive `strict=True` xfail — only emitted when an app build crashes at collection. Leave it.)

## Driver gaps — NOTHING to build

All 11 `raise NotImplementedError` under `tests/e2e/_drivers/` are accounted for and need no action:
- `app2.py:1464/1495/1530` (`drill_from_first_row`, `..._via_menu`, `open_metadata_panel`) — **precondition guards inside fully-implemented verbs**, firing only when the specific table/row lacks the affordance (`<tr data-row-drill>` / ⋯ button / metadata popup), converting to `pytest.skip` via `skips_if_unsupported()`. Not POLICY-2 gaps.
- `studio_editor.py:242-290` (8) — abstract-method stubs on `_BaseStudioEditorDriver`; both concrete transports override them (the base raises so a half-built transport fails loudly). Different driver family.
- `base.py` — zero real raises (it's the Protocol; bodies are `...`). The parametrize sweep confirms `[qs,app2]→[app2]` is **complete** — every renderer fixture is `params=["app2"]`, no `qs` callspec produced anywhere.

## Stale QS prose (cosmetic doc sweep — present-tense framings of the deleted driver)

The DW doc sweeps missed these present-tense / live-impl framings (most `tests/` QS hits are CORRECT past-tense annotations — leave those):
- `tests/e2e/_drivers/base.py` — module docstring ("Two impls: QsEmbedDriver … and App2Driver", `[qs, app2]` parametrize), the `dialect` attr ('"qs" for the embedded QuickSight dashboard'), ~15 verb docstrings carrying "App2-only — QsEmbedDriver raises NotImplementedError", and ~25 lines of "QS reads/virtualizes/stamps vs App2" comparison prose.
- `tests/audit/_dashboard_extract.py` (module docstring — "sealed inside QsEmbedDriver.table_row_count") + `tests/audit/test_dashboard_extract.py` ("talks to a live QuickSight dashboard via Playwright").
- `tests/e2e/test_inv_filters.py` (docstring "[qs, app2]" + a "**qs** — QsEmbedDriver.set_slider" bullet; body is current).
- `tests/e2e/test_inv_drilldown.py` (docstrings rationalizing the #331 skip via the dead [qs] leg).
- `tests/e2e/_capture.py` (docstring "QsEmbedDriver exposes ._page"; the `._page` branch is now dead code — strip it).
- Stragglers: `app2/test_dm_cascade_and_day_availability.py`, `test_parameter_anchored_sheets.py` ("renders identically on QuickSight and App 2"), `_picker_anchor.py`, `test_l1_account_filters.py:2,126`, `app2/test_html2_executives.py:13,23-24`, `test_drill_guardrail.py:365`.

## Known items

- **#239 (framing CORRECTED by the audit):** NOT "skip-on-sasquatch." Post-CS.12 the producer (`app2/test_inv_anomaly_app2.py:104`) + validator (`agreement/test_inv_anomaly_agreement.py:50`) skip only when the L2 declares NEITHER `CustomerSubledger` NOR `CustomerDDA` (sasquatch HAS `CustomerDDA`, so it runs). This is an L2-robustness gap (`_build_anomaly_generator` is brittle on role shape), not a sasquatch skip. Lower priority; the L2-optional skip is arguably legit, but DY can broaden the generator so fewer L2s fall through.
- **Screenshot capture (DW.8.4):** `src/recon_gen/common/browser/screenshot.py::capture_app_dashboards` (NotImplementedError placeholder, build recipe in its docstring) + `tests/unit/test_screenshot_placeholder.py` (pins it) both confirmed; prose is correctly past-tense. Build per the recipe (start the `recon-gen dashboards` server on an ephemeral port → `webkit_page` goto → walk `app.analysis.sheets` → `page.screenshot(full_page=True)` → `?param_<name>=` query string), retiring the pin.

## Aggregate

~95 live skip/xfail sites + ~155 `importorskip` optional-dep guards. The ~75 remaining `pytest.skip` + 5 `skipif` are all **conditional_legit** — docker/WebKit/optional-dep probes, cfg/DB-presence gates, per-dialect agreement routing, the documented `skips_if_unsupported` carve-out, and seed-presence / L2-topology / fixture-shape guards ("matview empty", "L2 declares no chains", "fixture has no limit_schedules") — genuinely sometimes-false, no action. (Two cosmetic: `test_studio_deploy_browser.py:73` imports a stale-named `QUICKSIGHT_GEN_BIN` constant that resolves to `.../recon-gen`; `test_bv33_trainer_dogfood.py:728`'s skip is gated on an empty frozenset — dormant.)
