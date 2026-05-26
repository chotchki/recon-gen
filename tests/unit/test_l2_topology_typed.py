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

from recon_gen.common.l2 import (
    Account,
    AccountTemplate,
    Chain,
    ChainChildSpec,
    Identifier,
    L2Instance,
    Name,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
    load_instance,
)
from recon_gen.common.l2.topology import (
    TopologyEdge,
    TopologyGraph,
    TopologyNode,
    topology_graph_for,
)
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX


FIXTURES = Path(__file__).parent.parent / "l2"


def _make_two_leg(
    name: str,
    src: str,
    dst: str,
) -> TwoLegRail:
    return TwoLegRail(
        name=Identifier(name),
        metadata_keys=(),
        source_role=(Identifier(src),),
        destination_role=(Identifier(dst),),
        origin="InternalInitiated",
        expected_net=Decimal("0"),
    )


def _kitchen_instance() -> L2Instance:
    """Topologically rich instance — every primitive kind exercised."""
    return L2Instance(
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
            ),
            SingleLegRail(
                name=Identifier("FeeCharge"),
                metadata_keys=(),
                leg_role=(Identifier("CustomerSubledger"),),
                leg_direction="Debit",
                origin="InternalInitiated",
            ),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("SettlementCycle"),
                expected_net=Decimal("0"),
                transfer_key=(Identifier("merchant_id"),),
                completion="business_day_end",
                leg_rails=(Identifier("FeeCharge"),),
            ),
        ),
        chains=(
            Chain(
                parent=Identifier("InboundRail"),
                children=(ChainChildSpec(name=Identifier("SettlementCycle")),),
            ),
        ),
        limit_schedules=(),
    )


# -- Typed projection structure ---------------------------------------------


def test_topology_graph_has_role_nodes_for_every_declared_role() -> None:
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
    role_ids = {n.id for n in g.nodes if n.kind == "role"}
    assert role_ids == {
        "role__InternalRole",
        "role__ExternalRole",
        "role__CustomerSubledger",
    }


def test_topology_graph_role_carries_scope_and_templated() -> None:
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
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
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
    tmpl = next(n for n in g.nodes if n.kind == "template")
    assert tmpl.id == "tmpl__SettlementCycle"
    # Inner label is just the name now — the per-key labels were dropped
    # to keep nodes compact (the key is infrastructure-only).
    assert tmpl.label == "SettlementCycle"
    # Metadata still carries the full source data for tooltips / future
    # editor surfaces; the cluster_label matches the inner label.
    # Z.B (2026-05-15): transfer_type metadata key dropped (no field).
    assert tmpl.metadata["transfer_key"] == "merchant_id"
    assert tmpl.metadata["cluster_label"] == "SettlementCycle"


def test_topology_graph_rail_nodes_for_template_legs_and_chain_refs() -> None:
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
    rail_ids = {n.id for n in g.nodes if n.kind == "rail"}
    # FeeCharge is a leg of SettlementCycle template → emitted as rail node.
    assert "rail__FeeCharge" in rail_ids
    # InboundRail is referenced by a chain edge (chain.parent).
    # SettlementCycle is in chain.children but it's a template, not a rail.
    assert "rail__InboundRail" in rail_ids
    # OutboundRail isn't in any template, isn't in any chain → no rail node.
    # (It still produces a bundle edge between roles; the rail itself
    # isn't a node unless a template or chain references it.)
    assert "rail__OutboundRail" not in rail_ids


