"""CF.4.d — home-page kind-namespaced URL state + cascade-reload
preservation.

`_render_home_page` reads `?rail_q=…&template_page_offset=25` off
the home URL (Q1B kind-namespaced) and bakes the per-section
fragments into each section's lazy-load `hx-get` URL using bare
keys (Q1A — what `/l2_shape/<kind>/?embed=1` actually parses).

On `HX-Trigger: l2-cascade-reload` htmx refetches the section URL
(unchanged) → state preserved across saves in *other* sections.
This mirrors the diagram URL-is-state-truth pattern (CF.3.m).

Sections with active state auto-open (Q6A — a collapsed section
that hides its own search hits is a footgun).
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
# No params on / → legacy hx-get behavior
# ---------------------------------------------------------------------------

def _section_is_open(html: str, kind: str) -> bool:
    """Probe whether the section for ``kind`` has the `open` attr on
    its `<details>`. Each section is one `<details data-kind="…">`."""
    marker = f'data-kind="{kind}"'
    idx = html.index(marker)
    tag_end = html.index(">", idx)
    tag_start = html.rfind("<details", 0, idx)
    return " open" in html[tag_start:tag_end + 1]


def test_home_no_params_uses_bare_section_hx_get(
    writable_l2_yaml: Path,
) -> None:
    """When the home URL carries no toolbar state, each section's
    `hx-get` URL is the legacy `/l2_shape/<kind>/?embed=1` (no extra
    query). The first section opens by default per the legacy pattern."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'hx-get="/l2_shape/account/?embed=1"' in body
    assert 'hx-get="/l2_shape/rail/?embed=1"' in body
    # First section (account) is the one that auto-opens.
    assert _section_is_open(body, "account")
    assert not _section_is_open(body, "rail")


# ---------------------------------------------------------------------------
# Kind-namespaced URL state baked into section hx-get
# ---------------------------------------------------------------------------

def test_home_translates_rail_q_into_section_hx_get(
    writable_l2_yaml: Path,
) -> None:
    """`/?rail_q=external` → the rail section's hx-get carries
    `?embed=1&q=external` (bare key, the section endpoint's URL
    contract per Q1A)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/?rail_q=external").text
    assert 'hx-get="/l2_shape/rail/?embed=1&q=external"' in body
    # Other sections don't pick up rail-prefixed params.
    assert 'hx-get="/l2_shape/account/?embed=1"' in body


def test_home_translates_page_offset_for_template(
    writable_l2_yaml: Path,
) -> None:
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(
            "/?transfer_template_page_offset=25"
            "&transfer_template_sort_column=name_desc",
        ).text
    assert (
        'hx-get="/l2_shape/transfer_template/'
        '?embed=1&sort_column=name_desc&page_offset=25"'
    ) in body


def test_home_multiple_sections_state_coexist(
    writable_l2_yaml: Path,
) -> None:
    """Multiple kind-prefixed params at once → each section's hx-get
    carries only its own slice (no cross-pollination)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get(
            "/?rail_q=ach&account_page_offset=2",
        ).text
    assert 'hx-get="/l2_shape/rail/?embed=1&q=ach"' in body
    assert 'hx-get="/l2_shape/account/?embed=1&page_offset=2"' in body


# ---------------------------------------------------------------------------
# Q6A — auto-open sections with active state
# ---------------------------------------------------------------------------

def test_home_active_state_auto_opens_section(
    writable_l2_yaml: Path,
) -> None:
    """`?rail_q=…` auto-opens the rail section (Q6A — a collapsed
    section that hides its own search hits is a footgun). Other
    sections stay collapsed unless they ALSO carry state."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/?rail_q=external").text
    assert _section_is_open(body, "rail")
    assert not _section_is_open(body, "account")
    assert not _section_is_open(body, "transfer_template")


def test_home_active_state_overrides_default_first_open(
    writable_l2_yaml: Path,
) -> None:
    """When ANY section has active state, the legacy `idx == 0`
    auto-open default is suppressed — only state-active sections
    open. Otherwise an operator searching `?chain_q=…` would also
    see the unrelated first section pop open."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/?chain_q=foo").text
    assert _section_is_open(body, "chain")
    # Account (the first section by index) STAYS closed.
    assert not _section_is_open(body, "account")


# ---------------------------------------------------------------------------
# Cascade-reload preservation (risk #1)
# ---------------------------------------------------------------------------

def test_section_hx_trigger_unchanged_so_cascade_reload_preserves_state(
    writable_l2_yaml: Path,
) -> None:
    """Each section keeps `hx-trigger="load, l2-cascade-reload from:body"`.
    A save in section A fires `HX-Trigger: l2-cascade-reload` →
    htmx refetches section B's hx-get URL → that URL still carries
    section B's toolbar state. State preserved automatically (Q9A /
    CF.3.m pattern)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/?rail_q=external&account_q=cust").text

    # Each section still wires cascade-reload.
    assert body.count('hx-trigger="load, l2-cascade-reload from:body"') >= 6
    # And the URLs they refetch carry the per-kind state.
    assert 'hx-get="/l2_shape/rail/?embed=1&q=external"' in body
    assert 'hx-get="/l2_shape/account/?embed=1&q=cust"' in body


# ---------------------------------------------------------------------------
# Unknown params are ignored
# ---------------------------------------------------------------------------

def test_home_ignores_unknown_prefixes(writable_l2_yaml: Path) -> None:
    """`?bogus_q=…` (no such kind) doesn't leak into any section's URL."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/?bogus_q=foo&xyzzy=bar").text
    # No section's hx-get contains "bogus" or "xyzzy".
    assert "bogus" not in body
    assert "xyzzy" not in body
    # Legacy default: first section (account) opens.
    assert _section_is_open(body, "account")


def test_home_value_is_url_encoded(writable_l2_yaml: Path) -> None:
    """Search terms with special characters get URL-encoded into the
    section's hx-get URL. `?rail_q=foo bar` → `q=foo%20bar`."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/?rail_q=foo%20bar").text
    # Either %20 or + is acceptable URL-encoding for a space.
    assert (
        'q=foo%20bar' in body or 'q=foo+bar' in body or 'q=foo bar' in body
    )
