"""Post-``demo apply`` row-count smoke for the containerized CI job (P.7).

Connects to the live demo DB resolved from cfg + L2 instance, asserts
≥1 row in every named matview the seed should populate. Catches the
class of bug where ``schema apply`` succeeds but ``data apply`` /
``data refresh`` silently produces empty matviews.

Y.2.gate.f.2 (2026-05-09): converted from the legacy
``tests/integration/verify_demo_apply.py`` CLI script. The exact-counts
arm of the CLI was dropped — only ``spec_example`` had locked counts and
CI was already calling ``--smoke``; lock-counts mode can be added back
when ``demo apply --anchor`` makes the counts deterministic across runs.

Each suffix is its own parametrized test so a single failure pinpoints
which matview is empty. Cfg-driven dialect dispatch via
``connect_demo_db`` so the same test runs against PG / Oracle / SQLite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.config import Config, load_config
from recon_gen.common.db import connect_demo_db
from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_GEN_CONFIG,
    RECON_GEN_E2E,
    RECON_GEN_TEST_L2_INSTANCE,
)
from recon_gen.common.l2 import L2Instance, load_instance

# Module-level cfg+L2 load (below) needs a live cfg yaml or env overrides;
# under the unit-only CI job neither exists, and load_config(None) raises
# the loud-fail ValueError, taking down pytest collection. Match the rest
# of the e2e suite's RECON_GEN_E2E gate at import time so collection
# cleanly skips this whole module when e2e is off.
if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "e2e tests disabled (set RECON_GEN_E2E=1)", allow_module_level=True,
    )


# Matview suffixes expected to be non-empty for any validated L2 instance.
# Excludes ``transactions`` / ``daily_balances`` from a stricter list — some
# L2s may have legitimately empty seed scenarios for either.
_SMOKE_SUFFIXES = (
    "transactions",
    "daily_balances",
    "todays_exceptions",
    "inv_money_trail_edges",
)


def _load_cfg() -> Config:
    """Same cfg-resolution pattern as ``test_dataset_sql_smoke.py``."""
    try:
        explicit = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit = None
    if explicit is not None:
        return load_config(str(explicit))
    candidates = (
        Path("config.yaml"),
        Path("run/config.yaml"),
        Path("run/config.postgres.yaml"),
        Path("run/config.oracle.yaml"),
    )
    for candidate in candidates:
        if candidate.exists():
            return load_config(str(candidate))
    return load_config(None)


def _load_l2() -> L2Instance:
    override = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


_CFG = _load_cfg()
_L2 = _load_l2()
# Z.C — db_table_prefix is a required cfg field; the operator's cfg.yaml
# carries the prefix that matches the seeded DB. (Was previously derived
# from cfg.l2_instance_prefix or l2_instance.instance, both gone.)
_PREFIX = _CFG.db_table_prefix


@pytest.fixture(scope="module")
def smoke_conn() -> Iterator[Any]:
    conn = connect_demo_db(_CFG)
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("suffix", _SMOKE_SUFFIXES)
def test_matview_has_at_least_one_row(suffix: str, smoke_conn: Any) -> None:
    """The named matview exists + carries at least one seeded row.

    Failure here = ``data apply`` / ``data refresh`` did not populate
    this matview against the live DB. Either the seed flow skipped the
    scenario, or the matview's source query produced zero rows.
    """
    table = f"{_PREFIX}_{suffix}"
    cur = smoke_conn.cursor()
    try:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            row = cur.fetchone()
        finally:
            cur.close()
    except Exception as e:  # noqa: BLE001 — every DB error class
        pytest.fail(f"{table}: query failed: {e}")
    assert row is not None and row[0] >= 1, (
        f"{table}: got {row[0] if row else 'no row'}, expected ≥1"
    )
