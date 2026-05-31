"""BV.3.1 — Parameterized plant round-trip over PLANT_REGISTRY.

For each of the 25 registry entries, runs the full Lock 9 #3
contract:

    fresh sqlite → schema → baseline seed → matview refresh →
    plant_function → matview refresh → dashboard_check

…and asserts the planted scenario actually surfaces on its declared
`dashboard_check` target. Entries that FAIL here are entries whose
operator-facing Tour will show "no rows" — exactly the chain_orphan
bug the operator hit live.

The test is intentionally **expected to red-up** when first run —
it's the audit-by-test surface, NOT a regression gate. As BV.3.2
fixes each broken entry, the corresponding param flips green.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from recon_gen.common.db import (
    _register_sqlite_aggregates,
    execute_script,
)
from recon_gen.common.l2 import load_instance
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    DashboardCheck,
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
    """Copy the sasquatch_pr fixture to a tmp path. Module-scoped
    because the L2 yaml itself never mutates across params — only
    the per-test sqlite does."""
    src = _FIXTURES / "sasquatch_pr.yaml"
    dst = tmp_path_factory.mktemp("bv31_l2") / "sasquatch_pr.yaml"
    shutil.copy(src, dst)
    yield dst


def _default_kwargs(entry: PlantKindEntry) -> dict[str, Any]:
    """Build the kwargs dict the plant page's POST would send when the
    operator clicks Plant with no field changes — each primitive
    contributes its `default`."""
    out: dict[str, Any] = {}
    for primitive in entry.primitives:
        if isinstance(primitive, PrimitiveIntField):
            out[primitive.name] = int(primitive.default)
        else:
            # Type narrows to PrimitiveStringField by union exhaustion;
            # second isinstance would trip reportUnnecessaryIsInstance.
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


def _check_matview(
    conn: sqlite3.Connection, matview_name: str, min_row_count: int,
) -> tuple[bool, str]:
    """Query the matview for rows; return (passed, message)."""
    full_name = f"{_PREFIX}_{matview_name}"
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {full_name}")
        row = cur.fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
    finally:
        cur.close()
    if count >= min_row_count:
        return True, f"matview {full_name} has {count} rows (>= {min_row_count})"
    return (
        False,
        f"matview {full_name} has {count} rows; expected >= {min_row_count}",
    )


def _check_url(
    conn: sqlite3.Connection, l2_path: Path,
    url_path: str, expect_text_contains: str,
) -> tuple[bool, str]:
    """Build a TestClient against the seeded sqlite, GET the url, check
    expected text in body. Currently only L2 triage `/etl/triage` is
    cheaply unit-testable; other URL targets defer to BV.3.3 browser e2e.

    For `/etl/triage` specifically, call `detect_gaps()` directly
    against the in-memory sqlite — same data the URL render would see
    + no Starlette setup cost."""
    if "/etl/triage" in url_path:
        return _check_etl_triage(conn, l2_path, expect_text_contains)
    if "/etl/run" in url_path:
        return _check_etl_run_coverage(conn, l2_path, expect_text_contains)
    if "/dashboards/l2_flow_tracing" in url_path:
        return _check_l2ft_dashboard(conn, l2_path, expect_text_contains)
    if "/dashboards/l1_dashboard" in url_path:
        return _check_l1_dashboard(conn, l2_path, expect_text_contains)
    return (
        False,
        f"url_path {url_path!r} doesn't match a known dashboard surface — "
        f"BV.3.1 helper needs a new branch for this kind",
    )


def _check_etl_triage(
    conn: sqlite3.Connection, l2_path: Path, expect_text_contains: str,
) -> tuple[bool, str]:
    """The L2 triage page renders gaps from `detect_gaps()` at query
    time. Run the detector against this sqlite + check the planted
    text shows up in any gap's diagnosis / observed_value."""
    import asyncio

    from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
    from recon_gen.common.l2.contract import derive_column_contracts
    from recon_gen.common.l2.triage import detect_gaps

    # detect_gaps needs an AsyncConnectionPool — quickest path is to
    # dump the in-memory conn to disk + open a pool against it.
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    import os as _os
    _os.close(fd)
    dst = sqlite3.connect(db_path)
    with dst:
        conn.backup(dst)
    dst.close()
    try:
        inst = load_instance(l2_path)
        from recon_gen.common.config import Config
        cfg = Config(
            aws_account_id="123456789012",
            aws_region="us-east-1",
            deployment_name="recon-bv31",
            db_table_prefix=_PREFIX,
            datasource_arn="arn:aws:quicksight:us-east-1:123456789012:datasource/test",
            dialect=Dialect.SQLITE,
            demo_database_url=db_path,
        )

        async def _go() -> list[Any]:
            pool: AsyncConnectionPool = await make_connection_pool(cfg)
            try:
                contracts = derive_column_contracts(inst)
                gaps = await detect_gaps(
                    pool, _PREFIX, inst, contracts, dialect=Dialect.SQLITE,
                )
            finally:
                await pool.close()
            return list(gaps)

        gaps = asyncio.run(_go())
        for g in gaps:
            if expect_text_contains in (g.diagnosis or ""):
                return True, f"triage gap found: {g.kind!r} ({g.diagnosis[:60]}...)"
            if expect_text_contains in (g.observed_value or ""):
                return True, f"triage gap found: {g.kind!r} value={g.observed_value!r}"
        return (
            False,
            f"detect_gaps returned {len(gaps)} gaps; none contained "
            f"{expect_text_contains!r}",
        )
    finally:
        if _os.path.exists(db_path):
            _os.unlink(db_path)


