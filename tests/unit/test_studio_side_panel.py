"""BTa.1 — `common/html/_studio_side_panel.py` unit tests.

Pins:
- GLOSSARY shape (one dict, every term has a non-empty markdown body)
- Render helpers produce the expected drawer + trigger HTML shape
- Route handlers return the right HTML for full / per-term / unknown
- The top-nav `[?]` trigger lands in `emit_top_nav` output
- The drawer container lands in `emit_top_nav` output (single instance
  per page)
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
from recon_gen.common.html._studio_side_panel import (
    GLOSSARY,
    render_side_panel_drawer_container,
    render_side_panel_trigger,
)
from recon_gen.common.html.render import (
    build_top_nav_entries,
    emit_top_nav,
)
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


# -- GLOSSARY shape ---------------------------------------------------------


def test_glossary_keys_are_lowercase_slugs() -> None:
    """Each key uses lowercase letters + hyphens only — the per-term
    route's path param normalizes via `.lower()`, and the display name
    is generated via `key.replace('-', ' ').title()`."""
    for key in GLOSSARY:
        assert key == key.lower()
        assert " " not in key
        # `l2` carries a digit; allow alphanumeric + hyphen.
        assert key.replace("-", "").isalnum(), key


def test_glossary_bodies_are_non_empty_markdown() -> None:
    """Every term has a non-empty body; markdown bold (`**...**`) is
    present in at least most entries (the display convention for the
    term-name lead)."""
    bold_count = 0
    for body in GLOSSARY.values():
        assert body.strip(), "empty glossary body"
        assert len(body) > 50, f"glossary body too short: {body[:30]}..."
        if "**" in body:
            bold_count += 1
    # Most entries lead with the term name in bold.
    assert bold_count >= len(GLOSSARY) - 2


def test_glossary_includes_load_bearing_l2_terms() -> None:
    """The cold-read flagged these as the highest-friction vocabulary
    items. Pin to prevent silent removal."""
    for must_have in ("l2", "rail", "transfer-template", "chain", "limit-schedule"):
        assert must_have in GLOSSARY, f"missing must-have term {must_have!r}"


# -- Render helpers --------------------------------------------------------


def test_drawer_container_has_aria_complementary_and_close_button() -> None:
    """Per BTa.0 Lock 1 — ARIA role=complementary + close affordance."""
    html = render_side_panel_drawer_container()
    assert 'role="complementary"' in html
    assert 'data-side-panel-close' in html
    assert 'aria-label="Close help panel"' in html
    assert 'id="side-panel"' in html
    assert 'id="side-panel-body"' in html
    # Slide-in transition (translate-x-full hidden by default).
    assert 'translate-x-full' in html
    # Click-outside overlay.
    assert 'data-side-panel-overlay' in html


def test_drawer_container_includes_escape_key_handler() -> None:
    """Escape closes the drawer (operator's expectation for any
    modal-ish surface)."""
    html = render_side_panel_drawer_container()
    assert "key === 'Escape'" in html


def test_side_panel_trigger_renders_button_with_hx_get() -> None:
    """Triggers POST nothing; they hx-get a fragment into the drawer
    body. data-side-panel-trigger tells the panel JS to slide open."""
    html = render_side_panel_trigger(
        "/studio/side-panel/glossary/rail",
        label="?",
        aria_label="What is a rail?",
    )
    assert 'data-side-panel-trigger' in html
    assert 'hx-get="/studio/side-panel/glossary/rail"' in html
    assert 'hx-target="#side-panel-body"' in html
    assert 'aria-label="What is a rail?"' in html
    assert '>?<' in html


# -- Top-nav integration --------------------------------------------------


def test_top_nav_emits_glossary_trigger_button() -> None:
    """BTa.1 — every page rendering the top-nav gets the `[?]` button
    + the drawer container."""
    entries = build_top_nav_entries(
        dashboards=[("smoke", "Smoke")],
        docs_url=None,
        studio_enabled=True,
    )
    html = emit_top_nav(entries=entries, active_href="/")
    # Glossary trigger lands in the nav.
    assert 'hx-get="/studio/side-panel/glossary"' in html
    assert 'data-side-panel-trigger' in html
    assert 'aria-label="Open glossary side panel"' in html
    # Drawer chrome lands once (after the nav).
    assert 'id="side-panel"' in html
    assert html.count('id="side-panel-body"') == 1


def test_top_nav_drawer_container_only_renders_once_per_call() -> None:
    """Pin against accidental double-injection (sliding two drawers
    open at once would target the same `#side-panel-body` and
    break)."""
    entries = build_top_nav_entries(
        dashboards=[("smoke", "Smoke")], docs_url=None, studio_enabled=True,
    )
    html = emit_top_nav(entries=entries)
    assert html.count('id="side-panel"') == 1
    assert html.count('data-side-panel-overlay') == 1


def test_top_nav_empty_entries_skips_nav_and_drawer() -> None:
    """No nav entries = no drawer either (the single-surface deploy
    contract; caller filters)."""
    assert emit_top_nav(entries=[]) == ""


# -- Route handlers --------------------------------------------------------


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


def test_glossary_full_route_returns_dl_with_every_term(
    writable_l2_yaml: Path,
) -> None:
    """GET /studio/side-panel/glossary returns the full glossary as
    a definition list. Every GLOSSARY key surfaces as a <dt>."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/glossary")
        assert resp.status_code == 200
        body = resp.text
    assert body.startswith("<dl")
    # Every term renders (Title-Case display name in a <dt>).
    for key in GLOSSARY:
        display = key.replace("-", " ").title()
        assert f">{display}</dt>" in body, f"missing term {display!r}"


def test_glossary_term_route_returns_single_term(
    writable_l2_yaml: Path,
) -> None:
    """GET /studio/side-panel/glossary/<term> returns just that
    term's definition."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/glossary/rail")
        assert resp.status_code == 200
        body = resp.text
    assert "Rail" in body
    # Markdown bold renders to <strong>.
    assert "<strong>Rail</strong>" in body


def test_glossary_unknown_term_returns_404_with_helpful_text(
    writable_l2_yaml: Path,
) -> None:
    """Unknown term → 404 + a pointer to the full glossary."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/glossary/not-a-real-term")
        assert resp.status_code == 404
        body = resp.text
    assert "not-a-real-term" in body
    assert "Help" in body or "glossary" in body


def test_glossary_term_route_is_case_insensitive(
    writable_l2_yaml: Path,
) -> None:
    """The route normalizes the path param to lowercase so
    `/RAIL` and `/rail` resolve to the same term."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body_lower = c.get("/studio/side-panel/glossary/rail").text
        body_upper = c.get("/studio/side-panel/glossary/RAIL").text
    # Same content (display name normalization is uniform).
    assert body_lower == body_upper
