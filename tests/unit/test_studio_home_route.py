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
    """GET / returns the editor home page — one <details> per editable
    entity kind, no embedded diagram (CF.3.l promoted the diagram to a
    sibling top-level surface)."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        resp = c.get("/")
        assert resp.status_code == 200
        body = resp.text

    # CF.3.l — the diagram iframe was removed; Diagram is its own page
    # reachable via the top-nav. (The section-body `hx-get` calls still
    # ask for `?embed=1` fragments — that's the entity-list lazy load,
    # not the diagram.)
    assert 'id="diagram-frame"' not in body
    assert "<iframe" not in body  # no iframe of any kind on the editor home

    # All six entity kinds get a <details> with the right data-kind.
    for kind in (
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    ):
        assert f'data-kind="{kind}"' in body, f"missing section for {kind}"


def test_home_page_each_section_carries_add_button(
    writable_l2_yaml: Path,
) -> None:
    """X.4.f.9 — every section's <summary> exposes a "+ Add" link
    that hx-gets the kind's blank form into the section body."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    for kind in (
        "account", "account_template", "rail",
        "transfer_template", "chain", "limit_schedule",
    ):
        # Plain navigation to the dedicated create page — the create
        # page has room for per-kind training prose explaining what
        # this entity is + field-level guidance, which a cramped
        # inline form couldn't carry.
        assert f'href="/l2_shape/{kind}/new"' in body, (
            f"missing + Add for {kind}"
        )
    # stopPropagation prevents the click from toggling the surrounding
    # <details> closed (browser still follows the href).
    assert "event.stopPropagation()" in body


def test_home_page_lists_instance_settings_singleton(
    writable_l2_yaml: Path,
) -> None:
    """AI.2.c — the home page surfaces the new top-level "Instance
    settings" singleton (description + institution_name +
    institution_acronym) alongside Theme / Persona, with an Edit link
    to its form."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    assert 'data-kind="instance"' in body
    assert "Instance settings" in body
    assert 'href="/l2_shape/instance/"' in body


def test_home_page_no_legacy_deploy_button(
    writable_l2_yaml: Path,
) -> None:
    """CF.4 followup (2026-06-05) — the X.4.g.14 "Studio · <prefix> /
    Deploy changes" header strip was dropped on operator request
    (it duplicated info the top-nav carries, and the deploy button
    is a chrome distraction for the editor surface). Deploy is
    reachable via `recon-gen json apply --execute`; the in-page
    button + `quicksightDeploy` JS handler are gone."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert 'id="deploy-btn"' not in body
    assert "quicksightDeploy" not in body
    assert 'id="deploy-status"' not in body


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


