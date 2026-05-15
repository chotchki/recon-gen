"""Studio POST /deploy route integration tests (X.4.g.13).

The route wraps ``run_deploy_pipeline`` against the cached L2 + the
operator-supplied cfg and returns ``DeploySummary.to_json()`` as the
response body. Status code = 200 on success, 503 on halted (etl_hook
failure short-circuits before any demo-DB mutation).

Smoke-app fixture skips the route when ``cfg`` is omitted from
``make_studio_routes`` (the bare-cache unit-test surface — the route
is conditionally mounted).
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from quicksight_gen.common.config import Config
from quicksight_gen.common.db import connect_demo_db, execute_script
from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.schema import emit_schema
from quicksight_gen.common.sql import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _sqlite_cfg(tmp_path: Path, **overrides: object) -> Config:
    db_path = tmp_path / "demo.sqlite"
    base = Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=f"sqlite:///{db_path}",
        dialect=Dialect.SQLITE,
    )
    if overrides:
        base = replace(base, **overrides)  # type: ignore[arg-type]: replace's overload erases the per-field types
    return base


def _apply_schema(cfg: Config, yaml_path: Path) -> None:
    instance = load_instance(yaml_path)
    schema_sql = emit_schema(instance, dialect=cfg.dialect)
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(cur, schema_sql, dialect=cfg.dialect)
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def _build_app(yaml_path: Path, cfg: Config | None) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    smoke_cfg = _sqlite_cfg(yaml_path.parent)  # smoke app needs a Config too
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


# ---------- success path ----------

def test_post_deploy_runs_pipeline_returns_summary(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    """POST /deploy with no etl_hook configured runs steps 2-5 against
    the demo DB; returns 200 + the DeploySummary JSON."""
    cfg = _sqlite_cfg(tmp_path)
    _apply_schema(cfg, writable_l2_yaml)
    app = _build_app(writable_l2_yaml, cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post("/deploy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["halted"] is False
    assert body["halt_reason"] is None
    assert body["step1_etl_hook_exit_code"] == 0
    # The full pipeline ran — step 5's bump produced a positive id.
    assert body["step5_data_generation_id"] > 0
    assert body["step4_matviews_done"] is True
    # The empty pre-pipeline DB → step 3's full-scope generator wrote rows.
    assert body["step3_generator"]["transactions_after"] > 0


# ---------- halt path ----------

def test_post_deploy_halts_on_etl_hook_failure(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    """POST /deploy with a failing etl_hook returns 503 (Service
    Unavailable) + halted=True summary; demo DB stays untouched."""
    cfg = _sqlite_cfg(tmp_path, etl_hook="false")
    _apply_schema(cfg, writable_l2_yaml)
    app = _build_app(writable_l2_yaml, cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post("/deploy")
    assert resp.status_code == 503
    body = resp.json()
    assert body["halted"] is True
    assert body["halt_reason"] is not None
    assert "etl_hook returned exit_code=1" in body["halt_reason"]
    assert body["step4_matviews_done"] is False
    assert body["step5_data_generation_id"] == 0


# ---------- conditional mount ----------

def test_post_deploy_omitted_when_cfg_unset(
    writable_l2_yaml: Path,
) -> None:
    """``make_studio_routes(cache)`` without a cfg silently omits the
    route — the bare-cache unit-test surface that doesn't exercise the
    pipeline. POST /deploy returns 405 (method not allowed) since
    no handler is registered for that path / method."""
    app = _build_app(writable_l2_yaml, cfg=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post("/deploy")
    # Starlette returns 405 when the path matches no route; the
    # route is unmounted, so any method on /deploy should 404.
    assert resp.status_code == 404
