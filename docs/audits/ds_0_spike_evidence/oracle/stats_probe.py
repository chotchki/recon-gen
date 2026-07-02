"""DS.0 Oracle stats-placement probe.

Hypothesis: the 429s csb refresh (full domain) is the SAME mechanism as
the confirmed PG bug — refresh_matviews_sql emits every
DBMS_STATS.GATHER_TABLE_STATS at the END of the script, so csb's refresh
plans against never/staleley-analyzed current_* matviews and the per-row
correlated SubPlan goes catastrophic.

Modes (argv[1]):
  baseline        quarter slice, UNMODIFIED emitted order
  reordered       quarter slice, current_* GATHERs moved right after
                  their own refreshes (same statements, new order)
  full-reordered  full 38,416-cell domain, reordered order
  full-baseline   full domain, unmodified order (not normally run; 11min)

Every mode: fresh drop_all + schema + config + DPL seed (verbatim reuse
of oracle_probe.py paths), per-statement refresh timing, optimizer-stats
state dumps, EXPLAIN PLAN for the csb + cdb defining queries in the
state the refresh will plan in, and DBMS_XPLAN.DISPLAY_CURSOR of the
real MV_REFRESH cursors pulled from v$sql right after each hot refresh.
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import oracledb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oracle_probe"))
import oracle_probe as P  # noqa: E402 — reuse domain/seed/drop_all verbatim

from recon_gen.common.db import (  # noqa: E402
    execute_script,
    oracle_dsn,
    split_oracle_script,
)
from recon_gen.common.l2.config_table import emit_config_populate_sql  # noqa: E402
from recon_gen.common.l2.loader import load_instance  # noqa: E402
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql  # noqa: E402
from recon_gen.common.sql import Dialect  # noqa: E402

PREFIX = P.PREFIX
DIALECT = Dialect.ORACLE
URL = P.URL

CSB_SELECT = f"""SELECT
    sb.account_id,
    sb.business_day_start,
    sb.business_day_end,
    sb.account_parent_role,
    COALESCE((
        SELECT SUM(tx.amount_money)
        FROM {PREFIX}_current_transactions tx
        WHERE tx.account_id = sb.account_id
          AND tx.status = 'Posted'
          AND tx.posting <= sb.business_day_end
    ), 0) AS computed_balance
