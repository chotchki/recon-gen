"""DS.6 — the per-dialect lane: every enumeration domain replayed on the
cfg dialect (PG / Oracle / DuckDB) with engine == residual re-proven on
that engine.

The unit-tier gate (DS.3.5) proves engine == residual on DuckDB across
the full boundary grid. The SQL emitters branch on ``Dialect``, so the
SAME residual must hold once the matview SQL is the PG / Oracle form —
this is where that lands. Each domain re-emits its schema at the cfg
dialect under a unique prefix, loads the identical cells + anchors,
refreshes, and asserts the engine's violation set matches the
residual-derived ``expected_for`` (dialect-INDEPENDENT — the law does
not know the engine).

POLICY 1: this test runs identically local and on CI; the dialect is the
cfg's, never hardcoded. Claim ledger: PROVEN-on-D DuckDB (unit tier) /
PROVEN-on-D_boundary PG + Oracle (here). Depends on DS.0a (the stats
cascade) already being live so PG / Oracle refresh in seconds, not the
pre-cascade minutes.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest

from recon_gen.common.config import Config
from recon_gen.common.db import SyncConnection, connect_demo_db
from tests.enumeration.domains import (
    DETECTOR_DOMAINS,
    DOMAIN_BUILDERS,
    DOMAIN_L2_PATHS,
)
from tests.enumeration.harness import PackedDomain, diff_violations
from tests.enumeration.replay import replay_domain

pytestmark = [pytest.mark.e2e, pytest.mark.api]

# Every domain some detector answers for — the replay set. (l1_exceptions
# is a rollup union check, not a per-detector residual; it rides the
# unit tier and is excluded here.)
_REPLAY_DOMAINS: tuple[str, ...] = tuple(
    dict.fromkeys(
        name for names in DETECTOR_DOMAINS.values() for name in names
    ),
)


def _replay_prefix(iso_prefix: str, domain: str) -> str:
    """A short, per-(worker, domain)-unique table prefix. Short so the
    longest matview suffix (~34 chars) still clears PG's 63-char
    identifier limit; hashed on the isolated prefix so parallel xdist
    workers never collide."""
    digest = hashlib.sha256(f"{iso_prefix}:{domain}".encode()).hexdigest()
    return f"d6{digest[:10]}"


@pytest.fixture(scope="module")
def replay_conn(isolated_cfg: Config) -> Iterator[SyncConnection]:
    """A WRITABLE connection for the replay (the default db-tier
    ``db_conn`` is read-only). Each domain emits + drops its own
    prefixed objects, so this one connection hosts them serially."""
    conn = connect_demo_db(isolated_cfg, read_only=False)
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("domain_name", _REPLAY_DOMAINS)
def test_domain_replays_engine_equals_residual_on_cfg_dialect(
    domain_name: str,
    isolated_cfg: Config,
    replay_conn: SyncConnection,
) -> None:
    """Re-emit the domain at the cfg dialect, load its cells, refresh,
    and assert every detector's engine set equals the residual-derived
    expectation — the DuckDB-proven law, re-proven on this engine."""
    dialect = isolated_cfg.db.dialect
    domain: PackedDomain = DOMAIN_BUILDERS[domain_name]()
    l2_path = DOMAIN_L2_PATHS[domain_name]()
    prefix = _replay_prefix(isolated_cfg.db.table_prefix, domain_name)

    engine_maps = replay_domain(
        replay_conn, domain, l2_path, prefix=prefix, dialect=dialect,
    )

    failures: list[str] = []
    checked = 0
    for check in domain.checks:
        expected = domain.expected_for(check.detector)
        engine = engine_maps[check.detector]
        # Non-vacuous: at least one domain/detector must carry a
        # violation, or the replay proves nothing.
        checked += len(expected)
        diff = diff_violations(
            engine, expected,
            label=f"{domain_name}/{check.detector} @ {dialect.value}",
        )
        if diff:
            failures.append(diff)
    assert not failures, "\n".join(failures)
    assert checked > 0, (
        f"{domain_name}: no expected violations across any detector — "
        f"the replay is vacuous"
    )
