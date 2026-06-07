"""Unit tests for the public ETL helpers (common/etl.py).

Verifies write_daily_balance:
- builds dialect-correct SQL (PG bare-quoted vs Oracle/DuckDB
  TIMESTAMP literal),
- round-trips through DuckDB :memory:,
- handles optional fields (expected_eod_balance / metadata / offset).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb  # type: ignore[import-untyped]  # WHY: duckdb ships partial type info; we use only execute/fetchall here
import pytest

from recon_gen.common.db import open_demo_db
from recon_gen.common.etl import write_daily_balance
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.sql.literals import (
    render_sql_literal,
    sql_timestamp_literal,
    strip_tz_offset,
)
from tests._test_helpers import make_test_config


@dataclass(frozen=True)
class _Account:
    """Minimal _AccountLike fixture for the structural Protocol."""
    id: str
    name: str
    role: str
    scope: str
    parent_role: str | None


_DDL = """
CREATE TABLE test_daily_balances (
    account_id VARCHAR, account_name VARCHAR,
    account_role VARCHAR, account_scope VARCHAR,
    account_parent_role VARCHAR,
    expected_eod_balance BIGINT,
    business_day_start TIMESTAMP, business_day_end TIMESTAMP,
    money BIGINT, metadata VARCHAR, supersedes VARCHAR
)
"""


def _fixture_account() -> _Account:
    return _Account(
        id="acct-cust-0001", name="Customer #0001",
        role="CustomerDDA", scope="internal", parent_role="DDAControl",
    )


# ---------------------------------------------------------------------------
# render_sql_literal — pure dialect-aware renderer
# ---------------------------------------------------------------------------


def test_render_sql_literal_null() -> None:
    assert render_sql_literal(None, Dialect.POSTGRES) == "NULL"
    assert render_sql_literal(None, Dialect.ORACLE) == "NULL"
    assert render_sql_literal(None, Dialect.DUCKDB) == "NULL"


def test_render_sql_literal_int() -> None:
    assert render_sql_literal(187500, Dialect.POSTGRES) == "187500"
    assert render_sql_literal(-1, Dialect.ORACLE) == "-1"


def test_render_sql_literal_string_escapes_quotes() -> None:
    assert render_sql_literal("O'Brien", Dialect.POSTGRES) == "'O''Brien'"
    assert render_sql_literal("plain", Dialect.DUCKDB) == "'plain'"


def test_render_sql_literal_timestamp_pg_vs_oracle() -> None:
    pg = render_sql_literal("2026-06-07T00:00:00", Dialect.POSTGRES, is_timestamp=True)
    assert pg == "'2026-06-07T00:00:00'"
    ora = render_sql_literal("2026-06-07T00:00:00", Dialect.ORACLE, is_timestamp=True)
    assert ora == "TIMESTAMP '2026-06-07 00:00:00'"


def test_render_sql_literal_rejects_float() -> None:
    with pytest.raises(TypeError, match="float"):
        render_sql_literal(1.5, Dialect.POSTGRES)


def test_render_sql_literal_rejects_bool() -> None:
    with pytest.raises(TypeError, match="bool"):
        render_sql_literal(True, Dialect.POSTGRES)


def test_strip_tz_offset_handles_z_and_offset() -> None:
    assert strip_tz_offset("2026-06-07T00:00:00Z") == "2026-06-07T00:00:00"
    assert strip_tz_offset("2026-06-07T00:00:00+05:00") == "2026-06-07T00:00:00"
    assert strip_tz_offset("2026-06-07T00:00:00") == "2026-06-07T00:00:00"


def test_sql_timestamp_literal_strips_offset_for_oracle() -> None:
    ora = sql_timestamp_literal("2026-06-07T12:30:00+05:00", Dialect.ORACLE)
    assert ora == "TIMESTAMP '2026-06-07 12:30:00'"


# ---------------------------------------------------------------------------
# write_daily_balance — roundtrip via DuckDB :memory:
# ---------------------------------------------------------------------------


def test_write_daily_balance_roundtrip_minimum_fields() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(_DDL)
        write_daily_balance(
            conn, Dialect.DUCKDB,
            prefix="test",
            account=_fixture_account(),
            business_day=date(2026, 6, 7),
            balance_dollars="1875.00",
        )
        rows = conn.execute(
            "SELECT account_id, account_name, account_role, "
            "account_scope, account_parent_role, expected_eod_balance, "
            "business_day_start, business_day_end, money, metadata, "
            "supersedes FROM test_daily_balances",
        ).fetchall()
        assert rows is not None and len(rows) == 1
        row = rows[0]
        assert row is not None
        assert row[0] == "acct-cust-0001"
        assert row[1] == "Customer #0001"
        assert row[2] == "CustomerDDA"
        assert row[3] == "internal"
        assert row[4] == "DDAControl"
        assert row[5] is None  # expected_eod_balance defaults None
        assert str(row[6]) == "2026-06-07 00:00:00"
        assert str(row[7]) == "2026-06-08 00:00:00"
        assert row[8] == 187500  # $1875.00 = 187500 cents
        assert row[9] is None
        assert row[10] is None  # supersedes
    finally:
        conn.close()


def test_write_daily_balance_optional_fields() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(_DDL)
        write_daily_balance(
            conn, Dialect.DUCKDB,
            prefix="test",
            account=_fixture_account(),
            business_day=date(2026, 6, 7),
            balance_dollars=Decimal("250.00"),
            expected_eod_dollars=Decimal("1000.00"),
            metadata={"limits": {"FeeCap": 50.0}, "scenario_id": "demo-1"},
            offset_hours=17,
            supersedes="bal-prior-001",
        )
        row = conn.execute(
            "SELECT expected_eod_balance, business_day_start, "
            "business_day_end, money, metadata, supersedes "
            "FROM test_daily_balances",
        ).fetchone()
        assert row is not None
        assert row[0] == 100000  # $1000 = 100000 cents
        assert str(row[1]) == "2026-06-07 17:00:00"
        assert str(row[2]) == "2026-06-08 17:00:00"
        assert row[3] == 25000  # $250 = 25000 cents
        # metadata JSON sorted by key (sort_keys=True in the helper)
        assert row[4] == '{"limits": {"FeeCap": 50.0}, "scenario_id": "demo-1"}'
        assert row[5] == "bal-prior-001"
    finally:
        conn.close()


def test_write_daily_balance_null_parent_role() -> None:
    """L2Instance accounts can carry parent_role=None for top-level
    singletons (e.g. CustomerLedger itself). Helper renders NULL
    correctly."""
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(_DDL)
        write_daily_balance(
            conn, Dialect.DUCKDB,
            prefix="test",
            account=_Account(
                id="acct-ledger", name="Customer Ledger",
                role="CustomerLedger", scope="internal", parent_role=None,
            ),
            business_day=date(2026, 6, 7),
            balance_dollars="0.00",
        )
        row = conn.execute(
            "SELECT account_parent_role, money FROM test_daily_balances",
        ).fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] == 0
    finally:
        conn.close()


def test_write_daily_balance_money_precision() -> None:
    """Sub-cent precision rounds at Cents.from_dollars boundary
    (Decimal quantization, no float dust)."""
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(_DDL)
        write_daily_balance(
            conn, Dialect.DUCKDB,
            prefix="test",
            account=_fixture_account(),
            business_day=date(2026, 6, 7),
            balance_dollars="0.01",
        )
        row = conn.execute(
            "SELECT money FROM test_daily_balances",
        ).fetchone()
        assert row is not None
        assert row[0] == 1  # $0.01 = 1 cent
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# open_demo_db — context-managed connection (commit/rollback/close)
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_cfg() -> object:
    """A Config pointing at a fresh DuckDB file under tempfile.

    Uses the SQLAlchemy-style triple-slash form (`duckdb:///abs/path`)
    that `duckdb_path` parses; the fourth slash starts the absolute
    path component.
    """
    import os
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    Path(path).unlink()  # delete the empty file; let DuckDB create it
    # runner.py uses `f"duckdb:///{abs_path}"` — three literal slashes
    # before the leading-slash path → four-slash URL that `duckdb_path`
    # strips back to the absolute path. Two-slash form would resolve
    # relative to cwd.
    return make_test_config(
        dialect=Dialect.DUCKDB,
        demo_database_url=f"duckdb:///{path}",
    )


def _duckdb_path_of(cfg: object) -> str:
    raw = getattr(cfg, "demo_database_url")  # noqa: B009 — explicit getattr for pyright; cfg typed as object
    assert isinstance(raw, str)
    from recon_gen.common.db import duckdb_path
    return duckdb_path(raw)


def _ensure_kv_table(cfg: object) -> None:
    """Bootstrap a tiny kv(k, v) table on the cfg's DuckDB file so the
    commit/rollback tests have something to observe."""
    conn = duckdb.connect(_duckdb_path_of(cfg))
    try:
        conn.execute("CREATE TABLE kv (k VARCHAR, v INTEGER)")
        conn.commit()
    finally:
        conn.close()


def test_open_demo_db_commits_on_success(duckdb_cfg: object) -> None:
    """Happy path: writes inside the `with` block survive after exit."""
    _ensure_kv_table(duckdb_cfg)
    with open_demo_db(duckdb_cfg) as conn:  # type: ignore[arg-type]: duckdb_cfg fixture returns object typed as structural Config
        cur = conn.cursor()
        cur.execute("INSERT INTO kv VALUES ('committed', 42)")
        cur.close()
    # Re-open and verify the row landed.
    with open_demo_db(duckdb_cfg) as conn:  # type: ignore[arg-type]: duckdb_cfg fixture returns object typed as structural Config
        cur = conn.cursor()
        cur.execute("SELECT v FROM kv WHERE k = 'committed'")
        row = cur.fetchone()
        cur.close()
    assert row is not None
    assert row[0] == 42


def test_open_demo_db_propagates_exception_and_closes(duckdb_cfg: object) -> None:
    """Exception path: the helper re-raises the exception AND closes
    the connection cleanly (no double-close, no swallowed errors).

    The helper's rollback is best-effort on DuckDB (per-cursor
    autocommit means writes already landed by the time the
    exception fires — see `open_demo_db` docstring). The
    transactional guarantee is PG/Oracle only; this test pins
    the exception-propagation + always-close contract that holds
    across every dialect.
    """
    _ensure_kv_table(duckdb_cfg)
    with pytest.raises(RuntimeError, match="boom"), \
            open_demo_db(duckdb_cfg) as conn:  # type: ignore[arg-type]: duckdb_cfg fixture returns object typed as structural Config
        cur = conn.cursor()
        cur.execute("INSERT INTO kv VALUES ('autocommitted', 99)")
        cur.close()
        raise RuntimeError("boom")
    # Helper closed cleanly; we can reopen and read state.
    with open_demo_db(duckdb_cfg) as conn:  # type: ignore[arg-type]: duckdb_cfg fixture returns object typed as structural Config
        cur = conn.cursor()
        cur.execute("SELECT v FROM kv WHERE k = 'autocommitted'")
        row = cur.fetchone()
        cur.close()
    assert row is not None
    # DuckDB autocommit: the row stuck around. PG/Oracle would have
    # rolled back. The helper's contract is exception-propagation +
    # always-close, not strict-rollback across dialects.
    assert row[0] == 99


def test_open_demo_db_closes_connection(duckdb_cfg: object) -> None:
    """After exit, the connection's closed — operations raise."""
    _ensure_kv_table(duckdb_cfg)
    with open_demo_db(duckdb_cfg) as conn:  # type: ignore[arg-type]: duckdb_cfg fixture returns object typed as structural Config
        captured = conn
    # DuckDB raises ConnectionException after close.
    with pytest.raises(Exception):  # noqa: PT011, BLE001 — DuckDB's exception class isn't part of any stable surface
        captured.cursor().execute("SELECT 1")
