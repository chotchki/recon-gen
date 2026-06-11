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

    - On a ``template`` node: ``transfer_key`` (comma-joined str) — used
      by the graphviz renderer to build the cluster header text.
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
# CF.3.f — shape vocabulary v0.1 (operator-locked 2026-06-04).
# Roles get distinct silhouettes by scope: cylinder = institution-side
# ledger; note = external counterparty; folder = templated role (many
# runtime instances). Distinct shape signals type at a glance — no
# badges, no info-density chrome.
_INTERNAL_STYLE = _RoleStyle(
    fill="#dbe9f6", border="#1f4e79", font="#1f4e79", shape="cylinder",
)
_EXTERNAL_STYLE = _RoleStyle(
    fill="#fff2cc", border="#7f6000", font="#7f6000", shape="note",
)
_TEMPLATE_STYLE = _RoleStyle(
    fill="#e8f0ff", border="#1f4e79", font="#1f4e79", shape="folder",
)
_RAIL_NODE_FILL = "#f5f5f5"
_RAIL_NODE_BORDER = "#666666"
# CF.3.f — TransferTemplate composite: HTML-table label with a header
# row + one leg-rail row per leg, each with a `PORT="leg_<rail>"` for
# east/west edge docking. Header fill is a brighter orange than legs
# so the title reads as the "head" of the shape.
_TEMPLATE_HEADER_FILL = "#ffcc99"
_TEMPLATE_LEG_FILL = "#fff2e0"
_TEMPLATE_XOR_FILL = "#ffe1c2"  # slightly tinted leg fill for XOR-group rows
_TRANSFER_TEMPLATE_FILL = "#fce4d6"  # kept for backwards-compat (used by typed-tree code paths)
_TRANSFER_TEMPLATE_BORDER = "#a6622c"
# CF.3.f.b WCAG palette (darker variants of the Dark2 hue family —
# operator-locked to be visually distinct + accessibility-conscious
# instead of the prior Dark2 spec-fill colors). Each rail-edge color
# now hits ≥3.5:1 contrast against the template-header fill
# (#ffcc99) and ≥5:1 against the lighter leg fill (#fff2e0),
# satisfying WCAG SC 1.4.11 (3:1 non-text). Hue families preserved
# (warm/cool/neutral/magenta) for operator recognition continuity.
_RAIL_EDGE_DEBIT_COLOR = "#b34a02"     # warm — money OUT of the role (was #d95f02)
_RAIL_EDGE_CREDIT_COLOR = "#0f5f47"    # cool — money IN to the role (was #1b9e77)
_RAIL_EDGE_VARIABLE_COLOR = "#4a4682"  # neutral — direction set per-fire (was #7570b3)
_CHAIN_EDGE_COLOR = "#7a0033"          # distinct magenta-purple (was #9e0142 — darkened for parity)
# CF.3.f.b — bumped rail-edge stroke width to 1.5 (was 1.0 default).
# Combined with the darkened palette this satisfies the WCAG
# graphical-objects threshold and makes the direction-color signal
# read at dense layouts where 1pt strokes were getting lost in the
# template orange.
_RAIL_EDGE_PENWIDTH = "1.5"
_BUNDLE_EDGE_COLOR = "#1f4e79"         # kept for top-level bundle edges
_SELF_LOOP_COLOR = "#7f6000"
_CONTROL_PARENT_COLOR = "#888888"
# AB.3.8 — XOR group sub-cluster styling. Distinct hue + tighter fill
# so the analyst can read "these rails are mutually exclusive per
# firing" without confusing them with the surrounding template's
# orange/brown chrome.
_XOR_GROUP_FILL = "#f0f4ff"
_XOR_GROUP_BORDER = "#5a6f9c"


@dataclass(frozen=True, slots=True)
class _BundledEdge:
    """Aggregate of one or more two-leg rails sharing a (src, dst) pair."""

    source: Identifier
    destination: Identifier
    rail_names: tuple[Identifier, ...]


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


