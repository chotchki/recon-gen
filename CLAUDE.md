# Recon Generator

Independent validation tool for midsize financial institutions: layers double-entry accounting invariants on top of the institution's unique shape (accounts, rails, templates, chains, limit schedules) declared in an L2 YAML. Three runtime fronts off one shared core:

- **AWS QuickSight** — four bundled apps (L1 Dashboard / L2 Flow Tracing / Investigation / Executives) emitted as JSON and deployed via boto3.
- **Self-hosted HTMX** (`recon-gen dashboards` / `recon-gen studio`) — same four apps via Starlette + Studio implementation tools (diagram, L2 editor, data-shaping panel). Offline iteration loop.
- **Regulator-ready PDF** (`recon-gen audit apply`) — cryptographically fingerprinted, optionally pyHanko-signed. End-of-pipeline 4-way agreement test gates QS / self-hosted / PDF / direct-DB on every L1 invariant violation set.

Three DB backends: PostgreSQL 17+, Oracle 19c+, SQLite 3.38+. Recon Generator validates data; it does not move it (customer ETL feeds `<prefix>_transactions` + `<prefix>_daily_balances`; Studio carries an `etl_hook`). Everything generated from code, deployed idempotently (delete-then-create).

## Quick Reference

- **Python 3.13** + **uv** (lock at `uv.lock`, venv at `.venv/`; `uv sync --all-extras` after pull; invoke via `.venv/bin/...`)
- **Entry point**: `python -m recon_gen` or `recon-gen`; **CLI**: Click; **Output**: JSON in `out/`
- **Dialects**: PostgreSQL 17+ / Oracle 19c+ / SQLite 3.38+; SQL emitters branch on `Dialect` enum (`common/sql/dialect.py`); SQLite uses JSON1 in place of SQL/JSON `JSON_VALUE` (`json_value` helper) and matviews are `CREATE TABLE … AS SELECT` (refresh = re-CREATE).

## Commands

Five artifact groups (**schema** | **data** | **json** | **docs** | **audit**), each with `apply`/`clean`/`test` (audit adds `verify`), plus HTTP servers **studio** + **dashboards**. Destructive defaults to emit (`out/` / stdout); only writes DB/AWS/disk with `--execute`.

```bash
# Install (uv handles env + lock; add extras as needed)
uv sync --all-extras                       # everything (recommended for dev)
uv sync --extra dev --extra prod          # just unit tests + audit PDF
uv sync --extra dev --extra prod  # for `data apply --execute`

# Generate all JSON for the four bundled apps
recon-gen json apply -c config.yaml -o out/
recon-gen json apply -c config.yaml -o out/ --l2 run/sasquatch_pr.yaml

# Deploy (writes JSON to out/, then delete-then-create against AWS)
recon-gen json apply -c config.yaml -o out/ --execute

# Cleanup: delete ManagedBy:recon-gen resources not in current out/
recon-gen json clean                 # dry-run (default)
recon-gen json clean --execute       # actually delete

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
# renderer alternative to QuickSight; same tree, two backends.
recon-gen dashboards -c run/config.yaml --l2 run/sasquatch_pr.yaml
recon-gen dashboards --app investigation   # narrow to one app for triage

# Tests — layered chain runner. Layers: unit → db → app2 → deploy → api → browser
# (invoking layer N runs 1..N-1; unit runs ONCE as a prelude, not per-cell).
# Variant matrix: scenario × dialect × target (sp/sq/fuzz × pg/or/sl × lo/aw);
# no flags = full 13-cell matrix; sl × aw auto-skips. Per-layer artifacts under
# runs/<id>/<variant>/<layer>/. Full reference: `./run_tests.sh --help` +
# docs/audits/y_2_gate_h_i_combined_spike.md.
./run_tests.sh up_to=unit                                  # ~20s prelude, no DB / no AWS
./run_tests.sh up_to=db                                    # prelude + full 13-cell matrix
./run_tests.sh up_to=db --dialects=pg --targets=lo         # pg-container only (4 cells)
./run_tests.sh up_to=db --variants=sp_pg_lo                # pin a single cell for triage
./run_tests.sh up_to=db --variants=f12345_pg_lo            # repro a fuzz failure by seed
./run_tests.sh sweep --yes                                 # cleanup orphan AWS resources

# Direct pytest is fine for one-off iteration on a single test you're
# actively writing. For layered work, always use the runner — see
# "Test sequencing" section below.
.venv/bin/pytest tests/unit/test_foo.py -k bar -v
```

