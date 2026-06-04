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

CB.17.d (2026-06-04) — migrated from module-import ``_CFG = _load_cfg()``
to the ``seeded_cfg`` fixture, which wraps ``isolated_cfg`` with
``apply_db_seed`` and yields a per-(module, worker) seeded prefix.
Each xdist worker has its own prefix; the schema-drop in
``isolated_cfg``'s teardown handles cleanup.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db
from recon_gen.common.env_keys import RECON_GEN_E2E

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
    "l1_exceptions",
    "inv_money_trail_edges",
)


@pytest.fixture(scope="module")
def smoke_conn(seeded_cfg: Config) -> Iterator[Any]:
    conn = connect_demo_db(seeded_cfg)
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("suffix", _SMOKE_SUFFIXES)
def test_matview_has_at_least_one_row(
    suffix: str, smoke_conn: Any, seeded_cfg: Config,
) -> None:
    """The named matview exists + carries at least one seeded row.

    Failure here = ``data apply`` / ``data refresh`` did not populate
    this matview against the live DB. Either the seed flow skipped the
    scenario, or the matview's source query produced zero rows.
    """
    table = f"{seeded_cfg.db_table_prefix}_{suffix}"
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
