# Y.2.gate.a — Test layer chain audit

> **Status:** draft for user review.
> **Purpose:** canonical inventory of what tests exist today, how each one is invoked + gated, and the gaps the Y.2.gate runner needs to close.
> **Origin:** Y.2.b shipped a SQL exception that the e2e suite let through (the smoke verifier was a CLI script not pytest-collected). Y.2.gate.a is the audit pass that maps the chain so we can build a runner that prevents the recurrence.

This doc is the source of truth for the runner design (Y.2.gate.b–g). When something here is wrong, fix here first; downstream tasks key off it.

---

## 1. Principle (refresher)

Tests form an **additive chain**, ordered cheap → expensive. Invoking layer N requires layers 1..N-1 to be green. Re-running cheap layers is always cheaper than rework after deploy. The runner's job is to enforce this — humans (and Claude) WILL forget if it depends on memory.

Variants (dialect, L2 instance, fuzz seed, Python version) sit per-layer. The runner multiplexes them; humans pick by name not by env-var combination.

Friction (looking up ARNs, env-var passthrough, `aws sso login`) is automation backlog, not workflow.

---

## 2. The layer table (canonical)

| # | Layer | What | Where | Today's invocation | Auto-hooked? | Variants today | Preconditions | Wall-clock (single-variant) |
|---|---|---|---|---|---|---|---|---|
| 1 | **pyright strict** | Type check on the strict-include set in `pyproject.toml::tool.pyright.include` | `src/quicksight_gen/{common/tree,common/l2,common/sql,common/browser,common/db,common/config,common/html/_sql_executor,common/html/_tree_fetcher,common/html/server,common/dataset_contract,common/models}` | `conftest.py::pytest_sessionstart` runs `.venv/bin/pyright`; `pytest` invokes it before any test collects. Bypass: `QS_GEN_SKIP_PYRIGHT=1`. | ✅ via pytest sessionstart | Python 3.13 only (pyright pinned). | None — pure static check. | ~1s |
| 2a | **Unit + JSON tests** | In-process tests for builders, models, tree, JSON-emit shapes | `tests/{unit,json,cli,docs,schema,l2}/test_*.py` (~120 files) | `pytest tests/{unit,json,cli,docs,schema,l2}` (also runs as part of bare `pytest tests/`) | ✅ via `pytest` | Python 3.13 only (3.12 dropped Y.2.gate); SQLite snapshot tests run in-process. | None — no DB, no AWS, no network. | ~10s |
| 2b | **Custom AST lints** | `tests/unit/test_typing_smells.py` walks pyright include set + flags `Any` / bare-str ID smells | Same as 2a (pytest-collected) | Same as 2a | ✅ via `pytest` | None (orthogonal to dialect/L2). | None. | ~1s |
| 2c | **JS unit tests** | Playwright-driven tests for HTMX renderers + bootstrap.js, against static HTML fixtures (no server) | `tests/js/test_*.py` (8 files) | Same as 2a (pytest-collected) | ✅ via `pytest` (requires `playwright install webkit`) | None. | Playwright WebKit binary installed locally. | ~5s |
| 3a | **DB SQL smoke (parse + plan)** | Each dataset's CustomSQL substituted with default values, wrapped in `SELECT * FROM (…) sub WHERE 1=0`, executed against live DB. Catches dialect-specific syntax bugs, missing-column refs (Y.2.b's bug class). | `tests/e2e/test_dataset_sql_smoke.py` (Y.2.b hotfix; pytest, behind `QS_GEN_E2E=1`) + `tests/integration/verify_dataset_sql.py` (older CLI script — same logic, used in CI) | Pytest: `QS_GEN_E2E=1 pytest tests/e2e/test_dataset_sql_smoke.py` (37 datasets parametrized). CLI: `python tests/integration/verify_dataset_sql.py --config <cfg> --l2-instance <yaml>`. | ✅ in pytest path; CLI variant remains parallel | **Dialect**: PG / Oracle (CLI runs both in CI; pytest infers from cfg). **L2 instance**: cfg-driven. | Live DB exists at `cfg.demo_database_url` AND schema applied AND seed loaded. | ~17s for 37 datasets on PG |
| 3b | **DB matview row-counts** | Per-invariant row-count assertions against deployed seed; locks expected scenario coverage. | `tests/integration/verify_demo_apply.py` (CLI script) | `python tests/integration/verify_demo_apply.py --config <cfg> --l2-instance <yaml>` | ❌ CLI only — **gap** | **Dialect**: PG + Oracle (both in CI). **L2 instance**: cfg-driven. | Same as 3a + matviews refreshed. | unknown — measure during conversion |
| 3c | **DB runtime assertions** | Per-invariant DB-side assertions (drift / overdraft / etc. populate as seeded). | `tests/data/test_l2_runtime_assertions.py` (~6 tests, `pytest.skip` if `QS_GEN_DEMO_DB_URL` unset) | `pytest tests/data/test_l2_runtime_assertions.py` with `QS_GEN_DEMO_DB_URL=...` | ⚠️ pytest-collected but skips silently without env var | **Dialect**: PG only today (psycopg-based). **Fuzz seed**: `QS_GEN_FUZZ_SEED` if pinned. | Same as 3a. | unknown |
| 3d | **Audit PDF vs DB** | PDF render vs DB-side recompute across invariants. Gated `QS_GEN_DB_TESTS=1`. | `tests/audit/test_pdf_matches_scenario.py` | `QS_GEN_DB_TESTS=1 QS_GEN_CONFIG=<cfg> pytest tests/audit/test_pdf_matches_scenario.py` | ⚠️ silently skipped without `QS_GEN_DB_TESTS=1` | **Dialect**: PG. | Same as 3a + audit PDF rendered. | unknown |
| 3e | **Audit PDF vs SQLite** | Same shape as 3d but in-process SQLite (no live DB). | `tests/audit/test_pdf_sqlite.py` | `pytest tests/audit/test_pdf_sqlite.py` | ✅ via `pytest` (in-process SQLite, no env var) | **Dialect**: SQLite only. | None (in-process). | unknown — fast |
| 4 | **Deploy** | `quicksight-gen json apply --execute` — builds JSON, then delete-then-creates AWS QuickSight resources tagged `ManagedBy:quicksight-gen` + `L2Instance:<prefix>`. | `quicksight-gen` CLI (Click) | `quicksight-gen json apply -c run/config.<dialect>.yaml -o run/out/ --execute` | ❌ manual — **gap** | **Dialect** (via cfg): PG-AWS / Oracle-AWS. **L2 instance** (via cfg): spec_example / sasquatch_pr / etc. | AWS creds valid; cfg present; schema + seed already in DB. | ~3min for 4 apps |
| 5 | **API e2e** | boto3 against deployed resources — describe + structural assertions, no browser. | `tests/e2e/test_*_deployed_resources.py`, `test_*_dashboard_structure.py` (5 files marked `@pytest.mark.api`) | `QS_GEN_E2E=1 pytest tests/e2e -m api` (or `./run_e2e.sh --skip-deploy api`) | ✅ via `pytest` behind `QS_GEN_E2E=1` | **Dialect** (via cfg): PG / Oracle. **L2 instance** (via cfg + `QS_GEN_TEST_L2_INSTANCE` override). | Layer 4 ran for the same cfg/L2 combo. | ~25s @ `-n auto` |
| 6 | **Browser e2e** | Playwright WebKit against embed URLs; sheet renders + visuals + filters + drilldowns. | `tests/e2e/test_*_dashboard_renders.py`, `test_*_sheet_visuals.py`, `test_*_filters.py`, `test_inv_drilldown.py`, `test_audit_dashboard_agreement.py`, `test_l2ft_*_dropdowns.py` (~15 files marked `@pytest.mark.browser`) | `QS_GEN_E2E=1 QS_E2E_USER_ARN=... pytest tests/e2e -m browser` (or `./run_e2e.sh --skip-deploy browser`) | ✅ via `pytest` behind `QS_GEN_E2E=1` + **`QS_E2E_USER_ARN` required** | Same as layer 5. | Same as 5 + `QS_E2E_USER_ARN` (raises if unset) + Playwright WebKit installed. | ~2min @ `-n 4` |
| 7 | **App2 (HTMX) live e2e** | Layer-2 e2e against the App2 Starlette server (HTMX dialect) — runs the server, hits routes, asserts rendered HTML. | `tests/e2e/test_html2_*.py` (3 files: executives, executives_live, money_trail) | `QS_GEN_E2E=1 pytest tests/e2e -k html2` | ✅ via `pytest` behind `QS_GEN_E2E=1` | App2 wraps the same dataset SQL — variants follow cfg. | Live DB available (no AWS needed for App2 — it's the alternative dialect that bypasses QS). | unknown |
| 8 | **Harness e2e (per-test ephemeral)** | M.4.1 end-to-end harness — each test deploys its own ephemeral QS resources (separate from prod cell), runs assertions, tags-cleanup at teardown. Runs across 3 L2_INSTANCES (spec_example, sasquatch_pr, fuzz-seed). | `tests/e2e/test_harness_end_to_end.py` + `_harness_*.py` helpers | `./run_e2e.sh --harness` (skips prod deploy + skips rest of e2e to avoid resource ID collision) | ✅ via shell wrapper | **L2 instance**: 3 variants per run. **Fuzz seed**: pinned via `QS_GEN_FUZZ_SEED`. | AWS creds + DB available. | 5–10 min per L2_INSTANCE @ xdist=3 |

---

## 3. Variant axes (per layer enablement)

| Variant | Layer 1 | 2a | 2b | 2c | 3a | 3b | 3c | 3d | 3e | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Python 3.13** | — | ✅ | ✅ | ✅ | — | — | — | — | — | — | — | — | — | — |
| **Dialect: PG** | — | — | — | — | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Dialect: Oracle** | — | — | — | — | ✅ | ✅ | ❌ (psycopg only) | ❌ | — | ✅ | ✅ | ❌ (cron only) | ❌ | ❌ |
| **Dialect: SQLite** | — | ✅ snapshot tests | — | — | (handled by 3e) | — | — | — | ✅ | — | — | — | ✅ (App2 supports it) | — |
| **L2 instance: spec_example** | — | ✅ | — | — | ✅ | ✅ | — | — | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| **L2 instance: sasquatch_pr** | — | ✅ (`test_l2_sasquatch_pr.py` + parametrized seed tests) | — | — | (cfg-driven) | (cfg-driven) | — | — | — | (cfg-driven) | (via `QS_GEN_TEST_L2_INSTANCE`) | (via override) | — | ✅ |
| **Fuzz seed (`QS_GEN_FUZZ_SEED`)** _today_ | — | ⚠ 1 seed/run | — | — | — | — | ⚠ | — | — | — | — | — | — | ✅ pinned via `run_e2e.sh` |
| **Fuzz seed** _under-exploited target_ (§7.11) | — | ✅ N seeds | — | — | ✅ N seeds | ✅ N seeds | ✅ N seeds | ⚠ | ✅ N seeds | ⚠ sample | ⚠ sample | ⚠ sample | ✅ N seeds | ✅ |

**Notes:**
- Layer 3c (runtime assertions) is PG-only because the file imports psycopg directly. To extend to Oracle, branch on `cfg.dialect`.
- Layer 6 browser × Oracle is **not run today** (CI nightly cron is PG-only). Whether to add it is a separate decision — Oracle browser is mostly redundant since QuickSight renders the same regardless of underlying SQL dialect, but it would catch dialect-specific QS-side rendering bugs. **Opinion:** worth adding as a nightly cell.
- **Fuzz seed is dramatically under-exploited.** Today only one test consumes it (`tests/data/test_l2_seed_contract.py`) at one seed per session. The fuzzer (`tests/l2/fuzz.py::random_l2_yaml`) produces a different valid L2 topology per seed — every layer 2a/3a/3b/3c/3e/7 could parametrize across N seeds for property-testing-style coverage. See §7.11.

---

## 4. Invocation surfaces today

What scripts/commands the developer (and CI) actually types:

| Command | Layers covered | Notes |
|---|---|---|
| `pytest` (or `.venv/bin/pytest`) | 1 + 2a + 2b + 2c + 3e | Full local fast suite. Skips e2e (3a, 3c, 3d, 5, 6, 7, 8) silently because `QS_GEN_E2E` unset. |
| `pytest tests/unit` | 1 + 2a + 2b (subset) | Pyright runs once at sessionstart regardless of selection. |
| `python tests/integration/verify_demo_apply.py …` | 3b | CLI only. Used in CI but not in dev loop. |
| `python tests/integration/verify_dataset_sql.py …` | 3a | CLI only. Used in CI; same logic also in `tests/e2e/test_dataset_sql_smoke.py`. |
| `quicksight-gen schema apply --execute` | (deploy schema only) | Precondition for 3a–3d, layer 4. |
| `quicksight-gen data apply --execute` | (seed only) | Precondition for 3a–3d. |
| `quicksight-gen data refresh --execute` | (matview refresh only) | Precondition for 3a–3d. |
| `quicksight-gen json apply --execute` | 4 | The deploy itself. |
| `./run_e2e.sh` | 4 + 5 + 6 (deploy + all e2e) | Pinned `QS_GEN_FUZZ_SEED` if not set. Default `--parallel 4`. |
| `./run_e2e.sh --skip-deploy api` | 5 only | Skips deploy. |
| `./run_e2e.sh --skip-deploy browser` | 6 only | Skips deploy. |
| `./run_e2e.sh --harness` | 8 (only) | Skips prod deploy + rest of e2e suite (resource-ID conflict). |
| `quicksight-gen json test` (and other `<verb> test` subcommands) | 1 + (subset of) 2 | Discovered: `cli/json.py::test` shells out to `pytest + pyright`. Per-artifact CLI surface for "run tests for this artifact". Worth investigating as a precedent / candidate runner foundation. |

**Gap surface:**
- No single command runs the full chain in order. Even `./run_e2e.sh` skips layers 1–3 (assumes you ran `pytest` separately).
- Layers 3b + 3c + 3d are silently skipped in normal `pytest` invocations (CLI script or env-gated). Easy to forget.
- Layer 7 (App2 e2e) doesn't run alongside layer 6 by default; needs `-k html2`.

---

## 5. CI workflows today

| Workflow | Trigger | Layers covered | Variants exercised |
|---|---|---|---|
| `ci.yml::test` | push:any + PR | 1 + 2a + 2b + 2c + 3e + (parts of 3a/3b via `tests/e2e/test_dataset_sql_smoke.py`? no — `QS_GEN_E2E` unset, so it skips) | Python 3.13 only (matrix dropped Y.2.gate) |
| `ci.yml::integration` | push:any + PR | 4 (deploys to ephemeral PG + Oracle Free containers) + 3a (`verify_dataset_sql.py`) + 3b (`verify_demo_apply.py`) + audit PDF render-and-verify | PG + Oracle dialects, spec_example L2 instance |
| `ci.yml::coverage` | after `test` matrix | (aggregator) | — |
| `ci.yml::docs-portable-install` | push:any + PR | builds wheel, installs in fresh venv, runs `quicksight-gen docs apply --portable` | — |
| `ci.yml::badges` | after coverage | (badge generation) | — |
| `e2e.yml::e2e-pg-api` | push:main + dispatch | 5 (against ephemeral AWS deploy) | PG-AWS, spec_example |
| `e2e.yml::e2e-pg-browser` | dispatch + nightly cron `0 6 * * *` | 6 (against ephemeral AWS deploy) | PG-AWS, spec_example |
| `e2e.yml::e2e-oracle-api` | push:main + dispatch | 5 (against ephemeral AWS deploy) | Oracle-AWS, spec_example |
| `release.yml::test` | tag:v* | 1 + 2 (Python 3.13 only) | — |
| `release.yml::e2e-against-testpypi` | tag:v* (gates `publish-pypi`) | 4 + 5 + 6 against the just-published TestPyPI wheel (NOT editable install) | PG-AWS, spec_example |
| `pages.yml` | docs change | mkdocs strict build | — |

**CI-side "works on my box" exposure:**
- **Layer 6 × Oracle** is unrun in CI. If a QS-side rendering bug only manifests with Oracle's column-type / value formatting, we wouldn't catch it locally OR in CI.
- **Layer 7 (App2 e2e)** doesn't have a dedicated CI cell — runs alongside e2e-pg-api implicitly via `pytest tests/e2e` (need to confirm — the `-m api` filter may exclude it).
- **Layer 8 (harness)** doesn't run in CI at all — local-only, gated `--harness`.
- **`tests/e2e/test_dataset_sql_smoke.py`** (Y.2.b's hotfix) runs in CI via the `integration` job's `verify_dataset_sql.py` invocation — same logic, different entry point. The pytest version runs locally only when someone passes `QS_GEN_E2E=1`. **Decision: in the runner, the pytest version is the canonical one and CI calls it via the runner; the CLI script is dropped.**

---

## 6. Credentials + env vars currently in play

What env vars the test paths consume today (sorted by category):

| Env var | Consumed by | Today's resolution | Discovery target (Y.2.gate.h) |
|---|---|---|---|
| `QS_GEN_E2E` | `tests/e2e/conftest.py` skip gate | Operator sets `=1` to opt in | Runner sets implicitly when invoking layer ≥5 |
| `QS_GEN_SKIP_PYRIGHT` | root `conftest.py` | Operator sets `=1` to bypass | Runner exposes `--skip-cheap` if useful; otherwise leave as-is |
| `QS_GEN_DB_TESTS` | `tests/audit/test_pdf_matches_scenario.py` skipif | Operator sets `=1` | Runner sets implicitly when invoking layer ≥3d |
| `QS_GEN_CONFIG` | conftest fixture; multiple test files | Operator points at one of `run/config.{postgres,oracle}.yaml` | Runner picks based on `--variant=<dialect>` |
| `QS_GEN_DEMO_DB_URL` | `tests/data/test_l2_runtime_assertions.py` | Operator exports DB URL | Runner reads from cfg or container output |
| `QS_GEN_TEST_L2_INSTANCE` | conftest `inv_l2_prefix` / `exec_l2_prefix` / etc. | Operator exports YAML path | Runner picks based on `--variant=<l2>` |
| `QS_GEN_FUZZ_SEED` | `tests/data/test_l2_seed_contract.py`, `run_e2e.sh` (auto-pins) | `run_e2e.sh` auto-generates if unset; harness expects pinned | Runner auto-pins; surfaces value in run summary |
| `QS_GEN_AWS_ACCOUNT_ID` / `QS_GEN_AWS_REGION` | dashboard-extract test + Config loader fallback | Cfg yaml takes precedence; env-var only if cfg absent | Runner reads cfg; never asks operator |
| `QS_GEN_DATASOURCE_ARN` | dashboard-extract test | Cfg fallback | Same as above |
| `QS_GEN_PRINCIPAL_ARNS` | Config loader | Cfg fallback | Same |
| `QS_E2E_USER_ARN` | `common/browser/helpers.py::get_user_arn` (raises if unset) | Operator must export | **Critical auto-discovery target** — derive from `aws sts get-caller-identity` + `aws quicksight list-users` |
| `QS_E2E_PAGE_TIMEOUT` / `QS_E2E_VISUAL_TIMEOUT` | conftest + harness | Defaults (30s / 10s) usually fine | Runner exposes as advanced tunable |
| `QS_E2E_IDENTITY_REGION` | conftest | Default us-east-1 | Same |
| `QS_E2E_SCREENSHOT_DIR` | `common/browser/helpers.py` | Default `tests/e2e/screenshots` | Runner sets per-variant subdir |

**Dropped silently or magic-defaulted:** `QS_GEN_AWS_ACCOUNT_ID` / `QS_GEN_DATASOURCE_ARN` get dummy values in the audit dashboard-extract test. That's fine for in-process tests but confirms we should never let production code path silently fall back to dummy values.

---

## 7. Gaps + decisions for review

Open design questions surfaced by the audit. Need user direction before Y.2.gate.b finalizes.

### 7.1 — CLI verifiers vs pytest tests

**Today:** `verify_demo_apply.py` + `verify_dataset_sql.py` are CLI scripts. The latter has a pytest twin (`test_dataset_sql_smoke.py`); the former does not.

**Decision:** Convert `verify_demo_apply.py` to `tests/e2e/test_demo_apply_row_counts.py` (or similar) and have CI call the pytest version. Then delete the CLI script. **Tracked in Y.2.gate.f.**

**Question for user:** Are there other "CLI tests" you know about? (`scripts/*.py` is unclear — `dump_top_queries.py`, `harness_manual_deploy.py`, `m2_6_verify.py`, `qs_substitution_probe.py`, `sweep_harness_orphans.py` — are any of these "tests" in disguise?)

### 7.2 — Silent-skip env gates vs runner-driven gates

**Today:** `QS_GEN_DB_TESTS=1`, `QS_GEN_DEMO_DB_URL=…`, `QS_E2E_USER_ARN=…` all silently skip (or raise) when the env is missing. The operator can't tell from a green pytest output that the env-gated tests didn't run.

**Decision options:**
- **(A) Strict**: rip out the silent-skip gates. The runner sets the env vars based on `--up-to=<layer>`. If a test is meant for layer 3c, the runner sets `QS_GEN_DEMO_DB_URL` (resolved from cfg or container); if not running layer 3c, the test isn't collected.
- **(B) Loud**: keep the gates but make pytest summarize "5 tests skipped because `QS_GEN_DEMO_DB_URL` unset" in red at the bottom. Operator knows what they missed.

**Opinion:** (A). Silent-skip is the bug class that lets Y.2.b-shaped problems through. The runner makes (A) viable because it knows what layer it's running.

### 7.3 — `quicksight-gen <verb> test` precedent

**Discovered:** `cli/{json,schema,data,docs,audit}.py::test` already shell out to `pytest + pyright` for per-artifact testing. This is a precedent for "CLI invokes the test layers it cares about".

**Decision options:**
- **(A) Build the runner as a Click subcommand**: `quicksight-gen test up_to=<layer> variants=<set>`. Rides the existing CLI infrastructure; operator's muscle memory carries over.
- **(B) Build as a shell script**: `./run_tests.sh up_to=<layer> variants=<set>`. Explicit, easier to read, doesn't pollute the user-facing CLI.

**Opinion:** (B) for the runner; keep `<verb> test` as is (per-artifact convenience), and have it eventually delegate to the runner under the hood. The runner is dev-tooling, not customer-facing CLI.

### 7.4 — Dialect coverage gap (browser × Oracle)

**Today:** `e2e-pg-browser` runs nightly. `e2e-oracle-browser` doesn't exist.

**Question:** Is QuickSight's rendering layer dialect-sensitive enough to justify the second cell? (My read: probably not for Sankey/Table/KPI; possibly for date formatting / timezone display.) **Defer decision** but flag as a candidate for nightly addition.

### 7.5 — Container-backed local DBs (LOCKED)

**Decision:** Containers as default for layer 3 (testcontainers-python). Aurora reserved for layer 4+ (deploy / QS e2e). Opt-in `--live-db` flag for the rare "test against the actual deployed DB" case. **Locked by user 2026-05-07** (consistent with §7.10's App2 promotion — both flow from "local-Docker is the fast-feedback substrate").

### 7.6 — Layer 3 substructure (3a/3b/3c/3d/3e)

The audit found 5 sub-layers under "DB tests". These differ in what they exercise (parse vs counts vs invariant assertions vs PDF agreement vs SQLite recompute) and how they're gated.

**Question:** Worth collapsing to fewer layers in the runner's UX (`up_to=db` runs all of them), or keep the granularity (`up_to=db.smoke` vs `up_to=db.runtime`)?

**Opinion:** Collapse to `up_to=db` for simplicity. Power users can scope further with `--only=…`.

### 7.7 — Layer 7 (App2) and layer 8 (harness) placement

**Today:** Layer 7 runs alongside layer 6 (in `pytest tests/e2e`); layer 8 runs ONLY via `--harness` (mutually exclusive with layer 5/6).

**Question:** Are these really the same conceptual layer? Layer 7 is "App2 dialect renders" — more like a dialect variant of layer 6. Layer 8 is "ephemeral per-test deploy + assertion" — a different topology entirely.

**Opinion:**
- Layer 7 → variant of layer 6 (App2 dialect).
- Layer 8 → its own layer 9, runs after layer 6, but optional (gated `--harness=true`). Nightly opt-in; not in the default chain.

### 7.8 — Wall-clock targets need measurement

The audit table marked some layers as "unknown" wall-clock. Before Y.2.gate.b finalizes the budget targets, we should measure each layer once and lock numbers in.

### 7.10 — App2 against local Docker as the early e2e gate (LOCKED)

**Decision:** App2 (HTMX dialect, layer 7 today) becomes the canonical fast-feedback e2e gate at **layer 3.7** in the new chain. QS layer 4-6 demotes to nightly + pre-release parity cell. **Locked by user 2026-05-07.**

**Rejected paths** (don't re-litigate):

- **VPC connection + Tailscale-into-VPC tunnel** to expose local Docker to QS. Plumbed via parked `hotfix-v8.7.4-vpc-connection-arn` branch. Rejected: QS VPC connections are expensive (per-hour billing) AND don't relieve the QS API throttle anyway — QS is still in AWS, still rate-limits at the render layer.
- **Public IP + port-forward** from local Docker to QS. User has the public IP available but explicitly chose not to use it: layering works given App2 is maturing and we'd rather invest in App2's maturity than build perimeter infra around QS.

**Why the reframe works:** App2's "immaturity" is a feature for this decision — it means every Phase Y / Phase X.2 sweep deepens its coverage. The percentage of bug classes App2 catches is monotonically increasing as the project progresses. QS coverage stays roughly constant (it's a frozen render layer); App2 grows. The chain placement reflects the trajectory, not just today's snapshot.

**Better answer that's already built — promote App2 in the chain:**

App2 (layer 7 in the audit, X.2.f/g/h) runs the **same dataset SQL** as QS, against any database including local Docker. No AWS contact, no QS rate-limits, no ARN/auth juggling. **It's the early e2e gate that already exists.**

**Reframe:**

| Pre-Y.2.gate framing | Post-Y.2.gate framing |
|---|---|
| Layer 7 (App2) ran alongside layer 6 (QS browser) with no clear ordering | Layer 7 (App2) becomes **layer 3.7** — runs after DB smoke, **before** QS deploy. Fast-feedback gate against local Docker. |
| QS layer 5/6 was the canonical "e2e" | QS layer 5/6 becomes the **parity / regression cell** — catches QS-side rendering bugs (Sankey layout, dashboard structure, embed-URL signing) that App2 by definition can't. Runs nightly + pre-release; not on every iteration. |
| Layer 4 (deploy) was a hard prerequisite for "e2e green" | Layer 4 (deploy) is a hard prerequisite for layer 5/6, but App2 (3.7) runs against ephemeral Docker and skips deploy entirely for fast-feedback runs. |

**Coverage matrix:**

| Bug class | App2 (local Docker) | QS (AWS) |
|---|---|---|
| Dialect SQL bugs (Y.2.b's `WHERE on alias`) | ✅ catches | ✅ catches |
| Calc-field-to-SQL translation (X.2.g.2.c) | ✅ catches | ✅ catches (via different path) |
| Pushdown parameter substitution (Y.1+) | ✅ via App2's `:param_*` bind preprocessor | ✅ via QS `<<$paramName>>` substitution |
| QS-side rendering (Sankey layout, KPI styling) | ❌ — App2 has its own renderer | ✅ catches |
| Embed URL signing / IAM identity | ❌ — App2 doesn't use embed URLs | ✅ catches |
| QS dashboard structure / sheet count | ❌ | ✅ catches |
| Filter widget UI behavior (dropdown, slider, date picker) | ⚠ App2 has equivalents but different UI surface | ✅ catches the QS-specific shape |

**~80% of the bug classes that today require an AWS deploy are catchable in App2 against local Docker.** The remaining 20% (rendering, embed, dashboard structure) genuinely need QS — but that's a small, slow nightly cell rather than a per-iteration gate.

**Implication for runner design:**

- **Default invocation** (`./run_tests.sh up_to=e2e`) goes through layer 3.7 (App2 + Docker). No AWS contact. Wall-clock target: under 5 minutes including Docker startup.
- **AWS invocation** (`./run_tests.sh up_to=qs-e2e` or similar) is opt-in for the cases where you actually changed QS-side wiring. Includes layer 4-6.
- **CI**: PR-quick = App2 only (fast PR feedback). Push:main = App2 + QS PG-API. Nightly = full matrix including QS browser. Existing `e2e.yml` cells become the QS-side cells; new `app2-e2e.yml` cell becomes the high-frequency gate.

**Strategic reprioritization scope:** Not Y.2.gate-only. Y.2.gate.b/c slot App2 ahead of QS in the chain; Phase X.2.j (4-way cross-tool agreement) owns the App2-coverage growth that justifies it. Compounding wins: Y.2 SQL pushdown reduces QS query pressure → AWS contention easier; App2 layering reduces AWS dependency further → iteration speed compounds.

---

### 7.11 — Fuzz-seed property-testing as the highest-value parallelism target (user-flagged)

**User observation:** the fuzz-seed variant axis is dramatically under-exploited; this is where parallelism unlocks the most coverage per minute.

**Today's reality:**

- `tests/l2/fuzz.py::random_l2_yaml(seed)` deterministically generates a **valid random L2 instance** — different topology of accounts / rails / transfers / chains / limit schedules per seed. Same seed = byte-identical YAML.
- One test consumes it: `tests/data/test_l2_seed_contract.py`, **one seed per session** (random per run unless `QS_GEN_FUZZ_SEED=N` pins it).
- Layer 8 (harness) honors the same pin so per-test ephemeral deploys are reproducible.
- **That's it.** Every other layer runs against the static L2 fixtures (`spec_example` and `sasquatch_pr` in `src/quicksight_gen/_l2_fixtures/`).

**The gap:**

Each fuzz seed = a different synthetic L2 instance the generator says is valid. Property-testing-style sampling across N seeds would catch a class of bugs the static fixtures can't:

- **Generator-output bugs**: "L2 with 0 merchant accounts crashes drift calc"; "L2 with 12 chains and 2 templates produces a SQL with duplicate alias"; "L2 with all rails of one transfer_type breaks the L2FT cascade dropdown"; etc.
- **Cross-dialect inconsistencies**: PG accepts what Oracle rejects. With static fixtures, we only catch dialect bugs that happen to exercise the static topology. Fuzz seed × dialect catches them combinatorially.
- **Schema-name collisions**: per-seed prefix uniqueness; no static-fixture coverage today.
- **Edge cases** the human-written `spec_example` / `sasquatch_pr` deliberately avoid for readability.

**The parallelism unlock:**

| Gate | Today (1 seed) | With 100 seeds in parallel | With 1000 seeds (nightly) |
|---|---|---|---|
| Layer 2a JSON-emit (in-process) | ~10s | ~10s @ xdist=auto | ~30s |
| Layer 3a DB SQL smoke (PG container) | ~17s | ~30s — 100 seeds × Docker PG, parallel | ~5min |
| Layer 3a DB SQL smoke (Oracle container) | (not run) | ~5min — Oracle slow image, but parallel | ~30min |
| Layer 7 App2 (local Docker) | (not parametrized) | ~2min — 100 servers + assertions, parallel | ~15min |

The numbers above are rough but illustrate the shape: **iteration speed drops from "1 seed per run" to "100 seeds per run" without proportional wall-clock cost**, because local Docker per-variant + xdist absorbs the parallelism. The user's beefy Mac is the substrate — no AWS contention to worry about.

**Design decisions for the runner:**

- **Sample sizes per layer**: 100 seeds for layer 2a/3a/3b/3c/3e/7 on each `up_to=e2e` invocation; 1000 seeds on nightly. AWS-touching layers (4-6) sample 1-3 seeds since each costs minutes.
- **Seed pool determinism**: the runner picks seeds from a deterministic sequence (e.g., `range(N)` or `Random(git_sha).sample(...)`) so the seed set is the same across local + CI for a given commit. Pinned by run-id for reproducibility.
- **Failure attribution**: per-test failure carries `fuzz_seed=<N>` so a triager can `QS_GEN_FUZZ_SEED=<N>` reproduce locally. Same shape as the M.4.1.f harness failure manifest.
- **Coverage threshold**: a seed that breaks 3 of 100 layer-3a tests is real signal; a seed that breaks 1/100 might be a generator edge case. Runner reports failure rate; threshold for ⚠ TBD (10%? 1%?).
- **Generator regressions**: if a previously-green seed starts failing, that's a regression in either the generator or the consumer code. Runner diffs failing seeds against the prior run's set (same-shape pattern as the timing-diff in §7.9).

**Compounds with §7.10's App2 promotion** — App2 against local Docker × 100 fuzz seeds is the same wall-clock as one App2 run, and exercises 100× more topology coverage. Each Phase Y / X.2 sweep that lands deepens what the property-test catches.

**Decision needed:**

- Sample sizes per layer (above are guesses; user calibration needed).
- ⚠ threshold for "this seed is a real bug, not a generator edge case".
- Whether the seed pool is fixed (range(N)) or hash-derived from the commit SHA.

**Tracked under:** Y.2.gate.b (variants design — fuzz seed promoted from "Future" to first-class), Y.2.gate.c (implement seed-pool sampling), Y.2.gate.j (parallelism — fuzz-seed parallelism is the biggest single perf win).

---

### 7.9 — Per-run output isolation + timing capture (new)

**User direction:** Every run gets its own isolated output dir, and the runner captures per-(layer, variant, test) timings. On the next run, the runner reads the prior run's timings and reports `step took 24s (was 19s, +26%)`. This becomes a smell detector — same shape as hash-locked seed data: a sudden timing delta flags a regression before it crashes the test.

**Output isolation (what gets isolated):**

Every artifact a test layer produces must land under a per-run dir, never overwriting the prior run. Candidates today:
- **Generated JSON:** `run/out/` (today shared); should become `runs/<run-id>/out/<dialect>/`.
- **Failure screenshots:** `tests/e2e/screenshots/` (today shared, gitignored); should become `runs/<run-id>/screenshots/<dialect>/<test-id>/`.
- **Failure manifests:** `tests/e2e/failures/*.txt` (today shared); should become `runs/<run-id>/failures/`.
- **Coverage data:** `.coverage.py3.{12,13}` (today named per-Python-version, accumulates); should become `runs/<run-id>/coverage/`.
- **Top-queries dumps:** `dump_top_queries.py` output (today shared); per-run.
- **Container logs:** PG / Oracle container stdout/stderr from layer 3 — never captured today; per-run.
- **Per-test fixture data:** if the runner spins per-test ephemeral DBs, each gets its own container ID logged under `runs/<run-id>/containers.jsonl`.

**Run-id scheme:** `<utc-iso-timestamp>-<git-short-sha>-<dirty-flag>`, e.g. `20260507T184215Z-30a5ac0`. Dirty-flag suffix when working tree has uncommitted changes (so timing deltas across dirty runs don't claim "+50%" because of unrelated edits).

**Timing capture:**

Runner writes a per-run `timings.json` keyed by (layer, variant, test-id):

```json
{
  "run_id": "20260507T184215Z-30a5ac0",
  "timings": {
    "layer_3a.pg.test_dataset_sql_smoke[inv-money-trail-dataset]": 0.42,
    "layer_3a.pg.test_dataset_sql_smoke[l1-drift-dataset]": 0.31,
    "layer_4.pg.deploy": 187.3,
    "layer_5.pg.test_inv_deployed_resources::test_dataset_count": 1.4,
    ...
  },
  "totals": {
    "layer_1": 0.9,
    "layer_2a": 8.4,
    ...
  }
}
```

**Diff against prior run:**

On invocation, runner reads the most recent `runs/*/timings.json` (matching git SHA if available; else most recent). For each (layer, variant, test) about to run, compares the prior timing. Output:

```
Layer 3a (pg) — DB SQL smoke
  test_dataset_sql_smoke[inv-money-trail-dataset]    0.41s  (was 0.42s, -2%)
  test_dataset_sql_smoke[l1-drift-dataset]           0.62s  (was 0.31s, +100%) ⚠
  ...
Layer 4 (pg) — Deploy
  deploy                                             204.8s (was 187.3s, +9%)
```

Threshold for warning highlight (the ⚠): **>50% change** (configurable). Below that, just print the absolute + delta.

**Why this works:**
- It's the same logical pattern as hash-locked seed data — a stable artifact (timing value) that changes loudly when something underlying shifts.
- Surfaces perf regressions that don't crash a test (Y.2's whole point is reducing query count + wire bytes; a sudden timing increase means we regressed).
- Detects flakes: if `test_X` flickers between 1s and 3s, the warnings will scream every run.
- Detects environmental drift: if PG container startup goes from 5s to 30s, we know.

**Storage policy:**
- `runs/` is gitignored (large, frequently-written).
- Keep last N=20 runs; auto-prune older. Configurable.
- Optionally upload `timings.json` as a CI artifact so cross-CI-run drift is also visible.

**Tracked under:** Y.2.gate.b (design) + Y.2.gate.c (implement) — adds requirements but doesn't need its own letter unless we decide it's enough scope to split out.

---

## 8. Appendix — file inventory by directory

For verification — every test/script file the audit considered.

### `tests/unit/` (Python in-process unit tests — layer 2a/2b)

`test_aging.py`, `test_browser_helpers.py`, `test_clickability.py`, `test_column_human_name.py`, `test_common_db.py`, `test_config_loader.py`, `test_config_partition.py`, `test_docs_cli_invocations.py`, `test_etl_examples.py`, `test_html_data_shape.py`, `test_html_error_handling.py`, `test_html_executives_wiring.py`, `test_html_filter_primitives.py`, `test_html_investigation_wiring.py`, `test_html_render.py`, `test_html_server.py`, `test_html_sheet_nav.py`, `test_html_sql_executor.py`, `test_html_theme_integration.py`, `test_html_tree_fetcher.py`, `test_l2_derived.py`, `test_l2_descriptions.py`, `test_l2_fixtures_sync.py`, `test_l2_fuzz.py`, `test_l2_loader_theme.py`, `test_l2_loader.py`, `test_l2_pr_primitives.py`, `test_l2_primitives.py`, `test_l2_sasquatch_pr.py`, `test_l2_topology.py`, `test_l2_validate.py`, `test_l2_yaml_naming.py`, `test_layer1_query_sqlite.py`, `test_layer1_query.py`, `test_main_macros.py`, `test_models.py`, `test_persona.py`, `test_rich_text.py`, `test_theme_presets.py`, `test_tree_validator.py`, `test_tree.py`, `test_typing_smells.py`

### `tests/json/` (JSON-emit shape tests — layer 2a)

`test_app_info.py`, `test_bar_chart_axis_labels.py`, `test_cleanup.py`, `test_cli_json.py`, `test_cli_smoke.py`, `test_cross_sheet_drill_date_widening.py`, `test_dataset_contract.py`, `test_dataset_parameters.py`, `test_deploy.py`, `test_drill.py`, `test_emit_cross_reference_consistency.py`, `test_executives.py`, `test_investigation.py`, `test_kitchen_app.py`, `test_l1_dashboard.py`, `test_l2_flow_tracing_matrix.py`, `test_l2_flow_tracing.py`, `test_probe.py`, `test_screenshot_harness.py`, `test_table_column_headers.py`, `test_text_box_safety.py`

### `tests/{cli,docs,schema,l2}/` (also layer 2a)

`cli/test_db_fetcher.py`, `cli/test_serve_smoke.py`; `docs/test_cli_export_screenshot.py`, `docs/test_cli_smoke.py`, `docs/test_docs_links.py`, `docs/test_docs_persona_neutral.py`, `docs/test_handbook_diagrams.py`, `docs/test_handbook_vocabulary.py`; `schema/test_cli_smoke.py`, `schema/test_l2_schema_oracle.py`, `schema/test_l2_schema_sqlite.py`, `schema/test_l2_schema.py`, `schema/test_sql_dialect.py`; `l2/fuzz.py` (helper, not a test)

### `tests/data/` (mostly layer 2a, some layer 3c)

`test_auto_scenario_broad.py`, `test_auto_scenario.py`, `test_cli_smoke.py`, `test_l2_baseline_seed_sqlite.py`, `test_l2_baseline_seed.py`, `test_l2_pipeline.py`, `test_l2_runtime_assertions.py` (3c), `test_l2_seed_contract.py`, `test_locked_seeds.py`, `test_seed_persona_clean.py`, `test_sqlite_e2e_local_loop.py`

### `tests/audit/` (mix of layer 2a, 3d, 3e)

`test_cli_smoke.py` (2a), `test_dashboard_extract.py` (2a), `test_pdf_extract.py` (2a — PDF parsing), `test_pdf_matches_scenario.py` (3d), `test_pdf_sqlite.py` (3e), `test_persona_clean.py` (2a), `test_scenario_expectations.py` (2a), `test_sql.py` (2a), `test_template_input.py` (2a)

### `tests/js/` (layer 2c)

`test_bootstrap.py`, `test_error_toasts.py`, `test_filter_primitives.py`, `test_render_barchart.py`, `test_render_kpi.py`, `test_render_linechart.py`, `test_render_table.py`

### `tests/integration/` (layer 3a/3b — CLI scripts)

`verify_dataset_sql.py` (3a — has pytest twin in `tests/e2e/test_dataset_sql_smoke.py`), `verify_demo_apply.py` (3b — CLI only)

### `tests/e2e/` (layer 5/6/7/8)

**Layer 3a (smoke, recent addition):** `test_dataset_sql_smoke.py`
**Layer 5 (API):** `test_l1_deployed_resources.py`, `test_inv_deployed_resources.py`, `test_exec_deployed_resources.py`, `test_l1_dashboard_structure.py`, `test_inv_dashboard_structure.py`, `test_exec_dashboard_structure.py`
**Layer 6 (browser):** `test_l1_dashboard_renders.py`, `test_inv_dashboard_renders.py`, `test_exec_dashboard_renders.py`, `test_l1_sheet_visuals.py`, `test_inv_sheet_visuals.py`, `test_exec_sheet_visuals.py`, `test_l1_filters.py`, `test_inv_filters.py`, `test_inv_drilldown.py`, `test_l1_cross_sheet_drill_date_widening.py`, `test_audit_dashboard_agreement.py`, `test_l2ft_chains_dropdowns.py`, `test_l2ft_metadata_cascade.py`, `test_l2ft_rails_dropdowns.py`, `test_l2ft_templates_dropdowns.py`
**Layer 7 (App2):** `test_html2_executives.py`, `test_html2_executives_live.py`, `test_html2_money_trail.py`
**Layer 8 (harness):** `test_harness_end_to_end.py`, `test_harness_seed.py`, `test_harness_browser.py`, `test_harness_cleanup.py`, `test_harness_deploy.py`, `test_harness_failure_dump.py`, `test_harness_l1_assertions.py`, `test_harness_l2ft_assertions.py`
**Helpers (not tests):** `_harness_*.py`, `_kitchen_app.py`, `_layer1_query.py`, `tree_validator.py`, `conftest.py`

### `scripts/` (NOT tests, but adjacent CLI tooling)

`bake_sample_output.py`, `dump_top_queries.py`, `harness_manual_deploy.py`, `m2_6_verify.py`, `qs_substitution_probe.py`, `sweep_harness_orphans.py`. **Question for user (per §7.1):** are any of these test-shaped and worth converting?

### CI workflows

`.github/workflows/{ci,e2e,pages,release}.yml`

### Top-level scripts

`run_e2e.sh`, `m2_6_verify.sh`, `conftest.py` (root pyright sessionstart)

---

## 9. Next steps

1. **User reviews this doc.** Push back on anything wrong, fill in gaps, decide §7's open questions.
2. **Lock the layer table + variant axes.** Iterate inline.
3. **Y.2.gate.b — Design the runner** uses §2 + §3 + §7 decisions as the spec.
4. **Y.2.gate.c — Implement.**
5. **Y.2.gate.{d,e,f,h,i,j,k} — wire conventions, regression test, convert CLI verifiers, automate creds, parallelism, CI parity.**

When this doc is locked we delete the gaps section + ship it as the canonical reference (move to `src/quicksight_gen/docs/reference/test-layer-chain.md` per the original Y.2.gate.a parenthetical).
