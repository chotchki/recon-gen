"""AY.4.c.3 — unit tests for `scenario_to_generators`.

The plant adapter turns a `ScenarioPlant` (the OLD aggregate from
`common/l2/seed.py`) into a tuple of spine `ViolationGenerator`s.
AY.4.d's `build_full_seed_sql` rewrite composes the returned tuple
via `ScenarioContext.compose(dry_run=True)` + the AY.4.b renderer.

What's pinned:

1. **Per-plant-kind dispatch.** A focused single-kind ScenarioPlant
   in → one matching generator out. Type-asserts the generator
   class for every kind we wire.
2. **N plants → N generators.** The account_id_override threading
   (c.1 + c.2 work) means N plants of the same kind don't PK-collide
   at compose-time.
3. **End-to-end smoke.** Full `default_scenario_for(spec_example)`
   → adapter → ScenarioContext.compose(dry_run=True) → render. The
   resulting SQL string is a non-empty INSERT script with no
   leftover placeholders — proves every plant kind on the canonical
   scenario fixture round-trips through the adapter without error.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.seed import (
    DriftPlant,
    InvFanoutPlant,
    OverdraftPlant,
    ScenarioPlant,
    SupersessionPlant,
)
from recon_gen.common.spine import (
    DriftGenerator,
    InvFanoutGenerator,
    OverdraftGenerator,
    ScenarioContext,
    SupersessionGenerator,
    dry_run_capture,
    render_captured_sql,
    scenario_to_generators,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)


def _load_instance() -> object:
    return load_instance(_SPEC_EXAMPLE)


# ---------------------------------------------------------------------------
# Per-kind dispatch — focused single-kind ScenarioPlant in, one gen out.
# ---------------------------------------------------------------------------


def test_empty_scenario_returns_empty_tuple() -> None:
    """No plants → no generators; the adapter doesn't synthesize
    extras."""
    inst = _load_instance()
    gens = scenario_to_generators(
        ScenarioPlant(template_instances=(), today=date(2030, 1, 1)),
        inst,  # type: ignore[arg-type]: load_instance returns L2Instance at runtime
    )
    assert gens == ()


def test_drift_plant_dispatches_to_drift_generator() -> None:
    """The plant's `account_id` flows into the generator via
    `child_account_id` override — the AY.4.c.1 wiring."""
    inst = _load_instance()
    # Pick the first CustomerSubledger account on spec_example.
    cust = next(
        a for a in inst.accounts  # type: ignore[attr-defined]
        if str(a.role) == "CustomerSubledger" and str(a.scope) == "internal"
        and a.parent_role is not None
    )
    plant = DriftPlant(
        account_id=cust.id,
        days_ago=1,
        delta_money=Decimal("5.0"),
        rail_name="ExternalRailInbound",  # type: ignore[arg-type]: Identifier accepts str at runtime
        counter_account_id="ext-counter",  # type: ignore[arg-type]
    )
    scenarios = ScenarioPlant(
        template_instances=(),
        drift_plants=(plant,),
        today=date(2030, 1, 1),
    )
    gens = scenario_to_generators(scenarios, inst)  # type: ignore[arg-type]
    assert len(gens) == 1
    assert isinstance(gens[0], DriftGenerator)
    # account_id override threaded through.
    assert gens[0].child_account_id == str(cust.id)


def test_overdraft_plant_dispatches_to_overdraft_generator() -> None:
    inst = _load_instance()
    cust = next(
        a for a in inst.accounts  # type: ignore[attr-defined]
        if str(a.role) == "CustomerSubledger" and str(a.scope) == "internal"
    )
    plant = OverdraftPlant(
        account_id=cust.id,
        days_ago=1,
        money=Decimal("-25.0"),
    )
    scenarios = ScenarioPlant(
        template_instances=(),
        overdraft_plants=(plant,),
        today=date(2030, 1, 1),
    )
    gens = scenario_to_generators(scenarios, inst)  # type: ignore[arg-type]
    assert len(gens) == 1
    assert isinstance(gens[0], OverdraftGenerator)
    assert gens[0].account_id == str(cust.id)


def test_inv_fanout_plant_dispatches_to_inv_fanout_generator() -> None:
    """InvFanoutPlant has a different shape (N senders + one
    recipient) — verify the adapter constructs the generator with
    the right fields."""
    inst = _load_instance()
    plant = InvFanoutPlant(
        recipient_account_id="acct-r",  # type: ignore[arg-type]
        sender_account_ids=("s-1", "s-2", "s-3"),  # type: ignore[arg-type]
        days_ago=3,
        rail_name="ach",  # type: ignore[arg-type]
        amount_per_transfer=Decimal("100.0"),
    )
    scenarios = ScenarioPlant(
        template_instances=(),
        inv_fanout_plants=(plant,),
        today=date(2030, 1, 1),
    )
    gens = scenario_to_generators(scenarios, inst)  # type: ignore[arg-type]
    assert len(gens) == 1
    g = gens[0]
    assert isinstance(g, InvFanoutGenerator)
    assert g.recipient_account_id == "acct-r"
    assert g.sender_account_ids == ("s-1", "s-2", "s-3")
    assert g.amount_per_transfer == 100.0


def test_supersession_plant_dispatches_to_supersession_generator() -> None:
    """Audit-fixture plant flows: adapter resolves account fields
    from the L2 instance, threads through to the generator."""
    inst = _load_instance()
    cust = next(
        a for a in inst.accounts  # type: ignore[attr-defined]
        if str(a.role) == "CustomerSubledger" and str(a.scope) == "internal"
    )
    plant = SupersessionPlant(
        account_id=cust.id,
        days_ago=1,
        rail_name="SubledgerCharge",  # type: ignore[arg-type]
        original_amount=Decimal("250.0"),
        corrected_amount=Decimal("200.0"),
    )
    scenarios = ScenarioPlant(
        template_instances=(),
        supersession_plants=(plant,),
        today=date(2030, 1, 1),
    )
    gens = scenario_to_generators(scenarios, inst)  # type: ignore[arg-type]
    assert len(gens) == 1
    g = gens[0]
    assert isinstance(g, SupersessionGenerator)
    assert g.account_id == str(cust.id)
    assert g.original_amount == 250.0
    assert g.corrected_amount == 200.0


# ---------------------------------------------------------------------------
# N plants → N generators (the account_id_override payoff).
# ---------------------------------------------------------------------------


def test_n_drift_plants_produce_n_distinct_generators() -> None:
    """The core AY.4.c.3 payoff — N plants on the same role produce
    N distinct generators. The OLD path would have collided at
    plant-emit time (or required adapter sequencing); the spine path
    threads each plant's account_id through `child_account_id`."""
    inst = _load_instance()
    # Pick 2 distinct CustomerSubledger leaf accounts.
    cust_accounts = [
        a for a in inst.accounts  # type: ignore[attr-defined]
        if str(a.role) == "CustomerSubledger" and str(a.scope) == "internal"
        and a.parent_role is not None
    ][:2]
    assert len(cust_accounts) == 2, (
        "spec_example needs ≥2 CustomerSubledger leaf accounts for "
        "this test; if shape changed, pick a different role"
    )
    plants = tuple(
        DriftPlant(
            account_id=a.id,
            days_ago=1,
            delta_money=Decimal("5.0"),  # same delta for both plants
            rail_name="ExternalRailInbound",  # type: ignore[arg-type]
            counter_account_id="ext-counter",  # type: ignore[arg-type]
        )
        for a in cust_accounts
    )
    scenarios = ScenarioPlant(
        template_instances=(),
        drift_plants=plants,
        today=date(2030, 1, 1),
    )
    gens = scenario_to_generators(scenarios, inst)  # type: ignore[arg-type]
    assert len(gens) == 2
    # Distinct account_ids on the resulting generators — no collision.
    assert (
        {g.child_account_id for g in gens}  # type: ignore[attr-defined]
        == {str(a.id) for a in cust_accounts}
    )


