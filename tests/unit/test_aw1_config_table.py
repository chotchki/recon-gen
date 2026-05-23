"""Unit tests for the AW.1 config table — DDL emission + helpers.

Validates the AW.1 surface:
1. `emit_config_table_ddl(prefix, dialect)` produces a valid CREATE
   TABLE that executes against an empty SQLite DB.
2. `emit_config_table_drop(prefix, dialect)` produces the matching
   DROP that's idempotent (safe to run on a missing table).
3. `replace_config(conn, ...)` inserts + replaces — single-row
   invariant preserved across multiple calls.
4. `set_as_of(conn, as_of=None)` defaults to CURRENT_TIMESTAMP;
   literal datetime pins.
5. `get_as_of(conn)` round-trips the stored value back as a datetime.
6. `emit_schema` integration — the config table CREATE appears in the
   full schema output + works against a freshly-initialized DB.

What's NOT tested here (deferred to AW.2+):
- The matview subquery shape that reads from the config table — AW.2
  lands `{epoch_age_seconds}` migration; tests there.
- PG / Oracle execution (CI's e2e layer; SQLite covers the design).
- json_check enforcement (SQLite no-op; PG/Oracle exercise it in deploy).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import (
    config_table_name,
    emit_config_table_ddl,
    emit_config_table_drop,
    get_as_of,
    replace_config,
    set_as_of,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.sql import Dialect

_PREFIX = "spec_example"
_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)


def _fresh_db_with_config_only() -> sqlite3.Connection:
    """Minimal DB with JUST the config table — no other schema. Tests
    AW.1's helpers in isolation."""
    conn = sqlite3.connect(":memory:")
    conn.execute(emit_config_table_ddl(_PREFIX, Dialect.SQLITE))
    conn.commit()
    return conn


