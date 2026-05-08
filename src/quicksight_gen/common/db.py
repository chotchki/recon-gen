"""Dialect-aware database connection + script execution helpers (P.9d).

Used by the CLI (``demo apply``) and the e2e harness fixtures. Both
need to:

  - Open a DB-API 2.0 connection against Postgres (psycopg2), Oracle
    (oracledb), or SQLite (stdlib ``sqlite3``), keyed off ``cfg.dialect``.
  - Run multi-statement DDL/DML scripts. psycopg2 accepts the whole
    script in one ``cursor.execute`` call; oracledb requires per-
    statement execution and treats PL/SQL blocks (``BEGIN…END;``) as
    one unit; sqlite3 accepts whole scripts via ``executescript``.

Both PG + Oracle surfaces existed inline in ``cli.py`` before P.9d.
Lifting them here lets ``tests/e2e/test_harness_end_to_end.py`` consume
the same helpers instead of hardcoding psycopg2 (which raised
``ProgrammingError`` at setup when the harness ran against an Oracle
config — see PLAN.md P.9d). X.3 added the SQLite arm using the stdlib
``sqlite3`` module — no extra dependency required.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from quicksight_gen.common.config import Config
from quicksight_gen.common.sql import Dialect


__all__ = [
    "AsyncConnection",
    "AsyncConnectionPool",
    "AsyncCursor",
    "batch_oracle_inserts",
    "connect_demo_db",
    "execute_script",
    "make_connection_pool",
    "oracle_dsn",
    "split_oracle_script",
    "sqlite_path",
]


def oracle_dsn(url: str) -> str:
    """Translate a SQLAlchemy-style Oracle URL into an oracledb DSN.

    Accepts either form:
      - ``oracle+oracledb://user:pass@host:port/?service_name=XEPDB1``
      - ``user/pass@host:port/XEPDB1`` (oracledb's native format)

    Returns a string ``oracledb.connect()`` understands.
    """
    if url.startswith(("oracle://", "oracle+oracledb://")):
        parsed = urlparse(url)
        user = parsed.username or ""
        pw = parsed.password or ""
        host = parsed.hostname or "localhost"
        port = parsed.port or 1521
        service = (
            parse_qs(parsed.query).get("service_name", [None])[0]
            or parsed.path.lstrip("/")
            or "FREEPDB1"
        )
        return f"{user}/{pw}@{host}:{port}/{service}"
    return url


def sqlite_path(url: str) -> str:
    """Translate a ``sqlite:///path/to/db.sqlite`` URL to a path string.

    Accepts the SQLAlchemy-style ``sqlite:///`` triple-slash form (the
    fourth slash starts the absolute path component) and the
    ``sqlite://:memory:`` in-memory form. Also accepts a bare path
    string for ergonomics — if the value isn't a recognized URL
    scheme, it's returned unchanged so the caller can pass the raw
    sqlite file path directly.

    Examples:
      - ``sqlite:///tmp/demo.sqlite`` → ``/tmp/demo.sqlite``
      - ``sqlite:///./relative.sqlite`` → ``./relative.sqlite``
      - ``sqlite://:memory:`` → ``:memory:``
      - ``/tmp/demo.sqlite`` → ``/tmp/demo.sqlite``
    """
    if url == "sqlite://:memory:" or url.endswith(":memory:"):
        return ":memory:"
    if url.startswith("sqlite:///"):
        # Triple-slash: the fourth ``/`` introduces the absolute path
        # (so ``sqlite:////tmp/demo.sqlite`` keeps the leading slash).
        return url[len("sqlite:///"):]
    if url.startswith("sqlite://"):
        # Edge case: ``sqlite://path`` (two slashes) — strip the
        # scheme + double slash; relative paths stay relative.
        return url[len("sqlite://"):]
    return url


def connect_demo_db(cfg: Config) -> Any:  # typing-smell: ignore[explicit-any]: DB-API 2.0 sync connection has no shared Protocol across psycopg/oracledb/sqlite3
    """Open a DB-API 2.0 connection to ``cfg.demo_database_url``.

    Branches on ``cfg.dialect``:
      - Postgres: psycopg (v3, from the ``[demo]`` extra).
      - Oracle: oracledb thin client (from the ``[demo-oracle]`` extra).
      - SQLite: stdlib ``sqlite3`` (no extra required).

    Raises:
      ImportError: if the matching driver isn't installed (PG / Oracle
        only — SQLite ships with stdlib). The error message names the
        extras-install command.
      ValueError: if ``cfg.demo_database_url`` is unset or
        ``cfg.dialect`` isn't recognized.
    """
    if cfg.demo_database_url is None:
        raise ValueError(
            "cfg.demo_database_url is unset; set it in your config YAML "
            "or via QS_GEN_DEMO_DATABASE_URL."
        )
    if cfg.dialect is Dialect.POSTGRES:
        try:
            import psycopg
        except ImportError as e:
            raise ImportError(
                "psycopg is required for Postgres connections. "
                "Install it with: pip install 'quicksight-gen[demo]'"
            ) from e
        return psycopg.connect(cfg.demo_database_url)
    if cfg.dialect is Dialect.ORACLE:
        try:
            import oracledb  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs
        except ImportError as e:
            raise ImportError(
                "oracledb is required for Oracle connections. "
                "Install it with: pip install 'quicksight-gen[demo-oracle]'"
            ) from e
        return oracledb.connect(oracle_dsn(cfg.demo_database_url))
    if cfg.dialect is Dialect.SQLITE:
        # stdlib — no try/except for ImportError. SQLite uses Python's
        # builtin ``sqlite3`` module so the local-iteration loop has
        # zero install friction beyond ``pip install quicksight-gen``.
        import sqlite3
        conn = sqlite3.connect(sqlite_path(cfg.demo_database_url))
        # Foreign keys are off by default; turn them on so any FK
        # declarations in future schema versions enforce. The schema
        # we emit today has no FKs, so this is forward-looking.
        conn.execute("PRAGMA foreign_keys = ON;")
        # Register the SQL/2008 STDDEV_SAMP aggregate that SQLite
        # doesn't ship natively but the inv_pair_rolling_anomalies
        # matview needs. Implementation is single-pass + numerically
        # stable (Welford's online algorithm).
        _register_sqlite_aggregates(conn)
        return conn
    raise ValueError(
        f"Unknown dialect {cfg.dialect!r}. "
        "Set 'dialect: postgres', 'dialect: oracle', or 'dialect: sqlite' "
        "in your config."
    )


class _StddevSampAggregate:
    """Welford's online algorithm for sample standard deviation —
    registered as the SQLite aggregate ``STDDEV_SAMP`` since SQLite
    doesn't ship the SQL/2008 standard aggregate natively.

    Numerically stable single-pass: tracks running mean + sum of
    squared deviations (``m2`` in Welford notation, lowercased here
    so pyright's ``reportConstantRedefinition`` doesn't trip on
    the per-step reassignment).
    Returns NULL when n < 2 (matching the SQL standard semantic where
    sample stddev of a single value is undefined, not 0).
    """

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0

    def step(self, value: Any) -> None:  # typing-smell: ignore[explicit-any]: SQLite aggregate step receives whatever the SQL column resolves to (NULL/INT/REAL/TEXT)
        if value is None:
            return
        x = float(value)
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.m2 += delta * delta2

    def finalize(self) -> float | None:
        if self.n < 2:
            return None
        return (self.m2 / (self.n - 1)) ** 0.5  # SQRT(m2 / (n-1))


def _register_sqlite_aggregates(conn: Any) -> None:  # typing-smell: ignore[explicit-any]: aiosqlite/sqlite3 connection has no Protocol covering create_aggregate
    """Register the SQL aggregates SQLite doesn't ship that the schema
    SQL needs.

    Today: ``STDDEV_SAMP``. Future additions land here so the SQLite
    connection looks SQL-standard from the schema's point of view.
    """
    conn.create_aggregate("STDDEV_SAMP", 1, _StddevSampAggregate)


def execute_script(
    cur: Any, sql: str, *, dialect: Dialect, oracle_insert_batch: int = 500,  # typing-smell: ignore[explicit-any]: sync DB-API 2.0 cursor — psycopg2 / oracledb / sqlite3 share no Protocol
) -> None:
    """Run a multi-statement SQL string against ``cur``.

    Postgres (psycopg2): the whole string in one ``execute`` call works.
    Oracle (oracledb): ``cursor.execute`` requires single statements (not
    PL/SQL blocks; not ``;``-separated). Splits via
    ``split_oracle_script`` and executes each statement individually,
    surfacing which statement (out of N) failed and the first 1500
    characters of its body for triage.
    SQLite (sqlite3): the connection's ``executescript`` method handles
    multi-statement scripts natively — but the ``cur`` parameter is the
    cursor, not the connection. Iterate per-statement (split on ``;``
    boundaries with the same comment-aware splitter the Oracle path
    uses) for symmetry with Oracle's per-statement error surfacing.

    Oracle bulk-INSERT batching (R.4.a): consecutive
    ``INSERT INTO same_table VALUES (...)`` statements get coalesced
    into ``INSERT ALL`` blocks of ``oracle_insert_batch`` rows. Cuts
    Phase R seed apply from ~30+ minutes (60k per-row round-trips at
    ~20ms each) to ~30 seconds. Set ``oracle_insert_batch=1`` to
    disable batching for debug.
    """
    if dialect is Dialect.POSTGRES:
        cur.execute(sql)
        return
    if dialect is Dialect.SQLITE:
        # The script is one logical unit. SQLite's connection-level
        # ``executescript`` handles multi-statement scripts directly.
        # We get the connection from the cursor — both stdlib's
        # sqlite3.Cursor and DB-API 2.0 cursors expose ``.connection``.
        cur.connection.executescript(sql)
        return
    statements = split_oracle_script(sql)
    if oracle_insert_batch > 1:
        statements = batch_oracle_inserts(
            statements, batch_size=oracle_insert_batch,
        )
    for i, stmt in enumerate(statements):
        try:
            cur.execute(stmt)
        except Exception as e:
            preview = stmt.strip()[:1500]
            raise RuntimeError(
                f"Oracle stmt #{i} failed ({type(e).__name__}: {e})\n"
                f"  Preview: {preview}"
            ) from e


def split_oracle_script(sql: str) -> list[str]:
    """Split an Oracle-style script into individual statements.

    Handles PL/SQL blocks (anything starting with ``BEGIN`` or
    ``DECLARE`` and ending with ``END;``) as one unit; everything else
    splits on bare ``;``.

    Trailing-semicolon contract differs between the two:

    - **PL/SQL blocks**: the ``;`` is part of the ``END;`` terminator
      and Oracle's parser rejects the block without it
      (PLS-00103 "encountered end-of-file"). Keep it.
    - **Plain SQL statements**: ``oracledb.Cursor.execute`` rejects
      a trailing ``;`` ("invalid character"). Strip it.
    """
    return _split_oracle_script_impl(sql)


_INSERT_HEAD_RE = __import__("re").compile(
    r"^\s*INSERT\s+INTO\s+(\S+)\s*(\([^)]*\))\s*VALUES\s*",
    __import__("re").IGNORECASE | __import__("re").DOTALL,
)

# Match the FIRST value in a VALUES tuple (the PK-id column for our seed
# tables). Pattern: leading "(", optional whitespace, then either a
# quoted string ('...') or a bare token. Used by batch_oracle_inserts
# to detect same-id rows that would collide under Oracle's INSERT ALL +
# IDENTITY behavior (the IDENTITY column allocates one value PER
# STATEMENT, not per row, so same-id rows in one INSERT ALL violate the
# composite (id, entry) PK).
_FIRST_VALUE_RE = __import__("re").compile(
    r"^\(\s*'((?:[^']|'')*)'",
)


def batch_oracle_inserts(
    statements: list[str], *, batch_size: int = 500,
) -> list[str]:
    """Coalesce consecutive ``INSERT INTO same_table ... VALUES (...)``
    statements into Oracle ``INSERT ALL`` blocks of up to ``batch_size``
    rows each.

    Format produced::

        INSERT ALL
          INTO sasquatch_pr_transactions (col1, col2) VALUES ('a', 'b')
          INTO sasquatch_pr_transactions (col1, col2) VALUES ('c', 'd')
          ...
        SELECT 1 FROM dual

    Cuts Phase R seed-apply round-trips from ~60k to ~120 (60k rows /
    500 per batch). Each Oracle round-trip is ~10-30ms remote, so the
    total seed-insert time drops from ~20 minutes to ~30 seconds.

    Statements that DON'T match the simple ``INSERT INTO foo VALUES``
    shape (CREATE TABLE, ALTER, complex INSERT...SELECT, PL/SQL blocks,
    etc.) pass through unchanged. The matcher only batches statements
    whose ``INSERT INTO <table> (<cols>)`` head is identical to the
    accumulating batch's head — different tables / column lists flush
    the current batch before starting a new one.
    """
    if batch_size < 2:
        return statements

    out: list[str] = []
    pending_head: str | None = None
    pending_table: str | None = None
    pending_cols: str | None = None
    pending_values: list[str] = []
    # PK ids in the current batch — Oracle's IDENTITY column allocates
    # ONE value per INSERT ALL statement (not per row). With composite
    # PK (id, entry), two rows with the same id in one INSERT ALL get
    # the same entry → ORA-00001 unique violation. Track ids to flush
    # before adding a duplicate.
    pending_ids: set[str] = set()

    def _flush() -> None:
        nonlocal pending_head, pending_table, pending_cols, pending_values
        nonlocal pending_ids
        if not pending_values:
            return
        if len(pending_values) == 1 and pending_head is not None:
            # Single-row batch: just re-emit as a regular INSERT INTO.
            out.append(
                f"INSERT INTO {pending_table} {pending_cols} VALUES "
                f"{pending_values[0]}"
            )
        elif pending_head is not None:
            into_clauses = "\n".join(
                f"  INTO {pending_table} {pending_cols} VALUES {v}"
                for v in pending_values
            )
            out.append(f"INSERT ALL\n{into_clauses}\nSELECT 1 FROM dual")
        pending_head = None
        pending_table = None
        pending_cols = None
        pending_values = []
        pending_ids = set()

    for stmt in statements:
        m = _INSERT_HEAD_RE.match(stmt)
        if m is None:
            _flush()
            out.append(stmt)
            continue
        table = m.group(1)
        cols = m.group(2)
        head_key = f"{table.lower()} {cols}"
        # Extract the VALUES tuple (everything after the matched head).
        values_part = stmt[m.end():].strip().rstrip(";").strip()
        # Pull the first PK column value (the row's id) for collision
        # detection. Falls back to a unique sentinel if the regex
        # misses — keeps the batcher safe for non-conforming shapes.
        id_match = _FIRST_VALUE_RE.match(values_part)
        row_id = (
            id_match.group(1) if id_match is not None
            else f"__no_id_{len(pending_values)}__"
        )
        if pending_head is not None and pending_head != head_key:
            _flush()
        # Same-id collision: flush before adding so the new row starts
        # a fresh INSERT ALL block (with a fresh IDENTITY allocation).
        if row_id in pending_ids:
            _flush()
        if pending_head is None:
            pending_head = head_key
            pending_table = table
            pending_cols = cols
        pending_values.append(values_part)
        pending_ids.add(row_id)
        if len(pending_values) >= batch_size:
            _flush()
    _flush()
    return out


def _split_oracle_script_impl(sql: str) -> list[str]:
    """Inner implementation kept separate to avoid recursion through the
    public ``split_oracle_script`` symbol when adding tests that mock it.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_plsql = False
    for raw_line in sql.splitlines():
        line = raw_line.rstrip()
        # Strip the trailing ``-- comment`` before checking for the
        # statement terminator; a ``;`` inside a SQL line-comment is
        # commentary, not a statement boundary, and treating it as one
        # falsely splits the next CREATE block off into a comment-only
        # "statement" that Oracle rejects with ORA-00900.
        code = line.split("--", 1)[0].rstrip()
        stripped_code = code.strip()
        if not in_plsql and stripped_code.upper().startswith(
            ("BEGIN ", "DECLARE")
        ):
            in_plsql = True
        buffer.append(line)
        if in_plsql:
            # PL/SQL block ends at "END;" (the ; is the PL/SQL
            # statement terminator — keep it, the parser needs it).
            if stripped_code.upper().endswith("END;"):
                statements.append("\n".join(buffer).rstrip())
                buffer = []
                in_plsql = False
        else:
            if stripped_code.endswith(";"):
                # Plain SQL: oracledb rejects the trailing ; — strip.
                stmt = "\n".join(buffer).rstrip().rstrip(";")
                # Skip comment-only buffers (the buffer is all whitespace
                # + comment text). We only need stripped-code non-empty;
                # the actual SQL body content doesn't matter for emit.
                if stripped_code:
                    statements.append(stmt)
                buffer = []
    # Trailing buffer (no final semicolon)
    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


