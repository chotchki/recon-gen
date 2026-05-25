# typing-smell: ignore-file[no-naked-interval-ctor]: this test module IS the test for the bare-constructor primitives — every `__post_init__` rejection test below has to call the bare constructor directly to verify the failure path.
"""BC.1 — Unit tests for the typed interval / plant-schedule value types.

Cover:

- Every named-convention constructor + boundary cases (today, today-1,
  today+1, single-day, multi-day).
- ``contains`` boundary behavior — closed-closed for ``DateInterval``,
  half-open for ``DateTimeInterval``.
- ``.days`` count round-trips through ``iter_days()``.
- ``as_half_open_datetimes()`` round-trip (closed-date end ↔ exclusive-
  datetime end+1).
- Constructor errors: end-before-start, days < 1, days_back negative or
  past the window's start, aware datetimes rejected by the single-TZ
  invariant.
- Plant-schedule constructors: derived day matches the window-relative
  policy named by the constructor; ``MultiDayPlant.iter_days()`` mirrors
  the underlying ``DateInterval``.
- Hashability (``frozen=True``) — dict-keyed lookups.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import pytest

from recon_gen.common.intervals import (
    DateInterval,
    DateTimeInterval,
    MultiDayPlant,
    PlantSchedule,
    SingleDayPlant,
)


# ---------------------------------------------------------------------------
# DateInterval — closed-closed dates
# ---------------------------------------------------------------------------


class TestDateInterval:
    def test_closed_constructor_round_trip(self) -> None:
        iv = DateInterval.closed(date(2026, 1, 1), date(2026, 1, 7))
        assert iv.start == date(2026, 1, 1)
        assert iv.end == date(2026, 1, 7)

    def test_single_day_is_one_day(self) -> None:
        iv = DateInterval.single_day(date(2026, 5, 24))
        assert iv.start == iv.end == date(2026, 5, 24)
        assert iv.days == 1
        assert list(iv.iter_days()) == [date(2026, 5, 24)]

    def test_trailing_days_ending_yesterday_excludes_today(self) -> None:
        # The audit-window convention. The chronic v11.10.0+ bug existed
        # because plants landed at `today` but `_PERIOD` excluded today.
        # The constructor name + the test naming the property together
        # make that mismatch unrepresentable.
        today = date(2026, 5, 24)
        iv = DateInterval.trailing_days_ending_yesterday(today, 7)
        assert iv.start == date(2026, 5, 17)
        assert iv.end == date(2026, 5, 23)
        assert iv.days == 7
        assert not iv.contains(today)  # today excluded
        assert iv.contains(today - timedelta(days=1))  # yesterday included

    def test_trailing_days_ending_today_includes_today(self) -> None:
        today = date(2026, 5, 24)
        iv = DateInterval.trailing_days_ending_today(today, 7)
        assert iv.start == date(2026, 5, 18)
        assert iv.end == today
        assert iv.days == 7
        assert iv.contains(today)
        assert iv.contains(date(2026, 5, 18))
        assert not iv.contains(date(2026, 5, 17))  # one day before window

    @pytest.mark.parametrize(
        "iv_factory",
        [
            lambda: DateInterval.closed(date(2026, 1, 1), date(2026, 1, 7)),
            lambda: DateInterval.single_day(date(2026, 5, 24)),
            lambda: DateInterval.trailing_days_ending_yesterday(
                date(2026, 5, 24), 7,
            ),
        ],
    )
    def test_contains_endpoint_inclusivity(
        self, iv_factory: Callable[[], DateInterval],
    ) -> None:
        iv = iv_factory()
        # Both endpoints inclusive — the closed-closed convention this
        # type carries.
        assert iv.contains(iv.start)
        assert iv.contains(iv.end)
        assert not iv.contains(iv.start - timedelta(days=1))
        assert not iv.contains(iv.end + timedelta(days=1))

    def test_days_equals_iter_count(self) -> None:
        iv = DateInterval.closed(date(2026, 1, 1), date(2026, 1, 7))
        assert iv.days == len(list(iv.iter_days())) == 7

    def test_iter_days_is_chronological(self) -> None:
        iv = DateInterval.closed(date(2026, 1, 1), date(2026, 1, 3))
        assert list(iv.iter_days()) == [
            date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3),
        ]

    def test_as_half_open_datetimes_widens_end(self) -> None:
        iv = DateInterval.closed(date(2026, 5, 17), date(2026, 5, 23))
        dti = iv.as_half_open_datetimes()
        assert dti.start == datetime(2026, 5, 17, 0, 0)
        # End widens to midnight of the day AFTER `iv.end` — so the half-
        # open shape covers all of `iv.end` itself.
        assert dti.end_exclusive == datetime(2026, 5, 24, 0, 0)
        assert dti.duration == timedelta(days=7)
        # Naive (single-TZ invariant).
        assert dti.start.tzinfo is None
        assert dti.end_exclusive.tzinfo is None

    # -- constructor errors --

    def test_rejects_end_before_start(self) -> None:
        with pytest.raises(ValueError, match="precedes start"):
            DateInterval(start=date(2026, 5, 24), end=date(2026, 5, 17))

    def test_trailing_yesterday_rejects_zero_days(self) -> None:
        with pytest.raises(ValueError, match="days must be >= 1"):
            DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 0)

    def test_trailing_today_rejects_zero_days(self) -> None:
        with pytest.raises(ValueError, match="days must be >= 1"):
            DateInterval.trailing_days_ending_today(date(2026, 5, 24), 0)

    def test_trailing_yesterday_rejects_negative_days(self) -> None:
        with pytest.raises(ValueError, match="days must be >= 1"):
            DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), -1)

    # -- hashability + immutability --

    def test_frozen_is_hashable_dict_key(self) -> None:
        iv1 = DateInterval.single_day(date(2026, 5, 24))
        iv2 = DateInterval.single_day(date(2026, 5, 24))
        iv3 = DateInterval.single_day(date(2026, 5, 25))
        m: dict[DateInterval, str] = {iv1: "today"}
        assert m[iv2] == "today"  # value equality round-trips
        assert iv3 not in m

    def test_frozen_blocks_mutation(self) -> None:
        iv = DateInterval.closed(date(2026, 1, 1), date(2026, 1, 7))
        with pytest.raises((AttributeError, TypeError)):
            iv.start = date(2026, 1, 2)  # type: ignore[misc]: frozen dataclass — assignment is supposed to fail; that's the test


# ---------------------------------------------------------------------------
# DateTimeInterval — half-open naive timestamps
# ---------------------------------------------------------------------------


class TestDateTimeInterval:
    def test_half_open_constructor_round_trip(self) -> None:
        dti = DateTimeInterval.half_open(
            datetime(2026, 5, 24, 9, 0),
            datetime(2026, 5, 24, 17, 0),
        )
        assert dti.duration == timedelta(hours=8)

    def test_trailing_duration_ending_now(self) -> None:
        now = datetime(2026, 5, 24, 12, 0)
        dti = DateTimeInterval.trailing_duration_ending_now(
            now, timedelta(hours=2),
        )
        assert dti.start == datetime(2026, 5, 24, 10, 0)
        assert dti.end_exclusive == now
        assert dti.duration == timedelta(hours=2)

    def test_contains_is_half_open(self) -> None:
        dti = DateTimeInterval.half_open(
            datetime(2026, 5, 24, 9, 0),
            datetime(2026, 5, 24, 17, 0),
        )
        # Start inclusive, end exclusive — the half-open convention.
        assert dti.contains(datetime(2026, 5, 24, 9, 0))  # start
        assert dti.contains(datetime(2026, 5, 24, 12, 0))  # middle
        assert not dti.contains(datetime(2026, 5, 24, 17, 0))  # end excluded
        assert not dti.contains(datetime(2026, 5, 24, 8, 59))  # before start

    def test_duration_is_end_minus_start(self) -> None:
        dti = DateTimeInterval.half_open(
            datetime(2026, 5, 24, 0, 0),
            datetime(2026, 5, 31, 0, 0),
        )
        assert dti.duration == timedelta(days=7)

    # -- single-TZ invariant: aware datetimes rejected at construction --

    def test_rejects_aware_start(self) -> None:
        with pytest.raises(ValueError, match="NAIVE datetimes"):
            DateTimeInterval(
                start=datetime(2026, 5, 24, 9, 0, tzinfo=timezone.utc),
                end_exclusive=datetime(2026, 5, 24, 17, 0),
            )

    def test_rejects_aware_end(self) -> None:
        with pytest.raises(ValueError, match="NAIVE datetimes"):
            DateTimeInterval(
                start=datetime(2026, 5, 24, 9, 0),
                end_exclusive=datetime(2026, 5, 24, 17, 0, tzinfo=timezone.utc),
            )

    def test_rejects_both_aware(self) -> None:
        with pytest.raises(ValueError, match="NAIVE datetimes"):
            DateTimeInterval(
                start=datetime(2026, 5, 24, 9, 0, tzinfo=timezone.utc),
                end_exclusive=datetime(2026, 5, 24, 17, 0, tzinfo=timezone.utc),
            )

    # -- non-empty invariant --

    def test_rejects_equal_endpoints(self) -> None:
        ts = datetime(2026, 5, 24, 12, 0)
        with pytest.raises(ValueError, match="positive duration"):
            DateTimeInterval(start=ts, end_exclusive=ts)

    def test_rejects_end_before_start(self) -> None:
        with pytest.raises(ValueError, match="positive duration"):
            DateTimeInterval(
                start=datetime(2026, 5, 24, 17, 0),
                end_exclusive=datetime(2026, 5, 24, 9, 0),
            )


# ---------------------------------------------------------------------------
# SingleDayPlant — derived from a window
# ---------------------------------------------------------------------------


class TestSingleDayPlant:
    def test_at_window_end_picks_window_end(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        p = SingleDayPlant.at_window_end(w)
        assert p.day == w.end == date(2026, 5, 23)

    def test_at_window_start_picks_window_start(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        p = SingleDayPlant.at_window_start(w)
        assert p.day == w.start == date(2026, 5, 17)

    def test_at_offset_from_end_walks_back_inside_window(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        # window = [2026-05-17, 2026-05-23]
        for days_back in range(w.days):
            p = SingleDayPlant.at_offset_from_end(w, days_back)
            assert p.day == w.end - timedelta(days=days_back)
            assert w.contains(p.day)

    def test_at_offset_from_end_zero_is_window_end(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        assert SingleDayPlant.at_offset_from_end(w, 0).day == w.end

    def test_at_offset_from_end_rejects_past_window_start(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        # window has 7 days — offset 7 would land at window.start - 1.
        with pytest.raises(ValueError, match="outside window"):
            SingleDayPlant.at_offset_from_end(w, w.days)

    def test_at_offset_from_end_rejects_negative(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        with pytest.raises(ValueError, match="days_back must be >= 0"):
            SingleDayPlant.at_offset_from_end(w, -1)

    def test_frozen_hashable(self) -> None:
        p1 = SingleDayPlant(day=date(2026, 5, 24))
        p2 = SingleDayPlant(day=date(2026, 5, 24))
        assert hash(p1) == hash(p2)
        assert {p1, p2} == {SingleDayPlant(day=date(2026, 5, 24))}


# ---------------------------------------------------------------------------
# MultiDayPlant — spans the whole window
# ---------------------------------------------------------------------------


class TestMultiDayPlant:
    def test_spans_carries_window(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        p = MultiDayPlant.spans(w)
        assert p.window == w

    def test_iter_days_mirrors_window(self) -> None:
        w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
        p = MultiDayPlant.spans(w)
        assert list(p.iter_days()) == list(w.iter_days())

    def test_single_day_window_is_one_day_span(self) -> None:
        w = DateInterval.single_day(date(2026, 5, 24))
        p = MultiDayPlant.spans(w)
        assert list(p.iter_days()) == [date(2026, 5, 24)]

    def test_frozen_hashable(self) -> None:
        w = DateInterval.single_day(date(2026, 5, 24))
        p1 = MultiDayPlant.spans(w)
        p2 = MultiDayPlant.spans(w)
        assert hash(p1) == hash(p2)


# ---------------------------------------------------------------------------
# PlantSchedule union — both arms type-check
# ---------------------------------------------------------------------------


def test_plant_schedule_union_accepts_both_arms() -> None:
    w = DateInterval.trailing_days_ending_yesterday(date(2026, 5, 24), 7)
    schedules: list[PlantSchedule] = [
        SingleDayPlant.at_window_end(w),
        MultiDayPlant.spans(w),
    ]
    assert len(schedules) == 2
    # Pattern match — each arm has a distinct shape.
    for s in schedules:
        match s:
            case SingleDayPlant(day=d):
                assert isinstance(d, date)
            case MultiDayPlant(window=win):
                assert isinstance(win, DateInterval)


# ---------------------------------------------------------------------------
# The chronic v11.10.0+ bug, retold as a property
# ---------------------------------------------------------------------------


def test_audit_window_contains_at_window_end_plant() -> None:
    """The chronic e2e gate failure: plant landed at ``today``, but the
    audit period excluded today. With the typed `DateInterval` +
    `SingleDayPlant.at_window_end`, the plant lands at the LAST day of
    the audit window — guaranteed to be inside the window by
    construction.

    This is the property test that the v11.10.0+ off-by-one cannot
    re-introduce while these types are in use.
    """
    today = date(2026, 5, 24)
    audit_window = DateInterval.trailing_days_ending_yesterday(today, 7)
    plant = SingleDayPlant.at_window_end(audit_window)

    assert not audit_window.contains(today)  # today excluded
    assert audit_window.contains(plant.day)  # plant lands inside
    assert plant.day == audit_window.end  # explicit policy honored
