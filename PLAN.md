# QuickSight Generator — Active Plan

**Where we are.** Phase W shipped (v8.6.0); the v8.3.x → v8.6.x cumulative bug sweep settled at v8.6.13. We're out of the heat of phase-driven development — new work runs as targeted minor features + bug sweeps in the **Sustainment & minor features** queue below. Historical detail for every phase prior to v8.0.0 lives in `PLAN_ARCHIVE.md` and `RELEASE_NOTES.md`. This file tracks **forward-looking** work only.

## Greater Plan
X.2 - add the non quicksight renderer
  - Solves testing limitations
X.3 - add sqlite as a database dialect
  - Does not support materialized views but shouldn’t matter due to the local nature of the db
X.4 - yaml editor - How do you handle the yaml? Aka the shape of the institution?
X.5 - etl helper
X.6 - Stop the documentation lying.
- [ ] Make the core domain model the source of the documentation site just as the yaml for the shape today. No duplication, use it to build the doc from the models.
X.7 Cloud cost optimization


---

## Phase history (one-line per shipped phase)

- **Phase N** (v6.1.0) — Investigation + Executives ported onto L1/L2 tree primitives; theme moved to L2 YAML attribute; preset registry dropped. Full detail: `PLAN_ARCHIVE.md`.
- **Phase O** (v6.2.0) — Unified docs render pipeline with mkdocs-macros + `HandbookVocabulary`; per-app handbooks render against any L2 instance.
- **Phase P** (v7.x cumulative) — Dialect-aware schema + dataset emission; Postgres + Oracle CI matrix; Phase R seed pipeline foundations.
- **Phase Q.1** — Dashboard polish: USD currency formatting via `Measure(currency=True)`, universal date-filter sweep, Oracle case-fold wrapper for `ORA-00904`.
- **Phase Q.2** — Doc IA cleanup; Shape C audience-first home; persona-leak sweep across handbook + walkthroughs.
- **Phase Q.4** (v7.3.0) — Persona-neutral docs release; new `persona:` block on L2 YAML; CI gate for persona-token leakage.
- **Phase Q.5** — Persona-neutral docs full L2-driven substitution; Investigation walkthroughs split into mechanics + worked-example admonitions.
- **Phase Q.3.a** (v8.0.0) — CLI redesign: four artifact groups (`schema | data | json | docs`); each `apply`/`clean` defaults to emit, `--execute` opts in to side effects; `cli_legacy.py` deleted; bundled JSON emit (no per-app filter).
- **Phase R** (v7.2.0) — 90-day per-Rail healthy baseline + embedded plant overlays (densify×5, broken×15, inv-fanout×5); Volume Anomalies signal real on the seed; lognormal amount distribution.
- **Phase S** — Research: drop the system `dot` binary. Spiked Mermaid+ELK (failed eyeball — self-loops floated, layout fidelity poor) and graphviz WASM via `@hpcc-js/wasm-graphviz` (passed — byte-identical to graphviz/dot). Verdict written into RELEASE_NOTES + git log; Phase T executed the migration.
- **Phase T** (v8.1.0) — Every diagram now renders client-side via `@hpcc-js/wasm-graphviz`. `render_*` helpers return DOT strings; `<template class="qs-graphviz-source">` blocks inside `<figure>` wrappers; ~50-line JS shim does the WASM render. 5 `apt-get install graphviz` lines deleted across CI / Release / Pages workflows.
- **Phase U** (v8.2.0) — Audit Reconciliation Report. Fifth artifact group (`audit`) ships `apply` / `clean` / `verify` / `test` verbs that emit a regulator-ready PDF (cover + exec summary + per-invariant violation tables + per-account Daily Statement walks + sign-off + provenance appendix) bound by a four-input SHA256 fingerprint (`tx hwm + bal hwm + l2 yaml + code identity`); optional pyHanko auto-sign. Release-gate U.8.b's three-way contract (`expected == PDF == dashboard`) verified live across **6 invariants × 2 dialects = 12/12 PASS**. Closed the L1 dashboard's stuck/supersession `[today-7, today]` date scope that hid current-state matview rows from the dashboard's view.
- **Phase V** (v8.3.0) — Cleanup + tooling: `config.yaml` ↔ L2 institution YAML strict split (env-only allowlist on `load_config`, hand-built `Config(...)` literals collapsed to `make_test_config(**overrides)`); `docs apply --portable` (offline static-site builds with inlined wasm-graphviz + no fetched fonts — ship-on-USB-stick workflow); App Info sheet enhancements (`__version__` deploy stamp, per-matview `latest_date` + base-table comparison rows, ETL stale-matview troubleshooting page); R.6.e baseline tune-up (limit_breach noise drop on customer outbound; intermediate-clearing overdraft credit cascades on aggregating-rail / TT-leg / MerchantPayout / ZBA patterns); reference-nav regroup (App handbooks / Data contract / Operations); pip → uv migration with `uv.lock` committed.
- **Phase W** (v8.6.0) — Browser e2e in GitHub Actions. Three e2e jobs (`e2e-pg-api` push:main, `e2e-pg-browser` nightly cron, `e2e-oracle-api` push:main) against operator-owned Aurora/Oracle via OIDC role assumption + dedicated `ci-bot` QS user; per-run resource isolation via `qs-ci-${run_id}-{pg|oracle}` prefix; workflow-level always-cleanup (`cleanup-pg` / `cleanup-oracle`); `e2e-against-testpypi` release-pipeline gate holds prod publish on a live AWS run against the just-published TestPyPI wheel; per-job `pg_stat_statements` / `v$sqlstats` top-queries dump as a markdown artifact; unified Hynek-pattern coverage report posted to GHA Step Summary + republished to the `badges` branch (no Codecov); `docs-portable-install` CI regression guard. Docs now ship inside the wheel so `quicksight-gen docs apply` works from a plain PyPI install.
- **v8.3.x → v8.6.x cumulative** — Post-Phase-V/W bug sweeps + small features (no phase number — graduated as a sustained release stream): independent-system bug sweep + per-prefix cleanup isolation (v8.4.0); plain-English column headers + BarChart axis labels (v8.5.0 / v8.5.5 / v8.6.1); cross-sheet drill date widening (v8.5.7); L2FT metadata cascade write-back (v8.6.5); Oracle 19c JSON_VALUE functional-index skip (v8.6.6); L2FT Transfer Templates SingleLegRail plants + dropdown perf indexes (v8.6.7 / v8.6.8); rich-text card padding 12px (v8.6.9); L2 theme CSS injection on docs site + relative logo/favicon paths (v8.6.10); `tagging_enabled` config override for IAM-restricted environments (v8.6.11); coverage uplift to ~82% via 33 new unit tests (v8.6.12); `json clean --all` purge mode for full-deploy teardown (v8.6.13).

_Phase S / T / U / V / W sub-task detail removed during post-phase cleanup. RELEASE_NOTES `v8.{1,2,3,4,5,6}.x` carry the per-phase + per-release narratives._

---

## Phase X — e2e testing expansion + cloud CI cost optimization

### Parallelism map

Phase X has tracks that genuinely don't touch each other. Identifying them up front so they can be farmed to isolated agent worktrees (or to humans on side branches) without redundant merge pain.

**Hard sequential — finish before fanning out:**
- **X.2.a → X.2.b → X.2.f** — architecture cleanup → REST surface → real data fetcher. These set the foundation; every parallel track that touches App 2 depends on them. Lock these first, on a single branch, with a single owner.
- **X.2.h, X.2.j, X.2.k** — depend on the body of X.2 being done.

**Parallel candidates, in descending isolation:**

1. **X.3 (SQLite dialect) — strongest parallel candidate.** Touches `common/sql/`, `common/l2/schema.py`, new locked seed file. Zero overlap with X.2 HTMX work. Can run in a worktree-isolated agent end-to-end through X.3.f. Skip X.3.g (CI cells) until X.2.h lands so the matrix cells line up. **Integration risk:** the locked seed file at `tests/data/_locked_seeds/spec_example.sqlite.sql` will need a re-lock if the seed pipeline shifts during X.2 — small risk, easy to detect via `data lock --check`.

2. **X.2.g per-app builds** (Executives / Investigation / L2FT / L1) once X.2.c (renderers) + X.2.f (data fetcher) land. Each app is its own `apps/<app>/` world. **Integration risk:** the shared renderer (`common/html/render.py`) is the contention surface — one app discovers it needs a feature the others haven't asked for, the renderer changes mid-flight. **Mitigation:** sequence X.2.c fully BEFORE fanning the app builds, AND have a single owner gate-keep `render.py` edits during X.2.g. App tracks PR their `apps/<app>/` changes; renderer changes go through the gate-keeper.

3. **X.2.c per-visual renderers** (KPI / Table / BarChart / LineChart). Each is one `case` arm in bootstrap + one JS function. All touch one file (`render.py`) so either sequence them or have agents open per-renderer PRs the human merges in order. The merge cost per renderer is small — sequencing is probably easier than coordinating.

4. **X.5 backend** (`etl.yaml` schema + loader + `--end-date` CLI) can start parallel to X.4 — the UI piece blocks on App 1 existing but the data plumbing doesn't.

**Integration pain mitigations (because integration IS always pain):**
- **Frequent merges back to the integration branch (X.2 main-line) — don't let parallel tracks drift for more than 2-3 days.** Long-lived branches accumulate conflict surface logarithmically.
- **Lock the foundation before fanning.** X.2.a/b/f decisions made AFTER agents fan out cost N times the cost made before.
- **Per-track CI** that runs against the integration branch's tip, not just the track branch — catches "my track passed in isolation but doesn't compose."
- **Single owner per shared file.** `render.py` and the data fetcher are the contention hotspots; everything else is per-app or per-dialect and naturally parallel.
- **Track owners write a brief integration note when their work lands.** "I added the Table renderer's `?sort_column` query param — the Sheet builder downstream needs to wire it" type thing. Captures the cross-track ripple before it bites.

### X.1 — e2e fixes + auto-screenshot foundation

Wedge: land auto-failure-screenshot first so all subsequent browser-test investigations get visual evidence. Then use that evidence to root-cause the two known browser-leg defects (L2FT cascade test reading 0 rows; Sasquatch L1 render flake). Then sweep the layered (query+render) pattern across the rest of the suite. Pre-warm Rails is queued behind these; it may become unnecessary depending on what X.1.b finds.

- [x] **X.1.a — Auto-failure-screenshot hook in `webkit_page`.** Shipped v8.6.14 + extended with JS console capture (X.1.a v2) and network response trace (X.1.a v3) during the X.1.b investigation. Every browser test failure now produces `_failures/<test_id>.{png,_console.txt,_qs_errors.txt,_network.txt}` in the GHA artifact.

- [x] **X.1.b — Diagnose L2FT cascade test.** Diagnostic phase complete. Findings:
  - Console + network capture (X.1.a v3) identified `[pageerror] Sample values not found` paired with 4 specific 404 URLs. 3 of the 4 are `tenK-sample-values-V2` calls for the Rail / Status / Bundle CategoryFilter dropdowns; the 4th is `GetThemeForDashboard` (theme).
  - Replacing the Metadata Value `LinkedValues` ParameterDropdown with a `ParameterTextField` shipped (added the new tree primitive). Eliminated 1 of 4 fetches. Visual still empty.
  - Tried statically encoding the 3 CategoryFilter dropdowns via `add_filter_dropdown(selectable_values=...)`. Reverted — AWS rejects `FilterDropdown + CategoryFilter(FILTER_ALL_VALUES) + StaticValues` with `InvalidParameterValueException: doesn't support SELECT_ITEMS control with the given properties`. Helpers (`declared_rail_names`, `transaction_status_values`, `bundle_status_values`) stayed in `apps/l2_flow_tracing/datasets.py` for the proper restructure (X.1.g).
  - Comment: I want full browser testing on everything which is fine if X.1.d is expanded and X.1.b just covers the filter chaining problem.

- [x] **X.1.f — Theme `GetThemeForDashboard` 404 — root-caused + fixed.** Cause (ii) from the X.1.f hypothesis list was right: the L1 Dashboard + L2 Flow Tracing per-app generators in `cli/_app_builders.py` called `build_theme(cfg, ...)` BEFORE stamping `cfg.l2_instance_prefix`, while their `build_*_app(cfg, l2_instance=...)` calls stamp the prefix internally. Result: `theme.json` shipped with id `<resource_prefix>-theme` but every dashboard's `ThemeArn` referenced `<resource_prefix>-<l2>-theme` — a dangling binding. QS's embed session called `GetThemeForDashboard` looking for a theme that didn't exist → 404. Investigation + Executives generators stamped the prefix correctly, so their dashboards had matching themes; the L1 / L2FT mismatch is what produced the 404s X.1.b's diagnostic bundle captured. Fix: stamp the prefix in both broken generators before `build_theme`, plus add a structural guard in `build_theme` that raises if `cfg.l2_instance_prefix is None` so a future per-app generator can't silently regress to the same shape. Verified locally: `describe-theme` resolves cleanly post-deploy + Rails browser e2e passes (deployed dashboard now sees its theme).

