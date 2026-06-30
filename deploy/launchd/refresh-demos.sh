#!/bin/sh
# AE.5 / CU.1 — nightly refresh wrapper for the Phase AE Mac mini demo host.
#
# CU.1 (2026-06-09): swapped from SQLite to DuckDB. CB.8 (v13.0.0) dropped
# the SQLite dialect entirely, so the prior `sqlite:///` URLs + `.sqlite3`
# paths started failing at `recon-gen schema apply` in every nightly run
# after the v13.0.0 release — the demos kept serving the last successful
# pre-v13.0.0 build until this fix landed.
#
# Sequence per instance (spec_example + sasquatch_pr):
#   1. pip install --upgrade recon-gen inside ~recon-demo/venv (or pin
#      to RECON_GEN_PIN_VERSION when set — the operator can hold a
#      release for one or more days if a regression lands).
#   2. Build a fresh DuckDB at $INSTANCE_DIR/next.duckdb via
#      schema apply + data apply + data refresh + audit verify. The
#      build runs OUTSIDE the launchd-loaded server's sandbox — it's
#      a regular shell invocation under recon-demo's user.
#   3. mv next.duckdb current.duckdb (atomic on POSIX same-filesystem).
#   4. SIGTERM the per-instance server; its plist's KeepAlive=true makes
#      launchd respawn it (~5s outage) reopening the swapped db.
#
# AH.6: the servers are LaunchDaemons (system domain) and recon-demo is a
# non-admin Standard user — it CANNOT `launchctl kickstart system/...`
# (that needs root) nor sudo. It CAN signal its own processes, and the
# server plists carry KeepAlive=true, so terminating the running server
# (matched by its unique --port) and letting launchd relaunch is the
# privilege-free restart. The pre-AH.6 `launchctl kickstart -k
# gui/<uid>/...` was a LaunchAgent-era leftover that returns exit 125
# ("Domain does not support specified action") against a system daemon.
#
# Failure handling: if pip install fails, the script aborts (set -e)
# and the existing servers keep serving the previous db. If schema
# apply / data apply / data refresh / audit verify fail for one
# instance, the script aborts before mv'ing next → current — the
# server keeps last-known-good. Logs to ~recon-demo/logs/refresh.{out,err}.log
# (the launchd plist's StandardOutPath/ErrorPath).
#
# Install:
#   cp deploy/launchd/refresh-demos.sh /Users/recon-demo/bin/
#   chmod 0500 /Users/recon-demo/bin/refresh-demos.sh
#   chown recon-demo:staff /Users/recon-demo/bin/refresh-demos.sh

set -eu

# Refuse to run as root. The 2026-06-15 demo-box outage burned hours
# because an operator reached for `sudo bash -x refresh-demos.sh` during
# 502 triage — sudo left root-owned `current.duckdb` / `audit.pdf` /
# log files that the launchd-managed dashboard service (running as
# recon-demo) couldn't read. Early-fail BEFORE pip install or any mv,
# so a wrong-user run leaves the filesystem untouched.
if [ "$(id -u)" -eq 0 ]; then
    echo "ERROR: refresh-demos.sh refuses to run as root." >&2
    echo "  Re-run as recon-demo:" >&2
    echo "    su - recon-demo -c $0" >&2
    echo "  Running under sudo leaves root-owned next.duckdb / audit.pdf" >&2
    echo "  files that the dashboard service (running as recon-demo)" >&2
    echo "  can't read after the atomic mv. The launchd-driven nightly" >&2
    echo "  fire always runs as recon-demo per the plist's UserName, so" >&2
    echo "  this check only affects manual invocations." >&2
    exit 2
fi

RECON_DEMO_HOME="${RECON_DEMO_HOME:-/Users/recon-demo}"
VENV="$RECON_DEMO_HOME/venv"
PIP="$VENV/bin/pip"
RECON_GEN="$VENV/bin/recon-gen"

echo "==> $(date -Iseconds) nightly refresh start"

# Step 1: upgrade the wheel. Honor RECON_GEN_PIN_VERSION if set
# (operator escape hatch to hold a release).
#
# CU.4-followup (2026-06-09): BS.6 (2026-05-29) collapsed the
# `[deploy,demo,audit,serve]` extras into one `[prod]`. Pre-followup
# the script targeted dead extras; pip emitted 4 "does not provide the
# extra" warnings and silently skipped installing every runtime dep
# (starlette / psycopg / oracledb / pyarrow / openpyxl / markdown /
# reportlab / pyHanko / boto3). That broke after a venv recreate
# until this swap landed.
if [ -n "${RECON_GEN_PIN_VERSION:-}" ]; then
    echo "==> pinning recon-gen==$RECON_GEN_PIN_VERSION"
    "$PIP" install --upgrade "recon-gen[prod]==$RECON_GEN_PIN_VERSION"
