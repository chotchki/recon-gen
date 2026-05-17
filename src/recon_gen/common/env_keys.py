"""Y.2.gate.b.15 — Typed registry for every RECON_GEN_* / RECON_E2E_* env var.

AC.B.3 — env vars renamed from ``QS_GEN_*`` / ``QS_E2E_*`` to
``RECON_GEN_*`` / ``RECON_E2E_*`` alongside the package rename.
Each spec carries a ``legacy_name`` pointing at the old name so
operators with ``export QS_GEN_*=...`` in their shell rc continue
to work for a 1-2 month grace window (mirrors the PyPI shim
window). Reads emit a one-shot ``DeprecationWarning`` per legacy
env-var name. The legacy fallback gets dropped in the same release
that drops the ``quicksight-gen`` PyPI shim.

Why this exists
---------------

Bare ``os.environ.get("RECON_GEN_RUN_DIR")`` strings spread across runner
+ config + conftest + harness + helpers as the test layer chain
runner grew. Three independent failure modes:

1. **Typos.** ``os.environ.get("RECON_GEN_DEMO_DATABASE_URLL")`` silently
   falls through to "env var unset" — no TypeError, no NameError, no
   probe failure. The bug surfaces as "DB connection used the cfg
   default instead of the variant URL" 30 seconds later in a totally
   different stack frame.

2. **Required-vs-optional context.** ``RECON_GEN_RUN_DIR`` is optional
   (sidecar capture); ``RECON_E2E_USER_ARN`` is required (browser e2e
   embed-URL signing); ``RECON_GEN_DEMO_DATABASE_URL`` is context-
   required (set by the runner for non-default variants). Every
   call site re-implements its own None-handling.

3. **Value validation.** When ``RECON_GEN_CONFIG=/typo/path`` is set,
   the failure shows up as ``FileNotFoundError`` 5 frames deep
   inside ``load_config`` — the operator has to trace it back to the
   env var. Better: catch at the boundary with the env var name +
   description in the error message.

(The historical context above used ``QS_GEN_*`` examples — flipped to
the new ``RECON_GEN_*`` form during AC.B.3 to match the live registry.)

Locked design (Y.2.gate.b.15.spec): typed ``EnvVar[T]`` dataclass
per env var carrying ``name + description + coercer + optional +
validator``. Three operations:

- ``.get_or_none() -> T | None`` — sidecar shape; absent or empty
  → None. Validator runs on the coerced value when present.
- ``.require() -> T`` — required-context shape; raises
  ``EnvVarRequired`` with the spec's ``description`` as the
  operator-actionable hint when absent.
- ``.serialize(value: T) -> str`` — set-in-subprocess-env shape;
  validator runs first (catches set-side bugs like "runner forgot
  to mkdir before setting RUN_DIR").

Validation runs in BOTH directions so set-side bugs and get-side
bugs both surface at the boundary with ``EnvVarInvalid`` carrying
name + description.

The ``b.15.lint`` follow-up adds an AST lint that catches any code
attempting to bypass this registry (bare ``os.environ.get`` with a
``QS_*`` literal). Until then, the convention is enforced by
review.
"""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final


# AC.B.3 — one-shot dedupe so the deprecation warning fires once per
# legacy name per process instead of spamming on every get_or_none().
_legacy_warned: set[str] = set()


class EnvVarError(Exception):
    """Base for env-var registry errors. Carries the spec name +
    description so the operator-facing message is always
    actionable."""

    def __init__(self, spec_name: str, description: str, detail: str) -> None:
        super().__init__(
            f"{spec_name}: {detail}\n  description: {description}"
        )
        self.spec_name = spec_name
        self.description = description
        self.detail = detail


class EnvVarRequired(EnvVarError):
    """Raised by ``EnvVar.require()`` when the env var is unset
    (or empty)."""


class EnvVarInvalid(EnvVarError):
    """Raised by ``EnvVar.get_or_none()`` / ``.require()`` /
    ``.serialize()`` when the value fails the spec's validator
    (e.g., path doesn't exist, int is non-positive)."""


# ---------------------------------------------------------------------------
# Reusable validators


def must_exist(p: Path) -> None:
    """Path must exist (file or directory)."""
    if not p.exists():
        raise ValueError(f"path does not exist: {p}")


def must_be_file(p: Path) -> None:
    """Path must exist AND be a regular file."""
    if not p.is_file():
        raise ValueError(f"not a file (or doesn't exist): {p}")


