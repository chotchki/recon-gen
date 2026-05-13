# QuickSight Generator — Active Plan

**Where we are.** **Phase X.2 — the self-hosted "App 2" dashboard renderer — is complete** (wrap = v9.3.0, prepped on `x-2-u-app2-drills`; merge + tag + push it to release). The four apps now render two ways off one L2 instance: AWS QuickSight (the stable `json apply` path) and App 2 (a self-hosted HTMX/d3 page server, offline-capable, all three SQL dialects), with a 4-way cross-tool agreement test gating the release. Active work moves to the rest of Phase X — **X.3** (SQLite dialect — partly landed: Layer-1 + Audit-PDF cells done, the App-2 SQLite cell + integrator-local-loop docs pending), **X.4** (YAML editor), **X.5** (ETL helper), **X.6** (model-driven docs / README positioning sweep) — and **Phase Q (continued)** (CLI/YAML ergonomics). Sub-task detail for shipped phases lives in `PLAN_ARCHIVE.md`; per-release narratives in `RELEASE_NOTES.md`. This file tracks **forward-looking** work only.

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
- **Phase Y** (v8.8.0aN cumulative → v9.0.0 → v9.0.1) — SQL-level parameter pushdown: the QuickSight + self-hosted (App 2) renderers converged on one filter mechanism — `<<$paramName>>` / `{date_filter}` placeholders in the dataset CustomSql, substituted per-renderer (QS via `MappedDataSetParameters`; App 2 via `:param_<name>` binds). Analysis-level `FilterGroup`s deprecated for filter intent; calc fields that existed only to be filtered pushed down to real dataset columns. Date-pushdown perf: −15.3% rows on the wire / −8.5% query time overall, `l1-transactions` −92%. Built `./run_tests.sh` — the layered chain runner (`unit → db → app2 → deploy → api → browser`, variant matrix) CI now wraps (Y.2.gate). e2e clean on PG + SQLite + Oracle. Customer-facing artifacts (CLI / config.yaml / L2 YAML) unchanged — the major bump is the internal filter-architecture clean break. Full detail: `PLAN_ARCHIVE.md` `# PLAN — Phase Y` + `# PLAN — Y.2.gate`.
- **Phase X.2** (v9.0.2 → v9.0.3 → v9.1.0 → v9.2.0 → v9.3.0 cumulative) — App 2: the four apps render two ways off one L2 instance — AWS QuickSight (the stable `json apply` path) and a self-hosted HTMX/d3 page server (`quicksight-gen serve app2 apply`) reading the same DB directly (no AWS account; all three SQL dialects; offline-capable — browser-side assets vendored in the wheel; `/docs` handbook embedded). Built on Phase Y's shared dataset SQL; a 4-way cross-tool agreement test (`scenario plants ⊆ direct matview SELECT == QuickSight == App 2`, `== audit PDF` where it applies) gates the release. Dialect-aware `DashboardDriver` e2e protocol with parametrized `[qs, app2]` bodies; L2-theme-driven Tailwind + Tom Select / Flatpickr / noUiSlider widgets; vendored offline asset bundle; row-level table drills + cross-sheet URL-param threading; `biome check` folded into the pytest sessionstart gate. No customer-facing change (the `serve` group is a dev/iteration surface, not the stable CLI). Full detail: `PLAN_ARCHIVE.md` `# PLAN — Phase X.2`.

_Phase S / T / U / V / W / Y / X.2 sub-task detail in `PLAN_ARCHIVE.md`. RELEASE_NOTES `v8.{1,2,3,4,5,6,8}.x` / `v9.x` carry the per-phase + per-release narratives._

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

### X.2 — App 2: self-hosted dashboard renderer *(COMPLETE — shipped v9.0.0 → v9.3.0; full plan archived in `PLAN_ARCHIVE.md` → "Phase X.2")*

The four bundled apps now render two ways off one L2 instance: **AWS QuickSight** (the stable `json apply --execute` path) and **App 2** — a self-hosted HTMX + d3 page server (`quicksight-gen serve app2 apply`) that reads the same database directly, no AWS account, all three SQL dialects, offline-capable (browser-side assets vendored in the wheel; `/docs` handbook embedded). The two renderers share the dataset SQL (parameter pushdown — Phase Y converged them), and a 4-way cross-tool agreement test (`scenario plants ⊆ direct matview SELECT == QuickSight == App 2`, `== audit PDF` where it applies) gates the release — so App 2 is "QuickSight parity, minus the QuickSight bugs", enforced not just claimed. App 2 is the offline-iteration loop and the renderer X.4 (YAML editor) and X.5 (ETL helper) build on.