def test_home_page_drops_iframe_supporting_js(
    writable_l2_yaml: Path,
) -> None:
    """CF.3.l — the iframe-cascade-reload listener + iframe-focus
    filter pipeline + click-to-focus iframe-URL mutator are all gone.
    The home page no longer reaches into a diagram iframe."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text

    # Iframe-cascade-reload bump (gone with the iframe).
    assert "addEventListener('l2-cascade-reload'" not in body
    assert "getElementById('diagram-frame')" not in body
    assert "f.src = f.src" not in body
    # X.4.f.8 iframe-focus filter pipeline (gone).
    assert "applyFocusFilter" not in body
    assert "refreshFocusFromIframe" not in body
    assert "/diagram/visible?focus=" not in body
    # X.4.f.8.reverse click-to-focus iframe-URL mutator (gone).
    assert "_focusDiagramOnNode" not in body


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

    # Wrapper present; no full-document chrome. AM.1 step 6
    # (2026-05-25): `.entity-list` semantic class retired; the
    # `data-kind="account"` attribute on the wrapper is the stable
    # hook the home-page JS reads.
    assert 'data-kind="account"' in body
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


def test_diagram_embed_mode_drops_sidebar_chrome(
    writable_l2_yaml: Path,
) -> None:
    """``?embed=1`` is for external embedders (Studio no longer iframes
    the diagram post-CF.3.l). Embed mode drops the floating sidebar so
    the host page can render its own chrome; standalone mode keeps it.
    """
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        embedded = c.get("/diagram?embed=1").text
        standalone = c.get("/diagram").text

    # CF.3.m — standalone diagram carries the floating sidebar
    # (`<details id="diagram-sidebar">`); embed mode drops it.
    assert 'id="diagram-sidebar"' in standalone
    assert 'id="diagram-sidebar"' not in embedded


def test_card_titles_are_plain_text_not_diagram_links(
    writable_l2_yaml: Path,
) -> None:
    """CF.4 followup (2026-06-05) — card titles are plain `<h3>`. The
    earlier title-as-diagram-focus link (CF.3.l) was dropped: jumping
    out of the editor surface on what looked like a heading was
    surprising. The Edit button next to the title is the explicit
    affordance; the Diagram is reachable from the top-level nav."""
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        for kind in (
            "account", "account_template", "rail", "transfer_template",
            "chain", "limit_schedule",
        ):
            body = c.get(f"/l2_shape/{kind}/?embed=1").text
            # No /diagram?focus=… anchors anywhere in the card grid.
            assert "/diagram?focus=" not in body, (
                f"{kind} embed leaks a /diagram?focus= link"
            )
            # The pre-CF.3.l simulated-button attributes also stay gone.
            assert "data-focus-node=" not in body
            assert 'role="button"' not in body


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


def test_put_from_home_page_redirects_to_read_card(
    writable_l2_yaml: Path,
) -> None:
    """BX.2 (2026-06-11) — a successful save (POST/PUT) 303-redirects to
    the entity's read card by default (operator stays in the editing
    flow). Pre-BX.2 default was the home page."""
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
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        assert resp.headers.get("location") == "/l2_shape/account/cust-001"


# ---------------------------------------------------------------------------
# BS.3 part 3 — shared top-nav injection into Studio pages.
# ---------------------------------------------------------------------------


def _build_app_with_top_nav(yaml_path: Path) -> object:
    """Studio app wired with a top_nav_fn closure — same shape
    ``cli/_html_serve.py`` builds in the real serve path."""
    from recon_gen.common.html._studio_routes import make_studio_routes
    from recon_gen.common.html.render import (
        build_top_nav_entries,
        emit_top_nav,
    )
    cache = L2InstanceCache.from_path(yaml_path)
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="Smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    nav_entries = build_top_nav_entries(
        [("smoke", "Smoke")],
        studio_enabled=True,
        docs_url="/docs/",
    )

    def _top_nav(active_href: str) -> str:
        return emit_top_nav(entries=nav_entries, active_href=active_href)

    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache, top_nav_fn=_top_nav),
    )


def test_studio_home_page_renders_shared_top_nav(
    writable_l2_yaml: Path,
) -> None:
    """BS.3 part 3: GET / renders the shared top-nav before its
    page-local header, with /  (the home page) flagged active.

    CF.3.l: top-nav surface now includes Diagram as the first
    authoring entry (left of L2 Editor)."""
    app = _build_app_with_top_nav(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/").text
    assert ">Diagram<" in body
    assert ">L2 Editor<" in body
    assert ">ETL Support<" in body
    assert ">Training<" in body
    assert ">Smoke<" in body
    assert ">Docs<" in body
    # Diagram entry sits LEFT of L2 Editor.
    assert body.index(">Diagram<") < body.index(">L2 Editor<")


def test_studio_diagram_page_renders_shared_top_nav_when_not_embedded(
    writable_l2_yaml: Path,
) -> None:
    """BS.3 part 3: GET /diagram (standalone) renders the top-nav.
    The ?embed=1 variant suppresses it (kept post-CF.3.l for external
    embedders even though Studio no longer iframes the diagram)."""
    app = _build_app_with_top_nav(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        standalone = c.get("/diagram").text
        embedded = c.get("/diagram?embed=1").text
    assert ">Diagram<" in standalone
    assert ">L2 Editor<" in standalone
    assert ">Smoke<" in standalone
    # Embedded variant drops the nav.
    assert ">L2 Editor<" not in embedded


def test_studio_data_page_renders_shared_top_nav(
    writable_l2_yaml: Path,
) -> None:
    """BS.3 part 3: GET /data renders the top-nav."""
    app = _build_app_with_top_nav(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        body = c.get("/data").text
    assert ">L2 Editor<" in body
    assert ">Smoke<" in body
    assert ">Docs<" in body


def test_studio_pages_no_top_nav_when_factory_not_supplied(
    writable_l2_yaml: Path,
) -> None:
    """The default (no top_nav_fn) path emits the same pages without the
    shared nav — used by unit-test surfaces that don't construct the
    closure. Verifies the kwarg is optional, not load-bearing."""
    # Use the original _build_app helper (no top_nav_fn).
    app = _build_app(writable_l2_yaml)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but the inferred return type from make_app is Any
        home = c.get("/").text
        diagram = c.get("/diagram").text
        data = c.get("/data").text
    # No shared-nav root element (only emitted by `emit_top_nav` when
    # top_nav_fn is supplied). The home page's intro <header> + h1
    # still anchors identity.
    for body in (home, diagram, data):
        assert 'aria-label="App nav"' not in body
        assert ">Recon-Gen<" not in body
    # Sanity: home still carries its page-local intro chrome.
    assert "L2 Editor" in home
    assert 'id="home-intro"' in home
    assert 'id="diagram-sidebar"' in diagram
    assert "Studio · data shaping" in data
