# pyright: reportPrivateUsage=false
"""Regression test for the ``_pick_metadata_value`` synthetic-fallback
bug surfaced by operator cold-read: a rail with
``metadata_keys=[sweep_date, settlement_batch_id]`` was emitting the
SAME synthesized string for both keys at a given ``firing_seq`` because
the fallback was ``f"{rail_name}-firing-{seq:04d}"`` — no key segment.

Fix: include ``key_name`` in the synthesized fallback so distinct keys
on the same rail/firing get distinct values. Examples-mode still
cycles by ``firing_seq`` and IGNORES ``key_name`` (backward-compat:
operator-declared examples are the source of truth).
"""
from __future__ import annotations

from recon_gen.common.l2.auto_scenario import _pick_metadata_value
from recon_gen.common.l2.primitives import Identifier


def test_synthetic_fallback_distinct_per_key_name() -> None:
    """Two different key_names on the same (rail, firing_seq) must
    yield two different synthesized strings — the bug."""
    rail = Identifier("ACHOriginationDailySweep")
    k1 = Identifier("sweep_date")
    k2 = Identifier("settlement_batch_id")

    v1 = _pick_metadata_value(
        examples=None, rail_name=rail, key_name=k1, firing_seq=61,
    )
    v2 = _pick_metadata_value(
        examples=None, rail_name=rail, key_name=k2, firing_seq=61,
    )

    assert v1 != v2, (
        "Two distinct metadata_keys on the same rail+firing collapsed "
        "to the same synthesized value — the original bug."
    )
    # Both should mention the rail + key + firing seq.
    assert "ACHOriginationDailySweep" in v1
    assert "ACHOriginationDailySweep" in v2
    assert "sweep_date" in v1
    assert "settlement_batch_id" in v2
    assert "0061" in v1
    assert "0061" in v2


def test_synthetic_fallback_shape() -> None:
    """The fallback format is ``<rail>-<key>-firing-<seq:04d>``."""
    rail = Identifier("WireOut")
    key = Identifier("ofac_status")
    got = _pick_metadata_value(
        examples=None, rail_name=rail, key_name=key, firing_seq=7,
    )
    assert got == "WireOut-ofac_status-firing-0007"


def test_examples_mode_cycles_ignoring_key_name() -> None:
    """When ``examples`` is set, ``firing_seq`` drives selection
    (modular), and ``key_name`` is ignored. This preserves the
    backward-compat contract: an operator-declared ``metadata_value_examples``
    list is the source of truth for that key, and if two keys happen
    to share the same examples list, they can legitimately yield the
    same value."""
    rail = Identifier("ACH")
    examples = ("alpha", "bravo", "charlie")

    # firing_seq=1 → examples[0], =2 → [1], =3 → [2], =4 → [0] (wrap)
    assert _pick_metadata_value(
        examples=examples, rail_name=rail,
        key_name=Identifier("k1"), firing_seq=1,
    ) == "alpha"
    assert _pick_metadata_value(
        examples=examples, rail_name=rail,
        key_name=Identifier("k1"), firing_seq=2,
    ) == "bravo"
    assert _pick_metadata_value(
        examples=examples, rail_name=rail,
        key_name=Identifier("k1"), firing_seq=4,
    ) == "alpha"

    # Same examples + same firing_seq + different key_name → same value
    # (key_name is intentionally ignored in examples mode).
    v_a = _pick_metadata_value(
        examples=examples, rail_name=rail,
        key_name=Identifier("k_first"), firing_seq=2,
    )
    v_b = _pick_metadata_value(
        examples=examples, rail_name=rail,
        key_name=Identifier("k_second"), firing_seq=2,
    )
    assert v_a == v_b == "bravo"


def test_examples_mode_modular_indexing_no_indexerror() -> None:
    """firing_seq larger than len(examples) wraps via modular indexing
    so per_rail_firings can exceed the examples list without an
    IndexError. Regression guard on the existing M.4.2b contract."""
    rail = Identifier("ACH")
    examples = ("only",)
    for seq in (1, 2, 1000):
        assert _pick_metadata_value(
            examples=examples, rail_name=rail,
            key_name=Identifier("k"), firing_seq=seq,
        ) == "only"
