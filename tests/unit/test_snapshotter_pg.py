"""BV.3.3 — PG-dialect Snapshotter integration tests.

Exercises ``PostgresSchemaSnapshotter`` against the session-scoped
dedicated PG container (``snapshotter_pg_container_url`` fixture
from ``tests/conftest.py`` — adopt-or-create against
``recon-gen-snap-test-pg``). When the env-URL escape hatch
(``RECON_GEN_DEMO_DATABASE_URL_PG``) is unset AND Docker isn't
available, the container fixture either skips or fails the fixture
setup — same gate as ``tests/unit/test_cb17a_container_fixtures.py``.

The dedicated container is separate from ``pg_container_url``'s
``recon-gen-test-pg`` so schema-create / drop / TRUNCATE ops here
don't fight the shared db-tier matrix or the bv33 trainer dogfood
walk for the same container (per BV.3.3 isolation split).

Why under tests/unit/ rather than tests/e2e/db/: this exercises the
snapshotter primitive in isolation (no Studio server, no Playwright,
no L1/L2FT visual assertions). The dogfood test that USES the
snapshotter to amortize Session Start lives in tests/e2e/app2/ —
that's the integration consumer.

What this file pins:

- ``take`` → mutate base table → ``restore`` round-trip leaves the
  base table byte-equivalent to its post-take state (the core
  hermeticity contract the trainer dogfood walk depends on).
- Multi-snapshot — two named snapshots coexist; restoring one
  doesn't disturb the other.
- Restore SLA — operator-locked at ~150ms on a few-MB v-overlay;
  we assert <10x headroom (1500ms) so the gate doesn't false-positive
  on CI jitter while still catching a real regression (e.g.,
  someone accidentally re-cloning matviews instead of refreshing).
  WSL2 self-hosted CI disk variance + xdist worker contention burned
  through the prior 5×/750ms headroom (v13.14.0 release blocked at
  815ms on gw12); 10× mirrors DuckDB's APFS-targeted 5× and gives PG
  the equivalent margin on its noisier substrate.

Tier: UNIT (the snapshotter primitive is unit-of-software; the
DB round-trip is its substrate — same shape as
``test_cb17a_container_fixtures.py``).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from recon_gen.common.config import Config
from recon_gen.common.db import (
    AsyncConnectionPool,
    connect_demo_db,
    execute_script,
    make_connection_pool,
)
from recon_gen.common.l2.loader import default_l2_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.l2.v_overlay import (
    clone_base_to_v_sql,
    create_v_overlay_sql,
    drop_v_overlay_sql,
    v_overlay_prefix,
)
from recon_gen.common.sql import Dialect
from tests._marks import Tier, tier
from recon_gen.common.snapshotter import PostgresSchemaSnapshotter


# BV.3.3.f xdist-isolation fix (2026-06-11) — `xdist_group` mark pins
# ALL tests in this file to ONE xdist worker. CAVEAT: this is a silent
# no-op under xdist's default `--dist=load` (which the unit layer uses
# per `_dev/runner.py::_layer_pytest_argv`). It documents intent + acts
# as belt-and-suspenders for the day someone re-enables `--dist=loadgroup`
# for the unit layer. The LOAD-BEARING fix is the per-worker
# `db_table_prefix` disambiguation in `pg_cfg` below: each worker
# operates on its own `snap_pg_test_<worker>` prefix so concurrent
# `emit_schema` DROP-then-CREATE streams against the shared
# `recon-gen-snap-test-pg` container don't race. PG hasn't surfaced
# the race spectrum that Oracle did (PG DDL is transactional with
# rollback, so most collisions surface as recoverable SQLSTATEs rather
# than the Oracle ORA-00955/00942/04063/12003/12006 storm), but the
# latent isolation bug is identical — the Oracle fix template
# (commit e09c3cf5) applies one-to-one.
pytestmark = [
    tier(Tier.UNIT),
    pytest.mark.xdist_group("snapshotter-pg"),
]


_BASE_PREFIX = "snap_pg_test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_cfg(snapshotter_pg_container_url: str, worker_id: str) -> Config:
    """Pin a module-scoped ``Config`` against the snapshotter-dedicated
    PG container.

    Module scope so the schema apply + v-overlay setup runs once per
    test file rather than per test. Each test ``take``s a fresh
    snapshot then ``restore``s it, so cross-test contamination is
    impossible by construction.

    BV.3.3.f (2026-06-11): the ``db_table_prefix`` is per-(file, worker)
    suffixed so concurrent xdist workers don't collide on the shared
    ``recon-gen-snap-test-pg`` container's namespace. ``worker_id`` is
    ``"master"`` under bare pytest and ``"gw<N>"`` under xdist. The
    longest envelope (``snap_pg_test_gw15_v_inv_money_trail_edges`` ≈
    45 chars) stays well under PG's 63-byte identifier cap.

    See the file-level xdist isolation note above for the load-bearing
    rationale + the Oracle sibling fix template.
    """
    from dataclasses import replace
    from tests._test_helpers import make_test_config

    base = make_test_config(
        dialect=Dialect.POSTGRES,
        db_table_prefix=f"{_BASE_PREFIX}_{worker_id}",
    )
    return replace(base, demo_database_url=snapshotter_pg_container_url)


@pytest.fixture(scope="module")
def l2_instance_fixture() -> L2Instance:
    """The default L2 instance (spec_example) — the snapshotter walks
    its matview surface during ``restore``, so a real instance is
    required (vs the bare ``L2Instance(...)`` used by the foundation
    protocol tests)."""
    return default_l2_instance()


@pytest.fixture(scope="module")
def v_overlay_seeded(
    pg_cfg: Config,
    l2_instance_fixture: L2Instance,
) -> Iterator[Config]:
    """Build the base + v-overlay schema once, yield the cfg.

    Steps:

    1. Drop any prior base schema (idempotent).
    2. Emit + apply base schema (empty tables — we don't need rows for
       the snapshot round-trip; the round-trip pins state, not
       semantics).
    3. Drop + create v-overlay schema.
    4. Clone base → v (empty clone is fine for state-round-trip).
    5. Refresh v matviews (empty matviews — still a valid post-
       session_start state for the snapshotter to capture).

    Module-scope teardown drops both schemas so the shared container
    is left clean for sibling test modules.

    BV.3.3.f follow-up (2026-06-11): pgcrypto install lifted up to the
    ``snapshotter_pg_container_url`` container fixture's
    ``post_spinup_fn`` hook (tests/conftest.py::
    ``_install_pgcrypto_extension``). The invariant — pgcrypto exists
    before any consumer's ``emit_schema`` runs — belongs at the
    container layer, not in per-test pre-amble, and the
    ``_shared_container_url`` FileLock serializes the install across
    xdist workers (only the first-firing worker enters spinup).
    """
    from recon_gen.common.l2.schema import emit_schema_drop_sql

    base_prefix = pg_cfg.db_table_prefix

    conn = connect_demo_db(pg_cfg)
    try:
        cur = conn.cursor()
        try:
            # Best-effort drop of any prior schema/v-overlay debris.
            for drop in (
                drop_v_overlay_sql(
                    l2_instance_fixture, base_prefix=base_prefix, dialect=pg_cfg.dialect,
                ),
                emit_schema_drop_sql(
                    l2_instance_fixture, prefix=base_prefix, dialect=pg_cfg.dialect,
                ),
            ):
                try:
                    execute_script(cur, drop, dialect=pg_cfg.dialect)
                except Exception:  # noqa: BLE001 — schema may not exist
                    conn.rollback()
            # Base schema + v overlay + clone + matview refresh.
            execute_script(
                cur,
                emit_schema(
                    l2_instance_fixture, prefix=base_prefix, dialect=pg_cfg.dialect,
                ),
                dialect=pg_cfg.dialect,
            )
            execute_script(
                cur,
                create_v_overlay_sql(
                    l2_instance_fixture, base_prefix=base_prefix, dialect=pg_cfg.dialect,
                ),
                dialect=pg_cfg.dialect,
            )
            execute_script(
                cur,
                clone_base_to_v_sql(base_prefix, dialect=pg_cfg.dialect),
                dialect=pg_cfg.dialect,
            )
            conn.commit()
            # NB — matview refresh is deliberately skipped in fixture
            # setup. ``CREATE MATERIALIZED VIEW WITH DATA`` (PG default)
            # populates each matview with 0 rows during ``emit_schema``,
            # which satisfies the ``REFRESH MATERIALIZED VIEW CONCURRENTLY``
            # "must have been populated" precondition. The snapshotter's
            # ``restore`` path drives its own refresh under autocommit.
            # Earlier iterations called REFRESH here too but hit txn-
            # block hangs (CONCURRENTLY can't run inside a psycopg2
            # implicit txn); removing the fixture-side refresh both
            # avoids the dance and matches the production ``session_start``
            # path (the studio's session-start only refreshes after
            # the clone, then the snapshotter takes over for round-trips).
        finally:
            cur.close()
    finally:
        conn.close()

    yield pg_cfg

    # Teardown — drop both schemas. Best-effort: shared container
    # survives, sibling modules don't see this prefix's debris.
    conn = connect_demo_db(pg_cfg)
    try:
        cur = conn.cursor()
        try:
            for drop in (
                drop_v_overlay_sql(
                    l2_instance_fixture, base_prefix=base_prefix, dialect=pg_cfg.dialect,
                ),
                emit_schema_drop_sql(
                    l2_instance_fixture, prefix=base_prefix, dialect=pg_cfg.dialect,
                ),
            ):
                try:
                    execute_script(cur, drop, dialect=pg_cfg.dialect)
                    conn.commit()
                except Exception:  # noqa: BLE001
                    conn.rollback()
        finally:
            cur.close()
    finally:
        conn.close()


@asynccontextmanager
async def _pool_and_snap(
    cfg: Config, l2_instance: L2Instance,
) -> AsyncGenerator[tuple[AsyncConnectionPool, PostgresSchemaSnapshotter]]:
    """Build pool + snapshotter inside the caller's event loop and
    guarantee teardown.

    Why an async-context-manager rather than two ``@pytest.fixture``
    wrappers: ``psycopg_pool.AsyncConnectionPool`` binds its internal
    ``asyncio.Lock`` / ``Event`` / ``Queue`` primitives to the event
    loop on which ``open()`` is awaited. The earlier shape — a sync
    fixture calling ``asyncio.run(make_connection_pool(...))`` then
    yielding the pool to a test that ran its own ``asyncio.run(_run())``
    — created the pool on loop A, then handed it to loop B. Acquires
    on loop B hung forever waiting on the dead loop A's primitives
    (sample(1) showed every test thread parked in ``_PySemaphore_Wait``
    inside a queue.get()). Mirrors the Oracle snapshotter test pattern
    (``test_snapshotter_oracle.py``), which already builds the pool +
    snapshotter inside the single ``_round_trip`` coroutine.

    Threads ``cfg.db_table_prefix`` through to the snapshotter so the
    per-worker prefix from ``pg_cfg`` is honored (BV.3.3.f).
    """
    pool = await make_connection_pool(cfg)
    snap = PostgresSchemaSnapshotter(
        pool=pool, base_prefix=cfg.db_table_prefix, l2_instance=l2_instance,
    )
    try:
        yield pool, snap
    finally:
        await snap.aclose()
        await pool.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _v_prefix(base_prefix: str) -> str:
    return v_overlay_prefix(base_prefix)


async def _insert_marker_row(
    pool: AsyncConnectionPool, base_prefix: str, marker: str,
) -> None:
    """Insert a single marker row into ``{v}_config_kv`` — a small,
    idempotent mutation we can detect via SELECT.

    ``config_kv`` columns (per ``common.l2.config_table.emit_config_table_ddl``):
    ``node_id`` VARCHAR PK / ``parent_id`` VARCHAR / ``key`` VARCHAR(255)
    / ``value`` TEXT. We use a unique node_id per marker so concurrent
    inserts in the same test don't collide on the PK."""
    v = _v_prefix(base_prefix)
    async with pool.acquire() as conn:
        raw: Any = conn
        await raw.execute(
            f"INSERT INTO {v}_config_kv "
            f"(node_id, parent_id, key, value) "
            f"VALUES (%s, %s, %s, %s)",
            (
                f"__snap_test_node_{marker}__",
                None,
                f"__snap_test_{marker}",
                marker,
            ),
        )
        await raw.commit()