Theme reads from L2 yaml's `theme:` block; absent ⇒ QS CLASSIC takes over. Schema emitted per-L2-instance via `common/l2/schema.py::emit_schema` (base tables + Current* views + L1/Investigation matviews); seed via `emit_full_seed` (90-day baseline + planted L1/Investigation scenarios).

### Auth + cfg + tunables (`Y.2.gate.h+i`)

`run/config.<dialect>.yaml` carries an optional `auth:` block (`aws_profile`, `quicksight_user_arn`). The runner injects `AWS_PROFILE=<value>` into every subprocess and auto-derives `QS_E2E_USER_ARN` via `sts:GetCallerIdentity` + `quicksight:ListUsers` — no operator env-var dance. Long-lived `recon-gen-local` IAM keys are preferred over SSO (a multi-hour Claude loop burns through SSO's ~12h cache; SSO cache miss → browser flow Claude can't auto-invoke).

Cfg precedence (DB URL / AWS account / region / tunables): `QS_GEN_*` env override → cfg yaml field → loud-fail with field name + env fallback. `load_config` raises `ValueError("Missing required configuration: ...")`; `connect_demo_db` raises with cfg/env hint; runner surfaces as `EXIT_NEEDS_OPERATOR=2`. Pre-dispatch probes (`probe_dependencies` — AWS creds, Docker daemon, RDS state) short-circuit with the same code rather than burning ~5 min on a container spin-up before "connection refused".

Tunables (env-overridable, sensible defaults): `QS_GEN_FUZZ_SEED`, `QS_E2E_PAGE_TIMEOUT`, `QS_E2E_VISUAL_TIMEOUT`, `QS_E2E_IDENTITY_REGION` (default `us-east-1`), `QS_GEN_TEST_L2_INSTANCE`, `QS_GEN_RUNNER_YES`.

Full runbook + IAM policy + onboarding steps + cfg shape: `docs/audits/y_2_gate_h_i_combined_spike.md`.

### Test sequencing + git hooks (`Y.2.gate.d`+`k.5`+`k.7`)

- **Always invoke `./run_tests.sh up_to=<layer>`** — the runner enforces `unit → db → app2 → deploy → api → browser` ordering (invoking layer N runs 1..N-1; `unit` runs once as prelude, not per-cell). Direct `pytest` is fine only for iterating on a single test you're actively writing; bare pytest for layered work has shipped silent dashboard failures (Y.2.b SELECT-alias-in-WHERE bug).
- **Git hooks (`k.5` + BT-era)** — opt in once per clone with `git config core.hooksPath .githooks`. Two hooks:
  - **pre-commit** — when a staged change touches `src/recon_gen/common/html/`, auto-rebuild `assets/output.css` (Tailwind) + re-stage. Eliminates the "pytest sessionstart drift gate fails, author hunts for the rebuild recipe" friction loop. Skips silently when `.venv/bin/python` is absent.
  - **pre-push** — runs `./run_tests.sh up_to=db --dialects=pg --targets=lo` (~30s).
  - `--no-verify` discouraged on either hook — investigate the failure rather than bypass.
- **Failure surface parity (`k.7`)** — same exit codes + artifact paths locally and in CI: `runs/<run-id>/<variant>/<layer>/{cmd.json,stdout.log,stderr.log,timings.json}` + per-cell `db-perf/top-queries.md`. Coverage data and timings upload as GHA artifacts. `EXIT_NEEDS_OPERATOR=2` for cfg / probe / boto3 failures with the actionable message in stderr; `EXIT_FAILURE=1` for pytest. No "decode the GH log" step — the artifact set IS the local triage shape.

## Project Structure

