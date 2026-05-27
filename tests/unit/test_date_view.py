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
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
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


# ---------------------------------------------------------------------------
# AR.2 — renderer emissions. ONE source (anchor_day) drives every binding.
# The C1 dual-default split is structurally unrepresentable.
# ---------------------------------------------------------------------------


def test_qs_analysis_default_is_a_static_value_at_anchor() -> None:
    view = DateView(frame=AsOfFrame.locked())
    default = view.emit_qs_analysis_default()
    # Strict-collapse: StaticValues, not RollingDate. The deploy bakes
    # the anchor; no wall-clock drift between deploys.
    assert default.StaticValues == ["2030-01-01T00:00:00"]
    assert default.RollingDate is None


def test_qs_dataset_default_emits_the_same_day_as_analysis() -> None:
    # The C1 collapse, value-level: both QS defaults derive from one
    # source, so they cannot disagree. AR.2's structural fix.
    view = DateView(frame=AsOfFrame.locked())
    analysis = view.emit_qs_analysis_default()
    dataset = view.emit_qs_dataset_default()
    assert analysis.StaticValues == dataset.StaticValues


def test_app2_date_to_matches_qs_dataset_value() -> None:
    # App2 reads dataset.StaticValues[0] directly; the bind value the
    # view emits and the dataset StaticValues must encode the same day.
    view = DateView(frame=AsOfFrame.locked())
    dataset = view.emit_qs_dataset_default()
    assert dataset.StaticValues is not None
    assert dataset.StaticValues[0].startswith(view.emit_app2_date_to())


def test_single_date_app2_date_from_equals_date_to() -> None:
    # Span=0 ⇒ the look-back collapses; date_from == date_to (the
    # single picked day).
    view = DateView(frame=AsOfFrame.locked())
    assert view.emit_app2_date_from() == view.emit_app2_date_to()


def test_range_app2_date_from_is_the_window_start() -> None:
    view = DateView(frame=AsOfFrame.locked(window_days=7))
    assert view.emit_app2_date_from() == "2029-12-25"  # 2030-01-01 minus 7d
    assert view.emit_app2_date_to() == "2030-01-01"


def test_all_three_renderer_bindings_share_one_source() -> None:
    # The audit's derivation-inversion claim made into a property test:
    # picker / dataset / App2 all carry the same concrete day, because
    # they all read from anchor_day. There's literally one source.
    view = DateView(frame=AsOfFrame.locked())
    analysis = view.emit_qs_analysis_default()
    dataset = view.emit_qs_dataset_default()
    assert analysis.StaticValues is not None
    assert dataset.StaticValues is not None
    iso_day = view.emit_app2_date_to()
    assert analysis.StaticValues[0] == f"{iso_day}T00:00:00"
    assert dataset.StaticValues[0] == f"{iso_day}T00:00:00"


# ---------------------------------------------------------------------------
# AR.3 — the plant ⟷ query-window contract becomes a TEST. A view's
# `required_coverage` is a stated precondition; the seed must satisfy it
# or a planted violation will fall outside the window the dashboard
# scans (silent-blank class of bug). Pinning it for the L1 balance-date
# view here makes the regression a unit-layer fail, not a discovery at
# the deploy/visual layer.
# ---------------------------------------------------------------------------


def _extract_balance_days(sql: str, anchor_year: int) -> list[date]:
    """Pull `YYYY-MM-DD` literals out of the emitted seed SQL whose year
    matches the anchor — filters out year-2099 / year-2999 sentinels +
    template literals that aren't actual baseline placements."""
    import re
    out: set[date] = set()
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", sql):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y != anchor_year:
            continue
        try:
            out.add(date(y, mo, d))
        except ValueError:
            continue
    return sorted(out)


def test_balance_date_view_required_coverage_is_satisfied_by_locked_seed() -> None:
    # The L1 balance-date view (constructed in `apps/l1_dashboard/datasets.py`
    # via `cfg.test_generator.as_of_frame()`) must have its
    # `required_coverage` actually satisfied by the locked seed. Today
    # that's trivial — a single-date LATEST_ON_EMPTY view accepts any day
    # ≤ anchor, and the 90-day baseline gives plenty. The TEST guards
    # against a future narrowing: if the view ever moves to SHOW_EMPTY
    # at an unsupported anchor, or to a range that exceeds the baseline
    # window, this fires BEFORE the dashboard goes blank in production.
    from pathlib import Path
    from recon_gen.cli._helpers import build_full_seed_sql
    from recon_gen.common.config import TestGeneratorConfig
    from recon_gen.common.l2 import load_instance
    from tests._test_helpers import make_test_config

    cfg = make_test_config(
        db_table_prefix=DEFAULT_PREFIX,
        test_generator=TestGeneratorConfig(end_date=LOCKED_ANCHOR),
    )
    instance = load_instance(
        str(Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"),
    )
    view = DateView(frame=cfg.test_generator.as_of_frame())

    sql: str = build_full_seed_sql(cfg, instance, anchor=view.anchor_day)
    days = _extract_balance_days(sql, LOCKED_ANCHOR.year)

    # Sanity: the anchor year is represented in the seed.
    assert days, "expected ≥1 day at the locked anchor's year in the seed"
    # The contract: every day the view *requires* must be findable in the
    # data the seed emits.
    assert view.is_satisfied_by(days), (
        f"locked seed does not satisfy view.required_coverage="
        f"{view.required_coverage}; first emitted anchor-year days: "
        f"{days[:5]}"
    )


def test_required_coverage_gate_fires_on_uncovered_view() -> None:
    # Inverse of the above: a contrived view whose required_coverage
    # lands OUTSIDE the seed's emitted range must fail the gate. Pins
    # the assertion's teeth — without this, a window-narrowing
    # regression could pass the gate silently.
    view = DateView(frame=AsOfFrame.locked(window_days=2))  # narrow range
    # No days at all → can't satisfy.
    assert not view.is_satisfied_by([])
    # Days only in the year 2099 → outside locked-anchor's [2029-12-30, 2030-01-01].
    far_future = [date(2099, 1, 1), date(2099, 1, 2)]
    assert not view.is_satisfied_by(far_future)
