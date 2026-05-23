# QuickSight Generator — Active Plan

## Phase AI

Per `feedback_build_verbs_not_skip`: when an editor verb's underlying UI is missing, BUILD the UI (and the verb that wires to it), don't skip the test param.
The dashboards-match assertion is the user-facing acceptance: "the editor produces an L2 that drives identical dashboards".
- [ ] AI.0 - Locks (decisions before AI.1 fires, 2026-05-19).
  - **Scope split for missing editor surfaces** (Lock 1, confirmed 2026-05-19). If the AI.1 audit reveals a non-trivial editor UI gap (no widget for a primitive that some test-input L2 uses), AI.2 BUILDS the missing UI inline as a sub-task per entity kind. Phase ships only when the editor can recreate the full corpus. No "scope to current surface; defer gaps" escape hatch — the dogfood claim has zero acceptable noise floor.
  - **L2Instance equivalence granularity.** Compare via parsed `L2Instance` dataclass equality, not byte-equality on the YAML file. Editor-emitted YAML may diff in formatting (field ordering, comment placement, indentation) but the L2Instance struct MUST match. Use existing `tests/unit/test_l2_loader.py`-style structural asserts.
  - **Dashboard equivalence granularity** (Lock 2, confirmed 2026-05-19). Per-sheet, per-visual: visual titles + table row content (rows + cell text) + KPI numeric values. Skip DOM byte-equality + screenshot diffs. Hermetic comparison: same L2 + seed + anchor must yield identical `DashboardDriver.visual_titles()` + `table_rows()` + `kpi_value()` outputs. Filter unstable fields (analysis_id / sheet_internal_id / wall-clock timestamps) from the comparison dict.
  - **Test layer = browser** (Lock 3, confirmed 2026-05-19). New file `tests/e2e/test_studio_dogfood.py` gated behind `QS_GEN_E2E=1`. Runs under `./run_tests.sh up_to=browser`. Marker: `@pytest.mark.browser` (existing convention).
  - **Transport: hybrid** (Lock 3 amendment, decided 2026-05-21 after the AI.1 audit revealed the editor surface is large — rail subtype picker, multi-selects, `multi_select_groups`, chain per-child checkboxes, 3 YAML-block singletons). `StudioEditorDriver` is one verb protocol with two transports: (a) `StudioBrowserEditorDriver` (Playwright) drives ONE full pass on the deterministic `spec_example` for real form-render+submit fidelity; (b) `StudioHttpEditorDriver` (httpx form-POST against a running studio server) drives `sasquatch_pr` + the fuzz-sampled bulk — fast, deterministic, fuzz-scalable. Rationale: the unit route tests (`test_studio_editor_routes.py`) already prove server-side coerce→create→serialize→save end-to-end, so HTTP covers the same fidelity; Playwright's marginal value (a form that renders-but-mis-submits) is captured by the one spec_example browser pass rather than a full-corpus×fuzz browser rebuild.
  - **Determinism + reproducibility.** Anchor `date(2030, 1, 1)`; `RECON_GEN_FUZZ_SEED` pinned per CI cell. Editor mutations are exact sequences (no waits-for-element ambiguity); fail loudly if a mutation widget reports unexpected state. Studio cfg is ephemeral (tmpdir-rooted) per test invocation so studio's `.studio-state.yaml` doesn't pollute across runs.
  - **No mutation of source yamls.** Dogfood'd YAML writes to `tmp/dogfood_<instance_name>.yaml` (test-scoped tmp dir). The shipping `tests/l2/spec_example.yaml` + `tests/l2/sasquatch_pr.yaml` are reference; the test asserts dogfood matches reference.
  - **Fuzz axis sample size.** Locked-input seeds: 5 per CI cell. Override via `QS_GEN_AI_FUZZ_SAMPLE_N=N` env var (default 5, runner pins this to a known-good value for the deterministic suite; ad-hoc local testing can crank it). The fuzz pool itself is `tests/l2/fuzz.py::random_l2_yaml(seed)` — already produces validator-passing L2 instances. Add an opt-in nightly run that bumps the sample to 100+ once the deterministic 5-seed sample is green.
  - **Editor save-to-yaml route.** The editor needs a single POST route that serializes the current in-memory L2Instance to a yaml file (file path supplied or returned). If no such route exists, AI.2 builds it (preferred path: `POST /l2/export?path=<dest>` returns 204 after writing). Save-on-mutate (every edit re-flushes to disk) is the current behavior; AI confirms or adjusts as needed.
- [x] AI.1 - Editor surface coverage audit. Walk every entity kind in the test-input corpus (spec_example + sasquatch + 5 fuzz seeds) and inventory whether each kind has an "add/edit/delete" widget in the Studio editor. Cover:
  - Account (with all optional fields: name, role, parent_role, expected_eod_balance, description)
  - AccountTemplate (with instance_id_template / instance_name_template)
  - SingleLegRail (with origin variants, metadata_keys, leg_role, leg_direction)
  - TwoLegRail (source_role, destination_role, source_origin, destination_origin)
  - AggregatingRail (cadence, bundles_activity)
  - TransferTemplate (leg_rails, transfer_key, completion, leg_rail_xor_groups)
  - Chain (parent, children with ChainChildSpec including fan_in + expected_parent_count + mixed-cardinality)
  - LimitSchedule (per-rail + per-account_type caps)
    Produce `docs/audits/ai_1_editor_surface_audit.md` listing per-entity gaps + "needed widget" punch list. This is the discovery step — without it, AI.2 has no driver-verb scope. Run the audit BEFORE locking AI.2 effort.
