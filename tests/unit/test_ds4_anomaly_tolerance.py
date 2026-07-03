"""DS.4 — the anomaly tolerance contract, checked engine-vs-law.

The law side (``common/spine/anomaly_contract.py``) is exact ℚ: z² is
rational, buckets are decided by rational comparison, guards are integer
comparisons. The engine side carries float/NUMERIC rounding. This module
sweeps a packed pair domain through both and asserts the contract:

- integer layer (window_sum, transfer_count) EXACT,
- mean / variance / z² within ``ENGINE_ZSQ_EPSILON`` (relative),
- z sign exact whenever the law z is nonzero,
- bucket EXACT when the law z² is ε-interior; when the law z² sits
  within ``BAND_EDGE_DELTA`` of a threshold, either adjacent bucket is
  a correct engine answer (the SQL's strict ``<`` reads exact-threshold
  rows into the upper bucket; a last-bit rounding elsewhere may not).

Packing: all pairs share one database — every matview aggregate
partitions per (sender, recipient), so pair-keyed packing is
interference-free (the DS.3.4 packing-contract argument).

The band-edge witness is the arithmetic-progression history
{100, 200, 300} on gap-spaced days: its endpoints sit at EXACTLY
z = ∓1 (mean 200, sample stddev 100) — a deterministic, dialect-stable
threshold hit with no float search involved.
"""
from __future__ import annotations

import datetime as dt
from fractions import Fraction
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.l2.schema import INV_MIN_HISTORICAL_WINDOWS
from recon_gen.common.spine.anomaly_contract import (
    BAND_EDGE_DELTA,
    ENGINE_ZSQ_EPSILON,
    PairWindowLaw,
    Z_BUCKETS,
    adjacent_buckets,
    anomaly_reference,
    band_distance,
    bucket_of_zsq,
    check_engine_row,
    is_band_edge,
)
from tests.enumeration import metamorphic as meta
from tests.enumeration.domains._base import WINDOW_START
from tests.enumeration.harness import CellBuilder

_NOON: Final = dt.time(12, 0)

AnomKey = tuple[str, str, dt.date]


def _gap_day(i: int) -> dt.date:
    """Active days spaced 2 apart — each rolling [day−1, day] window
    covers exactly its own day, so window sums equal day sums."""
    return WINDOW_START + dt.timedelta(days=2 * i)


def _gap_history(b: CellBuilder, pair: str, amounts: tuple[int, ...]) -> None:
    for i, amount in enumerate(amounts):
        meta.pair_transfer(
            b, transfer=f"{pair}t{i}", sender=f"{pair}s",
            recipient=f"{pair}r", day=_gap_day(i), amount=amount,
        )


