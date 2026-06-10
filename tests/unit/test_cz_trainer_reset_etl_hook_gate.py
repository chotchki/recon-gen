"""CZ.3 — Trainer reset etl_hook gate + DELETE-synthetic-only path.

Phase CZ's threat model: the customer turns off ``cfg.etl_hook`` (e.g.
after their real ETL backfill populated the demo DB with real
transactions / balances). The Trainer reset button still TRUNCATEs
+ reseeds — silently wiping real data with no next-cycle refill to
restore it.

CZ.3 puts a safety gate on the Trainer reset path. The Studio
Deploy-changes button gets a separate gate (CZ.4 — refuse outright);
the Trainer button keeps demo-data-wipe semantics in standalone-mode
by narrowing the wipe to rows the seed pipeline stamps via CZ.2
(``metadata.source = 'training'``). Unmarked rows are presumed real
and survive.

This file pins three contracts:

1. ``cfg.etl_hook`` configured → ``run_deploy_pipeline`` is invoked
   with ``synthetic_only_wipe=False`` (legacy full-TRUNCATE path,
   matches pre-CZ behavior).
2. ``cfg.etl_hook is None`` → ``run_deploy_pipeline`` is invoked with
   ``synthetic_only_wipe=True`` (DELETE-synthetic-only path).
3. The DELETE-synthetic-only SQL preserves rows where
   ``metadata.source`` is not ``'training'`` — the load-bearing
   "presumed real" invariant. Asserted end-to-end against DuckDB
   (`json_extract_string(metadata, '$.source') = 'training'`) by
   planting one row of each shape (training / real / unstamped /
   non-JSON-null metadata) + running the wipe + reading what
   survived.

The DuckDB end-to-end (3) is the trustworthy one — it proves the
SQL the dialect emitter generates actually fires against a real
engine. (1) + (2) are call-boundary checks via monkeypatch so the
test doesn't depend on the pipeline's full step 1-5 surface running
cleanly under the test rig.
"""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

starlette = pytest.importorskip("starlette")
TestClient = pytest.importorskip("starlette.testclient").TestClient

from recon_gen.common.config import Config
from recon_gen.common.db import (
    connect_demo_db,
    execute_script,
    fetch_one_required,
    make_demo_database_url,
)
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.html._studio_routes import make_studio_routes
from recon_gen.common.html.server import ServedDashboard, make_app
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.deploy_pipeline import step_2_wipe
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import emit_schema, wipe_demo_data_sql
from recon_gen.common.sql import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"


@pytest.fixture
def writable_l2_yaml(tmp_path: Path) -> Iterator[Path]:
    src = _FIXTURES / "spec_example.yaml"
    dst = tmp_path / "spec_example.yaml"
    shutil.copy(src, dst)
    yield dst


@pytest.fixture
def spec_example_instance() -> L2Instance:
    return load_instance(_FIXTURES / "spec_example.yaml")


def _duckdb_cfg(tmp_path: Path, **overrides: object) -> Config:
    db_path = tmp_path / "demo.duckdb"
    base = Config(
        aws_account_id="111122223333",
        aws_region="us-east-1",
        deployment_name="recon-test",
        db_table_prefix="test",
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:111122223333:datasource/x"
        ),
        demo_database_url=make_demo_database_url(Dialect.DUCKDB, db_path),
        dialect=Dialect.DUCKDB,
    )
    if overrides:
        base = replace(base, **overrides)  # type: ignore[arg-type]: replace's overload erases the per-field types
    return base


def _apply_schema(cfg: Config, instance: L2Instance) -> None:
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


def _build_app(yaml_path: Path, cfg: Config | None) -> object:
    cache = L2InstanceCache.from_path(yaml_path)
    smoke_cfg = _duckdb_cfg(yaml_path.parent)
    tree_app, sheet = build_smoke_app(smoke_cfg)
    served = ServedDashboard(
        tree_app=tree_app, sheet=sheet, title="smoke",
        data_fetcher=stub_money_trail_fetcher,
        filter_specs=SMOKE_FILTER_SPECS,
    )
    return make_app(
        dashboards={"smoke": served},
        studio_routes=make_studio_routes(cache, cfg=cfg),
    )


# ============================================================
# (1) — wipe_demo_data_sql SQL-emitter contract
# ============================================================