async def _select_marker_count(
    pool: AsyncConnectionPool, base_prefix: str, marker: str,
) -> int:
    """Count rows in ``{v}_config_kv`` whose ``value`` matches ``marker``.
    The single-cell shape sidesteps the heterogeneous-row typing dance."""
    v = _v_prefix(base_prefix)
    async with pool.acquire() as conn:
        raw: Any = conn
        cur = await raw.execute(
            f"SELECT COUNT(*) FROM {v}_config_kv WHERE value = %s",
            (marker,),
        )
        rows: list[Any] = await cur.fetchall()
    return int(rows[0][0])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """take → mutate → restore round-trip leaves the base table state
    byte-equivalent to its post-take state (modulo identity-column
    re-numbering which ``RESTART IDENTITY`` resets explicitly)."""

    def test_take_then_mutate_then_restore_undoes_mutation(
        self,
        v_overlay_seeded: Config,
        l2_instance_fixture: L2Instance,
    ) -> None:
        base_prefix = v_overlay_seeded.db_table_prefix

        async def _run() -> None:
            async with _pool_and_snap(
                v_overlay_seeded, l2_instance_fixture,
            ) as (pool, snap):
                # Pre-take state — empty config_kv.
                pre = await _select_marker_count(pool, base_prefix, "round_trip_marker")
                assert pre == 0, (
                    f"pre-take config_kv already has marker rows (count={pre}); "
                    "the module fixture's clean-slate guarantee is broken."
                )

                # Take snapshot of clean state.
                await snap.take("rt")

                # Mutate — insert a marker row.
                await _insert_marker_row(pool, base_prefix, "round_trip_marker")
                mid = await _select_marker_count(pool, base_prefix, "round_trip_marker")
                assert mid == 1, "mutation didn't land — INSERT path is broken"

                # Restore — marker should disappear.
                await snap.restore("rt")
                post = await _select_marker_count(pool, base_prefix, "round_trip_marker")
                assert post == 0, (
                    f"restore didn't undo the mutation (count={post} after "
                    "restore from clean snapshot); base-table TRUNCATE+INSERT "
                    "phase isn't producing byte-equivalent state."
                )

                # Cleanup the snapshot — drop is idempotent.
                await snap.drop("rt")

        asyncio.run(_run())


