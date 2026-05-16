"""Per-primitive end-to-end pipeline tests (M.1.6).

For each L2 primitive shape, walk a minimal-but-valid YAML through the
full pipeline — ``load_instance`` → ``validate`` → ``emit_schema`` — and
assert the primitive is reachable + the schema is well-formed. This
catches integration breakage that per-layer tests miss (e.g. a loader
that produces a primitive the validator can't accept, or a primitive
shape the schema can't represent).

Each test corresponds to one of the SPEC's "Worked example shapes" so
this file doubles as executable SPEC documentation: if a SPEC snippet
stops parsing/validating, the named test fires.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from quicksight_gen.common.l2 import (
    SingleLegRail,
    TwoLegRail,
    emit_schema,
    load_instance,
    validate,
)


def _pipeline(yaml_text: str, tmp_path: Path, *, prefix: str):
    """load → validate → emit_schema; return (instance, sql) on success."""
    p = tmp_path / "inst.yaml"
    p.write_text(yaml_text)
    inst = load_instance(p)
    validate(inst)
    sql = emit_schema(inst, prefix=prefix)
    return inst, sql


# -- Per-primitive shapes ----------------------------------------------------


def test_pipeline_singleton_account(tmp_path: Path) -> None:
    """One singleton Account with every optional field set."""
    inst, sql = _pipeline(dedent("""\
        accounts:
          - id: clearing-suspense
            name: Clearing Suspense
            role: ClearingSuspense
            scope: internal
            expected_eod_balance: 0
        rails: []
        """), tmp_path, prefix="t1")
    a = inst.accounts[0]
    assert a.id == "clearing-suspense"
    assert a.role == "ClearingSuspense"
    assert a.expected_eod_balance == 0
    assert "CREATE TABLE t1_transactions" in sql


def test_pipeline_account_template(tmp_path: Path) -> None:
    """AccountTemplate + the singleton Account it parents under."""
    inst, sql = _pipeline(dedent("""\
        accounts:
          - id: gl-control
            role: ControlAccount
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: ControlAccount
        rails: []
        """), tmp_path, prefix="t2")
    assert len(inst.account_templates) == 1
    assert inst.account_templates[0].role == "CustomerSubledger"
    assert inst.account_templates[0].parent_role == "ControlAccount"


def test_pipeline_two_leg_standalone_rail(tmp_path: Path) -> None:
    """Standalone two-leg Rail with expected_net=0 and metadata keys."""
    inst, _ = _pipeline(dedent("""\
        accounts:
          - id: a-int
            role: InternalDDA
            scope: internal
          - id: a-ext
            role: ExternalCounterparty
            scope: external
        rails:
          - name: ExternalRailInbound
            source_role: ExternalCounterparty
            destination_role: InternalDDA
            expected_net: 0
            origin: ExternalForcePosted
            metadata_keys: [external_reference, originator_id]
        """), tmp_path, prefix="t3")
    rail = inst.rails[0]
    assert isinstance(rail, TwoLegRail)
    assert rail.metadata_keys == ("external_reference", "originator_id")
    assert rail.expected_net == 0


def test_pipeline_single_leg_rail_in_transfer_template(tmp_path: Path) -> None:
    """Single-leg rail reconciled by being in a TransferTemplate.leg_rails."""
    inst, _ = _pipeline(dedent("""\
        accounts:
          - id: gl
            role: ControlAccount
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: ControlAccount
        rails:
          - name: SubledgerCharge
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [merchant_id, settlement_period]
        transfer_templates:
          - name: ChargeCycle
            expected_net: 0
            transfer_key: [merchant_id, settlement_period]
            completion: business_day_end+3d
            leg_rails: [SubledgerCharge]
        """), tmp_path, prefix="t4")
    rail = inst.rails[0]
    assert isinstance(rail, SingleLegRail)
    assert rail.leg_direction == "Debit"


def test_pipeline_single_leg_variable_direction_rail(tmp_path: Path) -> None:
    """Variable-direction closing leg of a TransferTemplate."""
    inst, _ = _pipeline(dedent("""\
        accounts:
          - id: gl
            role: MerchantLedger
            scope: internal
          - id: cust
            role: CustomerSubledger
            scope: internal
            parent_role: MerchantLedger
        rails:
          - name: SubledgerCharge
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [merchant_id, settlement_period]
          - name: SettlementClose
            leg_role: MerchantLedger
            leg_direction: Variable
            origin: InternalInitiated
            metadata_keys: [merchant_id, settlement_period]
        transfer_templates:
          - name: MerchantSettlementCycle
            expected_net: 0
            transfer_key: [merchant_id, settlement_period]
            completion: month_end
            leg_rails: [SubledgerCharge, SettlementClose]
        """), tmp_path, prefix="t5")
    settlement = next(r for r in inst.rails if r.name == "SettlementClose")
    assert isinstance(settlement, SingleLegRail)
    assert settlement.leg_direction == "Variable"


def test_pipeline_aggregating_rail(tmp_path: Path) -> None:
    """Aggregating two-leg rail with cadence + bundles_activity."""
    inst, _ = _pipeline(dedent("""\
        accounts:
          - id: north
            role: NorthPool
            scope: internal
          - id: south
            role: SouthPool
            scope: internal
        rails:
          - name: ChargeRail
            leg_role: NorthPool
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: []
          - name: PoolBalancingNorthToSouth
            source_role: NorthPool
            destination_role: SouthPool
            expected_net: 0
            origin: InternalInitiated
            metadata_keys: [bundled_transfer_type, business_day]
            aggregating: true
            bundles_activity: [ChargeRail]
            cadence: intraday-2h
        """), tmp_path, prefix="t6")
    pool = next(r for r in inst.rails if r.name == "PoolBalancingNorthToSouth")
    assert isinstance(pool, TwoLegRail)
    assert pool.aggregating is True
    assert pool.cadence == "intraday-2h"
    assert pool.bundles_activity == ("ChargeRail",)


@pytest.mark.parametrize("completion_expr", [
    "business_day_end",
    "business_day_end+1d",
    "month_end",
    "metadata.deadline",
])
def test_pipeline_transfer_template_each_completion_form(
    completion_expr: str, tmp_path: Path,
) -> None:
    """Every CompletionExpression vocabulary form parses + validates."""
    inst, _ = _pipeline(dedent(f"""\
        accounts:
          - id: a
            role: A
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: A
        rails:
          - name: ChargeLeg
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [merchant_id, deadline]
        transfer_templates:
          - name: T
            expected_net: 0
            transfer_key: [merchant_id]
            completion: {completion_expr}
            leg_rails: [ChargeLeg]
        """), tmp_path, prefix="t7")
    assert inst.transfer_templates[0].completion == completion_expr


def test_pipeline_chain_xor_group(tmp_path: Path) -> None:
    """Z.A: a multi-children Chain row encodes XOR alternation —
    three children listed under one parent in a single row."""
    inst, _ = _pipeline(dedent("""\
        accounts:
          - id: m
            role: MerchantLedger
            scope: internal
          - id: e
            role: ExternalCounterparty
            scope: external
        rails:
          - name: SettlementClose
            leg_role: MerchantLedger
            leg_direction: Variable
            origin: InternalInitiated
            metadata_keys: [merchant_id]
          - name: PayoutACH
            source_role: MerchantLedger
            destination_role: ExternalCounterparty
            expected_net: 0
            origin: InternalInitiated
            metadata_keys: [merchant_id]
          - name: PayoutVoucher
            source_role: MerchantLedger
            destination_role: ExternalCounterparty
            expected_net: 0
            origin: InternalInitiated
            metadata_keys: [merchant_id]
          - name: PayoutInternal
            source_role: MerchantLedger
            destination_role: MerchantLedger
            expected_net: 0
            origin: InternalInitiated
            metadata_keys: [merchant_id]
        transfer_templates:
          - name: SettlementCycle
            expected_net: 0
            transfer_key: [merchant_id]
            completion: business_day_end
            leg_rails: [SettlementClose]
        chains:
          - parent: SettlementCycle
            children:
              - PayoutACH
              - PayoutVoucher
              - PayoutInternal
        """), tmp_path, prefix="t8")
    assert len(inst.chains) == 1
    chain = inst.chains[0]
    assert chain.parent == "SettlementCycle"
    assert set(chain.children) == {"PayoutACH", "PayoutVoucher", "PayoutInternal"}


def test_pipeline_chain_fan_out(tmp_path: Path) -> None:
    """Z.A: singleton-children Chain row = required parent→child link."""
    inst, _ = _pipeline(dedent("""\
        accounts:
          - id: pool
            role: Pool
            scope: internal
          - id: ext
            role: ExternalCounterparty
            scope: external
        rails:
          - name: BatchInbound
            source_role: ExternalCounterparty
            destination_role: Pool
            expected_net: 0
            origin: ExternalForcePosted
            metadata_keys: []
          - name: PerRecipientCredit
            source_role: Pool
            destination_role: Pool
            expected_net: 0
            origin: InternalInitiated
            metadata_keys: []
        chains:
          - parent: BatchInbound
            children:
              - PerRecipientCredit
        """), tmp_path, prefix="t9")
    chain = inst.chains[0]
    assert chain.children == ("PerRecipientCredit",)
    assert len(chain.children) == 1  # singleton = required semantics


def test_pipeline_limit_schedule(tmp_path: Path) -> None:
    """LimitSchedule with parent_role + rail + cap."""
    inst, _ = _pipeline(dedent("""\
        accounts:
          - id: north
            role: NorthPool
            scope: internal
          - id: child
            role: ChildPool
            scope: internal
            parent_role: NorthPool
        rails:
          # R10: every LimitSchedule.rail must match some Rail.
          - name: ChildAch
            origin: InternalInitiated
            leg_role: ChildPool
            leg_direction: Debit
            metadata_keys: [batch_id]
        transfer_templates:
          # S3: single-leg ChildAch needs reconciliation.
          - name: AchCycle
            expected_net: 0
            transfer_key: [batch_id]
            completion: business_day_end
            leg_rails: [ChildAch]
        limit_schedules:
          - parent_role: NorthPool
            rail: ChildAch
            cap: 5000.00
        """), tmp_path, prefix="t10")
    ls = inst.limit_schedules[0]
    assert ls.parent_role == "NorthPool"
    assert ls.cap == 5000


# -- Kitchen-sink fixture (M.1.8) --------------------------------------------


KITCHEN_YAML = Path(__file__).parent.parent / "l2" / "_kitchen.yaml"


def test_kitchen_sink_loads_validates_emits() -> None:
    """The fixture YAML walks the full pipeline cleanly.

    Per M.1.8: this is the regression harness — if any primitive shape
    stops being supported by the loader / validator / emitter, this test
    fires. The fixture covers every primitive shape + every variant flag
    SPEC v1 declares.
    """
    inst = load_instance(KITCHEN_YAML)
    validate(inst)
    sql = emit_schema(inst, prefix="kitchen")
    assert "CREATE TABLE kitchen_transactions" in sql
    assert "CREATE MATERIALIZED VIEW kitchen_current_transactions AS" in sql


def test_drift_matview_carries_v840_perf_indexes() -> None:
    """v8.4.0 — date-leading composite index on _drift + _ledger_drift
    closes the L1 Drift account/role dropdown spin.

    Pre-v8.4.0 the existing indexes were leading on ``account_id``
    (one composite + one role-only). QuickSight's date-narrowed
    queries off the universal date filter could only use them by
    full-index-scanning ``account_id`` first, then filtering. On a
    large matview this became visible as a spinning dropdown.

    The new ``idx_<prefix>_drift_day_account_role`` is a date-leading
    composite covering ``(business_day_start, account_id,
    account_role)`` so date-range scans become trivial. Same on
    ``_ledger_drift`` for parity with the same query class.

    Snapshot regression test: if a future schema refactor drops the
    index, this test fails loudly with the matview name. Existing
    indexes (``account_day``, ``role``) are also asserted so a
    refactor doesn't accidentally narrow the index footprint."""
    inst = load_instance(KITCHEN_YAML)
    sql = emit_schema(inst, prefix="kitchen")
    p = "kitchen"

    # Drift matview indexes — pre-v8.4.0 indexes still present.
    assert (
        f"CREATE INDEX idx_{p}_drift_account_day\n"
        f"    ON {p}_drift (account_id, business_day_start);"
    ) in sql
    assert (
        f"CREATE INDEX idx_{p}_drift_role ON {p}_drift (account_role);"
    ) in sql
    # v8.4.0 — new date-leading composite.
    assert (
        f"CREATE INDEX idx_{p}_drift_day_account_role\n"
        f"    ON {p}_drift (business_day_start, account_id, account_role);"
    ) in sql

    # Ledger drift matview — same pre-v8.4.0 + new index parity.
    assert (
        f"CREATE INDEX idx_{p}_ledger_drift_account_day\n"
        f"    ON {p}_ledger_drift (account_id, business_day_start);"
    ) in sql
    assert (
        f"CREATE INDEX idx_{p}_ledger_drift_role\n"
        f"    ON {p}_ledger_drift (account_role);"
    ) in sql
    # v8.4.0 — new date-leading composite, parity with _drift.
    assert (
        f"CREATE INDEX idx_{p}_ledger_drift_day_account_role\n"
        f"    ON {p}_ledger_drift (business_day_start, account_id, account_role);"
    ) in sql


