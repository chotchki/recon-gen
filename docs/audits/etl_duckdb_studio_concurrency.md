# ETL hook can't update DuckDB while Studio is running

**Status:** Option A implemented + verified 2026-06-08. Initial cut +
adversarial review (4 parallel lenses) + follow-up hardening pass
(typed `PoolReleasedDuringRefresh`; lifecycle lock for cross-handler
serialization; in-flight cursor drain in `close()`;
`released_for_subprocess` atomic bracket; CancelledError handling in
`step_1_etl_hook` to terminate orphan subprocess). 3452 unit tests
green.

**Filed:** 2026-06-08, chotchki's autonomous-run handoff prompt.

**Open follow-ups (filed as BX backlog):**

- Themed "data refresh in progress" 503 page for the
  `PoolReleasedDuringRefresh` exception (today it surfaces through
  the generic 500 handler).
- Configurable `step_1_etl_hook` subprocess timeout (today no
  timeout — a hung customer hook makes the lock-release window
  unbounded; operator can cancel via the existing
  `/etl/run/cancel` button, which now correctly terminates the
  orphan subprocess).
- Integration test exercising one of the 4 Studio route call sites
  end-to-end with a real DuckDB pool + TestClient.

## The bug

Operator-facing flow:

1. `recon-gen studio -c run/config.yaml` starts the Studio (Starlette
   app), which opens an `_AsyncDuckdbPool` against the demo DuckDB
   file. The pool's root connection (`common/db.py:1714`) holds the
   DuckDB process-level lock for the lifetime of the Studio process.
2. Operator clicks **Refresh Data** on the studio's ETL page →
   `POST /deploy` → `run_deploy_pipeline`.
3. The pipeline's `step_1_etl_hook` calls
   `asyncio.create_subprocess_exec(*shlex.split(cfg.etl_hook))`.
4. The subprocess imports `duckdb` and tries
   `duckdb.connect(<path>)`. DuckDB refuses:
   ```
   IO Error: Could not set lock on file "<path>": Conflicting lock
   is held in <python> (PID <studio>) by user. See also
   https://duckdb.org/docs/stable/connect/concurrency
   ```

Per the DuckDB docs (and verified by direct probe — see *Probe* §):
**only one process at a time may write to a `.duckdb` file**. Any
open handle in the parent process blocks subprocess connect — even
a `read_only=True` parent handle blocks subprocess WRITE (subprocess
read works fine).

## Probe (direct verification, 2026-06-08)

```python
import duckdb, subprocess, sys, tempfile
path = tempfile.mktemp(suffix=".duckdb")

seed = duckdb.connect(path)
seed.execute("CREATE TABLE t(x INTEGER); INSERT INTO t VALUES (42)")
seed.close()

# Parent holds writer
c1 = duckdb.connect(path)            # parent acquires lock
# Subprocess tries to write
subprocess.run([sys.executable, "-c", f"""
import duckdb
c = duckdb.connect({path!r})
c.execute('INSERT INTO t VALUES (99)')
"""])
# → IOException: Could not set lock on file ...
```

Results, summarised:

| Parent state               | Subprocess WRITE | Subprocess READ |
| -------------------------- | ---------------- | --------------- |
| writer open (default)      | **FAILS**        | **FAILS**       |
| read_only open             | **FAILS**        | works           |
| parent closed              | works            | works           |
| 2nd writer open (same PID) | N/A — works      | N/A             |

(The `_AsyncDuckdbPool` docstring at `common/db.py:1681` claims same-process
re-`connect()` raises `BinderException: Unique file handle conflict`.
The probe shows otherwise on the duckdb 1.x in `[project.dependencies]`
— two `duckdb.connect(path)` calls in one process succeed cleanly.
The constraint that *does* hold is the cross-process write-lock above.
The pool docstring should be updated when the fix lands; the pool's
"one root + cursor() per acquire" shape is still defensible for
single-handle resource discipline, just not load-bearing on a
hard DuckDB constraint.)

## Why the rest of the deploy pipeline works fine

`step_2_wipe`, `step_3_generator`, `step_4_matviews` all run in the
**same process** as the Studio pool. Same-process multi-connect works
(the probe confirms). They open a fresh `connect_demo_db(cfg)` inside
`asyncio.to_thread(...)`, do their TRUNCATE / INSERT / refresh, close
the connection. The pool's open handle doesn't block them.

`step_1_etl_hook` is the only step that spawns a separate process. Per
the BS.4 architecture shift — `cfg.etl_hook` is an arbitrary shell
exec, written by the customer's ETL team — the design is "give the
hook direct DuckDB access" rather than streaming data through Studio.
That direct access is precisely what the parent-process lock blocks.

## Fix shapes

### Option A — Release the lock around the subprocess (recommended)

Close the pool's root DuckDB connection before `step_1_etl_hook`'s
subprocess fires; reopen after. Per-dialect: only relevant when
`cfg.dialect is Dialect.DUCKDB`. PG / Oracle don't have this
constraint.

