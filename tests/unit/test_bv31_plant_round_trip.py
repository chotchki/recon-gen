"""BV.3.1 — Parameterized plant round-trip over PLANT_REGISTRY.

For each of the 25 registry entries, runs the **clean-before /
present-after** Lock 9 #3 contract:

    fresh sqlite → schema → baseline seed → matview refresh
                                ↓
                    BEFORE: snapshot dashboard_check signal
                                ↓
        plant_function (operator's default form values)
                                ↓
                       matview refresh
                                ↓
                    AFTER: snapshot dashboard_check signal
                                ↓
    assert (after - before) >= dashboard_check.min_row_count

The before/after diff is the load-bearing claim — proves the plant
*caused* the dashboard delta, not that the dashboard happens to
have the expected text post-baseline (which it might from baseline
gaps unrelated to the plant).

Entries that fail this round-trip are BV.3.2 work — fix the plant
emitter, the dashboard_check, or both.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from recon_gen.common.db import (
    AsyncConnectionPool, _register_sqlite_aggregates, execute_script,
    make_connection_pool,
)
from recon_gen.common.l2 import load_instance
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantKindEntry,
    PrimitiveIntField,
)
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.seed import emit_baseline_seed
from recon_gen.common.sql.dialect import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"
_PREFIX = "sasquatch_pr"
_ANCHOR = datetime(2026, 5, 30, 12, 0, 0)


@pytest.fixture(scope="module")
def sasquatch_l2_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Copy the sasquatch_pr fixture to a tmp path."""
    src = _FIXTURES / "sasquatch_pr.yaml"
    dst = tmp_path_factory.mktemp("bv31_l2") / "sasquatch_pr.yaml"
    shutil.copy(src, dst)
    yield dst


def _default_kwargs(entry: PlantKindEntry) -> dict[str, Any]:
    """Build the kwargs the plant page's POST would send when the
    operator clicks Plant with no field changes."""
    out: dict[str, Any] = {}
    for primitive in entry.primitives:
        if isinstance(primitive, PrimitiveIntField):
            out[primitive.name] = int(primitive.default)
        else:
            out[primitive.name] = str(primitive.default)
    return out


def _build_seeded_sqlite(l2_path: Path) -> sqlite3.Connection:
    """Per-entry fresh sqlite: schema + baseline + initial matview
    refresh. Returns the connection ready for plant SQL."""
    inst = load_instance(l2_path)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    cur = conn.cursor()
    schema_sql = emit_schema(inst, prefix=_PREFIX, dialect=Dialect.SQLITE)
    execute_script(cur, schema_sql, dialect=Dialect.SQLITE)
    seed_sql = emit_baseline_seed(
        inst,
        prefix=_PREFIX,
        anchor=date(2026, 5, 30),
        dialect=Dialect.SQLITE,
        skip_rails=frozenset(),
        base_seed=42,
    )
    execute_script(cur, seed_sql, dialect=Dialect.SQLITE)
    refresh_sql = refresh_matviews_sql(inst, prefix=_PREFIX, dialect=Dialect.SQLITE)
    execute_script(cur, refresh_sql, dialect=Dialect.SQLITE)
    conn.commit()
    cur.close()
    return conn


# -- Signal probes (return an int "thing count" the assert can diff) -------


def _signal_matview(conn: sqlite3.Connection, matview_name: str) -> int:
    """Row count of the matview the dashboard reads from."""
    full_name = f"{_PREFIX}_{matview_name}"
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {full_name}")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        cur.close()


