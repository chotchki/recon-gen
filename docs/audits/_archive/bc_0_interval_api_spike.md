# BC.0 — Typed time-range interval API spike

**Status:** spike for sign-off — implementation gated on operator approval.
**Date:** 2026-05-24.
**Prompted by:** `e2e-against-testpypi` chronic red since v11.10.0 (PyPI publish blocked 8 releases). Root cause: plant `anchor_day = date.today()` lands on today; audit window is `[today-7, today-1]` (closed, today-excluded). The off-by-one is unrepresentable to detect at the wiring site — every `tuple[date, date]` callsite re-litigates inclusive-vs-exclusive in docstring comments.

Related: `date_range_model_audit.md` (AO.11) introduced the `(as_of, window, seed)` frame as the conceptual fix. AO.11 closed as a doc; the rollout was never scheduled. BC ships the value-type layer (intervals + plant schedules). Phase BD (queued in PLAN.md immediately after BC) consumes them in the `AsOfFrame` rollout — threading the typed frame through audit CLI → plant emit → dashboard defaults → QS `RollingDate` derivation. Splitting the work that way keeps BC small/reviewable/fast (unblocks PyPI publish via BC.6) and gives BD a real scheduled home rather than burying it in "later."

---

## Single-timezone invariant

**This system assumes one consistent timezone end-to-end. No conversion is offered.** All `datetime` values flowing through the codebase are naive (no `tzinfo`); their LOCAL meaning is the operator's machine TZ by convention ([[project_local_tz_convention]]). Spine generators use `datetime.now()` (naive, LOCAL); Oracle's WITH-TIME-ZONE limitation forced this; SQLite + Postgres mirror it for parity.

Consequence for the interval API: `DateInterval.as_half_open_datetimes()` takes NO `tzinfo` argument and returns naive datetimes. If callers want timezone-aware behavior, they're crossing a system boundary and need to build their own conversion at that boundary — the intervals layer refuses to be the place where TZ policy lives.

## Goals

- One named type per endpoint convention. Closed-closed `[start, end]` (the business convention — "week of May 17 - May 23" is both inclusive) is `DateInterval`. Half-open `[start, end)` (the math + SQL convention — `col >= start AND col < end`) is `DateTimeInterval`.
- Constructors NAME the convention; no `__init__(start, end)` lets a caller mint an interval without saying which convention they meant. Enforced by an AST lint in `tests/unit/test_typing_smells.py::no-naked-interval-ctor` — same shape as `no-playwright-leak`: any `DateInterval(...)` / `DateTimeInterval(...)` call outside `common/intervals.py` must be a `.classmethod_name(...)` form. Wiring sites that try the bare constructor fail the unit layer.
- Cross-type conversion (`DateInterval.as_half_open_datetimes()`) puts the `+1 day` flip closed→half-open inside the type, not at every callsite. Returns naive datetimes per the single-TZ invariant.
- Plant generators consume a **typed plant schedule** (`SingleDayPlant` or `MultiDayPlant`), not a raw window or anchor. Each generator's signature declares which shape it wants; the factory derives the right shape from the test's window per generator. No "where in the window do I plant?" convention lives in a generator's body — it lives in the type.

## Non-goals

- Not replacing `datetime.date` / `datetime.datetime` for point semantics. Plant `SingleDayPlant.day` is still a `date` — a single calendar day is a point, not an interval.
- Not the AO.11 frame rollout itself. BC ships value-types; **Phase BD** (PLAN.md, immediately after BC) threads them through the `(as_of, window, seed)` frame across audit CLI / plant emit / dashboard defaults / QS `RollingDate` derivation. BC keeps a tight scope so PyPI publish unblocks fast; BD does the broader sweep.
- Not yet replacing `RollingDate` parameter defaults (a QS analysis-layer construct). That's BD work — once BC's types exist, BD derives `RollingDate` expressions FROM `DateInterval`-typed defaults rather than from hand-written string expressions in `apps/*/app.py`.

