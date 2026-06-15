"""BX.8 (2026-06-11) — Diagram hover-Edit badge + inline mini-diagram
on edit pages.

Direction D2 from `docs/audits/bx_0_8_design_mockups/bx_8.md`:

  - Hover-revealed "Edit" badge in the upper-right of each editable
    diagram node (suppressed on bundles + shared roles). Keyboard
    focus reveal piggybacks on `:focus-within`. Anchor carries
    `data-role="diagram-edit-link"` + `data-edit-href=<url>`.
  - Inline server-rendered mini-diagram on each edit page (focused
    1-hop neighborhood). Self-node tinted via `class="self"` +
    `data-role="mini-diagram-self"`. Skipped for kinds with no
    topology projection (limit_schedule per operator Q3, theme,
    instance).
  - Click-to-focus on the main diagram preserved.
  - Mini-diagram absent when topology cannot project — limit_schedule
    edit page renders no mini section.

Browser drivers locate via data-attribute, not Tailwind class — per
`feedback_browser_drivers_user_facing_locators` memory.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from recon_gen.common.html._studio_routes import _focus_node_id_for_entity


FIXTURES_DIR = Path(__file__).parent.parent / "l2"


# ---------------------------------------------------------------------------
# Entity → focus-node id mapping (the BX.8 inverse for mini-diagram)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, attrs, expected",
    [
        # Rails: bijective — same arm the main diagram uses.
        ("rail", {"name": "ExternalRailInbound"}, "rail__ExternalRailInbound"),
        # Transfer templates: bijective.
        (
            "transfer_template",
            {"name": "CustomerSettlement"},
            "tmpl__CustomerSettlement",
        ),
        # Accounts project to their role node (multiple accounts may
        # share a role; the focus 1-hop will surface all of them).
        ("account", {"role": "CustomerSubledger"}, "role__CustomerSubledger"),
        # Account templates: same role projection.
        ("account_template", {"role": "DDAControl"}, "role__DDAControl"),
        # Chain: center on the parent rail.
        ("chain", {"parent": "CustomerInboundACH"}, "rail__CustomerInboundACH"),
        # limit_schedule has no clean topology projection (Q3 lock).
        (
            "limit_schedule",
            {"parent_role": "DDAControl", "rail": "X", "direction": "outbound"},
            None,
        ),
        # Singletons — no per-entity node.
        ("theme", {}, None),
        ("instance", {}, None),
    ],
)
def test_focus_node_id_for_entity(
    kind: str, attrs: dict[str, str], expected: str | None,
) -> None:
    class _Entity:
        def __init__(self, **kw: str) -> None:
            for k, v in kw.items():
                setattr(self, k, v)
    entity = _Entity(**attrs)
    assert _focus_node_id_for_entity(kind, entity) == expected


def test_focus_node_id_returns_none_when_addressing_key_blank() -> None:
    """Defensive: an entity with a blank name / role should never map
    to a half-formed focus id (`rail__`, `role__`). Mini-diagram
    helper short-circuits on None instead of feeding a broken id into
    the graphviz builder."""
    class _E:
        name = ""
        role = ""
        parent = ""
    e = _E()
    assert _focus_node_id_for_entity("rail", e) is None
    assert _focus_node_id_for_entity("transfer_template", e) is None
    assert _focus_node_id_for_entity("account", e) is None
    assert _focus_node_id_for_entity("chain", e) is None


# ---------------------------------------------------------------------------
# Hover-Edit badge — diagram.js source-level gates
# ---------------------------------------------------------------------------


def test_diagram_js_emits_hover_edit_badge_via_injector() -> None:
    """The `diagram.js` shim must call `_injectEditBadge` for every
    editable node and tag the anchor with `data-role="diagram-edit-link"`
    so browser drivers can find it without leaning on Tailwind classes."""
    diagram_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "diagram.js"
    ).read_text()
    # The injector exists and is wired into the node-annotation loop.
    assert "function _injectEditBadge(" in diagram_js
    assert "_injectEditBadge(g, editHref, id)" in diagram_js
    # Suppressed on null editor-url (bundles + roles defer to no
    # affordance — same rule the right-click menu uses).
    assert "const editHref = _editorUrlForNode(title);" in diagram_js
    # The anchor wears the locator hook + the URL it'll navigate to.
    assert "'data-role', 'diagram-edit-link'" in diagram_js
    # DJ.3 (2026-06-15): URL is `safeHref` (validated through
    # _validateHttpUrl) instead of the raw `href` arg — CodeQL
    # js/xss-through-dom hardening.
    assert "'data-edit-href', safeHref" in diagram_js
    # Keyboard reachability: tabindex=0 so :focus-within reveals the
    # badge for keyboard navigation.
    assert "'tabindex', '0'" in diagram_js
    # aria-label so screen readers hear "Edit <id>", not just "Edit".
    assert "aria-label" in diagram_js


def test_diagram_js_focus_click_skips_when_target_inside_edit_badge() -> None:
    """Click on the hover-Edit badge anchor must let the browser's
    native <a> navigation fire — not be preempted by the existing
    `_navigateToFocus` handler. The check is `target.closest('.edit-badge')`
    inside the node click handler."""
    diagram_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "diagram.js"
    ).read_text()
    assert ".closest('.edit-badge')" in diagram_js


def test_diagram_css_hides_edit_badge_by_default_reveals_on_hover() -> None:
    """The CSS rule reveals the badge only on hover OR keyboard focus
    (covering `:focus` and `:focus-within`). Default display is none."""
    css = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "diagram-svg.css"
    ).read_text()
    # Default hidden.
    assert ".topology-svg g.node .edit-badge" in css
    # Reveal on hover.
    assert ".topology-svg g.node:hover .edit-badge" in css
    # Reveal on keyboard focus (focus-within picks up the anchor child).
    assert "focus-within" in css


# ---------------------------------------------------------------------------
# Mini-diagram — render_mini_diagram_html
# ---------------------------------------------------------------------------


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml so the cache load doesn't mutate the
    bundled fixture (we don't mutate here, but the test pattern
    matches the rest of the editor-route suite)."""
    src = FIXTURES_DIR / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


