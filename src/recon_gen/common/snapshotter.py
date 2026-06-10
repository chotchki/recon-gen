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

Moved to ``src/recon_gen/common/`` (was ``tests/e2e/_snapshotter.py``)
because the Studio HTTP route on the runtime server imports it. The
test rig still owns the FIXTURE wiring (per-cell lifecycle, harness
plumbing); the snapshot MECHANISM is now shared runtime infrastructure
and can be reused for an operator-facing "restore baseline" button if
useful later.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from recon_gen.common.config import Config
from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.sql import Dialect

if TYPE_CHECKING:
    # Forward-only — avoids a runtime circular import via
    # ``recon_gen.common.l2.primitives`` (which transitively pulls a
    # large cone). The factory takes the L2Instance as a structural
    # carrier for per-instance table prefixes; we only need its type
    # signature at type-check time.
    from recon_gen.common.l2.primitives import L2Instance


__all__ = [
    "NotImplementedSnapshotter",
    "Snapshotter",
    "make_snapshotter",
]


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
    # Hold the wiring deps on a stub instance variable so per-dialect
    # impls have somewhere obvious to read them. The stub itself
    # discards them — that's fine; the test harness only inspects the
    # Protocol surface.
    del pool, base_prefix, l2_instance
    return NotImplementedSnapshotter()
