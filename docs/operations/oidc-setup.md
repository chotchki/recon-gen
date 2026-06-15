# OIDC setup — App2 login via authlib + Dex (test) / Okta (prod)

> Phase DD operator runbook. Locked design: [`docs/audits/dd_0_oauth_spike.md`](../audits/dd_0_oauth_spike.md). Runtime code: [`src/recon_gen/common/html/auth.py`](../../src/recon_gen/common/html/auth.py) (Starlette middleware + routes) + [`src/recon_gen/_dev/oidc/`](../../src/recon_gen/_dev/oidc/) (test-side Dex coordinator). This guide walks you from "no auth wired" to "Okta-style login working in prod **and** reproducible in CI via Dex" in ~20 min.

## What this builds

`recon-gen studio` / `recon-gen dashboards` gain an OIDC code-flow login gate when `cfg.auth.oidc` + `cfg.auth.session` are both set. Browser visits redirect to your IdP's authorize endpoint; the callback mints a signed JWT cookie (`recon_gen_session`) that protects every subsequent request. The IdP can be **any** OIDC-compliant provider — Okta, Auth0, AzureAD, Keycloak, Dex. For test/CI you spin a managed Dex container via the runner; production points at your real IdP.

| Posture | When | Trigger |
|---|---|---|
| **HTTP local-dev** (no auth) | First-time iteration, offline work, single-operator demos | `cfg.auth.oidc` / `cfg.auth.session` absent in cfg yaml |
| **HTTPS + OIDC** (production) | Shared deploys, regulator-facing artifacts, multi-operator | Both blocks present, IdP reachable from the App2 host |
| **HTTPS + OIDC via managed Dex** (test/CI) | Reproducing CI's auth posture locally, end-to-end tests | Both blocks present + `_dev/oidc/` reconciles a Dex container per the cfg's issuer URL |

POLICY 1 invariant: every block in this doc applies identically to CI and local dev — same cfg shape, same env-var registry, same `./run_tests.sh` invocation.

## Prerequisites

- [TLS](tls-setup.md) wired (cfg.app2.tls + the LE cert/key). OIDC over HTTP is a non-starter — DD assumes DC is shipped. The same LE cert mints both App2's HTTPS server and (test-side) the Dex container's HTTPS listener.
- An IdP to point at. **For production:** Okta / Auth0 / AzureAD tenant with admin access. **For test/CI:** nothing — the runner spins Dex automatically.
- For CI integration: admin access to the GitHub repo Settings → Secrets and variables → Actions.

## Step 1 — Pick the posture

**Posture A: HTTP local-dev (no auth).** Leave `auth:` out of your cfg yaml. `make_app(cfg=cfg)` skips middleware wiring entirely; `recon-gen studio` runs over plain HTTP with no login chrome. Same code path that existed pre-DD. Use this when you're iterating on dashboard shapes / wiring and don't care about identity.

**Posture B: HTTPS + production OIDC.** Add both `auth.oidc` and `auth.session` blocks to your cfg yaml pointing at your real IdP (Step 2). Set `app2.tls` from [tls-setup.md](tls-setup.md). The middleware fires on every request; redirect chain hits your IdP.

**Posture C: HTTPS + managed Dex (CI parity).** Same as B but point the issuer URL at the runner-managed Dex container (`https://localdev.recon-gen.hotchkiss.io:5557/dex` for your Mac, `https://localci...:5556/dex` for the WSL2 CI runner). The runner spins Dex automatically when the `app2` layer fires.

You can switch postures freely — they're cfg-driven, not code-branched.

## Step 2 — Cfg block

```yaml
auth:
  oidc:
    issuer_url: https://your-tenant.okta.com   # or Dex URL for posture C
    client_id: recon-gen-app2
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://app2.example.com/auth/callback
    scopes:
      - openid
      - email
      - profile
  session:
    jwt_secret_env: RECON_GEN_JWT_SECRET
```

Field semantics:

