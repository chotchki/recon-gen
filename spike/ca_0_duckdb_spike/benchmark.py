"""DuckDB matview-refresh benchmark + 3-way diff harness for spike CA.0.

Same shape as ``spike/bx_sqlite_matview_perf/benchmark.py`` but driving
DuckDB instead of SQLite, plus a 3-way row-count diff against the PG /
SQLite reference outputs.

Two run modes:

- ``--mode=perf``  Measure DuckDB's bundled refresh wallclock at a
  given target row count. Writes
  ``results_target_<rows>.md`` next to this script.
- ``--mode=diff``  Build the matview chain on DuckDB AND SQLite from
  the same seed, count rows per matview, dump computed_subledger_balance
  row-by-row, and write a 3-way comparison report.
- ``--mode=bonus`` Same as perf but USE the PG-style correlated-subquery
  body for ``computed_subledger_balance`` (the original shape; this
  is the BZ.0 SQLite arm's "pre-optimization" form). Tests the audit
  prediction that DuckDB's vectorized executor handles correlated
  SUM-WHERE-posting<=day natively.

Usage::

    .venv/bin/python -m spike.ca_0_duckdb_spike.benchmark --mode=perf --rows 130000
    .venv/bin/python -m spike.ca_0_duckdb_spike.benchmark --mode=perf --rows 250000
    .venv/bin/python -m spike.ca_0_duckdb_spike.benchmark --mode=perf --rows 1000000
    .venv/bin/python -m spike.ca_0_duckdb_spike.benchmark --mode=diff --rows 130000
    .venv/bin/python -m spike.ca_0_duckdb_spike.benchmark --mode=bonus --rows 1000000

Not production code; lives under spike/ per CA.0 charter.
"""

from __future__ import annotations

import argparse
import gc
import re
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import duckdb

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))

from recon_gen.common.config import Config  # noqa: E402
from recon_gen.common.db import (  # noqa: E402
    _register_sqlite_aggregates,
    _split_sqlite_statements,
)
from recon_gen.common.l2 import load_instance  # noqa: E402
from recon_gen.common.l2.schema import (  # noqa: E402
    emit_schema,
    refresh_matviews_sql,
)
from recon_gen.common.sql.dialect import Dialect  # noqa: E402

from spike.ca_0_duckdb_spike.translate import (  # noqa: E402
    translate_sqlite_to_duckdb,
    translate_sqlite_to_duckdb_pg_csb,
)


MATVIEW_NAMES_ORDERED: tuple[str, ...] = (
    "current_transactions",
    "current_daily_balances",
    "computed_subledger_balance",
    "computed_ledger_balance",
    "drift",
    "ledger_drift",
    "overdraft",
    "expected_eod_balance_breach",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "chain_parent_disagreement",
    "xor_group_violation",
    "transfer_parents",
    "fan_in_disagreement",
    "multi_xor_violation",
    "daily_statement_summary",
    "l1_exceptions",
    "inv_pair_rolling_anomalies",
    "inv_money_trail_edges",
)


def _make_cfg(*, dialect: Dialect, db_url: str, prefix: str) -> Config:
    return Config(  # type: ignore[call-arg]
        aws_account_id="111122223333",
        aws_region="us-east-1",
        dialect=dialect,
        demo_database_url=db_url,
        deployment_name="ca0spike",
        db_table_prefix=prefix,
    )


def _build_sqlite_setup(
    cfg: Config, instance: object, *, anchor: date,
    density: float, window_days: int,
) -> tuple[str, str, str]:
    """Return (schema_sql, populate_sql, seed_sql) for the SQLite arm."""
    schema_sql = emit_schema(instance, prefix=cfg.db_table_prefix, dialect=cfg.dialect)
    from recon_gen.cli._helpers import (  # noqa: PLC0415
        build_config_populate_sql,
        build_default_scenario,
    )
    from recon_gen.common.l2.seed import emit_full_seed  # noqa: PLC0415
    populate_sql = build_config_populate_sql(cfg, instance, anchor=anchor)
    scenario = build_default_scenario(instance, anchor=anchor, density=density)
    seed_sql = emit_full_seed(
        instance, scenario, prefix=cfg.db_table_prefix,
        dialect=cfg.dialect, anchor=anchor, baseline_window_days=window_days,
    )
    return schema_sql, populate_sql, seed_sql


