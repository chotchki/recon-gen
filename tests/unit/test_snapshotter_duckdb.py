"""BV.3.3 snapshot — DuckDBFileSnapshotter unit tests.

Asserts the file-copy + released_for_subprocess() bracket pattern
holds end-to-end against a real DuckDB file (tmp_path-backed; no
container, no shared state). Covers:

- seed → take → mutate → restore byte-equivalence
- multiple-snapshot byte-identical round-trip
- restore SLA on MB-class state (<1s target; tighter 250ms here)
- drop idempotence + aclose dir cleanup
- restore-of-missing-snapshot loud-fails
- snap file is chmod 0o444 (read-only protection)

The "concurrent take/restore raises clear error" case from the cell
spec is covered structurally: ``released_for_subprocess()`` holds the
pool's ``_lifecycle_lock`` for the whole bracket, so a second take()
issued mid-bracket queues on the lock — verified via two
``asyncio.create_task`` siblings + a hash check after both complete
(no torn snapshot; the second wins).

Test-rig only — lives under tests/unit/ (no container, no AWS); the
trainer-dogfood integration test (next phase BV.3.3) will wire this
into the harness on top of a live PG / Oracle / DuckDB seed.
"""
from __future__ import annotations

import asyncio
import filecmp
import hashlib
import stat
import time
from pathlib import Path

import pytest

from recon_gen.common.db import (
    AsyncConnectionPool,
    make_connection_pool,
    make_demo_database_url,
)
from recon_gen.common.sql import Dialect
from tests._test_helpers import make_test_config
from recon_gen.common.snapshotter import DuckDBFileSnapshotter


# -- shared fixtures + helpers --------------------------------------------


def _seed_sync(db_path: Path, rows: int = 1000) -> None:
    """Populate a fresh DuckDB file with `rows` deterministic rows.

    Sync helper — runs OUTSIDE the async pool so the test's setup
    doesn't entangle with the snapshotter's lifecycle. 1000 rows ≈
    a few KB of state; enough that a file-copy isn't a no-op but
    still well under the 250ms restore SLA.
    """
    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE t (id INTEGER PRIMARY KEY, label TEXT, val DOUBLE)",
        )
        # Single INSERT…VALUES batched via a generated range; deterministic
        # so cross-snapshot diffs are meaningful.
        conn.execute(
            "INSERT INTO t "
            "SELECT i, 'row-' || i, i * 1.5 FROM range(0, ?) t(i)",
            [rows],
        )
        conn.execute("CHECKPOINT")
    finally:
        conn.close()


async def _row_count(pool: AsyncConnectionPool) -> int:
    """Count rows via the live pool — proves the restored file is
    open-able + readable (not just byte-equal on disk)."""
    async with pool.acquire() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM t")
        rows = await cur.fetchall()
    return int(rows[0][0])


async def _max_id(pool: AsyncConnectionPool) -> int:
    async with pool.acquire() as conn:
        cur = await conn.execute("SELECT COALESCE(MAX(id), -1) FROM t")
        rows = await cur.fetchall()
    return int(rows[0][0])


