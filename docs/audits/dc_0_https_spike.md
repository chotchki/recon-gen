# DC.0 — HTTPS cert provisioning spike (revised)

**Status:** draft for decision · Date: 2026-06-14 · Prompted by: DC.1 landed the uvicorn TLS wiring (`cfg.app2.tls.{cert_path,key_path}` → `ssl_certfile`/`ssl_keyfile`) but punted *where the PEM files come from* to "operator concern". Shared-deployment operators don't have a default path, and Claude can't drive an end-to-end "fresh clone → HTTPS on a real hostname" without one. This revision incorporates the operator's redirect (2026-06-14 morning): drop the self-signed leg, go DNS + Let's Encrypt for *all* test / local-dev / CI environments — and explicitly scope us out of providing a cert mechanism for downstream `recon-gen.exe` operators (they expose `cfg.app2.tls.{cert_path,key_path}` and bring their own PEMs).

> Supersedes the prior DC.0 entry-spike (the cfg-shape locks — optional `app2.tls:` block, absent ⇒ HTTP, in-process uvicorn termination, App2-scope-only — carry forward unchanged; this revision answers the cert-source question that was explicitly out-of-scope before).

> Prior revision (mkcert + Caddy hybrid) is replaced wholesale by the DNS + Let's Encrypt path locked below. Kept in git history for the operator's redirect rationale.

## Why this exists

DC.1 makes uvicorn capable of serving HTTPS *if* given a cert + key. It does not produce certs. Today's options for an operator (and for Claude driving on their behalf):

- **Local dev on `localhost`** — browser warnings every restart, no way to test cookie/`Secure` paths cleanly.
- **CI on the WSL2 self-hosted runner** — same as local: no public DNS name, no browser-trusted cert, no honest way to drive the DD OIDC `Secure`-cookie flow.
- **Downstream `recon-gen.exe` operator** — out of scope for the coordinator; they own their cert/key paths via `cfg.app2.tls.*`. We don't ship a cert tool for them.

Our test / local-dev / CI environments are exactly the postures where ANY browser warning blocks the DD work — `Secure` cookies require browser-trusted HTTPS, and you can't honestly validate the OIDC login flow with click-through warnings every restart. We need real certs in those environments.

## The redirect — DNS + Let's Encrypt via Cloudflare

The prior revision recommended mkcert (local-CA self-signed) for local-dev + Caddy ACME for shared deploys. Operator pushback (2026-06-14): use real Let's Encrypt certs everywhere via DNS-01 challenge against `hotchkiss.io`, modeled on the [hotchkiss-io coordinator](https://github.com/chotchki/hotchkiss-io/tree/main/src/coordinator) pattern they already run for their personal site. Two domains, both `A → 127.0.0.1`:

- `localdev.recon-gen.hotchkiss.io` — operator's Mac
- `ci.recon-gen.hotchkiss.io` — WSL2 self-hosted CI runner's Windows machine

Both A records point at loopback. The cert validates because DNS-01 doesn't care that the target IP is local — it only requires that the validator can post `_acme-challenge.<domain>` TXT records via the Cloudflare API and that Let's Encrypt sees them. The browser hitting `https://localdev.recon-gen.hotchkiss.io:8765` resolves to 127.0.0.1 on the operator's machine, connects to the local uvicorn, and presents a real Let's Encrypt cert that every browser trusts out of the box.

**Why this beats the prior recommendation:**

- **Zero per-machine trust friction.** mkcert needs `mkcert -install` on every dev machine + every CI runner; Let's Encrypt is browser-trusted everywhere by default.
- **Symmetric local + CI shape.** Same DNS + ACME flow on the operator's Mac and the WSL2 runner; nothing branches on "local vs CI".
- **Removes implicit hotchkiss.io dep — by making it explicit.** Today the test runner already assumes `hotchkiss.io` ports forward to the dev machine ([[project_cb10_qs_to_docker_pg_constraints]]). The redirect doesn't add a dep; it formalizes the one we already have, with explicit DNS records under operator control.
- **Proven shape.** The hotchkiss-io coordinator (Rust, ACME + Cloudflare DNS, sqlite cert storage, ~600 LoC end-to-end) has shipped against this pattern for years; the architectural risk is zero.
- **Honest cookies.** `SameSite=Strict; Secure` cookies for OIDC work without `Insecure HTTPS` browser-flag dances; DD.2's middleware can rely on the browser treating the connection as Secure.

**Tradeoffs we accept:**

- One-time Cloudflare API token to provision (Zone:DNS:Edit on `hotchkiss.io`).
- Renewal requires network reachability to Cloudflare + Let's Encrypt; offline dev runs on existing cached certs until expiry.
- Two DNS records (one per environment) — one-time setup, never touched again unless we add a third environment.

## Comparison matrix (revised)

The mkcert / Caddy / nginx / AWS ACM options remain on the table for completeness but the operator's redirect rules them out for our test/local-dev/CI scope. Kept in the table to show why DNS+LE wins for our specific shape.

