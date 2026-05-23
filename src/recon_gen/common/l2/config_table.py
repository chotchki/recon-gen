"""``<prefix>_config`` — owns the temporal frame + L2/cfg accessible via SQL.

Per Phase AW (audit §6 "Own the temporal frame in config"). Single row,
JSON blobs for cfg + L2 yaml content, typed ``as_of`` sibling column
for hot reads. The user-accepted **only** relaxation of the
two-table rule because the table is DERIVED from cfg+L2 — Python
populates it; never operator-mutated; mirrors source YAML 1:1.

Operational model (the deploy vs ETL split the user confirmed):

- **Deploy event** (cfg.yaml or L2.yaml change): `replace_config`
  REPLACEs the row with new cfg + L2 + initial as_of. Matviews refresh
  (DROP+CREATE on SQLite, REFRESH MATERIALIZED VIEW on PG) so any
  cfg-derived value changes propagate.
- **Daily ETL event** (data load, cfg unchanged): `set_as_of` UPDATEs
  only as_of. Default `None` → ``CURRENT_TIMESTAMP`` (production —
  matview age uses "right now"). Literal datetime → pinned (tests —
  deterministic matview age).

Matview bodies read ``as_of`` (and per-L2 values from cfg/L2 JSON) via
subquery + LEFT JOIN against this table. Design validated by
``tests/unit/test_aw0_matview_as_of_spike.py`` +
``tests/unit/test_aw0b_jsonpath_filter_spike.py``.

`json_check` on cfg_yaml + l2_yaml enforces JSON validity on
PG/Oracle (SQLite ignores the constraint — its JSON1 doesn't validate
at insert; matview readers tolerate malformed JSON by returning NULL
on path extracts, which the matview's outer WHERE filters out).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from recon_gen.common.sql.dialect import (
    Dialect,
    json_check,
    text_type,
    timestamp_type,
)


def config_table_name(prefix: str) -> str:
    """The canonical ``<prefix>_config`` table name."""
    return f"{prefix}_config"


def emit_config_table_ddl(prefix: str, dialect: Dialect) -> str:
    """``CREATE TABLE <prefix>_config`` DDL for the given dialect.

    Column shape:
    - ``as_of TIMESTAMP NOT NULL`` — typed (every matview age formula
      reads this as a real timestamp; JSON path extraction would force
      a string-to-timestamp cast per query).
    - ``cfg_yaml`` — unbounded TEXT/CLOB; carries the deploy cfg.yaml
      content serialized to JSON. The size can exceed VARCHAR(4000)
      for large cfgs; aggregation isn't a concern (single row).
    - ``l2_yaml`` — same shape for L2 yaml content.

    ``json_check`` enforces JSON validity on PG/Oracle; SQLite no-op.
    """
    name = config_table_name(prefix)
    ts = timestamp_type(dialect)
    tt = text_type(dialect)
    cfg_check = json_check("cfg_yaml", dialect)
    l2_check = json_check("l2_yaml", dialect)
    checks = "".join(
        f",\n    {c}" for c in (cfg_check, l2_check) if c
    )
    return (
        f"CREATE TABLE {name} (\n"
        f"    as_of    {ts} NOT NULL,\n"
        f"    cfg_yaml {tt} NOT NULL,\n"
        f"    l2_yaml  {tt} NOT NULL"
        f"{checks}\n"
        f");"
    )


def emit_config_table_drop(prefix: str, dialect: Dialect) -> str:
    """``DROP TABLE IF EXISTS <prefix>_config;`` DDL for re-runs."""
    name = config_table_name(prefix)
    # Same idiom as the rest of schema.py — IF EXISTS on all three
    # dialects.
    if dialect is Dialect.ORACLE:
        # Oracle has no IF EXISTS; the BEGIN/EXCEPTION dance handles
        # missing-table without erroring.
        return (
            f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {name}'; "
            f"EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; "
            f"END IF; END;\n/"
        )
    return f"DROP TABLE IF EXISTS {name};"


def replace_config(
    conn: sqlite3.Connection,
    *,
    prefix: str,
    cfg_json: str,
    l2_json: str,
    as_of: datetime,
) -> None:
    """**Deploy event** — full-row replace.

    DELETE + INSERT preserves the single-row invariant without relying
    on dialect-specific UPSERT. The caller is responsible for
    serializing cfg + L2 to JSON strings (typically via
    ``dataclasses.asdict`` + ``json.dumps``, or by reading the source
    YAML and re-serializing to JSON).
    """
    name = config_table_name(prefix)
    conn.execute(f"DELETE FROM {name}")
    conn.execute(
        f"INSERT INTO {name} (as_of, cfg_yaml, l2_yaml) VALUES (?, ?, ?)",
        (as_of.strftime("%Y-%m-%d %H:%M:%S"), cfg_json, l2_json),
    )
    conn.commit()


def set_as_of(
    conn: sqlite3.Connection,
    *,
    prefix: str,
    as_of: datetime | None = None,
) -> None:
    """**ETL refresh event** — update only as_of.

    ``as_of=None`` (production default) → ``UPDATE ... SET as_of =
    CURRENT_TIMESTAMP``; matview age formulas pick up "right now" at
    refresh time. Literal datetime → pinned at that value (tests +
    backfill scenarios).

    Assumes the config row already exists — call `replace_config` once
    at deploy/init time before any `set_as_of` calls.
    """
    name = config_table_name(prefix)
    if as_of is None:
        conn.execute(f"UPDATE {name} SET as_of = CURRENT_TIMESTAMP")
    else:
        conn.execute(
            f"UPDATE {name} SET as_of = ?",
            (as_of.strftime("%Y-%m-%d %H:%M:%S"),),
        )
    conn.commit()


def get_as_of(conn: sqlite3.Connection, *, prefix: str) -> datetime:
    """Read the current ``as_of`` value back as a Python datetime.

    Convenience for spine generators: at scenario_for time, the
    generator can read the as_of the matview WILL use at next refresh,
    keeping plant + matview in sync. Matches the "plant + matview read
    from one as_of source" invariant the AW.0 spike pinned.
    """
    name = config_table_name(prefix)
    row = conn.execute(f"SELECT as_of FROM {name}").fetchone()
    if row is None:
        raise RuntimeError(
            f"{name} has no row — call replace_config(...) before "
            f"get_as_of(...) or set_as_of(...)."
        )
    return _parse_timestamp(row[0])


def _parse_timestamp(value: object) -> datetime:
    """Parse the various TIMESTAMP representations DB drivers return.

    psycopg2 returns datetime; oracledb returns datetime; sqlite3
    returns str. Handle all three uniformly."""
    if isinstance(value, datetime):
        return value
    s = str(value)
    # Tolerate fractional seconds (PG/Oracle CURRENT_TIMESTAMP) by
    # truncating to whole-second precision.
    if "." in s:
        s = s.split(".", 1)[0]
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
