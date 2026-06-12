"""BX.16 (2026-06-11) — Inline chain shape-preview below the DSL field.

Operator-locked direction: a small server-rendered mini-diagram sits
INLINE below the chain edit form's children chip-list, refreshing as
the operator edits via HTMX. Reuses the BX.8 mini-diagram CSS +
wasm-graphviz infrastructure via a dedicated ``chain-shape-preview.js``
shim (separate from ``mini-diagram.js`` so the two renderers don't
fight over the same render target).

Shape-preview SPEC (drives the assertions):

  - Singleton child   ⇒ required — solid edge.
  - Multiple children ⇒ XOR alternation — dashed edges.
  - Per-child fan_in  ⇒ ``N:1`` (or ``N:1 (EPC=K)``) edge label.
  - Empty children    ⇒ no DOT, prompt "Add children to see the chain shape."
  - Empty parent      ⇒ placeholder ``(pick a parent)`` node so the
                        children fan-out is still visible.

Browser drivers locate via ``data-section-name`` / ``data-role`` —
never Tailwind class — per feedback_browser_drivers_user_facing_locators.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    """Copy spec_example.yaml so the cache load doesn't mutate the
    bundled fixture (mirrors the BX.8 test fixture pattern)."""
    src = FIXTURES_DIR / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


# ---------------------------------------------------------------------------
# Helpers — pure shape-rendering logic
# ---------------------------------------------------------------------------


def test_render_chain_shape_preview_inner_empty_state() -> None:
    """Empty children list ⇒ no DOT template, just the prompt fragment
    so the operator knows the preview will appear once they add a child."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_chain_shape_preview_inner,
    )
    html = _render_chain_shape_preview_inner(object(), "SomeParent", [])
    # Prompt visible.
    assert "Add children to see the chain shape" in html
    # Empty-state anchor for the browser driver.
    assert 'data-role="chain-shape-empty"' in html
    # No DOT template (shim short-circuits).
    assert "chain-shape-dot" not in html
    assert "chain-shape-target" not in html


def test_render_chain_shape_preview_inner_singleton_required_edge(
    writable_l2_yaml: Path,
) -> None:
    """Z.A grammar: one selected child = required — edge style is solid
    (no dashed style attribute, no `xor` label)."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_chain_shape_preview_inner,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    inst = L2InstanceCache.from_path(writable_l2_yaml).get()
    # Use any two rail names from the fixture.
    rails = list(getattr(inst, "rails", ()))
    assert len(rails) >= 2
    parent_name = str(getattr(rails[0], "name"))
    child_name = str(getattr(rails[1], "name"))
    html = _render_chain_shape_preview_inner(
        inst, parent_name, [(child_name, False, None)],
    )
    # DOT template present (rendered shape).
    assert 'id="chain-shape-dot"' in html
    assert 'id="chain-shape-target"' in html
    # Heading + caption.
    assert "Chain shape preview" in html
    # Embedded DOT carries no `xor` label and no `style=dashed`.
    assert " xor " not in html
    # The dashed-style assertion is loose to avoid false positives from
    # arbitrary DOT comments; check that no edge has style=dashed.
    assert "style=dashed" not in html


def test_render_chain_shape_preview_inner_multi_xor_dashed_edges(
    writable_l2_yaml: Path,
) -> None:
    """Z.A grammar: two+ children = XOR alternation — edges render as
    dashed with ``xor`` label so the operator sees the alternation
    semantic without reading the chip-list helper text."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_chain_shape_preview_inner,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    inst = L2InstanceCache.from_path(writable_l2_yaml).get()
    rails = list(getattr(inst, "rails", ()))
    assert len(rails) >= 3
    parent_name = str(getattr(rails[0], "name"))
    c1 = str(getattr(rails[1], "name"))
    c2 = str(getattr(rails[2], "name"))
    html = _render_chain_shape_preview_inner(
        inst, parent_name, [(c1, False, None), (c2, False, None)],
    )
    # Dashed XOR edge style propagates into the DOT source.
    assert "dashed" in html
    # `xor` label rides on the multi-child edges.
    assert "xor" in html


def test_render_chain_shape_preview_inner_fan_in_label() -> None:
    """``fan_in=True`` renders the N:1 marker on the edge. With ``epc``
    set, the marker includes ``EPC=<n>`` so the operator can verify the
    expected-parent-count value without opening the chip widget."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_chain_shape_preview_inner,
    )

    html_no_epc = _render_chain_shape_preview_inner(
        object(), "ParentRail", [("ChildTmpl", True, None)],
    )
    assert "N:1" in html_no_epc

    html_with_epc = _render_chain_shape_preview_inner(
        object(), "ParentRail", [("ChildTmpl", True, 3)],
    )
    assert "N:1" in html_with_epc
    assert "EPC=3" in html_with_epc


def test_render_chain_shape_preview_inner_missing_parent_placeholder() -> None:
    """When the parent select is still blank (operator hasn't picked
    yet), the preview shows a labeled placeholder node so the fan-out
    shape is still legible."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_chain_shape_preview_inner,
    )
    html = _render_chain_shape_preview_inner(
        object(), "", [("ChildA", False, None)],
    )
    assert "pick a parent" in html


