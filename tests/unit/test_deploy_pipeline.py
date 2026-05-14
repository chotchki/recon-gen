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
from pathlib import Path

import pytest

from quicksight_gen.common.config import Config
from quicksight_gen.common.db import connect_demo_db, execute_script
from quicksight_gen.common.config import (
    EtlDatasourceConfig,
    TestGeneratorConfig,
)
from quicksight_gen.common.l2.deploy_pipeline import (
    DeploySummary,
    get_data_generation_id,
    run_deploy_pipeline,
    step_1_etl_hook,
    step_2_pull,
    step_2_wipe,
    step_3_generator,
    step_4_matviews,
    step_5_reload,
)
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.primitives import L2Instance
from quicksight_gen.common.l2.schema import (
    BASE_DAILY_BALANCES_COLUMNS,
    BASE_TRANSACTIONS_COLUMNS,
    emit_schema,
    wipe_demo_data_sql,
)
from quicksight_gen.common.sql import Dialect


def _base_cfg() -> Config:
    return Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
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
    schema_sql = emit_schema(instance, dialect=cfg.dialect)
    p = instance.instance
    plant_tx = (
        f"INSERT INTO {p}_transactions ("
        "id, account_id, account_scope, "
        "amount_money, amount_direction, status, posting, "
        "transfer_id, transfer_type, rail_name, origin"
        ") VALUES ("
        "'t1', 'a1', 'internal', "
        "100.00, 'Credit', 'posted', '2030-01-01 00:00:00', "
        "'g1', 'cash_withdrawal', 'r1', 'inbound'"
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
    p = instance.instance
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
    sql = wipe_demo_data_sql(
        spec_example_instance, dialect=Dialect.POSTGRES,
    )
    p = spec_example_instance.instance
    assert f"DELETE FROM {p}_daily_balances;" in sql
    assert f"DELETE FROM {p}_transactions;" in sql


def test_wipe_demo_data_sql_oracle_format(
    spec_example_instance: L2Instance,
) -> None:
    """Oracle accepts the same DELETE statements (case-folds the
    unquoted identifiers to uppercase to match the schema)."""
    sql = wipe_demo_data_sql(
        spec_example_instance, dialect=Dialect.ORACLE,
    )
    p = spec_example_instance.instance
    assert f"DELETE FROM {p}_daily_balances;" in sql
    assert f"DELETE FROM {p}_transactions;" in sql


def test_wipe_demo_data_sql_sqlite_format(
    spec_example_instance: L2Instance,
) -> None:
    sql = wipe_demo_data_sql(
        spec_example_instance, dialect=Dialect.SQLITE,
    )
    p = spec_example_instance.instance
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
    assert start["instance"] == spec_example_instance.instance
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
# step_2_pull (X.4.g.6)
# ============================================================


def _build_etl_source_sqlite(
    src_path: Path,
    *,
    txn_rows: int,
    bal_rows: int,
    posting_dates: list[str] | None = None,
    bd_end_dates: list[str] | None = None,
) -> None:
    """Provision a SQLite tempfile to act as an external etl_datasource.

    Schema mirrors the v6 base tables (column-by-column). Default
    posting/business_day_end dates are 2030-01-01 + i days; override
    via parameters for end_date filter tests.
    """
    import sqlite3
    conn = sqlite3.connect(src_path)
    try:
        cur = conn.cursor()
        # Mirror just the columns the pull cares about; CHECK
        # constraints would fail us in tests trying to assert "the
        # source can have arbitrary shape", so they're omitted here.
        cur.execute(
            "CREATE TABLE etl_txns ("
            + ", ".join(f"{c} TEXT" for c in BASE_TRANSACTIONS_COLUMNS)
            + ")"
        )
        cur.execute(
            "CREATE TABLE etl_balances ("
            + ", ".join(f"{c} TEXT" for c in BASE_DAILY_BALANCES_COLUMNS)
            + ")"
        )
        for i in range(txn_rows):
            posting = (
                posting_dates[i] if posting_dates
                else f"2030-01-{(i % 28) + 1:02d}"
            )
            row = [
                f"t{i}", f"a{i}", f"Acct {i}", "role",
                "internal", None, "100.00", "Credit", "posted",
                posting, f"g{i}", "cash_withdrawal", None, None,
                "r1", None, None, None, "inbound", None,
            ]
            cur.execute(
                "INSERT INTO etl_txns VALUES ("
                + ", ".join(["?"] * len(BASE_TRANSACTIONS_COLUMNS))
                + ")",
                row,
            )
        for i in range(bal_rows):
            bd_end = (
                bd_end_dates[i] if bd_end_dates
                else f"2030-01-{(i % 28) + 2:02d}"
            )
            row = [
                f"a{i}", f"Acct {i}", "role", "internal", None,
                None, f"2030-01-{(i % 28) + 1:02d}", bd_end,
                "100.00", None, None,
            ]
            cur.execute(
                "INSERT INTO etl_balances VALUES ("
                + ", ".join(["?"] * len(BASE_DAILY_BALANCES_COLUMNS))
                + ")",
                row,
            )
        conn.commit()
    finally:
        conn.close()


def _apply_demo_schema_only(
    cfg: Config, instance: L2Instance,
) -> None:
    """Apply the demo schema without planting any rows — the pull is
    what fills the base tables."""
    schema_sql = emit_schema(instance, dialect=cfg.dialect)
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


# ---------- skip path ----------

def test_step_2_pull_skip_when_etl_datasource_unset(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    cfg = _sqlite_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    tx, bal = asyncio.run(
        step_2_pull(cfg, spec_example_instance, dev_log=sink),
    )
    assert (tx, bal) == (0, 0)
    assert sink.kinds() == ["deploy:step2:pull:skip"]


# ---------- happy paths ----------

def test_step_2_pull_sqlite_to_sqlite_no_filter(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Degenerate same-dialect pull. No end_date filter — all rows
    flow through. Exercises the SELECT/INSERT plumbing."""
    src_path = tmp_path / "etl.sqlite"
    _build_etl_source_sqlite(src_path, txn_rows=3, bal_rows=2)
    cfg = replace(
        _sqlite_cfg(tmp_path),
        etl_datasource=EtlDatasourceConfig(
            url=f"sqlite:///{src_path}",
            transactions_table="etl_txns",
            daily_balances_table="etl_balances",
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    sink = _EventCollector()
    tx, bal = asyncio.run(
        step_2_pull(cfg, spec_example_instance, dev_log=sink),
    )
    assert (tx, bal) == (3, 2)
    assert _row_counts(cfg, spec_example_instance) == (3, 2)
    kinds = sink.kinds()
    assert kinds == [
        "deploy:step2:pull:start",
        "deploy:step2:pull:done",
    ]
    start = sink.by_kind("deploy:step2:pull:start")[0]
    assert start["source_dialect"] == "sqlite"
    assert start["dest_dialect"] == "sqlite"
    assert start["end_date"] is None
    done = sink.by_kind("deploy:step2:pull:done")[0]
    assert done["transactions_pulled"] == 3
    assert done["daily_balances_pulled"] == 2


def test_step_2_pull_end_date_filter_drops_future_rows(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """end_date carves the source: rows after the cutoff aren't pulled."""
    from datetime import date
    from quicksight_gen.common.config import TestGeneratorConfig
    src_path = tmp_path / "etl.sqlite"
    _build_etl_source_sqlite(
        src_path,
        txn_rows=4,
        bal_rows=4,
        posting_dates=[
            "2030-01-01", "2030-01-15", "2030-02-01", "2030-03-15",
        ],
        bd_end_dates=[
            "2030-01-02", "2030-01-16", "2030-02-02", "2030-03-16",
        ],
    )
    cfg = replace(
        _sqlite_cfg(tmp_path),
        etl_datasource=EtlDatasourceConfig(
            url=f"sqlite:///{src_path}",
            transactions_table="etl_txns",
            daily_balances_table="etl_balances",
        ),
        test_generator=TestGeneratorConfig(end_date=date(2030, 1, 31)),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    tx, bal = asyncio.run(
        step_2_pull(cfg, spec_example_instance, dev_log=None),
    )
    # Jan 1 + Jan 15 in; Feb 1 + Mar 15 out.
    assert tx == 2
    # Jan 2 + Jan 16 in; Feb 2 + Mar 16 out.
    assert bal == 2


def test_step_2_pull_empty_source(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    src_path = tmp_path / "etl.sqlite"
    _build_etl_source_sqlite(src_path, txn_rows=0, bal_rows=0)
    cfg = replace(
        _sqlite_cfg(tmp_path),
        etl_datasource=EtlDatasourceConfig(
            url=f"sqlite:///{src_path}",
            transactions_table="etl_txns",
            daily_balances_table="etl_balances",
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    tx, bal = asyncio.run(
        step_2_pull(cfg, spec_example_instance, dev_log=None),
    )
    assert (tx, bal) == (0, 0)


def test_step_2_pull_multi_batch_completes(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """12 rows with batch_size=5 → 3 fetchmany batches; all rows land."""
    src_path = tmp_path / "etl.sqlite"
    _build_etl_source_sqlite(src_path, txn_rows=12, bal_rows=8)
    cfg = replace(
        _sqlite_cfg(tmp_path),
        etl_datasource=EtlDatasourceConfig(
            url=f"sqlite:///{src_path}",
            transactions_table="etl_txns",
            daily_balances_table="etl_balances",
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    tx, bal = asyncio.run(
        step_2_pull(
            cfg, spec_example_instance, dev_log=None, batch_size=5,
        ),
    )
    assert (tx, bal) == (12, 8)
    assert _row_counts(cfg, spec_example_instance) == (12, 8)


# ---------- failure modes ----------

def test_step_2_pull_missing_source_column_loud_fails(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Source missing a v6 column → SELECT raises, contract violated."""
    import sqlite3
    src_path = tmp_path / "etl.sqlite"
    conn = sqlite3.connect(src_path)
    try:
        cur = conn.cursor()
        # Source has only id + account_id — no amount_money etc.
        cur.execute(
            "CREATE TABLE etl_txns (id TEXT, account_id TEXT)"
        )
        cur.execute(
            "CREATE TABLE etl_balances ("
            + ", ".join(f"{c} TEXT" for c in BASE_DAILY_BALANCES_COLUMNS)
            + ")"
        )
        conn.commit()
    finally:
        conn.close()
    cfg = replace(
        _sqlite_cfg(tmp_path),
        etl_datasource=EtlDatasourceConfig(
            url=f"sqlite:///{src_path}",
            transactions_table="etl_txns",
            daily_balances_table="etl_balances",
        ),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        asyncio.run(
            step_2_pull(cfg, spec_example_instance, dev_log=None),
        )


# ---------- column-list drift guard ----------

def test_base_columns_match_emitted_schema(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """BASE_*_COLUMNS must stay in sync with what emit_schema creates.

    Apply the schema to a fresh sqlite and PRAGMA table_info to
    extract the actual columns; assert the constants match (excluding
    the auto-generated ``entry`` column the pull intentionally drops)."""
    cfg = _sqlite_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    p = spec_example_instance.instance
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"PRAGMA table_info({p}_transactions)")
            actual_tx = tuple(
                row[1] for row in cur.fetchall() if row[1] != "entry"
            )
            cur.execute(f"PRAGMA table_info({p}_daily_balances)")
            actual_bal = tuple(
                row[1] for row in cur.fetchall() if row[1] != "entry"
            )
        finally:
            cur.close()
    finally:
        conn.close()
    assert actual_tx == BASE_TRANSACTIONS_COLUMNS, (
        "BASE_TRANSACTIONS_COLUMNS drift vs emit_schema; update "
        "common/l2/schema.py"
    )
    assert actual_bal == BASE_DAILY_BALANCES_COLUMNS, (
        "BASE_DAILY_BALANCES_COLUMNS drift vs emit_schema; update "
        "common/l2/schema.py"
    )


# ---------- dialect-from-url ----------

def test_dialect_from_url_postgres() -> None:
    from quicksight_gen.common.l2.deploy_pipeline import _dialect_from_url
    assert _dialect_from_url("postgresql://u:p@h:5432/d") is Dialect.POSTGRES
    assert _dialect_from_url("postgres://u:p@h:5432/d") is Dialect.POSTGRES


def test_dialect_from_url_oracle() -> None:
    from quicksight_gen.common.l2.deploy_pipeline import _dialect_from_url
    assert _dialect_from_url("oracle://u:p@h:1521/svc") is Dialect.ORACLE
    assert _dialect_from_url(
        "oracle+oracledb://u:p@h:1521/svc",
    ) is Dialect.ORACLE


def test_dialect_from_url_sqlite() -> None:
    from quicksight_gen.common.l2.deploy_pipeline import _dialect_from_url
    assert _dialect_from_url("sqlite:///tmp/foo.db") is Dialect.SQLITE


def test_dialect_from_url_unknown_rejects() -> None:
    from quicksight_gen.common.l2.deploy_pipeline import _dialect_from_url
    with pytest.raises(ValueError, match="Cannot infer dialect"):
        _dialect_from_url("mysql://u:p@h:3306/d")


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
    p = spec_example_instance.instance
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"INSERT INTO {p}_transactions ("
                "id, account_id, account_scope, amount_money, "
                "amount_direction, status, posting, transfer_id, "
                "transfer_type, rail_name, origin"
                ") VALUES ("
                "'op-1', 'op-acct', 'internal', 50.00, 'Credit', "
                "'posted', '2030-01-01 00:00:00', 'op-tr', "
                "'cash_withdrawal', ?, 'inbound')",
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
    from quicksight_gen.common.l2.deploy_pipeline import _covered_rail_names
    cfg = _sqlite_cfg(tmp_path)
    _apply_demo_schema_only(cfg, spec_example_instance)
    # Empty table → empty set.
    assert _covered_rail_names(cfg, spec_example_instance) == frozenset()
    # Plant 3 rows with 2 distinct rail_names.
    p = spec_example_instance.instance
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            for i, rail in enumerate(["RailA", "RailB", "RailA"]):
                cur.execute(
                    f"INSERT INTO {p}_transactions ("
                    "id, account_id, account_scope, amount_money, "
                    "amount_direction, status, posting, transfer_id, "
                    "transfer_type, rail_name, origin"
                    ") VALUES ("
                    f"'t{i}', 'a', 'internal', 1.00, 'Credit', 'posted', "
                    f"'2030-01-01', 'g{i}', 'cash_withdrawal', ?, 'inbound')",
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
    assert start["instance"] == spec_example_instance.instance
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
    p = spec_example_instance.instance
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
    p = spec_example_instance.instance
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
    """No etl_hook configured: step 1 skips, steps 2-5 run, summary
    reports per-step counts + the post-bump data_generation_id."""
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
    # Event ordering — step 1's skip event must precede step 5's bump.
    kinds = sink.kinds()
    assert kinds[0] == "deploy:step1:skip"
    assert kinds[-1] == "deploy:step5:reload:bump"
    # Captured events on the summary include every dev_log event too.
    assert len(summary.events) == len(sink.events)


def test_run_deploy_pipeline_halts_on_etl_failure(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """etl_hook returns non-zero exit ⇒ halt BEFORE step 2 wipes the
    demo DB. Summary.halted=True, halt_reason populated, downstream
    step counts at zero defaults."""
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
    # CRITICAL: step 2's wipe MUST NOT have run — pre-existing rows
    # are still there.
    post_tx, post_bal = _row_counts(cfg, spec_example_instance)
    assert (post_tx, post_bal) == (1, 1), (
        "etl_hook failure must NOT touch the demo DB — operator's "
        "pre-pipeline state is preserved"
    )
    # Default zeros for downstream steps.
    assert summary.step2_wipe_transactions_deleted == 0
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
    assert payload["step5_data_generation_id"] > 0
    assert isinstance(payload["events"], list)
    # Every event entry MUST be a plain dict (json-safe), not a Mapping
    # subclass that JSONResponse can't serialize.
    for evt in payload["events"]:
        assert isinstance(evt, dict)


# =====================================================================
# X.4.g.15 — pipeline orchestration shapes (per the PLAN bullet's
# enumeration). Two shapes (hook-fail-halt + no-etl) are covered by
# the X.4.g.13 tests above; the three remaining shapes land here.
# =====================================================================

def test_orchestration_etl_only_path(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """etl_hook present (succeeds) + no etl_datasource: step 1 runs +
    succeeds, step 2 pull skips, step 3 generator (full scope)
    populates the demo DB on its own."""
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
    # Step 2 pull skipped (no etl_datasource).
    assert summary.step2_pull_transactions_pulled == 0
    assert summary.step2_pull_daily_balances_pulled == 0
    # Step 3 generator (full scope) carried the load.
    assert summary.step3_generator_transactions_after > 0
    assert summary.step3_generator_daily_balances_after > 0
    assert summary.step4_matviews_done is True
    # Step 1 actually ran (start + done events) — not the skip path.
    kinds = sink.kinds()
    assert "deploy:step1:start" in kinds
    assert "deploy:step1:done" in kinds
    assert "deploy:step1:skip" not in kinds


def test_orchestration_etl_then_generator_path(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """etl_datasource set + scope:full generator: step 2's pull copies
    rows from the source, then step 3 layers full-scope plants on top.
    Final transactions count = pulled + generated."""
    src_path = tmp_path / "source.sqlite"
    _build_etl_source_sqlite(src_path, txn_rows=4, bal_rows=3)
    cfg = replace(
        _sqlite_cfg(tmp_path),
        etl_datasource=EtlDatasourceConfig(
            url=f"sqlite:///{src_path}",
            transactions_table="etl_txns",
            daily_balances_table="etl_balances",
        ),
        test_generator=TestGeneratorConfig(scope="full"),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=None),
    )
    assert summary.halted is False
    # Step 2's pull moved the 4+3 rows from the source.
    assert summary.step2_pull_transactions_pulled == 4
    assert summary.step2_pull_daily_balances_pulled == 3
    # Step 3 added more on top of those — final count strictly greater
    # than the pulled row count.
    assert summary.step3_generator_transactions_after > 4
    assert summary.step3_generator_daily_balances_after > 3
    assert summary.step4_matviews_done is True


def test_orchestration_etl_then_uncovered_rails_path(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """etl_datasource set + scope:uncovered_rails: step 2 pulls, then
    step 3 only fills baseline for rails the source DIDN'T cover.
    The pipeline composition matters here, not the exact row count —
    the per-rail skip semantics are covered by the step 3 unit tests."""
    src_path = tmp_path / "source.sqlite"
    _build_etl_source_sqlite(src_path, txn_rows=2, bal_rows=2)
    cfg = replace(
        _sqlite_cfg(tmp_path),
        etl_datasource=EtlDatasourceConfig(
            url=f"sqlite:///{src_path}",
            transactions_table="etl_txns",
            daily_balances_table="etl_balances",
        ),
        test_generator=TestGeneratorConfig(scope="uncovered_rails"),
    )
    _apply_demo_schema_only(cfg, spec_example_instance)
    summary = asyncio.run(
        run_deploy_pipeline(cfg, spec_example_instance, dev_log=None),
    )
    assert summary.halted is False
    assert summary.step2_pull_transactions_pulled == 2
    assert summary.step2_pull_daily_balances_pulled == 2
    # uncovered_rails still emits baseline for the (many) uncovered
    # rails in spec_example — totals are >= the 2 pulled rows.
    assert summary.step3_generator_transactions_after >= 2
    assert summary.step4_matviews_done is True
    assert summary.step5_data_generation_id > 0