def build_role_carriers(
    instance: L2Instance,
) -> dict[str, list[dict[str, str]]]:
    """Map every role node id → its (Account + AccountTemplate) carriers.

    A "carrier" is any Account or AccountTemplate that *references* the
    role via its ``role`` field. The L2 schema allows multiple carriers
    per role (e.g. ``spec_example.yaml`` declares two ``CustomerSubledger``
    Accounts AND one ``CustomerSubledger`` AccountTemplate); the diagram
    right-click menu uses this map to surface every concrete entity the
    operator might want to edit.

    Keyed by ``"role__<role>"`` (the node id graphviz emits, matching
    ``_role_id``) so the JS sidecar reader can look up directly with the
    node's ``data-id`` attr. Roles referenced ONLY by rails (no
    declaring Account / AccountTemplate) still appear as keys with an
    empty list — that's the "orphan" case the menu surfaces as a
    disabled ``No matches`` item.

    Each carrier dict has two keys:
      - ``kind``: ``"account"`` or ``"account_template"`` (matches the
        ``EntityKind`` literal so callers can plug it straight into
        ``/l2_shape/<kind>/<id>/edit`` URLs).
      - ``id``: the carrier's addressing id (``Account.id`` for
        accounts; ``AccountTemplate.role`` for templates — templates
        key on ``role`` per primitives.py's U2 uniqueness lock).

    Carriers within a role are sorted by ``(kind, id)`` so menu order
    stays deterministic across runs (matches the rest of the typed
    projection's stability contract).
    """
    out: dict[str, list[dict[str, str]]] = {
        _role_id(role): [] for role in _collect_roles(instance)
    }
    for account in instance.accounts:
        out.setdefault(_role_id(account.role), []).append(
            {"kind": "account", "id": str(account.id)},
        )
    for template in instance.account_templates:
        out.setdefault(_role_id(template.role), []).append(
            {"kind": "account_template", "id": str(template.role)},
        )
    for carriers in out.values():
        carriers.sort(key=lambda c: (c["kind"], c["id"]))
    return out


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
        list[Identifier],
    ] = {}
    for rail in rails:
        if not isinstance(rail, TwoLegRail):
            continue
        for source in rail.source_role:
            for destination in rail.destination_role:
                pairs.setdefault(
                    (source, destination), [],
                ).append(rail.name)
    bundled: list[_BundledEdge] = []
    for (source, destination), names in sorted(pairs.items()):
        bundled.append(
            _BundledEdge(
                source=source,
                destination=destination,
                rail_names=tuple(sorted(names)),
            )
        )
    return tuple(bundled)


def _bundle_label(bundle: _BundledEdge) -> str:
    """Pretty label for a bundled edge — count + rail names.

    When only one rail backs the edge, drop the count prefix. Multi-rail
    bundles get the count up front so visual scan picks out the
    high-traffic edges.
    """
    rail_count = len(bundle.rail_names)
    if rail_count == 1:
        return str(bundle.rail_names[0])
    rail_str = ", ".join(bundle.rail_names)
    return f"{rail_count} rails: {rail_str}"


def _self_loop_label(rail: SingleLegRail) -> str:
    """Pretty label for a single-leg rail self-loop."""
    return f"{rail.name}\n({rail.leg_direction})"


def _chain_label(
    chain: Chain,
    *,
    cardinality: Literal["required", "xor"],
    child_index: int | None = None,
) -> str:
    """Pretty label for a chain edge — required (singleton) / xor (multi).

    CF.3.f.b — XOR chain edges used to label themselves with the full
    comma-joined sibling list ON EVERY EDGE. Operator measured on a real
    upstream graph: a 4-sibling XOR group repeated 663 chars across 7
    edges — a ~750px label box dot had to make room for, the dominant
    width driver after composite landing. New shape: each XOR edge gets
    a short `(xor i of N)` position label; the full sibling list is
    discoverable via the model / tooltip / future group header. Width
    drops by −26 % (TB), info preserved at the L2-yaml + sidecar level.

    `child_index` (0-based) lets the caller place the edge within the
    XOR group; when None, fall back to the bare `(xor)` form.
    """
    if cardinality == "required":
        return "chain\n(required)"
    if child_index is None:
        return "chain\n(xor)"
    return f"chain\n(xor {child_index + 1} of {len(chain.children)})"


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


# ---------------------------------------------------------------------------
# CF.3.f helpers — port docking + composite template label
# ---------------------------------------------------------------------------


def _leg_port(rail_name: Identifier) -> str:
    """CF.3.f port id for a template leg-rail cell.

    Graphviz port identifiers must be alphanumeric + underscore. We
    sanitize the rail name and prefix with ``leg_`` so chain/rail edges
    can dock at the exact leg via ``tmpl__<template>:leg_<rail>``.
    """
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in str(rail_name))
    return f"leg_{safe}"


def _rail_node_attrs(rail: Rail) -> dict[str, str]:
    """CF.3.f shape attributes for STANDALONE rail nodes (not in a template).

    Family signal:
      - cylinder peripheries=2 → aggregating rail (sweep semantics; the
        ``aggregating`` flag is set on either TwoLegRail or SingleLegRail
        per SPEC — checked first since it dominates the shape choice)
      - cds peripheries=2      → TwoLegRail (double-stroke; "two legs converge")
      - cds                    → SingleLegRail (single-stroke chevron)

    Bundles reuse the underlying shape with a ``×N`` label suffix; the
    stroke count stays the same since "bundled" is orthogonal to
    "two-leg vs single-leg". Caller picks shape per rail; bundle code
    picks shape per the canonical first rail in the bundle.
    """
    if getattr(rail, "aggregating", False):
        return {"shape": "cylinder", "peripheries": "2"}
    if isinstance(rail, TwoLegRail):
        return {"shape": "cds", "peripheries": "2"}
    return {"shape": "cds"}


