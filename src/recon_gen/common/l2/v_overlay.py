"""BV.4.0/4.1 — v-overlay lifecycle orchestration.

The /training/ page operates on a ``<base>_v_*`` schema that's a
clone of the production ``<base>_*`` schema + the operator's enabled
plant set. Per `docs/audits/bv_5_dual_prefix_spike.md`:

- **Session Start** (`create_or_refresh_v_overlay`): ensures the v
  schema exists + clones data from base + refreshes v matviews.
  Does NOT call etl_hook directly — the route handler chains this
  with /etl/run when the operator clicks Session Start.
- **Apply plants** (`apply_plants_to_v_overlay`): emits + executes
  the enabled plant set into the v overlay + refreshes its matviews.
  Naive shape (clone-and-replay) for the vertical slice; DL.9
  diff-only Apply lands in BV.4.4.
- **Cleanup** (`drop_v_overlay`): drops the v schema entirely.

The v-overlay's prefix is always ``<cfg.db.table_prefix>_v`` —
derived at runtime, not configured.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.l2.schema import (
    emit_schema, emit_schema_drop_sql, refresh_matviews_sql,
)

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2 import L2Instance
    from recon_gen.common.l2.plant_registry import PlantKindEntry


# BV.3.3 — apply_plants accepts plants whose contract.mutates is in this
# set. Re-exported from plant_registry as a single source of truth;
# guarded by a TYPE_CHECKING-aware lazy lookup inside apply_plants to
# keep this module's import graph narrow (no eager import of
# plant_registry at module load, which pulls in seed.py + spine etc.).
def _allowed_plant_mutation_surfaces() -> frozenset[str]:
    from recon_gen.common.l2.plant_registry import _ALLOWED_MUTATION_SURFACES  # noqa: PLC0415
    return _ALLOWED_MUTATION_SURFACES


def v_overlay_prefix(base_prefix: str) -> str:
    """The conventional `<base>_v` suffix per DL.3.

    Single function so callers don't string-concatenate ad hoc —
    keeps a future rename atomic."""
    return f"{base_prefix}_v"


class BaseSchemaMissingError(RuntimeError):
    """CS.13 — raised by ``session_start`` when the base schema isn't
    applied yet. Includes the operator-actionable remedy in the
    message so the studio's error rendering can name the fix without
    extra wrapping."""

    def __init__(self, base_prefix: str) -> None:
        super().__init__(
            f"Base schema not applied (missing `{base_prefix}_transactions` "
            f"table). Run `recon-gen schema apply --execute` against your "
            f"demo DB first, then click Session Start again."
        )
        self.base_prefix = base_prefix


def _base_schema_exists(cfg: "Config") -> bool:
    """CS.13 — probe for `<base>_transactions` to short-circuit
    Session Start when the operator hasn't applied the base schema.

    Uses a tolerant SELECT-with-LIMIT-0 instead of an information-
    schema query so the probe works the same across PG / Oracle /
    DuckDB. Any exception (table missing, connection refused, etc.)
    is treated as "schema not applied" — the explicit
    BaseSchemaMissingError that follows surfaces the actionable
    remedy regardless of which dialect's error shape fired.
    """
    base_prefix = cfg.db.table_prefix
    try:
        conn = connect_demo_db(cfg)
    except Exception:  # noqa: BLE001 — connection failures are operator-actionable as "schema not applied"
        return False
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT 1 FROM {base_prefix}_transactions WHERE 1 = 0")  # type: ignore[attr-defined]: structural DBAPI cursor
            return True
        except Exception:  # noqa: BLE001 — table-missing errors are dialect-specific shapes
            return False
        finally:
            cur.close()  # type: ignore[attr-defined]: structural DBAPI cursor
    finally:
        conn.close()  # type: ignore[attr-defined]: structural DBAPI connection


def create_v_overlay_sql(
    instance: "L2Instance", *, base_prefix: str,
    dialect: object,
) -> str:
    """Schema DDL for the v overlay. Mirrors the base prefix's schema
    one-for-one but under the `_v` suffix. Idempotent: callers should
    drop + recreate via :func:`drop_v_overlay_sql` first if the
    schema may already exist (Studio's Session Start does)."""
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    d = dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB
    return emit_schema(
        instance, prefix=v_overlay_prefix(base_prefix), dialect=d,
    )


def drop_v_overlay_sql(
    instance: "L2Instance", *, base_prefix: str,
    dialect: object,
) -> str:
    """Drop every per-prefix object the v overlay created."""
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    d = dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB
    return emit_schema_drop_sql(
        instance, prefix=v_overlay_prefix(base_prefix), dialect=d,
    )


def clone_base_to_v_sql(
    base_prefix: str, dialect: object = None,
) -> str:
    """Pure data copy from base prefix tables → v overlay tables.

    Three base data tables (per `step_2_wipe` survey):
    - ``<base>_transactions`` → ``<base>_v_transactions``
    - ``<base>_daily_balances`` → ``<base>_v_daily_balances``
    - ``<base>_config_kv`` → ``<base>_v_config_kv``

    Matviews are NOT cloned — they get rebuilt by
    :func:`refresh_v_overlay_matviews_sql` against the cloned base
    data. Cheaper than copying every matview row + guarantees v
    matview rows are derivable from the v base tables (the v
    overlay is internally consistent, not a snapshot artifact).

    Dialect branching (DI.1 perf — 2026-06-10):

    - **PG / Oracle / default**: ``DELETE FROM v_X; INSERT INTO v_X
      SELECT * FROM base_X`` — the classic two-pass shape. Safe
      everywhere; preserves the ``create_v_overlay_sql`` schema
      (constraints, indexes, sequence DEFAULTs).
    - **DuckDB**: ``CREATE OR REPLACE TABLE v_X AS SELECT * FROM
      base_X`` — single-pass columnar bulk load. On a 500k-row table
      this is ~57× faster than DELETE+INSERT through PK validation
      (DELETE costs ~75% of the wall time; INSERT through a PK costs
      ~25%). The trade-off: CTAS drops the table's PK / CHECK /
      sequence DEFAULT. Callers MUST follow up with
      :func:`realign_v_overlay_entry_sequences_sql` so plant INSERTs
      that omit ``entry`` (relying on the DEFAULT sequence) keep
      working post-clone. The v overlay is ephemeral training-mode
      data — losing CHECK / PK constraints there is an accepted
      cost; production paths (etl into base, not v) still get full
      schema enforcement.

    ``dialect`` is accepted as ``object`` (not the ``Dialect`` enum
    directly) so existing call sites that pre-date this signature
    keep working — the default ``None`` falls through to the legacy
    DELETE+INSERT shape which is correct on every dialect.
    """
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    v = v_overlay_prefix(base_prefix)
    if isinstance(dialect, Dialect) and dialect is Dialect.DUCKDB:
        # CTAS path — drops the table's PK + CHECK + sequence DEFAULT
        # as a side effect; caller follows up with
        # realign_v_overlay_entry_sequences_sql to re-attach the
        # sequence DEFAULT for plant INSERTs.
        return "\n".join([
            f"CREATE OR REPLACE TABLE {v}_transactions AS "
            f"SELECT * FROM {base_prefix}_transactions;",
            f"CREATE OR REPLACE TABLE {v}_daily_balances AS "
            f"SELECT * FROM {base_prefix}_daily_balances;",
            f"CREATE OR REPLACE TABLE {v}_config_kv AS "
            f"SELECT * FROM {base_prefix}_config_kv;",
        ])
    return "\n".join([
        f"DELETE FROM {v}_transactions;",
        f"DELETE FROM {v}_daily_balances;",
        f"DELETE FROM {v}_config_kv;",
        f"INSERT INTO {v}_transactions SELECT * FROM {base_prefix}_transactions;",
        f"INSERT INTO {v}_daily_balances SELECT * FROM {base_prefix}_daily_balances;",
        f"INSERT INTO {v}_config_kv SELECT * FROM {base_prefix}_config_kv;",
    ])


def realign_v_overlay_entry_sequences_sql(
    base_prefix: str, *,
    max_tx_entry: int, max_db_entry: int,
) -> str:
    """DI.1 — post-CTAS sequence realignment for DuckDB v overlay.

    :func:`clone_base_to_v_sql`'s DuckDB CTAS branch drops the v
    overlay tables' ``entry`` column DEFAULT (the ``nextval('<v>_X
    _entry_seq')`` reference). Plant INSERTs that omit ``entry`` rely
    on that DEFAULT to generate the next supersession key. This
    helper emits the SQL to:

    1. Drop the existing v entry sequences (started at 1 by
       ``create_v_overlay_sql``; advanced by any prior plant INSERTs).
    2. Recreate them starting at ``max(entry) + 1`` so the next
       ``nextval`` call doesn't collide with the cloned base rows
       (entries 1..N from base).
    3. ``ALTER TABLE ... ALTER COLUMN entry SET DEFAULT
       nextval('<seq>')`` to re-attach the DEFAULT.

    DuckDB-specific — no-op for PG / Oracle (their entry columns use
    BIGSERIAL / IDENTITY, which CTAS doesn't touch on those dialects
    because they use DELETE+INSERT, not CTAS).

    ``max_tx_entry`` + ``max_db_entry`` are computed by the caller
    via ``SELECT COALESCE(MAX(entry), 0) FROM <v>_X`` — DuckDB
    ``CREATE SEQUENCE START WITH`` requires a literal, not a
    subquery, so the value must be substituted at SQL build time.
    """
    v = v_overlay_prefix(base_prefix)
    tx_seq = f"{v}_transactions_entry_seq"
    db_seq = f"{v}_daily_balances_entry_seq"
    return "\n".join([
        f"DROP SEQUENCE IF EXISTS {tx_seq};",
        f"DROP SEQUENCE IF EXISTS {db_seq};",
        f"CREATE SEQUENCE {tx_seq} START WITH {max_tx_entry + 1};",
        f"CREATE SEQUENCE {db_seq} START WITH {max_db_entry + 1};",
        f"ALTER TABLE {v}_transactions ALTER COLUMN entry "
        f"SET DEFAULT nextval('{tx_seq}');",
        f"ALTER TABLE {v}_daily_balances ALTER COLUMN entry "
        f"SET DEFAULT nextval('{db_seq}');",
    ])


def _realign_v_overlay_entry_sequences(
    cur: object, cfg: "Config", base_prefix: str,
) -> None:
    """DI.1 — DuckDB-only post-CTAS sequence realignment.

    DuckDB's ``CREATE OR REPLACE TABLE v_X AS SELECT * FROM base_X``
    is the columnar fast-path clone but drops the table's
    ``entry`` column DEFAULT (the ``nextval('<v>_X_entry_seq')``
    reference). This helper fetches ``MAX(entry)`` from each v
    overlay base table and emits SQL to:

    1. Drop + recreate the v entry sequences starting at ``max+1``.
    2. ``ALTER TABLE`` to re-attach the DEFAULT.

    No-op for PG / Oracle — their clone path is DELETE+INSERT, which
    preserves the schema (CTAS isn't used). The function is silent
    on those dialects so callers can invoke it unconditionally.
    """
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    if cfg.db.dialect is not Dialect.DUCKDB:
        return
    v = v_overlay_prefix(base_prefix)

    def _max_entry(table: str) -> int:
        cur.execute(f"SELECT COALESCE(MAX(entry), 0) FROM {table}")  # type: ignore[attr-defined]: structural DBAPI cursor
        row: object = cur.fetchone()  # type: ignore[attr-defined]: structural DBAPI cursor
        # DBAPI cursor returns a sequence-like row (tuple / Row); index 0
        # is the COALESCE'd MAX(entry). Cast through ``object`` so the
        # structural cursor typing stays pyright-clean.
        if row is None:
            return 0
        val = row[0]  # type: ignore[index]: DBAPI rows are sequence-like
        return int(val) if val is not None else 0  # type: ignore[arg-type]: int() accepts the DB driver's numeric scalar

    max_tx_entry = _max_entry(f"{v}_transactions")
    max_db_entry = _max_entry(f"{v}_daily_balances")
    realign_sql = realign_v_overlay_entry_sequences_sql(
        base_prefix,
        max_tx_entry=max_tx_entry,
        max_db_entry=max_db_entry,
    )
    execute_script(cur, realign_sql, dialect=cfg.db.dialect)


def refresh_v_overlay_matviews_sql(
    instance: "L2Instance", *, base_prefix: str,
    dialect: object,
) -> str:
    """`refresh_matviews_sql` against the v overlay prefix."""
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    d = dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB
    return refresh_matviews_sql(
        instance, prefix=v_overlay_prefix(base_prefix), dialect=d,
    )


async def session_start(
    cfg: "Config", instance: "L2Instance",
    *,
    refresh_base: bool = True,
    l2_yaml_path: object = None,
    dev_log: object = None,
    subprocess_lock_bracket: (
        Callable[[], AbstractAsyncContextManager[None]] | None
    ) = None,
) -> None:
    """Orchestrates Session Start (DL.10):

    1. Optionally invoke `run_deploy_pipeline` (the /etl/run flow)
       against the base prefix — fresh upstream data + matview
       refresh on `<base>_*`. Skipped when ``refresh_base=False``
       (the Re-clone button uses this).
    2. Drop v overlay schema (idempotent — silently no-ops if absent).
    3. Create v overlay schema.
    4. Clone base → v overlay (data tables only).
    5. Refresh v overlay matviews.

    The /etl/run leg uses the `TRAINER_CLEAN` overlay (baseline only,
    no plants) since the operator's plant choices live on the v
    overlay — not the base.

    BV.4.10.d — `dev_log` is a ``DevLogWriter | None`` callback that
    accumulates per-step events for the live-tail UI. None silences
    progress events (CLI / test callers); supplying it makes the
    Studio's `/training/session-start/stream` endpoint useful.
    """
    import time as _time  # noqa: PLC0415

    async def _emit(event: str, **fields: object) -> None:
        if dev_log is None:
            return
        payload = {"event": event, "ts_unix": _time.time(), **fields}
        await dev_log(payload)  # type: ignore[misc]: dev_log is DevLogWriter | None; None-guarded above

    await _emit("session_start:begin", refresh_base=refresh_base)
    # CS.13 — probe for base schema BEFORE any work. Without this guard,
    # missing base manifested as a silent no-op (the drop_v_overlay swallows
    # "doesn't exist", the create_v emits empty tables, the clone fails
    # opaquely on PG/Oracle or succeeds with zero rows on DuckDB — the
    # operator clicks Session Start and the page renders fine but no plants
    # ever apply). Now we short-circuit with an actionable message.
    if not await asyncio.to_thread(_base_schema_exists, cfg):
        await _emit(
            "session_start:error",
            error_kind="base_schema_missing",
            base_prefix=cfg.db.table_prefix,
            remedy=(
                "Run `recon-gen schema apply --execute` against your demo DB, "
                "then click Session Start again."
            ),
        )
        raise BaseSchemaMissingError(cfg.db.table_prefix)
    if refresh_base:
        from recon_gen.common.l2.deploy_pipeline import (  # noqa: PLC0415
            run_deploy_pipeline,
        )
        from recon_gen.common.l2.pipeline_overlays import (  # noqa: PLC0415
            TRAINER_CLEAN,
        )
        await _emit("session_start:etl_begin")
        await run_deploy_pipeline(
            cfg, instance, dev_log=dev_log, overlays=TRAINER_CLEAN,  # type: ignore[arg-type]: opaque DevLogWriter shape passed through to deploy pipeline
            subprocess_lock_bracket=subprocess_lock_bracket,
        )
        await _emit("session_start:etl_done")

    base_prefix = cfg.db.table_prefix
    # DL.14 — capture L2 yaml mtime + clone time so the landing render
    # can flag staleness when the operator edits the yaml mid-session.
    import os  # noqa: PLC0415

    session_start_str = datetime.now().isoformat(timespec="seconds")  # typing-smell: ignore[no-datetime-now]: session-start UI banner — wall clock IS the contract
    l2_mtime_str = ""
    if l2_yaml_path is not None:
        try:
            l2_mtime_str = str(os.path.getmtime(str(l2_yaml_path)))
        except OSError:
            pass

    # BV.4.10.d — record per-step completion against this list so the
    # async caller can `await _emit(...)` each step. Closure shape
    # (rather than emit inline inside _run, which is sync) keeps the
    # sync DB work on the threadpool while dev_log writes stay on
    # the event loop.
    step_log: list[tuple[str, dict[str, object]]] = []

    def _run() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                # Best-effort drop — tolerate "doesn't exist."
                try:
                    drop_sql = drop_v_overlay_sql(
                        instance, base_prefix=base_prefix,
                        dialect=cfg.db.dialect,
                    )
                    execute_script(cur, drop_sql, dialect=cfg.db.dialect)
                    step_log.append(("session_start:drop_v_done", {}))
                except Exception as exc:  # noqa: BLE001 — schema may not exist; that's fine
                    step_log.append((
                        "session_start:drop_v_skipped",
                        {"reason": str(exc)[:80]},
                    ))

                create_sql = create_v_overlay_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.db.dialect,
                )
                execute_script(cur, create_sql, dialect=cfg.db.dialect)
                step_log.append(("session_start:create_v_done", {}))

                clone_sql = clone_base_to_v_sql(
                    base_prefix, dialect=cfg.db.dialect,
                )
                execute_script(cur, clone_sql, dialect=cfg.db.dialect)
                # DI.1 — DuckDB CTAS clone drops the v overlay tables'
                # entry-column DEFAULT (the nextval('<v>_X_entry_seq')
                # reference). Re-attach via realign so plant INSERTs
                # that omit ``entry`` keep working. PG/Oracle use the
                # DELETE+INSERT branch which preserves the schema, so
                # the realign is a no-op there.
                _realign_v_overlay_entry_sequences(cur, cfg, base_prefix)
                step_log.append(("session_start:clone_done", {}))

                refresh_sql = refresh_v_overlay_matviews_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.db.dialect,
                )
                execute_script(cur, refresh_sql, dialect=cfg.db.dialect)
                step_log.append(("session_start:refresh_matviews_done", {}))

                # DL.14 — record session-start metadata so the
                # /training/ landing render can flag staleness when
                # the L2 yaml mtime drifts vs this snapshot.
                execute_script(
                    cur,
                    _kv_write_sql(
                        base_prefix, _SESSION_START_KEY,
                        session_start_str, "__bv_session_start__",
                    ),
                    dialect=cfg.db.dialect,
                )
                if l2_mtime_str:
                    execute_script(
                        cur,
                        _kv_write_sql(
                            base_prefix, _L2_YAML_MTIME_KEY,
                            l2_mtime_str, "__bv_l2_mtime__",
                        ),
                        dialect=cfg.db.dialect,
                    )

                # Wipe stale applied / failed state from any prior
                # session — fresh clone means no plants are applied yet.
                execute_script(
                    cur,
                    _kv_write_sql(
                        base_prefix, _APPLIED_STATE_KEY, "{}",
                        "__bv_applied__",
                    ),
                    dialect=cfg.db.dialect,
                )
                execute_script(
                    cur,
                    _kv_write_sql(
                        base_prefix, _FAILED_STATE_KEY, "{}",
                        "__bv_failed__",
                    ),
                    dialect=cfg.db.dialect,
                )
                # CF.1 — wipe last-Apply banner state too so a fresh
                # Session Start (incl. reclone via refresh_base=False)
                # starts with no stale green/amber/red banner.
                execute_script(
                    cur,
                    _kv_write_sql(
                        base_prefix, _LAST_APPLY_KEY, "{}",
                        "__bv_last_apply__",
                    ),
                    dialect=cfg.db.dialect,
                )

                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)
    for event, fields in step_log:
        await _emit(event, **fields)
    await _emit("session_start:done")


