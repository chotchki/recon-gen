"""AT.5.b — App2 Investigation dashboard ⋈ direct-matview agreement.

The App2 leg of AT.5's L2 4-way gate. Extends AT.5.a's spine⋈matview
contract (`tests/e2e/test_spine_live_agreement.py`) onto the App2
renderer:

    spine_plants  ⊆  direct_matview_SELECT(filtered)  ==  App2 dashboard

For each L2 invariant the dashboard filters the matview at the dataset
SQL level via a parameter pushdown (σ slider for anomaly, chain-root
dropdown for money_trail; see ``apps/investigation/datasets.py``). The
test pegs the App2 driver to a known parameter value, reads the
resulting table, and compares against a direct SELECT against the
matview filtered by the *same* parameter — apples-to-apples
agreement, not "unfiltered matview vs. filtered dashboard" (which
would only agree against the distribution chart, not the detail
tables).

L2 plants land via the spine generators (``AnomalyGenerator`` /
``MoneyTrailGenerator``) — same shape AT.5.a uses, just additionally
walking the App2 dashboard. The plants compose on top of the
``l1_plus_broad`` seed `apply_db_seed` runs first (matches what
``recon-gen data apply --execute`` produces in a real deploy);
matviews are re-refreshed after the L2 plants land so the dashboard's
SQL sees them.

QS leg lands in AT.5.c (heaviest — needs Investigation deploy on AWS).
PDF leg in AT.5.d (spike first — Investigation sections may or may not
exist in the audit PDF today). AT.5.e composes both with App2 into the
parametrized 4-way test.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from recon_gen.common.config import Config, load_config
from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_GEN_CONFIG,
    RECON_GEN_E2E,
)


# Module-level cfg load needs a live cfg yaml or env override; under
# the unit-only CI job neither exists. Match the rest of the e2e
# suite's RECON_GEN_E2E gate at import time so the unit job doesn't
# crash on collection.
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
from tests.e2e._drivers import App2Driver  # noqa: E402


pytestmark = [pytest.mark.e2e, pytest.mark.browser]


# Bundled persona-neutral L2 — the same yaml every other e2e test
# defaults to when no per-cell override is set.
_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE_BUNDLED = _FIXTURES / "spec_example.yaml"


def _resolve_cfg() -> Config:
    """Same cfg-resolution shape as ``test_spine_live_agreement.py``."""
    try:
        explicit_raw = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit_raw = None
    if explicit_raw is not None:
        return load_config(str(explicit_raw))
    candidates = (
        Path("config.yaml"),
        Path("run/config.yaml"),
        Path("run/config.postgres.yaml"),
        Path("run/config.oracle.yaml"),
    )
    for candidate in candidates:
        if candidate.exists():
            return load_config(str(candidate))
    raise RuntimeError(
        "no cfg yaml found; set RECON_GEN_CONFIG=<path> or place "
        "config.yaml / run/config.yaml in the cwd"
    )


_CFG = _resolve_cfg()
_INSTANCE: L2Instance = load_instance(_SPEC_EXAMPLE_BUNDLED)


# Anchor on real today so the broad seed's stuck_* plants land in the
# matview's past-relative-to-NOW window (matches
# test_audit_dashboard_agreement.py's rationale). The σ-threshold +
# chain-root agreement asserts here don't depend on absolute calendar
# date — only on the relative day shape the spine generator + matview
# share — so this stays deterministic across runs.
_TODAY = date.today()  # typing-smell: ignore[test-module-nondeterminism]: stuck_* matviews compute age via CURRENT_TIMESTAMP — anchor must be relative to NOW

# The σ slider's analysis-level default + dataset-parameter default. If
# the dashboard's default drifts (apps/investigation/app.py +
# apps/investigation/datasets.py) and this constant doesn't follow,
# the agreement test catches it loudly: the App2 driver will read at
# this σ and the direct SELECT filter will be at the same σ but the
# dashboard will have rendered a different shape.
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
# (depths 0/1/2) without growing the test's data footprint
# unnecessarily.
_MONEY_TRAIL_CHAIN_LENGTH = 3
_MONEY_TRAIL_AMOUNT = 100.0


@pytest.fixture(scope="module")
def seeded_l2_db():  # type: ignore[no-untyped-def]: returns nothing the test introspects — the fixture's contract is "DB is seeded"
    """Apply the schema + broad seed + L2 spine plants + matview refresh.

    Two-phase seed: ``apply_db_seed`` lays the
    schema+baseline-or-broad+L1-plants+initial refresh (the shape a real
    deploy of ``recon-gen data apply --execute`` produces); the spine
    generators then plant L2 violations on top, and a second
    matview refresh picks them up.

    Module-scoped — seeding is the expensive setup and the L2 anomaly +
    money_trail asserts both read the same matview state.
    """
    from tests.e2e._seed_helpers import apply_db_seed

    conn = connect_demo_db(_CFG)
    try:
        apply_db_seed(
            conn, _INSTANCE,
            prefix=_CFG.db_table_prefix,
            mode="l1_plus_broad",
            today=_TODAY,
            dialect=_CFG.dialect,
            include_baseline=False,
        )

        # AT.5.b — explicit L2 plants via the spine generators. Same
        # shape AT.4's semantic-lock tests use; here we drive the
        # generator at a live deployed DB rather than an in-memory
        # SQLite so the dashboard's renderer-layer has something to
        # show. ``prefix`` swap lets the generator write to whatever
        # the cfg's db_table_prefix declares (matches AS.4's
        # LedgerSimulation prefix wiring).
        anchor = _TODAY - timedelta(days=2)
        anomaly_gen = AnomalyInvariant().scenario_for(
            "CustomerSubledger", "CustomerSubledger",
            baseline_pair_count=_ANOMALY_BASELINE_PAIRS,
            baseline_amount=_ANOMALY_BASELINE_AMOUNT,
            spike_magnitude=_ANOMALY_SPIKE_MAGNITUDE,
            anchor_day=anchor,
            instance=_INSTANCE,
        )
        anomaly_gen.prefix = _CFG.db_table_prefix
        money_trail_gen = MoneyTrailInvariant().scenario_for(
            "CustomerSubledger",
            chain_length=_MONEY_TRAIL_CHAIN_LENGTH,
            amount=_MONEY_TRAIL_AMOUNT,
            anchor_day=anchor,
            instance=_INSTANCE,
        )
        money_trail_gen.prefix = _CFG.db_table_prefix
        anomaly_gen.emit(conn)
        money_trail_gen.emit(conn)
        conn.commit()

        # Refresh again so the matviews see the L2 plants (apply_db_seed
        # ran a refresh before the spine plants landed).
        refresh_sql = refresh_matviews_sql(
            _INSTANCE,
            prefix=_CFG.db_table_prefix,
            dialect=_CFG.dialect,
        )
        with conn.cursor() as cur:
            execute_script(cur, refresh_sql, dialect=_CFG.dialect)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def inv_app_tree():  # type: ignore[no-untyped-def]: returns the tree-built App; annotating would force the imports below to module scope
    """Tree-built Investigation App against the test cfg + bundled L2.

    Mirrors the conftest ``inv_app`` fixture's shape but builds against
    *this* test's cfg (not the broader e2e session's), so the dataset
    prefix matches whatever cfg + L2 the agreement test loaded above.
    """
    from recon_gen.apps.investigation.app import build_investigation_app

    app = build_investigation_app(_CFG, l2_instance=_INSTANCE)
    app.emit_analysis()
    return app


@pytest.fixture
def app2_inv_driver(inv_app_tree, seeded_l2_db):  # type: ignore[no-untyped-def]: returns an App2Driver; annotation would force the import to module scope
    """Function-scoped App2 driver pointed at the Investigation tree.

    Same pattern as the conftest ``_parametrized_dashboard_driver`` does
    for the app2 leg — wires the live-DB fetcher pair onto the tree and
    spins ``App2Driver.serving`` for the test body to drive. The
    function scope is intentional: the σ slider + chain-root dropdown
    writes between tests would otherwise carry state; a fresh server
    per test keeps the parameter URL clean. Server spin is ~1-2s,
    acceptable for the test count.

    Depends on ``seeded_l2_db`` so the L2 plants land before the
    dashboard reads them.
    """
    _ = seeded_l2_db  # ordering dep
    from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

    assert inv_app_tree.analysis is not None
    data_fetcher, options_fetcher = make_live_db_fetchers_for_app(
        tree_app=inv_app_tree, cfg=_CFG,
    )
    with App2Driver.serving(
        tree_app=inv_app_tree, sheet=inv_app_tree.analysis.sheets[0],
        data_fetcher=data_fetcher, options_fetcher=options_fetcher,
        dashboard_id="inv", dashboard_title="Investigation (live)",
    ) as driver:
        driver.open("inv")
        yield driver


@pytest.fixture
def db_conn():  # type: ignore[no-untyped-def]: live PG/Oracle/SQLite connection — concrete type varies per dialect, no shared protocol
    """Function-scoped raw DB connection for the direct-SELECT anchor.

    Cheap (a handful of count + key-set queries per test); the
    ``seeded_l2_db`` fixture's conn is closed by the time the asserts
    run.
    """
    conn = connect_demo_db(_CFG)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Anomaly — App2 dashboard ⋈ σ-filtered direct matview SELECT
# ---------------------------------------------------------------------------


def test_anomaly_app2_agrees_with_direct_matview(
    seeded_l2_db, app2_inv_driver, db_conn,
) -> None:
    """The "Flagged Pair-Windows — Ranked" table on App2 shows the same
    set of (sender, recipient, window_end) rows as a direct
    ``SELECT FROM <prefix>_inv_pair_rolling_anomalies WHERE z_score >=
    <sigma>``, when the σ slider is set to ``<sigma>``.

    The spike planted in ``seeded_l2_db`` is well above 4σ
    (100_000 spike vs 100 baseline pairs × $100 → ~140σ separation
    after stddev compression), so the σ=2 default unambiguously shows
    at least one row regardless of background-data shape.
    """
    _ = seeded_l2_db
    # Direct SELECT ground truth — every (sender, recipient, window_end)
    # whose z_score >= the σ slider value the App2 read below uses.
    direct_count = count_anomaly_matview_rows(
        db_conn, _CFG.db_table_prefix, sigma_threshold=_DEFAULT_SIGMA,
    )
    direct_keys = anomaly_matview_row_keys(
        db_conn, _CFG.db_table_prefix, sigma_threshold=_DEFAULT_SIGMA,
    )

    # The spike must reach the matview — a producer-side regression
    # below this asserts the plant→matview path is intact before the
    # renderer-side equality assert exposes any renderer issue.
    assert direct_count >= 1, (
        f"Producer-side regression (anomaly): the spike planted at "
        f"z>>4σ should be in the matview, but the σ>={_DEFAULT_SIGMA} "
        f"SELECT against {_CFG.db_table_prefix}_inv_pair_rolling_anomalies "
        f"returned 0 rows. Plant→matview path broken or matview not "
        f"refreshed after the L2 plants."
    )

    # App2 read at the same σ.
    app2_count = count_anomaly_rows(app2_inv_driver, _DEFAULT_SIGMA)
    assert app2_count == direct_count, (
        f"Renderer disagrees with the matview (anomaly, App2): the "
        f"dashboard's Flagged Pair-Windows table shows {app2_count} "
        f"rows at σ>={_DEFAULT_SIGMA}, a direct SELECT against "
        f"{_CFG.db_table_prefix}_inv_pair_rolling_anomalies WHERE "
        f"z_score >= {_DEFAULT_SIGMA} shows {direct_count}. Same "
        f"matview, same filter."
    )

    # Row identity — DOM-window guard first so a truncated read fails
    # loudly rather than passing a partial comparison.
    app2_seen = rows_seen_anomaly(app2_inv_driver, _DEFAULT_SIGMA)
    assert app2_seen == direct_count, (
        f"App2 table window truncated (anomaly): {app2_seen} of "
        f"{direct_count} rows visible — the row-identity comparison "
        f"would be partial. (A denser seed needs a read-all path.)"
    )

    app2_keys = anomaly_row_keys(app2_inv_driver, _DEFAULT_SIGMA)
    key_cols = key_columns_for("anomaly")
    assert direct_keys == app2_keys, (
        f"App2 disagrees with the matview on which anomaly rows "
        f"({key_cols}):\n"
        f"  matview-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}\n"
        f"  matview count: {len(direct_keys)}, App2 count: {len(app2_keys)}"
    )


# ---------------------------------------------------------------------------
# Money trail — App2 dashboard ⋈ root-filtered direct matview SELECT
# ---------------------------------------------------------------------------


def test_money_trail_app2_agrees_with_direct_matview(
    seeded_l2_db, app2_inv_driver, db_conn,
) -> None:
    """The "Money Trail — Hop-by-Hop" table on App2 shows the same set
    of ``(transfer_id, depth)`` rows as a direct
    ``SELECT FROM <prefix>_inv_money_trail_edges WHERE root_transfer_id
    = <root>``, when the chain-root dropdown is set to ``<root>``.

    The planted chain's root_transfer_id is ``xfer-money-trail-0``
    (deterministic from the MoneyTrailGenerator's account-id naming
    scheme); we read the matview's distinct roots to discover it
    rather than hardcoding, so a generator-rename doesn't silently
    break the test's setup phase.
    """
    _ = seeded_l2_db
    roots = distinct_money_trail_roots(db_conn, _CFG.db_table_prefix)
    assert "xfer-money-trail-0" in roots, (
        f"Producer-side regression (money_trail): the planted chain's "
        f"root ('xfer-money-trail-0') is missing from "
        f"{_CFG.db_table_prefix}_inv_money_trail_edges. Found roots: "
        f"{sorted(roots)[:10]} (+ {len(roots) - 10 if len(roots) > 10 else 0} "
        f"more). Plant→matview path broken or matview not refreshed "
        f"after the L2 plants."
    )
    planted_root = "xfer-money-trail-0"

    direct_count = count_money_trail_matview_rows(
        db_conn, _CFG.db_table_prefix, root_transfer_id=planted_root,
    )
    direct_keys = money_trail_matview_row_keys(
        db_conn, _CFG.db_table_prefix, root_transfer_id=planted_root,
    )
    # Sanity — the 3-deep chain plants 3 edges (depths 0/1/2).
    assert direct_count == _MONEY_TRAIL_CHAIN_LENGTH, (
        f"Producer-side regression (money_trail): planted a "
        f"chain_length={_MONEY_TRAIL_CHAIN_LENGTH} chain but the "
        f"matview holds {direct_count} edges under root "
        f"{planted_root!r}. Recursive-CTE walk may have changed shape "
        f"or the plants weren't refreshed in."
    )

    app2_count = count_money_trail_rows(app2_inv_driver, planted_root)
    assert app2_count == direct_count, (
        f"Renderer disagrees with the matview (money_trail, App2): the "
        f"dashboard's Hop-by-Hop table shows {app2_count} rows at "
        f"root={planted_root!r}, a direct SELECT against "
        f"{_CFG.db_table_prefix}_inv_money_trail_edges WHERE "
        f"root_transfer_id = {planted_root!r} shows {direct_count}."
    )

    app2_seen = rows_seen_money_trail(app2_inv_driver, planted_root)
    assert app2_seen == direct_count, (
        f"App2 table window truncated (money_trail): {app2_seen} of "
        f"{direct_count} rows visible."
    )

    app2_keys = money_trail_row_keys(app2_inv_driver, planted_root)
    key_cols = key_columns_for("money_trail")
    assert direct_keys == app2_keys, (
        f"App2 disagrees with the matview on which money_trail edges "
        f"({key_cols}):\n"
        f"  matview-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}\n"
        f"  matview count: {len(direct_keys)}, App2 count: {len(app2_keys)}"
    )
