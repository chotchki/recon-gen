"""Unit tests for the promoted `DateView` (AR.1).

The AP.1 spike (`tests/unit/test_ap1_view_primitive.py`) proved the design
in-process — C1 reproduced + collapsed against the real emitted balance
matview. This file is the production-side gate for the type now living in
`src/recon_gen/common/tree/date_view.py`. It covers the AR.1 authoring
abstraction (frame + empty-behavior + required-coverage + resolve_day);
the renderer-binding tests (DateTimeParam default, dataset StaticValues,
App2 binding all derive from one view) are AR.2's job.
"""

from __future__ import annotations

from datetime import date, timedelta

from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame
from recon_gen.common.tree import DateView, EmptyBehavior


# ---------------------------------------------------------------------------
# Single-date view (frame.window_days == 0). The spike's BalanceDateView
# shape — anchor_day == as_of; required_coverage depends on empty_behavior.
# ---------------------------------------------------------------------------


def test_single_date_view_anchors_at_frame_as_of() -> None:
    view = DateView(frame=AsOfFrame.locked())
    assert view.anchor_day == LOCKED_ANCHOR
    assert view.window_start == LOCKED_ANCHOR  # window_days=0 ⇒ window collapses


def test_single_date_latest_on_empty_required_coverage_is_anything_at_or_before() -> None:
    # "Latest day on or before the anchor" → any prior day satisfies the
    # contract. This is the KPI-style "show me the latest statement" view.
    view = DateView(frame=AsOfFrame.locked(), empty_behavior=EmptyBehavior.LATEST_ON_EMPTY)
    lo, hi = view.required_coverage
    assert lo == date.min
    assert hi == LOCKED_ANCHOR


def test_single_date_show_empty_required_coverage_is_exact_anchor() -> None:
    # "Show empty if anchor has no data" → only the anchor day itself
    # satisfies the contract. Regulator-snapshot view: blank is the right
    # answer when the anchor is unsettled.
    view = DateView(frame=AsOfFrame.locked(), empty_behavior=EmptyBehavior.SHOW_EMPTY)
    lo, hi = view.required_coverage
    assert lo == LOCKED_ANCHOR
    assert hi == LOCKED_ANCHOR


# ---------------------------------------------------------------------------
# Range view (frame.window_days > 0). The L1-universal-range / Exec-30-day
# shape — required_coverage is the look-back, empty_behavior is moot inside
# the window (the audit's §6.5 view taxonomy keeps the two orthogonal).
# ---------------------------------------------------------------------------


def test_range_view_required_coverage_is_the_look_back() -> None:
    view = DateView(frame=AsOfFrame.locked(window_days=7))
    lo, hi = view.required_coverage
    assert lo == LOCKED_ANCHOR - timedelta(days=7)
    assert hi == LOCKED_ANCHOR


def test_range_view_anchor_is_the_right_edge() -> None:
    # For a rolling-N view, the "anchor" is the right edge (= frame.as_of).
    # AR.2's emission layer drives this onto the QS RollingDate default.
    view = DateView(frame=AsOfFrame.locked(window_days=30))
    assert view.anchor_day == LOCKED_ANCHOR
    assert view.window_start == LOCKED_ANCHOR - timedelta(days=30)


# ---------------------------------------------------------------------------
# is_satisfied_by — the checkable seed-coverage contract (AR.3's gate.
# Pinned here for the primitive; AR.3 wires it into the actual seed test).
# ---------------------------------------------------------------------------


def test_is_satisfied_by_latest_on_empty_accepts_any_prior_day() -> None:
    view = DateView(frame=AsOfFrame.locked())
    assert view.is_satisfied_by([LOCKED_ANCHOR])
    assert view.is_satisfied_by([LOCKED_ANCHOR - timedelta(days=5)])
    assert not view.is_satisfied_by([])
    assert not view.is_satisfied_by([LOCKED_ANCHOR + timedelta(days=1)])  # only future


def test_is_satisfied_by_show_empty_requires_exact_anchor() -> None:
    view = DateView(frame=AsOfFrame.locked(), empty_behavior=EmptyBehavior.SHOW_EMPTY)
    assert view.is_satisfied_by([LOCKED_ANCHOR])
    # A day in the past is NOT enough under SHOW_EMPTY — the contract is
    # "exact-match-or-blank."
    assert not view.is_satisfied_by([LOCKED_ANCHOR - timedelta(days=1)])


def test_is_satisfied_by_range_view_needs_data_in_window() -> None:
    view = DateView(frame=AsOfFrame.locked(window_days=7))
    assert view.is_satisfied_by([LOCKED_ANCHOR - timedelta(days=3)])  # in-window
    assert view.is_satisfied_by([LOCKED_ANCHOR])                       # right edge
    assert view.is_satisfied_by([LOCKED_ANCHOR - timedelta(days=7)])  # left edge
    # Outside the window — required-coverage isn't met.
    assert not view.is_satisfied_by([LOCKED_ANCHOR - timedelta(days=30)])
    assert not view.is_satisfied_by([LOCKED_ANCHOR + timedelta(days=5)])


# ---------------------------------------------------------------------------
# resolve_day — empty-behavior applied. The AP.1 spike pinned this directly
# against real emitted balance data; here we cover the primitive's logic.
# ---------------------------------------------------------------------------


def test_resolve_day_latest_on_empty_falls_back_to_latest_prior() -> None:
    view = DateView(frame=AsOfFrame.locked())
    # Anchor present → resolves to anchor.
    assert view.resolve_day([LOCKED_ANCHOR]) == LOCKED_ANCHOR
    # Anchor missing → latest day ≤ anchor.
    earlier = LOCKED_ANCHOR - timedelta(days=2)
    assert view.resolve_day([earlier]) == earlier
    # No data at or before anchor → None (the view literally has nothing).
    assert view.resolve_day([LOCKED_ANCHOR + timedelta(days=1)]) is None


def test_resolve_day_show_empty_honors_the_anchor() -> None:
    view = DateView(frame=AsOfFrame.locked(), empty_behavior=EmptyBehavior.SHOW_EMPTY)
    # Show-empty ALWAYS returns the anchor — even when the data has no
    # row for it. The caller's downstream query will then return zero
    # rows, which IS the correct render under this view's contract.
    assert view.resolve_day([]) == LOCKED_ANCHOR
    assert view.resolve_day([LOCKED_ANCHOR - timedelta(days=5)]) == LOCKED_ANCHOR
