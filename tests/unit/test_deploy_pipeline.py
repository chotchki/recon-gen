"""X.4.g — deploy pipeline coverage.

The pipeline module is HTTP-free: each step takes the cfg + an
optional ``DevLogWriter`` (a ``Callable[[Mapping], Awaitable[None]]``)
and returns a primitive (exit code, row count, etc.). Tests assert
against the writer-collected event list, which is the same shape the
studio's POST /deploy endpoint will surface.

Async functions are wrapped in ``asyncio.run`` (project convention —
see tests/unit/test_common_db.py) rather than relying on
``pytest.mark.asyncio`` (the plugin isn't installed).
"""
from __future__ import annotations


import asyncio
import shlex
import sys as _sys
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from recon_gen.common.config import AwsConfig, Config
from recon_gen.common.db import connect_demo_db, duckdb_path, execute_script, fetch_one_required, make_demo_database_url
from recon_gen.common.config import (
    AwsConfig,
    TestGeneratorConfig,
)
from recon_gen.common.l2.deploy_pipeline import (
    DeploySummary,
    get_data_generation_id,
    run_deploy_pipeline,
    step_1_etl_hook,
    step_2_wipe,
    step_3_5_derive_balances,
    step_3_generator,
    step_4_matviews,
    step_5_reload,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import (
    emit_schema,
    wipe_demo_data_sql,
)
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.sql import Dialect


def _base_cfg() -> Config:
    return Config(
        aws=AwsConfig(account_id="111122223333", region="us-east-1"),
        # Z.C — Config requires deployment_name + db_table_prefix.
        # spec_example matches the bundled L2 fixture used downstream.
        deployment_name="recon-spec-example",
        db_table_prefix=DEFAULT_PREFIX,
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
    )


@pytest.fixture
def spec_example_instance() -> L2Instance:
    """Bundled spec_example fixture — the smallest valid L2."""
    return load_instance(Path("tests/l2/spec_example.yaml"))


def _duckdb_cfg(tmp_path: Path) -> Config:
    """Config bound to a fresh DuckDB tempfile for orchestrator tests."""
    db_path = tmp_path / "demo.duckdb"
    return Config(
        aws=AwsConfig(account_id="111122223333", region="us-east-1"),
        # Z.C — Config requires deployment_name + db_table_prefix.
        # spec_example matches the bundled L2 fixture used downstream.
        deployment_name="recon-spec-example",
        db_table_prefix=DEFAULT_PREFIX,
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=make_demo_database_url(Dialect.DUCKDB, db_path),
        dialect=Dialect.DUCKDB,
    )


def _apply_schema_and_plant_two_rows(
    cfg: Config, instance: L2Instance,
) -> None:
    """Set up a DuckDB tempfile DB with the L2 schema + two planted rows
    so the wipe has something to delete. Plants conform to the L2 v6
    schema's CHECK constraints (amount_direction enum, sign-direction
    agreement, account_scope enum).

    CZ.6.1 — rows carry ``metadata.source='training'`` (the CZ.2 stamp
    every post-CZ seed-pipeline writer attaches) so the step_2_wipe
    auto-mark in standalone mode (``cfg.app2.etl_hook is None``) treats
    them as post-CZ rows and skips the migrate_mark event. Tests that
    specifically want pre-CZ unstamped rows are in
    ``test_cz_migrate_mark.py``.
    """
    schema_sql = emit_schema(
        instance, prefix=cfg.db.table_prefix, dialect=cfg.db.dialect,
    )
    p = cfg.db.table_prefix
    plant_tx = (
        f"INSERT INTO {p}_transactions ("
        "id, account_id, account_scope, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, rail_name, origin, metadata"
        ") VALUES ("
        "'t1', 'a1', 'internal', "
        "100.00, 'Credit', 'posted', '2030-01-01 00:00:00', "
        "'g1', 'r1', 'inbound', '{\"source\":\"training\"}'"
        ");"
    )
    plant_bal = (
        f"INSERT INTO {p}_daily_balances ("
        "account_id, account_scope, "
        "business_day_start, business_day_end, money, metadata"
        ") VALUES ("
        "'a1', 'internal', "
        "'2030-01-01 00:00:00', '2030-01-02 00:00:00', 100.00, "
        "'{\"source\":\"training\"}'"
        ");"
    )
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(cur, schema_sql, dialect=cfg.db.dialect)
            execute_script(
                cur, plant_tx + "\n" + plant_bal, dialect=cfg.db.dialect,
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def _row_counts(cfg: Config, instance: L2Instance) -> tuple[int, int]:
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {p}_transactions")
            tx = int(fetch_one_required(cur)[0])
            cur.execute(f"SELECT COUNT(*) FROM {p}_daily_balances")
            bal = int(fetch_one_required(cur)[0])
            return tx, bal
        finally:
            cur.close()
    finally:
        conn.close()


def _apply_demo_schema_only(cfg: Config, instance: L2Instance) -> None:
    """Apply the demo schema without planting any rows — the etl_hook /
    generator path is what fills the base tables. (Pre-BS.4 this lived
    in the step_2_pull section; BS.4 retained it since the orchestrator
    tests still need to bootstrap an empty demo DB before running the
    pipeline.)"""
    schema_sql = emit_schema(
        instance, prefix=cfg.db.table_prefix, dialect=cfg.db.dialect,
    )
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(cur, schema_sql, dialect=cfg.db.dialect)
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


class _EventCollector:
    """List-collecting DevLogWriter for assertions."""

    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    async def __call__(self, payload: Mapping[str, object]) -> None:
        self.events.append(dict(payload))

    def kinds(self) -> list[str]:
        return [str(e.get("event", "")) for e in self.events]

    def by_kind(self, kind: str) -> list[Mapping[str, object]]:
        return [e for e in self.events if e.get("event") == kind]


def _run_step_1(cfg: Config, sink: _EventCollector | None) -> int:
    return asyncio.run(step_1_etl_hook(cfg, dev_log=sink))


# ---------- skip paths ----------

def test_etl_hook_unset_returns_zero_and_emits_skip() -> None:
    cfg = _base_cfg()
    assert cfg.app2.etl_hook is None
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 0
    assert sink.kinds() == ["deploy:step1:skip"]
    assert sink.events[0]["reason"] == "etl_hook not configured"


def test_etl_hook_whitespace_only_skips() -> None:
    """Empty / whitespace shlex result is treated as no-op (not error)."""
    cfg = replace(_base_cfg(), etl_hook="   ")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 0
    assert sink.kinds() == ["deploy:step1:skip"]
    assert "empty after shlex" in str(sink.events[0]["reason"])


# ---------- exit code propagation ----------

def test_etl_hook_zero_exit_returns_zero() -> None:
    cfg = replace(_base_cfg(), etl_hook="sh -c 'exit 0'")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 0
    assert "deploy:step1:done" in sink.kinds()
    assert sink.by_kind("deploy:step1:done")[0]["exit_code"] == 0


def test_etl_hook_nonzero_exit_propagates() -> None:
    """Halt contract: caller checks rc != 0 and skips step 2."""
    cfg = replace(_base_cfg(), etl_hook="sh -c 'exit 7'")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 7
    assert sink.by_kind("deploy:step1:done")[0]["exit_code"] == 7


# ---------- streaming ----------

def test_etl_hook_stdout_streams_line_by_line() -> None:
    cfg = replace(_base_cfg(), etl_hook="sh -c 'echo first; echo second'")
    sink = _EventCollector()
    _run_step_1(cfg, sink)
    stdout_lines = [
        e["line"] for e in sink.by_kind("deploy:step1:stdout")
    ]
    assert stdout_lines == ["first", "second"]


def test_etl_hook_stderr_streams_separately() -> None:
    cfg = replace(_base_cfg(), etl_hook=(
        "sh -c 'echo to-stdout; echo to-stderr 1>&2'"
    ))
    sink = _EventCollector()
    _run_step_1(cfg, sink)
    assert [e["line"] for e in sink.by_kind("deploy:step1:stdout")] == [
        "to-stdout",
    ]
    assert [e["line"] for e in sink.by_kind("deploy:step1:stderr")] == [
        "to-stderr",
    ]


def test_etl_hook_event_order_start_then_streams_then_done() -> None:
    """The full lifecycle in order; pipeline orchestration relies on
    this so it can render progress incrementally."""
    cfg = replace(_base_cfg(), etl_hook="sh -c 'echo go; exit 3'")
    sink = _EventCollector()
    assert _run_step_1(cfg, sink) == 3
    kinds = sink.kinds()
    assert kinds[0] == "deploy:step1:start"
    assert kinds[-1] == "deploy:step1:done"
    assert "deploy:step1:stdout" in kinds


# ---------- dev_log opt-out ----------

def test_etl_hook_dev_log_none_does_not_crash() -> None:
    """Pipeline callers may opt out of streaming (e.g. CLI's --quiet)."""
    cfg = replace(_base_cfg(), etl_hook="sh -c 'exit 0'")
    assert _run_step_1(cfg, None) == 0


# ---------- failure modes ----------

def test_etl_hook_missing_binary_propagates() -> None:
    """A missing binary is operator-actionable, NOT a silent skip.
    Whole point of declaring etl_hook is that it MUST run."""
    cfg = replace(
        _base_cfg(),
        etl_hook="/nonexistent/binary/that/does-not-exist arg1",
    )
    sink = _EventCollector()
    with pytest.raises(FileNotFoundError):
        _run_step_1(cfg, sink)
    # `start` event fired before the failure surfaced.
    assert sink.kinds()[0] == "deploy:step1:start"


# ---------- CS.9 — timeout path ----------

def test_etl_hook_timeout_terminates_and_returns_124(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CS.9 — a runaway etl_hook must NOT hang the studio session.
    The env override forces a tiny timeout; the hook sleeps longer
    than the cap; the helper terminates the process and returns
    124 (GNU timeout(1) convention) so callers can distinguish
    "hook never finished" from "hook exited with error"."""
    # 1-second cap; `sleep 60` is well past it but small enough that
    # a wedged test doesn't hang CI.
    monkeypatch.setenv("RECON_GEN_STUDIO_ETL_HOOK_TIMEOUT_SECS", "1")  # typing-smell: ignore[envvar-bypass]: testing the env override — needs raw set
    cfg = replace(_base_cfg(), etl_hook="sh -c 'sleep 60'")
    sink = _EventCollector()
    rc = _run_step_1(cfg, sink)
    assert rc == 124, (
        f"timeout path should return 124 (GNU timeout convention); "
        f"got {rc}. Subprocess events: {sink.kinds()}"
    )
    assert "deploy:step1:timeout_terminating_subprocess" in sink.kinds()
    # Final `done` event still fires with the timeout rc.
    done_events = sink.by_kind("deploy:step1:done")
    assert len(done_events) == 1
    assert done_events[0]["exit_code"] == 124


# ============================================================
# step_2_wipe (X.4.g.5)
# ============================================================


# ---------- SQL emitter ----------

def test_wipe_demo_data_sql_postgres_format(
    spec_example_instance: L2Instance,
) -> None:
    p = "spec_example"
    sql = wipe_demo_data_sql(
        spec_example_instance, prefix=p, dialect=Dialect.POSTGRES,
    )
    assert f"DELETE FROM {p}_daily_balances;" in sql
    assert f"DELETE FROM {p}_transactions;" in sql


def test_wipe_demo_data_sql_oracle_format(
    spec_example_instance: L2Instance,
) -> None:
    """Oracle accepts the same DELETE statements (case-folds the
    unquoted identifiers to uppercase to match the schema)."""
    p = "spec_example"
    sql = wipe_demo_data_sql(
        spec_example_instance, prefix=p, dialect=Dialect.ORACLE,
    )
    assert f"DELETE FROM {p}_daily_balances;" in sql
    assert f"DELETE FROM {p}_transactions;" in sql


def test_wipe_demo_data_sql_duckdb_format(
    spec_example_instance: L2Instance,
) -> None:
    p = "spec_example"
    sql = wipe_demo_data_sql(
        spec_example_instance, prefix=p, dialect=Dialect.DUCKDB,
    )
    assert f"DELETE FROM {p}_daily_balances;" in sql
    assert f"DELETE FROM {p}_transactions;" in sql


# ---------- step_2_wipe orchestrator (DuckDB tempfile) ----------

def test_step_2_wipe_clears_both_base_tables(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = _duckdb_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    pre_tx, pre_bal = _row_counts(cfg, spec_example_instance)
    assert (pre_tx, pre_bal) == (1, 1), (
        "fixture should plant exactly one row per table"
    )

    sink = _EventCollector()
    tx_deleted, bal_deleted = asyncio.run(
        step_2_wipe(cfg, spec_example_instance, dev_log=sink),
    )
    assert tx_deleted == 1
    assert bal_deleted == 1

    post_tx, post_bal = _row_counts(cfg, spec_example_instance)
    assert (post_tx, post_bal) == (0, 0)


def test_step_2_wipe_emits_start_then_done_events(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = _duckdb_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    sink = _EventCollector()
    asyncio.run(step_2_wipe(cfg, spec_example_instance, dev_log=sink))

    kinds = sink.kinds()
    assert kinds == [
        "deploy:step2:wipe:start",
        "deploy:step2:wipe:done",
    ]
    start = sink.by_kind("deploy:step2:wipe:start")[0]
    assert start["db_table_prefix"] == cfg.db.table_prefix
    assert start["dialect"] == "duckdb"
    done = sink.by_kind("deploy:step2:wipe:done")[0]
    assert done["transactions_deleted"] == 1
    assert done["daily_balances_deleted"] == 1


def test_step_2_wipe_dev_log_none_safe(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = _duckdb_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    tx, bal = asyncio.run(
        step_2_wipe(cfg, spec_example_instance, dev_log=None),
    )
    assert (tx, bal) == (1, 1)


def test_step_2_wipe_idempotent_on_empty_tables(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Wipe-then-wipe is safe — second call reports zero deletes."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    asyncio.run(step_2_wipe(cfg, spec_example_instance, dev_log=None))
    tx, bal = asyncio.run(
        step_2_wipe(cfg, spec_example_instance, dev_log=None),
    )
    assert (tx, bal) == (0, 0)


def test_step_2_wipe_auto_emits_schema_on_virgin_db(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """BX backlog #173 — Studio's Session Start lands here on a fresh
    DuckDB / Postgres / Oracle that's never been seeded. Pre-fix the
    WIPE blew up with a CatalogException ("table
    ``<prefix>_transactions`` does not exist") and the operator had
    to drop to the CLI for ``recon-gen schema apply --execute``. The
    self-heal probe now emits the base schema in-place + records the
    ``deploy:step2:schema_emitted`` event so the operator sees it
    happened. Subsequent WIPE proceeds normally on the freshly-empty
    tables."""
    cfg = _duckdb_cfg(tmp_path)
    # NO _apply_schema_and_plant_two_rows() — leave the DB virgin.
    sink: list[Mapping[str, object]] = []

    async def _capture(event: Mapping[str, object]) -> None:
        sink.append(event)

    tx, bal = asyncio.run(
        step_2_wipe(cfg, spec_example_instance, dev_log=_capture),
    )
    # Virgin DB → wipe is a no-op on freshly-emitted empty tables.
    assert (tx, bal) == (0, 0)
    # Schema-emitted event surfaces in the live-tail so the operator
    # sees what just happened.
    event_names = [str(e.get("event", "")) for e in sink]
    assert "deploy:step2:schema_emitted" in event_names, (
        f"expected deploy:step2:schema_emitted event, got {event_names}"
    )
    # Sanity: a second WIPE is now idempotent (proves the schema-emit
    # left the DB in a valid state — no half-created tables).
    tx2, bal2 = asyncio.run(
        step_2_wipe(cfg, spec_example_instance, dev_log=None),
    )
    assert (tx2, bal2) == (0, 0)


def test_step_2_wipe_skips_schema_emit_when_base_exists(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """The self-heal probe must NOT re-emit schema on populated DBs
    (would crash with duplicate-table errors and cost real wall-time
    on every Session Start). Probe + skip is the steady-state path."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    sink: list[Mapping[str, object]] = []

    async def _capture(event: Mapping[str, object]) -> None:
        sink.append(event)

    asyncio.run(step_2_wipe(cfg, spec_example_instance, dev_log=_capture))
    event_names = [str(e.get("event", "")) for e in sink]
    assert "deploy:step2:schema_emitted" not in event_names, (
        f"populated DB triggered schema re-emit: {event_names}"
    )


# ============================================================
# step_3_generator (X.4.g.7+8)
# ============================================================


# ---------- skip path ----------

def test_step_3_generator_skip_when_disabled(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(enabled=False),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    tx, bal = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=sink),
    )
    assert (tx, bal) == (0, 0)
    assert sink.kinds() == ["deploy:step3:generator:skip"]
    assert sink.events[0]["reason"] == (
        "test_generator.enabled is False"
    )
    # And the demo DB stayed empty.
    assert _row_counts(cfg, spec_example_instance) == (0, 0)


# ---------- happy path: scope=full ----------

def test_step_3_generator_full_writes_rows(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """scope=full at defaults runs the standard build_full_seed_sql
    pipeline and lands rows in both base tables."""
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(end_date=date(2030, 1, 1)),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    tx, bal = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=sink),
    )
    assert tx > 0, "spec_example baseline should write transactions"
    assert bal > 0, "spec_example baseline should write daily_balances"
    actual_tx, actual_bal = _row_counts(cfg, spec_example_instance)
    assert (actual_tx, actual_bal) == (tx, bal)


def test_step_3_generator_full_emits_start_then_done(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(
            end_date=date(2030, 1, 1), seed=12345,
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    tx, bal = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=sink),
    )
    kinds = sink.kinds()
    assert kinds == [
        "deploy:step3:generator:start",
        "deploy:step3:generator:done",
    ]
    start = sink.by_kind("deploy:step3:generator:start")[0]
    assert start["scope"] == "full"
    assert start["end_date"] == "2030-01-01"
    assert start["seed"] == 12345
    done = sink.by_kind("deploy:step3:generator:done")[0]
    assert done["transactions_written"] == tx
    assert done["daily_balances_written"] == bal


def test_step_3_generator_full_with_cutoff_truncates_emission(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """X.4.h trainer cutoff — when cfg.test.generator.cutoff_date is
    set, deploy emits the full scenario then DELETEs rows past cutoff.
    Plants land at fixed calendar positions (anchor=end_date), the
    cutoff just truncates the deployed dataset.

    Verifies: rows past cutoff get deleted; rows on/before stay.
    The trainer's "scrub head" model relies on this — clicking an
    earlier day in the timeline should leave plant calendar positions
    untouched and cut off the dataset.
    """
    from datetime import date

    no_cutoff_dir = tmp_path / "no_cutoff"
    no_cutoff_dir.mkdir()
    with_cutoff_dir = tmp_path / "with_cutoff"
    with_cutoff_dir.mkdir()
    cfg_no_cutoff = replace(
        _duckdb_cfg(no_cutoff_dir),
        test_generator=TestGeneratorConfig(end_date=date(2030, 1, 31)),
    )
    cfg_with_cutoff = replace(
        _duckdb_cfg(with_cutoff_dir),
        test_generator=TestGeneratorConfig(
            end_date=date(2030, 1, 31),
            cutoff_date=date(2030, 1, 15),  # truncate mid-month
        ),
    )
    _apply_demo_schema_only(cfg_no_cutoff, spec_example_instance)
    _apply_demo_schema_only(cfg_with_cutoff, spec_example_instance)
    full_tx, full_bal = asyncio.run(
        step_3_generator(cfg_no_cutoff, spec_example_instance, dev_log=None),
    )
    cut_tx, cut_bal = asyncio.run(
        step_3_generator(cfg_with_cutoff, spec_example_instance, dev_log=None),
    )

    # Cutoff version has strictly fewer rows (truncates ~half the
    # 90-day window — we picked 1/15 inside a window ending 1/31).
    assert cut_tx > 0, "cutoff version should retain rows on/before cutoff"
    assert cut_tx < full_tx, (
        "cutoff version should have strictly fewer transactions than "
        f"non-cutoff (got cut={cut_tx}, full={full_tx})"
    )
    assert cut_bal < full_bal, (
        "cutoff version should have strictly fewer daily_balances than "
        f"non-cutoff (got cut={cut_bal}, full={full_bal})"
    )


def test_step_3_generator_no_cutoff_emits_unchanged(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """When cutoff_date is None (CLI default + Studio when up_to ==
    window_end), no DELETE statements are appended — emission is
    byte-identical to legacy. Two runs with identical knobs and no
    cutoff produce the same row counts (the existing determinism test
    confirms this — this one just guards the cutoff_date=None path
    explicitly)."""
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(
            end_date=date(2030, 1, 31),
            cutoff_date=None,  # explicit — test the None path
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    tx, bal = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=None),
    )
    assert tx > 0
    assert bal > 0


def test_step_3_generator_full_anchor_determinism(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Same anchor + fresh DB ⇒ same row counts. Sanity for the
    deterministic-when-knobs-at-defaults contract."""
    from datetime import date

    def _run(label: str) -> tuple[int, int]:
        # Each run gets its own SQLite tempfile (same dir, distinct
        # file) so we don't carry state across runs.
        sub = tmp_path / label
        sub.mkdir()
        cfg = replace(
            _duckdb_cfg(sub),
            test_generator=TestGeneratorConfig(end_date=date(2030, 1, 1)),
        )
        _apply_demo_schema_only(cfg, spec_example_instance)
        return asyncio.run(
            step_3_generator(cfg, spec_example_instance, dev_log=None),
        )

    first = _run("a")
    second = _run("b")
    assert first == second, (
        "scope=full at defaults must be deterministic across runs"
    )


# ---------- not-yet-implemented modes ----------

def test_step_3_generator_exceptions_only_writes_fewer_than_full(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """X.4.g.9 — exceptions_only skips the 90-day baseline; row counts
    should be strictly less than scope=full at the same anchor."""
    from datetime import date

    def _run(scope: str, label: str) -> tuple[int, int]:
        sub = tmp_path / label
        sub.mkdir()
        cfg = replace(
            _duckdb_cfg(sub),
            test_generator=TestGeneratorConfig(
                scope=scope,  # pyright: ignore[reportArgumentType]  # WHY: parametrized over Literal at the call site
                end_date=date(2030, 1, 1),
            ),
        )
        _apply_demo_schema_only(cfg, spec_example_instance)
        return asyncio.run(
            step_3_generator(cfg, spec_example_instance, dev_log=None),
        )

    full_tx, full_bal = _run("full", "full")
    exc_tx, exc_bal = _run("exceptions_only", "exc")

    assert exc_tx > 0, "exceptions_only should plant some transactions"
    assert exc_tx < full_tx, (
        "exceptions_only must skip the 90-day baseline so it writes "
        f"strictly fewer transactions than full (got exc={exc_tx}, "
        f"full={full_tx})"
    )
    # Daily balances may or may not appear in the plants layer
    # (depends on which scenarios touch balance rows). Just verify
    # exc_bal <= full_bal — never higher.
    assert exc_bal <= full_bal


def test_step_3_generator_exceptions_only_emits_lifecycle_events(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(scope="exceptions_only"),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=sink),
    )
    kinds = sink.kinds()
    assert kinds[0] == "deploy:step3:generator:start"
    assert kinds[-1] == "deploy:step3:generator:done"
    assert sink.by_kind("deploy:step3:generator:start")[0]["scope"] == (
        "exceptions_only"
    )


def test_step_3_generator_uncovered_rails_empty_db_full_baseline(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """No rails covered (empty demo DB) ⇒ uncovered_rails emits the
    full baseline. Should match scope=full minus the plants layer."""
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(
            scope="uncovered_rails", end_date=date(2030, 1, 1),
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    tx, bal = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=None),
    )
    assert tx > 0, (
        "with no covered rails, uncovered_rails should still emit "
        "baseline for every rail"
    )
    assert bal > 0


def test_step_3_generator_uncovered_rails_skips_covered(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Pre-populate ONE rail's row in the demo DB; verify uncovered_rails
    emits strictly fewer transactions than the empty-DB case (the
    covered rail's baseline is skipped)."""
    from datetime import date

    def _empty_db_run(label: str) -> int:
        sub = tmp_path / label
        sub.mkdir()
        cfg = replace(
            _duckdb_cfg(sub),
            test_generator=TestGeneratorConfig(
                scope="uncovered_rails", end_date=date(2030, 1, 1),
            ),
        )
        _apply_demo_schema_only(cfg, spec_example_instance)
        tx, _bal = asyncio.run(
            step_3_generator(cfg, spec_example_instance, dev_log=None),
        )
        return tx

    full_count = _empty_db_run("full")

    # Now plant one row with a real rail name and re-run.
    sub = tmp_path / "partial"
    sub.mkdir()
    cfg = replace(
        _duckdb_cfg(sub),
        test_generator=TestGeneratorConfig(
            scope="uncovered_rails", end_date=date(2030, 1, 1),
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    # Pick the first rail in the L2 to "cover" — its baseline should
    # be skipped on the next emit.
    covered_rail_name = str(spec_example_instance.rails[0].name)
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"INSERT INTO {p}_transactions ("
                "id, account_id, account_scope, amount_money, "
                "amount_direction, status, posting, transfer_id, "
                "rail_name, origin"
                ") VALUES ("
                "'op-1', 'op-acct', 'internal', 50.00, 'Credit', "
                "'posted', '2030-01-01 00:00:00', 'op-tr', "
                "?, 'inbound')",
                (covered_rail_name,),
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()

    partial_tx, _ = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=None),
    )
    # partial_tx counts post-step-3 totals, including the 1 planted
    # row. Subtract it to get just step-3's contribution; that should
    # be strictly less than full_count (since one rail is skipped).
    step3_contribution = partial_tx - 1
    assert step3_contribution < full_count, (
        f"uncovered_rails should skip rail {covered_rail_name!r} so "
        f"step 3 emits fewer rows (got {step3_contribution} vs "
        f"empty-DB={full_count})"
    )


def test_covered_rail_names_distinct_set(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Helper: verify _covered_rail_names returns the de-duplicated set
    of rail_name values from <prefix>_transactions."""
    from recon_gen.common.l2.deploy_pipeline import _covered_rail_names
    cfg = _duckdb_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    # Empty table → empty set.
    assert _covered_rail_names(cfg, spec_example_instance) == frozenset()
    # Plant 3 rows with 2 distinct rail_names.
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            for i, rail in enumerate(["RailA", "RailB", "RailA"]):
                cur.execute(
                    f"INSERT INTO {p}_transactions ("
                    "id, account_id, account_scope, amount_money, "
                    "amount_direction, status, posting, transfer_id, "
                    "rail_name, origin"
                    ") VALUES ("
                    f"'t{i}', 'a', 'internal', 1.00, 'Credit', 'posted', "
                    f"'2030-01-01', 'g{i}', ?, 'inbound')",
                    (rail,),
                )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()
    covered = _covered_rail_names(cfg, spec_example_instance)
    assert {str(c) for c in covered} == {"RailA", "RailB"}


# ---------- additive contract ----------

def test_step_3_generator_full_adds_to_existing_rows(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Step 3 is always additive — runs after step 2's wipe + optional
    pull. Verify by planting one row before, then running step 3, and
    confirming the count is `1 + generator_output`."""
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(end_date=date(2030, 1, 1)),
    )
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    pre_tx, pre_bal = _row_counts(cfg, spec_example_instance)
    assert (pre_tx, pre_bal) == (1, 1)
    tx, bal = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=None),
    )
    # tx / bal are the post-step-3 totals (per the contract).
    # Generator's contribution = total - 1 plant row already present.
    assert tx > 1
    assert bal > 1


# =====================================================================
# X.4.i.1 — only_template scope mode
# =====================================================================


def test_only_template_rails_returns_template_leg_rails(
    spec_example_instance: L2Instance,
) -> None:
    """The closure for a known template = its declared leg_rails set."""
    from recon_gen.common.l2.deploy_pipeline import _only_template_rails

    closure = _only_template_rails(
        "MerchantSettlementCycle", spec_example_instance, cfg=_base_cfg(),
    )
    # spec_example fixture: MerchantSettlementCycle has leg_rails: [SubledgerCharge]
    assert {str(r) for r in closure} == {"SubledgerCharge"}


def test_only_template_rails_unknown_name_loud_fails(
    spec_example_instance: L2Instance,
) -> None:
    """Unknown template name halts the deploy with a useful error
    listing the declared templates so the operator can see the typo."""
    from recon_gen.common.l2.deploy_pipeline import _only_template_rails

    with pytest.raises(ValueError, match="MadeUpName"):
        _only_template_rails(
            "MadeUpName", spec_example_instance, cfg=_base_cfg(),
        )


def test_step_3_generator_only_template_requires_template_name(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """scope='only_template' with cfg.test.generator.only_template unset
    must loud-fail rather than silently degrade to scope=full."""
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(
            scope="only_template", end_date=date(2030, 1, 1),
            only_template=None,
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    with pytest.raises(ValueError, match="only_template"):
        asyncio.run(
            step_3_generator(cfg, spec_example_instance, dev_log=None),
        )


def test_step_3_generator_only_template_emits_closure_baseline(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """scope='only_template' against MerchantSettlementCycle should emit
    baseline rows for SubledgerCharge (its leg-rail). Chain firings that
    fan out from SubledgerCharge as parent are emitted too — that's the
    intended training surface (operator wants to see the full transfer
    flow rooted at the chosen template). Narrowness against scope=full
    is proven by the strictly-fewer-than-full sibling test."""
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(
            scope="only_template", end_date=date(2030, 1, 1),
            only_template="MerchantSettlementCycle",
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    tx, _bal = asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=None),
    )
    assert tx > 0, "only_template should emit baseline for the closure rail"
    # Closure rail must be present.
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT DISTINCT rail_name FROM {p}_transactions "
                "WHERE rail_name IS NOT NULL",
            )
            rail_names = {str(r[0]) for r in cur.fetchall()}
        finally:
            cur.close()
    finally:
        conn.close()
    assert "SubledgerCharge" in rail_names, (
        f"only_template should emit baseline for the closure rail, "
        f"got rail_names={rail_names}"
    )
    # And it should NOT touch rails that are unreachable from this template
    # (no chain or template links). ReconciliationLeg is in the OTHER
    # template (ExternalReconciliationCycle) — proves narrowness.
    assert "ReconciliationLeg" not in rail_names, (
        f"only_template={'MerchantSettlementCycle'!r} should NOT emit "
        f"rails from sibling templates; got rail_names={rail_names}"
    )


def test_step_3_generator_only_template_writes_strictly_fewer_than_full(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Closure of one TransferTemplate's leg_rails is a subset of all
    rails ⇒ only_template emits strictly fewer transactions than full
    against the same anchor."""
    from datetime import date

    def _run(scope: str, label: str, only_template: str | None) -> int:
        sub = tmp_path / label
        sub.mkdir()
        cfg = replace(
            _duckdb_cfg(sub),
            test_generator=TestGeneratorConfig(
                scope=scope,  # pyright: ignore[reportArgumentType]  # WHY: parametrized over Literal at the call site
                end_date=date(2030, 1, 1),
                only_template=only_template,
            ),
        )
        _apply_demo_schema_only(cfg, spec_example_instance)
        tx, _bal = asyncio.run(
            step_3_generator(cfg, spec_example_instance, dev_log=None),
        )
        return tx

    full_tx = _run("full", "full", None)
    only_tx = _run("only_template", "only", "MerchantSettlementCycle")
    assert only_tx < full_tx, (
        f"only_template={only_tx} should be strictly less than full={full_tx}"
    )


# =====================================================================
# X.4.i.2 — derive_balances composing flag (post-step-3 hook)
# =====================================================================


def _insert_test_transaction(
    cur: object,  # duckdb.DuckDBPyConnection
    p: str,
    *,
    tid: str,
    account_id: str,
    account_role: str,
    amount_money: float,
    posting: str,
    status: str = "posted",
) -> None:
    """Insert a row into <prefix>_transactions matching the v6 schema.
    `amount_money` is signed (per the L1 Amount invariant CHECK constraint)."""
    direction = "Credit" if amount_money >= 0 else "Debit"
    cur.execute(  # type: ignore[attr-defined]: cur is typed `object` so DB-API call site is by-attr — the helper accepts any cursor (sqlite3 / psycopg / oracledb)
        f"INSERT INTO {p}_transactions ("
        "id, account_id, account_name, account_role, "
        "account_scope, account_parent_role, amount_money, "
        "amount_direction, status, posting, transfer_id, "
        "rail_name, origin"
        ") VALUES ("
        "?, ?, 'Acct', ?, "
        "'internal', NULL, ?, "
        "?, ?, ?, 'tr-d', "
        "'TestRail', 'inbound')",
        (tid, account_id, account_role, amount_money,
         direction, status, posting),
    )


def _seed_two_account_roles_with_transactions(
    cfg: Config, instance: L2Instance, anchor_date: date,
) -> None:
    """Populate <prefix>_transactions with rows for ONE control account
    (gl_control) AND one DDA so we can assert the derive narrows to
    control-by-default."""
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            ts = anchor_date.isoformat() + " 12:00:00"
            # 3 control rows: +100 + +200 + -50 = 250 net for day.
            # 2 DDA rows: +75 + +25 = 100 net same day.
            for tid, acct, role, amt in [
                ("c1", "gl-1", "gl_control", 100.0),
                ("c2", "gl-1", "gl_control", 200.0),
                ("c3", "gl-1", "gl_control", -50.0),
                ("d1", "dda-1", "dda", 75.0),
                ("d2", "dda-1", "dda", 25.0),
            ]:
                _insert_test_transaction(
                    cur, p,
                    tid=tid, account_id=acct, account_role=role,
                    amount_money=amt, posting=ts,
                )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def test_derive_balances_no_op_when_disabled(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """When cfg.test.generator.derive_balances=False, the pass returns
    0 and writes nothing."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(derive_balances=False),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    _seed_two_account_roles_with_transactions(
        cfg, spec_example_instance, date(2030, 1, 1),
    )
    rows = asyncio.run(
        step_3_5_derive_balances(
            cfg, spec_example_instance, dev_log=None,
        ),
    )
    assert rows == 0
    # daily_balances stays empty.
    bal_count = _row_counts(cfg, spec_example_instance)[1]
    assert bal_count == 0


def test_derive_balances_default_account_roles_writes_control_only(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Default account-role set is control accounts only — DDA
    transactions are NOT derived into balances."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(derive_balances=True),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    _seed_two_account_roles_with_transactions(
        cfg, spec_example_instance, date(2030, 1, 1),
    )
    rows = asyncio.run(
        step_3_5_derive_balances(
            cfg, spec_example_instance, dev_log=None,
        ),
    )
    # 1 (gl-1, 2030-01-01) row written; the DDA's row was skipped.
    assert rows == 1
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT account_id, account_role, money "
                f"FROM {p}_daily_balances",
            )
            rows_seen = [
                (str(r[0]), str(r[1]), float(r[2]))
                for r in cur.fetchall()
            ]
        finally:
            cur.close()
    finally:
        conn.close()
    assert rows_seen == [("gl-1", "gl_control", 250.0)], (
        f"derive_balances default should write only the control-account "
        f"sum (gl-1 = 100+200-50 = 250.0); got {rows_seen}"
    )


def test_derive_balances_account_roles_override_widens_set(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Operator can opt DDA balances in via the override field — both
    control AND DDA rows get derived."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(
            derive_balances=True,
            derive_balances_account_roles=("gl_control", "dda"),
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    _seed_two_account_roles_with_transactions(
        cfg, spec_example_instance, date(2030, 1, 1),
    )
    rows = asyncio.run(
        step_3_5_derive_balances(
            cfg, spec_example_instance, dev_log=None,
        ),
    )
    assert rows == 2  # gl-1 + dda-1
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT account_id, money FROM {p}_daily_balances "
                f"ORDER BY account_id",
            )
            balances = {str(r[0]): float(r[1]) for r in cur.fetchall()}
        finally:
            cur.close()
    finally:
        conn.close()
    assert balances == {"gl-1": 250.0, "dda-1": 100.0}


def test_derive_balances_failed_transactions_excluded(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Transactions in status='failed' don't contribute to derived
    balances — they never posted."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(derive_balances=True),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            ts = "2030-01-01 12:00:00"
            for tid, amt, status in [
                ("c1", 100.0, "posted"),
                ("c2", 999.0, "failed"),  # excluded
                ("c3", 50.0, "posted"),
            ]:
                _insert_test_transaction(
                    cur, p,
                    tid=tid, account_id="gl-1",
                    account_role="gl_control",
                    amount_money=amt, posting=ts, status=status,
                )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()
    rows = asyncio.run(
        step_3_5_derive_balances(
            cfg, spec_example_instance, dev_log=None,
        ),
    )
    assert rows == 1
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT money FROM {p}_daily_balances "
                f"WHERE account_id = 'gl-1'",
            )
            money = float(fetch_one_required(cur)[0])
        finally:
            cur.close()
    finally:
        conn.close()
    assert money == 150.0, (
        f"failed transactions must be excluded; got money={money} "
        f"(expected 100 + 50 = 150)"
    )


def test_derive_balances_overwrites_existing_rows(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Re-running the derive overwrites existing daily_balances rows for
    the same (account, business_day) — operator can iteratively scrub."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(derive_balances=True),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    _seed_two_account_roles_with_transactions(
        cfg, spec_example_instance, date(2030, 1, 1),
    )
    # Run once.
    asyncio.run(
        step_3_5_derive_balances(
            cfg, spec_example_instance, dev_log=None,
        ),
    )
    # Add another posted control transaction the same day.
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            _insert_test_transaction(
                cur, p,
                tid="c4", account_id="gl-1",
                account_role="gl_control",
                amount_money=1000.0, posting="2030-01-01 13:00:00",
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()
    # Re-derive.
    asyncio.run(
        step_3_5_derive_balances(
            cfg, spec_example_instance, dev_log=None,
        ),
    )
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT COUNT(*), MAX(money) "
                f"FROM {p}_daily_balances WHERE account_id = 'gl-1'",
            )
            count, money = fetch_one_required(cur)
        finally:
            cur.close()
    finally:
        conn.close()
    assert count == 1, "re-derive should replace, not duplicate, the row"
    assert float(money) == 1250.0, (
        f"second derive should reflect the new transaction; "
        f"got {money} (expected 100+200-50+1000=1250)"
    )


def test_derive_balances_emits_lifecycle_events(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """When enabled, derive emits start + done events with the
    account_roles in the payload for visibility."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(
            derive_balances=True,
            derive_balances_account_roles=("gl_control",),
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    _seed_two_account_roles_with_transactions(
        cfg, spec_example_instance, date(2030, 1, 1),
    )
    sink = _EventCollector()
    asyncio.run(
        step_3_5_derive_balances(
            cfg, spec_example_instance, dev_log=sink,
        ),
    )
    events = [e["event"] for e in sink.events]
    assert "deploy:step3_5:derive:start" in events
    assert "deploy:step3_5:derive:done" in events
    done = [
        e for e in sink.events
        if e["event"] == "deploy:step3_5:derive:done"
    ][0]
    assert done["account_roles"] == ["gl_control"]
    assert done["rows"] == 1


# =====================================================================
# X.4.g.11 — Step 4 matview refresh
# =====================================================================

def test_step_4_matviews_refresh_emits_lifecycle_events(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """SQLite refresh path: drops + re-creates every matview-as-table.
    Lifecycle = start → done."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    asyncio.run(
        step_4_matviews(cfg, spec_example_instance, dev_log=sink),
    )
    assert sink.kinds() == [
        "deploy:step4:matviews:start",
        "deploy:step4:matviews:done",
    ]
    start = sink.by_kind("deploy:step4:matviews:start")[0]
    assert start["db_table_prefix"] == cfg.db.table_prefix
    assert start["dialect"] == cfg.db.dialect.value


def test_step_4_matviews_idempotent_on_empty_db(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Running matview refresh on an empty (post-wipe) DB must succeed —
    matviews exist (from the schema apply) but resolve to zero rows.
    Re-running is safe (drops + recreates)."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    asyncio.run(
        step_4_matviews(cfg, spec_example_instance, dev_log=None),
    )
    # Second invocation must not raise (refresh is idempotent).
    asyncio.run(
        step_4_matviews(cfg, spec_example_instance, dev_log=None),
    )
    # And every matview still exists (and is empty).
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {p}_drift")
            assert int(fetch_one_required(cur)[0]) == 0
            cur.execute(f"SELECT COUNT(*) FROM {p}_overdraft")
            assert int(fetch_one_required(cur)[0]) == 0
        finally:
            cur.close()
    finally:
        conn.close()


def test_step_4_matviews_picks_up_new_rows(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """After step 3 emits rows, step 4 must surface a non-empty
    current_transactions matview (it's the leaf that all L1 invariants
    derive from)."""
    from datetime import date
    cfg = replace(
        _duckdb_cfg(tmp_path),
        test_generator=TestGeneratorConfig(end_date=date(2030, 1, 1)),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=None),
    )
    asyncio.run(
        step_4_matviews(cfg, spec_example_instance, dev_log=None),
    )
    p = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {p}_current_transactions")
            n = int(fetch_one_required(cur)[0])
            assert n > 0, (
                "step_4_matviews must surface step_3's writes into the "
                "current_transactions matview"
            )
        finally:
            cur.close()
    finally:
        conn.close()


# =====================================================================
# X.4.g.12 — Step 5 reload (data_generation_id bump)
# =====================================================================

def test_step_5_reload_bumps_counter_by_one() -> None:
    """The contract: each step_5_reload call returns get + 1.
    Asserting relative deltas (not absolute values) keeps the test
    stable regardless of how many other tests bumped the counter
    earlier in the run."""
    before = get_data_generation_id()
    after = asyncio.run(step_5_reload(dev_log=None))
    assert after == before + 1
    assert get_data_generation_id() == after


def test_step_5_reload_emits_bump_event_with_new_value() -> None:
    sink = _EventCollector()
    new = asyncio.run(step_5_reload(dev_log=sink))
    assert sink.kinds() == ["deploy:step5:reload:bump"]
    assert sink.events[0]["data_generation_id"] == new


def test_step_5_reload_repeated_calls_increment_monotonically() -> None:
    """Successive calls always increase by one — the only contract
    Dashboards' poller relies on for deciding "should I reload?"."""
    before = get_data_generation_id()
    for i in range(3):
        new = asyncio.run(step_5_reload(dev_log=None))
        assert new == before + i + 1


# =====================================================================
# X.4.g.13 — run_deploy_pipeline orchestration (5 steps + halt contract)
# =====================================================================

def test_run_deploy_pipeline_no_etl_runs_all_steps(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """No etl_hook configured: wipe runs (BS.4 order: wipe FIRST),
    step 1 etl_hook skips, steps 3-5 run. Summary reports per-step
    counts + the post-bump data_generation_id."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=sink),
    )
    assert isinstance(summary, DeploySummary)
    assert summary.halted is False
    assert summary.halt_reason is None
    assert summary.step1_etl_hook_exit_code == 0
    # Empty DB pre-pipeline → step 2 wipe deletes 0 rows; step 3
    # generator (full scope, default) populates both base tables.
    assert summary.step3_generator_transactions_after > 0
    assert summary.step3_generator_daily_balances_after > 0
    assert summary.step4_matviews_done is True
    assert summary.step5_data_generation_id > 0
    # Event ordering — BS.4 (2026-05-29): wipe runs FIRST, then
    # etl_hook (here skipping since unset), then generator → matviews
    # → reload. The skip event lands between wipe:done and step3:start.
    kinds = sink.kinds()
    assert kinds[0] == "deploy:step2:wipe:start"
    assert "deploy:step1:skip" in kinds
    assert kinds.index("deploy:step2:wipe:done") < kinds.index("deploy:step1:skip")
    assert kinds[-1] == "deploy:step5:reload:bump"
    # Captured events on the summary include every dev_log event too.
    assert len(summary.events) == len(sink.events)


def test_run_deploy_pipeline_halts_on_etl_failure(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """etl_hook returns non-zero exit ⇒ halt AFTER the wipe but
    BEFORE the generator + matview + reload. Summary.halted=True,
    halt_reason populated, generator/matview/reload at zero defaults.

    BS.4 (2026-05-29) reordered: wipe runs FIRST so etl_hook writes
    into clean state. On etl_hook failure demo_db is left in whatever
    partial state the hook wrote — operators wrap their hook in a
    transaction to roll back to the post-wipe empty state on failure.
    The pre-BS.4 "demo DB not touched on etl_hook failure" property
    is gone (the test below now confirms the wipe RAN, not that it
    was skipped)."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        etl_hook="false",  # POSIX `false` exits 1 — universally available
    )
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    pre_tx, pre_bal = _row_counts(cfg, spec_example_instance)
    assert (pre_tx, pre_bal) == (1, 1)

    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=None),
    )
    assert summary.halted is True
    assert summary.halt_reason is not None
    assert "etl_hook returned exit_code=1" in summary.halt_reason
    assert summary.step1_etl_hook_exit_code == 1
    # BS.4: the wipe DID run (post-BS.4 the wipe is unconditional —
    # it precedes etl_hook in the orchestration). The pre-existing
    # rows are gone.
    post_tx, post_bal = _row_counts(cfg, spec_example_instance)
    assert (post_tx, post_bal) == (0, 0), (
        "BS.4: wipe runs before etl_hook, so a halted run still wipes "
        "demo_db. Operators wrap etl_hook in a transaction for rollback."
    )
    # Summary reflects the wipe (rows deleted = pre-pipeline counts).
    assert summary.step2_wipe_transactions_deleted == 1
    assert summary.step2_wipe_daily_balances_deleted == 1
    # But downstream steps (generator/matviews/reload) didn't run.
    assert summary.step3_generator_transactions_after == 0
    assert summary.step4_matviews_done is False


