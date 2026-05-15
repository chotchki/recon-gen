"""X.4.g — Studio "Deploy changes" pipeline.

Five-step orchestration that takes the operator's current `cfg` +
`L2Instance` and refreshes the demo DB so the dashboards re-render
against the new shape:

1. **etl_hook gate** — run `cfg.etl_hook` as a subprocess; halt on
   non-zero exit BEFORE step 2 touches the demo DB. (X.4.g.4)
2. **wipe + pull** — wipe demo data, then if `cfg.etl_datasource` is
   set, copy `transactions` + `daily_balances` rows filtered to
   `<= cfg.test_generator.end_date`. (X.4.g.5 / X.4.g.6)
3. **generator** — `emit_full_seed` against the current
   `cfg.test_generator` knobs; always additive. (X.4.g.7-10)
4. **matview refresh** — existing `refresh_matviews_sql(instance)`.
   (X.4.g.11)
5. **reload** — bump `data_generation_id`; Dashboards' open page
   polls and reloads its current URL. (X.4.g.12)

This module is HTTP-free. The studio's `POST /deploy` endpoint
(X.4.g.13) wires up a `DevLogWriter` that emits via `_DEVLOG.info`;
the CLI (a future `quicksight-gen deploy apply` subcommand) wires
one that prints to stdout; tests wire one that appends to a list.
"""
from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from quicksight_gen.common.db import (
    connect_demo_db,
    execute_script,
    oracle_dsn,
    sqlite_path,
)
from quicksight_gen.common.l2.primitives import Identifier
from quicksight_gen.common.l2.schema import (
    BASE_DAILY_BALANCES_COLUMNS,
    BASE_TRANSACTIONS_COLUMNS,
    refresh_matviews_sql,
    wipe_demo_data_sql,
)
from quicksight_gen.common.sql import Dialect

if TYPE_CHECKING:
    from quicksight_gen.common.config import Config
    from quicksight_gen.common.l2.primitives import L2Instance


# A function the pipeline calls to surface progress / errors. Each
# event is a JSON-serializable mapping with at minimum an ``event``
# key (string identifier like ``deploy:step1:start``) so consumers
# can switch on it. ``None`` disables emission entirely.
DevLogWriter = Callable[[Mapping[str, object]], Awaitable[None]]


async def _emit(
    dev_log: DevLogWriter | None, payload: Mapping[str, object],
) -> None:
    if dev_log is None:
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
    halts the pipeline if non-zero — step 2's wipe must NOT run when
    the operator's ETL refresh failed.

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

    await asyncio.gather(
        _stream(proc.stdout, "stdout"),
        _stream(proc.stderr, "stderr"),
    )
    rc = await proc.wait()
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

    X.4.g.5 — runs unconditionally (when the pipeline reaches it),
    AFTER step 1's etl_hook gate has succeeded. The matview re-derive
    is step 4's job; this just clears the two base tables so step 2's
    pull (etl_datasource) and step 3's generator both write into clean
    state.

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

    def _run_wipe() -> tuple[int, int]:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                # Count first so the dev_log can report what was wiped.
                p = cfg.db_table_prefix  # Z.C — was instance.instance
                cur.execute(f"SELECT COUNT(*) FROM {p}_transactions")
                tx_count = int(cur.fetchone()[0])
                cur.execute(f"SELECT COUNT(*) FROM {p}_daily_balances")
                bal_count = int(cur.fetchone()[0])
                execute_script(cur, sql, dialect=cfg.dialect)
                conn.commit()
                return tx_count, bal_count
            finally:
                cur.close()
        finally:
            conn.close()

    tx_count, bal_count = await asyncio.to_thread(_run_wipe)
    await _emit(dev_log, {
        "event": "deploy:step2:wipe:done",
        "transactions_deleted": tx_count,
        "daily_balances_deleted": bal_count,
    })
    return tx_count, bal_count


# X.4.g.6 — Step 2 pull: cross-dialect copy from etl_datasource.

# Batch size for the source-to-dest fetch+insert loop. 5000 rows is
# the operator-tested default — memory bounded, dashboards-load tested.
# Tunable per-call for tests that want to exercise the multi-batch
# path without seeding 10k+ rows.
_PULL_BATCH_SIZE = 5000


