"""Studio "Deploy changes" pipeline (X.4.g + BS.4 architecture shift).

Four-step orchestration that takes the operator's current `cfg` +
`L2Instance` and refreshes the demo DB so the dashboards re-render
against the new shape:

1. **wipe** — TRUNCATE `<prefix>_transactions` + `<prefix>_daily_balances`
   so step 2's etl_hook + step 3's generator both write into clean state.
2. **etl_hook** — run `cfg.etl_hook` as a subprocess. The hook is
   expected to write directly to demo_db (the BS.4 contract — see
   `docs/audits/_archive/bs_4_arch_shift_spike.md`). Non-zero exit halts the
   pipeline before steps 3-5 run; demo_db is left in whatever partial
   state the hook produced (operator re-runs after fixing the hook).
3. **generator** — `emit_full_seed` against the current
   `cfg.test_generator` knobs; always additive overlay on top of the
   etl_hook's rows.
4. **matview refresh** — existing `refresh_matviews_sql(instance)`.
5. **reload** — bump `data_generation_id`; Dashboards' open page
   polls and reloads its current URL.

BS.4 (2026-05-29) collapsed the pre-existing
`upstream → demo_db → matview refresh` model to
`truncate(demo_db) → ETL hook → matview refresh`. The legacy
`step_2_pull` (cross-dialect copy from `cfg.etl_datasource`) was
deleted along with `EtlDatasourceConfig`; etl_hook is the only
ETL-load contract now. The step ordering also flipped — wipe now runs
BEFORE etl_hook (the hook writes into a clean demo_db, not into a
parallel upstream that gets copied over).

This module is HTTP-free. The studio's `POST /deploy` endpoint wires
up a `DevLogWriter` that emits via `_DEVLOG.info`; tests wire one
that appends to a list.
"""
from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from recon_gen.common.db import (
    connect_demo_db,
    execute_script,
    fetch_one_required,
)
from recon_gen.common.l2.primitives import Identifier
from recon_gen.common.l2.schema import (
    emit_schema,
    refresh_matviews_sql,
    wipe_demo_data_sql,
)
from recon_gen.common.sql import Dialect

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2.pipeline_overlays import PipelineOverlays
    from recon_gen.common.l2.primitives import L2Instance


# A function the pipeline calls to surface progress / errors. Each
# event is a JSON-serializable mapping with at minimum an ``event``
# key (string identifier like ``deploy:step1:start``) so consumers
# can switch on it. ``None`` disables emission entirely.
DevLogWriter = Callable[[Mapping[str, object]], Awaitable[None]]


async def _emit(
    dev_log: DevLogWriter | None, payload: Mapping[str, object],
) -> None:
    """Emit a deploy event with a wall-clock timestamp.

    BTa.6 — every event carries ``ts_unix`` (float seconds since
    epoch) so the run-log renderer can compute per-step durations
    by diffing consecutive events. Caller-provided ``ts_unix``
    (uncommon) wins; usually we stamp at call time.
    """
    if dev_log is None:
        return
    if "ts_unix" not in payload:
        import time  # noqa: PLC0415

        stamped: dict[str, object] = {"ts_unix": time.time(), **payload}
        await dev_log(stamped)
        return
    await dev_log(payload)


