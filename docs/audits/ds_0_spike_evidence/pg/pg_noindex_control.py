"""Control: is PG immune to the quadratic shape, or just index-saved?

Runs the computed_subledger_balance defining query against an INDEX-LESS
copy of current_transactions in the probe_quarter database (left in place
by pg_probe.py --scale quarter). If PG's SubPlan falls back to a per-row
seq scan of the copy, the quadratic cost exists on PG too whenever the
inner index is missing — i.e. the pathology is planner-shape-general,
and the emitted indexes are what save the real refresh path.
"""
from __future__ import annotations

import argparse
import time

import psycopg

PREFIX = "ds0probe"

BODY = f"""
SELECT
    sb.account_id,
    sb.business_day_start,
    sb.business_day_end,
    sb.account_parent_role,
    COALESCE((
        SELECT SUM(tx.amount_money)
        FROM {PREFIX}_curr_tx_noidx tx
        WHERE tx.account_id = sb.account_id
          AND tx.status = 'Posted'
          AND tx.posting <= sb.business_day_end
    ), 0) AS computed_balance
FROM {PREFIX}_current_daily_balances sb
WHERE sb.account_scope = 'internal'
  AND sb.account_parent_role IS NOT NULL
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--db", default="probe_quarter")
    args = ap.parse_args()

    conn = psycopg.connect(
        f"postgresql://postgres:probe@127.0.0.1:{args.port}/{args.db}",
        autocommit=True)
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {PREFIX}_curr_tx_noidx")
    cur.execute(f"CREATE TABLE {PREFIX}_curr_tx_noidx AS "
                f"SELECT * FROM {PREFIX}_current_transactions")
    cur.execute("ANALYZE")

    cur.execute(f"EXPLAIN (FORMAT TEXT) {BODY}")
    print("-- plan against index-less inner --")
    for (line,) in cur.fetchall():
        print(f"  {line}")

    t0 = time.perf_counter()
    cur.execute(BODY)
    n = len(cur.fetchall())
    print(f"\nexecution against index-less inner: "
          f"{time.perf_counter() - t0:.2f}s ({n} rows)")

    cur.execute(f"DROP TABLE {PREFIX}_curr_tx_noidx")
    conn.close()


if __name__ == "__main__":
    main()
