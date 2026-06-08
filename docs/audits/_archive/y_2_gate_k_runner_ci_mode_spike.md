# Y.2.gate.k.1 + k.6 spike — runner CI-mode

**Status:** spike landed (v8.8.0a14+); CI workflows not yet rewired.
**Date:** 2026-05-09

## Why this spike

`Y.2.gate.k.1` and `Y.2.gate.k.6` both want CI workflows to invoke
`./run_tests.sh` directly so the runner is the single canonical entry
point. Discovered blocker (this spike): the runner's `setup_variant`
spins its own Docker container for `lo` targets, which conflicts with
GHA `services:` blocks (port collisions, double cost, no shared
health-check). Without a detection mode for "DB already provisioned
externally," the rewire couldn't move forward.

## What landed

- **`QS_GEN_RUNNER_CI`** typed EnvVar (`common/env_keys.py`) — bool
  opt-in. When set, `setup_variant` skips Docker spin-up for `lo`
  targets and assumes `QS_GEN_DEMO_DATABASE_URL` points at the
  pre-provisioned DB.
- **`setup_variant` CI-mode branch** — checks `QS_GEN_RUNNER_CI`
  before falling through to the testcontainers spin-up paths.
  Loud-fails via `EnvVarRequired` if the URL isn't also set.
- **`target=aw` unchanged** — the early-return path runs first, so
  CI mode is never consulted for aw cells.
- **4 unit tests** in `tests/unit/test_runner_skeleton.py` lock the
  contract: `pg/lo + url`, `or/lo + url`, `pg/lo + missing url →
  loud fail`, `aw + ci-mode → unchanged passthrough`.

## What did NOT land (intentionally)

CI workflow rewire is the obvious next step but stays out of this
spike for two reasons:

1. **Migration mapping is non-trivial.** `ci.yml::integration-pg`
   currently does:
   - schema apply → covered by runner's `seed_variant`
   - data apply + refresh → covered by runner's `seed_variant`
   - `pytest test_dataset_sql_smoke.py` → covered by runner's `db`
     layer
   - `pytest test_demo_apply_row_counts.py` → **NOT** covered by any
     runner layer today
   - `audit apply --execute` + `audit verify` → **NOT** covered by
     any runner layer today

   Two gaps. Either extend the runner's `db` layer to absorb them,
   or keep the workflow as a partial wrapper (workflow drives the
   runner for variant setup + `db` layer pytest, then runs the
   row-count + audit steps separately).

2. **Coverage is on the path.** The current `coverage` job
   downloads `.coverage.<pyversion>` artifacts the `test` job
   produced, runs `coverage combine`, and posts a Step Summary.
   The runner's per-cell pytest doesn't currently emit a
   `.coverage.*` file (no `--cov` flag in dispatch). Migrating
   `test` → runner means either teaching the runner to honor
   `--cov` flags or accepting a coverage gap during the migration.

## Migration plan (to land in follow-up commits)

**Phase 1 — proof point (smallest possible wedge).**
Wire `ci.yml::integration-pg` to use the runner for the bits it
already covers, keeping the row-count + audit steps as separate
workflow steps. Concretely:

```yaml
# Replace the manual schema/data/json apply chain with:
- name: Run db layer via runner (skip Docker spin-up — service container already up)
  env:
    QS_GEN_RUNNER_CI: "1"
    QS_GEN_DEMO_DATABASE_URL: "postgresql://postgres:ci_pg_password@localhost:5432/postgres"
    QS_GEN_CONFIG: /tmp/ci-pg.yaml
  run: ./run_tests.sh up_to=db --dialects=pg --targets=lo

# Then keep these workflow-level steps (not runner-driven yet):
- name: Verify Postgres row counts
  ...
- name: Render + verify the audit PDF against Postgres
  ...
```

This is a partial wrapper, not a full thin wrapper — but it proves
the path and exercises CI-mode end-to-end on a real GHA runner.

**Phase 2 — runner absorbs the row-count + audit steps.**
Either:
- (a) Add a runner sub-target like `up_to=db --include-row-counts
  --include-audit-pdf`, or
- (b) Promote `test_demo_apply_row_counts.py` + an audit-PDF
  pytest test into the `db` layer's default pytest set.

Option (b) is the simpler path — these are e2e pytest files
already, just not currently invoked by the `db` layer dispatch.
The runner's `dispatch_layer` for `db` currently runs only
`tests/e2e/test_dataset_sql_smoke.py`. Extending to also run
`test_demo_apply_row_counts.py` is a 1-line change. Audit-PDF
needs to be turned into a pytest first.

**Phase 3 — coverage pass-through.**
Teach the runner to emit `.coverage.<variant>` artifacts when a
flag is set (default off — local runs don't want coverage
overhead). CI workflows set the flag; the existing `coverage`
job's combine logic stays unchanged.

**Phase 4 — full thin-wrapper for ci.yml + e2e.yml.**
Once Phases 1–3 land, both workflows become 5-10 lines of YAML
that boil down to:
1. Check out + install deps + bring up service container.
2. Set `QS_GEN_RUNNER_CI=1` + URL.
3. `./run_tests.sh up_to=<layer> --dialects=<...> --targets=<...>`
4. Upload artifacts.

Coverage / top-queries / failure screenshots all flow through the
runner's existing per-cell artifact paths
(`runs/<run-id>/<variant>/...`).

## Why the spike is shippable as-is

The unit tests prove the dispatch contract; pyright-strict is
clean; no runtime regressions for non-CI-mode invocations (the
`if QS_GEN_RUNNER_CI.get_or_none()` check is a single env-var read
that returns None when unset, falling through to the existing
Docker spin-up path).

The follow-up phases are tractable because the spike unblocks
them: workflows can now set `QS_GEN_RUNNER_CI=1` and call the
runner without double-provisioning. Whether they actually do is a
separate decision per workflow.