def must_be_dir(p: Path) -> None:
    """Path must exist AND be a directory."""
    if not p.is_dir():
        raise ValueError(f"not a directory (or doesn't exist): {p}")


def positive_int(n: int) -> None:
    """Integer must be > 0."""
    if n <= 0:
        raise ValueError(f"must be positive, got {n}")


def matches(pattern: re.Pattern[str]) -> Callable[[str], None]:
    """Return a validator that checks the value matches ``pattern``."""

    def _check(value: str) -> None:
        if not pattern.fullmatch(value):
            raise ValueError(
                f"value does not match {pattern.pattern!r}: {value!r}"
            )

    return _check


# ---------------------------------------------------------------------------
# Coercers (string → typed value)


def _bool_coercer(s: str) -> bool:
    """Mirrors the existing ``bool(os.environ.get(...))`` pattern:
    any non-empty string is True, empty string is False. Operator
    does NOT write ``"false"`` and expect it to disable — that
    convention has never been part of this codebase."""
    return bool(s)


# ---------------------------------------------------------------------------
# EnvVar dataclass


@dataclass(frozen=True)
class EnvVar[T]:
    """Spec for one env var. Frozen so call sites can declare these
    at module top-level without mutation worry.

    Fields:
        name: the env-var name (the string passed to os.environ).
        description: operator-facing description; surfaces in the
            error message when ``require()`` finds the var unset
            or ``get_or_none()`` finds the value invalid.
        coercer: ``str → T``. Examples: ``Path``, ``int``,
            ``_bool_coercer``, ``str`` (identity).
        optional: True iff the var is allowed to be absent. Affects
            documentation only; ``get_or_none()`` always tolerates
            absence and ``require()`` always raises on absence.
        validator: optional ``T → None`` callable. Raises
            ``ValueError`` (re-wrapped as ``EnvVarInvalid`` by the
            EnvVar machinery) when the coerced value is unacceptable
            (path doesn't exist, int non-positive, etc.). Runs in
            both directions: get-side AND serialize-side.
    """

    name: str
    description: str
    coercer: Callable[[str], T]
    optional: bool = True
    validator: Callable[[T], None] | None = field(default=None)
    legacy_name: str | None = field(default=None)
    """AC.B.3 — deprecated legacy env-var name to fall back to when
    the canonical ``name`` is unset. Reads emit a one-shot
    ``DeprecationWarning`` per legacy name (deduped via
    ``_legacy_warned``). Set during AC.B.3 for the ``QS_GEN_*`` /
    ``QS_E2E_*`` → ``RECON_*`` rename; the fallback is dropped
    alongside the PyPI shim drop."""

    def _read_raw(self) -> str | None:
        """AC.B.3 — read the env var with canonical-first +
        legacy-fallback semantics. Returns the raw string when
        set (canonical wins), the legacy raw string when the
        canonical is unset, or None when both are unset. Emits a
        one-shot ``DeprecationWarning`` per legacy name when the
        legacy fallback fires."""
        raw = os.environ.get(self.name)
        if raw is not None and raw != "":
            return raw
        if self.legacy_name is None:
            return raw
        legacy_raw = os.environ.get(self.legacy_name)
        if legacy_raw is None or legacy_raw == "":
            return None
        if self.legacy_name not in _legacy_warned:
            _legacy_warned.add(self.legacy_name)
            warnings.warn(
                f"env var {self.legacy_name!r} is deprecated — "
                f"use {self.name!r} instead. The legacy fallback "
                f"will be dropped alongside the PyPI shim drop "
                f"(see AC.F.3). Description: {self.description}",
                DeprecationWarning,
                stacklevel=4,
            )
        return legacy_raw

    def get_or_none(self) -> T | None:
        """Read the env var. Returns None when unset or empty.
        Coerces + validates when present; ``EnvVarInvalid`` on
        validator failure.
        """
        raw = self._read_raw()
        if raw is None or raw == "":
            return None
        return self._coerce_and_validate(raw)

    def require(self) -> T:
        """Read the env var. Raises ``EnvVarRequired`` when unset
        or empty (with the spec's ``description`` in the error).
        Coerces + validates when present.
        """
        raw = self._read_raw()
        if raw is None or raw == "":
            raise EnvVarRequired(
                self.name,
                self.description,
                "env var is required but is unset (or empty)",
            )
        return self._coerce_and_validate(raw)

    def serialize(self, value: T) -> str:
        """Convert ``value`` to a string for placement in a
        subprocess env dict. Validates before serializing so
        set-side bugs ("runner forgot to mkdir before setting
        RUN_DIR") surface at the same boundary as get-side bugs.
        """
        if self.validator is not None:
            try:
                self.validator(value)
            except ValueError as exc:
                raise EnvVarInvalid(
                    self.name, self.description, str(exc),
                ) from exc
        return str(value)

    def _coerce_and_validate(self, raw: str) -> T:
        """Internal: apply coercer + validator. Wraps both layers'
        ``ValueError`` into ``EnvVarInvalid`` carrying name +
        description so the call site doesn't have to."""
        try:
            value = self.coercer(raw)
        except (ValueError, TypeError) as exc:
            raise EnvVarInvalid(
                self.name, self.description,
                f"coercion failed for value {raw!r}: {exc}",
            ) from exc
        if self.validator is not None:
            try:
                self.validator(value)
            except ValueError as exc:
                raise EnvVarInvalid(
                    self.name, self.description, str(exc),
                ) from exc
        return value


