"""DS.0 Oracle bulk-load probe — quarter-slice of throughput.py's domain A
run against a live Oracle 19c container through the REAL repo paths:
emit_schema -> execute_script, seed text -> execute_script (DPL fast path
+ forced INSERT-ALL batching path), refresh_matviews_sql, one SELECT per
money detector, all timed. Sanity: engine violation sets vs the same
pure-Python residual throughput.py used.
"""
from __future__ import annotations

import datetime as dt
import itertools
import time

import oracledb

from recon_gen.common.db import (
    batch_oracle_inserts,
    execute_script,
    oracle_dsn,
    split_oracle_script,
)
from recon_gen.common.l2.config_table import emit_config_populate_sql
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

from pathlib import Path

PREFIX = "ds0probe"
SPEC = Path("/Users/chotchki/workspace/quicksight/tests/l2/spec_example.yaml")
DIALECT = Dialect.ORACLE
URL = "oracle+oracledb://system:probe123@localhost:15990/?service_name=FREEPDB1"
D = [dt.date(2030, 1, 1), dt.date(2030, 1, 2)]

TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)
DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money", "metadata",
)

BAL_OPTS: list[tuple[int, int | None] | None] = [None] + [
    (m, e) for m in (-1, 0, 1) for e in (None, 0)
]
LEG_ATOMS = [(a, s, dt.time(12, 0)) for a in (-1, 0, 1) for s in ("Posted", "Pending")]


def multisets_le2(atoms):
    out = [()]
    out += [(x,) for x in atoms]
    out += [(atoms[i], atoms[j]) for i in range(len(atoms)) for j in range(i, len(atoms))]
    return out


def build_domain(bal_opts, leg_atoms):
    day_opts = [(b, l) for b in bal_opts for l in multisets_le2(leg_atoms)]
    return list(itertools.product(day_opts, day_opts)), day_opts


def build_rows(cells, tag):
    tx_rows, db_rows = [], []
    for i, cell in enumerate(cells):
        acct = f"{tag}{i:06d}"
        for di, (bal, legs) in enumerate(cell):
            d = D[di]
            bds = dt.datetime(d.year, d.month, d.day)
            bde = dt.datetime(d.year, d.month, d.day, 23, 59, 59)
            if bal is not None:
                m, e = bal
                db_rows.append((acct, acct, "CustomerSubledger", "internal",
                                "CustomerLedger", e, bds, bde, m, None))
            for li, (a, s, t) in enumerate(legs):
                tid = f"{tag}{i:06d}x{di}{li}"
                posting = dt.datetime(d.year, d.month, d.day, t.hour, t.minute, t.second)
                tx_rows.append((tid, acct, acct, "CustomerSubledger", "internal",
                                "CustomerLedger", a, "Credit" if a >= 0 else "Debit",
                                s, posting, tid, None, "RailX", None, "enum", None, None))
    return tx_rows, db_rows


def py_residuals(cells, tag):
    drift, over, eod = set(), set(), set()
    for i, cell in enumerate(cells):
        acct = f"{tag}{i:06d}"
        emits: dict[int, tuple[int, int | None]] = {}
        legs_by_day: dict[int, list] = {0: [], 1: []}
        for di, (bal, legs) in enumerate(cell):
            if bal is not None:
                emits[di] = bal
            legs_by_day[di] = list(legs)
        for di, (m, e) in emits.items():
            computed = sum(a for dj in range(di + 1)
                           for (a, s, _t) in legs_by_day[dj] if s == "Posted")
            if m != computed:
                drift.add((acct, D[di]))
            if e is not None and m != e:
                eod.add((acct, D[di]))
        if emits:
            for di in range(min(emits), len(D)):
                src = max(dj for dj in emits if dj <= di)
                if emits[src][0] < 0:
                    over.add((acct, D[di]))
    return drift, over, eod


def lit(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, dt.datetime):
        return f"TIMESTAMP '{v.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(v, int):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def seed_sql_text(tx_rows, db_rows) -> str:
    stmts = []
    tx_cols = ", ".join(TX_COLS)
    db_cols = ", ".join(DB_COLS)
    for r in tx_rows:
        stmts.append(
            f"INSERT INTO {PREFIX}_transactions ({tx_cols}) VALUES "
            f"({', '.join(lit(v) for v in r)});"
        )
    for r in db_rows:
        stmts.append(
            f"INSERT INTO {PREFIX}_daily_balances ({db_cols}) VALUES "
            f"({', '.join(lit(v) for v in r)});"
        )
    return "\n".join(stmts) + "\n"


def drop_all(cur):
    """Best-effort teardown of every PREFIX-owned object (fresh re-runs)."""
    cur.execute(
        "SELECT object_name, object_type FROM user_objects "
        f"WHERE object_name LIKE UPPER('{PREFIX}%') "
        "AND object_type IN ('TABLE','VIEW','MATERIALIZED VIEW','SEQUENCE')"
    )
    for name, otype in cur.fetchall():
        try:
            if otype == "TABLE":
                cur.execute(f'DROP TABLE "{name}" CASCADE CONSTRAINTS PURGE')
            else:
                cur.execute(f'DROP {otype} "{name}"')
        except oracledb.DatabaseError:
            pass


