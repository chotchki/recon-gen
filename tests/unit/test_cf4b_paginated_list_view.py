"""CF.4.b — server-side pagination + sort + search on /l2_shape/<kind>/.

Wires the CF.4.a typed primitive (`_components.ListToolbarState` +
`render_list_toolbar()`) into the editor's list route. These tests
pin: search drops non-matches, sort axes order entities as expected,
the page slice respects `page_offset` + `page_size`, and the toolbar
markup carries the current state.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.html._components import parse_toolbar_state
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


# ---------------------------------------------------------------------------
# parse_toolbar_state — query-param parsing + clamping
# ---------------------------------------------------------------------------

def test_parse_defaults_when_no_params() -> None:
    state = parse_toolbar_state({}, kind="rail", total_count=10)
    assert state.q == ""
    assert state.sort_axis == "default"
    assert state.page_offset == 0
    assert state.page_size == 25
    assert state.total_count == 10


def test_parse_strips_query_whitespace() -> None:
    state = parse_toolbar_state(
        {"q": "  foo  "}, kind="rail", total_count=10,
    )
    assert state.q == "foo"


def test_parse_clamps_negative_offset_to_zero() -> None:
    state = parse_toolbar_state(
        {"page_offset": "-5"}, kind="rail", total_count=10,
    )
    assert state.page_offset == 0


def test_parse_clamps_oversize_page_size_to_max() -> None:
    """Risk #6 — `?page_size=9999999` would otherwise blow up the render."""
    state = parse_toolbar_state(
        {"page_size": "9999999"}, kind="rail", total_count=10,
    )
    assert state.page_size == 200  # PAGE_SIZE_MAX


def test_parse_clamps_zero_page_size_to_min() -> None:
    """`?page_size=0` clamps to 1 (the minimum), not the default.
    A query string asserting an invalid value gets the bound it
    crossed; falling back to the default would silently mask the
    operator's typo."""
    state = parse_toolbar_state(
        {"page_size": "0"}, kind="rail", total_count=10,
    )
    assert state.page_size == 1


def test_parse_falls_back_when_sort_axis_out_of_universe() -> None:
    """`template_leg_count` is template-only; passing it for rails
    falls back to default rather than 500'ing."""
    state = parse_toolbar_state(
        {"sort_column": "template_leg_count"},
        kind="rail", total_count=10,
    )
    assert state.sort_axis == "default"


def test_parse_falls_back_when_nonint_page_offset() -> None:
    state = parse_toolbar_state(
        {"page_offset": "abc"}, kind="rail", total_count=10,
    )
    assert state.page_offset == 0


def test_parse_respects_url_prefix_for_home_page() -> None:
    """Home page embed reads kind-namespaced keys (rail_q, rail_page_offset)."""
    state = parse_toolbar_state(
        {
            "rail_q": "foo",
            "rail_page_offset": "25",
            "q": "wrong-key",  # bare keys ignored when prefix is set
        },
        kind="rail", total_count=100, url_prefix="rail",
    )
    assert state.q == "foo"
    assert state.page_offset == 25


# ---------------------------------------------------------------------------
# Filter + sort + pagination through /l2_shape/<kind>/
# ---------------------------------------------------------------------------

def _build_app(yaml_path: Path) -> object:
    """Studio app — same shape as `test_studio_home_route.py::_build_app`."""
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
    """Copy spec_example.yaml to a tempfile so writes don't mutate the
    bundled fixture."""
    src = FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def test_list_view_renders_toolbar_for_rail_kind(
    writable_l2_yaml: Path,
) -> None:
    """`/l2_shape/rail/` produces a toolbar + the per-card grid.
    Standalone page carries the search input + pager (no sort
    dropdown — operator lock 2026-06-05: dropdown doubled toolbar
    height for no daily value; default YAML-order is the canonical
    read shape)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/").text

    assert 'data-role="list-toolbar"' in body
    assert 'data-kind="rail"' in body
    assert 'name="q"' in body
    # No sort UI — only the URL parser still honors ?sort_column=… for
    # shared / external URLs (covered by test_list_view_sort_axis_…).
    assert 'name="sort_column"' not in body
    assert '<select' not in body


def test_list_view_search_filters_cards(writable_l2_yaml: Path) -> None:
    """`?q=External` filters rails to those with 'External' in entity_id."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        unfiltered = c.get("/l2_shape/rail/").text
        filtered = c.get("/l2_shape/rail/?q=External").text

    # ExternalRailInbound is in the fixture; SuspenseSweep is not.
    assert "ExternalRailInbound" in unfiltered
    assert "ExternalRailInbound" in filtered
    # spec_example has rails whose id doesn't contain "External".
    assert unfiltered.count('data-kind="rail"') > filtered.count(
        'data-kind="rail"',
    )


