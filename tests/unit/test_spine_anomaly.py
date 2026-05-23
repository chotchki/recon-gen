"""Unit tests for AT.1's `AnomalyInvariant` + `AnomalyGenerator`
promotion. Mirrors the AT.0 spike's assertions but against the
production-shape classes in `common/spine/anomaly.py`.

**AT.2 update**: the σ threshold moved off the detector and onto the
`AnomalyView` knob. `inv.detect(conn)` now returns ALL buckets; tests
that previously asserted only-high-sigma-rows now go through
`AnomalyView().slice(...)`. The AT.2 separation is pinned here AND in
`test_spine_anomaly_view.py` (the View's own properties).

What's pinned:

1. AnomalyInvariant satisfies the Invariant Protocol (`name`,
   `detect`); detect reads the matview returning every bucket (no
   threshold filter — that's the View's job).
2. AnomalyGenerator satisfies the ViolationGenerator Protocol; emits
   N baseline pairs + 1 spike pair.
3. scenario_for resolves sender + recipient roles; fails loud on
   missing roles.
4. The AT.0 statistical finding holds: with default
   baseline_pair_count=100, the spike fires '4+ sigma' (verified
   through the 3σ-default View slice).
5. The AT.0 honest-limit holds: with degenerate baseline (count=1),
   no anomaly fires (slice empty under 3σ default).
6. Identity round-trip — generator.intended matches detect projection.
7. Substitution-path absence (AR.5 lesson codified for AT.1).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    AnomalyInvariant,
    AnomalyView,
    Invariant,
    Violation,
    ViolationGenerator,
)
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


def _fresh_db() -> sqlite3.Connection:
    """Schema + AW config row seeded. Anomaly's matview doesn't read
    L2 yaml directly (it reads transactions), but the config row's
    `as_of` is referenced by the L1 matview's age formulas via the
    shared schema — having it populated keeps the whole emit clean."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}", l2_json=json.dumps({"rails": []}),
        as_of=datetime(2030, 1, 1, 12, 0, 0),
    )
    return conn


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Protocol satisfaction + matview name linkage.
# ---------------------------------------------------------------------------


def test_anomaly_invariant_carries_the_matview_name() -> None:
    assert AnomalyInvariant().name == "inv_pair_rolling_anomalies"


def test_anomaly_invariant_satisfies_invariant_protocol() -> None:
    assert isinstance(AnomalyInvariant(), Invariant)


def test_anomaly_generator_satisfies_violation_generator_protocol() -> None:
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
    )
    assert isinstance(gen, ViolationGenerator)


# ---------------------------------------------------------------------------
# Smart-constructor + scenario_for.
# ---------------------------------------------------------------------------


def test_scenario_for_resolves_both_roles() -> None:
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
    )
    assert gen.sender_account_role == "CustomerSubledger"
    assert gen.recipient_account_role == "CustomerSubledger"
    # Recipient must be leaf (matview filter); parent_role is set.
    assert gen.recipient_account_parent_role == "CustomerLedger"


def test_scenario_for_unknown_sender_fails_loud() -> None:
    with pytest.raises(ValueError, match="no anomaly sender-eligible"):
        AnomalyInvariant().scenario_for("NoSuchRole", "CustomerSubledger")


def test_scenario_for_unknown_recipient_fails_loud() -> None:
    # The find_internal_with_role error format for `must_be_leaf=True`
    # cases — uses the "leaf" phrase in the error message.
    with pytest.raises(
        ValueError, match="no anomaly recipient-eligible leaf",
    ):
        AnomalyInvariant().scenario_for("CustomerSubledger", "NoSuchRole")


# ---------------------------------------------------------------------------
# Emission + detection round-trip (the headline AT.1 contract).
# ---------------------------------------------------------------------------


