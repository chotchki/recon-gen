"""BV.4.9 — DL.9 diff-only Apply.

Pure-function tests on `compute_apply_diff` (the core decision
between fast-path and slow-path) + end-to-end Apply tests against
a real sqlite v overlay proving the fast path skips the clone while
the slow path rebuilds correctly.
"""

from __future__ import annotations

import asyncio
import duckdb
from collections.abc import Iterator
from pathlib import Path

import pytest

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.l2.seed import emit_full_seed
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.v_overlay import (
    ApplyDiff,
    apply_plants,
    clone_base_to_v_sql,
    compute_apply_diff,
    create_v_overlay_sql,
    read_applied_state,
    refresh_v_overlay_matviews_sql,
)
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.plant_registry import PLANT_REGISTRY
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


# -- Pure compute_apply_diff -----------------------------------------------


def test_diff_empty_current_empty_new_is_no_op() -> None:
    diff = compute_apply_diff({}, {})
    assert diff == ApplyDiff(
        unchanged=frozenset(),
        to_add=frozenset(),
        to_remove=frozenset(),
    )


def test_diff_pure_additive_skips_reclone() -> None:
    """Operator checks a new plant on top of existing applied state —
    no kind needs removal so the fast path fires."""
    current = {"phantom_rail": {"count": "5"}}
    new = {
        "phantom_rail": {"count": "5"},
        "missing_metadata": {"count": "3"},
    }
    diff = compute_apply_diff(current, new)
    assert diff.unchanged == frozenset({"phantom_rail"})
    assert diff.to_add == frozenset({"missing_metadata"})
    assert diff.to_remove == frozenset()


def test_diff_uncheck_triggers_reclone() -> None:
    """Operator unchecks a plant — to_remove non-empty forces the
    slow path because DELETE-style plants can't be undone surgically."""
    current = {"phantom_rail": {"count": "5"}, "missing_metadata": {"count": "3"}}
    new = {"phantom_rail": {"count": "5"}}
    diff = compute_apply_diff(current, new)
    assert diff.unchanged == frozenset({"phantom_rail"})
    assert diff.to_add == frozenset()
    assert diff.to_remove == frozenset({"missing_metadata"})


def test_diff_changed_form_values_treated_as_remove_plus_add() -> None:
    """If a kind stays checked but the operator changed its form
    values (e.g. count 5 → 10), it lands in BOTH to_remove and to_add
    so the slow path fires and the new fingerprint replaces the old."""
    current = {"phantom_rail": {"count": "5"}}
    new = {"phantom_rail": {"count": "10"}}
    diff = compute_apply_diff(current, new)
    assert diff.unchanged == frozenset()
    assert diff.to_add == frozenset({"phantom_rail"})
    assert diff.to_remove == frozenset({"phantom_rail"})


def test_diff_first_apply_against_empty_current_is_additive() -> None:
    """First Apply post-Session-Start: current is empty (Session
    Start wipes the ledger), so every enabled kind is to_add and
    nothing is to_remove → fast path."""
    current: dict[str, dict[str, str]] = {}
    new = {"phantom_rail": {"count": "5"}, "missing_metadata": {"count": "3"}}
    diff = compute_apply_diff(current, new)
    assert diff.unchanged == frozenset()
    assert diff.to_add == frozenset({"phantom_rail", "missing_metadata"})
    assert diff.to_remove == frozenset()


# -- End-to-end Apply against a real sqlite v overlay ----------------------


def _phantom_rail_entry():
    return next(
        e for e in PLANT_REGISTRY if e.kind == "phantom_rail"
    )


