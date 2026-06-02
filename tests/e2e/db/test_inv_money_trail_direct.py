"""CB.5 stage 2 — DB-tier producer: direct matview SELECT for L2 money_trail.

Decomposed from `tests/e2e/test_inv_dashboard_agreement.py`'s
`test_invariant_three_way_agreement[money_trail]` cell.

CB.7 (2026-06-02): swapped hand-rolled `isolated_inv_cfg` for the
canonical `db_cfg` fixture (tests/e2e/db/conftest.py). The
per-worker isolation makes the xdist_group pinning unnecessary —
each worker seeds its own prefix in parallel; no DROP CASCADE race.
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

# noqa: E402 — post-skip imports
from recon_gen.common.spine import (  # noqa: E402
    MoneyTrailInvariant,
    Violation,
)
from tests.audit._matview_extract import (  # noqa: E402
    count_money_trail_matview_rows,
    distinct_money_trail_roots,
    money_trail_matview_row_keys,
)
from tests._marks import writes  # noqa: E402
from tests.e2e._agreement import write_rendered_rows  # noqa: E402
from tests.e2e._agreement_helpers import (  # noqa: E402
    l2_yaml_for_test,
    today_anchor,
)

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.spine.money_trail import MoneyTrailGenerator


# CB.7 — xdist_group dropped: `db_cfg` gives each worker its own prefix,
# so concurrent module fixtures don't race.
pytestmark = [pytest.mark.e2e]


_TODAY = today_anchor()
_INSTANCE: L2Instance = load_instance(l2_yaml_for_test())

# 3-deep chain — exercises depths 0/1/2 of the recursive walk.
_MONEY_TRAIL_CHAIN_LENGTH = 3
_MONEY_TRAIL_AMOUNT = 100.0

# Deterministic root — the MoneyTrailGenerator's transfer-id scheme
# is `xfer-money-trail-{index}`; root is `xfer-money-trail-0`.
_PLANTED_CHAIN_ROOT = "xfer-money-trail-0"


def _plant_anchor_day() -> date:
    return _TODAY - timedelta(days=2)


def _build_money_trail_generator(
    cfg: "Config", anchor_day: date,
) -> "MoneyTrailGenerator":
    gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger",
        chain_length=_MONEY_TRAIL_CHAIN_LENGTH,
        amount=_MONEY_TRAIL_AMOUNT,
        anchor_day=anchor_day,
        instance=_INSTANCE,
    )
    gen.prefix = cfg.db_table_prefix
    return gen


@pytest.fixture(scope="module")
def seeded_l2_db(db_cfg: "Config") -> None:
    """Apply schema + broad seed + money_trail spine plants + matview
    refresh. CB.7 — uses canonical `db_cfg` per-worker isolation."""
    from tests.e2e._seed_helpers import apply_db_seed

    conn = connect_demo_db(db_cfg)
    try:
        apply_db_seed(
            conn, _INSTANCE,
            prefix=db_cfg.db_table_prefix,
            mode="l1_plus_broad",
            today=_TODAY,
            dialect=db_cfg.dialect,
            include_baseline=False,
        )
        anchor = _plant_anchor_day()
        mt_gen = _build_money_trail_generator(db_cfg, anchor)
        mt_gen.emit(conn)
        conn.commit()
        refresh_sql = refresh_matviews_sql(
            _INSTANCE,
            prefix=db_cfg.db_table_prefix,
            dialect=db_cfg.dialect,
        )
        with conn.cursor() as cur:
            execute_script(
                cur, refresh_sql, dialect=db_cfg.dialect,
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db_conn(db_cfg: "Config") -> "Iterator[Any]":
    conn = connect_demo_db(db_cfg)
    try:
        yield conn
    finally:
        conn.close()


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


def _serialize_keys(keys: "set[tuple[Any, ...]]") -> list[list[Any]]:
    return sorted([list(t) for t in keys])


@writes()
def test_money_trail_direct_extract(
    seeded_l2_db: None,
    db_conn: Any,
    db_cfg: "Config",
) -> None:
    """Direct root-filtered matview SELECT + spine `detect()` for
    money_trail. Writes both as artifacts for the validator.

    Producer-side assertion (AT.5.a contract): spine == direct
    when filtered to the planted root. The detector returns every
    edge across every chain; the dashboard shows one chain at a
    time per the analyst's root pick.
    """
    _ = seeded_l2_db
    prefix = db_cfg.db_table_prefix

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
    root_edge_keys = direct_keys  # already root-filtered
    spine_keys_at_root = spine_keys & root_edge_keys  # type: ignore[operator]: tuple[str,int] & tuple[str,int]

    assert spine_keys_at_root == direct_keys, (
        f"Spine.detect disagrees with the matview (money_trail):\n"
        f"  spine-only: {sorted(spine_keys_at_root - direct_keys)[:5]}\n"  # type: ignore[type-var]: set difference produces sortable tuples by construction
        f"  direct-only: {sorted(direct_keys - spine_keys_at_root)[:5]}\n"  # type: ignore[type-var]: same as above
        f"  counts: spine={len(spine_keys_at_root)} "
        f"direct={len(direct_keys)}"
    )

    # Producer-side lower bound — planted chain edges are exactly
    # `_MONEY_TRAIL_CHAIN_LENGTH` (depth 0..chain_length-1).
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
    for key_tuple in _serialize_keys(direct_keys):
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("db", "money_trail_direct_rows", payload)
    write_rendered_rows("db", "money_trail_direct_meta", [
        {
            "direct_count": direct_count,
            "expected_count": expected_count,
            "root_transfer_id": _PLANTED_CHAIN_ROOT,
        },
    ])