async def step_1_etl_hook(
    cfg: Config,
    *,
    dev_log: DevLogWriter | None = None,
) -> int:
    """Run ``cfg.etl_hook`` as a subprocess; stream output to ``dev_log``.

    Returns the subprocess exit code, OR 0 when ``cfg.etl_hook`` is
    unset / empty (no-op skip). Caller checks the return value and
    halts the pipeline if non-zero — steps 3-5 (generator overlay,
    matview refresh, reload) MUST NOT run when the operator's ETL
    failed.

    BS.4 (2026-05-29) reordered the pipeline so the wipe runs BEFORE
    this step (the hook writes directly to demo_db, not to a parallel
    upstream that gets copied over). On etl_hook failure, demo_db is
    left in whatever partial state the hook produced — operators
    re-run after fixing the hook (recommend wrapping hook writes in a
    transaction so partial writes roll back automatically).

    The command is ``shlex.split`` then run via
    ``asyncio.create_subprocess_exec`` (NOT ``shell=True``). Stdout
    and stderr stream line-by-line as ``deploy:step1:stdout`` /
    ``deploy:step1:stderr`` events so the operator watches progress
    in the studio's dev_log overlay rather than waiting for the
    subprocess to drain.

    A missing binary (``FileNotFoundError`` from
    ``create_subprocess_exec``) propagates — the caller surfaces it
    as an actionable error, NOT a silent skip. The whole point of
    declaring an ``etl_hook`` is that it MUST run.
    """
    if cfg.etl_hook is None:
        await _emit(dev_log, {
            "event": "deploy:step1:skip",
            "reason": "etl_hook not configured",
        })
        return 0
    cmd = shlex.split(cfg.etl_hook)
    if not cmd:
        await _emit(dev_log, {
            "event": "deploy:step1:skip",
            "reason": "etl_hook is empty after shlex split",
        })
        return 0

    await _emit(dev_log, {
        "event": "deploy:step1:start",
        "cmd": list(cmd),
    })
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _stream(
        stream: asyncio.StreamReader | None, channel: str,
    ) -> None:
        if stream is None:
            return
        # readline() returns b"" on EOF; readuntil(LF) raises at EOF.
        # The ``async for`` over a StreamReader yields chunks split on
        # newline and stops at EOF — that's what we want.
        async for raw_line in stream:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
            await _emit(dev_log, {
                "event": f"deploy:step1:{channel}",
                "line": line,
            })

    # CS.9 — wrap the subprocess + stream-gather in asyncio.wait_for
    # so a runaway etl_hook can't hang the studio session forever.
    # Default 600s (10 min); operator override via env. Timeout fires
    # a clean terminate→kill ladder just like the cancellation path
    # below, and returns a distinctive non-zero rc (124, matching
    # GNU `timeout(1)` convention) so callers can distinguish
    # "hook exited with error" from "hook never finished".
    from recon_gen.common.env_keys import (  # noqa: PLC0415
        RECON_GEN_STUDIO_ETL_HOOK_TIMEOUT_SECS,
    )
    _ETL_HOOK_TIMEOUT_SECS_DEFAULT = 600
    timeout_override = RECON_GEN_STUDIO_ETL_HOOK_TIMEOUT_SECS.get_or_none()
    timeout_secs = (
        int(timeout_override) if timeout_override is not None
        else _ETL_HOOK_TIMEOUT_SECS_DEFAULT
    )

    try:
        async def _run() -> int:
            await asyncio.gather(
                _stream(proc.stdout, "stdout"),
                _stream(proc.stderr, "stderr"),
            )
            return await proc.wait()
        try:
            rc = await asyncio.wait_for(_run(), timeout=timeout_secs)
        except asyncio.TimeoutError:
            await _emit(dev_log, {
                "event": "deploy:step1:timeout_terminating_subprocess",
                "pid": proc.pid,
                "timeout_secs": timeout_secs,
            })
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # already exited
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                await _emit(dev_log, {
                    "event": "deploy:step1:timeout_killing_subprocess",
                    "pid": proc.pid,
                })
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            rc = 124  # GNU timeout(1) convention — distinguishable from any normal exit
    except asyncio.CancelledError:
        # CO.x — operator cancelled (POST /etl/run/cancel) mid-
        # subprocess. WITHOUT terminating the orphan subprocess
        # explicitly, it keeps the DuckDB file lock; the surrounding
        # released_for_subprocess bracket would then fail on reopen
        # with `IO Error: Could not set lock on file`, leaving the
        # pool closed indefinitely — every dashboard 500s until
        # Studio restart. Terminate gently first (SIGTERM gives the
        # ETL hook a chance to flush / close); fall back to a hard
        # kill if it doesn't exit within a small grace window.
        await _emit(dev_log, {
            "event": "deploy:step1:cancelled_terminating_subprocess",
            "pid": proc.pid,
        })
        try:
            proc.terminate()
        except ProcessLookupError:
            pass  # already exited
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            await _emit(dev_log, {
                "event": "deploy:step1:cancelled_killing_subprocess",
                "pid": proc.pid,
            })
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        raise
    await _emit(dev_log, {
        "event": "deploy:step1:done",
        "exit_code": rc,
    })
    return rc


async def step_2_wipe(
    cfg: Config,
    instance: L2Instance,
    *,
    dev_log: DevLogWriter | None = None,
) -> tuple[int, int]:
    """Empty ``<prefix>_transactions`` + ``<prefix>_daily_balances``.

    BS.4 (2026-05-29) — runs FIRST in the pipeline so the etl_hook
    (step 1) and the synthetic-data generator (step 3) both write into
    clean state. Pre-BS.4 this ran AFTER step 1's etl_hook (the hook
    was assumed to write to upstream and step_2_pull copied to demo);
    the pull step is gone and the order swapped accordingly. The
    matview re-derive is step 4's job.

    Returns ``(transactions_deleted, daily_balances_deleted)`` row
    counts so the caller can surface "wiped 12,345 transactions" in
    the deploy summary.

    Sync DB-API 2.0 work runs in ``asyncio.to_thread`` so it doesn't
    block the asyncio loop — the studio's POST /deploy endpoint
    otherwise stalls all other requests for the wipe duration on
    a multi-million-row demo DB.
    """
    sql = wipe_demo_data_sql(instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect)
    await _emit(dev_log, {
        "event": "deploy:step2:wipe:start",
        "db_table_prefix": cfg.db_table_prefix,
        "dialect": cfg.dialect.value,
    })

    # BX backlog #173 (2026-06-05) — Studio's Session Start lands here
    # on a fresh DuckDB / Postgres / Oracle that's never been seeded.
    # Pre-fix the WIPE blew up with a CatalogException / UndefinedTable
    # ("table ``<prefix>_transactions`` does not exist") and the
    # operator had to drop to the CLI for ``recon-gen schema apply
    # --execute`` before re-trying — an unrecoverable UI dead-end. The
    # probe below catches the missing-base case and emits the full
    # schema in-place so Session Start is self-sufficient on a virgin
    # DB. Populated DBs cost one no-op SELECT.
    schema_emitted: bool = False

    def _run_wipe() -> tuple[int, int]:
        nonlocal schema_emitted
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                p = cfg.db_table_prefix  # Z.C — was instance.instance
                try:
                    # Cheap probe: SELECT * FROM ... WHERE 1=0 returns
                    # zero rows on every dialect we target but raises a
                    # catalog-shaped error when the table is missing.
                    cur.execute(f"SELECT * FROM {p}_transactions WHERE 1=0")
                    cur.fetchall()
                except Exception:  # noqa: BLE001 — dialect-specific catalog errors are not a shared exception type (DuckDB CatalogException / psycopg2 UndefinedTable / cx_Oracle ORA-00942 all subclass different bases)
                    # Base tables absent — emit the full schema.
                    schema_sql = emit_schema(
                        instance,
                        prefix=p,
                        dialect=cfg.dialect,
                    )
                    execute_script(cur, schema_sql, dialect=cfg.dialect)
                    conn.commit()
                    schema_emitted = True
                # Count first so the dev_log can report what was wiped.
                cur.execute(f"SELECT COUNT(*) FROM {p}_transactions")
                tx_count = int(fetch_one_required(cur)[0])
                cur.execute(f"SELECT COUNT(*) FROM {p}_daily_balances")
                bal_count = int(fetch_one_required(cur)[0])
                execute_script(cur, sql, dialect=cfg.dialect)
                conn.commit()
                return tx_count, bal_count
            finally:
                cur.close()
        finally:
            conn.close()

    tx_count, bal_count = await asyncio.to_thread(_run_wipe)
    if schema_emitted:
        await _emit(dev_log, {
            "event": "deploy:step2:schema_emitted",
            "reason": "base tables missing — auto-emitted via emit_schema",
        })
    await _emit(dev_log, {
        "event": "deploy:step2:wipe:done",
        "transactions_deleted": tx_count,
        "daily_balances_deleted": bal_count,
    })
    return tx_count, bal_count