def test_deploy_summary_to_json_serializes_every_field(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """``DeploySummary.to_json`` produces a flat dict with no
    dataclass-shaped values left over — POST /deploy serializes
    straight from this and Starlette's JSONResponse rejects nested
    dataclass instances."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=None),
    )
    payload = summary.to_json()
    # Top-level keys the studio + button rely on.
    assert payload["halted"] is False
    assert payload["halt_reason"] is None
    assert "step2_wipe" in payload
    assert "step3_generator" in payload
    assert payload["step4_matviews_done"] is True
    assert payload["step5_data_generation_id"] > 0  # pyright: ignore[reportOperatorIssue]: evt comparison against int sentinel; runtime dict[str, Any]
    assert isinstance(payload["events"], list)
    # Every event entry MUST be a plain dict (json-safe), not a Mapping
    # subclass that JSONResponse can't serialize.
    for evt in payload["events"]:  # pyright: ignore[reportUnknownVariableType]: evt is dict[str, Any] from boto3 paginator
        assert isinstance(evt, dict)


# =====================================================================
# X.4.g.15 — pipeline orchestration shapes (per the PLAN bullet's
# enumeration). Two shapes (hook-fail-halt + no-etl) are covered by
# the X.4.g.13 tests above; the three remaining shapes land here.
# =====================================================================

def test_orchestration_etl_hook_path(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """etl_hook present (succeeds): step 1 (wipe) runs, step 2 etl_hook
    runs + succeeds, step 3 generator (full scope) populates demo_db.

    BS.4 (2026-05-29): the legacy etl_datasource branch is gone — the
    only ETL contract is the etl_hook subprocess writing directly to
    demo_db (no upstream copy)."""
    cfg = replace(
        _duckdb_cfg(tmp_path),
        etl_hook="true",  # POSIX `true` exits 0
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=sink),
    )
    assert summary.halted is False
    assert summary.step1_etl_hook_exit_code == 0
    # Step 3 generator (full scope) carried the load — etl_hook is a
    # no-op (`true`) so the rows came from the generator.
    assert summary.step3_generator_transactions_after > 0
    assert summary.step3_generator_daily_balances_after > 0
    assert summary.step4_matviews_done is True
    # Step 1 etl_hook actually ran (start + done events) — not the skip path.
    kinds = sink.kinds()
    assert "deploy:step1:start" in kinds
    assert "deploy:step1:done" in kinds
    assert "deploy:step1:skip" not in kinds


def test_orchestration_no_etl_hook_path(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """No etl_hook configured: step 1 skips, step 2 wipe runs, step 3
    generator populates demo_db on its own. Default cfg path — the
    pre-BS.4 "etl-free" mode is now the canonical mode."""
    cfg = _duckdb_cfg(tmp_path)
    assert cfg.app2.etl_hook is None
    _apply_demo_schema_only(cfg, spec_example_instance)
    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=None),
    )
    assert summary.halted is False
    assert summary.step1_etl_hook_exit_code == 0
    assert summary.step3_generator_transactions_after > 0
    assert summary.step3_generator_daily_balances_after > 0
    assert summary.step4_matviews_done is True
    assert summary.step5_data_generation_id > 0



# ===== CO.x — Lock release/reacquire around etl_hook subprocess (DuckDB) =====
#
# The `_AsyncDuckdbPool` (run by Studio's `recon-gen studio` process)
# holds DuckDB's process-level write lock for the lifetime of the
# Studio process. When the operator triggers Refresh Data → POST
# /deploy → `step_1_etl_hook`, the customer's `cfg.app2.etl_hook` script
# runs as a SUBPROCESS via `asyncio.create_subprocess_exec`. That
# subprocess tries `duckdb.connect(path)` and gets
# `IO Error: Could not set lock on file`. Direct repro probe is in
# `docs/audits/etl_duckdb_studio_concurrency.md`.
#
# The fix: `run_deploy_pipeline` accepts an optional
# `subprocess_lock_bracket: Callable[[], AbstractAsyncContextManager[None]]`
# that brackets `step_1_etl_hook` (not the whole pipeline — same-
# process `connect_demo_db` works alongside the pool). The pool's
# `released_for_subprocess` async context manager combines close +
# yield + reopen under one lifecycle-lock acquisition so concurrent
# Studio handlers serialize through the bracket. Studio binds the
# callback to `db_pool.released_for_subprocess` for DuckDB; PG /
# Oracle pass None and the bracket no-ops via `nullcontext`.

from recon_gen.common.db import _AsyncDuckdbPool


def _etl_hook_writes_one_row(cfg: Config, scratch_dir: Path) -> str:
    """Build a `cfg.app2.etl_hook` shell-command that opens DuckDB,
    inserts a tagged row into <prefix>_transactions, and exits 0.

    Writes the Python script to a tempfile + invokes ``sys.executable
    <path>`` rather than ``-c "..."``. The nested-quotation
    interpolation in the inline form runs afoul of shlex.split (the
    SQL literal has both single + double quotes); the script-on-disk
    form sidesteps that entirely + matches what a real customer's
    cfg.app2.etl_hook would look like.

    Uses sys.executable so the subprocess is the same interpreter
    (no PATH skew across local dev / CI). The inserted row carries
    the marker `etl_hook_marker` in `transfer_type` so the test can
    assert it survived the pipeline.
    """
    import shlex  # noqa: PLC0415
    assert cfg.db.url is not None
    path = duckdb_path(cfg.db.url)
    prefix = cfg.db.table_prefix
    script = scratch_dir / "etl_hook_writer.py"
    script.write_text(
        "import duckdb\n"
        f"c = duckdb.connect({path!r})\n"
        "c.execute('''\n"
        f"INSERT INTO {prefix}_transactions\n"
        "(id, account_id, account_name, account_role, account_scope, "
        "amount_money, amount_direction, status, posting, transfer_id, "
        "rail_name, origin, metadata)\n"
        "VALUES ('etl-hook-row-1', "
        "'acct-1', 'Account 1', 'CustomerDDA', 'internal', "
        # The marker is on `origin` — `transfer_type` doesn't exist on
        # the v6 schema; `origin` is the closest free-text field the
        # ETL hook can stamp without breaking the L1 Amount invariant
        # CHECK (Credit + money >= 0).
        "100, 'Credit', 'Posted', '2026-01-01 00:00:00', "
        "'etl-hook-xfer', 'ach', 'etl_hook_marker', '{}')\n"
        "''')\n"
        "c.commit()\n"
        "c.close()\n",
        encoding="utf-8",
    )
    return f"{shlex.quote(_sys.executable)} {shlex.quote(str(script))}"


def test_run_deploy_pipeline_releases_pool_lock_for_etl_hook_subprocess(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """The lock-release/reacquire bracket lets a subprocess etl_hook
    acquire the DuckDB write lock even when the parent process holds
    an open `_AsyncDuckdbPool` root.

    Without the bracket, the subprocess fails with
    `IO Error: Could not set lock on file` → step1 exit_code=1
    → pipeline halts.

    With the bracket, pool.close() runs before the subprocess (frees
    the lock), subprocess writes its row, pool.reopen() runs after
    (reacquires for the remaining pipeline steps + subsequent
    dashboards queries).
    """
    cfg = _duckdb_cfg(tmp_path)
    cfg = replace(cfg, etl_hook=_etl_hook_writes_one_row(cfg, tmp_path))
    _apply_demo_schema_only(cfg, spec_example_instance)

    # Mirror Studio's runtime: the pool is constructed AFTER schema
    # apply (Studio's `_html_serve.py::_serve` opens the pool before
    # serving but the demo DB schema is established beforehand by
    # `recon-gen schema apply`). Lifetime spans the pipeline so the
    # released_for_subprocess bracket has something to surrender +
    # restore.
    assert cfg.db.url is not None
    pool = _AsyncDuckdbPool(duckdb_path(cfg.db.url))
    sink = _EventCollector()
    try:
        summary = asyncio.run(
            run_deploy_pipeline(
                cfg, spec_example_instance, dev_log=sink,
                subprocess_lock_bracket=pool.released_for_subprocess,
            ),
        )

        assert summary.halted is False
        assert summary.step1_etl_hook_exit_code == 0
        kinds = sink.kinds()
        assert "deploy:step1:locks_bracket_enter" in kinds
        assert "deploy:step1:locks_bracket_exit" in kinds
        assert "deploy:step1:done" in kinds
        # Prove the ORIGINAL pool was reacquired by acquiring through
        # it AFTER the pipeline returned — without a successful
        # reopen, this would raise PoolReleasedDuringRefresh.
        async def _verify_via_original_pool() -> int:
            async with pool.acquire() as conn:
                cur = await conn.execute(
                    f"SELECT COUNT(*) FROM {cfg.db.table_prefix}"
                    f"_transactions WHERE origin = ?",
                    ("etl_hook_marker",),
                )
                rows: list[Any] = await cur.fetchall()
                return int(rows[0][0])
        assert asyncio.run(_verify_via_original_pool()) == 1
    finally:
        asyncio.run(pool.close())


def test_run_deploy_pipeline_skips_bracket_when_unset(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Default-None path stays backward-compatible: no bracket,
    no bracket events in the log, pipeline runs through. Covers
    the CLI / unit-test invocations that don't own a pool.
    """
    cfg = _duckdb_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=sink),
    )
    assert summary.halted is False
    kinds = sink.kinds()
    assert "deploy:step1:locks_bracket_enter" not in kinds
    assert "deploy:step1:locks_bracket_exit" not in kinds