class TestMultiSnapshot:
    """Two named snapshots coexist; restoring one doesn't disturb the
    other. Validates the snap-schema namespace doesn't collide and
    that ``restore(A)`` reads ``snap_A``'s tables (not ``snap_B``'s)."""

    def test_two_snapshots_coexist_and_restore_independently(
        self,
        v_overlay_seeded: Config,
        l2_instance_fixture: L2Instance,
    ) -> None:
        base_prefix = v_overlay_seeded.db_table_prefix

        async def _run() -> None:
            async with _pool_and_snap(
                v_overlay_seeded, l2_instance_fixture,
            ) as (pool, snap):
                # Snapshot A — empty state.
                await snap.take("a")

                # Mutate, then snapshot B — has the marker_b row.
                await _insert_marker_row(pool, base_prefix, "multi_b")
                await snap.take("b")

                # Both snapshots should now exist in information_schema.
                v_prefix = _v_prefix(base_prefix)
                async with pool.acquire() as conn:
                    raw: Any = conn
                    cur = await raw.execute(
                        "SELECT COUNT(*) FROM information_schema.schemata "
                        "WHERE schema_name = %s OR schema_name = %s",
                        (f"{base_prefix}_v_snap_a", f"{base_prefix}_v_snap_b"),
                    )
                    rows: list[Any] = await cur.fetchall()
                assert int(rows[0][0]) == 2, (
                    "two named snapshots should both exist post-take; "
                    f"got {int(rows[0][0])} matching schemas. Snap-schema "
                    "naming collision?"
                )

                # Insert another marker so the live state is now distinct
                # from BOTH snapshots.
                await _insert_marker_row(pool, base_prefix, "multi_c")

                # Restore A — both markers should disappear.
                await snap.restore("a")
                assert await _select_marker_count(pool, base_prefix, "multi_b") == 0
                assert await _select_marker_count(pool, base_prefix, "multi_c") == 0

                # Restore B — marker_b returns, marker_c stays gone.
                await snap.restore("b")
                assert await _select_marker_count(pool, base_prefix, "multi_b") == 1
                assert await _select_marker_count(pool, base_prefix, "multi_c") == 0

                # Cleanup.
                await snap.drop("a")
                await snap.drop("b")

                # Final state should be just marker_b — restore to empty
                # via a fresh "clean" snapshot for the next test.
                await snap.take("__cleanup__")
                await snap.restore("__cleanup__")  # no-op but proves clean
                await snap.drop("__cleanup__")
                # Drop marker_b directly via raw DELETE — quicker than
                # round-tripping another snapshot.
                v = v_prefix
                async with pool.acquire() as conn:
                    raw2: Any = conn
                    await raw2.execute(
                        f"DELETE FROM {v}_config_kv WHERE value IN (%s, %s)",
                        ("multi_b", "multi_c"),
                    )
                    await raw2.commit()

        asyncio.run(_run())


