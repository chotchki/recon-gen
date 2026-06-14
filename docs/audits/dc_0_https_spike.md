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

## The redirect — DNS + Let's Encrypt via Cloudflare, 4 DNS names

The prior revision recommended mkcert (local-CA self-signed) for local-dev + Caddy ACME for shared deploys. Operator pushback (2026-06-14, two passes):

**Pass 1 — real LE certs via DNS-01.** Use real Let's Encrypt certs everywhere via DNS-01 challenge against `hotchkiss.io`, modeled on the [hotchkiss-io coordinator](https://github.com/chotchki/hotchkiss-io/tree/main/src/coordinator) the operator already runs for their personal site.

**Pass 2 — 4 DNS names, two with auto-discovered public IPs.** The prior 2-name shape (`localdev` + `ci`, both → 127.0.0.1) carried over the implicit assumption that *something else* keeps `hotchkiss.io` pointed at the operator's dev box (the existing `hotchkiss.io:5433` PG forward + `hotchkiss.io:1522` Oracle forward pattern documented in `[[project_cb10_qs_to_docker_pg_constraints]]`). Today the operator maintains that A record manually. Formalize it instead: the runner manages 4 DNS names directly, removing the external-process dep.

The 4 names per environment:

| Hostname | A → | Used for | Renewal |
|---|---|---|---|
| `localdev.recon-gen.hotchkiss.io` | `127.0.0.1` (static) | Operator's Mac browser → local uvicorn over loopback | One-time A record |
| `dev.recon-gen.hotchkiss.io` | Operator's Mac public IP (auto-discovered) | QuickSight (us-east-1) → operator's Mac PG/Oracle Docker (replaces the implicit `hotchkiss.io:5433` forward) | Runner updates A record when public IP changes |
| `localci.recon-gen.hotchkiss.io` | `127.0.0.1` (static) | WSL2 runner's local browser → local uvicorn over loopback | One-time A record |
| `ci.recon-gen.hotchkiss.io` | WSL2 runner's public IP (auto-discovered) | QuickSight (us-east-1) → WSL2 runner's PG/Oracle Docker | Runner updates A record when public IP changes |