| Axis | mkcert | Caddy | nginx+certbot | DNS+LE (this spike) | AWS ACM |
|---|---|---|---|---|---|
| Browser-trusted out of box | NO (CA install per machine) | YES (after public-DNS bootstrap) | YES | **YES (no per-machine install)** | YES |
| Local-dev fit (no public IP) | YES (local-CA) | NO (ACME HTTP-01 needs public 80) | NO | **YES (DNS-01 works against loopback)** | NO |
| CI fit (self-hosted runner) | needs install per runner | needs public IP / HTTP-01 forwarding | same | **YES (DNS-01 is identical to local-dev)** | NO |
| Cert renewal automation | manual | auto (background) | cron + reload | **auto (every `./run_tests.sh up`)** | auto |
| Cloudflare API dep | NO | NO | NO | **YES (one scoped token)** | NO |
| Operator bootstrap | 2 (install, mint) | 1 (Caddyfile) | 5+ | **3 one-time (token, 2 DNS A records, paste token env)** | N/A bare host |
| Codebase impact | ~80 LoC wrapper | ~120 LoC + Caddyfile | ~200 LoC + certbot | **~400 LoC + `acme` + `cryptography`** | ~150 LoC + boto3 |
| Claude end-to-end driveable | YES | YES | NO (certbot interactive) | **YES (idempotent CLI verb)** | NO (AWS console) |
| Shipping cert to downstream `recon-gen.exe` users | N/A | N/A | N/A | **Explicitly out-of-scope (user supplies cert via `cfg.app2.tls.*`)** | N/A |

## Recommendation — locked

**Build a `recon-gen tls` verb that handles ACME DNS-01 against Let's Encrypt via the Cloudflare API.** Scope it to our test/local-dev/CI use only — `localdev.recon-gen.hotchkiss.io` and `ci.recon-gen.hotchkiss.io`. Downstream `recon-gen.exe` end-users continue to supply their own cert + key paths via `cfg.app2.tls.{cert_path,key_path}` (no change to the cfg shape, no built-in coordinator for them).

This matches the operator's DX expectations: same flow local + CI; no per-machine CA install; real Let's Encrypt certs; auto-renew; one Cloudflare API token in env.

## Implementation plan (revised)

**DC.2 — `recon-gen tls` verb + auto-renew on `up`**

1. Add `src/recon_gen/cli/tls.py` with three subcommands:
   - `recon-gen tls ensure --host <fqdn>` — idempotent. Loads cert from `cfg.app2.tls.cert_path` if present; if absent or `not_after < now + 30d`, runs the ACME DNS-01 flow against Cloudflare + writes new PEMs to `cfg.app2.tls.{cert_path,key_path}`. Logs "cert valid until <date>" or "renewed via ACME (lasted Nd)".
   - `recon-gen tls status` — prints cert SAN list + `not_after` + days-remaining. Exits non-zero when `<14d`.
   - `recon-gen tls revoke --host <fqdn>` — calls Let's Encrypt's revoke endpoint for the cert; used during cleanup or when a key is suspected compromised.
2. ACME state machine via the `acme` library (IETF maintainer, `pip install acme`). Account key stored at `run/tls/account.key` (gitignored — already covered by `run/` rule). Account email read from `cfg.audit.signing.signer_name`'s email (one identity for the whole tool) or `cfg.app2.acme.account_email` if we want to split (default to single field).
3. Cloudflare API client — direct `requests` calls against `api.cloudflare.com/client/v4/zones/{zone_id}/dns_records`. Token read from env `RECON_GEN_CLOUDFLARE_TOKEN` (per the no-credential-friction rule, also accept a `cfg.cloudflare.token_env` indirection so the env var name lives in cfg). Zone ID derived once from `hotchkiss.io` lookup; cached at `run/tls/zone-id.txt`.
4. DNS-01 flow:
   1. ACME `new-order` for `<fqdn>`.
   2. Get authorization → DNS-01 challenge token.
   3. POST `_acme-challenge.<fqdn>` TXT record via Cloudflare API.
   4. Poll via `hickory`-style DNS resolver (use `dnspython` since we're Python) against `1.1.1.1` until TXT propagates (typical: <15s).
   5. Tell ACME the challenge is ready; poll order until VALID.
   6. Generate keypair via `cryptography`, build CSR, finalize order, download PEM chain.
   7. Write `cert.pem` + `key.pem` to `cfg.app2.tls.{cert_path,key_path}`.
   8. DELETE the TXT record (cleanup; idempotent on retry).
5. Wire `recon-gen tls ensure --host <fqdn>` into `src/recon_gen/_dev/runner.py`'s chain prelude: when `cfg.app2.tls.cert_path` is configured AND the file is missing or `<30d`, run the ensure. Skip when `cfg.app2.tls.*` is unset (downstream operators' tool runs).
6. Add `tests/unit/test_cli_tls.py` covering: account-key generation, cert-expiry computation, DNS-01 challenge response shape, missing-token error path. Mock `requests` for Cloudflare; mock the ACME client; real PEM parsing via `cryptography`.

**DC.3 — runner integration + phase exit**

