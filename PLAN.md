# QuickSight Generator — Active Plan

**Where we are.** **Phase X.2 — the self-hosted dashboard renderer (now named "Dashboards", was "App 2") — is complete** (shipped v9.0.0 → v9.3.0; full plan archived in `PLAN_ARCHIVE.md`). The four apps render two ways off one L2 instance: AWS QuickSight (`json apply`) and Dashboards (HTMX/d3 page server, offline-capable, all three SQL dialects), with a 4-way cross-tool agreement test gating the release. **Phase X.3 — SQLite as a database dialect — is complete** (a–d landed 2026-05-08; X.3.g added the `e2e-sqlite` CI cell on top of the existing Layer-1 + Audit-PDF SQLite unit suites). Active work is **X.4 — Studio**: implementation tools for the integrator / trainer / ETL engineer, designed in [`SPEC_studio.md`](SPEC_studio.md) on `x-4-5-spec-studio` (the original X.4 + X.5 folded into one phase — Studio is the YAML editor + unified diagram + data-shaping orchestrator + ETL coverage, all reached via `quicksight-gen studio`). Then **X.6** (model-driven docs — partly superseded by Studio's interactive surface; what survives is auto-reference + a positioning sweep) and **Phase Q (continued)** (CLI/YAML ergonomics). Sub-task detail for shipped phases lives in `PLAN_ARCHIVE.md`; per-release narratives in `RELEASE_NOTES.md`. This file tracks **forward-looking** work only.

## Greater Plan
X.2 - add the non quicksight renderer
  - Solves testing limitations
X.3 - add sqlite as a database dialect
  - Does not support materialized views but shouldn’t matter due to the local nature of the db
X.4 - Studio: implementation tools (yaml editor + unified diagram + data-shaping orchestrator + ETL coverage). Folds in the original X.5 (etl helper). See SPEC_studio.md.
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

- **Phase AA** (v10.1.0a1 → v11.0.2 cumulative, closed 2026-05-19) — Dashboard UX + exception literacy pass driven by X.4 trainer feedback. No yaml grammar touched. Headline: all 31 multi-select dropdowns flipped to SINGLE_SELECT (drill-to-one — collapses the 32-cap sentinel-guard pattern of X.2.t.2); Daily Statement gains Role cascade + balance-row-only Account filter; Exception literacy panels parsed from `L1_Invariants.md` land on every L1 invariant sheet + Studio trainer pane; Account dropdowns search by `name (id)` concat across L1; deep browser-capture infra hardened (`webkit_page` fixture-path capture, MUI Autocomplete lazy-render handling, QS embed lifecycle lifted into one shared primitive, `ws_frames.txt` + `sql_trace.txt` artifacts); generic additive/inverse picker tests across 7 L1 + 3 L2FT sheets via dataset-builder-driven anchor query. Triage chain closed AA.A.race (App2 cache-vs-network), AA.A.l2ft-rails-inverse (DOM-visible-rows vs SQL-count), AA.A.qs-triage (12 QS failures across Shape A date-collapse + Shape B parameter-bridge), AA.A.daterange Shape A 1-line fix. 7 items deferred (cross-corpus duplication lint, tree-walk picker→column derivation, structural DATE_RANGE refactor + .4/.5 followons, `table_rows()` typed-invariant) — rehomed to the in-PLAN "Backlog (rehomed from AA)" subsection. Full detail: `PLAN_ARCHIVE.md` `# PLAN — Phase AA`.

- **Phase AC** (v11.0.0, 2026-05-17) — Renamed package + CLI + repo `quicksight-gen` → `recon-gen` (trademark + scope clarity). Clean cut with a `quicksight-gen` PyPI meta-package shim (1–2 month grace, calendar-anchored). `RECON_GEN_*` / `RECON_E2E_*` env-var prefix flipped with `QS_*` legacy-name fallback honored on read. Resource tag `ManagedBy: recon-gen` (no dual-scan back-compat — zero live tagged resources at cut time). AWS QuickSight references in code (API shapes, error messages, `QS` abbreviations) preserved as factual product references. GitHub repo renamed via auto-redirect; OIDC trust-policy `sub` claim updated in lockstep. Full detail: `PLAN_ARCHIVE.md` `# PLAN — Phase AC`.

- **Phase Z** (next release — branch `phase-z-b`) — L2 grammar cleanup: chain collapse + transfer_type subsumption + cfg deployment-namespace collapse. Z.A: `Chain(parent, children: tuple[Identifier, ...])` — singleton ⇒ required, multi ⇒ XOR (drops `ChainEntry`'s `required` / `xor_group` flags + 3 validators C2/C4/C4.1). Z.B: drop `Rail.transfer_type` + `TransferTemplate.transfer_type` + `<prefix>_transactions.transfer_type` column; rename `LimitSchedule.transfer_type` → `LimitSchedule.rail` (closes pending task #498). Z.C: collapse `cfg.resource_prefix` + `cfg.l2_instance_prefix` + `l2.instance` into two cfg fields `cfg.deployment_name` (QS resource ID prefix, required) + `cfg.db_table_prefix` (DB-table prefix, required); cleanup tag pair `ResourcePrefix` + `L2Instance` collapses to single `Deployment`; L2 yaml's `instance:` field dropped entirely (loader hard-rejects with actionable migration error). End-of-phase verify: 11/11 deterministic db-matrix cells green across sp/sq × pg/or/sl × lo + sp/sq × pg/or × aw; 3 fuzz cells failed on a known fuzz-shape instability (todays_exceptions matview empty for seed 3313442831 — tracked as a fuzz-contract follow-up, not a Z regression). Full detail: `PLAN_ARCHIVE.md` `# PLAN — Phase Z`.

_Phase S / T / U / V / W / Y / X.2 / Z / AA / AC sub-task detail in `PLAN_ARCHIVE.md`. RELEASE_NOTES `v8.{1,2,3,4,5,6,8}.x` / `v9.x` / `v10.1.0a1` / `v11.0.0`-`v11.0.2` carry the per-phase + per-release narratives._

---

## Phase X — e2e testing expansion + cloud CI cost optimization

### Parallelism map *(historical: covered fan-out for X.2/X.3 — both shipped; archived in `PLAN_ARCHIVE.md`. X.4's parallel-vs-sequential shape lives in its own "Shape of the work" subsection below.)*

### X.1 — e2e fixes + auto-screenshot foundation *(COMPLETE — shipped pre-X.2; full plan archived in `PLAN_ARCHIVE.md` → "Phase X.1")*

E2e harness fixes + auto-failure-screenshot + L2FT cascade root-cause + dropdown migration + theme-404 fix + open-set Status + Chain validator + ETL-examples module + CLI-cross-reference checker + unified seed-hash lock surface + Sasquatch L1 'flake' (table virtualization) diagnosis + Layer-1 query helpers. Conditional X.1.e (pre-warm Rails) was overcome by X.1.b's actual root-cause fix and dropped.

### X.2 — App 2: self-hosted dashboard renderer *(COMPLETE — shipped v9.0.0 → v9.3.0; full plan archived in `PLAN_ARCHIVE.md` → "Phase X.2")*

The four bundled apps now render two ways off one L2 instance: **AWS QuickSight** (the stable `json apply --execute` path) and **App 2** — a self-hosted HTMX + d3 page server (`quicksight-gen serve app2 apply`) that reads the same database directly, no AWS account, all three SQL dialects, offline-capable (browser-side assets vendored in the wheel; `/docs` handbook embedded). The two renderers share the dataset SQL (parameter pushdown — Phase Y converged them), and a 4-way cross-tool agreement test (`scenario plants ⊆ direct matview SELECT == QuickSight == App 2`, `== audit PDF` where it applies) gates the release — so App 2 is "QuickSight parity, minus the QuickSight bugs", enforced not just claimed. App 2 is the offline-iteration loop and the renderer X.4 (YAML editor) and X.5 (ETL helper) build on.

Shipped per sub-phase: **v9.0.0** (Phase Y filter convergence) → **v9.0.2** (X.2.t dataset-param cap + X.2.s.1 + u.3.fix.demo) → **v9.0.3** (X.2.p offline assets + X.2.s.2 docs theme) → **v9.1.0** (X.2.g.5 serve-all-apps + X.2.i `/docs` embed + browser-e2e-on-push) → **v9.2.0** (X.2.j 4-way agreement + the `app2_date_filter` day-inclusivity fix) → **v9.3.0** (X.2.u.4.e App2 row-level drills + cross-sheet URL-param threading + the `[qs, app2]` parity test; the X.2.l/X.2.p close-outs incl. `docs/reference/self-host.md`; the `biome check` pytest-sessionstart lint gate). Sub-tasks all done: X.2.a–p (spike → arch cleanup → all-GET REST surface → d3 renderers → filter primitives → sheet structure + cross-sheet/cross-app nav → real data fetcher → all 4 apps → Layer-2 e2e → `/docs` embed → 4-way agreement → themed error pages → offline asset bundle), X.2.q/u (dialect-aware `DashboardDriver`, parametrized `[qs, app2]` parity), X.2.l (L2-theme-driven Tailwind + fancy filter widgets), X.2.r (event-driven settle, drop the sleep-waits), X.2.s/t (docs-CLI bugs, dataset-param sentinel), X.2.k (incremental releases + README App-2 section). Open follow-ons that survived close-out are X.6 scope (README "positioning sweep" beyond the App-2 section; the X.6.j self-host guide expansion) or backlog ("Demo seed quality" below — the densified-baseline reconciling-ledger + plant pair-window spikes; the `test_l2ft_rails_dropdowns` `require_all_advertised` coverage gap; the `test_inv_drilldown` anchor-determinism re-light).

### X.3 — SQLite as a database dialect (integrator-local persona) *(COMPLETE — shipped 2026-05-08 → 2026-05-12; full plan archived in `PLAN_ARCHIVE.md` → "Phase X.3")*

Third supported dialect alongside Postgres + Oracle. Schema emit, matview-as-table refresh, deterministic seed pipeline, hash-locked. CI cells: Layer-1 + Audit-PDF SQLite unit suites in ci.yml::test, plus the App-2-against-SQLite cell as ci.yml::e2e-sqlite. X.3.e's `--sqlite` CLI shorthand and X.3.f's integrator-local-loop walkthrough were cut — superseded by Studio (X.4), which becomes the integrator's local-iteration front door.

### X.4 — Studio: implementation tools *(COMPLETE — shipped v10.0.0; full plan archived in `PLAN_ARCHIVE.md` → "Phase X.4")*

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

## Phase AA — Dashboard UX + exception literacy *(COMPLETE — shipped v10.1.0a1; full plan archived in `PLAN_ARCHIVE.md` → "Phase AA")*

Bundled operator-feedback UX work that surfaced during X.4 trainer use — no yaml grammar touched. 8 sub-sections (AA.A dropdown flip, AA.A.race App2 freshness, AA.A.l2ft-rails-inverse row-count, AA.A.qs-triage 12-failure cohort, AA.A.daterange Shape A fix, AA.B Daily Statement, AA.C exception literacy panels + trainer pane, AA.D label hygiene, AA.E search-by-name-AND-id, AA.G dependabot, AA.H browser-capture infra). Closed 2026-05-19 with AA.A.qs-triage.5 Today's Exceptions resolution + `record_sql_trace` artifact. 7 deferred items rehomed to the "Backlog (rehomed from AA)" section below.

### Backlog (rehomed from AA)

Items uncovered during AA but explicitly deferred — all carry a spike-before-implement or "next phase end-of-phase" gate. Rehome onto a real driver when a phase picks one up.

- [ ] **AA.A.10 (stretch) — Tree-walk picker→column derivation.** Even after AA.A.9, `PickerSpec.column` is still hand-mapped (1 line per picker referencing the dataset projection column). The tree carries the wiring formally: `ParameterControl.parameter` → `Parameter.mapped_dataset_params` → `(dataset, dataset_param_name)` → dataset SQL's `<<$p>>` substitution site → the column it compares. Either parse the dataset SQL to find the column the param narrows on, OR annotate `DataSetParameter` with a `narrows_column` field at construction time (production-code surface change). Result: spec carries only sheet/visual/builder/order — every PickerSpec disappears, the helper derives the full picker→column map from the tree. Plan + spike before locking the annotation-vs-parse path.
- [ ] **AA.A.11 — Cross-corpus duplication lint (test ↔ src), paired approaches 1+3.** User-flagged 2026-05-17 as "huge structural win": every duplicated SQL string between `tests/` and `src/` is a second codebase that can pass while production breaks, or vice versa. Approach 1 (content-based AST lint walking `tests/` for SQL fingerprints + cross-referencing `src/`) + Approach 3 (provenance lint — require values in test assertions to come from `import` of src, not inline literal). Both, not either alone — Approach 1 catches today's drift, Approach 3 catches future drift before it can happen. Approach 2 (jscpd/PMD CPD) deliberately rejected (non-Rust). Spike-before-implement: throwaway script measuring false-positive rate at various length thresholds; allowlist syntax (sibling comment vs central registry); cheap-enough for unit prelude vs opt-in lint mode.
- [ ] **AA.A.l2ft-rails-inverse.4 — Type-encode the `table_rows()` invariant.** `table_rows()` for narrowing-assertion sites is a smell — the picker-row-survival contract is about SQL row count, not DOM visibility. Consider deprecating `len(table_rows())` for assertion use, or renaming to `dom_visible_rows()` so the cap is obvious at call sites.
- [ ] **AA.A.daterange.3 (BACKLOGGED) — Structural refactor: single DATE_RANGE control.** Replace each sheet's `(ParameterDateTimePickerControl Date From + ParameterDateTimePickerControl Date To + TimeRangeFilter)` triplet with one `FilterDateTimePickerControl(Type="DATE_RANGE")`. Closes the "from > to" UX footgun structurally and aligns L1 / L2FT / Exec with Investigation. **Wall:** L1's multi-dataset-per-sheet model needs a sharing mechanism — the filter-bound widget pattern Investigation uses binds to ONE filter on ONE dataset. Options: (a) consolidate L1 datasets per sheet (schema work), (b) accept one widget per dataset per sheet (UX noise), (c) find a QS mechanism for one widget driving multiple parameter-bound filters via parameter intermediates. Spike before locking direction.
- [ ] **AA.A.daterange.4 (BACKLOGGED) — App2 renderer for widget-bound DATE_RANGE.** Already proven for Investigation; would extend to the new L1/L2FT/Exec range controls. Follows .3.
- [ ] **AA.A.daterange.5 (BACKLOGGED) — Test infra.** `apply_anchor_to_pickers` for date pickers becomes "set the range to span anchor's date ±1 day" instead of separate from/to. Single picker spec, not two. Follows .3.

---

## Phase Z — L2 grammar cleanup *(COMPLETE — chain collapse + transfer_type subsumption + cfg deployment-namespace collapse; full plan archived in `PLAN_ARCHIVE.md` → "Phase Z")*

Z.A (Chain grammar collapse: singleton ⇒ required, multi ⇒ XOR) + Z.B (subsume transfer_type into rail) + Z.C (collapse `resource_prefix` + `l2_instance_prefix` + `l2.instance` into `cfg.deployment_name` + `cfg.db_table_prefix`) shipped together as one grammar settle. End-of-phase verify: 11/11 deterministic db-matrix cells green (sp/sq × pg/or/sl × lo + sp/sq × pg/or × aw); 3 fuzz cells failed on a known fuzz-shape instability (todays_exceptions matview empty for seed 3313442831 — NOT a Z regression; tracked as a fuzz-contract follow-up).

---

## Phase AB — SPEC enhancements for real-system gaps (inbound caps + template-as-chain-child + multi-Variable templates + N:1 chain fan-in)

Queues after Phase AA. Source: `SPEC_gap_feedback.md` (4 enhancements surfaced during integrator-side real-system modeling). All four are SPEC-level expressivity gaps — patterns integrators meet in real flows that the current L2 grammar either silently misroutes (Enhancement 4 — inbound caps drop from the L1 dashboard) or rejects outright (Enhancements 1, 2, 3 — multi-Variable per template, N:1 fan-in chains, template-as-chain-child). Each is additive in the operator yaml (new optional field or relaxed constraint), so the cohort fits within `v10.x` alphas — no second major-bump after Phase Z.

**Staging order** (per gap-doc §"How these proposals interrelate"): AB.1 first (inbound caps — independent, no chain/template plumbing). AB.2 (template-as-child) next — small, well-scoped, unlocks AB.4's preferred shape. AB.3 (multi-Variable + leg_rail XOR) third — self-contained. AB.4 (N:1 fan-in) last — biggest conceptual change, gated on a spike (see AB.4.spike).

**Cross-cutting contract per sub-phase.** Every enhancement below MUST land with all seven legs of the demo-quality bar — none of them are optional. The phase ratchets the bar; partial landings push the gap forward, not close it.

1. **Primitives + loader + validator + serializer** — the L2 yaml surface change end-to-end at the model layer. Loader rejects malformed shapes with an actionable error pointing at this PLAN section. Round-trip stable through `serialize_l2 → load_instance`.
2. **Studio editor field** — the new SPEC field surfaces as a Studio editor card field (dropdown / multi_select / textarea / checkbox per shape), with the appropriate `FieldSpec` declaration, validation message, and Save-button round-trip back to the L2 yaml. The integrator MUST be able to declare the new shape *via the Studio UI*, not just by hand-editing yaml. Without this, the new primitive is unreachable for the trainer/integrator persona Studio was built for (X.4) — and a future operator hitting a Studio "Cannot Edit" wall when this field appears in a customer yaml is the regression we're guarding against. **This leg is the AB.1-onward addition** — pre-AB phases didn't enforce a per-sub-phase Studio commit, and it's how new SPEC shapes end up loader-roundtrippable but Studio-blind.
3. **Schema + matview emit** — the per-prefix DDL covers the new shape so violations land in a queryable surface. Existing matview tests get a new parametrized case; no new matview tests pass unless schema apply succeeds against pg / oracle / sqlite.
4. **Exception tracking on the L1 dashboard** — every new violation kind lands on an existing or new sheet, with description text the CPA can read and a drill back to the offending Transactions row. New plant kinds MUST surface ≥1 row in the demo dashboard so the visual canary is non-empty (per CLAUDE.md "Every visual should have non-empty data in the demo").
5. **Error plants** — `auto_scenario.py` gets a new `Plant` dataclass + `emit_seed` integration for each violation kind, deterministic per seed. `TestScenarioCoverage` assertion guarantees ≥N rows of the new shape land in the matview. `sasquatch_pr.yaml` adds 1-2 instances of the new pattern (real-world flavored) so the demo dashboards have non-empty cells.
6. **Fuzzer + spec_example** — `tests/l2/fuzz.py::random_l2_yaml(seed)` MUST occasionally emit the new shape so the fuzz matrix cells (`fuzz:N`) exercise it at scale; `tests/l2/spec_example.yaml` (the minimal-fixture every dialect-snapshot test reads) MUST grow ≥1 instance of the new shape so deterministic snapshots cover the code path. Without both, the new primitive ships untested under the fuzz axis AND silently absent from `sp_pg_lo / sp_or_lo / sp_sl_lo / sp_pg_aw / sp_or_aw` cells. **This leg is the AB.1-onward addition** — pre-AB phases didn't enforce it, and it's how new SPEC shapes end up exercised only through hand-written end-to-end tests.
7. **Tests** — unit (validator + emit + plant-coverage) + db layer (matview row counts) + audit PDF (new violation appears in the appropriate per-invariant table) + 4-way agreement (scenario plants ⊆ direct matview SELECT == QS == App2 == PDF). Pyright strict-scope keeps holding across the changed files.

Plus the standard cuts: `Schema_v6.md` updated; handbook walkthroughs author one worked example per enhancement; seeds re-locked per dialect; PLAN sub-task ticks; quirks log entries when QS surfaces something the SPEC didn't predict.

**Decisions locked before implementation** (gap doc + user ratification, 2026-05-15):

- **AB.1 dashboard surface**: Inbound + Outbound on the **same Limit Breach sheet**, table gains a new "Direction" column. CPAs read both as "cap breach" semantically; splitting would be gap-doc copy mistaken for design. Baked into AB.1.8 below.
- **AB.2 two-template chain Parent disagreement**: gap doc §3 Tradeoffs locks **(a) reject as L1 Conservation-style violation** — surfaces the ETL bug rather than hiding it. Baked into AB.2.3 below.
- **AB.3 multi-Variable XOR resolution evidence**: gap doc §1 Tradeoffs locks **implicit-from-firing-existence** — no separate `variant_selector` MetadataKey; validation reads the leg_rail rows that landed. Baked into AB.3.3 below.
- **AB.4 N:1 fan-in chains**: ship **(B) build the primitive** — `fan_in: bool` on Chain + multi-parent matview + new L1 invariant. User judgment: worth it given the consolidated-payout / multi-source-batch-settlement patterns that show up beyond the merchant-payout case. AB.4 ships its full ~12 sub-tasks; docs-only fallback dropped.
- **AB.4 multi-parent storage shape**: **matview, not base-table change.** ETL already writes leg rows into `<prefix>_transactions` with `parent_transfer_id` in JSON metadata (existing per-leg convention). Multi-parent set for a fan_in child Transfer is derivable: `SELECT DISTINCT JSON_VALUE(metadata, '$.parent_transfer_id') GROUP BY transfer_id`. Pure matview territory — no base-table schema change, no ETL contract change. Baked into AB.4.3 below.

### AB.1 — Enhancement 4: Limit Breach inbound caps

Today's `OutboundFlow` theorem filters on `Amount.Direction = Debit`. Real-world AML / KYC policies frequently impose inbound caps (per-customer-DDA daily inbound ACH, new-account inbound caps until verification, counterparty inbound caps for source diligence, merchant inbound settlement caps for unusual-volume flagging). The integrator can't express any of these declaratively today — they enforce upstream at the ETL boundary (invisible to the L1 dashboard) or omit them from the model entirely.

**Preferred shape** (gap doc §4(A)): new optional `direction: {Outbound, Inbound}` field on `LimitSchedule` (default `Outbound` for backward compat); new `InboundFlow` theorem mirroring `OutboundFlow` but filtering on `Amount.Direction = Credit`; `<prefix>_limit_breach` matview's CASE branches handle both directions.

- [ ] **AB.1.0 — Locks (3 decisions before .1 fires, 2026-05-19).**
  - **Enum**: new `LimitDirection(OUTBOUND, INBOUND)` at the primitives layer (NOT reusing `Amount.Direction.Debit/Credit`). Reason: `Amount.Direction` is the accounting / entry-side perspective; `LimitDirection` is the cap-policy / flow-perspective. Conflating them invites the Z.B-class footgun where `LimitSchedule.transfer_type` got matched against the wrong column shape. YAML uses `Inbound` / `Outbound` literals.
  - **Matview shape**: `UNION ALL` two SELECTs in `<prefix>_limit_breach` — one per direction, each filtered to `amount_direction = 'Debit'/'Credit'` AND its own direction-filtered cap CASE. Each output row carries an explicit `direction` column (no inference downstream). `_render_limit_breach_cases` gains a `direction` parameter; the matview emits twice.
  - **Default**: `direction=OUTBOUND` (gap doc §4(A) locked; backward-compat for every existing L2 instance not yet specifying).
- [x] **AB.1.1 — SPEC + theorem.** Done. SPEC.md: added `InboundFlow` theorem (line 131, mirror of OutboundFlow filtering on `Credit`); rewrote the "Limit breach" SHOULD-constraint to reference both theorems per LimitSchedule direction; updated the `LimitSchedule` entity definition (line 567-area) to declare the `Direction` field with the `Outbound` default + the load-time note that `(ParentRole, Rail, Direction)` uniqueness allows `(parent, rail)` to appear twice with different directions; updated the M.2d.2 uniqueness rule (line 667) to match. Schema_v6.md: noted the per-direction emit in the matview-tree diagram + extended the LimitSchedule-CASE-branch prose to describe the UNION ALL shape. L1_Invariants.md: rewrote section 5 ("Outbound flow cap" → "Per-direction flow cap"), updated the SHOULD-constraint blockquote to reference both theorems, expanded the body + columns to mention the new `direction` column, expanded the `**What to do:**` line to call out the AML-flag interpretation for Inbound breaches. Parser tests (`tests/unit/test_handbook_invariants.py`) stay green at 22/22 (the parser keys on heading shape + `**Columns:**` + `**What to do:**` markers — all preserved). The dashboard panel's body content + the trainer-pane card both regenerate from the doc automatically (AA.C.2 + AA.C.5). Sub-tasks of AB.1.2-.4 will follow with the code-side changes (primitives + loader + matview).
- [x] **AB.1.2 — `primitives.py`: new `LimitDirection` enum + `direction: LimitDirection = "Outbound"` on LimitSchedule.** Done. Style-aligned with existing `Scope` / `LegDirection` / `SupersedeReason` Literal aliases (codebase pattern, not a separate `enum.Enum`) — `LimitDirection: TypeAlias = Literal["Outbound", "Inbound"]`. New `direction` field placed between `cap` and `description`, default `"Outbound"` keeps every existing keyword/positional construction byte-equivalent. Smoke-verified construction + positional path; full L2 unit suite green (130 passed).
- [x] **AB.1.3 — Loader + serializer + validator.** Done. Loader: new `_load_limit_direction(raw, *, path)` mirrors `_load_scope` / `_load_leg_direction` — rejects anything outside `{"Outbound", "Inbound"}` with the actionable message. `_load_limit_schedule` reads `raw_d.get("direction", "Outbound")` so unset YAMLs default cleanly. Serializer: `_dump_limit_schedule` emits `direction:` ONLY when non-default (`!= "Outbound"`) — every pre-AB.1 YAML is byte-equivalent through load+dump. Validator U5: broadened from `(parent_role, rail)` to `(parent_role, rail, direction)` triple — same `(parent, rail)` may now appear twice as long as direction differs. Header docstring + body updated with the AB.1 reason. Smoke-verified all 7 round-trip behaviors (default omit / explicit emit / load explicit / load default / reject bogus / U5 reject same-direction dup / U5 allow Outbound+Inbound on same parent+rail). Full L2 unit suite green (130 passed).
- [x] **AB.1.4 — `schema.py`: `<prefix>_limit_breach` matview UNION ALL two direction-branches.** Done. `_render_limit_breach_cases(instance, *, p, dialect, direction="Outbound")` filters inlined CASE branches to LimitSchedules matching `direction`; an empty filter result returns typed-NULL so the branch contributes zero breach rows after the outer `cap IS NOT NULL` filter (an L2 with zero Inbound caps stays cleanly empty without conditional template surgery). Matview's body is now `SELECT * FROM (Outbound-SELECT UNION ALL Inbound-SELECT) flow_with_cap WHERE cap IS NOT NULL AND outbound_total > cap`. Each branch carries a literal `'Outbound'` / `'Inbound'` direction column in the projection + filters on `amount_direction='Debit'`/`'Credit'` accordingly. New `idx_<p>_lb_direction` index. `LIMIT_BREACH_CONTRACT` extended with `ColumnSpec("direction", "STRING")` so `SELECT * FROM matview` projection matches the 9-column dataset shape on both PG and Oracle (the Oracle wrapper aliases by contract). Visual rendering of the column lands in AB.1.8. Verified the new matview SQL emits cleanly on PG / Oracle / SQLite; unit + schema + data + json + cli + audit suites green (2878 passed).
- [x] **AB.1.5 — `auto_scenario.py`: new `InboundCapBreachPlant`.** Done. New `InboundCapBreachPlant` dataclass in `common/l2/seed.py` (mirror of `LimitBreachPlant`); new `_emit_inbound_cap_breach_rows` emits Credit customer leg + Debit external counter (`Origin=ExternalForcePosted`, flips Outbound's `InternalInitiated`). `ScenarioPlant` gains `inbound_cap_breach_plants` field; `emit_seed` dispatches the new plants. `auto_scenario._pick_inbound_breach_inputs` mirrors `_pick_breach_inputs` but filters LimitSchedules by `direction=="Inbound"` and resolves an inbound 2-leg rail + external source counter. Existing `_pick_breach_inputs` narrowed to `direction=="Outbound"`. Plant assembly mirror at `days_ago=3` (distinct from Outbound's `days_ago=4`). All 3 `ScenarioPlant` pass-through call sites (`boost_inv_fanout_plants`, broken-rail wrapper, only_template selector) carry the new field. `replicate_inbound_breach` densifier mirror lands. Seed-header stats line splits Outbound/Inbound counts. `test_auto_scenario` permits `InboundCapBreachPlant` omission for spec_example (no Inbound LS yet — covered in AB.1.5.spec). Re-locked all 3 spec_example seeds (pg/oracle/sqlite). Full unit + schema + data + json + cli + audit suite green (2878 passed).
- [x] **AB.1.5.fuzz — `tests/l2/fuzz.py`: emit `direction` on synthesized LimitSchedules.** Done. `_build_limit_schedules` extended: per chosen `(parent_role, rail)` pair, draws `direction: Inbound` with 30% probability; `Outbound` is the default so it's omitted from the emitted YAML (matches serializer's "non-default only" emit, keeps pre-AB.1 fuzz_failure fixture bytes equivalent). Per-pair uniqueness still holds at the (parent, rail) level — fuzz doesn't plant both directions on the same pair within one instance (that combination is exercised by hand-written unit tests + AB.1.5.spec). Smoke-verified: 5/10 random seeds emit at least one Inbound LimitSchedule across their LimitSchedule rows.
- [x] **AB.1.5.spec — `tests/l2/spec_example.yaml`: add 1 Inbound LimitSchedule.** Done. Added `(parent_role=CustomerLedger, rail=ExternalRailInbound, cap=3000.00, direction=Inbound)` — sibling to the existing Outbound `ExternalRailOutbound` cap on the same parent. Validates: spec_example loads 2 LimitSchedules, auto_scenario plants 1 InboundCapBreachPlant ($4500 = cap * 1.5), 0 omitted. Re-locked all 3 dialect seeds; synced the 2 bundled copies (`src/recon_gen/_l2_fixtures/spec_example.yaml` + `src/recon_gen/apps/l1_dashboard/_default_l2.yaml`). Tightened `test_auto_scenario_against_spec_example_covers_all_six_plant_kinds` to assert `len(inbound_cap_breach_plants) == 1`. Full 2878-test sweep green.
- [ ] **AB.1.6 — `sasquatch_pr.yaml` real-world flavor.** Add 1 inbound cap (the gap-doc's "daily inbound ACH cap of $20K per customer DDA" — AML-flag threshold). Plants seed inbound days at $25K so the dashboard shows the violation.
- [ ] **AB.1.7 — `editor.py` + Studio LimitSchedule card (leg 2 of the contract).** Add `direction` `FieldSpec` to the LimitSchedule editor card: enum dropdown rendering "Outbound" / "Inbound", default "Outbound", validation surfaces an inline error if the new (parent_role, rail, direction) uniqueness rule is violated on Save. Round-trips through `editor.py::save_limit_schedule` → `serializer.py::_dump_limit_schedule` → loader cleanly. Per cross-cutting leg 2: the Studio user MUST be able to declare an Inbound cap entirely through the Studio UI without opening the L2 yaml.
- [ ] **AB.1.8 — Dashboard wiring.** Limit Breach sheet's table gains a "Direction" column (Inbound / Outbound) — same sheet, no split. Today's Exceptions inherits the new rows automatically (it reads the same matview).
- [ ] **AB.1.9 — Tests.** Validator (direction enum), schema (matview shape per dialect — pg + oracle + sqlite), seed (`InboundCapBreachPlant` deterministic + plant-coverage ≥1 row), audit PDF (limit_breach table includes inbound), 4-way agreement.
- [ ] **AB.1.10 — Docs + handbook walkthrough.** `concepts/l2/index.md` documents the field. New `walkthroughs/customization/how-do-i-add-an-aml-inbound-cap.md` worked-example walkthrough. `Schema_v6.md`'s LimitSchedule section gains the direction note.
- [ ] **AB.1.11 — Re-lock seeds, verify (unit + db + AW probe), commit.**

### AB.2 — Enhancement 3: Template-as-chain-child (symmetric with template-as-chain-parent)

Today's chain `child:` field accepts Rail only. Two-template chains (e.g. `InternalTransferCycle → InternalTransferBatch`) force an awkward "pick one of the child template's leg_rails" workaround.

**Preferred shape** (gap doc §3): allow `child: TransferTemplate` symmetric to `parent: TransferTemplate`. Semantics: first leg_rail firing of the child template sets the shared Transfer's `Parent` via the firing's `parent_transfer_id` metadata; subsequent firings join via lookup-or-create without rewriting Parent.

- [ ] **AB.2.1 — `loader.py`: chain `child:` accepts TransferTemplate identifier.** Currently rejects (or silently accepts and breaks downstream — verify). Loader resolves the identifier against rails + templates.
- [ ] **AB.2.2 — `validate.py`: new rule** — if `child` is a TransferTemplate, every leg_rail of that template MUST accept `parent_transfer_id` as a posted metadata key (auto-derived from the chain relationship). Catches the ETL contract at load time.
- [ ] **AB.2.3 — `schema.py` + new matview: "Chain Parent Disagreement" L1 invariant.** Gap doc §3 locked: when subsequent leg_rail firings claim a *different* `parent_transfer_id` than the first-firing-wins Parent, **reject as L1 Conservation-style violation** — surface the ETL bug rather than hiding it. New matview `<prefix>_chain_parent_disagreement` (or fold into an existing Conservation-shape matview) flags any child Transfer where the set of `parent_transfer_id`s across its leg_rail firings has cardinality > 1.
- [ ] **AB.2.4 — `schema.py`: chain-firing matview handles template-as-child.** The first-firing-wins Parent assignment lands here; AB.2.3 catches the disagreement case.
- [ ] **AB.2.5 — `seed.py`: two-template chain firings.** Chain firing where child is a template: emits leg_rail firings against the child template's lookup-or-create with `parent_transfer_id` resolved to the parent's Transfer.
- [ ] **AB.2.6 — `auto_scenario.py`: new `TwoTemplateChainPlant` + `ChainParentDisagreementPlant` (per AB.2.3).** Coverage assertions per pattern.
- [ ] **AB.2.6.fuzz — `tests/l2/fuzz.py`: emit template-as-chain-child shapes.** Currently `random_l2_yaml` synthesizes chains with rail children only; extend it to occasionally (~25% of chains, deterministic per seed) pick a TransferTemplate child + auto-derive the `parent_transfer_id` metadata-key declaration on every leg_rail of that template per AB.2.2's validator rule. Without this, the fuzz axis never exercises the two-template Conservation-violation matview that AB.2.3 ships.
- [ ] **AB.2.6.spec — `tests/l2/spec_example.yaml`: add 1 two-template chain.** Pick any two existing templates and chain them. Re-lock per dialect. Picks up the AB.2.4 matview's first-firing-wins parent semantic in deterministic snapshots.
- [ ] **AB.2.7 — `sasquatch_pr.yaml` real-world flavor.** Add the `InternalTransferCycle → InternalTransferBatch` chain from the gap doc, or a similar two-template chain that shows up natural in the merchant-acquiring flow. Plants demonstrate both the healthy case + the chain-Parent-disagreement violation case.
- [ ] **AB.2.8 — `topology.py` + Studio diagram + chain UI.** Chain edges with template children render with a distinct visual treatment (template box vs rail box). Studio chain card's children multi_select sources both rails AND templates (was rails-only).
- [ ] **AB.2.9 — Dashboard wiring.** Existing chain-aware sheets (PostedRequirements panel on L1; L2 Flow Tracing's Chains sheet) handle template children gracefully. Chain-Parent-disagreement violations (AB.2.3 matview) surface as a new row category on Today's Exceptions, alongside Conservation-shape violations.
- [ ] **AB.2.10 — Tests.** Validator (template-as-child accepts; legacy-style rejects appropriately), schema (chain matview shape), seed (chained-template firings produce expected parent linkage), L1 invariants (PostedRequirements still apply per child leg_rail), 4-way agreement.
- [ ] **AB.2.11 — Docs + handbook walkthrough.** Update `concepts/chain.md` to document the symmetric semantics. New `walkthroughs/customization/how-do-i-chain-two-templates.md`. `Schema_v6.md` chain section updated.
- [ ] **AB.2.12 — Re-lock seeds, verify, commit.**

### AB.3 — Enhancement 1: TransferTemplate multi-Variable + leg_rails XOR

Today's C1 ("at most one Variable-direction leg per template") blocks the natural model for per-mode operating variants (e.g. `MerchantCardSaleAutoSettle` / `StandardSettle` / `SlowSettle` as Variable closing legs, exactly one firing per cycle based on merchant config).

**Preferred shape** (gap doc §1): restate C1 as "per *firing* across XOR-grouped legs"; add `leg_rail_xor_groups: [[v1, v2, v3]]` field on TransferTemplate, mirroring the chain XOR pattern. Validator rule: members must all appear in the template's leg_rails.

- [ ] **AB.3.1 — `primitives.py`: `leg_rail_xor_groups: tuple[tuple[Identifier, ...], ...] = ()` on TransferTemplate.**
- [ ] **AB.3.2 — Loader + serializer + validator.** Loader parses the new field; validator: members ⊆ leg_rails; rewrite C1 as "at most one Variable-direction leg fires per Transfer (XOR-grouped Variables resolve to one firing per group; non-grouped Variables: still at most one)."
- [ ] **AB.3.3 — Schema + XOR resolution evidence (gap doc §1 locked).** Gap doc §1 locked: XOR resolution evidence is **implicit-from-firing-existence** — validation reads the leg_rail rows that actually posted; no separate `variant_selector` MetadataKey is added. Concretely: no new column on `<prefix>_transactions`, no schema-level grouping marker; the existing `transfer_type` / `rail_name` per leg already identifies which variant fired. C1's enforcement matview (`<prefix>_xor_group_violation` or fold into existing PostedRequirements matview) counts rows per (Transfer, XOR group) and flags >1 (overlap) or =0 once the template's Completion deadline has passed (missed firing — the AB.3.5 plant case).
- [ ] **AB.3.4 — `seed.py`: per-firing variant resolution.** For each cycle the chain fires, deterministically pick one variant from each XOR group (seeded RNG, same shape as today's chain-XOR resolution).
- [ ] **AB.3.5 — `auto_scenario.py`: new `XorVariantMissedFiringPlant`** — XOR group has zero firings ⇒ surfaces as stuck_unbundled-shape (Transfer never closes because no Variable variant fired). Plant primitive + coverage assertion.
- [ ] **AB.3.5.fuzz — `tests/l2/fuzz.py`: emit `leg_rail_xor_groups` on TransferTemplates.** When `random_l2_yaml(seed)` synthesizes a template with ≥3 Variable-direction leg_rails, occasionally group 2-3 of them into an XOR group (deterministic per seed). Members must be a subset of the template's leg_rails per AB.3.2's validator. Catches the C1-rewrite + XOR-resolution code paths under fuzz; without it, every synthesized template stays single-Variable.
- [ ] **AB.3.5.spec — `tests/l2/spec_example.yaml`: add 1 multi-Variable template with an XOR group.** The spec_example today has no XOR groups (C1 forbids them pre-AB.3). Pick any existing template with ≥2 Variable legs (or add legs) and declare the XOR group. Re-lock per dialect.
- [ ] **AB.3.6 — `sasquatch_pr.yaml` real-world flavor.** Add the gap-doc's three settlement-timing variants (`MerchantCardSaleAutoSettle` / `StandardSettle` / `SlowSettle`) to `MerchantSettlementCycle.leg_rails` + the matching XOR group. Per-merchant config picks the variant; demo plants show all three firing across the merchant population.
- [ ] **AB.3.7 — Studio editor: TransferTemplate card.** New `leg_rail_xor_groups` UI — list of multi_selects, each sourcing the template's own leg_rails.
- [ ] **AB.3.8 — Topology + dashboard.** Topology diagram renders XOR-grouped leg_rails with a visual hint (dashed grouping?). PostedRequirements / Pending Aging / Unbundled Aging gain "per variant" rollups.
- [ ] **AB.3.9 — Tests.** Validator (XOR rule, C1 rewrite), schema (matview shape unchanged but verify with multi-Variable fixture), seed (per-cycle variant resolution deterministic), L1 invariants (PostedRequirements per variant, stuck_unbundled fires when XOR misses), 4-way agreement.
- [ ] **AB.3.10 — Docs + handbook walkthrough.** `concepts/transfer-template.md` documents the XOR pattern. New `walkthroughs/customization/how-do-i-add-multi-mode-settlement.md`. `Schema_v6.md` TransferTemplate section updated.
- [ ] **AB.3.11 — Re-lock seeds, verify, commit.**

### AB.4 — Enhancement 2: N:1 chain fan-in

Today's `Transfer.Parent` is single-valued. Chains where N parent firings want to share one child Transfer (the batched-payout pattern) can't be expressed structurally — integrators fall back to metadata-only correlation (`batch_id` on each leg + ETL discipline).

**Preferred shape** (gap doc §2(B), user-ratified): new `fan_in: true` flag on Chain. Chains marked `fan_in` allow N parent firings to share one child Transfer; ETL writes each child leg with its contributing parent's id in JSON metadata (the existing per-leg `parent_transfer_id` convention); a new matview derives the multi-parent set per child Transfer.

- [ ] **AB.4.1 — `primitives.py`: `fan_in: bool = False` on Chain.** Backward-compat default.
- [ ] **AB.4.2 — Loader + serializer + validator.** Loader parses; validator: if `fan_in=true`, child must be TransferTemplate (gap doc §2 footnotes — "what if fan_in child is a Rail" is undefined; close that door). A fan_in chain MAY have multiple chains writing into one child — that's the whole point; validator allows.
- [ ] **AB.4.3 — Schema: `<prefix>_transfer_parents` matview (long form).** Derived from `<prefix>_transactions` — one row per `(child_transfer_id, parent_transfer_id)` pair, computed via `SELECT DISTINCT JSON_VALUE(metadata, '$.parent_transfer_id'), transfer_id FROM <prefix>_transactions WHERE metadata IS JSON AND JSON_EXISTS(metadata, '$.parent_transfer_id')`. No base-table schema change; no `<prefix>_transactions` column added; no ETL contract change. Wired into `refresh_matviews_sql(l2_instance)` alongside the existing L1 invariant matviews. SQLite emits as `CREATE TABLE … AS SELECT` per the existing dialect pattern.
- [ ] **AB.4.4 — `seed.py`: fan_in chain firings.** Multiple parent firings each write child-template legs with their contributing `parent_transfer_id` in metadata; the AB.4.3 matview derives the multi-parent set on refresh.
- [ ] **AB.4.5 — `auto_scenario.py`: `FanInChainPlant` + `FanInChainMissingParentPlant` (orphan child — parent set incomplete) + `FanInChainExtraParentPlant` (parent set includes a Transfer that shouldn't be in the batch).**
- [ ] **AB.4.5.fuzz — `tests/l2/fuzz.py`: emit `fan_in: true` on synthesized chains.** When `random_l2_yaml(seed)` synthesizes a chain whose child is a TransferTemplate (per AB.2.6.fuzz), occasionally (~20% of those chains, deterministic per seed) set `fan_in: true`. Each fan-in chain needs multiple parent firings per child Transfer in the synthesized scenario — `seed.py`'s fan-in path (AB.4.4) handles that. Catches the multi-parent matview shape under fuzz.
- [ ] **AB.4.5.spec — `tests/l2/spec_example.yaml`: add 1 fan-in chain.** Pick (or add) a TransferTemplate that's a child of a chain, mark the chain `fan_in: true`, ensure ≥2 parent firings per child instance in the deterministic seed scenario. Re-lock per dialect. The `<prefix>_transfer_parents` matview's multi-parent rows will land in the deterministic snapshots — first scenario to exercise cardinality > 1 on the parent set.
- [ ] **AB.4.6 — `sasquatch_pr.yaml`: add batched-payout flow** (gap doc §2's `MerchantPayoutBatch` example). Demo plants show healthy batching + the two violation cases.
- [ ] **AB.4.7 — Schema matview: new L1 invariant** — "every fan_in child Transfer's contributions match its expected parent set" (the gap-doc audit-trail use case). New matview `<prefix>_fan_in_disagreement` joins `<prefix>_transfer_parents` (AB.4.3) against the per-chain expected-parent-set rule and flags mismatches.
- [ ] **AB.4.8 — Dashboard + audit PDF: new violation kind** — fan-in disagreement on Today's Exceptions + a new per-invariant table on the audit PDF.
- [ ] **AB.4.9 — Studio chain card + topology (leg 2 of the contract).** New `fan_in` `FieldSpec` on the Chain editor card: boolean checkbox, default unchecked, with inline validation surfacing the AB.4.2 rule (fan_in requires child=TransferTemplate; rejected if checked while child is a Rail). `topology.py` renders fan-in chain edges with a distinct visual treatment (e.g., bundled-arrow notation or labeled "fan-in" badge) so the diagram reader sees the N:1 shape without reading the chain's yaml. Per cross-cutting leg 2: the Studio user MUST be able to declare a fan-in chain entirely through the Studio UI.
- [ ] **AB.4.10 — Tests.** Validator (fan_in implies template-as-child), schema (`<prefix>_transfer_parents` matview shape per dialect — pg + oracle + sqlite), seed (deterministic fan-in firings + multi-parent metadata), L1 invariants (fan_in_disagreement fires when plants violate), 4-way agreement.
- [ ] **AB.4.11 — Docs + walkthrough.** New `concepts/chain-fan-in.md` documenting the semantics + when to use fan_in. New `walkthroughs/customization/how-do-i-model-batched-payouts.md`. Schema_v6 chain section updated.
- [ ] **AB.4.12 — Re-lock seeds, verify, commit.**

### AB.5 — End-of-phase

- [ ] **AB.5.1 — Verify (full 13-cell db matrix + browser canary).** Same shape as Z.D.1 (`./run_tests.sh up_to=db` no-flags + a thin browser-layer `--scenarios=sp --dialects=pg --targets=aw` canary). Deterministic cells must all be green; fuzz-cell instability tracked separately.
- [ ] **AB.5.2 — Commit, archive Phase AB to PLAN_ARCHIVE.md, push.** Add Phase AB one-liner to the Phase history section. Cut a release tag (likely `v10.0.0a8` or later in the alpha train).

---

## Phase AC — Rename to `recon-gen` *(COMPLETE — shipped v11.0.0 2026-05-17; full plan archived in `PLAN_ARCHIVE.md` → "Phase AC")*

---

## Phase AD — QuickSight Standard edition migration + cost containment

Surfaced 2026-05-17 by a "nasty fee" alert from AWS. Cost audit (root profile) decoded the Generative BI burn that triggered it: `USE1-QuickSuite-Index` line item ran $107.71 across the first 17 days of May (7.5× the $14.28 April rate), credit-offset to ~$0 but accelerating fast. Operator's same-day fix landed the 4 GenBI opt-outs (Pro=0, Topics=0, indexing=DISABLED, Dashboard Q&A=DISABLED — all confirmed via `scripts/disable_quicksight_genai.sh audit` against the root profile). With GenBI off, the durable QuickSight cost surface is the **Enterprise edition subscription itself** (~$72/mo for 3 Author/Admin users at ~$24 each). Standard edition (~$9/Author/mo) would cut that ~3× — but Standard has no `generate_dashboard_embed_url` API, which means the `QsEmbedDriver` (and every browser e2e cell that routes through it) breaks.

**Staging decision (user-confirmed 2026-05-17):** AD.A executes now while AA's remaining work + AB are still being validated against QS Enterprise. AD.B-F (code refactor + downgrade ceremony) is gated on AA + AB close — those phases need a working QS validation path, which Standard would remove. Once AB ships clean against Enterprise, the project no longer depends on the Enterprise-only embed API and can downgrade.

**Scope of "nasty fee" diagnostic artifacts** (already in repo, not part of any AD task):
- `scripts/disable_quicksight_genai.sh` — 9-step audit (4 GenBI surfaces + Users / VPC / SPICE / Resource Sprawl / Cost Trend). Reusable for future cost drift detection.
- `cli/audit` paths in this conversation surfaced the resource sprawl count (146 dashboards / 147 analyses / ~1700 datasets / 63 datasources across 3 tag generations: `quicksight-gen` pre-rename CI, `recon-gen` current CI, untagged manual).

### AD.A — Pre-migration resource cleanup *(active — Enterprise-time work)*

The 1700-dataset / 146-dashboard sprawl is a cost lever regardless of edition (Enterprise + Standard both bill per-resource for some surfaces). The `recon-gen json clean --execute` sweep gates on `Deployment == cfg.deployment_name` and only touches resources tagged with the current cfg's deployment name — it won't touch CI residue from past chain runs or pre-rename `quicksight-gen` tagged work. Need a broader sweep before the downgrade.

- [ ] **AD.A.1 — Audit the three tag generations + categorize.** Walk every dashboard / analysis / dataset / datasource; bucket by `ManagedBy` tag (`recon-gen` / `quicksight-gen` / untagged) + by `Deployment` tag (current cfg's vs. anything else vs. none). Output: a count matrix + a target list of "deletable: yes/no" per resource. Untagged dashboards (the `test dashboard` shape) need manual review — could be intentional manual artifacts.
- [ ] **AD.A.2 — Extend `recon-gen json clean` to sweep historical tag values.** Add `--include-managed-by="quicksight-gen,recon-gen"` flag (default current = `recon-gen` only) so the pre-rename CI residue can be swept in one pass. Add `--all-deployments` flag (gated on `--execute` + explicit `--yes` confirmation) for the AD.A scope. Unit-test the tag-match logic; do NOT test against live AWS in unit suite (the existing `tests/e2e/test_cleanup.py` shape covers that).
- [ ] **AD.A.3 — Dry-run sweep against the AD.A.1 audit; confirm targets.** `recon-gen json clean --all-deployments --include-managed-by=quicksight-gen,recon-gen` (no `--execute`) should list every CI-residue resource. Cross-check against the audit list; reconcile any unexpected keeps/excludes.
- [ ] **AD.A.4 — Execute the sweep.** Same command + `--execute --yes`. Expect ~140 dashboards / ~140 analyses / ~1500 datasets to delete. Datasources are shared across deployments — preserve any used by surviving resources (sweep logic must check ref-count before delete).
- [ ] **AD.A.5 — Re-run `disable_quicksight_genai.sh audit`; capture post-sweep baseline.** Should show ~6 dashboards (the 4 apps + manual + ci-bot baseline) / similar analyses / ~50 datasets / ~3 datasources. Save the output to `docs/audits/ad_a_post_sweep_baseline.md` as the reference point for future drift detection.
- [ ] **AD.A.6 — Grant `recon-gen-local` the QS perms the audit script needs.** The IAM user's policy (`docs/audits/_iam/recon-gen-local-policy.json`) is missing `quicksight:ListTopics`, `quicksight:DescribeQuickSightQSearchConfiguration`, `quicksight:DescribeDashboardsQAConfiguration`. Add them so the script runs without needing root. Stays narrow — read-only audit perms, no Update/Delete.
- [ ] **AD.A.7 — Wire weekly cost-audit GHA cron.** New `.github/workflows/quicksight-cost-audit.yml` running `scripts/disable_quicksight_genai.sh audit` on cron (weekly Sunday 06:00 UTC). Posts to Step Summary; on detection of any Generative BI signal flip (Pro user added, Topic created, indexing re-enabled, Q&A re-enabled) opens an issue. Uses the `recon-gen-local` IAM user post-AD.A.6 perm grant.
- [ ] **AD.A.8 — AWS Budget alert at $20/mo for QuickSight.** Boto3 `budgets:CreateBudget` script — one-shot, not recurring. Notification email = `chris@hotchkiss.io`. Threshold = 75% of $20 (alert before bill arrives). Commit the script to `scripts/` so the budget can be re-created if blown away.
- [ ] **AD.A.9 — Commit + verify chain (unit only — no AWS in unit layer).** Standard chain shape; the live cleanup happens in AD.A.4 manually (not in CI).

### AD.B — DashboardDriver decoupling from embed API *(blocked on AB.5.* close)*

Enterprise → Standard breaks `generate_dashboard_embed_url`. Either drop the QS browser leg entirely (lose validation coverage; rely on App2 + boto3 API tier) or implement web-UI login flow (clunkier, but preserves QS coverage). Decide after AB ships, when we know what visual-validation footprint we actually need going forward.

- [ ] **AD.B.1 — Spike: web-UI login flow on a sandbox Standard subscription.** Verify Playwright can drive the IAM federation / SSO flow + persist storage_state across tests. Decision: Path A (clunky-but-real QS coverage) vs Path B (drop QS browser leg).
- [ ] **AD.B.2 — Gate `QsEmbedDriver` behind cfg `quicksight_edition: enterprise|standard`.** Enterprise = current embed flow; Standard = either web-UI flow (if AD.B.1 picked Path A) or `NotImplementedError` (Path B). `_parametrized_dashboard_driver` skips the [qs] param when edition=standard + Path B.
- [ ] **AD.B.3 — `test_audit_dashboard_agreement.py` 4-way → 3-way fallback.** When the QS leg is unavailable, the cross-tool agreement test runs `scenario_plants ⊆ direct_matview_SELECT == App2 (== PDF for drift)` — drops the QS column without failing.

### AD.C — CI workflow adjustments *(blocked on AD.B)*

- [ ] **AD.C.1 — Drop / convert `e2e-pg-browser` job in `e2e.yml`.** If AD.B picked Path A, swap to the web-UI login fixture; if Path B, delete the job + `needs:` references.
- [ ] **AD.C.2 — `release.yml::e2e-against-testpypi` browser tier handling.** Same decision shape; prod-publish gate stays load-bearing, but its QS leg follows the AD.B path.
- [ ] **AD.C.3 — Per-PR cleanup verification.** Assert all CI-created QS resources are tag-swept post-run (no orphans). New `tests/e2e/test_cleanup_completeness.py` shape: after a deploy → teardown cycle, `list-{dashboards,analyses,data-sets}` should return zero resources tagged with the test run's deployment_name.

### AD.D — Documentation sweep *(blocked on AD.B-C)*

- [ ] **AD.D.1 — README + CLAUDE.md: Standard is the supported tier.** Drop references to embed-only flows. Document the cfg edition flag.
- [ ] **AD.D.2 — Quirks log: prune embed-iframe quirks; add Standard-specific ones.** The `QsEmbedDriver`-specific entries (data-automation-id selectors, START_VIS/STOP_VIS WS frames, parameter-control URL-write quirk) shift to "Enterprise-only — kept for historical operators".
- [ ] **AD.D.3 — Operator runbook: self-host with Standard.** No per-tenant namespacing (Standard has one namespace). No row-level security. Document the workarounds.

### AD.E — The downgrade ceremony *(blocked on AD.A-D close)*

- [ ] **AD.E.1 — Pre-flight checklist** (re-run AD.A's audit script; confirm Pro=0, Topics=0, VPC=0, indexing=DISABLED, Q&A=DISABLED — these are already true today per the 2026-05-17 audit, just re-verify before the irreversible step).
- [ ] **AD.E.2 — Console downgrade Enterprise → Standard.** No CLI for this; AWS Console only. Operator action; document the exact navigation path in the runbook.
- [ ] **AD.E.3 — Verify subscription edition = STANDARD** via `describe-account-subscription`.
- [ ] **AD.E.4 — Full CI rerun on Standard subscription** to confirm nothing depends on Enterprise APIs.
- [ ] **AD.E.5 — Tag v11.1.0 (or v12.0.0 if Standard-specific breakages forced an API-surface change).** RELEASE_NOTES entry covers the migration.

### AD.F — Long-tail cost monitoring *(carries forward; no close gate)*

- [ ] **AD.F.1 — Monthly review of `disable_quicksight_genai.sh audit` step-summary output.** First-of-month task; if any drift, action immediately.
- [ ] **AD.F.2 — Quarterly resource-sprawl audit.** Even tag-gated cleanup can miss things; quarterly count check catches drift.

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
- [ ] **Dashboards-local L1 dashboard render errors (surfaced 2026-05-10, X.2.g.4 territory, NOT a Y.2.g regression).** With the Y.2.g.2.d pool-lifespan fix landed, `dashboards --app l1_dashboard` now starts cleanly + the drift KPI fetches data from the live matview, but other L1 visuals throw render errors in Dashboards (operator observed during the manual local pass; smoke + drift KPI work, broader rendering doesn't). This is per-visual coverage in `_tree_fetcher` / `wrap_for_visual` — investigation/L2FT shipped via X.2.g.{2,3} with the same pattern, so the gap is L1-specific visual kinds the renderer hasn't grown arms for yet (KPIs work, tables / line-charts may not). Triage: capture the failing visual_ids + the renderer error, extend `_tree_fetcher.wrap_for_visual` with the missing arms, mirror the Investigation/L2FT shape. Out of Y.2.g scope (Dashboards visual coverage ≠ pushdown SQL); on the X.2.g roadmap.
- [ ] **CI/release cleanup steps target the wrong scope — `database-2` + QS leak (captured 2026-05-10; non-trivial, pick a fix before doing).** Three related bugs, all "the cleanup ran but cleaned the wrong thing", all harmless functionally (no impact on the release publishing / e2e passing) but they leak resources:
    1. **`e2e.yml::cleanup-pg::schema clean -c /tmp/ci-pg.yaml`** (no `--l2`) drops `spec_example_*` tables — but `e2e-pg-api` runs the runner with `--variants=sp_pg_aw`, which synthesizes an L2 with `instance: sp_pg_aw` → creates `sp_pg_aw_*` tables. So `cleanup-pg` drops a table set nothing created; the actual `sp_pg_aw_*` (and on cron, `sp_pg_aw_*` from `e2e-pg-browser`) accumulate in the operator's `database-2` forever. (Pre-existing, surfaced during the gate.l.8 work.)
    2. **The runner's `teardown_variant` for an `aw` target doesn't `DROP` the per-variant `<spec.name>_*` schema it created** (it only no-ops the AWS env / drops `lo` containers). This is the *source* of #1's leak — if the runner self-cleaned its aw tables, `cleanup-pg` wouldn't need to schema-clean at all.
    3. **`release.yml::e2e-against-testpypi::Cleanup (always)` passes `--l2 /tmp/release-l2.yaml` to `quicksight-gen json clean`** — but `json clean` has no `--l2` option (only `-c` / `-o` / `--all` / `--execute`), so it exits nonzero, `|| true` swallows it, and the step is a no-op → the `qs-release-<tag>-rel_<tag>-*` QS resources (deployed with `--l2 /tmp/release-l2.yaml` → tagged `L2Instance: rel_<tag>`, which `json clean` defaulting to `spec_example` won't match anyway) linger in QuickSight. **Fix:** swap that line to `json clean -c /tmp/release-e2e.yaml --all --execute` — `--all` purge mode sweeps everything matching the cfg's `resource_prefix` (`qs-release-<tag>`) regardless of L2Instance tag, which is exactly the one resource set the release-e2e job deployed. (Introduced by gate.l.8, 2026-05-10.)
    **Fix options for #1+#2:** (a) make the runner's `teardown_variant` `DROP` the `<spec.name>_*` tables on `aw` teardown — robust, but a runner change touching the seed/teardown path; (b) have `cleanup-pg` re-synthesize the variant L2 (`sed "s/^instance:.*/instance: sp_pg_aw/" tests/l2/spec_example.yaml > /tmp/sp.yaml; schema clean -c /tmp/ci-pg.yaml --l2 /tmp/sp.yaml`) — quick but hardcodes the variant names; (c) a "sweep test tables" step that drops any `<test-variant-pattern>_transactions`-shaped table. **Recommend (a)** — it's the right home for "the runner cleans up after itself". #3 is a trivial one-liner (use `--all`); fold it into whichever release.yml touch comes next (or a tiny hotfix). All three are queued, not started — pick the approach when convenient.

### Known platform limitations — do not re-attempt without new evidence

- **QS URL-parameter control sync** — K.4.7 cross-app drills dropped. URL fragment sets the parameter store but doesn't push values into bound controls. Re-entry conditions: AWS fix, custom embedded app via `setParameters()` SDK, or a new URL form that triggers control sync. See `PLAN_ARCHIVE.md` for full re-entry details.
- **QS dropdown click target is the middle grey bar** — `ParameterDropDownControl` only opens on the inner grey bar; clicking the visible edge does nothing. Suggest before investigating "unresponsive dropdown" reports.
- **QS silent-fail mode** — datasets healthy + describe-cleanly, every visual on every sheet shows the spinner forever. See CLAUDE.md → Operational Footguns for the diagnostic ladder.