# BS.4 (2026-05-29) removed step_2_pull + EtlDatasourceConfig +
# the cross-dialect upstream→demo_db copy path. The etl_hook is the
# only ETL-load contract now — it writes directly to demo_db after
# step 1's wipe. See docs/audits/_archive/bs_4_arch_shift_spike.md.


# Step 3 generator: synthetic data overlay.

async def step_3_generator(
    cfg: Config,
    instance: L2Instance,
    *,
    dev_log: DevLogWriter | None = None,
) -> tuple[int, int]:
    """Run the synthetic-data generator, execute its SQL against the
    demo DB, return per-base-table row counts.

    X.4.g.7 — scaffolding. Honors ``cfg.test_generator``:
      - ``enabled = False`` ⇒ skip event + return ``(0, 0)``.
      - ``scope = "full"`` ⇒ X.4.g.8 — `build_full_seed_sql` (today's
        behavior). Byte-identical to the locked seeds when ``etl_hook``
        is absent (or a no-op) AND knobs at defaults.
      - ``scope = "exceptions_only"`` ⇒ ships in X.4.g.9.
      - ``scope = "uncovered_rails"`` ⇒ ships in X.4.g.10.

    Always *additive* — runs after step 2's wipe + optional pull, so
    the generator's INSERTs land on top of whatever step 2 left in the
    base tables. The returned counts are the *post-step-3* totals
    (not the delta), so the deploy summary can report "ended with
    12,345 transactions".
    """
    tg = cfg.test_generator
    if not tg.enabled:
        await _emit(dev_log, {
            "event": "deploy:step3:generator:skip",
            "reason": "test_generator.enabled is False",
        })
        return 0, 0

    await _emit(dev_log, {
        "event": "deploy:step3:generator:start",
        "scope": tg.scope,
        "end_date": tg.end_date.isoformat() if tg.end_date else None,
        "seed": tg.seed,
    })

    sql = _build_generator_sql(cfg, instance)

    def _run_apply() -> tuple[int, int]:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                execute_script(cur, sql, dialect=cfg.dialect)
                conn.commit()
                p = cfg.db_table_prefix  # Z.C — was instance.instance
                cur.execute(f"SELECT COUNT(*) FROM {p}_transactions")
                tx = int(fetch_one_required(cur)[0])
                cur.execute(f"SELECT COUNT(*) FROM {p}_daily_balances")
                bal = int(fetch_one_required(cur)[0])
                return tx, bal
            finally:
                cur.close()
        finally:
            conn.close()

    tx, bal = await asyncio.to_thread(_run_apply)
    await _emit(dev_log, {
        "event": "deploy:step3:generator:done",
        "transactions_written": tx,
        "daily_balances_written": bal,
    })
    return tx, bal


def _build_generator_sql(cfg: Config, instance: L2Instance) -> str:
    """Pick the SQL builder for ``cfg.test_generator.scope``.

    Split out so unit tests can exercise the dispatch + the NotImplemented
    fences without going through ``connect_demo_db``.

    When ``cfg.test_generator.cutoff_date`` is set (Studio trainer mode
    via ``cache.patched_config``), append DELETE statements after the
    generator's emit so rows past the cutoff get pruned. Lets the
    trainer scrub a cutoff inside a fixed scenario window without
    perturbing plant calendar positions. Default None (CLI invocations
    + Studio when up_to == window_end) ⇒ no truncation, byte-identical
    to legacy emit.
    """
    sql = _emit_scope_sql(cfg, instance)
    cutoff = cfg.test_generator.cutoff_date
    if cutoff is not None:
        # Trim to date <= cutoff. transactions.posting is TIMESTAMP,
        # daily_balances.business_day_start is TIMESTAMP — both compared
        # against the midnight-of-next-day bound. CT.0 — use
        # `date_literal()` so Oracle accepts the comparand (bare ISO
        # strings hit ORA-01843 on TIMESTAMP columns; PG + DuckDB
        # accept the same typed literal).
        from recon_gen.common.sql.dialect import date_literal  # noqa: PLC0415
        prefix = cfg.db_table_prefix
        next_day = (cutoff + timedelta(days=1)).isoformat()
        next_day_lit = date_literal(next_day, cfg.dialect)
        sql += (
            f"\n-- X.4.h trainer cutoff: prune rows past {cutoff.isoformat()}\n"
            f"DELETE FROM {prefix}_transactions "
            f"WHERE posting >= {next_day_lit};\n"
            f"DELETE FROM {prefix}_daily_balances "
            f"WHERE business_day_start >= {next_day_lit};\n"
        )
    return sql


