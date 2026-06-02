"""CB.7 refactor (2026-06-02) — isolation primitives for e2e fixtures.

Public API:

- `isolated_cfg` fixture — provider-marked, scope=module, dialect-aware.
  Writer fixtures request this in place of `cfg`; the framework
  provides a per-(module, worker) isolated copy by default. Files in
  a cross-tier agreement chain declare `@isolation_scope(...)` at
  module level; the fixture uses the scope value as the suffix instead
  so all tiers reading the same chain see the same prefix.

- `_isolate_cfg(cfg, suffix, tmp_path_factory)` — pure function. Apply
  isolation transform to a cfg. Tests that parametrize over dialect
  (e.g. `test_audit_invariants_direct.py`) call this directly because
  pytest's fixture caching can't compose with parametrize indirection.

The fixture and helpers live in `tests/e2e/_isolation.py` so all tiers
(`tests/e2e/db/`, `tests/e2e/app2/`, `tests/e2e/qs_browser/`) can
import + re-export. Putting it in `tests/e2e/conftest.py` directly
would also work but that file is already large; module separation
keeps the architectural concept discoverable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from typing import TYPE_CHECKING, Any, Iterator

import pytest

if TYPE_CHECKING:
    from recon_gen.common.config import Config


def _isolated_cfg_key(
    request: pytest.FixtureRequest, cfg: "Config",
) -> str:
    """Build a stable, short isolation suffix from
    (nodeid, l2_instance, dialect, worker_id). Hash inputs locked by
    operator 2026-06-02.

    SHA-256 truncated to 6 hex chars. Deterministic — same inputs
    always produce the same suffix. Short — fits Oracle's 30-char
    identifier cap with plenty of room for the base prefix.

    Counter-fallback documented in `tests/e2e/db/conftest.py` history:
    if the hash becomes opaque for triage, swap in a
    `dict[<key-tuple>, int]` registry emitting `a01`/`a02`/...
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    request_any: Any = request  # typing-smell: ignore[explicit-any]: pytest FixtureRequest protocol attrs are dynamic
    nodeid: str = str(
        getattr(request_any.node, "nodeid", None)
        or request_any.module.__name__,
    )
    l2 = cfg.default_l2_instance or "no-l2"
    dialect = cfg.dialect.value
    key = f"{nodeid}|{l2}|{dialect}|{worker_id}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]


def _isolate_cfg(
    cfg: "Config",
    *,
    suffix: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> "Config":
    """Return a per-suffix isolated cfg copy. Pure function — no
    fixture coupling. Dialect-aware.

    - DuckDB: clone `demo_database_url` to a fresh `.duckdb` file
      under `tmp_path_factory`.
    - PG / Oracle: append `_<suffix>` to `db_table_prefix` and
      `-<suffix>` to `deployment_name`.
    """
    from recon_gen.common.sql import Dialect

    if cfg.dialect is Dialect.DUCKDB:
        worker_db_dir = tmp_path_factory.mktemp(f"iso_{suffix}")
        from recon_gen.common.db import make_demo_database_url
        worker_url = make_demo_database_url(
            Dialect.DUCKDB, worker_db_dir / "demo.duckdb",
        )
        return dataclasses.replace(cfg, demo_database_url=worker_url)
    return dataclasses.replace(
        cfg,
        db_table_prefix=f"{cfg.db_table_prefix}_{suffix}",
        deployment_name=f"{cfg.deployment_name}-{suffix}",
    )


@pytest.fixture(scope="module")
def isolated_cfg(
    request: pytest.FixtureRequest,
    cfg: "Config",
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator["Config"]:
    """Module-scoped per-(file, xdist worker) isolated cfg, OR fixed-key
    isolated cfg when the module declares `@isolation_scope(...)`.

    See `tests/_marks.py::IsolationScope` for the cross-tier sharing
    semantics. Default behavior is per-(module, worker) hash — each
    file × xdist worker gets its own prefix; concurrent writes don't
    race. With `@isolation_scope(...)` at module scope, the fixture
    uses the scope value as a stable suffix; all three tiers (db /
    app2 / qs_browser) declaring the same scope read each other's
    state via the shared DB prefix.

    Teardown (best-effort): drop the worker's prefixed schema so
    repeated runs don't accumulate `_<suffix>` debris. Failures
    don't break the session — the next run's writer will DROP+CREATE.
    """
    from recon_gen.common.sql import Dialect

    # See `_isolated_cfg_key` for the pyright-cast rationale.
    request_any: Any = request  # typing-smell: ignore[explicit-any]: pytest FixtureRequest protocol attrs are dynamic
    scope_marker = next(
        request_any.node.iter_markers("isolation_scope"), None,
    )
    if scope_marker is not None and scope_marker.args:
        suffix = f"x_{scope_marker.args[0]}"
    else:
        suffix = _isolated_cfg_key(request, cfg)
    isolated = _isolate_cfg(cfg, suffix=suffix, tmp_path_factory=tmp_path_factory)
    yield isolated

    if isolated.dialect is Dialect.DUCKDB:
        return
    try:
        from recon_gen.common.db import connect_demo_db, execute_script
        from recon_gen.common.l2 import default_l2_instance
        from recon_gen.common.l2.schema import emit_schema_drop_sql
        instance = default_l2_instance()
        teardown_conn = connect_demo_db(isolated)
        try:
            clean_sql = emit_schema_drop_sql(
                instance,
                prefix=isolated.db_table_prefix,
                dialect=isolated.dialect,
            )
            with teardown_conn.cursor() as cur:
                execute_script(cur, clean_sql, dialect=isolated.dialect)
            teardown_conn.commit()
        finally:
            teardown_conn.close()
    except Exception as exc:  # noqa: BLE001
        print(
            f"isolated_cfg teardown[{suffix}]: best-effort drop failed: "
            f"{exc!r}"
        )


@pytest.fixture
def db_conn(isolated_cfg: "Config") -> Iterator[Any]:
    """Function-scoped DB connection opened against `isolated_cfg`.

    Centralizes `connect_demo_db(isolated_cfg) → yield → close` so
    individual test files don't reimplement it.
    """
    from recon_gen.common.db import connect_demo_db
    conn = connect_demo_db(isolated_cfg)
    try:
        yield conn
    finally:
        conn.close()
