"""DS.4 — the probabilistic tolerance contract for the anomaly matview.

The anomaly detector (``<prefix>_inv_pair_rolling_anomalies``) is the
one invariant excluded from residual typing by name (``MathKind.
PROBABILISTIC``): its z-score divides by a sample stddev, so the engine
value is irrational and exact set-equality — the enumeration gate's
comparator — cannot apply. This module is the replacement contract, and
it rests on one arithmetic fact:

    **z² is rational.** Window sums are integers; the per-pair mean and
    sample VARIANCE are exact in ℚ; and every bucket comparison
    ``ABS(z) < k`` is equivalent to ``z² < k²``. The square root never
    has to be taken — the LAW side of the contract carries zero
    floating-point error, computed here with ``Fraction`` end to end.

Only the ENGINE's z carries rounding (double on DuckDB, NUMERIC sqrt on
PG/Oracle). The contract absorbs that in two constants:

- ``ENGINE_ZSQ_EPSILON`` — relative tolerance for engine-vs-law z², mean
  and variance. Engine error on integer-cents inputs is ~1e-15 relative;
  1e-9 leaves six orders of headroom while still failing loudly on any
  real formula divergence (a wrong divisor, a dropped row, a frame-shape
  regression all move z² by far more).
- ``BAND_EDGE_DELTA`` — the z²-space margin around each bucket threshold
  inside which the law CLASSIFIES the row as band-edge. Interior rows
  (margin > δ) must match buckets exactly; band-edge rows may land on
  either adjacent bucket (the SQL's strict ``<`` puts an exact-threshold
  z in the upper bucket, but a last-bit rounding on another engine may
  flip it — both answers are correct readings of the same law). δ is
  three orders above ε, so any row the engine COULD misround across a
  threshold is always law-classified band-edge — the two constants'
  ordering is what makes the contract sound, and a test pins it.

The guard arms are EXACT on both sides, not tolerance-checked: the
min-n floor compares integers, and float stddev is zero iff the windows
are all equal (equal ints survive AVG exactly; deviations of integer
cents cannot underflow), which is exactly when the ℚ variance is zero.

Packing note: cells keyed by distinct (sender, recipient) account pairs
may share one database — every window aggregate in the matview
partitions per pair, so pair-keyed packing is interference-free (same
argument as the transfer-keyed packing contract in the DS.3.4 harness).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Final

from recon_gen.common.l2.schema import INV_MIN_HISTORICAL_WINDOWS
from recon_gen.common.spine.residuals import (
    MONEY_STATUSES,
    ResidualState,
    current_legs,
)

#: Bucket labels in ascending-|z| order — must match the matview CASE.
Z_BUCKETS: Final[tuple[str, ...]] = (
    "0-1 sigma", "1-2 sigma", "2-3 sigma", "3-4 sigma", "4+ sigma",
)

#: Bucket thresholds in z² space: ABS(z) < k  ⟺  z² < k².
_THRESHOLDS_ZSQ: Final[tuple[Fraction, ...]] = (
    Fraction(1), Fraction(4), Fraction(9), Fraction(16),
)

#: Relative tolerance for engine z² / mean / variance vs the exact law.
ENGINE_ZSQ_EPSILON: Final[Fraction] = Fraction(1, 10**9)

#: z²-space margin inside which a row is band-edge (adjacent-bucket rule).
BAND_EDGE_DELTA: Final[Fraction] = Fraction(1, 10**6)

_SCOPE_INTERNAL: Final = "internal"


@dataclass(frozen=True, slots=True)
class PairWindowLaw:
    """The exact-ℚ law value for one (sender, recipient, day) window."""

    window_sum: int
    transfer_count: int
    pair_n: int
    mean: Fraction
    variance: Fraction | None  # sample variance; None ⟺ pair_n == 1
    zsq: Fraction              # exact z²; 0 when a guard fires
    z_negative: bool           # window_sum < mean (sign of the law z)
    guard: str | None          # "floor" | "stddev0" | None (live z)
    bucket: str


def anomaly_reference(
    state: ResidualState,
) -> dict[tuple[str, str, date], PairWindowLaw]:
    """Exact-arithmetic mirror of the matview pipeline: supersession →
    pair legs (recipient +/Posted/internal-leaf × sender −/Posted, per
    LEG pair — a two-sender-leg transfer contributes its recipient
    amount once per sender leg, same as the SQL join) → per-day
    collapse → rolling [day−1, day] window → per-pair stats → guards →
    z² + bucket."""
    pair_rows: list[tuple[str, str, date, int, str]] = []
    legs = current_legs(state)
    for recipient in legs:
        if (
            recipient.amount.value <= 0
            or recipient.status not in MONEY_STATUSES
            or recipient.account_scope != _SCOPE_INTERNAL
            or recipient.account_parent_role is None
        ):
            continue
        for sender in legs:
            if (
                sender.transfer_id != recipient.transfer_id
                or sender.amount.value >= 0
                or sender.status not in MONEY_STATUSES
            ):
                continue
            pair_rows.append((
                sender.account_id,
                recipient.account_id,
                recipient.posting.date(),
                recipient.amount.value,
                recipient.transfer_id,
            ))

    day_sums: dict[tuple[str, str, date], int] = {}
    day_transfers: dict[tuple[str, str, date], set[str]] = {}
    for sender_id, recipient_id, day, amount, transfer_id in pair_rows:
        key = (sender_id, recipient_id, day)
        day_sums[key] = day_sums.get(key, 0) + amount
        day_transfers.setdefault(key, set()).add(transfer_id)

    pair_days: dict[tuple[str, str], list[date]] = {}
    for sender_id, recipient_id, day in day_sums:
        pair_days.setdefault((sender_id, recipient_id), []).append(day)

    out: dict[tuple[str, str, date], PairWindowLaw] = {}
    for (sender_id, recipient_id), days in pair_days.items():
        windows: dict[date, tuple[int, int]] = {}
        for day in days:
            frame = [
                d for d in days
                if (day - d).days in (0, 1)
            ]
            windows[day] = (
                sum(day_sums[(sender_id, recipient_id, d)] for d in frame),
                sum(
                    len(day_transfers[(sender_id, recipient_id, d)])
                    for d in frame
                ),
            )
        n = len(windows)
        sums = [w for w, _ in windows.values()]
        mean = Fraction(sum(sums), n)
        variance: Fraction | None = None
        if n >= 2:
            variance = sum(
                ((Fraction(w) - mean) ** 2 for w in sums), Fraction(0),
            ) / (n - 1)
        for day, (window_sum, transfer_count) in windows.items():
            if n < INV_MIN_HISTORICAL_WINDOWS:
                guard, zsq = "floor", Fraction(0)
            elif variance == 0:
                # n >= floor >= 2 here, so variance is never None on
                # this arm — the engine's COALESCE(STDDEV_SAMP, 0) only
                # matters at n=1, which the floor arm already caught.
                guard, zsq = "stddev0", Fraction(0)
            else:
                assert variance is not None
                guard = None
                zsq = (Fraction(window_sum) - mean) ** 2 / variance
            out[(sender_id, recipient_id, day)] = PairWindowLaw(
                window_sum=window_sum,
                transfer_count=transfer_count,
                pair_n=n,
                mean=mean,
                variance=variance,
                zsq=zsq,
                z_negative=Fraction(window_sum) < mean,
                guard=guard,
                bucket=Z_BUCKETS[0] if guard else bucket_of_zsq(zsq),
            )
    return out


def bucket_of_zsq(zsq: Fraction) -> str:
    """The matview CASE in z² space — strict ``<`` per threshold, so an
    exact-threshold z² lands in the UPPER bucket (mirrors SQL)."""
    for i, threshold in enumerate(_THRESHOLDS_ZSQ):
        if zsq < threshold:
            return Z_BUCKETS[i]
    return Z_BUCKETS[-1]


def band_distance(zsq: Fraction) -> Fraction:
    """Distance (z² space) from the nearest bucket threshold."""
    return min(abs(zsq - t) for t in _THRESHOLDS_ZSQ)


def is_band_edge(zsq: Fraction) -> bool:
    return band_distance(zsq) <= BAND_EDGE_DELTA


def adjacent_buckets(zsq: Fraction) -> tuple[str, str]:
    """The two buckets flanking the threshold nearest to ``zsq`` — the
    acceptable engine answers for a band-edge row."""
    nearest = min(
        range(len(_THRESHOLDS_ZSQ)),
        key=lambda i: abs(zsq - _THRESHOLDS_ZSQ[i]),
    )
    return (Z_BUCKETS[nearest], Z_BUCKETS[nearest + 1])


def _within(engine: Fraction, law: Fraction) -> bool:
    """Relative-with-absolute-floor tolerance: |e − l| ≤ ε·(1 + |l|)."""
    return abs(engine - law) <= ENGINE_ZSQ_EPSILON * (1 + abs(law))


def check_engine_row(
    law: PairWindowLaw,
    *,
    engine_window_sum: int,
    engine_transfer_count: int,
    engine_mean: Fraction,
    engine_stddev: Fraction,
    engine_z: Fraction,
    engine_bucket: str,
) -> list[str]:
    """The contract's teeth: every discrepancy between an engine row
    and its law value, as human-readable strings (empty = row passes).
    Integer layer exact; guard arms exact; z² within ε; sign exact when
    the law z is nonzero; bucket exact when ε-interior, adjacent when
    band-edge."""
    problems: list[str] = []
    if engine_window_sum != law.window_sum:
        problems.append(
            f"window_sum {engine_window_sum} != exact {law.window_sum}",
        )
    if engine_transfer_count != law.transfer_count:
        problems.append(
            f"transfer_count {engine_transfer_count} != exact "
            f"{law.transfer_count}",
        )
    if not _within(engine_mean, law.mean):
        problems.append(f"pop_mean {engine_mean} vs law {law.mean}")
    law_variance = law.variance if law.variance is not None else Fraction(0)
    if not _within(engine_stddev ** 2, law_variance):
        problems.append(
            f"pop_stddev² {engine_stddev ** 2} vs law variance "
            f"{law_variance}",
        )
    if law.guard is not None:
        # Guard arms emit LITERAL zeros in the CASE — exact, not ε.
        if engine_z != 0:
            problems.append(
                f"guard arm '{law.guard}' but engine z = {engine_z}",
            )
        if engine_bucket != Z_BUCKETS[0]:
            problems.append(
                f"guard arm '{law.guard}' but engine bucket "
                f"{engine_bucket!r}",
            )
        return problems
    if not _within(engine_z ** 2, law.zsq):
        problems.append(f"z² {engine_z ** 2} vs law {law.zsq}")
    if law.zsq != 0 and (engine_z < 0) != law.z_negative:
        problems.append(
            f"z sign: engine {engine_z} vs law negative={law.z_negative}",
        )
    if is_band_edge(law.zsq):
        if engine_bucket not in adjacent_buckets(law.zsq):
            problems.append(
                f"band-edge z²={law.zsq}: engine bucket {engine_bucket!r} "
                f"not in adjacent {adjacent_buckets(law.zsq)}",
            )
    elif engine_bucket != law.bucket:
        problems.append(
            f"interior z²={float(law.zsq):.6f}: engine bucket "
            f"{engine_bucket!r} != law {law.bucket!r}",
        )
    return problems
