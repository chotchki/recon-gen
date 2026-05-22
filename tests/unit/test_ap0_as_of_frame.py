"""AP.0 spike — own the `as_of` frame (D1), the keystone.

Sibling to the AP.2 / AP.3 spikes. The audit (`docs/audits/
date_range_model_audit.md` §5) names time as the *unowned coordinate*: the
dashboards improvise their temporal predicates from wall-clock `now()` (QS
`truncDate('DD', now())` rolling defaults, App2 sentinel bounds) while the
generator improvises data placement from a seed anchor. **Two clocks that
should be one.** Every §4 conflict — and the C1 release blocker — is a symptom
of that one gap.

D1's fix: a single owned `as_of` anchor (+ a window span) that BOTH directions
read instead of `now()`:

  * Production:  `as_of = now()`  — data genuinely flows up to now.
  * Demo / test: `as_of = the fixed scenario anchor` — data frozen there.

AP.0 spikes that frame on ONE surface (balance date — the natural guinea pig,
per the task) and answers: does owning `as_of` (a) collapse the QS/App2
dual-default into one derivation (structurally killing C1), (b) stay
deterministic under a locked anchor AND end-at-now under a live anchor *by the
same code path*, and (c) anchor the generator's data-end and a view's
window-end to the SAME value, so a plant at `as_of` is inside the view by
construction (the plant ⟷ query-window contract becomes checkable, not
developer-memory)?

SCOPE / honest limit (per the task: unit-test level, no db/deploy/e2e). The
"both renderers" claim is proven at the level the unit layer can reach: ONE
`AsOfFrame` emits BOTH the QS-side date-window bounds AND the App2-side
`date_from`/`date_to` bind values, and they are EQUAL. Today (surveyed) those
are two hand-maintained encodings — `truncDate('DD', now())` on the QS side,
a `1900`/`2999` sentinel on the App2 side — that can (and did, C1) diverge.
The spike proves the VALUES agree from one source; live-rendered parity stays
behind the parked deploy/e2e layers.

The frame vocab lives LOCAL to the spike (not promoted to `src/`), same
discipline as AP.2 / AP.3. FINDINGS fold back into audit §5.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE

# The locked scenario anchor the codebase already uses for byte-identical seed
# SQL (cli/data.py::_CANONICAL_LOCK_ANCHOR). The frame's whole point is that
# THIS is the only thing that floats: bind it fixed → deterministic; bind it
# now() → ends-at-now; one code path either way.
_LOCKED_ANCHOR = date(2030, 1, 1)


# ---------------------------------------------------------------------------
# The frame — LOCAL to this spike. A single owned `as_of` + window span; both
# the generator and the views derive their temporal bounds from it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AsOfFrame:
    """The owned temporal frame (D1). `as_of` is the scenario's "now"; the
    window is a view's look-back span. EVERY temporal bound — generator
    data-end, QS date-default, App2 bind value — derives from this one object,
    so the two-clocks divergence (C1) becomes unrepresentable: there is one
    `as_of`, not a QS `now()` and an App2 sentinel that must be kept in sync.
    """

    as_of: date
    window_days: int

    @classmethod
    def locked(cls, *, window_days: int) -> "AsOfFrame":
        """Demo/test binding — frozen at the canonical anchor → deterministic."""
        return cls(as_of=_LOCKED_ANCHOR, window_days=window_days)

    @classmethod
    def live(cls, *, window_days: int) -> "AsOfFrame":
        """Production binding — `as_of = today` → data ends at now. SAME code
        path as `locked`; only the anchor value differs (the §8 determinism
        story falls out for free)."""
        return cls(as_of=date.today(), window_days=window_days)

    @property
    def window_start(self) -> date:
        return self.as_of - timedelta(days=self.window_days)

    # --- generator side: data ends AT as_of (the fold's terminal day) -------
    def data_end_day(self) -> date:
        return self.as_of

    # --- view side: ONE definition the renderers derive from ----------------
    # QS today: a RollingDate expr off now(); App2 today: a sentinel literal.
    # Here both come from the same frame, so they CANNOT diverge.
    def qs_window_start(self) -> date:
        return self.window_start

    def qs_window_end(self) -> date:
        return self.as_of

    def app2_date_from(self) -> str:
        return self.window_start.isoformat()

    def app2_date_to(self) -> str:
        return self.as_of.isoformat()

    def contains(self, day: date) -> bool:
        """The view's required-coverage predicate: is `day` inside
        [window_start, as_of]? (Inclusive both ends.)"""
        return self.window_start <= day <= self.as_of


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


def _ts_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _emit_balance(conn: sqlite3.Connection, *, day: date, money: float) -> None:
    start, end = _ts_bounds(day)
    row = {
        "account_id": "acct-frame", "account_name": "Frame Acct",
        "account_role": "CustomerSubledger", "account_scope": "internal",
        "account_parent_role": "CustomerLedger", "expected_eod_balance": None,
        "business_day_start": start, "business_day_end": end, "money": money,
    }
    placeholders = ", ".join("?" for _ in _DB_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_daily_balances ({', '.join(_DB_COLS)}) "
        f"VALUES ({placeholders})",
        [row[c] for c in _DB_COLS],
    )


def _latest_balance_day(conn: sqlite3.Connection) -> date:
    (raw,) = conn.execute(
        f"SELECT MAX(business_day_start) FROM {_PREFIX}_current_daily_balances "
        f"WHERE account_id = 'acct-frame'",
    ).fetchone()
    return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()


def _emit_fold_to(frame: AsOfFrame, *, days: int) -> sqlite3.Connection:
    """Emit `days` daily balances ending exactly at `frame.data_end_day()` —
    the fold's terminal day IS `as_of` (the generator reads the frame, not
    now()). Each day's stored balance is a clean running total."""
    conn = _fresh_db()
    balance = 0.0
    for offset in range(days - 1, -1, -1):
        day = frame.data_end_day() - timedelta(days=offset)
        balance += 100.0
        _emit_balance(conn, day=day, money=balance)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# (a) The frame collapses the QS/App2 dual-default into one derivation (C1).
