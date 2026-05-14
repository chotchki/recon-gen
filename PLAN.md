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

- [x] **X.4.a.1** — `quicksight-gen studio` Click command in `cli/`; Starlette app mounts Dashboards routes + Studio routes via `make_app(studio_routes=...)`.
- [x] **X.4.a.2** — `quicksight-gen dashboards` Click command (replaces `serve app2 apply`); same app mounts Dashboards routes only. Shared body lives in `cli/_html_serve.py::run_html_server`.
- [x] **X.4.a.3** — Severability test (`tests/unit/test_studio_severability.py`): runtime contract (Dashboards-only mount keeps `/` redirect; Studio mount overrides `GET /`) + import-time contract (`cli.dashboards` source has no Studio-side imports; the `_html_serve` cache construction is gated on `studio_routes_factory is not None`).
- [x] **X.4.a.4** — Studio landing route (`GET /`) — minimal placeholder in `common/html/_studio_routes.py::make_studio_routes(cache)`; renders the L2 instance prefix + entity counts so the cache wiring is visible in the page body.
- [x] **X.4.a.5** — `cli/serve.py` + the `serve` Click group deleted; `tests/cli/test_serve_smoke.py` rewritten as `test_dashboards_smoke.py`; references swept across `README.md` + `tests/` + the in-tree `docs/` + module docstrings.
- [x] **X.4.a.6** — `common/l2/cache.py`: `save_yaml_atomic(text, path)` (temp-in-same-dir + fsync + rename) + `L2InstanceCache(path, instance)` with `from_path` / `get` / `replace`. The eventual `cache.save()` (composes `serialize_l2` + `save_yaml_atomic` + `replace`) lands at X.4.d.3 alongside the serializer.

#### X.4.b — Diagram renderer spike (timeboxed, gates X.4.c)

- [x] **X.4.b.1** — Adapter that emits the d3-force JSON shape from `common/l2/topology.py`'s graph model — full graph (roles + scope, rails bundled, SingleLeg self-loops, templates, chains). Shipped as a typed value object (`TopologyGraph` / `TopologyNode` / `TopologyEdge`) BOTH spike arms consume — `topology_graph_for(instance)` walks the L2 once; `to_d3_force_json(graph)` serializes for arm A; the existing `build_topology_graph` (graphviz `Digraph`) refactored to consume the same value object via `_render_to_graphviz` so docs-site diagrams + the 17 existing topology tests stay byte-stable through the refactor (verified). 16 unit tests in `tests/unit/test_l2_topology_typed.py`; pyright clean.
- [x] **X.4.b.2** — **Spike arm A: D3 + d3-force** tuned against `sasquatch_pr`. Iterated through per-kind banding, parent/child role split, link-bound min/max, viewport clamp, 28 force knobs. Ultimately rejected — d3-force is a *tuning* tool, not a *layout* tool: every dataset wants different knob values, the spike became a knob-addition treadmill, and the user's mental model ("templates top / rails middle / roles bottom") wants enforced ranking which force-directed will never deliver. Surfaced the rails-as-first-class-nodes model insight (`to_d3_per_rail_json`) that arm B's dot pivot adopted.
- [x] **X.4.b.3** — **Spike arm B: enhanced graphviz** — post-process `dot`'s SVG with `data-kind` / `data-id` per node + `data-source` / `data-target` per edge; JS shim drives wasm-graphviz render + chrome wiring (visibility toggles, edge-label toggles, layer stepper, mode select, click-to-focus). Pivoted mid-spike: rails moved from edge-labels (where chain/template references duplicated them as plaintext nodes) to first-class nodes via `build_topology_graph_per_rail` — dot's rank algorithm gives the layered "src_role → rail → dst_role" reading deterministically with zero knobs. Bundle nodes consolidate parallel pure-connectivity rails (anchored rails stay individual). Template_role helper edges dropped (templates point only to rails). Click-to-focus reworked to server-rendered re-emit (`?focus=<node_id>`) — focused subset re-lays-out cleanly via dot, with smart 1/2-hop default per node kind (roles/templates=2 to cross a rail, rails/bundles=1).
- [x] **X.4.b.cleanup.1** — Default per-rail on `/diagram` (dropped `?model=` toggle entirely; only renderer that ships).
- [x] **X.4.b.cleanup.2** — Compactness pass: `nodesep=0.15` + `ranksep=0.35` + `mclimit=2.0` + `concentrate=true` baked into per-rail emit defaults.
- [x] **X.4.b.cleanup.3** — Hard-deleted arm A: `/diagram/d3` route, `_studio_assets/diagram_d3.{js,css}`, `to_d3_force_json`, `to_d3_per_rail_json`, d3-force-only tests in `test_l2_topology_typed.py`. d3 vendored asset stays (Investigation Sankey + ForceGraph use it).
- [x] **X.4.b.cleanup.4** — Dropped bundled mode entirely: `build_topology_graph` + `_render_to_graphviz` + `render_topology` + `filter_topology_graph_focus` + `_VALID_ENGINES` + `_filter_orphan_role_nodes` + the bundled-mode branch in `_render_diagram_page`. Deleted `tests/unit/test_l2_topology.py`. `topology.py` shrunk from 1893 → ~1170 lines.
- [x] **X.4.b.cleanup.5** — Pruned dead CSS: dropped `.dim` / `.focus` / `.focused` rules that were the click-to-dim era; kept the hover/highlight stylings.
- [x] **X.4.b.4** — Spike judgment doc at `docs/audits/x_4_b_diagram_renderer_spike.md` capturing: rails-as-nodes as the load-bearing model insight (independent of renderer); dot's deterministic rank vs d3-force's emergent-layout treadmill; the focus-as-rerender pattern; the post-spike polish.
- [x] **X.4.b.5** — Renderer + per-rail model locked in `SPEC_studio.md` "The graph model — typed projection + per-rail dot renderer" section.

#### X.4.c — The unified diagram (build against the renderer that won)

- [x] **X.4.c.1** — Studio route serving the chosen diagram for the current L2. (Delivered by X.4.b spike: `GET /diagram` per-rail/dot.)
- [x] **X.4.c.2** — Toggle-by-entity-type chrome (Accounts / Rails / Chains / Templates checkboxes). (Delivered by X.4.b chrome iteration: Internal/External roles + Rails + Templates + Chains + Control hierarchy + per-edge-label toggles.)
- [x] **X.4.c.3** — Click-a-node → focus connected subgraph (everything not directly connected dims). (Delivered by X.4.b focus: server-rendered re-emit via `?focus=<node_id>`; out-of-subgraph nodes are removed entirely rather than dimmed — strictly more readable.)
- [x] **X.4.c.4** — Reset filters → back to the full graph, all types visible. (Delivered by X.4.b chrome: `<a id="toggle-reset" href="?">Reset</a>` drops every URL param.)
- [ ] **X.4.c.5** — Coverage-tint overlay: `coverage_for(connection, prefix, l2_instance)` data fetcher (binary presence per L2 primitive, row-count on hover).
  - [x] **X.4.c.5.a** — `coverage_for(pool, prefix, instance) → CoverageMap` in `common/l2/coverage.py`. 3 GROUP BY queries on the top-level `account_role` / `rail_name` / `template_name` columns (no `JSON_VALUE` needed — checked the schema: those are first-class columns on `<prefix>_transactions`). `CoverageMap.by_node_id` is keyed by `_role_id` / `_rail_id` / `_template_id` ; `CoverageMap.by_chain_edge_id` is keyed `chain__<parent>__<child>` and derived (a chain edge is "covered" when both endpoints — rail or template — have at least one row). Iteration over the L2-declared set so absence is its own signal (`present=False, count=0` instead of missing-from-map). 7 unit tests against a file-backed aiosqlite pool — present/absent for roles / rails / templates, derived chain edge, and the "every declared primitive lands in the map" contract.
  - [x] **X.4.c.5.b** — Studio plumbing: extended `make_studio_routes(cache, dev_log, *, db_pool=None)` to accept an optional `AsyncConnectionPool`; `_html_serve` now defers the factory call until after the pool is built inside `_serve()` (the pool MUST live in the same event loop as uvicorn — pre-existing X.2.g.2.d invariant). The diagram page emits `<meta name="diagram-coverage-available" content="1">` when the pool is present so the JS shim (X.4.c.5.d) knows whether to mount the Coverage toggle. Three pre-existing X.4.b stale assertions in `test_studio_diagram_route.py` (mode-select / engine pills / `.topology-svg.focused`) updated to match the post-cleanup chrome.
  - [x] **X.4.c.5.c** — Server route: `GET /diagram/coverage` → JSON `{nodes: {node_id: {present, count}}, chain_edges: {edge_id: {present, count}}}`. Mounted only when `db_pool is not None`. Computed on demand. Dialect + `prefix_override` threaded through `make_studio_routes` (kwarg-only); `cli/studio.py` binds them via `functools.partial` so the factory passed to `_html_serve` keeps its 3-arg shape (cache, dev_log, pool) — `_html_serve` stays Studio-internals-ignorant.
  - [x] **X.4.c.5.d** — Chrome surface: "Coverage" toggle in `_render_diagram_page`'s chrome bar (rendered only when the pool is wired). `diagram.js`'s `_wireCoverage(svg)` fetches `/diagram/coverage` once on first toggle-on (cached for the session — Studio is one user iterating, not a hot path), stamps `data-presence` + `data-row-count` per node + chain edge, applies `.coverage-on` to the SVG root. CSS rules in `diagram.css` desaturate absent (`data-presence="no"`) entities to muted grey so the operator's eye lands on the missing-ETL gaps.
  - [x] **X.4.c.5.e** — Hover surface: `_stampCoverage` injects/updates a `<title>` on each tinted node carrying `"<id> · 12,304 rows"` (or `"<id> · no data"` for absent), shown via the browser's native SVG hover tooltip. No custom tooltip widget needed.
  - [x] **X.4.c.5.f** — Tests: unit for `coverage_for` against SQLite + a hand-built `<prefix>_transactions` (7 tests in `test_l2_coverage.py`); route + chrome integration via Starlette `TestClient` (6 tests in `test_studio_diagram_coverage_route.py` — graceful degrade without pool, JSON shape with pool, chrome toggle present with pool, loud-fail on missing dialect, trainer-route + trainer-chrome integration). Browser-driven Playwright e2e (toggle the checkbox, assert `data-presence` on rendered SVG) deferred until we have a Studio in-process driver — the unit tier covers the contract and the JS handler is renderer-shape-locked to the JSON the route returns.
