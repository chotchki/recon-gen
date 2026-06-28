"""CZ.6 — pre-CZ DB migration: stamp ``metadata.source`` on legacy rows.

Phase CZ's standalone-mode cleanup gate keys on
``JSON_VALUE(metadata, '$.source') = 'training'`` as the synthetic-row
predicate. CZ.2 stamps new writes; CZ.6 fills in pre-CZ rows already
sitting in the DB at upgrade time.

Two surfaces under test:

- ``count_unstamped_rows`` / ``stamp_unstamped_rows`` library helpers —
  the dialect-portable count + UPDATE shape used by both the CLI verb
  and the ``data apply --execute`` pre-flight check.
- ``recon-gen schema migrate-mark`` — explicit operator-driven CLI
  verb (defaults ``--source=training``; ``--execute`` opt-in).

The auto-mark pre-flight behavior on ``data apply --execute`` is
covered at the unit level via the helpers (CLI integration with live
DB is exercised in the higher tiers).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from click.testing import CliRunner

from recon_gen.cli import main
from recon_gen.common.config import App2Config, AwsConfig, Config, DbConfig
from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.migrate_mark import (
    count_unstamped_rows,
    stamp_unstamped_rows,
)
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.sql import Dialect


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC_EXAMPLE_YAML = _REPO_ROOT / "tests" / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"


def _fresh_db() -> duckdb.DuckDBPyConnection:
    """Spin up an in-memory DuckDB with the spec_example schema applied."""
    conn = duckdb.connect(":memory:")
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    cur = conn.cursor()
    execute_script(
        cur,
        emit_schema(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    conn.commit()
    cur.close()
    return conn


def _insert_pre_cz_transaction(
    conn: duckdb.DuckDBPyConnection,
    *,
    tx_id: str,
    metadata: str | None,
) -> None:
    """INSERT a pre-CZ-shaped transaction row with the given metadata.

    Pre-CZ rows have either NULL metadata (``_balance_row_tuple``'s old
    hard-coded NULL path), ``'{}'`` empty metadata, or a non-empty JSON
    object missing the ``source`` key (``_baseline_metadata``'s rail-
    key-only path).
    """
    posting = datetime(2030, 1, 1, 12, 0, 0)
    conn.execute(
        f"INSERT INTO {_PREFIX}_transactions ("
        "  id, account_id, account_scope, amount_money, amount_direction, "
        "  status, posting, transfer_id, rail_name, origin, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tx_id, "acct-1", "internal", 100, "Credit",
            "Posted", posting, f"xfer-{tx_id}", "rail-A", "TEST", metadata,
        ),
    )


def _insert_pre_cz_balance(
    conn: duckdb.DuckDBPyConnection,
    *,
    account_id: str,
    metadata: str | None,
) -> None:
    """INSERT a pre-CZ-shaped daily_balances row with the given metadata."""
    start = datetime(2030, 1, 1, 0, 0, 0)
    end = datetime(2030, 1, 1, 23, 59, 59)
    conn.execute(
        f"INSERT INTO {_PREFIX}_daily_balances ("
        "  account_id, account_scope, business_day_start, business_day_end, "
        "  money, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, "internal", start, end, 0, metadata),
    )


def _source_value(metadata_str: str | None) -> str | None:
    """Helper: decode metadata + return ``$.source`` if present."""
    if metadata_str is None:
        return None
    parsed: object
    try:
        parsed = json.loads(metadata_str)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    # json.loads returns dict[Any, Any]; collapse values to object.
    # WHY: heterogenous JSON value types narrow to `object` at the boundary.
    parsed_typed = cast(dict[Any, Any], parsed)
    typed: dict[str, object] = {
        str(k): v for k, v in parsed_typed.items()
    }
    val = typed.get("source")
    return str(val) if val is not None else None


# ---------------------------------------------------------------------------
# count_unstamped_rows
# ---------------------------------------------------------------------------


def test_count_returns_zero_on_virgin_db() -> None:
    """A schema-applied DB with no rows has zero unstamped rows."""
    conn = _fresh_db()
    try:
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 0
        assert bal == 0
    finally:
        conn.close()


def test_count_catches_null_metadata() -> None:
    """Pre-CZ ``_balance_row_tuple`` wrote NULL metadata; that's the
    most common pre-CZ shape and must be counted as unstamped."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(conn, tx_id="t1", metadata=None)
        _insert_pre_cz_balance(conn, account_id="acct-1", metadata=None)
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 1
        assert bal == 1
    finally:
        conn.close()


