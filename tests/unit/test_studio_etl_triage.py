"""BT.4 — ``/studio/etl/triage`` route integration tests.

Covers the GET shape (header, empty-state when no gaps detected,
gap-card structure when gaps surface) + the no-DB-pool branch (unit
test surface). Gap-detector semantics are exhaustively covered in
``test_l2_triage``; this file narrows to the rendering wire shape.
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


def test_etl_triage_returns_200_with_header(writable_l2_yaml: Path) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/triage")
        assert resp.status_code == 200
        body = resp.text
    assert "<title>Studio · ETL · Triage" in body
    assert "Studio · ETL · Triage" in body


def test_etl_triage_no_db_pool_renders_banner(writable_l2_yaml: Path) -> None:
    """Unit surface (no db_pool) — the page surfaces a 'No DB pool
    wired' banner instead of crashing."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/triage").text
    assert "No DB pool wired" in body


def test_etl_triage_carries_top_nav_with_triage_route_active(
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
        body = c.get("/etl/triage").text
    assert 'data-test-active="/etl/triage"' in body


def test_etl_triage_landing_card_for_triage_drops_coming_in_hint(
    writable_l2_yaml: Path,
) -> None:
    """BT.1 landing card for Triage drops its 'coming in BT.4' hint
    once BT.4 ships."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    assert "coming in BT.4" not in body
    assert 'href="/etl/triage"' in body


# -- Render-shape tests via in-process call to _render_etl_triage_page -------

# Direct render tests bypass the route's db_pool requirement; let us
# pin the gap-card shape without booting a full pool.

import asyncio
import os
import sqlite3
import tempfile

from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
from recon_gen.common.html._studio_routes import _render_etl_triage_page  # noqa: PLC0415
from recon_gen.common.sql.dialect import Dialect


def _seeded_pool_with_unmatched_rail(yaml_path: Path) -> tuple[AsyncConnectionPool, str]:
    """Build a seeded pool whose transactions table has one row whose
    rail_name doesn't resolve in spec_example.yaml."""
    cache = L2InstanceCache.from_path(yaml_path)
    prefix = yaml_path.stem  # path.stem matches make_studio_routes' default
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "id TEXT PRIMARY KEY, "
        "rail_name TEXT, "
        "template_name TEXT, "
        "account_role TEXT, "
        "account_parent_role TEXT, "
        "amount_direction TEXT NOT NULL, "
        "transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, "
        "metadata TEXT)"
    )
    # spec_example.yaml doesn't declare "phantom_rail" → gap.
    conn.execute(
        f"INSERT INTO {prefix}_transactions VALUES "
        "('tx-1', 'phantom_rail', NULL, 'X', NULL, 'Credit', NULL, "
        "'2030-01-05 09:00:00', NULL)"
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=db_path)
    pool = asyncio.run(make_connection_pool(cfg))
    _ = cache  # cache loaded but the render reads via cache parameter
    return pool, db_path


def test_etl_triage_with_pool_renders_gap_cards(
    writable_l2_yaml: Path,
) -> None:
    """Drive the render through with a seeded pool that produces one
    unmatched-rail gap; assert the card lands."""
    pool, db_path = _seeded_pool_with_unmatched_rail(writable_l2_yaml)
    try:
        cache = L2InstanceCache.from_path(writable_l2_yaml)
        body = asyncio.run(_render_etl_triage_page(
            cache, dev_log=False,
            db_pool=pool, dialect=Dialect.SQLITE,
            prefix_override=None, cfg=None,
            demo_mode=False, top_nav_html="",
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)
    # Card surfaces with the right discriminator + CTA.
    assert 'data-test-gap-kind="unmatched_rail"' in body
    assert 'phantom_rail' in body
    assert '/l2_shape/rail/' in body
    # Header counts the gap.
    assert '1 gap detected' in body


def test_etl_triage_empty_state_when_no_gaps(
    writable_l2_yaml: Path,
) -> None:
    """An empty transactions table means zero gaps → the success
    affirmation lands instead of cards."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    prefix = writable_l2_yaml.stem
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.execute(
        f"CREATE TABLE {prefix}_transactions ("
        "id TEXT PRIMARY KEY, rail_name TEXT, template_name TEXT, "
        "account_role TEXT, account_parent_role TEXT, "
        "amount_direction TEXT NOT NULL, transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, metadata TEXT)"
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=db_path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        body = asyncio.run(_render_etl_triage_page(
            cache, dev_log=False,
            db_pool=pool, dialect=Dialect.SQLITE,
            prefix_override=None, cfg=None,
            demo_mode=False, top_nav_html="",
        ))
    finally:
        asyncio.run(pool.close())
        if os.path.exists(db_path):
            os.unlink(db_path)
    assert 'id="triage-empty"' in body
    assert 'No gaps detected' in body
