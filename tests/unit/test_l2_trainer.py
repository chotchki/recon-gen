"""Unit tests for ``common.l2.trainer.plants_per_node``.

The trainer overlay reads the in-memory ScenarioPlant (no DB) and
counts plants per topology node. These tests cover:

1. The default (auto-derived) scenario produces a non-empty map for
   spec_example (the auto-scenario is opinionated; if it stops
   producing plants we want to know).
2. A custom scenario routes each plant kind to the right node IDs
   (drift on role+rail, overdraft on role-only, supersession on
   rail-only, transfer_template on tmpl-only).
3. RailFiringPlant is excluded — broad-mode bulk firings aren't
   "planted exceptions" per the SPEC.
4. Multiple plants on the same node accumulate.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.primitives import Identifier, L2Instance
from quicksight_gen.common.l2.seed import (
    DriftPlant,
    OverdraftPlant,
    RailFiringPlant,
    ScenarioPlant,
    SupersessionPlant,
    TransferTemplatePlant,
)
from quicksight_gen.common.l2.topology import (
    _rail_id,
    _role_id,
    _template_id,
)
from quicksight_gen.common.l2.trainer import plants_per_node


_SPEC = Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"


@pytest.fixture
def spec_example() -> L2Instance:
    return load_instance(_SPEC)


def test_default_scenario_produces_nonempty_trainer_map(
    spec_example: L2Instance,
) -> None:
    """The bundled auto-scenario plants something on spec_example.

    Smoke-test: if the picker stops producing plants for spec_example
    (e.g. someone removes a Rail that was DriftPlant's host), we want
    to know — the trainer overlay would silently render empty.
    """
    tm = plants_per_node(spec_example)
    assert len(tm.by_node_id) > 0


def test_custom_scenario_routes_plants_to_correct_nodes(
    spec_example: L2Instance,
) -> None:
    """Each plant kind lands on the topology node(s) the trainer
    chrome expects. Constructing the scenario by hand keeps the
    assertion exact.
    """
    drift_account = spec_example.accounts[5]  # cust-001 → CustomerSubledger
    custom = ScenarioPlant(
        template_instances=(),
        today=date(2030, 1, 1),
        drift_plants=(
            DriftPlant(
                account_id=drift_account.id,
                days_ago=10,
                delta_money=Decimal("12.34"),
                rail_name=Identifier("ExternalRailInbound"),
                counter_account_id=Identifier("external-counterparty-one"),
            ),
        ),
        overdraft_plants=(
            OverdraftPlant(
                account_id=drift_account.id,
                days_ago=5,
                money=Decimal("-50.00"),
            ),
        ),
        supersession_plants=(
            SupersessionPlant(
                account_id=drift_account.id,
                days_ago=3,
                transfer_type="ach",
                rail_name=Identifier("ExternalRailInbound"),
                original_amount=Decimal("100.00"),
                corrected_amount=Decimal("90.00"),
            ),
        ),
        transfer_template_plants=(
            TransferTemplatePlant(
                template_name=Identifier("MerchantSettlementCycle"),
                days_ago=7,
                amount=Decimal("0"),
                source_account_id=drift_account.id,
                destination_account_id=Identifier("external-counterparty-one"),
                firing_seq=1,
            ),
        ),
    )

    tm = plants_per_node(spec_example, scenario=custom)

    # Drift hits both the role + the rail.
    assert tm.by_node_id[_role_id(Identifier("CustomerSubledger"))] == {
        "drift": 1,
        "overdraft": 1,
    }
    assert tm.by_node_id[_rail_id(Identifier("ExternalRailInbound"))] == {
        "drift": 1,
        "supersession": 1,
    }
    # Template lands on its tmpl__ id only.
    assert tm.by_node_id[_template_id(Identifier("MerchantSettlementCycle"))] == {
        "transfer_template": 1,
    }


def test_rail_firing_plants_are_excluded(spec_example: L2Instance) -> None:
    """RailFiringPlant is broad-mode bulk firings, not a SHOULD-violation.
    The trainer overlay shouldn't surface them as "planted exceptions".
    """
    custom = ScenarioPlant(
        template_instances=(),
        today=date(2030, 1, 1),
        rail_firing_plants=(
            RailFiringPlant(
                rail_name=Identifier("ExternalRailInbound"),
                days_ago=1,
                amount=Decimal("100"),
                account_id_a=Identifier("cust-001"),
                account_id_b=Identifier("external-counterparty-one"),
                firing_seq=1,
            ),
        ),
    )

    tm = plants_per_node(spec_example, scenario=custom)
    # Empty map — no plants land anywhere.
    assert tm.by_node_id == {}


def test_multiple_plants_on_same_node_accumulate(
    spec_example: L2Instance,
) -> None:
    """Two DriftPlants on the same rail should bump the count to 2,
    not overwrite each other.
    """
    custom = ScenarioPlant(
        template_instances=(),
        today=date(2030, 1, 1),
        drift_plants=(
            DriftPlant(
                account_id=Identifier("cust-001"),
                days_ago=10,
                delta_money=Decimal("12.34"),
                rail_name=Identifier("ExternalRailInbound"),
                counter_account_id=Identifier("external-counterparty-one"),
            ),
            DriftPlant(
                account_id=Identifier("cust-002"),
                days_ago=20,
                delta_money=Decimal("-5"),
                rail_name=Identifier("ExternalRailInbound"),
                counter_account_id=Identifier("external-counterparty-one"),
            ),
        ),
    )

    tm = plants_per_node(spec_example, scenario=custom)
    # Both customers are CustomerSubledger; rail is the same → count 2.
    assert tm.by_node_id[_rail_id(Identifier("ExternalRailInbound"))] == {
        "drift": 2,
    }
    # Both account_ids share the role → role count is 2.
    assert tm.by_node_id[_role_id(Identifier("CustomerSubledger"))] == {
        "drift": 2,
    }
