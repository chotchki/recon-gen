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
    assert "<title>Recon-Gen · Studio · ETL · Probe</title>" in body
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


def test_etl_probe_date_range_defaults_to_all_time(
    writable_l2_yaml: Path,
) -> None:
    """BTa.2 P1.1 — default window is "All time" (from=1900-01-01,
    to=today). Replaces the earlier "last 7 days" default: the
    cold-read showed first-time operators couldn't tell whether 0
    rows meant "data missing" or "wrong window," so the trust-killer
    fix is to start with the widest possible window."""
    from datetime import date
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe").text
    today = date.today().isoformat()  # typing-smell: ignore[no-datetime-now]: asserting against the same wall-clock the route renders; comparing today-to-today is the test's intent
    assert 'value="1900-01-01"' in body, (
        "expected from-date default 1900-01-01 (All time) in form"
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


# -- BTa.5 — picker polish + chain side-panel ----------------------------


def test_etl_probe_picker_renders_one_line_definitions_per_radio(
    writable_l2_yaml: Path,
) -> None:
    """BTa.5 — every slice-kind radio carries a short definition
    inline so first-time operators dont bounce to the glossary."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe").text
    assert "lowest-level L2 primitive" in body  # rail definition
    assert "multi-leg event template" in body  # transfer_template
    assert "parent" in body and "child" in body  # chain


def test_etl_probe_name_input_is_searchable_datalist_input(
    writable_l2_yaml: Path,
) -> None:
    """BTa.5 — name input is `<input list>` + `<datalist>` (native
    browser autocomplete; no JS), replacing the prior `<select>`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe?kind=rail").text
    assert "<input list=\"probe-name-suggestions\"" in body
    assert "<datalist id=\"probe-name-suggestions\">" in body
    # Old `<select name=\"name\">` shape gone.
    assert "<select name=\"name\"" not in body
    # Placeholder hints at search affordance.
    assert "Start typing to search" in body


def test_etl_probe_renders_four_date_chips_with_quick_windows(
    writable_l2_yaml: Path,
) -> None:
    """BTa.5 — 4 date quick-pick chips (Last 7d / 30d / 90d / All time)
    each carry an anchor link with the appropriate date_from/date_to
    query params + the current (kind, name) forwarded."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe?kind=rail&name=ach_credit").text
    for chip in ("Last 7d", "Last 30d", "Last 90d", "All time"):
        assert f"data-test-date-chip=\"{chip}\"" in body
    # Each chip preserves the current kind + name in the carryover.
    assert "kind=rail" in body
    assert "name=ach_credit" in body


def test_etl_probe_chip_for_default_window_renders_as_active(
    writable_l2_yaml: Path,
) -> None:
    """The default window (All time) is the operators starting state;
    its chip ships with the active styling so the active selection is
    visible at a glance."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get("/etl/probe").text
    # The All time chip is active (bg-accent text-accent-fg styling).
    # Find the All time chip block and assert it ships the active classes.
    chip_block = body.split("data-test-date-chip=\"All time\"", 1)[0]
    # Walk back to the opening <a tag.
    open_tag = chip_block.rsplit("<a ", 1)[1]
    assert "bg-accent" in open_tag
    assert "text-accent-fg" in open_tag


def test_etl_probe_chain_kind_renders_side_panel_arrow_trigger(
    writable_l2_yaml: Path,
) -> None:
    """BTa.5 — picking a chain parent surfaces the arrow-diagram
    side-panel trigger above the contract table."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    parent = str(cache.get().chains[0].parent)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        body = c.get(f"/etl/probe?kind=chain&name={parent}").text
    assert "data-test-chain-arrow-trigger" in body
    assert f"/studio/side-panel/chain/{parent}" in body
    assert "View arrow diagram" in body


def test_side_panel_chain_route_renders_parent_arrow_children(
    writable_l2_yaml: Path,
) -> None:
    """GET /studio/side-panel/chain/<parent> renders the parent name,
    a down-arrow, then the child list. Singleton vs XOR labels both
    surface based on `len(children)`."""
    cache = L2InstanceCache.from_path(writable_l2_yaml)
    parent = str(cache.get().chains[0].parent)
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get(f"/studio/side-panel/chain/{parent}")
        assert resp.status_code == 200
        body = resp.text
    assert parent in body
    assert "↓" in body
    # Either singleton or XOR label lands.
    assert "Singleton" in body or "XOR" in body


def test_side_panel_chain_route_unknown_parent_404s(
    writable_l2_yaml: Path,
) -> None:
    """Unknown parent ⇒ 404 + helpful pointer to /diagram."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.get("/studio/side-panel/chain/not_a_real_parent")
        assert resp.status_code == 404
        body = resp.text
    assert "No chain found" in body
    assert "/diagram" in body

