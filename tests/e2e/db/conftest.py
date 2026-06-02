"""CB.4 — auto-apply `@tier(Tier.DB)` to every test under tests/e2e/db/.

CB.7 (REFACTORED 2026-06-02) — also defines `isolated_cfg`: the
provider-supplied per-(module, worker) isolation primitive.

**Architectural shift from the original CB.7:** the prior design marked
test FUNCTIONS with `@writes()` and required them to inject `db_cfg`.
That was backwards — the test is the consumer, not the writer. The
actual mutation happens inside fixtures (`seeded_db`, `seeded_l2_db`,
etc.). Operator (2026-06-02): "I am the @writes provider at the
module/class/function level and everything else in this scope level
consumes what I provide."

New contract:

- **Writer fixtures request `isolated_cfg` instead of `cfg`.** That
  declaration IS the @writes-equivalent.
- **Test functions request whatever they need** (the writer fixture's
  return, plus `isolated_cfg` if they want to read it directly). No
  marker.
- **Pytest fixture caching handles consistency.** `isolated_cfg` is
  module-scoped; the same instance is shared across all consumers in
  the module. No scope mismatch with markers.
- **`xdist_group` markers become unnecessary** for concurrent-write
  races: each worker gets its own isolated_cfg via worker_id keying.
  Markers remain only where genuinely needed (cross-tier sharing via
  shared DB state — but that's a separate concern).
- **`@serial(reason)` becomes redundant** for the same reason — was
  always a band-aid over the same root cause.

Tier-mark auto-application:
- `test_dataset_sql_smoke.py` — per-dataset CustomSQL parse + bind
  smoke against the live demo DB via `connect_demo_db`
- `test_demo_apply_row_counts.py` — post-`demo apply` row-count
  smoke (≥1 row in every named matview the seed should populate)
- `test_audit_pdf_render_verify.py` — audit PDF render + verify
  against the live demo DB

These touch a DB but no QS embed and no browser rendering — pure
DB tier per the audit doc taxonomy.

`Need.DOCKER` is auto-added alongside the tier marker because every
DB-tier test needs a database container (PG / Oracle Docker images
when run locally).
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from typing import TYPE_CHECKING, Any, Iterator

import pytest

from tests._marks import Need, Tier, needs, tier

if TYPE_CHECKING:
    from recon_gen.common.config import Config


_DB_TIER_MARK = tier(Tier.DB)
_DB_NEEDS_MARK = needs(Need.DOCKER)
_OWN_DIR = pathlib.Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: Any, items: list[Any],  # typing-smell: ignore[explicit-any]: pytest.Config + Item from late import
) -> None:
    """Apply `@tier(Tier.DB)` + `@needs(DOCKER)` to every item collected
    from this dir."""
    _ = config
    own_dir = str(_OWN_DIR)
    for item in items:
        if own_dir not in str(item.path):
            continue
        if not any(m.name == "tier" for m in item.iter_markers()):
            item.add_marker(_DB_TIER_MARK)
        if not any(m.name == "needs" for m in item.iter_markers()):
            item.add_marker(_DB_NEEDS_MARK)


# ---------------------------------------------------------------------------
# CB.7 (refactored) — `isolated_cfg`: provider-supplied per-(module, worker)
# isolation primitive. Writer fixtures request this in place of `cfg`.
# ---------------------------------------------------------------------------


def _isolated_cfg_key(request: pytest.FixtureRequest) -> str:
    """Build a stable isolation suffix from module name + xdist worker id.

    The suffix combines:
    - module name (e.g. `test_audit_invariants_direct`) — so different
      test files don't collide on the same prefix.
    - xdist worker id (e.g. `gw0`) — so concurrent workers running the
      same module on different parametrize cells don't race.

    The result is a snake-case identifier safe for DB prefixes.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0").replace("gw", "w")
    # request.module.__name__ is e.g. "tests.e2e.db.test_audit_invariants_direct"
    mod_short = request.module.__name__.rsplit(".", 1)[-1]
    # Drop the "test_" prefix; cap at 18 chars so the combined suffix
    # stays well under cfg.db_table_prefix's 30-char Oracle limit.
    if mod_short.startswith("test_"):
        mod_short = mod_short[5:]
    mod_short = mod_short[:18]
    return f"{mod_short}_{worker_id}"


def _isolate_cfg(
    cfg: "Config",
    *,
    suffix: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> "Config":
    """Return a per-suffix isolated cfg copy.

    Pure function — no fixture coupling. Callers that need
    isolation outside the canonical `isolated_cfg` fixture chain (e.g.
    dialect-parametrized tests where `cfg` is computed differently per
    callspec) can call this directly with the suffix they want.

    Dialect-aware:
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
    # PG / Oracle prefix suffix.
    return dataclasses.replace(
        cfg,
        db_table_prefix=f"{cfg.db_table_prefix}_{suffix}",
        deployment_name=f"{cfg.deployment_name}-{suffix}",
    )


@pytest.fixture
def db_conn(isolated_cfg: "Config") -> Iterator[Any]:
    """Function-scoped DB connection opened against `isolated_cfg`.

    Centralizes `connect_demo_db(isolated_cfg) → yield → close` so
    individual test files don't reimplement it. Downstream tests
    just request `db_conn` (and optionally `isolated_cfg` if they
    need the cfg itself, e.g. to read `db_table_prefix` for SQL
    templating).

    Function-scope: each test gets its own connection. Cheap for
    DuckDB (in-process) and PG (pooled).
    """
    from recon_gen.common.db import connect_demo_db
    conn = connect_demo_db(isolated_cfg)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def isolated_cfg(
    request: pytest.FixtureRequest,
    cfg: "Config",
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator["Config"]:
    """Module-scoped per-(file, xdist worker) isolated cfg.

    Writer fixtures request this in place of `cfg`. The framework
    guarantees:

    1. Each (module, xdist worker) pair gets its OWN isolated cfg.
       Concurrent workers running the same module don't race on
       schema apply / seed (each writes to its own prefix).
    2. All fixtures and tests in the SAME (module, worker) share
       the SAME isolated cfg via pytest's normal fixture caching.
       Consumer reads see consistent state with the writer's writes.
    3. Module-scope writer fixtures depending on `isolated_cfg` run
       ONCE per module-worker — the seed cost is amortized across
       all tests in the file.

    Teardown (best-effort): drop the worker's prefixed schema so
    repeated runs don't accumulate `_<module>_w0` debris. Failures
    don't break the session — the next run's writer will DROP+CREATE.

    See `tests/e2e/db/test_inv_anomaly_direct.py::seeded_l2_db` and
    `tests/e2e/db/test_audit_invariants_direct.py::seeded_db` for
    canonical writer-fixture usage.
    """
    from recon_gen.common.sql import Dialect

    suffix = _isolated_cfg_key(request)
    isolated = _isolate_cfg(cfg, suffix=suffix, tmp_path_factory=tmp_path_factory)
    yield isolated

    # Teardown for PG / Oracle — drop the worker-prefixed schema so
    # repeated runs don't accumulate debris. DuckDB cleans itself via
    # `tmp_path_factory`.
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
