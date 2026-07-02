# Recon Generator

Independent validation tool for midsize financial institutions: layers double-entry accounting invariants on top of the institution's unique shape (accounts, rails, templates, chains, limit schedules) declared in an L2 YAML. Two runtime fronts off one shared core (QuickSight was a third through v15.x; removed in Phase DW):

- **Self-hosted HTMX** (`recon-gen dashboards` / `recon-gen studio`) — four bundled apps (L1 Dashboard / L2 Flow Tracing / Investigation / Executives) via Starlette + Studio implementation tools (diagram, L2 editor, data-shaping panel). The supported renderer + offline iteration loop.
- **Regulator-ready PDF** (`recon-gen audit apply`) — cryptographically fingerprinted, optionally pyHanko-signed. End-of-pipeline 3-way agreement test gates self-hosted / PDF / direct-DB on every L1 invariant violation set.

DB backends: PostgreSQL 17+ / Oracle 19c+ for prod; **DuckDB** as the local-iteration / Studio default (Phase CA swapped it in; Phase CB.8 dropped the prior SQLite dialect entirely in v13.0.0). Recon Generator validates data; it does not move it (customer ETL feeds `<prefix>_transactions` + `<prefix>_daily_balances`; Studio carries an `etl_hook`). Schema / seed / audit-PDF are all emitted from code; the destructive-write verbs (`schema apply` / `data apply` / `audit apply`) only touch the DB / disk with `--execute`.

## Quick Reference

