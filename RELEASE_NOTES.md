# Release Notes

## v11.1.0 — AB.1 per-direction limit caps (Outbound + Inbound)

Feature release. `LimitSchedule` gains a `direction` field — every cap is now `Outbound` (default, classic per-rail send cap) or `Inbound` (the AML / structuring-threshold pattern applied to inbound volume). Same `(parent_role, rail_name)` pair may carry both kinds of cap simultaneously; the L1 `<prefix>_limit_breach` matview UNIONs the two branches and emits a `direction` column distinguishing them. **Fully backwards-compatible** — any pre-AB.1 L2 yaml that omits `direction:` defaults cleanly to Outbound, and the serializer only emits the key when non-default, so existing files round-trip byte-equivalent through load+dump.

**L1 invariant — per-direction flow cap.** The matview rewrites from a single SELECT (Debit-only outbound) into a UNION ALL of two branches: Outbound filters `amount_direction='Debit'` and applies Outbound LimitSchedule caps; Inbound filters `'Credit'` and applies Inbound caps. Each branch projects a literal `direction` column ('Outbound'/'Inbound'). New `idx_<p>_lb_direction` index. An L2 with zero Inbound caps stays cleanly empty (typed-NULL cap excludes via the outer `cap IS NOT NULL` filter) — no conditional template surgery.

**Validator U5 broadened.** Uniqueness key is now `(parent_role, rail_name, direction)` triple. Same parent+rail with different directions is fine — that's the point. Two Inbound caps on the same parent+rail still rejected with `direction='Inbound'` in the error message.

**Dashboard wiring (L1 Limit Breach sheet).** Table visual gains a Direction column between Rail and Flow. Sheet subtitle explains the Outbound (send cap) vs Inbound (AML / structuring threshold) interpretation. Today's Exceptions inherits the new Inbound rows automatically since its UNION-over-matviews reads from `<prefix>_limit_breach` unchanged.

**Audit PDF wiring.** `LimitBreachViolation` dataclass gains `direction`; SELECT query extended; PDF detail table renderer gains a Direction column between Transfer type and Flow. Auditor reads the routing signal directly off the violation row.

**Studio editor (leg 2 of the cross-cutting contract).** LimitSchedule card gets a Direction select field (Outbound / Inbound). Card description prose calls out the routing-queue split (Outbound → ops triage, Inbound → AML / compliance review). Entity-ID composite extends from 2-part `parent_role::rail` to 3-part `parent_role::rail::direction`; backward-compat keeps 2-part keys working (treats as Outbound). Trainer can author + flip Inbound caps entirely through the Studio UI, no YAML hand-edit.

**Plant + scenario (leg 5 of the contract).** New `InboundCapBreachPlant` mirror of `LimitBreachPlant`: Credit customer leg + Debit external counter (`Origin=ExternalForcePosted`). `auto_scenario` derives `_pick_inbound_breach_inputs` and plants at `days_ago=3` (distinct from Outbound's `days_ago=4` on the date axis). All densifier + ScenarioPlant pass-through call sites carry the new field. `tests/l2/spec_example.yaml` now declares 1 Outbound + 1 Inbound cap on `(CustomerLedger, *)`. `run/sasquatch_pr.yaml` carries a real-world `(DDAControl, CustomerInboundACH, $20K, Inbound)` AML cap modeled after the federal CTR threshold.

**Fuzzer (leg 6).** `random_l2_yaml(seed)` randomly picks `direction: Inbound` for ~30% of synthesized LimitSchedules; default Outbound stays omitted so existing fuzz_failure fixtures round-trip byte-equivalent. ~5/10 random seeds emit at least one Inbound row.

**Docs.** `concepts/l2/limit-schedule.md` rewritten with the new field semantics + U5 triple-key rule + routing convention. New `walkthroughs/customization/how-do-i-add-an-aml-inbound-cap.md` worked-example walkthrough: the operator's story (compliance asks for $20K daily ACH inbound cap), the YAML diff, the verify path, and 3 "don't" rules. SPEC.md + Schema_v6.md + L1_Invariants.md all carry the per-direction theorem (InboundFlow) + matview shape + Inbound interpretation prose. Walkthrough wired into mkdocs nav.

**Tests.** 2 new validator tests cover the AB.1 U5 behaviors (same parent+rail with different directions allowed; duplicate Inbound triple rejected). New schema snapshot test verifies the matview emits both direction projections + filters + index across PG/Oracle/SQLite. Audit-side test fixture + assertions extended to exercise both directions. Verified at 2963 passed across unit + data + schema + json + cli + audit + docs layers; live PG / AWS QS verify happens when CI runs the e2e + agreement leg on this tag.

**Re-locked seeds:** `tests/data/_locked_seeds/spec_example.{postgres,oracle,sqlite}.sql` regenerated to include the planted Inbound breach rows.

## v11.0.2 — drift/overdraft visual + matview natural-key alignment; CI shim-lockstep fix

Bugfix release. Two production-facing fixes + one release-pipeline structural fix that re-armed the publish path after v11.0.1's smoke step blocked it.

**Drift / Overdraft / Parent Drift tables now display `business_day_start` at SECOND granularity** (was `business_day_end` at DAY). One logical day per row, full timestamp visible so per-account boundary differences (a 17:00→17:00 customer DDA vs midnight→midnight retail DDA) stay legible. Aligns the visual with the matview natural key + scenario plants + universal date filter, and corrects the Daily Statement drill that previously wrote `business_day_end` while Daily Statement filters on `business_day_start` (off by 1 day). `tests/audit/_dashboard_extract.py::_parse_day_cell` now parses both ISO (App2's `2026-05-13 17:00:00`) and locale-rendered (QS at SECOND granularity's `May 13, 2026 17:00:00`) head shapes for the 4-way agreement comparison. All 6 `test_invariant_four_way_agreement[postgres-*]` cells green against deployed Aurora.

**Driver-layer `<th>`-hiding** for QS↔App2 header divergence (`AA.A.995`). `DashboardDriver.table_rows(visual_title, columns=...)` now accepts an optional sequence of raw SQL column names; the driver projects each row to just those cells, looking each up by raw name (App2 stamps `account_id` on `<th>`) or title-case display label (QS stamps `Account ID`). `tests/e2e/_drivers/base.py::rekey_by_columns` + `_title_case_header` helpers preserve known initialisms (`id` / `sql` / `url` / `api`). Hides the previously-leaking renderer difference from the agreement test bodies.

**6 picker failures fixed** (`AA.A.993`). Additive + inverse picker tests on Overdraft + Transactions sheets. Root causes: (a) modern hybrid QS dropdown shape needed search-input selector probe in both `set_dropdown_value` and `_open_control_dropdown`; (b) anchor SQL for Transactions was selecting `clearing-suspense` accounts not in the Account dropdown's narrowed universe — `SheetAnchorSpec` gains an `anchor_where_template` field constraining anchors to the visible dropdown set (`current_daily_balances`); (c) Transfer-picker dropped from Transactions spec — its 8k option universe exceeds App2's 2000-cap (see #994 follow-on).

**L2FT rails inverse-picker reliability** (`AA.A.l2ft-rails-inverse.*`). Browser drivers now scroll-accumulate QS infinite-scroll tables; `DashboardDriver.find_row(visual_title, predicate)` walks for the first matching row with early-exit; 4 inverse-picker tests swapped from DOM-window `table_rows` to authoritative `table_row_count` for the post-filter total.

**Auto-emit `dump-last-errors` on chain failure** (`AA.A.992`). Runner's chain runner emits the dump on its own failure path so triage doesn't need a separate command after the chain reports failed.

**README / CLAUDE.md / GH Pages landing recast** for the v11.x voice: validation tool ("you bring the data — we tell you whether it adds up"), not "AWS QuickSight JSON generator". Explicit "Not an ETL tool" framing.

**CI shim-lockstep fix** (`AA.A.996.release-fix`). v11.0.1's release failed at the smoke step because `quicksight-gen-shim/pyproject.toml` was still pinned to `recon-gen==11.0.0` after the main package bumped to 11.0.1 — `pip install ./dist/*.whl` blew up on the pin conflict. `release.yml` now `sed`-rewrites the shim's `version` + `recon-gen==…` line from `${GITHUB_REF_NAME#v}` before build, so future cuts can't drift via forgotten manual bump. The in-tree shim version is now explicitly a placeholder; CI is authoritative.

## v11.0.1 — L1 / L2FT / Executives date-range filter inclusive bounds

Bugfix release. `TimeRangeFilter` with parameter-bound min/max and no
explicit `include_minimum` / `include_maximum` defaults to exclusive
bounds — QuickSight compiles that with day-granularity to
`column >= addDateTime(1, 'DD', truncDate('DD', date_from)) AND <
truncDate('DD', date_to)`. When `date_from == date_to` (anchor on a
single day), the lower bound shifts to `date_from + 1`, producing an
empty inverted range — the visual shows "No data". App2's already-
inclusive `BETWEEN date_from AND date_to + 1 day` (X.2.j.dateparity)
masked the divergence on the App2 side.

7 call sites flipped to explicit `include_minimum=True,
include_maximum=True`:

- `apps/l1_dashboard/app.py::_scope_one` (covers all 7 L1 invariant
  sheets + Daily Statement + Transactions + Drift Timelines)
- `apps/l2_flow_tracing/app.py` × 3 (Rails / Chains / Transfer
  Templates universal date controls)
- `apps/executives/app.py` × 3 (Account Coverage / Transaction Volume
  / Money Moved)

Investigation is unaffected — its `TimeRangeFilter`s bind directly to
the widget (no parameter intermediate); the filter receives values
directly and the include semantics never bite.

**Captured via** the new `ws_frames.txt` driver artifact (AA.A.qs-
triage.1) — every `QsEmbedDriver`-driven browser test now drops
captured QS WebSocket `START_VIS` frames alongside the existing
`console.txt` / `network.txt` / `dom.html` / `screenshot.png` /
`trace.zip` for forensic replay. No JS re-deploy / re-instrumentation
needed to capture the post-pick parameter substitution QS actually
sent.

**Structural follow-on (backlogged, AA.A.daterange):** the underlying
parameter-pair model has a known multi-widget UX footgun (user can set
"Date From > Date To"). The fix shape — replace
`(ParameterDateTimePickerControl Date From + ParameterDateTimePickerControl
Date To + TimeRangeFilter)` triplet with a single
`FilterDateTimePickerControl(Type="DATE_RANGE")` bound to one column,
same widget Investigation already uses in production — closes the
footgun structurally and aligns L1 / L2FT / Exec with Investigation
on idiomatic QS widget choice. Hits a multi-dataset-per-sheet wall
(L1's Drift sheet, for example, has both leaf-drift + ledger-drift
datasets sharing the date filter via parameter binding; the filter-
bound widget pattern needs a different sharing mechanism). Deferred
to a future cycle when we have appetite for the dataset-consolidation
work.

## v11.0.0 — Phase AC: project rename `quicksight-gen` → `recon-gen`

Consolidated final tag of the Phase AC alpha series (a1 through a6).
PyPI publishes `recon-gen` as the canonical package name; the
`quicksight-gen` shim remains on PyPI as a thin deprecation
meta-package depending on `recon-gen==11.0.0` (drops 1-2 months
after publish).

**Why the rename.** AWS owns the "QuickSight" trademark, and the
tool's scope has grown past pure QuickSight JSON generation — it
also ships a self-hosted HTMX dashboard renderer and a
regulator-ready audit PDF builder. `recon-gen` is trademark-clean
and accurately reflects what the tool does.

**Operator migration:**

- New install: `pip install recon-gen` (or `uv add recon-gen`).
- Existing install: `pip install -U quicksight-gen` keeps working
  through the grace window — the shim transparently pulls in
  `recon-gen` and prints a one-shot `DeprecationWarning` pointing
  at the new install + import paths.
- Imports: `from recon_gen import …` (was `from quicksight_gen import
  …`). No re-exports — the shim handles the *install* path only;
  code changes are required to migrate imports.
- CLI: `recon-gen <verb>` (was `quicksight-gen <verb>`).
- Env vars: `RECON_GEN_*` / `RECON_E2E_*` (was `QS_GEN_*` /
  `QS_E2E_*`). Legacy names still resolve through the EnvVar
  registry's `legacy_name` arm and emit a one-shot
  `DeprecationWarning` per name for the grace window.
- IAM user + OIDC trust policy: rename `quicksight-gen-local` →
  `recon-gen-local`; the `Github_e2e_testing` policy keeps its
  current shape.
- GitHub repo: `chotchki/recon-gen` (was `chotchki/quicksight-gen`);
  GitHub's 301 redirect handles old URLs.

**What's in the box** (carried over from a1—a6):

- `AC.A` — `src/quicksight_gen/` → `src/recon_gen/` (320 files
  preserved via `git mv`); `pyproject.toml` rewires every name +
  entrypoint + package-data + pyright/coverage scope.
- `AC.B` — Env var registry flips to `RECON_*`; lint rule renamed
  `qs-gen-prefix` → `recon-prefix`; tests + locked-seed headers
  re-emitted with the new prefix.
- `AC.D` — Doc surface sweep + audit markdown title flip + test
  signing cert regenerated.
- `AC.F` — PyPI shim (`quicksight-gen-shim/`); release.yml builds +
  publishes both wheels; verify-step asserts shim's
  DeprecationWarning fires and `recon-gen` resolves transitively.

**Alpha-iteration retrospective** (a1—a6 hotfixes, all rolled into
this final tag):

- `a1` — initial AC merge tag; release.yml died at unit collection
  on Z.C.2's loud-fail for missing required cfg fields.
- `a2` — gated `tests/e2e/test_dataset_sql_smoke.py` +
  `tests/e2e/test_demo_apply_row_counts.py` at module import on
  `RECON_GEN_E2E`, so the bare unit job no longer hits a cfg
  load at collection.
- `a3` — three AC.D test-coverage misses (audit markdown title × 2
  + test signing cert CN/Org); regenerated cert with new identity;
  deleted obsolete `tests/unit/test_l2_topology_dot_layout.py` (the
  graphviz `.pipe()` path violated Phase T's wasm-only contract).
- `a4` — added `db_counts.txt` as the 7th browser e2e failure
  artifact (per-matview row counts at moment-of-failure;
  dialect-aware via `pg_class` / `user_objects` / `sqlite_master`).
  Release.yml at this tag was the first fully-green publish to
  PyPI.
- `a5` + `a6` — unit-test env isolation for the new `db_counts`
  tests (runner sets `RECON_GEN_RUN_DIR`, which routes capture
  output away from the legacy `SCREENSHOT_DIR` path the
  assertions key off).

**Known pre-existing infra issue** (NOT a v11.0.0 regression):
`ci.yml::integration-oracle` fails at seed apply with
`ORA-01653: unable to increase tablespace SYSTEM by 1MB`. The CI
container logs in as Oracle's `system` user and creates tables in
the tiny default SYSTEM tablespace. Pre-AC main commits hit the
same failure. Fix is one of: switch CI to a dedicated app user
with `DEFAULT TABLESPACE USERS`, resize SYSTEM at container init,
or seed in chunks. Queued post-release.

## v11.0.0a6 — hotfix: extend a5's env-isolation fix to the other two tests

v11.0.0a5's `replace_all=true` edit only matched the comment block in
`test_writes_per_table_counts_for_prefixed_tables`; the other two
methods in `TestCaptureFailureDbCounts` had different `setattr` blocks
and didn't receive the `monkeypatch.delenv(RECON_GEN_RUN_DIR.name, …)`.
ci.yml's three runner-driven db jobs still failed on
`test_empty_file_when_no_prefixed_tables` +
`test_sidecar_swallows_bad_dialect`.

This release adds the delenv to those two too. Test-only change.

## v11.0.0a5 — hotfix: TestCaptureFailureDbCounts unit-test env isolation

v11.0.0a4 release pipeline went fully green (published to PyPI) — but
ci.yml's three runner-driven db jobs (`e2e-sqlite`, `integration-pg`,
`integration-oracle`) failed at the unit prelude because
`TestCaptureFailureDbCounts` keyed its assertion paths off the legacy
`SCREENSHOT_DIR / "_failures"` branch in `_capture_path`. The runner
sets `RECON_GEN_RUN_DIR` per-cell, which takes priority and routes
output to `<run_dir>/browser/<test_id>/db_counts.txt` instead. Tests
passed locally (env var unset) but `FileNotFoundError`'d in CI.

Fix: each test calls `monkeypatch.delenv(RECON_GEN_RUN_DIR.name,
raising=False)` before patching `SCREENSHOT_DIR`, forcing the legacy
branch the assertions key off. No prod-code change.

## v11.0.0a4 — e2e failure capture: per-matview row-count dump

Adds a 7th failure artifact to the browser e2e capture pipeline:
`db_counts.txt` — one `<table>: <count>` line per relation matching
`<cfg.db_table_prefix>_*` in the demo DB. Answers the first question
every "visual rendered blank" triage asks ("is the data even there?")
without DOM archaeology.

Motivated by v11.0.0a3's e2e leg: two `app2-Template` /
`app2-Completion` browser tests timed out with the table-visual stuck
on `htmx-request`, leaving triage to correlate the empty DOM against
the API leg's pass/fail. With this dump in place, the first line of
`db_counts.txt` immediately distinguishes:

- backend-empty (matview has 0 rows — seed didn't fire, refresh
  skipped, or the parameter narrow excluded everything), from
- backend-OK / frontend-stalled (matview has N rows but the swap
  landed wrong / HTMX request hung).

Implementation:

- `_capture_failure_db_counts(cfg, test_id)` in
  `common/browser/helpers.py` — dialect-aware enumeration (Postgres
  `pg_class`, Oracle `user_objects`, SQLite `sqlite_master`); one
  `COUNT(*)` per matching relation. Same sidecar contract as the
  other capture functions: never re-raise (capture failures emit
  `[CAPTURE FAILURE] db_counts.txt` to stderr and the original test
  failure still bubbles up).
- `trigger_failure_capture(page, *, test_id, cfg=None)` — new optional
  `cfg` kwarg; when present, db_counts fires alongside the other 5
  page-side dumps. Backwards-compatible (`cfg=None` skips just the
  DB dump).
- `tests/e2e/_capture.py::maybe_capture_on_failure` — resolves cfg
  via `request.getfixturevalue("cfg")` and forwards. Soft-fall when
  the fixture isn't in scope.

Unit-tested against SQLite — 3 cases (prefix matches, empty result,
bad cfg → "skipped" marker).

## v11.0.0a3 — hotfix: AC.D test-coverage misses + obsolete dot-binary test

v11.0.0a2's release got past collection but caught three Phase AC tail
issues the AC.D doc sweep missed:

- `tests/audit/test_cli_smoke.py` asserted the markdown header literal
  `# QuickSight Generator Audit Report`; the emitter was renamed to
  `# Recon Generator Audit Report` in AC.D. Flipped both call sites.
- Test signing cert (`tests/audit/fixtures/test-signing-{cert,key}.pem`)
  had `CN=quicksight-gen audit test signing, O=quicksight-gen test
  fixtures`; the corresponding test assertion was already updated to
  `recon-gen`. Regenerated the self-signed cert (RSA 2048, 100y) with
  the new CN/Org. No security impact — fixture cert, never trusted by
  any cert store.

Also deleted `tests/unit/test_l2_topology_dot_layout.py` — that test
shells out to the `dot` binary, but Phase T (v8.1.0) moved rendering
to the browser via `@hpcc-js/wasm-graphviz` and the Python lib is
contract-bound to source-string-only (`Digraph().source`, never
`.pipe()`, per `pyproject.toml` graphviz dep comment). The test was
added in X.4.j to guard against a layout-engine regression, but the
class of bug it guards against can't surface via the Python path now
that rendering is wasm-side — the equivalent regression risk lives in
Studio's browser e2e suite.

## v11.0.0a2 — hotfix: e2e collection skip when RECON_GEN_E2E unset

v11.0.0a1's release pipeline died at `Tests + pyright strict` (same
shape as v10.0.0a7 and v10.1.0a1 — three releases in a row). Two e2e
tests — `tests/e2e/test_dataset_sql_smoke.py` and
`tests/e2e/test_demo_apply_row_counts.py` — call `_load_cfg()` at
module import; Z.C.2's loud-fail on missing required cfg fields
(`aws_account_id`, `aws_region`, `deployment_name`, `db_table_prefix`,
`datasource_arn`) raises `ValueError`, crashing pytest collection in
the bare CI unit job (no cfg yaml, no env overrides).

Fix: `pytest.skip(allow_module_level=True)` guarded on
`RECON_GEN_E2E.get_or_none()` at the top of both modules — matches the
e2e suite's existing skip-marker pattern in
`tests/e2e/conftest.py::pytest_collection_modifyitems`, but fires
*before* import-time cfg load instead of after collection. Unit job
now collects 0 tests from these files and proceeds.

## v11.0.0a1 — Phase AC: project rename quicksight-gen → recon-gen

AWS owns the "QuickSight" trademark and the tool's scope has grown
beyond emitting QuickSight JSON — it also ships an HTMX renderer
and a regulator-ready audit PDF. Rename to clarify both.

- Source package: `quicksight_gen` → `recon_gen` (history preserved
  via `git mv`).
- PyPI distribution: `quicksight-gen` → `recon-gen`.
- CLI entrypoint: `quicksight-gen` → `recon-gen`.
- Env-var prefixes: `QS_GEN_*` / `QS_E2E_*` → `RECON_GEN_*` /
  `RECON_E2E_*`. Legacy names still resolve through `EnvVar`'s
  `legacy_name` arm and emit a one-shot `DeprecationWarning` per name
  for the grace window.
- IAM user + OIDC trust policy: `quicksight-gen-local` →
  `recon-gen-local`; existing `Github_e2e_testing` policy retained.
- PyPI shim package `quicksight-gen` (at 11.0.0a2 in lockstep) keeps
  `pip install quicksight-gen` working; it has no code, depends only
  on `recon-gen==<same version>`, and emits a `DeprecationWarning`
  pointing operators at `pip install recon-gen` + import-path
  rewrites. Shim wheel drops 1-2 months after v11.0.0 publish.

## v10.1.0a1 — Phase AA: dropdown defaults, exception literacy panels, account search, Daily Statement + App2 partial-refetch fixes

Pre-release / alpha tag. Closes Phase AA — a dashboard-UX literacy + correctness pass landing on top of the Phase Z grammar work. Six independent threads ship together; A.6/A.7/A.8 (generic test-coverage gaps) deferred to Phase AB.

**AA.A — Multi-select → single-select default for drill-to-one dropdowns.** Audited all 36 dropdowns across L1 / L2FT / Investigation; flipped 25 to `SINGLE_SELECT` (drill-to-one workflows where the operator picks one value and reads the narrowed table). 6 stay multi-select for workflow-state columns (`status` / `completion_status` / `bundle_status` / `check_type` — analysts often select 2-3 states at once). The `ALL` sentinel default holds the "show everything on load" semantic; `_data_value_clause` / `_match_all_in_clause` SQL helpers rewrote from `col IN (<<$pX>>)` to `('<sentinel>' = <<$pX>> OR col = <<$pX>>)`. App2 `_tree_filter_specs.py` gained a `SINGLE_SELECT + StaticValues` deriver — L2FT MetaKey dropdowns now render in App2 where they were previously skipped.

**AA.B — Daily Statement Role → Account → Business Day workflow.** AA.B.1 added the Role dropdown above Account so the cascade direction is visually explicit. AA.B.4 caught a wrong-visual-title regression in the e2e test (Posted Money Records vs sheet-name). AA.B.5 chain: TomSelect's internal Sync was overwriting manual `option.selected` mutations — `App2Driver.pick_filter` routes through `select.tomselect.setValue()` to fix. AA.B.5.followon: a true 3-of-6 partial-refetch bug surfaced — the bottom 3 visuals in DOM order silently dropped the refresh trigger under parallel-initial-load + mid-load filter pick. Fixed via three coordinated changes:

1. `hx-sync="this:queue last"` (was `this:replace`) — queues new requests behind in-flight ones instead of attempting an abort+new race HTMX dropped half the time.
2. Explicit per-visual `htmx.trigger(div, "refresh")` iteration in `wireFilterAutoRefresh` (was `htmx.trigger(body, "refresh")` broadcast). Bypasses the cross-element ordering edge case.
3. Per-visual loading skeleton (`.visual-loading` inside every `.visual-data` swap target; CSS `transition-delay: 300ms` so fast loads never flash; bootstrap.js re-injects on `htmx:beforeRequest` so refresh loads show it too). Presence/absence of `.visual-loading` is the new "is loading?" detection signal — `App2Driver.wait_loaded` + `_wait_for_refetch` poll skeleton-absence instead of the racy first-response + networkidle pattern (which lost queued refetches under queue-last).

AA.B.5.followon.2: Daily Statement browser tests went calendar-flake on UTC midnight crossover (cust-011 had 0 tx on chain's "yesterday"). New helper `tests/e2e/_daily_statement_pick.py::find_account_day_with_data(cfg)` queries the deployed DB for a `(account_display, role, business_day)` triple with most rows on the most-recent day; tests drive all three filter pickers to those values. New `DashboardDriver.set_date(label, iso)` verb (QS impl; App2 no-op since App2 skips `add_parameter_datetime_picker`).

**AA.C — Exception literacy panels.** `docs/L1_Invariants.md` parser captures a `what_to_do` field per invariant; `common/sheets/_exception_panel.py` helper composes plain-English text-box panels at the bottom of the 6 L1 invariant sheets + Today's Exceptions. Studio trainer pane (X.4.h.7's deferred work) wires the same content as a per-template knowledge surface — same prose, two consumers.

**AA.E — Account dropdown shows "Name (id)".** AA.E.1 picked the search-by-name-AND-id pattern (substring search hits either the customer name or the bare account id). AA.E.2 wired `account_display` (the `account_name || ' (' || account_id || ')'` projection) as the bound column across all L1 Account dropdowns. AA.E.3 catch: Daily Statement's direct `add_parameter_dropdown` callsite missed the AA.E.2 sweep — silent-empty regression caught by the new browser test `test_daily_statement_picked_account_narrows_table`.

**AA.H — QS-driver lifecycle primitive.** `QsEmbedDriver.embed(*, aws_account_id, aws_region, viewport=…)` is a `@contextmanager` classmethod that owns the WebKit page; `open(dashboard_id)` mints fresh region-matched single-use embed URLs. Wired across all 3 QS-driver fixtures + the audit-agreement scenarios; runner exit-code parity normalized.

**Diagnostics.** `data-bound-params` attribute on each visual's `<script class="chart-data">` self-describes what URL params each visual was queried with — bootstrap.js copies the attr onto the persistent `<section>` before clearing the script so failure-capture's `dom.html` reveals the exact per-visual bind shape. `network.txt` capture extended to keep every `/visuals/*/data` response regardless of status (was filtering out 200s, which masked empty-vs-no-request distinction).

**Deferred to Phase AB.** AA.A.6 (generic additive-pickers row-survival test), AA.A.7 (inverse exclusion test), AA.A.8 (generic "table failed to render" probe across all sheets + the adjacent Pending Aging duplicate-columns bug). These are coverage gaps + hardening, not regressions blocking the alpha. Each needs a spike before locking implementation.

**Migration for operators.** None — every change is internal to the dashboards / test infrastructure / docs. Existing `config.yaml` files, L2 YAML files, and deploy pipelines work unchanged from `v10.0.0a7`.

## v10.0.0a7 — Z.B + Z.C + Z.D: `transfer_type` subsumed into rail; deployment-namespace collapse; Phase Z complete

Pre-release / alpha tag. Closes Phase Z (started in `v10.0.0a6` with Z.A's chain grammar collapse). Two more grammar reshapes + an end-of-phase verify ship together.

**Z.B — `transfer_type` subsumed into rail.** `Rail.transfer_type` and `TransferTemplate.transfer_type` are dropped — `Rail.name` / `TransferTemplate.name` are the sole identifiers. The `<prefix>_transactions.transfer_type` column + its perf index disappear. `LimitSchedule.transfer_type` is renamed to `LimitSchedule.rail`. Validator U6 (per-leg `(transfer_type, role)` uniqueness — degenerate after collapse since rail name is already globally unique) is dropped; R10 folds into U5; R7 drops the OR-of-two-resolutions leg for `bundles_activity`. The "templated-leg footgun" (LimitSchedule on `<leg_rail_type>` not firing for templated transactions because the leg's `transfer_type` carried the template's type, not the leg rail's) goes away implicitly — LimitSchedule keys on `rail_name` now, which IS the leg rail's name on leg-transactions. Closes pending task #498 (LimitSchedule uniqueness on `(parent_role, transfer_type)`) trivially. Pure deletion + one rename; no new fields.

**Z.C — Deployment-namespace collapse.** Three slots → two cfg fields. `cfg.resource_prefix` (default `qs-gen`) + `cfg.l2_instance_prefix` (auto-stamped from `l2.instance`) collapse into a single `cfg.deployment_name` (required, loud-fail). `l2.instance` (which drove the DB-table prefix everywhere) is dropped entirely; that role moves to `cfg.db_table_prefix` (also required, loud-fail). Cleanup tag pair (`ResourcePrefix=...` + `L2Instance=...`) collapses to single `Deployment=<deployment_name>` tag. Generated QS resource IDs shift from `<resource_prefix>-<l2_instance_prefix>-<suffix>` to `<deployment_name>-<suffix>` — cleaner per-deploy isolation, and multiple deployments of the same L2 against one shared DB no longer collide because each deploy picks its own `db_table_prefix`. **Breaking for operator yamls:** existing `config.yaml` files need `deployment_name:` + `db_table_prefix:` added (loader raises `ValueError("Missing required configuration: ...")` with the field names + env-var fallbacks); existing L2 yamls need `instance:` removed (loader raises `L2LoaderError` with an actionable migration message pointing at the new cfg fields). The `_dev/runner.py` synthesizes per-cell cfg via `QS_GEN_DEPLOYMENT_NAME=qsgen-<spec.name>` + `QS_GEN_DB_TABLE_PREFIX=qsgen_<spec.name>` env injection — no shared-state collisions across parallel matrix cells. Sweep touched ~250 sites across `common/l2/`, `apps/*/datasets.py`, `cli/_helpers.py`, `_dev/runner.py`, `tests/`, `docs/`, `.github/workflows/{ci,e2e,release}.yml` (4 parallel agents for the test-fixture surface).

**Z.D — End-of-phase verify.** Full 13-cell `./run_tests.sh up_to=db` matrix: **11/11 deterministic cells GREEN** across `sp/sq × pg/or/sl × lo` + `sp/sq × pg/or × aw` (live Aurora `database-2`). 3 fuzz cells failed `test_demo_apply_row_counts.py::test_matview_has_at_least_one_row[todays_exceptions]` with `got 0, expected ≥1` — fuzz-shape instability (random seed 3313442831 produced an L2 yaml whose plant set doesn't fire today's exceptions; matview legitimately empty). NOT a Z regression — same class as historical task #450; tracked as a separate fuzz-contract follow-up.

**Two follow-on cleanups bundled in.** Z.C.8: `seed.py::_classify_rail` was matching legacy snake_case `transfer_type` tokens (`"ach_inbound"`) but Z.B's collapse means rails now carry CamelCase identifiers (`"CustomerInboundACH"`). Rewired to substring-match the new shape; restored the sasquatch_pr volume threshold (was widened 30k → 3k as Z.C.7 workaround). Same commit cleaned up duplicate `rail_name=` kwargs from WIP commit `a2191a0` in `tests/audit/test_scenario_expectations.py` + `tests/data/test_seed_persona_clean.py`. Z.C.9: the runner's per-cell `_synth_l2.yaml` was still injecting the dropped `instance:` key (pre-Z.C the runner used it for per-cell DB-table scoping); fix drops the inject and defensively pops any stray `instance:` from source yamls.

**Tests.** Pyright clean (0 errors, 0 warnings). Unit suite: **2919 tests pass, 0 fail with NO ignores** — the previously-skipped `test_scenario_expectations.py` + `test_seed_persona_clean.py` (collection-failed since WIP commit `a2191a0`) now collect + pass. Z.B.13 closed in the same sweep (spec_example seeds re-locked for pg/oracle/sqlite via `quicksight-gen data lock`).

**Migration for operators.** Existing `config.yaml` files: add `deployment_name: <your-prefix>` and `db_table_prefix: <your_prefix>` (use distinct values; can be the same string if you prefer single-axis identity, but DB table prefix typically uses underscores instead of hyphens for Oracle compatibility). Existing L2 yaml files: remove the `instance:` line. The loader errors at config / L2 load time with actionable messages pointing at this section. CI workflows: `.github/workflows/{ci,e2e,release}.yml` updated to the new field names in the bundled config blocks — operator-maintained workflow snippets need the same substitution.

## v10.0.0a6 — Z.A chain grammar collapse: `Chain(parent, children)` replaces `ChainEntry(parent, child, required, xor_group)`

Pre-release / alpha tag. Phase Z.A: collapse the Chain primitive's two-flag firing-semantic encoding (`required: bool` + `xor_group: <name>`) into a single list-of-children where the cardinality IS the semantic — singleton ⇒ required, multi ⇒ XOR alternation. The `xor_group` *name* was never load-bearing (it only grouped rows by string match); the list IS the group.

**The collapse (Z.2-Z.14).** `primitives.py`'s `ChainEntry(parent, child, required, xor_group)` becomes `Chain(parent, children: tuple[Identifier, ...])`. Loader rejects legacy keys (`child`, `required`, `xor_group`) with an actionable error pointing at the new grammar. Three validator rules dropped (C2 "xor_group members share parent" — impossible to violate now, every Chain row IS one parent; C4 "xor_group ≥ 2 members" — singleton means required, not degenerate; C4.1 "required + xor_group contradict" — unrepresentable). One added: C6 "for any given parent, no child appears in two Chain rows" (catches the previously-unrepresentable required-AND-in-xor-group overlap). Composite addressing for chain rows is `parent::sorted-children-csv` everywhere (`editor.py`, `_studio_editor_routes.py`, `_html_id_slug` CSS-pseudo-element-safe id swap kept).

**Consumer cascade.** `seed.py` chain firings, `auto_scenario.py` plant constructors, `derived.py` PostedRequirements auto-derivation (now keyed on `len(children) == 1 and only_child in chain_targets`), `topology.py` per-edge metadata (cardinality + xor_siblings), `coverage.py`, `l2_flow_tracing/datasets.py`'s `_declared_chains_cte`, `handbook/diagrams.py` chain-edge labels — all walk the new shape. Studio chain create/edit form drops the single-`child` / `required` / `xor_group` fields and replaces with a `children` multi_select sourced from rails+templates (the same pattern TransferTemplate.leg_rails uses).

**Fixture + docs prose migration.** Bundled L2 fixtures (`_l2_fixtures/sasquatch_pr.yaml`, `tests/l2/spec_example.yaml`, `apps/l1_dashboard/_default_l2.yaml`) migrated mechanically — XOR groups of N rows collapsed into one Chain row with N children, descriptions hoisted onto the row. Doc prose sweep across SPEC.md, handbook/l2_flow_tracing.md, handbook/seed-generator.md, scenario/index.md, concepts/l2/index.md replaces stale "required + optional" / "Required: true" / "xor_group" prose with the new singleton/multi framing.

**Why now.** X.4.f studio editor just shipped row-per-link chain UI (X.4.f.10) and Phase AA is the next public release. Once a published version carries the legacy `chains:` row-per-link grammar, migrating the yaml shape becomes a versioning headache (operator yaml files in the wild). Cleaning the grammar before the AA cut avoids that. The redesigned Z.B (drop `Rail.transfer_type`, rename `LimitSchedule.transfer_type` → `rail`) follows next.

**Tests.** Locked seeds re-locked per-dialect; spec_example chain firing is singleton-only so the SHA didn't shift, but the determinism check confirms the new emit produces byte-identical output. Re-enabled the 2 Studio chain form tests skipped during the Z.6 cascade (`test_chain_create_form_renders_parent_child_dropdowns`, `test_put_chain_edit_renders_card_after_save`) — both passing against the new multi_select form. Test counts: 1752 unit pass + 73 skipped (-5 from v10.0.0a5: removed C2/C4/C4.1 tests, added C5 empty-children + C6 duplicate-child tests). Pyright clean. **Full 13-cell `up_to=db` matrix**: green across `sp/sq/fuzz × pg/or/sl × lo + sp/sq × pg/or × aw` (10 effective cells; sl×aw auto-skipped). Previous browser/api e2e cells held for the AA GA cut.

## v10.0.0a5 — Studio sidefile persistence + only_template + derive_balances

Pre-release / alpha tag. X.4.h.7-9 + X.4.i.1-3: trainer-mode session state survives Studio restarts, plus two new "additive knobs" the operator asked for.

**X.4.h.7 — sidefile persistence (NOT in-place cfg.yaml rewrite).** Trainer-mode UI mutations (data-shaping panel knobs + scenario window + etl_hook toggle) persist to a sibling `<cfg.parent>/.studio-state.yaml` so they survive every Studio restart. Sidefile chosen over in-place cfg.yaml rewrite because operator-authored cfg.yaml carries `# comments` per section that PyYAML round-trip would obliterate (matches the SPEC's "freeform comments dropped on serialize" contract). New `common/l2/studio_state.py` carries `StudioState` frozen dataclass + `load`/`save` + `merge_into_test_generator`; atomic write via `save_yaml_atomic` (same primitive `L2InstanceCache.save_l2` uses). `TestGeneratorCache.from_cfg_with_state(cfg, cfg_path)` is the Studio-CLI factory that loads the sidefile + wires `_persist()` into every mutation method. Missing sidefile ⇒ pristine cfg defaults; malformed sidefile ⇒ same fallback with a warning to stderr.

**X.4.i.1 — `only_template` scope mode.** Fourth value on the scope-knob radio: emit baseline restricted to a single TransferTemplate's leg-rails dependency closure. Closure = `template.leg_rails` only (per design — "leg-rails + their accounts only", smallest dataset). Plants opt-in via `cfg.test_generator.plants` — default `()` keeps locked-seed determinism on a fresh `only_template` deploy; when the trainer flips plants on, the existing scenario plants land on top. Loud-fail at deploy when `cfg.test_generator.only_template` is unset or names an unknown template (lists declared templates in the error). New `emit_baseline_seed(only_rails=…)` inverse of the existing `skip_rails`. Chain firings from in-closure parents naturally fan out to children — that's the trainer's intended "see the whole transfer flow rooted at this template" surface.

**X.4.i.2 — `derive_balances` composing flag.** New post-step-3 hook (`step_3_5_derive_balances`) that runs after the generator regardless of scope. Computes `money = SUM(amount_money)` per `(account_id, business_day)` for the configured account roles, UPSERTs into `<prefix>_daily_balances` via DELETE-then-INSERT (dialect-portable two-pass). The drift invariant run forward — auditing `money == SUM(amount_money)` would always pass for derived rows since they were just computed that way. Default = control accounts only (`gl_control` / `concentration_master` / `funds_pool`); operator overrides per-L2 via the new `cfg.test_generator.derive_balances_account_roles` field for trainer scenarios that don't depend on stated-vs-derived drift. SQLite/PG/Oracle dialect branches for the date-bucket expression (`DATE(posting)` vs `CAST(posting AS DATE)`). `failed` transactions excluded. Re-running overwrites in-place. Wired as step 3.5 in `run_deploy_pipeline`; `DeploySummary` carries `step3_5_derived_balance_rows`.

**X.4.i.3 — UI controls for both.** Data-shaping chrome-strip extends with `_render_only_template_strip` (text input wired to PUT `/data/knobs/only_template`) + `_render_derive_balances_strip` (checkbox wired to PUT `/data/knobs/derive_balances`). The `derive_balances_account_roles` narrowing field stays cfg-yaml-only — surfaced as a read-only chip beside the toggle so the trainer sees what's in scope without crowding the chrome. New cache mutators `update_only_template` + `update_derive_balances` round-trip through `_persist()`. Scope-radio extends from 3 to 4 values.

**Verify.** 1757 unit tests pass + 73 skipped (+47 new since v10.0.0a4: 18 sidefile + 10 cache-wiring + 6 route-integration + 5 only_template + 6 derive_balances + 13 UI-strip + close-out doc fix). 0 pyright errors. CLAUDE.md `Commands` section now documents `studio` + `dashboards` with the four scope modes + derive_balances behavior; `docs/reference/self-host.md` renamed (App 2 → Dashboards) with Studio cross-mention. `up_to=app2` chain verify + live AWS deploy held for v10.0.0 GA.

## v10.0.0a4 — Studio etl_hook UI toggle + SQLite Deploy 10× faster (binds-not-literals)

Pre-release / alpha tag. Two pieces, both Studio-trainer ergonomics:

**X.4.h.etl-toggle — UI toggle for the upstream-re-seed pair.** Studio data-shaping panel gains a top-row strip showing `cfg.etl_hook` + a checkbox to disable it for the next Deploy. Disabling clears BOTH `cfg.etl_hook` (step 1) and `cfg.etl_datasource` (step 2 pull) on the patched cfg — they're a coupled "upstream re-seed" pair, and decoupling them produces 500s when the operator's `etl_datasource` only exists *because* the hook started it (e.g. the local postgres the hook brings up). Original cfg's stored fields are untouched — re-enable + re-deploy restores both.

Three render states surface honestly:

- configured + enabled (checked + `<code>` showing the command),
- configured + disabled (unchecked + greyed-out + line-through),
- not configured (disabled + "(not configured)" italic).

`PUT /data/knobs/etl_hook` + URL-state restore follow the same `HX-Trigger` / `HX-Push-Url` contract every other knob uses. Default state (enabled) keeps the URL clean — `?etl_hook=disabled` only appears when explicitly off.

17 new tests (6 cache + 11 route) covering all three render states, the PUT round-trip, the URL state restore, the severability rule (route absent without `tg_cache`), and the patched-cfg pair semantic.

**X.4.j.sqlite-binds — coalesce same-shape INSERTs into `executemany`.** SQLite Studio Deploy (sasquatch_pr, ~72k single-row INSERTs across 2 tables) drops from **~68s to ~7s end-to-end** — about a 10× speedup. The `step3_generator` insert phase alone goes from ~33–47s to ~0.5s.

Fast path (added to `common/db.py::execute_script`'s SQLite branch):

- Walk the script statement-by-statement; splitter respects single-quoted strings + `--` line comments.
- Consecutive INSERTs against the same `(table, cols)` accumulate into a buffer that flushes as one `cur.executemany` call. The literal parser handles `'string'`, `NULL`, int, float; anything fancier (function call, `''` escape, hex literal, etc.) returns `None` and the statement falls through to per-statement `cur.execute`.
- Non-INSERT statements (`CREATE` / `DELETE` / `REFRESH` / comments-only) flush the pending buffer first, then run via `cur.execute`. Order is strictly preserved.
- Caller's commit semantic is unchanged — helper does NOT commit; the connection's transaction stays open across the whole call.

Postgres + Oracle paths untouched (PG already does one `cur.execute` of the multi-statement string; Oracle has its own `INSERT ALL` batcher in `batch_oracle_inserts`). The optimization only affects SQLite, which is the Studio default for the trainer's local-iteration loop.

5 new tests cover: bind fast path with mixed literal types (string / NULL / int / float / JSON-quoted), grouping change flushes the buffer, non-INSERT pass-through preserves order, header comments dropped (not executed), caller owns commit (rollback wipes inserts).

**No customer-facing change** beyond Studio iteration speed. The `schema` / `data` / `json` / `audit` artifact groups, `config.yaml`, and L2 institution YAML are untouched. Test impact: full unit suite stays green at 1699 passed / 73 skipped (+22 from v10.0.0a3 baseline).

## v10.0.0a3 — hotfix: restore the lazy-import discipline for `[serve]`-extra deps in the CLI shell

Hotfix for v10.0.0a2. Caught by the v10.0.0a2 push pipeline:

- `pages.yml::build` (installs `[docs]`, runs `quicksight-gen docs apply`) crashed at `from quicksight_gen.cli import main` with `ModuleNotFoundError: No module named 'uvicorn'`.
- `release.yml::Smoke test wheel` (installs the wheel + `[docs]`, runs `quicksight-gen --version`) crashed at the same import path.

Root cause: the X.4.a refactor (v10.0.0a2) lifted `cli/serve.py`'s `import uvicorn` and `from quicksight_gen.common.html.{server,_smoke_app} import …` from inside the function body up to the module top of the new `cli/_html_serve.py`. The original lazy-import pattern existed exactly so a `[docs]`-only install could `--help` / `docs apply` without `[serve]` (uvicorn / starlette aren't pulled by `[docs]`); the lift broke that.

Fix:

- `cli/_html_serve.py` — uvicorn + starlette + `common.html.server` + `common.html._smoke_app` imports moved back inside `run_html_server`. `Route` / `Mount` move under `TYPE_CHECKING` (they're only the type-alias shape).
- `cli/_html_serve.py` — the `StudioRoutesFactory` type alias becomes a PEP 695 `type` statement (Python 3.13's lazy `type X = ...` form), so `Route | Mount` is only evaluated when a type-checker walks the alias, never at module load.
- `cli/studio.py` — `from quicksight_gen.common.html._studio_routes import make_studio_routes` deferred inside the `studio` function body (`_studio_routes.py` is starlette-backed; same gating concern).
- `tests/unit/test_cli_no_serve_extra.py` (new — pins the regression class). Installs a `sys.meta_path` finder that ImportErrors on every `uvicorn` / `starlette` import, evicts already-loaded copies, then asserts (a) `from quicksight_gen.cli import main` works and (b) `CliRunner.invoke(main, ['--help'])` exits 0 with both `studio` + `dashboards` listed (proves the full Click registration walks without crashing on the simulated-missing imports). Adds a guarded fixture so the eviction is bounded to the test scope and other tests that legitimately use starlette stay green.

The unit suite stays at +2 from the v10.0.0a2 baseline (1441 passed / 73 skipped); no behavioral change beyond making the CLI shell importable on the `[docs]`-only install paths the workflow files use.

## v10.0.0a2 — Phase X.4.a (Studio foundations): the severable mount + the `studio` / `dashboards` Click commands

Pre-release / alpha tag. **Internal CLI surface change**: the X.2-era `serve app2 apply` is removed (no deprecation alias — the operator was the only user), replaced by two top-level commands. The four-app behavior + every `--app` / `--stub` / `--docs` / `--dev-log` / `--host` / `--port` flag round-trip identically through the rename; the only visible move is the verb itself. **Customer-facing artifacts (the stable `schema` / `data` / `json` / `audit` groups, `config.yaml`, the L2 institution YAML)** are unchanged.

The work in this tag (X.4.a, all six sub-tasks ticked):

- **`quicksight-gen dashboards`** — the renamed `serve app2 apply`. Same Click options, same default behavior (no-arg = serve all four real apps + the embedded handbook on the same Starlette process). Lifted the cfg-load / theme / docs-embed / pool / uvicorn dance out of `cli/serve.py` into `cli/_html_serve.py::run_html_server` so the next command can reuse it without copy-pasting.
- **`quicksight-gen studio`** — new top-level command. Same Starlette app as `dashboards`, but with Studio routes spliced in: the X.4.a.4 landing placeholder owns `GET /` (replacing the dashboards-only redirect), and an in-memory `L2InstanceCache` is wired through to every Studio request handler. The unified diagram (X.4.c), editor (X.4.e), data-shaping panel (X.4.h), and Deploy orchestration (X.4.g) hang off `common/html/_studio_routes.py::make_studio_routes(cache)` in subsequent X.4 sub-phases. Studio always requires `--l2`; deliberately omits `--app smoke` and `--stub` (Studio's whole point is editing real L2 YAML).
- **Severability contract enforced two ways.** Runtime: `tests/unit/test_studio_severability.py` asserts `make_app(... studio_routes=None)` keeps the X.2 `GET / → /dashboards` redirect + the four Dashboards routes resolve, and `make_app(... studio_routes=...)` overrides `GET /` while every Dashboards route still resolves alongside. Code-time: an AST-grep test in the same file flags any Studio-side import (`_studio_routes`, `make_studio_routes`, `L2InstanceCache`, `cli.studio`) that ever reaches into `cli/dashboards.py`, and pins the `_html_serve.py` cache-construction guard inside the `studio_routes_factory is not None` branch — so the regression class ("someone wired Studio into Dashboards") fails CI before the runtime degradation lands.
- **`L2InstanceCache` + `save_yaml_atomic`.** Studio-owned, read-side: `from_path(p)` loads + caches; `get()` returns the cached instance (object-identity-stable, no per-call re-parse); `replace(new_instance)` swaps the cached value without touching disk (X.4.d's mutators land on top). The atomic-write primitive does temp-in-same-dir + fsync + rename: on POSIX, a crash between fsync and rename leaves the prior YAML intact; cleanup-on-failure suppresses any temp leftover. Lives in `common/l2/cache.py`. The `cache.save()` method that composes `serialize_l2` + `save_yaml_atomic` + `replace` lands at X.4.d.3 alongside the serializer.
- **`make_app` gains an optional `studio_routes` kwarg.** When set, the routes are spliced in BEFORE the Dashboards routes so a Studio-defined `GET /` wins on the route table; the default `/ → /dashboards` redirect is skipped. `cli.dashboards` always passes `None`; `cli.studio` passes the list `make_studio_routes(cache)` built. Backward-compatible — every X.2-era `make_app(...)` call site stays unchanged (the kwarg defaults to `None`).
- **Sweep.** `cli/serve.py` and `tests/cli/test_serve_smoke.py` deleted; `tests/cli/test_dashboards_smoke.py` is the same test surface under the new verb. References swept across `README.md` (the "Self-hosted renderer" section now reads "(Dashboards)" — and nudges integrators toward `quicksight-gen studio` for the editor / data-shaping loops as those land), the in-tree `docs/reference/self-host.md`, and the module docstrings in `cli/docs.py` / `common/html/__main__.py` / `common/html/_smoke_app.py` / `common/html/render.py`. The intentional historical pointers — `cli/__init__.py`'s comment "(rename of `serve app2 apply`)" and `cli/dashboards.py`'s docstring "Replaces the X.2-era `serve app2 apply`" — stay; they're orientation for someone who knew the old verb.

The CLI surface that lands at v10.0.0 GA after the rest of X.4 ships:

```bash
quicksight-gen studio -c config.yaml --l2 inst.yaml      # Studio + Dashboards (the implementation-tools surface)
quicksight-gen dashboards -c config.yaml --l2 inst.yaml  # Dashboards alone (the read-only renderer)
```

Both commands' defaults serve the four real apps on `localhost:8765`. `--port`, `--host`, `--app`, `--docs` work the same on both; `--stub` is `dashboards`-only.

What does NOT land in this tag: the unified diagram (X.4.b spike + X.4.c build), the editor primitives (X.4.d) + forms (X.4.e/f), the Deploy-changes pipeline (X.4.g), the data-shaping panel UI (X.4.h). The Studio landing page is a placeholder: it renders the L2 instance prefix + entity counts so a deploy mistake (wrong YAML wired) is visible in the page body, and links to `/dashboards`. Everything else is "coming in X.4.b through X.4.k".

Test impact: `tests/unit/` gains `test_l2_cache.py` (7 tests) + `test_studio_severability.py` (6 tests); `tests/cli/test_dashboards_smoke.py` replaces the deleted serve-smoke test with the same coverage shape. Full unit suite stays green at 1439 passed / 73 skipped (no new skips, no regressions).

## v10.0.0a1 — Phase X.4 (Studio) plan locked; design + PLAN landed (no code change yet)

Pre-release / alpha tag. **No code change** — the wheel is byte-equivalent to v9.4.0 in user-visible behavior. This tag marks the SPEC + PLAN landing for **Phase X.4 — Studio** (the implementation-tools surface; original X.4 + X.5 folded into one phase).

What landed in the repo (design artifacts only — Studio code starts after this tag):

- **`SPEC_studio.md`** (333 lines, sibling to the L2-model spec material in `docs/Schema_v6.md`). Goal / non-goals / 3 personas + their loops / architecture (process model, severability, source-of-truth, cascade discipline, allowlist expansion) / the 5-step "Deploy changes" pipeline (etl_hook gate → wipe + optional ETL pull → generator → matview refresh → Dashboards reload) / data-shaping model (`test_generator:` block: `enabled` / `scope` (full|uncovered_rails|exceptions_only) / `end_date` / `seed` / `plants` / `only_template` / `derive_balances`) / plant-timeline view / unified diagram (D3+d3-force vs enhanced graphviz spike — ELK out as a JVM-dep tier) / editor primitives + forms (Account/Rail/Theme/Chain/TransferTemplate, additive build) / CLI surface / testing scope (narrowed vs the X.2-era 13-cell matrix — Studio gets PG→SQLite primary + Oracle→SQLite + SQLite→SQLite secondary, NOT a full fan-out) / open-deferred / reuse inventory / hard invariants.
- **`docs/x_4_5_design_thoughts.md`** — the iteration log that produced the SPEC. The user's problem statement + persona reframe + the back-and-forth that converged on the 5-step pipeline, the renamed surfaces (Studio + Dashboards), the Generator scopes, the etl_hook ownership boundary, the editor cascade discipline.
- **`PLAN.md`** re-cut: one Phase X.4 (Studio) replacing the original X.4 + X.5 split. ~70 sub-task checkboxes across X.4.a (foundations) → X.4.b (renderer spike) → X.4.c (diagram) → X.4.d/e/f (editor) → X.4.g (Deploy pipeline, 15 sub-items) → X.4.h (shaping panel UI) → X.4.i (additive knobs) → X.4.j (testing scope) → X.4.k (wrap to v10.0.0). Phase X.1 + X.3 also swept to `PLAN_ARCHIVE.md` (both shipped); the stale "Parallelism map" historical section trimmed.

**CLI rename ahead of v10.0.0 GA:** `serve app2 apply` becomes `dashboards`, plus a new `studio` verb that mounts both. Removed outright when Studio MVP ships — no deprecation alias (the only user is the developer wielding it).

**No customer-facing change in this tag.** v9.4.0 → v10.0.0a1 = SPEC + PLAN landing, period. Code starts on the next commits in the X.4 sub-tree.

## v9.4.0 — Phase X.3 wrap: SQLite is a first-class database dialect; `e2e-sqlite` CI cell

Minor release — the **Phase X.3 wrap**. **No customer-facing change**: the CLI's stable surface (`schema` / `data` / `json` / `audit` × `apply` / `clean` / …), `config.yaml`, and the L2 institution YAML keep their shapes; a customization carried across v9.3.0 → v9.4.0 needs no edits. SQLite-as-a-dialect (`Dialect.SQLITE` — connection plumbing, schema emit, matview-as-table refresh, seed pipeline, locked seed file) landed incrementally on 2026-05-08; this release closes the phase out.

- **`e2e-sqlite` CI cell** (X.3.g). New `ci.yml::e2e-sqlite` job (`needs: test`, plain `ubuntu-latest`, no DB service container): `./run_tests.sh up_to=app2 --dialects=sl --targets=lo`. The runner's `_setup_local_sqlite()` makes a per-invocation tempfile DB + synthesized cfg, seeds it (schema + data + refresh), then runs the `db` layer (every dataset's CustomSQL against the live SQLite + per-matview row counts + the audit PDF render/verify cycle) and the `app2` layer (the self-hosted HTMX renderer served against that SQLite, `test_html2_*`). It's the cheapest cell in the whole test grid — no DB instance, no Docker, no AWS — and turns the integrator-local persona's iteration loop into a CI gate. (Layer-1 matview-check + audit-PDF SQLite *unit* coverage — `tests/unit/test_layer1_query_sqlite.py` + `tests/audit/test_pdf_sqlite.py` — already runs in `ci.yml::test`'s default `pytest` sweep; this job is the App-2-against-SQLite cell those two couldn't cover.)
- **App 2 reads from SQLite** (X.3.e). The `_tree_fetcher` data fetcher dispatches by dialect — the SQLite arm goes through stdlib `sqlite3` (sync path) + `aiosqlite` / `_AsyncSqlitePool` (async server path). `serve app2 apply -c config.sqlite.yaml` works today.
- **Scope cuts** (recorded, not silently dropped): the planned `--sqlite PATH` shorthand on `serve app2 apply` and the "integrator's local loop" handbook walkthrough are **cut** — X.4 (the YAML editor) becomes the integrator's local-iteration front door and will own its SQLite store, so a parallel CLI flag + a CLI-only walkthrough would be obsolete on landing. Phase X.6 (the docs-positioning sweep) picks up the integrator-local-loop story once X.4/X.5 have settled its shape.

## v9.3.0 — App 2 row-level drills + cross-sheet URL-param threading; Phase X.2 wrap (self-host docs, biome lint gate)

Minor release — the **Phase X.2 wrap**. **No customer-facing change**: the CLI's stable surface (`schema` / `data` / `json` / `audit` × `apply` / `clean` / …), `config.yaml`, and the L2 institution YAML keep their shapes; a customization carried across v9.2.0 → v9.3.0 needs no edits. The work is all in the self-hosted "App 2" renderer (the `serve` group — a Phase X.2 dev/iteration surface, not the stable CLI), the test infrastructure, and the docs.

- **App 2 table rows are drillable** (X.2.u.4.e.3). The App 2 Table renderer now reads each visual's tree-level `Drill` actions off a `data-row-drills` JSON attr and makes every row clickable — left-click fires the primary drill (a `DATA_POINT_CLICK` one if declared, else the first), and a trailing "⋯" button per row opens a `ctxmenu` popover listing every drill's label (also bound to the row's right-click, for QuickSight-gesture parity). The drill navigates `target_path?param_<name>=<row cell value>` for each declared param. `ctxmenu@2.1.0` (standalone build) joins the vendored offline asset bundle, re-skinned onto the L2 theme via `widgets-theme.css`. `App2Driver` gained `drill_from_first_row` / `drill_from_first_row_via_menu` verbs; both work on both renderers, so the row-drill e2e tests are parametrized `[qs, app2]`.
- **Cross-sheet drills (and bookmarked URL state) actually narrow the destination on App 2** (X.2.u.4.e.4). The App 2 sheet-page route now threads `?param_<name>=<v>` query keys into the rendered filter form's initial state (`server.py::_apply_url_param_overrides`): a dropdown pre-selects the matching `<option>`, a `ParameterMultiSelect` pre-selects all the repeated-key values, a `ParameterNumberSpec` slider takes the value as its initial position. Because every visual loads via `hx-include="#filter-form"`, the *first* fetch already carries the value — a row-drill "walk" (or a bookmarked `?param_X=Y` URL) renders the destination filtered, no manual re-pick. (QuickSight's own URL-param write still doesn't sync its controls — a standing QS limitation, unchanged.) New `[qs, app2]` parity test confirms the parameter-anchored sheets (Money Trail chain root / Account Network anchor / L2FT Transfer Templates) expose the same populated anchor control on both renderers.
- **Phase X.2 close-outs.** `docs/reference/self-host.md` — a new handbook page covering how to run App 2 offline, what browser-side assets ship in the wheel (the 10 vendored deps with versions + roles), and the maintainer recipes (`scripts/vendor_js_deps.py --update` to bump a vendored lib; the new `scripts/build_app2_css.py` to rebuild the Tailwind stylesheet — which surfaced + fixed a stale `output.css` that was silently missing ~1.5 KB of utility classes the markup references). The JS unit harness now loads d3 from the vendored copy instead of a CDN, so `tests/js/` runs offline too. README gains a "Self-hosted renderer (App 2)" section.
- **biome JS lint folds into the test session** (the X.2.l.4 follow-on). `conftest.py::pytest_sessionstart` now runs `biome check` alongside the pyright strict gate (errors fail, warnings don't, opt out via `QS_GEN_SKIP_BIOME=1`) — so a JS-lint regression fails `pytest tests/` / `./run_tests.sh up_to=unit` / `ci.yml::test` before any test collects. `biome` stays a system binary (`biomejs/setup-biome` in CI, `brew install biome` locally — there's no official Biome PyPI package yet, tracking biomejs/biome#8818); the gate skips cleanly when it isn't on `PATH`.
- **`test_inv_filters.py::test_min_sigma_slider_shrinks_anomalies_kpi`** now skips when the deployed L2's Volume Anomalies seed produces zero z-score anomalies above the default σ (nothing for the σ-slider narrowing guard to shrink) — same skip-on-empty shape the Money-Trail-min-hop slider test already had. (e2e-only; no runtime change. Re-light by planting pair-window spikes in the demo seed.)

## v9.2.0 — 4-way cross-tool agreement gate; App 2 date-filter day-inclusivity fix

Minor release. **No customer-facing change** — the CLI's stable surface (`schema` / `data` / `json` / `audit` × `apply` / `clean` / …), `config.yaml`, and the L2 institution YAML keep their shapes; a customization carried across v9.1.0 → v9.2.0 needs no edits. The work is test infrastructure (Phase X.2.j) plus one real bug fix in the self-hosted "App 2" renderer's date filter.

- **4-way cross-tool agreement gate** (X.2.j). `tests/e2e/test_audit_dashboard_agreement.py` (was U.8.b's 3-way `expected == PDF == QS dashboard`) now compares **five anchors** per L1 invariant: `scenario_plants ⊆ direct_matview_SELECT == QS dashboard == App 2 dashboard` (`== audit PDF` for the flat-shape `drift`). For the flat-shape invariants (drift / overdraft / limit_breach) it tightens to **row-identity** — the set of `(account_id, day[, transfer_type])` key tuples, not just a count, so "same count, different rows" can't slip through. The App 2 leg reads the same seeded DB via `App2Driver.serving` + a live-DB fetcher; the direct-SQL leg (`tests/audit/_matview_extract.py`) is the ground-truth anchor every renderer should be matching. Wired into `e2e.yml::e2e-pg-browser` (push:main) and `release.yml::e2e-against-testpypi` (the prod-publish gate); the runner's `browser` layer already picks it up. Verified live: 6/6 PostgreSQL cells green against a deployed Aurora-backed dashboard.
- **App 2 date-filter day-inclusivity fix** (X.2.j.dateparity). `app2_date_filter` (`common/sql/app2_filters.py`) emitted `column <= CAST(:date_to AS DATE)`, which against a non-midnight `TIMESTAMP` column **excludes** same-day rows (`2026-05-10 14:32:01 <= 2026-05-10 00:00:00` is false) while QuickSight's analysis-level `TimeRangeFilter` (`time_granularity="DAY"`) truncates at filter-eval time and **includes** them — a real parity gap affecting the L1 Transactions sheet, Executives, and Investigation date filters (and any fuzz L2 with per-role business-day offsets). The upper clause is now `column < date_to + 1 day` — exclusive of the day *after* `date_to` (PG `+ INTERVAL '1 day'` / Oracle `+ 1` / SQLite `date(:date_to, '+1 day')`), which keeps the column untruncated so an index on it stays usable; the sentinel "match all" date moved `9999-12-31` → `9999-12-30` so `+ 1` stays in range on Oracle/SQLite. QuickSight emit is untouched (it gets `{date_filter}` → `""`). Generated at serve time — no re-seed / re-lock.
- **App 2 production validation** (X.2.j.A) — all four App 2 apps (`l1_dashboard` / `l2_flow_tracing` / `investigation` / `executives`) walked sheet-by-sheet against a full-density `sasquatch_pr` seed: every app renders, the App-Info canary is healthy, `test_html2_executives_live.py` green. The "feature parity with QuickSight, minus the bugs" claim is now exercised, not just asserted.
- **`App2Driver.table_row_count` is pagination-aware** (X.2.j.B.0) — the App 2 Table renderer paginates server-side (`_TABLE_PAGE_SIZE = 50`); `table_row_count` now reads the true total off the `.table-pager-range` `"X–Y of M"` pager instead of `len(rendered DOM rows)`, which under-counted any table over one page. (e2e-only; no runtime change.)

## v9.1.0 — App 2: one process serves all four apps + the handbook; server-side table paging/sort; browser e2e on every push

Minor release. **No customer-facing change** — the CLI's stable surface (`schema` / `data` / `json` / `audit` × `apply` / `clean` / …), `config.yaml`, and the L2 institution YAML keep their shapes; a customization carried across the v9.0.x → v9.1.0 line needs no edits. The work is in the self-hosted "App 2" renderer (the `serve` group — a Phase X.2 dev/iteration surface, not the stable CLI) plus CI.

- **`quicksight-gen serve app2 apply` serves all four real apps from one process by default** (X.2.g.5). The server was always multi-dashboard-capable; the CLI just never made "all" the default. `--app` now takes `all` (default) | `smoke` | `l1_dashboard` | `l2_flow_tracing` | `investigation` | `executives` — `all` builds the four real apps off the resolved L2 instance into one Starlette app sharing a single DB pool (`/dashboards` lists all four). Same "no-arg = all" shape as `json apply` (which already builds all four). `--stub` is only meaningful with `--app smoke` now.
- **Table visuals paginate + sort server-side** (X.2.g.5.followon + X.2.h.5). A wide table (the L1 Transactions sheet returns ~68k rows on `sasquatch_pr`) used to come back as one ~20 MB HTML fragment and freeze the browser. The tree fetcher now wraps the dataset SQL with `SELECT qs_page.*, COUNT(*) OVER () AS qs_row_total FROM (<base>) qs_page ORDER BY <col>[ DESC], 1 <LIMIT m OFFSET n | OFFSET n ROWS FETCH NEXT m ROWS ONLY>` (dialect-correct paging + column quoting; a bare-identifier guard on the sort column → falls back to `ORDER BY 1` and echoes an empty `sort_column`, so an injected `sort_column` can't reach the SQL). `bootstrap.js::renderTable` renders a pager (`X–Y of N`, prev/next) + clickable sortable headers (▲/▼ badge) and round-trips `?page_offset=N&page_size=M&sort_column=<name>:<asc|desc>`. New browser e2e `tests/e2e/test_html2_table_pagination.py` (App2-only — QuickSight does client-side virtualization, not server-side page-offset) drives the round trip via `App2Driver.smoke()` + `expect_response`.
- **The MkDocs handbook is embedded at `/docs` in the App 2 server** (X.2.i). `serve app2 apply` gains `--docs/--no-docs` (default on): when mkdocs is importable it builds the handbook into a tempdir (theme = the active L2's `theme:` block, same as `docs export`) and mounts it at `/docs` (`StaticFiles(html=True)` — directory URLs resolve to `index.html`); the `/dashboards` landing page grows a Handbook link. mkdocs missing or build-fails ⇒ serves without `/docs` + a note (the `[serve]` extra doesn't pull `[docs]`). **Additive** — the standalone `docs apply` / `docs export` / `docs serve` commands are untouched; the new internal `build_docs_site(...)` helper is *extracted* from `docs_apply`, not a rewrite. App 2 and the handbook now live in one origin, which unblocks live-`<iframe>` doc embeds (X.6.e).
- **Browser e2e runs on every push to main, and the prod-publish gate now runs the full browser tier** (X.2.i.followon). `e2e.yml::e2e-pg-browser` dropped its dispatch/cron-only gate — it now fires on push:main too (it `needs:` the fast `e2e-pg-api` tier, so a SQL/deploy break short-circuits in ~10 min before the ~30-min browser job spins up; the nightly cron stays as a no-push-that-day backstop). `release.yml::e2e-against-testpypi` gained a `playwright install webkit` step + the full browser-tier pytest run (mirrors e2e.yml's file list) — a release now blocks on a blank-dashboard regression, not just on the API structure tests (~+20 min per release; the deliberate trade).

## v9.0.3 — App 2 runs offline; the handbook site follows the active L2 theme

Patch release. All three changes are **internal** — the CLI, `config.yaml`, and the L2 institution YAML keep their shapes; a customization carried across the v9.0.2 → v9.0.3 line needs no edits. With this release the last hard blockers for the big "App 2 ships" cut (X.2.k) are cleared.

- **App 2 has zero runtime internet dependency** (X.2.p). The self-hosted dashboard renderer used to CDN-load 6 JS + 3 CSS third-party libs (htmx, d3, d3-sankey, Tom Select, Flatpickr, noUiSlider) — so `pip install quicksight-gen[serve] && quicksight-gen serve app2 apply` only worked online. Those pre-built minified dist files are now committed under `common/html/assets/vendor/{js,css}/`, shipped inside the wheel, and served from `/static/vendor/...` off the existing static mount. Provenance + the version-bump recipe live in `assets/vendor/vendor.lock` + `scripts/vendor_js_deps.py` (a stdlib-only maintainer script — `--update` re-downloads + verifies + writes); `tests/unit/test_vendor_assets.py` is a SHA256-lock test (same model as the byte-stamped seed locks) and also asserts the rendered page shell carries zero external `<script>`/`<link>` URLs. No bundler / no esbuild — the spike (`docs/audits/x_2_p_offline_bundle_spike.md`) settled on vendoring pre-built dist over a from-source build (the no-npm constraint makes vendoring d3's source tree the painful path, and pre-built dist needs no bundling). The Tailwind `output.css` was already committed + shipped.
- **The mkdocs handbook site renders the active L2 instance's theme palette** (X.2.s.2). It was rendering the bundled forest-green demo palette regardless of which L2 you built with — `docs/stylesheets/site.css` hard-coded that palette, and the per-instance theme shim only overrode a handful of Material brand vars, so the header bar / links picked up the L2 accent but the hero block, walkthrough cards, section labels, and the diagram lightbox stayed green. `site.css` now routes every color through neutral `--qs-*` design tokens (mirroring the dashboards' `DEFAULT_PRESET`), and the generated `stylesheets/_l2_theme.css` overrides those tokens from the L2 `theme:` block — so one file re-skins both Material's chrome and the handbook-specific styling. A themeless L2 falls back to the neutral navy/grey, not a persona palette. (The original report — "`docs apply --portable` drops the L2 theme" — turned out to be a misdiagnosis: portable and non-editable builds were always byte-identical here.)
- **The nightly browser-e2e job's `[e2e]` extra now carries the App 2 server stack.** When the browser e2e suite was parametrized over `[qs, app2]` it started importing `App2Driver` → `common/html/server.py` → `starlette`, but the `[e2e]` extra didn't list the `[serve]` deps — so the 2026-05-12 `e2e-pg-browser` cron went red at collection (`ModuleNotFoundError: No module named 'starlette'`). Added `starlette` / `uvicorn[standard]` / `python-multipart` / `httpx` / `aiosqlite` to `[e2e]` (kept in sync with `[serve]`).

## v9.0.2 — X.2 close-outs: dataset-param 32-element cap, `docs serve` from an installed wheel, L2FT demo enrichment

Patch release. Three self-contained fixes from the Phase X.2 punch-list — all **internal**; the CLI, `config.yaml`, and the L2 institution YAML keep their shapes (a customization carried across the v9.0.1 → v9.0.2 line needs no changes).

- **Dataset-parameter `StaticValues` defaults are 1-element sentinels, not the full declared-value list** (X.2.t.2). AWS QuickSight's `create-data-set` rejects a `StringDatasetParameter.DefaultValues.StaticValues` list with **>32 elements** — so a per-sheet dropdown defaulting to "every declared value" blew up an L2 with >32 rails / chains / templates / transfer_types / roles. The dataset SQL is now shaped `WHERE ('<sentinel>' IN (<<$pX>>) OR <col> IN (<<$pX>>))` so the 1-element sentinel default means "match everything" on load (identically for the QuickSight renderer — the `MappedDataSetParameters` bridge overrides on selection — and the self-hosted renderer — the executor splices the sentinel default, or binds real URL values to narrow). The control's *options* still carry the full declared list (AWS caps only the dataset-param default). Belt-and-braces: `common/models.py::*DatasetParameterDefaultValues.__post_init__` now raises if a `StaticValues` list exceeds 32 — the bug is unrepresentable at construction. Mirrors the pattern the L1 `account_id` / `transfer_id` dropdowns already used since Phase Y. (Outstanding: a live AWS deploy against a >32-rail L2 to confirm against `create-data-set` itself — low-risk; the SQL shape is identical to the already-deployed pattern and the `DataSetParameters` shape is unchanged, so no new rejection surface.)
- **`quicksight-gen docs serve` works from a non-editable `pip install`** (X.2.s.1). `docs serve` was running mkdocs without setting `cwd`, while `docs apply` does — and mkdocs-macros resolves its `include_dir: docs/_macros` against the process cwd, not the config file's directory. From an installed wheel (cwd = wherever the operator ran the command, no `docs/_macros`) the initial build died with `"docs/_macros does not exist"` before serving a single page. Fix: `cwd=<bundled mkdocs.yml's dir>`, mirroring `docs apply`. Regression guards: a unit test plus a `ci.yml::docs-portable-install` step that runs `docs serve` from a clean cwd under a timeout and asserts it reaches "Serving on" without the macros-path error.
- **`spec_example` (the default / CI L2) now exercises every L2FT completion outcome** (X.2.u.3.fix.demo). Added a TwoLegRail-first transfer template (`ExternalReconciliationCycle`), its leg rail, and `spec_example`'s first `chains:` entry (a Required chain child). The auto-scenario now fires template instances covering `{Complete, Imbalanced, Orphaned}` and chain instances covering `{Completed, Incomplete}`, so the L2FT Chains + Transfer Templates "Completion" filter e2e tests are strict again (were relaxed because the prior single-leg-rail template only ever fired 'Imbalanced'). Re-locked the three byte-stamped `spec_example` seed files (`postgres` / `oracle` / `sqlite`) + the two bundled fixture copies.

Still open from X.2.s: **`docs apply --portable` dropping the L2 theme** (X.2.s.2) — couldn't reproduce from code reading; needs a fresh-wheel repro to pin the failure mode. Tracked as a follow-up.

(Everything below is unchanged from the v9.0.1 notes.)

## v9.0.1 — fix: v9.0.0's release pipeline died at Tests

v9.0.0's tag never reached PyPI — the release pipeline failed at its first job (`Tests + pyright strict`), so nothing published. v9.0.1 is the same content as v9.0.0 plus the one test fix that unblocks the pipeline. **No production-code change.**

- **`test_apply_demo_database_url_auto_emits_datasource_json` — hardened.** v9.0.0's `datasource_arn_was_derived` gate (the "explicit `datasource_arn` wins" change) is correct, but the test exercising the auto-emit path passed in isolation and failed in a full-suite run: `tests/audit/test_dashboard_extract.py` sets `QS_GEN_DATASOURCE_ARN` via a module-level `os.environ.setdefault`, pytest collects that module before `tests/json/`, and the leaked env value populated `cfg.datasource_arn` from the env override — flipping `datasource_arn_was_derived` to `False`, so the auto-emit gate didn't fire and `out/datasource.json` wasn't written. The test now `delenv`s the fallback up front (same defensive pattern as the sibling skip-emit test).

(Everything below is unchanged from the v9.0.0 notes.)

## v9.0.0 — Phase Y: SQL-level parameter pushdown (QuickSight + self-hosted renderer converge)

Major version. The headline is **convergence**: the QuickSight renderer and the self-hosted (App 2 / HTMX) renderer now do filtering the same way — a `<<$paramName>>` placeholder (or a `{date_filter}` slot) in the dataset's CustomSql, substituted at fetch time. QuickSight bridges an analysis parameter into the CustomSql via `MappedDataSetParameters`; the self-hosted renderer translates the same placeholder to a `:param_name` bind. One SQL, one narrowing path, two renderers — and the rows fetched shrink at the database instead of being pulled in full and filtered in-engine. Analysis-level `FilterGroup`s are deprecated for filter intent (kept only for the universal date control and the rare highlight-without-narrowing case).

This is internal renderer architecture. **Customer-facing surfaces are unchanged** — the CLI (`schema` / `data` / `json` / `audit` × `apply` / `clean` / …), `config.yaml`, and the L2 institution YAML (`theme:` / optional `persona:` / rails / chains / accounts / limit schedules) all keep their shapes. A customization carried across the v8 → v9 line needs no YAML changes; only the generated QuickSight definitions changed. The major bump reflects the clean break in how filters are authored, not a config migration.

**What's in v9.0.0** (subsumes everything since v8.8.0a25 — the a26/a27 alphas were the build-up):

- **Filter / parameter pushdown across all four apps** (Y.2/Y.3): every per-sheet dropdown / slider / date control narrows at the DB now. Calc fields that existed only to be filtered (Recipient Fanout distinct-senders, Account Network counterparty/amount, Volume Anomalies z-score) were pushed down to real dataset columns and the calc-field declarations dropped.
- **Date-pushdown perf** (Y.6): on a narrowed (last-7-days) view vs. the full population, **−15.3% rows on the wire / −8.5% query time** overall; `l1-transactions` alone **−92%**. (The "wide-open" scenario — empty date binds, sentinel match-everything — reproduces pre-Y behavior exactly, so the delta is the win, not a measurement artifact.)
- **`app2_sql=` → `app2_date_column=`** (Y.5.a): `build_dataset(sql_template, CONTRACT, ..., app2_date_column="t.posting")` does both substitutions itself; the old two-call form (`sql_template.format(date_filter=...)` + a hand-rolled `app2_sql=`) was a footgun — operators forgot the App2 half and shipped silently-broken date filters.
- **Oracle e2e clean** (Y.7 + Y.7-followup): the e2e suite is green on all three dialects (PostgreSQL / Oracle / SQLite). Y.7-followup hardened `drop_matview_if_exists` against half-dropped Oracle matviews, pinned the audit-agreement test's module-scoped re-seed fixture to one xdist worker (`xdist_group`, so Oracle's auto-committing DDL doesn't race across workers into ORA-00955), and gave the browser e2e layer a 60s page timeout + `pytest-rerunfailures --reruns 2` so a QS-embed-render flake retries itself instead of halting the chain.
- **Explicit `datasource_arn` wins.** If `config.yaml` carries a `datasource_arn` (a pre-existing customer datasource), the deploy uses it as-is and does **not** generate/deploy a competing QuickSight datasource — even when `demo_database_url` is also set in the cfg (for the seed/demo CLI). Previously the latter case still regenerated the datasource.
- **Test-runner robustness** (the Y.2.gate work, plus Y.7-followup): the `loadgroup` xdist default moved into `tests/conftest.py` (activates only when xdist is loaded — a no-xdist env like the wheel-smoke job no longer chokes on `--dist`); `pytest-xdist` + `pytest-rerunfailures` in the `[dev]` extra.

Docs caught up (Y.9): CLAUDE.md's new "Filter authoring" section is the canonical pattern; README's "Add a filter" rewritten; the customization handbook has a "How filters work" section with the migration note.

## v8.8.0a27 — Y.7 e2e gate (PG + SQLite clean) + two e2e-test fixes

Twenty-seventh alpha. Bundles a26's content (Y.3.b + Y.5.a + Y.6) plus the
Y.7 e2e verification pass and the two fixes it surfaced:

- **`test_date_filter_narrows_every_date_sensitive_count_kpi` — fixed.**
  Y.2.h removed this test's xfail expecting the date pushdown to make it
  pass, but only validated against `sp_pg_aw` (which happened to). It
  fails on every other dialect: the test picked a 2-day narrow window
  *inside* the 90-day seed, where the baseline generator touches every
  account every day, so the Active Accounts distinct-count KPI doesn't
  shrink for any in-seed window. Moved the narrow window to ~400 days
  pre-seed → every date-sensitive count KPI narrows to ~0 → still a
  valid proof the `:date_from` bind reached the SQL.
- **`test_invariant_three_way_agreement[postgres-supersession]` — fixed.**
  The strict `dashboard_count == pdf_count` assert is structurally
  invalid for supersession: the PDF's section is a count-by-table+category
  aggregate (≈3 rows); the dashboard's "Transactions Audit" table is
  per-row (≈34 at the live data density). It "passed" at m.5.d only
  because the density then happened to give exactly 3 supersession txns.
  Gated the strict-equality assert to `invariant == "drift"` (where the
  PDF section and the dashboard table are genuinely the same shape); the
  `>= expected` producer-side asserts still cover every invariant.
  Follow-up tracked: widen to row-identity matching for the
  divergent-shape invariants.

**Y.7 status:** PG (`sp_pg_lo` + `sp_pg_aw`) clean unit→db→app2→deploy→api→browser;
SQLite (`sp_sl_lo`) clean through app2; Oracle (`sp_or_lo`) clean db+app2,
`sp_or_aw` clean db+deploy+api but the **Oracle browser layer has 7
failures + 6 errors** (structural `*_dashboard_structure_matches_tree` +
dropdown-narrow tests) — deferred to a focused Oracle-browser triage
before the v9.0.0 "Phase Y done" cut. NOT in this release pipeline's
scope (`release.yml::e2e-against-testpypi` runs PG only).

## v8.8.0a26 — Y.3.b + Y.5.a + Y.6 (Investigation calc-field pushdown, app2_date_column refactor, perf verification)

Twenty-sixth alpha. Three landings on `y-3-investigation-calc-pushdown`:

**Y.3.b — Account Network calc fields pushed into dataset SQL.** New
`ACCOUNT_NETWORK_CONTRACT` extends `MONEY_TRAIL_CONTRACT` with three
DB-computed columns (`is_inbound_edge`, `is_outbound_edge`,
`counterparty_display`) — three CASE expressions over the
`<<$pInvANetworkAnchor>>` parameter in the dataset SELECT. The
Investigation analysis tree now emits **zero** analysis-level
CalcFields; the inbound/outbound Sankey FilterGroups target real
columns; the walk-the-flow drill source reads `counterparty_display`
directly. Both QS and App2 see one shape. (Y.3.c was a no-op — Volume
Anomalies had no analysis-level CalcFields; Y.3.d/e folded into a/b.)

**Y.5.a — `app2_sql=` → `app2_date_column=` refactor.** The raw-SQL
escape hatch (10 callsites: 8 L1 + 2 Executives, each hand-rolling
`app2_sql=template.format(date_filter=app2_date_filter("col", cfg.dialect))`)
is replaced by a typed declaration: the dataset declares
`app2_date_column="col"` and `build_dataset()` does both substitutions
+ registration internally. Operators forgetting the App2 variant used
to silently produce dataset SQL that ignored the date filter — that
footgun is gone. `app2_date_filter` is now imported only by
`build_dataset()` (lazily, to avoid a circular dep).

**Y.6 — Performance verification (the headline result).** Direct
dataset-SQL timing on Aurora PG, seeded with 68,879 transactions over
90 days. Compared **wide-open** (empty `:date_from`/`:date_to`;
sentinel binds match every row — functionally equivalent to pre-Y,
which had no WHERE at all and let QuickSight narrow in-engine) vs
**narrow-7d** (analyst applies a 7-day date filter — Phase Y pushes
that bind into the dataset WHERE; the DB returns the narrowed set):

| | wide-open | narrow-7d | Δ |
|---|---:|---:|---|
| **Total rows on the wire** (36 datasets, 4 apps) | 423,757 | 358,940 | **−15.3%** |
| **Total query time** (Aurora PG, 3-run median) | 43.1s | 39.4s | −8.5% |

Time delta understates the SQL-execution win — network round-trip
dominates the small datasets. **9 datasets see ≥80% row reduction.**
Star: `l1-transactions-ds` (the L1 Transactions sheet's main table) —
**68,865 → 5,423 rows (−92%)**, **5.3s → 1.4s (−74%)**. Also:
ledger-drift / drift-timelines (90 → 8, −91%), `exec-transaction-summary-ds`
(1,182 → 110, −91%), `l1-overdraft-ds` (100 → 12, −88%),
`l1-todays-exceptions-ds` (32 → 4, −88%), `l1-drift-ds` /
`l1-limit-breach-ds` (−80%).

Non-narrowing datasets (correct): dimension-tag feeds (`l2ft-meta-values-ds`
190k rows, `l2ft-postings-ds` 67k), dropdown enumerations
(`inv-money-trail-roots-ds` 32k, `l1-tx-ids-ds` 35k), and the
Investigation threshold-pushdown datasets (`inv-recipient-fanout-ds`,
`inv-volume-anomalies-ds` — their own `pInvFanoutThreshold` /
`pInvAnomaliesSigma` pushdowns exist but a date-only scenario doesn't
fire them). None is a missed Phase-Y opportunity. Methodology +
per-dataset table: `runs/y6/summary.md` (regenerable via
`spike/y6/measure.py` + `spike/y6/diff.py`).

## v8.8.0a25 — hotfix: api-layer test catch-up for Y.2.h + Y.2.f + Y.3.a

Twenty-fifth alpha. Pure test-fixture / assertion fix; no production-code
changes vs v8.8.0a24. v8.8.0a24 reached TestPyPI but `e2e-against-testpypi`
failed on three api-layer assertions that the local `up_to=app2` chain
runner doesn't fire (api layer requires deployed AWS + auth):

- `test_exec_deployed_resources::TestExecDatasetsExist::test_dataset_count`:
  `assert len(exec_dataset_ids) == 2` was a stale hardcoded count;
  Y.2.h's active-accounts dataset split made it 3 (plus 2 App Info → 5).
  Removed (redundant — `test_all_datasets_exist` iterates the
  derived IDs and `describe_data_set`s each, the actual meaningful check).

- `test_inv_deployed_resources::TestInvDatasetsExist::test_dataset_count`:
  `assert len(inv_dataset_ids) == 5` similarly stale (Y.2.b added the
  money-trail-roots companion + the narrow-accounts companion already
  existed → 9 now). Removed for the same reason.

- `test_exec_dashboard_structure::TestFilterGroups::test_active_only_filter_pinned_to_active_visuals`:
  Y.2.h dropped `fg-exec-account-active-only` (the visual-pinned
  NumericRangeFilter was replaced by the `exec-account-summary-active-ds`
  dataset SQL baking `WHERE COALESCE(activity_count, 0) > 0` in).
  Pivoted to `test_active_only_filter_dropped_after_y2h` — guards
  against regression that brings the pinned filter back.

Same pattern as the v8.8.0a23 hotfix (`l1_dataset_ids` fixture lag).
Re-ships v8.8.0a24's content (Y.2.h + Y.2.f + Y.3.a + KPI parser
decimal handling).

Process note: api-layer e2e is `QS_GEN_E2E=1`-gated and runs against
deployed AWS, so the local chain runner doesn't fire it. Need a way
to surface api-layer breakage at `up_to=app2` time without requiring
AWS — followup tracked elsewhere.

## v8.8.0a24 — Y.2.h + Y.2.f + Y.3.a (closing out Y.2 + opening Y.3)

Twenty-fourth alpha. Three landings on `y-2-h-executives-pushdown`:

- **Y.2.h — Executives Active Accounts dataset split.** Splits
  `exec-account-summary-ds` into a date-independent snapshot (drives
  Total Open Accounts + Open-by-Type bar + the unaggregated detail
  table) plus a new `exec-account-summary-active-ds` whose SQL bakes
  `WHERE COALESCE(activity_count, 0) > 0` plus the X.2.g.1.b
  `app2_date_filter` dual-SQL pattern. Replaces the visual-pinned
  NumericRangeFilter (`activity_count >= 1`) that QS applied but
  App2 didn't — same K.4.8k / Y.2.a roots-companion split-dataset
  pattern. Re-points Active KPI + bar at the new dataset; drops
  `_wire_account_coverage_filter_groups` + `_FG_EXEC_ACCT_ACTIVE_ONLY`
  + the now-unused `_AutoSentinel` import; adds a `TimeRangeFilter`
  FG scoping the active dataset's `last_activity_date`. Drops the
  `@pytest.mark.xfail` from `test_date_filter_narrows_every_date_sensitive_count_kpi`.

- **Y.2.f — L1 universal date-range pushdown (Option B: dual-SQL).**
  Same `app2_date_filter` dual-SQL pattern as Y.2.h, applied to all
  8 L1 datasets the analysis-level `TimeRangeFilter` family scopes
  (drift, ledger_drift, drift_timeline ×2, overdraft, limit_breach,
  todays_exceptions, transactions). Each builder converts to a
  `sql_template` with a `{date_filter}` slot. QS gets `""` (zero
  behavior change — analysis-level FG still does the date filtering);
  App2 gets `app2_date_filter("<col>", cfg.dialect)` so when L1 lands
  on App2 the date binds reach the SQL automatically. Stuck-pending
  / stuck-unbundled / supersession / daily-statement intentionally
  untouched (current-state matviews; date filter would diverge from
  the audit PDF, breaking U.8.b's three-way agreement).

- **Y.3.a — Recipient Fanout `distinct_senders` pushdown.** Pushes
  the analysis-level `recipient_distinct_sender_count` CalcField down
  to a real dataset column. PG doesn't support `COUNT(DISTINCT) OVER
  (PARTITION BY)`, so the SQL uses a two-CTE pattern: `joined` (per-leg
  cross product) + `distinct_per_recipient` (GROUP BY recipient,
  COUNT(DISTINCT sender)) + JOIN back. Outer `WHERE distinct_senders
  >= <<$pInvFanoutThreshold>>` does the threshold pushdown; the
  analysis-level slider param bridges via `MappedDataSetParameters`.
  Drops `CF_INV_FANOUT_DISTINCT_SENDERS` calc field +
  `FG_INV_FANOUT_THRESHOLD` analysis-level NumericRangeFilter — both
  QS and App2 now see one shape. The PG distinct-window limitation
  was caught live by the `db`-layer SQL smoke verifier
  (FeatureNotSupported in PG), validating the Y.2.b smoke layer's
  value as a dialect-bug catcher.

Plus an incidental test-harness fix: `_kpi_text_to_int` in
`tests/e2e/test_html2_executives_live.py` now preserves decimal points
(parses as float, scales by ×100 to integer cents). Previously stripped
all non-digits, so a $57M narrowed value parsed as 5_739_816_624 looked
larger than a $161M wide value parsed as 1_613_654_694, breaking the
date-narrowing comparison on currency KPIs. Pre-existing latent bug
that Y.3.a's chain run exposed.

End-to-end verified on `sq_pg_lo` (sasquatch_pr × postgres × local):
unit + db + app2 layers all green. AW deploy chain blocked on
network-side Aurora endpoint reachability (RDS reports `available`
but TCP times out from this network) — code is proven; deploy is an
operator-environment follow-up.

Y.2 fully closed (a, b, c, e, f, g, h all done; d was design-locked
no-op in spec_example baseline). Y.3.a done; Y.3.b/d/e to follow on
next branch.

Y.2.f.2 (date day-inclusivity parity check across QS / App2 / SQL /
Audit PDF) moved into X.2.j.dateparity for handling at the 4-way
agreement gate.

## v8.8.0a23 — hotfix: l1_dataset_ids fixture catch-up for Y.2.g.0's 3 new companions

Twenty-third alpha. Pure test-fixture fix; no production-code changes vs
v8.8.0a22. v8.8.0a22 reached TestPyPI but `e2e-against-testpypi` failed
on `test_dataset_count_matches_tree` because the `l1_dataset_ids`
session fixture in `tests/e2e/conftest.py` didn't list the 3 new L1
companion datasets Y.2.g.0 added (`l1-accounts-dataset`,
`l1-tx-ids-dataset`, `l1-tx-facets-dataset`). Live tree had 19 datasets;
fixture listed 16 → assertion `19 == 16` failed → publish-to-PyPI
skipped. Locally the chain runner only ran `up_to=db` for Y.2.g
verification; this test is in the API e2e layer and only fires against
a deployed dashboard, so the fixture drift slipped past the local gate.

Re-ships v8.8.0a22's content (Y.2.g L1 categorical pushdown +
X.2.g.2.d App2 pool fix).

## v8.8.0a22 — Y.2.g (L1 per-sheet categorical filter pushdown) + App2 pool fix

Twenty-second alpha. Two related landings on `y-2-gh-l1-categorical-pushdown`.

- **Y.2.g — L1 per-sheet categorical filter pushdown.** Converts the L1
  dashboard's ~22 per-sheet filter dropdowns from
  `CategoryFilter.with_values(values=[], FILTER_ALL_VALUES)` (the X.1.g
  cold-fetch QS footgun — those lazy-fetched the column's distinct values
  from QS's `tenK-sample-values-V2` endpoint, which 404s on cold per-CI-run
  dashboards) to dataset-SQL pushdown, fixing the footgun and converging
  the QuickSight / App2 narrowing path. Operator-verified the deployed
  sasquatch_pr dashboard is **noticeably faster** — the headline win:
  narrowing now pushes into indexed `WHERE col IN (<<$p>>)` predicates
  against the matview instead of QS's lazy sample-values round-trip on
  every dropdown. Two helper shapes in
  `apps/l1_dashboard/app.py`: `_populate_pushdown_enum_dropdown`
  (`StaticValues` for bounded enums — `transfer_type`, `rail_name`,
  `account_role`, `supersedes`, `check_type`) and
  `_populate_pushdown_value_dropdown` (`LinkedValues` from a small
  companion dataset + the `('__l1_all__' IN (<<$p>>) OR col IN (<<$p>>))`
  sentinel-OR guard for data-value columns — `account_id`, `transfer_id`,
  open-set `status`, open-set `origin`). Three new companion datasets
  (`<prefix>_l1_accounts`, `<prefix>_l1_tx_ids`, `<prefix>_l1_tx_facets`)
  feed those LinkedValues. Daily Statement's account narrow also pushed
  down (single-valued `pL1DsAccount` dataset param on summary +
  transactions; dropdown options re-pointed at `DS_L1_ACCOUNTS`). The
  M.2b.7 cross-sheet drill sentinel `pL1TxTransfer` is unchanged; the new
  Transactions-sheet transfer-id filter param is `pL1TxTransferId`
  (distinct name; the two compose).
- **X.2.g.2.d — App2 cross-event-loop pool bug fixed.** Surfaced while
  validating Y.2.g's L1 pushdown locally: `serve app2 apply --app
  <tree-app>` was calling `asyncio.run(make_connection_pool(...))` to
  open the psycopg3 async pool, then `uvicorn.run()` started a *new*
  event loop and tried to use it — the pool's background filler task
  was bound to the first loop and died, surfacing as
  `error connecting in 'pool-1':` on the first request and a 500 on
  every visual-data fetch. The smoke app worked because its sync
  fetcher doesn't use the async pool. Fix: collapse pool creation +
  uvicorn into one event loop via `uvicorn.Config + uvicorn.Server.serve()`
  inside an `asyncio.run` wrapper (see `cli/serve.py`). Also wired
  `--app l1_dashboard` (mirrors the executives / investigation /
  l2_flow_tracing arms — was the dead spot that the X.2.g.4 follow-up
  would have filled). Drift KPI live-fetch confirmed
  `{"value": 10}` from the `sasquatch_pr_drift` matview after the fix.

Tech debt captured (not in this release): App2-local L1 has per-visual
render errors beyond the drift KPI — that's `_tree_fetcher.wrap_for_visual`
needing arms for L1's visual kinds, X.2.g.4 territory, NOT a Y.2.g
pushdown regression.

## v8.8.0a21 — release-pipeline fix-up + post-Y.2.gate doc sweep

Twenty-first alpha. No `quicksight_gen` package behavior changes vs a20 —
this is a CI/release-pipeline fix plus documentation tidy-up. Supersedes
v8.8.0a18, a19, a20 (all reached TestPyPI but not PyPI).

- **release.yml fix** — `gate.l.8`'s per-release L2 synthesis
  (`/tmp/release-l2.yaml`, `instance: rel_<tag>`) was threaded through
  the *deploy* steps (`schema/data/json apply`, `data refresh`,
  `schema/json clean`) but NOT through the `Run API e2e against TestPyPI
  wheel` step's `QS_GEN_TEST_L2_INSTANCE` — so the conftest fixtures
  derived expected QS resource IDs from `cfg.default_l2_instance` (unset →
  bundled `spec_example`), looked for `qs-release-<tag>-spec_example-*`,
  found nothing, and every `test_*_deployed_resources.py` /
  `_dashboard_structure.py` test failed. That's what failed v8.8.0a20's
  release run (the `e2e-against-testpypi` gate *ran* this time — the l.8
  own-concurrency-group fix worked — it just couldn't find the
  dashboards). Fixed: `QS_GEN_TEST_L2_INSTANCE: /tmp/release-l2.yaml` on
  that step.
- **Post-Y.2.gate doc sweep** — PLAN.md collapsed (983 → 688 lines): the
  done Y.2.a–e + Y.2.app2.cde + "Resume tomorrow" block fold into a short
  summary that keeps the pushdown pattern Y.2.g/h follow + the App2
  executor primitives + the open pre-existing flakes; the whole
  `### Y.2.gate` section (a–o) collapses to a 4-line "DONE — see archive"
  pointer. PLAN_ARCHIVE.md gains a `# PLAN — Y.2.gate` close-out summary.
  CLAUDE.md: `up_to=unit` time corrected to ~20s, `--coverage` added to
  the test-commands block, coverage-artifacts line notes the runner's
  `.coverage.<variant>.<layer>` files. README.md: subtitle-required note
  → "enforced at construction" not "by a test".

## v8.8.0a20 — Y.2.gate close-out (k.1.coverage + l.8 release-pipeline fix)

Twentieth alpha. Closes `Y.2.gate` (a–o all landed). Supersedes
v8.8.0a18 and v8.8.0a19 — both reached TestPyPI but not PyPI because
the release pipeline's `e2e-against-testpypi` gate kept getting
**cancelled** by a concurrency-group collision (`Y.2.gate.l.8` below
is the fix). No `quicksight_gen` package behavior changes vs a19 — this
is a test-infra + CI-pipeline release.

### Y.2.gate.k.1.coverage — runner emits coverage data behind `--coverage`

- New `RunOptions.coverage` + `--coverage` flag on `./run_tests.sh
  up_to=<layer>`. When set, every pytest layer (unit / db / app2 / api
  / browser — not `deploy`, which is a `quicksight-gen json apply` CLI
  call) runs with `--cov=quicksight_gen --cov-report=` and points
  `COVERAGE_FILE` at `<run_dir>/.coverage.<variant>.<layer>` — one
  uniquely-suffixed data file per (variant, layer).
- `ci.yml::integration-pg` now passes `--coverage`, flattens
  `runs/**/.coverage.*` to the repo root, and uploads
  `coverage-data-pg-runner`; the `coverage` aggregator job gains
  `needs: integration-pg` and picks the data up via its existing
  `coverage-data-*` glob + `coverage combine` (W.8b unchanged).
  `integration-oracle` deliberately stays out (conditional job; its
  Oracle dialect branches are unit-covered by `test_sql_dialect.py`).
- `.gitignore`: `.coverage` → `.coverage*`.
- Closes `Y.2.gate.k` / `k.1` (the literal "push:main + PR cells run
  `up_to=browser variants=full`" was superseded by `k.3`'s cost-tiering
  + the thin-wrapper migration) and `Y.2.gate.o` (the `Y.2.c+
  unblocked` terminal gate — a–o all done). The m.4 follow-up "App2
  Oracle column-casing bug" was confirmed already fixed by `Y.3.f.alt`
  (re-verified: `up_to=app2 --variants=sp_or_lo` green).

### Y.2.gate.l.8 — `e2e-against-testpypi` own concurrency group + disjoint per-release L2

**The bug it kills:** `release.yml::e2e-against-testpypi` shared the
`e2e-pg` concurrency group with `e2e.yml`'s push:main PG jobs. Cutting
a release pushes the merge-to-main and the tag at ~the same time, so
this release-e2e gate (pending behind whichever e2e.yml job grabbed
`e2e-pg` first) and a *second* push's `e2e-pg-api` both become pending
in `e2e-pg` — GitHub keeps only the LATEST pending run per group and
**cancels the older one**, which is this gate → `publish-pypi` (which
`needs:` it) is skipped → the release never reaches PyPI. v8.8.0a18 AND
v8.8.0a19 both died this way. `cancel-in-progress: false` does not
prevent it — it only protects the *in-progress* run.

**Fix:**
- `e2e-against-testpypi` moves to its own group `e2e-pg-release` (never
  the evictable pending run).
- It now deploys against a synthesized per-release L2 instance
  (`sed "s/^instance:.*/instance: rel_<safe-tag>/" tests/l2/spec_example.yaml`)
  → tables `rel_<safe-tag>_*`, disjoint from e2e.yml's `sp_pg_aw_*`
  and `cleanup-pg`'s `spec_example_*` — plus its already-distinct QS
  `resource_prefix` (`qs-release-<tag>`) — so the now-possible
  concurrent runs can't collide on QS resources or DB tables. `--l2
  /tmp/release-l2.yaml` threaded through schema / data / json apply +
  data refresh + schema clean + json clean.
- "Last one stops the cluster": `cleanup-pg::Stop PG cluster` and
  `e2e-against-testpypi::Stop PG cluster` only `aws rds stop-db-cluster`
  when `gh run list` shows no other in-progress/queued E2E or Release
  run (else leave it up; the operator's `./run_tests.sh down aws` is
  the backstop; `|| echo 1` fail-safe = don't stop on a check failure).
  Needs `actions: read` (workflow-wide in e2e.yml; on the job in
  release.yml).

## v8.8.0a19 — Y.2.d/e + App2 convergence + unit-layer prelude

Nineteenth alpha. Closes out the Y.2 L2FT dataset-SQL pushdown sweep,
brings L2FT onto the App2 (HTMX) renderer with its filter controls
auto-derived from the tree, hoists the variant-independent `unit` test
layer out of the per-matrix-cell chain, and lands the gate.l.1
cold-RDS-start fix. Supersedes v8.8.0a18 (which reached TestPyPI but
not PyPI — the same gate.l.1 race this release fixes).

### Y.2.d / Y.2.e — L2FT Chains + Transfer Templates → dataset SQL

Same pattern as Y.2.c (Rails), now for the Chains and Transfer
Templates sheets:

- **Chains** — `pL2ftChainsChain` / `pL2ftChainsCompletion` multi-valued
  dataset params push the chain-parent / completion-status filters into
  the chain-instances dataset CustomSQL (subquery wrap so the
  CASE-aliased `completion_status` is visible to the outer WHERE);
  drop the `fg-l2ft-chains-*` FilterGroups; `_populate_pushdown_dropdown`
  wires the analysis-param → dataset-param bridge + MULTI_SELECT
  dropdown. `parent_chain_name IN ('__no_match__')` sentinel for the
  empty-instances fallback.
- **Transfer Templates** — `pL2ftTtTemplate` / `pL2ftTtCompletion`
  bridge to BOTH the tt-instances Table and the tt-legs Sankey
  (replacing the `ALL_DATASETS` CategoryFilter); metadata cascade stays
  in the inner WHERE.

### Y.2.app2.cde — App2 SQL-executor parity + L2FT on App2

- `_sql_executor` honors a QS dataset parameter's static default when
  the URL doesn't supply it, so a freshly-loaded App2 page matches QS's
  initial-load render; multi-valued URL params (`?param_pX=A&param_pX=B`)
  expand to `IN (:param_pX_0, :param_pX_1, …)` bind expansion (never
  string-spliced). `DataFetcher` contract is `(VisualId, Mapping[str,
  list[str]])`.
- L2FT is the fourth app on the generic tree-fetcher path
  (`quicksight-gen serve app2 apply --app l2_flow_tracing`). New
  `make_filter_specs_for_sheet` tree-walk renders each MULTI_SELECT
  `ParameterDropdown` as a `<select multiple>` whose selected options
  serialise as the repeated-key wire shape the executor consumes —
  applied route-level so investigation / executives pick it up too.
  Browser e2e: `tests/e2e/test_html2_l2ft.py`.

### Y.2.f (parked)

Pushing the L1 universal date-range filter into dataset SQL is parked
pending feature-parity: dataset-level `DateTimeDatasetParameter`
defaults only support `StaticValues`, not `RollingDate`, so a pushdown
would shift the L1 dashboard's opening view from "last 7 days" to "all
dates" — deferred until that trade-off is worth making.

### Y.2.gate.n — `unit` test layer is now a one-time prelude

The chain runner ran the ~165s `unit` suite once *per matrix cell*
(13× on the full default). `unit` is variant-independent, so
`./run_tests.sh up_to=<layer>` now runs it once as a prelude
(`runs/<id>/_prelude/unit/`) before the matrix fans out; `up_to=unit`
returns after the prelude with no fan-out; a prelude failure aborts
before any cell dispatches; drift treats `unit` as a run-level timing.

### gate.l.7 — resilient cold-RDS bring-up in CI

New `.github/actions/ensure-rds-available` composite action only issues
`start-db-*` when the resource is exactly `stopped` and waits out all
transitional states (`stopping` / `starting` / `configuring-*` / …),
fixing the `start || true; wait` deadlock that blocked v8.8.0a18's
PyPI publish. RDS teardown moves to the final `cleanup-*` jobs.

### Test-infra

- L2FT browser tests that exercise an *optional* L2 feature (chains /
  transfer templates) `pytest.skip` cleanly when the deployed L2 lacks
  it (`conftest.require_l2ft_feature` — the only thing a valid L2
  requires is one rail + one account; chains / templates are optional).
- `test_audit_dashboard_agreement` skips the cross-dialect param for
  `aw`-target cells (which seed only one dialect's DB).

## v8.8.0a18 — Y.2.c: L2FT Rails filter pushdown into dataset SQL

Eighteenth alpha. Resumes the Phase Y SQL-pushdown sweep after the
Y.2.gate runner work landed.

### Y.2.c — L2FT Rails: rail / status / bundle filters → dataset SQL

The Rails sheet's three category filters move from analysis-level
parameter-bound CategoryFilters to **multi-valued dataset parameters**
substituted into the postings dataset CustomSQL:

- `build_postings_dataset` — projection wraps in a subquery so the
  CASE-aliased `status` / `bundle_status` are visible to the outer
  WHERE; `rail_name` joins them there for symmetry. New dataset params
  `pL2ftRail` / `pL2ftStatus` / `pL2ftBundle` (all `MULTI_VALUED`,
  defaults = all declared rails / the status enum / both bundle
  states). Metadata cascade (`pKey` / `pValues`) stays in the inner
  WHERE unchanged.
- `apps/l2_flow_tracing/app.py` — the 3 `fg-l2ft-rails-{rail,status,
  bundle}` FilterGroups are dropped; new `_populate_pushdown_dropdown`
  helper wires analysis param → dataset-param bridge
  (`MappedDataSetParameters`) + MULTI_SELECT `ParameterDropdown`
  (reusable for the upcoming Y.2.d / Y.2.e Chains / Templates slices).
- Spike (Y.2.c.0) proved the pattern safe: emptying a `MULTI_SELECT`
  bound to a dataset param reverts to the param's default (= all
  values), **not** `IN ()`. Verified against a deployed Aurora
  dashboard — rail 859→284→859, status 859→20→859, bundle 859→74→859
  (narrow on pick, revert to all on deselect-all, no SQL errors);
  metadata cascade still narrows. Quirks-log §3.5 documents the
  empty-multi-select behaviour + the disabled "Select all" toggle.
- Tests: postings-params test now asserts 5 dataset params; two new
  L2FT JSON tests cover the SQL pushdown shape + the param bridges /
  absent FilterGroups.

### Carried from the merge

- `db: retry Oracle DDL on ORA-00054 / ORA-04021 lock timeout`
  (exponential backoff, logs each retry to stderr) — addresses the
  concurrent-DDL contention surfaced by the gate.m variant matrix
  hitting the multi-tenant single-instance Oracle data dictionary.

## v8.8.0a17 — Y.2.gate.l + d/e/f/h/j/c closeouts + SQLite audit fix

Seventeenth alpha. Closes Y.2.gate.l (ephemeral AWS infra), d (test
sequencing in CLAUDE.md), e (synthetic regression audit), f
("validate then delete-or-fold" sweep), plus retroactive master ticks
for h / j / c (all sub-items already landed). Y.2.gate open masters
after this: **b** (b.14.* runner ergonomics + b.15.lint.dict-vs-mapping
deferred) and **k** (k.1.coverage Phase 3, blocked on this merge +
the new CI infra activating) and **n** (terminal gate).

### gate.l — Ephemeral AWS infra (start/stop both ways)

- `common/aws_rds.py` — thin boto3 RDS wrapper (start/stop/get_status,
  idempotent on InvalidDB*StateFault). On the boto3-direct lint
  allowlist (5th known wrapper). Expanded `RdsStatus` enum to cover
  `upgrading` / `backing-up` / `configuring-*` / failure states
  (surfaced live: Oracle in `upgrading` was showing `unknown`).
- `_dev/runner.py` — `cmd_up` / `cmd_down` / `cmd_status` fully
  implemented. `up aws` starts the cfg-declared cluster + instance and
  polls until `available`; `down aws` stops them (async, no poll);
  `status [--cost]` shows local Docker + AWS RDS state with rough
  hourly estimates (only literal `stopped` gets storage-only price —
  `upgrading`/`starting`/etc. bill compute). Loads cfg via the
  existing `_resolve_seed_config` discovery + injects `AWS_PROFILE`
  from `cfg.auth.aws_profile` so long-lived IAM keys flow through.
- `common/config.py` + `env_keys.py` — `aws_pg_cluster_id` +
  `aws_oracle_instance_id` cfg fields; `QS_GEN_AWS_*_ID` env overrides.
- `_probe_aws_rds_running` (gate.l.3) — new dispatch probe wired into
  `_LAYER_DEPS` for `deploy` / `api` / `browser`. Refuses dispatch
  with "Run `./run_tests.sh up aws` first" when the cfg-declared
  cluster ≠ `available`, *before* container spin-up — vs the old
  failure mode of `psycopg.connection refused` 5 min into the deploy
  step. Skipped when cfg fields unset (opt-in shape).
- CI workflows (gate.l.1) — `e2e.yml::e2e-pg-{api,browser}`,
  `e2e.yml::e2e-oracle-api`, `release.yml::e2e-against-testpypi` each
  gain a pre-deploy `aws rds start-db-{cluster,instance}` +
  `if: always()` post-cleanup `stop-db-{cluster,instance}` step,
  gated `if: env.X != ''` on `secrets.QS_GEN_AWS_PG_CLUSTER_ID` /
  `_ORACLE_INSTANCE_ID` (set → activates; unset → pre-gate.l shape).
- Provisioning runbook at `docs/audits/y_2_gate_l_ci_aws_provisioning.md`
  (manual `qsgen-ci-aurora` + `qsgen-ci-oracle` creation, IAM
  additions for the OIDC role + the local IAM user, GH secret setup).
  `docs/audits/_iam/quicksight-gen-local-policy.json` extended with
  the `RDSLifecycle` statement.
- 16 new unit tests (boto3 mocks). 1312 unit suite green; pyright
  strict clean.

### gate.d — Test sequencing locked in CLAUDE.md

CLAUDE.md `Test sequencing + git hooks` section gained the
prescriptive "Never invoke `pytest tests/e2e/` directly for layered
work" rule (Y.2.b SQL bug as the cost example) + a pre-dispatch
probes paragraph for gate.l.3. Memory mirror at
`feedback_test_layer_chain.md`.

### gate.e — Synthetic regression audit

Three planted-bug experiments verified live: pyright violation → halts
at `unit`; failing unit test → halts at `unit`; SELECT-alias-in-WHERE
SQL bug → halts at `db` via `test_dataset_sql_smoke.py`. Audit at
`docs/audits/y_2_gate_e_synthetic_regression.md` carries the
planted-bug shapes, observed outputs, replay incantations, and a
coverage matrix mapping each bug class → catch layer → mechanism.

### gate.f — "Validate then delete-or-fold" closeout

`run_e2e.sh` deleted — bare `pytest tests/e2e` invocation directly
violated gate.d, plus a dead `--harness` flag (test file removed in
f.9). README + CLAUDE.md updated to `./run_tests.sh up_to=<layer>`
examples. `scripts/` now: 1 file (`dump_top_queries.py` thin shim,
kept until k.6 retires CI's direct calls).

### SQLite audit-PDF date handling

Surfaced by the gate.e chain doing its job: the SQLite cells died at
the db layer in `test_audit_pdf_render_verify.py` — SQLite returns
DATE/TIMESTAMP columns as ISO strings (`connect_demo_db` opens SQLite
without `detect_types`), but the audit module assumed `datetime`
objects (`.toordinal()` on a str in the daily-statement sort;
`.strftime()` on a str in the transaction-walk tables). New
`_coerce_to_date` / `_coerce_to_datetime` helpers in
`cli/audit/__init__.py` replace the 8 ad-hoc
`.date() if hasattr(...) else ...` sites + wrap the 4 `posting=r[N]`
construction sites. Not a gate.l regression — `test_audit_pdf_render_verify.py`
landed in a16; the full matrix just runs a SQLite cell with a
drift-heavy seed which exercises the daily-statement walk.

### Known reds (pre-existing, filed as follow-ups)

The first full-matrix `./run_tests.sh up_to=browser` run surfaced
two classes of pre-existing failure unrelated to this release:

- **Oracle concurrent-DDL contention** — `sq_or_lo` / `sp_or_aw` /
  `sq_or_aw` hit `ORA-00054` / `ORA-04021` lock-timeout at
  `schema apply` when sibling Oracle cells run DDL against the same
  multi-tenant single-instance Oracle. Runner-side follow-up:
  serialize Oracle cells (or retry DDL on lock-timeout).
- **AW PG browser** — `test_l2ft_*_dropdowns` (backlog: rewrite or
  delete the L2FT cascade test), `test_exec/inv_dashboard_structure_matches_tree`
  (likely QS silent-failure on the shared account — usually clears on
  redeploy), `test_audit_dashboard_agreement[postgres-supersession]`
  (known render flake noted in m.4.f).

## v8.8.0a16 — Y.2.gate.k.1.absorb-audit + thin-wrapper + k.6 closeout

Sixteenth alpha. Closes Y.2.gate.k.1.absorb-audit (Phase 2.5),
k.1.thin-wrapper (Phase 4), and k.6 (workflows as runner wrappers).
Only k.1.coverage (Phase 3) remains under k.1.

### k.1.absorb-audit — audit-PDF render+verify in runner db layer

New `tests/e2e/test_audit_pdf_render_verify.py` wraps
`quicksight-gen audit apply --execute` + `audit verify` as pytest
that reads `QS_GEN_CONFIG` + `QS_GEN_TEST_L2_INSTANCE` from the
variant subprocess env. Wired into the runner's `db` layer dispatch
alongside the SQL smoke + row-count tests.

The runner's `db` layer now dispatches three e2e files in one shot:
- `test_dataset_sql_smoke.py` — every dataset's CustomSQL → live DB
- `test_demo_apply_row_counts.py` — ≥1 row per named matview
- `test_audit_pdf_render_verify.py` — audit render + provenance
  fingerprint verify cycle

All three pick up the variant's synthesized prefix
(`<spec.name>_*`) automatically — no more workflow-side prefix
mismatches like the one that broke v8.8.0a15's Phase 1 proof.

Local CI-mode verify against transient `postgres:17` AND
`gvenzl/oracle-free:23-faststart` containers — both passed (43
db-layer tests in ~9s on each dialect).

### k.1.thin-wrapper + k.6 — workflow YAMLs become runner wrappers

`ci.yml::integration-pg`, `ci.yml::integration-oracle`,
`e2e.yml::e2e-pg-api`, `e2e.yml::e2e-pg-browser`,
`e2e.yml::e2e-oracle-api` all migrated. Each job now boils down to:

```yaml
- install deps
- (e2e.yml: configure AWS creds via OIDC)
- generate per-job config.yaml
- ./run_tests.sh up_to=<layer> --variants=<spec.name>
- upload runs/ artifact (per-cell logs + db-perf + manifest)
- cleanup
```

Net diff in e2e.yml: -100 lines (62 added, 160 removed). ci.yml's
two integration jobs collapsed by similar margins. Per-cell
artifacts (cmd/stdout/stderr/timings + db-perf top-queries +
manifest) flow through `runs/<run-id>/<variant>/<layer>/`
uniformly — local triage shape == CI triage shape (closes the
contract laid down by gate.k.7).

`cleanup-pg` + `cleanup-oracle` stay as separate
belt-and-suspenders jobs — they catch the runner-died case
(GHA hard-timeout / OOM / manual cancel) the per-job `if: always()`
step can't.

### release.yml unchanged

`release.yml` keeps its own publish-side shape. Its Tests/Smoke
cells predate the runner and are disjoint from CI test
orchestration; the runner pattern doesn't apply.

## v8.8.0a15 — Y.2.gate.k.1+k.6 spike + Phase 1 wedge

Fifteenth alpha. Lands the runner CI-mode unblocker for k.1 + k.6,
plus the Phase 1 proof point that uses it.

### Spike: runner CI-mode (`Y.2.gate.k.1+k.6`)

`QS_GEN_RUNNER_CI` typed EnvVar. When set, `setup_variant` skips
Docker container spin-up for `lo` targets and assumes the DB is
already reachable via `QS_GEN_DEMO_DATABASE_URL`. Loud-fails via
`EnvVarRequired` if CI mode is set but the URL isn't.

Why: the runner's testcontainers-based Docker spin-up conflicts
with GHA `services:` blocks (port collisions, double cost, no
shared health-check). Without a detection mode, k.1 + k.6 (rewire
CI workflows to invoke the runner) couldn't move forward. Audit
doc: `docs/audits/y_2_gate_k_runner_ci_mode_spike.md`.

4 unit tests cover the contract: `pg/lo + url`, `or/lo + url`,
`pg/lo + missing url → loud fail`, `aw + ci-mode → unchanged
passthrough`.

### Phase 1 wedge: `ci.yml::integration-pg` uses the runner

Replaces the prior manual `schema apply` + `data apply` + `data
refresh` + `json apply` + `pytest test_dataset_sql_smoke.py`
chain with a single runner invocation:

```yaml
env:
  QS_GEN_RUNNER_CI: "1"
  QS_GEN_DEMO_DATABASE_URL: "postgresql://..."
  QS_GEN_CONFIG: /tmp/ci-pg.yaml
  QS_GEN_TEST_L2_INSTANCE: tests/l2/spec_example.yaml
run: ./run_tests.sh up_to=db --dialects=pg --targets=lo
```

Per-cell artifacts land at
`runs/<run-id>/sp_pg_lo/db/{cmd,stdout,stderr}.log` for triage
parity with local invocations.

`test_demo_apply_row_counts.py` + audit-PDF render/verify stay as
separate workflow steps for now (k.1.absorb queues promoting them
into the runner's default `db` set as Phase 2).

Local verify against a transient `postgres:17` container:
- unit layer: 2119 passed, 76 skipped in 13.45s
- db layer (test_dataset_sql_smoke.py): 37 passed in 7.32s
- Total wall-clock: ~25s (vs the prior chain's ~5 min).

### Test isolation hotfix

`tests/json/test_cli_json.py::test_apply_no_demo_database_url_skips_datasource_emit`
was depending on `QS_GEN_DEMO_DATABASE_URL` being absent from
the env. CI mode now sets that var globally, surfacing the
pre-existing isolation gap. Fix: `monkeypatch.delenv` at the
start of the test.

## v8.8.0a14 — Y.2.gate.h hotfix + Y.2.gate.k.5/k.7

Fourteenth alpha. **Re-publishes a13's gate.h closeout** (a13 + a12 both
failed to publish to PyPI: a12's pipeline was concurrency-cancelled
when a13's tag pushed; a13 itself failed at the `test_typing_smells`
lint gate because the new h.5 tests in `tests/unit/test_config_loader.py`
called `monkeypatch.delenv("QS_GEN_AWS_ACCOUNT_ID", ...)` with bare
string literals instead of going through the typed EnvVar registry).

### Hotfix

- `tests/unit/test_config_loader.py`: 6 `monkeypatch.delenv()` calls
  now reference `QS_GEN_AWS_ACCOUNT_ID.name` etc. via imports from
  `quicksight_gen.common.env_keys` — satisfies the AST lint that
  catches EnvVar-registry bypass.

### Y.2.gate.k.5 — pre-push git hook

`.githooks/pre-push` runs `./run_tests.sh up_to=db --dialects=pg
--targets=lo` (~30s on local-pg) before any push goes through.
Operator opts in once via `git config core.hooksPath .githooks`;
skippable per-push via `git push --no-verify` (discouraged). Hook
no-ops on detached HEAD or branches missing the runner so it
doesn't break legacy-branch pushes.

### Y.2.gate.k.7 — failure surface parity (documentation)

CLAUDE.md gains a `Test sequencing + git hooks` section locking
the contract that CI's failure shape matches the local runner's:
same `runs/<run-id>/<variant>/<layer>/{cmd,stdout,stderr,timings}`
artifact paths, same exit codes (`EXIT_NEEDS_OPERATOR=2` / `EXIT_FAILURE=1`),
same per-layer perf dump (`db-perf/top-queries.md`). No "decode the
GH log" step — the artifact set IS the local triage shape.

## v8.8.0a13 — Y.2.gate.h: cred auto-discovery audit + close-out

Thirteenth alpha. **Closes Y.2.gate.h.** Audit found h.2-h.5 implementations
all already landed via h.1 + h.6 + i.x scaffolding (the cfg.AuthConfig +
EnvVar registry pattern from earlier work covers them). Remaining
deliverable was documentation + a unit test for h.5's loud-fail contract.

### Audit findings

- **h.2 (DB connection strings cfg-driven)**: every consumer reads
  `cfg.demo_database_url`. For local-pg/oracle/sqlite variants the
  runner spins a container and injects `QS_GEN_DEMO_DATABASE_URL=
  <container-url>` into that variant's subprocess env. For aw target,
  cfg yaml is the source of truth.
- **h.3 (AWS account/region/partition cfg-driven)**: `cfg.aws_account_id`,
  `cfg.aws_region`, `cfg.partition` (auto-derived from region). Loader
  honors `QS_GEN_AWS_ACCOUNT_ID` / `QS_GEN_AWS_REGION` env overrides.
- **h.4 (tunables defaulted)**: every `QS_GEN_*` / `QS_E2E_*` tunable
  EnvVar is `optional=True` with sensible defaults applied by consumers.
  `QS_GEN_FUZZ_SEED` rolls fresh per invocation; Playwright timeouts
  default in helpers; `QS_E2E_IDENTITY_REGION` defaults to us-east-1.

### h.5 — loud-fail on missing cfg, with operator-actionable messages

Implementation already landed: `load_config` raises `ValueError("Missing
required configuration: ...")` with the missing field names AND the
env-var fallbacks (`QS_GEN_AWS_ACCOUNT_ID`, etc.) so the operator
knows both how to fix in YAML AND the env-override alternative.
`connect_demo_db` raises with "set it in your config YAML or via
QS_GEN_DEMO_DATABASE_URL." Runner catches both → `EXIT_NEEDS_OPERATOR=2`
with the message bubbled to stderr.

**3 new unit tests** in `tests/unit/test_config_loader.py` lock the
contract: missing aws_account_id → ValueError naming key + env-var
fallback; missing datasource_arn-without-demo_url → same shape; demo_
database_url-set → datasource_arn auto-derived (no loud-fail).

### Documentation

CLAUDE.md gains a `Cfg precedence + tunable defaults` section under
the existing Auth block covering all 4 items.



Twelfth alpha. **Closes Y.2.gate.f.** ~5300 lines net deleted across
17 harness files; the legacy "layer 8" e2e harness lane is gone, with
its assertions now covered by the merged 6/7 layer running against
per-cell variant deploys.

### What dropped

- 9 `tests/e2e/_harness_*.py` modules (`_browser`, `_cleanup`, `_deploy`,
  `_exec_assertions`, `_failure_dump`, `_inv_assertions`,
  `_l1_assertions`, `_l2ft_assertions`, `_seed`).
- 8 `tests/e2e/test_harness_*.py` files including the 913-line
  `test_harness_end_to_end.py` (THE actual layer-8 lane). 7 of the 8
  were unit tests of the harness helpers themselves — only had value
  while the helpers existed.
- `tests/e2e/failures/` historical-artifact directory.

### What stayed

- `tests/e2e/_harness_html2.py` — App2 fixture infra (NOT layer-8;
  3 import sites in `test_html2_*` tests).

### Forced lifts (landed first so cmd_sweep + the audit test don't break)

- **`quicksight_gen/_dev/cleanup.py`** — new module. Lifted
  `sweep_qs_resources_by_tag` + `_collect_resources_matching_tag`
  from `_harness_cleanup.py`. `cmd_sweep` rewired to import directly;
  the `sys.path` + `importlib` dance + an unnecessary cast both gone.
- **`tests/e2e/_seed_helpers.py`** — new module. Lifted `apply_db_seed`
  from `_harness_seed.py` (only consumer is
  `test_audit_dashboard_agreement.py`). `build_planted_manifest` did
  NOT lift — harness-specific.

### Net delta across the gate.f sweep (a10 → a12)

`scripts/` shrunk from 8 files to 1 (`dump_top_queries.py` kept for
CI). `tests/e2e/_harness_*.py` shrunk from 10 modules to 1 (`_html2`
kept for App2). `tests/e2e/test_harness_*.py` shrunk from 8 files to
0. CLI scripts that fed unique value either folded into the runner
(`f.4` perf dump, `f.5` `--keep-on-failure` flag, `f.8` `sweep`
subcommand) or had their helpers moved to a typed module the runner
imports cleanly. **All 10 of `Y.2.gate.f` are ticked.**



Eleventh alpha. Three more `Y.2.gate.f` subitems folded into the runner.
9 of 10 done; only **f.9 (drop layer 8 — 18 harness files)** remains and
will land separately on a fresh slice. Pure runner-side automation; no
behavior changes for the operator outside the new flag.

### Folded into runner

- **f.4 — `scripts/dump_top_queries.py` → runner per-cell auto-dump.**
  Added `_dump_top_queries_for_variant(spec, variant_env, run_dir,
  terminal_prefix)` to `_dev/runner.py` (sibling of `teardown_variant`).
  Wired into `_run_one_variant`'s `finally` — fires after every chain
  that touched a DB layer. Runs on success AND failure so triage always
  has the perf signal. Output: `<run_dir>/<spec.name>/db-perf/top-
  queries.md`. Best-effort throughout: connection / query / format
  failures all degrade to a `format_skipped` marker via
  `_dev.perf.format_skipped`. SQLite skipped cleanly. Filter narrows to
  the L2 instance prefix so unrelated workloads on the shared DB get
  dropped. Slimmed `scripts/dump_top_queries.py` to a thin CLI shim
  that delegates to `_dev.perf` (~150 lines of duplicate helper code
  dropped). Script kept for now since `e2e.yml` still calls it directly
  (gate.k.6 retires those CI invocations when the workflows move to
  runner).
- **f.5 — `scripts/harness_manual_deploy.py` → DELETED + `--keep-on-
  failure` runner flag wired.** The flag was scaffolded in `RunOptions`
  + argparse already (since c.7) but unwired. Wired the suppression
  into `_run_one_variant`'s `finally`: chain failure + `--keep-on-
  failure` → skip `teardown_variant` + print operator-actionable
  guidance ("container LEFT UP; clean up later via `docker stop
  <name>` or `./run_tests.sh sweep`"). Default behavior (no flag, OR
  chain succeeded) tears down as before. 3 unit tests cover the
  contract. Deleted the standalone script — it depended on `_harness_*`
  modules f.9 will delete; the runner flag covers the local-container
  case; QS-deploy inspection goes through the e2e tests directly with
  existing skip-cleanup mechanisms.
- **f.8 — `scripts/sweep_harness_orphans.py` → DELETED.** `cmd_sweep`
  already lives in `_dev/runner.py` (added at c.9). Standalone script
  removed. `cmd_sweep` still imports `_harness_cleanup` helpers via
  `sys.path` trick — the lift to `quicksight_gen/_dev/cleanup.py` is
  forced by f.9 (which deletes the `_harness_*` modules) and lands as
  part of that.

### Net delta

`scripts/` shrunk from 8 files (pre-v8.8.0a10) to 1 file (`dump_top_
queries.py`, kept for CI). Runner now auto-captures perf data per
cell + carries an opt-in keep-state flag.



Tenth alpha. Knocks out 6 of 10 `Y.2.gate.f` "validate, then delete-or-fold"
sweeps over `scripts/` + `tests/integration/`. Pure cleanup (no behavior
changes); the wheel + CI surface gets simpler; ~1100 lines of dead /
duplicated infrastructure removed. f.4 (dump_top_queries fold), f.5
(--keep-on-failure runner flag), f.8 (sweep subcommand), f.9 (drop layer 8
harness as a distinct lane) carry into v8.8.0a11+ on the same branch.

### Folded into pytest

- **f.1 — `tests/integration/verify_dataset_sql.py` → DELETED.** The pytest
  twin (`tests/e2e/test_dataset_sql_smoke.py`) was IMPORTING `_smoke_one`
  from the CLI script, so it wasn't a clean duplicate. Lifted the helpers
  (`_format_value`, `_resolve_default`, `_substitute_qs_params`, `_wrap_smoke`,
  `_custom_sql`, `_smoke_one`) directly into the pytest test module, then
  deleted the CLI. CI's 2 invocations (PG + Oracle) rewired to call pytest
  with `QS_GEN_E2E=1` + cfg/L2 env overrides.
- **f.2 — `tests/integration/verify_demo_apply.py` → FOLDED + DELETED.** New
  pytest test at `tests/e2e/test_demo_apply_row_counts.py` (parametrized
  over the 4 smoke matview suffixes). Cfg-driven dialect dispatch via
  `connect_demo_db` so PG / Oracle / SQLite all share the same test.
  Dropped the legacy exact-counts arm — only spec_example had locked counts
  and CI was already calling --smoke. Empty `tests/integration/` directory
  removed.

### Deleted (dead / duplicated / unneeded)

- **f.3 — `scripts/bake_sample_output.py` → DELETED.** Per user direction
  ("unneeded") + locked redundancy: published wheel + docs site already
  give evaluators full access to the generated JSON shape via
  `pip install quicksight-gen && quicksight-gen json apply -c ...`.
  Removed `release.yml::bake-sample` job (35 lines), `bake-sample` dep
  from `publish-testpypi.needs`, the out-sample artifact download in
  `github-release`, and `dist/out-sample.zip` from the release files list.
  Next release won't ship out-sample.zip.
- **f.6 — `scripts/m2_6_verify.py` + `m2_6_verify.sh` → DELETED.** The
  deploy + plant + per-invariant assertion flow is now covered by
  `tests/e2e/test_harness_*` (live AWS deploy + assert) plus the M.2c.* L1
  e2e tests. Updated 6 stale references in `L1_Invariants.md` + the
  `customization.md` "Verify with..." section now points at the test
  runner instead of the deleted script.
- **f.7 — `scripts/qs_substitution_probe.py` → DELETED.** Y.1.o-vintage
  manual triage tool for QS runtime SQL behavior (inspect/snapshot/diff).
  No callers, not in CI, doesn't gate anything. The fold-into-runner
  option (per-run pg_stat_statements diagnostic dump) overlaps with f.4's
  dump_top_queries future plan — re-derive there if needed.
- **f.10 — `scripts/p9_deploy_verify.sh` + `scripts/p9_e2e.sh` → DELETED.**
  Closes the P.9f.d "re-run 4-cell e2e matrix" task as runner-subsumed:
  the variant matrix (`m.1+m.2+m.3` LANDED v8.8.0a6) covers all 4 cells
  natively as `{sp,sq}_{pg,or}_aw` across api+browser layers, and the
  per-run capture (`runs/<run-id>/<variant>/<layer>/`) carries enough
  signal for failure triage without bespoke re-run tooling.



Ninth alpha. Closes the m.5.d AW-chain blocker on the Oracle local +
Aurora variants by fixing three independent Oracle-driver / Oracle-SQL
quirks in the App2 (HTMX dialect) execution path. Each was a single
narrow defect, but they stacked: fixing one surfaced the next. Chain
runner sp_or_lo (local Oracle container) AND sp_or_aw (operator's
Aurora Oracle) both green through the db layer.

The Y.3.f umbrella ("emit dialect-correct identifier case") was
reduced to the narrowest fix that unblocks App2; the broader case-
folding sweep (would require ~30+ analysis-side column-ref sites) is
deferred. See `spike/y3g/findings.md` for the SQL-builder-library
spike that preceded this decision.

### Fixes

- **`common/html/_visual_sql.py::wrap_for_visual` quotes column
  refs** (Y.3.f.alt.1). `_dim_sql` and `_measure_sql` now produce
  `"col"` instead of bare `col`. Fixes the App2 Oracle bug where the
  wrapper's lowercase-quoted aliases get referenced unquoted →
  Oracle case-folds to UPPERCASE → no match → `ORA-00904: "ACCOUNT_ID":
  invalid identifier`. KPI / BarChart / LineChart / Sankey wrap
  branches all flow through the two helpers; one fix covers every
  visual kind. PG and SQLite are unaffected (both case-insensitive
  to quoted identifiers in this context). 7 unit tests in
  `tests/unit/test_visual_sql_wrap.py`.
- **`common/sql/app2_filters.py::app2_date_filter` is dialect-aware**
  (Y.3.f.alt.4a). Oracle's session `NLS_DATE_FORMAT` defaults to
  `DD-MON-RR`; `CAST(string AS DATE)` honors that and rejects ISO-8601
  strings with `ORA-01847`. Switch Oracle to `TO_DATE(..., 'YYYY-MM-DD')`
  with explicit format (bypasses NLS); keep PG on `CAST AS DATE`
  (parses ISO natively); SQLite uses plain TEXT comparison (no native
  DATE type, lex order works). 11 unit tests in
  `tests/unit/test_sql_app2_filters.py`.
- **`common/html/_sql_executor.py::execute_visual_sql_async` uses
  explicit cursor on Oracle** (Y.3.f.alt.4b). oracledb async
  `conn.execute()` returns `None` (executes against an internal
  cursor we can't read back), unlike psycopg + aiosqlite which
  return the cursor from the one-shot form. Add an Oracle branch
  that calls `cur = conn.cursor(); await cur.execute(...)`.
  Pyright-strict friendly via inline `cast(Any, ...)` with WHY
  comments — per-driver async cursor union has no shared Protocol
  for fetchall/description/close.

### Spike

- **`spike/y3g/`** — SQL builder library evaluation (SQLAlchemy Core
  2.0.49 + sqlglot 30.7.0). Both ported the Account Network dataset;
  per-dialect output diffed against current emitter baseline. **Decision:
  stay on strings + `common/sql/dialect.py` helpers.** sqlglot transpile
  was the most attractive option but Oracle JSON path is broken (emits
  non-existent `JSON_EXTRACT`) and SQLite date arithmetic transpiles to
  unsupported `INTERVAL`. Patching either gap reinvents `dialect.py`.
  Re-spike if helper count grows past ~60 (currently ~40), SQLite drops
  from the matrix, or a feature genuinely needs cross-dialect
  transformation. Full writeup in `spike/y3g/findings.md`.

## v8.8.0a8 — Hotfix: tests/conftest.py imports _dev unconditionally

Eighth alpha. Hotfix re-cut for v8.8.0a7 after the Release workflow
got past Tests (the playwright fix worked) and Build, then died at
`Smoke test wheel` with `ModuleNotFoundError: No module named
'quicksight_gen._dev'`.

The #741 conftest patch from v8.8.0a6 unconditionally
`from quicksight_gen._dev import runner` to monkeypatch
`runner.RUNS_DIR` for runs/ isolation. But `_dev` is excluded from
the customer wheel
(`pyproject.toml::tool.setuptools.packages.find::exclude`), so the
smoke test job — which installs the wheel into a fresh venv and runs
unit tests — crashed at `pytest_configure` before any test ran.

### Fix

- **`tests/conftest.py`** — wrap the `from quicksight_gen._dev import
  runner` in `try / except ImportError: return`. If `_dev` is absent,
  no test reachable from the wheel can call `runner.main`, so there's
  nothing to guard against — the patch is a no-op.

Latent bug: #741 was added in the gate.m close-out tagged as v8.8.0a6,
but v8.8.0a6's release was already broken on the upstream playwright
issue and never reached Smoke test wheel. v8.8.0a7 fixed playwright
and exposed this one.

## v8.8.0a7 — Hotfix: release.yml missing playwright install

Seventh alpha. Hotfix re-cut for v8.8.0a6 after the Release workflow
failed at the `Tests + pyright strict` gate with 58 webkit-launch
errors (`tests/js/test_render_table.py`). Code on the v8.8.0a6 git
tag is unchanged; what was missing was the `playwright install
--with-deps webkit` step in `.github/workflows/release.yml::tests` —
`ci.yml` had it (added under X.2.a.2 alongside the JS unit harness)
but `release.yml` was never updated to mirror. Latent gap surfaced
on the first tagged release after the JS harness landed.

### Fix

- **`.github/workflows/release.yml`** — added the `Install Playwright
  WebKit browser` step right after `uv sync`, mirroring `ci.yml::ci`'s
  step (with a WHY comment explaining the parity).

The v8.8.0a6 git tag remains in place as historical record (no PyPI
artifact, broken-release-publish-pipeline). v8.8.0a7 is the publishable
re-cut.

## v8.8.0a6 — Y.2.gate.j + Y.2.gate.m close-out (variant matrix + parallelism)

Sixth alpha. Closes both `Y.2.gate.j` (parallelism + iteration ergonomics)
and `Y.2.gate.m` (variant matrix composition). The runner now holds the
full design intent: the 13-cell `scenario × dialect × target` matrix
runs in true parallel (`asyncio.gather` across cells × pytest-xdist
within layers), Oracle containers persist across runs, and tests stop
polluting the operator's `runs/` dir.

### What ships

#### Y.2.gate.j — Parallelism

- **`j.5` — Oracle container reuse via per-cell stable name.** Each
  Oracle cell spawns a `quicksight-test-oracle-<spec.name>` container
  with a pinned `oracle_password`. Subsequent runs adopt via docker-py
  `containers.get(name)` and reconstruct the URL from the host port.
  PG containers stay ephemeral. `_PersistentContainerHandle` makes
  `teardown_variant` a no-op for Oracle so the container outlives the
  invocation. Operator owns the lifecycle (`docker stop ...` until
  gate.l.2's `down` verb lands).
- **`j.6` — Within-layer pytest defaults to `-n auto`.** Earlier draft
  was serial-by-default for unit/db/app2 (api/browser already had
  auto). Operator pins via `--parallel=N`.
- **`j.7` — `--only=<expr>` documented + ticked.** Argparse + `-k`
  pass-through were already wired; CLAUDE.md + `--help` now surface
  it.
- **`j.8` — `--skip-cheap` chain-level integration tests added.** The
  helpers + dispatch short-circuit were wired under `b.8.impl`; now
  has end-to-end coverage proving the cache actually short-circuits.

#### Y.2.gate.m — Variant matrix

- **m.5.c — Full matrix wall-clock measured.** With j.5+j.6 in place
  the matrix runs cleanly in parallel: lo cells through unit + db +
  app2 ≈ 50–100s + 60–80s + 80–100s per cell, wall-clock for the
  9-cell lo subset ≈ 4 min. Aw cells extrapolate to 10–15 min each
  through deploy + api + browser. The number — proving parallel
  scaling works — is satisfied; full 13/13 green re-attempt deferred
  to post-Y.3.f (Oracle dialect-correct case).
- **m.5.e — Per-variant artifact path collision check passed.**
  9 distinct cell dirs under `runs/<id>/`, no collisions. The m.4.f
  spec.name uniqueness invariant is empirically confirmed.
- **Hard-cut from legacy `local-pg` / `local-oracle` / `local-sqlite`
  / `default` variant names** (m.2 LOCKED 2026-05-08). Sub-flags
  `--scenarios` / `--dialects` / `--targets` or pin via
  `--variants=<sc>_<di>_<ta>`.

#### Four m.5 fix-ups (live-matrix-surfaced bugs)

- **`j.5.fix` — Per-cell Oracle container name + soft-fail
  asyncio.gather.** Two Oracle cells racing on
  `containers.create(name="quicksight-test-oracle")` produced a 409
  Conflict; the unrelated cell that crashed propagated through
  `asyncio.gather` killing siblings. Fixed both — per-cell name +
  `return_exceptions=True`.
- **`j.5.fix2` — Quiet typing-smell lints in j.5 commit.** Bare
  `# type: ignore` + `qs-gen-` literal both surfaced under matrix
  unit layer.
- **`j.6.fix` — Pin fuzz seed via `pytest_configure`.** Without it,
  `tests/data/test_l2_seed_contract.py:76` runs
  `secrets.randbits(32)` at module import per worker process; xdist
  workers register different parametrize IDs and refuse to start.
- **`m.5 fix-up` — Drop stale aw-no-op test + patch seed_variant in
  cmd_up_to tests.** `test_seed_variant_aw_target_is_no_op` patched
  `subprocess.run` but seed_variant uses `_spawn_with_tee` (Popen),
  so the test silently ran real Aurora schema-applies every
  invocation. Under matrix parallelism, Aurora connection saturation
  surfaced as SSL connection-resets. Test deleted; `test_cmd_up_to_*`
  tests now patch `seed_variant` directly.

#### Three follow-ups landed in this release

- **#741 — `runs/` test isolation.** Replaced the
  `PYTEST_XDIST_WORKER` prune-skip bandaid with a session-autouse
  monkeypatch in `tests/conftest.py::pytest_configure` that redirects
  `runner.RUNS_DIR` to a session tmp dir. Tests no longer pollute the
  operator's real `runs/`; prune contention vanishes.
- **#740 — `test-module-nondeterminism` lint.** New AST checker in
  `tests/unit/test_typing_smells.py` flags `random.X()` /
  `secrets.X()` / `datetime.now()` / `date.today()` calls at module
  top level in any test file. Catches the j.6.fix bug class
  proactively.
- **#729 — `VariantName` NewType wrapper.** `VariantSpec.name` now
  returns `VariantName = NewType("VariantName", str)` so accidental
  swaps with DialectCode / ScenarioCode / free-form str fail at the
  call site under pyright. Annotation-only — zero runtime cost.

### Operator-visible CLI

```bash
# No flags = full 13-cell matrix
./run_tests.sh up_to=browser
./run_tests.sh up_to=db --scenarios=sp --dialects=pg,or,sl --targets=lo
./run_tests.sh up_to=browser --only=test_drift     # full chain, narrow within-layer
./run_tests.sh up_to=db --skip-cheap               # short-circuit unit if cached green for SHA
./run_tests.sh up_to=db --variants=f12345_pg_lo    # repro a fuzz failure by seed
```

### Known follow-ups (post-gate.m)

- **Y.3.f — Stop bridging Oracle/Postgres case sensitivity.** Generator
  emits dialect-natural case (Oracle UPPERCASE; PG/SQLite lowercase).
  Unblocks `*_or_lo` app2 + `sp_or_aw`/`sq_or_aw` deploy/api/browser
  cells (currently fail with `ORA-00904: "ACCOUNT_ID": invalid
  identifier`).
- **App2 Oracle column-casing bug** — root-caused, fix lives in Y.3.f.

## v8.8.0a5 — Y.2.gate.i close-out (instructions + tests + validation)

Fifth alpha. Closes out `Y.2.gate.i.{2,3,4}` — the runtime wiring for
the auth path already shipped under `h.1` + `i.1` in v8.8.0a3, but
the supporting paperwork (operator-facing docs, unit tests for the
derivation contract, validation acceptance criteria) was still open.
Now done. The whole `Y.2.gate.i — AWS auth refresh` umbrella ticks.

### What ships

- **`Y.2.gate.i.2` — Instructions.** CLAUDE.md `Commands` block
  now carries an `Auth (Y.2.gate.h+i)` sub-section: cfg `auth:`
  block schema, why long-lived IAM keys over SSO (multi-hour
  Claude-loop survival), pointer to spike §6 (runbook) + §7 (policy)
  + the IAM policy json. Runner's `--help` now carries an
  `Auth (Y.2.gate.h+i)` epilog block (`_HELP_EPILOG` in
  `_dev/runner.py`) — same content surfaces directly when an operator
  runs `./run_tests.sh --help`.
- **`Y.2.gate.i.3` — Build paperwork.** 4 mock-boto3 unit tests in
  `tests/unit/test_runner_skeleton.py` lock the `_derive_qs_user_arn`
  contract: (a) cfg-override short-circuits boto3 entirely (sentinel
  factory raises if called), (b) join-key derivation matches
  `PrincipalId == "federated/iam/<UserId>"` + threads `aws_profile`
  to `boto3.Session`, (c) no-match → actionable `RuntimeError` naming
  UserId / IAM Arn / account / cfg-override escape / spike doc, (d)
  boto3 errors propagate (no swallowing — locks the i.1 deferred
  narrowing contract). Total runtime: ~0.2s.
- **`Y.2.gate.i.4` — Validation.** (1) ✓ `./run_tests.sh up_to=browser`
  ran end-to-end without env-var exports — VALIDATED LIVE in the c.5
  close-out chain (~4 min wall, all 6 layers green); runner derived
  `QS_E2E_USER_ARN` from `cfg.auth.aws_profile` silently. (2) ⊘ 4+ hour
  Claude-loop validation is longitudinal — carried as ongoing
  observation. (3) ✓ cfg-override path locked by a sentinel-boto3 unit
  test. (4) ✓ expired-keys propagation locked by a fake-ClientError
  unit test. Live access-key revoke skipped (blast-radius asymmetric
  vs unit-test coverage).

### Operator notes

- New `Auth` section in CLAUDE.md is the canonical source for the
  cfg `auth:` block schema and the long-lived-IAM-keys recommendation.
- `./run_tests.sh --help` now self-documents the auth path — no need
  to dig into the spike doc to know what to put in `auth.aws_profile`.

### Memory housekeeping

- `feedback_no_credential_friction.md` is a *principle* (kept — applies
  beyond Y.2.gate to any future credential-touching code).
- `project_qs_e2e_user_arn.md` content was rewritten earlier to
  triage notes (auto-derive + 3-QS-user namespace map). MEMORY.md
  index entry now matches that content (was a stale value-cache line).

## v8.8.0a4 — Layer chain reaches the browser tier (c.5 LANDED)

Fourth alpha. Closes out `Y.2.gate.c.5.{deploy,api,browser}` —
the runner's three previously-stubbed top layers now actually
dispatch real work. The full chain `unit → db → app2 → deploy →
api → browser` runs end-to-end against the operator's external
Aurora in ~4 minutes wall-clock, well inside the per-layer
budgets locked in the c.5 PLAN entries.

### What ships

- **`deploy` layer (`c.5.deploy`)** — `_layer_command` builds
  `quicksight-gen json apply --execute -c <cfg> --l2 <l2> -o <run_dir>/deploy/out`
  from the per-variant env (`QS_GEN_CONFIG` + `QS_GEN_TEST_L2_INSTANCE`
  injected by `h+i.0` + `h.6`). Default-variant cfg path falls back
  to `_resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)` since
  the default variant doesn't pre-inject cfg into env. Live: 112s.
- **`api` layer (`c.5.api`)** — `pytest tests/e2e/ -m api -q -n auto`.
  Test selection is mark-based (every API e2e file already carries
  `pytestmark = [pytest.mark.e2e, pytest.mark.api]`) — the runner
  doesn't enumerate test files; tests register themselves. Live:
  24s, 48 tests.
- **`browser` layer (`c.5.browser`)** — `pytest tests/e2e/ -m browser
  -q -n 4` (4 workers per existing `./run_e2e.sh` default). Same
  mark-based selection as api. Live: 57s, 21 passed + 16 skipped +
  1 xfailed.
- **Two test xfails recorded during c.5.browser validation
  (`c.5.followup`).** (1) `test_l1_filters.py::test_date_range_filter_narrows_drift_sheet`
  — Sasquatch L1 dashboard render flake (task backlog #466); xfail
  strict=False until Y.2 SQL pushdown reshapes L1 dataset binds.
  (2) `test_audit_dashboard_agreement.py` — entire file skipped;
  module fixtures need `spec_example` seeded into BOTH PG + Oracle
  in the same chain invocation (default variant only seeds ONE).
  Re-enable per `Y.2.gate.k.1` multi-variant CI parity.

### Operator notes

- `./run_tests.sh up_to=browser` is now the canonical full-chain
  entry point. Wall budget: ~4 min against external Aurora (no DB
  container spin-up); local-pg / local-oracle add Docker startup time.
- `pytest-xdist` is required for `-n auto` / `-n 4`. Already pulled
  in via the `e2e` extras group; `uv sync --all-extras` covers it.

### Deferred

- The merged `b.3.impl.gather` parallelism (target=app2 + target=qs
  fan-out via asyncio.gather) is still queued — c.5.browser today
  runs only the QS browser tier; App2 has its own `app2` layer.

## v8.8.0a3 — Cfg-driven AWS auth + L2-instance alignment

Third alpha. Folds `Y.2.gate.h+i.0` (combined spike + build) and
`Y.2.gate.h.6` into the runner: the test-layer chain now reads
operator-side env state (AWS profile, QuickSight embed user, L2
institution) from the cfg yaml exactly once, and threads it into
every layer subprocess. Eliminates three classes of operator
friction with one pattern.

### What ships

- **Long-lived IAM credentials replace AWS SSO for local-dev**
  (`Y.2.gate.h+i.0`). Combined spike
  (`docs/audits/y_2_gate_h_i_combined_spike.md`) locked candidate
  C: long-lived IAM access keys for a dedicated
  `quicksight-gen-local` user (policy = mirror of the existing
  CI role + `quicksight:ListUsers`). Multi-hour Claude-loop
  sessions no longer hit AWS SSO browser-flow refreshes that
  broke continuity.
- **Auto-derived `QS_E2E_USER_ARN`** (`Y.2.gate.h.1`).
  `_dev/runner.py::_derive_qs_user_arn` calls
  `sts:GetCallerIdentity` → `quicksight:ListUsers` paginate →
  matches `PrincipalId == "federated/iam/<UserId>"`. The join
  key was validated live across IAM-user, assumed-role, and root
  identities. `cfg.auth.quicksight_user_arn` overrides without
  any API call (CI escape hatch).
- **`AWS_PROFILE` injected from cfg** (`Y.2.gate.i.1`).
  `cfg.auth.aws_profile` flows into every subprocess
  `env_overrides`; the parent process uses the same profile for
  boto3 derivation.
- **`QS_GEN_TEST_L2_INSTANCE` injected from cfg** (`Y.2.gate.h.6`).
  Same shape: new `cfg.default_l2_instance` field carries the
  path to the L2 yaml the operator's external DB has been seeded
  with. Runner resolves to absolute (relative to repo root) +
  threads into env. Both the seed flow's `--l2 <yaml>` arg and
  the dataset-SQL smoke test's L2 picker align with the DB state
  automatically.
- **IAM runbook** at `docs/audits/_iam/quicksight-gen-local-policy.json`
  (drop-in for `aws iam put-user-policy`) plus the combined
  spike's §6 step-by-step (`aws iam create-user` →
  `put-user-policy` → `create-access-key` → `aws configure
  --profile` → `quicksight register-user`).
- **`AuthConfig` dataclass** in `common/config.py` mirrors the
  existing `SigningConfig` shape; `auth:` is allowlisted in the
  cfg loader.
- **`_probe_qs_e2e_user_arn` is cfg-aware**: probe pre-passes
  when cfg has either `auth.aws_profile` (derivation will work)
  or `auth.quicksight_user_arn` (explicit override). Falls back
  to the env-var presence check for legacy invocations.

### Validated end-to-end

Live derivation against account `470656905821`: runner output
shows
```
runner: variant-env [AWS_PROFILE]=quicksight-gen-local
runner: variant-env [QS_E2E_USER_ARN]=arn:aws:quicksight:us-east-1:...
runner: variant-env [QS_GEN_TEST_L2_INSTANCE]=/.../tests/l2/sasquatch_pr.yaml
```
on a fresh shell with NO env-var exports. The chain progresses
clean through the unit + db layers (37 dataset SQLs all green
post-h.6). 1981 unit tests pass; pyright clean.

### Operator action: one-time onboarding

Per the IAM runbook (combined spike §6):

1. `aws iam create-user --user-name quicksight-gen-local` (+ tags).
2. `aws iam put-user-policy --policy-document
   file://docs/audits/_iam/quicksight-gen-local-policy.json`.
3. Console-create access key for the user (avoids the secret
   landing in any transcript); paste into `aws configure
   --profile quicksight-gen-local`.
4. `aws quicksight register-user --identity-type IAM --iam-arn
   <user-arn> --user-role ADMIN`.
5. Add to `cfg`:
   ```yaml
   auth:
     aws_profile: "quicksight-gen-local"
   default_l2_instance: "tests/l2/sasquatch_pr.yaml"  # or your seeded L2
   ```
6. `./run_tests.sh up_to=db` — the runner self-derives everything.

### Out of scope (queued)

- **`Y.11`** — CLI shape revisit. Now that `cfg.default_l2_instance`
  exists, every CLI's `--l2 <yaml>` arg is partially redundant.
  Spike-before-implement comparing 4 candidate factorings; lands
  as v9.x.0 (breaking CLI change).
- `Y.2.gate.h.2-h.4` — DB connection strings, AWS account/region,
  tunable defaults are mostly already in cfg; remaining cleanup.
- `Y.2.gate.i.2-i.4` — instructions sweep + acceptance test of
  the long-running-loop scenario.

### Memory entry rewritten

`project_qs_e2e_user_arn.md` shifted from "operator exports the
env var" to "runner self-derives via cfg.auth.aws_profile" — the
old static value is now stale automation backlog. Retained as a
triage runbook for the three QS users in the namespace.

## v8.8.0a2 — Test-layer chain runner + SQLite as third dialect

Second alpha. Two themes: a new test-layer chain runner that
unifies local + CI invocations behind a single `./run_tests.sh`
entry point, and SQLite landing as a peer to PostgreSQL + Oracle
across the schema / seed / matview / audit-PDF surface. The
runner work is dev-only (the `_dev/` package is excluded from the
wheel) — no end-user API change. Install the alpha with
``pip install --pre quicksight-gen``.

### What ships in this alpha

- **Test-layer chain runner** (`Y.2.gate.b/c`). New
  `quicksight_gen/_dev/runner.py` (~1700 LOC) plus the
  `./run_tests.sh` bash shim. Verbs:
  - `up_to <layer>` — runs the chain (`unit → db → app2 → deploy
    → api → browser`) up to the named layer; stops on first
    failure; per-layer artifacts under `runs/<run-id>/<layer>/`
    (`cmd.json`, `stdout.log`, `stderr.log`).
  - `up | down | status [--cost] | sweep` — lifecycle stubs and
    orphan cleanup against the operator's AWS resource graph.
  - `--variants <csv>` selects local-DB variants
    (`local-pg | local-oracle | local-sqlite`); multi-variant
    fan-out runs them in parallel via `asyncio.gather`.
  - `--parallel N` threads `pytest-xdist -n N` into eligible layers.
  - `--skip-cheap` short-circuits unit/db when the current SHA
    has a green cache marker.
  - `--allow-dirty-deploy` overrides the layer-4+ tracked-changes
    refusal.
  - `--seed-density N` scales the demo seed's per-Rail densify /
    broken-rail / fanout-boost knobs.

- **Local-DB variants via `testcontainers-python`**:
  - `local-pg` boots `postgres:17-alpine` (~10s).
  - `local-oracle` boots `gvenzl/oracle-free:23-faststart` (~25s).
  - `local-sqlite` is no-Docker — temp file under a per-invocation
    `tempfile.mkdtemp` + auto-generated cfg threaded into the
    layer subprocesses.

- **Multi-variant parallel fan-out** (`Y.2.gate.c.6.async`).
  Cross-variant chains run concurrently via `asyncio.gather` +
  `asyncio.to_thread`. Per-variant terminal prefix
  (`[local-pg] …`) so interleaved output stays attributable; soft
  fast-fail per variant; per-variant artifact dirs under
  `runs/<run-id>/<variant>/<layer>/`.

- **Per-run output isolation + drift report**
  (`Y.2.gate.b.4 / c.2 / c.3`). Every invocation lands under
  `runs/<utc-ts>-<short-sha>[-dirty]/` with `timings.json` +
  `hashes.json`; drift-diff vs the prior run prints per-layer
  `±%` with a `⚠` marker on `±50%` swings. Last 20 runs auto-pruned.

- **Browser trace + App 2 server-log capture**
  (`Y.2.gate.c.11 + c.11.app2-server-logs`). Failed Playwright
  runs save `trace.zip` + extracted `trace/` + `screenshot.png` +
  `console.txt` + `network.txt` under
  `runs/<id>/browser/<test-id>/`. App 2's uvicorn access log +
  any `quicksight_gen.app2.devlog` events flow to
  `runs/<id>/app2/server.log` for post-mortem.

- **Top-queries auto-capture** (`Y.2.gate.c.10`). After every
  DB-touching layer, the top-50 most expensive queries land at
  `runs/<id>/db/<dialect>/top-queries.md` (PG via
  `pg_stat_statements`, Oracle via `v$sqlstats`).

- **Typed `EnvVar` registry + AST lint family** (`Y.2.gate.b.15`).
  All `QS_GEN_*` / `QS_E2E_*` env-var access goes through a
  typed `EnvVar[T]` instance in `common/env_keys.py` (validators,
  required-vs-optional, coercer); 8 paired lints in
  `tests/unit/test_typing_smells.py` catch convention bypasses
  (env-var bypass, bare type-ignore comments, unseeded `random.X()`
  in seed modules, direct `boto3.client(...)` outside the 4
  wrappers, hardcoded `qs-gen-` prefix, `datetime.now()` in
  determinism-sensitive modules, `time.sleep` in `tests/e2e/`,
  `@dataclass` without frozen/eq=False in `common/tree/`,
  `json.dumps` without `indent=2` in CLI/file-emit paths,
  `boto3.create_*` without `Tags=`).

### SQLite as third database dialect (X.3)

- **`Dialect.SQLITE` is a peer to PostgreSQL + Oracle** across
  every helper in `common/sql/dialect.py`: type names, casts,
  date arithmetic, JSON path extraction (new `json_value`
  helper — PG/Oracle `JSON_VALUE`, SQLite `json_extract`),
  row-wise `greatest`, date literals, `with_recursive`,
  matview-as-table emit (`CREATE TABLE … AS SELECT`), and
  refresh-as-DELETE+INSERT.
- **`emit_schema(instance, dialect=SQLITE)` works end-to-end**
  against an in-memory or file-backed SQLite. JSON metadata is
  validated via `json_valid` (JSON1 extension, built into stdlib
  `sqlite3` 3.38+).
- **`build_full_seed_sql` emits SQLite-compatible INSERTs** —
  timestamp literals strip TZ + use `datetime()`-friendly form;
  the locked seed lives at
  `tests/data/_locked_seeds/spec_example.sqlite.sql`.
- **Audit PDF SQLite arm** (`X.3.g.2`): `quicksight-gen audit
  apply --execute -c <sqlite-cfg>` produces the regulator-ready
  PDF against a SQLite seed; same provenance contract as the
  PG / Oracle paths.
- **Layer 1 SQLite tests** (`X.3.g.1`): 8 tests in
  `tests/unit/test_layer1_query_sqlite.py` cover the `query_*` /
  `assert_*` helpers against an in-memory SQLite.
- **The `local-sqlite` runner variant** (`b.2.impl.sqlite`):
  zero Docker, zero AWS, ~21s wall-clock for the unit + db chain.

### Bug fixes

- **L2FT money_trail e2e** (`X.2.h.6`): three Playwright tests
  fixed — chain-root parameter binding, sigma-slider value
  propagation, recipient-fanout HAVING threshold encoding.
- **App2 cache-busting** (`X.2.g.2.e`): static asset URLs gain a
  per-server-boot fingerprint so browsers don't serve stale CSS
  / JS after a re-deploy.
- **Re-emitted matview SQL is now ASCII-clean** for SQLite (the
  `--` SQL comment marker breaks if used inside a multi-line
  string template); no more silent matview empties on SQLite.

### Removed

- **Python 3.12 support** is dropped; 3.13 is now the minimum.
  3.12 was supported until the Y.2 phase work began; dropping
  it shrinks the test matrix and frees up newer-syntax features
  as they land.

### Known limits

- Cloud-layer dispatch (`deploy / api / browser`) is still
  stubbed in the runner — `up_to=db` runs end-to-end, the
  cloud layers print a skip line and pass through. Wiring lands
  with `c.5.{deploy, api, browser}` after `h.1 / h.2 / h.3`
  config plumbing + the `i.0` AWS auth spike close.
- App 2 still cannot read from a SQLite-backed cfg
  (`X.3.e` pending) — the X.3 work makes the schema / seed /
  matview / audit-PDF paths SQLite-compatible, but the
  HTMX dashboards still need PG or Oracle.
- The 4-way cross-tool agreement gate (`X.2.j` — `expected ==
  PDF == HTMX == QS`) is not yet locked; `v8.x.0` / `v9.0.0`
  GA waits on it.

## v8.8.0a1 — App 2 (HTMX dashboard renderer) preview

First public preview of **App 2**: a self-hosted HTMX + d3 +
Tailwind dashboard renderer that shares the tree, dataset
contract, and L2 instance with the QuickSight (App 1) and Audit
PDF dialects. Read-only, no auth, served via Starlette ASGI.

Why an alpha: Executives is the only one of the four trees wired
through the generic ``make_tree_db_fetcher`` so far; mkdocs
embed and the cross-tool 4-way agreement gate (X.2.j) are still
pending; there is no auth surface. Install with ``pip install
--pre quicksight-gen`` (regular ``pip install`` skips alphas).

### What ships today

- **Renderer + ASGI server** (``common/html/`` + ``cli/serve.py``).
  ``quicksight-gen serve app2 apply -c run/config.yaml`` runs
  uvicorn against the configured L2 instance. URL contract is
  all-GET, plural nouns, nested resource paths
  (``/dashboards/{id}/sheets/{sheet_id}/visuals/{visual_id}/data``).
- **Visual primitives** wired through d3: KPI, Table (sortable +
  paginated), BarChart (with axis labels), LineChart (with
  axes + legend), Sankey, ForceGraph.
- **Filter primitives** (``X.2.d``): ParameterDropdown,
  CategoryFilter, NumericRange, date range. Auto-refresh on
  filter change — no Refresh button.
- **Sheet structure + cross-sheet nav** (``X.2.e``).
- **Generic per-tree fetcher** (``X.2.g.0`` + ``X.2.g.1``):
  ``make_tree_db_fetcher`` walks any tree ``App`` and produces
  an async ``DataFetcher`` that resolves visual_id → dataset
  SQL → executes via the cross-dialect ``AsyncConnectionPool``
  → shapes per visual kind. Dialect-portable (PG / Oracle /
  SQLite), with visual-level aggregation wrapping so KPIs
  return a single value, not one row per dataset row.
- **Async DB stack** (``X.2.n``): psycopg3, oracledb async pool,
  aiosqlite. The App 2 server's request loop awaits the SQL
  roundtrip directly — no threadpool serialization. Pool size
  knob via ``cfg.app2_db_pool_size`` (default 10) +
  ``QS_GEN_APP2_DB_POOL_SIZE`` env var.
- **Theme parity** (``X.2.l``): the same L2 ``theme:`` block
  drives App 2's CSS-var-injected page shell.
- **Themed error pages** (``X.2.m``): 404 + 500 served via the
  same chrome as the dashboard, no Starlette default body.

### Type-strictness sweep (X.2.o)

- Pyright strict scope expanded to the full async DB stack
  (``db.py``, ``config.py``, ``_sql_executor.py``,
  ``_tree_fetcher.py``, ``server.py``); 0 errors / 0 warnings.
- New ``DashboardId`` ``NewType`` joins the existing
  ``SheetId`` / ``VisualId`` / ``FilterGroupId`` /
  ``ParameterName`` (``common/ids.py``); the ``DataFetcher``
  contract takes ``VisualId`` so kind-swap bugs surface at the
  call site, not as silent zero-row dashboards.
- Custom AST lint (``tests/unit/test_typing_smells.py``) catches
  bare-``str`` ID parameters and explicit ``Any`` annotations
  pyright doesn't natively flag (``reportExplicitAny`` is a
  basedpyright-only rule). Per-line + per-file suppression
  comments require a one-line WHY.

### Pending before v8.8.0 stable

- Wire Investigation, L1 Dashboard, L2 Flow Tracing trees
  through ``make_tree_db_fetcher`` (Executives is the only
  one wired today).
- Embed mkdocs into App 2 (``X.2.i``).
- 4-way cross-tool agreement gate
  (``expected == PDF == QS == HTMX``, ``X.2.j``).
- Decide whether App 2 graduates to ``9.0.0`` (default
  renderer) or stays additive at ``8.8.0`` (opt-in).

### Release pipeline

- ``release.yml`` accepts PEP 440 pre-release tag suffixes
  (``a1`` / ``b1`` / ``rc1``). The github-release step computes
  ``prerelease=true`` and ``make_latest=false`` from the suffix
  so an alpha doesn't displace the current stable as "Latest".
- TestPyPI → live e2e → prod PyPI publish chain unchanged.

## v8.7.3 — Hotfix: synthesized ARNs honor AWS partition

Three sites in ``Config`` (``__post_init__`` deriving
``datasource_arn`` from ``demo_database_url``, ``dataset_arn()``,
``theme_arn()``) hardcoded ``arn:aws:`` — broke every deploy against
GovCloud (``aws-us-gov``) or China (``aws-cn``) since QuickSight
rejects synthesized resource ARNs whose partition doesn't match the
account's actual partition.

Fix: new ``Config.partition`` property resolves the partition from,
in order:

1. The explicit ``datasource_arn`` if set (authoritative for
   pre-existing customer datasources).
2. The first ``principal_arns`` entry — the customer's user/role is
   in the same partition as the resources we'll synthesize.
3. ``aws`` as the default (preserves prior behavior for the
   spec_example / fuzz fixtures that don't carry a principal).

Bare strings and malformed ARNs (no ``arn:`` prefix, empty
partition slot) silently fall through to the default — defensive
against operator config typos.

10 unit tests cover the resolution order, GovCloud +
China + commercial partitions, the ``datasource_arn``-wins-over-
principal precedence, and the bare-string / empty-partition edges.

## v8.7.2 — X.1 phase closed (planning milestone)

No code change vs v8.7.1 — this is a planning-milestone tag marking
the close of phase X.1 (e2e fixes + layered-test foundation).

X.1.d ticked: the originally-planned sweep across active browser
tests was assessed as low value (those tests assert structural
claims, not specific row presence; the active row-presence tests
are skip-deferred). The shipped `tests/e2e/_layer1_query.py`
helpers are the deliverable; new row-presence tests use them.

Next phase: **X.2 — self-hosted dashboard renderer (HTMX dialect)**.
Sub-spike begins with the Investigation Money Trail sheet rendered
as static HTML from the existing tree.

## v8.7.1 — X.1.c "flake" fix + X.1.d.1 layered helpers

### X.1.c — Sasquatch L1 dashboard "flake" was table virtualization

Diagnosed and fixed the
``test_harness_l1_planted_scenarios_visible[sasquatch_pr]``
intermittent failure. **Not a flake** — a deterministic
data-density-dependent assertion bug.

The harness's ``_active_sheet_text`` reads ``inner_text()`` of every
visual on the active sheet to check whether a planted ``account_id``
surfaces. QS tables virtualize at ~10 DOM rows; ``inner_text()`` only
sees the rendered window. ``sasquatch_pr``'s denser seed pushed the
planted Limit Breach row (``cust-0001-snb``) below the visible 10
— ``spec_example``'s sparser seed kept it in view. Same code, same
dashboard, opposite results purely on data density.

Fix: new ``expand_all_tables_on_sheet`` helper bumps every paged
table on the active sheet to page-size 10000 before reading text.
Reuses the same QS ``simplePagedDisplayNav_dropdown_pageSize``
control that ``count_table_total_rows`` already exercises for row
counting; non-table visuals (KPIs, charts) silently skip via the
short pagination-detection timeout.

Verified live ``2026-05-04``: ``./run_e2e.sh --harness`` passes
**15 / 15 tests in 13:47** across all 3 L2 instances, including the
previously-flaking sasquatch_pr Limit Breach assertion.

### X.1.d.1 — Shared Layer 1 (matview presence) query helpers

New ``tests/e2e/_layer1_query.py`` module generalizes the
``assert_l1_matview_rows_present`` pattern (used by the harness)
into helpers that any browser e2e test can call to gate its render
assertion behind a fast matview check:

- ``query_matview_rows(conn, table, where, *, dialect, columns, limit)``
- ``matview_row_count(conn, table, where, *, dialect)``
- ``assert_matview_has_row(conn, table, where, *, dialect, context)``
- ``assert_account_in_matview(conn, matview, account_id, *, dialect)``

All branch placeholder syntax (Postgres ``%s``, Oracle ``:1``/``:2``)
and Oracle's ``FETCH FIRST`` vs Postgres ``LIMIT``. 14 unit tests
against an in-memory SQLite stand-in cover the SQL shape + assertion
behavior; live bind format verified by the existing harness against
Aurora / Oracle.

X.1.d.2/.3 will wire these into the existing browser e2e tests
(``test_inv_drilldown.py``, ``test_l2ft_metadata_cascade.py``,
``test_l1_sheet_visuals.py``, etc.) — adoption work, no further
helper changes expected.

### CI workflow concurrency fix

``e2e-pg-browser`` now ``needs: e2e-pg-api`` so the two PG jobs
serialize within a single workflow run. Prior shape (both jobs in
the same ``e2e-pg`` concurrency group with no internal dependency)
caused GHA to cancel ``e2e-pg-browser`` whenever a sibling
``e2e-pg-api`` was queued behind a prior run's ``cleanup-pg`` —
the would-be-deadlock cancel pattern. Pulled forward from the
X.2 (now X.3) "concurrency redesign" notes since X.1.c needed a
working ``workflow_dispatch`` path for harness verification.

## v8.7.0 — Unified seed-hash lock surface (X.1.k)

The seed-hash lock surface is now a single artifact per
``(L2 instance, dialect)``: the canonical-anchor SQL itself,
checked into ``tests/data/_locked_seeds/<instance>.<dialect>.sql``.
Replaces the four pre-existing lock sites (YAML ``seed_hash:``
blocks, ``L2Instance.seed_hash`` field, two
``test_full_seed_hash_lock_*`` Python constants, ``_BROAD_MODE_HASHES``
dict + matching parametrize test) — the partial-re-lock failure
mode that ate CI from 2026-05-04 21:58 to the fix-CI commit
collapses to a single "did you re-run ``data lock``?" question.

### Breaking — CLI

- ``quicksight-gen data hash`` is **removed**. Replaced by
  ``quicksight-gen data lock`` with these flags:
  - ``-c <config.yaml>`` — derives dialect from
    ``demo_database_url`` (was hardcoded to postgres).
  - ``--l2 <yaml_path>`` — picks which L2 to lock.
  - default behavior: refresh the locked SQL file.
  - ``--check`` — verify only; exits non-zero on drift, prints
    a unified diff (50-line cap) of what shifted.
- Run once per ``(postgres config, oracle config)`` to cover
  both dialects. Re-lock after any seed-shape-changing commit
  (new plant kind, plant emitter change, baseline generator
  tweak) before pushing.

### Breaking — L2 YAML schema

- ``seed_hash:`` block dropped from the L2 institution YAML
  schema. ``L2Instance.seed_hash`` field removed from the
  Python dataclass.

### emit_full_seed output

Every emit now prepends a ``-- SHA256: <hex>`` header
(deterministic against the body bytes). Saved-to-disk SQL
self-identifies; the locked file's first line and its
recomputed-hash value always travel together.

### Test surface

- New ``tests/data/test_locked_seeds.py`` auto-discovers
  ``_locked_seeds/*.sql`` and byte-compares each fresh emit.
  Adding a new ``(instance, dialect)`` pair = drop a file; no
  Python parametrize list to maintain.
- Locked spec_example at both dialects (~1.5 MB each).
  Skipped sasquatch_pr — its emit is ~55 MB per dialect, too
  much repo bloat for a determinism-checkpoint. spec_example
  is the canonical L2 contract that catches every change to
  the seed pipeline; sasquatch_pr is a flavor instance whose
  L2-shape changes are obvious in the YAML diff at PR review.

### Migration

```bash
# Re-lock both dialects against your local config + L2.
.venv/bin/quicksight-gen data lock -c run/config.postgres.yaml --l2 path/to/your.yaml
.venv/bin/quicksight-gen data lock -c run/config.oracle.yaml   --l2 path/to/your.yaml
```

The new CI gate (``test_locked_seeds.py``) catches missed
re-locks with one test failure plus a unified SQL diff,
instead of the 7+ scattered hash-mismatch assertions the
old multi-site layout produced.

## v8.6.21 — Per-generator cross-reference consistency test (X.1.f follow-up)

New unit test at
``tests/json/test_emit_cross_reference_consistency.py`` runs each
of the 4 per-app generators (Investigation / Executives /
L1 Dashboard / L2 Flow Tracing) in its own tmpdir against
sasquatch_pr.yaml and asserts the bundle each one emits is
internally consistent:

1. Every dashboard / analysis JSON's ``ThemeArn`` resolves to the
   id ``theme.json`` actually declares — the X.1.f regression
   guard at unit-test time instead of at deploy time.
2. Every ``DataSetArn`` referenced in a dashboard / analysis
   corresponds to a sibling ``datasets/<id>.json`` — catches the
   "per-app builder forgot to register a dataset" class. Pre-test
   that bug class would deploy cleanly but every visual bound to
   the missing dataset would render the spinner forever with no
   error banner.

Per-generator parametrization (8 tests = 4 generators × 2
invariants) matches the X.1.f fault surface: the bug lived
inside specific generators (L1 + L2FT didn't stamp the L2 prefix
before ``build_theme``; Investigation + Executives did). A single
shared-emit fixture would have masked the bug because each
generator overwrites ``theme.json`` at the same output path.

Bite verified: temporarily reverted the X.1.f stamp +
``build_theme`` guard → tests failed cleanly on
``test_theme_arn_matches_emitted_theme_id[l1_dashboard]`` and
``[l2_flow_tracing]`` with messages naming the offending
ThemeArn id vs. theme.json id. Restored fix → 8 / 8 pass.

## v8.6.20 — Fix dangling theme binding (Phase X.1.f)

The ``GetThemeForDashboard`` 404 captured in the X.1.b diagnostic
bundle is fixed. Root cause: the L1 Dashboard and L2 Flow Tracing
per-app generators in ``cli/_app_builders.py`` called
``build_theme(cfg, ...)`` before stamping
``cfg.l2_instance_prefix``, while their downstream ``build_*_app``
calls stamped it. Result: ``theme.json`` shipped with id
``<resource_prefix>-theme`` (no L2 segment) but every dashboard's
``ThemeArn`` referenced ``<resource_prefix>-<l2>-theme``. QS
embed sessions queried for a theme id that didn't exist on the
account → 404 on every render.

Investigation + Executives generators stamped the prefix
correctly, so the bug was specific to L1 + L2FT — exactly the two
dashboards X.1.b was trying to validate.

Two-layer fix:

1. Stamp ``cfg.l2_instance_prefix`` in both broken generators
   before the ``build_theme`` call.
2. Structural guard: ``build_theme`` now raises if the prefix
   isn't stamped. The dashboard's ThemeArn always includes the L2
   segment, so the theme MUST too — encoded in the type system
   so a future per-app generator can't silently regress.

Verified locally: ``aws quicksight describe-theme`` resolves the
expected id post-deploy; the L2FT Rails browser e2e (which loads
the deployed dashboard end-to-end) passes against the now-bound
theme.

## v8.6.19 — Doc → CLI cross-reference checker (Phase X.1.h.B v0)

New unit test at ``tests/unit/test_docs_cli_invocations.py`` walks
every ``.md`` file under ``src/quicksight_gen/docs/``, extracts
``quicksight-gen <command> <flags>`` invocations out of fenced
bash blocks, and asserts the subcommand chain + every cited flag
exists in the shipped Click tree. The checker catches the "doc
cites a removed CLI verb / renamed flag" hallucination class
before docs ship.

Handles real-world bash patterns:

- multi-line invocations with trailing-backslash continuations
- env-var prefixes (``QS_GEN_E2E=1 quicksight-gen ...``)
- positional args after the subcommand
  (``audit verify report.pdf -c config.yaml``)
- the ``--foo=value`` flag form
- rejects false positives like ``pip install quicksight-gen``

Discovers docs automatically via ``rglob`` — new docs need no
per-doc wiring. 12 docs cite ``quicksight-gen``; all 12 pass
against the v8.6.18 CLI surface.

Track B follow-ups split into the backlog:

- X.1.h.B.2 — Python doctest infra (``pytest-markdown-docs``
  + per-block fixtures for placeholder paths)
- X.1.h.B.3 — SQL block execution (needs a DB target decision)
- X.1.h.B.4 — Schema column cross-reference (catches drift like
  the ``transaction_id`` references in the populate-transactions
  walkthrough that should be ``id`` per the v6 schema)

## v8.6.18 — Real ETL example patterns (Phase X.1.h.A)

The ``quicksight-gen data etl-example`` CLI emits 10 canonical
INSERT-pattern blocks against the v6 schema instead of the
single-line placeholder it shipped with. Patterns cover:

- single-leg Posted transfer (the simplest case)
- two-leg paired transfer (debit + credit, sums to zero)
- force-posted external (``origin='ExternalForcePosted'``)
- Pending → Posted lifecycle (Lifecycle supersession)
- TechnicalCorrection rewrite (back-office amount fix)
- bundled transfer (card-network settlement shape)
- chained transfer (``transfer_parent_id``, drives Money Trail)
- daily balance row (one per account-day)
- daily balance with ``limits`` JSON (LimitSchedule projection)
- metadata extension (the open-set extras container)

Each block carries the documented ``-- WHY:`` (business invariant
the pattern protects) and ``-- Consumed by:`` (dashboard view that
reads the resulting rows) headers per the handbook contract.
Output is deterministic — no random IDs, no clock-dependent
timestamps — so the helper is safe to wire into CI / docs
publishing pipelines without churn.

The placeholder lived under ``apps/investigation/etl_examples.py``
and pointed at deleted-since-M.4 ``payment_recon`` /
``account_recon`` files, which made the etl.md handbook's
"every base-table shape" claim a lie. The new
``common/etl_examples.py`` location matches the patterns'
actual scope (the base tables, not investigation-specific
rows). 11 unit tests guard the structural contract.

Track B (doctest infrastructure that catches the *next* handbook
hallucination automatically) split into X.1.h.B and deferred —
the etl-example bug is fixed; the broader doc/code drift surface
needs more thought on which blocks are realistically testable.

## v8.6.17 — Status enum open-set + planted Failed transactions (Phase X.1.i)

The L2FT Rails Status dropdown's enum gained an ``Other`` sentinel
covering every status outside ``Pending`` / ``Posted``. The L1
schema's ``status`` column is open-set (any string is a valid
terminal state), but only ``Pending`` / ``Posted`` carry
first-class meaning in this tool — they drive Aging, Conservation,
and Completion checks. Every other raw status (Failed, Cancelled,
Rejected, ...) projects to ``Other`` in the L2FT Rails dataset
SQL via ``CASE WHEN status IN ('Pending','Posted') THEN status
ELSE 'Other' END`` so the static dropdown enum matches what the
column actually produces.

New ``FailedTransactionPlant`` dataclass + per-scenario emitter
plant one ``status='Failed'`` leg so the dropdown's ``Other``
option has matching seed rows. Re-uses the existing
``pending_rail`` pick (any non-aggregating rail with a
``max_pending_age`` cap works since Failed legs have no
counter-leg).

Bug caught + fixed during local verify: ``densify_scenario`` /
``boost_inv_fanout_plants`` / ``add_broken_rail_plants`` were
dropping the new field on ScenarioPlant reconstruction (the
default-empty-tuple meant the loss was silent until the dropdown
test surfaced it). Patched all 3 to pass the field through
explicitly.

Re-hash-locked the canonical seed SHA256 for both bundled L2
instances (sasquatch_pr + spec_example) and re-synced the
bundled ``_l2_fixtures/`` + ``_default_l2.yaml`` to match.

## v8.6.16 — L2 validator C5: chain parent MUST have a Required child or XOR group (Phase X.1.j)

New validator rule rejects L2 instances where any chain parent
declares only optional children with no XOR group attached. The
chain mechanism encodes "if X fires, Y must follow" — an
all-optional declaration makes Y's firing unobservable as a
constraint, so the rule catches a meaningless chain at L2 load
rather than silently advertising a dead-end filter value on the
L2FT Chains dashboard.

Surfaced by the X.1.g per-dropdown browser e2e tests: the
Chains `Completion` dropdown advertised `'No Required Children'`
but no chain firing in any L2 instance hit that branch. With C5
in place that branch is unreachable, so the enum + the matching
SQL CASE branch are dropped — the dropdown now advertises only
`Completed` / `Incomplete`, both populated.

Fuzzer (`tests/l2/fuzz.py::_build_chains`) updated to pin the
first plain entry per parent to `required = True` so generated
instances satisfy C5; previously the 50/50 coin flip on
``required`` left some single-child fuzz parents all-optional.

## v8.6.15 — L2FT CategoryFilter dropdowns → ParameterDropdown(StaticValues) (Phase X.1.g)

All 7 multi-select dropdowns on the L2 Flow Tracing dashboard
(Rails: rail_name / status / bundle_status; Chains:
parent_chain_name / completion_status; Transfer Templates:
template_name / completion_status) migrated from
``FilterDropdown(empty CategoryFilter + FILTER_ALL_VALUES)`` to
``ParameterDropdown(MULTI_SELECT, StaticValues) +
CategoryFilter.with_parameter``.

The previous shape forced QuickSight to lazy-fetch each dropdown's
distinct column values from the ``tenK-sample-values-V2`` endpoint
at first render. That endpoint 404s on cold per-CI-run dashboards
(diagnosed in X.1.a / X.1.b), and three of the four
``Sample values not found`` JS errors traced to the L2FT cascade
test failure all came from these dropdowns. The new shape sources
options from a static list at deploy time — no runtime fetch
needed, and the 404 path is structurally gone.

The migration is centralized behind a
``_populate_param_filter_dropdown`` helper at
``apps/l2_flow_tracing/app.py`` that wires three things in
lock-step: a multi-valued ``StringParam`` defaulting to all
declared values, a ``ParameterDropdown(MULTI_SELECT, StaticValues)``
for the option list, and a ``CategoryFilter.with_parameter``
analysis-side filter. New dataset helpers
``chain_completion_status_values`` /
``tt_completion_status_values`` enumerate the bounded enum sets
the SQL CASE branches produce.

Per-dropdown browser e2e tests added under
``tests/e2e/test_l2ft_{rails,chains,templates}_dropdowns.py``
(7 tests, walks every advertised dropdown option) — guards both
the X.1.g param-bound CategoryFilter narrowing and the broader
"advertised dropdown value with no seed data" bug class. Already
flagged two pre-existing data-coverage gaps locally
(Status='Failed' has no seed rows; Chains
Completion='No Required Children' is an all-optional chain
branch never produced) — fixes queued under X.1.i and X.1.j.

The L2FT cascade test (``test_l2ft_metadata_cascade.py``) is
re-skipped: X.1.b's text-field swap made the Metadata Value
control a free-text input, so the original
LinkedValues+MULTI_SELECT cascade-source regression class is
structurally unreachable. Rewrite (or deletion) queued as
X.1.g.11.

## v8.6.14 — Auto-failure-screenshot hook in browser e2e (Phase X.1.a)

Browser tests now capture a full-page screenshot when an exception
escapes the ``with webkit_page(...)`` block, written to
``tests/e2e/screenshots/_failures/<test_id>.png`` before the
browser tears down. The existing
``e2e-pg-browser-screenshots-${run_id}`` GHA artifact upload picks
up the new ``_failures/`` subdirectory automatically — no workflow
change needed.

Pre-v8.6.14, only the happy path produced screenshots (via the
test's own ``screenshot()`` call). When a test failed before
reaching that call, the GHA artifact carried no visual evidence —
diagnosing failures meant guessing from log lines about the page
state at the assertion moment. The L2FT cascade test failure
investigation in v8.6.13's wake (skipped under PLAN X.1 pending
this hook) was the proximate motivator: without a screenshot of
the Rails sheet at the ``count_table_total_rows == 0`` assertion,
we couldn't tell whether QS rendered the visual empty, was still
loading, or rendered something the test couldn't find.

The capture is best-effort: any exception inside the screenshot
path is swallowed so the original test failure (the assertion that
triggered the capture) bubbles up unchanged. Closed page, missing
``PYTEST_CURRENT_TEST`` env var, full disk — none of those should
mask the failure being investigated.

Test ID derives from ``PYTEST_CURRENT_TEST`` and handles
parametrized tests, class-scoped tests, and missing/empty env
vars (returning ``"unknown"``).

### Tests

- ``tests/unit/test_browser_helpers.py`` — 6 new cases covering
  the ``_test_id_from_pytest_env`` parser: phase-suffix stripping
  (call / setup / teardown), parametrized brackets preserved,
  class-scoped path segments, missing env var fallback, empty env
  var fallback, and the "no arg" path that reads from os.environ.

### Phase X foundation

Lays the foundation for the next two X.1 sub-tasks: re-enabling
+ diagnosing the L2FT cascade test (X.1.b) and the Sasquatch L1
dashboard render flake investigation (X.1.c) — both depend on
having actual visual evidence at the assertion moment.

## v8.6.13 — `json clean --all` for full-deploy teardown

``json clean`` defaults to a carve-out sweep: anything currently
emitted to ``out/`` is preserved; everything else matching the
cfg's prefix scope (tag-based or, post-v8.6.11, ID-prefix-based)
is stale. That's the right shape for everyday cleanup of orphaned
resources. It's the wrong shape for full-deploy teardown — the
operator has to manually clear ``out/`` first, or point ``-o`` at
an empty directory.

New ``--all`` flag opts into purge mode: ``out/`` is ignored
entirely, every matching resource is eligible for deletion
including the live deploy. Pair with ``--execute`` to actually
delete, or pass alone to preview the full sweep:

```bash
# Preview what a full teardown would delete:
quicksight-gen json clean -c config.yaml --all

# Actually nuke everything we deployed:
quicksight-gen json clean -c config.yaml --all --execute
```

Flags are independent — ``--all`` says "ignore the carve-out",
``--execute`` says "actually delete". Both default off.

The startup banner calls out PURGE-ALL mode so it's visible in
shell history and CI logs, distinguishing it from everyday clean
output.

### Tests

- ``tests/json/test_cli_json.py`` — 2 new cases: ``--all``
  threads ``purge_all=True`` to ``run_cleanup``; ``--all``
  alone (no ``--execute``) is a dry-run preview.
- ``tests/json/test_cleanup.py`` — 2 new cases: ``run_cleanup``
  with ``purge_all=True`` ignores the ``out_dir`` carve-out
  even when live-deploy JSONs are present; the startup banner
  announces PURGE-ALL mode.

## v8.6.12 — Coverage uplift (no behavior change)

Total coverage 81.2% → 82.4%. Four targeted test files for
modules that were under-covered or hosting recently-shipped
behavior:

- ``tests/unit/test_aging.py`` — ``common/aging.py`` from 0% → 100%
  (5 tests covering the shared aging-bucket bar visual factory:
  horizontal orientation, ``aging_bucket`` column wiring, COUNT
  aggregation, fixed ``Age`` / ``Count`` axis labels, FieldId
  prefix-from-visual-id contract).
- ``tests/unit/test_clickability.py`` — ``common/clickability.py``
  from 0% → 100% (6 tests covering the two visual languages —
  plain accent text vs accent + tint background — that cue
  left-click vs right-click drill-source cells; asserts the
  always-true expression form per the project memory).
- ``tests/json/test_cleanup.py`` — ``common/cleanup.py`` from
  42.5% → 82.8% (8 new tests). ``_delete_stale`` per-kind dispatch
  + failure-counting; ``run_cleanup`` short-circuit, dry-run,
  skip_confirm, confirm-no-aborts, and the v8.6.11
  ``tagging_enabled=False`` ID-prefix banner.
- ``tests/json/test_cli_json.py`` — ``cli/json.py`` from 49.4% →
  ~95% (15 new tests). ``json apply`` w/ + w/o ``--execute``,
  the V.1.a ``demo_database_url`` auto-emit-datasource gate,
  deploy failure → ClickException, ``json clean`` dry-run +
  execute + failure propagation, ``json probe`` per-app iteration,
  ``json test`` pytest+pyright dispatch, and a parameterized
  ``--help`` smoke for every sub-command.

No production code changes. The new modules / files only add
``tests/unit/test_aging.py``, ``tests/unit/test_clickability.py``,
and ``tests/json/test_cli_json.py``; the existing
``tests/json/test_cleanup.py`` is extended.

## v8.6.11 — `tagging_enabled` config override

Some IAM environments forbid ``quicksight:TagResource`` /
``UntagResource`` (governance-tag-only-by-other-system). Pre-v8.6.11
the deploy pipeline always passed ``Tags=[…]`` on every ``Create*``
call, so a no-tag IAM principal couldn't deploy at all.

New config knob ``tagging_enabled: bool = True`` (defaults to the
existing tagged behavior). Setting ``tagging_enabled: false``:

- ``Config.tags()`` returns ``None`` instead of the tag list. The
  dataclass field assignment ``Tags=cfg.tags()`` resolves to
  ``None`` and ``_strip_nones`` drops the field from the AWS JSON
  entirely. Net effect: no ``Tags`` kwarg on the ``Create*`` boto3
  calls; the IAM principal doesn't need ``Tag*Resource``.
- ``json clean`` falls back to ID-prefix matching against
  ``cfg.resource_prefix`` (since the tag check is bypassed).
  ``resource_prefix`` becomes mandatory in this mode — without
  either tags or a prefix scope the cleaner refuses to run.

### Why this is unwise (per-page warning)

Tag-based isolation is the only protection against ID-collision
sweeps. With tagging off, a hand-built dashboard whose ID happens
to start with ``<resource_prefix>-`` would be eligible for deletion.
Concurrent deploys with the same prefix can't be told apart.
Reference page ``docs/reference/disable-tagging.md`` lays out the
full risk surface, mitigations (highly-unique prefix, mandatory
``--dry-run`` first), and migration story for re-enabling tagging
later (the tag check is fail-CLOSED so previously-untagged
resources stay invisible to the post-flip cleaner).

### Tests

- ``tests/unit/test_config_loader.py`` — 4 new cases:
  ``tagging_enabled`` defaults to True, allowlists into
  ``_CONFIG_ALLOWED_KEYS``, returns ``None`` from ``cfg.tags()``
  when False, populates the full tag list when True, rejects
  non-bool values.
- ``tests/json/test_cleanup.py`` — 3 new cases:
  ``_collect_stale`` matches by ID prefix when tagging is off,
  still respects the ``expected`` set (live deploys stay safe),
  and refuses to run without ``resource_prefix`` (no scope at
  all = no sweep).

## v8.6.10 — L2 theme actually lands on the docs site

Two pre-existing gaps in the portable docs build that quietly
left the integrator's L2 theme block half-applied:

### 1. Theme color tokens didn't reach the docs site at all

``main.py::define_env`` only consumed ``theme.logo`` and
``theme.favicon`` from the L2 instance — every color field
(``data_colors``, ``primary_fg``, ``accent``, ``accent_fg``,
``link_tint``, etc.) was loaded into ``L2Instance.theme`` but
ignored by the mkdocs build. Result: the QS dashboards rendered
in the integrator's brand colors, the docs site rendered in
Material's default blue, and the integrator had to manually
override Material's CSS to match.

Fix: when ``L2Instance.theme`` is set, ``main.py`` writes a
generated stylesheet to ``docs/stylesheets/_l2_theme.css`` and
registers it via ``extra_css``. The CSS overrides Material's brand
custom properties with the L2 theme's ``accent`` (header /
nav / link color) + ``accent_fg`` (text-on-primary):

```css
:root {
  --md-primary-fg-color: <theme.accent>;
  --md-primary-fg-color--light: <theme.accent>;
  --md-primary-fg-color--dark: <theme.accent>;
  --md-primary-bg-color: <theme.accent_fg>;
  --md-accent-fg-color: <theme.accent>;
  --md-accent-fg-color--transparent: <theme.accent>1a;
}
```

Same ``_l2_*`` underscore-prefix convention the logo/favicon
copies use, so ``.gitignore`` keeps the generated CSS untracked.

### 2. ``theme.logo`` / ``theme.favicon`` rejected relative paths

The loader required absolute paths (or ``http(s)://``) — relative
paths like ``logo.svg`` or ``./img/mark.svg`` raised
``L2LoaderError``. The user's natural authoring shape — drop the
brand asset next to the L2 YAML and reference it by relative
path — wasn't supported.

Fix: ``_load_optional_brand_asset`` accepts relative paths and
resolves them against the L2 YAML file's directory at load time.
The dataclass still carries an absolute path, so downstream code
(``_apply_brand_asset_override`` in ``main.py``) is unchanged.

### Tests

- ``tests/unit/test_l2_loader_theme.py`` — flipped
  ``test_theme_logo_relative_paths_rejected`` →
  ``test_theme_logo_relative_paths_resolve_against_yaml_dir``;
  same parameterized cases (bare filename / ``./`` / ``../``)
  now assert the resolved absolute path matches what the YAML's
  parent directory + the relative segments produce.
- ``tests/unit/test_main_macros.py`` — new file. Asserts the
  generated CSS carries the L2 theme's accent + accent_fg colors,
  registers via ``extra_css``, is idempotent across re-applies,
  uses the ``_l2_`` prefix, and reflects whatever palette the
  caller passes in (synthesizes a bright-pink theme to prove
  it's not a coincidence).

## v8.6.9 — Card layout padding on every text box

Rich text boxes rendered with content flush against the card's
left + right edges — visually cramped, especially when the box
shared a row with a visual. Pre-v8.6.9 the only padding the
generator added was the v8.6.3 vertical interior padding (top +
bottom ``<br/><br/>`` inside ``<text-box>``), which doesn't reach
the horizontal edges.

The QS UI exposes this as **Card layout padding** on every
visual / text-box card. The matching API field is
``GridLayoutElement.Padding`` — a CSS-shaped string (``"12px"``)
that the renderer applies as interior padding on all four sides
of the card, OUTSIDE the rich-text content. Confirmed by
round-tripping a manually-edited analysis through
``describe-analysis-definition``.

Two changes:

- ``common/models.py::GridLayoutElement`` gains a
  ``Padding: str | None = None`` field that round-trips to
  ``GridLayoutElement.Padding`` in the AWS API.
- ``common/tree/structure.py::GridSlot.emit`` defaults
  ``Padding="12px"`` for every ``TEXT_BOX`` element.

Visuals (``KPI`` / ``Table`` / ``BarChart`` / ``Sankey`` /
``LineChart``) still emit with no card padding — they self-render
their own internal padding via ``ChartConfiguration`` title /
subtitle / data-area styling, so adding card padding on top
double-pads the title row and clips the data area on narrow
visuals.

## v8.6.8 — current_transactions indexes for L2FT Transfer Templates dropdowns

The L2FT Transfer Templates sheet's **Template** + **Completion**
dropdowns each run ``DISTINCT`` against the tt-instances dataset.
That dataset's CTE JOINs ``current_transactions`` ON
``ct.template_name = t.template_name`` and runs ``EXISTS``
subqueries keyed on ``transfer_parent_id`` for chain-child
detection. Pre-v8.6.8, neither ``template_name`` nor
``transfer_parent_id`` had an index on the
``<prefix>_current_transactions`` matview, so every dropdown
distinct-enum scanned the full matview multiple times — visible
as a long-spinning dropdown.

Two new indexes on the ``<prefix>_current_transactions`` matview:

```sql
CREATE INDEX idx_<p>_curr_tx_template_name
    ON <p>_current_transactions (template_name);
CREATE INDEX idx_<p>_curr_tx_parent
    ON <p>_current_transactions (transfer_parent_id);
```

Mirrors the v8.5.6 transfer_type-dropdown fix one layer further
into the L2FT explorer. Postgres + Oracle both get them.

## v8.6.7 — L2FT Transfer Templates sheet shows data

The L2FT Transfer Templates sheet rendered empty in the demo
because **no row** in the seeded data carried a non-NULL
``template_name``. Two gaps:

1. The ``M.3.10g`` ``TransferTemplatePlant`` picker only handled
   templates whose first ``leg_rails`` entry was a ``TwoLegRail``;
   single-leg first leg_rails (the only shape both shipped L2
   instances declare — ``MerchantSettlementCycle`` /
   ``InternalTransferCycle``) got skipped with an ``Omitted``
   reason. Net: 0 ``TransferTemplatePlant`` rows on either
   ``spec_example`` or ``sasquatch_pr``.
2. Even when plants exist, the demo CLI helper
   (``cli/_helpers.py::build_full_seed_sql``) called
   ``default_scenario_for(instance)`` with the default
   ``l1_invariants`` mode — and ``M.4.2a`` re-categorized
   ``transfer_template_plants`` as broad-mode-only. So even after
   fix (1), TT plants never made it into the SQL the demo applies.

Fix:

- ``auto_scenario.py`` — picker branches on the first ``leg_rails``
  entry's rail kind. ``TwoLegRail`` keeps the existing
  source/destination_role resolution; ``SingleLegRail`` resolves
  ``leg_role`` and reuses the same account_id for both
  ``source_account_id`` and ``destination_account_id`` on the
  plant (the emit ignores the destination for single-leg).
- ``seed.py::_emit_transfer_template_rows`` — branches on the
  rail kind. TwoLegRail keeps the 2-leg debit+credit shape.
  SingleLegRail emits ONE leg with direction per
  ``rail.leg_direction`` (``Variable`` treated as ``Debit`` for
  plant purposes; closing-leg semantics aren't material to
  surfacing data on the explorer). chain_children attach via
  ``transfer_parent_id`` regardless of parent rail shape.
- ``cli/_helpers.py::build_full_seed_sql`` switches to
  ``mode="l1_plus_broad"`` so the demo gets BOTH the L1
  SHOULD-violation plants AND the broad-layer TT + RailFiringPlant
  rows. The L2FT Rails sheet now also surfaces broad-mode
  rail firings on top of the baseline.

Single-leg TT firings surface on the L2FT Template Instances
table as ``Imbalanced`` against ``expected_net = 0`` (one bare
leg can't sum to zero) — accurate L1 representation. Multi-leg
shared-transfer cycles (sibling legs joining via the same
``transfer_id`` by ``transfer_key``) are still deferred.

Hash impact: L1-invariants-mode YAML ``seed_hash`` is unchanged
(picker change is gated by broad-mode include). Broad-mode and
``l1_plus_broad``-mode sidecar hashes (``_BROAD_MODE_HASHES`` in
``tests/data/test_l2_seed_contract.py``) re-baked.

## v8.6.6 — Skip Oracle 19c JSON_VALUE functional indexes

The v8.6.4 metadata-cascade functional indexes ship the same shape
on both dialects (Postgres double-paren expression form / Oracle
single-paren). The shape is fine for Postgres + Oracle 21c+, but
Oracle 19c rejects the indexed expression at INSERT time:

```
ORA-40845: failed to create object (qjsn:engine)
```

Its JSON Search Context Engine needs either a JSON Search Index
or a ``JSON_VALUE(... RETURNING VARCHAR2(N))`` clause to evaluate
the indexed expression deterministically — and even with the
RETURNING clause the bare functional index appears unsupported
in 19c. Operators on Oracle 19c hit this on the first ``data
apply`` after fresh schema apply (the index gets created during
schema apply but isn't exercised until a row gets inserted with a
non-null ``metadata`` JSON; INSERT then fires the indexed
expression and crashes the load).

Fix: ``_emit_metadata_index_creates`` + ``_emit_metadata_index_drops``
in ``common/l2/schema.py`` now early-return for any non-Postgres
dialect. Postgres keeps the perf optimization; Oracle falls back
to a sequential scan on ``metadata``. The L2FT cascade still
works — just slower on Oracle. Postgres-CI matrix stays green.

The CI Oracle e2e has been a more recent Oracle than 19c, which is
why the regression slipped through. Future iteration could re-add
Oracle metadata indexes via the JSON Search Index path
(``CREATE SEARCH INDEX``) — deferred until there's a measured
need.

## v8.6.5 — L2FT metadata cascade write-back + release-pipeline OIDC + tag sanitization

Three fixes landed together — one user-visible (cascade dropdown
write-back), two CI/release-pipeline (OIDC trust policy + tag
sanitization in ``resource_prefix``).

### L2FT Metadata Value dropdown — drop the ``cascade_source`` wiring

The L2FT Rails / Chains / Transfer Templates sheets each have a
two-stage metadata picker: a ``Metadata Key`` dropdown that picks
which JSON path to filter on, then a ``Metadata Value`` dropdown
that picks one or more values for that key. Pre-v8.6.5 the
Value dropdown carried both ``LinkedValues`` (for the option
list) AND ``cascade_source`` (so the option list narrowed when
the Key changed) — but the combination of ``cascade_source`` +
``LinkedValues`` + ``MULTI_SELECT`` killed parameter write-back:
selecting a value in the dropdown didn't update ``pMetaValue``,
so the downstream Transactions table went empty (all rows
filtered out by an unset parameter).

Dropped ``cascade_source`` + ``cascade_match_column`` from all
three sites. The Value dropdown still narrows in practice
because the ``LinkedValues`` query is dataset-parameterized on
``pMetaKey`` — picking a Key still filters the option list, just
via the dataset query rather than the cascade wiring.

Promoted to entry 2.1 of the QuickSight quirks log under the
URL-parameter / control-sync footgun.

### Release pipeline — OIDC trust policy + ``resource_prefix`` sanitization

Two failures hit the v8.6.4 release pipeline that this release
fixes:

1. **OIDC AssumeRoleWithWebIdentity rejected from tag context.**
   The ``Github_e2e_testing`` IAM role's trust policy had a
   ``StringLike`` of exactly
   ``repo:chotchki/Quicksight-Generator:ref:refs/heads/main``,
   which doesn't match the ``refs/tags/v*`` form GitHub Actions
   sends from a tag-triggered workflow. Widened the
   ``StringLike`` to a list accepting both branch and tag refs
   (tag form scoped to ``refs/tags/v*`` to match the release
   trigger pattern). Trust-policy update + ``E2E_SETUP.md``
   doc + ``PLAN.md`` W.0.c parenthetical land in this release.

2. **Dashboard ID rejected dots in ``resource_prefix``.** The
   ``e2e-against-testpypi`` job in ``release.yml`` injected
   ``resource_prefix: "qs-release-${{ github.ref_name }}"`` —
   so the v8.6.4 tag produced ``qs-release-v8.6.4-...`` which
   AWS QuickSight rejected with ``Member must satisfy regular
   expression pattern: [\w\-]+`` (dots aren't in ``\w``).
   Inline bash parameter substitution
   (``SAFE_TAG="${GITHUB_REF_NAME//./-}"``) collapses dots to
   hyphens before injecting, so v8.6.5 → ``qs-release-v8-6-5-…``.
   Underscores in ``\w`` are fine (``spec_example`` was a red
   herring in the v8.6.4 error message).

Both fixes get exercised by this release's tag push.

## v8.6.4 — JSON functional indexes + coverage-badge PEP-668 fix

Two unrelated fixes shipped together — a perf win on the L2FT
metadata cascade, and unblocking the coverage badge job in CI.

### JSON functional indexes for the L2FT metadata cascade

The L2FT Postings dataset filters via
``WHERE JSON_VALUE(metadata, '$.<key>') IN (<<$pValues>>)`` per the
analyst's Key + Value picks. Pre-v8.6.4 there was no index on the
JSON-extracted expression, so each cascade pick triggered a full
``<prefix>_transactions`` scan.

``emit_schema(instance, dialect)`` now emits one functional index
per L2-declared metadata key:

```sql
CREATE INDEX idx_<prefix>_tx_meta_<key>
  ON <prefix>_transactions ((JSON_VALUE(metadata, '$.<key>')));
```

(Postgres needs the double-paren expression form; Oracle uses
single parens — both dialects emit the right shape.) Index name
sanitization replaces non-``[A-Za-z0-9_]`` characters with ``_``;
identifier length stays inside both PG (63) and Oracle (128) limits
even with long L2 prefixes + key names. Co-located ``DROP INDEX IF
EXISTS`` block at the top of the schema script keeps the apply-
script idempotent across re-runs and L2-key churn.

L2 instances declaring no metadata keys emit nothing for these
placeholders — no behavioral change for spec-example-shaped
deployments without a metadata cascade.

### Coverage-badge job — drop ``--system``, use a venv

W.8b's coverage-badge rewrite did
``uv pip install --system 'genbadge[coverage]'``, which fails on
the GHA ubuntu-latest runner because the system Python is PEP 668
externally-managed (``error: The interpreter at /usr is externally
managed``). Switched to a fresh ``uv venv`` + venv-scoped install
(same pattern the docs-portable-install job uses). The badge job
should finally land on the next push to main.

## v8.6.3 — Interior padding on rich text boxes

``rt.text_box(*parts)`` now auto-pads the interior with leading +
trailing ``<br/><br/>`` so rendered text doesn't sit flush against
the box's top / bottom edges. ``SheetTextBox`` has no padding
fields in the AWS API — interior breathing room only comes via the
rich-text grammar inside ``Content``. Two ``<br/>`` per side
matches what hand-authored QS UI text boxes emit when an editor
hits Enter twice for spacing.

Touches every text box in every shipped app — small visual nudge,
no behavioral change.

## v8.6.2 — Oracle top-queries dump: read LOB before format

The W.8a top-queries dump crashed the v8.6.0 release-pipeline e2e
job on the Oracle leg with ``AttributeError: 'LOB' object has no
attribute 'replace'``. Oracle's ``v$sqlstats.sql_fulltext`` is a
CLOB; ``SUBSTR`` on a CLOB returns a CLOB (not VARCHAR2), so
oracledb hands back ``oracledb.LOB`` objects on the query-text
column. The markdown formatter then tried ``.replace()`` on it and
died.

### Fix

- ``scripts/dump_top_queries.py::_fetch_oracle`` now reads any
  trailing-column LOB to a string before returning rows — formatter
  stays dialect-agnostic.
- Defensive top-level wrapper in ``main()`` catches any uncaught
  formatter exception and writes a "skipped" marker (perf
  observability shouldn't break CI for any reason).

## v8.6.1 — BarChart / LineChart axis labels need ApplyTo binding

v8.5.5 wired ``BarChart.emit()`` to auto-derive plain-English axis
labels via ``_field_label`` and put them in
``CategoryLabelOptions.AxisLabelOptions[0].CustomLabel``. The class
test went green (the JSON shape matched), but the labels still
rendered as raw snake_case (``account_id``, ``signed_amount``) on
deployed dashboards — the fix kept "not landing".

Root cause: AWS QuickSight requires an ``ApplyTo`` ref (FieldId +
ColumnIdentifier) inside ``AxisLabelOptions`` to bind ``CustomLabel``
to a specific field-well leaf. Without ``ApplyTo`` the label is
parsed cleanly but silently ignored — the same FieldId-binding
pattern table column headers use (``TableFieldOption.FieldId``)
applies to chart axes too.

### Operator-facing

- **BarChart axis labels actually render now.** Every populated axis
  carries the auto-derived plain-English label, bound to its leaf via
  ``ApplyTo``.
- **LineChart picks up the same auto-derive cascade.** Pre-v8.6.1 it
  only took explicit ``category_label="..."``/``value_label="..."``;
  now it falls back to ``_field_label(first_leaf)`` like BarChart.
  Same ``ApplyTo`` binding so the labels actually render.

### Code-facing

- ``common/models.py`` — new ``AxisLabelReferenceOptions`` dataclass;
  ``AxisLabelOptions`` gains an optional ``ApplyTo`` field.
- ``common/tree/visuals.py::_axis_label_apply_to(leaf)`` — helper
  that constructs the ApplyTo from a Dim/Measure leaf (FieldId via
  ``resolve_field_id``, Column via ``leaf.dataset.identifier`` +
  the column name from a ``Column`` ref / ``CalcField`` name /
  bare-string fallback).
- ``BarChart.emit()`` + ``LineChart.emit()`` populate ``ApplyTo`` on
  every axis label they emit. LineChart also picks up the auto-derive
  cascade for parity.
- ``tests/json/test_bar_chart_axis_labels.py`` gains
  ``test_every_bar_chart_axis_label_carries_apply_to`` — class-level
  regression that walks every emitted BarChart and asserts no
  CustomLabel-without-ApplyTo escapes the build.

## v8.6.0 — Phase W: e2e CI infrastructure + docs ship in the wheel

The headline operator-facing fix: ``quicksight-gen docs apply`` now
works from a PyPI install. Pre-v8.6.0 the CLI assumed the docs build
inputs (``mkdocs.yml``, ``main.py``, the L2 fixtures) lived at the
repo root and would fail with ``Inherited config file 'mkdocs.yml'
does not exist`` (or worse, silently break) when run from a wheel —
even though the ``[docs]`` extra installed cleanly. Behind that fix
sits the rest of Phase W: a real CI graph for the e2e tier, a
release-pipeline gate that holds prod publish on a live AWS run, and
two perf-debug surfaces (top-queries dump + unified coverage report).

### Operator-facing

- **``docs apply`` works from PyPI.** ``mkdocs.yml`` and ``main.py``
  now ship inside the package at ``src/quicksight_gen/``, with the
  bundled L2 fixtures at ``src/quicksight_gen/_l2_fixtures/``.
  ``--portable`` builds also work end-to-end from a fresh install.
- **Release pipeline auto-gates prod PyPI on live AWS.** A new
  ``e2e-against-testpypi`` job in ``release.yml`` installs the
  TestPyPI wheel into a fresh venv, deploys it against the operator
  Aurora, runs the API e2e for L1 + Investigation + Executives, and
  blocks ``publish-pypi`` on the result. Manual approval on the
  ``pypi`` environment still honored as the operator's fast-path.
- **Nightly browser-tier sweep.** ``e2e-pg-browser`` runs the
  Playwright tests on a 6am UTC cron + ``workflow_dispatch`` only
  (NOT on every push — saves ~30 min per push). Failure screenshots
  upload as a 14-day-retention artifact.
- **Oracle e2e leg in CI.** ``e2e-oracle-api`` mirrors the PG API
  job against the operator's external Oracle. Distinct ``e2e-oracle``
  concurrency group runs in parallel with PG.
- **Per-job perf snapshot.** Every e2e job runs
  ``scripts/dump_top_queries.py`` after pytest and uploads a markdown
  table of the top-50 most expensive queries (by ``total_exec_time``
  on PG / ``elapsed_time`` on Oracle) as a CI artifact. Surfaces
  missing-index regressions and matview-refresh hot spots without
  having to open a DB console.
- **Unified coverage report.** Each test matrix entry uploads its
  ``.coverage`` data; a new ``coverage`` aggregator job combines
  them, posts a markdown table to the GHA Step Summary, and
  republishes both an HTML artifact and the markdown report to the
  ``badges`` branch. The README's coverage badge now wrap-links to
  the markdown report on the badges branch instead of the workflow
  runs page — one click goes straight to per-file coverage numbers.
- **Quirks log lead with the URL-param footgun.** A new
  prologue at the top of ``docs/reference/quicksight-quirks.md``
  names the URL-parameter / sheet-control mismatch defect class up
  front and lists what we ship to minimize damage. Detailed
  per-quirk entries unchanged.

### Code-facing

- ``mkdocs.yml`` → ``src/quicksight_gen/mkdocs.yml`` (one level up
  from ``docs/`` since mkdocs rejects ``docs_dir: .``); ``main.py`` →
  ``src/quicksight_gen/main.py``. ``cli/docs.py`` finds them via
  ``Path(__file__).parent.parent`` so the same code path serves
  dev checkouts and installed wheels.
- ``src/quicksight_gen/_l2_fixtures/{spec_example,sasquatch_pr}.yaml``
  ship via ``package_data`` and are guarded by
  ``tests/unit/test_l2_fixtures_sync.py`` against drift from the
  ``tests/l2/`` source-of-truth.
- ``scripts/dump_top_queries.py`` — dialect-aware (``pg_stat_statements``
  / ``v$sqlstats``), filters to queries matching the L2 test prefix,
  auto-installs the PG extension on first run, tolerates
  missing-extension / no-permission cases by writing a "skipped"
  marker (never breaks CI).
- ``[tool.coverage.run]`` config in ``pyproject.toml`` scopes coverage
  to the package + omits tests / ``__main__``. CI matrix sets
  ``COVERAGE_FILE=.coverage.py<version>`` per entry so files combine
  cleanly via ``coverage combine``.
- New ``ci.yml::docs-portable-install`` job builds the wheel,
  installs in a fresh non-editable venv, runs ``docs apply
  --portable``, and asserts the rendered HTML lands. Catches the
  regression class on every PR / push to main, not just at release
  time.
- ``cleanup-pg`` extended to also sweep the browser tier's
  ``-pg-browser`` resource_prefix; new ``cleanup-oracle`` mirrors
  ``cleanup-pg`` for the Oracle leg.

### Tests

- ``tests/unit/test_l2_fixtures_sync.py`` — fail-loud on bundled L2
  fixture drift.
- ``tests/docs/test_docs_*`` — repo-root path bug fixed (was
  ``parents[1]`` resolving to ``tests/``, silently skipping these
  tests in CI), now points at the bundled ``mkdocs.yml``.

## v8.5.8 — bullets() defensively strips ``<br/>`` from list items

L2 institution YAML descriptions authored as ``description: |`` block
scalars carry embedded ``\n`` from human-readable line wrapping. Under
the v8.5.4 path, ``rt.bullets()`` routed each item through
``rt.markdown()``, which converted those ``\n`` to ``<br/>`` — and
QuickSight's text-box XML parser rejects ``<br/>`` as a child of
``<li>`` with ``Element 'li' cannot have 'br' elements as children``,
crashing ``CreateAnalysis`` on the L1 Drift sheet's
``l1-drift-accounts`` text box (and wherever else a YAML-described
account / template / limit schedule landed in a bullet list).

### Operator-facing

- **Dashboards using YAML-described L2 instances deploy again.** The
  L1 Drift / Limit Breach / Getting Started bullet sections now reflow
  embedded line breaks to spaces so QS accepts the text box.
- **Build-time warning surfaces affected items.** Each stripped
  ``<br/>`` raises a ``UserWarning`` showing the original item, so
  authors can spot block-scalar descriptions and decide whether to
  reword them (e.g. switch ``|`` to ``>`` or shorten the prose).

### Code-facing

- ``common/rich_text.py::bullets()`` now post-processes each item to
  strip ``<br>`` / ``<br/>`` / ``<br />`` (case-insensitive) and emits
  a ``UserWarning`` per offender via ``warnings.warn`` (stacklevel=2,
  so the warning points at the caller's ``rt.bullets(...)`` line, not
  at ``rich_text.py`` itself).
- New ``common/rich_text.py::markdown_inline()`` primitive: same
  XML-escape + inline-link handling as ``markdown()`` but collapses
  every newline-bearing whitespace run to a single space and strips
  leading / trailing whitespace. Use this when you need a guaranteed-
  no-``<br/>`` rendering of a single string outside a bullet context.
- ``tests/json/test_text_box_safety.py`` gains
  ``test_no_br_inside_li_in_text_box_content`` — class-level
  regression that walks every text box in every shipped app's emitted
  analysis JSON and asserts no ``<br>`` survives inside a ``<li>``.
- ``docs/reference/quicksight-quirks.md`` gains an entry for the
  ``<br>``-inside-``<li>`` parser restriction.

## v8.5.7 — Cross-sheet drills widen the destination date filter

Drills from a current-state sheet (Pending Aging / Unbundled Aging /
Supersession Audit — none in the universal date filter scope) into
the Transactions sheet (which IS scoped to a default 7-day window)
silently lost the target transfer's legs whenever the source row's
``posting`` was older than 7 days. The drill wrote ``pL1TxTransfer``
but did NOT write the date range params, so the destination's
universal filter remained narrow and the table rendered empty.

### Operator-facing

- **Right-click → View Transactions actually shows the transfer**
  even when it's older than the picker's default 7-day window. The
  drill now also writes ``pL1DateStart=1990-01-01`` and
  ``pL1DateEnd=2099-12-31`` (effectively "all time") so the target
  row is always in scope. Side effect: the Transactions sheet's date
  picker visibly snaps to those values after the drill — a known
  QuickSight in-app-drill UX wart with no clean fix; analysts
  re-narrow the picker if they want a tighter window.

### Engineering surface

- **New primitive ``DrillStaticDateTime``** in ``common/drill.py``.
  Pairs with a ``DateTimeParam`` destination and emits
  ``CustomValuesConfiguration.CustomValues.DateTimeValues=[value]``
  in the SetParametersOperation. Wired through
  ``common/tree/actions.py``'s ``DrillWriteSource`` union and
  re-exported from ``common/tree/__init__.py``.
- **``_wide_date_writes()`` helper** in ``apps/l1_dashboard/app.py``
  returns the ``[(P_L1_DATE_START, ...), (P_L1_DATE_END, ...)]``
  pair callers append to ``writes=`` on cross-sheet drills into
  universally-date-scoped destinations. Three sites updated
  (Pending Aging / Unbundled Aging / Supersession Audit detail
  tables).
- **Class-level regression** in
  ``tests/json/test_cross_sheet_drill_date_widening.py``: walks the
  emitted L1 dashboard JSON, asserts every drill into the
  Transactions sheet writes both date params with the wide static
  values, and pins the count of such drills (3) so a new drill
  added without the wide writes flags as an unexpected count
  bump that demands review.
- **Browser-tier e2e regression** in
  ``tests/e2e/test_l1_cross_sheet_drill_date_widening.py``: opens
  the deployed L1 dashboard, navigates to Pending Aging, right-
  clicks a stuck row → "View Transactions for this transfer", and
  asserts the destination Posting Ledger has ≥1 row. The harness's
  ``add_broken_rail_plants`` guarantees stuck rows older than 7
  days exist regardless of when the test runs.

### Documentation

- **New ``docs/reference/quicksight-quirks.md``** — canonical log
  of every QuickSight bug / undocumented behavior / silent-failure
  mode we've hit while building the four shipped dashboards.
  Categorized by class (silent rendering, drill quirks, control UX,
  data type, backend) with observed behavior + workaround +
  suggested QS-side fix per entry. Intended audience: defect
  reports filed with the QuickSight team. Linked from the docs
  site under Reference → Operations.

## v8.5.6 — Transactions sheet transfer_type dropdown perf index

The L1 Transactions sheet's ``transfer_type`` filter dropdown
spun on open. Same root cause as the v8.4.0 Drift dropdown
fix: QuickSight's date-narrowed
``SELECT DISTINCT transfer_type WHERE posting BETWEEN ...``
query had no useful index on the matview, so it full-scanned
every time.

### Operator-facing

- **L1 Transactions ``transfer_type`` dropdown opens fast.** New
  date-leading composite index
  ``idx_<prefix>_curr_tx_posting_transfer_type`` on
  ``<prefix>_current_transactions (posting, transfer_type)`` lets
  the dropdown's distinct-value query index-scan the date range
  and return distinct transfer_types directly.

### Engineering surface

- ``common/l2/schema.py`` adds the new index in the
  ``<prefix>_current_transactions`` matview's CREATE INDEX block.
- ``tests/data/test_l2_pipeline.py`` —
  ``test_current_transactions_matview_carries_v856_perf_index``
  snapshot regression. Re-asserts the existing four indexes too
  so a future refactor can't silently narrow the index footprint.

### Known follow-ups (not in v8.5.6)

- Other Transactions-sheet filter dropdowns (account / transfer /
  status / origin) either have an index already or land in a small
  enough cardinality bucket. Revisit if the next round of testing
  flags more.

## v8.5.5 — Plain-English axis labels on BarChart visuals

Closes Q.1.a.3 — the deferred sibling of v8.5.0's Table column
header fix. Pre-v8.5.5 ``BarChart`` accepted optional
``category_label`` / ``value_label`` / ``color_label`` overrides
but defaulted them to ``None`` — sites that didn't pass an
explicit override emitted no axis-label options at all, and
QuickSight rendered the raw snake_case column name as the axis
title (``account_id``, ``signed_amount``).

### Operator-facing

- **BarChart axes now derive plain-English titles automatically.**
  When the author doesn't pass ``category_label`` / ``value_label`` /
  ``color_label``, ``BarChart.emit()`` falls back to the first
  leaf's ``human_name`` via the same ``_field_label`` helper Table
  column headers use (v8.5.0). ``account_id`` → ``Account ID``,
  ``signed_amount`` → ``Signed Amount``, etc.
- **Author overrides still win.** A chart that needs a custom
  axis title (e.g. ``"$ Limit Cap (per day)"`` on the limit-breach
  view) keeps passing the override explicitly; auto-derivation is
  the default, not a forced replacement.

### Engineering surface

- ``common/tree/visuals.py::BarChart.emit`` falls back to
  ``_field_label`` (shared with Table) when label args are None
  and the corresponding field-well is non-empty.
- Class-level regression in ``tests/json/test_bar_chart_axis_labels.py``:
  8 tests (4 apps × 2 invariants):
  1. Every populated BarChart axis emits a label-options entry
     so QS has an explicit axis title.
  2. No emitted ``CustomLabel`` survives in raw snake_case form
     (``^[a-z]+(_[a-z0-9]+)+$``).

## v8.5.4 — Bullet markdown links + daily-balance carry-forward

Two small testing-feedback fixes.

### Operator-facing

- **Markdown links inside bullets now render.**
  ``rt.bullets()`` previously XML-escaped each item directly, so
  an inline ``[text](url)`` link inside a bullet survived as
  literal text. L1 Drift's "Getting Started" block (which feeds
  L2 description strings, markdown-shaped by SPEC convention)
  showed the raw markup instead of clickable anchors. Bullets
  now apply ``markdown()`` per item — same helper that fixed
  paragraph prose in v8.4.0. Soft line breaks inside bullets
  also work (``\\n`` → ``<br/>``).
- **Daily Statement picker default no longer lands on a blank
  day.** Baseline ``daily_balances`` rows previously emitted only
  for Mon-Fri (the days the rail loop posts on). The Daily
  Statement picker defaults to *yesterday*; when yesterday is a
  Saturday / Sunday / US holiday, the unfilled view left the
  picker on a date with no balance row and the table rendered
  empty. ``_emit_baseline_daily_balances`` now carries each
  account's last business-day EOD forward through every calendar
  day in the window — Friday's EOD survives Sat + Sun + Mon
  morning, etc., so weekend / holiday picker defaults always
  land on a real row.

### Engineering surface

- ``common/rich_text.py`` — ``bullets()`` applies ``markdown()``
  per item. ``tests/unit/test_rich_text.py`` adds 3 regression
  tests (inline link inside bullet, soft break inside bullet,
  plain-text bullets unchanged).
- ``common/l2/seed.py`` — ``_emit_baseline_daily_balances``
  fills forward into the full calendar-day window (not just
  ``state.business_days``). Drift invariant unchanged: weekend
  rows carry the prior business day's EOD, which equals
  ``SUM(signed_amount)`` through that day (no legs post on
  weekends, so the cumulative sum is the same).
- ``tests/data/test_l2_baseline_seed.py`` — re-locked
  ``test_full_seed_hash_lock_*`` SHA256 anchors for both
  ``sasquatch_pr`` and ``spec_example`` after the daily_balances
  shape change. v8.5.4 hash anchors are documented inline.

### Known follow-ups (not in v8.5.4)

- **Weekend transactions.** The rail loop still skips Sat / Sun
  for transaction generation (only daily_balances now span the
  full calendar). A "weekend has no transactions" Sunday view on
  Monday morning is still surprising relative to real banking.
  Tracked separately — bigger change since it touches the rail-
  firing density model end to end.

## v8.5.3 — CI hotfix: playwright in [dev] for pyright stub resolution

v8.5.2 added ``common/browser/helpers.py`` to the pyright include
scope and annotated its API with ``Page`` types. Locally pyright
was clean because the dev environment is ``uv sync --all-extras``
(includes ``[e2e]``, which has Playwright). CI installs
``--extra dev --extra audit`` only — Playwright wasn't there,
pyright couldn't resolve the stubs, and the ``pytest sessionstart``
gate failed with 269 cascaded "type is unknown" errors. v8.5.2
never reached the build/publish steps as a result.

### Engineering surface

- **``[dev]`` extras += ``playwright>=1.40``.** Just the Python
  package — ``playwright install`` (browser binaries) is still
  e2e-only. Adds ~5MB to the dev install but lets pyright resolve
  the inline PEP 561 stubs.
- ``uv.lock`` refreshed.

## v8.5.2 — Pyright noise cleanup (type-stub plumbing + duplicate class)

Internal hygiene — no user-visible behavior change. Brings the 318
pre-existing pyright errors that surfaced when running
``pyright <specific_file>`` down to **0** across the whole include
scope.

### Engineering surface

- **``pyproject.toml [tool.pyright] venvPath / venv``.** Plain
  ``pyright`` invocation now resolves third-party stubs (Playwright
  PEP 561 inline, boto3-stubs, mypy_boto3_quicksight, psycopg2). Prior:
  pyright resolved these against an empty venv path and surfaced
  ~300 false-positive "type is unknown" cascades from Playwright +
  boto3 — entirely a tooling-config gap, not actual type bugs.
- **``common/browser/helpers.py`` strongly typed at the surface.**
  Every Playwright-touching helper now annotates its ``page`` /
  ``locator`` parameters with ``Page`` (imported under
  ``TYPE_CHECKING`` to keep playwright a lazy runtime dep). The
  boto3 ``QuickSightClient`` is sourced from ``mypy_boto3_quicksight``
  for ``generate_dashboard_embed_url``. ``_retry_on_playwright_timeout``
  takes a typed ``Callable[[], T]`` and returns ``T``.
  ``contextmanager``-decorated ``webkit_page`` switched from the
  deprecated ``Iterator[Page]`` to ``Generator[Page, None, None]``.
- **``common/models.py`` — duplicate ``ColumnIdentifier`` class
  consolidated.** Two identical declarations existed (one at line 36,
  one at line 1218). Pyright flagged the second as obscuring the
  first; the second was an accidental re-introduction. Merged the
  docstring onto the canonical declaration.
- **``_strip_nones`` recursion typed.** Pyright can't represent the
  recursive Any cascade; targeted ignores describe the why.
- **``common/dataset_contract.py``** — ``build_dataset`` asserts
  ``cfg.datasource_arn is not None`` to match the
  ``Config.__post_init__`` invariant (the dataclass default is None
  for ergonomics, but a constructed ``Config`` always carries a
  resolved ARN).
- **Pyright include scope expanded** to cover ``common/browser``,
  ``common/dataset_contract.py``, ``common/models.py``. Future
  regressions in those files surface at the
  ``pytest sessionstart`` pyright gate (M.1.9c) instead of waiting
  to be caught by ad-hoc invocation.

## v8.5.1 — Phase W: fail-loud user ARN + workflow-level cleanup job

CI hardening — no user-facing app behavior changes.

### Engineering surface

- **W.4: ``get_user_arn()`` now raises on unset env var.** The
  function previously fell back silently to a hardcoded
  account-specific ARN string when ``QS_E2E_USER_ARN`` was unset.
  That masked CI misconfiguration (Phase W's ``ci-bot`` user has
  a different ARN than the local-dev default — the fallback
  produced an embed URL the bot couldn't view) and burned a
  project AWS account ID into source. Now: env var unset =
  ``RuntimeError`` at the call site, with a message pointing at
  ``.github/E2E_SETUP.md``.
- **W.7: ``cleanup-pg`` workflow-level cleanup job.** Added as
  the final job in ``e2e.yml``. ``needs: [e2e-pg-api-l1]`` +
  ``if: always()`` so it always runs after the e2e job — including
  when the e2e job is cancelled or its runner dies (GHA hard
  timeout, OOM, manual cancel). Belt-and-suspenders to the
  per-job ``Cleanup (always)`` step, which only catches step /
  test failures (the runner has to be alive to fire it). Shares
  the ``e2e-pg`` concurrency group so cleanup never interleaves
  with another run's deploy.
- **CLAUDE.md** updated to note ``QS_E2E_USER_ARN`` is now
  required (not a tunable).
- **PLAN.md** ticked W.0.a–W.0.d, W.1, W.2, W.3, W.4, W.7 — all
  shipped. Remaining: W.5 (4 apps × API matrix + Oracle leg),
  W.6 (browser tier), W.8 (release-pipeline gate), W.9 (Phase W
  release cut).
- **Class-level regression** in ``tests/unit/test_browser_helpers.py``:
  4 tests on ``get_user_arn()`` (env var set / unset / empty /
  error message points at runbook) + 1 test that the helpers
  module source contains no hardcoded AWS account ID inside an
  ARN literal.

## v8.5.0 — Plain-English column headers on table visuals

Closes the v8.4.0 "Known follow-ups" carryover. Pre-v8.5.0 every
Table visual in QuickSight rendered the raw snake_case column name
as the column header (``account_id``, ``business_day_start``,
``transfer_id``, ``signed_amount``) — readable to engineers, jarring
to CPAs and operators staring at the dashboard all day.

### Operator-facing

- **Table column headers are now title-cased by default.**
  ``account_id`` → ``Account ID``, ``business_day_start`` →
  ``Business Day Start``, ``signed_amount`` → ``Signed Amount``.
  The smart-title pass preserves common initialisms verbatim
  (``id``, ``eod``, ``url``, ``sql``, ``json``, ``uuid``, ``ip``,
  ``api``, ``aws``, ``qs``, ``etl``, ``csv``, ``tsv``, ``uri``,
  ``tz``, ``utc``) so the header reads ``EOD Balance`` not
  ``Eod Balance``.
- **Per-column override.** Authors can set
  ``ColumnSpec(name="balance", display_name="Balance ($)")`` for
  any column where title-cased snake_case isn't the right form.
  Override applies anywhere ``Column.human_name`` is read (today:
  Table headers; future: BarChart/KPI axis labels can wire the
  same surface).
- **Applies to every Table visual across all 4 apps.** L1
  Dashboard, L2 Flow Tracing, Investigation, Executives — class
  test walks every app's emitted JSON and asserts no CustomLabel
  survives in raw snake_case form.

### Engineering surface

- **``ColumnSpec.human_name`` property + ``_smart_title`` helper.**
  In ``common/dataset_contract.py``. ``human_name`` returns
  ``display_name`` when set, otherwise ``_smart_title(name)``.
  ``_smart_title`` does ``str.title()`` then re-uppercases any
  word in ``_INITIALISMS``.
- **``Column.human_name`` property on the typed Column ref.** In
  ``common/tree/datasets.py``. Looks up the dataset's contract
  via ``get_contract`` and returns the ColumnSpec's ``human_name``;
  falls back to ``_smart_title(name)`` when no contract is
  registered (test fixtures, kitchen-sink).
- **``TableFieldOption`` + ``TableFieldOptions`` models.** In
  ``common/models.py``. Wires
  ``TableConfiguration.FieldOptions.SelectedFieldOptions[].CustomLabel``
  through to AWS — QuickSight's documented per-column header
  override surface.
- **``Table.emit()`` builds FieldOptions from every leaf.** In
  ``common/tree/visuals.py``. New ``_field_label`` helper resolves
  Column refs (via ``Column.human_name``), CalcFields (via
  ``_smart_title(name)``), and bare strings (via
  ``_smart_title``). New ``_all_leaves`` collects field-well
  leaves across both aggregated + unaggregated table shapes.
- **Class-level regression coverage.**
  - ``tests/unit/test_column_human_name.py`` — 14 unit tests on
    ``_smart_title`` (including all 16 initialisms, mixed-case
    edge cases) and ``ColumnSpec.human_name``
    (override-vs-derived).
  - ``tests/json/test_table_column_headers.py`` — 12 class tests
    (4 apps × 3 invariants):
    1. Every Table visual emits ``ChartConfiguration.FieldOptions``.
    2. No surviving ``CustomLabel`` matches the raw snake_case
       pattern (``^[a-z]+(_[a-z0-9]+)+$``).
    3. ``SelectedFieldOptions`` count exactly matches field-well
       leaf count (no drift between FieldWells and FieldOptions).

## v8.4.0 — Independent system test bug sweep + cleanup isolation

Four user-reported bugs from a from-scratch independent system test
(against an isolated L2 + AWS account), each with class-level
regression coverage.

### Operator-facing

- **Text box paragraph breaks now render.** Multi-paragraph prose
  (e.g. ``l2_instance.description`` from YAML) passed through
  ``rt.body()`` previously XML-escaped ``\n\n`` paragraph breaks
  verbatim — QS only honors ``<br/>`` for breaks, so all paragraphs
  ran together as one wall of text. New ``rt.markdown()`` helper
  converts ``\n\n`` → ``<br/><br/>``, ``\n`` → ``<br/>``, escapes
  the rest. All 20+ ``rt.body(...)`` call sites across the 4 apps
  switched to ``rt.markdown(...)`` (semantically identical for
  plain text, correct for multi-paragraph + link-bearing strings).
- **Markdown links in text boxes are now clickable.** Same
  ``rt.markdown()`` helper converts inline ``[text](url)`` to
  ``<a href="url" target="_self">text</a>`` so analysts can click
  cross-references in any Getting Started / coverage block.
- **L1 Drift account/role dropdown spin fixed.** New 3-column
  date-leading composite index ``idx_<prefix>_drift_day_account_role``
  on both ``_drift`` and ``_ledger_drift`` matviews lets QuickSight's
  date-narrowed dropdown queries scan a small index range instead
  of the full account-leading index.

### Engineering surface

- **Per-deploy ResourcePrefix tag + cleanup scoping.** Every
  deployed resource now carries a ``ResourcePrefix=<resource_prefix>``
  AWS tag alongside ``ManagedBy`` and ``L2Instance``.
  ``json clean`` filters by ``ResourcePrefix`` automatically (any
  caller passing a ``cfg`` does this). Critical fail-CLOSED behavior:
  a resource with NO ``ResourcePrefix`` tag (i.e. deployed by a
  pre-v8.4.0 version) is NEVER swept by a prefix-scoped cleanup.
  This closes the W.3 incident class where a CI run wiped a local
  deploy of the same L2 instance — both deployed under
  ``L2Instance=spec_example`` but with different ``ResourcePrefix``
  values, the new filter keeps them isolated.
- **Class-level regression coverage** for every bug:
  - ``tests/unit/test_rich_text.py`` — 27 unit tests on the new
    ``markdown()`` helper.
  - ``tests/json/test_text_box_safety.py`` — walks every shipped
    app's analysis JSON, asserts no literal ``\n\n`` or unconverted
    ``[text](url)`` survives in any text-box content.
  - ``tests/json/test_cleanup.py`` — 3 new tests on the
    ``ResourcePrefix`` filter (matching, fail-closed on missing
    tag, composes with ``L2Instance``).
  - ``tests/data/test_l2_pipeline.py`` — schema snapshot test
    pinning the new index DDL on both Drift matviews.

### Phase W (CI e2e)

- **e2e workflow auto-fire re-enabled.** v8.3.x ran the e2e workflow
  once and it nuked the user's local deploy via the L2-scoped
  cleanup; trigger was disabled. With ``ResourcePrefix`` isolation
  in place that's no longer possible (cleanup only sweeps the
  workflow's own ``qs-ci-${run_id}-pg`` resources). Auto-fire on
  ``push:main`` restored.

### Known follow-ups (not in v8.4.0)

- **Plain-English column headers on table visuals** — landed in
  v8.5.0.

## v8.3.4 — Tree validator: every parameter-bound filter must be settable

Defensive follow-up to v8.3.3. No bug fix — just a regression guard
that closes the structural slice of the v8.3.3 footgun class.

### What it catches

A filter that binds a parameter (`CategoryFilter.with_parameter`,
`TimeEqualityFilter`, `NumericRangeFilter` with `minimum_parameter` /
`maximum_parameter`) where the analyst has no way to set that
parameter is a load-bearing bug — the `WHERE` clause matches nothing
at runtime, every visual on the sheet renders blank, no error
message anywhere. v8.3.3 closed the construction-time slice (forgot
`selectable_values=` on a dropdown); this closes the structural
slice (forgot the dropdown entirely).

New `App._validate_filter_param_settability` runs at
`emit_analysis()` / `emit_dashboard()` time, walks every
parameter-bound filter, and asserts each bound parameter has EITHER:

- A `ParameterControl` somewhere on the analysis (Dropdown, Slider,
  DateTimePicker, etc.), OR
- A non-empty `default` on the parameter declaration

Drill-target params (set programmatically by `Drill` writes, no UI)
satisfy the second clause via their default sentinel — they pass
unchanged.

`TimeRangeFilter`'s dict-form `{"Parameter": name}` bindings aren't
walked (the binding is a string `ParameterName`, not a typed
`ParameterDeclLike`, so the cross-reference would have to look up by
name). All shipped uses bind to `DateTimeParam`s with `RollingDate`
defaults, so the gap is theoretical for now.

### Test

`TestValidateFilterParamSettability` in `tests/unit/test_tree.py`
covers the four cases:
- no control + no default → raises (the bug)
- with control → passes
- with default only → passes (drill-target shape)
- `emit_dashboard()` validates the same way as `emit_analysis()`

## v8.3.3 — Hotfix: L1 Daily Statement account dropdown empty

Bug fix on top of v8.3.2.

### Bug

On the L1 dashboard's Daily Statement sheet, the **Account** dropdown
was empty (just the QuickSight "All" placeholder). Analysts couldn't
pick an account, so the underlying `CategoryFilter.with_parameter`
matched nothing and every KPI / table on the sheet rendered blank.

The `add_parameter_dropdown(...)` call was missing
`selectable_values=` — without it QuickSight has no source to
populate the dropdown's option list. Investigation's two SINGLE_SELECT
dropdowns (Money Trail's "Chain root transfer", Account Network's
"Anchor account") set this correctly via
`LinkedValues.from_column(...)` — Daily Statement was a one-spot
miss in the original M.2b.4 wiring.

### Fix

Wire the dropdown to the daily-statement-summary matview's
`account_id` column:

```python
selectable_values=LinkedValues.from_column(summary_ds["account_id"]),
hidden_select_all=True,
```

Surfaced now because the user ran a from-scratch independent system
test against an isolated L2 + AWS account — exactly the read-only
black-box flow that exposes "no live deploy ever picked an account
on this sheet" footguns. None of the e2e tests against the bundled
spec_example would have caught this either, since the sheet renders
fine with the dropdown empty (the assertion is on visual presence,
not on the dropdown being usable).

### Prevent the class

Per the project's "encode invariants in the type system, not in
validation tests" rule: tightened `ParameterDropdown.selectable_values`
from `SelectableValues | None = None` to `SelectableValues` (no
default). Same for `Sheet.add_parameter_dropdown(...)`. A future
dropdown wired without a source list now fails at construction
time with `TypeError: missing 1 required argument 'selectable_values'`
— the bug is unrepresentable.

`FilterDropdown.selectable_values` stays optional — filter dropdowns
auto-populate from the filter's bound column, so omitting the
override is the common, correct case.

Audited every other `add_parameter_dropdown` call site in the
shipped apps before tightening — all 8 already pass
`selectable_values`. No call-site changes required.

## v8.3.2 — Hotfix: Investigation App Info latest_date column

Bug fix on top of v8.3.1.

### Bug

The Investigation app's App Info ("i") sheet matview-status table
emitted SQL of the form `SELECT MAX(posted_day) AS latest_date
FROM <prefix>_inv_pair_rolling_anomalies`, but the matview's outer
SELECT projects `window_end`, not `posted_day` (`posted_day` lives
only in an inner CTE). Net effect on a deployed dashboard: the
Investigation App Info sheet's "latest_date" column would error out
or render blank for the `inv_pair_rolling_anomalies` row.

Bug shipped in v8.3.0 (introduced when V.3.b/c added the
`latest_date` column to the App Info matview status table).
Surfaced now because the new dataset CustomSQL smoke added in
v8.3.1's CI was the first thing that actually executed the
generated SQL against a live DB.

### Fix

`_INV_MATVIEW_BARE_SPECS` in `apps/investigation/datasets.py` now
pairs `inv_pair_rolling_anomalies` with `window_end` — semantically
the "freshest day this rolling-window matview is current through".

## v8.3.1 — Install reference, CI integration breadth, audit-verify regression guard

Patch release on top of v8.3.0. All additive — no breaking changes,
no behavior changes on the deployed dashboards.

### Operator-facing

- **New install reference page** at `Reference > Operations > Install`.
  Per-extra unlock matrix (what `[deploy]` / `[demo]` / `[demo-oracle]`
  / `[audit]` / `[docs]` / `[dev]` / `[e2e]` each turn on), four
  common shapes with copy-pasteable commands, and the "quote the
  brackets in zsh" footgun called out. Triggered by a real consumer
  who installed bare `quicksight-gen` and didn't know which extras to
  add for signed PDFs.
- **New `[deploy]` extra** — `boto3` + `botocore[crt]`. Closes the
  gap where bare install + `json apply --execute` would `ImportError`
  on `boto3` unless you already had the heavyweight `[dev]` extra.
  `botocore[crt]` is needed for AWS SSO (`aws sso login`) auth.

### Engineering surface

- **CI integration job widened.** Each push now runs against the
  live PG + Oracle Free service containers:
    - `audit apply --execute -o /tmp/report.pdf` — full reportlab
      PDF generation against every L1 invariant matview
    - `audit verify /tmp/report.pdf` — provenance roundtrip
      (recomputes embedded SHA256s, asserts match)
    - `verify_dataset_sql.py` — every emitted dataset's CustomSQL
      parses + executes against the live DB (`WHERE 1=0` smoke).
      Catches dialect-specific SQL bugs (JSON path concat, ORA-25154
      USING qualifier, ORA-00911 underscore identifier) in seconds
      instead of at browser-test time.
- **Audit verify hwm-pinning regression guard.** New DB-gated test
  in `tests/audit/test_pdf_matches_scenario.py` that:
  (1) renders an audit PDF, (2) inserts a row into the base table
  above the embedded high-water-mark, (3) asserts `audit verify`
  still passes, (4) cleans up the inserted row. Locks the property
  that makes audit PDFs re-verifiable indefinitely against an
  append-only base table.
- **CLI doc scrub.** Dropped four stale phase tags from user-visible
  Click `help=` strings + command docstrings (the bits `mkdocs-click`
  renders into `Reference > Operations > CLI reference`):
  `--viewport` help, `audit apply` "Phase U.1 ships…" paragraph
  (Phase U is fully shipped), `audit test` U.8.a/c + U.8.b refs,
  `audit verify` trailing `(U.7)`. Internal helper docstrings
  preserved as in-code anchors.

## v8.3.0 — Phase V (config / institution split + uv migration + small follow-ups)

A grab-bag phase of post-Phase-U cleanup. Nothing user-visible on the
deployed dashboards; everything else moved.

### Operator-facing

- **`docs apply --portable`** — new flag. Builds a static site that
  opens via `file://` (no web server needed). Drops Material's Google
  Fonts CSS link, post-processes the rendered `qs-graphviz-wasm.js`
  to inline the WASM diagram bundle so diagrams render without the
  ES-module imports browsers block under `file://`. Ship-on-USB-stick
  / shared-drive workflow now works end-to-end.
- **CLI reference** — auto-generated man-page-style page at
  `Reference > Operations > CLI reference` via `mkdocs-click`.
  Reads the live Click command tree at docs build time, so it tracks
  every flag change automatically.
- **App Info sheet** — now bakes the `quicksight-gen` version into
  the deploy stamp + adds a `latest_date` column to the matview
  status table. Compare a base-table row's date against a matview
  row's date to spot stale matviews at a glance. New ETL handbook
  troubleshooting section explains the diagnostic.
- **`json apply` auto-emits `out/datasource.json`** when
  `demo_database_url` is set — closes the orphan-datasource gap that
  hit single-app deploys.

### Engineering surface

- **Strict `config.yaml` loader.** Rejects unknown keys, L2-only keys
  (theme / persona / rails / chains / accounts / templates / limit
  schedules / instance / description / seed_hash), and hand-set
  `l2_instance_prefix`. Each rejection points at where the field
  actually belongs. Test boilerplate collapsed: 17 hand-built
  `Config(...)` literals → `make_test_config(**overrides)` factory.
- **uv migration.** `uv.lock` committed (98 packages); CI / Release /
  Pages workflows use `astral-sh/setup-uv@v6` + `uv sync --frozen`.
  `pip install` end-user paths in `release.yml`'s
  TestPyPI / PyPI verify-install jobs intentionally kept on pip
  (they prove the published wheel installs cleanly via pip).
- **Reference nav regroup.** Flat 11-item Reference split into 3
  nested sections — App handbooks / Data contract / Operations.
  Content-area max-width bumped from 1220px → 1440px so wide
  topology diagrams breathe.

### Demo data

- **Baseline tune-up.** Two classes of spurious L1 invariant
  violations the realistic baseline still surfaced are gone:
  - **Limit_breach** on customer outbound (sasquatch_pr 51 → 0):
    per-`(account, transfer_type, day)` cumulative-outbound tracking
    in `_BaselineState`; firings clamped to remaining cap, skipped
    when remaining < $50.
  - **Overdraft** on intermediate clearing accounts: new
    `_emit_baseline_cascade_credits` walks already-emitted firings
    and materializes the missing credit legs that
    `TransferTemplate`-only cascades skipped (per-merchant per-day
    `CardSaleDailySettlement`-shaped credits on
    `MerchantPayableClearing`, paired credits for
    `InternalTransferSuspenseClose`, etc.). ZBA sub-accounts now
    funded via opening balance + matching daily inbound from the
    funds pool. Counter-legs net to zero.

### Misc fixes

- Graphviz diagrams missing on cross-page nav (Material's instant-nav
  swaps DOM but doesn't re-execute extra_javascript) — script now
  always runs once on init AND subscribes to `document$`.
- `docs/walkthroughs/screenshots/` re-captured at 1280×900 against
  the live `spec_example` Postgres deployment; reflects Phase R's
  realistic baseline + Phase U's audit work.
- Explicit plain-English `category_label` / `value_label` on the 4
  remaining BarChart sites that were defaulting to raw column names.
- `botocore[crt]` added to `[dev]` + `[e2e]` extras for AWS SSO
  login support (was failing in `docs screenshot` / e2e auth paths).

---

## v8.2.2 — CI coverage-badge job also needs `[audit]` extra

Follow-up to v8.2.1. v8.2.1 fixed the test job + release.yml install
lines to install `[dev,audit]`, but `ci.yml` had a SECOND install line
on the `coverage-badge` job (line 158) that I missed — still pinned to
`[dev]` only. Result: v8.2.1's CI run failed at `coverage-badge` with
the same `ModuleNotFoundError: No module named 'pyhanko'` from re-
running the audit tests for coverage XML.

(v8.2.1's release pipeline succeeded — TestPyPI + PyPI publishes both
went through. This bump only restores green CI on subsequent commits.)

No code changes from v8.2.1; same Phase U audit feature shipping.

---

## v8.2.1 — CI/release pipeline fix for v8.2.0 audit deps

Pipeline-only re-cut of v8.2.0. The v8.2.0 tag is on github but never
published to PyPI — its release pipeline failed at the test stage with
`ModuleNotFoundError: No module named 'pyhanko'`. Both `ci.yml` and
`release.yml` installed `[dev]` only; `tests/audit/*` invoke
`audit apply --execute` which lazy-imports pyhanko on every PDF render
(empty reviewer signature widgets land via pyhanko regardless of
whether `signing:` is configured). Switched both install lines to
`pip install -e ".[dev,audit]"` so the audit module is exercised end-
to-end with all its declared deps.

No code changes from v8.2.0; the entire Phase U audit feature ships
unchanged. Bumped to v8.2.1 (rather than force-moving the v8.2.0 tag)
for tag-history honesty.

---

## v8.2.0 — Audit Reconciliation Report (regulator-ready PDF)

Phase U adds a **fifth artifact group** to the CLI: `audit`. The
`quicksight-gen audit apply -c config.yaml --execute -o report.pdf`
verb queries the per-prefix L1 invariant matviews + base tables
directly, formats the result via `reportlab`, and emits a regulator-
ready PDF with a verifiable provenance fingerprint binding it to
its source data. Bypasses QuickSight pixel-perfect entirely (cost
+ wrong shape for the auditor).

### What ships

- **`quicksight-gen audit apply`** — Cover page (institution +
  period band) → executive summary (transaction / transfer counts,
  dollar volume gross+net, exception counts) → per-invariant
  violation tables (drift / overdraft / limit_breach / stuck_pending
  / stuck_unbundled / supersession audit) → per-account-day Daily
  Statement walk → sign-off block with reviewer signature widgets
  + fillable Notes field → provenance appendix with the four-input
  fingerprint canonical bytes + matview SHA256 evidence sidecar +
  embedded L2 YAML attachment + manual-recompute Python script
  attachment. Optional `signing:` config block auto-signs via
  pyHanko (PEM RSA key + cert).
- **`quicksight-gen audit verify report.pdf -c config.yaml`** —
  Recomputes the provenance fingerprint from the live DB + L2 YAML
  + code identity and compares against the embedded fingerprint;
  exits non-zero on mismatch. Regulator workflow: integrator runs
  `verify` against the operator's snapshot to confirm the report
  hasn't been tampered.
- **`quicksight-gen audit test`** — Mirrors the four other
  artifact-group test verbs; runs `pytest tests/audit/` (73 cases)
  + pyright on `cli/audit/`.
- **L1 dashboard fix.** Dropped the date-scope FilterGroups from
  the L1 dashboard's stuck_pending / stuck_unbundled / supersession
  sheets. Their underlying matviews are current-state (no date
  filter on the audit-PDF side either); a "stuck" item is stuck
  until cleared, regardless of the analyst's period of interest.
  Pre-fix the dashboard's `[today-7, today]` default scope dropped
  planted rows whose `posting` was outside the window — making the
  dashboard disagree with the audit PDF that surfaces every
  current-state row.

### Release gate (U.8.b)

The credibility-contract release gate: every number on the audit
PDF agrees with what the deployed L1 dashboard shows AND what the
scenario primitives planted, for the same period + L2 + DB
snapshot. Three-way assert per invariant (`expected == PDF ==
dashboard`). Verified live across both dialects:

- **Postgres `spec_example`**: 6/6 PASS
- **Oracle `spec_example`**: 6/6 PASS
- Total: 12/12 across the dialect × invariant matrix

### Compatibility

Additive — new CLI group, no breaking changes to the existing four
artifact groups (`schema | data | json | docs`). The `audit` group
needs `reportlab` (added to a new `[audit]` extra in
`pyproject.toml`); core install is unaffected. Provenance signing
is optional — omit the `signing:` config block and the PDF emits
unsigned with empty signature widgets for downstream tooling
(Adobe Sign / DocuSign / pyHanko CLI).

### Limitations + follow-ups

- Dialect-aware audit SQL: the audit's inline SQL uses ANSI
  `DATE 'YYYY-MM-DD'` literals which work on both Postgres + Oracle
  today; if a future invariant requires dialect branching, the
  `tests/audit/test_sql.py` snapshot becomes per-dialect.
- Exec-summary aggregate metrics (transaction count / transfer count
  / dollar volume) deferred from the U.8.b three-way assert — not
  cleanly derivable from plant tuples (baseline + plants summed),
  so the "derive from scenario" rule doesn't apply. PDF↔dashboard
  agreement on these can land later as a straight A==B check.

---

## v8.1.0 — Drop the system `dot` binary; render diagrams in the browser via WASM

Phase T migration. Every diagram on the docs site (L2 topology /
dataflow / hand-authored conceptual `.dot` files) now renders
client-side via `@hpcc-js/wasm-graphviz`. The build-time
`graphviz`/`dot` system binary requirement is gone — `apt-get
install graphviz` deleted from every CI / Release / Pages
runner.

### What changed

- **`common/handbook/diagrams.py`**: every `render_*` helper now
  returns a graphviz **DOT source string** (via `Digraph().source`)
  instead of pre-rendered SVG. Reuses the existing
  `_build_*_graph` builders untouched — only the output shape
  changed. `_to_svg` (the `.pipe(format='svg')` shellout helper)
  is gone.
- **`main.py`**: the `diagram(...)` mkdocs-macro emits
  `<figure class="qs-diagram"><script type="text/x-graphviz">DOT</script></figure>`
  for every diagram. The figure wrapper keeps the existing
  `qs-lightbox.js` click-to-zoom working unchanged.
- **`stylesheets/qs-graphviz-wasm.js`**: ~50-line client-side
  bootstrap. Finds every `<script type="text/x-graphviz">` block
  on page load, dynamically imports `@hpcc-js/wasm-graphviz` 1.x
  from jsDelivr (`+esm`), runs `g.dot(source)` against each, and
  replaces the script tag with the rendered SVG.
- **`.github/workflows/{ci,release,pages}.yml`**: 5 instances of
  `apt-get install -y graphviz` removed.
- **`pyproject.toml`**: `graphviz>=0.20` Python lib stays as a
  `[docs]` dep — it's still constructing DOT source strings — but
  the comment explicitly notes the system binary is no longer
  required.
- **`tests/docs/test_handbook_diagrams.py`**: assertions updated
  to validate the DOT shape (`digraph {...}` syntax, expected
  node labels, expected edge counts) instead of SVG content.

### Why this exists

Pre-Phase-T, every CI / Release / Pages job needed
`graphviz`/`dot` installed (a system package, not a Python wheel)
to build the docs site. New integrators hit the same wall on
their laptops. Phase S spiked two browser-rendered alternatives —
Mermaid + ELK and graphviz WASM — and concluded that
`@hpcc-js/wasm-graphviz` produces byte-identical layouts to the
build-time `dot` (it IS graphviz, just compiled to WASM and run
client-side). Mermaid + ELK lost on layout fidelity; the spike
findings live in `PLAN.md` § Phase S.

### Compatibility

No CLI / Python API changes. The `quicksight-gen docs apply`
verb still emits a static site; the only difference is the site
no longer requires `dot` at build time. Diagrams render on
first page-load via the WASM bundle (~800kB, cached by the
browser after first hit).

### Limitations + follow-ups

- Diagrams now require JavaScript to render; static-export
  consumers (PDF / printed docs) won't get them. Open follow-up
  if this becomes a real ask.
- `qs-graphviz-wasm.js` CDN-loads from jsDelivr. Vendoring the
  WASM lib into `docs/_static/` for offline-friendly deploys is
  queued as PLAN.md T.7 — defer if jsDelivr's reliability is
  acceptable for the demo audience.

## v8.0.1 — Release pipeline fix (no functional change)

The v8.0.0 Release workflow stopped at its "Smoke test wheel" step
with `ERROR: file or directory not found: tests/test_models.py`.
Q.3.a.9 had reorged those tests into
`tests/{schema,data,json,docs,unit}/`, but the workflow's pinned
test paths weren't updated. Because the publish-* jobs never ran
(pipeline halted at smoke), v8.0.0 never reached PyPI; v8.0.1
ships the same v8.0.0 codebase plus the workflow path fix.

If you were waiting on v8.0.0, install v8.0.1 — same change, same
upgrade story. The v8.0.0 git tag is preserved as a marker but
points at the broken Release run.

## v8.0.0 — CLI redesign: four artifact groups, emit/--execute pattern

Clean-break CLI redesign per `Q3_CLI_REDESIGN.md`. The single
`main` group now hangs four artifact groups instead of the v7.x
top-level verbs:

| Old verb (v7.x) | New shape (v8.0.0) |
| --- | --- |
| `quicksight-gen generate --all -c X -o Y` | `quicksight-gen json apply -c X -o Y` |
| `quicksight-gen generate <app> ...` | `quicksight-gen json apply ...` (no per-app filter; always emits all 4) |
| `quicksight-gen deploy --all --generate ...` | `quicksight-gen json apply ... --execute` |
| `quicksight-gen cleanup --dry-run` | `quicksight-gen json clean` (default IS dry-run) |
| `quicksight-gen cleanup --yes` | `quicksight-gen json clean --execute` |
| `quicksight-gen demo apply --all -c X -o Y` | `schema apply --execute && data apply --execute && data refresh --execute && json apply --execute` |
| `quicksight-gen demo emit-{schema,seed,refresh}` | `schema apply` / `data apply` / `data refresh` (no `--execute`) |
| `quicksight-gen demo apply-{schema,seed,refresh}` | `schema apply --execute` / `data apply --execute` / `data refresh --execute` |
| `quicksight-gen demo seed-l2 <yaml> [--lock\|--check-hash]` | `quicksight-gen data hash <yaml> [--lock\|--check]` |
| `quicksight-gen demo etl-example` | `quicksight-gen data etl-example` |
| `quicksight-gen demo topology` | dropped (call `render_topology()` directly in Python) |
| `quicksight-gen export docs -o DIR --l2-instance Y` | `quicksight-gen docs export -o DIR --l2 Y` |
| `quicksight-gen export screenshots ...` | `quicksight-gen docs screenshot ...` |
| `quicksight-gen probe ...` | `quicksight-gen json probe ...` |

### What's new

#### Four artifact groups

Top-level CLI is organized around the four artifacts the tool
produces — `schema | data | json | docs`. Each artifact has at
minimum `apply` / `clean` / `test`; some carry additional verbs:

```
schema  apply | clean | test
data    apply | refresh | clean | hash | etl-example | test
json    apply | clean | test | probe
docs    apply | serve | clean | test | export | screenshot
```

#### emit-vs-execute pattern

Every destructive operation defaults to *emit* — print SQL to
stdout, write JSON to `out/`, build the static site to `site/`.
Pass `--execute` to actually run the destructive thing (connect
to the demo DB, deploy to AWS QuickSight). The `docs` group has
no `--execute` because building a static site is the operation.

```bash
# Emit DDL to stdout (review first)
quicksight-gen schema apply -c run/config.yaml

# Same DDL, run against the demo DB
quicksight-gen schema apply -c run/config.yaml --execute

# Same shape for data + json
quicksight-gen data apply -c run/config.yaml --execute
quicksight-gen json apply -c run/config.yaml -o out/        # JSON only
quicksight-gen json apply -c run/config.yaml -o out/ --execute  # + AWS deploy
```

The safe default (just emit) means an integrator can never
accidentally drop a table or redeploy a dashboard.

#### Bundled JSON emit (no per-app filter)

`json apply` always emits all four bundled apps — investigation,
executives, l1-dashboard, l2-flow-tracing. Per-app development
ergonomics from M / N / O are gone (they didn't earn their
keep). One verb, four apps, every time.

#### `data hash` for canonical seed_hash workflow

The `--lock` / `--check-hash` workflow that used to live on
`demo seed-l2` is now `quicksight-gen data hash <yaml>` with
`--lock` and `--check` flags. The canonical-date plant-only seed
(`emit_seed`, anchored at 2030-01-01) drives the hash so it stays
stable across days. Distinct from `data apply`, which composes
the live full-seed pipeline (90-day baseline + plant overlays
rolled against today's date).

#### `cli_legacy.py` deleted

`src/quicksight_gen/cli_legacy.py` (1854 lines) is gone. All
shared helpers lifted into `cli/_helpers.py` and
`cli/_app_builders.py`. No aliases — the v7.x verbs do not exist
on v8.0.0. Update your scripts.

### Migration notes

If you were running `quicksight-gen demo apply --all`, that's
now four separate commands — chain them with `&&`:

```bash
quicksight-gen schema apply -c run/config.yaml --execute && \
quicksight-gen data apply   -c run/config.yaml --execute && \
quicksight-gen data refresh -c run/config.yaml --execute && \
quicksight-gen json apply   -c run/config.yaml -o out/ --execute
```

Drop the per-app argument from any `generate` / `deploy` /
`probe` invocation. `json apply` always handles all four.

If you were calling `quicksight-gen demo seed-l2 X --lock`, that
moves to `quicksight-gen data hash X --lock`. The `--check-hash`
flag is now just `--check`.

### Internal restructure

- `cli/__init__.py` — defines a fresh `main` group hanging only
  schema / data / json / docs.
- `cli/schema.py`, `cli/data.py`, `cli/json.py`, `cli/docs.py` —
  one Click group per artifact.
- `cli/_helpers.py` — shared options
  (`l2_instance_option` / `config_option` / `output_option` /
  `execute_option`) and shared functions
  (`resolve_l2_for_demo` / `build_full_seed_sql` /
  `emit_to_target` / `connect_and_apply` / `write_json` /
  `prune_stale_files` / `load_config` / `APPS`).
- `cli/_app_builders.py` — per-app JSON-emit helpers
  (`_generate_<app>` × 4, `_all_dataset_filenames`,
  `_dashboard_id_for_app`, `_resolve_l2`).
- New public emitters in `common/l2/`:
  `emit_schema_drop_sql(instance, dialect)` and
  `emit_truncate_sql(instance, dialect)`.

### Test reorg

`tests/` reorganized to mirror the artifact groups —
`tests/{schema,data,json,docs,unit}/`. The test files moved
in-place via `git mv` so blame survives. CLI test invocations
updated everywhere; the `data hash` workflow lives in
`tests/data/test_cli_seed_l2.py` (kept the filename for the diff
hint, since it tracks the same behavior).

### CI / scripts

- `.github/workflows/ci.yml` — chains the four artifact groups
  explicitly in both Postgres and Oracle integration jobs.
- `scripts/p9_deploy_verify.sh` — same chain per cell.
- `run_e2e.sh` — `json apply --execute` replaces
  `deploy --all --generate`.
- `scripts/bake_sample_output.py` — `json apply` replaces
  `generate --all`.

### Doc sweep

CLAUDE.md, README.md, every handbook page (l1, executives,
customization, l2_flow_tracing, integrator, Schema_v6), and
every walkthrough page that named an old verb (~17 docs files
total) updated to use the new four-artifact shape. mkdocs
build --strict passes.

## v7.4.0 — Persona-neutral docs + piecewise demo CLI

This release rolls together the Phase Q.5 persona-neutral docs work
(originally pre-tagged as v7.3.0 in code but never released) and a
set of additive `demo` CLI primitives that let integrators emit or
apply schema / seed / matview-refresh independently. Cuts cleanly
off v7.2.0; no breaking schema or CLI changes — old verbs continue
to work unchanged. The next release (planned v8.0.0) will be a
clean-break CLI redesign per `Q3_CLI_REDESIGN.md`.

### What's new since v7.2.0

#### Phase Q.5 — Persona-neutral docs (full L2-driven substitution)

With `QS_DOCS_L2_INSTANCE=tests/l2/spec_example.yaml` the rendered
mkdocs site contains zero unintentional persona tokens; with
`sasquatch_pr` the curated Sasquatch / Juniper / Cascadia / Shell
narrative renders exactly as before; with an integrator's own L2
YAML (no `persona:` block), neutral prose derived from L2 primitives
fills in.

##### Optional `persona:` block on L2 YAML

```yaml
persona:
  institution: ["Sasquatch National Bank", "SNB"]
  stakeholders: ["Federal Reserve Bank", "Fed", "..."]
  gl_accounts:
    - {code: "gl-1010", name: "Cash & Due From FRB", note: "..."}
  merchants: ["Big Meadow Dairy", "Bigfoot Brews", "..."]
  flavor: ["Margaret Hollowcreek", "Pacific Northwest", "..."]
```

Loaded into `L2Instance.persona: DemoPersona | None` by
`common/l2/loader.py::_load_persona`. Handbook templates read it
via `vocab.institution.name` / `vocab.gl_accounts` / etc. Empty /
missing block → neutral prose derived from L2-primitive defaults.
See the new `walkthroughs/customization/how-do-i-brand-my-handbook-prose.md`
for the integrator-facing field-by-field map.

##### Investigation walkthroughs split

The 4 Investigation walkthrough pages (recipient-fanout,
volume-anomalies, money-trail, account-network) now have body prose
written as L2-portable mechanics. Below the body, a collapsed
`??? example "Worked example: <fixture>"` admonition (mkdocs-material
`pymdownx.details`) renders the curated narrative — but only when
`vocab.demo.has_investigation_plants` is true. Against `spec_example`
the admonitions don't render and the body stands on its own.

##### `common/persona.py` rewrite

- Dropped `SNB_PERSONA` module constant. `persona.py` is now a
  generic typed skeleton — `DemoPersona` dataclass with empty-tuple
  defaults and a single `GLAccount` helper.
- Sasquatch persona content moved into `tests/l2/sasquatch_pr.yaml`'s
  new `persona:` block.
- `api/common-foundations.md` API ref describes the generic shape.

##### CI gates

- **`tests/test_docs_persona_neutral.py`** — builds the rendered
  site against both bundled fixtures. spec_example must have zero
  persona tokens outside a small per-page allowlist; sasquatch_pr
  must render the curated flavor (anti-regression on over-deletion).
  Asymmetric "tighten only" bound — new leaks fail; reductions
  must lower the bound.
- **`tests/test_docs_links.py`** — sweeps every internal href / src
  in the built site for both file existence and fragment anchor
  presence (mkdocs `--strict` only catches missing files).

##### Other docs improvements

- **Shape C IA** landed under For Your Role / Concepts / Handbook /
  Walkthroughs / API. For Your Role moved to nav position 1.
- **`quicksight-gen export screenshots`** CLI: captures all 4
  deployed dashboards at 1280×900 with optional URL date overrides.
  29 PNGs captured; 15 wrapped in collapsed `??? example "Screenshot"`
  admonitions.
- **Dataflow diagrams** on each handbook overview now correctly
  render dataset → sheet edges (was emitting an empty graph).
- **mkdocs-material library SVG fallback hidden** when no L2 logo
  is set (prevents persona-specific mark from leaking when the
  active L2 declares no `theme.logo`).
- Executives dataset bug: `WHERE t.status = 'success'` (lowercase,
  doesn't exist in the data) → `'Posted'`.
- 28 dead links swept across handbook + walkthrough pages.

#### Phase Q.3.a — Piecewise demo CLI primitives

Six new `demo` subcommands give the integrator emit-vs-apply
control over each piece of the schema/seed/refresh pipeline:

| Command | What it does |
|---|---|
| `demo emit-schema [-o FILE]` | Schema DDL → stdout/file. No DB connection. |
| `demo emit-seed [-o FILE]` | Full seed SQL (90-day baseline + plants) → stdout/file. |
| `demo emit-refresh [-o FILE]` | REFRESH MATERIALIZED VIEW SQL → stdout/file. |
| `demo apply-schema` | Connect + apply just the schema. |
| `demo apply-seed` | Connect + apply just the seed. |
| `demo apply-refresh` | Connect + run just the matview refresh. |

`demo apply` keeps working unchanged — refactored internally to
compose the same `_resolve_l2_for_demo` + `_build_full_seed_sql`
helpers so the bundled command stays in lockstep with the
piecewise emit-seed.

Each new command accepts `--l2-instance PATH` (defaults to
`spec_example`), `-c CONFIG`, and `-o FILE`.

Useful for:
- Piping schema DDL into a different DB tool: `quicksight-gen demo emit-schema | psql ...`
- Inspecting the seed before applying: `demo emit-seed -o seed.sql; less seed.sql`
- Iterating on seed plants without re-running the schema:
  `demo apply-seed && demo apply-refresh`

#### `Q3_CLI_REDESIGN.md` design doc

Drafts a broader CLI restructure — four artifacts (schema | data |
json | docs) × four operations (apply | clean | test, plus
artifact-specific extras). The user has reviewed and answered the 7
open questions. Execution lands in v8.0.0 as a clean-break.

### Migration

No action required for v7.4.0. Existing scripts using `generate` /
`deploy` / `cleanup` / `demo apply` continue to work. The new
`demo emit-*` / `demo apply-*` commands are additive.

If you've embedded the docs against your own L2 fixture, the
worked-example admonitions only render when your fixture plants
Investigation scenarios. To get the bundled Juniper / Cascadia
narrative, point docs at `tests/l2/sasquatch_pr.yaml`
(`QS_DOCS_L2_INSTANCE=...`).

**v8.0.0 will be a breaking CLI change** per `Q3_CLI_REDESIGN.md`.
Old verbs (`generate`, `deploy`, `cleanup`, `demo apply`,
`demo emit-*`, `demo apply-*`, `export *`, `probe`) drop in favor
of `<artifact> <verb>` shape (`schema apply`, `data clean`, etc.).
No deprecation cycle.

## v7.3.0 — superseded

This version was bumped in code (commit `a5170b6`) but never
tagged. Subsequently rolled into v7.4.0 above when the persona-block
walkthrough + Q.3.a piecewise commands landed in the same release
window.

## v7.2.0 — Realistic 3-month baseline seed + L2-coverage runtime assertions

Phase Q.5 finishes the multi-tenant docs story: with
`QS_DOCS_L2_INSTANCE=tests/l2/spec_example.yaml` the rendered
mkdocs site contains zero unintentional persona tokens; with
`sasquatch_pr` the curated Sasquatch / Juniper / Cascadia / Shell
narrative renders exactly as before; with an integrator's own L2
YAML (no `persona:` block), neutral prose derived from L2 primitives
fills in. No schema changes, no breaking CLI changes — only docs
infrastructure + a new optional YAML block.

### What's new — `persona:` block on L2 YAML

- **Optional `persona:` top-level block** in any L2 instance YAML:
  ```yaml
  persona:
    institution: ["Sasquatch National Bank", "SNB"]
    stakeholders: ["Federal Reserve Bank", "Fed", "..."]
    gl_accounts:
      - {code: "gl-1010", name: "Cash & Due From FRB", note: "..."}
    merchants: ["Big Meadow Dairy", "Bigfoot Brews", "..."]
    flavor: ["Margaret Hollowcreek", "Pacific Northwest", "..."]
  ```
  Loaded into `L2Instance.persona: DemoPersona | None` by
  `common/l2/loader.py::_load_persona`. Handbook templates read it
  via `vocab.institution.name` / `vocab.gl_accounts` / etc.
- **Empty / missing block → neutral prose.** Integrator L2s without
  the block load with `persona = None`; handbook prose falls
  through to L2-primitive-derived defaults (institution name from
  `description`, GL accounts from the account roster, etc.).

### Investigation walkthroughs split into mechanics + worked example

- The 4 Investigation walkthrough pages (recipient-fanout,
  volume-anomalies, money-trail, account-network) now have body
  prose written as L2-portable mechanics — how the slider works,
  what the σ-bucket histogram means, how to interpret each visual
  — without naming specific accounts.
- Below the body, a collapsed `??? example "Worked example: <fixture>"`
  admonition (mkdocs-material `pymdownx.details`) renders the
  curated Juniper / Cascadia / Shell A-B-C narrative — but only
  when `vocab.demo.has_investigation_plants` is true. Against
  `spec_example` the admonitions don't render and the body stands
  on its own.
- `handbook/investigation.md` "What you'll see in the demo" got
  the same guard with a "point at sasquatch_pr to see the worked
  example" fallback when the active L2 has no plants.

### `common/persona.py` rewrite

- **Dropped `SNB_PERSONA` module constant.** `persona.py` is now a
  generic typed skeleton — `DemoPersona` dataclass with empty-tuple
  defaults and a single `GLAccount` helper.
- **Sasquatch persona content moved into
  `tests/l2/sasquatch_pr.yaml`'s new `persona:` block.** Loader →
  `L2Instance.persona` → `vocabulary.py::_sasquatch_pr_vocabulary`.
  No code change is needed in `vocabulary.py` to add a per-L2
  curated narrative — the shape is purely data.
- `api/common-foundations.md` API ref now describes the generic
  shape without persona-specific examples.

### CI gates added

- **`tests/test_docs_persona_neutral.py`** — builds the rendered
  site against both bundled fixtures. spec_example must have zero
  persona tokens outside a small per-page allowlist for
  intentional Tier-2 citations (handbook hubs explaining the
  bundled demo). sasquatch_pr must render the curated flavor
  (anti-regression on over-deletion). Asymmetric "tighten only"
  bound — new leaks fail; reductions must lower the bound.
- **`tests/test_docs_links.py`** — sweeps every internal href / src
  in the built site, validating both file existence + fragment
  anchor presence. mkdocs `--strict` only catches missing files;
  this test catches missing `#section` anchors that strict mode
  silently allows.

### Other docs improvements

- **Shape C IA** landed under For Your Role / Concepts / Handbook /
  Walkthroughs / API. For Your Role moved to nav position 1.
- **`quicksight-gen export screenshots`** CLI: captures all 4
  deployed dashboards at 1280×900 with optional URL date overrides.
  29 PNGs captured in the v7.3 docs build; 15 wrapped in collapsed
  `??? example "Screenshot"` admonitions.
- **Dataflow diagrams** on each handbook overview now correctly
  render dataset → sheet edges (was emitting an empty graph).
- **Mkdocs material library SVG fallback hidden** when no L2 logo
  is set (prevents persona-specific mark from leaking when the
  active L2 declares no `theme.logo`).
- Executives dataset bug: `WHERE t.status = 'success'` (lowercase,
  doesn't exist in the data) → `'Posted'`. Surfaced during
  Q.2.c.exec.2.6 visual review.
- 28 dead links swept across handbook + walkthrough pages.

### Migration

No action required. Existing L2 YAMLs without a `persona:` block
load and render exactly as before. Add a `persona:` block only if
you want curated handbook prose for your institution; otherwise
the docs render generic prose derived from your L2 primitives.

If you've embedded the docs against your own L2 fixture, the
worked-example admonitions will only render when your fixture
plants Investigation scenarios (`inv_fanout_plants`). To get the
sample Juniper / Cascadia narrative, point docs at
`tests/l2/sasquatch_pr.yaml` (`QS_DOCS_L2_INSTANCE=...`).

## v7.2.0 — Realistic 3-month baseline seed + L2-coverage runtime assertions

Phase R replaces the previous "few-plants-per-L1-invariant" demo
seed with a 3-month healthy baseline of 60k+ transactions per L2
instance (sasquatch_pr) — the kind of working-system signal a
buyer needs to feel that this isn't a toy. Planted exception
scenarios (drift, overdraft, limit-breach, stuck-pending,
stuck-unbundled, supersession, Cascadia/Juniper fanout) now sit
ON TOP of that baseline as additive overlays rather than
constituting the whole seed. Cuts cleanly off v7.1.x; no schema
changes, no breaking CLI changes — `demo apply --all` does the
right thing automatically. Both Postgres and Oracle dialects
fully validated end-to-end.

### What's new — `emit_full_seed` pipeline

- **`emit_full_seed(instance, scenario, *, anchor=date.today())`** in
  `common/l2/seed.py` — the new public entry point CLI `demo apply`
  consumes. Composes:
  - **Baseline**: per-Rail leg loop over a 90-day rolling window.
    Volume + amount + time-of-day per rail kind from a 12-kind
    classifier (`_RailKind`); per-rail RNG sub-streams via
    `BASE_SEED ^ crc32(rail_name)` for isolation. Lognormal amount
    sampling with `LimitSchedule.cap` clamp. Aggregating-rail
    children-first + EOD/EOM bundling parent. Chain firings
    (Required ≈95%, Optional ≈50% completion). Daily-balance
    materialization by deferred-walk over the per-account leg log.
    Headline volumes on sasquatch_pr: **63k rows / 47 MB SQL**.
  - **Plant overlay** via `densify_scenario` (5× per-kind),
    `add_broken_rail_plants` (15 stuck-pending on one rail for
    visual hierarchy), `boost_inv_fanout_plants` (5× amount so
    the Cascadia/Juniper cluster reads against the customer-ACH
    baseline median).
  - All scenarios plant on customer indices `cust-0001`…`cust-0010`;
    baseline materializes `cust-0011`…`cust-0030+`. Pools disjoint —
    no plant-vs-baseline `daily_balances(account_id, day)` PK
    collisions.

### What's new — Oracle bulk-insert performance

- **`batch_oracle_inserts`** in `common/db.py` coalesces consecutive
  `INSERT INTO same_table VALUES (...)` into Oracle `INSERT ALL`
  blocks of up to 500 rows each. Cuts the 60k+ per-row round-trips
  to ~120, dropping Oracle apply from 20+ min (killed mid-flight) to
  ~12 min. Same-id flush handles Oracle's IDENTITY-in-INSERT-ALL
  quirk (one IDENTITY value per statement, not per row): the batcher
  tracks ids per batch and flushes before adding a duplicate so
  composite `(id, entry)` PKs stay unique.
- Postgres path unchanged (per-row INSERTs over psycopg2 land in
  ~16 s for the same 60k+ rows; no batching needed).

### What's new — runtime assertions

- **`tests/test_l2_runtime_assertions.py`** queries the live demo DB
  + asserts:
  - **Volume Anomalies smoke** (R.5.c): `<prefix>_inv_pair_rolling_anomalies`
    matview produces ≥5 windows clearing z>=3σ + planted recipient
    appears in the matview.
  - **L2 coverage** (R.5.d): every declared Rail has ≥M legs in
    `<prefix>_current_transactions` (cadence-aware threshold);
    every Chain has at least one parent + child firing pair; every
    TransferTemplate's instances net to expected_net ≥80% of the
    time; every LimitSchedule has matching legs.
- Skip cleanly when no demo DB URL is configured. Run after
  `demo apply` to verify the deployed dashboards have signal.

### What's new — full-pipeline hash-lock

- New `test_full_seed_hash_lock_*` tests pin the SHA256 of the FULL
  `emit_full_seed` pipeline (baseline + densify + broken + boost) at
  canonical anchor `date(2030, 1, 1)`. Catches drift in any of the
  composed pipeline stages without needing a deploy.

### Verified

- **Headline matview counts on the deployed sasquatch_pr (PG)**:
  drift=10 (planted only), overdraft=221, limit_breach=56,
  stuck_pending=20 (5 default + 15 broken-rail),
  stuck_unbundled=7, todays_exceptions=35, transactions=61,221,
  daily_balances=2,260.
- **Oracle parity**: same matview shape within RNG drift
  (drift=10, overdraft=221, limit_breach=56, stuck_pending=20,
  stuck_unbundled=7, todays_exceptions=35).
- **Probe**: 4 deployed dashboards across both dialects all clean
  (no datasource errors).
- **Full e2e (`./run_e2e.sh --skip-deploy`)**: 76 passed, 1 known
  pre-existing flake (`test_check_type_dropdown_exposes_options`,
  matches backlog item #466).

### Known follow-ups (backlog, not blocking)

- **Overdraft tuning**: ~220 of the overdraft rows are real signal
  on intermediate clearing accounts (`ach_orig_settlement`,
  `merchant_payable_clearing`, etc.) where the random emission
  order doesn't preserve causal cascade timing. Fix wants either
  causal leg-loop ordering or per-cascade zero-net materialization.
- **Limit_breach tuning**: ~56 rows from the per-firing amount
  sampler clamping individually but not tracking per-(account,
  transfer_type, day) cumulative outbound. Fix wants
  cumulative-aware sampling.
- **Volume Anomalies planted z-score**: the planted Cascadia/Juniper
  fanout sits at z≈-0.4 because the population is dominated by big
  merchant card sales. Path to >3σ wants either a separate
  per-account-type matview or a much larger planted spike.
- **R.7.e**: Lift the R.1.f generator-output spec out of PLAN.md
  into `docs/handbook/seed-generator.md` once we trust the headline
  numbers stable.

## v7.1.1 — Hotfix: `demo apply --all` now generates all four apps

Fresh installs of v7.1.0 deployed only Investigation + Executives
even with `demo apply --all` + `deploy --all`. Root cause: the
`DEMO_APP_CHOICE` Click choice list still listed only
`{investigation, executives}` (M.2.2 era when L1 + L2FT were
prototyped through a separate L2 pipeline), and the
`_apply_demo()` body had no codegen branches for L1 / L2FT. So
`demo apply --all` silently skipped two of four apps; `deploy
--all` then shipped what JSON existed in `out/`.

Fix: extend `DEMO_APP_CHOICE` to list all four apps, and add the
matching `_generate_l1_dashboard` / `_generate_l2_flow_tracing`
calls to `_apply_demo()`. Verified end-to-end: `demo apply --all`
now writes 4 analyses + 4 dashboards + 35 datasets +
datasource.json + theme.json. No other behavior change; existing
dev-loop deploys (`deploy --all --generate`) were not affected
because the standalone `generate --all` already handled all four
apps correctly — only `demo apply` was the gap.

## v7.1.0 — Dashboard polish: currency, universal date filters, Oracle wrapper, probe CLI

Q.1 dashboard polish phase shipped clean across both dialects (PG +
Oracle harness 15/15 each, probe CLI shows zero datasource errors
across all four apps). Cuts cleanly off v7.0.x; no schema changes,
no breaking dashboard JSON changes — additive polish + one Oracle-
specific bug fix that unblocked all Oracle visuals.

### What's new — money formatting

- **USD currency formatting on every money column.** Adds a typed
  `currency=True` flag to the tree's `Measure` (KPI / chart values)
  and `Dim.numerical` (table cells) primitives. Wires
  `CurrencyDisplayFormatConfiguration` (`$1,234.56`, comma
  thousands, 2 decimals) through both code paths. Emit-time assert
  rejects `currency=True` on categorical/date dims so wiring typos
  fail at the call site, not as silently dropped formatting.
- **Applied across L1 + L2 Flow Tracing + Investigation +
  Executives** to every dollar-shaped value: `stored_balance`,
  `computed_balance`, `drift`, `outbound_total`, `cap`,
  `amount_money`, `money`, `magnitude`, `parent_amount_money`,
  `actual_net`, `expected_net`, `net_diff`, plus all KPI sums
  (Net / Gross Money Moved, Liveness, etc.). Tables and KPIs now
  render consistently as `$1,234.56`.

### What's new — universal date-range filters

The M.2b.1 universal-date-filter pattern (shared analysis-level
DateTimeParams + per-dataset SINGLE_DATASET FilterGroups + paired
DateTimePicker controls per sheet) extended to every shipped
sheet that benefits from a date-range knob:

- **L1 Supersession Audit + Transactions** — joined the existing
  L1 universal date filter (rolling 7-day default).
- **Investigation Money Trail** — filter-bound DATE_RANGE picker
  on `posted_at` (matches Recipient Fanout / Volume Anomalies).
- **Executives Account Coverage / Transaction Volume / Money
  Moved** — new shared P_EXEC_DATE_START/END params with rolling
  30-day default (wider than L1's 7-day; Exec sheets are
  daily-grain summaries vs L1's per-leg detail).

### What's new — Oracle case-fold wrapper

Every Oracle dashboard was failing `ORA-00904: "col": invalid
identifier` on every visual — Oracle case-folds unquoted
identifiers to UPPERCASE while QuickSight quotes the lowercase
column names from its declared `Columns` list when building
visual queries. The class of bug was previously invisible (QS
shows only a generic "SQL exception" banner; the actual driver
message lives in the embed iframe's JS console).

- **`build_dataset` wraps every Oracle CustomSQL** with an outer
  SELECT that re-aliases each projected column from its UPPERCASE
  Oracle-stored form to a lowercase double-quoted alias matching
  what QS quotes. Postgres unchanged (folds unquoted identifiers
  to lowercase by default).
- **Alias `qs_inner` chosen** to start with a letter (Oracle
  rejects `_qs` with ORA-00911 — identifiers can't start with
  `_`).
- **All eight dashboards now probe clean** on both dialects.

### What's new — visibility for QS datasource errors

- **`quicksight-gen probe [APP|--all|--dashboard-id ID]` CLI** —
  walks every sheet of a deployed dashboard via Playwright + the
  embed URL, captures Stream-error console payloads per sheet,
  parses `errorCodeHierarchy` + `internalMessage`, prints a
  per-sheet error report. Replaces "open browser, hit F12, hunt
  through console" with one CLI invocation. Surfaces the kind
  of bug (column-rename, dialect-port regression, missing matview
  refresh) that QS otherwise hides behind its generic banner.
- **E2E harness ratchet** — `tests/e2e/_harness_browser.py`'s
  `run_dashboard_check_with_retry` now scans the per-attempt
  console-message sink for the same Stream errors after every
  successful operation. Catches the class where the operation's
  positive assertions all pass but every visual silently errored.

### What's new — per-app punch-list

- **L1 Supersession Audit** — added a "Supersessions with No
  Reason" KPI: count of higher-Entry rows whose `supersedes`
  reason is blank (target value = 0 — every supersession SHOULD
  declare its cause per the L1 SPEC). Backed by an analysis-level
  CalcField summed at the KPI; no FilterGroup gymnastics.
- **L1 Today's Exceptions** — bar chart axes now read in plain
  English (`Check Type` / `Open Exceptions`) instead of raw
  column names.
- **L1 Daily Statement** — date picker defaults to YESTERDAY
  rather than today (today's daily-balance row may not exist
  yet; yesterday is guaranteed to have closed-out balance rows).
- **L2 Flow Tracing Getting Started** — text-box reflow.
  YAML literal-block `description: |` preserves hard newlines
  that QuickSight's text-box renderer drops without word breaks,
  glomming adjacent words together. `" ".join(text.split())`
  collapses to a single paragraph.

### What's new — App Info diagnostic surface

- **App Info "Info" tab deploy stamp** now shows
  `git` / `generated` / `dialect` / `prefix` as a bulleted list
  (was a flat 3-line text block missing the prefix). Lets a
  viewer instantly see which institution + dialect the dashboard
  is rendered against.

### What's new — docs cleanup

- **Stale AR/PR refs dropped** from `handbook/customization.md`,
  `handbook/etl.md`, `handbook/investigation.md` (those apps
  were retired in M.4.3 / M.4.4 but the prose surface lagged).
- **Three "Schema v3" link-text mislabels** fixed (the link
  target was already `Schema_v6.md`; only the text was stale):
  `customization.md:223`, `etl.md:49,165`, `L1_Invariants.md:205`.
- **Operator role page** — L2 Flow Tracing promoted from
  "skim, don't study" to a proper second-tab where operator
  traces end ("but why is this happening every day?").
- **Integrator role page** — L2 model concepts (Account / Rail /
  Chain / TransferTemplate / LimitSchedule) elevated above the
  "How to start" list, plus a fresh-L2-authoring pointer at
  `spec_example.yaml` / `sasquatch_pr.yaml`.
- **Home page** reshaped as the "pick your role" front door
  (Q.2.b Shape C kickoff). Library shelves stay below as the
  fallback path.

### Breaking / behavior changes

None. Schema unchanged; CLI surface unchanged (additive only —
new `probe` subcommand). Existing dashboards regenerate +
redeploy without manual migration.

### Known issues / deferred

- **Q.1.a.3 — Auto-derive plain-English BarChart axis labels.**
  Manual labels applied to L1 Today's Exceptions; the auto-
  derive lift is queued for the next polish cut.
- **Q.1.c.6 — Executives Transaction Volume / Money Moved
  metadata grouping.** Needs L2-instance-aware Key+Value
  cascading dropdowns + a dataset pivot to expose metadata as
  a dim. Bigger than a punch-list item; queued.
- **Q.2.b/c/d/e — Doc IA shift to Shape C** (audience-first,
  For-Your-Role becomes the front door). Q.2.a (mechanical
  cleanup) + Q.2.b kickoff (Home reshape + audit + Shape C
  decision) + Q.2.d (operator + integrator onramp prose)
  shipped here. Q.2.b.exec.2-9 + Q.2.c (re-screenshot at
  1280×900) + Q.2.e ship in the next cut.

## v7.0.1 — Release pipeline fix (v7.0.0 retag)

The v7.0.0 release pipeline failed because the smoke-test job in
`.github/workflows/release.yml` referenced `tests/test_demo_data.py`
— a file removed in P.1.c-j when the v5 demo-data generators got
retired alongside the v5 schema. Fix: swap the smoke-test target to
`tests/test_persona.py` (the closest current-state analog —
DemoPersona + flavor-string coverage).

No code changes vs. v7.0.0 — just the workflow fix. v7.0.0 contents
ship under v7.0.1.

## v7.0.0 — Multi-database support: Postgres + Oracle 19c

### What's new

- **Oracle 19c is a first-class target alongside Postgres.** Every
  emit surface (schema DDL, L1-invariant matviews, Investigation
  matviews, dataset CustomSQL across all four shipped apps) is
  dialect-aware via `common/sql/dialect.py`. Pick a dialect by
  setting `dialect: postgres` (default) or `dialect: oracle` in
  the run config; everything downstream branches automatically.
- **`pip install quicksight-gen[demo]`** continues to install
  psycopg2 for Postgres demo apply; **`pip install
  quicksight-gen[demo-oracle]`** installs `oracledb` for Oracle.
- **Dataset CustomSQL parse/execute smoke verifier**
  (`tests/integration/verify_dataset_sql.py`) walks every emitted
  dataset's SQL, substitutes QS parameter placeholders with
  defaults, and runs the query against the live demo DB in a
  `WHERE 1=0` envelope. ~30 second feedback loop per dialect for
  catching dialect-specific SQL bugs that previously only surfaced
  at browser-test time.
- **Bounded VARCHAR for JSON metadata columns** (was unbounded
  TEXT/CLOB). Both `transactions.metadata` and
  `daily_balances.limits` are now `VARCHAR(4000)` on Postgres /
  `VARCHAR2(4000)` on Oracle. Required for Oracle (CLOB can't be
  aggregated, sorted, or compared with `IN ('literal')`); applied
  symmetrically to Postgres so "data too long" surfaces on either
  DB instead of leaking past PG.
- **Containerized CI for both dialects** runs the full unit suite
  + verify_dataset_sql against ephemeral Postgres + Oracle
  databases in GitHub Actions.
- **Harness L2FT plants-visibility check now Layer-1 matview
  query** (`assert_l2ft_matview_rows_present`) instead of the
  earlier dashboard text-scrape. The text-scrape failed
  deterministically on sasquatch_pr because QS table virtualization
  caps DOM rows at ~10 regardless of page size; with 57+ rail
  firings on the Rails sheet, plants past the visible window were
  invisible. The matview check is fast, deterministic, and points
  at the seed→matview-refresh layer if it fails.

### Breaking changes

- **`schema.sql` (the unprefixed v5 global schema) is removed.**
  Anyone still on v5 must migrate to per-prefix `emit_l2_schema()`
  emit. Migration path documented separately. Affects: legacy
  `transactions` / `daily_balances` global tables, the `ar_*`
  dimension tables (`ar_ledger_accounts`,
  `ar_subledger_accounts`, `ar_ledger_transfer_limits`), and
  ~15 dead `ar_*` views.
- **`demo schema` and `demo seed` CLI commands are removed.**
  Per-instance `demo apply` is the only emit surface.
- **L2 SPEC rule U6**: rails MUST be unique on
  `(transfer_type, role)`. Validator catches violations at L2
  load. The pre-shipped sasquatch_pr fixture was refactored to
  rename 9 rails to directional names (`ach_inbound`,
  `ach_outbound`, etc.) for compliance.
- **Investigation `build_all_datasets()` now requires
  `l2_instance` explicitly.** The silent fallback to
  `default_l2_instance()` (which prefix-poisoned App Info matview
  names with `spec_example_*`) is removed. Update callers to
  pass the L2 instance through.

### Internal cleanups

- Per-dialect SQL helpers in `common/sql/dialect.py`: type names
  (`serial_type`, `boolean_type`, `text_type`, `varchar_type`,
  `decimal_type`, `json_text_type`, `timestamp_type`), casts
  (`cast`, `typed_null`, `to_date`, `date_trunc_day`), date
  arithmetic (`epoch_seconds_between`, `interval_days`,
  `date_minus_days`), DDL idempotency
  (`drop_table_if_exists`, `drop_matview_if_exists`,
  `drop_index_if_exists`, `drop_view_if_exists`), matview
  options (`create_matview`, `matview_options`,
  `refresh_matview`), JSON checks (`json_check`), constant
  SELECTs (`dual_from`), recursive CTE preamble
  (`with_recursive`).
- `metadata_filter_clause(l2_instance, col)` emits one branch
  per declared metadata key with the JSON path as a literal.
  Replaces the `'$.' || pKey` runtime concat that Oracle's
  `JSON_VALUE` rejects (paths are parse-time literals).
- `common/db.py` consolidates `connect_demo_db(cfg)` +
  `execute_script(cur, sql, dialect)` so harness fixtures and
  `demo apply` share one Oracle/Postgres connection path.
- TZ-naive `TIMESTAMP` standardized across both dialects (was
  PG `TIMESTAMPTZ` / Oracle `TIMESTAMP WITH TIME ZONE`).
  Timezone normalization is the integrator's contract.
- `_oracle_type_alias` rewrites `varchar(N)` → `VARCHAR2(N)`
  so callers can pass Postgres-shape parameterized type names.
- Docs site mkdocs build is `--strict` clean.

## v6.2.4 — Reskin walkthrough documents the new logo + favicon fields

The v6.2.3 commit shipped the `theme.logo` / `theme.favicon`
mechanism but missed updating the
[How do I reskin the dashboards for my brand?](walkthroughs/customization/how-do-i-reskin-the-dashboards.md)
walkthrough. This patch adds the **Brand assets on the docs site**
section there with the URL / absolute-path acceptance rules and a
cross-link to the publishing-workflow walkthrough.

No code changes.

## v6.2.3 — Optional logo + favicon on the L2 theme block

### What's new

- **L2 `theme:` block now accepts optional `logo` + `favicon`
  fields.** Both override the mkdocs-material `theme.logo` /
  `theme.favicon` at docs-build time, so each integrator's
  rendered site shows their own branding instead of the SNB mark
  the canonical `mkdocs.yml` ships with.
- Both fields accept either a **URL** (`http://`, `https://`,
  protocol-relative `//`) or an **absolute file path** (starts
  with `/`). URLs pass through verbatim; absolute paths get
  copied into `<docs_dir>/img/_l2_<kind><ext>` at build time
  and the theme key is rewritten to the docs-relative path.
  Relative paths are rejected at L2 load (their resolution would
  depend on the integrator's CWD).
- Without an L2 override (the default for `spec_example` and the
  current `sasquatch_pr` fixture), the docs site falls back to
  whatever `mkdocs.yml` declares — preserving today's behavior.

### Internal cleanups

- `ThemePreset` carries `logo: str | None = None` and
  `favicon: str | None = None`; the L2 YAML loader's
  `_load_optional_brand_asset` validates the value shape with
  clear error messages on bad input.
- `main.py`'s `define_env(env)` reads the resolved L2's
  `theme.logo` / `theme.favicon` and mutates `env.conf['theme']`
  via a small `_apply_brand_asset_override` helper.
- `.gitignore` excludes `src/quicksight_gen/docs/img/_l2_*` so
  copied per-L2 assets don't sneak into commits.
- 8 new loader unit tests cover URL pass-through, absolute-path
  acceptance, relative-path rejection, non-string rejection,
  empty-string-as-None, and explicit-null handling.

## v6.2.2 — For Your Role: 5 deep role-orientation pages + release-notes extractor fix

### What's new

- **For Your Role section is no longer a placeholder.** Five
  full-depth role-orientation pages ship under
  `for-your-role/`, each following the audit's narrative shape:
  *what you do today → what this tool does differently → what
  we are not asking you to learn → how to start → concepts you'll
  want grounded → what good looks like.*
  - [For the operator](for-your-role/operator.md) — L1
    Reconciliation Dashboard daily routine.
  - [For the integrator](for-your-role/integrator.md) — L2 Flow
    Tracing for declaration / runtime reconciliation.
  - [For the ETL engineer](for-your-role/etl-engineer.md) — the
    two-table feed contract + matview refresh sequence.
  - [For the executive](for-your-role/executive.md) — the
    Account Coverage / Transaction Volume / Money Moved
    scorecard.
  - [For the compliance analyst](for-your-role/compliance-analyst.md) —
    the four question-shaped Investigation sheets.
  Every page substitutes the institution name + acronym via the
  Phase O.1.b `HandbookVocabulary`; spec_example renders with
  "Your Institution" placeholders, sasquatch_pr renders with SNB
  flavor.

### CI / release pipeline

- **Release-notes extractor fixed.** The `awk` in
  `.github/workflows/release.yml` previously matched only bare
  `## v6.2.0` headers; v6.x release bodies all shipped as
  placeholder "Release vX.Y.Z" because every actual header
  carries a descriptive ` — title` suffix. Switched to a
  field-2 match (`$2 == tag`) so descriptive headers extract
  cleanly going forward.

## v6.2.1 — CI: install Graphviz on workflow runners

The v6.2.0 release pipeline failed at the tests gate because the
Phase O.1.c diagram render module shells out to the system `dot`
binary, which wasn't pre-installed on the Ubuntu CI / Pages /
Release runners. The functional release is unchanged from v6.2.0;
this patch adds `apt-get install -y graphviz` as the first step
after checkout in `.github/workflows/{ci,pages,release}.yml`.

No code, schema, or API surface changes.

## v6.2.0 — Unified docs render pipeline; training/ removed

### What's new

- **Unified docs site under `docs/`.** The split between `docs/handbook/`
  (operator handbooks) and `training/handbook/` (whitelabel kit) is gone.
  All prose now lives under one mkdocs source tree organized by the
  5-section IA from the Phase O.0 audit: Concepts / Reference /
  Walkthroughs / For Your Role / Scenarios.
- **mkdocs-macros + Jinja templating.** `main.py` at the repo root
  registers a `{{ vocab }}` Jinja variable + a
  `{{ diagram(family, **kwargs) }}` macro. Pages can substitute
  `{{ vocab.institution.name }}` etc. and embed L2-driven /
  hand-authored diagrams without duplicating render calls.
- **`HandbookVocabulary`** (`common/handbook/vocabulary.py`) ships
  4 sub-shapes (`InstitutionVocabulary`, `StakeholderVocabulary`,
  `MerchantVocabulary`, `InvestigationPersonaVocabulary`) and
  `vocabulary_for(l2_instance)`. Built-in `sasquatch_pr` reuses
  `SNB_PERSONA` + adds Investigation personas. Anything else routes
  to a neutral fallback derived from the L2's own description (zero
  persona leakage by construction).
- **Diagram render pipeline** (`common/handbook/diagrams.py`) emits
  inline SVG via Graphviz for three families: L2 topology
  (accounts / chains / layered cuts walking the L2 primitives
  directly), per-app dataflow (walks the App tree and fans datasets
  to sheets), and hand-authored conceptual `.dot` files (six ship:
  double-entry, escrow-with-reversal, sweep-net-settle, vouchering,
  eventual-consistency, open-vs-closed-loop).
- **5-section IA navigation.** mkdocs.yml restructured to Concepts /
  Reference / Walkthroughs / For Your Role / Scenarios + API Reference
  with placeholder index pages standing up each section.
- **Concepts section populated.** All 6 concept pages migrated from
  `training/handbook/concepts/` into `docs/concepts/` with
  hand-authored Graphviz diagrams embedded. Each page replaces the
  original "In the SNB demo" section with persona-neutral
  "How L1 surfaces this" pointers.
- **Reference handbook intros vocab-substituted.** All 5 handbook
  pages (l1, l2_flow_tracing, investigation, etl, customization) drop
  the snb-hero hardcoded SNB wordmark for vocab-substituted intros.
  Per-app reference pages embed dataflow + topology diagrams.
- **Executives reference page** (`handbook/executives.md`) ships,
  closing the L.8 deferred Executives docs gap.
- **`export docs --l2-instance <yaml>`** validates an L2 path and
  echoes the `QS_DOCS_L2_INSTANCE=<path> mkdocs build` command the
  integrator should run to render docs against that institution.

### Breaking changes

- **`training/` directory deleted.** The 18-file Sasquatch training
  kit + `mapping.yaml.example` are gone; their content either
  migrated to `docs/` (concepts, scenarios) or was dropped (per-role
  guides + per-scenario walkthroughs were heavily SNB-coupled).
  Future role-orientation + scenario pages can be authored in vocab-
  templated form.
- **`quicksight-gen export training` CLI command removed.** Use
  `export docs` instead — the unified site replaces both surfaces.
- **`derive_mapping_yaml_text()` + `_HEADER` + `_yaml_kv` removed**
  from `common/persona.py`. The string-replace substitution
  machinery (`_apply_whitelabel`, `_parse_mapping`,
  `_WhitelabelResult`, `_WHITELABEL_*`) removed from `cli.py`.
  Templating happens at mkdocs render time via Jinja, not via
  post-render string replacement.
- **`SNB_PERSONA.account_labels` + `intentional_non_mappings`
  fields dropped.** Both were mapping.yaml-only; `account_labels`
  was already derivable from `gl_accounts.name`.

### Migration notes

- Integrators consuming `quicksight-gen export training`: switch to
  `export docs --l2-instance <your-l2.yaml>`. The unified site
  carries the same operator + integrator + customizer prose.
- Integrators consuming `derive_mapping_yaml_text()` / the
  whitelabel substitution pipeline: switch to either submitting a
  built-in `HandbookVocabulary` for your institution (PR), or
  authoring your own L2 instance YAML (the neutral fallback works
  out of the box for any L2).
- The `[docs]` extras now require `mkdocs-macros-plugin>=1.3` and
  `graphviz>=0.20`; the system `dot` binary must also be installed
  for diagram rendering.

### Internal cleanups

- 5 missing hand-authored conceptual `.dot` files added under
  `src/quicksight_gen/docs/_diagrams/conceptual/`.
- 25 unit tests cover `HandbookVocabulary` + the spec_example
  zero-leakage hard contract.
- 19 unit tests cover the three diagram render families against
  spec_example + sasquatch_pr.
- 1227 unit tests + `mkdocs build --strict` green at cut.

---

## v6.1.0 — Theme as L2 attribute; Investigation + Executives go L2-fed; Inv plant + harness parity

### What's new

- **Theme is an L2 institution attribute.** Each L2 YAML carries an inline `theme:` block validated by `ThemePreset`. Apps resolve via `resolve_l2_theme(l2_instance)`. When no inline theme is declared, AWS QuickSight CLASSIC takes over — `build_theme` returns None and the deploy skips emitting a custom Theme resource (silent-fallback contract).
- **All four shipped apps are L2-fed.** L1 Dashboard, L2 Flow Tracing, Investigation, and Executives all consume the same institution YAML's `instance` prefix + `theme:` block. One YAML drives the whole 4-app deployment unit.
- **Investigation + Executives ported.** Investigation reads from `<prefix>_inv_*` matviews (lifted from the legacy global namespace into per-prefix DDL via `common/l2/schema.py::_emit_inv_views`); Executives reads from `<prefix>_transactions` / `<prefix>_daily_balances` directly.
- **Investigation plant primitive.** New `InvFanoutPlant` in `common/l2/seed.py` populates the `<prefix>_inv_pair_rolling_anomalies` + `<prefix>_inv_money_trail_edges` matviews with N-sender → 1-recipient fanout edges. Wired into `auto_scenario.default_scenario_for` so every L2 instance with at least 2 sender candidates gets a planted Investigation scenario.
- **Harness parity for Investigation + Executives.** New `_harness_inv_assertions.assert_inv_planted_rows_visible` mirrors the L1 plant-row visibility check; `_harness_exec_assertions.assert_exec_base_tables_queryable` covers Executives' base-table contract.
- **`Config.with_l2_instance_prefix(prefix)` helper.** Centralizes the L2-prefix stamp + `datasource_arn` re-derive across all 8 sites (4 `_generate_*`, 2 dataset builders, 2 app builders, demo apply). Closes a class of bugs where per-app builders baked the unprefixed `qs-gen-demo-datasource` ARN into dataset JSON.
- **`demo apply` now plants L2-shape data.** Calls `emit_l2_seed(inv_l2, default_scenario_for(inv_l2).scenario)` and `refresh_matviews_sql(inv_l2)` so the prefixed L1 + Inv matviews actually populate. Pre-N.4 demo apply only seeded the legacy v5-shape unprefixed tables (which dashboards no longer read from). Closes pending issue #433 ("L1 dashboard date filter doesn't surface matview rows").
- **Investigation `recipient_fanout` dataset SQL** migrated to v6 column names (`amount_money` / `posting` / `account_role` / `status='Posted'` / leaf-internal predicate).
- **Inv + Exec analysis names** normalized to `Name (instance)` shape so multi-instance deployments are visually distinguishable in the QS dashboard list.

### Breaking changes

- **`cfg.theme_preset` field dropped** (Config dataclass + YAML loader + `QS_GEN_THEME_PRESET` env var). Theme is fully L2-driven.
- **CLI `--theme-preset` flag dropped** from `generate` + `deploy --generate`. Override theme by editing the L2 YAML's `theme:` block, or use `--l2-instance` to point at a different institution YAML.
- **`PRESETS` dict + `get_preset()` function dropped** from `common/theme.py`. The single `DEFAULT_PRESET` constant is the only fallback.
- **`populate_app_info_sheet(theme=None)` fallback dropped.** `theme: ThemePreset` is now required — every caller already passes one.
- **`build_theme(cfg)` → `build_theme(cfg, theme: ThemePreset | None) -> Theme | None`.** Returns None for the silent-fallback path (CLI + harness deploy skip writing `theme.json` accordingly).
- **`resolve_l2_theme` returns `ThemePreset | None`** instead of always returning a preset. Apps coerce via `resolve_l2_theme(l2_instance) or DEFAULT_PRESET` for in-canvas accent colors; CLI uses the raw Optional for the deploy decision.

### Migration notes (operators on v6.0.x)

- If your `config.yaml` carried `theme_preset: <name>`, remove that line. Move the brand colors into your L2 institution YAML's `theme:` block (see `tests/l2/sasquatch_pr.yaml` for an example, or omit entirely to fall back to AWS QuickSight CLASSIC).
- If you were invoking `quicksight-gen generate ... --theme-preset <name>`, drop the flag. The L2 YAML now owns this decision.
- If you have CI scripts that set `QS_GEN_THEME_PRESET=<name>`, drop the env var.

### Aurora deploy verify

End-to-end: 75 passed, 1 known-flake (`test_harness_l1_planted_scenarios_visible[sasquatch_pr]` Layer 2 — see backlog "Sasquatch L1 dashboard render flake"). Eight commits across the iteration loop captured the structural fixes; see commits `aff3229` → `38b2b6c`.

### Internal cleanups

- Inv matview `pair_legs` CTE now aliases v6 `account_role` back to v5 `_account_type` so downstream consumers don't need the rename.
- Inv matview filter `status='success'` → `status='Posted'` (v5-era leftover from the N.3.b column corrections).
- `_create_theme` skips when `theme.json` is missing (parity with `_delete_theme`'s existing guard).
- `_apply_demo` applies the per-instance L2 schema (`emit_l2_schema(inv_l2)`) alongside the legacy `schema.sql`.

## v6.0.4

### Hotfix — Re-cut to escape v6.0.3 duplicate-run race

> **v6.0.3 was tagged once but GitHub Actions kicked off two concurrent pipeline runs against the same tag.** The first run made it through `verify-testpypi-install` cleanly (the smoke-import fix from v6.0.3 worked), but the duplicate run failed at `Publish to TestPyPI` with `400 File already exists`, and the first run's `Publish to PyPI` deployment was rejected during the manual-approval step (operator saw the chaos and stopped it). v6.0.4 carries no code or workflow changes vs v6.0.3 — it's a clean re-cut so the pipeline runs once.

**Operator impact**: zero behavior change. The wheel is identical to v6.0.3 (which is identical to v6.0.0–v6.0.2). v6.0.3 sits on TestPyPI as a build artifact but never promoted.

## v6.0.3

### Hotfix — Drop deleted apps from release-pipeline smoke imports

> **v6.0.2 was tagged and pushed.** The smoke-test-wheel fix from v6.0.2 worked, but the next failure surfaced one job further down the pipeline: `verify-testpypi-install` failed at the "Smoke import the public surface" step with `ModuleNotFoundError: No module named 'quicksight_gen.apps.payment_recon'`. v6.0.2 was published to TestPyPI as a build artifact but never promoted to PyPI; v6.0.3 ships the same v6.0.0 + v6.0.1 + v6.0.2 work plus this fix. The v6.0.0–v6.0.2 git tags stay in place.

**Root cause**:
- `.github/workflows/release.yml`'s `verify-testpypi-install` and `verify-pypi-install` jobs both run a hardcoded smoke-import block listing the 4 apps to import.
- M.4.3 / M.4.4 deleted `quicksight_gen.apps.payment_recon` and `quicksight_gen.apps.account_recon` — the workflow's import list wasn't updated.
- `verify-testpypi-install` failed at the first hidden-import call.

**Fix**:
- Swapped `payment_recon` / `account_recon` imports for `l1_dashboard` / `l2_flow_tracing` — the 4 apps that actually ship in v6.

**Operator impact**: zero behavior change vs v6.0.0 / v6.0.1 / v6.0.2 — the wheel itself is identical. The fix is purely a release-pipeline assertion concern.

## v6.0.2

### Hotfix — Drop deleted test files from the smoke-test-wheel job

> **v6.0.1 was tagged and pushed.** The `bake-sample` fix from v6.0.1 worked (the bake job that broke v6.0.0 went green), but the next job in the pipeline — `smoke-test-wheel` — failed with `ERROR: file or directory not found: tests/test_account_recon.py`. v6.0.1 was published to TestPyPI as a build artifact but never promoted to PyPI; v6.0.2 ships the same v6.0.0 + v6.0.1 work plus this fix. The v6.0.0 and v6.0.1 git tags stay in place; v6.0.2 ships the corrected workflow.

**Root cause**:
- `.github/workflows/release.yml`'s `smoke` job ran a hardcoded list of pytest files against the freshly-installed wheel.
- M.4.3 / M.4.4 deleted `tests/test_account_recon.py`, `tests/test_demo_sql.py`, `tests/test_recon.py`, `tests/test_generate.py` — the workflow's hardcoded list wasn't updated.
- The job blew up at the pytest collection step before any test ran.

**Fix**:
- Removed the deleted test files from the smoke-job pytest invocation. Surviving wheel-shaped tests (`test_models.py`, `test_demo_data.py`, `test_theme_presets.py`, `test_dataset_contract.py`) stay in the list.
- Added `QS_GEN_SKIP_PYRIGHT=1` to the smoke-job env block. The smoke venv only installs the runtime wheel + a small pytest set (no `[dev]` extras), so pyright isn't on PATH; `pytest_sessionstart`'s pyright gate handles missing pyright gracefully (returns early), but setting the env var makes the intent explicit.

**Operator impact**: zero behavior change vs v6.0.0 / v6.0.1 — the wheel itself is identical. The fix is purely a release-pipeline concern.

## v6.0.1

### Hotfix — Bundle the L1 default L2 instance YAML in the wheel

> **v6.0.0 was cut and reached the post-PyPI install verification job before being caught.** The release pipeline's `bake-sample` job (runs `quicksight-gen generate --all` against the freshly-installed wheel) failed with `FileNotFoundError: '/tmp/qs-bake/lib/python3.13/tests/l2/spec_example.yaml'`. v6.0.0 was published to TestPyPI as a build artifact but never promoted to PyPI; v6.0.1 ships the same v6.0.0 work plus this hotfix. The v6.0.0 git tag stays in place; v6.0.1 ships the corrected wheel.

**Root cause** (uncovered during the v6.0.0 release pipeline run):
- M.4.3 migrated `default_l2_instance()` from `apps/account_recon/_l2.py` → `apps/l1_dashboard/_l2.py`.
- The migration kept the old path resolution: `Path(__file__).resolve().parents[4] / "tests" / "l2" / "spec_example.yaml"`.
- That path works in dev (where `parents[4]` from the source tree resolves to the repo root) but breaks when installed (the wheel doesn't ship `tests/l2/`).
- Any `quicksight-gen generate` invocation that fell back to the L1 dashboard's default L2 instance crashed.

**Fix**:
- Copied `tests/l2/spec_example.yaml` → `src/quicksight_gen/apps/l1_dashboard/_default_l2.yaml`.
- Switched `default_l2_instance()` to load via `importlib.resources.files(...) / "_default_l2.yaml"` so the YAML is bundled in the wheel.
- Added the new path to `pyproject.toml`'s `[tool.setuptools.package-data]` block.
- Added a unit-test regression (`test_default_l2_yaml_is_byte_identical_to_test_fixture`) that hashes both files; if the bundled copy ever drifts from the test fixture, this fails loudly with the resync command.

**Operator impact**: zero behavior change vs v6.0.0 — same default L2, same dashboard render. The fix is purely a wheel-packaging concern.

## v6.0.0

### Phase M — L2 foundation + 4-app consolidation (major)

**Headline**: every dashboard configurable from a single L2 institutional model. Drop a YAML in, run `quicksight-gen demo seed-l2 myorg.yaml`, and the L1 dashboard renders against your declared accounts / rails / chains / transfer templates without per-institution code changes.

The four-app v6 lineup:

- **L1 Dashboard** — persona-blind L1 invariant violation surface (drift / overdraft / limit breach / stuck pending / stuck unbundled / supersession audit / today's exceptions / daily statement / transactions). Configured by an L2 instance — feed any institution's L2 YAML once, dashboard renders against it.
- **L2 Flow Tracing** — Rails / Chains / Transfer Templates / L2 Hygiene Exceptions for the integrator validating their L2 instance against the SPEC.
- **Investigation** — recipient fanout, volume anomalies, money-trail provenance, account-network graphs (compliance / AML triage). Carry-forward from v5; reshape decision deferred to Phase N.
- **Executives** — board-cadence statistics over the shared base tables (Account Coverage, Transaction Volume, Money Moved). Carry-forward from v5.

The major bump is earned by:

- **L1 schema breaking change**. The shared base layer added new fields the L1 invariant matviews depend on (Entry, ExpectedEODBalance, ExpectedNet, Origin per-leg, Limits, Transfer.Parent). Existing v5 integrators must update their ETL to populate the new columns before deploying v6.
- **Account Reconciliation + Payment Reconciliation apps deleted** (M.4.3 + M.4.4). Both replaced by the L1 Dashboard configured against a per-institution L2 instance. External callers importing `quicksight_gen.apps.account_recon` or `quicksight_gen.apps.payment_recon` must migrate to `quicksight_gen.apps.l1_dashboard` + a hand-written L2 YAML for their institution. CLI: `generate account-recon` / `generate payment-recon` / `demo seed account-recon` / `demo seed payment-recon` removed.
- **`sasquatch-bank-ar` theme preset deleted**. Existing config files using `theme_preset: sasquatch-bank-ar` must switch to `sasquatch-bank` or `sasquatch-bank-investigation` before redeploy.
- **Internal API change**: many tree-building helpers in `common/tree/` gained typed-required parameters that previously had defaults (`DateTimeParam.default` is now required; `KPIOptions` requires the full block including empty `TargetValues=[]` / `TrendGroups=[]`; `build_liveness_dataset` / `build_matview_status_dataset` now require an `app_segment` keyword arg). These prevent classes of bugs from recurring at the wiring site rather than catching them in tests.

### What landed

**M.0–M.4: L2 foundation + L1 + L2FT apps + harness.** The L2 model (Accounts + AccountTemplates + Rails + TransferTemplates + Chains + LimitSchedules) loads from YAML, validates 30+ cross-entity rules at construction, and emits per-instance prefixed schema (`<prefix>_drift`, `<prefix>_stuck_pending`, etc.). The L1 dashboard reads from these prefixed matviews; feeding a different L2 instance gives the dashboard a different prefix automatically. The L2 Flow Tracing dashboard surfaces the L2-side hygiene exceptions (Chain Orphans / Unmatched Transfer Type / Dead Rails / Dead Bundles Activity / Dead Metadata Declarations / Dead Limit Schedules) for the integrator validating their YAML against runtime data.

A new end-to-end harness (`tests/e2e/test_harness_end_to_end.py`) parameterizes over the L2 contract matrix (3 instances: spec_example, sasquatch_pr, fuzz) and runs the full chain — schema apply → seed → matview refresh → deploy → Playwright asserts planted scenarios surface as visible rows. Per-test failure dumps a triage manifest (planted_manifest + matview row counts + dashboard IDs + embed URLs + JS console capture).

**M.4.4 hardening stack.** A long debug session post-deletes uncovered and fixed a stack of QS-UI compatibility bugs that had been silently working in v5:

- **App Info canary sheet** on every dashboard. Real-query liveness KPI + per-matview row-count table + deploy stamp. Collapses the QS spinner-forever footgun ladder to a single glance.
- **AnalysisDefinition shape match for QS UI editor**: top-level `Options` + `AnalysisDefaults` + per-sheet `CanvasSizeOptions`; auto-VisualIds use UUIDs not positional slugs; KPIs emit the full `KPIOptions` block (was crashing the QS editor on open).
- **`DateTimeParam.default` is now required.** Without one, QS's date picker initialized with no value and crashed the editor with `Error: epochMilliseconds must be a number, you gave: null`. Type-encoded the invariant so the bug class can't recur.
- **JS console capture in e2e failure manifests.** Every page-load attempt registers `page.on("console", ...)` + `page.on("pageerror", ...)` listeners; on failure the captured messages dump next to the screenshot. Drove the epochMilliseconds bug discovery (the error never surfaced in the QS UI but printed to the JS console).
- **Cap-aware `days_ago` for stuck_pending plant** (M.4.4.13). Hardcoded `days_ago=2` silently failed for any picked rail with `max_pending_age >= 2 days`. Fixed with the same cap-aware pattern stuck_unbundled already used.
- **L1 render xfails root-caused** (M.4.4.12). KPI title mismatch + `todays_exceptions` matview missing `stuck_pending`/`stuck_unbundled` UNION branches + manifest-derived expected math (reframed to query the matview directly). Plus dynamic date-filter widening from manifest `max(days_ago) + 7` so cap-aware plants don't fall outside the dashboard's filter window.
- **Per-app prefix in App Info DataSetIds** (M.4.4.7). Each app's App Info datasets now carry a per-app segment so `deploy <single-app>` can't delete-then-create another app's App Info dataset.
- **Per-role business_day offsets for the fuzz matrix** (M.4.4.14). Fuzz instances now emit varied per-role hour offsets so any future L1 view that depends on per-role business-day boundaries differing has fuzz coverage. Production fixtures stay midnight-aligned (no hash drift).

### Migration

For existing v5 integrators on Account Reconciliation or Payment Reconciliation:

1. Author an L2 instance YAML for your institution under `tests/l2/` (use `tests/l2/spec_example.yaml` as the skeleton).
2. Migrate your ETL to populate the new L1 schema fields (Entry, ExpectedEODBalance, ExpectedNet, Origin per-leg, Limits, Transfer.Parent).
3. Switch from `quicksight-gen generate account-recon` / `generate payment-recon` to `quicksight-gen generate l1-dashboard --l2-instance <yourorg>.yaml`.
4. Replace any `theme_preset: sasquatch-bank-ar` config with `theme_preset: sasquatch-bank`.

Investigation + Executives users: no migration needed; both apps continue working as in v5. Their Phase N reshape decision is forthcoming.

### Deferred to Phase N

The following work originally scoped under M.4.5, M.5, M.6, M.7, M.8, M.4.4.9, M.4.4.16 was punted to a new Phase N to ship v6 with the L2 foundation as the headline:

- **N.1**: Investigation + Executives reshape decision (keep / reshape onto L2 / delete)
- **N.2**: Demo persona infrastructure + unified theme — Sasquatch becomes N L2 instances; persona substitution wired into a `generate config demo` command
- **N.3**: CLI workflow polish — `generate config / apply schema / apply data / apply dashboards / generate training`
- **N.4**: Docs render pipeline — handbook prose templated against L2 persona vocabulary; replaces today's `mapping.yaml` substitution
- **N.5**: Training render pipeline — training site rendered from L2 + ScreenshotHarness regenerated per L2 instance
- **N.6**: QS-UI kitchen-sink reference tool — defensive measure, deferred since the concrete editor-crash bugs got fixed
- **N.7**: L2FT plants-visible date-filter widening — same shape as the M.4.4.12 L1 widening fix, currently wrapped in an inline xfail

The bulk of `schema.sql`'s AR `ar_*` view surface (~1130 lines) is dead code in v6 (no v6 app reads it) but stays in the schema because Investigation's demo seed registers its sub-ledgers in the AR dimension tables for FK integrity. Phase N's N.1 (Inv reshape) is the natural moment to either migrate Inv off the dim tables or accept the carry-over and sweep the dead views.

## v5.0.2

### Phase L — Tree primitives + Executives app + mkdocstrings API reference (major)

> **v5.0.0 and v5.0.1 were both cut but never made it through the release pipeline.** v5.0.0 reached the manual approval gate (TestPyPI publish ran) before being cancelled when the underlying CI run on the same commit was discovered red — two e2e drilldown tests crashed at parametrize-collection time on a stock CI runner. The fix landed plus a new `tests` job at the start of `release.yml` was added so any failing test stops the release before any publish step. v5.0.1 then hit the new gate: two `test_screenshot_harness.py` tests transitively imported `playwright` (via `tests.e2e.browser_helpers`) but CI's `[dev]` extras don't ship playwright, so the gate caught it cleanly — no publish step ran. v5.0.2 ships the same Phase L work plus the three release-pipeline reliability fixes (test-collection robustness, tests-gate-on-release, playwright-importorskip on screenshot-harness URL tests) — none of which change user-facing behavior. The v5.0.0 + v5.0.1 git tags were deleted; TestPyPI shows a v5.0.0 artifact (pre-fix wheel) which will not be promoted.

Replaces the constants-heavy, manually-cross-referenced dashboard construction in the per-app `analysis.py` / `filters.py` / `visuals.py` modules with a tree of typed builder objects in `common/tree/`. Visuals reference `Dataset` nodes (not string identifiers); filter groups reference `Visual` nodes; cross-sheet drills reference `Sheet` nodes. Internal IDs (visual_id, filter_group_id, action_id, layout element IDs) auto-derive from tree position; URL-facing identifiers (`SheetId`, `ParameterName`) and analyst-facing identifiers (`Dataset` identifier, `CalcField` name) stay explicit. `App.emit_analysis()` / `emit_dashboard()` runs validation walks (dataset / calc-field / parameter / drill-destination references) — a missing reference fails at construction with a stack trace pointing at the wiring site, not at deploy with an opaque "InvalidParameterValue".

All three existing apps (Payment Reconciliation, Account Reconciliation, Investigation) ported to the tree, and a fourth app — **Executives**, board-cadence statistics over the shared base tables — built greenfield directly on the new primitives. Combined source reduction across the three ports: -47%.

The major bump is earned by:

- **Internal API change.** External callers importing `quicksight_gen.apps.{payment_recon,account_recon,investigation}.{analysis,filters,visuals}` for programmatic dashboard construction must update — those modules are gone, collapsed into a single `apps/<app>/app.py` per app. The new public construction surface is `quicksight_gen.common.tree` (App / Analysis / Dashboard / Sheet plus typed Visual / Filter / Control / Drill wrappers). Per the project's no-backwards-compat-shims rule, no compatibility re-exports.
- **New Executives app** added as the fourth dashboard.
- **Layer-separation cleanup.** The codebase is now structured around an explicit three-layer model: L1 (`common/tree/` — persona-blind primitives), L2 (`apps/<app>/app.py` — per-app tree assembly in domain vocabulary), L3 (SQL strings + `demo_data.py` + `common/persona.py` + theme presets — persona / customer flavor). The L1 invariant: zero `sasquatch` hits in `common/tree/`.

### What landed

**Typed tree primitives in `common/tree/` (L.1)**

- `App` / `Analysis` / `Dashboard` / `Sheet` are the top-level structural nodes; cross-references between them are object refs, not string IDs.
- Typed `Visual` subtypes: `KPI`, `Table`, `BarChart`, `Sankey`, plus `TextBox` for rich-text content. Each subtype validates its dataset / column references at emit time.
- Typed Filter wrappers: `CategoryFilter`, `NumericRangeFilter`, `TimeRangeFilter`, plus `FilterGroup` for sheet-wide / visual-pinned / all-sheets scoping.
- Typed Parameter declarations (`StringParameter` / `IntegerParameter` / `DecimalParameter` / `DatetimeParameter`) and matching Filter / Parameter `Control` wrappers (Dropdown, Slider, DateTimePicker, CrossSheet).
- Typed `Drill` actions with `Sheet` object-ref targets — the tree validates the destination at emit time so a typo'd drill target fails at construction, not at deploy.
- Typed `Dataset` + `Column` nodes; `ds["col"].dim()` / `.sum()` / `.date()` chained factories produce typed `Dim` / `Measure` slots that visuals consume directly. Column refs validate against the registered `DatasetContract` so column-name typos raise a loud `KeyError` at the wiring site.
- Typed analysis-level `CalcField`; auto-naming from tree position.
- Auto-ID resolver (L.1.16) for internal IDs; pyright strict on `common/tree/`; kitchen-sink app exercising every primitive shape (L.1.10.6).

**Apps ported to the tree (L.2 / L.3 / L.4)**

- L.2 — Investigation: 5 sheets, 5 datasets, walk-the-flow drills on the directional Sankeys, parameter-bound chain-root + anchor selection.
- L.3 — Account Reconciliation: 5 sheets, 13 datasets including the 3 cross-check rollups + 2 daily-statement datasets, per-tab filters, drill-down chain (Balances → Transactions, Transfers → Transactions, Today's Exceptions → per-check details).
- L.4 — Payment Reconciliation: 6 sheets, 11 datasets, mutual-filter Payment Reconciliation tab, cross-pipeline drills (Payments → Settlements → Sales).
- Combined source reduction across the three ports: -47%.

**Executives — fourth app, greenfield (L.6)**

- 4 sheets: Getting Started + Account Coverage + Transaction Volume Over Time + Money Moved.
- 2 custom-SQL datasets reading the shared base tables only — no Executives-specific schema. Per-transfer pre-aggregation (`WITH per_transfer AS`) collapses multi-leg transfers so a 2-leg $100 movement counts as one $100 transfer (not two $200) in the volume + money rollups.
- Account Coverage's Active KPI + Active bar carry a visual-pinned `activity_count >= 1` filter so they read as "accounts that moved money in the period" while the Open KPI/bar count every row — same dataset, different scope.
- The greenfield author wrote zero `constants.py` (sheet IDs inline in `app.py`, internal IDs auto-resolved per L.1.16) and used only the L.1 primitives — the validation that Phase L's API design is sound.

**Browser e2e for Executives (L.7)**

- 20 new tests across 4 modules in `tests/e2e/`. API + browser layers cover dashboard / analysis / 2-dataset existence, sheet structure (4 sheets with descriptions, per-sheet visual counts, the visual-pinned active-only filter scoping), embed URL, sheet-tab smoke, and per-sheet visual rendering via `TreeValidator(exec_app, page).validate_structure()`.
- The `test_exec_*.py` files derive expected sets from the tree (`exec_app.analysis.{sheets,parameters,filter_groups}`) instead of hand-listed dicts — the L.11 "tree IS the source of truth" pattern.
- Cleanup of three pre-existing structural-test debts surfaced during the run-the-suite step: two Investigation tests rewritten off legacy hardcoded `V_INV_*` VisualIds onto analyst-facing visual titles (then dropped the now-orphan V_INV_* exports from `apps/investigation/constants.py`); one PR test rewritten to derive from `pr_app.dataset_dependencies()` (was over-asserting via the fixture, which still listed the unreferenced `merchants-dataset`).

**Docs sweep + mkdocstrings-driven Python API reference (L.9)**

- New "Tree pattern" section in `CLAUDE.md` under Architecture Decisions covering the three-layer model (L1 / L2 / L3), the persona-blind primitives rule, and the "tree IS the source of truth" rule with three concrete tree-walking examples from the codebase.
- `CLAUDE.md` project-structure tree refreshed: `common/tree/` expanded as a package with all 13 sub-modules listed; per-app entries collapsed off the dropped `analysis.py` / `visuals.py` / `filters.py` shape into `app.py`-only; `common/{persona,drill,ids}.py` + `apps/executives/` added; tests + e2e listings refreshed.
- README updated for 4 apps throughout — new Executives table block in "The four apps" section, demo scenario block, deploy commands, project structure, customising section.
- mkdocstrings wired into the mkdocs build (`mkdocstrings[python]>=0.26` added to `docs` extras). 7 API reference pages under `src/quicksight_gen/docs/api/`: `index.md` (overview + three-layer model), `tree-structure.md`, `tree-visuals.md`, `tree-data.md`, `tree-filters-controls.md`, `tree-actions.md`, `common-foundations.md`.
- New customization handbook walkthrough: "How do I author a new app on the tree?" — L.6 Executives is the worked example. `mkdocs build --strict` clean.

**Release pipeline reliability**

- **Post-publish install verification (L.10.0).** Two new symmetric jobs in `.github/workflows/release.yml` — `verify-testpypi-install` (gates `publish-pypi`) and `verify-pypi-install` (gates `github-release`). Each polls `pip install quicksight-gen==<TAG>` from the relevant index with retries (CDN propagation lag), confirms `quicksight-gen --version`, and runs a smoke import of the public surface (`common.tree.{App,Sheet,visuals,filters,actions}` + each app's `build_<app>_app` entry point). Catches missing-package-data or stripped-import bugs the local-wheel `smoke` job can't see, and prevents a half-published-then-unfetchable package from getting a GitHub Release.
- **Release gated on tests passing (added after v5.0.0 cancellation).** Added a `tests` job at the start of `release.yml` that re-runs the same pytest+pyright matrix `ci.yml` runs (Python 3.12 + 3.13). `build` depends on `tests`, so any test failure stops the release before any publish step fires — preventing the v5.0.0 situation where TestPyPI publish ran despite CI being red on the same commit. The new gate proved itself immediately by stopping the v5.0.1 attempt cold (see playwright fix below) before any publish ran.
- **e2e drilldown collection-time crash (v5.0.1 fix for v5.0.0 failure).** `tests/e2e/test_drilldown.py` and `test_ar_drilldown.py` call helpers at module import time (pytest.mark.parametrize argument) that load `config.yaml`. On stock CI (no config, no `QS_GEN_*` env) the load raises `ValueError` *before* the `QS_GEN_E2E` gate in `conftest.py` has a chance to skip the test. Fix: catch the `ValueError` and return an empty parameter list — pytest then marks the test as "no parameters" and skips cleanly. On a configured dev box the full enumeration still runs.
- **screenshot_harness URL tests skip without playwright (v5.0.2 fix for v5.0.1 failure).** Two `tests/test_screenshot_harness.py::TestCaptureWithStateUrlConstruction` tests exercise the URL-construction logic via `capture_with_state`, which transitively calls into `tests/e2e/browser_helpers` helpers that lazy-import `playwright.sync_api`. CI's `[dev]` extras don't pull in playwright (it lives under `[e2e]`), so the import raises `ModuleNotFoundError`. Fix: an `autouse` `pytest.importorskip("playwright")` fixture on the test class so it skips cleanly without playwright; locally the tests still run when `[e2e]` is installed. Refactoring `capture_with_state` to expose its URL-construction in a playwright-free helper is queued under tech-debt (cleaner than skipping, but bigger surgery).

### Migration path for external callers

For programmatic dashboard construction, replace per-app builder imports with the public tree API:

```python
# v4.x — gone
from quicksight_gen.apps.payment_recon.analysis import build_analysis
from quicksight_gen.apps.payment_recon.visuals import sales_overview_visual
from quicksight_gen.apps.payment_recon.filters import build_filter_group
from quicksight_gen.apps.payment_recon.constants import V_PR_SALES_KPI

# v5.0 — tree-based public API
from quicksight_gen.common.tree import App, Sheet
from quicksight_gen.common.tree.visuals import KPI, Table, BarChart, Sankey
from quicksight_gen.common.tree.filters import FilterGroup
from quicksight_gen.common.tree.actions import Drill
from quicksight_gen.apps.payment_recon.app import build_payment_recon_app
```

Read [`How do I author a new app on the tree?`](https://chotchki.github.io/Quicksight-Generator/walkthroughs/customization/how-do-i-author-a-new-app-on-the-tree/) for the worked-example narrative; the [API Reference](https://chotchki.github.io/Quicksight-Generator/api/) covers every public class.

The L.5 (default-vs-demo overlay) and L.8 (Executives handbook + walkthroughs) substeps were deferred to Phase M (Whitelabel-V2), where the persona-substitution surface gets unified across all four apps and the Executives copy lands in its final whitelabel-ready shape.

---

## v4.0.0

### Phase K.4 — Investigation app + `apps/` namespace re-org (major)

Adds a third independent QuickSight app — **Investigation**, the AML / compliance triage surface — alongside Payment Reconciliation and Account Reconciliation. Reads from the same shared `transactions` + `daily_balances` base tables (no schema change), backed by two new materialized views that pre-compute rolling-window pair statistics and recursive chain walks.

The major bump is earned by two breaking changes that ride along with the new app:

- The `payment_recon/` and `account_recon/` packages moved into a new `apps/` namespace (`quicksight_gen.apps.payment_recon` / `quicksight_gen.apps.account_recon`). Per the project's "no backward-compat shims" rule, no compatibility re-exports — external callers update their imports.
- The `quicksight_gen.training.distribute` and `quicksight_gen.training.publish` modules (superseded by `quicksight-gen export training` + `whitelabel.py` in v3.4.0 but never deleted) are gone. The `training/` tree is now pure content.

### What landed

**Re-org under `apps/` namespace + obsolete-script cleanup (K.4.1)**

- `src/quicksight_gen/payment_recon/` → `src/quicksight_gen/apps/payment_recon/`.
- `src/quicksight_gen/account_recon/` → `src/quicksight_gen/apps/account_recon/`.
- Every import across src + tests + scripts updated to `quicksight_gen.apps.{payment_recon,account_recon}`.
- `src/quicksight_gen/training/distribute.py` (handbook zipper — replaced by `export training`'s folder copy) and `src/quicksight_gen/training/publish.py` (string substitution — duplicated by `whitelabel.py`) deleted. `training/` is now pure content (handbook/, QUICKSTART.md, mapping.yaml.example).
- `src/quicksight_gen/docs/` (operator handbook, mkdocs source) and `src/quicksight_gen/training/` (audience-organized cross-training, whitelabel-able) stay separate trees with separate export paths. Merging the two is queued in Backlog under "Docs/Training Tree Merge"; today's split is the right call until K.4.x lands more targeted training examples.

**Investigation app skeleton + theme preset (K.4.2)**

- New `src/quicksight_gen/apps/investigation/` package mirroring `account_recon/`'s layout (`analysis.py`, `visuals.py`, `filters.py`, `datasets.py`, `demo_data.py`, `constants.py`, `etl_examples.py`).
- Wired into the CLI: `generate`, `deploy`, `demo apply` / `seed` / `etl-example` all accept `investigation` as a third app key; `--all` includes it.
- New `sasquatch-bank-investigation` theme preset (slate blue + amber alert palette).
- Five sheets: Getting Started + Recipient Fanout / Volume Anomalies / Money Trail / Account Network.

**Recipient Fanout sheet (K.4.3)**

- New `inv-recipient-fanout-dataset` (one row per (recipient leg, sender leg) pair sharing a `transfer_id`); recipient pool filtered to customer DDAs + merchant DDAs only so administrative sweeps don't dominate the ranking.
- Threshold filter is the analysis-level windowed calc field `recipient_distinct_sender_count = distinctCount({sender_account_id}, [{recipient_account_id}])`, gated by a `NumericRangeFilter` whose minimum is bound to a `pInvFanoutThreshold` integer parameter (slider 1–20, step 1, default 5).
- Three KPIs (qualifying recipients / distinct senders / total inbound) + recipient-grain ranked table sorted by distinct sender count desc.

**Volume Anomalies sheet (K.4.4)**

- New materialized view `inv_pair_rolling_anomalies` computes per-(sender, recipient) rolling 2-day SUM (`RANGE BETWEEN INTERVAL '1 day' PRECEDING AND CURRENT ROW` partitioned by sender+recipient) plus the population mean + sample standard deviation across all pair-windows; per-row z-score and 5-band z-bucket label projected at refresh time.
- σ threshold (`pInvAnomaliesSigma` integer parameter, default 2, slider 1–4 step 1) bound to a `NumericRangeFilter` on `z_score`, scoped `SELECTED_VISUALS` to KPI + table only — the distribution chart sees the full population so the cutoff lands in context.
- Visuals: KPI flagged-pair count + vertical bar chart (X = z_bucket, Y = COUNT) + table grouped to (sender, recipient, window_end) sorted by z_score desc.

**Money Trail sheet (K.4.5)**

- New materialized view `inv_money_trail_edges` walks `parent_transfer_id` chains via `WITH RECURSIVE`, flattened to one row per multi-leg edge with chain root, depth from root, source × target leg pair, and `source_display` / `target_display` strings (`name (id)`) for unambiguous dropdowns and tables.
- Visuals: native QuickSight Sankey as the headline + hop-by-hop detail table beside it. Filters: chain-root dropdown, max-hops slider, min-hop-amount slider.
- Single-leg PR transfers (`sale`, `external_txn`) appear in the table but don't draw Sankey ribbons — the matview projects multi-leg edges only.

**Investigation demo data + cross-app scenario coverage (K.4.6)**

- New `apps/investigation/demo_data.py` plants three converging scenarios on a single anchor account (Juniper Ridge LLC, `cust-900-0007-juniper-ridge-llc`):
  - Fanout cluster — 12 individual depositors × 2 ACH transfers each → Juniper.
  - Anomaly pair — Cascadia Trust Bank — Operations → Juniper, 8 baseline routine wires + 1 spike day ($25,000 wire vs ~$300–$700 baseline).
  - Money trail — 4-hop layering chain rooted on a Cascadia wire, fanning through Juniper into three shell DDAs (Shell A → B → C).
- Investigation registers its own internal ledger (`inv-customer-deposits-watch`) + two external ledgers so `demo seed investigation` is FK-safe standalone. Same Sasquatch National Bank persona — the Compliance / Investigation team is the third operational view of the same bank.
- `TestScenarioCoverage` assertions in `tests/test_investigation_demo_data.py`; per-app SHA256 seed hash locked.

**Cross-app drill plumbing — investigated, dropped (K.4.7)**

- Built `CustomActionURLOperation` model + `cross_app_drill()` URL-deep-link helper, wired three deferred Investigation → AR Transactions drills, proved the URL form `https://{region}.quicksight.aws.amazon.com/sn/dashboards/{id}/sheets/{sheet_id}#p.<param>=<<column>>` substitutes cleanly. Then **dropped the feature** — QuickSight doesn't sync sheet parameter controls to URL-set values: data filters correctly, but the on-screen control widgets continue to show "All". Same defect affects QS's own intra-product Navigation Action with parameters.
- Re-entry conditions documented in PLAN.md "QuickSight URL-parameter control sync — known platform limitation". The dropped K.4.7 code is recoverable from git history if a future static-link or non-parameterized URL feature wants it.

**Account Network sheet (K.4.8)**

- Second view over the K.4.5 matview, account-anchored instead of chain-rooted. Two side-by-side directional Sankeys (inbound on the left, outbound on the right, anchor visually meeting in the middle) + full-width touching-edges table below.
- Anchor parameter (`pInvANetworkAnchor`) backed by a small dedicated dataset wrapper (`inv-anetwork-accounts-ds`) that pre-deduplicates display strings, so the dropdown opens fast on a large matview.
- Walk-the-flow drill: right-click any table row → "Walk to other account on this edge" overwrites the anchor with the counterparty side; left-click any node in either directional Sankey performs the same walk (each directional Sankey has only one possible walk target). Per the K.4.7 control-sync defect, the dropdown widget may briefly lag behind a walk — sheet description tells analysts "trust the chart, not the control text".

**Browser e2e for Investigation (K.4.9)**

- 28 new tests across 6 modules in `tests/e2e/` mirroring AR's coverage shape, plus `inv_dashboard_id` / `inv_analysis_id` / `inv_dataset_ids` fixtures and matview warmups in the session-scoped Aurora pre-warm.
- API + browser layers cover: dashboard / analysis / 5-dataset existence, sheet structure (5 sheets with descriptions, per-sheet visual counts, K.4.8 directional-Sankey invariant — both inbound + outbound titles must surface), embed URL, sheet-tab smoke, and per-sheet visual rendering with TALL_VIEWPORT (1600×4000) for the Account Network's stacked layout.
- Three tests deferred for DOM follow-up (skipped with documented reasons): URL-hash parameter pre-seeding breaks dashboard loading, and the walk-the-flow drill needs a more reliable witness than touching-edges row count. The skipped surface is filter / drill propagation; the structural + render surface is fully covered.

**Investigation handbook + walkthroughs (K.4.10)**

- New `docs/handbook/investigation.md` plus four walkthroughs in `docs/walkthroughs/investigation/`, one per sheet's core question:
  - Who's getting money from too many senders? (Recipient Fanout)
  - Which sender → recipient pair just spiked? (Volume Anomalies)
  - Where did this transfer actually originate? (Money Trail)
  - What does this account's money network look like? (Account Network)
- Frames Investigation as **question-shaped** — pick the sheet whose question matches yours, no fixed reading order — vs. PR's pipeline-staged flow and AR's morning rotation.
- Every "Drilling in" section names the next sheet (intra-app) plus AR Transactions / PR pipeline tabs (cross-app at the row-evidence stage). `mkdocs.yml` nav extended with an Investigation Handbook block after PR; `docs/index.md` updated to count three apps.

### Conventions

- Investigation reads the shared base tables only — no investigation-specific schema, no app-specific dimension tables. Persona consistency: same Sasquatch National Bank, three operational views (Merchant Support / Treasury / Compliance).
- Sankey visuals use QuickSight's native `SankeyDiagramVisual` — feasibility validated in K.4.0's spike before any other K.4 code shipped.
- The two new matviews follow the same refresh contract as `ar_unified_exceptions`: declare `MATERIALIZED` in `schema.sql`, add `REFRESH MATERIALIZED VIEW <name>;` to the `demo apply` block in `cli.py`, document under [Materialized views](src/quicksight_gen/docs/Schema_v3.md#materialized-views) in Schema_v3.

### Migration

- **External callers importing `quicksight_gen.payment_recon.*` or `quicksight_gen.account_recon.*`**: update to `quicksight_gen.apps.payment_recon.*` and `quicksight_gen.apps.account_recon.*`. No compatibility re-exports — the old paths raise `ModuleNotFoundError`. Internal CLI / generate / deploy entry points are unchanged at the user-visible layer.
- **External callers importing `quicksight_gen.training.distribute` or `quicksight_gen.training.publish`**: both modules are deleted. Replacement is `quicksight-gen export training` (folder copy + whitelabel substitution in one step), which has been the supported path since v3.4.0.
- **ETL teams**: two new materialized views (`inv_pair_rolling_anomalies`, `inv_money_trail_edges`) join the existing `ar_unified_exceptions` under the same REFRESH contract. After every ETL load, run all three `REFRESH MATERIALIZED VIEW` statements — see [Materialized views](src/quicksight_gen/docs/Schema_v3.md#materialized-views) for the full contract. Skipping a refresh means anomaly z-scores and chain edges lag the source data; no data integrity loss, just stale operator-facing columns.
- **Deploy teams**: `quicksight-gen deploy` now manages a third dashboard (`qs-gen-investigation-dashboard`) + analysis + 5 Investigation datasets. `quicksight-gen cleanup` enumerates them under the same `ManagedBy:quicksight-gen` tag. `quicksight-gen demo apply --all` now seeds + refreshes Investigation alongside PR + AR.
- **Theme consumers**: a new `sasquatch-bank-investigation` preset is registered in `common/theme.py`. Existing PR (`sasquatch-bank`) + AR (`sasquatch-bank-ar`) presets unchanged.
- **No cross-app drill paths exist between Investigation and PR/AR.** K.4.7 dropped the URL-deep-link approach because QuickSight doesn't sync sheet parameter controls to URL-set values. Investigators leave the dashboard for AR Transactions / PR pipeline tabs by manually navigating; the Investigation handbook's "Drilling in" sections name the destination tab + filter explicitly to keep the path obvious.

---

## v3.8.0

### Phase K.3 — Lateness as a data column, not an operator threshold

Replaces the operator-applied "is this row past N days?" pattern with a per-leg `expected_complete_at` timestamp on `transactions` and a downstream `is_late` boolean predicate. PR's `late_default_days` config knob (and its slider) retire — the data answers, the slider only ever existed because the data didn't. AR gains an explicit Lateness picker on the Today's Exceptions and Trends sheets; the unified table surfaces `is_late` per row. Schema change is additive — the new column is NULLABLE with a `posted_at + INTERVAL '1 day'` COALESCE fallback, so existing ETL keeps working unchanged.

### What landed

**Schema + portability + ETL contract (K.3.0)**

- `transactions` gains `expected_complete_at TIMESTAMP NULL`. Not added to `daily_balances` — those are point-in-time snapshots; lateness is per-leg. The default formula `COALESCE(expected_complete_at, posted_at + INTERVAL '1 day')` is portable across the project's target RDBMS family (no JSONB, no Postgres-specific operators).
- New "Lateness as data" section in `docs/Schema_v3.md` documents the column as optional, the default formula, the `is_late` predicate SQL, and the multi-leg tie-breaker rule (the **earliest debit leg's** `expected_complete_at` becomes the transfer-level deadline).
- New "Optional: `expected_complete_at` (lateness)" section in `docs/handbook/etl.md` for the ETL team, with an "adopt one rail at a time" framing.

**Demo generators populate `expected_complete_at` per rail (K.3.1)**

- PR generator: card payments → T+3, external_txn rows → +1 hour (rail observations expected to settle almost immediately), sales / settlements / non-card payments → NULL (default applies).
- AR generator: instant rails (Fed wires, on-us internal) → same-day; ACH → T+2; non-rail-bound legs → NULL.
- Per-app SHA256 seed-hash assertions re-locked. New `TestExpectedCompleteAt` coverage classes pin the rail-specific values per generator.

**Datasets surface `is_late` + `expected_complete_at` (K.3.2)**

- `is_late STRING` (`"Late" / "On Time"` — labeled to match the codebase's `is_failed` / `is_returned` STRING convention so QS filter controls stay simple) and `expected_complete_at DATETIME` columns added to `ar_unified_exceptions` and the relevant per-check views, plus PR's exception + recon datasets. `DatasetContract` entries updated so the contract test catches projection drift.
- The `is_late` predicate is `CURRENT_TIMESTAMP > COALESCE(expected_complete_at, posted_at + INTERVAL '1 day')`. PR's recon dataset now derives `match_status` from the same predicate (`'matched'` / `'late'` / `'not_yet_matched'`) instead of the operator-threshold `(CURRENT_DATE - posted_at::date) > late_default_days` it used through K.2a.
- Shared `_lateness_columns()` helper in `payment_recon/datasets.py` keeps the SQL fragment in one place across the 6 PR datasets that surface lateness.

**KPIs / filters / visuals consume `is_late` (K.3.3)**

- AR Today's Exceptions + Trends sheets gain a Lateness picker (new `fg-ar-todays-exc-is-late` filter group + cross-sheet controls on both sheets). The unified Open Exceptions table surfaces `is_late` between `aging_bucket` and `account_id`.
- PR Late Transactions KPI subtitle updated to "Unmatched transactions past their expected completion time (per-row `is_late = 'Late'`)". The visual-pinned `match_status='late'` filter stays — it's the unmatched-AND-late semantic, which is the more actionable ops view than `is_late='Late'` alone (matched-but-late rows already resolved).
- `cfg.late_default_days` field + `QS_GEN_LATE_DEFAULT_DAYS` env var fully retired across `Config`, `README.md`, `docs/walkthroughs/customization/how-do-i-configure-the-deploy.md`, `CLAUDE.md`, and `SPEC.md`. Per the project's "no backward-compat shims" rule, no fallback / no flag — the deprecated knob is just gone.

**Handbook + walkthrough updates (K.3.4)**

- PR walkthrough `why-is-this-external-transaction-unmatched` rewritten to drop the hardcoded 30-day matching-window framing. Demo-data and "what it means" sections now reflect that with the rail-hour deadline (`expected_complete_at = posted_at + 1 hour` for external_txn), almost every unmatched external row is `match_status = 'late'`; orphan-recent vs orphan-late are reframed as aging buckets (urgency tiers) rather than match_status tiers.
- Customization handbook gains an "Optional ETL extensions" section introducing `expected_complete_at` (with the +1-day fallback) alongside the existing `metadata` extension, linking out to the schema's "Lateness as data" + the ETL handbook.
- AR + PR handbook Reference lists add Lateness-as-data cross-links to `Schema_v3.md`, with one-sentence framing for each app.

### Conventions

- Same theme as K.2 / K.2a: invariants encoded in the data shape itself rather than an operator-applied threshold. K.2 caught wrong-shape source-field bindings at the wiring line; K.2a caught wrong-kind-of-identifier mis-bindings at the same line; K.3 removes a whole class of "did the operator pick the right N?" inconsistency by moving the threshold into the row.
- The `is_late` column is STRING (`"Late" / "On Time"`), not BOOLEAN — matches the existing `is_failed` / `is_returned` STRING convention so QuickSight `CategoryFilter` controls stay uniform across the codebase.

### Migration

- **ETL teams**: `expected_complete_at` is fully optional. Every existing feed keeps working — when the column is NULL, the COALESCE fallback uses `posted_at + INTERVAL '1 day'`, which matches the conservative-default `is_late` semantic. Adopt rail-by-rail when convenient (the ETL handbook section recommends starting with whichever rail your team gets the most "is this really late or just slow?" questions about).
- **Dataset SQL consumers**: 6 PR datasets and `ar_unified_exceptions` gained `is_late` + `expected_complete_at` columns. Downstream consumers parsing `DatasetContract` will see new entries; existing column reads are unchanged.
- **`late_default_days` users**: the field is removed from `Config`. If you set it in `config.yaml` or via `QS_GEN_LATE_DEFAULT_DAYS`, that key is now ignored (and YAML loaders will not raise — it's just a silent no-op key). The slider on the PR Payment Reconciliation tab is gone; lateness comes from the data.

---

## v3.7.0

### Phase K.2a — Identifier scatter cleanup (typed constants for opaque IDs)

K.2 closed a class of cross-sheet drill bugs by encoding source-field / parameter shapes in the type system. K.2a applies the same approach to the rest of the identifier surface — filter group IDs, visual IDs, parameter names, demo-persona whitelabel strings, and the AWS enum-like fields (`ElementType`, `CrossDataset`, `Status`, `Scope`, `Trigger`). Before K.2a, these all appeared as bare string literals scattered across 12+ files each, with test fixtures hand-maintained against the literals from production code. A typo or rename in one place silently broke the binding without raising at deploy. After K.2a, every kind of identifier has a typed constant; mypy / pyright catches the wrong-kind-of-string at the call site instead of leaving it for a deploy-time visual mis-scope.

No analysis, dataset, schema, or runtime behavior change — type-system-only refactor.

### What landed

**Filter group ID constants (K.2a.1)**

- All 118 `fg-ar-*` / `fg-pr-*` literals promoted to `FG_*` module-level constants in `account_recon/constants.py` + `payment_recon/constants.py`. e2e existence tests now import `ALL_FG_AR_IDS` / `ALL_FG_PR_IDS` frozensets from the constants module instead of hand-maintaining `EXPECTED_IDS` against the production literals — adding a new filter group can no longer silently de-sync the test fixture.

**PR drill parameter constants (K.2a.2)**

- Added `P_PR_SETTLEMENT` / `P_PR_PAYMENT` / `P_PR_EXTERNAL_TXN` to `payment_recon/constants.py` mirroring AR's `P_AR_*`. Replaces ~75 plain-string occurrences of `pSettlementId` / `pPaymentId` / `pExternalTransactionId` across `payment_recon/analysis.py` + `recon_visuals.py` + tests.

**Visual ID constants (K.2a.3)**

- Largest mechanical sweep: all 314 visual ID literals are now `V_*` constants. `visuals.py` defines visuals via the constants; `analysis.py` `FilterGroup` scopes reference via the constants; e2e existence checks reference via the constants. Catches the K.2a-shaped silent bug — visual IDs flow into `SheetVisualScopingConfigurations.VisualIds`, where a typo silently widens scope (no error, just a filter that fails to apply to the expected visual).

**`NewType` wrappers in `common/ids.py` (K.2a.4)**

- New `common/ids.py` defines `SheetId`, `VisualId`, `FilterGroupId`, `ParameterName` as `NewType("...", str)`. The `*_ID` constants in both apps' `constants.py` are declared as the corresponding NewType, and function signatures across `analysis.py` / `filters.py` annotate accordingly. Same shape K.2 used for `ColumnShape` — wrong-kind-of-string fails at the call site, not in a deploy-time scope assertion.

**`DemoPersona` dataclass + auto-derived `mapping.yaml.example` (K.2a.5)**

- New `common/persona.py` introduces `DemoPersona` (a frozen dataclass with `institution`, `stakeholders`, `gl_accounts`, `account_labels`, `merchants`, `flavor`, `intentional_non_mappings` tuples) and a `SNB_PERSONA` instance — the single source of truth for whitelabel-substitutable strings ("Sasquatch National Bank", "Federal Reserve Bank", the `gl-1010` family, "Margaret Hollowcreek", etc.).
- `training/mapping.yaml.example` is now auto-derived via `derive_mapping_yaml_text(persona)`. A new `tests/test_persona.py::test_shipped_yaml_matches_derived` parity test fails loudly (printing the regenerated body) when the dataclass and the shipped YAML diverge — so a merchant rename in `demo_data.py` can no longer silently de-sync the publish-time substitution template. Connects to the post-K re-skinnable demo plan: the same `DemoPersona` instance the demo generators consumed at hash-lock time is what the substitution layer rewrites for the publish target, *after* the SHA256 seed-hash check.
- The follow-up of refactoring both `demo_data.py` modules to consume `DemoPersona` at every literal site is incremental and deferred — the SHA256 hash assertions need to stay green and that's a careful per-call refactor. The dataclass alone serves as source of truth for *what's substitutable*.

**AWS enum-like strings → `Literal` + class constants (K.2a.5b)**

- `ElementType`, `CrossDataset`, `Status`, `Scope`, `Trigger` were typed as bare `str` on the bearing dataclasses (`GridLayoutElement`, `FreeFormLayoutElement`, `FilterGroup`, `SheetVisualScopingConfiguration`, `VisualCustomAction`). A typo like `ElementType="VISULA"` survived to deploy time. The annotations are now `Literal[...]` so a typo fails under static analysis, and each bearing class now exposes `ClassVar` constants (`GridLayoutElement.VISUAL`, `FilterGroup.SINGLE_DATASET`, `SheetVisualScopingConfiguration.ALL_VISUALS`, `VisualCustomAction.DATA_POINT_CLICK` / `.ENABLED`) so call sites are IDE-discoverable.
- Swept all ~75 internal call sites to use the constants. Runtime is unchanged (Literal is a type-checker construct; ClassVar values resolve to the same string).

### Conventions

- Same theme as K.2: invariants encoded in the type system rather than in post-hoc validation tests. K.2 caught a wrong-shape source-field binding at the wiring line; K.2a catches a wrong-kind-of-identifier (visual ID where a sheet ID was expected) at the same line. The `DemoPersona` parity test is the one exception — encoded as a test rather than a type because the YAML is a serialized projection and "byte-equal" is the actual invariant; the test prints the regenerated body so the fix is paste-ready.

### Migration

- No migration needed for end users. The new constants module + `DemoPersona` dataclass are additive; no existing call site signature changed at runtime. Downstream consumers that import from the per-app `constants.py` modules will see new exported names but no removals.

---

## v3.6.1

### Docs — `ar_unified_exceptions` matview refresh contract

Re-cut of v3.6.0 with the operator-facing matview refresh contract written down. v3.6.0 shipped the conversion of `ar_unified_exceptions` to a `MATERIALIZED VIEW` (so the Today's Exceptions sheet renders under QuickSight Direct Query) and wired the refresh into `quicksight-gen demo apply` — but `docs/Schema_v3.md` did not mention the matview, so production ETL teams had no canonical reference for the refresh requirement. v3.6.1 adds it.

- **New "Materialized views" section in `docs/Schema_v3.md`** — sits between *Computed views catalogue* and *ETL examples*. Lists `ar_unified_exceptions`, the `REFRESH MATERIALIZED VIEW` requirement after each ETL load, the timing semantics for `days_outstanding` / `aging_bucket` (computed at refresh time, not query time — skipping a refresh lags the analyst-facing aging), and a "when to materialize" rule for future check views that cross the same read-cost threshold.
- **`docs/handbook/etl.md` cross-link** — *The contract* section now mentions the matview + REFRESH requirement and links to the Schema_v3 section. ETL team members reading the contract overview now see the requirement before they design their pipeline.

No code changes. No analysis, dataset, schema, or runtime behavior changes — `demo apply` was already running the REFRESH; this release just documents it for non-demo operators.

---

## v3.6.0

### Phase K.2 — Cross-sheet navigation parameter hygiene

This release closes a class of cross-sheet drill bugs where a destination sheet would silently render zero rows. Two underlying causes: (1) parameter-bound `CategoryFilter`s match the literal empty string when a parameter is at its sentinel default, suppressing every row; (2) drill source-field shapes coerced through `SINGLE_VALUED` string parameters could end up textually incompatible with the destination column they were meant to filter. K.2 makes both classes unrepresentable at the wiring site.

### What landed

**Calc-field PASS drill shape (K.2.1)**

- All six AR cross-sheet drill `FilterGroup`s switched from parameter-bound `CategoryFilter`s to a calc-field PASS shape: a per-drill calculated field returns `'PASS'` when the parameter is at its `__ALL__` sentinel OR when the row's column matches the parameter, and the filter retains only `'PASS'` rows. Removes the empty-string-match suppression that had every drill destination silently rendering zero rows when invoked from a non-defaulted source.
- `_DRILL_SPECS` becomes the single source of truth driving both the calc-field declarations and the matching `FilterGroup`s. `_drill_param_declaration()` raises if a name isn't in the derived sentinel-default set, so an incompatible declaration can't be silently constructed.

**Typed cross-sheet drill helpers (K.2.2)**

- New `common/drill.py` introduces `ColumnShape` (DATE_YYYY_MM_DD_TEXT, DATETIME_DAY, ACCOUNT_ID, SUBLEDGER_ACCOUNT_ID, LEDGER_ACCOUNT_ID, TRANSFER_ID, TRANSFER_TYPE), `DrillParam` (param + expected shape), `DrillSourceField` (source field + actual shape), and `cross_sheet_drill()` which refuses construction when the source-field shape can't assign to the destination param's expected shape. `SUBLEDGER_ACCOUNT_ID` and `LEDGER_ACCOUNT_ID` widen to `ACCOUNT_ID`; date encodings explicitly do not widen — that's the K.2 bug class (DATETIME silently coerced to a timestamp text that never matched a `TO_CHAR`'d YYYY-MM-DD column). The check fires at the wiring line, not in a downstream output-walking test.
- `build_dataset()` now takes a `visual_identifier` and registers each contract in a module-level registry, letting `field_source(visual_identifier, column_name)` resolve column shapes from the contract instead of duplicating shape annotations at every call site. Both `payment_recon/datasets.py` and `account_recon/datasets.py` register their full contract sets at import time so the registry is populated regardless of construction order.

**Drill-site migration + stale-param hygiene (K.2.3)**

- All 7 PR drill sites and all 6 AR drill sites migrated to `cross_sheet_drill()`. The 4 AR drills targeting the Transactions sheet flow through a new `_ar_drill_to_transactions()` helper that auto-resets every PASS-filtered param the caller doesn't explicitly write — closes a stale-param leak where a prior drill's value would silently narrow Transactions to zero rows. A `tests/test_account_recon.py::TestTransactionsDrillStaleParamHygiene` guard pins the helper's auto-reset set to `analysis._DRILL_SPECS` so a new drill spec can't bypass it.
- New `pArAccountId` parameter + `fg-ar-drill-account-on-txn` filter group added on the Transactions sheet for the K.1 Today's Exceptions account-day right-click drill (the K.1 spike landed these but the e2e dashboard-structure fixtures were never updated; synced here).
- New `tests/e2e/test_ar_cross_sheet_param_hygiene.py` (297 LoC) covers the full param-reset + PASS-filter behavior end-to-end against the deployed dashboard.

**Schema + Today's Exceptions drill bug fix**

- `ar_unified_exceptions` becomes a **MATERIALIZED VIEW** in `schema.sql`. The 14-block `UNION ALL` was too heavy for QuickSight Direct Query and the Today's Exceptions sheet wouldn't render. Operators must `REFRESH MATERIALIZED VIEW ar_unified_exceptions` after each ETL load; `demo apply` does this automatically.
- The Today's Exceptions account-day drill bound `exception_date` (DATETIME) to `pArActivityDate` (SINGLE_VALUED string), which QuickSight coerced to `"2026-04-07 00:00:00.000"`. The destination's `posted_date` filter compared that against `TO_CHAR(..., 'YYYY-MM-DD')` strings — never matched. Added an `exception_date_str` column to the unified exceptions projection (`DATE_YYYY_MM_DD_TEXT`) and switched the drill `SourceField` `FieldId` to bind that column instead. The K.2.2 type system would have caught this wiring at construction time.

**Conventions**

- `CLAUDE.md` gains a Conventions rule: **encode invariants in the type system, not in validation tests.** Typed wrappers + `__post_init__` validation + typed constructors that fail at the buggy line are preferred over post-hoc tests that walk generated output.

### Known issues

- 5 PR `FilterControl` dropdown e2e tests (`test_cashier_multi_select_narrows_sales`, `test_payment_method_narrows_payments`, three `test_show_only_toggle_narrows_and_clears` parametrize cases) time out in `_open_control_dropdown`. Pre-existing — failing on `v3.5.2` and on every K.2 commit, both serial and `--parallel 4`. Not a K.2 regression. Logged under `PLAN.md` Phase L Backlog > Test Reliability with the failing test ids, the broken selector, and a diagnostic path. Net e2e: 156 / 161.

---

## v3.5.2

### Release pipeline — SLSA build provenance + Node 24 actions

Supply-chain hardening for the release workflow. No analysis, dataset, or handbook changes.

- **SLSA build provenance attestations.** The release workflow's build job now runs `actions/attest-build-provenance@v4` against every artifact in `dist/`. Each release tag publishes a signed Sigstore attestation tying the wheel + sdist back to the exact commit, workflow run, and runner identity that produced them; visible at <https://github.com/chotchki/Quicksight-Generator/attestations>. Build job grants `id-token: write` + `attestations: write`; rest of the workflow keeps `contents: read` default.
- **All `actions/*` steps moved to latest majors** — `checkout` v4→v6, `setup-python` v5→v6, `upload-artifact` v4→v7, `download-artifact` v4→v8, `upload-pages-artifact` v3→v5, `deploy-pages` v4→v5. Clears the Node.js 20 deprecation warning ahead of the September 2026 runner removal. `softprops/action-gh-release` also bumped v2→v3.

---

## v3.5.1

### CI fix — boto3 in dev extras + workflow permissions

Re-cut of v3.5.0 (rejected at the PyPI approval gate) with two follow-up fixes that landed against `main` after the v3.5.0 tag was pushed.

- **`boto3>=1.34` added to `[project.optional-dependencies] dev`** in `pyproject.toml`. `tests/test_deploy.py` imports `quicksight_gen.common.deploy`, which has a module-level `import boto3`; the dev install previously only pulled `boto3-stubs` (type stubs, not the runtime package), so `pip install -e ".[dev]" && pytest` failed at collection time on a clean machine. Local `.venv` had boto3 from past `deploy` runs and masked the gap; CI on the v3.5.0 commit caught it.
- **Workflow-level `permissions: contents: read`** added to `.github/workflows/ci.yml` and `.github/workflows/release.yml` to satisfy CodeQL `actions/missing-workflow-permissions` findings (alerts #1–4). Per-job overrides on `coverage-badge` (`contents: write`), `publish-testpypi` / `publish-pypi` (`id-token: write`), and `github-release` (`contents: write`) are unchanged — they replace the workflow default for jobs that need elevated access.

No analysis, dataset, or handbook changes.

---

## v3.5.0

### Phase K.1 — AR Exceptions split + handbook rewrite + MIT relicense

This release rolls up Phase K.1 (the AR Exceptions density refactor and full AR Handbook rewrite) and a project-level relicense from the Unlicense to the MIT License. Dashboards: the AR Exceptions tab is gone, replaced by **Today's Exceptions** (unified-table operational view) and **Exceptions Trends** (rollups + aging matrix + per-check daily trend). Handbook: every per-check walkthrough rewrites against the new sheets. License: MIT replaces the Unlicense for clearer downstream usability.

### What landed

**AR Exceptions workflow split (K.1.0 – K.1.5)**

- **`ar_unified_exceptions` dataset** — UNION ALL across 14 per-check views with a `check_type` discriminator, severity-coloured tagging (`drift` / `overdraft` → red, `expected-zero` → orange, `limit-breach` → amber, others → yellow), and harmonized columns (`account_id`, `account_name`, `account_type`, `posted_at`, `balance_date`, `days_outstanding`, `aging_bucket`, `primary_amount`, `secondary_amount`). Wide+NULL projection — every check's specific column is first-class, NULL-filled where not applicable. Locked by `DatasetContract`.
- **Today's Exceptions sheet** — replaces the per-check KPI/table/aging blocks with one severity-coloured KPI strip + Check Type / Account / Aging / Transfer Type / Origin sheet controls + one *Open Exceptions* unified table sorted by severity then aging. Drill: right-click `account_id` → "View Transactions for Account-Day"; left-click `transfer_id` → Transactions sheet scoped to that transfer.
- **Exceptions Trends sheet** — new sheet hosting the 3 cross-check rollups (Balance Drift Timelines, Two-Sided Post Mismatch, Expected-Zero EOD), an aging matrix (5 buckets × 14 check types), and per-check daily trend lines.
- **Drill-scoping fix** — new `pArAccountId` parameter + `account_id`-bound filter group on the Transactions sheet; the unified Open Exceptions table writes both `pArAccountId` and `pArActivityDate` on right-click. Two system-aggregate checks (Concentration Master Sweep Drift, GL vs Fed Master Drift) carry neither `account_id` nor `transfer_id` and are intentionally un-drillable; reader cross-checks via the per-transfer companion check.
- **E2E coverage** — new parametrized `tests/e2e/test_ar_todays_exc_drill.py` over the 12 covered check_types (filter unified table to that check_type, dispatch matching click idiom, assert post-drill Transactions row count strictly less than baseline). Two new browser helpers (`right_click_first_row_of_visual`, `click_context_menu_item`) carry the right-click + menu-pick pattern.
- **Aurora warm-up + retry** — autouse session fixture issues `SELECT 1` + `COUNT(*)` against base tables; `_retry_on_playwright_timeout` wrapper survives one cold-start window for `wait_for_visual_titles_present` / `wait_for_visuals_present`.
- **Per-app dataset scoping in deploy** — `quicksight-gen deploy account-recon` (or `payment-recon`) no longer recreates the other app's datasets. `_dataset_ids_for_apps()` derives per-app DataSetIds from each loaded analysis's `Definition.DataSetIdentifierDeclarations`; deletes/creates skip files whose ID isn't in the allowed set. Guard test `tests/test_deploy.py::TestDatasetIdsForApps`.

**AR Handbook rewrite (K.1.6)**

- **17 walkthroughs rewritten** against the new sheets — 14 per-check (sub-ledger drift, ledger drift, non-zero transfers, sub-ledger limit breach, sub-ledger overdraft, sweep target non-zero EOD, concentration master sweep drift, ACH origination settlement non-zero EOD, ACH sweep without Fed confirmation, Fed activity without internal catch-up, GL vs Fed master drift, stuck in internal transfer suspense, internal transfer suspense non-zero EOD, internal reversal uncredited) + 3 rollups (balance drift timelines, two-sided post mismatch, expected-zero EOD). Each per-check walkthrough opens with a column-mapping table for the unified schema and routes drill instructions through the actual cell hints (pale-green `account_id` tint = right-click cue; accent-coloured `transfer_id` text = left-click cue). Three checks (Fed Activity Without Internal Catch-Up, Internal Transfer Stuck in Suspense, Internal Reversal Uncredited) had handbook-card titles that diverged from the dataset literal an operator sets as a Check Type filter; walkthroughs use the dataset literal so reader filter setting matches the screenshot.
- **22 fresh screenshots** captured — 3 shared Today's Exceptions (overview / breakdown bar / unified table) + 5 Trends (drift timelines / two-sided rollup / expected-zero rollup / aging matrix / per-check daily trend) + 14 per-check filtered Open Exceptions tables. Capture script (`scripts/generate_walkthrough_screenshots.py`) extended with `mode="full_sheet"` (clip from y=0 to lowest visual on the active sheet) and `_set_check_type_filter` (handles MUI listbox virtualization + prefix-collision-safe `^…$`-anchored regex deselection).
- **Handbook + training prose updated** — `handbook/ar.md` morning routine rewritten as a three-paragraph flow naming the two new sheets and the unified table; `Training_Story.md` GL Recon persona description updated to reference the new sheets.
- **mkdocs nav switched to horizontal tabs** — `navigation.tabs` added to Material features. The five handbooks (AR / PR / Data Integration / Customization / Training) render as tabs at the top of the page; left sidebar shrinks to the current handbook's entries (17 max for AR vs ~50 across all handbooks before). Fixed one nav label drift: "Fed Activity Without Internal Post" → "Fed Activity Without Internal Catch-Up" (matches dataset literal and walkthrough H1).
- **40 orphan PNGs deleted** — old per-check `<check>-01-kpi.png` / `<check>-02-table.png` / `<check>-03-aging.png` family removed from `src/quicksight_gen/docs/walkthroughs/screenshots/ar/` after the unified-table template made them obsolete.

**Relicense (K.1.7)**

- **MIT License replaces the Unlicense.** `LICENSE` rewritten to standard MIT text (Copyright © 2026 Christopher Hotchkiss). `pyproject.toml` `license = "Unlicense"` → `license = "MIT"`. Audit clean: only two runtime deps (`click` BSD-3-Clause, `pyyaml` MIT) and all optional deps (`psycopg2-binary`, `boto3`, `pytest`, `mkdocs-material`, `playwright`) are MIT-compatible; no source files carry headers needing update.
- **Beta tag removed.** `Development Status :: 4 - Beta` → `Development Status :: 5 - Production/Stable`. The project has been on a tagged-release PyPI cadence since v3.2.0 (Phase I.6) and the API surface has stabilized — beta no longer reflects reality.

---

## v3.4.0

### Ship docs + training kit in the wheel; add export commands

The wheel now bundles the full `docs/` + `training/` trees as `package-data`, and two new CLI commands (`quicksight-gen export-docs` and `quicksight-gen export-training`) write those bundles out into a target directory. Lets a `pip install quicksight-gen` user pull the handbooks and training scenarios down to disk without cloning the repo. No behavior change for source checkouts.

---

## v3.3.0

### Customization Handbook complete

Adds the full **Customization Handbook** (`handbook/customization.md` + `walkthroughs/customization/*.md`) — eight walkthroughs covering the database mapping, dataset-SQL swap, brand reskin, AWS deploy configuration, first-deploy walkthrough, app-specific metadata key extension, canonical-value extension, and customization testing. The handbook is wired into the docs site nav and the wheel's bundled docs. Phase J close-out — no analysis or dataset changes; this release ships the docs work entirely.

---

## v3.2.2

### Refactor — schema is the interface contract, not a demo artifact

Renames `quicksight_gen/demo/schema.sql` → `quicksight_gen/schema.sql` and the helper module `quicksight_gen.demo` → `quicksight_gen.schema`. The DDL is what production ETL writes against; the "demo" namespace was a misleading hangover from when the file lived under a top-level `demo/` directory beside the seed generators. The `quicksight-gen demo schema` CLI command is unchanged. Importers should switch from `from quicksight_gen.demo import generate_schema_sql` to `from quicksight_gen.schema import generate_schema_sql`.

---

## v3.2.1

### Fix — wheel ships demo schema

The v3.2.0 wheel didn't include `demo/schema.sql`, so `quicksight-gen demo schema` (and `demo apply`) failed against an installed wheel with `Schema file not found`. Patch release moves the schema into the package as `quicksight_gen/demo/schema.sql` (declared as `package-data`) and routes both CLI sites + the `TestSchemaSql` fixtures through a new `quicksight_gen.demo.generate_schema_sql()` helper. No behavior change for source checkouts.

---

## v3.2.0

### Phase H + Phase I — Handbooks, Daily Statement, sign-convention standardization, PyPI release pipeline

This release rolls up Phase H (handbook suite + walkthrough harness) and the bulk of Phase I (Daily Statement sheet, PR/AR cross-visibility unification, PR sign-convention fix, PyPI release plumbing). Dashboards are visually unchanged from v3.0.0; the seed shifts under the sign-convention fix (re-locked SHA256), and a new per-account Daily Statement sheet is added to the AR analysis. The CLI is now `pip install quicksight-gen` from PyPI on every tagged release, with a sample `out/` bundle attached to the GitHub Release for evaluators. (Version skips 3.1.0 — that tag was created during the Phase H merge before the release pipeline existed and never produced a PyPI artifact; left untouched on its original commit for history.)

### What landed

**Handbooks + walkthroughs (Phase H)**

- **MkDocs Material site** (`docs/`) deployed to GitHub Pages — Sasquatch palette + hero, with index pages for the AR Handbook, PR Handbook, Data Integration Handbook, and ETL training suite.
- **AR Handbook** — one walkthrough per AR Exceptions check (5 baseline + 9 CMS + 3 rollups), each with screenshots, scenario walkthrough, and SQL probe queries.
- **PR Handbook** — pipeline + matching walkthroughs for every merchant-support workflow (*Where's My Money*, mismatched settlements, unmatched external txns, returns, etc.).
- **Data Integration Handbook + ETL walkthroughs** — populate-transactions, validate-account-day, prove-ETL-is-working, what-to-do-when-demo-passes-but-prod-fails, add-metadata-key, tag-force-posted, extend-with-new-transfer-type.
- **`quicksight-gen demo etl-example`** CLI — emits the worked ETL example from `Schema_v3.md` as a runnable SQL script.
- **Walkthrough screenshot generator** — Playwright e2e harness reused to capture screenshots from the live deployed demo, keeping handbook screenshots in sync with the running dashboard.
- **`docs/Schema_v3.md` expansion** — per-key WHY narrative for every metadata key, end-to-end ETL examples for piping production data into the two base tables, and a new **Sign convention** subsection reconciling bank's-bookkeeping ("+= debit") and account-holder ("= money IN") readings.

**Daily Statement sheet (Phase I.1, I.2)**

- New **AR per-account daily statement** sheet (Opening Balance / Total Debits / Total Credits / Closing Stored / Drift KPIs + transaction-detail table with counter-leg account name resolution), reachable from any sub-ledger row on the AR Balances tab via right-click drill-down.
- **Two new datasets** — `ar-daily-statement-summary` (KPI strip) and `ar-daily-statement-transactions` (detail table), both following the greenfield "no artificial filters" convention.
- **PR KPI semantics regression test** (`tests/e2e/test_pr_kpi_semantics.py`) — locks the SUM-vs-COUNT and absolute-vs-signed semantics on every PR KPI against direct-DB SQL probes.
- **AR Exceptions KPI semantic coverage tests** — pinned visual-scoped filters on five PR/AR semantically-mismatched KPIs.
- **`ParameterControl` widgets** for the Daily Statement account picker (replaces `FilterControl` — gives nullable account selection + cleaner tab-load behavior).

**Cross-visibility unification (Phase I.4)**

- **`account_id NOT LIKE 'pr-%'` filters dropped** from `ar_subledger_overdraft` and `ar_subledger_daily_outbound_by_type` — PR merchant DDA rows now surface in AR exception views by intent.
- **`WHERE transfer_type IN (...)` filter dropped** from `build_ar_transactions_dataset` — PR transfer types (`sale`, `settlement`, `payment`, `external_txn`) now surface in the AR Transactions tab.
- **`ar_transfer_net_zero` widened to all transfer types**, with `expected_net_zero` flag derived in `ar_transfer_summary` so single-leg PR types (`sale`, `external_txn`) stay out of the AR Non-Zero Transfers KPI scope semantically rather than by hiding them.
- **Docs reframe** — CLAUDE.md / SPEC.md describe AR as a unified superset; PR is a tightly persona-scoped subset view. The pre-Phase I "PR-coexistence filters" framing is retired.

**PR sign convention standardization (Phase I.5)**

- **PR sale leg flipped to credit `merchant_sub`** (was debiting), aligning with the canonical `signed_amount > 0` = money IN to the account convention. Merchant DDA balances are no longer structurally negative; the drift-check invariant `daily_balances.balance = SUM(signed_amount)` holds across every account_type.
- **`-t.signed_amount` negation pattern retired** in three PR datasets (`build_sales_dataset`, `build_settlement_exceptions_dataset`, `build_sale_settlement_mismatch_dataset`) and the matching ETL example. PR datasets read the canonical sign directly.
- **SHA256 re-locked** to `6912a28c8902223a7a552194ee368f1e83df09d6779e5c735321a83c086c1cf0`.
- **Inverse cross-visibility assertion** — `tests/e2e/test_ar_cross_visibility.py::test_no_merchant_dda_is_structurally_negative` locks the fix (no merchant_dda runs negative across the entire seed window).

**PyPI release pipeline (Phase I.6)**

- **`pip install quicksight-gen`** (or `[demo]` extra) from PyPI on every tagged release.
- **Tag-triggered release workflow** (`.github/workflows/release.yml`) — six jobs: build, smoke-test wheel, bake sample bundle, publish to TestPyPI, manual-approval gate to PyPI, GitHub Release with sdist + wheel + `out-sample.zip`. Trusted Publisher OIDC; no API tokens in the repo.
- **`scripts/bake_sample_output.py`** + **`examples/config.yaml`** — produces a 39-file, ~86 KB sample of generated QuickSight JSON evaluators can inspect without running the generator.
- **`quicksight-gen --version`** flag, dynamic version source-of-truth on `quicksight_gen.__version__`.
- **README PyPI install snippet + version badge** — leads with the consumer install path before the developer-from-source path.

**CI + tooling**

- **GitHub Actions CI** — unit + integration tests on every push, build badge in README.
- **Code coverage badge** via pytest-cov + genbadge.
- **Cross-training handbook whitelabel kit** (`training/`) — strips the Sasquatch persona for licensees who want to fork the handbook structure without the demo branding.

### Notes

- **All 398 unit/integration tests** pass; e2e suite (gated on `QS_GEN_E2E=1`) covers both apps.
- **Same dataset IDs**, same dashboard IDs — safe in-place redeploy after `cleanup --yes` for any pre-3.1 stale resources.
- **Seed change**: PR sale leg sign flip changes the seed bytes; SHA256 re-lock applied. Existing demo databases need a re-`apply` to pick up the new seed (otherwise merchant DDA balances will read the old structurally-negative shape).
- **`pip install quicksight-gen`** lights up on the first published 3.1.0 tag. Source install (`pip install -e .`) continues to work for development.

---

## v3.0.0

### Phase G — Schema flatten + PR/AR data merger

The 12-table demo schema collapses to **two base tables**: `transactions` (every money-movement leg) and `daily_balances` (per-account end-of-day snapshots). PR and AR demo data now share the same physical tables; the `pr_*` legacy table family and the AR-only `transfer` / `posting` / `ar_*_daily_balances` tables are fully retired. App-specific attributes that used to live in dedicated columns now live in a portable `metadata TEXT` JSON column. Dashboards are visually identical to v2.x — only the underlying dataset SQL changed.

### What landed

- **Two-table feed contract** — `transactions` and `daily_balances` are the entire write surface a Data Integration Team has to populate. Every dataset SQL reads from these two tables plus the AR-only dimension tables (`ar_ledger_accounts`, `ar_subledger_accounts`, `ar_ledger_transfer_limits`). Six canonical `account_type` values (`gl_control`, `dda`, `merchant_dda`, `external_counter`, `concentration_master`, `funds_pool`) discriminate which app a row belongs to.
- **PR data merged in** — PR's sales / settlements / payments / external transactions / merchants now write to `transactions` + `daily_balances` instead of `pr_sales` / `pr_settlements` / `pr_payments` / `pr_external_transactions` / `pr_merchants`. PR-specific fields (`card_brand`, `cashier`, `settlement_type`, `payment_method`, `is_returned`, `return_reason`, etc.) move into the `metadata` JSON column. All 11 PR datasets rewritten to use `JSON_VALUE(metadata, '$.<key>')`.
- **AR data merged in** — AR's `transfer` + `posting` + `ar_ledger_daily_balances` + `ar_subledger_daily_balances` collapse into the same two base tables. Per-type ledger transfer limits (one row per ledger×type×day in the old `ar_ledger_transfer_limits` snapshots) collapse into the `daily_balances.metadata` JSON so the limit-breach view stays a single SELECT. All 21 AR datasets and computed views rewritten.
- **Portable JSON convention** — `metadata TEXT` columns are constrained `IS JSON` and queried only with SQL/JSON path functions (`JSON_VALUE`, `JSON_QUERY`, `JSON_EXISTS`). No JSONB. No `->>` / `->` / `@>` / `?` operators. No GIN indexes on JSON. This is enforced both by code review and by `tests/test_demo_sql.py::TestSchemaSql::test_shared_base_layer_uses_portable_json`.
- **PostgreSQL 17+ requirement** — the SQL/JSON path functions are PG 17+. Pre-17 Postgres lacks `JSON_VALUE` / `JSON_QUERY` / `JSON_EXISTS` and the portability convention forbids the Postgres-only fallbacks. Documented in `docs/Schema_v3.md`, `README.md`, and `demo/schema.sql`.
- **`docs/Schema_v3.md`** — new feed contract document for the Data Integration Team persona: column specifications, canonical `account_type` / `transfer_type` values, the `metadata` JSON key catalog per app, and end-to-end ETL examples for piping production data into the two base tables.
- **Determinism re-locked with SHA256 hash assertion** — `tests/test_demo_data.py::TestDeterminism::test_seed_output_hash_is_locked` and the matching test in `tests/test_account_recon.py` assert the full seed SQL hashes to a known value. Any byte-level drift in the generator fails loudly.
- **Dataset contract preserved** — every dataset's `DatasetContract` (column name + type list) is unchanged from v2.x; the SQL implementation moved to the new tables but the projection is identical. This is the safety net that kept dashboards visually intact through the migration.
- **Legacy schema cleanup** — `pr_merchants`, `pr_sales`, `pr_settlements`, `pr_payments`, `pr_external_transactions`, `transfer`, `posting`, `ar_ledger_daily_balances`, `ar_subledger_daily_balances` are dropped. `DROP TABLE IF EXISTS` for each remains in `demo/schema.sql` for upgrade safety from older installations.

### Notes

- **349 unit/integration tests** (was 344), all green. Hash-lock tests added per app; new `TestSharedBaseLayer` class asserts every PR row also satisfies the AR base-layer projection contract.
- Same dataset IDs, same dashboard IDs — safe in-place redeploy after `cleanup --yes` to remove any pre-v3 stale resources.
- **Breaking change for self-hosted deployments**: pre-v3 callers that wrote directly to `pr_*` or `ar_*_daily_balances` need to migrate to `transactions` + `daily_balances`. See `docs/Schema_v3.md` for the mapping.
- **Postgres < 17 is no longer supported** for `demo apply`; production callers using a pre-existing datasource ARN are unaffected as long as that database supports SQL/JSON path syntax.
- `demo apply --all` and `deploy --all --generate` verified green end-to-end against live AWS.

---

## v2.0.0

### Phase F — AR restructure into Sasquatch National Bank Cash Management Suite

The AR demo abstraction shifts from "Farmers Exchange Bank — generic valley ledgers" to "Sasquatch National Bank — Cash Management Suite (CMS)". The same Pacific-Northwest bank from the PR side is now viewed through its treasury operations after SNB absorbed FEB's commercial book. The new account topology and four CMS-driven telling-transfer flows expose failure classes the old structure couldn't, and a new layer of cross-check rollups teaches analysts to recognize error *classes* before drilling into individual rows.

### What landed

- **CMS account topology** — eight internal GL control accounts (Cash & Due From FRB, ACH Origination Settlement, Card Acquiring Settlement, Wire Settlement Suspense, Internal Transfer Suspense, Cash Concentration Master, Internal Suspense / Reconciliation, Customer Deposits — DDA Control) sit above seven customer DDAs (three coffee retailers shared with PR plus four commercial customers — Cascade Timber Mill, Pinecrest Vineyards, Big Meadow Dairy, Harvest Moon Bakery).
- **Four CMS telling-transfer flows** — ZBA / Cash Concentration sweeps, daily ACH origination sweeps to the FRB Master Account, external force-posted card settlements, and on-us internal transfers through Internal Transfer Suspense. Each plants both success cycles and characteristic failures.
- **9 new CMS-specific exception checks** — sweep-target-nonzero, concentration-master-sweep-drift, ach-orig-settlement-nonzero, ach-sweep-no-fed-confirmation, fed-card-no-internal-catchup, gl-vs-fed-master-drift, internal-transfer-stuck, internal-transfer-suspense-nonzero, internal-reversal-uncredited. Each is a dedicated dataset + KPI + detail table + aging bar following the established Phase D visual pattern.
- **3 cross-check rollups** at the top of the Exceptions tab — expected-zero EOD rollup, two-sided post-mismatch rollup, and balance drift timelines rollup — teaching error-class recognition before per-check drill-down.
- **AR dataset count** — 9 → 21 (9 baseline + 9 CMS checks + 3 rollups). Exceptions tab visual count: 17 → 47.
- **AR theme rename** — `farmers-exchange-bank` preset renamed to `sasquatch-bank-ar`. Palette unchanged (valley green + harvest gold + earth tones); the AR dashboard still reads visually distinct from PR (forest green + bank gold) so users can tell the merchant and treasury views of the same bank apart at a glance.
- **AR Getting Started rewrite** — the demo flavor block now describes the SNB / CMS structure: 8 GL control accounts, 7 customer DDAs, four telling-transfer flows, and the cross-check rollups.
- **`CategoricalMeasureField` DATETIME fix** — added `_measure_date_count` helper for `DateMeasureField(COUNT)`; switched four CMS-check KPIs and two aging-bar callers off `balance_date` to ledger-account grouping (`CategoricalMeasureField` rejects DATETIME columns).

### Notes

- **344 unit/integration tests** (was 254), **101 e2e tests** (was 75), all green.
- Theme rename is backwards-incompatible: existing config files using `theme_preset: farmers-exchange-bank` must be updated to `sasquatch-bank-ar` before redeploy.
- Dataset IDs added; no existing dataset IDs renamed. Safe in-place redeploy after `cleanup --yes` to remove the dropped `qs-gen-ar-*` resources.
- `demo apply --all` and `deploy --all --generate` verified against live AWS.

---

## v1.5.0

### Phase D — Aging buckets, origin wiring, and shared visual pattern

Every exception check across both apps now carries aging information (how long the exception has been outstanding) and follows a consistent visual pattern: KPI count + detail table + horizontal aging bar chart. The `origin` attribute (deferred since Phase A) is wired into AR filters and exception detail.

### What landed

- **Aging buckets** — 5 hardcoded bands (`0-1 day`, `2-3 days`, `4-7 days`, `8-30 days`, `>30 days`) with numeric-prefixed labels for correct QuickSight sort order. `days_outstanding` (INTEGER) + `aging_bucket` (STRING) added to all 11 exception dataset contracts and SQL queries across both apps plus the Payment Recon dataset.
- **AR exception aging** — 5 aging bar charts added to the Exceptions tab (ledger drift, sub-ledger drift, non-zero transfers, limit breach, overdraft). Detail tables gain `aging_bucket` column. Exceptions tab: 12 → 17 visuals.
- **PR exception aging** — 5 aging bar charts added to the Exceptions & Alerts tab. Payment returns gains `days_outstanding` (previously missing). Sale-settlement and settlement-payment mismatch tables gain `days_outstanding` column in the visual. Exceptions tab: 7 → 12 visuals.
- **PR Payment Recon aging** — aging bar chart on the Payment Reconciliation tab. Tab: 6 → 7 visuals.
- **Origin filter** — multi-select on Transactions + Exceptions tabs. `origin` column added to non-zero-transfer and transfer-summary dataset contracts and SQL.
- **Shared `aging_bar_visual()`** — extracted to `common/aging.py`, used by all 11 aging bar charts across both apps.
- **Visual consistency** — all exception detail tables now consistently show `days_outstanding` + `aging_bucket`.

### Deferred

- **PR exception drill-downs (D.7)** — adding drill-down actions to PR exception tables requires new parameters and filter groups; deferred to Phase E which will rework the tab structure.
- **ReconciliationCheck abstraction (D.5)** — the aging bar helper was extracted; the full check abstraction doesn't cleanly cover all shapes (left≠right, row-matches-condition, unpaired). Per-check implementations are already consistent.

### Notes

- **310 unit/integration tests**, all green.
- No dataset ID changes from v1.4.0; safe in-place redeploy.

---

## v1.4.0

### Phase C — Ledger-level direct postings

Ledger accounts can now receive postings directly, not just aggregate sub-ledger balances. The drift invariant changes from 2-input (`stored ledger balance vs Σ sub-ledger balances`) to 3-input (`stored ledger balance vs Σ direct ledger postings + Σ sub-ledger stored balances`), catching discrepancies that were previously invisible.

### What landed

- **Schema changes** — `posting.ledger_account_id NOT NULL` (every posting knows its ledger); `posting.subledger_account_id` now nullable (NULL for ledger-level postings). Three new transfer types: `funding_batch`, `fee`, `clearing_sweep`.
- **Ledger-level demo scenarios** — 5 funding batches (1 ledger credit + N sub-ledger debits, net zero), 3 fee assessments (single ledger debit, intentionally non-zero — test data for exceptions), 2 clearing sweeps (2 ledger postings, net zero). Daily balance computation updated to incorporate direct postings.
- **3-input drift formula** — `ar_computed_ledger_daily_balance` view rewritten with subqueries: sub-ledger stored balance total + direct ledger posting total. Sub-ledger drift is unchanged.
- **Transactions dataset expanded** — `posting_level` column (`'Ledger'` / `'Sub-Ledger'`) added to contract and SQL. JOIN on `posting.ledger_account_id`, LEFT JOIN on sub-ledger. `COALESCE(subledger_name, ledger_name)` for display.
- **Posting Level filter** — multi-select dropdown on Transactions tab lets users isolate ledger-level vs sub-ledger activity.
- **AR type filter expanded** — `WHERE transfer_type IN ('ach', 'wire', 'internal', 'cash', 'funding_batch', 'fee', 'clearing_sweep')` across all AR views and datasets.
- **9 scenario coverage tests** — `TestLedgerPostingScenarios` in `test_account_recon.py` verifying counts, NULL subledger, ledger FK, funding net-zero, fee non-zero, sweep net-zero, mixed-level funding.
- **PR/AR scope isolation verified** — zero transfer type overlap between apps; `pr-merchant-ledger` absent from `ar_ledger_daily_balances`.

### Notes

- **310 unit/integration tests** (was 301), all green.
- `demo apply --all` and `deploy --all --generate` verified against live AWS. Both analyses `CREATION_SUCCESSFUL`. `cleanup --dry-run` shows no stale resources.
- No dataset ID changes from v1.3.0; safe in-place redeploy.

---

## v1.3.0

### Phase B — Unified transfer schema + dataset column contracts

Both apps now share a common `transfer` + `posting` schema. AR datasets read exclusively from the unified tables; PR emits to them via dual-write (PR datasets still read legacy `pr_*` tables for domain-specific metadata). Every dataset declares an explicit column contract so the SQL is one implementation of a stable interface.

### What landed

- **Unified schema** — `transfer` and `posting` tables added to `demo/schema.sql`. `transfer` carries `transfer_id`, `parent_transfer_id` (self-ref for chains), `transfer_type`, `origin`, `amount`, `status`, `created_at`, `memo`, `external_system`. `posting` carries `posting_id`, `transfer_id` FK, `subledger_account_id` FK, `signed_amount`, `posted_at`, `status`.
- **AR fully migrated** — all 9 AR dataset SQL queries rewritten to project from `posting` + `transfer`. Legacy `ar_transactions` table dropped; AR views (`ar_transfer_summary`, `ar_subledger_daily_outbound_by_type`, etc.) rewritten to join `posting` + `transfer`. AR demo generator no longer emits `ar_transactions` INSERTs.
- **PR dual-write** — PR demo generator emits the full transfer chain (`external_txn → payment → settlement → sale`) linked by `parent_transfer_id`, with postings on PR-specific sub-ledger accounts (`pr-sub-{merchant}`, `pr-external-customer-pool`, `pr-external-rail`). Legacy `pr_*` tables still populated and read by PR datasets.
- **Dataset column contracts** — `DatasetContract` dataclass in `common/dataset_contract.py` with `ColumnSpec(name, type)`. All 20 dataset builders declare contracts; unit tests assert SQL projections match declared contracts.
- **Cross-app integrity tests** — posting FK integrity across apps, no ID collisions, transfer type enum coverage (all 8 CHECK values present in combined data).
- **Schema DDL ordering fix** — `transfer` + `posting` tables now created before AR views that reference them.

### Deferred

- **PR dataset cutover (B.6)** — PR datasets need domain-specific metadata (`card_brand`, `cashier`, `settlement_type`, `payment_method`) that lives on legacy `pr_*` tables. Cutover deferred until the customer decides which PR columns they actually need; at that point, extract metadata into slim tables and rewrite PR datasets to join `transfer`/`posting` with metadata.

### Notes

- **301 unit/integration tests** (was 255), **94 e2e tests** — all green.
- `demo apply --all` and `deploy --all --generate` verified against live AWS. `cleanup --dry-run` shows no stale resources.
- No dataset ID changes; safe in-place redeploy after `cleanup --yes` from v1.2.0.

---

## v1.2.0

### Phase A — Account Recon vocabulary rename + `origin` attribute

Account Reconciliation's internal vocabulary ("parent / child accounts") always read a little structural; the classical accounting pattern is **control account + subsidiary ledger**, and end users are accountants who already think in GL vocabulary. v1.2.0 aligns the code, SQL, QuickSight labels, and docs with that language, and plants an additive `origin` column on transactions for the later phases in the major evolution to consume.

### What landed

- **Vocabulary rename across AR** — user-visible across every AR tab:
  - Tables/views: `ar_accounts` → `ar_subledger_accounts`; drift/breach/overdraft views reshaped to `ar_subledger_*` / `ar_ledger_*`.
  - Columns: `account_id` → `subledger_account_id`, `parent_account_id` → `ledger_account_id` (cascades through every SELECT projection and dataset contract).
  - QuickSight labels: "Parent/Child Account" → "Ledger/Sub-Ledger Account" on every table, KPI, filter, drill-down, and Show-Only-X toggle.
  - Dataset IDs renamed from `qs-gen-ar-parent-*` / `qs-gen-ar-account-*` → `qs-gen-ar-ledger-*` / `qs-gen-ar-subledger-*`. **One-time cleanup required**: old tagged resources in the target account need `quicksight-gen cleanup --yes` after the v1.2.0 deploy, since dataset IDs are rename-as-delete-plus-create.
  - Drill-down parameters: `pArAccountId` → `pArSubledgerAccountId`, `pArParentAccountId` → `pArLedgerAccountId`.
- **`origin` attribute on transactions** — additive, tag-only in v1.2.0:
  - `ar_transactions.origin VARCHAR(30) NOT NULL DEFAULT 'internal_initiated' CHECK IN ('internal_initiated', 'external_force_posted')`.
  - Demo generator sprinkles ~10% `external_force_posted` (every 10th emitted leg) for deterministic coverage.
  - Surfaced as a visible column on Transaction Detail. **No filter, exception check, or drill consumes it yet** — Phase B/D will wire it in.

### Notes

- **255 unit/integration tests** (was 253) — added one scenario-coverage assertion for origin values and one dataset-contract assertion for the `origin` column. E2E verified against a live deploy with `./run_e2e.sh --parallel 4`.
- No behavioral changes in AR reconciliation logic — only vocabulary and one new column.
- Payment Recon is untouched: zero references to parent/child existed there.
- Phase B (unified transfer schema + column contract) will reshape PR's sales/settlements/payments into the same `transfer` primitives AR already uses. See `SPEC.md` "Suggested phasing".

---

## v1.1.0

### Filter-propagation browser e2e expansion

The browser e2e suite previously spot-checked a single date-range filter on one table per app. Every other filter was trusted to work if the dashboard JSON referenced it. v1.1.0 closes that gap on the Payment Recon side, captures one documented QuickSight limitation, and parallelizes the suite so the wider coverage fits the runtime budget.

### What landed

- **Payment Recon filter-propagation coverage** (Phases 1–2):
  - Shared filter-interaction helpers in `tests/e2e/browser_helpers.py` — `set_dropdown_value`, `set_multi_select_values`, `clear_dropdown`, `set_date_range`, `count_table_rows` / `count_table_total_rows` (pagination-aware), `count_chart_categories` (canvas-aware via aria-label + legend fallback), `read_kpi_value` / `wait_for_kpi_value_to_change`, plus `wait_for_*_to_change` pollers for each.
  - Split the shared `fg-date-range` filter group into four per-sheet groups (`fg-{sales,settlements,payments,exceptions}-date-range`), each scoped to its sheet's native timestamp column. The old `CrossDataset="ALL_DATASETS"` control rendered but was inert on sheets whose dataset didn't have a `sale_timestamp` column.
  - New parametrized tests for future-window, past-window, and in-window date filtering on Sales / Settlements / Payments.
- **Documented QS navigation filter-stacking** (Phase 5): drill-down-set parameters persist across tab-switches (`A → B → A` leaves B-derived filter on A). QuickSight has no API to clear a parameter on nav. Captured as `xfail(strict=False)` in `tests/e2e/test_filter_stacking.py`, documented under "Known limitations" in README, and called out on both Getting Started sheets (accent-colored bullet).
- **Parallelized e2e suite** (Phase 6): added `pytest-xdist`, default `-n 4` in `run_e2e.sh`, `--parallel N` override. Full 101-item suite drops from ~305s serial to ~133s at `-n 4` and ~81s at `-n 8`; `-n 12` flakes (timing-sensitive date-range narrowing).
- **Dedup pass** (Phase 1.8): five DOM-probe helpers (`selected_sheet_name`, `wait_for_sheet_tab`, `first_table_cell_text`, `wait_for_table_cells_present`, `click_first_row_of_visual`) plus `sheet_control_titles` / `wait_for_sheet_controls_present` / `wait_for_visual_titles_present` extracted from per-file copies into `browser_helpers.py`.

### Known gap

Account Recon filter-propagation coverage was deferred ahead of a major spec revision that will refactor AR heavily. Existing AR e2e still covers rendering, drill-downs, and Show-Only-X toggles; filter-propagation parity with PR will return after the revision lands.

### Notes

- **253 unit/integration tests**, **101 e2e tests** (94 passed / 6 skipped / 1 xfailed) — all green.
- No schema, dataset, or generated-resource ID changes beyond the internal split of `fg-date-range` into four per-sheet filter groups. Safe in-place redeploy.
- `run_e2e.sh --parallel 8` is the recommended stable ceiling on a modern Mac; `--parallel 1` forces serial.

---

## v1.0.1

### Post-release polish

Two small UX fixes from first round of v1.0.0 testing:

- **Payment Reconciliation tab — table order swapped.** Internal Payments now renders on the left, External Transactions on the right. Reading flow goes internal → external, matching the rest of the pipeline (sales → settlements → payments → external).
- **Account Recon Transfers tab — duplicate filter removed.** The "Show Only Unhealthy" SINGLE_SELECT toggle was redundant with the "Transfer Status" multi-select (both filtered on `net_zero_status`). Dropped the toggle; the multi-select stays.

### Notes

- Tests: 253 unit/integration (was 254 — one toggle assertion folded into a no-toggle assertion), 75 e2e — all green.
- No schema, dataset, or generated-resource ID changes; safe in-place redeploy.

---

## v1.0.0

### Spec complete — dual-dashboard restructure delivered

v1.0.0 ships the full spec: two independent QuickSight apps (Payment Reconciliation + Account Reconciliation) generated from Python, deployed via boto3, tested at four layers (unit, integration, API e2e, browser e2e). Both apps share one theme, account, datasource, and CLI surface, yet are selectable individually for fast iteration (`--all` exercises both, `payment-recon` / `account-recon` targets one).

### What landed since v0.5.0

- **Account Recon Phase 4** (v0.6.0): multi-select filters per tab (parent/child account, transfer status, transaction status); Show-Only-X SINGLE_SELECT toggles (unhealthy transfers, failed transactions, drift); left-click and right-click drill-downs covering all six user-research flows; Parent Drift Timeline alongside the existing Child Drift Timeline; same-sheet chart filtering on every new chart.
- **Account Recon Phase 5** (v0.7.0): per-type daily transfer limits (ACH / wire / internal / cash) enforced against parent limits fed upstream, plus child overdraft detection. Exceptions tab grew from 3 independent checks to 5 (parent drift, child drift, non-zero transfers, limit breaches, child overdrafts) laid out as paired half-width tables + two drift timelines for maximum density.
- **Account Recon browser e2e** (v0.8.0): 16 Playwright tests mirror PR's coverage — dashboard load, per-sheet visual counts, drill-downs (Balances→Txn, Transfers→Txn, Exceptions Breach→Txn), date-range filter narrowing, all five Show-Only-X toggles. Right-click `DATA_POINT_MENU` drill is covered structurally (Playwright menu-select is flaky). Screenshots namespaced per app under `tests/e2e/screenshots/{payment_recon,account_recon}/`.
- **Rich-text Getting Started sheets** (v1.0.0, Phase 6): both apps' landing tabs use proper typography — 36px welcome, 32px section headings, 20px subheadings, accent-colored links, bulleted per-sheet summaries — via a new `common/rich_text.py` XML composition helper. Theme accent resolves to hex at generate time (QuickSight text parser doesn't accept theme tokens).
- **Docs refresh** (v1.0.0, Phase 7): README rewritten for the two-app structure; CLAUDE.md updated for the `common/` + per-app module layout; SPEC.md swept — delivered checkboxes flipped, open questions collapsed into a Decisions section.

### Stats

- **~16,030 lines of Python** (10,570 in `src/`, 5,460 in `tests/`) + 485 lines of schema DDL.
- **254 unit / integration tests**, **75 e2e tests** (329 total), **436 assert statements**.
- **2 apps** (6 + 5 = 11 sheets), **20 datasets** (11 PR + 9 AR), **3 theme presets**, **1 shared datasource**.

### Notes

- The e2e suite is gated on `QS_GEN_E2E=1` and requires AWS credentials; `pytest` alone runs the 329 fast tests with no AWS dependency.
- Dataset Direct Query (no SPICE) — seed changes show up immediately after `demo apply`, no refresh step needed.
- `cleanup --dry-run` / `cleanup --yes` sweeps stale `ManagedBy: quicksight-gen` resources not in current `out/`.

---

## v0.5.0

### Account Reconciliation — second app

Phase 3 adds a second QuickSight app, Account Reconciliation, alongside the existing Payment Reconciliation dashboard. The AR dashboard covers a bank's double-entry ledger with two independent stored-balance feeds (parent-level and child-level) and reconciles both against the underlying transactions.

### New app

- **Account Reconciliation dashboard** — 5 tabs (Getting Started + Balances, Transfers, Transactions, Exceptions). Shared date-range filter; drill-downs and multi-select filters land in Phase 4.
- **Two independent drift checks** exposed side-by-side on the Exceptions tab:
  - Parent drift — stored parent balance vs Σ of its children's stored balances (points at the parent-balance upstream feed).
  - Child drift — stored child balance vs running Σ of posted transactions (points at the child-balance feed or a ledger miss).
- **Transfer reconciliation** — transfers are not a table; they're a `transfer_id` grouping of `ar_transactions`. `ar_transfer_summary` surfaces net-zero status and a representative memo per transfer. The Exceptions tab flags transfers whose non-failed legs don't sum to zero (failed counter-leg, keying error, fee drift).
- **`farmers-exchange-bank` theme preset** — earth tones, valley greens, harvest gold. Applies the "Demo — " analysis name prefix when selected.
- **Farmers Exchange Bank demo data** — 5 parent accounts (Big Meadow Checking, Harvest Moon Savings, Orchard Lending Pool, Valley Grain Co-op, Harvest Credit Exchange) moving money between 10 child accounts over ~40 days. Planted: 3 parent-day drifts, 4 child-day drifts (disjoint from parent cells), 4 failed-leg transfers, 4 off-amount transfers, 4 fully-failed transfers.
- **CLI — two-app aware** — `generate account-recon`, `demo schema|seed|apply account-recon`, `deploy account-recon`, and `--all` exercises both apps.

### Scope clarification (SPEC)

"Internal" vs "external" describes **this application's reconciliation scope**, not system ownership. All accounts (internal + external, parent + child) appear in the same tables; external-scope accounts are present but not reconciled (that's regulators' job). Parent-level and child-level stored balances may be fed by different upstream systems, which is why the two drift checks are independent.

### Resources

- Dashboard: `qs-gen-account-recon-dashboard`
- Analysis: `qs-gen-account-recon-analysis`
- 7 AR datasets: parent_accounts, accounts, transactions, parent_balance_drift, account_balance_drift, transfer_summary, non_zero_transfers
- 5 AR tables (`ar_parent_accounts`, `ar_accounts`, `ar_parent_daily_balances`, `ar_account_daily_balances`, `ar_transactions`) + 6 views (`ar_computed_account_daily_balance`, `ar_account_balance_drift`, `ar_computed_parent_daily_balance`, `ar_parent_balance_drift`, `ar_transfer_net_zero`, `ar_transfer_summary`)

### Notes

- AR browser e2e tests and cross-sheet drill-downs deferred to Phase 5.
- Phase 3 review caught a scope gap — child balances were not reconciled in the initial skeleton. Resolved in Phase 3.10 with an independent `ar_account_daily_balances` feed and a second drift view.

---

## v0.4.0

### Payment Reconciliation domain additions

Phase 2 bundles refunds, optional sales metadata, payment-method filtering, an expanded Exceptions tab, a Getting Started landing sheet, right-click drill-downs, and state-toggle filters into a richer Payment Reconciliation experience. Dashboard goes from 5 tabs to 6.

### New features

- **Refund support** — `sale_type` column on `pr_sales` with negative amounts; refund rows flow into settlements so signed sums net correctly.
- **Optional sales metadata** — taxes / tips / discount_percentage / cashier declared in `OPTIONAL_SALE_METADATA`. Each column auto-generates a typed filter control on Sales Overview (numeric → slider, string → multi-select).
- **Payment method filter** — multi-select dropdown scoped to Settlements + Payments tabs.
- **Expanded Exceptions & Alerts** — three new mismatch tables (sale↔settlement, settlement↔payment, unmatched external transactions) alongside the existing unsettled-sales and returned-payments tables.
- **Getting Started landing sheet** — now tab index 0, with one plain-text block per downstream sheet plus a demo-scenario flavor block when `--theme-preset sasquatch-bank` is active. Rich text / hyperlink formatting deferred to Phase 6.
- **Right-click drill-downs** — Sales `settlement_id` → Settlements, Payments `external_transaction_id` → Payment Reconciliation. Source cells styled with a pale tint to cue the menu. Plain left-click drills keep their accent-only styling for a visual distinction between the two click idioms.
- **Payment Reconciliation side-by-side tables** — External Transactions and Internal Payments render half-width rather than stacked; mutual click-filter still works.
- **State toggles (Show-Only-X)** — SINGLE_SELECT dropdowns on Sales ("Show Only Unsettled"), Settlements ("Show Only Unpaid"), Payments ("Show Only Unmatched Externally"). These replace the per-tab days-outstanding slider, which turned out to overlap with the existing date-range filter.
- **Orphan external transactions in demo data** — the generator now always emits ~13 ext txns with no internal payment link plus ~4 unmatched payments, so Payments toggle, Exceptions table, and Payment Reconciliation all have data out-of-the-box.

### Changed

- Dashboard sheet count: 5 → 6 (Getting Started added at index 0).
- Filter group count: raised from ~11 to 18+ (optional-metadata filters, state toggles, drill-down parameter filters, recon filters).
- Exceptions & Alerts visual count: 4 → 7.
- Demo data: refund rows added to sales; external transactions restructured to guarantee unmatched coverage.

### Removed

- **Days-outstanding slider** — removed from every tab. The date-range filter already covered the workflow and the slider duplicated intent. Replaced by Show-Only-X toggles on the three pipeline tabs.

### Notes

- Right-click menus rely on `DATA_POINT_MENU` trigger — only one left-click action per visual is allowed, so the menu trigger is how additional click targets surface without conflicting with charts' drill-down behavior.
- Every sheet still has a plain-language description; every visual still has a subtitle. Coverage asserted in unit and API e2e tests.

---

## v0.3.0

### End-to-end test harness

A two-layer e2e harness validates a deployed dashboard, complementing the existing unit suite. Tests are skipped by default unless `QS_GEN_E2E=1` is set, so a plain `pytest` run stays AWS-free.

**API layer (boto3, ~13s):** dashboard / analysis / theme / dataset existence and status, dashboard structure (sheets, visual counts, parameters, filter groups, dataset declarations), dataset import mode and key columns.

**Browser layer (Playwright WebKit headless, ~60s):** dashboard loads via a pre-authenticated embed URL, all 5 sheet tabs render, per-sheet visual counts in the actual DOM, Settlements→Sales and Payments→Settlements drill-down navigation, Payment Reconciliation mutual table filtering (external transaction click filters payments table), and date-range filter behavior (future date range empties Sales Detail).

### One-shot runner

`./run_e2e.sh` regenerates JSON, runs `deploy.sh`, then `pytest tests/e2e` so iteration is hands-off:

```bash
./run_e2e.sh                       # full cycle
./run_e2e.sh --skip-deploy api     # skip generate+deploy, API only
./run_e2e.sh --skip-deploy browser # skip generate+deploy, browser only
```

### New features

- 33 e2e tests across 8 test files under `tests/e2e/`
- Tunable timeouts via `QS_E2E_PAGE_TIMEOUT`, `QS_E2E_VISUAL_TIMEOUT` env vars (defaults 30s / 10s)
- Failure screenshots saved to `tests/e2e/screenshots/` (gitignored)
- New `e2e` optional dependency group: `pip install -e ".[e2e]"` then `playwright install webkit`

### Notes

- Embed URL must be generated against the **dashboard region**, not the QuickSight identity region (us-east-1). Embed URLs are **single-use** so fixtures are function-scoped.
- The conftest looks for config at `config.yaml` then `run/config.yaml` then env vars.

---

## v0.2.0

### Consolidated single-analysis architecture

The separate reconciliation analysis has been merged into the financial analysis as the **Payment Reconciliation** tab. The project now generates one analysis and one dashboard (down from two of each), reducing deployment complexity and enabling cross-sheet drill-down without URL-based linking.

### Payment-only reconciliation

Reconciliation now correctly focuses on payments -- the only records that leave the internal system. Sales and settlements no longer have external transaction IDs or recon views. This eliminated 3 datasets, 2 database views, and the `late_thresholds` table.

### New features

- **Payment Reconciliation tab** with 3 KPIs (matched amount, unmatched amount, late count), a stacked bar chart (match status by external system), and dual mutually-filterable tables (external transactions and internal payments)
- **Mutual table filtering** -- click an external transaction to see its linked payments; click a payment to filter back to its transaction
- **Config-driven late threshold** (`late_threshold_days`, default 30) replaces the database table. Users can also adjust interactively via the days-outstanding slider
- **Same-sheet chart filtering** on all tabs -- clicking a bar or pie slice filters the detail table on the same sheet
- **Cross-sheet drill-down** -- click a settlement row to jump to Sales filtered by that settlement; click a payment row to jump to Settlements

### Breaking changes

- `recon-analysis.json` and `recon-dashboard.json` are no longer generated. Delete them from AWS before deploying (`./deploy.sh --delete`)
- Dataset count reduced from 11 to 8. The removed datasets: `qs-gen-sales-recon-dataset`, `qs-gen-settlement-recon-dataset`, `qs-gen-recon-exceptions-dataset`
- `external_transaction_id` removed from sales and settlements datasets/schema
- `transaction_type` removed from external_transactions dataset/schema
- `late_thresholds` table removed from demo schema
- `build_recon_analysis()` and `build_recon_dashboard()` no longer exist

### Bug fixes

- Fixed `DefaultFilterControlConfiguration` rejection by using `SINGLE_DATASET` scope with direct filter controls for single-sheet filters
- Fixed `SetParametersOperation` requiring a preceding `NavigationOperation`
- Fixed QuickSight rejecting multiple `DATA_POINT_CLICK` actions on a single visual

---

## v0.1.0

Initial release. Financial analysis with 4 tabs (Sales, Settlements, Payments, Exceptions), reconciliation analysis with 4 tabs, demo data system, theme presets, and deploy script.
