#!/bin/sh
# AE.4 / CU.2 — sasquatch demo launcher wrapper. Per-launch tmpdir for
# (1) the L2 yaml overlay — visitor edits land here and are wiped on
# every KeepAlive restart — and (2) `.studio-state.yaml` (trainer
# knobs, same lifecycle).
#
# Install:
#   cp deploy/launchd/launch-sasquatch.sh /Users/recon-demo/bin/
#   chmod 0500 /Users/recon-demo/bin/launch-sasquatch.sh
#   chown recon-demo:staff /Users/recon-demo/bin/launch-sasquatch.sh
#
# The launchd plist (io.hotchkiss.recon-demo.sasquatch.plist) execs
# this script. Why a wrapper: launchd's ProgramArguments doesn't expand
# $(...) — to compute mktemp at start time, we shell out. Each launchd
# start (RunAtLoad, or a KeepAlive respawn after the nightly refresh
# SIGTERMs the server) creates a fresh tmpdir + re-copies the canonical
# L2 yaml; the previous tmpdir is left behind in /var/folders for the
# OS to eventually reap.
#
# CU.2 swap (2026-06-09): the L2 overlay copy moved from
# `recon-gen studio --demo-mode` (which CU.3 deletes) to this wrapper
# — `recon-gen` now sees a writable `--l2` path and never needs a
# demo-aware code path. The sandbox-exec profile is the security
# layer: writes to the canonical L2 inside INSTANCE_DIR remain denied,
# writes to the tmpdir overlay are allowed.

set -eu

# Skip Python bytecode caching — Python tries to write .pyc files
# alongside imported modules, which means `__pycache__/` dirs inside
# /opt/homebrew/Cellar/python@3.13/.../lib/python3.13/<module>/ for
# stdlib imports. The sandbox profile denies file-write outside the
# per-instance state dir + tmpdir; Python normally swallows the EPERM,
# but uvicorn's accept-loop setup surfaces the downstream error as
# `ERROR: [Errno 1] Operation not permitted` and crashes the server.
# Exported here (not just set in the plist) because launchd → wrapper
# → sandbox-exec → recon-gen env-var propagation has been unreliable
# on Tahoe; setting it directly in the wrapper is load-bearing.
export PYTHONDONTWRITEBYTECODE=1

INSTANCE_DIR=/Users/recon-demo/sasquatch_pr
CANONICAL_L2="$INSTANCE_DIR/l2.yaml"

STUDIO_STATE_DIR="$(mktemp -d -t recon-demo-studio-state)"
export STUDIO_STATE_DIR

# CU.2 — canonical → overlay copy. Visitor POST/PUT/DELETE on
# /l2_shape/* will write back to this overlay file (sandbox allows it,
# the canonical stays read-only). KeepAlive respawn re-runs this
# script → fresh tmpdir → canonical re-copied. Any accumulated visitor
# edits in the prior tmpdir are intentionally discarded.
cp "$CANONICAL_L2" "$STUDIO_STATE_DIR/l2.yaml"

# CU.2 — per-file disk cap. 50MB is way more than the L2 yaml + state
# file ever need (canonical sasquatch_pr l2.yaml is ~35KB); a hostile
# visitor scripting POSTs into accounts/rails/templates would have to
# add roughly a million entries to hit this cap. The sandbox-exec
# writable allowlist is STUDIO_STATE_DIR (for L2 overlay +
# .studio-state.yaml) + current.duckdb + .wal + INSTANCE_DIR/logs, so
# per-file cap is effectively the total cap for visitor-controlled
# disk usage.
ulimit -f 51200

# Pass `-c` / `--l2` / `--port` / `--host` via positional CLI args.
# DZ.6 — `--docs-dir` serves the handbook pre-built by provision /
# refresh-demos into $INSTANCE_DIR/site (read-only). NOT --no-docs +
# on-launch build: a themed L2 makes the build write a CSS shim into the
# installed package's docs tree, a write this sandbox denies, and a
# rebuild on every KeepAlive respawn would delay the bind. The site dir
# is canonical (outside STUDIO_STATE_DIR), so it survives the per-launch
# tmpdir churn. The `--l2` flag points at the tmpdir overlay (CU.2) —
# `recon-gen` sees a writable path with no awareness it's a demo install.
exec /usr/bin/sandbox-exec \
    -D HOME=/Users/recon-demo \
    -D INSTANCE_DIR="$INSTANCE_DIR" \
    -D PORT=8402 \
    -D PYTHON=/Users/recon-demo/venv/bin/python3.13 \
    -D STUDIO_STATE_DIR="$STUDIO_STATE_DIR" \
    -f /Users/recon-demo/sandbox/recon-demo-sasquatch.sb \
    -- /Users/recon-demo/venv/bin/recon-gen studio \
        -c /Users/recon-demo/sasquatch_pr/config.yaml \
        --l2 "$STUDIO_STATE_DIR/l2.yaml" \
        --port 8402 \
        --host 127.0.0.1 \
        --docs-dir "$INSTANCE_DIR/site"