def _domain_builder() -> CellBuilder:
    b = CellBuilder()
    # Band-edge witness: AP endpoints at exactly z = ∓1.
    _gap_history(b, "ds4ap", (100, 200, 300))
    # One-outlier-among-k-equals families: z² = k²/n exactly (shape-
    # only, magnitude-free), landing interior in each live bucket.
    _gap_history(b, "ds4b1", (100,) * 3 + (700,))      # spike z² = 9/4
    _gap_history(b, "ds4b2", (100,) * 7 + (3000,))     # z² = 49/8
    _gap_history(b, "ds4b3", (100,) * 11 + (10000,))   # z² = 121/12
    _gap_history(b, "ds4b4", (100,) * 18 + (100000,))  # z² = 324/19
    # Below-mean dip — the negative-z sign lane (z² = 16/5).
    _gap_history(b, "ds4ng", (2000, 2000, 2000, 2000, 100))
    # Guard arms: below-floor, single-window (COALESCE lane), flat.
    _gap_history(b, "ds4fl", (100, 5000))
    _gap_history(b, "ds4one", (750,))
    _gap_history(b, "ds4fz", (500, 500, 500))
    # Dense run + hole — windows genuinely straddle two days.
    for i, (offset, amount) in enumerate(
        ((0, 300), (1, 500), (2, 200), (4, 900)),
    ):
        meta.pair_transfer(
            b, transfer=f"ds4dnt{i}", sender="ds4dns", recipient="ds4dnr",
            day=WINDOW_START + dt.timedelta(days=offset), amount=amount,
        )
    # Multi-transfer day + a two-sender-leg transfer (the SQL join
    # contributes the recipient amount once PER sender leg).
    meta.pair_transfer(
        b, transfer="ds4mut0", sender="ds4mus", recipient="ds4mur",
        day=_gap_day(0), amount=100,
    )
    meta.pair_transfer(
        b, transfer="ds4mut1", sender="ds4mus", recipient="ds4mur",
        day=_gap_day(0), amount=200,
    )
    posting_multi = dt.datetime.combine(_gap_day(1), _NOON)
    b.leg(
        id="ds4mut2sa", account="ds4mus", amount=-60, status=POSTED_STATUS,
        posting=posting_multi, transfer="ds4mut2", rail="AnomRail",
        parent_role=None,
    )
    b.leg(
        id="ds4mut2sb", account="ds4mus", amount=-40, status=POSTED_STATUS,
        posting=posting_multi, transfer="ds4mut2", rail="AnomRail",
        parent_role=None,
    )
    b.leg(
        id="ds4mut2r", account="ds4mur", amount=100, status=POSTED_STATUS,
        posting=posting_multi, transfer="ds4mut2", rail="AnomRail",
    )
    meta.pair_transfer(
        b, transfer="ds4mut3", sender="ds4mus", recipient="ds4mur",
        day=_gap_day(2), amount=700,
    )
    # Status + supersession lane: three live days {100, 900→300, 100};
    # the 900 is superseded down to 300 (same leg ids, higher entry);
    # a Pending-recipient day and a Failed-sender day mint NO window.
    _gap_history(b, "ds4mx", (100, 900, 100))
    meta.pair_transfer(
        b, transfer="ds4mxt1", sender="ds4mxs", recipient="ds4mxr",
        day=_gap_day(1), amount=300,
    )
    posting_pending = dt.datetime.combine(_gap_day(3), _NOON)
    b.leg(
        id="ds4mxp_s", account="ds4mxs", amount=-500, status="Pending",
        posting=posting_pending, transfer="ds4mxp", rail="AnomRail",
        parent_role=None,
    )
    b.leg(
        id="ds4mxp_r", account="ds4mxr", amount=500, status="Pending",
        posting=posting_pending, transfer="ds4mxp", rail="AnomRail",
    )
    posting_failed = dt.datetime.combine(_gap_day(4), _NOON)
    b.leg(
        id="ds4mxf_s", account="ds4mxs", amount=-500, status="Failed",
        posting=posting_failed, transfer="ds4mxf", rail="AnomRail",
        parent_role=None,
    )
    b.leg(
        id="ds4mxf_r", account="ds4mxr", amount=500, status=POSTED_STATUS,
        posting=posting_failed, transfer="ds4mxf", rail="AnomRail",
    )
    return b


_CACHE: list[
    tuple[dict[AnomKey, meta.AnomalyRow], dict[AnomKey, PairWindowLaw]]
] = []


def _domain() -> tuple[
    dict[AnomKey, meta.AnomalyRow], dict[AnomKey, PairWindowLaw],
]:
    if not _CACHE:
        b = _domain_builder()
        law = anomaly_reference(b.state())
        tx, bal = meta.entried(b)
        db = meta.build_db(tx, bal)
        try:
            engine = meta.read_anomalies(db)
        finally:
            db.close()
        assert engine, "packed anomaly domain produced no engine rows"
        _CACHE.append((engine, law))
    return _CACHE[0]


