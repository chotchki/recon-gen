"""BV.3.3 snapshot — OracleGoldenMirrorSnapshotter integration tests.

Pins the take → mutate → restore round-trip + the multi-snapshot
shape + the operator-accepted ~2500ms restore SLA against a live
Oracle 19c container.

Why under tests/unit/ despite the live-DB dependency: the test file
exercises a single class's round-trip contract — pure mechanism, not
a workflow span. ``@tier(Tier.DB)`` + ``@needs(Need.DOCKER)`` route
it through the runner's DB tier (the same ordering chain the trainer
dogfood walk uses), and the ``@dialects(Dialect.OR)`` mark scopes it
to the Oracle cell so DuckDB / PG cells skip it cleanly.

Setup cost is one-shot per session — the
``snapshotter_oracle_container_url`` session-scoped fixture adopts the
BV.3.3-dedicated ``recon-gen-snap-test-oracle`` container (separate
from the shared db-tier ``recon-gen-test-oracle`` so heavy CTAS +
DBMS_MVIEW.REFRESH ops here don't fight the layered db-tier matrix
or the bv33 trainer dogfood walk); this test builds a minimal
v-overlay schema (full ``emit_schema`` so matview DBMS_MVIEW.REFRESH
targets resolve), inserts a handful of rows, then exercises the
snapshotter verbs.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, cast

import pytest

from recon_gen.common.db import (
    AsyncConnectionPool,
    connect_demo_db,
    execute_script,
    make_connection_pool,
)
from recon_gen.common.l2 import L2Instance, load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.l2.v_overlay import (
    create_v_overlay_sql,
    drop_v_overlay_sql,
    refresh_v_overlay_matviews_sql,
    v_overlay_prefix,
)
from recon_gen.common.sql.dialect import Dialect
from tests._marks import Dialect as MarkDialect, Need, Tier, dialects, needs, tier
from tests._test_helpers import make_test_config
from recon_gen.common.snapshotter import (
    OracleGoldenMirrorSnapshotter,
    _V_OVERLAY_BASE_TABLES,
)


pytestmark = [tier(Tier.DB), dialects(MarkDialect.OR), needs(Need.DOCKER)]


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spec_instance() -> L2Instance:
    """The spec_example L2 — minimum viable shape for emit_schema +
    refresh_v_overlay_matviews_sql. Module-scoped so all the test
    rounds share one parse."""
    return load_instance(_FIXTURES / "spec_example.yaml")


@pytest.fixture(scope="module")
def oracle_cfg(
    snapshotter_oracle_container_url: str,
    spec_instance: L2Instance,
) -> Any:
    """Build a ``Config`` pointed at the snapshotter-dedicated Oracle
    container.

    The ``db_table_prefix`` is per-module-suffixed (``snap_or``) so
    other Oracle-tier tests running in parallel don't collide on the
    v-overlay schema namespace.
    """
    return make_test_config(
        dialect=Dialect.ORACLE,
        demo_database_url=snapshotter_oracle_container_url,
        db_table_prefix="snap_or",
    )


@pytest.fixture(scope="module")
def seeded_v_overlay(
    oracle_cfg: Any,
    spec_instance: L2Instance,
) -> Any:
    """Build the base schema + v-overlay + insert two probe rows.

    Module-scoped so each test method reuses the same shape; verbs
    that mutate the v-overlay (the take → mutate → restore round-
    trip) restore back to this baseline at the end so the next test
    sees the same starting state.

    Returns the same ``Config`` — fixture is the side-effecting
    setup; ``oracle_cfg`` is the carrier.
    """
    base_prefix = oracle_cfg.db_table_prefix
    v_prefix = v_overlay_prefix(base_prefix)

    def _do_setup() -> None:
        conn = connect_demo_db(oracle_cfg)
        try:
            cur = conn.cursor()
            try:
                # Tear down any prior state from a half-finished run.
                # The schema-drop emitter is idempotent; we just run
                # both (base + v) so a partial test exit leaves the
                # next module run with a clean slate.
                drop_sql = drop_v_overlay_sql(
                    spec_instance, base_prefix=base_prefix,
                    dialect=Dialect.ORACLE,
                )
                # Wrap in a swallow so the FIRST run (nothing exists)
                # doesn't burn 22 ORA-00942s.
                try:
                    execute_script(cur, drop_sql, dialect=Dialect.ORACLE)
                except Exception:  # noqa: BLE001 — first-time setup
                    pass
                # Same for the base schema. The schema emit always
                # ships DROPs first so re-runs are clean — but only if
                # the schema EXISTS to drop. First-time on this
                # container, the DROP fires ORA-00942.
                try:
                    base_drop_sql = emit_schema(
                        spec_instance, prefix=base_prefix,
                        dialect=Dialect.ORACLE,
                    )
                    # Emit_schema includes the drops + creates as one
                    # script; one pass = clean state.
                    execute_script(cur, base_drop_sql, dialect=Dialect.ORACLE)
                except Exception:
                    # Re-raise the SECOND-pass error — first pass set
                    # up the schema cleanly; if THIS one fails, that's
                    # a real bug in emit_schema, not first-run noise.
                    conn.rollback()
                    raise
                # Now the base schema is fresh — build the v-overlay
                # against it.
                v_create_sql = create_v_overlay_sql(
                    spec_instance, base_prefix=base_prefix,
                    dialect=Dialect.ORACLE,
                )
                execute_script(cur, v_create_sql, dialect=Dialect.ORACLE)
                conn.commit()

                # Seed two probe rows into each base table. The
                # snapshotter round-trip is the load-bearing claim;
                # the row contents are just markers we can SELECT
                # against to verify byte-equivalence.
                _seed_probe_rows(cur, v_prefix)
                conn.commit()

                # Matview refresh so the v-matviews have rows for
                # DBMS_MVIEW.REFRESH to operate on. Without this the
                # FIRST snapshotter restore() would land on
                # never-refreshed matviews (technically still works,
                # but better to anchor against the same shape the
                # production session_start produces).
                mv_refresh_sql = refresh_v_overlay_matviews_sql(
                    spec_instance, base_prefix=base_prefix,
                    dialect=Dialect.ORACLE,
                )
                # The v-overlay matview refresh SQL is one PL/SQL
                # block per matview, semicolon-separated; execute_script
                # handles the split for Oracle.
                execute_script(cur, mv_refresh_sql, dialect=Dialect.ORACLE)
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    _do_setup()
    return oracle_cfg


def _seed_probe_rows(cur: Any, v_prefix: str) -> None:
    """Insert two transactions + two daily_balances + two config_kv
    rows into the v-overlay. Just enough to give the round-trip
    something to compare."""
    # transactions — minimal viable shape per the v6 schema.
    cur.execute(
        f"INSERT INTO {v_prefix}_transactions "
        "(id, account_id, account_role, account_scope, account_type, "
        "amount_money, amount_direction, status, posting, transfer_id, "
        "rail_name, origin, metadata) "
        "VALUES ('tx-001', 'acct-A', 'control', 'systemic', 'gl_control', "
        "100, 'in', 'posted', TIMESTAMP '2026-01-15 12:00:00', "
        "'xfer-1', 'core', 'demo', '{}')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_transactions "
        "(id, account_id, account_role, account_scope, account_type, "
        "amount_money, amount_direction, status, posting, transfer_id, "
        "rail_name, origin, metadata) "
        "VALUES ('tx-002', 'acct-B', 'sub', 'tenant', 'dda', "
        "200, 'out', 'posted', TIMESTAMP '2026-01-16 12:00:00', "
        "'xfer-2', 'core', 'demo', '{}')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_daily_balances "
        "(account_id, account_role, account_scope, account_type, "
        "business_day, balance, metadata) "
        "VALUES ('acct-A', 'control', 'systemic', 'gl_control', "
        "DATE '2026-01-15', 100, '{}')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_daily_balances "
        "(account_id, account_role, account_scope, account_type, "
        "business_day, balance, metadata) "
        "VALUES ('acct-B', 'sub', 'tenant', 'dda', "
        "DATE '2026-01-16', -200, '{}')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_config_kv (key, value) VALUES "
        "('snapshotter_test_key', 'baseline_v1')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_config_kv (key, value) VALUES "
        "('snapshotter_test_key2', 'baseline_v2')",
    )


def _select_probe_state(cfg: Any) -> dict[str, list[tuple[Any, ...]]]:
    """SELECT-order the v-overlay base tables; returns per-table
    sorted row tuples so the round-trip equivalence assertion is
    deterministic regardless of physical row order."""
    base_prefix = cfg.db_table_prefix
    v_prefix = v_overlay_prefix(base_prefix)
    state: dict[str, list[tuple[Any, ...]]] = {}
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            for tbl in _V_OVERLAY_BASE_TABLES:
                if tbl == "transactions":
                    cur.execute(
                        f"SELECT id, account_id, amount_money "
                        f"FROM {v_prefix}_{tbl} ORDER BY id",
                    )
                elif tbl == "daily_balances":
                    cur.execute(
                        f"SELECT account_id, business_day, balance "
                        f"FROM {v_prefix}_{tbl} ORDER BY account_id, business_day",
                    )
                else:  # config_kv
                    cur.execute(
                        f"SELECT key, value FROM {v_prefix}_{tbl} ORDER BY key",
                    )
                state[tbl] = [tuple(row) for row in cur.fetchall()]
        finally:
            cur.close()
    finally:
        conn.close()
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_round_trip_take_mutate_restore(
    seeded_v_overlay: Any,
) -> None:
    """take → DELETE rows → restore → SELECTs match the pre-take state.

    The load-bearing claim of the snapshotter contract: a restore is
    byte-equivalent (per ORDER BY anchored SELECT) to the state at
    take-time, regardless of any DML between them.
    """
    cfg = seeded_v_overlay
    base_prefix = cfg.db_table_prefix
    v_prefix = v_overlay_prefix(base_prefix)
    baseline = _select_probe_state(cfg)

    async def _round_trip() -> None:
        pool = await make_connection_pool(cfg, max_size=2)
        snap = OracleGoldenMirrorSnapshotter(
            pool=pool, base_prefix=base_prefix,
        )
        try:
            await snap.take("rt1")
            # Mutate the v-overlay so a no-op restore would visibly
            # fail. DELETE every row from each base table.
            async with pool.acquire() as conn:
                for tbl in _V_OVERLAY_BASE_TABLES:
                    await cast(Any, conn).execute(
                        f"DELETE FROM {v_prefix}_{tbl}",
                    )
                await cast(Any, conn).execute("COMMIT")
            # Sanity: the live SELECTs should return zero rows now —
            # so any post-restore non-zero count is the restore at work.
            await snap.restore("rt1")
        finally:
            await snap.drop("rt1")
            await snap.aclose()
            await pool.close()

    asyncio.run(_round_trip())
    restored = _select_probe_state(cfg)
    assert restored == baseline, (
        f"restore byte-mismatch: baseline={baseline!r} vs restored={restored!r}"
    )


def test_multi_snapshot_independence(
    seeded_v_overlay: Any,
) -> None:
    """Two snapshots taken at different states restore to their own
    states, not to each other's. Pins the per-name isolation.

    Sequence:
      take("a") — snap1 with rows {tx-001, tx-002}
      DELETE tx-001 — mutated state
      take("b") — snap2 with rows {tx-002}
      restore("a") — assert rows are {tx-001, tx-002}
      restore("b") — assert rows are {tx-002}
    """
    cfg = seeded_v_overlay
    base_prefix = cfg.db_table_prefix
    v_prefix = v_overlay_prefix(base_prefix)
    baseline = _select_probe_state(cfg)
    assert len(baseline["transactions"]) == 2, (
        "fixture-state precondition: 2 transaction rows"
    )

    async def _multi_snap() -> dict[str, dict[str, list[tuple[Any, ...]]]]:
        pool = await make_connection_pool(cfg, max_size=2)
        snap = OracleGoldenMirrorSnapshotter(
            pool=pool, base_prefix=base_prefix,
        )
        captured: dict[str, dict[str, list[tuple[Any, ...]]]] = {}
        try:
            await snap.take("a")
            # Mutate — delete the first transaction so snap "b" has
            # one fewer row.
            async with pool.acquire() as conn:
                await cast(Any, conn).execute(
                    f"DELETE FROM {v_prefix}_transactions "
                    "WHERE id = 'tx-001'",
                )
                await cast(Any, conn).execute("COMMIT")
            await snap.take("b")
            await snap.restore("a")
            captured["after_restore_a"] = _select_probe_state(cfg)
            await snap.restore("b")
            captured["after_restore_b"] = _select_probe_state(cfg)
            # Final restore back to baseline so the next test sees
            # the fixture-seeded shape.
            await snap.restore("a")
        finally:
            await snap.drop("a")
            await snap.drop("b")
            await snap.aclose()
            await pool.close()
        return captured

    captured = asyncio.run(_multi_snap())
    # snap "a" is the seeded baseline.
    assert captured["after_restore_a"] == baseline, (
        f"snap_a should match baseline: {captured['after_restore_a']!r} "
        f"vs {baseline!r}"
    )
    # snap "b" has only the second transaction.
    assert len(captured["after_restore_b"]["transactions"]) == 1
    assert captured["after_restore_b"]["transactions"][0][0] == "tx-002"


def test_restore_latency_under_sla(
    seeded_v_overlay: Any,
) -> None:
    """Operator-accepted ~2500ms restore budget — pin a 5s upper
    bound so a regression to the cumulative-walk (~30s/iter) trips
    the test before it lands in CI.

    5s is 2× the locked target — wide enough to absorb the noise of
    a shared CI Oracle container under load, tight enough to catch
    the order-of-magnitude regression we actually care about (the
    cumulative-walk was ~30s per session_start; even a 5s ceiling
    is 6× faster).
    """
    cfg = seeded_v_overlay
    base_prefix = cfg.db_table_prefix

    async def _measure() -> float:
        pool = await make_connection_pool(cfg, max_size=2)
        snap = OracleGoldenMirrorSnapshotter(
            pool=pool, base_prefix=base_prefix,
        )
        try:
            await snap.take("sla")
            # Warm-up restore — Oracle's first DBMS_MVIEW.REFRESH on
            # a fresh session can pay parse overhead the second one
            # doesn't. Run once untimed; measure the second.
            await snap.restore("sla")
            start = time.perf_counter()
            await snap.restore("sla")
            elapsed = time.perf_counter() - start
        finally:
            await snap.drop("sla")
            await snap.aclose()
            await pool.close()
        return elapsed

    elapsed = asyncio.run(_measure())
    # 5s upper bound; the operator-accepted target is ~2.5s. If the
    # tiny seed (4 base rows + 22 matviews) takes >5s we have a real
    # regression in either DBMS_MVIEW.REFRESH or direct-path INSERT
    # paths.
    assert elapsed < 5.0, (
        f"restore took {elapsed:.2f}s > 5s SLA; "
        "investigate matview refresh or direct-path INSERT fallback."
    )


def test_restore_without_take_raises(
    seeded_v_overlay: Any,
) -> None:
    """Calling restore() before any take() surfaces a typed error
    rather than ORA-00942 confusion."""
    cfg = seeded_v_overlay
    base_prefix = cfg.db_table_prefix

    async def _no_take() -> None:
        pool = await make_connection_pool(cfg, max_size=2)
        snap = OracleGoldenMirrorSnapshotter(
            pool=pool, base_prefix=base_prefix,
        )
        try:
            with pytest.raises(Exception) as excinfo:
                await snap.restore("nope")
            # The RAISE_APPLICATION_ERROR(-20001) surfaces as a
            # DatabaseError with that error code embedded in the
            # message. We don't pin the exception class (oracledb's
            # internal exception hierarchy isn't stable across
            # versions), just that the actionable hint reaches the
            # caller.
            assert "Snapshot nope not found" in str(excinfo.value) or (
                "ORA-20001" in str(excinfo.value)
            ), f"unexpected error: {excinfo.value!r}"
        finally:
            await snap.aclose()
            await pool.close()

    asyncio.run(_no_take())


def test_aclose_sweeps_undropped_snapshots(
    seeded_v_overlay: Any,
) -> None:
    """aclose() drops any leftover gold tables — pin via USER_TABLES
    count before + after.

    Sequence:
      take("a") — creates 3 gold tables
      take("b") — creates 3 more
      drop("b") — removes 3
      aclose() — sweeps the remaining 3 from "a"
      USER_TABLES post-aclose: zero gold tables under our prefix.
    """
    cfg = seeded_v_overlay
    base_prefix = cfg.db_table_prefix
    upper_prefix = v_overlay_prefix(base_prefix).upper()

    async def _sweep_check() -> tuple[int, int]:
        pool = await make_connection_pool(cfg, max_size=2)
        snap = OracleGoldenMirrorSnapshotter(
            pool=pool, base_prefix=base_prefix,
        )
        try:
            await snap.take("sweepa")
            await snap.take("sweepb")
            await snap.drop("sweepb")
            # Pre-aclose count — should be 3 (gold tables for "sweepa").
            pre = await _count_gold_tables(pool, upper_prefix)
            await snap.aclose()
            # Post-aclose — should be 0.
            post = await _count_gold_tables(pool, upper_prefix)
        finally:
            await pool.close()
        return pre, post

    pre, post = asyncio.run(_sweep_check())
    assert pre == len(_V_OVERLAY_BASE_TABLES), (
        f"expected {len(_V_OVERLAY_BASE_TABLES)} gold tables before aclose; got {pre}"
    )
    assert post == 0, f"aclose left {post} gold tables behind"


async def _count_gold_tables(
    pool: AsyncConnectionPool, upper_prefix: str,
) -> int:
    """USER_TABLES probe for our test's gold-table residue."""
    pattern = f"{upper_prefix}\\_%\\_GOLD\\_%"
    async with pool.acquire() as conn:
        cur = await cast(Any, conn).execute(
            (
                "SELECT COUNT(*) FROM USER_TABLES "
                "WHERE table_name LIKE :pattern ESCAPE '\\'"
            ),
            {"pattern": pattern},
        )
        rows = await cur.fetchall()
        return int(rows[0][0])


