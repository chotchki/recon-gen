"""AP.1 spike — the view primitive (D5): invert the derivation, kill C1.

Last of the four AP spikes (sibling to AP.0 frame / AP.2 stateful generator /
AP.3 self-validation). The audit (`docs/audits/date_range_model_audit.md` §5
"residual tension", §6.5, D5) names the cause of the release blocker C1:

    a *subjective view* (here the L1 Daily Statement single balance-date) is
    split across TWO independently-authored defaults — the analysis-param
    default (`RollingDate now-1d`, off wall-clock) and the dataset-param
    default (`2999-12-31` latest sentinel) — bridged by
    MappedDataSetParameters. QS pushes the analysis "yesterday" into the
    dataset param (the sentinel never applies) → no row at the 2030 anchor →
    0 rows → the KPIs don't render. App2 ignores the analysis RollingDate and
    uses the dataset sentinel → latest → renders. The QS/App2 split IS this
    dual-default disagreement.

D5's fix is a **derivation inversion**: today the picker control + RollingDate
expr ARE the definition; invert so a single typed `View` object is the source
of truth and the picker control, the analysis-param default, the dataset-param
default, AND the App2 binding all *derive* from it. One view → one default →
the renderers cannot diverge (C1 unrepresentable, not merely fixed). The View
carries `(anchor=as_of, span, empty-behavior, required-coverage)` and is an
*authoring* abstraction (need not be end-user-configurable).

This spike, on ONE view (balance date), (1) REPRODUCES C1 in-process against
real emitted balance data — the two legacy defaults resolve to different rows;
(2) shows one `BalanceDateView` emitting both QS defaults identically so the
divergence is gone behaviorally; (3) shows all four bindings derive from the
one view; (4) exercises empty-behavior (latest-on-empty) and required-coverage
(the checkable seed contract that replaces developer-memory).

Gated on AP.0: the view's anchor is an `AsOfFrame` (the owned `as_of`, not
now()). Vocab is LOCAL to the spike (not promoted to `src/`); the tree-carry
verdict + rollout shape go to audit §5. FINDINGS inline.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum, auto
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE
_LOCKED_ANCHOR = date(2030, 1, 1)


# ---------------------------------------------------------------------------
# AP.0's frame, re-stated minimally (the view's anchor — the owned as_of).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AsOfFrame:
    as_of: date
    window_days: int = 0

    @classmethod
    def locked(cls, *, window_days: int = 0) -> "AsOfFrame":
        return cls(as_of=_LOCKED_ANCHOR, window_days=window_days)


# ---------------------------------------------------------------------------
# The view primitive — LOCAL to the spike. ONE source of truth for a
# subjective view-window; all four renderer bindings derive from it.
# ---------------------------------------------------------------------------


class EmptyBehavior(Enum):
    LATEST_ON_EMPTY = auto()  # no row for the anchor day → fall back to latest ≤ anchor
    SHOW_EMPTY = auto()       # honor the anchor literally even if it has no rows


@dataclass(frozen=True)
class BalanceDateView:
    """A single balance-date view. `anchor_day` (= frame.as_of) is the ONE
    default every binding derives from — there is no separate analysis vs
    dataset default to disagree. `empty_behavior` + `required_coverage` make
    the view's limit explicit and checkable instead of silent-blank."""

    frame: AsOfFrame
    empty_behavior: EmptyBehavior = EmptyBehavior.LATEST_ON_EMPTY

    # --- the single source of truth -----------------------------------------
    def anchor_day(self) -> date:
        return self.frame.as_of

    # --- the four DERIVED bindings (today authored independently → C1) ------
    def qs_analysis_default_day(self) -> date:
        # replaces RollingDate(now-1d): a concrete day off `as_of`, not now().
        return self.anchor_day()

    def qs_dataset_default_day(self) -> date:
        # replaces the StaticValues 2999 sentinel: the SAME day, one source.
        return self.anchor_day()

    def picker_default_day(self) -> date:
        return self.anchor_day()

    def app2_date_to(self) -> str:
        return self.anchor_day().isoformat()

    # --- the view's stated limit (replaces developer-memory) ----------------
    def required_coverage(self) -> tuple[date, date]:
        """A single-date latest-fallback view needs ≥1 balance row at or
        before the anchor. (A range view would return [anchor-span, anchor].)"""
        return (date.min, self.anchor_day())

    def is_satisfied_by(self, available_days: list[date]) -> bool:
        lo, hi = self.required_coverage()
        return any(lo <= d <= hi for d in available_days)

    def resolve_day(self, available_days: list[date]) -> date | None:
        """Apply empty-behavior: the anchor if present, else (LATEST_ON_EMPTY)
        the latest day ≤ anchor, else (SHOW_EMPTY) the anchor literally."""
        if self.empty_behavior is EmptyBehavior.SHOW_EMPTY:
            return self.anchor_day()
        if self.anchor_day() in available_days:
            return self.anchor_day()
        earlier = [d for d in available_days if d <= self.anchor_day()]
        return max(earlier) if earlier else None


