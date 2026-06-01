"""SQLite matview-refresh benchmark harness for spike BX.

Measures `refresh_matviews_sql` wallclock on SQLite at varying base
transaction-row counts. Uses the production emitters unmodified — no
patches to `src/`. Per-matview timing is achieved by parsing the bundled
refresh SQL into statements and re-grouping them by target matview name
(every CREATE / CREATE INDEX / ANALYZE that mentions matview X gets
billed to X; the leading DROP block is billed proportionally to each
matview by name match).

Usage:
    .venv/bin/python -m spike.bx_sqlite_matview_perf.benchmark --rows 50000
    .venv/bin/python -m spike.bx_sqlite_matview_perf.benchmark --rows 250000
    .venv/bin/python -m spike.bx_sqlite_matview_perf.benchmark --rows 1000000

Outputs:
    spike/bx_sqlite_matview_perf/results_<rows>.md  — markdown table
    stdout — same table + provenance

Not production code; lives under spike/ per BX charter.
"""

from __future__ import annotations

import argparse
import gc
import re
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterable
from datetime import date
from pathlib import Path

# Resolve the repo root so the spike script runs from anywhere.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))

from recon_gen.common.config import Config  # noqa: E402
from recon_gen.common.db import (  # noqa: E402
    _register_sqlite_aggregates,
    _split_sqlite_statements,
)
from recon_gen.common.l2 import load_instance  # noqa: E402
from recon_gen.common.l2.schema import (  # noqa: E402
    emit_schema,
    refresh_matviews_sql,
)
from recon_gen.common.sql.dialect import Dialect  # noqa: E402


# Per-matview order in `refresh_matviews_sql` (SQLite arm). The order
# also encodes the dependency chain — leaves first, then helpers, then
# L1 invariants, then dashboard-shape, then Inv.
MATVIEW_NAMES_ORDERED: tuple[str, ...] = (
    "current_transactions",
    "current_daily_balances",
    "computed_subledger_balance",
    "computed_ledger_balance",
    "drift",
    "ledger_drift",
    "overdraft",
    "expected_eod_balance_breach",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "chain_parent_disagreement",
    "xor_group_violation",
    "transfer_parents",
    "fan_in_disagreement",
    "multi_xor_violation",
    "daily_statement_summary",
    "l1_exceptions",
    "inv_pair_rolling_anomalies",
    "inv_money_trail_edges",
)