def _rail_leg_marker(rail: Rail) -> str:
    """Short direction-marker shown inside a template's leg-rail cell.

    SingleLegRails carry a leg_direction (Debit/Credit/Variable) → D/C/V.
    TwoLegRails span source→destination; the leg-row uses "→" to
    indicate one-directional flow source→destination (vs the previous
    "↔" which read as bidirectional and was a CF.3.f.b review finding).
    Aggregating TwoLegRails get "⇉" (paired-arrow = sweep semantics)
    so they're visually distinct from a non-aggregating leg row.
    """
    if isinstance(rail, SingleLegRail):
        return {"Debit": "D", "Credit": "C", "Variable": "V"}.get(
            str(rail.leg_direction), "?",
        )
    if getattr(rail, "aggregating", False):
        return "⇉"
    return "→"


def _template_composite_label(
    template: TransferTemplate,
    rails_by_name: Mapping[Identifier, Rail],
    rail_to_xor_group: Mapping[Identifier, int],
    *,
    leg_in_focus: Any = None,
) -> str:
    """CF.3.f composite template label (HTML-<table>).

    Top row = template name + (transfer_key small-font, if set).
    Each subsequent row = one leg-rail, with PORT="leg_<rail>" for
    east/west chain/rail edge docking. XOR-group rows get a tinted
    background fill so the operator sees the grouping inside the
    shape (paired with matched edge style on the chain edges into
    those leg ports).
    """
    import html as _html

    name_html = _html.escape(str(template.name))
    # transfer_key is tuple[Identifier, ...]; `str()` would call
    # tuple.__repr__ and render `('key1', 'key2')` literally in the
    # template header. Join with ", " so the operator sees plain text.
    transfer_key = getattr(template, "transfer_key", None) or ()
    if transfer_key:
        key_text = ", ".join(_html.escape(str(k)) for k in transfer_key)
        header_inner = (
            f"<B>{name_html}</B>"
            f'<BR/><FONT POINT-SIZE="8">{key_text}</FONT>'
        )
    else:
        header_inner = f"<B>{name_html}</B>"

    lines = [
        '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" '
        'CELLPADDING="6">',
        f'  <TR><TD BGCOLOR="{_TEMPLATE_HEADER_FILL}" PORT="hdr">'
        f'{header_inner}</TD></TR>',
    ]
    for rail_name in template.leg_rails:
        if rail_name not in rails_by_name:
            continue
        # CF.3.f focus-gate: when a focus filter is active, skip
        # out-of-focus leg rows so the composite matches pre-CF.3.f
        # per-rail focus behavior. None = no filter, emit all.
        if leg_in_focus is not None and not leg_in_focus(rail_name):
            continue
        rail = rails_by_name[rail_name]
        rail_html = _html.escape(str(rail.name))
        marker = _rail_leg_marker(rail)
        bg = (
            _TEMPLATE_XOR_FILL
            if rail_name in rail_to_xor_group
            else _TEMPLATE_LEG_FILL
        )
        port_id = _leg_port(rail_name)
        lines.append(
            f'  <TR><TD BGCOLOR="{bg}" PORT="{port_id}" '
            f'ALIGN="LEFT">{rail_html}  ({marker})</TD></TR>',
        )
    lines.append("</TABLE>>")
    return "\n".join(lines)


def _rail_edge_color_for_direction(direction: str | None) -> str:
    """CF.3.f rail-edge color by direction (Debit warm / Credit cool / Var neutral)."""
    if direction == "Debit":
        return _RAIL_EDGE_DEBIT_COLOR
    if direction == "Credit":
        return _RAIL_EDGE_CREDIT_COLOR
    return _RAIL_EDGE_VARIABLE_COLOR


