"""Exhaustive small-domain enumeration spike — the SKEPTIC's empirical anchor.

Packs the ENTIRE 2-day money-family domain (38,416 account-cells) into one
in-memory DuckDB, refreshes the REAL emitted matviews (schema.py, unmodified),
compares drift/overdraft/expected_eod violation sets against an independent
pure-Python residual, then re-runs with mutated detector SQL to measure
mutant-detection on the real engine. All timings reported.
"""
from __future__ import annotations

import datetime as dt
import itertools
import re
import time

import duckdb
from pathlib import Path

from recon_gen.common.db import execute_script
from recon_gen.common.l2.config_table import replace_config
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

PREFIX = "spec_example"
SPEC = Path("/Users/chotchki/workspace/quicksight/tests/l2/spec_example.yaml")
DIALECT = Dialect.DUCKDB
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

# ---- Domain A (CI tier): per day, balance in {absent} u {(m,e)} (7), legs
# multiset<=2 over amount{-1,0,1} x status{Posted,Pending} (28) => 196/day,
# 196^2 = 38,416 cells over 2 days.
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
    """Independent pure-Python residual: drift / overdraft / expected_eod."""
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


def eng_sets(conn):
    def grab(table):
        rows = conn.execute(
            f"SELECT account_id, business_day_start FROM {PREFIX}_{table}"
        ).fetchall()
        return {(a, ts.date() if isinstance(ts, dt.datetime) else ts) for a, ts in rows}
    return grab("drift"), grab("overdraft"), grab("expected_eod_balance_breach")


def fresh_db():
    conn = duckdb.connect(":memory:")
    instance = load_instance(SPEC)
    cur = conn.cursor()
    execute_script(cur, emit_schema(instance, prefix=PREFIX, dialect=DIALECT), dialect=DIALECT)
    conn.commit()
    replace_config(conn, prefix=PREFIX, cfg_json="{}", l2_json='{"rails": [], "limit_schedules": []}',
                   as_of=dt.datetime(2030, 1, 3))
    return conn, instance


def bulk_insert(conn, tx_rows, db_rows):
    import pyarrow as pa
    t0 = time.perf_counter()
    tx_tbl = pa.table({c: [r[i] for r in tx_rows] for i, c in enumerate(TX_COLS)})
    db_tbl = pa.table({c: [r[i] for r in db_rows] for i, c in enumerate(DB_COLS)})
    conn.register("tx_arrow", tx_tbl)
    conn.register("db_arrow", db_tbl)
    conn.execute(
        f"INSERT INTO {PREFIX}_transactions ({', '.join(TX_COLS)}) SELECT * FROM tx_arrow")
    conn.execute(
        f"INSERT INTO {PREFIX}_daily_balances ({', '.join(DB_COLS)}) SELECT * FROM db_arrow")
    conn.unregister("tx_arrow")
    conn.unregister("db_arrow")
    conn.commit()
    return time.perf_counter() - t0


def mutate_stmt(script: str, table: str, pattern: str, repl: str) -> str:
    m = re.search(rf"(CREATE TABLE {table}\s.*?;)", script, re.S)
    assert m, f"no CREATE for {table}"
    stmt = m.group(1)
    new = re.sub(pattern, repl, stmt)
    assert new != stmt, f"mutation no-op for {table}: {pattern}"
    return script.replace(stmt, new)


def run_refresh(conn, script):
    t0 = time.perf_counter()
    cur = conn.cursor()
    execute_script(cur, script, dialect=DIALECT)
    conn.commit()
    return time.perf_counter() - t0


def compare(conn, py):
    t0 = time.perf_counter()
    eng = eng_sets(conn)
    dt_ = time.perf_counter() - t0
    names = ("drift", "overdraft", "expected_eod")
    diffs = {}
    for name, p, e in zip(names, py, eng):
        if p != e:
            diffs[name] = (sorted(p - e)[:3], sorted(e - p)[:3], len(p - e), len(e - p))
    return diffs, dt_