- **`issuer_url`** — the base URL of the OIDC provider. The discovery doc lives at `<issuer_url>/.well-known/openid-configuration`. authlib fetches it lazily on first login + caches the result. For Okta this is your tenant URL (`https://your-tenant.okta.com`); for Auth0 it's `https://your-tenant.us.auth0.com`; for Dex it's the issuer you registered (`https://localdev.recon-gen.hotchkiss.io:5557/dex`).
- **`client_id`** — the OIDC client ID your IdP issued when you registered the App2 application. Per IdP, per environment (dev / staging / prod).
- **`client_secret_env`** — the **name** of an env var carrying the client secret, never the secret itself ([`feedback_no_credential_friction`](../../CLAUDE.md) — secrets stay out of the cfg yaml). The convention is `RECON_GEN_OIDC_CLIENT_SECRET`; nothing forces it.
- **`redirect_uri`** — must EXACTLY match one of the redirect URIs your IdP has registered for the client. Path is fixed at `/auth/callback`. Host + port come from your deploy. The IdP rejects any mismatched callback URL.
- **`scopes`** — claims requested at authorize time. `openid` is required (it's what makes the IdP issue an ID token); `email` + `profile` are needed for `JwtCookieMiddleware` to stamp `{sub, email}` into the cookie. Add IdP-specific scopes (`groups`, `offline_access`) here when downstream code needs them.

`auth.session.jwt_secret_env` names the env var holding the HMAC-SHA256 signing secret for the local `recon_gen_session` JWT cookie. Same convention as the OIDC client secret — env-var name in cfg, value in `run/secrets.env`.

## Step 3 — Operator secrets

The runner reads three env vars at request time:

| Env var | Posture | What it holds |
|---|---|---|
| `RECON_GEN_OIDC_CLIENT_SECRET` | B, C | The OIDC client secret string (32+ chars). The IdP issued this when you registered the client. |
| `RECON_GEN_JWT_SECRET` | B, C | The HMAC signing secret for the local session JWT. Mint via `openssl rand -hex 32` — at least 32 bytes. |
| `RECON_GEN_DEX_USER_PASSWORD` | C only | Plaintext password for the static test user inside Dex. Mint via `openssl rand -hex 14`. The runner bcrypt-hashes it before injecting into Dex's config. |

Stash all three in `run/secrets.env` alongside the TLS token from [tls-setup.md](tls-setup.md):

```bash
cat >> run/secrets.env <<'EOF'
RECON_GEN_OIDC_CLIENT_SECRET=<from-your-IdP-or-openssl-rand-hex-32>
RECON_GEN_JWT_SECRET=$(openssl rand -hex 32)
RECON_GEN_DEX_USER_PASSWORD=$(openssl rand -hex 14)
EOF
chmod 600 run/secrets.env
```

Source it on shell entry (same hook as TLS):

```bash
[ -f run/secrets.env ] && set -a && . run/secrets.env && set +a
```

## Step 4a — Production IdP (Posture B): register the client at the IdP

The exact menu paths differ per IdP; the data shape is the same.

1. **Application type**: `Web application` (OIDC code flow, server-side callback).
2. **Sign-in redirect URI**: paste `<your-app2-base-url>/auth/callback` exactly.
3. **Sign-out redirect URI**: same base, `/` (the bare app root). RP-Initiated Logout points users back here after the IdP clears its session.
4. **Token endpoint auth method**: `Client Secret Basic` (default for authlib).
5. **Grant types**: `Authorization Code` (others disabled).
6. **Scopes**: ensure `openid`, `email`, `profile` are released. Add `groups` / `offline_access` if your downstream code consumes them.
7. **Assign users / groups**: per your IdP's UI. App2 doesn't enforce per-user authz — anyone the IdP successfully authenticates gets a session.

Copy the issued `client_id` and `client_secret` into the cfg (Step 2) + `run/secrets.env` (Step 3). Done — first browser hit to App2 redirects to your IdP's login page.

## Step 4b — Managed Dex (Posture C): nothing to do

The runner owns the Dex container lifecycle. On the first `./run_tests.sh up_to=app2` with cfg.auth.oidc + cfg.app2.tls both set, the runner's `_ensure_oidc_if_configured` reconciles a Dex container:

1. Reads `cfg.auth.oidc.issuer_url` (`https://localdev...:5557/dex` for your Mac, `https://localci...:5556/dex` for CI — picked by `cfg.app2.tls.env`).
2. Reads the DC.3 LE cert + key from `cfg.app2.tls.cert_path` / `.key_path`. Dex serves HTTPS using the same cert App2 does — no separate cert lifecycle.
3. Writes a tempdir with `config.yaml` (Dex's static client + static user pointing at `client_secret_env` + `user_password_env`) + `cert.pem` + `key.pem`.
4. Starts (or adopts) the shared `recon-gen-test-dex` Docker container — port `5557` on local dev, `5556` on the CI runner.
5. Polls the issuer URL's `/.well-known/openid-configuration` until Dex answers (30s timeout).

`RECON_GEN_DEX_USER_PASSWORD` is the password for the single static user `testuser@example.com` (the email is locked in the runner; cfg-overridable in a future iteration). Use this when you sign in via the test browser flow.

The `RECON_GEN_DEX_URL` env var (registered but disabled by default) short-circuits container spinup when a pre-spun Dex is reachable — for the shared-CI-Dex pattern not yet wired in `ci.yml`.

## Step 5 — CI GitHub secrets

The WSL2 self-hosted CI runner uses the same coordinator. Add three new secrets:

1. GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
2. Name: `OIDC_CLIENT_SECRET` — value: any random 32-byte hex string. (CI uses managed Dex, so this is just a per-run shared secret between Dex's static-client block and authlib's client config. It does NOT need to match a real IdP's secret.)
3. Same for `JWT_SECRET` — value: `openssl rand -hex 32`.
4. Same for `DEX_USER_PASSWORD` — value: `openssl rand -hex 14`.

`.github/workflows/ci.yml` already wires all three via the `Generate random OIDC credentials` step + cfg-overwrite heredoc (Phase DD.4.ci). The secrets reach the runner process; the runner reads them via the typed `RECON_GEN_*.get_or_none()` registry.

You can also generate fresh values in-CI rather than reading from GitHub secrets — `ci.yml`'s step uses `openssl rand` for all three, so the GitHub secrets are unused today. Kept for the day you want to pin a known-good test-IdP secret across runs.

## Step 6 — First run

```bash
./run_tests.sh up_to=app2
```

On first run with both blocks set, the runner:
1. Reconciles TLS (`_ensure_tls_if_configured`) — mints/renews the LE cert if needed.
2. Reconciles OIDC (`_ensure_oidc_if_configured`) — Posture C only; Posture B is a no-op here since the IdP runs externally.
3. Starts App2 with the auth middleware wired.

Open `https://<your-app2-host>:<port>/dashboards/...` in your browser. You're redirected to `/auth/login`, then to your IdP's authorize endpoint, then back to `/auth/callback` with a fresh JWT cookie. Subsequent visits in the same session see the cookie and skip the redirect chain.

Logout: visit `/auth/logout` (or click whatever sign-out affordance the dashboard exposes). App2 redirects you to the IdP's `end_session_endpoint`, which clears the IdP session and (typically) lands you back at the IdP's post-logout page.

## What's gated

`JwtCookieMiddleware` checks the cookie on every request. Public-prefix bypass:

- `/auth/` — login / callback / logout routes
- `/static/` — bundled CSS / JS
- `/docs/` — embedded mkdocs site (if mounted)
- `/health` — liveness probe

Everything else demands a valid `recon_gen_session` cookie. `HX-Request` header → 401 JSON (HTMX-friendly); browser navigation → 302 to `/auth/login`. Tampered or expired cookies fail-closed (401 / 302 same as missing).

## Troubleshooting

**"runner: OIDC pre-flight failed: client_secret is empty"** — `RECON_GEN_OIDC_CLIENT_SECRET` not exported. Source `run/secrets.env`.

**"runner: OIDC pre-flight failed: app2.tls block required for Dex HTTPS"** — Posture C without TLS. Wire TLS first ([tls-setup.md](tls-setup.md)) — Dex serves HTTPS using the LE cert App2 already manages.

**"runner: OIDC pre-flight failed (RuntimeError): Dex readiness check failed"** — container started but `/.well-known/openid-configuration` doesn't answer. Most common cause: cert/key paths in the cfg point at non-existent files, or the cert doesn't cover the issuer URL's hostname. Verify the LE cert SANs include the `localdev.recon-gen.hotchkiss.io` (DEV) or `localci.recon-gen.hotchkiss.io` (CI) hostname.

**"AuthConfigError: env var 'RECON_GEN_JWT_SECRET' (referenced by cfg.auth.session.jwt_secret_env) is unset or empty"** — same as the client-secret case but for the JWT signing key. Same fix.

**Browser lands on `/auth/login` over and over** — cookie not sticking. Three usual suspects: (1) IdP's callback succeeded but `userinfo.sub` was empty (check the callback response in browser dev tools / `network.txt` triage capture); (2) `redirect_uri` mismatch — the IdP redirected you somewhere, but not back to App2 (compare cfg.auth.oidc.redirect_uri exactly against the IdP's registered redirect URI); (3) cookie domain mismatch — `recon_gen_session` is set with `path=/` + `httponly` + `secure`; if App2 is over HTTP (Posture A wiring inadvertently lit up) the `secure` flag will be dropped silently by the browser.

**OIDC works locally but 401s on CI** — `RECON_GEN_JWT_SECRET` must be the same value across the App2 process and any process that mints JWTs. Different per-call values produce signatures that don't verify (`InvalidTokenError`). CI's `openssl rand` step generates the secret once per workflow run + exports to every subsequent step's env.

**"OAuth callback failed" 401 on `/auth/callback`** — authlib's token exchange against the IdP rejected. Usual causes: client secret wrong (re-export from IdP into `RECON_GEN_OIDC_CLIENT_SECRET`); clock skew between App2 host and IdP (the ID token's `iat`/`exp` are unforgiving — `chronyd`/`ntpd` on both sides); the IdP requires PKCE and authlib didn't send it (authlib does PKCE by default — confirm the IdP isn't rejecting on some other claim).