def _emit_scope_sql(cfg: Config, instance: L2Instance) -> str:
    """Inner dispatch — picks the per-scope SQL emitter without the
    cutoff post-processing. Split from ``_build_generator_sql`` so the
    cutoff truncation lives in exactly one place regardless of scope.
    """
    scope = cfg.test_generator.scope
    if scope == "full":
        # X.4.g.8 — full scope. ``build_full_seed_sql`` is the same
        # entry point ``data apply --execute`` already uses, so the
        # locked-seed determinism contract carries over: no
        # ``etl_hook`` (post-BS.4 the only ETL knob) + default
        # test_generator knobs ⇒ byte-identical to
        # ``tests/data/_locked_seeds/<inst>.<dialect>.sql``.
        # build_full_seed_sql still carries a no-untyped-def waiver
        # (CLI-wide typing sweep is a separate task per its own
        # ignore comment); the call is still by-position-correct.
        from recon_gen.cli._helpers import build_full_seed_sql  # pyright: ignore[reportUnknownVariableType]  # WHY: helper has pending untyped-def waiver in cli/_helpers.py
        return build_full_seed_sql(  # pyright: ignore[reportUnknownVariableType]  # WHY: same helper-untyped waiver propagates to the call expression
            cfg, instance,
            anchor=cfg.test_generator.end_date,
            plants=cfg.test_generator.plants or None,  # X.4.h.0.a — None ⇒ all kinds (locked-seed default)
            base_seed=cfg.test_generator.seed,  # X.4.h.0.b — None ⇒ _BASELINE_BASE_SEED (locked-seed default)
        )
    if scope == "exceptions_only":
        # X.4.g.9 — plants only, no baseline. The integrator's data
        # already lives in the demo DB (via the BS.4 etl_hook that ran
        # in step 1); we just lay the L1/Investigation exception
        # scenarios on top so the dashboards render planted violations
        # against their data. ``emit_seed`` is the plants-only emitter
        # that ``emit_full_seed`` wraps with a baseline; calling it
        # directly skips the 90-day baseline insert.
        from recon_gen.cli._helpers import build_default_scenario  # pyright: ignore[reportUnknownVariableType]  # WHY: helper has pending untyped-def waiver in cli/_helpers.py
        from recon_gen.common.l2.seed import emit_seed
        scenario = build_default_scenario(  # pyright: ignore[reportUnknownVariableType]  # WHY: same helper-untyped waiver propagates to the call expression
            instance,
            anchor=cfg.test_generator.end_date,
            plants=cfg.test_generator.plants or None,  # X.4.h.0.a — None ⇒ all kinds
        )
        return emit_seed(instance, scenario, prefix=cfg.db_table_prefix, dialect=cfg.dialect)  # pyright: ignore[reportUnknownArgumentType]  # WHY: build_default_scenario returns untyped-def ScenarioPlant per the same waiver
    if scope == "uncovered_rails":
        # X.4.g.10 — fill baseline only for rails the operator's
        # external DB hasn't already populated (via step 2's pull).
        # Inspect <prefix>_transactions for distinct rail_name values
        # — that's the covered set; emit baseline for everything else.
        # No plants in this mode: the operator's data is what they want
        # to see; we just patch the gaps so dashboards aren't empty.
        from recon_gen.common.l2.seed import emit_baseline_seed
        covered = _covered_rail_names(cfg, instance)
        return emit_baseline_seed(
            instance,
            prefix=cfg.db_table_prefix,
            anchor=cfg.test_generator.end_date,
            dialect=cfg.dialect,
            skip_rails=covered,
            base_seed=cfg.test_generator.seed,  # X.4.h.0.b — None ⇒ _BASELINE_BASE_SEED
        )
    if scope == "only_template":
        # X.4.i.1 — emit baseline restricted to a single TransferTemplate's
        # leg-rails dependency closure. Per the closure-scope decision:
        # closure = template.leg_rails (no LimitSchedule pull-in, no Chain
        # pull-in). Template name comes from cfg.test_generator.only_template
        # — required field for this scope; loud-fail when missing.
        from recon_gen.common.l2.seed import emit_baseline_seed
        template_name = cfg.test_generator.only_template
        if not template_name:
            raise ValueError(
                "scope='only_template' requires "
                "cfg.test_generator.only_template to name a TransferTemplate "
                "in the L2 instance.",
            )
        only_rails = _only_template_rails(template_name, instance, cfg=cfg)
        baseline = emit_baseline_seed(
            instance,
            prefix=cfg.db_table_prefix,
            anchor=cfg.test_generator.end_date,
            dialect=cfg.dialect,
            only_rails=only_rails,
            base_seed=cfg.test_generator.seed,
        )
        # Plants: respect cfg.test_generator.plants (operator-set tuple).
        # Default `()` → no plants (preserves locked-seed determinism on
        # a fresh only_template deploy). When the trainer flips plants on,
        # the scenario primitive plants for ALL kinds (filtered by the
        # tuple) but the SCENARIO's per-plant rail_name lookup naturally
        # narrows to in-closure plants — out-of-closure rails won't have
        # baseline rows for the planted scenario to attach to.
        plants_tuple = cfg.test_generator.plants
        if not plants_tuple:
            return baseline
        # Compose: baseline closure + plants. emit_seed appends to the
        # same INSERT script — concatenation is the same shape
        # `emit_full_seed` uses internally.
        from recon_gen.cli._helpers import build_default_scenario  # pyright: ignore[reportUnknownVariableType]  # WHY: helper has pending untyped-def waiver in cli/_helpers.py
        from recon_gen.common.l2.seed import emit_seed
        scenario = build_default_scenario(  # pyright: ignore[reportUnknownVariableType]  # WHY: same helper-untyped waiver propagates to the call expression
            instance,
            anchor=cfg.test_generator.end_date,
            plants=plants_tuple,
        )
        plants_sql = emit_seed(instance, scenario, prefix=cfg.db_table_prefix, dialect=cfg.dialect)  # pyright: ignore[reportUnknownArgumentType]  # WHY: build_default_scenario returns untyped-def ScenarioPlant per the same waiver
        return baseline + "\n" + plants_sql
    # Defensive — Literal[ScopeKind] should make this unreachable.
    raise ValueError(f"Unknown test_generator.scope: {scope!r}")


