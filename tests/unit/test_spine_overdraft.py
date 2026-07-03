"""Unit tests for the AU.1 overdraft family + registry edges.

Two layers of assertion (mirroring `test_spine_drift.py`'s shape):
1. `OverdraftInvariant` + `OverdraftGenerator` each behave as designed
   against the real emitted matview SQL (in-process SQLite harness).
2. The `INVARIANT_GENERATOR_EDGES` registry's two-edge entry for
   `OverdraftGenerator` is empirical: the test re-derives the edge set
   from actual `detect()` calls after emission and asserts the
   registry matches. AU.0's finding (overdraft-on-leaf ALSO trips
   drift) is locked in as a property, not just docs prose.

The substitution-path property test (AR.5 lesson codified for every
promoted detector) lands here for overdraft — same shape as drift's
spike test, just `OverdraftInvariant().detect()` as the subject.
"""

from __future__ import annotations

import duckdb

from tests.unit._spine_sql_capture import record_sql
from pathlib import Path

import pytest

from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    INVARIANT_GENERATOR_EDGES,
    DriftInvariant,
    Invariant,
    LedgerDriftInvariant,
    OverdraftGenerator,
    OverdraftInvariant,
    Violation,
    generators_for,
    invariants_for,
    iter_edges,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.DUCKDB


def _fresh_db() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


