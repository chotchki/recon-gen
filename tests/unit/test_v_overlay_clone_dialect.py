"""DI.1 — pin the dialect-specific shape of ``clone_base_to_v_sql`` so
the DuckDB CTAS fast path doesn't silently regress to DELETE+INSERT
(or vice versa for PG/Oracle).

Pure SQL-string assertions — no DB needed. The end-to-end correctness
(plant INSERTs work after CTAS, matview refresh sees the cloned data)
is covered by ``test_bv49_diff_apply.py`` + ``test_plant_contract.py``
which exercise the full session_start → apply pipeline against a real
DuckDB v overlay.
"""

from __future__ import annotations

from recon_gen.common.l2.v_overlay import (
    clone_base_to_v_sql,
    realign_v_overlay_entry_sequences_sql,
    v_overlay_prefix,
)
from recon_gen.common.sql.dialect import Dialect


# -- clone_base_to_v_sql dialect branching ---------------------------------


def test_clone_duckdb_emits_create_or_replace_table_as_select() -> None:
    """DI.1 fast path — DuckDB clone is CTAS (columnar bulk load, ~57×
    faster than DELETE+INSERT on a populated v overlay). Must NOT
    fall through to the DELETE+INSERT shape on DuckDB."""
    sql = clone_base_to_v_sql("demo", dialect=Dialect.DUCKDB)
    v = v_overlay_prefix("demo")
    # CTAS form per base table — drops + recreates atomically.
    assert f"CREATE OR REPLACE TABLE {v}_transactions AS " in sql
    assert f"CREATE OR REPLACE TABLE {v}_daily_balances AS " in sql
    assert f"CREATE OR REPLACE TABLE {v}_config_kv AS " in sql
    assert f"SELECT * FROM demo_transactions" in sql
    assert f"SELECT * FROM demo_daily_balances" in sql
    assert f"SELECT * FROM demo_config_kv" in sql
    # Should NOT emit DELETE on DuckDB — that's the perf regression
    # this branch exists to avoid.
    assert "DELETE FROM" not in sql
    assert "INSERT INTO" not in sql


def test_clone_postgres_keeps_delete_insert_shape() -> None:
    """PG preserves the table's PK / CHECK / sequence DEFAULT, so the
    existing DELETE+INSERT pattern stays the right shape (TRUNCATE
    isn't a win on PG either)."""
    sql = clone_base_to_v_sql("demo", dialect=Dialect.POSTGRES)
    v = v_overlay_prefix("demo")
    assert f"DELETE FROM {v}_transactions;" in sql
    assert f"DELETE FROM {v}_daily_balances;" in sql
    assert f"DELETE FROM {v}_config_kv;" in sql
    assert (
        f"INSERT INTO {v}_transactions SELECT * FROM demo_transactions;" in sql
    )
    assert (
        f"INSERT INTO {v}_daily_balances "
        f"SELECT * FROM demo_daily_balances;" in sql
    )
    # CTAS must NOT leak into the PG branch.
    assert "CREATE OR REPLACE TABLE" not in sql


def test_clone_oracle_keeps_delete_insert_shape() -> None:
    """Oracle's test isolation is sensitive (BV.3.3.c stabilization);
    keep the DELETE+INSERT pattern unchanged."""
    sql = clone_base_to_v_sql("demo", dialect=Dialect.ORACLE)
    v = v_overlay_prefix("demo")
    assert f"DELETE FROM {v}_transactions;" in sql
    assert (
        f"INSERT INTO {v}_transactions SELECT * FROM demo_transactions;" in sql
    )
    assert "CREATE OR REPLACE TABLE" not in sql


def test_clone_default_dialect_falls_through_to_delete_insert() -> None:
    """Backward-compat: callers that pre-date the dialect kwarg pass
    nothing (None) and get the legacy DELETE+INSERT shape — correct on
    every dialect, just slower on DuckDB."""
    sql_none = clone_base_to_v_sql("demo")
    sql_explicit_none = clone_base_to_v_sql("demo", dialect=None)
    assert sql_none == sql_explicit_none
    v = v_overlay_prefix("demo")
    assert f"DELETE FROM {v}_transactions;" in sql_none
    assert "CREATE OR REPLACE TABLE" not in sql_none


# -- realign_v_overlay_entry_sequences_sql shape ---------------------------


def test_realign_emits_drop_create_alter_for_both_base_tables() -> None:
    """After DuckDB CTAS the v overlay's entry-column DEFAULT is gone;
    realign drops + recreates the entry sequences past max(entry) and
    re-attaches them via ALTER COLUMN SET DEFAULT."""
    sql = realign_v_overlay_entry_sequences_sql(
        "demo", max_tx_entry=42, max_db_entry=17,
    )
    v = v_overlay_prefix("demo")
    tx_seq = f"{v}_transactions_entry_seq"
    db_seq = f"{v}_daily_balances_entry_seq"

    # Each sequence gets drop+create+alter.
    assert f"DROP SEQUENCE IF EXISTS {tx_seq};" in sql
    assert f"DROP SEQUENCE IF EXISTS {db_seq};" in sql
    # Start past max so plant nextval()s don't collide with cloned
    # base rows (entries 1..max from base).
    assert f"CREATE SEQUENCE {tx_seq} START WITH 43;" in sql
    assert f"CREATE SEQUENCE {db_seq} START WITH 18;" in sql
    # ALTER COLUMN re-attaches the DEFAULT — plant INSERTs that omit
    # ``entry`` pick the sequence up again.
    assert (
        f"ALTER TABLE {v}_transactions ALTER COLUMN entry "
        f"SET DEFAULT nextval('{tx_seq}');" in sql
    )
    assert (
        f"ALTER TABLE {v}_daily_balances ALTER COLUMN entry "
        f"SET DEFAULT nextval('{db_seq}');" in sql
    )


def test_realign_handles_empty_v_overlay() -> None:
    """When max(entry) is 0 (the v base tables are empty — shouldn't
    happen post-clone but defensive), the sequence starts at 1 so
    plant INSERTs immediately get well-formed entry values."""
    sql = realign_v_overlay_entry_sequences_sql(
        "demo", max_tx_entry=0, max_db_entry=0,
    )
    v = v_overlay_prefix("demo")
    assert f"CREATE SEQUENCE {v}_transactions_entry_seq START WITH 1;" in sql
    assert f"CREATE SEQUENCE {v}_daily_balances_entry_seq START WITH 1;" in sql
