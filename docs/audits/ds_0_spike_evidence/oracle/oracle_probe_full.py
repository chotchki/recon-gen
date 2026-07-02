"""Full-domain (38,416-cell) Oracle run — real path only, per-statement
refresh timing so the extrapolation is a measurement instead."""
from __future__ import annotations

import datetime as dt
import time

import oracledb

from recon_gen.common.db import execute_script, oracle_dsn, split_oracle_script
from recon_gen.common.l2.config_table import emit_config_populate_sql
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

import oracle_probe as P  # noqa: E402 — same dir

PREFIX = P.PREFIX
DIALECT = Dialect.ORACLE


def main():
    cells, day_opts = P.build_domain(P.BAL_OPTS, P.LEG_ATOMS)
    print(f"[domain A FULL] day_opts={len(day_opts)} cells={len(cells)}")
    tx_rows, db_rows = P.build_rows(cells, "en")
    n_rows = len(tx_rows) + len(db_rows)
    print(f"rows: tx={len(tx_rows)} bal={len(db_rows)} total={n_rows}")

    instance = load_instance(P.SPEC)
    conn = oracledb.connect(oracle_dsn(P.URL))
    cur = conn.cursor()
    P.drop_all(cur)
    conn.commit()

    t0 = time.perf_counter()
    execute_script(cur, emit_schema(instance, prefix=PREFIX, dialect=DIALECT),
                   dialect=DIALECT)
    conn.commit()
    print(f"schema apply: {time.perf_counter() - t0:.2f}s")

    execute_script(cur, emit_config_populate_sql(
        prefix=PREFIX, cfg_json="{}",
        l2_json='{"rails": [], "limit_schedules": []}',
        as_of=dt.datetime(2030, 1, 3), dialect=DIALECT), dialect=DIALECT)
    conn.commit()

    seed_sql = P.seed_sql_text(tx_rows, db_rows)
    t0 = time.perf_counter()
    execute_script(cur, seed_sql, dialect=DIALECT)
    conn.commit()
    t_ins = time.perf_counter() - t0
    print(f"bulk insert (DPL): {t_ins:.2f}s ({n_rows / t_ins:.0f} rows/s)")

    refresh_sql = refresh_matviews_sql(instance, prefix=PREFIX, dialect=DIALECT)
    stmts = split_oracle_script(refresh_sql)
    per: list[tuple[float, str]] = []
    t0 = time.perf_counter()
    for s in stmts:
        s0 = time.perf_counter()
        cur.execute(s)
        per.append((time.perf_counter() - s0, " ".join(s.split())[:110]))
    conn.commit()
    t_ref = time.perf_counter() - t0
    print(f"refresh matviews: {t_ref:.2f}s ({len(stmts)} stmts)")
    print("top-10 refresh statements:")
    for cost, head in sorted(per, reverse=True)[:10]:
        print(f"  {cost:7.2f}s  {head}")

    eng = {}
    for det in ("drift", "overdraft", "expected_eod_balance_breach"):
        s0 = time.perf_counter()
        cur.execute(f"SELECT account_id, business_day_start FROM {PREFIX}_{det}")
        rows = cur.fetchall()
        print(f"SELECT {det}: {time.perf_counter() - s0:.3f}s ({len(rows)} violations)")
        eng[det] = {(a, ts.date() if isinstance(ts, dt.datetime) else ts)
                    for a, ts in rows}

    py = P.py_residuals(cells, "en")
    ok = all(p == eng[n] for n, p in
             zip(("drift", "overdraft", "expected_eod_balance_breach"), py))
    print(f"violation counts py: drift={len(py[0])} over={len(py[1])} "
          f"eod={len(py[2])}  agreement: {'CLEAN' if ok else 'DIVERGES'}")

    P.drop_all(cur)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
