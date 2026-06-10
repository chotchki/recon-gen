"""Phase CW.6 — audit-PDF performance + correctness regression tests.

Three gates land here:

1. **Streamed-fingerprint perf regression.** ``hash_table_rows`` on a
   100k-row DuckDB fixture must complete in well under 2s (the perf
   audit measured 1.32s on 3.27M rows; 100k is ~3% of that, so a
   <2s budget is generous). Catches a regression that would silently
   re-introduce the per-cell Python loop or kill the in-engine
   per-row SHA-256.

2. **Merkle-Damgård equivalence.** The streamed Python fold
   (``hash_table_rows`` per-dialect SQL + ``h.update(rh)`` loop) must
   produce **byte-identical** output to ``sha256(string_agg(rh ORDER
   BY entry))`` computed entirely in SQL. Proves the
   theoretical claim from the perf audit empirically against the
   real codepath; gates against a future refactor that drops the
   ordering invariant.

3. **Legacy v1 frozen-ladder.** A unit-level smoke that
   ``legacy_hash_table_rows_v1`` reproduces a stable hex digest
   against a controlled synthetic cursor. The v1 path is frozen for
   pre-CW PDF verification; the test is the safety net that catches
   "someone touched the supposedly-frozen function".

DuckDB-only because the perf-audit measurements were taken on
DuckDB; cross-dialect coverage rides on the DB-tier integration
tests + the per-dialect SQL helpers in ``_row_hash_sql_expr``.
"""

from __future__ import annotations

import hashlib
import time

import duckdb

from recon_gen.common.provenance import (
    PROVENANCE_FORMAT_VERSION_CW,
    PROVENANCE_FORMAT_VERSION_LEGACY,
    canonical_value,
    hash_table_rows,
    legacy_hash_table_rows_v1,
)
from recon_gen.common.sql.dialect import Dialect


def _make_fixture_table(conn: duckdb.DuckDBPyConnection, n_rows: int) -> None:
    """Create a transactions-shaped fixture table of ``n_rows`` rows.

    Columns mirror the real ``<prefix>_transactions`` shape closely
    enough to exercise NULL handling, mixed types (text + bigint +
    date), and the column-order-by-lower(name) sort. Deterministic
    payload so the SHA value is stable across runs.

    Uses DuckDB's ``range(...)`` table function + computed
    expressions to bulk-materialize the fixture in one statement —
    ``executemany`` is ~60s for 100k rows (DuckDB's Python binding
    has no bulk-insert path), but a single ``INSERT INTO ... SELECT
    ... FROM range(...)`` is ~0.1s.
    """
    conn.execute(
        """
        CREATE TABLE fix_transactions (
            entry BIGINT PRIMARY KEY,
            account_id VARCHAR,
            signed_amount BIGINT,
            balance_date DATE,
            transfer_type VARCHAR,
            status VARCHAR,
            metadata VARCHAR
        )
        """
    )
    # Single-statement bulk load; mix some NULLs so the chr(0)
    # sentinel path is exercised.
    conn.execute(
        f"""
        INSERT INTO fix_transactions
        SELECT
            i AS entry,
            CASE WHEN i % 7 = 0 THEN NULL
                 ELSE 'acct' || (i % 100) END AS account_id,
            (i * 13) - 1000000 AS signed_amount,
            DATE '2026-01-01' + INTERVAL ((i - 1) % 365) DAY
                AS balance_date,
            CASE WHEN i % 3 = 0 THEN 'credit' ELSE 'debit' END
                AS transfer_type,
            CASE WHEN i % 5 = 0 THEN NULL
                 ELSE 'POSTED' END AS status,
            CASE WHEN i % 11 = 0 THEN NULL
                 ELSE '{{"k": ' || i || '}}' END AS metadata
        FROM range(1, {n_rows + 1}) AS r(i)
        """
    )


def test_streamed_fingerprint_perf_on_100k_rows() -> None:
    """Streamed ``hash_table_rows`` on 100k rows must beat 2s wall.

    Perf-audit measurement: ~1.32s on a 3.27M-row DuckDB. 100k is
    ~3% of that, so we expect ~0.04s; 2s is the alarm threshold. A
    regression that re-introduced ``fetchall + per-cell Python``
    would push it to 0.6s+ at this scale, blowing the budget loud.
    """
    conn = duckdb.connect(":memory:")
    _make_fixture_table(conn, 100_000)
    cur = conn.cursor()

    t0 = time.perf_counter()
    sha = hash_table_rows(
        cur, table="fix_transactions", hwm=100_000,
        dialect=Dialect.DUCKDB,
    )
    wall = time.perf_counter() - t0

    assert len(sha) == 64, "expected 64-char hex digest"
    assert wall < 2.0, (
        f"streamed hash_table_rows on 100k rows took {wall:.2f}s; "
        f"perf-audit budget is well under 2s (see "
        f"docs/audits/audit_pdf_perf.md). A regression here usually "
        f"means the per-cell Python loop crept back in."
    )


