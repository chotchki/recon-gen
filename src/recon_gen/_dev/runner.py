"""Test layer chain runner — orchestrates the layered test chain with per-run
output isolation + timing-diff drift detection.

Invoked via the ``./run_tests.sh`` bash shim at repo root; the shim
``exec``s into ``python -m recon_gen._dev.runner``.

Verbs:
    up_to <layer>     Run the chain up to and including <layer>.
                      Layers: unit | db | app2 | app2_browser | agreement
                      (pyright folds into unit via the conftest sessionstart
                      gate). DW.5.2 — QuickSight removed; the ``qs_api`` +
                      ``qs_browser`` tiers are gone. ``app2_browser`` is the
                      browser tier and DW.11 made ``agreement`` the terminal
                      layer (the final cross-renderer cross-check). ``unit``
                      is variant-independent — it runs ONCE as a prelude
                      before the matrix fans out (Y.2.gate.n), not once
                      per cell. Equivalent forms: ``up_to=<layer>`` and
                      ``up_to <layer>``.
    up [scope]        Boot dependencies. scope = local | aws | all (default).
    down [scope]      Tear down dependencies. scope as above.
    status [--cost]   Show what's currently running.

Exit codes:
    0  success
    1  test failure (one or more layers / variants failed)
    2  needs-operator (expired creds, dirty deploy refused, missing cfg, etc.)
    3  config / argument error

Substrate: pytest-as-orchestrator + this thin Python wrapper. See
``docs/audits/_archive/y_2_gate_b_0_runner_lang_spike.md`` for the design lock.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from io import TextIOWrapper
from dataclasses import dataclass, replace as dataclasses_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, cast

from recon_gen.common.env_keys import (
    RECON_E2E_PAGE_TIMEOUT,
    RECON_GEN_AS_OF_ANCHOR,
    RECON_GEN_CONFIG,
    RECON_GEN_DB_READ_ONLY,
    RECON_GEN_DEMO_DATABASE_URL,
    RECON_GEN_DEX_URL,
    RECON_GEN_DEX_USER_PASSWORD,
    RECON_GEN_ENV_LOG_DIR,
    RECON_GEN_DEMO_DATABASE_URL_OR,
    RECON_GEN_DEMO_DATABASE_URL_PG,
    RECON_GEN_FUZZ_SEED,
    RECON_GEN_LAYER,
    RECON_GEN_ORACLE_IMAGE,
    RECON_GEN_RUN_DIR,
    RECON_GEN_RUNNER_YES,
    RECON_GEN_TEST_L2_INSTANCE,
    RECON_GEN_TRACE_ALL,
    RECON_GEN_TRAINER_DIALECTS,
)

EXIT_SUCCESS: Final = 0
EXIT_FAILURE: Final = 1
EXIT_NEEDS_OPERATOR: Final = 2
EXIT_CONFIG_ERROR: Final = 3

LAYERS: Final[tuple[str, ...]] = (
    "unit",
    "db",
    "app2",
    "app2_browser",
    "agreement",
)
# DW.5.2 — QuickSight removed: the ``qs_api`` + ``qs_browser`` tiers are
# gone. ``app2_browser`` is the Playwright/WebKit browser tier — the root
# ``tests/e2e/test_*.py`` parametrized browser tests against locally-spun
# App 2 servers (app2-only post-DW.6). No AWS dep; the whole chain is
# fully local.
# DW.11 — ``agreement`` is the TERMINAL layer. It reads the JSON artifacts
# the db + app2 layers wrote (the cross-renderer high-watermark
# validators), needing neither a browser nor AWS, and runs LAST as the
# final cross-renderer cross-check. So ``up_to=agreement`` runs the whole
# chain (the comprehensive gate); ``up_to=app2`` is the fast gate that
# stops short of the browser tier. (DW.3 originally tucked agreement under
# app2_browser to keep it a fast gate; DW.11 promoted it to the top so the
# agreement check is the last word, after everything has rendered.)
# v14.0.0 fast-fail — per-layer stdout-stuck thresholds in seconds.
# When subprocess stdout hasn't grown in N seconds, the watchdog kills
# the layer. Calibrated against observed wall-clock times of clean runs
# (cefcceee baseline + post-v14 follow-ups). Layers absent from this
# dict get no watchdog (historical behavior). Operator-flagged
# 2026-06-13 after a 36-minute qs_browser hang ate a debugging cycle.
#
# 2026-06-14 calibration bump: ``app2`` + ``qs_browser`` raised from
# 600s → 900s after a false-positive kill on a 19m26s clean run with
# 2 faulthandler hits. ``-q -n auto --dist=loadgroup`` runs have
# stdout-silent windows up to ~9-10 min during cluster-of-slow-tests
# stretches; 600s clipped them mid-flight. 900s preserves the
# fast-fail intent (~30% over typical worst gap) without false kills.
#
# 2026-06-15 DJ.6 follow-on: ``app2`` raised 900s → 1800s after a
# diagnosed false-positive on a 19m45s 3-dialect concurrent run.
# Trainer-dogfood spans all 3 dialects (du/pg/or), each pinned to its
# own xdist loadgroup; after du+pg finish (~6min) only the Oracle
# worker emits dots, and an isolated Oracle slow-matview-refresh on a
# memory-pressured host can silently chew 12-15 min on a single test
# while the ~4-8GB Oracle container starves the other Studio servers.
# Isolation runs prove no single test exceeds 65s in clean conditions
# (or=18m32s for 17 tests in isolation; pg=6m16s; du=64s) — the
# concurrent run's silent window is resource contention, not a hung
# test. 1800s preserves fast-fail intent for a genuinely-hung session
# while accommodating worst-case memory-pressured cluster.
_HANG_THRESHOLDS: Final[dict[str, int]] = {
    "unit": 180,        # ~60s clean; faulthandler kicks at 180s
    "db": 240,          # ~40s clean; matview refresh can sprawl
    "app2": 1800,       # ~19m clean, ~30m memory-pressured 3-dialect concurrent
    # DW.5.2 — app2_browser replaces qs_browser as the browser tier.
    # App 2 server spin + Playwright loads + reruns dominate (no QS embed
    # now, but the root e2e suite is still heavy).
    "app2_browser": 900,
    "agreement": 240,   # ~seconds: JSON-artifact reads + set comparisons,
                        #           no DB / browser / AWS. db-like ceiling.
}
# CB.11.a.3 (2026-06-02) — renamed `api` → `qs_api`, `browser` →
# `qs_browser` to match the `Tier.QS_API` / `Tier.QS_BROWSER` marks
# defined in `tests/_marks.py`. The pytest mark selectors below still
# use `-m api` / `-m browser` against the old-style `@pytest.mark.api`
# / `@pytest.mark.browser` decorators — CB.6 will migrate selection to
# `--tier=qs_api` / `--tier=qs_browser` once the test-file migration
# (tests/e2e/qs_api/ + tests/e2e/qs_browser/ subdirs) finishes covering
# the full set.
# Y.2.gate.b.3.impl.layer (2026-05-07) — `app2` inserted as layer 3.7
# (between db + deploy) per audit §7.10. App2 is the local-Docker
# fast-feedback gate: same dataset SQL as QS, no AWS contact, runs
# the `tests/e2e/test_html2_*.py` files against the variant DB.
# Locked by audit §7.10 (App2 promotion: ~80% of bug classes
# catchable in App2 against local Docker).
# Y.2.gate.c.7-followup (2026-05-07) — `pyright` collapsed into the `unit`
# layer (and `biome check` joined it, the X.2.l.4 follow-on). The repo-root
# ``conftest.py::pytest_sessionstart`` runs pyright strict + (when `biome` is
# on PATH) `biome check` on session start; on failure ``pytest.exit(returncode=2)``
# fires before any test collects. So bare ``pytest tests/`` AND the runner
# both type-check + JS-lint, with no double-bookkeeping. Trade-off: both
# tools' duration folds into the unit layer's wall-clock instead of being
# their own `timings.json` entries. Acceptable — pyright ~2s, biome ~30ms.

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
RUNS_DIR: Final = REPO_ROOT / "runs"

# Y.2.gate.c.4 — keep last N runs; older auto-pruned at session end.
# 20 ≈ a couple weeks of inner-loop iteration; tunable by editing here if
# someone needs more triage history. `runs/` is gitignored so retention
# costs disk only.
RUNS_RETAIN_N: Final = 20

# Y.2.gate.b.8.impl — skip-if-already-green cache. Per-SHA per-layer
# pass markers so `--skip-cheap` can short-circuit the cheap layers
# (unit, db) when the current commit has already passed them in this
# session (or any prior session that hasn't been pruned). gitignored.
RUN_TESTS_CACHE_DIR: Final = REPO_ROOT / ".run_tests_cache"

# Y.2.gate.b.8 — only cheap layers participate in the cache. Heavy
# layers (deploy, api, browser) hit live AWS / spin up containers and
# their per-run state is fundamentally different (per-test resource
# names, AWS-side drift, etc.) — caching their pass-state would be
# unsound.
SKIPPABLE_LAYERS: Final = ("unit", "db")

# Y.2.gate.n — the `unit` layer (`pytest tests/unit tests/json …`; pyright
# folded in via the conftest sessionstart gate) is variant-INDEPENDENT — no
# DB / scenario / dialect / target dependency, byte-identical result every
# cell. So it runs ONCE per `up_to` invocation as a prelude (before the
# matrix fans out), not once per matrix cell. Artifacts land under
# `runs/<run-id>/_prelude/unit/`; the `--skip-cheap` cache marker uses this
# sentinel as its variant key (cache is variant-aware per b.8 — `_prelude`
# is the stable run-level bucket, never a real `<sc>_<di>_<ta>` spec name).
_PRELUDE_VARIANT: Final = "_prelude"

# Matches `<utc-ts>-<short-sha>[-dirty]` from create_run_id(); used by
# prune_old_runs to only touch directories we created, never unrelated
# files an operator might park under runs/.
_RUN_ID_PATTERN: Final = re.compile(r"^\d{8}T\d{6}Z-\w+(?:-dirty)?$")

# Y.2.gate.c.8 — per-layer dependency requirements. Authoritative mirror of
# audit doc §3 (variant axes table). Cross-checked by
# tests/unit/test_runner_skeleton.py::test_layer_deps_match_audit (c.14).
#
# Probe kinds (matched to _probe_* function names):
#   "docker"          — Docker daemon reachable (`docker ps`).
# CB.11.a.2 (2026-06-01) — `aws_rds_running` probe deleted. RDS Aurora is
# gone post-CB.12; Docker-on-self-hosted-runner is the only DB substrate.
# DW.11 (2026-06-28) — the `aws` creds probe deleted with the whole AWS
# footprint (DW.0.5 = fully-local). Docker is the only probe left.
#
# DB connectivity is probed via cfg-loaded URLs and lands when Y.2.gate.h.2
# (cfg-driven DB strings) wires up. For now, layers that need DB rely on the
# downstream pytest fixture failing loudly if the DB is unreachable.
_LAYER_DEPS: Final[dict[str, frozenset[str]]] = {
    "unit": frozenset(),
    "db": frozenset({"docker"}),
    # b.3.impl.layer — app2 needs Docker for the variant DB
    # container; intentionally NO `aws` because App2 is local-Docker
    # only by design (audit §7.10 LOCKED — App2 = local-feedback gate;
    # QS = AWS-deploy parity cell at 6/7).
    "app2": frozenset({"docker"}),
    # DW.3 — agreement reads artifacts on disk; it touches no AWS itself.
    # `docker` is the TRANSITIVE chain dep: `up_to=agreement` runs db +
    # app2 first, both of which need a container. Probing it here fails
    # fast on a docker-down box instead of ~2 layers deep.
    "agreement": frozenset({"docker"}),
    # DW.5.2 — app2_browser drives locally-spun App 2 servers (Playwright)
    # against the variant DB container. Needs `docker` only (no AWS) — same
    # dep set as `app2`. TLS cert provisioning is handled separately via
    # TLS_TOUCHING_LAYERS, not a probe dep.
    "app2_browser": frozenset({"docker"}),
}


@dataclass(frozen=True)
class ProbeFailure:
    """Y.2.gate.c.8 — a single missing or broken dependency.

    ``kind`` is a stable token (used by tests + telemetry); ``message`` is the
    operator-facing string (b.14.4 refusal pattern — actionable, points at
    what to do, never auto-invokes interactive flows).
    """

    kind: str
    message: str


def _run_probe_subprocess(
    cmd: list[str],
    timeout: float = 10.0,
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a probe subprocess with a timeout so a hanging command can't lock
    the runner. ``timeout=10s`` is generous; AWS CLI typically finishes in <2s,
    docker ps in <1s. On TimeoutExpired we synthesize a returncode=124 + empty
    stdout/stderr the caller can branch on.

    ``env_overrides``: caller-supplied env additions merged on top of
    `os.environ` for this subprocess only. (General-purpose seam; the
    only historical caller was the now-deleted `_probe_aws`.)
    """
    env = {**os.environ, **env_overrides} if env_overrides else None
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="probe timed out")
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=f"{cmd[0]}: not found")


_DOCKER_DAEMON_PROBE_BACKOFFS_SECONDS: Final[tuple[float, ...]] = (5.0, 10.0, 20.0)
"""CB.17.k — backoff schedule for ``_probe_docker`` daemon-down retries.

Post-macOS-reboot, Docker Desktop's daemon takes ~30-60s to be fully
responsive: `docker ps` answers immediately but `containers/<id>/start`
returns 500. Three attempts at 5s/10s/20s = ~35s ceiling matches the
operator-locked spec and beats the prior "single-shot probe → swallowed
warning → 32s of confusing db-tier failures" footgun. Wired into
``_probe_docker`` only for the ``docker_daemon_down`` classification —
``docker_cli_missing`` (rc=127) + other failure shapes still fail
fast.
"""


def _probe_docker() -> ProbeFailure | None:
    """Check Docker daemon is reachable via ``docker ps``.

    CB.17.k — when the daemon classification is ``docker_daemon_down``
    (the post-reboot lag window), retry with exponential backoff
    (5s, 10s, 20s = ~35s ceiling). Other failure shapes
    (``docker_cli_missing`` rc=127, ``docker_check_failed``) fail fast
    on the first attempt — they're not transient.

    Returns the same ``ProbeFailure`` shape on terminal failure; the
    message is updated to reference the elapsed timeout so the
    operator knows the retry budget was exhausted.
    """
    result = _run_probe_subprocess(["docker", "ps"])
    if result.returncode == 0:
        return None
    if result.returncode == 127:
        # CLI missing — not transient; no retries.
        return ProbeFailure(
            kind="docker_cli_missing",
            message="docker CLI not found — install Docker Desktop / docker engine, then re-invoke",
        )
    if "cannot connect to the docker daemon" not in result.stderr.lower():
        # Non-daemon-down failure (e.g. permission, malformed config) —
        # not transient; no retries.
        return ProbeFailure(
            kind="docker_check_failed",
            message=f"Docker check failed (rc={result.returncode}): {result.stderr.strip() or '(no stderr)'}",
        )

    # CB.17.k — daemon-down on attempt 1 may be the post-reboot lag
    # window. Retry with bounded backoff before declaring terminal.
    for backoff_seconds in _DOCKER_DAEMON_PROBE_BACKOFFS_SECONDS:
        print(
            f"runner: docker daemon not responsive — waiting "
            f"{backoff_seconds:.0f}s then retrying "
            f"(post-reboot lag window; ~35s ceiling)",
            file=sys.stderr,
        )
        time.sleep(backoff_seconds)
        result = _run_probe_subprocess(["docker", "ps"])
        if result.returncode == 0:
            return None
        if "cannot connect to the docker daemon" not in result.stderr.lower():
            # Shape changed mid-retry — surface as terminal.
            return ProbeFailure(
                kind="docker_check_failed",
                message=(
                    f"Docker check failed mid-retry "
                    f"(rc={result.returncode}): "
                    f"{result.stderr.strip() or '(no stderr)'}"
                ),
            )

    total_budget = int(sum(_DOCKER_DAEMON_PROBE_BACKOFFS_SECONDS))
    return ProbeFailure(
        kind="docker_daemon_down",
        message=(
            f"Docker daemon not responsive after {total_budget}s — try "
            "`docker info` to diagnose, or wait longer after "
            "`open -a Docker`"
        ),
    )


# CB.11.a.2 (2026-06-01) — `_probe_aws_rds_running` deleted along with
# the `aws_rds` module + RDS lifecycle commands. RDS Aurora is gone
# (CB.12 final); Docker on the self-hosted runner is the only DB
# substrate. Per-dialect Docker readiness probe lands in CB.11.b.


_ProbeFunc = Callable[[], "ProbeFailure | None"]
_PROBE_FUNCTIONS: Final[dict[str, _ProbeFunc]] = {
    "docker": _probe_docker,
}


@dataclass(frozen=True)
class RunOptions:
    """Y.2.gate.c.7 — operator-supplied flags threaded through dispatch.

    Most flags are scaffolding today (consumed by future c-stage tasks):

    - ``only`` — pytest ``-k <expr>`` filter (active now in c.7).
    - ``parallel`` — pytest-xdist worker count (active now in c.6; default 1 = serial).
    - ``fuzz_seed_value`` — the actual fuzz seed VALUE for this run (resolved at
      cmd_up_to entry: env-override > random-per-invocation; persists across xdist
      workers in this run via env passthrough — c.6.xdist-safety lock).
    - ``trace_all`` — Playwright capture every test (env var passthrough; consumed by c.11).
    - ``coverage`` — emit per-(variant, layer) ``.coverage.<variant>.<layer>`` data
      files (Y.2.gate.k.1.coverage). When set, every pytest layer (unit/db/app2/
      api/browser) runs with ``--cov=recon_gen --cov-report=`` and
      ``COVERAGE_FILE`` pointed at ``<run_dir>/.coverage.<run_dir.name>.<layer>``.
      The CI ``coverage`` aggregator job (W.8b) globs ``coverage-data-*`` artifacts
      and ``coverage combine``s them with no logic change. Off by default — opt in
      for CI; local runs don't need it.
    - ``scenarios`` / ``dialects`` / ``targets`` — variant matrix sub-flag narrowing (m.2.a).
      All None → ``compose_matrix`` returns the 13-cell ``full`` default. Any specified
      → cross-product mode where unspecified axes default per `variant.DEFAULT_*`.
    - ``variants`` — triage escape (single/multiple ``<sc>_<di>_<ta>`` codes); mutex
      with the sub-flag axes. None when not pinned.
    - ``fuzz_seeds`` — kept as count knob for future m.3 wiring (currently unused;
      fuzz cells inside ``compose_matrix`` already fan out via ``--scenarios=fuzz:N``).
    - ``skip_cheap`` — skip-if-already-green-this-SHA (active when cache lands; b.8).
    - ``keep_on_failure`` — leave the variant's ephemeral state up when the chain
      fails (gate.f.5; consumed in ``_run_one_variant``'s finally — see also
      gate.l.2 for the lifecycle commands that clean up afterward).
    """

    only: str | None = None
    parallel: int = 1
    scenarios: str | None = None
    dialects: str | None = None
    targets: str | None = None
    variants: str | None = None
    fuzz_seeds: int = 1
    fuzz_seed_value: int | None = None
    skip_cheap: bool = False
    keep_on_failure: bool = False
    trace_all: bool = False
    coverage: bool = False


def resolve_fuzz_seed_value() -> int:
    """Y.2.gate.c.6.xdist-safety — resolve the seed for this runner invocation.

    Priority: ``RECON_GEN_FUZZ_SEED`` env (operator pin for failure repro) > random
    per session (`secrets.randbits(32)`). Per audit §7.11 (LOCKED): default = 1
    random seed per run; cumulative coverage emerges across many runs. The seed
    is pinned across xdist workers within a single run so parametrize collection
    is deterministic (otherwise each worker rolls its own seed → collection
    diverges → ``Different tests were collected`` error).
    """
    override = RECON_GEN_FUZZ_SEED.get_or_none()
    if override is not None:
        return override
    return secrets.randbits(32)


@dataclass(frozen=True)
class LayerResult:
    """Y.2.gate.c.5 — outcome of dispatching one layer.

    `passed` checks the exit code; `duration_seconds` lands in the
    timings.json capture (c.2). Stub layers (deploy/api/browser until
    cfg loading lands per Y.2.gate.h.2) report skipped=True.
    """

    layer: str
    exit_code: int
    duration_seconds: float
    skipped: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


# Y.2.gate.c.5 — pre-resolved venv binaries. Dispatch needs absolute paths so
# pytest / pyright don't depend on the bash shim's PATH munging (it doesn't do
# any; this is just defensive against future changes).
_VENV_BIN: Final = REPO_ROOT / ".venv" / "bin"