def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
    return conn


def _connect_duckdb(db_path: Path | None) -> duckdb.DuckDBPyConnection:
    if db_path is None:
        return duckdb.connect(":memory:")
    return duckdb.connect(str(db_path))


def _execute_sqlite_script(conn: sqlite3.Connection, sql: str) -> None:
    cur = conn.cursor()
    for stmt in _split_sqlite_statements(sql):
        cur.execute(stmt)
    conn.commit()


def _execute_duckdb_script(
    conn: duckdb.DuckDBPyConnection, sql: str,
) -> None:
    """Execute a multi-statement script on DuckDB.

    DuckDB's Python binding accepts multi-statement strings, but for
    parity with the SQLite harness (and so we can surface which
    statement fails) we split + execute one at a time.
    """
    for i, stmt in enumerate(_split_sqlite_statements(sql)):
        try:
            conn.execute(stmt)
        except Exception as e:
            preview = stmt.strip()[:1500]
            raise RuntimeError(
                f"DuckDB stmt #{i} failed ({type(e).__name__}: {e})\n"
                f"  Preview: {preview}"
            ) from e


def _scale_for_target_rows(target_rows: int) -> tuple[float, int]:
    """Same scale lever as BX: density=1.0, scale window_days."""
    # spec_example has a much smaller per-day row footprint than
    # sasquatch_pr. Calibrate from a probe: at density=1, 90 days
    # produces roughly the L2's natural footprint; the harness will
    # scale window_days linearly to hit target_rows, falling back
    # to a probed rate after the first build.
    # Conservative initial estimate: 60 rows/day (much smaller than
    # sasquatch_pr's ~1400). At target 1M this yields ~16,000 days
    # which is too long; we'll re-calibrate after one probe.
    ROWS_PER_DAY = 1500
    days = max(90, int(target_rows / ROWS_PER_DAY))
    return 1.0, days


def _row_count(conn: object, table: str, *, dialect: str) -> int | None:
    try:
        if dialect == "duckdb":
            r = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        else:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            r = cur.fetchone()
        return int(r[0]) if r else 0
    except Exception:  # noqa: BLE001
        return None


def _run_one_duckdb(
    *, target_rows: int, instance: object, cfg: Config,
    anchor: date, density: float, window_days: int,
    use_pg_csb: bool = False,
    verbose: bool = True,
) -> dict[str, object]:
    """Build the matview chain on DuckDB; return timings + row counts."""
    schema_sql, populate_sql, seed_sql = _build_sqlite_setup(
        cfg, instance, anchor=anchor, density=density, window_days=window_days,
    )
    refresh_sql = refresh_matviews_sql(
        instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE,
    )

    translator = (
        translate_sqlite_to_duckdb_pg_csb if use_pg_csb
        else translate_sqlite_to_duckdb
    )

    # Translate every block.
    schema_d = translator(schema_sql)
    populate_d = translator(populate_sql)
    seed_d = translator(seed_sql)
    refresh_d = translator(refresh_sql)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"ca0-spike-{target_rows}-"))
    db_path = tmp_dir / "demo.duckdb"
    if verbose:
        print(f"[CA0] DuckDB tmp file: {db_path}", flush=True)
    conn = _connect_duckdb(db_path)

    t0 = time.perf_counter()
    _execute_duckdb_script(conn, schema_d)
    _execute_duckdb_script(conn, populate_d)
    schema_ms = (time.perf_counter() - t0) * 1000.0
    if verbose:
        print(f"[CA0] schema + populate: {schema_ms:.0f} ms", flush=True)

    t0 = time.perf_counter()
    _execute_duckdb_script(conn, seed_d)
    seed_apply_ms = (time.perf_counter() - t0) * 1000.0

    base_tx = _row_count(conn, f"{cfg.db_table_prefix}_transactions", dialect="duckdb") or 0
    base_db = _row_count(conn, f"{cfg.db_table_prefix}_daily_balances", dialect="duckdb") or 0
    if verbose:
        print(
            f"[CA0] seed apply: {seed_apply_ms:.0f} ms; "
            f"base tx={base_tx:,} db={base_db:,}", flush=True,
        )

    gc.collect()
    t0 = time.perf_counter()
    _execute_duckdb_script(conn, refresh_d)
    bundled_ms = (time.perf_counter() - t0) * 1000.0
    if verbose:
        print(f"[CA0] bundled refresh: {bundled_ms:.0f} ms", flush=True)

    # Per-matview row counts post-refresh.
    counts: dict[str, int | None] = {}
    for name in MATVIEW_NAMES_ORDERED:
        full = f"{cfg.db_table_prefix}_{name}"
        counts[name] = _row_count(conn, full, dialect="duckdb")

    db_size = db_path.stat().st_size if db_path.exists() else 0

    return {
        "base_tx_rows": base_tx,
        "base_db_rows": base_db,
        "schema_ms": schema_ms,
        "seed_apply_ms": seed_apply_ms,
        "bundled_refresh_ms": bundled_ms,
        "matview_row_counts": counts,
        "duckdb_file_size_bytes": db_size,
        "conn": conn,
        "tmp_dir": tmp_dir,
    }


