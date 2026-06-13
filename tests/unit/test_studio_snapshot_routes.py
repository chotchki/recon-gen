"""BV.3.3 snapshot — Studio POST /training/snapshot/{take,restore,drop} routes.

Asserts the HTTP surface the test harness drives (via App2Driver verbs):

- 204 on success — each verb invoked with the supplied ?name= param;
- 400 when ?name= missing — actionable for the harness vs. an opaque 500
  buried in the Snapshotter impl;
- 501 when the underlying Snapshotter raises ``NotImplementedError`` —
  surfaces the BV.3.3 foundation stub's "phase 2 not landed yet" message
  during the build-out window (DuckDB → PG → Oracle);
- 500 on other impl-side exceptions, with the exception message in the
  response body for actionable triage;
- 503 when the Studio surface lacks the deps needed to construct a
  Snapshotter (cfg / db_pool absent — bare unit-test surface);
- routes share one Snapshotter instance across requests (lazy-construct
  on first hit, cache on closure state).

The route handlers call ``make_snapshotter`` from
``recon_gen.common.snapshotter`` (moved out of ``tests/e2e/_snapshotter``
in this cell so the runtime Studio server can import it). We monkeypatch
the factory at the routes module's import site so the unit tests don't
need a live pool / cfg / l2 instance.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.config import AwsConfig, Config, DbConfig
from recon_gen.common.db import make_demo_database_url
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.sql import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Local copy of spec_example.yaml so cache writes don't dirty the repo."""
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _duckdb_cfg(tmp_path: Path) -> Config:
    """Minimal Config — these tests don't touch the DB; cfg is only used
    so the routes' ``cfg is None`` guard does NOT short-circuit to 503."""
    from recon_gen.common.config import DatasourceConfig
    db_path = tmp_path / "demo.duckdb"
    return Config(
        aws=AwsConfig(
            account_id="111122223333", region="us-east-1",
            deployment_name="recon-test",
            datasource=DatasourceConfig(
                mode="adopt",
                arn="arn:aws:quicksight:us-east-1:111122223333:datasource/x",
            ),
        ),
        db=DbConfig(table_prefix="test", url=make_demo_database_url(Dialect.DUCKDB, db_path), dialect=Dialect.DUCKDB),
    )


class _StubPool:
    """Stand-in for ``AsyncConnectionPool``.

    The route handlers only pass the pool reference through to
    ``make_snapshotter``; under monkeypatch the factory never touches it.
    Real pool integration is covered by the per-dialect impl cells'
    e2e tests in tests/e2e/db/.
    """

    pass


def _build_app(
    yaml_path: Path,
    *,
    cfg: Config | None,
    db_pool: object | None,
) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    smoke_cfg = _duckdb_cfg(yaml_path.parent)
    tree_app, sheet = build_smoke_app(smoke_cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    # The coverage route's prefix-resolve requires either cfg or
    # prefix_override when db_pool is set; pass an explicit override so
    # the cfg=None surface still mounts the snapshot routes.
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(
            cache,
            cfg=cfg,
            db_pool=db_pool,  # type: ignore[arg-type]: stub pool — factory monkeypatched
            dialect=Dialect.DUCKDB if db_pool is not None else None,
            prefix_override="test" if db_pool is not None else None,
        ),
    )


def _patch_factory(
    monkeypatch: pytest.MonkeyPatch,
    snapshotter: object,
) -> AsyncMock:
    """Replace ``make_snapshotter`` at the routes module's import site.

    Returns the AsyncMock standing in for the factory so the test can
    assert it was awaited (or not). The route handlers import inside the
    closure (``from recon_gen.common.snapshotter import make_snapshotter``),
    so the patch goes on the source module — not on
    ``_studio_routes`` — to intercept the closure's resolve.
    """
    factory = AsyncMock(return_value=snapshotter)
    monkeypatch.setattr(
        "recon_gen.common.snapshotter.make_snapshotter", factory,
    )
    return factory


# ---------- 204 success path ----------


