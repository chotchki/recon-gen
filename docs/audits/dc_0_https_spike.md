# DC.0 — HTTPS cert provisioning spike

**Status:** draft for decision · Date: 2026-06-14 · Prompted by: DC.1 landed the uvicorn TLS wiring (`cfg.app2.tls.{cert_path,key_path}` → `ssl_certfile`/`ssl_keyfile`) but punted *where the PEM files come from* to "operator concern". Shared-deployment operators don't have a default path, and Claude can't drive an end-to-end "fresh clone → HTTPS on a real hostname" without one. This spike picks the path.

> Supersedes the prior DC.0 entry-spike (the cfg-shape locks — optional `app2.tls:` block, absent ⇒ HTTP, in-process uvicorn termination, App2-scope-only — carry forward unchanged; this revision answers the cert-source question that was explicitly out-of-scope before).

## Why this exists

DC.1 makes uvicorn capable of serving HTTPS *if* given a cert + key. It does not produce certs. Today's options for an operator (and for Claude driving on their behalf):

- Local dev on `localhost` — browser warnings every restart, no way to test cookie/`Secure` paths cleanly.
- Shared deploy on a real DNS name — manual `openssl` or manual certbot, then a manual renewal cronjob the operator has to remember in 89 days. Claude can't drive it without a hand-off.

We need a default path per posture, wired into a CLI verb that Claude can drive in a single permission grant, with auto-renew that doesn't require the operator to remember anything (per the [no-credential-friction] norm).

## Candidate options

1. **mkcert wrapper** — Go binary, generates a local CA, installs it into the system + Firefox trust stores via `mkcert -install`, then mints per-host PEMs. Browser-trusted on machines that ran the install. Static certs; rotate manually. Not valid for the public internet.
2. **Caddy reverse proxy** — Go binary, listens on `:443`, terminates TLS, proxies to uvicorn on `127.0.0.1:<port>`. Auto-ACME against Let's Encrypt on first request; auto-renews in the background. Two-process model; uvicorn stays HTTP-only on loopback.
3. **nginx + certbot** — same shape as Caddy but two binaries, two config languages, a renewal cronjob, and certbot's Python deps. Strictly worse than Caddy on every axis we care about; included for completeness.
4. **In-process ACME (Python `acme` client at startup)** — recon-gen itself talks ACME, writes PEMs to `cfg.app2.tls.cert_path`, hot-reloads uvicorn on renewal. No extra binary; ~600 LoC + a heavy dep.
5. **AWS ACM** — only viable when App2 is fronted by ALB/CloudFront. Our shared-deploy story is "operator's box on a real hostname", not "behind AWS LB". N/A for the primary use case; keep in pocket for the day someone deploys App2 on ECS.
6. **Hybrid: mkcert (local-dev) + Caddy (shared deploy)** — different tools for different postures, both wrapped behind one `recon-gen tls …` verb so Claude's mental model stays single-surface.

## Comparison matrix

| Axis | mkcert | Caddy | nginx+certbot | In-proc ACME | AWS ACM | Hybrid (mkcert+Caddy) |
|---|---|---|---|---|---|---|
| Operator DX (steps to bootstrap) | 2 (install, mint) | 1 (Caddyfile + start) | 5+ | 1 (cfg flag) | N/A on bare host | 2 per posture |
| Shared-deploy fit (real hostname, browser-trusted) | NO | YES | YES | YES | YES (AWS-only) | YES (via Caddy) |
| Local-dev fit (`localhost` no warnings) | YES | weak (ACME needs public DNS) | weak | weak | NO | YES (via mkcert) |
| Cert renewal automation | manual | auto (background) | cron + reload | auto (in-process) | auto | auto on the shared leg |
| Codebase impact | ~80 LoC wrapper, 0 deps | ~120 LoC + Caddyfile template, 0 Python deps | ~200 LoC + certbot dep | ~600 LoC + `acme` + `cryptography` heavy | ~150 LoC + boto3 (already in tree) | ~200 LoC total, 0 Python deps |
| Claude end-to-end driveable | YES (Go binary, no prompts) | YES (Caddy binary, no prompts) | NO (certbot interactive prompts on first run) | YES | NO (needs ALB/listener clicks in console) | YES |
| Cfg shape impact | none (still writes to `cfg.app2.tls.{cert_path,key_path}`) | none (Caddy reads its own config; recon-gen stays HTTP) | none | none | adds `cfg.app2.tls.acm_arn` | none |
| Failure mode if cert source dies | browser warning | 502 from Caddy with clear log | silent expiry | uvicorn won't start | AWS console | isolated per posture |

## Recommendation

**Adopt the hybrid: mkcert for local-dev, Caddy for shared deploys, both wrapped behind a single `recon-gen tls …` verb.** Keep `cfg.app2.tls.{cert_path,key_path}` unchanged — both paths just write PEMs there (mkcert directly, Caddy via its auto-managed storage symlinked into the cfg-stamped paths). No cfg-shape churn.

The Rust/Go-binary preference and the "design for Claude loops" criterion both point at the same answer: mkcert and Caddy are single-static-binary Go tools with no interactive prompts on the happy path, so one Bash permission covers the whole bootstrap. Caddy's ACME loop is the part of this that's hardest to get right (HTTP-01 challenge, renewal scheduling, OCSP stapling, rate-limit backoff), and writing that ourselves in Python (~600 LoC + `acme` + `cryptography`) is exactly the kind of "build the cert robot" rabbit hole that the no-compat-shims + spike-before-locking norms warn against. Caddy has shipped this for eight years; we don't need to relitigate it.