class TestRestoreSLA:
    """Restore latency — operator-locked at ~150ms on a few-MB v-overlay.

    The empty-v-overlay round-trip should be substantially faster
    (matviews refresh near-instantly with no rows), so we assert
    <1500ms as a generous-but-meaningful upper bound. If this
    regresses to multi-second, something's badly off — likely
    matview-refresh is re-creating tables instead of REFRESHing
    (DuckDB-style "as table" fallback firing on PG by mistake), or
    the snapshot is mirroring matviews instead of regenerating.
    """

    def test_restore_completes_under_sla(
        self,
        v_overlay_seeded: Config,
        l2_instance_fixture: L2Instance,
    ) -> None:
        async def _run() -> float:
            async with _pool_and_snap(
                v_overlay_seeded, l2_instance_fixture,
            ) as (_pool, snap):
                await snap.take("sla")
                # Warm-up — first restore may pay JIT / plan-cache cost.
                await snap.restore("sla")
                # Measured restore.
                t0 = time.perf_counter()
                await snap.restore("sla")
                elapsed = time.perf_counter() - t0
                await snap.drop("sla")
                return elapsed

        elapsed = asyncio.run(_run())
        # 1500ms = 10× the operator-locked target. Tight enough to
        # catch a real regression; loose enough to survive CI jitter
        # + container cold-start + WSL2 self-hosted runner disk
        # variance + xdist worker contention. The trainer dogfood
        # walk will surface a slower regression via its own wall
        # budget; this is the smoke-level gate. Bumped from 5×/750ms
        # in v13.14.1 after v13.14.0 release flaked at 815ms on
        # gw12 (8.7% overshoot under xdist contention) — mirrors the
        # DuckDB sibling SLA's 5× headroom but doubled because PG
        # runs on WSL2 not local APFS.
        assert elapsed < 1.5, (
            f"restore took {elapsed:.3f}s, > 1500ms SLA. "
            "Likely a matview-refresh regression — verify "
            "refresh_v_overlay_matviews_sql is emitting REFRESH "
            "MATERIALIZED VIEW, not DROP+CREATE TABLE."
        )