async def cleanup(
    cfg: "Config", instance: "L2Instance",
) -> None:
    """Drop the v overlay schema. Base prefix untouched."""
    base_prefix = cfg.db.table_prefix

    def _run() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                drop_sql = drop_v_overlay_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.db.dialect,
                )
                execute_script(cur, drop_sql, dialect=cfg.db.dialect)
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)


@dataclass(frozen=True)
class ApplyDiff:
    """DL.9 — what changed between currently-applied plant state and
    the newly-requested checkbox/form selection.

    `unchanged` is in both with identical form-value fingerprints
    (no re-run needed). `to_add` is in the new selection but not
    currently applied (or with a changed fingerprint — treat as
    remove+add so the new fingerprint becomes truth without trying
    to surgically update one plant). `to_remove` is in current but
    not in the new selection (or with a changed fingerprint).

    Empty `to_remove` is the fast-path signal — skip the clone, just
    run new plants against existing v data. Non-empty `to_remove`
    triggers the slow-path reclone+replay since INSERT-style plants
    can be surgically `DELETE`'d but DELETE-style plants
    (uncovered_*, dead_*) can't be trivially undone; full reclone
    is the safe default.
    """
    unchanged: frozenset[str]
    to_add: frozenset[str]
    to_remove: frozenset[str]