def test_topology_graph_bundle_edge_collapses_parallel_rails() -> None:
    rails = (
        _make_two_leg("RailA", "X", "Y"),
        _make_two_leg("RailB", "X", "Y"),
    )
    inst = L2Instance(
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
    g = topology_graph_for(inst, db_table_prefix="test")
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
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
    self_loops = [e for e in g.edges if e.kind == "self_loop"]
    assert len(self_loops) == 1
    loop = self_loops[0]
    assert loop.source == "role__CustomerSubledger"
    assert loop.target == "role__CustomerSubledger"
    assert loop.metadata["direction"] == "Debit"
    assert loop.metadata["rail_name"] == "FeeCharge"


def test_topology_graph_template_member_edges() -> None:
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
    members = [e for e in g.edges if e.kind == "template_member"]
    assert len(members) == 1
    member = members[0]
    assert member.source == "tmpl__SettlementCycle"
    assert member.target == "rail__FeeCharge"


def test_topology_graph_emits_control_parent_for_template_parent_role() -> None:
    """A templated role with parent_role gets a control_parent edge."""
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
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
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
    chains = [e for e in g.edges if e.kind == "chain"]
    assert len(chains) == 1
    chain = chains[0]
    assert chain.source == "rail__InboundRail"
    # Child resolves to a template (SettlementCycle is a template name).
    assert chain.target == "tmpl__SettlementCycle"
    # Z.A: singleton-children rows carry cardinality="required".
    assert chain.metadata["cardinality"] == "required"
    assert "xor_siblings" not in chain.metadata
    assert "required" in chain.label


def test_topology_graph_chain_edge_carries_xor_group_metadata() -> None:
    inst = L2Instance(
        accounts=(
            Account(id=Identifier("a"), role=Identifier("X"), scope="internal"),
            Account(id=Identifier("b"), role=Identifier("Y"), scope="internal"),
            Account(id=Identifier("c"), role=Identifier("Z"), scope="internal"),
        ),
        account_templates=(),
        rails=(
            _make_two_leg("Parent", "X", "Y"),
            _make_two_leg("ChildA", "Y", "Z"),
            _make_two_leg("ChildB", "Y", "Z"),
        ),
        transfer_templates=(),
        chains=(
            Chain(
                parent=Identifier("Parent"),
                children=(
                    ChainChildSpec(name=Identifier("ChildA")),
                    ChainChildSpec(name=Identifier("ChildB")),
                ),
            ),
        ),
        limit_schedules=(),
    )
    g = topology_graph_for(inst, db_table_prefix="test")
    chain_edges = [e for e in g.edges if e.kind == "chain"]
    # Z.A: a multi-children chain row produces one edge per child.
    assert len(chain_edges) == 2
    for ce in chain_edges:
        assert ce.metadata["cardinality"] == "xor"
        # xor_siblings names the alternation set so the renderer can
        # group them visually.
        assert ce.metadata["xor_siblings"] == "ChildA,ChildB"


def test_topology_graph_template_member_edge_carries_xor_group_metadata() -> None:
    """AB.3.8 — when a template's ``leg_rail_xor_groups`` carries a
    rail, the template_member edge for that rail tags its metadata
    with ``xor_group_index`` (str-of-int, 0-based). Non-grouped
    leg_rails get no key — the absence IS the not-grouped signal.
    """
    inst = L2Instance(
        accounts=(
            Account(id=Identifier("a"), role=Identifier("X"), scope="internal"),
            Account(id=Identifier("b"), role=Identifier("Y"), scope="internal"),
        ),
        account_templates=(),
        rails=(
            SingleLegRail(
                name=Identifier("Auto"),
                metadata_keys=(),
                leg_role=(Identifier("X"),),
                leg_direction="Variable",
                origin="InternalInitiated",
            ),
            SingleLegRail(
                name=Identifier("Standard"),
                metadata_keys=(),
                leg_role=(Identifier("X"),),
                leg_direction="Variable",
                origin="InternalInitiated",
            ),
            SingleLegRail(
                name=Identifier("Slow"),
                metadata_keys=(),
                leg_role=(Identifier("X"),),
                leg_direction="Variable",
                origin="InternalInitiated",
            ),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("Cycle"),
                expected_net=Decimal("0"),
                transfer_key=(),
                completion="business_day_end",
                leg_rails=(
                    Identifier("Auto"),
                    Identifier("Standard"),
                    Identifier("Slow"),
                ),
                leg_rail_xor_groups=((
                    Identifier("Auto"), Identifier("Standard"),
                ),),
            ),
        ),
        chains=(),
        limit_schedules=(),
    )
    g = topology_graph_for(inst, db_table_prefix="test")
    members = [e for e in g.edges if e.kind == "template_member"]
    by_target = {e.target: e for e in members}
    # Grouped rails tag the group index; non-grouped Slow has no key.
    assert by_target["rail__Auto"].metadata["xor_group_index"] == "0"
    assert by_target["rail__Standard"].metadata["xor_group_index"] == "0"
    assert "xor_group_index" not in by_target["rail__Slow"].metadata


