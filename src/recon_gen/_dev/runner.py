"""Test layer chain runner — orchestrates the layered test chain with per-run
output isolation + timing-diff drift detection.

Invoked via the ``./run_tests.sh`` bash shim at repo root; the shim
``exec``s into ``python -m recon_gen._dev.runner``.

Verbs:
    up_to <layer>     Run the chain up to and including <layer>.
                      Layers: unit | db | app2 | deploy | api | browser
                      (pyright folds into unit via the conftest sessionstart
                      gate). ``unit`` is variant-independent — it runs ONCE
                      as a prelude before the matrix fans out (Y.2.gate.n),
                      not once per cell. Equivalent forms: ``up_to=<layer>``
                      and ``up_to <layer>``.
    up [scope]        Boot dependencies. scope = local | aws | all (default).
    down [scope]      Tear down dependencies. scope as above.
    status [--cost]   Show what's currently running.
    sweep             Clean orphan resources (tagged ManagedBy:recon-gen).

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
import asyncio
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

if TYPE_CHECKING:
    from recon_gen.common.config import Config

from recon_gen.common.env_keys import (
    RECON_E2E_PAGE_TIMEOUT,
    RECON_E2E_USER_ARN,
    RECON_GEN_CONFIG,
    RECON_GEN_DB_TABLE_PREFIX,
    RECON_GEN_DEMO_DATABASE_URL,
    RECON_GEN_DEPLOYMENT_NAME,
    RECON_GEN_E2E,
    RECON_GEN_FUZZ_SEED,
    RECON_GEN_LAYER,
    RECON_GEN_RUN_DIR,
    RECON_GEN_RUNNER_CI,
    RECON_GEN_RUNNER_YES,
    RECON_GEN_TEST_L2_INSTANCE,
    RECON_GEN_TRACE_ALL,
)
from recon_gen.common.variant import (
    DialectCode,
    VariantSpec,
    parse_dialects,
    parse_scenarios,
    parse_targets,
    parse_variant_code,
    partition_matrix,
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
#   "aws"             — AWS creds present + not expired (sts:GetCallerIdentity).
#   "docker"          — Docker daemon reachable (`docker ps`).
#   "qs_arn"          — RECON_E2E_USER_ARN set (browser e2e signs embed URLs as this user).
#   "aws_rds_running" — Y.2.gate.l.3 — cfg-declared RDS cluster + instance
#                       are 'available'. Refuses dispatch BEFORE container
#                       spin-up so a stopped cluster doesn't burn ~5min of
#                       deploy chatter to surface "connection refused".
#                       Skipped (passes through) when cfg fields are unset
#                       — operator hasn't opted in to cfg-driven lifecycle.
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
    "deploy": frozenset({"aws", "docker", "aws_rds_running"}),
    "api": frozenset({"aws", "docker", "aws_rds_running"}),
    "browser": frozenset({"aws", "docker", "qs_arn", "aws_rds_running"}),
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

    ``env_overrides`` (Y.2.gate.h+i.0): caller-supplied env additions merged
    on top of `os.environ` for this subprocess only. Used by `_probe_aws` to
    inject `AWS_PROFILE` from cfg before the SSO-default check fails — keeps
    the long-lived-IAM-keys path working even when the operator's ambient
    SSO token is expired.
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


def _probe_aws() -> ProbeFailure | None:
    """Y.2.gate.c.8 + b.14.4 — check AWS creds via ``aws sts get-caller-identity``.

    Returns ``None`` if creds work. On expired/missing/unknown failure, returns
    a ``ProbeFailure`` whose message tells the operator exactly what to type
    (`! aws sso login`); we **never** auto-invoke the SSO browser flow.

    Y.2.gate.h+i.0 — honors ``cfg.auth.aws_profile`` if discoverable. The
    runner injects `AWS_PROFILE` into subprocess env_overrides at variant
    setup, but the probe runs BEFORE that — so without this lookup, a probe
    running on an expired SSO ambient session would fail even when the cfg
    points at a long-lived IAM-keys profile that would have worked. Same
    cfg-discovery shape as `_probe_qs_e2e_user_arn`.
    """
    env_overrides: dict[str, str] | None = None
    cfg_path = _resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)
    if cfg_path is not None:
        try:
            from recon_gen.common.config import load_config  # noqa: PLC0415 — lazy
            cfg = load_config(str(cfg_path))
        except Exception:  # noqa: BLE001 — bad cfg surfaces elsewhere; here we just want a yes/no
            cfg = None
        if cfg is not None and cfg.auth is not None and cfg.auth.aws_profile is not None:
            env_overrides = {"AWS_PROFILE": cfg.auth.aws_profile}
    result = _run_probe_subprocess(
        ["aws", "sts", "get-caller-identity"], env_overrides=env_overrides,
    )
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
    """Check that the runner can satisfy ``RECON_E2E_USER_ARN``.

    Three paths are accepted (any one passes the probe):

    1. **Env var set** — operator-managed (legacy / CI).
    2. **Cfg `auth.quicksight_user_arn` set** — explicit override
       (combined h+i.0 spike escape hatch).
    3. **Cfg `auth.aws_profile` set** — h.1 derivation will fire
       inside ``_run_one_variant`` via ``_derive_qs_user_arn``.

    Y.2.gate.b.15 — registry call also runs the IAM-ARN regex
    validator on PRESENCE, so a malformed ARN surfaces here instead
    of inside the boto embed-URL call later.

    Cfg discovery uses the same default-candidate list as
    ``_resolve_runner_cfg_path("default")`` — handles the common
    "operator runs against external Aurora with `auth:` block in
    `run/config.postgres.yaml`" case without per-variant context.
    """
    if RECON_E2E_USER_ARN.get_or_none():
        return None
    cfg_path = _resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)
    if cfg_path is not None:
        try:
            from recon_gen.common.config import load_config  # noqa: PLC0415 — lazy: only load cfg when probing
            cfg = load_config(str(cfg_path))
        except Exception:  # noqa: BLE001 — bad cfg surfaces elsewhere; here we just want a yes/no
            cfg = None
        if cfg is not None and cfg.auth is not None and (
            cfg.auth.quicksight_user_arn is not None
            or cfg.auth.aws_profile is not None
        ):
            return None
    return ProbeFailure(
        kind="qs_arn_unset",
        message=(
            "RECON_E2E_USER_ARN unset and no cfg auth block found. "
            "Either export the QuickSight user ARN, or add an "
            "`auth: { aws_profile: <name> }` block to "
            "run/config.<dialect>.yaml (combined spike: "
            "docs/audits/y_2_gate_h_i_combined_spike.md)."
        ),
    )


def _probe_aws_rds_running() -> ProbeFailure | None:
    """Y.2.gate.l.3 — verify cfg-declared RDS resources are 'available'
    before dispatching deploy/api/browser layers.

    Without this probe a stopped Aurora cluster surfaces as a
    psycopg ``connection refused`` deep inside the deploy step's
    first SQL call — operator wastes ~5 min on container spin-up +
    boto3 chatter before seeing the actionable error. With it, the
    chain refuses at dispatch time with "run `./run_tests.sh up aws`
    first".

    Skipped (passes through) when both ``cfg.aws_pg_cluster_id`` and
    ``cfg.aws_oracle_instance_id`` are unset — that's the operator
    opting out of cfg-driven lifecycle (e.g., they manage clusters
    via console / Terraform / etc., or they're using legacy
    pre-gate.l shape). Same opt-in shape as ``cmd_up_aws`` /
    ``cmd_status``.

    Loads cfg via the lifecycle-helper which also injects
    ``AWS_PROFILE`` from ``cfg.auth.aws_profile`` so the boto3 RDS
    calls hit the long-lived IAM keys (matches gate.h.1 pattern).
    """
    cfg = _load_runner_cfg_for_lifecycle()
    if cfg is None:
        # No cfg discoverable — fall through; the `aws` probe will
        # surface the auth-or-cfg failure on its own. Layered probes
        # don't double-fail.
        return None
    if cfg.aws_pg_cluster_id is None and cfg.aws_oracle_instance_id is None:
        return None

    from recon_gen.common.aws_rds import RdsResource, get_status  # noqa: PLC0415 — lazy
    failures: list[str] = []

    if cfg.aws_pg_cluster_id is not None:
        resource = RdsResource(
            kind="cluster",
            identifier=cfg.aws_pg_cluster_id,
            aws_region=cfg.aws_region,
        )
        try:
            status = get_status(resource)
            if status != "available":
                failures.append(
                    f"PG cluster {cfg.aws_pg_cluster_id!r}: {status} "
                    f"(not 'available')"
                )
        except Exception as exc:  # noqa: BLE001 — surface AWS errors to operator
            failures.append(
                f"PG cluster {cfg.aws_pg_cluster_id!r}: ERROR — {exc}"
            )

    if cfg.aws_oracle_instance_id is not None:
        resource = RdsResource(
            kind="instance",
            identifier=cfg.aws_oracle_instance_id,
            aws_region=cfg.aws_region,
        )
        try:
            status = get_status(resource)
            if status != "available":
                failures.append(
                    f"Oracle instance {cfg.aws_oracle_instance_id!r}: "
                    f"{status} (not 'available')"
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(
                f"Oracle instance {cfg.aws_oracle_instance_id!r}: "
                f"ERROR — {exc}"
            )

    if not failures:
        return None

    return ProbeFailure(
        kind="aws_rds_not_running",
        message=(
            "Cfg-declared RDS resources are not all 'available':\n  "
            + "\n  ".join(failures)
            + "\nRun './run_tests.sh up aws' first (or bring the "
            "resources up via console) before re-invoking."
        ),
    )


_ProbeFunc = Callable[[], "ProbeFailure | None"]
_PROBE_FUNCTIONS: Final[dict[str, _ProbeFunc]] = {
    "aws": _probe_aws,
    "docker": _probe_docker,
    "qs_arn": _probe_qs_e2e_user_arn,
    "aws_rds_running": _probe_aws_rds_running,
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
    allow_dirty_deploy: bool = False
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
    if opts.trace_all:
        env_addl[RECON_GEN_TRACE_ALL.name] = "1"
    if opts.fuzz_seed_value is not None:
        env_addl[RECON_GEN_FUZZ_SEED.name] = str(opts.fuzz_seed_value)
    # Y.2.gate.k.1.coverage — every pytest layer (everything except `deploy`,
    # which is a `recon-gen json apply` CLI call) writes a per-(variant,
    # layer) `.coverage.<variant>.<layer>` data file when `--coverage` is set.
    # `run_dir` is the per-variant dir (or `runs/<id>/_prelude` for the unit
    # prelude); `run_dir.name` is the variant code (`sp_pg_lo`) or `_prelude`.
    # `--cov-report=` (empty) suppresses the per-layer terminal report — the
    # CI `coverage` aggregator (W.8b) globs every `.coverage.*` artifact and
    # `coverage combine`s them, so a per-layer report is just stdout.log clutter.
    _is_pytest_layer = layer in ("unit", "db", "app2", "api", "browser")
    _cov_args: list[str] = (
        ["--cov=recon_gen", "--cov-report="]
        if opts.coverage and _is_pytest_layer
        else []
    )
    if opts.coverage and _is_pytest_layer:
        # COVERAGE_FILE is coverage.py's standard env var (not a RECON_GEN_*
        # registry var); set it on the layer's subprocess env directly.
        env_addl["COVERAGE_FILE"] = str(run_dir / f".coverage.{run_dir.name}.{layer}")
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
        return (cmd, env_addl)
    if layer == "db":
        # 3a — DB-touching pytest (behind RECON_GEN_E2E=1). Three test files:
        #   - test_dataset_sql_smoke.py: parametrized over 37 datasets;
        #     substitutes QS `<<$param>>` placeholders with declared
        #     defaults, wraps in `WHERE 1=0`, runs against live DB.
        #   - test_demo_apply_row_counts.py: asserts ≥1 row in every
        #     named matview the seed populates (k.1.absorb — Phase 2 of
        #     Y.2.gate.k.1+k.6 spike).
        #   - test_audit_pdf_render_verify.py: invokes
        #     `recon-gen audit apply --execute` + `audit verify`
        #     against the variant's seeded DB (k.1.absorb-audit —
        #     Phase 2.5). Reads RECON_GEN_TEST_L2_INSTANCE so the audit
        #     CLI picks the variant's synthesized yaml and finds the
        #     `<spec.name>_*` prefixed tables the seed populated.
        # All three flow through the same RECON_GEN_TEST_L2_INSTANCE-aware
        # test resolution, so the variant's synthesized prefix is the
        # one source of truth for which tables to query / render from.
        # Real DB connection comes from cfg; until cfg loading lands the test
        # itself fails fast if cfg is missing. That's the expected shape.
        cmd = [
            str(_VENV_BIN / "pytest"),
            "tests/e2e/test_dataset_sql_smoke.py",
            "tests/e2e/test_demo_apply_row_counts.py",
            "tests/e2e/test_audit_pdf_render_verify.py",
            "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        # j.6 — see unit layer comment.
        cmd += _cov_args
        cmd += ["-n", str(opts.parallel) if opts.parallel > 1 else "auto"]
        return (cmd, {**env_addl, RECON_GEN_E2E.name: "1"})
    if layer == "app2":
        # b.3.impl.layer — App2 e2e (HTMX dialect, Playwright WebKit
        # against the App2 Starlette server). Stub-fetcher renderer tests:
        # `test_html2_executives.py`, `test_html2_money_trail.py`, and
        # `test_html2_l2ft.py` (Y.2.app2.cde.l2ft-wiring.c — proves the
        # auto-derived MULTI_SELECT pushdown dropdowns render + the
        # repeated-key `?param_<name>=A&param_<name>=B` refetch wire).
        # `test_html2_executives_live.py` uses `make_tree_db_fetcher(
        # tree_app, cfg)` against the variant DB — `connect_demo_db(cfg)`
        # reads `RECON_GEN_DEMO_DATABASE_URL` env override (config.py:364),
        # so the variant URL flows through naturally. Behind `RECON_GEN_E2E=1`
        # like every other tests/e2e/ file. NO AWS contact (audit §7.10 LOCKED).
        #
        # X.2.u.6.followon — `test_dashboard_driver.py` joins the list: its 8
        # `App2Driver.smoke()` protocol-parity tests (`test_showcase_*` /
        # `test_app2_*`) need only Playwright + the bundled smoke app (no DB,
        # no AWS), so this is their home.
        #
        # Z.B.14 (2026-05-15) — `-m "not browser"` deselects the 3
        # `@pytest.mark.browser` tests in `test_dashboard_driver.py`
        # (`test_qs_l1_*`). Earlier reasoning that they "skip cleanly here
        # (no `RECON_E2E_USER_ARN`)" is no longer true: Y.2.gate.h.1 made the
        # runner auto-derive `RECON_E2E_USER_ARN` from `cfg.auth.aws_profile`,
        # so the QS-bound tests now actually try to run pre-deploy and probe
        # whatever dashboard happens to be left in QS from a prior run
        # (cross-cell coupling). The Z.B.12 verification matrix surfaced
        # this on `sq_or_aw`: 3 timeouts on `[role="tab"]` against a stale
        # spec_example dashboard. The browser layer already picks these up
        # via `-m browser` against `tests/e2e/`, so no parallel addition
        # needed there. The other html2 files in this list carry no marks
        # (verified) so `-m "not browser"` keeps them in.
        cmd = [
            str(_VENV_BIN / "pytest"),
            "tests/e2e/test_html2_executives.py",
            "tests/e2e/test_html2_executives_live.py",
            "tests/e2e/test_html2_money_trail.py",
            "tests/e2e/test_html2_l2ft.py",
            # X.2.h.5 — Table sort + pagination round-trip (smoke app,
            # Playwright-only, no DB / no AWS).
            "tests/e2e/test_html2_table_pagination.py",
            "tests/e2e/test_dashboard_driver.py",
            "-m", "not browser",
            "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        # j.6 — see unit layer comment.
        cmd += _cov_args
        cmd += ["-n", str(opts.parallel) if opts.parallel > 1 else "auto"]
        return (cmd, {**env_addl, RECON_GEN_E2E.name: "1"})
    if layer == "deploy":
        # Y.2.gate.c.5.deploy — `recon-gen json apply --execute` against
        # the cfg + L2 the runner discovered. Two cfg-path sources, in order:
        # (1) `variant_env[RECON_GEN_CONFIG]` — `_run_one_variant` only injects
        #     this for non-default variants (local-pg / local-oracle /
        #     local-sqlite, where the per-variant cfg matches the variant's
        #     dialect-flavored DB). For the default variant `_run_one_variant`
        #     doesn't inject it because the variant's cfg-discovery is
        #     subprocess-side via `tests/e2e/conftest.py` etc.
        # (2) Fall back to `_resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)`
        #     so the default variant still finds run/config.{postgres,oracle}.yaml.
        # L2 path (`RECON_GEN_TEST_L2_INSTANCE`) is always set by `_run_one_variant`
        # when cfg.default_l2_instance is configured (h.6); when it isn't we
        # genuinely can't deploy and fall through to the dispatch-skip path
        # with an actionable error.
        ve = variant_env or {}
        cfg_str = ve.get(RECON_GEN_CONFIG.name)
        if cfg_str is None:
            fallback_cfg_path = _resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)
            cfg_str = str(fallback_cfg_path) if fallback_cfg_path is not None else None
        l2_str = ve.get(RECON_GEN_TEST_L2_INSTANCE.name)
        if cfg_str is None or l2_str is None:
            # Caller's dispatch_layer will print `dispatch-skip` — operator
            # gets a clear "set cfg.default_l2_instance:" pointer because
            # without both we genuinely cannot construct the command.
            return None
        out_dir = run_dir / "deploy" / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(_VENV_BIN / "recon-gen"), "json", "apply",
            "--execute",
            "-c", cfg_str,
            "--l2", l2_str,
            "-o", str(out_dir),
        ]
        # Note: `--allow-dirty-deploy` is a runner-only flag (cmd_up_to
        # gates the chain on it); the inner `recon-gen json apply`
        # CLI doesn't have a tracked-changes refusal of its own, so no
        # pass-through is needed.
        return (cmd, env_addl)
    if layer == "api":
        # Y.2.gate.c.5.api — boto3-only e2e tests verifying deployed QS
        # resources via `describe_*` calls. Pytest mark `api` (set by
        # pytestmark in every e2e file) selects the right files; no
        # hardcoded test-file list to drift. Behind `RECON_GEN_E2E=1`.
        #
        # Default `-n 4` (capped) — pre-cap (2026-05-17), this layer
        # ran ``-n auto`` (= cpu_count, ~10-12 workers on a beefy Mac)
        # and produced flaky ``ThrottlingException`` on
        # ``DescribeDashboardDefinition`` because 12 workers × 4 apps
        # × multiple describe calls each hit QuickSight's per-account
        # API rate ceiling (~100 req/s). Same logic as the browser
        # layer's existing -n 4 cap ("browser tier heavy enough that
        # 8+ workers thrash QS embed limits") — boto3 fan-out hammers
        # the same backend, just with a different protocol. Operator
        # can still override via ``--parallel=N`` for serial debug or
        # explicit bump.
        cmd = [
            str(_VENV_BIN / "pytest"), "tests/e2e/", "-m", "api", "-q",
        ]
        if opts.only:
            cmd += ["-k", opts.only]
        cmd += _cov_args
        cmd += ["-n", str(opts.parallel) if opts.parallel > 1 else "4"]
        return (cmd, {**env_addl, RECON_GEN_E2E.name: "1"})
    if layer == "browser":
        # Y.2.gate.c.5.browser — Playwright WebKit e2e against deployed QS
        # embed URLs. Pytest mark `browser`. Default `-n 4` per existing
        # `./run_e2e.sh` pattern (browser tier is heavy enough that 8+
        # workers thrash QS embed limits). Behind `RECON_GEN_E2E=1`.
        # `RECON_E2E_USER_ARN` already in subprocess env via h.1 derivation.
        #
        # Z.B.12-followup (2026-05-15) — split into two sequential pytest
        # invocations via a shell wrapper:
        #   1. The main browser tier with `-n 4` workers, ignoring the
        #      audit-dashboard-agreement file.
        #   2. ONLY ``test_audit_dashboard_agreement.py`` with ``-n 1`` —
        #      its module-scoped ``seeded_audit`` fixture re-applies the
        #      dialect schema (DROP MATERIALIZED VIEW + CREATE …); on
        #      ``aw`` target with persistent Aurora the conftest's
        #      ``--dist loadgroup`` bump has been observed not to pin
        #      both parametrizations onto a single worker, so multiple
        #      workers race the schema apply (Oracle DDL auto-commits;
        #      ORA-12006 fires when worker B's CREATE collides with
        #      worker A's). Forcing a single worker is the only race-free
        #      way; the test only contributes a few minutes total so the
        #      sequential run isn't a meaningful wall-clock cost. CI's
        #      ``e2e-pg-browser`` job already follows this pattern (the
        #      file runs as a separate step).
        nworkers = str(opts.parallel) if opts.parallel > 1 else "4"
        only = ["-k", opts.only] if opts.only else []
        agree_file = "tests/e2e/test_audit_dashboard_agreement.py"
        main_cmd = [
            str(_VENV_BIN / "pytest"), "tests/e2e/",
            f"--ignore={agree_file}",
            "-m", "browser", "-q",
            *only, *_cov_args,
            "-n", nworkers,
            # Y.7-followup — auto-retry a flaky browser test
            # (``pytest-rerunfailures``, in the [dev] extra) instead of
            # failing the whole chain on it. The browser tier walks a
            # live QuickSight embed under ``-n 4`` worker contention;
            # the structure tests ("every visual rendered, one snapshot
            # budget") occasionally lose a visual from the DOM when QS
            # is rate-limiting under the concurrent load (Oracle-`aw`
            # worst: slower per-query latency = workers hold QS sessions
            # longer = more concurrent pressure — the underlying queries
            # are ~8 ms and the data returns; it's a render-timing
            # flake, passes on re-run / in isolation). The rerun happens
            # INSIDE this same pytest invocation (xdist re-runs on the
            # same worker), not by restarting the chain — so a flake
            # costs ~one test re-run, not a whole ``unit→…→browser``
            # cycle. A test that's genuinely broken fails 3× → still
            # halts.
            "--reruns", "2", "--reruns-delay", "10",
        ]
        agree_cmd = [
            str(_VENV_BIN / "pytest"), agree_file, "-q",
            *only, *_cov_args,
            "-n", "1",
            "--reruns", "2", "--reruns-delay", "10",
        ]
        # ``bash -c '… && …'`` chains the two pytest invocations
        # sequentially; the shell exits non-zero if EITHER fails, which
        # is what dispatch_layer's stop-on-first-failure honors. Quote
        # each argv element so paths/args with spaces survive (none in
        # practice but defensive).
        chained = (
            " ".join(shlex.quote(a) for a in main_cmd)
            + " && "
            + " ".join(shlex.quote(a) for a in agree_cmd)
        )
        cmd = ["bash", "-c", chained]
        # Bump the per-page Playwright timeout for the browser layer to 60 s
        # (matches the CI `e2e-pg-browser` job). The default 30 s
        # (tests/e2e/conftest.py) is fine for a local-pg container but too
        # tight for the `aw` target's remote Aurora / Oracle. Operator-set
        # value wins.
        browser_env = {**env_addl, RECON_GEN_E2E.name: "1"}
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
) -> tuple[int, float]:
    """Spawn ``cmd`` as a subprocess; tee stdout/stderr to operator's
    terminal AND to the named log files; return (returncode, duration).

    Daemon threads drain each pipe so a full buffer on one stream can't
    deadlock the other. ``terminal_prefix`` flows to ``_tee_stream`` for
    per-variant line tagging in multi-variant fan-out.

    Y.2.gate.c.6.async — extracted from ``dispatch_layer`` so
    ``seed_variant`` (and any future subprocess) can capture + prefix
    with the same contract.
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
        proc.wait()
        # Drain both pipes before declaring done — wait() doesn't wait
        # on the reader threads.
        t_out.join()
        t_err.join()
    duration = time.monotonic() - start
    return proc.returncode, duration


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
        # Y.2.gate.c.5 — None means the layer needed preconditions that
        # weren't satisfied (most often: deploy without a cfg-discovered
        # cfg path or default L2 instance). Print a clear pointer to the
        # cfg fields the operator can set to unblock.
        if layer == "deploy":
            print(
                f"{terminal_prefix}runner: dispatch-skip [{layer}] cfg "
                f"missing — set `auth.aws_profile` (h+i.0) AND "
                f"`default_l2_instance` (h.6) in run/config.<dialect>.yaml"
            )
        else:
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
    returncode, duration = _spawn_with_tee(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        terminal_prefix=terminal_prefix,
    )

    # Re-write cmd.json with the result. Append shape (rather than two
    # files) keeps the per-layer summary in one place. Defensive
    # ensure-dir handles the race window (see _ensure_dir comment).
    cmd_meta["exit_code"] = returncode
    cmd_meta["duration_seconds"] = duration
    _ensure_dir()
    cmd_path.write_text(json.dumps(cmd_meta, indent=2) + "\n")

    return LayerResult(
        layer=layer, exit_code=returncode, duration_seconds=duration,
    )


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