def compute_apply_diff(
    current: Mapping[str, Mapping[str, str]],
    new: Mapping[str, Mapping[str, str]],
) -> ApplyDiff:
    """Pure diff between two `{kind: form_values}` maps. Tested
    directly without spinning up a DB."""
    current_keys = set(current.keys())
    new_keys = set(new.keys())
    same_fingerprint = frozenset(
        k for k in current_keys & new_keys if current[k] == new[k]
    )
    return ApplyDiff(
        unchanged=same_fingerprint,
        to_add=frozenset(new_keys - same_fingerprint),
        to_remove=frozenset(current_keys - same_fingerprint),
    )


async def apply_plants(
    cfg: "Config", instance: "L2Instance",
    enabled_plants: Iterable[tuple["PlantKindEntry", Mapping[str, object]]],
    *,
    anchor: datetime | None = None,
    dev_log: object = None,
) -> None:
    """BV.4.9 (DL.9) — diff-only Apply. Reads the currently-applied
    state from `<v>_config_kv` and decides between two paths:

    - **Fast path** (no kinds being removed / no fingerprint changes):
      keep the existing v overlay data, just run the newly-enabled
      plants. Skips the clone — Apply latency drops from
      ~clone+matview-refresh+N-plants to ~N-plants+matview-refresh.
    - **Slow path** (something has to come out — either an unchecked
      kind or a kind whose form values changed): reclone base → v
      and replay every enabled plant. Safe default because DELETE-
      style plants (uncovered_*, dead_*) can't be trivially undone.

    Each entry in ``enabled_plants`` is the registry entry + the
    operator's form values (the kwargs the plant_function expects).

    BV.4.10.d — ``dev_log`` is a ``DevLogWriter | None`` callback that
    accumulates per-step events for the live-tail UI (mirrors
    session_start's plumbing).
    """
    import json  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    async def _emit(event: str, **fields: object) -> None:
        if dev_log is None:
            return
        payload = {"event": event, "ts_unix": _time.time(), **fields}
        await dev_log(payload)  # type: ignore[misc]: dev_log is DevLogWriter | None; None-guarded above

    base_prefix = cfg.db.table_prefix
    v_prefix = v_overlay_prefix(base_prefix)
    anchor_dt = anchor or datetime(2026, 5, 30, 12, 0, 0)
    plants_list = list(enabled_plants)

    # Stringify the new selection's form values once so the diff
    # compares apples-to-apples with the persisted state (which is
    # stringified at write time).
    new_selection: dict[str, dict[str, str]] = {
        entry.kind: {k: str(v) for k, v in kwargs.items()}
        for entry, kwargs in plants_list
    }
    plants_by_kind: dict[str, tuple["PlantKindEntry", Mapping[str, object]]] = {
        entry.kind: (entry, kwargs) for entry, kwargs in plants_list
    }

    current_applied = await read_applied_state(cfg)
    diff = compute_apply_diff(current_applied, new_selection)
    needs_reclone = bool(diff.to_remove)
    await _emit(
        "apply:begin",
        path="slow" if needs_reclone else "fast",
        to_add=sorted(diff.to_add),
        to_remove=sorted(diff.to_remove),
        unchanged=sorted(diff.unchanged),
    )

    # BV.4.10.d — per-step log drained after the threadpool work.
    step_log: list[tuple[str, dict[str, object]]] = []

    def _run() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                # Failure dict starts fresh — last-Apply is the truth.
                # A previously-failed kind that the operator unchecked
                # this round shouldn't carry a stale error badge.
                failures: dict[str, str] = {}
                succeeded: dict[str, dict[str, str]] = {}

                if needs_reclone:
                    # Slow path: drop+clone wipes the v overlay; every
                    # enabled plant has to be re-run against fresh
                    # data (including kinds whose fingerprint didn't
                    # change — the cloned baseline no longer carries
                    # their planted rows).
                    clone_sql = clone_base_to_v_sql(
                        base_prefix, dialect=cfg.db.dialect,
                    )
                    execute_script(cur, clone_sql, dialect=cfg.db.dialect)
                    # DI.1 — DuckDB CTAS path drops the v overlay
                    # tables' entry-column DEFAULT; realign so the
                    # plant INSERTs below pick up nextval() again.
                    _realign_v_overlay_entry_sequences(
                        cur, cfg, base_prefix,
                    )
                    step_log.append(("apply:clone_done", {}))
                    kinds_to_run: list[
                        tuple["PlantKindEntry", Mapping[str, object]]
                    ] = plants_list
                else:
                    # Fast path: existing v overlay data stays; only
                    # the newly-added kinds get their plant_function
                    # invoked. Carry forward the already-succeeded
                    # state for unchanged kinds so the persisted
                    # ledger reflects the full enabled set.
                    succeeded = {
                        k: dict(current_applied[k]) for k in diff.unchanged
                    }
                    kinds_to_run = [
                        plants_by_kind[k] for k in diff.to_add
                        if k in plants_by_kind
                    ]

                # BV.3.3 — contract gate. The snapshot/restore lifecycle
                # assumes every plant_function only mutates v-overlay
                # tables; if a future entry widens its mutation surface
                # without updating the snapshot caller, fail loudly here
                # rather than corrupt the base prefix. The check runs
                # OUTSIDE the per-plant try/except — a contract violation
                # is a coding-time bug (registry entry misdeclares its
                # surface), not a runtime per-plant failure. Surface it
                # before any plant runs so the operator sees the
                # actionable RuntimeError instead of a buried "failed
                # plant" badge.
                allowed = _allowed_plant_mutation_surfaces()
                for entry, _kwargs in kinds_to_run:
                    if entry.contract.mutates not in allowed:
                        raise RuntimeError(
                            f"Plant {entry.kind!r} declares "
                            f"contract.mutates={entry.contract.mutates!r}, "
                            f"which apply_plants does not support. "
                            f"Allowed: {sorted(allowed)}."
                        )

                for entry, kwargs in kinds_to_run:
                    try:
                        plant_sql = entry.plant_function(
                            prefix=v_prefix,
                            dialect=cfg.db.dialect,
                            anchor=anchor_dt,
                            instance=instance,
                            **kwargs,
                        )
                        if plant_sql:
                            execute_script(
                                cur, plant_sql, dialect=cfg.db.dialect,
                            )
                        succeeded[entry.kind] = {
                            k: str(v) for k, v in kwargs.items()
                        }
                        step_log.append((
                            "apply:plant_done", {"kind": entry.kind},
                        ))
                    except Exception as exc:  # noqa: BLE001 — surfaces per kind
                        failures[entry.kind] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                        step_log.append((
                            "apply:plant_failed",
                            {"kind": entry.kind, "error": f"{type(exc).__name__}: {str(exc)[:80]}"},
                        ))

                # Refresh v matviews so the dashboards see the
                # composite of (existing v data + succeeded plants).
                # Always runs — even the fast path mutated v's base
                # tables, so matviews must be re-derived.
                refresh_sql = refresh_v_overlay_matviews_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.db.dialect,
                )
                execute_script(cur, refresh_sql, dialect=cfg.db.dialect)
                step_log.append(("apply:refresh_matviews_done", {}))

                state_sql = applied_state_write_sql(
                    base_prefix,
                    json.dumps(succeeded, separators=(",", ":")),
                )
                execute_script(cur, state_sql, dialect=cfg.db.dialect)

                failed_sql = _kv_write_sql(
                    base_prefix, _FAILED_STATE_KEY,
                    json.dumps(failures, separators=(",", ":")),
                    "__bv_failed__",
                )
                execute_script(cur, failed_sql, dialect=cfg.db.dialect)

                # CF.1 — record last-Apply outcome in one atomic
                # row so the v3 landing's banner can render
                # green/amber/red from kv on every GET (persists
                # across nav + Studio restart). datetime.now() at
                # the apply boundary, not at write-site, is
                # acceptable because this commit IS the apply
                # boundary.
                # Apply finish stamp is intentional wall-clock for
                # the operator-facing banner — not part of any locked
                # seed or deterministic artifact.
                finished_at_str = datetime.now().isoformat(timespec="seconds")  # typing-smell: ignore[no-datetime-now]: operator-facing apply timestamp, not seed-determinism input
                last_apply_payload = {
                    "attempted": sorted(
                        set(succeeded.keys()) | set(failures.keys())
                    ),
                    "succeeded": sorted(succeeded.keys()),
                    "failed": failures,
                    "finished_at": finished_at_str,
                    "path": "slow" if needs_reclone else "fast",
                }
                last_apply_sql = _kv_write_sql(
                    base_prefix, _LAST_APPLY_KEY,
                    json.dumps(last_apply_payload, separators=(",", ":")),
                    "__bv_last_apply__",
                )
                execute_script(cur, last_apply_sql, dialect=cfg.db.dialect)

                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)
    for event, fields in step_log:
        await _emit(event, **fields)
    await _emit("apply:done")