def _run_one_sqlite(
    *, target_rows: int, instance: object, cfg: Config,
    anchor: date, density: float, window_days: int,
    verbose: bool = True,
) -> dict[str, object]:
    """Build matview chain on SQLite for the 3-way diff."""
    schema_sql, populate_sql, seed_sql = _build_sqlite_setup(
        cfg, instance, anchor=anchor, density=density, window_days=window_days,
    )
    refresh_sql = refresh_matviews_sql(
        instance, prefix=cfg.db_table_prefix, dialect=Dialect.SQLITE,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"ca0-sqlite-ref-{target_rows}-"))
    db_path = tmp_dir / "demo.sqlite"
    if verbose:
        print(f"[CA0] SQLite ref tmp: {db_path}", flush=True)
    conn = _connect_sqlite(db_path)

    _execute_sqlite_script(conn, schema_sql)
    _execute_sqlite_script(conn, populate_sql)
    t0 = time.perf_counter()
    _execute_sqlite_script(conn, seed_sql)
    seed_ms = (time.perf_counter() - t0) * 1000.0
    base_tx = _row_count(conn, f"{cfg.db_table_prefix}_transactions", dialect="sqlite") or 0
    if verbose:
        print(
            f"[CA0] SQLite seed apply: {seed_ms:.0f} ms; "
            f"base tx={base_tx:,}", flush=True,
        )

    t0 = time.perf_counter()
    _execute_sqlite_script(conn, refresh_sql)
    bundled_ms = (time.perf_counter() - t0) * 1000.0
    if verbose:
        print(f"[CA0] SQLite bundled refresh: {bundled_ms:.0f} ms", flush=True)

    counts: dict[str, int | None] = {}
    for name in MATVIEW_NAMES_ORDERED:
        full = f"{cfg.db_table_prefix}_{name}"
        counts[name] = _row_count(conn, full, dialect="sqlite")
    return {
        "base_tx_rows": base_tx,
        "seed_apply_ms": seed_ms,
        "bundled_refresh_ms": bundled_ms,
        "matview_row_counts": counts,
        "conn": conn,
        "tmp_dir": tmp_dir,
    }


