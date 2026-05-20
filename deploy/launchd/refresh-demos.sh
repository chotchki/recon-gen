#!/bin/sh
# AE.5 — nightly refresh wrapper for the Phase AE Mac mini demo host.
#
# Sequence per instance (spec_example + sasquatch_pr):
#   1. pip install --upgrade recon-gen inside ~recon-demo/venv (or pin
#      to RECON_GEN_PIN_VERSION when set — the operator can hold a
#      release for one or more days if a regression lands).
#   2. Build a fresh SQLite db at $INSTANCE_DIR/next.sqlite3 via
#      schema apply + data apply + data refresh + audit verify. The
#      build runs OUTSIDE the launchd-loaded server's sandbox — it's
#      a regular shell invocation under recon-demo's user.
#   3. mv next.sqlite3 current.sqlite3 (atomic on POSIX same-filesystem).
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

RECON_DEMO_HOME="${RECON_DEMO_HOME:-/Users/recon-demo}"
VENV="$RECON_DEMO_HOME/venv"
PIP="$VENV/bin/pip"
RECON_GEN="$VENV/bin/recon-gen"

echo "==> $(date -Iseconds) nightly refresh start"

# Step 1: upgrade the wheel. Honor RECON_GEN_PIN_VERSION if set
# (operator escape hatch to hold a release).
if [ -n "${RECON_GEN_PIN_VERSION:-}" ]; then
    echo "==> pinning recon-gen==$RECON_GEN_PIN_VERSION"
    "$PIP" install --upgrade "recon-gen[deploy,demo,audit,serve]==$RECON_GEN_PIN_VERSION"
else
    echo "==> upgrading recon-gen from PyPI"
    "$PIP" install --upgrade "recon-gen[deploy,demo,audit,serve]"
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
    next_db="$instance_dir/next.sqlite3"
    current_db="$instance_dir/current.sqlite3"

    echo "==> refresh: $instance"

    # Build at next.sqlite3 to keep current.sqlite3 untouched until
    # we know the build succeeded. config.yaml points at current.sqlite3
    # (the served path), so temporarily override via env so the build
    # writes to next instead.
    rm -f "$next_db" "$next_db-journal" "$next_db-wal" "$next_db-shm"

    # RECON_GEN_DEMO_DATABASE_URL takes precedence over cfg's
    # demo_database_url (per the standard env-override precedence —
    # see CLAUDE.md's "Cfg precedence" section).
    RECON_GEN_DEMO_DATABASE_URL="sqlite:///$next_db" \
        "$RECON_GEN" schema apply -c "$cfg" --l2 "$l2" --execute
    RECON_GEN_DEMO_DATABASE_URL="sqlite:///$next_db" \
        "$RECON_GEN" data apply -c "$cfg" --l2 "$l2" --execute
    RECON_GEN_DEMO_DATABASE_URL="sqlite:///$next_db" \
        "$RECON_GEN" data refresh -c "$cfg" --l2 "$l2" --execute

    # audit verify as the sanity gate — if the seed pipeline + matview
    # refresh disagree on the L1 invariants, abort before the mv.
    pdf="$instance_dir/audit.pdf.next"
    RECON_GEN_DEMO_DATABASE_URL="sqlite:///$next_db" \
        "$RECON_GEN" audit apply -c "$cfg" --l2 "$l2" --execute -o "$pdf"
    RECON_GEN_DEMO_DATABASE_URL="sqlite:///$next_db" \
        "$RECON_GEN" audit verify "$pdf" -c "$cfg" --l2 "$l2"
    mv "$pdf" "$instance_dir/audit.pdf"

    # Atomic swap: mv on same filesystem is a single inode rename.
    # SQLite WAL/SHM/journal files are tied to the db file path —
    # remove the stale ones so SQLite recreates them fresh on the
    # server's next open.
    mv "$next_db" "$current_db"
    rm -f "$current_db-journal" "$current_db-wal" "$current_db-shm"

    # Restart the per-instance server so it reopens current.sqlite3.
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
