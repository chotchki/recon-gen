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

echo
echo "build.sh: done. Image ${LOCAL_TAG} ready for runner consumption."
echo
echo "Verify:   docker image inspect ${LOCAL_TAG} --format '{{.Architecture}}'"
echo "Cleanup:  docker rmi oracle/database:19.3.0-ee  # keeps recon-gen/oracle-19c:local"