def _only_template_rails(
    template_name: str, instance: L2Instance, *, cfg: Config,
) -> frozenset[Identifier]:
    """X.4.i.1 — return the leg_rails closure for the named template.

    Closure = template.leg_rails (per design decision: leg-rails + their
    accounts only, no LimitSchedule pull-in, no Chain pull-in). The
    AccountTemplate roles those rails name don't need explicit pull-in:
    `_materialize_baseline_template_instances` always materializes the
    full per-template instance set, and `emit_baseline_seed`'s per-rail
    loop only consults the templates whose roles its rails reference.
    Loud-fail when the template name doesn't exist in the L2 — better
    to halt the deploy than silently emit an empty closure.
    """
    template = next(
        (t for t in instance.transfer_templates if str(t.name) == template_name),
        None,
    )
    if template is None:
        declared = sorted(str(t.name) for t in instance.transfer_templates)
        raise ValueError(
            f"only_template={template_name!r} not found in L2 instance "
            f"(db_table_prefix={cfg.db_table_prefix!r}). "
            f"Declared TransferTemplates: {declared}",
        )
    return frozenset(template.leg_rails)


def _covered_rail_names(
    cfg: Config, instance: L2Instance,
) -> frozenset[Identifier]:
    """Return the set of rail names that already have rows in the demo
    DB's ``<prefix>_transactions`` table.

    X.4.g.10 — used by ``scope: uncovered_rails`` to decide which rails
    to skip in the baseline emit. Covered = "the BS.4 etl_hook
    populated this rail directly into demo_db"; uncovered = "no rows
    yet, fill the gap with baseline".
    """
    p = cfg.db_table_prefix  # Z.C — was instance.instance
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT DISTINCT rail_name FROM {p}_transactions"
                " WHERE rail_name IS NOT NULL"
            )
            return frozenset(
                Identifier(str(row[0])) for row in cur.fetchall()
                if row[0] is not None
            )
        finally:
            cur.close()
    finally:
        conn.close()


# X.4.i.2 — Default account-role set for derive_balances. Control accounts
# are bank-bookkeeping accounts where `money = SUM(amount_money)` holds by
# construction (the drift invariant run forward). DDA / external account
# balances come from upstream statements; deriving them masks
# reconciliation gaps the bank wants to see, so they're opt-in only.
_DERIVE_BALANCES_DEFAULT_ACCOUNT_ROLES: frozenset[str] = frozenset(
    {"gl_control", "concentration_master", "funds_pool"},
)


