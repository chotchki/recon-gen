"""AT.5.b + AT.5.c — Investigation dashboard ⋈ direct-matview agreement.

The 2-renderer leg of AT.5's L2 4-way gate. Extends AT.5.a's
spine⋈matview contract (`tests/e2e/test_spine_live_agreement.py`) onto
both Investigation dashboard renderers:

    spine_plants  ⊆  direct_matview_SELECT(filtered)  ==  App2  ==  QS

For each L2 invariant the dashboard filters the matview at the dataset
SQL level via a parameter pushdown (σ slider for anomaly, chain-root
dropdown for money_trail; see ``apps/investigation/datasets.py``). The
test pegs the driver to a known parameter value, reads the resulting
table, and compares against a direct SELECT against the matview
filtered by the *same* parameter — apples-to-apples agreement, not
"unfiltered matview vs. filtered dashboard" (which would only agree
against the distribution chart, not the detail tables).

L2 plants land via the spine generators (``AnomalyGenerator`` /
``MoneyTrailGenerator``) — same shape AT.5.a uses, additionally
walking the dashboard renderers. The plants compose on top of the
``l1_plus_broad`` seed ``apply_db_seed`` runs first (matches what
``recon-gen data apply --execute`` produces in a real deploy);
matviews are re-refreshed after the L2 plants land so the dashboard's
SQL sees them.

Parametrized over both renderers via the conftest's
``inv_dashboard_driver`` fixture (X.2.u). Per-leg degradation: if the
QS dashboard isn't deployed for this cell, the QS parametrization
``pytest.skip``s cleanly; the App2 leg still runs.

PDF leg lands in AT.5.d (spike first — Investigation sections may or
may not exist in the audit PDF today). AT.5.e composes the legs into
a parametrized 4-way test mirroring `test_audit_dashboard_agreement`'s
shape.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import RECON_GEN_E2E


# Module-level cfg load can't fire under the unit-only CI job — match
# the rest of the e2e suite's RECON_GEN_E2E gate at import time so the
# unit job doesn't crash on collection.
if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "Investigation dashboard agreement test requires RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports keep collection cheap on the unit job
from recon_gen.common.l2 import (  # noqa: E402
    L2Instance,
    load_instance,
    refresh_matviews_sql,
)
from recon_gen.common.spine import (  # noqa: E402
    AnomalyInvariant,
    MoneyTrailInvariant,
)
from tests.audit._inv_dashboard_extract import (  # noqa: E402
    anomaly_row_keys,
    count_anomaly_rows,
    count_money_trail_rows,
    key_columns_for,
    money_trail_row_keys,
    rows_seen_anomaly,
    rows_seen_money_trail,
)
from tests.audit._matview_extract import (  # noqa: E402
    anomaly_matview_row_keys,
    count_anomaly_matview_rows,
    count_money_trail_matview_rows,
    distinct_money_trail_roots,
    money_trail_matview_row_keys,
)


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


# Bundled persona-neutral L2 — the same yaml every other e2e test
# defaults to when no per-cell override is set.
_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE_BUNDLED = _FIXTURES / "spec_example.yaml"

_INSTANCE: L2Instance = load_instance(_SPEC_EXAMPLE_BUNDLED)


# Anchor on real today so the broad seed's stuck_* plants land in the
# matview's past-relative-to-NOW window (matches
# test_audit_dashboard_agreement.py's rationale). The σ-threshold +
# chain-root agreement asserts here don't depend on absolute calendar
# date — only on the relative day shape the spine generator + matview
# share — so this stays deterministic across runs.
_TODAY = date.today()  # typing-smell: ignore[test-module-nondeterminism]: stuck_* matviews compute age via CURRENT_TIMESTAMP — anchor must be relative to NOW


# The σ slider's analysis-level default + dataset-parameter default. If
# the dashboard's default drifts (``apps/investigation/app.py`` +
# ``apps/investigation/datasets.py``) and this constant doesn't follow,
# the agreement test catches it loudly: the driver reads at this σ and
# the direct SELECT filter is at the same σ but the dashboard would
# have rendered a different shape.
_DEFAULT_SIGMA = 2.0


# A high-magnitude anomaly plant — 1000× the baseline amount with 100
# background pairs feeding the population stddev. AT.0's finding: a
# spike against a too-thin population shifts the mean toward itself
# enough that even huge multipliers don't clear 3σ. 100 baseline pairs
# at $100 each + a $100,000 spike gives a clear >10σ separation.
_ANOMALY_BASELINE_PAIRS = 100
_ANOMALY_BASELINE_AMOUNT = 100.0
_ANOMALY_SPIKE_MAGNITUDE = 100_000.0

# A 3-deep money-trail chain — enough to exercise the recursive walk
# (depths 0/1/2) without growing the test's data footprint.
_MONEY_TRAIL_CHAIN_LENGTH = 3
_MONEY_TRAIL_AMOUNT = 100.0

# Deterministic root of the planted chain — the MoneyTrailGenerator's
# transfer-id scheme uses ``xfer-money-trail-{index}`` (see
# ``common/spine/money_trail.py::MoneyTrailGenerator._transfer_id``).
_PLANTED_CHAIN_ROOT = "xfer-money-trail-0"


@pytest.fixture(scope="module")
def seeded_l2_db(cfg):  # type: ignore[no-untyped-def]: returns nothing the test introspects — the fixture's contract is "DB is seeded"
    """Apply the schema + broad seed + L2 spine plants + matview refresh.

    Two-phase seed: ``apply_db_seed`` lays the
    schema+L1-plants+initial refresh (the shape a real
    ``recon-gen data apply --execute`` produces); the spine generators
    then plant L2 violations on top, and a second matview refresh
    picks them up.

    Module-scoped — seeding is the expensive setup and the L2 anomaly +
    money_trail asserts both read the same matview state.
    """
    from tests.e2e._seed_helpers import apply_db_seed

    conn = connect_demo_db(cfg)
    try:
        apply_db_seed(
            conn, _INSTANCE,
            prefix=cfg.db_table_prefix,
            mode="l1_plus_broad",
            today=_TODAY,
            dialect=cfg.dialect,
            include_baseline=False,
        )

        # AT.5.b — explicit L2 plants via the spine generators. Same
        # shape AT.4's semantic-lock tests use; driven at a live
        # deployed DB rather than an in-memory SQLite so the renderer
        # layer has something to show. ``prefix`` swap lets the
        # generator write to whatever the cfg declares (matches AS.4's
        # LedgerSimulation prefix wiring); the dialect-aware insert
        # helpers (AT.5.b refactor of ``_emit_helpers.insert_tx``)
        # make this work against PG / Oracle as well as SQLite.
        anchor = _TODAY - timedelta(days=2)
        anomaly_gen = AnomalyInvariant().scenario_for(
            "CustomerSubledger", "CustomerSubledger",
            baseline_pair_count=_ANOMALY_BASELINE_PAIRS,
            baseline_amount=_ANOMALY_BASELINE_AMOUNT,
            spike_magnitude=_ANOMALY_SPIKE_MAGNITUDE,
            anchor_day=anchor,
            instance=_INSTANCE,
        )
        anomaly_gen.prefix = cfg.db_table_prefix
        money_trail_gen = MoneyTrailInvariant().scenario_for(
            "CustomerSubledger",
            chain_length=_MONEY_TRAIL_CHAIN_LENGTH,
            amount=_MONEY_TRAIL_AMOUNT,
            anchor_day=anchor,
            instance=_INSTANCE,
        )
        money_trail_gen.prefix = cfg.db_table_prefix
        anomaly_gen.emit(conn)
        money_trail_gen.emit(conn)
        conn.commit()

        # Refresh again so the matviews see the L2 plants (apply_db_seed
        # ran a refresh before the spine plants landed).
        refresh_sql = refresh_matviews_sql(
            _INSTANCE,
            prefix=cfg.db_table_prefix,
            dialect=cfg.dialect,
        )
        with conn.cursor() as cur:
            execute_script(cur, refresh_sql, dialect=cfg.dialect)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_conn(cfg):  # type: ignore[no-untyped-def]: live PG/Oracle/SQLite connection — concrete type varies per dialect, no shared protocol
    """Function-scoped raw DB connection for the direct-SELECT anchor.

    Cheap (a handful of count + key-set queries per test); the
    ``seeded_l2_db`` fixture's conn is closed by the time the asserts
    run.
    """
    conn = connect_demo_db(cfg)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Anomaly — dashboard ⋈ σ-filtered direct matview SELECT
# ---------------------------------------------------------------------------


def test_anomaly_dashboard_agrees_with_direct_matview(
    seeded_l2_db, inv_dashboard_driver, db_conn, cfg,
) -> None:
    """The "Flagged Pair-Windows — Ranked" table shows the same set of
    ``(sender, recipient, window_end)`` rows as a direct
    ``SELECT FROM <prefix>_inv_pair_rolling_anomalies WHERE z_score >=
    <sigma>``, when the σ slider is at ``<sigma>``.

    Parametrized over ``[qs, app2]`` via the conftest's
    ``inv_dashboard_driver`` fixture — both renderers must agree with
    the matview, not just with each other. The QS leg skips cleanly
    when the dashboard isn't deployed (a SQLite cell, or a fresh AWS
    account with no Investigation deploy).

    The spike planted in ``seeded_l2_db`` is well above 4σ (100_000
    spike vs 100 baseline pairs × $100 → ~10σ separation after stddev
    compression), so the σ=2 default unambiguously shows at least one
    row regardless of the background data shape.
    """
    _ = seeded_l2_db
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg)

    # Direct SELECT ground truth — every (sender, recipient, window_end)
    # whose z_score >= the σ slider value the dashboard read below uses.
    direct_count = count_anomaly_matview_rows(
        db_conn, cfg.db_table_prefix, sigma_threshold=_DEFAULT_SIGMA,
    )
    direct_keys = anomaly_matview_row_keys(
        db_conn, cfg.db_table_prefix, sigma_threshold=_DEFAULT_SIGMA,
    )

    # The spike must reach the matview — a producer-side regression
    # below this asserts the plant→matview path is intact before the
    # renderer-side equality assert exposes any renderer issue.
    assert direct_count >= 1, (
        f"Producer-side regression (anomaly): the spike planted at "
        f"z>>4σ should be in the matview, but the σ>={_DEFAULT_SIGMA} "
        f"SELECT against {cfg.db_table_prefix}_inv_pair_rolling_anomalies "
        f"returned 0 rows. Plant→matview path broken or matview not "
        f"refreshed after the L2 plants."
    )

    # Dashboard read at the same σ.
    dashboard_count = count_anomaly_rows(driver, _DEFAULT_SIGMA)
    assert dashboard_count == direct_count, (
        f"Renderer disagrees with the matview (anomaly): the "
        f"dashboard's Flagged Pair-Windows table shows {dashboard_count} "
        f"rows at σ>={_DEFAULT_SIGMA}, a direct SELECT against "
        f"{cfg.db_table_prefix}_inv_pair_rolling_anomalies WHERE "
        f"z_score >= {_DEFAULT_SIGMA} shows {direct_count}. Same "
        f"matview, same filter."
    )

    # Row identity — DOM-window guard first so a truncated read fails
    # loudly rather than passing a partial comparison.
    rows_seen = rows_seen_anomaly(driver, _DEFAULT_SIGMA)
    assert rows_seen == direct_count, (
        f"Table window truncated (anomaly): {rows_seen} of "
        f"{direct_count} rows visible — the row-identity comparison "
        f"would be partial. (A denser seed needs a read-all path.)"
    )

    dashboard_keys = anomaly_row_keys(driver, _DEFAULT_SIGMA)
    key_cols = key_columns_for("anomaly")
    assert direct_keys == dashboard_keys, (
        f"Dashboard disagrees with the matview on which anomaly rows "
        f"({key_cols}):\n"
        f"  matview-only: {sorted(direct_keys - dashboard_keys)[:5]}\n"
        f"  dashboard-only: {sorted(dashboard_keys - direct_keys)[:5]}\n"
        f"  matview count: {len(direct_keys)}, dashboard count: "
        f"{len(dashboard_keys)}"
    )


# ---------------------------------------------------------------------------
# Money trail — dashboard ⋈ root-filtered direct matview SELECT
# ---------------------------------------------------------------------------


def test_money_trail_dashboard_agrees_with_direct_matview(
    seeded_l2_db, inv_dashboard_driver, db_conn, cfg,
) -> None:
    """The "Money Trail — Hop-by-Hop" table shows the same set of
    ``(transfer_id, depth)`` rows as a direct
    ``SELECT FROM <prefix>_inv_money_trail_edges WHERE root_transfer_id
    = <root>``, when the chain-root dropdown is set to ``<root>``.

    Parametrized over ``[qs, app2]`` — same per-leg degradation policy
    as the anomaly sibling.

    The planted chain's root_transfer_id is deterministic from the
    ``MoneyTrailGenerator`` account-id scheme (`xfer-money-trail-0`);
    we read the matview's distinct roots to verify it's there rather
    than trusting the scheme silently, so a generator-rename doesn't
    break the test invisibly.
    """
    _ = seeded_l2_db
    driver, dashboard_arg = inv_dashboard_driver
    driver.open(dashboard_arg)

    roots = distinct_money_trail_roots(db_conn, cfg.db_table_prefix)
    assert _PLANTED_CHAIN_ROOT in roots, (
        f"Producer-side regression (money_trail): the planted chain's "
        f"root ({_PLANTED_CHAIN_ROOT!r}) is missing from "
        f"{cfg.db_table_prefix}_inv_money_trail_edges. Found roots: "
        f"{sorted(roots)[:10]} (+ {max(len(roots) - 10, 0)} more). "
        f"Plant→matview path broken, matview not refreshed after the "
        f"L2 plants, or the generator's transfer-id naming changed."
    )

    direct_count = count_money_trail_matview_rows(
        db_conn, cfg.db_table_prefix, root_transfer_id=_PLANTED_CHAIN_ROOT,
    )
    direct_keys = money_trail_matview_row_keys(
        db_conn, cfg.db_table_prefix, root_transfer_id=_PLANTED_CHAIN_ROOT,
    )
    # Sanity — the 3-deep chain plants 3 edges (depths 0/1/2).
    assert direct_count == _MONEY_TRAIL_CHAIN_LENGTH, (
        f"Producer-side regression (money_trail): planted a "
        f"chain_length={_MONEY_TRAIL_CHAIN_LENGTH} chain but the "
        f"matview holds {direct_count} edges under root "
        f"{_PLANTED_CHAIN_ROOT!r}. Recursive-CTE walk may have changed "
        f"shape or the plants weren't refreshed in."
    )

    dashboard_count = count_money_trail_rows(driver, _PLANTED_CHAIN_ROOT)
    assert dashboard_count == direct_count, (
        f"Renderer disagrees with the matview (money_trail): the "
        f"dashboard's Hop-by-Hop table shows {dashboard_count} rows at "
        f"root={_PLANTED_CHAIN_ROOT!r}, a direct SELECT against "
        f"{cfg.db_table_prefix}_inv_money_trail_edges WHERE "
        f"root_transfer_id = {_PLANTED_CHAIN_ROOT!r} shows {direct_count}."
    )

    rows_seen = rows_seen_money_trail(driver, _PLANTED_CHAIN_ROOT)
    assert rows_seen == direct_count, (
        f"Table window truncated (money_trail): {rows_seen} of "
        f"{direct_count} rows visible."
    )

    dashboard_keys = money_trail_row_keys(driver, _PLANTED_CHAIN_ROOT)
    key_cols = key_columns_for("money_trail")
    assert direct_keys == dashboard_keys, (
        f"Dashboard disagrees with the matview on which money_trail "
        f"edges ({key_cols}):\n"
        f"  matview-only: {sorted(direct_keys - dashboard_keys)[:5]}\n"
        f"  dashboard-only: {sorted(dashboard_keys - direct_keys)[:5]}\n"
        f"  matview count: {len(direct_keys)}, dashboard count: "
        f"{len(dashboard_keys)}"
    )
