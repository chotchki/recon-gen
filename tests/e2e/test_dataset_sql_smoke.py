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
from typing import Any

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

# Module-level cfg+L2 load (below) needs a live cfg yaml or env overrides;
# under the unit-only CI job neither exists, and load_config(None) raises
# the loud-fail ValueError, taking down pytest collection. Match the rest
# of the e2e suite's RECON_GEN_E2E gate at import time so collection
# cleanly skips this whole module when e2e is off.
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
    # — sqlite3.Cursor doesn't implement the context-manager protocol.
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
    on cfg.db_table_prefix (was previously stamped from L2Instance.instance);
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


# Resolve cfg + l2 + datasets at module-import time so pytest-parametrize
# can name each test case by its DataSetId. Pure-Python builds — no DB or
# AWS contact, safe to call at import; the actual DB connection happens
# inside the per-test fixture.
_CFG = _load_cfg()
_L2 = _load_l2()
_DATASETS = _build_all_datasets(_CFG, _L2)
_DATASETS_BY_ID = {ds.DataSetId: ds for ds in _DATASETS}


@pytest.fixture(scope="module")
def smoke_conn():
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
    Symptom in CI was 3 datasets failing simultaneously
    (app-info-matviews / daily-statement-transactions / transactions).
    Autocommit closes the inter-statement lock-holding window — locks
    drop the instant each SELECT returns. ``_smoke_one``'s rollback
    on the error path stays as defensive housekeeping; the success
    path no longer needs one.
    """
    conn = connect_demo_db(_CFG)
    # Only the PG / Oracle drivers expose `autocommit` as a settable
    # attribute; SQLite is already effectively autocommit at the
    # statement level (it uses BEGIN-DEFERRED semantics + immediate
    # release on commit, and the smoke tests don't write).
    if hasattr(conn, "autocommit"):
        try:
            conn.autocommit = True
        except Exception:  # noqa: BLE001 — best-effort; SQLite raises here
            pass
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("dataset_id", sorted(_DATASETS_BY_ID))
def test_dataset_sql_parses_and_executes(
    dataset_id: str, smoke_conn,
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
    ok, msg = _smoke_one(smoke_conn, _DATASETS_BY_ID[dataset_id])
    assert ok, msg