# Y.2.gate.m.2 — variant axis. The runner expresses variants as
# 3-axis cells `scenario × dialect × target` (`common/variant.py`);
# operators narrow the matrix via `--scenarios` / `--dialects` /
# `--targets` (or pin a single cell via `--variants=<sc>_<di>_<ta>`).
# `setup_variant` dispatches on `(spec.dialect, spec.target)` to
# spin up local testcontainers (`lo`) or wire the operator's external
# Aurora/Oracle (`aw`).

# Layers whose subprocess needs the variant's DB connection threaded
# through (RECON_GEN_DEMO_DATABASE_URL etc.). Unit doesn't need it.
# `app2` (b.3.impl.layer) reads the variant DB via the App2 fetcher
# (`make_tree_db_fetcher`), so it lives here.
DB_TOUCHING_LAYERS: Final = ("db", "app2", "deploy", "api", "browser")

# m.4.f — layers that need an AWS-reachable datasource. Lo-target
# cells seed a localhost container that QuickSight in AWS can't reach;
# running deploy → api → browser against a localhost-pointed datasource
# is a guaranteed dead pointer (deploy succeeds, but every dashboard
# render times out because QS can't query localhost). Cap lo cells at
# `app2` (the local-Docker terminal, locked by audit §7.10).
AWS_TOUCHING_LAYERS: Final = ("deploy", "api", "browser")

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
# Pinned password matches the testcontainers `OracleDbContainer`
# behavior when `oracle_password` is explicitly set. Without pinning,
# testcontainers randomizes per invocation (`hex(randbits(24))`) and
# the adopt path can't predict the URL on subsequent runs.
ORACLE_REUSE_PASSWORD: Final = "qs-gen-test-pwd-2026"  # typing-smell: ignore[qs-gen-prefix]: local Docker fixture password — not an AWS resource ID, not multi-tenant; the prefix is incidental string content, not a Config-prefixed resource name


def _oracle_container_name_for(spec: VariantSpec) -> str:
    """j.5 — per-cell Oracle container name. The cell suffix prevents
    sibling Oracle cells from racing on docker `create(name=...)`.
    Same cell across runs → same name → adopt path hits."""
    return f"{ORACLE_REUSE_CONTAINER_PREFIX}{spec.name}"