def _layer_command(
    layer: str,
    run_dir: Path,
    options: RunOptions | None = None,
    *,
    variant_env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str]] | None:
    """Map layer → (subprocess argv, env additions). Returns None for layers
    that need preconditions the runner can't supply (e.g., deploy without a
    cfg-discovered L2 path).

    ``variant_env`` (Y.2.gate.c.5) — env_overrides the per-variant setup
    already injected (cfg path, L2 path, AWS profile, QS user ARN). The
    deploy layer reads `RECON_GEN_CONFIG` + `RECON_GEN_TEST_L2_INSTANCE` from
    here to construct the `recon-gen json apply` invocation; api +
    browser layers don't need it directly (env passes through to the
    pytest subprocess via the surrounding dispatch_layer).

    Pyright runs via the repo-root ``conftest.py::pytest_sessionstart`` hook
    (M.1.9c contract) at the start of every pytest invocation — so the unit
    layer's pytest invocation type-checks before any test runs. Direct
    ``pytest tests/`` invocations (developer one-test iteration) get the
    same gate. No separate runner layer; pyright duration folds into the
    unit layer's wall-clock.

    ``RECON_GEN_LAYER`` + ``RECON_GEN_RUN_DIR`` are threaded through to every
    pytest subprocess so ``tests/conftest.py``'s makereport hook (c.2)
    can write per-test timings into the right ``runs/<run-id>/timings/``
    file.

    Y.2.gate.c.7 — `options.only` adds `-k <expr>` to pytest invocations;
    `options.trace_all` exports `RECON_GEN_TRACE_ALL=1` (consumed by c.11
    browser fixtures).

    Y.2.gate.c.6.xdist-safety — `options.fuzz_seed_value` exports
    ``RECON_GEN_FUZZ_SEED=<N>`` so all xdist workers see the same seed and
    parametrize collection is deterministic.
    """
    opts = options or RunOptions()
    env_addl = {
        RECON_GEN_RUN_DIR.name: str(run_dir),
        RECON_GEN_LAYER.name: layer,
    }
    # CA.8 — DuckDB enforces single-writer-per-file across processes;
    # pytest-xdist workers in the db / app2 / browser tier all need
    # shared read access without locking each other out. Per the
    # DuckDB docs (https://duckdb.org/docs/current/clients/python/
    # dbapi#read_only-connections): "Read-only mode is required if
    # multiple Python processes want to access the same database file
    # at the same time." Setting this env tells the pytest workers'
    # connect_demo_db / _AsyncDuckdbPool to open with read_only=True.
    # Production CLI invocations (schema/data/seed apply) run before
    # pytest dispatch under sequential variant-seed steps that don't
    # see this env; they continue to open read-write. The audit verify
    # test subprocess inherits the env, which is correct — audit only
    # SELECTs from the seeded DB to render the PDF.
    if layer in ("db", "app2", "app2_browser"):
        ve = variant_env or {}
        url = ve.get(RECON_GEN_DEMO_DATABASE_URL.name, "")
        if url.startswith("duckdb://"):
            env_addl[RECON_GEN_DB_READ_ONLY.name] = "1"
    if opts.trace_all:
        env_addl[RECON_GEN_TRACE_ALL.name] = "1"
    if opts.fuzz_seed_value is not None:
        env_addl[RECON_GEN_FUZZ_SEED.name] = str(opts.fuzz_seed_value)
    # Y.2.gate-followon (2026-05-27) — conftest.py's `pytest_sessionstart`
    # runs `pyright` + `biome` before every pytest invocation. Pre-BE.7.D
    # those gates each fired against a ~91-file curated include + a thin
    # JS surface (a few seconds). Post-BE.7.D the pyright scope is the
    # whole `src/recon_gen` + `tests/` (~470 files, ~15-30s) and the
    # matrix dispatches 5 layers × N cells = repeats those static gates
    # dozens of times across a single `up_to=qs_browser` sweep.
    #
    # The unit prelude (`_run_unit_prelude`, runs ONCE) is the
    # authoritative static-gate run. Per-cell layers (db / app2 / deploy /
    # api / browser) are runtime gates; they don't need to re-run the
    # static checks. Set the opt-out env vars on their subprocess env so
    # the conftest skips both. Cuts a full-matrix sweep by ~15-30 min.
    if layer != "unit":
        env_addl["RECON_GEN_SKIP_PYRIGHT"] = "1"
        env_addl["RECON_GEN_SKIP_BIOME"] = "1"
        # BM.5 (2026-05-28) — also skip the Tailwind output.css drift
        # check on per-cell layers. The check rebuilds via Bun which
        # extracts a bundled native lib (lightningcss) to /tmp; 13
        # parallel cell pytest sessions racing on the same /tmp
        # extraction can crash any cell with
        # ``dlopen(lightningcss.darwin-arm64-XXX.node): no such file``
        # (Bun ERR_DLOPEN_FAILED, surfaced under `sp_sl_lo`). The
        # unit prelude already ran the gate cleanly once at session
        # start — per-cell pytest invocations don't need to re-run.
        env_addl["RECON_GEN_SKIP_TAILWIND"] = "1"
    # Y.2.gate.k.1.coverage — every pytest layer (everything except `deploy`,
    # which is a `recon-gen json apply` CLI call) writes a per-layer
    # `.coverage.<layer>` data file when `--coverage` is set.
    # `--cov-report=` (empty) suppresses the per-layer terminal report — the
    # CI `coverage combine` aggregator globs every `.coverage.*` file in
    # cwd, so a per-layer report is just stdout.log clutter.
    #
    # CB.17.g (2026-06-04) — `.coverage.<layer>` files now land at cwd
    # (REPO_ROOT) instead of `runs/<id>/.coverage.<run-id>.<layer>`. CI's
    # coverage step calls `coverage combine` from REPO_ROOT and the data
    # files are right there — no `find runs -name .coverage.* | xargs cp`
    # bespoke staging. The runs/<id>/ tree stays focused on triage
    # artifacts (cmd.json, stdout.log, timings.json).
    _is_pytest_layer = layer in (
        "unit", "db", "app2", "app2_browser", "agreement",
    )
    _cov_args: list[str] = (
        ["--cov=recon_gen", "--cov-report="]
        if opts.coverage and _is_pytest_layer
        else []
    )
    if opts.coverage and _is_pytest_layer:
        # COVERAGE_FILE is coverage.py's standard env var (not a RECON_GEN_*
        # registry var); set it on the layer's subprocess env directly.
        env_addl["COVERAGE_FILE"] = f".coverage.{layer}"
    if layer == "unit":
        cmd = [
            str(_VENV_BIN / "pytest"),
            "tests/unit",
            "tests/json",
            "tests/cli",
            "tests/docs",
            "tests/schema",
            "tests/l2",
            "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        # j.6 — within-layer pytest-xdist defaults to "auto" (= cpu_count
        # workers). Operator can pin via --parallel=N (e.g., --parallel=1
        # for serial debug). Same pattern as api/browser layers.
        cmd += _cov_args
        cmd += ["-n", str(opts.parallel) if opts.parallel > 1 else "auto"]
        # CB.7-followup (2026-06-02) — `--dist=loadgroup` was the cause
        # of the qs_browser cascade (workers crash at session-start,
        # cascade to max-worker-restart). Post-CB.7-unwind every test
        # self-isolates via per-(file, worker) hash, so xdist_group
        # pinning is no longer load-bearing. Scattered module-scope
        # seed fixtures reseed their own prefix per worker — no DB
        # contention, just N× wall on producer modules (acceptable).
        return (cmd, env_addl)
    if layer == "db":
        # 3a — DB-touching pytest. CB.6: discover
        # via the per-tier directory ``tests/e2e/db/`` — the conftest there
        # auto-applies ``@tier(Tier.DB)``, so adding a new DB-tier test is
        # ``touch tests/e2e/db/test_foo.py`` instead of editing this
        # hardcoded list. The composition-rule conftest at
        # ``tests/conftest.py`` validates tier marks at collection time.
        cmd = [
            str(_VENV_BIN / "pytest"),
            "tests/e2e/db/",
            "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        # j.6 — see unit layer comment.
        cmd += _cov_args
        cmd += ["-n", str(opts.parallel) if opts.parallel > 1 else "auto"]
        # CB.7-followup (2026-06-02) — loadgroup dropped; see unit-layer note.
        return (cmd, env_addl)
    if layer == "app2":
        # b.3.impl.layer — App2 e2e (HTMX dialect, Playwright WebKit
        # against the App2 Starlette server). CB.6: discover via the
        # per-tier directory ``tests/e2e/app2/`` — the conftest there
        # auto-applies ``@tier(Tier.APP2)``, replacing the prior hardcoded
        # ``test_html2_*.py`` + ``test_dashboard_driver.py`` list. NO AWS
        # contact (audit §7.10 LOCKED).
        cmd = [
            str(_VENV_BIN / "pytest"),
            "tests/e2e/app2/",
            "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        # j.6 — see unit layer comment.
        cmd += _cov_args
        cmd += ["-n", str(opts.parallel) if opts.parallel > 1 else "auto"]
        # Operator (2026-06-26) — app2 is a Playwright/WebKit browser tier too; a
        # wild driver exhausting resources occasionally times out a cascade
        # refetch (e.g. test_dm_cascade_and_day_availability's pick_filter
        # expect_response). Match the browser layer's auto-retry so one flake
        # retries instead of halting the whole chain; a genuinely-broken test
        # still fails twice → halts. (`pytest-rerunfailures`, [dev] extra.)
        cmd += ["--reruns", "1", "--reruns-delay", "15"]
        # BV.3.3 fix (2026-06-10) — re-enable `--dist=loadgroup` here ONLY.
        # The BV.3.3 trainer dogfood module pins its session-scope fixture
        # via `pytest.mark.xdist_group("trainer-<dialect>")` (see
        # tests/e2e/app2/test_bv33_trainer_dogfood.py:186-187). Under the
        # xdist default `--dist=load`, that mark is a silent no-op — all
        # 16 workers concurrently call `trainer_ready_session`, race the
        # shared `recon-gen-test-pg` container's /etl/run (full schema
        # drop + seed + matview refresh + clone-to-v-overlay), and pile
        # up on Playwright 600s timeouts.
        #
        # The CB.7-followup unwind that dropped the global loadgroup bump
        # was specifically the qs_browser marker-deselection × loadgroup
        # interaction (xdist 3.8 dies at session-start when marker-
        # deselected items carry xdist_group). App2 discovers via
        # `tests/e2e/app2/` directory with NO `-m` filter — that hazard
        # does not apply here.
        cmd += ["--dist=loadgroup"]
        # DW.5.2 triage — POLICY 1 parity: ci.yml pins
        # RECON_GEN_TRAINER_DIALECTS=du, but the runner set nothing, so the
        # LOCAL chain defaulted to all 3 dialects (du/pg/or —
        # test_bv33_trainer_dogfood.py:154) and spun three Studio servers
        # concurrently under --dist=loadgroup. On a memory-pressured host
        # they starve each other and the Trainer dogfood's
        # `Page.wait_for_function` hangs (the documented CE.4 Studio-server
        # flake; faulthandler shows the Playwright greenlet stuck in
        # run_until_complete). CI dodged it by setting the env; local didn't
        # → "passes on CI, hangs locally" — exactly the divergence POLICY 1
        # forbids. Default the runner to the same single dialect CI uses so
        # local ≡ CI; operator-set value wins. The deeper pg/or Studio-server
        # SessionStart hang stays the open CE.4 root-cause (backlog).
        if RECON_GEN_TRAINER_DIALECTS.name not in os.environ:
            env_addl[RECON_GEN_TRAINER_DIALECTS.name] = "du"
        return (cmd, env_addl)
    if layer == "agreement":
        # DW.3 — cross-renderer agreement validators. Pure JSON-artifact
        # readers (no DB, no browser, no AWS): they compare what the db +
        # app2 producers rendered into ``<run_dir>/{db,app2}/*.json``.
        #
        # Collect ``tests/e2e/db/`` + ``tests/e2e/app2/`` ALONGSIDE
        # ``tests/e2e/agreement/`` so the validators' ``@inputs(...)``
        # producer nodeids resolve at collection time (the conftest's
        # collection-time check needs them present), then
        # ``--tier=agreement`` deselects everything but the validators to
        # RUN. The producers already ran in the earlier db + app2 layers
        # and left their artifacts under the shared run dir; this layer
        # only reads + asserts. Every heavyweight autouse fixture
        # (qs_deployed / matview-refresh / pre-warm) gates on
        # ``_session_needs_aws`` — False here, since the validators
        # request no AWS fixtures — so no QS deploy, no DB contact fires.
        # No ``--reruns`` (deterministic file reads), no browser
        # page-timeout, no Oracle worker cap.
        cmd = [
            str(_VENV_BIN / "pytest"),
            "tests/e2e/db/",
            "tests/e2e/app2/",
            "tests/e2e/agreement/",
            "--tier=agreement",
            "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        cmd += _cov_args
        cmd += ["-n", str(opts.parallel) if opts.parallel > 1 else "auto"]
        return (cmd, env_addl)
    if layer == "app2_browser":
        # DW.5.2 — terminal browser tier (was ``qs_browser`` before
        # QuickSight was removed). Playwright/WebKit against locally-spun
        # App 2 servers: the root ``tests/e2e/test_*.py`` parametrized
        # browser tests (pytest mark ``browser``), now app2-only post-DW.6.
        #
        # Select by the ``browser`` mark, but ``--ignore`` the per-tier
        # subdirs — ``app2/`` files own the ``app2`` layer (and six of them
        # ALSO carry ``mark.browser``, so without the ignore they'd
        # double-run here); ``db/`` + ``agreement/`` have their own layers.
        # That leaves exactly the root e2e browser files.
        nworkers = str(opts.parallel) if opts.parallel > 1 else "4"
        # BR.x — Oracle cells lower the cap to 2. Oracle SE2 19c has no
        # DRCP (project_drcp_on_aws_oracle_dead_end) so every worker
        # opens a fresh session against the small connection ceiling;
        # 4 browser workers × 4 apps × concurrent picker tests
        # exhausted sessions mid-run and produced cascading
        # "rendered: []" structure failures (Oracle had 71-74 vs ≤9 on
        # PG at 4 workers). The narrow 2-worker cap costs ~2× wall on
        # Oracle but turns catastrophic failure into signal.
        ve = variant_env or {}
        cfg_path_str = ve.get(RECON_GEN_CONFIG.name)
        if cfg_path_str and opts.parallel <= 1:
            try:
                from recon_gen.common.config import load_config  # noqa: PLC0415 — lazy
                from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415
                cfg_peek = load_config(cfg_path_str)
                if cfg_peek.db.dialect is Dialect.ORACLE:
                    nworkers = "2"
            except Exception:  # noqa: BLE001 — peek failure shouldn't gate the layer
                pass
        only = ["-k", opts.only] if opts.only else []
        cmd = [
            str(_VENV_BIN / "pytest"), "tests/e2e/",
            "-m", "browser", "-q",
            "--ignore=tests/e2e/app2",
            "--ignore=tests/e2e/db",
            "--ignore=tests/e2e/agreement",
            *only, *_cov_args,
            "-n", nworkers,
            # Y.7-followup — auto-retry a flaky browser test
            # (``pytest-rerunfailures``, [dev] extra) instead of failing
            # the whole chain on it. The browser tier drives a live App 2
            # server under ``-n 4`` worker contention; a render-timing
            # flake (a visual lost from the DOM under concurrent load,
            # passes on re-run / in isolation) costs ~one test re-run,
            # not a whole ``unit→…→app2_browser`` cycle. A genuinely
            # broken test fails twice → still halts. One retry + 15s delay
            # (v14.0.0 fast-fail): real bugs surface within ~30s.
            "--reruns", "1", "--reruns-delay", "15",
            # CB.7-followup (2026-06-02) — loadgroup dropped; see unit-layer note.
        ]
        # Bump the per-page Playwright timeout to 60 s. The default 30 s
        # (tests/e2e/conftest.py) is fine for a local-pg container but too
        # tight for the Oracle container's slower per-query latency.
        # Operator-set value wins.
        browser_env = env_addl
        if RECON_E2E_PAGE_TIMEOUT.name not in os.environ:
            browser_env[RECON_E2E_PAGE_TIMEOUT.name] = "60000"
        return (cmd, browser_env)
    # Fallthrough: unknown layer name. Return None so dispatch prints
    # `dispatch-skip` rather than crashing — easier-to-triage failure mode
    # if someone adds a layer to LAYERS without wiring its command.
    return None


def _tee_stream(
    src: TextIOWrapper,
    terminal: TextIOWrapper,
    sink: TextIOWrapper,
    *,
    terminal_prefix: str = "",
) -> None:
    """Drain ``src`` line-by-line, writing each line to both ``terminal``
    (live operator feedback) and ``sink`` (persisted artifact). Used in
    a daemon thread per stream so stdout + stderr drain in parallel
    without buffer-fill deadlock.

    ``terminal_prefix`` (Y.2.gate.c.6.async) is prepended to each line
    written to the terminal so per-variant fan-out shows
    ``[local-pg] foo`` / ``[local-oracle] bar`` interleaved without
    losing track of which variant emitted which line. The sink (per-
    variant log file under ``<run_dir>/<variant>/<layer>/{stdout,
    stderr}.log``) gets the bare line — the directory already encodes
    the variant.
    """
    for line in iter(src.readline, ""):
        if terminal_prefix:
            terminal.write(terminal_prefix + line)
        else:
            terminal.write(line)
        terminal.flush()
        sink.write(line)
        sink.flush()


def _spawn_with_tee(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    terminal_prefix: str = "",
    hang_threshold_seconds: int | None = None,
    hang_kill_flag: list[bool] | None = None,
) -> tuple[int, float]:
    """Spawn ``cmd`` as a subprocess; tee stdout/stderr to operator's
    terminal AND to the named log files; return (returncode, duration).

    Daemon threads drain each pipe so a full buffer on one stream can't
    deadlock the other. ``terminal_prefix`` flows to ``_tee_stream`` for
    per-variant line tagging in multi-variant fan-out.

    Y.2.gate.c.6.async — extracted from ``dispatch_layer`` so
    ``seed_variant`` (and any future subprocess) can capture + prefix
    with the same contract.

    v14.0.0 fast-fail — when ``hang_threshold_seconds`` is set, a
    watchdog daemon thread polls the tee'd stdout file's size every
    30s. If size hasn't advanced for ``hang_threshold_seconds``,
    proc.kill() the subprocess + log a clear stderr message. The
    one-element ``hang_kill_flag`` list (passed from the caller so the
    flag survives thread boundaries) is set to True on kill so the
    caller can mark cmd.json + the failing-layers report. None disables
    the watchdog — historical behavior for tests that mock the
    subprocess shape.
    """
    start = time.monotonic()
    with stdout_path.open("w") as out_f, stderr_path.open("w") as err_f:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=1, text=True,
        )
        # mypy/pyright: Popen with stdout/stderr=PIPE + text=True
        # narrows both to TextIOWrapper, but the static analysis loses
        # the narrowing through the with-block branching. assert here.
        assert proc.stdout is not None and proc.stderr is not None
        t_out = threading.Thread(
            target=_tee_stream,
            args=(proc.stdout, sys.stdout, out_f),
            kwargs={"terminal_prefix": terminal_prefix},
            daemon=True,
        )
        t_err = threading.Thread(
            target=_tee_stream,
            args=(proc.stderr, sys.stderr, err_f),
            kwargs={"terminal_prefix": terminal_prefix},
            daemon=True,
        )
        t_out.start()
        t_err.start()
        if hang_threshold_seconds is not None:
            t_watchdog = threading.Thread(
                target=_hang_watchdog,
                args=(proc, stdout_path, hang_threshold_seconds, terminal_prefix),
                kwargs={"hang_kill_flag": hang_kill_flag},
                daemon=True,
            )
            t_watchdog.start()
        proc.wait()
        # Drain both pipes before declaring done — wait() doesn't wait
        # on the reader threads.
        t_out.join()
        t_err.join()
    duration = time.monotonic() - start
    return proc.returncode, duration


def _hang_watchdog(
    proc: "subprocess.Popen[str]",
    stdout_path: Path,
    threshold_seconds: int,
    terminal_prefix: str,
    *,
    hang_kill_flag: list[bool] | None = None,
) -> None:
    """v14.0.0 fast-fail — kill ``proc`` when its tee'd stdout file
    size hasn't advanced in ``threshold_seconds``. Polls every 30s.

    Picks file size (not mtime) as the progress signal because the tee
    thread appends every line as the subprocess emits it; size growth
    is a strict-monotonic proxy for "the subprocess is alive and making
    progress." mtime advances on the tee thread's flush which doesn't
    correlate to subprocess progress as cleanly.

    Logs a clear ``[hang-kill] layer=X — stuck for Ys`` line to stderr
    + sets ``hang_kill_flag[0] = True`` so the caller surfaces the kill
    in cmd.json + the failing-layers report (rather than reporting an
    opaque SIGKILL exit code as a normal failure).
    """
    last_size = 0
    last_progress = time.monotonic()
    poll_interval = 30
    while proc.poll() is None:
        time.sleep(poll_interval)
        try:
            size = stdout_path.stat().st_size
        except OSError:
            continue
        if size > last_size:
            last_size = size
            last_progress = time.monotonic()
            continue
        stuck_for = int(time.monotonic() - last_progress)
        if stuck_for > threshold_seconds:
            if hang_kill_flag is not None:
                hang_kill_flag[0] = True
            sys.stderr.write(
                f"{terminal_prefix}runner: [hang-kill] stdout stuck for "
                f"{stuck_for}s (threshold {threshold_seconds}s) — "
                f"killing subprocess + draining pipes\n"
            )
            sys.stderr.flush()
            proc.kill()
            return


def dispatch_layer(
    layer: str,
    run_dir: Path,
    options: RunOptions | None = None,
    *,
    variant_env: dict[str, str] | None = None,
    terminal_prefix: str = "",
) -> LayerResult:
    """Y.2.gate.c.5 — run one layer; return its result.

    Stub layers return a `skipped=True` LayerResult with exit_code=0 so the
    chain doesn't break — the deferred work is c.5+ follow-up, not a runner
    bug. Stubs print a clear `dispatch-skip` line so the operator knows.

    Y.2.gate.b.2.impl — ``variant_env`` (e.g.,
    ``{"RECON_GEN_DEMO_DATABASE_URL": "<container-url>"}``) gets merged into
    the subprocess env so the variant's resources (Docker container
    URL etc.) are visible to pytest fixtures + cfg loaders inside the
    subprocess.

    **Per-layer subprocess capture** (Y.2.gate.b.2.impl.oracle followup):
    every dispatch persists four artifacts under ``<run_dir>/<layer>/``:

    - ``cmd.json`` — the input: cmd argv, cwd, env-overrides (deltas
      from inherited os.environ — the layer-specific keys + variant env,
      not the noisy full environ). Written before the subprocess starts;
      re-written after with ``exit_code`` + ``duration_seconds``.
    - ``stdout.log`` — subprocess stdout, also teed to operator's
      terminal in real time.
    - ``stderr.log`` — subprocess stderr, also teed to terminal.

    Streams use a per-stream daemon-thread tee so a full pipe buffer on
    one stream can't deadlock the other. The operator sees live output
    same as before; failures leave a complete trail in the run dir for
    post-mortem (CI artifact upload, hands-off run review).
    """
    cmd_env = _layer_command(layer, run_dir, options, variant_env=variant_env)
    if cmd_env is None:
        # Y.2.gate.c.5 — None means the layer name isn't wired (likely
        # an unknown layer added to LAYERS without a matching arm in
        # ``_layer_command``). DI phase — the prior ``deploy``-specific
        # cfg-missing dispatch-skip is gone; deploy is retired and the
        # ``qs_deployed`` fixture's pytest.fail surfaces deploy
        # precondition failures (cfg / L2 missing) loudly from inside
        # the qs_api / qs_browser session.
        print(
            f"{terminal_prefix}runner: dispatch-skip [{layer}] no "
            f"command wired (unknown layer name?)"
        )
        return LayerResult(layer=layer, exit_code=0, duration_seconds=0.0, skipped=True)

    cmd, env_addl = cmd_env

    # Recursion guard: if dispatch_layer is about to spawn a pytest cmd
    # while we're already running INSIDE pytest AND ``subprocess.Popen``
    # is the real one (no test mock in effect), the test forgot to
    # isolate the spawn. Without this guard, the inner pytest re-runs
    # the full test suite, hits the same dispatch_layer code, and
    # fan-outs explosively until OS process limits or test timeout
    # kill it. Fail loud here with a message that names the fix.
    #
    # ``isinstance(subprocess.Popen, type)`` is the mock-detector:
    # real ``Popen`` is a class (a type); ``patch.object(subprocess,
    # "Popen", side_effect=...)`` replaces it with a ``MagicMock``
    # instance which isn't a type. Production code never replaces it,
    # so this check has no runtime cost outside test contexts.
    if (
        os.environ.get("PYTEST_CURRENT_TEST")
        # cast(object, ...) defeats pyright's "Popen is always a type"
        # narrowing — at RUNTIME, a unittest.mock.patch replaces
        # subprocess.Popen with a MagicMock instance, which fails the
        # isinstance(_, type) check. The cast tells the static
        # checker we know what we're doing.
        and isinstance(cast(object, subprocess.Popen), type)
        and cmd
        and "pytest" in os.path.basename(cmd[0])
    ):
        raise RuntimeError(
            f"dispatch_layer would spawn pytest for layer {layer!r} "
            f"while already inside pytest "
            f"(PYTEST_CURRENT_TEST={os.environ['PYTEST_CURRENT_TEST']!r}). "
            f"This recursive spawn explodes at test runtime. The test "
            f"must mock either ``subprocess.Popen`` (use the "
            f"``_fake_popen_factory`` helper in tests/unit/"
            f"test_runner_skeleton.py) or ``runner._layer_command`` "
            f"(monkeypatch to return a tiny ``python -c`` cmd) before "
            f"calling dispatch_layer."
        )
    # Y.2.gate.b.2.impl — variant_env only applies to layers that
    # actually need a DB. Unit doesn't (in-process tests / pyright);
    # leaking RECON_GEN_DEMO_DATABASE_URL into the unit subprocess
    # contaminates tests that assert "no demo_database_url is set".
    effective_variant_env = (
        variant_env if variant_env and layer in DB_TOUCHING_LAYERS else {}
    )
    # CB.17.d — point the subprocess's `pytest_sessionfinish` hook at a
    # per-layer dir so its EnvVar access log lands somewhere we can find.
    # Set in env_addl so cmd.json captures it as an override (visible
    # diff vs prior runs).
    env_log_dir = run_dir / layer / "env_log"
    env_log_dir.mkdir(parents=True, exist_ok=True)
    env_addl = {
        **env_addl,
        RECON_GEN_ENV_LOG_DIR.name: RECON_GEN_ENV_LOG_DIR.serialize(str(env_log_dir)),
    }
    env = {**os.environ, **env_addl, **effective_variant_env}

    # Per-layer capture artifacts. Created lazily so a stub-skip
    # doesn't litter empty dirs.
    layer_dir = run_dir / layer
    cmd_path = layer_dir / "cmd.json"
    stdout_path = layer_dir / "stdout.log"
    stderr_path = layer_dir / "stderr.log"

    def _ensure_dir() -> None:
        # Defensive remake: a concurrent ``prune_old_runs`` (from a
        # parallel runner invocation, or a test fixture mucking with
        # RUNS_DIR mid-test) can rmtree the run dir between writes.
        # Cheap call, idempotent — keeps the persisted-artifact
        # contract intact even under races.
        layer_dir.mkdir(parents=True, exist_ok=True)

    _ensure_dir()

    # Persist the input (cmd + env deltas) BEFORE running so a hard
    # crash still leaves a trail of what we tried to invoke.
    cmd_meta: dict[str, Any] = {
        "layer": layer,
        "cmd": list(cmd),
        "cwd": str(REPO_ROOT),
        "env_overrides": {**env_addl, **effective_variant_env},
    }
    cmd_path.write_text(json.dumps(cmd_meta, indent=2) + "\n")

    print(f"{terminal_prefix}runner: dispatch-run [{layer}] {' '.join(cmd)}")
    hang_kill_flag: list[bool] = [False]
    threshold = _HANG_THRESHOLDS.get(layer)
    returncode, duration = _spawn_with_tee(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        terminal_prefix=terminal_prefix,
        hang_threshold_seconds=threshold,
        hang_kill_flag=hang_kill_flag,
    )

    # Re-write cmd.json with the result. Append shape (rather than two
    # files) keeps the per-layer summary in one place. Defensive
    # ensure-dir handles the race window (see _ensure_dir comment).
    cmd_meta["exit_code"] = returncode
    cmd_meta["duration_seconds"] = duration
    if hang_kill_flag[0]:
        cmd_meta["hang_kill"] = True
        cmd_meta["hang_threshold_seconds"] = threshold
        print(
            f"{terminal_prefix}runner: [hang-kill] layer={layer} — "
            f"stdout was stuck for >{threshold}s; subprocess killed. "
            f"See {stderr_path.relative_to(REPO_ROOT)} for the last "
            f"thread dumps."
        )

    # DG.3 — heartbeat-hit detection. The stdlib faulthandler dumps a
    # stack trace to stderr whenever a test exceeds
    # ``faulthandler_timeout`` (pyproject.toml). Surface the count
    # here so the operator sees "1 test wedged for >180s on this
    # layer" without grepping logs. Pattern is "Timeout (HH:MM:SS)!"
    # at line start — faulthandler's exact format. The trace itself
    # stays in stderr.log for triage.
    fh_hits = 0
    try:
        for line in stderr_path.read_text().splitlines():
            if line.startswith("Timeout (") and line.endswith("!"):
                fh_hits += 1
    except OSError:
        # stderr.log not written (subprocess never started cleanly).
        pass
    cmd_meta["faulthandler_hits"] = fh_hits
    if fh_hits > 0:
        print(
            f"{terminal_prefix}runner: [heartbeat-hit] layer={layer} "
            f"— {fh_hits} faulthandler trip(s) logged "
            f"(see {stderr_path.relative_to(REPO_ROOT)} — grep "
            f"'^Timeout (' for stack traces)"
        )

    _ensure_dir()
    cmd_path.write_text(json.dumps(cmd_meta, indent=2) + "\n")

    return LayerResult(
        layer=layer, exit_code=returncode, duration_seconds=duration,
    )


# Y.2.gate.c.3 — drift threshold. ±50% triggers a ⚠ marker. Spec'd in audit
# §7.9 LOCKED 2026-05-07 — generous default; tightens as Phase Y / X.2 sweeps
# settle baselines (Y.2.gate.j.9: "first run = baseline; ratchet via timing-diff").
DRIFT_THRESHOLD_PCT: Final = 0.50


@dataclass(frozen=True)
class DriftEntry:
    """Y.2.gate.c.3 — one layer's drift vs the prior run."""

    layer: str
    current_seconds: float
    prior_seconds: float | None  # None if layer didn't run in the prior run

    @property
    def delta_pct(self) -> float | None:
        if self.prior_seconds is None or self.prior_seconds == 0:
            return None
        return (self.current_seconds - self.prior_seconds) / self.prior_seconds

    @property
    def is_drift(self) -> bool:
        delta = self.delta_pct
        return delta is not None and abs(delta) >= DRIFT_THRESHOLD_PCT


def _extract_sha(run_id: str) -> str:
    """``20260507T213138Z-9336911[-dirty]`` → ``9336911``.

    Used by `find_prior_run` to prefer matching-SHA prior runs over time-only
    nearest neighbors (a same-SHA comparison is the closest signal — same code,
    different timing).
    """
    parts = run_id.split("-")
    return parts[1] if len(parts) >= 2 else ""


def find_prior_run(current_run_id: str, runs_dir: Path | None = None) -> Path | None:
    """Y.2.gate.c.3 — pick the best prior run for drift comparison.

    Priority: (1) most-recent prior with the SAME SHA (closest signal — same
    code, lets us see real timing drift); (2) most-recent prior overall (good
    enough when no SHA match). Returns None if no prior runs exist."""
    target = runs_dir if runs_dir is not None else RUNS_DIR
    if not target.exists():
        return None
    current_sha = _extract_sha(current_run_id)
    candidates = [
        p for p in target.iterdir()
        if (
            p.is_dir()
            and _RUN_ID_PATTERN.match(p.name)
            and p.name != current_run_id
            and (p / "timings.json").exists()
        )
    ]
    if not candidates:
        return None
    same_sha = [p for p in candidates if _extract_sha(p.name) == current_sha]
    if same_sha:
        same_sha.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return same_sha[0]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def compute_drift(current: dict[str, Any], prior: dict[str, Any]) -> list[DriftEntry]:
    """Y.2.gate.c.3 — diff per-layer durations between two timings.json blobs.

    Only emits entries for layers present in `current` (not interested in
    layers that ran in prior but not now — that's chain-narrowing, not drift)."""
    current_durs: dict[str, float] = current.get("layer_durations", {})
    prior_durs: dict[str, float] = prior.get("layer_durations", {})
    entries: list[DriftEntry] = []
    for layer, current_dur in current_durs.items():
        prior_raw = prior_durs.get(layer)
        prior_val = float(prior_raw) if prior_raw is not None else None
        entries.append(DriftEntry(layer=layer, current_seconds=float(current_dur), prior_seconds=prior_val))
    return entries


def report_drift(current_run_dir: Path, runs_dir: Path | None = None) -> None:
    """Y.2.gate.c.3 — find prior run, compute drift, print report.

    Output shape:
        drift: comparing against <prior_run_id>
        drift: pyright 1.81s (was 1.85s, -2.2%)
        drift: unit 15.20s (was 10.42s, +45.9%)
        drift: db 24.10s (was 12.30s, +96.0%) ⚠

    The ⚠ marker fires on `abs(delta_pct) >= DRIFT_THRESHOLD_PCT` (±50%);
    same shape as hash-locked seed data — a sudden delta is signal, not noise.
    """
    prior_run = find_prior_run(current_run_dir.name, runs_dir)
    if prior_run is None:
        print("drift: no prior run to compare against")
        return
    print(f"drift: comparing against {prior_run.name}")
    current = json.loads((current_run_dir / "timings.json").read_text())
    prior = json.loads((prior_run / "timings.json").read_text())
    for entry in compute_drift(current, prior):
        if entry.prior_seconds is None:
            print(f"drift: {entry.layer} {entry.current_seconds:.2f}s (new — no prior)")
            continue
        delta_pct = entry.delta_pct or 0.0
        sign = "+" if delta_pct >= 0 else ""
        marker = " ⚠" if entry.is_drift else ""
        print(
            f"drift: {entry.layer} {entry.current_seconds:.2f}s "
            f"(was {entry.prior_seconds:.2f}s, {sign}{delta_pct * 100:.1f}%){marker}"
        )


def _aggregate_test_jsonl(run_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Read every ``timings/<layer>[-worker*].jsonl`` produced by conftest's
    makereport hook (c.2); return ``{layer: {test_id: {duration, outcome}}}``.

    The ``-worker*`` suffix lands when xdist parallelism kicks in (c.6); per-
    worker files avoid append contention. For now (no xdist), each layer
    writes one file."""
    timings_dir = run_dir / "timings"
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not timings_dir.exists():
        return out
    for jsonl_file in sorted(timings_dir.glob("*.jsonl")):
        # `<layer>.jsonl` or `<layer>-worker<n>.jsonl`
        layer = jsonl_file.stem.split("-", 1)[0]
        tests = out.setdefault(layer, {})
        for line in jsonl_file.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            tests[str(record["test_id"])] = {
                "duration_seconds": float(record["duration_seconds"]),
                "outcome": str(record["outcome"]),
            }
    return out


def collect_run_outputs(run_dir: Path, layer_results: Sequence[LayerResult]) -> None:
    """Y.2.gate.c.2 — write ``timings.json`` + ``hashes.json`` after the chain.

    ``timings.json`` aggregates per-layer wall-clock durations + per-test
    timings (for layers that ran pytest, via the conftest hook).
    ``hashes.json`` is a placeholder — populated by future tests/code as part
    of ``c.13`` (hash-lock collapses into the runs dir).

    Single-source-of-drift principle (audit §7.9): both files live alongside
    each other under ``runs/<run-id>/``; ``c.3``'s drift-diff reads them
    together against the prior run.
    """
    aggregated: dict[str, Any] = {
        "layer_durations": {r.layer: r.duration_seconds for r in layer_results if not r.skipped},
        "skipped_layers": [r.layer for r in layer_results if r.skipped],
        "layer_exit_codes": {r.layer: r.exit_code for r in layer_results},
        "test_durations": _aggregate_test_jsonl(run_dir),
    }
    (run_dir / "timings.json").write_text(json.dumps(aggregated, indent=2) + "\n")
    hashes_path = run_dir / "hashes.json"
    if not hashes_path.exists():
        # Empty stub — c.13 fills this in when the global SHA256 lock collapses
        # into per-run captures.
        hashes_path.write_text("{}\n")


def chain_through(target: str) -> list[str]:
    """Y.2.gate.c.5 — return the slice of LAYERS from start through ``target``.

    Chain semantics (b.9 LOCKED): cross-layer is sequential. ``up_to=db`` means
    pyright → unit → db; ``up_to=qs_browser`` means the full chain.
    """
    idx = LAYERS.index(target)
    return list(LAYERS[: idx + 1])


def probe_dependencies(layer: str) -> list[ProbeFailure]:
    """Y.2.gate.c.8 — probe every dep ``layer`` needs; return all failures.

    Probes run sequentially (cheap; few seconds total) and gather all failures
    so the operator sees everything missing in one pass instead of fixing one,
    re-running, hitting the next, etc. No state file (LOCKED §7.12) — each
    invocation re-probes."""
    failures: list[ProbeFailure] = []
    for dep_kind in sorted(_LAYER_DEPS[layer]):
        probe = _PROBE_FUNCTIONS[dep_kind]
        result = probe()
        if result is not None:
            failures.append(result)
    return failures


def _short_sha() -> str:
    """Return short git SHA, or 'nogit' if not in a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def _cache_marker_path(layer: str, sha: str, variant: str = "default") -> Path:
    """Y.2.gate.b.8.impl — path to the per-(layer, sha, variant)
    cache marker. Variant-aware (Y.2.gate.b.2.impl): a green marker
    for variant=default doesn't signal green for variant=local-pg.

    File schema (JSON):
      {"sha": "<short-sha>", "layer": "<name>", "variant": "<name>",
       "passed_at": "<utc-iso>", "duration_seconds": <float>}
    """
    return RUN_TESTS_CACHE_DIR / f"{sha}.{layer}.{variant}.json"


def write_cache_marker(
    layer: str, *, duration_seconds: float, variant: str = "default",
) -> None:
    """Y.2.gate.b.8.impl — record that ``layer`` passed for the
    current SHA + variant. No-op if not in a git repo (`_short_sha`
    returns 'nogit') so direct ``pytest`` invocations don't pollute
    the cache.
    """
    sha = _short_sha()
    if sha in ("nogit", ""):
        return
    if _is_dirty():
        return  # dirty SHA = don't cache; the marker would be unsound.
    try:
        RUN_TESTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        marker = _cache_marker_path(layer, sha, variant)
        marker.write_text(json.dumps({
            "sha": sha,
            "layer": layer,
            "variant": variant,
            "passed_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": float(duration_seconds),
        }) + "\n")
    except OSError:
        pass  # sidecar contract — never break the run.


def is_layer_cached_green(layer: str, *, variant: str = "default") -> bool:
    """Y.2.gate.b.8.impl — True iff ``layer`` has a green cache
    marker for the current SHA + variant. Used by `cmd_up_to` when
    ``--skip-cheap`` is set to short-circuit re-runs.
    """
    if layer not in SKIPPABLE_LAYERS:
        return False
    sha = _short_sha()
    if sha in ("nogit", ""):
        return False
    if _is_dirty():
        return False  # dirty SHA = always re-run; cached state is stale.
    marker = _cache_marker_path(layer, sha, variant)
    if not marker.exists():
        return False
    try:
        raw = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    # Sanity-check the marker matches what we expect — defensive
    # against a hand-edited or stale-format file.
    if not isinstance(raw, dict):
        return False
    data = cast("dict[str, Any]", raw)
    return bool(
        data.get("sha") == sha
        and data.get("layer") == layer
        and data.get("variant", "default") == variant
        and data.get("passed_at")
    )


# Y.2.gate.m.2 — variant axis. The runner expresses variants as
# 3-axis cells `scenario × dialect × target` (`common/variant.py`);
# operators narrow the matrix via `--scenarios` / `--dialects` /
# `--targets` (or pin a single cell via `--variants=<sc>_<di>_<ta>`).
# `setup_variant` dispatches on `(spec.db.dialect, spec.target)` to
# spin up local testcontainers (`lo`) or wire the operator's external
# Aurora/Oracle (`aw`).

# Layers whose subprocess needs the variant's DB connection threaded
# through (RECON_GEN_DEMO_DATABASE_URL etc.). Unit doesn't need it.
# `app2` (b.3.impl.layer) reads the variant DB via the App2 fetcher
# (`make_tree_db_fetcher`), so it lives here.
DB_TOUCHING_LAYERS: Final = ("db", "app2", "app2_browser")

# Phase DC.3 — layers whose subprocess serves HTTPS (App2 uvicorn). The
# runner auto-mints + renews certs via ``ensure_dev_env`` before
# dispatching these layers, but ONLY when ``cfg.app2.tls`` is configured.
# Operators without the tls block see no behavior change; unit/db runs
# never hit Cloudflare. DW.5.2 — ``app2_browser`` (root e2e browser tier)
# drives App 2 servers too, so it joins the TLS set.
TLS_TOUCHING_LAYERS: Final = ("app2", "app2_browser")

# Phase DD.4 — App2-only per spike lock. The OIDC auth tests live under
# tests/e2e/app2/; the root browser tier (app2_browser) carries none.
OIDC_TOUCHING_LAYERS: Final = ("app2",)

# Y.2.gate.j.5 — Oracle container reuse. **Per-cell** name (not single
# shared) so two Oracle cells (e.g., sp_or_lo + sq_or_lo) running in
# parallel don't collide on `containers.create(name=...)` with a 409
# Conflict. Each cell's container persists across `./run_tests.sh`
# invocations under its own name; operator stops via
# `docker stop $(docker ps -q --filter name=quicksight-test-oracle-)`
# (or future `./run_tests.sh down`, Y.2.gate.l.2). PG containers stay
# ephemeral — their ~5s cold-start doesn't justify the cleanup-hygiene
# cost, and per-cell naming would just litter the daemon.
ORACLE_REUSE_CONTAINER_PREFIX: Final = "quicksight-test-oracle-"
# CB.17.k — the xdist-shared PG container's stable name. Single name
# (not a prefix family) because one container is shared across all
# workers in a `pytest -n auto` run; conftest's `pg_container_url`
# fixture does adopt-or-create against this name. Mirrored from
# `tests/conftest.py::_SHARED_PG_CONTAINER_NAME`; kept as a separate
# constant here so `_cmd_down_local` doesn't reach into the test tree.
PG_SHARED_CONTAINER_NAME: Final = "recon-gen-test-pg"  # typing-smell: ignore[recon-prefix]: Docker container name for the CB.17.k xdist-shared PG test fixture (not a cfg-prefixed AWS / DB resource ID) — stable across `pytest -n auto` workers so conftest's `pg_container_url` fixture can adopt-or-create against a single shared container; not multi-tenant and intentionally does not flow through `cfg.aws.prefixed()`
# BV.3.3 — dedicated Snapshotter-unit-test containers. Same shape as
# the shared CB.17.k pair above but a separate name family so the
# snapshotter tests (heavy schema-create / drop / CTAS+REFRESH ops)
# don't fight the shared db-tier matrix or the bv33 trainer dogfood
# walk. Mirrored from `tests/conftest.py::_SHARED_SNAP_*_CONTAINER_NAME`
# so `_cmd_down_local` doesn't reach into the test tree.
SNAP_PG_SHARED_CONTAINER_NAME: Final = "recon-gen-snap-test-pg"  # typing-smell: ignore[recon-prefix]: Docker container name for the BV.3.3 snapshotter-unit-test PG fixture (not a cfg-prefixed AWS / DB resource ID) — stable across `pytest -n auto` workers so conftest's `snapshotter_pg_container_url` fixture can adopt-or-create against a single shared container; not multi-tenant and intentionally does not flow through `cfg.aws.prefixed()`
SNAP_ORACLE_SHARED_CONTAINER_NAME: Final = "recon-gen-snap-test-oracle"  # typing-smell: ignore[recon-prefix]: Docker container name for the BV.3.3 snapshotter-unit-test Oracle fixture (not a cfg-prefixed AWS / DB resource ID) — stable across `pytest -n auto` workers so conftest's `snapshotter_oracle_container_url` fixture can adopt-or-create against a single shared container; not multi-tenant and intentionally does not flow through `cfg.aws.prefixed()`
# Pinned password matches the testcontainers `OracleDbContainer`
# behavior when `oracle_password` is explicitly set. Without pinning,
# testcontainers randomizes per invocation (`hex(randbits(24))`) and
# the adopt path can't predict the URL on subsequent runs.
def generate_db_password() -> str:
    """BX.248 — fresh random password for an ephemeral PG / Oracle container.

    Returns 28 hex chars (`secrets.token_hex(14)`). Sized to fit
    inside Oracle's 30-byte quoted-identifier limit: ALTER USER ...
    IDENTIFIED BY "<pwd>" treats the quoted form as a quoted
    identifier, capping the password body at 30 bytes
    (CE.4-followup; pre-fix `token_hex(16)` = 32 chars tripped
    ORA-00972 "identifier is too long", the password reset silently
    failed, and every login attempt counted toward
    FAILED_LOGIN_ATTEMPTS → ORA-28000 "account is locked"). 28
    chars leaves 2 bytes of headroom and still satisfies Oracle 19c's
    "alphanumeric + ≥8 chars + ≥1 letter + ≥1 digit" rule by
    construction (hex is mixed letter+digit).

    Pre-BX.248 the runner pinned a static `ORACLE_REUSE_PASSWORD`
    constant in source — that string leaked DB credentials to
    anyone with repo access AND let the same source-disclosed
    password reach the home-firewall-exposed hotchkiss.io:5433/1522
    forwards. Generating per-invocation closes that hole.

    112 bits of entropy — strong enough for an ephemeral container
    credential.
    """
    import secrets  # noqa: PLC0415 — lazy: only used by container spinup
    return secrets.token_hex(14)


def _read_pg_container_user_db(container_name: str) -> tuple[str, str]:
    """BV.3.3.d — read the live container's POSTGRES_USER + POSTGRES_DB
    out of its environment so the adopt path targets the role that
    actually exists.

    testcontainers-python's `PostgresContainer` defaults user/db/password
    all to ``test`` (see ``testcontainers/postgres/__init__.py`` —
    ``username = self.dbname = self.password = "test"`` unless overridden
    by env). The pre-BV.3.3.d adopt path hardcoded ``postgres``, which
    silently failed because no ``postgres`` role exists in the live
    container — every downstream connect attempt then hit "role
    does not exist" or "password authentication failed" with no signal
    pointing back at the rendezvous URL.

    Returns ``(user, db)``. Raises on docker-exec failure or missing
    env vars — caller is responsible for surfacing as an actionable
    error.
    """
    import subprocess  # noqa: PLC0415 — lazy
    result = subprocess.run(
        ["docker", "exec", container_name, "env"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker exec {container_name!r} env failed "
            f"(rc={result.returncode}); container is unreachable. "
            f"stderr:\n{result.stderr.decode('utf-8', errors='replace')}\n"
            f"Recovery: `docker rm -f {container_name}` and rerun."
        )
    env_lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    env = dict(
        line.split("=", 1) for line in env_lines if "=" in line
    )
    user = env.get("POSTGRES_USER")
    db = env.get("POSTGRES_DB")
    if not user or not db:
        raise RuntimeError(
            f"Container {container_name!r} env missing POSTGRES_USER "
            f"({user!r}) and/or POSTGRES_DB ({db!r}); container is in a "
            f"poison state. Recovery: `docker rm -f {container_name}` "
            f"and rerun to recreate from scratch."
        )
    return user, db


def _wait_for_pg_ready(
    container_name: str, *, timeout_seconds: float = 60.0,
) -> None:
    """#266 — block until Postgres accepts connections in the named
    container, or raise with the last pg_isready output.

    Mirror of `_wait_for_oracle_ready`. On cold-Docker boots,
    `existing.start()` returns once the container PID is running, but
    `postmaster` needs another 1-3s before accepting connections; the
    pre-fix `_read_pg_container_user_db` ran `docker exec <name> env`
    and `_reset_pg_password_via_socket` ran `psql` during that gap,
    intermittently failing with "connection refused" or "database
    system is starting up". Polls `docker exec <name> pg_isready -U
    postgres -q` (the pg_isready binary is bundled with the postgres
    image and exits 0 iff the server accepts connections; -q
    suppresses chatter). Default 60s covers postgres:17-alpine
    cold-start which typically completes in <5s.
    """
    import subprocess  # noqa: PLC0415 — lazy
    import time  # noqa: PLC0415 — lazy
    deadline = time.monotonic() + timeout_seconds
    last_rc = 0
    last_err = ""
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        result = subprocess.run(
            ["docker", "exec", container_name, "pg_isready", "-q"],
            check=False,
            capture_output=True,
        )
        last_rc = result.returncode
        last_err = result.stderr.decode("utf-8", errors="replace")
        if last_rc == 0:
            return
        # pg_isready exit codes: 0=accepting, 1=rejecting, 2=no
        # response, 3=no attempt (e.g., bad args). 1/2 = retry; 3 = bail.
        if last_rc == 3:
            raise RuntimeError(
                f"pg_isready in {container_name!r} returned rc=3 (no "
                f"attempt — config / args problem). attempts={attempts}. "
                f"stderr:\n{last_err}"
            )
        time.sleep(1.0)
    raise RuntimeError(
        f"Postgres readiness probe timed out after {timeout_seconds:.0f}s "
        f"({attempts} attempts) waiting for {container_name!r} to accept "
        f"connections. Last pg_isready rc={last_rc}. "
        f"stderr:\n{last_err}"
    )


def _verify_pg_connect(
    url: str, *, attempts: int = 5, delay: float = 1.0,
) -> None:
    """#266 — smoke-connect to the Postgres container before publishing
    the rendezvous URL. Mirror of `_verify_oracle_connect`. If we can't
    auth here, the xdist test workers won't either; better to raise
    inside the first-firing fixture with a useful message than publish
    a poisoned URL and have N workers all hit auth errors with no clue
    where the regression came from.
    """
    import psycopg  # noqa: PLC0415 — lazy
    import time  # noqa: PLC0415 — lazy
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            conn = psycopg.connect(url, connect_timeout=5)
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best-effort close
                pass
            return
        except Exception as exc:  # noqa: BLE001 — retry on any connect failure
            last_exc = exc
            if i < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(
        f"Postgres smoke-connect failed after {attempts} attempts "
        f"against {_redact_password(url)!r}: {last_exc!r}. The "
        f"rendezvous URL would have been published with credentials "
        f"the container rejects."
    )


def _reset_pg_password_via_socket(container_name: str, password: str) -> None:
    """BX.248 — force-reset the container's Postgres superuser password
    via unix socket inside the container. BV.3.3.d — discover the actual
    superuser name from POSTGRES_USER (testcontainers default is ``test``,
    not ``postgres``) and fail LOUD on subprocess errors instead of
    swallowing them.

    `psql -U <user>` over the container's local socket uses `trust`
    auth (default in pg_hba.conf for local connections), so we don't
    need to know the current password to set a new one. Used on the
    adopt path when a container exists from a prior run with an
    unknown password.

    Pre-BV.3.3.d this hardcoded ``postgres`` for both the psql `-U`
    flag and the ALTER USER target. testcontainers-python ships
    ``POSTGRES_USER=test`` by default, so the docker-exec exited
    non-zero ("role 'postgres' does not exist"); ``check=False`` +
    ``capture_output=True`` ate the error. Every subsequent connect via
    the rendezvous URL then either authn-failed (wrong user) or
    authz-failed (missing db). Mirror of the Oracle #254 fix.

    #266 — wait for postmaster to be ready before running the docker
    exec env probe + ALTER USER. On cold-Docker boots `existing.start()`
    returns once the container PID is up; pg_isready takes another
    1-3s to flip green. Without the wait, `_read_pg_container_user_db`
    intermittently hit "could not connect to server" against fresh
    starts.
    """
    import subprocess  # noqa: PLC0415 — lazy
    _wait_for_pg_ready(container_name)
    user, _db = _read_pg_container_user_db(container_name)
    result = subprocess.run(
        [
            "docker", "exec", container_name,
            "psql", "-U", user,
            "-c", f"ALTER USER {user} WITH PASSWORD '{password}';",
        ],
        check=False,
        capture_output=True,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    # libpq prints "role does not exist" to stderr; psql's `\set ON_ERROR_STOP`
    # is off by default, so a non-zero rc is the primary signal. The stderr
    # scan adds belt-and-suspenders for cases where the docker exec layer
    # itself fails (container not running, etc.).
    has_pg_error = "does not exist" in stderr or "FATAL" in stderr
    if result.returncode != 0 or has_pg_error:
        raise RuntimeError(
            f"Postgres password reset via unix socket failed for "
            f"{container_name!r} (rc={result.returncode}, user={user!r}). "
            f"This would publish a rendezvous URL whose credential does "
            f"not match the live container, breaking every downstream "
            f"connect. "
            f"psql stdout:\n{stdout}\n"
            f"psql stderr:\n{stderr}\n"
            f"Recovery: `docker rm -f {container_name}` and rerun to "
            f"recreate from scratch."
        )


_ORACLE_NOT_READY_CODES: Final = (
    "ORA-01034", "ORA-01033", "ORA-01089", "ORA-12162", "ORA-01109",
)
"""Oracle error codes that mean "the instance is still starting up, retry
later" — not authoritative failure. ORA-01034 = ORACLE not available;
ORA-01033/01089 = startup/shutdown in progress; ORA-12162 = service name
not yet registered; ORA-01109 = database mounted but NOT OPEN yet (the
gvenzl image bounces the DB during first-boot setup, so the instance
accepts sysdba connects while DUAL/ALTER still raise 01109). The
`_wait_for_oracle_ready` poll loop tolerates these; any OTHER ORA-* code
raises immediately (real config / data problem, not a timing race)."""

_ORACLE_RESET_MAX_ATTEMPTS: Final = 5
"""How many times `_reset_oracle_password_via_socket` re-waits + retries
the `ALTER USER` when it hits a still-starting code. The first-boot DB
bounce is a TOCTOU race: `_wait_for_oracle_ready` can return (DUAL
selectable) microseconds before the image bounces the DB shut, so the
ALTER then hits ORA-01109. Each attempt re-runs the (now 01109-aware)
ready-wait, which blocks through the bounce — so a handful of attempts
makes the race vanishingly unlikely without an unbounded loop."""


def _wait_for_oracle_ready(
    container_name: str, *, timeout_seconds: float = 180.0,
) -> None:
    """#266 — block until Oracle's instance accepts sysdba SELECTs in
    the named container, or raise with the last sqlplus output.

    The adopt-path (`existing.start()`) returns once the container
    process is running, but Oracle's PMON/listener/instance take 30-90s
    after that on a cold-Docker boot before SQL*Plus can connect. Without
    this wait, `_reset_oracle_password_via_socket` fires `ALTER USER`
    during the gap and hits `ORA-01034: ORACLE not available` — the
    failure we saw in `runs/20260614T151920Z-68ec16d4/unit/` after a
    Docker restart.

    Polls `SELECT 1 FROM DUAL` via in-container sysdba. Treats
    ORA-01034 / ORA-01033 / ORA-01089 / ORA-12162 as "retry"; any other
    ORA-* code raises immediately. Default timeout 180s covers Oracle
    19c cold-start on the workstation; CI runner sets longer per its
    own container-boot orchestration.
    """
    import subprocess  # noqa: PLC0415 — lazy
    import time  # noqa: PLC0415 — lazy
    probe_sql = (
        "WHENEVER SQLERROR EXIT SQL.SQLCODE;\n"
        "SELECT 1 FROM DUAL;\n"
        "EXIT;\n"
    )
    deadline = time.monotonic() + timeout_seconds
    last_stdout = ""
    last_stderr = ""
    last_rc = 0
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        result = subprocess.run(
            [
                "docker", "exec", "-i", container_name,
                "bash", "-lc", "sqlplus -s / as sysdba",
            ],
            input=probe_sql.encode(),
            check=False,
            capture_output=True,
        )
        last_rc = result.returncode
        last_stdout = result.stdout.decode("utf-8", errors="replace")
        last_stderr = result.stderr.decode("utf-8", errors="replace")
        combined = last_stdout + last_stderr
        if last_rc == 0 and "ORA-" not in combined:
            return
        # Any non-retryable ORA-* code? Surface immediately so we don't
        # waste 180s waiting for a real config problem to "resolve".
        if any(code in combined for code in _ORACLE_NOT_READY_CODES):
            time.sleep(3.0)
            continue
        if "ORA-" in combined:
            raise RuntimeError(
                f"Oracle readiness probe hit unexpected ORA error in "
                f"{container_name!r} (rc={last_rc}, attempt={attempts}). "
                f"This is a real config/data problem, not a startup "
                f"race. sqlplus stdout:\n{last_stdout}\n"
                f"sqlplus stderr:\n{last_stderr}"
            )
        # rc != 0 with no ORA-* — likely docker exec / bash error.
        # Could be transient (container just started); retry briefly.
        time.sleep(2.0)
    raise RuntimeError(
        f"Oracle readiness probe timed out after {timeout_seconds:.0f}s "
        f"({attempts} attempts) waiting for {container_name!r} to accept "
        f"sysdba SELECTs. Last rc={last_rc}. "
        f"sqlplus stdout:\n{last_stdout}\n"
        f"sqlplus stderr:\n{last_stderr}"
    )


def _reset_oracle_password_via_socket(container_name: str, password: str) -> None:
    """BX.248 — force-reset Oracle `system` user password via in-container
    sysdba auth.

    `sqlplus / as sysdba` uses OS authentication for the in-container
    `oracle` user — works regardless of the current password. The
    heredoc is wrapped in `bash -lc` so sqlplus's environment (PATH,
    ORACLE_HOME, etc.) is set up; otherwise the binary isn't on the
    default exec path.

    CE.4-followup — `ACCOUNT UNLOCK` covers the case where past
    invocations racked up failed logins and tripped the DEFAULT
    profile's `FAILED_LOGIN_ATTEMPTS=10` threshold (ORA-28000). The
    profile bump to UNLIMITED makes future stale-password retries
    during the password-rotation dance harmless. Both clauses are
    idempotent.

    #254 — fail LOUD on sqlplus failure. Previously this swallowed all
    subprocess + ORA-* errors silently, which let the rendezvous
    publish a URL whose embedded password didn't match the live
    container's password — every xdist worker subsequently hit
    ORA-01017 at connect. Now both a non-zero exit AND any ORA-* token
    in stdout raise RuntimeError with the full sqlplus output, so the
    fixture errors point at the actual problem instead of a generic
    invalid-credentials downstream.

    #266 — wait for Oracle's instance to be ready before firing
    ALTER USER. Cold-Docker boots leave a 30-90s gap where the
    container is running but Oracle's listener / PMON haven't bound;
    `_wait_for_oracle_ready` polls sysdba SELECT until they have.

    #266-followup — the gvenzl image bounces the DB shut once during
    first-boot setup, so `_wait_for_oracle_ready` can return (DUAL
    selectable) microseconds before the ALTER fires into a now-closed
    DB → ORA-01109. That's a TOCTOU race, not a real failure, so we
    re-wait + retry the reset up to `_ORACLE_RESET_MAX_ATTEMPTS` times
    on any still-starting code before failing loud. Non-transient ORA
    errors (bad creds, syntax) still fail on the first hit.
    """
    import subprocess  # noqa: PLC0415 — lazy
    import time  # noqa: PLC0415 — lazy
    sql = (
        f'WHENEVER SQLERROR EXIT SQL.SQLCODE;\n'
        f'ALTER USER system IDENTIFIED BY "{password}" ACCOUNT UNLOCK;\n'
        f'ALTER PROFILE default LIMIT '
        f'FAILED_LOGIN_ATTEMPTS UNLIMITED PASSWORD_LIFE_TIME UNLIMITED;\n'
        f'EXIT;\n'
    )
    last_rc = 0
    last_stdout = ""
    last_stderr = ""
    for _attempt in range(_ORACLE_RESET_MAX_ATTEMPTS):
        # Re-wait each attempt: a 01109-aware ready-wait blocks through
        # the first-boot bounce, so the retry fires once the DB is open
        # for good (not just transiently selectable).
        _wait_for_oracle_ready(container_name)
        result = subprocess.run(
            [
                "docker", "exec", "-i", container_name,
                "bash", "-lc", "sqlplus -s / as sysdba",
            ],
            input=sql.encode(),
            check=False,
            capture_output=True,
        )
        last_rc = result.returncode
        last_stdout = result.stdout.decode("utf-8", errors="replace")
        last_stderr = result.stderr.decode("utf-8", errors="replace")
        combined = last_stdout + last_stderr
        # sqlplus's WHENEVER SQLERROR EXIT SQL.SQLCODE sets a non-zero rc
        # on ORA-* errors; the rc-only check covers most cases, but the
        # stdout-scan adds belt-and-suspenders for cases where the
        # heredoc never reaches sqlplus (container gone, bash error) —
        # those surface here too.
        if last_rc == 0 and "ORA-" not in combined:
            return
        # Still-starting code (incl. ORA-01109 from a mid-bounce DB) →
        # the ready-wait returned during the TOCTOU window; sleep and
        # retry. Any other ORA-* is a real problem → fail loud now.
        if any(code in combined for code in _ORACLE_NOT_READY_CODES):
            time.sleep(3.0)
            continue
        break
    raise RuntimeError(
        f"Oracle password reset via sysdba failed for {container_name!r} "
        f"(rc={last_rc}, after {_ORACLE_RESET_MAX_ATTEMPTS} attempts). This "
        f"will cause downstream ORA-01017 errors on every connection attempt. "
        f"sqlplus stdout:\n{last_stdout}\n"
        f"sqlplus stderr:\n{last_stderr}"
    )


def _verify_oracle_connect(url: str, *, attempts: int = 5, delay: float = 2.0) -> None:
    """#254 — smoke-connect to the Oracle container before publishing
    the URL to the xdist rendezvous state_file. If we can't auth here,
    we won't be able to auth from the test workers either; better to
    raise inside the first-firing fixture (with a useful message)
    than to publish a poisoned URL and have N xdist workers all hit
    ORA-01017 with no signal pointing back at the root cause.

    Retries because Oracle's initial listener-ready window has a brief
    period where ALTER USER lands but new logins still 1017 for ~1-2s.
    """
    import oracledb  # noqa: PLC0415 — lazy
    import time  # noqa: PLC0415 — lazy
    from recon_gen.common.db import oracle_dsn  # noqa: PLC0415 — lazy
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            conn = oracledb.connect(oracle_dsn(url))
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best-effort close
                pass
            return
        except Exception as exc:  # noqa: BLE001 — retry on any connect failure
            last_exc = exc
            if i < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(
        f"Oracle smoke-connect failed after {attempts} attempts against "
        f"{_redact_password(url)!r}: {last_exc!r}. The rendezvous URL would "
        f"have been published with a credential the container does not "
        f"accept — every test worker would have hit ORA-01017."
    )


def _redact_password(url: str) -> str:
    """Strip the password from an oracle URL for safe error logging."""
    import re  # noqa: PLC0415 — lazy
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url)

# CB.11.b — fixed host port for the local PG container. Single PG cell at
# a time — parallel PG cells collide on this port. (Pre-DW.11 this also
# matched the operator's hotchkiss.io:5433 QS-egress forward; QuickSight
# is gone, so the port is now purely the local-container bind target.)
_LOCAL_PG_HOST_PORT: Final = 5433
_LOCAL_ORACLE_HOST_PORT: Final = 1522


_LEGACY_VARIANT_HINTS: Final[dict[str, str]] = {
    "local-pg": "--dialects=pg --targets=lo",
    "local-oracle": "--dialects=or --targets=lo",
    "local-duckdb": "--dialects=du --targets=lo",
    "default": "(no flags = full matrix; or --dialects=pg,or --targets=aw for the AWS subset)",
}


class _DuckdbHandle:
    """CA.3 — teardown handle for the local-duckdb variant.
    ``.stop()`` unlinks the per-invocation .duckdb file + temp cfg via
    the duck-typed contract ``teardown_variant`` invokes on
    testcontainer handles. CB.7-followup (2026-06-02): the
    `_SqliteHandle` sibling that originally paired with this was
    deleted in the CB.7-followup cleanup after CB.8 dropped Dialect.SQLITE.
    """

    def __init__(self, db_path: Path, cfg_path: Path) -> None:
        self.db_path = db_path
        self.cfg_path = cfg_path

    def stop(self) -> None:
        """Best-effort cleanup. Sidecar contract preserved — never raises."""
        for path in (self.db_path, self.cfg_path):
            try:
                path.unlink()
            except (FileNotFoundError, OSError):
                pass


def _setup_local_duckdb() -> tuple[dict[str, str], object | None]:
    """Create the per-invocation DuckDB DB file + minimal cfg, return
    the env overrides + handle the variant lifecycle expects.

    Allocates a tempdir; the cfg slots:

    - ``dialect: duckdb`` so emit_schema / emit_full_seed /
      refresh_matviews_sql pick the DuckDB arms of the dialect
      helpers (CA.2 landed these);
    - ``demo_database_url: duckdb:///<path>`` so connect_demo_db
      opens the file via ``duckdb.connect(duckdb_path(...))`` (CA.3
      landed the db.py arm);
    - ``aws_account_id`` + ``aws_region`` placeholders satisfying
      ``Config`` validators (the local-duckdb variant never touches
      AWS — fields required by the loader but unused).

    The DB file is created empty — ``schema apply`` populates it
    including the per-table CREATE SEQUENCE statements that feed the
    ``entry`` column DEFAULT (DuckDB has no BIGSERIAL/IDENTITY-style
    inline auto-increment; see ``common/l2/schema.py``).

    Parallelism caveat (DuckDB docs):
    https://duckdb.org/docs/current/clients/python/overview#using-connections-in-parallel-python-programs

    - **Per-invocation isolation** — each runner cell + each
      ``./run_tests.sh`` invocation allocates a *fresh* tempdir +
      ``.duckdb`` file, so multi-cell parallel runs don't share a DB.
    - **Per-thread connection** — DuckDB's connection object is NOT
      thread-safe; ``cursor()`` returns a handle to the *same*
      connection (no extra parallelism). ``connect_demo_db`` opens
      a fresh connection per call, so layer subprocesses / pytest
      workers / App2 async tasks each get their own — safe by
      construction as long as nobody caches a shared handle.
    - **pytest-xdist intra-invocation** — workers within ONE runner
      cell share the cell's ``.duckdb`` file. Parallel readers are
      fine; concurrent writers (parallel ``schema apply`` + seed
      INSERTs) will serialize at the file lock — tests that mutate
      the DB must either use xdist-worker-scoped fixtures (one DB
      per worker) or serialize via ``@pytest.mark.xdist_group``.
      CA.7 + CA.4 audit these patterns when integration tests +
      Studio land.
    """
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="qs-gen-duckdb-"))  # typing-smell: ignore[qs-gen-prefix]: tempfile dir name only — not an AWS resource ID, just disambiguates per-invocation runner-managed temp dirs from other tools' tempfiles for operator-visible cleanup
    db_path = tmp_dir / "demo.duckdb"
    cfg_path = tmp_dir / "config.duckdb.yaml"
    cfg_path.write_text(
        "aws:\n"
        "  account_id: \"111122223333\"\n"
        "  region: \"us-east-1\"\n"
        "  deployment_name: \"qsgen-duckdb\"\n"
        "db:\n"
        "  dialect: duckdb\n"
        f"  url: \"duckdb:///{db_path}\"\n"
        "  table_prefix: \"qsgen_duckdb\"\n"
    )
    env: dict[str, str] = {
        RECON_GEN_DEMO_DATABASE_URL.name: f"duckdb:///{db_path}",
        RECON_GEN_CONFIG.name: str(cfg_path),
    }
    return env, _DuckdbHandle(db_path=db_path, cfg_path=cfg_path)


def _testcontainer_logs_tail(
    container: object | str, *, max_lines: int = 40,
) -> str:
    """DD.4 adversarial-review #7 (2026-06-16) — capture a docker
    container's stdout+stderr tail and format for inclusion in a
    thin-container start-failure RuntimeError. Mirrors the Dex-side
    ``_dex_logs_tail`` pattern so PG / Oracle start failures surface
    the actual crash reason instead of just the docker daemon's 500.

    Best-effort — returns an empty string when the docker SDK is
    unavailable, the wrapped container isn't readable, or logs can't
    be fetched. The primary error is the caller's; log capture is
    enrichment.

    Accepts:
    - a testcontainers wrapper exposing ``.get_wrapped_container()``
      (PostgresContainer, OracleDbContainer)
    - a docker Container object with ``.logs()`` directly
    - a string container name; resolved via ``docker.from_env()``
      (covers operator-staged adopt failures + the Oracle stable-name
      path where the helper raises before returning the handle).
    """
    try:
        if isinstance(container, str):
            import docker  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415
            client = docker.from_env()
            docker_container: object = client.containers.get(container)
        else:
            # testcontainers wraps the docker container; both PG and Oracle
            # bases expose .get_wrapped_container().
            wrapped = getattr(container, "get_wrapped_container", None)
            docker_container = wrapped() if callable(wrapped) else container
        raw = docker_container.logs(tail=max_lines, stdout=True, stderr=True)  # type: ignore[attr-defined]  # noqa: PLC0415 — duck-typed
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            # Duck-typed object (docker SDK lacks PEP 561 stubs); ``raw`` may
            # be a str or an iterator of bytes per docker-py's ``logs(stream=)``
            # surface; stringify defensively. The type: ignore on the prior
            # line propagates Unknown, hence the cast here.
            text = str(raw)  # pyright: ignore[reportUnknownArgumentType]: docker-py's logs() return is Unknown per the type: ignore on the prior line
        if not text.strip():
            return (
                "\n  (container produced no logs — likely never ran the "
                "image's entrypoint; check the image pull + command line)"
            )
        name = getattr(docker_container, "name", "container") or "container"
        return (
            f"\n  --- docker logs {name} (tail {max_lines}) ---\n  "
            + text.replace("\n", "\n  ").rstrip()
            + "\n  --- end docker logs ---"
        )
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        return (
            f"\n  (could not fetch container logs: "
            f"{type(exc).__name__}: {exc})"
        )


def _start_thin_container(
    cfg_path: Path,
) -> tuple[dict[str, str], object | None]:
    """CB.17.d — pre-spin the cfg-matching container for the thin path.

    Returns ``(env_overrides, handle)`` where ``handle.stop()`` tears down
    at end of run. Mirrors ``setup_variant`` but for a single (operator-
    discovered) cfg rather than a per-cell ``VariantSpec``.

    Why this exists: ``cmd_thin``'s design lock relied on test-side
    pytest fixtures (``pg_container_url`` / ``oracle_container_url``) to
    provision substrate lazily. That works for tests that consume ``cfg``
    via fixture, but several db-tier test files (``test_dataset_sql_smoke``,
    ``test_inv_direct``, ``test_audit_direct``, etc.) call
    ``_CFG = _load_cfg()`` at module-import time so pytest-parametrize
    can name DataSetId-keyed test cases. Module-import bypasses the
    fixture chain entirely. To make those files work under thin, the
    runner pre-spins the substrate + exports
    ``RECON_GEN_DEMO_DATABASE_URL`` to the pytest subprocess. ``Config``'s
    env-override path picks it up at module-import time.

    The ``_PG`` / ``_OR`` env is also exported so the session-scoped
    fixtures' env-URL fast-path kicks in (they yield the runner-provided
    URL and don't re-spin). Same singleton, two consumers.

    Dispatch by ``cfg.db.dialect``:

    - POSTGRES: ``postgres:17-alpine`` testcontainer, container takes
      ~10-15s to start. ``.stop()`` tears down at end. No fixed host
      port (unlike ``setup_variant``'s legacy ``_LOCAL_PG_HOST_PORT``
      bind) so thin + legacy don't collide on 5433.
    - ORACLE: ``_get_or_start_oracle_container`` adopts a running named
      container if one exists (saves ~90-120s cold-start). Container is
      persistent — handle's ``.stop()`` is a no-op.
    - DUCKDB: ``_setup_local_duckdb`` returns a tempdir + .duckdb file
      and a handle whose ``.stop()`` unlinks both. No Docker involvement.
    """
    from recon_gen.common.config import load_config  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    peek_cfg = load_config(str(cfg_path))

    # CB.17.f — env-URL short-circuit. When the caller (CI workflow, dev
    # using a pre-spun container) pre-sets ``RECON_GEN_DEMO_DATABASE_URL_PG``
    # / ``_OR``, honor it and skip the testcontainer spin. The pytest
    # session fixtures ``pg_container_url`` / ``oracle_container_url``
    # already do this lazily; mirror the same fast-path here so the
    # *eager* pre-spin doesn't fight CI's shared-container step.
    # ``_DuckdbHandle`` / ``_PersistentContainerHandle`` stops are no-ops
    # so the handle is also unneeded — return None for the handle.
    if peek_cfg.db.dialect is Dialect.POSTGRES:
        pre_set = RECON_GEN_DEMO_DATABASE_URL_PG.get_or_none()
        if pre_set:
            url = _normalize_pg_url(pre_set)
            return (
                {
                    RECON_GEN_DEMO_DATABASE_URL.name: url,
                    RECON_GEN_DEMO_DATABASE_URL_PG.name: url,
                },
                None,
            )
    elif peek_cfg.db.dialect is Dialect.ORACLE:
        pre_set = RECON_GEN_DEMO_DATABASE_URL_OR.get_or_none()
        if pre_set:
            return (
                {
                    RECON_GEN_DEMO_DATABASE_URL.name: pre_set,
                    RECON_GEN_DEMO_DATABASE_URL_OR.name: pre_set,
                },
                None,
            )

    if peek_cfg.db.dialect is Dialect.POSTGRES:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs  # noqa: PLC0415

        # Bind container's 5432 to host's _LOCAL_PG_HOST_PORT (5433) so the
        # hotchkiss.io:5433 DDNS forward terminates here when QS reaches in
        # for the deploy/qs_api/qs_browser layers. Same contract as
        # setup_variant's pg path — single-PG-at-a-time (parallel via
        # per-cell port pool is CB.11.c work).
        # CB.17.j — `shared_preload_libraries=pg_stat_statements` so the
        # conftest's `capture_top_queries` teardown can read real perf
        # data instead of a "skipped — extension not loaded" marker.
        # PG needs the library at server startup; `CREATE EXTENSION` at
        # session time isn't enough.
        container = (
            PostgresContainer("postgres:17-alpine")
            .with_command(
                "postgres -c max_connections=300 "
                "-c shared_preload_libraries=pg_stat_statements"
            )
            .with_bind_ports(5432, _LOCAL_PG_HOST_PORT)
        )
        try:
            container.start()
        except Exception as exc:  # noqa: BLE001 — enrich with container logs then re-raise
            # DD.4 adversarial-review #7 (2026-06-16): mirror Dex's
            # _dex_logs_tail pattern — capture the PG container's
            # stdout/stderr on start failure so the operator sees the
            # actual postmaster crash reason in the runner's error,
            # not just the docker daemon's 500.
            tail = _testcontainer_logs_tail(container)
            raise RuntimeError(
                f"PostgresContainer start failed: {exc!r}{tail}"
            ) from exc
        raw_url: str = container.get_connection_url()  # type: ignore[no-untyped-call]: testcontainers method has no type annotations
        url = _normalize_pg_url(raw_url)
        env: dict[str, str] = {
            RECON_GEN_DEMO_DATABASE_URL.name: url,
            RECON_GEN_DEMO_DATABASE_URL_PG.name: url,
        }
        return env, container

    if peek_cfg.db.dialect is Dialect.ORACLE:
        # Adopt-or-create; stable name so subsequent thin runs reuse.
        try:
            url, handle = _get_or_start_oracle_container(
                "recon-gen-thin-oracle", generate_db_password(),  # typing-smell: ignore[recon-prefix]: Docker container name (not a cfg-prefixed AWS / DB resource ID) — stable across thin-path runs so adopt-or-create can find the persistent container; not multi-tenant
            )
        except Exception as exc:  # noqa: BLE001 — enrich with container logs then re-raise
            # DD.4 adversarial-review #7 (2026-06-16): same enrichment as
            # the PG path. The stable name lets us look up the container
            # by docker name even if the testcontainers handle wasn't
            # returned.
            tail = _testcontainer_logs_tail("recon-gen-thin-oracle")  # typing-smell: ignore[recon-prefix]: same Docker container name as line 2402 — stable for adopt-or-create lookup, not an AWS / DB resource ID
            raise RuntimeError(
                f"OracleDbContainer start failed: {exc!r}{tail}"
            ) from exc
        env = {
            RECON_GEN_DEMO_DATABASE_URL.name: url,
            RECON_GEN_DEMO_DATABASE_URL_OR.name: url,
        }
        return env, handle

    if peek_cfg.db.dialect is Dialect.DUCKDB:
        # _setup_local_duckdb already sets RECON_GEN_DEMO_DATABASE_URL +
        # RECON_GEN_CONFIG; no `_PG` / `_OR` suffix needed (the container
        # fixtures only check those).
        return _setup_local_duckdb()

    raise ValueError(
        f"_start_thin_container: unhandled dialect={peek_cfg.db.dialect!r}"
    )


def _sweep_test_prefixes(
    cfg_path: Path,
    container_env: dict[str, str],
    run_dir: Path,
) -> int:
    """DG.2 — scorched-earth sweep of stale per-test prefixed objects
    at container boot.

    Discovery: `tests/e2e/_isolation.py::_isolate_cfg` stamps each
    per-(module, worker) cfg with ``db_table_prefix = "<base>_<6hex>"``.
    Objects created under that prefix all start with that string —
    every base table, matview, view, index. Find them via
    ``pg_tables`` / ``pg_matviews`` / ``pg_views`` / ``pg_indexes``
    (PG) or ``user_tables`` / ``user_mviews`` / ``user_views`` /
    ``user_indexes`` (Oracle), filtering on the ``<base>_[0-9a-f]{6}_``
    pattern.

    Drop with ``CASCADE`` per kind, in dependency order: indexes →
    views → matviews → tables. Drops are idempotent (``IF EXISTS``);
    safe to re-run.

    Pre-DG.2, ``tests/e2e/_isolation.py:158`` swallowed teardown
    failures silently. Across N persistent-container CI runs the
    cumulative debris exceeded PG's ``/dev/shm`` segment + tipped
    over with ``psycopg.errors.DiskFull``. DG.1's fail-loud teardown
    prevents NEW debris from going silent; DG.2's boot sweep cleans
    up debris that already exists from before the fix shipped. See
    ``docs/audits/dg_0_db_hygiene_audit.md``.

    Returns 0 on success; non-zero on connection failure (sweep is a
    prerequisite for clean seeding — fail loud, don't continue).

    No-op for DuckDB (per-worker fresh `.duckdb` file, no shared
    state). Returns 0.
    """
    from recon_gen.common.config import Config, load_config

    sweep_dir = run_dir / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    log_path = sweep_dir / "sweep.log"

    try:
        # cfg parsing — same path the seed step takes. The
        # container_env override (RECON_GEN_DEMO_DATABASE_URL) is
        # applied during the connect call below.
        cfg = load_config(cfg_path)
    except Exception as exc:  # noqa: BLE001 — surface as triage signal
        msg = f"DG.2 sweep: failed to load cfg from {cfg_path}: {exc!r}\n"
        log_path.write_text(msg)
        print(f"runner: DG.2 sweep cfg-load failed ({exc!r})", file=sys.stderr)
        return 1

    from recon_gen.common.sql import Dialect
    if cfg.db.dialect is Dialect.DUCKDB:
        log_path.write_text(
            "DG.2 sweep: DuckDB no-op (per-worker fresh files have no shared state).\n"
        )
        return 0

    # Pattern: `<base>_<6hex>_` — the suffix shape `_isolated_cfg_key`
    # produces (sha256 of the cfg-coordinates truncated to 6 hex chars).
    base = cfg.db.table_prefix
    if cfg.db.dialect is Dialect.POSTGRES:
        pattern_sql = f"^{base}_[0-9a-f]{{6}}_"
        discoveries: list[tuple[str, str, str]] = [
            ("matview",  "SELECT matviewname FROM pg_matviews WHERE matviewname ~ %s",  "DROP MATERIALIZED VIEW IF EXISTS {q} CASCADE"),
            ("view",     "SELECT viewname    FROM pg_views    WHERE viewname    ~ %s",  "DROP VIEW IF EXISTS {q} CASCADE"),
            ("index",    "SELECT indexname   FROM pg_indexes  WHERE indexname   ~ %s",  "DROP INDEX IF EXISTS {q} CASCADE"),
            ("table",    "SELECT tablename   FROM pg_tables   WHERE tablename   ~ %s",  "DROP TABLE IF EXISTS {q} CASCADE"),
        ]
    elif cfg.db.dialect is Dialect.ORACLE:
        # Oracle case-folds; user_* views return uppercase names.
        pattern_sql = f"^{base.upper()}_[0-9A-F]{{6}}_"
        discoveries = [
            ("matview", "SELECT mview_name FROM user_mviews  WHERE REGEXP_LIKE(mview_name, :p)", "DROP MATERIALIZED VIEW {q}"),
            ("view",    "SELECT view_name  FROM user_views   WHERE REGEXP_LIKE(view_name,  :p)", "DROP VIEW {q}"),
            ("index",   "SELECT index_name FROM user_indexes WHERE REGEXP_LIKE(index_name, :p)", "DROP INDEX {q}"),
            ("table",   "SELECT table_name FROM user_tables  WHERE REGEXP_LIKE(table_name, :p)", "DROP TABLE {q} CASCADE CONSTRAINTS"),
        ]
    else:
        log_path.write_text(f"DG.2 sweep: unhandled dialect {cfg.db.dialect!r}; no-op.\n")
        return 0

    # Apply the container_env URL override so we connect to the
    # runner-spun container, not whatever cfg.db.url
    # pointed at on disk.
    url_override = container_env.get(RECON_GEN_DEMO_DATABASE_URL.name)
    cfg_for_connect: Config = (
        dataclasses_replace(cfg, db=dataclasses_replace(cfg.db, url=url_override))
        if url_override is not None else cfg
    )

    from recon_gen.common.db import connect_demo_db

    log_lines: list[str] = [f"DG.2 sweep on {cfg.db.dialect.value} (base prefix={base!r}):"]
    total_dropped = 0
    try:
        conn = connect_demo_db(cfg_for_connect)
        try:
            cur = conn.cursor()
            try:
                for kind, discovery_sql, drop_tmpl in discoveries:
                    # PG psycopg2 uses %s; oracledb uses :p — both already
                    # baked into the discovery_sql per dialect above. The
                    # bound param shape is positional/dict accordingly.
                    if cfg.db.dialect is Dialect.POSTGRES:
                        cur.execute(discovery_sql, (pattern_sql,))
                    else:
                        cur.execute(discovery_sql, {"p": pattern_sql})
                    names = [row[0] for row in cur.fetchall()]
                    if not names:
                        log_lines.append(f"  {kind}: 0 stale objects")
                        continue
                    for name in names:
                        # Identifiers come from the DB catalog — safe to
                        # interpolate (not user input). PG quotes via "...".
                        if cfg.db.dialect is Dialect.POSTGRES:
                            quoted = f'"{name}"'
                        else:
                            quoted = f'"{name}"'  # Oracle also accepts "..."
                        try:
                            cur.execute(drop_tmpl.format(q=quoted))
                        except Exception as drop_exc:  # noqa: BLE001
                            log_lines.append(
                                f"  {kind} DROP failed for {name!r}: {drop_exc!r}"
                            )
                            continue
                        total_dropped += 1
                    log_lines.append(f"  {kind}: {len(names)} stale objects swept")
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — sweep failure is operator-actionable
        msg = (
            f"DG.2 sweep: DB error against {cfg.db.dialect.value}: {exc!r}\n"
        )
        log_lines.append(msg)
        log_path.write_text("\n".join(log_lines))
        print(f"runner: DG.2 sweep failed ({exc!r})", file=sys.stderr)
        return 1

    log_lines.append(f"DG.2 sweep complete: {total_dropped} object(s) dropped.")
    log_path.write_text("\n".join(log_lines) + "\n")
    print(
        f"runner: DG.2 sweep complete on {cfg.db.dialect.value} — "
        f"{total_dropped} stale object(s) dropped"
    )
    return 0


def _seed_thin_container(
    cfg_path: Path,
    l2_path: Path,
    container_env: dict[str, str],
    run_dir: Path,
) -> int:
    """CB.17.d — seed the runner-spun container's PLAIN cfg.db.table_prefix
    before any pytest layer.

    Restored transitionally — db-tier smoke tests migrated to
    ``seeded_cfg`` (isolated prefix) but app2-tier live tests
    (test_html2_executives_live et al) still consume the PLAIN
    ``cfg`` fixture and expect populated tables. Until those migrate
    too, the runner pre-seeds the plain prefix.

    Shells::

        recon-gen schema apply -c <cfg> --l2 <l2> --execute
        recon-gen data apply   -c <cfg> --l2 <l2> --execute
        recon-gen data refresh -c <cfg> --l2 <l2> --execute

    Stdout/stderr tee'd to ``<run_dir>/seed/{stdout,stderr}.log``.
    """
    seed_dir = run_dir / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **container_env}
    stdout_path = seed_dir / "stdout.log"
    stderr_path = seed_dir / "stderr.log"

    steps: list[tuple[str, str]] = [
        ("schema", "apply"),
        ("data", "apply"),
        ("data", "refresh"),
    ]
    for verb, sub in steps:
        cmd = [
            str(_VENV_BIN / "recon-gen"), verb, sub,
            "-c", str(cfg_path),
            "--l2", str(l2_path),
            "--execute",
        ]
        print(f"runner: thin seed [{verb} {sub}] {' '.join(cmd)}")
        returncode, _ = _spawn_with_tee(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            terminal_prefix="[seed] ",
        )
        if returncode != 0:
            return returncode
    return 0


@dataclass(frozen=True)
class _PersistentContainerHandle:
    """Y.2.gate.j.5 — handle wrapper that signals "leave the container
    running at teardown". `teardown_variant` calls `.stop()` on every
    handle; for persistent containers that's a no-op so the container
    survives across `./run_tests.sh` invocations and the next run can
    adopt it via `_get_or_start_oracle_container`.

    Holds the Docker container name so the operator can find / stop /
    inspect it manually (`docker stop quicksight-test-oracle`). The
    real container handle (the testcontainers `OracleDbContainer`
    instance) is intentionally discarded — Docker keeps the container
    running independently of the Python handle.
    """

    name: str

    def stop(self) -> None:
        """No-op by design — see class docstring. Operator owns the
        lifecycle via `docker stop <name>` or future `./run_tests.sh
        down` (Y.2.gate.l.2)."""


def _get_or_start_pg_container(
    name: str, password: str,
) -> tuple[str, _PersistentContainerHandle]:
    """CB.17.k — mirror of `_get_or_start_oracle_container` for Postgres.

    Adopts a running named PG container if one exists, else creates a
    fresh one with the same stable name. Returned handle's `.stop()` is
    a no-op — containers persist across runs. Operator manages lifecycle
    via `docker stop` or `./run_tests.sh down`.

    Used by the conftest's `pg_container_url` fixture so xdist's 16
    workers all converge on a SINGLE container per pytest run (the
    Docker daemon serializes by name; the first worker to call create
    wins, followers adopt). Without this, each worker spun its own
    container — 16 PG processes for a single test session, wasting
    ~5GB RAM.
    """
    try:
        import docker  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415 — lazy
        from docker.errors import NotFound  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415
    except ImportError:
        return _start_fresh_pg_container(name, password)

    try:
        client = docker.from_env()
        existing = client.containers.get(name)
    except NotFound:
        return _start_fresh_pg_container(name, password)
    except Exception:  # noqa: BLE001 — docker daemon unreachable / socket missing → fall through
        return _start_fresh_pg_container(name, password)

    if existing.status != "running":
        try:
            existing.start()
            existing.reload()
        except Exception:  # noqa: BLE001 — restart failed → recreate
            try:
                existing.remove(force=True)
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return _start_fresh_pg_container(name, password)

    try:
        ports = existing.attrs["NetworkSettings"]["Ports"]
        host_port = int(ports["5432/tcp"][0]["HostPort"])
    except (KeyError, IndexError, TypeError, ValueError):
        try:
            existing.remove(force=True)
        except Exception:  # noqa: BLE001 — best-effort
            pass
        return _start_fresh_pg_container(name, password)

    # BX.248 — existing container was started by an earlier invocation
    # with that invocation's password (now unknown to us). Force-reset
    # via unix-socket trust auth so our caller's password becomes the
    # live one. Cheap (~50ms) and idempotent. BV.3.3.d — raises LOUD
    # on subprocess failure instead of silently leaving a stale
    # password live (mirrors the Oracle #254 fix). #266 — embedded
    # pg_isready wait inside _reset_pg_password_via_socket so cold-Docker
    # boots don't fire ALTER USER during the postmaster-warming gap.
    _reset_pg_password_via_socket(name, password)
    # BV.3.3.d — adopt path URL must match the live container's actual
    # user + db (testcontainers default = "test", not "postgres"). The
    # fresh path's _normalize_pg_url derives this from
    # `container.get_connection_url()`; adopt path reads container env
    # to converge on the same shape.
    user, db = _read_pg_container_user_db(name)
    url = f"postgresql://{user}:{password}@localhost:{host_port}/{db}"
    # #266 — verify the URL actually authenticates before returning to
    # the rendezvous. Belt-and-suspenders: the reset above raises on its
    # own failure mode, but this catches "ALTER USER landed but
    # postmaster cached the prior password" timing races + any other
    # path where the live password drifts from what we expect.
    # Mirror of `_verify_oracle_connect` (issue #254).
    _verify_pg_connect(url)
    return url, _PersistentContainerHandle(name=name)


def _start_fresh_pg_container(
    name: str, password: str,
) -> tuple[str, _PersistentContainerHandle]:
    """Spin a fresh named PG container with `pg_stat_statements` preloaded.

    Race-safety: if another worker won the name-create race, Docker's
    daemon rejects the second create with "container name already
    exists". Caller's loop in the adopt-or-create flow handles that by
    falling back to adopt.
    """
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs  # noqa: PLC0415

    # CB.17.l — restore `max_connections=300` (dropped in the CB.17.k
    # refactor). Under xdist `-n auto` the shared container takes
    # 16 workers × up-to-4-conn studio_server pools × 1-2 conns from
    # the test itself = ~96 concurrent connections at peak. PG's
    # default `max_connections=100` was leaving no slack for the
    # admin connection that `capture_top_queries`' pg_stat_statements
    # query needs at teardown time.
    # #254-followup — beef up Postgres for concurrent xdist load. Under
    # `-n auto` (16 workers on this dev box), each trainer dogfood test
    # hits `/training/reset` → drop-and-recreate v overlay tables +
    # matview rebuild, all against the SAME shared PG container. Stock
    # alpine defaults (128 MB shared_buffers, 4 MB work_mem) thrash
    # under that contention and the studio_server's response misses the
    # Playwright 30 s navigation wait — observed as a click-then-hang.
    #
    # Bump shared_buffers to 1 GB (we control the box; memory is cheap
    # vs. 30 s timeouts) and work_mem to 32 MB (matview sort space).
    # `synchronous_commit=off` is safe for a throwaway test DB and
    # eliminates fsync stalls on heavy concurrent COMMITs.
    container = (
        PostgresContainer("postgres:17-alpine", password=password)
        .with_command(
            "postgres "
            "-c max_connections=300 "
            "-c shared_buffers=1GB "
            "-c work_mem=32MB "
            "-c maintenance_work_mem=256MB "
            "-c effective_cache_size=4GB "
            "-c synchronous_commit=off "
            "-c shared_preload_libraries=pg_stat_statements"
        )
        .with_name(name)
    )
    try:
        container.start()  # type: ignore[no-untyped-call]: testcontainers .start() lacks return-type hint
    except Exception:  # noqa: BLE001 — likely a name-collision race; try adopt
        # Another worker probably won — re-attempt the full adopt path.
        return _get_or_start_pg_container(name, password)
    raw_url: str = container.get_connection_url()  # type: ignore[no-untyped-call]: testcontainers method has no type annotations
    url = _normalize_pg_url(raw_url)
    return url, _PersistentContainerHandle(name=name)


def _get_or_start_oracle_container(
    name: str, password: str,
) -> tuple[str, _PersistentContainerHandle]:
    """Y.2.gate.j.5 — adopt a running named Oracle container if one
    exists, else start a fresh one with the same stable name. Either
    way the returned handle's `.stop()` is a no-op — the container
    persists across runs. Operator manages lifecycle via Docker.

    Adopt path: `docker.from_env().containers.get(name)` succeeds AND
    the container is running. Reconstruct the connection URL from the
    container's host port (`NetworkSettings.Ports["1521/tcp"][0].HostPort`)
    + the stable password the create path used. Saves ~30-60s of
    cold-start vs. recreate.

    Create path: testcontainers' `OracleDbContainer` with
    `oracle_password=password` (pinned so the URL is deterministic on
    next adopt) + `.with_name(name)` (so adopt can find it). The
    started container's port + URL come back from
    `get_connection_url()`.

    Stopped-but-exists path: `existing.start()` resumes the container
    in place (Docker keeps the data + image layers; only network +
    process restart). Then re-extract the port mapping.

    Failure modes:
    - docker SDK not importable → fall through to testcontainers
      create path (PostgresContainer side already lazy-imports
      testcontainers; same shape).
    - Inspect data shape unexpected → assume container is unhealthy,
      recreate.
    """
    try:
        import docker  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415 — lazy: only Oracle path needs it
        from docker.errors import NotFound  # type: ignore[import-untyped]: third-party SDK lacks PEP 561 stubs  # noqa: PLC0415
    except ImportError:
        return _start_fresh_oracle_container(name, password)

    try:
        client = docker.from_env()
        existing = client.containers.get(name)
    except NotFound:
        return _start_fresh_oracle_container(name, password)
    except Exception:  # noqa: BLE001 — docker daemon unreachable / socket missing → fall through
        return _start_fresh_oracle_container(name, password)

    if existing.status != "running":
        try:
            existing.start()
            existing.reload()
        except Exception:  # noqa: BLE001 — restart failed → recreate
            try:
                existing.remove(force=True)
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return _start_fresh_oracle_container(name, password)

    try:
        ports = existing.attrs["NetworkSettings"]["Ports"]
        host_port = int(ports["1521/tcp"][0]["HostPort"])
    except (KeyError, IndexError, TypeError, ValueError):
        # Inspect shape unexpected — likely a stale container from an
        # older runner version. Recreate.
        try:
            existing.remove(force=True)
        except Exception:  # noqa: BLE001 — best-effort
            pass
        return _start_fresh_oracle_container(name, password)

    # BX.248 — existing container was started by an earlier invocation
    # with that invocation's password (now unknown to us). Force-reset
    # via in-container sysdba so our caller's password becomes the live
    # one. Idempotent; Oracle's ALTER USER accepts the same password
    # without error. #254 — raises on sqlplus failure instead of
    # silently leaving a stale password live.
    _reset_oracle_password_via_socket(name, password)
    url = (
        f"oracle+oracledb://system:{password}@localhost:{host_port}"
        f"/?service_name=FREEPDB1"
    )
    # #254 — verify the URL actually authenticates before returning it
    # to the rendezvous. Belt-and-suspenders: the reset above raises on
    # its own failure mode, but this catches "ALTER USER landed but
    # listener cached the prior password" timing races and any other
    # path where the live password drifts from what we expect.
    _verify_oracle_connect(url)
    return url, _PersistentContainerHandle(name=name)


# CB.14 — local-build image tag. Wrote by ``tools/oracle-19c/build.sh``
# from Oracle's official ``oracle/docker-images`` recipe + operator-
# downloaded binary; production-parity with AWS RDS Oracle SE2 19c.
_LOCAL_ORACLE_19C_TAG: Final = "recon-gen/oracle-19c:local"
# Fallback when the local image isn't built. Oracle 23ai, multi-arch.
_FALLBACK_ORACLE_IMAGE: Final = "gvenzl/oracle-free:23-faststart"


def _docker_image_exists_locally(tag: str) -> bool:
    """True iff Docker reports the named image in the local store."""
    import subprocess  # noqa: PLC0415
    result = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _resolve_oracle_image() -> str:
    """Pick the Oracle image: env override → local 19c build → 23ai fallback.

    Warns once per process when falling back to 23ai so operators notice
    the production-parity gap without it being noisy.
    """
    override = RECON_GEN_ORACLE_IMAGE.get_or_none()
    if override:
        return override
    if _docker_image_exists_locally(_LOCAL_ORACLE_19C_TAG):
        return _LOCAL_ORACLE_19C_TAG
    if not getattr(_resolve_oracle_image, "_warned", False):
        print(
            f"runner: oracle image — {_LOCAL_ORACLE_19C_TAG} not built; "
            f"falling back to {_FALLBACK_ORACLE_IMAGE} (Oracle 23ai). "
            f"Build the 19c image once via tools/oracle-19c/build.sh "
            f"for production-parity local testing.",
        )
        _resolve_oracle_image._warned = True  # type: ignore[attr-defined]: one-shot warning flag
    return _FALLBACK_ORACLE_IMAGE


def _start_fresh_oracle_container(
    name: str, password: str,
) -> tuple[str, _PersistentContainerHandle]:
    """j.5 — start a new named Oracle container with the stable
    password. Returns the URL + a persistent handle (`.stop()` no-op
    so the container outlives this invocation and the next run can
    adopt it).

    Image selection (CB.14):

    1. ``RECON_GEN_ORACLE_IMAGE`` env override, when set.
    2. ``recon-gen/oracle-19c:local`` if Docker reports it locally —
       production-parity with AWS RDS Oracle SE2 19c, built via
       ``tools/oracle-19c/build.sh``. Cold-start ~90-120s.
    3. ``gvenzl/oracle-free:23-faststart`` fallback — pre-initialized
       23ai, multi-arch, ~20-30s cold-start. Correct-by-construction
       parity because the codebase sticks to the conservative
       19c-portable SQL/JSON subset.

    Service name defaults to FREEPDB1 on either image.
    """
    image = _resolve_oracle_image()
    from testcontainers.core.waiting_utils import wait_for_logs  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs  # noqa: PLC0415
    from testcontainers.oracle import OracleDbContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs  # noqa: PLC0415

    if image == _FALLBACK_ORACLE_IMAGE:
        # gvenzl path — testcontainers' OracleDbContainer expects this
        # image's ``ORACLE_PASSWORD`` env var + FREEPDB1 PDB + 120s
        # default ``wait_for_logs`` timeout, all of which match. Use
        # the default integration unchanged.
        container = OracleDbContainer(image, oracle_password=password).with_name(name)
        container.start()  # type: ignore[no-untyped-call]: testcontainers .start() lacks return-type hint
        url = container.get_connection_url()
        # #254 — force-set the SYSTEM password via sysdba. testcontainers'
        # wait-for-logs signal fires before the image's startup hook
        # (setPassword.sh / equivalent) has reliably applied
        # ORACLE_PWD/ORACLE_PASSWORD to SYSTEM. Calling sysdba reset
        # is idempotent and authoritative.
        _reset_oracle_password_via_socket(name, password)
        # Smoke-connect before publishing so the rendezvous URL is
        # known-good — catches any residual timing race past the reset.
        _verify_oracle_connect(url)
        return url, _PersistentContainerHandle(name=name)

    # 19c path — Oracle's official image differs from gvenzl on three
    # axes that the default ``OracleDbContainer`` integration can't
    # bridge: it reads ``ORACLE_PWD`` (not ``ORACLE_PASSWORD``), its
    # default PDB is ``ORCLPDB1`` (not ``FREEPDB1``), and a true cold
    # start runs 3-4 min vs gvenzl's 20-30s (testcontainers' 120s
    # default ``wait_for_logs`` fires mid-init). Subclass to bridge
    # all three; pin ``ORACLE_PDB=FREEPDB1`` so the connection URL
    # shape stays identical to the fallback path.
    class _Oracle19cContainer(OracleDbContainer):
        def _configure(self) -> None:  # type: ignore[no-untyped-def]: testcontainers method has no return-type hint
            super()._configure()
            self.with_env("ORACLE_PWD", self.oracle_password)
            self.with_env("ORACLE_PDB", "FREEPDB1")

        def _connect(self) -> None:  # type: ignore[no-untyped-def]: testcontainers method has no return-type hint
            wait_for_logs(  # type: ignore[no-untyped-call]: testcontainers helper lacks return-type hint
                self, ".*DATABASE IS READY TO USE!.*", timeout=900,
            )

    container = _Oracle19cContainer(image, oracle_password=password).with_name(name)
    try:
        container.start()  # type: ignore[no-untyped-call]: testcontainers .start() lacks return-type hint
    except Exception:  # noqa: BLE001 — likely name-collision race; try adopt
        # CB.17.k — another worker won the name race. Re-attempt the
        # full adopt path; that worker's container should now show up
        # via `docker.containers.get(name)`.
        return _get_or_start_oracle_container(name, password)
    url = container.get_connection_url()
    # #254 — same authoritative reset as the gvenzl path. The 19c image
    # entry-point ships setPassword.sh, but ALTER USER timing vs.
    # "DATABASE IS READY TO USE!" log isn't ordered the way
    # testcontainers' wait expects — observed live: SYSTEM rejected
    # the ORACLE_PWD value while sysdba showed status OPEN. sysdba
    # via local socket is the cheapest authoritative path.
    _reset_oracle_password_via_socket(name, password)
    _verify_oracle_connect(url)
    return url, _PersistentContainerHandle(name=name)


def _normalize_pg_url(raw_url: str) -> str:
    """testcontainers-python returns SQLAlchemy-style URLs
    (``postgresql+psycopg2://...``) by default, but ``connect_demo_db``
    uses psycopg3 directly which rejects the ``+psycopg2`` driver
    suffix (``missing "=" after "..."`` from libpq's conninfo
    parser). Strip the suffix so the URL is the plain libpq form
    psycopg accepts.

    Oracle has its own URL shape but ``oracle_dsn()`` in
    ``common/db.py`` accepts both the SQLAlchemy form and the native
    form, so no Oracle equivalent is needed here — see
    ``setup_variant``'s ``local-oracle`` arm.
    """
    return raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)


_LOCAL_PG_CFG_CANDIDATES: Final = (
    "run/config.postgres.yaml",
)

_LOCAL_ORACLE_CFG_CANDIDATES: Final = (
    "run/config.oracle.yaml",
)


def _resolve_seed_config(candidates: tuple[str, ...]) -> Path | None:
    """Y.2.gate.b.2.impl — find a dialect-flavored cfg the seed CLI
    verbs (`schema apply` / `data apply` / `data refresh`) can use
    against a variant's container. ``candidates`` is the per-variant
    fallback list (e.g. ``("run/config.postgres.yaml",)`` for
    local-pg).

    RECON_GEN_CONFIG always wins (operator pin); the candidates list is
    the per-variant default. Returns None if nothing matches; caller
    surfaces the failure with operator-actionable guidance. An
    explicit pin at a non-existent path returns None (matches the
    existing "respect the override; surface the absence" contract)
    rather than letting the registry's must_be_file validator raise.
    """
    # Read the raw value to honor the "non-existent → None" contract
    # (registry's must_be_file validator would otherwise raise on a
    # bad explicit pin, but this code path wants a soft None).
    explicit = os.environ.get(RECON_GEN_CONFIG.name)
    if explicit:
        candidate = Path(explicit)
        if candidate.is_absolute():
            return candidate if candidate.exists() else None
        resolved = REPO_ROOT / candidate
        return resolved if resolved.exists() else None
    for relative in candidates:
        candidate = REPO_ROOT / relative
        if candidate.exists():
            return candidate
    return None


_DEFAULT_RUNNER_CFG_CANDIDATES: Final = (
    "run/config.yaml",
    "run/config.postgres.yaml",
    "run/config.oracle.yaml",
)


def _is_dirty() -> bool:
    """True if the working tree has tracked modifications (b.10 lock — tracked-only).

    Untracked files are not treated as dirty (they're usually scratch / mid-edit
    new files, not deploy-blockers).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"],
            cwd=REPO_ROOT,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode != 0


def _rel_or_abs(p: Path) -> str:
    """#741 — print-friendly path display. ``p`` is usually under
    ``REPO_ROOT`` (production) but tests/conftest.py redirects
    ``RUNS_DIR`` to a session tmp dir outside the repo, where
    ``p.relative_to(REPO_ROOT)`` raises ValueError. Fall back to
    the absolute path in that case.
    """
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def create_run_id() -> str:
    """Y.2.gate.c.1 — `<utc-ts>-<short-sha>[-dirty]`.

    Stable, sortable, includes the dirty suffix so cross-run timing diffs
    don't compare a clean run against a dirty one and claim spurious drift.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sha = _short_sha()
    suffix = "-dirty" if _is_dirty() else ""
    return f"{ts}-{sha}{suffix}"


def prune_old_runs(retain: int = RUNS_RETAIN_N, runs_dir: Path | None = None) -> list[Path]:
    """Y.2.gate.c.4 — keep the most-recent ``retain`` runs; delete the rest.

    "Most recent" = mtime (robust to dirs an operator touches). Only directories
    matching `_RUN_ID_PATTERN` are candidates — defensive: don't accidentally
    nuke unrelated files an operator parked under `runs/`.

    Returns the list of deleted paths (for tests / future telemetry).
    Idempotent: missing runs_dir → no-op; <retain runs → no-op.

    Concurrency-safe: when multi-cell fan-out runs the unit suite in
    parallel and each unit subprocess itself calls `runner.main(...)`
    (e.g., `test_up_to_creates_run_dir`), sibling workers can race on
    the same `runs/` dir. ``shutil.rmtree(old)`` could see a path the
    sibling already deleted; FileNotFoundError is benign — the work
    is done. ``stat()`` failures during the listing pass are similarly
    benign (entry vanished mid-iter); skip and move on.

    #741 — tests no longer pollute the real ``runs/``: ``tests/
    conftest.py::pytest_configure`` redirects ``RUNS_DIR`` to a
    session tmp dir at pytest startup. So under matrix fan-out the
    200+ in-process ``runner.main`` calls all prune within the
    session-tmp tree — no operator-runs/ contention, no need for an
    xdist-only short-circuit guard.
    """
    target = runs_dir if runs_dir is not None else RUNS_DIR
    if not target.exists():
        return []
    candidates: list[Path] = []
    for p in target.iterdir():
        if not (p.is_dir() and _RUN_ID_PATTERN.match(p.name)):
            continue
        try:
            p.stat()
        except FileNotFoundError:
            continue  # sibling worker deleted it between iterdir() and stat()
        candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    to_delete = candidates[retain:]
    for old in to_delete:
        # ignore_errors=True: best-effort cleanup. With multi-cell
        # parallel fan-out (and unit tests like test_up_to_creates_run_dir
        # that themselves call runner.main), sibling workers can race
        # on the same runs/ dir — `os.rmdir`/`os.unlink` inside rmtree
        # will see paths another worker just deleted. Any partial leftovers
        # get picked up by the next prune call.
        shutil.rmtree(old, ignore_errors=True)
    return to_delete


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    """Pre-process argv so ``up_to=<layer>`` and ``up_to <layer>`` both work.

    The audit + PLAN spec uses ``up_to=<layer>``; argparse subcommands want
    ``up_to <layer>``. Split the leading token if it contains ``=``.
    """
    args = list(argv)
    if args and "=" in args[0]:
        verb, value = args[0].split("=", 1)
        return [verb, value, *args[1:]]
    return args


# DL.3.3 — pytest -k's tokenizer accepts identifiers (``[A-Za-z0-9_]+``)
# joined by ``and`` / ``or`` / ``not`` / parens. Visible-name substrings
# carrying spaces ("Leaf Account", "Parent Account") are first-class for
# operators (those ARE the visual / parametrize-id strings they grep
# the codebase for) but pytest rejects them with
# ``at column N: expected end of input; got identifier``. We transform
# each whitespace-containing token into ``(word1 and word2)`` so each
# word becomes its own identifier substring AND-joined — pytest's
# substring matching then finds parametrize ids like
# ``[Leaf Account Drift]``. Operator-typed ``and`` / ``or`` / ``not``
# stay as operators; parens stay intact. Quoting / escaping was the
# obvious-but-doesn't-work fallback (pytest -k flatly rejects
# ``"Leaf Account"`` as ``string literal``).
_K_OP_TOKENS: Final = frozenset({"and", "or", "not"})


def _normalize_only_expr(expr: str | None) -> str | None:
    """Convert pytest ``-k`` expressions with space-bearing identifier
    substrings into a tokenizer-safe equivalent.

    ``"Leaf Account or Parent Account"`` →
    ``"(Leaf and Account) or (Parent and Account)"``. ``None`` and
    already-tokenizer-safe expressions pass through unchanged.
    """
    if expr is None:
        return None
    # Strip trivial outer whitespace; pytest tolerates it but the
    # tokenization below is cleaner without it.
    if not expr.strip():
        return expr
    # Walk the expression character-by-character, partitioning into
    # identifier tokens vs parens. Whitespace ends an identifier token.
    # We accumulate identifier chars into ``buf``; on a paren or
    # whitespace boundary we flush + classify ``buf``.
    out: list[str] = []
    buf: list[str] = []

    def _flush() -> None:
        if not buf:
            return
        ident = "".join(buf)
        buf.clear()
        out.append(ident)

    prev_was_space = False
    for ch in expr:
        if ch.isspace():
            _flush()
            # Collapse consecutive whitespace into a single delimiter
            # token so the run-collecting loop below has a clean
            # invariant ("space tokens are exactly one space").
            if not prev_was_space:
                out.append(" ")
                prev_was_space = True
        elif ch in "()":
            _flush()
            out.append(ch)
            prev_was_space = False
        else:
            buf.append(ch)
            prev_was_space = False
    _flush()

    # Treat consecutive identifier tokens (no operator between them,
    # e.g. ``Leaf Account``) as a single space-bearing token that
    # needs the AND-rewrite.
    pieces: list[str] = []
    i = 0
    while i < len(out):
        tok = out[i]
        if tok == " ":
            pieces.append(" ")
            i += 1
            continue
        if tok in "()" or tok in _K_OP_TOKENS:
            pieces.append(tok)
            i += 1
            continue
        # Identifier token. Greedy-collect consecutive identifier tokens
        # (separated by exactly one whitespace, not operators / parens)
        # and AND-join them if there's more than one.
        run = [tok]
        j = i + 1
        while j + 1 < len(out) and out[j] == " ":
            nxt = out[j + 1]
            if nxt in "()" or nxt in _K_OP_TOKENS or nxt == " ":
                break
            run.append(nxt)
            j += 2
        if len(run) == 1:
            pieces.append(run[0])
        else:
            pieces.append("(" + " and ".join(run) + ")")
        i = j

    normalized = "".join(pieces).strip()
    # Collapse any doubled spaces left over (defensive — the
    # tokenization above already deduped them).
    while "  " in normalized:
        normalized = normalized.replace("  ", " ")
    return normalized


# DL.3.4 — earlier-layer pytest invocations under ``--only`` collect
# zero items. Pytest alone exits 5 (NO_TESTS_COLLECTED); pytest-xdist
# escalates to exit 3 (INTERNAL_ERROR) on the same "0 items" shape
# because its scheduler's ``handle_crashitem`` path hits an
# ``assert not crashitem`` before dispatch even reaches the test
# loop (xdist 3.8 / pytest 9 combination, surfaced 2026-06-15). The
# operator-actionable answer is the same in both cases: skip this
# layer, the filter targets a later one.
_PYTEST_NO_TESTS_COLLECTED = 5
_PYTEST_INTERNAL_ERROR = 3


def _is_only_no_match_exit(exit_code: int, only: str | None) -> bool:
    """Return True iff the layer's exit code is the ``--only``-filters-
    out-all-items shape and ``--only`` is set.

    Guarded by ``only is not None`` so we don't paper over a real
    internal error / no-tests-collected condition when the operator
    didn't ask for a filter. Returns False for rc=0 (passing) and rc=1
    (real failures) — those never get masked.
    """
    if only is None:
        return False
    return exit_code in (_PYTEST_NO_TESTS_COLLECTED, _PYTEST_INTERNAL_ERROR)


def _options_from_args(args: argparse.Namespace) -> RunOptions:
    """Build a RunOptions from the argparse Namespace. Defaults are baked in
    (most flags `default=False`/`default=None` from `_build_parser`).

    Y.2.gate.c.6.xdist-safety: ``fuzz_seed_value`` is resolved here (not
    argparse) — operator overrides via ``RECON_GEN_FUZZ_SEED`` env (the canonical
    pinning channel per audit §7.11), else random per invocation.

    DL.3.3: ``only`` is normalized via ``_normalize_only_expr`` so
    operator-typed visible names with spaces (``"Leaf Account or Parent
    Account"``) pass pytest -k's identifier-only tokenizer.
    """
    return RunOptions(
        only=_normalize_only_expr(getattr(args, "only", None)),
        parallel=getattr(args, "parallel", 1),
        scenarios=getattr(args, "scenarios", None),
        dialects=getattr(args, "dialects", None),
        targets=getattr(args, "targets", None),
        variants=getattr(args, "variants", None),
        fuzz_seeds=getattr(args, "fuzz_seeds", 1),
        fuzz_seed_value=resolve_fuzz_seed_value(),
        skip_cheap=getattr(args, "skip_cheap", False),
        keep_on_failure=getattr(args, "keep_on_failure", False),
        trace_all=getattr(args, "trace_all", False),
        coverage=getattr(args, "coverage", False),
    )


# m.2.b — bundled L2 fixture lookup. ``sp`` / ``sq`` resolve to the
# package's bundled YAMLs (the same files ``docs apply --portable``
# uses); operators don't need ``tests/`` checked out. ``us`` carries
# ``spec.user_yaml`` directly. ``f<n>`` is m.3 territory — synthesized
# at runtime via ``random_l2_yaml(seed)`` and written to the per-cell
# ``run_dir`` for inspection + reproduction.
_BUNDLED_L2_DIR: Final = REPO_ROOT / "src" / "recon_gen" / "_l2_fixtures"
_NAMED_L2_FIXTURES: Final[dict[str, str]] = {
    "sp": "spec_example.yaml",
    "sq": "sasquatch_pr.yaml",
}

# m.3.a — fuzz module lives under tests/l2/. The runner imports it via
# sys.path injection. Lifting random_l2_yaml into common/l2/ is a
# follow-up; for now the runner only ever runs from a source tree, not
# from a wheel.
_FUZZ_MODULE_DIR: Final = REPO_ROOT / "tests" / "l2"


def _dump_env_access(out_path: Path) -> None:
    """Write the run's consolidated EnvVar access log to ``out_path`` as JSON.

    Sources merged:

    1. **Runner-process** accesses — via ``env_keys.dump_env_access()``.
       Covers cmd-line parsing, probe_dependencies, anything the
       runner itself reads.
    2. **Subprocess pytest** accesses — every ``<run_dir>/<layer>/env_log/
       pytest-<pid>-<rand>.json`` file dropped by the conftest
       ``pytest_sessionfinish`` hook (the runner sets
       ``RECON_GEN_ENV_LOG_DIR`` per layer; xdist workers each drop a
       file). Walked + merged here.

    Output shape:

    ``{
        "by_name": {<name>: {"read_hit": N, "read_miss": N, "write": N}},
        "by_source": {
            "runner": {<name>: {...}},
            "pytest:<layer>": {<name>: {...}},
            ...
        }
      }``

    ``by_name`` is the cross-source roll-up used by the strangler diff;
    ``by_source`` keeps the per-layer slice for finer-grained
    debugging ("which layer actually reads RECON_GEN_DB_READ_ONLY?").

    CB.17.d — diff legacy vs thin paths via:
    ``diff <(jq -S .by_name runs/<legacy>/env_access.json) \\
           <(jq -S .by_name runs/<thin>/env_access.json)``
    """
    from recon_gen.common.env_keys import dump_env_access  # noqa: PLC0415

    run_dir = out_path.parent
    by_source: dict[str, dict[str, dict[str, int]]] = {}

    def _accumulate(target: dict[str, dict[str, int]], pairs: list[Any]) -> None:  # typing-smell: ignore[explicit-any]: pairs are (name, op) tuples decoded from JSON arrays — Any is the JSON-decode shape, validated at use
        for entry in pairs:
            name, op = entry[0], entry[1]
            bucket = target.setdefault(
                name, {"read_hit": 0, "read_miss": 0, "write": 0},
            )
            bucket[op] = bucket.get(op, 0) + 1

    # 1. Runner-process accesses.
    runner_events = dump_env_access()
    runner_summary: dict[str, dict[str, int]] = {}
    _accumulate(runner_summary, [list(e) for e in runner_events])
    if runner_summary:
        by_source["runner"] = runner_summary

    # 2. Subprocess pytest accesses — walk each layer's env_log/ dir.
    for env_log_dir in sorted(run_dir.glob("**/env_log")):
        layer = env_log_dir.parent.name
        layer_summary: dict[str, dict[str, int]] = {}
        for log_file in sorted(env_log_dir.glob("pytest-*.json")):
            try:
                doc = json.loads(log_file.read_text())
                _accumulate(layer_summary, doc.get("events", []))
            except (OSError, json.JSONDecodeError):
                continue
        if layer_summary:
            by_source[f"pytest:{layer}"] = layer_summary

    # Cross-source roll-up.
    by_name: dict[str, dict[str, int]] = {}
    for source_summary in by_source.values():
        for name, counts in source_summary.items():
            bucket = by_name.setdefault(
                name, {"read_hit": 0, "read_miss": 0, "write": 0},
            )
            for op, n in counts.items():
                bucket[op] = bucket.get(op, 0) + n

    payload = {"by_name": by_name, "by_source": by_source}
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    total_accesses = sum(
        n for bucket in by_name.values() for n in bucket.values()
    )
    print(
        f"runner: wrote {_rel_or_abs(out_path)} "
        f"({len(by_name)} env keys across {len(by_source)} sources, "
        f"{total_accesses} accesses)",
    )


def _finalize_run(
    run_dir: Path,
    unit_result: LayerResult,
    cell_aggregated: Sequence[LayerResult],
    code: int,
) -> int:
    """Y.2.gate.n — write the top-level ``timings.json`` (the ``unit`` prelude
    as a run-level entry + the matrix cells' ``<spec.name>.<layer>`` durations),
    run the drift diff against the prior run, prune old runs, return ``code``.

    The prelude's ``unit`` timing is a single run-level key (not per-cell), so
    ``report_drift`` compares it run-over-run with no special-casing in
    ``compute_drift``. Used by every ``cmd_up_to`` return path (prelude-fail,
    unit-only, single-cell, multi-cell) so the run-dir is always self-contained.
    """
    top_level: list[LayerResult] = [
        LayerResult(
            layer="unit",
            exit_code=unit_result.exit_code,
            duration_seconds=unit_result.duration_seconds,
            skipped=unit_result.skipped,
        ),
        *cell_aggregated,
    ]
    collect_run_outputs(run_dir, top_level)
    print(f"runner: wrote {_rel_or_abs(run_dir / 'timings.json')}")
    # CB.17.d strangler verification — dump the EnvVar access log so
    # the legacy + thin paths can be diff'd. Both runner entries write
    # to the same `env_access.json` shape; comparison happens offline.
    _dump_env_access(run_dir / "env_access.json")
    report_drift(run_dir)
    pruned = prune_old_runs()
    if pruned:
        print(f"runner: pruned {len(pruned)} old run(s) (retained last {RUNS_RETAIN_N})")
    # Auto-emit dump-last-errors on failure so the operator doesn't have
    # to remember the subcommand (2026-05-18 — pre-fix, both Claude and
    # operators were grepping stdout.log manually even though the tool
    # surfaced the actionable assertion in 1 sec).
    if code != EXIT_SUCCESS:
        print()
        _render_failures_for_run(run_dir)
    # Structured chain summary (#268) — written regardless of pipe
    # semantics so triage tools can verify chain status without trusting
    # `tee`-piped exit codes. `./run_tests.sh ... | tee logs.txt`
    # exits with `tee`'s rc (0) when no operator has `set -o pipefail`,
    # silently lying about chain status. The run.json file + the loud
    # final-status line below are the authoritative signal.
    run_json = {
        "run_id": run_dir.name,
        "exit_code": int(code),
        "status": "PASS" if code == EXIT_SUCCESS else "FAIL",
        "layers": [
            {
                "layer": lr.layer,
                "exit_code": int(lr.exit_code),
                "duration_seconds": float(lr.duration_seconds),
                "skipped": bool(lr.skipped),
            }
            for lr in top_level
        ],
    }
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2) + "\n")
    # Loud final-status line — last line of the chain's stdout. Operators
    # piping through tee can `tail -1` for unambiguous result; CI grep
    # patterns key off this. Format keeps "FAIL" / "PASS" at fixed col
    # so log scans are easy.
    if code == EXIT_SUCCESS:
        print(f"runner: chain status: PASS — all {len(top_level)} layers green")
    else:
        failed = [lr for lr in top_level if lr.exit_code != EXIT_SUCCESS and not lr.skipped]
        first_fail = failed[0] if failed else None
        if first_fail is not None:
            print(
                f"runner: chain status: FAIL at [{first_fail.layer}] "
                f"exit={first_fail.exit_code}"
            )
        else:
            # code != 0 but no failed layer found — probably a NEEDS_OPERATOR
            # bail before any layer dispatched (probe-fail / dirty / cfg).
            print(f"runner: chain status: FAIL exit={code} (pre-chain)")
    return code


