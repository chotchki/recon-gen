"""Test layer chain runner — orchestrates the layered test chain with per-run
output isolation + timing-diff drift detection.

Invoked via the ``./run_tests.sh`` bash shim at repo root; the shim
``exec``s into ``python -m quicksight_gen._dev.runner``.

Verbs:
    up_to <layer>     Run the chain up to and including <layer>.
                      Layers: pyright | unit | db | deploy | api | browser.
                      Equivalent forms: ``up_to=<layer>`` and ``up_to <layer>``.
    up [scope]        Boot dependencies. scope = local | aws | all (default).
    down [scope]      Tear down dependencies. scope as above.
    status [--cost]   Show what's currently running.
    sweep             Clean orphan resources (tagged ManagedBy:quicksight-gen).

Exit codes:
    0  success
    1  test failure (one or more layers / variants failed)
    2  needs-operator (expired creds, dirty deploy refused, missing cfg, etc.)
    3  config / argument error

Substrate: pytest-as-orchestrator + this thin Python wrapper. See
``docs/audits/y_2_gate_b_0_runner_lang_spike.md`` for the design lock.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, cast

from quicksight_gen.common.env_keys import (
    QS_E2E_USER_ARN,
    QS_GEN_CONFIG,
    QS_GEN_DEMO_DATABASE_URL,
    QS_GEN_E2E,
    QS_GEN_FUZZ_SEED,
    QS_GEN_LAYER,
    QS_GEN_RUN_DIR,
    QS_GEN_RUNNER_YES,
    QS_GEN_TEST_L2_INSTANCE,
    QS_GEN_TRACE_ALL,
)

EXIT_SUCCESS: Final = 0
EXIT_FAILURE: Final = 1
EXIT_NEEDS_OPERATOR: Final = 2
EXIT_CONFIG_ERROR: Final = 3

LAYERS: Final[tuple[str, ...]] = (
    "unit",
    "db",
    "app2",
    "deploy",
    "api",
    "browser",
)
# Y.2.gate.b.3.impl.layer (2026-05-07) — `app2` inserted as layer 3.7
# (between db + deploy) per audit §7.10. App2 is the local-Docker
# fast-feedback gate: same dataset SQL as QS, no AWS contact, runs
# the `tests/e2e/test_html2_*.py` files against the variant DB.
# Locked by audit §7.10 (App2 promotion: ~80% of bug classes
# catchable in App2 against local Docker).
# Y.2.gate.c.7-followup (2026-05-07) — `pyright` collapsed into the `unit`
# layer. The repo-root ``conftest.py::pytest_sessionstart`` (M.1.9c contract)
# runs pyright on session start; on failure ``pytest.exit(returncode=2)``
# fires before any test collects. So bare ``pytest tests/`` AND the runner
# both type-check, with no double-pyright bookkeeping. Trade-off: pyright
# duration folds into the unit layer's wall-clock instead of being its own
# entry in `timings.json`. Acceptable because pyright is ~2s.

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

# Matches `<utc-ts>-<short-sha>[-dirty]` from create_run_id(); used by
# prune_old_runs to only touch directories we created, never unrelated
# files an operator might park under runs/.
_RUN_ID_PATTERN: Final = re.compile(r"^\d{8}T\d{6}Z-\w+(?:-dirty)?$")

# Y.2.gate.c.8 — per-layer dependency requirements. Authoritative mirror of
# audit doc §3 (variant axes table). Cross-checked by
# tests/unit/test_runner_skeleton.py::test_layer_deps_match_audit (c.14).
#
# Probe kinds (matched to _probe_* function names):
#   "aws"     — AWS creds present + not expired (sts:GetCallerIdentity).
#   "docker"  — Docker daemon reachable (`docker ps`).
#   "qs_arn"  — QS_E2E_USER_ARN set (browser e2e signs embed URLs as this user).
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
    "deploy": frozenset({"aws", "docker"}),
    "api": frozenset({"aws", "docker"}),
    "browser": frozenset({"aws", "docker", "qs_arn"}),
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


def _run_probe_subprocess(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    """Run a probe subprocess with a timeout so a hanging command can't lock
    the runner. ``timeout=10s`` is generous; AWS CLI typically finishes in <2s,
    docker ps in <1s. On TimeoutExpired we synthesize a returncode=124 + empty
    stdout/stderr the caller can branch on."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="probe timed out")
    except FileNotFoundError:
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=f"{cmd[0]}: not found")