- [x] **X.4.c.6** — Trainer-mode overlay: planted exceptions visually annotated on their host entities. `plants_per_node(instance, scenario=None)` in `common/l2/trainer.py` walks the in-memory `ScenarioPlant` (auto-derived via `default_scenario_for` when `scenario=None`) and counts plants per topology node — drift on role+rail, overdraft on role-only, supersession on rail-only, transfer_template on tmpl-only, etc. `RailFiringPlant` excluded (broad-mode bulk firings, not SHOULD-violations). `GET /diagram/trainer` returns `{nodes: {node_id: {plant_kind: count}}}` (always mounted — pure scenario walk, no DB needed). Chrome carries a Trainer toggle next to Coverage; JS `_wireTrainer` fetches once on toggle-on, stamps `data-trainer-kinds="drift,overdraft"` per node + appends `[plants: drift×2, overdraft×1]` to the SVG `<title>` for native hover. CSS adds an amber outline to trainer-marked nodes. 4 unit tests (`test_l2_trainer.py`) + 2 integration tests (`test_studio_diagram_coverage_route.py`) cover the helper + route + chrome surface.
- [x] **X.4.c.7** — JS unit tests in `tests/js/` shape (Playwright + static fixture, mirroring `test_bootstrap.py`'s X.2.a.2 pattern). diagram.js's bottom-of-file conditional installs `window.__diagram_internals__` (and short-circuits the auto-`renderDiagram` invocation, which would otherwise trigger the wasm-graphviz dynamic import that fails over `file://` CORS) when `window.__test_mode__` is set. Fixture (`tests/js/fixtures/diagram_test_harness.html`) hand-builds an SVG with three nodes + a chain edge; tests load it via WebKit + drive `_stampCoverage` / `_stampTrainer` via `page.evaluate`. 3 tests in `test_diagram_overlays.py` cover: coverage stamps `data-presence` + count + native title; trainer stamps `data-trainer-kinds` + appends `[plants: drift×2, …]` to the title; trainer is idempotent (toggle-on/off cycle doesn't double-append). The other diagram features (toggles, focus, pan/zoom) are exercised end-to-end by the live `/diagram` route + the route+chrome integration tests in `test_studio_diagram_coverage_route.py` — adding more JS-unit coverage for them is a follow-on once we have a regression case worth pinning.

#### X.4.d — Editor primitives: server-owned cascade

- [x] **X.4.d.1** — `mutate_l2(instance, kind, entity_id, fields) → L2Instance` in `common/l2/editor.py`. `dataclasses.replace` on the matched entity; addresses by Account.id / Rail.name / TransferTemplate.name / AccountTemplate.role / `<parent>::<child>` (chains) / `<parent_role>::<transfer_type>` (limit_schedules).
- [x] **X.4.d.2** — `rename_identifier(instance, kind, old, new) → L2Instance` walks every reference. Reference catalog mirrors the strict validator's reference-resolution pass: role rename rewrites Account / AccountTemplate / Rail (source/destination/leg) / LimitSchedule (parent_role); rail rename rewrites TransferTemplate.leg_rails + Rail.bundles_activity + ChainEntry endpoints; transfer_template rename rewrites Rail.bundles_activity + ChainEntry endpoints. Chain / limit_schedule rename = no-op (leaf consumers).
- [x] **X.4.d.3** — `serialize_l2(instance) → str` in `common/l2/serializer.py`. Round-trip-stable (model equality after load → serialize → load) for both bundled fixtures (spec_example + sasquatch_pr); per-entity dumpers symmetric with the loader's 23 `_load_X` helpers; `_dump_money` / `_dump_duration` / `_dump_role_expression` invert the loader's scalar normalizers; defaults skipped to match hand-authored fixture style. Drops freeform `# comments` (per SPEC); preserves `description:`.
- [x] **X.4.d.4** — `validate(instance)` from `common/l2/validate.py` is composed by callers post-mutate (the editor module returns a new `L2Instance`; `validate(...)` raises `L2ValidationError` on structural break). The X.4.e PUT handler will compose `mutate → validate → save`; surfacing the validator raise as 400 + inline error fragment lands when X.4.e wires the HTMX path.
- [x] **X.4.d.5** — `delete_l2_entity(instance, kind, entity_id) → L2Instance` removes the matched entity. Per the SPEC's "structural break = reject" rule, the caller composes `validate(...)` after delete; a still-referenced entity raises `L2ValidationError` for the PUT handler to surface as 400. Verified by `test_delete_rail_with_dependent_template_validator_rejects` (deleting `ReconciliationLeg` while `ExternalReconciliationCycle.leg_rails` still references it raises).
- [x] **X.4.d.6** — Tests: 4 round-trip in `test_l2_serializer.py` (load → serialize → load equals original for spec_example + sasquatch_pr; emits valid YAML; defaults omitted); 14 editor in `test_l2_editor.py` (mutate per-kind incl. composite-keyed chains, unknown-id raises, unknown-field raises; rename per-kind walks every expected reference site + preserves unrelated; delete removes + revalidates structural break; mutate→serialize→load round-trip composes cleanly).

#### X.4.e — Editor: HTMX form discipline + cascade trigger

- [x] **X.4.e.1** — Form template scaffolding (per-entity `FieldSpec` tuple in `common/html/_studio_editor_routes.py`; one shared `_render_field` / `_render_edit_form` / `_render_read_card` helper renders any kind from its `FieldSpec` list; field labels + helper text are hand-coded in the spec — the docstring-extraction discipline ($X.6.a) is queued for follow-on once the X.4.f forms settle).
- [x] **X.4.e.2** — `GET /l2_shape/<kind>/` — list view; renders every entity of the kind as a read card with click-to-edit.
- [x] **X.4.e.3** — `GET /l2_shape/<kind>/<id>` (read-only card fragment) + `GET /l2_shape/<kind>/<id>/edit` (editable form fragment).
- [x] **X.4.e.4** — `PUT /l2_shape/<kind>/<id>` — the full cascade flow: form-data → coerce → `mutate_l2` → `validate` → `cache.save` (atomic write + cache.replace) → respond with read fragment + `HX-Trigger: l2-cascade-reload`.
- [x] **X.4.e.5** — Validation-failure path: bad PUT (validator raise) returns 400 + the edit form fragment with the user's typed content preserved + the validator error rendered in a `.form-global-error` block. Field coercion failures (bad Decimal etc.) take the same path.
- [x] **X.4.e.6** — `DELETE /l2_shape/<kind>/<id>` removes the entity + composes `validate` (structural-break ⇒ 400 + inline error). `POST /l2_shape/<kind>/` create deferred to a follow-on (the editor primitives can wire it; the per-entity defaults table needs designing).
- [x] **X.4.e.7** — Read-card / edit-form fragments emit the `hx-trigger` listener wiring; cascade-reload trigger fires on every successful PUT/DELETE so the diagram + sibling cards can `hx-get` themselves. Diagram-side listener (re-fetching `/diagram` on the trigger) lands when X.4.h's data-shaping panel adds the chrome — the trigger is fired so the diagram is ready to consume.

#### X.4.f — Editor forms (additive build order — parallelizable per entity)

- [x] **X.4.f.1** — Account form (flat) — pilot per-entity `FieldSpec` tuple. Six fields: id (required), scope (select), name, role, parent_role, expected_eod_balance (money), description (textarea).
- [x] **X.4.f.2/f.3** — Rail form — single FieldSpec list covers TwoLegRail + SingleLegRail (both subtypes share most fields; the subtype-only fields read off whichever is present at edit time). First-cut fields: name (required), transfer_type (required), origin, cadence, description. Subtype-discriminating fields (source/destination_role for TwoLeg, leg_role/leg_direction for Single) deferred to a follow-on alongside the metadata_keys / posted_requirements / aging-window editors.
- [x] **X.4.f.4** — Theme form deferred — Theme is an L2-instance attribute (singleton, not a list of entities), so it needs a separate route shape (`GET/PUT /l2_shape/theme/`). Track as X.4.f.4-followon; the FieldSpec pattern carries over directly.
- [x] **X.4.f.5** — Chain form (flat — sub-list editor for required/xor-group children deferred). Five fields: parent (required), child (required), required (select bool), xor_group, description.
- [x] **X.4.f.6** — TransferTemplate form (flat — sub-list editor for leg_rails deferred). Five fields: name (required), transfer_type (required), expected_net (money), completion (required), description.
- [x] **X.4.f.role-dropdown** — `parent_role` rendered as `<select>` populated from `Account.role ∪ AccountTemplate.role` (FieldSpec `select_from="roles"` resolved at render time); empty "— none —" option for clearing; current value preserved as a stale option if the role universe drifted. Two-layer rule: `parent_role` field hidden on the edit form AND read card when this entity's own role is already used as someone else's `parent_role` (a parent can't itself have a parent under the two-layer constraint). 10 tests in `test_studio_editor_routes.py`.
- [x] **X.4.f.10** — Templates + chains: rail composition. New `multi_select` FieldKind + `<select multiple>` renderer + form-data getlist coerce path; `select_from` extended with `"rails"` (rail names) and `"rails_or_templates"` (union — for chain.parent/child). TransferTemplate's `leg_rails` is now a multi-select sub-list editor; ChainEntry's `parent` and `child` are dropdowns of the rail+template universe (was free text). Empty leg_rails handled by a new validator rule (`_check_template_has_at_least_one_leg_rail` → R4.1) that surfaces as the inline error when the operator de-selects the last rail — message instructs them to either add a replacement or delete the whole template (matches user's "let validator reject" choice). Hidden `<name>__present` marker distinguishes "field rendered with empty selection" (clear) from "field absent" (no change). 4 new tests: leg_rails multi-select renders with current selected; PUT round-trips the selection; PUT with empty leg_rails returns 400 + the new validator error inline; chain create form renders parent/child dropdowns. `_coerce_form` refactored to take FormData directly (so getlist works) and return `(typed_fields, raw_overrides)` — overrides preserve multi-select tuples for re-render on validation failure.
- [x] **X.4.f.9.create-page** — Add lands on a dedicated full-page create screen (operator preference: room for training prose explaining what each entity kind IS — chart-of-accounts framing for Account, money-movement-contract framing for Rail, multi-leg-event framing for TransferTemplate, SLA framing for ChainEntry, daily-cap framing for LimitSchedule). `_render_create_page` returns full HTML chrome + intro prose (per-kind constants in `_CREATE_INTRO_BY_KIND`) + plain HTML POST form (no htmx). POST handler returns 303 → `/` on success; 400 + re-rendered page (with operator's typed values + error inline) on validation failure. Section's "+ Add" link is plain `<a href="/l2_shape/<kind>/new">` with `event.stopPropagation()` so the click doesn't toggle the surrounding `<details>` shut. Test updates: GET asserts full-page chrome + intro prose; POST success asserts 303 to `/`; POST conflict asserts the full page re-renders with the typed values intact.
- [x] **X.4.f.9** — Add + Delete buttons per entity kind (no delete cascade — operator clears references first). Server: `create_l2_entity(instance, kind, fields)` in `common/l2/editor.py` constructs the new entity per kind (id-collision check + required-field validation raises `ValueError`); routes `GET /l2_shape/<kind>/new` (blank create form) + `POST /l2_shape/<kind>/` (coerce → construct → validate → save → return new card + cascade trigger; failure re-renders form with error inline). UI: every read card carries a red Delete link next to Edit (`hx-delete` + `hx-confirm`); each home-page section's `<summary>` carries a "+ Add" link that hx-gets the blank form into the section body (with `event.stopPropagation()` so the click doesn't toggle the surrounding `<details>` closed, and `this.closest('details').open = true` to force-open if collapsed). Existing DELETE handler reused — validator already rejects structural breaks with 400 + inline error. 5 new tests across `test_studio_editor_routes.py` + `test_studio_home_route.py`: delete link wiring; new-form returns blank form; create persists + triggers cascade; duplicate id returns 400 with form re-rendered; home page wires + Add per section.
- [x] **X.4.f.8.reverse** — Card title click → diagram focus. Each entity card's `<h3>` carries `data-focus-node="<prefix>__<name>"` (computed by `_focus_node_for_entity` per kind: account/template/limit_schedule → `role__X`, rail → `rail__X`, transfer_template → `tmpl__X`, chain → parent endpoint with the right prefix). Document-level click + keydown delegation on the home page navigates the iframe to `?focus=<node>`; the existing iframe-load listener then fans out the same /diagram/visible filter pipeline. Title gets `cursor: pointer` + hover underline + focus outline; `role="button"` + `tabindex="0"` for keyboard access (Enter / Space). 2 new tests in `test_studio_home_route.py`: data-focus-node values per kind; click + keydown listeners present on the home page.
- [x] **X.4.f.8** — Diagram-click filters the entity cards. New `GET /diagram/visible?focus=<node_id>` returns `{kind: [entity_ids...]}` of entities reachable from that focus subgraph (uses the same `_focus_set` semantics as the diagram filter — direct neighbors + complete-rail). Implemented as `visible_entities_for(instance, focus_node_id)` in `common/l2/topology.py` working off a fresh adjacency build (rail↔role per-rail, not the bundle-collapsed typed graph) so focusing on an individual rail still pulls in its endpoint roles. Home page JS listens on iframe `load`, parses `?focus=` from the iframe URL, fetches `/diagram/visible`, and toggles `.is-hidden-by-focus` on cards (with `data-kind` + `data-entity-id`). Per-section "(N shown)" indicator surfaces in the `<summary>`. Filter survives cascade-reload via re-apply on `htmx:afterSettle`. Unknown / synthetic-bundle focus IDs un-filter (graceful). 6 unit tests in `test_visible_entities.py` (no focus = all; unknown = all; focus on role/rail/template); 4 integration tests in `test_studio_home_route.py` (visible route returns full set / filtered; home page wires the listener; cards carry data-attrs).
- [x] **X.4.f.7** — Unified Studio home page (diagram on top + per-kind entity sections below, one page) so the operator doesn't navigate to a separate URL per entity kind. Save/delete in any section emits `HX-Trigger: l2-cascade-reload`; the home page wires listeners on the diagram (iframe → JS catches the cascade event on document, bumps `iframe.src = iframe.src` for a same-origin reload — HTMX doesn't forward triggers across iframe boundaries) and on each entity section's inner `<div>` (`hx-trigger="load, l2-cascade-reload from:body"`) so they `hx-get` themselves to refresh on cascade. Keeps the existing per-kind routes intact (each `<details>` summary carries an `↗` deep-link to `/l2_shape/<kind>/`); home page composes them via `?embed=1` fragment mode added to `_render_list_page`. Entity sections use `<details>` (collapsed except first) so the page isn't an unbroken wall on a 7-rail / 30-account L2. 7 integration tests in `tests/unit/test_studio_home_route.py`: home renders iframe + 6 sections; first section open default; sections wire lazy-load + cascade trigger; iframe cascade-reload listener present; embed returns cards-only fragment; non-embed keeps full page; PUT emits HX-Trigger header.

X.4.e + X.4.f tested by 17 integration tests across `test_studio_editor_routes.py` (10) + `test_studio_home_route.py` (7): list view renders all accounts; read card returns fragment; edit form returns form fragment; unknown kind 404s; PUT account persists to disk + triggers cascade; PUT invalid value returns 400 with error inline + disk untouched; DELETE dependent rail returns 400; DELETE unreferenced account persists; parent_role renders as role dropdown for child accounts; parent_role hidden when the account is already a parent; home page renders diagram iframe + 6 entity sections; first section open by default; sections wire lazy-load + cascade trigger; iframe cascade-reload listener present; embed mode returns cards-only fragment; non-embed keeps full page; PUT emits cascade trigger header. All-in: 6 entity kinds (account / account_template / rail / transfer_template / chain / limit_schedule) editable through the same routes; the unified `/` home page composes them with the diagram on top.

#### X.4.f.11 — Rail editor field gaps (subtype-discriminating + load-bearing)

The X.4.f.2/.3 first-cut Rail editor covers cosmetic fields (`name` / `transfer_type` / `origin` / `cadence` / `description`) but not the subtype-discriminating fields (TwoLegRail's `source_role` / `destination_role`; SingleLegRail's `leg_role` / `leg_direction`) nor the load-bearing per-leg / aging / `bundles_activity` / `metadata_keys` fields. Without these, an operator onboarding a new rail in the studio editor can configure the cosmetic fields but **cannot wire the actual money-movement endpoints** — they still have to drop to yaml. ETL/Training blocker.

**Approach:** add `subtype_only: Literal["two_leg", "single_leg"] | None` flag to `FieldSpec`; renderer filters fields based on the rail's actual subtype at edit time. Create form gets a subtype radio first that dispatches to the right constructor. Three tiers, shipping load-bearing first.

**Tier 1 — load-bearing wiring (top priority):** *(all shipped in commit 9e2447f)*
- [x] **X.4.f.11.1** — `FieldSpec.subtype_only` flag + renderer filter (skip fields whose `subtype_only` doesn't match the entity's actual subtype). Current rail's subtype derived via `isinstance(rail, TwoLegRail)`.
- [x] **X.4.f.11.2** — TwoLegRail: `source_role` + `destination_role` (`multi_select` from `roles`; `RoleExpression` is `tuple[Identifier, ...]`, single role becomes 1-tuple).
- [x] **X.4.f.11.3** — SingleLegRail: `leg_role` (`multi_select` from `roles`) + `leg_direction` (`select` Debit / Credit / Variable).
- [x] **X.4.f.11.4** — Both subtypes: `aggregating` (`select` true / false). `cadence` already in editor; this is the gate flag.
- [x] **X.4.f.11.5** — Create form subtype picker for new rails: radio Two-leg / Single-leg → constructor dispatch in `create_l2_entity`.

**Tier 2 — frequently-touched:** *(all shipped in commit 9e2447f)*
- [x] **X.4.f.11.6** — `metadata_keys` + `posted_requirements` (`textarea`, one-per-line, comma-tolerant; coerced to `tuple[Identifier, ...]`).
- [x] **X.4.f.11.7** — `max_pending_age` + `max_unbundled_age` (`text` with ISO 8601 format hint `PT24H` / `P1D`; parsed via existing loader's Duration helper; empty = `None`).
- [x] **X.4.f.11.8** — TwoLegRail: `source_origin` + `destination_origin` (`text` — Origin is open enum) + `expected_net` (`money`).
- [x] **X.4.f.11.9** — `bundles_activity` (`multi_select` from `rails_or_templates` for v1; gains `transfer_types` membership after Z.B lands).

**Tier 3 — `metadata_value_examples` as a YAML-block textarea (locked 2026-05-13):** *(all shipped in commit 9e2447f)*

The field is `tuple[tuple[Identifier, tuple[str, ...]], ...]` — a list of (metadata-key → list-of-example-values) pairs. A custom nested-row editor (per-key text input + values textarea + add/remove buttons + JSON wire shape) is materially more work than the rest of the rail editor combined. Operator preference: **simple YAML block is fine** — the L2 yaml shape is what the operator already knows, so the editor just exposes it.

- [x] **X.4.f.11.6.5** — `yaml_block` FieldKind: renders a `<textarea>` (mono font, ~10 rows). Coerce parses with `yaml.safe_load`, validates the result is `dict[str, list[str]]` (or empty), then wraps to `tuple[(Identifier(k), tuple(map(str, vals))), ...]`. Bad YAML → `ValueError("Invalid YAML: ...")` → form re-render with operator's typed content + inline error. Display side: `_value_to_input_str` for the metadata_value_examples field uses `yaml.safe_dump({k: list(vals) for k, vals in tuple_of_tuples})` so round-trip is exact.
- [x] **X.4.f.11.6.6** — Wire on the Rail FieldSpec (both subtypes; no `subtype_only`). Helper text: "YAML map — keys are metadata field names, values are example strings the demo seed cycles through. Empty ⇒ uses synthetic per-rail fallback." Example block in placeholder.
- [x] **X.4.f.11.6.7** — Create-form constructor: thread `metadata_value_examples` from `fields.get("metadata_value_examples") or ()` into both TwoLeg + SingleLeg constructors (mirrors the other Tier-2 fields landed in X.4.f.11.6).
- [x] **X.4.f.11.6.8** — Test: edit form renders existing example map as YAML; PUT round-trips a 2-key/3-values-each example without dropping/reordering; bad-YAML PUT returns 400 + inline error + typed content preserved.

**Verify:** *(all shipped in commit 9e2447f — 42 test references for new fields confirm coverage)*
- [x] **X.4.f.11.10** — Tests in `test_studio_editor_routes.py`: subtype filter (TwoLeg form hides leg_role; SingleLeg form hides source_role); create form subtype picker; PUT round-trip per new field.
- [x] **X.4.f.11.11** — Live verify against `sasquatch_pr.yaml`: load → edit a rail end-to-end via the studio editor → save → assert yaml diff matches the form changes.
- [x] **X.4.f.11.12** — pyright + commit + tick PLAN.

#### X.4.f.12 — Theme + Persona singleton editors

Theme + Persona are L2-instance attributes (singletons, not lists). They need a different route shape: `GET/PUT /l2_shape/<kind>/` (no `<id>` path param). The home page surfaces them as one card each (current values + Edit button).

**Approach:** parameterize the existing route handlers on `is_singleton: bool`; reuse the FieldSpec / coerce / render machinery. Add a `color` FieldKind for `<input type="color">` hex inputs. Defer the `GLAccount` sub-form editor — v1 uses a textarea of `code|name|note` rows.

*(All shipped in commit 9e2447f — implementation pivoted from per-field FieldSpecs + `color` FieldKind to a single `yaml_block` per singleton: simpler + matches the operator's mental model of editing the L2 yaml shape they already know. `color` FieldKind dropped from scope; `singleton_save_l2` + `SINGLETON_KINDS` + `/l2_shape/{theme,persona}/` routes ship.)*
- [x] **X.4.f.12.1** — Singleton route handlers (`GET /l2_shape/<kind>/` form / `PUT /l2_shape/<kind>/` save) — pattern mirrors per-id but no path param. Add `singleton_save_l2(kind, fields)` to `editor.py` that does `dataclasses.replace(instance, theme=…)` / `…persona=…)`.
- [x] **X.4.f.12.2** — ~~`color` FieldKind~~ → dropped from scope. Singleton form uses a single `yaml_block` per the operator-preference call (the operator already knows the L2 yaml shape; v1 surfaces it directly rather than building 25+ per-field widgets). A dedicated `color` FieldKind can reland later if singleton editing turns out to be a frequent enough touchpoint to justify it.
- [x] **X.4.f.12.3** — Theme yaml_block route shipped (single textarea + `_load_theme` round-trip). FieldSpec list deferred per .12.2.
- [x] **X.4.f.12.4** — Persona yaml_block route shipped (single textarea + `_load_persona` round-trip). FieldSpec list deferred per .12.2.
- [x] **X.4.f.12.5** — Home page: 2 new sections (Theme / Persona) above the diagram or below the existing 6 sections (decision: below, since they're cosmetic / less-frequently-edited). Each shows the current value preview + Edit link landing on `/l2_shape/theme/` or `/l2_shape/persona/`.
- [x] **X.4.f.12.6** — Tests + verify + commit.

#### X.4.g — The "Deploy changes" pipeline

- [x] **X.4.g.1** — `etl_hook` config field + V.1.b allowlist entry.
- [x] **X.4.g.2** — `etl_datasource` config block (URL + `transactions` / `daily_balances` table allowlist) + V.1.b allowlist entry.
- [x] **X.4.g.3** — `test_generator:` config block (`enabled` / `scope` / `end_date` / `seed` / `plants` / `only_template` / `derive_balances`) + V.1.b allowlist entry. Defaults preserve byte-identical-to-locked-seeds output.
- [x] **X.4.g.4** — Step 1 (`etl_hook` gate): subprocess run; stream stdout/stderr to `/dev_log`; exit-code halts BEFORE step 2 if non-zero (demo DB never touched).
- [x] **X.4.g.5** — Step 2 wipe: call `wipe_demo_data_sql(instance, dialect)` against `demo_database_url`, always (when we reach this step).
- [x] **X.4.g.6** — Step 2 pull: cross-dialect copy from `etl_datasource` to `demo_database_url`, filtered to `≤ end_date`. Reuse `common/sql/dialect.py` + Oracle batcher.
- [x] **X.4.g.7** — Step 3 generator: `emit_full_seed` against the current `test_generator:` knobs; always additive (the wipe was step 2).
- [x] **X.4.g.8** — Step 3 — `scope: full` mode (today's behavior; byte-matches locked seeds when no `etl_datasource`).
- [x] **X.4.g.9** — Step 3 — `scope: exceptions_only` mode (skip baseline, plants only on top of whatever step 2 produced).
- [x] **X.4.g.10** — Step 3 — `scope: uncovered_rails` mode (inspect `demo_database_url` post-step-2, fill baseline only for rails without rows).
- [x] **X.4.g.11** — Step 4 matview refresh: existing `refresh_matviews_sql(instance)`.
- [x] **X.4.g.12** — Step 5 reload: bump a process-local `data_generation_id`; Dashboards' open page polls (or subscribes to) the counter and reloads its current URL on bump.
  - [x] **X.4.g.12.a** — Server-side: process-local `_data_generation_id` int + `step_5_reload` bumps + emits `deploy:step5:reload:bump` event; `GET /data_generation_id` route returns `{"data_generation_id": N}` always-mounted on `make_app` (commit 38ac9bd).
  - [x] **X.4.g.12.b** — Dashboards client-side poller: page-shell JS reads its own data_generation_id from a `<meta name="data-generation-id">` tag on first load, polls `GET /data_generation_id` every ~3s, and `location.reload()`s when the server-reported value differs from the captured one. Visible-tab-only (Page Visibility API) so backgrounded tabs don't burn CPU. Off when `dev_log=False` per debate? — KEEP ON in production: the whole point is auto-pickup of deploys; an integrator wouldn't know to manually refresh.
  - [x] **X.4.g.12.c** — Tests: server emits the meta on dashboard pages; JS poller fires on a stub server bumping its counter; reload triggers when counter advances; doesn't reload when stationary. (Unit + Playwright.)
- [x] **X.4.g.13** — `POST /deploy` orchestration endpoint that runs steps 1-5; returns a structured progress stream.
- [x] **X.4.g.14** — Studio "Deploy changes" button (global, in the Studio header); calls `POST /deploy`; surfaces step progress + errors via `/dev_log`.
- [x] **X.4.g.15** — Pipeline orchestration tests (one per shape: hook-fail-halt, no-etl path, etl-only path, etl-then-generator path, etl-then-`uncovered_rails` path).

#### X.4.h — Data-shaping panel (Studio's "trainer mode")

**Layout decision (locked 2026-05-14).** A *new top-level Studio mode* on `/data` — not a side rail on the home page. Cards aren't part of this view. Centerpiece is the vertical plant-timeline; right pane is space for "training on the errors" (per-exception explanatory content); knobs at the top:

```
┌────────────────────────────────────────────────────┐
│ Studio · data shaping        [← landing] [Deploy]  │  chrome
├────────────────────────────────────────────────────┤
│ scope: [full ▼]  end_date: [< Jan 12 >] [today]    │
│ seed: [_____] [🎲]                                 │
│ plants: ☑drift ☑overdraft ☑limit_breach ...       │
├─────────────────────┬──────────────────────────────┤
│ TIMELINE            │ TRAINING                     │
│ Jan 01              │ (Hover/select a day's        │
│ Jan 02 · drift×1    │  annotation → explain        │
│ Jan 03              │  what kind of exception      │
│ Jan 04 · overdraft  │  it is, what the trainee     │
│ ...                 │  should look for, link       │
│ Jan 12 · drift×2    │  to the relevant dashboard   │
│ Jan 90              │  sheet. Initial source of    │
│                     │  text: docs/walkthroughs/l1) │
└─────────────────────┴──────────────────────────────┘
```

**Sequencing:** chunk h.0 (backend wiring) lands first — without it, the UI knobs are no-ops. Then chunk h.1+ (the panel itself). Then X.4.i layers in once h ships and the trainer flow is validated. Per-chunk live verification through `scripts/studio-with-pg-source.sh`.

##### h.0 — Backend wiring (no UI yet)

Today: `cfg.test_generator.plants` and `.seed` are read into the orchestration log but the generator IGNORES them — `_build_generator_sql` calls `build_default_scenario(instance, anchor=…)` / `build_full_seed_sql(cfg, instance, anchor=…)` with no plants / no seed forwarded. h.0 closes that gap so the UI in h.1+ has something to drive.

- [x] **X.4.h.0.a** — Thread `tg.plants` filter into `build_default_scenario` + `build_full_seed_sql`. Empty/None tuple = today's behavior (all plants — the locked-seed default). Non-empty = subset filter. Helper: `filter_scenario_plants(base, kinds)` in `common/l2/auto_scenario.py` — pure projection over the 6 `PlantKind` tuples; non-PlantKind fixtures (`failed_transaction_plants`, `transfer_template_plants`, `rail_firing_plants`, `inv_fanout_plants`) pass through. Wired through `_build_generator_sql` (deploy_pipeline.py) for both `scope=full` and `scope=exceptions_only`.
- [x] **X.4.h.0.b** — Thread `tg.seed` into the scenario randomizer. `None` = today's `_BASELINE_BASE_SEED = 42` (locked-seed default — `effective_base_seed` short-circuit in `emit_baseline_seed`). Int = override the root RNG before per-rail XOR + before chain / cascade-credit sub-streams. Wired via `seed.py::emit_baseline_seed(base_seed=…)` → `emit_full_seed(base_seed=…)` → `cli/_helpers.py::build_full_seed_sql(base_seed=…)` → `deploy_pipeline._build_generator_sql` reads `cfg.test_generator.seed` for both `scope=full` and `scope=uncovered_rails` (`scope=exceptions_only` plants only — baseline-side knob is a no-op so left unwired). Plants stay deterministic (per-kind fixed seeds inside the scenario builder); only the 90-day baseline reseeds. Tests: 6 in `TestBaseSeedKnob` (`tests/data/test_l2_baseline_seed.py`) — None matches `_BASELINE_BASE_SEED`, different ints diverge, same int reproduces, propagation through `emit_full_seed`, per-rail isolation rule preserved.
- [x] **X.4.h.0.c** — Locked-seed determinism still holds with defaults: `tests/data/test_locked_seeds.py::test_locked_seed_matches_fresh_emit` 8 tests pass with `plants=None` short-circuit (identity return; no copy → byte-identical SQL).
- [x] **X.4.h.0.d** — Unit tests: per-plant filter (5 tests in `tests/unit/test_l2_auto_scenario.py` — None/empty short-circuits, single-kind keeps drift only, two-kind keeps both, non-L1 fixtures pass through). Seed-propagation tests deferred to h.0.b alongside the seed wiring.

##### h.1 — `/data` route + chrome shell (no knobs wired yet)

- [x] **X.4.h.1.a** — `make_studio_routes` adds `GET /data` + an empty page-shell. Renderer `_render_data_page(cache, dev_log)` in `_studio_routes.py` returns a chrome bar (← landing / → diagram / → dashboards / Deploy button + status), a knob-strip placeholder `<div class="data-knobs" id="data-knobs">` (h.2-h.5 will fill this), and a two-column `<main class="data-main">` with `<section id="data-timeline" aria-label="Plant timeline">` (h.6) and `<section id="data-training" aria-label="Training pane">` (h.9). New `_studio_assets/data.css` carries the grid layout; loaded alongside `diagram.css` so the page inherits the L2 theme tokens.
- [x] **X.4.h.1.b** — Studio chrome on `/` and `/diagram` adds a `→ data` nav link so the new mode is reachable. Both chromes now carry the link in the same `nav-link` class as `→ diagram (full)` / `→ dashboards`. Diagram in `?embed=1` mode strips the studio-header (existing X.4.f.8 behavior), so the link doesn't double up when the diagram is iframed inside the home page.
- [x] **X.4.h.1.c** — Tests: 6 cases in `tests/unit/test_studio_data_route.py` — route returns 200 + the three landmark elements present (knob strip + timeline + training); aria labels present (Playwright role-based selectors); deploy button + JS helper render; back-to-landing nav link present; home + diagram chromes carry the `→ data` link; embed-mode diagram omits it. Also added `_studio_assets/*.{css,js}` glob to `pyproject.toml::tool.setuptools.package-data` (the prior chrome assets were live-mounted via `Path(__file__).parent` and would have been missing from a real wheel install — closes a latent gap).

##### h.2-h.5 — Knob widgets

- [x] **X.4.h.2** — Plant-toggle checkboxes (one per exception kind: drift / overdraft / limit_breach / stuck_pending / stuck_unbundled / supersession). Empty selection = "all" (matches SPEC short-circuit). Mutations land via the new `TestGeneratorCache` (`common/l2/tg_cache.py`) — Studio-owned in-memory authority for `cfg.test_generator`; `update(...)` partial-set with `_UNSET` sentinel for None-valued fields; `patched_config(cfg)` clones a fresh Config for the deploy route. HTMX wiring: each checkbox's `change` event triggers `hx-put="/data/knobs/plants"` with `outerHTML` swap; the form re-paints from the server response so on-screen state always reflects what the cache holds. The `/deploy` route patches the cfg with `tg_cache.patched_config(cfg)` so the next pipeline run reads the latest knob values without mutating the startup cfg. Cache instantiated in `cli/studio.py` from `cfg.test_generator` and partial-bound into the studio_routes_factory. Tests: 9 in `tests/unit/test_tg_cache.py` (snapshot, partial update with sentinel, None-valued field handling, patched_config clone) + 8 in `test_studio_data_route.py::TestPlantsKnob` (default-all-checked, subset reflection, form contract, PUT round-trip + cache mutation, empty payload clears, junk-input drops, route-absent-without-cache, canonical-order preservation).
- [x] **X.4.h.3** — Day stepper UI for `end_date`: ← / → step ±1 day; native `<input type="date">` commits an absolute pick on change; "today" resets to None (the locked-default sentinel — generator falls back to its own anchor). Trailing chip surfaces the current ISO so the operator sees the cache state without depending on the date-input's UA styling. Renderer `_render_end_date_strip(selected: date | None)` in `_studio_routes.py`; PUT route `/data/knobs/end_date` reads `delta=<int>` (anchors on `date.today()` when cache holds None — trainer-mode UI is not a determinism path, so a `# typing-smell: ignore[no-datetime-now]` is honest here) OR `end_date=<YYYY-MM-DD>` (empty = clear to None). Delta wins when both present (defensive — UI never sends both). Invalid ISO silently drops (same posture as `put_plants`). Mounted only when `tg_cache` is wired (severability). Tests: 10 in `test_studio_data_route.py` — blank-input-when-none, value reflection, form contract (4 PUT URLs + delta hx-vals + reset hx-vals + change trigger), absolute-set, empty-clears-to-none, delta steps from cached anchor, delta anchors on today when cache None, invalid ISO silent drop, route-absent-without-cache, delta-wins-over-end_date.
- [x] **X.4.h.4** — Random-seed input + roll/clear chips. Number input takes a uint32 (`min=0 max=4294967295`) and commits on change; "roll" PUTs `roll=1` and the server picks a fresh `random.randint(0, 2**32 - 1)` (matches the `QS_GEN_FUZZ_SEED` runner contract — uint32 range); "clear" sends `seed=` (empty) to reset to None (= `_BASELINE_BASE_SEED = 42` locked default at the generator). Renderer `_render_seed_strip(selected: int | None)` in `_studio_routes.py`; PUT route `/data/knobs/seed` precedence: roll > seed (defensive — UI never sends both); invalid int silently drops; `seed=0` is a valid value (truthy-check guard test). Mounted only when `tg_cache` is wired. Tests: 9 in `test_studio_data_route.py` — blank-when-none, value reflection, form contract (3 PUT URLs + roll hx-vals + clear hx-vals + uint32 min/max + change trigger), absolute int set, empty clears to None, roll picks random uint32 in range, roll wins over seed when both sent, invalid int silent drop, seed=0 commits 0, route absent without cache.
- [ ] **X.4.h.5** — `scope` selector (full / uncovered_rails / exceptions_only).

##### h.6 — Plant-timeline view

- [ ] **X.4.h.6.a** — Derivation primitive `compute_plant_timeline(instance, tg) -> list[(date, list[PlantHit])]` in `common/l2/auto_scenario.py` (or a sibling). Given the L2 instance + the current `TestGeneratorConfig`, walks the scenario object and returns one entry per day in the window with the plants that hit. The scenario object already encodes "this plant hits day N" (per SPEC), so this is a *projection*, not new generator logic.
- [ ] **X.4.h.6.b** — Render the timeline as a vertical column, one row per day, annotated with planted exceptions (kind + count). Click a day → `end_date` jumps + auto-Deploy.
- [ ] **X.4.h.6.c** — Re-renders via HTMX `hx-get` when any knob changes (debounced) — no full-page reload.

##### h.7 — Persistence

- [ ] **X.4.h.7** — Knob changes persist back to `config.yaml`'s `test_generator:` block (debounced ~500ms after last change); reloaded on next startup. Atomic write through the same primitive `L2InstanceCache.save_l2` uses (X.4.a.6).

##### h.8 — Tests

- [ ] **X.4.h.8.a** — Unit tests for plant-timeline derivation (given a scenario + scope + seed, which days hit?).
- [ ] **X.4.h.8.b** — Integration: knob change → cfg.yaml updated → next page-load reflects the saved state.
- [ ] **X.4.h.8.c** — Browser e2e: open `/data`, toggle a plant off, click a timeline day, assert the deploy fires + status flips ok. (Live-drive equivalent: `scripts/studio-with-pg-source.sh` → `/data` → click around.)

##### h.9 — Training pane content (per-exception explanation)

- [ ] **X.4.h.9.a** — Source the per-exception text from `src/quicksight_gen/docs/walkthroughs/l1/{drift,overdraft,limit-breach,…}.md` (already written for the L1 dashboard handbook). Lift the first prose section per file as the "what to look for" pane.
- [ ] **X.4.h.9.b** — Hover/select a timeline annotation → right pane swaps to that exception kind's explanation. Link out to the corresponding dashboard sheet (`/dashboards/l1_dashboard/sheets/l1-sheet-drift` etc.).

#### X.4.i — Additive knobs (ships AFTER X.4.h — separate phase)

Queued AFTER X.4.h ships and the trainer flow is operator-validated. These are pipeline-side fixtures that compose into h.1's existing UI surface (h.2-h.5 widgets get extended with two more controls in i.3); they don't gate the MVP trainer demo per SPEC.

- [ ] **X.4.i.1** — `only_template` mode: scoped baseline = template + dependency closure (its leg-rails + the accounts those touch); rest of the L2 left empty.
- [ ] **X.4.i.2** — `derive_balances` mode: from subledger transactions in `demo_database_url`, derive control-account daily balances satisfying double-entry (the drift invariant run forward).
- [ ] **X.4.i.3** — UI controls for both in the data-shaping panel (extend the h.1 chrome-strip).

#### X.4.j — Testing scope (per SPEC's "Testing scope" section)

- [x] **X.4.j.1** — Cross-dialect pull tests: PG → SQLite (primary), Oracle → SQLite, SQLite → SQLite (degenerate). NOT a full matrix — the codebase keeps the dialect support; we just don't fan it out for Studio's tests.
  - Shape: postgres-in-docker is `etl_datasource`, sqlite tempfile is `demo_database_url`, an `etl_hook` script re-runs `quicksight-gen data apply --execute` against the postgres before each pipeline trigger. Tests the full X.4.g.13 contract: hook gates, cross-dialect pull, generator overlay, matview refresh, reload bump.
  - [x] **X.4.j.1.a** — Postgres container fixture: testcontainers `PostgresContainer("postgres:17-alpine")` (mirroring runner's `setup_variant("pg", "lo")`). Function-scoped so concurrent runs don't collide. Strips `+psycopg2` URL suffix per `_normalize_pg_url`.
  - [x] **X.4.j.1.b** — etl_hook fixture: writes `#!/bin/bash; exec quicksight-gen data apply --execute -c <generated-pg-cfg> --l2 sasquatch_pr.yaml` to a tempfile + `chmod +x`. Pre-applies the postgres schema. Sasquatch_pr is the L2; cfg generated per-test pointing at the container.
  - [x] **X.4.j.1.c** — Studio process spawn: in-process via `_build_studio_app()` (mirrors `cli/_html_serve.py::_serve`) + httpx `AsyncClient(transport=ASGITransport(app))` for HTTP drive. NOT TestClient (sync TestClient + asyncio.run() conflict — TestClient creates its own loop, so the make_connection_pool / AsyncClient share one loop instead).
  - [x] **X.4.j.1.d** — POST /deploy assertion: parses the DeploySummary JSON, asserts `halted=False`, `step1_etl_hook_exit_code=0`, `step2_pull_transactions_pulled > 0`, `step3_generator_transactions_after >= step2_pull_transactions_pulled`, `step4_matviews_done=True`, `step5_data_generation_id > 0`.
  - [x] **X.4.j.1.e** — Post-deploy SQLite verification: opens the sqlite tempfile directly via psycopg-style cursor; asserts `<prefix>_transactions` + `<prefix>_daily_balances` are non-empty AND `<prefix>_drift` matview present (proves step 4 ran).
  - [x] **X.4.j.1.f** — Dashboards round-trip: hits `GET /dashboards/<app>` for each of L1 / L2FT / Inv / Exec; asserts page renders + carries the data-generation-id meta. Per-visual data-endpoint asserts deferred to X.4.j.2 (browser e2e job).
  - [x] **X.4.j.1.g** — Halt-on-failed-hook integration: hook script `exit 1`s; asserts `halted=True`, halt_reason includes `etl_hook returned exit_code=1`, sqlite tables stay empty (no wipe).
  - [x] **X.4.j.1.h** — Layer + gating: lives in `tests/e2e/test_deploy_pipeline_pg_to_sqlite.py`, gated by `QS_GEN_E2E=1` (conftest) + `pytestmark` skipif on `docker ps` probe + sasquatch_pr.yaml presence + venv quicksight-gen install. ~70s halt, ~120s full loop.
  - [x] **X.4.j.1.bug** — Real bug discovered + fixed in `_pull_table`: psycopg returns `decimal.Decimal` for NUMERIC columns; sqlite3 doesn't bind Decimal natively → `ProgrammingError("type 'decimal.Decimal' is not supported")`. Added `_row_coercer_for(dest_dialect)` that converts Decimal → str on the way into sqlite. PG / Oracle destinations identity (their drivers accept Decimal natively). Caught by the e2e — a unit-test sqlite-to-sqlite path didn't expose it.
- [x] **X.4.j.2** — Browser e2e: Studio + Dashboards integrated loop on `sasquatch_pr` + SQLite. Two tests in `test_studio_deploy_browser.py`.
  - [x] **X.4.j.2.a** — Server fixture: `studio_server(cfg)` context manager spins uvicorn-in-thread (real bound port for Playwright). Inner `asyncio.run(_serve())` opens the pool + serves on the same loop, mirrors `cli/_html_serve.py::_serve` shape.
  - [x] **X.4.j.2.b** — Click Deploy, wait for `#deploy-status.deploy-status--ok` (120s timeout for the etl_hook subprocess to land), regex-match `Deployed \(gen \d+, \d+ tx\)`.
  - [x] **X.4.j.2.c** — Each of the 4 dashboards (L1 / L2FT / Inv / Exec) renders. Asserts: HTTP 200 + data-generation-id meta + non-empty title. (All apps land on a Getting-Started sheet that's text-only — no filter form / visuals in the initial markup; the universal signals are status / meta / title.)
  - [x] **X.4.j.2.d** — Auto-reload via the data_generation_id poller. Open dashboard, capture baseline meta, fire POST /deploy from urllib (separate context), wait for the page's meta to bump above baseline within 8s. Detects the actual reload via `framenavigated` events Playwright observes. Uses a no-op etl_hook (`exit 0`) + no etl_datasource so the deploy is fast (~10s, just generator + matview + reload bump) — full cross-dialect path is .b/c's job.
  - [x] **X.4.j.2.shared** — Helpers extracted to `tests/e2e/_studio_deploy_helpers.py` (apply_schema_to / write_pg_etl_cfg / write_etl_hook_script / make_studio_cfg / build_studio_app / studio_server / row_count). Both j.1 and j.2 import from here; fixtures stay per-test-file.
- [x] **X.4.j.3** — Verify the existing X.2 13-cell `scenario × dialect × target` matrix + 4-way agreement test still pass unchanged. Local spot check: 1591 unit prelude tests pass, `./run_tests.sh up_to=db --variants=sp_pg_lo` runs 47 tests in 13.82s (rc=0). The full 13-cell matrix + browser layer (incl. the agreement test) runs in CI on push:main + the nightly cron — they're the authoritative cross-cell coverage.
- [x] **X.4.j.4** — Operator live-drive runbook + helper script: `scripts/studio-with-pg-source.sh` spins postgres-in-docker (random port + EXIT-trap teardown), writes a per-run `studio_cfg.yaml` (sqlite tempfile destination + etl_datasource=postgres + etl_hook=`quicksight-gen data apply --execute`), applies schema to both DBs, and execs `quicksight-gen studio` in the foreground. `docs/audits/x_4_j_studio_deploy_spike.md` captures the operator-iteration walkthrough (cluster up → seed → click Deploy → tabs auto-reload). Folds into a `customization/how-do-i-deploy-changes.md` walkthrough at X.4.k release time.
- [x] **X.4.j.5** — Verify the locked-seed determinism test stays green (default `test_generator:` knobs = today's output, byte-for-byte). `tests/data/test_locked_seeds.py` — 8 tests in 0.27s, all pass; emit-pipeline byte-equality holds across the X.4.g pipeline additions (no shift in baseline / plant generation).

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

## Phase Z — L2 grammar cleanup: chain collapse + transfer_type promotion

Two distinct grammar reshapes that share the migration cost (fixture rewrites + seed re-lock + editor UI + docs sweep), bundled here so the operator yaml grammar settles in one motion before the next public release.

**Sequencing — Z is a P.10 prerequisite, NOT an X.4 prerequisite (locked 2026-05-13).** X.4 (studio editor) and ETL/Training work proceed first on the current grammar; Phase Z lands immediately before P.10 cuts. Bounded rework when Z lands: ~half-day on the studio editor's chain card UI (replace per-row `required` / `xor_group` form fields with a children-checkbox-group), ~hour on the rail card's transfer_type field (free text → dropdown sourced from declared TransferTypes), ~hour on `topology.py::_chain_label` + chain-edge metadata. Everything else in X.4 is grammar-agnostic. The P.10 gate is hard because once a tagged release ships the old `chains:` shape, migrating customer yamls becomes a versioning headache.

### Z.A — Chain grammar collapse (singleton = required, multi = XOR)

Surfaced 2026-05-13 during the `X.4.f.10.followup` C4.1 validator add (the "required + xor_group on the same row is a contradiction" rule). The user noticed a deeper redundancy: today's `ChainEntry(parent, child, required, xor_group)` encodes firing semantics across **two flags** that always contradict in one combination (C4.1 just rejected it) and overlap in another (a `required=true` child is structurally a singleton xor_group). The cleaner shape is **`Chain(parent, children: tuple[Identifier, ...])`** — list cardinality carries the entire firing semantic: **singleton ⇒ required, multi ⇒ XOR**. The xor_group **name** was never load-bearing — it only grouped rows by string match; the list IS the group.

**Why now (vs deferring):** the X.4.f studio editor just shipped with row-per-link chain UI (X.4.f.10), and P.10 is the next public release. Once a published version carries the `chains:` row-per-link grammar, migrating the yaml shape becomes a versioning headache (operator yaml files in the wild). Cleaning the grammar before P.10 ships avoids that.

**Why this isn't TransferTemplate's problem:** TransferTemplate's `leg_rails` is composition (all legs fire as one Transfer). Chain's `children` is a firing rule (singleton fires or one-of-N fires). Same list-of-identifiers shape, opposite semantics. They coexist; a Chain with `children=[some_template]` still atomically fires the named template.

**What disappears (the payoff):**
- C2 (`xor_group members share parent`) — impossible to violate; every Chain row IS one parent.
- C4 (`xor_group ≥ 2 members`) — gone; singleton means "required", not "degenerate XOR".
- C4.1 (`required + xor_group contradict`) — unrepresentable in the new shape.
- The `xor_group: <name>` field on ChainEntry + the dedupe-by-name code in 5+ call sites.
- Studio editor's `_html_id_slug` `parent::child` composite + the per-row required/xor_group form fields.

**What needs adding:**
- New uniqueness rule: "no child appears in two Chain rows for the same parent" (catches the previously-unrepresentable "required AND in xor_group" overlap).

- [ ] **Z.1 — Per-child descriptions: dropped (locked 2026-05-13).** sasquatch_pr's 6 chain rows each carry a per-child description ("ACH payout option in the PayoutVehicle XOR group" vs "Wire payout option ..." vs "Paper-check payout option ..."), but each child IS a Rail or TransferTemplate that carries its own `description` field. The per-child copy in chain rows was re-narrating the rail — copy-pasta, not load-bearing. New shape carries **one `Chain.description` per firing rule** that describes the rule itself ("PayoutVehicle: exactly one settlement vehicle fires per merchant payout cycle"), and per-child copy moves into (or stays in) the rail's own `description`. No sub-primitive needed.
- [ ] **Z.2 — `primitives.py`: replace `ChainEntry` with `Chain`.** Drop `child` / `required` / `xor_group`; add `children: tuple[Identifier, ...]`. Keep `parent`, `description`. The `chains: tuple[ChainEntry, ...]` field on `L2Instance` becomes `chains: tuple[Chain, ...]`.
- [ ] **Z.3 — `loader.py`: rewrite `_load_chain_entry` → `_load_chain`.** New yaml grammar:
  ```yaml
  chains:
    - parent: ACHOriginationDailySweep
      children: [ConcentrationToFRBSweep]   # singleton = required
      description: |
        Required: every daily ACH origination sweep should be followed by …
    - parent: MerchantSettlementCycle
      children: [MerchantPayoutACH, MerchantPayoutWire, MerchantPayoutCheck]  # multi = XOR
      description: |
        PayoutVehicle: exactly one settlement vehicle fires per merchant payout cycle.
  ```
  Pyright-strict on the loader path; reject legacy keys (`required` / `xor_group` / `child`) with an actionable error pointing at this section.
- [ ] **Z.4 — `validate.py`: drop C2 / C4 / C4.1; rewrite C5; add new uniqueness rule.** C5 becomes "every chain parent has ≥1 chain row whose children list is non-empty" (the only remaining failure mode). New rule (call it C6): "for any given parent, no child appears in two Chain rows."
- [ ] **Z.5 — `serializer.py`: rewrite `_dump_chain_entry` → `_dump_chain`.** Round-trip stable for the new grammar.
- [ ] **Z.6 — `editor.py`: rewrite `mutate` / `delete` / `rename` / `create_l2_entity` for Chain.** The composite-key addressing (`parent::child`) goes away — Chain rows now address by `parent::<position>` or `parent::<children-csv>` (pick the cleaner one). `rename_identifier` walks `Chain.parent` + each `Chain.children[i]` (or `Chain.children[i].name` for Option A).
- [ ] **Z.7 — `seed.py`: chain firings (R.2.d) read the new shape.** `len(children) == 1` ⇒ that child fires; `len(children) > 1` ⇒ deterministic-pick from the list (use the same RNG as today's xor_group resolution).
- [ ] **Z.8 — `auto_scenario.py` + `derived.py` + `topology.py`: update chain consumers.** Topology's per-edge `chain_metadata` ("required: true" / "xor_group: PayoutVehicle") becomes ("required: true" if singleton else "xor_group: <position>" or just rendered as "exactly one of N").
- [ ] **Z.9 — `fuzz.py`: rewrite chain emit.** Drop `xor_group` name invention; emit `Chain(parent, children=[c1])` for required, `Chain(parent, children=[c1, c2, ...])` for XOR. Drop the "every parent gets a required-or-xor child" coverage logic (now implicit — a Chain row IS a firing rule).
- [ ] **Z.10 — Studio editor (X.4.f.10) chain UI rewrite.** Per-row form: parent dropdown + children checkbox-group (sources from rails+templates, same as TransferTemplate.leg_rails). The `_html_id_slug` for `parent::child` composite goes away; new id is `parent::<row-index>` or `parent::<sorted-children-hash>`. Drop the per-row `required` / `xor_group` form fields entirely.
- [ ] **Z.11 — Migrate fixture yamls** (`tests/l2/{sasquatch_pr, spec_example, _kitchen}.yaml` + `tests/l2/fuzz_failures/*.yaml`). Mechanical translation: group rows by `(parent, xor_group)` → one new Chain row each; preserve descriptions per Z.1 choice.
- [ ] **Z.12 — Rewrite chain tests.** `test_l2_validate.py` (drop C2/C4/C4.1 tests, add C6 test); `test_l2_loader.py` (new grammar fixtures); `test_l2_pr_primitives.py`; `test_l2_editor.py`; `test_studio_editor_routes.py` (chain composite-id swap); `test_studio_home_route.py` (chain entity-card shape changes).
- [ ] **Z.13 — SPEC + Schema_v6 + walkthroughs prose.** Update the chain section of `docs/Schema_v6.md`; update any handbook walkthrough that mentions `xor_group` by name.
- [ ] **Z.14 — Re-lock seeds.** Chain firing-rule iteration order may shift baseline seed bytes — re-lock spec_example + sasquatch_pr per-dialect (`quicksight-gen data lock -c run/config.<pg|oracle|sqlite>.yaml --l2 …`). If unchanged, byte-equality test passes; if changed, document the shift in the commit message. (Verify + commit moves to Z.C.)

### Z.B — Promote transfer_type to a first-class declared entity

Surfaced 2026-05-13 during the chain-grammar discussion. The user has been having a hard time explaining the difference between `Rail.name` and `Rail.transfer_type` to people, and that pain is the design signal: today `transfer_type` is a bare snake_case string declared inline-by-mention on every Rail, with no central vocabulary or descriptions. sasquatch_pr declares 21 rails covering ~17 distinct transfer_types (`internal` shared by ZBASweep + InternalTransferSuspenseClose; `settlement` shared by ACHOriginationDailySweep + CustomerFeeMonthlySettlement; etc.) and there is **no place** that documents what each transfer_type means as a vocabulary item. The "many operational rails per money-movement kind" expressiveness is real and load-bearing (the validator's uniqueness key is `(transfer_type, role)`, NOT `name`), but the bare-string shape obscures it.

**The promotion:** lift `transfer_type` to a top-level declared entity (`transfer_types:` block, name + description), so:
- Rail.transfer_type / LimitSchedule.transfer_type / bundles_activity-by-type all reference a declared thing instead of matching strings by coincidence.
- The audit PDF, handbook, and studio editor get a real list of "what kinds of money movement does this institution handle" with operator-authored descriptions.
- The Rail.name vs Rail.transfer_type distinction becomes visually obvious in the yaml: `name` is a Rail's identity, `transfer_type:` references a top-level declaration.

**What's NOT in scope for Z.B:** splitting `bundles_activity`'s "matches Rail.name OR Rail.transfer_type" OR-of-two-resolutions into explicit `bundles_rail` / `bundles_type` kinds. That's a separate cleanup; flagging here so it doesn't accidentally land in this phase.

- [ ] **Z.B.1 — Audit: per-fixture `(distinct transfer_type count, rails-per-type histogram)`.** Output the per-fixture distinct count + which transfer_types are shared across multiple Rails — concrete shape for the new top-level block.
- [ ] **Z.B.2 — `primitives.py`: add `TransferTypeDecl(name: TransferType, description: str | None)`.** Add `transfer_types: tuple[TransferTypeDecl, ...]` to `L2Instance`. Keep `Rail.transfer_type: TransferType` as-is (the field still references by name; the change is that the loader now requires the name to resolve).
- [ ] **Z.B.3 — `loader.py`: parse top-level `transfer_types:` block.** Hard-cut (loader rejects yaml without the block with an actionable error pointing at this section). Reject any `Rail.transfer_type` value whose name doesn't appear in the declared list — surface as `L2ValidationError` with the missing name + the closest declared neighbors.
- [ ] **Z.B.4 — `validate.py`: tighten existing rules + add new R-rule.** New R-rule: "every `Rail.transfer_type` resolves to some `TransferTypeDecl.name`" (loader-side belt-and-suspenders). R10 (`LimitSchedule.transfer_type` matches some `Rail.transfer_type`) becomes "matches some declared `TransferTypeDecl.name`" — strictly more permissive sources, but cleaner: a cap declared against an undeclared type is caught at the type level, not via the rail-emit transitive lookup. R7 (`bundles_activity` matches `Rail.name` OR `Rail.transfer_type`) updates its "OR transfer_type" leg to "OR declared `TransferTypeDecl.name`" with the same pattern.
- [ ] **Z.B.5 — `serializer.py`: dump `transfer_types:` block.** Symmetric with Z.B.3 loader; round-trip stable for spec_example + sasquatch_pr.
- [ ] **Z.B.6 — `editor.py` + studio editor home page: TransferTypeDecl gets create / edit / delete + a home-page section.** Mirrors the existing per-kind UI from X.4.f. Fields: `name` (read-only after create — renaming a transfer_type cascades to every Rail / LimitSchedule / bundles_activity reference; defer the rename UI to a follow-on if it complicates this) + `description` (textarea). `delete_l2_entity` rejects via validator if any Rail still references the type — same pattern as the existing rail-deletion-with-dependent-template guard.
- [ ] **Z.B.7 — Migrate fixture yamls.** Add `transfer_types:` block to all L2 yamls (`tests/l2/{sasquatch_pr, spec_example, _kitchen}.yaml` + `tests/l2/fuzz_failures/*`). Auto-extract names from existing Rail rows. **Descriptions are stub-then-polish:** the migration script writes `description: TODO — what is this transfer_type` per declared name; the user (operator) replaces stubs with real prose in a follow-on commit since the descriptions reflect institutional knowledge, not codebase mechanics.
- [ ] **Z.B.8 — `fuzz.py`: emit `transfer_types:` in random L2 yamls.** Generate a deterministic per-seed list of N transfer_types (snake_case names + stub descriptions); each random Rail picks its `transfer_type` from this list. Drop the previous "invent transfer_type strings inline per rail" code path.
- [ ] **Z.B.9 — Topology + diagram: TransferType is NOT a node.** Confirm the diagram doesn't try to render TransferTypeDecls — they're labels on rails, not topology entities. The home-page section IS a card list (so operators can see / edit them); the diagram stays unchanged.
- [ ] **Z.B.10 — Audit PDF + handbook callouts.** The audit PDF's per-account-day Daily Statement walk currently shows `transfer_type` as a bare column value; cross-reference it against the new TransferTypeDecl.description (one extra column or hover/footnote — design call during Z.B.10). `docs/Schema_v6.md` gets a "Transfer Type vocabulary" section explaining the name-vs-type distinction with a worked example (the explanation pain the user has been hitting).
- [ ] **Z.B.11 — Tests: TransferTypeDecl in `test_l2_validate.py` + `test_l2_loader.py` + `test_l2_editor.py` + `test_studio_*.py`.** Coverage: (a) loader rejects yaml missing `transfer_types:` block; (b) loader rejects Rail referencing an undeclared transfer_type; (c) editor delete rejects when a Rail still references the type; (d) studio home page renders the TransferTypes section.
- [ ] **Z.B.12 — Re-lock seeds (folds into Z.14).** Seed bytes likely unchanged (transfer_types: declaration doesn't reach the SQL emitter — the values flow through Rail.transfer_type as today), but verify byte-equality before declaring "no shift."

### Z.C — End-of-phase

- [ ] **Z.C.1 — Verify (unit + db layer + AW probe).** Subsumes Z.15; runs after both Z.A and Z.B land. Same matrix as Z.15.
- [ ] **Z.C.2 — Commit, tick PLAN, push.** ci.yml runs the full chain. Plan archive sweep + summary line.

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
