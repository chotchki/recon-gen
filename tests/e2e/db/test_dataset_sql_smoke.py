"""E2E gate: every dataset's CustomSQL parses + executes against the live DB.

Wires the P.9f.e smoke verifier (``tests/integration/verify_dataset_sql.py``)
into the pytest-collected e2e suite so SQL bugs auto-fail rather than
only surfacing when QuickSight tries to render a visual.

Per-dataset parametrize so a single SQL break pinpoints exactly which
dataset's builder emitted bad SQL — pytest-xdist can parallelize the
checks; the failure name shows the offending DataSetId.

Each dataset's CustomSql is wrapped in ``SELECT * FROM (<sql>) sub
WHERE 1=0`` so it parses + binds + plans without returning data — fast
across PG / Oracle, the actual DB error pinpoints the bug.

Background: Y.2.b's broad-anchor pushdown referenced ``source_display``
in a WHERE clause where the column was a SELECT-list alias, not a real
matview column. PG raises ``UndefinedColumn`` at execute time;
QuickSight render fails opaquely. The browser e2e didn't notice because
no assertion checked "did the visual actually return rows" — only
structural shape. This pytest-collected smoke would have caught it
before deploy; CI gating on this prevents a re-occurrence.
"""

from __future__ import annotations


import re
from pathlib import Path
from typing import Any, Iterator

import pytest

from recon_gen.apps.executives.datasets import (
    build_all_datasets as build_exec_datasets,
)
from recon_gen.apps.investigation.datasets import (
    build_all_datasets as build_inv_datasets,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.apps.l1_dashboard.datasets import (
    build_all_l1_dashboard_datasets,
)
from recon_gen.apps.l2_flow_tracing.datasets import (
    build_all_l2_flow_tracing_datasets,
)
from recon_gen.common.config import Config, load_config
from recon_gen.common.db import connect_demo_db
from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_GEN_CONFIG,
    RECON_GEN_E2E,
    RECON_GEN_TEST_L2_INSTANCE,
)
from recon_gen.common.l2 import L2Instance, load_instance
from recon_gen.common.models import DataSet, DatasetParameter

