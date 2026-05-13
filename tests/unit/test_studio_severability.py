"""X.4.a.3 — Studio severability test.

The SPEC's severability contract (``SPEC_studio.md`` §"Process model"):

> ``dashboards`` MUST keep working with Studio's routes absent. Studio
> routes never assume Dashboards-side state (no shared in-memory cache
> that Dashboards reads). When phase.2 auth lands and Studio needs
> writes-grade auth, splitting Studio into its own process is a
> routing-table edit, not a rewrite.

This test pins three observable consequences:

1. **Dashboards-only mount works as before.** ``make_app(... studio_routes
   =None)`` keeps the X.2-era ``GET / → /dashboards`` redirect; the
   four Dashboards routes resolve.
2. **Studio mount overrides ``GET /``.** ``make_app(... studio_routes=[
   ...])`` serves the Studio landing on ``GET /`` (NOT a redirect),
   while every Dashboards route still resolves alongside.
3. **No import coupling.** ``cli.dashboards`` does NOT import the
   Studio routes module or the L2 cache (verified by source grep —
   the import-graph severability is a code-time invariant, not just
   a runtime one).

The third check is a static-analysis assertion against the dashboards
module's source — cheaper than monkeypatching ``sys.modules``, and
directly catches the regression class ("someone wired Studio into
Dashboards").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from tests._test_helpers import make_test_config
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.tree.structure import Analysis, App, Sheet
from quicksight_gen.common.tree.visuals import Sankey


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


def _build_dashboard() -> ServedDashboard:
    """Minimal ServedDashboard sufficient to mount + answer GET /dashboards."""
    cfg = make_test_config()
    app = App(name="severability-test", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="severability-analysis",
        name="Severability Test",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("only"),
        name="Only", title="Only Sheet", description="x",
    ))
    sheet.visuals.append(Sankey(
        title="Sankey", subtitle="t",
        visual_id=VisualId("v-sankey"),
    ))
    return ServedDashboard(
        tree_app=app, sheet=sheet, title="Test Dashboard",
        data_fetcher=lambda _v, _p: {},
    )


# -- 1. Dashboards-only mount -----------------------------------------------


def test_dashboards_only_keeps_root_redirect() -> None:
    """No ``studio_routes`` → ``GET /`` is the X.2 redirect to /dashboards."""
    asgi = make_app(dashboards={"d": _build_dashboard()})
    client = TestClient(asgi, follow_redirects=False)
    r = client.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/dashboards"


def test_dashboards_only_resolves_dashboards_routes() -> None:
    asgi = make_app(dashboards={"d": _build_dashboard()})
    client = TestClient(asgi)
    assert client.get("/dashboards").status_code == 200
    assert client.get("/dashboards/d").status_code == 200


# -- 2. Studio + Dashboards mount -------------------------------------------


def test_studio_mount_overrides_root_with_landing() -> None:
    """``studio_routes`` set → ``GET /`` is the Studio landing, not a redirect."""
    cache = L2InstanceCache.from_path(_SPEC_EXAMPLE)
    asgi = make_app(
        dashboards={"d": _build_dashboard()},
        studio_routes=make_studio_routes(cache),
    )
    client = TestClient(asgi, follow_redirects=False)
    r = client.get("/")
    assert r.status_code == 200, r.text
    body = r.text
    assert "Studio" in body, body
    # Cache wired through: instance prefix appears in the placeholder body.
    assert str(cache.get().instance) in body, body


def test_studio_mount_keeps_dashboards_routes_alive() -> None:
    """The Studio mount is ADDITIVE; Dashboards routes still resolve."""
    cache = L2InstanceCache.from_path(_SPEC_EXAMPLE)
    asgi = make_app(
        dashboards={"d": _build_dashboard()},
        studio_routes=make_studio_routes(cache),
    )
    client = TestClient(asgi)
    assert client.get("/dashboards").status_code == 200
    assert client.get("/dashboards/d").status_code == 200


# -- 3. Import-time severability --------------------------------------------


def _read_module_source(module: Any) -> str:  # type: ignore[no-untyped-def]: parameter is a Python module object — its file path is what we want
    return Path(module.__file__).read_text()


def test_dashboards_module_does_not_import_studio() -> None:
    """``cli.dashboards`` source has no Studio-side imports.

    A regression here means "someone made Dashboards depend on Studio";
    catches it at code-time before the runtime severability degrades.
    """
    from quicksight_gen.cli import dashboards as dashboards_module
    source = _read_module_source(dashboards_module)
    forbidden = (
        "_studio_routes",
        "make_studio_routes",
        "L2InstanceCache",
        "from quicksight_gen.cli.studio",
        "from quicksight_gen.common.l2.cache",
    )
    for token in forbidden:
        assert token not in source, (
            f"cli.dashboards must not reference {token!r} — it would "
            f"break the severability contract (SPEC_studio.md §Process model)."
        )


def test_html_serve_studio_routes_is_optional() -> None:
    """``run_html_server`` accepts ``studio_routes_factory=None`` and the
    Dashboards path doesn't touch the L2 cache.

    Static check: when factory is None, the only ``L2InstanceCache``
    reference in the source is guarded by ``studio_routes_factory is
    not None``. Pins the severability seam in the shared helper.
    """
    from quicksight_gen.cli import _html_serve as helper
    source = _read_module_source(helper)
    # The cache module appears (the factory contract is typed against
    # it), but its only construction site is gated on the factory.
    assert "L2InstanceCache.from_path(l2_instance_path)" in source
    # The construction line is preceded by the factory-not-None guard.
    cache_line_idx = source.index("L2InstanceCache.from_path(l2_instance_path)")
    preceding = source[:cache_line_idx]
    # The most-recent ``if studio_routes_factory is not None:`` must be
    # within the preceding scope (a few lines back).
    last_guard = preceding.rfind("if studio_routes_factory is not None:")
    assert last_guard != -1
    between = preceding[last_guard:].count("\n")
    assert between < 10, (
        "L2InstanceCache construction drifted out of the "
        "studio_routes_factory guard — severability seam broken."
    )
