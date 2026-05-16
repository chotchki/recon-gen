"""Shared helpers for X.4.j.1 + X.4.j.2 — Studio + Deploy e2e tests.

Both the API e2e (``test_deploy_pipeline_pg_to_sqlite.py``) and the
browser e2e (``test_studio_deploy_browser.py``) need the same
postgres-in-docker + etl_hook + studio-app composition. Centralizing
the helpers keeps the two test files focused on their assertion shape
without duplicating the wire-up.

Public surface:
  - ``QUICKSIGHT_GEN_BIN`` / ``SASQUATCH_YAML`` — path constants
  - ``docker_available()`` — skipif probe
  - ``apply_schema_to(cfg)`` — emit + apply v6 schema for sasquatch_pr
  - ``write_pg_etl_cfg(pg_url, tmp_path)`` — generate per-test pg cfg yaml
  - ``write_etl_hook_script(tmp_path, pg_cfg_path)`` — generate
    `quicksight-gen data apply --execute` shell script + chmod +x
  - ``make_studio_cfg(tmp_path, ...)`` — full Config wiring
  - ``build_studio_app(cfg, pool)`` — in-process studio ASGI app
  - ``studio_server(cfg)`` — uvicorn-in-thread context manager (browser e2e)
"""
from __future__ import annotations

import asyncio
import contextlib
import stat
import subprocess
import sysconfig
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import yaml

from quicksight_gen.cli._html_serve import (
    APP_TITLES,
    REAL_APPS,
    build_real_app,
)
from quicksight_gen.common.config import (
    Config,
    EtlDatasourceConfig,
    TestGeneratorConfig,
)
from quicksight_gen.common.db import (
    AsyncConnectionPool,
    connect_demo_db,
    execute_script,
    make_connection_pool,
)
from quicksight_gen.common.html._studio_routes import make_studio_routes
from quicksight_gen.common.html._tree_fetcher import make_tree_db_fetcher
from quicksight_gen.common.html.server import ServedDashboard, make_app
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.schema import emit_schema
from quicksight_gen.common.sql import Dialect


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SASQUATCH_YAML = _REPO_ROOT / "run" / "sasquatch_pr.yaml"
# Resolve quicksight-gen relative to this interpreter so the active venv
# is honored (whether `.venv/bin/pytest` or a runner-managed tmpvenv).
QUICKSIGHT_GEN_BIN = Path(sysconfig.get_path("scripts")) / "quicksight-gen"


def docker_available() -> bool:
    """Best-effort Docker daemon probe; mirrors the runner's
    `_probe_docker` shape."""
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def apply_schema_to(cfg: Config) -> None:
    """Apply ``emit_schema(sasquatch_pr_instance)`` to ``cfg``'s demo
    DB. Idempotent enough for test setup — schema CREATE IF NOT EXISTS
    handles re-runs."""
    instance = load_instance(SASQUATCH_YAML)
    schema_sql = emit_schema(instance, dialect=cfg.dialect)
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(cur, schema_sql, dialect=cfg.dialect)
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def write_pg_etl_cfg(pg_url: str, tmp_path: Path) -> tuple[Config, Path]:
    """Build a Config + write a yaml file for the postgres ETL source.
    The etl_hook script invokes `quicksight-gen` against this yaml so
    `data apply` can re-seed the postgres on each pipeline run."""
    pg_cfg_path = tmp_path / "pg_etl_cfg.yaml"
    # Z.C — deployment_name + db_table_prefix are required cfg fields.
    pg_cfg_dict = {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "deployment_name": "qsgen-pg-etl",
        "db_table_prefix": "sasquatch_pr",
        "datasource_arn": (
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        "demo_database_url": pg_url,
        "dialect": "postgres",
    }
    pg_cfg_path.write_text(yaml.safe_dump(pg_cfg_dict))
    cfg = Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
        deployment_name="qsgen-pg-etl",
        db_table_prefix="sasquatch_pr",
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=pg_url,
        dialect=Dialect.POSTGRES,
    )
    return cfg, pg_cfg_path


def write_etl_hook_script(
    tmp_path: Path, pg_cfg_path: Path,
) -> Path:
    """Write a shell script that runs ``quicksight-gen data apply
    --execute`` against the postgres container, then chmod +x."""
    script_path = tmp_path / "etl_hook.sh"
    script_path.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"exec {QUICKSIGHT_GEN_BIN} data apply --execute "
        f"-c {pg_cfg_path} --l2 {SASQUATCH_YAML}\n"
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return script_path


