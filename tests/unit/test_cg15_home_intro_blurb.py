"""CG.15 — home-page intro blurb guides first interactions instead
of reassuring the 5th-session operator about save semantics.

Cold-read v3 P2: "Saves cascade across sections automatically" is
reassurance for a returning operator; a first-session operator's
salient question is "where do I start?". Rewrite to point at the
mechanics — search, expand, diagram link.

Anti-drift pins so a future re-rewrite can't quietly slip back to
reassurance copy.
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


REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "l2"


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


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _intro_text(body: str) -> str:
    """Slice the <p>...</p> body inside the home-intro header."""
    header_start = body.index('id="home-intro"')
    p_start = body.index("<p", header_start)
    p_open_end = body.index(">", p_start) + 1
    p_end = body.index("</p>", p_open_end)
    return body[p_open_end:p_end]


def test_intro_no_longer_promises_save_cascade(
    writable_l2_yaml: Path,
) -> None:
    """The "saves cascade across sections automatically" line was
    reassurance for returning operators — useless to a first-time
    visitor. Pin its absence."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    intro = _intro_text(body)
    assert "saves cascade" not in intro.lower()
    assert "automatically" not in intro.lower()


def test_intro_guides_first_interactions(
    writable_l2_yaml: Path,
) -> None:
    """First-session operator's question is "where do I start?".
    The intro should point at the visible mechanics — expand a
    section, search, jump to the diagram. Pin those signals."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    intro = _intro_text(body)
    # "Expand" — points at the <details> mechanic
    assert "expand" in intro.lower()
    # "Search" — points at the in-summary input
    assert "search" in intro.lower()
    # "Diagram" — points at the cross-surface jump
    assert "diagram" in intro.lower()


def test_intro_describes_kinds_as_building_blocks(
    writable_l2_yaml: Path,
) -> None:
    """The intro frames the sections as "building blocks" of the
    L2 shape rather than a flat list of nouns to memorize."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    intro = _intro_text(body)
    assert "building block" in intro.lower()