- **Python 3.14** + **uv** (lock at `uv.lock`, venv at `.venv/`; `uv sync --all-extras` after pull; invoke via `.venv/bin/...`)
- **Entry point**: `python -m recon_gen` or `recon-gen`; **CLI**: Click; **Output**: JSON in `out/`
- **Dialects**: PostgreSQL 17+ / Oracle 19c+ / DuckDB (default local); SQL emitters branch on `Dialect` enum (`common/sql/dialect.py`); DuckDB uses `json_extract_string` (not `JSON_VALUE` — DuckDB's `JSON_VALUE` returns quoted JSON form, see [[project_duckdb_local_default_post_ca]]) and matviews are `CREATE TABLE … AS SELECT` (refresh = re-CREATE).

## Config file locations

Two kinds of `config.yaml` exist in this repo. Don't conflate them.

- **Operator-authored cfg** (`run/config.yaml` / `run/config.postgres.yaml` / `run/config.oracle.yaml`) — checked into nothing (the whole `run/` dir is gitignored). Holds AWS account / region, demo DB URL, deployment_name, signing material, auth profile. The CLI discovers it via this candidate order: env `RECON_GEN_CONFIG` override → `config.yaml` (repo root) → `run/config.yaml` → `run/config.postgres.yaml` → `run/config.oracle.yaml`. See `src/recon_gen/common/config.py::load_config` for the loader and `tests/e2e/conftest.py::cfg` for the test-side discovery candidate list. Memory: [[project_local_config_location]].

> **v14.0.0 cfg shape preview (DE phase, lands in v14.0.0).** Today's flat-field shape (`aws_account_id:` / `deployment_name:` / `dialect:` etc.) becomes concern-grouped (`aws:` / `db:` / `app2:` / `audit:` / `auth:` / `test:`) with `extends:` inheritance for base + per-env overlays. **In-process code already uses the v14 nested accessors** (`cfg.aws.account_id` / `cfg.db.url` / `cfg.aws.prefixed(name)` via DE.2 proxy properties); yaml files migrate at DE.5 cut. Spec: `docs/audits/de_0_cfg_redesign.md`. Migration map: `docs/audits/de_0_cfg_redesign.md#migration-v13x--v1400-hard-break`.
- **Runner-managed cfg** (under `runs/<run-id>/<variant>/cfg/`) — written by the runner per cell. `demo_database_url` points at `127.0.0.1:<container-port>` (or `duckdb://` for du dialect); used by every layer via env `RECON_GEN_CONFIG`. (Pre-DW.11 there was a second `qs.yaml` sibling pointing at a `hotchkiss.io:<forwarded-port>` endpoint so QuickSight in us-east-1 could reach the dev-machine Docker — QuickSight is gone, so the two-cfg pattern, the forward, and the `RECON_GEN_QS_CONFIG` env are all retired. Everything runs fully local now.)

## Commands

Five artifact groups (**schema** | **data** | **json** | **docs** | **audit**), each with `apply`/`clean`/`test` (audit adds `verify`), plus HTTP servers **studio** + **dashboards**. Destructive defaults to emit (`out/` / stdout); only writes DB/AWS/disk with `--execute`.

```bash
# Install (uv handles env + lock; add extras as needed)
uv sync --all-extras                       # everything (recommended for dev)
uv sync --extra dev --extra prod           # unit tests + App2 + audit PDF

# Demo flow: schema -> seed -> matview refresh against the demo DB
recon-gen schema apply -c config.yaml --execute
recon-gen data apply -c config.yaml --execute
recon-gen data refresh -c config.yaml --execute

# Audit PDF: query L1 invariant matviews + emit regulator-ready PDF
recon-gen audit apply -c config.yaml --execute -o report.pdf
recon-gen audit verify report.pdf -c config.yaml   # recompute + compare provenance

# Studio (X.4) — Starlette server with unified diagram + L2 editor +
# data-shaping panel + Deploy-changes + the four dashboards under
# /dashboards/. Trainer knob mutations persist to <cfg.parent>/.studio-state.yaml
# so the cfg.yaml's operator-authored comments survive restart.
recon-gen studio -c run/config.yaml --l2 run/sasquatch_pr.yaml
recon-gen studio --port 8765 --no-docs   # narrow surface for fast iteration

# Dashboards (X.2) — just the dashboards, no Studio chrome. The HTMX
# renderer, served straight from the shared tree.
recon-gen dashboards -c run/config.yaml --l2 run/sasquatch_pr.yaml
recon-gen dashboards --app investigation   # narrow to one app for triage

# Tests — layered chain runner. Layers: unit → db → app2 → app2_browser → agreement
# (invoking layer N runs 1..N-1; unit runs ONCE as a prelude, not per-cell).
# agreement is the terminal cross-renderer cross-check. Dialect comes from
# the cfg (RECON_GEN_CONFIG); fully local, no AWS. Per-layer artifacts under
# runs/<id>/<variant>/<layer>/. Full reference: `./run_tests.sh --help`.
./run_tests.sh up_to=unit                                  # ~20s prelude, no DB
./run_tests.sh up_to=db                                    # prelude + db layer (cfg dialect)
./run_tests.sh up_to=agreement                             # full local chain (terminal)
RECON_GEN_CONFIG=run/config.postgres.yaml ./run_tests.sh up_to=db   # pin the dialect via cfg

# Direct pytest is fine for one-off iteration on a single test you're
# actively writing. For layered work, always use the runner — see
# "Test sequencing" section below.
.venv/bin/pytest tests/unit/test_foo.py -k bar -v
```

Theme reads from L2 yaml's `theme:` block; absent ⇒ `DEFAULT_PRESET` (`common/theme.py`) takes over. Schema emitted per-L2-instance via `common/l2/schema.py::emit_schema` (base tables + Current* views + L1/Investigation matviews); seed via `emit_full_seed` (90-day baseline + planted L1/Investigation scenarios).

### Auth + cfg + tunables (`Y.2.gate.h+i`)

`run/config.<dialect>.yaml` carries an optional `auth:` block — `auth.oidc` + `auth.session` for App2's own user-login (Dex in tests), `app2.tls` for HTTPS. AWS-independent; the QS-embed STS-signing half (`auth.aws` + the `quicksight:ListUsers` user-ARN derive) was removed in DW.11.

Cfg precedence (DB URL / tunables): `RECON_GEN_*` env override → cfg yaml field → loud-fail with field name + env fallback. `load_config` raises with the missing field name; `connect_demo_db` raises with cfg/env hint; runner surfaces as `EXIT_NEEDS_OPERATOR=2`. The pre-dispatch probe (`probe_dependencies` — Docker daemon only, post-DW.11) short-circuits with the same code rather than burning ~5 min on a container spin-up before "connection refused".

Tunables (env-overridable, sensible defaults): `RECON_GEN_FUZZ_SEED`, `RECON_E2E_PAGE_TIMEOUT`, `RECON_E2E_VISUAL_TIMEOUT` (Playwright wait knobs), `RECON_GEN_TEST_L2_INSTANCE`, `RECON_GEN_RUNNER_YES`. (The legacy `QS_*`-prefixed aliases still resolve.)

Full runbook + IAM policy + onboarding steps + cfg shape: `docs/audits/y_2_gate_h_i_combined_spike.md`.

### Build hygiene contract — CI ≡ local, no deferred failures

Two non-negotiable locks govern every test/build interaction. They apply to all layers (unit / db / app2 / deploy / api / browser), all dialects, all renderers — not just QS.

- **POLICY 1 — Complete parity between CI and local dev.** Same `./run_tests.sh` invocation shape locally and on CI. Same per-layer artifact paths (`runs/<run-id>/<variant>/<layer>/…`). Same fixture wiring. Same fully-local Docker container substrate (no AWS, no remote anything). If a test passes locally it passes on CI; if it fails on CI it fails locally with the same seed/cfg. There is no "passes locally, fails on CI" class of bug — when a test diverges between the two surfaces, that is a fixture / cfg / env-derivation regression to fix at the divergence point, not a CI-only flake to retry. Workflow-internal parity (`ci.yml` ↔ `release.yml` step lists) falls under the same lock — diverging step lists are themselves the bug.
- **POLICY 2 — Failing tests cannot be deferred.** CI enforces a clean build. A failing test on the chain blocks merge. `xfail` / `skip` / "follow-up backlog" / "fix later" are NOT options for a broken test. The fix lives in the same commit chain as the failure detection. This tightens [[feedback_no_xfail_to_sweep_under_rug]] from a strong preference to a hard rule — the "acceptable uses" carve-out is retired. Genuinely-blocked external-infra work doesn't get an `xfail`; it gets the infra wired or the test deleted with a written rationale in the same commit. The parametrized-driver "verb not meaningful here → `NotImplementedError` skips param" mechanism (see E2E Test Conventions) is a vanishingly-rare escape hatch, not a queue — default to building the missing renderer verb (see [[feedback_build_verbs_not_skip]]).
- **Annotation surface for permanent capability gaps (POLICY 2 carve-out).** Some renderer behaviors are permanent capability gaps, not bugs to fix. These don't get a bare `xfail` / `skip` either. They get a STRUCTURED triple: (1) `NotImplementedError` in the driver verb with a comment naming the gap and cross-linking the memory entry, (2) an entry in the relevant quirks doc with the exact symptom + the verb that triggers it, (3) a memory file at `project_<renderer>_<gap>.md` recording the operator-confirmed reason it's permanent (workarounds tried). The annotation makes the failure traceable, reviewable, and aging-friendly. POLICY 2 prohibits BARE deferrals; the structured triple is an explicit operator-reviewed acknowledgment of a known capability limit, which is different. (The historical exemplars were all QuickSight — URL-params not syncing to controls, MUI Autocomplete virtualization — preserved in `docs/reference/quicksight-quirks.md`; renderer-agnostic harness gaps + the silent-renderer detection pattern live in `docs/reference/test-harness-quirks.md`; with QS gone the discipline now applies to App2 gaps when they surface.) If the renderer later gains the verb, all three artifacts retire together.

### Triage workflow — `./run_tests.sh triage <nodeid>` (2026-06-14, post-DI phase)

When a specific test fails (chain run, CI flake, picker bug, dataset SQL exception) and you need to drive the live system to investigate, `./run_tests.sh triage <pytest_nodeid>` is the canonical entry. Replaces every ad-hoc "spin a PG container by hand, pdb-import" sequence we did pre-DI.

- **What it does.** Layer-infers from nodeid (e.g. `tests/e2e/test_l2ft_*.py` → `app2_browser`; `tests/e2e/app2/` → `app2`; `tests/e2e/db/` → `db`; `tests/unit/` → `unit`). Spins the PG / Oracle / DuckDB container per cfg dialect. Spawns `pytest --pdb <nodeid>` inside a detached GNU `screen` session named `recon-gen-triage`. POLICY 1 enforced by construction: chain and triage both go through `pg_container_url`, so fixture setup is the same code path.
- **Multi-client pdb via screen.** Operator: `screen -x recon-gen-triage` to attach + drive pdb interactively (`Ctrl-A d` to detach without killing). Assistant (non-interactive): `screen -S recon-gen-triage -X stuff $'<cmd>\n'` to push pdb commands; `screen -S recon-gen-triage -X hardcopy /tmp/triage-snap.txt && cat /tmp/triage-snap.txt` to snapshot screen state. Both clients see the same buffer in real time.
- **Pdb gives access to live fixtures.** Inside pdb you have the test's locals plus the driver fixture state — for `app2_browser` tests `driver.page` (live Playwright Page; `.screenshot()`, `.evaluate(js)`, `.locator(...).click()`) + `driver.base_url`. For `db` tier tests: `isolated_cfg` + live cursors. This is the killer feature — you can actually fire `driver.pick_filter('Rail', ['ConcentrationToFRBSweep'])` from pdb and watch the dataset re-query happen.
- **Eager AA.H.6 capture on SQL exceptions.** When the driver's SQL-exception scanner fires, the AA.H.6 capture suite triggers eagerly — screenshot.png / dom.html / console.txt / network.txt / db_counts.txt / trace.zip land in `runs/<run-id>/browser/<sanitized_test_id>/` BEFORE pdb opens, so the artifacts reflect the moment of detection (not the post-assertion state). Pdb drops in at the assertion line; the locals are all reachable.
- **Teardown.** `./run_tests.sh triage-down --yes` — kills the screen session and stops ONLY the triage-spawned container (anonymous testcontainers name captured at spawn time; `recon-gen-snap-test-*` containers + other unrelated runners stay untouched). `--keep-container` skips the container stop.

### Test sequencing + git hooks (`Y.2.gate.d`+`k.5`+`k.7`)

- **Always invoke `./run_tests.sh up_to=<layer>`** — the runner enforces `unit → db → app2 → app2_browser → agreement` ordering (invoking layer N runs 1..N-1; `unit` runs once as prelude, not per-cell). `agreement` is the terminal cross-renderer cross-check. Direct `pytest` is fine only for iterating on a single test you're actively writing; bare pytest for layered work has shipped silent dashboard failures (Y.2.b SELECT-alias-in-WHERE bug). **For triage of a specific failing test, reach for `./run_tests.sh triage <nodeid>` (see Triage workflow above) instead of bare pytest — the runner sets up the same fixture surface the chain does and drops into pdb on the failure.**
- **Git hooks (`k.5` + BT-era)** — opt in once per clone with `git config core.hooksPath .githooks`. Two hooks:
  - **pre-commit** — when a staged change touches `src/recon_gen/common/html/`, auto-rebuild `assets/output.css` (Tailwind) + re-stage. Eliminates the "pytest sessionstart drift gate fails, author hunts for the rebuild recipe" friction loop. Skips silently when `.venv/bin/python` is absent.
  - **pre-push** — runs `./run_tests.sh up_to=unit` (~3-4 min, no Docker; CB.14 bumped it down from the `db` layer after env-leak CI flaps).
  - `--no-verify` discouraged on either hook — investigate the failure rather than bypass.
- **Failure surface parity (`k.7`)** — same exit codes + artifact paths locally and in CI: `runs/<run-id>/<variant>/<layer>/{cmd.json,stdout.log,stderr.log,timings.json}` + per-cell `db-perf/top-queries.md`. Coverage data and timings upload as GHA artifacts. `EXIT_NEEDS_OPERATOR=2` for cfg / probe failures with the actionable message in stderr; `EXIT_FAILURE=1` for pytest. No "decode the GH log" step — the artifact set IS the local triage shape.
- **Local ≡ CI, full stop** (POLICY 1 above — restated here as the operating mechanic). Same `./run_tests.sh` invocation, same per-layer artifact paths, same fixture wiring, same fully-local container substrate. No AWS, no remote anything: the WSL2 self-hosted CI runner spins the same Docker containers the local box does, and the chain tops out at `agreement` (all AWS-free). If a test passes locally, it passes on CI; if it fails on CI it reproduces locally with the same seed/cfg. Divergence is a fixture/cfg/env-derivation bug, not a retry candidate.

## Project Structure

```
src/recon_gen/
  cli/                  # Click CLI: schema | data | json | docs | audit + studio + dashboards
    audit/              # apply | clean | test | verify (PDF reconciliation)
  common/
    config.py           # Config dataclass + YAML/env loader
    models.py           # DatasetParameter family (the SQL-pushdown param shapes _sql_executor reads)
    ids.py              # Typed ID newtypes (SheetId / VisualId / ParameterName)
    theme.py            # DEFAULT_PRESET + ThemePreset + resolve_l2_theme
    persona.py          # DemoPersona — generic skeleton; populated from L2 YAML
    drill.py / clickability.py   # tree drill params + clickable-cell cues
    db.py               # execute_script + Oracle INSERT-ALL batcher
    aging.py / rich_text.py / dataset_contract.py / probe.py / provenance.py
    pdf/                # audit_chrome + signing (pyHanko CMS)
    tree/               # Phase L typed tree primitives
    browser/            # Playwright helpers (sealed inside drivers)
    handbook/           # mkdocs-macros vocabulary + diagrams
    sheets/app_info.py  # Info canary builder
    sql/dialect.py      # Dialect enum
    l2/                 # primitives, validate, loader, schema, seed, auto_scenario, theme, topology
  apps/{l1_dashboard, l2_flow_tracing, investigation, executives}/
  docs/                 # mkdocs source; extract via `recon-gen docs export`
tests/
  test_*.py             # Unit + integration (~50 modules)
  e2e/                  # API + browser layers (QS_GEN_E2E=1)
    _drivers/ / _harness_*.py / test_l1_*.py / test_inv_*.py / test_exec_*.py
scripts/                # Ad-hoc deploy helpers + screenshot generators
```

## Domain Model

All four apps feed two base tables per L2 instance: `<prefix>_transactions` and `<prefix>_daily_balances`. `account_role` + `account_scope` shape which dashboard surfaces a row. Full feed contract in `docs/Schema_v6.md`.

- **`<prefix>_transactions`** — one row per money-movement leg. Keys: `id` PK, `transfer_id` (groups legs of one event), `transfer_parent_id` (chains transfers). Amount: `amount_money` (signed BIGINT cents; + = money IN to account, − = OUT) paired with `amount_direction` (`Debit` / `Credit`). Plus `rail_name`, `origin`, `account_id` + denormalized account fields (`account_role`, `account_scope`, `account_parent_role`), `status`, `posting`, `metadata TEXT` constrained `IS JSON`. The `entry` BIGSERIAL is the supersession key (highest `entry` per logical key wins). Non-failed legs of a non-single-leg transfer net to the template's `expected_net` (zero for a classic debit/credit pair).
- **`<prefix>_daily_balances`** — one row per `(account_id, business_day_start)`. Denormalized account fields + `money` (stored end-of-day balance, signed BIGINT cents) + `expected_eod_balance` + `business_day_start` / `business_day_end` + `metadata TEXT` JSON.

**Sign convention**: `amount_money > 0` = money IN; `< 0` = OUT (agreeing with `amount_direction`). The drift check compares each account-day's stored `daily_balances.money` against the computed `SUM(amount_money)` (`<prefix>_computed_subledger_balance`). An account carries an `account_role` (per-L2 declared — a customer subledger, a control GL, etc.) and an `account_scope` (`internal` / `external`); a leaf account links to its parent via `account_parent_role`.

JSON metadata uses portable SQL/JSON path syntax (`JSON_VALUE`, `JSON_QUERY`, `JSON_EXISTS`). No JSONB, no `->>` / `->` / `@>` / `?` operators, no GIN indexes.

Investigation matviews (per-instance prefixed): `<prefix>_inv_pair_rolling_anomalies` (rolling 2-day SUM per pair → z-score + bucket) and `<prefix>_inv_money_trail_edges` (`WITH RECURSIVE` walk over `transfer_parent_id`). **Don't auto-refresh** — every ETL load runs `refresh_matviews_sql(l2_instance)`.

## Architecture Decisions

- Datasets use custom SQL with Direct Query (no SPICE) — seed changes show up immediately after `data apply`. The renderer reads tree nodes directly; the only `models.py` shapes still on the runtime path are the `DatasetParameter` family (the SQL-pushdown param defaults `_sql_executor` reads).
- SQL portable subset across PG + Oracle: SQL/JSON path syntax; no JSONB, no `->>`, no array / range types. **PG extensions**: scoped exception for `pgcrypto` (audit provenance per Phase CW.2 Lock 3 — `encode(digest(canon, 'sha256'), 'hex')`; MD5 considered + rejected on regulator-defensibility grounds). `recon-gen schema apply` emits `CREATE EXTENSION IF NOT EXISTS pgcrypto` at script top on PG. Do not extend the exception to dataset SQL or schema DDL.
- Resource IDs kebab-case under `cfg.aws.deployment_name` (Z.C, required, no default). Use `cfg.aws.prefixed(name)` → e.g. `recon-prod-l1-dashboard`. Enforced by `tests/unit/test_typing_smells.py::recon-prefix` (no hardcoded `"recon-..."` outside `common/config.py`).
- Tags (`ManagedBy: recon-gen` + `Deployment: <deployment_name>` + `extra_tags`) were for tagging QS/AWS resources; with QuickSight gone there are no tagged resources, so the tag config surface is dead-config pending the config-cleanup sweep.
- Every sheet has a description; every visual has a subtitle — enforced by `Sheet.__post_init__` + `Visual.__post_init__` raising on blank.
- Clickable cells via `common/clickability.py`: accent text = left-click drill; accent on pale-tint background = also carries right-click menu drill.
- **Drill direction convention** — left clicks move LEFT, right clicks move RIGHT. Deeper/down-the-pipeline goes on `DATA_POINT_MENU` (right-click); back-toward-source on `DATA_POINT_CLICK` (left-click). Call both out in visual subtitle when both wired. Existing pre-rule wirings not retroactively flipped.
- **Tree pattern (Phase L).** All four apps are tree-built. `common/tree/` contains `App` / `Analysis` / `Dashboard` / `Sheet` + typed `Visual` subtypes + typed Filter / Parameter / Control wrappers + `Drill` actions. Cross-references are object refs (not string IDs). Internal IDs auto-assigned at `App.resolve_auto_ids()` time; URL-facing IDs (`SheetId`, `ParameterName`) + analyst-facing identifiers stay explicit. `App.validate()` runs the validation walks. New app code uses the tree directly — `apps/<app>/app.py` is the only wiring file.
- **Three-layer model — L1 / L2 / L3.** L1 (`common/tree/`, `common/models.py`, `common/ids.py`, `common/dataset_contract.py`) = persona-blind primitives; `grep common/tree/ -r sasquatch` is zero (the L1 invariant). L2 (`apps/<app>/app.py`, `apps/<app>/constants.py`) = per-app assembly in *domain* vocabulary (CPA-readable), NOT persona names. L3 (`apps/<app>/datasets.py` SQL, L2 yaml `persona:` block) = persona/customer flavor.
- **Tree IS the source of truth.** Tests walk the tree for expected sets, not hand-listed parallel expectations. Identity assertions key off stable analyst-facing identifiers (visual titles, sheet names, dataset identifiers, parameter names) — never auto-derived internal IDs.

## Conventions
- ALL work is planned by `- [ ] phase.task.subtask` in PLAN.md; check boxes along the way; sweep to PLAN_ARCHIVE.md at phase end.
- Type hints throughout. One module per concern.
- **Never hardcode hex colors in analysis code.** Resolve from `theme.<token>` at generate time where `theme = resolve_l2_theme(l2_instance) or DEFAULT_PRESET`.
- **Theme is an L2 instance attribute.** L2 yaml carries inline `theme:` validated by `ThemePreset` (`common/l2/theme.py`); `resolve_l2_theme(l2_instance)` reads it. When omitted, `DEFAULT_PRESET` in `common/theme.py` is the fallback palette. Set `analysis_name_prefix="Demo"` to tag demo analyses.
- Default theme: blues and greys, high contrast, titles ≥ 16px, body ≥ 12px.
- Rich text via `common/rich_text.py`; theme-accent colors resolve at generate time.
- Each dataset declares a `DatasetContract` (column name + type list); SQL query is one implementation. Tests assert SQL projection matches contract.
- **Mark money measures with `currency=True`** so the emitter formats `$1,234.56` instead of `1234.56`.
- **Encode invariants in the type system, not validation tests.** Prefer typed wrappers / `__post_init__` validation / typed constructors that fail at the wiring site over a test that walks generated output. (e2e behavioral tests still own "does it render?")
- **NewType-wrap identifier strings.** `SheetId`, `VisualId`, `FilterGroupId`, `ParameterName`, `DashboardId` (`common/ids.py`) — function params, dict keys, dataclass fields use the wrapper, not bare `str`. Wrap at framework boundaries (`VisualId(str(request.path_params["visual_id"]))`). Identity at runtime; annotation-only cost.
- **`Mapping[K, V]` over `dict[K, V]` in read-only contracts.** Function params that don't mutate signal "I won't write to this"; `dict` stays for return types + mutating locals.
- **Pyright strict scope expands by file, not all-at-once.** Include list in `pyproject.toml::tool.pyright.include` is the gate. Explicit `Any` in strict-scope = escape hatch with `# WHY` comment; bare `Any` parameters are a smell.
- **Docs prose: bullet lists of 4+ items, not slash-separated.** Slash-separated is fine for 2-3-item lists + section titles.
- **Every dashboard's last sheet is `Info` — the App Info canary.** Built via `common/sheets/app_info.py::populate_app_info_sheet`. Real-query KPI + per-matview row-count table + deploy stamp (git SHA + ISO timestamp). When a sheet renders blank: if `Info` shows a number, the renderer is healthy and the empty visual is data/SQL; if `Info` is blank too, the render layer itself is broken. Originally named `i` but single-char tab names were hidden by the old QS renderer.

## Filter authoring — SQL-level parameter pushdown is the canonical pattern (Phase Y)

**A filter is a `<<$paramName>>` placeholder in the dataset's CustomSql, not an analysis-level `FilterGroup`.** App2 translates the placeholder to a `:param_name` bind via `_sql_executor`. Narrowing happens at the DB, not in-engine. (The `<<$...>>` syntax is the historical QS CustomSql form; it's retained because `_sql_executor` reads it.)