Shipped per sub-phase: **v9.0.0** (Phase Y filter convergence) → **v9.0.2** (X.2.t dataset-param cap + X.2.s.1 + u.3.fix.demo) → **v9.0.3** (X.2.p offline assets + X.2.s.2 docs theme) → **v9.1.0** (X.2.g.5 serve-all-apps + X.2.i `/docs` embed + browser-e2e-on-push) → **v9.2.0** (X.2.j 4-way agreement + the `app2_date_filter` day-inclusivity fix) → **v9.3.0** (X.2.u.4.e App2 row-level drills + cross-sheet URL-param threading + the `[qs, app2]` parity test; the X.2.l/X.2.p close-outs incl. `docs/reference/self-host.md`; the `biome check` pytest-sessionstart lint gate). Sub-tasks all done: X.2.a–p (spike → arch cleanup → all-GET REST surface → d3 renderers → filter primitives → sheet structure + cross-sheet/cross-app nav → real data fetcher → all 4 apps → Layer-2 e2e → `/docs` embed → 4-way agreement → themed error pages → offline asset bundle), X.2.q/u (dialect-aware `DashboardDriver`, parametrized `[qs, app2]` parity), X.2.l (L2-theme-driven Tailwind + fancy filter widgets), X.2.r (event-driven settle, drop the sleep-waits), X.2.s/t (docs-CLI bugs, dataset-param sentinel), X.2.k (incremental releases + README App-2 section). Open follow-ons that survived close-out are X.6 scope (README "positioning sweep" beyond the App-2 section; the X.6.j self-host guide expansion) or backlog ("Demo seed quality" below — the densified-baseline reconciling-ledger + plant pair-window spikes; the `test_l2ft_rails_dropdowns` `require_all_advertised` coverage gap; the `test_inv_drilldown` anchor-determinism re-light).

### X.3 — SQLite as a database dialect (integrator-local persona)

**What.** Add `Dialect.SQLITE` alongside Postgres + Oracle. SQLite is the integrator persona's local-iteration backend per `docs/x_2_design_thoughts.md` — "did I design my YAML right?", run 100% local, no remote DB setup, no Docker, no AWS.

**Why.** The integrator's iteration loop today requires either a live remote DB or a Docker-PG container. Both are heavyweight for "I want to see if my YAML edits look right." SQLite collapses that to a single file in `~/.quicksight-gen/` (or in-memory). App 2 (X.2) becomes the local viewer; SQLite becomes the local store.

**Scope.**