def _dialect_from_url(url: str) -> Dialect:
    """Infer the SQL dialect from a connection URL prefix.

    Supports the same URL shapes ``connect_demo_db`` accepts. The
    operator declares only the URL in ``cfg.etl_datasource``; we don't
    require a redundant ``dialect:`` field there.
    """
    if url.startswith(("postgresql://", "postgres://")):
        return Dialect.POSTGRES
    if url.startswith(("oracle://", "oracle+oracledb://")):
        return Dialect.ORACLE
    if url.startswith("sqlite://"):
        return Dialect.SQLITE
    raise ValueError(
        f"Cannot infer dialect from etl_datasource URL: {url!r}. "
        f"Supported prefixes: postgresql://, oracle://, sqlite://."
    )


def _connect_etl_source(url: str) -> Any:  # pyright: ignore[reportExplicitAny]  # WHY: DB-API 2.0 sync connection has no shared Protocol across psycopg/oracledb/sqlite3
    """Open a sync DB-API 2.0 connection to the etl_datasource URL.

    Mirrors ``connect_demo_db`` but takes a URL directly so the source
    DB doesn't share ``cfg.demo_database_url`` / ``cfg.dialect``.
    """
    dialect = _dialect_from_url(url)
    if dialect is Dialect.POSTGRES:
        import psycopg
        return psycopg.connect(url)
    if dialect is Dialect.ORACLE:
        import oracledb
        return oracledb.connect(oracle_dsn(url))
    import sqlite3
    return sqlite3.connect(sqlite_path(url))


def _insert_paramstyle_sql(
    table: str, columns: tuple[str, ...], dialect: Dialect,
) -> str:
    """Build an INSERT with the dialect's parameter placeholder style.

    psycopg uses ``%s``, oracledb uses ``:1, :2, ...``, sqlite3 uses
    ``?``. Single source of truth so the executemany call binds
    correctly per dialect.
    """
    cols_csv = ", ".join(columns)
    n = len(columns)
    if dialect is Dialect.POSTGRES:
        placeholders = ", ".join(["%s"] * n)
    elif dialect is Dialect.ORACLE:
        placeholders = ", ".join(f":{i + 1}" for i in range(n))
    else:
        placeholders = ", ".join(["?"] * n)
    return f"INSERT INTO {table} ({cols_csv}) VALUES ({placeholders})"


