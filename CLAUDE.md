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
- **Dialects**: PostgreSQL 17+, Oracle 19c+, and SQLite 3.38+. SQL emitters branch on `Dialect` enum (`common/sql/dialect.py`); SQLite uses JSON1 functions in place of SQL/JSON `JSON_VALUE` (via the `json_value` helper) and matviews emit as `CREATE TABLE … AS SELECT` (refresh = re-CREATE).

## Commands

The CLI is organized around the five artifacts the tool produces:
**schema** | **data** | **json** | **docs** | **audit**, plus two
HTTP-server commands for self-hosted iteration: **studio** (the
implementation tools — diagram + L2 editor + data-shaping panel +
Deploy-changes orchestration; X.4) and **dashboards** (the four real
apps served via the HTMX renderer; X.2). Each artifact has at minimum
`apply`/`clean`/`test`; the `audit` group also carries `verify`.
Everything destructive defaults to emit (print SQL, write JSON to
`out/`, render Markdown to stdout) and only runs against the DB / AWS
/ disk when you pass `--execute`.

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

# Studio (X.4) — implementation-tools surface for the integrator + trainer
# + ETL engineer. One Starlette process serves: unified diagram (`/diagram`),
# L2 editor (`/l2_shape/...`), data-shaping panel (`/data`), Deploy-changes
# (`POST /deploy`), and the four dashboards under `/dashboards/...`. The
# data-shaping panel persists trainer knob mutations to a sibling
# `<cfg.parent>/.studio-state.yaml` sidefile (X.4.h.7) so the cfg.yaml's
# operator-authored comments survive every Studio restart. Trainer knobs:
# scope (full / uncovered_rails / exceptions_only / only_template — X.4.i.1),
# plants subset, end_date scrub head, seed pin, only_template name (when
# scope=only_template), derive_balances toggle (X.4.i.2 — re-derives
# control-account balances from posted transactions for the configured
# account_role set, default gl_control / concentration_master / funds_pool).
quicksight-gen studio -c run/config.yaml --l2 run/sasquatch_pr.yaml
quicksight-gen studio --port 8765 --no-docs   # narrow surface for fast iteration

# Dashboards (X.2) — just the dashboards, no Studio chrome. The HTMX
# renderer alternative to QuickSight; same tree, two backends.
quicksight-gen dashboards -c run/config.yaml --l2 run/sasquatch_pr.yaml
quicksight-gen dashboards --app investigation   # narrow to one app for triage

# Tests — canonical entry is the layered chain runner (Y.2.gate.b/c/m/n).
# Layers: unit → db → app2 → deploy → api → browser. Stops on first
# failure; per-layer artifacts (cmd.json, stdout.log, stderr.log) land
# under runs/<utc-ts>-<short-sha>/<variant>/<layer>/. The `unit` layer
# is variant-independent so it runs ONCE per invocation as a prelude
# (artifacts → runs/<id>/_prelude/unit/), not once per matrix cell
# (Y.2.gate.n). `up_to=unit` runs just the prelude; `>= db` runs the
# prelude first, then fans the matrix out starting at `db`; a prelude
# failure aborts before any cell dispatches.
#
# Variant matrix (Y.2.gate.m): 3-axis cells `scenario × dialect × target`.
# - Scenarios: sp (spec_example), sq (sasquatch_pr), fuzz / fuzz:N (random
#   synthesized L2 yaml — random by default; pin via --variants=f<seed>_..),
#   us:<path> (operator-supplied yaml).
# - Dialects: pg (postgres), or (oracle), sl (sqlite).
# - Targets: lo (local container or sqlite tempfile), aw (operator's
#   external Aurora / Oracle).
# - No flags = full 13-cell matrix. Sub-flags compose multiplicatively;
#   sl × aw auto-skips (sqlite isn't reachable from QuickSight).
#
# Hard-cut from the legacy `local-pg` / `local-oracle` / `local-sqlite` /
# `default` variant names (Y.2.gate.m.2, 2026-05-08). Pass legacy names →
# error with the new sub-flag form to use instead.
./run_tests.sh up_to=unit                                  # ~20s, no DB / no AWS — the variant-independent prelude only (pytest -n auto)
./run_tests.sh up_to=db                                    # unit prelude once, then full 13-cell matrix (parallel via asyncio.gather)
./run_tests.sh up_to=db --dialects=pg --targets=lo         # pg-container only (4 cells: sp/sq × pg × lo)
./run_tests.sh up_to=db --scenarios=sp --dialects=pg,or,sl --targets=lo  # 3-dialect spec_example local
./run_tests.sh up_to=db --scenarios=sp --targets=aw        # AW-target only (operator's external Aurora + Oracle)
./run_tests.sh up_to=db --scenarios=fuzz:5 --dialects=pg --targets=lo    # 5 random fuzz seeds × pg × lo
./run_tests.sh up_to=app2 --variants=sp_pg_lo              # triage: pin a single cell (mutex w/ sub-flags)
./run_tests.sh up_to=db --variants=f12345_pg_lo            # repro a fuzz failure by seed (m.3)
./run_tests.sh up_to=db --parallel=4                       # pytest-xdist N=4 (default = -n auto)
./run_tests.sh up_to=browser --only=test_drift             # narrow every layer's pytest -k <expr>; chain still runs through deploy
./run_tests.sh up_to=db --skip-cheap                       # skip unit if cached green for current SHA
./run_tests.sh up_to=db --coverage                         # emit .coverage.<variant>.<layer> under runs/<id>/ (CI's coverage job globs coverage-data-*; gate.k.1.coverage)
./run_tests.sh sweep                                       # dry-run cleanup of orphan AWS resources
./run_tests.sh sweep --yes                                 # actual cleanup

