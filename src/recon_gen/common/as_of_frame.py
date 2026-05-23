"""The owned temporal frame (D1).

`AsOfFrame` is the single anchor both the generator and the views read in
place of wall-clock ``now()``. See `docs/audits/date_range_model_audit.md`
§5 ("step back — time is the unowned coordinate") and the AP.0 spike
result for the reasoning. Promoted from `tests/unit/test_ap0_as_of_frame.py`
by Phase AQ.

Two bindings:

  * `AsOfFrame.locked()` — anchor pinned at `LOCKED_ANCHOR` (the canonical
    demo date). Deterministic; locked seeds + tests stay byte-identical
    across runs.
  * `AsOfFrame.live()` — anchor = `date.today()`. Data ends-at-now in
    production. Same code path as `locked()` — only the bound value
    differs.

What is intentionally NOT here: renderer-specific derivations (QS
`RollingDate` exprs, App2 sentinels, picker defaults). Those live on the
`View` primitive (Phase AR) which takes an `AsOfFrame` as its anchor; one
view object emits both QS and App2 bindings so the C1 dual-default split
becomes unrepresentable. The frame's job is just to own ``as_of``.

`LOCKED_ANCHOR` here will become the single source of truth in AQ.3; today
`cli/data.py::_CANONICAL_LOCK_ANCHOR` carries the same value and is the
caller for byte-locked seed emission. A unit-level link-test in
`tests/unit/test_as_of_frame.py` asserts they agree, catching drift until
the AQ.3 funnel collapses them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

#: The canonical demo anchor — every locked seed + every locked-binding
#: frame anchors here. Matches `cli/data.py::_CANONICAL_LOCK_ANCHOR`
#: pending the AQ.3 funnel that makes this the sole source.
LOCKED_ANCHOR: Final[date] = date(2030, 1, 1)


@dataclass(frozen=True)
class AsOfFrame:
    """The owned temporal anchor (`as_of`) + a look-back span.

    Every temporal predicate the system reads — the generator's data-end
    day, a view's window bounds, "latest" semantics — derives from this
    one object instead of wall-clock ``now()``. That is what makes the
    locked-vs-live split a single binding choice rather than two
    independent encodings (the C1 release-blocker shape).
    """

    as_of: date
    window_days: int

    @classmethod
    def locked(cls, *, window_days: int = 0) -> "AsOfFrame":
        """Demo/test binding — anchor pinned at `LOCKED_ANCHOR`."""
        return cls(as_of=LOCKED_ANCHOR, window_days=window_days)

    @classmethod
    def live(cls, *, window_days: int = 0) -> "AsOfFrame":
        """Production binding — anchor = today. Same derivations as
        `locked()`; only the bound anchor value differs (the §8
        determinism story falls out of the frame for free)."""
        return cls(
            as_of=date.today(),  # typing-smell: ignore[no-datetime-now]: AsOfFrame.live() is the SINGLE blessed wall-clock read — every other site reads frame.as_of; AQ.3 funnels the 4 ad-hoc date.today() fallbacks through this constructor
            window_days=window_days,
        )

    @property
    def window_start(self) -> date:
        """The look-back's lower bound: ``as_of - window_days``."""
        return self.as_of - timedelta(days=self.window_days)

    def contains(self, day: date) -> bool:
        """Is ``day`` inside ``[window_start, as_of]`` (inclusive)?

        A required-coverage helper: when used to assert a planted
        violation lands inside the window the view will scan, the plant
        ⟷ query-window contract becomes a property of the frame rather
        than developer-memory.
        """
        return self.window_start <= day <= self.as_of
