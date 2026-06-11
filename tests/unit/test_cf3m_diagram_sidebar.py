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
        # Rails: bijective. Lands on the EDIT form per operator lock.
        (
            "rail__ExternalRailInbound",
            "/l2_shape/rail/ExternalRailInbound/edit",
        ),
        # Templates: bijective. Lands on the EDIT form.
        (
            "tmpl__CustomerSettlement",
            "/l2_shape/transfer_template/CustomerSettlement/edit",
        ),
        # Roles: ambiguous (a role is shared by multiple accounts /
        # templates / limit_schedules). No unique edit target;
        # deferred until disambiguation surface exists.
        ("role__CustomerSubledger", None),
        # Synthetic bundle nodes: no source-side entity.
        ("rail__bundle_42", None),
        # Empty / None / unknown: nothing to edit.
        (None, None),
        ("", None),
        ("mystery__X", None),
    ],
)
def test_editor_url_for_focus_node_inverse(
    node_id: str | None, expected_url: str | None,
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
    # Each of the four primary section labels appears as a <summary>.
    # Section title stayed "Layer" after operator clarified that the
    # "Roles & Structure" label belongs on the FIRST BUTTON inside
    # the section (replacing "1 · Roles + structure"), not on the
    # section header.
    for label in ("Layer", "Show", "Edge labels", "Overlays"):
        assert f">{label}</summary>" in html, f"missing section: {label!r}"
    # First layer button is "Roles & Structure" — the L1 lock label
    # the operator already uses.
    assert ">Roles &amp; Structure</a>" in html


def test_diagram_sidebar_view_in_editor_button_suppressed_when_no_focus() -> None:
    """No focus → no resolvable edit target → the button is omitted
    entirely. Operator lock (2026-06-05): rather than dump them on a
    list / home page, suppress the affordance until they pick a node."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache, dev_log=False, focus_node_id=None, layer=1, embed=False,
    )
    assert 'id="view-in-editor"' not in html


def test_diagram_sidebar_view_in_editor_button_with_rail_focus() -> None:
    """Focus on a rail → button targets the rail's EDIT form."""
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
        and 'href="/l2_shape/rail/ExternalRailInbound/edit"' in html
    )


def test_diagram_sidebar_view_in_editor_button_suppressed_for_role_focus() -> None:
    """Role focus is ambiguous (multiple accounts can share the role) —
    deferred per operator lock. Button stays hidden."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache,
        dev_log=False,
        focus_node_id="role__CustomerSubledger",
        layer=1,
        embed=False,
    )
    assert 'id="view-in-editor"' not in html


def test_diagram_sidebar_reset_buttons_in_always_visible_header() -> None:
    """CF.3.m polish: Reset zoom + Reset all live in the sidebar's
    always-visible header strip (`<summary>`), not in the collapsible
    body. Operator needs both reachable when the collapsible body is
    closed — scroll-zoom can leave you stuck at an unhelpful view.

    Both hide when the sidebar itself is collapsed (`not-open:hidden`)
    so the collapsed-strip width stays narrow. Click handlers stop
    propagation so the buttons don't toggle the parent `<details>`."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache, dev_log=False, focus_node_id=None, layer=3, embed=False,
    )

    # Both buttons render.
    assert 'id="reset-zoom-btn"' in html
    assert 'id="toggle-reset"' in html
    # Both hide when the parent <details> is closed.
    assert 'id="reset-zoom-btn" type="button"' in html
    # Sidebar root summary contains both (i.e., they're in the always-
    # visible header strip, not the collapsible body).
    summary_end = html.index("</summary>")
    summary_start = html.index('<summary class="flex items-center')
    summary_block = html[summary_start:summary_end]
    assert 'id="reset-zoom-btn"' in summary_block, (
        "Reset zoom button must live inside <summary> — "
        "operator needs it reachable when body is collapsed"
    )
    assert 'id="toggle-reset"' in summary_block, (
        "Reset all link must live inside <summary>"
    )
    # Hidden when the sidebar itself collapses — use the `group-*`
    # variants so children read the PARENT <details>'s open state
    # (a bare `not-open:` on a non-<details> child checks the child's
    # own [open] attr, which it never has, so the child silently
    # stays hidden in BOTH states — bug we hit on the first try).
    assert "group-not-open:hidden" in summary_block
    # Click handlers stop propagation so the buttons don't toggle
    # the parent <details>.
    assert 'onclick="event.stopPropagation()"' in summary_block

    # JS wires the Reset zoom button to the pan/zoom reset() closure.
    diagram_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "diagram.js"
    ).read_text()
    assert 'getElementById("reset-zoom-btn")' in diagram_js