def test_wipe_sql_default_unchanged_full_truncate(
    spec_example_instance: L2Instance,
) -> None:
    """Default (``synthetic_only=False``) preserves the legacy
    full-DELETE SQL. No WHERE clause; the SQL operates on every row."""
    sql = wipe_demo_data_sql(
        spec_example_instance, prefix="test", dialect=Dialect.DUCKDB,
    )
    assert "DELETE FROM test_daily_balances;" in sql
    assert "DELETE FROM test_transactions;" in sql
    # No metadata WHERE clause on the default path.
    assert "metadata" not in sql.lower()


def test_wipe_sql_synthetic_only_narrows_to_training_source_duckdb(
    spec_example_instance: L2Instance,
) -> None:
    """``synthetic_only=True`` on DuckDB uses ``json_extract_string``
    (DuckDB's ``JSON_VALUE`` returns quoted JSON form). Both base
    tables get the WHERE narrowing; the literal ``'training'`` is the
    CZ.2-stamped source value."""
    sql = wipe_demo_data_sql(
        spec_example_instance,
        prefix="test",
        dialect=Dialect.DUCKDB,
        synthetic_only=True,
    )
    assert "json_extract_string(metadata, '$.source')" in sql
    assert "= 'training'" in sql
    assert "DELETE FROM test_daily_balances WHERE" in sql
    assert "DELETE FROM test_transactions WHERE" in sql


def test_wipe_sql_synthetic_only_uses_json_value_on_postgres(
    spec_example_instance: L2Instance,
) -> None:
    """PG uses the SQL/JSON-standard ``JSON_VALUE`` (project portability
    constraint — no JSONB ``->>``)."""
    sql = wipe_demo_data_sql(
        spec_example_instance,
        prefix="test",
        dialect=Dialect.POSTGRES,
        synthetic_only=True,
    )
    assert "JSON_VALUE(metadata, '$.source')" in sql
    assert "= 'training'" in sql


def test_wipe_sql_synthetic_only_uses_json_value_on_oracle(
    spec_example_instance: L2Instance,
) -> None:
    """Oracle 19c+ uses the same SQL/JSON-standard ``JSON_VALUE``."""
    sql = wipe_demo_data_sql(
        spec_example_instance,
        prefix="test",
        dialect=Dialect.ORACLE,
        synthetic_only=True,
    )
    assert "JSON_VALUE(metadata, '$.source')" in sql
    assert "= 'training'" in sql


# ============================================================
# (2) — step_2_wipe DELETE-synthetic-only on DuckDB
# ============================================================

