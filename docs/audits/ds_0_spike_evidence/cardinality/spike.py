"""DS.0 pre-lock spike — exhaustive enumeration of the cardinality family.

xor_group_violation (warm-up) + multi_xor_violation on the REAL emitter
(spec_example, unmodified), CURRENT semantics (status <> 'Failed').

Phases:
  1. packed exhaustive run per family, engine vs independent Python residual
  2. combined-DB run (both families in one DB) — cross-family packing check
  3. isolated sample (~200 cells, one DB each) vs packed — packing-independence
  4. generalized regex mutation harness (X1-X3 + own picks), every mutant
     killed-or-survivor
  5. timings throughout
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import random
import re
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import yaml

from recon_gen.common.db import execute_script
from recon_gen.common.l2.config_table import config_table_name, kv_rows_for
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.serializer import serialize_l2
from recon_gen.common.sql import Dialect

PREFIX = "spec_example"
SPEC = Path("/Users/chotchki/workspace/quicksight/tests/l2/spec_example.yaml")
DIALECT = Dialect.DUCKDB
DAYS = (dt.date(2030, 1, 1), dt.date(2030, 1, 2))
STATUSES = ("Posted", "Pending", "Failed", "Zq9x")   # Zq9x = the unknown tail
CH_STATUSES = ("Posted", "Failed", "Zq9x")

# --- xor_group_violation shape (spec_example.yaml:406-416) ------------------
XOR_TMPL = "SettlementTimingCycle"
XOR_MEMBERS = ("SettlementAuto", "SettlementStandard")
XOR_NONMEMBER = "SettlementSlow"

# --- multi_xor_violation shapes (spec_example.yaml:569-606) -----------------
# non-member child rail deliberately = the OTHER chain's declared sibling, so
# the correlated-EXISTS membership check is probed for cross-chain precision.
MX_KINDS = (
    dict(kind="rail", parent="BulkAccrualSettlement",
         parent_rail="BulkAccrualSettlement", parent_tmpl=None,
         sibs=("BulkAccrualSettleACH", "BulkAccrualSettleWire"),
         nonmember="DisbursementSettleACH"),
    dict(kind="template", parent="DisbursementCycle",
         parent_rail="DisbursementAccrual", parent_tmpl="DisbursementCycle",
         sibs=("DisbursementSettleACH", "DisbursementSettleCheck"),
         nonmember="BulkAccrualSettleWire"),
)

TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)

TIMINGS: list[tuple[str, float]] = []


def timed(name):
    class _T:
        def __enter__(self):
            self.t0 = time.perf_counter()
            return self

        def __exit__(self, *a):
            self.dt = time.perf_counter() - self.t0
            TIMINGS.append((name, self.dt))
            print(f"    [t] {name}: {self.dt:.2f}s")
    return _T()


# ---------------------------------------------------------------------------
# Domain construction
# ---------------------------------------------------------------------------

def multisets(atoms, max_n):
    """All multisets of size 0..max_n over atoms (order-canonical)."""
    out = [()]
    for n in range(1, max_n + 1):
        out += list(itertools.combinations_with_replacement(atoms, n))
    return out


# xor cell = (memberA_legs, memberB_legs, extra_legs)
#   member atom = (status, day_idx)            -> 8 atoms, multiset<=2 -> 45
#   extra slot  = () | one (tag, status, day)  -> 11 options
#     tag SLOW        = non-member rail leg, template-stamped (8 status x day)
#     tag AUTO_NOTMPL = member-rail leg with template_name NULL (inert probe)
MEMBER_ATOMS = [(s, d) for s in STATUSES for d in (0, 1)]
EXTRA_OPTS = (
    [()]
    + [((("SLOW", s, d)),) for s in STATUSES for d in (0, 1)]
    + [(("AUTO_NOTMPL", "Posted", 0),), (("AUTO_NOTMPL", "Zq9x", 0),)]
)


def build_xor_domain():
    mm = multisets(MEMBER_ATOMS, 2)
    return [(a, b, e) for a in mm for b in mm for e in EXTRA_OPTS]


# mx cell = (kind_idx, parent_legs, childA_statuses, childB_statuses, extra)
#   parent atom = (status, day_idx) -> 8, multiset<=2 -> 45 (incl. absent parent)
#   childX = multiset<=2 over CH_STATUSES -> 10  (each element = one child
#            TRANSFER with a single leg on that sibling rail)
#   extra  = None | 'Posted' | 'Failed'  (one non-member child transfer)
def build_mx_domain():
    pm = multisets(MEMBER_ATOMS, 2)
    cm = multisets(CH_STATUSES, 2)
    return [(k, p, a, b, e)
            for k in range(len(MX_KINDS))
            for p in pm for a in cm for b in cm
            for e in (None, "Posted", "Failed")]


# ---------------------------------------------------------------------------
# Row builders (transfer-keyed disjoint ids)
# ---------------------------------------------------------------------------

def _posting(day_idx, hour=12):
    d = DAYS[day_idx]
    return dt.datetime(d.year, d.month, d.day, hour, 0, 0)


def _tx(id_, acct, status, posting, tid, ptid, rail, tmpl):
    return (id_, acct, acct, "CustomerSubledger", "internal", "CustomerLedger",
            0, "Credit", status, posting, tid, ptid, rail, tmpl, "enum",
            None, None)


def xor_rows(cells):
    rows = []
    for i, (ma, mb, ex) in enumerate(cells):
        tid, acct = f"xr{i:06d}", f"xa{i:06d}"
        n = 0
        for rail, legs in ((XOR_MEMBERS[0], ma), (XOR_MEMBERS[1], mb)):
            for s, d in legs:
                rows.append(_tx(f"{tid}L{n}", acct, s, _posting(d), tid,
                                None, rail, XOR_TMPL))
                n += 1
        for tag, s, d in ex:
            if tag == "SLOW":
                rows.append(_tx(f"{tid}L{n}", acct, s, _posting(d), tid,
                                None, XOR_NONMEMBER, XOR_TMPL))
            else:  # AUTO_NOTMPL — member rail, NULL template
                rows.append(_tx(f"{tid}L{n}", acct, s, _posting(d), tid,
                                None, XOR_MEMBERS[0], None))
            n += 1
    return rows


def mx_rows(cells):
    rows = []
    for i, (k, pl, ca, cb, ex) in enumerate(cells):
        kind = MX_KINDS[k]
        ptid, acct = f"mx{i:06d}", f"ma{i:06d}"
        for n, (s, d) in enumerate(pl):
            rows.append(_tx(f"{ptid}pL{n}", acct, s, _posting(d), ptid,
                            None, kind["parent_rail"], kind["parent_tmpl"]))
        for slot, (rail, sts) in enumerate(
                ((kind["sibs"][0], ca), (kind["sibs"][1], cb))):
            for j, s in enumerate(sts):
                ctid = f"{ptid}c{slot}{j}"
                rows.append(_tx(f"{ctid}L0", acct, s, _posting(0), ctid,
                                ptid, rail, None))
        if ex is not None:
            ctid = f"{ptid}cx"
            rows.append(_tx(f"{ctid}L0", acct, ex, _posting(0), ctid,
                            ptid, kind["nonmember"], None))
    return rows


# ---------------------------------------------------------------------------
# Independent Python residuals — CURRENT semantics (status <> 'Failed')
# ---------------------------------------------------------------------------

def xor_residual(cells):
    """Spec-reading residual for xor_group_violation.

    Violation row per (transfer, template, group 0) where the count of
    non-Failed member-rail legs <> 1; the transfer exists iff it has >=1
    non-Failed template-stamped leg (member or non-member). AUTO_NOTMPL
    legs are inert (NULL template never matches the template partition).
    key: tid -> (group_idx, firing_count, fired_rails_sorted, business_day)
    """
    out = {}
    for i, (ma, mb, ex) in enumerate(cells):
        tid = f"xr{i:06d}"
        tmpl_legs = ([(XOR_MEMBERS[0], s, d) for s, d in ma]
                     + [(XOR_MEMBERS[1], s, d) for s, d in mb]
                     + [(XOR_NONMEMBER, s, d) for tag, s, d in ex
                        if tag == "SLOW"])
        live = [(r, s, d) for r, s, d in tmpl_legs if s != "Failed"]
        if not live:
            continue
        bd = DAYS[min(d for _, _, d in live)]
        fired = sorted(r for r, _, _ in live if r in XOR_MEMBERS)
        if len(fired) != 1:
            out[tid] = (0, len(fired), tuple(fired), bd)
    return out


def mx_residual_spec(cells):
    """Spec-reading residual for multi_xor_violation (docstring semantics):
    child_count = number of DISTINCT declared sibling names with >=1
    non-Failed child transfer; parent exists iff >=1 non-Failed parent leg.
    key: ptid -> (parent_name, child_count, fired_sorted, kind, business_day)
    """
    out = {}
    for i, (k, pl, ca, cb, ex) in enumerate(cells):
        kind = MX_KINDS[k]
        ptid = f"mx{i:06d}"
        pdays = sorted({d for s, d in pl if s != "Failed"})
        if not pdays:
            continue
        names = []
        for sib, sts in ((kind["sibs"][0], ca), (kind["sibs"][1], cb)):
            if any(s != "Failed" for s in sts):
                names.append(sib)
        if len(names) != 1:
            out[ptid] = (kind["parent"], len(names), tuple(sorted(names)),
                         "missed" if not names else "overlap", DAYS[pdays[0]])
    return out


def mx_residual_engine(cells):
    """Engine-model residual: mirrors the emitted SQL's actual grouping —
    fired_children_distinct is DISTINCT (ptid, parent, DAY, name), so
    child_count = (#distinct non-Failed parent posting days) x (#names),
    and fired_children repeats each name once per parent day.
    """
    out = {}
    for i, (k, pl, ca, cb, ex) in enumerate(cells):
        kind = MX_KINDS[k]
        ptid = f"mx{i:06d}"
        pdays = sorted({d for s, d in pl if s != "Failed"})
        if not pdays:
            continue
        names = []
        for sib, sts in ((kind["sibs"][0], ca), (kind["sibs"][1], cb)):
            if any(s != "Failed" for s in sts):
                names.append(sib)
        count = len(pdays) * len(names)
        if count != 1:
            out[ptid] = (kind["parent"], count,
                         tuple(sorted(names * len(pdays))),
                         "missed" if count == 0 else "overlap",
                         DAYS[pdays[0]])
    return out


# ---------------------------------------------------------------------------
# DB machinery
# ---------------------------------------------------------------------------

INSTANCE = load_instance(SPEC)
SCHEMA_SQL = emit_schema(INSTANCE, prefix=PREFIX, dialect=DIALECT)
REFRESH_SQL = refresh_matviews_sql(INSTANCE, prefix=PREFIX, dialect=DIALECT)
L2_JSON = json.dumps(yaml.safe_load(serialize_l2(INSTANCE)),
                     separators=(",", ":"))
KV_ROWS = kv_rows_for("{}", L2_JSON, as_of=dt.datetime(2030, 1, 3))


def fresh_db():
    conn = duckdb.connect(":memory:")
    cur = conn.cursor()
    execute_script(cur, SCHEMA_SQL, dialect=DIALECT)
    conn.commit()
    # bulk config-kv populate (bypasses replace_config's row-loop for speed;
    # same rows — kv_rows_for is the shared serializer)
    kv = pa.table({c: [r[i] for r in KV_ROWS]
                   for i, c in enumerate(("node_id", "parent_id", "key",
                                          "value"))})
    conn.register("kv_arrow", kv)
    conn.execute(f"INSERT INTO {config_table_name(PREFIX)} "
                 f"(node_id, parent_id, key, value) SELECT * FROM kv_arrow")
    conn.unregister("kv_arrow")
    conn.commit()
    return conn


def bulk_insert_tx(conn, rows):
    tbl = pa.table({c: [r[i] for r in rows] for i, c in enumerate(TX_COLS)})
    conn.register("tx_arrow", tbl)
    conn.execute(f"INSERT INTO {PREFIX}_transactions ({', '.join(TX_COLS)}) "
                 f"SELECT * FROM tx_arrow")
    conn.unregister("tx_arrow")
    conn.commit()


def run_refresh(conn, script=REFRESH_SQL):
    cur = conn.cursor()
    execute_script(cur, script, dialect=DIALECT)
    conn.commit()


def _day(ts):
    return ts.date() if isinstance(ts, dt.datetime) else ts


def read_xor(conn):
    rows = conn.execute(
        f"SELECT transfer_id, xor_group_index, firing_count, fired_rails, "
        f"business_day FROM {PREFIX}_xor_group_violation").fetchall()
    return {t: (g, c, tuple(sorted(f.split(","))) if f else (), _day(b))
            for t, g, c, f, b in rows}


def read_mx(conn):
    rows = conn.execute(
        f"SELECT parent_transfer_id, parent_rail_or_template_name, "
        f"child_count, fired_children, disagreement_kind, business_day "
        f"FROM {PREFIX}_multi_xor_violation").fetchall()
    return {t: (p, c, tuple(sorted(f.split(","))) if f else (), k, _day(b))
            for t, p, c, f, k, b in rows}


def diff_report(name, eng, res, cells_by_tid=None, limit=6):
    only_eng = {k: eng[k] for k in eng.keys() - res.keys()}
    only_res = {k: res[k] for k in res.keys() - eng.keys()}
    both_diff = {k: (eng[k], res[k]) for k in eng.keys() & res.keys()
                 if eng[k] != res[k]}
    n = len(only_eng) + len(only_res) + len(both_diff)
    if n == 0:
        print(f"    {name}: EXACT MATCH ({len(eng)} violation rows)")
        return 0
    print(f"    {name}: {n} DIVERGENT keys "
          f"(eng-only={len(only_eng)} res-only={len(only_res)} "
          f"value-diff={len(both_diff)}); eng={len(eng)} res={len(res)}")
    for label, d in (("eng-only", only_eng), ("res-only", only_res),
                     ("value-diff", both_diff)):
        for k in sorted(d)[:limit // 2]:
            cell = ""
            if cells_by_tid is not None:
                cell = f"  cell={cells_by_tid[k]}"
            print(f"      {label} {k}: {d[k]}{cell}")
    return n


# ---------------------------------------------------------------------------
# Generalized mutation harness
# ---------------------------------------------------------------------------

def extract_create(script, table):
    m = re.search(rf"(CREATE TABLE {table}\s.*?;)", script, re.S)
    assert m, f"no CREATE TABLE for {table}"
    return m.group(1)


def apply_mutation(create_stmt, pattern, repl):
    new = re.sub(pattern, repl, create_stmt)
    assert new != create_stmt, f"mutation no-op: {pattern}"
    return new


def run_mutant(conn, table, mutated_create):
    t0 = time.perf_counter()
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(mutated_create)
    conn.commit()
    return time.perf_counter() - t0


MUTATIONS = [
    # (id, family, pattern, replacement, expectation note)
    ("X1", "xor",
     r"HAVING COUNT\(tx\.transfer_id\) <> 1",
     "HAVING COUNT(tx.transfer_id) > 1",
     "missed-firing (count=0) cells vanish"),
    ("X2", "xor",
     r"AND tx\.status <> 'Failed'",
     "AND tx.status = 'Posted'",
     "Pending/Zq9x member legs uncounted (LEFT-JOIN status law)"),
    ("X3", "xor",
     r"COUNT\(tx\.transfer_id\)",
     "COUNT(DISTINCT tx.rail_name)",
     "2-legs-same-rail overlap collapses to count=1"),
    ("X4", "xor",
     r"  AND tx\.template_name = e\.template_name\n",
     "",
     "own pick: NULL-template member-rail legs (AUTO_NOTMPL) start counting"),
    ("MX1", "mx",
     r"HAVING COUNT\(fcd\.matched_child_name\) <> 1",
     "HAVING COUNT(fcd.matched_child_name) > 1",
     "missed (count=0) cells vanish"),
    ("MX2", "mx",
     r"AND ch\.status <> 'Failed'",
     "AND ch.status = 'Posted'",
     "Zq9x child transfers uncounted"),
    ("MX3", "mx",
     r"AND m\.child_name = ch\.rail_name",
     "AND 1 = 1",
     "own pick: membership EXISTS always-true; non-member children counted"),
    ("MX4", "mx",
     r"ON ch\.transfer_parent_id = pf\.parent_transfer_id",
     "ON ch.transfer_id = pf.parent_transfer_id",
     "own pick: join-key corruption; children never match"),
    ("MX5", "mx",
     r"HAVING COUNT\(\*\) >= 2",
     "HAVING COUNT(*) >= 3",
     "own pick: multi-XOR qualifier off-by-one; both chains vanish"),
    ("MX6", "mx",
     r"  UNION\n",
     "  UNION ALL\n",
     "own pick: parent_firings UNION dedup dropped — predicted SURVIVOR "
     "(cross-arm dupes need a rail and template sharing a name; both arms "
     "are internally DISTINCT)"),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 72)
    print("PHASE 1 — packed exhaustive runs")
    print("=" * 72)

    with timed("xor: domain build"):
        xcells = build_xor_domain()
    xrows = xor_rows(xcells)
    print(f"  xor domain: {len(xcells)} cells, {len(xrows)} tx rows "
          f"(member-slot opts={len(multisets(MEMBER_ATOMS, 2))}, "
          f"extra opts={len(EXTRA_OPTS)})")

    with timed("mx: domain build"):
        mcells = build_mx_domain()
    mrows = mx_rows(mcells)
    print(f"  mx domain: {len(mcells)} cells, {len(mrows)} tx rows "
          f"(kinds=2, parent opts=45, child opts=10x10, extra=3)")

    xcell_by_tid = {f"xr{i:06d}": c for i, c in enumerate(xcells)}
    mcell_by_tid = {f"mx{i:06d}": c for i, c in enumerate(mcells)}

    # -- xor packed --
    with timed("xor: fresh_db+schema"):
        xconn = fresh_db()
    with timed("xor: insert"):
        bulk_insert_tx(xconn, xrows)
    with timed("xor: refresh (full real matview chain)"):
        run_refresh(xconn)
    with timed("xor: python residual"):
        xres = xor_residual(xcells)
    with timed("xor: engine read + compare"):
        xeng = read_xor(xconn)
        xdiv = diff_report("xor packed engine vs residual", xeng, xres,
                           xcell_by_tid)

    # -- mx packed --
    with timed("mx: fresh_db+schema"):
        mconn = fresh_db()
    with timed("mx: insert"):
        bulk_insert_tx(mconn, mrows)
    with timed("mx: refresh (full real matview chain)"):
        run_refresh(mconn)
    with timed("mx: python residuals (spec + engine-model)"):
        mres_spec = mx_residual_spec(mcells)
        mres_eng = mx_residual_engine(mcells)
    with timed("mx: engine read + compare"):
        meng = read_mx(mconn)
        print("  -- vs SPEC-reading residual (docstring semantics):")
        mdiv_spec = diff_report("mx packed engine vs spec-residual",
                                meng, mres_spec, mcell_by_tid)
        print("  -- vs ENGINE-model residual (day-multiplied counts):")
        mdiv_eng = diff_report("mx packed engine vs engine-model residual",
                               meng, mres_eng, mcell_by_tid)

    # classify spec-vs-engine divergence
    if mdiv_spec:
        classes = {}
        for k in (meng.keys() | mres_spec.keys()):
            if meng.get(k) == mres_spec.get(k):
                continue
            cell = mcell_by_tid[k]
            _, pl, ca, cb, ex = cell
            pdays = len({d for s, d in pl if s != "Failed"})
            names = sum(1 for sts in (ca, cb) if any(s != "Failed" for s in sts))
            classes[(pdays, names)] = classes.get((pdays, names), 0) + 1
        print(f"    divergence classes by (parent_days, fired_names): "
              f"{dict(sorted(classes.items()))}")

    print()
    print("=" * 72)
    print("PHASE 2 — combined DB (both families packed together)")
    print("=" * 72)
    with timed("combined: fresh+insert+refresh"):
        cconn = fresh_db()
        bulk_insert_tx(cconn, xrows + mrows)
        run_refresh(cconn)
    with timed("combined: compare"):
        cxeng = read_xor(cconn)
        cmeng = read_mx(cconn)
        same_x = cxeng == xeng
        same_m = cmeng == meng
        print(f"    xor rows combined==xor-only-packed: {same_x} "
              f"({len(cxeng)} rows)")
        print(f"    mx rows combined==mx-only-packed:  {same_m} "
              f"({len(cmeng)} rows)")
        if not same_x:
            diff_report("combined-vs-packed xor", cxeng, xeng, xcell_by_tid)
        if not same_m:
            diff_report("combined-vs-packed mx", cmeng, meng, mcell_by_tid)
    cconn.close()

    print()
    print("=" * 72)
    print("PHASE 3 — isolated sample (one cell per DB) vs packed")
    print("=" * 72)
    rng = random.Random(42)

    def xor_interesting(i):
        ma, mb, ex = xcells[i]
        return (not ma and not mb) or any(t == "AUTO_NOTMPL" for t, *_ in ex)

    def mx_interesting(i):
        k, pl, ca, cb, ex = mcells[i]
        pdays = len({d for s, d in pl if s != "Failed"})
        return pdays == 2 or ex is not None or not pl

    xsample = sorted(set(rng.sample(range(len(xcells)), 80))
                     | {i for i in range(len(xcells)) if xor_interesting(i)
                        and rng.random() < 0.005})
    msample = sorted(set(rng.sample(range(len(mcells)), 80))
                     | {i for i in range(len(mcells)) if mx_interesting(i)
                        and rng.random() < 0.002})
    print(f"  sample sizes: xor={len(xsample)} mx={len(msample)}")

    mismatches = 0
    with timed(f"isolated: {len(xsample)} xor cells"):
        for i in xsample:
            conn = fresh_db()
            bulk_insert_tx(conn, xor_rows_single(xcells, i))
            run_refresh(conn)
            iso = read_xor(conn)
            tid = f"xr{i:06d}"
            packed = {tid: xeng[tid]} if tid in xeng else {}
            if iso != packed:
                mismatches += 1
                print(f"    XOR PACKING MISMATCH cell {i} {xcells[i]}: "
                      f"iso={iso} packed={packed}")
            conn.close()
    with timed(f"isolated: {len(msample)} mx cells"):
        for i in msample:
            conn = fresh_db()
            bulk_insert_tx(conn, mx_rows_single(mcells, i))
            run_refresh(conn)
            iso = read_mx(conn)
            tid = f"mx{i:06d}"
            packed = {tid: meng[tid]} if tid in meng else {}
            if iso != packed:
                mismatches += 1
                print(f"    MX PACKING MISMATCH cell {i} {mcells[i]}: "
                      f"iso={iso} packed={packed}")
            conn.close()
    print(f"  packing-independence verdict: "
          f"{'CLEAN' if mismatches == 0 else f'{mismatches} MISMATCHES'}")

    print()
    print("=" * 72)
    print("PHASE 4 — generalized mutation harness")
    print("=" * 72)
    xor_mv = f"{PREFIX}_xor_group_violation"
    mx_mv = f"{PREFIX}_multi_xor_violation"
    xor_create = extract_create(REFRESH_SQL, xor_mv)
    mx_create = extract_create(REFRESH_SQL, mx_mv)
    results = []
    for mid, fam, pat, repl, note in MUTATIONS:
        table = xor_mv if fam == "xor" else mx_mv
        base_create = xor_create if fam == "xor" else mx_create
        conn = xconn if fam == "xor" else mconn
        baseline = xres if fam == "xor" else mres_eng
        reader = read_xor if fam == "xor" else read_mx
        mutated = apply_mutation(base_create, pat, repl)
        t = run_mutant(conn, table, mutated)
        eng = reader(conn)
        ndiff = (len(eng.keys() ^ baseline.keys())
                 + sum(1 for k in eng.keys() & baseline.keys()
                       if eng[k] != baseline[k]))
        # witnesses
        wit = []
        for k in sorted(eng.keys() ^ baseline.keys())[:2]:
            wit.append((k, "eng" if k in eng else "res-only",
                        eng.get(k, baseline.get(k))))
        if not wit:
            for k in sorted(eng.keys() & baseline.keys()):
                if eng[k] != baseline[k]:
                    wit.append((k, "value-diff", (eng[k], baseline[k])))
                    if len(wit) == 2:
                        break
        status = "KILLED" if ndiff else "SURVIVOR"
        results.append((mid, fam, status, ndiff, t, note, wit))
        print(f"  {mid} [{fam}] {status}: {ndiff} divergent keys "
              f"({t:.2f}s) — {note}")
        for w in wit:
            print(f"      witness: {w}")
        # restore baseline matview
        run_mutant(conn, table, base_create)
    # verify restoration
    assert read_xor(xconn) == xeng, "xor baseline not restored"
    assert read_mx(mconn) == meng, "mx baseline not restored"
    xconn.close()
    mconn.close()

    print()
    print("=" * 72)
    print("PHASE 5 — timing summary")
    print("=" * 72)
    for name, t in TIMINGS:
        print(f"  {t:8.2f}s  {name}")
    xor_full = sum(t for n, t in TIMINGS if n.startswith("xor:"))
    mx_full = sum(t for n, t in TIMINGS if n.startswith("mx:"))
    print(f"  xor family full-step total: {xor_full:.2f}s")
    print(f"  mx family full-step total:  {mx_full:.2f}s")
    print(f"  divergences: xor={xdiv} mx_spec={mdiv_spec} mx_engmodel={mdiv_eng}")


def xor_rows_single(cells, i):
    """Rows for exactly one xor cell, preserving packed ids."""
    ma, mb, ex = cells[i]
    sub = [(ma, mb, ex)]
    rows = xor_rows(sub)
    # rewrite the index-0 ids to the packed cell index
    fixed = []
    for r in rows:
        r = list(r)
        for f in (0, 1, 2, 10):  # id, account_id, account_name, transfer_id
            if r[f]:
                r[f] = r[f].replace("xr000000", f"xr{i:06d}").replace(
                    "xa000000", f"xa{i:06d}")
        fixed.append(tuple(r))
    return fixed


def mx_rows_single(cells, i):
    k, pl, ca, cb, ex = cells[i]
    sub = [(k, pl, ca, cb, ex)]
    rows = mx_rows(sub)
    fixed = []
    for r in rows:
        r = list(r)
        for f in (0, 1, 2, 10, 11):  # id, acct, acct_name, tid, ptid
            if r[f]:
                r[f] = r[f].replace("mx000000", f"mx{i:06d}").replace(
                    "ma000000", f"ma{i:06d}")
        fixed.append(tuple(r))
    return fixed


if __name__ == "__main__":
    sys.exit(main())