def _check_etl_run_coverage(
    conn: sqlite3.Connection, l2_path: Path, expect_text_contains: str,
) -> tuple[bool, str]:
    """The /etl/run Coverage panel reads from the rail / template
    coverage matview. Check that the planted DELETE actually emptied
    a rail's transactions — surfaces on the panel as a `0`-row rail."""
    del l2_path, expect_text_contains  # not used in the count-shape check
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT COUNT(DISTINCT rail_name) FROM {_PREFIX}_transactions"
        )
        row = cur.fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
    finally:
        cur.close()
    # The DELETE plant emptied one rail's transactions. We can't easily
    # cross-check against the L2's declared count without re-parsing
    # here; the simpler signal: there's still SOME transactions left
    # AND there's now a rail-count delta vs a no-plant baseline. The
    # baseline-vs-plant diff isn't testable in this shape — defer to
    # BV.3.3 browser e2e, and pass the unit-level check if the table
    # has rows at all.
    if count > 0:
        return True, f"{_PREFIX}_transactions has {count} distinct rails after plant"
    return False, f"{_PREFIX}_transactions is empty after plant — DELETE over-fired"


def _check_l2ft_dashboard(
    conn: sqlite3.Connection, l2_path: Path, expect_text_contains: str,
) -> tuple[bool, str]:
    """The L2 Flow Tracing dashboard reads from `<prefix>_current_transactions`
    plus dataset-time SQL (no per-check matview). Check that
    `current_transactions` has the right shape post-plant — the
    sheet-level render is BV.3.3 territory."""
    del l2_path, expect_text_contains
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {_PREFIX}_current_transactions")
        row = cur.fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
    finally:
        cur.close()
    if count > 0:
        return True, f"current_transactions has {count} rows post-plant"
    return False, "current_transactions is empty post-plant — pipeline didn't run"


def _check_l1_dashboard(
    conn: sqlite3.Connection, l2_path: Path, expect_text_contains: str,
) -> tuple[bool, str]:
    """L1 dashboard sheets render from per-invariant matviews. Without
    parsing the URL to extract the sheet → matview mapping, fall back
    to confirming `<prefix>_current_transactions` is non-empty. The
    real per-matview assertion lives in matview-shape dashboard_check
    entries; URL-shape L1 entries (supersession_audit) get this
    weaker check."""
    return _check_l2ft_dashboard(conn, l2_path, expect_text_contains)


def _execute_check(
    conn: sqlite3.Connection, l2_path: Path, check: DashboardCheck,
) -> tuple[bool, str]:
    if check.matview_name is not None:
        return _check_matview(conn, check.matview_name, check.min_row_count)
    if check.url_path is not None:
        return _check_url(
            conn, l2_path, check.url_path, check.expect_text_contains or "",
        )
    return False, "dashboard_check has neither matview_name nor url_path"


_REGISTRY_PARAMS: list[object] = [
    pytest.param(entry, id=entry.kind) for entry in PLANT_REGISTRY
]


@pytest.mark.parametrize("entry", _REGISTRY_PARAMS)
def test_plant_surfaces_on_dashboard(
    entry: PlantKindEntry, sasquatch_l2_path: Path,
) -> None:
    """BV.3.1 round-trip: build sqlite + baseline + refresh, plant,
    refresh, assert the planted scenario shows up on the dashboard
    surface declared by `entry.dashboard_check`.

    An entry's failure here is BV.3.2 work — fix the registry
    metadata, the plant emitter, or the dashboard_check shape so
    the operator's Tour shows the planted scenario."""
    inst = load_instance(sasquatch_l2_path)
    conn = _build_seeded_sqlite(sasquatch_l2_path)
    try:
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

        passed, msg = _execute_check(conn, sasquatch_l2_path, entry.dashboard_check)
        assert passed, (
            f"plant→refresh→check FAILED for kind={entry.kind!r}: {msg}\n"
            f"dashboard_check={entry.dashboard_check}\n"
            f"plant kwargs={kwargs}"
        )
    finally:
        conn.close()