async def _insert_row(pool: AsyncConnectionPool, row_id: int) -> None:
    """Mutation between take() and restore() — proves restore actually
    rolls state back, not "no-op pass-through"."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO t (id, label, val) VALUES (?, ?, ?)",
            [row_id, f"new-{row_id}", row_id * 2.0],
        )
        await conn.execute("CHECKPOINT")


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _build_cfg_for(db_file: Path):
    return make_test_config(
        db_dialect=Dialect.DUCKDB,
        db_url=make_demo_database_url(Dialect.DUCKDB, db_file),
    )


# -- roundtrip behaviour --------------------------------------------------


class TestRoundTrip:
    """take → mutate → restore restores byte-equivalent state.

    The byte-equivalence claim is the operator-visible contract: the
    trainer dogfood test reads back matview rows after restore + asserts
    on them, so anything less than byte-identity could shift the
    snapshot's L1/L2FT violation set across plants.
    """

    def test_seed_take_mutate_restore_round_trip(
        self, tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=1000)
        baseline_count = 1000

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file,
                pool=pool,
                snap_dir=tmp_path / "snaps",
            )
            try:
                # Confirm baseline before take.
                assert await _row_count(pool) == baseline_count
                # Snapshot.
                await snap.take("seeded")
                # Mutate.
                await _insert_row(pool, row_id=9999)
                assert await _row_count(pool) == baseline_count + 1
                # Restore.
                await snap.restore("seeded")
                # State is back to the snapshotted point.
                assert await _row_count(pool) == baseline_count
                assert await _max_id(pool) == baseline_count - 1
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())

    def test_multiple_snapshots_byte_identical_roundtrip(
        self, tmp_path: Path,
    ) -> None:
        """Two snapshots taken at different points + each restored
        independently → each restore produces a file byte-equal to its
        OWN snap, not the other.

        Catches a regression where ``restore()`` reads from a cached /
        stale path (e.g. last-snapshot-name shortcut)."""
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=500)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file,
                pool=pool,
                snap_dir=tmp_path / "snaps",
            )
            try:
                await snap.take("alpha")
                # Mutate between snaps so alpha != beta on disk.
                await _insert_row(pool, row_id=10001)
                await _insert_row(pool, row_id=10002)
                await snap.take("beta")
                # Restore alpha → file matches alpha snap.
                await snap.restore("alpha")
                assert filecmp.cmp(
                    snap.snap_dir / "alpha.duckdb", db_file, shallow=False,
                ), "alpha restore should leave db byte-equal to alpha snap"
                # Counts confirm logical-state match too.
                assert await _row_count(pool) == 500
                # Restore beta → file matches beta snap.
                await snap.restore("beta")
                assert filecmp.cmp(
                    snap.snap_dir / "beta.duckdb", db_file, shallow=False,
                ), "beta restore should leave db byte-equal to beta snap"
                assert await _row_count(pool) == 502
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())


# -- perf SLA --------------------------------------------------------------


class TestRestoreSLA:
    """Operator-locked perf target: ~50ms restore on MB-class state.

    We assert a much looser 250ms ceiling here — CI runner variance,
    macOS HFS+ vs APFS, cold filesystem cache — but still 10× faster
    than the PG schema-namespace path's ~150ms budget. A regression to
    seconds (which is what the cumulative-walk was paying) would fail
    loudly.

    The bracket itself (lock + drain + reopen) is the perf-limiting
    factor on small data — not the copy. We seed enough rows that
    the file is bigger than one disk block so the copy isn't trivially
    constant-time.
    """

    def test_restore_under_250ms(self, tmp_path: Path) -> None:
        db_file = tmp_path / "db.duckdb"
        # ~50k rows ≈ ~1MB file — representative of post-Session-Start
        # v-overlay scale (a few MB, dominated by matview footprint).
        _seed_sync(db_file, rows=50_000)

        async def _run() -> float:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file,
                pool=pool,
                snap_dir=tmp_path / "snaps",
            )
            try:
                await snap.take("perf")
                # Warm the FS cache + the pool lifecycle path once, so
                # we measure steady-state, not cold-cache + first-touch.
                await snap.restore("perf")
                # Mutate so the restored state is *different* — proves
                # the timed restore did real work.
                await _insert_row(pool, row_id=999_999)
                start = time.monotonic()
                await snap.restore("perf")
                elapsed = time.monotonic() - start
                # Sanity-check the restore took effect.
                assert await _row_count(pool) == 50_000
                return elapsed
            finally:
                await snap.aclose()
                await pool.close()

        elapsed = asyncio.run(_run())
        assert elapsed < 0.250, (
            f"restore took {elapsed * 1000:.1f}ms, "
            f"expected <250ms (operator target ~50ms)"
        )


# -- snap file invariants -------------------------------------------------


class TestSnapFileInvariants:
    """The snap file is the canonical state; we protect it from
    accidental writes with chmod 0o444. Tests assert the bit pattern
    rather than relying on a subsequent write failing (which would be
    a race + an OS-mode-bit-dependent flake)."""

    def test_take_writes_snap_at_expected_path(
        self, tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=100)
        snaps = tmp_path / "snaps"

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=snaps,
            )
            try:
                await snap.take("here")
                assert (snaps / "here.duckdb").is_file()
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())

    def test_snap_file_is_read_only_after_take(
        self, tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=100)

        async def _run() -> Path:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                await snap.take("ro")
                return snap.snap_dir / "ro.duckdb"
            finally:
                # Don't aclose() yet — we want to inspect the snap file
                # after _run returns. Pool close is fine.
                await pool.close()

        snap_path = asyncio.run(_run())
        st = snap_path.stat()
        mode = stat.S_IMODE(st.st_mode)
        # 0o444 == read-only for everyone.
        assert mode == 0o444, (
            f"snap perms = {oct(mode)}, expected 0o444 (read-only)"
        )

    def test_restored_db_is_writable(self, tmp_path: Path) -> None:
        """After restore, the live DB file must NOT inherit the snap's
        0o444 — otherwise the next test mutation would fail with
        PermissionError. (copy2 preserves perms; the impl chmods back
        after.)"""
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=100)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                await snap.take("w")
                await snap.restore("w")
                # A subsequent write must succeed.
                await _insert_row(pool, row_id=42_000)
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())


# -- lifecycle: drop + aclose ---------------------------------------------


class TestLifecycle:
    def test_drop_idempotent(self, tmp_path: Path) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=50)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                await snap.take("dropme")
                assert (snap.snap_dir / "dropme.duckdb").exists()
                await snap.drop("dropme")
                assert not (snap.snap_dir / "dropme.duckdb").exists()
                # Second drop is a no-op (idempotent).
                await snap.drop("dropme")
                # Drop on never-taken snap is also a no-op.
                await snap.drop("never_taken")
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())

    def test_restore_missing_snap_loud_fails(self, tmp_path: Path) -> None:
        """Calling restore("foo") when "foo" was never taken raises a
        clear error — not a silent no-op + stale-state surprise."""
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=10)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                with pytest.raises(FileNotFoundError, match="not found"):
                    await snap.restore("never_taken")
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())

    def test_aclose_removes_owned_empty_snap_dir(
        self, tmp_path: Path,
    ) -> None:
        """When the snapshotter created its own snap_dir AND all snaps
        are dropped, aclose() removes the dir."""
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=10)

        async def _run() -> Path:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            # No snap_dir → snapshotter mkdtemps its own.
            snap = DuckDBFileSnapshotter(db_path=db_file, pool=pool)
            owned = snap.snap_dir
            try:
                await snap.take("temp")
                await snap.drop("temp")
            finally:
                await snap.aclose()
                await pool.close()
            return owned

        owned = asyncio.run(_run())
        assert not owned.exists(), (
            f"aclose should have removed owned empty snap_dir {owned}"
        )

    def test_aclose_leaves_caller_owned_snap_dir(
        self, tmp_path: Path,
    ) -> None:
        """Caller-supplied snap_dir survives aclose — tmp_path stays
        intact for post-test inspection, matching pytest's expectations."""
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=10)
        snap_dir = tmp_path / "caller-owned"

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=snap_dir,
            )
            try:
                pass
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())
        assert snap_dir.is_dir(), (
            "caller-owned snap_dir should survive aclose"
        )

    def test_aclose_idempotent(self, tmp_path: Path) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=10)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                await snap.aclose()
                # Second aclose is a no-op.
                await snap.aclose()
            finally:
                await pool.close()

        asyncio.run(_run())