def make_studio_cfg(
    tmp_path: Path,
    *,
    etl_hook: Path | None = None,
    etl_datasource_url: str | None = None,
) -> tuple[Config, Path]:
    """Studio cfg: demo_database_url=sqlite tempfile under ``tmp_path``,
    optional etl_hook + etl_datasource. Returns ``(cfg, sqlite_path)``."""
    sqlite_path = tmp_path / "demo.sqlite"
    # Z.C — deployment_name + db_table_prefix are required cfg fields.
    db_prefix = "sasquatch_pr"
    cfg_kwargs: dict[str, Any] = {  # noqa: ANN401
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "deployment_name": "qsgen-studio",
        "db_table_prefix": db_prefix,
        "datasource_arn": (
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        "demo_database_url": f"sqlite:///{sqlite_path}",
        "dialect": Dialect.SQLITE,
        "test_generator": TestGeneratorConfig(scope="full"),
    }
    if etl_hook is not None:
        cfg_kwargs["etl_hook"] = str(etl_hook)
    if etl_datasource_url is not None:
        cfg_kwargs["etl_datasource"] = EtlDatasourceConfig(
            url=etl_datasource_url,
            transactions_table=f"{db_prefix}_transactions",
            daily_balances_table=f"{db_prefix}_daily_balances",
        )
    return Config(**cfg_kwargs), sqlite_path


def build_studio_app(
    cfg: Config, pool: AsyncConnectionPool,
) -> Any:  # noqa: ANN401  — Starlette ASGI app
    """Build the studio ASGI app pointing at ``cfg`` (sqlite demo DB)
    + ``pool`` (async sqlite pool against same DB). Mirrors the
    ``cli/_html_serve.py::_serve`` composition but in-process."""
    instance = load_instance(SASQUATCH_YAML)
    cache = L2InstanceCache(SASQUATCH_YAML, instance)

    dashboards: dict[str, ServedDashboard] = {}
    for app_name in REAL_APPS:
        tree_app, sheet = build_real_app(app_name, cfg, instance)
        dashboards[app_name] = ServedDashboard(
            tree_app=tree_app,
            sheet=sheet,
            title=APP_TITLES.get(app_name, app_name.title()),
            data_fetcher=make_tree_db_fetcher(tree_app, cfg, pool=pool),
            theme=instance.theme,
            filter_specs=(),
        )

    # Z.C — make_studio_routes' prefix_override is the legacy L2-segment
    # override. Pass cfg.db_table_prefix so SQL emitters key off the
    # right tables; the override path itself is being phased out.
    studio_routes = make_studio_routes(
        cache,
        dev_log=False,
        db_pool=pool,
        dialect=cfg.dialect,
        prefix_override=cfg.db_table_prefix,
        cfg=cfg,
    )
    return make_app(dashboards=dashboards, studio_routes=studio_routes)


@contextlib.contextmanager
def studio_server(cfg: Config) -> Generator[str, None, None]:
    """Spin a uvicorn server in a thread serving the full studio app
    against ``cfg``. Yields the bound base URL.

    Uses a thread (not a subprocess) so the test process directly owns
    the server's lifecycle — no port-scraping, no sigchld handling.
    The thread runs ``asyncio.run`` of an inner coroutine that opens
    the pool + serves uvicorn on the same event loop (mirrors the
    ``cli/_html_serve.py::_serve`` shape).

    Use this for browser-driven tests (Playwright needs a real bound
    port). API tests use ``httpx.AsyncClient(transport=ASGITransport)``
    directly against the in-process app.
    """
    import uvicorn

    # Mutable holders — the thread sets them.
    bound_port: list[int] = []
    server_ready = threading.Event()
    server_holder: list[Any] = []  # noqa: ANN401  — uvicorn.Server
    startup_error: list[BaseException] = []

    def _run_server() -> None:
        async def _serve() -> None:
            try:
                pool = await make_connection_pool(cfg, max_size=4)
            except Exception as e:  # noqa: BLE001
                startup_error.append(e)
                server_ready.set()
                return
            try:
                app = build_studio_app(cfg, pool)
                config = uvicorn.Config(
                    app, host="127.0.0.1", port=0, log_level="error",
                )
                server = uvicorn.Server(config)
                server_holder.append(server)
                serve_task = asyncio.create_task(server.serve())
                # Block until uvicorn binds the port.
                deadline = time.monotonic() + 10
                while not server.started:
                    if time.monotonic() > deadline:
                        startup_error.append(
                            RuntimeError(
                                "uvicorn didn't bind within 10s",
                            ),
                        )
                        server_ready.set()
                        return
                    await asyncio.sleep(0.05)
                bound_port.append(
                    server.servers[0].sockets[0].getsockname()[1],
                )
                server_ready.set()
                await serve_task
            finally:
                await pool.close()

        asyncio.run(_serve())

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    if not server_ready.wait(timeout=15):
        raise RuntimeError("studio server thread didn't signal ready")
    if startup_error:
        raise startup_error[0]
    base_url = f"http://127.0.0.1:{bound_port[0]}"
    try:
        yield base_url
    finally:
        if server_holder:
            server_holder[0].should_exit = True
        thread.join(timeout=15)


def row_count(cfg: Config, table: str) -> int:
    """Open a sync connection to ``cfg``'s demo DB and return
    ``COUNT(*) FROM table``. Used by both API + browser tests to
    assert post-deploy state."""
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return int(cur.fetchone()[0])
        finally:
            cur.close()
    finally:
        conn.close()