- [x] **X.3.a — Dialect.SQLITE enum + connection plumbing (LOCKED 2026-05-08; landed implicitly via X.3.g.1).** `Dialect.SQLITE` is in `common/sql/dialect.py:69` (alongside POSTGRES + ORACLE; module docstring §X.3 covers the SQLite branch contract). Connection via stdlib `sqlite3.connect()` in `common/db.py::connect_demo_db()` (lines 137–152), with `PRAGMA foreign_keys = ON` + `_register_sqlite_aggregates()` (lines 160–201) registering `STDDEV_SAMP` (matview SELECTs depend on it). `sqlite_path(url)` (lines 70–96) parses `sqlite:///path/to/db.sqlite` and `sqlite:///:memory:` URLs. Async pool via `_AsyncSqlitePool` (lines 552–575) wraps `aiosqlite`. `Config.dialect` accepts `Dialect.SQLITE` and `load_config()` parses `dialect: sqlite` from YAML + `QS_GEN_DIALECT` env. `execute_script(cur, sql, dialect=SQLITE)` uses `cur.connection.executescript(sql)` for multi-statement scripts.
- [x] **X.3.b — SQLite schema emit (LOCKED 2026-05-08; landed implicitly via X.3.g.1).** `common/l2/schema.py::emit_schema(instance, dialect=Dialect.SQLITE)` works end-to-end: 45KB of DDL emits cleanly + applies green against an in-mem SQLite. Per-dialect helpers in `dialect.py` already branch on SQLite for every primitive (`serial_type` → `INTEGER PRIMARY KEY AUTOINCREMENT` (single-col), `boolean_type` → `INTEGER` (0/1 convention), `text_type` / `json_text_type` / `varchar_type` → `TEXT`, `decimal_type` → `NUMERIC` (storage affinity), `cast` / `typed_null` → `CAST(... AS <_sqlite_type_alias>)`, `to_date` → `DATE(expr)`, `date_literal` → `'YYYY-MM-DD'` (plain text — `DATE 'literal'` is rejected by SQLite, see project memory + module docstring), `json_check` → `CHECK (col IS NULL OR json_valid(col))` (JSON1 built-in), `epoch_seconds_between` → `(julianday(later) - julianday(earlier)) * 86400`, `interval_days` / `range_interval_days` / `order_by_day_expr` adapt for SQLite's numeric-only RANGE frames + julianday projection, `date_trunc_day` → `datetime(expr, 'start of day')`, all `drop_*` → `DROP <thing> IF EXISTS` (no CASCADE — SQLite has no FKs in our schema), `with_recursive` → `WITH RECURSIVE`, `dual_from` → `""`). `_entry_column_decl(SQLITE)` returns `INTEGER PRIMARY KEY AUTOINCREMENT` (single-column auto-incrementing); composite `(id, entry)` collapses to `UNIQUE` (per-id supersession contract preserved by the matview's `WHERE entry = MAX(entry) WHERE id = ...` projection).
- [x] **X.3.c — Materialized views as truncate-and-select-into (LOCKED 2026-05-08; landed implicitly via X.3.g.1).** `dialect.py::matview_create_keyword(SQLITE)` returns `"CREATE TABLE"`; `matview_options(SQLITE)` returns `""`. So `CREATE TABLE <prefix>_X AS SELECT ...` (matview-as-table). `refresh_matviews_sql(instance, dialect=SQLITE)` in `common/l2/schema.py` branches to `_emit_sqlite_matview_refresh(instance)` which re-runs DROP + CREATE blocks for every matview-as-table, dependency-ordered. `drop_matview_if_exists(name, SQLITE)` collapses to `DROP TABLE IF EXISTS name;`. Same data contract + column shapes as PG / Oracle; preserves dialect-comparison parity. End-to-end probe (matview refresh against in-mem SQLite seeded with 864 transactions + 2,285 daily_balances) lands green.
- [x] **X.3.d — Seed pipeline against SQLite (LOCKED 2026-05-08; landed implicitly via X.3.g.1).** `cli/_helpers.py::build_full_seed_sql(cfg, instance, anchor=date(2030,1,1))` emits 1.4MB of dialect-aware INSERTs against `cfg.dialect = SQLITE` and applies clean. `seed.py::_sql_timestamp_literal(iso, SQLITE)` strips the timezone offset and converts `T` → space for SQLite's `datetime()` parser. `wipe_demo_data_sql(instance, dialect=SQLITE)` uses `DELETE FROM <table>` + `DELETE FROM sqlite_sequence WHERE name='<table>'` to reset AUTOINCREMENT counters (matches PG's `RESTART IDENTITY` semantics). Hash-locked SQL: `tests/data/_locked_seeds/spec_example.sqlite.sql` (1.4MB, byte-deterministic against the c.13.1 density=1.0 default). Locked-seed determinism test in `tests/data/test_locked_seeds.py` covers all three dialects.
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

### X.7 — Cloud CI cost optimization — DONE (2026-05-11; a/b/c, landed across Y.2.gate.k/l + P.7)

Out of active development iterations — manage cloud spend deliberately. Two-tier CI: a fast loop on every push that touches no AWS, and a gated full-e2e tier triggered by tag pushes (release gate, auto-fired), manual `workflow_dispatch`, or a weekly cron.

- [x] **X.7.a — Baseline the spend — DONE (2026-05-11; decision recorded).** Ballpark known (persistent Aurora ≈ $45/mo idle — see `feedback_ephemeral_aws_infra` memory). Decision: start/stop pre-existing instances rather than provision-and-terminate; keeps the connect strings static.
  - Answer: I think start/stop is fine for now, keeps the connect strings pretty static

- [x] **X.7.b — Fast loop on every push:main — DONE (2026-05-11; landed via `Y.2.gate.k.3` + `P.7`).** `ci.yml`: `test` job (pytest + pyright strict + Playwright JS unit), `integration-pg` (Postgres service-container db layer), `integration-oracle` (Oracle Free service-container db layer), `coverage` aggregator, `docs-portable-install`. No AWS touched. Per-commit feedback loop.