def test_list_view_search_with_no_matches_says_no_matches(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?q=defininitely_no_such_rail").text
    assert "No matches" in body


def test_list_view_pagination_slices_response(
    writable_l2_yaml: Path,
) -> None:
    """`?page_size=2` returns the first 2 entities; `?page_offset=2`
    skips them. Both pages render the toolbar with the right range."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        page1 = c.get(
            "/l2_shape/account/?page_size=2&sort_column=name_asc",
        ).text
        page2 = c.get(
            "/l2_shape/account/"
            "?page_size=2&page_offset=2&sort_column=name_asc",
        ).text

    # Page 1's range indicator shows "1–2 of <total>".
    assert "Showing 1–2 of" in page1
    # Page 2's range starts at 3.
    assert "Showing 3–" in page2
    # The two pages render different cards.
    page1_ids = _extract_article_ids(page1)
    page2_ids = _extract_article_ids(page2)
    assert len(page1_ids) == 2  # page_size=2 limit
    assert page1_ids and page2_ids
    assert set(page1_ids).isdisjoint(set(page2_ids))


def _extract_article_ids(html: str) -> list[str]:
    """Pull entity ids out of `<article id="<id>">` — the editor's
    read-card root."""
    import re
    return re.findall(r'<article[^>]*id="([^"]+)"', html)


def test_list_view_sort_axis_orders_alphabetically(
    writable_l2_yaml: Path,
) -> None:
    """`?sort_column=name_asc` orders entity ids A→Z; `name_desc`
    reverses. Server-side sort still honors URL params even though
    the dropdown UI was removed (2026-06-05) — kept so external
    tooling / shared URLs / power-user manual override still work."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        asc = c.get("/l2_shape/rail/?sort_column=name_asc").text
        desc = c.get("/l2_shape/rail/?sort_column=name_desc").text

    # Card order differs between asc and desc (assuming >1 rail
    # in the fixture, which spec_example satisfies).
    asc_ids = _extract_article_ids(asc)
    desc_ids = _extract_article_ids(desc)
    if len(asc_ids) > 1:
        assert asc_ids == sorted(asc_ids)
        assert desc_ids == sorted(desc_ids, reverse=True)


def test_list_view_oversized_page_size_does_not_500(
    writable_l2_yaml: Path,
) -> None:
    """Risk #6 — operator (or attacker) hits `?page_size=999999`; the
    parser clamps to 200, the route returns 200. (Unit tests on
    ``parse_toolbar_state`` already pin the clamp itself; this gate
    confirms the integrated route survives bad input.)"""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/rail/?page_size=9999999")
    assert resp.status_code == 200
    assert 'data-role="list-toolbar"' in resp.text


def test_list_view_embed_mode_returns_only_toolbar_plus_grid(
    writable_l2_yaml: Path,
) -> None:
    """`?embed=1` returns the toolbar + cards fragment (no <html>);
    that's the home-page hx-get target shape. Body toolbar is just
    range + pager — search is owned by the summary upstream and the
    sort dropdown was removed 2026-06-05."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/l2_shape/rail/?embed=1").text
    assert "<html" not in body
    # `<header` inside the cards matches naive `<head`; check for the
    # full opening tag instead.
    assert "<head>" not in body
    assert "</head>" not in body
    assert "<body" not in body
    assert 'data-role="list-toolbar"' in body
    assert 'data-kind="rail"' in body


def test_list_view_embed_pager_url_stays_in_embed_mode(
    writable_l2_yaml: Path,
) -> None:
    """When pagination is active (more than `page_size` rows post-
    filter), the Prev/Next hx-get URLs carry `embed=1` so clicking
    them refetches another embed fragment instead of a full page."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # Force pagination with a small page_size so the Next button
        # is enabled.
        body = c.get("/l2_shape/rail/?embed=1&page_size=2").text
    assert "/l2_shape/rail/?embed=1" in body
    assert "embed=1" in body
