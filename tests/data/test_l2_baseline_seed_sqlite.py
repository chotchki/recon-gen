"""Tests for ``emit_baseline_seed`` / ``emit_full_seed`` against SQLite.

X.3.d — mirrors the PG/Oracle-shaped tests in
``test_l2_baseline_seed.py`` but executes the emitted SQL against an
in-memory ``sqlite3`` connection. Asserts that:

- The schema apply (``emit_schema(..., dialect=Dialect.SQLITE)``) +
  the seed apply land together — no DDL/DML conflict, no missing
  helper.
- The base-table row counts match the PG/Oracle expectations (the
  same ``emit_full_seed`` call returns the same number of INSERTs
  per dialect; SQLite's ``executemany`` over per-row INSERT actually
  inserts each row).
- ``emit_truncate_sql`` cleanly empties the base tables on SQLite
  (the DELETE + sqlite_sequence reset closes the
  PG-RESTART-IDENTITY parity gap).
- ``emit_full_seed`` is byte-stable for a fixed anchor on SQLite
  too, the same determinism contract PG/Oracle follow.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path


from recon_gen.common.db import execute_script
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.l2.seed import (
    emit_baseline_seed,
    emit_full_seed,
    emit_truncate_sql,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
_SASQUATCH_PR = Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"
# Z.C — db_table_prefix is now a cfg.yaml field (was ``L2Instance.instance``).
# Tests pin the per-fixture prefix here so per-prefix table-name assertions
# stay valid.
_SPEC_EXAMPLE_PREFIX = "spec_example"
_SASQUATCH_PR_PREFIX = "sasquatch_pr"
_ANCHOR = date(2026, 4, 30)


def _open_sqlite() -> sqlite3.Connection:
    """Open an in-memory SQLite with the SQL/2008 STDDEV_SAMP aggregate
    registered — same setup ``connect_demo_db`` does for the SQLite
    dialect. Tests that only need to apply schema + seed don't depend
    on the aggregate, but matview refresh does — register here so the
    helper is reusable across both code paths.
    """
    from recon_gen.common.db import _register_sqlite_aggregates
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    return conn


# -- _sql_timestamp_literal SQLite branch -----------------------------------


class TestSqlTimestampLiteralSqlite:
    """X.3.d — _sql_timestamp_literal's SQLite branch.

    SQLite stores TIMESTAMP as TEXT; the literal must be a bare
    ISO-8601 string (no ``TIMESTAMP '...'`` typed-literal wrapper —
    that's Oracle-only) parseable by ``date()`` / ``datetime()`` /
    ``julianday()``. Use a space separator so SQLite's parser
    recognizes it unambiguously (T is also accepted but space matches
    the Oracle output for visual consistency).
    """

    def test_sqlite_strips_tz_offset(self) -> None:
        from recon_gen.common.l2.seed import _sql_timestamp_literal
        # Trailing +HH:MM offset → stripped per the TZ-naive contract.
        out = _sql_timestamp_literal(
            "2030-01-01T09:00:00+00:00", Dialect.SQLITE,
        )
        assert "+00:00" not in out
        assert out == "'2030-01-01 09:00:00'"

    def test_sqlite_strips_z_offset(self) -> None:
        from recon_gen.common.l2.seed import _sql_timestamp_literal
        out = _sql_timestamp_literal(
            "2030-01-01T09:00:00Z", Dialect.SQLITE,
        )
        assert out.endswith("'")
        assert "Z" not in out

    def test_sqlite_no_typed_literal_wrapper(self) -> None:
        # PG: ``'2030-01-01 09:00:00'``
        # Oracle: ``TIMESTAMP '2030-01-01 09:00:00'``
        # SQLite: ``'2030-01-01 09:00:00'`` (PG-shaped — no TIMESTAMP word)
        from recon_gen.common.l2.seed import _sql_timestamp_literal
        out = _sql_timestamp_literal(
            "2030-01-01T09:00:00", Dialect.SQLITE,
        )
        assert "TIMESTAMP" not in out

    def test_sqlite_uses_space_separator(self) -> None:
        # SQLite's date functions accept both T and space; we emit
        # space for visual parity with Oracle (and `julianday()` is
        # happier with space).
        from recon_gen.common.l2.seed import _sql_timestamp_literal
        out = _sql_timestamp_literal(
            "2030-01-01T09:30:45", Dialect.SQLITE,
        )
        assert " " in out
        assert "T" not in out

    def test_sqlite_round_trips_through_julianday(self) -> None:
        # The most direct functional assertion: the SQLite literal must
        # be parseable by julianday(), which the schema's
        # epoch_seconds_between branch depends on.
        from recon_gen.common.l2.seed import _sql_timestamp_literal
        lit = _sql_timestamp_literal(
            "2030-01-01T09:00:00+00:00", Dialect.SQLITE,
        )
        conn = sqlite3.connect(":memory:")
        cur = conn.execute(f"SELECT julianday({lit})")
        result = cur.fetchone()[0]
        assert result is not None
        # JD for 2030-01-01 09:00 UTC = 2462502.875
        assert 2462502 < result < 2462504


# -- emit_truncate_sql SQLite branch ----------------------------------------


class TestEmitTruncateSqlSqlite:
    """X.3.d — emit_truncate_sql's SQLite branch.

    SQLite has no ``TRUNCATE`` — uses ``DELETE FROM`` plus a
    sqlite_sequence reset to recover the AUTOINCREMENT counter.
    """

    def test_uses_delete_not_truncate(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_truncate_sql(
            instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
        )
        # No TRUNCATE statement (the word may appear in the comment
        # header, hence the keyword form).
        assert "TRUNCATE TABLE" not in sql
        assert "DELETE FROM spec_example_transactions" in sql
        assert "DELETE FROM spec_example_daily_balances" in sql

    def test_resets_sqlite_sequence(self) -> None:
        # The DELETE FROM sqlite_sequence call mirrors PG's
        # RESTART IDENTITY semantic — the next INSERT starts at entry=1.
        instance = load_instance(_SPEC_EXAMPLE)
        sql = emit_truncate_sql(
            instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
        )
        assert "sqlite_sequence" in sql
        assert "spec_example_transactions" in sql
        assert "spec_example_daily_balances" in sql

    def test_runs_on_fresh_schema_no_error(self) -> None:
        # Wipe-on-empty must be a no-op (sqlite_sequence may not exist
        # yet on a freshly-created schema with no INSERTs).
        instance = load_instance(_SPEC_EXAMPLE)
        conn = _open_sqlite()
        # Apply schema — no rows yet.
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        # Wipe.
        execute_script(
            cur, emit_truncate_sql(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        # Tables still exist; just empty.
        rows = cur.execute(
            "SELECT COUNT(*) FROM spec_example_transactions",
        ).fetchone()
        assert rows[0] == 0


# -- Schema + seed end-to-end against in-memory SQLite ---------------------


class TestSeedEndToEndSqlite:
    """X.3.d — the full schema + seed loop against in-memory SQLite.

    Verifies that ``emit_schema(SQLITE)`` + ``emit_full_seed(SQLITE)``
    apply cleanly together, and that the row counts in the base
    tables come out at the same magnitude as the PG/Oracle paths.
    """

    def test_spec_example_schema_then_seed_lands(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        conn = _open_sqlite()
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        seed_sql = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        execute_script(cur, seed_sql, dialect=Dialect.SQLITE)
        # Schema-then-seed lands without error; both base tables have
        # rows.
        n_tx = cur.execute(
            "SELECT COUNT(*) FROM spec_example_transactions",
        ).fetchone()[0]
        n_db = cur.execute(
            "SELECT COUNT(*) FROM spec_example_daily_balances",
        ).fetchone()[0]
        # spec_example baseline emits at least ~100 legs (per
        # test_l2_baseline_seed.py::test_spec_example_emits_thousands_of_legs).
        # Plus plant rows; total is comfortably well above 100.
        assert n_tx > 100, (
            f"spec_example transactions: expected >100, got {n_tx}"
        )
        assert n_db > 0, "spec_example daily_balances: expected >0"

    def test_sasquatch_pr_schema_then_seed_lands(self) -> None:
        # The big instance — exercises every plant kind + the
        # 25-template-instance baseline volume.
        instance = load_instance(_SASQUATCH_PR)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        conn = _open_sqlite()
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(
                instance, prefix=_SASQUATCH_PR_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        seed_sql = emit_full_seed(
            instance, scenario, prefix=_SASQUATCH_PR_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        execute_script(cur, seed_sql, dialect=Dialect.SQLITE)
        n_tx = cur.execute(
            "SELECT COUNT(*) FROM sasquatch_pr_transactions",
        ).fetchone()[0]
        n_db = cur.execute(
            "SELECT COUNT(*) FROM sasquatch_pr_daily_balances",
        ).fetchone()[0]
        # Restored to 30k lower bound after Z.C.7 follow-on rewired
        # ``seed.py::_classify_rail`` to substring-match the post-Z.B
        # CamelCase rail names (`CustomerInboundACH` etc.) instead of
        # the legacy snake_case tokens (`ach_inbound`).
        assert 30_000 <= n_tx <= 200_000, (
            f"sasquatch_pr transactions: expected 30k-200k, got {n_tx}"
        )
        assert 1_000 <= n_db <= 10_000, (
            f"sasquatch_pr daily_balances: expected 1k-10k, got {n_db}"
        )

    def test_metadata_column_validates_as_json(self) -> None:
        # The schema's metadata CHECK uses json_valid() on SQLite —
        # every emitted metadata literal must be well-formed JSON or
        # the INSERT raises "CHECK constraint failed: ...".
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        conn = _open_sqlite()
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        seed_sql = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        # If any metadata cell were invalid JSON, this would raise
        # IntegrityError on at least one INSERT.
        execute_script(cur, seed_sql, dialect=Dialect.SQLITE)
        # Belt-and-suspenders: re-validate every non-NULL metadata cell.
        bad = cur.execute(
            "SELECT COUNT(*) FROM spec_example_transactions "
            "WHERE metadata IS NOT NULL AND json_valid(metadata) = 0"
        ).fetchone()[0]
        assert bad == 0, (
            f"{bad} transaction rows have metadata that fails "
            "json_valid()"
        )

    def test_seed_then_truncate_then_seed_again(self) -> None:
        # X.3.d — full lifecycle: apply, wipe, re-apply. The wipe must
        # leave the schema intact; the second apply must land at the
        # same row count as the first.
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        conn = _open_sqlite()
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        seed_sql = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        execute_script(cur, seed_sql, dialect=Dialect.SQLITE)
        n1 = cur.execute(
            "SELECT COUNT(*) FROM spec_example_transactions",
        ).fetchone()[0]

        execute_script(
            cur, emit_truncate_sql(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        n_wiped = cur.execute(
            "SELECT COUNT(*) FROM spec_example_transactions",
        ).fetchone()[0]
        assert n_wiped == 0

        execute_script(cur, seed_sql, dialect=Dialect.SQLITE)
        n2 = cur.execute(
            "SELECT COUNT(*) FROM spec_example_transactions",
        ).fetchone()[0]
        assert n1 == n2, (
            f"Wipe+re-seed should yield same row count: {n1} vs {n2}"
        )


# -- Determinism: byte-stable emit per fixed anchor -------------------------


class TestSeedDeterminismSqlite:
    """X.3.d — the seed emit on SQLite is deterministic at a fixed
    anchor, same as PG/Oracle. Two emits at the same anchor must
    return byte-identical strings."""

    def test_baseline_byte_stable(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        a = emit_baseline_seed(
            instance, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        b = emit_baseline_seed(
            instance, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        assert a == b

    def test_full_seed_byte_stable(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        a = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        b = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        assert a == b


# -- Cross-dialect row count parity -----------------------------------------


class TestRowCountParityAcrossDialects:
    """X.3.d — the same scenario should yield the same INSERT count
    on every dialect (the per-row INSERT shape is identical and
    P.5.b explicitly chose per-row over multi-row VALUES for Oracle
    portability — SQLite gets the same shape for free).
    """

    def test_spec_example_same_tx_count_pg_vs_sqlite(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        pg_sql = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.POSTGRES,
        )
        sqlite_sql = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        pg_n = pg_sql.count("INSERT INTO spec_example_transactions")
        sqlite_n = sqlite_sql.count("INSERT INTO spec_example_transactions")
        assert pg_n == sqlite_n, (
            f"INSERT count parity: PG={pg_n}, SQLite={sqlite_n}"
        )

    def test_spec_example_same_db_count_pg_vs_sqlite(self) -> None:
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        pg_sql = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.POSTGRES,
        )
        sqlite_sql = emit_full_seed(
            instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.SQLITE,
        )
        pg_n = pg_sql.count("INSERT INTO spec_example_daily_balances")
        sqlite_n = sqlite_sql.count(
            "INSERT INTO spec_example_daily_balances",
        )
        assert pg_n == sqlite_n


# -- Refresh: SQLite matview refresh applies cleanly after seed -------------


class TestMatviewRefreshSqlite:
    """X.3.f — schema + seed + refresh full loop on in-memory SQLite.

    The refresh re-runs every matview-as-table CREATE so the
    derived tables (drift, overdraft, limit_breach, ...) populate
    from the freshly-loaded base tables.
    """

    def test_full_apply_refresh_loop(self) -> None:
        from recon_gen.common.l2.schema import refresh_matviews_sql
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        conn = _open_sqlite()
        cur = conn.cursor()
        # Schema.
        execute_script(
            cur, emit_schema(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        # Seed.
        execute_script(
            cur,
            emit_full_seed(
                instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
                anchor=_ANCHOR, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        # Refresh — drops + re-creates every matview-as-table.
        execute_script(
            cur, refresh_matviews_sql(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        # Current* matviews carry the max-Entry-per-(id) projection of
        # the base tables. A row count == base - (number of superseded
        # rows). spec_example's default scenario plants exactly one
        # SupersessionPlant (a 2-row supersession pair), so
        # current_transactions has exactly 1 fewer row than the base.
        n_base = cur.execute(
            "SELECT COUNT(*) FROM spec_example_transactions",
        ).fetchone()[0]
        n_curr = cur.execute(
            "SELECT COUNT(*) FROM spec_example_current_transactions",
        ).fetchone()[0]
        # Distinct id count == current_* row count by definition of
        # max-Entry-per-(id).
        n_distinct_ids = cur.execute(
            "SELECT COUNT(DISTINCT id) FROM spec_example_transactions",
        ).fetchone()[0]
        assert n_curr == n_distinct_ids, (
            f"current_transactions count {n_curr} should equal distinct "
            f"transaction-id count in base {n_distinct_ids}"
        )
        # And the current count matches base minus the superseded rows
        # (n_base - n_distinct_ids = number of superseded rows).
        assert n_base >= n_curr, (
            f"current count {n_curr} can't exceed base count {n_base}"
        )

    def test_refresh_resets_idempotent(self) -> None:
        # X.3.c — the SQLite refresh is teardown + rebuild; running
        # it twice in a row should leave the matviews in the same
        # state.
        from recon_gen.common.l2.schema import refresh_matviews_sql
        instance = load_instance(_SPEC_EXAMPLE)
        scenario = default_scenario_for(instance, today=_ANCHOR).scenario
        conn = _open_sqlite()
        cur = conn.cursor()
        execute_script(
            cur, emit_schema(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        execute_script(
            cur,
            emit_full_seed(
                instance, scenario, prefix=_SPEC_EXAMPLE_PREFIX,
                anchor=_ANCHOR, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        execute_script(
            cur, refresh_matviews_sql(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        n_curr_a = cur.execute(
            "SELECT COUNT(*) FROM spec_example_current_transactions",
        ).fetchone()[0]
        execute_script(
            cur, refresh_matviews_sql(
                instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.SQLITE,
            ),
            dialect=Dialect.SQLITE,
        )
        n_curr_b = cur.execute(
            "SELECT COUNT(*) FROM spec_example_current_transactions",
        ).fetchone()[0]
        assert n_curr_a == n_curr_b