def test_count_catches_empty_object_metadata() -> None:
    """Pre-CZ ``demo_etl_gaps`` hand-rolled '{}' on a few rows; must
    also count as unstamped — the predicate is on the ``$.source``
    leaf, not on the object's emptiness."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(conn, tx_id="t1", metadata="{}")
        _insert_pre_cz_balance(conn, account_id="acct-1", metadata="{}")
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 1
        assert bal == 1
    finally:
        conn.close()


def test_count_catches_other_keys_but_missing_source() -> None:
    """Pre-CZ ``_baseline_metadata`` wrote per-rail keys without the
    ``source`` stamp; must also count as unstamped."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(
            conn, tx_id="t1", metadata='{"transfer_key":"abc"}',
        )
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 1
        assert bal == 0
    finally:
        conn.close()


def test_count_skips_already_stamped_rows() -> None:
    """Post-CZ rows that carry ``$.source=training`` are NOT counted."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(
            conn, tx_id="t1",
            metadata='{"source":"training","transfer_key":"abc"}',
        )
        _insert_pre_cz_balance(
            conn, account_id="acct-1",
            metadata='{"source":"training"}',
        )
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 0
        assert bal == 0
    finally:
        conn.close()


def test_count_skips_rows_marked_real_too() -> None:
    """Rows the integrator stamped as ``source=real`` are NOT counted —
    the predicate is "$.source is set" not "$.source = training". Real
    rows survive standalone-mode cleanup precisely because they carry
    the source stamp."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(
            conn, tx_id="t1", metadata='{"source":"real"}',
        )
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 0
        assert bal == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# stamp_unstamped_rows
# ---------------------------------------------------------------------------


def test_stamp_fills_null_metadata_with_bare_source() -> None:
    """A NULL-metadata row gets ``'{"source":"training"}'`` written."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(conn, tx_id="t1", metadata=None)
        _insert_pre_cz_balance(conn, account_id="acct-1", metadata=None)
        tx_updated, bal_updated = stamp_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        conn.commit()
        assert tx_updated == 1
        assert bal_updated == 1
        row = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions WHERE id='t1'"
        ).fetchone()
        assert row is not None
        assert _source_value(row[0]) == "training"
        row = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_daily_balances "
            "WHERE account_id='acct-1'"
        ).fetchone()
        assert row is not None
        assert _source_value(row[0]) == "training"
    finally:
        conn.close()


def test_stamp_preserves_existing_metadata_keys() -> None:
    """A pre-CZ row with ``{"transfer_key":"abc"}`` becomes
    ``{"source":"training","transfer_key":"abc"}`` — existing keys
    survive the merge so L2FT cascade filters keep working."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(
            conn, tx_id="t1",
            metadata='{"transfer_key":"abc","other":"value"}',
        )
        stamp_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        conn.commit()
        row = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions WHERE id='t1'"
        ).fetchone()
        assert row is not None
        parsed = json.loads(row[0])
        assert parsed["source"] == "training"
        assert parsed["transfer_key"] == "abc"
        assert parsed["other"] == "value"
    finally:
        conn.close()


def test_stamp_is_idempotent() -> None:
    """Running stamp twice on the same DB updates 0 rows the second
    time — the post-CZ.2 contract test gate (CZ.2 stamps new writes)
    is preserved (no double-stamping, no infinite re-run cost)."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(conn, tx_id="t1", metadata=None)
        _insert_pre_cz_balance(conn, account_id="acct-1", metadata=None)
        first_tx, first_bal = stamp_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        conn.commit()
        assert first_tx == 1 and first_bal == 1
        second_tx, second_bal = stamp_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        conn.commit()
        assert second_tx == 0
        assert second_bal == 0
    finally:
        conn.close()


def test_stamp_supports_custom_source_for_real_rows() -> None:
    """``--source=real`` flow: an operator who loaded real ETL data
    before CZ landed can opt out of the synthetic-row predicate by
    stamping ``source=real`` — those rows then survive standalone-mode
    cleanup."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(conn, tx_id="t1", metadata=None)
        stamp_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
            source="real",
        )
        conn.commit()
        row = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions WHERE id='t1'"
        ).fetchone()
        assert row is not None
        assert _source_value(row[0]) == "real"
    finally:
        conn.close()