async def step_2_pull(
    cfg: Config,
    instance: L2Instance,
    *,
    dev_log: DevLogWriter | None = None,
    batch_size: int = _PULL_BATCH_SIZE,
) -> tuple[int, int]:
    """Cross-dialect copy from ``cfg.etl_datasource`` into the demo DB.

    X.4.g.6 — runs after step 2's wipe, BEFORE step 3's generator.
    For each base table:
      - SELECT mirrored columns from the source's declared
        transactions / daily_balances table, optionally filtered to
        ``cfg.test_generator.end_date`` (transactions: ``posting <=``;
        daily_balances: ``business_day_end <=``).
      - Fetch in ``batch_size`` chunks (default 5000).
      - INSERT each batch into the demo's ``<prefix>_<table>``.

    Skips entirely (no-op, returns ``(0, 0)``, emits a skip event)
    when ``cfg.etl_datasource is None`` — operator's no-etl path.

    Column mapping is by-name: the source must expose columns with
    the same names the L2 v6 schema declares (``BASE_*_COLUMNS``).
    Extra columns in the source are ignored; missing columns surface
    as a loud "column not found" failure from the source DB. This is
    the contract per the X.4.g.6 design — the operator's ETL is
    responsible for landing v6-compliant column names.

    Returns ``(transactions_pulled, daily_balances_pulled)`` row
    counts so the caller can surface "pulled 12,345 transactions" in
    the deploy summary.
    """
    if cfg.etl_datasource is None:
        await _emit(dev_log, {
            "event": "deploy:step2:pull:skip",
            "reason": "etl_datasource not configured",
        })
        return 0, 0

    src_cfg = cfg.etl_datasource
    src_dialect = _dialect_from_url(src_cfg.url)
    end_date = cfg.test_generator.end_date
    p = cfg.db_table_prefix  # Z.C — was instance.instance

    await _emit(dev_log, {
        "event": "deploy:step2:pull:start",
        "source_dialect": src_dialect.value,
        "dest_dialect": cfg.dialect.value,
        "db_table_prefix": p,
        "end_date": end_date.isoformat() if end_date else None,
    })

    def _run_pull() -> tuple[int, int]:
        src_conn = _connect_etl_source(src_cfg.url)
        try:
            dest_conn = connect_demo_db(cfg)
            try:
                tx_pulled = _pull_table(
                    src_conn=src_conn,
                    dest_conn=dest_conn,
                    src_table=src_cfg.transactions_table,
                    dest_table=f"{p}_transactions",
                    columns=BASE_TRANSACTIONS_COLUMNS,
                    filter_col="posting",
                    end_date=end_date,
                    dest_dialect=cfg.dialect,
                    batch_size=batch_size,
                )
                bal_pulled = _pull_table(
                    src_conn=src_conn,
                    dest_conn=dest_conn,
                    src_table=src_cfg.daily_balances_table,
                    dest_table=f"{p}_daily_balances",
                    columns=BASE_DAILY_BALANCES_COLUMNS,
                    filter_col="business_day_end",
                    end_date=end_date,
                    dest_dialect=cfg.dialect,
                    batch_size=batch_size,
                )
                dest_conn.commit()
                return tx_pulled, bal_pulled
            finally:
                dest_conn.close()
        finally:
            src_conn.close()

    tx_pulled, bal_pulled = await asyncio.to_thread(_run_pull)
    await _emit(dev_log, {
        "event": "deploy:step2:pull:done",
        "transactions_pulled": tx_pulled,
        "daily_balances_pulled": bal_pulled,
    })
    return tx_pulled, bal_pulled


def _pull_table(
    *,
    src_conn: Any,  # pyright: ignore[reportExplicitAny]  # WHY: DB-API 2.0 sync connection has no shared Protocol across drivers
    dest_conn: Any,  # pyright: ignore[reportExplicitAny]  # WHY: DB-API 2.0 sync connection has no shared Protocol across drivers
    src_table: str,
    dest_table: str,
    columns: tuple[str, ...],
    filter_col: str,
    end_date: date | None,
    dest_dialect: Dialect,
    batch_size: int,
) -> int:
    """Stream rows from one source table into one dest table.

    Returns the row count pulled. Source column order MUST match
    ``columns``; the SELECT names them explicitly so the operator's
    source can have extras + we still bind correctly to the INSERT.
    """
    cols_csv = ", ".join(columns)
    select_sql = f"SELECT {cols_csv} FROM {src_table}"
    if end_date is not None:
        # ISO-8601 date string is always-safe to inline (operator-controlled
        # via cfg.test_generator.end_date, typed `date`). DB-API param
        # styles differ across drivers — inlining keeps the source-side
        # path single-shape.
        select_sql += f" WHERE {filter_col} <= '{end_date.isoformat()}'"

    insert_sql = _insert_paramstyle_sql(dest_table, columns, dest_dialect)
    coerce_row = _row_coercer_for(dest_dialect)

    src_cur = src_conn.cursor()
    try:
        src_cur.execute(select_sql)
        dest_cur = dest_conn.cursor()
        try:
            total = 0
            while True:
                batch = src_cur.fetchmany(batch_size)
                if not batch:
                    break
                dest_cur.executemany(insert_sql, [coerce_row(r) for r in batch])
                total += len(batch)
            return total
        finally:
            dest_cur.close()
    finally:
        src_cur.close()


