"""X.2.f — generic SQL executor with dialect-aware filter substitution.

Today's ``_db_fetcher.py`` hand-writes WHERE clauses + bind params
inline per visual. X.2.g will need the same pattern across dozens
of visuals — at that point the boilerplate becomes the bug surface.
This module is the abstraction: a Visual + its dataset SQL + the
URL-keyed filter dict → executed query → ``(rows, columns)``.

Filter param convention (from X.2.d's URL contract):

    date_from    → WHERE <date_col> >= :date_from
    date_to      → WHERE <date_col> <= :date_to
    param_<name> → bound to ``:<name>`` in dataset SQL
    filter_<col> → WHERE <col> IN (...) (comma-split server side)
    min_<col>    → WHERE <col> >= :min_<col>
    max_<col>    → WHERE <col> <= :max_<col>

The dataset SQL author opts in to filters by referencing them as
``:date_from`` / ``:param_view`` / etc. Filters not referenced are
silently ignored (zero-impact when a sheet doesn't carry them).

Placeholder dispatch:

    Postgres → ``%(name)s``  (psycopg / psycopg_pool named bind)
    Oracle   → ``:name``     (oracledb named bind)
    SQLite   → ``:name``     (aiosqlite named bind)

So Oracle and SQLite share the source form (``:name``); Postgres
gets a single rewrite pass before execution.

Two execute fns:

  - ``execute_visual_sql_async`` (X.2.n.3): async; takes an
    ``AsyncConnectionPool`` and uses ``async with pool.acquire()``
    + ``await cur.execute()``. The hot path used by App2's
    ``visual_data`` route.
  - ``execute_visual_sql`` (legacy sync): kept for backward compat
    with tests + scripts that pass a sync ``connection_factory``.
    Will be removed once all callers move to the async pool.

Pure module — no network / no DB at import. The pool / factory is
the seam. ``execute_visual_sql_*`` is renderer-agnostic;
``shape_for_kind`` in ``_data_shape.py`` is the per-renderer step
that follows.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from quicksight_gen.common.sql.dialect import Dialect

if TYPE_CHECKING:
    from quicksight_gen.common.db import AsyncConnectionPool


# Matches ``:name`` placeholders. Excludes ``::`` (Postgres cast
# operator) by requiring the colon to NOT be preceded by another
# colon — uses a negative lookbehind. Identifier characters per
# Python identifier rules + digits.
_NAMED_PLACEHOLDER_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


# Y.1.e — QuickSight dataset-parameter placeholders.
#
# QS dataset Custom SQL uses ``<<$paramName>>`` for parameter
# substitution (literal value spliced in by QS at query time).
# App2 reads the same SQL but binds via ``:param_paramName`` —
# these patterns translate the QS shape to the bind shape so one
# SQL string serves both dialects.
#
# Two patterns, applied in order:
# 1. Quoted form ``'<<$pName>>'`` (QS string-param convention —
#    author wraps in single quotes so substitution produces a
#    valid SQL string literal). Bind variables don't need quoting,
#    so we strip the surrounding quotes when translating.
# 2. Unquoted form ``<<$pName>>`` (QS numeric-param convention —
#    author leaves bare so substitution produces a valid SQL
#    number literal). Bind variables work directly without quoting.
#
# Translated form: ``:param_paramName`` — App2's URL bridge maps
# this to the URL-supplied value. The ``param_`` prefix matches the
# X.2.b URL-as-state contract (``?param_pName=value`` → bind under
# the ``param_pName`` name in the bind dict).
_QS_QUOTED_DSP_RE = re.compile(r"'<<\$([A-Za-z_][A-Za-z0-9_]*)>>'")
_QS_UNQUOTED_DSP_RE = re.compile(r"<<\$([A-Za-z_][A-Za-z0-9_]*)>>")


def translate_qs_dataset_params(sql: str) -> str:
    """Translate QS-style ``<<$paramName>>`` placeholders to the
    App2 bind-variable form ``:param_paramName``.

    Quoted form first (``'<<$pName>>'`` strips the outer quotes —
    binds quote for us), then unquoted (``<<$pName>>`` becomes
    ``:param_pName`` directly). Idempotent for SQL that contains
    no QS placeholders — passes through unchanged.

    The translation is purely syntactic and dialect-agnostic; the
    later ``rewrite_placeholders_for_dialect`` step converts
    ``:param_pName`` to PG's ``%(param_pName)s`` if needed.
    """
    sql = _QS_QUOTED_DSP_RE.sub(r":param_\1", sql)
    sql = _QS_UNQUOTED_DSP_RE.sub(r":param_\1", sql)
    return sql


def rewrite_placeholders_for_dialect(sql: str, dialect: Dialect) -> str:
    """Convert ``:name`` placeholders to dialect-native form.

    SQLite + Oracle accept ``:name`` natively (DB-API 2.0 named
    paramstyle); Postgres uses ``%(name)s``. The rewrite is purely
    string-level — caller still passes the same dict of bind values
    regardless of dialect. ``::`` (PG cast) is preserved.
    """
    if dialect is Dialect.POSTGRES:
        return _NAMED_PLACEHOLDER_RE.sub(r"%(\1)s", sql)
    # Oracle + SQLite already accept ``:name``.
    return sql


def collect_bind_params(
    sql: str,
    url_params: Mapping[str, str],
) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: bind dict accepted by every driver coerces values per-driver — caller passes whatever the SQL placeholder needs
    """Build the bind-param dict for the SQL string.

    Walks ``sql`` for ``:name`` placeholders, looks up each name in
    ``url_params``, and returns the dict the DB driver wants. Names
    not present in ``url_params`` get an empty string — the dataset
    SQL author is responsible for guarding against empty filters
    (typically ``WHERE col >= :date_from OR :date_from = ''``).
    Names referenced in ``url_params`` but NOT in the SQL are
    dropped (no-op in the bind dict — the DB driver would reject
    them with "too many parameters" otherwise).
    """
    referenced = set(_NAMED_PLACEHOLDER_RE.findall(sql))
    return {name: url_params.get(name, "") for name in referenced}


def execute_visual_sql(
    connection_factory: Callable[[], Any],  # typing-smell: ignore[explicit-any]: sync DB-API 2.0 connection has no shared Protocol across psycopg/oracledb/sqlite3
    sql: str,
    url_params: Mapping[str, str],
    *,
    dialect: Dialect,
) -> tuple[list[tuple[Any, ...]], list[str]]:  # typing-smell: ignore[explicit-any]: row tuples are heterogeneous; per-call shape lives in the dataset SQL contract
    """Execute a Visual's dataset SQL via a sync DB-API 2.0 driver.

    DEPRECATED — kept for backward compatibility with sync test
    fixtures + ad-hoc scripts. The App2 server uses the async path
    (``execute_visual_sql_async``) which doesn't block the event
    loop. New code should pass an ``AsyncConnectionPool`` and
    ``await execute_visual_sql_async(...)``.

    Args:
        connection_factory: returns a fresh DB-API 2.0 connection.
            Caller is responsible for pooling / sharing if relevant
            — this fn opens + closes per call.
        sql: dataset SQL with ``:name`` placeholders (any dialect).
        url_params: the URL-keyed filter dict the App2 server
            extracted from the request query string. Keys not
            referenced in ``sql`` are ignored.
        dialect: SQL dialect of the connection. Drives placeholder
            rewriting (PG → ``%(name)s``; Oracle / SQLite stay).

    Returns:
        ``(rows, columns)``: rows is a list of tuples, columns is
        the list of column names from ``cursor.description``. The
        per-renderer shape adapter in ``_data_shape.py`` consumes
        this tuple.
    """
    sql = translate_qs_dataset_params(sql)
    rewritten = rewrite_placeholders_for_dialect(sql, dialect)
    binds = collect_bind_params(sql, url_params)
    conn = connection_factory()
    try:
        cur = conn.cursor()
        try:
            cur.execute(rewritten, binds)
            rows: list[Any] = list(cur.fetchall())  # typing-smell: ignore[explicit-any]: heterogeneous row tuples
            description: Sequence[Sequence[Any]] = cur.description or []  # typing-smell: ignore[explicit-any]: DB-API 2.0 description tuples mix types per column slot
            columns = [str(c[0]) for c in description]
        finally:
            cur.close()
    finally:
        conn.close()
    return [tuple(r) for r in rows], columns


async def execute_visual_sql_async(
    pool: AsyncConnectionPool,
    sql: str,
    url_params: Mapping[str, str],
    *,
    dialect: Dialect,
) -> tuple[list[tuple[Any, ...]], list[str]]:  # typing-smell: ignore[explicit-any]: row tuples are heterogeneous; per-call shape lives in the dataset SQL contract
    """Async sibling of ``execute_visual_sql`` — the hot path for the
    App2 ``visual_data`` route.

    Acquires a connection from the pool (returns to pool on context
    exit), opens a cursor, awaits ``execute`` + ``fetchall``, and
    returns ``(rows, columns)`` in the same shape the sync version
    produces. The pure-CPU bits (placeholder rewrite + bind
    collection) stay sync; only the I/O bits await.

    Cursor lifecycle is dialect-aware (Y.3.f.alt.4b):
      - psycopg: ``await conn.execute(sql, params)`` returns the
        AsyncCursor — the documented one-shot pattern.
      - aiosqlite: same — ``await conn.execute(...)`` returns a
        cursor.
      - oracledb async: ``conn.execute()`` does NOT return a cursor
        (returns ``None``; executes against an internal cursor we
        can't access). Must use the explicit ``cur = conn.cursor();
        await cur.execute(sql, params)`` pattern.

    Returns:
        Same ``(rows, columns)`` shape as the sync version. Rows
        are coerced to tuples (oracledb returns lists; psycopg
        returns tuples; aiosqlite returns Row objects that pickle
        as tuples).

    Raises:
        Whatever the underlying driver raises on bad SQL / pool
        exhaustion / connection failure. The App2 server's themed
        500 handler (X.2.m) catches it.
    """
    sql = translate_qs_dataset_params(sql)
    rewritten = rewrite_placeholders_for_dialect(sql, dialect)
    binds = collect_bind_params(sql, url_params)
    async with pool.acquire() as conn:
        if dialect is Dialect.ORACLE:
            # oracledb async: must open a cursor explicitly. The
            # ``conn.execute(sql, params)`` shorthand returns None
            # (executes via an internal cursor we can't read back).
            # cast: per-driver cursor union (psycopg AsyncCursor /
            # aiosqlite Cursor / oracledb AsyncCursor) lacks a
            # shared Protocol for fetchall/description/close, AND
            # `conn` is typed as psycopg AsyncConnection (the pool
            # alias) — Oracle conn doesn't share that interface.
            cur: Any = cast(Any, conn).cursor()  # typing-smell: ignore[explicit-any]: per-driver cursor union (psycopg/aiosqlite/oracledb) has no shared Protocol; conn typed as AsyncConnection so cursor() not exposed there either
            await cur.execute(rewritten, binds)
        else:
            # psycopg AsyncConnection + aiosqlite both return a
            # cursor from ``await conn.execute(sql, params)``.
            cur = cast(Any, await conn.execute(rewritten, binds))  # typing-smell: ignore[explicit-any]: per-driver cursor union (psycopg/aiosqlite) has no shared Protocol for fetchall/description/close
        try:
            rows: list[Any] = await cur.fetchall()  # typing-smell: ignore[explicit-any]: driver-typed row union widens to Any after Any cursor
            description = cast(list[Any], cur.description or [])  # typing-smell: ignore[explicit-any]: per-driver column-meta tuple union — same justification as `cur` above
            columns = [str(c[0]) for c in description]
        finally:
            # aiosqlite cursors close async; psycopg / oracledb
            # cursors are also async. Await regardless to keep the
            # path uniform.
            close = getattr(cur, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
    return [tuple(r) for r in rows], columns