# CB.17.d (2026-06-04) — module-import still loads cfg+L2 because
# pytest-parametrize needs the dataset NAME list at collection time.
# Module-import is DB-INDEPENDENT (yaml parse + pure-python dataset
# construction); the actual DB connection + seeded prefix lookup
# happens via the `seeded_cfg` fixture inside each test. Names are
# cfg-deployment_name-INDEPENDENT (each builder passes a human-readable
# label) so the collection-time PLAIN cfg's names match the runtime
# isolated cfg's names exactly.

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "e2e tests disabled (set RECON_GEN_E2E=1)", allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Parameter substitution + smoke helpers (Y.2.gate.f.1: lifted from the
# deleted ``tests/integration/verify_dataset_sql.py`` CLI script)
# ---------------------------------------------------------------------------
#
# QuickSight wire format: ``<<$paramName>>`` is replaced literally with
# the parameter's value before the SQL hits the database. We substitute
# the same way using each parameter's declared
# ``DefaultValues.StaticValues``. Per-type formatting:
# - SINGLE_VALUED string  → ``'value'``     (single-quoted)
# - MULTI_VALUED string   → ``'a','b','c'`` (comma-separated, used in IN)
# - SINGLE_VALUED int     → ``42``
# - MULTI_VALUED int      → ``1,2,3``
# - SINGLE_VALUED decimal → ``3.14``
# - DateTime              → ``'2030-01-01T00:00:00'`` (treat as string)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _resolve_default(param: DatasetParameter) -> tuple[str, list[Any]] | None:
    for sub in (
        param.StringDatasetParameter,
        param.IntegerDatasetParameter,
        param.DecimalDatasetParameter,
        param.DateTimeDatasetParameter,
    ):
        if sub is None:
            continue
        defaults = sub.DefaultValues
        if defaults is None or not defaults.StaticValues:
            return None
        return sub.Name, list(defaults.StaticValues)
    return None


def _substitute_qs_params(
    sql: str, params: list[DatasetParameter] | None,
) -> str:
    if not params:
        return sql
    for param in params:
        resolved = _resolve_default(param)
        if resolved is None:
            continue
        name, values = resolved
        replacement = ", ".join(_format_value(v) for v in values)
        pattern = re.compile(re.escape(f"<<${name}>>"))
        sql = pattern.sub(replacement, sql)
    return sql


def _wrap_smoke(sql: str) -> str:
    # ``SELECT * FROM (...) sub WHERE 1=0`` parses + binds + plans on PG
    # / Oracle / SQLite, returns zero rows, dialect-agnostic wrapper.
    return f"SELECT * FROM (\n{sql}\n) sub WHERE 1=0"


def _custom_sql(ds: DataSet) -> tuple[str, str]:
    for table_key, physical in ds.PhysicalTableMap.items():
        if physical.CustomSql is not None:
            return physical.CustomSql.SqlQuery, table_key
    raise AssertionError(
        f"Dataset {ds.DataSetId!r} has no CustomSql in PhysicalTableMap "
        f"— this verifier only handles CustomSql datasets."
    )


def _smoke_one(conn: Any, ds: DataSet) -> tuple[bool, str]:
    """Smoke-test one dataset. Returns (success, message).

    Rolls back the connection's transaction on any error so subsequent
    dataset checks can run cleanly. Without this, Postgres aborts the
    transaction on the first SQL error and rejects every following
    statement with ``InFailedSqlTransaction`` — masking real per-
    dataset bugs behind a cascade of bookkeeping errors.
    """
    raw_sql, table_key = _custom_sql(ds)
    sub_sql = _substitute_qs_params(raw_sql, ds.DatasetParameters)
    smoke_sql = _wrap_smoke(sub_sql)
    cur = conn.cursor()
    # try/finally + manual close instead of ``with conn.cursor() as cur``
    # — duckdb.DuckDBPyConnection doesn't implement the context-manager protocol.
    try:
        try:
            cur.execute(smoke_sql)
        finally:
            cur.close()
    except Exception as e:  # noqa: BLE001 — capture every DB error class
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — best-effort
            pass
        preview = smoke_sql[:800]
        return False, (
            f"  {ds.DataSetId} ({table_key}): {type(e).__name__}\n"
            f"    {e}\n"
            f"    SQL preview:\n      "
            + preview.replace("\n", "\n      ")
        )
    return True, ""


def _build_all_datasets(cfg: Config, l2: L2Instance) -> list[DataSet]:
    """Every dataset across all 4 apps. Z.C — the DB-table prefix lives
    on cfg.db.table_prefix (was previously stamped from L2Instance.instance);
    the cfg the operator points at the test e2e DB already carries the
    matching prefix, so dataset SQL renders the right matview names without
    further plumbing.
    """
    return (
        build_all_l1_dashboard_datasets(cfg, l2)
        + build_all_l2_flow_tracing_datasets(cfg, l2)
        + build_inv_datasets(cfg, l2)
        + build_exec_datasets(cfg)
    )


def _load_cfg() -> Config:
    """Load cfg the same way the rest of the e2e suite does — explicit
    RECON_GEN_CONFIG override, then per-dialect candidates."""
    # Soft-fall on validator (matches sweep / fixture pattern).
    try:
        explicit = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit = None
    if explicit is not None:
        return load_config(str(explicit))
    candidates = (
        Path("config.yaml"),
        Path("run/config.yaml"),
        Path("run/config.postgres.yaml"),
        Path("run/config.oracle.yaml"),
    )
    for candidate in candidates:
        if candidate.exists():
            return load_config(str(candidate))
    return load_config(None)


def _load_l2() -> L2Instance:
    """Honor the same RECON_GEN_TEST_L2_INSTANCE override the rest of the
    suite uses; default to the persona-neutral spec_example fixture."""
    override = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


# Resolve cfg + L2 + dataset INDICES at module-import time so
# pytest-parametrize can enumerate test cases at collection. Pure-Python
# build — no DB or AWS contact. ``_build_all_datasets`` is deterministic
# in its ordering, so collection-time PLAIN cfg index N corresponds to
# runtime ``seeded_cfg`` index N — only the cfg-prefix-baked fields
# (DataSetId, CustomSql.Name) differ; structural ordering is identical.
# Names go into pytest's ``ids=`` for test-ID readability (pytest
# auto-disambiguates duplicates as `name0`, `name1`, ...).
_COLLECTION_CFG = _load_cfg()
_COLLECTION_L2 = _load_l2()
_COLLECTION_DATASETS = _build_all_datasets(_COLLECTION_CFG, _COLLECTION_L2)
_DATASET_INDICES = list(range(len(_COLLECTION_DATASETS)))
_DATASET_TEST_IDS = [ds.Name for ds in _COLLECTION_DATASETS]


@pytest.fixture(scope="module")
def runtime_datasets(seeded_cfg: Config) -> list[DataSet]:
    """Rebuild datasets against ``seeded_cfg`` (the per-worker isolated
    prefix). Returns a list in the same order as ``_COLLECTION_DATASETS``
    — index N at collection = index N at runtime.
    """
    return _build_all_datasets(seeded_cfg, _COLLECTION_L2)


@pytest.fixture(scope="module")
def smoke_conn(seeded_cfg: Config) -> Iterator[Any]:
    """Module-scoped DB connection — opened once, reused across every
    parametrized test, set to autocommit so AccessShareLocks release
    statement-by-statement.

    AB.2.followon — switched to autocommit to fix intermittent
    ``DeadlockDetected`` on the PG integration CI job. Without
    autocommit, psycopg/oracledb hold an implicit transaction open
    after each ``SELECT * FROM (<dataset_sql>) WHERE 1=0``. The
    AccessShareLocks the planner+executor took on every referenced
    matview persist until the next commit/rollback. Across pytest-xdist
    workers each holding a module-scoped connection, two workers'
    cumulative lock sets can intersect with an in-flight
    ``REFRESH MATERIALIZED VIEW`` (or autovacuum / autoanalyze taking
    AccessExclusiveLock briefly) and PG's deadlock detector kills one.
    Autocommit closes the inter-statement lock-holding window — locks
    drop the instant each SELECT returns.
    """
    conn = connect_demo_db(seeded_cfg)
    if hasattr(conn, "autocommit"):
        try:
            setattr(conn, "autocommit", True)  # noqa: B010 — psycopg-specific attribute set behind hasattr guard; SyncConnection Protocol doesn't expose it because it's not in PEP 249
        except Exception:  # noqa: BLE001 — best-effort; SQLite raises here
            pass
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("dataset_idx", _DATASET_INDICES, ids=_DATASET_TEST_IDS)
def test_dataset_sql_parses_and_executes(
    dataset_idx: int,
    smoke_conn: Any,
    runtime_datasets: list[DataSet],
) -> None:
    """The dataset's CustomSQL parses, binds default-value
    substitutions, and executes against the live demo DB without
    error.

    Sentinel defaults are by design — the WHERE clause should match
    no rows on the sentinel, but the SQL must still PARSE + PLAN.
    Failure here = the SQL is malformed against this dialect (missing
    column, bad syntax, unknown function); QS would render the visual
    blank or error opaquely.
    """
    if dataset_idx >= len(runtime_datasets):
        pytest.fail(
            f"runtime_datasets has {len(runtime_datasets)} items; "
            f"collection-time vs runtime _build_all_datasets ordering "
            f"diverged at idx={dataset_idx}"
        )
    ds = runtime_datasets[dataset_idx]
    ok, msg = _smoke_one(smoke_conn, ds)
    assert ok, msg
