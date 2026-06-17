"""DR.1.c — Supersession Audit detection: behavioral exact-set coverage.

The Supersession Audit "flagged everything" because both halves of the
detection mis-used ``entry``, which is a GLOBAL insert-order serial
(BIGSERIAL / nextval — reset only at seed start), NOT per-logical-id:

  - DR.1.a (over-FLAG): ``l1_supersession_no_reason`` used ``entry > 1``
    (true for ~every row) and flagged the ORIGINAL row of every trail.
  - DR.1.b (over-SELECT): ``COUNT(*) OVER (PARTITION BY id) > 1`` selected
    any repeated ``id``. ``densify_scenario`` replicas reuse one ``id`` (the
    spine id omits the day) with NO ``supersedes``, so 5 replicas trip it —
    77 baseline rows where only 10 were a genuine supersession.

This proves the BEHAVIOR (the production dataset SQL run on DuckDB) against
synthetic rows: a genuine trail, a phantom densified-plant id-collision, and
a single un-superseded posting. It FAILS on the pre-DR.1 SQL (the phantom
leaks into the select; the original row flags no-reason) and passes on the
fix — closing the 4-way-gate blind spot (all three renderers over-selected
identically, so the agreement gate passed while all three were wrong).

DuckDB-executed (the dialect we can run without a container); the
dialect-invariant SQL fragments are pinned at the json tier
(``tests/json/test_l1_dashboard.py``).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import date
from typing import TYPE_CHECKING

import duckdb
import pytest

from recon_gen.apps.l1_dashboard.datasets import (
    L1_ALL_SENTINEL,
    build_supersession_transactions_dataset,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg

if TYPE_CHECKING:
    from recon_gen.common.config import Config


_PREFIX = "pfx"
_DAY_ISO = date(2026, 1, 5).isoformat()

# (entry, id, supersedes). ``entry`` is a GLOBAL increasing serial across all
# ids — mirroring the real BIGSERIAL/nextval — so the per-id MIN(entry) and
# the global value diverge (which is exactly what broke the old `entry > 1`).
_TRAIL: list[tuple[int, str, str | None]] = [
    # Genuine supersession trail (id has a row carrying `supersedes`):
    (10, "tx-genuine", None),                  # original (the id's MIN entry)
    (20, "tx-genuine", "TechnicalCorrection"),  # revision WITH a reason
    (30, "tx-genuine", None),                  # revision with NO reason
    # Phantom densified-plant collision: 2 entries, NO supersedes anywhere.
    (40, "tx-phantom", None),
    (50, "tx-phantom", None),
    # Single un-superseded posting (entry_count == 1).
    (60, "tx-single", None),
]


@pytest.fixture
def supersession_duckdb() -> Iterator["Config"]:
    """DuckDB with ``<prefix>_transactions`` seeded with a genuine trail, a
    phantom id-collision (no supersedes), and a single posting — only the
    columns the supersession dataset SQL projects need to exist."""
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    conn = duckdb.connect(path)
    conn.execute(
        f"CREATE TABLE {_PREFIX}_transactions ("
        "  entry BIGINT, id TEXT, supersedes TEXT,"
        "  account_id TEXT, account_name TEXT,"
        "  transfer_id TEXT, rail_name TEXT,"
        "  amount_money BIGINT, amount_direction TEXT,"
        "  status TEXT, posting TIMESTAMP, bundle_id TEXT"
        ")"
    )
    rows = [
        (
            entry, tid, sup, "acc-1", "Account 1",
            f"xfer-{entry}", "SomeRail",
            100_00, "Credit", "Posted", f"{_DAY_ISO} 12:00:00", None,
        )
        for entry, tid, sup in _TRAIL
    ]
    conn.executemany(
        f"INSERT INTO {_PREFIX}_transactions VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(
        db_dialect=Dialect.DUCKDB, db_url=path, db_table_prefix=_PREFIX,
    )
    try:
        yield cfg
    finally:
        os.unlink(path)


def _run_supersession_audit(cfg: "Config") -> list[dict[str, object]]:
    """Run the production Supersession Audit transactions dataset SQL with
    the supersedes-reason picker at its show-all sentinel (match-all)."""
    ds = build_supersession_transactions_dataset(cfg, default_l2_instance())
    physical = next(iter(ds.PhysicalTableMap.values()))
    assert physical.CustomSql is not None
    sql = physical.CustomSql.SqlQuery
    assert sql is not None
    return query_db_via_cfg(
        cfg, sql, binds={"param_pL1SupersedeReason": L1_ALL_SENTINEL},
    )


def test_dr1b_select_narrows_to_genuine_supersessions(
    supersession_duckdb: "Config",
) -> None:
    """DR.1.b — the audit selects ONLY trails that contain a real
    supersession. The phantom densified-plant id-collision (no supersedes)
    and the single un-superseded posting are filtered out (the 77→10 fix)."""
    rows = _run_supersession_audit(supersession_duckdb)
    selected_ids = {str(r["transaction_id"]) for r in rows}
    assert selected_ids == {"tx-genuine"}, (
        f"DR.1.b regression: expected only the genuine supersession trail, "
        f"got {sorted(selected_ids)}. A phantom id-collision (entry_count>1 "
        f"but no supersedes) or a single posting leaked into the audit."
    )
    assert len(rows) == 3  # the genuine trail's full 3-entry history


def test_dr1a_no_reason_flag_uses_per_id_min_entry(
    supersession_duckdb: "Config",
) -> None:
    """DR.1.a — the no-reason flag is computed against the id's OWN minimum
    entry, not ``entry > 1``: 0 for the original (min-entry) row, 0 for a
    revision that carries a reason, 1 for a revision with no reason."""
    rows = _run_supersession_audit(supersession_duckdb)
    # The "entry" / "l1_supersession_no_reason" keys are the supersession
    # dataset's projected result-dict columns (contract-defined via
    # SUPERSESSION_TRANSACTIONS_CONTRACT), independent of migrate_mark's
    # private _ROW_ID_COLUMN choice — hence the contract-independence allowlist.
    flag_by_entry = {
        int(str(r["entry"])): int(str(r["l1_supersession_no_reason"]))  # typing-smell: ignore[no-inline-production-constants]: dataset result-dict col
        for r in rows
    }
    assert flag_by_entry == {10: 0, 20: 0, 30: 1}, (
        f"DR.1.a regression: expected {{10:0 (original), 20:0 (with-reason), "
        f"30:1 (no-reason)}}, got {flag_by_entry}. The old `entry > 1` would "
        f"flag entry 10 (the original) as a no-reason supersession."
    )
