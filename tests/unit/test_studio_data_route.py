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
from datetime import date, timedelta
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


# ---------------------------------------------------------------------------
# X.4.h.3 — end_date day-stepper widget + PUT route
# ---------------------------------------------------------------------------


def test_end_date_strip_renders_blank_input_when_none(
    writable_l2_yaml: Path,
) -> None:
    """Default cfg.test_generator.end_date = None ⇒ the date input
    renders empty (blank value) and the trailing current-value chip
    shows '(default)'."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(end_date=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'class="end-date-input"' in body
    assert 'value=""' in body  # blank value
    assert "(default)" in body  # current-value chip


def test_end_date_strip_reflects_cached_value(
    writable_l2_yaml: Path,
) -> None:
    """When the cache holds a date, the input + chip render that ISO
    string."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'value="2026-05-14"' in body
    # The trailing chip shows the same ISO so the operator sees the
    # current state without depending on the date-input's UA styling.
    assert ">2026-05-14<" in body


def test_end_date_strip_form_targets_put_route(
    writable_l2_yaml: Path,
) -> None:
    """Each control independently PUTs to /data/knobs/end_date with
    outerHTML swap. Missing any one breaks the round-trip."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Three buttons + one input all carry the PUT URL.
    assert body.count('hx-put="/data/knobs/end_date"') == 4
    assert 'hx-target="#data-knob-end-date"' in body
    assert 'hx-swap="outerHTML"' in body
    # Prev / next buttons send delta payload via hx-vals (single-quoted
    # attribute → literal " chars inside JSON, no HTML-escape).
    assert 'hx-vals=\'{"delta": "-1"}\'' in body
    assert 'hx-vals=\'{"delta": "1"}\'' in body
    # "today" button sends an empty end_date to reset.
    assert 'hx-vals=\'{"end_date": ""}\'' in body
    # Date input commits on change.
    assert 'hx-trigger="change"' in body


def test_put_end_date_absolute_date_set(
    writable_l2_yaml: Path,
) -> None:
    """PUT /data/knobs/end_date with end_date=ISO commits the absolute
    date to the cache and re-renders the strip with the new value."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(end_date=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/end_date",
            [("end_date", "2026-06-01")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().end_date == date(2026, 6, 1)
    assert 'value="2026-06-01"' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_end_date_empty_string_clears_to_none(
    writable_l2_yaml: Path,
) -> None:
    """end_date= (empty) is the canonical "today reset" payload — the
    cache clears to None and the widget re-renders with a blank input."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/end_date", [("end_date", "")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().end_date is None
    assert 'value=""' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_end_date_delta_steps_from_cached_value(
    writable_l2_yaml: Path,
) -> None:
    """delta=1 with a cached date applies +1 day, delta=-1 applies -1.
    The cache's stored date is the anchor, not today's date."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # +1 day
        _put_form(c, "/data/knobs/end_date", [("delta", "1")])
        assert tg_cache.get().end_date == date(2026, 5, 15)
        # -1 day from new state
        _put_form(c, "/data/knobs/end_date", [("delta", "-1")])
        assert tg_cache.get().end_date == date(2026, 5, 14)


def test_put_end_date_delta_anchors_on_today_when_cache_none(
    writable_l2_yaml: Path,
) -> None:
    """delta from a None cache anchors on the system today's date so
    the operator can step from "today's data" without first picking a
    starting date. No determinism guarantee here — the trainer mode
    is a UI surface, not a hash-locked seed path."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(end_date=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        _put_form(c, "/data/knobs/end_date", [("delta", "1")])
    new = tg_cache.get().end_date
    assert new is not None
    today = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode test must compare against the same wall-clock anchor the route uses
    # Tomorrow — modulo a hypothetical midnight crossing during the test.
    assert new in {today + timedelta(days=1), today + timedelta(days=2)}


def test_put_end_date_invalid_iso_silently_drops(
    writable_l2_yaml: Path,
) -> None:
    """Garbage in the end_date field is silently dropped (the cache
    holds its prior value) — same posture as put_plants: a curl test
    or stale browser shouldn't be able to corrupt cache state."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/end_date",
            [("end_date", "not-a-date")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().end_date == date(2026, 5, 14)


def test_put_end_date_route_absent_without_cache(
    writable_l2_yaml: Path,
) -> None:
    """Without a tg_cache the mutation route is NOT mounted — same
    severability rule as plants."""
    app = _build_app(writable_l2_yaml, tg_cache=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/end_date", [("end_date", "2026-05-14")],
        )
    assert resp.status_code in (404, 405)  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_end_date_delta_wins_over_end_date(
    writable_l2_yaml: Path,
) -> None:
    """When both delta and end_date are sent, delta wins. The UI never
    sends both, but a hand-rolled curl might — making the priority
    explicit avoids ambiguous behavior."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        _put_form(
            c, "/data/knobs/end_date",
            [("delta", "1"), ("end_date", "2030-01-01")],
        )
    # delta wins → +1 day from cached anchor
    assert tg_cache.get().end_date == date(2026, 5, 15)
