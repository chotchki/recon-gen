# TLS setup — Cloudflare + Let's Encrypt via DNS-01

> Phase DC operator runbook. Locked design: [`docs/audits/dc_0_https_spike.md`](../audits/dc_0_https_spike.md). Runner code: [`src/recon_gen/_dev/tls/`](../../src/recon_gen/_dev/tls/). This guide gets a fresh clone from "no certs" to "https on a real hostname with no browser warning" in ~10 min, one-time setup per environment.

## What this builds

Real Let's Encrypt certs for the 2 DEV DNS names under `hotchkiss.io`, minted + renewed automatically by `./run_tests.sh` (no per-machine CA install, no browser warnings). The runner manages the DNS records via Cloudflare's API; LE validates via DNS-01 TXT records under the same zone.

| Hostname | A → | Purpose |
|---|---|---|
| `localdev.recon-gen.hotchkiss.io` | `127.0.0.1` (static) | Operator's Mac browser → local uvicorn over loopback |
| `dev.recon-gen.hotchkiss.io` | Operator's Mac public IP (auto-discovered) | stable public-facing dev SAN (was the QuickSight-inbound path; QS removed in v16, kept as a cert SAN) |

One DEV cert covering both SANs. The dynamic `dev.*` A record reconciles via the cloudflare_trace pattern (`GET https://1.1.1.1/cdn-cgi/trace`) on every chain run — public IP changes update Cloudflare automatically.

> **CI uses self-signed certs, NOT this Cloudflare coordinator (EA.4).** The GitHub-hosted `ubuntu-latest` runner has no DNS control, so CI mints a *self-signed* cert for the `localci.*` / `ci.*` SANs via `RECON_GEN_TLS_SELF_SIGNED` (no Cloudflare token, no ACME, no repo secret), appends it to certifi for httpx trust, and maps the SANs to loopback with `/etc/hosts`. That flow lives entirely in `.github/workflows/ci.yml`'s `test-everything` job — Steps 1–5 below are the DEV path only and don't apply to CI. (Pre-EA.4, CI ran on a self-hosted WSL2 box that DID use this coordinator with a `ci`-env ACME cert; that box is decommissioned.)

## Prerequisites

- Cloudflare account managing the `hotchkiss.io` zone.
- Personal email for the Let's Encrypt ACME account (used for rate-limit / abuse notifications only).