# --- the LEGACY (pre-D5) defaults — two independent authors, the C1 cause ---


def _legacy_analysis_default_day() -> date:
    """QS RollingDate `addDateTime(-1,'DD',truncDate('DD',now()))` — yesterday
    off WALL-CLOCK. Anchored to now(), NOT the scenario's as_of."""
    return date.today() - timedelta(days=1)


def _legacy_dataset_default_day(available_days: list[date]) -> date:
    """Dataset StaticValues `2999-12-31` → the SQL latest-day fallback."""
    return max(available_days)


# ---------------------------------------------------------------------------
# In-process harness — real emitted schema + refresh, no DB server.
# ---------------------------------------------------------------------------


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


_DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money",
)


def _emit_balance(conn: sqlite3.Connection, *, day: date, money: float) -> None:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    row = {
        "account_id": "acct-view", "account_name": "View Acct",
        "account_role": "CustomerSubledger", "account_scope": "internal",
        "account_parent_role": "CustomerLedger", "expected_eod_balance": None,
        "business_day_start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "business_day_end": (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "money": money,
    }
    placeholders = ", ".join("?" for _ in _DB_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_daily_balances ({', '.join(_DB_COLS)}) "
        f"VALUES ({placeholders})",
        [row[c] for c in _DB_COLS],
    )


def _emit_days(conn: sqlite3.Connection, days: list[date]) -> None:
    for i, day in enumerate(days):
        _emit_balance(conn, day=day, money=100.0 * (i + 1))
    conn.commit()


def _available_days(conn: sqlite3.Connection) -> list[date]:
    rows = conn.execute(
        f"SELECT business_day_start FROM {_PREFIX}_current_daily_balances "
        f"WHERE account_id = 'acct-view'",
    ).fetchall()
    return [datetime.strptime(str(r[0])[:10], "%Y-%m-%d").date() for r in rows]


def _kpi_rowcount_for_day(conn: sqlite3.Connection, picked: date) -> int:
    """The Daily Statement KPI summary, narrowed to one balance date — the
    query that returned 0 rows under C1. Mirrors the `<<$pL1DsBalanceDate>>`
    single-date pushdown."""
    (count,) = conn.execute(
        f"SELECT COUNT(*) FROM {_PREFIX}_current_daily_balances "
        f"WHERE account_id = 'acct-view' AND date(business_day_start) = date(?)",
        (picked.isoformat(),),
    ).fetchone()
    return int(count)


def _locked_db() -> sqlite3.Connection:
    # Data ends at the locked anchor (the fold's terminal day) — exactly the
    # situation where now()-anchored RollingDate defaults look at empty space.
    conn = _fresh_db()
    _emit_days(conn, [_LOCKED_ANCHOR - timedelta(days=n) for n in (4, 3, 2, 1, 0)])
    _refresh(conn)
    return conn


# ---------------------------------------------------------------------------
# (1) Reproduce C1 in-process: the two legacy defaults resolve different rows.
# ---------------------------------------------------------------------------


def test_c1_reproduced_legacy_dual_defaults_diverge() -> None:
    conn = _locked_db()
    try:
        avail = _available_days(conn)
        analysis_day = _legacy_analysis_default_day()  # yesterday off now() (~2026)
        dataset_day = _legacy_dataset_default_day(avail)  # 2999 → latest (2030)
        analysis_rows = _kpi_rowcount_for_day(conn, analysis_day)
        dataset_rows = _kpi_rowcount_for_day(conn, dataset_day)
    finally:
        conn.close()
    # The bug: QS resolves the analysis default (yesterday) → empty; App2 the
    # dataset default (latest) → rows. Same param, two defaults, two results.
    assert analysis_rows == 0, "legacy analysis default (now-1d) lands off the data"
    assert dataset_rows == 1, "legacy dataset default (latest) lands on data"
    assert analysis_rows != dataset_rows, "this divergence IS C1"


