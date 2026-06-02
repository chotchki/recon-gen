"""CB.4 — auto-apply `@tier(Tier.DB)` to every test under tests/e2e/db/.

CB.7 — also defines `db_cfg`: the per-worker isolation fixture for
`@writes()` tests. See the fixture docstring below for the contract.

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
when run locally; the runner's `--targets=aw` cells use AWS RDS so
the DOCKER need is "ok if absent when AWS_RDS is up" — CB.6's needs
audit may refine this to a logical-OR shape, but for now DOCKER is
the cheap-default need that won't false-positive a skip in the AWS
cells either because the runner already handles RDS-vs-Docker
substrate selection upstream of the needs check).
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
# CB.7 — `db_cfg`: per-worker isolation injection point for @writes() tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def db_cfg(
    request: pytest.FixtureRequest,
    cfg: "Config",
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator["Config"]:
    """Per-worker isolated cfg for tests that mutate DB state (`@writes()`).

    Test contract: `@writes()` tests must inject `db_cfg` instead of
    `cfg`. The fixture provides an isolation primitive that's
    dialect-aware:

    - **DuckDB**: returns a cfg whose `demo_database_url` points at a
      per-worker `.duckdb` file under `tmp_path_factory`. Each xdist
      worker gets its own file, so concurrent `@writes()` tests across
      workers never race on the file lock. The file starts empty —
      tests apply schema + seed against it (e.g. via
      `tests/e2e/_seed_helpers.py::apply_db_seed`).

    - **Postgres / Oracle**: returns a cfg whose `db_table_prefix` and
      `deployment_name` are suffixed with the worker id (e.g.
      `qsgen_postgres_w0`). Same DB, different prefix per worker —
      concurrent `@writes()` tests across workers operate on disjoint
      table sets. Tests apply schema + seed against the prefix.

    Why session-scoped: the isolation primitive (the cloned file or
    the worker-prefix) is reused across all `@writes()` tests on the
    same worker. Module-scope fixtures inside test files (`seeded_db`
    etc.) can still seed-once-per-file by re-applying schema against
    the worker-isolated prefix.

    This fixture replaces the hand-rolled isolation patterns scattered
    across `test_inv_anomaly_direct.py`, `test_inv_money_trail_direct.py`,
    etc. (each invented its own `isolated_<x>_cfg` fixture). The
    canonical pattern: `@writes()` declares intent; `db_cfg` provides
    the isolation primitive; tests + fixtures inject `db_cfg`.
    """
    del request  # required by pytest hook signature; isolation key derives from worker id
    from recon_gen.common.sql import Dialect
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "gw0")

    if cfg.dialect is Dialect.DUCKDB:
        # Per-worker .duckdb file. Empty — tests apply schema + seed.
        worker_db_dir = tmp_path_factory.mktemp(f"db_cfg_{worker_id}")
        from recon_gen.common.db import make_demo_database_url
        worker_url = make_demo_database_url(
            Dialect.DUCKDB, worker_db_dir / "demo.duckdb",
        )
        isolated = dataclasses.replace(cfg, demo_database_url=worker_url)
        yield isolated
        # No teardown needed — tmp_path_factory cleans the dir at session end.
        return

    # Postgres / Oracle: per-worker prefix.
    suffix = f"w{worker_id.replace('gw', '')}"
    new_prefix = f"{cfg.db_table_prefix}_{suffix}"
    new_deployment = f"{cfg.deployment_name}-{suffix}"
    isolated = dataclasses.replace(
        cfg,
        db_table_prefix=new_prefix,
        deployment_name=new_deployment,
    )
    yield isolated
    # Best-effort teardown — drop the worker's prefixed schema. Failures
    # don't fail the session (the next run's @writes() test will DROP+CREATE
    # via its own schema apply anyway).
    try:
        from recon_gen.common.db import connect_demo_db, execute_script
        from recon_gen.common.l2 import default_l2_instance
        from recon_gen.common.l2.schema import emit_schema_drop_sql
        instance = default_l2_instance()
        teardown_conn = connect_demo_db(isolated)
        try:
            clean_sql = emit_schema_drop_sql(
                instance, prefix=new_prefix, dialect=isolated.dialect,
            )
            with teardown_conn.cursor() as cur:
                execute_script(cur, clean_sql, dialect=isolated.dialect)
            teardown_conn.commit()
        finally:
            teardown_conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"db_cfg teardown[{worker_id}]: best-effort drop failed: {exc!r}")
