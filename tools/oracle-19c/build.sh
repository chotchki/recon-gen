#!/usr/bin/env bash
# Build the Oracle 19c container image we use for production-parity local
# testing (matches AWS RDS Oracle SE2 19c).
#
# Oracle does not redistribute their database binary; the build recipe is
# upstream-maintained in `oracle/docker-images`, but the zip itself must be
# downloaded from Oracle's account-gated portal first. See README.md.
#
# Architecture is implicit in which zip is present in the build context:
#
#   - arm64 host → expects ./LINUX.ARM64_1919000_db_home.zip (Oracle ships
#     19.19 directly as the arm64 base — no separate RU step)
#   - amd64 host → expects ./LINUX.X64_193000_db_home.zip (19.3 base)
#
# Output: tags the resulting image as `recon-gen/oracle-19c:local` so the
# runner can adopt it via image name regardless of host arch. The image is
# single-arch (the host's arch) — there is no cross-build path because each
# zip IS the architecture-specific bits.

set -euo pipefail

# Pinned upstream SHA — refresh by running:
#   curl -s https://api.github.com/repos/oracle/docker-images/commits/main \
#     | jq -r .sha
UPSTREAM_SHA="22bc10c9398e4061431aecabe15db037d720df0d"
UPSTREAM_REPO="https://github.com/oracle/docker-images.git"
LOCAL_TAG="recon-gen/oracle-19c:local"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

case "$(uname -m)" in
    arm64|aarch64)
        ARCH=arm64
        ZIP="LINUX.ARM64_1919000_db_home.zip"
        ;;
    x86_64|amd64)
        ARCH=amd64
        ZIP="LINUX.X64_193000_db_home.zip"
        ;;
    *)
        echo "build.sh: unsupported host arch $(uname -m)" >&2
        exit 2
        ;;
esac

if [[ ! -f "${SCRIPT_DIR}/${ZIP}" ]]; then
    cat >&2 <<EOF
build.sh: missing Oracle Database 19c binary for ${ARCH}.

Expected: ${SCRIPT_DIR}/${ZIP}

This binary is not redistributable. Download from Oracle:
  https://www.oracle.com/database/technologies/oracle19c-linux-downloads.html

For arm64:   "Oracle Database 19c (19.19) for LINUX ARM (aarch64)"
For amd64:   "Oracle Database 19c (19.3) for Linux x86-64"

You need a free Oracle account to download. Drop the zip in this directory
and re-run build.sh. Size is ~3GB; not committed to git.
EOF
    exit 2
fi

# Shallow-clone Oracle's repo at the pinned SHA into a scratch dir.
WORK="$(mktemp -d -t recon-gen-oracle-19c-XXXXXX)"
trap 'rm -rf "${WORK}"' EXIT

echo "build.sh: cloning oracle/docker-images @ ${UPSTREAM_SHA:0:12}…"
git clone --quiet --no-checkout "${UPSTREAM_REPO}" "${WORK}/upstream"
git -C "${WORK}/upstream" sparse-checkout init --cone
git -C "${WORK}/upstream" sparse-checkout set OracleDatabase/SingleInstance
git -C "${WORK}/upstream" checkout --quiet "${UPSTREAM_SHA}"

DOCKERFILE_DIR="${WORK}/upstream/OracleDatabase/SingleInstance/dockerfiles/19.3.0"
if [[ ! -d "${DOCKERFILE_DIR}" ]]; then
    echo "build.sh: pinned upstream is missing dockerfiles/19.3.0 — bump UPSTREAM_SHA" >&2
    exit 2
fi

# Hardlink (not symlink — Docker BuildKit's COPY can't resolve symlinks
# pointing outside the build context; not copy — the zip is 3GB and a
# fresh copy adds wall-clock for no benefit). Hardlinks need the source
# and target on the same filesystem; both are inside the repo + scratch
# dir on this machine's primary volume so this holds.
ln "${SCRIPT_DIR}/${ZIP}" "${DOCKERFILE_DIR}/${ZIP}"

echo "build.sh: building Oracle 19c EE for ${ARCH} (5-10 min)…"
pushd "${WORK}/upstream/OracleDatabase/SingleInstance/dockerfiles" > /dev/null
# Oracle's script invokes `docker build` with the canonical tag
# `oracle/database:19.3.0-ee`. -i skips the upstream checksum check (we
# trust the local zip; the operator downloaded it themselves).
./buildContainerImage.sh -v 19.3.0 -e -i
popd > /dev/null

# Retag as our stable local name so the runner doesn't care about
# upstream's versioned tag.
docker tag oracle/database:19.3.0-ee "${LOCAL_TAG}"