def test_stamp_leaves_post_cz_rows_alone() -> None:
    """A row already carrying ``$.source=training`` is NOT re-touched
    by a second migrate run — the merge skips already-stamped rows so
    the byte-shape of the JSON blob doesn't drift on idempotent runs."""
    conn = _fresh_db()
    try:
        _insert_pre_cz_transaction(
            conn, tx_id="t1",
            metadata='{"source":"training","transfer_key":"abc"}',
        )
        tx_updated, _ = stamp_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        conn.commit()
        assert tx_updated == 0
        row = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions WHERE id='t1'"
        ).fetchone()
        assert row is not None
        # Byte-identical to what was inserted.
        assert (
            row[0] == '{"source":"training","transfer_key":"abc"}'
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI: `recon-gen schema migrate-mark`
# ---------------------------------------------------------------------------


def _write_cfg_for_duckdb(
    tmp_path: Path, *, db_url: str, etl_hook: str | None,
) -> Path:
    """Emit a minimal cfg.yaml pointing at a DuckDB file with the
    given etl_hook gate state.

    Returns the path to the yaml — caller passes via ``-c``.
    """
    lines = [
        "aws:",
        "  account_id: '111111111111'",
        "  region: us-east-1",
        "  deployment_name: cz6-test",
        "db:",
        "  dialect: duckdb",
        f"  url: {db_url}",
        "  table_prefix: spec_example",
    ]
    if etl_hook is not None:
        lines += ["app2:", f"  etl_hook: {etl_hook}"]
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("\n".join(lines) + "\n")
    return cfg_path


def _seed_unstamped_db(db_path: Path) -> None:
    """Apply schema + insert a few pre-CZ-shaped rows on a DuckDB file."""
    conn = duckdb.connect(str(db_path))
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    conn.commit()
    cur.close()
    _insert_pre_cz_transaction(conn, tx_id="t1", metadata=None)
    _insert_pre_cz_transaction(conn, tx_id="t2", metadata='{"other":"val"}')
    _insert_pre_cz_balance(conn, account_id="acct-1", metadata=None)
    conn.commit()
    conn.close()


def test_cli_migrate_mark_dry_run_does_not_write(tmp_path: Path) -> None:
    """Without ``--execute``, the verb prints what it would do and
    leaves the DB untouched."""
    db_path = tmp_path / "cz6.duckdb"
    _seed_unstamped_db(db_path)
    cfg_path = _write_cfg_for_duckdb(
        tmp_path, db_url=f"duckdb:///{db_path}", etl_hook=None,
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["schema", "migrate-mark", "-c", str(cfg_path)],
    )
    assert result.exit_code == 0, result.output
    assert "found" in (result.output + result.stderr)
    assert "dry-run" in (result.output + result.stderr)

    # Verify DB untouched — counts still show pre-CZ rows.
    conn = duckdb.connect(str(db_path))
    try:
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 2
        assert bal == 1
    finally:
        conn.close()


def test_cli_migrate_mark_execute_stamps_rows(tmp_path: Path) -> None:
    """With ``--execute``, the verb stamps every unstamped row."""
    db_path = tmp_path / "cz6.duckdb"
    _seed_unstamped_db(db_path)
    cfg_path = _write_cfg_for_duckdb(
        tmp_path, db_url=f"duckdb:///{db_path}", etl_hook=None,
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["schema", "migrate-mark", "-c", str(cfg_path), "--execute"],
    )
    assert result.exit_code == 0, result.output

    conn = duckdb.connect(str(db_path))
    try:
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 0
        assert bal == 0
        # Existing keys preserved.
        row = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions WHERE id='t2'"
        ).fetchone()
        assert row is not None
        parsed = json.loads(row[0])
        assert parsed["source"] == "training"
        assert parsed["other"] == "val"
    finally:
        conn.close()


def test_cli_migrate_mark_custom_source(tmp_path: Path) -> None:
    """``--source=real`` writes the operator-supplied value verbatim
    rather than the ``training`` default."""
    db_path = tmp_path / "cz6.duckdb"
    _seed_unstamped_db(db_path)
    cfg_path = _write_cfg_for_duckdb(
        tmp_path, db_url=f"duckdb:///{db_path}", etl_hook=None,
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "schema", "migrate-mark", "-c", str(cfg_path),
            "--source", "real", "--execute",
        ],
    )
    assert result.exit_code == 0, result.output

    conn = duckdb.connect(str(db_path))
    try:
        row = conn.execute(
            f"SELECT metadata FROM {_PREFIX}_transactions WHERE id='t1'"
        ).fetchone()
        assert row is not None
        assert _source_value(row[0]) == "real"
    finally:
        conn.close()