def test_default_baseline_plus_spike_fires_anomaly() -> None:
    """With default settings (100 baseline + 100k spike), the spike
    pair gets ~9.95σ → '4+ sigma' bucket → fires under the default
    `AnomalyView(sigma_threshold=3.0)` slice."""
    inv = AnomalyInvariant()
    view = AnomalyView()  # default 3σ — matches AT.1 baked-in behaviour
    gen = inv.scenario_for("CustomerSubledger", "CustomerSubledger")

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        sliced = view.slice(inv.detect(conn))
    finally:
        conn.close()

    spike_hits = {
        v for v in sliced
        if (
            dict(v.identity).get("sender_account_id") == gen.sender_account_id
            and dict(v.identity).get("recipient_account_id")
                == gen.recipient_account_id
        )
    }
    assert spike_hits, (
        f"spike pair must fire anomaly under default 3σ slice; "
        f"sliced={sliced}"
    )


def test_detect_returns_every_bucket_unfiltered() -> None:
    """AT.2 contract: detect() returns EVERY bucket (no threshold
    filter — that's the View's job). The baseline pairs occupy
    '0-1 sigma', the spike '4+ sigma'; both surface."""
    inv = AnomalyInvariant()
    gen = inv.scenario_for("CustomerSubledger", "CustomerSubledger")

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    buckets = {dict(v.identity).get("z_bucket") for v in detected}
    # Baseline pairs are at-or-near the mean → '0-1 sigma'; spike is
    # ~9.95σ → '4+ sigma'. Both must be in the unfiltered detect set.
    assert "0-1 sigma" in buckets, (
        f"baseline pairs should be in '0-1 sigma'; got {buckets}"
    )
    assert "4+ sigma" in buckets, (
        f"spike pair should be in '4+ sigma'; got {buckets}"
    )


def test_no_spike_no_anomaly() -> None:
    """Non-violating: spike_magnitude == baseline_amount ⇒ no outlier
    ⇒ default 3σ slice empty. (detect() still returns the rows; they
    just sit in '0-1 sigma' and the View filters them out.)"""
    inv = AnomalyInvariant()
    view = AnomalyView()
    gen = inv.scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        spike_magnitude=100.0,  # ← same as baseline_amount default
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        assert view.slice(inv.detect(conn)) == set()
    finally:
        conn.close()


def test_degenerate_baseline_does_not_fire() -> None:
    """AT.0 finding: with baseline_pair_count=1, the spike's z is too
    small to fire ('0-1 sigma'). The outlier-shifts-mean effect.
    Default 3σ slice stays empty."""
    inv = AnomalyInvariant()
    view = AnomalyView()
    gen = inv.scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=1,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        assert view.slice(inv.detect(conn)) == set()
    finally:
        conn.close()


def test_generator_emit_writes_baseline_plus_spike_transactions() -> None:
    """The statistical-by-construction property: emit() writes N
    baseline pairs * 2 legs + 1 spike pair * 2 legs = (N+1)*2
    transactions. AT.1 default N=100 → 202 transactions."""
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=8,  # small for test count check
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    # 8 baseline pairs * 2 legs + 1 spike pair * 2 legs = 18
    assert tx_count == 18


# ---------------------------------------------------------------------------
# Identity round-trip + substitution-path absence (AR.5 lesson).
# ---------------------------------------------------------------------------


def test_violation_identity_matches_detect_projection() -> None:
    """The generator's intended Violation matches detect's projection
    shape (bucket-defaulting to '4+ sigma')."""
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
    )
    expected = Violation.of(
        "inv_pair_rolling_anomalies",
        sender_account_id=gen.sender_account_id,
        recipient_account_id=gen.recipient_account_id,
        window_end=gen.anchor_day,
        z_bucket="4+ sigma",
    )
    assert gen.intended == expected


def test_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    """AR.5 lesson codified for AT.1: anomaly's detect SQL has no
    `<<$param>>` substitution — no divergence risk between
    QS-bridge (typed value) and api/smoke (string literal)."""
    inv = AnomalyInvariant()
    conn = _fresh_db()
    try:
        captured: list[str] = []
        conn.set_trace_callback(captured.append)
        inv.detect(conn)
        conn.set_trace_callback(None)
    finally:
        conn.close()
    assert captured
    for sql in captured:
        assert "<<$" not in sql, (
            f"anomaly detector crossed a SQL-pushdown surface; "
            f"AR.5-style substitution-path test required.\n  sql: {sql!r}"
        )
