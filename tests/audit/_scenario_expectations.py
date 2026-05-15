"""Derive expected audit cell values from a ScenarioPlant (U.8.a).

The audit PDF and the L1 dashboard both render against the same
L1 matviews; U.8.b's release-gate test asserts they agree on what
they show. To avoid hardcoding magic numbers ("expect 3 drift
rows") that rot the moment seed densification or plant heuristics
change, this module turns the SAME plant primitives that planted
the rows into the expected counts. The plant tuples on
``ScenarioPlant`` are the source of truth.

Period semantics mirror the audit's exactly:
  - Time-series invariants (drift / overdraft / limit_breach /
    supersession) filter plants by effective date, where
    ``effective_date = scenario.today - timedelta(days=p.days_ago)``,
    inclusive on both endpoints (matches the audit's
    ``>= start AND < (end + 1 day)`` SQL).
  - Current-state invariants (stuck_pending / stuck_unbundled)
    skip the period filter — plants are designed never to resolve,
    so ``len(plants)`` matches the matview count regardless of
    when they were planted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from quicksight_gen.common.l2.seed import ScenarioPlant


@dataclass(frozen=True)
class ExpectedAuditCounts:
    """Per-invariant expected row counts derived from a ScenarioPlant.

    Each ``X_count`` is what U.8.b's three-way assert expects to
    find in the PDF's invariant table (and on the L1 dashboard's
    matching sheet) for the given scenario + period. The
    row-identity tuples (``X_account_days``, etc.) feed per-row
    asserts; the count is redundant with ``len(...)`` but exposing
    it explicitly keeps call sites readable.

    Supersession's count is the number of correcting transaction
    rows whose effective date lands in period — one per
    ``SupersessionPlant`` (each plant emits one correcting tx
    against the original, per ``SupersessionPlant`` docstring).
    """

    drift_count: int
    overdraft_count: int
    limit_breach_count: int
    stuck_pending_count: int
    stuck_unbundled_count: int
    supersession_count: int

    drift_account_days: tuple[tuple[str, date], ...]
    overdraft_account_days: tuple[tuple[str, date], ...]
    limit_breach_account_days: tuple[tuple[str, date, str], ...]
    stuck_pending_accounts: tuple[str, ...]
    stuck_unbundled_accounts: tuple[str, ...]
    supersession_account_days: tuple[tuple[str, date], ...]


def expected_audit_counts(
    scenario: ScenarioPlant,
    period: tuple[date, date],
) -> ExpectedAuditCounts:
    """Compute ExpectedAuditCounts from a scenario + audit period.

    ``scenario`` is the ``ScenarioPlant`` returned by
    ``default_scenario_for(instance, today=...)`` (or hand-built
    for unit tests). ``period`` is the ``(start, end)`` window the
    audit is rendered for; both endpoints are inclusive dates.
    """
    start, end = period

    def _eff(p) -> date:  # type: ignore[no-untyped-def]: p is one of the union of plant dataclasses; all carry days_ago
        return scenario.today - timedelta(days=p.days_ago)

    def _in_period(p) -> bool:  # type: ignore[no-untyped-def]: p is one of the union of plant dataclasses; all carry days_ago
        eff = _eff(p)
        return start <= eff <= end

    drift = tuple(p for p in scenario.drift_plants if _in_period(p))
    overdraft = tuple(
        p for p in scenario.overdraft_plants if _in_period(p)
    )
    breach = tuple(
        p for p in scenario.limit_breach_plants if _in_period(p)
    )
    supersession = tuple(
        p for p in scenario.supersession_plants if _in_period(p)
    )

    return ExpectedAuditCounts(
        drift_count=len(drift),
        overdraft_count=len(overdraft),
        limit_breach_count=len(breach),
        stuck_pending_count=len(scenario.stuck_pending_plants),
        stuck_unbundled_count=len(scenario.stuck_unbundled_plants),
        supersession_count=len(supersession),
        drift_account_days=tuple((p.account_id, _eff(p)) for p in drift),
        overdraft_account_days=tuple(
            (p.account_id, _eff(p)) for p in overdraft
        ),
        limit_breach_account_days=tuple(
            (p.account_id, _eff(p), p.rail_name) for p in breach
        ),
        stuck_pending_accounts=tuple(
            p.account_id for p in scenario.stuck_pending_plants
        ),
        stuck_unbundled_accounts=tuple(
            p.account_id for p in scenario.stuck_unbundled_plants
        ),
        supersession_account_days=tuple(
            (p.account_id, _eff(p)) for p in supersession
        ),
    )
