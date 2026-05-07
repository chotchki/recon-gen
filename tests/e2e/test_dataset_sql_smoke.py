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

import os
from pathlib import Path

import pytest

from quicksight_gen.apps.executives.datasets import (
    build_all_datasets as build_exec_datasets,
)
from quicksight_gen.apps.investigation.datasets import (
    build_all_datasets as build_inv_datasets,
)
from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
from quicksight_gen.apps.l1_dashboard.datasets import (
    build_all_l1_dashboard_datasets,
)
from quicksight_gen.apps.l2_flow_tracing.datasets import (
    build_all_l2_flow_tracing_datasets,
)
from quicksight_gen.common.config import Config, load_config
from quicksight_gen.common.db import connect_demo_db
from quicksight_gen.common.l2 import L2Instance, load_instance
from quicksight_gen.common.models import DataSet
from tests.integration.verify_dataset_sql import _smoke_one


def _build_all_datasets(cfg: Config, l2: L2Instance) -> list[DataSet]:
    """Every dataset across all 4 apps, with the L2 instance prefix
    threaded onto cfg so dataset SQL renders the right matview names.
    """
    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2.instance))
    return (
        build_all_l1_dashboard_datasets(cfg, l2)
        + build_all_l2_flow_tracing_datasets(cfg, l2)
        + build_inv_datasets(cfg, l2)
        + build_exec_datasets(cfg)
    )


def _load_cfg() -> Config:
    """Load cfg the same way the rest of the e2e suite does — explicit
    QS_GEN_CONFIG override, then per-dialect candidates."""
    explicit = os.environ.get("QS_GEN_CONFIG")
    if explicit:
        return load_config(explicit)
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
    """Honor the same QS_GEN_TEST_L2_INSTANCE override the rest of the
    suite uses; default to the persona-neutral spec_example fixture."""
    override = os.environ.get("QS_GEN_TEST_L2_INSTANCE")
    if override:
        return load_instance(Path(override))
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
    parametrized test. ``_smoke_one`` rolls back per-dataset so the
    connection stays clean across iterations."""
    conn = connect_demo_db(_CFG)
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
