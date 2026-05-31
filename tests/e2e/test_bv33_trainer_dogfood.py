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


def _browser_walkable_kinds() -> list[PlantKindEntry]:
    """Filter `PLANT_REGISTRY` to kinds whose Tour navigates to a
    dashboard sheet (not `/etl/triage` — those have a unit-tier BV.3.1
    surface) AND whose `dashboard_check.matview_name` lets the test
    query the planted row's account_id by matview-diff.

    The 5 `/etl/triage` kinds (`phantom_rail`, `phantom_template`,
    `missing_metadata_key`, `uncovered_rail`, `uncovered_template`)
    are skipped here — BV.3.1's matview-shape probe already covers
    them. The 5 no-matview kinds (`supersession_audit` + L2FT
    Hygiene) are skipped pending a per-kind transaction-id signature
    pattern; BV.3.3 backlog."""
    out: list[PlantKindEntry] = []
    for e in PLANT_REGISTRY:
        check = e.dashboard_check
        url = check.url_path or ""
        if "/etl/" in url:
            continue
        if check.matview_name is None:
            continue
        out.append(e)
    return out


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


def _v_matview_signatures(
    sqlite_path: Path, v_matview_name: str, matview_name: str,
) -> set[tuple[str, ...]]:
    """Snapshot a matview's planted-row signatures: pick the columns
    that exist (from a known priority list — account_id beats
    transfer_id beats child_transfer_id) and read them as tuples.

    Different matviews have different shapes (account-oriented for
    L1 conservation/cap; transfer-oriented for chain coherence).
    Reading the actual table info via PRAGMA + intersecting with a
    priority list keeps the signature column-selection robust without
    a per-matview hardcoded map (which kept drifting from the actual
    schema during BV.3.3.c iteration).

    Signature elements are concatenated to produce a "look for this
    in the rendered HTML" haystack for the surface assertion.
    """
    del matview_name  # kept on signature for future per-matview override
    # Priority order: prefer the most-uniquely-identifying columns
    # first so the diff against the BEFORE snapshot doesn't collapse
    # plants that share an account_id with a baseline row.
    priority = (
        "transfer_id", "child_transfer_id", "parent_transfer_id",
        "account_id", "business_day", "business_day_start",
        "rail_name", "direction",
    )
    conn = sqlite3.connect(str(sqlite_path))
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"PRAGMA table_info({v_matview_name})")
            cols = {str(row[1]) for row in cur.fetchall()}
            picked = [c for c in priority if c in cols]
            if not picked:
                # No recognizable id columns — fall back to selecting
                # ROWID so callers at least get a meaningful diff.
                cur.execute(f"SELECT ROWID FROM {v_matview_name}")
                return {(str(row[0]),) for row in cur.fetchall()}
            cols_sql = ", ".join(picked)
            cur.execute(
                f"SELECT DISTINCT {cols_sql} FROM {v_matview_name}"
            )
            return {
                tuple("" if v is None else str(v) for v in row)
                for row in cur.fetchall()
            }
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


@pytest.mark.browser
def test_bv33c_full_registry_walk_sqlite(tmp_path: Path) -> None:
    """BV.3.3.c — single-Session-Start cumulative walk over every
    browser-walkable, matview-bound kind. Per kind:

    1. Snapshot the v matview's account_ids (before).
    2. Enable just that kind on top of the cumulative state (DL.9
       fast-path keeps the per-kind Apply cheap — no reclone).
    3. Apply, snapshot account_ids (after); compute set diff.
    4. Navigate to the kind's Violation Tour, read `.table-data`
       inner HTML.
    5. Assert one of the new account_ids appears in the rendered
       HTML.

    Cumulative: each Apply adds the new kind on top of the prior
    plants (the test never unchecks anything). Set-diff identifies
    THIS kind's new account_ids — assertion stays sharp even when
    prior plants land on the same matview.

    Failures collected per kind so one bad kind doesn't mask the
    rest; the test ``pytest.fail``s at the end with a per-kind
    breakdown if any failed."""
    from tests.e2e._drivers.app2 import App2Driver  # noqa: PLC0415

    cfg, sqlite_path = make_studio_cfg(tmp_path)
    _seed_demo_db(cfg)

    walkable = _browser_walkable_kinds()
    base_prefix = cfg.db_table_prefix
    cumulative_enabled: dict[str, str] = {}
    failures: list[str] = []
    successes: list[str] = []

    with studio_server(cfg) as base_url, App2Driver.attached_to(
        base_url=base_url, cfg=cfg,
    ) as driver:
        driver.open_training()
        driver.trainer_start_session()

        for entry in walkable:
            matview = entry.dashboard_check.matview_name
            assert matview is not None  # walkable filter guarantees this
            v_matview = f"{base_prefix}_v_{matview}"

            try:
                before = _v_matview_signatures(sqlite_path, v_matview, matview)
            except sqlite3.OperationalError as exc:
                failures.append(
                    f"{entry.kind}: BEFORE-snapshot failed ({exc!s})"
                )
                continue

            # Cumulative: the landing renders with prior kinds'
            # checkboxes already ticked (from v_config_kv applied
            # state). Only the NEW kind needs ticking — DL.9 fast
            # path keeps the Apply cheap (one new plant, no clone).
            try:
                driver.open_training()
                driver.trainer_enable_plant(entry.kind, entry.family)
                driver.trainer_apply()
            except Exception as exc:  # noqa: BLE001 — collect-failures
                failures.append(f"{entry.kind}: Apply failed ({exc!s})")
                continue
            cumulative_enabled[entry.kind] = entry.family

            try:
                after = _v_matview_signatures(sqlite_path, v_matview, matview)
            except sqlite3.OperationalError as exc:
                failures.append(
                    f"{entry.kind}: AFTER-snapshot failed ({exc!s})"
                )
                continue

            new_sigs = after - before
            if not new_sigs:
                diag = _diagnose_v_state(sqlite_path, base_prefix)
                failures.append(
                    f"{entry.kind}: no new rows in {v_matview} "
                    f"(diag: {diag})"
                )
                continue

            try:
                driver.trainer_take_violation_tour(entry.kind, entry.family)
                rendered = driver.dashboard_table_inner_html()
            except Exception as exc:  # noqa: BLE001 — collect-failures
                failures.append(f"{entry.kind}: Tour failed ({exc!s})")
                continue

            # Surface assertion: any element of any new signature tuple
            # appearing in the rendered HTML. Loose but works because
            # signatures are designed plant-unique on at least one
            # element (account_id for L1 invariants, parent_transfer_id
            # for chain coherence).
            hit = next(
                (
                    val for sig in new_sigs for val in sig
                    if val and val in rendered
                ),
                None,
            )
            if hit is None:
                failures.append(
                    f"{entry.kind}: planted signatures "
                    f"{sorted(new_sigs)} absent from rendered "
                    f"dashboard table"
                )
                continue

            successes.append(f"{entry.kind} ✓ (sig={hit})")

    if failures:
        lines = ["BV.3.3.c failures:"]
        lines.extend(f"  ✗ {f}" for f in failures)
        lines.append(f"successes ({len(successes)}):")
        lines.extend(f"  ✓ {s}" for s in successes)
        pytest.fail("\n".join(lines))
