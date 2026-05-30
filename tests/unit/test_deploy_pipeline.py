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
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db, execute_script
from recon_gen.common.config import (
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
        aws_account_id="111122223333",
        aws_region="us-east-1",
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


def _sqlite_cfg(tmp_path: Path) -> Config:
    """Config bound to a fresh SQLite tempfile for orchestrator tests."""
    db_path = tmp_path / "demo.sqlite"
    return Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
        # Z.C — Config requires deployment_name + db_table_prefix.
        # spec_example matches the bundled L2 fixture used downstream.
        deployment_name="recon-spec-example",
        db_table_prefix=DEFAULT_PREFIX,
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=f"sqlite:///{db_path}",
        dialect=Dialect.SQLITE,
    )


def _apply_schema_and_plant_two_rows(
    cfg: Config, instance: L2Instance,
) -> None:
    """Set up a SQLite tempfile DB with the L2 schema + two planted rows
    so the wipe has something to delete. Plants conform to the L2 v6
    schema's CHECK constraints (amount_direction enum, sign-direction
    agreement, account_scope enum)."""
    schema_sql = emit_schema(
        instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
    )
    p = cfg.db_table_prefix
    plant_tx = (
        f"INSERT INTO {p}_transactions ("
        "id, account_id, account_scope, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, rail_name, origin"
        ") VALUES ("
        "'t1', 'a1', 'internal', "
        "100.00, 'Credit', 'posted', '2030-01-01 00:00:00', "
        "'g1', 'r1', 'inbound'"
        ");"
    )
    plant_bal = (
        f"INSERT INTO {p}_daily_balances ("
        "account_id, account_scope, "
        "business_day_start, business_day_end, money"
        ") VALUES ("
        "'a1', 'internal', "
        "'2030-01-01 00:00:00', '2030-01-02 00:00:00', 100.00"
        ");"
    )
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(cur, schema_sql, dialect=cfg.dialect)
            execute_script(
                cur, plant_tx + "\n" + plant_bal, dialect=cfg.dialect,
            )
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def _row_counts(cfg: Config, instance: L2Instance) -> tuple[int, int]:
    p = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {p}_transactions")
            tx = int(cur.fetchone()[0])
            cur.execute(f"SELECT COUNT(*) FROM {p}_daily_balances")
            bal = int(cur.fetchone()[0])
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
        instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
    )
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            execute_script(cur, schema_sql, dialect=cfg.dialect)
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
    assert cfg.etl_hook is None
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


def test_wipe_demo_data_sql_sqlite_format(
    spec_example_instance: L2Instance,
) -> None:
    p = "spec_example"
    sql = wipe_demo_data_sql(
        spec_example_instance, prefix=p, dialect=Dialect.SQLITE,
    )
    assert f"DELETE FROM {p}_daily_balances;" in sql
    assert f"DELETE FROM {p}_transactions;" in sql


# ---------- step_2_wipe orchestrator (SQLite tempfile) ----------