# ---------------------------------------------------------------------------
# Patterns (used by validators below)


# IAM ARN format — ``arn:aws:<service>:<region>:<account>:<resource>``.
# Fairly permissive on resource part (QS resource paths use slashes).
_IAM_ARN_RE: Final = re.compile(
    r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:[0-9]{12}:.+",
)


# AWS region — ``us-east-1`` shape.
_AWS_REGION_RE: Final = re.compile(r"[a-z]{2}-[a-z]+-\d+")


# ---------------------------------------------------------------------------
# Specs — the canonical registry


# Y.2.gate.c.2 — per-run output dir, set by the runner. Subprocess
# pytest fixtures + sidecar hooks (timings, browser traces, server
# logs) read this to route artifacts into ``runs/<run-id>/``. Absent
# in legacy mode (direct ``pytest`` invocation).
RECON_GEN_RUN_DIR: Final = EnvVar(
    name="RECON_GEN_RUN_DIR",
    legacy_name="QS_GEN_RUN_DIR",
    description=(
        "Per-run output directory; set by the test layer chain runner. "
        "Absent in legacy direct-pytest invocations."
    ),
    coercer=Path,
    optional=True,
    validator=must_be_dir,
)

# Y.2.gate.c.2 — which layer the subprocess belongs to. Used by
# the timings hook in tests/conftest.py to write into
# ``timings/<layer>.jsonl``. Set by the runner; absent in legacy.
RECON_GEN_LAYER: Final = EnvVar(
    name="RECON_GEN_LAYER",
    legacy_name="QS_GEN_LAYER",
    description="Layer name (unit/db/app2/deploy/api/browser); set by runner.",
    coercer=str,
    optional=True,
)

# E2E gate — every test under ``tests/e2e/`` checks this and skips
# silently when unset. Operator opts in for the heavyweight cells
# (DB / AWS / Playwright). Bool semantics: any non-empty = on.
RECON_GEN_E2E: Final = EnvVar(
    name="RECON_GEN_E2E",
    description="Bool gate for tests/e2e/ — set to any non-empty value to enable.",
    coercer=_bool_coercer,
    optional=True,
)

# Y.2.gate.b.2.impl — variant DB connection URL threaded by the
# runner to subprocess pytest. ``connect_demo_db`` reads via
# ``load_config`` env-override path so the variant container URL
# replaces the cfg-file URL when set.
RECON_GEN_DEMO_DATABASE_URL: Final = EnvVar(
    name="RECON_GEN_DEMO_DATABASE_URL",
    legacy_name="QS_GEN_DEMO_DATABASE_URL",
    description=(
        "DB connection URL override for demo / test runs. Set by the "
        "runner for non-default variants (local-pg / local-oracle). "
        "Falls back to cfg.demo_database_url when absent."
    ),
    coercer=str,
    optional=True,
    # No validator — psycopg / oracledb give actionable errors on
    # bad URLs; better to let those surface than to half-implement
    # URL parsing here.
)

# Y.2.gate.c.11 — operator opt-in to capture Playwright traces on
# every test (default is failure-only). Plumbed by RunOptions; the
# webkit_page helper checks it in the finally block.
RECON_GEN_TRACE_ALL: Final = EnvVar(
    name="RECON_GEN_TRACE_ALL",
    legacy_name="QS_GEN_TRACE_ALL",
    description=(
        "Bool — set to any non-empty value to capture Playwright traces "
        "on every test (default: failure-only)."
    ),
    coercer=_bool_coercer,
    optional=True,
)

