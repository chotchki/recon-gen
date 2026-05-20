#!/bin/sh
# AE.4 — sasquatch demo launcher wrapper. Computes a fresh per-process
# tmpdir for STUDIO_STATE_DIR before exec-ing into sandbox-exec.
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
# SIGTERMs the server) creates a fresh tmpdir; the previous tmpdir is
# left behind in /var/folders for the OS to eventually reap.
#
# The sandbox-exec profile (recon-demo-sasquatch.sb) read+writes only
# the tmpdir passed via STUDIO_STATE_DIR — so even if old tmpdirs
# linger, the sandboxed process can't see them.

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

STUDIO_STATE_DIR="$(mktemp -d -t recon-demo-studio-state)"
export STUDIO_STATE_DIR

# Pass --demo-mode -c -- l2 -- port -- host via positional CLI args.
# `--no-docs` keeps the mkdocs build off the critical path on launch
# (the demo doesn't ship the handbook).
exec /usr/bin/sandbox-exec \
    -D HOME=/Users/recon-demo \
    -D INSTANCE_DIR=/Users/recon-demo/sasquatch_pr \
    -D PORT=8402 \
    -D PYTHON=/Users/recon-demo/venv/bin/python3.13 \
    -D STUDIO_STATE_DIR="$STUDIO_STATE_DIR" \
    -f /Users/recon-demo/sandbox/recon-demo-sasquatch.sb \
    -- /Users/recon-demo/venv/bin/recon-gen studio \
        --demo-mode \
        -c /Users/recon-demo/sasquatch_pr/config.yaml \
        --l2 /Users/recon-demo/sasquatch_pr/l2.yaml \
        --port 8402 \
        --host 127.0.0.1 \
        --no-docs
