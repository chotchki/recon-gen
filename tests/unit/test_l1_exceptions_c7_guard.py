"""BV.3.3.c.bug5 — L1 Exceptions dataset SQL C7 guard regression gate.

The C7 cold-read guard (v11.26.1) was originally
``(magnitude_amount > 0) OR (magnitude_count > 0)`` — written to drop
degenerate rows where a money-keyed branch surfaced
``magnitude_amount = 0 AND magnitude_count = 0``. The shape worked for
money branches but accidentally dropped transfer-keyed *_missed
variants (``xor_group_missed`` / ``multi_xor_missed`` /
``fan_in_missing_parent``) — where ``magnitude_count = 0`` IS the
violation signal ("zero XOR siblings fired when one should have").

BV.3.3.c.bug5 (2026-06-10) relaxed the guard for transfer-keyed
branches by adding ``transfer_id IS NOT NULL`` as a third OR clause.
Money-keyed branches still emit ``transfer_id = NULL`` and remain
gated on the magnitude-positive condition. Transfer-keyed branches
emit a real ``transfer_id`` and the matview's own SELECT predicate
already gates on the cardinality anomaly — so trusting the row is
sound.

This test runs the actual dataset SQL against a synthetic DuckDB
``<prefix>_l1_exceptions`` populated with four shapes:

  * Money-keyed + non-zero magnitude — surfaces (control).
  * Money-keyed + magnitude_amount = 0 + magnitude_count = 0 —
    suppressed (the original C7 intent).
  * Transfer-keyed + magnitude_count = 0 (a *_missed violation) —
    SURFACES post-bug5 (would be suppressed pre-bug5).
  * Transfer-keyed + magnitude_count > 0 (a *_overlap violation) —
    surfaces (sanity).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from typing import TYPE_CHECKING

import duckdb
import pytest

from recon_gen.apps.l1_dashboard.datasets import build_l1_exceptions_dataset
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config

if TYPE_CHECKING:
    from recon_gen.common.config import Config


_PREFIX = "pfx"


def _extract_sql(cfg: "Config") -> str:
    """Pull the rendered CustomSql query out of the L1 Exceptions
    DataSet built against ``cfg``. The dataset's PhysicalTableMap has
    exactly one CustomSql entry — yank ``SqlQuery`` from it.
    """
    ds = build_l1_exceptions_dataset(cfg, default_l2_instance())
    tables = list(ds.PhysicalTableMap.values())
    assert len(tables) == 1, (
        f"expected 1 PhysicalTable, got {len(tables)}"
    )
    cs = tables[0].CustomSql
    assert cs is not None, "PhysicalTable.CustomSql is None"
    return cs.SqlQuery


@pytest.fixture
def planted_l1_exceptions_duckdb() -> Iterator["Config"]:
    """Synthetic ``<prefix>_l1_exceptions`` table with four rows
    exercising the C7 guard surface area. We don't go through the
    matview path — we plant the projected shape directly.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    conn = duckdb.connect(path)
    # Table shape mirrors the matview projection (see
    # common/l2/schema.py::3563-3710). For test purposes we use
    # nullable columns + integer magnitudes; the dataset SQL wraps
    # magnitude_amount in cents_to_dollars but we plant cents-level
    # values that satisfy ``> 0`` either way.
    conn.execute(
        f"CREATE TABLE {_PREFIX}_l1_exceptions ("
        "  check_type TEXT,"
        "  account_id TEXT,"
        "  account_name TEXT,"
        "  account_role TEXT,"
        "  account_parent_role TEXT,"
        "  business_day DATE,"
        "  rail_name TEXT,"
        "  transfer_id TEXT,"
        "  magnitude_amount BIGINT,"
        "  magnitude_count INTEGER"
        ")"
    )
    # Anchor business_day inside the L1 universal-range default window
    # (the dataset's date range clause is parameterized; we plant a
    # day comfortably inside the typical 30-day picker default).
    conn.executemany(
        f"INSERT INTO {_PREFIX}_l1_exceptions VALUES "
        "(?,?,?,?,?,?,?,?,?,?)",
        [
            # Money-keyed, non-zero magnitude — surfaces.
            (
                "drift", "acc-real", "Acc Real", "control_account",
                None, "2030-01-01", None, None, 500000, None,
            ),
            # Money-keyed, magnitude_amount=0, magnitude_count=0/NULL —
            # the original C7 target. Should be suppressed (no
            # actionable violation surface).
            (
                "drift", "acc-degenerate", "Acc Degen", "control_account",
                None, "2030-01-01", None, None, 0, None,
            ),
            # Transfer-keyed, magnitude_count=0 (xor_group_missed: zero
            # XOR siblings fired). Post-bug5 MUST surface. Pre-bug5
            # would be dropped — that's the regression we guard.
            (
                "xor_group_violation", None, None, None, None,
                "2030-01-01", "template-xor-A", "xfer-missed-001",
                None, 0,
            ),
            # Transfer-keyed, magnitude_count>0 (xor_group_overlap: 2
            # siblings fired). Sanity — surfaces under both guard
            # shapes.
            (
                "xor_group_violation", None, None, None, None,
                "2030-01-01", "template-xor-B", "xfer-overlap-002",
                None, 2,
            ),
        ],
    )
    conn.commit()
    conn.close()
    from recon_gen.common.config import DbConfig  # noqa: PLC0415
    cfg = make_test_config(
        db=DbConfig(
            table_prefix=_PREFIX,
            dialect=Dialect.DUCKDB,
            url=path,
        ),
    )
    try:
        yield cfg
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _substitute_default_params(sql: str) -> str:
    """The dataset SQL contains ``<<$paramName>>`` placeholders that
    QS substitutes at fetch time. For local DuckDB execution we
    substitute the sentinel defaults manually — the ``L1ALL`` sentinel
    on the categorical pickers (match-all by construction) + the
    universal date range that brackets the planted business_day.
    """
    # Categorical sentinel: each ``<<$pX>>`` lands inside a
    # ``('L1ALL' IN (<<$pX>>) OR col IN (<<$pX>>))`` shape; passing
    # 'L1ALL' satisfies the match-all guard.
    out = sql
    # Replace every ``<<$...>>`` with the sentinel string literal.
    # Conservative: works because every categorical filter uses the
    # same sentinel + date filters use date-typed placeholders. The
    # date range is wide enough to bracket 2030-01-01.
    import re
    # Categorical params (pL1Todays*) — quote-wrap the match-all
    # sentinel (``L1_ALL_SENTINEL = '__l1_all__'`` per
    # apps/l1_dashboard/datasets.py).
    out = re.sub(
        r"<<\$pL1TodaysExc[A-Za-z]+>>", "'__l1_all__'", out,
    )
    # Date range params (pL1Date*) — wide window bracketing 2030-01-01.
    out = re.sub(
        r"<<\$pL1DateStart>>", "DATE '2029-01-01'", out,
    )
    out = re.sub(
        r"<<\$pL1DateEnd>>", "DATE '2031-01-01'", out,
    )
    return out


