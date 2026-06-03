# CB.15 — Collapse cells design

**Status:** in_progress (2026-06-03). Forced by today's CI OOM kill on the v13.0.0+ainflow run after the shared-container ci.yml step was added in parallel to (not consumed by) per-cell `setup_variant` testcontainers.

**Goal:** delete the custom variant runner's container-per-cell loop. Let pytest + pytest-xdist + session-scoped container fixtures do the work they were designed to do.

**Scope discipline:** this is a rip-the-bandaid pass. Out of scope: rewriting the CLI surface, changing marker tier names (CB.1–CB.5 are fine), moving cfg shape. In scope: deleting `_run_one_variant` (and friends), pushing container lifecycle into pytest fixtures, collapsing 13 pytest processes to 1.

---

## What the marks doc (cb_test_layers_update.md) missed

CB.1–CB.5 were correct on the *marks* layer. The doc set up `@tier` /
`@dialects` / `@l2` / `@needs` / `@writes` as typed source-of-truth,
with progressive `MIGRATED_TIERS` ratchet and conftest validation
rules. That layer is fine and stays.

The doc punted on the *scheduler* layer with one load-bearing
sentence:

> "The hand-maintained variant matrix — `expand_full()` still
> defines which cells exist."

That punt forced every band-aid since. The marks doc considered `-m`
filtering for tier+dialect dispatch, found that `-m "tier(app2)"`
doesn't understand mark arguments, and concluded "use `--tier=X
--dialect=Y` custom flags + per-cell pytest invocations." It skipped
the native pytest pattern — `@pytest.fixture(scope="session",
params=Dialect)` IS what the cell loop reinvents. The doc never
asked "why do we need separate pytest processes per cell?" because
the answer would have unwound every choice above it.

Same shape as [[feedback_invariants_in_types]] — we built a process
boundary to enforce "one container per dialect," when pytest's
fixture system already enforces "one fixture per scope" with proper
xdist worker sharing. The process boundary was the wrong tool.

### What stays (real, not band-aids)

- **Typed marks** (`@tier` / `@dialects` / `@l2` / `@needs` /
  `@writes`) and the typed enums in `tests/_marks.py`. Marks ARE
  the source of truth; they just feed pytest filters + fixtures
  directly instead of a custom runner dispatch.
- **Layer ordering** as `pytest -m unit && pytest -m db && ...`.
  Real sequencing; expressed in markers, not a runner.
- **Prefix discipline** — `cfg.deployment_name`, `cfg.db_table_prefix`,
  `cfg.prefixed()`. *The* isolation knob. Carries through QS resource
  IDs, DB table names, and the `Deployment` tag (cleanup gating).
- **cfg precedence** — env override → yaml → loud-fail. Real and stays.
- **hotchkiss.io DDNS forward** for QS-reaching-dev-box. Real cfg
  routing; lives in the qs.yaml flavor.
- **Drift detection / timings** — becomes `pytest --report-log` JSON
  with the same "+50% = ⚠" shape per [[feedback_timings_as_smell]].
- **Fuzz seed pool** — becomes a parametrize axis on fuzz tests.
- **`./run_tests.sh up_to=<layer>` operator CLI** — thin alias preserved
  per [[feedback_common_path_default]].
- **The composition rules table + `MIGRATED_TIERS` ratchet** — rules
  hold regardless of scheduler; the ratchet just stays locked at all
  five tiers.

### What dies (now that shared container + prefix-per-worker is the design)

The shared-container shift makes this cut sharper than the
container-per-worker draft would have: the *only* reason the cell
existed as a runtime concept was to manage per-cell containers.
Take container management out of the cell, the cell itself has no
job to do.

| Band-aid | Why it dies |
|---|---|
| `_run_one_variant` (cell loop) | No per-cell containers to provision; nothing to orchestrate |
| `setup_variant` | Session fixture detects `RECON_GEN_DEMO_DATABASE_URL_*` env (CI) OR spins testcontainers (local) |
| `teardown_variant` | pytest finalizer |
| `asyncio.gather` over cells | pytest-xdist `-n auto` |
| `VariantSpec` / `expand_full()` / cell enum | Shrinks to `@pytest.fixture(params=...)` over dialect × scenario |
| Per-cell cfg materialization (`runs/<id>/<cell>/cfg/`) | `worker_cfg` fixture suffixes the base cfg by worker_id |
| `RECON_GEN_RUNNER_CI` env | Session fixture detects URL env directly |
| `RECON_GEN_DB_READ_ONLY` env | Per-worker prefix = no contention; CC [#226](../../PLAN.md) resolves |
| `@writes()` as a *fixture switch* | Becomes pure documentation marker; per-worker prefix already isolates writes |
| Custom coverage merge in `runner.py` | `coverage combine` over xdist's `.coverage.*` files |
| Custom artifact paths (`runs/<id>/<variant>/<layer>/`) | JUnit XML + `tmp_path_factory` |
| `--keep-on-failure` flag | Just don't tear down on failure (pytest already supports `--pdb`-style patterns) |
| Per-cell `qs.yaml` siblings (the CB.11.b cfg pair) | Shared container = one cfg per dialect with hotchkiss-routable URL; the local/qs split collapses |
| ci.yml "Materialize per-dialect runner cfgs" step | One cfg per dialect, materialized once, worker-id suffix at fixture time |
| `--tier=X --dialect=Y --l2=Z` custom pytest flags | Marks expanded to `-m`-friendly distinct values, OR kept as flags but consumed entirely inside the existing mark filter |
| BX [#184](../../PLAN.md) "Coverage merge doesn't render" | Stop fighting pytest-cov; vanilla flow |
| CB [#199](../../PLAN.md) "DuckDB xdist intra-cell file-lock" | No cell; per-worker tmp DuckDB file |
| CB [#200](../../PLAN.md) ":memory: per worker" | The default fixture shape |
| Today's shared-container ci.yml step paradox | Resolves naturally — no per-cell alternative |

The point of the shared-container shift wasn't to preserve cells —
it was to recognize that **the prefix was always the real isolation**
and the container was the wrong tool to ALSO use for isolation.
Accept that, and cells collapse.

---

## Today's shape (the thing we're killing)

```
./run_tests.sh up_to=db
  └─ runner.py::main()
      ├─ _run_unit_prelude()                              # one pytest call
      └─ asyncio.gather(_run_one_variant() for each cell) # 12 pytest calls in parallel
           ├─ setup_variant(spec) → testcontainers PG / Oracle / DuckDB
           ├─ pytest -m db (per cell, with cell-specific env)
           └─ teardown_variant(handle)
