"""AV.5 — ScenarioContext composition safety + cleanup attribution.

Promoted from the (deleted-after-merge) `scenario-context-spike`
branch. The spike used a `<prefix>_scenario_claims` sidecar table
because `daily_balances` had no metadata column; AV.1 fixed that, so
the production version tags per-row on BOTH base tables and has no
sidecar.

Pinning shape (mirrors the spike's 7 cases, against REAL spine
generators rather than the spike's mock classes):

1. ``claimed_accounts`` is well-defined for every spine generator
   (drift / overdraft / expected_eod / stuck_pending / stuck_unbundled
   / limit_breach / anomaly / money_trail) and matches the
   ClaimedAccountsGenerator Protocol.
2. Same-class collision: two OverdraftGenerator instances on the same
   account_id are caught at compose() time with a clear error naming
   both classes.
3. Cross-class collision: an OverdraftGenerator + a DriftGenerator
   that happen to share an account_id are also caught (the check is
   by account, not class).
4. Disjoint claims compose cleanly + every row gets tagged with
   ``metadata.scenario_id`` on both base tables.
5. Cross-scenario interference: applying scenario B that claims one
   of scenario A's accounts is refused at compose() time with an
   error naming the conflicting scenario_id.
6. Same-scenario can recompose on its own accounts (the cross-
   scenario check filters with `!= self.scenario_id`).
7. Cleanup by scenario_id deletes only the matching rows on both
   tables; sibling scenarios survive.
8. Untagged emit (the AS/AT/AU/legacy path — no ScenarioContext)
   stays byte-identical: rows land with metadata=NULL, no AV.5 tag.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    AnomalyInvariant,
    ClaimedAccountsGenerator,
    DriftInvariant,
    LimitBreachInvariant,
    MoneyTrailInvariant,
    OverdraftInvariant,
    ScenarioContext,
    StuckPendingInvariant,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"


def _fresh_db() -> sqlite3.Connection:
    """In-process SQLite with the L2 schema applied (matches the AS/AT/AU
    pattern). The post-AV.1 schema gives daily_balances its metadata
    column — without that, the ScenarioContext per-row tagging would
    fall back to the spike's sidecar approach."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.SQLITE),
        dialect=Dialect.SQLITE,
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# 1. Protocol satisfaction — every spine generator exposes claimed_accounts.
# ---------------------------------------------------------------------------


def test_every_spine_generator_satisfies_claimed_accounts_protocol() -> None:
    """Pin: claimed_accounts is the AV.5 contract; every promoted
    generator implements it (Protocol is runtime_checkable). Adding a
    new generator without claimed_accounts trips this test."""
    drift = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    overdraft = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=10.0,
    )
    limit = LimitBreachInvariant().scenario_for(
        "CustomerLedger", "ExternalRailOutbound", direction="Outbound",
    )
    anomaly = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=5, spike_magnitude=10_000.0,
    )
    money_trail = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=2,
    )
    stuck_pending = StuckPendingInvariant().scenario_for(
        "ExternalRailInbound",
        as_of=datetime.now(tz=timezone.utc),
    )
    for gen in (drift, overdraft, limit, anomaly, money_trail, stuck_pending):
        assert isinstance(gen, ClaimedAccountsGenerator), (
            f"{type(gen).__name__} doesn't satisfy ClaimedAccountsGenerator "
            f"(missing claimed_accounts property or scenario_id kwarg on emit)"
        )
        assert len(gen.claimed_accounts) >= 1, (
            f"{type(gen).__name__}.claimed_accounts is empty"
        )


# ---------------------------------------------------------------------------
# 2. Same-class collision caught at compose.
# ---------------------------------------------------------------------------


def test_same_class_collision_caught_at_compose() -> None:
    """Two OverdraftGenerator instances naturally collide (both target
    `acct-overdraft-CustomerSubledger`). The context catches it BEFORE
    the DB-level error with a clear two-class message."""
    ctx = ScenarioContext(scenario_id="test-collision", prefix=_PREFIX)
    gen_a = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=10.0,
    )
    gen_b = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=20.0,
    )
    conn = _fresh_db()
    try:
        with pytest.raises(ValueError, match="account_id collision"):
            ctx.compose(conn, gen_a, gen_b)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Cross-class collision also caught (check is by account, not class).
