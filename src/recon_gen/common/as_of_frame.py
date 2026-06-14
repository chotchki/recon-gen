"""The owned temporal frame (D1; BD-extended).

`AsOfFrame` is the single anchor both the generator and the views read in
place of wall-clock ``now()``. See `docs/audits/date_range_model_audit.md`
§5 ("step back — time is the unowned coordinate") and the AP.0 spike
result for the reasoning. Promoted from `tests/unit/test_ap0_as_of_frame.py`
by Phase AQ; BD ships the three-leg ``(as_of, window, seed)`` shape on
the same class (no `RunContext` rename — see `docs/audits/_archive/bd_0_asofframe_spike.md`).

Three fields:

  * ``as_of: date`` — the calendar day this run anchors to. The
    "right edge" of the system's temporal frame.
  * ``window: DateInterval`` — the closed-closed date range this run
    queries / plants into. NOT constrained to end at ``as_of`` (audit
    windows end yesterday; trainer windows span the operator's
    scenario; ``as_of`` is independent).
  * ``seed: int | None`` — RNG seed for deterministic outputs. None
    means "use the spine's internal default" (production live mode).

Four named constructors (the recommended call shapes):

  * ``AsOfFrame.locked(window_days=N)`` — demo/test binding. Anchor
    pinned at `LOCKED_ANCHOR`. ``window_days=N`` is an ergonomic
    shortcut that internally builds a closed-closed N-day window
    ending at the anchor.
  * ``AsOfFrame.live(window_days=N)`` — production binding. Anchor
    via ``date.today()`` (the single blessed wall-clock read in this
    codebase). Same ``window_days=N`` shortcut.
  * ``AsOfFrame.for_audit(today, *, lookback_days)`` — audit-CLI
    binding. Window = ``trailing_days_ending_yesterday(today,
    lookback_days)`` (today excluded, the audit-window convention).
  * ``AsOfFrame.for_test(*, window, seed, as_of=None)`` — test
    fixtures' explicit shape. ``seed`` required because tests are
    deterministic by construction; ``as_of`` defaults to
    ``window.end``.

What is intentionally NOT here: renderer-specific derivations (QS
`RollingDate` exprs, App2 sentinels, picker defaults). Those live on the
`View` primitive (Phase AR) which takes an `AsOfFrame` as its anchor; one
view object emits both QS and App2 bindings so the C1 dual-default split
becomes unrepresentable.

BD.0 D6 (locked 2026-05-25 per the no-compat-shims principle): the v1
``window_days: int`` field is GONE. Callers that read ``frame.window_days``
migrate to ``frame.window.days`` (closed-closed count) or
``frame.window.start`` (left edge — the dominant pattern, already
encapsulated by the ``window_start`` property which now derives from
``frame.window.start``). The ``window_days=N`` keyword arg stays on
``live()`` / ``locked()`` as a construction-time ergonomic shortcut —
construction-time ergonomics ≠ runtime escape hatch.

`LOCKED_ANCHOR` is the single source of truth for the demo anchor (AQ.3
funneled the value here; BD.6 retired the `cli/data.py::_CANONICAL_LOCK_ANCHOR`
alias kept for caller-compat). Every locked seed + every locked-binding
frame anchors here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from recon_gen.common.intervals import DateInterval

if TYPE_CHECKING:
    from recon_gen.common.db import SyncConnection

#: The canonical demo anchor — every locked seed + every locked-binding
#: frame anchors here. Single source of truth post-BD.6
#: (`_CANONICAL_LOCK_ANCHOR` alias in `cli/data.py` retired).
LOCKED_ANCHOR: Final[date] = date(2030, 1, 1)


@dataclass(frozen=True)
class AsOfFrame:
    """The owned temporal frame: anchor (as_of) + queried window + seed.

    Every temporal predicate the system reads — the generator's data-end
    day, a view's window bounds, "latest" semantics, RNG determinism —
    derives from this one object instead of wall-clock ``now()``,
    parallel `period: DateInterval` args, or scattered `seed: int`
    kwargs. The locked-vs-live split is a single binding choice rather
    than three independent encodings (the C1 release-blocker shape).
    """

    as_of: date
    window: DateInterval
    seed: int | None = None

    # -- named-convention constructors --

    @classmethod
    def locked(
        cls, *, window_days: int = 0, seed: int | None = None,
    ) -> "AsOfFrame":
        """Demo/test binding — anchor pinned at `LOCKED_ANCHOR`.

        ``window_days`` is an ergonomic shortcut: 0 means single-day,
        N>0 means a closed-closed N-day window ending at the anchor.
        (Internally builds the `DateInterval` — the FIELD on the
        resulting object is `window`, not the int.)
        """
        window = (
            DateInterval.single_day(LOCKED_ANCHOR)
            if window_days <= 0
            else DateInterval.trailing_days_ending_today(
                LOCKED_ANCHOR, window_days + 1,
            )
        )
        return cls(as_of=LOCKED_ANCHOR, window=window, seed=seed)

    @classmethod
    def live(
        cls, *, window_days: int = 0, seed: int | None = None,
    ) -> "AsOfFrame":
        """Production binding — anchor = today. Same derivations as
        `locked()`; only the bound anchor value differs (the §8
        determinism story falls out of the frame for free).

        ``window_days`` ergonomic shortcut — see `locked` for the
        construction shape.

        v14.0.0 — when ``RECON_GEN_AS_OF_ANCHOR`` is set (the runner
        exports it at chain start as an ISO date), use that pin
        instead of ``date.today()``. This makes the chain-wide
        ``as_of`` agree across deploy + dataset emit + qs_browser test
        even when the run straddles local midnight. Operators outside
        the runner (bare CLI, ad-hoc data apply) hit the unchanged
        ``date.today()`` path.
        """
        today = _as_of_today()
        window = (
            DateInterval.single_day(today)
            if window_days <= 0
            else DateInterval.trailing_days_ending_today(
                today, window_days + 1,
            )
        )
        return cls(as_of=today, window=window, seed=seed)

    @classmethod
    def live_from_db(
        cls,
        conn: "SyncConnection",
        prefix: str,
        *,
        window_days: int = 0,
        seed: int | None = None,
    ) -> "AsOfFrame":
        """v13.6.1 fix #3 — data-derived production binding.

        Anchor = ``MAX(business_day_start)`` over
        ``<prefix>_current_daily_balances`` (the most recent emitted
        day in the demo DB), falling back to ``date.today()`` when
        the table is empty OR the query fails. Solves the
        "operator opens dashboards before today's ETL load lands +
        picker defaults to today + nothing emitted yet + dashboard
        renders blank" UX. With the fix #2 spine widening, the
        carried row would already exist; this gets the picker on
        the latest-with-data day so the operator sees the most
        useful default.

        ``conn`` is the demo-DB connection (SyncConnection — any
        of psycopg / oracledb / duckdb). ``prefix`` is the L2
        instance's ``db_table_prefix``. Both required because
        ``current_daily_balances`` is per-instance prefixed.

        The locked-binding path is untouched: ``locked()`` /
        ``live()`` keep their current shapes. This sibling is the
        DB-aware variant; ``cfg.test.generator.as_of_frame()``
        accepts a ``db_anchor`` override that funnels through here.
        """
        anchor = _query_max_balance_day(conn, prefix)
        if anchor is None:
            return cls.live(window_days=window_days, seed=seed)
        window = (
            DateInterval.single_day(anchor)
            if window_days <= 0
            else DateInterval.trailing_days_ending_today(
                anchor, window_days + 1,
            )
        )
        return cls(as_of=anchor, window=window, seed=seed)

    @classmethod
    def for_audit(
        cls, today: date, *, lookback_days: int, seed: int | None = None,
    ) -> "AsOfFrame":
        """Audit-CLI binding. Window =
        ``DateInterval.trailing_days_ending_yesterday(today, lookback_days)``
        — N days ending yesterday, today EXCLUDED (the audit-window
        convention — "you can't audit a day that hasn't closed yet").
        ``as_of = today`` (the day the audit runs).
        """
        window = DateInterval.trailing_days_ending_yesterday(
            today, lookback_days,
        )
        return cls(as_of=today, window=window, seed=seed)

    @classmethod
    def for_test(
        cls,
        *,
        window: DateInterval,
        seed: int = 0,
        as_of: date | None = None,
    ) -> "AsOfFrame":
        """Test-fixture binding — explicit window + seed.

        ``seed`` defaults to ``0`` (a known-good deterministic seed);
        pass ``None`` to opt into the spine's internal default. ``as_of``
        defaults to ``window.end`` (the most-recently-closed day in the
        window — matches the audit-window-end convention).
        """
        anchor = as_of if as_of is not None else window.end
        return cls(as_of=anchor, window=window, seed=seed)

    # -- queries / derived helpers --

    @property
    def window_start(self) -> date:
        """The window's left edge — ``self.window.start``.

        Kept for backward compat with existing callers that read
        `frame.window_start` directly. New code prefers
        `frame.window.start`.
        """
        return self.window.start

    def contains(self, day: date) -> bool:
        """Is ``day`` inside ``self.window`` (closed-closed)?

        BD.1 — the semantic is now "in the window," not "in
        ``[window_start, as_of]``." For frames where ``window.end ==
        as_of`` (the dominant pre-BD pattern), the two are equivalent;
        for audit frames where ``window`` ends yesterday and ``as_of``
        is today, the new semantic is the right one — we're checking
        whether the day falls in the QUERIED range, not the wall-clock
        epoch.
        """
        return self.window.contains(day)


def _as_of_today() -> date:
    """The single blessed wall-clock read in this codebase — every
    other site reads ``frame.as_of`` via ``AsOfFrame.live()``. AQ.3
    funneled the 4 ad-hoc ``date.today()`` fallbacks through here; v14
    adds the ``RECON_GEN_AS_OF_ANCHOR`` env override.
    """
    from recon_gen.common.env_keys import RECON_GEN_AS_OF_ANCHOR  # noqa: PLC0415
    pinned = RECON_GEN_AS_OF_ANCHOR.get_or_none()
    if pinned is not None:
        return pinned
    return date.today()  # typing-smell: ignore[no-datetime-now]: this is the SINGLE blessed wall-clock read in the codebase — every other site reads ``frame.as_of`` via ``AsOfFrame.live()``


def _query_max_balance_day(
    conn: "SyncConnection", prefix: str,
) -> date | None:
    """v13.6.1 fix #3 helper — query
    ``SELECT MAX(business_day_start)::date FROM <prefix>_current_daily_balances``.

    Returns the most recent emitted business day as a ``date``, or
    ``None`` when the table is empty / the query fails. Best-effort
    by design: a hung / closed DB connection should NOT crash the
    dashboards' render path — falling back to wall-clock today is
    the right safety net, surfaced by the caller via the
    ``AsOfFrame.live()`` shape.
    """
    sql = (
        f"SELECT MAX(business_day_start) "
        f"FROM {prefix}_current_daily_balances "
        f"WHERE account_scope = 'internal'"
    )
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            row = cur.fetchone()
        finally:
            cur.close()
    except Exception:  # noqa: BLE001 — defensive: a DB error here MUST NOT crash the dashboard render
        return None
    if row is None or row[0] is None:
        return None
    candidate = row[0]
    # Drivers return either a date (PG / DuckDB native DATE) or a
    # datetime (PG / DuckDB on TIMESTAMP columns, Oracle's DATE).
    # Coerce to date — the field is conceptually a calendar day.
    from datetime import datetime as _dt  # noqa: PLC0415
    if isinstance(candidate, _dt):
        return candidate.date()
    if isinstance(candidate, date):
        return candidate
    # Defensive: an unexpected type means we shouldn't trust the
    # data-derive result. Fall back to wall-clock via None.
    return None