Surface changes:
- `run_deploy_pipeline(..., release_locks=None, reacquire_locks=None)`
  — optional async callbacks the caller provides. If both are set,
  pipeline calls `await release_locks()` before `step_1_etl_hook` and
  `await reacquire_locks()` after (always — both success and failure
  paths).
- POST /deploy passes callbacks bound to the Studio's pool:
  `release = pool.close`, `reacquire = lambda: pool.reopen()` (a new
  method we'd add to `_AsyncDuckdbPool`).
- During the window: dashboards return 503 / fall back to a "data
  refresh in progress" page. The deploy-progress endpoint already
  signals this to the open page via WebSocket events.

Trade-off: a 1-minute window where the dashboards return 503 during
an ETL hook run. Acceptable for the operator who explicitly clicked
*Refresh Data*; bad for unattended dashboard viewers if the operator
triggers Refresh during business hours.

### Option B — Force the etl_hook to use a subprocess connection helper

Provide a `recon-gen etl-write` CLI subcommand that connects in the
subprocess, accepts SQL on stdin, runs it. The customer's ETL hook
shells out to this. Studio's pool stays open; the lock conflict
moves to `recon-gen etl-write` itself, which can wait-with-backoff
for the parent to release.

Trade-off: the BS.4 lock states "etl_hook is an arbitrary shell exec"
— this option imposes a discipline (use our helper) on what the
customer ETL writes. Breaks BS.4 unless we make it strictly
opt-in (and Option A still needed for arbitrary hooks).

### Option C — Pipe SQL output from the etl_hook back into Studio

Customer's ETL hook emits SQL to stdout; Studio captures it + runs
the SQL through the pool's own connection. Lock never leaves
Studio's hands.

Trade-off: the customer ETL team has to know to emit SQL (not load
into DuckDB directly). Same break-BS.4 concern as Option B.

### Recommendation

**Option A**, with a 2-line `_AsyncDuckdbPool.reopen()` method. Smallest
change to the BS.4 contract; the 503 window is the same cost the
operator already implicitly accepts (they clicked Refresh Data, which
already wipes + rebuilds the demo DB). Production operators rarely
hit the demo dashboards mid-refresh anyway; this is a single-tenant
local dev tool, not a multi-tenant production dashboard.

## Proposed implementation sketch

```python
# common/db.py — extend _AsyncDuckdbPool
class _AsyncDuckdbPool:
    ...
    async def reopen(self) -> None:
        """Reopen the root connection after a close(). Used by the
        deploy pipeline to release + reacquire the DuckDB write lock
        across the etl_hook subprocess (which is a different process
        and would otherwise block on DuckDB's single-writer lock)."""
        import duckdb  # noqa: PLC0415
        if self._root is not None:
            return  # idempotent
        read_only = bool(RECON_GEN_DB_READ_ONLY.get_or_none())
        self._root = duckdb.connect(self._path, read_only=read_only)


# common/l2/deploy_pipeline.py — extend run_deploy_pipeline signature
async def run_deploy_pipeline(
    cfg, instance, *, dev_log=None, overlays=None,
    release_locks=None, reacquire_locks=None,
) -> DeploySummary:
    ...
    tx_del, bal_del = await step_2_wipe(pipeline_cfg, instance, dev_log=_tee)

    if release_locks is not None:
        await _emit(_tee, {"event": "deploy:locks:released"})
        await release_locks()
    try:
        rc = await step_1_etl_hook(pipeline_cfg, dev_log=_tee)
    finally:
        if reacquire_locks is not None:
            await reacquire_locks()
            await _emit(_tee, {"event": "deploy:locks:reacquired"})
    ...


# common/html/_studio_routes.py — wire the callbacks at /deploy
summary = await run_deploy_pipeline(
    patched_cfg, cache.get(), dev_log=_tee, overlays=overlays,
    release_locks=pool.close if pool is not None else None,
    reacquire_locks=pool.reopen if pool is not None else None,
)
```

## Testing

A unit test that:
1. Builds a tiny DuckDB file (`tempfile` + `duckdb.connect`).
2. Opens an `_AsyncDuckdbPool` against it.
3. Drives `run_deploy_pipeline` with a fake `cfg.etl_hook = "python -c
   <write a row>"` and the pool's close/reopen as the lock callbacks.
4. Asserts: subprocess exit code 0 (would be 1 without the fix), row
   was inserted, pool still works after pipeline returns (reopen
   succeeded).

A second test for the PG/Oracle dialect path that asserts the
callbacks are *not* invoked when dialect ≠ DUCKDB (the pool's lock
isn't relevant there; the subprocess connects independently via
psycopg/oracledb).

## Why I stopped at the audit

Per `[[feedback_autonomous_run_boundaries]]`: this is a judgment call
on architectural mechanics (lock lifecycle + cross-module callback
plumbing + 503 window during refresh). Stage on-branch, flag for
chotchki's sign-off on Option A vs. B vs. C, then implement.

A draft implementation of Option A is on this branch but not yet
committed beyond this audit. The probe + the diagnosis are
load-bearing; the fix shape's the right call to make at design time,
not autopilot.