def _diff_computed_subledger(
    duck_conn: duckdb.DuckDBPyConnection,
    sqlite_conn: sqlite3.Connection,
    prefix: str,
    *, tolerance: float = 0.005,
) -> dict[str, object]:
    """Row-by-row diff of computed_subledger_balance between DuckDB +
    SQLite. Returns {'rows_compared', 'mismatches', 'sample_diff'}.

    Key columns: (account_id, business_day_start, account_parent_role).
    Compare ROUND(computed_balance, 2) within ``tolerance``.
    """
    duck_rows = duck_conn.execute(
        f"SELECT account_id, business_day_start, account_parent_role, "
        f"computed_balance FROM {prefix}_computed_subledger_balance "
        f"ORDER BY account_id, business_day_start, account_parent_role"
    ).fetchall()

    cur = sqlite_conn.cursor()
    cur.execute(
        f"SELECT account_id, business_day_start, account_parent_role, "
        f"computed_balance FROM {prefix}_computed_subledger_balance "
        f"ORDER BY account_id, business_day_start, account_parent_role"
    )
    sqlite_rows = cur.fetchall()

    duck_map = {(r[0], str(r[1]), r[2]): float(r[3] or 0) for r in duck_rows}
    sqlite_map = {(r[0], str(r[1]), r[2]): float(r[3] or 0) for r in sqlite_rows}

    only_duck = duck_map.keys() - sqlite_map.keys()
    only_sqlite = sqlite_map.keys() - duck_map.keys()
    common = duck_map.keys() & sqlite_map.keys()

    mismatches: list[tuple[object, float, float]] = []
    for k in common:
        if abs(duck_map[k] - sqlite_map[k]) > tolerance:
            mismatches.append((k, duck_map[k], sqlite_map[k]))

    return {
        "duck_total": len(duck_rows),
        "sqlite_total": len(sqlite_rows),
        "only_duck": len(only_duck),
        "only_sqlite": len(only_sqlite),
        "value_mismatches": len(mismatches),
        "sample_only_duck": list(only_duck)[:5],
        "sample_only_sqlite": list(only_sqlite)[:5],
        "sample_value_mismatch": mismatches[:5],
    }


def _emit_perf_report(
    *, target_rows: int, l2_yaml: Path, density: float, window_days: int,
    duck: dict[str, object], use_pg_csb: bool,
) -> str:
    lines: list[str] = []
    mode_tag = " (bonus: PG-style correlated CSB body)" if use_pg_csb else ""
    lines.append(
        f"# CA.0 DuckDB matview perf — base_tx_rows="
        f"{duck['base_tx_rows']:,}{mode_tag}\n"
    )
    lines.append(f"- L2 yaml: `{l2_yaml.resolve().relative_to(_REPO.resolve())}`")
    lines.append(f"- density factor: {density:.2f}x default")
    lines.append(f"- baseline window days: {window_days}")
    lines.append(f"- base `<prefix>_transactions` rows: {duck['base_tx_rows']:,}")
    lines.append(f"- base `<prefix>_daily_balances` rows: {duck['base_db_rows']:,}")
    lines.append(f"- seed apply wallclock: {duck['seed_apply_ms']:.0f} ms")
    lines.append(
        f"- **bundled refresh wallclock (cold; integrator-visible): "
        f"{duck['bundled_refresh_ms']:.0f} ms**"
    )
    lines.append(
        f"- DuckDB file size: {duck['duckdb_file_size_bytes'] / 1e6:.1f} MB"
    )
    lines.append("")
    lines.append("| matview | output rows |")
    lines.append("| --- | ---: |")
    for n in MATVIEW_NAMES_ORDERED:
        c = duck["matview_row_counts"].get(n)  # type: ignore[union-attr]
        lines.append(f"| `{n}` | {c if c is not None else '—'} |")
    return "\n".join(lines) + "\n"


