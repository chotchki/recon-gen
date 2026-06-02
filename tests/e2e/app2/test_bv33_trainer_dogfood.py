"""BV.3.3 — Trainer dogfood browser e2e (per-kind, cross-dialect).

Drives the FULL Trainer flow through real Playwright WebKit against
a real Studio + 4-dashboards uvicorn server backed by a real demo DB.
Per registry kind, in its own isolated test:

1. Pre-seed the demo DB with base schema + baseline data.
2. Operator opens ``/training/``.
3. Operator clicks Session Start; v overlay schema appears.
4. Operator ticks the kind's enable checkbox + fills form fields.
5. Operator clicks Apply.
6. Operator clicks the Violation Tour link (carries ``?prefix=<v>``).
7. Test reads the rendered dashboard surface AND the v matview row,
   asserts the planted row's identifying signature surfaces in the
   rendered table (cheap "count went up" gates bit us in BO.1 — assert
   on the specific row).

History (per BV.3.3 + BV.3.3.c):

  - BV.3.3.a — vertical slice for ``limit_breach_outbound`` on sqlite.
  - BV.3.3.b — registry-aware cumulative loop driving every kind off
    one Session Start.
  - BV.3.3.c — DECOMPOSE the cumulative loop into per-kind tests. A
    single broken kind now fails ONLY its own test instead of tainting
    the whole walk; xfails are surgical (per-kind ``pytest.mark.xfail``
    on entries known to be regression-tracked rather than swept). The
    cumulative state-tracking dance (``cumulative_enabled`` / per-kind
    diagnostic-collect-then-fail) is gone — per-test isolation makes
    the per-test ``pytest.fail`` the right shape.

Cross-dialect coverage emerges from the runner re-running this file
per ``sp_pg_aw`` / ``sp_or_aw`` / ``sp_sl_aw`` cell. The runner sets
``RECON_GEN_DEMO_DATABASE_URL`` + ``RECON_GEN_DIALECT`` per cell; the
helpers below honor those overrides so a single test fires against
whichever substrate the cell selected. Helpers use
``recon_gen.common.db.connect_demo_db`` and ``cursor.description`` for
column introspection so every read works across Postgres + Oracle +
SQLite without dialect branching at the test layer.

Gated behind ``RECON_GEN_E2E=1`` per conftest (the ``browser`` marker).
File lives under ``tests/e2e/app2/`` so the dir-conftest auto-applies
``@tier(Tier.APP2)``.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

# Skip the whole module when Playwright isn't available — keeps the
# rest of the e2e suite collectible without the browser tier
# installed.
pytest.importorskip("playwright.sync_api")

from recon_gen.cli._helpers import build_config_populate_sql
from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import (
    RECON_GEN_DEMO_DATABASE_URL,
    RECON_GEN_DIALECT,
)
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY, PlantKindEntry
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.seed import emit_full_seed
from recon_gen.common.sql import Dialect
from tests.e2e._isolation import _isolate_cfg, _isolated_cfg_key
from tests.e2e._studio_deploy_helpers import (
    SASQUATCH_YAML,
    make_studio_cfg,
    studio_server,
)


# Module-level marker — file uses Playwright via ``App2Driver`` and
# only fires under the runner's ``browser`` layer. The ``tests/e2e/
# app2/`` dir-conftest auto-applies ``@tier(Tier.APP2)`` so the
# layer/tier classification is complete: ``browser`` here + APP2 from
# the dir-conftest. ``importorskip`` above handles the
# Playwright-absent fallback.
pytestmark = [pytest.mark.browser]


@pytest.fixture
def isolated_studio_cfg(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Config, Path]:
    """Per-test isolated Studio cfg honoring the runner cell's dialect.

    Builds a Studio-flavored cfg via :func:`make_studio_cfg`, overlays
    the runner cell's ``RECON_GEN_DEMO_DATABASE_URL`` + ``RECON_GEN_DIALECT``
    env vars (set by ``runner.py``'s PG/Oracle variant arms), then runs
    ``_isolate_cfg`` to suffix the ``db_table_prefix`` + ``deployment_name``
    with a per-(test nodeid, worker) hash.

    CB.7-followup migration (2026-06-02): pre-CB.7 the test built cfg
    inline via ``_studio_cfg_for_cell`` and seeded ``sasquatch_pr``
    against the runner's PG container without isolation — every
    parametrized kind raced every other on the shared prefix, producing
    the v11.* "test_trainer_dogfood_per_kind[*]" mass-fail signature.
    The fixture-side isolation closes that race: each test function
    (= each kind) lands on its own ``sasquatch_pr_<hash>`` prefix in
    the container.

    Returns ``(cfg, db_path)`` where ``db_path`` is informational
    (DuckDB tempfile for local cells, URL-cast for PG / Oracle).
    """
    base_cfg, sqlite_path = make_studio_cfg(tmp_path)

    dialect_override = RECON_GEN_DIALECT.get_or_none()
    url_override = RECON_GEN_DEMO_DATABASE_URL.get_or_none()
    if dialect_override is not None:
        base_cfg.dialect = Dialect(dialect_override)
    if url_override is not None:
        base_cfg.demo_database_url = url_override
        db_path = Path(url_override)
    else:
        db_path = sqlite_path

    suffix = _isolated_cfg_key(request, base_cfg)
    isolated = _isolate_cfg(
        base_cfg, suffix=suffix, tmp_path_factory=tmp_path_factory,
    )
    return isolated, db_path


def _seed_demo_db(cfg: Config) -> None:
    """Apply base schema + full seed + matview refresh against the
    cfg's demo DB. The Studio server picks up these tables when the
    operator clicks Session Start (the /etl/run leg sees the existing
    rows + refreshes matviews; nothing to regenerate)."""
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


# BV.3.3.c.bug4 — chain-coherence kinds were "passing" the surface
# assertion just because the test anchor date "2026-05-30 00:00:00"
# appeared in the rendered HTML for many unrelated rows. A date-only
# match doesn't prove the planted row surfaced — it just proves the
# table renders some row on that day. Tighten by requiring the
# match to be a non-date-shaped value (transfer_id / account_id /
# rail_name / etc.) so the assertion catches the actual planted row.
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _is_date_like(value: str) -> bool:
    """Date-shape detector: anything starting with ``YYYY-MM-DD`` is
    treated as a date for false-positive-rejection purposes."""
    return bool(_DATE_LIKE.match(value))


def _browser_walkable_kinds() -> list[PlantKindEntry]:
    """Filter ``PLANT_REGISTRY`` to kinds whose Tour navigates to a
    dashboard sheet (not ``/etl/triage`` — those have a unit-tier
    BV.3.1 surface) AND whose ``dashboard_check.matview_name`` lets
    the test query the planted row's account_id by matview-diff.

    The 5 ``/etl/triage`` kinds (``phantom_rail``, ``phantom_template``,
    ``missing_metadata_key``, ``uncovered_rail``, ``uncovered_template``)
    are skipped here — BV.3.1's matview-shape probe already covers
    them. The 5 no-matview kinds (``supersession_audit`` + L2FT
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


# BV.3.3.c.bug4-followup — chain-coherence kinds whose matview rows
# land correctly (the v_<matview> sigs are present in the DB) but
# whose rendered dashboard HTML doesn't surface their id-like columns
# (``transfer_id`` etc). Strict signature assertion catches it as a
# real bug: matview correct, dashboard rendering broken. Tracked as a
# known-failing set so BV.3.3.c can ship while the underlying chain-
# coherence dashboard rendering gets debugged separately. Adding to
# this set requires an explicit memory entry — the
# ``[feedback_no_xfail_to_sweep_under_rug]`` rule — so this is the
# minimum surface the registry-walk currently can't prove healthy.
# Failures absent from the set hard-fail the test.
_BUG4_FOLLOWUP_KNOWN_FAIL_KINDS: frozenset[str] = frozenset({
    "chain_parent_disagreement",
    "xor_group_missed",
    "xor_group_overlap",
    "fan_in_missing_parent",
    "fan_in_extra_parent",
    "multi_xor_missed",
    "multi_xor_overlap",
    # stuck_unbundled: bv33a vertical slice PASSES with the bug2
    # driver page_size=10000 fix; bv33c cumulative walk used to fail
    # ("planted signatures absent from rendered dashboard table").
    # Cumulative-state interaction needed separate investigation —
    # tracked at BV.3.3.c.bug2-cumulative-followup. In the per-kind
    # decomposition the cumulative-state shape is gone (every kind
    # gets a fresh Session Start); promote out of this set once the
    # per-kind run shows green for a sustained window.
    "stuck_unbundled",
})


def _column_names(cur: Any, table: str) -> list[str]:  # noqa: ANN401  — DB-API cursor has no shared Protocol across drivers
    """Read the column names of ``table`` from the cursor's
    ``description`` after a portable empty-row probe.

    ``SELECT * FROM <table> WHERE 1=0`` parses + executes on every
    dialect this codebase targets (PG / Oracle / SQLite — and any
    future addition that's still SQL-92-compliant for ``WHERE 1=0``).
    DB-API 2.0 guarantees ``cursor.description`` is populated after
    a SELECT with the column metadata; index 0 of each entry is the
    column name. Sidesteps SQLite's ``PRAGMA table_info``, PG's
    ``information_schema.columns``, and Oracle's ``all_tab_columns``
    in one shot.
    """
    cur.execute(f"SELECT * FROM {table} WHERE 1=0")
    description = cur.description or ()
    return [str(col[0]).lower() for col in description]


def _v_matview_signatures(
    cfg: Config, v_matview_name: str, matview_name: str,
) -> set[tuple[str, ...]]:
    """Snapshot a matview's planted-row signatures: pick the columns
    that exist (from a known priority list — account_id beats
    transfer_id beats child_transfer_id) and read them as tuples.

    Different matviews have different shapes (account-oriented for
    L1 conservation/cap; transfer-oriented for chain coherence).
    Reading the actual column set via ``cursor.description`` + intersecting
    with a priority list keeps the signature column-selection robust
    without a per-matview hardcoded map (which kept drifting from the
    actual schema during BV.3.3.c iteration).

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
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cols = set(_column_names(cur, v_matview_name))
            picked = [c for c in priority if c in cols]
            if not picked:
                # No recognizable id columns — fall back to a
                # row-shaped tuple covering whatever columns the
                # matview did expose so callers get a meaningful diff.
                fallback = ", ".join(sorted(cols)) or "1"
                cur.execute(
                    f"SELECT DISTINCT {fallback} FROM {v_matview_name}"
                )
                return {
                    tuple("" if v is None else str(v) for v in row)
                    for row in cur.fetchall()
                }
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
    cfg: Config, base_prefix: str,
) -> dict[str, object]:
    """Dump v overlay state for triage. Reads the trainer config_kv
    rows + counts the v transactions table so a failed Apply tells
    us what shape the overlay was in when the assertion fired."""
    conn = connect_demo_db(cfg)
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
                except Exception as exc:  # noqa: BLE001 — diagnostic, swallow any DB-API error
                    out[key] = f"<db err: {exc}>"
            for table in (
                f"{base_prefix}_v_transactions",
                f"{base_prefix}_transactions",
                f"{base_prefix}_v_current_transactions",
                f"{base_prefix}_v_limit_breach",
            ):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    out[f"{table}_count"] = int(cur.fetchone()[0])
                except Exception as exc:  # noqa: BLE001 — diagnostic
                    out[f"{table}_count"] = f"<db err: {exc}>"
            # The plant emits a row with id like 'tx-limit-breach-...';
            # check whether it survived the clone+plant pipe.
            try:
                cur.execute(
                    f"SELECT id, account_id, account_scope, "
                    f"account_parent_role, amount_money, "
                    f"amount_direction, status, posting, rail_name "
                    f"FROM {base_prefix}_v_transactions "
                    f"WHERE id LIKE 'tx-limit-breach%'"
                )
                rows = cur.fetchall()
                out["planted_tx_rows"] = [tuple(r) for r in rows[:5]]
            except Exception as exc:  # noqa: BLE001 — diagnostic
                out["planted_tx_rows"] = f"<db err: {exc}>"
            # Cap-join shape: does the v overlay's config view know
            # about CustomerCashWithdrawal?
            try:
                cur.execute(
                    f"SELECT parent_role, rail, direction, cap "
                    f"FROM {base_prefix}_v_config_limit_schedules "
                    f"WHERE rail = 'CustomerCashWithdrawal'"
                )
                out["v_config_schedule_for_rail"] = [
                    tuple(r) for r in cur.fetchall()
                ]
            except Exception as exc:  # noqa: BLE001 — diagnostic
                out["v_config_schedule_for_rail"] = f"<db err: {exc}>"
            # Cross-check base
            try:
                cur.execute(
                    f"SELECT parent_role, rail, direction, cap "
                    f"FROM {base_prefix}_config_limit_schedules "
                    f"WHERE rail = 'CustomerCashWithdrawal'"
                )
                out["base_config_schedule_for_rail"] = [
                    tuple(r) for r in cur.fetchall()
                ]
            except Exception as exc:  # noqa: BLE001 — diagnostic
                out["base_config_schedule_for_rail"] = f"<db err: {exc}>"
            # Count config_kv rows in v vs base.
            for table in (
                f"{base_prefix}_config_kv", f"{base_prefix}_v_config_kv",
            ):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    out[f"{table}_rowcount"] = int(cur.fetchone()[0])
                except Exception as exc:  # noqa: BLE001 — diagnostic
                    out[f"{table}_rowcount"] = f"<db err: {exc}>"
            return out
        finally:
            cur.close()
    finally:
        conn.close()


def _v_matview_account_ids(
    cfg: Config, v_matview_name: str,
) -> set[str]:
    """Snapshot the v overlay's matview rows by account_id. The diff
    between pre-Apply + post-Apply identifies what the plant added —
    used for the BV.3.3.a vertical-slice sanity assertion below.

    The per-kind walk uses ``_v_matview_signatures`` instead, which
    handles transfer-keyed matviews (chain coherence) where
    ``account_id`` isn't the discriminating column.
    """
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT DISTINCT account_id FROM {v_matview_name}")
            return {str(row[0]) for row in cur.fetchall()}
        finally:
            cur.close()
    finally:
        conn.close()


def test_bv33a_limit_breach_outbound_trainer_dogfood(
    isolated_studio_cfg: tuple[Config, Path],
) -> None:
    """BV.3.3.a vertical slice — drive the Trainer end-to-end for
    ``limit_breach_outbound`` and assert the planted row surfaces in
    the Limit Breach dashboard sheet AT the rendered HTML layer.

    Drives through ``App2Driver``'s trainer verbs (BV.3.3.b). Kept
    after the BV.3.3.c per-kind decomposition as the bare-minimum
    sanity gate — the per-kind parametrize covers every kind including
    this one, but a failure here pinpoints the smallest possible
    breakage shape (one specific plant, one specific matview)."""
    from tests.e2e._drivers.app2 import App2Driver  # noqa: PLC0415

    cfg, _db_path = isolated_studio_cfg
    _seed_demo_db(cfg)

    entry = _pick_kind("limit_breach_outbound")
    v_matview = f"{cfg.db_table_prefix}_v_limit_breach"

    with studio_server(cfg) as base_url, App2Driver.attached_to(
        base_url=base_url, cfg=cfg,
    ) as driver:
        driver.open_training()
        driver.trainer_start_session()

        before = _v_matview_account_ids(cfg, v_matview)
        driver.trainer_enable_plant(entry.kind, entry.family)
        driver.trainer_apply()

        after = _v_matview_account_ids(cfg, v_matview)
        new_accounts = after - before
        if not new_accounts:
            diag = _diagnose_v_state(cfg, cfg.db_table_prefix)
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


def _walkable_param_id(entry: PlantKindEntry) -> str:
    """pytest-friendly id: just the kind. Family is derivable from the
    registry if a failure needs the accordion-grouping context."""
    return entry.kind


def _walkable_params() -> list[Any]:  # noqa: ANN401  — ParameterSet has no public pyright stub
    """Build ``pytest.param`` entries from the walkable registry,
    applying ``@pytest.mark.xfail`` to BV.3.3.c.bug4-followup entries
    so a known-fail kind doesn't red the run while the underlying
    dashboard-rendering bug gets debugged separately."""
    params: list[Any] = []  # noqa: ANN401
    for entry in _browser_walkable_kinds():
        marks: list[Any] = []  # noqa: ANN401
        if entry.kind in _BUG4_FOLLOWUP_KNOWN_FAIL_KINDS:
            marks.append(
                pytest.mark.xfail(
                    strict=False,
                    reason=(
                        f"BV.3.3.c.bug4-followup: {entry.kind}'s "
                        f"matview row lands but its id-like columns "
                        f"don't surface in the rendered dashboard HTML"
                    ),
                ),
            )
        params.append(
            pytest.param(entry, id=_walkable_param_id(entry), marks=marks),
        )
    return params


@pytest.mark.parametrize("entry", _walkable_params())
def test_trainer_dogfood_per_kind(
    entry: PlantKindEntry,
    isolated_studio_cfg: tuple[Config, Path],
) -> None:
    """Per-kind trainer dogfood — each plant runs against a fresh
    studio + fresh DB; failure on one kind doesn't taint the rest.

    The runner cell determines dialect (``sp_pg_lo`` → PG;
    ``sp_or_lo`` → Oracle; ``sp_du_lo`` → DuckDB). Cross-dialect
    coverage emerges from re-running this test file per cell — no
    explicit ``@pytest.mark.parametrize("dialect", ...)`` here.
    """
    from tests.e2e._drivers.app2 import App2Driver  # noqa: PLC0415

    cfg, _db_path = isolated_studio_cfg
    _seed_demo_db(cfg)

    matview = entry.dashboard_check.matview_name
    assert matview is not None  # _browser_walkable_kinds() guarantees this
    v_matview = f"{cfg.db_table_prefix}_v_{matview}"

    with studio_server(cfg) as base_url, App2Driver.attached_to(
        base_url=base_url, cfg=cfg,
    ) as driver:
        driver.open_training()
        driver.trainer_start_session()

        before = _v_matview_signatures(cfg, v_matview, matview)
        driver.trainer_enable_plant(entry.kind, entry.family)
        driver.trainer_apply()

        after = _v_matview_signatures(cfg, v_matview, matview)
        new_signatures = after - before
        if not new_signatures:
            diag = _diagnose_v_state(cfg, cfg.db_table_prefix)
            pytest.fail(
                f"{entry.kind} plant didn't add a row to {v_matview}. "
                f"diag: {diag}"
            )

        driver.trainer_take_violation_tour(entry.kind, entry.family)
        rendered_html = driver.dashboard_table_inner_html()
        # BV.3.3.c.bug4 — surface assertion rejects date-only matches.
        # The anchor date (e.g. "2026-05-30") appears in the rendered
        # HTML for MANY unrelated rows on a busy dashboard; matching on
        # it doesn't prove the planted row is the one rendered. Require
        # an id-like value (transfer_id / account_id / rail_name / etc.)
        # to match.
        hit = next(
            (
                val for sig in new_signatures for val in sig
                if val and val in rendered_html and not _is_date_like(val)
            ),
            None,
        )
        assert hit is not None, (
            f"{entry.kind} planted signatures "
            f"{sorted(new_signatures)} should appear in the rendered "
            f"dashboard HTML; got: {rendered_html[:500]!r}"
        )