# Direct pytest is fine for one-off iteration on a single test you're
# actively writing. For layered work (anything beyond unit tests), use
# the runner — see "Test sequencing" section below.
.venv/bin/pytest tests/unit/test_foo.py -k bar -v
```

The `data apply --execute` path reads theme from the L2 institution YAML's inline `theme:` block; when omitted, deploy skips emitting a Theme resource and AWS QuickSight CLASSIC takes over (silent-fallback contract). Schema is emitted per-L2-instance via `common/l2/schema.py::emit_schema(l2_instance)` — base tables (`<prefix>_transactions`, `<prefix>_daily_balances`), Current* views, L1 invariant matviews, Investigation matviews. Seed via `emit_full_seed(l2_instance, scenario)` — composes `emit_baseline_seed` (90-day per-Rail leg generator) + `emit_seed` (planted L1/Investigation scenarios).

### Auth (`Y.2.gate.h+i`) — cfg-driven, never `sso login` in the loop

`run/config.<dialect>.yaml` carries an optional `auth:` block. Both fields are optional; when absent the runner falls back to ambient AWS env / SSO cache.

```yaml
auth:
  # Profile name in ~/.aws/credentials. Runner injects AWS_PROFILE=<value>
  # into every subprocess. Keys live in ~/.aws/credentials, NOT in cfg yaml.
  aws_profile: "quicksight-gen-local"

  # Optional explicit override; skips the auto-derive STS+ListUsers call.
  # Use case: authed as one IAM principal but want embed URLs signed for
  # a different QS user (CI's per-job cfg with the GH-secret value baked in).
  quicksight_user_arn: null