## Proposed API

```python
# src/recon_gen/common/intervals.py

from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Self


@dataclass(frozen=True, slots=True)
class DateInterval:
    """Closed-closed [start, end] interval over calendar dates.

    The business-facing default. "Week of May 17 - May 23" means BOTH
    endpoints included (May 17 AND May 23 are in scope). Audit reports,
    matview BETWEEN clauses, and dashboard date-range filters all use
    this convention.

    For SQL/math half-open shape, convert via `.as_half_open_datetimes()`
    — the +1 day to flip closed-closed→half-open lives inside this method,
    not at each callsite.

    Invariant: `start <= end` (a one-day interval is `start == end`).

    Single-TZ invariant: returned naive datetimes (no tzinfo). The system
    assumes one consistent TZ end-to-end; conversion isn't offered.
    """
    start: date
    end: date  # inclusive

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"DateInterval: end ({self.end}) precedes start "
                f"({self.start}). For an empty interval use a sentinel "
                f"type — DateInterval is non-empty by construction."
            )

    # -- named-convention constructors (the only way wiring sites should
    # mint a DateInterval; bare DateInterval(s, e) is enforced off-limits
    # by the no-naked-interval-ctor AST lint) --
    @classmethod
    def closed(cls, start: date, end: date) -> Self:
        """Explicit closed-closed construction. The call site names the
        convention even when the two endpoints are already in hand."""
        return cls(start=start, end=end)

    @classmethod
    def single_day(cls, d: date) -> Self:
        """A one-day interval [d, d]."""
        return cls(start=d, end=d)

    @classmethod
    def trailing_days_ending_yesterday(cls, today: date, days: int) -> Self:
        """`[today - days, today - 1]` — the audit window convention.
        N days, ending on the most-recently-closed business day. `today`
        itself is excluded (the day isn't closed yet).
        Example: `trailing_days_ending_yesterday(2026-05-24, 7)` →
        `[2026-05-17, 2026-05-23]` (7 days, today excluded)."""
        if days < 1:
            raise ValueError(f"days must be >= 1, got {days}")
        return cls(start=today - timedelta(days=days), end=today - timedelta(days=1))

    @classmethod
    def trailing_days_ending_today(cls, today: date, days: int) -> Self:
        """`[today - days + 1, today]` — "last N days including today".
        For live-streaming dashboards where today's partial data counts.
        Example: `trailing_days_ending_today(2026-05-24, 7)` →
        `[2026-05-18, 2026-05-24]` (7 days, today included)."""
        if days < 1:
            raise ValueError(f"days must be >= 1, got {days}")
        return cls(start=today - timedelta(days=days - 1), end=today)

    # -- queries --
    def contains(self, d: date) -> bool:
        """True iff `d` is in [start, end] (both endpoints inclusive)."""
        return self.start <= d <= self.end

    @property
    def days(self) -> int:
        """Number of days in the interval, both endpoints counted.
        `single_day(x).days == 1`."""
        return (self.end - self.start).days + 1

    def iter_days(self) -> Iterable[date]:
        """Yield every date from start to end inclusive."""
        cur = self.start
        while cur <= self.end:
            yield cur
            cur = cur + timedelta(days=1)

    # -- conversions --
    def as_half_open_datetimes(self) -> "DateTimeInterval":
        """Convert `[start, end]` (closed dates) to `[start 00:00, end+1 00:00)`
        (half-open NAIVE datetimes). Single-TZ invariant — no `tzinfo`.
        The +1 day to widen end-of-day-on-end into start-of-day-on-end+1
        lives here, so callers don't write `end + timedelta(days=1)` by
        hand."""
        start_dt = datetime(self.start.year, self.start.month, self.start.day)
        end_dt_exclusive = datetime(self.end.year, self.end.month, self.end.day) + timedelta(days=1)
        return DateTimeInterval(start=start_dt, end_exclusive=end_dt_exclusive)


@dataclass(frozen=True, slots=True)
class DateTimeInterval:
    """Half-open [start, end_exclusive) interval over timestamps.

    The math/SQL convention — `col >= start AND col < end_exclusive`.
    Used by:
    - stuck_* matviews that filter `posted_at >= now - interval`
    - SQL `BETWEEN` callers that need to express "end of day" without
      hand-rolling `+1 day`
    - Live-streaming dashboards where the window slides past midnight

    The closed-closed daily counterpart is `DateInterval`; convert via
    `DateInterval.as_half_open_datetimes()`.

    Invariant: `start < end_exclusive`. A single instant is NOT
    representable (this is a half-open interval; the empty case
    `start == end_exclusive` is excluded by convention for clarity).

    Single-TZ invariant: `start` + `end_exclusive` are NAIVE datetimes
    (no tzinfo). The system assumes one consistent TZ end-to-end.
    """
    start: datetime
    end_exclusive: datetime

    def __post_init__(self) -> None:
        if self.end_exclusive <= self.start:
            raise ValueError(
                f"DateTimeInterval: end_exclusive ({self.end_exclusive}) "
                f"<= start ({self.start}). Half-open intervals must have "
                f"positive duration."
            )
        if self.start.tzinfo is not None or self.end_exclusive.tzinfo is not None:
            raise ValueError(
                "DateTimeInterval requires naive datetimes (no tzinfo). "
                "The system assumes one consistent TZ end-to-end; see "
                "single-TZ invariant in common/intervals.py."
            )

    @classmethod
    def half_open(cls, start: datetime, end_exclusive: datetime) -> Self:
        """Explicit half-open construction. The call site names the
        convention even when the two endpoints are already in hand."""
        return cls(start=start, end_exclusive=end_exclusive)

    @classmethod
    def trailing_duration_ending_now(cls, now: datetime, duration: timedelta) -> Self:
        """`[now - duration, now)` — the stuck_* matview convention.
        Right edge is exclusive (the rendering of "in flight as of now"
        excludes the now-instant itself by convention)."""
        return cls(start=now - duration, end_exclusive=now)

    def contains(self, dt: datetime) -> bool:
        """True iff `dt` is in [start, end_exclusive)."""
        return self.start <= dt < self.end_exclusive

    @property
    def duration(self) -> timedelta:
        return self.end_exclusive - self.start


# -- Plant schedule types (BC + extension into BD) --
# Each plant generator declares which schedule it consumes; the factory
# in plant_adapter.py constructs the right shape from the test's window
# per generator. No "where in the window do I plant?" convention lives in
# generator bodies — it lives in the type.

@dataclass(frozen=True, slots=True)
class SingleDayPlant:
    """A plant that lands on exactly one calendar day.

    `DriftGenerator`, `OverdraftGenerator`, `LimitBreachGenerator` — the
    invariants that fire on one day at a time consume this. The day is a
    derived value, not a free field: the factory uses one of the named
    `at_*` constructors so the call site declares its policy.
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
        """`window.end - days_back`, validated against `window`. For
        generators that need to plant N days before the window's tail
        (e.g. a drift that the audit catches late)."""
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

    `StuckUnbundledGenerator` (the unbundled-bucket has been stuck for N
    days), `RailFiringGenerator` (a rail fires repeatedly across a
    span). Generators that consume `MultiDayPlant` walk `iter_days()`;
    they DO NOT pick "the right day" internally.
    """
    window: DateInterval

    @classmethod
    def spans(cls, window: DateInterval) -> Self:
        """Plant covers every day in `window`. Constructor named to mirror
        the docstring policy: the plant SPANS the window, it doesn't
        sample within it."""
        return cls(window=window)

    def iter_days(self) -> Iterable[date]:
        return self.window.iter_days()


# Plant schedule is the closed union; new schedule kinds are added as
# new types in this file, not as new "modes" of an existing type.
PlantSchedule = SingleDayPlant | MultiDayPlant
```