def cell_chain(spec: VariantSpec, requested_chain: list[str]) -> list[str]:
    """m.4.f — filter the requested chain to layers this cell can run.

    - ``target=aw`` cells run every layer the operator asked for;
      passes ``requested_chain`` through unchanged.
    - ``target=lo`` cells drop ``deploy`` / ``api`` / ``browser`` —
      QuickSight can't reach the localhost container that backs the
      cell's seeded data, so those layers would deploy a dead-pointer
      dashboard. The natural lo terminal is ``app2`` (b.3.impl.layer
      LOCKED that as the local-Docker fast-feedback layer).

    The operator's ``up_to=<layer>`` is the *upper* cap; this function
    further trims based on what the cell can physically support. Both
    caps compose: ``up_to=db`` for any cell already excludes app2+.
    """
    if spec.target == "aw":
        return requested_chain
    return [layer for layer in requested_chain if layer not in AWS_TOUCHING_LAYERS]


# m.2.a hard-cut hint — operator's old `--variants=local-pg` shape no
# longer accepted. Map to the new sub-flag form.
_LEGACY_VARIANT_HINTS: Final[dict[str, str]] = {
    "local-pg": "--dialects=pg --targets=lo",
    "local-oracle": "--dialects=or --targets=lo",
    "local-sqlite": "--dialects=sl --targets=lo",
    "default": "(no flags = full matrix; or --dialects=pg,or --targets=aw for the AWS subset)",
}


def _check_legacy_variant_names(arg: str) -> None:
    """Surface the m.2 hard-cut migration on the legacy `--variants` shape.

    The new `--variants=<sc>_<di>_<ta>` codes (`sp_pg_lo`, `f42_or_lo`)
    pass through `parse_variant_code` unchanged; legacy values would
    fail there with a regex error that doesn't tell the operator how
    to fix it. Catch them here, point at the right sub-flag form.
    """
    raw = [v.strip() for v in arg.split(",") if v.strip()]
    legacy_seen = [v for v in raw if v in _LEGACY_VARIANT_HINTS]
    if legacy_seen:
        first = legacy_seen[0]
        raise ValueError(
            f"--variants={first!r} is the legacy shape (Y.2.gate.m.2 hard-cut); "
            f"use {_LEGACY_VARIANT_HINTS[first]} instead"
        )


class _SqliteHandle:
    """Y.2.gate.b.2.impl.sqlite — teardown handle for the local-sqlite
    variant. Mirrors the duck-typed ``.stop()`` shape that
    ``teardown_variant`` calls on testcontainer handles, but unlinks
    the per-invocation SQLite DB file + temp cfg instead of stopping
    a Docker container.
    """

    def __init__(self, db_path: Path, cfg_path: Path) -> None:
        self.db_path = db_path
        self.cfg_path = cfg_path

    def stop(self) -> None:
        """Best-effort cleanup of the per-invocation files. Sidecar
        contract preserved — never raises."""
        for path in (self.db_path, self.cfg_path):
            try:
                path.unlink()
            except (FileNotFoundError, OSError):
                # Already gone or unwritable — drop it.
                pass


def _setup_local_sqlite() -> tuple[dict[str, str], object | None]:
    """Create the per-invocation SQLite DB file + minimal cfg, return
    the env overrides + handle the variant lifecycle expects.

    Allocates a fresh temp directory (``tempfile.mkdtemp(prefix=
    "qs-gen-sqlite-")``) so the DB and cfg files are isolated from
    other concurrent invocations. The DB file is created empty —
    ``schema apply`` populates it via ``connect_demo_db`` (which
    handles the SQLite branch + ``STDDEV_SAMP`` aggregate
    registration). The cfg carries:

    - ``dialect: sqlite`` so emit_schema / emit_full_seed /
      refresh_matviews_sql pick the SQLite arms of the dialect helpers;
    - ``demo_database_url: sqlite:///<path>`` so connect_demo_db
      points at the right file;
    - ``aws_account_id`` + ``aws_region`` placeholders that satisfy
      ``Config`` validators (the local-sqlite variant never touches
      AWS — these fields are required by the loader but unused).

    Both ``RECON_GEN_DEMO_DATABASE_URL`` and ``RECON_GEN_CONFIG`` end up in
    the env overrides so DB-touching layer subprocesses (``db``,
    ``app2``) load the right cfg + connect to the right file.
    """
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="qs-gen-sqlite-"))  # typing-smell: ignore[qs-gen-prefix]: tempfile dir name only — not an AWS resource ID, just disambiguates per-invocation runner-managed temp dirs from other tools' tempfiles for operator-visible cleanup
    db_path = tmp_dir / "demo.sqlite"
    cfg_path = tmp_dir / "config.sqlite.yaml"
    # Z.C — synth cfg uses ``deployment_name`` + ``db_table_prefix``
    # (Z.C.2 collapse). The runner injects per-cell overrides via
    # RECON_GEN_DEPLOYMENT_NAME / RECON_GEN_DB_TABLE_PREFIX env vars in
    # ``_run_one_variant`` so multi-cell parallel runs don't collide
    # — the values written here are the per-invocation defaults that
    # apply when a cell doesn't override.
    cfg_path.write_text(
        f"aws_account_id: \"111122223333\"\n"
        f"aws_region: \"us-east-1\"\n"
        f"dialect: sqlite\n"
        f"demo_database_url: \"sqlite:///{db_path}\"\n"
        f"deployment_name: \"qsgen-sqlite\"\n"
        f"db_table_prefix: \"qsgen_sqlite\"\n"
    )
    env: dict[str, str] = {
        RECON_GEN_DEMO_DATABASE_URL.name: f"sqlite:///{db_path}",
        RECON_GEN_CONFIG.name: str(cfg_path),
    }
    return env, _SqliteHandle(db_path=db_path, cfg_path=cfg_path)


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

    url = (
        f"oracle+oracledb://system:{password}@localhost:{host_port}"
        f"/?service_name=FREEPDB1"
    )
    return url, _PersistentContainerHandle(name=name)


def _start_fresh_oracle_container(
    name: str, password: str,
) -> tuple[str, _PersistentContainerHandle]:
    """j.5 — start a new named Oracle container with the stable
    password. Returns the URL + a persistent handle (`.stop()` no-op
    so the container outlives this invocation and the next run can
    adopt it).
    """
    from testcontainers.oracle import OracleDbContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs  # noqa: PLC0415

    # gvenzl/oracle-free:23-faststart — pre-initialized DB starts in
    # seconds vs. the multi-minute cold-start on :slim. Image is
    # heavier (~3 GB) but the time savings dominate test-loop
    # economics. Service name defaults to FREEPDB1 (the oracle-free
    # image's pluggable DB).
    container = OracleDbContainer(
        "gvenzl/oracle-free:23-faststart",
        oracle_password=password,
    ).with_name(name)
    container.start()  # type: ignore[no-untyped-call]: testcontainers .start() lacks return-type hint
    url: str = container.get_connection_url()
    return url, _PersistentContainerHandle(name=name)


def setup_variant(spec: VariantSpec) -> tuple[dict[str, str], object | None]:
    """Bring up the resources a variant cell needs. Returns
    ``(env_overrides, handle_for_teardown)``. Caller threads
    env_overrides into the pytest subprocess and passes handle to
    `teardown_variant` after.

    Dispatch by ``(spec.dialect, spec.target)``:

    - ``target=aw`` (any dialect): no-op. Operator's external DB
      (Aurora cluster, etc.); cfg-discovery for AWS auth happens
      separately in ``_run_one_variant``.
    - ``(pg, lo)``: postgres:17-alpine testcontainer; URL override.
    - ``(or, lo)``: gvenzl/oracle-free:23-faststart testcontainer;
      URL override.
    - ``(sl, lo)``: per-invocation SQLite tempdir + cfg; both
      ``RECON_GEN_DEMO_DATABASE_URL`` and ``RECON_GEN_CONFIG`` overrides
      (no on-disk cfg under ``run/`` for sqlite — it's ephemeral).
    - ``(sl, aw)``: rejected upstream by ``VariantSpec.is_valid()``
      (sqlite is file-based; QS can't reach it remotely). Defensive
      raise here for completeness.

    PG container takes ~10-15s to start. Oracle container
    (``gvenzl/oracle-free:23-faststart``) takes ~20-30s — still
    fast for a fresh Oracle DB. SQLite is instant (file-create
    only). Lifetime is the chain (one DB / container reused across
    all layers in a single ``up_to`` invocation), not per-layer.
    """
    if spec.target == "aw":
        return {}, None
    # Y.2.gate.k.1+k.6 — runner CI-mode: skip Docker for lo targets
    # when the workflow YAML pre-provisions the DB via GHA service
    # containers. Operator (or workflow) sets RECON_GEN_RUNNER_CI=1 +
    # RECON_GEN_DEMO_DATABASE_URL=<service-container-url>; setup_variant
    # is then a no-op and the variant URL passes through unchanged.
    # SQLite has no container to skip — but we still honor CI mode
    # for symmetry (the workflow can pre-create the SQLite file).
    if RECON_GEN_RUNNER_CI.get_or_none():
        # Loud-fail if the operator set CI mode but forgot the URL —
        # we'd otherwise silently fall back to cfg.demo_database_url
        # and break in confusing ways downstream.
        url = RECON_GEN_DEMO_DATABASE_URL.require()
        return {RECON_GEN_DEMO_DATABASE_URL.name: url}, None
    # target == "lo" — local container or sqlite tempfile.
    if spec.dialect == "pg":
        # Lazy-import: testcontainers requires Docker, which not every
        # operator has. Importing only on demand keeps non-Docker
        # invocations clean.
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs

        # Pin to the exact PG version we run in production (Aurora 17).
        container = PostgresContainer("postgres:17-alpine")
        container.start()
        raw_url: str = container.get_connection_url()  # type: ignore[no-untyped-call]: testcontainers method has no type annotations
        return {RECON_GEN_DEMO_DATABASE_URL.name: _normalize_pg_url(raw_url)}, container
    if spec.dialect == "or":
        # Y.2.gate.j.5 — Oracle container reuse. Image cold-start is
        # ~30-60s; recreating per chain run dominates iteration time.
        # `_get_or_start_oracle_container` adopts the named container
        # if it's already running (subsequent runs pay ~0s startup),
        # else starts a fresh one with the stable name. Either way the
        # returned handle's `.stop()` is a no-op so `teardown_variant`
        # leaves the container running for the next run.
        # Oracle URL flows through unchanged — ``oracle_dsn()`` in
        # ``common/db.py`` already accepts the SQLAlchemy-style
        # ``oracle+oracledb://...`` form.
        url, handle = _get_or_start_oracle_container(
            _oracle_container_name_for(spec), ORACLE_REUSE_PASSWORD,
        )
        return {RECON_GEN_DEMO_DATABASE_URL.name: url}, handle
    if spec.dialect == "sl":
        # Y.2.gate.b.2.impl.sqlite — no Docker, no network. Create
        # a tempdir with a SQLite DB file + minimal cfg pointing at
        # it; both env overrides flow to layer subprocesses. Teardown
        # unlinks both files via the ``_SqliteHandle.stop()`` duck-
        # typed contract ``teardown_variant`` already calls.
        return _setup_local_sqlite()
    raise ValueError(
        f"setup_variant: unhandled (dialect={spec.dialect!r}, target={spec.target!r})"
    )


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


def _dump_top_queries_for_variant(
    spec: VariantSpec,
    variant_env: dict[str, str],
    run_dir: Path,
    terminal_prefix: str,
) -> None:
    """Y.2.gate.f.4 — best-effort per-cell top-queries snapshot.

    Fires after every chain that touched a DB layer. Output:
    ``<run_dir>/<spec.name>/db-perf/top-queries.md``. Cumulative across
    everything the variant's chain ran (db smoke + app2 + e2e + browser
    if reached); ``pg_stat_statements`` / ``v$sqlstats`` carry the totals.

    Filter narrows to queries whose text contains the L2 instance prefix
    so we drop the operator's unrelated workloads on the shared DB.

    Never raises — connection / query / format failures all degrade to a
    ``format_skipped`` marker so a flaky stats view can't break the
    chain. SQLite has no equivalent stats view (skipped cleanly).
    """
    # Lazy imports keep startup fast and avoid pulling psycopg/oracledb
    # into pyright-strict scope unless this helper actually fires.
    from recon_gen._dev import perf
    from recon_gen.common.config import load_config
    from recon_gen.common.db import connect_demo_db
    from recon_gen.common.sql import Dialect

    out_dir = run_dir / spec.name / "db-perf"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "top-queries.md"
    title = f"Top expensive queries — {spec.name}"

    cfg_path = variant_env.get(RECON_GEN_CONFIG.name)
    if not cfg_path:
        out_path.write_text(perf.format_skipped(
            title=title, dialect="?",
            reason=f"no {RECON_GEN_CONFIG.name} in variant_env",
        ))
        return

    try:
        cfg = load_config(cfg_path)
    except Exception as e:  # noqa: BLE001 — never break the chain
        out_path.write_text(perf.format_skipped(
            title=title, dialect="?",
            reason=f"could not load cfg from {cfg_path!r}: {e!r}",
        ))
        return

    dialect_str = perf.dialect_name(cfg.dialect)
    if cfg.dialect is Dialect.SQLITE:
        out_path.write_text(perf.format_skipped(
            title=title, dialect=dialect_str,
            reason="SQLite has no pg_stat_statements / v$sqlstats equivalent",
        ))
        print(f"{terminal_prefix}runner: db-perf [{spec.name}] skipped (sqlite)")
        return

    # Filter on the DB-table prefix so we drop the operator's
    # unrelated traffic on the shared DB. Z.C: was cfg.l2_instance_prefix;
    # cfg.db_table_prefix is the direct replacement (same wire shape —
    # the LIKE pattern matches `<prefix>_*` table refs in the query log).
    like_pattern = cfg.db_table_prefix

    try:
        conn = connect_demo_db(cfg)
    except Exception as e:  # noqa: BLE001
        out_path.write_text(perf.format_skipped(
            title=title, dialect=dialect_str,
            reason=f"could not connect: {e!r}",
        ))
        return

    try:
        rows = perf.fetch_top_queries(
            conn, cfg.dialect, like_pattern=like_pattern, top=50,
        )
    except Exception as e:  # noqa: BLE001
        # Most likely: pg_stat_statements not installed (PG) or
        # ORA-00942/ORA-01031 on v$sqlstats (Oracle, no privilege).
        out_path.write_text(perf.format_skipped(
            title=title, dialect=dialect_str,
            reason=(
                f"stats view unavailable: {type(e).__name__}: {e}. "
                f"Pre-req for postgres: CREATE EXTENSION pg_stat_statements; "
                f"for oracle: SELECT on v$sqlstats."
            ),
        ))
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass

    try:
        out_path.write_text(perf.format_top_queries_markdown(
            title=title, dialect=dialect_str,
            like_pattern=like_pattern, rows=rows,
        ))
    except Exception as e:  # noqa: BLE001 — formatter shouldn't break chain
        out_path.write_text(perf.format_skipped(
            title=title, dialect=dialect_str,
            reason=f"format failed: {type(e).__name__}: {e}",
        ))
        return

    print(
        f"{terminal_prefix}runner: db-perf [{spec.name}] "
        f"wrote {len(rows)} rows to {out_path}"
    )


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
# Cfg discovery priority for local-pg: RECON_GEN_CONFIG env override
# wins (operator pin), else run/config.postgres.yaml (PG-dialect cfg
# the container expects). run/config.yaml is intentionally skipped —
# it may be Oracle-flavored, which doesn't match a Postgres container.
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