def _refresh(conn: duckdb.DuckDBPyConnection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# OverdraftInvariant — detect from `<prefix>_overdraft`; scenario_for shape.
# ---------------------------------------------------------------------------


def test_overdraft_invariant_carries_the_matview_name() -> None:
    # The spine link: `Invariant.name` matches the matview suffix.
    assert OverdraftInvariant().name == "overdraft"


def test_overdraft_scenario_for_resolves_role_against_the_shape() -> None:
    # The smart constructor: a shape selector (role name) in, concrete
    # coordinates (account_id, account_role, account_parent_role) out.
    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    assert gen.account_role == "CustomerSubledger"
    # CustomerSubledger is a leaf (has parent_role=CustomerLedger) in
    # spec_example, so the empirical drift edge is live for this gen.
    assert gen.account_parent_role == "CustomerLedger"
    assert gen.magnitude == 5.0


def test_overdraft_scenario_for_resolves_parent_role_account() -> None:
    # Overdraft fires on ANY internal account (parent or leaf) — unlike
    # drift, which requires parent_role IS NOT NULL. Confirm the smart
    # constructor accepts a parent role too.
    gen = OverdraftInvariant().scenario_for("CustomerLedger", magnitude=5.0)
    assert gen.account_role == "CustomerLedger"
    # CustomerLedger has no parent_role — it's a parent account itself.
    assert gen.account_parent_role is None


def test_overdraft_scenario_for_unknown_role_fails_loud() -> None:
    # Smart-constructor discipline matching drift's: unknown role ⇒
    # ValueError at request time, no silent inert emission.
    with pytest.raises(ValueError, match="no overdraft-eligible"):
        OverdraftInvariant().scenario_for("NoSuchRole", magnitude=5.0)


# ---------------------------------------------------------------------------
# OverdraftGenerator — emission trips OverdraftInvariant.
# ---------------------------------------------------------------------------


def test_overdraft_generator_trips_overdraft_invariant() -> None:
    inv = OverdraftInvariant()
    gen = inv.scenario_for("CustomerSubledger", magnitude=5.0)
    intended = gen.intended

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    assert intended in detected, (
        f"OverdraftInvariant.detect did not include the intended "
        f"violation.\n  intended: {intended}\n  detected: {detected}"
    )


def test_overdraft_generator_magnitude_zero_does_not_fire() -> None:
    # AP.2 non-violating convention: magnitude=0 ⇒ stored=0 ⇒ NOT
    # < 0 ⇒ overdraft detector does not include this violation.
    inv = OverdraftInvariant()
    clean = inv.scenario_for("CustomerSubledger", magnitude=0.0)
    dirty = inv.scenario_for("CustomerSubledger", magnitude=5.0)

    conn = _fresh_db()
    try:
        clean.emit(conn)
        conn.commit()
        _refresh(conn)
        assert dirty.intended not in inv.detect(conn)
    finally:
        conn.close()


def test_overdraft_generator_sweeps_marker_txs_across_carry_window() -> None:
    # DL.3.7 — when ``as_of_day`` is set the generator emits one
    # zero-amount marker tx per business day from ``anchor_day`` through
    # ``as_of_day`` inclusive. The ``<prefix>_overdraft`` matview reads
    # ``effective_balances``'s CL.5 carry-forward, so a single plant
    # surfaces on EVERY day from its emit day onward until the next emit
    # (or window end). DL.3.1's single-marker-on-anchor-day pattern left
    # every carry day's `Overdraft Violations → Daily Statement` drill
    # landing on an empty destination — only the emit day matched.
    # Sweeping markers across the window fixes that.
    from datetime import date, timedelta
    from recon_gen.common.spine import OverdraftGenerator
    anchor = date(2030, 1, 1)
    as_of = anchor + timedelta(days=4)  # 5-day carry window: anchor + 4 carry days
    gen = OverdraftGenerator(
        account_id="acct-overdraft-CustomerSubledger",
        account_role="CustomerSubledger",
        account_parent_role="CustomerLedger",
        anchor_day=anchor,
        magnitude=5.0,
        as_of_day=as_of,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = list(conn.execute(
            f"SELECT id, amount_money, DATE_TRUNC('day', posting) AS day "
            f"FROM {_PREFIX}_transactions ORDER BY day"
        ).fetchall())
    finally:
        conn.close()
    # 5 days inclusive (anchor + 4) → 5 markers.
    assert len(rows) == 5, (
        f"DL.3.7 expected 5 marker txs (one per day in "
        f"[anchor={anchor}, as_of={as_of}]); got {len(rows)}: {rows}"
    )
    # Every marker is amount=0 (preserves L1 invariant identities).
    for tx_id, amount, _day in rows:
        assert amount == 0, (
            f"marker tx amount={amount} on {tx_id!r}; expected 0 — "
            f"non-zero would shift _computed_subledger and break "
            f"drift's `stored − Σ legs` identity"
        )
        assert str(tx_id).startswith("tx-overdraft-marker-"), (
            f"unexpected tx id {tx_id!r}; expected the "
            f"`tx-overdraft-marker-` DL.3.7 prefix"
        )
    # Days span anchor..as_of inclusive, one row per day.
    days = sorted({str(r[2])[:10] for r in rows})
    expected_days = [
        (anchor + timedelta(days=i)).isoformat() for i in range(5)
    ]
    assert days == expected_days, (
        f"DL.3.7 marker days mismatch — expected {expected_days}, "
        f"got {days}"
    )


def test_overdraft_generator_two_plants_overlapping_carry_windows_no_pk_collision() -> None:
    # DL.3.7 — densify_scenario factor=5 emits multiple OverdraftPlants
    # on the same (account_id, account_role) — e.g. days_ago=6/13/20/27/34
    # all on cust-0002-snb. Their carry windows OVERLAP (plant at days_ago=13
    # covers days_ago=13..0; plant at days_ago=6 covers days_ago=6..0; the
    # 6..0 range is shared). The DL.3.7 id includes BOTH the plant's
    # anchor day AND the coverage day so overlapping plants emit
    # non-colliding marker tx ids on shared coverage days. Without the
    # anchor-day prefix, ``_current_transactions``'s UNIQUE INDEX on
    # (id) would reject the second plant's INSERT on every shared day.
    from datetime import date
    from recon_gen.common.spine import OverdraftGenerator
    anchor_early = date(2030, 1, 1)
    anchor_late = date(2030, 1, 4)
    as_of = date(2030, 1, 7)
    gen_a = OverdraftGenerator(
        account_id="acct-shared",
        account_role="CustomerSubledger",
        account_parent_role="CustomerLedger",
        anchor_day=anchor_early,
        magnitude=5.0,
        as_of_day=as_of,
    )
    gen_b = OverdraftGenerator(
        account_id="acct-shared",
        account_role="CustomerSubledger",
        account_parent_role="CustomerLedger",
        anchor_day=anchor_late,
        magnitude=5.0,
        as_of_day=as_of,
    )
    conn = _fresh_db()
    try:
        # gen_a + gen_b share Jan 4..Jan 7 coverage; without DL.3.7's
        # anchor-day-in-id discipline this second emit would PK-collide.
        gen_a.emit(conn)
        gen_b.emit(conn)
        conn.commit()
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions"
        ).fetchone()
        assert count_row is not None, (
            "COUNT(*) returned no row — DuckDB harness misconfigured"
        )
        count = int(count_row[0])
    finally:
        conn.close()
    # gen_a: Jan 1..Jan 7 → 7 markers
    # gen_b: Jan 4..Jan 7 → 4 markers
    # No collisions → 11 total.
    assert count == 11, (
        f"DL.3.7 overlapping-window PK-collision regression — expected "
        f"11 markers (7 from gen_a + 4 from gen_b), got {count}"
    )


def test_overdraft_generator_emits_one_zero_amount_marker_tx() -> None:
    # DL.3.1 — OverdraftGenerator emits exactly ONE Posted Money Records
    # row on the same account-day as the planted overdraft, with
    # `amount_money = 0`. This is the drilldown marker that makes the
    # `Overdraft Violations → Daily Statement for this account-day`
    # drill destination non-empty WITHOUT changing the L1 invariant
    # math:
    #
    # - Overdraft matview reads `effective_money < 0` purely from
    #   daily_balances; transactions don't enter that formula.
    # - The AU.0 edge (overdraft on a leaf ALSO trips drift) survives
    #   because drift's `stored ≠ Σ posted legs` predicate stays true
    #   when amount=0 (Σ legs = 0; stored = −magnitude; −magnitude ≠ 0).
    # - `computed_subledger`'s cumulative Σ stays unchanged at +0,
    #   so other accounts' drift values are not affected by a stray
    #   non-zero leg here.
    #
    # Pinning the exact shape (count=1, amount=0, account_name matches
    # the synthetic balance row) so a future change can't silently grow
    # a *non-zero* leg that would break drift's intended-identity
    # invariant.
    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        rows = list(conn.execute(
            f"SELECT id, account_id, account_name, amount_money "
            f"FROM {_PREFIX}_transactions"
        ).fetchall())
    finally:
        conn.close()
    assert len(rows) == 1, (
        f"OverdraftGenerator emitted {len(rows)} transactions; expected 1 "
        f"(the DL.3.1 zero-amount drilldown marker)"
    )
    tx_id, _account_id, account_name, amount_money = rows[0]
    assert str(tx_id).startswith("tx-overdraft-marker-"), (
        f"unexpected tx id {tx_id!r}; expected the `tx-overdraft-marker-` "
        f"DL.3.1 marker shape"
    )
    assert amount_money == 0, (
        f"OverdraftGenerator marker tx amount={amount_money}; expected 0 "
        f"(non-zero would break drift's `Σ legs ≠ stored` AU.0 edge + "
        f"shift cumulative computed_subledger on subsequent days)"
    )
    assert "Overdraft Acct" in str(account_name), (
        f"unexpected account_name {account_name!r}; expected to match the "
        f"synthetic daily_balances row so the Daily Statement Transactions "
        f"WHERE clause matches account_display"
    )


# ---------------------------------------------------------------------------
# The AU.0 finding pinned: overdraft on a leaf ALSO trips drift.
# ---------------------------------------------------------------------------


def test_overdraft_on_leaf_also_trips_drift() -> None:
    # AU.0's spike caught this — the empirical edge to drift. The matview
    # filter `parent_role IS NOT NULL AND stored ≠ Σ legs` is satisfied
    # by the overdraft plant on a leaf account (leaf has parent_role;
    # plant emits zero transactions, so Σ legs = 0 ≠ −magnitude).
    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    drift_intended = gen.also_trips_drift
    assert drift_intended is not None, (
        "spec_example's CustomerSubledger should be a leaf account (has "
        "parent_role); overdraft on it MUST advertise the drift edge"
    )
    drift_inv = DriftInvariant()

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = drift_inv.detect(conn)
    finally:
        conn.close()
    assert drift_intended in detected, (
        f"DriftInvariant did not fire on the overdraft plant.\n"
        f"  intended: {drift_intended}\n"
        f"  detected: {detected}"
    )


def test_overdraft_on_parent_role_does_not_advertise_drift_edge() -> None:
    # The flip side of the AU.0 edge: planting overdraft on a parent
    # account (no parent_role) means drift's matview filter excludes
    # the row — `also_trips_drift` returns None to advertise this.
    gen = OverdraftInvariant().scenario_for("CustomerLedger", magnitude=5.0)
    assert gen.account_parent_role is None
    assert gen.also_trips_drift is None
    # Confirm the matview agrees — drift doesn't fire on the parent.
    drift_inv = DriftInvariant()
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = drift_inv.detect(conn)
        # No drift row for THIS account (the plant's account_id) should fire.
        assert not any(
            dict(v.identity).get("account_id") == gen.account_id
            for v in detected
        ), (
            f"DriftInvariant unexpectedly fired on a parent-role "
            f"overdraft plant for account_id={gen.account_id}; "
            f"detected={detected}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Registry — empirical contract that AU.1's two-edge entry holds.
# ---------------------------------------------------------------------------


def test_overdraft_generator_emission_fires_exactly_the_registered_edges() -> None:
    # Empirical edge contract for overdraft: re-derive the
    # (generator → {invariants}) map from actual detect() calls after
    # emission on a LEAF account (where both edges fire), then assert
    # the registry matches. If a future change adds a detect path that
    # fires on overdraft emission (a new matview, schema-shape change),
    # this assertion forces the registry update.
    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    candidate_invariants: tuple[Invariant, ...] = (
        OverdraftInvariant(),
        DriftInvariant(),
        LedgerDriftInvariant(),
    )
    fired: set[type[Invariant]] = set()

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        for inv in candidate_invariants:
            # Edge "fires" when detect() includes ≥1 Violation for THIS
            # generator's account_id. (Other plants from prior tests
            # wouldn't matter here — fresh DB — but the filter is
            # defensive against future shared-DB harness changes.)
            hits = {
                v for v in inv.detect(conn)
                if dict(v.identity).get("account_id") == gen.account_id
            }
            if hits:
                fired.add(type(inv))
    finally:
        conn.close()

    registered = set(INVARIANT_GENERATOR_EDGES[OverdraftGenerator])
    assert fired == registered, (
        f"OverdraftGenerator's empirical edges don't match the registry.\n"
        f"  fired (empirical): {sorted(c.__name__ for c in fired)}\n"
        f"  registered: {sorted(c.__name__ for c in registered)}"
    )


def test_invariants_for_overdraft_returns_two_edges() -> None:
    edges = invariants_for(OverdraftGenerator)
    assert edges == (OverdraftInvariant, DriftInvariant)


def test_generators_for_overdraft_invariant_returns_overdraft_generator() -> None:
    # OverdraftInvariant has exactly ONE generator that trips it
    # (OverdraftGenerator). DriftInvariant's two-generator reverse-lookup
    # (DriftGenerator + OverdraftGenerator post-AU.1) is asserted by
    # `test_spine_drift.py::test_generators_for_reverse_lookup`; keeping
    # the dual-source assertion in ONE place keeps the contract clear
    # and the test set non-duplicative.
    assert generators_for(OverdraftInvariant) == {OverdraftGenerator}


def test_iter_edges_includes_overdraft_edges() -> None:
    edges = list(iter_edges())
    assert (OverdraftGenerator, OverdraftInvariant) in edges
    assert (OverdraftGenerator, DriftInvariant) in edges


# ---------------------------------------------------------------------------
# Substitution-path property test (AR.5 lesson codified per detector).
# ---------------------------------------------------------------------------


def test_overdraft_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    """Per-promoted-invariant property: detect()'s SQL has no
    `<<$param>>` substitution — zero divergence risk between QS-bridge
    (typed value) and api/smoke (string literal). Mirrors drift's
    AS.0 substitution-path test."""
    inv = OverdraftInvariant()
    conn = _fresh_db()
    try:
        captured: list[str] = []
        inv.detect(record_sql(conn, captured))
    finally:
        conn.close()

    assert captured, "expected OverdraftInvariant.detect() to run ≥1 SQL"
    for sql in captured:
        assert "<<$" not in sql, (
            f"overdraft detector unexpectedly crossed a SQL-pushdown "
            f"surface; AR.5-style substitution-path test required.\n"
            f"  sql: {sql!r}"
        )


# ---------------------------------------------------------------------------
# Smoke: Violation round-trip via the smart constructor.
# ---------------------------------------------------------------------------


def test_overdraft_violation_identity_matches_detect_projection() -> None:
    # The generator's `intended` and the detector's projected Violation
    # must be EQUAL — same invariant name, same identity columns, same
    # exact-cents magnitude. Pinning this protects against drift in
    # either side's identity shape.
    gen = OverdraftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    intended = gen.intended
    # Hand-build the expected detector projection and compare.
    expected = Violation.of(
        "overdraft",
        account_id=gen.account_id,
        business_day=gen.anchor_day,
        # DS.7: exact cents — a $5.00 overdraft is -500 cents.
        stored_balance=-500,
    )
    assert intended == expected