# ---------------------------------------------------------------------------
# X.2.n.2 — Async connection pool abstraction
# ---------------------------------------------------------------------------
#
# The App2 server is asyncio-based (Starlette). Sync DB drivers block the
# event loop, forcing every visual fetch through a threadpool offload that
# silently serializes when the pool fills. Two costs compound:
#
#   1. Threadpool slots cap concurrency at ~40, AND every slot held while
#      a SQL query runs blocks one worker. With N visuals on a sheet,
#      one user's refresh can saturate the pool.
#   2. Every visual fetch opens a fresh DB connection (TLS, auth,
#      role assumption) — 50 ms of pure setup cost on every request,
#      multiplied by visual count.
#
# This abstraction is the seam where both costs go away. ``acquire()``
# checks out a pre-opened connection from a per-dialect native pool;
# the connection returns to the pool on context exit instead of being
# torn down. With async drivers (psycopg3 / oracledb async / aiosqlite)
# the asyncio loop stays free between SQL await points, so concurrent
# visuals truly run in parallel without burning threadpool slots.
#
# The protocol is intentionally minimal — ``acquire()`` returning an
# async context manager and ``close()`` for shutdown — so per-dialect
# native pools (whose surface APIs all differ slightly: psycopg_pool's
# ``connection()`` vs oracledb's ``acquire()``) wrap cleanly behind it.