```
src/recon_gen/
  cli/                  # Click CLI: schema | data | json | docs | audit + studio + dashboards
    audit/              # apply | clean | test | verify (PDF reconciliation)
  common/
    config.py           # Config dataclass + YAML/env loader
    models.py           # Dataclasses → AWS QS API JSON (to_aws_json + _strip_nones)
    ids.py              # Typed ID newtypes (SheetId / VisualId / ParameterName)
    theme.py            # DEFAULT_PRESET + build_theme(cfg, theme | None)
    persona.py          # DemoPersona — generic skeleton; populated from L2 YAML
    deploy.py / cleanup.py / datasource.py / drill.py / clickability.py
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

All four apps feed two base tables per L2 instance: `<prefix>_transactions` and `<prefix>_daily_balances`. `account_type` discriminates which app a row belongs to. Full feed contract in `docs/Schema_v6.md`.

- **`<prefix>_transactions`** — one row per money-movement leg. Keys: `transaction_id` PK, `transfer_id` (groups legs of one event), `parent_transfer_id` (chains transfers). Amount: `signed_amount` (+ = money IN to account, − = OUT), `amount` (absolute). Plus `transfer_type`, `origin`, `account_id` + denormalized account fields, `status`, `posted_at`, `balance_date`, `metadata TEXT` constrained `IS JSON`. Non-failed legs of a non-single-leg transfer net to zero.
- **`<prefix>_daily_balances`** — one row per `(account_id, balance_date)`. Denormalized account fields + `balance` (stored end-of-day) + `metadata TEXT` JSON.

**Sign convention**: `signed_amount > 0` = money IN; `< 0` = OUT. `daily_balances.balance = SUM(signed_amount)` (the drift-check invariant). Six canonical `account_type` values: `gl_control`, `dda`, `merchant_dda`, `external_counter`, `concentration_master`, `funds_pool`; `control_account_id` is self-referential FK.

JSON metadata uses portable SQL/JSON path syntax (`JSON_VALUE`, `JSON_QUERY`, `JSON_EXISTS`). No JSONB, no `->>` / `->` / `@>` / `?` operators, no GIN indexes.

Investigation matviews (per-instance prefixed): `<prefix>_inv_pair_rolling_anomalies` (rolling 2-day SUM per pair → z-score + bucket) and `<prefix>_inv_money_trail_edges` (`WITH RECURSIVE` walk over `transfer_parent_id`). **Don't auto-refresh** — every ETL load runs `refresh_matviews_sql(l2_instance)`.

## Architecture Decisions

- Models use Python dataclasses with `to_aws_json()` → exact AWS QS API dict shape. `_strip_nones()` recursively cleans None.
- Datasets use custom SQL with Direct Query (no SPICE). Seed changes show up immediately after `demo apply`.
- SQL portable subset across PG + Oracle: SQL/JSON path syntax; no JSONB, no `->>`, no extensions, no array / range types.
- Resource IDs kebab-case under `cfg.deployment_name` (Z.C, required, no default). Use `cfg.prefixed(name)` → e.g. `recon-prod-l1-dashboard`. Enforced by `tests/unit/test_typing_smells.py::recon-prefix` (no hardcoded `"recon-..."` outside `common/config.py`).
- Tags: `ManagedBy: recon-gen` + `Deployment: <deployment_name>` (single-axis, replaces legacy `ResourcePrefix` + `L2Instance`); `extra_tags` merged in. `cleanup` gates on `Deployment` only sweeping its own deployment.
- Every sheet has a description; every visual has a subtitle — enforced by `Sheet.__post_init__` + `Visual.__post_init__` raising on blank.
- Clickable cells via `common/clickability.py`: accent text = left-click drill; accent on pale-tint background = also carries right-click menu drill.
- **Drill direction convention** — left clicks move LEFT, right clicks move RIGHT. Deeper/down-the-pipeline goes on `DATA_POINT_MENU` (right-click); back-toward-source on `DATA_POINT_CLICK` (left-click). Call both out in visual subtitle when both wired. Existing pre-rule wirings not retroactively flipped.
- **Tree pattern (Phase L).** All four apps are tree-built. `common/tree/` contains `App` / `Analysis` / `Dashboard` / `Sheet` + typed `Visual` subtypes + typed Filter / Parameter / Control wrappers + `Drill` actions. Cross-references are object refs (not string IDs). Internal IDs auto-assigned at emit time; URL-facing IDs (`SheetId`, `ParameterName`) + analyst-facing identifiers stay explicit. `App.emit_analysis()` / `emit_dashboard()` run validation walks. New app code uses the tree directly — `apps/<app>/app.py` is the only wiring file.
- **Three-layer model — L1 / L2 / L3.** L1 (`common/tree/`, `common/models.py`, `common/ids.py`, `common/dataset_contract.py`) = persona-blind primitives; `grep common/tree/ -r sasquatch` is zero (the L1 invariant). L2 (`apps/<app>/app.py`, `apps/<app>/constants.py`) = per-app assembly in *domain* vocabulary (CPA-readable), NOT persona names. L3 (`apps/<app>/datasets.py` SQL, L2 yaml `persona:` block) = persona/customer flavor.
- **Tree IS the source of truth.** Tests walk the tree for expected sets, not hand-listed parallel expectations. Identity assertions key off stable analyst-facing identifiers (visual titles, sheet names, dataset identifiers, parameter names) — never auto-derived internal IDs.

## Conventions
- ALL work is planned by `- [ ] phase.task.subtask` in PLAN.md; check boxes along the way; sweep to PLAN_ARCHIVE.md at phase end.
- Type hints throughout. One module per concern.
- **Never hardcode hex colors in analysis code.** Resolve from `theme.<token>` at generate time where `theme = resolve_l2_theme(l2_instance) or DEFAULT_PRESET`.
- **Theme is an L2 instance attribute.** L2 yaml carries inline `theme:` validated by `ThemePreset` (`common/l2/theme.py`). When omitted, `build_theme` returns None and QS CLASSIC takes over. `DEFAULT_PRESET` in `common/theme.py` is the in-canvas-accent fallback. Set `analysis_name_prefix="Demo"` to tag demo analyses.
- Default theme: blues and greys, high contrast, titles ≥ 16px, body ≥ 12px.
- Rich text via `common/rich_text.py`; theme-accent colors resolve at generate time.
- Each dataset declares a `DatasetContract` (column name + type list); SQL query is one implementation. Tests assert SQL projection matches contract.
- **Mark money measures with `currency=True`** so the emitter formats `$1,234.56` instead of `1234.56`.
- **Encode invariants in the type system, not validation tests.** Prefer typed wrappers / `__post_init__` validation / typed constructors that fail at the wiring site over a test that walks generated output. (e2e behavioral tests still own "does it render?")
- **NewType-wrap identifier strings.** `SheetId`, `VisualId`, `FilterGroupId`, `ParameterName`, `DashboardId` (`common/ids.py`) — function params, dict keys, dataclass fields use the wrapper, not bare `str`. Wrap at framework boundaries (`VisualId(str(request.path_params["visual_id"]))`). Identity at runtime; annotation-only cost.
- **`Mapping[K, V]` over `dict[K, V]` in read-only contracts.** Function params that don't mutate signal "I won't write to this"; `dict` stays for return types + mutating locals.
- **Pyright strict scope expands by file, not all-at-once.** Include list in `pyproject.toml::tool.pyright.include` is the gate. Explicit `Any` in strict-scope = escape hatch with `# WHY` comment; bare `Any` parameters are a smell.
- **Docs prose: bullet lists of 4+ items, not slash-separated.** Slash-separated is fine for 2-3-item lists + section titles.
- **Every dashboard's last sheet is `Info` — the App Info canary.** Built via `common/sheets/app_info.py::populate_app_info_sheet`. Real-query KPI + per-matview row-count table + deploy stamp (git SHA + ISO timestamp). When a sheet renders blank: if `Info` shows a number, QS is healthy and the empty visual is data/SQL; if `Info` is blank too, QS itself is broken (the spinner-forever footgun). Originally named `i` but QS hides single-char tab names.