def _signal_etl_triage(
    conn: sqlite3.Connection, l2_path: Path, expected_gap_kind: str | None,
) -> int:
    """Count of gaps the L2 triage detector returns. When
    ``expected_gap_kind`` is set (drawn from the registry entry's
    section_kind), narrows to gaps of THAT kind — proves the planted
    kind specifically increased, not just total gap count."""
    from recon_gen.common.config import Config
    from recon_gen.common.l2.contract import derive_column_contracts
    from recon_gen.common.l2.triage import detect_gaps

    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    dst = sqlite3.connect(db_path)
    with dst:
        conn.backup(dst)
    dst.close()
    try:
        inst = load_instance(l2_path)
        cfg = Config(
            aws_account_id="123456789012",
            aws_region="us-east-1",
            deployment_name="recon-bv31",
            db_table_prefix=_PREFIX,
            datasource_arn="arn:aws:quicksight:us-east-1:123456789012:datasource/test",
            dialect=Dialect.SQLITE,
            demo_database_url=db_path,
        )

        async def _go() -> int:
            pool: AsyncConnectionPool = await make_connection_pool(cfg)
            try:
                contracts = derive_column_contracts(inst)
                gaps = await detect_gaps(
                    pool, _PREFIX, inst, contracts, dialect=Dialect.SQLITE,
                )
            finally:
                await pool.close()
            if expected_gap_kind is not None:
                return sum(1 for g in gaps if g.kind == expected_gap_kind)
            return len(list(gaps))

        return asyncio.run(_go())
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def _signal_etl_run_coverage(
    conn: sqlite3.Connection, l2_path: Path,
) -> int:
    """Count of L2-declared primitives with present=False in
    ``coverage_for`` — same data the /etl/run Coverage panel renders."""
    from recon_gen.common.config import Config
    from recon_gen.common.l2.coverage import coverage_for

    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    dst = sqlite3.connect(db_path)
    with dst:
        conn.backup(dst)
    dst.close()
    try:
        inst = load_instance(l2_path)
        cfg = Config(
            aws_account_id="123456789012",
            aws_region="us-east-1",
            deployment_name="recon-bv31",
            db_table_prefix=_PREFIX,
            datasource_arn="arn:aws:quicksight:us-east-1:123456789012:datasource/test",
            dialect=Dialect.SQLITE,
            demo_database_url=db_path,
        )

        async def _go() -> int:
            pool: AsyncConnectionPool = await make_connection_pool(cfg)
            try:
                cov_map = await coverage_for(
                    pool, _PREFIX, inst, dialect=Dialect.SQLITE,
                )
            finally:
                await pool.close()
            return sum(
                1 for entry in cov_map.by_node_id.values()
                if not entry.present
            )

        return asyncio.run(_go())
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def _signal_for(
    conn: sqlite3.Connection, l2_path: Path, entry: PlantKindEntry,
) -> int:
    """Dispatch to the right probe for the entry's dashboard_check shape.

    Returns the signal count — the test diffs before vs after.
    Entries whose dashboard surface BV.3.1 can't cheaply unit-test
    (L2FT exception SQL, L1 sheet rendering) return 0 here and the
    test xfails them — BV.3.3 browser e2e is their gate.
    """
    check = entry.dashboard_check
    if check.matview_name is not None:
        return _signal_matview(conn, check.matview_name)
    if check.url_path is None:
        return 0
    if "/etl/triage" in check.url_path:
        # entry.section_kind doubles as the GapKind literal for L2
        # Triage rows (per BU.2a typed source).
        return _signal_etl_triage(conn, l2_path, entry.section_kind or entry.kind)
    if "/etl/run" in check.url_path:
        return _signal_etl_run_coverage(conn, l2_path)
    # L2FT + L1 dashboard URLs aren't unit-testable here — defer to BV.3.3.
    return -1  # sentinel: not-checkable-at-unit-layer


_REGISTRY_PARAMS: list[object] = [
    pytest.param(entry, id=entry.kind) for entry in PLANT_REGISTRY
]


@pytest.mark.parametrize("entry", _REGISTRY_PARAMS)
def test_plant_surfaces_on_dashboard(
    entry: PlantKindEntry, sasquatch_l2_path: Path,
) -> None:
    """BV.3.1 clean-before / present-after round-trip.

    Builds a fresh sqlite, snapshots the dashboard_check signal BEFORE
    planting, runs the plant + matview refresh, snapshots AFTER, then
    asserts the signal delta >= dashboard_check.min_row_count.

    Entries that fail are BV.3.2 fix queue — wrong plant SQL,
    mis-aimed dashboard_check, or missing picker coverage in
    sasquatch_pr."""
    inst = load_instance(sasquatch_l2_path)
    conn = _build_seeded_sqlite(sasquatch_l2_path)
    try:
        # BEFORE: signal on the clean baseline.
        before = _signal_for(conn, sasquatch_l2_path, entry)
        if before == -1:
            pytest.skip(
                f"dashboard_check shape for kind={entry.kind!r} "
                f"(url_path={entry.dashboard_check.url_path!r}) isn't "
                f"unit-testable at BV.3.1 — BV.3.3 browser e2e is its gate"
            )

        kwargs = _default_kwargs(entry)
        plant_sql = entry.plant_function(
            prefix=_PREFIX,
            dialect=Dialect.SQLITE,
            anchor=_ANCHOR,
            instance=inst,
            **kwargs,
        )
        cur = conn.cursor()
        execute_script(cur, plant_sql, dialect=Dialect.SQLITE)
        refresh_sql = refresh_matviews_sql(
            inst, prefix=_PREFIX, dialect=Dialect.SQLITE,
        )
        execute_script(cur, refresh_sql, dialect=Dialect.SQLITE)
        conn.commit()
        cur.close()

        # AFTER: signal post-plant + refresh.
        after = _signal_for(conn, sasquatch_l2_path, entry)
        delta = after - before
        min_delta = entry.dashboard_check.min_row_count

        assert delta >= min_delta, (
            f"plant didn't move the dashboard for kind={entry.kind!r}\n"
            f"  dashboard_check={entry.dashboard_check}\n"
            f"  plant kwargs={kwargs}\n"
            f"  before={before}  after={after}  delta={delta}  "
            f"required delta >= {min_delta}"
        )
    finally:
        conn.close()