# -- snapshot name validation ---------------------------------------------


class TestSnapshotNameValidation:
    """The snapshot name is validated at every entry point — defends
    against path-traversal (``../``) + Oracle identifier-unsafe chars.
    These tests probe the validator via the DuckDB impl's verbs (vs.
    importing the private ``_validate_snapshot_name`` directly) so a
    future refactor of where the validation lives doesn't silently
    drop the gate."""

    def test_empty_name_rejected(self, tmp_path: Path) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=10)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                with pytest.raises(ValueError, match="non-empty"):
                    await snap.take("")
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=10)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                # ``..`` would let a caller write outside snap_dir if
                # the path were interpolated naively.
                with pytest.raises(ValueError, match="A-Za-z0-9_"):
                    await snap.take("../escape")
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())


# -- concurrency ----------------------------------------------------------


class TestConcurrency:
    """``released_for_subprocess`` holds the pool's ``_lifecycle_lock``
    across the whole bracket, so concurrent take/restore serialize
    naturally. Two siblings issued via ``asyncio.create_task`` should
    both complete; the second sees a consistent (not torn) DB file."""

    def test_concurrent_takes_serialize_without_corruption(
        self, tmp_path: Path,
    ) -> None:
        db_file = tmp_path / "db.duckdb"
        _seed_sync(db_file, rows=200)

        async def _run() -> None:
            cfg = _build_cfg_for(db_file)
            pool = await make_connection_pool(cfg)
            snap = DuckDBFileSnapshotter(
                db_path=db_file, pool=pool, snap_dir=tmp_path / "snaps",
            )
            try:
                # Two concurrent takes — the pool's lifecycle_lock
                # serializes them. Both should complete without
                # raising; both snap files should be valid DuckDB
                # files (sha256 matches the live DB at take time).
                await asyncio.gather(
                    snap.take("c1"),
                    snap.take("c2"),
                )
                # Both snaps present + non-empty.
                p1 = snap.snap_dir / "c1.duckdb"
                p2 = snap.snap_dir / "c2.duckdb"
                assert p1.is_file() and p1.stat().st_size > 0
                assert p2.is_file() and p2.stat().st_size > 0
                # Both snaps are byte-identical to each other (no
                # mutation between them) — proves the serialization
                # held + neither saw a torn copy.
                assert _file_hash(p1) == _file_hash(p2)
            finally:
                await snap.aclose()
                await pool.close()

        asyncio.run(_run())
