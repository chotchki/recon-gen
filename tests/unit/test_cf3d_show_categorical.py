"""CF.3.d — `?show=...` categorical onion contract.

The CF.3.d work replaced the four CSS-hide chrome checkboxes (rail /
template / chain / control_parent) with server-side re-emit anchors
backed by a ``show`` parameter on ``build_topology_graph_per_rail``.
A category absent from the resolved show-set causes that phase of
the emit to skip; the smaller subset re-lays out cleanly under dot.

Why this matters: the prior CSS-toggle path hid nodes but kept the
original positions, leaving a frozen / gappy layout. The new server-
emit path hands a shrunken DOT to graphviz and gets a fresh,
well-distributed layout.

This file pins the contract:

  - `show=None` falls through to the layer compat shim (matches the
    legacy ``layer`` argument behavior; required so prior cached
    bookmarks stay valid).
  - explicit show-sets override layer, narrow to category subsets.
  - the four categories (rail / template / chain / control_parent)
    each independently drop their corresponding nodes/edges.
  - the studio route handler parses `?show=role,rail` correctly,
    rejecting unknown tokens, and surfaces toggle anchors with
    consistent on/off state in the chrome.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.topology import (
    _VALID_SHOW_CATEGORIES,
    _categories_for_layer,
    build_topology_graph_per_rail,
)


FIXTURES_DIR = Path(__file__).parent.parent / "l2"


def _instance() -> L2Instance:
    return load_instance(FIXTURES_DIR / "spec_example.yaml")


# ---------------------------------------------------------------------------
# Compat shim: `show=None` matches the layer behavior
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("layer", [1, 2, 3])
def test_show_none_matches_layer_shim(layer: int) -> None:
    """`show=None` must be byte-identical to the prior `layer`-only call.

    The compat shim derives a category set from `layer` (`role` +
    `control_parent` at L1, +`rail` at L2, +`template` +`chain` at
    L3). Callers that don't pass show should see exactly the same
    DOT they got before CF.3.d landed.
    """
    pytest.importorskip("graphviz")
    inst = _instance()
    legacy = build_topology_graph_per_rail(
        inst, db_table_prefix="cf3d", layer=layer,
    )
    explicit = build_topology_graph_per_rail(
        inst, db_table_prefix="cf3d", layer=layer,
        show=_categories_for_layer(layer),
    )
    assert legacy.source == explicit.source, (
        f"L{layer} compat shim drift: `show=None` and "
        f"`show={_categories_for_layer(layer)!r}` must emit identical DOT"
    )


def test_categories_for_layer_matches_spec() -> None:
    """The shim ladder is locked: L1=role+control_parent;
    L2 adds rail; L3 adds template+chain."""
    assert _categories_for_layer(1) == frozenset({"role", "control_parent"})
    assert _categories_for_layer(2) == frozenset(
        {"role", "control_parent", "rail"},
    )
    assert _categories_for_layer(3) == frozenset(
        {"role", "control_parent", "rail", "template", "chain"},
    )


def test_valid_show_categories_locked() -> None:
    """If someone adds a new category, this gate forces them to
    decide what the compat shim, route parser, and chrome anchors do."""
    assert _VALID_SHOW_CATEGORIES == frozenset(
        {"role", "rail", "template", "chain", "control_parent"},
    )


# ---------------------------------------------------------------------------
# Each category independently drops its corresponding emit
# ---------------------------------------------------------------------------

def _dot_for(inst: L2Instance, show: frozenset[str]) -> str:
    return build_topology_graph_per_rail(
        inst, db_table_prefix="cf3d", layer=3, show=show,
    ).source


def test_show_without_rail_drops_rail_nodes() -> None:
    pytest.importorskip("graphviz")
    inst = _instance()
    with_rail = _dot_for(inst, frozenset({"role", "rail", "control_parent"}))
    without_rail = _dot_for(inst, frozenset({"role", "control_parent"}))
    assert "rail__" in with_rail
    assert "rail__" not in without_rail


def test_show_without_template_drops_template_emit() -> None:
    """Dropping `template` from the show-set strips the template
    subgraph + node emit. Chain edges that reference template IDs as
    endpoints DO remain as dangling references (graphviz then draws
    them as small anonymous nodes — the operator chose chains-without-
    templates, opting into the dangling shape). Hence the assertion
    measures "fewer ``tmpl__`` references", not zero."""
    pytest.importorskip("graphviz")
    inst = _instance()
    with_t = _dot_for(inst, frozenset({"role", "rail", "template", "chain"}))
    without_t = _dot_for(inst, frozenset({"role", "rail", "chain"}))
    assert with_t.count("tmpl__") > without_t.count("tmpl__"), (
        f"template emit not gated: with={with_t.count('tmpl__')} "
        f"without={without_t.count('tmpl__')}"
    )


def test_show_without_template_or_chain_drops_all_tmpl_refs() -> None:
    """When BOTH ``template`` and ``chain`` are off, no ``tmpl__``
    reference should leak through — the only remaining producer is
    the chain-edge endpoint, which is itself gated."""
    pytest.importorskip("graphviz")
    inst = _instance()
    dot = _dot_for(inst, frozenset({"role", "rail", "control_parent"}))
    assert "tmpl__" not in dot


def test_show_only_role_strips_rail_and_template_emit() -> None:
    """Tightest subset: role-only. No rail or template nodes
    emit; chain/control_parent edges are also gated."""
    pytest.importorskip("graphviz")
    inst = _instance()
    dot = _dot_for(inst, frozenset({"role"}))
    assert "rail__" not in dot
    assert "tmpl__" not in dot


def test_show_unknown_category_silently_ignored() -> None:
    """Intersecting with `_VALID_SHOW_CATEGORIES` keeps stray tokens
    from breaking the emit (matches the route handler's parse + AND
    pattern)."""
    pytest.importorskip("graphviz")
    inst = _instance()
    full = _dot_for(inst, _categories_for_layer(3))
    polluted = _dot_for(
        inst,
        _categories_for_layer(3) | frozenset({"bogus", "also_bogus"}),
    )
    # The unknowns should be filtered out before they reach the emit
    # — both calls produce identical DOT.
    assert full == polluted


# ---------------------------------------------------------------------------
# Chrome chrome: route handler surfaces the toggle anchors
# ---------------------------------------------------------------------------

def test_render_diagram_page_emits_show_toggle_anchors() -> None:
    """The rendered page must carry one anchor per server-side
    category, with the correct on/off state baked into the URL and
    a `data-show-category` attr for downstream tests / lint."""
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
    # All four server-side categories show up as toggle anchors.
    for category in ("rail", "template", "chain", "control_parent"):
        assert f'data-show-category="{category}"' in html, (
            f"missing toggle anchor for category {category!r}"
        )

    # L3 default shim → every category is "on" → state word "on"
    # appears next to each anchor.
    for category in ("rail", "template", "chain", "control_parent"):
        assert f'data-show-category="{category}" data-show-state="on"' in html


def test_render_diagram_page_show_param_flips_state() -> None:
    """Passing `show={role}` should render every server-side
    category's anchor with state "off" (since they're absent from the
    active set), with an href that adds the category back in."""
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
        show=frozenset({"role"}),
        embed=False,
    )
    for category in ("rail", "template", "chain", "control_parent"):
        assert f'data-show-category="{category}" data-show-state="off"' in html, (
            f"{category!r} should render as off when show={{'role'}}"
        )
        # And its toggle URL adds the category back to the show-set
        # (the URL builder sorts and joins comma-separated).
        # The exact ordering: role + the toggled category, sorted.
        expected_show_substring = (
            f"show={','.join(sorted(['role', category]))}"
        )
        assert expected_show_substring in html, (
            f"missing add-back URL fragment for {category!r}; "
            f"expected substring {expected_show_substring!r}"
        )