def test_engine_matches_law_across_the_packed_domain() -> None:
    """Every engine row satisfies the contract against its exact-ℚ law
    value; key sets match both directions; and the sweep is non-vacuous
    in every dimension the contract distinguishes (all five buckets
    interior, a band-edge row, both guard arms, a negative z, a
    multi-transfer window, a two-day window)."""
    engine, law = _domain()
    assert set(engine) == set(law), (
        sorted(set(engine) ^ set(law)),
    )
    failures: list[str] = []
    for key, row in sorted(engine.items()):
        problems = check_engine_row(
            law[key],
            engine_window_sum=row.window_sum,
            engine_transfer_count=row.transfer_count,
            engine_mean=Fraction(row.pop_mean),
            engine_stddev=Fraction(row.pop_stddev),
            engine_z=Fraction(row.z_score),
            engine_bucket=row.z_bucket,
        )
        failures.extend(f"{key}: {p}" for p in problems)
    assert not failures, "\n".join(failures)
    interior_buckets = {
        v.bucket for v in law.values()
        if v.guard is None and not is_band_edge(v.zsq)
    }
    assert interior_buckets == set(Z_BUCKETS), interior_buckets
    assert any(
        v.guard is None and is_band_edge(v.zsq) for v in law.values()
    ), "no band-edge row — the adjacent-bucket arm never exercised"
    assert {v.guard for v in law.values() if v.guard} == {
        "floor", "stddev0",
    }
    assert any(
        v.guard is None and v.z_negative and v.zsq > 1 for v in law.values()
    ), "no live negative-z row"
    assert any(v.transfer_count > 1 for v in law.values())
    dense_d1 = law[("ds4dns", "ds4dnr", WINDOW_START + dt.timedelta(days=1))]
    assert dense_d1.window_sum == 800, (
        "dense pair's [day−1, day] window should straddle both days "
        f"(300 + 500), got {dense_d1.window_sum}"
    )


def test_band_edge_witness_sits_exactly_on_z1() -> None:
    """The AP history's endpoints carry z² == 1 EXACTLY (rational
    arithmetic, no tolerance): distance to the threshold is zero, the
    law classifies them band-edge, and the engine's answer is one of
    the two adjacent buckets. The law's own strict-< mirror reads an
    exact threshold into the upper bucket, same as the SQL."""
    engine, law = _domain()
    endpoints = [
        (("ds4aps", "ds4apr", _gap_day(0))),
        (("ds4aps", "ds4apr", _gap_day(2))),
    ]
    for key in endpoints:
        row = law[key]
        assert row.zsq == Fraction(1), row
        assert band_distance(row.zsq) == 0
        assert is_band_edge(row.zsq)
        assert engine[key].z_bucket in adjacent_buckets(row.zsq)
    middle = law[("ds4aps", "ds4apr", _gap_day(1))]
    assert middle.zsq == Fraction(0)
    assert law[endpoints[0]].z_negative is True
    assert law[endpoints[1]].z_negative is False
    assert bucket_of_zsq(Fraction(1)) == "1-2 sigma"
    assert adjacent_buckets(Fraction(1)) == ("0-1 sigma", "1-2 sigma")


def test_guard_arms_are_exact_on_both_sides() -> None:
    """Guards are integer/equality decisions — no tolerance on either
    side. Below-floor (n=2), single-window (the COALESCE lane, n=1) and
    flat (stddev exactly 0 at n ≥ floor) all read literal zero z and
    the floor bucket from the engine."""
    engine, law = _domain()
    floor_keys = [k for k in law if k[0] == "ds4fls"]
    one_keys = [k for k in law if k[0] == "ds4ones"]
    flat_keys = [k for k in law if k[0] == "ds4fzs"]
    assert len(floor_keys) == 2 and len(one_keys) == 1
    assert len(flat_keys) == 3
    for key in floor_keys + one_keys:
        assert law[key].guard == "floor"
        assert law[key].pair_n < INV_MIN_HISTORICAL_WINDOWS
        assert engine[key].z_score == 0
        assert engine[key].z_bucket == Z_BUCKETS[0]
    for key in flat_keys:
        assert law[key].guard == "stddev0"
        assert law[key].variance == 0
        assert engine[key].pop_stddev == 0
        assert engine[key].z_score == 0
        assert engine[key].z_bucket == Z_BUCKETS[0]
    # The flat pair proves the arm split: it clears the floor, so the
    # zero must come from the stddev arm, not insufficient history.
    assert law[flat_keys[0]].pair_n >= INV_MIN_HISTORICAL_WINDOWS