- [x] **X.7.c — Gated full e2e on three triggers — DONE (2026-05-11; landed via `Y.2.gate.l` + `e2e.yml` + `release.yml`).** RDS start/stop cycling: `./run_tests.sh up aws` / `down` / `status [--cost]` (`l.2.c–e`), `aws_rds_running` pre-dispatch probe (`l.3.a`), CI start/stop wiring (`l.1.a`). Triggers: (1) tag push — `release.yml::e2e-against-testpypi` auto-gates before PyPI publish; (2) `workflow_dispatch` — `e2e-pg-api` / `e2e-pg-browser` / `e2e-oracle-api`; (3) nightly cron — `e2e-pg-browser`. Aurora scale-to-zero deferred to operator (will reconfigure scaling when revisited); start/stop additional IAM grants TBD when the operator wires it.
  - Answer: Scale to zero is 100% doable, I'd just recommend start /stopping oracle. I'll reconfigure the scaling once we're here. For the start/stop I'll just need to know the additional permission grants.
  - **Concurrency redesign — SUPERSEDED (2026-05-11).** (1) **within-run race** — resolved by the trigger split: `e2e-pg-api` fires on push:main, `e2e-pg-browser` only on cron + workflow_dispatch, so they're never concurrent in one trigger; and the `Y.2.gate.m` variant matrix uses per-cell prefixes (`sp_pg_aw` etc.) so even concurrent runs don't collide on `spec_example_*`. (2) **cross-run cancellation** — `e2e.yml` keeps distinct per-dialect concurrency groups (`e2e-pg` / `e2e-oracle`) plus workflow-level `cleanup-pg`; the 1-second-cancellation pathology hasn't recurred under the trigger split. If it does, the workflow-level-concurrency fix is still the move.

### X.8 - Ask for configuring the row counts and date range for the data seeding

### X.9 — _(folded into X.6.g)_

The README + handbook positioning sweep is now X.6.g, since it shares the model-driven-docs concern.

### X.10 — Runner: intra-cell layer DAG (deploy starts right after seed)

The per-cell chain `unit → seed_variant → db → app2 → deploy → api → browser` is run strictly serially today, but `db`, `app2`, and `deploy` only depend on `seed_variant` — they're true siblings. `deploy` is the long pole (~2 min: boto3 creates theme + datasource + ~30 datasets + 4 analyses + 4 dashboards, each waiting on QS's slow async `CREATION_SUCCESSFUL`); `db` (~45 s) + `app2` (~30 s) fit entirely inside it. Fan `{db, app2, deploy}` out with `asyncio.gather` after the seed, then gather `{api, browser}` after `deploy` → ~75 s saved per cell. Only `aw`-target cells benefit (`lo` cells already drop deploy/api/browser), so ~5 cells × 75 s ≈ ~6 min off a full-matrix run, plus a noticeable win on a single `--variants=sp_or_aw` iteration loop. The `asyncio` plumbing already exists (`Y.2.gate.c.6.async` cell-level `gather`) — this pushes it one level down. Fits Phase X's cloud-CI-cost theme: less wall-clock on `aw` cells = less RDS uptime per run.

- [ ] **X.10.a — `cell_chain` expresses deps, not just order.** Today it returns an ordered `list[str]`; change to a small DAG (`{layer: frozenset[deps]}`) so `_run_one_variant` can topo-sort + gather sibling layers. Keep the same `cell_chain(spec, requested_chain)` truncation semantics (`up_to=app2` ⇒ no deploy/api/browser).
- [ ] **X.10.b — `_run_one_variant` gathers the sibling layers.** After `seed_variant`: `await asyncio.gather(db, app2, deploy)`; after `deploy` succeeds: `await asyncio.gather(api, browser)`. Per-(variant, layer) artifact / timing / db-perf dirs are already distinct, so concurrent writes are fine. The 3× concurrent `pytest -n auto` "bringing up nodes…" spin-ups add CPU pressure but the dev box absorbs it.
- [ ] **X.10.c — Decide failure semantics for in-flight `deploy` (the one real wrinkle).** If `db` fails while `deploy` is mid-flight, boto3 `create_data_set` calls aren't cleanly cancellable — cancelling orphans a half-deployed QS graph (the next `json clean` sweeps it, but it's messier than today's "halt at the failed layer, nothing downstream started" guarantee). Default: let the in-flight `deploy` finish, report the `db` failure, skip `api`/`browser`. Document the choice; preserve the `EXIT_FAILURE` / `EXIT_NEEDS_OPERATOR` exit-code contract.
- [ ] **X.10.d — Unit tests for the DAG dispatch** (`tests/unit/test_runner_skeleton.py`): topo order, sibling-gather, truncation, failure-skips-downstream. Mock the layer dispatch (no live DB/AWS).
- [ ] **X.10.e — Live wall-clock check + pyright + commit.** Run `--variants=sp_pg_aw` (or `sp_or_aw`) before/after; record the delta in this entry. CLAUDE.md "Commands" section: update the chain description (`unit → seed → {db | app2 | deploy} → {api | browser}`).

