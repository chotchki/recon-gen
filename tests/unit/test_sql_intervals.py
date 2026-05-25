"""BC.3 — Unit tests for dialect-aware SQL emission of intervals.

Covers:

- ``between_clause`` (closed-closed) for `DateInterval` over each
  dialect; literal shape matches `date_literal` (PG/Oracle: ``DATE
  'YYYY-MM-DD'``; SQLite: bare quoted text).
- ``range_clause`` for both ``DateInterval`` (with the +1-day flip
  for closed-end → exclusive-end) and ``DateTimeInterval`` (straight
  pass-through).
- Boundary cases: single-day interval, multi-day interval, instant
  ``DateTimeInterval``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from recon_gen.common.intervals import DateInterval, DateTimeInterval
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.sql.intervals import between_clause, range_clause


_DATE_INTERVAL = DateInterval.closed(date(2026, 5, 17), date(2026, 5, 23))


# ---------------------------------------------------------------------------
# between_clause (closed-closed)
# ---------------------------------------------------------------------------


class TestBetweenClause:
    @pytest.mark.parametrize(
        "dialect, expected",
        [
            (
                Dialect.POSTGRES,
                "business_day_start BETWEEN DATE '2026-05-17' AND DATE '2026-05-23'",
            ),
            (
                Dialect.ORACLE,
                "business_day_start BETWEEN DATE '2026-05-17' AND DATE '2026-05-23'",
            ),
            (
                Dialect.SQLITE,
                "business_day_start BETWEEN '2026-05-17' AND '2026-05-23'",
            ),
        ],
    )
    def test_dialect_literal_shape(
        self, dialect: Dialect, expected: str,
    ) -> None:
        out = between_clause(
            _DATE_INTERVAL, dialect=dialect, column="business_day_start",
        )
        assert out == expected

    def test_single_day_interval(self) -> None:
        iv = DateInterval.single_day(date(2026, 5, 24))
        out = between_clause(
            iv, dialect=Dialect.POSTGRES, column="day",
        )
        assert out == "day BETWEEN DATE '2026-05-24' AND DATE '2026-05-24'"

    def test_column_arg_passes_through(self) -> None:
        out = between_clause(
            _DATE_INTERVAL, dialect=Dialect.POSTGRES, column="t.posted_at",
        )
        assert out.startswith("t.posted_at BETWEEN ")


# ---------------------------------------------------------------------------
# range_clause(DateInterval) — closed-input, half-open-output (+1 day flip)
# ---------------------------------------------------------------------------


class TestRangeClauseDateInterval:
    @pytest.mark.parametrize(
        "dialect, expected",
        [
            (
                Dialect.POSTGRES,
                "business_day_start >= DATE '2026-05-17' AND "
                "business_day_start < DATE '2026-05-24'",
            ),
            (
                Dialect.ORACLE,
                "business_day_start >= DATE '2026-05-17' AND "
                "business_day_start < DATE '2026-05-24'",
            ),
            (
                Dialect.SQLITE,
                "business_day_start >= '2026-05-17' AND "
                "business_day_start < '2026-05-24'",
            ),
        ],
    )
    def test_widens_end_by_one_day(
        self, dialect: Dialect, expected: str,
    ) -> None:
        # `_DATE_INTERVAL.end` is 2026-05-23; the half-open right edge
        # should be 2026-05-24 (the +1-day flip lives inside the helper,
        # not at the callsite).
        out = range_clause(
            _DATE_INTERVAL, dialect=dialect, column="business_day_start",
        )
        assert out == expected

    def test_single_day_interval_covers_one_day(self) -> None:
        iv = DateInterval.single_day(date(2026, 5, 24))
        out = range_clause(iv, dialect=Dialect.POSTGRES, column="day")
        # Half-open over single-day = `>= today AND < tomorrow`.
        assert out == (
            "day >= DATE '2026-05-24' AND day < DATE '2026-05-25'"
        )


# ---------------------------------------------------------------------------
# range_clause(DateTimeInterval) — half-open pass-through
# ---------------------------------------------------------------------------


class TestRangeClauseDateTimeInterval:
    @pytest.mark.parametrize(
        "dialect, expected_prefix",
        [
            (Dialect.POSTGRES, "posted_at >= TIMESTAMP '2026-05-24 09:00:00' AND posted_at < TIMESTAMP '2026-05-24 17:00:00'"),
            (Dialect.ORACLE, "posted_at >= TIMESTAMP '2026-05-24 09:00:00' AND posted_at < TIMESTAMP '2026-05-24 17:00:00'"),
            (Dialect.SQLITE, "posted_at >= '2026-05-24 09:00:00' AND posted_at < '2026-05-24 17:00:00'"),
        ],
    )
    def test_dialect_timestamp_literal_shape(
        self, dialect: Dialect, expected_prefix: str,
    ) -> None:
        dti = DateTimeInterval.half_open(
            datetime(2026, 5, 24, 9, 0),
            datetime(2026, 5, 24, 17, 0),
        )
        out = range_clause(dti, dialect=dialect, column="posted_at")
        assert out == expected_prefix

    def test_short_duration_interval(self) -> None:
        # An hour-long interval — verify the literals don't blow up on
        # sub-day timestamps.
        dti = DateTimeInterval.trailing_duration_ending_now(
            datetime(2026, 5, 24, 12, 30), timedelta(hours=1),
        )
        out = range_clause(
            dti, dialect=Dialect.POSTGRES, column="posted_at",
        )
        assert "11:30:00" in out
        assert "12:30:00" in out


# ---------------------------------------------------------------------------
# Cross-shape consistency: range_clause(DateInterval) ≡ range_clause(DateInterval.as_half_open_datetimes())
# on date columns, the two paths should produce equivalent semantics
# (the closed→half-open flip lives in two places, both consistent).
# ---------------------------------------------------------------------------


def test_date_interval_range_clause_consistent_with_as_half_open() -> None:
    """The +1-day flip in `range_clause(DateInterval)` must match the
    semantic of converting the interval to a `DateTimeInterval` via
    `as_half_open_datetimes()` first. Both paths represent the same
    half-open window; both must select the same rows (modulo the
    literal-type difference, DATE vs TIMESTAMP)."""
    direct = range_clause(
        _DATE_INTERVAL, dialect=Dialect.POSTGRES, column="day",
    )
    converted = range_clause(
        _DATE_INTERVAL.as_half_open_datetimes(),
        dialect=Dialect.POSTGRES, column="day",
    )
    # Both express "day in [05-17 00:00, 05-24 00:00)". The literal
    # shape differs (DATE 'X' vs TIMESTAMP 'X HH:MM:SS') but the
    # bound dates match.
    assert "2026-05-17" in direct and "2026-05-17" in converted
    assert "2026-05-24" in direct and "2026-05-24" in converted
