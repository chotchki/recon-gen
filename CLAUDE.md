# QuickSight Analysis Generator

Python tool that programmatically generates AWS QuickSight JSON definitions (theme, datasets, analyses, dashboards) and deploys them via boto3. Ships **four independent QuickSight apps** plus a **regulator-ready PDF reconciliation report**, all L2-fed off one institution YAML (account, datasource, theme, per-instance schema prefix), sharing the CLI surface:

- **L1 Dashboard** — persona-blind L1 invariant violation surface (drift / overdraft / limit breach / stuck pending / stuck unbundled / supersession audit / today's exceptions / daily statement / transactions). Configured by an L2 instance — feed any institution's L2 YAML once, dashboard renders against it.
- **L2 Flow Tracing** — Rails / Chains / Transfer Templates / L2 Hygiene Exceptions for the integrator validating their L2 instance against the SPEC.
- **Investigation** — 5 sheets: Getting Started, Recipient Fanout, Volume Anomalies, Money Trail, Account Network. Compliance / AML triage flow.
- **Executives** — 4 sheets: Getting Started, Account Coverage, Transaction Volume, Money Moved. Executive scorecard.
- **Audit Reconciliation Report** — printable PDF generated from the same per-instance L1 invariant matviews. Cover + exec summary + per-invariant violation tables + per-account-day Daily Statement walks + sign-off block + cryptographic provenance fingerprint binding the PDF to its source data. Optionally auto-signs via pyHanko when `config.yaml` carries a `signing:` block.

The customer doesn't know exactly what they want yet. Everything is generated from code and deployed idempotently (delete-then-create) so a change is one command to roll out.

## Quick Reference

- **Language**: Python 3.13. (3.12 was supported until Y.2.gate; dropped to simplify the test matrix and to free up newer-syntax features as they land.)
- **Package manager**: uv (lock at `uv.lock`); setuptools build backend; venv at `.venv/`. Run `uv sync --all-extras` after pulling to refresh deps; tests/CLI invoke via `.venv/bin/...` directly (no `source activate`).
- **Entry point**: `python -m quicksight_gen` or `quicksight-gen` (installed script)
- **CLI framework**: Click
- **Output**: JSON files in `out/` (theme, per-app analysis/dashboard, datasets, optional datasource)
- **Dialects**: PostgreSQL 17+ and Oracle 19c+. SQL emitters branch on `Dialect` enum (`common/sql/dialect.py`).

## Commands

The CLI is organized around the five artifacts the tool produces:
**schema** | **data** | **json** | **docs** | **audit**. Each
artifact has at minimum `apply`/`clean`/`test`; the `audit` group
also carries `verify`. Everything destructive defaults to emit
(print SQL, write JSON to `out/`, render Markdown to stdout) and
only runs against the DB / AWS / disk when you pass `--execute`.

```bash
# Install (uv handles env + lock; add extras as needed)
uv sync --all-extras                       # everything (recommended for dev)
uv sync --extra dev --extra audit          # just unit tests + audit PDF
uv sync --extra dev --extra demo --extra demo-oracle  # for `data apply --execute`

# Generate all JSON for the four bundled apps
quicksight-gen json apply -c config.yaml -o out/
quicksight-gen json apply -c config.yaml -o out/ --l2 run/sasquatch_pr.yaml

# Deploy (writes JSON to out/, then delete-then-create against AWS)
quicksight-gen json apply -c config.yaml -o out/ --execute

# Cleanup: delete ManagedBy:quicksight-gen resources not in current out/
quicksight-gen json clean                 # dry-run (default)
quicksight-gen json clean --execute       # actually delete

# Demo flow: schema -> seed -> matview refresh against the demo DB
quicksight-gen schema apply -c config.yaml --execute
quicksight-gen data apply -c config.yaml --execute
quicksight-gen data refresh -c config.yaml --execute

# Audit PDF: query L1 invariant matviews + emit regulator-ready PDF
quicksight-gen audit apply -c config.yaml --execute -o report.pdf
quicksight-gen audit verify report.pdf -c config.yaml   # recompute + compare provenance

# Tests
pytest                              # unit + integration, fast, no AWS
./run_e2e.sh                        # regenerate + deploy 4 apps + e2e (pytest-xdist -n 4)
./run_e2e.sh --parallel 8           # override worker count (1 = serial; ceiling ~8)
./run_e2e.sh --skip-deploy api      # API e2e only
./run_e2e.sh --skip-deploy browser  # browser e2e only
```

The `data apply --execute` path reads theme from the L2 institution YAML's inline `theme:` block; when omitted, deploy skips emitting a Theme resource and AWS QuickSight CLASSIC takes over (silent-fallback contract). Schema is emitted per-L2-instance via `common/l2/schema.py::emit_schema(l2_instance)` — base tables (`<prefix>_transactions`, `<prefix>_daily_balances`), Current* views, L1 invariant matviews, Investigation matviews. Seed via `emit_full_seed(l2_instance, scenario)` — composes `emit_baseline_seed` (90-day per-Rail leg generator) + `emit_seed` (planted L1/Investigation scenarios).

## Project Structure

```
src/quicksight_gen/
  cli/                  # Click CLI shell — schema | data | json | docs | audit groups
    __init__.py         # main + group registration
    schema.py / data.py / json.py / docs.py
    audit/              # apply | clean | test | verify (PDF reconciliation report)
    _helpers.py         # shared resolve_l2_for_demo / emit_to_target / connect_and_apply
    _app_builders.py    # per-app JSON-emit helpers (lifted from legacy CLI)
  __main__.py           # delegates to cli.main
  common/
    config.py           # Config dataclass + YAML/env loader
    models.py           # Dataclasses → AWS QuickSight API JSON (to_aws_json + _strip_nones)
    ids.py              # Typed ID newtypes (SheetId / VisualId / ParameterName / etc.)
    theme.py            # DEFAULT_PRESET fallback + build_theme(cfg, theme | None) → Theme | None
    persona.py          # DemoPersona dataclass — generic skeleton; populated from L2 YAML's persona: block
    deploy.py / cleanup.py / datasource.py
    db.py               # execute_script(cur, sql, dialect=...); Oracle INSERT-ALL batcher
    drill.py            # CustomActionURLOperation cross-app deep-link builder
    clickability.py     # accent text (left-click) + tint background (right-click)
    aging.py            # aging_bar_visual()
    rich_text.py        # XML composition for SheetTextBox.Content
    dataset_contract.py # ColumnSpec + DatasetContract + build_dataset()
    probe.py            # Playwright walker for deployed-dashboard error surfacing
    provenance.py       # Audit fingerprint primitives (Phase U.7)
    pdf/                # audit_chrome.py (bookmarks/footer) + signing.py (pyHanko CMS)
    tree/               # Phase L typed tree primitives — see "Tree pattern"
    browser/            # Playwright helpers (helpers.py + ScreenshotHarness)
    handbook/           # mkdocs-macros vocabulary + diagrams (Phase O.1)
    sheets/app_info.py  # populate_app_info_sheet — Info canary builder
    sql/dialect.py      # Dialect enum (POSTGRES / ORACLE)
    l2/                 # L2 model: primitives, validate, loader, schema, seed,
                        # auto_scenario, derived, theme, topology
  apps/
    l1_dashboard/       # 11 sheets, configured by L2 instance
    l2_flow_tracing/    # Rails/Chains/Templates/Hygiene
    investigation/      # 5 sheets — fanout/anomalies/money trail/account network
    executives/         # 4 sheets — coverage/volume/money moved
  docs/                 # mkdocs source — concepts/, handbook/, walkthroughs/,
                        # for-your-role/, scenario/, Schema_v6.md, _diagrams/, _macros/.
                        # Extract via `quicksight-gen docs export`.
tests/
  test_*.py             # Unit + integration (~50 modules)
  e2e/                  # Two layers: API (boto3) + browser (Playwright); QS_GEN_E2E=1
    _harness_*.py       # End-to-end harness (seed → deploy → assert → cleanup)
    test_l1_*.py / test_inv_*.py / test_exec_*.py
    tree_validator.py / _kitchen_app.py
scripts/                # Ad-hoc deploy helpers + screenshot generators
run_e2e.sh
```

## Domain Model

### Shared base layer

All four apps feed two base tables per L2 instance: `<prefix>_transactions` and `<prefix>_daily_balances`. The `account_type` column discriminates which app a row belongs to. Full feed contract in `docs/Schema_v6.md`.

- **`<prefix>_transactions`** — one row per money-movement leg. Carries `transaction_id` PK, `transfer_id` (groups legs of one financial event), `parent_transfer_id` (chains transfers), `transfer_type`, `origin`, `account_id`, denormalized account fields (`account_name`, `account_type`, `control_account_id`, `is_internal`), `signed_amount` (positive = money IN to the account, negative = money OUT), `amount` (absolute), `status`, `posted_at`, `balance_date`, `external_system`, `memo`, and a `metadata TEXT` column constrained `IS JSON` for app-specific keys. Non-failed legs of a non-single-leg transfer net to zero.
- **`<prefix>_daily_balances`** — one row per `(account_id, balance_date)`. Same denormalized account fields as `transactions` plus `balance` (stored end-of-day) and a `metadata TEXT` JSON column.

**Sign convention.** `signed_amount > 0` = money IN to the account; `< 0` = money OUT. `daily_balances.balance = SUM(signed_amount)` over the account's history (the drift-check invariant). Account-holder view; `Schema_v6.md` reads the same rule from the bank's bookkeeping side.

Six canonical `account_type` values: `gl_control`, `dda`, `merchant_dda`, `external_counter`, `concentration_master`, `funds_pool`. `control_account_id` is a self-referential FK.

JSON metadata uses portable SQL/JSON path syntax (`JSON_VALUE`, `JSON_QUERY`, `JSON_EXISTS`). No JSONB, no `->>` / `->` / `@>` / `?` operators, no GIN indexes on JSON. PostgreSQL 17+ for `demo apply`.

### Investigation matviews

Two materialized views back the heavier sheets, both per-instance prefixed: `<prefix>_inv_pair_rolling_anomalies` (rolling 2-day SUM per (sender, recipient) pair → z-score + 5-band bucket) and `<prefix>_inv_money_trail_edges` (`WITH RECURSIVE` walk over `transfer_parent_id` flattened to one row per multi-leg edge with chain root + depth + `source_display` / `target_display` strings). Both **do not auto-refresh** — every ETL load must run `REFRESH MATERIALIZED VIEW` (use `refresh_matviews_sql(l2_instance)` for the dependency-ordered statements).

The Account Network anchor dropdown is backed by a small dedicated dataset that pre-deduplicates `name (id)` display strings — querying the matview directly forces the planner to compute the concat per row before dedupe.

Walk-the-flow drills (Account Network): right-click any touching-edges table row OR left-click any directional Sankey node overwrites the `pInvANetworkAnchor` parameter. The dropdown widget may briefly lag (QS URL-parameter control sync limitation); sheet description tells analysts "trust the chart, not the control text".

## Architecture Decisions

- All models use Python dataclasses with `to_aws_json()` methods producing the exact dict shape for AWS QuickSight API (`create-analysis`, `create-dashboard`, `create-data-set`, `create-theme`, `create-data-source`). `_strip_nones()` recursively cleans None values.
- Config accepts a pre-existing DataSource ARN for production; for demo, `datasource_arn` is auto-derived from `demo_database_url` and `datasource.json` is generated.
- All datasets use custom SQL with Direct Query (no SPICE). Seed changes show up immediately after `demo apply`.
- SQL is constrained to a portable subset across Postgres + Oracle: SQL/JSON path syntax; no JSONB, no `->>`, no extensions, no array / range types.
- Generated resource IDs are kebab-case with prefix `qs-gen-`; the L2 instance prefix becomes the middle segment via `cfg.l2_instance_prefix` (auto-derived from `l2_instance.instance` in each app's build entry point), producing IDs like `qs-gen-sasquatch_ar-l1-dashboard`.
- All resources tagged `ManagedBy: quicksight-gen` plus `L2Instance: <prefix>` when set; `extra_tags` in config are merged in. `cleanup` uses those tags: legacy mode (no prefix) sweeps any `ManagedBy` resource not in current `out/`; per-instance mode only sweeps resources whose `L2Instance` tag matches.
- Every sheet has a plain-language description; every visual has a subtitle — the end customer is not technical. Coverage enforced in unit + API e2e tests.
- Clickable cells use `common/clickability.py`: accent-colored text = left-click drill; accent text on pale-tint background = also carries a right-click menu drill (use this style whenever a right-click action exists, even if a left-click is also wired).
- **Drill direction convention** — left clicks move you LEFT, right clicks move you RIGHT. Pick the trigger by which sheet the drill points to relative to the source: deeper / further-down-the-pipeline / further-right goes on `DATA_POINT_MENU` (right-click); back-toward-source goes on `DATA_POINT_CLICK` (left-click). Call out both clicks in the visual's subtitle when both are wired. Existing pre-rule wirings are not retroactively flipped.
- **Tree pattern (Phase L).** All four apps are tree-built. `common/tree/` contains `App` / `Analysis` / `Dashboard` / `Sheet` plus typed `Visual` subtypes (`KPI` / `Table` / `BarChart` / `Sankey` / `LineChart`), typed Filter wrappers, Parameter + Filter `Control` wrappers, and `Drill` actions. Cross-references are object refs, not string IDs (visuals reference datasets by `Dataset` node; filter groups reference visuals by `Visual` node; drills reference sheets by `Sheet` node). Internal IDs auto-assigned at emit time using a position-indexed scheme; URL-facing IDs (`SheetId`, `ParameterName`) and analyst-facing identifiers (`Dataset` identifier, `CalcField` name) stay explicit. `App.emit_analysis()` / `emit_dashboard()` runs validation walks. New app code uses the tree directly — `apps/<app>/app.py` is the only file that wires sheets/visuals/filters; per-app `constants.py` carries only URL-facing + analyst-facing identifiers.
- **Three-layer model — L1 / L2 / L3.** The tree's existence is the test case for layer separation:
  - **L1 — `common/tree/`, `common/models.py`, `common/ids.py`, `common/dataset_contract.py`.** Persona-blind primitives. Every type knows about *dashboards* (sheets, visuals, filters, drills, dataset contracts) and nothing about Sasquatch / banks / accounts / transfers. `grep common/tree/ -r sasquatch` should return zero hits — that grep is the L1 invariant.
  - **L2 — `apps/<app>/app.py`, `apps/<app>/constants.py`.** Per-app tree assembly. Talks the *domain* vocabulary ("Account Coverage", "transfer_type", "expected_net_zero") — domain language a CPA would recognize, but **not** persona names ("Sasquatch", "Bigfoot Brews", "FRB Master").
  - **L3 — `apps/<app>/datasets.py` SQL strings, L2 instance YAMLs (incl. optional `persona:` block).** Persona / customer flavor. SQL strings reference real column names; `common/persona.py` is the generic `DemoPersona` skeleton populated by the L2 YAML's `persona:` block; docs templating reads the same strings via `common/handbook/vocabulary.py`.
- **Tree IS the source of truth.** Tests walk the tree to derive expected sets — they do not maintain parallel hand-listed expectations. Identity assertions key off **stable analyst-facing identifiers** (visual *titles*, sheet *names*, dataset *identifiers*, parameter *names*) — never off auto-derived internal IDs (`v-table-s4-2`), which are positional and regenerate on tree restructure.

## Conventions
- ALL work is planned by "- [ ] phase.task.subtask.[subsubtask]" in PLAN.md. The checkboxes MUST be checked along the way. Add additional items aggressively. At the end of a phase, work is summarized and sweep to PLAN_ARCHIVE.md.
- Type hints throughout.
- **Never hardcode hex colors in analysis code.** Resolve from `theme.<token>` at generate time (accent, primary_fg, link_tint, etc.) where `theme` is the `ThemePreset` returned by `resolve_l2_theme(l2_instance) or DEFAULT_PRESET`.
- **Theme is an L2 instance attribute.** Each L2 institution YAML carries an inline `theme:` block validated by `ThemePreset` (`common/l2/theme.py`). When omitted, `build_theme` returns None and AWS QuickSight CLASSIC takes over at deploy. The single `DEFAULT_PRESET` in `common/theme.py` is the in-canvas-accent fallback for apps when their L2 instance declares no theme — no registry, no `--theme-preset` flag. Set `analysis_name_prefix="Demo"` on demo themes to tag analyses.
- One module per concern.
- Default theme: blues and greys, high contrast, titles ≥ 16px, body ≥ 12px.
- Rich text on Getting Started sheets uses `common/rich_text.py`; theme-accent colors resolve to hex at generate time.
- Each dataset declares a `DatasetContract` (column name + type list) in its `datasets.py`; the SQL query is one implementation. Tests assert the SQL projection matches the contract.
- **Mark money measures with `currency=True`** so the emitter formats `$1,234.56` instead of `1234.56`.
- **Encode invariants in the type system, not in validation tests.** When a class of bug can be made unrepresentable through typed wrappers, dataclass `__post_init__` validation, or typed constructor functions that fail at the wiring site, prefer that over a separate test that walks the generated output and asserts shape. End-to-end behavioral tests (e2e) are still the right tool for "does the deployed thing actually render?" — the rule is specifically about correctness invariants of constructed objects.
- **NewType-wrap identifier strings.** Anything that's an *identifier* in a domain — `SheetId`, `VisualId`, `FilterGroupId`, `ParameterName`, `DashboardId` (`common/ids.py`) — gets a `NewType("Foo", str)` wrapper. Function parameters, dict keys, and dataclass fields use the wrapper, not bare `str`. A typo or kind-swap (passing a SheetId where VisualId is expected) becomes a type error at the call site instead of a silent zero-row dashboard. Wrap at framework boundaries — `request.path_params["visual_id"]` comes back `Any`, narrow with `str()` then brand with `VisualId(...)`. NewType is identity at runtime; cost is annotation-only.
- **`Mapping[K, V]` over `dict[K, V]` in read-only contracts.** Function parameters that don't mutate the input use `Mapping[K, V]` so the signature signals "I won't write to this". `dict[K, V]` stays for return types and locals you intend to mutate. (`dict.items()` / `__getitem__` work the same on `Mapping`.)
- **Pyright strict scope expands by file, not all-at-once.** New files added once they're stable + behaviorally tested. The include list in `pyproject.toml::tool.pyright.include` is the gate. When a new file lands in scope, prefer real types over `Any` — explicit `Any` annotations in a strict-scope file are an opt-in escape hatch with a `# WHY` comment; bare `Any` parameters are a smell to fix.
- **Docs prose: bullet lists of 4+ items, not slash-separated.** "X / Y / Z / W / V" gets unreadable fast. Convert any in-prose enumeration of 4+ items to a `-` Markdown bullet list. Slash-separated naming is fine for **section / page titles** that group sister concepts and for **2-3 item lists**.
- **Every dashboard's last sheet is `Info` — the App Info canary.** Built via `common/sheets/app_info.py::populate_app_info_sheet`. Carries a real-query liveness KPI, a per-matview row-count table (caller-supplied list of fully-qualified matview names), and a deploy stamp text box (git short SHA + ISO timestamp baked at generate time). When a sheet renders blank in QuickSight, glance at `Info` first: if `Info` renders a number, QS is healthy and the empty visual is a data/SQL issue; if `Info` is also blank, QuickSight itself is broken (the spinner-forever footgun — see Operational Footguns). Originally named `i` (single-char) but QS hides single-char tab names from the rendered tab strip — verified against us-east-2.

## E2E Test Conventions

- Two layers: API (boto3) and browser (Playwright WebKit, headless). Both gated behind `QS_GEN_E2E=1`.
- Embed URL must be generated against the **dashboard region** (not the QuickSight identity region us-east-1) and is **single-use** — fixtures are function-scoped.
- DOM selectors rely on QuickSight's `data-automation-id` attributes: `analysis_visual`, `analysis_visual_title_label`, `selectedTab_sheet_name`, `sn-table-cell-{row}-{col}`, `date_picker_{0|1}`, `sheet_control_name`. Sheet tabs use `[role="tab"]`.
- Tab switches are racy: `click_sheet_tab` snapshots prior visual titles and waits for them to disappear before callers query the new sheet.
- Filter / drill-down assertions poll for the visual state to change rather than sleeping.
- Below-the-fold tables virtualize their cells — call `scroll_visual_into_view(page, title, timeout_ms)` before asserting on cell content or clicking a row.
- QS tables also virtualize vertically (~10 DOM rows at a time, regardless of page size). `count_table_rows` returns DOM-visible count, saturating at ~10. For filter-narrowing assertions where before/after may exceed the viewport, use `count_table_total_rows` + `wait_for_table_total_rows_to_change` (slower; bumps page size to 10000 and scroll-accumulates the true total).
- Failure screenshots saved to `tests/e2e/screenshots/<app>/` (gitignored).
- `QS_E2E_USER_ARN` is **required** (not a tunable) — `get_user_arn()` raises `RuntimeError` if unset. Export the ARN of the QuickSight user the embed URL should sign for: locally, your default-namespace IAM user; in CI, the `ci-bot` user. Tunables (with defaults): `QS_E2E_PAGE_TIMEOUT`, `QS_E2E_VISUAL_TIMEOUT`, `QS_E2E_IDENTITY_REGION`. Set `QS_GEN_TEST_L2_INSTANCE` to point fixtures at a non-default L2 YAML.
- The `_harness_*` modules (under `tests/e2e/`) compose seed → deploy → planted-row assertions → cleanup as one fixture; every harness test (`test_harness_end_to_end.py`) runs that flow against a live DB + QuickSight account.

### CI artifacts

- **`.github/workflows/e2e.yml`** runs three e2e jobs against the user's external DBs (auth-smoke gate first):
  - `e2e-pg-api` — push:main + workflow_dispatch. L1 + Inv + Exec API tests with `pytest-xdist -n auto`. Per-job + workflow-level (`cleanup-pg`) cleanup.
  - `e2e-pg-browser` — workflow_dispatch + nightly cron (`0 6 * * *`) only (NOT push:main — too slow). L1 + Inv + Exec browser tests with `-n 2`, `QS_E2E_PAGE_TIMEOUT=60000`. Failure screenshots uploaded as artifact.
  - `e2e-oracle-api` — same as `e2e-pg-api` but against the operator's external Oracle (`QS_GEN_ORACLE_URL` secret, `--extra demo-oracle`). Distinct `e2e-oracle` concurrency group runs in parallel with PG.
- **Per-job perf dump** — every e2e job runs `scripts/dump_top_queries.py` after pytest and uploads top-50 expensive queries as a markdown artifact. `pg_stat_statements` (PG) / `v$sqlstats` (Oracle); auto-installs the PG extension on first run.
- **`.github/workflows/release.yml::e2e-against-testpypi`** holds prod publish on a live AWS run against the just-published TestPyPI wheel. `publish-pypi` `needs:` includes this job.
- **`.github/workflows/ci.yml::coverage`** combines per-matrix `.coverage` data files via `coverage combine` and posts a markdown report to the GHA Step Summary + republishes to the `badges` branch (README badge wrap-links to it).
- **`.github/workflows/ci.yml::docs-portable-install`** builds the wheel, installs in a fresh non-editable venv, runs `quicksight-gen docs apply --portable`, and asserts the rendered HTML lands. Regression guard for the bundled-docs path.

## Demo Data Conventions

- Every visual should have non-empty data in the demo. For each new visual that relies on a scenario (drift, overdraft, limit-breach, stuck-pending, etc.), add a `TestScenarioCoverage` assertion guaranteeing ≥N rows of that shape — counts alone don't catch "zero scenario rows slipped through".
- Generators must stay deterministic. Enforced by `tests/unit/test_typing_smells.py::determinism` — no module-level `random.X()` in seed modules.
- Write the coverage assertion **before** the visual, not after.
- The L2-shape demo seed plants every L1 SHOULD-violation kind plus the Investigation fanout via `emit_full_seed(l2_instance, scenario)` — driven by `default_scenario_for(l2_instance)` from `common/l2/auto_scenario.py`. The `data apply` CLI wraps the scenario in `densify_scenario(factor=5) → add_broken_rail_plants(15) → boost_inv_fanout_plants(5×)` before calling `emit_full_seed`, so the live demo gets ~60k baseline rows + plants on top. Pass `quicksight-gen data apply --seed-density=N` (Y.2.gate.c.13.1) to scale all three knobs by `N` — `1.0` (default) is byte-identical to the locked SQL files; `2.0` doubles plant counts for heavier nightly scenarios; `0.5` halves them for fast-iteration runs. Density flows through `cli/_helpers.py::build_full_seed_sql(... density=N)`.
- Determinism is locked at `tests/data/_locked_seeds/<instance>.<dialect>.sql` — one byte-stamped file per `(L2 instance, dialect)` pair. `tests/data/test_locked_seeds.py::test_locked_seed_matches_fresh_emit` re-emits and asserts byte-equality (which subsumes hash equality — the `-- SHA256: <hex>` line in each file's header is a human-readable provenance stamp, not the assertion mechanism). Any generator change that shifts a single byte fails the test loudly — re-lock with `quicksight-gen data lock -c <postgres-or-oracle config> --l2 <yaml>` (run once per dialect) when the shift is intentional. Anchor pinned at `date(2030, 1, 1)` so the emit is machine-independent. Per-run drift detection (across runs of any density) lives separately in `runs/<run-id>/{timings,hashes}.json` (Y.2.gate.c.2 + c.3).

## Operational Footguns

- **QuickSight can fail silently — datasets healthy, analyses dead.** Symptom: every visual on every sheet shows the spinner forever, no error banner, no narrowing-to-zero filter, no API-level error. Datasets describe-cleanly, return data when queried directly through the QS data-source connection, the database itself responds in milliseconds. The dashboard / analysis is just frozen on the QS side. **Diagnostic ladder:** (1) verify the database returns rows for the underlying SQL via psycopg2 / oracledb — proves the data is there; (2) verify `describe_data_set` returns CREATION_SUCCESSFUL — proves the dataset exists; (3) try opening the dashboard in a fresh incognito window — rules out browser cache; (4) **assume QS itself is the broken layer.** Either wait it out (it has cleared on its own) OR force a full delete-then-create of the entire QS resource graph (theme, datasource, all datasets, analysis, dashboard) plus a clean re-seed + matview refresh. Don't keep re-checking your code — the data and SQL are almost certainly fine if (1) and (2) pass.
- **Oracle INSERT-ALL with IDENTITY columns** assigns the SAME identity value to all rows in one statement, breaking composite-PK uniqueness. `common/db.py::batch_oracle_inserts` solves this by tracking ids per batch and flushing before adding a duplicate. Different-id batching is fine; same-id forces a flush.
- **Matviews don't auto-refresh.** Every ETL load (and every `demo apply`) must call `refresh_matviews_sql(l2_instance)` after seeding — otherwise the L1 invariant matviews + Investigation matviews lag the source data and dashboards look empty.