@pytest.mark.parametrize(
    "verb,path",
    [
        ("take", "/training/snapshot/take"),
        ("restore", "/training/snapshot/restore"),
        ("drop", "/training/snapshot/drop"),
    ],
)
def test_post_returns_204_on_success(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    path: str,
) -> None:
    """Each verb POSTs `?name=<n>` → 204 + invokes the matching verb on
    the underlying Snapshotter with the exact name string."""
    snap = MagicMock()
    snap.take = AsyncMock(return_value=None)
    snap.restore = AsyncMock(return_value=None)
    snap.drop = AsyncMock(return_value=None)
    factory = _patch_factory(monkeypatch, snap)

    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(f"{path}?name=plant_42")
    assert resp.status_code == 204
    # Empty body on 204 — Starlette's Response default.
    assert resp.content == b""

    factory.assert_awaited_once()
    invoked = getattr(snap, verb)
    invoked.assert_awaited_once_with("plant_42")


# ---------- 400 missing name ----------


@pytest.mark.parametrize(
    "path",
    [
        "/training/snapshot/take",
        "/training/snapshot/restore",
        "/training/snapshot/drop",
    ],
)
def test_post_returns_400_when_name_missing(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Missing ``?name=`` → 400 with an actionable body. Factory is not
    awaited — the name check fires before the lazy construct."""
    snap = MagicMock()
    snap.take = AsyncMock()
    snap.restore = AsyncMock()
    snap.drop = AsyncMock()
    factory = _patch_factory(monkeypatch, snap)

    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(path)
    assert resp.status_code == 400
    assert "name" in resp.text.lower()
    # 400 fires before lazy construct — factory must not be touched.
    factory.assert_not_awaited()


@pytest.mark.parametrize(
    "path",
    [
        "/training/snapshot/take",
        "/training/snapshot/restore",
        "/training/snapshot/drop",
    ],
)
def test_post_returns_400_when_name_blank(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """``?name=`` (empty / whitespace) → 400 — same validation arm as
    missing name. Catches the "App2Driver constructed an empty name
    string" footgun before the impl raises an opaque ValueError."""
    _patch_factory(monkeypatch, MagicMock())
    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Whitespace-only name should also fail validation.
        resp = c.post(f"{path}?name=%20%20")
    assert resp.status_code == 400


# ---------- 501 NotImplementedError ----------


@pytest.mark.parametrize(
    "verb,path",
    [
        ("take", "/training/snapshot/take"),
        ("restore", "/training/snapshot/restore"),
        ("drop", "/training/snapshot/drop"),
    ],
)
def test_post_returns_501_when_impl_raises_not_implemented(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    path: str,
) -> None:
    """The BV.3.3 foundation stub raises ``NotImplementedError`` for
    every non-aclose verb; the route surfaces that as a 501 with the
    stub's "phase 2 not landed yet" message in the body so the harness
    gets an actionable failure rather than an opaque 500."""
    snap = MagicMock()
    snap.take = AsyncMock(side_effect=NotImplementedError("phase 2 pending"))
    snap.restore = AsyncMock(side_effect=NotImplementedError("phase 2 pending"))
    snap.drop = AsyncMock(side_effect=NotImplementedError("phase 2 pending"))
    _patch_factory(monkeypatch, snap)

    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(f"{path}?name=plant_42")
    assert resp.status_code == 501
    assert "phase 2 pending" in resp.text
    del verb  # parameter only used to keep the parametrize id readable


# ---------- 500 generic impl error ----------


@pytest.mark.parametrize(
    "verb,path",
    [
        ("take", "/training/snapshot/take"),
        ("restore", "/training/snapshot/restore"),
        ("drop", "/training/snapshot/drop"),
    ],
)
def test_post_returns_500_when_impl_raises_unexpected(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    path: str,
) -> None:
    """Unknown impl-side exception → 500 with the exception message in
    the body so the harness can triage without grep'ing server logs."""
    boom = RuntimeError("golden-mirror CTAS exploded")
    snap = MagicMock()
    snap.take = AsyncMock(side_effect=boom)
    snap.restore = AsyncMock(side_effect=boom)
    snap.drop = AsyncMock(side_effect=boom)
    _patch_factory(monkeypatch, snap)

    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(f"{path}?name=plant_42")
    assert resp.status_code == 500
    assert "golden-mirror CTAS exploded" in resp.text
    del verb  # parameter only used to keep the parametrize id readable


# ---------- 503 missing cfg / pool ----------


@pytest.mark.parametrize(
    "path",
    [
        "/training/snapshot/take",
        "/training/snapshot/restore",
        "/training/snapshot/drop",
    ],
)
def test_post_returns_503_when_cfg_missing(
    writable_l2_yaml: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """``make_studio_routes`` without a cfg can't construct a
    Snapshotter. Route stays mounted (App2Driver always tries it) but
    returns 503 with an actionable body so the harness can skip the
    cell."""
    factory = _patch_factory(monkeypatch, MagicMock())
    app = _build_app(writable_l2_yaml, cfg=None, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(f"{path}?name=plant_42")
    assert resp.status_code == 503
    assert "cfg or db_pool missing" in resp.text
    factory.assert_not_awaited()


@pytest.mark.parametrize(
    "path",
    [
        "/training/snapshot/take",
        "/training/snapshot/restore",
        "/training/snapshot/drop",
    ],
)
def test_post_returns_503_when_pool_missing(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Same 503 arm when ``db_pool`` is None — the snapshotter can't run
    DDL/DML without a pool, so the cfg-only surface still 503s rather
    than letting the factory raise downstream."""
    factory = _patch_factory(monkeypatch, MagicMock())
    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.post(f"{path}?name=plant_42")
    assert resp.status_code == 503
    factory.assert_not_awaited()


# ---------- shared Snapshotter instance across requests ----------


def test_snapshotter_constructed_once_across_requests(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Snapshotter is process-local but shared across requests —
    same pool, same v-overlay state. The lazy-construct cache must hold
    after first hit so 14× restore between plants doesn't pay the
    factory's golden-mirror CTAS cost on each call."""
    snap = MagicMock()
    snap.take = AsyncMock(return_value=None)
    snap.restore = AsyncMock(return_value=None)
    snap.drop = AsyncMock(return_value=None)
    factory = _patch_factory(monkeypatch, snap)

    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Hit take once + restore three times — the per-plant pattern.
        for _ in range(4):
            resp_take = c.post("/training/snapshot/take?name=anchor")
            assert resp_take.status_code == 204
            resp_restore = c.post("/training/snapshot/restore?name=anchor")
            assert resp_restore.status_code == 204
    # Factory awaited exactly once; the cache held across the 8 hits.
    factory.assert_awaited_once()
    assert snap.take.await_count == 4
    assert snap.restore.await_count == 4


# ---------- name forwarding ----------


def test_name_forwarded_verbatim(
    writable_l2_yaml: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever ``?name=<v>`` carries reaches the verb verbatim (after
    URL-decode + strip). The per-plant test pattern uses
    ``f"plant_{kind}"`` as the snapshot name, so a regression that
    silently mangled it would mis-restore between plants."""
    snap = MagicMock()
    snap.take = AsyncMock(return_value=None)
    snap.restore = AsyncMock(return_value=None)
    snap.drop = AsyncMock(return_value=None)
    _patch_factory(monkeypatch, snap)

    cfg = _duckdb_cfg(tmp_path)
    app = _build_app(writable_l2_yaml, cfg=cfg, db_pool=_StubPool())
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        c.post("/training/snapshot/take?name=plant_limit_breach_outbound")
        c.post("/training/snapshot/restore?name=plant_limit_breach_outbound")
        c.post("/training/snapshot/drop?name=plant_limit_breach_outbound")

    snap.take.assert_awaited_once_with("plant_limit_breach_outbound")
    snap.restore.assert_awaited_once_with("plant_limit_breach_outbound")
    snap.drop.assert_awaited_once_with("plant_limit_breach_outbound")