def _make_cfg(*, db_path: Path, prefix: str) -> Config:
    """Build a minimal SQLite Config that the production library accepts."""
    return Config(  # type: ignore[call-arg]
        aws_account_id="111122223333",
        aws_region="us-east-1",
        dialect=Dialect.SQLITE,
        demo_database_url=f"sqlite:///{db_path}",
        deployment_name="bxspike",
        db_table_prefix=prefix,
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    # Speed knobs the production path also uses. WAL is the default
    # journaling mode for offline iteration; the production path
    # doesn't pin it. Leave SQLite defaults so the measurement
    # reflects what the integrator sees.
    _register_sqlite_aggregates(conn)
    return conn


def _execute_script(conn: sqlite3.Connection, sql: str) -> None:
    """Apply a multi-statement SQL script (DDL + DML)."""
    cur = conn.cursor()
    for stmt in _split_sqlite_statements(sql):
        cur.execute(stmt)
    conn.commit()


def _assign_statement_to_matview(stmt: str, prefix: str) -> str | None:
    """Return the matview short name (e.g. ``drift``) the statement
    operates on, or None if it can't be cleanly attributed.

    Matching strategy: scan the statement body for the FIRST occurrence
    of any known matview short name (prefixed with ``<prefix>_``) and
    bill the whole statement to that matview. Index CREATEs name the
    target matview in ``ON <prefix>_<name>``; ANALYZE names it as the
    bare table; DROPs name it directly; CREATE TABLE ... AS names it
    as the new table. ``inv_money_trail_edges`` walks a WITH RECURSIVE
    over the base ``<prefix>_transactions``; the leading CREATE is
    still the matview itself, so the head match still wins.
    """
    # Match the first <prefix>_<known-matview> token in the statement.
    # Longest-name first so e.g. ``daily_statement_summary`` isn't
    # matched as ``daily_balances`` of current_daily_balances.
    names_longest_first = sorted(MATVIEW_NAMES_ORDERED, key=len, reverse=True)
    pattern = re.compile(
        r"\b" + re.escape(prefix) + r"_(" + "|".join(
            re.escape(n) for n in names_longest_first
        ) + r")\b"
    )
    m = pattern.search(stmt)
    if m is None:
        return None
    return m.group(1)


def _group_refresh_statements_by_matview(
    refresh_sql: str, prefix: str,
) -> dict[str, list[str]]:
    """Parse the bundled refresh SQL into per-matview statement groups.

    Returns a mapping matview-name → list of statements. The order
    within each group preserves the source order (drops before creates
    before indexes before ANALYZE; the production emitter already
    interleaves these correctly so we just preserve appearance order).

    Statements that can't be attributed (e.g. a bare standalone
    comment that the splitter let through) land in the ``__unattributed__``
    bucket and are logged. Index creates that mention multiple
    matview names (none today, but defensive) bill to the first.
    """
    groups: dict[str, list[str]] = {name: [] for name in MATVIEW_NAMES_ORDERED}
    groups["__unattributed__"] = []
    for stmt in _split_sqlite_statements(refresh_sql):
        name = _assign_statement_to_matview(stmt, prefix)
        if name is None:
            groups["__unattributed__"].append(stmt)
        else:
            groups[name].append(stmt)
    return groups


def _time_group(
    conn: sqlite3.Connection, statements: Iterable[str],
) -> float:
    """Execute the statements as one logical unit; return wallclock ms."""
    cur = conn.cursor()
    t0 = time.perf_counter()
    for stmt in statements:
        cur.execute(stmt)
    conn.commit()
    return (time.perf_counter() - t0) * 1000.0


def _matview_row_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return None


def _build_full_seed_with_window(
    cfg: Config, instance: object, *,
    density: float, window_days: int, anchor: date,
) -> str:
    """Mirror of `cli._helpers.build_full_seed_sql` that threads
    `baseline_window_days` through to `emit_full_seed`. The production
    helper hardcodes the default 90; we need the lever for scale.
    """
    from recon_gen.cli._helpers import build_default_scenario  # noqa: E402
    from recon_gen.common.l2.seed import emit_full_seed  # noqa: E402

    final = build_default_scenario(
        instance, anchor=anchor, density=density,
    )
    return emit_full_seed(
        instance,
        final,
        prefix=cfg.db_table_prefix,
        dialect=cfg.dialect,
        anchor=anchor,
        baseline_window_days=window_days,
    )


def _scale_for_target_rows(target_rows: int) -> tuple[float, int]:
    """Return ``(density, baseline_window_days)`` to hit ``target_rows``.

    Calibrated against sasquatch_pr at density=1.0 + 90-day baseline →
    ~127k transaction rows. The 90-day baseline dominates (~120k
    rows); the densify multiplier scales plants only — at density 5×
    plants are still only ~7k of the 127k total.

    So the right scale lever for the integrator-sized data is
    ``baseline_window_days``. Linear in window: at ~1,400 rows/day
    (127k / 90), 250k rows ≈ 180 days, 1M rows ≈ 720 days.

    Density is kept at 1.0 to preserve plant-row proportions. Returns
    ``(1.0, days)`` where ``days = ceil(target / 1400)`` clipped to a
    minimum of 90 (the production default; sub-90 day windows haven't
    been exercised by the seed pipeline).
    """
    ROWS_PER_DAY = 127_500 / 90
    days = max(90, int(target_rows / ROWS_PER_DAY))
    return 1.0, days


def benchmark(target_rows: int, l2_yaml_path: Path, prefix: str) -> str:
    """Run one scale of the benchmark. Returns the markdown report."""
    instance = load_instance(l2_yaml_path)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"bx-spike-{target_rows}-"))
    db_path = tmp_dir / "demo.sqlite"
    print(f"[BX] tmp_dir = {tmp_dir}", flush=True)
    print(f"[BX] target rows = {target_rows:,}", flush=True)

    cfg = _make_cfg(db_path=db_path, prefix=prefix)

    # Step 1: schema apply (DDL only — no <prefix>_config row, since
    # the matview bodies reference v_config_* views which need the kv
    # row populated before they're queryable).
    print(f"[BX] emitting schema DDL ...", flush=True)
    schema_sql = emit_schema(
        instance, prefix=prefix, dialect=Dialect.SQLITE,
    )

    # We also need to populate <prefix>_config_kv from the L2 yaml +
    # cfg so the v_config_* views resolve. Reuse the helper.
    from recon_gen.cli._helpers import (  # noqa: E402
        build_config_populate_sql,
    )
    populate_sql = build_config_populate_sql(
        cfg, instance, anchor=date(2030, 1, 1),
    )

    conn = _connect(db_path)
    t0 = time.perf_counter()
    _execute_script(conn, schema_sql)
    _execute_script(conn, populate_sql)
    schema_ms = (time.perf_counter() - t0) * 1000.0
    print(f"[BX] schema + config populate: {schema_ms:.0f} ms", flush=True)

    # Step 2: seed at scaled window (density stays at 1.0 — see
    # `_scale_for_target_rows`).
    density, window_days = _scale_for_target_rows(target_rows)
    print(
        f"[BX] emitting seed (density={density:.2f}, "
        f"window_days={window_days}) ...", flush=True,
    )
    t0 = time.perf_counter()
    seed_sql = _build_full_seed_with_window(
        cfg, instance, density=density,
        window_days=window_days, anchor=date(2030, 1, 1),
    )
    emit_ms = (time.perf_counter() - t0) * 1000.0
    print(
        f"[BX] seed emit: {emit_ms:.0f} ms; SQL size {len(seed_sql)/1e6:.1f} MB",
        flush=True,
    )

    t0 = time.perf_counter()
    _execute_script(conn, seed_sql)
    seed_apply_ms = (time.perf_counter() - t0) * 1000.0
    print(f"[BX] seed apply: {seed_apply_ms:.0f} ms", flush=True)

    base_tx_rows = _matview_row_count(conn, f"{prefix}_transactions") or 0
    base_db_rows = _matview_row_count(conn, f"{prefix}_daily_balances") or 0
    print(
        f"[BX] base rows: transactions={base_tx_rows:,} "
        f"daily_balances={base_db_rows:,}", flush=True,
    )

    # Step 3: emit refresh SQL and split into per-matview groups.
    refresh_sql = refresh_matviews_sql(
        instance, prefix=prefix, dialect=Dialect.SQLITE,
    )
    groups = _group_refresh_statements_by_matview(refresh_sql, prefix)

    # First, run a bundled refresh once to record the total
    # wallclock — this is the integrator-visible "data refresh"
    # latency.
    print(f"[BX] running bundled refresh (cold, integrator-visible cost)", flush=True)
    gc.collect()
    t0 = time.perf_counter()
    _execute_script(conn, refresh_sql)
    total_bundled_ms = (time.perf_counter() - t0) * 1000.0
    print(f"[BX] bundled refresh wallclock: {total_bundled_ms:.0f} ms", flush=True)

    # Per-matview hot-spot measurement: with all matviews already
    # built by the bundled refresh, drop JUST the one matview we're
    # measuring and re-run its group. This reflects "selective
    # refresh" cost — what would it take if we only had to rebuild
    # this single matview, with everything else cache-warm + already
    # populated. Useful for identifying WHICH matview dominates the
    # bundled cost.
    print(f"[BX] running selective per-matview rebuilds (hot-spot map)", flush=True)
    per_matview: list[dict[str, object]] = []
    for short_name in MATVIEW_NAMES_ORDERED:
        full = f"{prefix}_{short_name}"
        stmts = groups[short_name]
        if not stmts:
            per_matview.append({
                "name": short_name,
                "input_rows": "—",
                "output_rows": "—",
                "wallclock_ms": 0.0,
            })
            continue
        # Drop only this matview; its statements include its own
        # DROP IF EXISTS so we just need to make sure dependent
        # downstream matviews are present (they are — we just ran
        # the bundled refresh).
        gc.collect()
        ms = _time_group(conn, stmts)
        out_rows = _matview_row_count(conn, full)
        per_matview.append({
            "name": short_name,
            "input_rows": base_tx_rows,
            "output_rows": out_rows if out_rows is not None else "—",
            "wallclock_ms": ms,
        })
        print(
            f"[BX]   {short_name:<32} {ms:>10.1f} ms  -> "
            f"{out_rows if out_rows is not None else '—'} rows",
            flush=True,
        )

    if groups["__unattributed__"]:
        print(
            f"[BX] WARNING: {len(groups['__unattributed__'])} unattributed "
            f"statements (first 1 head): "
            f"{groups['__unattributed__'][0][:120]!r}", flush=True,
        )

    conn.close()
    db_size_bytes = db_path.stat().st_size

    # Markdown report
    lines: list[str] = []
    lines.append(f"# BX SQLite matview perf — base_tx_rows={base_tx_rows:,}\n")
    lines.append(f"- L2 yaml: `{l2_yaml_path.relative_to(_REPO)}`")
    lines.append(f"- density factor: {density:.2f}x default")
    lines.append(f"- base `<prefix>_transactions` rows: {base_tx_rows:,}")
    lines.append(f"- base `<prefix>_daily_balances` rows: {base_db_rows:,}")
    lines.append(f"- seed apply wallclock: {seed_apply_ms:.0f} ms")
    lines.append(
        f"- **bundled refresh wallclock (cold; integrator-visible): "
        f"{total_bundled_ms:.0f} ms**"
    )
    sum_per = sum(float(row["wallclock_ms"]) for row in per_matview)
    lines.append(
        f"- per-matview selective-rebuild total (warm; hot-spot proxy): "
        f"{sum_per:.0f} ms"
    )
    lines.append(
        f"  - per-matview numbers are *selective rebuilds* against a "
        f"pre-built matview graph — they identify which matview's CREATE "
        f"AS SELECT dominates, but they don't sum to the bundled cold "
        f"cost (page cache + index-rebuild cascade is in the cold delta)."
    )
    lines.append(f"- SQLite DB file size: {db_size_bytes/1e6:.1f} MB")
    lines.append("")
    lines.append(
        "| matview | input rows | output rows | refresh wallclock (ms) |"
    )
    lines.append(
        "| --- | ---: | ---: | ---: |"
    )
    for row in per_matview:
        lines.append(
            f"| `{row['name']}` | {row['input_rows']:,} "
            f"| {row['output_rows']} | {row['wallclock_ms']:.1f} |"
            if isinstance(row['input_rows'], int)
            else f"| `{row['name']}` | {row['input_rows']} "
            f"| {row['output_rows']} | {row['wallclock_ms']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows", type=int, required=True,
        help="Target base <prefix>_transactions row count (50000 / 250000 / 1000000).",
    )
    parser.add_argument(
        "--l2", type=Path,
        default=_REPO / "tests" / "l2" / "sasquatch_pr.yaml",
        help="L2 yaml to drive schema + seed shape.",
    )
    parser.add_argument(
        "--prefix", type=str, default="bxspike",
        help="DB table prefix (default: bxspike).",
    )
    parser.add_argument(
        "--out", type=Path,
        default=None,
        help="Output markdown path (default: spike/bx_sqlite_matview_perf/results_<rows>.md).",
    )
    args = parser.parse_args()

    out_path = args.out or (
        _REPO / "spike" / "bx_sqlite_matview_perf" /
        f"results_target_{args.rows}.md"
    )
    report = benchmark(args.rows, args.l2, args.prefix)
    out_path.write_text(report)
    print(f"\n[BX] wrote {out_path}", flush=True)
    print()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