def _probe_aws() -> ProbeFailure | None:
    """Y.2.gate.c.8 + b.14.4 — check AWS creds via ``aws sts get-caller-identity``.

    Returns ``None`` if creds work. On expired/missing/unknown failure, returns
    a ``ProbeFailure`` whose message tells the operator exactly what to type
    (`! aws sso login`); we **never** auto-invoke the SSO browser flow."""
    result = _run_probe_subprocess(["aws", "sts", "get-caller-identity"])
    if result.returncode == 0:
        return None

    stderr_lower = result.stderr.lower()
    if "expiredtoken" in stderr_lower or "tokenexpired" in stderr_lower:
        return ProbeFailure(
            kind="aws_creds_expired",
            message="AWS creds expired — type '! aws sso login' yourself, then re-invoke",
        )
    if "unable to locate credentials" in stderr_lower or "no credentials" in stderr_lower:
        return ProbeFailure(
            kind="aws_no_creds",
            message="No AWS credentials — set AWS_PROFILE or run 'aws configure', then re-invoke",
        )
    if result.returncode == 127:
        return ProbeFailure(
            kind="aws_cli_missing",
            message="aws CLI not found — install awscli, then re-invoke",
        )
    return ProbeFailure(
        kind="aws_check_failed",
        message=f"AWS check failed (rc={result.returncode}): {result.stderr.strip() or '(no stderr)'}",
    )


def _probe_docker() -> ProbeFailure | None:
    """Check Docker daemon is reachable via ``docker ps``."""
    result = _run_probe_subprocess(["docker", "ps"])
    if result.returncode == 0:
        return None
    if result.returncode == 127:
        return ProbeFailure(
            kind="docker_cli_missing",
            message="docker CLI not found — install Docker Desktop / docker engine, then re-invoke",
        )
    if "cannot connect to the docker daemon" in result.stderr.lower():
        return ProbeFailure(
            kind="docker_daemon_down",
            message="Docker daemon not running — start Docker Desktop (or `colima start`), then re-invoke",
        )
    return ProbeFailure(
        kind="docker_check_failed",
        message=f"Docker check failed (rc={result.returncode}): {result.stderr.strip() or '(no stderr)'}",
    )


def _probe_qs_e2e_user_arn() -> ProbeFailure | None:
    """Check ``QS_E2E_USER_ARN`` env var is set (required for browser e2e
    embed-URL signing). Auto-derivation from AWS identity lands under
    ``Y.2.gate.h.1``; for now the env var is operator-set.

    Y.2.gate.b.15 — registry call also runs the IAM-ARN regex
    validator on PRESENCE, so a malformed ARN surfaces here instead
    of inside the boto embed-URL call later.
    """
    if QS_E2E_USER_ARN.get_or_none():
        return None
    return ProbeFailure(
        kind="qs_arn_unset",
        message="QS_E2E_USER_ARN unset — export the QuickSight user ARN for embed signing, then re-invoke",
    )


