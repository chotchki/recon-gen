"""AS.6 + AT.5.a / CB.5 stage 2 — spine ⋈ live-deployed-DB agreement (DB tier).

Was `tests/e2e/test_spine_live_agreement.py` at the e2e root; CB.5 stage 2
moved it under `tests/e2e/db/` so it picks up the auto-applied
`@tier(Tier.DB) + @needs(Need.DOCKER)` from `tests/e2e/db/conftest.py`.
Both spine `detect()` and the direct matview SELECT are DB-tier
operations (same connection, no QS/App2/browser legs), so this stays
single-tier — there's nothing to decompose into producers + validator;
the assertion already lives inside the producer's process.

The bridge between the in-process semantic correctness AS.0-7 proved
(`Invariant.detect(ViolationGenerator.emit()) ⊇ intended` on an
in-memory SQLite) and the live-rendered correctness the existing
4-way agreement test pins (`scenario_plants ⊆ direct_matview ==
QS == App2 (== PDF)`). The spine becomes the 5th party in the chain:
its `detect()` MUST agree with the deployed DB's direct matview SELECT
for every promoted invariant.

Today the L1 invariants' `detect()` is a near-pass-through of the
matview SELECT — agreement is close to tautological by construction.
The gate's REAL value: if a spine change adds semantic filtering to
`detect` (e.g., AT.2 moved anomaly's 3σ bucket filter off the detector
onto an `AnomalyView` slice), this test catches the divergence
between spine semantics and matview row-set semantics LOUD, at deploy
time. AT.2's specific shift validates here — the detector returning
every bucket means anomaly's `spine_keys == direct_keys` for the
unfiltered matview, with no manual filter to keep in sync.

Scope:
- AS.6 — L1 invariants (drift, ledger_drift). AU.x adds the rest.
- AT.5.a — L2 invariants (anomaly, money_trail). The View-side filters
  (`AnomalyView`, `MoneyTrailView`) intentionally do NOT participate
  in this gate; they're analyst-facing slices over the detector's full
  output, separate from the matview-detector agreement.

AR.5's hard lesson encoded: the bridge between in-process and
deployed is where divergence surfaces. This gate is MANDATORY — not
polish — because that's the exact failure mode it exists to catch.
"""

from __future__ import annotations

from typing import Any

import pytest

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db


from recon_gen.common.spine import (  # noqa: E402 — post-skip imports
    AnomalyInvariant,
    DriftInvariant,
    LedgerDriftInvariant,
    MoneyTrailInvariant,
    Violation,
)


pytestmark = [pytest.mark.e2e, pytest.mark.api]
# CB.5 stage 2 — `tests/e2e/db/conftest.py` auto-applies `@tier(Tier.DB)
# + @needs(Need.DOCKER)`; the legacy `e2e` / `api` marks above stay for
# back-compat with `pytest -m api` invocations until CB.6 drops them.


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


