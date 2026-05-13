# pyright: reportMissingImports=false, reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# The `graphviz` package ships no type stubs, so every `Digraph.node()`
# / `.edge()` / `.subgraph()` call type-checks as `Unknown`. The L2-side
# logic (role collection, bundling, label rendering) IS strictly typed;
# only the graphviz-wrapper surface is untyped, and the SVG output is
# the verifiable contract. Suppressing graphviz noise here keeps the
# rest of the L2 module under strict pyright without per-line ignores.
"""Topology projection of an ``L2Instance`` — typed value object + renderers.

Two layers:

1. **The typed projection** (``TopologyGraph`` + ``TopologyNode`` +
   ``TopologyEdge``, built by ``topology_graph_for``). Pure data — one
   walk over an ``L2Instance``, no rendering. Both the X.4.b spike arms
   consume this: arm A (D3 + d3-force) reads it via ``to_d3_force_json``;
   arm B (post-processed graphviz) reads it via ``_render_to_graphviz``
   that re-emits a ``graphviz.Digraph``. One topology walk, two
   renderers — no duplicated traversal logic between the spike arms.
2. **The graphviz renderer** (``build_topology_graph`` +
   ``render_topology``). The pre-X.4 surface, preserved bit-for-bit:
   ``build_topology_graph(instance)`` returns the same ``graphviz.Digraph``
   shape it always has (now via ``_render_to_graphviz`` under the hood),
   and ``render_topology`` writes the same SVG. The handbook-diagram
   pipeline (``common/handbook/diagrams.py``) and the existing topology
   tests are unaffected.

The diagram surfaces (same in both renderer paths):

- **Roles** (nodes): every Role declared on an Account or
  AccountTemplate is one node. Internal vs external scope is styled
  visually (color + shape) so the analyst sees the institutional
  perimeter at a glance. Template-declared roles use a third style.
- **TwoLegRail** (directed edge): ``source_role -> destination_role``.
  Multiple rails between the same (source, destination) pair collapse
  into one bundled edge with a count + comma-joined rail-name label —
  keeps dense instances legible without losing per-rail names.
- **SingleLegRail** (self-loop): a single-leg rail attaches to its
  ``leg_role`` as a self-loop, with the leg direction in the label.
- **TransferTemplate** (cluster + node): each template renders as a
  subgraph cluster grouping the template's ``leg_rails`` (visually as
  template-name node + dotted membership edges to each leg-rail's
  endpoints), so the analyst sees "these N rails fire together as
  one shared Transfer".
- **Chain** (dashed edge): ``parent`` → ``child`` rendered as a dashed
  edge between rail/template nodes with ``required`` + ``xor_group``
  badged in the label.

Bundling rationale: real-world L2 instances easily declare 8+ rails
between the same (FRB, Customer DDA) pair. Drawing each as its own
edge clutters the graph; collapsing to one labeled edge with the rail
names + count keeps the institutional skeleton readable.

Engines (graphviz renderer): ``dot`` (default; hierarchical layout —
good for chain DAGs); ``neato`` / ``sfdp`` / ``fdp`` / ``twopi`` /
``circo`` available as fallbacks for force-directed layouts when the
graph has many cycles (common for instances with bidirectional rails
between counterparties).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias

from .primitives import (
    Account,
    AccountTemplate,
    ChainEntry,
    Identifier,
    L2Instance,
    Rail,
    Scope,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)


# Engines accepted by --engine. Map directly onto Graphviz's bundled
# layout binaries; the dot driver picks the binary based on this name.
_VALID_ENGINES = ("dot", "neato", "sfdp", "fdp", "twopi", "circo")


# -- Typed projection (the X.4.b.1 adapter) ---------------------------------


NodeKind: TypeAlias = Literal["role", "rail", "template"]
EdgeKind: TypeAlias = Literal[
    "rail_bundle", "self_loop", "template_member", "chain",
]


@dataclass(frozen=True, slots=True)
class TopologyNode:
    """A node in the L2 topology projection — role, rail, or template.

    ``id`` carries the discriminated prefix scheme used by the existing
    graphviz renderer (``role__<role>``, ``rail__<rail>``, ``tmpl__<name>``)
    so arm B's post-processed SVG can key off the rendered ``id`` attr to
    find each node and tag it with ``data-kind`` / ``data-id``.

    ``label`` is the human-readable display label (may contain ``\\n`` for
    multi-line). For templates it carries the ``<name>\\nkeys: <list>``
    inner label that the existing renderer puts on the template's
    ``shape="component"`` node.

    ``scope`` + ``templated`` are role-only (``None`` / ``False`` for
    rails + templates). ``metadata`` carries kind-specific extras the
    renderer may need but the typed model doesn't promote to first-class
    fields:

    - On a ``template`` node: ``transfer_type`` (str) + ``transfer_key``
      (comma-joined str) — both used by the graphviz renderer to build
      the cluster header text.
    - Open for future use (e.g., row-counts for the X.4.c.5 coverage tint).
    """

    id: str
    kind: NodeKind
    label: str
    scope: Scope | None = None
    templated: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    """An edge in the L2 topology projection.

    ``kind`` discriminates the four edge flavors:

    - ``rail_bundle`` — one or more parallel TwoLegRails between the
      same ``(source, destination)`` role pair. ``metadata`` carries
      ``rail_count`` (str-of-int) so the renderer can scale stroke
      width and the d3 side can show a count badge.
    - ``self_loop`` — a SingleLegRail rendered as a self-loop on its
      ``leg_role``. ``metadata`` carries ``direction`` (Debit / Credit /
      Variable).
    - ``template_member`` — a dotted membership edge from a
      TransferTemplate's node to one of its ``leg_rails``. The graphviz
      renderer wraps these inside the template's cluster.
    - ``chain`` — a ChainEntry parent → child relationship. ``metadata``
      carries ``required`` (str-bool) and optionally ``xor_group``.

    ``label`` is the human-readable display label (may be empty for
    membership edges; the graphviz renderer suppresses labels on those).
    """

    source: str
    target: str
    kind: EdgeKind
    label: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TopologyGraph:
    """Typed projection of an ``L2Instance``'s topology.

    Frozen value object — both spike arms read it; neither mutates.
    Iteration order (nodes + edges) is deterministic across runs of the
    same input, matching the existing graphviz renderer's walk so the
    rendered DOT stays stable for the docs-site diagrams that snapshot
    against it.
    """

    instance_name: str
    nodes: tuple[TopologyNode, ...]
    edges: tuple[TopologyEdge, ...]


# -- Internal styling constants (used by the graphviz renderer) -------------


@dataclass(frozen=True, slots=True)
class _RoleStyle:
    """Per-scope visual styling for a Role node."""

    fill: str
    border: str
    font: str
    shape: str


# Two scopes, two styles. Internal = soft blue (institution-side);
# external = soft yellow (counterparty / outside-the-perimeter). Both
# rounded rectangles for accounts; templates get a different shape so
# the analyst can tell "the role exists as a singleton" from "the role
# is templated and exists in many instances at runtime".
_INTERNAL_STYLE = _RoleStyle(
    fill="#dbe9f6", border="#1f4e79", font="#1f4e79", shape="box",
)
_EXTERNAL_STYLE = _RoleStyle(
    fill="#fff2cc", border="#7f6000", font="#7f6000", shape="box",
)
_TEMPLATE_STYLE = _RoleStyle(
    fill="#e8f0ff", border="#1f4e79", font="#1f4e79", shape="folder",
)
_RAIL_NODE_FILL = "#f5f5f5"
_RAIL_NODE_BORDER = "#666666"
_TRANSFER_TEMPLATE_FILL = "#fce4d6"
_TRANSFER_TEMPLATE_BORDER = "#a6622c"
_CHAIN_EDGE_COLOR = "#5a5a5a"
_BUNDLE_EDGE_COLOR = "#1f4e79"
_SELF_LOOP_COLOR = "#7f6000"


@dataclass(frozen=True, slots=True)
class _BundledEdge:
    """Aggregate of one or more two-leg rails sharing a (src, dst) pair."""

    source: Identifier
    destination: Identifier
    rail_names: tuple[Identifier, ...]
    transfer_types: tuple[str, ...]


def _role_id(role: Identifier) -> str:
    """Graphviz / d3 node id for a Role.

    Prefixing with ``role__`` avoids collision with rail / template node
    ids (``rail__X`` / ``tmpl__X``). The same string is what the
    rendered SVG carries in its ``<g id="...">`` attr — arm B's
    post-processor reads that prefix to assign ``data-kind="role"``.
    """
    return f"role__{role}"


def _rail_id(rail_name: Identifier) -> str:
    """Graphviz / d3 node id for a Rail (used by chain edges + template clusters)."""
    return f"rail__{rail_name}"


def _template_id(template_name: Identifier) -> str:
    """Graphviz / d3 node id for a TransferTemplate."""
    return f"tmpl__{template_name}"


def _scope_for_role(
    role: Identifier,
    accounts: Iterable[Account],
    templates: Iterable[AccountTemplate],
) -> Scope | None:
    """Return the scope that declares ``role``, or None if undeclared.

    A role is "declared" by an Account or AccountTemplate that names
    it. The same role may appear on both a singleton Account and a
    template — when that happens, the singleton's scope wins (it's the
    more concrete declaration). When neither declares the role (rails
    can reference roles that aren't declared anywhere — invalid per the
    SPEC validator, but the renderer must still degrade gracefully so
    integrators get a useful diagnostic), returns None.
    """
    for account in accounts:
        if account.role == role:
            return account.scope
    for template in templates:
        if template.role == role:
            return template.scope
    return None


def _is_templated(
    role: Identifier,
    templates: Iterable[AccountTemplate],
) -> bool:
    """True if any AccountTemplate declares this role.

    Templated roles are visually distinct (folder shape) from singleton
    roles (box) so the diagram surfaces "this role exists in many
    instances at runtime" without needing the analyst to read a legend.
    """
    return any(t.role == role for t in templates)


def _collect_roles(instance: L2Instance) -> tuple[Identifier, ...]:
    """All roles referenced by accounts, templates, or rails — sorted, deduped.

    Includes roles referenced only by rails (not declared on any
    Account / AccountTemplate) so the diagram still draws them — they
    render with the "undeclared" style as a soft hint at the data
    quality issue. Sorting ensures a stable graph layout across runs
    (the ``dot`` engine is stable for stable input order).
    """
    seen: set[Identifier] = set()
    for account in instance.accounts:
        if account.role is not None:
            seen.add(account.role)
    for template in instance.account_templates:
        seen.add(template.role)
    for rail in instance.rails:
        if isinstance(rail, TwoLegRail):
            seen.update(rail.source_role)
            seen.update(rail.destination_role)
        else:
            seen.update(rail.leg_role)
    return tuple(sorted(seen))


def _bundle_two_leg_rails(
    rails: Iterable[Rail],
) -> tuple[_BundledEdge, ...]:
    """Collapse parallel two-leg rails between the same (src, dst) pair.

    Each TwoLegRail's ``source_role`` / ``destination_role`` is a
    ``RoleExpression`` (tuple of admissible roles) — for the diagram we
    fan out across the cross-product so a rail with
    ``source_role: [A, B]`` and ``destination_role: [C]`` produces
    A→C and B→C bundled edges. This keeps the diagram showing every
    admissible flow path; the integrator can simplify rail definitions
    to collapse if visual density gets too high.

    Bundling key is ``(source, destination)`` so a rail named
    ``ExtInbound`` going A→B and another named ``WireIn`` going A→B
    collapse into one labeled "2 rails: ExtInbound, WireIn" edge.
    Sorting rail names within the bundle keeps the label deterministic.
    """
    pairs: dict[
        tuple[Identifier, Identifier],
        list[tuple[Identifier, str]],
    ] = {}
    for rail in rails:
        if not isinstance(rail, TwoLegRail):
            continue
        for source in rail.source_role:
            for destination in rail.destination_role:
                pairs.setdefault(
                    (source, destination), [],
                ).append((rail.name, rail.transfer_type))
    bundled: list[_BundledEdge] = []
    for (source, destination), entries in sorted(pairs.items()):
        sorted_entries = sorted(entries)
        bundled.append(
            _BundledEdge(
                source=source,
                destination=destination,
                rail_names=tuple(name for name, _ in sorted_entries),
                transfer_types=tuple(tt for _, tt in sorted_entries),
            )
        )
    return tuple(bundled)


def _bundle_label(bundle: _BundledEdge) -> str:
    """Pretty label for a bundled edge — count + rail names + types.

    When only one rail backs the edge, drop the count prefix to avoid
    "1 rail: Foo (ach)" noise. Multi-rail bundles get the count up
    front so visual scan picks out the high-traffic edges.
    """
    rail_count = len(bundle.rail_names)
    type_set = sorted(set(bundle.transfer_types))
    types_str = ", ".join(type_set)
    if rail_count == 1:
        return f"{bundle.rail_names[0]}\n({types_str})"
    rail_str = ", ".join(bundle.rail_names)
    return f"{rail_count} rails: {rail_str}\n({types_str})"


def _self_loop_label(rail: SingleLegRail) -> str:
    """Pretty label for a single-leg rail self-loop."""
    return (
        f"{rail.name}\n"
        f"({rail.transfer_type}, {rail.leg_direction})"
    )


def _chain_label(entry: ChainEntry) -> str:
    """Pretty label for a chain edge — required / xor flagged."""
    parts: list[str] = []
    if entry.required:
        parts.append("required")
    if entry.xor_group is not None:
        parts.append(f"xor: {entry.xor_group}")
    if parts:
        return "chain\n(" + ", ".join(parts) + ")"
    return "chain"


def _template_inner_label(template: TransferTemplate) -> str:
    """The template node's inner display label (kept stable for tests)."""
    return f"{template.name}\nkeys: " + ", ".join(template.transfer_key)


