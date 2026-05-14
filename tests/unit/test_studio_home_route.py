"""Studio home page integration tests (X.4.f.7).

Locks the contract for the unified Studio home page: GET ``/`` renders
a single page composing the diagram (iframe) + every editable entity
kind (lazy-loaded ``<details>`` sections), with HX-Trigger fan-out
wiring on each container so a save in any section refreshes the
diagram + every section together.

The browser-level "iframe actually reloaded" check needs Playwright;
TestClient covers the wiring assertion (the right ``hx-trigger`` /
``hx-get`` selectors land in the rendered HTML, the listener for the
iframe reload is present in the inline script) and the server-side
contract (``?embed=1`` returns a fragment, save returns the cascade
trigger header).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.l2.cache import L2InstanceCache
from tests._test_helpers import make_test_config


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml to a tempfile so PUT writes don't mutate
    the bundled fixture."""
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def _build_app(yaml_path: Path) -> object:
    """Studio app — same shape as test_studio_editor_routes uses."""
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


# ---------------------------------------------------------------------------
# Home page render shape
# ---------------------------------------------------------------------------


def test_home_page_renders_diagram_iframe_and_six_entity_sections(
    writable_l2_yaml: Path,
) -> None:
    """GET / returns the unified home page: diagram iframe + a <details>
    for each of the 6 editable entity kinds."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/")
        assert resp.status_code == 200
        body = resp.text

    # Diagram iframe is present and points at the existing /diagram route.
    assert 'id="diagram-frame"' in body
    assert 'src="/diagram?layer=1"' in body

    # All six entity kinds get a <details> with the right data-kind.
    for kind in (
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    ):
        assert f'data-kind="{kind}"' in body, f"missing section for {kind}"


def test_home_page_first_section_open_default_others_collapsed(
    writable_l2_yaml: Path,
) -> None:
    """The first <details> renders with the ``open`` attribute; the
    others render closed so a 7-rail / 30-account L2 isn't an
    unbroken wall on first paint."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    # Account is the first section per _HOME_SECTIONS order.
    assert 'data-kind="account" open' in body
    # Every other kind appears WITHOUT the open attribute.
    for kind in (
        "account_template", "rail", "transfer_template",
        "chain", "limit_schedule",
    ):
        assert f'data-kind="{kind}" open' not in body, (
            f"{kind} section should be collapsed by default"
        )
        # Sanity: it does appear (just without `open`).
        assert f'data-kind="{kind}"' in body


