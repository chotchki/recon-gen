"""Steady-state check: re-run the per-statement CONCURRENTLY refresh a
SECOND time on an already-populated database (the integrator's real
cadence — matviews are never empty after the first ETL load), with
auto_explain capturing any nested statement over 1s so the first-refresh
computed_subledger blowup gets a plan attached.
"""
from __future__ import annotations

import argparse
import time

import psycopg

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import refresh_matviews_sql
from recon_gen.common.sql import Dialect

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oracle_probe"))
import oracle_probe as P  # noqa: E402

PREFIX = P.PREFIX


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    instance = load_instance(P.SPEC)
    conn = psycopg.connect(
        f"postgresql://postgres:probe@127.0.0.1:{args.port}/{args.db}",
        autocommit=True)
    cur = conn.cursor()
    cur.execute("LOAD 'auto_explain'")
    cur.execute("SET auto_explain.log_min_duration = '1s'")
    cur.execute("SET auto_explain.log_nested_statements = on")

    refresh_sql = refresh_matviews_sql(instance, prefix=PREFIX,
                                       dialect=Dialect.POSTGRES)
    stmts = [s.strip() for s in refresh_sql.split(";") if s.strip()]
    per: list[tuple[float, str]] = []
    t0 = time.perf_counter()
    for s in stmts:
        s0 = time.perf_counter()
        cur.execute(s)
        per.append((time.perf_counter() - s0, " ".join(s.split())[:110]))
    total = time.perf_counter() - t0
    print(f"[{args.db}] second refresh (CONCURRENTLY, warm): "
          f"{total:.2f}s ({len(stmts)} stmts)")
    print("top-10:")
    for cost, head in sorted(per, reverse=True)[:10]:
        print(f"  {cost:7.2f}s  {head}")
    conn.close()


if __name__ == "__main__":
    main()
