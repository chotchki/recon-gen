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

_Phase S / T / U / V / W / Y / X.2 sub-task detail in `PLAN_ARCHIVE.md`. RELEASE_NOTES `v8.{1,2,3,4,5,6,8}.x` / `v9.x` carry the per-phase + per-release narratives._

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

### X.4 — Studio: implementation tools (integrator + trainer + ETL engineer)

**SPEC.** [`SPEC_studio.md`](SPEC_studio.md) — drafted on `x-4-5-spec-studio` from `docs/x_4_5_design_thoughts.md`. Read it first; the PLAN below derives from the SPEC, not the other way around. **The X.4 / X.5 split from the original plan is folded:** Studio is one phase covering both the editor (was X.4) and the ETL helper (was X.5). The persona reframe ("implementation tools") and the orchestrator pipeline (one Deploy button, one process) make them the same surface.

**Three personas, one front door.** The integrator (YAML editor + unified diagram), the trainer (plant-toggle + day-stepper + plant-timeline), the ETL engineer (`etl_hook` + `etl_datasource` pull + `scope: uncovered_rails` + coverage overlay) all reach Studio via `quicksight-gen studio`. Dashboards (the X.2 wrap surface, renamed from "App 2") is mounted under Studio and is also independently runnable via `quicksight-gen dashboards`.