# ----- Pre-initialize the DB into the image (gvenzl-style fast-start) -----
#
# The base image ships only Oracle binaries; first container boot runs DBCA
# at /opt/oracle/oradata/$ORACLE_SID and takes ~3-4 min. We boot once now,
# let DBCA create the DB into the writable layer, gracefully shut down,
# then `docker commit` back onto the same tag. On subsequent boots
# Oracle's runOracle.sh sees the existing data files and skips DBCA —
# cold-start drops from ~240s to ~30s.
#
# INIT_PWD is the image-build-time placeholder — DBCA bakes it during the
# first-boot init step, then the image carries it as the post-commit default.
# It's NEVER the runtime password: post-BX.248 the runner (`_get_or_start_
# oracle_container` adopt path) and CI (`Force-reset` workflow step) both
# overwrite it with a per-invocation random value via
# `_reset_oracle_password_via_socket` / `sqlplus / as sysdba`. The image
# password is essentially scratch — but DBCA still needs SOMETHING during
# init, and it must be alphanumeric (dbca-silent rejects hyphens).
# `ORACLE_PDB=FREEPDB1` matches gvenzl's PDB name so URL shape stays
# unified across image fallbacks.
INIT_PWD="qsgentestpwd2026"
INIT_PDB="FREEPDB1"
INIT_NAME="recon-gen-oracle-init-$$"

echo "build.sh: pre-initializing DB into the image (~3-4 min, one-time)…"
docker run -d --name "${INIT_NAME}" \
    -e ORACLE_PWD="${INIT_PWD}" \
    -e ORACLE_PDB="${INIT_PDB}" \
    "${LOCAL_TAG}" > /dev/null

# Cleanup the init container even if we bail.
trap 'docker rm -f "${INIT_NAME}" >/dev/null 2>&1 || true; rm -rf "${WORK}"' EXIT

# Poll for the ready marker. Oracle 19c's first-init logs progress as
# "Prepare for db operation / 8% complete / Copying database files / 31%…"
# and ends with "DATABASE IS READY TO USE!".
#
# All grep pipes use `|| true` so set -euo pipefail doesn't kill the poll
# loop when grep returns 1 (no match yet on the first iteration).
echo "build.sh: waiting for DATABASE IS READY TO USE (up to 15 min)…"
DEADLINE=$(($(date +%s) + 900))
READY=0
while [ $(date +%s) -lt $DEADLINE ]; do
    LOGS=$(docker logs "${INIT_NAME}" 2>&1 || true)
    if printf "%s" "${LOGS}" | grep -q "DATABASE IS READY TO USE"; then
        READY=1
        break
    fi
    # Surface the most recent percentage line so the operator can see
    # progress without re-tailing logs manually.
    LAST_PROGRESS=$(printf "%s" "${LOGS}" | grep -E "^[[:space:]]*[0-9]+% complete" | tail -1 || true)
    if [[ -n "${LAST_PROGRESS}" ]]; then
        printf "\rbuild.sh: %s    " "${LAST_PROGRESS}"
    fi
    sleep 10
done
echo

if [[ "${READY}" != 1 ]]; then
    echo "build.sh: container never reached DATABASE IS READY in 15min — aborting" >&2
    docker logs --tail 50 "${INIT_NAME}" >&2 || true
    exit 2
fi

# Graceful Oracle shutdown via sqlplus inside the container; ensures the
# data files commit consistently (no recovery needed on next boot).
echo "build.sh: shutting down DB cleanly so the commit captures consistent files…"
docker exec "${INIT_NAME}" bash -c \
    'echo -e "SHUTDOWN IMMEDIATE;\nexit\n" | sqlplus -s / as sysdba' \
    > /dev/null 2>&1 || true

# Stop the container so docker commit captures a quiesced state.
docker stop --time 60 "${INIT_NAME}" > /dev/null

# Commit the writable layer back onto the same tag. Future `docker run`
# starts the container with the DB files already present; runOracle.sh
# detects the existing $ORACLE_BASE/oradata/$ORACLE_SID and skips DBCA.
echo "build.sh: committing initialized DB back onto ${LOCAL_TAG}…"
docker commit "${INIT_NAME}" "${LOCAL_TAG}" > /dev/null
docker rm "${INIT_NAME}" > /dev/null

echo
echo "build.sh: done. Image ${LOCAL_TAG} pre-initialized + ready."
echo
echo "Verify:   docker image inspect ${LOCAL_TAG} --format '{{.Architecture}}'"
echo "Cleanup:  docker rmi oracle/database:19.3.0-ee  # keeps recon-gen/oracle-19c:local"
