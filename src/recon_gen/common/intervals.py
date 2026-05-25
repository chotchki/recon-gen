"""BC.1 — Typed time-range interval value types.

Four types, two responsibilities:

- **Time-range:** ``DateInterval`` (closed-closed ``[start, end]`` over
  calendar dates) and ``DateTimeInterval`` (half-open
  ``[start, end_exclusive)`` over naive timestamps). One named type per
  endpoint convention. The business-facing convention pairs with the
  granularity that uses it most: dates are closed-closed (audit reports
  say "week of May 17 - May 23" meaning both endpoints), timestamps are
  half-open (math + SQL ``col >= start AND col < end``).

- **Plant schedule:** ``SingleDayPlant`` (one day, derived from a window
  via ``at_window_end`` / ``at_window_start`` / ``at_offset_from_end``)
  and ``MultiDayPlant`` (spans every day, via ``spans``). Each plant
  generator declares which schedule it consumes in its type signature;
  the factory in ``plant_adapter.py`` constructs the right shape per
  generator from the test's window. No "where in the window do I
  plant?" convention lives in generator bodies — it lives in the type.

Single-TZ invariant
-------------------

All datetimes in this codebase are NAIVE (no ``tzinfo``); their LOCAL
meaning is the operator's machine TZ by convention (see
``project_local_tz_convention`` in the auto-memory). The system assumes
ONE consistent TZ end-to-end. ``DateTimeInterval.__post_init__`` rejects
aware datetimes at construction; ``DateInterval.as_half_open_datetimes()``
returns naive datetimes. The intervals layer refuses to be the place
where TZ policy lives.

Wiring-site enforcement
-----------------------

Two AST lints in ``tests/unit/test_typing_smells.py``:

- ``no-naked-interval-ctor``: bare ``DateInterval(...)`` / etc. calls
  outside this module must be a ``.classmethod_name(...)`` form. The
  named-convention constructors are the only way wiring sites should
  mint these.

- ``no-raw-temporal-args``: function/method parameters annotated
  ``date`` or ``datetime`` in ``src/recon_gen/**`` are a smell — wrap
  in one of these types or ``RunContext`` (Phase BD). Dataclass field
  annotations are unaffected (point values of real events, not
  policy-carrying params). Staged: enabled at end of BC.5 once the
  migration surface is wrapped.

Per ``feedback_invariants_in_types``: types bring meaning with them,
convention hides it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Self


@dataclass(frozen=True, slots=True)
class DateInterval:
    """Closed-closed ``[start, end]`` interval over calendar dates.

    Business-facing convention — audit reports, matview ``BETWEEN``
    clauses, dashboard date-range filters. For SQL/math half-open shape
    convert via ``.as_half_open_datetimes()`` — the +1-day flip lives
    inside that method, not at each callsite.

    Invariant: ``start <= end`` (a one-day interval is ``start == end``).
    """

    start: date
    end: date  # inclusive

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"DateInterval: end ({self.end}) precedes start "
                f"({self.start}). For an empty interval use Optional — "
                f"DateInterval is non-empty by construction."
            )

    # -- named-convention constructors --
    # The no-naked-interval-ctor AST lint requires wiring sites to call
    # one of these; bare ``DateInterval(s, e)`` is off-limits outside
    # this module.

    @classmethod
    def closed(cls, start: date, end: date) -> Self:
        """Explicit closed-closed construction."""
        return cls(start=start, end=end)

    @classmethod
    def single_day(cls, d: date) -> Self:
        """A one-day interval ``[d, d]``."""
        return cls(start=d, end=d)

    @classmethod
    def trailing_days_ending_yesterday(cls, today: date, days: int) -> Self:
        """``[today - days, today - 1]`` — the audit window convention.

        N days, ending on the most-recently-closed business day. ``today``
        itself is excluded (the day isn't closed yet — you can't audit a
        day before it ends).

        Example: ``trailing_days_ending_yesterday(2026-05-24, 7)`` →
        ``[2026-05-17, 2026-05-23]`` (7 days, today excluded).
        """
        if days < 1:
            raise ValueError(f"days must be >= 1, got {days}")
        return cls(
            start=today - timedelta(days=days),
            end=today - timedelta(days=1),
        )

    @classmethod
    def trailing_days_ending_today(cls, today: date, days: int) -> Self:
        """``[today - days + 1, today]`` — "last N days including today".

        For live-streaming dashboards where today's partial data counts.

        Example: ``trailing_days_ending_today(2026-05-24, 7)`` →
        ``[2026-05-18, 2026-05-24]`` (7 days, today included).
        """
        if days < 1:
            raise ValueError(f"days must be >= 1, got {days}")
        return cls(
            start=today - timedelta(days=days - 1),
            end=today,
        )

    # -- queries --

    def contains(self, d: date) -> bool:
        """True iff ``d`` is in ``[start, end]`` (both endpoints inclusive)."""
        return self.start <= d <= self.end

    @property
    def days(self) -> int:
        """Number of days in the interval, both endpoints counted.

        ``single_day(x).days == 1``.
        """
        return (self.end - self.start).days + 1

    def iter_days(self) -> Iterable[date]:
        """Yield every date from ``start`` to ``end`` inclusive."""
        cur = self.start
        while cur <= self.end:
            yield cur
            cur = cur + timedelta(days=1)

    # -- conversions --

    def as_half_open_datetimes(self) -> "DateTimeInterval":
        """Convert ``[start, end]`` (closed dates) to ``[start 00:00, end+1 00:00)``
        (half-open naive datetimes).

        Single-TZ invariant — returned datetimes are NAIVE. The +1-day flip
        widens "end-of-day on end" into "start-of-day on end+1," so callers
        don't write ``end + timedelta(days=1)`` by hand.
        """
        start_dt = datetime(self.start.year, self.start.month, self.start.day)
        end_dt_exclusive = (
            datetime(self.end.year, self.end.month, self.end.day)
            + timedelta(days=1)
        )
        return DateTimeInterval(start=start_dt, end_exclusive=end_dt_exclusive)


@dataclass(frozen=True, slots=True)
class DateTimeInterval:
    """Half-open ``[start, end_exclusive)`` interval over NAIVE timestamps.

    The math/SQL convention — ``col >= start AND col < end_exclusive``.
    Used by ``stuck_*`` matviews (``posted_at >= now - interval``), SQL
    ``BETWEEN`` callers that need "end of day" without hand-rolling +1
    day, live-streaming dashboards that slide past midnight.

    Closed-closed daily counterpart is ``DateInterval``; convert via
    ``DateInterval.as_half_open_datetimes()``.

    Single-TZ invariant: ``start`` and ``end_exclusive`` MUST be naive
    (``tzinfo is None``). Aware datetimes raise ValueError at
    construction. See module docstring.
    """

    start: datetime
    end_exclusive: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end_exclusive.tzinfo is not None:
            raise ValueError(
                "DateTimeInterval requires NAIVE datetimes (no tzinfo). "
                "The system assumes one consistent TZ end-to-end; see "
                "single-TZ invariant in common/intervals.py."
            )
        if self.end_exclusive <= self.start:
            raise ValueError(
                f"DateTimeInterval: end_exclusive ({self.end_exclusive}) "
                f"<= start ({self.start}). Half-open intervals must have "
                f"positive duration."
            )

    @classmethod
    def half_open(cls, start: datetime, end_exclusive: datetime) -> Self:
        """Explicit half-open construction."""
        return cls(start=start, end_exclusive=end_exclusive)

    @classmethod
    def trailing_duration_ending_now(
        cls, now: datetime, duration: timedelta,
    ) -> Self:
        """``[now - duration, now)`` — the stuck_* matview convention.

        Right edge is exclusive (the rendering of "in flight as of now"
        excludes the now-instant itself by convention).
        """
        return cls(start=now - duration, end_exclusive=now)

    def contains(self, dt: datetime) -> bool:
        """True iff ``dt`` is in ``[start, end_exclusive)``."""
        return self.start <= dt < self.end_exclusive

    @property
    def duration(self) -> timedelta:
        """``end_exclusive - start``."""
        return self.end_exclusive - self.start


# ---------------------------------------------------------------------------
# Plant schedule types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SingleDayPlant:
    """A plant that lands on exactly one calendar day.

    ``DriftGenerator``, ``OverdraftGenerator``, ``LimitBreachGenerator`` —
    the invariants that fire on one day at a time consume this. The day
    is a derived value, not a free field: the factory uses one of the
    named ``at_*`` constructors so the call site declares its policy.
    """

    day: date

    @classmethod
    def at_window_end(cls, window: DateInterval) -> Self:
        """Most-recently-closed day in the window. The default for
        single-day plants whose existence implies a finished day."""
        return cls(day=window.end)

    @classmethod
    def at_window_start(cls, window: DateInterval) -> Self:
        """Earliest day in the window."""
        return cls(day=window.start)

    @classmethod
    def at_offset_from_end(cls, window: DateInterval, days_back: int) -> Self:
        """``window.end - days_back``, validated against ``window``.

        For generators that need to plant N days before the window's tail
        (e.g. a drift that the audit catches late).
        """
        if days_back < 0:
            raise ValueError(f"days_back must be >= 0, got {days_back}")
        target = window.end - timedelta(days=days_back)
        if not window.contains(target):
            raise ValueError(
                f"SingleDayPlant.at_offset_from_end: target {target} "
                f"falls outside window [{window.start}, {window.end}]"
            )
        return cls(day=target)


@dataclass(frozen=True, slots=True)
class MultiDayPlant:
    """A plant whose effect spans every day of a window.

    ``StuckUnbundledGenerator`` (unbundled-bucket stuck for N days),
    ``RailFiringGenerator`` (a rail fires repeatedly across a span).
    Generators that consume ``MultiDayPlant`` walk ``iter_days()``; they
    DO NOT pick "the right day" internally.
    """

    window: DateInterval

    @classmethod
    def spans(cls, window: DateInterval) -> Self:
        """Plant covers every day in ``window``. Named to mirror its
        policy: the plant SPANS the window, it doesn't sample within it.
        """
        return cls(window=window)

    def iter_days(self) -> Iterable[date]:
        """Yield every date in the underlying window (inclusive both ends)."""
        return self.window.iter_days()


# Closed union of plant schedules — new schedule kinds are added as new
# types in this file, not as new "modes" of an existing type.
PlantSchedule = SingleDayPlant | MultiDayPlant