(CI needs none of this — it's self-signed and self-contained; see the callout above and Step 5.)

## Step 1 — Mint the Cloudflare API token (one-time)

1. Go to https://dash.cloudflare.com/profile/api-tokens.
2. **Create Token** → **Custom token**.
3. Token name: `recon-gen TLS coordinator`.
4. Permissions: **Zone** → **DNS** → **Edit**.
5. Zone Resources: **Include** → **Specific zone** → `hotchkiss.io`.
6. TTL: leave open (we revoke via the dashboard if compromised).
7. **Continue to summary** → **Create Token** → copy the token (shown once).

The token grants exactly two operations on the `hotchkiss.io` zone: list/edit A records (for the 4 managed names) and list/create/delete TXT records (for ACME DNS-01 challenges). It cannot edit any other zone or touch your account-level settings.

## Step 2 — Local dev paste

Stash the token in `run/secrets.env` (the convention `[[feedback_no_credential_friction]]` follows — never look it up twice):

```bash
echo 'RECON_GEN_CLOUDFLARE_TOKEN=<token-from-step-1>' >> run/secrets.env
chmod 600 run/secrets.env
```

`run/` is gitignored in its entirety, so the file never reaches the remote. Source it before invoking `./run_tests.sh`:

```bash
# Option A — shell hook (add to ~/.zshrc or ~/.bashrc):
[ -f run/secrets.env ] && set -a && . run/secrets.env && set +a

# Option B — direnv (.envrc in repo root, also gitignored):
dotenv run/secrets.env
```

Same file holds future secrets (DD's `RECON_GEN_OIDC_CLIENT_SECRET` etc.) — one secret-file convention.

## Step 3 — Cfg block

Add the `app2.tls` block to your `run/config.<dialect>.yaml`:

```yaml
app2:
  tls:
    cert_path: /Users/<you>/.local/share/recon-gen/tls/dev/cert.pem
    key_path:  /Users/<you>/.local/share/recon-gen/tls/dev/key.pem
    account_email: you@example.com
    env: dev    # 'ci' selects the CI SAN pair (used by the self-signed CI path, not this dev coordinator)
```

`cert_path` and `key_path` are where the coordinator writes the freshly-minted PEMs. The runner's storage module owns the parent dir creation + atomic write; you don't pre-create the path. The XDG convention `~/.local/share/recon-gen/tls/<env>/` is recommended — pick anything writable.

`account_email` is the ACME registration identity (rate-limit + abuse contact for Let's Encrypt only — no marketing).

`env: dev` selects the SAN pair `(localdev.recon-gen.hotchkiss.io, dev.recon-gen.hotchkiss.io)`. `env: ci` selects `(localci..., ci...)`. The two envs maintain independent certs.

## Step 4 — First run

```bash
./run_tests.sh up_to=app2
```

On first run with the cfg block set, the runner:
1. Discovers the `hotchkiss.io` zone ID via Cloudflare API (cached at `~/.local/share/recon-gen/tls/zone-id.txt` after).
2. Reconciles the 2 A records for this env (creates `localdev.*` → `127.0.0.1` if absent, PATCHes `dev.*` → your current public IP if changed).
3. Discovers your public IP via `https://1.1.1.1/cdn-cgi/trace`.
4. Mints an ACME account (cached at `~/.local/share/recon-gen/tls/account-key.pem`).
5. Runs DNS-01 challenges against Let's Encrypt — plants TXT records, waits for propagation, finalizes the cert.
6. Writes `cert.pem` + `key.pem` to your cfg paths.

Expected first-run wall time: ~30s (LE finalize + cert download). Subsequent runs no-op until the cert is within 30d of expiry (~60d gap between renewals on a 90d LE cert).

Open `https://localdev.recon-gen.hotchkiss.io:8765` in your browser — no warning, real LE chain, padlock icon.

## Step 5 — CI needs no TLS setup

CI is fully self-contained (EA.4). `.github/workflows/ci.yml`'s `test-everything` job sets `RECON_GEN_TLS_SELF_SIGNED=1` and mints a self-signed cert for the `localci.*` / `ci.*` SANs at job start — no Cloudflare token, no `TLS_ACME_EMAIL`, no repo secret, no ACME round-trip. It appends the cert to the runner's certifi bundle (httpx trusts certifi, not `SSL_CERT_FILE`) and maps the SANs to loopback via `/etc/hosts`; `RECON_GEN_BROWSER_INSECURE_TLS=1` tells WebKit to accept it. Nothing to configure — the `CLOUDFLARE_TOKEN` above is a local-dev concern only.

## Troubleshooting

**"runner: TLS pre-flight failed: RECON_GEN_CLOUDFLARE_TOKEN not set"** — your shell hasn't sourced `run/secrets.env`. Source it manually (`set -a; . run/secrets.env; set +a`) or fix your shell hook from Step 2.

**"runner: TLS pre-flight failed (RuntimeError): Cloudflare API 403"** — token scope wrong. Confirm Zone:DNS:Edit on `hotchkiss.io` specifically (not account-level), no extra restrictions.

**"runner: TLS pre-flight failed: rate limit"** — Let's Encrypt enforces 50 certs/week/registered domain. Use the staging directory to iterate by passing `acme_directory_url=https://acme-staging-v02.api.letsencrypt.org/directory` to `ensure_dev_env` (currently runner-internal; not yet exposed via cfg).

**Public IP changed but A record stale** — the runner reconciles on every `./run_tests.sh up_to=...` invocation that hits TLS_TOUCHING_LAYERS. If you ran only `up_to=db`, the reconciler didn't fire. Run `up_to=app2` (or higher) to refresh.

**Cert files exist but renewal not firing** — the renewal threshold is 30d of remaining validity. If the cert was minted with `env: ci` and you're now invoking with `env: dev`, the SAN list doesn't match and the coordinator will mint a fresh cert (not renew) — the previous cert stays as-is on disk and the new one overwrites only the configured `cert_path` / `key_path`.

## Token rotation

1. Cloudflare dashboard → API Tokens → revoke the old token.
2. Mint a new one (same scope, same zone).
3. Update `run/secrets.env` (locally). No GitHub secret to update — CI is self-signed and doesn't use `CLOUDFLARE_TOKEN`.
4. Run `./run_tests.sh up_to=app2` once to confirm the new token works (the coordinator re-reads on every fire).

The zone ID cache + ACME account key both survive token rotation — no need to clear `~/.local/share/recon-gen/tls/`.

## Out of scope

Downstream `recon-gen.exe` operators (people who pip-install the wheel and run `recon-gen studio` against their own deployments) own their cert lifecycle externally. They set `cfg.app2.tls.cert_path` + `cfg.app2.tls.key_path` (+ optionally `account_email` if they want the same coordinator) and use certbot / acme.sh / their CDN's cert manager — whatever fits their infra. The `_dev/tls/` module is excluded from the published wheel; the `acme` / `cryptography` / `dnspython` / `requests` deps live under the `[dev]` extra only.
