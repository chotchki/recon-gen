# Oracle Database 19c — local-build container image

Builds an Oracle 19c container image we control. Production parity with AWS
RDS Oracle SE2 19c. Both arm64 (Apple Silicon dev Macs) and amd64 (the WSL2
self-hosted CI runner) are supported — the build script picks by host arch.

## Why we build this ourselves

Oracle does not redistribute their database binary, so there is no public
"good" 19c image on Docker Hub. The two prior options were both dead ends:

- `gvenzl/oracle-free:23-faststart` — multi-arch, well-maintained, but it's
  Oracle 23ai not 19c. Mismatches AWS RDS Oracle SE2 19c production.
- `doctorkirk/oracle-19c` — 19c, but amd64-only, last published 2 years
  ago, and under QEMU emulation on Apple Silicon it hangs mid-init at
  "10% complete / Copying database files."

The maintained source for the build recipe is Oracle's own
[oracle/docker-images](https://github.com/oracle/docker-images) repo. We
shallow-clone it at a pinned SHA inside `build.sh`, drop in the binary
zip the operator downloaded from Oracle's portal, and run their
`buildContainerImage.sh`. Output is tagged `recon-gen/oracle-19c:local`
for the runner to adopt.

## One-time setup (per developer machine)

You need a free [Oracle account](https://www.oracle.com/) to download the
database binary. Sign in, then pull the right zip for your host:

| Host                           | Zip                                | Source                                                                                          |
|---|---|---|
| Apple Silicon Mac (arm64)      | `LINUX.ARM64_1919000_db_home.zip`  | [Oracle 19c LINUX ARM (aarch64)](https://www.oracle.com/database/technologies/oracle19c-linux-downloads.html) |
| WSL2 / Linux / Intel Mac (amd64) | `LINUX.X64_193000_db_home.zip`     | [Oracle 19c Linux x86-64](https://www.oracle.com/database/technologies/oracle19c-linux-downloads.html)        |

Drop the zip in this directory (`tools/oracle-19c/`). Both `.gitignore`d.

### Download via curl (preferred — avoids Safari re-archiving)

Safari and Finder's Archive Utility wrap downloaded zips in an outer
`LINUX/` directory plus `__MACOSX/` resource-fork metadata, which breaks
the Dockerfile's `COPY` + `runInstaller` path. Use `curl` (or download via
Chrome with "Show in Finder" → drag raw file) to get Oracle's bytes
verbatim.

Oracle's download links are session-cookie gated, so the cleanest pattern
is the cookie-jar dance:

```bash
# 1. Log in once at https://login.oracle.com → click the download link in
#    the browser. The browser fetches a short-lived signed S3 URL.
# 2. In Chrome/Safari DevTools → Network tab, find the in-flight request
#    to download.oracle.com or adwc.objectstorage.<region>.oci.customer-oci.com,
#    right-click → "Copy as cURL". Paste into a terminal — that command
#    embeds all the auth headers you need:

curl 'https://download.oracle.com/.../LINUX.ARM64_1919000_db_home.zip' \
     -H 'Cookie: <whatever-DevTools-copied>' \
     -o tools/oracle-19c/LINUX.ARM64_1919000_db_home.zip

# 3. Repeat for the amd64 zip on the WSL2 runner.
```

### Verify the zip is clean (no Safari wrap)

Before running `build.sh`, sanity-check that the top entry is `runInstaller`
and there's no `__MACOSX/` shadow tree:

```bash
unzip -l tools/oracle-19c/LINUX.ARM64_1919000_db_home.zip | head -5
# Expected: top-level entries like runInstaller, install/, network/, ...
# BAD:      LINUX/runInstaller  +  __MACOSX/._LINUX  (re-archived; redownload)

unzip -l tools/oracle-19c/LINUX.ARM64_1919000_db_home.zip \
    | grep -c __MACOSX
# Expected: 0
```

## Build

```bash
./tools/oracle-19c/build.sh
```

Takes ~10-15 min on Apple Silicon, ~6-8 min on the WSL2 box. The script:

1. Clones Oracle's `oracle/docker-images` at the pinned SHA.
2. Runs their `buildContainerImage.sh -v 19.3.0 -e -i` against the dropped-in
   zip → tags as `oracle/database:19.3.0-ee`, retagged `recon-gen/oracle-19c:local`.
3. **Pre-initializes the DB into the image** (one-time, ~3-4 min): boots a
   throwaway container, waits for `DATABASE IS READY TO USE`, runs
   `SHUTDOWN IMMEDIATE` via sqlplus to quiesce data files, then
   `docker commit`s the writable layer back onto the same tag.

After step 3, every subsequent container boot finds existing data files
under `/opt/oracle/oradata/$ORACLE_SID` and skips DBCA — cold-start drops
from ~240s to ~30s. The runner's persistent-named-container path still
caches further runs at ~10s.

## Runner consumption

The runner's `_start_fresh_oracle_container` (in `src/recon_gen/_dev/runner.py`)
prefers `recon-gen/oracle-19c:local` when present and falls back to
`gvenzl/oracle-free:23-faststart` (Oracle 23ai) with a one-line warning
otherwise. Run `build.sh` once per machine and the runner picks it up; no
config change needed.

Override the image with `RECON_GEN_ORACLE_IMAGE=<image:tag>` if you want
to test against a different build (e.g., a checkpointed 19.20 PSU).

## Upstream SHA refresh

When you want the latest Oracle Dockerfile fixes:

```bash
curl -s https://api.github.com/repos/oracle/docker-images/commits/main | jq -r .sha
```

Update `UPSTREAM_SHA` in `build.sh`. Then re-run the build to validate.

## What's not here

- The Oracle binary zips themselves (license: not redistributable).
- A multi-arch manifest. Each `build.sh` run produces a single-arch image
  because each zip IS the architecture-specific bits. The runner only
  needs the host's arch, so single-arch tagged `:local` is the simplest
  shape that works.
- A PSU/RU patch step. The arm64 zip is already 19.19; the amd64 zip is
  19.3 base. If production parity needs to converge on a specific RU,
  add a layered Dockerfile that applies the patch — out of scope for
  the initial build.
