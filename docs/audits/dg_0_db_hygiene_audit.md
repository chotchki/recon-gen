# DG.0 — CI database hygiene audit

**Date:** 2026-06-13
**Phase:** DG.0 (audit + lock fix shape)
**Status:** Audit drafted; locks pending operator confirmation.

## TL;DR

CI's multi-week red streak (`d09edd36` / `f62b8f2c` / `a27492ec` / `49a94a01` all failing on `CI` workflow) roots to two compounding bugs:

1. **`tests/e2e/_isolation.py:158` swallows teardown failures silently.** Per-(file, worker) prefixed schemas accumulate forever whenever a drop happens to fail; the next run's drops layer on top.
2. **`ci-shared-pg` / `ci-shared-oracle` containers persist across workflow runs.** Per `.github/workflows/ci.yml:477-490`, containers only get `docker rm`'d if THIS workflow run started them — adopted containers stay alive indefinitely, carrying every prior run's accumulated debris.

Symptom: `psycopg.errors.DiskFull: could not resize shared memory segment ... No space left on device` once cumulative working set exceeds PG's `shared_buffers` (1 GB) + temp file headroom on the runner volume.

**Oracle has the same code path.** Buying time on bigger default tablespace headroom + less frequent test fire-rate; not a different problem.

**Fix shape locked at this audit's end (pending operator confirm):**
- **DG.1** Fail-loud teardown — drop the `except Exception: print + swallow` for `raise`-and-collect.
- **DG.2** Container-boot scorched-earth sweep — drop every `_<suffix>` schema (PG) + every test user schema (Oracle) BEFORE the test layer fires. Idempotent.
- **DG.3** Triage the 12 v13.15.1-gate failures with hygiene fixed — separate cascade from genuine bugs.

## Inventory

### DB-touching fixtures + scopes

`grep -n "scope=" tests/e2e/conftest.py tests/e2e/_isolation.py`:

| Fixture | Scope | What it creates | Teardown contract |
|---|---|---|---|
| `isolated_cfg` | `module` | `<base>_<hash>` table prefix; `<deployment>-<hash>` QS deployment | `emit_schema_drop_sql` against the prefix, wrapped in `except Exception: print(...)` (silent) |
| `db_conn` | function | DB connection against `isolated_cfg` | close on `finally:`, no DB-state cleanup |
| `qs_driver` | session | QS embed | webkit browser teardown only |
| `app2 server` (live) | per-test | Starlette server bound to `isolated_cfg` | `App2Driver.serving()` ctx-manager closes server |

The bug is concentrated in `isolated_cfg`. Every other fixture either has no DB state (`qs_driver`) or trusts `isolated_cfg`'s prefix to disambiguate.

### Per-test prefix accumulation math

`_isolated_cfg_key()` builds: `sha256(nodeid|l2|dialect|worker_id)[:6]`.

- ~150 e2e test files
- xdist `-n 4` workers
- Each file's module gets ONE prefix per worker that touches it (but files are NOT pinned to a worker, so observed prefix count per CI run = files × workers in the worst case ≈ 600)

Even at the realistic average (each file fires on ~1-2 workers per run), a single run mints ~200-300 prefix sets. Each prefix-set creates ~30 objects: 2 base tables + 2 Current* matviews + ~12 L1 invariant matviews + ~6 Inv matviews + typed config views + indexes.

**~6,000-10,000 PG objects per run.** Multiply by N runs since container last `docker rm`'d (often days/weeks of CI history).

### The silent-swallow site

`tests/e2e/_isolation.py:141-162`:

```python
try:
    from recon_gen.common.db import connect_demo_db, execute_script
    from recon_gen.common.l2 import default_l2_instance
    from recon_gen.common.l2.schema import emit_schema_drop_sql
    instance = default_l2_instance()
    teardown_conn = connect_demo_db(isolated)
    try:
        clean_sql = emit_schema_drop_sql(
            instance,
            prefix=isolated.db_table_prefix,
            dialect=isolated.dialect,
        )
        with teardown_conn.cursor() as cur:
            execute_script(cur, clean_sql, dialect=isolated.dialect)
        teardown_conn.commit()
    finally:
        teardown_conn.close()
except Exception as exc:  # noqa: BLE001
    print(
        f"isolated_cfg teardown[{suffix}]: best-effort drop failed: "
        f"{exc!r}"
    )
```

Three failure modes the swallow hides:

- **Connection failure on teardown** (PG is hot — out of connections, out of disk, OOM in progress) → no drop fires.
- **Cross-test L2 drift.** `default_l2_instance()` reads CURRENT HEAD's L2. If yesterday's run created prefix-objects from an L2 that since had a Rail removed, today's `emit_schema_drop_sql` doesn't know about the removed Rail's matviews → orphan rows in `pg_class`.
- **Partial-success on multi-statement script.** `execute_script` runs the entire drop-SQL string; if one DROP raises mid-script, subsequent DROPs don't fire (and the exception is swallowed silently).

### The container-reuse site

`.github/workflows/ci.yml:477-490`:

```yaml
- name: Stop shared PG + Oracle containers (only if this run started them)
  if: always()
  run: |
    if [ "${{ steps.ensure_dbs.outputs.started_pg }}" = "true" ]; then
      docker stop ci-shared-pg 2>/dev/null || true
      docker rm ci-shared-pg 2>/dev/null || true
    fi
    if [ "${{ steps.ensure_dbs.outputs.started_or }}" = "true" ]; then
      docker stop ci-shared-oracle 2>/dev/null || true
      docker rm ci-shared-oracle 2>/dev/null || true
    fi
```

`started_pg` is set TRUE only when CI couldn't find an existing container on port 5432 — i.e., a fresh boot. On the self-hosted runner the container is almost always already up from the previous run → `started_pg=false` → no teardown → container persists with all yesterday's prefixes.

### Oracle equivalent

`emit_schema_drop_sql` is dialect-aware (calls `drop_*_if_exists(..., dialect)`); the silent-swallow wrapper around it is dialect-agnostic — same code path. Oracle's failure mode would be:

- `ORA-01000: maximum open cursors exceeded` (cumulative leaked cursors from killed teardowns)
- `ORA-01652: unable to extend temp segment` (tablespace exhaustion)
- `ORA-00018: maximum number of sessions exceeded` (cumulative leaked sessions)

Oracle's tablespace headroom in `recon-gen/oracle-19c:local` is bigger than PG's `shared_buffers=1GB`, plus Oracle e2e tests fire less often in the full chain, plus Oracle's failure modes surface with louder error codes (operators notice sooner). Hasn't tipped over yet — same bug.

## Locks (operator-confirmed 2026-06-13)

1. **Fail-loud teardown: (b) collect + report at session end + EXIT NON-ZERO.** A teardown failure is still a CI-blocking failure; the deferral is purely about preserving signal order (real test failures above, hygiene failures in their own summary block at the bottom). Per operator: "still needs to be a failure so it doesn't get ignored and blow up the next run." Implementation: pytest `pytest_sessionfinish` collects accumulated teardown failures + sets `session.exitstatus = pytest.ExitCode.TESTS_FAILED` (or similar non-zero code) when the failure list is non-empty.

2. **Sweep wired as a runner step in `run_tests.sh up_to=db` prelude.** Visible in CI logs as a discrete step; doesn't depend on test-collection succeeding; idempotent + safe on a fresh container.

3. **No `VACUUM FULL`.** Operator: "the underlying disk on the server is fine, so whatever is fastest is what I'd lean towards." The `DiskFull` error was about PG's POSIX shared memory segment (`/dev/shm` is a tmpfs constrained per-container, ~64MB default), not host disk. PG reclaims pages lazily but the concurrent-query working set is what hits the limit. Sweep DROPs the accumulated objects (so connections don't have to scan their definitions) — that's enough to keep `/dev/shm` from saturating. Skipping VACUUM keeps boot-prelude fast.

4. **Don't switch to per-worker DBs.** Considered (each xdist worker gets its own `recon_worker_<n>` DB; CREATE/DROP on boot). Rejected: triples test boot time + breaks the existing `cfg.db.url` contract every layer expects.

5. **Out-of-scope-but-noted: bump container `--shm-size`.** `.github/workflows/ci.yml:241-248` runs `docker run -d --name ci-shared-pg` without `--shm-size`, inheriting the 64MB default. A `--shm-size=2g` bump on container creation would raise the ceiling independent of the sweep work. Files as a follow-up after DG.2 ships — the sweep is the right architectural fix; shm-size is the belt-and-suspenders.

## DG.1+ unblock

DG.1 (fail-loud teardown) + DG.2 (boot sweep) can land in parallel. DG.3 (triage v13.15.1 failures) waits for DG.1/.2 to land + CI to fire on a clean container.
