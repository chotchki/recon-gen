# pyright: reportMissingImports=false, reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# The `graphviz` package ships no type stubs, so every `Digraph.node()`
# / `.edge()` / `.subgraph()` call type-checks as `Unknown`. The L2-side
# logic (role collection, bundling, label rendering) IS strictly typed;
# only the graphviz-wrapper surface is untyped, and the SVG output is
# the verifiable contract. Suppressing graphviz noise here keeps the
# rest of the L2 module under strict pyright without per-line ignores.
"""Topology projection of an ``L2Instance`` — typed value object + renderer.

Two layers:

1. **The typed projection** (``TopologyGraph`` + ``TopologyNode`` +
   ``TopologyEdge``, built by ``topology_graph_for``). Pure data — one
   walk over an ``L2Instance``, no rendering. Studio's diagram chrome
   reads this for entity counts (rails / chains / templates / role
   scopes); the per-rail emitter also reuses it for role-node
   iteration so the typed walk isn't duplicated.
2. **The graphviz renderer** (``build_topology_graph_per_rail``).
   Builds a ``graphviz.Digraph`` with rails as first-class nodes
   (``src_role → rail → dst_role`` becomes a 3-rank chain dot can
   lay out deterministically). Bundle nodes consolidate parallel
   pure-connectivity rails (anchored rails — chain endpoints / template
   leg-rails — stay individual). Templates render as clusters around
   their leg-rails. Chains as dashed edges between rail/template
   nodes. Control_parent (subledger → control role) as dashed gray
   edges. Optional focus filter (``focus_node_id`` + smart-default
   hops) for click-to-zoom-in re-render.

The X.4.b spike (locked 2026-05-13) chose this rails-as-nodes /
graphviz-dot model over the d3-force alternative. The dot pivot
makes the user's mental "roles → rails → roles" reading fall out of
dot's rank algorithm with zero knobs; force-directed layouts required
extensive per-graph tuning. See ``docs/audits/x_4_b_diagram_renderer_spike.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from .primitives import (
    Account,
    AccountTemplate,
    Chain,
    Identifier,
    L2Instance,
    Rail,
    Scope,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)


# -- Typed projection -------------------------------------------------------


NodeKind: TypeAlias = Literal["role", "rail", "template"]
EdgeKind: TypeAlias = Literal[
    "rail_bundle", "self_loop", "template_member", "chain",
    "control_parent",
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

    ``kind`` discriminates the five edge flavors:

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
    - ``chain`` — a Chain row's parent → child relationship. One edge
      per child in ``chain.children`` (singleton row = 1 edge,
      multi-children row = N edges). ``metadata`` carries ``cardinality``
      (``"required"`` for singleton-children rows, ``"xor"`` for
      multi-children rows) and, for ``"xor"`` edges, ``xor_siblings``
      (the comma-joined sibling names so the renderer can group them).
    - ``control_parent`` — an Account / AccountTemplate's ``parent_role``
      relationship (subledger rolls up to control account). Structural,
      not flow — the chart-of-accounts hierarchy that explains why a
      "control" account exists even when no rail terminates on it.
      ``metadata`` carries ``child_kind`` ("account" / "template") so
      the renderer can style differently. When the parent role also
      carries one or more ``LimitSchedule`` entries, ``has_limits=true``
      flags it for cap-badge rendering.
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
_CONTROL_PARENT_COLOR = "#888888"


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


def _chain_label(chain: Chain, *, cardinality: Literal["required", "xor"]) -> str:
    """Pretty label for a chain edge — required (singleton) / xor (multi).

    For an ``xor`` edge the label calls out the sibling set so the
    renderer makes the alternation visible alongside any one
    individual edge.
    """
    if cardinality == "required":
        return "chain\n(required)"
    siblings = ", ".join(str(c) for c in chain.children)
    return f"chain\n(xor: {siblings})"


def _template_inner_label(template: TransferTemplate) -> str:
    """The template node's inner display label.

    Just the name. ``transfer_key`` and ``transfer_type`` previously
    inflated the label with infrastructure-only info; the cluster
    border carries the same name so the template is identifiable
    without doubling up.
    """
    return str(template.name)


def _template_cluster_label(template: TransferTemplate) -> str:
    """The cluster's outer header text — name only, see _template_inner_label."""
    return str(template.name)


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
        for child in chain.children:
            chain_referenced.add(child)
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

    # 4c.5 Control-parent edges (Account.parent_role + AccountTemplate.parent_role).
    # Structural hierarchy (subledger → control), not flow connectivity. Maps
    # cleanly to the user's "Layer 1" (chart-of-accounts) mental model — these
    # are the GL roll-up relationships the institution's reconciliation rests on.
    parents_with_limits: set[Identifier] = {
        ls.parent_role for ls in instance.limit_schedules
    }
    for account in instance.accounts:
        if account.parent_role is None or account.role is None:
            continue
        cp_metadata: dict[str, str] = {"child_kind": "account"}
        if account.parent_role in parents_with_limits:
            cp_metadata["has_limits"] = "true"
        edges.append(TopologyEdge(
            source=_role_id(account.role),
            target=_role_id(account.parent_role),
            kind="control_parent",
            label="controls",
            metadata=cp_metadata,
        ))
    for template in instance.account_templates:
        if template.parent_role is None:
            continue
        cp_metadata = {"child_kind": "template"}
        if template.parent_role in parents_with_limits:
            cp_metadata["has_limits"] = "true"
        edges.append(TopologyEdge(
            source=_role_id(template.role),
            target=_role_id(template.parent_role),
            kind="control_parent",
            label="controls",
            metadata=cp_metadata,
        ))

    # 4d. Chain edges (declaration order). Z.A: every chain row emits
    # one edge per child — singleton-children rows produce a single
    # ``required`` edge; multi-children rows produce N ``xor`` edges
    # whose ``xor_siblings`` metadata names the alternation set so the
    # renderer can group them.
    for chain in instance.chains:
        parent_id = (
            _template_id(chain.parent)
            if chain.parent in template_names
            else _rail_id(chain.parent)
        )
        cardinality: Literal["required", "xor"] = (
            "required" if len(chain.children) == 1 else "xor"
        )
        siblings_str = ",".join(str(c) for c in chain.children)
        for child_name in chain.children:
            child_id = (
                _template_id(child_name)
                if child_name in template_names
                else _rail_id(child_name)
            )
            chain_metadata: dict[str, str] = {"cardinality": cardinality}
            if cardinality == "xor":
                chain_metadata["xor_siblings"] = siblings_str
            edges.append(TopologyEdge(
                source=parent_id,
                target=child_id,
                kind="chain",
                label=_chain_label(chain, cardinality=cardinality),
                metadata=chain_metadata,
            ))

    return TopologyGraph(
        instance_name=str(instance.instance),
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def _focus_set(
    focus_node_id: str,
    adjacency: Mapping[str, set[str]],
) -> set[str]:
    """Compute the focus set: direct neighbors + complete rails.

    "Direct connections + complete rail" semantics (X.4.b polish,
    2026-05-13):

    1. Start with the focus node.
    2. Add all 1-hop neighbors (any edge kind).
    3. For any rail or bundle in the resulting set, also add its
       endpoint roles — so a rail you can see is always shown with
       BOTH its endpoint roles (no dangling half-edges).

    Avoids the "hops=2" expansion that previously picked up templates
    that own the touching rails, chain neighbors of those rails, and
    control_parents of the other-side roles. Those extras are
    semantically interesting in their own right but they're not what
    "show me this node and what touches it" should mean — they're a
    second click away (focus on the template, focus on the chain
    neighbor) when the user actually wants them.
    """
    focused: set[str] = {focus_node_id}
    # 1-hop direct neighbors.
    focused.update(adjacency.get(focus_node_id, ()))
    # Rail completion: pull in the OTHER endpoint role of any rail/
    # bundle in the set. ``rail__`` prefix covers both individual rails
    # (``rail__Foo``) and bundles (``rail__bundle_N``).
    to_add: set[str] = set()
    for node_id in focused:
        if not node_id.startswith("rail__"):
            continue
        for nbr in adjacency.get(node_id, ()):
            if nbr.startswith("role__"):
                to_add.add(nbr)
    focused |= to_add
    return focused


def build_topology_graph_per_rail(
    instance: L2Instance,
    *,
    bundle_parallel_rails: bool = True,
    focus_node_id: str | None = None,
    layer: int = 3,
) -> Any:
    """Build a Graphviz Digraph with Rails as first-class nodes (X.4.b dot pivot).

    Sibling to ``build_topology_graph`` (which models rails as edges
    between roles + clusters them inside templates). This view promotes
    every Rail to its own node + connects it to its endpoint roles via
    directed edges (``src_role → rail → dst_role`` for TwoLegRail;
    ``leg_role → rail`` or ``rail → leg_role`` for SingleLegRail by
    direction). The dot algorithm can then rank-layout the result —
    the user's mental "roles → rails → roles" 3-rank reading falls
    out of dot's DAG ranking deterministically, no force tuning, no
    knobs.

    The d3-force arm A's per-rail emit (``to_d3_per_rail_json``) drove
    the same model insight; this is the graphviz analog so the dot
    renderer can be re-evaluated against the layered reading the user
    wanted. Both emits share the bundling rule: pure-connectivity rails
    (TwoLegRails sharing exact source/destination role expressions AND
    SingleLegRails sharing leg_role/direction, with NEITHER referenced
    by any chain or template) collapse into one bundle node per group.
    Anchored rails (chain endpoints / template leg-rails) always stay
    individual since the sequencing/composition edges need stable
    rail identity.

    Templates render as clusters containing their leg-rail nodes;
    chains as dashed edges between rail/template nodes; control_parent
    as dashed edges between roles. Orphan roles (declared but
    unreferenced) are filtered at emit time so the dot layout stays
    focused on the connectivity story.

    ``bundle_parallel_rails`` (default True) is the bundling switch;
    set False to render every rail as its own node (denser graph,
    occasionally clearer for low-rail-count instances).

    ``focus_node_id`` (optional) — when set, filter the diagram to
    that node's "direct connections + complete rail" neighborhood
    (see ``_focus_set``). Adjacency is computed over the FULL graph
    (so bundle IDs stay stable across full-vs-focused renders).
    Nodes / edges outside the focus set are skipped at emit time;
    dot re-lays out the smaller subgraph cleanly. Click-away in the
    chrome navigates back to the no-focus URL to restore the full
    picture.

    ``layer`` (1 / 2 / 3, default 3) — conceptual progressive disclosure
    of the model:

    - ``1`` — roles + control hierarchy only (chart of accounts).
    - ``2`` — adds rails + their endpoint connectivity.
    - ``3`` — adds chains + transfer templates (the full diagram).

    Implemented as a server-side filter so dot re-lays-out the smaller
    subset cleanly per layer (the same "click to zoom in, get a fresh
    layout" pattern the focus filter uses). Default 3 keeps Python
    callers (tests, etc.) seeing the full diagram unless they ask
    otherwise.

    Returns a ``graphviz.Digraph`` ready for ``.render()`` or
    ``.source`` inspection. Typed as ``Any`` because the ``graphviz``
    package ships without type stubs.
    """
    import graphviz

    g: Any = graphviz.Digraph(
        name=f"l2_topology_per_rail_{instance.instance}",
        comment=(
            f"L2 topology (rails as nodes) for instance "
            f"'{instance.instance}'"
        ),
    )
    # Compactness pass: tighter node/rank spacing, higher mclimit (more
    # iterations spent reducing edge crossings), splines=polyline
    # (straight segments with bends — at-least-as-good as spline at
    # small graphs, ~30% smaller PNG and faster on dense real-world
    # ones), 10pt node fontsize (free compaction; default 14pt was too
    # large for typical rail names). Trades CPU for visual density —
    # sasquatch_pr-scale lays out under 200ms.
    #
    # WHY no `concentrate=true`: at L3 (clusters + cross-cluster chain
    # edges) graphviz emits `Error: rebuild_vlists: lead is null for
    # rank 3 / concentrate=true may not work correctly` and produces
    # NaN coordinates → 8x8 viewBox, all paths `Mnan,-nan…`. Dropping
    # the option costs minor parallel-edge consolidation we never
    # really benefited from (rail edges are 1:1 src→dst by construction).
    g.attr(
        rankdir="LR",
        splines="polyline",
        overlap="false",
        nodesep="0.15",
        ranksep="0.35",
        mclimit="2.0",
    )
    g.attr("node", style="filled,rounded", fontname="Helvetica", fontsize="10")
    g.attr("edge", fontname="Helvetica", fontsize="9")

    rail_names_set: set[Identifier] = {r.name for r in instance.rails}
    template_names_set: set[Identifier] = {
        t.name for t in instance.transfer_templates
    }
    rails_by_name: dict[Identifier, Rail] = {r.name: r for r in instance.rails}

    # Anchored = referenced by a chain or a template's leg_rails. These
    # never bundle since chain/template edges need stable rail identity.
    anchored_rails: set[Identifier] = set()
    for chain in instance.chains:
        if chain.parent in rail_names_set:
            anchored_rails.add(chain.parent)
        for child in chain.children:
            if child in rail_names_set:
                anchored_rails.add(child)
    for tmpl in instance.transfer_templates:
        for rn in tmpl.leg_rails:
            if rn in rail_names_set:
                anchored_rails.add(rn)

    # Compute bundling. rail_to_bundle[name] -> bundle_id (when bundled).
    # bundles: list of (bundle_id, label, key) emit-ordered.
    rail_to_bundle: dict[Identifier, str] = {}
    bundles: list[
        tuple[
            str,
            str,
            tuple[str, tuple[Identifier, ...], tuple[Identifier, ...] | str],
        ]
    ] = []
    if bundle_parallel_rails:
        groups: dict[
            tuple[str, tuple[Identifier, ...], tuple[Identifier, ...] | str],
            list[Rail],
        ] = {}
        for rail in instance.rails:
            if isinstance(rail, TwoLegRail):
                key: tuple[
                    str, tuple[Identifier, ...], tuple[Identifier, ...] | str,
                ] = (
                    "twoleg",
                    tuple(rail.source_role),
                    tuple(rail.destination_role),
                )
            else:
                key = ("singleleg", tuple(rail.leg_role), rail.leg_direction)
            groups.setdefault(key, []).append(rail)

        bundle_idx = 0
        for key, rails_in_group in groups.items():
            unanchored = [
                r for r in rails_in_group if r.name not in anchored_rails
            ]
            if len(unanchored) < 2:
                continue
            bundle_id = f"rail__bundle_{bundle_idx}"
            bundle_idx += 1
            names_sorted = sorted(str(r.name) for r in unanchored)
            # Names only — transfer_type / leg_direction were on a
            # trailing line but added noise. Direction's still implicit
            # in the bundle's edge arrowheads.
            bundle_label = (
                f"{len(unanchored)} rails:\n"
                + "\n".join(names_sorted)
            )
            for r in unanchored:
                rail_to_bundle[r.name] = bundle_id
            bundles.append((bundle_id, bundle_label, key))

    # Roles referenced by anything we'll emit (rails or control_parent).
    # Filters orphans the same way _filter_orphan_role_nodes does for
    # the bundled view — declared-but-unused accounts stay out of the
    # diagram so dot's rank focuses on the connectivity story.
    referenced_roles: set[Identifier] = set()
    for rail in instance.rails:
        if isinstance(rail, TwoLegRail):
            referenced_roles.update(rail.source_role)
            referenced_roles.update(rail.destination_role)
        else:
            referenced_roles.update(rail.leg_role)
    for account in instance.accounts:
        if account.parent_role is not None and account.role is not None:
            referenced_roles.add(account.role)
            referenced_roles.add(account.parent_role)
    for tmpl_acc in instance.account_templates:
        if tmpl_acc.parent_role is not None:
            referenced_roles.add(tmpl_acc.role)
            referenced_roles.add(tmpl_acc.parent_role)

    # Focus filter: BFS from focus_node_id over the FULL adjacency, then
    # only emit nodes/edges in the resulting set. Adjacency walk uses the
    # same node IDs the rest of the function emits (role__/rail__/tmpl__/
    # rail__bundle_N). Bundle IDs are deterministic from instance.rails
    # iteration order, so they stay stable across full-vs-focused renders.
    focus_set: set[str] | None = None
    if focus_node_id is not None:
        adjacency: dict[str, set[str]] = {}

        def _add_adj(a: str, b: str) -> None:
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)

        # Rail/bundle ↔ role endpoints.
        for rail in instance.rails:
            anchor_id = rail_to_bundle.get(rail.name) or _rail_id(rail.name)
            if isinstance(rail, TwoLegRail):
                for src_role in rail.source_role:
                    _add_adj(anchor_id, _role_id(src_role))
                for dst_role in rail.destination_role:
                    _add_adj(anchor_id, _role_id(dst_role))
            else:
                for leg_role in rail.leg_role:
                    _add_adj(anchor_id, _role_id(leg_role))

        # Template ↔ leg-rail membership.
        for tmpl in instance.transfer_templates:
            for rn in tmpl.leg_rails:
                if rn not in rail_names_set:
                    continue
                rail_anchor = rail_to_bundle.get(rn) or _rail_id(rn)
                _add_adj(_template_id(tmpl.name), rail_anchor)

        # Chain edges (rail/template ↔ rail/template) — one edge per
        # child in the row.
        for chain in instance.chains:
            parent_id = (
                _template_id(chain.parent)
                if chain.parent in template_names_set
                else _rail_id(chain.parent)
            )
            for child_name in chain.children:
                child_id = (
                    _template_id(child_name)
                    if child_name in template_names_set
                    else _rail_id(child_name)
                )
                _add_adj(parent_id, child_id)

        # Control-parent edges (subledger ↔ control role).
        for account in instance.accounts:
            if account.parent_role is not None and account.role is not None:
                _add_adj(
                    _role_id(account.role),
                    _role_id(account.parent_role),
                )
        for tmpl_acc in instance.account_templates:
            if tmpl_acc.parent_role is not None:
                _add_adj(
                    _role_id(tmpl_acc.role),
                    _role_id(tmpl_acc.parent_role),
                )

        # "Direct connections + complete rail" — see ``_focus_set``.
        focus_set = _focus_set(focus_node_id, adjacency)

    def _in_focus(node_id: str) -> bool:
        return focus_set is None or node_id in focus_set

    # Layer thresholds. Layer 1 = roles + control_parent only. Layer 2
    # = + rails + their endpoint connectivity. Layer 3 = + chains +
    # transfer-template clusters. Server-side filter so dot re-lays-out
    # the smaller subset cleanly per layer.
    show_rails = layer >= 2
    show_chains_and_templates = layer >= 3

    # Phase A — Role nodes (top-level, referenced only, in focus).
    typed = topology_graph_for(instance)
    for n in typed.nodes:
        if n.kind != "role":
            continue
        role_name = Identifier(n.id.removeprefix("role__"))
        if role_name not in referenced_roles:
            continue
        if not _in_focus(n.id):
            continue
        style = _style_for(n.scope, n.templated)
        g.node(
            n.id,
            label=n.label,
            shape=style.shape,
            fillcolor=style.fill,
            color=style.border,
            fontcolor=style.font,
        )

    def _emit_rail_node(g_or_sub: Any, rail: Rail) -> None:
        # Rail name only — transfer_type was on a second line but added
        # noise that made every rail visually dominant. Direction for
        # single-leg rails is conveyed by the arrowhead direction
        # (rail → leg_role for Credit, leg_role → rail for Debit).
        g_or_sub.node(
            _rail_id(rail.name),
            label=str(rail.name),
            shape="ellipse",
            fillcolor=_RAIL_NODE_FILL,
            color=_RAIL_NODE_BORDER,
            fontcolor=_RAIL_NODE_BORDER,
            style="filled",
        )

    # Phase B — Templates as clusters with their leg-rail nodes inside.
    # Cluster only emits if either the template itself OR any of its
    # leg-rails are in focus. Inside the cluster, each leg-rail is
    # emitted only if in focus. Layer-gated: clusters only show at L3.
    rails_in_clusters: set[Identifier] = set()
    template_iter = (
        instance.transfer_templates if show_chains_and_templates else ()
    )
    for template in template_iter:
        tmpl_id = _template_id(template.name)
        in_template_legs = [
            rn for rn in template.leg_rails
            if rn in rail_names_set and _in_focus(_rail_id(rn))
        ]
        if not _in_focus(tmpl_id) and not in_template_legs:
            continue
        cluster_name = f"cluster_tmpl_{template.name}"
        cluster_label = _template_cluster_label(template)
        with g.subgraph(name=cluster_name) as sub:
            assert sub is not None
            sub.attr(
                label=cluster_label,
                style="dashed,rounded",
                color=_TRANSFER_TEMPLATE_BORDER,
                fontcolor=_TRANSFER_TEMPLATE_BORDER,
                fontname="Helvetica",
                fontsize="11",
            )
            if _in_focus(tmpl_id):
                sub.node(
                    tmpl_id,
                    label=_template_inner_label(template),
                    shape="component",
                    fillcolor=_TRANSFER_TEMPLATE_FILL,
                    color=_TRANSFER_TEMPLATE_BORDER,
                    fontcolor=_TRANSFER_TEMPLATE_BORDER,
                    style="filled",
                )
            for rail_name in template.leg_rails:
                if rail_name not in rail_names_set:
                    continue
                if not _in_focus(_rail_id(rail_name)):
                    continue
                rail = rails_by_name[rail_name]
                _emit_rail_node(sub, rail)
                rails_in_clusters.add(rail_name)
                if _in_focus(tmpl_id):
                    sub.edge(
                        tmpl_id,
                        _rail_id(rail_name),
                        style="dotted",
                        color=_TRANSFER_TEMPLATE_BORDER,
                        arrowhead="none",
                    )

    # Phase C — Top-level individual rails (not bundled, not in a cluster).
    # Layer-gated: rail nodes only show at L2+.
    for rail in instance.rails:
        if not show_rails:
            break
        if rail.name in rail_to_bundle:
            continue
        if rail.name in rails_in_clusters:
            continue
        if not _in_focus(_rail_id(rail.name)):
            continue
        _emit_rail_node(g, rail)

    # Phase D — Bundle nodes (top-level). Layer-gated.
    for bundle_id, bundle_label, _key in bundles:
        if not show_rails:
            break
        if not _in_focus(bundle_id):
            continue
        g.node(
            bundle_id,
            label=bundle_label,
            shape="ellipse",
            fillcolor="#e8e8e8",
            color=_BUNDLE_EDGE_COLOR,
            fontcolor=_RAIL_NODE_BORDER,
            style="filled",
            penwidth="1.5",
        )

    # Phase E — Endpoint edges. Individual rails: src→rail→dst as 2 edges.
    # Bundles: same shape but consolidated onto the bundle node.
    # Each edge requires both endpoints in focus. Layer-gated.
    for rail in instance.rails:
        if not show_rails:
            break
        if rail.name in rail_to_bundle:
            continue
        rail_node_id = _rail_id(rail.name)
        if not _in_focus(rail_node_id):
            continue
        if isinstance(rail, TwoLegRail):
            for src_role in rail.source_role:
                if not _in_focus(_role_id(src_role)):
                    continue
                g.edge(
                    _role_id(src_role), rail_node_id,
                    color=_BUNDLE_EDGE_COLOR,
                    arrowhead="none",
                )
            for dst_role in rail.destination_role:
                if not _in_focus(_role_id(dst_role)):
                    continue
                g.edge(
                    rail_node_id, _role_id(dst_role),
                    color=_BUNDLE_EDGE_COLOR,
                    arrowhead="open",
                )
        else:
            for leg_role in rail.leg_role:
                if not _in_focus(_role_id(leg_role)):
                    continue
                if rail.leg_direction == "Credit":
                    g.edge(
                        rail_node_id, _role_id(leg_role),
                        color=_SELF_LOOP_COLOR,
                        arrowhead="open",
                    )
                else:
                    g.edge(
                        _role_id(leg_role), rail_node_id,
                        color=_SELF_LOOP_COLOR,
                        arrowhead="none",
                    )

    for bundle_id, _label, key in bundles:
        if not show_rails:
            break
        if not _in_focus(bundle_id):
            continue
        if key[0] == "twoleg":
            src_tuple = key[1]
            dst_tuple = key[2]
            assert isinstance(dst_tuple, tuple)
            penwidth = str(min(1.0 + 0.3 * len([
                r for r in instance.rails if rail_to_bundle.get(r.name) == bundle_id
            ]), 3.0))
            for src_role in src_tuple:
                if not _in_focus(_role_id(src_role)):
                    continue
                g.edge(
                    _role_id(src_role), bundle_id,
                    color=_BUNDLE_EDGE_COLOR,
                    arrowhead="none",
                    penwidth=penwidth,
                )
            for dst_role in dst_tuple:
                if not _in_focus(_role_id(dst_role)):
                    continue
                g.edge(
                    bundle_id, _role_id(dst_role),
                    color=_BUNDLE_EDGE_COLOR,
                    arrowhead="open",
                    penwidth=penwidth,
                )
        else:
            leg_tuple = key[1]
            direction = str(key[2])
            for leg_role in leg_tuple:
                if not _in_focus(_role_id(leg_role)):
                    continue
                if direction == "Credit":
                    g.edge(
                        bundle_id, _role_id(leg_role),
                        color=_SELF_LOOP_COLOR,
                        arrowhead="open",
                        penwidth="1.5",
                    )
                else:
                    g.edge(
                        _role_id(leg_role), bundle_id,
                        color=_SELF_LOOP_COLOR,
                        arrowhead="none",
                        penwidth="1.5",
                    )

    # Phase F — Chain edges (rail → rail or template → template).
    # Layer-gated: chains only show at L3. Z.A: emit one edge per
    # child in the row.
    for chain in instance.chains:
        if not show_chains_and_templates:
            break
        parent_id = (
            _template_id(chain.parent)
            if chain.parent in template_names_set
            else _rail_id(chain.parent)
        )
        cardinality: Literal["required", "xor"] = (
            "required" if len(chain.children) == 1 else "xor"
        )
        for child_name in chain.children:
            child_id = (
                _template_id(child_name)
                if child_name in template_names_set
                else _rail_id(child_name)
            )
            if not (_in_focus(parent_id) and _in_focus(child_id)):
                continue
            g.edge(
                parent_id, child_id,
                label=_chain_label(chain, cardinality=cardinality),
                color=_CHAIN_EDGE_COLOR,
                style="dashed",
                fontcolor=_CHAIN_EDGE_COLOR,
            )

    # Phase G — Control-parent edges (subledger → control role).
    parents_with_limits: set[Identifier] = {
        ls.parent_role for ls in instance.limit_schedules
    }
    for account in instance.accounts:
        if account.parent_role is None or account.role is None:
            continue
        src = _role_id(account.role)
        dst = _role_id(account.parent_role)
        if not (_in_focus(src) and _in_focus(dst)):
            continue
        cp_label = "controls"
        if account.parent_role in parents_with_limits:
            cp_label = "controls\n($ caps)"
        g.edge(
            src, dst,
            label=cp_label,
            color=_CONTROL_PARENT_COLOR,
            style="dashed",
            fontcolor=_CONTROL_PARENT_COLOR,
            arrowhead="onormal",
        )
    for tmpl_acc in instance.account_templates:
        if tmpl_acc.parent_role is None:
            continue
        src = _role_id(tmpl_acc.role)
        dst = _role_id(tmpl_acc.parent_role)
        if not (_in_focus(src) and _in_focus(dst)):
            continue
        cp_label = "controls"
        if tmpl_acc.parent_role in parents_with_limits:
            cp_label = "controls\n($ caps)"
        g.edge(
            src, dst,
            label=cp_label,
            color=_CONTROL_PARENT_COLOR,
            style="dashed",
            fontcolor=_CONTROL_PARENT_COLOR,
            arrowhead="onormal",
        )

    return g


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


