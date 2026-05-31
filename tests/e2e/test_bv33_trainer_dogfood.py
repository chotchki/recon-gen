"""BV.3.3 — Trainer dogfood browser e2e.

Drives the FULL Trainer flow through real Playwright WebKit against
a real Studio + 4-dashboards uvicorn server backed by a real sqlite
DB. Per registry kind:

1. Pre-seed sqlite with base schema + baseline data.
2. Operator opens `/training/`.
3. Operator clicks Session Start; v overlay schema appears.
4. Operator ticks the kind's enable checkbox + fills form fields.
5. Operator clicks Apply.
6. Operator clicks the Violation Tour link (carries `?prefix=<v>`).
7. Test reads the rendered dashboard surface AND the v matview row,
   asserts the planted row's identifying signature surfaces in the
   rendered table (the cheap "count went up" gate bit us in the past
   — BO.1 cluster — so we assert the specific row).

BV.3.3.a — vertical slice: one kind (`limit_breach_outbound`) on
sqlite. BV.3.3.b will expand to the full registry. BV.3.3.c adds
PG + Oracle Docker variants.

Gated behind ``RECON_GEN_E2E=1`` per conftest (the `browser` marker).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

# Skip the whole module when Playwright isn't available — keeps the
# rest of the e2e suite collectible without the browser tier
# installed.
pytest.importorskip("playwright.sync_api")

from recon_gen.cli._helpers import build_config_populate_sql
from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY, PlantKindEntry
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.seed import emit_full_seed
from tests.e2e._studio_deploy_helpers import (
    SASQUATCH_YAML,
    make_studio_cfg,
    studio_server,
)


def _seed_demo_db(cfg_obj: object) -> None:
    """Apply base schema + full seed + matview refresh against the
    cfg's demo DB. The Studio server picks up these tables when the
    operator clicks Session Start (the /etl/run leg sees the existing
    rows + refreshes matviews; nothing to regenerate)."""
    from recon_gen.common.config import Config  # noqa: PLC0415

    assert isinstance(cfg_obj, Config)
    cfg = cfg_obj
    instance = load_instance(SASQUATCH_YAML)
    scenarios = default_scenario_for(instance).scenario
    base_prefix = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(
                cur,
                emit_schema(instance, prefix=base_prefix, dialect=cfg.dialect),
                dialect=cfg.dialect,
            )
            # Populate config_kv from the L2 declaration so cap-joining
            # matviews (limit_breach, stuck_pending, etc.) can resolve
            # their config views. Without this the matview's LEFT JOIN
            # against `<prefix>_v_config_limit_schedules` returns NULL
            # and the breach filter excludes the planted row.
            execute_script(
                cur,
                build_config_populate_sql(cfg, instance, anchor=date(2026, 5, 30)),
                dialect=cfg.dialect,
            )
            execute_script(
                cur,
                emit_full_seed(
                    instance, scenarios,
                    prefix=base_prefix, dialect=cfg.dialect,
                    anchor=date(2026, 5, 30),
                ),
                dialect=cfg.dialect,
            )
            execute_script(
                cur,
                refresh_matviews_sql(
                    instance, prefix=base_prefix, dialect=cfg.dialect,
                ),
                dialect=cfg.dialect,
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def _pick_kind(kind: str) -> PlantKindEntry:
    return next(e for e in PLANT_REGISTRY if e.kind == kind)


def _v_matview_account_ids(
    sqlite_path: Path, v_matview_name: str,
) -> set[str]:
    """Snapshot the v overlay's matview rows by account_id. The
    diff between pre-Apply + post-Apply identifies what the plant
    added — used for the dashboard-surface assertion below."""
    conn = sqlite3.connect(str(sqlite_path))
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT DISTINCT account_id FROM {v_matview_name}")
            return {str(row[0]) for row in cur.fetchall()}
        finally:
            cur.close()
    finally:
        conn.close()


def _diagnose_v_state(
    sqlite_path: Path, base_prefix: str,
) -> dict[str, object]:
    """Dump v overlay state for triage. Reads the trainer config_kv
    rows + counts the v transactions table so a failed Apply tells
    us what shape the overlay was in when the assertion fired."""
    conn = sqlite3.connect(str(sqlite_path))
    try:
        cur = conn.cursor()
        try:
            out: dict[str, object] = {}
            for key in (
                "trainer_applied_plants", "trainer_failed_plants",
                "trainer_session_start_time",
            ):
                try:
                    cur.execute(
                        f"SELECT value FROM {base_prefix}_v_config_kv "
                        f"WHERE parent_id = '__bv__' AND key = '{key}'"
                    )
                    row = cur.fetchone()
                    out[key] = row[0] if row else None
                except sqlite3.Error as exc:
                    out[key] = f"<sqlite err: {exc}>"
            for table in (
                f"{base_prefix}_v_transactions",
                f"{base_prefix}_transactions",
                f"{base_prefix}_v_current_transactions",
                f"{base_prefix}_v_limit_breach",
            ):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    out[f"{table}_count"] = int(cur.fetchone()[0])
                except sqlite3.Error as exc:
                    out[f"{table}_count"] = f"<sqlite err: {exc}>"
            # The plant emits a row with id like 'tx-limit-breach-...';
            # check whether it survived the clone+plant pipe.
            try:
                cur.execute(
                    f"SELECT id, account_id, account_scope, "
                    f"account_parent_role, amount_money, "
                    f"amount_direction, status, posting, rail_name "
                    f"FROM {base_prefix}_v_transactions "
                    f"WHERE id LIKE 'tx-limit-breach%' LIMIT 5"
                )
                out["planted_tx_rows"] = [tuple(r) for r in cur.fetchall()]
            except sqlite3.Error as exc:
                out["planted_tx_rows"] = f"<sqlite err: {exc}>"
            # Cap-join shape: does the v overlay's config view know
            # about CustomerCashWithdrawal?
            try:
                cur.execute(
                    f"SELECT parent_role, rail, direction, cap "
                    f"FROM {base_prefix}_v_config_limit_schedules "
                    f"WHERE rail = 'CustomerCashWithdrawal'"
                )
                out["v_config_schedule_for_rail"] = [tuple(r) for r in cur.fetchall()]
            except sqlite3.Error as exc:
                out["v_config_schedule_for_rail"] = f"<sqlite err: {exc}>"
            # Cross-check base
            try:
                cur.execute(
                    f"SELECT parent_role, rail, direction, cap "
                    f"FROM {base_prefix}_config_limit_schedules "
                    f"WHERE rail = 'CustomerCashWithdrawal'"
                )
                out["base_config_schedule_for_rail"] = [tuple(r) for r in cur.fetchall()]
            except sqlite3.Error as exc:
                out["base_config_schedule_for_rail"] = f"<sqlite err: {exc}>"
            # Count config_kv rows in v vs base.
            for table in (
                f"{base_prefix}_config_kv", f"{base_prefix}_v_config_kv",
            ):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    out[f"{table}_rowcount"] = int(cur.fetchone()[0])
                except sqlite3.Error as exc:
                    out[f"{table}_rowcount"] = f"<sqlite err: {exc}>"
            return out
        finally:
            cur.close()
    finally:
        conn.close()


@pytest.mark.browser
def test_bv33a_limit_breach_outbound_trainer_dogfood(
    tmp_path: Path,
) -> None:
    """BV.3.3.a vertical slice — drive the Trainer end-to-end for
    `limit_breach_outbound` and assert the planted row surfaces in
    the Limit Breach dashboard sheet AT the rendered HTML layer.

    Drives through ``App2Driver``'s trainer verbs (BV.3.3.b)."""
    from tests.e2e._drivers.app2 import App2Driver  # noqa: PLC0415

    cfg, sqlite_path = make_studio_cfg(tmp_path)
    _seed_demo_db(cfg)

    entry = _pick_kind("limit_breach_outbound")
    v_matview = f"{cfg.db_table_prefix}_v_limit_breach"

    with studio_server(cfg) as base_url, App2Driver.attached_to(
        base_url=base_url, cfg=cfg,
    ) as driver:
        driver.open_training()
        driver.trainer_start_session()

        before = _v_matview_account_ids(sqlite_path, v_matview)
        driver.trainer_enable_plant(entry.kind, entry.family)
        driver.trainer_apply()

        after = _v_matview_account_ids(sqlite_path, v_matview)
        new_accounts = after - before
        if not new_accounts:
            diag = _diagnose_v_state(sqlite_path, cfg.db_table_prefix)
            pytest.fail(
                f"{entry.kind} plant didn't add a row to the v overlay's "
                f"{v_matview}. before={sorted(before)} after={sorted(after)}. "
                f"v_config_kv state: {diag}"
            )

        driver.trainer_take_violation_tour(entry.kind, entry.family)
        rendered_html = driver.dashboard_table_inner_html()
        hit = next(
            (acc for acc in new_accounts if acc in rendered_html),
            None,
        )
        assert hit is not None, (
            f"{entry.kind} planted account_ids "
            f"{sorted(new_accounts)} should appear in the rendered "
            f"Limit Breach table; got HTML head: "
            f"{rendered_html[:500]!r}"
        )