@pytest.fixture
def fresh_v_overlay(tmp_path: Path) -> Iterator[tuple[Config, Path]]:
    """Spin up a real sqlite-backed v overlay against the spec_example
    L2 fixture so apply_plants's clone + plant + matview-refresh
    pipeline exercises the actual paths.

    Uses the schema-emit + seed-emit primitives directly rather than
    `session_start`, which would invoke the full deploy pipeline
    (designed for an already-bootstrapped base prefix)."""
    db_path = tmp_path / "bv49.sqlite"
    cfg = make_test_config(
        dialect=Dialect.DUCKDB,
        demo_database_url=str(db_path),
        db_table_prefix="bv49",
    )

    fixtures_root = Path(__file__).resolve().parent.parent / "l2"
    yaml_path = fixtures_root / "spec_example.yaml"
    cache = L2InstanceCache.from_path(yaml_path)
    instance = cache.get()

    base_prefix = cfg.db_table_prefix
    scenarios = default_scenario_for(instance).scenario
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(
                cur, emit_schema(instance, prefix=base_prefix, dialect=cfg.dialect),
                dialect=cfg.dialect,
            )
            execute_script(
                cur,
                emit_full_seed(
                    instance, scenarios,
                    prefix=base_prefix, dialect=cfg.dialect,
                ),
                dialect=cfg.dialect,
            )
            execute_script(
                cur,
                create_v_overlay_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                ),
                dialect=cfg.dialect,
            )
            execute_script(
                cur, clone_base_to_v_sql(base_prefix), dialect=cfg.dialect,
            )
            execute_script(
                cur,
                refresh_v_overlay_matviews_sql(
                    instance, base_prefix=base_prefix, dialect=cfg.dialect,
                ),
                dialect=cfg.dialect,
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()

    yield cfg, yaml_path


def test_apply_first_run_fast_path_plants_phantom_rail(
    fresh_v_overlay: tuple[Config, Path],
) -> None:
    """First Apply post-Session-Start should take the fast path
    (no kinds to remove) and successfully plant phantom_rail."""
    cfg, yaml_path = fresh_v_overlay
    cache = L2InstanceCache.from_path(yaml_path)
    instance = cache.get()
    entry = _phantom_rail_entry()

    asyncio.run(apply_plants(
        cfg, instance,
        enabled_plants=[(entry, {"count": 3, "rail_name": "Phantom Rail"})],
    ))

    applied = asyncio.run(read_applied_state(cfg))
    assert "phantom_rail" in applied
    assert applied["phantom_rail"]["count"] == "3"


def test_apply_repeated_unchanged_does_not_re_run_plant(
    fresh_v_overlay: tuple[Config, Path],
) -> None:
    """DL.9 core promise: re-applying the same plant with the same
    form values should NOT re-run the plant_function. Proof: count
    the v-overlay `__demo_phantom_rail` rows after the first Apply,
    then again after a second Apply with identical inputs — they
    must match exactly (a naive replay would double-plant)."""
    cfg, yaml_path = fresh_v_overlay
    cache = L2InstanceCache.from_path(yaml_path)
    instance = cache.get()
    entry = _phantom_rail_entry()
    plant = (entry, {"count": 4, "rail_name": "Phantom Rail"})

    asyncio.run(apply_plants(cfg, instance, enabled_plants=[plant]))
    first_count = _count_phantom_rows(cfg)

    asyncio.run(apply_plants(cfg, instance, enabled_plants=[plant]))
    second_count = _count_phantom_rows(cfg)

    assert first_count == second_count, (
        f"DL.9 fast path re-ran the plant — phantom row count went "
        f"{first_count} → {second_count} when it should have stayed "
        f"constant for an unchanged-fingerprint re-Apply."
    )
    assert first_count > 0, "first Apply didn't plant anything"


def test_apply_uncheck_triggers_reclone_removing_plant_rows(
    fresh_v_overlay: tuple[Config, Path],
) -> None:
    """Apply with phantom_rail enabled, then re-Apply with it
    unchecked. The slow path fires (to_remove non-empty) and reclone
    wipes the planted rows."""
    cfg, yaml_path = fresh_v_overlay
    cache = L2InstanceCache.from_path(yaml_path)
    instance = cache.get()
    entry = _phantom_rail_entry()

    asyncio.run(apply_plants(
        cfg, instance,
        enabled_plants=[(entry, {"count": 2, "rail_name": "Phantom Rail"})],
    ))
    assert _count_phantom_rows(cfg) > 0

    asyncio.run(apply_plants(cfg, instance, enabled_plants=[]))
    assert _count_phantom_rows(cfg) == 0, (
        "uncheck → reclone should have wiped phantom_rail planted rows"
    )

    applied = asyncio.run(read_applied_state(cfg))
    assert "phantom_rail" not in applied


def _count_phantom_rows(cfg: Config) -> int:
    """Count v-overlay transactions whose id marker indicates a
    phantom_rail plant. Used to verify whether the plant was
    re-applied or skipped."""
    conn: duckdb.DuckDBPyConnection = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) FROM bv49_v_transactions "
                "WHERE id LIKE '__demo_gap_phantom_rail%'"
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            cur.close()
    finally:
        conn.close()
