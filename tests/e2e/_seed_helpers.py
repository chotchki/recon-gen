"""DB-side seeding helpers for e2e tests.

Lifted from ``tests/e2e/_harness_seed.py`` (Y.2.gate.f.9) so the
non-harness e2e tests that still need a per-test schema+seed+refresh
flow have it at a stable location after the layer-8 harness drop.

Currently the only consumer is ``test_audit_dashboard_agreement.py``.
``build_planted_manifest`` did NOT lift — it was harness-specific
(walked plants for the harness's per-test triage manifest).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from recon_gen.common.db import execute_script
from recon_gen.common.intervals import DateInterval
from recon_gen.common.l2 import (
    L2Instance,
    emit_schema,
    refresh_matviews_sql,
)
from recon_gen.common.l2.auto_scenario import (
    ScenarioMode,
    add_broken_rail_plants,
    boost_inv_fanout_plants,
    default_scenario_for,
    densify_scenario,
)
from recon_gen.common.l2.seed import (
    ScenarioPlant,
    emit_full_seed,
)
from recon_gen.common.sql import Dialect


# Pinned reference date for hash-locked seed determinism (M.2a.8).
DEFAULT_SEED_TODAY = date(2030, 1, 1)


def apply_db_seed(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union (psycopg/oracledb/sqlite3) has no shared Protocol
    instance: L2Instance,
    *,
    prefix: str,
    mode: ScenarioMode = "l1_plus_broad",
    today: date | None = None,
    plant_window: DateInterval | None = None,
    dialect: Dialect = Dialect.POSTGRES,
    include_baseline: bool = False,
) -> ScenarioPlant:
    """Apply schema + seed + matview refresh against ``conn``.

    Three DB-side steps in order, each committed independently so a
    mid-flow failure leaves the prefixed objects in a known state for
    the test teardown to drop cleanly. Routes every multi-statement
    script through ``common/db.execute_script`` so Oracle's per-
    statement + PL/SQL-block execution works alongside Postgres's
    atomic-script behavior (P.9d).

    Returns the ``ScenarioPlant`` so the caller can inspect what was
    planted.

    Args:
      prefix (Z.C): the table-prefix the schema/seed/refresh SQL keys
        off (pass ``cfg.db_table_prefix``). The L2 yaml no longer
        carries an ``instance:`` field, so the prefix is a required
        kwarg.
      today (legacy, BC.4 superseded by `plant_window`): the reference
        date the scenario builder uses to compute plant `days_ago`
        offsets. Kept for backward-compat with callers that don't yet
        thread an audit window through (e.g. `test_pdf_matches_scenario`
        / `test_inv_dashboard_agreement`); when both `today` AND
        `plant_window` are supplied, `plant_window.end` wins for
        spine-generator anchoring (the day plants land on) while
        `today` continues to drive the scenario's `days_ago` math.
      plant_window (BC.4c): when supplied, threaded into
        `scenario_to_generators(plant_window=...)` so the L1-invariant
        spine generators anchor each plant on
        `SingleDayPlant.at_window_end(plant_window).day` — the
        most-recently-closed auditable day. Fixes the chronic v11.10.0
        off-by-one (plant landed on `today`, audit window was
        `[today-7, today-1]`).
      include_baseline (R.5.b): when True, the seed step uses
        ``emit_full_seed`` + the densify+broken+boost pipeline that
        the CLI's ``demo apply`` uses. Adds ~60k baseline rows on top
        of the lean planted scenarios. Default False — fast feedback,
        plants only.
    """
    today_ref = today or DEFAULT_SEED_TODAY

    # 1. Schema.
    schema_sql = emit_schema(instance, prefix=prefix, dialect=dialect)
    with conn.cursor() as cur:
        execute_script(cur, schema_sql, dialect=dialect)
    conn.commit()

    # 1b. Populate <prefix>_config. The plants-only path bypasses
    # `build_full_seed_sql` (it composes through the spine pipeline),
    # so we mirror the production schema-apply populate inline using
    # the same `build_config_populate_sql` helper. Production has the
    # populate in `schema apply` (BC.7 / BC.12); this fixture keeps it
    # here because the schema-apply equivalent in `apply_db_seed`
    # (`emit_schema` above) doesn't go through the Click CLI layer that
    # wires the populate.
    #
    # BC.8 retired the Duration→seconds walk hack — `serialize_l2` now
    # emits `max_pending_age_seconds` / `max_unbundled_age_seconds`
    # natively, and `build_config_populate_sql` consumes it.
    import json as _yaml_json
    from datetime import datetime as _datetime

    import yaml as _yaml
    from recon_gen.common.l2.config_table import emit_config_populate_sql
    from recon_gen.common.l2.serializer import serialize_l2

    l2_yaml_text = serialize_l2(instance)
    l2_dict_from_yaml = _yaml.safe_load(l2_yaml_text)
    populate_sql = emit_config_populate_sql(
        prefix=prefix,
        cfg_json="{}",
        l2_json=_yaml_json.dumps(
            l2_dict_from_yaml, default=str, separators=(",", ":"),
        ),
        as_of=_datetime(today_ref.year, today_ref.month, today_ref.day, 12, 0, 0),
        dialect=dialect,
    )
    with conn.cursor() as cur:
        execute_script(cur, populate_sql, dialect=dialect)
    conn.commit()

    # 2. Seed (mode-aware via M.4.2).
    report = default_scenario_for(instance, today=today_ref, mode=mode)
    if include_baseline:
        # Match what cli._apply_demo does (densify → broken-rail →
        # boost → baseline).
        scenario = boost_inv_fanout_plants(
            add_broken_rail_plants(
                densify_scenario(report.scenario, factor=5),
                instance, broken_count=15,
            ),
            amount_multiplier=5,
        )
        seed_sql = emit_full_seed(
            instance, scenario, prefix=prefix, anchor=today_ref, dialect=dialect,
        )
    else:
        scenario = report.scenario
        # AY.4.e — plants-only path routes through the spine pipeline
        # (was: emit_seed's OLD per-plant-kind dispatch). The adapter
        # materializes one ViolationGenerator per plant kind;
        # ScenarioContext.compose(dry_run=True) captures the dbapi
        # writes; render_captured_sql produces dialect-appropriate
        # static SQL the same shape execute_script consumes. Plants-
        # only emit carries metadata.scenario_id per the AV.5 contract.
        from recon_gen.common.spine import (
            ScenarioContext,
            dry_run_capture,
            render_captured_sql,
            scenario_to_generators,
        )
        generators = scenario_to_generators(
            scenario, instance,
            anchor=today_ref,
            plant_window=plant_window,
            prefix=prefix,
        )
        cap = dry_run_capture(dialect)
        ctx = ScenarioContext(
            scenario_id=f"apply-db-seed-{prefix}",
            prefix=prefix,
            dialect=dialect,
        )
        captured = ctx.compose(cap, *generators, dry_run=True)  # type: ignore[arg-type]: ViolationGenerator → ClaimedAccountsGenerator Protocol narrowing not inferred at this seam
        seed_sql = (
            render_captured_sql(captured, dialect=dialect)
            if captured else ""
        )
    with conn.cursor() as cur:
        execute_script(cur, seed_sql, dialect=dialect)
    conn.commit()

    # 3. Refresh matviews.
    refresh_sql = refresh_matviews_sql(instance, prefix=prefix, dialect=dialect)
    with conn.cursor() as cur:
        execute_script(cur, refresh_sql, dialect=dialect)
    conn.commit()

    return scenario