# ---------------------------------------------------------------------------


def test_one_frame_drives_both_renderers_to_the_same_bounds() -> None:
    # The structural kill of C1: today the QS end-default (now()-rolling) and
    # the App2 date_to (a sentinel) are independent encodings that diverged.
    # With one frame, both derive from `as_of` — equality is by construction,
    # there is nothing to keep in sync.
    frame = AsOfFrame.locked(window_days=7)
    assert frame.qs_window_end() == frame.as_of
    assert frame.app2_date_to() == frame.as_of.isoformat()
    assert frame.qs_window_start() == frame.window_start
    assert frame.app2_date_from() == frame.window_start.isoformat()
    # cross-renderer agreement (the property C1 violated):
    assert frame.app2_date_to() == frame.qs_window_end().isoformat()
    assert frame.app2_date_from() == frame.qs_window_start().isoformat()


# ---------------------------------------------------------------------------
# (b) Deterministic under a locked anchor; ends-at-now under a live anchor;
#     ONE code path (only the bound anchor value differs).
# ---------------------------------------------------------------------------


def test_locked_frame_is_deterministic() -> None:
    a = AsOfFrame.locked(window_days=7)
    b = AsOfFrame.locked(window_days=7)
    assert a == b == AsOfFrame(as_of=_LOCKED_ANCHOR, window_days=7)
    assert a.qs_window_end() == _LOCKED_ANCHOR  # not wall-clock
    assert a.window_start == date(2029, 12, 25)


def test_live_frame_ends_at_now_via_same_code_path() -> None:
    live = AsOfFrame.live(window_days=30)
    # Ends at now; the derivations are the SAME methods the locked frame used.
    assert live.as_of == date.today()
    assert live.qs_window_end() == date.today()
    assert live.window_start == date.today() - timedelta(days=30)
    # locked vs live differ ONLY in the anchor — the derivation is identical:
    locked = AsOfFrame.locked(window_days=30)
    assert (live.as_of - live.window_start) == (locked.as_of - locked.window_start)


# ---------------------------------------------------------------------------
# (c) The frame anchors generator data-end AND view window to ONE value, so a
#     plant at `as_of` is inside the view by construction — and "latest" is the
#     fold's terminal state at `as_of`, not wall-clock (the AP.2 D1 duality).
# ---------------------------------------------------------------------------


def test_fold_data_ends_at_as_of_and_latest_is_the_terminal_state() -> None:
    # Run under BOTH bindings; "latest balance day" == frame.as_of either way,
    # by the SAME emission code. This is AP.2's duality made concrete: "latest"
    # is the terminal State' of the fold at `as_of`, never a now() filter.
    for frame in (AsOfFrame.locked(window_days=7), AsOfFrame.live(window_days=7)):
        conn = _emit_fold_to(frame, days=5)
        try:
            _refresh(conn)
            assert _latest_balance_day(conn) == frame.as_of
        finally:
            conn.close()


def test_plant_at_as_of_is_inside_the_view_window_by_construction() -> None:
    # The plant ⟷ query-window contract (audit §5): a violation planted at the
    # data-end (`as_of`) is GUARANTEED inside the view's [window_start, as_of]
    # — because both sides read the same frame. Today this agreement is
    # developer-memory; here it is a property of the frame.
    frame = AsOfFrame.locked(window_days=7)
    assert frame.contains(frame.data_end_day())          # plant at as_of: in
    assert frame.contains(frame.as_of - timedelta(days=7))  # window_start: in
    assert not frame.contains(frame.as_of - timedelta(days=8))  # just outside
    assert not frame.contains(frame.as_of + timedelta(days=1))  # future: out


def test_a_narrow_view_states_its_own_coverage_limit() -> None:
    # The residual-tension point (audit §5): a view's window carries an implicit
    # precondition — data must exist in [window_start, as_of]. A 1-day view off
    # `as_of` does NOT cover a plant 3 days back; that's the view hitting its
    # STATED limit, not a silent "bug". Owning the frame makes the limit
    # inspectable BEFORE render (the AP.1 view-primitive will carry it).
    narrow = AsOfFrame.locked(window_days=1)
    plant_day = narrow.as_of - timedelta(days=3)
    assert not narrow.contains(plant_day)
    wide = AsOfFrame.locked(window_days=7)
    assert wide.contains(plant_day)  # widen the view → the plant is covered