def _template_cluster_label(template: TransferTemplate) -> str:
    """The cluster's outer header text (graphviz-renderer-specific)."""
    return (
        f"TransferTemplate: {template.name}\n"
        f"({template.transfer_type})"
    )


def topology_graph_for(instance: L2Instance) -> TopologyGraph:
    """Walk an L2Instance and return its typed topology projection.

    Pure construction — no graphviz import, no rendering, no I/O. Both
    spike arms consume this single projection so the topology walk
    isn't duplicated between renderers.

    Iteration order matches the legacy ``build_topology_graph`` walk
    (roles sorted; templates in declaration order; chains in
    declaration order) so the graphviz renderer that consumes it
    produces the same DOT shape it always did.
    """
    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []

    # 1. Role nodes — sorted, scope/templated tagged.
    for role in _collect_roles(instance):
        scope = _scope_for_role(
            role, instance.accounts, instance.account_templates,
        )
        templated = _is_templated(role, instance.account_templates)
        nodes.append(TopologyNode(
            id=_role_id(role),
            kind="role",
            label=str(role),
            scope=scope,
            templated=templated,
        ))

    # 2. Template nodes (one per TransferTemplate) — declaration order.
    for template in instance.transfer_templates:
        nodes.append(TopologyNode(
            id=_template_id(template.name),
            kind="template",
            label=_template_inner_label(template),
            metadata={
                "transfer_type": template.transfer_type,
                "transfer_key": ", ".join(template.transfer_key),
                "cluster_label": _template_cluster_label(template),
            },
        ))

    # 3. Rail nodes — every rail referenced by a template OR a chain.
    # Templates own their leg-rails as cluster children (graphviz puts
    # them inside the cluster); standalone chain-referenced rails sit
    # at the top level. Both go in the typed graph as ``kind=rail``.
    rails_in_templates: set[Identifier] = set()
    for template in instance.transfer_templates:
        rails_in_templates.update(template.leg_rails)
    chain_referenced: set[Identifier] = set()
    for chain in instance.chains:
        chain_referenced.add(chain.parent)
        chain_referenced.add(chain.child)
    template_names: set[Identifier] = {
        t.name for t in instance.transfer_templates
    }

    # 3a. Rails inside templates — preserve template + leg_rails order.
    seen_rail_ids: set[str] = set()
    for template in instance.transfer_templates:
        for rail_name in template.leg_rails:
            rail_id = _rail_id(rail_name)
            if rail_id in seen_rail_ids:
                continue
            seen_rail_ids.add(rail_id)
            nodes.append(TopologyNode(
                id=rail_id,
                kind="rail",
                label=str(rail_name),
            ))

    # 3b. Standalone chain-referenced rails (sorted, matching legacy).
    for ref in sorted(chain_referenced):
        if ref in template_names:
            continue
        rail_id = _rail_id(ref)
        if rail_id in seen_rail_ids:
            continue
        seen_rail_ids.add(rail_id)
        nodes.append(TopologyNode(
            id=rail_id,
            kind="rail",
            label=str(ref),
        ))

    # 4. Edges by kind — bundle, self-loop, template-member, chain.
    # Order matches the legacy walk for DOT stability.

    # 4a. Two-leg bundles (sorted by (source, destination) pair).
    for bundle in _bundle_two_leg_rails(instance.rails):
        edges.append(TopologyEdge(
            source=_role_id(bundle.source),
            target=_role_id(bundle.destination),
            kind="rail_bundle",
            label=_bundle_label(bundle),
            metadata={
                "rail_count": str(len(bundle.rail_names)),
                "rail_names": ", ".join(bundle.rail_names),
                "transfer_types": ", ".join(sorted(set(bundle.transfer_types))),
            },
        ))

    # 4b. Single-leg self-loops (declaration order; leg_role expansion order).
    for rail in instance.rails:
        if not isinstance(rail, SingleLegRail):
            continue
        for role in rail.leg_role:
            edges.append(TopologyEdge(
                source=_role_id(role),
                target=_role_id(role),
                kind="self_loop",
                label=_self_loop_label(rail),
                metadata={
                    "rail_name": str(rail.name),
                    "transfer_type": rail.transfer_type,
                    "direction": rail.leg_direction,
                },
            ))

    # 4c. Template-member edges (template → each leg rail).
    for template in instance.transfer_templates:
        for rail_name in template.leg_rails:
            edges.append(TopologyEdge(
                source=_template_id(template.name),
                target=_rail_id(rail_name),
                kind="template_member",
                label="",
            ))

    # 4d. Chain edges (declaration order).
    for chain in instance.chains:
        parent_id = (
            _template_id(chain.parent)
            if chain.parent in template_names
            else _rail_id(chain.parent)
        )
        child_id = (
            _template_id(chain.child)
            if chain.child in template_names
            else _rail_id(chain.child)
        )
        chain_metadata: dict[str, str] = {
            "required": "true" if chain.required else "false",
        }
        if chain.xor_group is not None:
            chain_metadata["xor_group"] = str(chain.xor_group)
        edges.append(TopologyEdge(
            source=parent_id,
            target=child_id,
            kind="chain",
            label=_chain_label(chain),
            metadata=chain_metadata,
        ))

    return TopologyGraph(
        instance_name=str(instance.instance),
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def to_d3_force_json(graph: TopologyGraph) -> dict[str, Any]:
    """Serialize a TopologyGraph for d3-force consumption.

    Output shape is the d3-force convention (``{"nodes": [...],
    "links": [...]}``) plus a top-level ``instance`` for the rendering
    page's title bar. Every value is JSON-serializable (str / int / bool
    / None / dict-of-strs).

    Each link's ``source`` / ``target`` are the string node IDs (d3 will
    resolve them to node references by ID on first tick) — same shape
    arm A's renderer expects without further massaging.

    ``metadata`` is preserved as a sub-object so arm A's renderer can
    surface rail counts / chain flags / direction hints in tooltips
    without a second fetch. Empty metadata is dropped to keep the JSON
    payload tight.
    """
    json_nodes: list[dict[str, Any]] = []
    for n in graph.nodes:
        node_dict: dict[str, Any] = {
            "id": n.id,
            "kind": n.kind,
            "label": n.label,
        }
        if n.scope is not None:
            node_dict["scope"] = n.scope
        if n.templated:
            node_dict["templated"] = True
        if n.metadata:
            node_dict["metadata"] = dict(n.metadata)
        json_nodes.append(node_dict)

    json_links: list[dict[str, Any]] = []
    for e in graph.edges:
        link_dict: dict[str, Any] = {
            "source": e.source,
            "target": e.target,
            "kind": e.kind,
            "label": e.label,
        }
        if e.metadata:
            link_dict["metadata"] = dict(e.metadata)
        json_links.append(link_dict)

    return {
        "instance": graph.instance_name,
        "nodes": json_nodes,
        "links": json_links,
    }


# -- Graphviz renderer (consumes the typed projection) ----------------------


def _style_for(scope: Scope | None, templated: bool) -> _RoleStyle:
    """Select node style by (scope, is-templated)."""
    if templated:
        return _TEMPLATE_STYLE
    if scope == "external":
        return _EXTERNAL_STYLE
    if scope == "internal":
        return _INTERNAL_STYLE
    # Undeclared role — fall through with the internal style as the
    # least-surprising default. The validator will reject the L2
    # instance separately; the renderer's job is just to not crash.
    return _INTERNAL_STYLE


def _render_to_graphviz(graph: TopologyGraph) -> Any:
    """Render a TopologyGraph as a ``graphviz.Digraph``.

    Preserves the legacy ``build_topology_graph`` walk order so the
    emitted DOT (and the rendered SVG, by extension) stays stable for
    the docs-site diagram pipeline + the property assertions in
    ``tests/unit/test_l2_topology.py``.
    """
    import graphviz

    g: Any = graphviz.Digraph(
        name=f"l2_topology_{graph.instance_name}",
        comment=f"L2 topology for instance '{graph.instance_name}'",
    )
    g.attr(rankdir="LR", splines="true", overlap="false")
    g.attr("node", style="filled,rounded", fontname="Helvetica")
    g.attr("edge", fontname="Helvetica", fontsize="10")

    # Group nodes by kind for the cluster reconstruction below.
    role_nodes = [n for n in graph.nodes if n.kind == "role"]
    template_nodes = [n for n in graph.nodes if n.kind == "template"]
    rail_nodes = {n.id: n for n in graph.nodes if n.kind == "rail"}

    # Build template_id → list-of-child-rail-ids from template_member edges.
    template_children: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.kind == "template_member":
            template_children.setdefault(edge.source, []).append(edge.target)

    # 1. Role nodes (top-level, in projection order = sorted).
    for node in role_nodes:
        style = _style_for(node.scope, node.templated)
        g.node(
            node.id,
            label=node.label,
            shape=style.shape,
            fillcolor=style.fill,
            color=style.border,
            fontcolor=style.font,
        )

    # 2. Bundle edges (placed after role nodes, before clusters — matches
    # the legacy walk order for DOT stability).
    for edge in graph.edges:
        if edge.kind != "rail_bundle":
            continue
        rail_count = int(edge.metadata.get("rail_count", "1"))
        g.edge(
            edge.source,
            edge.target,
            label=edge.label,
            color=_BUNDLE_EDGE_COLOR,
            penwidth=str(min(1.0 + 0.5 * rail_count, 4.0)),
        )

    # 3. Self-loop edges.
    for edge in graph.edges:
        if edge.kind != "self_loop":
            continue
        g.edge(
            edge.source,
            edge.target,
            label=edge.label,
            color=_SELF_LOOP_COLOR,
            style="solid",
        )

    # 4. Template clusters — each template node + its leg-rail children
    # + the dotted membership edges all live inside the cluster subgraph.
    rails_in_clusters: set[str] = set()
    for tmpl_node in template_nodes:
        cluster_name = f"cluster_tmpl_{tmpl_node.id.removeprefix('tmpl__')}"
        cluster_label = tmpl_node.metadata.get(
            "cluster_label", tmpl_node.label,
        )
        with g.subgraph(name=cluster_name) as sub:
            assert sub is not None  # graphviz returns subgraph in `with` form
            sub.attr(
                label=cluster_label,
                style="dashed,rounded",
                color=_TRANSFER_TEMPLATE_BORDER,
                fontcolor=_TRANSFER_TEMPLATE_BORDER,
                fontname="Helvetica",
                fontsize="11",
            )
            sub.node(
                tmpl_node.id,
                label=tmpl_node.label,
                shape="component",
                fillcolor=_TRANSFER_TEMPLATE_FILL,
                color=_TRANSFER_TEMPLATE_BORDER,
                fontcolor=_TRANSFER_TEMPLATE_BORDER,
                style="filled",
            )
            for child_rail_id in template_children.get(tmpl_node.id, ()):
                rails_in_clusters.add(child_rail_id)
                child_rail_node = rail_nodes.get(child_rail_id)
                if child_rail_node is None:
                    # Defensive: a template_member edge points at a rail
                    # node we didn't emit. Shouldn't happen — but if it
                    # does, skip rather than crash.
                    continue
                sub.node(
                    child_rail_id,
                    label=child_rail_node.label,
                    shape="ellipse",
                    fillcolor=_RAIL_NODE_FILL,
                    color=_RAIL_NODE_BORDER,
                    fontcolor=_RAIL_NODE_BORDER,
                    style="filled",
                )
                sub.edge(
                    tmpl_node.id,
                    child_rail_id,
                    style="dotted",
                    color=_TRANSFER_TEMPLATE_BORDER,
                    arrowhead="none",
                )

    # 5. Stand-alone rail nodes (referenced only by chain edges, not in
    # any template cluster).
    for rail_node in rail_nodes.values():
        if rail_node.id in rails_in_clusters:
            continue
        g.node(
            rail_node.id,
            label=rail_node.label,
            shape="ellipse",
            fillcolor=_RAIL_NODE_FILL,
            color=_RAIL_NODE_BORDER,
            fontcolor=_RAIL_NODE_BORDER,
            style="filled",
        )

    # 6. Chain edges (declaration order).
    for edge in graph.edges:
        if edge.kind != "chain":
            continue
        g.edge(
            edge.source,
            edge.target,
            label=edge.label,
            color=_CHAIN_EDGE_COLOR,
            style="dashed",
            fontcolor=_CHAIN_EDGE_COLOR,
        )

    return g


def build_topology_graph(instance: L2Instance) -> Any:
    """Build a Graphviz directed graph capturing the L2 topology.

    Pure construction — no rendering, no I/O. Returns a
    ``graphviz.Digraph`` ready for the caller to ``.render()`` or
    ``.source`` inspect. Typed as ``Any`` because the ``graphviz``
    package ships without type stubs; callers should treat the return
    value as opaque and use ``.render()`` / ``.source`` only.

    Internally walks the ``L2Instance`` once into a typed
    ``TopologyGraph`` (``topology_graph_for``) and renders that — so the
    same projection feeds the X.4.b spike's d3 arm without a second
    walk.

    Raises ``ImportError`` if the ``graphviz`` Python package isn't
    installed; ``render_topology`` surfaces this as a friendly CLI
    error.
    """
    return _render_to_graphviz(topology_graph_for(instance))


def render_topology(
    instance: L2Instance,
    output_path: Path,
    *,
    engine: str = "dot",
) -> Path:
    """Render an L2 topology diagram to an SVG file.

    Returns the actual on-disk path of the rendered SVG (Graphviz
    appends the format suffix when missing). Surfaces a friendly
    ``RuntimeError`` when the system ``dot`` binary isn't installed —
    the Python ``graphviz`` package is a wrapper, not a renderer, so
    the binary is the actual dependency that makes/breaks rendering.

    ``engine`` defaults to ``dot`` (hierarchical layout — good for
    chains). Force-directed alternatives ``neato`` / ``sfdp`` / ``fdp``
    / ``twopi`` / ``circo`` are accepted for instances where the
    hierarchical layout reads poorly (lots of bidirectional edges
    between counterparties).

    Raises:
        ImportError: the ``graphviz`` Python package isn't installed.
        ValueError: ``engine`` isn't one of the supported names.
        RuntimeError: the system ``dot`` binary is missing or fails.
    """
    if engine not in _VALID_ENGINES:
        raise ValueError(
            f"engine={engine!r} not supported; pick one of "
            f"{_VALID_ENGINES}"
        )
    try:
        import graphviz
    except ImportError as exc:
        raise ImportError(
            "The 'graphviz' Python package is required for L2 topology "
            "rendering. Install it with: pip install graphviz"
        ) from exc

    graph: Any = build_topology_graph(instance)
    graph.engine = engine

    # graphviz.render() appends the format suffix when the path doesn't
    # already carry it. Strip the suffix from the user-supplied path
    # before passing in, then put it back when reporting the actual
    # output path. This dance avoids the wrapper writing
    # "topology.svg.svg" when the caller passes a path already ending
    # in .svg.
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stem_path = output_path.with_suffix("")

    try:
        rendered: Any = graph.render(
            filename=str(stem_path),
            format="svg",
            cleanup=True,
            quiet=True,
        )
    except graphviz.ExecutableNotFound as exc:
        raise RuntimeError(
            "Graphviz 'dot' binary not found on PATH. Install it with "
            "your system package manager (Homebrew: 'brew install "
            "graphviz'; Debian/Ubuntu: 'apt install graphviz'; "
            "Fedora: 'dnf install graphviz')."
        ) from exc
    except graphviz.CalledProcessError as exc:
        raise RuntimeError(
            f"Graphviz '{engine}' failed to render the L2 topology: {exc}"
        ) from exc

    return Path(rendered)


__all__ = [
    "EdgeKind",
    "NodeKind",
    "TopologyEdge",
    "TopologyGraph",
    "TopologyNode",
    "build_topology_graph",
    "render_topology",
    "to_d3_force_json",
    "topology_graph_for",
]