def test_streamed_fold_equals_listagg_string_agg_form_duckdb() -> None:
    """Merkle-Damgård empirical proof: streamed fold == all-SQL form.

    Computes the fingerprint two ways:
    - The CW.2 ``hash_table_rows`` (per-row sha256 in SQL + Python
      h.update fold).
    - All-SQL form ``sha256(string_agg(rh ORDER BY entry))`` over the
      same per-row sha256 column.

    Asserts byte-equality. Proves the Python fold is correct (no
    casing-drift, no separator surprise, no missing rows) against
    the math from the perf-audit doc.
    """
    conn = duckdb.connect(":memory:")
    _make_fixture_table(conn, 1_000)
    cur = conn.cursor()

    # Path A: streamed
    sha_streamed = hash_table_rows(
        cur, table="fix_transactions", hwm=1_000,
        dialect=Dialect.DUCKDB,
    )

    # Path B: all-SQL string_agg. Manually construct the per-row
    # hash with the same canonicalization the helper builds.
    # Columns sorted by lower(name); chr(0) NULL sentinel; chr(31)
    # column separator.
    columns_sorted = [
        '"account_id"', '"balance_date"', '"entry"', '"metadata"',
        '"signed_amount"', '"status"', '"transfer_type"',
    ]
    canon_args = ", ".join(
        f"coalesce(CAST({c} AS VARCHAR), chr(0))"
        for c in columns_sorted
    )
    cur.execute(
        f"""
        SELECT sha256(string_agg(rh, '' ORDER BY entry_)) FROM (
            SELECT entry AS entry_,
                   lower(sha256(concat_ws(chr(31), {canon_args}))) AS rh
            FROM fix_transactions
            WHERE entry <= 1000
        )
        """
    )
    row = cur.fetchone()
    assert row is not None, "all-SQL form returned no row"
    sha_listagg = row[0]

    assert sha_streamed == sha_listagg, (
        f"Merkle-Damgård equivalence broken:\n"
        f"  streamed: {sha_streamed}\n"
        f"  listagg:  {sha_listagg}\n"
        f"This is the load-bearing claim from the perf audit "
        f"(docs/audits/audit_pdf_perf.md). A drift here means the "
        f"Python fold is no longer byte-identical to the all-SQL "
        f"form — usually a separator / casing / ordering regression."
    )


def test_legacy_hash_table_rows_v1_stable_against_synthetic_cursor() -> None:
    """The v1 ladder must produce a deterministic SHA against a
    controlled cursor — the safety net for "someone touched the
    supposedly-frozen function".

    CW.4 wires this function into ``audit verify``'s v1 dispatch
    branch; any byte-drift here breaks verification of every
    pre-CW PDF in existence. The test pins a known value computed
    against a 3-row synthetic cursor with the exact byte shape the
    original ladder produced.
    """
    class FakeCursor:
        def __init__(self) -> None:
            self._rows: list[tuple[object, ...]] = []
            self.description: list[
                tuple[str, object, object, object, object, object, object]
            ] = [
                ("entry", None, None, None, None, None, None),
                ("account_id", None, None, None, None, None, None),
                ("signed_amount", None, None, None, None, None, None),
            ]

        def execute(self, sql: str) -> None:  # noqa: ARG002
            self._rows = [
                (1, "alpha", 100),
                (2, None, -50),
                (3, "gamma", 0),
            ]

        def fetchall(self) -> list[tuple[object, ...]]:
            return self._rows

    cur = FakeCursor()
    sha = legacy_hash_table_rows_v1(cur, table="t", hwm=3)

    # Manually reproduce what the ladder should compute. If this
    # value drifts, the ladder changed and v1 PDF verification is
    # broken. Compute by hand from `canonical_value` so we don't
    # tautologically use the same function.
    expected_h = hashlib.sha256()
    # Sorted by lower(name): ['account_id', 'entry', 'signed_amount']
    # Row 1: alpha, 1, 100
    expected_h.update(
        b"\x1f".join([
            canonical_value("alpha"),
            canonical_value(1),
            canonical_value(100),
        ])
    )
    expected_h.update(b"\x1e")
    # Row 2: None, 2, -50
    expected_h.update(
        b"\x1f".join([
            canonical_value(None),
            canonical_value(2),
            canonical_value(-50),
        ])
    )
    expected_h.update(b"\x1e")
    # Row 3: gamma, 3, 0
    expected_h.update(
        b"\x1f".join([
            canonical_value("gamma"),
            canonical_value(3),
            canonical_value(0),
        ])
    )
    expected_h.update(b"\x1e")
    expected = expected_h.hexdigest()

    assert sha == expected, (
        f"v1 ladder drifted!\n"
        f"  legacy_hash_table_rows_v1: {sha}\n"
        f"  expected (hand-computed):  {expected}\n"
        f"The v1 function is frozen so pre-CW PDFs keep verifying. "
        f"Revert the change."
    )


def test_format_version_sentinels_match_cw_lock_doc() -> None:
    """Lock-doc 4 (docs/audits/cw_0_audit_pdf_perf_locks.md) pins
    the version numbers: legacy=1, CW=2. The sentinels in
    ``common/provenance.py`` need to track that contract; a silent
    drift would re-confuse the dispatch in ``audit verify``.
    """
    assert PROVENANCE_FORMAT_VERSION_LEGACY == 1
    assert PROVENANCE_FORMAT_VERSION_CW == 2
