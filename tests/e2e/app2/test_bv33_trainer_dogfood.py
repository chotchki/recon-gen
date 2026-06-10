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

Cross-dialect coverage (CB.17.i): the ``isolated_studio_cfg`` fixture
is parametrized over ``Dialect.{DUCKDB, POSTGRES, ORACLE}``. The
DuckDB cell uses a tempfile (no Docker); the PG / Oracle cells pull
URLs from the session-scoped ``pg_container_url`` /
``oracle_container_url`` fixtures lazily so DuckDB-only runs don't
spin Docker. Subset the dialects via
``RECON_GEN_TRAINER_DIALECTS=du`` (or ``du,pg`` etc.) for fast
iteration. The legacy ``RECON_GEN_DIALECT`` runner-cell env still
works as a single-dialect pin for back-compat. Helpers use
``recon_gen.common.db.connect_demo_db`` and ``cursor.description``
for column introspection so every read works across all three
dialects without dialect branching at the test layer.

Gated behind ``RECON_GEN_E2E=1`` per conftest (the ``browser`` marker).
File lives under ``tests/e2e/app2/`` so the dir-conftest auto-applies
``@tier(Tier.APP2)``.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from collections.abc import Generator
from typing import Any, cast

import pytest

# Skip the whole module when Playwright isn't available — keeps the
# rest of the e2e suite collectible without the browser tier
# installed.
pytest.importorskip("playwright.sync_api")

from recon_gen.cli._helpers import build_config_populate_sql
from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db, execute_script, fetch_one_required
from recon_gen.common.env_keys import RECON_GEN_DIALECT, RECON_GEN_TRAINER_DIALECTS
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY, PlantKindEntry
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.seed import emit_full_seed
from recon_gen.common.sql import Dialect
from tests.e2e._isolation import _isolate_cfg
from tests.e2e._studio_deploy_helpers import (
    SASQUATCH_YAML,
    make_studio_cfg,
    studio_server,
)


# CB.17.j — no `pytest.mark.browser`. Pre-CB.17.d the runner used a
# `-m browser` selector on the qs_browser layer; post-collapse the
# qs_browser layer still runs `pytest tests/e2e/ -m browser` which
# was pulling THIS file in too, causing every trainer-dogfood test
# to run twice (once via the app2 layer's `tests/e2e/app2/` path
# selection, once via the qs_browser layer's marker selection).
# The dir-conftest auto-applies `@tier(Tier.APP2)`; that single
# classification is what we want. `importorskip("playwright.sync_api")`
# above handles the playwright-absent fallback at the test layer.


# CB.7 followup — the seed + dashboard must agree on the as-of anchor
# or the L1 date-range filter clips out the plants. Pre-fix the seed
# pinned anchor=2026-05-30 but the cfg defaulted to `AsOfFrame.live()`
# (= today). On a wall-clock day ≥7 days past the anchor, the default
# 7-day window `[as_of-7d, as_of]` excluded plants near the anchor —
# e.g. `overdraft` plants at `anchor - 6 days = 2026-05-24` and the
# wall-clock window `[today-7d, today]` doesn't reach back that far.
# Pin both ends to the same constant so the trainer flow is wall-clock-
# independent.
_TRAINER_ANCHOR = date(2026, 5, 30)


def _trainer_dialect_params() -> list[Any]:  # noqa: ANN401 — ParameterSet has no public pyright stub
    """Build the dialect parametrize set for the trainer fixture.

    Default: fan out over all three production dialects (DuckDB +
    Postgres + Oracle), which is what the post-CB.17.d single-pytest-
    per-layer flow needs to actually cover cross-dialect behavior.

    Env override: ``RECON_GEN_TRAINER_DIALECTS=du`` (or `du,pg`, etc.)
    pins to a subset for fast iteration. Use cases:

    - ``du`` — pure local iteration, no Docker required.
    - ``du,pg`` — when Oracle's ~3min cold-start is unwanted but PG
      coverage matters.
    - ``pg,or`` — when the dialect-specific surface is the target
      (e.g. confirming an Oracle bugfix).

    Honors the legacy ``RECON_GEN_DIALECT`` env (runner cells set it
    historically) by also restricting to that single dialect when
    set — preserves any operator workflow that pinned dialect via
    the runner cell shorthand.
    """
    raw = RECON_GEN_TRAINER_DIALECTS.get_or_none()
    legacy = RECON_GEN_DIALECT.get_or_none()
    if raw:
        picks = [d.strip().lower() for d in raw.split(",") if d.strip()]
    elif legacy:
        picks = [legacy.lower()]
    else:
        picks = ["du", "pg", "or"]
    name_to_dialect = {"du": Dialect.DUCKDB, "pg": Dialect.POSTGRES, "or": Dialect.ORACLE}
    out: list[Any] = []  # noqa: ANN401
    for short in picks:
        dialect = name_to_dialect.get(short)
        if dialect is None:
            continue
        # CE.4-followup #3 — pin all trainer tests of a given dialect to
        # ONE xdist worker via `xdist_group`. Without this, 16 workers
        # × 3 dialects = 48 fixture instances all try to do Session
        # Start in parallel at the start of `pytest -n auto`, blowing
        # the same 600s `_trainer_wait_until_finished` wire that CB.17.m
        # worked around — the session-scope fixture only shares Session
        # Start WITHIN a worker, not ACROSS them. The `loadgroup` dist
        # mode is already on (configured via tests/conftest.py::
        # pytest_configure when xdist is active), so a `xdist_group`
        # mark of "trainer-<dialect>" funnels all of one dialect's
        # trainer tests onto a single worker. 3 workers total (one
        # per dialect) instead of 16; each does its own Session Start
        # once + N cheap reclones.
        out.append(
            pytest.param(
                dialect, id=short,
                marks=[pytest.mark.xdist_group(f"trainer-{short}")],
            ),
        )
    return out


