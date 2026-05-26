"""Unit tests for the BC.12 config kv table — DDL emission + helpers.

Validates the BC.12 surface (replaces the pre-BC.12 AW.1 3-column shape):

1. ``emit_config_table_ddl(prefix, dialect)`` produces a valid CREATE
   TABLE + idx ``<prefix>_config_kv(node_id, parent_id, key, value)``.
2. ``emit_config_table_drop`` produces the matching DROP that's
   idempotent (safe on a missing table).
3. ``replace_config(conn, ...)`` walks the parsed cfg+L2 JSON into kv
   rows + inserts them — single-row invariant on ``as_of``, full tree
   on each side.
4. ``set_as_of(conn, as_of=None)`` defaults to CURRENT_TIMESTAMP;
   literal datetime pins.
5. ``get_as_of(conn)`` round-trips the stored value back as a datetime.
6. ``emit_schema`` integration — config_kv CREATE + typed-view CREATEs
   appear in the full schema output.

BC.12 specifics this gate locks:
- Walker emits parent-before-child ordering (FK satisfiability without
  FK constraint).
- ``l2_yaml`` / ``cfg_yaml`` containers anchor at ``parent_id IS NULL``.
- Typed projection views (``<prefix>_v_config_rails``,
  ``<prefix>_v_config_limit_schedules``) emit from ``emit_schema``.
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
    emit_config_populate_sql,
    emit_config_table_ddl,
    emit_config_table_drop,
    get_as_of,
    kv_rows_for,
    replace_config,
    set_as_of,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.sql import Dialect

_PREFIX = "spec_example"
_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)


def _fresh_db_with_config_only() -> sqlite3.Connection:
    """Minimal DB with JUST the config_kv table + index."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    execute_script(
        cur, emit_config_table_ddl(_PREFIX, Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
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


def test_config_table_name_follows_kv_suffix_convention() -> None:
    """BC.12 renamed from ``<prefix>_config`` → ``<prefix>_config_kv``
    so the suffix announces the shape change."""
    assert config_table_name(DEFAULT_PREFIX) == f"{DEFAULT_PREFIX}_config_kv"
    assert config_table_name("recon_prod") == "recon_prod_config_kv"


# ---------------------------------------------------------------------------
# DDL emission.
# ---------------------------------------------------------------------------


def test_ddl_sqlite_creates_kv_table_with_expected_columns() -> None:
    ddl = emit_config_table_ddl(_PREFIX, Dialect.SQLITE)
    assert "CREATE TABLE spec_example_config_kv" in ddl
    assert "node_id" in ddl
    assert "parent_id" in ddl
    assert "key" in ddl
    assert "value" in ddl
    # Index on the typed-view filter shape.
    assert "idx_spec_example_config_kv_parent_key" in ddl

    # Execute it.
    conn = sqlite3.connect(":memory:")
    try:
        cur = conn.cursor()
        execute_script(cur, ddl, dialect=Dialect.SQLITE)
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(spec_example_config_kv)",
            ).fetchall()
        }
    finally:
        conn.close()
    assert cols == {"node_id", "parent_id", "key", "value"}


def test_ddl_postgres_uses_text_value_type() -> None:
    """PG path emits TEXT for the value column (no VARCHAR2 limit)."""
    ddl = emit_config_table_ddl(_PREFIX, Dialect.POSTGRES)
    assert "VARCHAR(4000)" not in ddl  # unbounded per text_type
    assert "TEXT" in ddl
    # BIGINT for the node_id PK + parent_id self-ref.
    assert "BIGINT" in ddl


def test_drop_ddl_idempotent_on_missing_table() -> None:
    drop = emit_config_table_drop(_PREFIX, Dialect.SQLITE)
    assert "DROP TABLE IF EXISTS" in drop
    assert "spec_example_config_kv" in drop

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(drop)
    finally:
        conn.close()


def test_drop_then_recreate_works() -> None:
    """Re-deploy story: schema CREATE + DROP can both run safely on
    the same DB without "table already exists" errors."""
    create = emit_config_table_ddl(_PREFIX, Dialect.SQLITE)
    drop = emit_config_table_drop(_PREFIX, Dialect.SQLITE)

    conn = sqlite3.connect(":memory:")
    try:
        cur = conn.cursor()
        execute_script(cur, create, dialect=Dialect.SQLITE)
        conn.execute(drop)
        execute_script(cur, create, dialect=Dialect.SQLITE)
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(spec_example_config_kv)",
            ).fetchall()
        }
    finally:
        conn.close()
    assert cols == {"node_id", "parent_id", "key", "value"}


# ---------------------------------------------------------------------------
# Walker.
# ---------------------------------------------------------------------------


def test_walker_emits_parent_before_children() -> None:
    """FK satisfiability — even without an actual FK constraint, every
    parent_id reference must point to a node_id earlier in the row order."""
    rows = kv_rows_for(
        cfg_json="{}",
        l2_json=json.dumps({"rails": [{"name": "ACH"}]}),
        as_of=datetime(2030, 1, 1),
    )
    seen: set[int] = set()
    for node_id, parent_id, _key, _value in rows:
        if parent_id is not None:
            assert parent_id in seen, (
                f"node {node_id} parent_id={parent_id} unseen — "
                f"out-of-order walk breaks FK direction"
            )
        seen.add(node_id)


