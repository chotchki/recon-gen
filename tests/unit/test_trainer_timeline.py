"""``compute_plant_timeline`` unit tests (X.4.h.6.a).

Locks the contract for the trainer-mode timeline projection:

- Walk the auto-scenario for an L2 + TestGeneratorConfig, project
  each plant onto ``today - timedelta(days=plant.days_ago)``.
- Days with zero plants are omitted (sparse timeline).
- ``scope == "uncovered_rails"`` → empty timeline (deploy emits no
  plants in that mode).
- ``tg.plants`` filter narrows the projection to the requested
  subset (same chain the deploy pipeline uses).
- ``tg.end_date`` controls the window anchor.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quicksight_gen.common.config import TestGeneratorConfig
from quicksight_gen.common.l2 import Identifier
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.seed import (
    DriftPlant,
    OverdraftPlant,
    ScenarioPlant,
    StuckPendingPlant,
    SupersessionPlant,
)
from quicksight_gen.common.l2.trainer_timeline import (
    PlantHit,
    TimelineDay,
    _scenario_to_timeline,
    compute_plant_timeline,
    hits_by_kind,
)


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def spec_example() -> object:
    return load_instance(_FIXTURES / "spec_example.yaml")


# ---------------------------------------------------------------------------
# _scenario_to_timeline — pure projection over a hand-crafted scenario
# ---------------------------------------------------------------------------


def test_empty_scenario_yields_empty_timeline() -> None:
    scenario = ScenarioPlant(template_instances=(), today=date(2026, 5, 14))
    assert _scenario_to_timeline(scenario) == []


def test_drift_plant_projects_to_correct_date() -> None:
    scenario = ScenarioPlant(
        template_instances=(),
        today=date(2026, 5, 14),
        drift_plants=(
            DriftPlant(
                account_id="cust-1",
                days_ago=5,
                delta_money=Decimal("75.00"),
                rail_name="rail-A",
                counter_account_id="ext-1",
            ),
        ),
    )
    timeline = _scenario_to_timeline(scenario)
    assert len(timeline) == 1
    day = timeline[0]
    assert day.day == date(2026, 5, 9)  # 14 - 5
    assert len(day.hits) == 1
    hit = day.hits[0]
    assert hit.kind == "drift"
    assert hit.account_id == "cust-1"
    assert hit.rail_name == "rail-A"
    assert hit.amount == Decimal("75.00")


def test_overdraft_plant_carries_no_rail() -> None:
    """Overdraft is an account-balance state, not a rail-bound transfer
    — its PlantHit.rail_name must be None to reflect that."""
    scenario = ScenarioPlant(
        template_instances=(),
        today=date(2026, 5, 14),
        overdraft_plants=(
            OverdraftPlant(
                account_id="cust-2", days_ago=6,
                money=Decimal("-1500.00"),
            ),
        ),
    )
    timeline = _scenario_to_timeline(scenario)
    assert timeline[0].hits[0].rail_name is None
    assert timeline[0].hits[0].amount == Decimal("-1500.00")


def test_supersession_uses_corrected_amount() -> None:
    """Supersession has both original_amount + corrected_amount; the
    timeline shows the *corrected* one (the visible-state amount)."""
    scenario = ScenarioPlant(
        template_instances=(),
        today=date(2026, 5, 14),
        supersession_plants=(
            SupersessionPlant(
                account_id="cust-1", days_ago=3,
                rail_name=Identifier("ExternalRailInbound"),
                original_amount=Decimal("250.00"),
                corrected_amount=Decimal("275.00"),
            ),
        ),
    )
    hit = _scenario_to_timeline(scenario)[0].hits[0]
    assert hit.kind == "supersession"
    assert hit.amount == Decimal("275.00")


def test_multiple_plants_same_day_collect_into_one_entry() -> None:
    """Two drift plants planted at days_ago=5 land on the same day —
    the timeline carries one TimelineDay with two hits."""
    scenario = ScenarioPlant(
        template_instances=(),
        today=date(2026, 5, 14),
        drift_plants=(
            DriftPlant(
                account_id="cust-1", days_ago=5,
                delta_money=Decimal("75"), rail_name="r1",
                counter_account_id="ext-1",
            ),
            DriftPlant(
                account_id="cust-2", days_ago=5,
                delta_money=Decimal("100"), rail_name="r1",
                counter_account_id="ext-1",
            ),
        ),
    )
    timeline = _scenario_to_timeline(scenario)
    assert len(timeline) == 1
    assert len(timeline[0].hits) == 2


def test_timeline_sorted_oldest_to_newest() -> None:
    """Days emit in chronological order so the column reads top-down
    as time-forward."""
    scenario = ScenarioPlant(
        template_instances=(),
        today=date(2026, 5, 14),
        drift_plants=(
            DriftPlant(
                account_id="c", days_ago=2,
                delta_money=Decimal("1"), rail_name="r",
                counter_account_id="x",
            ),
            DriftPlant(
                account_id="c", days_ago=7,
                delta_money=Decimal("1"), rail_name="r",
                counter_account_id="x",
            ),
            DriftPlant(
                account_id="c", days_ago=4,
                delta_money=Decimal("1"), rail_name="r",
                counter_account_id="x",
            ),
        ),
    )
    days = [td.day for td in _scenario_to_timeline(scenario)]
    assert days == [date(2026, 5, 7), date(2026, 5, 10), date(2026, 5, 12)]


def test_zero_day_plants_omitted_from_timeline() -> None:
    """Sparse: only days with at least one plant appear. days_ago
    values not represented in any plant tuple don't get an empty
    TimelineDay row."""
    scenario = ScenarioPlant(
        template_instances=(),
        today=date(2026, 5, 14),
        drift_plants=(
            DriftPlant(
                account_id="c", days_ago=5,
                delta_money=Decimal("1"), rail_name="r",
                counter_account_id="x",
            ),
        ),
        stuck_pending_plants=(
            StuckPendingPlant(
                account_id="c", days_ago=10,
                rail_name=Identifier("ExternalRailInbound"),
                amount=Decimal("100"),
            ),
        ),
    )
    days = [td.day for td in _scenario_to_timeline(scenario)]
    # Two distinct days only — 5/9 and 5/4 — not the 10 in between.
    assert days == [date(2026, 5, 4), date(2026, 5, 9)]
    assert len(days) == 2


# ---------------------------------------------------------------------------
# compute_plant_timeline — integration with auto_scenario + tg knobs
# ---------------------------------------------------------------------------


def test_compute_against_spec_example_full_scope(spec_example: object) -> None:
    """Default tg (scope=full, all plants) against the bundled L2 fixture
    yields a non-empty timeline. spec_example carries enough rails to
    auto-derive each L1 plant kind."""
    tg = TestGeneratorConfig(
        end_date=date(2026, 5, 14),
        scope="full",
    )
    timeline = compute_plant_timeline(spec_example, tg)  # type: ignore[arg-type]: load_instance return type narrowed by L2Instance shape; fixture cast to object for pytest collection ergonomics
    assert len(timeline) > 0
    # Every hit's date is within 30 days of the anchor (plant cap_days
    # for stuck_unbundled can extend the back-of-window).
    for td in timeline:
        diff = (date(2026, 5, 14) - td.day).days
        assert 0 <= diff <= 60, f"plant on {td.day} is outside reasonable window"


def test_uncovered_rails_scope_yields_empty_timeline(
    spec_example: object,
) -> None:
    """In uncovered_rails mode the deploy pipeline emits no plants —
    just baseline fill — so the timeline is empty."""
    tg = TestGeneratorConfig(
        end_date=date(2026, 5, 14),
        scope="uncovered_rails",
    )
    assert compute_plant_timeline(spec_example, tg) == []  # type: ignore[arg-type]: same L2Instance fixture cast as above


def test_exceptions_only_scope_yields_same_plants_as_full(
    spec_example: object,
) -> None:
    """``exceptions_only`` and ``full`` both emit the same plant set
    (full also emits baseline; exceptions_only doesn't). Timeline is
    plants-only, so the two scopes produce identical timelines."""
    tg_full = TestGeneratorConfig(
        end_date=date(2026, 5, 14), scope="full",
    )
    tg_exc = TestGeneratorConfig(
        end_date=date(2026, 5, 14), scope="exceptions_only",
    )
    assert compute_plant_timeline(spec_example, tg_full) == compute_plant_timeline(spec_example, tg_exc)  # type: ignore[arg-type]: same L2Instance fixture cast as above


def test_plants_filter_narrows_timeline(spec_example: object) -> None:
    """When tg.plants restricts to a subset, the timeline only shows
    those kinds."""
    tg = TestGeneratorConfig(
        end_date=date(2026, 5, 14),
        scope="full",
        plants=("drift",),
    )
    timeline = compute_plant_timeline(spec_example, tg)  # type: ignore[arg-type]: same L2Instance fixture cast as above
    kinds = {hit.kind for td in timeline for hit in td.hits}
    assert kinds == {"drift"}


def test_end_date_anchors_window(spec_example: object) -> None:
    """A different end_date shifts every plant date by the same delta."""
    tg_a = TestGeneratorConfig(end_date=date(2026, 5, 14), scope="full")
    tg_b = TestGeneratorConfig(end_date=date(2026, 6, 14), scope="full")
    days_a = sorted(td.day for td in compute_plant_timeline(spec_example, tg_a))  # type: ignore[arg-type]: same L2Instance fixture cast as above
    days_b = sorted(td.day for td in compute_plant_timeline(spec_example, tg_b))  # type: ignore[arg-type]: same L2Instance fixture cast as above
    # Same shape — same number of distinct plant-days.
    assert len(days_a) == len(days_b)
    # b is a-shifted by exactly 31 days for every entry.
    for a, b in zip(days_a, days_b, strict=True):
        assert (b - a).days == 31


# ---------------------------------------------------------------------------
# hits_by_kind — header summary helper
# ---------------------------------------------------------------------------


def test_hits_by_kind_counts_across_days() -> None:
    timeline = [
        TimelineDay(
            day=date(2026, 5, 9),
            hits=(
                PlantHit("drift", "c", "r", Decimal("1")),
                PlantHit("drift", "c", "r", Decimal("2")),
            ),
        ),
        TimelineDay(
            day=date(2026, 5, 10),
            hits=(
                PlantHit("drift", "c", "r", Decimal("3")),
                PlantHit("overdraft", "c", None, Decimal("-100")),
            ),
        ),
    ]
    counts = hits_by_kind(timeline)
    assert counts == {"drift": 3, "overdraft": 1}


def test_hits_by_kind_empty_returns_empty_mapping() -> None:
    assert hits_by_kind([]) == {}