def test_classify_chain_node_rail_vs_template(
    writable_l2_yaml: Path,
) -> None:
    """Node-id prefix parity with the main diagram: rails get
    ``rail__`` prefix and templates get ``tmpl__`` so a future
    chain-shape-preview navigate-on-click could route through the
    same ``_miniEditorUrlForNode`` arms ``mini-diagram.js`` uses."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _classify_chain_node,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    inst = L2InstanceCache.from_path(writable_l2_yaml).get()
    rails = list(getattr(inst, "rails", ()))
    templates = list(getattr(inst, "transfer_templates", ()))
    assert rails and templates
    rail_name = str(getattr(rails[0], "name"))
    tmpl_name = str(getattr(templates[0], "name"))
    assert _classify_chain_node(inst, rail_name) == "rail"
    assert _classify_chain_node(inst, tmpl_name) == "tmpl"
    # Unknown ⇒ defaults to "rail" (mid-typeahead names shouldn't crash).
    assert _classify_chain_node(inst, "ThisDoesNotExist_xx_zz") == "rail"


# ---------------------------------------------------------------------------
# Container wiring — HTMX wire + initial paint
# ---------------------------------------------------------------------------


def test_chain_shape_preview_container_htmx_wire(
    writable_l2_yaml: Path,
) -> None:
    """The wrapper carries the data-section-name anchor + the HTMX wire
    (POST to the preview endpoint on input changed, swap innerHTML)."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_chain_shape_preview_container,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    inst = L2InstanceCache.from_path(writable_l2_yaml).get()
    html = _render_chain_shape_preview_container(inst, [])
    # Container id + section-name anchor for the driver.
    assert 'id="chain-shape-preview"' in html
    assert 'data-section-name="chain-shape-preview"' in html
    # HTMX wire shape.
    assert 'hx-post="/l2_shape/chain/shape-preview"' in html
    assert "input changed delay:300ms" in html
    assert 'hx-target="#chain-shape-preview"' in html
    assert 'hx-swap="innerHTML"' in html
    # Initial paint — empty state when no children.
    assert "Add children to see the chain shape" in html


def test_chain_shape_preview_container_initial_paint_with_children(
    writable_l2_yaml: Path,
) -> None:
    """Initial paint includes the rendered shape (DOT template +
    render target) when the chain already has children — so the
    operator sees the saved state immediately on edit-page open
    without needing to trigger an HTMX swap."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _render_chain_shape_preview_container,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    inst = L2InstanceCache.from_path(writable_l2_yaml).get()
    html = _render_chain_shape_preview_container(
        inst, [("ChildA", False, None), ("ChildB", False, None)],
    )
    assert 'id="chain-shape-dot"' in html
    assert 'id="chain-shape-target"' in html
    # XOR semantic visible (2 children).
    assert "dashed" in html


# ---------------------------------------------------------------------------
# Chain edit form embeds the preview below the children field
# ---------------------------------------------------------------------------


def test_chain_edit_page_includes_shape_preview_below_children(
    writable_l2_yaml: Path,
) -> None:
    """The chain edit page must include the shape-preview container in
    the rendered HTML, positioned after the children chip-list (the
    DSL/children field) so the operator's eye moves "edit children →
    see shape" naturally."""
    starlette = pytest.importorskip("starlette")
    pytest.importorskip("graphviz")
    TestClient = pytest.importorskip("starlette.testclient").TestClient
    del starlette
    from recon_gen.common.html._smoke_app import (  # noqa: PLC0415
        SMOKE_FILTER_SPECS,
        build_smoke_app,
        stub_money_trail_fetcher,
    )
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        _url_entity_id,
    )
    from recon_gen.common.html._studio_routes import make_studio_routes  # noqa: PLC0415
    from recon_gen.common.html.server import ServedDashboard, make_app  # noqa: PLC0415
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415
    from tests._test_helpers import make_test_config  # noqa: PLC0415

    cache = L2InstanceCache.from_path(writable_l2_yaml)
    instance = cache.get()
    chains = list(getattr(instance, "chains", ()))
    if not chains:
        pytest.skip("spec_example.yaml has no chains to test against")
    chain = chains[0]
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
    url_id = _url_entity_id("chain", chain)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient stubs accept ASGI apps but make_app is typed Any
        resp = c.get(f"/l2_shape/chain/{url_id}/edit")
        assert resp.status_code == 200
        body = resp.text
        # Preview container present.
        assert 'id="chain-shape-preview"' in body
        assert 'data-section-name="chain-shape-preview"' in body
        # HTMX wire shape carried through.
        assert 'hx-post="/l2_shape/chain/shape-preview"' in body
        assert "input changed delay:300ms" in body
        # The dedicated shim is referenced.
        assert "chain-shape-preview.js" in body
        # Preview sits AFTER the children chip-list (DSL field). Both
        # markers appear; the preview marker must appear after the
        # chip-list marker so the operator's reading order is
        # "edit children → see shape preview".
        chip_list_marker = 'data-multiselect-order-list="children"'
        assert chip_list_marker in body
        assert body.index(chip_list_marker) < body.index(
            'id="chain-shape-preview"',
        )


