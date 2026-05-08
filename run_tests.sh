#!/usr/bin/env bash
#
# Y.2.gate runner — layered test chain with per-run output isolation
# and timing-diff drift detection.
#
# Usage examples:
#   ./run_tests.sh up_to=browser
#   ./run_tests.sh up_to=db --variants=pg --fuzz-seeds=10
#   ./run_tests.sh sweep --yes        # clean orphan AWS/Docker resources
#   ./run_tests.sh up [local|aws]     # boot dependencies (default = both)
#   ./run_tests.sh down [local|aws]   # tear down (default = both); --yes required
#   ./run_tests.sh status [--cost]    # what's currently running
#
# All real logic lives in src/quicksight_gen/_dev/runner.py. This script
# is a thin shim: verify the venv exists, exec the orchestrator. Argparse
# is owned by Python.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "error: .venv not found at ${SCRIPT_DIR}/.venv — run 'uv sync --all-extras' first" >&2
  exit 1
fi

# Y.2.gate.h+i.0 — unset env vars the runner derives from cfg. Stale values
# in the operator's shell would shadow the cfg-injected ones (or confuse
# triage when "why is the runner using THIS user / profile / L2?" doesn't
# match what cfg declares). Pre-existing values are never useful: the runner
# is the sole source of truth for these, populated from cfg.auth.aws_profile,
# the STS+ListUsers derivation, and cfg.default_l2_instance respectively.
unset AWS_PROFILE QS_E2E_USER_ARN QS_GEN_TEST_L2_INSTANCE

exec .venv/bin/python -m quicksight_gen._dev.runner "$@"
