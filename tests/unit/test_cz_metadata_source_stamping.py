"""CZ.2 — every seed-pipeline write stamps ``metadata.source = 'training'``.

The Phase CZ design lock:

- ``cfg.etl_hook`` is the gate signal. Configured ⇒ ETL-mode (TRUNCATE
  + reseed safe). Unset ⇒ standalone-mode; the Trainer reset and
  Studio Deploy-changes paths must DELETE only provably-synthetic
  rows so they don't wipe real ETL-loaded customer data.
- The synthetic-row predicate is
  ``JSON_VALUE(metadata, '$.source') = 'training'``.
- Every seed writer MUST emit the stamp. One missing writer leaks an
  unmarked synthetic row, which gets ambiguously preserved on the
  next standalone-mode reset.

This module pins that contract end-to-end. It applies the canonical
seed pipelines against an in-memory DuckDB and asserts every row in
both base tables has ``$.source = 'training'``. Future writer additions
that skip the stamp fail loud here — the test is the gate.

Source: REPLAN CZ.2 writer inventory + Phase CZ design lock.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from recon_gen.common.db import execute_script
from recon_gen.common.l2.auto_scenario import default_scenario_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.l2.seed import (
    emit_baseline_seed,
    emit_full_seed,
)
from recon_gen.common.l2.demo_etl_gaps import emit_demo_etl_gap_sql
from recon_gen.common.spine.scenario_context import scenario_metadata
from recon_gen.common.sql import Dialect


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC_EXAMPLE_YAML = _REPO_ROOT / "tests" / "l2" / "spec_example.yaml"
_SPEC_EXAMPLE_PREFIX = "spec_example"
_ANCHOR = date(2030, 1, 1)


def _fresh_db() -> duckdb.DuckDBPyConnection:
    """Spin up an in-memory DuckDB with the spec_example schema applied."""
    conn = duckdb.connect(":memory:")
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    cur = conn.cursor()
    execute_script(
        cur,
        emit_schema(
            instance, prefix=_SPEC_EXAMPLE_PREFIX, dialect=Dialect.DUCKDB,
        ),
        dialect=Dialect.DUCKDB,
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# scenario_metadata helper — the chokepoint that every spine writer calls.
# ---------------------------------------------------------------------------


def test_scenario_metadata_stamps_source_training_when_untagged() -> None:
    """The chokepoint helper always emits ``source='training'`` even
    with no scenario_id.

    Pre-CZ.2 callers guarded the call with
    ``if scenario_id is not None else None`` and emitted SQL NULL on
    the untagged path. Post-CZ.2 the call is unconditional and the
    helper itself injects the stamp.
    """
    blob = scenario_metadata()
    assert '"source":"training"' in blob
    # No scenario_id key when not provided.
    assert "scenario_id" not in blob


def test_scenario_metadata_keeps_source_training_when_tagged() -> None:
    """When a scenario_id is threaded the source stamp stays alongside
    so cleanup can match either predicate."""
    blob = scenario_metadata("test-cz2-tagged", generator="UnitTest")
    assert '"source":"training"' in blob
    assert '"scenario_id":"test-cz2-tagged"' in blob
    assert '"generator":"UnitTest"' in blob


def test_scenario_metadata_caller_override_via_extra() -> None:
    """A caller-supplied ``source=`` in ``**extra`` overrides the default.

    Nothing in production overrides the stamp today (the public ETL
    boundary in ``common/etl.py`` deliberately doesn't call this
    helper at all — real-row absence of the tag IS the signal). The
    override path is exercised so future code can opt out explicitly
    if it ever needs to.
    """
    blob = scenario_metadata(None, source="real")
    assert '"source":"real"' in blob
    assert '"source":"training"' not in blob


# ---------------------------------------------------------------------------
# emit_baseline_seed — 90-day historical noise. The biggest row producer.
# ---------------------------------------------------------------------------


def _apply_sql(conn: duckdb.DuckDBPyConnection, sql: str) -> None:
    cur = conn.cursor()
    try:
        execute_script(cur, sql, dialect=Dialect.DUCKDB)
        conn.commit()
    finally:
        cur.close()


def test_baseline_seed_every_transaction_row_stamps_source_training() -> None:
    """Every row ``emit_baseline_seed`` writes to ``_transactions``
    carries ``metadata.$.source = 'training'``.

    Pre-CZ.2 ``_baseline_metadata`` returned rail-key/value pairs only;
    CZ.2 appended ``source='training'`` to the returned dict so every
    leg the baseline emits inherits the stamp regardless of which
    rail fired.
    """
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    conn = _fresh_db()
    try:
        sql = emit_baseline_seed(
            instance, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.DUCKDB,
        )
        _apply_sql(conn, sql)
        total = conn.execute(
            f"SELECT COUNT(*) FROM {_SPEC_EXAMPLE_PREFIX}_transactions",
        ).fetchone()
        assert total is not None and total[0] > 0, (
            "baseline must emit at least one transaction (sanity)"
        )
        unstamped = conn.execute(
            f"SELECT COUNT(*) FROM {_SPEC_EXAMPLE_PREFIX}_transactions "
            f"WHERE COALESCE("
            f"  json_extract_string(metadata, '$.source'), '<missing>'"
            f") <> 'training'",
        ).fetchone()
        assert unstamped is not None, "COUNT(*) returns one row always"
        assert unstamped[0] == 0, (
            f"every baseline transaction row must carry "
            f"$.source='training'; {unstamped[0]} rows missing the stamp"
        )
    finally:
        conn.close()


def test_baseline_seed_every_balance_row_stamps_source_training() -> None:
    """Every row ``emit_baseline_seed`` writes to ``_daily_balances``
    carries the stamp too.

    Pre-CZ.2 ``_balance_row_tuple`` hard-coded NULL for the metadata
    column. CZ.2 emits ``{"source":"training"}`` so the per-day
    materialized balance rows are equally cleanup-eligible.
    """
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    conn = _fresh_db()
    try:
        sql = emit_baseline_seed(
            instance, prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR, dialect=Dialect.DUCKDB,
        )
        _apply_sql(conn, sql)
        total = conn.execute(
            f"SELECT COUNT(*) FROM {_SPEC_EXAMPLE_PREFIX}_daily_balances",
        ).fetchone()
        assert total is not None and total[0] > 0, (
            "baseline must emit at least one daily_balances row (sanity)"
        )
        unstamped = conn.execute(
            f"SELECT COUNT(*) FROM {_SPEC_EXAMPLE_PREFIX}_daily_balances "
            f"WHERE COALESCE("
            f"  json_extract_string(metadata, '$.source'), '<missing>'"
            f") <> 'training'",
        ).fetchone()
        assert unstamped is not None, "COUNT(*) returns one row always"
        assert unstamped[0] == 0, (
            f"every baseline balance row must carry "
            f"$.source='training'; {unstamped[0]} rows missing the stamp"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# emit_full_seed — baseline + plant overlays. Walks every spine generator.
# ---------------------------------------------------------------------------


def test_full_seed_every_transaction_row_stamps_source_training() -> None:
    """The full seed pipeline — baseline + every default-scenario
    plant — stamps every emitted transaction row.

    This is the contract anti-drift gate: future writer additions
    (new spine generators, new plant kinds) fail this test if they
    skip the stamp.
    """
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    scenario = default_scenario_for(instance, today=_ANCHOR).scenario
    conn = _fresh_db()
    try:
        sql = emit_full_seed(
            instance,
            scenario,
            prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR,
            dialect=Dialect.DUCKDB,
        )
        _apply_sql(conn, sql)
        total = conn.execute(
            f"SELECT COUNT(*) FROM {_SPEC_EXAMPLE_PREFIX}_transactions",
        ).fetchone()
        assert total is not None and total[0] > 0, (
            "full seed must emit at least one transaction (sanity)"
        )
        unstamped_rows = conn.execute(
            f"SELECT id, "
            f"json_extract_string(metadata, '$.source') AS src "
            f"FROM {_SPEC_EXAMPLE_PREFIX}_transactions "
            f"WHERE COALESCE("
            f"  json_extract_string(metadata, '$.source'), '<missing>'"
            f") <> 'training' "
            f"LIMIT 20",
        ).fetchall()
        assert unstamped_rows == [], (
            f"every full-seed transaction row must carry "
            f"$.source='training'; sample of unstamped rows: "
            f"{unstamped_rows}"
        )
    finally:
        conn.close()


def test_full_seed_every_balance_row_stamps_source_training() -> None:
    """Mirror of the transaction-row gate for ``_daily_balances`` —
    every full-seed balance row (baseline materialization + spine
    generator balance writes) carries the stamp.
    """
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    scenario = default_scenario_for(instance, today=_ANCHOR).scenario
    conn = _fresh_db()
    try:
        sql = emit_full_seed(
            instance,
            scenario,
            prefix=_SPEC_EXAMPLE_PREFIX,
            anchor=_ANCHOR,
            dialect=Dialect.DUCKDB,
        )
        _apply_sql(conn, sql)
        total = conn.execute(
            f"SELECT COUNT(*) FROM {_SPEC_EXAMPLE_PREFIX}_daily_balances",
        ).fetchone()
        assert total is not None and total[0] > 0, (
            "full seed must emit daily_balances rows (sanity)"
        )
        unstamped_rows = conn.execute(
            f"SELECT account_id, "
            f"business_day_start, "
            f"json_extract_string(metadata, '$.source') AS src "
            f"FROM {_SPEC_EXAMPLE_PREFIX}_daily_balances "
            f"WHERE COALESCE("
            f"  json_extract_string(metadata, '$.source'), '<missing>'"
            f") <> 'training' "
            f"LIMIT 20",
        ).fetchall()
        assert unstamped_rows == [], (
            f"every full-seed balance row must carry "
            f"$.source='training'; sample of unstamped rows: "
            f"{unstamped_rows}"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# demo_etl_gaps — hand-rolled INSERT builders (phantom_rail / phantom_template /
# chain_orphan / missing_metadata).
# ---------------------------------------------------------------------------


def test_demo_etl_gap_rows_stamp_source_training() -> None:
    """The hand-rolled demo overlay rows (phantom_rail, phantom_template,
    chain_orphan_parent, missing_metadata) used to emit
    ``metadata = '{}'``. CZ.2 swapped that for
    ``'{"source":"training"}'`` so the standalone-mode cleanup grabs
    these rows too.

    The missing-metadata gap's contract requires the row to be missing
    the template's ``transfer_key`` (or similar) — adding ``source``
    doesn't shadow that gap because ``source`` is not a transfer_key.
    """
    from datetime import datetime
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    sql = emit_demo_etl_gap_sql(
        instance,
        prefix=_SPEC_EXAMPLE_PREFIX,
        dialect=Dialect.DUCKDB,
        anchor=datetime(2026, 5, 30, 14, 0, 0),
    )
    # Every INSERT that this module emits should carry the source stamp.
    insert_count = sql.count(f"INSERT INTO {_SPEC_EXAMPLE_PREFIX}_transactions")
    stamp_count = sql.count('{"source":"training"}')
    assert insert_count > 0, "demo overlay must emit at least one INSERT (sanity)"
    assert stamp_count == insert_count, (
        f"every demo overlay INSERT must carry the source stamp; "
        f"INSERTs={insert_count}, stamps={stamp_count}"
    )
    # No row should carry the legacy empty-JSON metadata.
    assert ", '{}')" not in sql, (
        "demo overlay rows must not emit the legacy '{}' metadata "
        "literal — CZ.2 swapped it for '{\"source\":\"training\"}'"
    )


# ---------------------------------------------------------------------------
# Spine emit helpers — every generator that builds metadata through
# scenario_metadata gets the stamp automatically. Exercise a representative
# slice end-to-end.
# ---------------------------------------------------------------------------


def test_drift_overdraft_emit_stamps_source_training_in_balances() -> None:
    """Two spine generators that write balance rows (drift +
    overdraft) stamp both their ``_transactions`` legs (drift only)
    and ``_daily_balances`` rows.
    """
    from recon_gen.common.spine import (
        DriftInvariant, OverdraftInvariant,
    )
    conn = _fresh_db()
    try:
        drift = DriftInvariant().scenario_for(
            "CustomerSubledger", magnitude=10.0,
        )
        # OverdraftInvariant claims a different default account.
        overdraft = OverdraftInvariant().scenario_for(
            "CustomerLedger", magnitude=10.0,
        )
        drift.emit(conn)
        overdraft.emit(conn)
        conn.commit()

        # Both generators emit at least one balance row.
        balance_sources = conn.execute(
            f"SELECT DISTINCT "
            f"json_extract_string(metadata, '$.source') "
            f"FROM {_SPEC_EXAMPLE_PREFIX}_daily_balances",
        ).fetchall()
        assert balance_sources == [("training",)], (
            f"every balance row must stamp source='training'; "
            f"observed: {balance_sources}"
        )

        # Drift also emits transaction legs; verify they're stamped.
        tx_count_row = conn.execute(
            f"SELECT COUNT(*) FROM {_SPEC_EXAMPLE_PREFIX}_transactions",
        ).fetchone()
        assert tx_count_row is not None
        if tx_count_row[0] > 0:
            tx_sources = conn.execute(
                f"SELECT DISTINCT "
                f"json_extract_string(metadata, '$.source') "
                f"FROM {_SPEC_EXAMPLE_PREFIX}_transactions",
            ).fetchall()
            assert tx_sources == [("training",)], (
                f"every drift transaction leg must stamp "
                f"source='training'; observed: {tx_sources}"
            )
    finally:
        conn.close()
