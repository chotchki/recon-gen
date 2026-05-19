# Mac mini self-hosted demo host

End-to-end runbook for the Phase AE Cloudflare-routed public demo at
`recon-gen-spec.hotchkiss.io` (dashboards) and
`recon-gen-sasquatch.hotchkiss.io` (studio --demo-mode).

This document is operator-private — it lives under `docs/operations/`
which is excluded from the public mkdocs build at
`src/recon_gen/docs/`. The instructions reference user accounts, file
paths, and Cloudflare account names that are specific to one operator;
nothing public should link in.

## Architecture at a glance

```
┌────────────────────────────────────────────────────────────────────┐
│                     Cloudflare edge (public)                        │
│  recon-gen-spec.hotchkiss.io     recon-gen-sasquatch.hotchkiss.io  │
│           │                                  │                       │
│           │           Cloudflare Tunnel      │                       │
└───────────┼──────────────────────────────────┼───────────────────────┘
            │                                  │
            │     cloudflared (launchd)        │
            │                                  │
            ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Mac mini · user: recon-demo · sandboxed via sandbox-exec           │
│                                                                      │
│   io.hotchkiss.recon-demo.spec.plist   io.hotchkiss.recon-demo.     │
│   sandbox-exec -f spec.sb --                .sasquatch.plist        │
│   recon-gen dashboards                  launch-sasquatch.sh         │
│   --port 8401 --no-docs                 (mktemp + sandbox-exec       │
│   (binds 127.0.0.1:8401)                 + recon-gen studio         │
│                                          --demo-mode --port 8402)    │
│         │                                       │                    │
│         └────► spec_example/current.sqlite3     └────► sasquatch_pr/ │
│                                                       current.sqlite3│
│                                                                      │
│   io.hotchkiss.recon-demo.refresh.plist (StartCalendarInterval 03:00)│
│     refresh-demos.sh: pip install --upgrade → schema + data +       │
│     refresh + audit verify → atomic mv next → current → kickstart   │
│                                                                      │
│   io.hotchkiss.recon-demo.tunnel.plist   io.hotchkiss.recon-demo.   │
│     cloudflared tunnel run recon-demo       .runner.plist           │
│                                              (self-hosted Actions    │
│                                              runner, label           │
│                                              mac-mini-demo)          │
└──────────────────────────────────────────────────────────────────────┘
```

## One-time install (AE.1 → AE.8)

Run as your admin user unless noted "as recon-demo".

### 1. Create the recon-demo user

```bash
# As admin, in System Settings → Users & Groups:
#   - New Standard user "recon-demo"
#   - No GUI login (uncheck "Allow user to log in")
#   - Strong random password (you won't use it interactively)
#
# Or via sysadminctl:
sudo sysadminctl -addUser recon-demo -fullName "Recon Demo Host" \
    -password "$(openssl rand -base64 24)" -shell /bin/zsh \
    -home /Users/recon-demo
sudo dscl . -append /Groups/staff GroupMembership recon-demo
```

### 2. Set up the venv + install recon-gen

```bash
# Switch to recon-demo:
sudo -u recon-demo -i

# Inside recon-demo's shell:
cd ~
mkdir -p venv bin sandbox logs spec_example sasquatch_pr tunnel runner
python3.13 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install "recon-gen[deploy,demo,audit,serve]"
```

### 3. Install the sandbox profiles + launcher wrappers