class AsyncCursor(Protocol):
    """Minimal async DB-API cursor surface used by ``execute_visual_sql_async``.

    All three drivers (psycopg / oracledb / aiosqlite) implement at
    least this much under their async modes. The protocol intentionally
    omits ``close()`` — drivers split between sync- and async-close
    semantics, and the executor's call site does the right duck-typed
    thing with ``getattr + __await__`` rather than declaring a single
    contract here.
    """

    @property
    def description(self) -> Sequence[Sequence[Any]] | None: ...  # typing-smell: ignore[explicit-any]: DB-API 2.0 description tuples carry mixed types per column
    async def fetchall(self) -> list[Any]: ...  # typing-smell: ignore[explicit-any]: rows are heterogeneous by definition; per-call shape lives in the SQL contract


class AsyncConnection(Protocol):
    """Minimal async DB-API connection surface used by the App2 executor.

    The single async ``execute(query, params)`` method returns a
    cursor with results pre-staged — psycopg / oracledb / aiosqlite
    all support this one-shot shape, so the executor doesn't need
    a separate ``cursor()`` step.
    """

    async def execute(
        self, query: str, params: Any = ..., /,  # typing-smell: ignore[explicit-any]: bind params dict — driver coerces per-driver; no shared Protocol covers both psycopg's dict + oracledb's dict + aiosqlite's tuple/dict
    ) -> AsyncCursor: ...


