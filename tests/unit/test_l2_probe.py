"""BT.2 — ``common.l2.probe`` unit tests.

Two surfaces under test:

- ``fetch_probe_rows`` — SQL fetcher; seeded aiosqlite pool with a
  handful of transactions rows across rail / template / chain
  slices, asserts that the correct rows come back in posting-DESC
  order with the right total count.
- ``evaluate_predicate`` — pure-Python; one test per BT.5
  predicate shape (equals / one_of / not_null + the
  metadata.<key> branch).

The fetcher tests mirror ``test_l2_coverage.py``'s file-backed
aiosqlite pool pattern (in-memory mode would give each pool
connection its own DB).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import date, datetime

import pytest

from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
from recon_gen.common.l2 import (
    ColumnPredicate,
)
from recon_gen.common.l2.probe import (
    ProbeKind,
    ProbeRow,
    evaluate_predicate,
    fetch_probe_rows,
)
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


# -- Fetcher fixture ---------------------------------------------------------


_PREFIX = "probe_test"


@pytest.fixture
def seeded_pool() -> Iterator[AsyncConnectionPool]:
    """File-backed aiosqlite pool seeded with a handful of transactions
    rows spanning rail / template / chain slices across a 10-day window.

    Layout:
    - Rail ``ach_credit``: 3 rows, postings 2030-01-05 / 06 / 07
    - Rail ``wire``: 2 rows, postings 2030-01-04 / 05
    - Template ``merchant_cycle``: 1 row (rail_name=ach_credit,
      template_name=merchant_cycle, posting 2030-01-08)
    - Chain parent ``payment_orchestrator`` matches the wire row's
      rail_name (so chain-kind probes find it)
    - All rows carry transfer_parent_id NULL except the merchant_cycle
      row (which has transfer_parent_id='tx-orig').
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute(
        f"CREATE TABLE {_PREFIX}_transactions ("
        "id TEXT PRIMARY KEY, "
        "rail_name TEXT, "
        "template_name TEXT, "
        "account_role TEXT, "
        "amount_direction TEXT NOT NULL, "
        "transfer_parent_id TEXT, "
        "posting TIMESTAMP NOT NULL, "
        "metadata TEXT)"
    )
    rows: list[tuple[str, str, str | None, str, str, str | None, str, str | None]] = [
        # id, rail, template, role, direction, parent_id, posting, metadata
        ("tx-001", "ach_credit", None, "CustomerLedger", "Credit", None,
         "2030-01-05 09:00:00", '{"trace_id":"abc"}'),
        ("tx-002", "ach_credit", None, "ExtCounterparty", "Debit", None,
         "2030-01-06 09:00:00", '{"trace_id":"def"}'),
        ("tx-003", "ach_credit", None, "CustomerLedger", "Credit", None,
         "2030-01-07 09:00:00", None),
        ("tx-004", "wire", None, "CustomerLedger", "Credit", None,
         "2030-01-04 09:00:00", '{"trace_id":"ghi"}'),
        ("tx-005", "wire", None, "CustomerLedger", "Credit", None,
         "2030-01-05 09:00:00", '{"trace_id":"jkl"}'),
        ("tx-006", "ach_credit", "merchant_cycle", "ExtCounterparty",
         "Debit", "tx-orig", "2030-01-08 09:00:00",
         '{"merchant_id":"m1","trace_id":"mno"}'),
    ]
    conn.executemany(
        f"INSERT INTO {_PREFIX}_transactions "
        "(id, rail_name, template_name, account_role, amount_direction, "
        " transfer_parent_id, posting, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        yield pool
    finally:
        asyncio.run(pool.close())
        os.unlink(path)


def _fetch(
    pool: AsyncConnectionPool, kind: ProbeKind, name: str,
    date_from: date = date(2030, 1, 1),
    date_to: date = date(2030, 1, 31),
    limit: int = 25,
):  # noqa: ANN202 — return type self-evident
    return asyncio.run(fetch_probe_rows(
        pool, _PREFIX,
        kind=kind, name=name,
        date_from=date_from, date_to=date_to,
        dialect=Dialect.SQLITE, limit=limit,
    ))


# -- fetch_probe_rows: rail slice --------------------------------------------


def test_fetch_rail_slice_returns_matching_rows_in_posting_desc_order(
    seeded_pool: AsyncConnectionPool,
) -> None:
    result = _fetch(seeded_pool, "rail", "ach_credit")
    # 4 rows have rail_name=ach_credit (tx-001/002/003/006).
    assert result.total_count == 4
    assert [r.transaction_id for r in result.rows] == [
        "tx-006", "tx-003", "tx-002", "tx-001",
    ]
    # Most-recent row carries the template_name + parent_id.
    first = result.rows[0]
    assert first.template_name == "merchant_cycle"
    assert first.transfer_parent_id == "tx-orig"


def test_fetch_rail_slice_empty_when_no_match(
    seeded_pool: AsyncConnectionPool,
) -> None:
    result = _fetch(seeded_pool, "rail", "nonexistent_rail")
    assert result.total_count == 0
    assert result.rows == ()


# -- fetch_probe_rows: template slice ----------------------------------------


def test_fetch_template_slice_only_matches_non_null_template_rows(
    seeded_pool: AsyncConnectionPool,
) -> None:
    result = _fetch(seeded_pool, "transfer_template", "merchant_cycle")
    assert result.total_count == 1
    assert result.rows[0].transaction_id == "tx-006"


# -- fetch_probe_rows: chain slice -------------------------------------------


def test_fetch_chain_slice_matches_rail_OR_template_named_after_parent(
    seeded_pool: AsyncConnectionPool,
) -> None:
    """Chain slice picks up rows where the chain's parent name appears
    as EITHER rail_name OR template_name (parents can be either)."""
    # 'wire' parent — 2 rows with rail_name=wire, 0 with template_name=wire.
    result = _fetch(seeded_pool, "chain", "wire")
    assert result.total_count == 2
    assert {r.transaction_id for r in result.rows} == {"tx-004", "tx-005"}

    # 'merchant_cycle' parent — 1 row with template_name=merchant_cycle.
    result = _fetch(seeded_pool, "chain", "merchant_cycle")
    assert result.total_count == 1
    assert result.rows[0].transaction_id == "tx-006"


# -- fetch_probe_rows: window narrowing --------------------------------------


def test_fetch_window_narrowing_excludes_rows_outside_window(
    seeded_pool: AsyncConnectionPool,
) -> None:
    """A narrow window includes both endpoints; rows outside fall off."""
    # 2030-01-05 only: tx-001 (ach_credit) + tx-005 (wire) posted that day.
    result = _fetch(
        seeded_pool, "rail", "ach_credit",
        date_from=date(2030, 1, 5), date_to=date(2030, 1, 5),
    )
    assert result.total_count == 1
    assert result.rows[0].transaction_id == "tx-001"


def test_fetch_window_endpoints_are_inclusive_both_ends(
    seeded_pool: AsyncConnectionPool,
) -> None:
    """Posting on the end date (e.g. 2030-01-07) is included."""
    result = _fetch(
        seeded_pool, "rail", "ach_credit",
        date_from=date(2030, 1, 5), date_to=date(2030, 1, 7),
    )
    assert result.total_count == 3
    # Descending posting order.
    assert [r.transaction_id for r in result.rows] == [
        "tx-003", "tx-002", "tx-001",
    ]


# -- fetch_probe_rows: limit -------------------------------------------------


def test_fetch_limit_caps_rows_but_not_total_count(
    seeded_pool: AsyncConnectionPool,
) -> None:
    """``total_count`` is the pre-limit count; ``rows`` is capped."""
    result = _fetch(seeded_pool, "rail", "ach_credit", limit=2)
    assert result.total_count == 4
    assert len(result.rows) == 2
    # Top-2 by posting DESC.
    assert [r.transaction_id for r in result.rows] == ["tx-006", "tx-003"]


# -- evaluate_predicate: equals ----------------------------------------------


def _row(**overrides: object) -> ProbeRow:
    """Build a ProbeRow with sensible defaults; override per-test."""
    defaults: dict[str, object] = dict(
        transaction_id="tx-x",
        rail_name="ach_credit",
        template_name=None,
        account_role="CustomerLedger",
        amount_direction="Credit",
        transfer_parent_id=None,
        posting=datetime(2030, 1, 5, 9, 0, 0),
        metadata=None,
    )
    defaults.update(overrides)
    return ProbeRow(**defaults)  # type: ignore[arg-type]: dict-spread loses precise field typing; values are constrained by caller-supplied keys


def test_evaluate_equals_predicate_holds_on_matching_row() -> None:
    pred = ColumnPredicate(
        column="amount_direction", kind="equals", expected="Credit",
    )
    assert evaluate_predicate(pred, _row()) is True


def test_evaluate_equals_predicate_contradicts_on_mismatch() -> None:
    pred = ColumnPredicate(
        column="amount_direction", kind="equals", expected="Debit",
    )
    assert evaluate_predicate(pred, _row()) is False


# -- evaluate_predicate: one_of ----------------------------------------------


def test_evaluate_one_of_predicate_holds_when_value_in_set() -> None:
    pred = ColumnPredicate(
        column="account_role", kind="one_of",
        expected=("CustomerLedger", "ExtCounterparty"),
    )
    assert evaluate_predicate(pred, _row()) is True


def test_evaluate_one_of_predicate_contradicts_when_value_not_in_set() -> None:
    pred = ColumnPredicate(
        column="account_role", kind="one_of",
        expected=("ExtCounterparty",),
    )
    assert evaluate_predicate(pred, _row()) is False


# -- evaluate_predicate: not_null --------------------------------------------


def test_evaluate_not_null_predicate_holds_on_non_null_value() -> None:
    pred = ColumnPredicate(
        column="transfer_parent_id", kind="not_null", expected=None,
    )
    assert evaluate_predicate(pred, _row(transfer_parent_id="tx-orig")) is True


def test_evaluate_not_null_predicate_contradicts_on_null_value() -> None:
    pred = ColumnPredicate(
        column="transfer_parent_id", kind="not_null", expected=None,
    )
    assert evaluate_predicate(pred, _row(transfer_parent_id=None)) is False


# -- evaluate_predicate: metadata key presence -------------------------------


def test_evaluate_metadata_key_predicate_holds_when_key_in_json() -> None:
    pred = ColumnPredicate(
        column="metadata.trace_id", kind="not_null", expected=None,
    )
    row = _row(metadata='{"trace_id":"abc","other":"x"}')
    assert evaluate_predicate(pred, row) is True


def test_evaluate_metadata_key_predicate_contradicts_when_key_absent() -> None:
    pred = ColumnPredicate(
        column="metadata.trace_id", kind="not_null", expected=None,
    )
    row = _row(metadata='{"other":"x"}')
    assert evaluate_predicate(pred, row) is False


def test_evaluate_metadata_key_predicate_returns_none_on_null_metadata() -> None:
    """NULL metadata is inconclusive — not a per-key violation, but also
    not a confirmation. Render layer shows it as '—'."""
    pred = ColumnPredicate(
        column="metadata.trace_id", kind="not_null", expected=None,
    )
    assert evaluate_predicate(pred, _row(metadata=None)) is None


# -- evaluate_predicate: NULL value semantics --------------------------------


def test_evaluate_one_of_on_null_value_returns_none_not_false() -> None:
    """A NULL account_role isn't a contradiction of the predicate — it's
    a missing observation. Render layer paints '—'."""
    pred = ColumnPredicate(
        column="account_role", kind="one_of",
        expected=("CustomerLedger",),
    )
    assert evaluate_predicate(pred, _row(account_role=None)) is None


def test_evaluate_unrecognized_column_returns_none() -> None:
    """A predicate naming a column the probe row doesn't carry (e.g. a
    future BT.5 surface) returns None rather than crashing — the
    render layer paints '—' so the operator sees the gap."""
    pred = ColumnPredicate(
        column="bundle_id", kind="not_null", expected=None,
    )
    assert evaluate_predicate(pred, _row()) is None
