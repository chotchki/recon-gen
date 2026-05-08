"""Unit tests for ``tests/e2e/_harness_seed.py::build_planted_manifest``
(M.4.1.b).

The planted_manifest is what M.4.1.f's failure dump consumes AND what
M.4.1.d/.e Playwright assertions iterate to find planted rows on the
right deployed dashboard sheets. So a buggy manifest-builder either
(a) misses plant kinds the harness should be asserting, or (b) reports
the wrong column for a plant kind so the assertion can't find the row.

This file exercises the builder against synthetic ScenarioPlants —
no real auto-scenario picker, no DB, no fixtures. The integration
smoke (real picker → real plants → real manifest) lives in
``tests/test_auto_scenario_broad.py`` + the harness fixture itself.

Lives at the project test root (not under ``tests/e2e/``) so it runs
in the default ``pytest`` invocation — pure-data unit tests, not e2e.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Add tests/e2e to import path so the test can pull in the helper
# module directly without adding it to the package install.
sys.path.insert(0, str(Path(__file__).parent))
from _harness_seed import build_planted_manifest  # noqa: E402

from quicksight_gen.common.l2.primitives import Identifier
from quicksight_gen.common.l2.seed import (
    DriftPlant,
    InvFanoutPlant,
    LimitBreachPlant,
    OverdraftPlant,
    RailFiringPlant,
    ScenarioPlant,
    StuckPendingPlant,
    StuckUnbundledPlant,
    SupersessionPlant,
    TemplateInstance,
    TransferTemplatePlant,
)


def _empty_scenario() -> ScenarioPlant:
    """Minimal ScenarioPlant — every plant tuple defaults to ()."""
    return ScenarioPlant(
        template_instances=(
            TemplateInstance(
                template_role=Identifier("CustomerSubledger"),
                account_id=Identifier("cust-001"),
                name=Identifier("Customer 1"),  # type: ignore[arg-type]: TemplateInstance.name expects Name newtype, Identifier shape-compatible
            ),
        ),
        today=date(2030, 1, 1),
    )


def test_empty_scenario_yields_empty_lists() -> None:
    """A scenario with no planted SHOULD-violations or rail firings
    produces a manifest where every plant-kind list is empty (not
    missing) — Playwright assertions iterate every kind regardless."""
    manifest = build_planted_manifest(_empty_scenario())
    expected_kinds = {
        "drift_plants",
        "overdraft_plants",
        "limit_breach_plants",
        "stuck_pending_plants",
        "stuck_unbundled_plants",
        "supersession_plants",
        "transfer_template_plants",
        "rail_firing_plants",
        "inv_fanout_plants",
    }
    assert set(manifest.keys()) == expected_kinds
    for kind, entries in manifest.items():
        assert entries == [], (
            f"empty scenario should yield empty list for {kind!r}; got {entries!r}"
        )


def test_drift_plant_manifest_carries_account_and_delta() -> None:
    """DriftPlant manifest entries carry account_id, days_ago,
    delta_money, rail_name — the columns the L1 dashboard's Drift
    sheet table renders + the assertion needs to find the row."""
    p = DriftPlant(
        account_id=Identifier("cust-001"),
        days_ago=2,
        delta_money=Decimal("12.34"),
        rail_name=Identifier("CustomerInboundACH"),
        counter_account_id=Identifier("ext-counter"),
    )
    scenario = ScenarioPlant(
        template_instances=(),
        drift_plants=(p,),
        today=date(2030, 1, 1),
    )
    manifest = build_planted_manifest(scenario)
    assert manifest["drift_plants"] == [{
        "account_id": "cust-001",
        "days_ago": 2,
        "delta_money": Decimal("12.34"),
        "rail_name": "CustomerInboundACH",
    }]


def test_limit_breach_plant_manifest_carries_amount_not_breach_amount() -> None:
    """The LimitBreachPlant dataclass field is ``amount``, not
    ``breach_amount`` — the manifest matches the source field name so
    cross-reference between scenario + manifest is one-to-one (a
    rename here would be a footgun for assertions that use the field
    name as a column key)."""
    p = LimitBreachPlant(
        account_id=Identifier("cust-001"),
        days_ago=4,
        transfer_type="ach",
        rail_name=Identifier("OutboundACH"),
        amount=Decimal("6000.00"),
        counter_account_id=Identifier("ext-counter"),
    )
    scenario = ScenarioPlant(
        template_instances=(),
        limit_breach_plants=(p,),
        today=date(2030, 1, 1),
    )
    entry = build_planted_manifest(scenario)["limit_breach_plants"][0]
    assert entry["amount"] == Decimal("6000.00")
    assert "breach_amount" not in entry


def test_overdraft_plant_manifest_carries_account_and_money() -> None:
    p = OverdraftPlant(
        account_id=Identifier("cust-002"),
        days_ago=1,
        money=Decimal("-500.00"),
    )
    manifest = build_planted_manifest(ScenarioPlant(
        template_instances=(), overdraft_plants=(p,), today=date(2030, 1, 1),
    ))
    assert manifest["overdraft_plants"] == [{
        "account_id": "cust-002",
        "days_ago": 1,
        "money": Decimal("-500.00"),
    }]


def test_stuck_pending_and_unbundled_manifests_share_shape() -> None:
    """Stuck-pending and stuck-unbundled plants carry the same fields
    (account_id, days_ago, transfer_type, rail_name, amount). The
    L1 dashboard shows them on different sheets but the manifest
    shape is identical."""
    sp = StuckPendingPlant(
        account_id=Identifier("cust-001"),
        days_ago=3,
        transfer_type="ach",
        rail_name=Identifier("InboundACH"),
        amount=Decimal("250.00"),
    )
    su = StuckUnbundledPlant(
        account_id=Identifier("cust-002"),
        days_ago=5,
        transfer_type="charge",
        rail_name=Identifier("FeeAccrual"),
        amount=Decimal("12.50"),
    )
    manifest = build_planted_manifest(ScenarioPlant(
        template_instances=(),
        stuck_pending_plants=(sp,),
        stuck_unbundled_plants=(su,),
        today=date(2030, 1, 1),
    ))
    assert manifest["stuck_pending_plants"][0].keys() == (
        manifest["stuck_unbundled_plants"][0].keys()
    )


def test_supersession_plant_manifest_carries_both_amounts() -> None:
    """SupersessionPlant emits TWO rows (original + correction) under
    one logical id. The manifest entry carries both amounts so the
    Supersession Audit sheet assertion can verify the correction
    landed at the expected delta."""
    p = SupersessionPlant(
        account_id=Identifier("cust-001"),
        days_ago=6,
        transfer_type="settlement",
        rail_name=Identifier("Settlement"),
        original_amount=Decimal("100.00"),
        corrected_amount=Decimal("275.00"),
    )
    entry = build_planted_manifest(ScenarioPlant(
        template_instances=(), supersession_plants=(p,), today=date(2030, 1, 1),
    ))["supersession_plants"][0]
    assert entry["original_amount"] == Decimal("100.00")
    assert entry["corrected_amount"] == Decimal("275.00")


def test_transfer_template_plant_manifest_carries_template_name_and_seq() -> None:
    """TransferTemplatePlant carries template_name + firing_seq +
    days_ago + amount. firing_seq disambiguates multiple firings of
    the same template per the M.3.10g 3-firings-per-TT pattern."""
    p1 = TransferTemplatePlant(
        template_name=Identifier("MerchantSettlementCycle"),
        days_ago=3, amount=Decimal("125.00"),
        source_account_id=Identifier("ext-card"),
        destination_account_id=Identifier("merchant-clearing"),
        firing_seq=1,
    )
    p2 = TransferTemplatePlant(
        template_name=Identifier("MerchantSettlementCycle"),
        days_ago=4, amount=Decimal("125.00"),
        source_account_id=Identifier("ext-card"),
        destination_account_id=Identifier("merchant-clearing"),
        firing_seq=2,
    )
    entries = build_planted_manifest(ScenarioPlant(
        template_instances=(),
        transfer_template_plants=(p1, p2),
        today=date(2030, 1, 1),
    ))["transfer_template_plants"]
    assert [e["firing_seq"] for e in entries] == [1, 2]
    assert all(
        e["template_name"] == "MerchantSettlementCycle" for e in entries
    )


def test_rail_firing_plant_manifest_carries_template_and_chain_links() -> None:
    """RailFiringPlant manifest carries:
    - template_name (set when the rail is a leg_rail; None otherwise)
    - transfer_parent_id (set for chain-child firings; None otherwise)

    Both being None for a standalone-rail firing is the M.4.2 default;
    both being set is the M.4.2a leg_rail-of-template + chain-child
    pattern."""
    standalone = RailFiringPlant(
        rail_name=Identifier("StandaloneRail"),
        days_ago=1, firing_seq=1, amount=Decimal("100.00"),
        account_id_a=Identifier("a"), account_id_b=Identifier("b"),
    )
    chain_child = RailFiringPlant(
        rail_name=Identifier("ChildRail"),
        days_ago=2, firing_seq=3, amount=Decimal("100.00"),
        account_id_a=Identifier("a"), account_id_b=Identifier("b"),
        transfer_parent_id="tr-rail-0001",
        template_name=Identifier("Cycle"),
    )
    entries = build_planted_manifest(ScenarioPlant(
        template_instances=(),
        rail_firing_plants=(standalone, chain_child),
        today=date(2030, 1, 1),
    ))["rail_firing_plants"]
    # Standalone: both template_name + transfer_parent_id are None.
    assert entries[0]["template_name"] is None
    assert entries[0]["transfer_parent_id"] is None
    # Chain child: both populated.
    assert entries[1]["template_name"] == "Cycle"
    assert entries[1]["transfer_parent_id"] == "tr-rail-0001"


def test_inv_fanout_plant_manifest_carries_recipient_and_senders() -> None:
    """InvFanoutPlant manifest entries carry recipient_account_id +
    sender_account_ids tuple — the columns assert_inv_planted_rows_visible
    queries the prefixed Inv matviews with."""
    p = InvFanoutPlant(
        recipient_account_id=Identifier("cust-001"),
        sender_account_ids=(
            Identifier("ext-a"), Identifier("ext-b"), Identifier("ext-c"),
        ),
        days_ago=2,
        transfer_type="ach",
        rail_name=Identifier("ExternalRailInbound"),
        amount_per_transfer=Decimal("500.00"),
    )
    entries = build_planted_manifest(ScenarioPlant(
        template_instances=(),
        inv_fanout_plants=(p,),
        today=date(2030, 1, 1),
    ))["inv_fanout_plants"]
    assert entries == [{
        "recipient_account_id": "cust-001",
        "sender_account_ids": ("ext-a", "ext-b", "ext-c"),
        "days_ago": 2,
        "transfer_type": "ach",
        "rail_name": "ExternalRailInbound",
        "amount_per_transfer": Decimal("500.00"),
    }]


def test_manifest_keys_match_scenario_plant_attribute_names() -> None:
    """Manifest dict keys exactly match the ScenarioPlant tuple-field
    names — so a new plant kind added to ScenarioPlant naturally
    drops into the manifest without renaming. Catches drift if
    someone adds a plant kind without extending the manifest builder.
    """
    manifest = build_planted_manifest(_empty_scenario())
    # Walk ScenarioPlant's dataclass fields; every tuple-typed plant
    # field except `template_instances` (synthetic accounts) and
    # `today` (date) MUST appear as a manifest key.
    import dataclasses
    plant_field_names = {
        f.name
        for f in dataclasses.fields(ScenarioPlant)
        if f.name.endswith("_plants")
    }
    assert plant_field_names == set(manifest.keys()), (
        f"manifest keys drifted from ScenarioPlant plant-tuple fields:\n"
        f"  scenario plants: {sorted(plant_field_names)!r}\n"
        f"  manifest keys:   {sorted(manifest.keys())!r}\n"
        f"Add the missing kind to build_planted_manifest "
        f"or remove the unused manifest entry."
    )
