"""Loader tests for ``common.l2.load_instance``.

Coverage split:
- Inline kitchen-sink YAML covering every primitive shape — including
  AccountTemplate, both rail shapes, aggregating rails, TransferTemplate,
  Chain with multi-children (XOR), LimitSchedule.
- Per-helper rejection tests: F4 Money coercion, F5 InstancePrefix regex
  + length cap, Rail discrimination, missing-required-field paths.

Every load-time validator gets a rejection test in this file. Cross-entity
validation (singleton-ParentRole, ≤1 Variable leg, vocabulary literals)
lives in ``test_l2_validate.py``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from recon_gen.common.l2 import (
    L2LoaderError,
    L2ValidationError,
    SingleLegRail,
    TwoLegRail,
    load_instance,
)
from recon_gen.common.env_keys import RECON_GEN_RUN_DIR


# -- Happy paths --------------------------------------------------------------


def test_loads_kitchen_sink_yaml_inline(tmp_path: Path) -> None:
    """Every primitive at least once + aggregating + xor + union role."""
    yaml_text = dedent("""\
        accounts:
          - id: gl-control
            name: Control Account
            role: ControlAccount
            scope: internal
            expected_eod_balance: 0

          - id: ext-counter
            role: ExternalCounterparty
            scope: external

        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: ControlAccount

          - role: MerchantLedger
            scope: internal
            parent_role: ControlAccount
            expected_eod_balance: 100.50

        rails:
          - name: SubledgerCharge
            origin: InternalInitiated
            metadata_keys: [merchant_id, settlement_period]
            leg_role: CustomerSubledger
            leg_direction: Debit

          - name: ExtInbound
            origin: ExternalForcePosted
            metadata_keys: [external_reference]
            source_role: [ExternalCounterparty, MerchantLedger]
            destination_role: ControlAccount
            expected_net: 0

          - name: PoolBalancing
            origin: InternalInitiated
            metadata_keys: []
            source_role: ControlAccount
            destination_role: ControlAccount
            expected_net: 0
            aggregating: true
            bundles_activity: [SubledgerCharge, ExtInbound]
            cadence: intraday-2h

        transfer_templates:
          - name: MerchantSettlementCycle
            expected_net: 0
            transfer_key: [merchant_id, settlement_period]
            completion: metadata.settlement_period_end
            leg_rails: [SubledgerCharge]

        chains:
          - parent: MerchantSettlementCycle
            children:
              - MerchantPayoutACH
              - MerchantPayoutWire

        limit_schedules:
          - parent_role: ControlAccount
            rail: ExtInbound
            cap: 5000.00
        """)
    p = tmp_path / "kitchen.yaml"
    p.write_text(yaml_text)

    # validate=False: this fixture is intentionally partial — the chain
    # references a template not declared in transfer_templates so the
    # cross-entity validator (M.2d.2) would reject it. Loader-only test.
    inst = load_instance(p, validate=False)
    assert len(inst.accounts) == 2
    assert len(inst.account_templates) == 2
    assert len(inst.rails) == 3
    assert len(inst.transfer_templates) == 1
    assert len(inst.chains) == 1
    assert len(inst.limit_schedules) == 1

    # Single-leg + two-leg discrimination
    by_name = {r.name: r for r in inst.rails}
    assert isinstance(by_name["SubledgerCharge"], SingleLegRail)
    assert isinstance(by_name["ExtInbound"], TwoLegRail)
    assert isinstance(by_name["PoolBalancing"], TwoLegRail)

    # Union role → tuple of identifiers
    ext_inbound = by_name["ExtInbound"]
    assert isinstance(ext_inbound, TwoLegRail)
    assert ext_inbound.source_role == ("ExternalCounterparty", "MerchantLedger")
    # Single-string role → 1-tuple
    assert ext_inbound.destination_role == ("ControlAccount",)

    # Aggregating fields populated correctly
    pool = by_name["PoolBalancing"]
    assert isinstance(pool, TwoLegRail)
    assert pool.aggregating is True
    assert pool.bundles_activity == ("SubledgerCharge", "ExtInbound")
    assert pool.cadence == "intraday-2h"

    # Money coercion
    template = inst.transfer_templates[0]
    assert template.expected_net == Decimal("0")
    assert inst.limit_schedules[0].cap == Decimal("5000.00")

    # Z.A: chain rows carry parent + a children tuple. The above
    # fixture is multi-children (XOR semantics).
    chain = inst.chains[0]
    assert chain.parent == "MerchantSettlementCycle"
    assert chain.children == ("MerchantPayoutACH", "MerchantPayoutWire")

    # AccountTemplate Money coercion (the float-precision case for F4)
    merchant_tmpl = inst.account_templates[1]
    assert merchant_tmpl.expected_eod_balance == Decimal("100.50")


# -- F4 Money coercion --------------------------------------------------------


def test_money_coercion_dodges_float_precision(tmp_path: Path) -> None:
    """Per F4: Decimal(str(value)) instead of Decimal(value) — preserves '0.1'."""
    yaml_text = dedent("""\
        accounts:
          - id: a1
            scope: internal
            role: A
            expected_eod_balance: 0.1
        rails:
          - name: R
            origin: o
            source_role: A
            destination_role: A
            expected_net: 0.1
        """)
    p = tmp_path / "money.yaml"
    p.write_text(yaml_text)

    inst = load_instance(p)
    assert inst.accounts[0].expected_eod_balance == Decimal("0.1")
    rail = inst.rails[0]
    assert isinstance(rail, TwoLegRail)
    assert rail.expected_net == Decimal("0.1")
    # The naive Decimal(0.1) would yield '0.1000000000000000055...'; F4
    # explicitly avoids that by going through str().
    assert str(rail.expected_net) == "0.1"


def test_money_rejects_non_numeric(tmp_path: Path) -> None:
    """Money fields reject obvious garbage."""
    yaml_text = dedent("""\
        accounts:
          - id: a1
            scope: internal
            expected_eod_balance: "not a number"
        rails: []
        """)
    p = tmp_path / "bad_money.yaml"
    p.write_text(yaml_text)
    with pytest.raises(L2LoaderError, match="not a valid decimal"):
        load_instance(p)


# -- F5 InstancePrefix regex + length cap -------------------------------------
#
# The F5 regex + 30-char cap moved to ``cfg.db_table_prefix`` (Z.C,
# 2026-05-15). Coverage lives in ``tests/unit/test_config_loader.py``.
# The L2 yaml side no longer carries an ``instance:`` field at all —
# loader rejection coverage lives in ``test_legacy_instance_key_rejected``.


# -- Rail discrimination ------------------------------------------------------


def test_rail_rejects_both_two_leg_and_single_leg(tmp_path: Path) -> None:
    """A Rail cannot declare both shape's fields."""
    yaml_text = dedent("""\
        accounts:
          - id: a
            scope: internal
            role: R
        rails:
          - name: BadRail
            origin: o
            source_role: R
            destination_role: R
            expected_net: 0
            leg_role: R
            leg_direction: Debit
        """)
    p = tmp_path / "both.yaml"
    p.write_text(yaml_text)
    with pytest.raises(L2LoaderError, match="not both"):
        load_instance(p)


