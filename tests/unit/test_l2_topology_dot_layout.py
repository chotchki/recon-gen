"""Regression: ``build_topology_graph_per_rail`` at L3 lays out cleanly.

Manual-drive bug (X.4.j post-merge): with ``concentrate=true`` set on
the digraph attrs, graphviz's network-simplex layout could not solve
the L3 graph (clusters + cross-cluster chain edges) for sasquatch_pr.
It emitted::

    Error: rebuild_vlists: lead is null for rank 3
    concentrate=true may not work correctly.

…and produced an SVG with viewBox ``0 0 8 8`` and edge paths full of
``Mnan,-nanCnan,-nan`` — visually a tiny smear of overlapping labels
where the diagram should be. Layer 1 + Layer 2 were unaffected because
neither uses clusters.

This test runs the actual ``dot`` layout on sasquatch_pr at L3 and
asserts the layout succeeded:

  - viewBox dimensions are not the trivial 8×8 fallback
  - no NaN coordinates anywhere in the SVG (path / position / size)
  - the SVG declares roughly the expected number of node groups

Future regression: if anything else collapses the graph at L3, this
flips red the same shape as the original report.
"""
from __future__ import annotations

import re

import pytest

graphviz = pytest.importorskip("graphviz")

from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.topology import build_topology_graph_per_rail


_FIXTURES_DIR = "tests/l2"


def _layout_l3_svg(instance_name: str) -> str:
    """Render the L3 diagram for ``<fixture>.yaml`` to SVG via dot."""
    instance = load_instance(f"{_FIXTURES_DIR}/{instance_name}.yaml")
    g = build_topology_graph_per_rail(instance, db_table_prefix="test", layer=3)
    # ``pipe`` runs the configured engine (default ``dot``) and returns
    # the bytes — no on-disk file required.
    return g.pipe(format="svg").decode("utf-8")


def test_l3_layout_has_no_nan_coordinates_on_spec_example() -> None:
    """The cluster + cross-cluster-chain shape that broke sasquatch_pr
    also exists at lower density in spec_example. Smaller fixture, same
    invariant: zero NaN tokens anywhere in the SVG output."""
    svg = _layout_l3_svg("spec_example")
    nan_hits = re.findall(r"\bnan\b", svg)
    assert not nan_hits, (
        f"graphviz layout produced {len(nan_hits)} NaN tokens — likely "
        f"``concentrate=true`` re-introduced (see test docstring for the "
        f"original failure shape). first 3 hits: {nan_hits[:3]}"
    )


def test_l3_layout_has_substantial_viewbox_on_spec_example() -> None:
    """Trivial 8×8 viewBox means dot bailed and produced the broken
    fallback SVG. The real layout is hundreds of points per dim."""
    svg = _layout_l3_svg("spec_example")
    m = re.search(r'viewBox="0\.00 0\.00 (\d+\.\d+) (\d+\.\d+)"', svg)
    assert m is not None, "no viewBox found in SVG (or unexpected format)"
    width, height = float(m.group(1)), float(m.group(2))
    # 8×8 is the broken fallback. Real layouts at L3 are >100 pts per dim
    # even for tiny fixtures.
    assert width > 50 and height > 50, (
        f"viewBox suspiciously small ({width}×{height}) — dot layout likely "
        f"failed (8×8 fallback). expected real LR layout in hundreds of pts."
    )


def test_l3_layout_emits_at_least_three_node_groups_on_spec_example() -> None:
    """Sanity-check the SVG actually contains rendered nodes — not just a
    blank canvas. The broken case rendered 1 node + 8 NaN-coord edges."""
    svg = _layout_l3_svg("spec_example")
    node_groups = re.findall(r'class="node"', svg)
    assert len(node_groups) >= 3, (
        f"expected ≥3 ``class=\"node\"`` groups in L3 SVG; got "
        f"{len(node_groups)} — dot likely failed to lay out the graph."
    )