def test_cli_migrate_mark_no_op_on_clean_db(tmp_path: Path) -> None:
    """Idempotent verb: running on an already-stamped DB exits 0 with
    a "nothing to do" hint and no DB writes."""
    db_path = tmp_path / "cz6.duckdb"
    _seed_unstamped_db(db_path)
    cfg_path = _write_cfg_for_duckdb(
        tmp_path, db_url=f"duckdb:///{db_path}", etl_hook=None,
    )
    runner = CliRunner()
    # First run to stamp.
    result = runner.invoke(
        main,
        ["schema", "migrate-mark", "-c", str(cfg_path), "--execute"],
    )
    assert result.exit_code == 0, result.output
    # Second run: nothing to do.
    result = runner.invoke(
        main,
        ["schema", "migrate-mark", "-c", str(cfg_path), "--execute"],
    )
    assert result.exit_code == 0, result.output
    assert "nothing to do" in (result.output + result.stderr)


# ---------------------------------------------------------------------------
# data apply --execute pre-flight (CZ.6 auto-mark)
# ---------------------------------------------------------------------------


def test_pre_flight_auto_marks_when_etl_hook_is_none(
    tmp_path: Path,
) -> None:
    """Standalone-mode (``cfg.app2.etl_hook is None``): pre-flight auto-
    marks pre-CZ rows with ``training`` before continuing the apply.

    Tests the helper directly via ``_cz6_pre_flight_migrate_mark`` —
    full ``data apply --execute`` integration is exercised at higher
    tiers (this verifies the gate decision + the UPDATE shape).
    """
    from recon_gen.cli.data import _cz6_pre_flight_migrate_mark
    db_path = tmp_path / "cz6.duckdb"
    _seed_unstamped_db(db_path)
    cfg = Config(
        aws=AwsConfig(deployment_name="cz6-test"),
        db=DbConfig(table_prefix=_PREFIX, url=f"duckdb:///{db_path}", dialect=Dialect.DUCKDB),
        app2=App2Config(etl_hook=None),  # standalone mode
    )
    _cz6_pre_flight_migrate_mark(cfg)

    # Verify the rows are stamped.
    conn = duckdb.connect(str(db_path))
    try:
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 0
        assert bal == 0
    finally:
        conn.close()


def test_pre_flight_refuses_auto_mark_when_etl_hook_configured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """ETL mode (``cfg.app2.etl_hook is not None``): pre-flight detects the
    pre-CZ rows, logs a hint, but does NOT auto-mark. Those rows might
    be real customer data — the operator must run the explicit verb
    to choose ``--source=training`` (synthetic) or ``--source=real``
    (preserve)."""
    from recon_gen.cli.data import _cz6_pre_flight_migrate_mark
    db_path = tmp_path / "cz6.duckdb"
    _seed_unstamped_db(db_path)
    cfg = Config(
        aws=AwsConfig(deployment_name="cz6-test"),
        db=DbConfig(table_prefix=_PREFIX, url=f"duckdb:///{db_path}", dialect=Dialect.DUCKDB),
        app2=App2Config(etl_hook="/bin/true"),  # ETL mode (configured)
    )
    _cz6_pre_flight_migrate_mark(cfg)
    captured = capsys.readouterr()
    assert "NOT auto-marking" in captured.err
    assert "migrate-mark" in captured.err

    # DB untouched — rows still unstamped.
    conn = duckdb.connect(str(db_path))
    try:
        tx, bal = count_unstamped_rows(
            conn, prefix=_PREFIX, dialect=Dialect.DUCKDB,
        )
        assert tx == 2
        assert bal == 1
    finally:
        conn.close()


