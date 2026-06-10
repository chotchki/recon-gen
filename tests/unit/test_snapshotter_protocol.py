"""BV.3.3 snapshot foundation — Protocol + factory unit tests.

Asserts the Snapshotter surface (take / restore / drop / aclose),
the factory dispatches by ``cfg.dialect`` (every dialect currently
returns the NotImplemented stub until phase 2 lands), and the stub
raises ``NotImplementedError`` on each non-aclose verb with an
actionable message.

Test-rig only — lives under tests/unit/ because the foundation cell
has no DB round-trip; the per-dialect impls (phase 2) will integration-
test against live containers in tests/e2e/db/.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from recon_gen.common.l2 import L2Instance
from recon_gen.common.sql import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._snapshotter import (
    DuckDBFileSnapshotter,
    NotImplementedSnapshotter,
    Snapshotter,
    make_snapshotter,
)


# -- shared scaffolding ----------------------------------------------------


def _empty_l2() -> L2Instance:
    """Bare L2Instance — the foundation factory doesn't read any fields,
    so a content-free instance suffices (matches BV.6's pattern)."""
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


class _DummyPool:
    """Stand-in for ``AsyncConnectionPool``.

    The foundation factory only holds the pool reference for the future
    dialect impls — it never calls ``acquire`` / ``close`` — so a no-op
    object suffices. Real wiring through ``make_connection_pool`` lives
    in the phase 2 integration tests.
    """

    def acquire(self) -> None:  # pragma: no cover — never invoked by the stub
        raise AssertionError("foundation stub must not touch the pool")

    async def close(self) -> None:  # pragma: no cover — same
        raise AssertionError("foundation stub must not touch the pool")


# -- Protocol surface ------------------------------------------------------


class TestSnapshotterProtocol:
    """The four verbs are the contract; phase 2 impls structurally
    conform without explicit inheritance."""

    def test_protocol_has_exactly_four_verbs(self) -> None:
        # Catch a future drift where someone adds a verb to the stub
        # but forgets to declare it on the Protocol (or vice versa) —
        # both would compile but fail at duck-type time.
        expected = {"take", "restore", "drop", "aclose"}
        actual = {
            name
            for name in vars(Snapshotter)
            if not name.startswith("_")
        }
        assert actual == expected, (
            f"Snapshotter verbs drifted: expected {expected}, got {actual}"
        )

    def test_stub_structurally_conforms(self) -> None:
        # The duck-type check the test harness relies on — every
        # Protocol verb is present on the stub. We don't use
        # ``isinstance(stub, Snapshotter)`` because Snapshotter isn't
        # ``@runtime_checkable`` (matches AsyncConnectionPool's pattern;
        # see tests/unit/test_common_db.py:408).
        stub = NotImplementedSnapshotter()
        for verb in ("take", "restore", "drop", "aclose"):
            assert hasattr(stub, verb), f"stub missing {verb}"
            assert callable(getattr(stub, verb)), f"stub.{verb} not callable"


# -- NotImplementedSnapshotter behaviour -----------------------------------


class TestNotImplementedSnapshotter:
    """The stub is the foundation's default impl — every non-aclose verb
    raises with an actionable message so a test that hits it gets a
    clear "phase 2 not landed yet" error instead of a silent no-op."""

    def test_take_raises(self) -> None:
        stub = NotImplementedSnapshotter()
        with pytest.raises(NotImplementedError) as excinfo:
            asyncio.run(stub.take("snap1"))
        # Message names the foundation phase so a CI failure points the
        # operator at the right plan-task — not just an opaque tracebck.
        assert "BV.3.3 snapshot foundation" in str(excinfo.value)

    def test_restore_raises(self) -> None:
        stub = NotImplementedSnapshotter()
        with pytest.raises(NotImplementedError):
            asyncio.run(stub.restore("snap1"))

    def test_drop_raises(self) -> None:
        stub = NotImplementedSnapshotter()
        with pytest.raises(NotImplementedError):
            asyncio.run(stub.drop("snap1"))

    def test_aclose_is_a_no_op(self) -> None:
        # The single safe verb — test ``finally`` blocks can call it
        # unconditionally without try/except even before phase 2 lands.
        stub = NotImplementedSnapshotter()
        # Must not raise.
        asyncio.run(stub.aclose())


# -- Factory dispatch ------------------------------------------------------


class TestMakeSnapshotter:
    """The factory shape mirrors ``make_connection_pool`` — async, dialect
    dispatched, raises on unknown dialect. Every dialect arm currently
    returns the stub; phase 2 swaps in real impls cell-by-cell."""

    def test_pg_arm_returns_real_impl(self) -> None:
        """BV.3.3 — PG arm dispatches to ``PostgresSchemaSnapshotter``.

        The factory holds the pool ref but never invokes ``acquire()``
        during construction (the impl's ``__init__`` only reads
        ``base_prefix`` + ``l2_instance``), so a ``_DummyPool`` is
        sufficient for the dispatch contract. The PG round-trip
        contract is covered by ``test_snapshotter_pg.py`` against the
        shared PG container fixture.
        """
        from tests.e2e._snapshotter import PostgresSchemaSnapshotter

        cfg = make_test_config(dialect=Dialect.POSTGRES)
        snap = asyncio.run(
            make_snapshotter(
                cfg,
                _DummyPool(),  # type: ignore[arg-type]: dummy structural pool — factory holds ref only
                base_prefix=cfg.db_table_prefix,
                l2_instance=_empty_l2(),
            ),
        )
        assert isinstance(snap, PostgresSchemaSnapshotter)

    def test_oracle_arm_returns_real_impl(self) -> None:
        """BV.3.3 — Oracle arm dispatches to
        ``OracleGoldenMirrorSnapshotter``.

        The factory holds the pool ref but never invokes ``acquire()``
        during construction (the impl's ``__init__`` only computes the
        v-prefix), so a ``_DummyPool`` is sufficient for the dispatch
        contract. The Oracle round-trip contract is covered by
        ``test_snapshotter_oracle.py`` against the shared Oracle
        container fixture.
        """
        from tests.e2e._snapshotter import OracleGoldenMirrorSnapshotter

        cfg = make_test_config(dialect=Dialect.ORACLE)
        snap = asyncio.run(
            make_snapshotter(
                cfg,
                _DummyPool(),  # type: ignore[arg-type]: dummy structural pool — factory holds ref only
                base_prefix=cfg.db_table_prefix,
                l2_instance=_empty_l2(),
            ),
        )
        assert isinstance(snap, OracleGoldenMirrorSnapshotter)

    def test_duckdb_arm_returns_real_impl(self, tmp_path: Path) -> None:
        """BV.3.3 (this cell) — DuckDB arm wired to DuckDBFileSnapshotter.

        Uses a real ``_AsyncDuckdbPool`` against a tmp_path-backed file
        so the factory's ``duckdb_path`` parse + path construction
        round-trip end-to-end. We don't ``take()``/``restore()`` here —
        that's covered in test_snapshotter_duckdb.py; this is just the
        dispatch contract.
        """
        # Async lifecycle bundled into one helper so the test stays
        # synchronous-looking and matches the file's pattern.
        import asyncio as _asyncio
        from recon_gen.common.db import make_connection_pool, make_demo_database_url

        db_file = tmp_path / "dispatch.duckdb"
        cfg = make_test_config(
            dialect=Dialect.DUCKDB,
            demo_database_url=make_demo_database_url(Dialect.DUCKDB, db_file),
        )

        async def _run() -> None:
            pool = await make_connection_pool(cfg)
            try:
                snap = await make_snapshotter(
                    cfg,
                    pool,
                    base_prefix=cfg.db_table_prefix,
                    l2_instance=_empty_l2(),
                )
                assert isinstance(snap, DuckDBFileSnapshotter)
                await snap.aclose()
            finally:
                await pool.close()

        _asyncio.run(_run())

    def test_unknown_dialect_raises(self) -> None:
        # Construct a Config then mutate dialect to a non-enum value
        # through __dict__ — emulates the way ``make_connection_pool``
        # surfaces a corrupt cfg.yaml (same loud-fail philosophy).
        cfg = make_test_config(dialect=Dialect.POSTGRES)
        object.__setattr__(cfg, "dialect", "mysql")  # type: ignore[arg-type]: intentional bad value to trigger the guard
        with pytest.raises(ValueError, match="Unknown dialect"):
            asyncio.run(
                make_snapshotter(
                    cfg,
                    _DummyPool(),  # type: ignore[arg-type]: same dummy carrier
                    base_prefix=cfg.db_table_prefix,
                    l2_instance=_empty_l2(),
                ),
            )

    def test_factory_is_async(self) -> None:
        # Belt-and-braces: phase 2's PG / Oracle impls run DDL during
        # construction (golden-schema CTAS etc), so the factory must
        # stay awaitable. A regression to a sync return would break
        # those impls' construction shape.
        import inspect
        assert inspect.iscoroutinefunction(make_snapshotter)
