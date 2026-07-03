"""Per-dialect snapshot/restore for trainer v-overlay state.

Captures the post-Session-Start state once + restores between plants
so per-plant tests are hermetic. Replaces the cumulative-walk
amortization workaround that ran the trainer dogfood test through 15
sequential Session Starts (~30 min total on Oracle).

Per-dialect picks (operator-locked, BV.3.3 snapshot foundation):

- **DuckDB**: ``shutil.copy2`` of the duckdb file bracketed by
  ``AsyncConnectionPool.released_for_subprocess()`` — ~50ms restore.
- **PG**: schema-namespace snapshot — CTAS a "golden" schema once,
  TRUNCATE+INSERT+matview-refresh on restore — ~150ms.
- **Oracle**: golden-mirror CTAS + TRUNCATE+INSERT /*+ APPEND */ +
  DBMS_MVIEW.REFRESH — ~2500ms (accepted by operator).

Snapshotter is wired into the test harness via an HTTP route on the
Studio server (``POST /training/snapshot/{take,restore,drop}``) + matching
``App2Driver`` verbs (preserves the "everything through driver"
invariant — see X.2.q).

Lives in ``src/recon_gen/common/`` (consolidated from the pre-BV.3.3.c
fork at ``tests/e2e/_snapshotter.py``) because the Studio HTTP route on
the runtime server imports it. The test rig still owns the FIXTURE
wiring (per-cell lifecycle, harness plumbing); the snapshot MECHANISM
is shared runtime infrastructure and can be reused for an operator-
facing "restore baseline" button if useful later.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from recon_gen.common.config import Config
from recon_gen.common.db import AsyncConnectionPool, duckdb_path
from recon_gen.common.sql import Dialect

if TYPE_CHECKING:
    # Forward-only — avoids a runtime circular import via
    # ``recon_gen.common.l2.primitives`` (which transitively pulls a
    # large cone). The factory takes the L2Instance as a structural
    # carrier for per-instance table prefixes; we only need its type
    # signature at type-check time.
    from recon_gen.common.l2.primitives import L2Instance


__all__ = [
    "DuckDBFileSnapshotter",
    "NotImplementedSnapshotter",
    "OracleGoldenMirrorSnapshotter",
    "PostgresSchemaSnapshotter",
    "Snapshotter",
    "make_snapshotter",
]


#: The three base data tables the v-overlay carries. Matches the
#: ``clone_base_to_v_sql`` set in ``common/l2/v_overlay.py`` — kept as
#: a module-level tuple so the snapshotter walks exactly the same
#: surface the session_start clone path does. Matviews are NOT in this
#: list — they get rebuilt from base tables via
#: ``refresh_v_overlay_matviews_sql`` after restore.
_V_OVERLAY_BASE_TABLES: tuple[str, ...] = (
    "transactions",
    "daily_balances",
    "config_kv",
)


#: V-overlay matview suffixes (without the ``{prefix}_v_`` envelope) —
#: refreshed in dependency order after restore so each downstream
#: matview reads fresh upstream data. Mirrors the matview names list
#: in ``common.l2.schema.refresh_matviews_sql`` (post-BV.6, 2026-06-10
#: matview set). Add / rename / reorder here when the schema changes;
#: ``test_dq0_overlay_suffixes_match_refresh_order`` pins this against
#: ``refresh_matviews_sql``'s canonical order so a re-divergence fails
#: loud (DQ.0 §1 — the dogfood-walk guarantee this comment used to claim
#: is Oracle-only and never runs in the DuckDB chain, which is exactly how
#: the DS.3.2 effective_balances mis-order stayed silent). Used by the
#: Oracle impl, which drives one ``DBMS_MVIEW.REFRESH`` block per matview.
_V_OVERLAY_MATVIEW_SUFFIXES: tuple[str, ...] = (
    # Leaves: read from base tables only.
    "current_transactions",
    "current_daily_balances",
    # CL.5 carry-forward effective balance — the spine the computed_*
    # matviews read FROM (DS.3.2 re-keyed them onto it), so it MUST
    # refresh BEFORE them. A complete-refresh (DBMS_MVIEW.REFRESH 'C')
    # doesn't cascade, so computed_* ordered ahead of this would recompute
    # against stale carry-forward balances — the DQ.0 §1 overlay bug.
    "effective_balances",
    # Computed balances: read FROM effective_balances (+ current_*).
    "computed_subledger_balance",
    "computed_ledger_balance",
    # DK.1 data_anchor singleton — leaf (reads only from current_*).
    # Regenerated from base tables on snapshot restore so the anchor
    # always reflects the post-restore row set; this is the property
    # that justified matview over config_kv persistence (DK.0 audit).
    "data_anchor",
    # L1 invariants.
    "drift",
    "ledger_drift",
    # DL.3.5 — drift_summary UNION ALL'd derivation of drift + ledger_drift;
    # refresh AFTER both parents are restored so its rows reflect the
    # snapshot's drift / ledger_drift content.
    "drift_summary",
    "overdraft",
    "expected_eod_balance_breach",
    "balance_cadence_gap",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "chain_parent_disagreement",
    "xor_group_violation",
    "transfer_parents",
    "fan_in_disagreement",
    "multi_xor_violation",
    # Dashboard-shape matviews — depend on L1 invariants.
    "daily_statement_summary",
    "l1_exceptions",
    # Investigation matviews.
    "inv_pair_rolling_anomalies",
    "inv_money_trail_edges",
)


def _validate_snapshot_name(name: str) -> None:
    """Reject snapshot names that aren't safe to embed in identifiers.

    The Oracle impl composes the snapshot name into table identifiers
    (``{prefix}_v_<table>_gold_<name>``). Oracle folds unquoted
    identifiers to upper and accepts ``[A-Z0-9_$#]`` — we tighten to
    ``[A-Za-z0-9_]`` at the API surface so error logs stay readable
    and an operator can copy-paste a snapshot name into a manual
    cleanup query without shell-quoting ``$`` / ``#``.

    Length cap is 32 chars: Oracle 19c's 128-byte identifier cap minus
    the worst-case ``<base_prefix>_v_<longest_matview_suffix>_gold_``
    envelope (~55 chars on a 16-char base_prefix + 30-char matview
    suffix) leaves comfortable headroom.
    """
    if not name:
        raise ValueError("Snapshot name must be non-empty.")
    if len(name) > 32:
        raise ValueError(
            f"Snapshot name {name!r} too long ({len(name)} chars > 32); "
            "Oracle 19c identifier cap is 128 bytes and the gold-table "
            "envelope eats most of it.",
        )
    if not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(
            f"Snapshot name {name!r} must match [A-Za-z0-9_]+ "
            "(safe to interpolate into Oracle identifiers).",
        )


class Snapshotter(Protocol):
    """Captures + restores v-overlay state set up by ``session_start()``.

    Contract: after ``take(name)``, one or more ``restore(name)`` calls
    each produce a state byte-indistinguishable from immediately-post-
    take (for the L1/L2FT invariant matview SELECTs the trainer dogfood
    test reads back).

    Restore target latency: <1s on MB-class v-overlay (Oracle: <5s).

    Lifecycle: one ``Snapshotter`` instance per ``(worker, dialect)``
    cell — held by the test harness across the whole per-plant walk.
    ``aclose()`` releases dialect-specific resources (golden-mirror
    schemas / temp files / metadata cursors); a missed ``aclose()``
    leaks at most one schema-or-file per worker per session, never
    enough to OOM but worth not leaking.

    All verbs are async because the Oracle + PG impls run DDL+DML on
    the shared ``AsyncConnectionPool``; DuckDB's impl is sync under
    the hood but stays async-shaped for protocol uniformity.
    """

    async def take(self, name: str) -> None: ...
    async def restore(self, name: str) -> None: ...
    async def drop(self, name: str) -> None: ...
    async def aclose(self) -> None: ...


class NotImplementedSnapshotter:
    """Stub raising ``NotImplementedError`` for each verb.

    Kept on the surface because the factory's dialect-gate raises
    BEFORE reaching this class; tests still construct it directly to
    assert the Protocol shape. If a future dialect lands without an
    impl, the factory can fall back here rather than crash.
    """

    _PENDING_MSG = (
        "Snapshotter impl pending — BV.3.3 snapshot foundation only ships "
        "the Protocol + factory shape; per-dialect impls land in phase 2 "
        "(DuckDB → PG → Oracle)."
    )

    async def take(self, name: str) -> None:
        raise NotImplementedError(self._PENDING_MSG)

    async def restore(self, name: str) -> None:
        raise NotImplementedError(self._PENDING_MSG)

    async def drop(self, name: str) -> None:
        raise NotImplementedError(self._PENDING_MSG)

    async def aclose(self) -> None:
        # ``aclose`` is the one verb safe to no-op — the stub holds no
        # resources, so callers in test ``finally`` blocks don't need a
        # try/except. Per-dialect impls override this with real cleanup.
        return None


class DuckDBFileSnapshotter:
    """BV.3.3 — DuckDB-dialect Snapshotter via raw file copy.

    DuckDB stores the entire database in a single ``.duckdb`` file plus
    a small WAL sidecar; after ``CHECKPOINT`` the file alone is the
    canonical byte-level state. The snapshot operation is therefore
    just ``shutil.copy2`` of the file — no DDL, no per-table walk, no
    matview refresh. On the operator-locked perf budget (~50ms restore)
    this beats the PG schema-namespace pattern by an order of magnitude.

    The catch: DuckDB's process-level write lock means we MUST release
    every open handle (pool root + cursors) before the copy, or the OS
    file lock will block / produce a torn snapshot. The release is
    bracketed by
    :meth:`_AsyncDuckdbPool.released_for_subprocess` — the same context
    manager the deploy pipeline uses to surrender the lock to its ETL
    subprocess. Reusing it keeps the snapshot/restore lifecycle inside
    one well-tested code path rather than a parallel "drain the pool"
    cone.

    Snap-directory layout: ``snap_dir / "<name>.duckdb"``. The directory
    is owned by this snapshotter — ``aclose()`` removes it when empty.
    Snap files are ``chmod 0o444`` (read-only) after take so an
    accidental concurrent write surfaces as ``PermissionError`` instead
    of silently corrupting the canonical state.

    Concurrency: ``released_for_subprocess`` holds the pool's
    ``_lifecycle_lock`` for the duration of the bracket, so concurrent
    ``take()`` / ``restore()`` serialize naturally — no extra lock
    needed in this class.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        pool: AsyncConnectionPool,
        snap_dir: Path | None = None,
    ) -> None:
        self._db_path = db_path
        # ``pool`` is the live ``_AsyncDuckdbPool``; we keep the
        # ``AsyncConnectionPool`` annotation for cross-dialect uniformity
        # but rely on ``released_for_subprocess`` at runtime. A non-
        # DuckDB pool wouldn't have that method — the factory wires this
        # up only for ``Dialect.DUCKDB`` so the duck-type check is
        # implicit (rather than an isinstance gate that would force us
        # to import the private ``_AsyncDuckdbPool`` symbol).
        self._pool = pool
        # Snap directory: caller-supplied (tests pass a tmp_path) or a
        # process-scoped ``mkdtemp`` under the system tmp root. We track
        # ``_owns_snap_dir`` so ``aclose()`` only deletes when WE
        # created the directory — caller-owned tmp_paths stay intact for
        # post-test inspection.
        if snap_dir is None:
            self._snap_dir = Path(tempfile.mkdtemp(prefix="snap-duckdb-"))
            self._owns_snap_dir = True
        else:
            snap_dir.mkdir(parents=True, exist_ok=True)
            self._snap_dir = snap_dir
            self._owns_snap_dir = False
        self._closed = False

    @property
    def snap_dir(self) -> Path:
        """Read-only accessor for tests that need to assert on the path."""
        return self._snap_dir

    def _snap_path(self, name: str) -> Path:
        # Validate at every entry point — defends against snapshot
        # names smuggled in via untrusted sources (the Studio test
        # harness HTTP route, future operator-facing UI) that would
        # otherwise let an attacker write outside ``self._snap_dir``
        # via path traversal (``../etc``). Same validator that gates
        # the future Oracle impl's identifier composition — uniform
        # cross-dialect surface.
        _validate_snapshot_name(name)
        return self._snap_dir / f"{name}.duckdb"

    async def take(self, name: str) -> None:
        """Capture the current DB file as snapshot ``name``.

        Bracket via ``released_for_subprocess`` so every cursor + the
        pool root drain + close BEFORE the copy. Inside the bracket:
        open a short-lived sync ``duckdb.connect`` and run
        ``CHECKPOINT`` to flush the WAL into the main file (so the
        copy captures the canonical state, not "main-file + sidecar
        WAL"); close it; then ``shutil.copy2`` the file. Finally
        ``chmod 0o444`` so an accidental writer surfaces loudly.
        """
        snap = self._snap_path(name)
        # ``cast(Any, ...)`` to reach ``released_for_subprocess`` — only the
        # private ``_AsyncDuckdbPool`` exposes it; the cross-dialect
        # ``AsyncConnectionPool`` Protocol doesn't (and shouldn't, since
        # PG / Oracle pools don't have an analogous "release the file
        # lock" semantics). Factory wires DuckDBFileSnapshotter only for
        # ``Dialect.DUCKDB``, so the duck-type access is safe at runtime.
        async with cast(Any, self._pool).released_for_subprocess():
            import duckdb  # noqa: PLC0415
            # CHECKPOINT flushes the WAL so the on-disk file is the
            # canonical state. Without this, a recent INSERT could be
            # in the WAL sidecar at copy time → the snapshot misses
            # it AND the WAL replay during restore replays the wrong
            # base file. Short-lived connection (open / checkpoint /
            # close) — no pool reuse so the file lock releases
            # immediately.
            conn = duckdb.connect(str(self._db_path))
            try:
                conn.execute("CHECKPOINT")
            finally:
                conn.close()
            # ``copy2`` preserves mtime + permissions; we override
            # permissions immediately below so only mtime preservation
            # matters here (handy for tests that assert on file age).
            shutil.copy2(self._db_path, snap)
            # Read-only protection — an accidental write would otherwise
            # silently corrupt the canonical state. ``stat.S_IRUSR |
            # stat.S_IRGRP | stat.S_IROTH`` is the portable 0o444.
            os.chmod(
                snap,
                stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
            )

    async def restore(self, name: str) -> None:
        """Restore the DB file from snapshot ``name``.

        Bracket via ``released_for_subprocess`` (same drain + lock
        release as ``take``). Inside the bracket: ``shutil.copy2``
        the snapshot file onto the live DB path. ``copy2`` overwrites
        the destination atomically-from-the-FS-perspective (it
        truncates + writes); since the pool is closed inside the
        bracket, no in-flight cursor sees a torn read. The bracket's
        ``finally`` reopens the pool against the restored file.
        """
        snap = self._snap_path(name)
        if not snap.exists():
            raise FileNotFoundError(
                f"Snapshot {name!r} not found at {snap}; "
                f"call take({name!r}) before restore.",
            )
        # ``cast(Any, ...)`` to reach ``released_for_subprocess`` — only the
        # private ``_AsyncDuckdbPool`` exposes it; the cross-dialect
        # ``AsyncConnectionPool`` Protocol doesn't (and shouldn't, since
        # PG / Oracle pools don't have an analogous "release the file
        # lock" semantics). Factory wires DuckDBFileSnapshotter only for
        # ``Dialect.DUCKDB``, so the duck-type access is safe at runtime.
        async with cast(Any, self._pool).released_for_subprocess():
            # Live DB file is read-only-protected from take()? No — we
            # only chmod the snap file. The live file's perms are
            # whatever the OS / umask + duckdb.connect produced (usually
            # 0o644). copy2 will overwrite cleanly.
            shutil.copy2(snap, self._db_path)
            # Ensure the restored DB file is writable — copy2 preserves
            # the snap's 0o444 perms, which would lock subsequent
            # writes. The umask-typical 0o644 is what duckdb.connect
            # would create on its own.
            os.chmod(
                self._db_path,
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
            )

    async def drop(self, name: str) -> None:
        """Delete snapshot ``name``. Idempotent: missing is not an error
        (matches ``DROP TABLE IF EXISTS`` semantics on PG/Oracle impls)."""
        snap = self._snap_path(name)
        try:
            # The 0o444 chmod from take() does NOT block unlink — the
            # mode bits on the file gate writes to the file's
            # contents; unlink modifies the parent directory entry,
            # gated by the parent dir's perms (which we control).
            snap.unlink()
        except FileNotFoundError:
            pass

    async def aclose(self) -> None:
        """Idempotent. Removes the snap dir if (a) WE created it AND
        (b) it's empty. Caller-supplied snap_dir is left intact so a
        test's ``tmp_path`` survives for post-mortem inspection."""
        if self._closed:
            return
        self._closed = True
        if not self._owns_snap_dir:
            return
        try:
            # ``rmdir`` only succeeds on empty directories — matches
            # the docstring's "if empty" semantics. If a snapshot was
            # taken but never dropped, the dir stays; the operator
            # then sees the leftover under /tmp/snap-duckdb-*
            # and can clean it manually (or the next reboot does).
            self._snap_dir.rmdir()
        except OSError:
            # Non-empty or already-gone — either way, no action needed
            # and no signal to surface.
            pass


