"""``visible_entities_for`` semantics (X.4.f.8).

Pure unit tests over ``spec_example.yaml``. The home page's
diagram-click filter (the JS that calls ``GET /diagram/visible``)
relies on these mappings being consistent with the diagram's own
focus filter (``_focus_set``):

- focus on a role → that role's accounts/templates + adjacent
  rails + their roles + control-parent neighbors;
- focus on a rail → just that rail + its endpoint roles + their
  accounts/templates;
- focus on a template → just that template + its leg-rails +
  endpoint roles;
- no focus / unknown focus → all entities (no filter).
"""

from __future__ import annotations

from pathlib import Path

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.topology import visible_entities_for


_FIXTURE = Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"


def _instance():
    return load_instance(_FIXTURE)


# ---------------------------------------------------------------------------
# No focus / unknown focus → full set
# ---------------------------------------------------------------------------


def test_no_focus_returns_all_entities() -> None:
    inst = _instance()
    v = visible_entities_for(inst, None)
    assert v["account"] == frozenset(str(a.id) for a in inst.accounts)
    assert v["account_template"] == frozenset(
        str(t.role) for t in inst.account_templates
    )
    assert v["rail"] == frozenset(str(r.name) for r in inst.rails)
    assert v["transfer_template"] == frozenset(
        str(t.name) for t in inst.transfer_templates
    )
    # Z.A: chain composite key = "parent::sorted-children-csv" so the
    # editor + topology + visibility surfaces all address chain rows
    # the same way.
    assert v["chain"] == frozenset(
        f"{c.parent}::{','.join(sorted(str(ch.name) for ch in c.children))}"
        for c in inst.chains
    )
    assert v["limit_schedule"] == frozenset(
        f"{ls.parent_role}::{ls.rail}"
        for ls in inst.limit_schedules
    )


def test_unknown_focus_node_returns_all_entities() -> None:
    """Stale URL / typo / synthetic bundle id → un-filter (graceful)."""
    inst = _instance()
    v_known = visible_entities_for(inst, None)
    v_unknown = visible_entities_for(inst, "role__DoesNotExist")
    assert v_unknown == v_known
    # Bundle node IDs (``rail__bundle_3``) are graphviz-side aggregates;
    # not in the typed adjacency. Should also un-filter.
    v_bundle = visible_entities_for(inst, "rail__bundle_0")
    assert v_bundle == v_known


# ---------------------------------------------------------------------------
# Focused on a role
# ---------------------------------------------------------------------------


def test_focus_on_customer_subledger_role_includes_accounts_with_that_role() -> None:
    """spec_example: cust-001 + cust-002 both play CustomerSubledger."""
    inst = _instance()
    v = visible_entities_for(inst, "role__CustomerSubledger")
    assert "cust-001" in v["account"]
    assert "cust-002" in v["account"]
    # The AccountTemplate that materializes the role.
    assert "CustomerSubledger" in v["account_template"]
    # Rails that touch CustomerSubledger should be visible.
    assert "ExternalRailInbound" in v["rail"]
    assert "ExternalRailOutbound" in v["rail"]
    assert "SubledgerCharge" in v["rail"]
    # Unrelated accounts (NorthPool, SouthPool) are NOT in the filter.
    # NorthPool is connected to SouthPool via PoolBalancing rail, NOT
    # to CustomerSubledger, so filtering by CustomerSubledger excludes it.
    assert "north-pool" not in v["account"]
    assert "south-pool" not in v["account"]


def test_focus_on_customer_ledger_role_includes_subledger_via_control_parent() -> None:
    """control_parent edge (subledger → ledger) means focusing on the
    ledger pulls in the subledger role too."""
    inst = _instance()
    v = visible_entities_for(inst, "role__CustomerLedger")
    # The ledger Account itself.
    assert "customer-ledger" in v["account"]
    # The subledger Accounts roll up via control_parent → also visible.
    assert "cust-001" in v["account"]
    assert "cust-002" in v["account"]
    # Limit schedules anchored on CustomerLedger.
    assert any(
        cls.startswith("CustomerLedger::") for cls in v["limit_schedule"]
    )


# ---------------------------------------------------------------------------
# Focused on a rail
# ---------------------------------------------------------------------------


def test_focus_on_external_rail_inbound_filters_to_endpoint_roles() -> None:
    """The rail itself + its source/destination role accounts."""
    inst = _instance()
    v = visible_entities_for(inst, "rail__ExternalRailInbound")
    # The rail itself.
    assert "ExternalRailInbound" in v["rail"]
    # Its endpoints: ExternalCounterparty + CustomerSubledger.
    assert "external-counterparty-one" in v["account"]  # ExternalCounterparty
    assert "cust-001" in v["account"]  # CustomerSubledger
    assert "cust-002" in v["account"]  # CustomerSubledger
    # Other rails (PoolBalancing, etc.) NOT in the filter.
    assert "PoolBalancing" not in v["rail"]


# ---------------------------------------------------------------------------
# Focused on a template
# ---------------------------------------------------------------------------


def test_focus_on_template_includes_its_leg_rails() -> None:
    """Templates own their leg_rails — focus pulls them in via the
    template_member edge, then rail-completion adds endpoint roles."""
    inst = _instance()
    if not inst.transfer_templates:
        return  # spec_example has at least one; defensive
    tmpl = inst.transfer_templates[0]
    v = visible_entities_for(inst, f"tmpl__{tmpl.name}")
    assert str(tmpl.name) in v["transfer_template"]
    for rail_name in tmpl.leg_rails:
        assert str(rail_name) in v["rail"]