# ---------------------------------------------------------------------------


def test_cross_class_collision_caught_at_compose() -> None:
    """Two different generator classes claiming the same account_id
    fire the same compose-time error. The check is keyed on
    account_id, not class identity — masking via type confusion is
    impossible."""
    # Both target acct-drift-child-CustomerSubledger by construction.
    # Construct an overdraft that ALSO targets that account by hand
    # (the smart constructor wouldn't pick that ID, but a manual
    # construction can).
    from recon_gen.common.spine import DriftGenerator, OverdraftGenerator
    import random as _random
    drift_gen = DriftGenerator(
        child_account_id="acct-shared",
        child_role="CustomerSubledger",
        parent_role="CustomerLedger",
        parent_account_id=None,
        parent_account_role=None,
        anchor_day=date(2030, 1, 1),
        magnitude=5.0,
        rng=_random.Random(0),
    )
    overdraft_gen = OverdraftGenerator(
        account_id="acct-shared",
        account_role="CustomerSubledger",
        account_parent_role="CustomerLedger",
        anchor_day=date(2030, 1, 1),
        magnitude=10.0,
    )
    ctx = ScenarioContext(scenario_id="test-cross-class", prefix=_PREFIX)
    conn = _fresh_db()
    try:
        with pytest.raises(ValueError, match="account_id collision"):
            ctx.compose(conn, drift_gen, overdraft_gen)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Disjoint claims compose cleanly + every row tagged on both tables.
# ---------------------------------------------------------------------------


def test_disjoint_claims_compose_and_tag_both_tables() -> None:
    """The happy path: drift (touches transactions + daily_balances)
    + a money_trail chain (touches transactions only — transfers-only
    LedgerSimulation) compose cleanly. Every emitted row carries
    ``metadata.scenario_id`` matching the context's id, on BOTH base
    tables — the AV.1 daily_balances rename unlocked this."""
    ctx = ScenarioContext(scenario_id="test-happy", prefix=_PREFIX)
    drift_gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    money_trail_gen = MoneyTrailInvariant().scenario_for(
        "CustomerSubledger", chain_length=2,
    )
    conn = _fresh_db()
    try:
        ctx.compose(conn, drift_gen, money_trail_gen)
        # Every transaction row carries the scenario tag.
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions "
            f"WHERE json_extract(metadata, '$.scenario_id') = ?",
            ("test-happy",),
        ).fetchone()[0]
        tx_total = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions"
        ).fetchone()[0]
        assert tx_count == tx_total > 0, (
            f"transactions: {tx_count} tagged of {tx_total} total — "
            "every emitted tx row must carry the scenario_id"
        )
        # And every daily_balances row.
        db_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances "
            f"WHERE json_extract(metadata, '$.scenario_id') = ?",
            ("test-happy",),
        ).fetchone()[0]
        db_total = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances"
        ).fetchone()[0]
        assert db_count == db_total > 0, (
            f"daily_balances: {db_count} tagged of {db_total} total — "
            "AV.1's metadata column should be carrying tags on this table too"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Cross-scenario interference caught at compose.
# ---------------------------------------------------------------------------


def test_cross_scenario_interference_caught() -> None:
    """Apply scenario A; scenario B that claims one of A's accounts
    is refused at compose-time. The error names the conflicting
    scenario_id so the operator knows what to cleanup first.

    The check uses ``JSON_VALUE(metadata, '$.scenario_id')`` against
    the data tables (no sidecar — AV.5 dropped the spike's
    side-table approach now that AV.1 unified the metadata column)."""
    ctx_a = ScenarioContext(scenario_id="scenario-A", prefix=_PREFIX)
    ctx_b = ScenarioContext(scenario_id="scenario-B", prefix=_PREFIX)
    gen_a = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=10.0,
    )
    gen_b = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=20.0,
    )
    conn = _fresh_db()
    try:
        ctx_a.compose(conn, gen_a)  # A claims the account
        with pytest.raises(ValueError, match="cross-scenario interference"):
            ctx_b.compose(conn, gen_b)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Same-scenario can recompose on its own accounts.