## Dialect-aware SQL emission

Deliberately omitted from the type bodies above. The `to_sql_between` / `to_sql_range` methods from the v1 spike depend on `Dialect`, but `common/intervals.py` shouldn't import `common/sql/dialect.py` — interval value-types have no business knowing what database is downstream. Instead, BC.3 adds a thin module `common/sql/intervals.py` that takes `(interval, dialect, column) → SQLFragment` and does the dialect-specific literal formatting (`DATE 'YYYY-MM-DD'` for PG/Oracle, `'YYYY-MM-DD'` for SQLite). The interval type stays a pure value; the SQL formatting is a function over `(value, dialect, column)` that lives at the SQL seam.

## How the chronic v11.10.0+ bug clears

Today's flow:

```python
# test_audit_dashboard_agreement.py
_TODAY = date.today()
_PERIOD: tuple[date, date] = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))

# seeded_audit fixture
scenario = apply_db_seed(conn, instance, prefix=..., today=_TODAY, ...)

# apply_db_seed
today_ref = today or DEFAULT_SEED_TODAY  # date(2030, 1, 1)
generators = scenario_to_generators(scenario, instance, anchor=today_ref, ...)

# DriftGenerator.emit_balances
business_day_start = anchor_day  # = today
```

The mismatch: `_PERIOD` excludes today; plant lands AT today. The comment on `_PERIOD` ("the audit period contains the plant effective dates by construction") was the author's intent but never enforced.