# Y.2.gate.c.6.xdist-safety — operator pin for the random-per-run
# fuzz seed. Set this to repro a fuzz failure. Otherwise the runner
# rolls a fresh value each invocation.
RECON_GEN_FUZZ_SEED: Final = EnvVar(
    name="RECON_GEN_FUZZ_SEED",
    legacy_name="QS_GEN_FUZZ_SEED",
    description=(
        "Int — pin the fuzz-seed value (operator-facing repro knob). "
        "Absent → runner rolls a fresh random value per invocation."
    ),
    coercer=int,
    optional=True,
    validator=positive_int,
)

# Y.2.gate.b.14.3 — destructive-op opt-in. ``./run_tests.sh down`` /
# ``./run_tests.sh sweep`` / dirty-state deploy bypass all check
# this when their explicit ``--yes`` flag is absent.
RECON_GEN_RUNNER_YES: Final = EnvVar(
    name="RECON_GEN_RUNNER_YES",
    legacy_name="QS_GEN_RUNNER_YES",
    description=(
        "Bool — confirms destructive runner ops (down/sweep, dirty "
        "deploy bypass) when the --yes flag is absent."
    ),
    coercer=_bool_coercer,
    optional=True,
)

# Y.2.gate.k.1+k.6 — runner CI-mode opt-in. When set, ``setup_variant``
# skips Docker container spin-up for ``lo`` targets and assumes the DB
# is already reachable at ``RECON_GEN_DEMO_DATABASE_URL``. Used by GHA
# workflow YAMLs that pre-provision Postgres / Oracle via the
# ``services:`` block — the runner becomes a thin orchestrator instead
# of double-spinning Docker.
#
# Why this exists: the runner's local-Docker spin-up conflicts with
# GHA service containers (port collisions, double cost, no shared
# health-check). CI-mode lets the workflow keep its service block AND
# still invoke the runner so the chain (unit → db → app2 → ...) is
# the single canonical entry point.
#
# Contract: when set + target=lo, ``RECON_GEN_DEMO_DATABASE_URL`` MUST
# also be set; setup_variant raises ``EnvVarRequired`` otherwise. The
# variant's URL passes through unchanged to the chain subprocesses.
# target=aw is unaffected (always cfg-driven).
RECON_GEN_RUNNER_CI: Final = EnvVar(
    name="RECON_GEN_RUNNER_CI",
    legacy_name="QS_GEN_RUNNER_CI",
    description=(
        "Bool — when set, setup_variant skips Docker spin-up for lo "
        "targets and assumes RECON_GEN_DEMO_DATABASE_URL points at a "
        "pre-provisioned DB (e.g. GHA service container). Required "
        "for k.6 workflow rewire so CI doesn't double-spin Docker."
    ),
    coercer=_bool_coercer,
    optional=True,
)

# Y.2.gate.c.9 / cmd_sweep — operator override for cfg-file
# discovery. Absent → fall back to per-dialect candidates under
# ``run/``.
RECON_GEN_CONFIG: Final = EnvVar(
    name="RECON_GEN_CONFIG",
    legacy_name="QS_GEN_CONFIG",
    description=(
        "Override path to config.yaml. Absent → cfg discovery falls "
        "back to run/config.{postgres,oracle}.yaml etc."
    ),
    coercer=Path,
    optional=True,
    validator=must_be_file,
)

# tests/e2e/conftest.py — operator override for which L2 instance
# the e2e fixtures target. Absent → bundled spec_example default.
# Used by harness fixtures + the dataset SQL smoke test.
RECON_GEN_TEST_L2_INSTANCE: Final = EnvVar(
    name="RECON_GEN_TEST_L2_INSTANCE",
    description=(
        "Path to L2 instance YAML for e2e fixtures. Absent → bundled "
        "spec_example default."
    ),
    coercer=Path,
    optional=True,
    validator=must_be_file,
)

# Browser e2e — required for embed-URL signing. The probe
# (_probe_qs_e2e_user_arn) catches the absent case before dispatch
# to give an operator-actionable message.
RECON_E2E_USER_ARN: Final = EnvVar(
    name="RECON_E2E_USER_ARN",
    legacy_name="QS_E2E_USER_ARN",
    description=(
        "QuickSight user ARN for embed-URL signing in browser e2e tests. "
        "Required when running the browser layer."
    ),
    coercer=str,
    optional=False,
    validator=matches(_IAM_ARN_RE),
)

