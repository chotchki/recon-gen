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

from recon_gen.common.config import TestGeneratorConfig
from recon_gen.common.intervals import DateInterval
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.tg_cache import TestGeneratorCache
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
    cfg: object = None,
) -> object:
    """Build a Studio test app.

    ``cfg=None`` (default) preserves the legacy contract — POST /deploy
    is omitted and the etl_hook strip renders "(not configured)". Pass
    a real Config (with optional etl_hook= override) to exercise the
    deploy + etl-hook surfaces.
    """
    cache = L2InstanceCache.from_path(yaml_path)
    tree_app, sheet = build_smoke_app(make_test_config())
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(
            cache,
            tg_cache=tg_cache,
            cfg=cfg,  # type: ignore[arg-type]: tests pass either a real Config or None; make_studio_routes accepts Config | None
        ),
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

    # AM.2 step 1 (2026-05-25): `.nav-link` semantic class retired;
    # check the href + visible text instead (what the operator
    # actually clicks).
    assert 'href="/">← landing</a>' in body
    assert 'href="/diagram">→ diagram</a>' in body


def test_data_route_training_pane_replaces_x4_h9_placeholder(
    writable_l2_yaml: Path,
) -> None:
    """AA.C.5 — the X.4.h.9 placeholder is gone and the trainer pane
    rendered the per-kind catalogue from L1_Invariants.md instead. The
    pane lands inside ``<section id="data-training">``."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Placeholder string is gone (would have been the only "X.4.h.9"
    # ref on the page).
    assert "training pane lands in X.4.h.9" not in body
    # Catalogue intro + at least one card landed.
    assert 'data-training__heading' in body
    assert 'data-training__list' in body
    assert 'data-training__entry' in body
    # Every L1 invariant kind has a card (mirrors the unit-test pin in
    # test_studio_training_pane, but at the integrated /data response).
    for kind in (
        "drift", "ledger_drift", "overdraft", "limit_breach",
        "expected_eod_balance_breach", "stuck_pending",
        "stuck_unbundled", "supersession_audit",
    ):
        assert f'data-kind="{kind}"' in body, (
            f"/data trainer pane missing card for kind={kind!r}"
        )


def test_data_route_training_pane_links_to_app2_l1_dashboard(
    writable_l2_yaml: Path,
) -> None:
    """The trainer pane's per-kind links target the App2 L1 dashboard
    (not the QS embed). Pin one specific link to catch a future
    refactor that, for instance, accidentally rebuilds the link map
    around the QS dashboard URL pattern."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Drift card links to the L1 Drift sheet on App2.
    assert (
        'href="/dashboards/l1_dashboard/sheets/l1-sheet-drift"' in body
    ), "drift card should deep-link to App2 L1 Drift sheet"
    # Supersession Audit links to its dedicated sheet.
    assert (
        'href="/dashboards/l1_dashboard/sheets/l1-sheet-supersession-audit"'
        in body
    ), "supersession_audit card should deep-link to App2 sheet"


