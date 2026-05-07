# Y.2.gate.b.0 — Runner implementation language spike

**Status:** DRAFT for user review (2026-05-07).
**Locks:** audit `§7.3` once user agrees.
**Scope:** decide the implementation language / substrate for the Y.2.gate runner — the CLI surface that runs the layered test chain (`./run_tests.sh up_to=<layer>` or equivalent).

## 1. Why this spike exists

An earlier draft of audit `§7.3` LOCKED "shell script (`./run_tests.sh`)" via discussion alone — no real comparison against alternatives. User flagged the lock as vibes-not-spike: process management, parallelism, and "I don't want a huge second code base" make this a codebase-shape decision that needs an actual evaluation. `§7.3` is now UNLOCKED until this spike returns.

## 2. Constraint set

The runner needs to:

1. **Process management** — spawn subprocesses (xdist children, Docker containers, AWS CLI, pytest); per-test timeout; cleanup-on-failure (the `--keep-on-failure` flag from `Y.2.gate.f.5`); trap on SIGINT.
2. **Parallelism** —
   - cross-variant fan-out at layer 3 (PG + Oracle Docker simultaneously, audit §7.5);
   - App2 + QS targets in parallel at the merged 6/7 layer with fast-fail (audit §7.10/§7.12);
   - xdist within-layer-within-variant (existing `./run_e2e.sh --parallel` knob);
   - fuzz-seed × dialect product when sample > 1 (audit §7.11).
3. **JSON I/O** — `runs/<run-id>/timings.json` + `hashes.json` write-and-diff vs prior run. The diff loop is the runner's main control flow (audit §7.9).
4. **Dependency probe (no state file, LOCKED §7.12)** — `aws sts get-caller-identity`, `psycopg.connect`, `docker ps` before invoking AWS/Docker-touching layers.
5. **Layer dependency graph** — layers strictly sequential (chain semantics, LOCKED `Y.2.gate.b.9`); cross-variant within a layer is parallel; cross-layer is never parallel.
6. **Variant selection / config** — `--variants=full | pg | oracle`, `--fuzz-seeds=N`, `--only=<test-id>`, `--skip-cheap`, `--up-to=<layer>` flags.
7. **Per-run output isolation** — `runs/<run-id>/...` directory at session start; all artifacts route under it (LOCKED `Y.2.gate.b.4`).

**Anti-goal (user-flagged):** a "huge second code base." If we're rolling our own state machine / dependency graph / parallelism engine, we've gone wrong. Lean on existing tools where they fit; small bespoke code where they don't.

## 3. Candidates

### 3.1 Pure bash + jq + GNU parallel

| Constraint | Fit |
|---|---|
| Process mgmt | Trap-on-EXIT works; killing parallel children on SIGINT requires careful jobs/wait juggling |
| Parallelism | `parallel` + background jobs work, but per-job exit codes need `wait $! ; echo $?` plumbing |
| JSON I/O | jq does it, but verbosely. Diff loop = tedious nested jq invocations |
| Dependency probe | Easy — `aws sts get-caller-identity \|\| exit 1` |
| Layer graph | If/exit chain works for sequential layers; parallel-within-layer needs job array bookkeeping |
| Variant selection | `case $variant in pg) ...; esac` works; gets ugly fast |
| Codebase impact | One file, ~500-800 LOC of `set -euo pipefail` carefully |

**Verdict:** can be made to work, but the JSON drift-diff loop dominates the orchestration logic and is bash-painful (jq nested filters, escape hells, no real type-checking). Failure-handling across parallel jobs is the second pain point. Not recommended as the primary substrate.

### 3.2 Pure Python (asyncio + subprocess + stdlib json)

| Constraint | Fit |
|---|---|
| Process mgmt | `asyncio.create_subprocess_exec()` + `asyncio.wait_for()` for timeouts |
| Parallelism | `asyncio.gather(*per_variant_tasks)` for fan-out; clean and typed |
| JSON I/O | stdlib `json` for read/write; dict diffing is straightforward Python |
| Dependency probe | `subprocess.run(["aws", "sts", "get-caller-identity"], check=True)` etc. |
| Layer graph | Hard-coded in Python: sequential `await layer1(); await layer2(); ...` |
| Variant selection | `argparse` or `click`; clean dataclass config |
| Codebase impact | ~600-1000 LOC for a "real" orchestrator that reinvents what pytest already does |

**Verdict:** clean implementation, but reinvents pytest's session lifecycle / fixture scoping / xdist / marker selection. Risk of sliding into the "huge second code base" trap is real here.

### 3.3 `just` (Rust task runner)

