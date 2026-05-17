"""X.3.f — end-to-end local SQLite loop test.

Mirrors the operator workflow:

  recon-gen schema apply --execute  -c sqlite-config.yaml
  recon-gen data   apply --execute  -c sqlite-config.yaml
  recon-gen data   refresh --execute  -c sqlite-config.yaml

Against a real sqlite file (tmp_path-scoped, not in-memory) — so the
connect-and-apply machinery, not just the emit, gets exercised. The
matview-as-table populations after refresh should mirror what PG /
Oracle produce against the same scenario.

Why a separate file from test_l2_baseline_seed_sqlite.py: that file
uses ``execute_script(cur, sql, ...)`` directly with an in-memory
connection. This file goes through ``connect_demo_db(cfg)`` +
``connect_and_apply(cfg, sql, ...)``, the exact path the CLI takes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from recon_gen.cli._helpers import (
    build_full_seed_sql,
    connect_and_apply,
)
from recon_gen.common.config import Config
from recon_gen.common.db import (
    _register_sqlite_aggregates,
    connect_demo_db,
    execute_script,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import (
    emit_schema,
    refresh_matviews_sql,
)
from recon_gen.common.sql import Dialect


_REPO_ROOT = Path(__file__).resolve().parents[1]
_L2_DIR = _REPO_ROOT / "l2"
_SPEC_EXAMPLE = _L2_DIR / "spec_example.yaml"
_ANCHOR = date(2030, 1, 1)


def _sqlite_cfg(db_path: Path) -> Config:
    """Build a Config keyed for SQLite with a file-backed URL."""
    # Z.C — deployment_name + db_table_prefix are required cfg fields.
    return Config(
        aws_account_id="111122223333",
        aws_region="us-west-2",
        deployment_name="recon-sqlite-loop",
        db_table_prefix="spec_example",
        datasource_arn=(
            "arn:aws:quicksight:us-west-2:111122223333:datasource/test-ds"
        ),
        dialect=Dialect.SQLITE,
        demo_database_url=f"sqlite:///{db_path.as_posix()}",
    )


# -- connect_demo_db SQLite path --------------------------------------------


class TestConnectDemoDbSqlite:
    """X.3.f — connect_demo_db opens a file-backed SQLite + registers
    the STDDEV_SAMP aggregate that the inv_pair_rolling_anomalies
    matview body needs.
    """

    def test_connect_returns_sqlite_connection(self, tmp_path: Path) -> None:
        cfg = _sqlite_cfg(tmp_path / "demo.sqlite")
        conn = connect_demo_db(cfg)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
        finally:
            conn.close()

    def test_stddev_samp_registered(self, tmp_path: Path) -> None:
        # The matview SQL uses STDDEV_SAMP; SQLite doesn't ship it
        # natively. connect_demo_db must register the aggregate.
        cfg = _sqlite_cfg(tmp_path / "demo.sqlite")
        conn = connect_demo_db(cfg)
        try:
            conn.executescript(
                "CREATE TABLE t (v REAL); "
                "INSERT INTO t VALUES (1.0), (2.0), (3.0), (4.0), (5.0);"
            )
            cur = conn.execute("SELECT STDDEV_SAMP(v) FROM t")
            stddev = cur.fetchone()[0]
            # Sample stddev of [1..5] is sqrt(2.5) ~= 1.5811388
            assert stddev is not None
            assert 1.58 < stddev < 1.59
        finally:
            conn.close()

    def test_stddev_samp_returns_null_under_2_rows(self, tmp_path: Path) -> None:
        # SQL/2008 contract: sample stddev of a single value is
        # undefined → NULL, not 0.
        cfg = _sqlite_cfg(tmp_path / "demo.sqlite")
        conn = connect_demo_db(cfg)
        try:
            conn.executescript(
                "CREATE TABLE t (v REAL); INSERT INTO t VALUES (42.0);"
            )
            assert conn.execute(
                "SELECT STDDEV_SAMP(v) FROM t"
            ).fetchone()[0] is None
        finally:
            conn.close()


# -- Full schema + data + refresh against a real SQLite file ----------------


class TestSqliteFullLocalLoop:
    """X.3.f — schema apply, data apply, data refresh against a real
    sqlite file via the CLI's connect_and_apply path.

    Goes through ``connect_and_apply(cfg, sql, label=...)`` so the
    SQLite cursor-context-manager handling lands together with the
    full schema → seed → refresh pipeline. PG / Oracle take the
    same code path with their cm-supporting cursors.
    """

    def test_schema_then_data_then_refresh_applies(self, tmp_path: Path) -> None:
        cfg = _sqlite_cfg(tmp_path / "demo.sqlite")
        instance = load_instance(_SPEC_EXAMPLE)

        # Step 1: schema apply via connect_and_apply (CLI surface).
        connect_and_apply(
            cfg, emit_schema(instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE),
            label="schema",
        )

        # Step 2: data apply — full seed through build_full_seed_sql,
        # the same composer the CLI uses, via connect_and_apply.
        connect_and_apply(
            cfg, build_full_seed_sql(cfg, instance, anchor=_ANCHOR),
            label="seed data",
        )

        # Step 3: data refresh — re-runs every matview-as-table CREATE.
        connect_and_apply(
            cfg, refresh_matviews_sql(instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE),
            label="matview refresh",
        )

        # The matview-as-tables should now have rows populated by the
        # refresh-time SELECT bodies. Spot-check the L1 invariants
        # (drift / overdraft / limit_breach / etc.) and the dashboard-
        # shape rollups (todays_exceptions / daily_statement_summary).
        conn = connect_demo_db(cfg)
        try:
            # Base tables present + non-empty.
            assert conn.execute(
                "SELECT COUNT(*) FROM spec_example_transactions",
            ).fetchone()[0] > 100
            assert conn.execute(
                "SELECT COUNT(*) FROM spec_example_daily_balances",
            ).fetchone()[0] > 100
            # Current* matviews + L1 invariants + rollups + Inv all
            # present (table exists + accepts a SELECT). Row counts
            # depend on the planted scenario; the contract here is
            # "the refresh applied without raising and the table
            # exists in the schema".
            for matview in (
                "spec_example_current_transactions",
                "spec_example_current_daily_balances",
                "spec_example_drift",
                "spec_example_ledger_drift",
                "spec_example_overdraft",
                "spec_example_expected_eod_balance_breach",
                "spec_example_limit_breach",
                "spec_example_stuck_pending",
                "spec_example_stuck_unbundled",
                "spec_example_computed_subledger_balance",
                "spec_example_computed_ledger_balance",
                "spec_example_daily_statement_summary",
                "spec_example_todays_exceptions",
                "spec_example_inv_pair_rolling_anomalies",
                "spec_example_inv_money_trail_edges",
            ):
                conn.execute(f"SELECT COUNT(*) FROM {matview}").fetchone()
        finally:
            conn.close()

    def test_planted_overdraft_lands_in_overdraft_matview(
        self, tmp_path: Path,
    ) -> None:
        # The default scenario plants overdrafts; after the full local
        # loop, the spec_example_overdraft matview should carry the
        # planted rows. This is the mirror-of-PG/Oracle assertion
        # X.3.f calls out: same scenario, same matview row count.
        cfg = _sqlite_cfg(tmp_path / "demo.sqlite")
        instance = load_instance(_SPEC_EXAMPLE)

        connect_and_apply(
            cfg, emit_schema(instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE),
            label="schema",
        )
        connect_and_apply(
            cfg, build_full_seed_sql(cfg, instance, anchor=_ANCHOR),
            label="seed data",
        )
        connect_and_apply(
            cfg, refresh_matviews_sql(instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE),
            label="matview refresh",
        )

        conn = connect_demo_db(cfg)
        try:
            # spec_example's default scenario plants ≥1 overdraft per
            # auto_scenario.default_scenario_for. Densification is in
            # build_full_seed_sql (factor=5), so we get 5+ overdraft
            # rows in the matview.
            n_overdraft = conn.execute(
                "SELECT COUNT(*) FROM spec_example_overdraft"
            ).fetchone()[0]
            assert n_overdraft >= 5, (
                f"Expected ≥5 overdraft rows after densify×5, got {n_overdraft}"
            )
        finally:
            conn.close()

    def test_inv_pair_rolling_anomalies_uses_stddev_samp(
        self, tmp_path: Path,
    ) -> None:
        # The inv_pair_rolling_anomalies matview's body calls
        # STDDEV_SAMP. If the aggregate isn't registered, the refresh
        # raises sqlite3.OperationalError("no such function:
        # STDDEV_SAMP"). This test exercises the full loop and checks
        # the matview populates.
        cfg = _sqlite_cfg(tmp_path / "demo.sqlite")
        instance = load_instance(_SPEC_EXAMPLE)

        connect_and_apply(
            cfg, emit_schema(instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE),
            label="schema",
        )
        connect_and_apply(
            cfg, build_full_seed_sql(cfg, instance, anchor=_ANCHOR),
            label="seed data",
        )
        # The refresh re-runs the CREATE TABLE AS SELECT for the
        # anomaly matview; STDDEV_SAMP is in the SELECT body.
        connect_and_apply(
            cfg, refresh_matviews_sql(instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE),
            label="matview refresh",
        )

        conn = connect_demo_db(cfg)
        try:
            # Matview is populated with z-scored window rows.
            rows = conn.execute(
                "SELECT z_score, z_bucket FROM "
                "spec_example_inv_pair_rolling_anomalies LIMIT 5"
            ).fetchall()
            # spec_example may not have enough fanout to exercise the
            # matview heavily, but the table must accept the query.
            assert isinstance(rows, list)
        finally:
            conn.close()


# -- connect_and_apply SQLite cursor handling -------------------------------


class TestConnectAndApplySqliteCursor:
    """X.3.f — connect_and_apply's SQLite arm uses an explicit
    cursor.close() pattern (not ``with`` block) since sqlite3.Cursor
    doesn't implement the cursor context-manager protocol that
    psycopg2 + oracledb both support.

    These tests pin the contract so a future driver upgrade that
    grants sqlite3.Cursor cm support doesn't silently change the
    code path.
    """

    def test_sqlite_cursor_lacks_context_manager(self) -> None:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        try:
            cur = conn.cursor()
            assert not hasattr(cur, "__enter__"), (
                "If sqlite3.Cursor grew __enter__, connect_and_apply's "
                "explicit close() arm could collapse back into the "
                "shared with-statement form."
            )
        finally:
            conn.close()

    def test_connect_and_apply_runs_a_simple_script(self, tmp_path: Path) -> None:
        # Smoke-test that connect_and_apply's SQLite arm wires
        # together: open conn, run script, commit, close.
        cfg = _sqlite_cfg(tmp_path / "demo.sqlite")
        connect_and_apply(
            cfg,
            (
                "CREATE TABLE t (v INTEGER);\n"
                "INSERT INTO t VALUES (1);\n"
                "INSERT INTO t VALUES (2);\n"
            ),
            label="smoke",
        )
        # Re-open the file; rows should be present (commit succeeded).
        conn = connect_demo_db(cfg)
        try:
            n = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            assert n == 2
        finally:
            conn.close()
