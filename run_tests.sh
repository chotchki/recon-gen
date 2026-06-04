#!/usr/bin/env bash
#
# Thin operator entrypoint to the layered test chain.
#
# Architecture (post CB.17.d/e):
#   - The runner (`src/recon_gen/_dev/runner.py`) runs ONE pytest
#     invocation per layer. The cell-loop is gone; cmd_up_to is the
#     "thin pytest alias" for `up_to=<layer>` — it owns container
#     pre-spin, QS-side cfg materialization, plain-prefix seeding,
#     and per-layer env routing (all things conftest fixtures can't
#     do for subprocess-shelling tests like `test_audit_pdf_render_verify`).
#   - The shell script forwards to the runner for the chained layers
#     (db / app2 / deploy / qs_api / qs_browser) and for operational
#     verbs (sweep / up / down / status / pyright / dump-last-errors).
#   - The unit layer has no orchestration needs — direct pytest.
#
# Usage examples:
#   ./run_tests.sh up_to=unit                  # pytest direct (no orchestration)
#   ./run_tests.sh up_to=db                    # runner: one pytest, with container
#   ./run_tests.sh up_to=qs_browser            # full chain through deploy
#   ./run_tests.sh sweep --yes                 # clean orphan AWS/Docker resources
#   ./run_tests.sh status                      # what's currently running
#   ./run_tests.sh pyright [<paths>...]        # fast standalone type-check
#
# Cfg → env injection: `tests/conftest.py::_derive_env_from_cfg`
# promotes `cfg.auth.aws_profile` → `AWS_PROFILE`,
# `cfg.default_l2_instance` → `RECON_GEN_TEST_L2_INSTANCE`, and the
# resolved cfg path → `RECON_GEN_CONFIG`. Operator-set env wins.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "error: .venv not found at ${SCRIPT_DIR}/.venv — run 'uv sync --all-extras' first" >&2
  exit 1
fi

# Y.2.gate.h+i.0 — unset env vars the runner / conftest derive from cfg.
# Stale values in the operator's shell would shadow the cfg-injected ones
# and confuse triage when "why is the runner using THIS user / profile /
# L2?" doesn't match what cfg declares. Pre-existing values are never
# useful: cfg is the source of truth, populated from cfg.auth.aws_profile,
# the STS+ListUsers derivation, and cfg.default_l2_instance respectively.
unset AWS_PROFILE RECON_E2E_USER_ARN RECON_GEN_TEST_L2_INSTANCE

# Fast path: bare `up_to=unit` is pure unit + json + cli + docs + schema
# + l2 tests — no DB, no AWS, no L2-flavored fixtures. Direct pytest
# skips the runner's probe/run-dir/telemetry overhead (~1s) and keeps
# the iterate loop tight. ANY extra arg flips to the runner so the
# flag's behavior (incl. --coverage, --only, --parallel) matches the
# chained-layer invocation.
if [ "${1:-}" = "up_to=unit" ] && [ "$#" -eq 1 ]; then
  exec .venv/bin/pytest \
    tests/unit tests/json tests/cli tests/docs tests/schema tests/l2 \
    -q -n auto
fi

exec .venv/bin/python -m recon_gen._dev.runner "$@"