async def step_3_5_derive_balances(
    cfg: Config,
    instance: L2Instance,
    *,
    dev_log: DevLogWriter | None = None,
) -> int:
    """X.4.i.2 — re-derive ``<prefix>_daily_balances`` from
    ``<prefix>_transactions`` for the configured account roles.

    No-op when ``cfg.test_generator.derive_balances`` is False (the default).
    When enabled, computes ``money = SUM(amount_money)`` per
    (account_id, business_day_end) for accounts whose ``account_role``
    matches ``cfg.test_generator.derive_balances_account_roles`` (or the
    default control-account set when None) and UPSERTs into the
    daily_balances table. Existing rows for those roles are overwritten
    in-place; rows for other roles are untouched.

    The drift invariant is what this is "running forward": auditing
    `money == SUM(amount_money)` would always pass for derived rows
    since they were just computed that way. That's the point — operators
    use this when they want planted scenarios to reconcile cleanly
    against derived balances (e.g. test the dashboard renders against
    a known-clean control set), or when their ETL provides only
    transactions and balances must be back-filled.

    Returns the number of (account_id, business_day) rows inserted /
    updated. ``dev_log`` receives lifecycle events
    ``deploy:step3_5:derive:start`` and ``deploy:step3_5:derive:done``
    (with ``rows`` count + ``account_roles`` for visibility).
    """
    if not cfg.test_generator.derive_balances:
        return 0

    p = cfg.db_table_prefix  # Z.C — was instance.instance
    account_roles = (
        cfg.test_generator.derive_balances_account_roles
        if cfg.test_generator.derive_balances_account_roles is not None
        else tuple(sorted(_DERIVE_BALANCES_DEFAULT_ACCOUNT_ROLES))
    )
    await _emit(dev_log, {
        "event": "deploy:step3_5:derive:start",
        "account_roles": list(account_roles),
    })

    # Build ('a', 'b', ...) literal — account_role values come from the
    # canonical role strings declared in the L2 model; this cfg field is
    # a tuple[str, ...] validated at load time. Quoting them inline is
    # safe (no user-controlled SQL) and matches the dialect-portable
    # style the rest of the matview SQL uses.
    roles_clause = ", ".join(f"'{r}'" for r in account_roles)

    # Sum amount_money per (account_id, business_day_end). Use a CAST
    # of posting to DATE to derive the business-day grouping key; the
    # resulting (start, end) span the operator's local-day window.
    date_expr = "CAST(posting AS DATE)"
    bday_start = "CAST(CAST(posting AS DATE) AS TIMESTAMP)"
    # +1 day for the half-open business-day window.
    if cfg.dialect == Dialect.ORACLE:
        bday_end = "CAST(CAST(posting AS DATE) AS TIMESTAMP) + INTERVAL '1' DAY"
    else:
        bday_end = "CAST(CAST(posting AS DATE) AS TIMESTAMP) + INTERVAL '1 day'"

    conn = connect_demo_db(cfg)
    rows_written = 0
    try:
        cur = conn.cursor()
        try:
            # Two-pass UPSERT for dialect portability:
            #   1. DELETE existing rows for these account roles.
            #   2. INSERT the freshly-computed rows.
            # Cleaner than dialect-specific INSERT ... ON CONFLICT /
            # MERGE — we already wipe + rebuild for scope=full, so this
            # is a focused sub-wipe.
            cur.execute(
                f"DELETE FROM {p}_daily_balances "
                f"WHERE account_role IN ({roles_clause})",
            )
            cur.execute(
                f"INSERT INTO {p}_daily_balances ("
                f"account_id, account_name, account_role, "
                f"account_scope, account_parent_role, "
                f"expected_eod_balance, business_day_start, "
                f"business_day_end, money, metadata, supersedes"
                f") "
                f"SELECT "
                f"  account_id, "
                f"  MAX(account_name), "
                f"  MAX(account_role), "
                f"  MAX(account_scope), "
                f"  MAX(account_parent_role), "
                f"  SUM(amount_money), "  # expected = derived (drift = 0)
                f"  {bday_start}, "
                f"  {bday_end}, "
                f"  SUM(amount_money), "
                # CZ.2: synthetic-row predicate stamp — this derivation
                # only runs in the deploy pipeline (Studio / Trainer-
                # driven); ETL integrators populate _daily_balances
                # themselves via etl.write_daily_balance and never hit
                # this branch. The derived rows are synthetic by
                # construction.
                f"  '{{\"source\":\"training\"}}', "
                f"  NULL "
                f"FROM {p}_transactions "
                f"WHERE account_role IN ({roles_clause}) "
                f"  AND status <> 'failed' "
                f"GROUP BY account_id, {date_expr}",
            )
            if cfg.dialect is Dialect.DUCKDB:
                # DuckDB returns -1 for cur.rowcount on INSERT-FROM-SELECT;
                # re-query the row set we just wrote to get the actual count.
                cur.execute(
                    f"SELECT COUNT(*) FROM {p}_daily_balances "
                    f"WHERE account_role IN ({roles_clause})",
                )
                rows_written = int(fetch_one_required(cur)[0])
            else:
                rows_written = cur.rowcount or 0
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()

    await _emit(dev_log, {
        "event": "deploy:step3_5:derive:done",
        "rows": rows_written,
        "account_roles": list(account_roles),
    })
    return rows_written


# X.4.g.11 — Step 4: refresh L1 invariant + Investigation matviews so
# every dashboard re-derives off the post-step-3 base-table state.

async def step_4_matviews(
    cfg: Config,
    instance: L2Instance,
    *,
    dev_log: DevLogWriter | None = None,
) -> None:
    """Run ``refresh_matviews_sql(instance, dialect=cfg.dialect)`` against
    the demo DB.

    The schema helper picks the right shape per dialect:
      - PG / Oracle: ``REFRESH MATERIALIZED VIEW`` + ``ANALYZE`` per name.
      - SQLite (matview-as-table): ``DROP TABLE`` + ``CREATE TABLE … AS``
        per name (re-runs the matview body).

    No-op safe — the SQL is dependency-ordered + idempotent at the
    schema level. Sync DB-API work runs in ``asyncio.to_thread`` so the
    studio's POST /deploy doesn't block other requests for the refresh
    duration (matview refresh is the slowest pipeline step on a
    multi-million-row demo).
    """
    sql = refresh_matviews_sql(instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect)
    await _emit(dev_log, {
        "event": "deploy:step4:matviews:start",
        "db_table_prefix": cfg.db_table_prefix,
        "dialect": cfg.dialect.value,
    })

    def _run_refresh() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                execute_script(cur, sql, dialect=cfg.dialect)
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run_refresh)
    await _emit(dev_log, {
        "event": "deploy:step4:matviews:done",
    })


# X.4.g.12 — Step 5: bump a process-local generation counter so any
# Dashboards page open in another tab knows to reload itself.
#
# The counter starts at 0 on process boot. ``step_5_reload`` bumps it
# by 1 each call. Open Dashboards pages poll ``GET /data_generation_id``
# (or subscribe via SSE in a future iteration) and reload when the
# server-reported value differs from what they last observed. Lives at
# module scope so a fresh import sees the same value across requests
# inside one process. Cross-process invalidation is out of scope —
# Studio + Dashboards run in the same uvicorn worker by design.
_data_generation_id: int = 0


def get_data_generation_id() -> int:
    """Read the current generation counter — used by the
    ``GET /data_generation_id`` endpoint Dashboards polls."""
    return _data_generation_id


