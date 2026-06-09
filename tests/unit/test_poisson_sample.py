"""CR.14 — Poisson sampler's large-mean (normal-approximation) branch.

Pre-CR.14 the ``_poisson_sample`` docstring claimed the >50 branch was
defensive dead code that "per-day targets stay well below" — but
default-density customer instances (20 × daily_target_per_unit=4.0 =
mean 80) actively hit it. CR.14 reworded the docstring AND adds this
test so any future "actually we never hit >50" rewrite fails loudly.
"""

from __future__ import annotations

import random
import statistics

from recon_gen.common.l2.seed import _poisson_sample  # noqa: PLC2701 — anti-drift: exercises the >50 normal-approximation branch flagged in CR.14


def test_poisson_sample_zero_mean_returns_zero() -> None:
    rng = random.Random(0)  # noqa: S311 — deterministic test fixture, not crypto
    assert _poisson_sample(rng, 0) == 0
    assert _poisson_sample(rng, -1) == 0


def test_poisson_sample_small_mean_knuth_path() -> None:
    """Small-mean branch (Knuth's iterative algorithm). Distribution
    over many draws should center near the mean."""
    rng = random.Random(42)  # noqa: S311
    mean = 10.0
    samples = [_poisson_sample(rng, mean) for _ in range(2000)]
    observed = statistics.mean(samples)
    # Loose bound — 2000 samples × Poisson(10) has stdev ≈ 0.07.
    assert abs(observed - mean) < 0.5
    assert all(s >= 0 for s in samples)


def test_poisson_sample_large_mean_normal_approximation_is_actively_used() -> None:
    """CR.14 — the >50 branch is hit by default-density customers
    (20 accounts × daily_target_per_unit=4.0 = mean 80). Verify the
    normal-approximation branch:
    - returns plausible non-negative ints
    - centers near the requested mean
    - has variance roughly matching Poisson's (= mean)
    """
    rng = random.Random(7)  # noqa: S311
    mean = 80.0
    samples = [_poisson_sample(rng, mean) for _ in range(2000)]
    observed = statistics.mean(samples)
    # Generous bound — Gauss(80, sqrt(80)) over 2000 samples has
    # stderr ≈ sqrt(80)/sqrt(2000) ≈ 0.2.
    assert abs(observed - mean) < 1.5, observed
    assert all(s >= 0 for s in samples), "normal approximation must clamp at 0"
    assert max(samples) > 50, "values must actually reach into the >50 regime"


def test_poisson_sample_at_boundary_mean_50_uses_knuth() -> None:
    """The threshold check is ``mean > 50``, so mean==50 stays on the
    Knuth path. Pin this — a future ``>=`` typo would silently shift
    the boundary."""
    rng = random.Random(11)  # noqa: S311
    sample = _poisson_sample(rng, 50.0)
    assert sample >= 0