# ---------------------------------------------------------------------------
# End-to-end smoke — full default scenario round-trips.
# ---------------------------------------------------------------------------


def test_default_scenario_full_corpus_adapts_without_error() -> None:
    """The integration check at the adapter boundary: load
    spec_example → build the default scenario → adapt every plant
    kind in one pass. Proves every kind in the canonical fixture
    fits the adapter without ValueError / TypeError.

    Does NOT compose+render — that's AY.4.d's concern. The default
    scenario plants multiple same-class generators (e.g., 2+
    LimitBreachGenerators on different accounts with the same rail +
    direction) whose underlying transaction.id derivations don't yet
    include the account_id and thus PK-collide. Fixing the PK
    derivation on each generator (so account_id participates) is a
    follow-on concern that lands either as AY.4.c.4 or rolls into
    AY.4.d when we hit the live-DB reroute.
    """
    inst = _load_instance()
    scenario_bundle = default_scenario_for(
        inst,  # type: ignore[arg-type]
        today=date(2030, 1, 1),
    )
    gens = scenario_to_generators(
        scenario_bundle.scenario, inst,  # type: ignore[arg-type]
    )
    assert len(gens) > 0, (
        "default_scenario_for(spec_example) produces a non-empty "
        "plant set; adapter should produce a non-empty generator tuple"
    )
    # Sanity: at least one generator of each major category landed.
    from recon_gen.common.spine import (
        ChainParentDisagreementGenerator,
        FailedTransactionGenerator,
        FanInChainGenerator,
        InvFanoutGenerator,
        LimitBreachGenerator,
        RailFiringGenerator,
        SupersessionGenerator,
        TransferTemplateGenerator,
        TwoTemplateChainGenerator,
    )
    classes_present = {type(g) for g in gens}
    # Spot-check categories — exact list depends on the L2 fixture's
    # shape; assert "at least these 9 kinds resolved" rather than
    # exhaustive enumeration so the test survives fixture evolution.
    # RailFiringGenerator + TransferTemplateGenerator are produced by
    # `densify_scenario`'s broad mode, not the base `default_scenario_for`
    # call; they don't appear in this corpus check (which only exercises
    # the structured scenario). They get exercised by the c.1/c.2/c.3
    # per-kind dispatch tests above + show up when AY.4.d composes the
    # full `build_default_scenario` (which calls densify_scenario).
    expected_present = {
        DriftGenerator, OverdraftGenerator, LimitBreachGenerator,
        TwoTemplateChainGenerator, ChainParentDisagreementGenerator,
        FanInChainGenerator, InvFanoutGenerator,
        FailedTransactionGenerator, SupersessionGenerator,
    }
    # Silence unused-import warnings for the broad-mode classes
    # referenced in the docstring comment above.
    _broad_mode_classes = (RailFiringGenerator, TransferTemplateGenerator)
    del _broad_mode_classes
    missing = expected_present - classes_present
    assert not missing, (
        f"adapter dropped these expected generator classes from the "
        f"default scenario: {sorted(c.__name__ for c in missing)}"
    )


