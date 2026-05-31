"""BV.4.1 — v-overlay lifecycle orchestration.

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

The v-overlay's prefix is always ``<cfg.db_table_prefix>_v`` —
derived at runtime, not configured.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
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


def v_overlay_prefix(base_prefix: str) -> str:
    """The conventional `<base>_v` suffix per DL.3.

    Single function so callers don't string-concatenate ad hoc —
    keeps a future rename atomic."""
    return f"{base_prefix}_v"


def create_v_overlay_sql(
    instance: "L2Instance", *, base_prefix: str,
    dialect: object,
) -> str:
    """Schema DDL for the v overlay. Mirrors the base prefix's schema
    one-for-one but under the `_v` suffix. Idempotent: callers should
    drop + recreate via :func:`drop_v_overlay_sql` first if the
    schema may already exist (Studio's Session Start does)."""
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    d = dialect if isinstance(dialect, Dialect) else Dialect.SQLITE
    return emit_schema(
        instance, prefix=v_overlay_prefix(base_prefix), dialect=d,
    )


def drop_v_overlay_sql(
    instance: "L2Instance", *, base_prefix: str,
    dialect: object,
) -> str:
    """Drop every per-prefix object the v overlay created."""
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    d = dialect if isinstance(dialect, Dialect) else Dialect.SQLITE
    return emit_schema_drop_sql(
        instance, prefix=v_overlay_prefix(base_prefix), dialect=d,
    )


def clone_base_to_v_sql(base_prefix: str) -> str:
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
    """
    v = v_overlay_prefix(base_prefix)
    return "\n".join([
        f"DELETE FROM {v}_transactions;",
        f"DELETE FROM {v}_daily_balances;",
        f"DELETE FROM {v}_config_kv;",
        f"INSERT INTO {v}_transactions SELECT * FROM {base_prefix}_transactions;",
        f"INSERT INTO {v}_daily_balances SELECT * FROM {base_prefix}_daily_balances;",
        f"INSERT INTO {v}_config_kv SELECT * FROM {base_prefix}_config_kv;",
    ])


def refresh_v_overlay_matviews_sql(
    instance: "L2Instance", *, base_prefix: str,
    dialect: object,
) -> str:
    """`refresh_matviews_sql` against the v overlay prefix."""
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    d = dialect if isinstance(dialect, Dialect) else Dialect.SQLITE
    return refresh_matviews_sql(
        instance, prefix=v_overlay_prefix(base_prefix), dialect=d,
    )


async def session_start(
    cfg: "Config", instance: "L2Instance",
) -> None:
    """Orchestrates Session Start (DL.10):

    1. Drop v overlay schema (idempotent — silently no-ops if absent).
    2. Create v overlay schema.
    3. Clone base → v overlay (data tables only).
    4. Refresh v overlay matviews.

    Per DL.3.a — does NOT call etl_hook. The route handler is
    responsible for chaining /etl/run BEFORE invoking session_start
    when the operator clicks the Session Start button (so the base
    prefix is current before the clone snapshot).
    """
    base_prefix = cfg.db_table_prefix

    def _run() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                # Best-effort drop — tolerate "doesn't exist."
                try:
                    drop_sql = drop_v_overlay_sql(
                        instance, base_prefix=base_prefix,
                        dialect=cfg.dialect,
                    )
                    execute_script(cur, drop_sql, dialect=cfg.dialect)
                except Exception:  # noqa: BLE001 — schema may not exist; that's fine
                    pass

                create_sql = create_v_overlay_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                )
                execute_script(cur, create_sql, dialect=cfg.dialect)

                clone_sql = clone_base_to_v_sql(base_prefix)
                execute_script(cur, clone_sql, dialect=cfg.dialect)

                refresh_sql = refresh_v_overlay_matviews_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                )
                execute_script(cur, refresh_sql, dialect=cfg.dialect)

                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)


async def cleanup(
    cfg: "Config", instance: "L2Instance",
) -> None:
    """Drop the v overlay schema. Base prefix untouched."""
    base_prefix = cfg.db_table_prefix

    def _run() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                drop_sql = drop_v_overlay_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                )
                execute_script(cur, drop_sql, dialect=cfg.dialect)
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)


async def apply_plants(
    cfg: "Config", instance: "L2Instance",
    enabled_plants: Iterable[tuple["PlantKindEntry", Mapping[str, object]]],
    *,
    anchor: datetime | None = None,
) -> None:
    """Naive Apply (vertical-slice shape, NOT DL.9 diff-only):
    re-clone base → v then replay enabled plants in registry order.

    DL.9 diff-only Apply lands in BV.4.4 — at that point this
    function gets replaced with a state-diff against
    `<v>_config_kv`'s `trainer_applied_plants` row. For the BV.4.0
    vertical slice we do the safe clone-and-replay shape so we can
    prove the rest of the architecture first.

    Each entry in ``enabled_plants`` is the registry entry + the
    operator's form values (the kwargs the plant_function expects).
    """
    base_prefix = cfg.db_table_prefix
    v_prefix = v_overlay_prefix(base_prefix)
    anchor_dt = anchor or datetime(2026, 5, 30, 12, 0, 0)
    plants_list = list(enabled_plants)

    def _run() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                # Re-clone base → v (naive shape — DL.9 lands incremental).
                clone_sql = clone_base_to_v_sql(base_prefix)
                execute_script(cur, clone_sql, dialect=cfg.dialect)

                # Emit each enabled plant's SQL against the v prefix.
                for entry, kwargs in plants_list:
                    plant_sql = entry.plant_function(
                        prefix=v_prefix,
                        dialect=cfg.dialect,
                        anchor=anchor_dt,
                        instance=instance,
                        **kwargs,
                    )
                    if plant_sql:
                        execute_script(cur, plant_sql, dialect=cfg.dialect)

                # Refresh v matviews so the dashboards see the
                # composite of (cloned baseline + applied plants).
                refresh_sql = refresh_v_overlay_matviews_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                )
                execute_script(cur, refresh_sql, dialect=cfg.dialect)

                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)


# -- date arg is unused at the moment but kept for symmetry with
# -- production deploy paths that thread `as_of`. Silences lint.
_ = date
