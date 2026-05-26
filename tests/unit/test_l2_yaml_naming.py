"""Tests for the M.4.2b YAML-driven synthetic naming polish.

Two opt-in YAML extensions:

1. ``account_templates[].instance_id_template`` +
   ``instance_name_template`` — Python str.format() templates the
   demo seed's ``_materialize_instances`` uses to render persona-aware
   per-template-instance ids and display names. Both default to None;
   seed falls back to the legacy ``cust-{n:03d}`` + ``Customer {n}``
   patterns. Loader validates the format string only references
   ``{role}`` + ``{n}`` placeholders.

2. ``rails[].metadata_value_examples`` — optional per-key example
   value lists. Broad-mode seed cycles through them by firing seq
   when set; falls back to the synthetic ``<rail>-firing-<seq>``
   pattern when absent. Validator R13 rejects example-list keys not
   in the rail's ``metadata_keys``.

Coverage:
- Loader: parses both fields cleanly when present; preserves
  None/empty when absent; rejects bad shapes / invalid placeholders.
- Validator R13: rejects mismatched example keys.
- Seed: render correct values when opted-in; falls back when not.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest

from recon_gen.common.l2 import (
    L2LoaderError,
    L2ValidationError,
    TwoLegRail,
    load_instance,
    validate,
)
from recon_gen.common.l2.auto_scenario import (
    _materialize_instances,
    default_scenario_for,
)
from recon_gen.common.l2.seed import emit_seed


CANONICAL_TODAY = date(2030, 1, 1)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "instance.yaml"
    p.write_text(dedent(body))
    return p


# ---------------------------------------------------------------------------
# instance_id_template / instance_name_template — loader
# ---------------------------------------------------------------------------


def test_instance_templates_load_when_set(tmp_path: Path) -> None:
    """Both optional fields parse from YAML and land on the
    AccountTemplate dataclass."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
            instance_id_template: "cust-{n:03d}-bigfoot"
            instance_name_template: "Bigfoot Customer {n}"
        rails:
          - name: Charge
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [k]
        transfer_templates:
          - name: Cycle
            expected_net: 0
            transfer_key: [k]
            completion: business_day_end
            leg_rails: [Charge]
        """)
    inst = load_instance(p)
    tmpl = inst.account_templates[0]
    assert tmpl.instance_id_template == "cust-{n:03d}-bigfoot"
    assert tmpl.instance_name_template == "Bigfoot Customer {n}"


def test_instance_templates_default_to_none(tmp_path: Path) -> None:
    """When the YAML omits both fields, AccountTemplate carries None
    for each — the seed's fallback pattern triggers downstream."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
        rails:
          - name: Charge
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [k]
        transfer_templates:
          - name: Cycle
            expected_net: 0
            transfer_key: [k]
            completion: business_day_end
            leg_rails: [Charge]
        """)
    inst = load_instance(p)
    tmpl = inst.account_templates[0]
    assert tmpl.instance_id_template is None
    assert tmpl.instance_name_template is None


def test_instance_template_rejects_unknown_placeholder(tmp_path: Path) -> None:
    """Loader rejects any placeholder other than {role} + {n} — catches
    integrator typos like {customer_id} or {prefix}."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
            instance_name_template: "Cust-{customer_id}"
        rails: []
        """)
    with pytest.raises(L2LoaderError, match="unknown placeholder 'customer_id'"):
        load_instance(p, validate=False)


def test_instance_template_rejects_non_string(tmp_path: Path) -> None:
    """Loader rejects non-string values — catches accidental YAML
    object/list shapes."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
            instance_id_template: 42
        rails: []
        """)
    with pytest.raises(L2LoaderError, match="instance template must be a string"):
        load_instance(p, validate=False)


def test_materialize_instances_uses_templates_when_set(tmp_path: Path) -> None:
    """Seed's _materialize_instances renders persona-aware ids + names
    when the YAML opts in."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
            instance_id_template: "cust-{n:03d}-bigfoot"
            instance_name_template: "Bigfoot {role} {n}"
        rails:
          - name: Charge
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [k]
        transfer_templates:
          - name: Cycle
            expected_net: 0
            transfer_key: [k]
            completion: business_day_end
            leg_rails: [Charge]
        """)
    inst = load_instance(p)
    cust1, cust2 = _materialize_instances(inst.account_templates[0])
    assert str(cust1.account_id) == "cust-001-bigfoot"
    assert str(cust1.name) == "Bigfoot CustomerSubledger 1"
    assert str(cust2.account_id) == "cust-002-bigfoot"
    assert str(cust2.name) == "Bigfoot CustomerSubledger 2"


def test_materialize_instances_falls_back_when_unset(tmp_path: Path) -> None:
    """Without templates declared, seed renders the legacy synthetic
    `cust-{n:03d}` + `Customer {n}` patterns — preserves existing
    fixture seed_hash."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
        rails:
          - name: Charge
            leg_role: CustomerSubledger
            leg_direction: Debit
            origin: InternalInitiated
            metadata_keys: [k]
        transfer_templates:
          - name: Cycle
            expected_net: 0
            transfer_key: [k]
            completion: business_day_end
            leg_rails: [Charge]
        """)
    inst = load_instance(p)
    cust1, cust2 = _materialize_instances(inst.account_templates[0])
    assert str(cust1.account_id) == "cust-001"
    assert str(cust1.name) == "Customer 1"
    assert str(cust2.account_id) == "cust-002"
    assert str(cust2.name) == "Customer 2"


# ---------------------------------------------------------------------------
# metadata_value_examples — loader + validator R13
# ---------------------------------------------------------------------------


def test_metadata_value_examples_load(tmp_path: Path) -> None:
    """Loader parses the YAML mapping into a tuple-of-tuples on the rail."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
          - id: ext
            role: ExternalCounterparty
            scope: external
        rails:
          - name: ExternalRail
            origin: InternalInitiated
            source_role: Ledger
            destination_role: ExternalCounterparty
            expected_net: 0
            metadata_keys: [merchant_id, settlement_period]
            metadata_value_examples:
              merchant_id: ["m-001", "m-002", "m-003"]
              settlement_period: ["2026-04", "2026-05"]
        """)
    inst = load_instance(p)
    rail = inst.rails[0]
    assert isinstance(rail, TwoLegRail)
    examples = dict(rail.metadata_value_examples)
    assert examples["merchant_id"] == ("m-001", "m-002", "m-003")
    assert examples["settlement_period"] == ("2026-04", "2026-05")


def test_metadata_value_examples_default_empty(tmp_path: Path) -> None:
    """Without the field, the rail carries an empty tuple — preserves
    legacy seed behavior (no opt-in needed)."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
          - id: ext
            role: ExternalCounterparty
            scope: external
        rails:
          - name: ExternalRail
            origin: InternalInitiated
            source_role: Ledger
            destination_role: ExternalCounterparty
            expected_net: 0
            metadata_keys: [merchant_id]
        """)
    inst = load_instance(p)
    assert inst.rails[0].metadata_value_examples == ()


def test_metadata_value_examples_loader_rejects_empty_list(
    tmp_path: Path,
) -> None:
    """An empty value list is a typo (the integrator forgot to fill it
    in); reject at load."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        rails:
          - name: Rail
            origin: InternalInitiated
            leg_role: Ledger
            leg_direction: Debit
            metadata_keys: [k]
            metadata_value_examples:
              k: []
        """)
    with pytest.raises(L2LoaderError, match="example list must be non-empty"):
        load_instance(p, validate=False)


def test_metadata_value_examples_loader_rejects_non_string_value(
    tmp_path: Path,
) -> None:
    """Example values must be strings — catches numeric / null typos
    where YAML auto-coerces."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        rails:
          - name: Rail
            origin: InternalInitiated
            leg_role: Ledger
            leg_direction: Debit
            metadata_keys: [k]
            metadata_value_examples:
              k: ["a", 42, "c"]
        """)
    with pytest.raises(L2LoaderError, match="example values must be strings"):
        load_instance(p, validate=False)


def test_validator_r13_rejects_example_key_not_in_metadata_keys(
    tmp_path: Path,
) -> None:
    """R13: an example key not in metadata_keys is a typo that would
    silently never be used — caught at validate."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
        rails:
          - name: Rail
            origin: InternalInitiated
            leg_role: Ledger
            leg_direction: Debit
            metadata_keys: [merchant_id]
            metadata_value_examples:
              merchant_idx: ["a", "b"]
        transfer_templates:
          - name: Cycle
            expected_net: 0
            transfer_key: [merchant_id]
            completion: business_day_end
            leg_rails: [Rail]
        """)
    # Skip cross-entity validate here, then fire R13 explicitly
    inst = load_instance(p, validate=False)
    with pytest.raises(
        L2ValidationError,
        match="metadata_value_examples: key 'merchant_idx' is not in metadata_keys",
    ):
        validate(inst)


# ---------------------------------------------------------------------------
# Broad-mode seed integration
# ---------------------------------------------------------------------------


def test_broad_mode_uses_metadata_examples_when_set(tmp_path: Path) -> None:
    """Broad-mode RailFiringPlant emission cycles through declared
    examples (firing 1 → examples[0], firing 2 → examples[1], etc.).

    Fixture needs an AccountTemplate so the auto-scenario picker
    materializes customer instances; without one, default_scenario_for
    early-returns with no plants regardless of mode.
    """
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
          - id: ext
            role: ExternalCounterparty
            scope: external
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
        rails:
          - name: ExternalRail
            origin: InternalInitiated
            source_role: Ledger
            destination_role: ExternalCounterparty
            expected_net: 0
            metadata_keys: [merchant_id]
            metadata_value_examples:
              merchant_id: ["bigfoot-001", "yeti-002", "sasquatch-003"]
        """)
    inst = load_instance(p)
    report = default_scenario_for(
        inst, today=CANONICAL_TODAY, mode="broad",
    )
    sql = emit_seed(inst, report.scenario, prefix="test")
    # All three example values should appear in the seed SQL (one per
    # firing of ExternalRail).
    assert "bigfoot-001" in sql
    assert "yeti-002" in sql
    assert "sasquatch-003" in sql
    # The synthetic fallback pattern should NOT appear for this rail
    # (cycle uses examples; no `<rail>-firing-<seq>` for merchant_id).
    assert "ExternalRail-firing-0001" not in sql


def test_broad_mode_falls_back_when_examples_not_set(tmp_path: Path) -> None:
    """When a rail's key has no example list, broad mode emits the
    legacy `<rail>-firing-<seq>` pattern. Mixing opt-in keys with
    non-opt-in keys works naturally."""
    p = _write_yaml(tmp_path, """\
        accounts:
          - id: gl
            role: Ledger
            scope: internal
          - id: ext
            role: ExternalCounterparty
            scope: external
        account_templates:
          - role: CustomerSubledger
            scope: internal
            parent_role: Ledger
        rails:
          - name: ExternalRail
            origin: InternalInitiated
            source_role: Ledger
            destination_role: ExternalCounterparty
            expected_net: 0
            metadata_keys: [merchant_id, batch_id]
            metadata_value_examples:
              merchant_id: ["bigfoot-001"]
            # batch_id has no examples — should fall back
        """)
    inst = load_instance(p)
    report = default_scenario_for(
        inst, today=CANONICAL_TODAY, mode="broad",
    )
    sql = emit_seed(inst, report.scenario, prefix="test")
    assert "bigfoot-001" in sql
    assert "ExternalRail-firing-0001" in sql  # batch_id fallback