def test_home_page_sections_wire_lazy_load_and_cascade_reload(
    writable_l2_yaml: Path,
) -> None:
    """Each section's inner div carries the right hx-get + hx-trigger
    pair so it lazy-loads on render AND refetches when ANY save fires
    HX-Trigger: l2-cascade-reload."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    for kind in (
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    ):
        # The inner div fetches the editor route's embed fragment.
        assert f'hx-get="/l2_shape/{kind}/?embed=1"' in body, (
            f"missing hx-get for {kind} section"
        )
    # The trigger pair is shared across all sections — assert it appears
    # at least once per kind (6 sections → 6 occurrences).
    occurrences = body.count(
        'hx-trigger="load, l2-cascade-reload from:body"',
    )
    assert occurrences == 6, (
        f"expected 6 cascade-reload triggers (one per section), "
        f"got {occurrences}"
    )


def test_home_page_includes_iframe_cascade_reload_listener(
    writable_l2_yaml: Path,
) -> None:
    """Diagram iframe is its own document context; HTMX doesn't forward
    HX-Trigger events into iframes. The home page's inline JS must
    listen for the cascade event on document and bump iframe.src to
    force a same-origin reload."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    assert "addEventListener('l2-cascade-reload'" in body
    # The reload mechanism must reach the iframe by id.
    assert "getElementById('diagram-frame')" in body
    # Reassigning src=src forces the reload (vs. setting a new URL).
    assert "f.src = f.src" in body


# ---------------------------------------------------------------------------
# Embed-fragment route + cascade trigger header
# ---------------------------------------------------------------------------


def test_l2_shape_embed_returns_cards_fragment_no_html_chrome(
    writable_l2_yaml: Path,
) -> None:
    """GET /l2_shape/<kind>/?embed=1 returns just the cards container —
    no <html>/<head>/<body>. The home page already loads htmx + the
    editor CSS in its own <head>, so the embed fragment skips them."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/?embed=1")
        assert resp.status_code == 200
        body = resp.text

    # Wrapper present; no full-document chrome.
    assert '<div class="entity-list" data-kind="account">' in body
    assert "<!doctype" not in body.lower()
    assert "<html" not in body
    assert "<head>" not in body
    assert "<body" not in body
    # Cards still render — pick a known account from spec_example.
    assert "cust-001" in body


def test_l2_shape_no_embed_query_returns_full_page(
    writable_l2_yaml: Path,
) -> None:
    """Backwards compat — the existing /l2_shape/<kind>/ route (no
    ?embed=1) keeps returning the full HTML page so deep-links from
    the home page's ↗ section-link still work."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/l2_shape/account/")
        assert resp.status_code == 200
        body = resp.text

    assert "<!doctype" in body.lower()
    assert "<html" in body
    assert "<head>" in body
    assert "<body" in body
    assert "cust-001" in body


def test_diagram_visible_route_returns_full_set_when_no_focus(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.8 — GET /diagram/visible (no ?focus=) returns every entity
    of every kind, sorted, as JSON. The home page treats this as the
    "no filter" baseline."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/diagram/visible")
        assert resp.status_code == 200
        body = resp.json()

    # Every kind key present; account list includes spec_example's known IDs.
    assert set(body.keys()) == {
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    }
    assert "cust-001" in body["account"]
    assert "ExternalRailInbound" in body["rail"]


def test_diagram_visible_route_filters_by_focus(
    writable_l2_yaml: Path,
) -> None:
    """?focus=role__CustomerSubledger narrows to entities reachable
    from that node (rails touching the role + sibling subledger
    accounts + the AccountTemplate)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/diagram/visible?focus=role__CustomerSubledger")
        assert resp.status_code == 200
        body = resp.json()

    accounts = set(body["account"])
    rails = set(body["rail"])
    assert "cust-001" in accounts
    assert "cust-002" in accounts
    assert "ExternalRailInbound" in rails
    # NorthPool isn't connected to CustomerSubledger.
    assert "north-pool" not in accounts


def test_home_page_carries_diagram_filter_listener(
    writable_l2_yaml: Path,
) -> None:
    """The home page's inline JS must wire iframe-load → fetch
    /diagram/visible → toggle .is-hidden-by-focus on cards."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    # Iframe load listener present.
    assert "addEventListener('load', refreshFocusFromIframe)" in body
    # The fetch URL points at the new route.
    assert "/diagram/visible?focus=" in body
    # Hide-class application is in the script.
    assert "is-hidden-by-focus" in body
    # Re-apply on cascade-driven HTMX swap so the filter survives refetch.
    assert "addEventListener('htmx:afterSettle', applyFocusFilter)" in body


def test_home_page_cards_carry_data_attributes_for_filter(
    writable_l2_yaml: Path,
) -> None:
    """Cards in the home-page sections must expose data-kind +
    data-entity-id so the JS filter can target them."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        # The embed fragment is what each section actually loads.
        body = c.get("/l2_shape/account/?embed=1").text

    assert 'data-kind="account"' in body
    assert 'data-entity-id="cust-001"' in body


def test_put_from_home_page_emits_cascade_trigger_for_diagram_and_sections(
    writable_l2_yaml: Path,
) -> None:
    """Server-side contract: a successful PUT against any editor route
    returns ``HX-Trigger: l2-cascade-reload``. The home page's section
    divs (assert above) and the iframe listener (assert above) consume
    that trigger to refetch — this test pins the wire-side half."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.put(
            "/l2_shape/account/cust-001",
            data={
                "id": "cust-001",
                "scope": "internal",
                "name": "Customer One — home edited",
                "role": "CustomerSubledger",
                "parent_role": "CustomerLedger",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("HX-Trigger") == "l2-cascade-reload"
