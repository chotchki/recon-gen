"""Tests for the optional ``description`` field on every L2 primitive
(M.1a.6).

The field is free-form prose used by handbook + training render
templates. The library does no pre-processing on the value beyond
"non-empty string when present"; the tests cover the loader behaviour
(present / absent / wrong-type / blank) and verify the parsed value
reaches the dataclass instance untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quicksight_gen.common.l2 import (
    L2LoaderError,
    load_instance,
)


# -- Default behaviour: omitted descriptions stay None -----------------------


def test_omitted_description_defaults_to_none(tmp_path: Path) -> None:
    """When YAML omits `description:`, the loaded primitive carries None."""
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
    )
    inst = load_instance(p)
    assert inst.description is None
    assert inst.accounts[0].description is None


# -- Description on every primitive kind -------------------------------------


def test_top_level_instance_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "description: Sasquatch National Bank — alignment-only test instance.\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
    )
    inst = load_instance(p)
    assert inst.description == (
        "Sasquatch National Bank — alignment-only test instance."
    )


def test_account_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "    description: The internal operations account.\n"
    )
    inst = load_instance(p)
    assert inst.accounts[0].description == "The internal operations account."


def test_account_template_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: ParentRole\n"
        "    scope: internal\n"
        "account_templates:\n"
        "  - role: ChildTemplate\n"
        "    scope: internal\n"
        "    parent_role: ParentRole\n"
        "    description: Per-customer subledger template.\n"
    )
    inst = load_instance(p)
    assert inst.account_templates[0].description == (
        "Per-customer subledger template."
    )


def test_two_leg_rail_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "  - id: ext-001\n"
        "    role: B\n"
        "    scope: external\n"
        "rails:\n"
        "  - name: ExtRail\n"
        "    source_role: B\n"
        "    destination_role: A\n"
        "    expected_net: 0\n"
        "    origin: InternalInitiated\n"
        "    description: Inbound ACH from external counterparty.\n"
    )
    inst = load_instance(p)
    assert inst.rails[0].description == "Inbound ACH from external counterparty."


def test_single_leg_rail_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "rails:\n"
        "  - name: Charge\n"
        "    leg_role: A\n"
        "    leg_direction: Debit\n"
        "    origin: InternalInitiated\n"
        "    metadata_keys: [k]\n"
        "    description: Per-customer charge leg.\n"
        "transfer_templates:\n"
        "  - name: Cycle\n"
        "    expected_net: 0\n"
        "    transfer_key: [k]\n"
        "    completion: business_day_end\n"
        "    leg_rails: [Charge]\n"
    )
    inst = load_instance(p)
    assert inst.rails[0].description == "Per-customer charge leg."


def test_transfer_template_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "rails:\n"
        "  - name: Charge\n"
        "    leg_role: A\n"
        "    leg_direction: Debit\n"
        "    origin: InternalInitiated\n"
        "    metadata_keys: [k]\n"
        "transfer_templates:\n"
        "  - name: Cycle\n"
        "    expected_net: 0\n"
        "    transfer_key: [k]\n"
        "    completion: business_day_end\n"
        "    leg_rails: [Charge]\n"
        "    description: One Transfer per (key) grouping.\n"
    )
    inst = load_instance(p)
    assert inst.transfer_templates[0].description == (
        "One Transfer per (key) grouping."
    )


def test_chain_entry_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "  - id: ext-001\n"
        "    role: B\n"
        "    scope: external\n"
        "rails:\n"
        "  - name: Parent\n"
        "    source_role: B\n"
        "    destination_role: A\n"
        "    expected_net: 0\n"
        "    origin: InternalInitiated\n"
        "  - name: Child\n"
        "    source_role: A\n"
        "    destination_role: B\n"
        "    expected_net: 0\n"
        "    origin: InternalInitiated\n"
        "chains:\n"
        "  - parent: Parent\n"
        "    children:\n"
        "      - Child\n"
        "    description: Every Parent firing should trigger a Child.\n"
    )
    inst = load_instance(p)
    assert inst.chains[0].description == (
        "Every Parent firing should trigger a Child."
    )


def test_limit_schedule_description_loads(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: ParentRole\n"
        "    scope: internal\n"
        "limit_schedules:\n"
        "  - parent_role: ParentRole\n"
        "    rail: NonexistentRail\n"
        "    cap: 5000.00\n"
        "    description: Per-child daily cap mandated by policy.\n"
    )
    # validate=False: narrow per-primitive description test — the
    # fixture has no Rail with name 'NonexistentRail' (R10 would reject),
    # but this test only asserts the description field round-trips.
    inst = load_instance(p, validate=False)
    assert inst.limit_schedules[0].description == (
        "Per-child daily cap mandated by policy."
    )


# -- Rejection behaviour -----------------------------------------------------


def test_blank_description_rejected(tmp_path: Path) -> None:
    """Empty string description is a configuration smell — reject vs accept-as-None."""
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "    description: ''\n"
    )
    with pytest.raises(L2LoaderError, match="description is empty"):
        load_instance(p)


def test_whitespace_only_description_rejected(tmp_path: Path) -> None:
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "    description: '   '\n"
    )
    with pytest.raises(L2LoaderError, match="description is empty"):
        load_instance(p)


def test_non_string_description_rejected(tmp_path: Path) -> None:
    """A YAML mapping or list under ``description:`` is almost certainly
    a key collision — fail loudly rather than silently coerce."""
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "    description:\n"
        "      short: hi\n"
        "      long: there\n"
    )
    with pytest.raises(L2LoaderError, match="description must be a string"):
        load_instance(p)


# -- Multi-line markdown still survives whole --------------------------------


def test_multiline_markdown_description_preserved(tmp_path: Path) -> None:
    """The library does no pre-processing — markdown reaches handbook
    templates verbatim, including paragraph breaks."""
    p = tmp_path / "instance.yaml"
    p.write_text(
        "instance: spk\n"
        "accounts:\n"
        "  - id: int-001\n"
        "    role: A\n"
        "    scope: internal\n"
        "    description: |\n"
        "      Header line one.\n"
        "\n"
        "      Body paragraph two with a [link](https://example).\n"
    )
    inst = load_instance(p)
    desc = inst.accounts[0].description
    assert desc is not None
    assert "Header line one." in desc
    assert "[link](https://example)" in desc
    assert "\n\n" in desc  # paragraph break preserved
