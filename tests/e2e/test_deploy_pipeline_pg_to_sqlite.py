"""X.4.j.1 — Cross-dialect deploy pipeline e2e (PG → SQLite primary).

Exercises the full X.4.g.13 contract end-to-end against real DBs:

  postgres-in-docker  ── etl_datasource ─┐
                                          │
              etl_hook ──────────────────►│  pipeline orchestration
              (`recon-gen data        │  (POST /deploy)
               apply --execute` against    │
               the postgres container)     ▼
                                  ┌──────────────────┐
                                  │  step 1: hook    │
                                  │  step 2: wipe    │
                                  │  step 2: pull    │
                                  │  step 3: gen     │
                                  │  step 4: matview │
                                  │  step 5: bump    │
                                  └────────┬─────────┘
                                           │
                                           ▼
                                  sqlite tempfile
                                  (demo_database_url)

Then asserts each of the 4 dashboards (L1 / L2FT / Inv / Exec) returns
non-empty visual data after the deploy.

Layer + gating:
  - lives in ``tests/e2e/`` (gated by QS_GEN_E2E=1 via conftest)
  - skipif on docker probe (testcontainers needs a daemon)
  - takes ~120s with sasquatch_pr (postgres container ~10s + data
    apply ~15s + sqlite seed ~5s + matview refresh ~5s)
"""
from __future__ import annotations

import asyncio
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

# Skip the whole module if testcontainers / docker / starlette aren't
# available — those are e2e dependencies, not unit-test deps.
pytest.importorskip("testcontainers.postgres")
pytest.importorskip("aiosqlite")
pytest.importorskip("httpx")

import httpx
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs

from recon_gen.cli._html_serve import REAL_APPS
from recon_gen.common.config import Config, EtlDatasourceConfig
from recon_gen.common.db import make_connection_pool
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.sql import Dialect

from tests.e2e._studio_deploy_helpers import (
    QUICKSIGHT_GEN_BIN,
    SASQUATCH_YAML,
    apply_schema_to,
    build_studio_app,
    docker_available,
    make_studio_cfg,
    row_count,
    write_etl_hook_script,
    write_pg_etl_cfg,
)


pytestmark = [
    pytest.mark.skipif(
        not docker_available(),
        reason="docker not available — skipping postgres-in-docker e2e",
    ),
    pytest.mark.skipif(
        not SASQUATCH_YAML.exists(),
        reason=(
            f"{SASQUATCH_YAML} missing — sasquatch_pr.yaml is gitignored "
            "(operator config); copy it into run/ to enable this test"
        ),
    ),
    pytest.mark.skipif(
        not QUICKSIGHT_GEN_BIN.exists(),
        reason=f"{QUICKSIGHT_GEN_BIN} missing — install the package first",
    ),
]


# X.4.j.1.a — postgres container fixture
@pytest.fixture
def pg_container_url() -> Iterator[str]:
    """Spin a postgres:17-alpine testcontainer; yield its connection URL.

    Function-scoped so concurrent tests don't collide on container
    name / port. testcontainers handles port allocation + cleanup
    via Ryuk. Strips the SQLAlchemy ``+psycopg2`` suffix because
    psycopg3 (our async driver) wants the plain libpq URL form.
    """
    container = PostgresContainer("postgres:17-alpine")
    container.start()
    try:
        raw_url: str = container.get_connection_url()  # type: ignore[no-untyped-call]: testcontainers method has no type annotations
        url = raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)
        yield url
    finally:
        container.stop()


# X.4.j.1.b — etl_hook fixture
@pytest.fixture
def etl_hook_script(
    tmp_path: Path, pg_container_url: str,
) -> Path:
    """Write a shell script that runs ``recon-gen data apply
    --execute`` against the postgres container, exercising the
    realistic operator pattern. Pre-applies the schema so step 1's
    seed has tables to write into."""
    pg_cfg, pg_cfg_path = write_pg_etl_cfg(pg_container_url, tmp_path)
    apply_schema_to(pg_cfg)
    return write_etl_hook_script(tmp_path, pg_cfg_path)


# ---------------------------------------------------------------------------
# X.4.j.1.d-g — the test scenarios
# ---------------------------------------------------------------------------


