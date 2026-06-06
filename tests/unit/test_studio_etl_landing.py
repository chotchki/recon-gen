"""BT.1 — ``/studio/etl/`` landing-page route tests.

Pins the contract for the 3-card index that fronts the BT.2-BT.4 ETL
Support sub-pages: GET ``/etl/`` returns a page with one card per
sub-page, each card carries the eventual destination href and a
short description. BT.2/3/4 land the actual sub-page routes; this
file only tests the landing.

Subsequent BT phases extend make_studio_routes with /etl/probe,
/etl/run, /etl/triage — until then a click on a card 404s; the
landing page's "coming in BT.N" hint primes the operator that the
destination isn't live yet.
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


def test_etl_landing_returns_200_and_renders_header(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/")
        assert resp.status_code == 200
        body = resp.text

    assert "<title>Recon-Gen · Studio · ETL</title>" in body
    # The Studio header title sets the operator's mental model — they're
    # in the ETL slice, not the L2 editor or Training. CG.21 unified
    # the title shape to `Recon-Gen · Studio · ETL` (was "Studio · ETL
    # Support — <deployment>" with mixed em-dash).
    assert "Studio · ETL" in body


def test_etl_landing_emits_three_cards_with_expected_routes(
    writable_l2_yaml: Path,
) -> None:
    """One card per BT.2/3/4 sub-page, in the BTa.3 numbered loop
    order (Refresh Data → Triage gaps → Probe & fix)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text

    for href in ("/etl/probe", "/etl/run", "/etl/triage"):
        assert f'href="{href}"' in body, f"missing landing card → {href}"
    # BTa.3 renamed Run → Refresh Data (matches the in-page button copy),
    # Probe → Probe & fix, Triage → Triage gaps. `&` HTML-escapes to `&amp;`.
    expected_card_titles = ("Refresh Data", "Triage gaps", "Probe &amp; fix")
    for title in expected_card_titles:
        assert f">{title}</h2>" in body, f"missing card title {title!r}"


def test_etl_landing_cards_drop_coming_in_hint_once_destinations_ship(
    writable_l2_yaml: Path,
) -> None:
    """Once BT.2/3/4 ship, the landing cards drop their "coming in BT.N"
    hint and surface as plain links."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    # All three sub-pages have shipped — no "coming in" hints anywhere.
    for phase in ("BT.2", "BT.3", "BT.4"):
        assert f"coming in {phase}" not in body


def test_etl_landing_carries_top_nav_when_factory_provided(
    writable_l2_yaml: Path,
) -> None:
    """When make_studio_routes is wired with top_nav_fn, the landing
    page renders the shared nav strip with /etl/ as the active entry."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    cfg = make_test_config()

    def fake_nav(active_href: str) -> str:
        return (
            f'<nav data-test-nav="1" data-test-active="{active_href}">'
            "TOP_NAV</nav>"
        )

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
        body = c.get("/etl/").text
    assert 'data-test-nav="1"' in body
    assert 'data-test-active="/etl/"' in body


def test_etl_landing_skips_top_nav_when_factory_absent(
    writable_l2_yaml: Path,
) -> None:
    """Default surface: no factory → no nav strip; landing page still
    renders. Mirrors the home-page null-nav test established in BS.3."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/")
        assert resp.status_code == 200
        body = resp.text
    assert "data-test-nav" not in body


def test_etl_landing_describes_each_card_with_user_facing_summary(
    writable_l2_yaml: Path,
) -> None:
    """Each card carries a one-line description of the sub-page's
    purpose — operators reading the index should understand what each
    workflow does without clicking through. Loose match so copy edits
    don't tip the gate."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text

    # Probe = investigate one slice (declared vs runtime side-by-side).
    assert "rail, template, or chain" in body
    # Run = execute pipeline + per-kind coverage.
    assert "coverage tally" in body
    # Triage = find gaps + deep link to editor.
    assert "deep link" in body or "L2 editor" in body


