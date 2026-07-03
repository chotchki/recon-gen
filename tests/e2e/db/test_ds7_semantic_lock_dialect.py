"""DS.7 — the semantic lock's violation set, re-checked on PG / Oracle.

The unit tier byte-compares the DuckDB lock against a fresh DuckDB emit.
This proves the stronger claim the DuckDB lock rests on: the violation
SET is dialect-INVARIANT (BZ.4 / CA.0). The matview SQL branches per
dialect, so the only way to know PG and Oracle detect the SAME violations
the committed (DuckDB-built) lock records is to build the lock against a
LIVE PG / Oracle connection and compare — which is what this does.

The comparison is on the ``violations`` section only: the
``scenario_fingerprint.dialect`` field legitimately differs (postgres /
oracle vs duckdb), and the table prefix is a per-worker throwaway — but
the violation identities are seed-derived (account ids, business days,
exact cents), so they must match byte-for-byte across engines.

One known soft spot rides the DS.4 tolerance contract: the anomaly
z-bucket is a FLOAT computation (per-pair stddev + division), so a pair
window sitting within a rounding-boundary of a sigma threshold could
bucket differently PG vs DuckDB. The demo's planted anomalies are
engineered far from thresholds (spike z ~ 400 vs the 4-sigma band), so in
practice every anomaly row is deep in its bucket — but if a band-edge row
ever diverges, the failure names it and the DS.4 contract (not this exact
gate) is where that tolerance lives.

POLICY 1: dialect from the cfg, same test local + CI.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from recon_gen.common.as_of_frame import LOCKED_ANCHOR
from recon_gen.common.config import Config
from recon_gen.common.db import SyncConnection, connect_demo_db, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema_drop_sql
from recon_gen.cli.data import _build_fresh_semantic_lock  # pyright: ignore[reportPrivateUsage]: reuse the CLI's own builder so the dialect check and the operator's --check can't diverge

pytestmark = [pytest.mark.e2e, pytest.mark.api]

_REPO = Path(__file__).parent.parent.parent.parent
_LOCKS = _REPO / "tests" / "data" / "_semantic_locks"
_INSTANCES = ("spec_example", "sasquatch_pr")


def _lock_prefix(iso_prefix: str, instance_name: str) -> str:
    """Short per-(worker, instance)-unique table prefix — the emitted
    matview names must clear PG's 63-char limit and never collide across
    parallel workers."""
    digest = hashlib.sha256(f"{iso_prefix}:{instance_name}".encode()).hexdigest()
    return f"sl{digest[:10]}"


def _violations(lock_json: str) -> dict[str, object]:
    payload = json.loads(lock_json)
    assert isinstance(payload, dict)
    violations = cast("dict[str, object]", payload)["violations"]
    assert isinstance(violations, dict)
    return cast("dict[str, object]", violations)


@pytest.mark.parametrize("instance_name", _INSTANCES)
def test_semantic_lock_violation_set_is_dialect_invariant(
    instance_name: str,
    isolated_cfg: Config,
) -> None:
    """Build the lock against the cfg dialect (PG / Oracle) and assert
    its violation set equals the committed DuckDB lock's — the
    dialect-invariance the lock's single-dialect storage relies on."""
    dialect = isolated_cfg.db.dialect
    yaml_path = _REPO / "tests" / "l2" / f"{instance_name}.yaml"
    instance = load_instance(yaml_path)
    prefix = _lock_prefix(isolated_cfg.db.table_prefix, instance_name)

    conn: SyncConnection = connect_demo_db(isolated_cfg, read_only=False)
    try:
        fresh = _build_fresh_semantic_lock(
            instance, LOCKED_ANCHOR, prefix=prefix, dialect=dialect,
            existing_conn=conn,
        )
        cur = conn.cursor()
        try:
            execute_script(
                cur,
                emit_schema_drop_sql(instance, prefix=prefix, dialect=dialect),
                dialect=dialect,
            )
        finally:
            cur.close()
        conn.commit()
    finally:
        conn.close()

    committed = (_LOCKS / f"{instance_name}.duckdb.json").read_text()
    live = _violations(fresh)
    disk = _violations(committed)

    names = sorted(set(live) | set(disk))
    mismatches: list[str] = []
    for name in names:
        live_rows = live.get(name)
        disk_rows = disk.get(name)
        if live_rows != disk_rows:
            lc = len(cast("list[object]", live_rows)) if isinstance(live_rows, list) else 0
            dc = len(cast("list[object]", disk_rows)) if isinstance(disk_rows, list) else 0
            mismatches.append(f"{name}: {dialect.value}={lc} vs duckdb-lock={dc}")
    assert not mismatches, (
        f"{instance_name}: the {dialect.value} violation set diverges from "
        f"the committed DuckDB lock — the set is NOT dialect-invariant "
        f"for:\n  " + "\n  ".join(mismatches) + "\nIf these are anomaly "
        f"band-edge rows, that tolerance lives in the DS.4 contract; any "
        f"other invariant diverging is a real per-dialect SQL bug."
    )
