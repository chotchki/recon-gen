"""CB.17.a — Smoke tests for the shared-container session fixtures.

Verifies the env-URL fast path (no Docker required). The testcontainers-
spin path is exercised implicitly when CB.17.b chains the cfg fixture
to consume these URLs, and by the existing testcontainers-using tests
once they migrate.

What this file pins:

- ``_strip_sa_url_prefix`` correctly rewrites SQLAlchemy-flavored URLs
  from testcontainers into the plain forms recon_gen's drivers expect.
- ``pg_container_url`` yields ``RECON_GEN_DEMO_DATABASE_URL_PG`` verbatim
  when set (no testcontainers spin).
- ``oracle_container_url`` yields ``RECON_GEN_DEMO_DATABASE_URL_OR``
  verbatim when set.

NOT exercised here (need Docker, deferred to CB.17.c full-stack spike):

- Actual testcontainers spin + container teardown.
- xdist multi-worker container lifecycle.
"""
from __future__ import annotations

from typing import Generator, cast

import pytest

from recon_gen.common.env_keys import (
    RECON_GEN_DEMO_DATABASE_URL_OR,
    RECON_GEN_DEMO_DATABASE_URL_PG,
)
from tests._marks import Tier, tier
from tests.conftest import (
    _strip_sa_url_prefix,
    oracle_container_url,
    pg_container_url,
)


pytestmark = tier(Tier.UNIT)


def test_strip_sa_url_prefix_postgres() -> None:
    """SQLAlchemy-flavored PG URL → libpq-friendly form."""
    sa = "postgresql+psycopg2://user:pw@host:5432/db"
    assert _strip_sa_url_prefix(sa) == "postgresql://user:pw@host:5432/db"


def test_strip_sa_url_prefix_oracle() -> None:
    """SQLAlchemy-flavored Oracle URL → python-oracledb form."""
    sa = "oracle+oracledb://user:pw@host:1521/?service_name=FREEPDB1"
    assert _strip_sa_url_prefix(sa) == (
        "oracle://user:pw@host:1521/?service_name=FREEPDB1"
    )


def test_strip_sa_url_prefix_already_plain_passes_through() -> None:
    """No-op when the URL is already in plain form (idempotent)."""
    plain = "postgresql://user:pw@host:5432/db"
    assert _strip_sa_url_prefix(plain) == plain


def test_pg_container_url_yields_env_when_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Env-URL fast path: yields the env value verbatim, no Docker spin.

    Drives the fixture's underlying generator directly so we don't need
    a sub-pytester to exercise it.

    CB.17.k — fixture signature now includes
    ``(tmp_path_factory, worker_id)`` for the xdist-shared-container
    coordinator. The env-URL fast path bails before either is
    consulted, so any values are fine.

    BV.3.3.f follow-up (2026-06-11): the env-URL path now runs the
    pgcrypto cluster-init hook (``_install_pgcrypto_extension`` under
    a FileLock) before yielding. Monkeypatch the installer to a no-op
    so this smoke test doesn't try to connect to the fake URL —
    coverage of the install itself lives in the live-container path.
    Asserts the installer IS called against the env URL (the contract
    is "env-URL still gets pgcrypto installed, just without spinning
    a new container").
    """
    fake = "postgresql://fake:5432/x"
    monkeypatch.setenv(RECON_GEN_DEMO_DATABASE_URL_PG.name, fake)
    install_calls: list[str] = []
    monkeypatch.setattr(
        "tests.conftest._install_pgcrypto_extension",
        install_calls.append,
    )
    # pytest's @fixture decorator stashes the undecorated function on
    # `__wrapped__`; cast to its real Iterator type since the decorator
    # erases the return annotation under strict pyright.
    gen = cast(
        "Generator[str, None, None]",
        pg_container_url.__wrapped__(  # type: ignore[attr-defined]: pytest decorator stashes the generator
            tmp_path_factory=tmp_path_factory, worker_id="master",
        ),
    )
    assert next(gen) == fake
    assert install_calls == [fake]
    # Exhaust the generator to trigger the (no-op) finalize.
    with pytest.raises(StopIteration):
        next(gen)


def test_oracle_container_url_yields_env_when_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Env-URL fast path for Oracle. Same shape as the PG test above."""
    fake = "oracle://fake:1521/?service_name=FREEPDB1"
    monkeypatch.setenv(RECON_GEN_DEMO_DATABASE_URL_OR.name, fake)
    gen = cast(
        "Generator[str, None, None]",
        oracle_container_url.__wrapped__(  # type: ignore[attr-defined]: pytest decorator stashes the generator
            tmp_path_factory=tmp_path_factory, worker_id="master",
        ),
    )
    assert next(gen) == fake
    with pytest.raises(StopIteration):
        next(gen)