def topology_graph_for(
    instance: L2Instance, *, db_table_prefix: str,
) -> TopologyGraph:
    """Walk an L2Instance and return its typed topology projection.

    Pure construction — no graphviz import, no rendering, no I/O. Both
    spike arms consume this single projection so the topology walk
    isn't duplicated between renderers.

    Iteration order matches the legacy ``build_topology_graph`` walk
    (roles sorted; templates in declaration order; chains in
    declaration order) so the graphviz renderer that consumes it
    produces the same DOT shape it always did.

    Z.C — ``db_table_prefix`` is the cfg.db_table_prefix (formerly read
    off the dropped ``L2Instance.instance`` field) and surfaces as
    ``TopologyGraph.instance_name`` so the rendered diagram still
    carries the operator-facing prefix label.
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
            chain_referenced.add(child.name)
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
                    "direction": rail.leg_direction,
                },
            ))

    # 4c. Template-member edges (template → each leg rail).
    # AB.3.8: when a leg_rail is part of an XOR group, the edge's
    # metadata carries ``xor_group_index`` (str-of-int, 0-based) so
    # the renderer can sub-cluster grouped rails inside the template
    # cluster. Non-grouped leg_rails get no ``xor_group_index`` key —
    # the absence IS the "not grouped" signal (mirrors the chain
    # edge's ``cardinality`` metadata pattern).
    for template in instance.transfer_templates:
        rail_to_group: dict[Identifier, int] = {}
        for gi, group in enumerate(template.leg_rail_xor_groups):
            for member in group:
                rail_to_group[member] = gi
        for rail_name in template.leg_rails:
            edge_metadata: dict[str, str] = {}
            if rail_name in rail_to_group:
                edge_metadata["xor_group_index"] = str(
                    rail_to_group[rail_name],
                )
            edges.append(TopologyEdge(
                source=_template_id(template.name),
                target=_rail_id(rail_name),
                kind="template_member",
                label="",
                metadata=edge_metadata,
            ))

    # 4c.5 Control-parent edges (Account.parent_role + AccountTemplate.parent_role).
    # Structural hierarchy (subledger → control), not flow connectivity. Maps
    # cleanly to the user's "Layer 1" (chart-of-accounts) mental model — these
    # are the GL roll-up relationships the institution's reconciliation rests on.
    parents_with_limits: set[Identifier] = {
        ls.parent_role for ls in instance.limit_schedules
    }
    for account in instance.accounts:
        if account.parent_role is None:
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
        siblings_str = ",".join(str(c.name) for c in chain.children)
        for child_index, child_spec in enumerate(chain.children):
            child_name = child_spec.name
            child_id = (
                _template_id(child_name)
                if child_name in template_names
                else _rail_id(child_name)
            )
            chain_metadata: dict[str, str] = {"cardinality": cardinality}
            if cardinality == "xor":
                chain_metadata["xor_siblings"] = siblings_str
            # AB.6 (per-child) — fan_in edges tag metadata with
            # ``fan_in=true`` (and the child's ``expected_parent_count``
            # when set) so renderers can apply distinct styling on the
            # specific child edge. Mirrors AB.3.8's ``xor_group_index``
            # metadata convention; AB.4.9 tagged at chain level — AB.6
            # narrows to the specific fan-in child edge.
            if child_spec.fan_in:
                chain_metadata["fan_in"] = "true"
                if child_spec.expected_parent_count is not None:
                    chain_metadata["expected_parent_count"] = str(
                        child_spec.expected_parent_count,
                    )
            edges.append(TopologyEdge(
                source=parent_id,
                target=child_id,
                kind="chain",
                label=_chain_label(
                    chain, cardinality=cardinality,
                    child_index=child_index if cardinality == "xor" else None,
                ),
                metadata=chain_metadata,
            ))

    return TopologyGraph(
        instance_name=db_table_prefix,  # Z.C — was str(instance.instance)
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


# CF.3.d — categorical show-set. Each entry gates one Phase of the
# emit. The chrome's 4 server-side "Show:" checkboxes round-trip
# through the URL `?show=...` param; the JS-CSS scope split
# (internal/external roles) stays separate. Unknown values are
# silently dropped — defensive against malformed URLs.
_VALID_SHOW_CATEGORIES = frozenset(
    {"role", "rail", "template", "chain", "control_parent"},
)


def _categories_for_layer(layer: int) -> frozenset[str]:
    """Compat shim: derive show-set from the legacy ``layer`` param.

    - layer 1: roles + control hierarchy only
    - layer 2: adds rails
    - layer 3: adds chains + templates
    """
    cats: set[str] = {"role", "control_parent"}
    if layer >= 2:
        cats.add("rail")
    if layer >= 3:
        cats.add("template")
        cats.add("chain")
    return frozenset(cats)


def build_topology_graph_per_rail(
    instance: L2Instance,
    *,
    db_table_prefix: str,
    bundle_parallel_rails: bool = True,
    focus_node_id: str | None = None,
    layer: int = 1,
    hide_singleleg: bool = False,
    show: frozenset[str] | None = None,
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
        name=f"l2_topology_per_rail_{db_table_prefix}",  # Z.C — was instance.instance
        comment=(
            f"L2 topology (rails as nodes) for instance "
            f"'{db_table_prefix}'"
        ),
    )
    # CF.3.c — edge-kind sidecar. As edges are emitted, record their
    # typed kind in `edge_meta` so the JS shim can classify by exact
    # known kind instead of the `_edgeKind` heuristic at diagram.js:
    # the heuristic misclassified role↔role control_parent edges as
    # rail_bundle (no disambiguation in title) and conflated some
    # corner cases. Key = the exact `"<src>-><dst>"` form graphviz
    # emits (including any port suffix); value = "chain" /
    # "rail_bundle" / "control_parent" / "self_loop". Read by the
    # route's sidecar serializer + attached to the digraph via
    # `setattr` (graphviz.Digraph allows ad-hoc attrs).
    edge_meta: dict[str, str] = {}

    def _record_edge(src: str, dst: str, kind: str) -> None:
        edge_meta[f"{src}->{dst}"] = kind
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
    # CF.3.f.b — Layer 3 swaps to rankdir=TB. The CF.3.f composite shape
    # (templates as port-docked HTML tables) chains template-A's
    # credit-east to template-B's debit-west, which under LR pushes each
    # chained template rightward and balloons width. Operator measurement
    # on a real upstream graph (115/182 → composite L3): LR 6125×1371pt
    # (~4.5:1 ribbon), TB 3711pt wide (~1.3:1), −30 % crossings.
    # L1 / L2 keep LR (sparse roles + control_parent / rails + endpoints
    # read naturally left-to-right; the width pain is L3-specific).
    rank_direction = "TB" if layer >= 3 else "LR"
    g.attr(
        rankdir=rank_direction,
        splines="polyline",
        overlap="false",
        nodesep="0.15",
        ranksep="0.35",
        # CF.3.a — bumped 2.0→10.0 (v13.1.1 audit "free add-on"). More
        # mincross iterations for ~no marginal layout cost on our graph
        # sizes; pairs with the template_member constraint=false change
        # below to land the audit's 65% crossings reduction at L3.
        mclimit="10.0",
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
            if child.name in rail_names_set:
                anchored_rails.add(child.name)
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
        if account.parent_role is not None:
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
            for child_spec in chain.children:
                child_name = child_spec.name
                child_id = (
                    _template_id(child_name)
                    if child_name in template_names_set
                    else _rail_id(child_name)
                )
                _add_adj(parent_id, child_id)

        # Control-parent edges (subledger ↔ control role).
        for account in instance.accounts:
            if account.parent_role is not None:
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

    # CF.3.d — categorical show-set. When the caller passes `show=`
    # use it directly (each entry gates one emit Phase). When None,
    # derive from the legacy ``layer`` param (1/2/3) — backwards
    # compatible: Phase B and Phase F gates retain their old
    # bool-from-layer behavior.
    active_categories: frozenset[str] = (
        show if show is not None else _categories_for_layer(layer)
    )
    # Defensive: drop unknown entries the route might pass through.
    active_categories = active_categories & _VALID_SHOW_CATEGORIES
    show_roles = "role" in active_categories
    show_rails = "rail" in active_categories
    show_chains = "chain" in active_categories
    show_templates = "template" in active_categories
    show_control_parent = "control_parent" in active_categories
    # CF.3.d — templates and chains used to share a single gate
    # (``show_chains_and_templates``). The chrome's "Show:" toggles
    # now let the operator hide chains OR templates independently —
    # Phase B's template iter consults ``show_templates`` and Phase F
    # gates chain edges on ``show_chains``.

    # Phase A — Role nodes (top-level, referenced only, in focus).
    # CF.3.d — gated by ``show_roles``; when off, dot still emits any
    # edges that reference role IDs as anonymous nodes — operator
    # opt-in to dangling-edge behavior is documented in the spec.
    typed = topology_graph_for(instance, db_table_prefix=db_table_prefix)
    for n in typed.nodes:
        if not show_roles:
            break
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
        # CF.3.f — standalone rails get a cds-family shape (chevron with
        # direction baked into the silhouette); single-stroke for
        # SingleLegRail, double-stroke (peripheries=2) for TwoLegRail,
        # cylinder + peripheries=2 for AggregatingRail. Bundles reuse
        # the underlying shape; the "×N" lives in the bundle label.
        attrs = _rail_node_attrs(rail)
        g_or_sub.node(
            _rail_id(rail.name),
            label=str(rail.name),
            fillcolor=_RAIL_NODE_FILL,
            color=_RAIL_NODE_BORDER,
            fontcolor=_RAIL_NODE_BORDER,
            style="filled",
            **attrs,
        )

    # CF.3.f — Templates as composite HTML-table nodes.
    # Each template renders as ONE node: a `plaintext`-shaped graphviz
    # node whose label is an HTML table with a top header row (template
    # name) and one row per leg-rail (PORT="leg_<rail>"). Chain edges
    # and rail edges dock at the exact leg ports
    # (`tmpl__<template>:leg_<rail>:e` / `:w`). The dashed cluster
    # boundary, the inner `tmpl__<name>` component node, and the dotted
    # template_member edges from the pre-CF.3.f emit are all gone —
    # the composite shape IS the template (operator lock 2026-06-04:
    # "whole over parts; click for details").
    rail_to_template: dict[Identifier, Identifier] = {}
    for _template in instance.transfer_templates:
        for _rail_name in _template.leg_rails:
            rail_to_template[_rail_name] = _template.name

    rails_in_clusters: set[Identifier] = set()
    template_iter = (
        instance.transfer_templates if show_templates else ()
    )
    for template in template_iter:
        tmpl_id = _template_id(template.name)
        in_template_legs = [
            rn for rn in template.leg_rails
            if rn in rail_names_set and _in_focus(_rail_id(rn))
        ]
        if not _in_focus(tmpl_id) and not in_template_legs:
            continue
        # Mark all of this template's rails as "owned by template" so
        # Phase C / D / E skip them as standalone-rail emits — they're
        # port rows in the composite shape now, not separate nodes.
        rail_to_group: dict[Identifier, int] = {}
        for gi, group in enumerate(template.leg_rail_xor_groups):
            for member in group:
                rail_to_group[member] = gi
        # When focus is active, render only the in-focus legs in the
        # composite + only mark THOSE as rails_in_clusters (the others
        # fall back to standalone-rail rendering at Phase C/D/E).
        def _leg_in_focus(rn: Identifier) -> bool:
            return focus_set is None or _rail_id(rn) in focus_set
        html_label = _template_composite_label(
            template, rails_by_name, rail_to_group,
            leg_in_focus=_leg_in_focus,
        )
        g.node(
            tmpl_id,
            label=html_label,
            shape="plaintext",
        )
        for rail_name in template.leg_rails:
            if rail_name not in rail_names_set:
                continue
            if not _leg_in_focus(rail_name):
                continue
            rails_in_clusters.add(rail_name)

    # Phase C — Top-level individual rails (not bundled, not in a cluster).
    # Layer-gated: rail nodes only show at L2+.
    # CF.3.h — when ``hide_singleleg`` is set, skip STANDALONE
    # SingleLegRails (single-leg rails that are NOT a leg of a template).
    # Template-resident single-leg rails stay as cells in the composite
    # shape — hiding them would break the template's port system.
    for rail in instance.rails:
        if not show_rails:
            break
        if rail.name in rail_to_bundle:
            continue
        if rail.name in rails_in_clusters:
            continue
        if hide_singleleg and isinstance(rail, SingleLegRail):
            continue
        if not _in_focus(_rail_id(rail.name)):
            continue
        _emit_rail_node(g, rail)

    # Phase D — Bundle nodes (top-level). Layer-gated.
    # CF.3.f — bundles render with the same cds-family shape as their
    # underlying rails (TwoLeg = cds + peripheries=2). The `×N` lives
    # in the bundle_label, so stroke count is the type signal and the
    # label is the cardinality signal.
    for bundle_id, bundle_label, key in bundles:
        if not show_rails:
            break
        if not _in_focus(bundle_id):
            continue
        # CF.3.h — single-leg bundles disappear too when hide_singleleg.
        if hide_singleleg and key[0] != "twoleg":
            continue
        bundle_shape = "cds"
        bundle_peripheries = "2" if key[0] == "twoleg" else "1"
        g.node(
            bundle_id,
            label=bundle_label,
            shape=bundle_shape,
            peripheries=bundle_peripheries,
            fillcolor="#e8e8e8",
            color=_RAIL_NODE_BORDER,
            fontcolor=_RAIL_NODE_BORDER,
            style="filled",
        )

    # Phase E — Endpoint edges. Individual rails: src→rail→dst as 2 edges.
    # Bundles: same shape but consolidated onto the bundle node.
    # CF.3.f — template-resident rails dock at the template's leg port
    # (`tmpl__X:leg_<rail>:w` for incoming, `:e` for outgoing) instead
    # of the standalone rail_node_id. Direction-encoded edge colors:
    # orange = money OUT (Debit/source-leg), teal = money IN
    # (Credit/dest-leg), purple = Variable. Drops the rail-edge labels
    # (operator: arrow + color carry direction; click for details).
    for rail in instance.rails:
        if not show_rails:
            break
        if rail.name in rail_to_bundle:
            continue
        # CF.3.h — skip edges for hidden standalone single-leg rails.
        # Template-resident rails (rails_in_clusters) keep their edges
        # since their port row is still rendered inside the composite.
        if (
            hide_singleleg
            and isinstance(rail, SingleLegRail)
            and rail.name not in rails_in_clusters
        ):
            continue
        rail_node_id = _rail_id(rail.name)
        if not _in_focus(rail_node_id):
            continue
        # Resolve the edge endpoints — template-resident rails route
        # through the composite shape's leg port.
        if rail.name in rails_in_clusters:
            tmpl_name = rail_to_template[rail.name]
            # CF.3.f.b — no `:w`/`:e` compass pin. Under rankdir=TB the
            # west/east pinning is perpendicular to the flow direction
            # (vertical) and was forcing horizontal sprawl. Letting dot
            # pick the port docking side wins ~−2% width and −19 crossings
            # vs the pinned variant (operator measured). Same port id,
            # graphviz picks the side per layout.
            port_node = f"{_template_id(tmpl_name)}:{_leg_port(rail.name)}"
            port_west = port_node
            port_east = port_node
        else:
            port_west = rail_node_id
            port_east = rail_node_id
        if isinstance(rail, TwoLegRail):
            for src_role in rail.source_role:
                if not _in_focus(_role_id(src_role)):
                    continue
                g.edge(
                    _role_id(src_role), port_west,
                    color=_RAIL_EDGE_DEBIT_COLOR,
                    arrowhead="normal",
                    penwidth=_RAIL_EDGE_PENWIDTH,
                )
                _record_edge(_role_id(src_role), port_west, "rail_bundle")
            for dst_role in rail.destination_role:
                if not _in_focus(_role_id(dst_role)):
                    continue
                g.edge(
                    port_east, _role_id(dst_role),
                    color=_RAIL_EDGE_CREDIT_COLOR,
                    arrowhead="normal",
                    penwidth=_RAIL_EDGE_PENWIDTH,
                )
                _record_edge(port_east, _role_id(dst_role), "rail_bundle")
        else:
            color = _rail_edge_color_for_direction(str(rail.leg_direction))
            # CF.3.f.b review: Variable direction means direction is
            # set per-fire (unknown at design time). Double-arrow makes
            # the silhouette match the purple color's semantic; pre-fix
            # the arrowhead was "normal" (Debit-shape) regardless and
            # the visual lied about Credit + Variable.
            variable_arrow = "normalonormal"
            for leg_role in rail.leg_role:
                if not _in_focus(_role_id(leg_role)):
                    continue
                if rail.leg_direction == "Credit":
                    g.edge(
                        port_east, _role_id(leg_role),
                        color=color,
                        arrowhead="normal",
                        penwidth=_RAIL_EDGE_PENWIDTH,
                    )
                    _record_edge(port_east, _role_id(leg_role), "rail_bundle")
                elif rail.leg_direction == "Variable":
                    g.edge(
                        port_east, _role_id(leg_role),
                        color=color,
                        arrowhead=variable_arrow,
                        dir="both",
                        penwidth=_RAIL_EDGE_PENWIDTH,
                    )
                    _record_edge(port_east, _role_id(leg_role), "rail_bundle")
                else:
                    g.edge(
                        _role_id(leg_role), port_west,
                        color=color,
                        arrowhead="normal",
                        penwidth=_RAIL_EDGE_PENWIDTH,
                    )
                    _record_edge(_role_id(leg_role), port_west, "rail_bundle")

    # CF.3.f — bundle edges use the same direction color palette as
    # standalone rail edges (orange=Debit/source-leg, teal=Credit/
    # dest-leg, purple=Variable). penwidth still scales with bundle
    # size so high-traffic edges visually pop.
    for bundle_id, _label, key in bundles:
        if not show_rails:
            break
        if not _in_focus(bundle_id):
            continue
        # CF.3.h — single-leg bundle edges go too.
        if hide_singleleg and key[0] != "twoleg":
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
                    color=_RAIL_EDGE_DEBIT_COLOR,
                    arrowhead="normal",
                    penwidth=penwidth,
                )
                _record_edge(_role_id(src_role), bundle_id, "rail_bundle")
            for dst_role in dst_tuple:
                if not _in_focus(_role_id(dst_role)):
                    continue
                g.edge(
                    bundle_id, _role_id(dst_role),
                    color=_RAIL_EDGE_CREDIT_COLOR,
                    arrowhead="normal",
                    penwidth=penwidth,
                )
                _record_edge(bundle_id, _role_id(dst_role), "rail_bundle")
        else:
            leg_tuple = key[1]
            direction = str(key[2])
            color = _rail_edge_color_for_direction(direction)
            for leg_role in leg_tuple:
                if not _in_focus(_role_id(leg_role)):
                    continue
                if direction == "Credit":
                    g.edge(
                        bundle_id, _role_id(leg_role),
                        color=color,
                        arrowhead="normal",
                        penwidth="1.5",
                    )
                    _record_edge(bundle_id, _role_id(leg_role), "rail_bundle")
                elif direction == "Variable":
                    # CF.3.f.b — bundle Variable bundle parity with the
                    # standalone branch above (double-arrow + dir=both
                    # so the silhouette matches the purple semantic).
                    g.edge(
                        bundle_id, _role_id(leg_role),
                        color=color,
                        arrowhead="normalonormal",
                        dir="both",
                        penwidth="1.5",
                    )
                    _record_edge(bundle_id, _role_id(leg_role), "rail_bundle")
                else:
                    g.edge(
                        _role_id(leg_role), bundle_id,
                        color=color,
                        arrowhead="normal",
                        penwidth="1.5",
                    )
                    _record_edge(_role_id(leg_role), bundle_id, "rail_bundle")

    # Phase F — Chain edges (rail → rail or template → template).
    # CF.3.f — when a chain endpoint is a TEMPLATE-RESIDENT rail (i.e.
    # a leg of a TransferTemplate), the edge docks at the template's
    # leg port (`tmpl__X:leg_<rail>:e` for source, `:w` for target)
    # so the connection visually lands on the exact leg row of the
    # composite shape. Standalone rails + whole-template chain
    # endpoints route through their plain node IDs as before.
    def _chain_endpoint(name: Identifier, *, side: str) -> tuple[str, str]:
        """Return (graphviz_target, focus_check_id) for a chain endpoint.

        CF.3.f.b: `side` is preserved as a kwarg for API stability but no
        longer used (compass-pin dropped — see the port-docking comment
        in Phase E for why). focus_check_id is the un-ported node id so
        the focus filter can match per-node, not per-port.
        """
        del side  # CF.3.f.b — compass pin dropped; param kept for API stability
        if name in template_names_set:
            tid = _template_id(name)
            return tid, tid
        if name in rail_to_template:
            owning = rail_to_template[name]
            tmpl_id_str = _template_id(owning)
            return (
                f"{tmpl_id_str}:{_leg_port(name)}",
                _rail_id(name),
            )
        return _rail_id(name), _rail_id(name)

    def _is_hidden_singleleg(name: Identifier) -> bool:
        """CF.3.h — chain edges to a hidden single-leg rail dangle.

        Skip the edge when its endpoint is a standalone single-leg
        rail and ``hide_singleleg`` is set. Template-resident rails
        stay reachable via the template's leg port even when their
        underlying primitive is single-leg, so this only fires on
        the standalone case.
        """
        if not hide_singleleg:
            return False
        rail = rails_by_name.get(name)
        if rail is None or not isinstance(rail, SingleLegRail):
            return False
        return name not in rails_in_clusters

    for chain in instance.chains:
        if not show_chains:
            break
        if _is_hidden_singleleg(chain.parent):
            continue
        parent_id, parent_focus_id = _chain_endpoint(chain.parent, side="e")
        cardinality: Literal["required", "xor"] = (
            "required" if len(chain.children) == 1 else "xor"
        )
        for child_index, child_spec in enumerate(chain.children):
            child_name = child_spec.name
            if _is_hidden_singleleg(child_name):
                continue
            child_id, child_focus_id = _chain_endpoint(child_name, side="w")
            if not (_in_focus(parent_focus_id) and _in_focus(child_focus_id)):
                continue
            # AB.6 (per-child) — fan-in edges render with a distinct
            # visual hint: "bold" pen weight + a label annotation
            # "fan-in N→1" so the diagram reader sees the N:1 shape
            # without reading the yaml. Non-fan-in edges (including
            # siblings of a fan-in entry under mixed-cardinality)
            # stay unchanged.
            if child_spec.fan_in:
                fan_in_suffix = (
                    f" [fan-in {child_spec.expected_parent_count}→1]"
                    if child_spec.expected_parent_count is not None
                    else " [fan-in N→1]"
                )
                label = _chain_label(
                    chain, cardinality=cardinality,
                    child_index=child_index if cardinality == "xor" else None,
                ) + fan_in_suffix
                g.edge(
                    parent_id, child_id,
                    label=label,
                    color=_CHAIN_EDGE_COLOR,
                    style="dashed",
                    penwidth="2.0",
                    arrowhead="onormalonormal",
                    fontcolor=_CHAIN_EDGE_COLOR,
                )
                _record_edge(parent_id, child_id, "chain")
            else:
                g.edge(
                    parent_id, child_id,
                    label=_chain_label(
                        chain, cardinality=cardinality,
                        child_index=child_index if cardinality == "xor" else None,
                    ),
                    color=_CHAIN_EDGE_COLOR,
                    style="dashed",
                    fontcolor=_CHAIN_EDGE_COLOR,
                )
                _record_edge(parent_id, child_id, "chain")

    # Phase G — Control-parent edges (subledger → control role).
    # CF.3.d — gated by ``show_control_parent``; when off, the two
    # iterators below short-circuit via the loop guards.
    parents_with_limits: set[Identifier] = {
        ls.parent_role for ls in instance.limit_schedules
    }
    for account in instance.accounts:
        if not show_control_parent:
            break
        if account.parent_role is None:
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
        _record_edge(src, dst, "control_parent")
    for tmpl_acc in instance.account_templates:
        if not show_control_parent:
            break
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
        _record_edge(src, dst, "control_parent")

    # CF.3.c — stash the edge-kind sidecar on the digraph so the route
    # serializer can pick it up. Used by diagram.js's `_edgeKind` to
    # classify edges from server-truth instead of the title-heuristic.
    setattr(g, "edge_meta", edge_meta)
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
      ``"<parent_role>::<rail>"`` composite for
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
        for child_spec in chain.children:
            child_name = child_spec.name
            child_id = (
                _template_id(child_name)
                if child_name in template_names_set
                else _rail_id(child_name)
            )
            _add(parent_id, child_id)

    # Control-parent edges (role ↔ role).
    for account in instance.accounts:
        if account.parent_role is not None:
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
        if str(a.role) in visible_roles
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
        f"{c.parent}::{','.join(sorted(str(ch.name) for ch in c.children))}"
        for c in instance.chains
        if str(c.parent) in rail_or_tmpl
        or any(str(ch.name) in rail_or_tmpl for ch in c.children)
    )
    limit_schedules = frozenset(
        f"{ls.parent_role}::{ls.rail}"
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
            f"{c.parent}::{','.join(sorted(str(ch.name) for ch in c.children))}"
            for c in instance.chains
        ),
        "limit_schedule": frozenset(
            f"{ls.parent_role}::{ls.rail}"
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

