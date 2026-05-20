"""``compute_plant_timeline`` — project planted exceptions onto a per-day
timeline (X.4.h.6.a).

The Studio data-shaping panel renders this as a vertical column —
one row per day in the plant window, annotated with which exception
kinds hit that day. The trainer can scan top-to-bottom to learn how
each plant lands across time, click a day to jump the ``end_date``
knob there, and re-deploy.

Pure projection: walks the same ``ScenarioPlant`` the deploy pipeline
emits (``build_default_scenario`` from ``auto_scenario.py``), reads
``plant.days_ago`` + ``scenario.today``, and bins each plant onto
``today - timedelta(days=days_ago)``. No new generator logic — this
is a read-only view of what the pipeline already plants.

Scope-aware: when ``tg.scope == "uncovered_rails"`` the deploy
pipeline emits NO plants (just baseline fill), so this returns an
empty timeline. ``"full"`` and ``"exceptions_only"`` both emit the
same plant set, so they share the same timeline output.

Severability: pure Python, no DB import, no async. Same posture as
``trainer.py``'s ``plants_per_node``. The Studio route that wraps
this calls it at request time and renders to HTML.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from recon_gen.common.config import PlantKind, TestGeneratorConfig
from recon_gen.common.l2.auto_scenario import (
    default_scenario_for,
    filter_scenario_plants,
)
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.seed import ScenarioPlant


@dataclass(frozen=True, slots=True)
class PlantHit:
    """One planted exception, projected onto its emit date.

    ``kind`` matches one of the 6 ``PlantKind`` values the trainer
    panel's plant-toggle filters on. ``rail_name`` is None for
    overdraft (no rail involved — overdraft is an account-balance
    state, not a rail-bound transfer). ``amount`` is the canonical
    money magnitude — for supersession, the corrected_amount.
    """

    kind: PlantKind
    account_id: str
    rail_name: str | None
    amount: Decimal | None


@dataclass(frozen=True, slots=True)
class TimelineDay:
    """A row in the rendered timeline — date + the plants that hit it."""

    day: date
    hits: tuple[PlantHit, ...]


def compute_plant_timeline(
    instance: L2Instance,
    tg: TestGeneratorConfig,
) -> list[TimelineDay]:
    """Walk the auto-scenario for ``instance`` + ``tg`` and return one
    ``TimelineDay`` per distinct plant date, sorted oldest → newest.

    Days with zero plants are omitted — the operator scans the timeline
    for "what landed when", not "every calendar day". The window is
    determined by the plant set itself (typically 7 days back from
    ``tg.end_date``).

    When ``tg.scope == "uncovered_rails"`` the deploy pipeline emits
    NO plants (only baseline fill), so this returns an empty list.
    The trainer's UI can then render a "no plants in this scope" hint.

    Threads ``tg.plants`` (None or empty = all kinds; non-empty =
    subset filter) through the same ``filter_scenario_plants`` chain
    the deploy pipeline uses, so the timeline reflects exactly what
    the next ``Deploy changes`` will land.
    """
    if tg.scope == "uncovered_rails":
        return []

    report = default_scenario_for(instance, today=tg.end_date)
    scenario = filter_scenario_plants(report.scenario, tg.plants or None)
    return _scenario_to_timeline(scenario)


def _scenario_to_timeline(scenario: ScenarioPlant) -> list[TimelineDay]:
    """Project every plant on ``scenario`` onto its emit date.

    Split out for unit tests that build a hand-crafted scenario
    (without re-running ``default_scenario_for``).
    """
    today = scenario.today
    by_date: dict[date, list[PlantHit]] = {}

    def _add(d: date, hit: PlantHit) -> None:
        by_date.setdefault(d, []).append(hit)

    for p in scenario.drift_plants:
        _add(today - timedelta(days=p.days_ago), PlantHit(
            kind="drift",
            account_id=str(p.account_id),
            rail_name=str(p.rail_name),
            amount=p.delta_money,
        ))
    for p in scenario.overdraft_plants:
        _add(today - timedelta(days=p.days_ago), PlantHit(
            kind="overdraft",
            account_id=str(p.account_id),
            rail_name=None,
            amount=p.money,
        ))
    for p in scenario.limit_breach_plants:
        _add(today - timedelta(days=p.days_ago), PlantHit(
            kind="limit_breach",
            account_id=str(p.account_id),
            rail_name=str(p.rail_name),
            amount=p.amount,
        ))
    for p in scenario.stuck_pending_plants:
        _add(today - timedelta(days=p.days_ago), PlantHit(
            kind="stuck_pending",
            account_id=str(p.account_id),
            rail_name=str(p.rail_name),
            amount=p.amount,
        ))
    for p in scenario.stuck_unbundled_plants:
        _add(today - timedelta(days=p.days_ago), PlantHit(
            kind="stuck_unbundled",
            account_id=str(p.account_id),
            rail_name=str(p.rail_name),
            amount=p.amount,
        ))
    for p in scenario.supersession_plants:
        _add(today - timedelta(days=p.days_ago), PlantHit(
            kind="supersession",
            account_id=str(p.account_id),
            rail_name=str(p.rail_name),
            amount=p.corrected_amount,
        ))

    # AG.5 note: the timeline is intentionally scoped to the operator-
    # TOGGLEABLE plant kinds (``config.PlantKind`` — the 6 above). It's
    # the "how does my plant-toggle selection land across time" view, NOT
    # an all-planted projection — ``compute_plant_timeline`` applies
    # ``filter_scenario_plants`` (which gates exactly these 6) upstream.
    # The AB.1-AB.6 + failed/transfer_template/inv_fanout kinds surface
    # on the per-node BADGES (``trainer.plants_per_node``, fixed in AG.5),
    # which is where Gap E's "incomplete badges" complaint actually lives.
    # Adding them here would require making them toggleable
    # (``config.PlantKind`` expansion + gating) — a separate operator-
    # facing change, deferred.

    return [
        TimelineDay(day=d, hits=tuple(by_date[d]))
        for d in sorted(by_date)
    ]


def hits_by_kind(
    timeline: Sequence[TimelineDay],
) -> Mapping[PlantKind, int]:
    """Aggregate count per ``PlantKind`` across the whole timeline.

    Helper for the timeline header — surfaces "12 drift, 2 overdraft,
    1 supersession" so the operator gets a one-line summary before
    scrolling the per-day rows. Returns kinds with zero count omitted.
    """
    counts: dict[PlantKind, int] = {}
    for day in timeline:
        for hit in day.hits:
            counts[hit.kind] = counts.get(hit.kind, 0) + 1
    return counts