def _ensure_tls_if_configured(cfg_path: Path, layer: str) -> int:
    """DC.3 — pre-flight TLS cert reconciliation.

    When ``cfg.app2.tls`` is configured AND the chain target is in
    ``TLS_TOUCHING_LAYERS``, call ``ensure_dev_env`` to mint / renew
    the cert + key + reconcile this env's 2 managed A records under
    ``hotchkiss.io``. No-op when the tls block is absent (operator hasn't
    opted in) or the target is a pre-TLS layer (unit/db).

    Returns 0 on success / no-op; ``EXIT_NEEDS_OPERATOR`` with an
    actionable stderr message on failure.
    """
    if layer not in TLS_TOUCHING_LAYERS:
        return 0

    from recon_gen.common.config import load_config  # noqa: PLC0415 — lazy
    cfg = load_config(str(cfg_path))

    tls = cfg.app2.tls
    if tls is None:
        return 0

    from recon_gen._dev.tls import Env, ensure_dev_env  # noqa: PLC0415 — lazy

    try:
        ensure_dev_env(
            Env(tls.env),
            cert_path=Path(tls.cert_path).expanduser(),
            key_path=Path(tls.key_path).expanduser(),
            account_email=tls.account_email,
        )
    except ValueError as exc:
        print(f"runner: TLS pre-flight failed: {exc}", file=sys.stderr)
        print(
            "runner: ensure RECON_GEN_CLOUDFLARE_TOKEN is set "
            "(run/secrets.env on dev; CLOUDFLARE_TOKEN GitHub secret on CI)",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    except Exception as exc:  # noqa: BLE001 — operator-actionable bubble
        print(
            f"runner: TLS pre-flight failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        print(
            "runner: Cloudflare API / ACME challenge / public-IP discovery "
            "errored; check token scope (Zone:DNS:Edit on hotchkiss.io), "
            "Let's Encrypt rate limits, and network connectivity",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    return 0


def _ensure_oidc_if_configured(cfg_path: Path, layer: str) -> int:
    """DD.4 — pre-flight Dex OIDC IdP spinup.

    When ``cfg.auth.oidc`` is configured AND the chain target is in
    ``OIDC_TOUCHING_LAYERS``, spin/adopt the Dex container with
    scrambled-per-run credentials, mounting ``cfg.app2.tls`` cert/key
    for HTTPS. No-op when the block is absent or the target is a
    pre-app2 layer (unit/db).

    Hard-depends on DC.3 — if ``cfg.auth.oidc`` is set but
    ``cfg.app2.tls`` is None, return ``EXIT_NEEDS_OPERATOR`` with an
    actionable message pointing at ``docs/operations/tls-setup.md``.
    Dex serves HTTPS via the LE cert minted by ``ensure_dev_env``;
    no separate cert mgmt in DD.4.

    Returns 0 on success/no-op; ``EXIT_NEEDS_OPERATOR`` on failure.
    """
    if layer not in OIDC_TOUCHING_LAYERS:
        return 0

    from recon_gen.common.config import load_config  # noqa: PLC0415 — lazy
    cfg = load_config(str(cfg_path))

    if cfg.auth.oidc is None:
        return 0

    if cfg.app2.tls is None:
        print(
            "runner: OIDC pre-flight failed: cfg.auth.oidc is set but "
            "cfg.app2.tls is None. Dex needs the DC.3 LE cert to serve "
            "HTTPS. Set up cfg.app2.tls first — see "
            "docs/operations/tls-setup.md.",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR

    # Short-circuit: when RECON_GEN_DEX_URL is set (CI pre-spun shared
    # container OR a developer pointing at a manually-spun Dex), skip
    # the spinup. The runner trusts the operator's URL.
    env_url = RECON_GEN_DEX_URL.get_or_none()
    if env_url is not None:
        return 0

    from recon_gen._dev.oidc import Env, ensure_dev_idp  # noqa: PLC0415
    from recon_gen._dev.oidc.secrets import (  # noqa: PLC0415
        generate_client_secret,
        generate_user_password,
    )

    client_secret = (
        os.environ.get(cfg.auth.oidc.client_secret_env)
        or generate_client_secret()
    )
    user_password = (
        RECON_GEN_DEX_USER_PASSWORD.get_or_none()
        or generate_user_password()
    )

    try:
        ensure_dev_idp(
            Env(cfg.app2.tls.env),
            cfg=cfg,
            cert_path=Path(cfg.app2.tls.cert_path).expanduser(),
            key_path=Path(cfg.app2.tls.key_path).expanduser(),
            client_id=cfg.auth.oidc.client_id,
            client_secret=client_secret,
            redirect_uri=cfg.auth.oidc.redirect_uri,
            user_email="testuser@example.com",
            user_password=user_password,
        )
    except ValueError as exc:
        print(f"runner: OIDC pre-flight failed: {exc}", file=sys.stderr)
        print(
            "runner: ensure RECON_GEN_OIDC_CLIENT_SECRET, "
            "RECON_GEN_JWT_SECRET, RECON_GEN_DEX_USER_PASSWORD are set "
            "(run/secrets.env on dev; GitHub secrets on CI)",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    except Exception as exc:  # noqa: BLE001 — operator-actionable bubble
        print(
            f"runner: OIDC pre-flight failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        print(
            "runner: Dex container spinup / readiness probe / DC.3 cert "
            "mount errored; check Docker daemon + cfg.app2.tls cert/key "
            "existence",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    return 0


def cmd_up_to(args: argparse.Namespace) -> int:
    """Run the test chain up to and including the named layer.

    Pre-flight: probes the named layer's required deps. On any failure,
    prints the operator-actionable message and exits NEEDS_OPERATOR.

    Each layer runs ONCE as a single subprocess; ``pytest-xdist`` handles
    in-layer parallelism; session-scoped ``pg_container_url`` /
    ``oracle_container_url`` fixtures (tests/conftest.py CB.17.a)
    provision substrate on first consumption; ``isolated_cfg`` gives
    each (file, worker) its own ``deployment_name`` / ``db_table_prefix``
    suffix (CB.7 + CB.17.c).

    Cfg discovery: ``RECON_GEN_CONFIG`` env override →
    ``_DEFAULT_RUNNER_CFG_CANDIDATES``. L2 path comes from
    ``cfg.db.default_l2_instance`` (h.6) when present; missing →
    the ``qs_deployed`` fixture (tests/e2e/conftest.py) raises
    ``pytest.fail`` from the qs_api / qs_browser session start.

    Drops ``env_access.json`` per run for env-var liveness diff.

    CB.17.d (2026-06-04): replaced the legacy 13-cell variant matrix +
    ``_run_one_variant`` cell loop with this single-pytest-per-layer
    path. Per-(file, worker) isolation moved into pytest fixtures
    (``isolated_cfg``, ``seeded_cfg``). The cell-loop architecture
    + its 1500+ LOC of supporting machinery deleted in the same pass.
    """
    options = _options_from_args(args)

    failures = probe_dependencies(args.layer)
    if failures:
        for failure in failures:
            print(f"runner: probe-fail [{failure.kind}] {failure.message}", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR

    run_id = create_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"runner: run_id={run_id}")
    print(f"runner: run_dir={_rel_or_abs(run_dir)}")
    print(f"runner: up_to={args.layer}")
    if options.fuzz_seed_value is not None:
        print(f"runner: fuzz_seed={options.fuzz_seed_value} (pin via RECON_GEN_FUZZ_SEED env to repro)")

    # v14.0.0 — pin the chain's ``as_of`` anchor at chain start, before
    # any layer subprocess spawns. Exported via ``RECON_GEN_AS_OF_ANCHOR``
    # so every ``AsOfFrame.live()`` read across the chain agrees on the
    # same calendar day even when the run straddles local midnight.
    # Operator override (already-set env) wins — supports replay /
    # bisect against a pinned historical date.
    import datetime as _dt  # noqa: PLC0415
    as_of_anchor = (
        os.environ.get(RECON_GEN_AS_OF_ANCHOR.name)
        or _dt.date.today().isoformat()
    )
    print(f"runner: as_of_anchor={as_of_anchor}")

    # Resolve cfg + L2 ONCE for the whole run (vs cmd_up_to which does it
    # per cell). Deploy/qs_api/qs_browser layers need both; pytest-only
    # layers (unit/db/app2) tolerate absence (their pytest fixtures
    # discover cfg themselves via load_config's precedence chain).
    #
    # Issue #1 fix: delegate to ``_setup_thin_chain_environment`` so the
    # env-shape is built in ONE place (helper is also called by
    # ``cmd_triage``). ``warn_on_missing_l2=True`` prints the stderr
    # breadcrumb when ``cfg.db.default_l2_instance`` points at a missing
    # file.
    cfg_path = _resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)
    if cfg_path is not None:
        # DC.3 — pre-flight TLS cert reconciliation BEFORE any layer
        # dispatches. No-op when cfg.app2.tls block is absent or the
        # chain target isn't TLS-touching.
        tls_exit = _ensure_tls_if_configured(cfg_path, args.layer)
        if tls_exit != 0:
            return tls_exit
        # DD.4 — pre-flight Dex IdP spinup. No-op when cfg.auth.oidc
        # is None or the chain target isn't in OIDC_TOUCHING_LAYERS.
        # Hard-depends on DC.3 (cert/key for Dex HTTPS).
        oidc_exit = _ensure_oidc_if_configured(cfg_path, args.layer)
        if oidc_exit != 0:
            return oidc_exit
        runner_variant_env = _setup_thin_chain_environment(
            cfg_path,
            as_of_anchor=as_of_anchor,
            warn_on_missing_l2=True,
        )
    else:
        # Helper requires cfg_path; without one we still need the
        # as_of anchor exported to every layer subprocess.
        runner_variant_env = {
            RECON_GEN_AS_OF_ANCHOR.name: as_of_anchor,
        }
        print(
            "runner: no cfg found via _DEFAULT_RUNNER_CFG_CANDIDATES; "
            "deploy/qs_* layers will dispatch-skip",
            file=sys.stderr,
        )

    # CB.17.d (2026-06-04) — pre-spin the cfg-matching container so
    # module-import `_load_cfg()` calls (test_dataset_sql_smoke et al)
    # see the URL via `RECON_GEN_DEMO_DATABASE_URL`. The pytest session
    # fixtures (`pg_container_url` / `oracle_container_url`) honor the
    # `_PG` / `_OR`-suffixed env fast-path and don't re-spin. Skipped
    # for layers that don't need DB (unit-only invocations).
    container_handle: object | None = None
    container_env: dict[str, str] = {}
    if cfg_path is not None and args.layer != "unit":
        try:
            container_env, container_handle = _start_thin_container(cfg_path)
            runner_variant_env.update(container_env)
            print(
                f"runner: thin container up (dialect-matching) — "
                f"{RECON_GEN_DEMO_DATABASE_URL.name}=...exported"
            )
        except Exception as exc:  # noqa: BLE001 — container start failure should fail loud
            # CB.17.k — fail fast, don't swallow. The probe-side
            # `_probe_docker` retries cover the daemon-down lag window,
            # but `containers/<id>/start` can still 500 when the
            # daemon answers `ps` but isn't ready to honor `start`
            # (post-reboot race). Continuing here cascades into db/app2/
            # deploy/qs_* layers running with an unpopulated
            # `RECON_GEN_DEMO_DATABASE_URL` → fallback to the
            # operator-authored cfg URL → testcontainers-default
            # mismatch → 32s of confusing failures. Surface as
            # EXIT_NEEDS_OPERATOR per the Y.2.gate.h+i convention
            # (matches the seed-failure abort below).
            msg = (
                f"runner: thin container start failed ({exc!r}); "
                f"aborting chain — `docker info` + `docker ps -a` to "
                f"diagnose (post-reboot daemon may still be warming up)"
            )
            print(msg, file=sys.stderr)
            _write_synthetic_cmd_json(
                run_dir,
                layer="container_start",
                exit_code=EXIT_NEEDS_OPERATOR,
                duration_seconds=0.0,
                message=msg,
            )
            if container_handle is not None:
                try:
                    container_handle.stop()  # type: ignore[attr-defined]: duck-typed teardown contract — testcontainers Container, _DuckdbHandle, _PersistentContainerHandle all expose .stop() but share no nominal parent
                except Exception:  # noqa: BLE001 — teardown is best-effort
                    pass
            return _finalize_run(
                run_dir,
                LayerResult(
                    layer="unit", exit_code=EXIT_SUCCESS,
                    duration_seconds=0.0, skipped=True,
                ),
                [LayerResult(
                    layer="container_start",
                    exit_code=EXIT_NEEDS_OPERATOR,
                    duration_seconds=0.0,
                )],
                EXIT_NEEDS_OPERATOR,
            )

    # DG.2 — scorched-earth sweep of stale per-test prefixed objects
    # before any seed runs. Pre-DG.2, `tests/e2e/_isolation.py:158`
    # swallowed teardown failures silently; cumulative debris across
    # persistent-container CI runs eventually exhausted PG's /dev/shm
    # segment with `psycopg.errors.DiskFull`. DG.1 prevents NEW debris
    # going silent; DG.2 cleans up what's already there from before
    # the fix shipped + from any teardown that legitimately couldn't
    # fire (e.g. test process killed mid-run). See
    # `docs/audits/dg_0_db_hygiene_audit.md`.
    if container_env and cfg_path is not None:
        sweep_rc = _sweep_test_prefixes(cfg_path, container_env, run_dir)
        if sweep_rc != 0:
            msg = (
                f"runner: DG.2 sweep failed rc={sweep_rc}; aborting chain "
                f"(see runs/<id>/sweep/ for triage)"
            )
            print(msg, file=sys.stderr)
            # `_sweep_test_prefixes` already writes runs/<id>/sweep/{cmd,stdout,stderr},
            # but its cmd.json doesn't carry an `exit_code` field — the
            # failure walker treated the missing-key as exit=0 and silently
            # dropped the sweep failure. Re-stamp with the rc so the walker
            # surfaces it.
            _write_synthetic_cmd_json(
                run_dir,
                layer="sweep",
                exit_code=sweep_rc,
                duration_seconds=0.0,
                message=msg,
            )
            if container_handle is not None:
                try:
                    container_handle.stop()  # type: ignore[attr-defined]: duck-typed teardown contract — testcontainers Container, _DuckdbHandle, _PersistentContainerHandle all expose .stop() but share no nominal parent
                except Exception:  # noqa: BLE001 — teardown is best-effort
                    pass
            return _finalize_run(
                run_dir,
                LayerResult(
                    layer="unit", exit_code=EXIT_SUCCESS,
                    duration_seconds=0.0, skipped=True,
                ),
                [LayerResult(
                    layer="sweep", exit_code=sweep_rc, duration_seconds=0.0,
                )],
                sweep_rc,
            )

    # CB.17.d — seed the PLAIN cfg prefix transitionally. db-tier smoke
    # tests use the `seeded_cfg` fixture (isolated prefix per (module,
    # worker)), but app2-tier live tests (test_html2_executives_live
    # et al) still consume the PLAIN `cfg` fixture and expect populated
    # tables. Until those migrate, the runner pre-seeds the plain
    # prefix once at session start. After full migration this whole
    # block goes away — fixtures own seeding.
    l2_path_env = runner_variant_env.get(RECON_GEN_TEST_L2_INSTANCE.name)
    # CB.17.f — gate seed on `container_env` (URL populated), not on
    # `container_handle is not None`. The env-URL short-circuit
    # returns ``(env, None)``: the container was pre-started by CI /
    # the operator, so there's no handle to tear down, but the seed
    # still needs to fire against the URL.
    if (
        container_env
        and cfg_path is not None
        and l2_path_env is not None
    ):
        seed_rc = _seed_thin_container(
            cfg_path, Path(l2_path_env), container_env, run_dir,
        )
        if seed_rc != 0:
            msg = (
                f"runner: thin seed failed rc={seed_rc}; "
                f"aborting chain (see runs/<id>/seed/ for triage)"
            )
            print(msg, file=sys.stderr)
            _write_synthetic_cmd_json(
                run_dir,
                layer="seed",
                exit_code=seed_rc,
                duration_seconds=0.0,
                message=msg,
            )
            if container_handle is not None:
                try:
                    container_handle.stop()  # type: ignore[attr-defined]: duck-typed teardown contract — testcontainers Container, _DuckdbHandle, _PersistentContainerHandle all expose .stop() but share no nominal parent
                except Exception:  # noqa: BLE001 — teardown is best-effort
                    pass
            return _finalize_run(
                run_dir,
                LayerResult(
                    layer="unit", exit_code=EXIT_SUCCESS,
                    duration_seconds=0.0, skipped=True,
                ),
                [LayerResult(
                    layer="seed", exit_code=seed_rc, duration_seconds=0.0,
                )],
                seed_rc,
            )

    chain = chain_through(args.layer)
    print(f"runner: chain={chain} (thin path: one subprocess per layer)")

    layer_results: list[LayerResult] = []
    final_code = EXIT_SUCCESS
    try:
        for layer in chain:
            # Each layer gets a fresh copy of the runner-built env (DB
            # URL + container handles). No per-layer env-munge — every
            # layer runs against the same local container.
            layer_env = dict(runner_variant_env)
            result = dispatch_layer(
                layer, run_dir, options, variant_env=layer_env,
            )
            # #986 followon parity + DL.3.4 — when --only narrows to a
            # single later-layer test, earlier-layer pytest invocations
            # collect nothing. Pytest alone exits 5; pytest-xdist
            # escalates to 3 (INTERNAL_ERROR via ``assert not
            # crashitem``). Both shapes mean the same thing: the filter
            # targets a later layer, this one has nothing to do. The
            # target layer's own invocation still fails loud if the
            # expr typos.
            if _is_only_no_match_exit(result.exit_code, options.only):
                prior_rc = result.exit_code
                result = LayerResult(
                    layer=layer,
                    exit_code=0,
                    duration_seconds=result.duration_seconds,
                    skipped=True,
                )
                print(
                    f"runner: layer-skip [{layer}] rc={prior_rc} → 0 "
                    f"(--only={options.only!r} matched no tests in "
                    f"this layer, deferring to later layers)"
                )
            layer_results.append(result)
            if not result.passed and not result.skipped:
                final_code = result.exit_code
                break  # stop-on-first-failure (matches cmd_up_to's cross-layer lock)
    finally:
        if container_handle is not None:
            try:
                # Duck-typed: testcontainers Container, _DuckdbHandle,
                # _PersistentContainerHandle all expose .stop().
                container_handle.stop()  # type: ignore[attr-defined]: duck-typed teardown matching setup_variant's contract
                print("runner: thin container down")
            except Exception as exc:  # noqa: BLE001 — teardown is best-effort
                print(f"runner: thin container teardown skipped ({exc!r})", file=sys.stderr)

    # Synthesize a ``unit`` LayerResult for _finalize_run's signature
    # (legacy splits prelude vs cells; thin has no prelude). When unit
    # ran, use its real result; when it didn't (e.g., bail-before-chain),
    # use a synthetic skipped entry.
    if layer_results and layer_results[0].layer == "unit":
        unit_result = layer_results[0]
        rest = layer_results[1:]
    else:
        unit_result = LayerResult(
            layer="unit", exit_code=EXIT_SUCCESS,
            duration_seconds=0.0, skipped=True,
        )
        rest = layer_results

    return _finalize_run(run_dir, unit_result, rest, final_code)


def cmd_up(args: argparse.Namespace) -> int:
    """Boot dependencies. scope = local (default).

    Local PG / Oracle / DuckDB spin on-demand inside ``setup_variant``
    per matrix cell — there's no shared "local cluster" to pre-boot.
    Reported for symmetry with ``down``.
    """
    scope = getattr(args, "scope", "local")
    if scope in ("local", "all"):
        print(
            "runner: up — no-op "
            "(local containers spin on-demand per matrix cell; "
            "AWS RDS removed in CB.12)"
        )
        return EXIT_SUCCESS
    print(
        f"runner: unknown up scope {scope!r} "
        "(only 'local' is supported post-CB.12)",
        file=sys.stderr,
    )
    return EXIT_NEEDS_OPERATOR


def cmd_down(args: argparse.Namespace) -> int:
    """Tear down dependencies. scope = local (default).

    Destructive — requires --yes (Y.2.gate.b.14.3 destructive-op
    opt-in). Stops the named persistent local DB containers: the
    Oracle reuse-prefix family (j.5) AND the CB.17.k shared PG
    container (`recon-gen-test-pg`). The `aws` scope was removed in
    CB.11.a.2 along with RDS Aurora.
    """
    if not args.yes and not RECON_GEN_RUNNER_YES.get_or_none():
        print(
            "runner: 'down' is destructive — pass --yes "
            "(or set RECON_GEN_RUNNER_YES=1)",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    scope = getattr(args, "scope", "local")
    if scope in ("local", "all"):
        return _cmd_down_local()
    print(
        f"runner: unknown down scope {scope!r} "
        "(only 'local' is supported post-CB.12)",
        file=sys.stderr,
    )
    return EXIT_NEEDS_OPERATOR


def _probe_named_container(name: str) -> tuple[list[str], int]:
    """Anchored-regex probe for an exact-name container.

    ``docker ps --filter name=<X>`` is a substring match, which means a
    bare `recon-gen-test-pg` filter would also catch e.g.
    `recon-gen-test-pg-staging`. Anchoring with ``^X$`` keeps each
    teardown-target concern decoupled and avoids accidentally killing
    operator side-projects whose names happen to share a substring.

    Returns ``(names, exit_code)``. ``exit_code`` is ``EXIT_NEEDS_OPERATOR``
    if the probe itself failed (Docker daemon unreachable); ``EXIT_SUCCESS``
    otherwise. ``names`` is the matched-name list (zero or one entry for
    an anchored probe, but kept as a list so the caller folds it into a
    single iteration).
    """
    result = subprocess.run(
        ["docker", "ps", "--filter",
         f"name=^{name}$",
         "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(
            f"runner: docker ps ({name}) failed "
            f"(rc={result.returncode}): {result.stderr.strip()}",
            file=sys.stderr,
        )
        return [], EXIT_NEEDS_OPERATOR
    return [n for n in result.stdout.strip().splitlines() if n], EXIT_SUCCESS


def _cmd_down_local() -> int:
    """Stop persistent local containers.

    Two families today:
      * Oracle reuse-prefix containers (j.5 pattern, per-cell names
        under `quicksight-test-oracle-*`).
      * Named single-instance shared containers (each adopted-or-created
        by a session-scoped conftest fixture, each persists across
        `pytest` invocations):
        - `recon-gen-test-pg` (CB.17.k, db-tier matrix + bv33 trainer
          dogfood walk; `pg_container_url`).
        - `recon-gen-snap-test-pg` (BV.3.3, snapshotter unit tests;
          `snapshotter_pg_container_url`).
        - `recon-gen-snap-test-oracle` (BV.3.3, snapshotter unit tests;
          `snapshotter_oracle_container_url`).
        The CB.17.k shared Oracle (`recon-gen-test-oracle`) is NOT
        currently torn down here — that's a pre-existing gap from when
        the down-local hygiene pass (`9736877d`) only covered PG; in
        scope to fix later but separate from BV.3.3 snap isolation.

    Pre-CB.17.k PG was genuinely ephemeral (testcontainers tore it down
    per session); the docstring claim has been stale since the shared
    fixture landed. Symmetric teardown closes that gap. Each anchored-
    regex probe stays a separate `docker ps` call so a single
    misbehaving container doesn't take down the rest of the sweep.
    """
    result = subprocess.run(
        ["docker", "ps", "--filter",
         f"name={ORACLE_REUSE_CONTAINER_PREFIX}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(
            f"runner: docker ps failed (rc={result.returncode}): "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    names = [n for n in result.stdout.strip().splitlines() if n]

    # Anchored-name probes for the single-instance shared containers.
    # Each call is independent so the probe-set extends cleanly when
    # a new shared container is introduced (just add another name).
    for shared_name in (
        PG_SHARED_CONTAINER_NAME,
        SNAP_PG_SHARED_CONTAINER_NAME,
        SNAP_ORACLE_SHARED_CONTAINER_NAME,
    ):
        matched, probe_rc = _probe_named_container(shared_name)
        if probe_rc != EXIT_SUCCESS:
            return probe_rc
        names = [*names, *matched]

    if not names:
        print("runner: down local — no persistent local containers running")
        return EXIT_SUCCESS
    for name in names:
        print(f"runner: stopping container {name}…")
        stop_rc = subprocess.run(
            ["docker", "stop", name], capture_output=True, text=True, check=False,
        )
        if stop_rc.returncode != 0:
            print(
                f"runner: docker stop {name} failed: {stop_rc.stderr.strip()}",
                file=sys.stderr,
            )
            return EXIT_FAILURE
    return EXIT_SUCCESS


def cmd_status(args: argparse.Namespace) -> int:
    """Show what's currently running. Local-container only post-CB.12
    (the AWS RDS section is gone). --cost flag retained for CLI
    compat but no longer surfaces hourly estimates (no cloud DB cost).
    """
    print("runner: status — local containers")
    _status_local()
    return EXIT_SUCCESS


def _status_local() -> None:
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter",
         f"name={ORACLE_REUSE_CONTAINER_PREFIX}",
         "--format", "{{.Names}}\t{{.Status}}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(f"  docker ps failed (rc={result.returncode})")
        return
    rows = [r for r in result.stdout.strip().splitlines() if r]
    if not rows:
        print("  (none — no persistent local containers)")
        return
    for row in rows:
        print(f"  {row}")


def cmd_pyright(args: argparse.Namespace) -> int:
    """Y.2.gate.b.14 — run pyright directly for fast type-check iteration.

    Pyright runs via the unit layer's conftest sessionstart hook on every
    `up_to=unit` invocation, but that pulls in the full ~9s test suite.
    For tight type-check loops during editing, this verb shells out to
    `.venv/bin/pyright` directly.

    Stays behind the runner (per `b.14.2` "every sub-tool absorbed by the
    orchestrator") so an always-allow rule on `./run_tests.sh*` covers it
    — no separate Claude-Code permission for `.venv/bin/pyright`.

    Returns FAILURE on type errors so the chain-style `&&`-and-continue
    pattern works (`./run_tests.sh pyright && ./run_tests.sh up_to=db`).
    """
    cmd = [str(_VENV_BIN / "pyright")]
    if args.paths:
        cmd += list(args.paths)
    target = " ".join(args.paths) if args.paths else "(strict-include set from pyproject.toml)"
    print(f"runner: pyright {target}")
    start = time.monotonic()
    result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    duration = time.monotonic() - start
    print(f"runner: pyright rc={result.returncode} duration={duration:.2f}s")
    return EXIT_SUCCESS if result.returncode == 0 else EXIT_FAILURE


def cmd_dump_last_errors(args: argparse.Namespace) -> int:
    """Surface failing-layer assertions + missing-capture warnings
    from the most-recent run dir.

    Triage shortcut: instead of ``find runs/ → grep stdout → reconstruct
    pytest output``, walk the latest ``runs/<utc-ts>-<sha>/`` and dump
    a structured report — per failing (variant, layer) cell, with the
    pytest FAILED summary, the assertion text per failing test, and a
    pointer to (or warning about missing) AA.H.6 capture artifacts.

    Surfaces:

    - **Per failing layer**: layer name + exit code + duration +
      cell-level env (cmd.json fields: ``RECON_GEN_DEPLOYMENT_NAME``,
      ``RECON_GEN_FUZZ_SEED``, ``RECON_GEN_TEST_L2_INSTANCE``).
    - **Per failing test**: the ``FAILED ...`` summary line + the
      matched ``____ <test_id> ____`` traceback block from
      ``stdout.log`` (truncated at the next ``____`` / ``=====``).
    - **Capture-artifact pointer**: ``$RECON_GEN_RUN_DIR/browser/<sanitized
      test_id>/`` paths, with a loud warning if AA.H.6's 6 files
      (screenshot.png / dom.html / console.txt / network.txt /
      qs_errors.txt / trace.zip) are missing — AA.H.10 wired the hook
      to all three QS-driver fixtures, so a missing capture is a
      regression worth flagging.

    Use ``--run <run-id>`` to pick a specific run (e.g.
    ``20260516T203824Z-914fc4c``); default = latest by mtime. Use
    ``--variant <name>`` to narrow to one cell.

    Exit code: always ``EXIT_SUCCESS`` — this is a triage tool, not a
    gate. The caller cares about the chain's exit; this just helps
    them read it faster.
    """
    runs_dir = RUNS_DIR
    if not runs_dir.exists():
        print("runner: no runs/ dir — no chain has been run yet.",
              file=sys.stderr)
        return EXIT_SUCCESS

    # Resolve target run dir.
    if args.run:
        run_dir = runs_dir / args.run
        if not run_dir.is_dir():
            print(
                f"runner: --run {args.run!r} not found under {runs_dir}",
                file=sys.stderr,
            )
            return EXIT_NEEDS_OPERATOR
    else:
        candidates = [
            p for p in runs_dir.iterdir()
            if p.is_dir() and _RUN_ID_PATTERN.match(p.name)
        ]
        if not candidates:
            print(
                "runner: no runs found under runs/ (looked for "
                "<utc-ts>-<sha>[-dirty] dirs).",
                file=sys.stderr,
            )
            return EXIT_SUCCESS
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        run_dir = candidates[0]

    if args.variant is not None:
        print(
            f"runner: --variant {args.variant!r} ignored — the 13-cell "
            f"matrix was retired in CB.17.d; the thin path produces one "
            f"set of layer dirs per run.",
            file=sys.stderr,
        )
    _render_failures_for_run(run_dir)
    return EXIT_SUCCESS


def _write_synthetic_cmd_json(
    run_dir: Path,
    *,
    layer: str,
    exit_code: int,
    duration_seconds: float,
    message: str,
) -> None:
    """Stamp a synthetic ``<run_dir>/<layer>/cmd.json`` for failures that
    happen OUTSIDE the per-layer pytest dispatch — container_start,
    sweep, seed. The chain summary walker keys off ``cmd.json``'s
    ``exit_code`` field; without this, those failures were invisible to
    ``dump-last-errors`` even though the chain aborted on them.

    Stamps cmd.json with the synthesized exit_code. If a stdout.log
    already exists in the dir (sweep + seed write their own subprocess
    log), preserve it. Only writes a placeholder stdout.log when none
    exists (container_start fails before any subprocess runs).
    """
    layer_dir = run_dir / layer
    layer_dir.mkdir(parents=True, exist_ok=True)
    # If the failing path already wrote a cmd.json (sweep / seed do),
    # merge our exit_code into it instead of clobbering — preserves
    # the cmd, cwd, env_overrides fields the original write captured.
    cmd_path = layer_dir / "cmd.json"
    existing: dict[str, Any] = {}
    if cmd_path.is_file():
        try:
            loaded = json.loads(cmd_path.read_text())
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            existing = cast(dict[str, Any], loaded)
    cmd_meta: dict[str, Any] = {
        **existing,
        "layer": layer,
        "synthetic": True,
        "exit_code": int(exit_code),
        "duration_seconds": float(duration_seconds),
        "message": message,
    }
    cmd_path.write_text(json.dumps(cmd_meta, indent=2) + "\n")
    stdout_path = layer_dir / "stdout.log"
    if not stdout_path.is_file():
        stdout_path.write_text(message + "\n")


def _render_failures_for_run(run_dir: Path) -> bool:
    """Render the failing-layers report for ``run_dir`` to stdout.

    Returns ``True`` if at least one failing layer was found, else ``False``.

    Shared by ``cmd_dump_last_errors`` (operator-invoked triage) and
    ``_finalize_run`` (auto-emit on chain failure, 2026-05-18 per user
    request — saves the operator from remembering the subcommand).
    """
    print(f"# Failing layers in {run_dir.name}")
    print()

    # Collect (display_label, layer_name, cell_dir, layer_dir) tuples
    # by scanning both runner shapes:
    #
    #   - **Thin path** (single-cell `up_to=<layer>` runs): layer dirs
    #     live directly under run_dir (`runs/<id>/unit/cmd.json`).
    #     Display label is just `<layer>`; cell_dir is run_dir itself
    #     (capture-artifact resolution looks one level up).
    #   - **Matrix path**: layer dirs live under a cell variant subdir
    #     (`runs/<id>/sp_pg_lo/db/cmd.json`). Display label is
    #     `<cell>/<layer>`; cell_dir is the variant subdir.
    #
    # A directory is "thin-path layer" iff `<dir>/cmd.json` is a file.
    # The `_prelude` cell still walks one level deeper (its layer dirs
    # carry `cmd.json` inside `_prelude/<layer>/`, not `_prelude/cmd.json`).
    layer_entries: list[tuple[str, str, Path, Path]] = []
    for sub in sorted(run_dir.iterdir()):
        if not sub.is_dir():
            continue
        # Thin-path: top-level directory IS a layer (has cmd.json).
        if (sub / "cmd.json").is_file():
            layer_entries.append((sub.name, sub.name, run_dir, sub))
            continue
        # Matrix-path or prelude: walk one level deeper for layer dirs.
        # Kept for backward compat reading old run dirs from before
        # CB.17.d retired the 13-cell matrix.
        for child in sorted(sub.iterdir()):
            if not child.is_dir():
                continue
            if (child / "cmd.json").is_file():
                layer_entries.append(
                    (f"{sub.name}/{child.name}", child.name, sub, child),
                )

    found_any_failure = False
    for display_label, layer, cell_dir, layer_dir in layer_entries:
        if layer in ("timings", "db-perf", "l2", "seed"):
            # Auxiliary subdirs, not chain layers.
            continue
        cmd_json_path = layer_dir / "cmd.json"
        if not cmd_json_path.is_file():
            continue
        cmd_json = json.loads(cmd_json_path.read_text())
        # Treat MISSING `exit_code` as a failure, not a pass. A cmd.json
        # without `exit_code` means the layer never finished writing —
        # killed mid-run, machine rebooted, OOM, etc. Pre-fix the
        # `cmd_json.get("exit_code", 0)` default silently treated this
        # as exit=0 (a layer SKIP, the only legitimate exit=0 path) and
        # the chain summary said "all clean" while the run was half-baked.
        if "exit_code" not in cmd_json:
            found_any_failure = True
            duration = cmd_json.get("duration_seconds")
            duration_str = f"{duration:.1f}s" if duration else "?"
            print(
                f"## [{display_label}] exit=? duration={duration_str} "
                f"(cmd.json incomplete — subprocess never finished)"
            )
            print()
            continue
        exit_code = int(cmd_json.get("exit_code") or 0)
        if exit_code == 0:
            continue
        found_any_failure = True
        duration = cmd_json.get("duration_seconds")
        duration_str = f"{duration:.1f}s" if duration else "?"
        hang_kill = bool(cmd_json.get("hang_kill"))
        hang_marker = " HANG-KILLED" if hang_kill else ""
        print(
            f"## [{display_label}]{hang_marker} "
            f"exit={exit_code} duration={duration_str}"
        )
        print()
        if hang_kill:
            threshold = cmd_json.get("hang_threshold_seconds", "?")
            print(
                f"- Subprocess killed by hang watchdog: stdout stuck "
                f"for >{threshold}s. Inspect `stderr.log` for the "
                f"faulthandler thread dump at the time of the hang."
            )
            print()
        env = cmd_json.get("env_overrides", {})
        # Surface the high-signal env values — operator can derive
        # rest from the run-id + cmd.json directly.
        for key in (
            "RECON_GEN_DEPLOYMENT_NAME", "RECON_GEN_FUZZ_SEED",
            "RECON_GEN_TEST_L2_INSTANCE",
        ):
            if key in env:
                print(f"- `{key}={env[key]}`")
        print()
        stdout_log = layer_dir / "stdout.log"
        if not stdout_log.is_file():
            print("(no stdout.log)")
            print()
            continue
        stdout = stdout_log.read_text(errors="replace")
        _dump_pytest_failures(stdout)
        _dump_capture_status(cell_dir, layer_dir, stdout)
    if not found_any_failure:
        print("(no failing layers in this run — all clean)")
    return found_any_failure


_FAILED_LINE_RE: Final = re.compile(r"^FAILED (?P<nodeid>\S+)(?: - (?P<reason>.+))?$", re.MULTILINE)
"""Matches pytest's ``FAILED tests/e2e/test_X.py::test_Y[param] - reason``
summary lines (one per failure, emitted at end of run)."""

_FAILURE_BLOCK_RE: Final = re.compile(
    r"^_+\s+(?P<name>\S+)\s+_+\s*$\n(?P<body>.*?)(?=^_+\s+\S+\s+_+\s*$|^=+\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
"""Matches pytest's ``______ test_name ______`` block headers and
captures everything until the next block header or a summary divider."""


def _dump_pytest_failures(stdout: str) -> None:
    """Extract + print every FAILED test's name + traceback block."""
    failed = list(_FAILED_LINE_RE.finditer(stdout))
    if not failed:
        # Layer failed but pytest didn't surface FAILED lines — show
        # the tail (likely a non-pytest crash: docker-compose error,
        # AWS API exception, etc.).
        tail = "\n".join(stdout.splitlines()[-30:])
        print("### Non-pytest failure — stdout tail (30 lines)")
        print()
        print("```")
        print(tail)
        print("```")
        print()
        return

    # Index per-test bodies by the unparametrized test name (which is
    # what the block header carries — pytest's parametrize shows the
    # full ``test[param]`` in FAILED but the section header uses just
    # the param fragment).
    blocks: dict[str, str] = {}
    for m in _FAILURE_BLOCK_RE.finditer(stdout):
        blocks[m.group("name")] = m.group("body").strip()

    print(f"### {len(failed)} FAILED test(s)")
    print()
    for m in failed:
        nodeid = m.group("nodeid")
        reason = (m.group("reason") or "").strip()
        # The block header drops the file prefix + `::` and renders
        # the parametrized form as ``test_name[param]`` (matching the
        # nodeid's tail). Walk both candidates.
        short = nodeid.split("::")[-1]
        body = blocks.get(short) or blocks.get(nodeid.split("/")[-1].replace(".py::", " "))
        print(f"#### `{nodeid}`")
        if reason:
            print(f"- **reason:** {reason}")
        print()
        if body:
            # Truncate to ~50 lines — full traceback is in stdout.log.
            lines = body.splitlines()
            shown = "\n".join(lines[:50])
            print("```")
            print(shown)
            print("```")
            if len(lines) > 50:
                print(
                    f"_(truncated; {len(lines) - 50} more lines in "
                    "stdout.log)_"
                )
        print()


def _dump_capture_status(cell_dir: Path, layer_dir: Path, stdout: str) -> None:
    """For a failing app2_browser layer, check whether AA.H.6 capture
    artifacts landed for each failed test. Print a warning if any
    failed test has no matching capture dir — that's an AA.H.10
    regression worth investigating."""
    if layer_dir.name != "app2_browser":
        return
    # AA.H.6 captures land at `$RECON_GEN_RUN_DIR/browser/<test_id>/`
    # (common/browser/helpers.py:185+368+432), NOT at
    # `app2_browser/<test_id>/`. The runner walks `runs/<id>/browser/`
    # (cell_dir == run_dir in the thin path), not the layer dir.
    browser_capture_root = cell_dir / "browser"
    failed = list(_FAILED_LINE_RE.finditer(stdout))
    if not failed:
        return
    expected_files = {
        "screenshot.png", "dom.html", "console.txt",
        "network.txt", "qs_errors.txt", "trace.zip",
    }
    missing_captures: list[str] = []
    for m in failed:
        nodeid = m.group("nodeid")
        # Sanitization mirrors common.browser.helpers._sanitize_test_id —
        # ``/`` and ``::`` collapse to ``_``, ``.py`` strips, then any
        # non-[A-Za-z0-9_\-\[\].] char collapses to ``_``. Reproducing
        # the sanitizer's exact behavior here avoids a runner-side
        # import of the browser-helpers module (which would drag
        # Playwright into the runner's import graph).
        slug = re.sub(
            r"[^A-Za-z0-9_\-\[\].]+", "_",
            nodeid.replace("/", "_").replace("::", "__").replace(".py", ""),
        )
        candidate = browser_capture_root / slug
        if not candidate.is_dir():
            missing_captures.append(nodeid)
            continue
        present = {p.name for p in candidate.iterdir()}
        if not (expected_files & present):
            missing_captures.append(nodeid)
    if missing_captures:
        print("### ⚠ AA.H.6 capture artifacts missing")
        print()
        print(
            "These failed tests have NO capture dir under "
            f"`{browser_capture_root}/<test_id>/`. AA.H.10 wired the "
            "hook into all three QS-driver fixtures; a missing capture "
            "is a regression — check the fixture wiring."
        )
        print()
        for nodeid in missing_captures:
            print(f"- `{nodeid}`")
        print()


# ---------------------------------------------------------------------------
# triage / triage-down — interactive PDB session inside a detached GNU screen.
# ---------------------------------------------------------------------------
#
# Locked design: docs/audits — `triage` spawns ONE long-lived screen session
# (name fixed at `recon-gen-triage`) running pytest under --pdb against a
# single nodeid; operator attaches via ``screen -x recon-gen-triage``. State
# (run_id, nodeid, layer, container handle metadata) persists to
# ``runs/.triage-state.json`` so ``triage-down`` can tear everything down
# without re-discovering it.

_TRIAGE_SCREEN_NAME: Final = "recon-gen-triage"  # typing-smell: ignore[recon-prefix]: GNU screen session name (not a cfg-prefixed AWS / DB resource ID) — stable across triage spawn/teardown so `triage-down` can find the session by fixed name; not multi-tenant and intentionally does not flow through `cfg.aws.prefixed()`
_TRIAGE_STATE_FILE: Final = RUNS_DIR / ".triage-state.json"

# Issue #9 fix: resolve `screen` via PATH instead of hardcoding
# `/usr/bin/screen` (legacy macOS 4.00.03). chotchki's box has
# `brew install screen` 5.0.1 at `/opt/homebrew/bin/screen`; PATH
# resolution lets brew's newer build win without per-host overrides.
_screen_bin_cache: str | None = None


def _resolve_screen_bin() -> str:
    """Return the resolved ``screen`` binary path (PATH-discovered, cached).

    Raises ``RuntimeError`` when ``screen`` isn't installed — triage
    requires the GNU screen tool. Cached after the first lookup so
    repeated calls don't re-pay the PATH walk.
    """
    global _screen_bin_cache
    if _screen_bin_cache is not None:
        return _screen_bin_cache
    resolved = shutil.which("screen")
    if resolved is None:
        raise RuntimeError(
            "runner: `screen` not found on PATH — install via your "
            "package manager (`brew install screen` on macOS, "
            "`apt install screen` on Debian/Ubuntu); triage requires "
            "GNU screen for the detached pdb session"
        )
    _screen_bin_cache = resolved
    return resolved

# Root-e2e parametrized test files. Per the design (rule 5): these files live
# at tests/e2e/ root and partition by `[qs, app2]` parametrize ids. The
# qs_browser layer's pytest invocation is a strict superset of app2's
# prerequisites, so default to qs_browser when the nodeid matches; operator
# can downshift via ``--layer=app2`` when they know the test is only
# app2-parametrized.
_ROOT_E2E_PARAMETRIZED_PREFIXES: Final[tuple[str, ...]] = (
    "test_l1_",
    "test_l2ft_",
    "test_inv_",
    "test_exec_",
    "test_dashboard_driver",
    "test_cq_picker_",
    "test_studio_",
    "test_parameter_anchored_sheets",
    # DW.5.2 — dropped ``test_audit_`` (no root audit files; they live in
    # the db/ + app2/ tier dirs) and ``test_db3_parity_snaps`` (the
    # QS-vs-App2 parity capture, deleted with QuickSight).
)

# Unit-layer prefixes (no DB / no AWS). Audit + data dirs are routed here as
# the safe floor per design rule 7 (gap-handling).
_UNIT_LAYER_PREFIXES: Final[tuple[str, ...]] = (
    "tests/unit/",
    "tests/json/",
    "tests/cli/",
    "tests/docs/",
    "tests/schema/",
    "tests/l2/",
    "tests/audit/",
    "tests/data/",
)


def _infer_layer_from_nodeid(nodeid: str) -> str | None:
    """Map a pytest nodeid → layer via path-prefix lookup.

    Returns None when no rule matches; ``cmd_triage`` maps that to
    EXIT_CONFIG_ERROR with a "pass --layer=<X>" hint.

    Normalization: strip leading ``./`` and any ``::selector`` suffix
    BEFORE prefix matching (selectors / parametrize ids are irrelevant).
    Absolute paths return None so ``cmd_triage`` rejects them — pytest
    nodeids are repo-relative and silently coercing an absolute path
    would hide operator typos.

    Order of rules (first match wins) matches the design lock:
      1. tests/e2e/agreement/  → agreement
      2. tests/e2e/app2/       → app2
      3. tests/e2e/db/         → db
      4. tests/e2e/<root parametrized file> → app2_browser
      5. tests/{unit,json,cli,docs,schema,l2}/ → unit
      6. tests/{audit,data}/   → unit (safe-floor fallback)
      7. otherwise → None

    DW.5.2 — the ``qs_browser`` + ``qs_api`` subdir rules retired with
    QuickSight; the root parametrized e2e files (now app2-only) infer to
    the ``app2_browser`` terminal tier.
    """
    if not nodeid:
        return None
    # Strip ::selector suffix; keep only the file path for prefix matching.
    file_path = nodeid.split("::", 1)[0]
    # Strip leading "./" if present.
    if file_path.startswith("./"):
        file_path = file_path[2:]
    # Absolute paths are operator error — reject by returning None.
    if file_path.startswith("/"):
        return None
    # Rules 1-3: per-tier subdirs.
    if file_path.startswith("tests/e2e/agreement/"):
        return "agreement"
    if file_path.startswith("tests/e2e/app2/"):
        return "app2"
    if file_path.startswith("tests/e2e/db/"):
        return "db"
    # Rule 4: root parametrized files → the app2_browser terminal tier.
    if file_path.startswith("tests/e2e/"):
        filename = file_path[len("tests/e2e/"):].split("/", 1)[0]
        # Strip .py for prefix-match against the parametrized-file list.
        stem = filename[:-3] if filename.endswith(".py") else filename
        for prefix in _ROOT_E2E_PARAMETRIZED_PREFIXES:
            if stem.startswith(prefix) or stem == prefix:
                return "app2_browser"
        return None
    # Rules 5-6: pytest-only trees.
    for prefix in _UNIT_LAYER_PREFIXES:
        if file_path.startswith(prefix):
            return "unit"
    return None


def _screen_session_exists(name: str) -> bool:
    """True when a GNU screen session named ``name`` is alive.

    ``screen -ls <name>`` rc semantics shifted between 4.0 and 4.6 — rc=1
    when nothing matches on 4.00.03, but the 4.6+ behavior isn't
    consistent. Use string matching on the listing output (the session
    line looks like ``\t12345.recon-gen-triage\t(Detached)`` — tab- or
    space-separated depending on screen version).
    """
    result = subprocess.run(
        [_resolve_screen_bin(), "-ls", name],
        capture_output=True, text=True, check=False,
    )
    return f".{name}\t" in result.stdout or f".{name}  " in result.stdout


def _screen_kill(name: str) -> bool:
    """Kill the named screen session. Idempotent — returns True when
    the session was already gone (matches the design lock).

    ``screen -S <name> -X quit`` returns rc=1 when no matching session
    exists; the stderr / stdout carries "No screen session found".
    Treat that as success so ``triage-down`` is safe to run repeatedly.
    """
    result = subprocess.run(
        [_resolve_screen_bin(), "-S", name, "-X", "quit"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return True
    if (
        "No screen session found" in result.stderr
        or "No screen session" in result.stdout
    ):
        return True
    return False


def _write_triage_state(state: dict[str, Any]) -> None:
    """Persist the triage state file. Caller owns the schema."""
    _TRIAGE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TRIAGE_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _read_triage_state() -> dict[str, Any] | None:
    """Read the triage state file. Returns None when absent;
    raises ValueError when present-but-malformed (so ``cmd_triage_down``
    bails to EXIT_NEEDS_OPERATOR rather than silently fall back to
    "kill the well-known session name").
    """
    if not _TRIAGE_STATE_FILE.is_file():
        return None
    try:
        loaded = json.loads(_TRIAGE_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"triage state file {_TRIAGE_STATE_FILE} is unparseable: {exc}"
        ) from exc
    if not isinstance(loaded, dict):
        raise ValueError(
            f"triage state file {_TRIAGE_STATE_FILE} is not a JSON object"
        )
    return cast(dict[str, Any], loaded)


def _setup_thin_chain_environment(
    cfg_path: Path,
    *,
    as_of_anchor: str,
    warn_on_missing_l2: bool = False,
) -> dict[str, str]:
    """Build the runner_variant_env dict for the thin chain — mirrors
    the lines 3434-3470 block in ``cmd_up_to``.

    Returns the env-override dict the caller threads into subprocess
    spawns. Doesn't touch ``os.environ``. Doesn't start any containers
    (that's ``_start_thin_container``); doesn't seed (that's
    ``_seed_thin_container``).

    Used by both ``cmd_up_to`` (via the in-line block) and ``cmd_triage``
    (which calls this helper directly). Single source of truth for the
    env-shape so the two verbs can't drift apart.

    ``warn_on_missing_l2``: when True, prints a stderr warning when
    ``cfg.db.default_l2_instance`` points at a missing file. cmd_up_to
    surfaces this; cmd_triage silently elides the L2 env entry (its own
    dispatch gates handle the missing-L2 path).
    """
    runner_variant_env: dict[str, str] = {
        RECON_GEN_AS_OF_ANCHOR.name: as_of_anchor,
        RECON_GEN_CONFIG.name: str(cfg_path),
    }
    try:
        from recon_gen.common.config import load_config  # noqa: PLC0415
        peek_cfg = load_config(str(cfg_path))
        l2_default = peek_cfg.db.default_l2_instance
        if l2_default:
            l2_path = (
                REPO_ROOT / l2_default
                if not Path(l2_default).is_absolute()
                else Path(l2_default)
            )
            if l2_path.exists():
                runner_variant_env[RECON_GEN_TEST_L2_INSTANCE.name] = str(l2_path)
            elif warn_on_missing_l2:
                print(
                    f"runner: cfg.db.default_l2_instance={l2_default!r} not found on disk",
                    file=sys.stderr,
                )
    except Exception as exc:  # noqa: BLE001 — peek failure shouldn't gate the run
        print(f"runner: cfg peek for L2 discovery failed ({exc!r}); continuing")
    return runner_variant_env


def cmd_triage(args: argparse.Namespace) -> int:
    """Spawn a detached GNU screen session running ``pytest --pdb``
    against a single nodeid. Operator attaches via
    ``screen -x recon-gen-triage`` to drive pdb interactively.

    Pre-flight sequence (mirrors ``cmd_up_to`` minus the matrix):
      1. Infer layer from nodeid (or honor ``--layer`` override).
      2. Reject existing session unless ``--force``.
      3. Probe deps + dirty-tree gate (matches cmd_up_to).
      4. Resolve cfg + L2.
      5. Spin thin container (for layer != unit).
      6. Write QS-side cfg (for layers that touch QS).
      7. DG.2 sweep + seed (idempotent + cheap).
      8. Spawn the screen session — pytest fires the session-autouse
         ``qs_deployed`` fixture at session start when the layer
         transitively touches AWS (qs_api / qs_browser).

    Returns EXIT_SUCCESS when the session is spawned + state written.
    Whether pytest INSIDE the screen passes or fails is not this verb's
    concern — that's what triage is FOR (operator drives pdb).

    DI phase — the prior inline ``recon-gen json apply --execute``
    block was retired. Deploy is owned by ``tests/e2e/conftest.py::
    qs_deployed`` (session-scope, autouse, xdist-FileLock'd). One
    deploy code path; cmd_triage + cmd_up_to both reach it via pytest
    fixture dispatch. POLICY 1: single source of truth.
    """
    nodeid = args.nodeid
    # Reject empty / absolute nodeids early — design lock.
    if not nodeid or nodeid.startswith("/"):
        print(
            f"runner: cannot infer layer from nodeid {nodeid!r}",
            file=sys.stderr,
        )
        print(
            "runner: nodeids must be repo-relative (e.g. "
            "tests/e2e/test_l1_filters.py::test_bar)",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    # Layer resolution.
    layer = args.layer or _infer_layer_from_nodeid(nodeid)
    if layer is None:
        print(
            f"runner: cannot infer layer from nodeid {nodeid!r}",
            file=sys.stderr,
        )
        print(
            "runner: known prefixes: tests/e2e/{agreement,app2,db}/ ;"
            " tests/{unit,json,cli,docs,schema,l2,audit,data}/",
            file=sys.stderr,
        )
        print(
            "runner: pass --layer=<unit|db|app2|agreement|app2_browser> "
            "to override",
            file=sys.stderr,
        )
        return EXIT_CONFIG_ERROR

    # Existing-session gate.
    if _screen_session_exists(_TRIAGE_SCREEN_NAME):
        if not args.force:
            print(
                f"runner: existing screen session {_TRIAGE_SCREEN_NAME!r} "
                f"detected"
            )
            print(
                f"runner: re-run with --force to kill and respawn, or "
                f"attach with:"
            )
            print(f"runner:   screen -x {_TRIAGE_SCREEN_NAME}")
            print(
                f"runner: alternatively, ./run_tests.sh triage-down --yes "
                f"to clean up first"
            )
            return EXIT_NEEDS_OPERATOR
        # --force: kill the old session before spawning a new one.
        if not _screen_kill(_TRIAGE_SCREEN_NAME):
            print(
                f"runner: failed to kill existing screen session "
                f"{_TRIAGE_SCREEN_NAME!r}",
                file=sys.stderr,
            )
            return EXIT_NEEDS_OPERATOR

    # Probe deps.
    failures = probe_dependencies(layer)
    if failures:
        for failure in failures:
            print(
                f"runner: probe-fail [{failure.kind}] {failure.message}",
                file=sys.stderr,
            )
        return EXIT_NEEDS_OPERATOR

    # Run-id + run-dir.
    run_id = create_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    triage_dir = run_dir / "triage"
    triage_dir.mkdir(parents=True, exist_ok=True)

    print(f"runner: run_id={run_id}")
    print(f"runner: run_dir={_rel_or_abs(run_dir)}")
    print(f"runner: triage nodeid={nodeid}")
    print(f"runner: inferred layer={layer}")

    # As-of anchor (matches cmd_up_to).
    import datetime as _dt  # noqa: PLC0415
    as_of_anchor = (
        os.environ.get(RECON_GEN_AS_OF_ANCHOR.name)
        or _dt.date.today().isoformat()
    )
    print(f"runner: as_of_anchor={as_of_anchor}")

    # Cfg + L2 + env shape.
    cfg_path = _resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)
    if cfg_path is None:
        print(
            "runner: no cfg found via _DEFAULT_RUNNER_CFG_CANDIDATES; "
            "triage requires a cfg",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    runner_variant_env = _setup_thin_chain_environment(
        cfg_path, as_of_anchor=as_of_anchor,
    )

    # Container spin (skipped for unit-only triage).
    container_handle: object | None = None
    container_env: dict[str, str] = {}
    docker_container_name: str | None = None
    if layer != "unit":
        # Bug A.7 fix — disable testcontainers Ryuk so the container
        # persists past cmd_triage exit. Default Ryuk behavior tears
        # down ALL testcontainers-spawned containers when the parent
        # process exits. cmd_triage spawns screen + returns; the
        # screen-attached pytest session needs the container ALIVE.
        # triage-down explicitly stops by the docker name captured
        # below. Setting the env BEFORE _start_thin_container's lazy
        # `from testcontainers.postgres import PostgresContainer`
        # ensures Ryuk reads the disabled state at module init.
        os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
        try:
            container_env, container_handle = _start_thin_container(cfg_path)
            runner_variant_env.update(container_env)
            print(
                f"runner: thin container up (dialect-matching) — "
                f"{RECON_GEN_DEMO_DATABASE_URL.name}=...exported"
            )
            # Bug A.7 fix — extract docker container name (anonymous
            # testcontainers names are like `friendly_goldberg`). Used
            # by triage-down to stop ONLY this container, not the
            # over-broad `_cmd_down_local()` sweep that hit
            # `recon-gen-snap-test-*` containers unrelated to triage.
            # Best-effort: handle types vary (PostgresContainer wraps
            # docker SDK, _PersistentContainerHandle has `.name`,
            # _DuckdbHandle has no docker container).
            try:
                wrapped = getattr(container_handle, "get_wrapped_container", None)
                if wrapped is not None:
                    docker_container_name = str(wrapped().name)
                else:
                    # _PersistentContainerHandle / Oracle path
                    handle_name = getattr(container_handle, "name", None)
                    if handle_name:
                        docker_container_name = str(handle_name)
            except Exception:  # noqa: BLE001 — name extraction is best-effort
                docker_container_name = None
        except Exception as exc:  # noqa: BLE001
            msg = (
                f"runner: thin container start failed ({exc!r}); aborting "
                f"triage"
            )
            print(msg, file=sys.stderr)
            _write_synthetic_cmd_json(
                run_dir,
                layer="container_start",
                exit_code=EXIT_NEEDS_OPERATOR,
                duration_seconds=0.0,
                message=msg,
            )
            return EXIT_NEEDS_OPERATOR

        # DG.2 sweep + seed (idempotent + cheap).
        if container_env:
            sweep_rc = _sweep_test_prefixes(cfg_path, container_env, run_dir)
            if sweep_rc != 0:
                msg = f"runner: DG.2 sweep failed rc={sweep_rc}; aborting triage"
                print(msg, file=sys.stderr)
                _write_synthetic_cmd_json(
                    run_dir,
                    layer="sweep",
                    exit_code=sweep_rc,
                    duration_seconds=0.0,
                    message=msg,
                )
                _teardown_container_best_effort(container_handle)
                return EXIT_NEEDS_OPERATOR
            print("runner: DG.2 sweep done")
        l2_path_env = runner_variant_env.get(RECON_GEN_TEST_L2_INSTANCE.name)
        if l2_path_env is not None and container_env:
            seed_rc = _seed_thin_container(
                cfg_path, Path(l2_path_env), container_env, run_dir,
            )
            if seed_rc != 0:
                msg = f"runner: thin seed failed rc={seed_rc}; aborting triage"
                print(msg, file=sys.stderr)
                _write_synthetic_cmd_json(
                    run_dir,
                    layer="seed",
                    exit_code=seed_rc,
                    duration_seconds=0.0,
                    message=msg,
                )
                _teardown_container_best_effort(container_handle)
                return EXIT_NEEDS_OPERATOR
            print("runner: thin seed done (schema apply + data apply + data refresh)")

    # DI phase — deploy is owned by the session-autouse ``qs_deployed``
    # fixture in ``tests/e2e/conftest.py``. cmd_triage's inline deploy
    # block is gone; the pytest invocation inside the screen session
    # fires the fixture at session start (under FileLock + sentinel
    # rendezvous) and the test proceeds against a freshly delete-then-
    # created QS state. POLICY 1 (single source of truth): chain +
    # triage both reach deploy through the fixture; neither
    # orchestrator dispatches deploy directly.
    #
    # ``triage-down`` still owns the QS sweep on the way down — pytest
    # fixture teardown doesn't fire reliably under ``screen --pdb``
    # detach + ``triage-down --force`` mid-session, so explicit
    # ``recon-gen json clean --all --execute`` is the safe path.

    # Build the screen launch script (cmd.sh) + spawn.
    log_path = triage_dir / "screen.log"
    cmd_path = triage_dir / "cmd.sh"
    pytest_cmd_parts = [
        shlex.quote(str(_VENV_BIN / "pytest")),
        shlex.quote(nodeid),
        "-p", "no:xdist",
        "-p", "no:rerunfailures",
        "-p", "no:cacheprovider",
        "--capture=no", "-s", "--pdb", "-v",
    ]
    pytest_cmd_str = " ".join(pytest_cmd_parts)
    cmd_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"cd {shlex.quote(str(REPO_ROOT))}\n"
        f"echo '=== recon-gen triage: {nodeid} (layer={layer}) ==='\n"
        f"{pytest_cmd_str}\n"
        "echo '=== pytest exited with rc='$? ' ==='\n"
        # Keep the session alive after pytest exits so the operator can
        # scroll back / re-invoke. WITHOUT this the screen session dies
        # as soon as pytest returns and the operator loses scrollback.
        "exec bash --norc -i\n"
    )
    cmd_path.chmod(0o755)

    print(f"runner: spawning screen session {_TRIAGE_SCREEN_NAME!r}")
    screen_argv = [
        _resolve_screen_bin(),
        "-dmS", _TRIAGE_SCREEN_NAME,
        "-L",
        "-Logfile", str(log_path),
        "bash", str(cmd_path),
    ]
    # POLICY 1 (CLAUDE.md "Build hygiene contract") — triage's env shape
    # must match the chain's `up_to=<layer>` env shape EXACTLY. Pull the
    # layer-specific env_addl from `_layer_command(layer, ...)` (the same
    # function `cmd_up_to`'s dispatch loop calls per layer) and merge it
    # into the spawn env. This brings in
    # RECON_GEN_LAYER + RECON_GEN_DEMO_DATABASE_URL[_PG/_OR] +
    # RECON_E2E_PAGE_TIMEOUT + the SKIP_PYRIGHT/BIOME/TAILWIND set the
    # chain's qs_browser layer sets — without which the e2e conftest's
    # collect-modifyitems skips the test silently and triage drops into
    # bash with rc=0 instead of into pdb on the real test body.
    layer_env_addl: dict[str, str] = {}
    layer_cmd_env = _layer_command(
        layer, run_dir, options=None, variant_env=runner_variant_env,
    )
    if layer_cmd_env is not None:
        _layer_argv, layer_env_addl = layer_cmd_env
    spawn_env = {**os.environ, **runner_variant_env, **layer_env_addl}
    spawn_result = subprocess.run(
        screen_argv,
        cwd=REPO_ROOT,
        env=spawn_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if spawn_result.returncode != 0:
        msg = (
            f"runner: screen spawn failed rc={spawn_result.returncode}: "
            f"{spawn_result.stderr.strip() or spawn_result.stdout.strip()}"
        )
        print(msg, file=sys.stderr)
        _write_synthetic_cmd_json(
            run_dir,
            layer="triage",
            exit_code=EXIT_NEEDS_OPERATOR,
            duration_seconds=0.0,
            message=msg,
        )
        _teardown_container_best_effort(container_handle)
        return EXIT_NEEDS_OPERATOR

    # Persist the triage state so triage-down can find the run + sweep
    # targets without re-discovering them.
    #
    # DI phase — slimmed: dropped run_id / layer / as_of_anchor (none
    # were read by triage-down). State is operational glue between
    # triage + triage-down, NOT a source of truth for what was deployed
    # (the QS account is) or which container is up (Docker is).
    state: dict[str, Any] = {
        "run_dir": str(run_dir),
        "nodeid": nodeid,
        "screen_name": _TRIAGE_SCREEN_NAME,
        "cfg_path": str(cfg_path),
        # Bug A.7 fix — persist the triage-spawned container name so
        # triage-down stops ONLY that container (instead of the
        # over-broad `_cmd_down_local()` sweep that hit unrelated
        # `recon-gen-snap-test-*` containers during the inaugural
        # session 2026-06-14). None when the triage layer was unit
        # (no container spun) or container name extraction failed.
        "docker_container_name": docker_container_name,
    }
    _write_triage_state(state)
    print(f"runner: triage state -> {_rel_or_abs(_TRIAGE_STATE_FILE)}")

    print()
    print("================ TRIAGE READY ================")
    print(f"  Attach:    screen -x {_TRIAGE_SCREEN_NAME}")
    print("  Detach:    Ctrl-A then d (inside the session)")
    print(f"  Log file:  {_rel_or_abs(log_path)}")
    print("  Teardown:  ./run_tests.sh triage-down --yes")
    print("  Assistant agents (non-interactive):")
    print(f"    Snap screen state: screen -S {_TRIAGE_SCREEN_NAME} -X hardcopy /tmp/triage-snap.txt && cat /tmp/triage-snap.txt")
    print(f"    Send pdb command:  screen -S {_TRIAGE_SCREEN_NAME} -X stuff $'<cmd>\\n'   (e.g. 'p variable\\n')")
    print(f"    Tail pytest log:   tail -f {_rel_or_abs(log_path)}")
    print()
    print("  Inside the session:")
    print("    - pytest is running with --pdb; the test will drop into pdb on")
    print("      first failure or any breakpoint() call.")
    print("    - `(Pdb)` prompt commands: p / pp / l / n / s / c / w / q")
    print("    - When pytest exits, an interactive bash shell takes over so you")
    print("      can poke the run_dir, re-invoke pytest, etc. The screen session")
    print("      stays alive until triage-down.")
    print("===============================================")

    return EXIT_SUCCESS


def _teardown_container_best_effort(handle: object | None) -> None:
    """Best-effort container.stop() — duck-typed contract (testcontainers
    Container, _DuckdbHandle, _PersistentContainerHandle all expose
    .stop()). Swallows exceptions; teardown failures shouldn't mask the
    primary error.
    """
    if handle is None:
        return
    try:
        handle.stop()  # type: ignore[attr-defined]: duck-typed teardown contract
    except Exception:  # noqa: BLE001
        pass


def cmd_triage_down(args: argparse.Namespace) -> int:
    """Tear down the active triage session: kill the screen session and
    stop the local container.

    Destructive — requires ``--yes`` (matches cmd_down).

    Idempotent — when no state file exists, prints a friendly message
    and returns EXIT_SUCCESS rather than EXIT_NEEDS_OPERATOR. Rationale:
    triage-down should be safely runnable any time; "nothing to clean
    -> error 2" would train the operator to ignore the exit code.
    """
    if not args.yes and not RECON_GEN_RUNNER_YES.get_or_none():
        print(
            "runner: 'triage-down' is destructive — pass --yes "
            "(or set RECON_GEN_RUNNER_YES=1)",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR

    print(f"runner: triage-down — reading state from {_rel_or_abs(_TRIAGE_STATE_FILE)}")
    try:
        state = _read_triage_state()
    except ValueError as exc:
        print(f"runner: {exc}", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR
    if state is None:
        print(
            f"runner: triage-down — no active triage state at "
            f"{_rel_or_abs(_TRIAGE_STATE_FILE)}"
        )
        print("runner: nothing to do; exit 0")
        return EXIT_SUCCESS

    nodeid = state.get("nodeid", "<unknown>")
    print(
        f"runner: killing screen session {_TRIAGE_SCREEN_NAME!r} "
        f"(was for nodeid={nodeid})"
    )
    if not _screen_kill(_TRIAGE_SCREEN_NAME):
        print(
            f"runner: failed to kill screen session {_TRIAGE_SCREEN_NAME!r} "
            f"— continuing teardown",
            file=sys.stderr,
        )
    else:
        print("runner: screen session terminated")

    # Container teardown — narrowed to ONLY the triage-spawned
    # container (bug A.7 fix). Pre-fix this called `_cmd_down_local()`
    # which stopped ALL `recon-gen-*` containers including
    # `recon-gen-snap-test-pg/oracle` that weren't part of triage.
    if args.keep_container:
        print("runner: --keep-container set; skipping local container stop")
    else:
        container_name_raw = state.get("docker_container_name")
        container_name = (
            container_name_raw if isinstance(container_name_raw, str) else None
        )
        if container_name is None:
            print(
                "runner: no triage-spawned container recorded in state; "
                "skipping container teardown (likely a unit-layer triage)"
            )
        else:
            print(f"runner: stopping triage container {container_name!r}…")
            stop_result = subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["docker", "stop", container_name],  # noqa: S607
                capture_output=True, text=True, check=False,
            )
            if stop_result.returncode != 0:
                # Treat "no such container" as success (already gone);
                # any other failure is loud.
                stderr_lower = stop_result.stderr.lower()
                if "no such container" not in stderr_lower:
                    print(
                        f"runner: docker stop {container_name!r} failed "
                        f"rc={stop_result.returncode}: "
                        f"{stop_result.stderr.strip()}",
                        file=sys.stderr,
                    )
                    _TRIAGE_STATE_FILE.unlink(missing_ok=True)
                    return EXIT_FAILURE
            # Best-effort docker rm so anonymous testcontainers names
            # don't accumulate in `docker ps -a`.
            subprocess.run(  # noqa: S603 — fixed argv, no shell
                ["docker", "rm", container_name],  # noqa: S607
                capture_output=True, text=True, check=False,
            )

    _TRIAGE_STATE_FILE.unlink(missing_ok=True)
    print("runner: triage-down complete (state file removed)")
    return EXIT_SUCCESS


_HELP_EPILOG = """\
Layer chain (Y.2.gate.b/c/n):
  unit -> db -> app2 -> agreement -> app2_browser
  (DW.5.2: QuickSight removed; the chain is fully local — no AWS tier.
  ``app2_browser`` is the terminal Playwright tier driving local App 2
  servers.)
  ./run_tests.sh up_to=<layer>  runs the chain through that layer.
  Post-CB.17.d the runner uses a single-pytest-per-layer "thin path":
  each layer runs ONE pytest subprocess (no cell loop, no prelude
  split). Per-(file, worker) isolation moved into pytest fixtures
  (`isolated_cfg`, `seeded_cfg`). Layer artifacts land at
  runs/<id>/<layer>/{cmd.json,stdout.log,stderr.log,env_log/}.

  A failure at layer N aborts the chain — layers N+1..end don't
  dispatch. `dump-last-errors` surfaces the failing layer's traceback
  + missing-capture warnings from the latest run dir.
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_tests.sh",
        description="Test layer chain runner. See module docstring for full usage.",
        epilog=_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="verb", required=True)

    p_up_to = subs.add_parser("up_to", help="Run the chain up to and including <layer>")
    p_up_to.add_argument("layer", choices=LAYERS)
    p_up_to.add_argument(
        "--only", metavar="<expr>", default=None,
        help="pytest -k <expr>: narrow within-layer tests.",
    )
    p_up_to.add_argument(
        "--parallel", type=int, default=1, metavar="N",
        help="within-layer pytest-xdist worker count. Default = `-n auto` (= cpu_count); pin via `--parallel=N`.",
    )
    p_up_to.add_argument(
        "--trace-all", action="store_true",
        help="Playwright capture every test (failure-only is the default).",
    )
    p_up_to.add_argument(
        "--coverage", action="store_true",
        help="emit .coverage.<run-id>.<layer> data files under runs/<id>/.",
    )
    p_up_to.set_defaults(func=cmd_up_to)

    p_up = subs.add_parser("up", help="Boot local dependencies")
    p_up.add_argument("scope", nargs="?", default="local", choices=["local"])
    p_up.set_defaults(func=cmd_up)

    p_down = subs.add_parser("down", help="Tear down local dependencies")
    p_down.add_argument("scope", nargs="?", default="local", choices=["local"])
    p_down.add_argument("--yes", action="store_true", help="confirm destructive op")
    p_down.set_defaults(func=cmd_down)

    p_status = subs.add_parser("status", help="Show what's currently running")
    p_status.add_argument("--cost", action="store_true", help="include hourly cost estimate")
    p_status.set_defaults(func=cmd_status)

    p_triage = subs.add_parser(
        "triage",
        help=(
            "Spawn a detached GNU screen session running a single test under "
            "PDB control. Operator attaches via `screen -x recon-gen-triage` "
            "to drive `pdb` interactively. Intended for stuck-test diagnosis; "
            "use `triage-down` to clean up when finished."
        ),
    )
    p_triage.add_argument(
        "nodeid",
        metavar="<test_nodeid>",
        help=(
            "Pytest nodeid (e.g. tests/e2e/test_inv_filters.py::"
            "test_min_sigma_slider_shrinks_anomalies_kpi[app2]). Layer is "
            "inferred from the path prefix; see --help for the rules."
        ),
    )
    p_triage.add_argument(
        "--layer",
        choices=LAYERS,
        default=None,
        metavar="<layer>",
        help=(
            "Override inferred layer. Use when nodeid resolves to an "
            "ambiguous path (e.g. tests/e2e/test_dashboard_driver.py)."
        ),
    )
    p_triage.add_argument(
        "--force",
        action="store_true",
        help=(
            "Kill any existing `recon-gen-triage` screen session before "
            "spawning a fresh one (idempotent re-launch). Without --force, "
            "an existing session aborts the verb with EXIT_NEEDS_OPERATOR "
            "(2) so the operator can decide whether to attach or replace."
        ),
    )
    p_triage.set_defaults(func=cmd_triage)

    p_triage_down = subs.add_parser(
        "triage-down",
        help=(
            "Tear down the active triage session: kill the screen session "
            "and stop the local container."
        ),
    )
    p_triage_down.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirm destructive teardown (required; matches `down` "
            "convention). Also honored via RECON_GEN_RUNNER_YES=1."
        ),
    )
    p_triage_down.add_argument(
        "--keep-container",
        action="store_true",
        help=(
            "Skip the `docker stop` of the local PG/Oracle container; useful "
            "when you want to re-launch triage against the same seeded data."
        ),
    )
    p_triage_down.set_defaults(func=cmd_triage_down)

    p_pyright = subs.add_parser(
        "pyright",
        help="Run pyright directly (fast type-check; no pytest, no chain)",
    )
    p_pyright.add_argument(
        "paths",
        nargs="*",
        help="optional file/dir paths; defaults to the strict-include set in pyproject.toml",
    )
    p_pyright.set_defaults(func=cmd_pyright)

    p_dump = subs.add_parser(
        "dump-last-errors",
        help=(
            "Surface failing-layer assertions + missing-capture "
            "warnings from the most-recent run dir (triage shortcut)."
        ),
    )
    p_dump.add_argument(
        "--run",
        default=None,
        metavar="RUN_ID",
        help=(
            "specific run-id (e.g. 20260516T203824Z-914fc4c); "
            "default = latest by mtime."
        ),
    )
    # `--variant` retired post-CB.17.d — the 13-cell matrix is gone;
    # the thin path produces one set of layer dirs per run, no cells
    # to filter. Kept on the argparse surface as a deprecated alias so
    # existing scripts don't crash; the value gets ignored with a
    # warning.
    p_dump.add_argument(
        "--variant",
        default=None,
        metavar="NAME",
        help="DEPRECATED — the matrix path was retired in CB.17.d. Ignored.",
    )
    p_dump.set_defaults(func=cmd_dump_last_errors)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(_normalize_argv(raw))
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
