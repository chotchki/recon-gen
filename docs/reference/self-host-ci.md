# Self-hosted CI runner — operational guide

Phase BY landed a self-hosted GitHub Actions runner that absorbs the
slow CI / E2E jobs. This page is the operator runbook: what the
runner is, when to touch it, and how to recover when it's down.

## What's on it

- **Host:** Windows 11 box, AMD 5800X3D (8c/16t, 96MB L3), 64GB RAM
- **Network:** behind home NAT, on-LAN with operator
- **Runtime:** WSL2 Ubuntu, `docker.io` daemon inside WSL2 (not Docker Desktop)
- **Runner identity:** `recon-gen-ci-local` registered to `chotchki/recon-gen`
- **Runner labels:** `[self-hosted, Linux, X64, recon-gen-ci-local]`
- **Service mode:** systemd unit `actions.runner.chotchki-recon-gen.recon-gen-ci-local.service` auto-starts on WSL2 boot
- **Power policy:** Windows host stays awake when plugged in; `powercfg /h off` disabled hibernate. **NOT 24/7** — operator powers the box up when developing.

## What runs on it

| Workflow | Job | Why self-hosted |
|---|---|---|
| `ci.yml` | `test` | Slowest unit-tier job. 68min → 8m49s (7.7×) under BY.1 + BY.1.1 (`-n auto` on 16 threads). |
| `e2e.yml` | `e2e-pg-browser` | Second-slowest. Playwright WebKit + matview-heavy deploy chain benefit from the bigger box. `-n 4` on a 2-vCPU GHA runner used to flake; the 5800X3D handles it. |

## What does NOT run on it (BY.3 secret-isolation policy)

**All `release.yml` jobs stay on `ubuntu-latest`.** This is enforced by
`tests/unit/test_release_yml_secret_isolation.py`. The covered surface:
PyPI tokens (`publish-testpypi` / `publish-pypi`), AWS OIDC role
assumption (`release-e2e` / `e2e-against-testpypi`), `GITHUB_TOKEN`
write scope (`github-release`). See the lint module's docstring + the
header block of release.yml for full rationale.

CI's downstream parallel jobs (`integration-pg`, `integration-oracle`,
`e2e-sqlite`, `docs-portable-install`, `coverage`, `coverage-badge`)
also stay on `ubuntu-latest` — they run in parallel after `needs: test`
clears, and serializing them on a single self-hosted runner would
net-negative. Same for `e2e-pg-api`, `e2e-oracle-api`, and the
`cleanup-*` jobs in e2e.yml.

## Procedures

### Restarting the runner service

Inside WSL2:

```bash
cd ~/actions-runner
sudo ./svc.sh status            # check current state
sudo ./svc.sh stop              # graceful stop
sudo ./svc.sh start             # start
sudo systemctl restart actions.runner.chotchki-recon-gen.recon-gen-ci-local.service
```

After a Windows reboot, WSL2 + the runner systemd unit should come
back automatically (per BY.4 step 1). Verify with:

```bash
wsl -d Ubuntu-24.04 -e systemctl is-active actions.runner.chotchki-recon-gen.recon-gen-ci-local.service
# expected: active
```

### Updating the runner binary

GitHub auto-updates the runner agent on a rolling basis. To force-update:

```bash
cd ~/actions-runner
sudo ./svc.sh stop
./config.sh remove --token <removal-token-from-Settings-Actions-Runners-page>
# Download latest from https://github.com/chotchki/recon-gen/settings/actions/runners/new
./config.sh --url https://github.com/chotchki/recon-gen \
            --token <fresh-registration-token> \
            --labels self-hosted,recon-gen-ci-local \
            --unattended --replace
sudo ./svc.sh install $USER
sudo ./svc.sh start
```

### Disk cleanup (BY.4 #3)

The runner accumulates two kinds of cruft over time:

- **Docker images + volumes** — GHA service containers (postgres:17,
  gvenzl/oracle-free:23-faststart) get pulled per-job-type. The
  postgres image is ~150MB, oracle-free is ~2GB. Without pruning these
  accumulate as GHA rotates versions.
- **Runner workspace + tool cache** — `~/actions-runner/_work/_tool/`
  caches per-job Python installs from `actions/setup-python` (~150MB
  per Python version), `~/actions-runner/_work/_actions/` caches
  downloaded action source tarballs.

Install once via crontab inside WSL2:

```bash
crontab -e
# Add these lines (uses runner's user crontab — adjust paths if installed elsewhere):

# Daily 04:00 local — prune docker images older than 7 days (168h)
0 4 * * * /usr/bin/docker image prune -af --filter "until=168h" > /tmp/runner-cleanup.log 2>&1

# Daily 04:05 — prune anonymous docker volumes (keeps labelled volumes)
5 4 * * * /usr/bin/docker volume prune -f >> /tmp/runner-cleanup.log 2>&1

# Daily 04:10 — prune _work/_tool/ Python installs unused for 14+ days
# (actions/setup-python re-downloads if cache miss; cheap)
10 4 * * * find /home/recon-gen/actions-runner/_work/_tool -mindepth 2 -maxdepth 3 -type d -mtime +14 -exec rm -rf {} + >> /tmp/runner-cleanup.log 2>&1

# Daily 04:15 — prune _work/_actions/ cached action tarballs unused for 14+ days
15 4 * * * find /home/recon-gen/actions-runner/_work/_actions -mindepth 3 -maxdepth 4 -type d -mtime +14 -exec rm -rf {} + >> /tmp/runner-cleanup.log 2>&1
```