- [x] **X.1.g — Convert L2FT CategoryFilter dropdowns to `ParameterDropdown(StaticValues)` shape.** All 7 dropdowns migrated:
  - **Rails**: rail_name / status / bundle_status.
  - **Chains**: parent_chain_name / completion_status.
  - **Templates**: template_name / completion_status (with `cross_dataset="ALL_DATASETS"` to keep tt-instances + tt-legs in lockstep).

  Each control: a multi-valued `StringParam` defaulting to all declared values + `add_parameter_dropdown(StaticValues, MULTI_SELECT)` + `CategoryFilter.with_parameter` (analysis-side filter). Centralized in `_populate_param_filter_dropdown` helper at `apps/l2_flow_tracing/app.py`. New datasets helpers: `chain_completion_status_values`, `tt_completion_status_values`. Per-dropdown browser e2e tests added: `test_l2ft_{rails,chains,templates}_dropdowns.py` (7 tests, walks every advertised option, asserts table doesn't go empty — guards both X.1.g param-bound CategoryFilter narrowing and the broader "advertised dropdown value with no seed data" bug class). Local e2e against deployed sasquatch_pr passed 5 / 7; 2 failures are pre-existing data-coverage gaps, queued as X.1.i (Status `Failed` + open-set enum) and X.1.j (Chain validator: reject zero-Required-children). Cascade test re-skipped, queued as X.1.g.11 follow-up — Metadata Value is now a text field, the original cascade-source regression class is structurally unreachable on that shape. Index cleanup (v8.6.8 template_name + transfer_parent_id) deferred until X.1.i / X.1.j land.

- [x] **X.1.i — Status enum: open-set + plant Failed transactions.** L2FT Rails Status dropdown's `Failed` enum value replaced with `Other` sentinel (covering every status outside `Pending`/`Posted` per the open-set schema). L2FT Rails dataset SQL gained `CASE WHEN status IN ('Pending','Posted') THEN status ELSE 'Other' END AS status` projection. New `FailedTransactionPlant` dataclass + `_emit_failed_transaction_rows` emitter; auto-scenario plants one Failed leg per scenario via the existing `pending_rail` pick. Bug caught + fixed during local verify: `densify_scenario` / `boost_inv_fanout_plants` / `add_broken_rail_plants` were dropping the new field on reconstruct — patched all 3 to pass it through. Re-hash-locked sasquatch_pr + spec_example seed SHA256s; bundled `_l2_fixtures/` + `_default_l2.yaml` synced. Verified locally: deployed Status dropdown e2e passes against sasquatch_pr (1 Failed seed leg confirmed in DB; QS narrowing through the projected `Other` value works).

- [x] **X.1.j — L2 validator: reject Chain with zero Required children AND zero XOR groups.** Added validator rule C5 at `common/l2/validate.py::_check_chain_parent_has_required_or_xor`: every chain parent MUST have at least one `required=True` child OR at least one `xor_group`-tagged child. Dropped the `'No Required Children'` enum value from `chain_completion_status_values()` and the matching CASE branch from the chain-instances dataset SQL. Updated the fuzzer (`tests/l2/fuzz.py::_build_chains`) to pin the first plain entry per parent to `required=True` so generated instances satisfy C5. Verified locally: deployed Chains Completion e2e passes against sasquatch_pr (only `Completed` / `Incomplete` advertised, both have data).

- [x] **X.1.h.A — Build out real ETL example patterns (the etl.md hallucination fix).** Pre-X.1.h `apps/investigation/etl_examples.py::generate_etl_examples_sql()` returned a single placeholder line claiming the patterns lived in deleted-since-M.4 `payment_recon/etl_examples.py` + `account_recon/etl_examples.py` files. New `common/etl_examples.py` ships 10 canonical INSERT-pattern blocks against the v6 schema covering: single-leg Posted, two-leg paired, force-posted (`origin='ExternalForcePosted'`), Pending → Posted lifecycle, TechnicalCorrection rewrite, bundled transfer (`bundle_id`), chained transfer (`transfer_parent_id`), daily balance row, daily balance with limits JSON, and metadata extension. Each block carries `-- WHY:` + `-- Consumed by:` headers per the handbook contract. Output is deterministic (no random IDs, no clock-dependent timestamps). 11 unit tests guard the structural contract.

- [x] **X.1.h.B — CLI cross-reference checker (Track B v0).** Custom pytest test at `tests/unit/test_docs_cli_invocations.py` walks `src/quicksight_gen/docs/**/*.md`, extracts every `quicksight-gen <cmd> <flags>` invocation from fenced bash blocks, and asserts the subcommand chain + every flag exist in the live Click tree. Catches the "doc cites a removed CLI verb / renamed flag" hallucination class. Handles bash line continuations, env-var prefixes (`QS_GEN_E2E=1 quicksight-gen ...`), positional args (`audit verify report.pdf -c config.yaml`), and rejects false positives like `pip install quicksight-gen`. 12 docs cited the CLI; all 12 pass against the v8.6.18 surface. Backlog items split out: X.1.h.B.2 (Python doctest infra w/ fixtures), X.1.h.B.3 (SQL block execution), X.1.h.B.4 (schema column cross-reference, e.g. `transaction_id` → `id` drift in populate-transactions walkthrough).

- [x] **X.1.k — Unify the seed-hash lock surface; key the CLI off config.yaml, not the L2.** Shape (c2) shipped: `data hash` renamed to `data lock`, takes `-c config.yaml --l2 path/to/L2.yaml`, derives dialect from `demo_database_url`, and writes the locked SQL to `tests/data/_locked_seeds/<instance>.<dialect>.sql`. `--check` re-emits and shows a unified diff on drift (50-line cap). `emit_full_seed` stamps a `-- SHA256: <hex>` header on every emit so saved-to-disk SQL self-identifies. Deleted: the four old hash sites — YAML `seed_hash:` blocks (6 files), `seed_hash` field on `L2Instance`, `_load_seed_hash` loader, two `test_full_seed_hash_lock_*` Python constants, `_BROAD_MODE_HASHES` dict + `test_broad_mode_hashes_match_lock` parametrize. New auto-discovering test at `tests/data/test_locked_seeds.py` parameterizes over `_locked_seeds/*.sql`. Locked spec_example at both dialects (~1.5MB each); skipped sasquatch_pr (would have been 55MB+ each, way too much repo bloat — spec_example is the canonical contract and catches every change to the seed pipeline; sasquatch_pr is a flavor instance whose L2-shape changes are obvious in the YAML diff at PR review time). 1706 unit tests passing. Today the hash lock has four distinct sites and one CLI that updates one of them: (i) `seed_hash.postgres` in each L2 YAML, (ii) `seed_hash.oracle` in each L2 YAML, (iii) the Python full-seed hash constants in `tests/data/test_l2_baseline_seed.py`, (iv) `_BROAD_MODE_HASHES` in `tests/data/test_l2_seed_contract.py`. `quicksight-gen data hash --lock` only writes (i) because dialect was hardcoded to postgres. A partial re-lock during X.1.i ⇒ CI red on every commit between 2026-05-04 21:58 and the fix-CI commit (`ff40c8e` … `3b113ed`); the docs-templating commit `ff40c8e` added 3 unrelated failures on top, masking that the lock surface was the real problem.

  **Reshape:** the hash is a function of `(L2 instance, dialect)`. Dialect lives on `config.yaml` (via `demo_database_url`). So the CLI should take `-c config.yaml` instead of an L2 path, derive the dialect, and write to the matching L2 slot — config.yaml is what knows the deploy target, the L2 just carries the scenario. New invocation: `quicksight-gen data hash --lock -c config.postgres.yaml` and `... -c config.oracle.yaml` together cover both dialects for the L2 referenced by each config.

  **Storage shape (decided):** the hash IS the seed's identity, not an input — so it doesn't belong in the L2 YAML or in Python constants. **Lock the emitted SQL itself.** Per `(instance, dialect)` write the canonical-anchor seed to `tests/data/_locked_seeds/<instance>.<dialect>.sql` (checked into git). Test recomputes SHA256 of a fresh emit and compares against SHA256 of the locked file; failure surfaces the SQL diff so reviewers see what shifted, not "trust me, the new hash is correct." `emit_seed` also stamps `-- SHA256: <hash>` as a header comment (computed against the post-strip body) for human-readability of one-off SQL outputs. Sites (i)-(iv) (postgres / oracle YAML `seed_hash:` blocks, `test_l2_baseline_seed.py` Python constants, `_BROAD_MODE_HASHES` dict) all get deleted. Test parametrizes over auto-discovered `_locked_seeds/*.sql` so adding a new (instance, dialect) pair = drop a file, no Python constant to maintain. CLI rename: `data hash` → `data lock` (with `--lock` / `--check` flags) — what we're doing is byte-snapshotting the SQL, not just hashing it.

  **CLI shape:** `quicksight-gen data lock -c config.postgres.yaml` writes / refreshes the locked SQL for the L2 referenced by config (dialect derived from `demo_database_url`). `quicksight-gen data lock --check -c config.postgres.yaml` exits non-zero if the fresh emit doesn't match the locked file. Run once per (config.postgres.yaml, config.oracle.yaml) covers both dialects. Reviewer cost: bigger PR diffs (~hundreds of KB of SQL per shift) — which is a feature for a hash-lock that exists specifically to catch unreviewed seed drift. Seed SQL compresses well in git.

  **Architectural smell (deferred):** the per-artifact emit / lock surface (schema | data | json | docs | audit each independently emittable, each with their own contracts + locks) is what made today's partial re-lock possible. A unified "deploy bundle" view — emit all five together, hash all five together, lock the bundle — would collapse the failure mode. Counter-argument: independence is what gives `json apply` (re-deploy dashboard without re-running schema) its ergonomic appeal; a unified bundle would force every change through the slowest emitter. **Hold for now.** If a second bug of this class shows up (one artifact's hash drifts while siblings stay locked, or cross-artifact id consistency breaks again like the X.1.f theme binding), revisit as Phase Y or similar.

  **Gate:** add `data lock --check` as a CI step before the unit suite, run once per `(config.postgres.yaml, config.oracle.yaml)`. Failure shows the SQL diff inline so reviewers don't have to chase down which of 7+ scattered hash-mismatch assertions is the real signal.

  Must land before X.1.c / X.1.d so a future seed-shape change in either phase doesn't repeat the partial-lock incident.

- [x] **X.1.c — Sasquatch L1 dashboard 'flake' was table virtualization.** Diagnosed + fixed in `09e8f16`. NOT a flake — deterministic data-density-dependent assertion bug. The harness's `_active_sheet_text` reads `inner_text()` of every visual on the active sheet to check whether a planted account_id surfaces; QS tables virtualize at ~10 DOM rows so `inner_text()` only sees the rendered window. Sasquatch_pr's denser seed pushed `cust-0001-snb` below the visible 10 — spec_example's sparser seed kept it visible. The candidate fix list (widen wait / screenshot diff / tighter days_ago plant) was off-target; the real root cause was the assertion mechanism, not timing or sheet state. Fix: new `expand_all_tables_on_sheet` helper bumps every paged table to page-size 10000 before reading text. Verified live `2026-05-04`: `./run_e2e.sh --harness` passes 15/15 across all 3 L2 instances in 13:47, including the previously-flaking sasquatch_pr Limit Breach assertion.

- [x] **X.1.d — Layered (query+render) pattern: helpers shipped, sweep deferred.** X.1.d.1 lifted the harness's `assert_l1_matview_rows_present` pattern into a reusable module at `tests/e2e/_layer1_query.py` (`query_matview_rows`, `matview_row_count`, `assert_matview_has_row`, `assert_account_in_matview` — all dialect-aware, 14 unit tests). The originally-planned `.d.2/.d.3` sweep across active browser tests (`test_l1_filters`, `test_l1_sheet_visuals`, `test_inv_filters`, `test_inv_sheet_visuals`, `test_exec_sheet_visuals`) was assessed as low value: those tests assert *structural* claims (visuals present, filter narrows row count, dropdown opens) rather than *specific row presence*, so Layer 1's "is the row in the matview" check doesn't have a natural site to slot in. The active row-presence tests that would benefit (`test_inv_drilldown.py`, `test_l2ft_metadata_cascade.py`) are both `@pytest.mark.skip`'d (dependent on URL-hash anchor pre-seed and on a removed dropdown, respectively). The harness — already layered — remains the canonical row-presence test. **Going forward**: new e2e tests that assert specific row presence should call into `_layer1_query.py` to gate the Layer 2 render assertion.

- [ ] **X.1.e — Pre-warm Rails sheet (perf hardening — reassess after X.1.b).** Originally proposed as a fix for the L2FT cascade test failure, but X.1.b's investigation will reveal whether the actual root cause was perf-related. If X.1.b resolves the failure without needing pre-warm, this item drops from scope. If perf was a contributing factor: visit Rails once during dashboard warm-up, navigate away, then re-enter for the actual assertion (cache is hot the second time).

### X.2 — App 2: self-hosted dashboard renderer (QuickSight as one dialect, not THE frontend)

**Reframe.** Same way `Dialect.POSTGRES`/`ORACLE` are SQL dialects projected from one schema model, the dashboard tree (`common/tree/`) becomes a **dashboard model** with three renderers: QuickSight JSON (today), audit PDF (today, proves the pattern works), and a self-hosted HTML renderer (this phase). The tree's job description sharpens — it IS the dashboard, not a description of QS dashboards.

**Stack** (all proven via spike): Python backend (Starlette) + HTMX + Tailwind v4 (via `pytailwindcss` standalone CLI) + d3 from CDN. No bundler, no TS, no npm. Read-only surface, no auth in X.2 (auth deferred to backlog).

