# EA.1 — is the self-hosted-runner rationale still load-bearing?

**Verdict: the ORIGINAL rationale is moot; the move to a GitHub-hosted runner is a capability YES, gated on three residual couplings — none of them AWS.** The self-hosted `recon-gen-ci-local` (WSL2 Ubuntu on a 5800X3D / 64GB box) was adopted when CI still had to reach QuickSight in us-east-1 (the `hotchkiss.io:5432` / `:1521` DDNS forwards let QS reach the dev-machine Docker). QS + the whole AWS footprint left in Phase DW (v16); `ci.yml` already runs `./run_tests.sh up_to=agreement` **fully local, AWS-free** — DB URLs are `127.0.0.1` containers, there are no AWS secrets, no boto, no `us-east-1`. So the reason the box EXISTS is gone. What's left is three couplings to untangle, in rising order of effort.

## Residual coupling 1 — App2 OIDC / TLS auth test (the real work)

The `app2` + `app2_browser` layers run App2's own login flow (DD.4): a Dex IdP container + HTTPS, configured in the CI cfg as

- `issuer_url: https://localci.recon-gen.hotchkiss.io:5556/dex`
- `redirect_uri: https://localci.recon-gen.hotchkiss.io:8765/auth/callback`
- `app2.tls.cert_path: ~/.local/share/recon-gen/tls/ci/cert.pem` + `account_email: ${{ secrets.TLS_ACME_EMAIL }}`

The cert is minted by `_dev/tls/ensure.py::ensure_dev_env` via **ACME DNS-01 over Cloudflare** (`RECON_GEN_CLOUDFLARE_TOKEN`, scope `Zone:DNS:Edit` on `hotchkiss.io`), firing only when `cfg.app2.tls` is set AND the target layer ∈ `{app2, app2_browser}`.

**Why this PORTS (and isn't a blocker):** DNS-01 is API-driven — it proves domain control by writing a TXT record via the Cloudflare API, so it does NOT require the runner itself to be publicly reachable. A GitHub-hosted runner with the `CLOUDFLARE_TOKEN` + `TLS_ACME_EMAIL` secrets can mint the same cert. The one box-ism is the hostname resolving to the local Dex/App2 — a `/etc/hosts` line (`127.0.0.1 localci.recon-gen.hotchkiss.io`) on the ephemeral runner closes that. **The cleaner alternative** the spike should weigh: drop ACME entirely for CI and generate a per-run self-signed cert for `localhost` (or `127.0.0.1.nip.io`) — no Cloudflare secret, no external dependency, one fewer thing that can rate-limit or expire. That's the EA.2 fork.

## Residual coupling 2 — persistent shared PG / Oracle containers

The box reuses `ci-shared-pg` / `ci-shared-oracle` across runs (the CB.17.f env-URL short-circuit tells `run_tests.sh` not to re-spin). Ephemeral cloud runners can't persist containers — each run spins fresh. That's *cleaner* (no cross-run state) but *slower*, and the Oracle Free container is the long pole (image pull + `FREEPDB1` boot). This is the EA.3 question: Oracle-container fit + xdist sizing + minutes cost against a fresh-every-run substrate.

## Residual coupling 3 — the CI-only trainer hang (a latent POLICY-1 violation the move may resolve)

`RECON_GEN_TRAINER_DIALECTS: "du"` pins the trainer dogfood tier to DuckDB because, on the box's **persistent** PG/Oracle containers, 14 trainer tests hang at `Page.wait_for_function: Timeout 600000ms` in the browser→HTTP→studio_server stack — reproducing in CI but NOT locally, untraceable from log-only triage (CE.4-followup-4). That is a "passes locally, fails on CI" bug living against the persistent-container substrate — exactly the class POLICY 1 says shouldn't exist. A fresh-ephemeral-container runner is a natural experiment: the hang may be persistent-container state (→ resolves for free on the move) or a genuine PG/Oracle-vs-DuckDB timing bug (→ reproduces on cloud, and we finally get a clean repro to trace). Either outcome is progress; the move should un-pin the trainer dialects and watch.

## What's explicitly OUT of scope

`demo-publish.yml` runs on a **different** self-hosted runner (`[self-hosted, mac-mini-demo]`) for demo publishing — not the CI test chain. EA touches `ci.yml` (+ `release.yml` parity per [[feedback_ci_release_workflow_parity]]); the Mac mini demo runner is its own decision. `pages.yml` + the `docs-portable-install` / `coverage-badge` jobs are already `ubuntu-latest`.

## Recommendation → EA.2

Migration is feasible; it is NOT a one-line `runs-on:` flip. Spike a GitHub-hosted `ubuntu-latest` run of the full `up_to=agreement` chain on a throwaway workflow (`workflow_dispatch`), resolving coupling 1 via the **self-signed-localhost** path first (cheapest, no Cloudflare dependency) and falling back to the Cloudflare-DNS-01 port only if the auth test genuinely needs the real hostname. Measure the wall time + peak RSS against the box's ~50 min / 90-min-cap baseline (EA.3). The trainer-dialect un-pin rides along as the coupling-3 experiment.