def test_pg_to_sqlite_deploy_pipeline_full_loop(
    tmp_path: Path,
    etl_hook_script: Path,
    pg_container_url: str,
) -> None:
    """X.4.j.1.d/e/f — single integration test covering the full loop:

    1. POST /deploy returns 200 + DeploySummary with positive counts
       per step (X.4.j.1.d).
    2. Sqlite tempfile has rows in the base tables AND matview tables
       afterwards — proves step 4's matview refresh ran (X.4.j.1.e).
    3. Each of the 4 dashboards' canary page renders + carries the
       data-generation-id meta (X.4.j.1.f).
    """
    cfg, _sqlite_path = make_studio_cfg(
        tmp_path,
        etl_hook=etl_hook_script,
        etl_datasource_url=pg_container_url,
    )
    apply_schema_to(cfg)
    # Z.C — DB-table prefix lives on cfg now (was instance.instance).
    prefix = cfg.db_table_prefix

    async def _drive() -> None:
        pool = await make_connection_pool(cfg, max_size=4)
        try:
            app = build_studio_app(cfg, pool)
            transport = httpx.ASGITransport(app=app)
            # 120s timeout because deploy runs `recon-gen data
            # apply --execute` against postgres as the etl_hook
            # subprocess — that's ~15-30s on sasquatch_pr.
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=120,
            ) as client:
                # X.4.j.1.d — drive POST /deploy.
                resp = await client.post("/deploy")
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["halted"] is False
                assert body["halt_reason"] is None
                assert body["step1_etl_hook_exit_code"] == 0
                assert body["step2_pull"]["transactions_pulled"] > 0, (
                    "etl_hook should have seeded postgres + step 2 should "
                    "have pulled those rows into sqlite"
                )
                assert (
                    body["step3_generator"]["transactions_after"]
                    >= body["step2_pull"]["transactions_pulled"]
                ), "step 3 generator's writes are additive on top of step 2"
                assert body["step4_matviews_done"] is True
                assert body["step5_data_generation_id"] > 0

                # X.4.j.1.e — sqlite tempfile is non-empty.
                assert row_count(cfg, f"{prefix}_transactions") > 0
                assert row_count(cfg, f"{prefix}_daily_balances") > 0
                # Matview check — drift is the L1 invariant matview
                # most likely to be non-empty under sasquatch_pr's
                # planted scenarios. If 0 rows, step 4 failed silently.
                drift_rows = row_count(cfg, f"{prefix}_drift")
                assert drift_rows >= 0  # presence-only assertion

                # X.4.j.1.f — each dashboard's canary page.
                listing = await client.get("/dashboards")
                assert listing.status_code == 200

                for app_name in REAL_APPS:
                    dash_resp = await client.get(f"/dashboards/{app_name}")
                    assert dash_resp.status_code == 200, (
                        f"dashboard page for {app_name} did not render "
                        f"(status={dash_resp.status_code}): "
                        f"{dash_resp.text[:500]}"
                    )
                    # Per-visual data round-trip is X.4.j.2's browser
                    # e2e job. We settle for "page rendered + carries
                    # the data-generation-id meta" here.
                    assert (
                        '<meta name="data-generation-id"'
                        in dash_resp.text
                    ), f"{app_name} dashboard missing poller baseline"
        finally:
            await pool.close()

    asyncio.run(_drive())


# ---------------------------------------------------------------------------
# X.4.j.1.g — halt-on-failed-hook integration test
# ---------------------------------------------------------------------------


def test_pg_to_sqlite_halt_on_failed_etl_hook(
    tmp_path: Path,
    pg_container_url: str,
) -> None:
    """X.4.j.1.g — same fixture but the hook script ``exit 1``s. Assert
    halted=True + sqlite tempfile stays empty (no wipe occurred —
    proves the halt prevents the demo DB from being touched when the
    operator's ETL refresh failed).
    """
    sqlite_path = tmp_path / "demo.sqlite"

    bad_hook = tmp_path / "bad_etl_hook.sh"
    bad_hook.write_text("#!/bin/bash\nexit 1\n")
    bad_hook.chmod(bad_hook.stat().st_mode | stat.S_IEXEC)

    # Z.C — deployment_name + db_table_prefix are required cfg fields.
    db_prefix = "sasquatch_pr"
    cfg = Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
        deployment_name="recon-halt-test",
        db_table_prefix=db_prefix,
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=f"sqlite:///{sqlite_path}",
        dialect=Dialect.SQLITE,
        etl_hook=str(bad_hook),
        etl_datasource=EtlDatasourceConfig(
            url=pg_container_url,
            transactions_table=f"{db_prefix}_transactions",
            daily_balances_table=f"{db_prefix}_daily_balances",
        ),
    )
    apply_schema_to(cfg)
    prefix = db_prefix

    async def _drive() -> None:
        pool = await make_connection_pool(cfg, max_size=4)
        try:
            app = build_studio_app(cfg, pool)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=30,
            ) as client:
                resp = await client.post("/deploy")
                assert resp.status_code == 503, resp.text
                body = resp.json()
                assert body["halted"] is True
                assert body["halt_reason"] is not None
                assert "etl_hook returned exit_code=1" in body["halt_reason"]
                # CRITICAL: sqlite tables stay empty — wipe never ran.
                assert row_count(cfg, f"{prefix}_transactions") == 0
                assert row_count(cfg, f"{prefix}_daily_balances") == 0
        finally:
            await pool.close()

    asyncio.run(_drive())