def test_chain_edit_page_only_loads_shape_preview_js() -> None:
    """The dedicated shim ships only on chain edit pages — rail / theme /
    instance / template / limit_schedule / account edit pages must NOT
    pull it in (keep payload lean per BX.8 mini-diagram CSS pattern)."""
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

    cache = L2InstanceCache.from_path(
        FIXTURES_DIR / "spec_example.yaml",
    )
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
        assert "chain-shape-preview.js" not in resp.text


# ---------------------------------------------------------------------------
# Preview endpoint — POST /l2_shape/chain/shape-preview
# ---------------------------------------------------------------------------


def test_chain_shape_preview_endpoint_renders_dot_for_children(
    writable_l2_yaml: Path,
) -> None:
    """POST with parent + children form fields ⇒ rendered fragment with
    the DOT template + render target (the shim then layouts it
    client-side via wasm-graphviz)."""
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
    inst = cache.get()
    rails = list(getattr(inst, "rails", ()))
    assert len(rails) >= 3
    parent_name = str(getattr(rails[0], "name"))
    child_a = str(getattr(rails[1], "name"))
    child_b = str(getattr(rails[2], "name"))
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
        resp = c.post(
            "/l2_shape/chain/shape-preview",
            data={
                "parent": parent_name,
                "children": [child_a, child_b],
            },
        )
        assert resp.status_code == 200
        body = resp.text
        # DOT template + render target swap in.
        assert 'id="chain-shape-dot"' in body
        assert 'id="chain-shape-target"' in body
        # Multi-child XOR shape (dashed edges).
        assert "dashed" in body


def test_chain_shape_preview_endpoint_returns_empty_state(
    writable_l2_yaml: Path,
) -> None:
    """POST with no children ⇒ empty-state prompt fragment (no DOT,
    no render target). Validates the shim short-circuit path."""
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
        resp = c.post(
            "/l2_shape/chain/shape-preview",
            data={"parent": "AnyRail"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "Add children to see the chain shape" in body
        assert 'data-role="chain-shape-empty"' in body
        assert "chain-shape-dot" not in body


def test_chain_shape_preview_endpoint_threads_fan_in_and_epc(
    writable_l2_yaml: Path,
) -> None:
    """POST with `fan_in_<name>` + `epc_<name>` siblings ⇒ rendered
    DOT carries the N:1 label suffix (with EPC=K when set)."""
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
        resp = c.post(
            "/l2_shape/chain/shape-preview",
            data={
                "parent": "ParentRail",
                "children": "BatchedPayout",
                "fan_in_BatchedPayout": "true",
                "epc_BatchedPayout": "5",
            },
        )
        assert resp.status_code == 200
        body = resp.text
        assert "N:1" in body
        assert "EPC=5" in body


# ---------------------------------------------------------------------------
# Asset / shim source-level gates
# ---------------------------------------------------------------------------


def test_chain_shape_preview_js_re_renders_on_htmx_swap() -> None:
    """The shim must listen for `htmx:afterSwap` against the
    `#chain-shape-preview` container so every HTMX swap re-runs the
    wasm-graphviz layout against the freshly-swapped DOT template.
    Without the listener, the first swap renders correctly but
    subsequent edits leave the operator looking at stale SVG."""
    shim = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "chain-shape-preview.js"
    ).read_text()
    assert "htmx:afterSwap" in shim
    assert "chain-shape-preview" in shim
    # Reads the freshly-swapped DOT from the template element.
    assert "chain-shape-dot" in shim
    # Renders into the dedicated target div.
    assert "chain-shape-target" in shim
    # Reuses the wasm-graphviz path mini-diagram.js uses.
    assert "wasm-graphviz" in shim