| Constraint | Fit |
|---|---|
| Process mgmt | `just` invokes shell; same shape as bash + recipes |
| Parallelism | `just --parallel layer-3a-pg layer-3a-oracle layer-3a-sqlite` works for cross-variant; within-recipe = bash |
| JSON I/O | None native; calls Python helper for diff |
| Dependency probe | `dep-probe` recipe calls Python or shell helper |
| Layer graph | Recipe `:` dependencies express the chain naturally |
| Variant selection | Recipe parameters: `up-to layer variant: ...` |
| Codebase impact | ~150 LOC justfile + ~300 LOC Python helpers for the diff/probe loop = split-brain |

**Verdict:** attractive (Rust binary, declarative recipes, user prefers Rust tools) BUT the JSON drift-diff loop is dominant orchestration logic that lives in Python helpers either way. Once we're writing Python helpers for the diff loop, the justfile becomes thin glue and most of the value evaporates. The split-brain (justfile + Python helpers) is its own maintenance cost.

### 3.4 Pytest-as-orchestrator + thin Python wrapper ⭐

| Constraint | Fit |
|---|---|
| Process mgmt | Pytest fixtures already do this (DB setup/teardown, conftest scoping, `--timeout`) |
| Parallelism | Cross-variant: wrapper dispatches per-variant pytest subprocess via `asyncio.gather`. Within-variant: pytest-xdist (existing) |
| JSON I/O | `pytest_runtest_protocol` hook writes per-test timing JSON; wrapper reads + diffs |
| Dependency probe | Wrapper's pre-flight (~50 LOC) before invoking pytest |
| Layer graph | Wrapper hard-codes sequential dispatch; pytest selects layer via marker (`-m "layer3a"`) |
| Variant selection | Wrapper passes per-variant env / marker to each pytest invocation |
| Codebase impact | ~300-500 LOC wrapper + small conftest changes; reuses existing pytest infra |

**Verdict:** maximizes reuse of what's already in the project. Pytest already runs in this codebase, has xdist, fixtures, markers, conftest hooks, JSON reporters. The wrapper does **only** the things pytest doesn't natively do: cross-variant subprocess dispatch, run-id creation, drift-diff against prior runs, dependency probe.

**Concrete shape:**

- `./run_tests.sh` — tiny bash wrapper that `exec`s the Python orchestrator. Bash exists only for the shell-script CLI feel + minimal env probing.
- `quicksight_gen/_dev/runner.py` — Python orchestrator (private `_dev/` package; not customer surface). Roughly:
  - `main()` — argparse, run-id creation, dispatch loop
  - `probe_dependencies(layer)` — checks `aws sts get-caller-identity`, `psycopg.connect`, `docker ps` per the layer's needs
  - `dispatch_layer(layer, variants)` — `asyncio.gather` over per-variant `subprocess` invocations of `pytest -m <layer> --variant=<v>`
  - `capture_run(run_id)` — collects `runs/<run-id>/timings.json` + `hashes.json` from each pytest's JSON output
  - `diff_against_prior(run_id)` — finds most-recent prior run for the same SHA (else most-recent overall); reports `step took X (was Y, ±Z%)` with ±50% ⚠
- `tests/conftest.py` — adds layer markers, per-variant fixture parametrization, JSON-output hooks. Most fixture infra already exists.

### 3.5 `nox` / `tox` / `mise`

| Constraint | Fit |
|---|---|
| Process mgmt | Built around env isolation — not our need (`uv` handles env) |
| Parallelism | nox has `@parametrize`; weak cross-session parallelism story |
| JSON I/O | None; same Python helper pattern as `just` |
| Dependency probe | Generic; would call out anyway |
| Layer graph | Session dependencies exist but aren't strong |
| Codebase impact | noxfile.py + Python helpers; isolation we don't need |

**Verdict:** built for the wrong problem. Skip.

### 3.6 Click subcommand inside `quicksight-gen`

| Constraint | Fit |
|---|---|
| Process mgmt / parallelism / JSON / probe | Same as 3.4 — same Python orchestrator code, just different entry point |
| Layer graph | Same |
| Variant selection | Click's `@click.option` |
| Codebase impact | Same orchestrator goes under `quicksight_gen/cli/test.py`; ~50 LOC additional Click wrapping |

**Verdict:** the orchestrator code is the same as 3.4 either way; the question is just *where the entry point lives*. Existing `cli/{json,schema,data,docs,audit}.py::test` subcommands already shell out to pytest — that's a precedent in the *opposite* direction of "let's add more dev tooling to the customer-facing CLI." Recommend keeping the runner outside the customer Click surface; expose via `./run_tests.sh` as a dev-tooling shell script.