class OracleGoldenMirrorSnapshotter:
    """BV.3.3 — Oracle-dialect Snapshotter via golden-mirror CTAS.

    Captures the post-Session-Start v-overlay state into a set of
    per-table golden-mirror tables (``{prefix}_v_<tbl>_gold_<name>``)
    using ``CREATE TABLE … AS SELECT *``, then restores by
    ``TRUNCATE`` + direct-path ``INSERT /*+ APPEND */`` from the
    mirror + ``DBMS_MVIEW.REFRESH(method => 'C')`` per matview.
    Operator-locked perf budget: ~2500ms restore — accepted on the
    "Oracle is the slow path; the trainer dogfood walk's wall-time
    win still goes from ~30 min cumulative to a few minutes" basis.

    Design choices (per task brief + operator concern callouts):

    - **Direct-path INSERT** (``/*+ APPEND */``) writes blocks above
      the High Water Mark, bypassing the buffer cache. Requires no
      triggers + no enabled FK on the target. V-overlay base tables
      satisfy both: the schema emitter (``common.l2.schema``) builds
      flat tables on the v prefix — no FK between v_transactions /
      v_daily_balances, no triggers in the L1 schema. If a future
      schema addition introduces either, the direct-path silently
      degrades to conventional INSERT (Oracle's documented
      fallback); we'd notice via the SLA test below.
    - **TRUNCATE … REUSE STORAGE** keeps the table's existing extents
      allocated so the immediately-following direct-path INSERT can
      reuse them rather than re-allocating. With or without REUSE,
      the High Water Mark resets to 0 after TRUNCATE, so direct-path
      writes start at block 0 either way.
    - **IDENTITY column safety.** v_overlay base tables declare
      ``entry NUMBER GENERATED BY DEFAULT AS IDENTITY``; the golden
      mirror's ``SELECT *`` carries explicit entry values, so the
      restore INSERT supplies them and doesn't touch the sequence.
      But: the underlying Oracle identity sequence is NOT reset by
      TRUNCATE — a subsequent INSERT that omits the entry column
      could land on a value below the restored MAX(entry) and
      collide. We bump the identity start past MAX(entry) after
      restore via ``ALTER TABLE … MODIFY (entry GENERATED BY DEFAULT
      AS IDENTITY (START WITH N))`` — pinned by the multi-restore
      round-trip test.
    - **Commit discipline.** Oracle's direct-path INSERT requires a
      COMMIT before any subsequent DML on the same table in the
      same transaction (otherwise ORA-12838). We commit between
      tables; the COMMIT also clears the redo-log accumulation so
      one snapshot doesn't bloat undo for the next.
    - **Matview refresh.** ``DBMS_MVIEW.REFRESH('<name>', 'C')`` is
      a complete refresh — re-derives the matview from scratch.
      Matches the schema's own ``refresh_matviews_sql`` path so a
      post-restore SELECT sees the same shape ``session_start()``
      would have produced.

    Lifecycle: caller-owned (one per dialect cell). ``aclose()``
    drops any leftover ``_gold_*`` mirror tables matching the
    snapshot pattern under the v-overlay prefix — a missed
    ``aclose()`` leaks one mirror per take but the next test
    session's ``aclose()`` sweeps them all.

    Concurrency: not safe across processes (different oracledb
    sessions can race the same ``USER_TABLES`` discovery). The
    runner's per-worker cell scoping handles this; this class
    doesn't add its own lock.
    """

    def __init__(
        self,
        *,
        pool: AsyncConnectionPool,
        base_prefix: str,
    ) -> None:
        self._pool = pool
        self._base_prefix = base_prefix
        self._v_prefix = f"{base_prefix}_v"
        # Track every snapshot name we've taken so ``aclose()`` can
        # sweep leftovers without a USER_TABLES round-trip in the
        # happy path. USER_TABLES is the source of truth on aclose —
        # this set is just a fast hint.
        self._taken: set[str] = set()
        self._closed = False

    def _gold_table(self, table_suffix: str, name: str) -> str:
        """Compose the golden-mirror table name for one base table.

        Pattern: ``{prefix}_v_<suffix>_gold_<name>`` (lowercase as
        written; Oracle folds to upper at the DDL boundary).
        """
        return f"{self._v_prefix}_{table_suffix}_gold_{name}"

    def _live_table(self, table_suffix: str) -> str:
        """Compose the live v-overlay table name (no _gold_ suffix)."""
        return f"{self._v_prefix}_{table_suffix}"

    def _matview_name(self, matview_suffix: str) -> str:
        """Compose the v-overlay matview name from a suffix."""
        return f"{self._v_prefix}_{matview_suffix}"

    async def _exec(self, conn: object, sql: str) -> None:
        """Execute one statement against the AsyncConnection.

        Wraps ``conn.execute(sql)`` with a ``cast(Any, conn)`` so the
        Protocol's positional-only typing doesn't fight the call site.
        The underlying oracledb async connection ignores DDL return
        values, but we still await the coroutine to honor async
        semantics.
        """
        await cast(Any, conn).execute(sql)

    async def take(self, name: str) -> None:
        """Capture v-overlay state into golden-mirror tables.

        For each base table:

        1. Drop any pre-existing mirror under the same name
           (idempotent re-take: a previous failed take left a half-
           mirror; the DROP-via-PL/SQL swallow-on-missing pattern
           handles that).
        2. ``CREATE TABLE <gold> AS SELECT * FROM <live>`` — Oracle
           CTAS auto-commits the DDL and lands the mirror with the
           same column shape (identity columns become plain NUMBER
           in the mirror — we don't need identity semantics there).
        """
        _validate_snapshot_name(name)
        async with self._pool.acquire() as conn:
            for tbl in _V_OVERLAY_BASE_TABLES:
                gold = self._gold_table(tbl, name)
                live = self._live_table(tbl)
                # Oracle has no ``DROP TABLE IF EXISTS``; the canonical
                # idiom is a PL/SQL block that catches ORA-00942 (table
                # or view does not exist) and re-raises everything else.
                await self._exec(
                    conn,
                    (
                        "BEGIN "
                        f"EXECUTE IMMEDIATE 'DROP TABLE {gold} PURGE'; "
                        "EXCEPTION WHEN OTHERS THEN "
                        "IF SQLCODE != -942 THEN RAISE; END IF; "
                        "END;"
                    ),
                )
                # CTAS — Oracle auto-commits DDL; no explicit COMMIT.
                # ``NOLOGGING`` would speed this up further but we
                # default to logged so a crash mid-take leaves a
                # recoverable database. The take budget is the cheap
                # leg of the round-trip anyway (~few hundred ms);
                # restore is the operator-locked 2500ms.
                await self._exec(
                    conn,
                    f"CREATE TABLE {gold} AS SELECT * FROM {live}",
                )
        self._taken.add(name)

    async def restore(self, name: str) -> None:
        """Restore v-overlay state from golden-mirror tables.

        Per base table:

        1. ``TRUNCATE TABLE <live> REUSE STORAGE`` — fast wipe + HWM
           reset; REUSE STORAGE keeps the extent allocation for the
           immediately-following direct-path INSERT to reuse.
        2. ``INSERT /*+ APPEND */ INTO <live> SELECT * FROM <gold>``
           — direct-path write, bypassing the buffer cache.
        3. ``COMMIT`` — required after direct-path INSERT before any
           subsequent DML on the same table (Oracle would otherwise
           raise ORA-12838 on the next access in the same txn).
        4. Bump the identity sequence past MAX(entry) so subsequent
           INSERTs that omit the entry column don't collide with
           restored values.

        After every base table is restored, refresh each v-overlay
        matview via ``DBMS_MVIEW.REFRESH('<name>', method => 'C')``
        in dependency order (the same order the schema emitter uses).
        """
        _validate_snapshot_name(name)
        async with self._pool.acquire() as conn:
            for tbl in _V_OVERLAY_BASE_TABLES:
                gold = self._gold_table(tbl, name)
                live = self._live_table(tbl)
                # Existence probe — if the gold table is missing
                # (no prior take), surface a typed error rather than
                # a downstream ORA-00942 on the INSERT.
                await self._exec(
                    conn,
                    (
                        "BEGIN "
                        f"EXECUTE IMMEDIATE 'SELECT 1 FROM {gold} WHERE ROWNUM = 1'; "
                        "EXCEPTION WHEN OTHERS THEN "
                        f"IF SQLCODE = -942 THEN RAISE_APPLICATION_ERROR(-20001, "
                        f"'Snapshot {name} not found for table {tbl}; call take({name}) first.'); "
                        "ELSE RAISE; END IF; "
                        "END;"
                    ),
                )
                # REUSE STORAGE keeps the extent allocation around —
                # the direct-path INSERT immediately below re-fills
                # them above the (now-reset) High Water Mark. Marginal
                # win on a single restore; compounds across the
                # ~15-plant trainer dogfood walk.
                await self._exec(
                    conn,
                    f"TRUNCATE TABLE {live} REUSE STORAGE",
                )
                await self._exec(
                    conn,
                    f"INSERT /*+ APPEND */ INTO {live} SELECT * FROM {gold}",
                )
                # ORA-12838 protection — direct-path INSERT marks the
                # table such that any subsequent DML in the same txn
                # raises until COMMIT. ``COMMIT`` is its own statement
                # under oracledb async; no PL/SQL wrapper needed.
                await self._exec(conn, "COMMIT")
                # IDENTITY sequence bump — only relevant for tables
                # that DECLARE an entry column. transactions + balances
                # do; config_kv doesn't (PK is the kv key). Both the
                # MAX(entry) SELECT and the ALTER TABLE MODIFY IDENTITY
                # run via ``EXECUTE IMMEDIATE`` so the table's column
                # set is resolved at RUNTIME, not at PL/SQL compile
                # time. Without EXECUTE IMMEDIATE, the static SELECT
                # against a no-entry table (config_kv) raises ORA-06550
                # wrapping ORA-00904 BEFORE the inner EXCEPTION handler
                # can see it — compile-time errors aren't catchable.
                # ORA-00904 / -32793 / -30673 cover the
                # "column missing" / "column-exists-but-isn't-identity"
                # variants that would surface if the schema ever swapped
                # IDENTITY for a plain column on transactions/balances.
                await self._exec(
                    conn,
                    (
                        "DECLARE max_entry NUMBER; BEGIN "
                        "BEGIN EXECUTE IMMEDIATE "
                        f"'SELECT NVL(MAX(entry), 0) + 1 FROM {live}' "
                        "INTO max_entry; "
                        "EXCEPTION WHEN OTHERS THEN "
                        "IF SQLCODE = -904 THEN RETURN; ELSE RAISE; END IF; "
                        "END; "
                        f"EXECUTE IMMEDIATE 'ALTER TABLE {live} MODIFY (entry "
                        "GENERATED BY DEFAULT AS IDENTITY (START WITH ' || max_entry || '))'; "
                        "EXCEPTION WHEN OTHERS THEN "
                        "IF SQLCODE IN (-32793, -30673, -904) THEN NULL; ELSE RAISE; END IF; "
                        "END;"
                    ),
                )
            # Matview refresh — one PL/SQL block per matview, dep
            # order. DBMS_MVIEW.REFRESH method 'C' is a complete
            # refresh; matches schema.refresh_matviews_sql semantics.
            for mv_suffix in _V_OVERLAY_MATVIEW_SUFFIXES:
                mv_name = self._matview_name(mv_suffix)
                await self._exec(
                    conn,
                    f"BEGIN DBMS_MVIEW.REFRESH('{mv_name}', method => 'C'); END;",
                )

    async def drop(self, name: str) -> None:
        """Drop the golden-mirror tables for snapshot ``name``.

        Idempotent: missing tables are swallowed via the ORA-00942
        pattern so callers don't need a try/except. After the sweep
        the snapshot name is removed from our local cache so a
        subsequent ``take(name)`` doesn't try to drop it again
        unnecessarily.
        """
        _validate_snapshot_name(name)
        async with self._pool.acquire() as conn:
            for tbl in _V_OVERLAY_BASE_TABLES:
                gold = self._gold_table(tbl, name)
                await self._exec(
                    conn,
                    (
                        "BEGIN "
                        f"EXECUTE IMMEDIATE 'DROP TABLE {gold} PURGE'; "
                        "EXCEPTION WHEN OTHERS THEN "
                        "IF SQLCODE != -942 THEN RAISE; END IF; "
                        "END;"
                    ),
                )
        self._taken.discard(name)

    async def aclose(self) -> None:
        """Sweep any remaining ``_gold_*`` tables under the v-prefix.

        Idempotent. Discovers leftovers via USER_TABLES — a snapshot
        taken but never explicitly dropped (test crash, missing
        ``finally``) still gets cleaned up.

        The discovery query uses ``LIKE`` against the v-prefix +
        ``_gold_`` infix; Oracle's case-fold means the LIKE pattern
        is upper-case. Matches ALL gold tables under this v-prefix —
        a different snapshotter instance for a different v-prefix on
        the same DB won't collide.
        """
        if self._closed:
            return
        self._closed = True
        # Pattern: every gold table for this v-prefix matches
        # '<UPPER_PREFIX>\_%\_GOLD\_%' with backslash-escape on the
        # underscores so SQL LIKE's ``_`` wildcard doesn't match
        # spurious tables under a sibling prefix.
        upper_prefix = self._v_prefix.upper()
        pattern = f"{upper_prefix}\\_%\\_GOLD\\_%"
        try:
            async with self._pool.acquire() as conn:
                async with cast(Any, conn).cursor() as cur:
                    await cur.execute(
                        (
                            "SELECT table_name FROM USER_TABLES "
                            "WHERE table_name LIKE :pattern ESCAPE '\\'"
                        ),
                        {"pattern": pattern},
                    )
                    rows = await cur.fetchall()
                for row in rows:
                    # row[0] is the upper-case table name as Oracle
                    # stored it. Each PL/SQL DROP is its own block so
                    # one stale-already-dropped table doesn't abort
                    # the sweep of the rest.
                    table_name = str(row[0])
                    await self._exec(
                        conn,
                        (
                            "BEGIN "
                            f"EXECUTE IMMEDIATE 'DROP TABLE {table_name} PURGE'; "
                            "EXCEPTION WHEN OTHERS THEN "
                            "IF SQLCODE != -942 THEN RAISE; END IF; "
                            "END;"
                        ),
                    )
        except Exception:  # noqa: BLE001 — best-effort cleanup; pool may already be closed
            return
        self._taken.clear()