**Browser support.** Modern evergreens only — Chrome / Edge / Firefox / Safari, current minus 2 versions. No IE, no legacy mobile webview support. The HTMX + d3 + Tailwind v4 stack we're building on doesn't push beyond standard ES6 / modern SVG / CSS variables, so this is essentially "browsers people actually use today" — no exotic features being demanded.

**Why it's worth building (not just an annoyed diversion):**
- **RESTful filtering as the killer USP.** Every (sheet, filter-set) is a bookmarkable URL — "click this URL to see what I saw on May 5 at 3pm." QS embed URLs are signed, single-use, don't carry filter state. Pairs with the audit PDF — both become reproducible-view artifacts.
- **Server-side cache keys are trivial.** `(sheet, filter-set)` → SQL result. URL == cache key. SPICE-equivalent for the Python backend, with edge / browser caching free via Cache-Control.
- **Bypasses the QS quirks log entirely.** URL parameter sync, dropdown click-target weirdness, spinner-forever footgun, render flakes — none of these surface for the HTMX dialect.
- **No QS per-user license fees.** Real cost story for customers.
- **Same e2e harness gates both dialects.** Layer 1 (matview check) is renderer-agnostic; Layer 2 swaps the driver. Bug-parity falls out of the harness shape already in place.

**Hard parts (still real, but smaller post-spike):**
- **Sheet count + visual richness.** L1 dashboard alone is 11 sheets × 5–15 visuals each. Each app's surface needs the full visual catalog wired (KPI / Table / BarChart / LineChart / Sankey + filters).
- **Embedding context.** QS dashboards embed via signed URLs; HTMX dialect needs its own iframe story when phase.2 lands. Out of scope here.
- **Maintenance.** Two render implementations of the same surface — Layer 2 e2e parity (the dialect-comparison gate) is what keeps them honest.

**Spike sequence — proven on `phase-x-2-htmx-renderer` branch (Apr-May 2026):**

- [x] **X.2.spike.1** — `emit_html(app, sheet)` projects tree → HTML. Pure projection, takes in-memory tree node, never touches disk. spike.1.fix promoted `App._resolve_auto_ids` to public + threaded App through `emit_html` so `data-visual-id` attributes resolve before render.
- [x] **X.2.spike.2** — Starlette server + HTMX swap + d3 hydration on swapped fragments. Pluggable `DataFetcher` callable so spike tests stay DB-free. `python -m quicksight_gen.common.html` is the smoke runner.
- [x] **X.2.spike.3** — Layer 2 (Playwright + WebKit) against the local Starlette server. Positive case asserts SVG rect/path counts match Layer 1 fetcher's promise; negative case (route-intercepted empty body) catches the regression class. Same shape as the QS harness's Layer 2, different selectors. Dialect-comparison thesis proven.
- [x] **X.2 capability experiments** — clickable Sankey via d3 + `htmx.ajax` (anchor click pattern), dev-log + HTMX event forwarder, Tailwind v4 CSS via pytailwindcss CLI (styles HTML + d3 SVG), ForceGraph visual via d3-force (proves new visual kinds add as one `case` arm + one render function).

Architecture decisions captured in `docs/x_2_design_thoughts.md` (App 1 vs App 2 split, all-GET REST surface, plural nouns + nested resource paths, query-param state, cascade-rename via `HX-Trigger`, force-directed as shared primitive across three surfaces).

**Test matrix expansion (X.2 + X.3 woven together).** As renderers + DB backends multiply, the regression surface multiplies. The matrix that needs to stay green to call this "parity":

|                 | Postgres | Oracle | SQLite (X.3) |
|-----------------|----------|--------|--------------|
| **Layer 1** (matview check, renderer-agnostic — `_layer1_query.py`) | ✓ today | ✓ today | ✓ X.3.g.1 |
| **L2 QS JSON dialect** | ✓ today (`e2e-pg-api/browser`) | ✓ today (`e2e-oracle-api`) | N/A — QS doesn't read SQLite |
| **L2 Audit PDF dialect** | ✓ today (`tests/audit/`) | ✓ today | ✓ X.3.g.2 |
| **L2 HTMX dialect (this phase)** | X.2.h | X.2.h | X.3.g |
| **Cross-tool agreement** (U.8.b becomes 4-way: `expected == PDF == QS == HTMX`) | X.2.j | X.2.j | X.3.g |

Eight cells worth gating on (3 renderers × 3 DBs minus QS-on-SQLite). Five exist today; X.2.h adds two (HTMX × {PG, Oracle}); X.3.g adds three (Layer 1 + Audit PDF + HTMX, all on SQLite); X.2.j wraps it with the 4-way agreement contract that catches "all the renderers passed but they disagreed." Cost shape (which cells need AWS, which run on Docker / locally) lives in X.7.

**Parity-as-much-as-possible principle.** Some cells are physically unsupportable — QS×SQLite is the obvious one (QuickSight can't read SQLite; no native datasource type for it). Future combos may surface similar gaps (e.g. a renderer that doesn't support a particular visual kind, a DB feature missing on one dialect). When that happens: mark the cell N/A in the matrix with a one-line reason, run every other combination in the row + column. The principle is parity wherever the combination is technically possible — not parity everywhere, regardless of physics.

**Theme parity — its own 4-way test.** Distinct concern from data parity. The L2 YAML's `theme:` block (`common/l2/theme.py::ThemePreset`) is the single source of truth for accent / primary_fg / link_tint / etc. Today QS dashboards, audit PDF, and the docs site all consume it (Phase U.1 cover, v8.6.10 docs CSS injection). App 2 (X.2) currently uses hardcoded Tailwind classes — needs to consume the same theme. Once it does, the four visual surfaces below should render the same accent for the same theme spec:

| Surface | Status |
|---------|--------|
| QS dashboard | ✓ today (`build_theme(cfg, theme)` → QS Theme JSON) |
| Audit PDF | ✓ today (Phase U.1 cover) |
| Docs site | ✓ today (v8.6.10 CSS injection) |
| App 2 HTMX | X.2.l |

Test: change one accent value in the L2 YAML, all four surfaces shift in lockstep. Becomes a release-gate visual smoke (one screenshot per surface; pixel-diff or eyeball — eyeball acceptable since the gate is "do they all match the spec," not "are they pixel-identical").

**Scope to feature parity + e2e tests passing:**

- [ ] **X.2.a — Architecture cleanup post-spike.** Promote the experimental code into the phase shape:
  - Lift `tests/spike/test_html_layer2.py` → `tests/e2e/_harness_html2.py` (Layer 2 against HTMX dialect, fixture pattern matches the QS harness).
  - Replace the smoke runner's stub fetcher with a real DB-backed `DataFetcher` factory; keep the stub callable for unit tests.
  - Move the `python -m quicksight_gen.common.html` entry point under a Click subcommand (`quicksight-gen serve` group: `app2 apply`, mirroring the existing `schema | data | json | docs | audit` shape).
  - Branch consolidation pass — squash / rebase the spike experiments before further work, get the diff reviewable.
  - **JS tooling — same standalone-binary pattern as pytailwindcss:** add `biome` (Rust binary, single executable — replaces eslint + prettier + a minifier) via `biome-py` on PyPI or a 30-line downloader if no current wrapper. Adds to `[dev]` extras. CI step: `biome lint` + `biome check --write` (format) + `biome --apply --write --max-diagnostics=0` to gate merges. Minify in the build pipeline alongside the tailwindcss build. **JS unit tests:** extend the existing Playwright pattern — small HTML fixtures under `tests/js/` load the function under test in a real browser context, exercise it, write results to `window.__test_result` for Playwright to assert on. No jest / vitest / Node dependency. Trade-off: each test costs ~50ms Playwright launch overhead; we accept that because the JS is DOM/HTMX-coupled enough that real-browser tests catch the right class of bugs.

- [ ] **X.2.b — All-GET REST surface per design doc.** Drop `POST /visual/{id}/data`. New routes (plural nouns, nested resources):
  - `GET /` — landing page, lists `/dashboards`.
  - `GET /dashboards` — fixed list of the four apps for the served L2 instance (L1 Dashboard + L2 Flow Tracing + Investigation + Executives). One App 2 process serves one L2 instance — multi-tenancy is the auth use case (deferred to phase.2).
  - `GET /dashboards/:id` — redirect to first sheet OR render dashboard chrome + first sheet inline.
  - `GET /dashboards/:id/sheets/:id` — full sheet content.
  - `GET /dashboards/:id/sheets/:id/visuals/:id` — single visual (with chart data inline). Filter values via query string.
  - Bookmarkability: `hx-push-url="true"` on every swap so URL stays in sync with filter / sort / page state. Browser back/forward replays.
  - Cache-Control headers on data routes (Money Trail data scoped to a date range becomes cacheable per-tuple).
  - `POST /log` (dev-only) is the only POST that survives — for the dev-log forwarder.

- [ ] **X.2.c — d3 renderers for the remaining visual primitives.** Currently Sankey + ForceGraph proven. Add one `case` arm + one `renderXxx` JS function each:
  - **KPI** — a single number + delta arrow. ~10 lines of d3 (mostly text styling).
  - **Table** — `<table>` with sortable headers (each `<th>` is a link to `?sort_column=col:desc`). Pagination via `?page_offset=N&page_size=50`. Tailwind classes for striped rows + sticky header.
    - Comment: for the pagination, a big win over quicksight would be to give a total row count with the pagination (prefer 0-50 of x over page 1 of xx). this will make the e2e testing way easier
  - **BarChart** — d3 bars with axis labels (the plain-English ones added in v8.5.5 carry over via the tree).
  - **LineChart** — d3 line + axes + legend.
  - Per-renderer Layer 2 unit test: stub fetcher, assert SVG / DOM shape matches Layer 1 promise (rect counts, path counts, etc.).