# -- date arg is unused at the moment but kept for symmetry with
# -- production deploy paths that thread `as_of`. Silences lint.
_ = date


# -- Applied-state persistence ----------------------------------------------
#
# The /training/ landing's checkbox state + per-card form values need to
# survive the POST→re-render hop AND inform DL.9 diff-only Apply. We
# park the state in a single row of `<v>_config_kv` keyed by a known
# parent_id. Cheap; survives Studio restarts; lives inside the v overlay
# so a Cleanup wipes it (correct — fresh Session Start should reset).


_APPLIED_STATE_KEY = "trainer_applied_plants"
_FAILED_STATE_KEY = "trainer_failed_plants"
_LAST_APPLY_KEY = "trainer_last_apply"
_SESSION_START_KEY = "trainer_session_start_time"
_L2_YAML_MTIME_KEY = "trainer_l2_yaml_mtime"


def _kv_read_sql(base_prefix: str, key: str) -> str:
    v = v_overlay_prefix(base_prefix)
    return (
        f"SELECT value FROM {v}_config_kv "
        f"WHERE parent_id = '__bv__' AND key = '{key}'"
    )


def _kv_write_sql(base_prefix: str, key: str, payload: str, node_id: str) -> str:
    """UPSERT shape — DELETE + INSERT works on PG / Oracle / sqlite
    without needing ON CONFLICT support."""
    v = v_overlay_prefix(base_prefix)
    escaped = payload.replace("'", "''")
    return "\n".join([
        (
            f"DELETE FROM {v}_config_kv "
            f"WHERE parent_id = '__bv__' AND key = '{key}';"
        ),
        (
            f"INSERT INTO {v}_config_kv "
            "(node_id, parent_id, key, value) VALUES "
            f"('{node_id}', '__bv__', '{key}', '{escaped}');"
        ),
    ])