async def step_5_reload(
    *, dev_log: DevLogWriter | None = None,
) -> int:
    """Bump ``_data_generation_id`` by one and emit the new value.

    Returns the post-bump value so the deploy summary can surface
    "data_generation_id: 7". This is the cheapest pipeline step — no
    DB access, no I/O, just an integer increment under the asyncio
    event loop's single-threaded guarantee.
    """
    global _data_generation_id
    _data_generation_id += 1
    new = _data_generation_id
    await _emit(dev_log, {
        "event": "deploy:step5:reload:bump",
        "data_generation_id": new,
    })
    return new


# X.4.g.13 — Run the full pipeline: orchestrate steps 1→5 with the
# halt-on-etl-failure contract baked in.

@dataclass(frozen=True)
class DeploySummary:
    """Structured per-step outcome of a ``run_deploy_pipeline`` call.

    Wraps the raw event stream the studio + tests collect. The studio's
    POST /deploy serializes this dataclass straight to JSON; tests
    assert against the typed fields without re-parsing event payloads.

    ``halted`` flips when step 1's etl_hook returns non-zero exit (the
    only halt point — every other step runs unconditionally once we're
    past step 1). Any halted summary leaves later steps' fields at
    their default (zeros / False) — read ``halted`` first.
    """

    halted: bool = False
    halt_reason: str | None = None
    step1_etl_hook_exit_code: int = 0
    step2_wipe_transactions_deleted: int = 0
    step2_wipe_daily_balances_deleted: int = 0
    step3_generator_transactions_after: int = 0
    step3_generator_daily_balances_after: int = 0
    # X.4.i.2 — number of (account_id, balance_date) rows the
    # post-step-3 derive_balances pass wrote. Zero when the flag is off.
    step3_5_derived_balance_rows: int = 0
    step4_matviews_done: bool = False
    step5_data_generation_id: int = 0
    events: tuple[Mapping[str, object], ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, object]:
        """Serialize to a JSON-safe dict for ``POST /deploy`` responses."""
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "step1_etl_hook_exit_code": self.step1_etl_hook_exit_code,
            "step2_wipe": {
                "transactions_deleted": self.step2_wipe_transactions_deleted,
                "daily_balances_deleted": (
                    self.step2_wipe_daily_balances_deleted
                ),
            },
            "step3_generator": {
                "transactions_after": self.step3_generator_transactions_after,
                "daily_balances_after": (
                    self.step3_generator_daily_balances_after
                ),
            },
            "step3_5_derived_balance_rows": (
                self.step3_5_derived_balance_rows
            ),
            "step4_matviews_done": self.step4_matviews_done,
            "step5_data_generation_id": self.step5_data_generation_id,
            "events": [dict(e) for e in self.events],
        }


