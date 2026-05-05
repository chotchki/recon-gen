# QuickSight Generator — Active Plan

**Where we are.** Phase W shipped (v8.6.0); the v8.3.x → v8.6.x cumulative bug sweep settled at v8.6.13. We're out of the heat of phase-driven development — new work runs as targeted minor features + bug sweeps in the **Sustainment & minor features** queue below. Historical detail for every phase prior to v8.0.0 lives in `PLAN_ARCHIVE.md` and `RELEASE_NOTES.md`. This file tracks **forward-looking** work only.

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

- [ ] **X.1.c — Sasquatch L1 dashboard render flake.** `test_harness_l1_planted_scenarios_visible[sasquatch_pr]` Layer 2 occasionally misses `cust-0001-snb` on the Limit Breach sheet — Layer 1 (matview row presence) passes, the row IS in the matview, but the deployed Limit Breach table doesn't render the cell within the visual timeout. One retry already baked in via `run_dashboard_check_with_retry`; second attempt also misses. Spec_example + fuzz variants of the same test pass on the same run, so the flake is data-shape-specific (sasquatch_pr's seed has more transactions; the L1 dashboard's per-sheet transfer_type dropdown may default-narrow before the table loads). With X.1.a in place, capture the failing Limit Breach sheet's state at the assertion moment and compare against spec_example's working render. Pick the right fix from these candidates: (a) widen the harness's per-sheet wait to assert "table rendered" before sheet_text capture; (b) add screenshot diff between failing/passing instances; (c) re-deploy sasquatch_pr seed with a tighter `days_ago=1` limit_breach plant to rule out timing. Do NOT xfail (M.4.4.12 lesson — silent xfails masked real bugs).

- [ ] **X.1.d — Apply layered (query+render) pattern across all browser e2e tests.** Was M.4.1.k; U.8.b.4 applied the pattern to the audit-dashboard agreement suite. Pattern: each test gets a Layer 1 (query the matview directly via psycopg2 / oracledb to confirm row presence) before Layer 2 (assert the deployed dashboard renders the row). When Layer 1 passes but Layer 2 fails, the bug is in QS rendering; when Layer 1 fails, the bug is in seed / matview. Sweep across `tests/e2e/test_l1_*.py`, `test_inv_*.py`, `test_exec_*.py`, `test_l2ft_metadata_cascade.py`. Best done after X.1.b/c shake out the existing defects so the layered shape isn't masking still-undiscovered bugs.

- [ ] **X.1.e — Pre-warm Rails sheet (perf hardening — reassess after X.1.b).** Originally proposed as a fix for the L2FT cascade test failure, but X.1.b's investigation will reveal whether the actual root cause was perf-related. If X.1.b resolves the failure without needing pre-warm, this item drops from scope. If perf was a contributing factor: visit Rails once during dashboard warm-up, navigate away, then re-enter for the actual assertion (cache is hot the second time).

### X.2 — Cloud CI cost optimization

Out of active development iterations — manage cloud spend deliberately. Two-tier CI: a fast loop on every push that touches no AWS, and a gated full-e2e tier triggered by tag pushes (release gate, auto-fired), manual `workflow_dispatch`, or a weekly cron.

- [ ] **X.2.a — Baseline the spend.** ~5 min in AWS Cost Explorer for the last 30 days; record the per-resource line items in this entry so X.2.b/c are sized against actual numbers. Without this we're guessing whether the right answer is "stop Oracle when idle" or "tear everything down between runs."
  - Answer: I think start/stop is fine for now, keeps the connect strings pretty static

- [ ] **X.2.b — Fast loop on every push:main.** pytest + pyright + Docker-PG + Docker-Oracle (both dialects already containerized) for everything that doesn't need QuickSight: unit + integration + contract + docs build + audit PDF generation. No AWS resources touched. This is the per-commit feedback loop.