1. **Date filter** — `build_dataset(sql_template, CONTRACT, ..., app2_date_column="<table>.<col>")` with a `{date_filter}` slot in the WHERE; App2 gets a `BETWEEN :date_from AND :date_to` bind clause (sentinels match-all by default). The typed form prevents half-done hand-rolls (Y.5.a). The universal date range is now SQL pushdown (`universal_date_range_clause`), not an analysis-layer filter (see [[project_bm_dissolved_universal_timerangefilter]]).
2. **Categorical / slider** — `<<$pParamName>>` directly in the dataset SQL's `WHERE`, derived from the `ParameterControl` tree node. The `DatasetParameter` default-values family in `common/models.py` caps `StaticValues` at **32 elements** (`__post_init__` raises at construction). For unbounded value universes (rail / chain / template names — an L2 may declare >32 of any) use the `('<sentinel>' IN (<<$pX>>) OR <col> IN (<<$pX>>))` shape so the 1-element sentinel default means "match all" (`apps/l1_dashboard/datasets.py::_data_value_clause` + `L1_ALL_SENTINEL`; `apps/l2_flow_tracing/datasets.py::_match_all_in_clause` + `L2FT_ALL_SENTINEL`). Fixed-schema enums ≤32 by construction keep the direct value-list default + bare `IN (<<$pX>>)`. Use `common/sql/dialect.py::column_name` for Oracle case-correctness.
3. **Cross-app drill** — `Drill` action writes the target's parameter via the deep-link URL fragment; the destination's `server.py` threads `?param_*` into the filter form's initial state, so the control reflects the drilled value (App2 only — the old QS renderer had a URL-param-no-control-sync gap, preserved in the quirks log).