def applied_state_read_sql(base_prefix: str) -> str:
    """SELECT the JSON-encoded applied-plant set from `<v>_config_kv`."""
    return _kv_read_sql(base_prefix, _APPLIED_STATE_KEY)


def applied_state_write_sql(base_prefix: str, json_payload: str) -> str:
    """UPSERT the JSON-encoded applied-plant set into `<v>_config_kv`."""
    return _kv_write_sql(
        base_prefix, _APPLIED_STATE_KEY, json_payload, "__bv_applied__",
    )


async def read_failed_kinds(cfg: "Config") -> dict[str, str]:
    """`{kind: error_message}` for plants whose plant_function or
    plant SQL raised in the last Apply. Empty when no Apply has
    fired or all succeeded."""
    import json  # noqa: PLC0415
    from typing import cast  # noqa: PLC0415

    base_prefix = cfg.db.table_prefix

    def _run() -> dict[str, str]:
        try:
            conn = connect_demo_db(cfg)
        except Exception:  # noqa: BLE001
            return {}
        try:
            cur = conn.cursor()
            try:
                try:
                    cur.execute(_kv_read_sql(base_prefix, _FAILED_STATE_KEY))
                    row = cur.fetchone()
                except Exception:  # noqa: BLE001
                    return {}
                if row is None or row[0] is None:
                    return {}
                try:
                    raw: object = json.loads(str(row[0]))
                except (ValueError, TypeError):
                    return {}
                if not isinstance(raw, dict):
                    return {}
                d = cast(dict[object, object], raw)
                return {str(k): str(v) for k, v in d.items()}
            finally:
                cur.close()
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