# tests/e2e/conftest.py tunables — Playwright wait knobs. Both
# default to sensible values in the helpers; operator override
# extends timeouts on slow CI runners.
RECON_E2E_PAGE_TIMEOUT: Final = EnvVar(
    name="RECON_E2E_PAGE_TIMEOUT",
    legacy_name="QS_E2E_PAGE_TIMEOUT",
    description=(
        "Playwright page-load timeout in milliseconds. Defaults applied "
        "in the helpers; override for slow CI runners."
    ),
    coercer=int,
    optional=True,
    validator=positive_int,
)

RECON_E2E_VISUAL_TIMEOUT: Final = EnvVar(
    name="RECON_E2E_VISUAL_TIMEOUT",
    legacy_name="QS_E2E_VISUAL_TIMEOUT",
    description=(
        "Playwright per-visual wait timeout in milliseconds. Defaults "
        "applied in the helpers."
    ),
    coercer=int,
    optional=True,
    validator=positive_int,
)

# QuickSight identity region (us-east-1 by convention; the embed-URL
# generator is identity-region-aware).
RECON_E2E_IDENTITY_REGION: Final = EnvVar(
    name="RECON_E2E_IDENTITY_REGION",
    legacy_name="QS_E2E_IDENTITY_REGION",
    description="QuickSight identity region for embed-URL signing.",
    coercer=str,
    optional=True,
    validator=matches(_AWS_REGION_RE),
)

# config.py — comma-separated list of IAM principal ARNs to grant
# permissions on generated resources. CSV format; the loader splits
# + validates each entry.
RECON_GEN_PRINCIPAL_ARNS: Final = EnvVar(
    name="RECON_GEN_PRINCIPAL_ARNS",
    legacy_name="QS_GEN_PRINCIPAL_ARNS",
    description=(
        "Comma-separated IAM principal ARNs to grant permissions on "
        "generated resources. Overrides cfg.principal_arns."
    ),
    coercer=str,
    optional=True,
    # No validator — the parsed list is checked downstream by the cfg
    # loader (each ARN runs through the same regex used elsewhere).
)

# common/browser/helpers.py — operator override for where browser
# screenshots / failure dumps land in legacy mode (when RECON_GEN_RUN_DIR
# unset). Default: tests/e2e/screenshots.
RECON_E2E_SCREENSHOT_DIR: Final = EnvVar(
    name="RECON_E2E_SCREENSHOT_DIR",
    legacy_name="QS_E2E_SCREENSHOT_DIR",
    description=(
        "Override directory for browser e2e screenshots / failure dumps "
        "in legacy mode (when RECON_GEN_RUN_DIR is unset). "
        "Default: tests/e2e/screenshots."
    ),
    coercer=Path,
    optional=True,
    # No must_be_dir validator — the helper auto-creates the dir on
    # first write. Validating "must exist" would defeat that.
)

# tests/audit/test_pdf_matches_scenario.py — bool gate for
# destructive DB tests in the audit suite. Same shape as
# RECON_GEN_E2E (any non-empty = on).
RECON_GEN_DB_TESTS: Final = EnvVar(
    name="RECON_GEN_DB_TESTS",
    legacy_name="QS_GEN_DB_TESTS",
    description=(
        "Bool — set to any non-empty value to enable the destructive "
        "audit DB tests in tests/audit/."
    ),
    coercer=_bool_coercer,
    optional=True,
)


# ---------------------------------------------------------------------------
# Cfg-shaped env vars — overrides for fields in `Config`.
#
# config.py's `load_config` walks a (cfg_key → EnvVar) map; each
# spec here owns one cfg field that can be overridden via env. Used
# in CI / containerized setups where editing run/config.yaml isn't
# practical. Most are str (cfg loader does its own coercion to
# Path / int as needed).

RECON_GEN_AWS_ACCOUNT_ID: Final = EnvVar(
    name="RECON_GEN_AWS_ACCOUNT_ID",
    legacy_name="QS_GEN_AWS_ACCOUNT_ID",
    description="AWS account ID — overrides cfg.aws_account_id.",
    coercer=str,
    optional=True,
    validator=matches(re.compile(r"\d{12}")),
)

RECON_GEN_AWS_REGION: Final = EnvVar(
    name="RECON_GEN_AWS_REGION",
    legacy_name="QS_GEN_AWS_REGION",
    description="AWS region (us-east-1 shape) — overrides cfg.aws_region.",
    coercer=str,
    optional=True,
    validator=matches(_AWS_REGION_RE),
)