## Filter authoring — SQL-level parameter pushdown is the canonical pattern (Phase Y)

**A filter is a `<<$paramName>>` placeholder in the dataset's CustomSql, not an analysis-level `FilterGroup`.** QS substitutes the literal at fetch time (bridged from an analysis parameter via `MappedDataSetParameters`); App2 translates the same placeholder to a `:param_name` bind via `_sql_executor`. Same SQL, same narrowing, two renderers — narrowing happens at the DB, not in-engine.

1. **Date filter** — `build_dataset(sql_template, CONTRACT, ..., app2_date_column="<table>.<col>")` with a `{date_filter}` slot in the WHERE. QS gets the empty substitution (the universal date `TimeRangeFilter` narrows the analysis layer); App2 gets `BETWEEN :date_from AND :date_to` bind clause (sentinels match-all by default). The typed form prevents half-done hand-rolls (Y.5.a).
2. **Categorical / slider** — `<<$pParamName>>` directly in the dataset SQL's `WHERE`. QS auto-derives `DataSetParameter` + `MappedDataSetParameters` from the `ParameterControl` tree node; App2 reads the same node (X.2.g.2.f/g). `DataSetParameter.DefaultValues.StaticValues` is capped at **32 elements** (`common/models.py::*DatasetParameterDefaultValues.__post_init__` raises at construction). For unbounded value universes (rail / chain / template / transfer_type names — an L2 may declare >32 of any) use the `('<sentinel>' IN (<<$pX>>) OR <col> IN (<<$pX>>))` shape so the 1-element sentinel default means "match all" (`apps/l1_dashboard/datasets.py::_data_value_clause` + `L1_ALL_SENTINEL`; `apps/l2_flow_tracing/datasets.py::_match_all_in_clause` + `L2FT_ALL_SENTINEL`). Fixed-schema enums ≤32 by construction keep the direct value-list default + bare `IN (<<$pX>>)`. Use `common/sql/dialect.py::column_name` for Oracle case-correctness.
3. **Cross-app drill** — `Drill` action writes the target's parameter via the deep-link URL fragment. Standing QS quirk (quirks log): the write hits the parameter *store* but NOT bound *controls* (`MappedDataSetParameters` doesn't fire on URL-driven initial-load) — data filters but the control widget shows "All". Surface in sheet description; don't add new URL-param drills without re-checking AWS.