def _resolve_seed_config_for_dialect(dialect: DialectCode) -> Path | None:
    """Per-dialect cfg dispatcher — returns the dialect-flavored cfg
    for ``pg`` / ``or``, ``None`` for ``sl`` (the per-invocation
    cfg is generated by ``setup_variant`` and threaded via
    ``env_overrides[RECON_GEN_CONFIG]``, not discovered on disk).

    For ``aw`` targets the same per-dialect cfg is also right —
    operator's external Aurora is already addressable via
    ``run/config.<dialect>.yaml``. ``_resolve_runner_cfg_path``
    falls back to the ``_DEFAULT_RUNNER_CFG_CANDIDATES`` list when
    this returns None (e.g., operator only has ``run/config.yaml``).
    """
    if dialect == "pg":
        return _resolve_seed_config(_LOCAL_PG_CFG_CANDIDATES)
    if dialect == "or":
        return _resolve_seed_config(_LOCAL_ORACLE_CFG_CANDIDATES)
    return None


# Y.2.gate.h+i.0 — runner-side cfg discovery for AWS auth. Used by
# ``_run_one_variant`` to load the cfg in the parent process so we can
# inject ``AWS_PROFILE`` and derive ``RECON_E2E_USER_ARN`` before
# dispatching layers. Variant-specific cfg wins (so local-pg's auth
# matches its dialect-flavored cfg); falls through to a generic
# candidate list for ``default`` (the operator's external DB).
_DEFAULT_RUNNER_CFG_CANDIDATES: Final = (
    "run/config.yaml",
    "run/config.postgres.yaml",
    "run/config.oracle.yaml",
)


def _resolve_runner_cfg_path(spec: VariantSpec) -> Path | None:
    """Find the cfg file the runner reads for AWS auth + ARN derivation.

    Per-dialect first (``pg`` → ``run/config.postgres.yaml``,
    ``or`` → ``run/config.oracle.yaml``); falls through to the
    candidate list when the dialect-specific cfg isn't present
    (operator may only have ``run/config.yaml``). Returns ``None``
    when nothing matches — caller skips auth wiring and the layer's
    own probes catch any operator-action need.
    """
    dialect_cfg = _resolve_seed_config_for_dialect(spec.dialect)
    if dialect_cfg is not None:
        return dialect_cfg
    return _resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)


def _derive_qs_user_arn(cfg: "Config") -> str:
    """Y.2.gate.h.1 — derive QS user ARN from AWS identity.

    Combined h+i.0 spike (`docs/audits/y_2_gate_h_i_combined_spike.md`):
    cfg override wins (explicit `cfg.auth.quicksight_user_arn`); else
    derive via ``sts:GetCallerIdentity`` → ``quicksight:ListUsers``
    filter on ``PrincipalId == "federated/iam/<UserId>"``. The join
    key was validated live against three identity types (IAM user,
    assumed-role, root) in account 470656905821 — all three QS users'
    ``PrincipalId`` matched the STS UserId exactly.

    Honors ``cfg.auth.aws_profile`` by passing it to ``boto3.Session``
    so the derivation runs against the same creds the layer
    subprocesses will use (subprocess env carries ``AWS_PROFILE``;
    parent process needs the explicit kwarg).
    """
    if cfg.auth and cfg.auth.quicksight_user_arn:
        return cfg.auth.quicksight_user_arn

    # Lazy import: boto3 cold-start is ~300ms; this function only fires
    # when the chain reaches a layer needing ``qs_arn``, not on every
    # runner invocation.
    import boto3  # noqa: PLC0415 — keep cold-start light when h.1 unused

    profile = cfg.auth.aws_profile if cfg.auth else None
    # boto3-stubs's huge per-service overload union confuses pyright —
    # narrow Any suppression matches the pattern in cmd_sweep below.
    session: Any = (  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]: boto3-stubs huge overload union confuses pyright (X.2.o.5)
        boto3.Session(profile_name=profile)
        if profile is not None
        else boto3.Session()
    )

    sts: Any = session.client("sts", region_name=cfg.aws_region)
    identity = sts.get_caller_identity()
    user_id = str(identity["UserId"])
    target_principal = f"federated/iam/{user_id}"

    qs: Any = session.client("quicksight", region_name=cfg.aws_region)
    paginator = qs.get_paginator("list_users")
    for page in paginator.paginate(
        AwsAccountId=cfg.aws_account_id, Namespace="default",
    ):
        for user in page["UserList"]:
            if user["PrincipalId"] == target_principal:
                return str(user["Arn"])

    raise RuntimeError(
        f"AWS principal UserId {user_id!r} (Arn {identity['Arn']!r}) "
        f"does not match any QuickSight user in account "
        f"{cfg.aws_account_id} namespace 'default'. Either authenticate "
        f"as a registered QS user, or set 'auth.quicksight_user_arn:' "
        f"in cfg yaml. (Spike: docs/audits/y_2_gate_h_i_combined_spike.md)"
    )


