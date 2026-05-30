"""BT.2 — ``/studio/etl/probe`` route integration tests.

Verifies the picker form, contract panel, and observed-rows panel
land in the rendered HTML against the spec_example L2 fixture. The
test surface uses ``db_pool=None`` so the observed panel shows the
"no DB pool wired" banner — full DB-backed probe behavior is
exercised by ``test_l2_probe`` against the seeded aiosqlite pool.
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
    # db_pool intentionally omitted — exercises the "no DB pool wired"
    # branch of _render_etl_probe_page so the unit test stays fast.
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )


def test_etl_probe_returns_200_with_picker_form(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/etl/probe")
        assert resp.status_code == 200
        body = resp.text
    assert "<title>Studio · ETL · Probe" in body
    # Picker form with the 3 radios.
    assert '<form method="get" action="/etl/probe"' in body
    for kind_value in ("rail", "transfer_template", "chain"):
        assert f'data-test-kind="{kind_value}"' in body


def test_etl_probe_initial_load_shows_empty_state_no_observed_panel(
    writable_l2_yaml: Path,
) -> None:
    """Bare ``/etl/probe`` (no name picked) renders the empty-state
    nudge instead of the side-by-side body."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe").text
    assert 'id="probe-empty-initial"' in body
    # Side-by-side body NOT rendered (no name yet).
    assert 'id="probe-body"' not in body


def test_etl_probe_picker_populates_dropdown_with_l2_rail_names(
    writable_l2_yaml: Path,
) -> None:
    """Rail kind dropdown carries every L2-declared rail name as an
    <option>. Loose match: don't pin specific names so the fixture can
    evolve, just assert at least one option lands."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe?kind=rail").text
    # The L2InstanceCache for spec_example has at least 4 rails; pin
    # via the structural shape (multiple <option value="..."> entries).
    option_count = body.count('<option value="')
    assert option_count > 2, (
        f"expected multiple rail options in dropdown, got {option_count}"
    )


def test_etl_probe_named_rail_renders_contract_panel(
    writable_l2_yaml: Path,
) -> None:
    """Picking a rail name populates the contract panel + (since
    db_pool is absent) the 'no DB pool wired' branch of the observed
    panel."""
    # Pull a real rail name from the L2 to dodge fixture drift.
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    rail_name = str(cache.get().rails[0].name)

    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(f"/etl/probe?kind=rail&name={rail_name}").text
    # Side-by-side body lands.
    assert 'id="probe-body"' in body
    assert 'id="probe-contract-panel"' in body
    assert 'id="probe-observed-panel"' in body
    # Contract panel carries the selector row.
    assert 'rail_name' in body
    assert rail_name in body
    # Editor deep link surfaces.
    assert f'/l2_shape/rail/{rail_name}/edit' in body
    # No-pool banner (db_pool=None in fixture).
    assert 'No DB pool wired' in body


def test_etl_probe_unknown_name_renders_no_such_entity_message(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe?kind=rail&name=does_not_exist").text
    assert 'No rail named' in body
    assert 'does_not_exist' in body


def test_etl_probe_chain_kind_lists_chain_parents_in_dropdown(
    writable_l2_yaml: Path,
) -> None:
    """When ?kind=chain, the dropdown carries chain parent names, not
    rail names. (Cross-talk would mean operator pickers see the wrong
    universe.)"""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    expected_parents = {str(c.parent) for c in cache.get().chains}

    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe?kind=chain").text
    # At least one declared parent surfaces as an <option>.
    for parent in expected_parents:
        if f'<option value="{parent}"' in body:
            break
    else:
        pytest.fail(
            f"none of {expected_parents} surfaced as a chain dropdown option"
        )


def test_etl_probe_date_range_defaults_to_last_seven_days(
    writable_l2_yaml: Path,
) -> None:
    """The from/to inputs default to (today-6, today) when no query
    params land; the operator-controlled window per BT.0.5 mockup."""
    from datetime import date, timedelta
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe").text
    today = date.today().isoformat()  # typing-smell: ignore[no-datetime-now]: asserting against the same wall-clock the route renders; comparing today-to-today is the test's intent
    week_ago = (date.today() - timedelta(days=6)).isoformat()  # typing-smell: ignore[no-datetime-now]: same wall-clock anchor as above; assertion shape is "default window matches today's date math"
    assert f'value="{week_ago}"' in body, (
        f"expected from-date default {week_ago} in form"
    )
    assert f'value="{today}"' in body, (
        f"expected to-date default {today} in form"
    )


def test_etl_probe_carries_top_nav_with_probe_route_active(
    writable_l2_yaml: Path,
) -> None:
    """When make_studio_routes is wired with top_nav_fn, /etl/probe
    renders the nav with the probe path as the active marker."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    cfg = make_test_config()

    def fake_nav(active_href: str) -> str:
        return f'<nav data-test-active="{active_href}">NAV</nav>'

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
        body = c.get("/etl/probe").text
    assert 'data-test-active="/etl/probe"' in body
