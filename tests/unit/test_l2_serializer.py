"""Round-trip tests for ``common.l2.serializer.serialize_l2`` (X.4.d.3).

Contract: ``load(write(serialize_l2(load(yaml))))`` is field-equal to
the original ``L2Instance`` for every bundled fixture. This is the
foundation the X.4.d editor primitives + X.4.e cascade flow stand on —
without round-trip stability, every PUT mutates the wrong shape.

We round-trip every YAML fixture in ``tests/l2/`` (spec_example +
sasquatch_pr — the two hand-authored institutions). Validator runs by
default, so the emitted YAML also has to pass cross-entity validation.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.serializer import serialize_l2


_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "l2"


@pytest.mark.parametrize(
    "fixture_name",
    ["spec_example.yaml", "sasquatch_pr.yaml"],
)
def test_load_serialize_load_round_trip_preserves_model(
    fixture_name: str,
) -> None:
    """The serializer's contract: a load → serialize → load round-trip
    yields a field-equal L2Instance.

    Both bundled fixtures (the SPEC-shape spec_example and the
    persona-rich sasquatch_pr) are exercised so any field a
    persona-rich L2 sets that spec_example doesn't is also covered.
    """
    fixture_path = _FIXTURES_DIR / fixture_name
    original = load_instance(fixture_path)

    yaml_text = serialize_l2(original)

    fd, tmp_path_str = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    tmp_path = Path(tmp_path_str)
    try:
        tmp_path.write_text(yaml_text)
        round_tripped = load_instance(tmp_path)
    finally:
        tmp_path.unlink()

    # Frozen dataclass __eq__ walks every field recursively — this one
    # assert covers every primitive's every field.
    assert round_tripped == original, (
        f"round-trip diverged on {fixture_name}; first divergent field "
        f"is wherever frozen-dataclass __eq__ landed (re-run with "
        f"a per-field dataclass-walk if you need locator detail)."
    )


def test_serialize_l2_emits_valid_yaml() -> None:
    """The emitted text MUST be valid YAML (not just JSON-shaped)."""
    import yaml as _yaml

    instance = load_instance(_FIXTURES_DIR / "spec_example.yaml")
    text = serialize_l2(instance)
    parsed = _yaml.safe_load(text)
    assert isinstance(parsed, dict)
    # Z.C — serializer no longer emits `instance:` (the field is gone).
    assert "instance" not in parsed
    assert isinstance(parsed["accounts"], list)


def test_serialize_l2_skips_default_optional_fields() -> None:
    """Optional fields with default values shouldn't bloat the emitted
    YAML — match the hand-authored fixture style of "only what's set"."""
    instance = load_instance(_FIXTURES_DIR / "spec_example.yaml")
    text = serialize_l2(instance)
    # spec_example has no role_business_day_offsets — should be absent.
    assert "role_business_day_offsets" not in text
    # spec_example has no persona block — should be absent.
    assert "persona:" not in text
    # spec_example has rails with empty bundles_activity / no posted_requirements
    # on most rails — those defaults shouldn't bloat per-rail YAML.
    # (Just spot-check that we don't emit empty `posted_requirements: []`)
    assert "posted_requirements: []" not in text
    assert "bundles_activity: []" not in text
    # AB.4 — fan_in / expected_parent_count default values shouldn't
    # bloat pre-AB.4 chain rows. spec_example has one fan-in chain
    # (AB.4.5.spec activated it), so the field SHOULD appear — but
    # only on chains that declare it. Spot-check that ALL chains
    # don't carry the field (i.e., default omission still works for
    # the non-fan-in chains).
    chain_blocks = text.split("- parent: ")[1:]
    fan_in_chain_blocks = [b for b in chain_blocks if "fan_in:" in b]
    assert len(fan_in_chain_blocks) == 1, (
        f"spec_example expected exactly 1 chain with fan_in field; "
        f"got {len(fan_in_chain_blocks)}"
    )
    # The one fan-in chain emits both fields.
    assert "fan_in: true" in text
    assert "expected_parent_count: 2" in text


def test_serialize_l2_emits_fan_in_when_non_default() -> None:
    """AB.4 — when a Chain declares fan_in=True (and optionally
    expected_parent_count), the serializer emits both fields so the
    YAML round-trips byte-equivalent. The non-default-only emit rule
    means pre-AB.4 chains stay clean (above test); fan-in chains
    surface both fields here."""
    import dataclasses

    from recon_gen.common.l2.primitives import Chain, ChainChildSpec, Identifier
    from recon_gen.common.l2.serializer import serialize_l2

    instance = load_instance(_FIXTURES_DIR / "spec_example.yaml")
    # Synthesize a fan-in chain on the existing ReconciliationLeg →
    # MerchantSettlementCycle pair (AB.2.6.spec — template-as-child).
    fanin_chain = Chain(
        parent=Identifier("ReconciliationLeg"),
        children=(
            ChainChildSpec(
                name=Identifier("MerchantSettlementCycle"),
                fan_in=True,
                expected_parent_count=3,
            ),
        ),
    )
    instance = dataclasses.replace(instance, chains=(fanin_chain,))
    text = serialize_l2(instance)
    assert "fan_in: true" in text
    assert "expected_parent_count: 3" in text


def test_serialize_l2_amount_typical_range_round_trips() -> None:
    """AB.5 — non-default amount_typical_range emits + round-trips
    byte-equivalent; default None is omitted. spec_example carries
    3 ranged rails (AB.5.6.spec), so the count is fixture-pinned at
    3 — adding a 4th ranged rail would require updating both spec +
    this test together."""
    from recon_gen.common.l2.serializer import serialize_l2

    instance = load_instance(_FIXTURES_DIR / "spec_example.yaml")
    text = serialize_l2(instance)
    assert "amount_typical_range:" in text
    # spec_example carries 3 ranged rails (ExternalRailInbound /
    # ExternalRailOutbound / SubledgerCharge). Other rails (no range)
    # don't emit the field per "skip default optional fields" rule.
    assert text.count("amount_typical_range:") == 3


def test_serialize_l2_omits_expected_parent_count_when_unset() -> None:
    """AB.4 — a fan-in chain that leaves expected_parent_count unset
    (variable-batch-size flow) serializes with fan_in: true but
    omits expected_parent_count entirely."""
    import dataclasses

    from recon_gen.common.l2.primitives import Chain, ChainChildSpec, Identifier
    from recon_gen.common.l2.serializer import serialize_l2

    instance = load_instance(_FIXTURES_DIR / "spec_example.yaml")
    fanin_chain = Chain(
        parent=Identifier("ReconciliationLeg"),
        children=(
            ChainChildSpec(
                name=Identifier("MerchantSettlementCycle"),
                fan_in=True,
                expected_parent_count=None,
            ),
        ),
    )
    instance = dataclasses.replace(instance, chains=(fanin_chain,))
    text = serialize_l2(instance)
    assert "fan_in: true" in text
    assert "expected_parent_count:" not in text