def test_current_transactions_matview_carries_v856_perf_index() -> None:
    """v8.5.6 — date-leading composite index on ``_current_transactions``
    closes the L1 Transactions ``transfer_type`` filter dropdown spin.

    Pre-v8.5.6 the existing matview indexes were leading on
    ``account_id`` / ``transfer_id`` / ``id`` / ``status`` — none
    covered ``WHERE posting BETWEEN x AND y`` followed by
    ``SELECT DISTINCT transfer_type``. QuickSight had to full-scan
    the matview every time the Transactions sheet's transfer_type
    dropdown opened with a date-narrowed window in effect.

    The new ``idx_<prefix>_curr_tx_posting_transfer_type`` is a
    date-leading composite covering ``(posting, transfer_type)`` so
    QS can index-only-scan the date range and return the distinct
    transfer_types directly. Mirrors the v8.4.0 Drift dropdown fix.

    Snapshot regression test: if a future schema refactor drops the
    index, this test fails loudly with the matview name. Existing
    indexes are also re-asserted so a refactor doesn't accidentally
    narrow the index footprint."""
    inst = load_instance(KITCHEN_YAML)
    sql = emit_schema(inst, prefix="kitchen")
    p = "kitchen"

    # Pre-v8.5.6 indexes still present.
    assert (
        f"CREATE INDEX idx_{p}_curr_tx_account_posting\n"
        f"    ON {p}_current_transactions (account_id, posting);"
    ) in sql
    assert (
        f"CREATE INDEX idx_{p}_curr_tx_transfer "
        f"ON {p}_current_transactions (transfer_id);"
    ) in sql
    assert (
        f"CREATE INDEX idx_{p}_curr_tx_id "
        f"ON {p}_current_transactions (id);"
    ) in sql
    assert (
        f"CREATE INDEX idx_{p}_curr_tx_status "
        f"ON {p}_current_transactions (status);"
    ) in sql
    # v8.5.6 — new date-leading composite for the rail_name
    # dropdown (formerly transfer_type pre-Z.B grammar collapse).
    assert (
        f"CREATE INDEX idx_{p}_curr_tx_posting_rail_name\n"
        f"    ON {p}_current_transactions (posting, rail_name);"
    ) in sql