1. The runner's `cmd_up` (`up local|aws|all`) gets a new prereq step: if `cfg.app2.tls.cert_path` is set and any of `localdev.recon-gen.hotchkiss.io` / `ci.recon-gen.hotchkiss.io` need renewal, run `recon-gen tls ensure --host <fqdn>`. Failure ⇒ `EXIT_NEEDS_OPERATOR` with the actionable message (token missing, Cloudflare 4xx, ACME rate-limit).
2. Document the **one-time operator setup** in `docs/reference/local-dev.md`:
   - Cloudflare API token creation (Zone:DNS:Edit, restricted to the `hotchkiss.io` zone) — paste step-by-step.
   - DNS records: `localdev.recon-gen.hotchkiss.io A 127.0.0.1` and `ci.recon-gen.hotchkiss.io A 127.0.0.1` (one Cloudflare dashboard click each).
   - Env: `RECON_GEN_CLOUDFLARE_TOKEN=<token>` in shell profile (or a `direnv` `.envrc` if the operator prefers).
   - `cfg.app2.tls.cert_path: run/tls/cert.pem` + `cfg.app2.tls.key_path: run/tls/key.pem` (templated into `run/base.yaml`'s example).
3. Add `tests/e2e/app2/test_tls_letsencrypt_smoke.py` — runs against the Let's Encrypt **staging** endpoint (NOT prod — prod has per-account/IP rate limits we don't want to chew through in CI). Asserts ACME-DNS-01 against a test domain returns a valid cert + that uvicorn serves HTTPS using it.
4. Sweep DC to PLAN_ARCHIVE.md.

## Operator-confirm questions

1. **Cloudflare token scope** — `Zone:DNS:Edit` on `hotchkiss.io` only? Or a broader read+edit on the whole account? Default: zone-restricted edit-only.
2. **Account email for the ACME account** — single email shared with `cfg.audit.signing.signer_name`'s identity, or a separate `cfg.app2.acme.account_email`? Default: shared (one human identity for the whole tool).
3. **DNS-01 vs HTTP-01** — DNS-01 (this spike) works against loopback A records; HTTP-01 would require port 80 on a public-routable host. Default: DNS-01.
4. **Cert renewal at-rest threshold** — 30 days before expiry triggers renewal. Default. Drop to 14 if you want to amortize ACME calls; bump to 45 if you want more buffer against extended outages.
5. **Single tool vs separate `recon-gen` binary vs library import** — the `recon-gen tls` verb compiles into the same binary as `recon-gen studio` / `recon-gen dashboards`. The DC.3 step that runs ensure-on-`up` is `subprocess.run([sys.executable, "-m", "recon_gen", "tls", "ensure", ...])` for clean process isolation. Default.
6. **Account-key location** — `run/tls/account.key` (per-clone, regenerated on a fresh checkout) or `~/.local/share/recon-gen/tls/account.key` (per-machine, survives reclones)? Default: per-machine in XDG — saves the operator from having to bootstrap a new ACME account on every fresh clone.

## Out of scope (explicit)

- **Cert tooling for downstream `recon-gen.exe` operators.** They supply `cfg.app2.tls.{cert_path,key_path}`; the coordinator above is a `recon-gen tls` verb gated by `cfg.app2.tls.cert_path` being one of *our* managed paths. Out-of-scope: shipping the ACME flow as a feature of the downstream tool.
- **HSTS / Strict-Transport-Security headers** — DD's territory once cookies land.
- **HTTP→HTTPS redirect** — uvicorn-direct doesn't need it; if a third environment lands behind Caddy/Traefik, that's a separate config concern.
- **Client cert / mTLS** — not requested.
- **QuickSight embed TLS posture** — owned by AWS, not us.
- **Container/Kubernetes deploy shapes** — bare-host is the only shape we support.
- **Windows operator path for the downstream tool** — local-dev assumes the operator's Mac; the WSL2 CI runner uses the same Python-side coordinator.

## Risks

1. **Cloudflare API down** — renewals fail until it's back. Mitigated by 30-day buffer; cached cert keeps working. Outage longer than 30d would be unprecedented.
2. **Let's Encrypt rate limits** — 50 certs / domain / week for prod; staging has its own (looser) limits. We renew 2 certs every 60 days each → ~1/week worst case. Well under the limit.
3. **Cloudflare API token leak** — token has Zone:DNS:Edit on a single zone. Impact: attacker can poison DNS records under `hotchkiss.io`. Mitigation: rotate token via the `recon-gen tls revoke` flow + Cloudflare dashboard; treat token like an AWS access key (`[[feedback_no_credential_friction]]` says we don't make humans look this up, so it lives in shell profile / `direnv`).
4. **ACME account key compromise** — distinct from cert keys (ACME accounts can revoke their own certs). If exfiltrated, the attacker can mint certs on our domains until we rotate. Account key in XDG (`~/.local/share/`) is no worse than other credentials there.
5. **Renewal-during-CI race** — two concurrent `./run_tests.sh up` runs both trying to renew the same cert would collide on Cloudflare API. Mitigated by a file lock at `run/tls/renew.lock` (advisory, ~10s critical section). Same shape as the existing PG container-rendezvous lock.
