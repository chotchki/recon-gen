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

    assert "<title>Studio · ETL Support" in body
    # The Studio header title sets the operator's mental model — they're
    # in the ETL slice, not the L2 editor or Training.
    assert "Studio · ETL Support" in body


def test_etl_landing_emits_three_cards_with_expected_routes(
    writable_l2_yaml: Path,
) -> None:
    """One card per BT.2/3/4 sub-page, in the operator-flow order
    (Probe → Run → Triage)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/").text

    for href in ("/etl/probe", "/etl/run", "/etl/triage"):
        assert f'href="{href}"' in body, f"missing landing card → {href}"
    for title in ("Probe", "Run", "Triage"):
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