Post-BC:

```python
# test_audit_dashboard_agreement.py
_TODAY = date.today()
_PERIOD: DateInterval = DateInterval.trailing_days_ending_yesterday(_TODAY, 7)

# seeded_audit fixture
scenario = apply_db_seed(conn, instance, prefix=..., plant_window=_PERIOD, ...)

# apply_db_seed
plant_window_ref = plant_window or DEFAULT_PLANT_WINDOW  # DateInterval.single_day(date(2030, 1, 1))
generators = scenario_to_generators(scenario, instance, plant_window=plant_window_ref, ...)

# plant_adapter.py — per-plant-kind schedule construction.
#   Drift / Overdraft / LimitBreach: SingleDayPlant.at_window_end(plant_window)
#   StuckUnbundled / RailFiring:    MultiDayPlant.spans(plant_window)
# Each generator's signature declares which it takes; the factory picks
# the right constructor per kind.

# DriftGenerator.emit_balances — sig: schedule: SingleDayPlant
business_day_start = schedule.day  # window.end = today - 1 (within [today-7, today-1])

# StuckUnbundledGenerator.emit — sig: schedule: MultiDayPlant
for day in schedule.iter_days():
    ...  # plant covers every day in [today-7, today-1]
```

Three layers of unrepresentability stacked:
1. `_PERIOD` and the seed window are LITERALLY the same `DateInterval` value — no two-fingered consistency check needed.
2. Each generator's signature declares its schedule type; you can't accidentally hand a `MultiDayPlant` to a `DriftGenerator` (and vice versa).
3. The schedule's "where in the window does the plant land?" is in the constructor name (`at_window_end`, `at_window_start`, `spans`), not in a generator body docstring.

## Open decisions for operator review