RECON_GEN_DATASOURCE_ARN: Final = EnvVar(
    name="RECON_GEN_DATASOURCE_ARN",
    legacy_name="QS_GEN_DATASOURCE_ARN",
    description=(
        "QuickSight datasource ARN — overrides cfg.datasource_arn. "
        "Required only when cfg has no demo_database_url."
    ),
    coercer=str,
    optional=True,
    validator=matches(_IAM_ARN_RE),
)

RECON_GEN_DEPLOYMENT_NAME: Final = EnvVar(
    name="RECON_GEN_DEPLOYMENT_NAME",
    legacy_name="QS_GEN_DEPLOYMENT_NAME",
    description=(
        "Per-deploy QS namespace (kebab-case) — overrides "
        "cfg.deployment_name. Z.C: replaces v8.x's RECON_GEN_RESOURCE_PREFIX "
        "+ RECON_GEN_L2_INSTANCE_PREFIX (those collapsed into one field). "
        "Used by the Y.2.gate.m runner to namespace per-cell aw-target "
        "deploys so sister cells (e.g., sp_pg_aw + sp_or_aw) don't "
        "collide on QS resource IDs."
    ),
    coercer=str,
    optional=True,
)

RECON_GEN_DB_TABLE_PREFIX: Final = EnvVar(
    name="RECON_GEN_DB_TABLE_PREFIX",
    legacy_name="QS_GEN_DB_TABLE_PREFIX",
    description=(
        "Per-deploy DB table-name prefix (snake_case, ≤30 chars) — "
        "overrides cfg.db_table_prefix. Z.C: replaces direct reads of "
        "L2Instance.instance in schema/seed/datasets emit paths. "
        "Used by the Y.2.gate.m runner to namespace per-cell aw-target "
        "deploys so sister cells don't collide on shared-DB tables."
    ),
    coercer=str,
    optional=True,
)

RECON_GEN_DIALECT: Final = EnvVar(
    name="RECON_GEN_DIALECT",
    legacy_name="QS_GEN_DIALECT",
    description=(
        "DB dialect (postgres / oracle / sqlite) — overrides "
        "cfg.dialect."
    ),
    coercer=str,
    optional=True,
    validator=matches(re.compile(r"postgres|oracle|sqlite")),
)

RECON_GEN_APP2_DB_POOL_SIZE: Final = EnvVar(
    name="RECON_GEN_APP2_DB_POOL_SIZE",
    description=(
        "App2 DB pool size — overrides cfg.app2_db_pool_size. "
        "Cfg loader coerces from string to int."
    ),
    coercer=str,
    optional=True,
    # No validator — load_config does the int coercion + range check.
)

# Y.2.gate.l — RDS identifiers for the start/stop lifecycle commands.
# RDS identifier rules: 1-63 chars, lowercase alphanumeric + hyphens,
# starts with a letter, no trailing hyphen, no consecutive hyphens.
# `cmd_up aws` / `cmd_down aws` / `cmd_status` read these to know which
# cluster + instance to act on; CI workflows inject them as the CI-side
# identifiers (separate from operator's local-dev ones — see gate.l.0
# provisioning runbook).
_RDS_IDENT_RE: Final = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")

RECON_GEN_AWS_PG_CLUSTER_ID: Final = EnvVar(
    name="RECON_GEN_AWS_PG_CLUSTER_ID",
    legacy_name="QS_GEN_AWS_PG_CLUSTER_ID",
    description=(
        "Aurora PG cluster identifier (e.g., 'database-2' or "
        "'recon-ci-aurora') — overrides cfg.aws_pg_cluster_id. "
        "Required for `./run_tests.sh up aws` / `down aws` / `status`."
    ),
    coercer=str,
    optional=True,
    validator=matches(_RDS_IDENT_RE),
)

RECON_GEN_AWS_ORACLE_INSTANCE_ID: Final = EnvVar(
    name="RECON_GEN_AWS_ORACLE_INSTANCE_ID",
    legacy_name="QS_GEN_AWS_ORACLE_INSTANCE_ID",
    description=(
        "Oracle RDS instance identifier (e.g., 'database-3' or "
        "'recon-ci-oracle') — overrides cfg.aws_oracle_instance_id. "
        "Required for `./run_tests.sh up aws` / `down aws` / `status`."
    ),
    coercer=str,
    optional=True,
    validator=matches(_RDS_IDENT_RE),
)
