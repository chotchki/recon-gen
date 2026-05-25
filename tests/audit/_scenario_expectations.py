"""Derive expected audit cell values from a ScenarioPlant (U.8.a) +
spine generators (AT.5.f).

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

AT.5.f adds the L2 sibling ``ExpectedL2AuditCounts`` /
``expected_l2_audit_counts``. The L2 plant primitives live on the
spine (``AnomalyGenerator`` / ``MoneyTrailGenerator``) rather than on
``ScenarioPlant`` — the spine's `intended` and dataclass fields
already encode the lower bound the dashboard / matview should
surface, so the same "the planter IS the source of truth" pattern
extends without growing ScenarioPlant. The L2 3-way agreement test
(``tests/e2e/test_inv_dashboard_agreement.py``) consumes these to
replace hardcoded ``_MONEY_TRAIL_CHAIN_LENGTH`` / spike-count
constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from recon_gen.common.intervals import DateInterval
from recon_gen.common.l2.seed import ScenarioPlant
from recon_gen.common.spine import AnomalyGenerator, MoneyTrailGenerator


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
    period: DateInterval,
) -> ExpectedAuditCounts:
    """Compute ExpectedAuditCounts from a scenario + audit period.

    ``scenario`` is the ``ScenarioPlant`` returned by
    ``default_scenario_for(instance, today=...)`` (or hand-built
    for unit tests). ``period`` is the ``DateInterval`` window the
    audit is rendered for; both endpoints are inclusive dates
    (BC.4d — was ``tuple[date, date]``).
    """

    def _eff(p) -> date:  # type: ignore[no-untyped-def]: p is one of the union of plant dataclasses; all carry days_ago
        return scenario.today - timedelta(days=p.days_ago)

    def _in_period(p) -> bool:  # type: ignore[no-untyped-def]: p is one of the union of plant dataclasses; all carry days_ago
        return period.contains(_eff(p))

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


# =====================================================================
# L2 lower bounds (AT.5.f) — anomaly + money_trail
# =====================================================================
#
# The L2 spine generators (AnomalyGenerator + MoneyTrailGenerator)
# carry their plant intent directly: the dataclass fields encode the
# rows the matview should surface. So the same "the planter IS the
# source of truth" pattern that powers ``expected_audit_counts``
# extends without forcing the L2 plants through ``ScenarioPlant``.
#
# Lower-bound semantics:
#
# - **anomaly** — at least 1 row. The AnomalyGenerator plants one
#   spike pair on ``anchor_day``; the spike's
#   ``(sender, recipient, window_end)`` natural-key tuple must appear
#   in the matview when filtered by any σ-threshold the spike clears.
#   The rolling-window matview may also emit neighboring window-end
#   rows for the same pair (the 2-day window slides), so direct count
#   can exceed the lower bound. Identity exposure is exact for the
#   spike row, partial for any incidentals — so ``anomaly_keys`` is
#   a singleton.
# - **money_trail** — at least ``chain_length`` rows. Every transfer
#   in the planted chain becomes one edge in the matview
#   (``(transfer_id, depth)``); the matview surfaces all of them, so
#   the lower bound is exact when the dashboard's chain-root dropdown
#   pegs the planted root. Identity exposure is full: every
#   ``(xfer-money-trail-{i}, i)`` for i in [0, chain_length).


@dataclass(frozen=True)
class ExpectedL2AuditCounts:
    """Per-L2-invariant expected row counts derived from the spine
    generators that planted them.

    Counts are LOWER bounds — the matview may surface more rows
    (rolling-window neighbors for anomaly; the L1+broad seed's
    organic chains for money_trail) — but at least these are
    guaranteed by the plant. The 3-way agreement test
    (``test_inv_dashboard_agreement::test_invariant_three_way_agreement``)
    uses these as the producer-side regression anchor: every
    renderer's count must be ≥ the lower bound.

    Key projections expose the natural-key tuples the matview's
    detail surface uses (matches the App2 / QS table group_by). The
    spine's `intended` Violation populates the anomaly singleton;
    money_trail's deterministic transfer-id scheme populates the
    full chain edge set.
    """

    anomaly_count: int
    money_trail_count: int

    # Singleton — the spike row's natural-key tuple. The matview
    # surfaces it with `window_end = anomaly_gen.anchor_day` by
    # construction (the spike posts on anchor_day and the rolling
    # 2-day window's end is the spike's posting day).
    anomaly_keys: tuple[tuple[str, str, date], ...]

    # Full chain — one tuple per edge, depth 0..chain_length-1.
    money_trail_keys: tuple[tuple[str, int], ...]


def expected_l2_audit_counts(
    *,
    anomaly_gen: AnomalyGenerator,
    money_trail_gen: MoneyTrailGenerator,
) -> ExpectedL2AuditCounts:
    """Compute ``ExpectedL2AuditCounts`` from the spine generators.

    Both generators carry deterministic identifiers (the
    sender/recipient account_ids on ``AnomalyGenerator``; the
    ``xfer-money-trail-{i}`` transfer-id scheme on
    ``MoneyTrailGenerator``), so the expected key tuples derive
    purely from the generator dataclass fields — no live DB read,
    no scenario hash dependency. A generator-rename / scheme change
    surfaces here loudly rather than silently shifting the test's
    expected set.
    """
    # Anomaly: the spike row is the (sender, recipient, anchor_day)
    # natural key. The generator's `intended` Violation carries the
    # same identity; we read off the dataclass fields directly to
    # avoid coupling to AnomalyView semantics.
    anomaly_keys = (
        (
            anomaly_gen.sender_account_id,
            anomaly_gen.recipient_account_id,
            anomaly_gen.anchor_day,
        ),
    )

    # Money trail: one edge per chain depth. Transfer ids match
    # ``MoneyTrailGenerator._transfer_id`` — keep this scheme in
    # sync if it ever changes (the lock is the test below + the
    # generator's own scheme; semantic drift trips both at once).
    money_trail_keys = tuple(
        (f"xfer-money-trail-{i}", i)
        for i in range(money_trail_gen.chain_length)
    )

    return ExpectedL2AuditCounts(
        anomaly_count=len(anomaly_keys),
        money_trail_count=len(money_trail_keys),
        anomaly_keys=anomaly_keys,
        money_trail_keys=money_trail_keys,
    )