class PostgresSchemaSnapshotter:
    """BV.3.3 — PG-dialect Snapshotter via golden-schema CTAS + restore.

    Pattern:

    1. ``take(name)`` — ``CREATE SCHEMA <prefix>_v_snap_<name>`` then
       ``CREATE TABLE <snap>.<tbl> AS SELECT * FROM <prefix>_v_<tbl>``
       for each base table (``transactions`` / ``daily_balances`` /
       ``config_kv``). Matviews regenerate from base tables via
       ``refresh_v_overlay_matviews_sql`` during restore.
    2. ``restore(name)`` — TRUNCATE+INSERT for all base tables under
       one txn, then ``refresh_v_overlay_matviews_sql`` under autocommit
       (PG ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` can't run in a
       txn block — BV.6 / DL.15; mirrors
       ``deploy_pipeline.py::step_4_matviews``).
    3. ``drop(name)`` — ``DROP SCHEMA <prefix>_v_snap_<name> CASCADE``
       (idempotent via ``IF EXISTS``).
    4. ``aclose()`` — best-effort sweep of every snap schema matching
       the ``<prefix>_v_snap_%`` pattern.

    Why a PG schema namespace (not another table-prefix tier):
    ``DROP SCHEMA ... CASCADE`` drops every snap table in one DDL.
    """

    def __init__(
        self,
        *,
        pool: AsyncConnectionPool,
        base_prefix: str,
        l2_instance: "L2Instance",
    ) -> None:
        self._pool = pool
        self._base_prefix = base_prefix
        self._l2_instance = l2_instance
        from recon_gen.common.l2.v_overlay import v_overlay_prefix  # noqa: PLC0415
        self._v_prefix = v_overlay_prefix(base_prefix)
        self._closed = False

    def _snap_schema(self, name: str) -> str:
        """Pattern: ``<base_prefix>_v_snap_<name>``."""
        return f"{self._base_prefix}_v_snap_{name}"

    async def _exec(self, conn: Any, sql: str) -> None:  # typing-smell: ignore[explicit-any]: psycopg AsyncConnection — cross-dialect Protocol doesn't surface commit/cursor
        """Driver-uniform ``await conn.execute(sql)`` shim."""
        await conn.execute(sql)

    async def take(self, name: str) -> None:
        """CREATE SCHEMA + three CTAS inside one psycopg txn.

        Mid-CTAS error rolls back atomic — no half-built snap schema.
        """
        _validate_snapshot_name(name)
        snap = self._snap_schema(name)
        async with self._pool.acquire() as conn:
            await self._exec(conn, f"CREATE SCHEMA {snap}")
            for tbl in _V_OVERLAY_BASE_TABLES:
                await self._exec(
                    conn,
                    f"CREATE TABLE {snap}.{tbl} AS "
                    f"SELECT * FROM {self._v_prefix}_{tbl}",
                )
            # Explicit commit so CTAS tables are visible to the next
            # ``acquire()``.
            await cast(Any, conn).commit()

    async def restore(self, name: str) -> None:
        """TRUNCATE+INSERT under one txn, then matview refresh under
        autocommit (CONCURRENTLY can't run in a txn block).

        TRUNCATE … RESTART IDENTITY CASCADE: defensive against a future
        FK addition (none today per Schema_v6); RESTART IDENTITY is a
        no-op on tables without identity columns.
        """
        _validate_snapshot_name(name)
        snap = self._snap_schema(name)

        # Phase 1: TRUNCATE+INSERT under default (non-autocommit) txn.
        async with self._pool.acquire() as conn:
            for tbl in _V_OVERLAY_BASE_TABLES:
                await self._exec(
                    conn,
                    f"TRUNCATE {self._v_prefix}_{tbl} "
                    f"RESTART IDENTITY CASCADE",
                )
                await self._exec(
                    conn,
                    f"INSERT INTO {self._v_prefix}_{tbl} "
                    f"SELECT * FROM {snap}.{tbl}",
                )
            await cast(Any, conn).commit()

        # Phase 2: matview refresh under autocommit. Late-import to
        # keep the heavy ``common/l2`` cone out of the import graph
        # (this module loads at session start).
        from recon_gen.common.l2.v_overlay import (  # noqa: PLC0415
            refresh_v_overlay_matviews_sql,
        )
        refresh_sql = refresh_v_overlay_matviews_sql(
            self._l2_instance,
            base_prefix=self._base_prefix,
            dialect=Dialect.POSTGRES,
        )
        async with self._pool.acquire() as conn:
            # psycopg-specific autocommit flip — mirror of the
            # ``deploy_pipeline.py::step_4_matviews`` dance.
            raw = cast(Any, conn)
            prior = bool(raw.autocommit)
            await raw.set_autocommit(True)
            try:
                # refresh_v_overlay_matviews_sql emits multiple
                # statements separated by ``;\n``. Split + run each
                # individually so a parse error surfaces the offending
                # statement, not the whole script.
                for stmt in refresh_sql.split(";\n"):
                    s = stmt.strip().rstrip(";")
                    if not s:
                        continue
                    await self._exec(conn, s)
            finally:
                await raw.set_autocommit(prior)

    async def drop(self, name: str) -> None:
        """Drop snapshot ``name``'s schema. Idempotent via IF EXISTS."""
        _validate_snapshot_name(name)
        snap = self._snap_schema(name)
        async with self._pool.acquire() as conn:
            await self._exec(conn, f"DROP SCHEMA IF EXISTS {snap} CASCADE")
            await cast(Any, conn).commit()

    async def aclose(self) -> None:
        """Best-effort sweep of every leftover snap schema for this prefix.

        Catches a ``take()`` whose paired ``drop()`` never ran (test
        crash between them). Pool-already-closed errors are swallowed
        so we don't mask the original failure.
        """
        if self._closed:
            return
        self._closed = True
        # LIKE pattern with backslash-escaped underscores so the
        # literal ``_v_snap_`` infix doesn't match arbitrary single
        # chars. PG defaults to ``\`` as the LIKE escape.
        like_pattern = f"{self._base_prefix}\\_v\\_snap\\_%"
        try:
            async with self._pool.acquire() as conn:
                cur = await cast(Any, conn).execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name LIKE %s",
                    (like_pattern,),
                )
                rows: list[Any] = await cur.fetchall()  # typing-smell: ignore[explicit-any]: psycopg row tuples are heterogeneous
                for row in rows:
                    schema_name = str(row[0])
                    await self._exec(
                        conn,
                        f"DROP SCHEMA IF EXISTS {schema_name} CASCADE",
                    )
                await cast(Any, conn).commit()
        except Exception:  # noqa: BLE001 — best-effort cleanup; pool may already be closed
            return


