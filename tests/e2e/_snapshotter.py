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
Studio test server + matching ``App2Driver`` verbs (preserves the
"everything through driver" invariant — see X.2.q).

Test-rig only — NOT a Studio runtime concern. The Studio server's
production code path doesn't see this module; only e2e tests do.
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
#: regression surfaces as a snapshot round-trip drift in the dogfood
#: walk rather than a silent miss. Used by the Oracle impl, which
#: drives one ``DBMS_MVIEW.REFRESH`` PL/SQL block per matview.
_V_OVERLAY_MATVIEW_SUFFIXES: tuple[str, ...] = (
    # Leaves: read from base tables only.
    "current_transactions",
    "current_daily_balances",
    # Helpers: read from current_*.
    "computed_subledger_balance",
    "computed_ledger_balance",
    # CL.5 carry-forward effective balance.
    "effective_balances",
    # L1 invariants.
    "drift",
    "ledger_drift",
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

    Returned by ``make_snapshotter`` for every dialect until the per-
    dialect impls land in the build-out phase. The factory shape lets
    the test harness wire the call sites + HTTP route + driver verbs
    against this stub first — each impl can then be swapped in cell-by-
    cell without re-touching call sites.

    Each verb names the dialect-impl phase it's waiting on, so a test
    that hits the stub gets an actionable failure rather than an opaque
    ``NotImplementedError``.
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
            self._snap_dir = Path(tempfile.mkdtemp(prefix="recon-snap-duckdb-"))
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
            # then sees the leftover under /tmp/recon-snap-duckdb-*
            # and can clean it manually (or the next reboot does).
            self._snap_dir.rmdir()
        except OSError:
            # Non-empty or already-gone — either way, no action needed
            # and no signal to surface.
            pass


async def make_snapshotter(
    cfg: Config,
    pool: AsyncConnectionPool,
    *,
    base_prefix: str,
    l2_instance: "L2Instance",
) -> Snapshotter:
    """Dialect-dispatched factory. Mirrors ``make_connection_pool`` shape.

    Args:
      cfg: Loaded ``Config``; ``cfg.dialect`` drives dispatch (and the
        future DuckDB impl will also read ``cfg.demo_database_url`` to
        locate the file to copy).
      pool: The shared async pool against the live DB. The future PG +
        Oracle impls run all DDL/DML through it; the future DuckDB impl
        wraps the snapshot+restore in ``pool.released_for_subprocess()``
        so the file isn't held open during the copy.
      base_prefix: DB-table prefix (``cfg.db_table_prefix``) — needed by
        the future PG / Oracle impls to enumerate the per-instance
        tables + matviews to mirror. Passed explicitly (rather than re-
        derived from cfg) so the factory signature documents the
        dependency.
      l2_instance: The L2Instance the v-overlay was built from. The PG
        / Oracle impls walk its account / template / matview surface
        to compose the golden-mirror DDL. Forward-typed to avoid a
        circular import; the factory only stores the reference for the
        impl to consult later.

    Returns:
      A ``Snapshotter`` for this cell. Until the per-dialect impls land,
      every dialect returns a ``NotImplementedSnapshotter`` (the build-
      out phase replaces this dispatch arm-by-arm).

    Raises:
      ValueError: ``cfg.dialect`` isn't one of the three supported
        dialects (PG / Oracle / DuckDB) — matches ``make_connection_pool``'s
        unknown-dialect handling.
    """
    # Sanity-check the dialect now so the factory fails loudly at wire
    # time rather than at first ``take()`` call. We don't *use* the
    # dialect yet (every arm returns the stub), but the explicit gate
    # documents the supported surface + protects the future per-dialect
    # dispatch from "Unknown dialect" silent-fallthroughs.
    if cfg.dialect not in (Dialect.DUCKDB, Dialect.POSTGRES, Dialect.ORACLE):
        raise ValueError(
            f"Unknown dialect {cfg.dialect!r}. "
            "Snapshotter supports duckdb / postgres / oracle.",
        )
    if cfg.dialect is Dialect.DUCKDB:
        # BV.3.3 (this cell) — DuckDB arm now ships.
        # ``base_prefix`` + ``l2_instance`` aren't read by the file-copy
        # impl (the whole file IS the state), but the factory signature
        # keeps them for cross-dialect uniformity.
        del base_prefix, l2_instance
        if cfg.demo_database_url is None:
            raise ValueError(
                "cfg.demo_database_url is unset; "
                "DuckDBFileSnapshotter needs a file path.",
            )
        return DuckDBFileSnapshotter(
            db_path=Path(duckdb_path(cfg.demo_database_url)),
            pool=pool,
        )
    # PG / Oracle arms — stub until phase 2 lands their impls.
    # Hold the wiring deps on a stub instance variable so per-dialect
    # impls have somewhere obvious to read them. The stub itself
    # discards them — that's fine; the test harness only inspects the
    # Protocol surface.
    del pool, base_prefix, l2_instance
    return NotImplementedSnapshotter()