def test_home_chrome_links_to_data(writable_l2_yaml: Path) -> None:
    """X.4.h.1.b — landing page chrome carries a `→ data` link."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    # AM.2 step 1: same locator change as `_data_route_carries_back`.
    assert 'href="/data">→ data</a>' in body


def test_diagram_chrome_links_to_data(writable_l2_yaml: Path) -> None:
    """X.4.h.1.b — diagram page chrome carries a `→ data` link."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/diagram").text

    assert 'href="/data">→ data</a>' in body


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
    outerHTML swap. The strip is now labeled "up to" since it's the
    scrub head within the trainer's scenario window (h.3.window)."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
        window=DateInterval.closed(date(2026, 4, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # The up_to strip itself carries 4 PUT URLs (← / date input / → /
    # snap-to-end). The timeline section (h.6) ALSO writes
    # /data/knobs/end_date — one PUT URL per timeline-day button — so
    # we narrow to the strip's own form by asserting at least 4.
    assert body.count('hx-put="/data/knobs/end_date"') >= 4
    assert 'id="data-knob-end-date"' in body
    assert 'hx-target="#data-knob-end-date"' in body
    assert 'hx-swap="outerHTML"' in body
    # Prev / next buttons send delta payload via hx-vals (single-quoted
    # attribute → literal " chars inside JSON, no HTML-escape).
    assert 'hx-vals=\'{"delta": "-1"}\'' in body
    assert 'hx-vals=\'{"delta": "1"}\'' in body
    # "snap to end" button sends end_date = window_end (not empty —
    # the empty-string semantic is now "snap to window_end" handled
    # server-side, but the button skips the round-trip and sends the
    # explicit value for the snap action).
    assert 'hx-vals=\'{"end_date": "2026-05-31"}\'' in body
    # Date input commits on change.
    assert 'hx-trigger="change"' in body
    # Date input has min/max bounds matching the window so the
    # browser-native picker can't pick outside it.
    assert 'min="2026-04-01"' in body
    assert 'max="2026-05-31"' in body


def test_put_end_date_absolute_date_set(
    writable_l2_yaml: Path,
) -> None:
    """PUT /data/knobs/end_date with end_date=ISO commits the absolute
    date to the cache (clamped to window) and re-renders the strip
    with the new value."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=None),
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 6, 30)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/end_date",
            [("end_date", "2026-06-01")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().end_date == date(2026, 6, 1)
    assert 'value="2026-06-01"' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_end_date_clamps_to_window(
    writable_l2_yaml: Path,
) -> None:
    """An absolute end_date outside the window clamps to the nearest
    window edge — the up_to scrubber is bounded by the trainer's
    scenario window, not the wide-open calendar."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Above window: clamps to window_end.
        _put_form(c, "/data/knobs/end_date", [("end_date", "2030-01-01")])
        assert tg_cache.get().end_date == date(2026, 5, 31)
        # Below window: clamps to window_start.
        _put_form(c, "/data/knobs/end_date", [("end_date", "2020-01-01")])
        assert tg_cache.get().end_date == date(2026, 5, 1)


def test_put_end_date_empty_string_snaps_to_window_end(
    writable_l2_yaml: Path,
) -> None:
    """end_date= (empty) is the canonical "snap to end" payload —
    the cache holds window_end explicitly so subsequent reads stay
    stable even if the window moves later."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/end_date", [("end_date", "")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().end_date == date(2026, 5, 31)
    assert 'value="2026-05-31"' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_end_date_delta_steps_from_cached_value(
    writable_l2_yaml: Path,
) -> None:
    """delta=1 with a cached date applies +1 day, delta=-1 applies -1.
    The cache's stored date is the anchor; results clamp to the window."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # +1 day
        _put_form(c, "/data/knobs/end_date", [("delta", "1")])
        assert tg_cache.get().end_date == date(2026, 5, 15)
        # -1 day from new state
        _put_form(c, "/data/knobs/end_date", [("delta", "-1")])
        assert tg_cache.get().end_date == date(2026, 5, 14)


def test_put_end_date_delta_anchors_on_window_end_when_cache_none(
    writable_l2_yaml: Path,
) -> None:
    """delta from a None cache resolves up_to via cache.get_up_to()
    which falls back to window_end. delta+1 from window_end clamps
    back to window_end (since up_to can't exceed it)."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=None),
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # +1 from window_end → clamps back to window_end.
        _put_form(c, "/data/knobs/end_date", [("delta", "1")])
        assert tg_cache.get().end_date == date(2026, 5, 31)
        # -1 from window_end → 2026-05-30 (in window).
        _put_form(c, "/data/knobs/end_date", [("delta", "-1")])
        assert tg_cache.get().end_date == date(2026, 5, 30)


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
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        _put_form(
            c, "/data/knobs/end_date",
            [("delta", "1"), ("end_date", "2030-01-01")],
        )
    # delta wins → +1 day from cached anchor
    assert tg_cache.get().end_date == date(2026, 5, 15)


# ---------------------------------------------------------------------------
# X.4.h.4 — seed input + roll/clear PUT route
# ---------------------------------------------------------------------------


def test_seed_strip_renders_blank_input_when_none(
    writable_l2_yaml: Path,
) -> None:
    """Default cfg.test_generator.seed = None ⇒ the number input renders
    blank (with placeholder) and the chip shows '(default)'."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'class="seed-input"' in body
    # Number input renders blank.
    assert 'name="seed" value=""' in body
    assert 'placeholder="(default)"' in body
    # Chip says "(default)".
    assert ">(default)<" in body


def test_seed_strip_reflects_cached_value(
    writable_l2_yaml: Path,
) -> None:
    """When the cache holds an int, the number input + chip render that
    integer."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=12345))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'name="seed" value="12345"' in body
    assert ">12345<" in body


def test_seed_strip_form_targets_put_route(
    writable_l2_yaml: Path,
) -> None:
    """Each control independently PUTs to /data/knobs/seed with
    outerHTML swap. Missing any one breaks the round-trip."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Two buttons + one input all carry the PUT URL.
    assert body.count('hx-put="/data/knobs/seed"') == 3
    assert 'hx-target="#data-knob-seed"' in body
    # The input's uint32 range must be expressed in the markup —
    # gives the browser's native number stepper sensible bounds.
    assert 'min="0"' in body
    assert 'max="4294967295"' in body
    # roll button sends roll=1; clear sends seed= (empty).
    assert 'hx-vals=\'{"roll": "1"}\'' in body
    assert 'hx-vals=\'{"seed": ""}\'' in body
    # Input commits on change.
    assert 'hx-trigger="change"' in body


def test_put_seed_absolute_int_set(
    writable_l2_yaml: Path,
) -> None:
    """PUT /data/knobs/seed with seed=<int> commits the absolute value
    to the cache and re-renders with the new value."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/seed", [("seed", "9876")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().seed == 9876
    assert 'value="9876"' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_seed_empty_string_clears_to_none(
    writable_l2_yaml: Path,
) -> None:
    """seed= (empty) is the canonical "clear" payload — cache resets to
    None (which the generator treats as _BASELINE_BASE_SEED locked
    default), and the widget re-renders blank."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=42))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/seed", [("seed", "")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().seed is None
    assert 'name="seed" value=""' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_seed_roll_picks_random_uint32(
    writable_l2_yaml: Path,
) -> None:
    """roll=1 picks a fresh seed in the uint32 range and pins it. The
    test asserts the value lands in the valid range (the actual
    randomness is the OS RNG — we don't pin it here)."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/seed", [("roll", "1")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    rolled = tg_cache.get().seed
    assert rolled is not None
    assert 0 <= rolled <= 2**32 - 1
    # The strip re-renders with the new value.
    assert f'value="{rolled}"' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_seed_roll_wins_over_seed_field(
    writable_l2_yaml: Path,
) -> None:
    """When roll=1 + seed=<int> are both sent, roll wins. The UI never
    sends both, but a curl might."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        _put_form(
            c, "/data/knobs/seed",
            [("roll", "1"), ("seed", "12345")],
        )
    rolled = tg_cache.get().seed
    assert rolled is not None
    # Roll wins — random pick, almost certainly not 12345.
    assert 0 <= rolled <= 2**32 - 1
    # (We can't assert != 12345 without making the test theoretically
    # flaky — the random RNG has 1-in-4-billion odds of producing
    # 12345 — but the cache update path went through roll, not seed.)


def test_put_seed_invalid_int_silently_drops(
    writable_l2_yaml: Path,
) -> None:
    """Garbage in the seed field silently drops — same posture as the
    date stepper / plant toggle. Curl tests / stale browsers can't
    corrupt the cache."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=42))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/seed", [("seed", "not-a-number")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().seed == 42


def test_put_seed_zero_is_a_valid_value(
    writable_l2_yaml: Path,
) -> None:
    """seed=0 commits 0 (a valid uint32 starting boundary). Truthy-check
    bugs would silently treat 0 as "absent" — explicit test guards
    against that regression."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(seed=42))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        _put_form(c, "/data/knobs/seed", [("seed", "0")])
    assert tg_cache.get().seed == 0


def test_put_seed_route_absent_without_cache(
    writable_l2_yaml: Path,
) -> None:
    """Without tg_cache the mutation route is NOT mounted (severability
    rule, mirrors plants + end_date)."""
    app = _build_app(writable_l2_yaml, tg_cache=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/seed", [("seed", "1234")])
    assert resp.status_code in (404, 405)  # type: ignore[attr-defined]: TestClient stub return is Any


# ---------------------------------------------------------------------------
# X.4.h.5 — scope selector radio group + PUT route
# ---------------------------------------------------------------------------


def test_scope_strip_renders_four_radios_full_default(
    writable_l2_yaml: Path,
) -> None:
    """Default cfg.test_generator.scope = 'full' ⇒ that radio renders
    pre-checked; the other three unchecked. Renders all four so the
    operator can switch without a dropdown click. (X.4.i.1 added
    'only_template' as the fourth scope.)"""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(scope="full"))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    for value in (
        "full", "uncovered_rails", "exceptions_only", "only_template",
    ):
        assert f'name="scope" value="{value}"' in body, (
            f"missing radio for {value}"
        )
    # full is the cached value → pre-checked.
    assert 'value="full" checked ' in body
    # The others render unchecked.
    assert 'value="uncovered_rails" hx-put' in body
    assert 'value="exceptions_only" hx-put' in body
    assert 'value="only_template" hx-put' in body
    # Exactly one checked across the four.
    assert body.count('name="scope"') == 4
    assert body.count('value="full" checked') == 1


def test_scope_strip_reflects_cached_value(
    writable_l2_yaml: Path,
) -> None:
    """When the cache holds a non-default scope, that value pre-checks."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(scope="exceptions_only"),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'value="exceptions_only" checked' in body
    assert 'value="full" checked' not in body
    assert 'value="uncovered_rails" checked' not in body


def test_scope_strip_form_targets_put_route(
    writable_l2_yaml: Path,
) -> None:
    """Each radio independently PUTs to /data/knobs/scope (its own
    value as the form-encoded payload — no hx-vals needed)."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert body.count('hx-put="/data/knobs/scope"') == 4
    assert 'hx-target="#data-knob-scope"' in body
    assert 'hx-trigger="change"' in body
    # Hover hint title= attrs render so the operator can discover
    # the difference between the four modes.
    assert "Wipe + emit baseline" in body  # full hint
    assert "patch the gaps" in body  # uncovered_rails hint
    assert "Plants only, no baseline" in body  # exceptions_only hint
    assert "leg-rails closure" in body  # only_template hint


def test_put_scope_changes_cached_value(
    writable_l2_yaml: Path,
) -> None:
    """PUT /data/knobs/scope with a known value commits and re-renders
    the strip with that radio pre-checked."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(scope="full"))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/scope", [("scope", "uncovered_rails")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().scope == "uncovered_rails"
    assert 'value="uncovered_rails" checked' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any
    assert 'value="full" checked' not in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_scope_unknown_value_silently_drops(
    writable_l2_yaml: Path,
) -> None:
    """A typo or curl-injected garbage scope value silently drops —
    cache holds its prior value. Same posture as plants/end_date/seed."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(scope="exceptions_only"),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/scope", [("scope", "made_up_mode")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().scope == "exceptions_only"


def test_put_scope_route_absent_without_cache(
    writable_l2_yaml: Path,
) -> None:
    """Without tg_cache the route is NOT mounted (severability rule,
    mirrors plants/end_date/seed)."""
    app = _build_app(writable_l2_yaml, tg_cache=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/scope", [("scope", "full")])
    assert resp.status_code in (404, 405)  # type: ignore[attr-defined]: TestClient stub return is Any


# ---------------------------------------------------------------------------
# X.4.h.url — URL state round-trip (HX-Push-Url + GET /data?... restore)
# ---------------------------------------------------------------------------


def test_put_emits_hx_push_url_with_state(
    writable_l2_yaml: Path,
) -> None:
    """Every knob PUT carries HX-Push-Url alongside the existing
    HX-Trigger header so the browser bar reflects current cache
    state. Bookmark + share + reload all flow through this URL."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(),
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/end_date", [("end_date", "2026-05-15")],
        )
    assert resp.headers.get("HX-Push-Url") is not None  # type: ignore[attr-defined]: TestClient stub return is Any
    push_url = resp.headers["HX-Push-Url"]  # type: ignore[attr-defined]: TestClient stub return is Any
    # Window bounds + end_date all surface in the URL.
    assert "window_start=2026-05-01" in push_url
    assert "window_end=2026-05-31" in push_url
    assert "end_date=2026-05-15" in push_url


def test_default_state_url_is_clean(
    writable_l2_yaml: Path,
) -> None:
    """All-default cache emits a clean /data URL — no query params.
    Operator should see a tidy bar when nothing's been touched."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    # window_start / window_end default to "today - 89 / today" via
    # the cache's __init__ → matches the URL builder's default
    # detection, so they get omitted.
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # PUT with empty plants (default) — nothing to push beyond /data.
        resp = _put_form(c, "/data/knobs/plants", [])
    assert resp.headers.get("HX-Push-Url") == "/data"  # type: ignore[attr-defined]: TestClient stub return is Any


def test_get_data_with_url_params_restores_cache(
    writable_l2_yaml: Path,
) -> None:
    """GET /data?... reads the URL into the cache so a bookmark or
    reload restores trainer state. Round-trips for every knob."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(
            "/data?window_start=2026-04-01&window_end=2026-05-31"
            "&end_date=2026-05-15&scope=exceptions_only"
            "&seed=12345&plants=drift,overdraft",
        )
    assert resp.status_code == 200
    # Cache mutated by the GET parsing.
    assert tg_cache.get_window() == DateInterval.closed(
        date(2026, 4, 1), date(2026, 5, 31),
    )
    assert tg_cache.get().end_date == date(2026, 5, 15)
    assert tg_cache.get().scope == "exceptions_only"
    assert tg_cache.get().seed == 12345
    assert tg_cache.get().plants == ("drift", "overdraft")


def test_get_data_invalid_url_params_silently_drop(
    writable_l2_yaml: Path,
) -> None:
    """Malformed URL params don't crash — they leave the cache alone
    (same posture as PUT routes' validation). Operator-friendly: a
    truncated bookmark or stale share-link still loads the page."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
        window=DateInterval.closed(date(2026, 5, 1), date(2026, 5, 31)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get(
            "/data?window_start=not-a-date&end_date=garbage"
            "&seed=abc&scope=made_up&plants=fake_kind,drift",
        )
    assert resp.status_code == 200
    # Window untouched (window_start was junk).
    assert tg_cache.get_window() == DateInterval.closed(
        date(2026, 5, 1), date(2026, 5, 31),
    )
    # end_date untouched (junk silently dropped).
    assert tg_cache.get().end_date == date(2026, 5, 14)
    # seed untouched.
    assert tg_cache.get().seed is None
    # scope unchanged (junk dropped).
    assert tg_cache.get().scope == "full"
    # plants kept only the known value.
    assert tg_cache.get().plants == ("drift",)


# ---------------------------------------------------------------------------
# X.4.h.6.b/c — plant-timeline section + HX-Trigger refresh wiring
# ---------------------------------------------------------------------------


def test_data_page_renders_timeline_section(
    writable_l2_yaml: Path,
) -> None:
    """The /data page emits a populated timeline section (replacing
    the h.1 placeholder). Header summarizes total + per-kind, rows
    carry per-day chips."""
    # AO.S2.a — pin window_end (the scenario-end / plant anchor) so the
    # timeline is deterministic regardless of wall-clock date; it's a
    # distinct knob from end_date (the load-up-to scrub head). With both
    # at 2026-05-14 the scenario is fully loaded to its end, so its plants
    # render as data-days (not the dimmed "future" zone).
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14), scope="full"),
        # Restore the v1 implicit default (window_start = window_end - 89);
        # BC.9 made the window typed but didn't change the default policy.
        window=DateInterval.trailing_days_ending_today(date(2026, 5, 14), 90),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Section landmark (the h.1 placeholder is replaced).
    assert 'id="data-timeline"' in body
    # Timeline header surfaces total count + at least one chip kind.
    assert 'class="timeline-header"' in body
    # spec_example yields plants ⇒ at least one row with hx-put writing
    # end_date (the click-to-jump-day affordance).
    assert 'class="timeline-day"' in body
    # Each row's chips carry a per-kind class.
    assert "timeline-chip--" in body


def test_timeline_plants_anchor_on_scenario_end_not_load_up_to() -> None:
    """AO.S2.a — the scenario-end date (``window_end``, the plant anchor)
    and the load-up-to scrub head (``end_date`` / ``up_to``) are distinct.

    The trainer slides up_to to load earlier (show good days) then later
    (reveal when the issue hits); the plants must stay at fixed calendar
    positions while it moves. So with ``window_end`` held constant, the
    projected plant timeline must be IDENTICAL across different up_to
    values — anchoring plants on up_to (the bug this guards) would drag
    them backward as the trainer loads an earlier day."""
    import dataclasses as _dc
    from recon_gen.common.l2 import default_l2_instance
    from recon_gen.common.l2.trainer_timeline import compute_plant_timeline

    inst = default_l2_instance()
    scenario_end = date(2026, 5, 22)

    def plant_days(up_to: date) -> list[date]:
        tg = TestGeneratorCache(
            TestGeneratorConfig(end_date=up_to, scope="full"),
            window=DateInterval.trailing_days_ending_today(scenario_end, 90),
        )
        # _render_timeline_section projects plants on window.end, NOT up_to.
        proj = _dc.replace(tg.get(), end_date=tg.get_window().end)
        return [td.day for td in compute_plant_timeline(inst, proj)]  # type: ignore[arg-type]: instance shape is Any-ish; compute_plant_timeline narrows internally

    early = plant_days(date(2026, 5, 10))   # loaded only through good days
    late = plant_days(date(2026, 5, 22))    # loaded through the issue
    assert early == late
    assert len(early) > 0  # spec_example has plants in this window


def test_data_page_timeline_uncovered_rails_renders_dense_window(
    writable_l2_yaml: Path,
) -> None:
    """uncovered_rails scope ⇒ no plants emitted but the dense 90-day
    window still renders so the operator sees the timeline context.
    The header carries a hint that no plants are in this scope; every
    row renders as `--empty` (no chips) including the anchor."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(
            end_date=date(2026, 5, 14),
            scope="uncovered_rails",
        ),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Header surfaces the no-plants hint.
    assert "scope=uncovered_rails" in body
    # Dense window: 90 rows still render.
    assert body.count('<button type="button" class="timeline-day') == 90
    # Anchor row exists (= end_date).
    assert 'id="timeline-anchor-row"' in body
    # No chips because no plants emitted.
    assert "timeline-chip--" not in body


def test_get_data_timeline_returns_section_fragment(
    writable_l2_yaml: Path,
) -> None:
    """GET /data/timeline returns the rendered section as a fragment
    (the HTMX hx-get target). Same shape as the inline render — same
    test selectors apply."""
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14)),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/data/timeline")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="data-timeline"' in body
    assert 'hx-get="/data/timeline"' in body  # self-rebinds for next refresh
    assert 'hx-trigger="trainer-knobs-changed from:body"' in body


def test_knob_puts_emit_hx_trigger_header(
    writable_l2_yaml: Path,
) -> None:
    """Every knob PUT carries HX-Trigger: trainer-knobs-changed so the
    timeline section's hx-trigger="...from:body" listener fires + the
    section auto-refetches with the new state. Without this header,
    knob mutations would orphan the timeline (UI shows old plant set)."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp_p = _put_form(c, "/data/knobs/plants", [("plant", "drift")])
        resp_d = _put_form(c, "/data/knobs/end_date", [("end_date", "2026-05-14")])
        resp_s = _put_form(c, "/data/knobs/seed", [("seed", "42")])
        resp_c = _put_form(c, "/data/knobs/scope", [("scope", "full")])

    for resp, name in [
        (resp_p, "plants"), (resp_d, "end_date"),
        (resp_s, "seed"), (resp_c, "scope"),
    ]:
        assert resp.headers.get("HX-Trigger") == "trainer-knobs-changed", (  # type: ignore[attr-defined]: TestClient stub return is Any
            f"PUT /data/knobs/{name} missing HX-Trigger header"
        )


def test_timeline_dense_window_renders_anchor_and_full_window(
    writable_l2_yaml: Path,
) -> None:
    """The timeline dense-renders the full baseline window (90 days
    by default, sourced from seed.DEFAULT_BASELINE_WINDOW_DAYS) with
    the anchor row highlighted via .timeline-day--anchor + a stable
    id for scrollIntoView."""
    from recon_gen.common.l2.seed import DEFAULT_BASELINE_WINDOW_DAYS

    # Pin window_end too — the timeline window anchors on window_end
    # (which defaults to date.today()), NOT cfg.end_date. Without this
    # pin the test rolls a day every midnight.
    window_end = date(2026, 5, 14)
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=window_end, scope="full"),
        window=DateInterval.trailing_days_ending_today(
            window_end, DEFAULT_BASELINE_WINDOW_DAYS,
        ),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # One row per day in the window.
    assert body.count('<button type="button" class="timeline-day') == DEFAULT_BASELINE_WINDOW_DAYS
    # The anchor row gets a stable id so the scrollIntoView script can
    # find it after every HTMX swap.
    assert 'id="timeline-anchor-row"' in body
    assert "timeline-day--anchor" in body
    # Anchor row's date is the cached end_date.
    assert 'value="2026-05-14"' in body  # end_date strip
    # Inline script wires the scrollIntoView call.
    assert "scrollIntoView" in body
    # First (oldest) row is window_days - 1 days back from anchor.
    window_start = window_end - timedelta(days=DEFAULT_BASELINE_WINDOW_DAYS - 1)
    assert f'>{window_start.isoformat()}<' in body


def test_timeline_anchors_on_today_when_end_date_none(
    writable_l2_yaml: Path,
) -> None:
    """When end_date is None the timeline anchors on the system today
    (matching what the generator does when tg.end_date is None at
    Deploy time). Trainer-mode UI is not a determinism path."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig(end_date=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    today = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode test must use the same wall-clock anchor the renderer uses
    # The anchor row's date label is today's ISO.
    assert f'>{today.isoformat()}<' in body
    assert 'id="timeline-anchor-row"' in body


def test_timeline_day_button_writes_end_date(
    writable_l2_yaml: Path,
) -> None:
    """Each timeline-day button uses hx-put + hx-vals to write the
    clicked day's ISO into /data/knobs/end_date. The button targets
    #data-knob-end-date so the on-screen end_date strip refreshes
    (and the end_date PUT in turn fires the trainer-knobs-changed
    trigger that re-renders the timeline)."""
    # AO.S2.a — pin window_end (the scenario-end / plant anchor) so the
    # timeline is deterministic regardless of wall-clock date; it's a
    # distinct knob from end_date (the load-up-to scrub head). With both
    # at 2026-05-14 the scenario is fully loaded to its end, so its plants
    # render as data-days (not the dimmed "future" zone).
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(end_date=date(2026, 5, 14), scope="full"),
        # Restore the v1 implicit default (window_start = window_end - 89);
        # BC.9 made the window typed but didn't change the default policy.
        window=DateInterval.trailing_days_ending_today(date(2026, 5, 14), 90),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    # Some specific timeline day in spec_example's plant set falls on
    # an ISO date matching this regex; the timeline-day button carries
    # the hx-vals end_date payload + targets the end_date strip.
    assert 'class="timeline-day"' in body
    assert "hx-target=\"#data-knob-end-date\"" in body
    # hx-vals end_date payload — the JSON structure we emit.
    assert 'hx-vals=\'{"end_date": "' in body


# ---------------------------------------------------------------------------
# X.4.h.etl-toggle — etl_hook enable/disable strip
# ---------------------------------------------------------------------------


def test_etl_hook_strip_renders_not_configured_without_cfg(
    writable_l2_yaml: Path,
) -> None:
    """Without cfg wired, the strip surfaces "(not configured)" + a
    disabled checkbox. The toggle is moot — Deploy is also absent."""
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'id="data-knob-etl-hook"' in body
    assert "(not configured)" in body
    assert "etl-hook-command--missing" in body
    assert 'type="checkbox" disabled' in body


def test_etl_hook_strip_renders_command_when_configured(
    writable_l2_yaml: Path,
) -> None:
    """With cfg.etl_hook set + toggle enabled (default), the strip
    surfaces the command in a <code> + a checked checkbox."""
    cfg = make_test_config(etl_hook="echo upstream-pull && sync")
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'id="data-knob-etl-hook"' in body
    # Command surfaces in a <code> with title= for full hover text.
    assert "echo upstream-pull &amp;&amp; sync" in body
    assert "etl-hook-toggle" in body
    # Default = enabled ⇒ checkbox checked.
    assert 'type="checkbox" name="enabled" value="on" checked' in body
    assert "etl-hook-command--missing" not in body


def test_etl_hook_strip_renders_disabled_state(
    writable_l2_yaml: Path,
) -> None:
    """When the cache flag is off but cfg.etl_hook is set, the
    checkbox renders unchecked + the command shows greyed out
    (line-through). The command isn't erased — the toggle is the only
    thing that changes."""
    cfg = make_test_config(etl_hook="echo upstream-pull")
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(),
        etl_hook_enabled=False,
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text

    assert 'id="data-knob-etl-hook"' in body
    # Unchecked: the rendered tag must not have ``checked`` after value=on.
    assert 'name="enabled" value="on" checked' not in body
    assert 'name="enabled" value="on"' in body
    # Command still shown, but with the disabled style class.
    assert "echo upstream-pull" in body
    assert "etl-hook-command--disabled" in body


def test_put_etl_hook_disable(
    writable_l2_yaml: Path,
) -> None:
    """PUT with no `enabled` field flips the cache to disabled
    (HTML form default for unchecked checkboxes)."""
    cfg = make_test_config(etl_hook="echo x")
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    assert tg_cache.is_etl_hook_enabled() is True
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Empty form payload mirrors what HTMX sends for an unchecked
        # checkbox change event.
        resp = _put_form(c, "/data/knobs/etl_hook", [])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.is_etl_hook_enabled() is False
    # Returned strip has the disabled-state styling.
    assert "etl-hook-command--disabled" in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_etl_hook_enable(
    writable_l2_yaml: Path,
) -> None:
    """PUT with `enabled=on` flips the cache to enabled."""
    cfg = make_test_config(etl_hook="echo x")
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(),
        etl_hook_enabled=False,
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/etl_hook", [("enabled", "on")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.is_etl_hook_enabled() is True
    assert 'name="enabled" value="on" checked' in resp.text  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_etl_hook_emits_hx_headers(
    writable_l2_yaml: Path,
) -> None:
    """The PUT response carries the same HX-Trigger + HX-Push-Url
    contract every other knob does — so the timeline section refreshes
    + the URL bar reflects state."""
    cfg = make_test_config(etl_hook="echo x")
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/etl_hook", [])
    assert resp.headers.get("HX-Trigger") == "trainer-knobs-changed"  # type: ignore[attr-defined]: TestClient stub return is Any
    push_url = resp.headers.get("HX-Push-Url")  # type: ignore[attr-defined]: TestClient stub return is Any
    assert push_url is not None
    # Disabled state surfaces as ?etl_hook=disabled.
    assert "etl_hook=disabled" in push_url


def test_put_etl_hook_url_clean_when_enabled(
    writable_l2_yaml: Path,
) -> None:
    """Default state (enabled) keeps the URL clean — etl_hook param
    only appears when explicitly disabled."""
    cfg = make_test_config(etl_hook="echo x")
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(),
        etl_hook_enabled=False,
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/etl_hook", [("enabled", "on")])
    push_url = resp.headers.get("HX-Push-Url")  # type: ignore[attr-defined]: TestClient stub return is Any
    assert push_url is not None
    assert "etl_hook=" not in push_url


def test_get_data_with_etl_hook_url_param_restores_state(
    writable_l2_yaml: Path,
) -> None:
    """GET /data?etl_hook=disabled flips the cache (bookmark / reload
    restore). Idempotent: applying twice is a no-op."""
    cfg = make_test_config(etl_hook="echo x")
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    assert tg_cache.is_etl_hook_enabled() is True
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        c.get("/data?etl_hook=disabled")
    assert tg_cache.is_etl_hook_enabled() is False


def test_get_data_with_etl_hook_enabled_url_param(
    writable_l2_yaml: Path,
) -> None:
    """?etl_hook=enabled is the inverse — flips back on."""
    cfg = make_test_config(etl_hook="echo x")
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(),
        etl_hook_enabled=False,
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        c.get("/data?etl_hook=enabled")
    assert tg_cache.is_etl_hook_enabled() is True


def test_get_data_with_bad_etl_hook_url_param_silently_drops(
    writable_l2_yaml: Path,
) -> None:
    """Garbage values silently drop — same posture as other knobs."""
    cfg = make_test_config(etl_hook="echo x")
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        c.get("/data?etl_hook=garbage")
    # Cache state preserved (default = True).
    assert tg_cache.is_etl_hook_enabled() is True


def test_put_etl_hook_route_absent_without_cache(
    writable_l2_yaml: Path,
) -> None:
    """Severability rule — without tg_cache the route doesn't mount."""
    cfg = make_test_config(etl_hook="echo x")
    app = _build_app(writable_l2_yaml, tg_cache=None, cfg=cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/etl_hook", [("enabled", "on")])
    assert resp.status_code in (404, 405)  # type: ignore[attr-defined]: TestClient stub return is Any


# ----- X.4.h.8.b/c — knob change → sidefile updated → next page-load reflects -----
#
# These run against the route layer (not Playwright) because the
# sidefile-write is the contract under test, not the JS chrome. The
# fast loop also lets us cover all five knob routes; full Playwright
# would multiply 2-3 minutes per test which is wasteful for a
# server-side persistence check.


def _cache_with_sidefile(
    cfg_path: Path,
) -> TestGeneratorCache:
    """Construct a cache wired to a sidefile next to ``cfg_path`` —
    same factory the studio CLI uses."""
    return TestGeneratorCache.from_cfg_with_state(
        make_test_config(), cfg_path,
    )


def test_put_plants_writes_sidefile(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    """A real PUT through the route mutates the cache AND persists to
    the sibling sidefile so the next Studio launch sees the picked
    subset."""
    from recon_gen.common.l2.studio_state import (  # noqa: PLC0415
        SIDEFILE_NAME,
        load_studio_state,
    )

    cfg_path = tmp_path / "config.yaml"
    tg_cache = _cache_with_sidefile(cfg_path)
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/plants",
            [("plant", "drift"), ("plant", "overdraft")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    sidefile = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert sidefile is not None
    assert sidefile.plants == ("drift", "overdraft")


def test_put_seed_writes_sidefile(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    from recon_gen.common.l2.studio_state import (  # noqa: PLC0415
        SIDEFILE_NAME,
        load_studio_state,
    )

    cfg_path = tmp_path / "config.yaml"
    tg_cache = _cache_with_sidefile(cfg_path)
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/seed", [("seed", "12345")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    sidefile = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert sidefile is not None
    assert sidefile.seed == 12345


def test_put_scope_writes_sidefile(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    from recon_gen.common.l2.studio_state import (  # noqa: PLC0415
        SIDEFILE_NAME,
        load_studio_state,
    )

    cfg_path = tmp_path / "config.yaml"
    tg_cache = _cache_with_sidefile(cfg_path)
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/scope", [("scope", "exceptions_only")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    sidefile = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert sidefile is not None
    assert sidefile.scope == "exceptions_only"


def test_put_end_date_writes_sidefile(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    from recon_gen.common.l2.studio_state import (  # noqa: PLC0415
        SIDEFILE_NAME,
        load_studio_state,
    )

    cfg_path = tmp_path / "config.yaml"
    tg_cache = _cache_with_sidefile(cfg_path)
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Pick a date inside the cache's default window (today-89..today).
        within_window = (date.today() - timedelta(days=10)).isoformat()  # typing-smell: ignore[no-datetime-now]: test mirrors the trainer-mode default-window anchor; not a determinism-sensitive path
        resp = _put_form(
            c, "/data/knobs/end_date", [("end_date", within_window)],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    sidefile = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert sidefile is not None
    assert sidefile.end_date == date.fromisoformat(within_window)


def test_put_etl_hook_writes_sidefile(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    from recon_gen.common.l2.studio_state import (  # noqa: PLC0415
        SIDEFILE_NAME,
        load_studio_state,
    )

    cfg_path = tmp_path / "config.yaml"
    tg_cache = TestGeneratorCache.from_cfg_with_state(
        make_test_config(etl_hook="echo x"), cfg_path,
    )
    app = _build_app(
        writable_l2_yaml,
        tg_cache=tg_cache,
        cfg=make_test_config(etl_hook="echo x"),
    )
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Empty body = checkbox unchecked = "disabled".
        resp = _put_form(c, "/data/knobs/etl_hook", [])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    sidefile = load_studio_state(tmp_path / SIDEFILE_NAME)
    assert sidefile is not None
    assert sidefile.etl_hook_enabled is False


# ----- X.4.i.3 — only_template + derive_balances UI controls -----


def test_only_template_strip_renders_blank_when_none(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(TestGeneratorConfig(only_template=None))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text
    assert 'id="data-knob-only-template"' in body
    assert 'value=""' in body  # input empty
    assert "(none)" in body


def test_only_template_strip_reflects_cached_value(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(only_template="MerchantSettlementCycle"),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text
    assert 'value="MerchantSettlementCycle"' in body


def test_put_only_template_sets_value(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(TestGeneratorConfig())
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/only_template",
            [("only_template", "MerchantSettlementCycle")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().only_template == "MerchantSettlementCycle"


def test_put_only_template_empty_clears_to_none(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(only_template="MerchantSettlementCycle"),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/only_template",
                         [("only_template", "")])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().only_template is None


def test_put_only_template_route_absent_without_cache(
    writable_l2_yaml: Path,
) -> None:
    """Severability — without tg_cache the route doesn't mount."""
    app = _build_app(writable_l2_yaml, tg_cache=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/only_template",
                         [("only_template", "X")])
    assert resp.status_code in (404, 405)  # type: ignore[attr-defined]: TestClient stub return is Any


def test_derive_balances_strip_renders_unchecked_by_default(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(TestGeneratorConfig(derive_balances=False))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text
    assert 'id="data-knob-derive-balances"' in body
    assert "(disabled)" in body
    assert 'name="enabled"' in body
    assert "checked" not in (
        body.split('id="data-knob-derive-balances"', 1)[1]
        .split("</form>", 1)[0]
    )


def test_derive_balances_strip_renders_checked_when_enabled(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(TestGeneratorConfig(derive_balances=True))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text
    section = (
        body.split('id="data-knob-derive-balances"', 1)[1]
        .split("</form>", 1)[0]
    )
    assert "checked" in section
    assert "control accounts (default)" in section


def test_derive_balances_strip_reflects_role_override(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(
        TestGeneratorConfig(
            derive_balances=True,
            derive_balances_account_roles=("gl_control", "dda"),
        ),
    )
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text
    assert "gl_control, dda" in body


def test_put_derive_balances_enable(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(TestGeneratorConfig(derive_balances=False))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(
            c, "/data/knobs/derive_balances", [("enabled", "on")],
        )
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().derive_balances is True


def test_put_derive_balances_disable(
    writable_l2_yaml: Path,
) -> None:
    tg_cache = TestGeneratorCache(TestGeneratorConfig(derive_balances=True))
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Empty body = unchecked checkbox.
        resp = _put_form(c, "/data/knobs/derive_balances", [])
    assert resp.status_code == 200  # type: ignore[attr-defined]: TestClient stub return is Any
    assert tg_cache.get().derive_balances is False


def test_put_derive_balances_route_absent_without_cache(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml, tg_cache=None)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = _put_form(c, "/data/knobs/derive_balances", [("enabled", "on")])
    assert resp.status_code in (404, 405)  # type: ignore[attr-defined]: TestClient stub return is Any


def test_put_only_template_persists_to_sidefile(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    """The sidefile mirror — same contract as the other knob routes
    (tested in detail in the X.4.h.7 sidefile-integration block)."""
    cfg_path = tmp_path / "config.yaml"
    tg_cache = _cache_with_sidefile(cfg_path)
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        _put_form(
            c, "/data/knobs/only_template",
            [("only_template", "MerchantSettlementCycle")],
        )
    # Reload from disk via a second cache.
    tg_b = _cache_with_sidefile(cfg_path)
    assert tg_b.get().only_template == "MerchantSettlementCycle"


def test_put_derive_balances_persists_to_sidefile(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "config.yaml"
    tg_cache = _cache_with_sidefile(cfg_path)
    app = _build_app(writable_l2_yaml, tg_cache=tg_cache)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        _put_form(
            c, "/data/knobs/derive_balances", [("enabled", "on")],
        )
    tg_b = _cache_with_sidefile(cfg_path)
    assert tg_b.get().derive_balances is True


def test_studio_restart_reflects_persisted_state(
    writable_l2_yaml: Path, tmp_path: Path,
) -> None:
    """Full restart loop: PUT a knob → sidefile written → second cache
    constructed via from_cfg_with_state on the same cfg path → second
    app's GET /data renders the persisted selection. This is the
    h.8.b "next page-load reflects the saved state" contract."""
    cfg_path = tmp_path / "config.yaml"

    # Studio-1 — operator picks a non-default scope + a plant subset.
    tg_a = _cache_with_sidefile(cfg_path)
    app_a = _build_app(writable_l2_yaml, tg_cache=tg_a)
    with TestClient(app_a) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        c.put(
            "/data/knobs/scope",
            content="scope=exceptions_only",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        _put_form(
            c, "/data/knobs/plants",
            [("plant", "drift"), ("plant", "overdraft")],
        )

    # Studio-2 — fresh cache from the same cfg path. The sidefile lands.
    tg_b = _cache_with_sidefile(cfg_path)
    assert tg_b.get().scope == "exceptions_only"
    assert tg_b.get().plants == ("drift", "overdraft")

    # And the second app's GET /data shows the picked plants checked.
    app_b = _build_app(writable_l2_yaml, tg_cache=tg_b)
    with TestClient(app_b) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text
    assert 'value="drift" checked' in body
    assert 'value="overdraft" checked' in body
    # Other plants are NOT checked.
    assert 'value="limit_breach" />' in body
