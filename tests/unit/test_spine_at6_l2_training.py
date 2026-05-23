"""AT.6 — L2 training/docs scenarios self-validated.

Parallel to AS.7's `test_spine_training.py` but for L2 (anomaly +
money_trail). The same `TrainingScenario` mechanism that catches
prose-vs-data drift for L1 invariants applies to the Investigation
surface: a docs page that claims "the analyst sees a 4σ anomaly between
sender X and recipient Y on day Z" must actually produce that row when
the named generator emits.

Two L2 cases pinned:

  - **Anomaly training scenario** — a baseline + spike pair where the
    docs claim the σ-thresholded matview surfaces the spike. The
    generator's `intended` Violation IS the claim; `self_validate`
    asserts the matview contains it.
  - **Money-trail training scenario** — a 3-deep chain where the docs
    claim the deepest edge is the analyst-meaningful endpoint.
    `MoneyTrailGenerator.intended` returns the leaf edge; the test
    asserts it lands in the recursive matview.

Plus the L2 ALL-set integration: `validate_all(L2_TRAINING_SCENARIOS,
_fresh_db)` runs both end-to-end as a batch — the docs build hook's
shape.

Lying-scenario coverage is already pinned by AS.7's
`test_spine_training.py::test_lying_scenario_fails_loud` (the mechanism
is identical across L1/L2; no need to duplicate the negative case here).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    AnomalyInvariant,
    MoneyTrailInvariant,
    TrainingScenario,
    validate_all,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_ANCHOR = date(2030, 1, 1)


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# AT.6 — L2 training scenarios. Each is a docs-renderable, self-validating
# bundle of (generator, invariant, claimed intended Violation).
# ---------------------------------------------------------------------------


def _anomaly_training_scenario() -> TrainingScenario:
    """The canonical Investigation anomaly demo: a >4σ spike between a
    CustomerSubledger sender and a CustomerSubledger recipient on the
    anchor day surfaces in ``<prefix>_inv_pair_rolling_anomalies``."""
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=50,
        spike_magnitude=100_000.0,
        anchor_day=_ANCHOR,
    )
    return TrainingScenario(
        name="Volume Anomaly — spike against background distribution",
        description=(
            "Plant 50 baseline pairs at $100 each + one spike pair at "
            "$100,000 between sender CustomerSubledger and recipient "
            "CustomerSubledger on the anchor day. The σ-thresholded "
            "Volume Anomalies matview surfaces the spike's "
            "(sender, recipient, window_end) row in the 4+ σ bucket."
        ),
        emitters=(gen,),
        invariants=(AnomalyInvariant(),),
        intended=frozenset({gen.intended}),
    )


def _money_trail_training_scenario() -> TrainingScenario:
    """The canonical Investigation money-trail demo: a 3-hop chain whose
    deepest edge (depth=2) is the analyst-meaningful endpoint. The
    recursive matview ``<prefix>_inv_money_trail_edges`` surfaces every
    edge; `MoneyTrailGenerator.intended` returns the leaf."""
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger",
        chain_length=3,
        anchor_day=_ANCHOR,
    )
    return TrainingScenario(
        name="Money Trail — 3-deep parent-linked chain",
        description=(
            "Plant 3 Posted transfers forming a chain: account[0] → "
            "account[1] → account[2] → account[3]. Each transfer's "
            "parent_transfer_id links to the previous transfer; the "
            "recursive Money Trail matview walks the chain and "
            "surfaces every edge. The Investigation dashboard's Hop-"
            "by-Hop table shows the 3 edges (depths 0/1/2) when the "
            "analyst picks the root from the chain dropdown. The "
            "leaf edge at depth=2 is the analyst-meaningful endpoint."
        ),
        emitters=(gen,),
        invariants=(MoneyTrailInvariant(),),
        intended=frozenset({gen.intended}),
    )


# The registry — docs builds + tests both iterate this list. Adding a
# new L2 training scenario means appending to this tuple; the
# `test_l2_training_scenarios_all_self_validate` test below picks it
# up automatically (drift-resistant).
L2_TRAINING_SCENARIOS: tuple[TrainingScenario, ...] = (
    _anomaly_training_scenario(),
    _money_trail_training_scenario(),
)


# ---------------------------------------------------------------------------
# Pinning tests.
# ---------------------------------------------------------------------------


def test_anomaly_training_scenario_self_validates() -> None:
    """The anomaly demo's docs claim hold: a >4σ spike produces the
    spike's (sender, recipient, window_end) Violation."""
    scenario = _anomaly_training_scenario()
    conn = _fresh_db()
    try:
        scenario.self_validate(conn)  # raises on missing intended
    finally:
        conn.close()


def test_money_trail_training_scenario_self_validates() -> None:
    """The money-trail demo's docs claim hold: a 3-deep chain's
    leaf-edge Violation lands in the recursive matview."""
    scenario = _money_trail_training_scenario()
    conn = _fresh_db()
    try:
        scenario.self_validate(conn)
    finally:
        conn.close()


def test_l2_training_scenarios_all_self_validate() -> None:
    """The docs-build hook's shape: `validate_all(L2_TRAINING_SCENARIOS,
    conn_factory)` runs every registered L2 scenario as a batch, each
    against a fresh DB.

    Adding a new scenario by appending to ``L2_TRAINING_SCENARIOS``
    extends this test automatically — no parallel hand-list to keep
    in sync."""
    validate_all(L2_TRAINING_SCENARIOS, _fresh_db)