# ---------------------------------------------------------------------------
# Snapshot-name validation (sync — no DB needed)
# ---------------------------------------------------------------------------


class TestSnapshotNameValidation:
    """Pin the validator's exhaustive rejection surface so a slip
    into the SQL composition layer surfaces here, not as a runtime
    ORA-00911."""

    def test_empty_name_rejected(self) -> None:
        from recon_gen.common.snapshotter import _validate_snapshot_name
        with pytest.raises(ValueError, match="non-empty"):
            _validate_snapshot_name("")

    def test_too_long_rejected(self) -> None:
        from recon_gen.common.snapshotter import _validate_snapshot_name
        with pytest.raises(ValueError, match="too long"):
            _validate_snapshot_name("x" * 33)

    @pytest.mark.parametrize("bad", ["a-b", "a b", "a;DROP", "a$b", "a#b"])
    def test_non_identifier_chars_rejected(self, bad: str) -> None:
        from recon_gen.common.snapshotter import _validate_snapshot_name
        with pytest.raises(ValueError, match=r"\[A-Za-z0-9_\]\+"):
            _validate_snapshot_name(bad)

    @pytest.mark.parametrize("ok", ["a", "snap_1", "MyName", "a_b_c_123"])
    def test_valid_names_accepted(self, ok: str) -> None:
        from recon_gen.common.snapshotter import _validate_snapshot_name
        # Must not raise.
        _validate_snapshot_name(ok)
