"""``plants_per_node()`` — derive per-topology-node planted-exception counts
from an L2 scenario object (X.4.c.6).

The trainer-mode overlay shows the trainer "this role has 2 drift plants
and 1 overdraft" without involving the demo DB — every plant primitive
in ``common/l2/seed.py`` carries its host (``account_id`` / ``rail_name``
/ ``template_name``) directly, so a pure walk of the
``ScenarioPlant`` aggregates into a per-node count map.

Symmetric in shape to ``coverage.py`` — the chrome chrome consumes both
through the same ``data-presence`` / ``data-trainer-kinds`` SVG attr
pattern. They differ only in the data-source: coverage hits the DB,
trainer reads the in-memory scenario.

Severability: pure Python, no DB import, no async. The Studio route
that wraps this calls it at request time and serializes to JSON.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.primitives import Identifier, L2Instance
from recon_gen.common.l2.seed import ScenarioPlant
from recon_gen.common.l2.topology import (
    _rail_id,
    _role_id,
    _template_id,
)


# All the plant kinds the chrome cares about. Lower-case so they
# round-trip cleanly through the JSON shape and the SVG
# ``data-trainer-kinds`` attr (comma-joined) without escaping. AG.5
# (Gap E) made this an authoritative Literal (was a rotted ``str`` alias
# whose comment listed only the original 9) — it now enumerates every
# plant kind ``plants_per_node`` can emit, so a new ScenarioPlant tuple
# that forgets to wire a badge fails pyright at the ``_bump`` call site.
# NOTE: a superset of ``config.PlantKind`` (the 6 operator-TOGGLEABLE L1
# kinds) — the extra kinds are badge/timeline-visible but not gated by
# ``cfg.test_generator.plants``.
PlantKind = Literal[
    # Original 9 (M-phase).
    "drift", "overdraft", "limit_breach", "stuck_pending", "stuck_unbundled",
    "supersession", "failed", "transfer_template", "inv_fanout",
    # AB.1-AB.6 chain / template / cap plant kinds.
    "inbound_cap_breach",
    "two_template_chain", "chain_parent_disagreement",
    "xor_variant_missed_firing", "xor_variant_overlap",
    "fan_in_chain", "fan_in_chain_missing_parent", "fan_in_chain_extra_parent",
    "multi_xor_missed", "multi_xor_overlap",
]


@dataclass(frozen=True, slots=True)
class TrainerMap:
    """Per-topology-node planted-plant counts derived from a ScenarioPlant.

    ``by_node_id`` keys are the same topology IDs ``coverage.py`` uses
    (``role__X`` / ``rail__X`` / ``tmpl__X``); values are
    ``{plant_kind: count}`` mappings with one entry per plant kind that
    landed on that node. Nodes with zero plants are absent from the map
    (the chrome's "no badge" default is the empty case).
    """

    by_node_id: Mapping[str, Mapping[PlantKind, int]]


def plants_per_node(
    instance: L2Instance,
    scenario: ScenarioPlant | None = None,
) -> TrainerMap:
    """Count planted plants per topology node.

    When ``scenario`` is None, derives the auto-scenario via
    ``default_scenario_for(instance)`` — same default the demo apply
    pipeline uses so the trainer surface previews the same plants the
    deployed DB will carry.

    Each plant kind contributes:

    - **drift**: role(``account_id``) + rail(``rail_name``)
    - **overdraft**: role(``account_id``)
    - **limit_breach**: role(``account_id``) + rail(``rail_name``)
    - **stuck_pending**: rail(``rail_name``)
    - **stuck_unbundled**: rail(``rail_name``)
    - **supersession**: rail(``rail_name``)
    - **failed**: rail(``rail_name``)
    - **transfer_template**: template(``template_name``)
    - **inv_fanout**: rail(``rail_name``) (recipient role isn't a clean
      "host" — the recipient is one of N senders' targets, not a
      plant-singularity owner)

    ``RailFiringPlant`` is excluded — it's broad-mode bulk firings, not
    a SHOULD-violation per the SPEC, so it shouldn't show up as a
    "planted exception".

    A plant kind that lands on multiple nodes (e.g. drift on both a role
    and a rail) increments the count on each node — the trainer chrome
    shows badges per node, not per plant.
    """
    if scenario is None:
        scenario = default_scenario_for(instance).scenario

    role_lookup = _account_id_to_role(instance)
    counts: dict[str, Counter[PlantKind]] = {}

    def _bump(node_id: str, kind: PlantKind) -> None:
        counts.setdefault(node_id, Counter())[kind] += 1

    for p in scenario.drift_plants:
        role = role_lookup.get(p.account_id)
        if role is not None:
            _bump(_role_id(role), "drift")
        _bump(_rail_id(p.rail_name), "drift")

    for p in scenario.overdraft_plants:
        role = role_lookup.get(p.account_id)
        if role is not None:
            _bump(_role_id(role), "overdraft")

    for p in scenario.limit_breach_plants:
        role = role_lookup.get(p.account_id)
        if role is not None:
            _bump(_role_id(role), "limit_breach")
        _bump(_rail_id(p.rail_name), "limit_breach")

    for p in scenario.stuck_pending_plants:
        _bump(_rail_id(p.rail_name), "stuck_pending")

    for p in scenario.stuck_unbundled_plants:
        _bump(_rail_id(p.rail_name), "stuck_unbundled")

    for p in scenario.supersession_plants:
        _bump(_rail_id(p.rail_name), "supersession")

    for p in scenario.failed_transaction_plants:
        _bump(_rail_id(p.rail_name), "failed")

    for p in scenario.transfer_template_plants:
        _bump(_template_id(p.template_name), "transfer_template")

    for p in scenario.inv_fanout_plants:
        _bump(_rail_id(p.rail_name), "inv_fanout")

    # AG.5 (Gap E): AB.1-AB.6 plant kinds. Bindings per gap doc:
    #   - inbound_cap_breach → role + rail (mirrors limit_breach)
    #   - two_template_chain / chain_parent_disagreement / fan_in trio →
    #     the chain-CHILD template node (the disagreement / cardinality
    #     check is observed on the child)
    #   - xor_variant_missed/overlap → the template carrying the XOR group
    #   - multi_xor_missed/overlap → the chain PARENT (rail OR template;
    #     post-AG.3 a chain parent can be either), where the XOR
    #     violation is observed at the parent firing.
    for p in scenario.inbound_cap_breach_plants:
        role = role_lookup.get(p.account_id)
        if role is not None:
            _bump(_role_id(role), "inbound_cap_breach")
        _bump(_rail_id(p.rail_name), "inbound_cap_breach")

    for p in scenario.two_template_chain_plants:
        _bump(_template_id(p.child_template_name), "two_template_chain")

    for p in scenario.chain_parent_disagreement_plants:
        _bump(_template_id(p.child_template_name), "chain_parent_disagreement")

    for p in scenario.xor_variant_missed_firing_plants:
        _bump(_template_id(p.template_name), "xor_variant_missed_firing")

    for p in scenario.xor_variant_overlap_plants:
        _bump(_template_id(p.template_name), "xor_variant_overlap")

    for p in scenario.fan_in_chain_plants:
        _bump(_template_id(p.child_template_name), "fan_in_chain")

    for p in scenario.fan_in_chain_missing_parent_plants:
        _bump(_template_id(p.child_template_name), "fan_in_chain_missing_parent")

    for p in scenario.fan_in_chain_extra_parent_plants:
        _bump(_template_id(p.child_template_name), "fan_in_chain_extra_parent")

    for p in scenario.multi_xor_missed_plants:
        _bump(
            _chain_parent_node_id(p.chain_parent_rail_name, instance),
            "multi_xor_missed",
        )

    for p in scenario.multi_xor_overlap_plants:
        _bump(
            _chain_parent_node_id(p.chain_parent_rail_name, instance),
            "multi_xor_overlap",
        )

    return TrainerMap(
        by_node_id={k: dict(v) for k, v in counts.items()},
    )


def _chain_parent_node_id(name: Identifier, instance: L2Instance) -> str:
    """AG.5 (Gap E): resolve a chain-parent identifier to its topology
    node id. Post-AG.3 a chain parent may resolve to either a Rail or a
    TransferTemplate; pick the matching node so the multi_xor badge lands
    on the right shape. Defaults to the rail node if the name resolves to
    neither (defensive — the picker guards, but keep the badge from
    silently vanishing)."""
    if any(t.name == name for t in instance.transfer_templates):
        return _template_id(name)
    return _rail_id(name)


def _account_id_to_role(instance: L2Instance) -> dict[Identifier, Identifier]:
    """Reverse index from Account.id → Account.role.

    Plant primitives reference accounts by ``account_id``; the topology
    keys roles by ``role``. The lookup short-circuits when an account
    has no ``role`` (rare — a singleton-only Account; SPEC requires one
    of name/role/parent_role, but role is the topology surface).
    Materialized customers (``TemplateInstance.account_id``) aren't
    declared on ``instance.accounts`` — they're synthesized at seed
    time — so a missing key here for a TemplateInstance plant means
    the topology node is the AccountTemplate's role. The caller's
    fallback to ``role_lookup.get(...)`` returning None covers it.
    """
    by_id: dict[Identifier, Identifier] = {}
    for acct in instance.accounts:
        if acct.role is not None:
            by_id[acct.id] = acct.role
    # AccountTemplate's materialized children — if any plant references
    # a TemplateInstance.account_id, fall back to the template's role.
    # We can't know which materialized id maps to which template here
    # without re-running the seed; the auto-scenario plants reference
    # only declared Account.ids, so this is safe in practice. If a
    # customer plant lands without an entry, the chrome simply won't
    # badge that role — degraded but not wrong.
    return by_id