def visible_entities_for(
    instance: L2Instance,
    focus_node_id: str | None,
) -> Mapping[str, frozenset[str]]:
    """Return the L2 entity IDs visible in a focused diagram subgraph.

    Used by Studio's home page (X.4.f.8) to filter the entity-card
    sections when the operator clicks a node in the diagram. The keys
    are the editor-route entity-kind slugs (``account``,
    ``account_template``, ``rail``, ``transfer_template``, ``chain``,
    ``limit_schedule``); the values are frozen sets of entity IDs in
    the same shape Studio's ``/l2_shape/<kind>/<id>`` URLs use:

    - ``account.id``, ``account_template.role``, ``rail.name``,
      ``transfer_template.name``;
    - ``"<parent>::<child>"`` composite for chains and
      ``"<parent_role>::<transfer_type>"`` composite for
      limit_schedules (matches ``_entity_id`` in
      ``_studio_editor_routes``).

    When ``focus_node_id`` is None or the node ID is unrecognized
    (typo / stale URL / synthetic bundle id like ``rail__bundle_3``
    that doesn't have a matching individual rail), returns the FULL
    set per kind so the home page un-filters cleanly.

    Adjacency is built directly from ``instance`` (rather than from
    ``topology_graph_for``'s typed projection) so each Rail keeps its
    own role↔rail edges instead of being collapsed into a bundle
    edge — focusing on a single Rail must still pull in its endpoint
    roles even when several parallel rails share those roles.
    """
    all_entities = _all_entities_per_kind(instance)
    if focus_node_id is None:
        return all_entities

    adjacency: dict[str, set[str]] = {}

    def _add(a: str, b: str) -> None:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    # Rail ↔ role endpoints (per individual rail, not the bundle aggregate).
    for rail in instance.rails:
        rail_id = _rail_id(rail.name)
        if isinstance(rail, TwoLegRail):
            for r in rail.source_role:
                _add(rail_id, _role_id(r))
            for r in rail.destination_role:
                _add(rail_id, _role_id(r))
        else:
            for r in rail.leg_role:
                _add(rail_id, _role_id(r))

    # Template ↔ leg-rail membership.
    for tmpl in instance.transfer_templates:
        for rn in tmpl.leg_rails:
            _add(_template_id(tmpl.name), _rail_id(rn))

    # Chain edges (rail/template ↔ rail/template) — one edge per
    # child in the row.
    template_names_set = {t.name for t in instance.transfer_templates}
    for chain in instance.chains:
        parent_id = (
            _template_id(chain.parent)
            if chain.parent in template_names_set
            else _rail_id(chain.parent)
        )
        for child_name in chain.children:
            child_id = (
                _template_id(child_name)
                if child_name in template_names_set
                else _rail_id(child_name)
            )
            _add(parent_id, child_id)

    # Control-parent edges (role ↔ role).
    for account in instance.accounts:
        if account.parent_role is not None and account.role is not None:
            _add(_role_id(account.role), _role_id(account.parent_role))
    for tmpl_acc in instance.account_templates:
        if tmpl_acc.parent_role is not None:
            _add(_role_id(tmpl_acc.role), _role_id(tmpl_acc.parent_role))

    if focus_node_id not in adjacency:
        # Unknown / synthetic node (e.g., rail__bundle_N) — un-filter.
        return all_entities

    focus_set = _focus_set(focus_node_id, adjacency)
    visible_roles: set[str] = {
        n.removeprefix("role__") for n in focus_set
        if n.startswith("role__")
    }
    visible_rail_names: set[str] = {
        n.removeprefix("rail__") for n in focus_set
        if n.startswith("rail__") and not n.startswith("rail__bundle_")
    }
    visible_template_names: set[str] = {
        n.removeprefix("tmpl__") for n in focus_set
        if n.startswith("tmpl__")
    }
    rail_or_tmpl = visible_rail_names | visible_template_names

    accounts = frozenset(
        str(a.id) for a in instance.accounts
        if (a.role is not None and str(a.role) in visible_roles)
        or (a.parent_role is not None and str(a.parent_role) in visible_roles)
    )
    account_templates = frozenset(
        str(t.role) for t in instance.account_templates
        if str(t.role) in visible_roles
        or (t.parent_role is not None and str(t.parent_role) in visible_roles)
    )
    rails = frozenset(
        str(r.name) for r in instance.rails
        if str(r.name) in visible_rail_names
    )
    transfer_templates = frozenset(
        str(t.name) for t in instance.transfer_templates
        if str(t.name) in visible_template_names
    )
    # Z.A: chain composite key = "parent::sorted-children-csv" — the
    # same shape the editor's _find_entity uses to address Chain rows.
    chains = frozenset(
        f"{c.parent}::{','.join(sorted(str(ch) for ch in c.children))}"
        for c in instance.chains
        if str(c.parent) in rail_or_tmpl
        or any(str(ch) in rail_or_tmpl for ch in c.children)
    )
    limit_schedules = frozenset(
        f"{ls.parent_role}::{ls.transfer_type}"
        for ls in instance.limit_schedules
        if str(ls.parent_role) in visible_roles
    )
    return {
        "account": accounts,
        "account_template": account_templates,
        "rail": rails,
        "transfer_template": transfer_templates,
        "chain": chains,
        "limit_schedule": limit_schedules,
    }


def _all_entities_per_kind(
    instance: L2Instance,
) -> Mapping[str, frozenset[str]]:
    """Full entity-id set per kind — used as the no-focus / unknown-focus
    return value of ``visible_entities_for``."""
    return {
        "account": frozenset(str(a.id) for a in instance.accounts),
        "account_template": frozenset(
            str(t.role) for t in instance.account_templates
        ),
        "rail": frozenset(str(r.name) for r in instance.rails),
        "transfer_template": frozenset(
            str(t.name) for t in instance.transfer_templates
        ),
        "chain": frozenset(
            f"{c.parent}::{','.join(sorted(str(ch) for ch in c.children))}"
            for c in instance.chains
        ),
        "limit_schedule": frozenset(
            f"{ls.parent_role}::{ls.transfer_type}"
            for ls in instance.limit_schedules
        ),
    }


__all__ = [
    "EdgeKind",
    "NodeKind",
    "TopologyEdge",
    "TopologyGraph",
    "TopologyNode",
    "build_topology_graph_per_rail",
    "topology_graph_for",
    "visible_entities_for",
]