def test_rail_rejects_neither_shape(tmp_path: Path) -> None:
    """A Rail must declare at least one shape."""
    yaml_text = dedent("""\
        accounts: []
        rails:
          - name: BadRail
            origin: o
        """)
    p = tmp_path / "neither.yaml"
    p.write_text(yaml_text)
    with pytest.raises(L2LoaderError, match="EITHER two-leg .* OR single-leg"):
        load_instance(p)


def test_two_leg_rail_requires_both_role_fields(tmp_path: Path) -> None:
    """Source-only or destination-only is rejected."""
    yaml_text = dedent("""\
        accounts: []
        rails:
          - name: BadRail
            origin: o
            source_role: R
            expected_net: 0
        """)
    p = tmp_path / "src_only.yaml"
    p.write_text(yaml_text)
    with pytest.raises(L2LoaderError, match="both source_role and destination_role"):
        load_instance(p)


# -- Top-level shape ---------------------------------------------------------


def test_empty_yaml_rejected(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("")
    with pytest.raises(L2LoaderError, match="file is empty"):
        load_instance(p)


def test_malformed_yaml_rejected(tmp_path: Path) -> None:
    p = tmp_path / "malformed.yaml"
    p.write_text("instance: spk\n  accounts: [oops bad indent\n")
    with pytest.raises(L2LoaderError, match="YAML syntax error"):
        load_instance(p)


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- not\n- a\n- mapping\n")
    with pytest.raises(L2LoaderError, match="top-level must be a mapping"):
        load_instance(p)


def test_missing_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(L2LoaderError, match="could not read"):
        load_instance(tmp_path / "nonexistent.yaml")


# -- M.1a.2 — per-leg Origin / PostedRequirements / aging / Duration --------


def _write_rail_yaml(tmp_path: Path, body: str) -> Path:
    """Helper: dump a minimal L2 instance with the given rail body."""
    p = tmp_path / "instance.yaml"
    p.write_text(
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "  - id: ext-001\n"
        "    role: B\n"
        "    scope: external\n"
        "rails:\n"
        + body
    )
    return p


def test_two_leg_rail_loads_per_leg_origin_overrides(tmp_path: Path) -> None:
    """Per-leg origin overrides land as fields on the loaded TwoLegRail."""
    p = _write_rail_yaml(tmp_path, """\
  - name: ExtInbound
    source_role: B
    destination_role: A
    expected_net: 0
    source_origin: ExternalForcePosted
    destination_origin: InternalInitiated
""")
    inst = load_instance(p)
    rail = inst.rails[0]
    from recon_gen.common.l2 import TwoLegRail
    assert isinstance(rail, TwoLegRail)
    assert rail.origin is None
    assert rail.source_origin == "ExternalForcePosted"
    assert rail.destination_origin == "InternalInitiated"


def test_single_leg_rail_rejects_per_leg_origin_overrides(tmp_path: Path) -> None:
    """Per the M.1a hard-error stance — `source_origin`/`destination_origin`
    on a single-leg rail is a load-time configuration error."""
    p = _write_rail_yaml(tmp_path, """\
  - name: BadSingle
    leg_role: A
    leg_direction: Debit
    origin: InternalInitiated
    source_origin: ExternalForcePosted
""")
    with pytest.raises(
        L2LoaderError,
        match=r"source_origin: per-leg Origin overrides.*two-leg rails",
    ):
        load_instance(p)


def test_single_leg_rail_rejects_destination_origin(tmp_path: Path) -> None:
    p = _write_rail_yaml(tmp_path, """\
  - name: BadSingle
    leg_role: A
    leg_direction: Debit
    origin: InternalInitiated
    destination_origin: InternalInitiated
""")
    with pytest.raises(
        L2LoaderError,
        match=r"destination_origin: per-leg Origin overrides.*two-leg rails",
    ):
        load_instance(p)


def test_two_leg_rail_origin_now_optional(tmp_path: Path) -> None:
    """Per the SPEC's per-leg Origin section: rail-level ``origin`` is
    optional when both per-leg overrides are present (legacy required
    behavior dropped in M.1a)."""
    p = _write_rail_yaml(tmp_path, """\
  - name: ExtInbound
    source_role: B
    destination_role: A
    expected_net: 0
    source_origin: ExternalForcePosted
    destination_origin: InternalInitiated
""")
    # Loads cleanly without a top-level `origin:` key.
    inst = load_instance(p)
    assert inst.rails[0].origin is None


def test_rail_loads_posted_requirements(tmp_path: Path) -> None:
    """Integrator-declared PostedRequirements list loads as a tuple of Identifiers."""
    p = _write_rail_yaml(tmp_path, """\
  - name: ExtRail
    leg_role: A
    leg_direction: Debit
    origin: ExternalForcePosted
    metadata_keys: [external_reference, originator_id]
    posted_requirements: [external_reference]
""")
    # validate=False: rail-only fixture exercises loader-side parsing
    # (a one-rail YAML can't satisfy single-leg reconciliation S3).
    inst = load_instance(p, validate=False)
    assert inst.rails[0].posted_requirements == ("external_reference",)


def test_rail_loads_aging_durations(tmp_path: Path) -> None:
    """ISO 8601 duration literals → datetime.timedelta on the loaded Rail."""
    from datetime import timedelta
    p = _write_rail_yaml(tmp_path, """\
  - name: AgingRail
    leg_role: A
    leg_direction: Debit
    origin: InternalInitiated
    max_pending_age: PT24H
    max_unbundled_age: PT4H
""")
    # validate=False: rail-only fixture (R8 would reject max_unbundled_age
    # without an aggregating rail bundling this one).
    inst = load_instance(p, validate=False)
    rail = inst.rails[0]
    assert rail.max_pending_age == timedelta(hours=24)
    assert rail.max_unbundled_age == timedelta(hours=4)


@pytest.mark.parametrize("literal,expected", [
    ("PT24H", "hours=24"),
    ("PT4H", "hours=4"),
    ("PT30M", "minutes=30"),
    ("PT15S", "seconds=15"),
    ("P1D", "days=1"),
    ("P7D", "days=7"),
    ("P1DT12H", "days=1, hours=12"),
    ("P2DT6H30M", "days=2, hours=6, minutes=30"),
])
def test_duration_literal_accepted(
    literal: str, expected: str, tmp_path: Path,
) -> None:
    """Every SPEC-shaped duration literal parses; expected is a docstring
    that names the components (used to make the parametrize labels readable)."""
    p = _write_rail_yaml(tmp_path, f"""\
  - name: R
    leg_role: A
    leg_direction: Debit
    origin: InternalInitiated
    max_pending_age: {literal}
""")
    # Parses without raising. The exact timedelta-arithmetic equivalence
    # is covered by test_rail_loads_aging_durations above.
    # validate=False: rail-only fixture, S3 reconciliation not satisfied.
    load_instance(p, validate=False)
    assert expected  # silence the unused-arg lint (the parametrize label IS the value)


@pytest.mark.parametrize("bad", [
    "P1Y",        # years not supported (no fixed timedelta)
    "P1M",        # months not supported (no fixed timedelta)
    "PT",         # empty time component
    "P",          # empty date component
    "24H",        # missing the leading "PT"
    "PT-1H",     # negative
    "1 day",     # natural language
    "",           # empty string
])
def test_duration_literal_rejected(bad: str, tmp_path: Path) -> None:
    p = _write_rail_yaml(tmp_path, f"""\
  - name: R
    leg_role: A
    leg_direction: Debit
    origin: InternalInitiated
    max_pending_age: '{bad}'
""")
    with pytest.raises(L2LoaderError, match="ISO 8601 duration"):
        load_instance(p)


def test_duration_rejects_non_string(tmp_path: Path) -> None:
    """A YAML numeric like ``86400`` is a plausible mistake — explicitly reject."""
    p = _write_rail_yaml(tmp_path, """\
  - name: R
    leg_role: A
    leg_direction: Debit
    origin: InternalInitiated
    max_pending_age: 86400
""")
    with pytest.raises(L2LoaderError, match="expected ISO 8601 duration string"):
        load_instance(p)


# -- Z.B (2026-05-15) legacy `transfer_type:` rejection ----------------------


def test_rail_rejects_legacy_transfer_type_key(tmp_path: Path) -> None:
    """Z.B: rail's `name` IS the type — `transfer_type:` is a load-time error."""
    p = _write_rail_yaml(tmp_path, """\
  - name: R
    transfer_type: charge
    leg_role: A
    leg_direction: Debit
    origin: InternalInitiated
""")
    with pytest.raises(
        L2LoaderError,
        match=r"transfer_type: legacy field no longer supported.*Z\.B",
    ):
        load_instance(p, validate=False)


def test_transfer_template_rejects_legacy_transfer_type_key(tmp_path: Path) -> None:
    """Z.B: TransferTemplate's `name` IS the type identifier."""
    yaml_text = dedent("""\
        accounts:
          - id: gl-1
            role: Control
            scope: internal

        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: Control
            leg_direction: Debit

        transfer_templates:
          - name: ChargeBatch
            transfer_type: charge
            expected_net: 0
            transfer_key: [batch_id]
            completion: business_day_end
            leg_rails: [SubCharge]
        """)
    p = tmp_path / "tt_legacy.yaml"
    p.write_text(yaml_text)
    with pytest.raises(
        L2LoaderError,
        match=r"transfer_type: legacy field no longer supported.*Z\.B",
    ):
        load_instance(p, validate=False)


def test_limit_schedule_rejects_legacy_transfer_type_key(tmp_path: Path) -> None:
    """Z.B: LimitSchedule's discriminator renamed `transfer_type` → `rail`."""
    yaml_text = dedent("""\
        accounts:
          - id: gl-1
            role: Control
            scope: internal

        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: Control
            leg_direction: Debit

        limit_schedules:
          - parent_role: Control
            transfer_type: charge
            cap: 1000.00
        """)
    p = tmp_path / "ls_legacy.yaml"
    p.write_text(yaml_text)
    with pytest.raises(
        L2LoaderError,
        match=r"transfer_type: legacy field renamed to `rail`.*Z\.B",
    ):
        load_instance(p, validate=False)


# -- M.2d.2 parse-time validation enforcement -------------------------------


def test_load_instance_runs_validate_by_default(tmp_path: Path) -> None:
    """``load_instance(p)`` runs the cross-entity validator before returning.

    Per M.2d.2 — every SHOULD-constraint in the SPEC's Validation Rules
    section is a YAML parse-time error. The fixture below has duplicate
    LimitSchedule (parent_role, rail) which violates U5; the loader
    MUST raise on it without the caller having to know to call
    ``validate()`` separately.
    """
    yaml_text = dedent("""\
        accounts:
          - id: gl-1
            role: Control
            scope: internal
            expected_eod_balance: 0

        account_templates:
          - role: Sub
            scope: internal
            parent_role: Control

        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: Sub
            leg_direction: Debit

        transfer_templates:
          - name: ChargeBatch
            expected_net: 0
            transfer_key: [batch_id]
            completion: business_day_end
            leg_rails: [SubCharge]

        limit_schedules:
          - parent_role: Control
            rail: SubCharge
            cap: 1000.00
          - parent_role: Control
            rail: SubCharge
            cap: 5000.00
        """)
    p = tmp_path / "dup_limit.yaml"
    p.write_text(yaml_text)

    with pytest.raises(L2ValidationError, match="duplicate"):
        load_instance(p)


def test_load_instance_validate_false_skips_cross_entity_pass(
    tmp_path: Path,
) -> None:
    """``validate=False`` opts out of cross-entity validation.

    The same overlap fixture as the test above loads cleanly when
    ``validate=False`` is passed — proves the kwarg actually disables
    the validator pass (and not just the U5 check). Useful for narrow
    loader tests that intentionally exercise partial fixtures.
    """
    yaml_text = dedent("""\
        accounts:
          - id: gl-1
            role: Control
            scope: internal

        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: Control
            leg_direction: Debit

        limit_schedules:
          - parent_role: Control
            rail: SubCharge
            cap: 1000.00
          - parent_role: Control
            rail: SubCharge
            cap: 5000.00
        """)
    p = tmp_path / "dup_limit_skip.yaml"
    p.write_text(yaml_text)

    inst = load_instance(p, validate=False)
    assert len(inst.limit_schedules) == 2


# -- Y.2.gate.c.12 — capture-to-run-dir sidecar -----------------------------


_MINIMAL_YAML = dedent("""\
    accounts:
      - id: gl-1
        role: Control
        scope: internal

    rails:
      - name: SubCharge
        origin: InternalInitiated
        leg_role: Control
        leg_direction: Debit
    """)


# -- AB.4: Chain.fan_in + expected_parent_count loader contract -------------


def test_loader_chain_fan_in_defaults_to_false_when_absent(
    tmp_path: Path,
) -> None:
    """Pre-AB.4 YAML (no fan_in: key) loads with fan_in=False +
    expected_parent_count=None — backwards-compatible default."""
    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: ParentRail
            origin: InternalInitiated
            source_role: R
            destination_role: R
            expected_net: "0"
        transfer_templates:
          - name: ChildTpl
            expected_net: "0"
            transfer_key: []
            completion: business_day_end+1d
            leg_rails: [ParentRail]
        chains:
          - parent: ParentRail
            children: [ChildTpl]
    """))
    inst = load_instance(p, validate=False)
    assert inst.chains[0].fan_in is False
    assert inst.chains[0].expected_parent_count is None


def test_loader_chain_fan_in_parses_true_with_expected_parent_count(
    tmp_path: Path,
) -> None:
    """AB.4 YAML loads fan_in + expected_parent_count cleanly."""
    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: ParentRail
            origin: InternalInitiated
            source_role: R
            destination_role: R
            expected_net: "0"
        transfer_templates:
          - name: ChildTpl
            expected_net: "0"
            transfer_key: []
            completion: business_day_end+1d
            leg_rails: [ParentRail]
        chains:
          - parent: ParentRail
            children: [ChildTpl]
            fan_in: true
            expected_parent_count: 3
    """))
    inst = load_instance(p, validate=False)
    assert inst.chains[0].fan_in is True
    assert inst.chains[0].expected_parent_count == 3