```

**Cells:** scenario (sp/sq/fuzz) × dialect (pg/or/du) × target (lo/aw) = 13 (sl×aw skipped).

**What each cell costs:**
- `lo` PG cell: ~500 MB resident (testcontainers postgres:17-alpine)
- `lo` Oracle cell: ~2 GB resident (recon-gen/oracle-19c:local)
- `lo` DuckDB cell: ~10 MB (file)
- `aw` cells: 0 — point at operator's external infra

**What CI also spins, in parallel:**
- `ci-shared-pg` (postgres:17-alpine, ~500 MB) — for aw cells, via ci.yml's "Start shared PG + Oracle" step
- `ci-shared-oracle` (recon-gen/oracle-19c:local, ~2 GB) — same

**The bug:** every `lo` cell starts its own testcontainers PG/Oracle. The shared pair is wasted on `lo` cells; only `aw` cells reach them via the hotchkiss.io route. So at peak:

- 4 PG containers (1 shared + 3 lo cells) = ~2 GB
- 4 Oracle containers (1 shared + 3 lo cells) = ~8 GB
- pytest workers per cell, xdist-parallel, holding open DB conns
- Total: well past the runner's RAM ceiling → OOM → orphan-process avalanche

The OOM kill at 22:16:37 today (job 79403992240) had no in-cell stdout in the log — the runner never got a chance to teardown. ~hundreds of pytest/python orphans terminated by GHA's job-kill at 22:19–22:20.

---

## What pytest + ecosystem already provides (and we ignore)

### 1. Session-scoped container fixtures
One fixture at `tests/e2e/db/conftest.py`:

```python
@pytest.fixture(scope="session")
def pg_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:17-alpine") as pg:
        yield pg
