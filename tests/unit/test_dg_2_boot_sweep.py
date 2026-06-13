"""DG.2 — pin the boot-sweep contract.

The sweep runs at runner-step time (between container start + plain
prefix seed) to drop every per-test ``<base>_<6hex>_*`` object that
accumulated from prior runs. This unit test pins:

1. DuckDB short-circuits to no-op (per-worker fresh .duckdb files
   share no state — no debris to clean).
2. PG sweep produces the right discovery + drop SQL shape against
   the expected catalog views.
3. Oracle sweep mirrors the PG shape with uppercase + REGEXP_LIKE.

The live-DB exercise (actually sweeping a real PG/Oracle container)
fires from the runner step under the db layer and is covered by the
CI's clean-container baseline once DG.3 triage validates it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from recon_gen._dev.runner import _sweep_test_prefixes


def _make_cfg_yaml(tmp_path: Path, dialect: str, table_prefix: str) -> Path:
    """Minimal cfg.yaml the loader accepts for the sweep test."""
    cfg_text = (
        f"aws_account_id: '470656905821'\n"
        f"aws_region: us-east-1\n"
        f"deployment_name: dg2-test\n"
        f"db_table_prefix: {table_prefix}\n"
        f"dialect: {dialect}\n"
        f"demo_database_url: 'placeholder://will-be-overridden-by-env'\n"
    )
    p = tmp_path / "cfg.yaml"
    p.write_text(cfg_text)
    return p


def test_duckdb_sweep_is_noop(tmp_path: Path) -> None:
    """DuckDB per-worker fresh files have no shared state to clean.
    Sweep returns 0 immediately + writes a no-op log line."""
    cfg_path = _make_cfg_yaml(tmp_path, dialect="duckdb", table_prefix="qsgen_du")
    rc = _sweep_test_prefixes(
        cfg_path,
        container_env={},
        run_dir=tmp_path,
    )
    assert rc == 0
    log_path = tmp_path / "sweep" / "sweep.log"
    assert log_path.exists()
    assert "DuckDB no-op" in log_path.read_text()


def test_postgres_sweep_drops_each_object_kind(tmp_path: Path) -> None:
    """PG path: discovers stale objects via pg_matviews / pg_views /
    pg_indexes / pg_tables filtered on the ``<base>_<6hex>_`` pattern,
    drops each with CASCADE in dependency-safe order (matview → view
    → index → table)."""
    cfg_path = _make_cfg_yaml(tmp_path, dialect="postgres", table_prefix="qsgen_postgres")

    # Mock the DB conn. fetchall returns the discovered names per kind;
    # we sequence them by query call order.
    mock_cur = MagicMock()
    # Sequenced fetchall results: matviews, views, indexes, tables.
    mock_cur.fetchall.side_effect = [
        [("qsgen_postgres_abc123_current_transactions",)],   # matviews
        [],                                                  # views
        [("qsgen_postgres_abc123_some_idx",)],               # indexes
        [
            ("qsgen_postgres_abc123_transactions",),
            ("qsgen_postgres_def456_transactions",),
        ],                                                   # tables
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with patch(
        "recon_gen.common.db.connect_demo_db", return_value=mock_conn,
    ):
        rc = _sweep_test_prefixes(
            cfg_path,
            container_env={"RECON_GEN_DEMO_DATABASE_URL": "postgresql://test:test@127.0.0.1:5432/test"},
            run_dir=tmp_path,
        )

    assert rc == 0
    # Execute call order: 4 discoveries interleaved with their per-kind
    # drops (discover matviews → drop matviews → discover views → drop
    # views → ...). Total = 4 discoveries + 4 drops = 8 calls.
    execute_calls: Any = mock_cur.execute.call_args_list  # pyright: ignore[reportExplicitAny]: Mock.call_args_list is partially unknown
    assert len(execute_calls) == 8, (
        f"Expected 4 discovery + 4 drop calls; got {len(execute_calls)}: "
        f"{[c[0][0] for c in execute_calls]}"
    )
    all_sqls = [str(call[0][0]) for call in execute_calls]
    discovery_sqls = [s for s in all_sqls if s.startswith("SELECT")]
    drop_sqls = [s for s in all_sqls if s.startswith("DROP")]
    assert len(discovery_sqls) == 4
    assert len(drop_sqls) == 4
    # Each catalog view fires exactly once.
    assert any("pg_matviews" in s for s in discovery_sqls)
    assert any("pg_views" in s for s in discovery_sqls)
    assert any("pg_indexes" in s for s in discovery_sqls)
    assert any("pg_tables" in s for s in discovery_sqls)
    # Discovered names reach the drop SQL.
    assert any("DROP MATERIALIZED VIEW IF EXISTS" in s for s in drop_sqls)
    assert any("DROP INDEX IF EXISTS" in s for s in drop_sqls)
    assert any('DROP TABLE IF EXISTS' in s and "abc123" in s for s in drop_sqls)
    assert any('DROP TABLE IF EXISTS' in s and "def456" in s for s in drop_sqls)
    # CASCADE on table drops (PG path).
    assert all(
        "CASCADE" in s for s in drop_sqls if "DROP TABLE" in s
    ), "PG table drops must include CASCADE to handle FK chains"
    # Commit fires exactly once at end.
    assert mock_conn.commit.call_count == 1

    log_text = (tmp_path / "sweep" / "sweep.log").read_text()
    assert "4 object(s) dropped" in log_text


def test_oracle_sweep_uppercase_pattern(tmp_path: Path) -> None:
    """Oracle path: case-folds identifiers. Discovery pattern is the
    uppercase form; user_mviews / user_views / user_indexes /
    user_tables drive discovery."""
    cfg_path = _make_cfg_yaml(tmp_path, dialect="oracle", table_prefix="qsgen_oracle")

    mock_cur = MagicMock()
    mock_cur.fetchall.side_effect = [
        [],  # matviews
        [],  # views
        [],  # indexes
        [("QSGEN_ORACLE_BADCAFE_TRANSACTIONS",)],  # tables
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with patch(
        "recon_gen.common.db.connect_demo_db", return_value=mock_conn,
    ):
        rc = _sweep_test_prefixes(
            cfg_path,
            container_env={"RECON_GEN_DEMO_DATABASE_URL": "oracle+oracledb://test:test@127.0.0.1:1521/?service_name=FREEPDB1"},
            run_dir=tmp_path,
        )

    assert rc == 0
    execute_calls: Any = mock_cur.execute.call_args_list  # pyright: ignore[reportExplicitAny]: Mock.call_args_list is partially unknown
    discovery_sqls = [str(call[0][0]) for call in execute_calls[:4]]
    assert any("user_mviews" in s for s in discovery_sqls)
    assert any("user_tables" in s for s in discovery_sqls)
    # Oracle uses REGEXP_LIKE not the PG ~ operator.
    assert all("REGEXP_LIKE" in s for s in discovery_sqls), (
        "Oracle discovery must use REGEXP_LIKE — got " + str(discovery_sqls)
    )
    # The pattern bound is the uppercase form (catalog returns
    # uppercase + the regex must match accordingly).
    # Pattern is passed via :p binding; checking the discovery query
    # bind shape on the first call.
    first_bind = execute_calls[0][0][1]  # second arg of execute()
    assert "QSGEN_ORACLE_" in first_bind["p"], (
        f"Oracle pattern bind should be uppercase; got {first_bind!r}"
    )
    # Oracle table drop uses CASCADE CONSTRAINTS (different keyword).
    table_drop = next(
        str(c[0][0]) for c in execute_calls[4:] if "DROP TABLE" in str(c[0][0])
    )
    assert "CASCADE CONSTRAINTS" in table_drop


def test_sweep_continues_on_individual_drop_failure(tmp_path: Path) -> None:
    """If one DROP raises (e.g. a dependency forces a different order),
    the sweep logs the failure but moves on to the next object —
    one stuck index shouldn't block dropping the other 50."""
    cfg_path = _make_cfg_yaml(tmp_path, dialect="postgres", table_prefix="qsgen_postgres")

    mock_cur = MagicMock()
    # Two tables discovered, first drop raises, second should still fire.
    mock_cur.fetchall.side_effect = [
        [], [], [],  # matview/view/index discoveries empty
        [
            ("qsgen_postgres_aaa111_transactions",),
            ("qsgen_postgres_bbb222_transactions",),
        ],
    ]

    def execute_side_effect(sql: str, *_args: Any) -> None:
        if "DROP TABLE" in sql and "aaa111" in sql:
            raise RuntimeError("simulated stuck drop")

    mock_cur.execute.side_effect = execute_side_effect
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with patch(
        "recon_gen.common.db.connect_demo_db", return_value=mock_conn,
    ):
        rc = _sweep_test_prefixes(
            cfg_path,
            container_env={"RECON_GEN_DEMO_DATABASE_URL": "postgresql://test:test@127.0.0.1:5432/test"},
            run_dir=tmp_path,
        )

    # Sweep itself returns 0 — per-object drop failures are logged + skipped.
    assert rc == 0
    log_text = (tmp_path / "sweep" / "sweep.log").read_text()
    assert "DROP failed for" in log_text
    assert "aaa111" in log_text
    # Second drop attempted (the side_effect would have raised again if
    # we double-attempted aaa111 — bbb222 is a separate call).
    drop_calls = [
        c for c in mock_cur.execute.call_args_list
        if "DROP TABLE" in str(c[0][0])
    ]
    assert len(drop_calls) == 2, (
        f"Both table drops should be attempted even after one failure; "
        f"got {len(drop_calls)}"
    )


def test_sweep_connection_failure_returns_non_zero(tmp_path: Path) -> None:
    """If the sweep can't even open a connection, return non-zero so
    the runner aborts the chain. Continuing on a broken DB cascades
    into confusing seed failures."""
    cfg_path = _make_cfg_yaml(tmp_path, dialect="postgres", table_prefix="qsgen_postgres")

    with patch(
        "recon_gen.common.db.connect_demo_db",
        side_effect=RuntimeError("connection refused"),
    ):
        rc = _sweep_test_prefixes(
            cfg_path,
            container_env={"RECON_GEN_DEMO_DATABASE_URL": "postgresql://test:test@127.0.0.1:5432/test"},
            run_dir=tmp_path,
        )

    assert rc != 0
    log_text = (tmp_path / "sweep" / "sweep.log").read_text()
    assert "connection refused" in log_text or "RuntimeError" in log_text