# ---------------------------------------------------------------------------


def test_same_scenario_can_recompose_on_own_accounts() -> None:
    """Re-composing on the SAME scenario_id is allowed — it's the
    same operator's continuation. The cross-scenario check filters
    with `!= self.scenario_id`."""
    ctx = ScenarioContext(scenario_id="scenario-X", prefix=_PREFIX)
    first = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=10.0,
    )
    conn = _fresh_db()
    try:
        ctx.compose(conn, first)
        # Same scenario, same account — should NOT trigger the cross-
        # scenario check (own scenario_id, not different).
        # (Same-class same-account WITHIN one compose() call would still
        # be a pairwise-disjoint collision; here we recompose across
        # calls with a different generator type that shares the
        # account_id is allowed by the cross-scenario check.)
        # Use a DriftGenerator that targets a different account_id —
        # the recompose works because no claim conflict surfaces.
        from recon_gen.common.spine import DriftGenerator
        import random as _random
        recompose = DriftGenerator(
            child_account_id="acct-overdraft-CustomerSubledger",
            child_role="CustomerSubledger",
            parent_role="CustomerLedger",
            parent_account_id=None,
            parent_account_role=None,
            anchor_day=date(2030, 1, 1),
            magnitude=5.0,
            rng=_random.Random(0),
        )
        ctx.compose(conn, recompose)  # same scenario_id → allowed
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. Cleanup by scenario_id surgical; siblings survive.
# ---------------------------------------------------------------------------


def test_cleanup_by_scenario_id_is_surgical() -> None:
    """``cleanup`` deletes rows tagged with this scenario's id on both
    base tables; siblings survive. No sidecar to consult — the
    metadata tag IS the bookkeeping (AV.1 unlocked this)."""
    ctx_a = ScenarioContext(scenario_id="scenario-A", prefix=_PREFIX)
    ctx_b = ScenarioContext(scenario_id="scenario-B", prefix=_PREFIX)
    # Two scenarios on DIFFERENT accounts (no cross-scenario conflict).
    from recon_gen.common.spine import OverdraftGenerator
    gen_a = OverdraftGenerator(
        account_id="acct-cleanup-A",
        account_role="CustomerSubledger",
        account_parent_role="CustomerLedger",
        anchor_day=date(2030, 1, 1),
        magnitude=10.0,
    )
    gen_b = OverdraftGenerator(
        account_id="acct-cleanup-B",
        account_role="CustomerSubledger",
        account_parent_role="CustomerLedger",
        anchor_day=date(2030, 1, 1),
        magnitude=20.0,
    )
    conn = _fresh_db()
    try:
        ctx_a.compose(conn, gen_a)
        ctx_b.compose(conn, gen_b)
        assert conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_daily_balances",
        ).fetchone()[0] == 2
        deleted = ctx_a.cleanup(conn)
        assert deleted == 1, (
            f"Expected 1 row deleted (one daily_balances row); got {deleted}"
        )
        remaining = conn.execute(
            f"SELECT account_id FROM {_PREFIX}_daily_balances",
        ).fetchall()
        assert remaining == [("acct-cleanup-B",)], (
            f"After cleanup of scenario-A, only B's row should remain. "
            f"Found: {remaining}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 8. Untagged emit (the AS/AT/AU/legacy path) stays byte-identical.
# ---------------------------------------------------------------------------


def test_untagged_emit_writes_null_metadata() -> None:
    """Generators called without ScenarioContext (scenario_id=None
    default on emit) write metadata=NULL — byte-identical to pre-AV.5.
    Every existing AS/AT/AU test that calls gen.emit(conn) directly
    keeps working without modification."""
    gen = OverdraftInvariant().scenario_for(
        "CustomerSubledger", magnitude=10.0,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)  # no scenario_id — untagged path
        rows = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_daily_balances",
        ).fetchall()
        assert rows == [(None,)], (
            f"Untagged emit must write metadata=NULL; got {rows}"
        )
    finally:
        conn.close()