Analysis-level `FilterGroup`s (`with_category_filter` / `scope_visuals` / etc.) are **deprecated for filter intent** — kept in `common/tree/` only for the rare "highlight without narrowing" case.

## E2E Test Conventions

### Browser e2e tests speak `DashboardDriver` — never raw Playwright (X.2.q)

Browser e2e tests drive dashboards through `DashboardDriver` (`tests/e2e/_drivers/base.py`), NOT Playwright directly. The protocol exposes ~18 renderer-agnostic verbs (`open` / `goto_sheet` / `sheet_names` / `visual_titles` / `filter_labels` / `filter_options` / `wait_loaded` / `table_rows` / `table_row_count` / `kpi_value` / `pick_filter` / `set_date_range` / `set_slider` / `clear_filters` / `cross_link` / `drill_from_first_row` / `drill_from_first_row_via_menu` / `screenshot` / `close`); every read returns plain Python — no `Locator` / `Page`. The `DashboardDriver` layer STAYS even at a single renderer (the abstraction's value is readability, not just parametrization — [[feedback_keep_test_driver_split]]). One impl:

- **`App2Driver`** (`tests/e2e/_drivers/app2.py`) — self-hosted HTMX/d3 page. `App2Driver.smoke()` for the bundled smoke app; `App2Driver.serving(*, tree_app, sheet, data_fetcher, …)` for any tree + fetcher. `driver.page` / `driver.base_url` escape hatch for App2-internal wire-shape assertions. Row-level drills: `drill_from_first_row` left-clicks the primary; `drill_from_first_row_via_menu(visual, item)` opens the "⋯" ctxmenu and picks the named label; navigates `target_path?param_<name>=<row cell value>`, destination's `server.py` threads `?param_*` into the filter form's initial state.

(QuickSight had a second impl, `QsEmbedDriver`, and the browser tests were parametrized over `[qs, app2]`; DW.6 de-parametrized to app2-only and DW.5 deleted the driver.) When a renderer genuinely can't do a verb — a permanent capability limit — the structured-triple discipline from POLICY 2 applies: the `NotImplementedError` raise gets a comment naming the gap + cross-linking the memory entry, an entry in the quirks doc, AND a `project_<renderer>_<gap>.md` memory file. Bare `NotImplementedError` with no triple is a temporary gap → build the verb ([[feedback_build_verbs_not_skip]]). **Enforced** by `no-playwright-leak` AST lint (`tests/unit/test_typing_smells.py`): no raw `playwright` imports or `common/browser/{helpers,screenshot}` reaches in `tests/e2e/**` outside `_drivers/`.

### Layers, env, sealed-internals

The `app2_browser` layer runs the browser tests (Playwright WebKit headless, `-m browser`) against locally-spun App2 servers. `_harness_*` modules compose seed → render → assert as one fixture against a live DB — all local, no AWS. The `agreement` layer (terminal) reads the db + app2 producer artifacts and asserts the 3-way cross-check.

App2Driver internals (you don't touch these) are sealed inside `_drivers/app2.py`; the renderer-agnostic verbs are the only surface tests touch. Historical QuickSight-renderer quirks: `docs/reference/quicksight-quirks.md`; local browser-test-harness quirks + the silent-renderer detection pattern: `docs/reference/test-harness-quirks.md`.

CI: post-CB.11.c, `e2e.yml` is gone — the e2e legs absorbed into `ci.yml`'s Layered runner job on the WSL2 self-hosted runner (per BY phase). The runner invokes the same `./run_tests.sh up_to=agreement` shape that runs locally; same fully-local container substrate, same per-renderer agreement producers + high-watermark validators under `tests/e2e/agreement/` + `tests/e2e/app2/` (the 3-way gate: `scenario_plants ⊆ direct_matview_SELECT == App2 (== PDF for drift)`). `release.yml::e2e-against-testpypi` is the prod-publish gate (kept on `ubuntu-latest`). Per-job perf dumps upload top-50 expensive queries as workflow artifact.

## Demo Data Conventions

- Every visual should have non-empty data. For each new scenario-dependent visual, add a `TestScenarioCoverage` assertion (≥N rows of that shape) — counts alone don't catch "zero scenario rows slipped through". Write the assertion **before** the visual.
- Generators stay deterministic. Enforced by `tests/unit/test_typing_smells.py::determinism` — no module-level `random.X()` in seed modules.
- Seed: `emit_full_seed(l2_instance, scenario)` driven by `default_scenario_for(l2_instance)`. `data apply` wraps as `densify_scenario(factor=5) → add_broken_rail_plants(15) → boost_inv_fanout_plants(5×)` → ~60k baseline rows + plants. `--seed-density=N` scales all three knobs (1.0 = byte-identical to locked SQL; 2.0 doubles plants).
- Determinism locked at `tests/data/_semantic_locks/<instance>.duckdb.json` (DuckDB-only post-AZ.5 + CB.8; PG/Oracle gated by db-tier integration tests instead). Snapshot is the violation-set per L1 invariant at the canonical anchor (2030-01-01). Re-lock via `recon-gen data semantic-lock --l2 <yaml>` (one invocation per instance) when the shift is intentional. Anchor pinned at `date(2030, 1, 1)`. Per-run drift detection in `runs/<run-id>/{timings,hashes}.json` (Y.2.gate.c.2+c.3).

## Operational Footguns

- **Oracle INSERT-ALL with IDENTITY columns** assigns the SAME identity value to all rows in one statement, breaking composite-PK uniqueness. `common/db.py::batch_oracle_inserts` solves this by tracking ids per batch and flushing before adding a duplicate. Different-id batching is fine; same-id forces a flush.
- **Matviews don't auto-refresh.** Every ETL load (and every `demo apply`) must call `refresh_matviews_sql(l2_instance)` after seeding — otherwise the L1 invariant matviews + Investigation matviews lag the source data and dashboards look empty.