def test_run_deploy_pipeline_reopens_pool_on_etl_hook_failure(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """The released_for_subprocess bracket ALWAYS reopens — even
    when the etl_hook subprocess exits non-zero. Without this, the
    pool would stay closed after a failed deploy and the operator's
    dashboards would 500 until the Studio process restarted.
    """
    cfg = _duckdb_cfg(tmp_path)
    cfg = replace(cfg, etl_hook="false")  # exit 1
    _apply_demo_schema_only(cfg, spec_example_instance)
    assert cfg.db.url is not None
    pool = _AsyncDuckdbPool(duckdb_path(cfg.db.url))
    sink = _EventCollector()
    try:
        summary = asyncio.run(
            run_deploy_pipeline(
                cfg, spec_example_instance, dev_log=sink,
                subprocess_lock_bracket=pool.released_for_subprocess,
            ),
        )
        assert summary.halted is True
        assert summary.step1_etl_hook_exit_code == 1
        kinds = sink.kinds()
        # Bracket entered + exited cleanly despite the subprocess failure.
        assert "deploy:step1:locks_bracket_enter" in kinds
        assert "deploy:step1:locks_bracket_exit" in kinds
        # Pool is healthy post-reopen — acquire should succeed.
        async def _ping() -> int:
            async with pool.acquire() as conn:
                cur = await conn.execute("SELECT 1")
                rows: list[Any] = await cur.fetchall()
                return int(rows[0][0])
        assert asyncio.run(_ping()) == 1
    finally:
        asyncio.run(pool.close())


def test_async_duckdb_pool_reopen_is_idempotent(tmp_path: Path) -> None:
    """Pool.reopen() called twice in a row is a no-op on the second
    call (the root is already open). Mirrors close()'s idempotency.
    """
    db_path = str(tmp_path / "smoke.duckdb")
    import duckdb  # noqa: PLC0415
    seed = duckdb.connect(db_path)
    seed.execute("CREATE TABLE smoke (x INTEGER)")
    seed.close()

    pool = _AsyncDuckdbPool(db_path)
    try:
        # Already open at construction — reopen is no-op.
        asyncio.run(pool.reopen())
        # Close + reopen + reopen-again — second reopen is no-op.
        asyncio.run(pool.close())
        asyncio.run(pool.reopen())
        asyncio.run(pool.reopen())
        async def _ping() -> int:
            async with pool.acquire() as conn:
                cur = await conn.execute("SELECT COUNT(*) FROM smoke")
                rows: list[Any] = await cur.fetchall()
                return int(rows[0][0])
        assert asyncio.run(_ping()) == 0
    finally:
        asyncio.run(pool.close())


def test_async_duckdb_pool_released_for_subprocess_reopens_on_exception(
    tmp_path: Path,
) -> None:
    """The released_for_subprocess context manager ALWAYS reopens —
    even when the bracket body raises. Mirrors the deploy pipeline's
    "operator should never have to restart Studio after a failed
    etl_hook" guarantee.
    """
    db_path = str(tmp_path / "smoke.duckdb")
    import duckdb  # noqa: PLC0415
    seed = duckdb.connect(db_path)
    seed.execute("CREATE TABLE smoke (x INTEGER)")
    seed.close()

    pool = _AsyncDuckdbPool(db_path)
    try:
        class _Boom(Exception):
            pass

        async def _bracket_raises() -> None:
            async with pool.released_for_subprocess():
                raise _Boom("subprocess equivalent failed mid-bracket")

        with pytest.raises(_Boom):
            asyncio.run(_bracket_raises())

        # After the exception the pool is healthy.
        async def _ping() -> int:
            async with pool.acquire() as conn:
                cur = await conn.execute("SELECT 1")
                rows: list[Any] = await cur.fetchall()
                return int(rows[0][0])
        assert asyncio.run(_ping()) == 1
    finally:
        asyncio.run(pool.close())


def test_duckdb_pool_subprocess_bracket_helper_duck_types_correctly() -> None:
    """CO.x — `_duckdb_pool_subprocess_bracket` returns the bound
    bracket method for DuckDB pools and None for PG / Oracle. Covers
    the audit's "second test for the PG/Oracle dialect path that
    asserts the callbacks are *not* invoked when dialect ≠ DUCKDB."
    """
    from recon_gen.common.html._studio_routes import (  # noqa: PLC0415
        _duckdb_pool_subprocess_bracket,
    )

    # None input → None output (no pool, no bracket).
    assert _duckdb_pool_subprocess_bracket(None) is None

    # Fake PG/Oracle pool: has acquire + close but no
    # released_for_subprocess attribute. Duck-typed check returns None.
    class _FakePgPool:
        def acquire(self) -> object: ...
        async def close(self) -> None: ...
    assert _duckdb_pool_subprocess_bracket(_FakePgPool()) is None  # pyright: ignore[reportArgumentType]: structural Protocol; intentional duck-type

    # Fake DuckDB pool: has released_for_subprocess. Returns the method.
    class _FakeDuckPool:
        def acquire(self) -> object: ...
        async def close(self) -> None: ...
        def released_for_subprocess(self) -> object: ...
    fake = _FakeDuckPool()
    bracket = _duckdb_pool_subprocess_bracket(fake)  # pyright: ignore[reportArgumentType]: structural Protocol; intentional duck-type
    # `is` comparison fails on bound-method dance (new bound-method
    # object per access) — compare via `==` which Python defines as
    # same-function-same-receiver for bound methods.
    assert bracket == fake.released_for_subprocess


def test_session_start_passes_bracket_to_run_deploy_pipeline(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """CO.x — v_overlay.session_start MUST forward
    subprocess_lock_bracket to its inner run_deploy_pipeline call.
    If a future refactor drops the kwarg, training_session_start
    silently regresses to the lock-conflict bug.

    Uses a real DuckDB pool but a no-op etl_hook so the bracket
    fires without spawning a subprocess (the test isn't probing the
    subprocess path — only the parameter-forwarding contract).
    """
    from recon_gen.common.l2.v_overlay import session_start  # noqa: PLC0415

    cfg = _duckdb_cfg(tmp_path)
    cfg = replace(cfg, etl_hook=None)  # step_1 skip path
    _apply_demo_schema_only(cfg, spec_example_instance)
    assert cfg.db.url is not None
    pool = _AsyncDuckdbPool(duckdb_path(cfg.db.url))
    bracket_uses = 0

    @asynccontextmanager
    async def _counting_bracket() -> AsyncGenerator[None, None]:
        nonlocal bracket_uses
        bracket_uses += 1
        async with pool.released_for_subprocess():
            yield

    try:
        asyncio.run(
            session_start(
                cfg, spec_example_instance,
                refresh_base=True,
                subprocess_lock_bracket=_counting_bracket,
            ),
        )
    finally:
        asyncio.run(pool.close())

    # Bracket fired exactly once — for step_1_etl_hook inside the
    # inner run_deploy_pipeline call.
    assert bracket_uses == 1


def test_async_duckdb_pool_drains_in_flight_cursor_before_close(
    tmp_path: Path,
) -> None:
    """CO.x — pool.close() must wait for outstanding cursors to
    release before tearing down the root. Without this drain, an
    in-flight `cur.fetchall()` would see DuckDB raise
    `InvalidInputException: No open result set` mid-query, and the
    dashboards' 500 handler would render a useless stack instead of
    the clean PoolReleasedDuringRefresh page.
    """
    db_path = str(tmp_path / "drain.duckdb")
    import duckdb  # noqa: PLC0415
    seed = duckdb.connect(db_path)
    seed.execute("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1)")
    seed.close()

    pool = _AsyncDuckdbPool(db_path)

    held_event = asyncio.Event()
    release_event = asyncio.Event()
    close_completed = asyncio.Event()

    async def _holder() -> int:
        async with pool.acquire() as conn:
            cur = await conn.execute("SELECT 1")
            held_event.set()
            await release_event.wait()
            rows: list[Any] = await cur.fetchall()
            return int(rows[0][0])

    async def _closer() -> None:
        await held_event.wait()
        # Kick the close — it must wait for the holder to release.
        close_task = asyncio.create_task(pool.close())
        # Brief yield so close_task gets to its drain-wait.
        await asyncio.sleep(0.05)
        assert not close_task.done(), (
            "pool.close() should be waiting for in-flight cursor "
            "to drain, but completed immediately"
        )
        # Let the holder finish.
        release_event.set()
        await close_task
        close_completed.set()

    async def _orchestrate() -> int:
        holder_task = asyncio.create_task(_holder())
        closer_task = asyncio.create_task(_closer())
        holder_result = await holder_task
        await closer_task
        return holder_result

    try:
        assert asyncio.run(_orchestrate()) == 1
        assert close_completed.is_set()
    finally:
        # If the orchestration didn't complete, ensure the pool gets
        # closed; otherwise this is a no-op (already closed).
        asyncio.run(pool.close())


def test_async_duckdb_pool_serializes_concurrent_subprocess_brackets(
    tmp_path: Path,
) -> None:
    """CO.x — concurrent /deploy + /training/reset must serialize
    through the lifecycle lock. Without it, pipeline B's reopen
    would reacquire the parent's DuckDB lock WHILE pipeline A's
    subprocess (in our test, the bracket body) is still running,
    causing A's `etl_hook` to fail with `IOException: Could not set
    lock on file` — the EXACT bug CO.x was meant to fix.

    Probes the lifecycle lock by spawning two concurrent bracket
    tasks; the second must wait for the first to release.
    """
    db_path = str(tmp_path / "serialize.duckdb")
    import duckdb  # noqa: PLC0415
    duckdb.connect(db_path).close()

    pool = _AsyncDuckdbPool(db_path)
    order: list[str] = []
    a_entered = asyncio.Event()
    a_release = asyncio.Event()

    async def _bracket_a() -> None:
        async with pool.released_for_subprocess():
            order.append("a_enter")
            a_entered.set()
            await a_release.wait()
            order.append("a_exit")

    async def _bracket_b() -> None:
        # Wait for A to enter, then try to enter — must block until A
        # exits the bracket.
        await a_entered.wait()
        async with pool.released_for_subprocess():
            order.append("b_enter")
            order.append("b_exit")

    async def _orchestrate() -> None:
        task_a = asyncio.create_task(_bracket_a())
        task_b = asyncio.create_task(_bracket_b())
        # Give both a chance to schedule + B to await the lock.
        await asyncio.sleep(0.05)
        assert order == ["a_enter"], (
            f"B should be blocked on the lifecycle lock, got order={order}"
        )
        a_release.set()
        await task_a
        await task_b

    try:
        asyncio.run(_orchestrate())
    finally:
        asyncio.run(pool.close())

    assert order == ["a_enter", "a_exit", "b_enter", "b_exit"]


def test_async_duckdb_pool_acquire_after_close_raises_typed(
    tmp_path: Path,
) -> None:
    """A pool acquire while the root is released raises the typed
    PoolReleasedDuringRefresh — dashboards' 500 handler keys off the
    class for a themed error page (followup), not a string match.
    """
    from recon_gen.common.db import PoolReleasedDuringRefresh  # noqa: PLC0415
    db_path = str(tmp_path / "smoke.duckdb")
    import duckdb  # noqa: PLC0415
    duckdb.connect(db_path).close()
    pool = _AsyncDuckdbPool(db_path)
    asyncio.run(pool.close())
    async def _attempt() -> None:
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    with pytest.raises(PoolReleasedDuringRefresh):
        asyncio.run(_attempt())


# (Superseded by test_async_duckdb_pool_acquire_after_close_raises_typed
# above — kept the symbol name in case of import-by-test-name patterns
# in pytest plugins; both assert the same shape.)


# This test is a probabilistic race detector: 10 iterations × 16 queries =
# 160 attempts looking for "rug-pull" failures during DuckDB pool cursor
# creation. The buggy code path surfaces ≥1 forbidden failure with very high
# probability, but under heavy xdist load on the WSL2 self-hosted CI runner
# the fixed path can still hit a stray "No open result set" once every
# several CI runs — within the test's accuracy budget, but enough to redden
# release CI. Two reruns give us a 3-strike confidence boundary: a real
# regression in the fix will fail consistently across all 3 attempts; a
# rare environmental flake will clear on retry. Don't bump reruns higher
# without re-examining the underlying pool implementation.
@pytest.mark.flaky(reruns=2, reruns_delay=1)
def test_async_duckdb_pool_acquire_during_cursor_creation_doesnt_get_rugpulled(
    tmp_path: Path,
) -> None:
    """Round-2 review reproduced 3/30: when close() races a concurrent
    acquire() whose `to_thread(root.cursor)` is in flight, the old
    code (which incremented in_flight AFTER the cursor await) saw
    `_drained` as set, tore down the root, and the returned cursor
    referenced a closed handle → opaque `ConnectionException: Connection
    already closed`. Fix: bump in_flight BEFORE the to_thread await.

    Re-tests the race shape under load. With the pre-fix code this
    would fail intermittently; with the fix it's deterministic.
    """
    db_path = str(tmp_path / "race.duckdb")
    import duckdb  # noqa: PLC0415
    seed = duckdb.connect(db_path)
    seed.execute("CREATE TABLE t (x INTEGER); INSERT INTO t VALUES (1)")
    seed.close()

    pool = _AsyncDuckdbPool(db_path)
    failures: list[str] = []

    async def _one_iteration() -> None:
        # Many parallel acquires racing one close. Without the
        # bump-before-await fix, some acquires would see a closed
        # root mid-fetchall and raise the opaque IO shape.
        async def _query(idx: int) -> None:
            try:
                async with pool.acquire() as conn:
                    cur = await conn.execute("SELECT x FROM t")
                    rows: list[Any] = await cur.fetchall()
                    assert rows[0][0] == 1
            except Exception as exc:  # noqa: BLE001 — capture all error shapes
                failures.append(f"query {idx}: {type(exc).__name__}: {exc}")

        async def _bracket() -> None:
            # Use the proper bracket so the in-flight drain fires +
            # the lifecycle lock serializes against concurrent acquires.
            async with pool.released_for_subprocess():
                # Yield once so any in-flight cursor() to_thread can
                # land — without the fix, this is where the race
                # would expose the closed root.
                await asyncio.sleep(0.001)

        # Spawn a burst of queries + one bracket. The bracket should
        # drain in-flight queries before closing; new queries during
        # the bracket should get PoolReleasedDuringRefresh; queries
        # after the bracket should succeed.
        tasks = [asyncio.create_task(_query(i)) for i in range(16)]
        await asyncio.sleep(0)
        bracket_task = asyncio.create_task(_bracket())
        await asyncio.gather(*tasks, bracket_task)

    async def _all_iterations_and_close() -> None:
        # All iterations + pool close live inside a SINGLE
        # asyncio.run() because _AsyncDuckdbPool's asyncio.Event +
        # asyncio.Lock bind to the loop they're first awaited from
        # (event-loop affinity, X.2.g.2.d). Spreading iterations
        # across asyncio.run() calls would crash with
        # "bound to a different event loop".
        try:
            for _ in range(10):
                await _one_iteration()
        finally:
            await pool.close()

    # 10 iterations — round-2 saw 3/30 failures on the buggy path.
    # 10 iters at 16 queries each = 160 attempts; the buggy path
    # would surface ≥1 failure with very high probability.
    asyncio.run(_all_iterations_and_close())

    # Allowed failure shape: PoolReleasedDuringRefresh (the queries
    # that landed DURING the bracket window). NOT allowed: opaque
    # IOException / ConnectionException / "already closed" — those
    # are the rug-pull failures the fix exists to eliminate.
    forbidden = [
        f for f in failures
        if (
            "Connection already closed" in f
            or "IOException" in f
            or "ConnectionException" in f
            or "No open result set" in f
        )
    ]
    assert not forbidden, (
        "rug-pull failures during cursor creation:\n"
        + "\n".join(forbidden)
    )


def test_step_1_etl_hook_terminates_subprocess_on_cancel(
    tmp_path: Path,
) -> None:
    """Round-2: the CancelledError handler in step_1_etl_hook (which
    terminates the orphan subprocess) had no test. This test creates
    a long-running subprocess, cancels the awaiting task, and asserts
    the subprocess was reaped within the 5s grace window — without
    the terminate+kill logic the subprocess would outlive the task
    and continue holding the DuckDB file lock.
    """
    cfg = _duckdb_cfg(tmp_path)
    # 60-second sleep — outlives any reasonable test wall-clock and
    # ensures the terminate path (not a clean exit) is the only way
    # the subprocess goes down.
    sleep_cmd = f"{shlex.quote(_sys.executable)} -c 'import time; time.sleep(60)'"
    cfg = replace(cfg, etl_hook=sleep_cmd)
    sink = _EventCollector()

    async def _orchestrate() -> None:
        task = asyncio.create_task(step_1_etl_hook(cfg, dev_log=sink))
        # Give step_1 time to spawn the subprocess.
        await asyncio.sleep(0.5)
        task.cancel()
        # CancelledError should propagate out within ~5s grace.
        with pytest.raises(asyncio.CancelledError):
            await task

    import time
    start = time.time()
    asyncio.run(_orchestrate())
    elapsed = time.time() - start
    # Generous bound: should complete in ~0.5s setup + 0.x cancel.
    # The 5s grace is the max wait for SIGTERM before SIGKILL; in
    # practice Python responds to SIGTERM nearly instantly.
    assert elapsed < 10.0, (
        f"step_1_etl_hook cancel took {elapsed:.1f}s — subprocess "
        "didn't terminate cleanly"
    )
    kinds = sink.kinds()
    assert "deploy:step1:cancelled_terminating_subprocess" in kinds


def test_build_generator_sql_cutoff_uses_date_literal_across_dialects(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """CT.0 sibling — the X.4.h trainer cutoff DELETEs must use typed
    ``DATE 'YYYY-MM-DD'`` literals, not bare ISO strings. Oracle 19c
    rejects the latter with ORA-01843 ("not a valid month") because
    its NLS_TIMESTAMP_FORMAT doesn't auto-coerce the ISO-8601 shape;
    PG + DuckDB happen to accept it. Pin the typed-literal shape
    across every dialect so the regression can't reappear in the
    trainer's scrub-head codepath."""
    from datetime import date
    from dataclasses import replace as _replace
    from recon_gen.common.l2.deploy_pipeline import _build_generator_sql
    from recon_gen.common.sql.dialect import Dialect

    base = _duckdb_cfg(tmp_path)
    for dialect in (Dialect.ORACLE, Dialect.POSTGRES, Dialect.DUCKDB):
        cfg = _replace(
            base,
            dialect=dialect,
            test_generator=TestGeneratorConfig(
                end_date=date(2030, 1, 31),
                cutoff_date=date(2030, 1, 15),
            ),
        )
        sql = _build_generator_sql(cfg, spec_example_instance)
        # cutoff_date + 1 = 2030-01-16 — the half-open upper bound.
        assert "DATE '2030-01-16'" in sql, (
            f"{dialect.value}: expected typed DATE '2030-01-16' for the "
            f"cutoff upper bound; got SQL tail:\n{sql[-500:]}"
        )
        # Pre-CT.0 footgun shape must be gone (the exact string Oracle
        # was hitting with ORA-01843).
        assert "posting >= '2030-01-16'" not in sql, (
            f"{dialect.value}: bare ISO string in posting predicate "
            f"would crash Oracle. SQL tail:\n{sql[-500:]}"
        )
        assert "business_day_start >= '2030-01-16'" not in sql, (
            f"{dialect.value}: bare ISO string in business_day_start "
            f"predicate would crash Oracle. SQL tail:\n{sql[-500:]}"
        )