```

Lifecycle is pytest's job: instantiate lazily on first test that asks, tear down at session end. **One container per pytest process**, not one per test or per cell.

### 2. `pytest-xdist` work distribution
- **`--dist=worksteal`** — default behavior we want. Tests distributed across workers as they finish; idle workers steal from busy ones. No pinning, no manual grouping.
- **`-n auto`** — worker count scales to cores. Today we hardcode parallelism via cell count.

**Explicitly NOT `--dist=loadgroup`.** That mode pins tests to a worker via `@pytest.mark.xdist_group("pg")`. Sounds clever — "all PG tests share one container" — but the historic failure mode is that one group ends up huge and serializes the run while other workers idle. We've been bitten before; don't reach for it.

### 3. Prefix discipline IS the isolation — containers stay shared
The repo already namespaces every QS + DB resource by `cfg.deployment_name` (QS kebab-case prefix) + `cfg.db_table_prefix` (DB snake_case prefix). `common/config.py:213,224` are the canonical fields; `cfg.prefixed("foo")` everywhere produces `<deployment_name>-foo` for QS resources, and SQL emit threads `db_table_prefix` into every `<prefix>_transactions` / `<prefix>_daily_balances` reference.

This means workers don't need their own containers. They need their own *prefix*. One PG container + one Oracle container per pytest session, with each worker writing to its own prefix-scoped table set inside that shared DB. No table collision, no QS resource collision.

Concretely:
- `pg_container` session-scoped fixture (singleton per pytest run, not per worker)
- `worker_cfg` fixture that takes the base cfg + worker id and returns a cfg with `deployment_name = f"{base}-w{worker_id}"` and `db_table_prefix = f"{base}_w{worker_id}"`
- Tests bind `worker_cfg` (or whatever fixture name fits); shared schema apply per (worker × dialect) on first test that touches it

Memory math: **1 PG + 1 Oracle per pytest session, regardless of `-n`.** `~2.5 GB` total. CI box has 60 GB headroom; laptop fits comfortably. This is what the existing "Start shared PG + Oracle for aw-target cells" ci.yml step set up — it was right; the bug was per-cell `setup_variant` *also* spinning containers in parallel rather than consuming the shared pair.

### 3. `pytest --forked` (per-test subprocess isolation)
The custom runner forks per-cell because some tests can't share process state. pytest can fork per-test for that subset — `pytestmark = pytest.mark.forked` on the file, done. Most tests don't need it.

### 4. Parametrize as the matrix axis, not cells
```python
@pytest.fixture(scope="session", params=["sp", "sq", "fuzz"])
def scenario(request) -> str: ...

