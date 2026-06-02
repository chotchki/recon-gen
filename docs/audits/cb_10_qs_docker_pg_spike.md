# CB.10 spike — QuickSight ↔ Docker PostgreSQL via hotchkiss.io DDNS

**Outcome: ✅ working shape locked.** QS `us-east-1` connects to PostgreSQL running inside WSL2 Docker on the self-hosted runner at `hotchkiss.io:5432` in ~2 seconds, infers column types correctly, supports the full delete-then-create credential-rotation loop.

**Big bonus signal**: the entire validation (Docker PG, WSL portproxy, Windows Firewall rule) was performed on the **GitHub self-hosted runner itself** — the same machine that will host CI cells. This means CB.11 (wire bridge into runner / CI) has effectively zero infrastructure gap to bridge between the spike environment and the production CI cell environment. The runner is already configured for the working shape.

## Working shape

| Layer | Setting |
|---|---|
| Docker | `docker run -d -e POSTGRES_PASSWORD=... -p 0.0.0.0:5432:5432 postgres:17-alpine` (vanilla Docker apt-installed inside WSL2 Ubuntu, not Docker Desktop) |
| WSL2 | Mirrored networking mode (Win11 22H2+). Docker `-p` bind reaches Windows on `127.0.0.1` automatically. |
| Windows | `netsh interface portproxy add v4tov4 listenport=5432 listenaddress=0.0.0.0 connectport=5432 connectaddress=127.0.0.1` |
| Windows Firewall | `New-NetFirewallRule -DisplayName "WSL Postgres 5432" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5432` |
| Router | QS regional egress IP range whitelisted (forwarded to Windows runner per [[project_cb10_qs_to_docker_pg_constraints]]) |
| DNS | `hotchkiss.io` apex A-record points at the runner's WAN IP (already in place) |
| QS data source | `Host=hotchkiss.io Port=5432 Database=<db> SslMode=disabled` with raw username/password credentials |

## Measured timings (run on 2026-06-02)

| Stage | Time |
|---|---|
| `create_data_source` → `CREATION_SUCCESSFUL` (poll interval 1s) | 2s |
| `create_data_set` + `describe_data_set` (column inference) | <1s |
| `delete_data_set` + `delete_data_source` (cleanup) | <1s |
| **Full delete-then-create cycle** | **~3s** |

The 3s cycle time fits comfortably inside the CI budget for per-boot credential rotation per [[project_cb10_qs_to_docker_pg_constraints]] (the design that each CI cell rotates the PG password + recreates the QS data source per boot).

## What we proved

- **Network path works**: `QS us-east-1 → public internet → router → portproxy → Docker → PG` end-to-end.
- **SSL disabled is sufficient**: the working shape uses `SslProperties={"DisableSsl": True}`. Cert chains are not in the critical path. TLS hardening is a follow-up if/when needed; not a blocker for CB.12 (Aurora drop).
- **Column inference is correct**: QS read `INT / TEXT / TIMESTAMP` from the probe schema and inferred `INTEGER / STRING / DATETIME` data set types. No type-mapping surprises.
- **Cleanup is fast + reliable**: `delete_data_source` succeeds immediately even on a freshly-created source. The per-boot rotate-and-recreate loop has no resource leak risk.

## Earlier failure modes (for triage memory)

Two `CREATION_FAILED` runs before the working shape:

1. **First run** — `GENERIC_SQL_FAILURE / "The connection attempt failed."` (32s timeout). Cause: Docker PG not yet started inside WSL.
2. **Second run** — same error. Cause: Docker PG up, but bound only to `127.0.0.1` on Windows (WSL2 mirrored networking exposes Docker `-p` to localhost only by default). Windows portproxy + firewall rule fixed it.

The QS error message is opaque ("The connection attempt failed") — does not distinguish "no route" vs "connection refused" vs "TLS handshake failure" vs "auth rejected." If a future CB.10-style failure surfaces, packet capture on the Windows side (`Get-NetTCPConnection`, `tcpdump` inside WSL) will narrow the cause faster than QS's `ErrorInfo`.

## Caveats + follow-ups

1. **The portproxy line hardcodes `127.0.0.1` as the connect address** (not WSL2's dynamic IP). That's safe because WSL2 mirrored networking ensures `127.0.0.1` on Windows always reaches WSL Docker's `-p` binding. Survives WSL restarts; doesn't need re-derivation.
2. **The firewall rule is `Profile: Any`** — accepts inbound from any network profile (Domain / Private / Public). If you switch the runner to a network whose default profile is Public, this still allows. Tighten via `-Profile Private` if the runner only sits on a known network.
3. **TLS is a separate hardening task.** The current shape sends username/password in cleartext over the WAN. Risk surface: anyone on the path between QS egress IPs and the runner WAN IP could MITM. Mitigations to consider in CB.10-followup: (a) Caddy-fronted PG with Let's Encrypt cert + `SslProperties={"DisableSsl": False}`, (b) self-signed cert with QS's cert-trust manually configured, (c) restrict inbound TCP 5432 to QS's regional egress IPs only (already implicitly done via the router whitelist, but Windows Firewall could add a defense-in-depth `-RemoteAddress` clause).
4. **`make_studio_cfg` / `connect_demo_db` integration**: this spike validated the raw QS-side flow via `boto3`. CB.11 (wire bridge into runner / CI) is the next task — make the cells boot Docker PG on the Windows runner, randomize the password, create the QS data source via `boto3`, expose the connection details via `RECON_GEN_DEMO_DATABASE_URL`, run e2e tests, delete-then-cleanup at end of cell.
5. **Stale CI data sources**: 12 `qs-ci-*` data sources were present in the QS account at spike time, all `CREATION_SUCCESSFUL` and presumably from old CI runs. CB.11 should sweep them as part of the cleanup step. The current QS quota is 50/account; we have headroom but it would compound under heavy CI run rates.

## Artifacts

- `scripts/cb10_qs_data_source_probe.py` — re-runnable boto3 probe; serves as the executable spec for CB.11's integration.
- `[[project_cb10_qs_to_docker_pg_constraints]]` (memory) — operator's pre-spike design decisions; this doc supersedes / confirms.
- Console screenshot of the QS UI's "New PostgreSQL data source" form (operator-captured 2026-06-02; not committed) — showed `Enable SSL` as a checkbox, which was the key signal that we didn't need to solve cert chains in this spike.