mkcert covers the local-dev gap that Caddy can't: ACME needs a public DNS name, and `https://localhost:8765` on a developer's laptop doesn't have one. mkcert's local CA solves this cleanly — `mkcert -install` once per machine, then every per-host cert it mints is trusted by Chrome/Firefox/Safari with no warning banner. This matters for the cookie work in DD (the prompt names `dd_0_oauth_spike.md` as a sibling) — `Secure` cookies need browser-trusted HTTPS to test honestly, and you can't run that loop with a click-through warning every restart.

The rejected paths each fail one hard criterion: nginx+certbot fails Claude-driveability (certbot's first-run is interactive), in-process ACME fails the spike-before-locking rule (we'd be writing security-critical code we don't have to write), and AWS ACM fails the deployment-shape assumption (operators on a bare host don't have an ALB). Hybrid is not over-engineering — it's two cheap wrappers around two binaries that each do one posture well.

## Implementation plan

**DC.2 — local-dev DX (mkcert leg)**

1. Add `recon-gen tls bootstrap-local --host <hostname>` Click subcommand under a new `cli/tls.py`. Default `--host localhost`.
2. The subcommand: (a) checks for `mkcert` on PATH, prints install hint with `brew install mkcert` / `apt install mkcert` if missing; (b) runs `mkcert -install` if the local CA isn't already trusted (idempotent — mkcert handles the check); (c) runs `mkcert -cert-file <run/tls/cert.pem> -key-file <run/tls/key.pem> <hostname> localhost 127.0.0.1 ::1`; (d) prints the exact yaml block to paste into `run/config.yaml`:
   ```yaml
   app2:
     tls:
       cert_path: run/tls/cert.pem
       key_path:  run/tls/key.pem
   ```
3. Add `recon-gen tls status` — reads `cfg.app2.tls`, prints cert SAN list + not-after, flags certs expiring in <14 days.
4. Add `tests/unit/test_cli_tls.py` covering: missing-binary error path, idempotent bootstrap, status parsing. Mock `subprocess.run` for mkcert; real PEM parsing via `cryptography`.
5. Update `docs/reference/local-dev.md` with the one-time `recon-gen tls bootstrap-local` step.

**DC.3 — shared-deployment leg (Caddy) + phase exit**

1. Add `recon-gen tls bootstrap-shared --host <fqdn> --email <acme-account>` subcommand. Writes a minimal `Caddyfile` to `run/tls/Caddyfile` pointing at uvicorn's loopback port, with `tls <email>` for ACME account binding.
2. Emit a `recon-gen-caddy.service` systemd unit + a `launchd` plist alongside it; print the install command for both. Caddy itself is operator-installed (one apt/brew line printed by the bootstrap).
3. The subcommand symlinks Caddy's auto-managed cert storage (`~/.local/share/caddy/certificates/.../<fqdn>.crt`) into `run/tls/cert.pem` + `key.pem` so `cfg.app2.tls.{cert_path,key_path}` keeps working unchanged. *Caveat: uvicorn becomes a fallback path here — the canonical shared-deploy flow is "Caddy on :443, uvicorn on :8765 HTTP-only". We document both: cfg-stamped TLS for ops that want uvicorn-direct, Caddy in front for ops that want auto-renew.*
4. Add `tests/e2e/app2/test_tls_caddy_smoke.py` — spins Caddy via Docker against a self-signed step-ca in the test harness (Let's Encrypt staging is too slow + rate-limited for CI), asserts uvicorn-behind-Caddy serves the index over HTTPS.
5. Update `docs/reference/shared-deploy.md`: prereqs (real DNS name, port 80+443 reachable), `recon-gen tls bootstrap-shared` invocation, the systemd/launchd install line, the renewal-is-automatic note.
6. Sweep DC to PLAN_ARCHIVE.md.

## Operator-confirm questions

1. **Hybrid vs single-tool** — confirm hybrid (mkcert local + Caddy shared) over picking just one. If you'd rather have one tool everywhere, Caddy can do local with its internal CA (`tls internal`), but loses the no-warning property on machines that haven't trusted Caddy's root.
2. **Caddy as a separate process** — OK with the two-process shape (uvicorn HTTP on loopback, Caddy HTTPS on :443)? The alternative is in-process ACME which I'm recommending against.
3. **mkcert cert storage path** — `run/tls/{cert,key}.pem` cfg-stamped, or under `~/.local/share/recon-gen/tls/`? The `run/` path keeps it visible alongside `run/config.yaml`; XDG keeps it out of the gitignored work dir. I'm defaulting to `run/tls/` for visibility.
4. **Caddyfile ownership** — generator-owned (regen on every `bootstrap-shared`, operator never edits) or one-shot-then-operator-owned? I'm defaulting to generator-owned; operator escape hatch is "edit `run/tls/Caddyfile` and re-run with `--no-overwrite`".
5. **Renewal monitoring** — should `recon-gen tls status` exit non-zero when cert is <14 days from expiry, so it can be wired into a pre-deploy probe? Default yes.

## Out of scope

- HSTS / Strict-Transport-Security headers — DD's territory once cookies land.
- HTTP→HTTPS redirect at the uvicorn layer — Caddy handles this for the shared posture; local dev doesn't need it.
- Client cert / mTLS — not requested; far-future if it ever shows up.
- QuickSight embed TLS posture — owned by AWS, not us.
- Container/Kubernetes deploy shapes (cert-manager, Traefik) — bare-host is the only shared-deploy shape we support today.
- Windows operator path — local-dev assumes macOS/Linux; mkcert works on Windows but we don't test it.