# ---------------------------------------------------------------------------
# (2) One view emits both QS defaults identically — C1 gone (value + behavior).
# ---------------------------------------------------------------------------


def test_one_view_emits_both_qs_defaults_identically() -> None:
    view = BalanceDateView(frame=AsOfFrame.locked())
    # There is ONE default day; both sides emit it. MappedDataSetParameters
    # bridging is now a no-op divergence — nothing to keep in sync.
    assert view.qs_analysis_default_day() == view.qs_dataset_default_day()
    assert view.qs_analysis_default_day() == view.anchor_day()


def test_view_default_resolves_to_rows_on_both_sides() -> None:
    conn = _locked_db()
    try:
        view = BalanceDateView(frame=AsOfFrame.locked())
        a_rows = _kpi_rowcount_for_day(conn, view.qs_analysis_default_day())
        d_rows = _kpi_rowcount_for_day(conn, view.qs_dataset_default_day())
    finally:
        conn.close()
    # Both renderers now resolve the same day (= as_of, which has data) →
    # identical, non-zero. The KPIs render on QS too.
    assert a_rows == d_rows == 1


# ---------------------------------------------------------------------------
# (3) The derivation inversion: all four bindings derive from the one view.
# ---------------------------------------------------------------------------


def test_all_four_bindings_derive_from_one_source() -> None:
    view = BalanceDateView(frame=AsOfFrame.locked())
    src = view.anchor_day()
    assert view.qs_analysis_default_day() == src
    assert view.qs_dataset_default_day() == src
    assert view.picker_default_day() == src
    assert view.app2_date_to() == src.isoformat()
    # cross-renderer agreement — the property C1 violated, now structural:
    assert view.app2_date_to() == view.qs_dataset_default_day().isoformat()


# ---------------------------------------------------------------------------
# (4) Empty-behavior + required-coverage: the view states its own limit.
# ---------------------------------------------------------------------------


def test_empty_behavior_latest_falls_back() -> None:
    # Anchor day has no row (data ends the day before as_of); LATEST_ON_EMPTY
    # resolves to the latest day ≤ anchor → renders, doesn't go blank.
    conn = _fresh_db()
    try:
        _emit_days(conn, [_LOCKED_ANCHOR - timedelta(days=n) for n in (3, 2, 1)])
        _refresh(conn)
        avail = _available_days(conn)
        view = BalanceDateView(frame=AsOfFrame.locked())  # anchor = _LOCKED_ANCHOR
        resolved = view.resolve_day(avail)
        assert resolved == _LOCKED_ANCHOR - timedelta(days=1)
        assert resolved is not None
        assert _kpi_rowcount_for_day(conn, resolved) == 1

        strict = BalanceDateView(
            frame=AsOfFrame.locked(), empty_behavior=EmptyBehavior.SHOW_EMPTY,
        )
        strict_day = strict.resolve_day(avail)
        assert strict_day == _LOCKED_ANCHOR  # honors the empty day
        assert strict_day is not None
        assert _kpi_rowcount_for_day(conn, strict_day) == 0
    finally:
        conn.close()


def test_required_coverage_is_a_checkable_contract() -> None:
    # The seed-coverage assertion that replaces developer-memory: the view
    # declares the data it needs; a scenario either satisfies it or the view
    # fails loud BEFORE render (not silent-blank at the dashboard).
    view = BalanceDateView(frame=AsOfFrame.locked())
    assert view.is_satisfied_by([_LOCKED_ANCHOR - timedelta(days=1)])  # ≤ anchor: ok
    assert view.is_satisfied_by([_LOCKED_ANCHOR])                      # at anchor: ok
    assert not view.is_satisfied_by([])                               # no data: unmet
    assert not view.is_satisfied_by([_LOCKED_ANCHOR + timedelta(days=5)])  # only future