**Shape of the work.**
- **Hard sequential bottleneck:** the diagram-renderer spike (`X.4.b`). The renderer choice (D3 + d3-force vs enhanced graphviz) gates everything that touches the unified diagram. Spike, judge, lock, then proceed.
- **Parallelizable once foundations land:** editor forms (`X.4.f` — "trivial agent-worthy work" per user), shaping panel (`X.4.h`), pipeline orchestration (`X.4.g`) all branch off the cascade primitives + Deploy endpoint.
- **CLI rename is a clean cut:** `serve app2 apply` → `dashboards` (no deprecation alias); the `serve` Click group goes away. v10.0.0 ships when Studio MVP is usable.
- **Testing scope is narrowed** vs X.2 (per SPEC's "Testing scope"): the cross-dialect pull is a small targeted matrix (PG → SQLite primary; Oracle → SQLite + SQLite → SQLite secondary), not the X.2-era 13-cell `scenario × dialect × target` fan-out. The codebase keeps the dialect support; we just don't extend the matrix to Studio's surface.

**Process discipline (carried from CLAUDE.md):** ticking goes inline. When work expands beyond a checkbox, split it: tick what landed, add a new unchecked item for what's left. When a gap surfaces, add it. When a task turns out to be wrong, mark + replace. Phases exit only when every box is ticked + e2e green + docs updated, then summarize + sweep to PLAN_ARCHIVE.md.

#### X.4.a — Foundations: process model + the severable mount

- [ ] **X.4.a.1** — `quicksight-gen studio` Click command in `cli/`; Starlette app mounts Dashboards routes + Studio routes.
- [ ] **X.4.a.2** — `quicksight-gen dashboards` Click command (replaces `serve app2 apply`); same app mounts Dashboards routes only.
- [ ] **X.4.a.3** — Severability test: `dashboards` runs cleanly with Studio routes absent (no shared in-memory cache that Dashboards reads).
- [ ] **X.4.a.4** — Studio landing route (`GET /`) — minimal placeholder; asserts the mount + routes resolve.
- [ ] **X.4.a.5** — `serve app2 apply` and the `serve` Click group removed; usage swept across `tests/` + `docs/` + `CLAUDE.md` + `README.md`.
- [ ] **X.4.a.6** — In-memory `L2Instance` cache on the server; `save_l2(path, instance)` writer (atomic: write to temp, fsync, rename). Reload-on-file-change is OUT OF SCOPE (Studio writes; nobody else writes).

#### X.4.b — Diagram renderer spike (timeboxed, gates X.4.c)

- [ ] **X.4.b.1** — Adapter that emits the d3-force JSON shape from `common/l2/topology.py`'s graph model — full graph (roles + scope, rails bundled, SingleLeg self-loops, templates, chains).
- [ ] **X.4.b.2** — **Spike arm A: D3 + d3-force** tuned against `sasquatch_pr` — try parents-above-children, edge bundling, spread-to-fill, toggles, focus.
- [ ] **X.4.b.3** — **Spike arm B: enhanced graphviz** — post-process `dot`'s SVG with data-attrs per node + edge + JS handlers for click-to-focus + type-toggle.
- [ ] **X.4.b.4** — Compare on the SPEC's "good enough" criteria (legible on `sasquatch_pr`, all four entity-type toggles work, click-focus works, coverage tint works). Capture the judgment call in a short spike doc under `docs/audits/`.
- [ ] **X.4.b.5** — Lock the renderer for the X.4.c work; record the choice in `SPEC_studio.md`.

#### X.4.c — The unified diagram (build against the renderer that won)

- [ ] **X.4.c.1** — Studio route serving the chosen diagram for the current L2.
- [ ] **X.4.c.2** — Toggle-by-entity-type chrome (Accounts / Rails / Chains / Templates checkboxes).
- [ ] **X.4.c.3** — Click-a-node → focus connected subgraph (everything not directly connected dims).
- [ ] **X.4.c.4** — Reset filters → back to the full graph, all types visible.
- [ ] **X.4.c.5** — Coverage-tint overlay: `coverage_for(connection, prefix, l2_instance)` data fetcher (binary presence per L2 primitive, row-count on hover).
- [ ] **X.4.c.6** — Trainer-mode overlay: planted exceptions visually annotated on their host entities (reads the same scenario object the plant-timeline view consumes).
- [ ] **X.4.c.7** — JS unit tests in `tests/js/` shape (one harness per feature; renderer-specific).

#### X.4.d — Editor primitives: server-owned cascade

- [ ] **X.4.d.1** — `mutate_l2(instance, kind, id, fields) → L2Instance` — applies a single-entity mutation to the in-memory model, returns the new instance.
- [ ] **X.4.d.2** — `rename_identifier(instance, kind, old, new) → L2Instance` — walks every reference and rewrites; uses the typed Identifier wrappers (`common/ids.py` + `common/l2/primitives.py`).
- [ ] **X.4.d.3** — `serialize_l2(instance) → str` — re-serializes the YAML from the model (drops freeform `# comments`, preserves `description:`).
- [ ] **X.4.d.4** — `validate(instance)` hook on every save: validator raise → 400 + inline error fragment (no save).
- [ ] **X.4.d.5** — Delete primitive: subject to validator's reject-on-structural-break rule (a structural-break delete returns 400; user fixes the dependent first).
- [ ] **X.4.d.6** — Unit tests: mutate-round-trip (mutate → validate → serialize → load → assert equal), rename-touches-every-expected-reference, delete-rejected-when-dependent.

#### X.4.e — Editor: HTMX form discipline + cascade trigger

- [ ] **X.4.e.1** — Form template scaffolding (one Jinja partial per entity kind; field labels + helper text come from `common/l2/primitives.py` field docstrings — the X.6.a discipline laid down early so it doesn't need a sweep).
- [ ] **X.4.e.2** — `GET /l2_shape/<kind>/` — list view (rows, click to expand).
- [ ] **X.4.e.3** — `GET /l2_shape/<kind>/<id>` (read-only card) + `GET /l2_shape/<kind>/<id>/edit` (editable fragment).
- [ ] **X.4.e.4** — `PUT /l2_shape/<kind>/<id>` — the cascade flow: validate → mutate → save → respond with the new fragment + (if rippled) `HX-Trigger: l2-cascade-reload`.
- [ ] **X.4.e.5** — Validation-failure path: 400 + inline error fragment, targeted swap, preserves the user's typed content.
- [ ] **X.4.e.6** — `POST /l2_shape/<kind>/` create + `DELETE /l2_shape/<kind>/<id>`.
- [ ] **X.4.e.7** — Diagram + entity list listen for `l2-cascade-reload` and `hx-get` themselves.

#### X.4.f — Editor forms (additive build order — parallelizable per entity)

- [ ] **X.4.f.1** — Account form (flat — first; proves the per-entity pattern).
- [ ] **X.4.f.2** — Rail form — TwoLegRail subtype.
- [ ] **X.4.f.3** — Rail form — SingleLegRail subtype.
- [ ] **X.4.f.4** — Theme form (flat).
- [ ] **X.4.f.5** — Chain form (sub-list editor: required/xor-group children).
- [ ] **X.4.f.6** — TransferTemplate form (sub-list editor: leg-rail composition).

#### X.4.g — The "Deploy changes" pipeline

- [ ] **X.4.g.1** — `etl_hook` config field + V.1.b allowlist entry.
- [ ] **X.4.g.2** — `etl_datasource` config block (URL + `transactions` / `daily_balances` table allowlist) + V.1.b allowlist entry.
- [ ] **X.4.g.3** — `test_generator:` config block (`enabled` / `scope` / `end_date` / `seed` / `plants` / `only_template` / `derive_balances`) + V.1.b allowlist entry. Defaults preserve byte-identical-to-locked-seeds output.
- [ ] **X.4.g.4** — Step 1 (`etl_hook` gate): subprocess run; stream stdout/stderr to `/dev_log`; exit-code halts BEFORE step 2 if non-zero (demo DB never touched).
- [ ] **X.4.g.5** — Step 2 wipe: call `wipe_demo_data_sql(instance, dialect)` against `demo_database_url`, always (when we reach this step).
- [ ] **X.4.g.6** — Step 2 pull: cross-dialect copy from `etl_datasource` to `demo_database_url`, filtered to `≤ end_date`. Reuse `common/sql/dialect.py` + Oracle batcher.
- [ ] **X.4.g.7** — Step 3 generator: `emit_full_seed` against the current `test_generator:` knobs; always additive (the wipe was step 2).
- [ ] **X.4.g.8** — Step 3 — `scope: full` mode (today's behavior; byte-matches locked seeds when no `etl_datasource`).
- [ ] **X.4.g.9** — Step 3 — `scope: exceptions_only` mode (skip baseline, plants only on top of whatever step 2 produced).
- [ ] **X.4.g.10** — Step 3 — `scope: uncovered_rails` mode (inspect `demo_database_url` post-step-2, fill baseline only for rails without rows).
- [ ] **X.4.g.11** — Step 4 matview refresh: existing `refresh_matviews_sql(instance)`.
- [ ] **X.4.g.12** — Step 5 reload: bump a process-local `data_generation_id`; Dashboards' open page polls (or subscribes to) the counter and reloads its current URL on bump.
- [ ] **X.4.g.13** — `POST /deploy` orchestration endpoint that runs steps 1-5; returns a structured progress stream.
- [ ] **X.4.g.14** — Studio "Deploy changes" button (global, in the Studio header); calls `POST /deploy`; surfaces step progress + errors via `/dev_log`.
- [ ] **X.4.g.15** — Pipeline orchestration tests (one per shape: hook-fail-halt, no-etl path, etl-only path, etl-then-generator path, etl-then-`uncovered_rails` path).

#### X.4.h — Data-shaping panel UI

- [ ] **X.4.h.1** — Panel layout (which knobs are visible; how it sits alongside the diagram + the global Deploy button).
- [ ] **X.4.h.2** — Plant-toggle checkboxes (one per exception kind: drift / overdraft / limit_breach / stuck_pending / stuck_unbundled / supersession).
- [ ] **X.4.h.3** — Day stepper UI for `end_date` (← prev / next →; jump-to-day input).
- [ ] **X.4.h.4** — Random-seed input (number entry + "roll" button to randomize; pin/save current).
- [ ] **X.4.h.5** — `scope` selector (full / uncovered_rails / exceptions_only).
- [ ] **X.4.h.6** — Plant-timeline view: vertical day column, planted exceptions annotated per day under the *current* shaping config; click a day → `end_date` jumps + Deploy.
- [ ] **X.4.h.7** — Knob changes persist back to `config.yaml`'s `test_generator:` block (debounced); reloaded on next startup.
- [ ] **X.4.h.8** — Unit tests for plant-timeline derivation (given a scenario + scope + seed, which days hit?).

#### X.4.i — Additive knobs (ship later — not gating Studio MVP)

- [ ] **X.4.i.1** — `only_template` mode: scoped baseline = template + dependency closure (its leg-rails + the accounts those touch); rest of the L2 left empty.
- [ ] **X.4.i.2** — `derive_balances` mode: from subledger transactions in `demo_database_url`, derive control-account daily balances satisfying double-entry (the drift invariant run forward).
- [ ] **X.4.i.3** — UI controls for both in the data-shaping panel.

#### X.4.j — Testing scope (per SPEC's "Testing scope" section)

- [ ] **X.4.j.1** — Cross-dialect pull tests: PG → SQLite (primary), Oracle → SQLite, SQLite → SQLite (degenerate). NOT a full matrix — the codebase keeps the dialect support; we just don't fan it out for Studio's tests.
- [ ] **X.4.j.2** — Browser e2e: Studio + Dashboards integrated loop on `sasquatch_pr` + SQLite (edit YAML in Studio → Deploy → Dashboards reloads with the new data). Single test, NOT parametrized over `[qs, app2]`.
- [ ] **X.4.j.3** — Verify the existing X.2 13-cell `scenario × dialect × target` matrix + 4-way agreement test still pass unchanged.
- [ ] **X.4.j.4** — Verify the locked-seed determinism test stays green (default `test_generator:` knobs = today's output, byte-for-byte).

#### X.4.k — Wrap + release (v10.0.0)

- [ ] **X.4.k.1** — Update CLAUDE.md "Quick Reference" + Commands section: add `studio` / `dashboards`; remove all `serve app2 apply` references.
- [ ] **X.4.k.2** — README: add a "Studio" section near the top; rename "Self-hosted renderer (App 2)" → "Self-hosted renderer (Dashboards)".
- [ ] **X.4.k.3** — Update `docs/reference/self-host.md` for the rename.
- [ ] **X.4.k.4** — RELEASE_NOTES v10.0.0 entry (major: CLI verbs renamed, `serve app2 apply` removed, Studio shipped).
- [ ] **X.4.k.5** — Bump `__version__` to 10.0.0.
- [ ] **X.4.k.6** — End-of-phase verify: `./run_tests.sh up_to=app2` green; full pytest sweep green; one live AWS deploy still green.
- [ ] **X.4.k.7** — Tag v10.0.0; merge to main; push.
- [ ] **X.4.k.8** — Sweep X.4 detail to PLAN_ARCHIVE.md; collapse the in-PLAN entry to a one-line done summary.

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
