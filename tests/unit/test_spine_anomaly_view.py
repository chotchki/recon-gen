"""Unit tests for AT.2's `AnomalyView` — the σ-threshold knob that
slices over `AnomalyInvariant.detect()`'s full output set.

The split AT.2 locks in: the matview emits every (pair, window) row;
the detector returns every row; the View decides "is this anomalous?"
based on `sigma_threshold`. The detector's output is stable across
threshold choices — no re-query needed to compare 2σ vs 3σ vs 4σ.

What's pinned:

1. Default `sigma_threshold=3.0` reproduces AT.1's baked-in behaviour
   (includes '3-4 sigma' + '4+ sigma' only).
2. Threshold semantics — bucket is included iff its lower bound is
   ≥ threshold (the bucket → σ map is `BUCKET_LOWER_BOUNDS`).
3. `sigma_threshold=0.0` returns the full input set.
4. `sigma_threshold=5.0` returns empty (nothing exceeds '4+ sigma'
   lower bound of 4).
5. Threshold raises = smaller subset; threshold lowers = larger
   subset (monotonic).
6. Bucket vocab consistency — every bucket in `BUCKET_LOWER_BOUNDS`
   matches a CASE branch in the anomaly matview SQL (shape-drift
   guard).
7. Non-anomaly violations passed in by mistake (no z_bucket key) are
   dropped silently — the View is defensive, not lossy on the wrong
   shape.
8. Unknown bucket strings raise `KeyError` (matview shape drift fails
   loud).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from recon_gen.common.spine import (
    BUCKET_LOWER_BOUNDS,
    AnomalyView,
    Violation,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic violation factory (no DB).
# ---------------------------------------------------------------------------


def _viol(bucket: str, *, sender: str = "s", recipient: str = "r") -> Violation:
    return Violation.of(
        "inv_pair_rolling_anomalies",
        sender_account_id=sender,
        recipient_account_id=recipient,
        window_end=date(2030, 1, 1),
        z_bucket=bucket,
    )


def _all_buckets() -> set[Violation]:
    # One violation per bucket, distinct sender so set-equality works.
    return {_viol(b, sender=f"s-{b}") for b in BUCKET_LOWER_BOUNDS}


# ---------------------------------------------------------------------------
# Defaults + threshold semantics.
# ---------------------------------------------------------------------------


def test_default_threshold_is_three_sigma() -> None:
    """The AT.1 baked-in cutoff lives on the View now as the default
    — analyst convention: "anomaly" starts at 3σ."""
    assert AnomalyView().sigma_threshold == 3.0


def test_default_slice_matches_at1_baked_in_filter() -> None:
    """Default `AnomalyView(3.0).slice(...)` returns exactly the
    '3-4 sigma' + '4+ sigma' rows — the set AT.1's detector used to
    return on its own. Backwards-compat assertion."""
    sliced = AnomalyView().slice(_all_buckets())
    buckets = {dict(v.identity)["z_bucket"] for v in sliced}
    assert buckets == {"3-4 sigma", "4+ sigma"}


def test_threshold_zero_returns_everything() -> None:
    """0σ threshold includes every bucket — no filter."""
    sliced = AnomalyView(sigma_threshold=0.0).slice(_all_buckets())
    assert sliced == _all_buckets()


def test_threshold_above_max_returns_empty() -> None:
    """Threshold above '4+ sigma''s lower bound (4) → empty slice.
    Threshold of 5.0 demonstrates the boundary."""
    sliced = AnomalyView(sigma_threshold=5.0).slice(_all_buckets())
    assert sliced == set()


def test_bucket_lower_bound_semantics_are_inclusive() -> None:
    """A bucket is included iff `BUCKET_LOWER_BOUNDS[bucket] >=
    sigma_threshold`. Threshold=2.0 includes '2-3 sigma' (lower=2.0
    matches exactly). Pins the inclusive-edge semantics."""
    sliced = AnomalyView(sigma_threshold=2.0).slice(_all_buckets())
    buckets = {dict(v.identity)["z_bucket"] for v in sliced}
    assert buckets == {"2-3 sigma", "3-4 sigma", "4+ sigma"}


@pytest.mark.parametrize(
    "threshold,expected_buckets",
    [  # pyright: ignore[reportUnknownArgumentType]: argvalues to pytest.parametrize
        (0.0, {"0-1 sigma", "1-2 sigma", "2-3 sigma", "3-4 sigma", "4+ sigma"}),
        (1.0, {"1-2 sigma", "2-3 sigma", "3-4 sigma", "4+ sigma"}),
        (2.0, {"2-3 sigma", "3-4 sigma", "4+ sigma"}),
        (3.0, {"3-4 sigma", "4+ sigma"}),
        (4.0, {"4+ sigma"}),
        (5.0, set()),
    ],
)
def test_threshold_parametrized(
    threshold: float, expected_buckets: set[str],
) -> None:
    """The full threshold-to-buckets table, locked."""
    sliced = AnomalyView(sigma_threshold=threshold).slice(_all_buckets())
    assert {dict(v.identity)["z_bucket"] for v in sliced} == expected_buckets


def test_threshold_is_monotonic() -> None:
    """Raising the threshold can only shrink the slice; lowering can
    only grow it. Pure-function property the analyst surface depends
    on for "tighten the filter" UX."""
    universe = _all_buckets()
    sizes = [
        len(AnomalyView(sigma_threshold=t).slice(universe))
        for t in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
    ]
    # Strictly non-increasing as threshold rises.
    assert sizes == sorted(sizes, reverse=True), (
        f"slice sizes must be monotonically non-increasing in "
        f"threshold; got {sizes}"
    )


# ---------------------------------------------------------------------------
# Bucket vocab integrity with the matview SQL.
# ---------------------------------------------------------------------------


def test_anomaly_view_bucket_vocab_matches_matview() -> None:
    """The bucket strings in `BUCKET_LOWER_BOUNDS` MUST match the CASE
    branch literals in the anomaly matview SQL — schema-shape drift
    here means the View can't slice correctly, fails loud.

    Reads `common/l2/schema.py` and checks every bucket appears as a
    literal in the file. Cheap shape-drift guard; the alternative is
    a real DB read which is heavier."""
    schema_py = (
        Path(__file__).resolve().parents[2]
        / "src" / "recon_gen" / "common" / "l2" / "schema.py"
    )
    text = schema_py.read_text()
    for bucket in BUCKET_LOWER_BOUNDS:
        assert f"'{bucket}'" in text, (
            f"bucket vocab drift: '{bucket}' is in BUCKET_LOWER_BOUNDS "
            f"but NOT in {schema_py.name}'s anomaly matview SQL. Either "
            f"the matview changed labels (update BUCKET_LOWER_BOUNDS) "
            f"or the constant gained a typo."
        )


# ---------------------------------------------------------------------------
# Defensive behaviour: foreign shapes + unknown buckets.
# ---------------------------------------------------------------------------


def test_non_anomaly_violations_dropped_silently() -> None:
    """A drift Violation has no z_bucket key. If someone passes it in
    by mistake (cross-invariant mix), the View drops it rather than
    raising — same defensive shape as L1 detectors that filter on
    invariant name."""
    drift = Violation.of(
        "drift",
        account_id="acct-1",
        balance_date=date(2030, 1, 1),
    )
    anomaly = _viol("4+ sigma")
    sliced = AnomalyView().slice({drift, anomaly})
    assert sliced == {anomaly}


def test_unknown_bucket_raises_keyerror() -> None:
    """If the matview ever emits a NEW bucket (e.g. '5+ sigma' from a
    schema bump) and `BUCKET_LOWER_BOUNDS` isn't updated, the View
    should fail loud rather than silently drop. The KeyError surfaces
    at the slice site, not in production data."""
    foreign = _viol("99+ sigma")  # not in BUCKET_LOWER_BOUNDS
    with pytest.raises(KeyError):
        AnomalyView().slice({foreign})


def test_empty_input_returns_empty_at_any_threshold() -> None:
    """No anomaly rows → empty slice, regardless of threshold."""
    for t in (0.0, 3.0, 99.0):
        assert AnomalyView(sigma_threshold=t).slice(set()) == set()
