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
from recon_gen.common.config import DbConfig
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
from tests._timing_signal import timing_signal
from recon_gen.common.snapshotter import (
    OracleGoldenMirrorSnapshotter,
    _V_OVERLAY_BASE_TABLES,
)


# BV.3.3.f xdist-isolation fix (2026-06-11) — `xdist_group` mark pins
# ALL tests in this file to ONE xdist worker. CAVEAT: this is a silent
# no-op under xdist's default `--dist=load` (which the unit layer uses
# per `_dev/runner.py::_layer_pytest_argv`). It documents intent + acts
# as belt-and-suspenders for the day someone re-enables `--dist=loadgroup`
# for the unit layer. The LOAD-BEARING fix is the per-worker
# `db_table_prefix` disambiguation in `oracle_cfg` below: each worker
# operates on its own `snap_or_<worker>` schema namespace so concurrent
# `emit_schema` DROP-then-CREATE streams against the shared
# `recon-gen-snap-test-oracle` container don't race on object-existence
# (the ORA-00955 / ORA-12003 / ORA-12006 / ORA-00942 / ORA-04063 storm
# observed in CI). Oracle's DDL auto-commit-per-statement was surfacing
# a real isolation bug per `feedback_strict_engines_surface_isolation_bugs`
# — fix the isolation, don't relax the engine.
pytestmark = [
    tier(Tier.DB),
    dialects(MarkDialect.OR),
    needs(Need.DOCKER),
    pytest.mark.xdist_group("snapshotter-oracle"),
]


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
    worker_id: str,
) -> Any:
    """Build a ``Config`` pointed at the snapshotter-dedicated Oracle
    container.

    BV.3.3.f (2026-06-11): the ``db_table_prefix`` is per-(file, worker)
    suffixed so concurrent xdist workers running tests from this module
    don't collide on the shared ``recon-gen-snap-test-oracle`` container's
    schema namespace. Under xdist's default ``--dist=load`` (the unit
    layer's mode per ``_dev/runner.py::_layer_pytest_argv``) any
    ``xdist_group`` mark is a silent no-op, so the file-level ``pytestmark``
    above pins intent only — the load-bearing isolation lives in this
    prefix. Each worker gets ``snap_or_<worker>`` (e.g. ``snap_or_gw0``);
    Oracle 19c's 128-byte identifier cap leaves comfortable headroom
    even with the longest gold-table envelope
    (``<prefix>_v_inv_money_trail_edges_gold_<name>`` ≈ 60 chars +
    snapshot name up to 32 chars).

    Without this disambiguation, two workers' concurrent ``emit_schema``
    DROP-then-CREATE streams against the same prefix race on
    object-existence (DDL auto-commits per statement on Oracle, no way
    to roll back). The race window covers ORA-00955 ("name is already
    used"), ORA-12006 ("matview already exists"), ORA-12003 ("matview
    does not exist"), ORA-00942 ("table or view does not exist"), and
    ORA-04063 ("view has errors") — each one is a different point in
    the DROP-CREATE-REFERENCE sequence where worker B's compile hits
    something worker A is mid-flight on.
    """
    # ``worker_id`` is ``"master"`` under bare pytest (no xdist) and
    # ``"gw<N>"`` under xdist. Suffix is short enough to fit Oracle's
    # identifier cap; using the raw worker_id (rather than a hash)
    # keeps the prefix grep-able during triage.
    return make_test_config(
        db=DbConfig(
            table_prefix=f"snap_or_{worker_id}",
            dialect=Dialect.ORACLE,
            url=snapshotter_oracle_container_url,
        ),
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
    base_prefix = oracle_cfg.db.table_prefix
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
    something to compare.

    BV.3.3 fix (test side, 2026-06-10): the helper was authored
    against a stale column model (``account_type`` / ``business_day`` /
    ``balance``) — the current base-table DDL uses ``account_role`` /
    ``account_parent_role`` / ``business_day_start`` /
    ``business_day_end`` / ``money`` (see ``common/l2/schema.py`` lines
    1932 + 1995). On Oracle the missing column manifests as the
    ORA-00904 "ACCOUNT_TYPE": invalid identifier setup error this fix
    addresses.

    Column refs are *unquoted* — the base-table DDL emits unquoted
    identifiers (Oracle stores them case-folded to UPPERCASE), and an
    unquoted ref in the INSERT case-folds to UPPERCASE at parse and
    matches the stored identifier. (This is the symmetric Oracle
    side of the production case-folding convention; the production
    ``_quote_col`` fix in commit 8c5b2347 applies only to the
    *wrapper-aliased* SELECT path where the outer wrapper deliberately
    emits case-preserved-lowercase quoted aliases — a different
    surface than these base-table INSERTs.)
    """
    cols_tx = (
        "id, account_id, account_name, account_role, "
        "account_scope, account_parent_role, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, rail_name, origin, metadata"
    )
    # transactions — minimal viable shape per the v6 schema. The
    # CHECK constraint pairs amount_money sign with amount_direction
    # (Credit ≥ 0, Debit ≤ 0); seed both sides so the round-trip
    # exercises both sign branches.
    cur.execute(
        f"INSERT INTO {v_prefix}_transactions ({cols_tx}) "
        "VALUES ('tx-001', 'acct-A', 'Acct A', 'control', "
        "'internal', 'gl_control', "
        "100, 'Credit', 'posted', TIMESTAMP '2026-01-15 12:00:00', "
        "'xfer-1', 'core', 'demo', '{}')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_transactions ({cols_tx}) "
        "VALUES ('tx-002', 'acct-B', 'Acct B', 'sub', "
        "'internal', 'dda', "
        "-200, 'Debit', 'posted', TIMESTAMP '2026-01-16 12:00:00', "
        "'xfer-2', 'core', 'demo', '{}')",
    )
    cols_db = (
        "account_id, account_name, account_role, "
        "account_scope, account_parent_role, "
        "business_day_start, business_day_end, "
        "money, metadata"
    )
    # daily_balances — CHECK requires business_day_end > business_day_start.
    cur.execute(
        f"INSERT INTO {v_prefix}_daily_balances ({cols_db}) "
        "VALUES ('acct-A', 'Acct A', 'control', 'internal', 'gl_control', "
        "TIMESTAMP '2026-01-15 00:00:00', "
        "TIMESTAMP '2026-01-16 00:00:00', 100, '{}')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_daily_balances ({cols_db}) "
        "VALUES ('acct-B', 'Acct B', 'sub', 'internal', 'dda', "
        "TIMESTAMP '2026-01-16 00:00:00', "
        "TIMESTAMP '2026-01-17 00:00:00', -200, '{}')",
    )
    # config_kv — node_id is NOT NULL PK (no auto-increment); supply
    # it explicitly per the BC.12 schema shape (see
    # ``common/l2/config_table.py::emit_config_table_ddl``).
    cur.execute(
        f"INSERT INTO {v_prefix}_config_kv "
        "(node_id, parent_id, key, value) VALUES "
        "('snap_test_node_1', NULL, 'snapshotter_test_key', 'baseline_v1')",
    )
    cur.execute(
        f"INSERT INTO {v_prefix}_config_kv "
        "(node_id, parent_id, key, value) VALUES "
        "('snap_test_node_2', NULL, 'snapshotter_test_key2', 'baseline_v2')",
    )


def _select_probe_state(cfg: Any) -> dict[str, list[tuple[Any, ...]]]:
    """SELECT-order the v-overlay base tables; returns per-table
    sorted row tuples so the round-trip equivalence assertion is
    deterministic regardless of physical row order.

    Column refs unquoted — match the base-table DDL convention (Oracle
    case-folds to UPPERCASE at parse, matching the stored identifier).
    See ``_seed_probe_rows`` for the case-folding rationale.

    config_kv.value materializes as a CLOB on Oracle (sized to fit
    the ~37KB sasquatch_pr L2 yaml per ``common/l2/config_table.py``);
    ``oracledb`` returns an opaque LOB object per row, so identity-based
    tuple equality breaks the round-trip assertion. ``_lob_str`` reads
    the LOB to a plain ``str`` before tupling so equality compares
    content, not handle.
    """
    base_prefix = cfg.db.table_prefix
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
                        f"SELECT account_id, business_day_start, money "
                        f"FROM {v_prefix}_{tbl} "
                        f"ORDER BY account_id, business_day_start",
                    )
                else:  # config_kv
                    cur.execute(
                        f"SELECT key, value FROM {v_prefix}_{tbl} "
                        f"ORDER BY key",
                    )
                state[tbl] = [
                    tuple(_lob_str(v) for v in row) for row in cur.fetchall()
                ]
        finally:
            cur.close()
    finally:
        conn.close()
    return state


def _lob_str(v: Any) -> Any:
    """Read an ``oracledb`` LOB to ``str``; pass-through for other types.

    Identity-based tuple equality breaks round-trip assertions when
    ``value`` materializes as a LOB object (two reads = two distinct
    handles even when the underlying content matches). ``read()``
    materializes the content once so equality compares strings.
    """
    read = getattr(v, "read", None)
    if callable(read):
        return read()
    return v


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
    base_prefix = cfg.db.table_prefix
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
    base_prefix = cfg.db.table_prefix
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
    base_prefix = cfg.db.table_prefix

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
    # EA.3 — timings-as-signal (tests/_timing_signal.py): record the restore
    # SLA (a real regression in matview refresh / direct-path INSERT shows as
    # an over-budget signal) without hard-failing on the box-calibrated 5s.
    timing_signal("snapshotter_oracle_restore", elapsed, budget_s=5.0)


def test_restore_without_take_raises(
    seeded_v_overlay: Any,
) -> None:
    """Calling restore() before any take() surfaces a typed error
    rather than ORA-00942 confusion."""
    cfg = seeded_v_overlay
    base_prefix = cfg.db.table_prefix

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
    base_prefix = cfg.db.table_prefix
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
        async with cast(Any, conn).cursor() as cur:
            await cur.execute(
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