def test_loader_chain_fan_in_rejects_non_bool(tmp_path: Path) -> None:
    """fan_in: not-a-bool surfaces a typed error at load time, not a
    silent coercion."""
    from recon_gen.common.l2.loader import L2LoaderError

    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: ParentRail
            origin: InternalInitiated
            source_role: R
            destination_role: R
            expected_net: "0"
        chains:
          - parent: ParentRail
            children: [ParentRail]
            fan_in: "yes"
    """))
    with pytest.raises(L2LoaderError, match="fan_in.*expected bool"):
        load_instance(p, validate=False)


def test_loader_chain_expected_parent_count_rejects_non_int(
    tmp_path: Path,
) -> None:
    """expected_parent_count: not-an-int surfaces typed error."""
    from recon_gen.common.l2.loader import L2LoaderError

    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: ParentRail
            origin: InternalInitiated
            source_role: R
            destination_role: R
            expected_net: "0"
        chains:
          - parent: ParentRail
            children: [ParentRail]
            expected_parent_count: "three"
    """))
    with pytest.raises(L2LoaderError, match="expected_parent_count.*expected int"):
        load_instance(p, validate=False)


# -- AB.5: Rail.amount_typical_range loader contract ------------------------


def test_loader_amount_typical_range_defaults_to_none_when_absent(
    tmp_path: Path,
) -> None:
    """Pre-AB.5 YAML loads with amount_typical_range=None."""
    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: R
            leg_direction: Debit
    """))
    inst = load_instance(p, validate=False)
    assert inst.rails[0].amount_typical_range is None


def test_loader_amount_typical_range_parses_two_element_list(
    tmp_path: Path,
) -> None:
    """AB.5 YAML loads [min, max] cleanly into a tuple of Money."""
    from decimal import Decimal

    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: R
            leg_direction: Debit
            amount_typical_range: ["5.00", "500.00"]
    """))
    inst = load_instance(p, validate=False)
    rng = inst.rails[0].amount_typical_range
    assert rng is not None
    assert rng[0] == Decimal("5.00")
    assert rng[1] == Decimal("500.00")


