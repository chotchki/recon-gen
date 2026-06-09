"""Dialect-aware database connection + script execution helpers (P.9d).

Used by the CLI (``demo apply``) and the e2e harness fixtures. Both
need to:

  - Open a DB-API 2.0 connection against Postgres (psycopg2), Oracle
    (oracledb), or DuckDB (in-process ``duckdb`` wheel), keyed off
    ``cfg.dialect``.
  - Run multi-statement DDL/DML scripts. psycopg2 accepts the whole
    script in one ``cursor.execute`` call; oracledb requires per-
    statement execution and treats PL/SQL blocks (``BEGIN…END;``) as
    one unit; DuckDB accepts whole scripts via ``connection.execute``.

Both PG + Oracle surfaces existed inline in ``cli.py`` before P.9d.
Lifting them here lets ``tests/e2e/test_harness_end_to_end.py`` consume
the same helpers instead of hardcoding psycopg2 (which raised
``ProgrammingError`` at setup when the harness ran against an Oracle
config — see PLAN_ARCHIVE.md P.9d). CA swapped DuckDB in as the
local-iteration default; CB.8 dropped the prior SQLite arm.
"""

from __future__ import annotations

# typing-smell: ignore-file[no-sqlite-prose]: sqlite_path() URL helper is dead post-CB.8 but still defined + exported; removal tracked under task #323 (CR.15.followup). Until then file-suppress so the lint stays useful on the rest of the file.

import asyncio
import sys
import time
from pathlib import Path
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    contextmanager,
)
from typing import Any, Protocol, cast
from urllib.parse import parse_qs, urlparse

from recon_gen.common.config import Config
from recon_gen.common.sql import Dialect


