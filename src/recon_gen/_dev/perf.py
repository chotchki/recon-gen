"""Dev tooling — top-N expensive query capture (Y.2.gate.c.10).

Lifted from ``scripts/dump_top_queries.py`` (W.8a) so both the
standalone script (still in place until ``Y.2.gate.f.4`` deletes it)
and the in-process e2e conftest fixture import the same helpers.

Sources:
  * PostgreSQL — ``pg_stat_statements`` (auto-loaded on Aurora; needs
    ``CREATE EXTENSION pg_stat_statements`` once per database).
  * Oracle — ``v$sqlstats`` (DBA view; the ``admin`` user on RDS
    Oracle SE2 has read access by default).
  * SQLite — no equivalent stats view; caller skips and writes a
    "skipped" marker.

Both available sources are cumulative across the operator's other
workloads on the shared DB. The ``like_pattern`` filter narrows to
queries whose text contains the configured substring (default: the
L2 instance prefix) so the output is ours, not noise from other
tenants.

All helpers are pure functions on the connection / row data — the
caller owns connection lifecycle and "where to write the file".
"""

from __future__ import annotations

import textwrap
from typing import Any

from recon_gen.common.sql import Dialect


# pg_stat_statements: top-N rows by cumulative execution time. Cast
# microsecond columns to ms for human readability. Filter on query
# text containing the L2 instance prefix so we drop the operator's
# unrelated traffic on the shared database.
_PG_TOP_QUERIES_SQL = """
SELECT
    calls,
    ROUND(total_exec_time::numeric, 1) AS total_ms,
    ROUND(mean_exec_time::numeric, 2)  AS mean_ms,
    rows,
    LEFT(REGEXP_REPLACE(query, '\\s+', ' ', 'g'), 400) AS query_text
FROM pg_stat_statements
WHERE query ILIKE %s
ORDER BY total_exec_time DESC
LIMIT %s
"""


# v$sqlstats: same shape, micro to ms via /1000. ``elapsed_time`` is
# the closest analog to ``total_exec_time``. Oracle uses bind-style
# parameters (``:1``, ``:2``) for the prepared statement.
_ORACLE_TOP_QUERIES_SQL = """
SELECT
    executions,
    ROUND(elapsed_time / 1000.0, 1) AS total_ms,
    ROUND((elapsed_time / NULLIF(executions, 0)) / 1000.0, 2) AS mean_ms,
    rows_processed,
    SUBSTR(REGEXP_REPLACE(sql_fulltext, '\\s+', ' '), 1, 400) AS query_text
FROM v$sqlstats
WHERE UPPER(sql_fulltext) LIKE UPPER(:1)
ORDER BY elapsed_time DESC
FETCH FIRST :2 ROWS ONLY
"""


def fetch_top_queries(
    conn: Any, dialect: Dialect, *, like_pattern: str, top: int,
) -> list[tuple[Any, ...]]:
    """Run the dialect-appropriate top-queries query.

    For PG, idempotently bootstraps ``pg_stat_statements`` (Aurora
    rds_superuser can; locked-down PGs raise InsufficientPrivilege
    which we swallow so the next query falls into the caller's
    "skipped" path). For Oracle, the ``v$sqlstats.sql_fulltext``
    column is a CLOB; the last column gets ``.read()``-ed here so
    downstream formatters stay dialect-agnostic.

    Raises whatever the underlying driver raises — the caller is
    responsible for the "stats view unavailable / skipped" fallback.
    """
    if dialect is Dialect.POSTGRES:
        cur = conn.cursor()
        try:
            try:
                cur.execute(
                    "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"
                )
                conn.commit()
            except Exception:
                conn.rollback()
            cur.execute(_PG_TOP_QUERIES_SQL, (f"%{like_pattern}%", top))
            return list(cur.fetchall())
        finally:
            cur.close()
    if dialect is Dialect.ORACLE:
        cur = conn.cursor()
        try:
            cur.execute(_ORACLE_TOP_QUERIES_SQL, (f"%{like_pattern}%", top))
            rows = cur.fetchall()
            return [
                (*r[:-1], r[-1].read() if hasattr(r[-1], "read") else r[-1])
                for r in rows
            ]
        finally:
            cur.close()
    raise NotImplementedError(
        f"top-queries not supported for dialect {dialect.value!r}"
    )


def format_top_queries_markdown(
    *,
    title: str,
    dialect: str,
    like_pattern: str,
    rows: list[tuple[Any, ...]],
    note: str | None = None,
) -> str:
    """Render a perf-debug markdown table from rows fetched above.

    Renders empty-row state as ``_No matching rows._`` (CI artifacts
    that ran zero matching queries are still valid evidence).
    """
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Dialect:** {dialect}")
    lines.append(f"- **Filter (LIKE):** `%{like_pattern}%`")
    lines.append(f"- **Rows returned:** {len(rows)}")
    if note:
        lines.append(f"- **Note:** {note}")
    lines.append("")
    if not rows:
        lines.append("_No matching rows._")
        return "\n".join(lines) + "\n"
    lines.append("| Calls | Total (ms) | Mean (ms) | Rows | Query |")
    lines.append("|---:|---:|---:|---:|---|")
    for r in rows:
        calls, total_ms, mean_ms, n_rows, query_text = r
        # Markdown table escapes — pipes inside the query break the
        # row, backticks make literal-render look right.
        q = (query_text or "").replace("|", "\\|").replace("\n", " ")
        q = textwrap.shorten(q, width=380, placeholder="…")
        lines.append(
            f"| {calls} | {total_ms} | {mean_ms} | {n_rows} | `{q}` |"
        )
    return "\n".join(lines) + "\n"


def format_skipped(*, title: str, dialect: str, reason: str) -> str:
    """Render a "we tried but couldn't" marker. Caller writes this
    when the stats view is unavailable, the connection failed, or the
    dialect isn't supported. Output stays valid markdown so the CI
    artifact upload + downstream readers don't choke."""
    return (
        f"# {title}\n\n"
        f"- **Dialect:** {dialect}\n"
        f"- **Status:** _skipped_\n"
        f"- **Reason:** {reason}\n"
    )


def dialect_name(dialect: Dialect) -> str:
    """Stable string representation for path/filename use.

    Mirrors the script's ``"postgres" if cfg.dialect is POSTGRES else
    "oracle"`` shape but covers SQLite explicitly. Used for
    ``$RECON_GEN_RUN_DIR/db/<dialect>/top-queries.md`` paths.
    """
    return dialect.value