def _emit_diff_report(
    *, target_rows: int, l2_yaml: Path,
    duck: dict[str, object], sqlite: dict[str, object],
    csb_diff: dict[str, object],
) -> str:
    lines: list[str] = []
    lines.append(
        f"# CA.0 DuckDB ↔ SQLite 3-way diff — target {target_rows:,} base tx\n"
    )
    lines.append(f"- L2 yaml: `{l2_yaml.resolve().relative_to(_REPO.resolve())}`")
    lines.append(
        f"- DuckDB base tx rows: {duck['base_tx_rows']:,}; "
        f"SQLite base tx rows: {sqlite['base_tx_rows']:,}"
    )
    lines.append("")
    lines.append("## Matview row-count parity")
    lines.append("")
    lines.append("| matview | DuckDB rows | SQLite rows | Δ |")
    lines.append("| --- | ---: | ---: | ---: |")
    for n in MATVIEW_NAMES_ORDERED:
        d = duck["matview_row_counts"].get(n)  # type: ignore[union-attr]
        s = sqlite["matview_row_counts"].get(n)  # type: ignore[union-attr]
        if d is None or s is None:
            delta = "—"
        else:
            delta = str(d - s)
        lines.append(
            f"| `{n}` | {d if d is not None else '—'} "
            f"| {s if s is not None else '—'} | {delta} |"
        )
    lines.append("")
    lines.append("## computed_subledger_balance row-by-row diff")
    lines.append("")
    lines.append(
        f"- DuckDB rows: {csb_diff['duck_total']:,}, "
        f"SQLite rows: {csb_diff['sqlite_total']:,}"
    )
    lines.append(f"- only-in-DuckDB keys: {csb_diff['only_duck']}")
    lines.append(f"- only-in-SQLite keys: {csb_diff['only_sqlite']}")
    lines.append(
        f"- value mismatches (|delta| > $0.005): {csb_diff['value_mismatches']}"
    )
    if csb_diff["sample_only_duck"]:
        lines.append("")
        lines.append("Sample only-in-DuckDB keys:")
        for k in csb_diff["sample_only_duck"]:  # type: ignore[union-attr]
            lines.append(f"- {k}")
    if csb_diff["sample_only_sqlite"]:
        lines.append("")
        lines.append("Sample only-in-SQLite keys:")
        for k in csb_diff["sample_only_sqlite"]:  # type: ignore[union-attr]
            lines.append(f"- {k}")
    if csb_diff["sample_value_mismatch"]:
        lines.append("")
        lines.append("Sample value mismatches (key, DuckDB, SQLite):")
        for k, dv, sv in csb_diff["sample_value_mismatch"]:  # type: ignore[union-attr]
            lines.append(f"- {k}: DuckDB={dv:.4f}, SQLite={sv:.4f}, delta={dv - sv:.4f}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("perf", "diff", "bonus"), required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument(
        "--l2", type=Path, default=_REPO / "tests" / "l2" / "spec_example.yaml",
    )
    parser.add_argument("--prefix", type=str, default="ca0spike")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    anchor = date(2030, 1, 1)
    instance = load_instance(args.l2)
    density, window_days = _scale_for_target_rows(args.rows)

    cfg = _make_cfg(
        dialect=Dialect.SQLITE,
        db_url=f"sqlite:///{args.prefix}.sqlite",  # ignored, harness uses tmp
        prefix=args.prefix,
    )

    if args.mode == "perf":
        duck = _run_one_duckdb(
            target_rows=args.rows, instance=instance, cfg=cfg,
            anchor=anchor, density=density, window_days=window_days,
        )
        report = _emit_perf_report(
            target_rows=args.rows, l2_yaml=args.l2,
            density=density, window_days=window_days,
            duck=duck, use_pg_csb=False,
        )
    elif args.mode == "bonus":
        duck = _run_one_duckdb(
            target_rows=args.rows, instance=instance, cfg=cfg,
            anchor=anchor, density=density, window_days=window_days,
            use_pg_csb=True,
        )
        report = _emit_perf_report(
            target_rows=args.rows, l2_yaml=args.l2,
            density=density, window_days=window_days,
            duck=duck, use_pg_csb=True,
        )
    else:  # diff
        duck = _run_one_duckdb(
            target_rows=args.rows, instance=instance, cfg=cfg,
            anchor=anchor, density=density, window_days=window_days,
        )
        sqlite_run = _run_one_sqlite(
            target_rows=args.rows, instance=instance, cfg=cfg,
            anchor=anchor, density=density, window_days=window_days,
        )
        csb_diff = _diff_computed_subledger(
            duck["conn"],  # type: ignore[arg-type]
            sqlite_run["conn"],  # type: ignore[arg-type]
            args.prefix,
        )
        report = _emit_diff_report(
            target_rows=args.rows, l2_yaml=args.l2,
            duck=duck, sqlite=sqlite_run, csb_diff=csb_diff,
        )

    suffix = "" if args.mode == "perf" else f"_{args.mode}"
    default_out = (
        _REPO / "spike" / "ca_0_duckdb_spike"
        / f"results{suffix}_target_{args.rows}.md"
    )
    out_path = args.out or default_out
    out_path.write_text(report)
    print(f"\n[CA0] wrote {out_path}", flush=True)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