class AsyncConnectionPool(Protocol):
    """Uniform async DB-connection pool across PG / Oracle / SQLite.

    Each dialect's native pool has a different surface
    (``psycopg_pool.AsyncConnectionPool.connection()``,
    ``oracledb`` async pool's ``acquire()``, no built-in pool for
    SQLite). This protocol normalizes them behind one ``acquire()``
    method that returns an async context manager yielding an
    ``AsyncConnection``, plus an ``async close()`` for
    application-shutdown lifecycle.

    The connection yielded by ``acquire()`` returns to the pool on
    context exit (or is closed for SQLite, which has no real pool).
    Callers MUST use ``async with pool.acquire() as conn`` — a leaked
    acquire blocks one pool slot until interpreter shutdown.
    """

    def acquire(self) -> AbstractAsyncContextManager[AsyncConnection]: ...
    async def close(self) -> None: ...


class _AsyncPgPool:
    """Thin wrapper around ``psycopg_pool.AsyncConnectionPool``.

    psycopg_pool's per-checkout method is ``connection()`` (not
    ``acquire()``); rename here for cross-dialect uniformity. The
    pool itself is created with ``open=False`` so the caller can
    ``await pool.open()`` explicitly and surface connection-failure
    errors at server-startup time rather than at first request.
    """

    def __init__(self, pool: Any) -> None:  # typing-smell: ignore[explicit-any]: psycopg_pool.AsyncConnectionPool has no installed stubs at strict
        self._pool = pool

    def acquire(self) -> AbstractAsyncContextManager[Any]:  # typing-smell: ignore[explicit-any]: psycopg's connection() returns AsyncConnection; widened to Any to match the cross-dialect AsyncConnectionPool Protocol return
        return self._pool.connection()

    async def close(self) -> None:
        await self._pool.close()


