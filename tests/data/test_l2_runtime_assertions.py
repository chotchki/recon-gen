"""R.5.c + R.5.d — runtime assertions against the demo Postgres DB.

These tests query the live ``<prefix>_*`` matviews + base tables and
assert that the Phase R baseline + plant pipeline produced data with
the expected shape:

- **R.5.c (Volume Anomalies smoke)**: the
  ``<prefix>_inv_pair_rolling_anomalies`` matview has at least N
  rows with ``z_score >= 3`` — proves the rolling 2-day stddev
  signal is meaningful on top of the 90-day baseline. Without the
  baseline, every (sender, recipient) pair would be near-zero
  variance and the dashboard's "high anomaly" coloring band would
  always be empty.

- **R.5.d (L2 coverage assertions)**: per Rail / Chain / TransferTemplate
  / LimitSchedule declared in the L2 instance, assert runtime
  evidence exists in ``<prefix>_current_transactions``. Catches:
  - Dead Rails (declared but never fired)
  - Chains with no completed parent → child pair
  - TransferTemplates whose legs don't actually net to expected_net
  - LimitSchedules absent from any rail

Both tests gate on a live Postgres connection via ``QS_GEN_DEMO_DATABASE_URL``
or fall back to ``run/config.postgres.yaml``'s ``demo_database_url``.
Skip cleanly when the DB isn't reachable (CI environments without it,
fresh checkouts).

These run as ordinary pytest tests — no e2e harness needed. The data
comes from a previous ``demo apply`` run; nothing is mutated. Run after
applying the seed:

    quicksight-gen demo apply --all -c run/config.postgres.yaml \\
        --l2-instance tests/l2/sasquatch_pr.yaml -o run/out
    pytest tests/test_l2_runtime_assertions.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from quicksight_gen.common.env_keys import QS_GEN_DEMO_DATABASE_URL
from quicksight_gen.common.l2 import L2Instance
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.primitives import TwoLegRail


_SASQUATCH_PR_YAML = Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"


def _demo_database_url() -> str | None:
    """Locate the demo DB URL from env or run/config.postgres.yaml.

    Returns None if no URL is reachable; the test will skip.
    """
    env_url = QS_GEN_DEMO_DATABASE_URL.get_or_none()
    if env_url:
        return env_url
    cfg_path = Path(__file__).parent.parent / "run" / "config.postgres.yaml"
    if not cfg_path.exists():
        return None
    import yaml
    text = cfg_path.read_text()
    cfg = yaml.safe_load(text)
    return cfg.get("demo_database_url")


@pytest.fixture(scope="module")
def demo_db_conn() -> Any:
    """Module-scoped psycopg2 connection to the demo DB.

    Skips the entire module when the DB is not reachable — e.g.,
    fresh checkout, CI without DB credentials.
    """
    url = _demo_database_url()
    if not url:
        pytest.skip(
            "Demo DB URL not configured — set QS_GEN_DEMO_DATABASE_URL or "
            "populate run/config.postgres.yaml::demo_database_url to run "
            "live runtime assertions."
        )
    try:
        import psycopg
    except ImportError:
        pytest.skip("psycopg not installed — `pip install -e .[demo]`")
    try:
        conn = psycopg.connect(url)
    except Exception as e:
        pytest.skip(f"Demo DB unreachable at {url[:40]}...: {e}")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def sasquatch_instance() -> L2Instance:
    return load_instance(_SASQUATCH_PR_YAML)


def _matview_has_rows(conn: Any, name: str) -> bool:
    """True iff the matview/table exists AND has > 0 rows."""
    with conn.cursor() as cur:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {name}")
            row = cur.fetchone()
            return bool(row) and row[0] > 0
        except Exception:
            conn.rollback()
            return False


# ---------------------------------------------------------------------------
# R.5.c — Volume Anomalies smoke
# ---------------------------------------------------------------------------


class TestVolumeAnomaliesSignal:
    """R.5.c — the rolling-2-day z-score matview has signal at ≥3σ."""

    def test_matview_populated(
        self, demo_db_conn: Any, sasquatch_instance: L2Instance,
    ) -> None:
        prefix = sasquatch_instance.instance
        view = f"{prefix}_inv_pair_rolling_anomalies"
        if not _matview_has_rows(demo_db_conn, view):
            pytest.skip(
                f"{view} matview empty — apply the demo seed first."
            )
        with demo_db_conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {view}")
            n = cur.fetchone()[0]
        assert n >= 50, (
            f"{view} should have ≥50 (sender, recipient, day) windows "
            f"after a 90-day baseline; got {n}. The Phase R baseline "
            f"should produce thousands of pair-windows."
        )

    def test_at_least_5_anomalies_clear_3sigma(
        self, demo_db_conn: Any, sasquatch_instance: L2Instance,
    ) -> None:
        # R.5.c bar: with 90 days of baseline, the rolling-2-day-stddev
        # signal must produce at least 5 anomalies in the dashboard's
        # "high" coloring band (z >= 3). Without baseline, the matview
        # produces near-zero rows because every pair has 1-2 windows
        # only — stddev computation is meaningless.
        prefix = sasquatch_instance.instance
        view = f"{prefix}_inv_pair_rolling_anomalies"
        if not _matview_has_rows(demo_db_conn, view):
            pytest.skip(f"{view} empty — apply the demo seed first.")
        with demo_db_conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {view} WHERE z_score >= 3")
            n_high = cur.fetchone()[0]
        assert n_high >= 5, (
            f"R.5.c — Volume Anomalies should produce ≥5 z>=3σ rows "
            f"after Phase R baseline; got {n_high}. Either the baseline "
            f"isn't generating enough variance, or the matview SQL is "
            f"broken. Check the inv_pair_rolling_anomalies matview shape."
        )

    def test_planted_recipient_appears_in_matview(
        self, demo_db_conn: Any, sasquatch_instance: L2Instance,
    ) -> None:
        # R.5.d adjacent — even if the planted InvFanoutPlant doesn't
        # personally clear 3σ (the population is dominated by big
        # merchant card sales), the planted recipient SHOULD have at
        # least one window in the matview. Catches "fanout plant got
        # filtered out" regressions.
        prefix = sasquatch_instance.instance
        view = f"{prefix}_inv_pair_rolling_anomalies"
        if not _matview_has_rows(demo_db_conn, view):
            pytest.skip(f"{view} empty — apply the demo seed first.")
        with demo_db_conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {view} "
                f"WHERE recipient_account_id = %s",
                ("cust-0001-snb",),
            )
            n = cur.fetchone()[0]
        assert n >= 1, (
            "planted InvFanoutPlant recipient cust-0001-snb missing from "
            f"{view}; the fanout plant should produce at least one "
            "(sender, cust-0001-snb, day) window."
        )


# ---------------------------------------------------------------------------
# R.5.d — L2 coverage assertions
# ---------------------------------------------------------------------------


class TestL2CoverageAssertions:
    """R.5.d — every L2-declared primitive has runtime evidence."""

    def test_every_rail_has_legs(
        self, demo_db_conn: Any, sasquatch_instance: L2Instance,
    ) -> None:
        # Per R.5.d: for every Rail in the L2 instance, assert N >= M
        # legs in <prefix>_current_transactions. Catches dead Rails
        # (declared but never fired by the seed generator).
        #
        # Threshold is cadence-aware: monthly_eom rails fire only ~3
        # times over 90 days so M=2 there; daily/intraday/non-aggregating
        # rails should comfortably clear M=5. Rails that can't (e.g.,
        # misconfigured roles in the L2 YAML) surface here.
        prefix = sasquatch_instance.instance
        view = f"{prefix}_current_transactions"
        if not _matview_has_rows(demo_db_conn, view):
            pytest.skip(f"{view} empty — apply the demo seed first.")

        missing: list[tuple[str, int, int]] = []
        for rail in sasquatch_instance.rails:
            cadence = (rail.cadence or "").lower()
            threshold = 2 if "monthly" in cadence else 5
            with demo_db_conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM {view} WHERE rail_name = %s",
                    (str(rail.name),),
                )
                count = cur.fetchone()[0]
            if count < threshold:
                missing.append((str(rail.name), count, threshold))
        assert not missing, (
            f"R.5.d — Rails with < threshold legs in {view} (dead-rail "
            f"candidates, format (rail, actual, threshold)): {missing}"
        )

    def test_every_chain_has_a_completed_pair(
        self, demo_db_conn: Any, sasquatch_instance: L2Instance,
    ) -> None:
        # R.5.d: every Chain must have at least one parent firing
        # whose transfer_id appears as transfer_parent_id on at least
        # one child firing. Catches Chain-emit regressions.
        #
        # Skips chains whose parent is a TransferTemplate (not a Rail);
        # the baseline only emits chain children for Rail-parented
        # chains (per R.2.d's first land — TransferTemplate firings
        # are tracked separately).
        prefix = sasquatch_instance.instance
        view = f"{prefix}_current_transactions"
        if not _matview_has_rows(demo_db_conn, view):
            pytest.skip(f"{view} empty — apply the demo seed first.")

        rail_names = {str(r.name) for r in sasquatch_instance.rails}
        missing: list[str] = []
        for entry in sasquatch_instance.chains:
            parent = str(entry.parent)
            child = str(entry.child)
            if parent not in rail_names:
                continue  # skip TransferTemplate-parented chains
            if child not in rail_names:
                continue  # skip TransferTemplate-childed chains
            with demo_db_conn.cursor() as cur:
                cur.execute(
                    f"""SELECT COUNT(*) FROM {view} child
                    WHERE child.rail_name = %s
                      AND EXISTS (
                          SELECT 1 FROM {view} parent
                          WHERE parent.rail_name = %s
                          AND parent.transfer_id = child.transfer_parent_id
                      )""",
                    (child, parent),
                )
                count = cur.fetchone()[0]
            if count == 0:
                missing.append(f"{parent} -> {child}")
        assert not missing, (
            f"R.5.d — Chains with no completed parent → child firing "
            f"pair (chain-emit regression): {missing}"
        )

    def test_every_transfer_template_legs_net_to_expected(
        self, demo_db_conn: Any, sasquatch_instance: L2Instance,
    ) -> None:
        # R.5.d: per TransferTemplate with expected_net=0, assert that
        # >= 80% of template instances actually net to zero. R.3 plants
        # intentionally violate to surface on the L2 Exceptions sheet,
        # so we allow some slack.
        prefix = sasquatch_instance.instance
        view = f"{prefix}_current_transactions"
        if not _matview_has_rows(demo_db_conn, view):
            pytest.skip(f"{view} empty — apply the demo seed first.")

        from decimal import Decimal
        violations: list[str] = []
        for tt in sasquatch_instance.transfer_templates:
            if tt.expected_net != Decimal("0"):
                continue
            with demo_db_conn.cursor() as cur:
                cur.execute(
                    f"""SELECT transfer_id,
                              SUM(amount_money) AS net
                       FROM {view}
                       WHERE template_name = %s
                       GROUP BY transfer_id""",
                    (str(tt.name),),
                )
                rows = cur.fetchall()
            if not rows:
                continue  # template never instantiated; not R.5.d's job
            non_zero = sum(1 for _, net in rows if net != Decimal("0"))
            total = len(rows)
            violation_rate = non_zero / total
            if violation_rate > 0.20:  # >20% violating is too much
                violations.append(
                    f"{tt.name}: {non_zero}/{total} instances net != 0 "
                    f"({violation_rate:.0%})"
                )
        assert not violations, (
            f"R.5.d — TransferTemplates with too-many net != expected "
            f"violations (>20% threshold): {violations}"
        )

    def test_every_limit_schedule_has_a_matching_rail(
        self, demo_db_conn: Any, sasquatch_instance: L2Instance,
    ) -> None:
        # R.5.d: every LimitSchedule.rail should resolve to a Rail.name
        # that has runtime legs (otherwise the cap never fires). The
        # validator's R10 covers the structural binding; this runtime
        # check confirms legs actually exist for every rail the
        # LimitSchedule covers.
        prefix = sasquatch_instance.instance
        view = f"{prefix}_current_transactions"
        if not _matview_has_rows(demo_db_conn, view):
            pytest.skip(f"{view} empty — apply the demo seed first.")

        no_legs: list[str] = []
        for ls in sasquatch_instance.limit_schedules:
            with demo_db_conn.cursor() as cur:
                cur.execute(
                    f"""SELECT COUNT(*) FROM {view}
                       WHERE rail_name = %s""",
                    (str(ls.rail),),
                )
                count = cur.fetchone()[0]
            if count == 0:
                no_legs.append(
                    f"rail={ls.rail} "
                    f"(parent_role={ls.parent_role}, cap=${ls.cap})"
                )
        assert not no_legs, (
            f"R.5.d — LimitSchedules whose rail has zero legs in the "
            f"runtime data (cap can never fire): {no_legs}"
        )