async def make_snapshotter(
    cfg: Config,
    pool: AsyncConnectionPool,
    *,
    base_prefix: str,
    l2_instance: "L2Instance",
) -> Snapshotter:
    """Dialect-dispatched factory. Mirrors ``make_connection_pool`` shape.

    Args:
      cfg: Loaded ``Config``; ``cfg.db.dialect`` drives dispatch (and the
        DuckDB impl also reads ``cfg.db.url`` to locate the
        file to copy).
      pool: The shared async pool against the live DB. PG + Oracle
        impls run all DDL/DML through it; the DuckDB impl wraps the
        snapshot+restore in ``pool.released_for_subprocess()`` so the
        file isn't held open during the copy.
      base_prefix: DB-table prefix (``cfg.db.table_prefix``) — needed by
        the PG / Oracle impls to enumerate the per-instance tables +
        matviews to mirror. Passed explicitly (rather than re-derived
        from cfg) so the factory signature documents the dependency.
      l2_instance: The L2Instance the v-overlay was built from. The PG
        impl walks its account / template / matview surface to compose
        the golden-mirror refresh DDL. Forward-typed to avoid a
        circular import.

    Returns:
      A ``Snapshotter`` for this cell.

    Raises:
      ValueError: ``cfg.db.dialect`` isn't one of the three supported
        dialects (PG / Oracle / DuckDB) — matches ``make_connection_pool``'s
        unknown-dialect handling.
    """
    # Sanity-check the dialect now so the factory fails loudly at wire
    # time rather than at first ``take()`` call.
    if cfg.db.dialect not in (Dialect.DUCKDB, Dialect.POSTGRES, Dialect.ORACLE):
        raise ValueError(
            f"Unknown dialect {cfg.db.dialect!r}. "
            "Snapshotter supports duckdb / postgres / oracle.",
        )
    if cfg.db.dialect is Dialect.DUCKDB:
        # DuckDB arm — file-copy via DuckDBFileSnapshotter.
        # ``base_prefix`` + ``l2_instance`` aren't read by the file-copy
        # impl (the whole file IS the state), but the factory signature
        # keeps them for cross-dialect uniformity.
        del base_prefix, l2_instance
        if cfg.db.url is None:
            raise ValueError(
                "cfg.db.url is unset; "
                "DuckDBFileSnapshotter needs a file path.",
            )
        return DuckDBFileSnapshotter(
            db_path=Path(duckdb_path(cfg.db.url)),
            pool=pool,
        )
    if cfg.db.dialect is Dialect.ORACLE:
        # BV.3.3 — Oracle arm via golden-mirror CTAS +
        # TRUNCATE + INSERT /*+ APPEND */ + DBMS_MVIEW.REFRESH.
        # ``l2_instance`` is currently unused by this impl (the
        # v-overlay base + matview surface is enumerated by suffix
        # constants at module scope); kept on the factory signature
        # for cross-dialect uniformity.
        del l2_instance
        return OracleGoldenMirrorSnapshotter(
            pool=pool,
            base_prefix=base_prefix,
        )
    # BV.3.3 — PG arm via schema-namespace CTAS.
    return PostgresSchemaSnapshotter(
        pool=pool,
        base_prefix=base_prefix,
        l2_instance=l2_instance,
    )