def test_topology_graphviz_per_rail_emits_xor_subcluster() -> None:
    """AB.3.8 — the graphviz per-rail renderer wraps XOR-grouped
    leg_rails in a nested sub-cluster inside the template cluster.
    The sub-cluster's label is "XOR group N (exactly 1 fires)" so
    the analyst can see the mutual-exclusion contract directly on
    the topology diagram.

    Skipped if the ``graphviz`` package isn't installed in the test
    env (it's a soft dep — typed projection covers the contract).
    """
    import pytest
    graphviz = pytest.importorskip("graphviz")
    del graphviz  # only used as availability check
    from recon_gen.common.l2.topology import build_topology_graph_per_rail
    inst = load_instance(FIXTURES / "spec_example.yaml")
    g = build_topology_graph_per_rail(
        inst, db_table_prefix="spec_example",
    )
    src = g.source
    # spec_example's SettlementTimingCycle declares one XOR group.
    assert "cluster_tmpl_SettlementTimingCycle_xor_0" in src
    assert "XOR group 1 (exactly 1 fires)" in src


def test_topology_graph_chain_edge_carries_fan_in_metadata() -> None:
    """AB.4.9 — when a chain declares ``fan_in=True``, the typed
    projection's chain edge metadata tags ``fan_in='true'`` and
    (when set) ``expected_parent_count='N'``. Non-fan-in chain
    edges get neither key.
    """
    inst = L2Instance(
        accounts=(
            Account(id=Identifier("a"), role=Identifier("X"), scope="internal"),
            Account(id=Identifier("b"), role=Identifier("Y"), scope="internal"),
        ),
        account_templates=(),
        rails=(
            _make_two_leg("Parent", "X", "Y"),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("ChildTpl"),
                expected_net=Decimal("0"),
                transfer_key=(),
                completion="business_day_end",
                leg_rails=(Identifier("Parent"),),
            ),
        ),
        chains=(
            Chain(
                parent=Identifier("Parent"),
                children=(
                    ChainChildSpec(
                        name=Identifier("ChildTpl"),
                        fan_in=True,
                        expected_parent_count=3,
                    ),
                ),
            ),
        ),
        limit_schedules=(),
    )
    g = topology_graph_for(inst, db_table_prefix="test")
    chain_edges = [e for e in g.edges if e.kind == "chain"]
    assert len(chain_edges) == 1
    chain_edge = chain_edges[0]
    assert chain_edge.metadata["fan_in"] == "true"
    assert chain_edge.metadata["expected_parent_count"] == "3"


def test_topology_graphviz_per_rail_renders_fan_in_chain_distinctly() -> None:
    """AB.4.9 — the graphviz per-rail renderer applies a distinct
    visual treatment to fan_in chain edges: ``[fan-in N→1]`` label
    annotation + bolder pen + double-arrowhead. spec_example
    declares ``BatchPayoutTrigger → BatchedPayoutBatch`` with
    expected_parent_count=2."""
    import pytest
    graphviz = pytest.importorskip("graphviz")
    del graphviz
    from recon_gen.common.l2.topology import build_topology_graph_per_rail
    inst = load_instance(FIXTURES / "spec_example.yaml")
    g = build_topology_graph_per_rail(
        inst, db_table_prefix="spec_example",
    )
    src = g.source
    # Fan-in label annotation embedded in the chain edge label.
    assert "fan-in 2→1" in src


# -- Typed projection against shipped fixtures ------------------------------


def test_topology_graph_for_spec_example_smoke() -> None:
    inst = load_instance(FIXTURES / "spec_example.yaml")
    g = topology_graph_for(inst, db_table_prefix="spec_example")
    # Carries the instance name for page titles + JSON output.
    assert g.instance_name == DEFAULT_PREFIX
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
    g = topology_graph_for(inst, db_table_prefix="sasquatch_pr")
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
    g = topology_graph_for(_kitchen_instance(), db_table_prefix="test")
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
        g.nodes = ()  # pyright: ignore[reportAttributeAccessIssue]: testing the frozen-dataclass mutation rejection itself
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.label = "x"  # pyright: ignore[reportAttributeAccessIssue]: testing the frozen-dataclass mutation rejection itself