@pytest.fixture(scope="session", params=["pg", "or", "du"])
def dialect(request) -> str: ...
```

One pytest process iterates the full matrix. Containers are still session-scoped per-(dialect) tuple via pytest's fixture caching (the `params=` cross-product caches per parameter combination). 13 cells collapses to 1 pytest run with parametrize generating 12 test instances.

### 5. `pytest-cov` combine
We built our own coverage merge in `runner.py`. `pytest-cov` with `--cov-report=` + `coverage combine` handles xdist + parametrize natively. The combine job in PLAN backlog ([#184](../../PLAN.md)) is solving a problem we created.

### 6. `pytest --junitxml` + per-test stash
The runner writes `cmd.json`/`stdout.log`/`stderr.log`/`timings.json` per cell. JUnit XML + `pytest-reportlog` + `tmp_path_factory` give the same triage shape without bespoke wiring. Failure parity ([feedback_test_layer_chain](../../.claude/projects/-Users-chotchki-workspace-quicksight/memory/feedback_test_layer_chain.md)) holds because JUnit is the CI artifact standard.

### 7. `pytest -m` marker filtering = layered ordering
We have `Tier.UNIT/APP2/DB/QS_API/QS_BROWSER` markers (CB.1–CB.5). "Layered execution" = sequential pytest invocations with `-m`:

```bash
pytest -m unit && pytest -m app2 && pytest -m db && pytest -m qs_api && pytest -m qs_browser
```

That's 5 commands, not 5 cells × 13 dispatches.

### 8. `pytest_addoption` + `request.config.getoption`
`cfg` becomes a fixture parameter, not an env-var threaded through subprocess invocations. `--cfg=run/config.postgres.yaml` lands on the pytest CLI; the fixture parses it once per session.

---

## What we built that genuinely had no pytest analogue

- **Layered ordering across separate pytest processes.** Only matters because we artificially run separate processes. If layers are markers in one process, ordering is `pytest -m unit -x` then `pytest -m db -x`. Vanilla.
- **CI vs local cfg parity.** Real, but solved by env-var precedence + fixture, not a runner. The fixture reads `RECON_GEN_CONFIG` env override → cfg yaml → loud-fail. Same precedence, no runner.
- **Variant matrix product.** pytest does this with parametrize + indirect fixtures. No runner needed.
- **Fuzz-seed pinning + drift detection.** `--fuzz-seed=N` via `pytest_addoption`; per-run timings via `pytest --report-log` JSON. Vanilla. The timings-as-smell memory ([feedback_timings_as_smell](../../.claude/projects/-Users-chotchki-workspace-quicksight/memory/feedback_timings_as_smell.md)) still holds; the stable-artifact shape just becomes the report-log JSON.
- **`up_to=<layer>` operator-facing CLI.** Worth keeping. Becomes a thin alias for `pytest -m "$markers" -n auto`.

---

## Proposed shape

```
./run_tests.sh up_to=db                 # thin alias preserved
  └─ pytest tests/ -m "unit or app2 or db" --dist=worksteal -n auto
       │
       ├─ session-scoped containers (singleton per pytest run, NOT per worker):
       │    pg_container       : PostgresContainer    (one PG total)
       │    oracle_container   : Oracle19cContainer   (one Oracle total)
       │    duckdb_workdir     : Path                 (tmp, worker-local file)
       │
       ├─ per-worker cfg (the isolation knob):
       │    worker_cfg = base_cfg.with_prefix(f"-w{worker_id}")
       │      → deployment_name = "recon-test-w0", "recon-test-w1", ...
       │      → db_table_prefix = "recon_test_w0", "recon_test_w1", ...
       │    one schema apply per (worker × dialect), cached for session
       │
       ├─ xdist worker pool (-n auto = cores - 2):
       │    workers share the containers, isolate via prefix
       │    no xdist_group pinning — load-balanced via worksteal
       │
       └─ parametrize axes (indirect fixtures, not a runner cell-loop):
            dialect ∈ {pg, or, du}        (fixture selects the container)
            scenario ∈ {sp, sq, fuzz}     (cfg fixture parameter)
            target ∈ {lo, aw}             (URL fixture parameter; aw → hotchkiss.io)
```

Memory math: **1 PG + 1 Oracle TOTAL per pytest run** (~2.5 GB), independent of `-n`. CI box has 60 GB. Laptop fits easily. Container memory cost is no longer a `-n` constraint — connection pool size is.

**ci.yml step shape** — the existing "Start shared PG + Oracle" step is actually what we want. It was wrong only because per-cell `setup_variant` ALSO spun containers in parallel. Under the new shape that side spin-up goes away, so:

```yaml
- name: Start shared PG + Oracle
  run: |
    docker run -d --name ci-shared-pg -p 5432:5432 ... postgres:17-alpine
    docker run -d --name ci-shared-oracle -p 1521:1521 ... recon-gen/oracle-19c:local
    # wait for both to be ready