def seed_variant(
    spec: VariantSpec,
    env_overrides: dict[str, str],
    *,
    run_dir: Path | None = None,
    terminal_prefix: str = "",
) -> None:
    """Y.2.gate.b.2.impl.schema — bootstrap the variant cell's DB so
    the db / deploy / api / browser layers have something to query.

    Both ``target=aw`` and ``target=lo`` cells run the same 3-step
    seed flow. For aw, the cfg's ``demo_database_url`` (operator's
    external Aurora) is the target; the runner-driven seed creates
    only the L2-prefixed tables (``<spec.name>_*``) so it never
    touches operator-managed data under other prefixes. For lo,
    the env-overridden URL points at the per-cell container.

    Spawns three CLI subprocesses in dependency order against the
    cell's URL:

        1. ``recon-gen schema apply --execute -c <cfg> [--l2 <yaml>]``
           — creates base tables, Current* views, L1 invariant
           matviews, Investigation matviews.
        2. ``recon-gen data apply --execute -c <cfg> [--l2 <yaml>]``
           — runs the full emit_full_seed pipeline (90-day baseline +
           per-Rail densified plants + broken-rail plants + fanout
           boost).
        3. ``recon-gen data refresh --execute -c <cfg> [--l2 <yaml>]``
           — REFRESH MATERIALIZED VIEW so matviews see seeded rows
           (they don't auto-refresh — see CLAUDE.md operational
           footguns).

    ``env_overrides`` (typically ``{"RECON_GEN_DEMO_DATABASE_URL":
    "<container-url>"}``) flows to each subprocess; ``load_config``
    in the subprocess picks up the env override (config.py:364) and
    writes against the container instead of the cfg-file URL.

    L2 instance follows ``RECON_GEN_TEST_L2_INSTANCE``; ``_run_one_variant``
    sets it per-spec from the scenario code (sp/sq/us → fixture path).

    Raises ``RuntimeError`` on cfg-discovery failure or any subprocess
    non-zero exit. Caller (``_run_one_variant``) catches + maps to
    EXIT_NEEDS_OPERATOR; teardown still runs via the surrounding
    try/finally.
    """
    # Discover dialect-flavored cfg — same lookup path for both aw + lo
    # cells. For aw, the cfg's `demo_database_url` is the operator's
    # Aurora/Oracle (cfg-driven). For lo, the env-overridden URL flows
    # through `load_config` and points at the per-cell container.
    if spec.dialect == "pg":
        cfg_path = _resolve_seed_config_for_dialect("pg")
        if cfg_path is None:
            raise RuntimeError(
                f"variant {spec.name}: no postgres-dialect cfg found "
                f"(checked RECON_GEN_CONFIG env, run/config.postgres.yaml). "
                f"Create run/config.postgres.yaml (dialect: postgres) or "
                f"set RECON_GEN_CONFIG to a postgres-dialect cfg path."
            )
    elif spec.dialect == "or":
        cfg_path = _resolve_seed_config_for_dialect("or")
        if cfg_path is None:
            raise RuntimeError(
                f"variant {spec.name}: no oracle-dialect cfg found "
                f"(checked RECON_GEN_CONFIG env, run/config.oracle.yaml). "
                f"Create run/config.oracle.yaml (dialect: oracle) or "
                f"set RECON_GEN_CONFIG to an oracle-dialect cfg path."
            )
    elif spec.dialect == "sl":
        # Y.2.gate.b.2.impl.sqlite — cfg path comes from
        # ``setup_variant`` (it generates the per-invocation cfg + DB
        # file under a tempdir and returns the cfg path in
        # ``env_overrides[RECON_GEN_CONFIG]``). No on-disk cfg in
        # ``run/`` — the SQLite variant is by-design ephemeral
        # per-invocation. If the override isn't there, setup_variant
        # was bypassed; fail loud.
        cfg_str = env_overrides.get(RECON_GEN_CONFIG.name)
        if not cfg_str:
            raise RuntimeError(
                f"variant {spec.name}: setup_variant must set "
                f"RECON_GEN_CONFIG in env_overrides (it generates the "
                f"per-invocation cfg). Did the caller skip setup_variant?"
            )
        cfg_path = Path(cfg_str)
    else:
        raise ValueError(f"seed_variant: unhandled dialect {spec.dialect!r}")

    env = {**os.environ, **env_overrides}
    l2_arg: list[str] = []
    # m.2.g hotfix — env_overrides wins over os.environ. Per-cell
    # injection (`_run_one_variant` sets RECON_GEN_TEST_L2_INSTANCE per
    # spec) MUST flow to the seed CLI; reading os.environ directly
    # would give every parallel cell the same L2 (or none), causing
    # cells to seed under the wrong prefix and downstream smoke tests
    # to fail with "table does not exist" against the right prefix.
    l2_path_str = env_overrides.get(
        RECON_GEN_TEST_L2_INSTANCE.name,
    ) or RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if l2_path_str:
        l2_arg = ["--l2", str(l2_path_str)]

    seed_steps: tuple[tuple[str, ...], ...] = (
        ("schema", "apply"),
        ("data", "apply"),
        ("data", "refresh"),
    )
    cli = str(_VENV_BIN / "recon-gen")
    # Y.2.gate.c.6.async — capture per-step stdout/stderr to
    # ``<run_dir>/seed/<step>.{stdout,stderr}.log`` so multi-variant
    # fan-out leaves a per-cell trail (cell lives in ``run_dir``
    # itself, e.g., ``runs/<id>/sp_pg_lo/seed/...``).
    seed_dir: Path | None = None
    if run_dir is not None:
        seed_dir = run_dir / "seed"
        seed_dir.mkdir(parents=True, exist_ok=True)
    for step in seed_steps:
        cmd = [cli, *step, "--execute", "-c", str(cfg_path), *l2_arg]
        print(f"{terminal_prefix}runner: variant-seed [{spec.name}] {' '.join(cmd)}")
        step_label = "-".join(step)
        if seed_dir is not None:
            stdout_path = seed_dir / f"{step_label}.stdout.log"
            stderr_path = seed_dir / f"{step_label}.stderr.log"
        else:
            stdout_path = Path(os.devnull)
            stderr_path = Path(os.devnull)
        returncode, _ = _spawn_with_tee(
            cmd, cwd=REPO_ROOT, env=env,
            stdout_path=stdout_path, stderr_path=stderr_path,
            terminal_prefix=terminal_prefix,
        )
        if returncode != 0:
            raise RuntimeError(
                f"variant-seed [{spec.name}] failed at step {' '.join(step)!r} "
                f"(rc={returncode})"
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


def _options_from_args(args: argparse.Namespace) -> RunOptions:
    """Build a RunOptions from the argparse Namespace. Defaults are baked in
    (most flags `default=False`/`default=None` from `_build_parser`).

    Y.2.gate.c.6.xdist-safety: ``fuzz_seed_value`` is resolved here (not
    argparse) — operator overrides via ``RECON_GEN_FUZZ_SEED`` env (the canonical
    pinning channel per audit §7.11), else random per invocation.
    """
    return RunOptions(
        only=getattr(args, "only", None),
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
        allow_dirty_deploy=getattr(args, "allow_dirty_deploy", False),
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
# sys.path injection (matches the cmd_sweep pattern for tests/e2e/
# helpers). Lifting random_l2_yaml into common/l2/ is a follow-up; for
# now the runner only ever runs from a source tree, not from a wheel.
_FUZZ_MODULE_DIR: Final = REPO_ROOT / "tests" / "l2"


def _load_random_l2_yaml() -> Callable[[int], str]:
    """Lazy-import ``random_l2_yaml`` from ``tests/l2/fuzz.py``.

    Lazy because importing ``tests.l2.fuzz`` pulls in PyYAML + the
    L2 primitives module — keeps the runner's cold-start light when
    no fuzz cells are in the matrix.
    """
    import importlib  # noqa: PLC0415 — lazy
    sys.path.insert(0, str(_FUZZ_MODULE_DIR.parent))
    try:
        fuzz_mod = importlib.import_module("l2.fuzz")
    finally:
        sys.path.pop(0)
    return cast("Callable[[int], str]", fuzz_mod.random_l2_yaml)


def _resolve_l2_yaml_for_spec(spec: VariantSpec, run_dir: Path) -> Path:
    """Map ``spec.scenario`` → on-disk L2 YAML path. Threaded into
    each variant's subprocess env via ``RECON_GEN_TEST_L2_INSTANCE``
    so the seed CLI + downstream e2e tests pick the right instance.

    m.4.f — ALL cells get a per-cell synthesized yaml under
    ``run_dir / "_synth_l2.yaml"``. The synthesis loads the source
    yaml (bundled fixture for sp/sq, operator-supplied for us, fuzz
    output for f<n>) and writes it back as-is.

    Z.C.9 (2026-05-15) follow-on: pre-Z.C this step also injected
    ``parsed["instance"] = spec.name`` so each cell got a unique
    ``<spec.name>_*`` DB-table prefix. Z.C dropped the L2 yaml's
    ``instance:`` field entirely (loader now hard-rejects it) — the
    per-cell DB-table prefix moves to ``cfg.db_table_prefix`` via the
    ``RECON_GEN_DB_TABLE_PREFIX=qsgen_<spec.name>`` env override that
    ``_run_one_variant`` injects per cell. Defensively pop any stray
    ``instance:`` key from the source (older fuzz output, hand-edited
    user yamls); the loader would reject it anyway.

    - DB schema prefix becomes ``qsgen_<spec.name>_*`` (e.g.,
      ``qsgen_sp_pg_aw_transactions``) — set via the per-cell env var
      override. Sister cells (sp_pg_aw + sp_or_aw + sq_pg_aw + ...)
      deploy to non-colliding tables on shared external Aurora.
    - QS resource ID prefix becomes ``qsgen-<spec.name>`` via the
      ``RECON_GEN_DEPLOYMENT_NAME`` env override.
    - Fuzz determinism preserved: same seed → same fuzzer output →
      same synthesized yaml (it's now byte-identical to the source).

    Operators reproduce a failed fuzz cell with
    ``--variants=f<seed>_<di>_<ta>`` — same spec.name → same env
    overrides → byte-identical deploy.
    """
    import yaml  # noqa: PLC0415 — lazy: only needed for synthesis path
    synth_path = run_dir / "_synth_l2.yaml"
    synth_path.parent.mkdir(parents=True, exist_ok=True)

    if spec.scenario in _NAMED_L2_FIXTURES:
        source_text = (_BUNDLED_L2_DIR / _NAMED_L2_FIXTURES[spec.scenario]).read_text()
    elif spec.scenario == "us":
        # __post_init__ guarantees user_yaml is set for us scenarios.
        assert spec.user_yaml is not None
        source_text = spec.user_yaml.read_text()
    elif spec.scenario.startswith("f"):
        # __post_init__ guarantees fuzz_seed is set + matches scenario.
        assert spec.fuzz_seed is not None
        random_l2_yaml = _load_random_l2_yaml()
        source_text = random_l2_yaml(spec.fuzz_seed)
    else:
        raise ValueError(f"unknown scenario code {spec.scenario!r}")

    # Defensive: pop any legacy `instance:` key that may slip in via
    # older fuzz output or operator-edited user yaml. yaml.safe_dump
    # preserves insertion order with sort_keys=False.
    parsed = cast("dict[str, Any]", yaml.safe_load(source_text))
    parsed.pop("instance", None)
    synth_text = yaml.safe_dump(
        parsed, sort_keys=False, default_flow_style=False, width=120,
    )
    synth_path.write_text(synth_text)
    return synth_path


def _write_cell_manifest(spec: VariantSpec, run_dir: Path) -> None:
    """m.3.c — write per-cell manifest.json with spec details + repro hint.

    Captures the seed value for fuzz cells so the operator can pin a
    failing run with ``--variants=f<seed>_<di>_<ta>`` for byte-identical
    reproduction. Written for ALL cells (not just fuzz) so the run dir's
    shape is consistent + future tooling can rely on the file existing.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "name": spec.name,
        "scenario": spec.scenario,
        "dialect": spec.dialect,
        "target": spec.target,
        "fuzz_seed": spec.fuzz_seed,
        "user_yaml": str(spec.user_yaml) if spec.user_yaml is not None else None,
    }
    if spec.fuzz_seed is not None:
        manifest["repro_hint"] = f"--variants={spec.name}"
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )


def _run_one_variant(
    spec: VariantSpec,
    run_dir: Path,
    options: RunOptions,
    chain: list[str],
    *,
    terminal_prefix: str = "",
) -> tuple[VariantSpec, list[LayerResult], int]:
    """Y.2.gate.c.6.async — run one variant cell's full chain end-to-end.

    Owns the cell's lifecycle: setup → seed (DB-touching layers only)
    → dispatch chain (stop on first fail per b.9) → teardown (always,
    via finally). Returns (spec, layer_results, exit_code) so the
    caller (single- or multi-cell) can aggregate.

    ``run_dir`` is the per-cell directory ``runs/<id>/<spec.name>/``
    (m.2.d — `<sc>_<di>_<ta>` naming so parallel cells don't collide
    on artifacts or AWS tags). ``terminal_prefix`` (e.g.,
    ``[sp_pg_lo] ``) is prepended to every line printed by this
    coroutine + every line streamed from spawned subprocesses, so
    interleaved fan-out output stays attributable.

    Soft fast-fail per c.6.async lock: a layer failure inside this
    cell breaks out of the layer loop but does NOT raise — the
    finally cleans up the container, the function returns the partial
    layer_results + EXIT_FAILURE. The caller's gather collects every
    cell's return regardless of pass/fail (no exception propagation
    kills sibling cells).
    """
    # m.4.f — per-cell layer cap. Lo cells naturally terminate at app2
    # because the deploy/api/browser layers need an AWS-reachable
    # datasource and lo cells point at localhost containers QS can't
    # see. Aw cells run whatever the operator asked for.
    chain = cell_chain(spec, chain)
    if not chain:
        print(
            f"{terminal_prefix}runner: variant={spec.name} skipped — "
            f"no layers run for this cell (target={spec.target} can't reach "
            f"the requested chain)",
        )
        return spec, [], EXIT_SUCCESS

    if spec.target == "lo":
        print(f"{terminal_prefix}runner: variant={spec.name} (spinning up container...)")
    variant_env, variant_handle = setup_variant(spec)
    # Y.2.gate.b.2.impl.oracle — also thread RECON_GEN_CONFIG into the
    # layer subprocess env. Layers that load cfg (e.g.,
    # tests/e2e/test_dataset_sql_smoke.py reading run/config.yaml by
    # default) MUST pick up the dialect-matching cfg or the connector
    # selection (cfg.dialect → psycopg vs oracledb) mismatches the
    # variant URL (env RECON_GEN_DEMO_DATABASE_URL), producing
    # "invalid connection option oracle+oracledb://" from psycopg.
    dialect_cfg = _resolve_seed_config_for_dialect(spec.dialect)
    if dialect_cfg is not None and RECON_GEN_CONFIG.name not in variant_env:
        variant_env[RECON_GEN_CONFIG.name] = str(dialect_cfg)

    # Z.C.4 — per-cell ``deployment_name`` + ``db_table_prefix`` overrides
    # so multi-cell parallel runs (sp_pg_lo / sq_pg_lo / fuzz cells) don't
    # collide on AWS resource IDs (``deployment_name`` weaves into every
    # qs-gen-* ID via ``Config.prefixed``) or DB table prefixes
    # (``db_table_prefix`` is the per-instance schema prefix every
    # `<prefix>_transactions`/etc. read keys off). Operator's
    # ``run/config.<dialect>.yaml`` carries STATIC values
    # (e.g. ``qsgen-postgres``) that're fine for single-cell runs but
    # collide the moment two cells share a target. The cfg loader
    # (Z.C.2) honors these env vars over yaml fields.
    #
    # ``spec.name`` is ``<sc>_<di>_<ta>`` (e.g. ``sp_pg_lo``) — already
    # snake_case, max 17 chars (well under cfg.db_table_prefix's 30-char
    # cap). DB prefix joins with `_`; QS-resource-ID prefix joins with
    # `-` (DB identifiers reject hyphens unquoted; QS resource IDs are
    # kebab-case). Use `qsgen` (no hyphen) as the literal prefix on both.
    if RECON_GEN_DEPLOYMENT_NAME.name not in variant_env:
        variant_env[RECON_GEN_DEPLOYMENT_NAME.name] = f"qsgen-{spec.name}"
    if RECON_GEN_DB_TABLE_PREFIX.name not in variant_env:
        variant_env[RECON_GEN_DB_TABLE_PREFIX.name] = f"qsgen_{spec.name}"

    # Y.2.gate.h+i.0 — AWS auth wiring. Inject AWS_PROFILE so subprocess
    # boto3 calls + AWS CLI invocations see the long-lived IAM-user creds
    # (combined spike candidate C). Derive RECON_E2E_USER_ARN from STS+ListUsers
    # so browser-layer embed signing works without operator-managed env vars.
    # Cfg-load failures here downgrade to "skip auth wiring + let layer
    # probes catch any operator-action need" — keeps unit/db layers running
    # when AWS is unreachable.
    runner_cfg_path = _resolve_runner_cfg_path(spec)
    if runner_cfg_path is not None:
        try:
            from recon_gen.common.config import load_config  # noqa: PLC0415 — lazy: cfg load only when AWS-touching layers in chain
            runner_cfg = load_config(str(runner_cfg_path))
        except Exception as exc:  # noqa: BLE001 — cfg load failures shouldn't block unit/db layers; surface for triage
            print(
                f"{terminal_prefix}runner: auth-cfg load failed "
                f"({type(exc).__name__}: {exc}); skipping AWS_PROFILE + "
                f"RECON_E2E_USER_ARN injection",
                file=sys.stderr,
            )
            runner_cfg = None
        if runner_cfg is not None and runner_cfg.auth is not None:
            if runner_cfg.auth.aws_profile is not None:
                variant_env["AWS_PROFILE"] = runner_cfg.auth.aws_profile
            # Derive RECON_E2E_USER_ARN only when chain reaches a layer
            # that needs it (qs_arn dep). Avoids the boto3 import +
            # ~1s ListUsers cost on unit-only invocations.
            if any(
                "qs_arn" in _LAYER_DEPS.get(layer, frozenset()) for layer in chain
            ):
                try:
                    arn = _derive_qs_user_arn(runner_cfg)
                    variant_env[RECON_E2E_USER_ARN.name] = arn
                except Exception as exc:  # noqa: BLE001 — surface as EXIT_NEEDS_OPERATOR
                    print(
                        f"{terminal_prefix}runner: RECON_E2E_USER_ARN derivation "
                        f"failed: {exc}",
                        file=sys.stderr,
                    )
                    return spec, [], EXIT_NEEDS_OPERATOR

    # m.3.c — per-cell manifest for one-line repro of fuzz failures.
    # Written before any subprocess fires so even a fast-fail cell
    # leaves a manifest behind for triage.
    _write_cell_manifest(spec, run_dir)

    # m.2.b + m.3.a — per-spec L2 instance injection. Scenario code
    # (sp/sq/us) determines which YAML the seed CLI + downstream e2e
    # tests use; fuzz scenarios (f<n>) synthesize per-cell into
    # run_dir/_synth_l2.yaml. Overrides cfg.default_l2_instance —
    # the matrix axis is the source of truth, not whatever the cfg
    # happened to default to.
    l2_yaml = _resolve_l2_yaml_for_spec(spec, run_dir)
    variant_env[RECON_GEN_TEST_L2_INSTANCE.name] = str(l2_yaml)
    # m.4.f — the synthesized yaml's `instance` field IS spec.name,
    # so cfg.db_table_prefix downstream (Z.C — formerly
    # cfg.with_l2_instance_prefix(instance.instance)) produces per-cell-
    # unique QS resource IDs naturally. The explicit
    # RECON_GEN_L2_INSTANCE_PREFIX env override is no longer set by the
    # runner (the env var stays in env_keys/cfg as a general-purpose
    # escape hatch, just no longer needed here).

    if variant_env:
        for key, val in variant_env.items():
            display = (val[:60] + "...") if len(val) > 60 else val
            print(f"{terminal_prefix}runner: variant-env [{key}]={display}")

    final_code = EXIT_SUCCESS
    layer_results: list[LayerResult] = []
    try:
        # Y.2.gate.b.2.impl.schema + m.4.f — both lo and aw cells need
        # seeding (lo containers start empty; aw operator's external
        # Aurora needs the per-cell <spec.name>_* tables created so
        # downstream dataset SQL can find them). Skipped when the
        # chain is unit-only (saves ~30s on type-check iteration).
        # Wrapped inside the try block so a seed failure still hits
        # teardown_variant via the finally.
        if any(layer in DB_TOUCHING_LAYERS for layer in chain):
            print(f"{terminal_prefix}runner: variant={spec.name} seeding (schema apply + data apply + data refresh)...")
            try:
                seed_variant(
                    spec, variant_env,
                    run_dir=run_dir, terminal_prefix=terminal_prefix,
                )
            except RuntimeError as exc:
                print(f"{terminal_prefix}runner: variant-seed failed: {exc}", file=sys.stderr)
                return spec, layer_results, EXIT_NEEDS_OPERATOR

        for layer in chain:
            # Y.2.gate.b.8.impl — `--skip-cheap` short-circuits cheap
            # layers (unit, db) when the current SHA already has a
            # green cache marker. Defensive: dirty-SHA / non-skippable
            # / no-cache all degrade to "run normally". Cache lookup
            # is variant-aware: a green marker for variant X doesn't
            # signal green for variant Y.
            if options.skip_cheap and is_layer_cached_green(layer, variant=spec.name):
                print(f"{terminal_prefix}runner: layer-cached [{layer}] skipped (--skip-cheap, current SHA already green for variant={spec.name})")
                cached_result = LayerResult(
                    layer=layer, exit_code=0, duration_seconds=0.0, skipped=True,
                )
                layer_results.append(cached_result)
                continue

            result = dispatch_layer(
                layer, run_dir, options,
                variant_env=variant_env, terminal_prefix=terminal_prefix,
            )
            # #986 followon, 2026-05-19 — extend the prelude's exit-5 + --only
            # tolerance to every per-cell layer. Same shape: when the operator
            # narrows with ``--only=<expr>`` aimed at a single later-layer test
            # (e.g. one browser-layer picker), every earlier layer's pytest
            # collects nothing and exits 5; without the rewrite the chain
            # halts at db / app2 / deploy on "no tests collected" before the
            # target layer ever dispatches. The matched layer's own invocation
            # still fails loudly if the expr typos.
            only_filter_matched_nothing = (
                result.exit_code == 5 and options.only is not None
            )
            if only_filter_matched_nothing:
                result = LayerResult(
                    layer=layer,
                    exit_code=0,
                    duration_seconds=result.duration_seconds,
                    skipped=True,
                )
                print(
                    f"{terminal_prefix}runner: layer-skip [{layer}] rc=5 → 0 "
                    f"(--only={options.only!r} matched no tests in this layer, "
                    f"deferring to later layers)"
                )
                layer_results.append(result)
                continue
            layer_results.append(result)
            marker = "skip" if result.skipped else ("ok" if result.passed else "FAIL")
            print(f"{terminal_prefix}runner: layer-{marker} [{layer}] rc={result.exit_code} duration={result.duration_seconds:.2f}s")
            if not result.passed:
                print(f"{terminal_prefix}runner: stop-on-first-failure — chain halted at {layer}", file=sys.stderr)
                final_code = EXIT_FAILURE
                break
            # Y.2.gate.b.8.impl — record the green pass so a future
            # --skip-cheap on the same SHA + variant can short-circuit.
            if not result.skipped and result.passed:
                write_cache_marker(layer, duration_seconds=result.duration_seconds, variant=spec.name)
    finally:
        # Y.2.gate.f.4 — best-effort top-queries snapshot per cell.
        # Same gating as the seed step (only fire when the chain
        # touched a DB layer); runs on success AND failure so triage
        # always has the perf signal. Wrapped in try/except as
        # additional defense in depth — the helper never raises, but
        # if it ever does we don't want it to leak past teardown.
        if any(layer in DB_TOUCHING_LAYERS for layer in chain):
            try:
                _dump_top_queries_for_variant(
                    spec, variant_env, run_dir, terminal_prefix,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"{terminal_prefix}runner: db-perf [{spec.name}] "
                    f"unexpected failure: {exc!r}",
                    file=sys.stderr,
                )
        # Y.2.gate.f.5 — --keep-on-failure suppresses container teardown
        # when the chain failed so the operator can poke at the deployed
        # state interactively. Cleanup later via `docker stop <name>`,
        # `./run_tests.sh sweep` (gate.f.8), or `./run_tests.sh down`
        # (gate.l.2). Default behavior (no flag, OR chain succeeded)
        # tears down as before.
        if (
            options.keep_on_failure
            and final_code != EXIT_SUCCESS
            and variant_handle is not None
        ):
            print(
                f"{terminal_prefix}runner: variant={spec.name} container "
                f"LEFT UP (--keep-on-failure + chain failed); clean up "
                f"later via `docker stop <name>` or `./run_tests.sh sweep`",
                file=sys.stderr,
            )
        else:
            teardown_variant(variant_handle)
            if variant_handle is not None:
                print(f"{terminal_prefix}runner: variant={spec.name} container torn down")

    return spec, layer_results, final_code


def _run_unit_prelude(prelude_dir: Path, options: RunOptions) -> LayerResult:
    """Y.2.gate.n — run the variant-independent ``unit`` layer once, as a
    prelude before the matrix fans out.

    ``unit`` (``pytest tests/unit tests/json …``; pyright via the conftest
    sessionstart gate) has no DB / scenario / dialect / target dependency —
    byte-identical result every cell. Running it per matrix cell was pure
    waste (a 13-cell default ran the ~165s suite up to 13×; ``--skip-cheap``
    mitigated cells 2..N but is opt-in). Now it runs once here; the per-cell
    chain starts at ``db``.

    ``--skip-cheap`` is honored via the ``_PRELUDE_VARIANT`` cache bucket
    (b.8's cache is variant-aware — this is the stable run-level key).
    Artifacts land under ``prelude_dir / "unit"`` (= ``runs/<id>/_prelude/unit/``)
    via the standard ``dispatch_layer`` capture.
    """
    if options.skip_cheap and is_layer_cached_green("unit", variant=_PRELUDE_VARIANT):
        print(
            "runner: layer-cached [unit] skipped (--skip-cheap, current SHA "
            "already green — prelude)"
        )
        return LayerResult(layer="unit", exit_code=0, duration_seconds=0.0, skipped=True)

    result = dispatch_layer("unit", prelude_dir, options)
    # #986, 2026-05-18 — When ``--only=<expr>`` is set, the same ``-k`` filter
    # is applied to the unit prelude AND every per-cell layer (CLAUDE.md:
    # "narrow every layer's pytest -k <expr>"). If the operator is targeting
    # a test that lives in a later layer (e.g. ``--only=account_filters``,
    # which matches only ``tests/e2e/test_l1_account_filters.py``), pytest
    # collects nothing in the unit roots, exits 5 ("no tests collected"),
    # and the chain halts before the matrix dispatches the real work. Treat
    # exit 5 on the prelude as "skipped" when ``--only`` is in effect — the
    # operator's intent is explicit and the matched layer's own pytest
    # invocation will still fail loud if the expr typos.
    only_filter_matched_nothing = (
        result.exit_code == 5 and options.only is not None
    )
    if only_filter_matched_nothing:
        result = LayerResult(
            layer="unit",
            exit_code=0,
            duration_seconds=result.duration_seconds,
            skipped=True,
        )
        print(
            f"runner: layer-skip [unit] rc=5 → 0 (prelude; --only="
            f"{options.only!r} matched no unit tests, deferring to later layers)"
        )
    else:
        marker = "skip" if result.skipped else ("ok" if result.passed else "FAIL")
        print(
            f"runner: layer-{marker} [unit] rc={result.exit_code} "
            f"duration={result.duration_seconds:.2f}s (prelude — runs once for all cells)"
        )
    if not result.skipped and result.passed:
        write_cache_marker("unit", duration_seconds=result.duration_seconds, variant=_PRELUDE_VARIANT)
    if not result.passed:
        print(
            "runner: stop-on-first-failure — chain halted at unit (prelude); "
            "matrix not dispatched",
            file=sys.stderr,
        )
    return result


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
    return code


def cmd_up_to(args: argparse.Namespace) -> int:
    """Run the test chain up to and including the named layer.

    Pre-flight: probes the named layer's required deps (c.8). On any failure,
    prints the operator-actionable message and exits NEEDS_OPERATOR — does NOT
    auto-invoke any interactive flow (b.14.4).

    Y.2.gate.b.10 — for layers >= deploy, refuses on tracked-changes dirty
    state unless `--allow-dirty-deploy` (or `RECON_GEN_RUNNER_YES=1`) is set.

    Then dispatches the chain (c.5): stop on first layer failure (b.9 LOCKED:
    cross-layer = sequential). Stubbed layers (deploy/api/browser pending cfg
    loading + variants) report skipped + pass-through so the chain doesn't
    falsely block.

    Y.2.gate.m.2 — variant matrix: ``--scenarios`` / ``--dialects`` /
    ``--targets`` compose a list of ``VariantSpec`` cells (no flags →
    ``compose_matrix`` returns the 13-cell ``full`` default).
    ``--variants=<sc>_<di>_<ta>`` is the triage escape (mutex with the
    sub-flag axes). Each cell runs concurrently via ``asyncio.gather``
    with its own nested run_dir (``runs/<id>/<spec.name>/``) and
    per-line terminal prefix (``[<spec.name>] ``). Soft fast-fail per
    cell: a failure in one cell doesn't kill its siblings — every
    cell runs to completion (or its own first failure). Top-level
    ``timings.json`` aggregates across cells with ``<spec.name>.<layer>``
    keys so ``report_drift`` works unchanged.

    Y.2.gate.n — the ``unit`` layer runs ONCE as a prelude (before the
    matrix fans out), not per cell — it's variant-independent. ``up_to=unit``
    runs the prelude and returns with no matrix at all; everything ``>= db``
    runs the prelude first, then fans the matrix out starting at ``db``.
    A prelude failure aborts before any cell dispatches (stop-on-first-failure).
    """
    options = _options_from_args(args)

    if _is_deploy_or_later(args.layer) and _is_dirty():
        if not options.allow_dirty_deploy and not RECON_GEN_RUNNER_YES.get_or_none():
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

    # Compose + validate the matrix BEFORE the (expensive) unit prelude so a
    # bad --scenarios/--dialects/--targets combo fails in <1s, not after the
    # ~165s unit suite. ``up_to=unit`` is variant-irrelevant — no matrix at
    # all (the prelude IS the whole run); skip composition entirely there.
    specs: list[VariantSpec] = []
    if args.layer != "unit":
        try:
            specs, skipped_specs = _compose_specs_from_options(options)
        except ValueError as exc:
            print(f"runner: {exc}", file=sys.stderr)
            return EXIT_NEEDS_OPERATOR
        # m.4.b — surface invalid-cell skips so operators see the filter
        # happen rather than silently dropped cells. The only invalid
        # combination today is sl × aw (sqlite is file-based; QuickSight
        # has no remote DataSource for it).
        for skipped in skipped_specs:
            reason = (
                "sl × aw: sqlite is file-based; QuickSight can't reach it remotely"
                if skipped.dialect == "sl" and skipped.target == "aw"
                else f"unhandled invalid combination ({skipped.dialect} × {skipped.target})"
            )
            print(f"runner: skip [{skipped.name}] ({reason})")
        if not specs:
            print("runner: variant matrix narrowed to zero cells (sub-flags filtered everything out)", file=sys.stderr)
            return EXIT_NEEDS_OPERATOR

    run_id = create_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"runner: run_id={run_id}")
    print(f"runner: run_dir={_rel_or_abs(run_dir)}")
    print(f"runner: up_to={args.layer}")
    if options.fuzz_seed_value is not None:
        print(f"runner: fuzz_seed={options.fuzz_seed_value} (pin via RECON_GEN_FUZZ_SEED env to repro)")

    # Y.2.gate.n — unit prelude: run the variant-independent layer once,
    # before any matrix fan-out. Artifacts → runs/<id>/_prelude/unit/.
    prelude_dir = run_dir / _PRELUDE_VARIANT
    prelude_dir.mkdir(parents=True, exist_ok=True)
    unit_result = _run_unit_prelude(prelude_dir, options)
    collect_run_outputs(prelude_dir, [unit_result])
    print(f"runner: wrote {_rel_or_abs(prelude_dir / 'timings.json')}")
    if not unit_result.passed:
        # Prelude failed → don't fan out the matrix at all.
        return _finalize_run(run_dir, unit_result, [], EXIT_FAILURE)
    if args.layer == "unit":
        # Unit-only invocation: the prelude IS the whole run.
        return _finalize_run(run_dir, unit_result, [], EXIT_SUCCESS)

    # The per-cell chain starts at ``db`` — ``unit`` is the prelude now.
    chain_full = chain_through(args.layer)
    chain = chain_full[1:]
    print(f"runner: chain={chain_full} (unit ran once as prelude; cells run {chain})")

    if len(specs) == 1:
        # Single-cell: stay synchronous; nested run_dir uses spec.name
        # so artifact paths are consistent across single + multi
        # invocations (m.2.d — every cell gets its own subdir).
        spec = specs[0]
        cell_dir = run_dir / spec.name
        cell_dir.mkdir(parents=True, exist_ok=True)
        _, layer_results, final_code = _run_one_variant(
            spec, cell_dir, options, chain,
        )
        collect_run_outputs(cell_dir, layer_results)
        print(f"runner: wrote {_rel_or_abs(cell_dir / 'timings.json')}")
        # Aggregate single cell to top-level (alongside the prelude's
        # `unit` entry, added by _finalize_run) so report_drift still
        # works on the canonical top-level timings.json.
        aggregated_single: list[LayerResult] = [
            LayerResult(
                layer=f"{spec.name}.{r.layer}",
                exit_code=r.exit_code,
                duration_seconds=r.duration_seconds,
                skipped=r.skipped,
            )
            for r in layer_results
        ]
        return _finalize_run(run_dir, unit_result, aggregated_single, final_code)
    else:
        # Multi-cell: fan out via asyncio.gather, each cell in its own
        # nested run_dir + with its own ``[<spec.name>] `` terminal prefix.
        # ``asyncio.to_thread`` bridges the sync ``_run_one_variant`` (which
        # blocks on subprocess + container I/O) into the event loop without
        # forcing the whole chain to be async. Per design lock: no
        # concurrency cap (default to len(specs) — Docker is the
        # bottleneck, not the runner).
        spec_names = [s.name for s in specs]
        print(f"runner: variants={spec_names} (parallel fan-out)")
        # Pre-create per-cell dirs so the post-gather
        # ``collect_run_outputs`` always has a target to write to,
        # even if a cell was a complete no-op (e.g., probes failed
        # inside the cell or all layers were skipped via cache).
        for s in specs:
            (run_dir / s.name).mkdir(parents=True, exist_ok=True)
        # Pre-warm the testcontainers Ryuk reaper singleton serially so
        # parallel ``setup_variant`` calls don't race on
        # ``Reaper._create_instance`` — otherwise both threads try to
        # create a container with the same fixed Ryuk name and the
        # second one crashes with HTTP 409 from Docker. Lazy-imported
        # so the runner stays Docker-free for AWS-only invocations.
        if any(s.target == "lo" and s.dialect != "sl" for s in specs):
            try:
                from testcontainers.core.container import Reaper  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs
                Reaper.get_instance()
            except Exception as exc:  # noqa: BLE001
                # Reaper init failure is non-fatal — the per-cell
                # ``setup_variant`` will surface the real error with
                # operator-actionable context. Log here so the
                # operator sees the pre-warm attempt.
                print(f"runner: reaper pre-warm skipped ({exc!r}); continuing")

        async def _gather() -> list[tuple[VariantSpec, list[LayerResult], int]]:
            tasks = [
                asyncio.to_thread(
                    _run_one_variant,
                    s, run_dir / s.name, options, chain,
                    terminal_prefix=f"[{s.name}] ",
                )
                for s in specs
            ]
            # m.5.c.fix — `return_exceptions=True` so a setup_variant /
            # teardown_variant raise in one cell doesn't cancel sibling
            # cells (m.4 "soft fast-fail per cell" promise). Caller
            # converts exceptions to a failed `LayerResult` entry below.
            raw = await asyncio.gather(*tasks, return_exceptions=True)
            results: list[tuple[VariantSpec, list[LayerResult], int]] = []
            for spec, item in zip(specs, raw, strict=True):
                if isinstance(item, BaseException):
                    print(
                        f"[{spec.name}] runner: cell crashed before any layer ran "
                        f"({type(item).__name__}: {item})",
                        file=sys.stderr,
                    )
                    crash = LayerResult(
                        layer="setup",
                        exit_code=EXIT_FAILURE,
                        duration_seconds=0.0,
                    )
                    results.append((spec, [crash], EXIT_FAILURE))
                else:
                    results.append(item)
            return results

        per_variant_results = asyncio.run(_gather())

        # Per-cell timings.json under each cell subdir (keeps the
        # per-cell run-dir self-contained — useful for CI artifact
        # uploads + post-mortem of one cell).
        for cell_spec, layer_results, _ in per_variant_results:
            variant_dir = run_dir / cell_spec.name
            collect_run_outputs(variant_dir, layer_results)
            print(f"runner: wrote {_rel_or_abs(variant_dir / 'timings.json')}")

        # Aggregated top-level timings.json with ``<spec.name>.<layer>``
        # keyed durations (plus the prelude's run-level `unit` entry,
        # added by _finalize_run). ``report_drift`` reads this against the
        # prior run's top-level — so when both prior + current ran the same
        # cells, drift fires per-cell per-layer with no special casing in
        # ``compute_drift``.
        aggregated_results: list[LayerResult] = []
        for cell_spec, layer_results, _ in per_variant_results:
            for r in layer_results:
                aggregated_results.append(LayerResult(
                    layer=f"{cell_spec.name}.{r.layer}",
                    exit_code=r.exit_code,
                    duration_seconds=r.duration_seconds,
                    skipped=r.skipped,
                ))

        # Final code: any non-zero cell fails the run. EXIT_FAILURE
        # wins over EXIT_NEEDS_OPERATOR (real failures hide config gaps
        # — the operator should fix the failure first).
        codes = [code for _, _, code in per_variant_results if code != EXIT_SUCCESS]
        if EXIT_FAILURE in codes:
            final_code = EXIT_FAILURE
        elif codes:
            final_code = codes[0]
        else:
            final_code = EXIT_SUCCESS

        return _finalize_run(run_dir, unit_result, aggregated_results, final_code)


def _compose_specs_from_options(
    options: RunOptions,
) -> tuple[list[VariantSpec], list[VariantSpec]]:
    """m.2.e + m.4.b — translate RunOptions into ``(valid, skipped)``
    spec lists. The runner logs the skipped invalid cells so operators
    see the filter happen (per m.4.b).

    Two modes:

    - ``--variants`` (triage escape): each comma-separated entry is a
      ``<sc>_<di>_<ta>`` cell code parsed via ``parse_variant_code``.
      Mutex with the sub-flag axes — caller errors out below if both
      are set. Legacy ``local-pg`` / etc. names raise with a hint
      (``_check_legacy_variant_names``). Triage codes are presumed
      valid (the operator typed them explicitly); skipped list is empty.
    - Sub-flag composition: ``--scenarios`` / ``--dialects`` /
      ``--targets`` feed ``partition_matrix``. All None → ``expand_full``
      (the curated 13-cell default; skipped list is empty since
      `expand_full` constructs only valid cells by design).
    """
    if options.variants is not None:
        if any(x is not None for x in (options.scenarios, options.dialects, options.targets)):
            raise ValueError(
                "--variants is the triage escape and is mutex with "
                "--scenarios / --dialects / --targets. Pick one shape."
            )
        _check_legacy_variant_names(options.variants)
        codes = [c.strip() for c in options.variants.split(",") if c.strip()]
        if not codes:
            raise ValueError("--variants value is empty")
        return [parse_variant_code(c) for c in codes], []

    sc_specs = parse_scenarios(options.scenarios) if options.scenarios else None
    di_codes = parse_dialects(options.dialects) if options.dialects else None
    ta_codes = parse_targets(options.targets) if options.targets else None
    return partition_matrix(sc_specs, di_codes, ta_codes)


# Y.2.gate.l.2 — RDS lifecycle commands. Helpers below the cmd_*
# triple. They depend on the cfg loader (lazy import keeps cmd_pyright
# / cmd_up_to fast paths free of cfg parse cost when not needed).


def _load_runner_cfg_for_lifecycle() -> Config | None:
    """Find + load the operator's cfg for the lifecycle commands. Same
    discovery shape as ``_probe_aws_creds`` — RECON_GEN_CONFIG override
    first, then ``run/config.yaml`` / ``run/config.postgres.yaml`` /
    ``run/config.oracle.yaml``. Returns None when none found OR when
    the loaded cfg fails validation; caller surfaces operator-actionable
    guidance.

    Y.2.gate.l.2 — when cfg carries ``auth.aws_profile``, also injects
    ``AWS_PROFILE`` into ``os.environ`` so the boto3 RDS client picks
    up the operator's long-lived IAM keys (matches the per-variant
    subprocess auth pattern from gate.h.1; lifecycle commands run in
    the parent process so they need the env set here directly).
    """
    cfg_path = _resolve_seed_config(_DEFAULT_RUNNER_CFG_CANDIDATES)
    if cfg_path is None:
        return None
    try:
        from recon_gen.common.config import load_config  # noqa: PLC0415 — lazy
        cfg = load_config(str(cfg_path))
    except Exception as exc:  # noqa: BLE001 — operator-facing failure surface, not silent
        print(
            f"runner: failed to load cfg from {cfg_path}: {exc}",
            file=sys.stderr,
        )
        return None
    if cfg.auth is not None and cfg.auth.aws_profile is not None:
        os.environ["AWS_PROFILE"] = cfg.auth.aws_profile
    return cfg


def _resolve_rds_resources(cfg: Config) -> tuple[Any, Any]:
    """Build per-resource RdsResource objects from cfg. Returns
    ``(pg_resource | None, oracle_resource | None)`` — None when the
    matching cfg field is unset (operator hasn't configured that
    resource yet). Lazy import of aws_rds so the cmd_pyright fast path
    stays import-cheap.
    """
    from recon_gen.common.aws_rds import RdsResource  # noqa: PLC0415 — lazy: keep cmd_pyright fast path light

    pg = (
        RdsResource(kind="cluster", identifier=cfg.aws_pg_cluster_id,
                    aws_region=cfg.aws_region)
        if cfg.aws_pg_cluster_id is not None
        else None
    )
    oracle = (
        RdsResource(kind="instance", identifier=cfg.aws_oracle_instance_id,
                    aws_region=cfg.aws_region)
        if cfg.aws_oracle_instance_id is not None
        else None
    )
    return pg, oracle


def _poll_until(
    resource: Any,
    target_status: str,
    *,
    timeout_s: int = 900,
    interval_s: int = 10,
) -> str:
    """Poll ``aws_rds.get_status`` until the status matches ``target_status``
    or ``timeout_s`` elapses. Returns the final observed status. Logs
    each poll to stdout so the operator sees progress.

    Aurora cold-start is ~5-7 minutes; Oracle ~3-5 minutes. The 900s
    (15min) cap leaves headroom for first-boot. Caller decides whether
    a non-target final status is a failure or just a "still in flight".
    """
    from recon_gen.common.aws_rds import get_status  # noqa: PLC0415 — lazy
    deadline = time.monotonic() + timeout_s
    last_status: str = ""
    while time.monotonic() < deadline:
        status = get_status(resource)
        if status != last_status:
            print(f"runner: {resource.identifier} → {status}")
            last_status = status
        if status == target_status:
            return status
        time.sleep(interval_s)
    return last_status or "timeout"


def cmd_up(args: argparse.Namespace) -> int:
    """Boot dependencies. scope = local | aws | all (default).

    - **local**: no-op. Local PG / Oracle / SQLite spin on-demand
      inside ``setup_variant`` per matrix cell — there's no shared
      "local cluster" to start. Reported for symmetry with ``down``.
    - **aws**: start the cfg-declared Aurora cluster + Oracle instance
      (`cfg.aws_pg_cluster_id` / `cfg.aws_oracle_instance_id`). Polls
      each until status hits ``available``. Idempotent — already-running
      resources return immediately. Loud-fails when the cfg fields are
      unset with a pointer to the gate.l provisioning runbook.
    - **all** (default): both. Local first (fast no-op), then AWS.
    """
    scope = args.scope
    if scope == "local":
        return _cmd_up_local()
    if scope == "aws":
        return _cmd_up_aws()
    if scope == "all":
        rc_local = _cmd_up_local()
        rc_aws = _cmd_up_aws()
        return rc_local or rc_aws
    print(f"runner: unknown up scope {scope!r}", file=sys.stderr)
    return EXIT_NEEDS_OPERATOR


def _cmd_up_local() -> int:
    """Local containers are demand-spawned by setup_variant; nothing to
    pre-boot. Reported for symmetry — operator can `up local` and the
    next `up_to=db --targets=lo` invocation will just work."""
    print(
        "runner: up local — no-op "
        "(local containers spin on-demand per matrix cell)"
    )
    return EXIT_SUCCESS


def _cmd_up_aws() -> int:
    """Start cfg-declared RDS resources + poll until available."""
    cfg = _load_runner_cfg_for_lifecycle()
    if cfg is None:
        print(
            "runner: up aws — no cfg discoverable. Set RECON_GEN_CONFIG or "
            "place run/config.{postgres,oracle}.yaml.",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    pg, oracle = _resolve_rds_resources(cfg)
    if pg is None and oracle is None:
        print(
            "runner: up aws — neither cfg.aws_pg_cluster_id nor "
            "cfg.aws_oracle_instance_id set. Add them to your cfg "
            "(see docs/audits/y_2_gate_l_ci_aws_provisioning.md) or "
            "set RECON_GEN_AWS_PG_CLUSTER_ID / RECON_GEN_AWS_ORACLE_INSTANCE_ID.",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    from recon_gen.common.aws_rds import start  # noqa: PLC0415 — lazy
    final_rc = EXIT_SUCCESS
    for resource in (pg, oracle):
        if resource is None:
            continue
        try:
            print(f"runner: starting {resource.kind} {resource.identifier}…")
            initial = start(resource)
            if initial == "available":
                print(
                    f"runner: {resource.identifier} already available — no wait"
                )
                continue
            final = _poll_until(resource, "available")
            if final != "available":
                print(
                    f"runner: {resource.identifier} did not reach "
                    f"'available' (final={final!r}) — check AWS console",
                    file=sys.stderr,
                )
                final_rc = EXIT_FAILURE
        except Exception as exc:  # noqa: BLE001 — surface AWS errors to operator
            print(f"runner: start {resource.identifier} failed: {exc}", file=sys.stderr)
            final_rc = EXIT_NEEDS_OPERATOR
    return final_rc


def cmd_down(args: argparse.Namespace) -> int:
    """Tear down dependencies. scope = local | aws | all (default).

    Destructive — requires --yes (Y.2.gate.b.14.3 destructive-op
    opt-in). For ``local``, stops the named persistent Oracle
    containers (PG containers are ephemeral, no action needed). For
    ``aws``, calls ``stop_db_cluster`` / ``stop_db_instance``;
    idempotent + non-blocking (stop takes minutes; runner returns
    after the stop request is accepted, doesn't poll).
    """
    if not args.yes and not RECON_GEN_RUNNER_YES.get_or_none():
        print(
            "runner: 'down' is destructive — pass --yes "
            "(or set RECON_GEN_RUNNER_YES=1)",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    scope = args.scope
    if scope == "local":
        return _cmd_down_local()
    if scope == "aws":
        return _cmd_down_aws()
    if scope == "all":
        rc_local = _cmd_down_local()
        rc_aws = _cmd_down_aws()
        return rc_local or rc_aws
    print(f"runner: unknown down scope {scope!r}", file=sys.stderr)
    return EXIT_NEEDS_OPERATOR


def _cmd_down_local() -> int:
    """Stop persistent local containers (Oracle reuse pattern from j.5).
    PG containers are ephemeral — testcontainers tears them down per
    test session — so no action there.
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


def _cmd_down_aws() -> int:
    """Stop cfg-declared RDS resources. Stop is asynchronous on the
    RDS side; runner doesn't poll for ``stopped`` (would add ~5min).
    Operator can ``./run_tests.sh status`` to confirm.
    """
    cfg = _load_runner_cfg_for_lifecycle()
    if cfg is None:
        print(
            "runner: down aws — no cfg discoverable. Set RECON_GEN_CONFIG or "
            "place run/config.{postgres,oracle}.yaml.",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    pg, oracle = _resolve_rds_resources(cfg)
    if pg is None and oracle is None:
        print(
            "runner: down aws — neither cfg.aws_pg_cluster_id nor "
            "cfg.aws_oracle_instance_id set. Nothing to stop.",
            file=sys.stderr,
        )
        return EXIT_NEEDS_OPERATOR
    from recon_gen.common.aws_rds import stop  # noqa: PLC0415 — lazy
    final_rc = EXIT_SUCCESS
    for resource in (pg, oracle):
        if resource is None:
            continue
        try:
            print(f"runner: stopping {resource.kind} {resource.identifier}…")
            status = stop(resource)
            print(f"runner: {resource.identifier} → {status}")
        except Exception as exc:  # noqa: BLE001 — surface AWS errors
            print(f"runner: stop {resource.identifier} failed: {exc}", file=sys.stderr)
            final_rc = EXIT_NEEDS_OPERATOR
    return final_rc


# Y.2.gate.l.2 — rough hourly cost estimates for `status --cost`. We
# don't query AWS pricing API; values are rounded approximations from
# us-east-1 list prices for typical demo instance sizes (db.r5.large
# Aurora, db.t3.small Oracle SE2). Marked "rough" in output so operator
# isn't misled into treating these as billing-grade.
_ROUGH_HOURLY_COSTS: Final[dict[str, float]] = {
    "aurora-cluster-running": 0.30,    # db.r5.large compute
    "aurora-cluster-stopped": 0.05,    # storage only (varies)
    "oracle-instance-running": 0.10,   # db.t3.small SE2
    "oracle-instance-stopped": 0.02,   # storage only
}


def cmd_status(args: argparse.Namespace) -> int:
    """Show what's currently running. --cost adds rough hourly
    estimates so the operator's cost surface stays visible.

    Two sections:

    - **local**: docker containers matching ``ORACLE_REUSE_CONTAINER_PREFIX``
      (the j.5 named-Oracle reuse set). Ephemeral PG containers don't
      show up here — they live ~test-session and `docker ps` may catch
      them in flight, but the runner doesn't manage them.
    - **aws**: cfg-declared RDS resources via ``aws_rds.get_status``.
      Loud-fails when neither cfg field is set.
    """
    print("runner: status — local containers")
    _status_local()
    print()
    print("runner: status — AWS RDS resources")
    rc = _status_aws(show_cost=bool(args.cost))
    return rc


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


def _status_aws(*, show_cost: bool) -> int:
    cfg = _load_runner_cfg_for_lifecycle()
    if cfg is None:
        print("  no cfg discoverable; skip AWS status")
        return EXIT_NEEDS_OPERATOR
    pg, oracle = _resolve_rds_resources(cfg)
    if pg is None and oracle is None:
        print(
            "  cfg has no aws_pg_cluster_id or aws_oracle_instance_id; "
            "nothing to query"
        )
        return EXIT_SUCCESS
    from recon_gen.common.aws_rds import get_status  # noqa: PLC0415 — lazy
    total_hourly = 0.0
    for resource in (pg, oracle):
        if resource is None:
            continue
        try:
            status = get_status(resource)
        except Exception as exc:  # noqa: BLE001 — operator-facing
            print(f"  {resource.identifier}: ERROR — {exc}")
            continue
        line = f"  {resource.kind} {resource.identifier}: {status}"
        if show_cost:
            # Only the literal `stopped` state gets storage-only billing;
            # everything else (available, starting, upgrading, backing-up,
            # …) bills compute. The runner's pricing is rough by
            # definition (no Pricing API call) but conflating
            # transitional states with stopped underreports cost during
            # multi-hour boots — meaningful when Oracle takes 30+ min.
            running = status != "stopped"
            cost_key = (
                f"aurora-cluster-{'running' if running else 'stopped'}"
                if resource.kind == "cluster"
                else f"oracle-instance-{'running' if running else 'stopped'}"
            )
            cost = _ROUGH_HOURLY_COSTS.get(cost_key, 0.0)
            total_hourly += cost
            line += f"  (~${cost:.2f}/hr)"
        print(line)
    if show_cost:
        print(f"  rough total: ~${total_hourly:.2f}/hr (estimates only)")
    return EXIT_SUCCESS


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

    _render_failures_for_run(run_dir, variant_filter=args.variant)
    return EXIT_SUCCESS


def _render_failures_for_run(
    run_dir: Path, *, variant_filter: str | None = None,
) -> bool:
    """Render the failing-layers report for ``run_dir`` to stdout.

    Returns ``True`` if at least one failing layer was found, else ``False``.

    Shared by ``cmd_dump_last_errors`` (operator-invoked triage) and
    ``_finalize_run`` (auto-emit on chain failure, 2026-05-18 per user
    request — saves the operator from remembering the subcommand).
    """
    print(f"# Failing layers in {run_dir.name}")
    print()

    # Walk cells (variant subdirs). Skip the unit-prelude dir specially
    # — it's `_prelude/unit/`, not a variant.
    cells: list[tuple[str, Path]] = []
    prelude = run_dir / "_prelude"
    if prelude.is_dir():
        cells.append(("_prelude", prelude))
    for sub in sorted(run_dir.iterdir()):
        if not sub.is_dir() or sub.name == "_prelude":
            continue
        if variant_filter and sub.name != variant_filter:
            continue
        cells.append((sub.name, sub))

    found_any_failure = False
    for cell_name, cell_dir in cells:
        for layer_dir in sorted(cell_dir.iterdir()):
            if not layer_dir.is_dir():
                continue
            layer = layer_dir.name
            if layer in ("timings", "db-perf", "l2", "seed"):
                # Auxiliary subdirs, not chain layers.
                continue
            cmd_json_path = layer_dir / "cmd.json"
            if not cmd_json_path.is_file():
                continue
            cmd_json = json.loads(cmd_json_path.read_text())
            exit_code = int(cmd_json.get("exit_code", 0) or 0)
            if exit_code == 0:
                continue
            found_any_failure = True
            duration = cmd_json.get("duration_seconds")
            duration_str = f"{duration:.1f}s" if duration else "?"
            print(f"## [{cell_name}/{layer}] exit={exit_code} duration={duration_str}")
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
    """For a failing browser layer, check whether AA.H.6 capture
    artifacts landed for each failed test. Print a warning if any
    failed test has no matching capture dir — that's an AA.H.10
    regression worth investigating."""
    if layer_dir.name != "browser":
        return
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


def cmd_sweep(args: argparse.Namespace) -> int:
    """Y.2.gate.c.9 — clean orphan QuickSight resources tagged
    ``Harness:e2e``.

    Replaces ``scripts/sweep_harness_orphans.py`` (deletion of the
    standalone script is `Y.2.gate.f.8`). Same default: dry-run
    (collect + print). Pass ``--yes`` (or set ``RECON_GEN_RUNNER_YES=1``,
    matching the destructive-op convention from `b.14.3`) to actually
    delete.

    Tag set: ``Harness:e2e`` — production deploys don't carry that
    tag (they wear ``ManagedBy:recon-gen`` + optional
    ``L2Instance:<prefix>``), so this is safe against the production
    resource graph.

    Exit codes:
      0 — clean (dry-run completed OR delete completed)
      2 — needs operator (AWS creds expired / config not found)
    """
    from recon_gen.common.config import load_config

    # Sweep only needs aws_account_id + aws_region — any cfg has those.
    # Lookup mirrors tests/e2e/conftest.py::cfg with the per-dialect
    # files added (Phase P split run/config.yaml → per-dialect).
    config_path: Path | None = None
    # Soft-fallback: registry's must_be_file validator would raise on
    # a non-existent pin; sweep is best-effort + cfg discovery has
    # other candidates, so soak the absence rather than fail-loud.
    explicit = os.environ.get(RECON_GEN_CONFIG.name)
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
            "(checked RECON_GEN_CONFIG, run/config.yaml, config.yaml, "
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

    # Y.2.gate.f.9 — sweep helpers lifted from
    # tests/e2e/_harness_cleanup.py to recon_gen/_dev/cleanup.py.
    # Direct import; no sys.path / importlib gymnastics.
    from recon_gen._dev.cleanup import (
        _collect_resources_matching_tag,
        sweep_qs_resources_by_tag,
    )

    # boto3-stubs's huge per-service overload union confuses pyright
    # — Unknown branches leak through on most-cases. Suppress narrowly.
    client: Any = boto3.client(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]: boto3-stubs huge overload union confuses pyright (X.2.o.5)
        "quicksight", region_name=cfg.aws_region,
    )

    confirm = bool(args.yes) or bool(RECON_GEN_RUNNER_YES.get_or_none())
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
        # f.9 — direct import means pyright knows the return type;
        # no cast needed.
        matched = raw_matched
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
    counts = raw_counts
    print(f"runner: sweep deleted: {counts} (total={sum(counts.values())})")
    return EXIT_SUCCESS


_HELP_EPILOG = """\
Auth (Y.2.gate.h+i):
  AWS profile + QS embed user are read from run/config.<dialect>.yaml's
  optional auth: block. Set:
      auth:
        aws_profile: "recon-gen-local"   # ~/.aws/credentials profile
        quicksight_user_arn: null             # optional explicit override
  When set, the runner injects AWS_PROFILE into every layer subprocess and
  auto-derives RECON_E2E_USER_ARN via STS+ListUsers — no env-var exports.
  One-time IAM-user setup runbook + IAM policy json:
      docs/audits/y_2_gate_h_i_combined_spike.md   §6 (runbook), §7 (policy)
      docs/audits/_iam/recon-gen-local-policy.json

Layer chain (Y.2.gate.b/c/n):
  unit -> db -> app2 -> deploy -> api -> browser
  ./run_tests.sh up_to=<layer>  runs the chain through that layer.
  `unit` is variant-independent, so it runs ONCE per invocation as a
  prelude (artifacts → runs/<id>/_prelude/unit/) — not once per matrix
  cell. `up_to=unit` runs just the prelude; `>= db` runs the prelude
  first, then fans the matrix out starting at `db`. A prelude failure
  aborts before any cell dispatches.

Variant matrix (Y.2.gate.m):
  No flags = full 13-cell matrix (sp/sq named scenarios × pg/or/sl × lo/aw,
  plus 3 fuzz cells × pg/or/sl × lo). Narrow via sub-flags or pin via --variants.
  Invalid cells (sl × aw — sqlite isn't reachable from QS) auto-skip with a log.

  Examples (all assume `up_to=db` or higher):
    --scenarios=sp,sq                       sp + sq named-scenario subset
    --scenarios=fuzz                        1 random fuzz seed (per-dialect cell)
    --scenarios=fuzz:5                      5 random fuzz seeds (× dialect axis)
    --scenarios=us:run/customer.yaml        operator-supplied L2 yaml
    --dialects=pg                           postgres only
    --dialects=pg,or                        cross-dialect (no sqlite)
    --targets=lo                            local containers / sqlite tempfile
    --targets=aw                            operator's external Aurora / Oracle
    --variants=sp_pg_lo                     triage: pin a single cell
    --variants=f12345_pg_lo                 reproduce a fuzz failure by seed
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
        help="within-variant pytest-xdist worker count. Default = `-n auto` (= cpu_count); pin via `--parallel=N` to override.",
    )
    # m.2.a — 3-axis matrix sub-flags. All None → compose_matrix returns full
    # 13-cell default. Any specified → cross-product narrowing (variant.compose_matrix).
    p_up_to.add_argument(
        "--scenarios",
        metavar="<csv>",
        default=None,
        help="scenarios axis CSV (sp / sq / fuzz / fuzz:N / us:<path>); default = sp,sq.",
    )
    p_up_to.add_argument(
        "--dialects",
        metavar="<csv>",
        default=None,
        help="dialects axis CSV (pg / or / sl); default = pg,or,sl.",
    )
    p_up_to.add_argument(
        "--targets",
        metavar="<csv>",
        default=None,
        help="targets axis CSV (lo / aw); default = lo,aw. sl × aw auto-skips.",
    )
    # m.2.a — --variants is the triage escape: each entry is a single
    # ``<sc>_<di>_<ta>`` cell code (e.g., sp_pg_lo, f42_or_lo). Mutex with
    # --scenarios/--dialects/--targets. The legacy local-pg/local-oracle/etc.
    # names error with a hint pointing at the new sub-flag form.
    p_up_to.add_argument(
        "--variants",
        metavar="<csv>",
        default=None,
        help="triage escape: comma-separated <sc>_<di>_<ta> cell codes (mutex with sub-flag axes).",
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
        help="leave ephemeral state up when the chain fails so the operator can poke at it interactively. Default tears down. Clean up later via `docker stop <name>` or `./run_tests.sh sweep` (f.8).",
    )
    p_up_to.add_argument(
        "--trace-all",
        action="store_true",
        help="Playwright capture every test (failure-only is the default). Threads RECON_GEN_TRACE_ALL=1 to subprocesses (consumed by c.11).",
    )
    p_up_to.add_argument(
        "--allow-dirty-deploy",
        action="store_true",
        help="bypass the tracked-changes refusal on layers >= deploy (b.10).",
    )
    p_up_to.add_argument(
        "--coverage",
        action="store_true",
        help="emit per-(variant, layer) .coverage.<variant>.<layer> data files under runs/<id>/ (pytest layers run with --cov=recon_gen). CI's W.8b coverage aggregator globs coverage-data-* artifacts. Off by default (k.1.coverage).",
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

    p_sweep = subs.add_parser("sweep", help="Clean orphan resources tagged ManagedBy:recon-gen")
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
    p_dump.add_argument(
        "--variant",
        default=None,
        metavar="NAME",
        help=(
            "narrow to a single variant cell (e.g. sp_pg_aw); "
            "default = all cells in the run."
        ),
    )
    p_dump.set_defaults(func=cmd_dump_last_errors)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(_normalize_argv(raw))
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