def _direct_matview_keys(
    conn: Any,
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


def test_drift_invariant_agrees_with_direct_matview(seeded_cfg: Config) -> None:
    prefix = seeded_cfg.db.table_prefix
    inv = DriftInvariant(prefix=prefix)
    conn = connect_demo_db(seeded_cfg)
    try:
        spine_keys = _violation_keys(inv.detect(conn))
        direct_keys = _direct_matview_keys(conn, prefix, "drift")
    finally:
        conn.close()
    assert spine_keys == direct_keys, (
        f"DriftInvariant.detect disagrees with direct {prefix}_drift SELECT.\n"
        f"  spine-only: {sorted(spine_keys - direct_keys)[:5]}\n"
        f"  direct-only: {sorted(direct_keys - spine_keys)[:5]}\n"
        f"  spine count: {len(spine_keys)}, direct count: {len(direct_keys)}"
    )


def test_ledger_drift_invariant_agrees_with_direct_matview(seeded_cfg: Config) -> None:
    prefix = seeded_cfg.db.table_prefix
    inv = LedgerDriftInvariant(prefix=prefix)
    conn = connect_demo_db(seeded_cfg)
    try:
        spine_keys = _violation_keys(inv.detect(conn))
        direct_keys = _direct_matview_keys(conn, prefix, "ledger_drift")
    finally:
        conn.close()
    assert spine_keys == direct_keys, (
        f"LedgerDriftInvariant.detect disagrees with direct "
        f"{prefix}_ledger_drift SELECT.\n"
        f"  spine-only: {sorted(spine_keys - direct_keys)[:5]}\n"
        f"  direct-only: {sorted(direct_keys - spine_keys)[:5]}\n"
        f"  spine count: {len(spine_keys)}, direct count: {len(direct_keys)}"
    )


# ---------------------------------------------------------------------------
# AT.5.a — L2 invariants. Different key shape per detector; per-invariant
# projections mirror what `Violation.identity` carries.
# ---------------------------------------------------------------------------


def _anomaly_keys_from_violations(
    violations: set[Violation],
) -> set[tuple[str, str, str, str]]:
    """Project anomaly Violations to (sender, recipient, window_end,
    z_bucket) tuples. Mirrors `AnomalyInvariant.detect`'s identity
    shape."""
    out: set[tuple[str, str, str, str]] = set()
    for v in violations:
        items = dict(v.identity)
        sender = items.get("sender_account_id")
        recipient = items.get("recipient_account_id")
        window_end = items.get("window_end")
        z_bucket = items.get("z_bucket")
        if any(k is None for k in (sender, recipient, window_end, z_bucket)):
            continue
        out.add((
            str(sender), str(recipient), str(window_end)[:10], str(z_bucket),
        ))
    return out


def _direct_anomaly_matview_keys(
    conn: Any,
    prefix: str,
) -> set[tuple[str, str, str, str]]:
    """Direct SELECT against the anomaly matview, projected to match
    `_anomaly_keys_from_violations`. Reads every bucket — the AT.2
    detector contract is bucket-agnostic; the View slices later."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT sender_account_id, recipient_account_id, window_end, "
        f"z_bucket "
        f"FROM {prefix}_inv_pair_rolling_anomalies"
    )
    return {
        (str(said), str(raid), str(we)[:10], str(zb))
        for said, raid, we, zb in cur.fetchall()  # type: ignore[misc]: dbapi cursor.fetchall returns Sequence[Sequence[Any]]; untyped at the e2e seam
    }


def _money_trail_keys_from_violations(
    violations: set[Violation],
) -> set[tuple[str, str, int]]:
    """Project money_trail Violations to (root_transfer_id,
    transfer_id, depth). Mirrors `MoneyTrailInvariant.detect`."""
    out: set[tuple[str, str, int]] = set()
    for v in violations:
        items = dict(v.identity)
        root = items.get("root_transfer_id")
        tid = items.get("transfer_id")
        depth = items.get("depth")
        if any(k is None for k in (root, tid, depth)):
            continue
        out.add((str(root), str(tid), int(depth)))  # type: ignore[arg-type]: depth narrowed by the any-None check above; pyright doesn't follow the early-continue
    return out


def _direct_money_trail_matview_keys(
    conn: Any,
    prefix: str,
) -> set[tuple[str, str, int]]:
    """Direct SELECT against the money_trail matview, projected to
    match `_money_trail_keys_from_violations`."""
    cur = conn.cursor()
    cur.execute(
        f"SELECT root_transfer_id, transfer_id, depth "
        f"FROM {prefix}_inv_money_trail_edges"
    )
    return {
        (str(root), str(tid), int(d))
        for root, tid, d in cur.fetchall()  # type: ignore[misc]: dbapi cursor.fetchall returns Sequence[Sequence[Any]]; untyped at the e2e seam
    }


def test_anomaly_invariant_agrees_with_direct_matview(seeded_cfg: Config) -> None:
    """AT.5.a — the post-AT.2 detector returns every bucket (no
    `WHERE z_bucket IN (...)` filter); a direct unfiltered SELECT
    should match exactly. The View slice happens DOWNSTREAM and is
    not part of this gate."""
    prefix = seeded_cfg.db.table_prefix
    inv = AnomalyInvariant(prefix=prefix)
    conn = connect_demo_db(seeded_cfg)
    try:
        spine_keys = _anomaly_keys_from_violations(inv.detect(conn))
        direct_keys = _direct_anomaly_matview_keys(conn, prefix)
    finally:
        conn.close()
    assert spine_keys == direct_keys, (
        f"AnomalyInvariant.detect disagrees with direct "
        f"{prefix}_inv_pair_rolling_anomalies SELECT. "
        f"This often signals an accidental filter crept back onto the "
        f"detector — the AT.2 contract is detector returns every bucket; "
        f"View slices.\n"
        f"  spine-only: {sorted(spine_keys - direct_keys)[:5]}\n"
        f"  direct-only: {sorted(direct_keys - spine_keys)[:5]}\n"
        f"  spine count: {len(spine_keys)}, direct count: {len(direct_keys)}"
    )


def test_money_trail_invariant_agrees_with_direct_matview(seeded_cfg: Config) -> None:
    """AT.5.a — money_trail detector returns every edge (root + every
    descendant); a direct unfiltered SELECT should match exactly. The
    `MoneyTrailView` depth-threshold slice is downstream."""
    prefix = seeded_cfg.db.table_prefix
    inv = MoneyTrailInvariant(prefix=prefix)
    conn = connect_demo_db(seeded_cfg)
    try:
        spine_keys = _money_trail_keys_from_violations(inv.detect(conn))
        direct_keys = _direct_money_trail_matview_keys(conn, prefix)
    finally:
        conn.close()
    assert spine_keys == direct_keys, (
        f"MoneyTrailInvariant.detect disagrees with direct "
        f"{prefix}_inv_money_trail_edges SELECT.\n"
        f"  spine-only: {sorted(spine_keys - direct_keys)[:5]}\n"
        f"  direct-only: {sorted(direct_keys - spine_keys)[:5]}\n"
        f"  spine count: {len(spine_keys)}, direct count: {len(direct_keys)}"
    )
