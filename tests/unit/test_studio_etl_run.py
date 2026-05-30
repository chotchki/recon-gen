"""BT.3 — ``/studio/etl/run`` route integration tests.

Covers the GET shape (Run button + empty-state when no run has
happened) + the no-DB-pool branch (unit-test surface). Real POST
exercises against the live pipeline live in
``test_studio_deploy_route`` (existing); BT.3 narrows to the
new wire shape — disabled-generator cfg patching + 303 redirect +
last-run-state caching across requests — covered by an in-process
double of ``run_deploy_pipeline``.

The metadata-coverage helper has its own tests in
``test_l2_coverage_metadata``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(yaml_path: Path) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


def test_etl_run_get_returns_200_with_run_button(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/run")
        assert resp.status_code == 200
        body = resp.text
    assert "<title>Studio · ETL · Run" in body
    assert 'id="etl-run-btn"' in body
    # Form posts back to /etl/run.
    assert '<form method="post" action="/etl/run">' in body


def test_etl_run_get_shows_no_runs_yet_when_state_empty(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    assert 'No runs yet' in body


def test_etl_run_get_shows_no_db_pool_banner_when_pool_absent(
    writable_l2_yaml: Path,
) -> None:
    """The unit-test surface omits db_pool — the coverage section
    surfaces a 'No DB pool wired' banner instead of crashing."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    assert 'No DB pool wired' in body


def test_etl_run_post_without_cfg_redirects_to_etl_landing(
    writable_l2_yaml: Path,
) -> None:
    """When make_studio_routes is built without cfg (unit surface),
    POST cannot run the pipeline; bail by 303-redirecting to /etl/
    rather than crashing."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/etl/run", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/etl/"


def test_etl_run_carries_top_nav_with_run_route_active(
    writable_l2_yaml: Path,
) -> None:
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    cfg = make_test_config()

    def fake_nav(active_href: str) -> str:
        return f'<nav data-test-active="{active_href}">NAV</nav>'

    routes = make_studio_routes(cache, top_nav_fn=fake_nav)
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    app = make_app(
        dashboards={"smoke": served},
        studio_routes=routes,
    )
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/run").text
    assert 'data-test-active="/etl/run"' in body


def test_etl_run_landing_card_for_run_lights_up_after_BT_3_ships(
    writable_l2_yaml: Path,
) -> None:
    """BT.1's landing card for Run drops its 'coming in BT.3' hint
    once BT.3 has shipped (this test fires after BT.3 lands)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    assert "coming in BT.3" not in body
    # And the card still points at /etl/run.
    assert 'href="/etl/run"' in body