async def run_deploy_pipeline(
    cfg: Config,
    instance: L2Instance,
    *,
    dev_log: DevLogWriter | None = None,
    overlays: "PipelineOverlays | None" = None,
    subprocess_lock_bracket: (
        Callable[[], AbstractAsyncContextManager[None]] | None
    ) = None,
) -> DeploySummary:
    """Orchestrate the BS.4 4-step deploy pipeline (with the 3.5 derive
    sub-step). Order: wipe → etl_hook → generator → overlays → matviews → reload.

    BS.4 (2026-05-29) reordered + dropped the legacy `step_2_pull`.
    The wipe now runs FIRST (clean slate for etl_hook + generator to
    write into); etl_hook writes directly to demo_db (no parallel
    upstream); pull is gone.

    BU.1.8 — ``overlays`` is the typed surface for post-baseline plant
    layers (replaces the round-1 ``cfg.test_generator.scope = "uncovered_rails"``
    indirection). When ``None``, defaults are dialect-aware: ETL_DEBUG
    (full noise) for studio Refresh Data callers, TRAINER_CLEAN for
    Trainer reset, LOCKED_SEED for tests. See
    ``common/l2/pipeline_overlays.py`` for the named flows.

    When ``overlays`` is provided AND ``cfg.test_generator.scope`` is
    the default ``"full"``, the generator step emits the BASELINE-only
    SQL (`emit_baseline_seed`) + each overlay layer applies after.
    This separates baseline-vs-plants so the Trainer can plant on a
    quiet baseline without the L1-plant overlay firing.

    Callers passing ``overlays=None`` get the legacy behavior
    (build_full_seed_sql via scope-string dispatch) for CLI data apply
    + locked-seed test back-compat.

    Halt contract: step 2's ``etl_hook`` exit code gates steps 3-5.
    Non-zero ⇒ stop. demo_db is left in whatever state the hook
    produced (the wipe ran; the hook may have written some / no /
    partial rows). Operators are expected to wrap their hook in a
    transaction so a failure rolls back to the post-wipe empty state.

    Every step shares one event-collecting writer that fans out to the
    caller's ``dev_log`` AND captures the events on the returned
    ``DeploySummary.events`` tuple — so the studio's POST /deploy can
    render a "what happened" timeline even if dev_log is off.
    """
    # Avoid forward-ref-only import; lazy-import to dodge circular
    # (pipeline_overlays imports cli/_helpers which imports here).
    from recon_gen.common.l2.pipeline_overlays import (  # noqa: PLC0415
        L1_INVARIANT_PLANTS,
        OverlayContext,
    )

    captured: list[Mapping[str, object]] = []

    async def _tee(payload: Mapping[str, object]) -> None:
        captured.append(dict(payload))
        if dev_log is not None:
            await dev_log(payload)

    # BU.1.8 — when typed overlays are supplied, patch cfg.test_generator.scope
    # so the step-3 generator emits baseline-only IFF the L1 plants overlay
    # is NOT in the list. L1_INVARIANT_PLANTS as an overlay is implemented
    # by the scope="full" path (preserves locked-seed bytes); other overlay
    # layers (L2_DEMO_GAP_OVERLAY, future Trainer-mode amplification) apply
    # post-pipeline via the loop below. Without overlays (default), legacy
    # scope-string dispatch keeps the CLI + locked-seed behavior unchanged.
    pipeline_cfg = cfg
    post_pipeline_overlays: tuple[object, ...] = ()
    if overlays is not None:
        has_l1 = any(
            layer.name == L1_INVARIANT_PLANTS.name
            for layer in overlays.layers
        )
        if not has_l1 and cfg.test_generator.scope == "full":
            from dataclasses import replace as _dr  # noqa: PLC0415
            pipeline_cfg = _dr(
                cfg, test_generator=_dr(
                    cfg.test_generator, scope="uncovered_rails",
                ),
            )
        # Non-L1 overlays apply AFTER the pipeline's baseline + step 3.5.
        post_pipeline_overlays = tuple(
            layer for layer in overlays.layers
            if layer.name != L1_INVARIANT_PLANTS.name
        )
        await _emit(_tee, {
            "event": "deploy:overlays:declared",
            "overlay_names": list(overlays.names()),
            "scope_override": (
                pipeline_cfg.test_generator.scope
                if pipeline_cfg is not cfg else None
            ),
        })

    # BS.4: wipe FIRST so etl_hook + generator write into clean state.
    tx_del, bal_del = await step_2_wipe(pipeline_cfg, instance, dev_log=_tee)

    # CO.x — release the dashboards' pool lock around step_1_etl_hook so
    # the operator's cfg.etl_hook subprocess can acquire the DuckDB
    # write lock. PG / Oracle pools don't need this (concurrent writers
    # are fine on those engines); the caller passes None and the bracket
    # no-ops via nullcontext. The pool's released_for_subprocess context
    # manager holds its lifecycle lock across the whole window so
    # concurrent /deploy + /training/reset handlers serialize through
    # this bracket; the close drains in-flight cursors first; the reopen
    # ALWAYS fires (even on subprocess failure or CancelledError) so the
    # pool comes back online for the pipeline's remaining same-process
    # steps (generator / matview refresh) and subsequent dashboards
    # queries. Same-process connect_demo_db inside this process doesn't
    # need the bracket — only the cross-process subprocess does.
    bracketed = subprocess_lock_bracket is not None
    try:
        bracket: AbstractAsyncContextManager[None] = (
            subprocess_lock_bracket() if subprocess_lock_bracket is not None
            else nullcontext()
        )
        if bracketed:
            await _emit(_tee, {"event": "deploy:step1:locks_bracket_enter"})
        async with bracket:
            rc = await step_1_etl_hook(pipeline_cfg, dev_log=_tee)
    finally:
        if bracketed:
            await _emit(_tee, {"event": "deploy:step1:locks_bracket_exit"})
    if rc != 0:
        await _emit(_tee, {
            "event": "deploy:halt",
            "reason": (
                f"etl_hook returned exit_code={rc}; "
                "demo DB left in partial state (post-wipe + whatever "
                "the hook wrote before failing)"
            ),
        })
        return DeploySummary(
            halted=True,
            halt_reason=(
                f"etl_hook returned exit_code={rc}; "
                "demo DB left in partial state (post-wipe + whatever "
                "the hook wrote before failing)"
            ),
            step1_etl_hook_exit_code=rc,
            step2_wipe_transactions_deleted=tx_del,
            step2_wipe_daily_balances_deleted=bal_del,
            events=tuple(captured),
        )

    tx_after, bal_after = await step_3_generator(
        pipeline_cfg, instance, dev_log=_tee,
    )
    derived_rows = await step_3_5_derive_balances(
        pipeline_cfg, instance, dev_log=_tee,
    )

    # BU.1.8 — apply post-pipeline overlay layers (L2_DEMO_GAP_OVERLAY,
    # future Trainer-mode amplification, etc) BEFORE matview refresh so
    # the matviews see the overlay'd state.
    if post_pipeline_overlays:
        ctx = OverlayContext(
            cfg=pipeline_cfg, instance=instance, dev_log=_tee,
        )
        for layer in post_pipeline_overlays:
            # cast: post_pipeline_overlays carries OverlayLayer objects
            # widened to object via the tuple-comprehension narrowing.
            from recon_gen.common.l2.pipeline_overlays import (  # noqa: PLC0415
                OverlayLayer,
            )
            assert isinstance(layer, OverlayLayer), (
                f"post_pipeline_overlays must hold OverlayLayer, got {type(layer)}"
            )
            await layer.apply(ctx)

    await step_4_matviews(pipeline_cfg, instance, dev_log=_tee)
    new_gen_id = await step_5_reload(dev_log=_tee)

    return DeploySummary(
        halted=False,
        halt_reason=None,
        step1_etl_hook_exit_code=rc,
        step2_wipe_transactions_deleted=tx_del,
        step2_wipe_daily_balances_deleted=bal_del,
        step3_generator_transactions_after=tx_after,
        step3_generator_daily_balances_after=bal_after,
        step3_5_derived_balance_rows=derived_rows,
        step4_matviews_done=True,
        step5_data_generation_id=new_gen_id,
        events=tuple(captured),
    )
