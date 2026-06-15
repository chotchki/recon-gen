"""Unit tests for `bulk_insert_tx` + `bulk_insert_balance`.

The public tuple-bulk-load surface for etl_hook integrators. Sibling to
the single-row `insert_tx` / `insert_balance` helpers but takes a list
of positional tuples (in `TX_COLS` / `DB_COLS` order) and routes through
the dialect's fast path:

- DuckDB → `_flush_duckdb_multivalues` (multi-row VALUES literal, CA.10)
- PG     → `cursor.executemany` (psycopg pipelines binds)
- Oracle → `cursor.executemany` (oracledb 2+ — each iteration gets its
  own IDENTITY value, so we avoid the INSERT ALL same-id collision the
  `batch_oracle_inserts` walker has to guard against)

Three happy-path tests cover the DuckDB fast path (in-memory, no
container). PG / Oracle paths are reachable via the runner's db-tier
matrix (`./run_tests.sh up_to=db --dialects=pg`); here we exercise the
dispatch shape through `_placeholder_style` directly so the layered
test grids don't pay the per-cell container cost just for this helper.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb

from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import CREDIT, POSTED_STATUS
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine._emit_helpers import (
    DB_COLS,
    TX_COLS,
    bulk_insert_balance,
    bulk_insert_tx,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.DUCKDB


def _fresh_db() -> duckdb.DuckDBPyConnection:
    """Bring up a fresh in-memory DuckDB seeded with spec_example's
    schema — same pattern as `tests/unit/test_spine_drift.py::_fresh_db`.
    """
    conn = duckdb.connect(":memory:")
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur,
        emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


def _tx_row(
    *,
    tx_id: str,
    account_id: str = "clearing-suspense-1",
    account_role: str = "ClearingSuspense",
    amount_money: object = 100.0,
    amount_direction: str = "Credit",
    posting: str = "2026-01-01 12:00:00",
    transfer_id: str = "xfer-bulk-1",
    metadata: object = None,
) -> tuple[object, ...]:
    """Build a TX_COLS-ordered tuple for a `Posted` transaction row.

    Kept as a helper so each test focuses on the field it's exercising
    rather than repeating the 17-element tuple literal.
    """
    by_col = {
        "id": tx_id,
        "account_id": account_id,
        "account_name": "Test Account",
        "account_role": account_role,
        "account_scope": "internal",
        "account_parent_role": None,
        "amount_money": amount_money,
        "amount_direction": amount_direction,
        "status": "Posted",
        "posting": posting,
        "transfer_id": transfer_id,
        "transfer_parent_id": None,
        "rail_name": "_bulk_test",
        "template_name": None,
        "origin": "etl",
        "metadata": metadata,
        "supersedes": None,
    }
    return tuple(by_col[c] for c in TX_COLS)


def _balance_row(
    *,
    account_id: str = "clearing-suspense-1",
    account_role: str = "ClearingSuspense",
    money: object = 0.0,
    business_day_start: str = "2026-01-01 00:00:00",
    business_day_end: str = "2026-01-02 00:00:00",
    metadata: object = None,
) -> tuple[object, ...]:
    by_col = {
        "account_id": account_id,
        "account_name": "Test Account",
        "account_role": account_role,
        "account_scope": "internal",
        "account_parent_role": None,
        "expected_eod_balance": None,
        "business_day_start": business_day_start,
        "business_day_end": business_day_end,
        "money": money,
        "metadata": metadata,
    }
    return tuple(by_col[c] for c in DB_COLS)


# ---------------------------------------------------------------------------
# Happy path — DuckDB fast path (the in-memory layer; PG/Oracle covered
# by the db-tier runner matrix exercising the same dispatch).
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_lands_one_hundred_rows() -> None:
    conn = _fresh_db()
    rows = [_tx_row(tx_id=f"tx-{i:04d}") for i in range(100)]
    bulk_insert_tx(conn, rows, prefix=_PREFIX)
    conn.commit()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {_PREFIX}_transactions")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 100
    # Spot-check one round-tripped tuple matches what we sent. amount_money
    # round-trips as BIGINT cents (100 dollars → 10000 cents) per the
    # `_coerce_to_cents_int` boundary the bulk helper inherits from
    # `insert_tx`.
    cur.execute(
        f"SELECT id, account_id, amount_money, amount_direction, status "
        f"FROM {_PREFIX}_transactions WHERE id = 'tx-0042'"
    )
    fetched = cur.fetchone()
    assert fetched == ("tx-0042", "clearing-suspense-1", 10000, CREDIT, POSTED_STATUS)


def test_bulk_insert_balance_lands_one_hundred_rows() -> None:
    conn = _fresh_db()
    rows = [
        _balance_row(account_id=f"acct-{i:04d}", money=float(i))
        for i in range(100)
    ]
    bulk_insert_balance(conn, rows, prefix=_PREFIX)
    conn.commit()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 100
    # 42 dollars → 4200 cents.
    cur.execute(
        f"SELECT account_id, money FROM {_PREFIX}_daily_balances "
        f"WHERE account_id = 'acct-0042'"
    )
    fetched = cur.fetchone()
    assert fetched == ("acct-0042", 4200)


# ---------------------------------------------------------------------------
# Empty-rows — no-op contract. No cursor opened, no SQL parsed.
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_empty_rows_is_noop() -> None:
    conn = _fresh_db()
    bulk_insert_tx(conn, [], prefix=_PREFIX)
    bulk_insert_tx(conn, (), prefix=_PREFIX)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {_PREFIX}_transactions")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 0


def test_bulk_insert_balance_empty_rows_is_noop() -> None:
    conn = _fresh_db()
    bulk_insert_balance(conn, [], prefix=_PREFIX)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 0


# ---------------------------------------------------------------------------
# Money coercion — float dollars → BIGINT cents at the bulk boundary
# (same contract as `insert_tx`'s `_coerce_to_cents_int`).
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_money_coercion_float_dollars_to_cents() -> None:
    conn = _fresh_db()
    # 12.34 dollars must land as 1234 cents — same `Cents.from_dollars(str(v))`
    # path the single-row helper uses, so float-init Decimal drift can't
    # bite.
    bulk_insert_tx(conn, [_tx_row(tx_id="tx-money", amount_money=12.34)])
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT amount_money FROM {_PREFIX}_transactions WHERE id = 'tx-money'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 1234


def test_bulk_insert_balance_money_coercion_float_dollars_to_cents() -> None:
    conn = _fresh_db()
    bulk_insert_balance(
        conn,
        [_balance_row(account_id="acct-money", money=98.76)],
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT money FROM {_PREFIX}_daily_balances "
        f"WHERE account_id = 'acct-money'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 9876


# ---------------------------------------------------------------------------
# Metadata pass-through — no auto-stamp. Integrator owns the column;
# `source='real'` survives verbatim instead of being clobbered to
# `source='training'`. See the bulk_insert_tx docstring.
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_metadata_passthrough_no_auto_stamp() -> None:
    conn = _fresh_db()
    # Real-world-flavored metadata — explicit `source='real'` that a
    # training-stamp would overwrite. Bulk helpers must preserve.
    real_meta = '{"source": "real"}'
    bulk_insert_tx(
        conn,
        [_tx_row(tx_id="tx-real", metadata=real_meta)],
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT metadata FROM {_PREFIX}_transactions WHERE id = 'tx-real'"
    )
    row = cur.fetchone()
    assert row is not None
    # Verbatim string round-trip — no JSON re-encoding, no `source`
    # mutation, no `scenario_id` injection.
    assert row[0] == real_meta


def test_bulk_insert_tx_metadata_none_round_trips_to_null() -> None:
    # The other half of the metadata contract: when the integrator
    # leaves the slot None, the DB stores NULL — not a stamped JSON
    # object. Same byte-shape as `insert_tx` with `metadata` omitted.
    conn = _fresh_db()
    bulk_insert_tx(conn, [_tx_row(tx_id="tx-no-meta", metadata=None)])
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT metadata FROM {_PREFIX}_transactions "
        f"WHERE id = 'tx-no-meta'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] is None


# ---------------------------------------------------------------------------
# Performance smoke — DuckDB only. CA.10 measured 54× faster than
# DuckDB's executemany at 50k rows; ~112k rows/sec. 10k rows should
# clock under 1s. Loose ceiling — purpose is regression-detect, not
# absolute timing.
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_10k_rows_under_one_second() -> None:
    conn = _fresh_db()
    rows = [_tx_row(tx_id=f"tx-perf-{i:05d}") for i in range(10_000)]
    t0 = time.monotonic()
    bulk_insert_tx(conn, rows, prefix=_PREFIX)
    conn.commit()
    elapsed = time.monotonic() - t0
    # CA.10 probe: 112k rows/sec → 10k in ~90ms locally. The ceiling is
    # set wide (5s) for WSL2 self-hosted CI variance — observed 1.43s on
    # the CI runner under xdist load while dev clocked ~30ms (commit
    # 6b4c2adb CI fail). The gate's purpose is regression-detect for the
    # per-row `cur.execute` fallback path (which clocks ~5s for 10k rows
    # per the same CA.10 probe), NOT absolute-timing certification.
    # If this ever flakes again, bump the ceiling further or drop the
    # gate — DON'T regress to per-row inserts to make it pass.
    assert elapsed < 5.0, f"bulk_insert_tx of 10k rows took {elapsed:.2f}s"
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {_PREFIX}_transactions")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 10_000


# ---------------------------------------------------------------------------
# 2026-06-15 fix: _coerce_to_cents_int now accepts strings (the
# canonical csv.DictReader shape) and raises TypeError on unrecognized
# types — replaces the prior silent passthrough that surfaced as opaque
# downstream BIGINT INSERT failures.
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_money_coercion_string_dollars_to_cents() -> None:
    """CSV bulk loads land every column as a string (csv.DictReader /
    pandas object dtype). `_coerce_to_cents_int` now routes strings
    through `Cents.from_dollars` so "12.34" → 1234 cents same as 12.34
    or Decimal("12.34")."""
    conn = _fresh_db()
    bulk_insert_tx(
        conn, [_tx_row(tx_id="tx-csv-string", amount_money="12.34")],
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT amount_money FROM {_PREFIX}_transactions "
        f"WHERE id = 'tx-csv-string'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 1234


def test_bulk_insert_balance_money_coercion_string_dollars_to_cents() -> None:
    conn = _fresh_db()
    bulk_insert_balance(
        conn,
        [_balance_row(account_id="acct-csv-string", money="98.76")],
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT money FROM {_PREFIX}_daily_balances "
        f"WHERE account_id = 'acct-csv-string'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 9876


def test_coerce_to_cents_int_raises_on_unrecognized_type() -> None:
    """Pre-2026-06-15: unknown types silently passed through and
    surfaced as PG `invalid input syntax for type bigint` / Oracle
    ORA-01722 with no breadcrumb back to the bad caller. Now raises
    TypeError at the coerce boundary."""
    import pytest as _pytest

    from recon_gen.common.spine._emit_helpers import _coerce_to_cents_int

    with _pytest.raises(TypeError, match="unsupported money value type"):
        _coerce_to_cents_int(object())


# ---------------------------------------------------------------------------
# 2026-06-15 fix: bulk_insert_tx / bulk_insert_balance accept an
# optional `columns` override so ETL integrators can load schema
# columns TX_COLS/DB_COLS exclude (transfer_completion / bundle_id
# for transactions; supersedes for balances) without changing the
# canonical plant-author default.
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_custom_columns_includes_transfer_completion() -> None:
    """ETL integrators bulk-loading real transactions need to set
    `transfer_completion` (the post-settlement timestamp). TX_COLS
    omits it for spine-author byte-identity; `columns=` opt-in lifts
    the restriction."""
    conn = _fresh_db()
    custom_cols = (
        "id", "account_id", "account_scope", "amount_money",
        "amount_direction", "status", "posting", "transfer_id",
        "transfer_completion", "rail_name", "origin",
    )
    rows = [(
        "tx-completion", "clearing-suspense-1", "internal",
        100.0, "Credit", "Posted", "2026-01-01 12:00:00",
        "xfer-completion", "2026-01-01 12:00:05",
        "_bulk_test", "etl",
    )]
    bulk_insert_tx(conn, rows, prefix=_PREFIX, columns=custom_cols)
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT transfer_completion FROM {_PREFIX}_transactions "
        f"WHERE id = 'tx-completion'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] is not None  # set to '2026-01-01 12:00:05', not NULL


def test_bulk_insert_tx_custom_columns_includes_bundle_id() -> None:
    """Same opt-in for `bundle_id` — the schema column TX_COLS omits
    because the stuck_unbundled plant relies on NULL-by-default. ETL
    integrators bulk-loading bundled transactions pass `columns=` with
    bundle_id present."""
    conn = _fresh_db()
    custom_cols = (
        "id", "account_id", "account_scope", "amount_money",
        "amount_direction", "status", "posting", "transfer_id",
        "rail_name", "origin", "bundle_id",
    )
    rows = [(
        "tx-bundled", "clearing-suspense-1", "internal",
        50.0, "Credit", "Posted", "2026-01-01 12:00:00",
        "xfer-bundled", "_bulk_test", "etl", "bundle-abc",
    )]
    bulk_insert_tx(conn, rows, prefix=_PREFIX, columns=custom_cols)
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT bundle_id FROM {_PREFIX}_transactions "
        f"WHERE id = 'tx-bundled'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "bundle-abc"


def test_bulk_insert_balance_custom_columns_includes_supersedes() -> None:
    """Mirror for `bulk_insert_balance` — `supersedes` is in the
    schema but DB_COLS omits it (snapshots without corrections are
    the default). `columns=` lifts that."""
    conn = _fresh_db()
    custom_cols = (
        "account_id", "account_scope", "business_day_start",
        "business_day_end", "money", "supersedes",
    )
    from recon_gen.common.l2.primitives import SUPERSEDE_TECHNICAL_CORRECTION
    rows = [(
        "acct-correction", "internal",
        "2026-01-01 00:00:00", "2026-01-02 00:00:00",
        0.0, SUPERSEDE_TECHNICAL_CORRECTION,
    )]
    bulk_insert_balance(
        conn, rows, prefix=_PREFIX, columns=custom_cols,
    )
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT supersedes FROM {_PREFIX}_daily_balances "
        f"WHERE account_id = 'acct-correction'"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == SUPERSEDE_TECHNICAL_CORRECTION


# ---------------------------------------------------------------------------
# 2026-06-15 fix — single-quote escape in `_render_sql_literal`. Reported
# from v14.0.1: bulk-loading a transaction with a string containing `'`
# (e.g. customer name "O'Reilly") bombed the SQL because the DuckDB
# multi-row VALUES path didn't escape the inner quote, producing
# malformed SQL like `INSERT ... VALUES ('O'Reilly', ...)`.
# ---------------------------------------------------------------------------


def test_bulk_insert_tx_handles_single_quote_in_strings() -> None:
    """A string containing `'` must round-trip through the DuckDB
    multi-row VALUES coalescer + back from the DB as the same string.
    Pre-fix: `_render_sql_literal` wrapped strings as `f"'{v}'"` with
    no escape, so `O'Reilly` became `'O'Reilly'` → SQL parse error."""
    conn = _fresh_db()
    rows = [
        _tx_row(tx_id="tx-quote-1", account_id="O'Reilly Capital"),
        _tx_row(tx_id="tx-quote-2", account_id="Bob's Bank"),
        _tx_row(tx_id="tx-quote-3", account_id="''double-leading'"),
    ]
    bulk_insert_tx(conn, rows, prefix=_PREFIX)
    conn.commit()
    cur = conn.cursor()
    cur.execute(
        f"SELECT id, account_id FROM {_PREFIX}_transactions "
        f"WHERE id LIKE 'tx-quote-%' ORDER BY id"
    )
    fetched = cur.fetchall()
    assert fetched == [
        ("tx-quote-1", "O'Reilly Capital"),
        ("tx-quote-2", "Bob's Bank"),
        ("tx-quote-3", "''double-leading'"),
    ]


def test_render_sql_literal_escapes_single_quotes() -> None:
    """Unit-level test of the escape — independent of the DB layer.
    `_render_sql_literal` is the actual fix site."""
    from recon_gen.common.db import _render_sql_literal
    assert _render_sql_literal("plain") == "'plain'"
    assert _render_sql_literal("O'Reilly") == "'O''Reilly'"
    # Input has 2 single quotes → 4 escaped + outer quotes = 5+leading+1
    assert _render_sql_literal("''leading") == "'''''leading'"
    assert _render_sql_literal("trailing'") == "'trailing'''"
    assert _render_sql_literal("multi'quote'string") == "'multi''quote''string'"
    # NULL / numeric / bool unchanged
    assert _render_sql_literal(None) == "NULL"
    assert _render_sql_literal(True) == "TRUE"
    assert _render_sql_literal(42) == "42"