_ProbeFunc = Callable[[], "ProbeFailure | None"]
_PROBE_FUNCTIONS: Final[dict[str, _ProbeFunc]] = {
    "aws": _probe_aws,
    "docker": _probe_docker,
    "qs_arn": _probe_qs_e2e_user_arn,
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
    - ``allow_dirty_deploy`` — bypass tracked-changes refusal on layer 4+ (active now per b.10).
    - ``variants`` / ``fuzz_seeds`` — cross-variant fan-out via asyncio.gather (lands when
      real variants exist; deploy/api/browser are stubs today). ``fuzz_seeds`` = COUNT
      (sample size for property-testing axis) vs ``fuzz_seed_value`` = VALUE.
    - ``skip_cheap`` — skip-if-already-green-this-SHA (active when cache lands; b.8).
    - ``keep_on_failure`` — don't tear down ephemeral state on failure (active when
      Y.2.gate.l.2 lifecycle commands land; b.14.3 / f.5).
    """

    only: str | None = None
    parallel: int = 1
    variants: str = "default"
    fuzz_seeds: int = 1
    fuzz_seed_value: int | None = None
    skip_cheap: bool = False
    keep_on_failure: bool = False
    trace_all: bool = False
    allow_dirty_deploy: bool = False


def resolve_fuzz_seed_value() -> int:
    """Y.2.gate.c.6.xdist-safety — resolve the seed for this runner invocation.

    Priority: ``QS_GEN_FUZZ_SEED`` env (operator pin for failure repro) > random
    per session (`secrets.randbits(32)`). Per audit §7.11 (LOCKED): default = 1
    random seed per run; cumulative coverage emerges across many runs. The seed
    is pinned across xdist workers within a single run so parametrize collection
    is deterministic (otherwise each worker rolls its own seed → collection
    diverges → ``Different tests were collected`` error).
    """
    override = QS_GEN_FUZZ_SEED.get_or_none()
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
    layer: str, run_dir: Path, options: RunOptions | None = None
) -> tuple[list[str], dict[str, str]] | None:
    """Map layer → (subprocess argv, env additions). Returns None for layers
    not yet wired (cfg-loading-blocked: deploy / api / browser).

    Pyright runs via the repo-root ``conftest.py::pytest_sessionstart`` hook
    (M.1.9c contract) at the start of every pytest invocation — so the unit
    layer's pytest invocation type-checks before any test runs. Direct
    ``pytest tests/`` invocations (developer one-test iteration) get the
    same gate. No separate runner layer; pyright duration folds into the
    unit layer's wall-clock.

    ``QS_GEN_LAYER`` + ``QS_GEN_RUN_DIR`` are threaded through to every
    pytest subprocess so ``tests/conftest.py``'s makereport hook (c.2)
    can write per-test timings into the right ``runs/<run-id>/timings/``
    file.

    Y.2.gate.c.7 — `options.only` adds `-k <expr>` to pytest invocations;
    `options.trace_all` exports `QS_GEN_TRACE_ALL=1` (consumed by c.11
    browser fixtures).

    Y.2.gate.c.6.xdist-safety — `options.fuzz_seed_value` exports
    ``QS_GEN_FUZZ_SEED=<N>`` so all xdist workers see the same seed and
    parametrize collection is deterministic.
    """
    opts = options or RunOptions()
    env_addl = {
        QS_GEN_RUN_DIR.name: str(run_dir),
        QS_GEN_LAYER.name: layer,
    }
    if opts.trace_all:
        env_addl[QS_GEN_TRACE_ALL.name] = "1"
    if opts.fuzz_seed_value is not None:
        env_addl[QS_GEN_FUZZ_SEED.name] = str(opts.fuzz_seed_value)
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
        if opts.parallel > 1:
            cmd += ["-n", str(opts.parallel)]
        return (cmd, env_addl)
    if layer == "db":
        # 3a — DB SQL smoke (parametrized over 37 datasets). Behind QS_GEN_E2E=1.
        # Real DB connection comes from cfg; until cfg loading lands the test
        # itself fails fast if cfg is missing. That's the expected shape.
        cmd = [str(_VENV_BIN / "pytest"), "tests/e2e/test_dataset_sql_smoke.py", "-q"]
        if opts.only:
            cmd += ["-k", opts.only]
        if opts.parallel > 1:
            cmd += ["-n", str(opts.parallel)]
        return (cmd, {**env_addl, QS_GEN_E2E.name: "1"})
    if layer == "app2":
        # b.3.impl.layer — App2 e2e (HTMX dialect, Playwright WebKit
        # against the App2 Starlette server). Three test files today:
        # `test_html2_executives.py` + `test_html2_money_trail.py`
        # use stub fetchers (renderer correctness); `test_html2_executives_live.py`
        # uses `make_tree_db_fetcher(tree_app, cfg)` against the variant
        # DB — `connect_demo_db(cfg)` reads `QS_GEN_DEMO_DATABASE_URL`
        # env override (config.py:364), so the variant URL flows
        # through naturally. Behind `QS_GEN_E2E=1` like every other
        # tests/e2e/ file. NO AWS contact (audit §7.10 LOCKED).
        cmd = [
            str(_VENV_BIN / "pytest"),
            "tests/e2e/test_html2_executives.py",
            "tests/e2e/test_html2_executives_live.py",
            "tests/e2e/test_html2_money_trail.py",
            "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        if opts.parallel > 1:
            cmd += ["-n", str(opts.parallel)]
        return (cmd, {**env_addl, QS_GEN_E2E.name: "1"})
    # deploy / api / browser: not yet wired. Need cfg loading (Y.2.gate.h.2)
    # + variant fan-out (b.3.impl.gather for the 6/7 asyncio.gather).
    return None


def dispatch_layer(
    layer: str,
    run_dir: Path,
    options: RunOptions | None = None,
    *,
    variant_env: dict[str, str] | None = None,
) -> LayerResult:
    """Y.2.gate.c.5 — run one layer; return its result.

    Stub layers return a `skipped=True` LayerResult with exit_code=0 so the
    chain doesn't break — the deferred work is c.5+ follow-up, not a runner
    bug. Stubs print a clear `dispatch-skip` line so the operator knows.

    Y.2.gate.b.2.impl — ``variant_env`` (e.g.,
    ``{"QS_GEN_DEMO_DATABASE_URL": "<container-url>"}``) gets merged into
    the subprocess env so the variant's resources (Docker container
    URL etc.) are visible to pytest fixtures + cfg loaders inside the
    subprocess.
    """
    cmd_env = _layer_command(layer, run_dir, options)
    if cmd_env is None:
        print(f"runner: dispatch-skip [{layer}] not-yet-wired (cfg loading + variants)")
        return LayerResult(layer=layer, exit_code=0, duration_seconds=0.0, skipped=True)

    cmd, env_addl = cmd_env
    # Y.2.gate.b.2.impl — variant_env only applies to layers that
    # actually need a DB. Unit doesn't (in-process tests / pyright);
    # leaking QS_GEN_DEMO_DATABASE_URL into the unit subprocess
    # contaminates tests that assert "no demo_database_url is set".
    effective_variant_env = (
        variant_env if variant_env and layer in DB_TOUCHING_LAYERS else {}
    )
    env = {**os.environ, **env_addl, **effective_variant_env}
    print(f"runner: dispatch-run [{layer}] {' '.join(cmd)}")
    start = time.monotonic()
    result = subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False)
    duration = time.monotonic() - start
    return LayerResult(layer=layer, exit_code=result.returncode, duration_seconds=duration)


def _is_deploy_or_later(layer: str) -> bool:
    """Y.2.gate.b.10 — layers ≥ deploy touch AWS/external state. Dirty-state
    refusal applies only to those (layers 1-3 are local + idempotent)."""
    return LAYERS.index(layer) >= LAYERS.index("deploy")


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
    pyright → unit → db; ``up_to=browser`` means the full chain.
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


# Y.2.gate.b.2.impl — variant axis (lock per b.1).
#
# Today's runner is single-variant by default ("default" = use whatever
# DB the cfg resolves to, typically the operator's external Aurora /
# Oracle SE2). Y.2.gate.b.2 locks the design for per-variant Docker
# containers; this is the impl. ``--variants=local-pg`` spins up a
# Postgres testcontainer, threads its connection URL via
# ``QS_GEN_DEMO_DATABASE_URL`` env to the pytest subprocess, and tears
# the container down at the end of the chain.
#
# Multi-variant fan-out (running both default + local-pg in one
# invocation) is `c.6.async`'s job — needs `asyncio.gather` for
# parallel per-variant subprocesses. b.2.impl scopes to the
# single-non-default-variant case to deliver the keystone value:
# operator can run db tests against a local container instead of
# burning Aurora minutes.
KNOWN_VARIANTS: Final = ("default", "local-pg")

# Y.2.gate.b.2.impl — layers whose subprocess needs the variant's
# DB connection threaded through (QS_GEN_DEMO_DATABASE_URL etc.).
# Unit doesn't need it; deploy/api/browser would (when wired).
# `app2` (b.3.impl.layer) reads the variant DB via the App2 fetcher
# (`make_tree_db_fetcher`), so it lives here.
DB_TOUCHING_LAYERS: Final = ("db", "app2", "deploy", "api", "browser")


def resolve_variants(variants_arg: str) -> list[str]:
    """Parse ``--variants=<set>`` CSV into a list. ``default`` always
    resolves to a single ``["default"]`` (today's behavior preserved).
    Unknown variant names raise — fail-loud so a typo doesn't silently
    fall through to "default behavior" the operator didn't ask for.
    """
    raw = [v.strip() for v in variants_arg.split(",") if v.strip()]
    if not raw or raw == ["default"]:
        return ["default"]
    unknown = [v for v in raw if v not in KNOWN_VARIANTS]
    if unknown:
        raise ValueError(
            f"unknown variant(s) {unknown!r}; known: {list(KNOWN_VARIANTS)}"
        )
    return raw


def setup_variant(name: str) -> tuple[dict[str, str], object | None]:
    """Bring up the resources a variant needs. Returns
    ``(env_overrides, handle_for_teardown)``. Caller threads
    env_overrides into the pytest subprocess and passes handle to
    `teardown_variant` after.

    For ``default``: no-op. For ``local-pg``: spins up a Postgres
    container via testcontainers-python and returns its connection
    URL as a ``QS_GEN_DEMO_DATABASE_URL`` override.

    Postgres container takes ~10-15s to start. Lifetime is the chain
    (one container reused across all layers in a single ``up_to``
    invocation), not per-layer.
    """
    if name == "default":
        return {}, None
    if name == "local-pg":
        # Lazy-import: testcontainers requires Docker, which not every
        # operator has. Importing only on demand keeps non-Docker
        # invocations clean.
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs

        # Pin to the exact PG version we run in production (Aurora 17).
        container = PostgresContainer("postgres:17-alpine")
        container.start()
        raw_url: str = container.get_connection_url()  # type: ignore[no-untyped-call]: testcontainers method has no type annotations
        return {QS_GEN_DEMO_DATABASE_URL.name: _normalize_pg_url(raw_url)}, container
    raise ValueError(f"setup_variant: unknown variant {name!r}")


def _normalize_pg_url(raw_url: str) -> str:
    """testcontainers-python returns SQLAlchemy-style URLs
    (``postgresql+psycopg2://...``) by default, but ``connect_demo_db``
    uses psycopg3 directly which rejects the ``+psycopg2`` driver
    suffix (``missing "=" after "..."`` from libpq's conninfo
    parser). Strip the suffix so the URL is the plain libpq form
    psycopg accepts.
    """
    return raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)


def teardown_variant(handle: object | None) -> None:
    """Stop + remove the container if one was started. No-op for
    ``default`` (handle is None)."""
    if handle is None:
        return
    try:
        # All testcontainers expose ``.stop()`` for shutdown + cleanup.
        handle.stop()  # type: ignore[attr-defined]: testcontainers handle is duck-typed (.stop() across all variants)
    except Exception:  # noqa: BLE001
        # Sidecar contract — never break the chain on teardown.
        pass


# Y.2.gate.b.2.impl.schema — non-default variants spin up empty
# containers; the db layer (and downstream layers) need the schema
# applied + data seeded + matviews refreshed before tests can run.
# Cfg discovery priority for local-pg: QS_GEN_CONFIG env override
# wins (operator pin), else run/config.postgres.yaml (PG-dialect cfg
# the container expects). run/config.yaml is intentionally skipped —
# it may be Oracle-flavored, which doesn't match a Postgres container.
_LOCAL_PG_CFG_CANDIDATES: Final = (
    "run/config.postgres.yaml",
)


def _resolve_seed_config_for_local_pg() -> Path | None:
    """Y.2.gate.b.2.impl.schema — find a postgres-dialect cfg the
    seed CLI verbs (`schema apply` / `data apply` / `data refresh`)
    can use against the local-pg container. Returns None if nothing
    matches; caller surfaces the failure with operator-actionable
    guidance.

    QS_GEN_CONFIG is read via the typed registry; an explicit
    operator pin at a non-existent path returns None (matches the
    existing "respect the override; surface the absence" contract)
    rather than letting the validator raise.
    """
    # Read the raw value to honor the "non-existent → None" contract
    # (registry's must_be_file validator would otherwise raise on a
    # bad explicit pin, but this code path wants a soft None).
    explicit = os.environ.get(QS_GEN_CONFIG.name)
    if explicit:
        candidate = Path(explicit)
        if candidate.is_absolute():
            return candidate if candidate.exists() else None
        resolved = REPO_ROOT / candidate
        return resolved if resolved.exists() else None
    for relative in _LOCAL_PG_CFG_CANDIDATES:
        candidate = REPO_ROOT / relative
        if candidate.exists():
            return candidate
    return None


def seed_variant(name: str, env_overrides: dict[str, str]) -> None:
    """Y.2.gate.b.2.impl.schema — bootstrap the variant's DB so the
    db / deploy / api / browser layers have something to query.

    For ``default``: no-op (the operator's external Aurora / Oracle
    is presumed already seeded). For ``local-pg``: spawns three CLI
    subprocesses in dependency order against the container URL:

        1. ``quicksight-gen schema apply --execute -c <cfg> [--l2 <yaml>]``
           — creates base tables, Current* views, L1 invariant
           matviews, Investigation matviews.
        2. ``quicksight-gen data apply --execute -c <cfg> [--l2 <yaml>]``
           — runs the full emit_full_seed pipeline (90-day baseline +
           per-Rail densified plants + broken-rail plants + fanout
           boost).
        3. ``quicksight-gen data refresh --execute -c <cfg> [--l2 <yaml>]``
           — REFRESH MATERIALIZED VIEW so matviews see seeded rows
           (they don't auto-refresh — see CLAUDE.md operational
           footguns).

    ``env_overrides`` (typically ``{"QS_GEN_DEMO_DATABASE_URL":
    "<container-url>"}``) flows to each subprocess; ``load_config``
    in the subprocess picks up the env override (config.py:364) and
    writes against the container instead of the cfg-file URL.

    L2 instance follows the same `QS_GEN_TEST_L2_INSTANCE` env
    override the rest of the e2e suite respects; absent that, the CLI
    defaults to bundled spec_example.

    Raises ``RuntimeError`` on cfg-discovery failure or any subprocess
    non-zero exit. Caller (cmd_up_to) catches + maps to
    EXIT_NEEDS_OPERATOR; teardown still runs via the surrounding
    try/finally.
    """
    if name == "default":
        return
    if name != "local-pg":
        raise ValueError(f"seed_variant: unknown variant {name!r}")

    cfg_path = _resolve_seed_config_for_local_pg()
    if cfg_path is None:
        raise RuntimeError(
            "local-pg variant: no postgres-dialect cfg found "
            "(checked QS_GEN_CONFIG env, run/config.postgres.yaml). "
            "Create run/config.postgres.yaml (dialect: postgres) or "
            "set QS_GEN_CONFIG to a postgres-dialect cfg path."
        )

    env = {**os.environ, **env_overrides}
    l2_arg: list[str] = []
    l2_override = QS_GEN_TEST_L2_INSTANCE.get_or_none()
    if l2_override:
        l2_arg = ["--l2", str(l2_override)]

    seed_steps: tuple[tuple[str, ...], ...] = (
        ("schema", "apply"),
        ("data", "apply"),
        ("data", "refresh"),
    )
    cli = str(_VENV_BIN / "quicksight-gen")
    for step in seed_steps:
        cmd = [cli, *step, "--execute", "-c", str(cfg_path), *l2_arg]
        print(f"runner: variant-seed [{name}] {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"variant-seed [{name}] failed at step {' '.join(step)!r} "
                f"(rc={result.returncode})"
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
    """
    target = runs_dir if runs_dir is not None else RUNS_DIR
    if not target.exists():
        return []
    candidates = [p for p in target.iterdir() if p.is_dir() and _RUN_ID_PATTERN.match(p.name)]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = candidates[retain:]
    for old in to_delete:
        shutil.rmtree(old)
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


def _options_from_args(args: argparse.Namespace) -> RunOptions:
    """Build a RunOptions from the argparse Namespace. Defaults are baked in
    (most flags `default=False`/`default=None` from `_build_parser`).

    Y.2.gate.c.6.xdist-safety: ``fuzz_seed_value`` is resolved here (not
    argparse) — operator overrides via ``QS_GEN_FUZZ_SEED`` env (the canonical
    pinning channel per audit §7.11), else random per invocation.
    """
    return RunOptions(
        only=getattr(args, "only", None),
        parallel=getattr(args, "parallel", 1),
        variants=getattr(args, "variants", "default"),
        fuzz_seeds=getattr(args, "fuzz_seeds", 1),
        fuzz_seed_value=resolve_fuzz_seed_value(),
        skip_cheap=getattr(args, "skip_cheap", False),
        keep_on_failure=getattr(args, "keep_on_failure", False),
        trace_all=getattr(args, "trace_all", False),
        allow_dirty_deploy=getattr(args, "allow_dirty_deploy", False),
    )


def cmd_up_to(args: argparse.Namespace) -> int:
    """Run the test chain up to and including the named layer.

    Pre-flight: probes the named layer's required deps (c.8). On any failure,
    prints the operator-actionable message and exits NEEDS_OPERATOR — does NOT
    auto-invoke any interactive flow (b.14.4).

    Y.2.gate.b.10 — for layers >= deploy, refuses on tracked-changes dirty
    state unless `--allow-dirty-deploy` (or `QS_GEN_RUNNER_YES=1`) is set.

    Then dispatches the chain (c.5): stop on first layer failure (b.9 LOCKED:
    cross-layer = sequential). Stubbed layers (deploy/api/browser pending cfg
    loading + variants) report skipped + pass-through so the chain doesn't
    falsely block.
    """
    options = _options_from_args(args)

    if _is_deploy_or_later(args.layer) and _is_dirty():
        if not options.allow_dirty_deploy and not QS_GEN_RUNNER_YES.get_or_none():
            print(
                "runner: refusing to deploy: tracked changes present "
                "(commit / stash, or pass --allow-dirty-deploy)",
                file=sys.stderr,
            )
            return EXIT_NEEDS_OPERATOR

    failures = probe_dependencies(args.layer)
    if failures:
        for failure in failures:
            print(f"runner: probe-fail [{failure.kind}] {failure.message}", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR

    run_id = create_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"runner: run_id={run_id}")
    print(f"runner: run_dir={run_dir.relative_to(REPO_ROOT)}")
    print(f"runner: up_to={args.layer}")
    if options.fuzz_seed_value is not None:
        print(f"runner: fuzz_seed={options.fuzz_seed_value} (pin via QS_GEN_FUZZ_SEED env to repro)")

    # Y.2.gate.b.2.impl — resolve variants up front; spin up
    # containers ONCE for the whole chain (not per layer). Multi-
    # variant fan-out lands with c.6.async; today b.2.impl scopes to
    # a single non-default variant per invocation.
    try:
        variants = resolve_variants(options.variants)
    except ValueError as exc:
        print(f"runner: {exc}", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR
    if len(variants) > 1:
        print(
            f"runner: multi-variant fan-out not yet wired (Y.2.gate.c.6.async); "
            f"got --variants={options.variants}",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    variant = variants[0]
    if variant != "default":
        print(f"runner: variant={variant} (spinning up container...)")
    variant_env, variant_handle = setup_variant(variant)
    if variant_env:
        for key, val in variant_env.items():
            print(f"runner: variant-env [{key}]={val[:60]}..." if len(val) > 60 else f"runner: variant-env [{key}]={val}")

    chain = chain_through(args.layer)
    print(f"runner: chain={chain}")
    final_code = EXIT_SUCCESS
    layer_results: list[LayerResult] = []
    try:
        # Y.2.gate.b.2.impl.schema — non-default variants spin up
        # empty containers; seed schema + data + matview refresh
        # before the first DB-touching layer dispatches. Skipped
        # when the chain is unit-only (saves ~30s on type-check
        # iteration). Wrapped inside the try block so a seed failure
        # still hits teardown_variant via the finally.
        if variant != "default" and any(layer in DB_TOUCHING_LAYERS for layer in chain):
            print(f"runner: variant={variant} seeding (schema apply + data apply + data refresh)...")
            try:
                seed_variant(variant, variant_env)
            except RuntimeError as exc:
                print(f"runner: variant-seed failed: {exc}", file=sys.stderr)
                return EXIT_NEEDS_OPERATOR

        for layer in chain:
            # Y.2.gate.b.8.impl — `--skip-cheap` short-circuits cheap
            # layers (unit, db) when the current SHA already has a
            # green cache marker. Defensive: dirty-SHA / non-skippable
            # / no-cache all degrade to "run normally". Cache lookup
            # is variant-aware: a green marker for variant X doesn't
            # signal green for variant Y.
            if options.skip_cheap and is_layer_cached_green(layer, variant=variant):
                print(f"runner: layer-cached [{layer}] skipped (--skip-cheap, current SHA already green for variant={variant})")
                cached_result = LayerResult(
                    layer=layer, exit_code=0, duration_seconds=0.0, skipped=True,
                )
                layer_results.append(cached_result)
                continue

            result = dispatch_layer(layer, run_dir, options, variant_env=variant_env)
            layer_results.append(result)
            marker = "skip" if result.skipped else ("ok" if result.passed else "FAIL")
            print(f"runner: layer-{marker} [{layer}] rc={result.exit_code} duration={result.duration_seconds:.2f}s")
            if not result.passed:
                print(f"runner: stop-on-first-failure — chain halted at {layer}", file=sys.stderr)
                final_code = EXIT_FAILURE
                break
            # Y.2.gate.b.8.impl — record the green pass so a future
            # --skip-cheap on the same SHA + variant can short-circuit.
            if not result.skipped and result.passed:
                write_cache_marker(layer, duration_seconds=result.duration_seconds, variant=variant)
    finally:
        teardown_variant(variant_handle)
        if variant_handle is not None:
            print(f"runner: variant={variant} container torn down")

    collect_run_outputs(run_dir, layer_results)
    print(f"runner: wrote {(run_dir / 'timings.json').relative_to(REPO_ROOT)}")
    report_drift(run_dir)

    pruned = prune_old_runs()
    if pruned:
        print(f"runner: pruned {len(pruned)} old run(s) (retained last {RUNS_RETAIN_N})")
    return final_code


def cmd_up(args: argparse.Namespace) -> int:
    """Boot dependencies. scope = local | aws | all (default)."""
    print(f"runner: up scope={args.scope} — not implemented yet (Y.2.gate.l.2)")
    return EXIT_NEEDS_OPERATOR


def cmd_down(args: argparse.Namespace) -> int:
    """Tear down dependencies. scope = local | aws | all (default).

    Destructive — requires --yes (Y.2.gate.b.14.3 destructive-op opt-in)."""
    if not args.yes and not QS_GEN_RUNNER_YES.get_or_none():
        print("runner: 'down' is destructive — pass --yes (or set QS_GEN_RUNNER_YES=1)", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR
    print(f"runner: down scope={args.scope} --yes — not implemented yet (Y.2.gate.l.2)")
    return EXIT_NEEDS_OPERATOR


def cmd_status(args: argparse.Namespace) -> int:
    """Show what's currently running. --cost for hourly cost estimate."""
    print(f"runner: status (cost={args.cost}) — not implemented yet (Y.2.gate.l.2)")
    return EXIT_NEEDS_OPERATOR


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


def cmd_sweep(args: argparse.Namespace) -> int:
    """Y.2.gate.c.9 — clean orphan QuickSight resources tagged
    ``Harness:e2e``.

    Replaces ``scripts/sweep_harness_orphans.py`` (deletion of the
    standalone script is `Y.2.gate.f.8`). Same default: dry-run
    (collect + print). Pass ``--yes`` (or set ``QS_GEN_RUNNER_YES=1``,
    matching the destructive-op convention from `b.14.3`) to actually
    delete.

    Tag set: ``Harness:e2e`` — production deploys don't carry that
    tag (they wear ``ManagedBy:quicksight-gen`` + optional
    ``L2Instance:<prefix>``), so this is safe against the production
    resource graph.

    Exit codes:
      0 — clean (dry-run completed OR delete completed)
      2 — needs operator (AWS creds expired / config not found)
    """
    from quicksight_gen.common.config import load_config

    # Sweep only needs aws_account_id + aws_region — any cfg has those.
    # Lookup mirrors tests/e2e/conftest.py::cfg with the per-dialect
    # files added (Phase P split run/config.yaml → per-dialect).
    config_path: Path | None = None
    # Soft-fallback: registry's must_be_file validator would raise on
    # a non-existent pin; sweep is best-effort + cfg discovery has
    # other candidates, so soak the absence rather than fail-loud.
    explicit = os.environ.get(QS_GEN_CONFIG.name)
    if explicit:
        candidate = Path(explicit)
        if candidate.exists():
            config_path = candidate
    if config_path is None:
        for candidate in (
            REPO_ROOT / "run" / "config.yaml",
            REPO_ROOT / "config.yaml",
            REPO_ROOT / "run" / "config.postgres.yaml",
            REPO_ROOT / "run" / "config.oracle.yaml",
        ):
            if candidate.exists():
                config_path = candidate
                break
    if config_path is None:
        print(
            "runner: sweep — no config.yaml found in repo "
            "(checked QS_GEN_CONFIG, run/config.yaml, config.yaml, "
            "run/config.{postgres,oracle}.yaml); cannot resolve "
            "AWS account/region.",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR

    cfg = load_config(str(config_path))
    try:
        import boto3
    except ImportError as exc:
        print(f"runner: sweep — boto3 missing: {exc}", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR

    # Imports of harness cleanup helpers are deferred to avoid pulling
    # in tests/ at module import time. Y.2.gate.f.8 will lift the
    # helpers into ``quicksight_gen/_dev/cleanup.py`` and drop the
    # ``sys.path`` + ``importlib`` dance.
    import importlib

    sys.path.insert(0, str(REPO_ROOT / "tests" / "e2e"))
    try:
        _harness_cleanup = importlib.import_module("_harness_cleanup")
    finally:
        sys.path.pop(0)
    _collect_resources_matching_tag: Any = (
        _harness_cleanup._collect_resources_matching_tag
    )
    sweep_qs_resources_by_tag: Any = (
        _harness_cleanup.sweep_qs_resources_by_tag
    )

    # boto3-stubs's huge per-service overload union confuses pyright
    # — Unknown branches leak through on most-cases. Suppress narrowly.
    client: Any = boto3.client(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]: boto3-stubs huge overload union confuses pyright (X.2.o.5)
        "quicksight", region_name=cfg.aws_region,
    )

    confirm = bool(args.yes) or bool(QS_GEN_RUNNER_YES.get_or_none())
    tag_key, tag_value = "Harness", "e2e"

    if not confirm:
        # Dry-run: collect-only, no deletes. Same shape as
        # scripts/sweep_harness_orphans.py without --confirm.
        try:
            raw_matched = _collect_resources_matching_tag(
                client, cfg.aws_account_id,
                tag_key=tag_key, tag_value=tag_value,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"runner: sweep — collect failed: {exc!r}",
                file=sys.stderr,
            )
            return EXIT_NEEDS_OPERATOR
        # Cast away the Unknown type that pyright assigns to the
        # dynamic ``_harness_cleanup`` import. The function's declared
        # return type is ``dict[str, list[tuple[str, str]]]``; the lift
        # to ``quicksight_gen/_dev/cleanup.py`` (Y.2.gate.f.8) drops
        # this cast.
        matched = cast("dict[str, list[tuple[str, str]]]", raw_matched)
        print(
            f"runner: sweep DRY-RUN — would delete resources tagged "
            f"{tag_key}={tag_value} in {cfg.aws_region}:"
        )
        total = 0
        for kind, items in matched.items():
            print(f"  {kind}: {len(items)}")
            total += len(items)
            for resource_id, _arn in items:
                print(f"    - {resource_id}")
        print(f"  total: {total}")
        if total > 0:
            print("runner: re-run with --yes to actually delete.")
        return EXIT_SUCCESS

    print(
        f"runner: sweep --yes — deleting resources tagged "
        f"{tag_key}={tag_value} in {cfg.aws_region}"
    )
    try:
        raw_counts = sweep_qs_resources_by_tag(
            client, cfg.aws_account_id,
            tag_key=tag_key, tag_value=tag_value,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"runner: sweep — delete pass failed: {exc!r}", file=sys.stderr)
        return EXIT_NEEDS_OPERATOR
    counts = cast("dict[str, int]", raw_counts)
    print(f"runner: sweep deleted: {counts} (total={sum(counts.values())})")
    return EXIT_SUCCESS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_tests.sh",
        description="Test layer chain runner. See module docstring for full usage.",
    )
    subs = parser.add_subparsers(dest="verb", required=True)

    p_up_to = subs.add_parser("up_to", help="Run the chain up to and including <layer>")
    p_up_to.add_argument("layer", choices=LAYERS)
    # Y.2.gate.c.7 — flag plumbing.
    p_up_to.add_argument(
        "--only",
        metavar="<expr>",
        default=None,
        help="pytest -k <expr>: narrow within-layer tests. Active now.",
    )
    p_up_to.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="within-variant pytest-xdist worker count (default 1 = serial). Mirrors ./run_e2e.sh --parallel.",
    )
    p_up_to.add_argument(
        "--variants",
        metavar="<set>",
        default="default",
        help="variant fan-out (dialect / l2-instance / fuzz-seed) — consumed by c.6.",
    )
    p_up_to.add_argument(
        "--fuzz-seeds",
        type=int,
        default=1,
        metavar="N",
        help="property-testing fuzz seed sample size (default 1; opt-in heavier; b.1 lock).",
    )
    p_up_to.add_argument(
        "--skip-cheap",
        action="store_true",
        help="skip layers 1-2 if green for current SHA earlier in session — consumed by future cache work (b.8).",
    )
    p_up_to.add_argument(
        "--keep-on-failure",
        action="store_true",
        help="don't tear down ephemeral state on failure — consumed when lifecycle commands land (l.2 / b.14.3 / f.5).",
    )
    p_up_to.add_argument(
        "--trace-all",
        action="store_true",
        help="Playwright capture every test (failure-only is the default). Threads QS_GEN_TRACE_ALL=1 to subprocesses (consumed by c.11).",
    )
    p_up_to.add_argument(
        "--allow-dirty-deploy",
        action="store_true",
        help="bypass the tracked-changes refusal on layers >= deploy (b.10).",
    )
    p_up_to.set_defaults(func=cmd_up_to)

    p_up = subs.add_parser("up", help="Boot dependencies (default scope = all)")
    p_up.add_argument("scope", nargs="?", default="all", choices=["local", "aws", "all"])
    p_up.set_defaults(func=cmd_up)

    p_down = subs.add_parser("down", help="Tear down dependencies (default scope = all)")
    p_down.add_argument("scope", nargs="?", default="all", choices=["local", "aws", "all"])
    p_down.add_argument("--yes", action="store_true", help="confirm destructive op")
    p_down.set_defaults(func=cmd_down)

    p_status = subs.add_parser("status", help="Show what's currently running")
    p_status.add_argument("--cost", action="store_true", help="include hourly cost estimate")
    p_status.set_defaults(func=cmd_status)

    p_sweep = subs.add_parser("sweep", help="Clean orphan resources tagged ManagedBy:quicksight-gen")
    p_sweep.add_argument("--yes", action="store_true", help="confirm destructive op")
    p_sweep.set_defaults(func=cmd_sweep)

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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(_normalize_argv(raw))
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