else
    echo "==> upgrading recon-gen from PyPI"
    "$PIP" install --upgrade "recon-gen[prod]"
fi

# Step 2-4 per instance. Define the loop body as a function so a
# failure on one instance still aborts the script (set -e propagates).
refresh_one() {
    instance="$1"
    short="$2"        # short label suffix (matches the plist Label suffix)
    port="$3"         # server's bind port — the unique restart matcher
    instance_dir="$RECON_DEMO_HOME/$instance"
    cfg="$instance_dir/config.yaml"
    l2="$instance_dir/l2.yaml"
    next_db="$instance_dir/next.duckdb"
    current_db="$instance_dir/current.duckdb"

    echo "==> refresh: $instance"

    # Build at next.duckdb to keep current.duckdb untouched until
    # we know the build succeeded. config.yaml points at current.duckdb
    # (the served path), so temporarily override via env so the build
    # writes to next instead.
    rm -f "$next_db" "$next_db.wal"

    # RECON_GEN_DEMO_DATABASE_URL takes precedence over cfg's
    # demo_database_url (per the standard env-override precedence —
    # see CLAUDE.md's "Cfg precedence" section).
    RECON_GEN_DEMO_DATABASE_URL="duckdb:///$next_db" \
        "$RECON_GEN" schema apply -c "$cfg" --l2 "$l2" --execute
    RECON_GEN_DEMO_DATABASE_URL="duckdb:///$next_db" \
        "$RECON_GEN" data apply -c "$cfg" --l2 "$l2" --execute
    RECON_GEN_DEMO_DATABASE_URL="duckdb:///$next_db" \
        "$RECON_GEN" data refresh -c "$cfg" --l2 "$l2" --execute

    # audit verify as the sanity gate — if the seed pipeline + matview
    # refresh disagree on the L1 invariants, abort before the mv.
    pdf="$instance_dir/audit.pdf.next"
    RECON_GEN_DEMO_DATABASE_URL="duckdb:///$next_db" \
        "$RECON_GEN" audit apply -c "$cfg" --l2 "$l2" --execute -o "$pdf"
    RECON_GEN_DEMO_DATABASE_URL="duckdb:///$next_db" \
        "$RECON_GEN" audit verify "$pdf" -c "$cfg" --l2 "$l2"
    mv "$pdf" "$instance_dir/audit.pdf"

    # Atomic swap: mv on same filesystem is a single inode rename.
    # DuckDB's .wal sits next to the db file; clear any stale one so
    # the next open starts from a clean WAL (the just-built next.duckdb
    # was opened and closed cleanly by the seed pipeline, so its .wal
    # should already be reconciled, but belt-and-suspenders).
    mv "$next_db" "$current_db"
    rm -f "$current_db.wal"

    # DZ.6 — rebuild the handbook into site.next, then swap, so a
    # mid-build window never serves a partial tree and a failed build
    # leaves the previous site/ intact. Built here (unsandboxed) because
    # the server's sandbox denies the build's write into the package docs
    # tree; the server serves site/ read-only via --docs-dir and reopens
    # it on the KeepAlive respawn below. NON-FATAL: the DB refresh +
    # audit gate already succeeded and current.duckdb is swapped, so a
    # docs warning must not abort the run (the `if` guard keeps set -e
    # from tripping on a non-zero build).
    site_dir="$instance_dir/site"
    site_next="$instance_dir/site.next"
    rm -rf "$site_next"
    if "$RECON_GEN" docs apply --l2 "$l2" -o "$site_next" --no-strict; then
        rm -rf "$site_dir"
        mv "$site_next" "$site_dir"
    else
        echo "WARN: docs build failed — keeping previous $site_dir" >&2
        rm -rf "$site_next"
    fi

    # Restart the per-instance server so it reopens current.duckdb.
    # SIGTERM the running process (matched by its unique --port, scoped
    # to our own uid); KeepAlive=true in the plist makes launchd respawn
    # it (~ThrottleInterval s) against the freshly-swapped db. `|| true`:
    # if the server is already down, launchd's KeepAlive is already
    # (re)starting it, so a no-match is not an error. See the AH.6 note
    # in the header for why this isn't `launchctl kickstart`.
    echo "==> restart server: io.hotchkiss.recon-demo.$short (SIGTERM + KeepAlive respawn)"
    pkill -TERM -U "$(id -u)" -f "[-][-]port $port" || true
}

refresh_one spec_example spec 8401
refresh_one sasquatch_pr sasquatch 8402

echo "==> $(date -Iseconds) nightly refresh complete"
