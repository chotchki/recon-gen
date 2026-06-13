"""CZ.4 — Studio POST /deploy etl_hook gate tests.

Refuse outright (HTTP 409 + structured JSON) when ``cfg.app2.etl_hook is
None`` — standalone-mode means no ETL cycle will re-populate the
demo DB after Deploy's wipe-and-reseed step, so any unmarked row in
the DB might be real customer data. No click-through; the operator
must either configure ``cfg.app2.etl_hook`` (the supported integration
path) or drop to the CLI (``recon-gen data apply --execute``) for
the documented escape hatch. The Trainer button (BU.1.6) remains
available in standalone-mode with DELETE-synthetic-only semantics —
that's the path for "I want demo-data wipe behavior". See PLAN
Phase CZ + the repo CLAUDE.md.

This file pins the route-level contract:

- ``cfg.app2.etl_hook = "true"`` (or any configured value) → existing
  behavior. POST /deploy runs the pipeline + returns the usual
  ``DeploySummary`` JSON (200 success / 503 halted). Already covered
  by ``test_studio_deploy_route.py`` post-CZ.4; mirrored here so the
  CZ.4 test file documents the full contract.
- ``cfg.app2.etl_hook = None`` → refuse with 409. Response body carries
  ``halted: True`` + ``halt_reason: "standalone-mode"`` + a ``message``
  string that explains WHY (cfg.app2.etl_hook is None means we cannot
  prove unmarked rows are synthetic) and WHAT the operator can do
  (configure etl_hook, use the Trainer button, or use the CLI).
  ``run_deploy_pipeline`` is NOT invoked.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.config import AwsConfig, Config
from recon_gen.common.db import (
    connect_demo_db,
    execute_script,
    make_demo_database_url,
)
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.sql import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _duckdb_cfg(tmp_path: Path, **overrides: object) -> Config:
    db_path = tmp_path / "demo.duckdb"
    base = Config(
        aws=AwsConfig(account_id="111122223333"),
        aws_region="us-east-1",
        deployment_name="recon-test",
        db_table_prefix="test",
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=make_demo_database_url(Dialect.DUCKDB, db_path),
        dialect=Dialect.DUCKDB,
    )
    if overrides:
        base = replace(base, **overrides)  # type: ignore[arg-type]: replace's overload erases the per-field types
    return base


def _apply_schema(cfg: Config, yaml_path: Path) -> None:
    instance = load_instance(yaml_path)
    schema_sql = emit_schema(
        instance, prefix=cfg.db.table_prefix, dialect=cfg.db.dialect,
    )
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(cur, schema_sql, dialect=cfg.db.dialect)
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def _build_app(yaml_path: Path, cfg: Config | None) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    smoke_cfg = _duckdb_cfg(yaml_path.parent)
    tree_app, sheet = build_smoke_app(smoke_cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache, cfg=cfg),
    )


# ---------- refuse path ----------

def test_post_deploy_refuses_when_etl_hook_is_none(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    """CZ.4 — ``cfg.app2.etl_hook is None`` triggers the standalone-mode
    refuse path. Response is 409 Conflict + structured JSON; the
    pipeline is NOT invoked.
    """
    cfg = _duckdb_cfg(tmp_path)
    assert cfg.app2.etl_hook is None  # sanity — this is the refuse scenario
    _apply_schema(cfg, writable_l2_yaml)
    app = _build_app(writable_l2_yaml, cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/deploy")

    assert resp.status_code == 409
    body = resp.json()
    assert body["halted"] is True
    assert body["halt_reason"] == "standalone-mode"
    # The message must name WHY (etl_hook empty → standalone-mode) and
    # the two unblock paths (configure etl_hook OR the Trainer button
    # OR the CLI). All three are operator-actionable; refusal is a
    # redirect to the correct path, not a dead-end.
    message = body["message"]
    assert "Standalone mode" in message
    assert "cfg.app2.etl_hook" in message
    assert "Trainer" in message
    assert "recon-gen data apply --execute" in message


def test_post_deploy_refuse_does_not_invoke_pipeline(
    writable_l2_yaml: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refuse path short-circuits BEFORE ``run_deploy_pipeline``
    is invoked. Monkeypatch the pipeline entry-point to a sentinel
    that records calls; assert it never fires on the refuse path.

    This is the load-bearing CZ.4 invariant — even a "halted=True
    with halt_reason=standalone-mode" response is moot if the
    pipeline ran first and wiped the DB. Verify at the call boundary,
    not via DB observation (which can lag or be order-dependent).
    """
    cfg = _duckdb_cfg(tmp_path)
    assert cfg.app2.etl_hook is None
    _apply_schema(cfg, writable_l2_yaml)

    pipeline_calls: list[object] = []

    async def _spy_pipeline(*args: object, **kwargs: object) -> object:
        pipeline_calls.append((args, kwargs))
        raise AssertionError(
            "CZ.4 violation: run_deploy_pipeline invoked on the "
            "standalone-mode refuse path"
        )

    monkeypatch.setattr(
        "recon_gen.common.html._studio_routes.run_deploy_pipeline",
        _spy_pipeline,
    )

    app = _build_app(writable_l2_yaml, cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/deploy")

    assert resp.status_code == 409
    assert pipeline_calls == [], (
        "refuse path must not invoke run_deploy_pipeline"
    )


# ---------- success path (etl_hook configured) ----------

def test_post_deploy_proceeds_when_etl_hook_configured(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    """``cfg.app2.etl_hook`` configured → existing behavior. Pipeline runs
    + returns ``DeploySummary`` JSON. Uses ``true`` (the shell builtin)
    as a no-op hook that exits 0 so step 1 succeeds and steps 2-5
    proceed. Mirrors the post-CZ.4 contract in
    ``test_studio_deploy_route.py::test_post_deploy_runs_pipeline_returns_summary``.
    """
    cfg = _duckdb_cfg(tmp_path, etl_hook="true")
    _apply_schema(cfg, writable_l2_yaml)
    app = _build_app(writable_l2_yaml, cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/deploy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["halted"] is False
    # CZ.4's refuse path uses a distinct halt_reason; success path
    # carries the pipeline's None.
    assert body["halt_reason"] is None
    # Sanity — the pipeline produced real output.
    assert body["step5_data_generation_id"] > 0