def main():
    cells_full, day_opts = build_domain(BAL_OPTS, LEG_ATOMS)
    cells = cells_full[::4]  # stratified quarter slice
    print(f"[domain A quarter-slice] day_opts={len(day_opts)} "
          f"cells={len(cells)} (full={len(cells_full)})")
    tx_rows, db_rows = build_rows(cells, "en")
    n_rows = len(tx_rows) + len(db_rows)
    print(f"rows: tx={len(tx_rows)} bal={len(db_rows)} total={n_rows}")

    instance = load_instance(SPEC)
    conn = oracledb.connect(oracle_dsn(URL))
    cur = conn.cursor()
    drop_all(cur)
    conn.commit()

    timings: dict[str, float] = {}

    # ---- stage 1: schema apply (real emitter + real execute_script) ----
    schema_sql = emit_schema(instance, prefix=PREFIX, dialect=DIALECT)
    t0 = time.perf_counter()
    execute_script(cur, schema_sql, dialect=DIALECT)
    conn.commit()
    timings["schema_apply"] = time.perf_counter() - t0
    print(f"schema apply: {timings['schema_apply']:.2f}s "
          f"({len(split_oracle_script(schema_sql))} stmts)")

    # ---- stage 2: config kv populate (deploy-path emitter) ----
    cfg_sql = emit_config_populate_sql(
        prefix=PREFIX, cfg_json="{}",
        l2_json='{"rails": [], "limit_schedules": []}',
        as_of=dt.datetime(2030, 1, 3), dialect=DIALECT)
    t0 = time.perf_counter()
    execute_script(cur, cfg_sql, dialect=DIALECT)
    conn.commit()
    timings["config_populate"] = time.perf_counter() - t0
    print(f"config populate: {timings['config_populate']:.2f}s")

    # ---- stage 3a: bulk insert, real execute_script path (DPL expected) --
    seed_sql = seed_sql_text(tx_rows, db_rows)
    dpl = hasattr(cur, "connection") and hasattr(cur.connection, "direct_path_load")
    t0 = time.perf_counter()
    execute_script(cur, seed_sql, dialect=DIALECT)
    conn.commit()
    timings["insert_real_path"] = time.perf_counter() - t0
    print(f"bulk insert via execute_script "
          f"({'DPL fast path' if dpl else 'INSERT-ALL path'}): "
          f"{timings['insert_real_path']:.2f}s "
          f"({n_rows / timings['insert_real_path']:.0f} rows/s)")
    cur.execute(f"SELECT COUNT(*) FROM {PREFIX}_transactions")
    ntx = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {PREFIX}_daily_balances")
    nbal = cur.fetchone()[0]
    assert (ntx, nbal) == (len(tx_rows), len(db_rows)), (ntx, nbal)

    # ---- stage 4: matview refresh (real emitter) ----
    refresh_sql = refresh_matviews_sql(instance, prefix=PREFIX, dialect=DIALECT)
    t0 = time.perf_counter()
    execute_script(cur, refresh_sql, dialect=DIALECT)
    conn.commit()
    timings["refresh_matviews"] = time.perf_counter() - t0
    print(f"refresh matviews: {timings['refresh_matviews']:.2f}s "
          f"({len(split_oracle_script(refresh_sql))} stmts)")

    # ---- stage 5: one violation-set SELECT per money detector ----
    eng = {}
    for det in ("drift", "overdraft", "expected_eod_balance_breach"):
        t0 = time.perf_counter()
        cur.execute(
            f"SELECT account_id, business_day_start FROM {PREFIX}_{det}")
        rows = cur.fetchall()
        timings[f"select_{det}"] = time.perf_counter() - t0
        eng[det] = {
            (a, ts.date() if isinstance(ts, dt.datetime) else ts)
            for a, ts in rows}
        print(f"SELECT {det}: {timings[f'select_{det}']:.3f}s "
              f"({len(rows)} violations)")

    # ---- sanity: engine == python residual ----
    py = py_residuals(cells, "en")
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

    # ---- stage 3b: forced INSERT-ALL batching path (the footgun path) ----
    cur.execute(f"TRUNCATE TABLE {PREFIX}_transactions")
    cur.execute(f"TRUNCATE TABLE {PREFIX}_daily_balances")
    conn.commit()
    t0 = time.perf_counter()
    stmts = split_oracle_script(seed_sql)
    t_split = time.perf_counter() - t0
    t0 = time.perf_counter()
    batched = batch_oracle_inserts(stmts, batch_size=500)
    t_batch = time.perf_counter() - t0
    t0 = time.perf_counter()
    for s in batched:
        cur.execute(s)
    conn.commit()
    t_exec = time.perf_counter() - t0
    timings["insert_insertall_path"] = t_split + t_batch + t_exec
    cur.execute(f"SELECT COUNT(*) FROM {PREFIX}_transactions")
    ntx2 = cur.fetchone()[0]
    assert ntx2 == len(tx_rows), ntx2
    print(f"bulk insert via INSERT-ALL batching: "
          f"{timings['insert_insertall_path']:.2f}s "
          f"(split {t_split:.2f}s + batch {t_batch:.2f}s + exec {t_exec:.2f}s, "
          f"{len(batched)} round-trips, "
          f"{n_rows / timings['insert_insertall_path']:.0f} rows/s)")

    # ---- teardown of probe objects ----
    drop_all(cur)
    conn.commit()
    conn.close()

    print("\n== TIMING TABLE (seconds) ==")
    for k, v in timings.items():
        print(f"{k:>24}: {v:8.2f}")
    total = (timings["schema_apply"] + timings["config_populate"]
             + timings["insert_real_path"] + timings["refresh_matviews"]
             + sum(v for k, v in timings.items() if k.startswith("select_")))
    print(f"{'end-to-end (real path)':>24}: {total:8.2f}")


if __name__ == "__main__":
    main()