def test_pre_flight_silent_no_op_on_clean_db(tmp_path: Path) -> None:
    """Common steady-state path: clean (already-stamped) DB → pre-
    flight returns silently with no side effects."""
    from recon_gen.cli.data import _cz6_pre_flight_migrate_mark
    db_path = tmp_path / "cz6.duckdb"
    # Apply schema only — no pre-CZ rows.
    conn = duckdb.connect(str(db_path))
    instance = load_instance(_SPEC_EXAMPLE_YAML)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    conn.commit()
    cur.close()
    conn.close()

    cfg = Config(
        aws=AwsConfig(deployment_name="cz6-test"),
        db=DbConfig(table_prefix=_PREFIX, url=f"duckdb:///{db_path}", dialect=Dialect.DUCKDB),
        app2=App2Config(etl_hook=None),
    )
    # Should not raise; should not change anything.
    _cz6_pre_flight_migrate_mark(cfg)


def test_pre_flight_silent_when_base_tables_missing(
    tmp_path: Path,
) -> None:
    """Virgin DB (no schema applied yet): pre-flight swallows the
    catalog error and returns silently — the seed apply that follows
    will create the rows correctly stamped by CZ.2."""
    from recon_gen.cli.data import _cz6_pre_flight_migrate_mark
    db_path = tmp_path / "cz6.duckdb"
    # Create the file but DO NOT apply the schema.
    duckdb.connect(str(db_path)).close()

    cfg = Config(
        aws=AwsConfig(deployment_name="cz6-test"),
        db=DbConfig(table_prefix=_PREFIX, url=f"duckdb:///{db_path}", dialect=Dialect.DUCKDB),
        app2=App2Config(etl_hook=None),
    )
    # Should not raise.
    _cz6_pre_flight_migrate_mark(cfg)


# ---------------------------------------------------------------------------
# CZ.6.1 — deploy_pipeline.step_2_wipe auto-mark call site
# ---------------------------------------------------------------------------
#
# step_2_wipe is the third call site for the pre-CZ auto-mark
# (alongside the explicit ``schema migrate-mark`` verb and the
# ``data apply --execute`` pre-flight). It fires AFTER the schema-
# emit probe (which sets ``schema_emitted``) but BEFORE the wipe SQL
# runs — so that on a populated pre-CZ DB the subsequent
# synthetic_only wipe correctly DELETEs the now-stamped rows (the
# WHERE narrows to ``metadata.source='training'``).
#
# Gated on ``cfg.app2.etl_hook is None`` per the CZ.0 REPLAN locked
# decision: in ETL mode the unstamped rows may be real customer data;
# operator must run the explicit verb to choose --source.
#
# These tests exercise the integration through ``step_2_wipe`` — the
# helper-level count + stamp shapes are covered above.


def _load_spec_example() -> "Any":
    """Local alias for the bundled L2 fixture so the helper functions
    below don't need to be parameterized over loader paths."""
    return load_instance(_SPEC_EXAMPLE_YAML)


def _step_2_wipe_cfg(db_path: Path, *, etl_hook: str | None) -> Config:
    """Config bound to a DuckDB tempfile + the requested etl_hook state."""
    return Config(
        aws=AwsConfig(deployment_name="cz6-test"),
        db=DbConfig(table_prefix=_PREFIX, url=f"duckdb:///{db_path}", dialect=Dialect.DUCKDB),
        app2=App2Config(etl_hook=etl_hook),
    )


def test_step_2_wipe_auto_marks_when_etl_hook_is_none(
    tmp_path: Path,
) -> None:
    """Standalone-mode + populated DB with pre-CZ rows → step_2_wipe
    auto-stamps them BEFORE the wipe SQL runs.

    This is the CZ.6.1 substantive case: without auto-mark, a
    Trainer-reset / Studio Deploy-changes synthetic_only wipe would
    skip the unstamped rows entirely (WHERE narrows to
    ``metadata.source='training'``), leaving them to pollute the
    standalone-mode invariant gate. Auto-mark stamps them so the
    narrowed DELETE catches them.
    """
    import asyncio as _asyncio

    from recon_gen.common.l2.deploy_pipeline import step_2_wipe

    db_path = tmp_path / "cz61.duckdb"
    _seed_unstamped_db(db_path)
    cfg = _step_2_wipe_cfg(db_path, etl_hook=None)
    instance = _load_spec_example()

    events: list[Mapping[str, object]] = []

    async def _capture(payload: Mapping[str, object]) -> None:
        events.append(dict(payload))

    _asyncio.run(step_2_wipe(cfg, instance, dev_log=_capture))

    # Sanity: the migrate_mark event surfaces with the stamped counts
    # so the operator's live-tail shows what happened.
    mm_events = [
        e for e in events if e.get("event") == "deploy:step2:migrate_mark"
    ]
    assert len(mm_events) == 1, (
        f"expected exactly one deploy:step2:migrate_mark event, "
        f"got events={[e.get('event') for e in events]}"
    )
    assert mm_events[0]["transactions_stamped"] == 2
    assert mm_events[0]["daily_balances_stamped"] == 1
    assert mm_events[0]["source"] == "training"

    # Post-condition: every row is now stamped (count_unstamped is 0).
    # The wipe SQL (synthetic_only=False default) then emptied both
    # tables, but the assertion above proves the auto-mark fired
    # before that — re-seed + re-count would just show 0 from the
    # empty tables. So re-run count on the actual updated rows by
    # seeding again with already-stamped rows is overkill; the event
    # payload is the contract.