def test_render_mini_diagram_embeds_svg_template_with_self_id(
    writable_l2_yaml: Path,
) -> None:
    """For a rail edit page, the mini-diagram fragment carries:
      - the `data-role="mini-diagram"` wrapper for browser-driver lookup
      - `data-self-id="rail__<name>"` so the JS shim can self-highlight
      - a `<template id="mini-topology-dot">` carrying the focused DOT
      - an "Open full diagram" anchor with `data-role="mini-diagram-open-full"`
    """
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import render_mini_diagram_html  # noqa: PLC0415
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    instance = cache.get()
    # Look up an actual rail from the loaded instance — spec_example
    # carries ExternalRailInbound at the top of `rails:`.
    rails = list(getattr(instance, "rails", ()))
    assert rails, "spec_example.yaml should declare at least one rail"
    rail = rails[0]
    rail_name = str(getattr(rail, "name"))
    html = render_mini_diagram_html(instance, "rail", rail)
    assert html, "rail edit page should render a mini-diagram section"
    assert 'data-role="mini-diagram"' in html
    assert f'data-self-id="rail__{rail_name}"' in html
    # The DOT source lives in a <template> (so the JS shim can read
    # it via dotTemplate.content.textContent without rendering it on
    # initial paint).
    assert 'id="mini-topology-dot"' in html
    assert "<template" in html
    # The "Open full diagram" anchor preserves the focus + layer.
    assert 'data-role="mini-diagram-open-full"' in html
    assert f"focus=rail__{rail_name}" in html
    # The JS shim is referenced (asset_url adds a cachebust query
    # string — match the path prefix without that).
    assert "mini-diagram.js" in html
    # The render target the shim writes into.
    assert 'id="mini-diagram-target"' in html


def test_render_mini_diagram_returns_empty_for_limit_schedule() -> None:
    """Operator Q3 lock (bx_8.md): limit_schedule has no clean topology
    projection (it's a constraint on a rail triple, not a node). The
    helper returns empty string so the edit page omits the mini section
    entirely — no half-broken "could not render" placeholder."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import render_mini_diagram_html  # noqa: PLC0415

    class _LS:
        parent_role = "DDAControl"
        rail = "ExternalRailInbound"
        direction = "outbound"
    assert render_mini_diagram_html(object(), "limit_schedule", _LS()) == ""


def test_render_mini_diagram_returns_empty_for_singletons() -> None:
    """Singletons (theme, instance) have no per-entity diagram node;
    return empty string."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import render_mini_diagram_html  # noqa: PLC0415

    assert render_mini_diagram_html(object(), "theme", object()) == ""
    assert render_mini_diagram_html(object(), "instance", object()) == ""


