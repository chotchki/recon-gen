"""DS.0 PostgreSQL 17 probe — same domain-A enumeration as the Oracle probe
(oracle_probe.py's build_domain/build_rows/seed_sql_text/py_residuals reused
verbatim via import), run against a throwaway postgres:17-alpine container
through the REAL repo paths: emit_schema -> execute_script(POSTGRES),
plain-INSERT seed via execute_script, refresh_matviews_sql split
per-statement (the documented caller contract for PG refresh), detector
SELECTs, python-residual agreement, and EXPLAIN evidence on the two hot
matview defining queries (SubPlan vs decorrelated join).

Usage: pg_probe.py --scale {quarter,full} --port <mapped-port>
Each scale gets its own database (probe_quarter / probe_full).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oracle_probe"))
import oracle_probe as P  # noqa: E402 — reuse domain construction verbatim

from recon_gen.common.db import execute_script  # noqa: E402
from recon_gen.common.l2.config_table import emit_config_populate_sql  # noqa: E402
from recon_gen.common.l2.loader import load_instance  # noqa: E402
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql  # noqa: E402
from recon_gen.common.sql import Dialect  # noqa: E402

PREFIX = P.PREFIX
DIALECT = Dialect.POSTGRES

HOT_MATVIEWS = (
    f"{PREFIX}_current_transactions",
    f"{PREFIX}_current_daily_balances",
    f"{PREFIX}_computed_subledger_balance",
)


def split_pg_statements(sql: str) -> list[str]:
    """Refresh script is one `REFRESH MATERIALIZED VIEW ...;` per line —
    no comments, no strings-with-semicolons. Line-splitting is safe."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def fresh_database(port: int, dbname: str) -> str:
    admin = psycopg.connect(
        f"postgresql://postgres:probe@127.0.0.1:{port}/postgres", autocommit=True)
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {dbname} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {dbname}")
    admin.close()
    return f"postgresql://postgres:probe@127.0.0.1:{port}/{dbname}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=("quarter", "full"), required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--explain-analyze", action="store_true",
                    help="also EXPLAIN ANALYZE the computed_subledger body")
    args = ap.parse_args()

    cells_full, day_opts = P.build_domain(P.BAL_OPTS, P.LEG_ATOMS)
    cells = cells_full[::4] if args.scale == "quarter" else cells_full
    print(f"[domain A {args.scale}] day_opts={len(day_opts)} "
          f"cells={len(cells)} (full={len(cells_full)})")
    tx_rows, db_rows = P.build_rows(cells, "en")
    n_rows = len(tx_rows) + len(db_rows)
    print(f"rows: tx={len(tx_rows)} bal={len(db_rows)} total={n_rows}")

    instance = load_instance(P.SPEC)
    url = fresh_database(args.port, f"probe_{args.scale}")
    conn = psycopg.connect(url, autocommit=True)
    cur = conn.cursor()

    timings: dict[str, float] = {}

    # ---- stage 1: schema apply (real emitter + real execute_script) ----
    schema_sql = emit_schema(instance, prefix=PREFIX, dialect=DIALECT)
    t0 = time.perf_counter()
    execute_script(cur, schema_sql, dialect=DIALECT)
    timings["schema_apply"] = time.perf_counter() - t0
    print(f"schema apply: {timings['schema_apply']:.2f}s")

    # ---- stage 2: config kv populate (deploy-path emitter) ----
    cfg_sql = emit_config_populate_sql(
        prefix=PREFIX, cfg_json="{}",
        l2_json='{"rails": [], "limit_schedules": []}',
        as_of=dt.datetime(2030, 1, 3), dialect=DIALECT)
    t0 = time.perf_counter()
    execute_script(cur, cfg_sql, dialect=DIALECT)
    timings["config_populate"] = time.perf_counter() - t0
    print(f"config populate: {timings['config_populate']:.2f}s")

    # ---- stage 3: plain-INSERT seed via the real PG path (one execute) ----
    seed_sql = P.seed_sql_text(tx_rows, db_rows)
    t0 = time.perf_counter()
    execute_script(cur, seed_sql, dialect=DIALECT)
    timings["insert_real_path"] = time.perf_counter() - t0
    print(f"bulk insert via execute_script (PG one-shot): "
          f"{timings['insert_real_path']:.2f}s "
          f"({n_rows / timings['insert_real_path']:.0f} rows/s)")
    cur.execute(f"SELECT COUNT(*) FROM {PREFIX}_transactions")
    ntx = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {PREFIX}_daily_balances")
    nbal = cur.fetchone()[0]
    assert (ntx, nbal) == (len(tx_rows), len(db_rows)), (ntx, nbal)

    # ---- stage 4: matview refresh, per-statement (documented PG caller
    # contract: split on ';' + execute each; CONCURRENTLY needs autocommit) --
    refresh_sql = refresh_matviews_sql(instance, prefix=PREFIX, dialect=DIALECT)
    stmts = split_pg_statements(refresh_sql)
    per: list[tuple[float, str]] = []
    t0 = time.perf_counter()
    for s in stmts:
        s0 = time.perf_counter()
        cur.execute(s)
        per.append((time.perf_counter() - s0, " ".join(s.split())[:110]))
    timings["refresh_matviews"] = time.perf_counter() - t0
    print(f"refresh matviews (CONCURRENTLY, real path): "
          f"{timings['refresh_matviews']:.2f}s ({len(stmts)} stmts)")
    print("top-10 refresh statements:")
    for cost, head in sorted(per, reverse=True)[:10]:
        print(f"  {cost:7.2f}s  {head}")

    # ---- stage 5: one violation-set SELECT per money detector ----
    eng = {}
    for det in ("drift", "overdraft", "expected_eod_balance_breach"):
        t0 = time.perf_counter()
        cur.execute(f"SELECT account_id, business_day_start FROM {PREFIX}_{det}")
        rows = cur.fetchall()
        timings[f"select_{det}"] = time.perf_counter() - t0
        eng[det] = {(a, ts.date() if isinstance(ts, dt.datetime) else ts)
                    for a, ts in rows}
        print(f"SELECT {det}: {timings[f'select_{det}']:.3f}s "
              f"({len(rows)} violations)")

    # ---- sanity: engine == python residual ----
    py = P.py_residuals(cells, "en")
    names = ("drift", "overdraft", "expected_eod_balance_breach")
    ok = True
    for name, p in zip(names, py):
        e = eng[name]
        if p != e:
            ok = False
            print(f"  DISAGREE {name}: py-only={sorted(p - e)[:3]} "
                  f"eng-only={sorted(e - p)[:3]} ({len(p - e)}/{len(e - p)})")
    print(f"violation counts py: drift={len(py[0])} over={len(py[1])} "
          f"eod={len(py[2])}  agreement: {'CLEAN' if ok else 'DIVERGES'}")

    # ---- stage 6: plain (non-CONCURRENT) refresh of the hot matviews —
    # isolates defining-query cost from CONCURRENTLY's temp-copy + diff ----
    print("plain REFRESH (no CONCURRENTLY) on hot matviews:")
    for mv in HOT_MATVIEWS:
        t0 = time.perf_counter()
        cur.execute(f"REFRESH MATERIALIZED VIEW {mv}")
        cost = time.perf_counter() - t0
        timings[f"plain_refresh_{mv}"] = cost
        print(f"  {cost:7.2f}s  {mv}")

    # ---- stage 7: EXPLAIN evidence — SubPlan vs decorrelated join ----
    print("\n== EXPLAIN (FORMAT TEXT) on hot matview defining queries ==")
    for mv in (f"{PREFIX}_computed_subledger_balance",
               f"{PREFIX}_current_transactions"):
        cur.execute(
            "SELECT definition FROM pg_matviews WHERE matviewname = %s", (mv,))
        body = cur.fetchone()[0].rstrip().rstrip(";")
        cur.execute(f"EXPLAIN (FORMAT TEXT) {body}")
        print(f"\n-- {mv} --")
        for (line,) in cur.fetchall():
            print(f"  {line}")

    print("\n-- after ANALYZE (fresh stats) --")
    cur.execute("ANALYZE")
    for mv in (f"{PREFIX}_computed_subledger_balance",
               f"{PREFIX}_current_transactions"):
        cur.execute(
            "SELECT definition FROM pg_matviews WHERE matviewname = %s", (mv,))
        body = cur.fetchone()[0].rstrip().rstrip(";")
        cur.execute(f"EXPLAIN (FORMAT TEXT) {body}")
        print(f"\n-- {mv} (post-ANALYZE) --")
        for (line,) in cur.fetchall():
            print(f"  {line}")

    if args.explain_analyze:
        mv = f"{PREFIX}_computed_subledger_balance"
        cur.execute(
            "SELECT definition FROM pg_matviews WHERE matviewname = %s", (mv,))
        body = cur.fetchone()[0].rstrip().rstrip(";")
        cur.execute(f"EXPLAIN (ANALYZE, FORMAT TEXT) {body}")
        print(f"\n-- {mv} (EXPLAIN ANALYZE) --")
        for (line,) in cur.fetchall():
            print(f"  {line}")

    conn.close()

    print("\n== TIMING TABLE (seconds) ==")
    for k, v in timings.items():
        print(f"{k:>50}: {v:8.2f}")
    total = (timings["schema_apply"] + timings["config_populate"]
             + timings["insert_real_path"] + timings["refresh_matviews"]
             + sum(v for k, v in timings.items() if k.startswith("select_")))
    print(f"{'end-to-end (real path)':>50}: {total:8.2f}")


if __name__ == "__main__":
    main()
