"""inv_pair_rolling_anomalies — EXCLUDED from enumeration, by design.

Written rationale (DS.0 §3 row 12, signed): the detector is
PROBABILISTIC — its output is a z-score against a rolling mean/stddev
and a bucket label, not a law with an exact residual. ``MathKind.
PROBABILISTIC`` carries NO residual by definition (the kit's signature
lock), so there is no ``{cells : residual != 0}`` set for an
enumeration comparator to check the engine against: any "expected
z-score" the harness computed would be a reimplementation of the
detector's own arithmetic, verifying the SQL against itself — exactly
the transliteration failure mode the DS.1 authoring rule exists to
prevent.

What owns it instead:

- DS.4's TOLERANCE CONTRACT: band definitions, the min-n floor and
  the stddev=0 guard, with band-edge epsilon semantics;
- DS.3.6's metamorphic laws for the INTEGER window layer (the rolling
  2-day SUM under insert-order permutation / supersession
  idempotence), which is exact and does admit law-shaped tests;
- the DS.3.3b supersession alignment (its base-table read now goes
  through Current*), pinned by test_ds33b.

This module exists so the exclusion is a named, greppable decision
next to its peers rather than a silent absence (finding 7: registries
fail silently — so do domain registries).
"""
from __future__ import annotations

from typing import Final

EXCLUSION: Final = (
    "probabilistic detector — no exact residual exists; tolerance "
    "contract owned by DS.4, integer window layer by DS.3.6 metamorphic "
    "laws"
)