- [x] **X.2.d — Filter primitives wired.** Currently date range form. Add:
  - **ParameterDropdown** — `<select>` driven by L2 / dataset values; change fires a swap with the new value in `?param_<name>=...`.
  - **CategoryFilter** — multi-select check group; change fires `?filter_<col>=v1,v2,v3`.
  - **NumericRange** — two inputs or slider; fires `?min_<col>=N&max_<col>=M`.
  - All values flow into URL query params per X.2.b. No form state.
  - **Status:** `ParameterDropdownSpec` / `CategoryFilterSpec` / `NumericRangeSpec` frozen dataclasses in `common/html/render.py`, exported from `common/html/__init__.py`. `emit_html(..., filter_specs=[...])` threads them into the form. Server-side: existing `visual_data` route already passes all query params through to the data fetcher, so no server changes needed (X.2.f's real fetcher consumes the prefix-keyed dict). CategoryFilter's checkboxes are unnamed — a hidden `filter_<col>` input is what HTMX serializes; `wireCategoryFilters` (bootstrap.js) keeps it in sync as a comma-joined string. 23 tests: 16 in `tests/unit/test_html_filter_primitives.py` (render + TestClient round-trip) + 7 in `tests/js/test_filter_primitives.py` (CategoryFilter checkbox sync).

- [x] **X.2.e — Sheet structure + cross-sheet + cross-app navigation.** Sheet tabs across the top of `/dashboards/:id`. Click switches sheet via `hx-get` + `hx-push-url`. Cross-sheet drills (currently QS `CustomActionURLOperation`) become plain `<a href>`s in the rendered HTML — `/dashboards/:id/sheets/:other-sheet?param_account_id=...`. URL-as-state means the QS URL-param-doesn't-sync-controls quirk doesn't surface.
  - **Cross-app drills (QS-blocked, App 2 unblocks).** L1 → Investigation drills are dropped under QS (`project_qs_url_parameter_no_control_sync` memory: K.4.7 — URL fragment sets parameter but doesn't push values into bound controls). App 2's URL-as-state model makes this trivial — `<a href="/dashboards/investigation/sheets/money-trail?param_anchor=...">` works. Wire the previously-deferred K.4.7 cross-app drills back in for the App 2 dialect; QS dialect stays as-is. Drill destinations need to know the OTHER app's URL pattern — resolve at render time via a small `app_for(visual)` helper that maps tree references to dashboard ids.
  - **X.2.g.1.c — Visual-level aggregation wrapping.** ✓ — App2's tree fetcher now wraps the dataset SQL with the visual's declared aggregation before executing. Without this, KPI(values=[count(...)]) rendered one card PER DATASET ROW (the screenshot bug). New `common/html/_visual_sql.py::wrap_for_visual(base_sql, visual)` produces: KPI → `SELECT <agg(col)> FROM (base_sql) sub`; BarChart → `SELECT <cat>, <agg(val)> FROM (base_sql) sub GROUP BY <cat>`; LineChart → same + `ORDER BY <cat>`; Table / Sankey / ForceGraph → unwrapped pass-through. Maps Measure.kind ("sum"/"max"/"min"/"average"/"count"/"distinct_count") to the SQL aggregation function. Dialect-portable (SQL-92 aggregations every dialect supports). Tree fetcher unit tests updated to use row-grain dataset SQL.
  - **X.2.g.1.b — Cross-dialect filter wiring via SQL templating.** ✓ — `build_dataset(..., app2_sql=...)` accepts an optional App2 SQL variant; QS SQL stays as-is for `CustomSql.SqlQuery`. Each app's dataset author writes ONE SQL template with a `{date_filter}` slot — QS gets `""` (its filter comes from the analysis-level FilterGroup), App2 gets the bind-clause snippet from `common/sql/app2_filters.app2_date_filter("col_name")`. The snippet uses `NULLIF(:date_from, '') IS NULL OR ...` to handle PG's `''` vs Oracle's `''`-as-NULL vs SQLite's `''`-as-text. Executives' two datasets (`exec_transaction_summary`, `exec_account_summary`) ported to the templating pattern — no SQL duplication. The same pattern lifts to L1 / L2FT / Investigation when their X.2.g.{2,3,4} wiring lands. **Architecture note:** this is the latest example of the broader "one logical model, projected per target runtime" pattern: SQL placeholders (PG `%s` / Oracle `:1` / SQLite `?` / App2 `:name` / QS `<<$name>>`), theme tokens (CSS vars vs QS Theme JSON), filter primitives (URL params vs ParameterControl), all converging.
  - **Status:** Sheet tabs + sheet route shipped. `_render_sheet_tabs(dashboard_id, sheets, active_sheet_id)` renders plain `<a href="/dashboards/:d/sheets/:s">` per analysis sheet (active tab carries `bg-accent`); single-sheet analyses get an empty tab strip (suppressed). New `/dashboards/:d/sheets/:s` route in `server.py::sheet_view` resolves any analysis sheet by id (404 themed). `visual_data` route widened to accept any analysis-sheet id, not just the served (default landing) sheet's. Tabs use full-page reload (no HTMX swap) — Tailwind/HTMX/d3 are cached so only chrome reloads, and URL stays trivially bookmarkable. Decision was plain-anchor over hx-get to avoid the fragment-vs-full-page distinction. 13 tests in `tests/unit/test_html_sheet_nav.py`. **Deferred to X.2.e.2:** cross-sheet drill rendering (per-row anchors derived from the tree's `Drill` primitive — needs renderer-per-visual-kind work in bootstrap.js or per-row server fragments) and cross-app drill rendering (the K.4.7 re-enable — needs an `app_for(visual)` resolver that lands when X.2.g wires the 4 apps). The plumbing this phase shipped is what those follow-ons sit on top of.

- [x] **X.2.f — Real data fetcher.** Replace the stub `DataFetcher` callable with an implementation that:
  - Takes the visual's dataset SQL (already in `apps/<app>/datasets.py`).
  - Substitutes filter values into the SQL (parameterized — never string-format user input).
  - Executes against the configured DB (Postgres / Oracle, dispatched via `Dialect`).
  - Returns rows shaped per visual kind (Table → list of dicts, Sankey → nodes/links, KPI → number, etc.).
  - Per-visual `data_shape()` helper on each tree primitive that documents the JSON shape its d3 renderer expects.
  - **Status:** Infrastructure shipped. `common/html/_sql_executor.py` provides `execute_visual_sql(connection_factory, sql, url_params, dialect)` — accepts `:name`-style placeholders in dataset SQL, rewrites to `%(name)s` for Postgres (Oracle/SQLite stay native), collects bind params from the URL-keyed filter dict, executes against any DB-API 2.0 connection, returns `(rows, columns)`. Unreferenced URL params are silently dropped (forms serialize every input on every Refresh; per-visual SQL only references the filters it cares about). `common/html/_data_shape.py` provides `shape_kpi` / `shape_table` / `shape_bar_chart` / `shape_line_chart` / `shape_sankey` adapters matching the bootstrap.js `renderXxx` JSON contracts exactly, plus `shape_for_kind(kind, rows, columns, **opts)` dispatcher keyed off `type(visual).__name__`. ForceGraph stays projector-driven (no SQL). Wiring into `make_db_fetcher` per app lands when X.2.g builds out the 4 apps — X.2.f is the renderer-agnostic infrastructure those per-app fetchers consume. 31 tests in `tests/unit/test_html_data_shape.py` (17) + `tests/unit/test_html_sql_executor.py` (14); end-to-end SQLite round-trip in the executor tests.

- [ ] **X.2.g — Build out the 4 apps.** Order by dependency / risk, lightest first to surface architecture gaps early:
  - **X.2.g.0 — Generic per-tree DataFetcher factory** ✓ — `make_tree_db_fetcher(tree_app, cfg)` walks visuals, looks up dataset SQL via the new `get_sql(visual_identifier)` registry (populated by `build_dataset` → `register_sql`), executes via X.2.f's `_sql_executor`, shapes via X.2.f's `_data_shape`. 10 tests in `tests/unit/test_html_tree_fetcher.py` (build-time visual indexing + dispatch + filter substitution + multi-sheet + loud-fail on missing SQL).
  - **X.2.g.1 — Executives** (5 sheets: Getting Started + Account Coverage + Transaction Volume + Money Moved + Info; KPI + Table + BarChart visuals). ✓ — `quicksight-gen serve app2 apply --app executives` builds the real Executives tree, calls `build_all_datasets(cfg)` to populate the SQL registry, constructs the fetcher via `make_tree_db_fetcher`, and serves through the existing App2 server. 4 wiring tests in `tests/unit/test_html_executives_wiring.py` pin sheet count + assert the build-time SQL lookup succeeds for every visual. Operator verifies end-to-end against live PG before X.2.g.2 lands. Fastest end-to-end signal that the renderer carries a real app.
  - **X.2.g.2 — Investigation** (5 sheets: Money Trail Sankey already proven via spike, Account Network already a force-directed shape).
  - **X.2.g.3 — L2 Flow Tracing** (4 sheets: Rails, Chains, Templates, Hygiene — heavy on ParameterDropdown + cascading filters from X.1.g).
  - **X.2.g.4 — L1 Dashboard** (11 sheets — biggest surface, do last when the renderer is settled).
  - Per-app smoke: render every sheet, walk every drill, exercise every filter.
  - **Discipline (pre-X.6 docs work):** every new sheet's `description` and every visual's `subtitle` is the single source of truth for the corresponding docs page. CLAUDE.md already mandates "every visual has a subtitle" — extend the rule to "the subtitle IS the docs source of truth." X.6 will read these strings to auto-generate walkthroughs; if they live in tree primitives now, X.6 has nothing to refactor.
  - **App Info sheet parity.** Every QS app has the `Info` canary as its last sheet (CLAUDE.md mandate; renders App version + per-matview latest_date + deploy stamp). App 2 serves the same Info sheet — same diagnostic value when a sheet renders empty: glance at Info first to tell "App 2 is healthy, the data/SQL is the bug" from "App 2 itself broke." Renderer treats the Info text-box visual the same as a KPI card.

- [ ] **X.2.h — Layer 2 e2e against the HTMX dialect.** Lift `tests/spike/test_html_layer2.py` pattern across the harness. Same fixture shape as the QS harness, same Layer-1 + Layer-2 assertions, different selectors:
  - **Status: X.2.h.1 (Executives stub) + X.2.h.2 (Executives live PG) shipped.** `tests/e2e/test_html2_executives.py` (stub fetcher, 6 tests) covers: sheet tabs render, TextBoxes render, visuals auto-load on DOMContentLoaded, filter changes refetch, filter form suppression on text-box-only sheets. `tests/e2e/test_html2_executives_live.py` (live PG, 2 tests) covers: KPI renders with real data (catches "wrong L2 prefix → relation does not exist"), date filter doesn't error when applied (caught the PG OR-short-circuit gotcha that X.2.g.1.b's COALESCE+sentinel-date pattern fixes). Live-PG variant gates on `QS_GEN_TEST_L2_INSTANCE` env var pointing at the L2 YAML matching the seeded DB; skips cleanly when unset. Shared assertion helpers (`wait_for_kpi_value`, `wait_for_table_rows`, `make_live_db_fetcher_for_app`) extracted into `_harness_html2.py` so X.2.h.{2,3,4} for Investigation / L2FT / L1 ports are mechanical — same shape per app, only sheet IDs / visual IDs / matview names change.
  - Add an `HTMX_HARNESS=1` env gate alongside `QS_GEN_E2E=1`.
  - Re-use `_layer1_query.py` (already renderer-agnostic per X.1.d.1).
  - Mirror every existing browser test (test_l1_*, test_inv_*, test_exec_*, test_l2ft_*) against the HTMX dialect.
  - **Per-DB cells.** HTMX runs against PG + Oracle now (Docker, no AWS — HTMX dialect doesn't need QuickSight). SQLite cell lands when X.3 ships. Two new GHA jobs: `e2e-htmx-pg`, `e2e-htmx-oracle` (+ `e2e-htmx-sqlite` post-X.3).
  - **Performance budget gate.** New `tests/perf/test_swap_latency.py` against the local Starlette server: assert P95 swap latency under a budget (initial: 100ms server-side per swap; revisit after the 4-app surface lands). Pass/fail in CI. Catches the day someone introduces an N+1 query that ruins the iteration UX. The "we run circles around QS" claim becomes measurable, not aspirational.
  - The dialect-comparison gate: same Layer 2 assertion catches a render bug in both dialects = parity proven.

- [ ] **X.2.i — Embed MkDocs into App 2.** `Mount("/docs", StaticFiles(directory=mkdocs_build_dir))` on the Starlette app. One process, one cache, one auth front (when phase.2 lands). Per the design thoughts. Removes the "docs live on Pages, dashboards live in QS, audit PDF lives wherever" three-surface story.

- [ ] **X.2.j — Production validation + 4-way cross-tool agreement + backward-compat.** Deploy App 2 against real Aurora across all 4 apps with sasquatch_pr seed. Visual eyeball + Layer 2 e2e green = the "feature parity with QS, minus the bugs" claim verified. Cross-tool agreement extends U.8.b's three-way contract (`expected == PDF == QS dashboard`) to four-way (`expected == PDF == QS dashboard == HTMX dashboard`). Per DB. Becomes a release-gate test in `e2e-against-testpypi`. Catches the "all renderers individually passed but they disagree on a violation row" failure class — the only test that really earns the parity claim.
  - **Backward-compat assertion.** Existing QS deploys keep working through X.2-X.5 ship. Both local CI (`pytest tests/json/`) and `e2e-against-testpypi` already exercise the QS surface — the assertion is "those tests stay green for the entire phase X, no exceptions." If a tree primitive change for App 2 breaks QS emit, that's a regression to be fixed, not a tradeoff to accept.

- [ ] **X.2.l — Theme integration: L2 YAML drives Tailwind rendering.** Currently App 2 hardcodes Tailwind classes (`bg-slate-50`, `fill-blue-500`, etc.) — same source-of-truth violation as hand-written docs. Wire L2 theme through:
  - `common/l2/theme.py::ThemePreset` is already the canonical theme. App 2's page shell injects per-instance CSS variables in `<head>` (`--color-accent: #...; --color-primary-fg: #...; ...`) read from the resolved theme.
  - Tailwind v4's `@theme` block in `input.css` declares semantic color tokens (accent, primary-fg, link-tint, surface, surface-muted) that resolve to the CSS variables. Tailwind generates `bg-accent`, `text-accent`, `fill-accent`, etc. utilities that consume them.
  - Replace hardcoded utility names in `render.py` (`bg-slate-50` → `bg-surface`, `fill-blue-500` → `fill-accent`, etc.) so the Tailwind output is theme-token-driven, not literal-color-driven.
  - d3 SVG class strings in the bootstrap JS get the same treatment — `fill-accent` instead of `fill-blue-500`.
  - **4-way theme parity test** (per the matrix above): single screenshot per surface (QS dashboard, audit PDF, docs site, App 2 HTMX) generated with the same L2 theme spec. Eyeball comparison gate at release. Fast — runs against the existing demo deploy.
  - Honors the silent-fallback contract from N.4.k: when L2 has no `theme:` block, App 2 falls back to a built-in default Tailwind palette, same as QS does CLASSIC.
  - **Accessibility rule (carries across all four surfaces).** Color SHALL NOT be the only indicator for status / category / severity. Use texture / icon / shading / position alongside. If the operator picks a low-contrast theme that's their call (theme is their input), but the visual encodings the renderer chooses must remain interpretable in monochrome. Phase X.2 enforcement is a code-review rule; future phase could add an a11y CI lint.

- [x] **X.2.m — Error handling: 5xx pages + transient toasters.** Today's spike returns raw stack traces on dataset failure / DB down / sheet-not-found. Production needs:
  - **Status:** themed 5xx + 404 pages (`emit_error_page` reuses the X.2.l theme injection so the per-dashboard `ThemePreset` cascades through; `DEFAULT_PRESET` fallback when L2 has no theme) + HTMX toasters via `htmx:responseError` / `htmx:sendError` listeners in `bootstrap.js` (toast at top-right, `bg-danger` / `text-accent-fg` semantic tokens, auto-dismiss 5s, stacks). Production mode hides tracebacks; `dev_log=True` carries them inside `<details>`. Tests in `tests/unit/test_html_error_handling.py` (16) + `tests/js/test_error_toasts.py` (7). Structured server logs + health check endpoint deferred — they land alongside whichever later phase introduces the logging stack.
  - **Full-page errors** (404 sheet not found, 500 dataset SQL crashed, 503 DB unreachable) — render a styled error page that fits the page shell + dev-log + theme. Includes a "report this" surface that captures URL + request-id + timestamp for the operator's logs.
  - **Toasters for transient on-page errors** — a swap returned 5xx but the rest of the page is fine; show a dismissable toast at the top of the viewport ("Couldn't refresh Money Trail — retrying" with a manual retry button). HTMX has `htmx:responseError` → catch in the dev-log forwarder pattern, render the toast via OOB swap.
  - **Structured server logs** — every 5xx writes a structured log line (timestamp / route / SQL / params / exception class + message) so prod operators can grep without guessing. Plain Python `logging` module to stderr; ALB / log aggregator picks it up downstream.
  - **Health check endpoint** — `GET /healthz` returns 200 if the data fetcher can `SELECT 1` against the DB. ALB liveness / k8s readiness consume it.

- [ ] **X.2.k — Release v8.x.0 / v9.0.0.** RELEASE_NOTES, README update reflecting the 4 apps now ship in two dialects, CLAUDE.md updates for the new `serve` CLI group + the architecture, tag + push.

**Deferred to backlog (post-X.2):**
- **Multi-user OIDC auth** (was X.2.phase.2). ALB + IAM Identity Center / Cognito + JWT in `X-Amzn-Oidc-Data` + per-request STS `AssumeRoleWithWebIdentity`. Read-only collapses authorization to a `(identity → [allowed L2 prefixes])` lookup. ~50 lines + ALB config; revisit when there's a customer asking for it.
- **Per-customer caching layer** (CDN / Redis in front of the data fetcher).
- **Embedding context** (X-Frame-Options, signed-URL handoff for iframe contexts).

### X.3 — SQLite as a database dialect (integrator-local persona)

**What.** Add `Dialect.SQLITE` alongside Postgres + Oracle. SQLite is the integrator persona's local-iteration backend per `docs/x_2_design_thoughts.md` — "did I design my YAML right?", run 100% local, no remote DB setup, no Docker, no AWS.

**Why.** The integrator's iteration loop today requires either a live remote DB or a Docker-PG container. Both are heavyweight for "I want to see if my YAML edits look right." SQLite collapses that to a single file in `~/.quicksight-gen/` (or in-memory). App 2 (X.2) becomes the local viewer; SQLite becomes the local store.

**Scope.**

- [ ] **X.3.a — Dialect.SQLITE enum + connection plumbing.** Add to `common/sql/dialect.py`. Connection via stdlib `sqlite3` (no new dep). `demo_database_url: sqlite:///path/to/db.sqlite` parses via `urlparse`.
- [ ] **X.3.b — SQLite schema emit.** `common/l2/schema.py::emit_schema(l2_instance, dialect=SQLITE)` needs the SQL dialect-aware bits Postgres + Oracle already have. JSON metadata via SQLite's `JSON1` extension (built-in since 3.38). Most of `common/sql/dialect.py` switching already accepts an enum; SQLite is a third arm.
- [ ] **X.3.c — Materialized views as truncate-and-select-into.** Per design doc: SQLite has no `CREATE MATERIALIZED VIEW`, so `_l1_invariant_views_template` + `_inv_matviews_template` emit as `CREATE TABLE <prefix>_X AS SELECT ...` for SQLite. `refresh_matviews_sql` becomes `DELETE FROM <prefix>_X; INSERT INTO <prefix>_X SELECT ...` for SQLite. Same data contract, same column shapes — different SQL to populate. Preserves dialect-comparison parity with PG / Oracle.
- [ ] **X.3.d — Seed pipeline against SQLite.** `data apply --execute -c config.sqlite.yaml` works. Hash-locked seed SQL needs a third locked file (`tests/data/_locked_seeds/spec_example.sqlite.sql`) for the contract.
- [ ] **X.3.e — App 2 reads from SQLite.** The X.2.f data fetcher dispatches by dialect; SQLite arm uses `sqlite3` rows. Smoke runner gets `--sqlite` shorthand for the integrator's local-iteration flow.
- [ ] **X.3.f — Documentation.** Handbook addition: "the integrator's local loop" — install, point at SQLite, edit YAML, see it. Probably one walkthrough page.
- [ ] **X.3.g — CI cells (the SQLite column of the X.2 matrix).** **Status: X.3.g.1 (Layer 1) and X.3.g.2 (Audit PDF) both shipped. Layer 1: 8 tests in `tests/unit/test_layer1_query_sqlite.py` exercise `query_matview_rows` / `matview_row_count` / `assert_matview_has_row` / `assert_account_in_matview` against an in-memory `sqlite3` connection. Audit PDF: 8 tests in `tests/audit/test_pdf_sqlite.py` exercise `_query_drift_violations` / `_query_overdraft_violations` / `_query_limit_breach_violations` / `_query_stuck_pending_violations` / `_query_stuck_unbundled_violations` / `_query_supersession` / `_query_executive_summary` against an in-memory SQLite seeded with the L1 invariant matview shapes. The 8 inline date-literal sites (15 lines) in `cli/audit/__init__.py` were routed through the new `date_literal(value, dialect)` helper in `common/sql/dialect.py` — Postgres + Oracle keep the SQL-standard `DATE 'YYYY-MM-DD'` form (byte-identical between them); SQLite gets a plain `'YYYY-MM-DD'` text literal (the `CAST('YYYY-MM-DD' AS DATE)` form coerces to INTEGER 2030 in SQLite, silently breaking comparisons). HTMX cell still pending — lands when X.2.h ships.** Three new jobs (or three matrix-axis entries on existing workflows):
  - `e2e-sqlite-layer1` — Layer 1 matview-check unit suite parametrized over the SQLite dialect. Mirrors what `_layer1_query.py` already does for PG / Oracle.
  - `e2e-sqlite-audit` — Audit PDF generation against SQLite seed; same `tests/audit/` shape as PG / Oracle audit tests.
  - `e2e-htmx-sqlite` — HTMX dialect Layer 2 against SQLite (the third DB cell of the X.2.h matrix; lights up when both X.2.h and X.3 land).
  - Cheapest cells in the matrix — SQLite needs no DB instance, no AWS, no Docker. Runs anywhere, finishes fast. The integrator-local persona's iteration loop becomes the CI's iteration loop too.
  - Updates `e2e-against-testpypi` to include the SQLite arm of the 4-way cross-tool agreement (`expected == PDF == HTMX` for SQLite — QS column drops because QS doesn't read SQLite).

**Notes on portability:**
- **Recursive CTEs.** Investigation matviews use `WITH RECURSIVE`; SQLite supports this since 3.8.3 — should port cleanly.
- **CONCAT operator.** PG / Oracle / SQLite all use `||`. Same.
- **Window functions.** SQLite supports them since 3.25; matview refresh queries port.
- **Date arithmetic.** SQLite uses `date()` / `datetime()` functions. Already dialect-aware in `common/sql/dialect.py` per Phase P.

### X.4 — App 1: YAML editor (the institution-shape helper)

**What.** Per `docs/x_2_design_thoughts.md`: a web-based editor for the L2 institution YAML. Top-of-page force-directed view of the L2 + click-to-filter + filter toggles + cards for editing each L2 entity. Hitting save on a card PUTs the entity, server applies + cascades; affected scope reloads via `HX-Trigger: l2-cascade-reload`.

**Why.** Hand-editing the L2 YAML is the integrator's primary friction. The validator is strict (good) but the feedback loop is "edit YAML → run schema apply → maybe see error → fix → repeat." A live editor with the L2 force-directed map + form-based entity editing collapses that loop.

**Persona match.** The integrator. Local iteration loop, paired with X.3 SQLite for "edit YAML, see it apply against local data, see the dashboard render in App 2."

**Architecture (per design doc):**
- Same Starlette process as App 2 OR separate process — TBD when X.4 starts. Same-process for the dev tool (X.4 era); split when phase.2 auth lands (App 1 has writes, needs different auth posture than App 2's read-only).
- REST shape: `/l2_shape` (force-directed view), `/l2_shape/accounts`, `/l2_shape/rails`, `/l2_shape/chains`, `/l2_shape/transfer_templates`, `/l2_shape/theme` — CRUD per entity type.
- Cascade mechanism: PUT entity, server returns 200 + new body. When the change rippled (rename rewrites references, etc.) the response ALSO emits `HX-Trigger: l2-cascade-reload`. Client-side the L2-shape view + force-directed canvas listen for that event and `hx-get` themselves. No client-side cascade computation.
- Force-directed visual is the **shared primitive** with App 2 — already in `common/tree/visuals.py::ForceGraph` from the X.2 spike. App 1's editor canvas + App 2's dashboard visual + App 1 ETL coverage overlay are three uses of the same renderer.

**Scope (placeholder — fill in detail after X.2 lands):**

- [ ] **X.4.a — Read-only L2 viewer.** `/l2_shape` GETs the L2, renders the force-directed map. Click a node → filter to the connected subgraph. Filter toggles for entity categories. Reset filters button. No edits yet — viewer first to confirm the visualization carries the L2 model.
- [ ] **X.4.b — Per-entity card view.** GET `/l2_shape/accounts/:id` returns one entity's card (read-only). All fields visible. **Discipline (pre-X.6):** card field labels + helper text come from `common/l2/primitives.py` field docstrings, NOT hand-written in the editor template. Same source X.6's mkdocstrings expansion will eventually consume — keeps editor + docs aligned by construction.
- [ ] **X.4.c — Edit + PUT + cascade.** Card flips to edit mode, save PUTs. Server applies + recomputes references + writes back the YAML. Response carries `HX-Trigger: l2-cascade-reload` when the change rippled.
- [ ] **X.4.d — Live validation feedback.** PUT validates against the existing strict L2 validator; failure returns 400 + the validator error inline as a swap fragment under the form.
- [ ] **X.4.e — Force-directed integration.** Editor canvas at the top stays in sync with edits via the same cascade-reload event.
- [ ] **X.4.f — CLI integration.** `quicksight-gen edit -c config.yaml --l2 path/to/L2.yaml` opens the local server + browser tab.
- [ ] **X.4.g — Layer 2 e2e.** Playwright tests covering the edit-cascade flow + force-directed canvas updates.
- [ ] **X.4.h — Hot reload / rebuild button.** Integrator's loop is "edit YAML → see it in App 2." Today: change YAML → re-run `data apply --execute` → restart App 2. Painful. App 1 surfaces a "Rebuild" button that: (1) kills the App 2 process, (2) re-runs `data apply -c config.yaml --execute` (whichever backing DB the config.yaml points at — SQLite for the integrator-local persona, Postgres or Oracle when the operator runs against a remote DB), (3) restarts App 2, (4) reloads App 2's open tab once it's back up. UX shape will be obvious once the editor + ETL pieces land — defer detailed spec to when X.4 starts. Note for that future moment: the user can keep reading the YAML in App 1 while the rebuild runs, so the perceived downtime is small.

### X.5 — App 1 ETL helper

**What.** Per `docs/x_2_design_thoughts.md`: data-loading helper inside App 1. Two paths:
1. Build up SQL load steps (saved to `etl.yaml`, NOT `config.yaml` — preserves the V.1.b allowlist boundary).
2. Run the synthetic data generator (existing `data apply` pipeline, exposed via the App 1 UI).

Plus: load repeatedly up to a defined date for time-travel simulations of business-over-time. View the force-directed L2 with data-coverage overlay (color/saturation by whether rows exist for each L2 primitive).

**Why.** Closes the loop for the ETL engineer persona: "did I load all the data I expected to?" The data-coverage overlay on the force-directed view is the integrator's answer to "what's covered?"

**Scope (placeholder — fill in detail after X.4 lands):**

- [ ] **X.5.a — `etl.yaml` schema + loader.** New file alongside `config.yaml`, separate strict allowlist. Holds list of SQL load steps with `--end-date` parameter support.
- [ ] **X.5.b — `data apply --end-date <ISO date>`.** Trivial in `emit_full_seed`: skip records past the cutoff. Punt on `--density-factor` for perf testing — note in PLAN, ship when needed.
- [ ] **X.5.c — App 1 ETL UI.** SQL step builder cards, run button, progress feedback, link to view results in App 2.
- [ ] **X.5.d — Force-directed coverage overlay.** Color/saturation by row-count per L2 primitive. Shared force-directed visual from X.2 + a second data fetcher that returns coverage stats.
- [ ] **X.5.e — Time-travel re-loads.** Run loader repeatedly with advancing `--end-date` to simulate business-over-time. UI exposes the date stepper.

### X.6 — Model-driven docs (drift reduction)

**What.** Drive the documentation site from the same data model that drives the renderers. Today the L2 entity reference, visual reference, dataset reference, and per-sheet walkthroughs are hand-written in `docs/` Markdown — each is a place documentation can drift from code. Phase X.6 collapses those into `common/l2/primitives.py`, `common/tree/visuals.py`, `DatasetContract`, and the tree's `Sheet.description` / `Visual.subtitle` strings as the single source of truth, with mkdocs-macros + mkdocstrings rendering them into the docs site at build time.

**Why.** Documentation drift is the failure mode this codebase has hit repeatedly (X.1.h ETL hallucination; persona-leak sweeps in Q.4 / Q.5; CLI-invocation drift caught by X.1.h.B). Each fix has been a one-off audit + handwritten correction. Auto-generation makes drift structurally impossible for the surfaces it covers — when the field docstring changes, the docs page changes the same commit.

**Scope:**

- [ ] **X.6.a — mkdocstrings expansion.** Auto-generate L2 entity reference (`common/l2/primitives.py` — Account, Rail, Chain, TransferTemplate, etc.) and visual reference (`common/tree/visuals.py` — KPI, Table, BarChart, Sankey, ForceGraph). Per-class page with docstring + field table. Replaces today's hand-written `docs/reference/l2-spec.md` + per-visual handbook callouts.
- [ ] **X.6.b — Custom mkdocs-macros plugin: tree → walkthrough scaffolds.** Reads sheet/visual descriptions from each app's tree (`apps/<app>/app.py` builds the tree; the plugin walks it). Emits per-sheet walkthrough scaffold with the sheet's own `description` as the lede + each visual's `subtitle` as a section. Hand-written prose can extend the scaffold but the model-derived parts can't drift.
- [ ] **X.6.c — Auto dataset reference.** `DatasetContract` lists columns + types + (often) shape. Generates per-dataset reference page. Replaces today's hand-written column lists in `docs/data-contract/`.
- [ ] **X.6.d — Auto config reference.** Config dataclass + `etl.yaml` schema (X.5.a) + L2 schema validators → reference pages for each config file the user touches. Field docstrings + valid-value enums → docs.
- [ ] **X.6.e — Live-embed App 2 fragments in mkdocs pages.** Because X.2.i mounts `/docs` in App 2's Starlette process, mkdocs pages can include `<iframe src="/dashboards/.../sheets/.../visuals/...">` fragments that render live. Doc walkthrough shows the actual visual it describes against the demo data, not a screenshot that goes stale. Use sparingly (page-load weight) — per-app handbook overview is the natural home.
- [ ] **X.6.f — Migrate hand-written walkthroughs.** Sweep `docs/walkthroughs/` to use the X.6.b scaffolds. Hand-written content lives in extension blocks; model-derived content comes from the tree. Captures the prose that's still useful while killing the drift surface.
- [ ] **X.6.g — README + handbook positioning sweep** (folded from former X.9). The 4 apps × 2 dialects × 3 DBs surface needs a fresh top-of-funnel pitch. README + handbook home pages should communicate what the tool now does — not what it did pre-X.2.
- [ ] **X.6.h — Drift CI gate.** pytest test that fails if any docs page references a model attribute (field, class, enum value) that no longer exists. Same shape as X.1.h.B's CLI invocation checker — extracts model references from docs, asserts they resolve. Catches the regression class even when the auto-generation is bypassed for hand-written prose.

- [ ] **X.6.i — Source-of-truth discipline sweep.** X.2.g + X.4.b only call out sheet description + visual subtitle + L2 entity field labels. Audit every analyst-facing string on tree primitives: parameter labels, calc field display names, drill action labels, dataset display names, theme token names, etc. Anything that appears in BOTH the rendered surface and the docs is a candidate for "lives on the tree, docs reads from it." Avoid the long-tail cleanup list that would otherwise hit at X.6 time.

- [ ] **X.6.j — Self-host handbook guide.** New handbook page covering the App 2 deployment story end-to-end — Dockerfile recipe (multi-stage: tailwind build → wheel install → uvicorn entry), env var contract (`QS_GEN_*` vars consumed at boot), reverse-proxy notes, ALB / phase.2 OIDC pointers. Operators going from local-iteration → production-self-host should not have to grep the codebase. Companion: a working Dockerfile in the repo at `deploy/Dockerfile` (referenced from the guide).

**Threads from X.2-X.5 already in place** (so X.6 has source material to consume):
- X.2.g — sheet `description` + visual `subtitle` are the docs source of truth from day one.
- X.4.b — L2 entity card labels come from `common/l2/primitives.py` field docstrings, not hand-written in the editor.

### X.7 — Cloud CI cost optimization
- Note there's an issue with auto creating quicksight datasources if they need to traverse a VPC endpoint. The work is parked on branch hotfix-v8.7.4-vpc-connection-arn.
Out of active development iterations — manage cloud spend deliberately. Two-tier CI: a fast loop on every push that touches no AWS, and a gated full-e2e tier triggered by tag pushes (release gate, auto-fired), manual `workflow_dispatch`, or a weekly cron.

- [ ] **X.7.a — Baseline the spend.** ~5 min in AWS Cost Explorer for the last 30 days; record the per-resource line items in this entry so X.7.b/c are sized against actual numbers. Without this we're guessing whether the right answer is "stop Oracle when idle" or "tear everything down between runs."
  - Answer: I think start/stop is fine for now, keeps the connect strings pretty static

- [ ] **X.7.b — Fast loop on every push:main.** pytest + pyright + Docker-PG + Docker-Oracle (both dialects already containerized) for everything that doesn't need QuickSight: unit + integration + contract + docs build + audit PDF generation. No AWS resources touched. This is the per-commit feedback loop.

- [ ] **X.7.c — Gated full e2e on three triggers.** Combined behavior: everything from X.7.b + start AWS RDS (PG + Oracle, both pre-existing) + warm + deploy QS + run true e2e + stop RDS + (if a tag) push to TestPyPI then prod approve. Trigger sources: (1) **on tag push** — release pipeline auto-fires it as the gate before TestPyPI publish (no manual step; failing e2e blocks the release); (2) **manual workflow_dispatch** — operator on-demand verification; (3) **weekly cron** — catches AWS breaking us between releases. Use `start-db-instance` / `stop-db-instance` cycling instead of provision-and-terminate (RDS billing pauses when stopped except storage; ~5 min start vs ~10-20 min provision). Aurora Serverless v2 already scales to 0.5 ACU min — verify whether scale-to-0 is configurable. The DB should be clean at start anyway, so cycling pre-existing instances is equivalent to fresh provision for our purposes.
  - Answer: Scale to zero is 100% doable, I'd just recommend start /stopping oracle. I'll reconfigure the scaling once we're here. For the start/stop I'll just need to know the additional permission grants.
  - **Concurrency redesign — fold in observed races (May 2026).** The current `e2e.yml` has two structural defects worth fixing as part of the X.7 redesign rather than separately: (1) **within-run race** — `e2e-pg-api` and `e2e-pg-browser` run in parallel within a single workflow run and both `schema apply` against the same `spec_example_*` tables, racing on DROP/CREATE. Caused the X.1.b L2FT cascade test to see an empty matview at assertion time. Fix: make `e2e-pg-browser` `needs: e2e-pg-api` (sequential within a run) OR give each job its own L2 instance prefix so the schemas don't collide. (2) **cross-run cancellation** — concurrency group `e2e-pg` with `cancel-in-progress: false` interacts badly with rapid push+dispatch sequences (observed 1-second cancellation when a push:main run's cleanup overlapped a queued workflow_dispatch). Fix: move concurrency to the workflow level (one run at a time, period) + simplify the per-job concurrency groups. Both are CI-shape changes that fit X.7's scope.

### X.8 - Ask for configuring the row counts and date range for the data seeding

### X.9 — _(folded into X.6.g)_

The README + handbook positioning sweep is now X.6.g, since it shares the model-driven-docs concern.

---

## Phase Y — SQL-level parameter pushdown (QuickSight + App2 convergence)

**Why.** Today QS runs every visual as a Direct Query that pulls the *entire* dataset SQL result set, then narrows in QS's process via analysis-level FilterGroups + calc fields. Datasets are parameter-blind — the database has no idea the analyst's slider is set to 5 — so DB indexes never apply to the analyst's filter, and a 1k-row matview ships in full per visual. App2 (built the database-native way) uses bind variables that prune at SQL-execute time. Result: two implementations of one filter intent, drifting each other, AND a perf hit on QS that's only growing as the matviews grow.

**Architectural shift.** The dataset SQL becomes the single source of truth — `<<$paramName>>` substitution for QS (literal at query time via `MappedDataSetParameters`); `:param_<name>` bind vars for App2 (one-line preprocessor in `_sql_executor`); analysis-level FilterGroups + calc fields go away (or stay only as decorative chrome where useful). What we lose: the QS Filters pane "active filters" list + per-visual filter icon — neither persona uses these today; the slider/dropdown control widget (the actual analyst input) is unchanged. What we gain: one SQL truth → drift gone; database does the filtering → orders-of-magnitude fewer rows on the wire; the calc field workarounds (window functions evaluated in QS engine) become real columns the database computes once.

**Branch.** `phase-y-sql-pushdown` off `main` (current main, includes Phase X work merged through this point). Phase X's in-flight work (X.2.g.3 dataset `app2_sql=` blocks, etc.) gets *replaced* by Phase Y's convergent path — when Y lands, Phase X's branch rebases on top and drops the now-redundant `app2_sql=` plumbing.

### Y.1 — Spike: prove the mechanism on Volume Anomalies

Single sheet, single slider (`pInvAnomaliesSigma`), no calc fields. **PG-only spike** — SQLite + Oracle dialect coverage lands at Y.7 once the mechanism is proven; the spike is about verifying the QS substitution + App2 bind translation round-trip on the most-used dialect, not about full dialect spread. End-state: QS sees `<<$pInvAnomaliesSigma>>` substituted to the literal value; PG `pg_stat_statements` shows the WHERE clause hitting the database; row counts to QS drop by orders of magnitude; App2 still works via the bind preprocessor.

#### Spike-time finding — visual-scoped filters need the companion-dataset workaround

Reading the Investigation app's σ wiring before touching code surfaced a design constraint not in the PLAN's premise: σ is `SELECTED_VISUALS`-scoped (KPI + Table only) — the distribution chart **deliberately stays unfiltered** so the analyst can see the full population shape and read "where my threshold sits" against the bucket bars. Pushing σ into the dataset SQL with `<<$pInvAnomaliesSigma>>` would filter every visual reading that dataset, including the distribution chart — a visual regression.

**The convention going forward:** filters fall into three scope categories, and the SQL-pushdown decision differs per category.

| Scope            | Pushdown shape                                                                                                          |
|------------------|-------------------------------------------------------------------------------------------------------------------------|
| `scope_sheet(...)` (every visual) | Clean pushdown into the dataset SQL via `<<$paramName>>` — one SQL, every visual filters identically. Most filters fall here. |
| `scope(fg, [v1, v2])` (SELECTED_VISUALS) | **Companion-dataset pattern**: filtered visuals point at the parameter-bearing dataset; unfiltered visuals point at a separate dataset reading the same source (no parameter). Both wrap the same matview; the second dataset has no `<<$pName>>`.   |
| Cross-sheet      | TBD — will surface in Y.2's L1 sweep.                                                                                   |

The σ case is the spike's load-bearing example of the SELECTED_VISUALS pattern. Y.1 ships with the companion dataset built; Y.2 audits each existing FilterGroup for scope before pushdown and applies the pattern as needed.

#### Steps

- [x] **Y.1.a — Branch off main as `phase-y-sql-pushdown`.**
- [x] **Y.1.b — Convert `pInvAnomaliesSigma` to a dataset parameter in `build_volume_anomalies_dataset`.** Dataset declares the parameter with default value; SQL gains `WHERE 1=1 AND z_score >= <<$pInvAnomaliesSigma>>`. KPI + Table read this dataset.
- [x] **Y.1.b.companion — Introduce `build_volume_anomalies_distribution_dataset` — companion dataset reading the same matview WITHOUT the σ parameter.** Distribution chart re-binds to this so it stays unfiltered. Validates the SELECTED_VISUALS workaround pattern Y.2 will reuse.
- [x] **Y.1.c — Add `MappedDataSetParameters` bridging analysis param → dataset param.** The analysis-level `pInvAnomaliesSigma` flows down to the dataset's parameter via the mapping.
- [x] **Y.1.d — Drop `FG_INV_ANOMALIES_SIGMA` analysis-level FilterGroup from the Investigation app.** Confirm slider widget still drives the value.
- [x] **Y.1.e — App2 SQL preprocessor — `<<$paramName>>` → `:param_paramName`.** Two regex passes (quoted form first, unquoted second) in `translate_qs_dataset_params` — runs before `rewrite_placeholders_for_dialect` in both sync + async executors. End-to-end SQLite test confirms the QS→bind round-trip filters at the database.
- [x] **Y.1.f — Drop the X.2.g.3.b `app2_sql=` block.** N/A on this branch — the X.2.g.3.b commit lives on `phase-x-2-g-investigation-app2` awaiting Y.8 rebase.
- [x] **Y.1.g — Re-lock affected hash tests + JSON-emit tests.** `test_filter_groups_in_expected_order` (FG count 12 → 11), `test_investigation_datasets_in_expected_order` (7 → 8), `test_investigation_datasets_declared_in_analysis` (added DS_INV_VOLUME_ANOMALIES_DISTRIBUTION), `test_money_trail_dataset_reads_from_matview` (index 2 → 3), `test_fanout_sheet_serializes_to_aws_json` (FilterGroups count 12 → 11). Two stale sigma tests replaced with five Y.1-shape assertions covering pushdown SQL, dataset parameter declaration, mapped bridge, and companion-dataset binding.
- [x] **Y.1.h — Deploy to Aurora PG; capture `pg_stat_statements` baseline + post-Y delta.** **VERIFIED LIVE.** Pre-Y baseline (historical): `SELECT * FROM sasquatch_pr_inv_pair_rolling_anomalies` — 6 calls × 6213 rows = 37,278 rows scanned, mean 192.5ms. Post-Y dashboard load issued THREE distinct queries (visible via `pg_stat_statements` with QS provenance comments tagging visualId): (1) Table — `... WHERE $2=$3 AND z_score >= $4`, 7 calls × 133 rows avg = 933 rows total, mean 19.6ms. (2) KPI — `SELECT COUNT(*) ... WHERE $1=$2 AND z_score >= $3`, 7 calls × 1 row = 7 rows, mean 19.8ms. (3) Distribution — `SELECT "z_bucket", COUNT(*) ... FROM "Investigation Volume Anomalies — Distribution"` — NO `WHERE` clause (companion dataset bound), 1 call × 5 rows, mean ~20ms. The `$2=$3` is the prepared-statement cache form of our `WHERE 1=1` template; `$4` is the σ literal QS substitutes via `MappedDataSetParameters`. **Companion dataset binding works — distribution chart gets no σ filter as intended.**
- [ ] **Y.1.i — Deploy to Oracle; capture `v$sqlstats` baseline + post-Y delta.** Same shape as PG. **Operator action — Oracle path not exercised in this session.**
- [x] **Y.1.j — Performance verdict.** **WIN, with receipts.** ~10× latency reduction (192.5ms → ~20ms mean). ~48× row reduction for the Table visual (6213 → ~130 rows on the wire). ~6213× row reduction for the KPI's COUNT(*) (full scan → 1 row). Distribution chart preserves its full-population view (~5 z_bucket aggregate rows post-server-side GROUP BY). The spike validates the entire Phase Y premise: dataset-SQL-level pushdown moves the work from QS's in-memory engine to the database, which is the right place for it.
- [x] **Y.1.k — Cascade dropdown probe.** **CLOSED. Verdict: QS-side limitation, not fixable from our wiring.** URL params populate widget state + analysis-level filter values, but `MappedDataSetParameters` bridges into dataset-level `<<$paramName>>` substitution do NOT fire on initial URL load. Bridge fires only on manual widget interaction. Y.1.p tried three reference shapes (B1 filter-with-param, B2 echo-column + filter, B3 calc field with `${param}` expression — LuisBorrego's community workaround) — all emit the right SQL via pg_stat_statements but `$N` binds remain at sentinel defaults until manual interaction. Y.1.p attempts reverted (commits 7aa0602 / 66524a4 / 0a3ded6); cascade restored to pre-Y.1.p OR-cascade WHERE shape that works under manual interaction. Quirks log entry 2.1 rewritten with the precise mechanism.
- [x] **Y.1.l — Spike commit + spike-time findings captured.** Branch is ready for Y.2 sweep. Findings:
    1. **SELECTED_VISUALS scope = companion dataset.** Pre-spike PLAN missed this; the σ filter is per-visual scoped (KPI + Table only, not Distribution). Solution shipped in Y.1.b.companion: a second dataset wraps the same matview without the parameter; the unfiltered visual binds to it. Y.2 audits each FilterGroup's scope before pushdown.
    2. **Cascade verdict reversed mid-spike.** Initial pass: "already proven via L2FT". User correction: L2FT metadata cascade does NOT work right in production today — likely a "layer filter violation" between dataset param + MappedDataSetParameter bridge + per-visual filter scope that the X.1.g browser e2e didn't catch (e2e validated dropdown UI shape, not data filter effect). Y.1.k REOPENED — cascade behavior under SQL pushdown is unverified, not proven. Y.2's L2FT sweep needs the live cascade behavior pinned down first.
    3. **App2 preprocessor is one regex pass.** `translate_qs_dataset_params` handles quoted (string) + unquoted (numeric) forms; the existing PG `:name` → `%(name)s` rewrite picks up the translated binds without changes.
    4. **Mechanism verified at SQLite layer.** End-to-end test in `test_html_sql_executor.py` confirms `<<$pName>>` placeholder + URL `param_pName=N` produces the right rows via real aiosqlite execution. PG + Oracle verification deferred to Y.1.h/i.
    5. **Live perf numbers (Y.1.h/j) deferred to operator deploy.** Mechanism is proven; the verdict on whether QS Direct Query actually pushes the WHERE to Postgres in the wire goes pending the next Aurora roll.

#### Y.1.m — Control-wiring fix (multi_valued type validator) — DONE

**Outcome.** L2FT cascade was originally broken because `pL2ftMetaValue` was `multi_valued=True` bound to `add_parameter_text_field` (single-string control). X.1.b swapped LinkedValues dropdown → text-field but didn't flip multi_valued. Text-field commits to multi-valued params silently revert to default.

- [x] **Y.1.m.1 — Type-system enforcement landed.** `ParameterTextField.__post_init__` rejects `parameter.multi_valued=True` at construction. Caught all 3 L2FT offenders on first run.
- [x] **Y.1.m.2 — Apps fixed.** `pL2ftMetaValue` / `pL2ftChainsMetaValue` / `pL2ftTtMetaValue` flipped to `multi_valued=False`; matching dataset `pValues` params (×4) flipped to `SINGLE_VALUED`. Manual interaction now commits cleanly.
- [x] **Y.1.m.3 — Tests updated + 1376 unit + JSON green.** Commit `582a900`.

#### Y.1.n — Cross-sheet / cross-dashboard URL-param drill re-evaluation — CLOSED (verdict: not viable for dataset-bridged params)

**Investigated via Y.1.p:** three analysis-level reference shapes attempted to wake the `MappedDataSetParameters` bridge on URL load. All failed identically — bridges fire only on manual widget interaction.

**Verdict on cross-app drills:**

- ✅ Cross-app URL stamping works for **analysis-level params** (`CategoryFilter` / `NumericRangeFilter` / `TimeRangeFilter` taking `${param}` directly).
- ❌ Cross-app URL stamping does NOT work for **dataset-bridged params** — the L2FT-cascade pattern. Bridge stays at sentinel default until manual interaction.

**Implications:**

- K.4.7's dropped cross-app drills that target dataset-bridged params (the L2FT metadata cascade specifically) **stay dropped** — no JSON shape on our end fixes this.
- K.4.7-style drills that target purely analysis-level params **could** be re-enabled (pending separate verify) — they don't depend on the bridge.

#### Y.1.p — Cascade-via-bridge attempts — REVERTED

Three reference shapes tried, all reverted:

- **B1** (`3c8af5c`, reverted in `66524a4`): replace OR-cascade WHERE with `_meta_match_value` CASE projection; analysis-level `CategoryFilter.with_parameter` on the column. SQL emitted with `_meta_match_value = $N` but `$N` stayed at sentinel default.
- **B2** (folded into B1 commits): + `_meta_key_echo` echo column + tautological filter using `pL2ftMetaKey`. Same outcome.
- **B3** (uncommitted; deployed via `berrt932m`, code discarded): replace param-as-filter-value with analysis-level `CalcField` whose expression references `${param}` (LuisBorrego's pattern). pg_stat_statements showed the calc fields pushed down as `CASE WHEN ... = $N THEN ... END IN ($M)` but `$N` still bound to sentinel on URL load.

**Diagnostic confirmed via `scripts/qs_substitution_probe.py`:** the SQL was correct in every iteration (direct PG execution returned 49 rows for the URL-1 inputs); the QS-side bind chose the sentinel default until the analyst manually committed.

**Y.1.q — quirks log + memory update done.** Entry 2.1 rewritten + worst-footgun framing updated (`d8f5c5e`).

### Y.2 — Sweep simple-filter datasets (one sub-task per dataset)

Spike-proven pattern applied across every sheet that has SQL-pushdownable filters today. Each sub-task is one dataset: convert FilterGroup → dataset parameter, drop `app2_sql=` if any, re-lock hash tests, verify deploy.

**Constraint baked in (post-Y.1.p verdict):** cross-app URL drills cannot pre-narrow a dataset-bridged-param cascade on initial load. SQL pushdown via dataset parameters works fine for **manual interaction** (Y.1's σ slider). Y.2 sweeps target the manual-interaction perf win — not the cross-app handoff. The L2FT cascade stays on its restored OR-cascade WHERE shape (works under manual interaction; URL load returns the unfiltered universe — accepted UX cost).

**Per Y.1 finding, audit each FilterGroup's scope first.** `scope_sheet(...)` filters get clean pushdown; `SELECTED_VISUALS` filters need the Y.1.b.companion pattern (companion dataset without the parameter for the unfiltered visuals); cross-sheet filters TBD.

- [x] **Y.2.a — Money Trail dataset: `pInvMoneyTrailRoot` + `pInvMoneyTrailMaxHops` + `pInvMoneyTrailMinAmount`.** Three params on one dataset.
    - [x] **Y.2.a.1 — Pushdown SQL.** `build_money_trail_dataset` now declares 3 dataset parameters (1 String + 2 Integer) bridged from analysis params via `mapped_dataset_params`. SQL gains `WHERE 1=1 AND e.root_transfer_id = <<$pInvMoneyTrailRoot>> AND e.depth <= <<$pInvMoneyTrailMaxHops>> AND e.hop_amount >= <<$pInvMoneyTrailMinAmount>>`.
    - [x] **Y.2.a.2 — Roots-companion dataset (spike-time finding).** `build_money_trail_roots_dataset` — unfiltered `SELECT DISTINCT root_transfer_id FROM <prefix>_inv_money_trail_edges`, no parameters. Required because the chain-root dropdown reads from `LinkedValues.from_column(...root_transfer_id)`; once the main dataset filters by `<<$pInvMoneyTrailRoot>>`, the dropdown's own DISTINCT-roots query inherits the WHERE (sentinel default → 0 options). Dropdown's `LinkedValues` repointed at the companion. Same shape as Y.1.b.companion / K.4.8k.
    - [x] **Y.2.a.3 — Drop the three FilterGroups.** `FG_INV_MONEY_TRAIL_{ROOT,HOPS,AMOUNT}` removed from `apps/investigation/{app,constants}.py`. `FG_INV_MONEY_TRAIL_WINDOW` (TimeRangeFilter, not parameter-bound) stays — Y.2.f's job.
    - [x] **Y.2.a.4 — Tests re-locked.** `test_filter_groups_in_expected_order` (FG count 11 → 8), `test_investigation_datasets_in_expected_order` (8 → 9 datasets), `test_investigation_datasets_declared_in_analysis` (added DS_INV_MONEY_TRAIL_ROOTS), `test_fanout_sheet_serializes_to_aws_json` (FG count 11 → 8). Three stale K.4.5 FG-shape tests replaced with five Y.2.a-shape assertions covering pushdown SQL, dataset parameter declaration, mapped bridges, companion dataset, and dropdown's companion-binding. Companion-dataset roots-only contract test added.
    - [x] **Y.2.a.5 — Drop X.2.g.3.c `app2_sql=` block.** N/A on this branch — lives on `phase-x-2-g-investigation-app2` awaiting Y.8 rebase, same as Y.1.f.
    - [x] **Y.2.a.6 — Full unit + JSON suite green.** 1376 unit + JSON tests pass.
    - [x] **Y.2.a.7 — Aurora deploy + verify.** Deployed to Aurora PG (commit `6db9f3e` — combined with Y.2.b deploy). All 4 dashboards `CREATION_SUCCESSFUL`. API e2e green (48/48). Investigation browser e2e green (28 passed, 9 skipped — pre-existing drill / cross-app skips). Hands off to user for manual `pg_stat_statements` walk + dashboard interaction.
- [x] **Y.2.b — Account Network dataset: `pInvANetworkAnchor` + `pInvANetworkMinAmount`.** Two params on one dataset.
    - [x] **Y.2.b.1 — Pushdown SQL.** `build_account_network_dataset` now declares 2 dataset parameters (1 String + 1 Integer) bridged from analysis params via `mapped_dataset_params`. SQL gains `WHERE 1=1 AND (source_display = <<$pInvANetworkAnchor>> OR target_display = <<$pInvANetworkAnchor>>) AND hop_amount >= <<$pInvANetworkMinAmount>>` — broad anchor narrow + min-amount cutoff.
    - [x] **Y.2.b.2 — No companion needed for the dropdown.** K.4.8k's `DS_INV_ANETWORK_ACCOUNTS` already plays the unfiltered-companion role for the anchor dropdown's `LinkedValues` source. The Money Trail-style chicken-and-egg problem doesn't apply.
    - [x] **Y.2.b.3 — Drop FG_INV_ANETWORK_ANCHOR + FG_INV_ANETWORK_AMOUNT.** Two of four account-network FGs go (broad anchor narrow + min-amount cutoff now in dataset SQL). The two directional FGs (`FG_INV_ANETWORK_INBOUND` / `FG_INV_ANETWORK_OUTBOUND`) stay — they partition the pre-narrowed anchor-touching set into per-Sankey directions; Y.3.b will push those into SQL CASE expressions too.
    - [x] **Y.2.b.4 — Drop is_anchor_edge calc field.** Only consumer was the now-dropped `FG_INV_ANETWORK_ANCHOR`; ds_anet's SQL pushdown means every row IS anchor-touching by construction. Y.3.b's calc-field-pushdown TODO is one item smaller.
    - [x] **Y.2.b.5 — Tests re-locked.** `test_filter_groups_in_expected_order` (FG count 8 → 6), `test_fanout_sheet_serializes_to_aws_json` (FGs 8 → 6, calc fields 5 → 4). Three stale K.4.8 FG/calc-field tests dropped (`is_anchor_edge` + anchor + amount FG shapes); five Y.2.b-shape assertions added covering pushdown SQL, dataset parameter declarations + sentinel default, mapped bridges, and the orphaned-calc-field removal.
    - [x] **Y.2.b.6 — Drop X.2.g.3.d `app2_sql=` block.** N/A on this branch — same as Y.1.f / Y.2.a.5.
    - [x] **Y.2.b.7 — Full unit + JSON suite green.** 1374 unit + JSON tests pass.
    - [x] **Y.2.b.8 — Aurora deploy + verify.** Deployed in same roll as Y.2.a (commit `6db9f3e`). Initial deploy surfaced a SQL bug — `source_display` / `target_display` are SELECT-list aliases over concat expressions, not real matview columns; PG / Oracle / SQLite all evaluate WHERE before SELECT, so the alias isn't visible to a same-query WHERE. PG raised `UndefinedColumn` at execute time; QS rendered the visuals blank with no banner. Fixed by wrapping projection in a CTE so the alias is in scope by the time WHERE runs. Re-deployed clean. **Spike-time finding logged**: the existing P.9f.e smoke verifier (`tests/integration/verify_dataset_sql.py`) caught the bug in seconds, but it was a CLI script not pytest-collected — so the e2e suite passed despite a deployed SQL exception. Wired it as a pytest test (`tests/e2e/test_dataset_sql_smoke.py`) so future Y.2.x mistakes auto-fail; 37 datasets parametrized one test each. 65 passed, 9 pre-existing skips, 0 failures on re-run.
- [ ] **Y.2.c — L2FT Rails dataset: rail / status / bundle / metadata cascade.** Unblocked. The metadata cascade stays on the existing OR-cascade WHERE (works under manual interaction; URL load returns full universe — accepted per Y.1.p verdict). Other filters (rail / status / bundle) are good pushdown candidates and follow the Y.1 spike pattern.
- [ ] **Y.2.d — L2FT Chains dataset: parent chain + completion + metadata cascade.** Same constraint as Y.2.c.
- [ ] **Y.2.e — L2FT Transfer Templates dataset: template + completion + metadata cascade.** Same constraint as Y.2.c.
- [ ] **Y.2.f — L1 universal date range across every L1 dataset.** `date_from` / `date_to` move from analysis-level TimeRangeFilter into dataset SQL `WHERE posted_at BETWEEN <<$pDateFrom>> AND <<$pDateTo>>` pattern. Highest-impact change for L1 perf.
- [ ] **Y.2.g — L1 per-sheet filter controls** (Drift / Overdraft / Limit Breach / Today's Exceptions / Daily Statement / Transactions / Pending Aging / Unbundled Aging / Supersession Audit). Per-sheet sub-bullet as we hit them.
- [ ] **Y.2.h — Executives datasets review.** Verify whether any Exec sheet has filters that benefit; sweep what does. Likely a thin pass.

### Y.3 — Push calc fields down to dataset SQL as real columns

Calc fields exist in the QS analysis layer because QS could evaluate them in its engine. Pushing them into the dataset SQL as window functions in CTEs makes them real columns the database computes once at query time. Visuals reference real columns; the calc field declarations vanish from the analysis tree; both QS and App2 see one shape.

- [ ] **Y.3.a — Recipient Fanout: `distinct_senders` window column.** `WITH base AS (...), enriched AS (SELECT base.*, COUNT(DISTINCT sender_account_id) OVER (PARTITION BY recipient_account_id) AS distinct_senders FROM base) SELECT * FROM enriched`. The HAVING-via-X.2.g.3.e becomes a plain `WHERE distinct_senders >= <<$pInvFanoutThreshold>>` (Y.2-style). Drops `CF_INV_FANOUT_DISTINCT_SENDERS` calc field.
- [ ] **Y.3.b — Account Network: `is_inbound_edge` + `is_outbound_edge` + `counterparty_display` window columns.** Each calc field's expression converts to a SQL CASE / window function in the dataset SQL. Drops the three remaining calc field declarations (Y.2.b already dropped `is_anchor_edge` as orphaned).
- [ ] **Y.3.c — Volume Anomalies: any calc fields used by the ranked table** (e.g. `z_score_max`). Same pattern.
- [ ] **Y.3.d — Update visuals to reference real columns instead of calc fields.** `Measure.max(ds, calc_field)` becomes `Measure.max(ds["distinct_senders"])` etc. The Table-aggregated wrap (X.2.g.4 if we land it pre-Y) reads them naturally.
- [ ] **Y.3.e — Drop now-unreferenced CalcField declarations from the analysis tree.** Sweep `apps/investigation/app.py`; remove the calc field constructors; the analysis emit no longer carries them.

### Y.4 — Test sweep

Hashes shift, FilterGroup-walking tests get pruned, App2 preprocessor gets unit coverage.

- [ ] **Y.4.a — Re-lock all dataset SQL hash tests.** Per-instance + per-dialect.
- [ ] **Y.4.b — Re-lock JSON-emit hash tests for every analysis whose FilterGroups / calc fields changed.** Investigation + L1 + L2FT all shift.
- [ ] **Y.4.c — Update `tests/integration/verify_dataset_sql.py` smoke verifier for the new placeholder shape.** Verifier currently parses + executes dataset SQL; needs to pass dataset parameter values for `<<$>>` substitution to round-trip cleanly.
- [ ] **Y.4.d — Drop or rewrite FilterGroup-walking tests that no longer apply.** Tests that asserted "Investigation has FG_INV_ANOMALIES_SIGMA scoped to sheet X" are obsolete. Replace with tests that assert "Volume Anomalies dataset SQL contains `:param_pInvAnomaliesSigma` after preprocessor".
- [ ] **Y.4.e — App2 unit test for `<<$paramName>>` → `:param_paramName` preprocessor.** Cover string + numeric param shapes; cover the `'<<$pName>>'` quoted case.
- [ ] **Y.4.f — Full unit test suite green.** Pytest + pyright clean.

### Y.5 — App2 cleanup (drop now-redundant infrastructure)

The X.2.g.3 work I (the engineer) added over the past few days becomes redundant once Y.1–Y.4 land. Sweep it.

- [ ] **Y.5.a — Drop `app2_sql=` parameter from `build_dataset` signature.** All call sites converted in Y.1–Y.2.
- [ ] **Y.5.b — Drop `app2_anchor_filter` / `app2_param_eq` / `app2_param_gte` / `app2_param_lte` helpers in `common/sql/app2_filters.py`.** Filter snippets now live in dataset SQL via `<<$>>` substitution; the App2-specific helper module shrinks to (or drops) `app2_date_filter` if Y.2.f converts it.
- [ ] **Y.5.c — Drop `tests/unit/test_sql_app2_filters.py`** if all helpers gone; otherwise keep for what remains.
- [ ] **Y.5.d — `_filter_specs_from_tree.py` simplifies.** ParameterControl auto-derive still walks tree controls (Y doesn't change that surface), but the LinkedValues query path may collapse if dataset parameters carry their own option lookup.

### Y.6 — Performance verification (the headline result)

The whole point of Phase Y. Concrete before/after numbers, per dialect.

- [ ] **Y.6.a — Pre-Y baseline: rows-on-the-wire per dashboard load (PG).** Use `pg_stat_statements` + a clean dashboard-walk script. Capture rows-fetched + query duration per visual.
- [ ] **Y.6.b — Pre-Y baseline (Oracle).** Same shape via `v$sqlstats`.
- [ ] **Y.6.c — Post-Y measurement: same dashboards, same operations, fresh capture.** Per dialect.
- [ ] **Y.6.d — Document deltas: % reduction in rows + query duration.** RELEASE_NOTES + a perf section in CLAUDE.md.
- [ ] **Y.6.e — Identify any sheet that DIDN'T see expected gains and root-cause** (likely cases: dataset SQL that's already narrow; a missed pushdown opportunity).

### Y.7 — e2e verification (clean on all three dialects, the gate)

Per user direction: the e2e suite must be clean on PG, Oracle, AND SQLite before Y can merge. SQLite is App2-only (QS doesn't support it), so the SQLite gate is specifically about the App2 bind preprocessor + dataset SQL with `:param_<name>` placeholders working end-to-end through aiosqlite.

- [ ] **Y.7.a — Full unit + integration suite green.**
- [ ] **Y.7.b — `e2e-pg-api` green.** API layer covers the dataset-parameter wiring + analysis JSON shape on PG.
- [ ] **Y.7.c — `e2e-oracle-api` green.** Same; surfaces any Oracle-specific `<<$>>` substitution + bind-translation quirks (Oracle's NUMBER/NUMERIC handling + empty-string-as-NULL semantics).
- [ ] **Y.7.d — `e2e-pg-browser` green.** Browser-level cascade + slider drag round-trips end-to-end.
- [ ] **Y.7.e — SQLite e2e green.** Layer 1 + Audit PDF SQLite tests (X.3.g.{1,2,3}) re-run after the dataset-SQL preprocessor changes; verify the bind translation works against aiosqlite.
- [ ] **Y.7.f — Resolve any flakes / regressions.** Per-failure investigation; xfail only with documented reason + follow-up task.

### Y.8 — Phase X rebase + merge

Phase X's still-in-flight work (X.2.g.3 dataset SQL, anything dependent on `app2_sql=`) gets rebased on top of Y so it picks up the convergent shape. The X.2.g.3.* tasks I just completed are subsumed by Y.2 — they get DROPPED in the rebase, not preserved.

- [ ] **Y.8.a — Rebase the in-flight Phase X branch onto `phase-y-sql-pushdown`.** Drop X.2.g.3.{a,b,c,d,e} commits; their effect is now in Y.2.
- [ ] **Y.8.b — Resolve conflicts in `apps/investigation/datasets.py` + `cli/serve.py`.** Likely the two contention points.
- [ ] **Y.8.c — Re-run e2e PG green post-rebase.**
- [ ] **Y.8.d — Re-run e2e Oracle green post-rebase.**
- [ ] **Y.8.e — Merge Phase Y to main.** Phase X continues from there with the new convergent path baked in.

### Y.9 — Convention + docs sweep

The new authoring pattern needs to be the canonical one for any future filter / parameter work. Docs catch up.

- [ ] **Y.9.a — CLAUDE.md update.** New section "Authoring filters: SQL-level parameter pushdown is the canonical pattern" — `<<$paramName>>` in dataset SQL + `MappedDataSetParameters`; analysis-level FilterGroups deprecated for filter intent (kept only for visual highlighting if any case justifies it).
- [ ] **Y.9.b — README sweep** for the architecture overview section.
- [ ] **Y.9.c — Customization handbook walkthrough — "How filters work" page.** Cover the dataset-parameter pattern + cascade behavior + perf intent.
- [ ] **Y.9.d — Migration note.** Customer L2 instance YAMLs are unaffected (this is internal architecture); flag explicitly so customers don't worry.

### Y.10 — Cut release

- [ ] **Y.10.a — Bump version (likely v9.0.0 — major architectural shift).**
- [ ] **Y.10.b — RELEASE_NOTES entry: convergence + perf wins headlined.**
- [ ] **Y.10.c — Tag + push.**
- [ ] **Y.10.d — Verify release pipeline runs green** (the existing `e2e-against-testpypi` gate already covers this).

---

## Sustainment & minor features

Backlog beyond Phase X. Promote to a numbered phase entry when scope justifies it.

### L2 model gaps

- **Multiple dashboards from one L2 instance** (shared prefix + naming).
- **PR dashboard → generic L2-validation dashboard** (re-skinning of L2FT for a different validation persona).

### Dashboard polish

- **Executives Transaction Volume + Money Moved — metadata grouping** (was Q.1.c.6). Needs L2-instance-aware metadata key dropdowns (cascading Key + Value like L2FT Rails sheet) plus a dataset pivot to expose metadata as a dim. Bigger than a punch-list item; queue as its own sub-phase.

### Post-X.2 App 2 polish (queued — not part of phase X scope)

- **Mobile / responsive.** Tailwind handles the layout primitives but no explicit mobile-first design pass. Promote when there's a customer story. Note: dashboards are dense by nature; mobile may always be a worse experience than desktop, regardless of effort.
- **Per-table CSV / XLSX export.** Operators expect "export to spreadsheet" on tables (QS has it). Lower priority than feature parity — punt unless it's a small agent task. The audit PDF already covers the "regulator-ready snapshot" case; spreadsheet export is for analyst self-serve.

### Audit / data evaluation

- **Postgres dataset evaluator** — given a connection, evaluate whether all exception cases are present; report stats on the CLI.

### Tech debt

- **Encode more invariants in the type system.** K.2 did this for drill-param shape compatibility; Phase L's tree primitives close another big chunk. What remains after L is the candidate list for the next round.

### Known platform limitations — do not re-attempt without new evidence

- **QS URL-parameter control sync** — K.4.7 cross-app drills dropped. URL fragment sets the parameter store but doesn't push values into bound controls. Re-entry conditions: AWS fix, custom embedded app via `setParameters()` SDK, or a new URL form that triggers control sync. See `PLAN_ARCHIVE.md` for full re-entry details.
- **QS dropdown click target is the middle grey bar** — `ParameterDropDownControl` only opens on the inner grey bar; clicking the visible edge does nothing. Suggest before investigating "unresponsive dropdown" reports.
- **QS silent-fail mode** — datasets healthy + describe-cleanly, every visual on every sheet shows the spinner forever. See CLAUDE.md → Operational Footguns for the diagnostic ladder.