Analysis-level `FilterGroup`s (`with_category_filter` / `scope_visuals` / etc.) are **deprecated for filter intent** — kept in `common/tree/` only for the rare "highlight without narrowing" case and the universal date `TimeRangeFilter`.

## E2E Test Conventions

### Browser e2e tests speak `DashboardDriver` — never raw Playwright (X.2.q)

Browser e2e tests drive dashboards through `DashboardDriver` (`tests/e2e/_drivers/base.py`), NOT Playwright directly. The protocol exposes ~18 renderer-agnostic verbs (`open` / `goto_sheet` / `sheet_names` / `visual_titles` / `filter_labels` / `filter_options` / `wait_loaded` / `table_rows` / `table_row_count` / `kpi_value` / `pick_filter` / `set_date_range` / `set_slider` / `clear_filters` / `cross_link` / `drill_from_first_row` / `drill_from_first_row_via_menu` / `screenshot` / `close`); every read returns plain Python — no `Locator` / `Page`. Two impls:

- **`QsEmbedDriver`** (`tests/e2e/_drivers/qs.py`) — embedded QS iframe. `QsEmbedDriver.embed(*, aws_account_id, aws_region, …)` is a `@contextmanager` that owns the WebKit page; `open(dashboard_id)` mints a fresh single-use embed URL. The conftest `qs_driver` fixture wraps it (skips when `QS_E2E_USER_ARN` is unset — the runner derives it from `cfg.auth.aws_profile`).
- **`App2Driver`** (`tests/e2e/_drivers/app2.py`) — self-hosted HTMX/d3 page. `App2Driver.smoke()` for the bundled smoke app; `App2Driver.serving(*, tree_app, sheet, data_fetcher, …)` for any tree + fetcher. `driver.page` / `driver.base_url` escape hatch for App2-internal wire-shape assertions. Row-level drills: `drill_from_first_row` left-clicks the primary; `drill_from_first_row_via_menu(visual, item)` opens the "⋯" ctxmenu and picks the named label; navigates `target_path?param_<name>=<row cell value>`, destination's `server.py` threads `?param_*` into the filter form's initial state (App2 only — QS's URL-param-no-control-sync quirk still applies).

Parametrized over both; "verb not meaningful here" → `NotImplementedError` (skips the param, not the verb). **Enforced** by `no-playwright-leak` AST lint (`tests/unit/test_typing_smells.py`): no raw `playwright` imports or `common/browser/{helpers,screenshot}` reaches in `tests/e2e/**` outside `_drivers/`.

### Layers, env, sealed-internals

Two layers gated behind `QS_GEN_E2E=1`: API (boto3, `-m api`) and browser (Playwright WebKit headless, `-m browser`). `QS_E2E_USER_ARN` is required for the QS leg (the runner auto-derives it from `cfg.auth.aws_profile`; export manually for a bare `pytest` run). `_harness_*` modules compose seed → deploy → assert → cleanup as one fixture against live DB + QuickSight.

`QsEmbedDriver` internals (you don't touch these): QS `data-automation-id` selectors; racy tab switches (snapshot-and-wait); virtualized tables (~10 DOM rows, `table_row_count()` does the page-size-bump dance); parameter-write waits on the `START_VIS` / `STOP_VIS` WebSocket frames (X.2.r) — no fixed sleeps; embed URLs are single-use; failure diagnostics auto-capture to `$QS_GEN_RUN_DIR/browser/<test_id>/`. Full internals + quirks: `docs/reference/quicksight-quirks.md`.

CI: `e2e.yml` runs `e2e-pg-api` + `e2e-pg-browser` + `e2e-oracle-api` (auth-smoke gate first); browser job includes the 4-way agreement test (`test_audit_dashboard_agreement.py`: `scenario_plants ⊆ direct_matview_SELECT == QS == App2 (== PDF for drift)`). `release.yml::e2e-against-testpypi` is the prod-publish gate. Per-job perf dumps upload top-50 expensive queries as workflow artifact.

## Demo Data Conventions

- Every visual should have non-empty data. For each new scenario-dependent visual, add a `TestScenarioCoverage` assertion (≥N rows of that shape) — counts alone don't catch "zero scenario rows slipped through". Write the assertion **before** the visual.
- Generators stay deterministic. Enforced by `tests/unit/test_typing_smells.py::determinism` — no module-level `random.X()` in seed modules.
- Seed: `emit_full_seed(l2_instance, scenario)` driven by `default_scenario_for(l2_instance)`. `data apply` wraps as `densify_scenario(factor=5) → add_broken_rail_plants(15) → boost_inv_fanout_plants(5×)` → ~60k baseline rows + plants. `--seed-density=N` scales all three knobs (1.0 = byte-identical to locked SQL; 2.0 doubles plants).
- Determinism locked at `tests/data/_locked_seeds/<instance>.<dialect>.sql`. `test_locked_seed_matches_fresh_emit` re-emits + asserts byte-equality. Re-lock via `recon-gen data lock -c <config> --l2 <yaml>` (per dialect) when the shift is intentional. Anchor pinned at `date(2030, 1, 1)`. Per-run drift detection in `runs/<run-id>/{timings,hashes}.json` (Y.2.gate.c.2+c.3).

## Operational Footguns

- **QuickSight can fail silently — datasets healthy, analyses dead.** Every visual spins forever, no error banner, no API error; datasets describe-cleanly, DB responds in ms. **Diagnostic ladder:** (1) hit the underlying SQL via psycopg2/oracledb (proves data); (2) verify `describe_data_set` = `CREATION_SUCCESSFUL` (proves dataset); (3) try a fresh incognito window (rules out browser cache); (4) **assume QS itself is the broken layer** — wait it out OR force full delete-then-create of the entire QS graph (theme + datasource + datasets + analysis + dashboard) plus re-seed + matview refresh. Don't re-check your code if (1) and (2) pass.
- **Oracle INSERT-ALL with IDENTITY columns** assigns the SAME identity value to all rows in one statement, breaking composite-PK uniqueness. `common/db.py::batch_oracle_inserts` solves this by tracking ids per batch and flushing before adding a duplicate. Different-id batching is fine; same-id forces a flush.
- **Matviews don't auto-refresh.** Every ETL load (and every `demo apply`) must call `refresh_matviews_sql(l2_instance)` after seeding — otherwise the L1 invariant matviews + Investigation matviews lag the source data and dashboards look empty.
