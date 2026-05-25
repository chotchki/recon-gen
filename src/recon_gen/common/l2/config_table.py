"""``<prefix>_config_kv`` — flattened cfg + L2 JSON tree (BC.12).

Originally a 3-column ``<prefix>_config(as_of, cfg_yaml, l2_yaml)``
single-row table (Phase AW). Replaced in BC.12 to dodge Oracle's
**ORA-32368: cannot create JSON materialized view without relational
table** — Oracle 19c+ refuses to build a matview whose source is
``JSON_TABLE`` of a CLOB column. The kv flattening lets matviews JOIN
typed projection views (`<prefix>_v_config_rails` etc.) whose own
bodies are plain self-joins on relational columns; the matview engine
sees a relational source, not a JSON_TABLE-of-CLOB.

Table shape::

    <prefix>_config_kv(
        node_id   BIGINT       PRIMARY KEY,
        parent_id BIGINT       NULL,   -- self-ref, NULL for roots
        key       VARCHAR(255) NULL,   -- JSON key / array index
        value     TEXT         NULL    -- CLOB on Oracle (holds l2_yaml_raw)
    )

Walk semantics (Python-side at populate time):

- Top-level scalars (``as_of``, ``l2_yaml_raw`` opaque provenance) live
  at ``parent_id IS NULL`` as flat rows.
- Nested structures (rails, limit_schedules, etc.) walk recursively;
  each container gets a row with ``value IS NULL`` (its descendants
  carry the data); each scalar leaf gets a row with the scalar value.
- Array elements: ``key`` is the stringified index (``'0'``, ``'1'``…).
- Object fields: ``key`` is the field name.

Operational lifecycle (BC.12 lock):

1. **Once** — ``schema apply --execute`` creates ``<prefix>_config_kv``.
2. **Every deploy** (L2 changes) — ``schema apply --execute`` re-creates
   matviews + repopulates ``<prefix>_config_kv`` (TRUNCATE + INSERT-N
   from the parsed cfg+L2 JSON).
3. **Daily** (post-ETL) — ``data refresh --execute`` REFRESHes matviews.

Indexed on ``(parent_id, key)`` — the typed views' filter shape. No
index on ``value`` (CLOB-incompatible on Oracle without a function-
based index; the typed views walk by parent_id+key, never by value).

Migration from the pre-BC.12 ``<prefix>_config`` table: existing
customer deploys had ``<prefix>_config`` empty (BC.6 surfaced
production never populated it), so the "migration" is just
``schema apply --execute`` against the v11.19.0 schema — no data
loss because there was no data.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any  # typing-smell: ignore[explicit-any]: kv values are JSON-shaped — int/str/bool/None/dict/list — the union is wider than ergonomic; pinned at the walker boundary

from recon_gen.common.sql.dialect import (
    Dialect,
    bigint_type,
    text_type,
    varchar_type,
)


def config_table_name(prefix: str) -> str:
    """The canonical ``<prefix>_config_kv`` table name.

    BC.12 renamed from ``<prefix>_config`` to ``<prefix>_config_kv`` so
    the suffix announces the shape change (flattened kv vs the old
    3-column shape). Pre-BC.12 deploys had the old table empty (BC.6
    finding); no migration needed beyond running the new schema.
    """
    return f"{prefix}_config_kv"


def emit_config_table_ddl(prefix: str, dialect: Dialect) -> str:
    """``CREATE TABLE <prefix>_config_kv`` DDL for the given dialect.

    Column shape (BC.12.3 spike-locked):

    - ``node_id`` BIGINT PK — Python-side monotonic counter at populate
      time (no DB sequence; keeps TRUNCATE + repopulate atomic).
    - ``parent_id`` BIGINT NULL — self-ref to ``node_id``; NULL for root
      nodes (top-level keys). No FK declared: kv is internal-only and
      the walker writes parents before children, so the constraint adds
      no value over correctness-by-construction (and Oracle would force
      a deferred constraint for batch inserts).
    - ``key`` VARCHAR(255) — JSON field name or stringified array index.
    - ``value`` TEXT/CLOB — scalar leaf value (or NULL for container
      nodes). CLOB on Oracle so the ``l2_yaml_raw`` opaque-provenance
      row fits (sasquatch_pr's full L2 JSON is ~37KB; VARCHAR2(4000)
      would force splitting). The typed projection views coerce
      CLOB → VARCHAR2 via ``lob_substr`` before MAX/aggregation
      (Oracle's MAX rejects CLOB with ORA-22849).

    Index on ``(parent_id, key)`` — the typed views' filter shape
    (``WHERE parent_id = X AND key = 'Y'``). No index on ``value``
    (CLOB-incompatible on Oracle without function-based indexes).
    """
    name = config_table_name(prefix)
    bigint = bigint_type(dialect)
    vc255 = varchar_type(255, dialect)
    text_t = text_type(dialect)
    return (
        f"CREATE TABLE {name} (\n"
        f"    node_id   {bigint}   NOT NULL PRIMARY KEY,\n"
        f"    parent_id {bigint},\n"
        f"    key       {vc255},\n"
        f"    value     {text_t}\n"
        f");\n"
        f"CREATE INDEX idx_{name}_parent_key ON {name} (parent_id, key);"
    )


def emit_config_table_drop(prefix: str, dialect: Dialect) -> str:
    """``DROP TABLE IF EXISTS <prefix>_config_kv;`` DDL for re-runs.

    Drops the index implicitly (PG + Oracle + SQLite all drop indexes
    when the table they belong to is dropped).
    """
    name = config_table_name(prefix)
    if dialect is Dialect.ORACLE:
        # Oracle has no IF EXISTS; the BEGIN/EXCEPTION dance handles
        # missing-table without erroring. NO trailing `/` — that's a
        # SQL*Plus directive, not OCI-valid; the Oracle script splitter
        # already terminates PL/SQL blocks on the inner `END;`. A bare
        # `/` line leaks into the next statement and triggers ORA-00900
        # on schema apply (caught 2026-05-24 in CI e2e-oracle-api).
        return (
            f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {name}'; "
            f"EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; "
            f"END IF; END;"
        )
    return f"DROP TABLE IF EXISTS {name};"


# ---------------------------------------------------------------------------
# Walker: JSON tree → kv rows
# ---------------------------------------------------------------------------


def _walk(
    obj: Any,  # typing-smell: ignore[explicit-any]: JSON values are inherently dynamic; isinstance checks below narrow safely
    *,
    counter: list[int],
    parent_id: int | None,
    key: str | None,
) -> list[tuple[int, int | None, str | None, str | None]]:
    """Walk a parsed JSON object/array/scalar; yield kv rows.

    Each visited node gets one row. Containers (dict / list) have
    ``value=None``; scalars (int / float / str / bool / None) have
    ``value = stringified-form``. Each child's ``parent_id`` is the
    container's ``node_id`` (assigned from ``counter``).

    Ordering: parent-before-children (so the FK direction is satisfiable
    even without an actual FK constraint). Within a container, fields
    are visited in insertion order (Python 3.7+ dict semantics).
    """
    counter[0] += 1
    node_id = counter[0]
    rows: list[tuple[int, int | None, str | None, str | None]] = []
    if isinstance(obj, dict):
        rows.append((node_id, parent_id, key, None))
        for k, v in obj.items():  # pyright: ignore[reportUnknownVariableType]: JSON dict values typed Any at the walker boundary
            rows.extend(_walk(v, counter=counter, parent_id=node_id, key=str(k)))  # pyright: ignore[reportUnknownArgumentType]: same JSON-Any boundary
    elif isinstance(obj, list):
        rows.append((node_id, parent_id, key, None))
        for i, v in enumerate(obj):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]: JSON list elements typed Any at the walker boundary
            rows.extend(_walk(v, counter=counter, parent_id=node_id, key=str(i)))  # pyright: ignore[reportUnknownArgumentType]: same JSON-Any boundary
    else:
        # Scalar leaf. None → NULL (no value); bool/int/float → str repr
        # (JSON typing is preserved by the typed projection views' CAST).
        if obj is None:
            scalar_text: str | None = None
        elif isinstance(obj, bool):
            # Order matters: bool is a subclass of int, so check first.
            scalar_text = "true" if obj else "false"
        else:
            scalar_text = str(obj)
        rows.append((node_id, parent_id, key, scalar_text))
    return rows


def kv_rows_for(
    cfg_json: str, l2_json: str, *, as_of: datetime,
) -> list[tuple[int, int | None, str | None, str | None]]:
    """Walk parsed cfg + L2 JSON into kv rows.

    Row order: ``as_of`` scalar first, then the parsed L2 tree, then the
    parsed cfg tree, then the opaque-provenance ``l2_yaml_raw`` /
    ``cfg_yaml_raw`` rows. The walker assigns ``node_id`` monotonically;
    callers MUST insert in this order to preserve parent-before-child
    ordering even without an FK constraint.

    The L2 tree's top-level fields (``rails``, ``limit_schedules``, etc.)
    land at ``parent_id`` = the ``l2_yaml`` container node. The typed
    projection views walk DOWN from that container via ``parent_id`` JOINs.

    ``cfg_json`` is included for downstream symmetry / future
    cfg-derived views, even though no current matview consumes it.
    Caller-friendly: pass ``'{}'`` to skip cfg-side rows entirely (the
    walk produces one empty-container row).
    """
    counter = [0]
    rows: list[tuple[int, int | None, str | None, str | None]] = []
    # 1) Flat top-level scalars at parent_id=NULL.
    counter[0] += 1
    rows.append((
        counter[0], None, "as_of", as_of.strftime("%Y-%m-%d %H:%M:%S"),
    ))
    # 2) The L2 tree under a container node keyed 'l2_yaml'.
    rows.extend(_walk(
        json.loads(l2_json),
        counter=counter,
        parent_id=None,
        key="l2_yaml",
    ))
    # 3) The cfg tree under a container node keyed 'cfg_yaml'.
    rows.extend(_walk(
        json.loads(cfg_json),
        counter=counter,
        parent_id=None,
        key="cfg_yaml",
    ))
    # BC.12.3 deferred: opaque-provenance rows (``l2_yaml_raw``,
    # ``cfg_yaml_raw``) carrying the full JSON in a single kv row.
    # Defer (queued as BC.12 backlog): the long-literal form's
    # ``TO_CLOB(c1) || TO_CLOB(c2) || ...`` collides with
    # ``batch_oracle_inserts``'s quote-aware coalescer (multiple
    # quoted literals in one VALUES row makes the regex assumptions
    # wrong). Operators retain access to the original yaml via the
    # `--l2 <path>` file the operator ships; the typed projection
    # views give matviews the data they actually consume. Add raw
    # provenance back when the batcher can either be bypassed for
    # long-literal rows or rewritten to handle concat-LOB shapes.
    return rows


def emit_config_populate_sql(
    *,
    prefix: str,
    cfg_json: str,
    l2_json: str,
    as_of: datetime,
    dialect: Dialect,
) -> str:
    """Emit the DELETE + INSERT-N-rows SQL that populates
    ``<prefix>_config_kv`` from parsed cfg+L2 JSON + as_of.

    Replaces the pre-BC.12 single-row ``INSERT INTO <prefix>_config
    (as_of, cfg_yaml, l2_yaml) VALUES (...)``.

    Empties the table first (DELETE — TRUNCATE isn't atomic with the
    subsequent INSERTs in every dialect's transactional semantics, and
    DELETE is plenty fast for a single-deploy populate), then issues
    one INSERT per kv row. Each INSERT goes on its own line so the
    script splitter in ``common/db.execute_script`` can run them
    individually — dialect drivers don't all support multi-row VALUES
    cleanly across vendor lines (and oracledb in particular).

    Long string values (e.g. the ``l2_yaml_raw`` provenance row at
    ~37 KB for sasquatch_pr) are split on Oracle into 4000-byte chunks
    concatenated via ``TO_CLOB(chunk1) || TO_CLOB(chunk2) || ...`` —
    Oracle's literal limit is **ORA-01704: string literal too long** at
    4000 bytes per single-quoted literal, but the concatenation of
    multiple shorter literals into a CLOB has no such cap. PG + SQLite
    accept multi-megabyte literals directly; their path is the single-
    literal form.

    All input is walker-controlled (parsed JSON values, never operator
    input flowing through to SQL) — no SQL-injection surface.
    """
    name = config_table_name(prefix)
    rows = kv_rows_for(cfg_json, l2_json, as_of=as_of)
    lines: list[str] = [
        f"DELETE FROM {name};",
    ]
    for node_id, parent_id, key, value in rows:
        parent_sql = "NULL" if parent_id is None else str(parent_id)
        key_sql = "NULL" if key is None else _sql_quote(key)
        value_sql = (
            "NULL" if value is None else _sql_quote_long(value, dialect)
        )
        lines.append(
            f"INSERT INTO {name} (node_id, parent_id, key, value) "
            f"VALUES ({node_id}, {parent_sql}, {key_sql}, {value_sql});"
        )
    return "\n".join(lines) + "\n"


def _sql_quote(s: str) -> str:
    """Quote ``s`` as a SQL string literal (escape embedded single quotes
    by doubling). Walker-controlled input only; no SQL-injection surface.
    """
    return "'" + s.replace("'", "''") + "'"


# Oracle string-literal cap: ORA-01704 fires above this. PG + SQLite
# accept multi-megabyte literals; only Oracle needs chunked TO_CLOB.
_ORACLE_LITERAL_MAX_CHARS = 4000


def _sql_quote_long(s: str, dialect: Dialect) -> str:
    """Quote ``s`` as a SQL string literal, chunking long values for
    Oracle's ORA-01704 cap.

    Below the 4000-byte threshold (or on PG / SQLite), returns the
    plain single-quoted literal — same as ``_sql_quote``. Above the
    threshold on Oracle, splits into 4000-byte chunks and joins via
    ``TO_CLOB(chunk) || TO_CLOB(chunk) || ...`` so the resulting CLOB
    has no literal-size cap. Each chunk's embedded single quotes are
    individually doubled.

    Important: chunk boundaries must not split inside a single
    SQL-doubled quote sequence (``''``) — we chunk on the RAW string
    before doubling, so each chunk's quote-doubling stays self-
    contained. Equivalent to writing the raw bytes 4000 at a time and
    quoting each segment.
    """
    if dialect is not Dialect.ORACLE or len(s) <= _ORACLE_LITERAL_MAX_CHARS:
        return _sql_quote(s)
    chunks = [
        s[i:i + _ORACLE_LITERAL_MAX_CHARS]
        for i in range(0, len(s), _ORACLE_LITERAL_MAX_CHARS)
    ]
    return " || ".join(f"TO_CLOB({_sql_quote(c)})" for c in chunks)


# ---------------------------------------------------------------------------
# Runtime helpers (cursor-based).
# ---------------------------------------------------------------------------


def replace_config(
    conn: sqlite3.Connection,
    *,
    prefix: str,
    cfg_json: str,
    l2_json: str,
    as_of: datetime,
) -> None:
    """**Deploy event** — re-populate kv from parsed cfg+L2 + as_of.

    DELETE + INSERT preserves an idempotent populate without relying on
    dialect-specific UPSERT. The caller is responsible for serializing
    cfg + L2 to JSON strings (typically via ``dataclasses.asdict`` +
    ``json.dumps``, or by reading the source YAML and re-serializing).

    Cursor flavor: the type annotation is ``sqlite3.Connection`` for
    SQLite-test convenience, but the function is duck-typed against the
    PEP 249 conn / cursor interface — psycopg2 + oracledb both work.
    """
    name = config_table_name(prefix)
    conn.execute(f"DELETE FROM {name}")
    rows = kv_rows_for(cfg_json, l2_json, as_of=as_of)
    # Parameterized batch insert — single ``?``-style placeholder works
    # on SQLite; psycopg2 + oracledb each have their own placeholder
    # styles that callers of this function (production deploy path)
    # don't currently exercise. The deploy path uses the emit_config_
    # populate_sql + execute_script route instead so the placeholder
    # mismatch doesn't bite.
    conn.executemany(
        f"INSERT INTO {name} (node_id, parent_id, key, value) "
        f"VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def set_as_of(
    conn: sqlite3.Connection,
    *,
    prefix: str,
    as_of: datetime | None = None,
) -> None:
    """**ETL refresh event** — update the as_of scalar row.

    ``as_of=None`` (production default) → updates to CURRENT_TIMESTAMP
    so matview age formulas pick up "right now" at refresh time.
    Literal datetime → pinned (tests + backfill scenarios).

    Assumes the kv table is already populated — call ``replace_config``
    once at deploy/init time before any ``set_as_of`` calls.
    """
    name = config_table_name(prefix)
    if as_of is None:
        conn.execute(
            f"UPDATE {name} SET value = strftime('%Y-%m-%d %H:%M:%S', 'now') "
            f"WHERE parent_id IS NULL AND key = 'as_of'",
        )
    else:
        conn.execute(
            f"UPDATE {name} SET value = ? "
            f"WHERE parent_id IS NULL AND key = 'as_of'",
            (as_of.strftime("%Y-%m-%d %H:%M:%S"),),
        )
    conn.commit()


def get_as_of(conn: sqlite3.Connection, *, prefix: str) -> datetime:
    """Read the current ``as_of`` value back as a Python datetime.

    Reads from the single ``as_of`` kv row at ``parent_id IS NULL``.
    Raises ``RuntimeError`` if the row doesn't exist — the single-row
    invariant: call ``replace_config`` before ``get_as_of`` / ``set_as_of``.
    """
    name = config_table_name(prefix)
    row = conn.execute(
        f"SELECT value FROM {name} "
        f"WHERE parent_id IS NULL AND key = 'as_of'",
    ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError(
            f"{name} has no row — call replace_config(...) before "
            f"get_as_of(...) or set_as_of(...).",
        )
    return _parse_timestamp(row[0])


def _parse_timestamp(value: object) -> datetime:
    """Parse the various TIMESTAMP representations DB drivers return.

    psycopg2 returns datetime; oracledb returns datetime; sqlite3
    returns str. Handle all three uniformly.
    """
    if isinstance(value, datetime):
        return value
    s = str(value)
    # Tolerate fractional seconds (PG/Oracle CURRENT_TIMESTAMP) by
    # truncating to whole-second precision.
    if "." in s:
        s = s.split(".", 1)[0]
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# SQL expression helpers for typed views (used by schema.py).
# ---------------------------------------------------------------------------


def kv_root_id_for(prefix: str, top_key: str) -> str:
    """Scalar subquery returning the ``node_id`` of the root container
    keyed ``top_key`` (one of ``'l2_yaml'`` / ``'cfg_yaml'``). Used by
    typed views to anchor their walks.

    The kv populate guarantees exactly one row per top-level key at
    ``parent_id IS NULL``; the subquery returns a single value.
    """
    name = config_table_name(prefix)
    return (
        f"(SELECT node_id FROM {name} "
        f"WHERE parent_id IS NULL AND key = {_sql_quote(top_key)})"
    )


def kv_as_of_subquery(prefix: str) -> str:
    """Scalar subquery returning the raw ``as_of`` value (text form).

    The walker stores ``as_of`` as a stringified ``YYYY-MM-DD HH:MM:SS``
    leaf; this helper wraps the kv read so callers don't need to know
    the storage shape. Pair with ``kv_as_of_as_timestamp_sql`` when the
    consumer needs a typed TIMESTAMP (e.g. for date arithmetic).
    """
    name = config_table_name(prefix)
    return (
        f"(SELECT value FROM {name} "
        f"WHERE parent_id IS NULL AND key = 'as_of')"
    )


def kv_as_of_as_timestamp_sql(prefix: str, dialect: Dialect) -> str:
    """Read the ``as_of`` kv row and project as TIMESTAMP for date
    arithmetic (``epoch_seconds_between`` and friends).

    Dialect-specific coercion:

    - **PG**: ``CAST(... AS TIMESTAMP)`` — PG accepts the ISO-format
      text and yields a proper TIMESTAMP.
    - **Oracle**: ``TO_TIMESTAMP(DBMS_LOB.SUBSTR(value, 100, 1),
      'YYYY-MM-DD HH24:MI:SS')`` — Oracle won't CAST CLOB to TIMESTAMP
      (ORA-00932); DBMS_LOB.SUBSTR converts to VARCHAR2 first, then
      TO_TIMESTAMP parses the fixed-format string.
    - **SQLite**: bare text — SQLite has no TIMESTAMP type, and
      ``julianday(text)`` accepts ISO-format strings natively (so the
      ``epoch_seconds_between`` SQLite branch's
      ``(julianday(later) - julianday(earlier)) * 86400`` works
      unchanged).
    """
    sub = kv_as_of_subquery(prefix)
    if dialect is Dialect.POSTGRES:
        return f"CAST({sub} AS TIMESTAMP)"
    if dialect is Dialect.ORACLE:
        return (
            f"TO_TIMESTAMP(DBMS_LOB.SUBSTR({sub}, 100, 1), "
            f"'YYYY-MM-DD HH24:MI:SS')"
        )
    # SQLite: text passthrough; julianday() accepts it.
    return sub


# A type alias for the kv row tuple shape — exported for tests that
# build kv rows directly without going through the walker.
KvRow = tuple[int, int | None, str | None, str | None]


__all__ = [
    "KvRow",
    "config_table_name",
    "emit_config_populate_sql",
    "emit_config_table_ddl",
    "emit_config_table_drop",
    "get_as_of",
    "kv_as_of_as_timestamp_sql",
    "kv_as_of_subquery",
    "kv_root_id_for",
    "kv_rows_for",
    "replace_config",
    "set_as_of",
]


# Backwards-compat shim — callers iterating the walker output via
# the typed-tuple alias.
_AssertedRowsType = list[KvRow]
_ = Iterable  # used in the type alias; quiet unused-import for linter