FROM {PREFIX}_current_daily_balances sb
WHERE sb.account_scope = 'internal'
  AND sb.account_parent_role IS NOT NULL"""

CDB_SELECT = f"""SELECT * FROM {PREFIX}_daily_balances sb
WHERE sb.entry = (
    SELECT MAX(entry)
    FROM {PREFIX}_daily_balances
    WHERE account_id = sb.account_id
      AND business_day_start = sb.business_day_start
)"""

WATCH_TABLES = [
    f"{PREFIX}_transactions",
    f"{PREFIX}_daily_balances",
    f"{PREFIX}_current_transactions",
    f"{PREFIX}_current_daily_balances",
    f"{PREFIX}_computed_subledger_balance",
]


def dump_stats_state(cur, label: str) -> None:
    names = ", ".join(f"'{t.upper()}'" for t in WATCH_TABLES)
    cur.execute(
        "SELECT table_name, num_rows, blocks, last_analyzed "
        f"FROM user_tables WHERE table_name IN ({names}) ORDER BY table_name"
    )
    print(f"\n-- optimizer stats state [{label}] --")
    print(f"{'table':<42} {'num_rows':>9} {'blocks':>7}  last_analyzed")
    for name, num_rows, blocks, la in cur.fetchall():
        print(f"{name:<42} {str(num_rows):>9} {str(blocks):>7}  {la}")


def explain(cur, label: str, select_sql: str) -> None:
    cur.execute("DELETE FROM plan_table")
    cur.execute(f"EXPLAIN PLAN FOR {select_sql}")
    cur.execute("SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY(NULL, NULL, 'TYPICAL'))")
    print(f"\n-- EXPLAIN PLAN [{label}] --")
    for (line,) in cur.fetchall():
        print(line)


def real_cursor_plans(cur, mv_name: str, label: str) -> None:
    """DBMS_XPLAN.DISPLAY_CURSOR for the MV_REFRESH INSERT/DELETE cursors."""
    cur.execute(
        "SELECT sql_id, child_number, command_type, executions, "
        "ROUND(elapsed_time/1e6,2), SUBSTR(sql_text, 1, 90) "
        "FROM v$sql WHERE UPPER(sql_text) LIKE :pat "
        "AND UPPER(sql_text) NOT LIKE '%V$SQL%' "
        "AND UPPER(sql_text) NOT LIKE 'EXPLAIN%' "
        "AND command_type IN (2, 7) "  # INSERT, DELETE
        "ORDER BY elapsed_time DESC",
        pat=f"%{mv_name.upper()}%",
    )
    rows = cur.fetchall()
    print(f"\n-- v$sql refresh cursors for {mv_name} [{label}] --")
    for sql_id, child, ctype, execs, elapsed, head in rows:
        print(f"sql_id={sql_id} child={child} type={ctype} execs={execs} "
              f"elapsed={elapsed}s\n  text: {head}")
    for sql_id, child, ctype, execs, elapsed, head in rows[:2]:
        cur.execute(
            "SELECT plan_table_output FROM "
            "TABLE(DBMS_XPLAN.DISPLAY_CURSOR(:sid, :child, 'TYPICAL'))",
            sid=sql_id, child=child,
        )
        print(f"\n-- DISPLAY_CURSOR {sql_id}/{child} (elapsed {elapsed}s) --")
        for (line,) in cur.fetchall():
            print(line)


def reorder(stmts: list[str]) -> list[str]:
    """Move the current_* GATHER_TABLE_STATS to right after the
    current_* refresh pair. Same statement multiset, new order only."""
    refreshes = [s for s in stmts if "DBMS_MVIEW.REFRESH" in s]
    gathers = [s for s in stmts if "GATHER_TABLE_STATS" in s]
    assert len(refreshes) + len(gathers) == len(stmts), "unclassified stmt"
    early_keys = (f"'{PREFIX}_current_transactions'",
                  f"'{PREFIX}_current_daily_balances'")
    early = [g for g in gathers if any(k in g for k in early_keys)]
    late = [g for g in gathers if g not in early]
    assert len(early) == 2, early
    out: list[str] = []
    for r in refreshes:
        out.append(r)
        if f"'{PREFIX}_current_daily_balances'" in r:
            out.extend(early)
    out.extend(late)
    assert sorted(out) == sorted(stmts), "reorder changed the statement set"
    return out


def stmt_head(s: str) -> str:
    return " ".join(s.split())[:100]


def run(mode: str) -> None:
    full = mode.startswith("full")
    do_reorder = "reordered" in mode
    cells_full, day_opts = P.build_domain(P.BAL_OPTS, P.LEG_ATOMS)
    cells = cells_full if full else cells_full[::4]
    print(f"[{mode}] day_opts={len(day_opts)} cells={len(cells)}")
    tx_rows, db_rows = P.build_rows(cells, "en")
    n_rows = len(tx_rows) + len(db_rows)
    print(f"rows: tx={len(tx_rows)} bal={len(db_rows)} total={n_rows}")

    instance = load_instance(P.SPEC)
    conn = oracledb.connect(oracle_dsn(URL))
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

    dump_stats_state(cur, "post-seed, pre-refresh")

    refresh_sql = refresh_matviews_sql(instance, prefix=PREFIX, dialect=DIALECT)
    stmts = split_oracle_script(refresh_sql)
    ordered = reorder(stmts) if do_reorder else stmts
    if "base" in mode:
        # Complete-fix probe: base-table stats BEFORE any refresh, so the
        # current_* supersession refreshes (which read the base tables)
        # plan with real cardinalities + index stats too.
        ordered = [
            f"BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, '{PREFIX}_transactions'); END;",
            f"BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, '{PREFIX}_daily_balances'); END;",
        ] + ordered
    print(f"\nrefresh script: {len(ordered)} stmts "
          f"({'REORDERED' if do_reorder else 'UNMODIFIED emitted order'}"
          f"{' + base-table stats first' if 'base' in mode else ''})")

    csb_refresh_key = f"DBMS_MVIEW.REFRESH('{PREFIX}_computed_subledger_balance'"
    cdb_refresh_key = f"DBMS_MVIEW.REFRESH('{PREFIX}_current_daily_balances'"

    per: list[tuple[float, str]] = []
    t0 = time.perf_counter()
    for s in ordered:
        if cdb_refresh_key in s:
            explain(cur, "cdb defining query, state at its refresh", CDB_SELECT)
        if csb_refresh_key in s:
            dump_stats_state(cur, "state at csb refresh")
            explain(cur, "csb defining query, state at its refresh", CSB_SELECT)
        s0 = time.perf_counter()
        cur.execute(s)
        cost = time.perf_counter() - s0
        per.append((cost, stmt_head(s)))
        if cdb_refresh_key in s:
            real_cursor_plans(cur, f"{PREFIX}_current_daily_balances",
                              "actual refresh cursor")
        if csb_refresh_key in s:
            real_cursor_plans(cur, f"{PREFIX}_computed_subledger_balance",
                              "actual refresh cursor")
    conn.commit()
    t_ref = time.perf_counter() - t0
    print(f"\nrefresh matviews TOTAL: {t_ref:.2f}s ({len(ordered)} stmts)")
    print("\n== per-statement timing (execution order) ==")
    for cost, head in per:
        print(f"  {cost:8.2f}s  {head}")

    # violation-set agreement vs pure-python residuals
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
    print(f"\n[{mode}] refresh_total={t_ref:.2f}s  EXIT OK")


if __name__ == "__main__":
    run(sys.argv[1])