def test_kitchen_sink_covers_every_primitive_kind() -> None:
    """Coverage gate: every primitive type + every important variant present.

    If a new primitive kind is added to the SPEC, extending it here
    enforces that the kitchen fixture is updated to exercise it.
    """
    inst = load_instance(KITCHEN_YAML)

    # Every entity bucket non-empty
    assert inst.accounts, "kitchen fixture missing singleton accounts"
    assert inst.account_templates, "kitchen fixture missing AccountTemplates"
    assert inst.rails, "kitchen fixture missing Rails"
    assert inst.transfer_templates, "kitchen fixture missing TransferTemplates"
    assert inst.chains, "kitchen fixture missing Chains"
    assert inst.limit_schedules, "kitchen fixture missing LimitSchedules"

    # Both rail shapes present
    two_legs = [r for r in inst.rails if isinstance(r, TwoLegRail)]
    single_legs = [r for r in inst.rails if isinstance(r, SingleLegRail)]
    assert two_legs, "kitchen fixture missing TwoLegRail"
    assert single_legs, "kitchen fixture missing SingleLegRail"

    # Aggregating variant on both rail shapes
    assert any(r.aggregating for r in two_legs), \
        "kitchen fixture missing aggregating TwoLegRail"
    assert any(r.aggregating for r in single_legs), \
        "kitchen fixture missing aggregating SingleLegRail"

    # Variable-direction leg present
    assert any(
        isinstance(r, SingleLegRail) and r.leg_direction == "Variable"
        for r in inst.rails
    ), "kitchen fixture missing Variable-direction leg"

    # Union role (RoleA | RoleB) present
    assert any(
        isinstance(r, TwoLegRail) and len(r.source_role) > 1
        for r in inst.rails
    ), "kitchen fixture missing union role on a Rail"

    # Standalone two-leg (with expected_net) AND template-leg two-leg (without).
    assert any(
        isinstance(r, TwoLegRail) and r.expected_net is not None
        for r in inst.rails
    ), "kitchen fixture missing standalone TwoLegRail"
    assert any(
        isinstance(r, TwoLegRail) and r.expected_net is None
        for r in inst.rails
    ), "kitchen fixture missing template-leg TwoLegRail (no expected_net)"

    # Multiple Completion vocabulary forms across templates.
    completion_forms = {t.completion for t in inst.transfer_templates}
    assert len(completion_forms) >= 2, \
        "kitchen fixture should exercise >1 Completion form"

    # Z.A: both grammar shapes present — singleton-children (required)
    # AND multi-children (XOR alternation).
    assert any(len(c.children) >= 2 for c in inst.chains), \
        "kitchen fixture missing a multi-children (XOR) Chain row"
    assert any(len(c.children) == 1 for c in inst.chains), \
        "kitchen fixture missing a singleton-children (required) Chain row"

    # N.1.i — inline brand theme exercises the loader's _load_theme path.
    assert inst.theme is not None, \
        "kitchen fixture missing inline theme block"
    assert inst.theme.theme_name == "Kitchen Sink Theme"
    assert inst.theme.accent.startswith("#"), \
        "kitchen theme.accent must be a hex color"


