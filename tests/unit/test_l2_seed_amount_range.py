"""AB.5 (E7): seed log-uniform amount-range picker — shape contracts.

Pins the `_baseline_amount_sample`'s AB.5 path (rail with
`amount_typical_range` declared) so it samples log-uniformly within
the declared range, respects the `cap` parameter, and stays
deterministic per seed.
"""

from __future__ import annotations

import random
from decimal import Decimal

from recon_gen.common.l2.primitives import (
    Identifier,
    Money,
    SingleLegRail,
)
from recon_gen.common.l2.seed import _baseline_amount_sample


def _rail_with_range(lo: str, hi: str) -> SingleLegRail:
    return SingleLegRail(
        name=Identifier("RailA"),
        metadata_keys=(),
        leg_role=(Identifier("R"),),
        leg_direction="Debit",
        origin="InternalInitiated",
        amount_typical_range=(Money(Decimal(lo)), Money(Decimal(hi))),
    )


def test_amount_in_range_when_declared() -> None:
    """AB.5 (E7): a rail with declared amount_typical_range samples
    within [min, max] (inclusive) on every draw."""
    rail = _rail_with_range("5.00", "500.00")
    rng = random.Random(42)
    for _ in range(200):
        amount = _baseline_amount_sample(
            rng, "internal_transfer", rail=rail,
        )
        assert Decimal("5.00") <= amount <= Decimal("500.00"), (
            f"sampled amount {amount} fell outside declared range [5, 500]"
        )


def test_amount_picker_log_uniform_clusters_at_low_end() -> None:
    """AB.5.0 lock: log-uniform default. Sampling 2000 times from
    [1, 1000] should yield more values below 100 than above (because
    in log space the lower decade [1, 10] has the same width as
    [100, 1000])."""
    rail = _rail_with_range("1.00", "1000.00")
    rng = random.Random(42)
    samples = [
        _baseline_amount_sample(rng, "internal_transfer", rail=rail)
        for _ in range(2000)
    ]
    below_100 = sum(1 for a in samples if a < Decimal("100"))
    above_100 = sum(1 for a in samples if a >= Decimal("100"))
    # Log-uniform on [1, 1000]: P(amount < 100) = log(100)/log(1000) = 2/3.
    # Expect ~1333 vs ~667 below_100 vs above_100. Generous tolerance.
    assert below_100 > above_100, (
        f"log-uniform should cluster below 100; got "
        f"{below_100} below vs {above_100} above"
    )


def test_amount_picker_deterministic_per_seed() -> None:
    """Same seed + same rail → same amount sequence (reproducibility)."""
    rail = _rail_with_range("10.00", "1000.00")
    rng_a = random.Random(0xDEADBEEF)
    rng_b = random.Random(0xDEADBEEF)
    a = [_baseline_amount_sample(rng_a, "internal_transfer", rail=rail)
         for _ in range(20)]
    b = [_baseline_amount_sample(rng_b, "internal_transfer", rail=rail)
         for _ in range(20)]
    assert a == b


def test_amount_picker_respects_cap() -> None:
    """When cap is set + cap < range.max, samples are clamped below
    cap (via resample loop)."""
    rail = _rail_with_range("100.00", "10000.00")
    rng = random.Random(42)
    cap = Decimal("500")
    for _ in range(100):
        amount = _baseline_amount_sample(
            rng, "internal_transfer", cap=cap, rail=rail,
        )
        assert amount <= cap, f"amount {amount} exceeded cap {cap}"


def test_amount_picker_falls_back_to_lognormal_when_no_range() -> None:
    """A rail without amount_typical_range falls through to the
    per-kind lognormal path; output is well-formed Decimal but NOT
    in the AB.5 range space — verifies the AB.5 path is gated on the
    rail field."""
    rail = SingleLegRail(
        name=Identifier("RailNoRange"),
        metadata_keys=(),
        leg_role=(Identifier("R"),),
        leg_direction="Debit",
        origin="InternalInitiated",
    )
    rng = random.Random(42)
    amount = _baseline_amount_sample(rng, "internal_transfer", rail=rail)
    assert isinstance(amount, Decimal)
    # Per-kind heuristic returns a Decimal — no range constraint.
    assert amount > Decimal("0")


def test_amount_picker_none_rail_falls_back_to_lognormal() -> None:
    """When ``rail=None``, the AB.5 path is skipped entirely (pre-AB.5
    callers that didn't pass rail = unaffected)."""
    rng = random.Random(42)
    amount = _baseline_amount_sample(rng, "internal_transfer")
    assert isinstance(amount, Decimal)
    assert amount > Decimal("0")