## Secret rotation

1. Mint new values (`openssl rand -hex 32` for `JWT_SECRET`, request new client secret from IdP for `OIDC_CLIENT_SECRET`).
2. Update both `run/secrets.env` (locally) and the GitHub `OIDC_CLIENT_SECRET` / `JWT_SECRET` / `DEX_USER_PASSWORD` secrets.
3. Restart `recon-gen studio` / `recon-gen dashboards`. The Dex container picks up the new secrets when the runner re-spins it (the `secretEnv` / `hashFromEnv` wiring re-reads from env on container restart). For Posture B, the IdP already accepted the new client secret as soon as you rotated it on their end.
4. Sessions issued under the old `JWT_SECRET` are now invalid — operators redirect to `/auth/login` on their next request. Tell them to re-authenticate.

## Out of scope

Downstream `recon-gen.exe` operators (pip-install the wheel, run `recon-gen studio` against their own deployments) own their IdP relationship externally. They:

- Register an OIDC client at their IdP of choice.
- Stash `client_id` in cfg + `client_secret` in their own env-var convention.
- Set `cfg.auth.oidc.redirect_uri` to match what the IdP has registered.
- Optionally use the test-side Dex coordinator (`_dev/oidc/`) for local-iteration parity — but that module is under the `[dev]` extra and not in the published wheel.

Anything beyond the cfg-driven authlib code-flow (refresh tokens, audience claims, group-based authz, per-user dashboard scoping) is not in scope today. The single-IdP-per-cfg lock from DD.0 is deliberate — multi-IdP routing would change the cfg shape and isn't justified by current customer demand.