def test_pipeline_full_merchant_acquirer_end_to_end(tmp_path: Path) -> None:
    """SPEC's end-to-end merchant-acquirer example through the full pipeline.

    This is the kitchen-sink shape — every primitive in one declaration.
    Lifted from SPEC.md's "End-to-end: a complete merchant-acquiring
    instance" worked example with light adaptations for self-containment.
    If this stops working, the SPEC's example needs updating too.
    """
    inst, sql = _pipeline(dedent("""\
        accounts:
          - id: north-pool
            role: NorthPool
            scope: internal
          - id: south-pool
            role: SouthPool
            scope: internal
          - id: clearing-suspense
            role: ClearingSuspense
            scope: internal
            expected_eod_balance: 0
          - id: ext-counter
            role: ExternalCounterparty
            scope: external

        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: SouthPool
          - role: MerchantLedger
            scope: internal
            parent_role: NorthPool

        rails:
          - name: SubledgerCharge
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [merchant_id, customer_id, settlement_period, settlement_period_end]

          - name: SubledgerRefund
            leg_role: CustomerSubledger
            leg_direction: Credit
            origin: InternalInitiated
            metadata_keys: [merchant_id, customer_id, settlement_period, settlement_period_end]

          - name: SettlementClose
            leg_role: MerchantLedger
            leg_direction: Variable
            origin: InternalInitiated
            metadata_keys: [merchant_id, settlement_period, settlement_period_end]

          - name: MerchantPayoutACH
            source_role: MerchantLedger
            destination_role: ExternalCounterparty
            expected_net: 0
            origin: InternalInitiated
            metadata_keys: [merchant_id, settlement_period]

          - name: PoolBalancingSouthToNorth
            source_role: SouthPool
            destination_role: NorthPool
            expected_net: 0
            origin: InternalInitiated
            metadata_keys: [bundled_transfer_type, business_day]
            aggregating: true
            bundles_activity: [SubledgerCharge, SubledgerRefund, SettlementClose]
            cadence: intraday-2h

        transfer_templates:
          - name: MerchantSettlementCycle
            expected_net: 0
            transfer_key: [merchant_id, settlement_period]
            completion: metadata.settlement_period_end
            leg_rails: [SubledgerCharge, SubledgerRefund, SettlementClose]

        chains:
          - parent: MerchantSettlementCycle
            children:
              - MerchantPayoutACH

        limit_schedules:
          - parent_role: SouthPool
            rail: SubledgerCharge
            cap: 5000.00
        """), tmp_path, prefix="ex_acq")

    # Every primitive present
    assert len(inst.accounts) == 4
    assert len(inst.account_templates) == 2
    assert len(inst.rails) == 5
    assert len(inst.transfer_templates) == 1
    assert len(inst.chains) == 1
    assert len(inst.limit_schedules) == 1

    # Both rail shapes
    assert sum(1 for r in inst.rails if isinstance(r, TwoLegRail)) == 2
    assert sum(1 for r in inst.rails if isinstance(r, SingleLegRail)) == 3

    # Aggregating rail present + correctly flagged
    pool = next(r for r in inst.rails if r.name == "PoolBalancingSouthToNorth")
    assert pool.aggregating is True

    # Variable-direction closing leg
    settlement = next(r for r in inst.rails if r.name == "SettlementClose")
    assert isinstance(settlement, SingleLegRail)
    assert settlement.leg_direction == "Variable"

    # Schema includes the prefix and Current* views
    assert "CREATE TABLE ex_acq_transactions" in sql
    assert "CREATE TABLE ex_acq_daily_balances" in sql
    assert "CREATE MATERIALIZED VIEW ex_acq_current_transactions AS" in sql
    assert "CREATE MATERIALIZED VIEW ex_acq_current_daily_balances AS" in sql
