"""Studio data-shaping panel route tests (X.4.h.1).

Locks the contract for the new ``/data`` mode shell:

- ``GET /data`` returns 200 + a page that carries the three landmark
  elements the trainer mode is built around (knob strip, timeline
  column, training column). Knob widgets land in h.2-h.5; this test
  just guarantees the page-shell selectors are stable for that wiring
  to bind to.
- The home + diagram chrome pick up a ``→ data`` nav link so the new
  mode is discoverable from every existing studio page.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from quicksight_gen.common.config import TestGeneratorConfig
from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.tg_cache import TestGeneratorCache
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(
    yaml_path: Path,
    *,
    tg_cache: TestGeneratorCache | None = None,
) -> object:
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
        studio_routes=make_studio_routes(cache, tg_cache=tg_cache),
    )


def test_data_route_returns_200_with_landmarks(
    writable_l2_yaml: Path,
) -> None:
    """GET /data renders the trainer-mode page-shell with chrome bar,
    knob strip, timeline column, and training column landmarks."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/data")
        assert resp.status_code == 200
        body = resp.text

    # Three landmark elements h.2-h.9 will bind to.
    assert 'id="data-knobs"' in body, "knob-strip placeholder missing"
    assert 'id="data-timeline"' in body, "timeline column missing"
    assert 'id="data-training"' in body, "training column missing"
    # Aria labels matter for the screen-reader landmark map (and give
    # the Playwright e2e in h.8.c stable role-based selectors).
    assert 'aria-label="Plant timeline"' in body
    assert 'aria-label="Training pane"' in body


def test_data_route_carries_deploy_button(
    writable_l2_yaml: Path,
) -> None:
    """The trainer page exposes the same Deploy button the home page
    does, so the operator can re-deploy without bouncing back to /."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'id="deploy-btn"' in body
    assert 'id="deploy-status"' in body
    assert 'function quicksightDeploy()' in body


def test_data_route_carries_back_to_landing_link(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert '<a class="nav-link" href="/">← landing</a>' in body
    assert '<a class="nav-link" href="/diagram">→ diagram</a>' in body


def test_home_chrome_links_to_data(writable_l2_yaml: Path) -> None:
    """X.4.h.1.b — landing page chrome carries a `→ data` link."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    assert '<a class="nav-link" href="/data">→ data</a>' in body


def test_diagram_chrome_links_to_data(writable_l2_yaml: Path) -> None:
    """X.4.h.1.b — diagram page chrome carries a `→ data` link."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/diagram").text

    assert '<a class="nav-link" href="/data">→ data</a>' in body


def test_diagram_chrome_omits_data_link_in_embed_mode(
    writable_l2_yaml: Path,
) -> None:
    """The diagram is iframed inside the home page in embed mode; the
    embed strips the studio-header so the page doesn't carry two nav
    bars. The data link rides on that header so it should be absent
    in embed mode too."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/diagram?embed=1").text

    # Whole studio-header is omitted in embed mode (existing X.4.f.8
    # behavior); just sanity-check the data link doesn't sneak through.
    assert 'href="/data"' not in body


# ---------------------------------------------------------------------------
# X.4.h.2 — plant-toggle widget + PUT route
# ---------------------------------------------------------------------------


def test_plants_strip_renders_six_checkboxes_all_checked_by_default(
    writable_l2_yaml: Path,
) -> None:
    """Empty cfg.test_generator.plants ⇒ "all kinds" per SPEC; every
    checkbox renders pre-checked. Verifies the SPEC default round-trips
    through the cache + renderer."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(plants=()))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    for kind in (
        "drift", "overdraft", "limit_breach",
        "stuck_pending", "stuck_unbundled", "supersession",
    ):
        # Each plant kind has its own checkbox.
        assert f'name="plant" value="{kind}"' in body, (
            f"missing checkbox for {kind}"
        )
    # All six render pre-checked when plants tuple is empty.
    assert body.count('name="plant"') == 6
    assert body.count(" checked /") == 6


def test_plants_strip_reflects_cache_subset(
    writable_l2_yaml: Path,
) -> None:
    """When the cache holds a non-empty subset, only those checkboxes
    render checked; the others render unchecked."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(plants=("drift", "supersession")),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Two checked, four unchecked.
    assert body.count(" checked /") == 2
    # Verify by string proximity (the value attr appears in the same tag).
    assert 'value="drift" checked' in body
    assert 'value="supersession" checked' in body
    # Unchecked kinds: just no `checked` between value="X" and the closing.
    for kind in ("overdraft", "limit_breach", "stuck_pending", "stuck_unbundled"):
        assert f'value="{kind}" />' in body, (
            f"{kind} should render unchecked when not in subset"
        )


