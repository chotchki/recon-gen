"""Reproduce the full-scale FIRST-refresh computed_subledger blowup with
auto_explain armed, so the slow nested statement's plan lands in the
container log. Fresh DB, full domain, only the first three refresh
statements (current_transactions, current_daily_balances,
computed_subledger_balance) — the minimal repro chain.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oracle_probe"))
import oracle_probe as P  # noqa: E402

from recon_gen.common.db import execute_script  # noqa: E402
from recon_gen.common.l2.config_table import emit_config_populate_sql  # noqa: E402
from recon_gen.common.l2.loader import load_instance  # noqa: E402
from recon_gen.common.l2.schema import emit_schema  # noqa: E402
from recon_gen.common.sql import Dialect  # noqa: E402

PREFIX = P.PREFIX
DIALECT = Dialect.POSTGRES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()

    cells, _ = P.build_domain(P.BAL_OPTS, P.LEG_ATOMS)
    tx_rows, db_rows = P.build_rows(cells, "en")

    admin = psycopg.connect(
        f"postgresql://postgres:probe@127.0.0.1:{args.port}/postgres",
        autocommit=True)
    with admin.cursor() as c:
        c.execute("DROP DATABASE IF EXISTS probe_diag WITH (FORCE)")
        c.execute("CREATE DATABASE probe_diag")
    admin.close()

    instance = load_instance(P.SPEC)
    conn = psycopg.connect(
        f"postgresql://postgres:probe@127.0.0.1:{args.port}/probe_diag",
        autocommit=True)
    cur = conn.cursor()
    execute_script(cur, emit_schema(instance, prefix=PREFIX, dialect=DIALECT),
                   dialect=DIALECT)
    execute_script(cur, emit_config_populate_sql(
        prefix=PREFIX, cfg_json="{}",
        l2_json='{"rails": [], "limit_schedules": []}',
        as_of=dt.datetime(2030, 1, 3), dialect=DIALECT), dialect=DIALECT)
    t0 = time.perf_counter()
    execute_script(cur, P.seed_sql_text(tx_rows, db_rows), dialect=DIALECT)
    print(f"seed: {time.perf_counter() - t0:.1f}s")

    cur.execute("LOAD 'auto_explain'")
    cur.execute("SET auto_explain.log_min_duration = '5s'")
    cur.execute("SET auto_explain.log_nested_statements = on")
    for mv in (f"{PREFIX}_current_transactions",
               f"{PREFIX}_current_daily_balances",
               f"{PREFIX}_computed_subledger_balance"):
        t0 = time.perf_counter()
        cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
        print(f"first refresh {mv}: {time.perf_counter() - t0:.2f}s")
    conn.close()


if __name__ == "__main__":
    main()