def test_loader_amount_typical_range_rejects_non_list(tmp_path: Path) -> None:
    """A scalar (not a list) surfaces typed error."""
    from recon_gen.common.l2.loader import L2LoaderError

    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: R
            leg_direction: Debit
            amount_typical_range: "5"
    """))
    with pytest.raises(
        L2LoaderError, match=r"amount_typical_range.*expected a 2-element list",
    ):
        load_instance(p, validate=False)


def test_loader_amount_typical_range_rejects_wrong_length(tmp_path: Path) -> None:
    """3-element list rejected."""
    from recon_gen.common.l2.loader import L2LoaderError

    p = tmp_path / "src.yaml"
    p.write_text(dedent("""\
        accounts:
          - id: a
            role: R
            scope: internal
        rails:
          - name: SubCharge
            origin: InternalInitiated
            leg_role: R
            leg_direction: Debit
            amount_typical_range: ["5", "100", "500"]
    """))
    with pytest.raises(
        L2LoaderError, match=r"expected exactly 2 elements",
    ):
        load_instance(p, validate=False)


def test_capture_no_op_when_run_dir_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Y.2.gate.c.12 — direct invocation (env unset) writes nothing."""
    monkeypatch.delenv(RECON_GEN_RUN_DIR.name, raising=False)
    p = tmp_path / "src.yaml"
    p.write_text(_MINIMAL_YAML)

    load_instance(p, validate=False)

    # The tmp_path tree has only the source YAML — no l2/ subdir.
    assert list(tmp_path.iterdir()) == [p]


