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

# Auto-load run/secrets.env so callers never need the `set -a; source`
# dance — `./run_tests.sh` is meant to just work. The file holds dev-only
# secrets (e.g. RECON_GEN_CLOUDFLARE_TOKEN for the QS→Docker-PG DNS
# forward the qs_browser layer's TLS pre-flight requires).
#
# Env wins, file fills the gap: a var already in the environment (CI's
# GitHub secrets, or an operator's exported value) is NEVER overwritten;
# the file only supplies vars that aren't set. The sed rewrites each
# `KEY=val` to `KEY="${KEY:-val}"` so the existing env value survives,
# then `set -a` exports the result so the runner subprocess inherits it.
# Tolerates an optional `export ` prefix + comment/blank lines.
# [[feedback_no_credential_friction]] — the runner derives; the operator
# never has to remember.
if [ -f run/secrets.env ]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed -E 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$/\2="${\2:-\3}"/' run/secrets.env)
  set +a
fi

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