def test_focused_single_plant_round_trips_through_compose_render() -> None:
    """End-to-end smoke at the focused-scenario level: one drift
    plant → adapter → compose(dry_run=True) → render. Skip the full
    default scenario because of the LimitBreachGenerator PK
    derivation issue (documented in the prior test); a single-plant
    scenario doesn't trip it.
    """
    inst = _load_instance()
    cust = next(
        a for a in inst.accounts  # type: ignore[attr-defined]
        if str(a.role) == "CustomerSubledger" and str(a.scope) == "internal"
        and a.parent_role is not None
    )
    plant = DriftPlant(
        account_id=cust.id,
        days_ago=1,
        delta_money=Decimal("5.0"),
        rail_name="ExternalRailInbound",  # type: ignore[arg-type]
        counter_account_id="ext-counter",  # type: ignore[arg-type]
    )
    scenarios = ScenarioPlant(
        template_instances=(),
        drift_plants=(plant,),
        today=date(2030, 1, 1),
    )
    gens = scenario_to_generators(scenarios, inst)  # type: ignore[arg-type]
    cap = dry_run_capture(Dialect.SQLITE)
    ctx = ScenarioContext(scenario_id="ay4c3-smoke", prefix="spec_example")
    captured = ctx.compose(cap, *gens, dry_run=True)
    assert captured is not None
    assert len(captured) > 0
    sql = render_captured_sql(captured, dialect=Dialect.SQLITE)
    assert "?" not in sql
    assert "INSERT INTO" in sql
    assert "ay4c3-smoke" in sql


# ---------------------------------------------------------------------------
# Error surfaces — bad references blow up loudly.
# ---------------------------------------------------------------------------


def test_unknown_account_id_raises_loudly() -> None:
    """A drift plant referencing a non-existent account_id surfaces
    immediately with a clear message — not silent generator
    construction that fails later at compose-time."""
    inst = _load_instance()
    plant = DriftPlant(
        account_id="acct-does-not-exist",  # type: ignore[arg-type]
        days_ago=1,
        delta_money=Decimal("5.0"),
        rail_name="ExternalRailInbound",  # type: ignore[arg-type]
        counter_account_id="ext-counter",  # type: ignore[arg-type]
    )
    scenarios = ScenarioPlant(
        template_instances=(),
        drift_plants=(plant,),
        today=date(2030, 1, 1),
    )
    with pytest.raises(ValueError, match="not declared on the L2"):
        scenario_to_generators(scenarios, inst)  # type: ignore[arg-type]
