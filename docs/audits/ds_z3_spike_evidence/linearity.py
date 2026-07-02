"""Refresh-time linearity check: pack 1x / 2x / 4x copies of domain A."""
import time
import datetime as dt
from throughput import (build_domain, build_rows, bulk_insert, fresh_db,
                        run_refresh, BAL_OPTS, LEG_ATOMS, PREFIX)
from recon_gen.common.l2.schema import refresh_matviews_sql
from recon_gen.common.sql import Dialect

cells, _ = build_domain(BAL_OPTS, LEG_ATOMS)
for mult in (1, 2, 4):
    conn, instance = fresh_db()
    total = 0
    t_ins = 0.0
    for m in range(mult):
        tx, db = build_rows(cells, f"m{m}x")
        t_ins += bulk_insert(conn, tx, db)
        total += len(tx) + len(db)
    script = refresh_matviews_sql(instance, prefix=PREFIX, dialect=Dialect.DUCKDB)
    t_ref = run_refresh(conn, script)
    n_drift = conn.execute(f"SELECT COUNT(*) FROM {PREFIX}_drift").fetchone()[0]
    print(f"mult={mult} cells={len(cells)*mult:>7} rows={total:>7} "
          f"insert={t_ins:.2f}s refresh={t_ref:.2f}s drift_rows={n_drift}")
    conn.close()
