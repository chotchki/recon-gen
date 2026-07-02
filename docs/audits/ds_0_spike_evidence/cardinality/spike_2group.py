"""2-xor-group variant — closes the xor_group_index partition gap.

spec_example declares one group; this variant (scratchpad-only yaml, loader-
validated) adds a second group (SettlementWireA/B) to SettlementTimingCycle.
Exhaustive: per-rail leg option {absent, Posted, Failed, Zq9x} over the 4
member rails x non-member {absent, Posted} = 512 cells, packed in one DB.
Checks per-(transfer, group-index) row partitioning against the residual.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
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
DIALECT = Dialect.DUCKDB
HERE = Path(__file__).parent
DAY = dt.date(2030, 1, 1)
TMPL = "SettlementTimingCycle"
GROUPS = (("SettlementAuto", "SettlementStandard"),
          ("SettlementWireA", "SettlementWireB"))
NONMEMBER = "SettlementSlow"
RAILS = [r for g in GROUPS for r in g]
OPTS = (None, "Posted", "Failed", "Zq9x")

TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)


def main():
    t0 = time.perf_counter()
    instance = load_instance(HERE / "spec_example_2group.yaml")
    schema_sql = emit_schema(instance, prefix=PREFIX, dialect=DIALECT)
    refresh_sql = refresh_matviews_sql(instance, prefix=PREFIX, dialect=DIALECT)
    l2_json = json.dumps(yaml.safe_load(serialize_l2(instance)),
                         separators=(",", ":"))
    kv = kv_rows_for("{}", l2_json, as_of=dt.datetime(2030, 1, 3))

    cells = list(itertools.product(OPTS, OPTS, OPTS, OPTS, (None, "Posted")))
    rows = []
    posting = dt.datetime(DAY.year, DAY.month, DAY.day, 12)
    for i, cell in enumerate(cells):
        tid, acct = f"g2{i:04d}", f"g2a{i:04d}"
        n = 0
        for rail, s in zip(RAILS + [NONMEMBER], cell):
            if s is not None:
                rows.append((f"{tid}L{n}", acct, acct, "CustomerSubledger",
                             "internal", "CustomerLedger", 0, "Credit", s,
                             posting, tid, None, rail, TMPL, "enum",
                             None, None))
                n += 1
    print(f"2-group domain: {len(cells)} cells, {len(rows)} tx rows")

    conn = duckdb.connect(":memory:")
    cur = conn.cursor()
    execute_script(cur, schema_sql, dialect=DIALECT)
    kvt = pa.table({c: [r[j] for r in kv] for j, c in
                    enumerate(("node_id", "parent_id", "key", "value"))})
    conn.register("kv_arrow", kvt)
    conn.execute(f"INSERT INTO {config_table_name(PREFIX)} SELECT * FROM kv_arrow")
    txt = pa.table({c: [r[j] for r in rows] for j, c in enumerate(TX_COLS)})
    conn.register("tx_arrow", txt)
    conn.execute(f"INSERT INTO {PREFIX}_transactions ({', '.join(TX_COLS)}) "
                 f"SELECT * FROM tx_arrow")
    conn.commit()
    execute_script(conn.cursor(), refresh_sql, dialect=DIALECT)
    conn.commit()

    # residual: per (transfer, group-index) — transfer exists iff >=1
    # non-Failed template leg anywhere (member of EITHER group or non-member)
    res = {}
    for i, cell in enumerate(cells):
        tid = f"g2{i:04d}"
        legs = [(rail, s) for rail, s in zip(RAILS + [NONMEMBER], cell)
                if s is not None]
        live = [(r, s) for r, s in legs if s != "Failed"]
        if not live:
            continue
        for gi, members in enumerate(GROUPS):
            fired = sorted(r for r, _ in live if r in members)
            if len(fired) != 1:
                res[(tid, gi)] = (len(fired), tuple(fired), DAY)
    eng_rows = conn.execute(
        f"SELECT transfer_id, xor_group_index, firing_count, fired_rails, "
        f"business_day FROM {PREFIX}_xor_group_violation").fetchall()
    eng = {(t, g): (c, tuple(sorted(f.split(","))) if f else (),
                    b.date() if isinstance(b, dt.datetime) else b)
           for t, g, c, f, b in eng_rows}
    assert len(eng_rows) == len(eng), "duplicate (tid, group) rows!"

    diff = {k for k in eng.keys() ^ res.keys()}
    diff |= {k for k in eng.keys() & res.keys() if eng[k] != res[k]}
    print(f"engine rows={len(eng)} residual rows={len(res)} "
          f"divergent={len(diff)}")
    if diff:
        for k in sorted(diff)[:8]:
            print(f"  {k}: eng={eng.get(k)} res={res.get(k)}")
    else:
        print("2-GROUP PARTITION: EXACT MATCH")
    # partition-precision spot check: cells where group0 is exactly-one but
    # group1 violates must emit ONLY the group1 row
    spot = 0
    for i, cell in enumerate(cells):
        tid = f"g2{i:04d}"
        if (tid, 0) not in res and (tid, 1) in res and (tid, 0) not in eng:
            spot += 1
    print(f"one-group-clean/other-violating cells (row emitted only for the "
          f"violating group): {spot}")
    print(f"total: {time.perf_counter() - t0:.2f}s")


if __name__ == "__main__":
    main()
