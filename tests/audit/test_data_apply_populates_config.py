"""BC.7.3 — production ``data apply`` populates ``<prefix>_config``.

Regression for the chronic v11.10.0+ production bug surfaced in BC.6:
every customer running ``recon-gen data apply --execute`` saw empty
``limit_breach`` / ``stuck_pending`` / ``stuck_unbundled`` matview tabs
because the CLI never seeded ``<prefix>_config``, so the L1 invariants'
``JSON_TABLE`` join over ``<prefix>_config.l2_yaml`` found zero per-rail
caps. BC.7.2 folded ``build_config_populate_sql`` into
``build_full_seed_sql`` so the production path now emits the populate
SQL alongside the seed; this test pins that contract.

Spins a real postgres:17-alpine container (matches
``test_deploy_pipeline_pg_to_sqlite.py`` shape) and invokes the
``recon-gen data apply --execute`` code path through the Click CLI
against it. Asserts the config row materializes AND
``<prefix>_limit_breach`` is non-zero (the matview JOIN finds the
L2's limit_schedules).

Skip conditions (in order):
- testcontainers / docker not available — local without Docker.
- spec_example.yaml limit_schedules / planted L1 violations not
  reaching limit_breach would defeat the assertion, so we use the
  baseline + densified scenario the CLI itself runs.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

pytest.importorskip("testcontainers.postgres")

from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]: third-party library lacks PEP 561 stubs

from recon_gen.cli import main
from recon_gen.common.config import load_config
from recon_gen.common.db import connect_demo_db


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC_EXAMPLE_YAML = _REPO_ROOT / "tests" / "l2" / "spec_example.yaml"


def _docker_available() -> bool:
    """Best-effort Docker daemon probe; mirrors `_studio_deploy_helpers`."""
    import subprocess
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


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="docker not available — skipping postgres-in-docker e2e",
)


@pytest.fixture
def pg_url() -> Iterator[str]:
    """Spin a postgres:17-alpine testcontainer; yield connection URL.

    Function-scoped so concurrent tests don't collide on container
    name / port. Strips the SQLAlchemy ``+psycopg2`` URL suffix because
    psycopg (libpq) wants the plain form.
    """
    container = PostgresContainer("postgres:17-alpine")
    container.start()
    try:
        raw_url: str = container.get_connection_url()  # type: ignore[no-untyped-call]: testcontainers method has no type annotations
        yield raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    finally:
        container.stop()


@pytest.fixture
def cfg_path(tmp_path: Path, pg_url: str) -> Path:
    """Write a minimal config.yaml pointing at the test PG container."""
    cfg_dict = {
        "aws_account_id": "111122223333",
        "aws_region": "us-east-1",
        "deployment_name": "recon-bc7-test",
        "db_table_prefix": "spec_example",
        "datasource_arn": (
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        "demo_database_url": pg_url,
        "dialect": "postgres",
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_dict))
    return cfg_path


def _scalar(cfg, sql: str):  # type: ignore[no-untyped-def]: cfg untyped, return is row[0] of unknown driver type
    """Fetch a single scalar value from the demo DB via the cfg's driver."""
    conn = connect_demo_db(cfg)  # pyright: ignore[reportUnknownArgumentType]: connect_demo_db cfg from untyped fixture
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            cur.close()
    finally:
        conn.close()


def test_data_apply_populates_config_and_limit_breach(
    cfg_path: Path,
) -> None:
    """BC.7.3 — full ``schema apply`` + ``data apply`` round-trip:
    the config row is populated AND ``limit_breach`` is non-empty.

    Drives via Click ``CliRunner`` so the path under test is the real
    operator-visible CLI surface (mix-in semantics, dialect dispatch,
    same code the wheel ships).
    """
    runner = CliRunner()
    common_args = [
        "-c", str(cfg_path),
        "--l2", str(_SPEC_EXAMPLE_YAML),
        "--execute",
    ]

    schema_result = runner.invoke(
        main, ["schema", "apply", *common_args],
        catch_exceptions=False,
    )
    assert schema_result.exit_code == 0, schema_result.output

    apply_result = runner.invoke(
        main, ["data", "apply", *common_args],
        catch_exceptions=False,
    )
    assert apply_result.exit_code == 0, apply_result.output

    refresh_result = runner.invoke(
        main, ["data", "refresh", *common_args],
        catch_exceptions=False,
    )
    assert refresh_result.exit_code == 0, refresh_result.output

    cfg = load_config(str(cfg_path))
    # BC.7 wrote one row to <prefix>_config. BC.12 replaced that 3-column
    # table with the EAV <prefix>_config_kv (per the Oracle 19c
    # ORA-32368 fix — see docs/audits/bc_12_config_kv_spike.md +
    # docs/reference/oracle-19c-constraints.md). The contract is the
    # same: schema apply populates the table; if it's empty, the L1
    # matviews see no caps and the dashboards stay blank in production.
    # Assertion now: kv has >0 rows AND the typed projection view that
    # limit_breach consumes has the L2's limit_schedules in it.
    kv_rows = _scalar(cfg, "SELECT COUNT(*) FROM spec_example_config_kv")
    assert kv_rows is not None and kv_rows > 0, (
        f"BC.7+BC.12 regression: schema apply did not populate "
        f"spec_example_config_kv (rows={kv_rows!r}). The L1 invariant "
        f"matviews JOIN typed projection views over this EAV; without "
        f"the populate, limit_breach / stuck_pending / stuck_unbundled "
        f"stay empty in production."
    )

    breach_rows = _scalar(
        cfg, "SELECT COUNT(*) FROM spec_example_limit_breach",
    )
    assert breach_rows is not None and breach_rows > 0, (
        f"BC.7+BC.12 fix did not unblock the matview JOIN: "
        f"spec_example_limit_breach is still empty (rows={breach_rows!r}). "
        f"The kv populated but the typed view found no limit_schedules — "
        f"check emit_config_populate_sql + v_config_limit_schedules."
    )