def _row_coercer_for(
    dest_dialect: Dialect,
) -> Callable[[tuple[Any, ...]], tuple[Any, ...]]:  # pyright: ignore[reportExplicitAny]  # WHY: row tuple values are arbitrary DB-driver types
    """Return a function that coerces source-row values to types the
    destination DB driver accepts.

    The cross-dialect pull path is the only place this matters: psycopg
    returns ``decimal.Decimal`` for NUMERIC columns and stdlib
    ``datetime.datetime`` for TIMESTAMP. sqlite3 (PEP 249) doesn't
    register Decimal as a recognized parameter type — the executemany
    raises ``ProgrammingError: type 'decimal.Decimal' is not
    supported``. Convert on the way in so the source can be any dialect.

    PG / Oracle destinations: identity. Their drivers accept Decimal +
    datetime natively, so the per-row clone is wasted work but stays
    correct.
    """
    if dest_dialect is not Dialect.SQLITE:
        return lambda row: row
    # Lazy decimal import — keep the cost off the hot PG path.
    from decimal import Decimal

    def _coerce(row: tuple[Any, ...]) -> tuple[Any, ...]:  # pyright: ignore[reportExplicitAny]  # WHY: see _row_coercer_for docstring
        out: list[Any] = []  # pyright: ignore[reportExplicitAny]  # WHY: see _row_coercer_for docstring
        for v in row:
            if isinstance(v, Decimal):
                # str preserves arbitrary precision; sqlite stores it
                # in the TEXT affinity that the schema declares for
                # money fields anyway (the JSON1 / SQL/JSON path
                # contract per Schema_v6).
                out.append(str(v))
            else:
                out.append(v)
        return tuple(out)

    return _coerce