- **D1 — Two interval types, one per convention.** `DateInterval` (closed-closed dates) + `DateTimeInterval` (half-open datetimes). Not a parametric `Interval[T]` with an endpoint-policy enum — the enum drops at callsites the same way the docstring policy drops today, types bring meaning with them. **Recommendation: two types.**
- **D2 — Non-empty by construction.** `DateInterval` rejects `end < start`; `DateTimeInterval` rejects `end_exclusive <= start`. No empty-interval factory. Consumers that need "no data" use `Optional[DateInterval]`. **Recommendation: non-empty.**
- **D3 — Single-TZ invariant, no conversion.** All datetimes naive; system assumes one consistent TZ end-to-end. `DateInterval.as_half_open_datetimes()` takes no `tzinfo` and returns naive datetimes. `DateTimeInterval.__post_init__` REJECTS aware datetimes — wiring sites that try to pass `tzinfo` fail at construction. **Recommendation: single-TZ invariant, enforced at construction.**
- **D4 — SQL emit lives at the SQL seam.** Interval types are pure values; `common/sql/intervals.py` provides `between_clause(interval, dialect, column)` + `range_clause(interval, dialect, column)`. Reason: `common/intervals.py` shouldn't import `common/sql/dialect.py` (the interval is upstream of every storage layer). **Recommendation: separate module at the seam.**
- **D5 — Plant schedules are typed, not convention.** `SingleDayPlant` (one day, derived via `at_window_end` / `at_window_start` / `at_offset_from_end`) + `MultiDayPlant` (spans every day, via `spans`). Each generator's signature declares which it consumes; the factory picks per generator kind. No "where in the window?" logic in a generator body. **Recommendation: ship two plant schedule types in BC.1.**
- **D6 — `iter_days()` chronological only.** Reverse helper added if a consumer needs it. **Recommendation: chronological.**
- **D7 — AST lint on naked constructors.** `tests/unit/test_typing_smells.py::no-naked-interval-ctor`: every `DateInterval(...)` / `DateTimeInterval(...)` / `SingleDayPlant(...)` / `MultiDayPlant(...)` outside `common/intervals.py` must be a `.classmethod_name(...)` form. Same shape as the existing `no-playwright-leak` lint. The convention-naming constructors are only enforced as the wiring-site contract if the lint is in place — without it, callers will mint bare `DateInterval(s, e)` and the type's meaning leaks. **Recommendation: ship the lint in BC.1 alongside the types.**
- **D8 — AST lint on raw `date` / `datetime` in function parameters.** `tests/unit/test_typing_smells.py::no-raw-temporal-args`: any function/method parameter annotated `date` or `datetime` (or their `| None` variants) in `src/recon_gen/**` is a smell — wrap in `DateInterval` / `DateTimeInterval` / `SingleDayPlant` / `MultiDayPlant` / `RunContext` (BD) instead. Whitelist:
  - The typed wrappers' own classmethods + `__init__` (`common/intervals.py` — boundary where raw → wrapped happens).
  - Dataclass field annotations are NOT function parameters — `Transaction.posted_at: datetime` is unaffected (it's the actual single-point value of a real event; no policy to encode).
  - `# typing-smell: ignore[raw-date-arg]: WHY` escape hatch with a required justification comment.

  Lint staging — disabled in BC.1 (otherwise reds the whole tree before BC has anywhere to migrate to), enabled at the end of BC.5 once the migration surface is wrapped. **Recommendation: ship disabled in BC.1, enable end of BC.5, document the migration shape.**

  Forced design changes from D8 that are worth surfacing:
  - **Click decorators.** Today `audit apply` takes `--from X --to Y` with the callback signed `def apply(period_from: datetime, period_to: datetime, ...)`. Lint fires. Two ways out: (a) custom Click type that produces `DateInterval` and a single `--period` arg (`--period trailing:7` or `--period 2026-05-17..2026-05-23`), (b) wrap inside the callback with `# typing-smell: ignore[raw-date-arg]` and immediate `DateInterval.closed(period_from.date(), period_to.date())`. Prefer (a) — cleaner CLI + the lint naturally enforces it.
  - **`datetime.now()` / `date.today()` seams.** These return raw values. The lint is on parameter declarations, not call expressions, so `datetime.now()` calls are unaffected. What IS affected: the recipient of the now-value. The seam fix is "wrap immediately at the call site" — `cli/data.py::run` calls `now = datetime.now()` then constructs `RunContext.live(now=now)` BEFORE passing anything downstream. Downstream takes `RunContext`, not `datetime`.

**Recommendation:** ship D7 + D8 together; D7 catches "wrong convention," D8 catches "no convention at all." Without both, there's a back door.

## Tests required (BC.1)

- Property tests (hypothesis): `DateInterval` invariants — `start <= end`, `.contains(start)`, `.contains(end)`, `not .contains(start - 1d)`, `not .contains(end + 1d)`, `.days == (end - start).days + 1`; `as_half_open_datetimes().duration == timedelta(days=days)`.
- Round-trip: `DateInterval(s, e).as_half_open_datetimes()` then `.start.date() == s`, `.end_exclusive.date() == e + 1d`.
- Constructor errors: `DateInterval(end < start)` raises ValueError; `trailing_days_ending_*(today, 0)` raises ValueError; `DateTimeInterval(end <= start)` raises ValueError; `DateTimeInterval` with `tzinfo` set raises ValueError (single-TZ invariant enforcement).
- Plant schedule invariants: `SingleDayPlant.at_window_end(w).day == w.end`; `SingleDayPlant.at_offset_from_end(w, k).day == w.end - k`; `SingleDayPlant.at_offset_from_end(w, w.days + 1)` raises ValueError (target outside window); `MultiDayPlant.spans(w).iter_days() == list(w.iter_days())`.
- AST lint #1 (`no-naked-interval-ctor`): walks `src/` + `tests/` and asserts no bare `DateInterval(...)` / `DateTimeInterval(...)` / `SingleDayPlant(...)` / `MultiDayPlant(...)` constructor calls outside `common/intervals.py` itself. A planted-violation fixture file proves the lint catches it.
- AST lint #2 (`no-raw-temporal-args`): walks `src/recon_gen/**` function/method signatures and asserts no parameter is annotated `date` / `datetime` (or `| None` variants) outside the wrapper module's own classmethods/`__init__`. Whitelisted: dataclass field annotations (point values, not function params), `# typing-smell: ignore[raw-date-arg]: WHY` escape with required WHY. Staged: ships DISABLED in BC.1; ENABLED at end of BC.5.
- SQL emit (BC.3 module): parametrize `between_clause` + `range_clause` over `(postgres, oracle, sqlite)` and assert the right literal shape per dialect.
- Type round-trip: `frozen=True` makes it hashable; `dict[DateInterval, ...]` works.

## Out of scope (deferred to BD or BC.7)

- **AO.11 frame rollout** (Phase BD, queued in PLAN.md). `AsOfFrame` typed dataclass wrapping `(as_of, window, seed)`; threaded through audit CLI → plant emit → dashboard defaults → QS `RollingDate` derivation. Consumes BC's `DateInterval` + `PlantSchedule` types; not part of BC.
- **Studio trainer state** (`tg_cache.py::window_start/window_end`, `studio_state.py`) — BC.7 sweep candidates. Migrate after audit + dashboard surface settles; trainer cache semantics ("data window" vs "plant window") need their own naming-the-convention pass before flipping.
- **QS analysis-layer date filters** (`common/tree/date_view.py::required_coverage`, `RollingDate` defaults in `apps/*/app.py`) — BD scope, not BC. BD re-derives the `RollingDate` expressions FROM typed `DateInterval` defaults rather than from hand-written strings.

---

**Operator decision needed:** sign off on D1-D8, then BC.1 implements types + tests + both AST lints (D7 enabled, D8 staged-disabled-until-BC.5). Surface any temporal types or callsites I'm missing — especially:
- Places where the single-TZ invariant would be uncomfortable to enforce (any I/O that touches an external system with a known TZ different from the operator's machine?).
- Places where the no-raw-temporal-args lint would force a costly rewrite that the migration shape doesn't anticipate (e.g., heavily-parametrized utility functions in the spine that take dozens of `date` args by convention).