async def read_last_apply(cfg: "Config") -> dict[str, object] | None:
    """CF.1 — last-Apply outcome for the kv-sourced banner. Three
    return states:

    - ``None`` — kv unreachable (connect error / cursor error / JSON
      parse error / non-dict shape). Banner should render nothing
      under this state, NOT fall back to a stale optimistic claim.
    - ``{}`` — empty dict, row exists with value '{}' (Session Start
      has fired but no Apply yet). Banner renders nothing.
    - populated dict with ``finished_at`` — render
      green/amber/red based on ``succeeded`` + ``failed``.

    Payload shape (last-Apply-wins, not cumulative):
    ``{"attempted": [<kind>, ...],
        "succeeded": [<kind>, ...],
        "failed": {<kind>: <ExcName: msg>, ...},
        "finished_at": <ISO seconds, local TZ>,
        "path": "fast" | "slow"}``
    """
    import json  # noqa: PLC0415

    base_prefix = cfg.db.table_prefix

    def _run() -> dict[str, object] | None:
        try:
            conn = connect_demo_db(cfg)
        except Exception:  # noqa: BLE001
            return None
        try:
            cur = conn.cursor()
            try:
                try:
                    cur.execute(_kv_read_sql(base_prefix, _LAST_APPLY_KEY))
                    row = cur.fetchone()
                except Exception:  # noqa: BLE001
                    return None
                if row is None or row[0] is None:
                    return None
                try:
                    raw: object = json.loads(str(row[0]))
                except (ValueError, TypeError):
                    return None
                if not isinstance(raw, dict):
                    return None
                # Empty kv row (Session Start wipe) → empty dict; banner
                # render branch on `not last_apply` short-circuits.
                from typing import cast  # noqa: PLC0415
                d = cast(dict[object, object], raw)
                return {str(k): v for k, v in d.items()}
            finally:
                cur.close()
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


