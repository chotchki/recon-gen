# DC.0 — HTTPS support for Studio + Dashboards (spike + locks)

**Date:** 2026-06-13
**Phase:** DC.0
**Status:** Locks confirmed; ready for DC.1.

## Locks (operator-confirmed inline in PLAN.md 2026-06-12)

- **Cert provisioning:** cfg-stamped PEM file paths. No CLI flags. Both `studio` and `dashboards` Click commands read `cfg.tls.cert_path` + `cfg.tls.key_path`; absent block ⇒ plain HTTP (back-compat for local dev).
- **Local dev experience:** self-signed cert is fine. Operator accepts the browser warning during local iteration.
- **In-process TLS termination.** uvicorn supports `--ssl-keyfile` / `--ssl-certfile` natively (delegates to the standard library `ssl` module). No reverse proxy required.
- **App2 scope only.** QS embed has its own HTTPS posture; not touched. App2 is the surface that gains HTTPS.
- **No cookies today** → no `Secure` cookie work needed. If/when App2 adds cookies, flag for follow-up.

## Architecture

```
recon-gen studio -c run/config.yaml
                                ↓
                  load_config (common/config.py)
                                ↓
                cfg.tls.{cert_path, key_path}?
                       /                 \
                  present              absent
                       ↓                   ↓
            uvicorn.run(            uvicorn.run(
              app,                    app,
              host=…,                 host=…,
              port=…,                 port=…,
              ssl_certfile=cert_path  # no ssl_* args
              ssl_keyfile=key_path    )
            )
```

Where the wiring lives:

- **`common/config.py`** — add `TLSConfig` dataclass (path-typed); add `cfg.tls: TLSConfig | None`.
- **`cli/studio.py` + `cli/dashboards.py`** — read `cfg.tls` after `load_config`; thread to uvicorn config.
- **`common/html/server.py`** (if uvicorn config lives there) — accept optional `ssl_certfile` / `ssl_keyfile`.

## CFG shape

```yaml
# Optional — absent means plain HTTP (local dev posture).
tls:
  cert_path: /etc/letsencrypt/live/example.com/fullchain.pem
  key_path: /etc/letsencrypt/live/example.com/privkey.pem
```

Phase DE will fold this under `app2.tls.*` per the cfg-redesign locks. DC.1 lands the field at top level; DE.4 migrates.

## Spike validation plan (DC.0 → DC.1 unblock)

1. Generate a self-signed cert with `openssl req -x509 -newkey rsa:4096 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=localhost"`.
2. Drop `tls.cert_path` + `tls.key_path` in a scratch cfg.
3. Run `recon-gen studio -c <cfg>`. Open `https://localhost:8765`. Accept the browser warning. Page loads.
4. Run `recon-gen dashboards -c <cfg>`. Open `https://localhost:8765`. Same.
5. Remove the `tls:` block. Re-run both. Page loads on HTTP (backwards-compat).

If step 1-5 pass, DC.1 is ready to land.

## Migration path

- **Existing deployments:** unchanged. Absent `tls:` block ⇒ plain HTTP (current behavior).
- **New HTTPS deployments:** add `tls:` block. Cert provisioning is operator-managed (Let's Encrypt, ACME, mkcert, self-signed — operator choice).

## Out-of-scope

- HSTS headers, Strict-Transport-Security policy. Defer until cookies / sensitive data lands (which is DD's territory).
- HTTP→HTTPS redirect. Operators run separate processes if they need both; the cfg picks one.
- TLS cert auto-renewal. Operator concern (cronjob, certbot, etc.); not in scope for the generator.

## Unblock criteria for DC.1

- This spike doc ✓
- Operator confirms locks ✓ (already done in PLAN.md inline comments)
- Self-signed validation pass (operator-driven)

DC.1 lands cfg field + uvicorn wiring. DC.2 docs the local-dev onboarding. DC.3 sweeps to PLAN_ARCHIVE.