---

## Phase Q (continued) — CLI / YAML ergonomics

The standing "Phase Q" thread (Q.1–Q.5 + Q.3.a shipped; see Phase history). What's still open: the CLI-shape revisit below, plus the older "schema ergonomics around the L2 yaml" item (task #488 — fold into Q.6's spike or its own sub-item when scoped). Queues behind Phase X.

### Q.6 — CLI shape revisit: cfg ⇄ L2 dual-yaml factoring

Surfaced 2026-05-08 during `Y.2.gate.h.6` build. The runner now reads
`cfg.default_l2_instance: tests/l2/sasquatch_pr.yaml` and threads it as
`QS_GEN_TEST_L2_INSTANCE` into subprocess env_overrides — meaning the operator
declares the L2 instance ONCE in cfg and the runner aligns the seed flow + the
dataset-SQL smoke test automatically. **This makes the CLI's existing dual-arg
shape (`-c <cfg.yaml> --l2 <l2.yaml>`) partially redundant**: every `quicksight-gen
{schema|data|json|audit} {apply|clean|...}` invocation requires `--l2 <yaml>`
even though the cfg now carries that pointer. The dual-yaml factoring itself
may also be wrong now — a single combined cfg-with-L2-pointer (or a single
yaml union) might be the right shape.

Spike-before-implement (per `feedback_spike_before_locking_implementation.md`):
this is a CLI-surface change touching every operator command + every doc
example + tests. Wrong factoring locks in for years.

- [ ] **Q.6.0 — SPIKE: combined-yaml vs cfg-with-L2-pointer vs status-quo
  (LOCKED 2026-05-08; spike before Q.6.1).** Output `docs/audits/y_11_cli_shape_spike.md`.
  Compare the candidate factorings against today's two-yaml shape:

  - **A. Status quo + `--l2` defaults from `cfg.default_l2_instance`.**
    Operator can omit `--l2`; cfg implies it; explicit `--l2 <yaml>` overrides.
    Smallest delta, mostly-additive. Only one breaking case: cfg without
    `default_l2_instance:` AND CLI without `--l2` becomes ambiguous (was an
    error before; now silently uses bundled `default_l2_instance()` =
    spec_example).
  - **B. Single combined yaml.** Cfg + L2 merge into one file; CLI takes one
    `-c <combined.yaml>`. Eliminates the dual-yaml friction entirely.
    Trade-off: cfg yaml grows large (couple hundred lines for a real
    institution); env-only fields (account, DB password, signing material)
    co-mingle with institution-flavor fields (rails, chains, accounts) — the
    Q.5 separation existed for a reason (dev secrets vs institution shape are
    different release cadences).
  - **C. Cfg-with-L2-pointer + `--l2` removed entirely.** Cfg ALWAYS carries
    `default_l2_instance:` (made required); CLI drops `--l2`. Operators who
    deploy multiple L2 instances against one cfg use multiple cfg files (one
    per instance). Cleanest narrow change. Forces multi-instance operators to
    duplicate cfg.
  - **D. `--l2 <yaml>` becomes `--l2 <name>` indexed against an L2 registry
    in cfg.** Cfg carries `l2_instances: { sasquatch_pr: tests/l2/sasquatch_pr.yaml,
    spec_example: tests/l2/spec_example.yaml }` plus `default_l2_instance: sasquatch_pr`.
    CLI: `--l2 spec_example` (named, short). Multi-instance operators get
    cleaner ergonomics; single-instance operators still benefit from the
    default. Trade-off: another layer of indirection.

  **Constraint set:**
  1. **Operator types `quicksight-gen json apply --execute` with no other
     args** and gets a sane deploy of the default L2.
  2. **Multi-L2 operators don't have to copy cfg files** to deploy each L2.
  3. **Existing `--l2 <yaml>` invocations keep working** OR a clean migration
     path is documented (sed-able rename, deprecation warning, etc.).
  4. **Doc examples shrink** — every CLAUDE.md / README / handbook command
     example currently shows `-c X --l2 Y`; the spike's chosen shape should
     simplify the dominant single-instance case.
  5. **Tests pass without env-var passthrough** — the runner's
     `QS_GEN_TEST_L2_INSTANCE` injection (h.6) covers the test-side; the spike
     decides whether the CLI grows the same default behavior for non-test
     invocations.

  **Likely outcome (to validate in spike):** A or D. A is the smallest delta;
  D is the cleanest if multi-L2-per-cfg becomes common.