def test_step_2_wipe_skips_auto_mark_when_etl_hook_configured(
    tmp_path: Path,
) -> None:
    """ETL mode (``cfg.app2.etl_hook is not None``) → step_2_wipe does NOT
    auto-mark pre-CZ rows + emits no migrate_mark event.

    Those rows may be real customer data that the integrator's etl_hook
    loaded before the CZ upgrade; auto-stamping them ``training`` would
    silently make them eligible for synthetic_only deletion. Operator
    must opt in via the explicit ``schema migrate-mark`` verb.
    """
    import asyncio as _asyncio

    from recon_gen.common.l2.deploy_pipeline import step_2_wipe

    db_path = tmp_path / "cz61.duckdb"
    _seed_unstamped_db(db_path)
    cfg = _step_2_wipe_cfg(db_path, etl_hook="/bin/true")
    instance = _load_spec_example()

    events: list[Mapping[str, object]] = []

    async def _capture(payload: Mapping[str, object]) -> None:
        events.append(dict(payload))

    _asyncio.run(step_2_wipe(cfg, instance, dev_log=_capture))

    # No migrate_mark event — the auto-mark was skipped entirely.
    mm_events = [
        e for e in events if e.get("event") == "deploy:step2:migrate_mark"
    ]
    assert mm_events == [], (
        f"ETL-mode step_2_wipe should NOT auto-mark; got {mm_events}"
    )

    # Pre-CZ rows were eligible for the full TRUNCATE-style default
    # wipe (synthetic_only=False), so the table is empty afterward —
    # but they were never STAMPED. Verifying the "no stamp" half:
    # re-seed + count_unstamped should still see them all unstamped.
    # (Easier: re-seed a fresh DB + repeat the run with the dev_log
    # off to spot-check. The event-absence above is the contract.)


def test_step_2_wipe_no_op_when_no_pre_cz_rows(
    tmp_path: Path,
) -> None:
    """Steady-state path: every row already carries metadata.source →
    step_2_wipe's auto-mark counts (0, 0), runs no UPDATE, and emits
    no migrate_mark event (no log noise on the common path)."""
    import asyncio as _asyncio

    from recon_gen.common.l2.deploy_pipeline import step_2_wipe

    db_path = tmp_path / "cz61.duckdb"
    # Apply schema + plant ONE properly-stamped row per base table so
    # the wipe has something to delete (rules out "no rows ⇒ trivial").
    conn = duckdb.connect(str(db_path))
    instance = _load_spec_example()
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    conn.commit()
    cur.close()
    # Stamped rows — these DO carry metadata.source.
    _insert_pre_cz_transaction(
        conn, tx_id="t1", metadata='{"source":"training"}',
    )
    _insert_pre_cz_balance(
        conn, account_id="acct-1", metadata='{"source":"training"}',
    )
    conn.commit()
    conn.close()

    cfg = _step_2_wipe_cfg(db_path, etl_hook=None)

    events: list[Mapping[str, object]] = []

    async def _capture(payload: Mapping[str, object]) -> None:
        events.append(dict(payload))

    _asyncio.run(step_2_wipe(cfg, instance, dev_log=_capture))

    # No migrate_mark event — quiet steady-state.
    mm_events = [
        e for e in events if e.get("event") == "deploy:step2:migrate_mark"
    ]
    assert mm_events == [], (
        f"clean DB should produce no migrate_mark event; got {mm_events}"
    )
    # And the wipe still fired normally — done event surfaces.
    done_events = [
        e for e in events if e.get("event") == "deploy:step2:wipe:done"
    ]
    assert len(done_events) == 1