def test_walker_emits_as_of_at_top_level() -> None:
    rows = kv_rows_for(
        cfg_json="{}", l2_json="{}",
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    as_of_rows = [r for r in rows if r[2] == "as_of"]
    assert len(as_of_rows) == 1
    assert as_of_rows[0][1] is None  # parent_id NULL → top level
    assert as_of_rows[0][3] == "2030-01-01 12:00:00"


def test_walker_emits_l2_container_at_top_level() -> None:
    """The ``l2_yaml`` container row anchors the typed projection views'
    walk (root.key='l2_yaml' AND root.parent_id IS NULL)."""
    rows = kv_rows_for(
        cfg_json="{}", l2_json=json.dumps({"rails": []}),
        as_of=datetime(2030, 1, 1),
    )
    l2_root = [r for r in rows if r[2] == "l2_yaml" and r[1] is None]
    assert len(l2_root) == 1


# ---------------------------------------------------------------------------
# replace_config.
# ---------------------------------------------------------------------------


def test_replace_config_inserts_kv_rows() -> None:
    conn = _fresh_db_with_config_only()
    try:
        replace_config(
            conn,
            prefix=_PREFIX,
            cfg_json=json.dumps({"db_url": "postgres://..."}),
            l2_json=json.dumps({"rails": [{"name": "ACH"}]}),
            as_of=datetime(2030, 1, 1, 12, 0, 0),
        )
        rows = conn.execute(
            "SELECT node_id, parent_id, key, value FROM spec_example_config_kv "
            "ORDER BY node_id"
        ).fetchall()
    finally:
        conn.close()
    # >= 4 rows: as_of + l2_yaml container + rails container + ACH name leaf.
    assert len(rows) >= 4
    # as_of value lives at parent_id IS NULL, key='as_of'.
    as_of_rows = [r for r in rows if r[1] is None and r[2] == "as_of"]
    assert len(as_of_rows) == 1
    assert as_of_rows[0][3] == "2030-01-01 12:00:00"
    # The rail name 'ACH' is in there somewhere.
    name_rows = [r for r in rows if r[2] == "name" and r[3] == "ACH"]
    assert len(name_rows) == 1


def test_replace_config_replaces_existing_rows() -> None:
    """Two successive replace_config calls — second wins, DELETE-before
    -INSERT keeps the table at exactly the second walk's row set."""
    conn = _fresh_db_with_config_only()
    try:
        replace_config(
            conn, prefix=_PREFIX,
            cfg_json="{}",
            l2_json=json.dumps({"rails": [{"name": "A"}]}),
            as_of=datetime(2030, 1, 1),
        )
        first_count = conn.execute(
            "SELECT COUNT(*) FROM spec_example_config_kv",
        ).fetchone()[0]
        replace_config(
            conn, prefix=_PREFIX,
            cfg_json="{}",
            l2_json=json.dumps({"rails": [{"name": "B"}, {"name": "C"}]}),
            as_of=datetime(2030, 6, 1),
        )
        second_count = conn.execute(
            "SELECT COUNT(*) FROM spec_example_config_kv",
        ).fetchone()[0]
        # The second walk has 2 rails → more rows than the first.
        assert second_count > first_count
        # The first walk's 'A' rail is GONE — single-row invariant on
        # the populate side (TRUNCATE-then-INSERT).
        a_rows = conn.execute(
            "SELECT COUNT(*) FROM spec_example_config_kv "
            "WHERE key = 'name' AND value = 'A'",
        ).fetchone()[0]
        assert a_rows == 0
    finally:
        conn.close()


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
            "SELECT value FROM spec_example_config_kv "
            "WHERE parent_id IS NULL AND key = 'as_of'",
        ).fetchone()
    finally:
        conn.close()
    assert row == ("2027-04-15 14:30:00",)


def test_set_as_of_none_uses_current_timestamp() -> None:
    """Production default — as_of=None updates to "now" via SQLite's
    strftime('%Y-%m-%d %H:%M:%S', 'now')."""
    conn = _fresh_db_with_config_only()
    try:
        initial = datetime(2020, 1, 1)
        replace_config(
            conn, prefix=_PREFIX, cfg_json="{}", l2_json="{}",
            as_of=initial,
        )
        set_as_of(conn, prefix=_PREFIX, as_of=None)
        row = conn.execute(
            "SELECT value FROM spec_example_config_kv "
            "WHERE parent_id IS NULL AND key = 'as_of'",
        ).fetchone()
    finally:
        conn.close()
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
    """The single-as_of-row invariant: if no replace_config has run,
    get_as_of fails loud."""
    conn = _fresh_db_with_config_only()
    try:
        with pytest.raises(RuntimeError, match="has no row"):
            get_as_of(conn, prefix=_PREFIX)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# emit_schema integration.
# ---------------------------------------------------------------------------