def _plant_tagged_rows(cfg: Config) -> None:
    """Plant four rows across both base tables, varying metadata:

    - ``t1`` / ``b1`` — synthetic (``metadata.source='training'``);
      should be deleted on the synthetic-only path.
    - ``t2`` / ``b2`` — real customer data (``metadata.source='real'``
      — but per CZ design, real-row stamping is NOT required of
      integrators); should survive.
    - ``t3`` / ``b3`` — unmarked metadata (``{}``); presumed real,
      should survive.
    - ``t4`` / ``b4`` — NULL metadata; presumed real, should survive.

    The four rows cover the full predicate surface for the wipe SQL:
    matching the stamp, mismatching the stamp, JSON without the key,
    and SQL NULL. Each behaves correctly under SQL/JSON-standard
    ``JSON_VALUE`` returning NULL on missing path / NULL input.
    """
    p = cfg.db_table_prefix
    tx_inserts = [
        # t1: synthetic — should be wiped
        f"""INSERT INTO {p}_transactions (
            id, account_id, account_scope,
            amount_money, amount_direction, status, posting,
            transfer_id, rail_name, origin, metadata
        ) VALUES (
            't1', 'a1', 'internal',
            100.00, 'Credit', 'posted', '2030-01-01 00:00:00',
            'g1', 'r1', 'inbound', '{{"source":"training"}}'
        );""",
        # t2: marked-real — should survive
        f"""INSERT INTO {p}_transactions (
            id, account_id, account_scope,
            amount_money, amount_direction, status, posting,
            transfer_id, rail_name, origin, metadata
        ) VALUES (
            't2', 'a2', 'internal',
            200.00, 'Credit', 'posted', '2030-01-01 00:00:00',
            'g2', 'r2', 'inbound', '{{"source":"real"}}'
        );""",
        # t3: unmarked JSON — presumed real, should survive
        f"""INSERT INTO {p}_transactions (
            id, account_id, account_scope,
            amount_money, amount_direction, status, posting,
            transfer_id, rail_name, origin, metadata
        ) VALUES (
            't3', 'a3', 'internal',
            300.00, 'Credit', 'posted', '2030-01-01 00:00:00',
            'g3', 'r3', 'inbound', '{{}}'
        );""",
        # t4: NULL metadata — presumed real, should survive
        f"""INSERT INTO {p}_transactions (
            id, account_id, account_scope,
            amount_money, amount_direction, status, posting,
            transfer_id, rail_name, origin, metadata
        ) VALUES (
            't4', 'a4', 'internal',
            400.00, 'Credit', 'posted', '2030-01-01 00:00:00',
            'g4', 'r4', 'inbound', NULL
        );""",
    ]
    bal_inserts = [
        f"""INSERT INTO {p}_daily_balances (
            account_id, account_scope,
            business_day_start, business_day_end, money, metadata
        ) VALUES (
            'a1', 'internal',
            '2030-01-01 00:00:00', '2030-01-02 00:00:00', 100.00,
            '{{"source":"training"}}'
        );""",
        f"""INSERT INTO {p}_daily_balances (
            account_id, account_scope,
            business_day_start, business_day_end, money, metadata
        ) VALUES (
            'a2', 'internal',
            '2030-01-01 00:00:00', '2030-01-02 00:00:00', 200.00,
            '{{"source":"real"}}'
        );""",
        f"""INSERT INTO {p}_daily_balances (
            account_id, account_scope,
            business_day_start, business_day_end, money, metadata
        ) VALUES (
            'a3', 'internal',
            '2030-01-01 00:00:00', '2030-01-02 00:00:00', 300.00,
            '{{}}'
        );""",
        f"""INSERT INTO {p}_daily_balances (
            account_id, account_scope,
            business_day_start, business_day_end, money, metadata
        ) VALUES (
            'a4', 'internal',
            '2030-01-01 00:00:00', '2030-01-02 00:00:00', 400.00,
            NULL
        );""",
    ]
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            for stmt in tx_inserts + bal_inserts:
                execute_script(cur, stmt, dialect=cfg.dialect)
            conn.commit()
        finally:
            cur.close()
    finally:
        conn.close()


