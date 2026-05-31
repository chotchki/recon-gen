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
    *,
    refresh_base: bool = True,
    l2_yaml_path: object = None,
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
    """
    if refresh_base:
        from recon_gen.common.l2.deploy_pipeline import (  # noqa: PLC0415
            run_deploy_pipeline,
        )
        from recon_gen.common.l2.pipeline_overlays import (  # noqa: PLC0415
            TRAINER_CLEAN,
        )
        await run_deploy_pipeline(
            cfg, instance, dev_log=None, overlays=TRAINER_CLEAN,
        )

    base_prefix = cfg.db_table_prefix
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

                # DL.14 — record session-start metadata so the
                # /training/ landing render can flag staleness when
                # the L2 yaml mtime drifts vs this snapshot.
                execute_script(
                    cur,
                    _kv_write_sql(
                        base_prefix, _SESSION_START_KEY,
                        session_start_str, "__bv_session_start__",
                    ),
                    dialect=cfg.dialect,
                )
                if l2_mtime_str:
                    execute_script(
                        cur,
                        _kv_write_sql(
                            base_prefix, _L2_YAML_MTIME_KEY,
                            l2_mtime_str, "__bv_l2_mtime__",
                        ),
                        dialect=cfg.dialect,
                    )

                # Wipe stale applied / failed state from any prior
                # session — fresh clone means no plants are applied yet.
                execute_script(
                    cur,
                    _kv_write_sql(
                        base_prefix, _APPLIED_STATE_KEY, "{}",
                        "__bv_applied__",
                    ),
                    dialect=cfg.dialect,
                )
                execute_script(
                    cur,
                    _kv_write_sql(
                        base_prefix, _FAILED_STATE_KEY, "{}",
                        "__bv_failed__",
                    ),
                    dialect=cfg.dialect,
                )

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
    import json  # noqa: PLC0415

    base_prefix = cfg.db_table_prefix
    v_prefix = v_overlay_prefix(base_prefix)
    anchor_dt = anchor or datetime(2026, 5, 30, 12, 0, 0)
    plants_list = list(enabled_plants)

    # applied_snapshot / applied_json moved INSIDE _run so we
    # serialize the SUCCEEDED set, not the requested set. The
    # `succeeded` dict (populated per-plant below) is what gets
    # persisted.

    # Track per-plant failures so the UI can render error badges
    # (DL.12). The plant SQL emission is the most likely failure point
    # (picker can't satisfy the L2 → ValueError from the adapter).
    failures: dict[str, str] = {}
    succeeded: dict[str, dict[str, str]] = {}

    def _run() -> None:
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            try:
                # Re-clone base → v (naive shape — DL.9 lands incremental).
                clone_sql = clone_base_to_v_sql(base_prefix)
                execute_script(cur, clone_sql, dialect=cfg.dialect)

                # Emit each enabled plant's SQL against the v prefix —
                # per-plant try/except so one failure doesn't drop the
                # whole batch.
                for entry, kwargs in plants_list:
                    try:
                        plant_sql = entry.plant_function(
                            prefix=v_prefix,
                            dialect=cfg.dialect,
                            anchor=anchor_dt,
                            instance=instance,
                            **kwargs,
                        )
                        if plant_sql:
                            execute_script(cur, plant_sql, dialect=cfg.dialect)
                        succeeded[entry.kind] = {
                            k: str(v) for k, v in kwargs.items()
                        }
                    except Exception as exc:  # noqa: BLE001 — surfaces per kind
                        failures[entry.kind] = (
                            f"{type(exc).__name__}: {exc}"
                        )

                # Refresh v matviews so the dashboards see the
                # composite of (cloned baseline + succeeded plants).
                refresh_sql = refresh_v_overlay_matviews_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                )
                execute_script(cur, refresh_sql, dialect=cfg.dialect)

                # Persist applied state — only includes kinds that
                # actually emitted SQL successfully. Failed kinds
                # surface in the parallel failed-state row so the UI
                # can render error badges WITHOUT pretending the
                # plant landed.
                state_sql = applied_state_write_sql(
                    base_prefix,
                    json.dumps(succeeded, separators=(",", ":")),
                )
                execute_script(cur, state_sql, dialect=cfg.dialect)

                failed_sql = _kv_write_sql(
                    base_prefix, _FAILED_STATE_KEY,
                    json.dumps(failures, separators=(",", ":")),
                    "__bv_failed__",
                )
                execute_script(cur, failed_sql, dialect=cfg.dialect)

                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)


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

    base_prefix = cfg.db_table_prefix

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


async def read_session_metadata(cfg: "Config") -> dict[str, str]:
    """Session-start timestamp + L2 yaml mtime captured at Session
    Start (DL.14 staleness banner). Empty when no Session Start has
    fired."""
    base_prefix = cfg.db_table_prefix

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

    base_prefix = cfg.db_table_prefix

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