def test_capture_writes_yaml_when_run_dir_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Y.2.gate.c.12 — env set → ``<run-dir>/l2/<source-basename>.yaml``
    written with the same bytes as the source.

    Z.C (2026-05-15): the sidecar filename now derives from the source
    yaml's basename (``yaml_path.stem``), since the L2 yaml no longer
    carries an ``instance:`` field. Multiple yaml files in one run dir
    must therefore use distinct basenames to avoid collision.
    """
    # Y.2.gate.b.15 — must_be_dir validator requires the path to
    # exist; the sidecar's _capture_to_run_dir swallows
    # EnvVarInvalid, so without mkdir the capture would soft-fall
    # to no-op and the assertion below would fail.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
    p = tmp_path / "src.yaml"
    p.write_text(_MINIMAL_YAML)

    load_instance(p, validate=False)

    target = run_dir / "l2" / "src.yaml"
    assert target.exists()
    assert target.read_text() == _MINIMAL_YAML


def test_capture_overwrites_on_repeat_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading the same instance twice in one session writes the same
    bytes to the same target — last write wins (idempotent for the
    no-mutation case; if the YAML changed mid-run the latest content
    is what lands)."""
    # See test_capture_writes_yaml_when_run_dir_set for why mkdir is needed.
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(run_dir))
    p = tmp_path / "src.yaml"
    p.write_text(_MINIMAL_YAML)

    load_instance(p, validate=False)
    # Tweak content (still valid) and reload.
    p.write_text(_MINIMAL_YAML + "\n# trailing comment\n")
    load_instance(p, validate=False)

    target = run_dir / "l2" / "src.yaml"
    assert target.read_text().endswith("# trailing comment\n")


def test_capture_sidecar_failure_doesnt_break_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sidecar contract: if the capture write fails, ``load_instance``
    must still return the parsed instance. Simulated here by pointing
    ``RECON_GEN_RUN_DIR`` at a path that can't be created (a regular file
    sitting where a directory should be)."""
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a directory.")
    # /<blocker>/l2/<basename>.yaml — mkdir(blocker/l2) fails because
    # blocker is a file, but load_instance must still succeed.
    monkeypatch.setenv(RECON_GEN_RUN_DIR.name, str(blocker))
    p = tmp_path / "src.yaml"
    p.write_text(_MINIMAL_YAML)

    inst = load_instance(p, validate=False)
    # Smoke: load returned a usable instance despite the sidecar failure.
    assert inst.accounts