__all__ = [
    "AsyncConnection",
    "AsyncConnectionPool",
    "AsyncCursor",
    "batch_oracle_inserts",
    "connect_demo_db",
    "execute_script",
    "make_connection_pool",
    "open_demo_db",
    "oracle_dsn",
    "split_oracle_script",
    "duckdb_path",
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


def make_demo_database_url(dialect: Dialect, path: str | Path) -> str:
    """Build the canonical ``demo_database_url`` for a dialect + path.

    Inverse of ``duckdb_path`` / ``sqlite_path``. Used by test fixtures
    and config-building helpers so the URL-scheme literal lives in one
    place instead of being copy-pasted as ``f"duckdb:///{path}"`` at
    every callsite.

    PG / Oracle URLs aren't constructible from a bare path — they need
    user / password / host — so this raises for those dialects. Callers
    that need a PG / Oracle URL build it from full config (cfg.yaml or
    env), not from a path.
    """
    if dialect is Dialect.DUCKDB:
        return f"duckdb:///{path}"
    raise ValueError(
        f"make_demo_database_url is path-based and only meaningful for "
        f"file-backed dialects (DuckDB). Got {dialect!r}; build the URL "
        f"from full config for that dialect."
    )


def duckdb_path(url: str) -> str:
    """Translate a ``duckdb:///path/to/db.duckdb`` URL to a path string.

    Accepts the SQLAlchemy-style ``duckdb:///`` triple-slash form (the
    fourth slash starts the absolute path component) and the
    ``duckdb://:memory:`` in-memory form. Also accepts a bare path
    string for ergonomics — if the value isn't a recognized URL
    scheme, it's returned unchanged so the caller can pass the raw
    DuckDB file path directly. Mirrors the ``sqlite_path`` contract.

    Examples:
      - ``duckdb:///tmp/demo.duckdb`` → ``/tmp/demo.duckdb``
      - ``duckdb:///./relative.duckdb`` → ``./relative.duckdb``
      - ``duckdb://:memory:`` → ``:memory:``
      - ``/tmp/demo.duckdb`` → ``/tmp/demo.duckdb``
    """
    if url == "duckdb://:memory:" or url.endswith(":memory:"):
        return ":memory:"
    if url.startswith("duckdb:///"):
        return url[len("duckdb:///"):]
    if url.startswith("duckdb://"):
        return url[len("duckdb://"):]
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


def connect_demo_db(cfg: Config) -> "SyncConnection":  # CB.16 — replaces the `-> Any` escape hatch with the structural Protocol defined below. Per-driver concrete types (psycopg.Connection / oracledb.Connection / duckdb.DuckDBPyConnection) all match SyncConnection structurally, so callers downcast at the boundary if they need driver-specific features.
    """Open a DB-API 2.0 connection to ``cfg.demo_database_url``.

    Branches on ``cfg.dialect``:
      - Postgres: psycopg (v3, from the ``[prod]`` extra).
      - Oracle: oracledb thin client (from the ``[prod]`` extra).
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
            "or via RECON_GEN_DEMO_DATABASE_URL."
        )
    if cfg.dialect is Dialect.POSTGRES:
        try:
            import psycopg
        except ImportError as e:
            raise ImportError(
                "psycopg is required for Postgres connections. "
                "Install it with: pip install 'recon-gen[prod]'"
            ) from e
        return cast("SyncConnection", psycopg.connect(cfg.demo_database_url))  # psycopg.Connection.cursor accepts an optional `scrollable` kwarg; structurally compatible at the call shape we use, but pyright needs the cast
    if cfg.dialect is Dialect.ORACLE:
        try:
            import oracledb  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs
        except ImportError as e:
            raise ImportError(
                "oracledb is required for Oracle connections. "
                "Install it with: pip install 'recon-gen[prod]'"
            ) from e
        conn = oracledb.connect(oracle_dsn(cfg.demo_database_url))
        # CB.14 — pin session NLS to ISO so string-shaped date literals
        # (e.g. spine generators emitting "2026-01-15") parse without
        # ORA-01843. Oracle's default NLS_DATE_FORMAT (DD-MON-YY) made
        # the spine inserts in tests/e2e/db/test_inv_direct.py fail at
        # cur.execute(sql, params); pinning here makes every connection
        # speak the same date dialect regardless of the image's locale.
        nls_cur: Any = conn.cursor()  # typing-smell: ignore[explicit-any]: oracledb.Cursor partial-unknown without stubs; matches the Any-typed `cur` parameter shape used elsewhere in this module
        try:
            nls_cur.execute("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD'")
            nls_cur.execute(
                "ALTER SESSION SET NLS_TIMESTAMP_FORMAT = 'YYYY-MM-DD HH24:MI:SS.FF'"
            )
        finally:
            nls_cur.close()
        return cast("SyncConnection", conn)  # oracledb.Connection.cursor accepts an optional `scrollable` kwarg; same shape as psycopg above
    if cfg.dialect is Dialect.DUCKDB:
        # CA.3 — DuckDB is a core dialect (in `[project.dependencies]`,
        # not extras). Pure-Python wheel, no extra install friction.
        # `STDDEV_SAMP` ships natively (the inv_pair_rolling_anomalies
        # matview's aggregate); no need for the SQLite-style aggregate
        # registration. FK enforcement is on by default in DuckDB.
        #
        # CA.8 — `RECON_GEN_DB_READ_ONLY=1` env override opens the file
        # in read-only mode. DuckDB enforces single-writer-per-file
        # across processes; pytest-xdist workers in the db / app2 tier
        # all need shared read access without conflicting locks. Per
        # the DuckDB docs (https://duckdb.org/docs/current/clients/
        # python/dbapi#read_only-connections): "Read-only mode is
        # required if multiple Python processes want to access the
        # same database file at the same time." Runner sets the env
        # for DuckDB cells at pytest-launch time; production CLI
        # invocations (schema/data/seed apply) run without it and
        # open read-write.
        import duckdb
        from recon_gen.common.env_keys import RECON_GEN_DB_READ_ONLY
        path = duckdb_path(cfg.demo_database_url)
        read_only = bool(RECON_GEN_DB_READ_ONLY.get_or_none())
        return cast("SyncConnection", duckdb.connect(path, read_only=read_only))  # DuckDBPyConnection.commit returns self for chaining; SyncConnection.commit returns None per PEP 249. The two-arg `cur.execute(sql, params)` shape callers use works on both
    raise ValueError(
        f"Unknown dialect {cfg.dialect!r}. "
        "Set 'dialect: postgres', 'dialect: oracle', or 'dialect: duckdb' "
        "in your config."
    )


@contextmanager
def open_demo_db(cfg: Config) -> Generator["SyncConnection"]:
    """Context-managed `connect_demo_db(cfg)` — commits on success,
    rolls back on exception (best-effort), always closes.

    The per-driver `with conn:` shapes diverge (psycopg keeps the
    connection open + commits the txn; oracledb / duckdb close on
    exit), so `SyncConnection.__enter__/__exit__` deliberately stays
    off the Protocol. This helper papers over the divergence with
    one consistent shape: commit-on-success + always-close, identical
    on every dialect. Use it for ETL hooks + any "open, write a
    batch, close" pattern.

    **Transaction semantics caveat (DuckDB).** PG and Oracle use
    implicit-transaction-on-write — every cursor's write joins the
    connection's pending transaction, and `conn.rollback()` undoes
    every pending write across every cursor. DuckDB defaults to
    autocommit-per-cursor — each cursor's writes commit immediately
    when the cursor is closed (or sometimes per-statement), so the
    rollback path can't undo them. For ETL hooks that need
    transactional batching against DuckDB, drive `BEGIN` /
    `COMMIT` / `ROLLBACK` through ONE cursor explicitly and don't
    close it mid-batch. PG/Oracle behave the way you'd expect from
    PEP 249 — `open_demo_db` is fully transactional there.

    Example::

        from recon_gen.common.db import open_demo_db
        from recon_gen.common.etl import write_daily_balance

        with open_demo_db(cfg) as conn:
            cur = conn.cursor()
            try:
                for row in feed:
                    write_daily_balance(cur, cfg.dialect, ...)
            finally:
                cur.close()

    For per-day batches, call `conn.commit()` explicitly in the body
    and a fresh transaction starts on the next write; the final
    commit-on-success becomes a no-op.
    """
    conn = connect_demo_db(cfg)
    try:
        yield conn
    except BaseException:
        # DuckDB's per-cursor autocommit means there may be nothing
        # to roll back even when an exception fires mid-batch — the
        # driver raises "cannot rollback - no transaction is active".
        # Swallow it: the helper's promise on DuckDB is best-effort
        # rollback, not strict-transactional rollback (see docstring).
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — driver-specific "no transaction" shapes vary
            pass
        raise
    else:
        conn.commit()
    finally:
        conn.close()



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
    if dialect is Dialect.DUCKDB:
        # CA.6 — DuckDB's Python driver accepts multi-statement strings
        # as a single execute() call. CA.10 — but the seed apply emits
        # ~127k individual INSERT statements; one-shot execute() parses
        # + executes them serially (~179s on a sasquatch-scale seed).
        # CA.10 routed consecutive INSERT runs through a multi-row VALUES
        # coalescer (~86s on sasquatch-scale; ~21s on M1 post-typed-literal-fix).
        # CA.11 — if pyarrow is installed, take the structural fast path:
        # finditer-scan the seed text for INSERTs in one pass (skipping the
        # 8.7s comment-aware `_split_sqlite_statements`), parse VALUES bodies
        # with the same `_parse_simple_values` walker, and bulk-load via
        # `con.register(pa.Table) + INSERT SELECT`. Measured 21.8s → ~3.8s
        # on the 131k-row sasquatch_pr seed. Falls back to the CA.10 path
        # when pyarrow isn't available (operator on minimal `[dev]` install,
        # not `[prod]`).
        if _try_import_pyarrow() is not None:
            _apply_seed_via_duckdb_pyarrow(cur, sql)
        else:
            _execute_with_coalesced_inserts(cur, sql, _flush_duckdb_multivalues)
        return
    # CA.13 — Oracle fast path: if oracledb 3.4+ is installed AND pyarrow
    # is available, use Connection.direct_path_load() (server-side bypass
    # of the SQL parser; writes blocks above the High Water Mark). On the
    # 131k-row sasquatch_pr seed this drops Oracle apply from ~10 minutes
    # to ~30 seconds (~20× win — Oracle is the dialect with the biggest
    # leverage from this work). Falls back to the existing INSERT-ALL
    # coalescer if either dep is absent or if the connection doesn't
    # expose direct_path_load (oracledb < 3.4.0).
    if (
        _try_import_pyarrow() is not None
        and hasattr(cur, "connection")
        and hasattr(cur.connection, "direct_path_load")
    ):
        _apply_seed_via_oracle_dpl(cur, sql)
        return
    statements = split_oracle_script(sql)
    if oracle_insert_batch > 1:
        statements = batch_oracle_inserts(
            statements, batch_size=oracle_insert_batch,
        )
    for i, stmt in enumerate(statements):
        try:
            _execute_oracle_stmt_with_lock_retry(cur, stmt)
        except Exception as e:
            preview = stmt.strip()[:1500]
            raise RuntimeError(
                f"Oracle stmt #{i} failed ({type(e).__name__}: {e})\n"
                f"  Preview: {preview}"
            ) from e


# Oracle lock-timeout error codes worth retrying. Both surface when a
# concurrent session holds a lock on the object we're about to modify:
#   ORA-00054 — "resource busy and acquire with NOWAIT specified or
#               timeout expired" (e.g. DROP/ALTER against a table
#               another session is touching).
#   ORA-04021 — "timeout occurred while waiting to lock object" — the
#               data-dictionary lock. DDL serializes on the dictionary,
#               so two sessions running `schema apply` in parallel (e.g.
#               two matrix cells against the same multi-tenant Oracle)
#               deadlock here.
# Both are transient: the holding session releases when its statement
# finishes. Retry with exponential backoff before giving up.
_ORACLE_LOCK_TIMEOUT_CODES: tuple[str, ...] = ("ORA-00054", "ORA-04021")
# Pre-retry sleeps (seconds). len = number of retries; total = 5
# attempts (1 initial + 4 retries), max ~75s of waiting. Sized to
# outlast a sibling cell's full `schema apply` against the same
# multi-tenant Oracle (~30-90s of DDL when the matrix fans out) —
# a short backoff would just exhaust retries while the holder is
# still mid-run.
_ORACLE_LOCK_RETRY_BACKOFF_S: tuple[float, ...] = (5.0, 10.0, 20.0, 40.0)


def _execute_oracle_stmt_with_lock_retry(cur: Any, stmt: str) -> None:  # typing-smell: ignore[explicit-any]: oracledb cursor — see execute_script's same suppression
    """Run one Oracle statement, retrying on ORA-00054 / ORA-04021.

    The lock-timeout error codes (see ``_ORACLE_LOCK_TIMEOUT_CODES``)
    are transient — a sibling session is mid-DDL on the same object
    (or the data dictionary). Retry with exponential backoff. Any
    other error propagates immediately (no point retrying a syntax
    error). Each retry logs to stderr so a stalling matrix cell is
    visible rather than looking hung.
    """
    last_exc: Exception | None = None
    for attempt in range(len(_ORACLE_LOCK_RETRY_BACKOFF_S) + 1):
        try:
            cur.execute(stmt)
            return
        except Exception as exc:  # noqa: BLE001 — re-raised below unless it's a lock timeout
            if not any(code in str(exc) for code in _ORACLE_LOCK_TIMEOUT_CODES):
                raise
            last_exc = exc
            if attempt < len(_ORACLE_LOCK_RETRY_BACKOFF_S):
                sleep_s = _ORACLE_LOCK_RETRY_BACKOFF_S[attempt]
                sys.stderr.write(
                    f"db: Oracle lock timeout ({type(exc).__name__}) — "
                    f"retry {attempt + 1}/{len(_ORACLE_LOCK_RETRY_BACKOFF_S)} "
                    f"in {sleep_s:.0f}s\n"
                )
                sys.stderr.flush()
                time.sleep(sleep_s)
    # Exhausted retries — re-raise the last lock-timeout error so the
    # caller's `Oracle stmt #N failed` wrapper carries the real cause.
    assert last_exc is not None  # loop body sets it before the last iteration
    raise last_exc


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


# X.4.j.sqlite-binds — full INSERT pattern for SQLite executemany path.
# Captures the table, the column list (without parens), and the VALUES
# tuple body (without parens). Used to coalesce consecutive same-shape
# INSERTs into a single executemany call. Stricter than _INSERT_HEAD_RE
# because we also need the body — anything not matching falls back to
# per-statement cur.execute.
_INSERT_FULL_RE = __import__("re").compile(
    r"^\s*INSERT\s+INTO\s+(\S+)\s*\(([^)]+)\)\s*VALUES\s*\(([^;]+)\)\s*;?\s*$",
    __import__("re").IGNORECASE | __import__("re").DOTALL,
)


class _TypedSqlLiteral:
    """Sentinel for ``<KEYWORD> '<text>'`` typed-literal forms — e.g.
    ``TIMESTAMP '2026-03-03 00:00:01'`` or ``DATE '2026-03-03'``. The
    parser emits these instead of plain strings so the flush stage
    can re-render them in dialect-appropriate form (DuckDB / PG /
    Oracle keep the typed prefix; SQLite collapses to bare text via
    `_typed_lit_to_sqlite_bind`).

    Without this, the seed's ~131k INSERTs against the `transactions`
    table — every one carrying a ``TIMESTAMP '...'`` column — bail out
    of the simple-values parser and fall through to per-row
    ``cur.execute``, defeating the multi-row VALUES coalescer entirely
    (CA.10 followup; diagnosed at 99.8% bail rate against sasquatch_pr).
    """

    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str) -> None:
        self.kind = kind
        self.text = text

    def __repr__(self) -> str:
        return f"_TypedSqlLiteral(kind={self.kind!r}, text={self.text!r})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, _TypedSqlLiteral)
            and self.kind == other.kind
            and self.text == other.text
        )

    def __hash__(self) -> int:
        return hash((self.kind, self.text))


# Typed-literal keywords the parser recognizes. SQL has more (INTERVAL
# with WITH TIME ZONE, etc.) but these are the only ones the L2 seed
# emits today. Add to this set if the emit grows another shape.
_TYPED_LITERAL_KEYWORDS = frozenset({"TIMESTAMP", "DATE"})


def _parse_simple_values(body: str) -> tuple[object, ...] | None:
    """Walk a VALUES tuple body and emit Python primitives, or None if
    the body uses any form this parser can't safely handle.

    Recognized literals: ``'string'`` (no escape sequences),
    ``NULL``, integers, floats, and ``<KEYWORD> '<text>'`` typed
    literals (``TIMESTAMP '...'`` / ``DATE '...'``) which round-trip
    via the `_TypedSqlLiteral` sentinel so the flush stage can
    re-render dialect-appropriately. JSON metadata strings happen to
    embed double-quotes which never collide with the single-quote
    delimiter. Anything else (function call, ``''`` escape, hex
    literal, etc.) returns None — the caller passes the raw INSERT
    through to ``cur.execute`` instead.
    """
    out: list[object] = []
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        if c == "'":
            j = body.find("'", i + 1)
            if j == -1:
                return None
            # Reject embedded `''` escapes — current seed doesn't emit
            # them, and decoding here would mis-handle them by treating
            # the second `'` as the closing quote. Bail to executescript.
            if j + 1 < n and body[j + 1] == "'":
                return None
            out.append(body[i + 1:j])
            i = j + 1
        elif (c == "N" or c == "n") and body[i:i + 4].upper() == "NULL":
            out.append(None)
            i += 4
        elif c == "-" or c.isdigit():
            j = i + 1
            while j < n and body[j] not in ", \n\t":
                j += 1
            tok = body[i:j]
            try:
                out.append(float(tok) if "." in tok or "e" in tok.lower() else int(tok))
            except ValueError:
                return None
            i = j
        elif c.isalpha():
            # Possible `<KEYWORD> '<text>'` typed literal (TIMESTAMP /
            # DATE). Read the keyword, require whitespace, then a
            # quoted string. Anything else bails.
            j = i + 1
            while j < n and body[j].isalpha():
                j += 1
            kw = body[i:j].upper()
            if kw not in _TYPED_LITERAL_KEYWORDS:
                return None
            # Skip whitespace.
            while j < n and body[j].isspace():
                j += 1
            if j >= n or body[j] != "'":
                return None
            k = body.find("'", j + 1)
            if k == -1:
                return None
            # Same `''`-escape bail rule as plain strings.
            if k + 1 < n and body[k + 1] == "'":
                return None
            out.append(_TypedSqlLiteral(kw, body[j + 1:k]))
            i = k + 1
        else:
            return None
    return tuple(out)



def _execute_with_coalesced_inserts(
    cur: Any,  # typing-smell: ignore[explicit-any]: sync DB-API 2.0 cursor — see execute_script
    sql: str,
    flush_fn: "Callable[[Any, str, str, list[tuple[object, ...]]], None]",  # typing-smell: ignore[explicit-any]: per-dialect bulk-insert strategy callback — receives the same Any-typed cursor as `cur` above, see that param's WHY
) -> None:
    """Walk a multi-statement script, coalescing consecutive same-shape
    ``INSERT INTO foo (cols) VALUES (...)`` runs into one bulk call per
    run via ``flush_fn(cur, table, cols, rows)``. Non-INSERT or
    non-conforming statements pass through to ``cur.execute(stmt)``
    so the contract matches ``executescript``.

    The bulk-insert strategy varies per dialect (CA.10): SQLite uses
    ``executemany`` (real C++ bulk path), DuckDB uses multi-row
    ``VALUES (…),(…),…`` literal SQL (DuckDB's executemany is a
    Python loop, ~55× slower than the literal form per probe). Oracle
    has its own coalescer at ``batch_oracle_inserts`` because its
    INSERT ALL syntax doesn't fit this scanner shape.

    Transaction stays open across the call — caller commits.
    """
    statements = _split_sqlite_statements(sql)

    # Buffered per-(table, cols) INSERT runs; flushed on group change
    # or non-conforming statement.
    pending_table: str | None = None
    pending_cols: str | None = None
    pending_rows: list[tuple[object, ...]] = []

    def _flush() -> None:
        nonlocal pending_table, pending_cols, pending_rows
        if not pending_rows or pending_table is None or pending_cols is None:
            pending_rows = []
            pending_table = None
            pending_cols = None
            return
        flush_fn(cur, pending_table, pending_cols, pending_rows)
        pending_rows = []
        pending_table = None
        pending_cols = None

    for stmt in statements:
        m = _INSERT_FULL_RE.match(stmt)
        if m is not None:
            table, cols, body = m.group(1), m.group(2).strip(), m.group(3)
            row = _parse_simple_values(body)
            if row is not None:
                # Group with current run if shape matches; else flush + start new.
                if pending_table == table and pending_cols == cols:
                    pending_rows.append(row)
                else:
                    _flush()
                    pending_table = table
                    pending_cols = cols
                    pending_rows = [row]
                continue
        # Non-INSERT or unparseable — flush pending, then run raw.
        _flush()
        cur.execute(stmt)
    _flush()



def _flush_duckdb_multivalues(
    cur: Any,  # typing-smell: ignore[explicit-any]: duckdb.DuckDBPyConnection — DB-API 2.0 with no shared Protocol
    table: str, cols: str, rows: list[tuple[object, ...]],
) -> None:
    """DuckDB bulk path: multi-row ``VALUES (…),(…),…`` literal SQL,
    batched in ~1000-row chunks to bound the SQL string size.

    CA.10 — measured 54× faster than DuckDB's executemany at 50k rows
    (22.76s → 0.44s); 2196 → 112772 rows/sec. DuckDB's executemany is
    essentially a Python loop (~5% faster than individual ``execute()``);
    the multi-row literal lets the engine parse once + plan once for
    the whole batch. Sasquatch_pr's 127,554-tx seed apply: ~179s
    (unbatched DuckDB) → ~86s (this path) end-to-end.

    Memory bound: one batch's SQL string holds ~1000 rows × ~150
    chars/row ≈ 150KB peak. Caller is the seed-apply CLI which already
    holds the full SQL script in memory, so this is well within budget.
    """
    BATCH = 1000
    for chunk_start in range(0, len(rows), BATCH):
        chunk = rows[chunk_start:chunk_start + BATCH]
        values = ",".join(
            "(" + ",".join(_render_sql_literal(v) for v in row) + ")"
            for row in chunk
        )
        cur.execute(f"INSERT INTO {table} ({cols}) VALUES {values}")


# CA.11 — pyarrow-based DuckDB fast path. The CA.10 multi-row VALUES path
# still parses + re-renders every value; the bigger latent cost is the
# 8.7s comment-aware `_split_sqlite_statements` walk over 100MB of seed
# text. CA.11 replaces both with a single `finditer` scan + a pyarrow
# `con.register + INSERT SELECT` bulk-load, ~5-6× faster end-to-end.

# Quote-aware INSERT scanner. The body alternation matches either a
# single-quoted string (with doubled-quote escape allowance for safety
# even though the seed parser bails on `''`) OR any non-quote non-`;`
# char. Required because the config-populate text the schema-apply path
# emits embeds operator-authored descriptions containing `;`, `(`, etc.
# inside string literals; the naive `[^;]+` body capture truncated at
# the first quoted-`;`.
# Measured on the 131k-row sasquatch_pr seed: 3.2s scan vs 1.0s with
# the naive `[^;]+` form; still well below `_split_sqlite_statements`
# at 8.7s. The 2.2s of extra Python regex work buys correctness.
_SEED_INSERT_SCAN_RE = __import__("re").compile(
    r"INSERT\s+INTO\s+(\S+)\s*\(([^)]+)\)\s*VALUES\s*\("
    r"((?:'(?:[^']|'')*'|[^;'])+)"
    r"\)\s*;",
    __import__("re").IGNORECASE | __import__("re").DOTALL,
)


def _try_import_pyarrow() -> Any:  # typing-smell: ignore[explicit-any]: lazy-loaded extension; no stubs in [dev], same posture as oracledb's lazy import
    """Return the pyarrow module if installed, else None.

    CA.11 — pyarrow lives in `[prod]` extras alongside oracledb. Minimal
    dev installs without `[prod]` won't have it; the seed-apply path
    transparently falls back to the CA.10 multi-row VALUES coalescer
    instead of crashing.
    """
    try:
        import pyarrow as pa  # noqa: PLC0415 — lazy import is the point
        return pa
    except ImportError:
        return None


def _apply_seed_via_duckdb_pyarrow(cur: Any, sql: str) -> None:  # typing-smell: ignore[explicit-any]: duckdb.DuckDBPyConnection cursor — DB-API 2.0 with no shared Protocol
    """Apply a seed SQL script to DuckDB via pyarrow bulk-load.

    CA.11 — scans the script for INSERT statements via a single
    `finditer` (skips the 8.7s `_split_sqlite_statements` walker),
    parses each VALUES body with the same `_parse_simple_values`
    used by CA.10, groups consecutive same-shape inserts, and flushes
    via `pa.Table.from_pylist + con.register + INSERT SELECT`. The
    DuckDB Arrow ingest path is zero-copy through the C API; no SQL
    parse cost on the DB side per row.

    Order-preserving: residual non-INSERT chunks (DDL, SET commands,
    pragmas, comments) are executed *between* INSERT batches in the
    same order they appear in the script. A pre-fix version queued
    residual until the end of the function, which broke any input
    where `CREATE TABLE foo` appeared before `INSERT INTO foo` —
    the INSERT flushed against a missing table. Now: at every
    boundary where the next INSERT's `(table, cols)` differs from
    the pending group (and at every non-conforming statement),
    flush pending INSERTs first, then execute the residual chunk
    that preceded this match, then start a fresh group. End-of-
    script residual flushes after the final INSERT group.
    """
    pa = _try_import_pyarrow()
    assert pa is not None  # caller guarantees via `_try_import_pyarrow() is not None`

    # Group state.
    pending_table: str | None = None
    pending_cols: tuple[str, ...] | None = None
    pending_rows: list[tuple[object, ...]] = []
    # Staging table name needs a unique suffix per flush so re-registering
    # doesn't trip "view already exists" on quick-fire flushes.
    staging_counter = 0
    last_end = 0

    def _flush_inserts() -> None:
        nonlocal pending_rows, pending_table, pending_cols, staging_counter
        if not pending_rows or pending_table is None or pending_cols is None:
            pending_rows = []
            pending_table = None
            pending_cols = None
            return
        # Collapse `_TypedSqlLiteral` sentinels to their raw text; DuckDB
        # will cast VARCHAR → TIMESTAMP / DATE on the INSERT SELECT via
        # implicit conversion. Pyarrow stores them as strings.
        col_names = pending_cols
        rows_as_dicts = [
            {
                col_names[i]: (
                    v.text if isinstance(v, _TypedSqlLiteral) else v
                )
                for i, v in enumerate(row)
            }
            for row in pending_rows
        ]
        arrow_table = pa.Table.from_pylist(rows_as_dicts)
        staging_counter += 1
        staging_name = f"_recon_gen_ca11_staging_{staging_counter}"
        cur.register(staging_name, arrow_table)
        try:
            col_list = ", ".join(col_names)
            cur.execute(
                f"INSERT INTO {pending_table} ({col_list}) "
                f"SELECT * FROM {staging_name}"
            )
        finally:
            cur.unregister(staging_name)
        pending_rows = []
        pending_table = None
        pending_cols = None

    def _execute_residual(text: str) -> None:
        """Run any non-INSERT text between matches.

        Comment-only lines (the seed's `-- SHA256: ...` header) are
        stripped so DuckDB's parser doesn't choke on bare `;`s; real
        DDL / SET / pragma statements run via a single multi-statement
        `cur.execute`, preserving their declared order.
        """
        if not text.strip():
            return
        code_lines = [
            ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        ]
        if code_lines:
            cur.execute("\n".join(code_lines))

    # Single pass over the script. At each match boundary: if the new
    # match's group key differs from the pending group, flush pending
    # INSERTs FIRST, then execute the residual text that lay between
    # the previous group's end and this match's start (the residual
    # might contain DDL the next INSERT depends on — flush has to
    # precede the residual for the residual to see all already-INSERTed
    # rows, AND the residual has to precede the next INSERT in case
    # the next INSERT depends on DDL inside the residual).
    for match in _SEED_INSERT_SCAN_RE.finditer(sql):
        residual_before = sql[last_end:match.start()]
        last_end = match.end()
        table = match.group(1)
        cols_str = match.group(2).strip()
        body = match.group(3)
        row = _parse_simple_values(body)
        cols_tuple = tuple(s.strip() for s in cols_str.split(","))
        key = (table, cols_tuple)
        if row is None:
            # Non-conforming VALUES body. Flush pending, run residual,
            # then fall through to per-statement `cur.execute` on the
            # raw INSERT text.
            _flush_inserts()
            _execute_residual(residual_before)
            cur.execute(match.group(0))
            continue
        if (pending_table, pending_cols) != key:
            # Group boundary. Flush pending INSERT batch FIRST so any
            # tables the residual queries see the rows that were already
            # buffered. Then run residual DDL/etc. so the new group's
            # target table exists if the residual just created it.
            _flush_inserts()
            _execute_residual(residual_before)
            pending_table, pending_cols = table, cols_tuple
        else:
            # Continuing the same group — residual must still run before
            # the next INSERT lands (rare, but matters if a SET / pragma
            # sneaks in between same-shape INSERTs).
            if residual_before.strip():
                _flush_inserts()
                _execute_residual(residual_before)
                pending_table, pending_cols = table, cols_tuple
        pending_rows.append(row)
    # Tail: flush final INSERT batch first, then execute trailing residual.
    _flush_inserts()
    _execute_residual(sql[last_end:])


# CA.13 — Oracle direct_path_load fast path. Mirrors the DuckDB path's
# scan-once-then-bulk-load shape; the per-dialect divergence is in the
# flush step (DuckDB takes a `con.register(pa.Table)` + INSERT SELECT;
# Oracle takes `conn.direct_path_load(schema_name, table_name, ...)`
# which bypasses the SQL parser at the server side and writes blocks
# above the High Water Mark). Direct Path is committed at end of the
# call — separate from the caller's transaction. Operationally fine
# for `data apply` (no concurrent writers); flagged in the docstring
# in case a future caller has different semantics needs.


# Cache: built pyarrow schemas per (conn, schema, table, col-tuple). The
# col-tuple is part of the key because a single Oracle table can be the
# target of multiple INSERT shapes in one apply — the seed today emits
# both 11-col and 10-col INSERTs against `diag_daily_balances` (the
# 10-col plant-shape drops `supersedes`), and both 19-col and 17-col
# INSERTs against `diag_transactions`. Caching by (conn, schema, table)
# alone would return a stale schema on the second shape and
# pyarrow.Table.from_pylist would silently drop the missing column,
# producing a data-shape vs column_names mismatch that direct_path_load
# rejects with `DPY-4009: N positional bind values are required but M
# were provided`. The all_tab_columns lookup itself is cheap; the cache
# is purely for the ~few thousand same-shape repeat flushes per apply.
_ORACLE_ARROW_SCHEMA_CACHE: dict[
    tuple[int, str, str, tuple[str, ...]], object
] = {}


def _oracle_table_arrow_schema(
    conn: Any,  # typing-smell: ignore[explicit-any]: oracledb.Connection — DB-API 2.0 with no shared Protocol
    schema_name: str,
    table_name: str,
    column_names: tuple[str, ...],
) -> object:
    """Build a pyarrow Schema for ``table_name``'s columns from Oracle
    metadata.

    Maps Oracle types to pyarrow:

    - VARCHAR2 / CHAR / NCHAR / NVARCHAR2 / CLOB → ``pa.string()``
    - NUMBER with scale=0 (or scale=None on the metadata schema we emit) → ``pa.int64()``
    - NUMBER with scale>0 → ``pa.float64()`` (the seed doesn't emit decimals today)
    - TIMESTAMP / TIMESTAMP(N) → ``pa.timestamp("us")`` (TZ-naive per P.9a)
    - DATE → ``pa.timestamp("us")`` (Oracle DATE carries time-of-day)

    Cached per (conn, schema, table). The seed loops through ~3 distinct
    (table, cols) groups; cache hit rate is near-100%.
    """
    pa = _try_import_pyarrow()
    assert pa is not None

    col_names_upper = tuple(c.upper() for c in column_names)
    key = (id(conn), schema_name.upper(), table_name.upper(), col_names_upper)
    cached = _ORACLE_ARROW_SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT column_name, data_type, data_scale
            FROM all_tab_columns
            WHERE owner = :owner AND table_name = :tbl
            """,
            owner=schema_name.upper(), tbl=table_name.upper(),
        )
        rows = {r[0].upper(): (r[1], r[2]) for r in cur.fetchall()}
    finally:
        cur.close()

    fields: list[object] = []
    for col in column_names:
        col_upper = col.upper()
        if col_upper not in rows:
            raise RuntimeError(
                f"Oracle DPL: column {col_upper!r} not found in "
                f"all_tab_columns for {schema_name}.{table_name}. "
                f"Available: {sorted(rows.keys())}"
            )
        data_type, data_scale = rows[col_upper]
        # Normalize Oracle type strings — e.g. ``TIMESTAMP(6)`` returns
        # base name ``TIMESTAMP`` here because all_tab_columns omits the
        # precision suffix; ``data_type`` is just ``TIMESTAMP``.
        if data_type in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR", "CLOB"):
            fields.append((col_upper, pa.string()))
        elif data_type == "NUMBER":
            if data_scale is None or data_scale == 0:
                fields.append((col_upper, pa.int64()))
            else:
                fields.append((col_upper, pa.float64()))
        elif data_type.startswith("TIMESTAMP") or data_type == "DATE":
            fields.append((col_upper, pa.timestamp("us")))
        else:
            raise RuntimeError(
                f"Oracle DPL: column {col_upper!r} has unsupported type "
                f"{data_type!r} (data_scale={data_scale}). Add a mapping "
                f"to `_oracle_table_arrow_schema`."
            )
    schema = pa.schema(fields)
    _ORACLE_ARROW_SCHEMA_CACHE[key] = schema
    return schema


def _oracle_dpl_normalize_value(v: object, expected_pa_type: object) -> object:
    """Convert a `_parse_simple_values` output to a Python primitive that
    pyarrow can encode as the column's expected type.

    The seed parser emits ints / strings / None / `_TypedSqlLiteral`.
    For TIMESTAMP columns we parse the ISO-8601 text into a `datetime`;
    Oracle DPL rejects raw strings against TIMESTAMP. For string columns,
    pyarrow handles `_TypedSqlLiteral.text` round-trip via `.text`. For
    NUMBER columns the parser already gives us an int.
    """
    pa = _try_import_pyarrow()
    assert pa is not None
    if v is None:
        return None
    if isinstance(v, _TypedSqlLiteral):
        # The TIMESTAMP / DATE typed literal: parse ISO string to datetime.
        if pa.types.is_timestamp(expected_pa_type):
            from datetime import datetime  # noqa: PLC0415
            # Strip trailing fractional-second / TZ tail Oracle's DATE
            # columns can't take; our seed emits TZ-naive `YYYY-MM-DD HH:MM:SS`
            # after `_sql_timestamp_literal`'s normalization (P.9a).
            return datetime.fromisoformat(v.text)
        # Fall through: render text as-is (rare).
        return v.text
    # Plain primitive (str / int / float).
    return v


def _apply_seed_via_oracle_dpl(cur: Any, sql: str) -> None:  # typing-smell: ignore[explicit-any]: oracledb.Cursor — DB-API 2.0 cursor
    """Apply a seed SQL script to Oracle via Connection.direct_path_load.

    CA.13 — scans the script for INSERTs in a single `finditer` pass
    (same approach as `_apply_seed_via_duckdb_pyarrow`), parses each
    VALUES body with `_parse_simple_values`, groups consecutive
    same-shape rows, and flushes each group via
    `conn.direct_path_load(schema_name=..., table_name=...,
    column_names=[...], data=pa_table)`. Non-INSERT residual chunks
    (DDL, SET commands, comments) are executed IN ORDER between
    INSERT batches, mirroring the CA.11 ordering contract.

    Oracle Direct Path Load characteristics worth knowing:

    - **Server-side parser bypass**: writes blocks directly above the
      High Water Mark, skipping the SQL parser entirely. The performance
      win comes from this, not from network round-trip reduction.
    - **CHECK constraints enforced**: NOT NULL + CHECK constraints
      still validate per row (we proved this against
      `account_scope IN ('internal', 'external')` during the CA.13
      probe). Foreign keys + triggers behave per Oracle's documented
      Direct Path semantics — see docs/audits/ if a constraint shape
      surfaces a CA.13 issue.
    - **Auto-commit at call end**: each call commits its own batch.
      `connect_and_apply`'s explicit `conn.commit()` at the end of the
      apply is a no-op against an already-committed transaction (or
      raises in some oracledb versions); `_apply_seed_via_oracle_dpl`
      itself does not call commit/rollback.
    - **Identity columns auto-fill**: pass column_names WITHOUT the
      identity column (e.g. ENTRY) and Oracle assigns it from the
      sequence. Our scan extracts the explicit column list from
      `INSERT INTO foo (col1, col2, ...) VALUES (...)`, which never
      includes ENTRY (the seed emit doesn't either), so this is
      handled automatically.
    """
    pa = _try_import_pyarrow()
    assert pa is not None

    conn = cur.connection
    # Discover the current schema (Oracle's "owner") once per call —
    # all our seed tables live in the connecting user's schema.
    discover = conn.cursor()
    try:
        discover.execute("SELECT user FROM dual")
        schema_name = str(discover.fetchone()[0]).upper()
    finally:
        discover.close()

    pending_table: str | None = None
    pending_cols: tuple[str, ...] | None = None
    pending_rows: list[tuple[object, ...]] = []
    last_end = 0

    def _flush_inserts() -> None:
        nonlocal pending_rows, pending_table, pending_cols
        if not pending_rows or pending_table is None or pending_cols is None:
            pending_rows = []
            pending_table = None
            pending_cols = None
            return
        table_upper = pending_table.upper()
        col_names_upper = tuple(c.upper() for c in pending_cols)
        arrow_schema: Any = _oracle_table_arrow_schema(  # typing-smell: ignore[explicit-any]: pyarrow Schema lacks stubs; iteration over it is dynamic
            conn, schema_name, table_upper, col_names_upper,
        )
        expected_types: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: pyarrow Field.type is dynamic, same posture as the lazy import
            field.name: field.type for field in arrow_schema
        }
        rows_as_dicts = [
            {
                col_names_upper[i]: _oracle_dpl_normalize_value(
                    v, expected_types[col_names_upper[i]],
                )
                for i, v in enumerate(row)
            }
            for row in pending_rows
        ]
        arrow_table = pa.Table.from_pylist(rows_as_dicts, schema=arrow_schema)
        # CB.14 — direct_path_load raises ORA-26085 when the connection
        # has an active transaction. oracledb 3.x opens an implicit
        # transaction on the first statement of any kind (including the
        # SELECT user discovery above), and the non-INSERT residual
        # chunks can also leave a write txn open. Commit before each
        # flush so direct_path_load can start its own.
        conn.commit()
        conn.direct_path_load(
            schema_name=schema_name,
            table_name=table_upper,
            column_names=list(col_names_upper),
            data=arrow_table,
        )
        pending_rows = []
        pending_table = None
        pending_cols = None

    def _execute_residual(text: str) -> None:
        """Execute non-INSERT residual via the existing Oracle script
        splitter (`split_oracle_script`) so comment-aware splitting +
        trailing-`;` stripping behave correctly."""
        if not text.strip():
            return
        for stmt in split_oracle_script(text):
            stripped = stmt.strip()
            if not stripped or all(
                ln.strip().startswith("--") or not ln.strip()
                for ln in stripped.splitlines()
            ):
                continue
            _execute_oracle_stmt_with_lock_retry(cur, stmt)

    for match in _SEED_INSERT_SCAN_RE.finditer(sql):
        residual_before = sql[last_end:match.start()]
        last_end = match.end()
        table = match.group(1)
        cols_str = match.group(2).strip()
        body = match.group(3)
        row = _parse_simple_values(body)
        cols_tuple = tuple(s.strip() for s in cols_str.split(","))
        key = (table, cols_tuple)
        if row is None:
            _flush_inserts()
            _execute_residual(residual_before)
            # CB.14 — `_SEED_INSERT_SCAN_RE` captures through the trailing
            # `;` (the regex anchor); oracledb's thin-mode `cur.execute()`
            # rejects trailing semicolons with ORA-00933. Latent under
            # 23ai (apparently lenient) — exposed by 19c. Strip before
            # executing the verbatim fallback.
            stmt = match.group(0).rstrip().rstrip(";")
            _execute_oracle_stmt_with_lock_retry(cur, stmt)
            continue
        if (pending_table, pending_cols) != key:
            _flush_inserts()
            _execute_residual(residual_before)
            pending_table, pending_cols = table, cols_tuple
        else:
            if residual_before.strip():
                _flush_inserts()
                _execute_residual(residual_before)
                pending_table, pending_cols = table, cols_tuple
        pending_rows.append(row)
    _flush_inserts()
    _execute_residual(sql[last_end:])


def _render_sql_literal(v: object) -> str:
    """Render a Python primitive (from `_parse_simple_values`) back as
    DuckDB SQL literal text. The parser bails on any string containing
    `''` escapes so we don't need quote-escape logic here — only the
    primitive shapes the parser emits round-trip.

    NULL → ``NULL``; bool → ``TRUE`` / ``FALSE``; int/float → ``str(v)``;
    str → single-quoted (no escape needed per the parser's contract);
    `_TypedSqlLiteral(kind, text)` → ``<kind> '<text>'`` (e.g.
    ``TIMESTAMP '2026-03-03 00:00:01'``). Unrecognized shapes raise —
    would be a parser/flush invariant violation.
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, _TypedSqlLiteral):
        return f"{v.kind} '{v.text}'"
    if isinstance(v, str):
        return f"'{v}'"
    raise TypeError(
        f"_render_sql_literal: unrecognized primitive {type(v).__name__}: "
        f"{v!r}. `_parse_simple_values` should have bailed on this shape."
    )


def _split_sqlite_statements(sql: str) -> list[str]:
    """Split a multi-statement SQLite script on ``;`` boundaries while
    respecting single-quoted strings and ``--`` line comments.

    Returns a list of non-empty statement bodies (no trailing ``;``;
    ``cur.execute`` accepts both forms but stripping is consistent).
    Comment-only chunks (between statements) are dropped.
    """
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    in_string = False
    in_line_comment = False
    while i < n:
        c = sql[i]
        if in_line_comment:
            buf.append(c)
            if c == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_string:
            buf.append(c)
            if c == "'":
                in_string = False
            i += 1
            continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            in_line_comment = True
            buf.append(c)
            i += 1
            continue
        if c == "'":
            in_string = True
            buf.append(c)
            i += 1
            continue
        if c == ";":
            stmt = "".join(buf).strip()
            # Drop comment-only chunks: a chunk where every non-blank
            # line starts with `--`.
            code_lines = [
                ln for ln in stmt.splitlines()
                if ln.strip() and not ln.strip().startswith("--")
            ]
            if code_lines:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        code_lines = [
            ln for ln in tail.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        ]
        if code_lines:
            out.append(tail)
    return out


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

    BC.12 (2026-05-24): the splitter now tracks single-quote state across
    lines so a ``;`` embedded inside a multi-line string literal
    (e.g. a `<prefix>_config_kv` description field that itself contains
    semicolons + newlines) doesn't false-split the statement. The
    pre-BC.12 line-by-line scan treated every ``;`` at end-of-line as a
    statement terminator, which is correct only when every literal is
    single-line — false for the BC.12 kv populate path on L2s with
    multi-line / semicolon-bearing descriptions.

    Quote-state semantics: a single quote toggles in-string state;
    inside a string, ``--`` is NOT a comment start, ``;`` is NOT a
    terminator, ``BEGIN``/``END`` aren't PL/SQL keywords. SQL's
    doubled-quote escape (``''``) is naturally handled because two
    toggles cancel.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_plsql = False
    in_string = False
    for raw_line in sql.splitlines():
        line = raw_line.rstrip()
        # Scan the line char-by-char to track quote state and find the
        # boundary between code and comment.
        # ``code`` accumulates only the chars outside any string; the
        # ``--`` comment check applies only when not inside a string.
        code_chars: list[str] = []
        i = 0
        n = len(line)
        while i < n:
            c = line[i]
            if c == "'":
                in_string = not in_string
                code_chars.append(c)
                i += 1
                continue
            if not in_string and c == "-" and i + 1 < n and line[i + 1] == "-":
                # Rest of line is a comment.
                break
            code_chars.append(c)
            i += 1
        code = "".join(code_chars).rstrip()
        stripped_code = code.strip()
        if not in_string and not in_plsql and stripped_code.upper().startswith(
            ("BEGIN ", "DECLARE"),
        ):
            in_plsql = True
        buffer.append(line)
        if in_string:
            # Multi-line string literal in flight — don't even consider
            # this line as a statement-terminator candidate.
            continue
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


class SyncCursor(Protocol):
    """DB-API 2.0 sync cursor surface — the union of methods actually
    called across the spine + dialect-agnostic helpers.

    All sync dbapi drivers we touch (duckdb / sqlite3 / psycopg /
    oracledb) expose this surface. The Protocol matches structurally
    so callers don't need to import a Union of driver types — pass
    any DBAPI cursor and pyright is satisfied.

    CB.16 expansion (2026-06-03): grew from `execute` + `close` to
    cover `fetchall` / `fetchone` / `description` / `rowcount` so the
    spine + html/_sql_executor + html/_tree_fetcher modules can drop
    their cursor-level `Any` annotations.
    """

    def execute(
        self, sql: str, params: Any = ..., /,  # typing-smell: ignore[explicit-any]: bind params are per-driver-coerced sequences; no shared shape covers tuple / list / dict across psycopg / oracledb / sqlite3 / duckdb
    ) -> object: ...
    def fetchall(self) -> list[Any]: ...  # typing-smell: ignore[explicit-any]: rows are heterogeneous by query — per-call shape lives in the SQL contract, not the DBAPI surface (same posture as AsyncCursor below)
    def fetchone(self) -> Any | None: ...  # typing-smell: ignore[explicit-any]: same — heterogeneous per query
    def fetchmany(self, size: int = ..., /) -> list[Any]: ...  # typing-smell: ignore[explicit-any]: same — heterogeneous per query; default size is driver-specific (sqlite3 / duckdb default to `arraysize`)
    @property
    def description(self) -> Sequence[Sequence[Any]] | None: ...  # typing-smell: ignore[explicit-any]: DB-API 2.0 description tuples carry mixed types per column (mirrors AsyncCursor)
    @property
    def rowcount(self) -> int: ...
    def close(self) -> None: ...
    # Context-manager surface (psycopg + oracledb + duckdb cursors all implement;
    # sqlite3 doesn't, but SQLite is gone post-CB.8). `with conn.cursor() as cur`
    # is the canonical close-on-exit shape used by cli/_helpers.py + others.
    def __enter__(self) -> "SyncCursor": ...
    def __exit__(self, *_args: object) -> object: ...


class SyncConnection(Protocol):
    """DB-API 2.0 sync connection surface — pair of ``SyncCursor``.

    Replaces the ``connect_demo_db(cfg) -> Any`` escape hatch (the
    upstream source of every downstream lie that made the spine
    type-check as ``duckdb.DuckDBPyConnection`` regardless of the
    actual driver). Dispatch from connection module name → per-dialect
    placeholder style happens inside ``_emit_helpers._placeholder_style``;
    the Protocol just captures the dbapi surface shared across drivers.

    CB.16 expansion (2026-06-03): added `commit` + `close` so the
    Protocol covers the connection-level call surface used by the
    spine + cli/docs.py + _isolation.py teardown paths.
    """

    def cursor(self) -> SyncCursor: ...
    def commit(self) -> object: ...  # PEP 249 says `None`; duckdb returns `self` for chaining. Widen to `object` so both match the Protocol — callers never use the return value
    def rollback(self) -> object: ...  # Same shape as commit — error-path partner used by cli/_helpers.py + similar
    def close(self) -> object: ...  # Same: PEP 249 says `None`; some drivers chain. Callers never use the return value


def fetch_one_required(cur: SyncCursor) -> Sequence[Any]:  # typing-smell: ignore[explicit-any]: returns a heterogeneous row — caller knows the column shape from the SQL it just ran
    """``cur.fetchone()`` with a non-None assertion baked in.

    PEP 249's ``fetchone() -> Row | None`` is honest about the empty-result
    case, but the bulk of internal callers run aggregate / single-row
    queries (``SELECT COUNT(*)``, ``SELECT MAX(x)``, ``SELECT * FROM kv
    WHERE key=?``) where None violates the SQL contract. This helper
    raises loudly instead of letting the downstream tuple-unpack fail
    with a confusing ``cannot unpack non-iterable NoneType`` traceback.

    Callers that genuinely need the ``None`` branch (no-row-found
    queries) use ``cur.fetchone()`` directly.
    """
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(
            "cursor.fetchone() returned None where one row was expected "
            "by the SQL contract"
        )
    return row


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



class _AsyncDuckdbCursor:
    """CA.4 — async-shaped cursor result adapter for sync DuckDB.

    DuckDB's Python driver is sync-only; ``_AsyncDuckdbConnection.execute``
    runs the query + ``fetchall()`` synchronously under
    ``asyncio.to_thread``, then stashes the rows + description here so
    the ``AsyncCursor`` Protocol's later ``await fetchall()`` is a
    no-op return.
    """

    def __init__(
        self,
        rows: list[Any],  # typing-smell: ignore[explicit-any]: DB-API rows are heterogeneous per the SQL contract
        description: Sequence[Sequence[Any]] | None,  # typing-smell: ignore[explicit-any]: DB-API cursor.description tuples carry mixed types per column
    ) -> None:
        self._rows = rows
        self._description = description

    @property
    def description(self) -> Sequence[Sequence[Any]] | None:  # typing-smell: ignore[explicit-any]: DB-API cursor.description shape
        return self._description

    async def fetchall(self) -> list[Any]:  # typing-smell: ignore[explicit-any]: rows are heterogeneous; per-call shape lives in the SQL contract
        return self._rows


class _AsyncDuckdbConnection:
    """CA.4 — async adapter over a sync ``duckdb.DuckDBPyConnection``.

    DuckDB has no native async driver. The adapter dispatches each
    ``execute(query, params)`` call onto a worker thread via
    ``asyncio.to_thread``, eagerly materializes rows + description,
    and returns an ``_AsyncDuckdbCursor`` carrying both. Eager
    materialization (sync ``fetchall()`` inside the thread) keeps the
    later ``await cursor.fetchall()`` a no-op — matches the App2
    executor's "one-shot execute + fetchall" usage.

    Connection-thread-safety caveat (see ``_AsyncDuckdbPool``): DuckDB
    connections are NOT thread-safe; the pool gives each acquire its
    OWN connection, and that connection is bound to whichever thread
    eventually runs the ``asyncio.to_thread`` calls. Don't share an
    ``_AsyncDuckdbConnection`` across acquire scopes.
    """

    def __init__(self, conn: Any) -> None:  # typing-smell: ignore[explicit-any]: duckdb.DuckDBPyConnection has no PEP 561 stubs at strict
        self._conn = conn

    async def execute(
        self,
        query: str,
        params: Any = None,  # typing-smell: ignore[explicit-any]: bind params dict — driver coerces per-driver; no shared Protocol covers the dict + list shapes DuckDB accepts
        /,
    ) -> AsyncCursor:
        conn = self._conn

        def _run() -> tuple[list[Any], Sequence[Sequence[Any]] | None]:  # typing-smell: ignore[explicit-any]: see class docstring
            if params is None:
                cur = conn.execute(query)
            else:
                cur = conn.execute(query, params)
            rows = cur.fetchall()
            desc = cur.description
            return rows, desc

        rows, desc = await asyncio.to_thread(_run)
        return _AsyncDuckdbCursor(rows, desc)


class PoolReleasedDuringRefresh(RuntimeError):
    """CO.x — raised when ``_AsyncDuckdbPool.acquire()`` is called
    while the pool's root is released (e.g. the deploy pipeline is
    bracketing ``step_1_etl_hook``'s subprocess).

    Typed subclass of ``RuntimeError`` so the dashboards' 500 handler
    can key off the class for a themed "data refresh in progress"
    page (followup) and tests can assert on the type rather than
    string-matching the message.
    """


class _AsyncDuckdbPool:
    """CA.4 — one-root-connection + cursor-per-acquire pool for the
    App2 async DuckDB path.

    The constraint that ACTUALLY holds across duckdb 1.x (verified
    by the CO.x probe in ``docs/audits/etl_duckdb_studio_concurrency.md``):

    1. **Only one process may open a ``.duckdb`` file for write at a
       time.** A second process's ``duckdb.connect(path)`` against the
       same file raises ``IOException: Could not set lock on file …
       Conflicting lock is held in <other_pid>``. Even a parent
       ``read_only=True`` handle blocks subprocess WRITE (subprocess
       READ succeeds). This is the constraint that makes
       ``cfg.etl_hook`` (a subprocess) collide with the Studio
       process's long-lived pool — and the reason this class exposes
       :meth:`released_for_subprocess` so the deploy pipeline can
       surrender the lock around the subprocess and reacquire it
       afterwards in one atomic, drain-then-reopen window.

    2. **``connection.cursor()`` returns a fresh thread-safe
       ``DuckDBPyConnection``** — a separate object that shares the
       underlying database but can be used concurrently from another
       thread without colliding with the root connection's locks.
       (The reference API doc says ``cursor()`` "creates a duplicate
       of the current connection"; the parallelism overview claims
       it's just another handle, but a probe of 8 parallel threads
       each running queries via ``root.cursor()`` succeeds cleanly.)

    (An older docstring on this class claimed same-process double-
    ``duckdb.connect()`` raises ``BinderException: Unique file handle
    conflict``. The CO.x probe shows that's not the case on duckdb
    1.x: two ``duckdb.connect(path)`` calls in one process work fine.
    The cross-process write-lock above IS the only hard constraint;
    the one-root-per-pool shape stays defensible for resource
    discipline + the deploy-pipeline release/reacquire pattern, just
    not load-bearing on a hard DuckDB constraint.)

    Pool shape: open ONE root ``duckdb.connect(path)`` at construction;
    each ``acquire()`` calls ``root.cursor()`` to fork a fresh
    sub-connection. Cursor close is cheap (no file I/O). Pool close
    tears down the root. :meth:`released_for_subprocess` is the
    async context manager the deploy pipeline brackets
    ``step_1_etl_hook`` with — it serializes via ``_lifecycle_lock``
    (so concurrent /deploy + /training/reset don't re-trigger the
    very bug this fix addresses), drains in-flight cursors before
    closing (so dashboards mid-fetch get a clean
    :class:`PoolReleasedDuringRefresh` instead of a rug-pull
    ``IOException``), and ALWAYS reopens in its ``finally``.
    Preserves pool identity: closure-captured references (e.g. in
    ``make_studio_routes`` handlers) stay valid across the window.
    ``max_size`` semaphore bounds in-flight cursors so a spike of
    parallel requests doesn't pile up handles.
    """

    def __init__(self, path: str, *, max_size: int = 10) -> None:
        import duckdb  # noqa: PLC0415
        from recon_gen.common.env_keys import RECON_GEN_DB_READ_ONLY  # noqa: PLC0415

        self._path = path
        self._sem = asyncio.Semaphore(max_size)
        # CO.x — serializes the release/reacquire bracket across
        # concurrent Studio handlers. Without this, two overlapping
        # /deploy invocations would each spawn their own etl_hook
        # subprocess; whichever one's reopen() lands first would
        # reacquire the lock and crash the OTHER's still-running
        # subprocess with the same IO error this fix exists to
        # prevent.
        self._lifecycle_lock = asyncio.Lock()
        # CO.x — drain coordination. `_in_flight` counts active
        # cursors; `_drained` flips set whenever the count is zero so
        # `close()` can wait for outstanding queries to finish before
        # tearing down the root. Without this, a dashboard fetch in
        # the middle of `cur.fetchall()` would see DuckDB raise
        # `InvalidInputException: No open result set` when the root
        # closes under it.
        self._in_flight = 0
        self._drained = asyncio.Event()
        self._drained.set()
        # CA.8 — honor RECON_GEN_DB_READ_ONLY=1 for the same multi-
        # process safety reason `connect_demo_db` does. The runner sets
        # this for the App2 pytest tier's DuckDB cells so xdist workers
        # share read access against the seeded .duckdb file.
        self._read_only = bool(RECON_GEN_DB_READ_ONLY.get_or_none())
        # Open the root eagerly so a bad path / corrupt file surfaces
        # at construction (server startup) rather than first request.
        self._root: Any = duckdb.connect(path, read_only=self._read_only)  # typing-smell: ignore[explicit-any]: duckdb.DuckDBPyConnection has no PEP 561 stubs at strict

    def acquire(self) -> AbstractAsyncContextManager[AsyncConnection]:
        return self._acquire()

    @asynccontextmanager
    async def _acquire(self) -> AsyncGenerator[AsyncConnection, None]:
        async with self._sem:
            if self._root is None:
                # CO.x — the pool's root has been released (likely by
                # the deploy pipeline bracketing step_1_etl_hook).
                # Typed exception lets the dashboards' 500 handler
                # render a clean themed page (followup) and tests
                # assert on the class instead of message-matching.
                raise PoolReleasedDuringRefresh(
                    "DuckDB pool released — a data refresh is in "
                    "progress; the dashboard will reload when it "
                    "completes.",
                )
            # CO.x — bump the in-flight counter BEFORE the cursor()
            # await so a concurrent close() sees the count and blocks
            # on its drain wait. Without this, the close-during-
            # cursor() window leaks: close sees `_drained` as set,
            # tears down the root, and the cursor we get back
            # references a now-closed handle. The check + increment
            # pair runs synchronously between two awaits (semaphore
            # entry + the to_thread below), so asyncio gives us
            # atomicity for free.
            self._in_flight += 1
            self._drained.clear()
            try:
                cursor_conn = await asyncio.to_thread(self._root.cursor)
            except BaseException:
                # cursor() raised — release the slot we just claimed
                # so close()'s drain doesn't wait forever for a
                # cursor that never materialized.
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._drained.set()
                raise
            try:
                yield _AsyncDuckdbConnection(cursor_conn)
            finally:
                await asyncio.to_thread(cursor_conn.close)
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._drained.set()

    async def close(self) -> None:
        """Tear down the root connection.

        CO.x — drains in-flight cursors first (waits up to forever
        for outstanding queries to finish) so a concurrent
        ``acquire()`` doesn't see the root yanked mid-fetch. The
        ``_lifecycle_lock`` blocks reopen calls from racing the
        close. ``self._root = None`` lives inside try/finally so a
        ``duckdb.close()`` exception still clears the field — the
        next ``reopen()`` will then rebuild a fresh handle instead
        of incorrectly assuming an open one.
        """
        async with self._lifecycle_lock:
            if self._root is None:
                return
            # Wait for any in-flight cursors to release before
            # closing the root. Without this drain, a dashboard
            # query mid-`cur.fetchall()` raises
            # `InvalidInputException: No open result set` when the
            # underlying root closes.
            await self._drained.wait()
            try:
                await asyncio.to_thread(self._root.close)
            finally:
                self._root = None

    async def reopen(self) -> None:
        """CO.x — reopen the root connection after a ``close()``.

        Idempotent. Held under the same ``_lifecycle_lock`` as
        :meth:`close` so concurrent close/reopen calls serialize.
        Preserves pool identity: callers that closure-captured the
        ``_AsyncDuckdbPool`` instance (e.g. ``make_studio_routes``'s
        nested route handlers) see the new root through their
        existing reference; no rewiring required.
        """
        async with self._lifecycle_lock:
            if self._root is not None:
                return
            import duckdb  # noqa: PLC0415
            self._root = await asyncio.to_thread(
                duckdb.connect, self._path, read_only=self._read_only,
            )

    @asynccontextmanager
    async def released_for_subprocess(self) -> AsyncGenerator[None, None]:
        """CO.x — atomic release/reacquire bracket for a subprocess
        that needs the DuckDB process-level write lock.

        Used by ``run_deploy_pipeline`` to wrap ``step_1_etl_hook``'s
        subprocess invocation. Combines :meth:`close` (with its
        drain) and :meth:`reopen` into one context-managed unit
        whose lifecycle is held under a SINGLE lock acquisition —
        so concurrent ``/deploy`` + ``/training/reset`` handlers
        SERIALIZE through this bracket rather than racing their
        independent close/reopen calls and reintroducing the very
        bug this fix addresses.

        Always reopens in ``finally`` — even on subprocess failure,
        CancelledError, or exception inside the yield body.
        Exception priority follows the standard ``try: yield finally:
        reopen()`` shape: if reopen ALSO raises after the yield body
        raised, the reopen exception wins (Python's standard
        try/finally semantics) and the original is set on its
        ``__context__``. Operators reading the dashboards-side log
        would see the reopen failure first; the deploy-pipeline's
        own log captures the original subprocess error separately
        via the live-tail events.
        """
        async with self._lifecycle_lock:
            # Re-implement close() inline so the lock stays held
            # across the close → yield → reopen window.
            already_released = self._root is None
            if not already_released:
                await self._drained.wait()
                try:
                    await asyncio.to_thread(self._root.close)
                finally:
                    self._root = None
            try:
                yield
            finally:
                if self._root is None:
                    import duckdb  # noqa: PLC0415
                    self._root = await asyncio.to_thread(
                        duckdb.connect,
                        self._path,
                        read_only=self._read_only,
                    )


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
            "or via RECON_GEN_DEMO_DATABASE_URL."
        )
    if cfg.dialect is Dialect.POSTGRES:
        try:
            from psycopg_pool import AsyncConnectionPool as _PgAsyncPool
        except ImportError as e:
            raise ImportError(
                "psycopg_pool is required for the async Postgres pool. "
                "Install it with: pip install 'recon-gen[prod]' "
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
                "Install it with: pip install 'recon-gen[prod]'"
            ) from e
        # oracledb's async pool factory is sync (it returns the pool
        # object; connection acquisition is the async part).
        pool = oracledb.create_pool_async(
            dsn=oracle_dsn(cfg.demo_database_url), min=1, max=max_size,
        )
        return _AsyncOraclePool(pool)
    if cfg.dialect is Dialect.DUCKDB:
        # CA.4 — core dep, no extras-install gate. Probe to surface a
        # missing wheel at pool-construction time so Studio's startup
        # fails loudly rather than at first request.
        try:
            import duckdb as _duckdb_probe  # noqa: PLC0415, F401
        except ImportError as e:
            raise ImportError(
                "duckdb is required for the DuckDB pool. It's a core "
                "dependency — run `uv sync` (or `pip install duckdb`)."
            ) from e
        del _duckdb_probe
        return _AsyncDuckdbPool(
            duckdb_path(cfg.demo_database_url), max_size=max_size,
        )
    raise ValueError(
        f"Unknown dialect {cfg.dialect!r}. "
        "Set 'dialect: postgres', 'dialect: oracle', or 'dialect: duckdb' "
        "in your config."
    )
