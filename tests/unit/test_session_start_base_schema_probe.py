"""CS.13 — session_start probes for base schema before running.

Pre-CS.13, the Studio's POST /training/session-start clicked through
without error when the operator forgot to run `recon-gen schema apply
--execute` against their demo DB. The drop_v_overlay silently no-ops
on missing tables, the create_v emits empty tables, and the clone
fails opaquely or succeeds with zero rows. Operator sees no visible
indication anything's wrong.

This test pins the new behavior: `session_start` short-circuits with
`BaseSchemaMissingError` when the base prefix's transactions table
doesn't exist + emits a structured `session_start:error` event with
the actionable remedy in the payload so the live-tail UI can render
it.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Mapping
from pathlib import Path

import duckdb  # type: ignore[import-untyped]  # WHY: duckdb partial type info; use only execute/close here
import pytest

from recon_gen.common.l2.v_overlay import (
    BaseSchemaMissingError,
    _base_schema_exists,
    session_start,
)
from recon_gen.common.config import DbConfig
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


def _make_duckdb_cfg(db_path: Path) -> object:
    return make_test_config(
        db=DbConfig(
            table_prefix="cs13_probe",
            dialect=Dialect.DUCKDB,
            url=f"duckdb:///{db_path}",
        ),
    )


def test_base_schema_exists_returns_false_when_no_base() -> None:
    """Empty DB → probe returns False (the path Session Start trips on)."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "empty.duckdb"
        # Touch + close so the file exists but contains no recon-gen tables.
        duckdb.connect(str(db_path)).close()
        cfg = _make_duckdb_cfg(db_path)
        assert _base_schema_exists(cfg) is False  # type: ignore[arg-type]: cfg is structurally Config from make_test_config


def test_base_schema_exists_returns_true_when_table_present() -> None:
    """With `<prefix>_transactions` present, the probe returns True."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "seeded.duckdb"
        conn = duckdb.connect(str(db_path))
        try:
            conn.execute("CREATE TABLE cs13_probe_transactions (id VARCHAR)")
        finally:
            conn.close()
        cfg = _make_duckdb_cfg(db_path)
        assert _base_schema_exists(cfg) is True  # type: ignore[arg-type]: cfg is structurally Config from make_test_config


def test_session_start_raises_base_schema_missing_error_with_remedy() -> None:
    """The acid test — Session Start short-circuits cleanly when base
    isn't applied, emits the actionable remedy via dev_log, and raises
    the typed exception so the UI's task-error path surfaces it."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "no_base.duckdb"
        duckdb.connect(str(db_path)).close()
        cfg = _make_duckdb_cfg(db_path)
        # Minimal L2Instance — the probe fires before any L2-walk work, so
        # we only need session_start to GET that far. Use a stub: any
        # object satisfies the type-checker via the runtime path.
        from recon_gen.common.l2 import default_l2_instance  # noqa: PLC0415
        instance = default_l2_instance()

        events: list[Mapping[str, object]] = []

        async def _sink(payload: Mapping[str, object]) -> None:
            events.append(dict(payload))

        with pytest.raises(BaseSchemaMissingError) as exc_info:
            asyncio.run(
                session_start(
                    cfg,  # type: ignore[arg-type]: cfg is structurally Config from make_test_config
                    instance,
                    refresh_base=False,  # don't trigger /etl/run probe; just the schema probe
                    dev_log=_sink,
                ),
            )

        # The exception's message names the prefix + the actionable remedy.
        msg = str(exc_info.value)
        assert "cs13_probe_transactions" in msg
        assert "recon-gen schema apply --execute" in msg

        # The dev_log got a structured error event with the same remedy
        # so the live-tail UI can render the message verbatim.
        error_events = [
            e for e in events if e.get("event") == "session_start:error"
        ]
        assert len(error_events) == 1
        err = error_events[0]
        assert err["error_kind"] == "base_schema_missing"
        assert err["base_prefix"] == "cs13_probe"
        assert "recon-gen schema apply --execute" in str(err.get("remedy", ""))

        # session_start:begin fires before the probe, session_start:done
        # does NOT (we short-circuit before the work).
        kinds = [e.get("event") for e in events]
        assert "session_start:begin" in kinds
        assert "session_start:done" not in kinds
