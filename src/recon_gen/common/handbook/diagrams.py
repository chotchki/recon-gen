"""Diagram render pipeline for the unified mkdocs site.

Three diagram families:

1. **L2-driven topology** (``render_l2_topology``) — accounts + rails +
   chains laid out from the loaded ``L2Instance``. Cuts: ``accounts``
   (account-rail-account edges), ``chains`` (parent → child DAG over
   rails / transfer templates), ``layered`` (both, layered).

2. **Per-app dataflow** (``render_dataflow``) — which datasets feed
   which sheets, walked off the typed ``App`` tree. One per app's
   reference page.

3. **Hand-authored conceptual** (``render_conceptual``) — reads a
   ``.dot`` file from ``docs/_diagrams/conceptual/``. Used for the
   narrative concept pages where the diagram is a teaching aid that
   doesn't derive from any L2 data (double-entry, escrow-with-reversal,
   sweep-net-settle, etc.).

All three return the **DOT source string**; the mkdocs-macros
``diagram(...)`` macro wraps it in ``<script type="text/x-graphviz">``
inside a ``<figure>`` and ``stylesheets/qs-graphviz-wasm.js`` renders
it client-side via ``@hpcc-js/wasm-graphviz``. No system ``dot``
binary is invoked at build time (Phase T migration).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import graphviz

from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    Chain,
    L2Instance,
    Rail,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)


# -- Public API --------------------------------------------------------------


TopologyKind = Literal[
    "accounts", "account_templates", "chains", "layered", "hierarchy",
    "transfer_template",
]


def render_l2_topology(  # noqa: D401
    l2_instance: L2Instance,
    kind: TopologyKind,
    *,
    name: str | None = None,
) -> str:
    """Render an L2 instance's structure as an inline SVG.

    ``kind="accounts"`` shows every Account as a node and every Rail as
    an edge between source-role-account and destination-role-account.
    Single-leg rails draw a self-loop on the leg-role account so they
    show up at all.

    ``kind="account_templates"`` mirrors the accounts diagram but with
    ``AccountTemplate`` nodes (keyed by role) instead of singleton
    Accounts. Rails whose ``source_role`` / ``destination_role`` /
    ``leg_role`` reference a template's role get edges to those template
    nodes; rails whose roles touch no template are excluded — this
    diagram is the "what does the template-shape graph look like?" view,
    not the full topology.

    ``kind="chains"`` shows every Rail / TransferTemplate the chains
    table references, with ``parent → child`` edges (XOR groups
    rendered as a shared cluster). Required edges drawn solid; optional
    edges dashed.

    ``kind="layered"`` lays the accounts diagram on top of the chains
    diagram in two ranks — the accounts row at the top, the chains row
    below.

    ``kind="hierarchy"`` shows the parent → child rollup of singleton
    accounts and account templates. Each node is an Account or
    AccountTemplate; an edge points from a child to its parent
    (resolved by ``child.parent_role == parent.role``). Singleton
    accounts have solid borders; account templates carry dashed
    borders since they're a SHAPE, not an instance.

    ``kind="transfer_template"`` requires a ``name`` kwarg naming one
    of the instance's TransferTemplates. Renders that template as a
    parent node with each leg-rail as a child node, edge-labeled with
    the leg's direction (Debit / Credit / Variable / two-leg). The
    template node carries its expected_net + transfer_key + completion
    so a reader gets the closure shape at a glance.
    """
    if kind == "accounts":
        return _build_accounts_graph(l2_instance).source
    if kind == "account_templates":
        return _build_account_templates_graph(l2_instance).source
    if kind == "chains":
        return _build_chains_graph(l2_instance).source
    if kind == "layered":
        return _build_layered_graph(l2_instance).source
    if kind == "hierarchy":
        return _build_hierarchy_graph(l2_instance).source
    if kind == "transfer_template":
        if name is None:
            raise ValueError(
                "kind='transfer_template' requires a name= kwarg "
                "naming one of the instance's TransferTemplates."
            )
        return _build_transfer_template_graph(l2_instance, name).source
    raise ValueError(f"unknown topology kind: {kind!r}")


def render_l2_account_focus(l2_instance: L2Instance) -> str | None:
    """Render the first singleton Account with its parent edge (if any).

    Returns None if the instance has no singleton accounts. Caller (the
    mkdocs-macros entry) handles the fallback to ``spec_example``.
    """
    if not l2_instance.accounts:
        return None
    acc = l2_instance.accounts[0]
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="BT", nodesep="0.4", ranksep="0.7")
    g.attr("node", fontsize="11", style="filled")
    _add_account_node(g, acc)
    if acc.parent_role is not None:
        parent = _role_to_account(l2_instance).get(str(acc.parent_role))
        if parent is not None:
            _add_account_node(g, parent)
            g.edge(str(acc.id), str(parent.id), color="#666666")
    return g.source


def render_l2_account_template_focus(l2_instance: L2Instance) -> str | None:
    """Render the first AccountTemplate with its parent singleton."""
    if not l2_instance.account_templates:
        return None
    template = l2_instance.account_templates[0]
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="BT", nodesep="0.4", ranksep="0.7")
    g.attr("node", fontsize="11", style="filled")
    _add_account_template_node(g, template)
    if template.parent_role is not None:
        parent = _role_to_account(l2_instance).get(str(template.parent_role))
        if parent is not None:
            _add_account_node(g, parent)
            g.edge(_template_node_id(template), str(parent.id), color="#666666")
    return g.source


def render_l2_rail_focus(l2_instance: L2Instance) -> str | None:
    """Render the first Rail with its endpoint accounts.

    For TwoLeg, source + destination side-by-side with the rail edge
    between them. For SingleLeg, the leg-role account with a self-loop
    edge.
    """
    if not l2_instance.rails:
        return None
    rail = l2_instance.rails[0]
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.5", ranksep="1.0")
    g.attr("node", fontsize="11", style="filled")
    role_to_account = _role_to_account(l2_instance)
    if isinstance(rail, TwoLegRail):
        sources = _expand_role_expression(rail.source_role)
        destinations = _expand_role_expression(rail.destination_role)
        for r in (*sources, *destinations):
            acc = role_to_account.get(r)
            if acc is not None:
                _add_account_node(g, acc)
    elif isinstance(rail, SingleLegRail):
        for r in _expand_role_expression(rail.leg_role):
            acc = role_to_account.get(r)
            if acc is not None:
                _add_account_node(g, acc)
    _add_rail_edges(g, rail, role_to_account)
    return g.source


def render_l2_transfer_template_focus(l2_instance: L2Instance) -> str | None:
    """Render the first TransferTemplate as a chain of leg rails.

    Each leg becomes a node labeled with its rail_name; edges connect
    them in declaration order. The template name + ``expected_net``
    sit in the graph label.
    """
    if not l2_instance.transfer_templates:
        return None
    template = l2_instance.transfer_templates[0]
    g = graphviz.Digraph(format="svg")
    g.attr(
        rankdir="LR", nodesep="0.4", ranksep="0.9",
        label=f"{template.name}  (expected_net={template.expected_net})",
        labelloc="t", fontsize="12",
    )
    g.attr(
        "node", fontsize="11", shape="box",
        style="filled,rounded", fillcolor="#e0f7fa",
    )
    rails_by_name = {str(r.name): r for r in l2_instance.rails}
    prev: str | None = None
    for idx, leg in enumerate(template.leg_rails):
        rail_name = str(leg)
        node_id = f"leg_{idx}_{rail_name}"
        rail = rails_by_name.get(rail_name)
        if isinstance(rail, TwoLegRail):
            kind = "TwoLeg"
        elif isinstance(rail, SingleLegRail):
            kind = "SingleLeg"
        else:
            kind = ""
        label = f"{rail_name}\n({kind})" if kind else rail_name
        g.node(node_id, label)
        if prev is not None:
            g.edge(prev, node_id, color="#666666")
        prev = node_id
    return g.source


def render_l2_chain_focus(l2_instance: L2Instance) -> str | None:
    """Render the first Chain row with parent + every child labeled.

    Endpoint nodes are coloured by kind (rail vs template vs unresolved).
    Edge style + label match the same conventions as the full chains
    diagram (solid=required for singleton-children rows, dashed=xor
    for multi-children rows). Z.A: a multi-children row produces N
    edges from the same parent.
    """
    if not l2_instance.chains:
        return None
    chain = l2_instance.chains[0]
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.4", ranksep="0.9")
    g.attr("node", fontsize="11", shape="box", style="filled,rounded")

    rails_by_name = {str(r.name) for r in l2_instance.rails}
    templates_by_name = {str(t.name) for t in l2_instance.transfer_templates}

    def _add_endpoint(ref: object) -> None:
        ref_id = str(ref)
        if ref_id in rails_by_name:
            g.node(ref_id, ref_id, fillcolor="#e0f7fa")
        elif ref_id in templates_by_name:
            g.node(ref_id, f"{ref_id}\n(template)", fillcolor="#fff9c4")
        else:
            g.node(ref_id, ref_id, fillcolor="#f5f5f5")

    _add_endpoint(chain.parent)
    for child_spec in chain.children:
        _add_endpoint(child_spec.name)
    _add_chain_edge(g, chain)
    return g.source


def render_l2_limit_schedule_focus(l2_instance: L2Instance) -> str | None:
    """Render the first LimitSchedule as a (parent_role, rail) → cap.

    Visual: a parent-role node on the left with a labeled edge to a
    "cap" node showing the daily flow ceiling. Conceptual rather than
    topological since LimitSchedules are configuration, not topology.
    """
    if not l2_instance.limit_schedules:
        return None
    sched = l2_instance.limit_schedules[0]
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.4", ranksep="1.0")
    g.attr("node", fontsize="11", style="filled")

    role_node = f"role_{sched.parent_role}"
    cap_node = f"cap_{sched.parent_role}_{sched.rail}"
    g.node(
        role_node, f"role: {sched.parent_role}",
        shape="box", fillcolor="#bbdefb",
    )
    g.node(
        cap_node, f"daily cap\n{sched.cap}",
        shape="cylinder", fillcolor="#ffe0b2",
    )
    g.edge(
        role_node, cap_node,
        label=f"rail:\n{sched.rail}",
        fontsize="9", color="#666666",
    )
    return g.source


def render_dataflow(app_name: str) -> str:
    """Render which datasets feed which sheets for ``app_name``.

    Reads the typed ``App`` tree — every Visual exposes ``datasets()``
    which returns the set of Datasets its field-well leaves reference.
    Resulting graph: dataset cylinders on the left, sheet boxes on the
    right, an edge from a dataset to every sheet that has at least one
    visual sourced from it. TextBox visuals (no field wells) are
    skipped via ``hasattr(visual, "datasets")``.
    """
    from recon_gen.common.tree.structure import App

    app = _build_app(app_name)
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.4", ranksep="1.2")
    g.attr("node", fontsize="11")

    # Node IDs use single ``__`` (not ``::``) — graphviz's edge writer
    # treats ``::`` inside a bare-word ID as a port-reference separator
    # (``node:port``), which mangles `g.edge(...)` output and trips the
    # downstream `dot` parser.
    datasets_seen: set[str] = set()
    edges: set[tuple[str, str]] = set()
    for sheet in app.analysis.sheets:
        sheet_id = f"sheet__{sheet.name}"
        g.node(
            sheet_id,
            sheet.name,
            shape="box",
            style="filled,rounded",
            fillcolor="#e3f2fd",
        )
        for visual in sheet.visuals:
            # TextBox + future content-only visuals don't expose
            # datasets(); skip them so the diagram only shows true
            # data-bound wiring.
            if not hasattr(visual, "datasets"):
                continue
            for ds in visual.datasets():
                ds_id = f"ds__{ds.identifier}"
                if ds_id not in datasets_seen:
                    g.node(
                        ds_id,
                        ds.identifier,
                        shape="cylinder",
                        style="filled",
                        fillcolor="#fff3e0",
                    )
                    datasets_seen.add(ds_id)
                edges.add((ds_id, sheet_id))

    for ds_id, sheet_id in sorted(edges):
        g.edge(ds_id, sheet_id, color="#666666")

    return g.source


def render_conceptual(name: str) -> str:
    """Render a hand-authored ``.dot`` file from the conceptual catalog.

    Reads ``docs/_diagrams/conceptual/<name>.dot`` and pipes it through
    Graphviz. ``KeyError`` if the named diagram doesn't exist — surfaces
    in the mkdocs build with a clear "no such conceptual diagram" line.
    """
    dot_path = _CONCEPTUAL_DIR / f"{name}.dot"
    if not dot_path.exists():
        available = sorted(p.stem for p in _CONCEPTUAL_DIR.glob("*.dot"))
        raise KeyError(
            f"No conceptual diagram named {name!r}. "
            f"Available: {', '.join(available) or '(none)'}."
        )
    return dot_path.read_text(encoding="utf-8")


# -- L2 graph builders -------------------------------------------------------


def _build_accounts_graph(l2_instance: L2Instance) -> graphviz.Digraph:
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.5", ranksep="1.0")
    g.attr("node", fontsize="11", style="filled")

    role_to_account = _role_to_account(l2_instance)
    for acc in l2_instance.accounts:
        _add_account_node(g, acc)

    # Bundle edges that share (src_node, dst_node, edge-kind) so multiple
    # rails along the same direction render as one labeled edge instead
    # of N parallel lines. Direction stays split because the key is
    # ordered (src, dst). edge-kind separates two-leg flow from single-leg
    # self-loops so they keep their distinct visual styling.
    bundle = _RailEdgeBundle()
    for rail in l2_instance.rails:
        _collect_rail_edges_for_accounts(rail, role_to_account, bundle)
    bundle.emit(g)
    return g


def _build_account_templates_graph(l2_instance: L2Instance) -> graphviz.Digraph:
    """Template-focused topology: every rail that touches at least one
    AccountTemplate, with template nodes (dashed) on the template legs
    and singleton nodes (solid) on any non-template legs.

    Rails that touch no template at all drop out, so the diagram stays
    a focused template-topology view rather than a full re-render of
    the accounts graph. Singletons that only appear on dropped rails
    don't get rendered either — keeps the canvas small.
    """
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.5", ranksep="1.0")
    g.attr("node", fontsize="11", style="filled")

    template_roles = {str(t.role) for t in l2_instance.account_templates}
    role_to_template = _role_to_template(l2_instance)
    role_to_account = _role_to_account(l2_instance)
    for template in l2_instance.account_templates:
        _add_account_template_node(g, template)

    rendered_singletons: set[str] = set()
    bundle = _RailEdgeBundle()
    for rail in l2_instance.rails:
        _collect_rail_edges_for_templates(
            g, rail, template_roles, role_to_template,
            role_to_account, rendered_singletons, bundle,
        )
    bundle.emit(g)
    return g


def _build_transfer_template_graph(
    l2_instance: L2Instance, name: str,
) -> graphviz.Digraph:
    """One-template diagram: the named TransferTemplate as a parent
    node + each leg-rail as a child node.

    The template node carries its closure-relevant attributes
    (expected_net, transfer_key, completion) so a reader gets the
    "what closes this bundle" answer at a glance. Leg-rail edges
    are color-coded by direction:

    - SingleLegRail Debit: blue
    - SingleLegRail Credit: green
    - SingleLegRail Variable: amber (the closure leg — flagged
      because its amount + direction are determined at posting time)
    - TwoLegRail: grey (the rail itself has both legs internally)

    Aggregating leg rails are intentionally NOT styled differently —
    they're forbidden from appearing here per validator R7 (template
    leg_rails must be non-aggregating), so the case can't arise.
    """
    template = next(
        (t for t in l2_instance.transfer_templates if str(t.name) == name),
        None,
    )
    if template is None:
        declared = [str(t.name) for t in l2_instance.transfer_templates]
        raise ValueError(
            f"no TransferTemplate named {name!r} on the L2 instance. "
            f"Declared: {declared!r}"
        )

    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.4", ranksep="1.0")
    g.attr("node", fontsize="11", style="filled")

    # Template node — distinguished shape (double-bordered rounded box)
    # so it reads as "this is the bundle, the rails below are its legs".
    # Z.B (2026-05-15): the template's `name` IS the type identifier.
    template_id = f"tt__{template.name}"
    template_label_lines = [
        f"<b>{template.name}</b>",
        f"expected_net = {template.expected_net}",
        f"completion = {template.completion}",
    ]
    if template.transfer_key:
        keys = ", ".join(str(k) for k in template.transfer_key)
        template_label_lines.append(f"transfer_key = [{keys}]")
    g.node(
        template_id,
        label=f"<{'<br/>'.join(template_label_lines)}>",
        shape="box",
        style="filled,rounded",
        fillcolor="#fff3e0",
        color="#e65100",
        penwidth="2",
    )

    rails_by_name = {str(r.name): r for r in l2_instance.rails}
    for leg_name in template.leg_rails:
        rail = rails_by_name.get(str(leg_name))
        if rail is None:
            # R4 already guarantees existence at validate-time; defensive.
            continue
        leg_id = f"tt__{template.name}__leg__{rail.name}"
        leg_label, edge_color, edge_label = _leg_rail_render(rail)
        g.node(
            leg_id,
            label=leg_label,
            shape="box",
            style="filled",
            fillcolor="#e3f2fd",
        )
        g.edge(
            template_id, leg_id,
            label=edge_label, fontsize="9", color=edge_color,
        )
    return g


def _leg_rail_render(rail: Rail) -> tuple[str, str, str]:
    """Return (node_label, edge_color, edge_label) for a leg rail.

    Two-leg rails surface their source → destination pair on the node
    label; single-leg rails surface the leg_role + direction.
    """
    if isinstance(rail, TwoLegRail):
        srcs = " | ".join(_expand_role_expression(rail.source_role))
        dsts = " | ".join(_expand_role_expression(rail.destination_role))
        return (
            f"<<b>{rail.name}</b><br/>{srcs} → {dsts}>",
            "#666666",  # neutral grey — two-leg has its own internal direction
            "two-leg",
        )
    # SingleLegRail
    leg_roles = " | ".join(_expand_role_expression(rail.leg_role))
    direction = rail.leg_direction
    color_map = {
        "Debit": "#1976d2",     # blue
        "Credit": "#2e7d32",    # green
        "Variable": "#f57c00",  # amber — closure leg
    }
    edge_color = color_map.get(direction, "#666666")
    return (
        f"<<b>{rail.name}</b><br/>leg_role: {leg_roles}>",
        edge_color,
        direction,
    )


def _build_chains_graph(l2_instance: L2Instance) -> graphviz.Digraph:
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="LR", nodesep="0.4", ranksep="0.9")
    g.attr("node", fontsize="11", shape="box", style="filled,rounded")

    referenced_ids: set[str] = set()
    for chain in l2_instance.chains:
        referenced_ids.add(str(chain.parent))
        for child_spec in chain.children:
            referenced_ids.add(str(child_spec.name))

    rails_by_name = {str(r.name): r for r in l2_instance.rails}
    templates_by_name = {str(t.name): t for t in l2_instance.transfer_templates}

    for ref_id in sorted(referenced_ids):
        if ref_id in rails_by_name:
            g.node(ref_id, ref_id, fillcolor="#e0f7fa")
        elif ref_id in templates_by_name:
            g.node(ref_id, f"{ref_id} (template)", fillcolor="#fff9c4")
        else:
            g.node(ref_id, ref_id, fillcolor="#f5f5f5")

    for chain in l2_instance.chains:
        _add_chain_edge(g, chain)
    return g


def _build_layered_graph(l2_instance: L2Instance) -> graphviz.Digraph:
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="TB", nodesep="0.4", ranksep="1.4")

    with g.subgraph(name="cluster_accounts") as c:
        c.attr(label="Accounts + Rails", style="rounded", color="#90caf9")
        c.attr("node", fontsize="11", style="filled")
        role_to_account = _role_to_account(l2_instance)
        for acc in l2_instance.accounts:
            _add_account_node(c, acc)
        bundle = _RailEdgeBundle()
        for rail in l2_instance.rails:
            _collect_rail_edges_for_accounts(rail, role_to_account, bundle)
        bundle.emit(c)

    with g.subgraph(name="cluster_chains") as c:
        c.attr(label="Chains", style="rounded", color="#a5d6a7")
        c.attr(
            "node", fontsize="11", shape="box", style="filled,rounded"
        )
        rails_by_name = {str(r.name) for r in l2_instance.rails}
        templates_by_name = {str(t.name) for t in l2_instance.transfer_templates}
        seen: set[str] = set()
        for chain in l2_instance.chains:
            endpoints: list[object] = [chain.parent, *chain.children]
            for ref in endpoints:
                ref_id = str(ref)
                if ref_id in seen:
                    continue
                seen.add(ref_id)
                if ref_id in rails_by_name:
                    c.node(f"chain::{ref_id}", ref_id, fillcolor="#e0f7fa")
                elif ref_id in templates_by_name:
                    c.node(
                        f"chain::{ref_id}",
                        f"{ref_id} (template)",
                        fillcolor="#fff9c4",
                    )
                else:
                    c.node(f"chain::{ref_id}", ref_id, fillcolor="#f5f5f5")
        for chain in l2_instance.chains:
            # Z.A: singleton-children = required (solid); multi-children
            # = XOR alternation (dashed). One edge per child.
            is_required = len(chain.children) == 1
            style = "solid" if is_required else "dashed"
            label = "" if is_required else "xor"
            for child_spec in chain.children:
                c.edge(
                    f"chain::{chain.parent}",
                    f"chain::{child_spec.name}",
                    label=label,
                    style=style,
                    color="#666666",
                )
    return g


def _build_hierarchy_graph(l2_instance: L2Instance) -> graphviz.Digraph:
    """Render the parent → child rollup of accounts and account templates.

    Singleton ``Account`` nodes use the same scope-colored fill as the
    other diagrams (blue=internal, orange=external). ``AccountTemplate``
    nodes use a dashed border so a reader can distinguish "this is one
    account" from "this is a SHAPE that exists in many instances at
    runtime" at a glance.

    Edges run from child to parent (singleton or template child →
    singleton-account parent), resolved by
    ``child.parent_role == parent.role``. The edge arrow points at the
    parent so the rollup direction reads naturally with ``rankdir=BT``
    (children at the top, control accounts at the bottom).

    Roots (singletons with ``parent_role=None``) appear ungrouped at
    the bottom rank.
    """
    g = graphviz.Digraph(format="svg")
    g.attr(rankdir="BT", nodesep="0.4", ranksep="0.9")
    g.attr("node", fontsize="11", style="filled")

    role_to_account = _role_to_account(l2_instance)

    for acc in l2_instance.accounts:
        _add_account_node(g, acc)

    for template in l2_instance.account_templates:
        _add_account_template_node(g, template)

    # Child → parent edges (children: singletons + templates with
    # parent_role set; parents: singletons whose role matches).
    for acc in l2_instance.accounts:
        if acc.parent_role is None:
            continue
        parent = role_to_account.get(str(acc.parent_role))
        if parent is None:
            continue
        g.edge(str(acc.id), str(parent.id), color="#666666")

    for template in l2_instance.account_templates:
        if template.parent_role is None:
            continue
        parent = role_to_account.get(str(template.parent_role))
        if parent is None:
            continue
        g.edge(_template_node_id(template), str(parent.id), color="#666666")

    return g


# -- Graph helpers -----------------------------------------------------------


def _role_to_account(l2_instance: L2Instance) -> dict[str, Account]:
    return {
        str(acc.role): acc for acc in l2_instance.accounts if acc.role is not None
    }


def _role_to_template(l2_instance: L2Instance) -> dict[str, AccountTemplate]:
    return {str(t.role): t for t in l2_instance.account_templates}


def _add_account_node(g: graphviz.Digraph, acc: Account) -> None:
    color = "#bbdefb" if acc.scope == "internal" else "#ffe0b2"
    label = acc.name or acc.id
    g.node(str(acc.id), str(label), fillcolor=color, shape="box")


def _template_node_id(template: AccountTemplate) -> str:
    """Stable graph node id for an AccountTemplate.

    Templates have no ``id`` field (they're a SHAPE, not an instance) so
    we synthesize one from the role with a ``tmpl__`` prefix to avoid
    collisions with singleton account ids.

    Underscore — NOT colon. The ``graphviz`` Python lib quotes node
    IDs in node-definition statements but emits unquoted endpoints in
    edge statements, where Graphviz dot syntax then parses ``a:b`` as
    "node ``a``, port ``b``". A previous ``tmpl::`` prefix made every
    template edge collapse onto a phantom ``tmpl`` node — see commit
    history for the fix.
    """
    return f"tmpl__{template.role}"


def _add_account_template_node(
    g: graphviz.Digraph, template: AccountTemplate,
) -> None:
    """Render an AccountTemplate node with a dashed border.

    Uses the same scope-coloured fill as singletons but a dashed
    border to mark it as "this is a SHAPE, populated at runtime"
    rather than a single physical account. Label includes ``role × N``
    to nudge readers toward the multi-instance reading.
    """
    color = "#bbdefb" if template.scope == "internal" else "#ffe0b2"
    g.node(
        _template_node_id(template),
        f"{template.role} × N",
        fillcolor=color,
        shape="box",
        style="filled,dashed",
    )


class _RailEdgeBundle:
    """Group rail edges by (src_node, dst_node, kind) so parallel lines
    along the same direction collapse into one labeled edge.

    Two kinds:
    - ``"two_leg"`` — solid blue ``#1976d2`` for two-leg rail flow.
    - ``"single_leg"`` — dashed purple ``#7b1fa2`` for single-leg
      self-loops.

    Direction stays split because the key tuple is ordered (src, dst):
    a Customer→External rail and an External→Customer rail produce
    distinct keys even when they target the same role pair, matching
    the user's "split directions, bundle within a direction" rule.
    """

    def __init__(self) -> None:
        # key: (src_node_id, dst_node_id, kind)
        # value: list of rail labels (one line per rail)
        self._edges: dict[tuple[str, str, str], list[str]] = {}

    def add(self, src: str, dst: str, kind: str, label: str) -> None:
        self._edges.setdefault((src, dst, kind), []).append(label)

    def emit(self, g: graphviz.Digraph) -> None:
        for (src, dst, kind), labels in self._edges.items():
            color, style = (
                ("#1976d2", "solid") if kind == "two_leg"
                else ("#7b1fa2", "dashed")
            )
            label = "\n".join(labels)
            kwargs = {"label": label, "fontsize": "9", "color": color}
            if style != "solid":
                kwargs["style"] = style
            g.edge(src, dst, **kwargs)


def _rail_label(rail: Rail) -> str:
    """One-line label for a rail in a bundled edge.

    Just the rail name — no transfer_type. With direction-bundling
    the same edge already groups rails sharing (src, dst), so the
    transfer_type was visual noise (often duplicates within a bundle,
    or close variants like ``ach_inbound`` vs ``wire_inbound`` that
    don't add information beyond what the rail name conveys).
    """
    return str(rail.name)


def _collect_rail_edges_for_accounts(
    rail: Rail,
    role_to_account: dict[str, Account],
    bundle: _RailEdgeBundle,
) -> None:
    """Singleton-accounts diagram: collect each rail's edges into the
    bundle keyed by (src_account_id, dst_account_id, kind)."""
    if isinstance(rail, TwoLegRail):
        sources = _expand_role_expression(rail.source_role)
        destinations = _expand_role_expression(rail.destination_role)
        for src_role in sources:
            src_acc = role_to_account.get(src_role)
            for dst_role in destinations:
                dst_acc = role_to_account.get(dst_role)
                if src_acc is None or dst_acc is None:
                    continue
                bundle.add(
                    str(src_acc.id), str(dst_acc.id),
                    "two_leg", _rail_label(rail),
                )
    elif isinstance(rail, SingleLegRail):
        for leg_role in _expand_role_expression(rail.leg_role):
            acc = role_to_account.get(leg_role)
            if acc is None:
                continue
            bundle.add(
                str(acc.id), str(acc.id),
                "single_leg", _rail_label(rail),
            )


def _add_rail_edges(
    g: graphviz.Digraph,
    rail: Rail,
    role_to_account: dict[str, Account],
) -> None:
    """Legacy per-rail emit (still referenced by the layered/hierarchy
    diagrams' ad-hoc rendering paths). Kept as a thin compat wrapper —
    prefer ``_collect_rail_edges_for_accounts`` + ``_RailEdgeBundle``
    for any new caller so direction-bundled edges happen automatically.
    """
    if isinstance(rail, TwoLegRail):
        sources = _expand_role_expression(rail.source_role)
        destinations = _expand_role_expression(rail.destination_role)
        for src_role in sources:
            src_acc = role_to_account.get(src_role)
            for dst_role in destinations:
                dst_acc = role_to_account.get(dst_role)
                if src_acc is None or dst_acc is None:
                    continue
                g.edge(
                    str(src_acc.id),
                    str(dst_acc.id),
                    label=str(rail.name),
                    fontsize="9",
                    color="#1976d2",
                )
    elif isinstance(rail, SingleLegRail):
        for leg_role in _expand_role_expression(rail.leg_role):
            acc = role_to_account.get(leg_role)
            if acc is None:
                continue
            g.edge(
                str(acc.id),
                str(acc.id),
                label=str(rail.name),
                fontsize="9",
                style="dashed",
                color="#7b1fa2",
            )


def _collect_rail_edges_for_templates(
    g: graphviz.Digraph,
    rail: Rail,
    template_roles: set[str],
    role_to_template: dict[str, AccountTemplate],
    role_to_account: dict[str, Account],
    rendered_singletons: set[str],
    bundle: _RailEdgeBundle,
) -> None:
    """Template-focused diagram: collect each rail's edges into the
    bundle so parallel rails along the same direction render as one
    labeled edge.

    Template-roled legs draw against the dashed template node;
    singleton-roled legs draw against the singleton account node
    (added on-demand to ``rendered_singletons`` so each appears once).
    Rails that touch no template at all are skipped entirely.
    """
    if isinstance(rail, TwoLegRail):
        sources = _expand_role_expression(rail.source_role)
        destinations = _expand_role_expression(rail.destination_role)
        if not _rail_touches_template(sources, destinations, template_roles):
            return
        for src_role in sources:
            src_id = _template_or_singleton_node_id(
                g, src_role, template_roles, role_to_template,
                role_to_account, rendered_singletons,
            )
            if src_id is None:
                continue
            for dst_role in destinations:
                dst_id = _template_or_singleton_node_id(
                    g, dst_role, template_roles, role_to_template,
                    role_to_account, rendered_singletons,
                )
                if dst_id is None:
                    continue
                bundle.add(src_id, dst_id, "two_leg", _rail_label(rail))
    elif isinstance(rail, SingleLegRail):
        for leg_role in _expand_role_expression(rail.leg_role):
            if leg_role not in template_roles:
                continue
            template = role_to_template[leg_role]
            node_id = _template_node_id(template)
            bundle.add(node_id, node_id, "single_leg", _rail_label(rail))


def _add_template_rail_edges(
    g: graphviz.Digraph,
    rail: Rail,
    template_roles: set[str],
    role_to_template: dict[str, AccountTemplate],
    role_to_account: dict[str, Account],
    rendered_singletons: set[str],
) -> None:
    """Legacy per-rail emit. Kept as a thin wrapper for any external
    caller — the in-tree builder uses ``_collect_rail_edges_for_templates``
    + ``_RailEdgeBundle`` so parallel rails along the same direction
    render as one bundled edge.
    """
    if isinstance(rail, TwoLegRail):
        sources = _expand_role_expression(rail.source_role)
        destinations = _expand_role_expression(rail.destination_role)
        if not _rail_touches_template(sources, destinations, template_roles):
            return
        for src_role in sources:
            src_id = _template_or_singleton_node_id(
                g, src_role, template_roles, role_to_template,
                role_to_account, rendered_singletons,
            )
            if src_id is None:
                continue
            for dst_role in destinations:
                dst_id = _template_or_singleton_node_id(
                    g, dst_role, template_roles, role_to_template,
                    role_to_account, rendered_singletons,
                )
                if dst_id is None:
                    continue
                g.edge(
                    src_id,
                    dst_id,
                    label=str(rail.name),
                    fontsize="9",
                    color="#1976d2",
                )
    elif isinstance(rail, SingleLegRail):
        for leg_role in _expand_role_expression(rail.leg_role):
            if leg_role not in template_roles:
                continue
            template = role_to_template[leg_role]
            node_id = _template_node_id(template)
            g.edge(
                node_id,
                node_id,
                label=str(rail.name),
                fontsize="9",
                style="dashed",
                color="#7b1fa2",
            )


def _rail_touches_template(
    sources: tuple[str, ...],
    destinations: tuple[str, ...],
    template_roles: set[str],
) -> bool:
    """True iff any leg's role resolves to a declared AccountTemplate."""
    return any(r in template_roles for r in (*sources, *destinations))


def _template_or_singleton_node_id(
    g: graphviz.Digraph,
    role: str,
    template_roles: set[str],
    role_to_template: dict[str, AccountTemplate],
    role_to_account: dict[str, Account],
    rendered_singletons: set[str],
) -> str | None:
    """Resolve a role to a graph node id, adding the singleton on first use.

    Templates were already added by the builder; singletons get added
    lazily here the first time a template-touching rail references one,
    so unrelated singletons stay out of the diagram.
    """
    if role in template_roles:
        return _template_node_id(role_to_template[role])
    acc = role_to_account.get(role)
    if acc is None:
        return None
    acc_id = str(acc.id)
    if acc_id not in rendered_singletons:
        _add_account_node(g, acc)
        rendered_singletons.add(acc_id)
    return acc_id


def _expand_role_expression(expr: object) -> tuple[str, ...]:
    """RoleExpression is either a single Identifier or a tuple of them."""
    if isinstance(expr, tuple):
        return tuple(str(e) for e in expr)
    return (str(expr),)


def _add_chain_edge(g: graphviz.Digraph, chain: Chain) -> None:
    """Z.A: emit one edge per child in the row. Singleton-children
    rows render as solid ``required`` edges; multi-children rows
    render as dashed ``xor`` edges (one per sibling).
    """
    is_required = len(chain.children) == 1
    style = "solid" if is_required else "dashed"
    label = "required" if is_required else "xor"
    for child_spec in chain.children:
        g.edge(
            str(chain.parent),
            str(child_spec.name),
            label=label,
            fontsize="9",
            style=style,
            color="#666666",
        )


# -- App tree builder dispatch -----------------------------------------------


def _build_app(app_name: str):
    """Build the named app's tree against a default L2 + minimal Config.

    Used for ``render_dataflow`` — only needs the analysis structure
    (sheets + visuals + dataset refs), not a real datasource.
    """
    from recon_gen.common.config import Config
    from recon_gen.common.l2.loader import load_instance

    spec_example = load_instance(_TESTS_L2_DIR / "spec_example.yaml")
    cfg = Config(
        aws_account_id="000000000000",
        aws_region="us-east-2",
        deployment_name="qs-gen",
        db_table_prefix="spec_example",
        datasource_arn=(
            "arn:aws:quicksight:us-east-2:000000000000:"
            "datasource/qs-gen-demo-datasource"
        ),
        principal_arns=[
            "arn:aws:quicksight:us-east-2:000000000000:user/default/dummy"
        ],
    )
    return _APP_BUILDERS[app_name](cfg, l2_instance=spec_example)


def _build_l1_app(cfg, *, l2_instance):
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    return build_l1_dashboard_app(cfg, l2_instance=l2_instance)


def _build_l2ft_app(cfg, *, l2_instance):
    from recon_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
    return build_l2_flow_tracing_app(cfg, l2_instance=l2_instance)


def _build_inv_app(cfg, *, l2_instance):
    from recon_gen.apps.investigation.app import build_investigation_app
    return build_investigation_app(cfg, l2_instance=l2_instance)


def _build_exec_app(cfg, *, l2_instance):
    from recon_gen.apps.executives.app import build_executives_app
    return build_executives_app(cfg, l2_instance=l2_instance)


_APP_BUILDERS = {
    "l1_dashboard": _build_l1_app,
    "l2_flow_tracing": _build_l2ft_app,
    "investigation": _build_inv_app,
    "executives": _build_exec_app,
}


# -- Paths -------------------------------------------------------------------


_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
_CONCEPTUAL_DIR = _DOCS_DIR / "_diagrams" / "conceptual"
# Bundled L2 fixtures live inside the package at
# ``src/recon_gen/_l2_fixtures/`` (see ``main.py`` for the matching
# constant). Pre-restructure this walked up to ``<repo>/tests/l2/``,
# which broke ``render_dataflow`` from an installed wheel.
_TESTS_L2_DIR = (
    Path(__file__).parent.parent.parent / "_l2_fixtures"
)