def main():
    # ---------------- primary exhaustive run ----------------
    cells, day_opts = build_domain(BAL_OPTS, LEG_ATOMS)
    print(f"[domain A/CI] day_opts={len(day_opts)} cells={len(cells)}")
    tx_rows, db_rows = build_rows(cells, "en")
    print(f"rows: tx={len(tx_rows)} bal={len(db_rows)} total={len(tx_rows)+len(db_rows)}")

    conn, instance = fresh_db()
    t_ins = bulk_insert(conn, tx_rows, db_rows)
    print(f"insert: {t_ins:.2f}s")

    script = refresh_matviews_sql(instance, prefix=PREFIX, dialect=DIALECT)
    t_ref = run_refresh(conn, script)
    print(f"refresh (full 20+ matview chain, real emitter): {t_ref:.2f}s")

    t0 = time.perf_counter()
    py = py_residuals(cells, "en")
    t_py = time.perf_counter() - t0
    diffs, t_cmp = compare(conn, py)
    print(f"python residual: {t_py:.2f}s  comparator read: {t_cmp:.2f}s")
    print(f"violation counts py: drift={len(py[0])} over={len(py[1])} eod={len(py[2])}")
    print(f"BASELINE disagreements: {diffs if diffs else 'NONE — engine == residual on all cells'}")

    # ---------------- mutant A: status filter dropped in csb ----------------
    mA = mutate_stmt(script, f"{PREFIX}_computed_subledger_balance",
                     r"AND tx\.status = 'Posted'\s*", "")
    tA = run_refresh(conn, mA)
    diffsA, _ = compare(conn, py)
    got = diffsA.get("drift")
    print(f"MUTANT A (drop status='Posted'): refresh {tA:.2f}s -> "
          f"{'CAUGHT' if diffsA else 'MISSED'} "
          f"{ {k: (v[2], v[3]) for k, v in diffsA.items()} }")
    if got:
        print(f"  sample counterexample cells: py-only={got[0]} eng-only={got[1]}")

    # ---------------- mutant B: posting <= day_end -> < (boundary) ----------
    mB = mutate_stmt(script, f"{PREFIX}_computed_subledger_balance",
                     r"tx\.posting <= sb\.business_day_end",
                     "tx.posting < sb.business_day_end")
    tB = run_refresh(conn, mB)
    diffsB, _ = compare(conn, py)
    print(f"MUTANT B (<= -> < on day boundary) on noon-only domain: refresh {tB:.2f}s -> "
          f"{'CAUGHT' if diffsB else 'MISSED (postings never AT day_end — boundary absent from domain)'}")

    # ---------------- mutant B rerun on boundary-augmented domain -----------
    conn.close()
    bal_b: list = [None] + [(m, None) for m in (-1, 0, 1)]
    atoms_b = [(a, "Posted", t) for a in (-1, 1) for t in (dt.time(12, 0), dt.time(23, 59, 59))]
    cells_b, day_opts_b = build_domain(bal_b, atoms_b)
    print(f"[domain A-boundary] day_opts={len(day_opts_b)} cells={len(cells_b)}")
    txb, dbb = build_rows(cells_b, "bd")
    conn2, _ = fresh_db()
    bulk_insert(conn2, txb, dbb)
    run_refresh(conn2, script)
    pyb = py_residuals(cells_b, "bd")
    d0, _ = compare(conn2, pyb)
    print(f"  baseline on boundary domain: {'clean' if not d0 else d0}")
    run_refresh(conn2, mB)
    dB2, _ = compare(conn2, pyb)
    print(f"  MUTANT B on boundary domain: "
          f"{'CAUGHT ' + str({k: (v[2], v[3]) for k, v in dB2.items()}) if dB2 else 'MISSED'}")

    # ---------------- mutant C: supersession argmax dropped -----------------
    # tiny supersession domain: 1 day, emit sequences len 1-2 (money {-1,0,1}),
    # legs <=1 posted {-1,0,1} -> exercises current_daily_balances dedup.
    conn2.close()
    conn3, _ = fresh_db()
    tx3, db3 = [], []
    cells_c = []
    seqs = [(m,) for m in (-1, 0, 1)] + [(m1, m2) for m1 in (-1, 0, 1) for m2 in (-1, 0, 1)]
    legs_c = [None, -1, 0, 1]
    i = 0
    for seq in seqs:
        for leg in legs_c:
            acct = f"sp{i:04d}"
            cells_c.append((acct, seq, leg))
            d0d = D[0]
            bds = dt.datetime(d0d.year, d0d.month, d0d.day)
            bde = dt.datetime(d0d.year, d0d.month, d0d.day, 23, 59, 59)
            for m in seq:  # entry auto-increments: later insert supersedes
                db3.append((acct, acct, "CustomerSubledger", "internal",
                            "CustomerLedger", None, bds, bde, m, None))
            if leg is not None:
                tid = f"sp{i:04d}t"
                tx3.append((tid, acct, acct, "CustomerSubledger", "internal",
                            "CustomerLedger", leg, "Credit" if leg >= 0 else "Debit",
                            "Posted", dt.datetime(d0d.year, d0d.month, d0d.day, 12), tid,
                            None, "RailX", None, "enum", None, None))
            i += 1
    bulk_insert(conn3, tx3, db3)
    run_refresh(conn3, script)
    py_drift_c = set()
    for acct, seq, leg in cells_c:
        stored = seq[-1]  # supersession: last entry wins
        computed = leg if leg is not None else 0
        if stored != computed:
            py_drift_c.add((acct, D[0]))
    eng_drift_c = {(a, ts.date()) for a, ts in conn3.execute(
        f"SELECT account_id, business_day_start FROM {PREFIX}_drift").fetchall()}
    print(f"[supersession lemma domain] cells={len(cells_c)} baseline "
          f"{'clean' if py_drift_c == eng_drift_c else 'DIVERGES'}")
    mC = mutate_stmt(script, f"{PREFIX}_current_daily_balances",
                     r"(?s)WHERE sb\.entry = \(.*\)", "", )
    try:
        run_refresh(conn3, mC)
        eng_c2 = {(a, ts.date()) for a, ts in conn3.execute(
            f"SELECT account_id, business_day_start FROM {PREFIX}_drift").fetchall()}
        print(f"  MUTANT C (supersession dropped): "
              f"{'CAUGHT diff=' + str(len(py_drift_c ^ eng_c2)) if py_drift_c != eng_c2 else 'MISSED'}")
    except Exception as exc:  # engine-side integrity failure is also detection
        print(f"  MUTANT C: refresh CRASHED on real engine (also detection): {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
