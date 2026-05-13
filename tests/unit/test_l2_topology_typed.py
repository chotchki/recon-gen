"""Typed topology projection tests.

Covers ``topology_graph_for`` — builds a ``TopologyGraph`` with the
right node / edge shape against handwritten + shipped fixtures
(spec_example, sasquatch_pr).

Designed to NOT depend on the system ``dot`` binary or the Python
``graphviz`` package — the typed projection is pure walk / pure data,
so it runs everywhere CI does.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from quicksight_gen.common.l2 import (
    Account,
    AccountTemplate,
    ChainEntry,
    Identifier,
    L2Instance,
    Name,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
    load_instance,
)
from quicksight_gen.common.l2.topology import (
    TopologyEdge,
    TopologyGraph,
    TopologyNode,
    topology_graph_for,
)


FIXTURES = Path(__file__).parent.parent / "l2"


def _make_two_leg(
    name: str,
    src: str,
    dst: str,
    *,
    transfer_type: str = "ach",
) -> TwoLegRail:
    return TwoLegRail(
        name=Identifier(name),
        transfer_type=transfer_type,
        metadata_keys=(),
        source_role=(Identifier(src),),
        destination_role=(Identifier(dst),),
        origin="InternalInitiated",
        expected_net=Decimal("0"),
    )


def _kitchen_instance() -> L2Instance:
    """Topologically rich instance — every primitive kind exercised."""
    return L2Instance(
        instance=Identifier("kitchen"),
        accounts=(
            Account(
                id=Identifier("acc-internal"),
                name=Name("Internal Account"),
                role=Identifier("InternalRole"),
                scope="internal",
            ),
            Account(
                id=Identifier("acc-external"),
                name=Name("External Account"),
                role=Identifier("ExternalRole"),
                scope="external",
            ),
        ),
        account_templates=(
            AccountTemplate(
                role=Identifier("CustomerSubledger"),
                scope="internal",
                parent_role=Identifier("InternalRole"),
            ),
        ),
        rails=(
            _make_two_leg("InboundRail", "ExternalRole", "CustomerSubledger"),
            _make_two_leg(
                "OutboundRail", "CustomerSubledger", "ExternalRole",
                transfer_type="wire",
            ),
            SingleLegRail(
                name=Identifier("FeeCharge"),
                transfer_type="fee",
                metadata_keys=(),
                leg_role=(Identifier("CustomerSubledger"),),
                leg_direction="Debit",
                origin="InternalInitiated",
            ),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("SettlementCycle"),
                transfer_type="settlement",
                expected_net=Decimal("0"),
                transfer_key=(Identifier("merchant_id"),),
                completion="business_day_end",
                leg_rails=(Identifier("FeeCharge"),),
            ),
        ),
        chains=(
            ChainEntry(
                parent=Identifier("InboundRail"),
                child=Identifier("SettlementCycle"),
                required=True,
            ),
        ),
        limit_schedules=(),
    )


# -- Typed projection structure ---------------------------------------------


def test_topology_graph_has_role_nodes_for_every_declared_role() -> None:
    g = topology_graph_for(_kitchen_instance())
    role_ids = {n.id for n in g.nodes if n.kind == "role"}
    assert role_ids == {
        "role__InternalRole",
        "role__ExternalRole",
        "role__CustomerSubledger",
    }


def test_topology_graph_role_carries_scope_and_templated() -> None:
    g = topology_graph_for(_kitchen_instance())
    nodes_by_id = {n.id: n for n in g.nodes}
    # Internal singleton: scope=internal, templated=False
    internal = nodes_by_id["role__InternalRole"]
    assert internal.scope == "internal"
    assert internal.templated is False
    # External singleton: scope=external, templated=False
    external = nodes_by_id["role__ExternalRole"]
    assert external.scope == "external"
    assert external.templated is False
    # Templated role: scope=internal (template's scope), templated=True
    templated = nodes_by_id["role__CustomerSubledger"]
    assert templated.scope == "internal"
    assert templated.templated is True


def test_topology_graph_template_node_has_inner_label_and_metadata() -> None:
    g = topology_graph_for(_kitchen_instance())
    tmpl = next(n for n in g.nodes if n.kind == "template")
    assert tmpl.id == "tmpl__SettlementCycle"
    # Inner label is just the name now — transfer_type/key labels were
    # dropped to keep nodes compact (the key is infrastructure-only).
    assert tmpl.label == "SettlementCycle"
    # Metadata still carries the full source data for tooltips / future
    # editor surfaces; the cluster_label matches the inner label.
    assert tmpl.metadata["transfer_type"] == "settlement"
    assert tmpl.metadata["transfer_key"] == "merchant_id"
    assert tmpl.metadata["cluster_label"] == "SettlementCycle"


def test_topology_graph_rail_nodes_for_template_legs_and_chain_refs() -> None:
    g = topology_graph_for(_kitchen_instance())
    rail_ids = {n.id for n in g.nodes if n.kind == "rail"}
    # FeeCharge is a leg of SettlementCycle template → emitted as rail node.
    assert "rail__FeeCharge" in rail_ids
    # InboundRail is referenced by a chain edge (chain.parent).
    # SettlementCycle is the chain.child but it's a template, not a rail.
    assert "rail__InboundRail" in rail_ids
    # OutboundRail isn't in any template, isn't in any chain → no rail node.
    # (It still produces a bundle edge between roles; the rail itself
    # isn't a node unless a template or chain references it.)
    assert "rail__OutboundRail" not in rail_ids


def test_topology_graph_bundle_edge_collapses_parallel_rails() -> None:
    rails = (
        _make_two_leg("RailA", "X", "Y"),
        _make_two_leg("RailB", "X", "Y", transfer_type="wire"),
    )
    inst = L2Instance(
        instance=Identifier("bun"),
        accounts=(
            Account(id=Identifier("a"), role=Identifier("X"), scope="internal"),
            Account(id=Identifier("b"), role=Identifier("Y"), scope="internal"),
        ),
        account_templates=(),
        rails=rails,
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )
    g = topology_graph_for(inst)
    bundles = [e for e in g.edges if e.kind == "rail_bundle"]
    assert len(bundles) == 1
    bundle = bundles[0]
    assert bundle.source == "role__X"
    assert bundle.target == "role__Y"
    assert bundle.metadata["rail_count"] == "2"
    assert "RailA" in bundle.metadata["rail_names"]
    assert "RailB" in bundle.metadata["rail_names"]
    # Label preserved from the legacy _bundle_label
    assert bundle.label.startswith("2 rails:")


def test_topology_graph_self_loop_for_single_leg_rail() -> None:
    g = topology_graph_for(_kitchen_instance())
    self_loops = [e for e in g.edges if e.kind == "self_loop"]
    assert len(self_loops) == 1
    loop = self_loops[0]
    assert loop.source == "role__CustomerSubledger"
    assert loop.target == "role__CustomerSubledger"
    assert loop.metadata["direction"] == "Debit"
    assert loop.metadata["rail_name"] == "FeeCharge"


def test_topology_graph_template_member_edges() -> None:
    g = topology_graph_for(_kitchen_instance())
    members = [e for e in g.edges if e.kind == "template_member"]
    assert len(members) == 1
    member = members[0]
    assert member.source == "tmpl__SettlementCycle"
    assert member.target == "rail__FeeCharge"


def test_topology_graph_emits_control_parent_for_template_parent_role() -> None:
    """A templated role with parent_role gets a control_parent edge."""
    g = topology_graph_for(_kitchen_instance())
    cp_edges = [e for e in g.edges if e.kind == "control_parent"]
    # CustomerSubledger has parent_role=InternalRole in the kitchen fixture.
    assert any(
        e.source == "role__CustomerSubledger" and e.target == "role__InternalRole"
        for e in cp_edges
    )
    cp_for_subledger = next(
        e for e in cp_edges if e.source == "role__CustomerSubledger"
    )
    assert cp_for_subledger.metadata["child_kind"] == "template"
    assert cp_for_subledger.label == "controls"


def test_topology_graph_chain_edge_carries_required_metadata() -> None:
    g = topology_graph_for(_kitchen_instance())
    chains = [e for e in g.edges if e.kind == "chain"]
    assert len(chains) == 1
    chain = chains[0]
    assert chain.source == "rail__InboundRail"
    # Child resolves to a template (SettlementCycle is a template name).
    assert chain.target == "tmpl__SettlementCycle"
    assert chain.metadata["required"] == "true"
    assert "xor_group" not in chain.metadata
    assert "required" in chain.label


def test_topology_graph_chain_edge_carries_xor_group_metadata() -> None:
    inst = L2Instance(
        instance=Identifier("xor"),
        accounts=(
            Account(id=Identifier("a"), role=Identifier("X"), scope="internal"),
            Account(id=Identifier("b"), role=Identifier("Y"), scope="internal"),
            Account(id=Identifier("c"), role=Identifier("Z"), scope="internal"),
        ),
        account_templates=(),
        rails=(
            _make_two_leg("Parent", "X", "Y"),
            _make_two_leg("ChildA", "Y", "Z"),
            _make_two_leg("ChildB", "Y", "Z", transfer_type="wire"),
        ),
        transfer_templates=(),
        chains=(
            ChainEntry(
                parent=Identifier("Parent"),
                child=Identifier("ChildA"),
                required=False,
                xor_group=Identifier("payouts"),
            ),
            ChainEntry(
                parent=Identifier("Parent"),
                child=Identifier("ChildB"),
                required=False,
                xor_group=Identifier("payouts"),
            ),
        ),
        limit_schedules=(),
    )
    g = topology_graph_for(inst)
    chain_edges = [e for e in g.edges if e.kind == "chain"]
    assert len(chain_edges) == 2
    for ce in chain_edges:
        assert ce.metadata["required"] == "false"
        assert ce.metadata["xor_group"] == "payouts"


# -- Typed projection against shipped fixtures ------------------------------


def test_topology_graph_for_spec_example_smoke() -> None:
    inst = load_instance(FIXTURES / "spec_example.yaml")
    g = topology_graph_for(inst)
    # Carries the instance name for page titles + JSON output.
    assert g.instance_name == "spec_example"
    role_labels = {n.label for n in g.nodes if n.kind == "role"}
    assert "ClearingSuspense" in role_labels
    assert "ExternalCounterparty" in role_labels
    assert "CustomerSubledger" in role_labels
    template_ids = {n.id for n in g.nodes if n.kind == "template"}
    assert "tmpl__MerchantSettlementCycle" in template_ids


def test_topology_graph_for_sasquatch_pr_meets_richness_bar() -> None:
    """sasquatch_pr is the spike's legibility test (X.4.b.2/3).

    Not an exhaustive enumeration — just a "this is the meaty graph
    we're tuning the diagram against; it has dozens of nodes / many
    edges / multiple kinds" assertion.
    """
    inst = load_instance(FIXTURES / "sasquatch_pr.yaml")
    g = topology_graph_for(inst)
    assert g.instance_name == "sasquatch_pr"
    # Per-kind cardinality bar — sized to the actual fixture so it
    # catches both "the projection broke" (counts crash) and "the
    # fixture was gutted" (counts shrink).
    role_count = sum(1 for n in g.nodes if n.kind == "role")
    rail_count = sum(1 for n in g.nodes if n.kind == "rail")
    template_count = sum(1 for n in g.nodes if n.kind == "template")
    assert role_count >= 8, f"expected >=8 role nodes; got {role_count}"
    assert template_count >= 2, f"expected >=2 templates; got {template_count}"
    # Rail nodes are emitted only for chain-refs + template legs;
    # sasquatch_pr has both → expect at least a handful.
    assert rail_count >= 5, f"expected >=5 rail nodes; got {rail_count}"
    # All five edge kinds present — the legibility bar is meaningless
    # if the fixture loses one of them. control_parent surfaces the
    # subledger→control hierarchy (X.4.b.3 follow-up: roles whose only
    # appearance is via parent_role were silently dropped pre-fix).
    edge_kinds = {e.kind for e in g.edges}
    assert "rail_bundle" in edge_kinds
    assert "self_loop" in edge_kinds
    assert "template_member" in edge_kinds
    assert "chain" in edge_kinds
    assert "control_parent" in edge_kinds
    # template_role edges were dropped (X.4.b dot pivot, 2026-05-13).
    # Templates point only to rails — the user's mental model + the only
    # connection that survives the per-rail layout cleanly.
    assert "template_role" not in edge_kinds


# -- Frozen-dataclass invariants --------------------------------------------


def test_topology_dataclasses_are_frozen() -> None:
    """TopologyGraph / Node / Edge are frozen value objects — neither
    spike arm should mutate them.
    """
    g = topology_graph_for(_kitchen_instance())
    import dataclasses
    assert dataclasses.is_dataclass(TopologyGraph)
    assert dataclasses.is_dataclass(TopologyNode)
    assert dataclasses.is_dataclass(TopologyEdge)
    # Capture node BEFORE the failed setattr — pyright otherwise narrows
    # ``g.nodes`` to ``tuple[()]`` post-mutation-attempt.
    node = g.nodes[0]
    # Frozen → AttributeError on any setattr.
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        g.nodes = ()  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.label = "x"  # pyright: ignore[reportAttributeAccessIssue]
