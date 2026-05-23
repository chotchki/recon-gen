"""AS.6 — spine ⋈ live-deployed-DB agreement (the MANDATORY GATE).

The bridge between the in-process semantic correctness AS.0-7 proved
(`Invariant.detect(ViolationGenerator.emit()) ⊇ intended` on an
in-memory SQLite) and the live-rendered correctness the existing
4-way agreement test pins (`scenario_plants ⊆ direct_matview ==
QS == App2 (== PDF)`). The spine becomes the 5th party in the chain:
its `detect()` MUST agree with the deployed DB's direct matview SELECT
for every promoted invariant.

Today drift's detect is `SELECT account_id, business_day_start, drift
FROM <prefix>_drift` — same matview the existing direct-anchor reads.
Agreement is tautological by construction. The gate's REAL value: if
a future spine change adds semantic filtering to `detect` (e.g., AT.2's
anomaly thresholds the z-score at 3σ before returning), this test
catches the divergence between spine semantics and matview row-set
semantics LOUD, at deploy time.

Scope per AS.0/AS.2: the two promoted L1 invariants — `DriftInvariant`
and `LedgerDriftInvariant`. AU adds the rest; AT extends to L2's
Investigation matviews. AS.6 pins the GATE MECHANISM on the spine's
current coverage.

AR.5's hard lesson encoded: the bridge between in-process and
deployed is where divergence surfaces. This gate is MANDATORY — not
polish — because that's the exact failure mode it exists to catch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from recon_gen.common.config import Config, load_config
from recon_gen.common.db import connect_demo_db
from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_GEN_CONFIG,
    RECON_GEN_E2E,
)

# Module-level cfg load needs a live cfg yaml or env override; under
# the unit-only CI job neither exists, and `load_config` raises the
# loud-fail ValueError, taking down pytest collection. Match the rest
# of the e2e suite's RECON_GEN_E2E gate at import time.
if not RECON_GEN_E2E.get_or_none():
    pytest.skip(
        "spine live-agreement test requires RECON_GEN_E2E=1",
        allow_module_level=True,
    )

from recon_gen.common.spine import (  # noqa: E402 — post-skip imports
    DriftInvariant,
    LedgerDriftInvariant,
    Violation,
)


pytestmark = [pytest.mark.e2e, pytest.mark.api]


def _resolve_cfg() -> Config:
    """Same cfg-resolution shape as `test_dataset_sql_smoke.py`."""
    try:
        explicit_raw = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit_raw = None
    if explicit_raw is not None:
        return load_config(str(explicit_raw))
    candidates = (
        Path("config.yaml"),
        Path("run/config.yaml"),
        Path("run/config.postgres.yaml"),
        Path("run/config.oracle.yaml"),
    )
    for candidate in candidates:
        if candidate.exists():
            return load_config(str(candidate))
    raise RuntimeError(
        "no cfg yaml found; set RECON_GEN_CONFIG=<path> or place "
        "config.yaml / run/config.yaml in the cwd"
    )


_CFG = _resolve_cfg()


def _conn() -> sqlite3.Connection:  # type: ignore[return]: live PG/Oracle/SQLite — concrete return varies per dialect, no shared protocol
    """Per-test live DB connection (psycopg / oracledb / sqlite3
    depending on `_CFG.dialect`). Caller closes."""
    return connect_demo_db(_CFG)


def _violation_keys(violations: set[Violation]) -> set[tuple[str, str]]:
    """Project a Violation set to its account_id + business_day_text
    key tuple — the comparison shape both sides project to."""
    out: set[tuple[str, str]] = set()
    for v in violations:
        items = dict(v.identity)
        account_id = items.get("account_id")
        business_day = items.get("business_day")
        if account_id is None or business_day is None:
            continue
        out.add((str(account_id), str(business_day)[:10]))
    return out


def _direct_matview_keys(  # type: ignore[no-untyped-def]: conn is psycopg/oracledb/sqlite3 — no shared dbapi protocol typed in this project
    conn,
    prefix: str,
    matview_suffix: str,
) -> set[tuple[str, str]]:
    """Direct SELECT against the deployed matview — the 4-way
    agreement chain's existing anchor, projected to the same key
    shape `_violation_keys` returns."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT account_id, business_day_start "
        f"FROM {prefix}_{matview_suffix}"
    )
    return {
        (str(aid), str(bds)[:10])
        for aid, bds in cur.fetchall()  # type: ignore[misc]: dbapi cursor.fetchall returns Sequence[Sequence[Any]]; untyped at the e2e seam
    }


# ---------------------------------------------------------------------------
# The 5-way bridge — spine's detect agrees with the direct matview SELECT
# for every promoted invariant.
# ---------------------------------------------------------------------------


def test_drift_invariant_agrees_with_direct_matview() -> None:
    inv = DriftInvariant(prefix=_CFG.db_table_prefix)
    conn = _conn()
    try:
        spine_keys = _violation_keys(inv.detect(conn))
        direct_keys = _direct_matview_keys(
            conn, _CFG.db_table_prefix, "drift",
        )
    finally:
        conn.close()
    assert spine_keys == direct_keys, (
        f"DriftInvariant.detect disagrees with direct {_CFG.db_table_prefix}_drift "
        f"SELECT.\n"
        f"  spine-only: {sorted(spine_keys - direct_keys)[:5]}\n"
        f"  direct-only: {sorted(direct_keys - spine_keys)[:5]}\n"
        f"  spine count: {len(spine_keys)}, direct count: {len(direct_keys)}"
    )


def test_ledger_drift_invariant_agrees_with_direct_matview() -> None:
    inv = LedgerDriftInvariant(prefix=_CFG.db_table_prefix)
    conn = _conn()
    try:
        spine_keys = _violation_keys(inv.detect(conn))
        direct_keys = _direct_matview_keys(
            conn, _CFG.db_table_prefix, "ledger_drift",
        )
    finally:
        conn.close()
    assert spine_keys == direct_keys, (
        f"LedgerDriftInvariant.detect disagrees with direct "
        f"{_CFG.db_table_prefix}_ledger_drift SELECT.\n"
        f"  spine-only: {sorted(spine_keys - direct_keys)[:5]}\n"
        f"  direct-only: {sorted(direct_keys - spine_keys)[:5]}\n"
        f"  spine count: {len(spine_keys)}, direct count: {len(direct_keys)}"
    )