- [ ] **Q.6.1 — Implement per spike result.** Updates touch `cli/json.py`,
  `cli/schema.py`, `cli/data.py`, `cli/audit.py`, `cli/_helpers.py::resolve_l2_for_demo`,
  every CLAUDE.md / README / handbook example, every test that invokes
  `runner.invoke([...,"--l2",...])`, and every CI workflow YAML that uses
  `--l2`. Migration warning for at least one minor version.
- [ ] **Q.6.2 — Sweep memory entries + docs for stale `--l2 <yaml>` references.**
- [ ] **Q.6.3 — Update CLAUDE.md "Commands" block** to show the new shape as
  canonical; keep the explicit `--l2` form as the "multi-instance / explicit
  override" sub-pattern.
- [ ] **Q.6.4 — Bump version (breaking CLI change — post-v9.0.0) + RELEASE_NOTES
  entry highlighting the simplification + migration recipe.**

---

## Sustainment & minor features

Backlog beyond Phase X. Promote to a numbered phase entry when scope justifies it.

### L2 model gaps

- **Multiple dashboards from one L2 instance** (shared prefix + naming).
- **PR dashboard → generic L2-validation dashboard** (re-skinning of L2FT for a different validation persona).

### Demo seed quality

- **Baseline generator should produce a reconciling ledger — the planted scenarios should be the *only* L1-invariant violations** (surfaced 2026-05-12 during X.2.j.A; relates to closed `#525 Drift bug: rewrite _emit_baseline_daily_balances`). `emit_full_seed`'s baseline (before plants) leaves incidental violations that swamp the explicitly-planted lessons — at `data apply` (densified) density: `spec_example` shows ~70 `_drift` (leaf) + ~236 `_overdraft` rows against only ~1 explicitly-planted drift cell; `sasquatch_pr` shows 10 `_drift` + 98 `_overdraft`. The demo's pedagogy ("teach error classes, not topology" — see `feedback_demo_teaches_error_classes`) wants the matview rows to *be* the planted scenarios, not planted + noise; the 4-way agreement test's `expected ⊆ direct_SQL` shape tolerates the noise but `direct_SQL == expected` would be cleaner. Fix: make the per-Rail leg loop + daily-balance materialization (`common/l2/seed.py`) double-entry-exact so a baseline-only feed has zero `_drift`/`_overdraft`/`_ledger_drift` rows; encode as a **generator-invariant unit test** ("re-emit baseline-only `emit_full_seed`, assert the L1-invariant matviews are empty"), instance-agnostic but run on `spec_example` (hash-locked → a generator change shows up loud). The intraday-negative-balance-snapshot path (a balance dips negative mid-day before a credit lands, and the EOD snapshot catches it) is one suspected source for `_overdraft`. **Separate symptom, `sasquatch_pr`-specific:** parent `_ledger_drift` — `sasquatch_pr` shows 90 rows (worst day $50.3M), `spec_example` shows 0 — so the parent/child ledger-balance theorem fails only at `sasquatch_pr`'s topology. Needs its own `sasquatch_pr`/fuzz repro; can't be reproduced (or fixed) against `spec_example`.

### Dashboard polish

- **Executives Transaction Volume + Money Moved — metadata grouping** (was Q.1.c.6). Needs L2-instance-aware metadata key dropdowns (cascading Key + Value like L2FT Rails sheet) plus a dataset pivot to expose metadata as a dim. Bigger than a punch-list item; queue as its own sub-phase.

### Post-X.2 App 2 polish (queued — not part of phase X scope)

- **Mobile / responsive.** Tailwind handles the layout primitives but no explicit mobile-first design pass. Promote when there's a customer story. Note: dashboards are dense by nature; mobile may always be a worse experience than desktop, regardless of effort.
- **Per-table CSV / XLSX export.** Operators expect "export to spreadsheet" on tables (QS has it). Lower priority than feature parity — punt unless it's a small agent task. The audit PDF already covers the "regulator-ready snapshot" case; spreadsheet export is for analyst self-serve.