Check `/tmp/runner-cleanup.log` weekly. If disk pressure grows
faster than the cron handles, lower the `until=168h` window or
trigger a manual `docker system prune -af` (more aggressive — drops
the build cache too; next first job pays a re-pull cost).

### Health check — operator-driven (BY.4 #4)

The runner is **not 24/7**. Operator powers the Windows host up when
developing; CI/E2E queue behind it until the host is online. No
automated heartbeat / paging is wired up — explicitly scoped out of BY.4
on the rationale that "machine isn't always on" is a known property,
not an incident.

Quick liveness check from the operator's Mac:

```bash
gh api repos/chotchki/recon-gen/actions/runners --jq \
  '.runners[] | select(.name == "recon-gen-ci-local") | {status, busy, labels: [.labels[].name]}'
# expected on a healthy box:
# {"status": "online", "busy": false, "labels": ["self-hosted", "Linux", "X64", "recon-gen-ci-local"]}
```

`status: offline` while expecting `online` → power up the box / check
WSL2 / restart the systemd unit (see "Restarting the runner service").

### Emergency fallback — runner is down, you need CI to ship (BY.4 #5)

If the WSL2 runner is unavailable (host off, WSL2 wedged, runner
binary crashed) AND you need a CI run to land *right now*, temporarily
flip the affected jobs back to `ubuntu-latest`. Procedure:

1. **Identify the affected jobs.** Default targets to flip:
   - `.github/workflows/ci.yml::test` — currently `runs-on: [self-hosted, recon-gen-ci-local]`
   - `.github/workflows/e2e.yml::e2e-pg-browser` — currently `runs-on: [self-hosted, recon-gen-ci-local]`

2. **Make the flip.** For each affected job:
   - Change `runs-on: [self-hosted, recon-gen-ci-local]` → `runs-on: ubuntu-latest`
   - Restore `playwright install` → `playwright install --with-deps webkit` (the WSL2 runner has WebKit system libs pre-installed; `ubuntu-latest` doesn't)
   - For `ci.yml::test`: leave `pytest -n auto` as-is. Ubuntu-latest is 2-vCPU; `-n auto` becomes `-n 2`. Runs slower but works.
   - For `e2e.yml::e2e-pg-browser`: change `-n 4` → `-n 2`. The 4-worker Playwright contention will flake on 2-vCPU GHA runners.

3. **Commit + push.** Suggested message:
   ```
   ops: temporarily revert <job> to ubuntu-latest (self-hosted runner down)
   ```
   Mark in the message: `Revert when runner recovers.`

4. **Restore once the runner is back.** Inverse of step 2 — flip
   `runs-on:` back to `[self-hosted, recon-gen-ci-local]`, drop
   `--with-deps`, restore `-n auto` / `-n 4`. Verify with a
   `workflow_dispatch` run before declaring restored.

5. **If the runner has been down for >24 hours and BY.3-violating
   pressure is mounting** (e.g., a release is blocked), consider
   re-registering a fresh runner on a different machine — same
   labels, same one-time bootstrap from BY.4 disk-cleanup section.
   Don't shortcut by putting `release.yml` jobs on the self-hosted
   runner — that's a BY.3 violation and the lint will block.

## Known gotchas

- **Concurrency cascade in e2e.yml.** Multiple pushes to main in quick
  succession queue against the `e2e-pg` concurrency group with
  `cancel-in-progress: false`. GHA's "single pending" rule cancels
  older queued runs when newer ones arrive. If you're verifying a
  change, prefer one push + one `workflow_dispatch` over rapid-fire
  pushes; the dispatch doesn't trigger the chain cascade.

- **Self-hosted job logs lag in the GHA UI.** The runner posts logs
  in batches; per-step output may appear seconds-to-a-minute after
  the step actually fires. `gh run watch <id>` polls the API directly
  and is more responsive.

- **`runner_name: null` in `gh run view --json`.** GitHub's job-level
  API doesn't always populate `runner_name` even after the job
  completes. To verify a job ran on the self-hosted runner, check
  `gh api repos/.../actions/runners --jq '.runners[] | .busy'`
  during the job, or inspect the step log header which contains the
  runner's home dir path (`/home/recon-gen/actions-runner/...`).

- **`--with-deps` requires sudo.** The runner's NOPASSWD allowlist
  intentionally doesn't permit arbitrary apt-get. `playwright install
  --with-deps webkit` will fail. The WSL2 runner has WebKit deps
  pre-installed (libsoup3, libwebkitgtk-6.0, libatomic1, etc.) so
  per-job `playwright install webkit` works without `--with-deps`.
  If a new Playwright version introduces a new system dep, manually
  bootstrap it via `sudo .venv/bin/playwright install-deps webkit`
  during a maintenance window.
