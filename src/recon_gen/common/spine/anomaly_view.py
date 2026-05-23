"""Anomaly View — owns the σ-threshold (AP.3 finding #3 lock).

The `AnomalyInvariant` detector reads every row in
``<prefix>_inv_pair_rolling_anomalies`` — every (pair, window_end) tuple
the matview computed, across every z_bucket. The threshold for "is this
anomalous?" lives on the **View**, not the detector. AT.2 lands that
separation.

The split mirrors the L1 design (detector returns the matview's truth;
the analyst/View slices over it). Three reasons:

1. **Same data, multiple thresholds.** An investigator might want 3σ at
   triage time but 2σ during deep-dive; the same detect() result feeds
   both — no re-query.
2. **Threshold isn't a property of the invariant.** The "this is
   suspicious" judgement is analyst-facing — same as money_trail's
   depth-threshold (AT.3 territory) — so it belongs on the View knob
   the analyst configures, not the math the matview pins.
3. **Composition.** A `SeverityView` could compose threshold + window
   length + minimum transfer count; AT.2's `AnomalyView` is just the
   threshold knob, but the slice-over-detected-violations shape extends
   cleanly.

The bucket → σ lower-bound mapping is **fixed by the matview SQL**
(`common/l2/schema.py`'s anomaly CASE branches); this module re-encodes
it so the slice can compare numerically. Tests pin both directions —
schema-shape-drift on the bucket vocab fails loud here, not at the
analyst's surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from recon_gen.common.spine.violation import Violation


#: Lower bound (in σ) of each ``z_bucket`` label the matview emits. The
#: matview's CASE rounds |z| down to the nearest integer; '4+ sigma'
#: catches everything ≥ 4. Re-encoded here for the View slice; pinned
#: against the schema by ``test_anomaly_view_bucket_vocab_matches_matview``.
BUCKET_LOWER_BOUNDS: Final[dict[str, float]] = {
    "0-1 sigma": 0.0,
    "1-2 sigma": 1.0,
    "2-3 sigma": 2.0,
    "3-4 sigma": 3.0,
    "4+ sigma": 4.0,
}


@dataclass(frozen=True)
class AnomalyView:
    """Analyst-facing slice over the anomaly detector's full output.

    Holds the σ threshold for "include this bucket in the violation
    set." A violation with bucket B is included iff
    ``BUCKET_LOWER_BOUNDS[B] >= sigma_threshold`` — i.e. the bucket's
    lower edge is at-or-above the threshold. Defaults to 3.0 to match
    AT.1's baked-in cutoff (analyst convention: "anomaly" starts at 3σ).

    The View is pure (no IO; deterministic on its inputs); the
    detector still does the SQL read. `slice(violations)` is the only
    behaviour — other Views (depth-threshold for money_trail, etc.) will
    mirror this shape: pure projection over the detector's output set.
    """

    sigma_threshold: float = 3.0

    def slice(self, violations: set[Violation]) -> set[Violation]:
        """Return the subset of ``violations`` whose ``z_bucket``'s
        lower bound is ≥ ``sigma_threshold``. Violations with no
        z_bucket key (defensive — non-anomaly invariants would be
        passed by mistake) are dropped silently; the caller's job is
        to pass anomaly violations only.

        Bucket strings not in ``BUCKET_LOWER_BOUNDS`` raise ``KeyError``
        — that's a matview-shape drift signal, not a normal runtime
        case, so it should fail loud rather than silently drop.
        """
        out: set[Violation] = set()
        for v in violations:
            bucket = dict(v.identity).get("z_bucket")
            if bucket is None:
                continue
            if BUCKET_LOWER_BOUNDS[str(bucket)] >= self.sigma_threshold:
                out.add(v)
        return out
