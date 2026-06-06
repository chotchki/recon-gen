"""CG.20 — `/l2_shape/<unknown-kind>/` and `/l2_shape/persona/`
404s render the studio chrome (top-nav + trainer-style header
strip) instead of a bare `<h1>404</h1><p>...</p>` dead-end.

Cold-read v4 P1 #3: persona was dropped from the home page (CF.4
cleanup) but `/l2_shape/persona/` still resolved to a bare h1/p
with no top-nav, no anchor back. Bookmarks / browser history / old
runbook links land here stranded.
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


# ---------------------------------------------------------------------------
# Persona — the real cold-read scenario
# ---------------------------------------------------------------------------

def test_persona_404_returns_studio_chrome(
    writable_l2_yaml: Path,
) -> None:
    """`/l2_shape/persona/` returns 404 with studio chrome: top-nav,
    trainer-style header, h1, a recoverable link back to the L2
    editor. Pre-CG.20 this was a bare `<h1>404</h1><p>persona is
    not an editable entity kind (yet).</p>`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/persona/")
    assert resp.status_code == 404
    body = resp.text
    # Studio chrome: top-nav + the trainer-style header
    assert "L2 Editor" in body  # top-nav link text
    # Recoverable anchors
    assert 'href="/"' in body
    assert 'href="/diagram"' in body
    # h1 reads "Page not found", not the cold-read's "<h1>404</h1>"
    assert "Page not found" in body
    # The bare-message phrasing from before should not be present.
    assert "is not an editable entity kind (yet)." not in body


def test_persona_404_carries_kind_in_message(
    writable_l2_yaml: Path,
) -> None:
    """The page tells the operator WHICH path they hit — useful
    when an HR-vintage shortcut on the corporate intranet still
    points at the old URL."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/persona/").text
    assert "persona" in body


# ---------------------------------------------------------------------------
# Truly invalid kind (URL slug not in EntityKind universe)
# ---------------------------------------------------------------------------

def test_invalid_kind_slug_returns_studio_chrome(
    writable_l2_yaml: Path,
) -> None:
    """A made-up URL slug like `/l2_shape/foobar/` (not in
    EntityKind at all) also gets the studio chrome instead of the
    bare 404 page."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/foobar/")
    assert resp.status_code == 404
    body = resp.text
    assert "L2 Editor" in body
    assert "Page not found" in body
    assert "foobar" in body
    assert "is not an editable entity kind (yet)." not in body


def test_invalid_kind_slug_xss_escaped(
    writable_l2_yaml: Path,
) -> None:
    """The raw URL slug is rendered as a `<code>` so an attacker-
    constructed path with `<script>` doesn't escape. Pin the escape
    behavior."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/%3Cscript%3E/").text
    assert "<script>" not in body  # would have escaped
    assert "&lt;script&gt;" in body


# ---------------------------------------------------------------------------
# Singleton kinds still render correctly (regression guard)
# ---------------------------------------------------------------------------

def test_theme_singleton_still_renders_singleton_page(
    writable_l2_yaml: Path,
) -> None:
    """Theme + instance are SINGLETON_KINDS — `/l2_shape/theme/`
    must still render the singleton edit page, NOT the unknown-kind
    404 chrome. The unknown-kind branch should only fire when the
    URL slug genuinely doesn't resolve to an editable kind."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/theme/")
    assert resp.status_code == 200
    body = resp.text
    # Theme singleton page reads "Theme" prominently in the form
    # header, not "Page not found".
    assert "Page not found" not in body


def test_valid_list_kind_still_renders_list_page(
    writable_l2_yaml: Path,
) -> None:
    """Regression guard — refactoring `list_view` to surface the
    chrome page on the 404 paths shouldn't break the happy path."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/")
    assert resp.status_code == 200
    assert "Page not found" not in resp.text