**Why both loopback + public per environment.** Loopback is fastest and most reliable for the local browser (Studio / dashboards / QS embed in the operator's Chrome → local uvicorn never leaves the box). Public is needed so QuickSight in us-east-1 can reach the operator's Postgres/Oracle Docker (the existing `hotchkiss.io:5433` forward pattern, just renamed under our managed namespace). Splitting them gives the cert SAN list a coherent shape: one cert per environment covering both names, so the local browser AND QuickSight present the same Let's Encrypt cert as appropriate.

**Self-managed public IP discovery.** Both `dev.recon-gen.hotchkiss.io` and `ci.recon-gen.hotchkiss.io` discover their public IP via the [`cloudflare_trace`](https://github.com/chotchki/hotchkiss-io/blob/main/src/coordinator/ip/cloudflare_trace.rs) pattern: GET `https://1.1.1.1/cdn-cgi/trace`, parse the `ip=` line, compare to the current Cloudflare A record value, PATCH if different. Run on every `./run_tests.sh up` and on a heartbeat inside the runner. Self-contained — no external "make sure hotchkiss.io still points here" process required.

**Both DNS-01 challenges still validate against loopback or public — Let's Encrypt only cares about TXT records.** The browser hitting `https://localdev.recon-gen.hotchkiss.io:8765` resolves to 127.0.0.1, connects to local uvicorn, and presents a real Let's Encrypt cert. QuickSight hitting `dev.recon-gen.hotchkiss.io:5433` resolves to the operator's current public IP, connects to the PG Docker, and presents the same real Let's Encrypt cert (PG with TLS uses the same cert).

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
| Operator bootstrap | 2 (install, mint) | 1 (Caddyfile) | 5+ | **2 one-time (token, paste token env); runner manages all 4 DNS records** | N/A bare host |
| Codebase impact | ~80 LoC wrapper | ~120 LoC + Caddyfile | ~200 LoC + certbot | **~450 LoC under `_dev/tls/` + `acme` + `cryptography` (both in the `[dev]` extra, NOT shipped in the published wheel)** | ~150 LoC + boto3 |
| Claude end-to-end driveable | YES | YES | NO (certbot interactive) | **YES (runner-internal helper)** | NO (AWS console) |
| Shipping cert to downstream `recon-gen.exe` users | N/A | N/A | N/A | **Explicitly out-of-scope (user supplies cert via `cfg.app2.tls.*`)** | N/A |

## Recommendation — locked

**Build the ACME DNS-01 flow + public-IP auto-discovery as runner-internal machinery under `src/recon_gen/_dev/tls/` — no user-facing CLI verb.** Scope it to our test/local-dev/CI use only — 4 DNS names across two environments (operator's Mac + WSL2 CI runner). Downstream `recon-gen.exe` end-users continue to supply their own cert + key paths via `cfg.app2.tls.{cert_path,key_path}` and own their own renewal flow (certbot, acme.sh, whatever fits their environment).

This matches the operator's DX expectations:

- Same flow local + CI; no per-machine CA install; real Let's Encrypt certs; auto-renew on `./run_tests.sh up`.
- One Cloudflare API token in env (Zone:DNS:Edit on `hotchkiss.io`).
- Removes the prior implicit "external process keeps `hotchkiss.io` pointed at the dev box" dep — the runner now owns it.
- Operators of the published wheel see nothing new — the `_dev/` namespace is excluded from the build.

## Implementation plan (revised — runner-internal, 4 DNS names, 2 SAN certs)

The cert + DNS provisioning is internal test/dev/CI machinery. Downstream `recon-gen.exe` operators see no new commands — they keep using `cfg.app2.tls.{cert_path,key_path}` with paths they manage themselves. Our flow lives entirely under `src/recon_gen/_dev/` (the dev-only namespace, excluded from the published wheel — verified via `pyproject.toml::tool.hatch.build.targets.wheel.packages`).

**DC.2 — runner-internal ACME + public-IP discovery**

The module layout under `src/recon_gen/_dev/tls/`:

```
_dev/tls/
  ensure.py         # the top-level entry: ensure_dev_env(env: Env) -> None
  acme_client.py    # ACME DNS-01 state machine
  cloudflare_api.py # Cloudflare client (DNS records + zone discovery)
  public_ip.py      # cloudflare_trace.rs pattern, ported to Python
  storage.py        # XDG paths, file locks, cert+key persistence
```

The shape of the entry function:

```python
class Env(StrEnum):
    DEV = "dev"  # operator's Mac
    CI = "ci"    # WSL2 CI runner

# Hostnames per env (locked tuples)
_HOSTS_BY_ENV: Final = {
    Env.DEV: ("localdev.recon-gen.hotchkiss.io", "dev.recon-gen.hotchkiss.io"),
    Env.CI:  ("localci.recon-gen.hotchkiss.io",  "ci.recon-gen.hotchkiss.io"),
}

def ensure_dev_env(
    env: Env, *, cert_path: Path, key_path: Path,
) -> None:
    """Idempotent. One call covers everything the runner needs for HTTPS.

    Concretely:
      1. Reconcile A records: `local<env>` → 127.0.0.1 (static),
         `<env>` → result of public_ip.discover() (auto). PATCH via
         Cloudflare API if drift detected; no-op if current.
      2. Read cert at cert_path. If present + not_after >= now+30d,
         done.
      3. Else run ACME DNS-01 for BOTH hostnames in _HOSTS_BY_ENV[env]
         as a single SAN cert. Write PEM chain + key to caller paths.

    Holds an advisory file lock at ~/.local/share/recon-gen/tls/renew.lock
    so two concurrent runner invocations don't double-write or hit
    Cloudflare API limits.
    """
```

1. ACME state machine via the `acme` library (IETF maintainer, `pip install acme`) under a new `[dev]` extra so the downstream wheel stays minimal. ACME long-lived state lives at the XDG path `~/.local/share/recon-gen/tls/` — survives `rm -rf run/` and fresh clones, avoids burning the Let's Encrypt account-creation rate limit (5 per IP per 3h) on every reset. Layout:
   ```
   ~/.local/share/recon-gen/tls/
     account.key       # ACME account private key (one per machine)
     zone-id.txt       # Cloudflare zone-id cache (skips lookup on every run)
     dev/cert.pem      # operator's-Mac SAN cert (localdev + dev hostnames)
     dev/key.pem
     ci/cert.pem       # WSL2-runner SAN cert (localci + ci hostnames)
     ci/key.pem
     renew.lock        # advisory file lock for concurrent runner safety
   ```
   The WSL2 self-hosted CI runner's home dir persists across workflow runs (same as the long-lived `recon-gen-local` IAM keys), so XDG works on CI without special-case. `release.yml` on `ubuntu-latest` doesn't need the coordinator — it's just wheel install + smoke test.
   Account email read from `cfg.audit.signing.signer_name` — one identity for the whole tool. Cfg escape hatch `cfg.app2.acme.state_dir` overrides the XDG default for operators who prefer one storage convention everywhere.
2. Cloudflare API client — direct `requests` calls against `api.cloudflare.com/client/v4/zones/{zone_id}/dns_records`. Token read from env `RECON_GEN_CLOUDFLARE_TOKEN` (per `[[feedback_no_credential_friction]]`). Local-dev secret-at-rest convention: `run/secrets.env` (gitignored, sourceable: `set -a; source run/secrets.env; set +a`) holds the token. CI: GitHub secret → workflow `env:` → process env. Single read-site in either case. Zone ID auto-discovered from `hotchkiss.io` lookup on first run; cached at `~/.local/share/recon-gen/tls/zone-id.txt` (alongside the rest of the XDG state).
3. Public-IP discovery (`public_ip.py`) ports the [`cloudflare_trace.rs`](https://github.com/chotchki/hotchkiss-io/blob/main/src/coordinator/ip/cloudflare_trace.rs) pattern: GET `https://1.1.1.1/cdn-cgi/trace`, parse text body line-by-line, return the `ip=` value. ~30 LoC. Retries 3× on transient HTTP errors; raises on 4xx (token / IP misconfig).
4. DNS-01 flow inside `acme_client.py`, called once per env (covering both SANs in one cert):
   1. ACME `new-order` with two identifiers (`local<env>` + `<env>` hostnames).
   2. For each authorization → DNS-01 challenge token.
   3. POST `_acme-challenge.<each_hostname>` TXT record via Cloudflare API.
   4. Poll via `dnspython` against `1.1.1.1` until BOTH TXTs propagate (typical: <15s).
   5. Tell ACME each challenge is ready; poll order until VALID.
   6. Generate keypair via `cryptography`, build CSR with both SANs, finalize order, download PEM chain.
   7. Write `cert.pem` + `key.pem` to caller-supplied paths.
   8. DELETE both TXT records (cleanup; idempotent on retry).
5. A-record reconciliation in `cloudflare_api.py`:
   - For static records (`local<env>` → 127.0.0.1): GET current value, PATCH if different. No-op if equal.
   - For dynamic records (`<env>` → public IP): GET current value, call `public_ip.discover()`, PATCH if different. Skip if equal.
   - All four records reconciled on every `ensure_dev_env` call. Together this is <500ms when nothing changes (4 GETs, 0 PATCHes).
6. Wire `ensure_dev_env` into `cmd_up_to`'s prelude: when `cfg.app2.tls.cert_path` is configured AND the file is missing or `<30d`, OR public-IP-discovery indicates drift on either dynamic A record, call it. Skip when `cfg.app2.tls.*` is unset (the publication path — no managed cert involved). Env detection: `Env.CI` when `os.environ.get("CI")` is truthy, else `Env.DEV`.
7. Add `tests/unit/test_dev_tls_ensure.py` covering: account-key generation, cert-expiry computation, DNS-01 challenge response shape, missing-token error path, idempotent no-op when cert is fresh + A records match, cloudflare_trace parsing, A-record reconcile drift detection, public-IP-changed-without-cert-renewal path. Mock `requests` for Cloudflare + trace; mock the ACME client; real PEM parsing via `cryptography`.

**DC.3 — runner integration + phase exit**

1. The runner's `cmd_up` already pre-flights local containers. Extend it: if `cfg.app2.tls.cert_path` is configured, call `ensure_dev_env(...)` before any layer dispatches. Failure ⇒ `EXIT_NEEDS_OPERATOR` with the actionable message (token missing, Cloudflare 4xx, ACME rate-limit, IP-discovery failure).
2. **Migrate the existing `hotchkiss.io:5433` / `hotchkiss.io:1522` forwards** to the new managed names. Update `[[project_cb10_qs_to_docker_pg_constraints]]` reference; `cfg.aws.qs_disable_pg_ssl` may now be flipped to TLS-on since QS can validate the cert. (Out of strict DC scope but called out as a follow-up so it isn't lost.)
3. Document the **one-time operator setup** in `docs/reference/local-dev.md`:
   - Cloudflare API token creation (Zone:DNS:Edit, restricted to the `hotchkiss.io` zone) — paste step-by-step.
   - 4 DNS records (created automatically by the runner on first `up`; manual fallback documented). The `local<env>` records require no maintenance; the `<env>` records auto-update on public-IP change.
   - **Local-dev token storage**: paste `RECON_GEN_CLOUDFLARE_TOKEN=<token>` into `run/secrets.env` (gitignored). Operator sources it via `set -a; source run/secrets.env; set +a` in their shell profile, or via `direnv` with a `.envrc` that sources `run/secrets.env`. Same file holds future secrets (DD's OIDC client secret + JWT secret) so we keep ONE secret file convention.
   - **CI token storage**: GitHub secret `CLOUDFLARE_TOKEN` → `e2e.yml` / `ci.yml` workflow `env: RECON_GEN_CLOUDFLARE_TOKEN: ${{ secrets.CLOUDFLARE_TOKEN }}` → process env. Token never lands on disk in CI.
   - `cfg.app2.tls.cert_path: run/tls/cert.pem` + `cfg.app2.tls.key_path: run/tls/key.pem` (templated into `run/base.yaml`'s example).
   - ACME long-lived state under `~/.local/share/recon-gen/tls/` — no operator-facing action; the runner creates the dir on first run.
4. Add `tests/e2e/app2/test_tls_letsencrypt_smoke.py` — runs against the Let's Encrypt **staging** endpoint (NOT prod — prod has per-account/IP rate limits we don't want to chew through in CI). Asserts ACME-DNS-01 against a test SAN cert returns a valid cert + that uvicorn serves HTTPS using it + that the cert covers both SAN entries.
5. Sweep DC to PLAN_ARCHIVE.md.

## Operator-confirm questions

1. **Cloudflare token scope** — `Zone:DNS:Edit` on `hotchkiss.io` only? Or a broader read+edit on the whole account? Default: zone-restricted edit-only.
2. **Account email for the ACME account** — single email shared with `cfg.audit.signing.signer_name`'s identity, or a separate `cfg.app2.acme.account_email`? Default: shared (one human identity for the whole tool).
3. **DNS-01 vs HTTP-01** — DNS-01 (this spike) works against loopback A records; HTTP-01 would require port 80 on a public-routable host. Default: DNS-01.
4. **Cert renewal at-rest threshold** — 30 days before expiry triggers renewal. Default. Drop to 14 if you want to amortize ACME calls; bump to 45 if you want more buffer against extended outages.
5. **No user-facing CLI verb** — locked by operator 2026-06-14. The ACME flow lives under `src/recon_gen/_dev/tls/` (the dev-only namespace), called inline by the runner. Downstream `recon-gen.exe` operators see no new commands; they supply their own cert+key paths via `cfg.app2.tls.*` and own the renewal externally (certbot, acme.sh, manual, whatever).
6. **Account-key location** — `~/.local/share/recon-gen/tls/account.key` (per-machine, survives reclones, XDG path). Locked default.

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