class _AsyncOraclePool:
    """Thin wrapper around ``oracledb.create_pool_async()``.

    oracledb's async pool already exposes ``acquire()`` returning an
    async context manager — this wrapper exists for API parity (and
    so the rest of the code doesn't import oracledb directly).
    """

    def __init__(self, pool: Any) -> None:  # typing-smell: ignore[explicit-any]: oracledb async pool surface isn't typed in the public stubs at strict
        self._pool = pool

    def acquire(self) -> AbstractAsyncContextManager[Any]:  # typing-smell: ignore[explicit-any]: oracledb pool.acquire() returns AsyncConnection; widened to Any to match Protocol return shape
        return self._pool.acquire()

    async def close(self) -> None:
        await self._pool.close()


class _AsyncSqlitePool:
    """No-pool wrapper around ``aiosqlite.connect``.

    SQLite is local file (or in-memory) access — pooling would just
    hold open file handles to the same file. Each ``acquire()``
    opens a fresh connection and closes it on context exit; the
    ``max_size`` argument is accepted upstream but ignored here.
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def acquire(self) -> AbstractAsyncContextManager[AsyncConnection]:
        return self._acquire()

    @asynccontextmanager
    async def _acquire(self) -> AsyncGenerator[AsyncConnection, None]:
        import aiosqlite  # noqa: PLC0415

        async with aiosqlite.connect(self._path) as conn:
            yield conn  # type: ignore[misc]: aiosqlite Connection conforms to AsyncConnection protocol

    async def close(self) -> None:
        return None


async def make_connection_pool(
    cfg: Config, *, max_size: int = 10,
) -> AsyncConnectionPool:
    """Open an ``AsyncConnectionPool`` against ``cfg.demo_database_url``.

    Branches on ``cfg.dialect`` (mirrors ``connect_demo_db``):
      - Postgres: ``psycopg_pool.AsyncConnectionPool`` with
        ``min_size=1``, ``max_size=N``. Pre-opens so connection
        failures surface here.
      - Oracle: ``oracledb.create_pool_async`` with the same shape.
      - SQLite: thin ``aiosqlite``-per-acquire wrapper (no real pool).

    Args:
      cfg: Loaded Config; ``cfg.demo_database_url`` and ``cfg.dialect``
        drive both URL parsing and driver selection.
      max_size: Pool size cap. Defaults to 10 — enough for a typical
        sheet's visuals to fetch concurrently without queueing. Tune
        upward for high-fan-in dashboards or multi-user demo loads.

    Raises:
      ImportError: matching async driver isn't installed
        (``psycopg[binary,pool]`` for PG, ``oracledb`` for Oracle,
        ``aiosqlite`` for SQLite). Each ImportError names the
        extras-install command.
      ValueError: ``cfg.demo_database_url`` unset or
        ``cfg.dialect`` unrecognized.
    """
    if cfg.demo_database_url is None:
        raise ValueError(
            "cfg.demo_database_url is unset; set it in your config YAML "
            "or via QS_GEN_DEMO_DATABASE_URL."
        )
    if cfg.dialect is Dialect.POSTGRES:
        try:
            from psycopg_pool import AsyncConnectionPool as _PgAsyncPool
        except ImportError as e:
            raise ImportError(
                "psycopg_pool is required for the async Postgres pool. "
                "Install it with: pip install 'quicksight-gen[demo]' "
                "(the [pool] extra is bundled into psycopg[binary,pool])."
            ) from e
        # ``open=False`` so we await ``open()`` here — surfaces bad DSNs
        # / unreachable hosts at server-startup time instead of inside
        # the first request handler.
        pool = _PgAsyncPool(
            cfg.demo_database_url, min_size=1, max_size=max_size, open=False,
        )
        await pool.open()
        return _AsyncPgPool(pool)
    if cfg.dialect is Dialect.ORACLE:
        try:
            import oracledb  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs
        except ImportError as e:
            raise ImportError(
                "oracledb is required for Oracle connections. "
                "Install it with: pip install 'quicksight-gen[demo-oracle]'"
            ) from e
        # oracledb's async pool factory is sync (it returns the pool
        # object; connection acquisition is the async part).
        pool = oracledb.create_pool_async(
            dsn=oracle_dsn(cfg.demo_database_url), min=1, max=max_size,
        )
        return _AsyncOraclePool(pool)
    if cfg.dialect is Dialect.SQLITE:
        # Probe the import here so a missing aiosqlite surfaces at
        # pool-construction time (server startup) instead of inside
        # the first request handler.
        try:
            import aiosqlite as _aiosqlite_probe  # noqa: PLC0415, F401
        except ImportError as e:
            raise ImportError(
                "aiosqlite is required for the async SQLite pool. "
                "Install it with: pip install 'quicksight-gen[serve]'"
            ) from e
        del _aiosqlite_probe
        return _AsyncSqlitePool(sqlite_path(cfg.demo_database_url))
    raise ValueError(
        f"Unknown dialect {cfg.dialect!r}. "
        "Set 'dialect: postgres', 'dialect: oracle', or 'dialect: sqlite' "
        "in your config."
    )
