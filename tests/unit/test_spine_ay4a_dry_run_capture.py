"""AY.4.a — unit tests for `dry_run_capture` + `ScenarioContext.compose(dry_run=True)`.

The dry-run capture infrastructure that AY.4.b's renderer + AY.4.c's
adapter + AY.4.d's `build_full_seed_sql` rewrite all depend on.

What's pinned:

1. **Dialect-discriminated placeholders**. `dry_run_capture(SQLITE)` →
   `?` in captured SQL; `dry_run_capture(POSTGRES)` → `%s`;
   `dry_run_capture(ORACLE)` → `:N`. Mirrors what
   `_emit_helpers._placeholder_style` reads off `type(conn).__module__`.
2. **Captured shape**. `[(sql_str, params_tuple), ...]`. Params is
   ALWAYS a tuple (normalized from list / empty / etc.) so the
   AY.4.b renderer can rely on it.
3. **`compose(dry_run=True)` returns the captured list**; live mode
   stays unchanged (returns None, commits).
4. **Pairwise-disjoint check still fires in dry-run**; cross-scenario
   check is SKIPPED (no real DB state).
5. **Type-safety guard**: passing a real conn with `dry_run=True`
   raises TypeError loudly.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.spine import (
    DriftInvariant,
    ScenarioContext,
    dry_run_capture,
)
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"


# ---------------------------------------------------------------------------
# Dialect discrimination — captured SQL uses the right placeholder style.
# ---------------------------------------------------------------------------


def test_dry_run_capture_sqlite_uses_question_mark_placeholders() -> None:
    cap = dry_run_capture(Dialect.SQLITE)
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    gen.emit(cap)
    assert cap.captured, "DriftGenerator should have emitted at least 1 row"
    sql, params = cap.captured[0]
    assert "?" in sql, f"SQLite capture should use '?'; got: {sql!r}"
    assert "%s" not in sql
    assert ":1" not in sql
    assert isinstance(params, tuple)


def test_dry_run_capture_postgres_uses_percent_s_placeholders() -> None:
    cap = dry_run_capture(Dialect.POSTGRES)
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    gen.emit(cap)
    sql, _ = cap.captured[0]
    assert "%s" in sql, f"PG capture should use '%s'; got: {sql!r}"
    assert "?" not in sql.split("VALUES")[1]  # ? may appear elsewhere; check VALUES half


def test_dry_run_capture_oracle_uses_numeric_placeholders() -> None:
    cap = dry_run_capture(Dialect.ORACLE)
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    gen.emit(cap)
    sql, _ = cap.captured[0]
    assert ":1" in sql, f"Oracle capture should use ':N'; got: {sql!r}"


def test_dry_run_capture_default_dialect_is_sqlite() -> None:
    """No-arg `dry_run_capture()` defaults to SQLite — matches the
    rest of the spine's default-dialect convention."""
    cap = dry_run_capture()
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    gen.emit(cap)
    sql, _ = cap.captured[0]
    assert "?" in sql


# ---------------------------------------------------------------------------
# Captured shape — tuple normalization + dbapi conn interface.
# ---------------------------------------------------------------------------


def test_dry_run_capture_normalizes_params_to_tuple() -> None:
    """Whether insert_tx passes a list, tuple, or empty params, the
    captured slot is always a tuple — downstream renderer can rely
    on `len(params)`."""
    cap = dry_run_capture(Dialect.SQLITE)
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    gen.emit(cap)
    for _, params in cap.captured:
        assert isinstance(params, tuple), (
            f"params should always be a tuple; got {type(params).__name__}"
        )


def test_dry_run_capture_supports_dbapi_cursor_lifecycle() -> None:
    """The fake conn must satisfy the cursor/commit/close shape
    insert_tx / insert_balance use."""
    cap = dry_run_capture(Dialect.SQLITE)
    cur = cap.cursor()
    cur.execute("SELECT 1", ())
    cur.close()  # no-op; should not raise
    cap.commit()  # no-op; should not raise
    cap.close()  # no-op; should not raise
    assert cap.captured == [("SELECT 1", ())]


def test_dry_run_capture_is_fresh_per_call() -> None:
    """Two `dry_run_capture()` calls don't share state — the AY.4
    builder needs to know each call gets a clean capture buffer."""
    a = dry_run_capture(Dialect.SQLITE)
    b = dry_run_capture(Dialect.SQLITE)
    a.cursor().execute("FAKE", ())
    assert a.captured == [("FAKE", ())]
    assert b.captured == []


# ---------------------------------------------------------------------------
# ScenarioContext.compose(dry_run=True) — the public API entry.
# ---------------------------------------------------------------------------


def _fresh_live_db() -> sqlite3.Connection:
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


def test_compose_dry_run_returns_captured_list() -> None:
    """Hitting `dry_run=True` returns the (sql, params) list; live
    mode returns None."""
    ctx = ScenarioContext(scenario_id="test-ay4a-dry", prefix=_PREFIX)
    cap = dry_run_capture(Dialect.SQLITE)
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    captured = ctx.compose(cap, gen, dry_run=True)
    assert captured is not None
    assert isinstance(captured, list)
    assert len(captured) >= 1
    # Every entry shape: (sql_str, params_tuple).
    for entry in captured:
        sql, params = entry
        assert isinstance(sql, str)
        assert isinstance(params, tuple)


def test_compose_live_mode_unchanged_returns_none() -> None:
    """Pre-AY.4.a behavior preserved: live conn + no dry_run kwarg
    returns None + emits to the real conn + commits."""
    ctx = ScenarioContext(scenario_id="test-ay4a-live", prefix=_PREFIX)
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    conn = _fresh_live_db()
    try:
        result = ctx.compose(conn, gen)
        rows = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    assert result is None
    assert rows >= 1


def test_compose_dry_run_still_runs_pairwise_disjoint_check() -> None:
    """Two generators claiming the same account_id must blow up at
    compose-time even in dry_run — the pairwise check is pure data,
    no DB needed."""
    ctx = ScenarioContext(scenario_id="test-ay4a-collide", prefix=_PREFIX)
    gen_a = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    gen_b = DriftInvariant().scenario_for("CustomerSubledger", magnitude=10.0)
    cap = dry_run_capture(Dialect.SQLITE)
    with pytest.raises(ValueError, match="account_id collision"):
        ctx.compose(cap, gen_a, gen_b, dry_run=True)


def test_compose_dry_run_skips_cross_scenario_check() -> None:
    """Cross-scenario check requires real DB state. Dry-run skips it;
    no exceptions when the capture conn has no `cursor.fetchone` shape
    for the check."""
    ctx = ScenarioContext(scenario_id="test-ay4a-cross", prefix=_PREFIX)
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    cap = dry_run_capture(Dialect.SQLITE)
    # Pre-AY.4.a, this would crash on _check_cross_scenario's
    # `cur.execute(sql, params)` against the fake conn (no fetchone).
    # Post-AY.4.a, the check is bypassed.
    captured = ctx.compose(cap, gen, dry_run=True)
    assert captured is not None and len(captured) >= 1


def test_compose_dry_run_rejects_real_conn_loudly() -> None:
    """A real SQLite conn with `dry_run=True` is a programmer error;
    surfacing as a TypeError prevents silent no-ops."""
    ctx = ScenarioContext(scenario_id="test-ay4a-type", prefix=_PREFIX)
    gen = DriftInvariant().scenario_for("CustomerSubledger", magnitude=5.0)
    conn = _fresh_live_db()
    try:
        with pytest.raises(TypeError, match="dry_run_capture"):
            ctx.compose(conn, gen, dry_run=True)
    finally:
        conn.close()