async def read_session_metadata(cfg: "Config") -> dict[str, str]:
    """Session-start timestamp + L2 yaml mtime captured at Session
    Start (DL.14 staleness banner). Empty when no Session Start has
    fired."""
    base_prefix = cfg.db.table_prefix

    def _run() -> dict[str, str]:
        try:
            conn = connect_demo_db(cfg)
        except Exception:  # noqa: BLE001
            return {}
        try:
            cur = conn.cursor()
            out: dict[str, str] = {}
            for key in (_SESSION_START_KEY, _L2_YAML_MTIME_KEY):
                try:
                    cur.execute(_kv_read_sql(base_prefix, key))
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        out[key] = str(row[0])
                except Exception:  # noqa: BLE001
                    pass
            cur.close()
            return out
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


def session_metadata_session_start_key() -> str:
    """Constant exposing the session-start key name so render code
    can pull it off the dict without re-hardcoding."""
    return _SESSION_START_KEY


def session_metadata_l2_mtime_key() -> str:
    return _L2_YAML_MTIME_KEY


async def read_applied_state(
    cfg: "Config",
) -> dict[str, dict[str, str]]:
    """Read the persisted `{kind: form_values}` map from `<v>_config_kv`.

    Returns empty dict when the v overlay doesn't exist OR the state
    row is absent (no Apply has ever fired)."""
    import json  # noqa: PLC0415

    base_prefix = cfg.db.table_prefix

    def _run() -> dict[str, dict[str, str]]:
        try:
            conn = connect_demo_db(cfg)
        except Exception:  # noqa: BLE001
            return {}
        try:
            cur = conn.cursor()
            try:
                try:
                    cur.execute(applied_state_read_sql(base_prefix))
                    row = cur.fetchone()
                except Exception:  # noqa: BLE001
                    return {}
                if row is None or row[0] is None:
                    return {}
                try:
                    raw: object = json.loads(str(row[0]))
                except (ValueError, TypeError):
                    return {}
                if not isinstance(raw, dict):
                    return {}
                # Coerce to dict[str, dict[str, str]] defensively.
                # Casting at the loop boundary keeps pyright strict happy.
                from typing import cast  # noqa: PLC0415
                raw_dict = cast(dict[object, object], raw)
                out: dict[str, dict[str, str]] = {}
                for k, v in raw_dict.items():
                    if not isinstance(v, dict):
                        continue
                    v_dict = cast(dict[object, object], v)
                    out[str(k)] = {
                        str(fk): str(fv) for fk, fv in v_dict.items()
                    }
                return out
            finally:
                cur.close()
        finally:
            conn.close()

    return await asyncio.to_thread(_run)