# X.4.g.7+8 — Step 3 generator: synthetic data overlay.

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
        behavior). Byte-identical to the locked seeds when no
        ``etl_datasource`` AND knobs at defaults.
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
                tx = int(cur.fetchone()[0])
                cur.execute(f"SELECT COUNT(*) FROM {p}_daily_balances")
                bal = int(cur.fetchone()[0])
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
        # daily_balances.business_day_start is DATE — same `>= next_day`
        # predicate works for both via the midnight-of-next-day bound
        # (avoids dialect-specific DATE() / TRUNC() function calls; ISO
        # strings sort lexicographically the way we want).
        prefix = cfg.db_table_prefix
        next_day = (cutoff + timedelta(days=1)).isoformat()
        sql += (
            f"\n-- X.4.h trainer cutoff: prune rows past {cutoff.isoformat()}\n"
            f"DELETE FROM {prefix}_transactions "
            f"WHERE posting >= '{next_day}';\n"
            f"DELETE FROM {prefix}_daily_balances "
            f"WHERE business_day_start >= '{next_day}';\n"
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
        # ``etl_datasource`` + default test_generator knobs ⇒
        # byte-identical to ``tests/data/_locked_seeds/<inst>.<dialect>.sql``.
        # build_full_seed_sql still carries a no-untyped-def waiver
        # (CLI-wide typing sweep is a separate task per its own
        # ignore comment); the call is still by-position-correct.
        from quicksight_gen.cli._helpers import build_full_seed_sql  # pyright: ignore[reportUnknownVariableType]  # WHY: helper has pending untyped-def waiver in cli/_helpers.py
        return build_full_seed_sql(  # pyright: ignore[reportUnknownVariableType]  # WHY: same helper-untyped waiver propagates to the call expression
            cfg, instance,
            anchor=cfg.test_generator.end_date,
            plants=cfg.test_generator.plants or None,  # X.4.h.0.a — None ⇒ all kinds (locked-seed default)
            base_seed=cfg.test_generator.seed,  # X.4.h.0.b — None ⇒ _BASELINE_BASE_SEED (locked-seed default)
        )
    if scope == "exceptions_only":
        # X.4.g.9 — plants only, no baseline. The integrator's external
        # data already lives in the demo DB (via step 2's etl_datasource
        # pull); we just lay the L1/Investigation exception scenarios
        # on top so the dashboards render planted violations against
        # their data. ``emit_seed`` is the plants-only emitter that
        # ``emit_full_seed`` wraps with a baseline; calling it directly
        # skips the 90-day baseline insert.
        from quicksight_gen.cli._helpers import build_default_scenario  # pyright: ignore[reportUnknownVariableType]  # WHY: helper has pending untyped-def waiver in cli/_helpers.py
        from quicksight_gen.common.l2.seed import emit_seed
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
        from quicksight_gen.common.l2.seed import emit_baseline_seed
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
        from quicksight_gen.common.l2.seed import emit_baseline_seed
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
        from quicksight_gen.cli._helpers import build_default_scenario  # pyright: ignore[reportUnknownVariableType]  # WHY: helper has pending untyped-def waiver in cli/_helpers.py
        from quicksight_gen.common.l2.seed import emit_seed
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
    to skip in the baseline emit. Covered = "operator's external data
    populated this rail (via step 2's etl_datasource pull)";
    uncovered = "no rows yet, fill the gap with baseline".
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
    # SQLite needs DATE() function; PG / Oracle use CAST(posting AS DATE).
    if cfg.dialect == Dialect.SQLITE:
        date_expr = "DATE(posting)"
        bday_start = (
            "DATETIME(DATE(posting) || ' 00:00:00')"
        )
        bday_end = "DATETIME(DATE(posting, '+1 day') || ' 00:00:00')"
    else:
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
                f"business_day_end, money, limits, supersedes"
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
                f"  NULL, "
                f"  NULL "
                f"FROM {p}_transactions "
                f"WHERE account_role IN ({roles_clause}) "
                f"  AND status <> 'failed' "
                f"GROUP BY account_id, {date_expr}",
            )
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
    step2_pull_transactions_pulled: int = 0
    step2_pull_daily_balances_pulled: int = 0
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
            "step2_pull": {
                "transactions_pulled": self.step2_pull_transactions_pulled,
                "daily_balances_pulled": (
                    self.step2_pull_daily_balances_pulled
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
) -> DeploySummary:
    """Orchestrate steps 1→5 of the X.4.g pipeline.

    Halt contract: step 1's ``etl_hook`` exit code gates everything
    downstream. Non-zero ⇒ stop BEFORE step 2's wipe, return a
    ``DeploySummary(halted=True, halt_reason=...)``. The whole point
    of declaring an etl_hook is that the demo DB MUST NOT be touched
    when the operator's ETL refresh failed (their data isn't where
    they expect it).

    Every step shares one event-collecting writer that fans out to the
    caller's ``dev_log`` AND captures the events on the returned
    ``DeploySummary.events`` tuple — so the studio's POST /deploy can
    render a "what happened" timeline even if dev_log is off.
    """
    captured: list[Mapping[str, object]] = []

    async def _tee(payload: Mapping[str, object]) -> None:
        captured.append(dict(payload))
        if dev_log is not None:
            await dev_log(payload)

    rc = await step_1_etl_hook(cfg, dev_log=_tee)
    if rc != 0:
        await _emit(_tee, {
            "event": "deploy:halt",
            "reason": (
                f"etl_hook returned exit_code={rc}; "
                "demo DB not touched"
            ),
        })
        return DeploySummary(
            halted=True,
            halt_reason=(
                f"etl_hook returned exit_code={rc}; "
                "demo DB not touched"
            ),
            step1_etl_hook_exit_code=rc,
            events=tuple(captured),
        )

    tx_del, bal_del = await step_2_wipe(cfg, instance, dev_log=_tee)
    tx_pull, bal_pull = await step_2_pull(cfg, instance, dev_log=_tee)
    tx_after, bal_after = await step_3_generator(
        cfg, instance, dev_log=_tee,
    )
    derived_rows = await step_3_5_derive_balances(
        cfg, instance, dev_log=_tee,
    )
    await step_4_matviews(cfg, instance, dev_log=_tee)
    new_gen_id = await step_5_reload(dev_log=_tee)

    return DeploySummary(
        halted=False,
        halt_reason=None,
        step1_etl_hook_exit_code=rc,
        step2_wipe_transactions_deleted=tx_del,
        step2_wipe_daily_balances_deleted=bal_del,
        step2_pull_transactions_pulled=tx_pull,
        step2_pull_daily_balances_pulled=bal_pull,
        step3_generator_transactions_after=tx_after,
        step3_generator_daily_balances_after=bal_after,
        step3_5_derived_balance_rows=derived_rows,
        step4_matviews_done=True,
        step5_data_generation_id=new_gen_id,
        events=tuple(captured),
    )