def _read_transaction_ids(cfg: Config) -> list[str]:
    p = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT id FROM {p}_transactions ORDER BY id")
            return [str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()
    finally:
        conn.close()


def _read_balance_account_ids(cfg: Config) -> list[str]:
    p = cfg.db_table_prefix
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT account_id FROM {p}_daily_balances ORDER BY account_id"
            )
            return [str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()
    finally:
        conn.close()


def _row_counts(cfg: Config) -> tuple[int, int]:
    p = cfg.db_table_prefix
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


def test_step_2_wipe_synthetic_only_deletes_only_training_rows(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """End-to-end on DuckDB — synthetic-only wipe deletes only the
    ``metadata.source='training'`` rows. The other three shapes
    (marked-real / unmarked JSON / NULL metadata) all survive.

    CZ.6.1 — etl_hook is set so step_2_wipe's auto-mark stays
    silent (otherwise unmarked rows would be auto-stamped 'training'
    in standalone mode + caught by the synthetic_only WHERE). This
    test specifically isolates the CZ.3 wipe SQL narrowing contract;
    the CZ.6.1 standalone-mode auto-mark interaction is covered in
    tests/unit/test_cz_migrate_mark.py.
    """
    cfg = _duckdb_cfg(tmp_path, etl_hook="/bin/true")
    _apply_schema(cfg, spec_example_instance)
    _plant_tagged_rows(cfg)
    pre_tx, pre_bal = _row_counts(cfg)
    assert (pre_tx, pre_bal) == (4, 4), "fixture should plant 4 rows per table"

    tx_pre, bal_pre = asyncio.run(
        step_2_wipe(
            cfg, spec_example_instance, dev_log=None, synthetic_only=True,
        ),
    )
    # step_2_wipe returns PRE-DELETE row counts (the "wiped N" message
    # framing) — both tables had 4 rows before the wipe ran.
    assert (tx_pre, bal_pre) == (4, 4)

    # Only the synthetic rows (t1 / a1) deleted; t2 / t3 / t4 + a2 / a3 / a4
    # all survive as presumed real / marked real.
    assert _read_transaction_ids(cfg) == ["t2", "t3", "t4"]
    assert _read_balance_account_ids(cfg) == ["a2", "a3", "a4"]


def test_step_2_wipe_default_path_deletes_all_rows(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """The default (ETL-mode) path keeps the full-TRUNCATE semantics —
    every row goes, regardless of metadata stamp. Sanity that CZ.3's
    new synthetic_only param doesn't accidentally fire on the
    ``cfg.etl_hook`` configured path."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_schema(cfg, spec_example_instance)
    _plant_tagged_rows(cfg)
    asyncio.run(
        step_2_wipe(
            cfg, spec_example_instance, dev_log=None, synthetic_only=False,
        ),
    )
    assert _read_transaction_ids(cfg) == []
    assert _read_balance_account_ids(cfg) == []


def test_step_2_wipe_synthetic_only_event_payload_flags_path(
    tmp_path: Path, spec_example_instance: L2Instance,
) -> None:
    """Both the start + done events carry ``synthetic_only`` so the
    studio's POST /training/reset live-tail can render which path
    actually ran. Operator-debuggability hinges on this."""
    cfg = _duckdb_cfg(tmp_path)
    _apply_schema(cfg, spec_example_instance)
    _plant_tagged_rows(cfg)
    events: list[object] = []

    async def _sink(payload: object) -> None:
        events.append(payload)

    asyncio.run(
        step_2_wipe(
            cfg,
            spec_example_instance,
            dev_log=_sink,  # type: ignore[arg-type]: DevLogWriter is Callable[[Mapping], Awaitable[None]]; the `object` sink accepts the wider payload type used in tests
            synthetic_only=True,
        ),
    )

    starts = [e for e in events if e.get("event") == "deploy:step2:wipe:start"]  # type: ignore[union-attr]: events are Mapping[str, object] at runtime; .get is safe
    dones = [e for e in events if e.get("event") == "deploy:step2:wipe:done"]  # type: ignore[union-attr]: events are Mapping[str, object] at runtime; .get is safe
    assert len(starts) == 1 and len(dones) == 1
    assert starts[0]["synthetic_only"] is True  # type: ignore[index]: event Mappings carry the flag per CZ.3
    assert dones[0]["synthetic_only"] is True  # type: ignore[index]: event Mappings carry the flag per CZ.3


# ============================================================
# (3) — route-level: training_reset selects the correct path
# ============================================================

def _spy_pipeline_factory() -> tuple[
    list[dict[str, object]], object,
]:
    """Return ``(calls, async_callable)``. The callable records each
    invocation's kwargs into ``calls`` and returns a minimal stub
    summary (the route only redirects; it doesn't read the summary)."""
    calls: list[dict[str, object]] = []

    async def _spy(*args: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))

        # Minimal DeploySummary-shaped stub so the redirect doesn't blow
        # up on attribute access (the handler ignores the return value —
        # it just 303s back to /training/?reset=1).
        class _Stub:
            halted = False

        return _Stub()

    return calls, _spy


def test_training_reset_etl_hook_none_runs_synthetic_only_wipe(
    writable_l2_yaml: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cfg.etl_hook is None`` (standalone-mode) → the route invokes
    ``run_deploy_pipeline(..., synthetic_only_wipe=True)``."""
    cfg = _duckdb_cfg(tmp_path)
    assert cfg.etl_hook is None  # sanity — the standalone scenario

    calls, spy = _spy_pipeline_factory()
    monkeypatch.setattr(
        "recon_gen.common.html._studio_routes.run_deploy_pipeline", spy,
    )

    app = _build_app(writable_l2_yaml, cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/training/reset", follow_redirects=False)
    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["synthetic_only_wipe"] is True


def test_training_reset_etl_hook_configured_runs_full_truncate(
    writable_l2_yaml: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cfg.etl_hook = "true"`` (ETL-mode) → the route invokes
    ``run_deploy_pipeline(..., synthetic_only_wipe=False)`` (legacy
    full-TRUNCATE). Next ETL cycle refills, so wiping everything is
    safe."""
    cfg = _duckdb_cfg(tmp_path, etl_hook="true")
    assert cfg.etl_hook == "true"

    calls, spy = _spy_pipeline_factory()
    monkeypatch.setattr(
        "recon_gen.common.html._studio_routes.run_deploy_pipeline", spy,
    )

    app = _build_app(writable_l2_yaml, cfg)
    with TestClient(app) as c:  # type: ignore[arg-type]: TestClient accepts ASGI apps but make_app returns Any
        resp = c.post("/training/reset", follow_redirects=False)
    assert resp.status_code == 303
    assert len(calls) == 1
    assert calls[0]["synthetic_only_wipe"] is False
