"""CB.17.b — Smoke tests for the cfg→container_url bridge.

`tests/e2e/conftest.py::cfg_with_container_url` swaps
`cfg.db.url` for the matching shared-container fixture URL
based on `cfg.db.dialect`. These tests exercise the dispatch logic
directly by building minimal `Config` instances and calling the
fixture body via its underlying function.

NOT exercised here:

- The full e2e fixture chain (`cfg` → `cfg_with_container_url` →
  `isolated_cfg`). That's CB.17.c full-stack spike.
- DuckDB passthrough against a real `.duckdb` file path. That's the
  same downstream territory.
"""
from __future__ import annotations

from typing import Callable, cast

from recon_gen.common.config import Config
from recon_gen.common.sql.dialect import Dialect
from tests._marks import Tier, tier


pytestmark = tier(Tier.UNIT)


def _minimal_cfg(dialect: Dialect, *, demo_url: str | None = None) -> Config:
    """Construct a minimal `Config` for fixture-body testing.

    Only fields needed for the dispatch under test are populated;
    others get their dataclass defaults or sentinel placeholders that
    the bridge never touches.
    """
    return Config(
        aws_account_id="000000000000",
        aws_region="us-east-1",
        deployment_name="recon-test",
        db_table_prefix="recon_test",
        demo_database_url=demo_url,
        dialect=dialect,
    )


def _call_bridge(
    cfg: Config, *, pg_url: str = "PG_URL", oracle_url: str = "OR_URL",
) -> Config:
    """Invoke `cfg_with_container_url`'s undecorated body."""
    # Late import — tests/e2e/ is a package and conftest fixtures live
    # under it. Pull the wrapped function for direct invocation so the
    # unit test doesn't need a full e2e session to exercise the dispatch.
    from tests.e2e.conftest import cfg_with_container_url

    fn = cast(
        "Callable[[Config, str, str], Config]",
        cfg_with_container_url.__wrapped__,  # type: ignore[attr-defined]: pytest decorator stashes the wrapped fn
    )
    return fn(cfg, pg_url, oracle_url)


def test_postgres_dialect_swaps_to_pg_url() -> None:
    """POSTGRES cfg → demo_database_url becomes the PG container URL."""
    cfg = _minimal_cfg(Dialect.POSTGRES, demo_url="cfg-loaded-url")
    out = _call_bridge(cfg, pg_url="postgresql://container:5432/x")
    assert out.demo_database_url == "postgresql://container:5432/x"
    # Other fields preserved by `dataclasses.replace`.
    assert out.aws.deployment_name == "recon-test"
    assert out.dialect is Dialect.POSTGRES


def test_oracle_dialect_swaps_to_oracle_url() -> None:
    """ORACLE cfg → demo_database_url becomes the Oracle container URL."""
    cfg = _minimal_cfg(Dialect.ORACLE, demo_url="cfg-loaded-url")
    out = _call_bridge(
        cfg, oracle_url="oracle://container:1521/?service_name=FREEPDB1",
    )
    assert out.demo_database_url == (
        "oracle://container:1521/?service_name=FREEPDB1"
    )
    assert out.dialect is Dialect.ORACLE


def test_duckdb_dialect_passes_through_unchanged() -> None:
    """DUCKDB cfg → no swap (file-based; yaml URL is authoritative).

    The bridge returns the same `cfg` object identity to make the
    passthrough contract obvious.
    """
    cfg = _minimal_cfg(Dialect.DUCKDB, demo_url="duckdb:///tmp/foo.duckdb")
    out = _call_bridge(cfg)
    assert out is cfg
    assert out.demo_database_url == "duckdb:///tmp/foo.duckdb"


def test_other_cfg_fields_preserved_on_swap() -> None:
    """Swap only touches `demo_database_url`; everything else carries through.

    Pyright would catch a missing field, but this test pins the
    invariant against future hand-edits to the bridge that might
    accidentally reset other fields.
    """
    cfg = _minimal_cfg(Dialect.POSTGRES, demo_url="old")
    out = _call_bridge(cfg, pg_url="new")
    assert out.aws.account_id == cfg.aws.account_id
    assert out.aws.region == cfg.aws.region
    assert out.aws.deployment_name == cfg.aws.deployment_name
    assert out.db_table_prefix == cfg.db.table_prefix
    # And the swap actually happened.
    assert out.demo_database_url != cfg.db.url