def test_c7_guard_keeps_money_keyed_actionable_row(
    planted_l1_exceptions_duckdb: "Config",
) -> None:
    """Sanity: a money-keyed branch with magnitude_amount > 0 surfaces
    under the (post-bug5) C7 guard. Catches an over-aggressive
    refactor that broke the money-actionable surface.
    """
    cfg = planted_l1_exceptions_duckdb
    sql = _substitute_default_params(_extract_sql(cfg))
    db_url = cfg.db.url
    assert db_url is not None
    conn = duckdb.connect(db_url)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    account_ids = {row[1] for row in rows}
    assert "acc-real" in account_ids, (
        f"money-keyed actionable row was dropped by the guard: "
        f"got account_ids={account_ids!r}"
    )


def test_c7_guard_drops_money_keyed_degenerate_row(
    planted_l1_exceptions_duckdb: "Config",
) -> None:
    """REGRESSION GATE on the original C7 intent: a money-keyed branch
    row with magnitude_amount = 0 AND magnitude_count IS NULL must be
    suppressed. The bug5 relaxation must NOT widen the guard for
    money-keyed branches — only transfer-keyed ones.
    """
    cfg = planted_l1_exceptions_duckdb
    sql = _substitute_default_params(_extract_sql(cfg))
    db_url = cfg.db.url
    assert db_url is not None
    conn = duckdb.connect(db_url)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    account_ids = {row[1] for row in rows}
    assert "acc-degenerate" not in account_ids, (
        f"money-keyed degenerate row (mag_amount=0, mag_count=NULL, "
        f"transfer_id=NULL) leaked through the C7 guard — the bug5 "
        f"relaxation must NOT widen the guard for money-keyed branches. "
        f"got account_ids={account_ids!r}"
    )


def test_c7_guard_keeps_transfer_keyed_missed_violation(
    planted_l1_exceptions_duckdb: "Config",
) -> None:
    """BV.3.3.c.bug5 PRIMARY ASSERTION — a transfer-keyed row with
    magnitude_count = 0 (xor_group_missed: zero XOR siblings fired)
    MUST surface. Pre-bug5 the C7 guard dropped this row; post-bug5
    the ``transfer_id IS NOT NULL`` OR clause keeps it.
    """
    cfg = planted_l1_exceptions_duckdb
    sql = _substitute_default_params(_extract_sql(cfg))
    db_url = cfg.db.url
    assert db_url is not None
    conn = duckdb.connect(db_url)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    transfer_ids = {row[7] for row in rows}
    assert "xfer-missed-001" in transfer_ids, (
        f"transfer-keyed *_missed row (magnitude_count=0) was dropped "
        f"by the C7 guard — the bug5 transfer_id-NOT-NULL relaxation "
        f"did not land. got transfer_ids={transfer_ids!r}"
    )


def test_c7_guard_keeps_transfer_keyed_overlap_violation(
    planted_l1_exceptions_duckdb: "Config",
) -> None:
    """Sanity twin: a transfer-keyed row with magnitude_count > 0
    (xor_group_overlap: 2 siblings fired) surfaces under both pre- and
    post-bug5 guard shapes. Confirms we didn't accidentally invert the
    discriminator.
    """
    cfg = planted_l1_exceptions_duckdb
    sql = _substitute_default_params(_extract_sql(cfg))
    db_url = cfg.db.url
    assert db_url is not None
    conn = duckdb.connect(db_url)
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    transfer_ids = {row[7] for row in rows}
    assert "xfer-overlap-002" in transfer_ids, (
        f"transfer-keyed *_overlap row (magnitude_count=2) was "
        f"dropped — the guard inverted? "
        f"got transfer_ids={transfer_ids!r}"
    )