def test_diagram_sidebar_chevron_flips_on_collapse() -> None:
    """The master collapse chevron uses « when expanded and » when
    collapsed. Both glyphs render; the parent `<details>` carries the
    `group` class so children use `group-open:` / `group-not-open:`
    to read its open state.

    Why `group-*` and not bare `open:` / `not-open:`: the bare
    variants check the CHILD's own `[open]` attribute, not the
    parent. A `<span>` never has `[open]`, so a bare `not-open:hidden`
    on it stays hidden in both states (the first-render bug)."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache, dev_log=False, focus_node_id=None, layer=1, embed=False,
    )
    # Parent <details> carries the `group` marker so children can
    # read its open state.
    assert 'class="group absolute' in html or 'class="group ' in html
    # Both chevron glyphs and their show-when-{open,closed} variants.
    assert 'class="group-not-open:hidden">«' in html
    assert 'class="group-open:hidden">»' in html


def test_diagram_sidebar_status_carries_prefix() -> None:
    """The bottom status line (`#diagram-status`) carries
    `data-prefix="<L2 stem>"`, which `diagram.js` prepends to the
    "<N> nodes · <M> edges" text. Operator wanted the breadcrumb so
    the bottom info line tells you both the L2 and the counts at once.
    """
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache, dev_log=False, focus_node_id=None, layer=1, embed=False,
    )
    # Stem of the cache path is `spec_example` (the unit fixture).
    assert 'id="diagram-status" data-prefix="spec_example"' in html
    # JS prepends it.
    diagram_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "diagram.js"
    ).read_text()
    assert "status.dataset.prefix" in diagram_js


def test_diagram_sidebar_unified_checkbox_shape() -> None:
    """CF.3.m polish: Show / Edge labels / Overlays render as
    `<input type="checkbox">` rows — the previously-anchor-styled
    show-category toggles and the single-leg pill became real
    checkboxes that navigate on change. Visual uniformity + native
    keyboard handling (space toggles) come for free."""
    pytest.importorskip("graphviz")
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _render_diagram_page,
    )
    from recon_gen.common.l2.cache import L2InstanceCache  # noqa: PLC0415

    cache = L2InstanceCache.from_path(FIXTURES_DIR / "spec_example.yaml")
    html = _render_diagram_page(
        cache, dev_log=False, focus_node_id=None, layer=3, embed=False,
    )
    # Show categories — onchange navigates with the flipped show-set.
    for category in ("rail", "template", "chain", "control_parent"):
        assert (
            f'data-show-category="{category}" data-show-state="on" '
            in html
        ), f"{category!r} missing label wrapper"
    # All four are wired as <input type="checkbox"> with onchange-navigate.
    assert html.count("onchange=\"window.location.href=&#x27;") >= 5, (
        "expected ≥5 navigating checkboxes "
        "(rail/template/chain/control_parent + single-leg)"
    )
    # Edge label checkboxes still present and inside the Edge labels
    # section.
    assert 'id="toggle-edge-label-rail_bundle"' in html
    assert 'id="toggle-edge-label-chain"' in html


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
    `_editor_url_for_focus_node` arms. Both sides must:
    (a) handle the same four prefixes; (b) emit `/edit` URLs for
    resolvable cases; (c) return null/None for ambiguous cases.

    CF.3.m + BX.6/11 follow-up (2026-06-11) — the right-click handler
    no longer defers to the browser's native menu when ``_editorUrlForNode``
    returns null; it ALWAYS fires a custom menu. The fallback shape lives
    in ``_menuItemsForNode``, which surfaces role carriers (Account +
    AccountTemplate references) or a disabled "No matches" item.
    """
    diagram_js = (
        Path(__file__).parent.parent.parent
        / "src" / "recon_gen" / "common" / "html"
        / "_studio_assets" / "diagram.js"
    ).read_text()
    # Every prefix the Python inverse handles must also appear in the
    # JS dispatch.
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
    # Resolvable arms emit /edit URLs.
    assert '"/l2_shape/rail/" + nodeId.slice("rail__".length) + "/edit"' in diagram_js
    assert "+ nodeId.slice(\"tmpl__\".length) + \"/edit\"" in diagram_js
    # The right-click handler routes contextmenu events through the
    # general builder + always-fires path (BX.6/11 follow-up).
    assert 'addEventListener("contextmenu"' in diagram_js
    assert "_showNodeContextMenu" in diagram_js
    assert "_menuItemsForNode" in diagram_js
    # Menu item labels — rail/tmpl bijective stays unchanged; role + bundle +
    # orphan share the "No matches" disabled shape.
    assert '"Edit this entity"' in diagram_js
    assert '"No matches"' in diagram_js
    # Role-carrier item labels.
    assert "Edit Account:" in diagram_js
    assert "Edit AccountTemplate:" in diagram_js