def test_plants_strip_form_targets_put_route(
    writable_l2_yaml: Path,
) -> None:
    """The form's hx-put + change trigger + outerHTML swap is the
    interaction contract; missing any one breaks the round-trip."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'hx-put="/data/knobs/plants"' in body
    assert 'hx-trigger="change"' in body
    assert 'hx-target="#data-knob-plants"' in body
    assert 'hx-swap="outerHTML"' in body


def _put_form(c: TestClient, url: str, fields: list[tuple[str, str]]) -> "object":  # type: ignore[no-untyped-def]: TestClient stub return is Any
    """Send a PUT with explicit application/x-www-form-urlencoded body.

    httpx ``put(..., data=[(k,v),...])`` treats list-of-tuples as raw
    bytes content rather than form-encoding it, so the explicit content
    + Content-Type header is the route around that. Used everywhere
    the test needs to round-trip multiple checkbox values."""
    body = "&".join(f"{k}={v}" for k, v in fields)
    return c.put(
        url,
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def test_put_plants_replaces_cache_state(
    writable_l2_yaml: Path,
) -> None:
    """PUT /data/knobs/plants with a checkbox payload mutates the
    cache and returns the freshly-rendered strip."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(plants=()))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Submit a subset (the form serializes only checked boxes).
        resp = _put_form(
            c, "/data/knobs/plants",
            [("plant", "drift"), ("plant", "limit_breach")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    # Cache reflects the new selection.
    assert tg_cache.get().plants == ("drift", "limit_breach")
    # Response is the re-rendered strip with the new selection checked.
    body = resp.text  # type: ignore[attr-defined]: TestClient stub return is Any
    assert 'value="drift" checked' in body
    assert 'value="limit_breach" checked' in body
    # The other kinds rendered unchecked.
    assert 'value="overdraft" />' in body


def test_put_plants_empty_payload_clears_to_all(
    writable_l2_yaml: Path,
) -> None:
    """An empty form (no checked boxes) sets plants to () = 'all kinds'
    per the SPEC short-circuit. The strip re-renders with everything
    pre-checked again."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(plants=("drift",)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/plants", [])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().plants == ()
    # All six checkboxes pre-checked because tuple is empty.
    assert resp.text.count(" checked /") == 6  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_plants_drops_unknown_kinds(
    writable_l2_yaml: Path,
) -> None:
    """A curl test or stale browser shouldn't be able to inject junk
    into the cache — unknown plant names silently drop."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(plants=()))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/plants",
            [("plant", "drift"), ("plant", "made_up_kind")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().plants == ("drift",)


def test_put_plants_route_absent_without_cache(
    writable_l2_yaml: Path,
) -> None:
    """Without a tg_cache, the mutation route is NOT mounted — there's
    nothing to mutate, so a PUT must surface as 405 / 404 rather than
    a silent no-op that the operator might trust."""
    app = _build_app(writable_l2_yaml, tg_cache=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/plants", [("plant", "drift")],
        )
    assert resp.status_code in (404, 405)  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_plants_preserves_kind_order(
    writable_l2_yaml: Path,
) -> None:
    """The cache always stores plants in canonical _PLANT_LABELS order
    regardless of form-submission order — keeps the resulting tuple
    stable for hash-locked-seed comparison."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(plants=()))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Submit out-of-order.
        _put_form(
            c, "/data/knobs/plants",
            [
                ("plant", "supersession"),
                ("plant", "drift"),
                ("plant", "limit_breach"),
            ],
        )
    # Canonical order: drift / limit_breach / supersession.
    assert tg_cache.get().plants == ("drift", "limit_breach", "supersession")