```

When `aws_profile` is set:

- Runner injects `AWS_PROFILE=<value>` into every layer subprocess (`AuthConfig` → `_run_one_variant`).
- Runner auto-derives `QS_E2E_USER_ARN` via `sts:GetCallerIdentity` → `quicksight:ListUsers` → match on `PrincipalId == "federated/iam/<UserId>"`. **The operator no longer exports the env var.**
- `quicksight_user_arn` (when set) wins over the derivation — no API calls fired.

**Long-lived IAM user vs SSO.** The recommended local-dev approach is a dedicated `quicksight-gen-local` IAM user with long-lived access keys (NOT SSO). The reason: a multi-hour Claude-loop session burns through the SSO token's ~12-hour cache, and every cache miss triggers a browser-based `aws sso login` that Claude can't auto-invoke (per `Y.2.gate.b.14.4`'s refusal pattern). Long-lived keys never trigger a browser flow.

**One-time onboarding:** runbook in `docs/audits/y_2_gate_h_i_combined_spike.md` §6. IAM policy: `docs/audits/_iam/quicksight-gen-local-policy.json` (mirror of `Github_e2e_testing` + `quicksight:ListUsers`). CI keeps OIDC unchanged.

### Cfg precedence + tunable defaults (`Y.2.gate.h.2`–`h.5`)

**No `export`-then-run dance.** The operator declares everything once in `run/config.<dialect>.yaml`; the runner injects per-variant overrides into subprocess env when the dialect-flavored container needs a fresh URL.

**DB connection strings (h.2):** every consumer reads `cfg.demo_database_url`. For local-pg / local-oracle / local-sqlite variants, the runner spins a container and injects `QS_GEN_DEMO_DATABASE_URL=<container-url>` into that variant's subprocess env (no operator action). For aw target, `cfg.demo_database_url` from `run/config.<dialect>.yaml` is the source of truth. Precedence the cfg loader honors when present: `QS_GEN_DEMO_DATABASE_URL` env override → cfg yaml field → unset (loud-fail at `connect_demo_db`).

**AWS account / region / partition (h.3):** `cfg.aws_account_id`, `cfg.aws_region`, `cfg.partition` (auto-derived from region). All read from cfg yaml; loader honors `QS_GEN_AWS_ACCOUNT_ID` / `QS_GEN_AWS_REGION` env overrides. Required fields with no defaults — loud-fail when missing.

**Tunables (h.4) — sensible defaults pre-filled, env override for the rare case:**

- `QS_GEN_FUZZ_SEED` — absent ⇒ runner rolls a fresh random `uint32` per invocation; pin only to repro a fuzz failure. Surfaced in runner output (`runner: fuzz_seed=<n> (pin via QS_GEN_FUZZ_SEED env to repro)`).
- `QS_E2E_PAGE_TIMEOUT` — Playwright page-load ms. Default in helpers; override for slow CI runners.
- `QS_E2E_VISUAL_TIMEOUT` — Playwright per-visual wait ms. Default in helpers.
- `QS_E2E_IDENTITY_REGION` — QuickSight identity region for embed-URL signing. Default `us-east-1` (the region where QuickSight subscriptions live regardless of dashboard region).
- `QS_GEN_TEST_L2_INSTANCE` — path to L2 yaml. Default = `cfg.default_l2_instance` (when set in cfg) else bundled `tests/l2/spec_example.yaml`.
- `QS_GEN_RUNNER_YES` — bool; confirms destructive ops (`down`/`sweep`/dirty deploy bypass) when `--yes` flag absent. Off by default.

**Loud failure on missing config (h.5):** `load_config` raises `ValueError("Missing required configuration: ...")` with the missing field names AND the env-var fallbacks (`QS_GEN_AWS_ACCOUNT_ID`, etc.). `connect_demo_db` raises with "set it in your config YAML or via QS_GEN_DEMO_DATABASE_URL." Runner catches both → `EXIT_NEEDS_OPERATOR=2` with the message bubbled to stderr. Probes (`probe_dependencies` for AWS / Docker / cfg-load) fire pre-dispatch and short-circuit the chain with the same exit code. No silent skips.

### Test sequencing + git hooks (`Y.2.gate.d`+`k.5`+`k.7`)

**Always invoke `./run_tests.sh up_to=<layer>`** for the layer you care about. The chain runner enforces `unit → db → app2 → deploy → api → browser` ordering: invoking layer N runs layers 1..N-1 first. `unit` is variant-independent, so it runs ONCE per invocation as a prelude before the matrix fans out (Y.2.gate.n) — not once per cell; a prelude failure aborts before any cell dispatches.

**Never invoke `pytest tests/e2e/` (or any direct `pytest`) for layered work.** Bare pytest skips the earlier gates entirely — a SQL bug becomes "the API e2e passed but the deployed dashboard is empty" 15 minutes after a clean test run, instead of a 30-second smoke failure. Y.2.b's SELECT-alias-in-WHERE bug shipped to a deployed dashboard for exactly this reason. Direct pytest is fine for one-off iteration on a single test you're actively writing; treat it as a smell when the chain hasn't run since.

**Pre-dispatch probes (`Y.2.gate.l.3`).** Before the runner spins up containers or fires AWS calls, it probes live state for every dep the requested layer needs: `aws sts get-caller-identity` (creds), `docker ps` (daemon), `QS_E2E_USER_ARN` (browser embed), and `aws rds describe-db-{cluster,instance}` (cfg-declared cluster + instance status when set). Refuses with `EXIT_NEEDS_OPERATOR=2` and an actionable message ("Run `./run_tests.sh up aws` first") rather than burning ~5 min on container spin-up before surfacing "connection refused".

**Pre-push git hook (`k.5`).** Operator opts in once per clone:

```bash
git config core.hooksPath .githooks
```

Then every `git push` runs `./run_tests.sh up_to=db --dialects=pg --targets=lo` (~30s on local-pg) before the push goes through. Skippable per-push via `git push --no-verify` — discouraged; investigate the failure rather than bypass. The hook no-ops on detached HEAD or branches missing `run_tests.sh` so it doesn't break legacy-branch pushes.

**Failure surface parity (`k.7`).** When CI fails, the message + artifact set matches the runner's local output shape:

- **Per-layer artifacts:** `runs/<run-id>/<variant>/<layer>/{cmd.json,stdout.log,stderr.log,timings.json}` — same paths locally and in CI (the runner uses `RUNS_DIR=runs/` regardless of context).
- **Top-queries dump:** `runs/<run-id>/<variant>/db-perf/top-queries.md` per cell, auto-emitted by the runner (gate.f.4). CI uploads `runs/` as a workflow artifact for triage.
- **Coverage:** the `test` job's `.coverage.<py-version>` + (when `--coverage` is passed — `ci.yml::integration-pg` does, gate.k.1.coverage) the runner's per-(variant, layer) `.coverage.<variant>.<layer>` files, all combined by the `coverage` aggregator into `coverage.md` GHA Step Summary (W.8b — its `coverage-data-*` glob picks both up unchanged).
- **Timings:** `timings.json` uploaded as workflow artifact (gate.k.4); cross-CI-run drift comparable.
- **Failure shape:** `RuntimeError` / `ValueError` from cfg / probe / boto3 paths surface as `EXIT_NEEDS_OPERATOR=2` with the actionable message bubbled to stderr; pytest failures surface as `EXIT_FAILURE=1` with the failed test names + traceback. Same exit codes locally and in CI.

No "decode the GH log" step — the artifact set IS the local triage shape, just packaged in GH artifacts.

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
- Generated resource IDs are kebab-case with prefix `qs-gen-`; use `cfg.prefixed(name)` so the L2 instance prefix is woven in (auto-derived from `l2_instance.instance`), producing IDs like `qs-gen-sasquatch_ar-l1-dashboard`. Enforced by `tests/unit/test_typing_smells.py::qs-gen-prefix` — no hardcoded `"qs-gen-..."` literals outside `common/config.py`.
- All resources tagged `ManagedBy: quicksight-gen` plus `L2Instance: <prefix>` when set; `extra_tags` in config are merged in. `cleanup` uses those tags: legacy mode (no prefix) sweeps any `ManagedBy` resource not in current `out/`; per-instance mode only sweeps resources whose `L2Instance` tag matches.
- Every sheet has a plain-language description; every visual has a subtitle — the end customer is not technical. Enforced by `Sheet.__post_init__` + `Visual.__post_init__` raising `ValueError` on missing/blank.
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

## Filter authoring — SQL-level parameter pushdown is the canonical pattern (Phase Y)

**A filter is a `<<$paramName>>` placeholder in the dataset's CustomSql, not an analysis-level `FilterGroup`.** Phase Y converged the QuickSight and App2 dialects on this: the filter's narrowing happens *in the database query*, identically for QS (which substitutes the literal value into the CustomSql at fetch time, bridged from an analysis parameter via `MappedDataSetParameters`) and App2 (which translates the same placeholder to a `:param_name` bind via the `_sql_executor` preprocessor). Same SQL, same narrowing, two renderers — and the rows fetched shrink at the DB instead of being pulled in full and filtered in-engine.

How to add one:

1. **Date filter** — write the dataset SQL as a template with a `{date_filter}` slot in its `WHERE`, then pass `build_dataset(sql_template, CONTRACT, ..., app2_date_column="<table>.<col>")`. `build_dataset` does both substitutions: QS gets `{date_filter}` → `""` (the analysis-level universal date TimeRangeFilter narrows QS; the SQL just exposes the column), App2 gets `{date_filter}` → `app2_date_filter("<col>", cfg.dialect)` (a `BETWEEN :date_from AND :date_to` bind clause; empty values bind sentinel `1900-01-01`/`9999-12-31` so it matches all rows by default). **Never** hand-roll `sql_template.format(date_filter=...)` + `app2_sql=` — operators forgot the `app2_sql=` and shipped silently-broken date filters; `build_dataset(... app2_date_column=...)` is the typed form that can't be half-done (Y.5.a).
2. **Categorical / slider filter** — put `<<$pParamName>>` directly in the dataset SQL's `WHERE` (`... AND z_score >= <<$pInvAnomaliesSigma>>`, `... AND status IN (<<$pStatusVals>>)` for multi-valued). On the QS side: declare the analysis parameter, wire a `ParameterDropDownControl` / `ParameterSlider` control, and the tree's emit auto-derives the dataset's `DataSetParameter` + the `MappedDataSetParameters` bridge. The `DataSetParameter`'s `DefaultValues.StaticValues` is a **short sentinel, never the full declared-value list** — AWS caps it at **32 elements** (`create-data-set` rejects more; `common/models.py::*DatasetParameterDefaultValues.__post_init__` raises at construction if you exceed it). For a multi-valued dropdown whose value universe is unbounded (rail / chain / template / transfer_type / role names — an L2 may declare >32 of any), shape the SQL `... WHERE ('<sentinel>' IN (<<$pX>>) OR <col> IN (<<$pX>>))` so the 1-element sentinel default means "match all" on load and the bridge narrows on selection (`apps/l1_dashboard/datasets.py::_data_value_clause` + `L1_ALL_SENTINEL`; `apps/l2_flow_tracing/datasets.py::_match_all_in_clause` + `L2FT_ALL_SENTINEL`). The *control's* selectable values still carry the full declared list — AWS caps only the dataset-param default. Fixed-schema enums that are ≤32 by construction (`check_type`, `SupersedeReason`, `status`, `bundle_status`, `completion_status`) keep their direct value-list default + bare `IN (<<$pX>>)`. On the App2 side: the filter spec is auto-derived from the same `ParameterControl` tree node (`X.2.g.2.f/g`) — no separate wiring. Helpers: `common/sql/app2_filters.py::app2_param_eq/gte/lte`; `common/sql/dialect.py::column_name` for case-correct Oracle column refs inside the substituted SQL.
3. **Cross-app drill that filters the target** — a `Drill` action that writes the target's parameter via the deep-link URL. Note the QS limitation (Operational Footguns / quirks log): the URL fragment writes the parameter *store* but does NOT push the value into bound *controls*, and `MappedDataSetParameters` bridges don't fire on URL-driven initial-load value changes — so an embed-driven parameter write filters the data but the control widget shows "All". Surface that in the sheet description; don't add new URL-param drills without re-checking AWS.

Analysis-level `FilterGroup`s (`with_category_filter` / `scope_visuals` / etc.) are **deprecated for filter intent** — kept in `common/tree/` only for the rare "highlight without narrowing" case and the universal date TimeRangeFilter. New filter work goes through dataset SQL. (Customer L2 instance YAMLs are unaffected by any of this — it's internal QS/App2 architecture; their `theme:` / `persona:` / rails / chains blocks are untouched.)

## E2E Test Conventions

### Browser e2e tests speak `DashboardDriver` — never raw Playwright (X.2.q)

A browser e2e test drives a dashboard through the `DashboardDriver` protocol (`tests/e2e/_drivers/base.py`), NOT Playwright directly. The protocol is the test vocabulary — `open` / `goto_sheet` / `sheet_names` / `visual_titles` / `filter_labels` / `filter_options` / `wait_loaded` / `table_rows` / `table_row_count` / `kpi_value` / `pick_filter` / `set_date_range` / `set_slider` / `clear_filters` / `cross_link` / `drill_from_first_row` / `drill_from_first_row_via_menu` / `screenshot` / `close` — and every read returns plain Python (a `list[str]`, a `dict[str, str]`, an `int`), never a `Locator` or `Page`. Two impls:

- **`QsEmbedDriver`** (`tests/e2e/_drivers/qs.py`) — the embedded QuickSight iframe. `QsEmbedDriver.embed(*, aws_account_id, aws_region, viewport=…)` is a `@contextmanager` classmethod that owns the WebKit page; `open(dashboard_id)` mints a fresh region-matched single-use embed URL. The conftest `qs_driver` fixture wraps it (skips cleanly when `QS_E2E_USER_ARN` is unset — the runner derives it from `cfg.auth.aws_profile`; export it yourself for a direct `pytest` run).
- **`App2Driver`** (`tests/e2e/_drivers/app2.py`) — the self-hosted HTMX/d3 page. `App2Driver.smoke()` serves the bundled smoke app + stub fetcher; `App2Driver.serving(*, tree_app, sheet, data_fetcher, …)` serves any tree + fetcher (stub or live-DB via `make_live_db_fetcher_for_app`). `driver.page` / `driver.base_url` are the escape hatch for App2-internal wire-shape assertions (`page.route` for HTTP intercept, `page.expect_response` for refetch checks, `select_option` on `<select name="param_X">`) — the kind of thing the renderer-agnostic verbs deliberately don't expose. Row-level drills (u.4.e.3): App2's Table renderer reads each visual's tree-level `Drill` actions off a `data-row-drills` JSON attr on the visual section and makes every row clickable (left-click → the primary drill, i.e. a `DATA_POINT_CLICK` one if declared else the first), plus a trailing "⋯" button per row that opens a `ctxmenu` popover (vendored — `assets/vendor/js/ctxmenu.min.js`; re-skinned onto the L2 theme via `widgets-theme.css`'s `.ctxmenu` block) listing every drill's label — `drill_from_first_row` clicks the row, `drill_from_first_row_via_menu(visual, item)` clicks the "⋯" then the named `ctxmenu` `<li>`; both verbs work on both renderers (QS uses the data-point click / right-click context menu). The drill navigates `target_path?param_<name>=<row cell value>` for each declared param, and the destination sheet's `server.py` route threads those `?param_*` keys into the rendered filter form's initial state (`_apply_url_param_overrides` — u.4.e.4): a dropdown pre-selects the matching `<option>`, a `ParameterMultiSelect` pre-selects all the repeated-key values, a `ParameterNumberSpec` slider takes the value as its `default`. Because every visual loads via `hx-include="#filter-form"`, the *first* fetch already carries the value — a cross-sheet drill (or a bookmarked `?param_X=Y` URL) renders the destination narrowed, no manual re-pick. QS's own URL-param write still doesn't sync its controls (the standing quirk — `project_qs_url_parameter_no_control_sync`), so don't expect the QS leg to show the picked value in the widget.

A QS-only check just uses `qs_driver`; an App2-only check uses `App2Driver`; an overlapping check ("every visual rendered", "filter narrows the table") is one body parametrized over both. "This verb isn't meaningful here" → the driver raises `NotImplementedError` (not skip) — the test skips the *param*, not the verb. **Enforced**: the `no-playwright-leak` AST lint (`tests/unit/test_typing_smells.py`) flags any `import playwright[.x]` / `from playwright[.x] import …` / `from quicksight_gen.common.browser.{helpers,screenshot} import …` in `tests/e2e/**` outside `tests/e2e/_drivers/` (the AWS-only `get_user_arn` / `generate_dashboard_embed_url` helpers are exempt). New e2e tests use `DashboardDriver`; they do NOT reach into `common/browser/helpers.py`.

### What's sealed inside `QsEmbedDriver` (you don't touch these)

- DOM selectors are QuickSight's `data-automation-id` attributes (`analysis_visual`, `analysis_visual_title_label`, `sn-table-cell-{row}-{col}`, `sn-table-column-N`, `date_picker_0`, `sheet_control_name`, `simplePagedDisplayNav_*`); sheet tabs are `[role="tab"]`. Lives in `common/browser/helpers.py`.
- Tab switches are racy — `click_sheet_tab` snapshots prior visual titles and waits for them to disappear.
- QS tables virtualize: ~10 DOM rows at a time regardless of page size, and below-the-fold visuals don't mount their cells at all. `table_rows()` returns the rendered window; `table_row_count()` does the page-size-bump-to-10000 + scroll-accumulate dance for the true post-filter total.
- After a parameter write, the prior page's rows linger until the dataset re-query lands. The driver waits on QS's WebSocket data layer — `_QsWsActivityTracker` watches the `{"type":"START_VIS","cid":…}` / `{"type":"STOP_VIS","cids":[…]}` frames; `pick_filter` / `set_date_range` / `drill_from_first_row` block until `sent_START - sent_STOP` drains to zero + a 300 ms quiet window (X.2.r — `docs/audits/x_2_r_event_wait_spike.md`, quirks log §3.6). No fixed sleeps.
- Embed URLs are single-use → `qs_driver` is function-scoped.
- Failure diagnostics (screenshot / console / qs-error-overlay text / network / Playwright trace) auto-capture on exception via `webkit_page` — under the runner they land in `$QS_GEN_RUN_DIR/browser/<test_id>/`; in legacy direct-`pytest` mode in `tests/e2e/screenshots/_failures/<test_id>.*`.

### Layers + env

- Two layers: API (boto3 — `-m api`) and browser (Playwright WebKit, headless — `-m browser`). Both gated behind `QS_GEN_E2E=1`. The runner's `api` and `browser` chain layers dispatch them; `e2e.yml` keeps its per-renderer jobs — `e2e-pg-api` and `e2e-pg-browser` both run on push:main + workflow_dispatch + the nightly cron (`0 6 * * *`); browser flipped on for push:main in X.2.i.followon, with the cron retained as a no-push-that-day safety net. `release.yml::e2e-against-testpypi` runs both tiers (API then full browser) against the just-published TestPyPI wheel as the prod-publish gate. The App2 `test_html2_*` tests run via the runner's `app2` layer (`./run_tests.sh up_to=app2 …`) — they spin local servers, no AWS.
- `QS_E2E_USER_ARN` is **required** (not a tunable) for the QS leg — `get_user_arn()` raises `RuntimeError` if unset. The runner auto-derives it from `cfg.auth.aws_profile`; export it manually for a bare `pytest` run. Tunables (with defaults): `QS_E2E_PAGE_TIMEOUT`, `QS_E2E_VISUAL_TIMEOUT`, `QS_E2E_IDENTITY_REGION`. Set `QS_GEN_TEST_L2_INSTANCE` to point fixtures at a non-default L2 YAML.
- The `_harness_*` modules (under `tests/e2e/`) compose seed → deploy → planted-row assertions → cleanup as one fixture; every harness test (`test_harness_end_to_end.py`) runs that flow against a live DB + QuickSight account. (Pre-driver-layer; an open follow-on is to fold its render-side checks onto `DashboardDriver`.)

### CI artifacts

- **`.github/workflows/e2e.yml`** runs three e2e jobs against the user's external DBs (auth-smoke gate first):
  - `e2e-pg-api` — push:main + workflow_dispatch. L1 + Inv + Exec API tests with `pytest-xdist -n auto`. Per-job + workflow-level (`cleanup-pg`) cleanup.
  - `e2e-pg-browser` — push:main + workflow_dispatch + nightly cron (`0 6 * * *`) (flipped on for push:main in X.2.i.followon — the cron stays as a no-push-that-day safety net; `needs: [auth-smoke, e2e-pg-api]` so a SQL/deploy break short-circuits in the fast tier first). L1 + Inv + Exec + L2FT browser tests with `-n 2`, `QS_E2E_PAGE_TIMEOUT=60000`, then — in a *separate* step (its `seeded_audit` fixture re-applies the prefixed schema, so it can't share a worker pool) — the **4-way cross-tool agreement test** (`test_audit_dashboard_agreement.py`: `scenario_plants ⊆ direct_matview_SELECT == QS == App2` (`== PDF` for drift), 6 L1 invariants, full 4-way here since `spec_example` is deployed; row-identity for the flat-shape invariants; spike: `docs/audits/x_2_j_agreement_spike.md`). Failure screenshots uploaded as artifact.
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