From your dev clone of the repo (NOT inside recon-demo's account):

```bash
# Copy sandbox profiles
sudo cp deploy/sandbox/recon-demo-spec.sb /Users/recon-demo/sandbox/
sudo cp deploy/sandbox/recon-demo-sasquatch.sb /Users/recon-demo/sandbox/

# Copy launcher wrappers
sudo cp deploy/launchd/launch-sasquatch.sh /Users/recon-demo/bin/
sudo cp deploy/launchd/refresh-demos.sh /Users/recon-demo/bin/
sudo cp scripts/provision_demo_instance.sh /Users/recon-demo/bin/

# Permissions: scripts mode 0500, owned by recon-demo
sudo chown -R recon-demo:staff /Users/recon-demo/sandbox /Users/recon-demo/bin
sudo chmod 0500 /Users/recon-demo/bin/*.sh
sudo chmod 0400 /Users/recon-demo/sandbox/*.sb
```

### 4. Sandbox-exec sanity check

Before wiring launchd, confirm the profiles let the server boot and
deny what they should. As recon-demo:

```bash
# Substitute paths if you customized them.
sudo -u recon-demo /usr/bin/sandbox-exec \
    -D HOME=/Users/recon-demo \
    -D INSTANCE_DIR=/Users/recon-demo/spec_example \
    -D PORT=8401 \
    -D PYTHON=/Users/recon-demo/venv/bin/python3.13 \
    -f /Users/recon-demo/sandbox/recon-demo-spec.sb \
    -- /bin/sh

# Inside the sandboxed shell:
touch /Users/recon-demo/spec_example/l2.yaml      # MUST FAIL
touch /Users/recon-demo/spec_example/current.sqlite3.x  # MUST SUCCEED
exit
```

If either outcome is wrong, edit the `.sb` file and re-test.

### 5. Provision both SQLite instances

As recon-demo:

```bash
/Users/recon-demo/bin/provision_demo_instance.sh spec_example 8401
/Users/recon-demo/bin/provision_demo_instance.sh sasquatch_pr 8402
```

Each takes ~30s. The script prints the rebuilt db path + invokes audit
verify as a sanity probe; failure aborts before launchd loads the plist.

### 6. Install Cloudflare Tunnel + register

```bash
# As admin: install cloudflared (Apple Silicon)
brew install cloudflared

# As recon-demo: authenticate + create tunnel
sudo -u recon-demo cloudflared tunnel login    # opens browser
sudo -u recon-demo cloudflared tunnel create recon-demo
# → writes <tunnel-uuid>.json to /Users/recon-demo/.cloudflared/

# Copy + edit the ingress config
sudo -u recon-demo cp deploy/cloudflared/cloudflared.yml.example \
    /Users/recon-demo/.cloudflared/config.yml
sudo -u recon-demo vi /Users/recon-demo/.cloudflared/config.yml
# → replace <REPLACE-WITH-TUNNEL-UUID> with the UUID from credentials.json

# DNS routing (one command per hostname)
sudo -u recon-demo cloudflared tunnel route dns recon-demo \
    recon-gen-spec.hotchkiss.io
sudo -u recon-demo cloudflared tunnel route dns recon-demo \
    recon-gen-sasquatch.hotchkiss.io
```

### 7. Install + load the launchd services

```bash
sudo cp deploy/launchd/*.plist /Users/recon-demo/Library/LaunchAgents/
sudo chown recon-demo:staff /Users/recon-demo/Library/LaunchAgents/*.plist

# Load each (as recon-demo so launchctl picks up the user's GUI session)
for plist in /Users/recon-demo/Library/LaunchAgents/io.hotchkiss.recon-demo.*.plist; do
    sudo -u recon-demo launchctl load -w "$plist"
done
```

### 8. Install the GitHub Actions runner

```bash
sudo -u recon-demo bash -c '
    cd ~/runner
    # Download the latest macOS runner from
    # https://github.com/actions/runner/releases (arm64 for Apple Silicon)
    curl -L -o actions-runner.tar.gz https://github.com/.../actions-runner-osx-arm64-X.Y.Z.tar.gz
    tar xzf actions-runner.tar.gz
    # Configure with a single-repo PAT scoped to actions:write
    ./config.sh --url https://github.com/chotchki/recon-gen \
        --token <YOUR-RUNNER-REG-TOKEN> \
        --labels mac-mini-demo \
        --unattended
    ./svc.sh install   # launchd service
    ./svc.sh start
'
```

Confirm the runner shows up at
`https://github.com/chotchki/recon-gen/settings/actions/runners`
with label `mac-mini-demo` and status `Idle`.

## Operating the demo

### Health check

```bash
curl -sI https://recon-gen-spec.hotchkiss.io/dashboards/l1_dashboard/
curl -sI https://recon-gen-sasquatch.hotchkiss.io/dashboards/l1_dashboard/
# Both should return HTTP/2 200.
```

### View launchd service status

```bash
sudo -u recon-demo launchctl list | grep io.hotchkiss.recon-demo
```

Status column 1 is PID (or `-` if not running); column 2 is last exit
status (0 is healthy).

### Tail the logs

```bash
tail -F /Users/recon-demo/spec_example/logs/server.{out,err}.log
tail -F /Users/recon-demo/sasquatch_pr/logs/server.{out,err}.log
tail -F /Users/recon-demo/logs/refresh.{out,err}.log
tail -F /Users/recon-demo/logs/tunnel.{out,err}.log
```

### Force an immediate refresh

```bash
sudo -u recon-demo /Users/recon-demo/bin/refresh-demos.sh
```

Same script the nightly cron runs. Useful when you want to pick up a
freshly published wheel without waiting for 03:00 ET.

### Restart a single server

```bash
RECON_DEMO_UID=$(id -u recon-demo)
sudo -u recon-demo launchctl kickstart -k \
    "gui/$RECON_DEMO_UID/io.hotchkiss.recon-demo.spec"
```

### Pin to a specific recon-gen version

```bash
# As recon-demo, edit the refresh-demos.sh env or set per-shell:
sudo -u recon-demo bash -c '
    export RECON_GEN_PIN_VERSION=11.6.3
    /Users/recon-demo/bin/refresh-demos.sh
'
```

Holds the demo at 11.6.3 until the env var is cleared. The nightly
refresh job also honors `RECON_GEN_PIN_VERSION` when set in its
plist's `EnvironmentVariables` block (edit the plist + reload).

## Troubleshooting

### "demo URL returns 502 / 503"

cloudflared can't reach the origin. Order of investigation:

1. `sudo -u recon-demo launchctl list | grep io.hotchkiss.recon-demo` — is the per-instance server actually running?
2. `tail -F /Users/recon-demo/<instance>/logs/server.err.log` — what did the server log on its way down?
3. `curl http://127.0.0.1:8401/` (from the Mac mini itself) — does the loopback bind work?
4. `tail -F /Users/recon-demo/logs/tunnel.err.log` — cloudflared logs origin-connect failures here.

### "Refresh job aborts mid-build"

`refresh-demos.sh` uses `set -e` so any sub-step failure stops the
script. The most common causes:

- **pip install --upgrade times out** — TestPyPI / PyPI propagation delay or network blip. The previous wheel + previous SQLite db both stay in place; the server is unaffected.
- **schema apply fails on a new wheel's schema migration** — the migration changed in a way the existing cfg doesn't accept. Pin to the prior version via `RECON_GEN_PIN_VERSION` and investigate.
- **audit verify fails** — seed pipeline + matview refresh disagree on L1 invariants for the new wheel. Bug in the just-shipped wheel; pin to prior + open an issue.

### "Cloudflare Tunnel daemon won't start"

Check `/Users/recon-demo/logs/tunnel.err.log`. Common causes:

- credentials.json missing or wrong UUID in config.yml — re-run `cloudflared tunnel list` to get the right UUID.
- cloudflared binary path differs (Intel vs Apple Silicon). Edit `io.hotchkiss.recon-demo.tunnel.plist` and reload.
- Operator's Cloudflare account session expired — run `cloudflared tunnel login` again.

### "sandbox-exec: bad profile syntax"

The profile uses Apple's S-expression dialect; version-1 syntax errors
abort the launchd job at startup. Re-run the AE.4 sanity check (step 4
above) to surface the syntax error in plain terminal, not buried in
launchd's log.

### "Trainer-knob mutations stop working on sasquatch"

`launch-sasquatch.sh` allocates a fresh tmpdir per launchd start. If
the launchd job restarted mid-day (e.g. crash + KeepAlive respawn), the
previous tmpdir is left behind and unwritable from the new process.
This is expected; trainer-knob state is ephemeral by design. State
returns when the next `launchctl kickstart -k` cycles in a fresh tmpdir.

## Tear-down

Disable + remove the demo:

```bash
# Stop everything
for plist in /Users/recon-demo/Library/LaunchAgents/io.hotchkiss.recon-demo.*.plist; do
    sudo -u recon-demo launchctl unload -w "$plist"
done

# Remove plists (otherwise they reload at next boot)
sudo rm /Users/recon-demo/Library/LaunchAgents/io.hotchkiss.recon-demo.*.plist

# Tear down the tunnel (releases the Cloudflare-side DNS routes)
sudo -u recon-demo cloudflared tunnel delete recon-demo

# Remove the runner registration (from GitHub Settings → Actions → Runners,
# OR via the CLI from the runner dir):
sudo -u recon-demo bash -c 'cd ~/runner && ./svc.sh uninstall && ./config.sh remove'

# Delete the user (preserves home in case of forensics)
sudo sysadminctl -deleteUser recon-demo -keep
```

## Phase AE deferred / follow-on work

- **AE.2.b.chrome** — Demo-mode banner across the studio chrome + Deploy button hide. Route-level lockdown already prevents the mutation; the banner is friendlier UX so visitors know what they're looking at.
- **AE.9** — Smoke test `tests/operations/test_demo_host_smoke.py` (gated on `RECON_DEMO_HOST=1`) for an opt-in cron probe of both URLs.
- **AE.11** — Once AE.1 + AE.7 + AE.9 are all green, fold AE into the PLAN_ARCHIVE.md sweep + add "AE — Mac mini self-hosted Cloudflare-routed demo (SQLite, daily refresh, sandbox-exec)" to the Phase history one-liner block.
