#!/usr/bin/env bash
# X.4.j.4 — Operator helper: spin a fresh postgres-in-docker as the
# Studio's ETL source, point Studio at a sqlite tempfile destination,
# and bind Studio. Deploy clicks then exercise the full
# etl_hook → wipe → pull → generator → matview-refresh → reload-bump
# pipeline against the just-spun postgres.
#
# Usage:
#   scripts/studio-with-pg-source.sh [run/sasquatch_pr.yaml]
#
# Defaults to run/sasquatch_pr.yaml. Container is torn down on exit
# (EXIT trap). Prints the bound Studio URL before spawning.
#
# Prereqs: docker daemon running, .venv with quicksight-gen + dev
# extras installed (uv sync --extra dev --extra audit), the L2 yaml
# at the path argument exists.

set -euo pipefail

L2_YAML="${1:-run/sasquatch_pr.yaml}"
if [[ ! -f "$L2_YAML" ]]; then
  echo "error: L2 yaml not found at $L2_YAML" >&2
  echo "  pass an explicit path: scripts/studio-with-pg-source.sh path/to/your.yaml" >&2
  exit 2
fi
L2_YAML_ABS="$(cd "$(dirname "$L2_YAML")" && pwd)/$(basename "$L2_YAML")"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
QSGEN="$REPO_ROOT/.venv/bin/quicksight-gen"
if [[ ! -x "$QSGEN" ]]; then
  echo "error: $QSGEN not found — run 'uv sync --extra dev --extra audit' first" >&2
  exit 2
fi

if ! docker ps >/dev/null 2>&1; then
  echo "error: docker daemon not reachable — start Docker Desktop / dockerd" >&2
  exit 2
fi

# Fresh tempdir for the per-run cfg / sqlite / etl_hook.
WORK_DIR="$(mktemp -d -t quicksight-studio-XXXXXX)"
echo "studio workspace: $WORK_DIR"

# Pick a random unused port for postgres. Bash + python fallback, in
# order of preference; falls back to a fixed sentinel if both fail.
PG_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()' 2>/dev/null || echo "")"
if [[ -z "$PG_PORT" ]]; then
  echo "error: could not allocate a free port via python3 socket" >&2
  exit 2
fi

PG_CONTAINER_NAME="quicksight-studio-pg-$$"
PG_PASSWORD="quicksight_studio_pw"

cleanup() {
  echo "tearing down postgres container $PG_CONTAINER_NAME …"
  docker rm -f "$PG_CONTAINER_NAME" >/dev/null 2>&1 || true
  echo "(workspace $WORK_DIR retained for debugging)"
}
trap cleanup EXIT INT TERM

echo "starting postgres:17-alpine on 127.0.0.1:$PG_PORT (container $PG_CONTAINER_NAME) …"
docker run -d --rm \
  --name "$PG_CONTAINER_NAME" \
  -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -e POSTGRES_DB=quicksight \
  -p "127.0.0.1:$PG_PORT:5432" \
  postgres:17-alpine >/dev/null

# Wait for postgres to accept connections.
echo -n "waiting for postgres to accept connections "
for _ in $(seq 1 30); do
  if docker exec "$PG_CONTAINER_NAME" pg_isready -U postgres >/dev/null 2>&1; then
    echo " ready"
    break
  fi
  echo -n "."
  sleep 1
done

PG_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PG_PORT}/quicksight"
SQLITE_PATH="$WORK_DIR/demo.sqlite"

# Derive the L2 instance prefix (sasquatch_pr / spec_example / …) by
# grepping the yaml. Cheap awk is fine — operator-controlled file,
# not adversarial.
INSTANCE_PREFIX="$(awk -F': *' '/^instance:/ { print $2; exit }' "$L2_YAML_ABS" | tr -d '"' | tr -d "'")"
if [[ -z "$INSTANCE_PREFIX" ]]; then
  echo "error: could not derive instance prefix from $L2_YAML_ABS (looking for top-level 'instance:' key)" >&2
  exit 2
fi
echo "L2 instance: $INSTANCE_PREFIX"

# Per-pipeline pg cfg (the etl_hook invokes quicksight-gen against this).
PG_CFG="$WORK_DIR/pg_etl_cfg.yaml"
cat >"$PG_CFG" <<EOF
aws_account_id: "111122223333"
aws_region: us-east-1
datasource_arn: arn:aws:quicksight:us-east-1:111122223333:datasource/x
demo_database_url: "$PG_URL"
dialect: postgres
EOF

# etl_hook: shells out to quicksight-gen data apply --execute against
# the postgres container. Mirrors tests/e2e/_studio_deploy_helpers.py
# write_etl_hook_script.
ETL_HOOK="$WORK_DIR/etl_hook.sh"
cat >"$ETL_HOOK" <<EOF
#!/bin/bash
set -euo pipefail
exec "$QSGEN" data apply --execute -c "$PG_CFG" --l2 "$L2_YAML_ABS"
EOF
chmod +x "$ETL_HOOK"

# Studio cfg: sqlite destination + etl_datasource=pg + etl_hook.
STUDIO_CFG="$WORK_DIR/studio_cfg.yaml"
cat >"$STUDIO_CFG" <<EOF
aws_account_id: "111122223333"
aws_region: us-east-1
datasource_arn: arn:aws:quicksight:us-east-1:111122223333:datasource/x
demo_database_url: "sqlite:///$SQLITE_PATH"
dialect: sqlite
default_l2_instance: "$L2_YAML_ABS"
etl_hook: "$ETL_HOOK"
etl_datasource:
  url: "$PG_URL"
  transactions_table: "${INSTANCE_PREFIX}_transactions"
  daily_balances_table: "${INSTANCE_PREFIX}_daily_balances"
test_generator:
  scope: full
EOF

# Apply schema to both dbs so step 2's pull and the initial studio
# render have tables to talk to.
echo "applying schema to postgres (etl source) …"
"$QSGEN" schema apply -c "$PG_CFG" --l2 "$L2_YAML_ABS" --execute >/dev/null

echo "applying schema to sqlite (studio destination) …"
"$QSGEN" schema apply -c "$STUDIO_CFG" --l2 "$L2_YAML_ABS" --execute >/dev/null

cat <<EOF

╭───────────────────────────────────────────────────────────────╮
│ Studio is starting against:                                   │
│   etl source:  $PG_URL
│   destination: sqlite:///$SQLITE_PATH
│   etl hook:    $ETL_HOOK
│                                                               │
│ Click "Deploy changes" in the Studio header to exercise the   │
│ full pipeline:                                                │
│   1. etl_hook re-seeds postgres                               │
│   2. studio wipes the sqlite demo data                        │
│   3. studio pulls postgres → sqlite                           │
│   4. generator runs scope=full on top                         │
│   5. matviews refresh                                         │
│   6. data_generation_id bumps; open dashboard tabs reload     │
│                                                               │
│ Ctrl+C to stop. Container will be torn down automatically.    │
╰───────────────────────────────────────────────────────────────╯

EOF

# NOT `exec` — exec replaces bash with the studio process, which
# defeats the EXIT trap (no bash means no trap to fire when studio
# exits). Run as a child with wait so SIGINT/SIGTERM/normal-exit
# all reach our trap and tear the postgres container down.
"$QSGEN" studio -c "$STUDIO_CFG" --l2 "$L2_YAML_ABS" &
STUDIO_PID=$!
wait "$STUDIO_PID"
