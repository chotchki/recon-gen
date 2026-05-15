"""Studio /diagram/coverage route integration tests (X.4.c.5.c/d/f).

These tests verify the JSON-route + chrome wiring end-to-end through
``make_studio_routes`` + ``make_app``, using Starlette's TestClient and
a file-backed aiosqlite pool seeded with a tiny ``<prefix>_transactions``
table. The browser-driven Playwright e2e (toggle the checkbox, then
read ``data-presence`` off the rendered SVG) lives separately under
``tests/e2e/test_studio_diagram_coverage.py`` once we have a Studio
in-process driver — for the unit tier, the contract that matters is:

1. Without a pool, ``GET /diagram/coverage`` 404s and the diagram chrome
   omits the ``#toggle-coverage`` checkbox.
2. With a pool, the route returns ``{nodes: {...}, chain_edges: {...}}``
   shaped like ``coverage_for``'s output, and the chrome carries the
   checkbox + a ``<meta name="diagram-coverage-available">`` so the JS
   shim knows to mount its handler.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from quicksight_gen.common.db import AsyncConnectionPool, make_connection_pool
from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def seeded_studio_pool() -> Iterator[AsyncConnectionPool]:
    """File-backed aiosqlite pool seeded against ``spec_example``'s prefix.

    Same shape as ``test_l2_coverage.py``'s seeded_pool but the table
    name uses the L2 instance's ``instance`` field as the prefix
    (``spec_example_transactions``) so the route's auto-derived prefix
    matches.
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE spec_example_transactions ("
        "id INTEGER PRIMARY KEY, "
        "account_role TEXT, "
        "rail_name TEXT, "
        "template_name TEXT)"
    )
    conn.executemany(
        "INSERT INTO spec_example_transactions "
        "(account_role, rail_name, template_name) VALUES (?, ?, ?)",
        [
            ("CustomerLedger", "ExternalRailInbound", None),
            ("CustomerLedger", "ExternalRailInbound", None),
            ("ExternalCounterparty", "ExternalRailInbound", None),
        ],
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        yield pool
    finally:
        asyncio.run(pool.close())
        os.unlink(path)


def _build_studio_app_no_pool():
    """Studio app with no demo-DB pool — coverage route should be absent."""
    cache = L2InstanceCache.from_path(_FIXTURES / "spec_example.yaml")
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


def _build_studio_app_with_pool(pool: AsyncConnectionPool):
    """Studio app WITH a pool — coverage route + toggle land."""
    cache = L2InstanceCache.from_path(_FIXTURES / "spec_example.yaml")
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(
            cache, db_pool=pool, dialect=Dialect.SQLITE,
        ),
    )


def test_no_pool_coverage_route_404s_and_chrome_omits_toggle() -> None:
    """Graceful degrade: when Studio is constructed without a pool,
    GET /diagram/coverage 404s and the diagram chrome omits the
    Coverage checkbox + the diagram-coverage-available meta.
    """
    app = _build_studio_app_no_pool()
    with TestClient(app) as client:
        # The route shouldn't be mounted at all.
        resp = client.get("/diagram/coverage")
        assert resp.status_code == 404

        # The diagram page renders, just without the coverage chrome.
        diagram_resp = client.get("/diagram")
        assert diagram_resp.status_code == 200
        body = diagram_resp.text
        assert 'id="toggle-coverage"' not in body
        assert 'name="diagram-coverage-available"' not in body


def test_pool_present_coverage_route_returns_json(
    seeded_studio_pool: AsyncConnectionPool,
) -> None:
    """With a pool, GET /diagram/coverage returns the JSON shape the JS
    shim consumes: top-level ``nodes`` + ``chain_edges``, each value a
    ``{present, count}`` dict.
    """
    app = _build_studio_app_with_pool(seeded_studio_pool)
    with TestClient(app) as client:
        resp = client.get("/diagram/coverage")
        assert resp.status_code == 200
        payload = resp.json()
        assert set(payload) == {"nodes", "chain_edges"}

        nodes = payload["nodes"]
        # CustomerLedger seeded with 2 rows.
        assert nodes["role__CustomerLedger"] == {"present": True, "count": 2}
        # CustomerSubledger declared in the YAML, no rows seeded.
        assert nodes["role__CustomerSubledger"] == {"present": False, "count": 0}
        # ExternalRailInbound seeded with 3 rows.
        assert nodes["rail__ExternalRailInbound"] == {"present": True, "count": 3}
        # ExternalRailOutbound declared, no rows.
        assert nodes["rail__ExternalRailOutbound"] == {"present": False, "count": 0}

        chain_edges = payload["chain_edges"]
        # Every declared chain entry lands here.
        assert len(chain_edges) >= 1


def test_pool_present_chrome_carries_toggle_and_meta(
    seeded_studio_pool: AsyncConnectionPool,
) -> None:
    """With a pool, the diagram page surfaces the Coverage checkbox +
    the diagram-coverage-available meta so the JS shim knows to wire it.
    """
    app = _build_studio_app_with_pool(seeded_studio_pool)
    with TestClient(app) as client:
        resp = client.get("/diagram")
        assert resp.status_code == 200
        body = resp.text
        assert 'id="toggle-coverage"' in body
        assert 'name="diagram-coverage-available"' in body


def test_make_studio_routes_pool_without_dialect_raises() -> None:
    """Defensive: passing ``db_pool`` without ``dialect`` is a programmer
    error (the coverage_for fetcher needs ``column_name(...)`` to
    case-fold per dialect). Loud-fail at factory time, not at request
    time.
    """
    cache = L2InstanceCache.from_path(_FIXTURES / "spec_example.yaml")
    # Build a real pool just to have a non-None value to hand in.
    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=":memory:")
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        with pytest.raises(ValueError, match="dialect"):
            make_studio_routes(cache, db_pool=pool)
    finally:
        asyncio.run(pool.close())


# ---------------------------------------------------------------------------
# X.4.c.6 — Trainer route + chrome integration
# ---------------------------------------------------------------------------


def test_trainer_route_returns_json_without_pool() -> None:
    """Trainer is pure scenario walk — no pool needed. Always mounted."""
    app = _build_studio_app_no_pool()
    with TestClient(app) as client:
        resp = client.get("/diagram/trainer")
        assert resp.status_code == 200
        payload = resp.json()
        assert "nodes" in payload
        # spec_example's auto-scenario plants something — assert at least
        # one node has at least one plant kind.
        nodes = payload["nodes"]
        assert len(nodes) > 0
        # Each entry is {plant_kind: count}; counts must be positive ints.
        for node_id, kinds in nodes.items():
            assert isinstance(kinds, dict)
            assert all(isinstance(c, int) and c > 0 for c in kinds.values())
            # Node IDs match the topology prefix scheme.
            assert (
                node_id.startswith("role__")
                or node_id.startswith("rail__")
                or node_id.startswith("tmpl__")
            )


def test_trainer_chrome_toggle_always_present() -> None:
    """The Trainer toggle + meta tag are server-rendered always (the
    overlay reads the in-memory L2, no DB needed)."""
    app = _build_studio_app_no_pool()
    with TestClient(app) as client:
        body = client.get("/diagram").text
        assert 'id="toggle-trainer"' in body
        assert 'name="diagram-trainer-available"' in body
