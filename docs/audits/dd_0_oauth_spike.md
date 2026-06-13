# DD.0 — OAuth-based authentication spike (App2-side, IdP container test rig)

**Date:** 2026-06-13
**Phase:** DD.0
**Status:** Locks confirmed in PLAN.md; this doc adds the IdP-container + CI port-forward decision needed before DD.1 lands.

## Locks (operator-confirmed in PLAN.md 2026-06-12)

- **App2-side only.** QS embed has its own auth flow; DD doesn't touch it.
- **Deployment tier = role.** Studio deployment (editor surface) and Dashboards deployment (read-only) are separate processes. Authn collapses to "is the user authenticated for this deployment". No per-dashboard ACLs.
- **HTTP local-dev preserved.** Absent cfg `auth:` block ⇒ no auth gate. Presence ⇒ full HTTPS + OAuth pipeline (DD assumes DC has shipped).
- **Library: authlib.** Starlette-native; handles PKCE, state, nonce, CSRF at the library layer.
- **Provider shape: single IdP per cfg.** `auth.oidc.{issuer_url, client_id, client_secret, redirect_uri, scopes}`. No multi-IdP registry.
- **No identity → QS user mapping needed.** App2 reads the DB directly.
- **Session: stateless JWT cookies.** Encoded with IdP-signed claims; no server-side session store.
- **Logout: OIDC `end_session_endpoint`.** RP-initiated logout; local cookie clear + IdP single-sign-out.
- **Testing leverages hotchkiss.io port-forward** (same pattern as QS→Docker-PG per `[[project_cb10_qs_to_docker_pg_constraints]]`).

## Open at DD.0: pick the test-side IdP container

Three candidates evaluated:

### Option A — Keycloak
- **Pros:** full OIDC + OAuth2 + SAML; battle-tested; rich admin UI; broad client support; Docker image ships configured.
- **Cons:** ~500 MB image; ~30-60s cold-start; heavy for CI test fixture; requires DB (uses H2 in dev mode).
- **CI fit:** acceptable on `ci-shared-*` style persistent container; slow as a per-run spinup.

### Option B — Dex
- **Pros:** lightweight (~50 MB); pure OIDC; static-config friendly (yaml); cold-start <5s; CNCF project.
- **Cons:** less feature-rich (no admin UI; only OIDC/OAuth, no SAML); requires upstream IdP (e.g. mocked GitHub OIDC); less common.
- **CI fit:** ideal — fast cold-start, fits the `--shm-size=2g`-style ephemeral pattern.

### Option C — authlib's built-in dev IdP
- **Pros:** in-process; no Docker; trivial to enable.
- **Cons:** not a real IdP; doesn't exercise the full OIDC flow (PKCE, state, nonce) faithfully; gives false-positive test coverage.
- **CI fit:** poor — tests would pass with the dev IdP but fail against a real Okta/Entra deploy.

**Recommend:** **Option B (Dex).** Cold-start latency matters for CI's `--shm-size`-class operator-feedback loop; Dex's <5s cold-start fits. Static yaml config means deterministic test setup. Feature gap (no SAML) is irrelevant — our scope is OIDC only.

## Port-forward shape

Per `[[project_cb10_qs_to_docker_pg_constraints]]`:
- Local Docker container exposes Dex on host port `5556` (Dex's documented default).
- Home firewall forwards `hotchkiss.io:5556` → `<dev-machine>:5556`.
- CI's Dex container exposes the same port; same forward.
- Cfg's `auth.oidc.issuer_url` for tests = `https://hotchkiss.io:5556/dex`.
- TLS posture: Dex supports TLS termination natively (`web.tls.cert` + `web.tls.key`); use a self-signed cert for the forward.

The `5556` value chosen because it's Dex's documented default + doesn't collide with existing forwards (5433 PG, 1522 Oracle, 8765 Studio).

## Cfg shape (post-DD.1, pre-DE.4 rename)

```yaml
auth:
  oidc:
    issuer_url: https://hotchkiss.io:5556/dex
    client_id: recon-gen-app2
    client_secret_env: RECON_GEN_OIDC_CLIENT_SECRET
    redirect_uri: https://localhost:8765/auth/callback
    scopes: ["openid", "email", "profile"]
  session:
    jwt_secret_env: RECON_GEN_JWT_SECRET
```

Per `[[feedback_no_credential_friction]]` — secrets reference env vars; cfg never stores raw secrets.

Phase DE will fold into the cfg-redesign concern grouping; this shape is interim.

## Spike validation plan

Local prototype (DD.0 exit):

1. Spin Dex in Docker: `docker run -d --name dex -p 5556:5556 ghcr.io/dexidp/dex:v2.40.0 dex serve /etc/dex/config.yaml`.
2. Bootstrap Dex with a static OIDC client (`recon-gen-app2`) + a single static user (`testuser@example.com` / passphrase).
3. Stand up App2 with the cfg shape above + a stub `/auth/login` + `/auth/callback` route using authlib.
4. Click "Sign in" → Dex login form → enter creds → callback fires → JWT cookie set → land at App2's protected route.
5. Click "Sign out" → Dex `end_session_endpoint` fires → cookie cleared + Dex session terminated.
6. Verify JWT decoder catches a tampered cookie + returns 401.

If steps 1-6 pass, DD.1 is ready.

## DD.1+ unblock criteria

- This spike doc ✓
- Operator confirms locks (already in PLAN.md inline) ✓
- Dex chosen as the test-side IdP (recommended above)
- Port-5556 allocation confirmed by operator
- Dex prototype completes (DD.0 last step)

DD.1 lands the cfg block + authlib wiring. DD.2 adds the Starlette routes + JWT session middleware. DD.3 wires into both studio + dashboards apps. DD.4 wires the Dex container into the CI runner. DD.5 docs. DD.6 phase exit.

## Out-of-scope

- IdP-side user provisioning (operator's IdP admin concern).
- Per-route fine-grained authorization. DD = authn-only. Per-route ACLs would be a separate phase if/when needed.
- Refresh token handling. JWT-only; when JWT expires, user re-authenticates via Dex (which has its own session).
- MFA. IdP-side concern.