- [ ] AI.2 - StudioEditorDriver verbs + missing UI builds. Extend the App2Driver protocol (or subclass it as `StudioEditorDriver` in `tests/e2e/_drivers/`) with editor verbs keyed off the AI.1 audit. Per Lock 1, build any missing editor UI inline as AI.2.x sub-tasks. Driver shape:
  - `create_account(id, role, scope, **opts)` + similar for other entity kinds
  - `set_template_leg_rail_xor_groups(template, groups)` (AB.3 surface)
  - `create_chain(parent, children: list[ChainChildSpec])` (AB.6 surface — mixed-cardinality)
  - `save_l2_to_path(path)` — invoke editor's serialize-to-yaml route, write to disk
  - Bulk-create helper `create_l2(reference: L2Instance)` that walks reference entities in dependency order and creates each via the verb-per-entity-kind path
  - [x] AI.2.a - AI.2.a Fix create-path field drops (rail cadence/amount_typical_range/firings_typical_per_period + chain per-child fan_in/epc)
  - [x] AI.2.b - AI.2.b TransferTemplate transfer_key field (FieldSpec + create wiring)
  - [x] AI.2.c - AI.2.c Top-level L2 editor for description + role_business_day_offsets (new singleton kind)
  - [ ] AI.2.d - AI.2.d StudioEditorDriver verbs + create_l2(reference) bulk helper
    - [ ] AI.2.d.1 - AI.2.d.1 Protocol + HTTP transport + create_l2 bulk *(WIP — protocol + encoders + HTTP transport + create_l2 walk + build_editor_app shipped; rebuild round-trip blocked, see AI.2.d.1.a)*
      - [ ] AI.2.d.1.a - Defer-validation bulk-load path. The rebuild round-trip (`test_studio_editor_driver.py`, currently SKIPPED) surfaced that the editor runs full `validate()` after EACH create/save, so an incremental bulk rebuild hits invalid intermediate states (an AccountTemplate whose `parent_role` isn't yet on any Account → 400). Two candidate fixes: (1) a defer-validation bulk path that validates ONCE at the end; (2) a topological create order over the reference graph (parent-accounts → child-accounts → templates → rails → transfer_templates → chains → limits) IF every validator check is reference-resolution (no completeness checks on partial graphs). Spike (2) first — no editor-surface change; fall back to (1) if completeness checks fail on partial graphs. Un-skip the rebuild test once green.
    - [ ] AI.2.d.2 - AI.2.d.2 Playwright transport (spec_example pass)
  - [x] AI.2.e - AI.2.e Route diagram edit/add affordance to dedicated screens (drop inline-on-diagram editing)
- [ ] AI.3 - Test harness — `tests/e2e/test_studio_dogfood.py`. Parameterized over L2 yaml input:
  ```python
  @pytest.mark.parametrize("l2_source", [
      pytest.param("tests/l2/spec_example.yaml", id="spec_example"),
      pytest.param("tests/l2/sasquatch_pr.yaml", id="sasquatch_pr"),
      *[
          pytest.param(_fuzz_yaml(seed), id=f"fuzz_{seed:010d}")
          for seed in _fuzz_seeds_for_run()
      ],
  ])
  ```
  Sequence per test case: (a) load reference L2 via `load_instance(l2_source)`; (b) start `recon-gen studio` on a tmpdir-rooted cfg + empty L2; (c) StudioEditorDriver creates every entity from the reference IN DEPENDENCY ORDER (AccountTemplates → Accounts → Rails → TransferTemplates → Chains → LimitSchedules); (d) save via `save_l2_to_path(dogfood_yaml_path)`; (e) shutdown studio. Failures surface the FIRST missing editor verb + entity + cell.
- [ ] AI.4 - L2Instance equivalence assertion. Load both `l2_source` and `dogfood_yaml_path` via `load_instance`. Assert structural equality: `original.accounts == dogfood.accounts`, `original.rails == dogfood.rails`, etc. Use dataclass `__eq__`. Fail with a focused diff (which entity differs in which field). This is the FIRST acceptance gate — if it fails, the editor lost information during the round-trip.
- [ ] AI.5 - Dashboard equivalence assertion (SQLite-only, App2 only). For each of `(original L2, dogfood L2)`:
  1. Apply schema against a fresh SQLite db (one per L2; tmpdir-rooted)
  2. `data apply --execute` against the same anchor + seed
  3. `data refresh` to materialize L1 + Investigation matviews
  4. Start `recon-gen dashboards -c <ephemeral cfg> --l2 <yaml>` on a distinct port; mount in App2Driver
  5. Walk every dashboard's every sheet's every visual; collect `(dashboard_id, sheet_name, visual_title) → {row_count, rows, kpi_value}` into a comparison dict
  6. Compare the two collected dicts; assert byte-equal modulo dashboard-internal-id randomness
- [ ] AI.6 - Fuzz axis wiring. Extend the runner so `./run_tests.sh up_to=browser` honors `QS_GEN_AI_FUZZ_SAMPLE_N` (default 5 per cell; nightly opt-in cranks to 100+). Fuzz seeds derive from the run-id-hash so reruns at the same commit see the same seed pool (per `feedback_fuzzer_as_property_testing.md` reproducibility contract). Failed fuzz seeds get re-runnable via `./run_tests.sh up_to=browser --variants=fNNNNN_sl_lo`.
- [ ] AI.7 - Re-verify + commit. Run `./run_tests.sh up_to=browser --scenarios=sp --dialects=sl --targets=lo` locally to confirm the 5-seed deterministic dogfood suite passes; gate CI on it via the existing browser-job pipeline (`e2e.yml` or analog). Phase history one-liner: "AI — Studio editor dogfood: ANY L2 yaml (spec_example + sasquatch_pr + fuzz-sampled) rebuilt via browser-driven editor matches reference structurally + in dashboard output (SQLite/App2 only)".
- [x] AH.8 - Docs builds: isolate into run-folder sandbox (kills xdist flakes)
- [ ] AH.9 - Tailwind output.css scan-scoping — stop whole-repo prose pollution
- [ ] AH.10 - demo-publish on-release refresh broken (trigger + runner-user)
- [ ] AI.0 - Studio editor dogfood: any L2 yaml rebuilt via browser-driven editor

## Phase AM - Standardize on tailwind *(prefer after: AI)*
The HTML surface is split across two styling systems: App2 dashboards + rich-text render via Tailwind utilities (`output.css`), but the Studio editor + diagram pages are styled by hand-written `editor.css` + `diagram.css` (semantic classes — `.create-page`, `.studio-header`, `.create-form`, …). Standardize the whole surface on Tailwind so there's ONE system (no drift between two CSS approaches; the editor screens already LOAD `output.css`, so the utilities are right there).

**Sequenced after Phase AI deliberately** (user, 2026-05-21): the AI dogfood round-trip (structural + dashboard-equivalence, browser-driven) pins the editor's behavior + emitted HTML structure, so the CSS refactor lands with a regression safety net and doesn't churn the editor markup mid-test-build.

- [ ] AM.0 - Standardize the Studio + editor surface on Tailwind *(future — sequenced AFTER Phase AI)*
  - [ ] AM.0.1 - Audit + spike: inventory every hand-written class in `editor.css` + `diagram.css`; map each to a Tailwind utility set (or an `@apply` component class). Lock utility-inline vs `@apply`-component (lean: `@apply` for repeated structures like `.create-form`, inline utilities for one-offs). Spike the Tailwind build + scan-scoping so `output.css` covers the `_studio_assets` templates (ties to AH.9 / #176 output.css scan-scoping).
- [ ] AM.1 - Convert the editor screens (create / edit / list / singleton / read-card) to Tailwind; drop the `editor.css` rules they replace. **Fold in the AI.2.e part-2 stretch** — richer subtype-aware per-field requirement hints beyond the existing `*` markers + entity intro (e.g. a subtype requirements banner on the rail form).
- [ ] AM.2 - Convert the diagram + Studio chrome (`studio-header`, nav, data-knob panel) to Tailwind; drop `diagram.css` rules.
- [ ] AM.3 - Verify: re-screenshot create / edit / diagram / dashboards before↔after for visual parity; AI dogfood + browser e2e stay green; output.css scan-scoping doesn't regress.
- [ ] AM.4 - Drop `editor.css` / `diagram.css` (or reduce to the irreducible non-utility remainder); update asset links; commit + Phase AM history one-liner.

## Phase AO

Findings route to four buckets: the money-precision root (AO.1 — drives several drift symptoms), real dashboard/SQL bugs (warm pass), seed/instance artifacts (don't ship as dashboard bugs), and confirm-on-AWS. **Keystone = AO.2 (Daily Statement KPIs don't compute)** — newly visible after the v11.9.4 picker fix let the statement populate.

> **Autonomous /goal run (night of 2026-05-21) — operating agreement:** stage every fix on the `ai-studio-editor-dogfood` branch (commit + version-bump + RELEASE_NOTES ready), but do NOT merge to main / tag / push — the operator ships after a morning visual check. Verify to unit + data + 4-way-agreement + App2-local-render level; the QS-render / cold-read visual pass is the operator's. Judgment / taste calls: pick a sensible reversible default, implement it, and flag it here + in the morning summary. Work order: AO.2 → AO.1 → AO.3 → AO.4/5/6; hold the presentation/seed/AWS-visual items (AO.7-9 wording, AO.S, AO.C) for operator review.
>
> **RUN LOG (night of 2026-05-21, staged on `ai-studio-editor-dogfood` — nothing pushed/tagged):**
> - ✅ **AO.2 diagnosed** (signed-`MAX` + TEXT-date mismatch, data-confirmed) + executable fix plan recorded. Impl deferred — render-affecting + regenerates QS json fixtures, wants a visual confirm + the default-day decision. Commit `42c4...`-era diagnosis.
> - ✅ **AO.3 DONE (code)** — Investigation institution name persona-driven + neutral fallback; new gate test; unit green. Commit `e55d8aed`.
> - ✅ **AO.6 DONE (code)** — L2 Exceptions `count` column → "Occurrences" (disambiguate from the violation-tally KPI); l2ft json + unit green. Commit `f582456c`.
> - ⏸ **PAUSED** the autonomous run here (deliberate — long session; remaining items are judgment/visual/migration-heavy, better fresh/paired). **Operator morning checklist:** (1) visual-confirm AO.3 + AO.6 on a deploy, then ship as a patch (version bump + RELEASE_NOTES + tag + push). (2) **NEXT to implement:** AO.7 (rail direction mislabel — needs the specific rail found in `sasquatch_pr.yaml` + a judgment on wrong-vs-intentional; may re-lock seeds), then AO.4/AO.5. (3) **Pair with me on:** AO.2-impl (date SQL-pushdown) + AO.1 (money cents — its 4-way gate won't catch a *uniform* 100× translation error → needs a known-value/visual check, not just suite-green).

**Money precision (systemic root — underlies the drift symptoms in feedback #1/#3/#4).** `amount_money` / `money` are declared `DECIMAL(20,2)` (exact on PG/Oracle) but SQLite's NUMERIC affinity stores them as REAL (float64); `_computed_subledger_balance`'s `SUM` accumulates ~1e-11 float error → the `_drift` matviews' exact `money <> computed_balance` flags float dust (114/116 rows display $0.00). SQLite-only manifestation.
**Real dashboard / SQL bugs (warm pass):**
**Seed / instance-side artifacts (don't ship as dashboard bugs — fix seed or annotate):**
**Confirm-on-AWS:**
- [ ] AO.0 - v11.9.4 cold-read warm-pass dashboard fixes *(source: `docs/audits/v11_9_4_feedback.md` — 4 context-isolated cold-judge passes; per-finding agreement N/4 + triage routing live in that file. Data-correctness AND presentation both in scope.)*
- [ ] AO.1 - (ROOT fix) Store money as integer cents, translate back at the edges (user call 2026-05-21 — materiality band rejected as a band-aid). **Spike done:** cents-space drift = 2 genuine vs 116 float-noisy on sasquatch_pr SQLite (≈ PG's exact NUMERIC → restores cross-dialect agreement); blast radius = 5 money columns, ~45 `currency=True` display sites (unchanged), money math concentrated in a few matviews. Design: money MATH in integer cents, translate cents→dollars at ONE chokepoint (`Current*` / matview projection). Footguns: ingest `×100` round-to-int at the `etl_hook`; literals flip to cents; divisions cast to NUMERIC; a missed `÷100` = silent 100× error.
  - [x] AO.1.lock - LOCKED: UNIFORM cents (BIGINT money base on ALL dialects; dollars-at-projection). Decided 2026-05-21. Decisive factor = the ETL integrator: an upstream-Oracle → downstream-SQLite pipeline must feed ONE money representation, so the customer feed contract (`<prefix>_transactions.amount_money` / `<prefix>_daily_balances.money`) MUST be dialect-agnostic. Uniform = one feed contract (integer cents); `etl_hook` converts dollars→cents at the edge. Update `docs/Schema_v6.md`.
  - [ ] AO.1.impl - Per lock: money cols → BIGINT cents; matview math in integer cents; `Current*`/matview projection → dollars; seed emits cents + `etl_hook` `×100`; literals → cents; re-lock seeds; SQLite structural drift count 116→2; full suite + 4-way green; commit + patch release.

- [x] AO.2 - (feedback #1 · BLOCKER 3/4 · KEYSTONE) Daily Statement summary KPIs don't compute / don't tie to the detail. **DONE (piece 1 — the keystone) `8c4c8815` on branch `ao-dashboard-sql-fixes` (stacked on `ao-app2-renderer-parity`; both unmerged).** Live-verified on :8766 (cust-0011, account picked): Opening −$9,174,092.74, Debits −$147,378.52, Credits $18,761.76 (were all $0). Full unit + json green.
  - **DIAGNOSIS (2026-05-21, data-confirmed; matview is CORRECT — real per-day debits/credits/drift):** two bugs. (1) **Signed-`MAX` aggregation** — the 5 KPIs use `ds_summary[col].max()` (`_populate_daily_statement_sheet`, app.py ~1490-1521) assuming the date filter narrows to one row; but `total_debits` is stored NEGATIVE and `drift` is signed, so `MAX(total_debits)`=0 and `MAX(drift)`=0 over multiple days (a zero/no-activity day wins the max). (2) **Date filter can't narrow** — the balance-date is an analysis-level `TimeEqualityFilter` on `business_day_start` (`_wire_daily_statement_filters`, app.py ~2080), but `business_day_start` is a TEXT timestamp (`'2026-04-29 00:00:00'`); a DAY-granularity equality (`= '2026-04-29'`) matches 0 rows (`date(business_day_start) = '2026-04-29'` matches 1) → summary narrows to empty → all KPIs read 0 while the detail (txn `business_day` = `date_trunc`) still populates = the "KPIs 0, detail full" asymmetry. (Also: the balance-date defaults to wall-clock yesterday → misses fixed-anchor seed data — secondary.)
  - **FIX (canonical SQL-pushdown):** push the balance-date into the summary + txn dataset SQL via a date dataset-param with a day-truncated comparison (`date_trunc_day(business_day_start) = <<$pBalanceDate>>`), replacing the analysis-level `TimeEqualityFilter`s — fixes the TEXT match AND guarantees exactly one (account, day) row so the signed-`MAX` is moot. Verify at the data level (run the dataset SQL with a date → one row + correct per-day values) + App2 local-render KPI assertion. Operator visual-confirms after.
  - **DONE (piece 1, `8c4c8815`):** both daily-statement datasets push `pL1DsBalanceDate` (DateTimeDatasetParameter) down with `date_trunc_day(<col>) = date_trunc_day(<<$param>>)` (dialect-portable) + a sentinel→`MAX(day)`-per-account fallback (data runs through *yesterday* — the 2030 anchor is locked-seed-only — so latest-day opens populated even for accounts whose last activity wasn't literally yesterday; user call 2026-05-21: "latest-day data, picker best-effort"). Dropped `fg-l1-ds-summary-date` / `fg-l1-ds-txn-date`; bridged `P_L1_DS_BALANCE_DATE` → both dataset params. Rewrote `test_daily_statement_date_pushes_down_not_filter_group`. Verified end-to-end via the App2 executor on live SQLite (1 row each: active/sentinel→latest, picked→that day, sparse→its latest).
  - **TODO piece 2 (App2 single-date control — operator-flagged range-vs-single):** App2 still renders the universal date-RANGE for this sheet because `common/html/_tree_filter_specs.py` (see its lines ~40/104) lists `ParameterDateTimePicker` as out-of-scope ("date-control parity is the universal date range"). Fix: add a single-date `FilterSpec` (e.g. `ParameterDateSpec`) in `common/html/render.py` + a flatpickr-single renderer, and a `ParameterDateTimePicker` case in `make_filter_specs_for_sheet` → `?param_pL1DsBalanceDate=<date>`. Then App2 shows ONE date picker matching QS (the tree already wires `add_parameter_datetime_picker`). QS picker shows yesterday/last-picked best-effort (can't data-drive to "latest"); data opens on latest via the sentinel — flag the brief picker-vs-data disagreement for the operator's QS visual pass.
  - **FLAG (latent, operator):** the signed-`MAX` is moot with one row, but "Debits" still displays the stored-NEGATIVE value (−$147k). Consider showing magnitude (positive) for the Debits KPI — a one-line presentation call for the operator's visual pass.
- [ ] AO.3 - (feedback #2 · BLOCKER 2/4) Investigation (AML) landing shows the template-default institution name instead of the deploy's configured identity — a placeholder on the examiner-facing compliance surface. Source the getting-started title from the L2 persona/config so a real deploy shows the real name (demo = the Sasquatch persona; the bug is it isn't config-driven / reads as a placeholder).
  - **DONE — code-complete + verified, staged on branch (commit e55d8aed), pending operator visual + ship.** `_build_getting_started_sheet` now reads `l2_instance.persona.institution[0]` (neutral "the shared base ledger" fallback when no persona). Verified: spec_example → no "Sasquatch National Bank" leak; sasquatch_pr → renders it. New gate `tests/unit/test_investigation_getting_started_persona.py`; unit layer green (2872). Grep confirmed only `investigation/app.py:220` had the hardcode in RENDERED prose (L1/L2FT matches were comments). **FOLLOW-UP (flagged):** there's no general *dashboard-prose* persona-neutral gate (the docs gate scans mkdocs only, not analysis JSON); my test covers just the investigation landing. A broad gate (all apps × all sheets, like `test_docs_persona_neutral` but over emitted analyses) would catch future leaks — queue it.
- [ ] AO.4 - (feedback #3 · MAJOR 3/4) Today's Exceptions count integrity: KPI 828 vs footer "1–50 of 935"; not sorted by magnitude (multi-million `ledger_drift` buried under magnitude-0 rows); rows span multiple days despite "today." Fix the count source + default sort (magnitude desc) + "today" scoping. (Magnitude-0 rows themselves = seed residual, AO.S1.)
- [ ] AO.5 - (feedback #5 · MAJOR 2/4) Executives "Average Daily Volume" ≈67× off vs total ÷ active-days — avg calc bug (wrong denominator / window). (Time-axis "outage + gap" = short seed window + weekends → AO.S2 annotate.)
- [x] AO.6 - (feedback #6 · MAJOR 1/4) L2 Exceptions "count" overloaded — "Open L2 Violations = 39" directly above a detail column showing hundreds. **DONE (code, staged, commit `f582456c`):** the detail table's per-violation magnitude column display_name → "Occurrences" + matching subtitle, so it no longer reads as "Count" colliding with the violation-tally KPI. Cosmetic display label (l2ft json + unit green); no dedicated test (suite-covered). Operator visual-confirms + ships.
- [ ] AO.7 - (feedback #7 · MAJOR) Rail direction mislabel — an ACH credit-side rail tagged `direction = Debit` (name/direction mismatch); real data-classification issue, check leg_direction vs semantics. (Paired "mangled origin string" RESOLVED in the feedback: the `origin` column holds only the two clean values → display/transform artifact or screenshot misread, NOT our data.)
- [x] AO.8 - (feedback #8 · MAJOR 2/4) Chart presentation: per-rail bar-chart x-axis label smear + "stacked by type" not stacking/no legend. **→ DONE via AO.R.2** (App2 BarChart: STACKED + per-series colors + legend + rotated/long-label x-axis + currency y-axis). Operator visual-confirms; the 30-series legend density is flagged under AO.R.
- [ ] AO.9 - (feedback #9 · MINOR multi) Empty-state clarity + Anomalies flag-logic. **Anomalies "0 flagged" → DONE via AO.R.4** (slider int-binding; live 2σ→129 / 3σ→67). **Still open (real dashboard *content*, both renderers — not a render gap):** as-of stamps (Limit Breach "clean vs didn't run"), dollar-exposure context (Unbundled 802 / Supersession), clipped y-axis ("0,000,000"), context for the net "money moved" figure.

  **App2 renderer parity (AO.R — the smell the operator named 2026-05-21: a fix to a shared contract field landed in QS *only*, because App2's projection layer silently drops it).** The v11.9.4 cold-read ran on App2, which has confirmed renderer-parity gaps vs QuickSight: `_data_shape.shape_table` emits `[{"name"}]` only (drops `ColumnSpec.human_name` header + currency `format`); `shape_bar_chart` drops `format` + the JS BarChart has no STACKED/legend/long-label handling though the tree declares `bars_arrangement="STACKED"`; `rt.markdown` renders only links + paragraph breaks (not bold/code/bullets). The JS renderer is already capable (`col.label||col.name`, `formatTableCell(v,col.format)`, `formatKPIValue(...,"currency")`) — the Python projection starves it. So AO.6 (label), AO.8 (charts), and AO.9-anomalies are App2-render gaps where QS is already correct → **fix App2 to honor what the tree already declares; AO.R supersedes the renderer-portion of those items.** Operator decision 2026-05-21: default to *enhancing App2* (it's fully ours) + a parity drift gate so a QS-only contract field can't ship silently again. One branch: `ao-app2-renderer-parity`.
- [x] AO.10 - Fix ORA-00932: AO.2 date_trunc_day on ISO string literals breaks Oracle (release blocker)
- [x] AO.11 - Audit doc: unify the date/range/anchor model across apps, datasets, seed, trainer, App2
- [x] AO.R - App2 renderer parity cluster — make App2's projection honor the contract/tree the same way QS does (single branch)
  > **DONE (2026-05-21, branch `ao-app2-renderer-parity`, 6 commits — NOT merged/tagged; operator visual-confirms + ships).** All 5 children shipped + each live-verified on the SQLite dashboards server (`recon-gen dashboards -c /tmp/config.sqlite.yaml --port 8766`):
  > - **R.1** table headers/currency (`12f1d154`): L2FT detail table now reads "Check Type / Entity A / … / Occurrences" (the AO.6 label finally lands in App2), money cols `$`-formatted. **R.5** parity gate (`test_html_table_parity.py`) asserts it across all 4 apps.
  > - **R.3** rich-text (`bf8a64e3`): `rt.markdown` now parses `**bold**` / `` `code` `` / `- bullets` / `> quote` → the L2FT bottom panel renders a real `<ul>` + bold leads (was raw markdown in BOTH renderers). Strengthened `test_text_box_safety.py` to reject surviving `**bold**`.
  > - **R.2** charts (`802289bf` + `88c6b576`): App2 BarChart now stacks (`bars_arrangement="STACKED"` honored end-to-end: wrap_for_visual projects the colors dim → shape pivots to series → d3 stacks), per-series colors + legend, rotated x-labels, currency y-axis. Exec daily-stacked bar emits 30 series + stacked + format live. **FLAG (operator):** 30 rails = a 30-segment bar + 30-row legend (dense); composition now renders vs. the prior flat total — consider an "other"-bucket rollup for dense instances.
  > - **R.4** slider binding (`c8099b37`): integer/decimal slider URL values now coerce to numeric → moved-slider no longer reads "0 flagged". Live: σ-anomaly KPI default→129, σ=2→129, σ=3→67, σ=4→53 (matches the cold-read's 129@2σ / 67@3σ). Fixes every numeric slider (also resolves **AO.C2** for App2; AWS still its own confirm).
  > - **DISCOVERY:** R.4's symptom was a *distinct* bug from the cold-read's (the cold-read's "0 flagged at default" was the pre-v11.9.4 default-not-applied path, already fixed by the fetcher wiring; the moved-slider int-binding was the live bug). **Full unit layer green (2904); full non-e2e tree green** (one tests/js poller test flaked under xdist, passes in isolation — not ours).

  - [x] AO.R.1 - App2 table headers + currency format honor the contract
  - [x] AO.R.2 - App2 charts: currency format + STACKED + legible axis labels
  - [x] AO.R.3 - App2 text panels render bold/code/bullets (rt.markdown)
  - [x] AO.R.4 - App2 slider control default bound on initial load
  - [x] AO.R.5 - parity drift gate: App2 table shape carries QS's label+format
- [ ] AO.S - 
  - [ ] AO.S.1 - (feedback #3/#4) Magnitude-0 exception rows + pool `ledger_drift` in the millions = the known plant-residual / independent-leg fan-out modeling. AO.1's integer-cents math clears the float-noise portion; decide fix-seed-to-reduce-noise vs annotate-as-intended for the rest. NOTE: feedback #4's "child postings don't roll up to parent / no working drill" IS a real dashboard concern (the dead-end drill depends on AO.2).
  - [ ] AO.S.2 - (feedback #5) Exec time-axis multi-week empty stretch + weekend gaps = the deliberate short seed window — annotate the chart so it doesn't read as an outage.

    - [x] AO.S2.a - Trainer timeline determinism: scenario-end date (`window_end`, plant anchor) is DISTINCT from load-up-to (`up_to`/`end_date`, the trainer's scrub head — load early for good days, advance to reveal the issue). Tests passed 5/21, broke 5/22 (window_end floated on wall-clock today). Fix: pin window_end explicitly in the two timeline tests (cache default stays today for the live trainer); + regression test that plants stay fixed at window_end across different up_to values. (`ao-oracle-release-fix`)
- [ ] AO.C1 - (feedback #10) App Info shows `dialect: sqlite` / dev prefix — fine for the dev capture; confirm a production deploy shows the real engine + prefix.
- [ ] AO.C2 - (feedback #9) Empty-default AML sliders (Fanout, Anomalies) — confirm render-on-default vs broken on AWS (likely the slider-default class; pairs with AO.9).

## Phase AS - invariant spine (D6) *(depends on: AR)*

The destination: invariant as single source of truth, with generators + views referencing it. Biggest lift; AS.0 re-plans the decomposition from the spike findings first (the layer most likely to redecompose).

**Learnings carried in from AQ/AR (info; AS.0 absorbs into the rollout plan, not pre-baked into leaves):**
- *Many-to-many invariant↔generator wiring is real.* AP.3 finding: a `drift` plant trips both `drift` AND `ledger_drift` detectors; `xor_variant_missed_firing` + `xor_variant_overlap` plants both trip the single `xor_group_violation` detector. The taxonomy unification (AS.2) needs an explicit relation, not a direct rename.
- *Substitution-path divergence is a deploy-time hazard.* AR.5's bite: "one value emitted everywhere" wasn't enough — QS bridge substitutes typed values, api/smoke substitutes string literals; PG can't accept both shapes with one SQL function. For every promoted Invariant whose `detect()` crosses SQL-pushdown surfaces, expect to add an api-layer smoke test covering BOTH substitution shapes (typed value vs string literal).
- *Four window kinds, not three.* The audit named three (invariant-derived / data-deadline / subjective-view); AR.4 surfaced a fourth: **no-narrowing** (L2FT's static "show all" sentinels). The taxonomy work should account for it.
- *Type-the-binding-then-funnel works.* AQ's pattern (`LOCKED_ANCHOR` constant + `locked()`/`live()` factories + funneled call sites; suppressions disappear as a side-effect) is the template for AS.1's promotion.
- *In-process SQLite harness is load-bearing.* AP.3's pattern (`emit_schema` + `_register_sqlite_aggregates` + real matview SQL, no DB server) made AR's design confidence cheap. Every promoted Invariant should self-validate this way before its production wiring.
- *Honest limits become the actual blocker.* AR.5's "honest limit" (substitution paths) was the live regression. AP.2's honest limit is **cross-account vector state** — AS.4 is therefore the highest-exposure leaf in this phase.

- [x] AS.0 - Plan/spike the spine rollout decomposition (lock the `src/` home + the taxonomy migration order before building)
- [x] AS.1 - promote `Violation` / `Invariant` / `ViolationGenerator` / `View` types to `src/`
- [x] AS.2 - unify the fractured taxonomy: `PlantKind` (20) ⋈ `check_type` (~10 untyped) → one closed `Violation` taxonomy; total `invariant→{generators,views}` maps, exhaustiveness-checked (data/deadline windows stay invariant-owned)
- [x] AS.3 - generator = stateful fold carrying `(balances, active-violation-set)`; `Invariant.scenario_for(shape, selector)`; non-violating = perturbation off
- [x] AS.4 - cross-account VECTOR state (AP.2's honest limit): legs net to zero across accounts; `ledger_drift` parent rollup; cross-boundary propagation
- [x] AS.5 - retire byte-locked seed SQL → semantic self-validation (`detect(gen) ⊇ intended`) replaces byte-identity
- [x] AS.6 - **MANDATORY GATE** — 4-way agreement + `TestScenarioCoverage` become the runtime linkage assertion over the spine. The bridge between in-process semantic correctness and live-rendered correctness; AR.5 proved this layer is where deploy-time divergence surfaces, so this is non-skippable, not polish.
- [x] AS.7 - training/docs scenarios self-validated (can't lie / can't silently fail to demonstrate)

## Phase AU - L1 invariant composition (second + more L1 violation types) *(depends on: AS)*

AS piloted ONE invariant (drift) end-to-end through the framework. AU proves the
framework SCALES: adds at least one more L1 invariant + a composition scenario that
exercises multiple distinct generators in one `LedgerSimulation`, verifying each
`Invariant.detect` picks up its own violations + the carried-set tracks all without
interference. **The honest limit AS leaves open** — "does this work for ONE
invariant or for a SPINE of them?" — only resolves under composition. AU is parallel
to AT (both depend on AS, can land independently); AU finishes the L1 surface while
AT crosses into L2's distinct complexity classes.

The promotion order's set by AU.0; the cleanest pilot is **overdraft** (simplest
L1 after drift — negative-balance check, structurally distinct from drift's
arithmetic, single-row witness, no instance coupling). `limit_breach` deferred to
near the end because of its instance-coupled `from_instance` smart constructor
(AP.3 finding #4 — the disproof of the "blind generator").

**AU.0 learnings (info, not prescription — what the overdraft spike taught us):**

- **Many-to-many edges are universal, not drift-specific.** Spike was written
  predicting overdraft as the "structural inverse" of drift (single-row witness,
  no edges to other detectors). First run FAILED on the no-edge claim: an
  overdraft planted on a LEAF internal account ALSO trips `DriftInvariant`,
  because drift's matview filter `parent_role IS NOT NULL` AND `stored ≠ Σ legs`
  is satisfied by the overdraft plant (leaf has parent_role; emission has
  zero transactions, so Σ legs = 0 ≠ −magnitude). **Every new
  `ViolationGenerator` lands with an empirical multi-matview detect-sweep
  check, NOT a structural prediction of "only my own invariant fires."**
- **`scenario_for` discipline is per-invariant.** Overdraft's matview has no
  leaf/parent filter (any internal account can overdraft), so overdraft's
  smart constructor accepts ANY internal account with the requested role —
  drift's `parent_role IS NOT NULL` filter does NOT transfer. The minimal
  Protocol shape (no shared base class) is the right call.
- **Substitution-path checklist extends cleanly.** Overdraft's `detect()` reads
  the matview via a static SQL with no `<<$param>>` — zero AR.5 risk, same as
  drift. AU.1 inherits the property-test pattern.
- **Promotion-order locked from cost-driver analysis** (full table in audit §5
  "AU.0 result"): overdraft (LOW) → expected_eod_balance_breach (LOW) →
  stuck_pending/stuck_unbundled (MEDIUM, time-window'd) → limit_breach (HIGH,
  instance-coupled per AP.3 finding #4).

- [x] AU.0 - Plan/spike: pilot Overdraft (next-simplest L1) end-to-end through
  the AS framework. Lock the promotion-order for the remaining L1 invariants from
  what the AS drift rollout taught us about each step's cost.
- [x] AU.1 - `OverdraftInvariant` + `OverdraftGenerator` promoted to
  `common/spine/`. Register the edges; per-invariant substitution-path property
  test (AR.5 lesson encoded — every promoted detector ships with one).
  Landed: `src/recon_gen/common/spine/overdraft.py` (mirrors `drift.py`'s shape:
  `ClassVar[str]` name, frozen dataclass invariant, module-private helpers);
  registry gains `OverdraftGenerator: (OverdraftInvariant, DriftInvariant)`
  (the AU.0 empirical edge); `__init__.py` re-exports. `tests/unit/
  test_spine_overdraft.py` (15 tests) pins detect, scenario_for, the
  empirical drift edge on leaf accounts, the parent-role variant's no-edge
  behavior, registry helpers, and the substitution-path property. AS-era
  `test_generators_for_reverse_lookup` updated for DriftInvariant's now
  two-generator reverse-lookup.
- [x] AU.2 - **Composition test** — scenario combining `DriftGenerator` +
  `OverdraftGenerator` via `apply_scenario(*emitters)` (AS.5's existing
  primitive; PLAN's "in one `LedgerSimulation`" was imprecise — that
  composes `AccountSimulation`s, not arbitrary generators). THREE invariants
  fire on the leaf-overdraft variant (overdraft + drift on TWO accounts +
  ledger_drift on drift's parent); the carried set tracks all three without
  masking. AU.2 findings (caught mid-write by the stop-and-evaluate cadence,
  per `feedback_aws_research`-style empirical discipline): (1) lone
  parent-role overdraft trips ONLY overdraft, NOT ledger_drift, because
  `_computed_ledger_balance` gates on `EXISTS (child_with_parent_role)` —
  AU.0's honest-limit prediction was WRONG; (2) composition-induced edges
  are a real, separate property — drift+parent-overdraft DOES trip
  ledger_drift on both parents (drift's child supplies the prerequisite);
  (3) registry semantics stay per-generator (AU.1's two-edge entry stands);
  composition-induced edges are scenario-level, not class-level. AU.5 needs
  a dual-axis exhaustiveness gate (per-generator AND per-invariant via
  composition). Landed: `tests/unit/test_spine_au2_composition.py` (6 tests).
  Audit subsection "AU.2 result" captures the lessons.
- [x] AU.3 - Promote the data/deadline-derived L1 invariants
  (`expected_eod_balance_breach`, `stuck_pending`, `stuck_unbundled`). Each carries
  a window that's PART of the invariant definition (per audit §5 "second source"),
  not subject to the AR view primitive's empty-behavior. Landed as three
  sub-leaves AU.3.a/b/c, each with its own `<invariant>.py` + tests +
  registry edge: ExpectedEodBalanceGenerator (two-edge to drift via the
  AU.0 leaf-overdraft pattern), StuckPendingGenerator (single-edge; first
  L2-coupled + first transaction-based + first wall-clock invariant),
  StuckUnbundledGenerator (single-edge twin of stuck_pending with disjoint
  Posted+bundle_id-NULL filter). TZ convention captured as memory
  (`[[project-local-tz-convention]]` — application uses LOCAL `datetime.now()`
  per Oracle WITH-TIME-ZONE limitation; SQLite tests absorb UTC skew via
  ±12h overshoot windows). Invariant Protocol docstring augmented with the
  scenario_for(instance=None) de-facto convention pending the
  spike-before-locking decision on a formal `from_instance` Protocol
  method (deferred to post-AU.4).
- [x] AU.4 - Promote `limit_breach` — the instance-coupled case. AP.3 finding #4:
  the `from_instance` smart constructor reads the L2's `LimitSchedule` (no blind
  generator possible). The `(parent_role, rail, direction) → cap` table threads
  through here. Landed `src/recon_gen/common/spine/limit_breach.py` with the
  smart constructor reading `LimitSchedule.cap` AS a load-bearing input to
  the plant amount (cap + overshoot). Both Outbound (Debit, negative money) +
  Inbound (Credit, positive money) variants exercised. Single-edge registry
  entry confirmed empirically (Posted leg, no balance row ⇒ no drift JOIN
  match ⇒ no drift fire — same shape as stuck_unbundled). 16 unit tests
  in `tests/unit/test_spine_limit_breach.py`. **Protocol-enhancement
  decision point** (second L2-coupling data point, deferred from AU.3.b):
  the existing `scenario_for(<selectors>, *, instance=None)` convention
  SCALED to limit_breach without strain. AT.2 (windowed anomaly) is the
  next decision gate.
- [x] AU.5 - **Dual-axis exhaustiveness gate** (refined by AU.2 finding #4):
  - **Per-generator-class** (original scope): every L1-related `PlantKind`
    value has ≥1 registered `ViolationGenerator`; every L1 `check_type` has
    ≥1 registered `Invariant`.
  - **Per-invariant-class** (the AU.2 addition): every L1 `Invariant` has
    ≥1 SCENARIO (single-generator OR composition) that empirically trips
    it. Composition-induced edges (e.g., overdraft+drift → ledger_drift on
    overdraft's parent) are part of the gate; AU.5 must enumerate
    composition scenarios, not just generator classes.
  The taxonomy unification (AS.2) gets its empirical guarantee here.
  Landed: `ALL_L1_INVARIANTS` + `ALL_L1_GENERATORS` registries added to
  `common/spine/registry.py` (explicit, hand-maintained per promotion).
  `tests/unit/test_spine_au5_exhaustiveness.py` (17 tests): parametrized
  per-generator-registered check, per-invariant-has-a-generator check,
  cross-cutting "every promoted invariant fires from a real scenario"
  sweep, internal-consistency checks (no orphan refs). Composition-
  induced edges stay scenario-level (per the AU.2 boundary decision) —
  AU.5 covers per-class wiring only; composition coverage lives in
  test_spine_au2_composition.py. **Phase AU complete (6/6 leaves).**

## Phase AT - L2 invariant spine rollout *(depends on: AS)*

Parallel rollout to AS, scoped to the **L2 (Investigation) surface**: the Investigation
matviews carry the two non-arithmetic complexity classes from AP.3 — windowed-statistical
(`inv_pair_rolling_anomalies` z-score) and recursive-graph (`inv_money_trail_edges`
WITH RECURSIVE walk). AS pilots the arithmetic case (drift); AT extends the proven
spine to the other two classes on the actual Investigation app.

AP.3 already proved the spine holds across all three complexity classes in-process; AT
is the production rollout for the two L2 classes. AT.0 redecomposes from AS's results
(same pattern as AS.0 did from AP's spike findings).

- [x] AT.0 - Plan/spike the L2 spine extension: pilot the windowed-statistical case
  (anomaly) end-to-end through the AS-produced `Violation`/`Invariant`/`ViolationGenerator`/
  `View` shape. Lock the migration order for AT.1-AT.5 based on what AS's L1 rollout
  taught us about each step's actual cost. Landed `tests/unit/test_at0_anomaly_full_spine.py`
  (8 in-process tests). **AT.0 findings**: (1) windowed-statistical case fits the
  existing spine Protocol without change — multi-row baseline-plus-spike emission
  is just a multi-INSERT inside `emit()`; no Protocol surface needed; (2) the
  spike's OUTLIER-EFFECT-ON-MEAN reduces its own z-score — needs ~100 baseline
  points for a 1000:1 spike-to-baseline ratio to clear 3σ (caught mid-spike; 8
  baseline points gave only z≈2.67); (3) View ownership of the σ-threshold (per
  AP.3 finding #3) is unavoidable — for the spike, detect() bakes in `>=3σ`;
  AT.1 + AT.2 move it to a View knob; (4) single-edge to anomaly empirically
  (Posted leg, no balance row ⇒ no drift JOIN match — same shape as
  stuck_unbundled/limit_breach).
- [x] AT.1 - extend the `Invariant` taxonomy with the two L2 kinds (`anomaly`,
  `money_trail`); detector shims read the existing Investigation matview rows.
  Landed: `src/recon_gen/common/spine/anomaly.py` (AnomalyInvariant +
  scenario_for + simple AnomalyGenerator promoted from AT.0 spike;
  AT.2 will refactor the generator to use AS.3's AccountSimulation
  stateful-fold base) + `src/recon_gen/common/spine/money_trail.py`
  (MoneyTrailInvariant detector only; AT.3 lands the recursive
  parent-linked generator). 12 + 6 unit tests in
  `tests/unit/test_spine_anomaly.py` + `tests/unit/test_spine_money_trail.py`.
  L2 invariants NOT added to ALL_L1_INVARIANTS — they'll get
  ALL_L2_INVARIANTS / ALL_L2_GENERATORS sibling registries when AT.5's
  L2-side exhaustiveness gate lands.
- [x] AT.2 - **σ-threshold View knob** (AP.3 finding #3 lock). New `AnomalyView`
  spine primitive owns `sigma_threshold` (default 3.0); detector returns ALL anomaly
  rows, the View slices on threshold via the `BUCKET_LOWER_BOUNDS` map. Anomaly's
  `detect()` is now threshold-free + projects every bucket; 16 new tests in
  `tests/unit/test_spine_anomaly_view.py` pin defaults, monotonicity, full
  (threshold → buckets) table, matview-vocab drift guard, defensive shapes.
  Investigation dataset already projected every bucket — no app touch needed.
  **AT.2 decomposition decision (2026-05-23)**: the originally-planned
  "fold AnomalyGenerator onto AS.3 stateful simulator" doesn't fit — anomaly is
  fundamentally pair-shaped (one day, many pairs), `AccountSimulation` is
  single-account multi-day; the natural shape is `LedgerSimulation.transfers` which
  is documented as AT.3's primitive. AT.2 keeps the View-only scope; AT.3 lands
  `Transfer` once + refactors both `AnomalyGenerator` (consumer 1) +
  `MoneyTrailGenerator` (consumer 2).
- [x] AT.3 - **`Transfer` primitive on `LedgerSimulation`** + two-consumer refactor.
  Extended `LedgerSimulation` with `transfers: list[Transfer]` (TransferLeg per
  account with signed amount + shared `transfer_id` + optional `parent_transfer_id`
  for chains, rail_name + status + origin + hour). Refactored `AnomalyGenerator` —
  baseline + spike pairs now build through `_transfers() -> list[Transfer]` and emit
  via a transfers-only LedgerSimulation (single-edge property preserved: no balance
  rows → no drift trip). Landed `MoneyTrailGenerator` for parent-linked chain
  emission (each hop's recipient = next hop's sender; chain_length controls depth;
  consecutive days for posted_at ordering) + `MoneyTrailView(min_depth=0)` for the
  depth-threshold knob mirroring AnomalyView's σ pattern. 16 Transfer-primitive
  tests + 20 money_trail (generator + view) tests; AnomalyGenerator refactor is
  shape-preserving (all AT.2 tests still pass).
- [x] AT.4 - retire L2 byte-locked seed SQL → semantic self-validation extends to the
  Investigation matviews (parallel to AS.5 for L1). Landed
  `tests/unit/test_spine_at4_l2_semantic_lock.py` (11 tests): per-scenario stability
  for anomaly + money_trail alone, per-invariant lock keying, cross-class composition
  (anomaly + money_trail both fire in one scenario without masking), single-edge
  property preserved post-AT.3 refactor (no L1 trip from L2-only plants), gate-has-
  teeth checks (different spike magnitudes / chain lengths → different locks),
  L1-L2 cross-layer composition (drift + anomaly), View-sliced lock stability. The
  byte-locked `tests/data/_locked_seeds/spec_example.*.sql` files stay for now —
  they pin the FULL densified 60-day seed (not just L2 violations); their actual
  removal is post-AT.5 (4-way agreement gate) when there's an alternative
  source-of-truth for the seed shape itself. AT.4 establishes the pattern that the
  4-way gate will compose.
- [x] AT.5 - **MANDATORY GATE** — 3-way agreement on the Investigation dashboard
  (the L2 sibling of AS.6 for L1). Cross-tool linkage assertion that spine /
  QS / App2 / direct-DB agree on L2 violations. **3-way not 4-way per AT.5.d**:
  audit PDF stops at L1 by design; L2 has no PDF leg. **Decomposed 2026-05-23**
  into per-leg sub-tasks. All subtasks landed; verified GREEN against live PG.
  - [x] AT.5.a - **Spine ⋈ direct-matview agreement** (the 5-way bridge for L2).
    Extended `tests/e2e/test_spine_live_agreement.py` with AnomalyInvariant +
    MoneyTrailInvariant tests; per-invariant key projection (anomaly:
    sender/recipient/window_end/z_bucket; money_trail: root/transfer/depth).
    Verified GREEN against live PG (RDS database-2 / qsgen_postgres prefix)
    post deploy + seed + refresh — all 4 tests in the file pass (drift,
    ledger_drift, anomaly, money_trail). The 5-way bridge for L2 is live;
    AT.2's "detector returns every bucket" contract validates against the
    matview's unfiltered row set, no manual filter to keep in sync.
  - [x] AT.5.b - **App2 Investigation dashboard ⋈ direct-matview agreement**.
    Add `_dashboard_extract` projections for anomaly + money_trail tables;
    App2 leg (no AWS infra needed).
  - [x] AT.5.c - **QS Investigation dashboard ⋈ direct-matview agreement**.
    Heaviest leg. AWS QS deploy of Investigation + InvestigationDriver
    projections.
  - [x] AT.5.d - **Investigation PDF section ⋈ direct-matview agreement** —
    **NOT APPLICABLE** (decision recorded 2026-05-23 after spike). The audit
    PDF stops at L1 (drift / overdraft / limit_breach / stuck_* / supersession
    + daily statement walks); anomaly + money_trail surface only on the
    Investigation dashboard. Different audience by design: PDF is for the
    regulator + accounting-trail invariants, Investigation is for the AML
    analyst + fraud-pattern detection. AT.5 gate composes 3 renderers (spine
    ⊆ direct_matview == App2 == QS), not 4. L1's PDF leg remains; L2's gate
    is intentionally 3-way.
  - [x] AT.5.e - AT.5.e — Compose parametrized `test_inv_three_way_agreement`
    (or sibling test for L2). Cross-renderer assertion in one place. (3-way
    not 4-way per AT.5.d's decision — no PDF leg.)
  - [x] AT.5.f - **Scenario-plant lower-bound counts** — extend
    `expected_audit_counts` to include anomaly + money_trail.
- [ ] AT.6 - L2 training/docs scenarios self-validated (anomaly + money_trail scenarios
  can't silently fail to demonstrate; parallel to AS.7).

## Phase AV - Rename `daily_balances.limits` → `daily_balances.metadata` (with `limits` as a nested key)

**Recovered 2026-05-23** from `scenario-context-spike` branch — the planning
was lost from `main`'s PLAN.md when the spike branch diverged. Original
surfaced during the `tests/unit/test_scenario_context_spike.py` exploration
of `ScenarioContext` (composition safety). Spike found that `_daily_balances`
has no `metadata` column today (only `limits` JSON for per-rail caps);
`_transactions` has both. Per-row scenario-tagging — the clean shape for
"make sure they don't step on each other" composition safety — needs metadata
on BOTH tables.

User-proposed clean fix (2026-05-23, vs. adding a separate metadata
column): **rename the existing `limits` column to `metadata`, with
`limits` becoming a key inside that JSON** (`metadata.limits = {...}`).
Same JSON column, restructured. No new column; the existing storage +
indexes carry through. As a side effect, the spike's side-table approach
(`<prefix>_scenario_claims`) goes away — once both base tables carry
`metadata`, scenario tagging happens per-row.

Why this needs its own phase (the user's exact reason — *"without a
strong phase will get lost and bite us"*):
- **Schema change** — every dialect's CREATE TABLE shape changes; every
  CHECK constraint referencing `limits` (e.g. the JSON validity guard)
  updates.
- **ETL contract change** — customer ETL feeds populate this column;
  the wire shape changes.
- **Locked seeds** — `tests/data/_locked_seeds/<instance>.<dialect>.sql`
  files contain `limits` column literals; re-lock per dialect.
- **Every reader** — every dataset SQL + matview SQL + Python helper
  that reads `limits` must update to read `metadata.limits` via the
  dialect-portable `JSON_VALUE(metadata, '$.limits')` (or equivalent).
- **Phase AV is the prereq** for promoting the `ScenarioContext`
  mechanism with per-row metadata tagging on both tables. Until AV
  lands, the scenario-context work would need the side-table approach
  (the spike pattern); landing AV obviates the sidecar entirely.

- [ ] AV.0 - Audit + spike: inventory every reader of `daily_balances.
  limits` (matview SQL + dataset SQL + Python helpers + tests). Map each
  to its `metadata.limits` migration path. Output `docs/audits/av_0_
  limits_metadata_rename_audit.md` with the punch list. Lock the
  migration ordering (schema → ETL contract → matview/dataset SQL →
  Python helpers → tests + locked seeds → docs). Also inventory the
  `scenario-context-spike` branch's `<prefix>_scenario_claims` sidecar
  and confirm AV's per-row tagging path eliminates the sidecar (per
  user 2026-05-23: "this should also remove the sidecar table we've
  gained for the scenario planting tagging").
- [ ] AV.1 - Schema change: rename column, update CHECK constraints,
  emit per-dialect. Includes the JSON-validity guard rename. No data
  semantics change — `metadata.limits` carries what `limits` carried.
- [ ] AV.2 - Update every matview SQL + dataset SQL to read
  `metadata.limits` via the dialect-portable `JSON_VALUE` form. Includes
  L1's expected_eod_balance_breach JOIN + the L2FT dashboards that read
  per-rail caps. Verify by re-running the full suite + 4-way agreement.
- [ ] AV.3 - Update Python helpers + tests (including `tests/data/
  _locked_seeds/`). Re-lock seeds per dialect (the column literal in
  the locked SQL changes).
- [ ] AV.4 - Bump version (post-v?.?.?) + RELEASE_NOTES entry. Migration
  warning ≥1 minor version for downstream operators with custom ETL.
- [ ] AV.5 - (After AV lands) Promote `ScenarioContext` mechanism from
  spike to `src/recon_gen/common/spine/scenario_context.py` using
  per-row metadata tagging (replacing the spike's side-table approach).
  Updates every existing generator (drift / overdraft / expected_eod /
  stuck_pending / stuck_unbundled / limit_breach / anomaly / money_trail
  as they exist at AV-land time) to expose `claimed_accounts` +
  thread `scenario_id` into metadata on emit. Decouples from the
  side-table bridge.

## Phase AW - Own the temporal frame + cfg/L2 in the DB (`<prefix>_config`)

**Surfaced 2026-05-23** during AU.3.d hoist work. User caught the spine's
`datetime.now()` calls in `stuck_pending.py` + `stuck_unbundled.py` as
an uncontrolled dependency violating AR's "own the temporal frame"
principle (audit §6). Root cause is upstream — the matview SQL itself
uses `CURRENT_TIMESTAMP` / `julianday('now')` for the
`age_seconds = NOW - posting` computation, and the spine generators
have to follow the matview's wall-clock to keep tests deterministic.

User-proposed design (the ONLY allowed relaxation of the "two-table
rule" because the new table is DERIVED from cfg+L2, never
operator-mutated, mirrors the source YAML 1:1):

```sql
CREATE TABLE <prefix>_config (
    as_of    TIMESTAMP   NOT NULL,
    cfg_yaml {json_text} NOT NULL,
    l2_yaml  {json_text} NOT NULL
);
```

**Operational model** (the two-event split user explicitly confirmed):

- **Deploy** (cfg.yaml or L2.yaml changes): Python reads yamls,
  serializes to JSON, REPLACEs the config row. Initial as_of populated
  (CURRENT_TIMESTAMP or pinned per cfg). Matviews refresh.
- **Daily ETL** (data load, cfg unchanged): customer ETL fills
  `_transactions` + `_daily_balances`; refresh helper runs
  `UPDATE config SET as_of = CURRENT_TIMESTAMP; <refresh matviews>;`.
  The cfg/L2 blobs stay untouched. The matview's subquery picks up the
  new as_of.

**Spike findings (both green; design locked):**

- `tests/unit/test_aw0_matview_as_of_spike.py` (AW.0, 5 tests) —
  validated the subquery-in-matview mechanism: SQLite's `julianday(...)`
  accepts scalar subquery; matview body stays stable across refreshes;
  same plant + varied as_of → varied `age_seconds` → varied fire
  status; plant + matview reading from one as_of source eliminates
  wall-clock skew.
- `tests/unit/test_aw0b_jsonpath_filter_spike.py` (AW.0.b, 7 tests) —
  SQL/JSON portability: SQLite does NOT support filter-path syntax
  (`$.arr[?(@.name == "X")]`); PG 12+ and Oracle 12c+ do. Portable
  workaround for SQLite: `json_each(...) + WHERE` + LEFT JOIN against
  the matview's main FROM. Composes cleanly into matview SELECTs. The
  dialect-switch helper in `common/sql/dialect.py` renders the right
  shape per backend.

**Storage shape locked: JSON blobs + typed `as_of` sibling column**
(vs key-value rows). The L2 yaml has deep multi-valued structure
(rails[], chains[], limit_schedules[]); flattening to KV would be
either lossy or require JSON-in-values. JSON mirrors the source
1:1; matches existing project patterns (transactions metadata, the
AV-renamed daily_balances.metadata, Investigation matviews' JSONPath
usage); supports the WIDE-access cases (limit_breach iterates ALL
LimitSchedules in one fetch).

**Migration scope** (the user accepted "I'm sold on the migration
either way"):

| Currently baked at emit-time | After AW |
|---|---|
| `{epoch_age_seconds}` → `EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - posting))` | `EXTRACT(EPOCH FROM ((SELECT as_of FROM <prefix>_config) - posting))` |
| `{pending_age_cases}` (per-rail CASE) | LEFT JOIN config + `json_each(l2_yaml, '$.rails')` + WHERE on name |
| `{unbundled_age_cases}` | same shape |
| `{limit_cases_outbound}` / `_inbound` | LEFT JOIN config + `json_each(l2_yaml, '$.limit_schedules')` + WHERE on (parent_role, rail, direction) |
| `{rolling_window}` (anomaly's hardcoded 2 days) | could move to cfg in a follow-on |
| Every L2-derived literal | reads from `<prefix>_config` JSON via dialect helper |

Cleanness payoff: matview bodies become **persona-blind** — no per-L2
literals; same SQL across spec_example, sasquatch_pr, every future L2.
Operator can introspect: `SELECT JSON_VALUE(l2_yaml, '$.rails[*].max_pending_age') FROM <prefix>_config`.

**Phase relationship to other phases:**
- **Blocks AU.5** (the dual-axis exhaustiveness gate composes many
  generators; needs the deterministic as_of). AU.3.d hoist resumes
  after AW lands.
- **Blocks AT.2** (windowed anomaly may want to read its threshold
  from cfg too).
- **Synergy with AV** (limits→metadata rename): both touch matview
  literals; AV's rename moves the `limits` JSON to `metadata`; AW
  ALSO reads from JSON. Probably want AV merged in or just-after AW.

- [x] AW.0 - Spike: validated runtime-table-subquery mechanism (5 tests
  in `tests/unit/test_aw0_matview_as_of_spike.py`). Pivoted under user
  feedback: instead of `_runtime` (as_of-only), use `<prefix>_config`
  (cfg + L2 yaml + typed as_of) — the ONE allowed relaxation of the
  two-table rule. Spike's mechanism (subquery in matview body) carries
  over directly to the bigger design.
- [x] AW.0.b - Spike: SQL/JSON portability across PG/Oracle/SQLite
  (7 tests in `tests/unit/test_aw0b_jsonpath_filter_spike.py`).
  Finding: SQLite doesn't support filter-path syntax; portable shape is
  `json_each() + WHERE` + LEFT JOIN. Dialect helper switches per backend.
- [x] AW.1 - Schema: emit `<prefix>_config` table at init; populate with
  cfg + L2 as JSON + initial as_of. Drop/recreate handling for re-deploy.
  Python helpers: `replace_config(conn, cfg, l2, as_of)` for deploy
  events, `set_as_of(conn, as_of=None)` for refresh events (None →
  CURRENT_TIMESTAMP). Landed `src/recon_gen/common/l2/config_table.py`
  (DDL emission + `replace_config` / `set_as_of` / `get_as_of` helpers
  taking pre-serialized JSON strings — caller does the dataclass→JSON
  conversion). `emit_schema` + `emit_schema_drop_sql` integrated with
  the new table (drop before base, create after, symmetric teardown).
  15 unit tests in `tests/unit/test_aw1_config_table.py` pin DDL shape,
  table-name convention, helper round-trip, single-row invariant under
  re-replace, CURRENT_TIMESTAMP default, full-schema integration. Bridge
  typing-smell suppressions added to stuck_pending.py + stuck_unbundled.py
  for `datetime.now()` (the bridge until AW.5 retrofits generators to
  read from `<prefix>_config.as_of`).
- [x] AW.2 - Migrate `{epoch_age_seconds}` substitution to read as_of
  from `<prefix>_config`. PG/Oracle uses `EXTRACT(EPOCH FROM ((SELECT
  as_of FROM ...) - posting))`; SQLite uses the subquery-in-julianday
  shape. Update `common/sql/dialect.py::epoch_seconds_between`
  signature to accept the as_of expression. Landed: schema.py call site
  changed (the helper signature didn't need updating — it already took
  arbitrary expression strings; the call site just passes
  `f"(SELECT as_of FROM {p}_config)"` instead of `"CURRENT_TIMESTAMP"`).
  stuck_pending + stuck_unbundled test `_fresh_db` helpers seed the
  config row with `datetime.now()` as the as_of bridge (typing-smell
  suppression added; AW.5 retrofits to LOCKED_ANCHOR). Pre-existing
  shape-asserting tests in `tests/schema/test_l2_schema.py` updated
  for the new SQL shape. Full prelude: 3139 unit tests pass.
- [x] AW.3 - Migrate `{pending_age_cases}` + `{unbundled_age_cases}` to
  read from `<prefix>_config.l2_yaml` via JSON path. Landed with two new
  dialect helpers in `common/sql/dialect.py`: `json_array_iterate` (LEFT
  JOIN clause iterating a JSON array — SQLite uses `json_each`; PG 17+
  + Oracle 12c+ use SQL/JSON-standard `JSON_TABLE`) and
  `json_field_extract` (per-row field extract — SQLite `json_extract`,
  PG/Oracle `JSON_VALUE`). Matview templates simplified: emit-time CASE
  branches gone (`_render_pending_age_cases` + `_render_unbundled_age_cases`
  deleted); matview body is now persona-blind (same SQL across all L2s).
  Real portability catch: first iteration used PG's `jsonb_array_elements`
  with `::jsonb` cast; project CLAUDE.md rule bans JSONB (caught by
  pre-existing `assert "JSONB" not in _strip_comments(sql).upper()`).
  Pivoted to SQL/JSON-standard JSON_TABLE on PG (requires PG 17+ which
  the project already targets). Test config-row JSON expanded to carry
  the rails the matviews iterate. Shape-asserting tests in
  `tests/schema/test_l2_schema.py` updated for the new JOIN form
  (`FROM <prefix>_config` + `JSON_VALUE(rail.value, '$....')` instead
  of `WHEN ct.rail_name = 'X' THEN N`).
- [x] AW.4 - Migrate `{limit_cases_outbound}` + `{limit_cases_inbound}`
  to the same JOIN-against-config shape. Multi-key filter via the
  dialect helper (parent_role + rail + direction). Landed:
  `_render_limit_breach_cases` deleted; matview LEFT JOINs
  `<prefix>_config.l2_yaml.$.limit_schedules` with 3-key ON clause
  (parent_role + rail + direction='Outbound'/'Inbound'). Cap aggregated
  via `MAX(cap)` since validator U5 guarantees one cap per triple →
  same value across the GROUP BY. New substitutions `limit_join_outbound`
  / `limit_join_inbound` / `limit_cap_value`. tests/unit/
  test_spine_limit_breach.py + test_ap3_invariant_self_validation.py
  `_fresh_db` helpers updated to seed limit_schedules JSON. Shape-
  asserting tests in test_l2_schema.py refactored to assert on JOIN-form
  + the inert-when-empty body uniformity (same SQL regardless of L2
  contents).
- [x] AW.5 - Generators retrofitted — stuck_pending + stuck_unbundled
  drop `datetime.now()`; accept `as_of: datetime` (or read from
  config-via-Python at scenario_for time). Spine tests become
  wall-clock-independent; the ±50_000s TZ-skew overshoots in
  `test_spine_stuck_pending.py` + `test_spine_stuck_unbundled.py`
  shrink to small natural values (e.g. ±60s). The
  `no-datetime-now` typing-smell suppressions drop. Landed:
  `StuckPendingGenerator` + `StuckUnbundledGenerator` gain `as_of:
  datetime` field; `scenario_for` requires it as a keyword arg
  (no default — explicit > implicit). Tests use a pinned `_TEST_AS_OF
  = datetime(2030, 1, 1, 12, 0, 0)` shared between config-seed +
  generator. Overshoots dropped from ±50_000s to ±60s — deterministic
  with no wall-clock skew to absorb. The two typing-smell suppressions
  (`stuck_pending.py:171`, `stuck_unbundled.py:133`) dropped, and the
  bridge `datetime.now()` calls in `_fresh_db` helpers all gone. Full
  prelude: 3139 pass.
- [x] AW.6 - Re-lock seeds per dialect (matview SQL changes for ALL
  dialects). Run full suite + 4-way agreement (PG + Oracle + SQLite +
  PDF) to verify no regression in the dashboard/PDF surface. Document
  performance delta on PG refresh (DROP+CREATE vs JOIN-readingsubquery).
  **No re-lock needed**: the AW matview SQL changes are in
  `common/l2/schema.py`'s emit code; the locked seeds at
  `tests/data/_locked_seeds/*.<dialect>.sql` capture INSERT data only.
  `tests/data/test_locked_seeds.py` 8/8 passes byte-equality post-AW
  with zero re-locking — schema emit is regenerated at every test run.
  Verification ladder run end-to-end on Postgres:
  (a) `./run_tests.sh up_to=db --dialects=pg --targets=lo` — 48 + 48
  tests pass on sp_pg_lo + sq_pg_lo; the matview SQL works against
  real Postgres (LEFT JOIN + JSON_TABLE shape executes; spec_example
  + sasquatch_pr both green); audit PDF render+verify (one leg of the
  4-way) passes. (b) `./run_tests.sh up_to=app2 --dialects=pg
  --targets=lo` — App2 layer green on both variants; the App2
  renderer reads from the new matview shape correctly (App2 leg of
  the 4-way). Not run here: Oracle DB layer (dialect helpers are
  mechanical, JSON_TABLE is SQL-standard; CI exercises this) +
  QuickSight browser layer (heavyweight + costs $; CI's e2e.yml runs
  this on every release).
- [x] AW.7 - Version bump (post-v?.?.?) + RELEASE_NOTES entry. Migration
  warning ≥1 minor version: downstream operators with custom ETL paths
  now need to handle `<prefix>_config` (populate at deploy; UPDATE
  as_of at refresh — or let the recon-gen refresh helper do it).
  Document the operational two-event split (deploy vs daily ETL).
  Landed: version bumped 11.10.1 → 11.11.0 (minor — schema change +
  new operator-facing helpers); `RELEASE_NOTES.md` entry covers
  schema change + matview persona-blind shape + dialect helpers +
  spine generators wall-clock-free + operational two-event split +
  migration warning for custom ETL operators + verification ladder +
  pointer to the unlocked dashboard-pickers backlog. **Phase AW
  complete (7/7 leaves).**

# Backlog (not yet phased)

- **Studio / Dashboards rethink under the post-AW DB-projected
  L2/cfg.** Surfaced 2026-05-23 after AW completed. AW lifted L2 + cfg
  yaml into `<prefix>_config` as DB-resident JSON; matviews now JOIN to
  it for per-L2 values. That changes a bunch of Studio/Dashboard
  questions that deserve evaluation as a unit, not piecemeal:
  - **Studio editing model.** Studio currently writes the L2 yaml file
    only; deploy regenerates the schema + populates the config table.
    Options: (a) yaml stays the only source of truth; deploy projects;
    (b) Studio also UPDATEs `<prefix>_config` on save for "live-reflect"
    semantics; (c) Studio writes the DB; exports back to yaml on
    operator request. AW makes (b) + (c) feasible; whether they're
    desirable is the open question.
  - **"Deployed-vs-edited" Studio view.** Studio could show the
    DB-resident `<prefix>_config` row alongside the WIP yaml edit — a
    "what's deployed vs what you're authoring" diff surface. Useful for
    "this rail's cap was 5000 in prod; my edit sets it to 7000."
  - **Dashboard pickers from L2 yaml** (the originally-queued item):
    pickers that show L2-DECLARED values (vs dataset-derived which
    shows whatever's in the data); pickers that JOIN to L2 metadata
    (descriptions, types, classifications); pickers that survive
    deploys without re-emitting JSON. Caveat: requires changing
    dashboard JSON's filter shape from `StaticValues` to
    `LinkToDataSetColumn`.
  - **Cross-tabular L2 context in dashboards.** Per-rail
    descriptions / per-account roles surfaced in tooltips, drill
    contexts, etc. — directly JOIN to `<prefix>_config.l2_yaml` from
    the dataset SQL rather than baking at emit time.
  - **Customer ETL access.** Operators / customer pipelines may want
    SQL access to "the same L2 values the matview uses" — DB-resident
    is friendlier than parsing yaml.
  - **Auditability.** `<prefix>_config` could carry timestamps for
    "when did the config last replace?" — Studio could surface "L2
    last updated 3 hours ago" as a deploy-state indicator.
  Scope this as its own evaluation phase (likely audit + spike +
  decision per surface); each Studio/Dashboard surface gets a "use
  DB / don't use DB / hybrid" call. Sized after picking a driver
  goal (analyst-friendliness vs operator ergonomics vs Studio
  iteration speed).

- **Dashboard pickers sourced from `<prefix>_config.l2_yaml` (post-AW
  follow-on).** Surfaced 2026-05-23 during AW.3. Today's pickers come
  from two places: (1) hardcoded option lists baked into the QS dataset
  JSON at emit time (rail-name picker, direction picker, parent-role
  picker — Python reads cfg/L2, emits `StaticValues`); (2) dataset-
  derived `SELECT DISTINCT <col> FROM <dataset>`. AW landed a third
  option: the `<prefix>_config` table is SQL-queryable, so dashboards
  can JOIN to `l2_yaml` for picker options — `SELECT JSON_VALUE(rail.
  value, '$.name') FROM <prefix>_config, JSON_TABLE(l2_yaml, '$.rails
  [*]' COLUMNS (value json PATH '$')) rail WHERE JSON_VALUE(rail.value,
  '$.max_pending_age_seconds') IS NOT NULL`. What this unlocks: pickers
  that show only L2-DECLARED values (vs. dataset-derived which shows
  whatever's in the data); pickers that JOIN to L2 metadata
  (descriptions, types, classifications); pickers that survive deploys
  without re-emitting JSON (cfg row updates; pickers re-read); cross-
  dashboard consistency without re-encoding the L2 in each dataset.
  **Caveat**: requires changing the dashboard JSON's filter shape from
  `StaticValues` to `LinkToDataSetColumn` (or equivalent). Not free
  with AW; this is the dashboard-side migration that exercises AW's
  payoff. *(Sized as its own phase when picker complexity surfaces as
  friction.)*

- **Q.6 — CLI shape revisit: cfg ⇄ L2 dual-yaml factoring.** Surfaced 2026-05-08 during `Y.2.gate.h.6`. The runner reads `cfg.default_l2_instance` and threads `QS_GEN_TEST_L2_INSTANCE` to subprocesses, making the CLI's dual-arg shape (`-c <cfg.yaml> --l2 <l2.yaml>`) partially redundant. Spike-before-implement (per `feedback_spike_before_locking_implementation`); CLI-surface change touches every operator command + doc example + tests.
  - **Q.6.0 SPIKE: combined-yaml vs cfg-with-L2-pointer vs status-quo** (LOCKED 2026-05-08; deferred from PLAN 2026-05-19). Output `docs/audits/y_11_cli_shape_spike.md`. Four candidates: **(A)** status quo + `--l2` defaults from `cfg.default_l2_instance` (smallest delta, mostly additive); **(B)** single combined yaml (eliminates dual-yaml friction but env-only fields co-mingle with institution-flavor — Q.5 separation existed for a reason); **(C)** cfg-with-L2-pointer + `--l2` removed entirely (forces multi-instance operators to duplicate cfg); **(D)** `--l2 <name>` indexed against an `l2_instances:` registry in cfg + `default_l2_instance:` (named ergonomics, one indirection layer). Constraints: (1) no-args `json apply --execute` deploys the default L2; (2) multi-L2 operators don't copy cfg files; (3) existing `--l2 <yaml>` keeps working or has a documented migration; (4) doc examples shrink; (5) tests pass without env-var passthrough. Likely outcome: A or D — A smallest delta, D cleanest if multi-L2-per-cfg becomes common.
  - **Q.6.1–Q.6.3 implement per spike result.** Updates `cli/{json,schema,data,audit}.py`, `cli/_helpers.py::resolve_l2_for_demo`, every CLAUDE.md / README / handbook example, every `runner.invoke([..., "--l2", ...])` test, every CI workflow YAML that uses `--l2`. Migration warning ≥1 minor version. Sweep memory entries + docs for stale `--l2 <yaml>` refs. Update CLAUDE.md "Commands" block to show the new shape as canonical; keep explicit `--l2` form as the multi-instance/override sub-pattern.
  - **Q.6.4 bump version (breaking CLI change — post-v9.0.0) + RELEASE_NOTES** (deferred from PLAN 2026-05-19) — entry highlighting the simplification + migration recipe.
- **Dashboards-local L1 dashboard render errors (surfaced 2026-05-10, X.2.g.4 territory, NOT a Y.2.g regression).** With the Y.2.g.2.d pool-lifespan fix landed, `dashboards --app l1_dashboard` starts cleanly + the drift KPI fetches data from the live matview, but other L1 visuals throw render errors in Dashboards (smoke + drift KPI work, broader rendering doesn't). Per-visual coverage in `_tree_fetcher` / `wrap_for_visual` — investigation/L2FT shipped via X.2.g.{2,3} with the same pattern, so the gap is L1-specific visual kinds the renderer hasn't grown arms for yet. Triage: capture the failing visual_ids + renderer error, extend `wrap_for_visual` with the missing arms, mirror the Investigation/L2FT shape. Out of Y.2.g scope (Dashboards visual coverage ≠ pushdown SQL); on the X.2.g roadmap. *(deferred from PLAN 2026-05-19)*
- **CI/release cleanup steps target the wrong scope — `database-2` + QS leak (captured 2026-05-10).** Three related bugs, all "the cleanup ran but cleaned the wrong thing," all functionally harmless (no impact on release publishing / e2e passing) but they leak resources. *(deferred from PLAN 2026-05-19; pick a fix before doing.)*
- **IAM: widen the `rds-start-stop` inline policy → create/destroy on `recon-gen-local` + `Github_e2e_testing`** (`rds:Create*/Delete*DBCluster/DBInstance`, DBSubnetGroup, `iam:PassRole` for monitoring, `ec2:` SG/subnet describe+authorize). *(deferred from PLAN 2026-05-20)*
- **CI: create a fresh DB per run → apply the current locked seed → drop after** (NO snapshot — seeds churn every commit). Runner `up`/`down` lifecycle per `feedback_ephemeral_aws_infra`. *(deferred from PLAN 2026-05-20)*
- **Local scripts: same pattern — own ephemeral DB per local run, pointed at the one standing Enterprise QS account.** *(deferred from PLAN 2026-05-20)*
- **Per-run cleanup verification:** after deploy→teardown, both the DB and any QS resources tagged with the run's `deployment_name` are gone (no orphans). `tests/e2e/test_cleanup_completeness.py` shape. *(deferred from PLAN 2026-05-20)*
- **Verify chain + commit; confirm idle RDS → ~$0 between runs in CE.** *(deferred from PLAN 2026-05-20)*
- **X.10 — Runner: intra-cell layer DAG (deploy starts right after seed).** The per-cell chain `unit → seed_variant → db → app2 → deploy → api → browser` runs strictly serially today, but `db` / `app2` / `deploy` only depend on `seed_variant` — they're siblings. `deploy` is the long pole (~2 min QS async creation); `db` (~45 s) + `app2` (~30 s) fit inside it. Fan `{db, app2, deploy}` with `asyncio.gather` after seed, then gather `{api, browser}` after `deploy` → ~75 s saved per `aw`-target cell (~6 min off a full-matrix run). The `asyncio` plumbing exists (`Y.2.gate.c.6.async`). Sub-tasks: (a) `cell_chain` returns a `{layer: frozenset[deps]}` DAG; (b) `_run_one_variant` topo-sorts + gathers siblings; (c) failure semantics for in-flight `deploy` (boto3 isn't cleanly cancellable — let it finish, report `db` failure, skip downstream); (d) unit tests for the DAG dispatch (topo/sibling-gather/truncation/failure-skips-downstream, mocked layer dispatch); (e) live wall-clock check, pyright, commit; update CLAUDE.md "Commands" chain description.
- **AA.0 Dashboard UX + exception literacy** *(COMPLETE — shipped v10.1.0a1; full plan archived in `PLAN_ARCHIVE.md` → "Phase AA")*. Six follow-ups still queued from that work:
  - **AA.A.10 (stretch) Tree-walk picker→column derivation.** `PickerSpec.column` still hand-mapped; the tree carries the wiring formally (`ParameterControl.parameter → Parameter.mapped_dataset_params → (dataset, dataset_param_name) → SQL <<$p>> site → column`). Either parse the dataset SQL or annotate `DataSetParameter` with a `narrows_column` field at construction. Spike before locking annotation-vs-parse.
  - **AA.A.11 Cross-corpus duplication lint (test ↔ src), paired approaches 1+3.** Every duplicated SQL string between `tests/` and `src/` is a second codebase that can pass while production breaks. Approach 1 = content-based AST lint walking `tests/` for SQL fingerprints + cross-ref `src/`; Approach 3 = provenance lint requiring values in test assertions to come from `import` of src. Both, not either alone. Spike for false-positive rate at length thresholds; allowlist syntax; cheap-enough for unit prelude vs opt-in mode.
  - **AA.A.l2ft rails-inverse.4 Type-encode the `table_rows()` invariant.** `table_rows()` for narrowing-assertion sites is a smell — picker-row-survival is about SQL row count, not DOM visibility. Deprecate `len(table_rows())` for assertion use, or rename to `dom_visible_rows()`.
  - **AA.A.daterange.3 Structural refactor: single DATE_RANGE control.** Replace each sheet's `(DateTimePickerControl from + to + TimeRangeFilter)` triplet with one `FilterDateTimePickerControl(Type="DATE_RANGE")`. Closes "from > to" footgun; aligns L1 / L2FT / Exec with Investigation. **Wall:** L1's multi-dataset-per-sheet model needs a sharing mechanism (Investigation's filter-bound widget binds to ONE filter on ONE dataset). Options: (a) consolidate L1 datasets, (b) one widget per dataset per sheet, (c) QS mechanism driving multiple parameter-bound filters via intermediates. Spike before locking.
  - **AA.A.daterange.4 App2 renderer for widget-bound DATE_RANGE.** Already proven for Investigation; extends to the new L1/L2FT/Exec range controls. Follows .3.
  - **AA.A.daterange.5 Test infra.** `apply_anchor_to_pickers` becomes "set the range to span anchor's date ±1 day" instead of separate from/to. Single picker spec. Follows .3.
- **Model-driven docs (drift reduction).** Headline carried forward; design TBD.
- **Mobile / responsive.** Tailwind handles the layout primitives but no explicit mobile-first design pass. Promote when there's a customer story. Note: dashboards are dense by nature; mobile may always be a worse experience than desktop, regardless of effort.
- **Per-table CSV / XLSX export.** Operators expect "export to spreadsheet" on tables (QS has it). Lower priority than feature parity — punt unless it's a small agent task. The audit PDF already covers the "regulator-ready snapshot" case; spreadsheet export is for analyst self-serve.
- ~~**Fold the biome JS lint into the test runner, like pyright.**~~ *(done, 2026-05-12.)* `conftest.py::pytest_sessionstart` now runs `biome check --max-diagnostics=400` alongside the pyright gate — `biome check` exits non-zero on lint *errors* (e.g. `noInnerDeclarations`) and zero on warnings, so the gate fires before any test collects (`pytest.exit(returncode=2)`); opt out with `QS_GEN_SKIP_BIOME=1`. `biome` is a standalone Rust binary (brew locally; `biomejs/setup-biome@v2` in CI), not an npm/pip package — when it's not on `PATH` the gate skips cleanly (same posture pyright has if it's missing). Bare `pytest tests/`, `./run_tests.sh up_to=unit`, and `ci.yml::test` all enforce it. (Why not a `[dev]` dep like `pyright` / `pytailwindcss`? The Biome project hasn't published an *official* PyPI package yet — in flight at biomejs/biome#8818. The unofficial `biome-js` wrapper bundles the Rust binary like `ruff` does, but ships only a `manylinux_2_28_x86_64` wheel — no macOS / arm64 / sdist, and it's a stale single release on Biome 2.3.x — so adding it would break `uv sync --extra dev` off linux-x86_64. Biome therefore stays a system binary; the `[dev]` block carries a NB comment recording this + a "revisit when biomejs/biome#8818 merges" pointer. `dev_setup`: `brew install biome`, or any of biome's install methods. **Follow-on when biomejs/biome#8818 lands:** add the official package to `[dev]`, drop the `setup-biome` CI step + the system-binary fallback in conftest / install.md.)
- **Drop `_oracle_lowercase_alias_wrapper`; emit dialect-natural identifier case from the generator** (was Y.3.f, parked 2026-05-09). DDL is emitted unquoted (PG folds lowercase, Oracle UPPERCASE → divergent storage); `_oracle_lowercase_alias_wrapper` (`common/dataset_contract.py`) bolts an outer `SELECT qs_inner."ACCOUNT_ID" AS "account_id" ...` so QuickSight (which builds `SELECT "account_id" FROM (...)` from its declared lowercase Columns) finds matching aliases. The proper fix — generator emits dialect-natural case in `DatasetContract.to_input_columns()`, QS quotes UPPERCASE on Oracle natively, wrapper gone — is bigger than it looks: QuickSight's analysis-side validation is *case-sensitive* against `Dataset.Columns` (Y.3.f.2's reverted Oracle-deploy probe surfaced 45 column-missing errors), so it requires case-folding ~30+ analysis-side column refs per dialect (visuals / filters / calc-fields / drills), not just the Columns declaration. The original App2 Oracle column-casing bug it would have fixed was instead fixed narrowly by Y.3.f.alt (`wrap_for_visual` quotes its column refs). Re-spike if the dialect-helper count grows past ~60, or if SQLite gets dropped from the matrix. See `project_qs_analysis_validates_columns_case_sensitive` memory.