- [ ] **X.2.c — Gated full e2e on three triggers.** Combined behavior: everything from X.2.b + start AWS RDS (PG + Oracle, both pre-existing) + warm + deploy QS + run true e2e + stop RDS + (if a tag) push to TestPyPI then prod approve. Trigger sources: (1) **on tag push** — release pipeline auto-fires it as the gate before TestPyPI publish (no manual step; failing e2e blocks the release); (2) **manual workflow_dispatch** — operator on-demand verification; (3) **weekly cron** — catches AWS breaking us between releases. Use `start-db-instance` / `stop-db-instance` cycling instead of provision-and-terminate (RDS billing pauses when stopped except storage; ~5 min start vs ~10-20 min provision). Aurora Serverless v2 already scales to 0.5 ACU min — verify whether scale-to-0 is configurable. The DB should be clean at start anyway, so cycling pre-existing instances is equivalent to fresh provision for our purposes.
  - Answer: Scale to zero is 100% doable, I'd just recommend start /stopping oracle. I'll reconfigure the scaling once we're here. For the start/stop I'll just need to know the additional permission grants.
  - **Concurrency redesign — fold in observed races (May 2026).** The current `e2e.yml` has two structural defects worth fixing as part of the X.2 redesign rather than separately: (1) **within-run race** — `e2e-pg-api` and `e2e-pg-browser` run in parallel within a single workflow run and both `schema apply` against the same `spec_example_*` tables, racing on DROP/CREATE. Caused the X.1.b L2FT cascade test to see an empty matview at assertion time. Fix: make `e2e-pg-browser` `needs: e2e-pg-api` (sequential within a run) OR give each job its own L2 instance prefix so the schemas don't collide. (2) **cross-run cancellation** — concurrency group `e2e-pg` with `cancel-in-progress: false` interacts badly with rapid push+dispatch sequences (observed 1-second cancellation when a push:main run's cleanup overlapped a queued workflow_dispatch). Fix: move concurrency to the workflow level (one run at a time, period) + simplify the per-job concurrency groups. Both are CI-shape changes that fit X.2's scope.

### X.3 - Ask for configuring the row counts and date range for the data seeding

### X.4 — README + verbiage update (post-X.2)

- [ ] **X.4 — README + handbook positioning sweep.** We're not currently selling what this tool actually accomplishes. Detailed scope deferred until X.2 lands — the CI shape changes (release-gated true-e2e, fast feedback elsewhere) will likely shift how we describe the project's "shape." Sweep should also cover the docs handbook home pages, not just README — those carry customer-facing claims about what the tool does too.

---

## Sustainment & minor features

Backlog beyond Phase X. Promote to a numbered phase entry when scope justifies it.

### L2 model gaps

- **Multiple dashboards from one L2 instance** (shared prefix + naming).
- **PR dashboard → generic L2-validation dashboard** (re-skinning of L2FT for a different validation persona).

### Dashboard polish

- **Executives Transaction Volume + Money Moved — metadata grouping** (was Q.1.c.6). Needs L2-instance-aware metadata key dropdowns (cascading Key + Value like L2FT Rails sheet) plus a dataset pivot to expose metadata as a dim. Bigger than a punch-list item; queue as its own sub-phase.

### Audit / data evaluation

- **Postgres dataset evaluator** — given a connection, evaluate whether all exception cases are present; report stats on the CLI.

### Tech debt

- **Encode more invariants in the type system.** K.2 did this for drill-param shape compatibility; Phase L's tree primitives close another big chunk. What remains after L is the candidate list for the next round.

### Known platform limitations — do not re-attempt without new evidence

- **QS URL-parameter control sync** — K.4.7 cross-app drills dropped. URL fragment sets the parameter store but doesn't push values into bound controls. Re-entry conditions: AWS fix, custom embedded app via `setParameters()` SDK, or a new URL form that triggers control sync. See `PLAN_ARCHIVE.md` for full re-entry details.
- **QS dropdown click target is the middle grey bar** — `ParameterDropDownControl` only opens on the inner grey bar; clicking the visible edge does nothing. Suggest before investigating "unresponsive dropdown" reports.
- **QS silent-fail mode** — datasets healthy + describe-cleanly, every visual on every sheet shows the spinner forever. See CLAUDE.md → Operational Footguns for the diagnostic ladder.