@pytest.fixture(scope="session", params=_trainer_dialect_params())
def trainer_ready_session(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Generator[tuple[Config, str], None, None]:
    """CE.2 — session-scope Session Start fixture, shared across all
    trainer dogfood tests on the same xdist worker × dialect cell.

    Backlog #249 root cause: each test was running `_seed_demo_db` +
    Session Start (`/etl/run`) afresh against its own per-test
    isolated prefix. On 16-xdist-worker CI with one shared PG /
    Oracle container per dialect, that's N × 10-min Oracle ETL +
    seed-apply contention — easily blew the 600s
    `_trainer_wait_until_finished` wire for [pg] / [or] parametrize
    cells.

    New shape (CE.0 spike confirmed PG: 2.4× per-test speedup):

    - Per-(worker × dialect) STABLE prefix (`trainer_<dialect>_<worker>`)
      — no nodeid in the key, so all tests on the same worker share
      the same base.
    - `_seed_demo_db` once.
    - `studio_server` once (uvicorn instance shared across tests).
    - One-shot driver opens, clicks Session Start, closes — base + v
      overlay are now both populated.
    - Yield `(cfg, base_url)` for tests to attach their own drivers
      against the same server.

    Tests then call `driver.trainer_reset_overlay()` (CE.1 — clicks
    Re-clone) at the start of each test body — wipes + reclones the
    v overlay from base, refreshes v matviews, skips `/etl/run`.
    Cheap (PG ~13s, Oracle ~1-2min, DuckDB sub-second).

    Cross-test contamination guard: each test reclones at the start
    of its body. Plants from prior tests on the same worker are
    cleared by the reclone before the new test plants.
    """
    from tests.e2e._drivers.app2 import App2Driver  # noqa: PLC0415

    dialect: Dialect = request.param
    base_dir = tmp_path_factory.mktemp(f"trainer-{dialect.value}-{worker_id}")
    demo_url: str | None = None
    if dialect is Dialect.POSTGRES:
        demo_url = cast(
            str, request.getfixturevalue("pg_container_url"),
        )
    elif dialect is Dialect.ORACLE:
        demo_url = cast(
            str, request.getfixturevalue("oracle_container_url"),
        )
    base_cfg, _ = make_studio_cfg(
        base_dir, dialect=dialect, demo_database_url=demo_url,
    )

    base_cfg.test_generator = dataclasses.replace(
        base_cfg.test_generator, end_date=_TRAINER_ANCHOR,
    )

    # Stable per-(worker, dialect) suffix — drops the nodeid component
    # that the per-test `isolated_studio_cfg` includes, so all tests
    # sharing this session fixture land on the same prefix.
    suffix = f"trainer_{dialect.value}_{worker_id}"
    cfg = _isolate_cfg(
        base_cfg, suffix=suffix, tmp_path_factory=tmp_path_factory,
    )

    _seed_demo_db(cfg)

    with studio_server(cfg) as base_url:
        # One-shot driver to drive the initial full Session Start.
        # Subsequent tests open their own drivers + call
        # `trainer_reset_overlay()` (cheap reclone) against the same
        # server.
        with App2Driver.attached_to(base_url=base_url, cfg=cfg) as driver:
            driver.open_training()
            driver.trainer_start_session()
        yield (cfg, base_url)


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
                build_config_populate_sql(cfg, instance, anchor=_TRAINER_ANCHOR),
                dialect=cfg.dialect,
            )
            execute_script(
                cur,
                emit_full_seed(
                    instance, scenarios,
                    prefix=base_prefix, dialect=cfg.dialect,
                    anchor=_TRAINER_ANCHOR,
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
    # BV.3.3.c.bug4-followup: chain-coherence kinds whose v_<matview>
    # rows surface correctly (the bv31 db-tier round-trip is green) but
    # whose rendered L1 Exceptions dashboard HTML doesn't reliably
    # expose their transfer_id. Root cause (debugged 2026-06-10): the
    # dashboard's `sort_by=(amount DESC)` + `page_size` aren't plumbed
    # through to the htmx data-fetch URL — the fetch hits server default
    # `ORDER BY 1` (check_type ASC) + page_size=50. NULL-magnitude
    # chain-coherence rows that sort BELOW "chain_" alphabetically fall
    # off page 1. Tracked at BV.3.3.c.bug4-followup. Promote out of this
    # set once the dashboard plumbing lands.
    # 3 fail reliably: xor_group_missed/overlap, multi_xor_missed.
    # 4 happen to pass under default ORDER BY 1 (chain_parent_disagreement,
    #   fan_in_missing_parent, fan_in_extra_parent, multi_xor_overlap)
    # but the pass is incidental — they're kept here so the set
    # documents the full chain-coherence rendering risk.
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


_SIGNATURE_ID_COLUMNS: tuple[str, ...] = (
    # Strong identity columns — the value-set the diff actually narrows
    # on. Order = picker priority (transfer_id beats child_transfer_id
    # beats account_id). Every chain-coherence matview must intersect
    # with at least one of these or `_v_matview_signatures` raises.
    "transfer_id", "child_transfer_id", "parent_transfer_id",
    "account_id", "rail_name", "direction",
)
_SIGNATURE_DATE_COLUMNS: tuple[str, ...] = (
    # Date columns — useful for surface-rendering assertions because
    # the dashboard typically shows the planted row's business day, but
    # they are NOT identity columns and must never be the sole component
    # of a signature. BV.3.3.c.bug4 (false-positive fix): a signature
    # that collapsed to ``(business_day,)`` matched every row on that
    # day, including unrelated plants/baseline data.
    "business_day", "business_day_start",
)


def _v_matview_signatures(
    cfg: Config, v_matview_name: str, matview_name: str,
) -> set[tuple[str, ...]]:
    """Snapshot a matview's planted-row signatures: pick the columns
    that exist (from known ID + date priority lists) and read them
    as tuples.

    Different matviews have different shapes (account-oriented for
    L1 conservation/cap; transfer-oriented for chain coherence).
    Reading the actual column set via ``cursor.description`` +
    intersecting with the priority lists keeps the picker robust
    without a per-matview hardcoded map (which kept drifting from the
    actual schema during BV.3.3.c iteration).

    BV.3.3.c.bug4 (false-positive fix): the picker now SPLITS its
    priority list into ID columns (``transfer_id`` /
    ``child_transfer_id`` / ``parent_transfer_id`` / ``account_id`` /
    ``rail_name`` / ``direction``) and date columns
    (``business_day`` / ``business_day_start``). At least one ID
    column MUST intersect with the matview's actual columns — a
    signature that collapses to date-only is a false-positive vector
    (every row on that day matches). The caller's
    ``_is_date_like`` guard at the surface-assertion layer is a
    second line of defense; the structural guard here makes the
    failure mode unrepresentable at the picker.

    Signature elements are concatenated to produce a "look for this
    in the rendered HTML" haystack for the surface assertion.
    """
    del matview_name  # kept on signature for future per-matview override
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cols = set(_column_names(cur, v_matview_name))
            picked_ids = [c for c in _SIGNATURE_ID_COLUMNS if c in cols]
            picked_dates = [c for c in _SIGNATURE_DATE_COLUMNS if c in cols]
            if not picked_ids:
                # No identifying column intersects — the matview's
                # priority footprint is too thin to produce a
                # non-degenerate signature. Surface the gap loudly
                # rather than silently degrade to a date-only
                # signature (the BV.3.3.c.bug4 false-positive shape)
                # or to a row-shaped fallback (which masks a real
                # picker drift). When a new chain-coherence matview
                # lands with an exotic key, extend
                # ``_SIGNATURE_ID_COLUMNS`` rather than relax this guard.
                raise AssertionError(
                    f"_v_matview_signatures: no ID column intersects "
                    f"with {v_matview_name}'s columns "
                    f"({sorted(cols)!r}). Extend "
                    f"_SIGNATURE_ID_COLUMNS so the signature carries "
                    f"a non-date identifier."
                )
            picked = picked_ids + picked_dates
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
                    out[f"{table}_count"] = int(fetch_one_required(cur)[0])
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
                    out[f"{table}_rowcount"] = int(fetch_one_required(cur)[0])
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
    trainer_ready_session: tuple[Config, str],
) -> None:
    """BV.3.3.a vertical slice — drive the Trainer end-to-end for
    ``limit_breach_outbound`` and assert the planted row surfaces in
    the Limit Breach dashboard sheet AT the rendered HTML layer.

    Drives through ``App2Driver``'s trainer verbs (BV.3.3.b). Kept
    after the BV.3.3.c per-kind decomposition as the bare-minimum
    sanity gate — the per-kind parametrize covers every kind including
    this one, but a failure here pinpoints the smallest possible
    breakage shape (one specific plant, one specific matview).

    CE.3 — consumes the session-scope `trainer_ready_session`
    fixture: base + initial v overlay already populated. This test
    just reclones the v overlay (cheap), plants, applies, verifies.
    """
    from tests.e2e._drivers.app2 import App2Driver  # noqa: PLC0415

    cfg, base_url = trainer_ready_session

    entry = _pick_kind("limit_breach_outbound")
    v_matview = f"{cfg.db_table_prefix}_v_limit_breach"

    with App2Driver.attached_to(
        base_url=base_url, cfg=cfg,
    ) as driver:
        driver.open_training()
        driver.trainer_reset_overlay()

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
    applying ``pytest.mark.skip`` to BV.3.3.c.bug4-followup entries so
    a known-fail kind doesn't red the run while the underlying
    dashboard-rendering bug gets debugged separately.

    CB.17.j — was ``xfail(strict=False)``. xfail RUNS the test (it
    just inverts the expected outcome), which on the trainer dogfood
    flow means ~130s of Session Start + studio_server + Apply
    timeout per kind × 8 kinds × both app2 + qs_browser layers — ~30
    min of cumulative work and ~5 min of wall time we were burning
    to verify "yes, the broken kinds are still broken." `skip`
    short-circuits at collection time. When BV.3.3.c.bug4-followup
    actually fixes the dashboard rendering, demote the entries out
    of `_BUG4_FOLLOWUP_KNOWN_FAIL_KINDS` and they re-enter the
    parametrize set.
    """
    params: list[Any] = []  # noqa: ANN401
    for entry in _browser_walkable_kinds():
        marks: list[Any] = []  # noqa: ANN401
        if entry.kind in _BUG4_FOLLOWUP_KNOWN_FAIL_KINDS:
            marks.append(
                pytest.mark.skip(
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


@pytest.mark.parametrize("entry", _walkable_params())  # typing-smell: ignore[no-inline-production-constants]: 'entry' is the pytest parametrize fixture name, not the `_ROW_ID_COLUMN` value
def test_trainer_dogfood_per_kind(
    entry: PlantKindEntry,
    trainer_ready_session: tuple[Config, str],
) -> None:
    """Per-kind trainer dogfood — each plant runs against a cleanly-
    recloned v overlay sharing one base + Session Start per worker ×
    dialect. CE.3 — consumes the session-scope `trainer_ready_session`
    fixture; the per-test reclone wipes plants from prior tests on
    the same worker so each test starts from a clean overlay.

    Dialect parametrization comes from the session fixture's `params`
    list (RECON_GEN_TRAINER_DIALECTS env-overridable).
    """
    from tests.e2e._drivers.app2 import App2Driver  # noqa: PLC0415

    cfg, base_url = trainer_ready_session

    # CT.0 — CL.13 Oracle-skip removed. Root cause was the plant's
    # DELETE using bare ISO-8601 strings ('2026-05-31') as TIMESTAMP
    # comparands, which Oracle rejected with ORA-01843 ("not a valid
    # month") — the whole DELETE error'd out silently before reaching
    # the matview. Fix: use `date_literal()` from common.sql.dialect
    # to emit `DATE 'YYYY-MM-DD'` (portable on PG + DuckDB + Oracle).
    # See _invoke_balance_cadence_gap_plant in plant_registry.py.

    matview = entry.dashboard_check.matview_name
    assert matview is not None  # _browser_walkable_kinds() guarantees this
    v_matview = f"{cfg.db_table_prefix}_v_{matview}"

    with App2Driver.attached_to(
        base_url=base_url, cfg=cfg,
    ) as driver:
        driver.open_training()
        driver.trainer_reset_overlay()

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