### Audit / data evaluation

- **Postgres dataset evaluator** — given a connection, evaluate whether all exception cases are present; report stats on the CLI.

### Tech debt

- **QuickSight datasource auto-create over a VPC endpoint** (kicked to backlog 2026-05-11; was a note under X.7). Auto-creating a QS datasource that has to traverse a VPC endpoint needs a `VpcConnectionArn` on the `create-data-source` call. Groundwork is done + parked on branch `hotfix-v8.7.4-vpc-connection-arn` (NOT merged): `VpcConnectionProperties` dataclass in `models.py`, `vpc_connection_arn` in `Config` + allowlist + `load_config`, wired into `build_datasource`, config-loader + datasource-emit tests cover it, version bumped + RELEASE_NOTES drafted. Held because QS VPC connections carry an hourly cost — revisit alongside the cloud-cost work (now-closed X.7's spirit) or when a customer actually needs a VPC-fronted datasource. See `project_vpc_endpoint_parked` memory.
- **Encode more invariants in the type system.** K.2 did this for drill-param shape compatibility; Phase L's tree primitives close another big chunk. What remains after L is the candidate list for the next round.
- ~~**Fold the biome JS lint into the test runner, like pyright.**~~ *(done, 2026-05-12.)* `conftest.py::pytest_sessionstart` now runs `biome check --max-diagnostics=400` alongside the pyright gate — `biome check` exits non-zero on lint *errors* (e.g. `noInnerDeclarations`) and zero on warnings, so the gate fires before any test collects (`pytest.exit(returncode=2)`); opt out with `QS_GEN_SKIP_BIOME=1`. `biome` is a standalone Rust binary (brew locally; `biomejs/setup-biome@v2` in CI), not an npm/pip package — when it's not on `PATH` the gate skips cleanly (same posture pyright has if it's missing). Bare `pytest tests/`, `./run_tests.sh up_to=unit`, and `ci.yml::test` all enforce it. (Why not a `[dev]` dep like `pyright` / `pytailwindcss`? The Biome project hasn't published an *official* PyPI package yet — in flight at biomejs/biome#8818. The unofficial `biome-js` wrapper bundles the Rust binary like `ruff` does, but ships only a `manylinux_2_28_x86_64` wheel — no macOS / arm64 / sdist, and it's a stale single release on Biome 2.3.x — so adding it would break `uv sync --extra dev` off linux-x86_64. Biome therefore stays a system binary; the `[dev]` block carries a NB comment recording this + a "revisit when biomejs/biome#8818 merges" pointer. `dev_setup`: `brew install biome`, or any of biome's install methods. **Follow-on when biomejs/biome#8818 lands:** add the official package to `[dev]`, drop the `setup-biome` CI step + the system-binary fallback in conftest / install.md.)
- **Drop `_oracle_lowercase_alias_wrapper`; emit dialect-natural identifier case from the generator** (was Y.3.f, parked 2026-05-09). DDL is emitted unquoted (PG folds lowercase, Oracle UPPERCASE → divergent storage); `_oracle_lowercase_alias_wrapper` (`common/dataset_contract.py`) bolts an outer `SELECT qs_inner."ACCOUNT_ID" AS "account_id" ...` so QuickSight (which builds `SELECT "account_id" FROM (...)` from its declared lowercase Columns) finds matching aliases. The proper fix — generator emits dialect-natural case in `DatasetContract.to_input_columns()`, QS quotes UPPERCASE on Oracle natively, wrapper gone — is bigger than it looks: QuickSight's analysis-side validation is *case-sensitive* against `Dataset.Columns` (Y.3.f.2's reverted Oracle-deploy probe surfaced 45 column-missing errors), so it requires case-folding ~30+ analysis-side column refs per dialect (visuals / filters / calc-fields / drills), not just the Columns declaration. The original App2 Oracle column-casing bug it would have fixed was instead fixed narrowly by Y.3.f.alt (`wrap_for_visual` quotes its column refs). Re-spike if the dialect-helper count grows past ~60, or if SQLite gets dropped from the matrix. See `project_qs_analysis_validates_columns_case_sensitive` memory.
- [ ] **App2-local L1 dashboard render errors (surfaced 2026-05-10, X.2.g.4 territory, NOT a Y.2.g regression).** With the Y.2.g.2.d pool-lifespan fix landed, `serve app2 apply --app l1_dashboard` now starts cleanly + the drift KPI fetches data from the live matview, but other L1 visuals throw render errors in App2 (operator observed during the manual local pass; smoke + drift KPI work, broader rendering doesn't). This is per-visual coverage in `_tree_fetcher` / `wrap_for_visual` — investigation/L2FT shipped via X.2.g.{2,3} with the same pattern, so the gap is L1-specific visual kinds the renderer hasn't grown arms for yet (KPIs work, tables / line-charts may not). Triage: capture the failing visual_ids + the renderer error, extend `_tree_fetcher.wrap_for_visual` with the missing arms, mirror the Investigation/L2FT shape. Out of Y.2.g scope (App2 visual coverage ≠ pushdown SQL); on the X.2.g roadmap.
- [ ] **CI/release cleanup steps target the wrong scope — `database-2` + QS leak (captured 2026-05-10; non-trivial, pick a fix before doing).** Three related bugs, all "the cleanup ran but cleaned the wrong thing", all harmless functionally (no impact on the release publishing / e2e passing) but they leak resources:
    1. **`e2e.yml::cleanup-pg::schema clean -c /tmp/ci-pg.yaml`** (no `--l2`) drops `spec_example_*` tables — but `e2e-pg-api` runs the runner with `--variants=sp_pg_aw`, which synthesizes an L2 with `instance: sp_pg_aw` → creates `sp_pg_aw_*` tables. So `cleanup-pg` drops a table set nothing created; the actual `sp_pg_aw_*` (and on cron, `sp_pg_aw_*` from `e2e-pg-browser`) accumulate in the operator's `database-2` forever. (Pre-existing, surfaced during the gate.l.8 work.)
    2. **The runner's `teardown_variant` for an `aw` target doesn't `DROP` the per-variant `<spec.name>_*` schema it created** (it only no-ops the AWS env / drops `lo` containers). This is the *source* of #1's leak — if the runner self-cleaned its aw tables, `cleanup-pg` wouldn't need to schema-clean at all.
    3. **`release.yml::e2e-against-testpypi::Cleanup (always)` passes `--l2 /tmp/release-l2.yaml` to `quicksight-gen json clean`** — but `json clean` has no `--l2` option (only `-c` / `-o` / `--all` / `--execute`), so it exits nonzero, `|| true` swallows it, and the step is a no-op → the `qs-release-<tag>-rel_<tag>-*` QS resources (deployed with `--l2 /tmp/release-l2.yaml` → tagged `L2Instance: rel_<tag>`, which `json clean` defaulting to `spec_example` won't match anyway) linger in QuickSight. **Fix:** swap that line to `json clean -c /tmp/release-e2e.yaml --all --execute` — `--all` purge mode sweeps everything matching the cfg's `resource_prefix` (`qs-release-<tag>`) regardless of L2Instance tag, which is exactly the one resource set the release-e2e job deployed. (Introduced by gate.l.8, 2026-05-10.)
    **Fix options for #1+#2:** (a) make the runner's `teardown_variant` `DROP` the `<spec.name>_*` tables on `aw` teardown — robust, but a runner change touching the seed/teardown path; (b) have `cleanup-pg` re-synthesize the variant L2 (`sed "s/^instance:.*/instance: sp_pg_aw/" tests/l2/spec_example.yaml > /tmp/sp.yaml; schema clean -c /tmp/ci-pg.yaml --l2 /tmp/sp.yaml`) — quick but hardcodes the variant names; (c) a "sweep test tables" step that drops any `<test-variant-pattern>_transactions`-shaped table. **Recommend (a)** — it's the right home for "the runner cleans up after itself". #3 is a trivial one-liner (use `--all`); fold it into whichever release.yml touch comes next (or a tiny hotfix). All three are queued, not started — pick the approach when convenient.

### Known platform limitations — do not re-attempt without new evidence

- **QS URL-parameter control sync** — K.4.7 cross-app drills dropped. URL fragment sets the parameter store but doesn't push values into bound controls. Re-entry conditions: AWS fix, custom embedded app via `setParameters()` SDK, or a new URL form that triggers control sync. See `PLAN_ARCHIVE.md` for full re-entry details.
- **QS dropdown click target is the middle grey bar** — `ParameterDropDownControl` only opens on the inner grey bar; clicking the visible edge does nothing. Suggest before investigating "unresponsive dropdown" reports.
- **QS silent-fail mode** — datasets healthy + describe-cleanly, every visual on every sheet shows the spinner forever. See CLAUDE.md → Operational Footguns for the diagnostic ladder.