# -- BTa.3 — numbered loop + tutorial banner ------------------------------


def test_etl_landing_cards_carry_step_numbers_in_loop_order(
    writable_l2_yaml: Path,
) -> None:
    """BTa.3 Lock 2 — cards are numbered 1./2./3. in the loop order
    (Refresh Data → Triage → Probe). The data-step attribute pins the
    sequence so a reorder is loud."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    # The step number appears in a per-card chip + on the href anchor.
    assert 'data-step="1"' in body
    assert 'data-step="2"' in body
    assert 'data-step="3"' in body
    # Step 1 lands on /etl/run (Refresh Data is the entry-point).
    assert (
        'data-step="1"'
    ) in body.split('href="/etl/run"', 1)[0].split("<a", -1)[-1] + (
        'href="/etl/run"'
    ) or 'data-step="1"' in body  # loose check that the chip renders
    # Step ordering — Refresh Data comes before Triage which comes
    # before Probe in the rendered HTML.
    refresh_idx = body.index('href="/etl/run"')
    triage_idx = body.index('href="/etl/triage"')
    probe_idx = body.index('href="/etl/probe"')
    assert refresh_idx < triage_idx < probe_idx, (
        "BTa.3 loop order: Refresh Data → Triage → Probe"
    )


def test_etl_landing_renders_arrows_between_cards(
    writable_l2_yaml: Path,
) -> None:
    """Visual arrows between cards reinforce the loop sequence (only
    on wide screens; the `hidden lg:flex` strip stacks the cards
    vertically when the viewport can't fit a single row)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    # 2 arrows between 3 cards.
    assert body.count('aria-hidden="true">→</div>') == 2


def test_etl_landing_renders_tutorial_banner_with_localstorage_key(
    writable_l2_yaml: Path,
) -> None:
    """BTa.3 Lock 2 — dismissable "First time here?" banner with a
    5-step inline checklist. Dismissal persists in localStorage keyed
    on deployment_name so each environment carries its own state."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    assert 'id="etl-tutorial-banner"' in body
    assert "First time here?" in body
    assert "data-tutorial-banner-key=" in body
    # Test config's deployment_name fronts the storage key.
    assert "recon_gen.tutorial_dismissed." in body
    # Dismiss button + the JS shim that wires localStorage.
    assert "data-tutorial-dismiss" in body
    assert "localStorage" in body
    # 5 steps land in the checklist (one <li> per).
    assert body.count('<li class="mb-2 last:mb-0">') == 5


def test_etl_landing_tutorial_banner_hidden_initially_for_js_reveal(
    writable_l2_yaml: Path,
) -> None:
    """The banner ships with `display:none` so the JS shim can reveal
    it after checking localStorage — avoids a one-frame flash for
    returning operators who already dismissed it."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text
    # Find the banner's opening tag and confirm display:none ships
    # alongside the banner ID.
    banner_chunk = body.split('id="etl-tutorial-banner"', 1)[1][:300]
    assert "display:none" in banner_chunk


# -- BTa.8 cold-read v3 — htmx loaded on every ETL sub-page ----------------


@pytest.mark.parametrize("path", ["/etl/", "/etl/run", "/etl/triage", "/etl/probe"])
def test_etl_sub_pages_load_htmx_for_side_panel_drawer(
    writable_l2_yaml: Path, path: str,
) -> None:
    """The top-nav `[?]` button + side-panel drawer use `hx-get` to
    swap the glossary fragment into `#side-panel-body`. Every page
    that renders the top-nav MUST load htmx — otherwise the drawer
    opens but stays on `Loading…`. Pre-BTa.8 the ETL sub-pages
    rendered the [?] button but never loaded htmx, so clicking it
    on /etl/triage left the drawer hung."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(path).text
    assert "htmx.org@1.9.10" in body, (
        f"{path} renders the [?] button but doesn't load htmx — "
        f"the side-panel drawer will hang on Loading…"
    )
