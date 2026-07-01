"""DY.8 — SQL-capture helper for the spine ``Invariant.detect()``
pushdown-surface tests.

SQLite's ``conn.set_trace_callback(sink.append)`` recorded every executed
statement; DuckDB (the post-CB.8 in-memory generator DB) has no equivalent
hook, which is why the ten ``*_detect_does_not_cross_a_sql_pushdown_surface``
tests were skipped. ``record_sql(conn, sink)`` is the portable port: it
wraps the connection so every ``cursor().execute(sql)`` appends ``sql`` to
``sink`` before delegating to the real cursor.

Why a connection wrapper (not a patch on ``fetch_all``): it's the faithful
equivalent of ``set_trace_callback`` — it intercepts at the connection, so
it captures ANY cursor-driven statement the detector runs, and it works
identically across duckdb / psycopg / oracledb. Every detector funnels its
SQL through the single choke point ``common/spine/_db.py::fetch_all`` (which
does ``conn.cursor()`` then ``cur.execute(sql)``), so wrapping ``.cursor()``
is sufficient today; the callers' ``assert sink`` guards against a future
detector that stops going through that path.
"""

from __future__ import annotations

from typing import Any, TypeVar, cast

# The proxy is type-transparent: it returns the same connection type it
# wraps, so each caller's detect() sees the exact type it declared — the
# src invariants take SyncConnection, the au0/as0/at0 full-spine tests'
# local invariants take duckdb.DuckDBPyConnection, and one helper serves
# both without an Any-hole.
_ConnT = TypeVar("_ConnT")


class _RecordingCursor:
    """DB-API cursor proxy that logs the SQL of every ``execute`` call."""

    def __init__(self, inner: Any, sink: list[str]) -> None:  # typing-smell: ignore[explicit-any]: per-driver Cursor has no shared Protocol at this layer
        self._inner = inner
        self._sink = sink

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:  # typing-smell: ignore[explicit-any]: delegates to the wrapped driver cursor
        self._sink.append(sql)
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:  # typing-smell: ignore[explicit-any]: transparently delegates fetchall/close/description/rowcount/...
        return getattr(self._inner, name)


class _RecordingConnection:
    """DB-API connection proxy whose cursors record executed SQL."""

    def __init__(self, inner: Any, sink: list[str]) -> None:  # typing-smell: ignore[explicit-any]: per-driver Connection has no shared Protocol at this layer
        self._inner = inner
        self._sink = sink

    def cursor(self, *args: Any, **kwargs: Any) -> _RecordingCursor:  # typing-smell: ignore[explicit-any]: forwards driver-specific cursor kwargs
        return _RecordingCursor(self._inner.cursor(*args, **kwargs), self._sink)

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:  # typing-smell: ignore[explicit-any]: DuckDB's convenience conn.execute() shim; delegates to the driver
        # Some detectors use DuckDB's ``conn.execute(sql).fetchall()``
        # shim instead of the cursor path (``fetch_all`` → ``cursor()``);
        # set_trace_callback recorded BOTH, so this wrapper must too.
        self._sink.append(sql)
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:  # typing-smell: ignore[explicit-any]: transparently delegates commit/close/...
        return getattr(self._inner, name)


def record_sql(conn: _ConnT, sink: list[str]) -> _ConnT:
    """Wrap ``conn`` so executed SQL is appended to ``sink``.

    The port of SQLite's ``set_trace_callback`` for the spine
    pushdown-surface tests: ``inv.detect(record_sql(conn, captured))``
    leaves ``captured`` holding every statement the detector ran.

    Returns the SAME connection type it wraps: the proxy delegates the
    whole DB-API surface at runtime (via ``__getattr__``), which pyright
    can't see through, so the cast is the honest boundary — the object
    behaves as a connection everywhere ``detect`` touches it.
    """
    return cast(_ConnT, _RecordingConnection(conn, sink))