def test_render_mini_diagram_swallows_topology_errors_gracefully() -> None:
    """A focus_node_id that doesn't exist in the graph (stale id, rename
    mid-flight) must not crash the edit page render. The helper catches
    and returns empty string so the operator can keep editing."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import render_mini_diagram_html  # noqa: PLC0415
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    instance = cache.get()

    class _PhantomRail:
        name = "ThisRailDoesNotExistInTheFixture_xx_zz"
    # Should return either empty (graceful) OR a valid fragment — the
    # contract is "don't crash". Build the focused graph would either
    # raise KeyError (caught) or return a tiny degenerate graph.
    result = render_mini_diagram_html(instance, "rail", _PhantomRail())
    # Acceptable shapes: empty string OR a wrapper that includes the
    # standard data-role anchor. Crash = test failure.
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Edit page embeds the mini-diagram section (integration)
# ---------------------------------------------------------------------------


def test_edit_page_includes_mini_diagram_for_rail(
    writable_l2_yaml: Path,
) -> None:
    """Rail edit page must inline the mini-diagram section above the
    form. The diagram-svg.css is pulled in only when the mini is
    present (avoids loading 105 lines of CSS on theme / instance /
    limit_schedule edit pages)."""
    starlette = pytest.importorskip("starlette")
    pytest.importorskip("graphviz")
    TestClient = pytest.importorskip("starlette.testclient").TestClient
    del starlette
    from recon_gen.common.html._smoke_app import (  # noqa: PLC0415
        SMOKE_FILTER_SPECS,
        build_smoke_app,
        stub_money_trail_fetcher,
    )
    from recon_gen.common.html._studio_routes import make_studio_routes  # noqa: PLC0415
    from recon_gen.common.html.server import ServedDashboard, make_app  # noqa: PLC0415
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415
    from tests._test_helpers import make_test_config  # noqa: PLC0415

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    instance = cache.get()
    rails = list(getattr(instance, "rails", ()))
    assert rails
    rail_name = str(getattr(rails[0], "name"))
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    app = make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but make_app is typed Any
        resp = c.get(f"/l2_shape/rail/{rail_name}/edit")
        assert resp.status_code == 200
        body = resp.text
        # Mini-diagram section present.
        assert 'data-role="mini-diagram"' in body
        assert f'data-self-id="rail__{rail_name}"' in body
        # diagram-svg.css is linked (carries the self-highlight rule).
        assert "diagram-svg.css" in body
        # mini-diagram.js is referenced.
        assert "mini-diagram.js" in body


def test_edit_page_omits_mini_diagram_for_limit_schedule(
    writable_l2_yaml: Path,
) -> None:
    """limit_schedule edit page must NOT carry a mini-diagram section
    (operator Q3 — no clean topology projection). The diagram-svg.css
    + mini-diagram.js are correspondingly omitted to keep the page
    payload lean."""
    starlette = pytest.importorskip("starlette")
    pytest.importorskip("graphviz")
    TestClient = pytest.importorskip("starlette.testclient").TestClient
    del starlette
    from recon_gen.common.html._smoke_app import (  # noqa: PLC0415
        SMOKE_FILTER_SPECS,
        build_smoke_app,
        stub_money_trail_fetcher,
    )
    from recon_gen.common.html._studio_routes import make_studio_routes  # noqa: PLC0415
    from recon_gen.common.html.server import ServedDashboard, make_app  # noqa: PLC0415
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415
    from tests._test_helpers import make_test_config  # noqa: PLC0415

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    instance = cache.get()
    # Find a limit_schedule in the fixture, if any.
    schedules = list(getattr(instance, "limit_schedules", ()) or ())
    if not schedules:
        pytest.skip("spec_example.yaml has no limit_schedules to test against")
    ls = schedules[0]
    cfg = make_test_config()
    tree_app, sheet = build_smoke_app(cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    app = make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache),
    )
    # Compute the URL via the same `_url_entity_id` the route uses so
    # the path matches whatever opaque shape BX.10 emitted for this kind.
    from recon_gen.common.html._studio_editor_routes import _url_entity_id  # noqa: PLC0415

    url_id = _url_entity_id("limit_schedule", ls)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but make_app is typed Any
        resp = c.get(f"/l2_shape/limit_schedule/{url_id}/edit")
        assert resp.status_code == 200
        body = resp.text
        # No mini-diagram section.
        assert 'data-role="mini-diagram"' not in body
        # No mini-diagram JS module reference either.
        assert "mini-diagram.js" not in body


# ---------------------------------------------------------------------------
# mini-diagram.js — source-level gates (parity with diagram.js arms)
# ---------------------------------------------------------------------------


def test_mini_diagram_js_self_highlight_class_and_data_role() -> None:
    """The mini-diagram shim must tag the matching `<g.node>` with
    BOTH `class="self"` (CSS hook) and `data-role="mini-diagram-self"`
    (browser-driver hook) so the self-node is locatable without
    relying on Tailwind / SVG-internal classes."""
    mini_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "mini-diagram.js"
    ).read_text()
    assert "'data-role', 'mini-diagram-self'" in mini_js
    assert "self" in mini_js
    # Match the wrapper's data-self-id to drive the loop.
    assert "data-self-id" in mini_js


def test_mini_diagram_js_editor_url_arms_match_diagram_js() -> None:
    """Drift between the three inverse implementations (Python,
    diagram.js, mini-diagram.js) is a UX bug. Pin the prefix arms here
    just like `test_diagram_js_inverse_arms_match_python` does for
    diagram.js."""
    mini_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "mini-diagram.js"
    ).read_text()
    for prefix in (
        '"rail__bundle_"',
        '"rail__"',
        '"tmpl__"',
        '"role__"',
    ):
        assert prefix in mini_js, (
            f"mini-diagram.js _miniEditorUrlForNode is missing the {prefix} "
            "arm — Python / diagram.js / mini-diagram.js must agree"
        )
    # Resolvable arms point at the /edit form, not the list.
    assert "/l2_shape/rail/" in mini_js
    assert "/l2_shape/transfer_template/" in mini_js
    assert "/edit" in mini_js