class TestDropIdempotent:
    """``drop`` is idempotent (IF EXISTS semantics) — calling it on a
    never-taken name is a no-op, not an error. The trainer dogfood
    test's per-plant cleanup ``finally`` relies on this."""

    def test_drop_of_never_taken_name_is_a_no_op(
        self,
        v_overlay_seeded: Config,
        l2_instance_fixture: L2Instance,
    ) -> None:
        async def _run() -> None:
            async with _pool_and_snap(
                v_overlay_seeded, l2_instance_fixture,
            ) as (_pool, snap):
                # Must not raise.
                await snap.drop("never_taken")

        asyncio.run(_run())

    def test_drop_after_take_is_idempotent(
        self,
        v_overlay_seeded: Config,
        l2_instance_fixture: L2Instance,
    ) -> None:
        async def _run() -> None:
            async with _pool_and_snap(
                v_overlay_seeded, l2_instance_fixture,
            ) as (_pool, snap):
                await snap.take("dropme")
                await snap.drop("dropme")
                # Second drop should be a no-op.
                await snap.drop("dropme")

        asyncio.run(_run())


class TestAcloseSweepsLeftoverSchemas:
    """``aclose`` sweeps any leftover snap schemas for this prefix —
    catches the case where a ``take()`` succeeded but its matching
    ``drop()`` never ran (test crash between them)."""

    def test_aclose_drops_orphan_snap_schemas(
        self,
        v_overlay_seeded: Config,
        l2_instance_fixture: L2Instance,
    ) -> None:
        base_prefix = v_overlay_seeded.db_table_prefix

        async def _run() -> None:
            # Build a fresh pool + snapshotter inline; we want to call
            # aclose() ourselves and observe its sweep, so we bypass the
            # _pool_and_snap helper's automatic aclose() teardown.
            pool = await make_connection_pool(v_overlay_seeded)
            try:
                local = PostgresSchemaSnapshotter(
                    pool=pool, base_prefix=base_prefix,
                    l2_instance=l2_instance_fixture,
                )
                await local.take("orphan")

                # Verify the orphan schema exists.
                async with pool.acquire() as conn:
                    raw: Any = conn
                    cur = await raw.execute(
                        "SELECT COUNT(*) FROM information_schema.schemata "
                        "WHERE schema_name = %s",
                        (f"{base_prefix}_v_snap_orphan",),
                    )
                    rows: list[Any] = await cur.fetchall()
                assert int(rows[0][0]) == 1, "take didn't create the snap schema"

                # aclose should sweep it.
                await local.aclose()

                async with pool.acquire() as conn:
                    raw2: Any = conn
                    cur2 = await raw2.execute(
                        "SELECT COUNT(*) FROM information_schema.schemata "
                        "WHERE schema_name = %s",
                        (f"{base_prefix}_v_snap_orphan",),
                    )
                    rows2: list[Any] = await cur2.fetchall()
                assert int(rows2[0][0]) == 0, (
                    "aclose didn't sweep the orphan schema — leftover "
                    "namespace pollution across test sessions."
                )
            finally:
                await pool.close()

        asyncio.run(_run())