def test_supersession_and_status_filters_flow_through_the_law() -> None:
    """The mixstat pair documents the input semantics: the superseded
    900 is invisible (current 300 wins), the Pending-recipient and
    Failed-sender days mint no window at all — three windows
    {100, 300, 100}, nothing else."""
    _, law = _domain()
    mx = {k: v for k, v in law.items() if k[0] == "ds4mxs"}
    assert sorted(k[2] for k in mx) == [_gap_day(0), _gap_day(1), _gap_day(2)]
    sums = {k[2]: v.window_sum for k, v in mx.items()}
    assert sums == {_gap_day(0): 100, _gap_day(1): 300, _gap_day(2): 100}


def _synthetic_law(
    zsq: Fraction, *, guard: str | None = None,
) -> PairWindowLaw:
    return PairWindowLaw(
        window_sum=300, transfer_count=1, pair_n=3, mean=Fraction(200),
        variance=Fraction(10000), zsq=zsq, z_negative=False, guard=guard,
        bucket=Z_BUCKETS[0] if guard else bucket_of_zsq(zsq),
    )


def _check_synthetic(
    law_row: PairWindowLaw, *, engine_z: Fraction, engine_bucket: str,
) -> list[str]:
    """The synthetic-law rows share exact integer-layer/stat values so
    only the z + bucket arms are in play."""
    return check_engine_row(
        law_row, engine_window_sum=300, engine_transfer_count=1,
        engine_mean=Fraction(200), engine_stddev=Fraction(100),
        engine_z=engine_z, engine_bucket=engine_bucket,
    )


def test_comparator_arms_reject_what_they_must() -> None:
    """The comparator's own teeth: a wrong interior bucket fails, a
    band-edge row accepts BOTH adjacent buckets and nothing further
    out, a guard row rejects any nonzero engine z."""
    interior = _synthetic_law(Fraction(5))  # z ≈ 2.24, interior 2-3
    # √5 to double precision — z² lands within ~2e-16 of 5, far inside ε.
    z = Fraction(22360679774997896, 10**16)
    assert not _check_synthetic(
        interior, engine_z=z, engine_bucket="2-3 sigma",
    )
    assert _check_synthetic(
        interior, engine_z=z, engine_bucket="1-2 sigma",
    )
    edge = _synthetic_law(Fraction(4))  # exactly z = 2
    for acceptable in ("1-2 sigma", "2-3 sigma"):
        assert not _check_synthetic(
            edge, engine_z=Fraction(2), engine_bucket=acceptable,
        )
    assert _check_synthetic(
        edge, engine_z=Fraction(2), engine_bucket="3-4 sigma",
    )
    guard = _synthetic_law(Fraction(0), guard="floor")
    assert _check_synthetic(
        guard, engine_z=Fraction(1, 1000), engine_bucket=Z_BUCKETS[0],
    )
    assert not _check_synthetic(
        guard, engine_z=Fraction(0), engine_bucket=Z_BUCKETS[0],
    )


def test_delta_dominates_epsilon() -> None:
    """The soundness ordering: any row the engine could misround across
    a threshold (error ≤ ε-scale) must be law-classified band-edge, so
    δ must dominate ε by orders of magnitude — pinned, not assumed."""
    assert BAND_EDGE_DELTA >= 1000 * ENGINE_ZSQ_EPSILON