## 4. Recommendation

**Pytest-as-orchestrator + thin Python wrapper (option 3.4), with `./run_tests.sh` as the bash entry-point shim.**

- **CLI surface:** `./run_tests.sh up_to=<layer> [--variants=...] [--fuzz-seeds=N] [--only=...] [--skip-cheap] [--keep-on-failure] [--trace-all]`. The shell script is a one-liner that `exec`s the Python orchestrator, plus optional minimum-bash arg validation.
- **Orchestrator:** `quicksight_gen/_dev/runner.py` — private dev tooling, not customer-facing. ~400 LOC budget. Owns: argparse, run-id, dependency probe, per-variant subprocess dispatch via `asyncio.gather`, JSON capture/diff, drift report.
- **Substrate:** pytest. Tests carry layer markers (`@pytest.mark.layer3a`, etc.); per-variant fixture parametrization; JSON-output via `pytest_runtest_protocol` hook. Pytest-xdist for within-variant parallelism (existing).
- **Conftest delta:** add layer markers; add `--variant` option that parametrizes DB / Docker fixtures; add JSON-timing hook. Reuses everything already there.

### Why this wins on the constraint set

- **Process management:** pytest fixtures handle setup/teardown with proper scope; orchestrator just spawns pytest subprocesses with timeouts.
- **Parallelism:** `asyncio.gather` for cross-variant fan-out; pytest-xdist for within-variant.
- **JSON I/O:** stdlib `json`; pytest already emits structured per-test info via hooks.
- **Dependency probe:** ~50 LOC orchestrator pre-flight.
- **Layer graph:** sequential `await dispatch(layer)` is enough; no DAG engine needed.
- **Codebase impact:** ~400 LOC orchestrator + small conftest changes. Most logic is reuse, not new code.

### Why the alternatives lose

- **Pure bash:** JSON drift loop is the dominant logic; bash makes that brittle. Failure handling across parallel jobs is the second-worst pain point.
- **Pure Python (no pytest):** reinvents fixture scoping, xdist, marker selection, conftest. Risk of "huge second code base."
- **`just`:** the JSON-diff Python helpers dominate; justfile becomes thin glue with split-brain maintenance. Rust binary preference doesn't outweigh that.
- **`nox`/`tox`/`mise`:** built for env isolation we don't need.
- **Click subcommand:** same orchestrator code, but bloats customer-facing CLI. Existing precedent (`cli/{json}::test`) argues against adding more dev tooling there.

### Risk: "huge second code base" mitigation

Budget is ~400 LOC for the orchestrator. Mitigations:
- **No DAG engine** — layer chain is a hard-coded sequence; cross-variant within layer is `asyncio.gather`. That's it.
- **No marker engine** — pytest's `-m` selection does it.
- **No fixture scoping** — pytest fixtures do it.
- **No xdist parallelism** — pytest-xdist does it.
- **Only novel code:** run-id creation, JSON capture/diff loop, dep probe, per-variant subprocess dispatch. ~250-400 LOC total.

If during implementation the orchestrator starts growing past ~600 LOC, that's a signal to revisit — likely we're rebuilding pytest infrastructure that should be expressed as fixtures instead.

## 5. What this locks / unlocks if accepted

**Locks:**
- Audit `§7.3` — replace the "shell script LOCKED" framing with this recommendation.
- `Y.2.gate.b.0` — ticks complete; spike output is this doc.

**Unlocks:**
- `Y.2.gate.b.1` ... `b.11` — design sub-tasks proceed under this substrate. Some sub-tasks shrink (e.g. `b.1 variant axis catalog` is now "what markers + parametrize fixtures do we need").
- `Y.2.gate.c` — implementation: bash entry-point shim + Python orchestrator + conftest deltas. Each `c.X` sub-task gets specific Python module / function ownership.

**Open follow-ups (not deferrals — concrete sub-tasks that arise from this lock):**
- Confirm pytest version + plugin availability for the JSON hook we'd use (likely a custom hook in conftest; no new dependency).
- Confirm the `_dev/` package shape vs sibling alternatives (e.g., `tests/_runner/`). Lean `quicksight_gen/_dev/runner.py` because it's tooling that ships in dev installs but not in the customer wheel — pyproject.toml include rules handle that.
- The bash entry-point shim — minimum viable shape. Probably ~10 lines (`#!/usr/bin/env bash; exec uv run python -m quicksight_gen._dev.runner "$@"` plus a "venv exists?" check).
