"""Unit tests for the scenario-expectations helper (U.8.a).

Verifies period filtering at endpoints, current-state semantics
(stuck_pending / stuck_unbundled skip the filter), and that real
scenarios from ``default_scenario_for(spec_example)`` flow through
the helper without surprise.

The hand-crafted ScenarioPlant scenarios in these tests pin
expected behaviors of the helper (period inclusion at endpoints,
days_ago=0 "today" plants, etc.) — they're testing the COMPUTATION
function, not the audit-layer contract. The U.8.b agreement tests
will use ``expected_audit_counts`` AS the source of truth instead
of hardcoding their own numbers.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from quicksight_gen.common.l2.auto_scenario import default_scenario_for
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.seed import (
    DriftPlant,
    LimitBreachPlant,
    OverdraftPlant,
    ScenarioPlant,
    StuckPendingPlant,
    StuckUnbundledPlant,
    SupersessionPlant,
)

from tests.audit._scenario_expectations import expected_audit_counts


_SPEC_EXAMPLE = (
    Path(__file__).parent.parent / "l2" / "spec_example.yaml"
)

# Reference date the hand-crafted scenarios anchor on. All
# ``days_ago`` values below subtract from this; the ``period`` arg
# in each test selects which subset of plants should land in-window.
_TODAY = date(2026, 5, 1)


def _make_scenario(**plants) -> ScenarioPlant:  # type: ignore[no-untyped-def]: **plants is the union of all plant kw-tuples
    """Build a minimal ScenarioPlant pinned to ``_TODAY``."""
    return ScenarioPlant(
        template_instances=(),
        today=_TODAY,
        **plants,
    )


def test_drift_plant_inside_period_counts():
    scenario = _make_scenario(
        drift_plants=(
            DriftPlant(
                account_id="acct-1",
                days_ago=3,
                delta_money=Decimal("10"),
                rail_name="r1",
                counter_account_id="ext-1",
            ),
        ),
    )
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.drift_count == 1
    assert expected.drift_account_days == (
        ("acct-1", _TODAY - timedelta(days=3)),
    )


def test_drift_plants_outside_period_excluded():
    scenario = _make_scenario(
        drift_plants=(
            DriftPlant(
                account_id="acct-old",
                days_ago=30,  # too old
                delta_money=Decimal("10"),
                rail_name="r1",
                counter_account_id="ext-1",
            ),
            DriftPlant(
                account_id="acct-today",
                days_ago=0,  # today — outside default audit window
                delta_money=Decimal("10"),
                rail_name="r1",
                counter_account_id="ext-1",
            ),
        ),
    )
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.drift_count == 0


def test_period_endpoints_inclusive_both_sides():
    """Plants on the start AND end edge of the period both count."""
    scenario = _make_scenario(
        drift_plants=(
            DriftPlant(
                account_id="acct-start",
                days_ago=7,  # equals period start
                delta_money=Decimal("10"),
                rail_name="r1",
                counter_account_id="ext-1",
            ),
            DriftPlant(
                account_id="acct-end",
                days_ago=1,  # equals period end
                delta_money=Decimal("10"),
                rail_name="r1",
                counter_account_id="ext-1",
            ),
        ),
    )
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.drift_count == 2
    assert {a for a, _ in expected.drift_account_days} == {
        "acct-start", "acct-end",
    }


def test_overdraft_period_filtering():
    scenario = _make_scenario(
        overdraft_plants=(
            OverdraftPlant(
                account_id="o1",
                days_ago=2,
                money=Decimal("-50"),
            ),
            OverdraftPlant(
                account_id="o2",
                days_ago=20,  # too old
                money=Decimal("-50"),
            ),
        ),
    )
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.overdraft_count == 1
    assert expected.overdraft_account_days == (
        ("o1", _TODAY - timedelta(days=2)),
    )


def test_limit_breach_carries_transfer_type_in_identity():
    scenario = _make_scenario(
        limit_breach_plants=(
            LimitBreachPlant(
                account_id="b1",
                days_ago=4,
                rail_name="ACH",
                amount=Decimal("999"),
                counter_account_id="ext-1",
            ),
        ),
    )
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.limit_breach_count == 1
    assert expected.limit_breach_account_days == (
        ("b1", _TODAY - timedelta(days=4), "ACH"),
    )


def test_stuck_pending_skips_period_filter():
    """Current-state matview: count is len(plants) regardless of dates."""
    scenario = _make_scenario(
        stuck_pending_plants=(
            StuckPendingPlant(
                account_id="a",
                days_ago=100,  # far older than any plausible period
                rail_name="t",
                amount=Decimal("1"),
            ),
            StuckPendingPlant(
                account_id="b",
                days_ago=0,
                rail_name="t",
                amount=Decimal("1"),
            ),
        ),
    )
    # Narrow period that excludes both plant dates by date arithmetic;
    # current-state semantics ignore it.
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.stuck_pending_count == 2
    assert expected.stuck_pending_accounts == ("a", "b")


def test_stuck_unbundled_skips_period_filter():
    scenario = _make_scenario(
        stuck_unbundled_plants=(
            StuckUnbundledPlant(
                account_id="u1",
                days_ago=100,
                rail_name="t",
                amount=Decimal("1"),
            ),
        ),
    )
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.stuck_unbundled_count == 1
    assert expected.stuck_unbundled_accounts == ("u1",)


def test_supersession_in_period_counts_as_correcting_tx():
    scenario = _make_scenario(
        supersession_plants=(
            SupersessionPlant(
                account_id="s1",
                days_ago=2,
                rail_name="t",
                original_amount=Decimal("100"),
                corrected_amount=Decimal("110"),
            ),
            SupersessionPlant(
                account_id="s2",
                days_ago=50,  # outside period
                rail_name="t",
                original_amount=Decimal("100"),
                corrected_amount=Decimal("110"),
            ),
        ),
    )
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(scenario, period)
    assert expected.supersession_count == 1
    assert expected.supersession_account_days == (
        ("s1", _TODAY - timedelta(days=2)),
    )


def test_empty_scenario_returns_all_zeros():
    expected = expected_audit_counts(
        _make_scenario(),
        (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1)),
    )
    assert expected.drift_count == 0
    assert expected.overdraft_count == 0
    assert expected.limit_breach_count == 0
    assert expected.stuck_pending_count == 0
    assert expected.stuck_unbundled_count == 0
    assert expected.supersession_count == 0


def test_default_spec_example_scenario_smokes():
    """Real scenario shape flows through the helper without exception.

    Counts are re-derived from the scenario tuples (NOT compared to
    hardcoded numbers) — this asserts the helper's contract holds on
    real data, not a magic-number check that would rot if the auto-
    scenario picker changed its heuristics.
    """
    instance = load_instance(_SPEC_EXAMPLE)
    report = default_scenario_for(instance, today=_TODAY)
    period = (_TODAY - timedelta(days=7), _TODAY - timedelta(days=1))
    expected = expected_audit_counts(report.scenario, period)

    # Re-derive the period filter independently, then check the
    # helper agrees. This is essentially a pinning test for the
    # helper's logic against the live scenario data.
    in_period_drift = sum(
        1 for p in report.scenario.drift_plants
        if period[0] <= report.scenario.today
            - timedelta(days=p.days_ago) <= period[1]
    )
    assert expected.drift_count == in_period_drift
    assert expected.stuck_pending_count == len(
        report.scenario.stuck_pending_plants,
    )
    assert expected.stuck_unbundled_count == len(
        report.scenario.stuck_unbundled_plants,
    )