def test_step_2_wipe_clears_both_base_tables(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = _sqlite_cfg(tmp_path)
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
    cfg = _sqlite_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    sink = _EventCollector()
    asyncio.run(step_2_wipe(cfg, spec_example_instance, dev_log=sink))

    kinds = sink.kinds()
    assert kinds == [
        "deploy:step2:wipe:start",
        "deploy:step2:wipe:done",
    ]
    start = sink.by_kind("deploy:step2:wipe:start")[0]
    assert start["db_table_prefix"] == cfg.db_table_prefix
    assert start["dialect"] == "sqlite"
    done = sink.by_kind("deploy:step2:wipe:done")[0]
    assert done["transactions_deleted"] == 1
    assert done["daily_balances_deleted"] == 1


def test_step_2_wipe_dev_log_none_safe(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = _sqlite_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    tx, bal = asyncio.run(
        step_2_wipe(cfg, spec_example_instance, dev_log=None),
    )
    assert (tx, bal) == (1, 1)


def test_step_2_wipe_idempotent_on_empty_tables(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Wipe-then-wipe is safe — second call reports zero deletes."""
    cfg = _sqlite_cfg(tmp_path)
    _apply_schema_and_plant_two_rows(cfg, spec_example_instance)
    asyncio.run(step_2_wipe(cfg, spec_example_instance, dev_log=None))
    tx, bal = asyncio.run(
        step_2_wipe(cfg, spec_example_instance, dev_log=None),
    )
    assert (tx, bal) == (0, 0)


# ============================================================
# step_3_generator (X.4.g.7+8)
# ============================================================


# ---------- skip path ----------

def test_step_3_generator_skip_when_disabled(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = replace(
        _sqlite_cfg(tmp_path),
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
        _sqlite_cfg(tmp_path),
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
        _sqlite_cfg(tmp_path),
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
    """X.4.h trainer cutoff — when cfg.test_generator.cutoff_date is
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
        _sqlite_cfg(no_cutoff_dir),
        test_generator=TestGeneratorConfig(end_date=date(2030, 1, 31)),
    )
    cfg_with_cutoff = replace(
        _sqlite_cfg(with_cutoff_dir),
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
        _sqlite_cfg(tmp_path),
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
            _sqlite_cfg(sub),
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
            _sqlite_cfg(sub),
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
        _sqlite_cfg(tmp_path),
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
        _sqlite_cfg(tmp_path),
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
            _sqlite_cfg(sub),
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
        _sqlite_cfg(sub),
        test_generator=TestGeneratorConfig(
            scope="uncovered_rails", end_date=date(2030, 1, 1),
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    # Pick the first rail in the L2 to "cover" — its baseline should
    # be skipped on the next emit.
    covered_rail_name = str(spec_example_instance.rails[0].name)
    p = cfg.db_table_prefix
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
    cfg = _sqlite_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    # Empty table → empty set.
    assert _covered_rail_names(cfg, spec_example_instance) == frozenset()
    # Plant 3 rows with 2 distinct rail_names.
    p = cfg.db_table_prefix
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
        _sqlite_cfg(tmp_path),
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
    """scope='only_template' with cfg.test_generator.only_template unset
    must loud-fail rather than silently degrade to scope=full."""
    from datetime import date
    cfg = replace(
        _sqlite_cfg(tmp_path),
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
        _sqlite_cfg(tmp_path),
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
    p = cfg.db_table_prefix
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
            _sqlite_cfg(sub),
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
    cur: object,  # sqlite3.Cursor
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
    p = cfg.db_table_prefix
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
    """When cfg.test_generator.derive_balances=False, the pass returns
    0 and writes nothing."""
    cfg = replace(
        _sqlite_cfg(tmp_path),
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
        _sqlite_cfg(tmp_path),
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
    p = cfg.db_table_prefix
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
        _sqlite_cfg(tmp_path),
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
    p = cfg.db_table_prefix
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
        _sqlite_cfg(tmp_path),
        test_generator=TestGeneratorConfig(derive_balances=True),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    p = cfg.db_table_prefix
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
            money = float(cur.fetchone()[0])
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
        _sqlite_cfg(tmp_path),
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
    p = cfg.db_table_prefix
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
            count, money = cur.fetchone()
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
        _sqlite_cfg(tmp_path),
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
    cfg = _sqlite_cfg(tmp_path)
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
    assert start["db_table_prefix"] == cfg.db_table_prefix
    assert start["dialect"] == cfg.dialect.value


def test_step_4_matviews_idempotent_on_empty_db(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Running matview refresh on an empty (post-wipe) DB must succeed —
    matviews exist (from the schema apply) but resolve to zero rows.
    Re-running is safe (drops + recreates)."""
    cfg = _sqlite_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    asyncio.run(
        step_4_matviews(cfg, spec_example_instance, dev_log=None),
    )
    # Second invocation must not raise (refresh is idempotent).
    asyncio.run(
        step_4_matviews(cfg, spec_example_instance, dev_log=None),
    )
    # And every matview still exists (and is empty).
    p = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {p}_drift")
            assert int(cur.fetchone()[0]) == 0
            cur.execute(f"SELECT COUNT(*) FROM {p}_overdraft")
            assert int(cur.fetchone()[0]) == 0
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
        _sqlite_cfg(tmp_path),
        test_generator=TestGeneratorConfig(end_date=date(2030, 1, 1)),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    asyncio.run(
        step_3_generator(cfg, spec_example_instance, dev_log=None),
    )
    asyncio.run(
        step_4_matviews(cfg, spec_example_instance, dev_log=None),
    )
    p = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {p}_current_transactions")
            n = int(cur.fetchone()[0])
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
    cfg = _sqlite_cfg(tmp_path)
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
        _sqlite_cfg(tmp_path),
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
    cfg = _sqlite_cfg(tmp_path)
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
        _sqlite_cfg(tmp_path),
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
    cfg = _sqlite_cfg(tmp_path)
    assert cfg.etl_hook is None
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

