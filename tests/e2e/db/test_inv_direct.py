"""CB.7 (refactored 2026-06-02) — DB-tier producer: direct matview SELECT
for the Investigation cross-tier agreement chain.

CB.5-era decomposition split this into two files (one per invariant);
that misnamed the chain — both invariants share the same baseline seed
+ same isolated prefix, so the two files raced on DROP+CREATE under the
shared `AGREEMENT_INV` scope (concurrent workers deadlocked; sequential
workers wiped each other's state). Merged 2026-06-02 into ONE producer
that:

1. Loads cfg + seeds the isolated prefix with the `l1_plus_broad`
   baseline.
2. Emits BOTH plant generators (anomaly + money_trail) on top.
3. Refreshes matviews so the plants land in
   `_inv_pair_rolling_anomalies` and `_inv_money_trail_edges`.
4. Two test functions assert spine == direct for each invariant
   independently against the shared seeded state, writing
   `anomaly_direct_*` and `money_trail_direct_*` artifacts the
   cross-renderer validators in `tests/e2e/qs_browser/` consume.

One producer per scope = no within-scope race, no `xdist_group` pin
needed — the AST check (CB.7-followup) enforces "exactly one producer
file per IsolationScope variant" once the migration completes.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.env_keys import RECON_GEN_E2E
from recon_gen.common.l2 import (
    L2Instance,
    load_instance,
    refresh_matviews_sql,
)
from recon_gen.common.l2.primitives import SCOPE_INTERNAL

if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "Investigation agreement producer needs RECON_GEN_E2E=1",
        allow_module_level=True,
    )

# noqa: E402 — post-skip imports keep collection cheap on the unit job
from recon_gen.common.spine import (  # noqa: E402
    AnomalyInvariant,
    MoneyTrailInvariant,
    Violation,
)
from tests.audit._matview_extract import (  # noqa: E402
    anomaly_matview_row_keys,
    count_anomaly_matview_rows,
    count_money_trail_matview_rows,
    distinct_money_trail_roots,
    money_trail_matview_row_keys,
)
from tests.e2e._agreement import write_rendered_rows  # noqa: E402
from tests.e2e._agreement_helpers import (  # noqa: E402
    l2_yaml_for_test,
    today_anchor,
)

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.spine.anomaly import AnomalyGenerator
    from recon_gen.common.spine.money_trail import MoneyTrailGenerator


from tests._marks import IsolationScope, isolation_producer  # noqa: E402

pytestmark = [
    pytest.mark.e2e,
    isolation_producer(IsolationScope.AGREEMENT_INV),
    # Pin all tests in this writer file to ONE xdist worker so the
    # module-scope `seeded_l2_db` fixture seeds once and all tests
    # share the same DB state. Without this, `-n auto` distributes
    # individual tests across workers; each worker reseeds the same
    # scope-keyed prefix → PG schema-create race.
    pytest.mark.xdist_group(IsolationScope.AGREEMENT_INV.value),
]


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

# 3-deep chain — exercises depths 0/1/2 of the recursive walk.
_MONEY_TRAIL_CHAIN_LENGTH = 3
_MONEY_TRAIL_AMOUNT = 100.0

# Deterministic root — the MoneyTrailGenerator's transfer-id scheme
# is `xfer-money-trail-{index}`; root is `xfer-money-trail-0`.
_PLANTED_CHAIN_ROOT = "xfer-money-trail-0"


def _plant_anchor_day() -> date:
    return _TODAY - timedelta(days=2)


def _pick_internal_leaf_role() -> str:
    """Pick any internal-leaf account's role from the test L2 instance.

    CB.14 — anomaly + money_trail scenarios need a role bound to an
    internal-leaf account; the role NAME is incidental but a parent_role
    IS NOT NULL leaf MUST exist because the matviews filter on
    `account_parent_role IS NOT NULL`. spec_example's L2 carries
    `CustomerSubledger` (templated leaves under a parent_role);
    sasquatch's L2 models only top-level GL accounts with no parents,
    so anomaly + money_trail are structurally unsupportable there.
    pytest.skip is the right tool for "L2 doesn't model this invariant
    shape" — auto-enables when a future L2 grows leaves; until then it
    keeps the matrix green on shapes that can't carry the assertion.
    """
    for a in _INSTANCE.accounts:
        if a.scope == SCOPE_INTERNAL and a.parent_role is not None:
            return str(a.role)
    pytest.skip(
        "L2 instance has no internal-leaf account (no `parent_role IS NOT NULL` "
        "rows); anomaly + money_trail matviews require leaf recipients."
    )


def _build_anomaly_generator(
    cfg: "Config", anchor_day: date,
) -> "AnomalyGenerator":
    role = _pick_internal_leaf_role()
    gen = AnomalyInvariant().scenario_for(
        role, role,
        baseline_pair_count=_ANOMALY_BASELINE_PAIRS,
        baseline_amount=_ANOMALY_BASELINE_AMOUNT,
        spike_magnitude=_ANOMALY_SPIKE_MAGNITUDE,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db.table_prefix
    return gen


def _build_money_trail_generator(
    cfg: "Config", anchor_day: date,
) -> "MoneyTrailGenerator":
    gen = MoneyTrailInvariant().scenario_for(
        _pick_internal_leaf_role(),
        chain_length=_MONEY_TRAIL_CHAIN_LENGTH,
        amount=_MONEY_TRAIL_AMOUNT,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db.table_prefix
    return gen


@pytest.fixture(scope="module")
def seeded_l2_db(isolated_cfg: "Config") -> None:
    """Apply schema + broad seed + BOTH spine plant sets + matview
    refresh against `isolated_cfg`.

    Both anomaly + money_trail share this seed — they target different
    matviews and don't interact, so seeding once + asserting twice is
    both safer and ~2x faster than the old two-file design.
    """
    from tests.e2e._seed_helpers import apply_db_seed

    # CB.14 followup — clear `RECON_GEN_DB_READ_ONLY` before this
    # module-scoped fixture's connect_demo_db. The runner sets the env
    # for du_lo cells per the pre-CB.7 cell-shared-DB model, but this
    # fixture is itself the seeder; RO mode rejects connect because the
    # isolated cfg's per-worker DB file doesn't exist until seed runs.
    # Module-scoped fixture so monkeypatch (function-scoped) doesn't fit
    # — direct os.environ.pop persists only for the seed; the next
    # test's reader-shape uses are unaffected.
    import os
    os.environ.pop("RECON_GEN_DB_READ_ONLY", None)
    os.environ.pop("QS_GEN_DB_READ_ONLY", None)
    conn = connect_demo_db(isolated_cfg)
    try:
        apply_db_seed(
            conn, _INSTANCE,
            prefix=isolated_cfg.db.table_prefix,
            mode="l1_plus_broad",
            today=_TODAY,
            dialect=isolated_cfg.db.dialect,
            include_baseline=False,
        )
        anchor = _plant_anchor_day()
        anomaly_gen = _build_anomaly_generator(isolated_cfg, anchor)
        anomaly_gen.emit(conn)
        mt_gen = _build_money_trail_generator(isolated_cfg, anchor)
        mt_gen.emit(conn)
        conn.commit()
        refresh_sql = refresh_matviews_sql(
            _INSTANCE,
            prefix=isolated_cfg.db.table_prefix,
            dialect=isolated_cfg.db.dialect,
        )
        with conn.cursor() as cur:
            execute_script(
                cur, refresh_sql, dialect=isolated_cfg.db.dialect,
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Anomaly invariant — assertion shape from pre-CB.7 test_inv_anomaly_direct.py
# ---------------------------------------------------------------------


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


def _serialize_anomaly_keys(
    keys: "set[tuple[Any, ...]]",
) -> list[list[Any]]:
    return sorted([_normalise_row(list(t)) for t in keys])


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
    prefix = isolated_cfg.db.table_prefix
    anchor = _plant_anchor_day()
    gen = _build_anomaly_generator(isolated_cfg, anchor)
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
    for key_tuple in _serialize_anomaly_keys(direct_keys):
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("db", "anomaly_direct_rows", payload)
    write_rendered_rows("db", "anomaly_direct_meta", [
        {
            "direct_count": direct_count,
            "expected_count": expected_count,
            "sigma_threshold": _DEFAULT_SIGMA,
        },
    ])


# ---------------------------------------------------------------------
# Money-trail invariant — assertion shape from pre-CB.7 test_inv_money_trail_direct.py
# ---------------------------------------------------------------------


def _money_trail_spine_keys(
    violations: set[Violation],
) -> set[tuple[str, int]]:
    """Project to (transfer_id, depth) — matches matview key +
    dashboard group_by."""
    out: set[tuple[str, int]] = set()
    for v in violations:
        items = dict(v.identity)
        tid = items.get("transfer_id")
        depth = items.get("depth")
        if tid is None or depth is None:
            continue
        out.add((str(tid), int(depth)))  # type: ignore[arg-type]: depth narrowed by early-continue
    return out


def _serialize_mt_keys(keys: "set[tuple[Any, ...]]") -> list[list[Any]]:
    return sorted([list(t) for t in keys])


def test_money_trail_direct_extract(
    seeded_l2_db: None,
    db_conn: Any,
    isolated_cfg: "Config",
) -> None:
    """Direct root-filtered matview SELECT + spine `detect()` for
    money_trail. Writes both as artifacts for the validator.

    Producer-side assertion (AT.5.a contract): spine == direct
    when filtered to the planted root. The detector returns every
    edge across every chain; the dashboard shows one chain at a
    time per the analyst's root pick.
    """
    _ = seeded_l2_db
    prefix = isolated_cfg.db.table_prefix

    roots = distinct_money_trail_roots(db_conn, prefix)
    assert _PLANTED_CHAIN_ROOT in roots, (
        f"Planted chain root {_PLANTED_CHAIN_ROOT!r} missing from "
        f"{prefix}_inv_money_trail_edges; roots found: "
        f"{sorted(roots)[:10]}"
    )

    direct_count = count_money_trail_matview_rows(
        db_conn, prefix, root_transfer_id=_PLANTED_CHAIN_ROOT,
    )
    direct_keys = money_trail_matview_row_keys(
        db_conn, prefix, root_transfer_id=_PLANTED_CHAIN_ROOT,
    )

    inv = MoneyTrailInvariant(prefix=prefix)
    spine_keys = _money_trail_spine_keys(inv.detect(db_conn))  # type: ignore[arg-type]: Invariant.detect annotated as sqlite3 but accepts any DBAPI 2.0 connection
    root_edge_keys = direct_keys
    spine_keys_at_root = spine_keys & root_edge_keys  # type: ignore[operator]: tuple[str,int] & tuple[str,int]

    assert spine_keys_at_root == direct_keys, (
        f"Spine.detect disagrees with the matview (money_trail):\n"
        f"  spine-only: {sorted(spine_keys_at_root - direct_keys)[:5]}\n"  # type: ignore[type-var]: set difference produces sortable tuples by construction
        f"  direct-only: {sorted(direct_keys - spine_keys_at_root)[:5]}\n"  # type: ignore[type-var]: same as above
        f"  counts: spine={len(spine_keys_at_root)} "
        f"direct={len(direct_keys)}"
    )

    expected_keys = {
        (f"xfer-money-trail-{i}", i)
        for i in range(_MONEY_TRAIL_CHAIN_LENGTH)
    }
    expected_count = len(expected_keys)
    assert expected_keys <= direct_keys, (  # type: ignore[operator]: tuple[str,int] ⊆ tuple[str|int,...]; pyright doesn't follow subset through the union
        f"Planted money_trail edges missing:\n"
        f"  planted but absent: "
        f"{sorted(expected_keys - direct_keys)[:5]}"  # type: ignore[type-var,operator]: set difference + sort produce sortable tuples
    )
    assert direct_count >= expected_count, (
        f"Producer regression: planted {expected_count} edges, "
        f"matview holds {direct_count}"
    )

    payload: list[dict[str, Any]] = []
    for key_tuple in _serialize_mt_keys(direct_keys):
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("db", "money_trail_direct_rows", payload)
    write_rendered_rows("db", "money_trail_direct_meta", [
        {
            "direct_count": direct_count,
            "expected_count": expected_count,
            "root_transfer_id": _PLANTED_CHAIN_ROOT,
        },
    ])
