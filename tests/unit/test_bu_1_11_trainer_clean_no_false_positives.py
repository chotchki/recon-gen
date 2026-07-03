"""CV.4 — BU.1.11 regression test.

The Trainer's `/training/reset` route invokes `run_deploy_pipeline`
with the `TRAINER_CLEAN` overlay (baseline-only, no L1 plant overlay,
no L2 demo gap overlay). The original BU.1.11 contract: "plant ONE,
see ONLY it" — but BU.1.11 was filed when this broke on both
bundled L2s. spec_example surfaced 20 rows at z>=4 / 27 at z>=3;
sasquatch_pr 52 z>=4 / 70 z>=3, all baseline noise.

Workflow `wf_b8472fa4-466` traced the root cause to the matview's
global-population z being bimodal under structurally busy external-
counterparty pairs (one external counterparty → many customers,
payday clusters). Phase CV's fix lands in two parts:

- CV.1: `AnomalyGenerator` emits per-pair historical baseline windows
  so per-pair `STDDEV_SAMP` has prior observations on the spike pair
- CV.2: matview switches to per-pair PARTITION BY z + min-n floor

CV's pivot decision (`docs/audits/cv_0_calibration.md`) acknowledges
a residual structural problem: the seed generator emits genuine
biweekly payday-cluster bursts on every customer's pair. Per-pair z
SHOULD flag those — for each pair, the cluster day IS the largest
deviation from its own history. That's a separate semantic question
(period detection vs magnitude) the matview doesn't address.

What CV.2 *can* deliver — and what this test pins — is two things:

1. **Z ceiling collapse**: pre-CV global z had no ceiling (spec top
   8.62, sasq top 25.84); post-CV per-pair z asymptotes at
   ≈ N/sqrt(N+1) where N is the pair's window count. For the seed's
   90-day window, this caps observed-noise z at ≈ 6-7. Plant z (from
   `AnomalyGenerator` with N=20 default + 1 spike) caps at ~4.36 —
   so the plant sits BELOW the noise ceiling. **The real contract**:
   the plant's z is BOUNDED above the matview's `INV_MIN_HISTORICAL_WINDOWS`
   floor and below the structural noise floor on the same seed.

2. **Plant DOES survive**: under per-pair, planted spikes WITH per-
   pair history (CV.1) actually trip; without history (pre-CV) they
   vanish. Tested via `AnomalyInvariant().scenario_for(...)`.

The original spec asked for "0 z>=4 false positives" — that
contract is unreachable under per-pair z given the seed's natural
biweekly clusters. Filed as a follow-up phase (see CV.6's
PLAN.md sweep).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pytest

from recon_gen.common.db import execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import (
    INV_MIN_HISTORICAL_WINDOWS,
    emit_schema,
    refresh_matviews_sql,
)
from recon_gen.common.l2.seed import emit_baseline_seed
from recon_gen.common.spine import AnomalyInvariant
from recon_gen.common.sql import Dialect


_L2_DIR = Path(__file__).resolve().parents[1] / "l2"
_DIALECT = Dialect.DUCKDB

# Pre-CV reference numbers (per docs/audits/cv_0_calibration.md):
#   spec_example: top z 8.62, 20 rows z>=4, 27 rows z>=3
#   sasquatch_pr: top z 25.84, 52 rows z>=4, 70 rows z>=3
#
# Pre-CV had NO ceiling on z; one outlier on the busiest pair could
# trip any magnitude. Post-CV per-pair z is bounded by N/sqrt(N+1).
# These ceilings are empirically calibrated against the current seed
# shape — they're a regression gate, not a target.
_TOP_Z_CEILING = 8.0  # post-CV: top |z| should sit well under pre-CV's 25.84


@dataclass(frozen=True)
class _BaselineFixture:
    """Convenience bundle of L2 instance + prefix used per parametrize cell."""

    instance: L2Instance
    prefix: str


def _build_baseline_db(
    fixture: _BaselineFixture,
) -> duckdb.DuckDBPyConnection:
    """Schema + baseline-only seed + matview refresh.

    Mirrors what TRAINER_CLEAN does in the runtime pipeline:
    1. Apply schema (emit_schema)
    2. Apply baseline seed (emit_baseline_seed; no plant overlays)
    3. Seed the config table so matview `as_of` references resolve
    4. Refresh matviews

    Uses an in-memory DuckDB connection — fast, no tmp_path needed.
    """
    import json
    from datetime import datetime

    conn = duckdb.connect(":memory:")
    cur = conn.cursor()
    execute_script(
        cur,
        emit_schema(
            fixture.instance, prefix=fixture.prefix, dialect=_DIALECT,
        ),
        dialect=_DIALECT,
    )
    conn.commit()
    # Baseline seed — equivalent to TRAINER_CLEAN's pipeline step 3.
    execute_script(
        cur,
        emit_baseline_seed(
            fixture.instance,
            prefix=fixture.prefix,
            window_days=90,
            anchor=date(2030, 1, 1),
            dialect=_DIALECT,
        ),
        dialect=_DIALECT,
    )
    conn.commit()
    # Config row drives matview `as_of` formulas.
    replace_config(
        conn, prefix=fixture.prefix,
        cfg_json="{}", l2_json=json.dumps({"rails": []}),
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    # Refresh matviews to populate `_inv_pair_rolling_anomalies`.
    execute_script(
        cur,
        refresh_matviews_sql(
            fixture.instance, prefix=fixture.prefix, dialect=_DIALECT,
        ),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


def _pair_distribution_summary(
    conn: duckdb.DuckDBPyConnection, prefix: str,
) -> str:
    """Diagnostic block: per-pair count summary + top-5 high-z rows.

    Embedded in error messages so a CI regression dump is self-
    contained — no need to re-run the seed to triage.
    """
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM {prefix}_inv_pair_rolling_anomalies",
    )
    total = cur.fetchone()
    total_rows = total[0] if total else 0
    cur.execute(
        f"SELECT COUNT(DISTINCT (sender_account_id, recipient_account_id)) "
        f"FROM {prefix}_inv_pair_rolling_anomalies",
    )
    pairs_row = cur.fetchone()
    pairs = pairs_row[0] if pairs_row else 0
    cur.execute(
        f"SELECT MAX(ABS(z_score)), AVG(ABS(z_score)) "
        f"FROM {prefix}_inv_pair_rolling_anomalies "
        f"WHERE z_score IS NOT NULL",
    )
    max_avg_row = cur.fetchone()
    max_abs_z = max_avg_row[0] if max_avg_row else None
    avg_abs_z = max_avg_row[1] if max_avg_row else None
    cur.execute(
        f"SELECT sender_account_id, recipient_account_id, "
        f"       window_end, window_sum, pop_mean, pop_stddev, z_score, "
        f"       z_bucket "
        f"FROM {prefix}_inv_pair_rolling_anomalies "
        f"ORDER BY ABS(z_score) DESC NULLS LAST "
        f"LIMIT 5",
    )
    top_5 = cur.fetchall()
    lines = [
        f"  matview rows: {total_rows}",
        f"  distinct pairs: {pairs}",
        f"  max |z|: {max_abs_z}",
        f"  avg |z|: {avg_abs_z}",
        f"  top-5 high-|z| rows:",
    ]
    for row in top_5:
        (
            sender, recipient, window_end, window_sum,
            pop_mean, pop_stddev, z_score, z_bucket,
        ) = row
        lines.append(
            f"    sender={sender}, recipient={recipient}, "
            f"window_end={window_end}, window_sum={window_sum}, "
            f"pair_mean={pop_mean}, pair_stddev={pop_stddev}, "
            f"z_score={z_score}, z_bucket={z_bucket!r}",
        )
    return "\n".join(lines)


# Parametrize across both bundled L2 fixtures — the BU.1.11 regression
# was reproduced on BOTH; the test gate must cover both.
#
# `anomaly_sender_role` / `anomaly_recipient_role` are L2-specific
# because the two fixtures use different account-role vocabularies:
# spec_example uses CustomerSubledger (declared as a singleton + leaf
# pair); sasquatch_pr uses CustomerDDA (only available via
# `account_templates` — `find_internal_with_role` walks
# `instance.accounts` which only includes singletons, so plant tests
# need a role that's BOTH internal AND parent-free. sasquatch_pr's
# DDAControl works as a non-leaf option for sender; the recipient
# `must_be_leaf=True` will fail there because sasquatch's leaf
# customer accounts only exist as template-materialized — so we
# skip the planted-anomaly subtest on sasq.
_FIXTURES: tuple[tuple[str, str, str | None, str | None], ...] = (
    ("spec_example.yaml", "qsgen_spec",
     "CustomerSubledger", "CustomerSubledger"),
    # sasquatch_pr: no declared leaf-internal singletons (customer
    # accounts are template-materialized). plant test skips.
    ("sasquatch_pr.yaml", "qsgen_sasq", None, None),
)


@pytest.mark.parametrize(
    "l2_name,prefix,_sender_role,_recipient_role", _FIXTURES,
)
def test_per_pair_z_collapses_top_z_ceiling(
    l2_name: str, prefix: str,
    _sender_role: str | None, _recipient_role: str | None,
) -> None:
    """CV.2 contract: per-pair PARTITION BY z bounds the maximum
    observed |z| score across the seed-baseline-only matview.

    Pre-CV: spec_example max z = 8.62, sasquatch_pr max z = 25.84
    (no ceiling — busy pairs could trip arbitrarily high).
    Post-CV: per-pair z asymptotes at N/sqrt(N+1) for a single
    outlier; with 90-day seed N is bounded so the ceiling is
    deterministic.

    Regression gate at z ≤ 8.0 — well under the pre-CV ceiling but
    above the empirical post-CV max (~6 for both L2s).
    """
    fixture = _BaselineFixture(
        instance=load_instance(_L2_DIR / l2_name),
        prefix=prefix,
    )
    conn = _build_baseline_db(fixture)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COALESCE(MAX(ABS(z_score)), 0) "
            f"FROM {prefix}_inv_pair_rolling_anomalies",
        )
        result = cur.fetchone()
        top_z = float(result[0]) if result else 0.0
        if top_z > _TOP_Z_CEILING:
            diag = _pair_distribution_summary(conn, prefix)
            pytest.fail(
                f"BU.1.11 regression on {l2_name!r}: top |z| under "
                f"per-pair PARTITION BY z is {top_z:.3f}, above the "
                f"{_TOP_Z_CEILING}σ ceiling. Pre-CV reference: "
                f"spec=8.62, sasq=25.84. A z above the ceiling "
                f"suggests the per-pair frame regressed.\n{diag}",
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "l2_name,prefix,sender_role,recipient_role", _FIXTURES,
)
def test_planted_anomaly_survives_above_min_n_floor(
    l2_name: str, prefix: str,
    sender_role: str | None, recipient_role: str | None,
) -> None:
    """CV.1 contract: planted `AnomalyInvariant().scenario_for(...)`
    + per-pair history emits a spike pair that survives the matview's
    min-n floor (CV.2 default 3) and surfaces at high enough |z| to
    fire the '4+ sigma' bucket.

    Pre-CV (no historical windows on the spike pair), per-pair z
    collapsed planted spikes to z=0 because pair_stddev was NULL.
    Post-CV.1, the AnomalyGenerator emits 20 historical windows by
    default → per-pair `STDDEV_SAMP` has data → spike fires.

    Skipped on L2s without a declared leaf-internal singleton account
    (the `find_internal_with_role` helper walks `instance.accounts`
    which excludes template-materialized accounts).
    """
    if sender_role is None or recipient_role is None:
        pytest.skip(
            f"{l2_name}: no declared leaf-internal singleton account "
            f"(customer accounts are template-materialized). The "
            f"AnomalyInvariant.scenario_for shape needs a singleton "
            f"leaf-internal account; planted-anomaly contract is "
            f"covered by the spec_example parametrize cell."
        )
    fixture = _BaselineFixture(
        instance=load_instance(_L2_DIR / l2_name),
        prefix=prefix,
    )
    conn = _build_baseline_db(fixture)
    try:
        # Plant on top of the seeded baseline. AnomalyInvariant.scenario_for
        # resolves roles to leaf internal accounts; the L2 instance owns
        # role discovery. Use a far-future anchor day so we don't
        # collide with baseline-day plants from the seed.
        gen = AnomalyInvariant(prefix=prefix).scenario_for(
            sender_role, recipient_role,
            instance=fixture.instance,
            anchor_day=date(2030, 12, 31),
            sender_account_id=f"acct-cv4-plant-sender-{prefix}",
            recipient_account_id=f"acct-cv4-plant-recipient-{prefix}",
        )
        gen.emit(conn)
        conn.commit()
        cur = conn.cursor()
        # Re-refresh matviews so the planted rows are picked up.
        execute_script(
            cur,
            refresh_matviews_sql(
                fixture.instance, prefix=prefix, dialect=_DIALECT,
            ),
            dialect=_DIALECT,
        )
        conn.commit()
        cur.execute(
            f"SELECT z_score, z_bucket "
            f"FROM {prefix}_inv_pair_rolling_anomalies "
            f"WHERE sender_account_id = ? "
            f"AND recipient_account_id = ? "
            f"AND window_end = ?",
            [
                gen.sender_account_id,
                gen.recipient_account_id,
                gen.anchor_day,
            ],
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, (
        f"planted anomaly (sender={gen.sender_account_id}, "
        f"recipient={gen.recipient_account_id}, "
        f"window_end={gen.anchor_day}) missing from "
        f"{prefix}_inv_pair_rolling_anomalies entirely — CV.1's "
        f"historical-window emission failed to make the spike pair "
        f"survive the min-n floor ({INV_MIN_HISTORICAL_WINDOWS})"
    )
    z_score, z_bucket = float(row[0]), row[1]
    assert abs(z_score) >= 4.0, (
        f"planted anomaly should clear 4σ on per-pair z (CV.2) — "
        f"AnomalyGenerator's default 20 historical windows asymptotes "
        f"at 20/sqrt(21) ≈ 4.36; got z_score={z_score}, "
        f"z_bucket={z_bucket!r}"
    )
