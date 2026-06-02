"""CB.5 stage 2 — DB-tier producer: direct matview SELECT for L2 anomaly.

Decomposed from `tests/e2e/test_inv_dashboard_agreement.py`'s
`test_invariant_three_way_agreement[anomaly]` cell. The producer:

1. Loads cfg + seeds the isolated `<prefix>_iagree` DB with the
   `l1_plus_broad` baseline + spine-generator anomaly plants.
2. Refreshes matviews so the plants land in `_inv_pair_rolling_anomalies`.
3. Runs the spine `AnomalyInvariant.detect(conn)` AND the direct
   σ-filtered matview SELECT — both at the same threshold.
4. Asserts spine == direct (the AT.5.a contract), writes both as
   artifacts so the cross-renderer validator (in qs_browser/) can
   compare against App2 + QS.

The L2 isolation suffix (`iagree`) keeps the destructive DROP CASCADE
out of the runner's shared seed namespace — mirrors the pre-CB.5
fixture's design.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Iterator

import pytest

from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import RECON_GEN_E2E
from recon_gen.common.l2 import (
    L2Instance,
    load_instance,
    refresh_matviews_sql,
)

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "Investigation agreement producer needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports keep collection cheap on the unit job
from recon_gen.common.spine import (  # noqa: E402
    AnomalyInvariant,
    Violation,
)
from tests.audit._matview_extract import (  # noqa: E402
    anomaly_matview_row_keys,
    count_anomaly_matview_rows,
)
from tests.e2e._agreement import write_rendered_rows  # noqa: E402
from tests.e2e._agreement_helpers import (  # noqa: E402
    l2_yaml_for_test,
    today_anchor,
)

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.spine.anomaly import AnomalyGenerator


# CB.7 (2026-06-02) — removed `pytest.mark.xdist_group(...)` previously
# pinned this module to a single worker. The new `isolated_cfg` fixture
# (tests/e2e/db/conftest.py) gives every xdist worker its own
# per-worker prefix, so parametrize cells distributing across workers
# no longer race on DROP CASCADE — each worker seeds its own prefix.
pytestmark = [pytest.mark.e2e]


_TODAY = today_anchor()
_INSTANCE: L2Instance = load_instance(l2_yaml_for_test())

# σ slider's analysis-level default + dataset-parameter default.
# Same threshold both producers (direct SELECT and renderer reads)
# apply, so the agreement comparison is apples-to-apples.
_DEFAULT_SIGMA = 2.0

# A high-magnitude anomaly plant — 1000× baseline with 100 background
# pairs feeding population stddev. See pre-CB.5 module for the
# 3σ-separation rationale.
_ANOMALY_BASELINE_PAIRS = 100
_ANOMALY_BASELINE_AMOUNT = 100.0
_ANOMALY_SPIKE_MAGNITUDE = 100_000.0

def _plant_anchor_day() -> date:
    return _TODAY - timedelta(days=2)


def _build_anomaly_generator(
    cfg: "Config", anchor_day: date,
) -> "AnomalyGenerator":
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=_ANOMALY_BASELINE_PAIRS,
        baseline_amount=_ANOMALY_BASELINE_AMOUNT,
        spike_magnitude=_ANOMALY_SPIKE_MAGNITUDE,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db_table_prefix
    return gen


@pytest.fixture(scope="module")
def seeded_l2_db(isolated_cfg: "Config") -> None:
    """Apply schema + broad seed + spine plants + matview refresh
    against the per-worker isolated `isolated_cfg`. CB.7: replaced the
    hand-rolled `isolated_inv_cfg` + `_iagree` suffix with the
    canonical `isolated_cfg` injection (per-worker prefix from
    `tests/e2e/db/conftest.py`)."""
    from tests.e2e._seed_helpers import apply_db_seed

    conn = connect_demo_db(isolated_cfg)
    try:
        apply_db_seed(
            conn, _INSTANCE,
            prefix=isolated_cfg.db_table_prefix,
            mode="l1_plus_broad",
            today=_TODAY,
            dialect=isolated_cfg.dialect,
            include_baseline=False,
        )
        # AT.5.b — L2 plants via the spine generator.
        anchor = _plant_anchor_day()
        anomaly_gen = _build_anomaly_generator(isolated_cfg, anchor)
        anomaly_gen.emit(conn)
        conn.commit()
        # Refresh again so matviews see the L2 plants.
        refresh_sql = refresh_matviews_sql(
            _INSTANCE,
            prefix=isolated_cfg.db_table_prefix,
            dialect=isolated_cfg.dialect,
        )
        with conn.cursor() as cur:
            execute_script(
                cur, refresh_sql, dialect=isolated_cfg.dialect,
            )
        conn.commit()
    finally:
        conn.close()


def _anomaly_spine_keys(
    violations: set[Violation],
) -> set[tuple[str, str, date]]:
    """Project anomaly Violations to (sender, recipient, window_end)
    — matches matview key + dashboard group_by."""
    from datetime import datetime
    out: set[tuple[str, str, date]] = set()
    for v in violations:
        items = dict(v.identity)
        sender = items.get("sender_account_id")
        recipient = items.get("recipient_account_id")
        we = items.get("window_end")
        if sender is None or recipient is None or we is None:
            continue
        if isinstance(we, date):
            we_date = we
        elif isinstance(we, datetime):
            we_date = we.date()
        else:
            we_date = date.fromisoformat(str(we)[:10])
        out.add((str(sender), str(recipient), we_date))
    return out


def _serialize_keys(
    keys: "set[tuple[Any, ...]]",
) -> list[list[Any]]:
    return sorted([_normalise_row(list(t)) for t in keys])


def _normalise_row(row: list[Any]) -> list[Any]:
    from datetime import date as _date, datetime as _datetime
    out: list[Any] = []
    for cell in row:
        if isinstance(cell, _datetime):
            out.append(cell.date().isoformat())
        elif isinstance(cell, _date):
            out.append(cell.isoformat())
        else:
            out.append(cell)
    return out


def test_anomaly_direct_extract(
    seeded_l2_db: None,
    db_conn: Any,
    isolated_cfg: "Config",
) -> None:
    """Direct σ-filtered matview SELECT + spine `detect()` for the
    anomaly invariant. Writes both as artifacts for the validator.

    Producer-side assertion (the AT.5.a contract): spine == direct
    at the same σ threshold. The detector returns every bucket;
    we filter spine keys by intersection with the σ-thresholded
    matview SELECT to mirror the dashboard's WHERE-clause pushdown.
    """
    _ = seeded_l2_db
    prefix = isolated_cfg.db_table_prefix
    anchor = _plant_anchor_day()
    gen = _build_anomaly_generator(isolated_cfg, anchor)
    # The expected anomaly singleton — see
    # `expected_l2_audit_counts` for the same shape. Local construction
    # avoids requiring a MoneyTrailGenerator from the anomaly producer.
    expected_anomaly_keys: tuple[tuple[str, str, date], ...] = (
        (
            gen.sender_account_id,
            gen.recipient_account_id,
            gen.anchor_day,
        ),
    )
    expected_count = len(expected_anomaly_keys)

    direct_count = count_anomaly_matview_rows(
        db_conn, prefix, sigma_threshold=_DEFAULT_SIGMA,
    )
    direct_keys = anomaly_matview_row_keys(
        db_conn, prefix, sigma_threshold=_DEFAULT_SIGMA,
    )

    # Spine ⋈ direct matview (AT.5.a). Detector returns every bucket;
    # intersection with σ-filtered direct keys mirrors dashboard
    # pushdown.
    inv = AnomalyInvariant(prefix=prefix)
    spine_keys = _anomaly_spine_keys(inv.detect(db_conn))  # type: ignore[arg-type]: live dbapi conn — Invariant.detect annotated as sqlite3 but accepts any 2.0 conn
    spine_keys_at_sigma = spine_keys & direct_keys  # type: ignore[operator]: matview_keys items are tuple[str|date,...] union; subset of spine_keys' tuple[str,str,date]

    assert spine_keys_at_sigma == direct_keys, (
        f"Spine.detect disagrees with the matview (anomaly):\n"
        f"  spine-only: {sorted(spine_keys_at_sigma - direct_keys)[:5]}\n"  # type: ignore[type-var]: set difference produces sortable tuples by construction
        f"  direct-only: {sorted(direct_keys - spine_keys_at_sigma)[:5]}\n"  # type: ignore[type-var]: same as above
        f"  counts: spine={len(spine_keys_at_sigma)} "
        f"direct={len(direct_keys)}"
    )
    expected_planted_keys = set(expected_anomaly_keys)
    assert expected_planted_keys <= direct_keys, (  # type: ignore[operator]: tuple[str,str,date] ⊆ tuple[str|date,...]; pyright doesn't follow the subset relation through the union
        f"Planted anomaly keys missing from the matview:\n"
        f"  planted but absent: "
        f"{sorted(expected_planted_keys - direct_keys)[:5]}"  # type: ignore[type-var,operator]: set difference + sort over union tuple
    )
    assert direct_count >= expected_count, (
        f"Producer regression: scenario planted "
        f"{expected_count} rows but matview holds {direct_count}"
    )

    payload: list[dict[str, Any]] = []
    for key_tuple in _serialize_keys(direct_keys):
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("db", "anomaly_direct_rows", payload)
    write_rendered_rows("db", "anomaly_direct_meta", [
        {
            "direct_count": direct_count,
            "expected_count": expected_count,
            "sigma_threshold": _DEFAULT_SIGMA,
        },
    ])
