#!/usr/bin/env bash
# AE.3 / CU.1 — provision one DuckDB demo instance for the Mac mini host.
#
# Bootstraps a per-instance state directory under the operator-chosen
# RECON_DEMO_HOME (default /Users/recon-demo). The instance directory
# carries: config.yaml (DuckDB-mode, per-instance deployment_name +
# db_table_prefix), l2.yaml (copied from the wheel's bundled fixture),
# current.duckdb (built via schema apply + data apply + data refresh),
# and an empty logs/ subdir for launchd's stdout/stderr destinations.
#
# CU.1 (2026-06-09): swapped from SQLite to DuckDB. CB.8 (v13.0.0)
# dropped the SQLite dialect; this script was emitting cfgs that no
# longer parse against the current `recon-gen` binary.
#
# Run this once per instance after AE.1 (user + venv setup); re-run as
# needed when starting fresh. Idempotent in the sense that it
# delete-then-creates the DuckDB file — operator state in the db file
# is lost (intended: every run is a clean rebuild).
#
# Usage:
#   provision_demo_instance.sh <instance_name> <port>
#
#   <instance_name>  Either ``spec_example`` (minimal pedagogical L2)
#                    or ``sasquatch_pr`` (realistic community-bank L2).
#                    Maps to the L2 yaml filename in the wheel's
#                    ``recon_gen/_l2_fixtures/`` package.
#   <port>           TCP port the server will bind on 127.0.0.1
#                    (e.g. 8401 for spec, 8402 for sasquatch). Stored
#                    in config.yaml metadata only — the launchd plist
#                    is what actually binds.
#
# Env (override defaults):
#   RECON_DEMO_HOME       Default: /Users/recon-demo
#   RECON_DEMO_VENV       Default: $RECON_DEMO_HOME/venv
#
# Exit codes:
#   0  success
#   1  bad usage
#   2  wheel install missing or fixture not found
#   3  recon-gen schema/data/audit step failed

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <instance_name> <port>" >&2
    echo "  <instance_name>  spec_example | sasquatch_pr" >&2
    echo "  <port>           bind port (e.g. 8401, 8402)" >&2
    exit 1
fi

INSTANCE="$1"
PORT="$2"

case "$INSTANCE" in
    spec_example|sasquatch_pr) ;;
    *)
        echo "error: instance_name must be spec_example or sasquatch_pr (got: $INSTANCE)" >&2
        exit 1
        ;;
esac

RECON_DEMO_HOME="${RECON_DEMO_HOME:-/Users/recon-demo}"
RECON_DEMO_VENV="${RECON_DEMO_VENV:-$RECON_DEMO_HOME/venv}"
RECON_GEN_BIN="$RECON_DEMO_VENV/bin/recon-gen"
PYTHON_BIN="$RECON_DEMO_VENV/bin/python3"

if [[ ! -x "$RECON_GEN_BIN" ]]; then
    echo "error: $RECON_GEN_BIN not found or not executable" >&2
    echo "       run AE.1 first to set up the venv + pip install recon-gen" >&2
    exit 2
fi

# Locate the wheel's bundled L2 yaml fixture. The wheel ships fixtures
# under ``recon_gen/_l2_fixtures/`` (pyproject.toml package_data entry).
RECON_GEN_INSTALL_DIR="$("$PYTHON_BIN" -c 'import recon_gen, os; print(os.path.dirname(recon_gen.__file__))')"
L2_FIXTURE_SRC="$RECON_GEN_INSTALL_DIR/_l2_fixtures/${INSTANCE}.yaml"

if [[ ! -f "$L2_FIXTURE_SRC" ]]; then
    echo "error: bundled L2 fixture not found at $L2_FIXTURE_SRC" >&2
    echo "       expected the wheel to ship recon_gen/_l2_fixtures/${INSTANCE}.yaml" >&2
    exit 2
fi

INSTANCE_DIR="$RECON_DEMO_HOME/$INSTANCE"
DB_FILE="$INSTANCE_DIR/current.duckdb"
CFG_FILE="$INSTANCE_DIR/config.yaml"
L2_FILE="$INSTANCE_DIR/l2.yaml"

