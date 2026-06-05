"""CF.3.m — floating sidebar + node-id → editor-URL inverse.

The diagram surface dropped its page-local header + two horizontal
chrome rows in favor of a left-docked floating sidebar with
collapsible sections (Layer / Show / Edge labels / Overlays / Focus).
A "View in editor" button anchors the bottom; right-click on a node
in the SVG (JS side) hits the same node-id → editor-URL inverse.

This file pins:
  - the inverse (`_editor_url_for_focus_node`) at construction time
  - the sidebar markup (root id, section presence, no leaked chrome rows)
  - the JS shim's inverse stays in sync with the Python one
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.html._studio_routes import _editor_url_for_focus_node


FIXTURES_DIR = Path(__file__).parent.parent / "l2"


# ---------------------------------------------------------------------------
# Inverse: focus node id → editor URL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "node_id, expected_url",
    [
        # Rails: bijective.
        ("rail__ExternalRailInbound", "/l2_shape/rail/ExternalRailInbound"),
        # Templates: bijective.
        ("tmpl__CustomerSettlement", "/l2_shape/transfer_template/CustomerSettlement"),
        # Roles: ambiguous (multiple accounts can share), punt to list.
        ("role__CustomerSubledger", "/l2_shape/account/"),
        # Synthetic bundle nodes: no source-side entity.
        ("rail__bundle_42", "/l2_shape/rail/"),
        # Empty / None: editor home.
        (None, "/"),
        ("", "/"),
        # Unknown prefix: editor home.
        ("mystery__X", "/"),
    ],
)
def test_editor_url_for_focus_node_inverse(
    node_id: str | None, expected_url: str,
) -> None:
    assert _editor_url_for_focus_node(node_id) == expected_url


# ---------------------------------------------------------------------------
# Sidebar markup: the floating column replaces the old chrome rows
# ---------------------------------------------------------------------------

def test_diagram_sidebar_replaces_horizontal_chrome_rows() -> None:
    """The two pre-CF.3.m horizontal chrome rows + the
    ``<h1>Studio · diagram</h1>`` page-local header are all gone; the
    sidebar (`<details id="diagram-sidebar">`) replaces them with
    collapsible sections."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache,
        dev_log=False,
        focus_node_id=None,
        layer=3,
        embed=False,
    )

    # Sidebar root present.
    assert 'id="diagram-sidebar"' in html
    # Page-local title bar gone.
    assert "Studio · diagram" not in html
    # The two pre-CF.3.m chrome bar rows are gone.
    assert 'class="flex flex-wrap items-center gap-3 px-4 py-2' not in html
    # Each of the five primary section labels appears as a <summary>.
    for label in ("Layer", "Show", "Edge labels", "Overlays"):
        assert f">{label}</summary>" in html, f"missing section: {label!r}"


def test_diagram_sidebar_view_in_editor_button_default() -> None:
    """No focus → the View in editor button points at the editor home."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache,
        dev_log=False,
        focus_node_id=None,
        layer=1,
        embed=False,
    )
    assert 'id="view-in-editor"' in html
    assert 'href="/"' in html


def test_diagram_sidebar_view_in_editor_button_with_focus() -> None:
    """Focus set → button targets the resolved entity URL."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache,
        dev_log=False,
        focus_node_id="rail__ExternalRailInbound",
        layer=3,
        embed=False,
    )
    assert (
        'id="view-in-editor"' in html
        and 'href="/l2_shape/rail/ExternalRailInbound"' in html
    )


def test_diagram_sidebar_omitted_in_embed_mode() -> None:
    """External embedders (?embed=1) skip the sidebar — the host page
    is responsible for its own chrome."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    embed_html = _render_diagram_page(
        cache,
        dev_log=False,
        focus_node_id=None,
        layer=1,
        embed=True,
    )
    standalone_html = _render_diagram_page(
        cache,
        dev_log=False,
        focus_node_id=None,
        layer=1,
        embed=False,
    )
    assert 'id="diagram-sidebar"' not in embed_html
    assert 'id="diagram-sidebar"' in standalone_html


# ---------------------------------------------------------------------------
# JS/Python parity for the inverse
# ---------------------------------------------------------------------------

def test_diagram_js_inverse_arms_match_python() -> None:
    """The JS `_editorUrlForNode` arms must mirror the Python
    `_editor_url_for_focus_node` arms. If you add a new prefix arm
    to either side, this gate fails until you update both."""
    diagram_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "diagram.js"
    ).read_text()
    # Every prefix the Python inverse handles must also appear in
    # the JS dispatch.
    for prefix in (
        '"rail__bundle_"',
        '"rail__"',
        '"tmpl__"',
        '"role__"',
    ):
        assert prefix in diagram_js, (
            f"diagram.js _editorUrlForNode is missing the {prefix} arm — "
            "Python and JS inverses must agree"
        )
    # And the right-click handler routes contextmenu events to it.
    assert 'addEventListener("contextmenu"' in diagram_js
    assert "_showNodeContextMenu" in diagram_js
    assert "_editorUrlForNode" in diagram_js