- name: Run layered tests
  run: pytest tests/ -m "${{ matrix.layer }}" -n auto --junitxml=runs/junit.xml
  env:
    RECON_GEN_DEMO_DATABASE_URL_PG: postgres://...:5432/postgres
    RECON_GEN_DEMO_DATABASE_URL_OR: oracle://...:1521/...

- name: Stop shared containers
  if: always()
  run: docker rm -f ci-shared-pg ci-shared-oracle
```

Session fixtures detect the `RECON_GEN_DEMO_DATABASE_URL_*` env vars and skip testcontainers spin — they connect to the provisioned URL instead. Locally (no env set), session fixtures spin testcontainers; in CI, they consume the workflow-provisioned pair. Either way, **one PG and one Oracle for the whole pytest run**, prefix discipline isolating workers inside it.

The `aw`-style "reach the dev machine via hotchkiss.io" path stays a cfg-routing concern handled by the existing `RECON_GEN_QS_CONFIG` separation (qs.yaml siblings) — that's about who can reach the DB, not how many of them exist.

---

## Tradeoffs (the honest ones)

### Single-pytest-process means a hard hang stalls everything
Today's per-cell isolation lets one cell hang without affecting the other 12. Single pytest with xdist still parallelizes via workers, but a hang in one test holds its worker. Mitigations:

- **`pytest-timeout`** — already in the ecosystem, set per-test budget; killed tests don't propagate. We aren't using it today; we should be.
- **`--forked`** for the small set of tests that historically hang (probably the qs_browser tier per [feedback_test_layer_chain](../../.claude/projects/-Users-chotchki-workspace-quicksight/memory/feedback_test_layer_chain.md)).

### QS deploy `deployment_name` collisions — solved by the existing prefix discipline
This isn't really an open question: `deployment_name` was always meant to be the isolation knob. The `worker_cfg` fixture suffixes it (`f"{base}-w{worker_id}"`) and the existing `cfg.prefixed("foo") → "<deployment_name>-foo"` plumbing carries through. Two parallel workers running qs_api tests get `recon-test-w0-l1-dashboard` and `recon-test-w1-l1-dashboard` — distinct resources, distinct delete-then-create cycles, no collision.

`db_table_prefix` gets the same treatment for the table side: `recon_test_w0_transactions` vs `recon_test_w1_transactions`. Per-worker schema apply runs once on first touch, cached for the rest of the session.

The only spot to watch is QS *cleanup* — `cleanup` gates on the `Deployment` tag, so the per-worker suffixes need to come through that tag too. They do, since `Deployment = cfg.deployment_name`.

### Fuzz-seed pool shrinks per-run
Today the runner can dispatch N fuzz seeds as N separate cells, each in isolation. Under parametrize, fuzz seeds become a `@pytest.mark.parametrize("seed", [...])` axis on the fuzz tests only — same wall-clock, same sampling, just inside one pytest. No real loss.

### Coverage shape changes
`coverage combine` over xdist's per-worker `.coverage.*` files is standard, but the runner today merges across cells too. If we keep cell-level merge as a CI artifact step, vanilla `coverage combine` covers it. The BX backlog item [#184](../../PLAN.md) about "merged coverage doesn't render in PR comments" likely *improves* under vanilla pytest-cov because we stop fighting the tool.

### `RECON_GEN_RUNNER_CI=1` mode is no longer needed
Today the runner has a CI-mode escape hatch that skips Docker spin-up when the workflow YAML pre-provisions. Under session fixtures, the same effect is "session fixture detects `RECON_GEN_DEMO_DATABASE_URL` is preset, skips testcontainers spin, yields a thin URL handle." Cleaner.

---

## What's already shipped (the discovery that shrinks this whole plan)

Most of CB.15's infrastructure was built incrementally by earlier
phases and is already live. We aren't architecting from scratch —
we're connecting wires already in the box.

- **`isolated_cfg` fixture** at `tests/e2e/_isolation.py:117` —
  module-scoped, per-(file, xdist worker). Already does
  `dataclasses.replace(cfg, db_table_prefix=...,
  deployment_name=...)` with a per-worker suffix (line 110-114).
  This IS the `worker_cfg` pattern CB.15 needs. CB.7-followup
  unwind (2026-06-02) locked the per-(file, worker) shape;
  cross-tier sharing dropped — tiers communicate via JSON artifacts
  on disk.
- **`IsolationScope` typed enum** + `@isolation_producer` /
  `@isolation_consumer` decorators in `tests/_marks.py:157`.
- **`isolated_cfg` teardown** — best-effort DROP of the worker's
  prefixed schema so repeated runs don't accumulate suffix debris.
- **Typed marks** (CB.1–5) — `@tier` / `@dialects` / `@l2` / `@needs`
  fully locked across all five tiers; `MIGRATED_TIERS` ratchet
  complete.
- **Prefix discipline** — `cfg.deployment_name` / `cfg.db_table_prefix`
  / `cfg.prefixed()` shipped in production and threaded through QS
  resources, DB tables, and the `Deployment` cleanup tag.
- **testcontainers wired** — `tests/audit/test_data_apply_populates_config.py:76`
  and `tests/e2e/test_studio_deploy_browser.py` already
  `PostgresContainer("postgres:17-alpine")`-spin and yield. The
  pattern exists; it just needs to graduate to session-scoped + env-
  detecting.
- **Shared CI containers** — `ci-shared-pg` + `ci-shared-oracle`
  steps in ci.yml today. The bug is `setup_variant` also spawns
  per-cell containers in parallel rather than consuming the shared
  pair via env URL.

**What's actually missing** is much smaller than the original draft
implied:

1. Session-scoped `pg_container` + `oracle_container` fixtures
   that detect `RECON_GEN_DEMO_DATABASE_URL_*` env (CI) → return
   the URL, else spin testcontainers (local). ~30 lines.
2. Top-level `cfg` fixture starts from the container URL instead of
   `connect_demo_db` directly. ~20 lines.
3. Delete `_run_one_variant`, `setup_variant`, `teardown_variant`,
   the cell loop, `RECON_GEN_RUNNER_CI`. ~600–1000 lines deleted.
4. Rewrite `run_tests.sh` as `pytest -m "..." -n auto`. ~300 → ~50.
5. Delete custom coverage merge; `coverage combine` does it.

That's one workday of editing, not weeks. The CB.7 + CB.1–5 work
that I treated as background context is actually the load-bearing
infrastructure — it shipped CB.15's hard parts already.

## Migration plan (sequential, each step pushable to CI green)

- [ ] **CB.17.a — Wire `pg_container` + `oracle_container` session fixtures.**
      Promote the existing testcontainers patterns at
      `tests/audit/test_data_apply_populates_config.py:76` (and the
      e2e equivalent) into session-scoped fixtures at top-level
      `tests/conftest.py`. Detect `RECON_GEN_DEMO_DATABASE_URL_PG`
      / `_OR` env → return that URL (CI / pre-provisioned path).
      Else spin testcontainers (local path). Verify lifecycle under
      `pytest -n 2` against a single db-tier test.

- [ ] **CB.17.b — Top-level `cfg` fixture sources from container fixtures.**
      Today's `cfg` reads `RECON_GEN_CONFIG` env. Add an upstream
      bridge: the container fixture's URL becomes the source if no
      env override. `isolated_cfg` continues downstream unchanged —
      same suffixing, same teardown.

- [ ] **CB.17.c — Confirm `isolated_cfg` worker-suffix covers QS resources.**
      `isolated_cfg` already touches `deployment_name`; verify two
      parallel qs_api workers against the operator's AWS account
      produce non-colliding resource graphs and that
      `cleanup --execute` honors per-worker scoping via the
      `Deployment` tag.

- [ ] **CB.17.d — Delete the cell loop.**
      `_run_one_variant`, `setup_variant`, `teardown_variant`,
      `asyncio.gather`-over-cells, `RECON_GEN_RUNNER_CI`,
      per-cell cfg materialization, `--keep-on-failure`,
      `RECON_GEN_DB_READ_ONLY`. `runner.py` shrinks to: argparse →
      marker selection → one pytest invocation → exit code.

- [ ] **CB.17.e — Rewrite `./run_tests.sh` as a thin pytest alias.**
      Preserves the operator CLI per
      [[feedback_common_path_default]]: no-arg default = full
      layered chain; `up_to=<layer>` shells
      `pytest -m "<layer-expr>" -n auto`. ~50 lines down from ~300.

- [ ] **CB.17.f — Collapse ci.yml.**
      Keep the "Start shared PG + Oracle" step (it was right; just
      wasn't consumed). Drop the "Materialize per-dialect runner
      cfgs" step for `lo` cells — only the `aw` qs.yaml siblings
      remain. Add `RECON_GEN_DEMO_DATABASE_URL_PG` / `_OR` env on
      the test step pointing at the shared containers. One pytest
      invocation per layer.

- [ ] **CB.17.g — Delete custom coverage merge + bespoke artifact paths.**
      `pytest-cov` + `coverage combine` over xdist's `.coverage.*`
      files. JUnit XML + `tmp_path_factory` for artifacts. Resolves
      BX [#184](../../PLAN.md) "Coverage merge doesn't render."

- [ ] **CB.17.h — Re-verify on the WSL2 self-hosted runner.**
      The OOM that triggered CB.15 should be gone. Capture peak RSS
      in a CI artifact per [[feedback_timings_as_smell]].

---

## Things this design opens up (mostly already opened by CB.7's isolated_cfg)

- **CC [#226](../../PLAN.md) "Re-examine `RECON_GEN_DB_READ_ONLY` after cell removal"** — that env existed to defeat per-cell DuckDB lock issues. Under `isolated_cfg` + per-(file, worker) DuckDB path, the lock isn't shared across workers and the env isn't needed. Delete in CB.17.d.
- **CB [#199](../../PLAN.md) "DuckDB pytest-xdist intra-cell file-lock contention"** — same root, resolves with `isolated_cfg`'s per-worker scoping.
- **CB [#200](../../PLAN.md) "db-tier fixtures to :memory: per worker"** — `isolated_cfg` already steers per-worker; switching to `:memory:` is a tmp_path → `:memory:` swap inside the fixture.
- **BX [#184](../../PLAN.md) coverage merge doesn't render in PR comments** — stop fighting pytest-cov.
- **CB.16 (typing honesty)** lands independently — the fixture migration doesn't touch `connect_demo_db`'s return type.

## Things to confirm before cutting

- [ ] **The `scope="session"` vs xdist contract.** pytest-xdist's "session" is per-worker, not per-pytest-run. For `pg_container` to be a true singleton, we need a file-lock pattern (`FileLock` on a shared tmp file, first worker spins, rest read URL). Alternative: rely on the CI env-URL injection so the container is workflow-owned, and locally let testcontainers per-worker spawn (laptops have memory for 2–4 PGs). Pick before CB.17.a.
- [ ] PG `max_connections=200` defensive pin in testcontainer args (default 100 is tight under heavy parametrize + pytest-cov collection).
- [ ] `coverage combine` over xdist's `.coverage.*` files renders cleanly in the existing GH PR-comment flow (BX [#184](../../PLAN.md) is the standing complaint).
- [ ] `isolated_cfg`'s per-worker suffix actually carries the `Deployment` tag for cleanup gating (verify by reading the cleanup gate code; should already work since `Deployment` tag = `cfg.deployment_name`).
- [ ] `pytest-timeout` per-test budget covers the qs_browser tier's worst-case hangs without false-positives.