def test_emit_schema_includes_config_kv_table_create() -> None:
    sql = emit_schema(
        load_instance(_SPEC_EXAMPLE),
        prefix=_PREFIX, dialect=Dialect.SQLITE,
    )
    assert "CREATE TABLE spec_example_config_kv" in sql


def test_emit_schema_includes_typed_projection_views() -> None:
    """BC.12: matview JOIN target views — must be in the emitted schema
    on every dialect, between config_kv CREATE and matview CREATEs."""
    for dialect in (Dialect.SQLITE, Dialect.POSTGRES, Dialect.ORACLE):
        sql = emit_schema(
            load_instance(_SPEC_EXAMPLE),
            prefix=_PREFIX, dialect=dialect,
        )
        assert f"CREATE VIEW spec_example_v_config_rails AS" in sql, (
            f"missing rails view on {dialect}"
        )
        assert f"CREATE VIEW spec_example_v_config_limit_schedules AS" in sql, (
            f"missing limit_schedules view on {dialect}"
        )


def test_emit_schema_no_json_table_anywhere() -> None:
    """BC.12 invariant: no matview body iterates JSON via JSON_TABLE.
    The typed projection views replaced the JSON_TABLE pattern; if
    JSON_TABLE creeps back in, the Oracle CI cell will red on the next
    matview CREATE (ORA-32368)."""
    for dialect in (Dialect.POSTGRES, Dialect.ORACLE):
        sql = emit_schema(
            load_instance(_SPEC_EXAMPLE),
            prefix=_PREFIX, dialect=dialect,
        )
        assert "JSON_TABLE" not in sql, (
            f"JSON_TABLE found in {dialect} schema — BC.12 regression"
        )


def test_full_schema_applies_cleanly_with_config_kv_table() -> None:
    """End-to-end: emit_schema produces SQL that initializes the kv
    table + typed views + matviews alongside everything else."""
    conn = _fresh_db_with_full_schema()
    try:
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(spec_example_config_kv)",
            ).fetchall()
        }
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name LIKE 'spec_example_%'",
            ).fetchall()
        }
    finally:
        conn.close()
    assert cols == {"node_id", "parent_id", "key", "value"}
    assert "spec_example_config_kv" in tables
    assert "spec_example_transactions" in tables
    assert "spec_example_daily_balances" in tables
    assert "spec_example_v_config_rails" in tables
    assert "spec_example_v_config_limit_schedules" in tables


def test_replace_config_works_against_full_schema() -> None:
    """The helpers work against a fully-emitted schema (not just the
    isolated-config_kv harness). After populate, the typed views
    project the rails the matview would see."""
    conn = _fresh_db_with_full_schema()
    try:
        instance = load_instance(_SPEC_EXAMPLE)
        # Serialize the L2 to JSON via the serializer round-trip.
        from recon_gen.common.l2.serializer import serialize_l2
        import yaml
        l2_dict = yaml.safe_load(serialize_l2(instance))
        replace_config(
            conn, prefix=_PREFIX,
            cfg_json="{}",
            l2_json=json.dumps(l2_dict),
            as_of=datetime(2030, 1, 1),
        )
        # as_of round-trips.
        assert get_as_of(conn, prefix=_PREFIX) == datetime(2030, 1, 1)
        # Typed view projects rails — at least one row.
        rails = conn.execute(
            "SELECT name, max_pending_age_seconds FROM spec_example_v_config_rails"
        ).fetchall()
        assert len(rails) >= 1
    finally:
        conn.close()


def test_drop_schema_includes_config_kv_drop_and_typed_view_drops() -> None:
    """Teardown drops both the kv table AND the typed views."""
    from recon_gen.common.l2.schema import emit_schema_drop_sql
    sql = emit_schema_drop_sql(
        load_instance(_SPEC_EXAMPLE),
        prefix=_PREFIX, dialect=Dialect.SQLITE,
    )
    assert "DROP TABLE IF EXISTS spec_example_config_kv" in sql
    assert "DROP VIEW IF EXISTS spec_example_v_config_rails" in sql
    assert "DROP VIEW IF EXISTS spec_example_v_config_limit_schedules" in sql


# ---------------------------------------------------------------------------
# emit_config_populate_sql.
# ---------------------------------------------------------------------------


def test_emit_config_populate_sql_starts_with_delete() -> None:
    """The populate is DELETE + N INSERTs — DELETE first to enforce
    populate-from-scratch semantics."""
    sql = emit_config_populate_sql(
        prefix=_PREFIX,
        cfg_json="{}",
        l2_json=json.dumps({"rails": [{"name": "ACH"}]}),
        as_of=datetime(2030, 1, 1),
        dialect=Dialect.SQLITE,
    )
    lines = [line for line in sql.split("\n") if line.strip()]
    assert lines[0].startswith("DELETE FROM spec_example_config_kv")
    # The rest are INSERTs.
    assert all(
        line.startswith("INSERT INTO spec_example_config_kv") for line in lines[1:]
    ), "every non-DELETE line should be an INSERT INTO config_kv"