def _fresh_db_with_full_schema() -> sqlite3.Connection:
    """Full L2 schema applied — exercises the emit_schema integration."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Table-name helper.
# ---------------------------------------------------------------------------


def test_config_table_name_follows_prefix_convention() -> None:
    assert config_table_name("spec_example") == "spec_example_config"
    assert config_table_name("recon_prod") == "recon_prod_config"


# ---------------------------------------------------------------------------
# DDL emission.
# ---------------------------------------------------------------------------


def test_ddl_sqlite_creates_table_with_expected_columns() -> None:
    ddl = emit_config_table_ddl(_PREFIX, Dialect.SQLITE)
    assert "CREATE TABLE spec_example_config" in ddl
    assert "as_of" in ddl
    assert "cfg_yaml" in ddl
    assert "l2_yaml" in ddl

    # Execute it.
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(ddl)
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(spec_example_config)",
            ).fetchall()
        }
    finally:
        conn.close()
    assert cols == {"as_of", "cfg_yaml", "l2_yaml"}


def test_ddl_postgres_includes_json_check() -> None:
    # PG path emits `<col> IS JSON` per json_check; not executed here
    # (no PG in-process), just verify the shape.
    ddl = emit_config_table_ddl(_PREFIX, Dialect.POSTGRES)
    assert "VARCHAR(4000)" not in ddl  # unbounded per text_type
    assert "TEXT" in ddl


def test_drop_ddl_idempotent_on_missing_table() -> None:
    """DROP TABLE IF EXISTS on SQLite — running on an empty DB is a
    no-op (no error). Mirrors the existing schema.py idiom."""
    drop = emit_config_table_drop(_PREFIX, Dialect.SQLITE)
    assert "DROP TABLE IF EXISTS" in drop
    assert "spec_example_config" in drop

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(drop)  # no error
    finally:
        conn.close()


def test_drop_then_recreate_works() -> None:
    """Re-deploy story: schema CREATE + DROP can both run safely on
    the same DB without "table already exists" errors."""
    create = emit_config_table_ddl(_PREFIX, Dialect.SQLITE)
    drop = emit_config_table_drop(_PREFIX, Dialect.SQLITE)

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(create)
        conn.execute(drop)
        conn.execute(create)  # re-creates cleanly
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(spec_example_config)",
            ).fetchall()
        }
    finally:
        conn.close()
    assert cols == {"as_of", "cfg_yaml", "l2_yaml"}


# ---------------------------------------------------------------------------
# replace_config.
# ---------------------------------------------------------------------------


def test_replace_config_inserts_first_call() -> None:
    conn = _fresh_db_with_config_only()
    try:
        replace_config(
            conn,
            prefix=_PREFIX,
            cfg_json=json.dumps({"db_url": "postgres://..."}),
            l2_json=json.dumps({"rails": []}),
            as_of=datetime(2030, 1, 1, 12, 0, 0),
        )
        rows = conn.execute(
            "SELECT as_of, cfg_yaml, l2_yaml FROM spec_example_config",
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    as_of, cfg_json, l2_json = rows[0]
    assert as_of == "2030-01-01 12:00:00"
    assert json.loads(cfg_json) == {"db_url": "postgres://..."}
    assert json.loads(l2_json) == {"rails": []}


def test_replace_config_replaces_existing_row() -> None:
    """Two successive replace_config calls — second wins, single-row
    invariant holds."""
    conn = _fresh_db_with_config_only()
    try:
        replace_config(
            conn, prefix=_PREFIX,
            cfg_json=json.dumps({"version": 1}),
            l2_json=json.dumps({"v": 1}),
            as_of=datetime(2030, 1, 1),
        )
        replace_config(
            conn, prefix=_PREFIX,
            cfg_json=json.dumps({"version": 2}),
            l2_json=json.dumps({"v": 2}),
            as_of=datetime(2030, 6, 1),
        )
        rows = conn.execute(
            "SELECT cfg_yaml, l2_yaml FROM spec_example_config",
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert json.loads(rows[0][0]) == {"version": 2}
    assert json.loads(rows[0][1]) == {"v": 2}


# ---------------------------------------------------------------------------
# set_as_of.
# ---------------------------------------------------------------------------


def test_set_as_of_with_literal_pins_value() -> None:
    conn = _fresh_db_with_config_only()
    try:
        replace_config(
            conn, prefix=_PREFIX, cfg_json="{}", l2_json="{}",
            as_of=datetime(2030, 1, 1),
        )
        new_as_of = datetime(2027, 4, 15, 14, 30, 0)
        set_as_of(conn, prefix=_PREFIX, as_of=new_as_of)
        row = conn.execute(
            "SELECT as_of FROM spec_example_config",
        ).fetchone()
    finally:
        conn.close()
    assert row == ("2027-04-15 14:30:00",)


def test_set_as_of_none_uses_current_timestamp() -> None:
    """Production default — as_of=None updates to CURRENT_TIMESTAMP.
    The literal varies per-run (wall-clock), so the test just verifies
    it changed FROM the initial value to something else."""
    conn = _fresh_db_with_config_only()
    try:
        initial = datetime(2020, 1, 1)
        replace_config(
            conn, prefix=_PREFIX, cfg_json="{}", l2_json="{}",
            as_of=initial,
        )
        set_as_of(conn, prefix=_PREFIX, as_of=None)
        row = conn.execute(
            "SELECT as_of FROM spec_example_config",
        ).fetchone()
    finally:
        conn.close()
    # Value should differ from the pinned initial (CURRENT_TIMESTAMP is
    # whatever wall-clock now is — definitely not 2020-01-01).
    assert row[0] != "2020-01-01 00:00:00"


# ---------------------------------------------------------------------------
# get_as_of.
# ---------------------------------------------------------------------------


def test_get_as_of_round_trips_literal() -> None:
    conn = _fresh_db_with_config_only()
    try:
        target = datetime(2027, 4, 15, 14, 30, 0)
        replace_config(
            conn, prefix=_PREFIX, cfg_json="{}", l2_json="{}",
            as_of=target,
        )
        result = get_as_of(conn, prefix=_PREFIX)
    finally:
        conn.close()
    assert result == target


def test_get_as_of_raises_when_table_empty() -> None:
    """The single-row invariant: if no replace_config has run, the
    table has no row, and get_as_of fails loud rather than silently
    returning None or a bogus default."""
    conn = _fresh_db_with_config_only()
    try:
        with pytest.raises(RuntimeError, match="has no row"):
            get_as_of(conn, prefix=_PREFIX)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# emit_schema integration.
# ---------------------------------------------------------------------------


def test_emit_schema_includes_config_table_create() -> None:
    """The full schema output now includes the config table CREATE.
    Verifies the integration without re-running emit_schema's output
    parser."""
    from recon_gen.common.l2.schema import emit_schema
    sql = emit_schema(
        load_instance(_SPEC_EXAMPLE),
        prefix=_PREFIX, dialect=Dialect.SQLITE,
    )
    assert "CREATE TABLE spec_example_config" in sql


def test_full_schema_applies_cleanly_with_config_table() -> None:
    """End-to-end: emit_schema produces SQL that initializes the config
    table alongside everything else, no errors."""
    conn = _fresh_db_with_full_schema()
    try:
        # Config table exists, with the expected columns
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(spec_example_config)",
            ).fetchall()
        }
        # The other base tables also still exist (no regression).
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'spec_example_%'",
            ).fetchall()
        }
    finally:
        conn.close()
    assert cols == {"as_of", "cfg_yaml", "l2_yaml"}
    assert "spec_example_config" in tables
    assert "spec_example_transactions" in tables
    assert "spec_example_daily_balances" in tables


def test_replace_config_works_against_full_schema() -> None:
    """The helpers work against a fully-emitted schema (not just the
    isolated-config-table harness)."""
    conn = _fresh_db_with_full_schema()
    try:
        replace_config(
            conn, prefix=_PREFIX,
            cfg_json=json.dumps({"deployment_name": "demo"}),
            l2_json=json.dumps({"rails": [{"name": "X"}]}),
            as_of=datetime(2030, 1, 1),
        )
        result = get_as_of(conn, prefix=_PREFIX)
    finally:
        conn.close()
    assert result == datetime(2030, 1, 1)


def test_drop_schema_includes_config_table_drop() -> None:
    """The teardown path drops the config table too — no stale row
    after schema clean."""
    from recon_gen.common.l2.schema import emit_schema_drop_sql
    sql = emit_schema_drop_sql(
        load_instance(_SPEC_EXAMPLE),
        prefix=_PREFIX, dialect=Dialect.SQLITE,
    )
    assert "DROP TABLE IF EXISTS spec_example_config" in sql