mkdir -p "$INSTANCE_DIR/logs"

# Copy the L2 yaml fixture into the instance dir. The launchd-managed
# server reads from this copy; the original in the wheel is untouched.
cp "$L2_FIXTURE_SRC" "$L2_FILE"

# Write config.yaml. The values mirror src/recon_gen/_dev/runner.py's
# synth-cfg shape for the DuckDB dialect. ``aws_account_id`` /
# ``aws_region`` are pinned to harmless placeholders — the dashboards
# server never talks to AWS, but the Config dataclass requires them.
cat > "$CFG_FILE" <<EOF
# Phase AE — Mac mini self-hosted demo cfg for the $INSTANCE instance.
# Provisioned by scripts/provision_demo_instance.sh on $(date +%Y-%m-%d).
# Bound port: $PORT (launchd plist handles the actual bind).
aws_account_id: "111122223333"
aws_region: "us-east-1"
dialect: duckdb
demo_database_url: "duckdb:///$DB_FILE"
deployment_name: "recon-demo-$INSTANCE"
db_table_prefix: "demo_${INSTANCE//[^a-z0-9_]/_}"
EOF

# Build the DuckDB db fresh. delete-then-create so this script is
# idempotent (re-running rebuilds rather than appending). Schema +
# seed + matview refresh + audit verify in sequence; any step's
# non-zero exit aborts the script (set -e).
echo "==> provisioning $INSTANCE on port $PORT"
rm -f "$DB_FILE" "$DB_FILE.wal"

echo "==> schema apply"
"$RECON_GEN_BIN" schema apply -c "$CFG_FILE" --l2 "$L2_FILE" --execute

echo "==> data apply"
"$RECON_GEN_BIN" data apply -c "$CFG_FILE" --l2 "$L2_FILE" --execute

echo "==> data refresh"
"$RECON_GEN_BIN" data refresh -c "$CFG_FILE" --l2 "$L2_FILE" --execute

echo "==> audit verify (sanity probe)"
# audit verify recomputes L1 invariants against the just-built db and
# compares against the audit PDF emit. For a fresh seed it should
# always agree. A mismatch here means the seed pipeline + matview
# refresh disagree — abort before launchd picks up the new db.
AUDIT_PDF="$INSTANCE_DIR/audit.pdf"
"$RECON_GEN_BIN" audit apply -c "$CFG_FILE" --l2 "$L2_FILE" --execute -o "$AUDIT_PDF"
"$RECON_GEN_BIN" audit verify "$AUDIT_PDF" -c "$CFG_FILE" --l2 "$L2_FILE"

echo "==> docs build (handbook against this L2)"
# DZ.6 — build the mkdocs handbook ONCE here, into $INSTANCE_DIR/site.
# The launchd-managed server serves this dir read-only via `--docs-dir`
# (it does NOT build on launch — the build writes a theme-CSS shim into
# the installed package's docs tree, a write the server's sandbox
# denies, and rebuilding on every KeepAlive respawn would delay the
# bind). Fatal here on purpose: the server's `--docs-dir` requires this
# dir to exist with an index.html, so provisioning must produce it
# before launchd loads the plist. (refresh-demos.sh rebuilds it nightly
# with an atomic swap.) `--no-docs` build strictness so a stray mkdocs
# warning doesn't abort a fresh provision.
SITE_DIR="$INSTANCE_DIR/site"
rm -rf "$SITE_DIR"
"$RECON_GEN_BIN" docs apply --l2 "$L2_FILE" -o "$SITE_DIR" --no-strict

echo "==> $INSTANCE provisioned at $INSTANCE_DIR"
echo "    db:   $DB_FILE"
echo "    cfg:  $CFG_FILE"
echo "    l2:   $L2_FILE"
echo "    pdf:  $AUDIT_PDF"
echo "    site: $SITE_DIR (served at /docs/)"
echo ""
echo "Next: load the launchd plist for this instance:"
echo "  launchctl load -w ~/Library/LaunchAgents/io.hotchkiss.recon-demo.${INSTANCE/_*/}.plist"
