#!/usr/bin/env python3
"""Dump the top-N most expensive queries hitting the test schema.

W.8a — perf-debug companion to the e2e suite. After tests run, this
queries the dialect's stats view to surface the slowest queries that
touched our L2 instance's tables, writes a markdown table to
``--output``, and the workflow uploads it as a CI artifact.

Y.2.gate.f.4 (2026-05-09): the implementation lives in
``quicksight_gen._dev.perf`` (helpers lifted there at W.8a so both this
CLI and the runner's per-cell auto-dump share the same code path). This
file is now the thin CLI shim — argparse + cfg load + connect + write.

Usage::

    .venv/bin/python scripts/dump_top_queries.py \\
        -c /tmp/ci-pg.yaml -o /tmp/top-queries.md --top 50

Output is best-effort. When the stats view is unavailable (extension
not installed, permission denied, dialect not yet supported), this
exits 0 with a "skipped" note in the markdown so a missing perf
snapshot never breaks CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicksight_gen._dev import perf  # noqa: E402
from quicksight_gen.common.config import load_config  # noqa: E402
from quicksight_gen.common.db import connect_demo_db  # noqa: E402
from quicksight_gen.common.sql import Dialect  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", required=True, type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument(
        "--like",
        default="spec_example",
        help=(
            "Filter queries whose text contains this substring "
            "(default: spec_example, the CI L2 instance prefix)."
        ),
    )
    parser.add_argument(
        "--top", type=int, default=50,
        help="Number of rows to dump (default: 50).",
    )
    parser.add_argument(
        "--title",
        default="Top expensive queries",
        help="Heading for the markdown output.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    dialect_str = perf.dialect_name(cfg.dialect)

    if cfg.dialect is Dialect.SQLITE:
        args.output.write_text(perf.format_skipped(
            title=args.title, dialect=dialect_str,
            reason="SQLite has no pg_stat_statements / v$sqlstats equivalent",
        ))
        return 0

    try:
        conn = connect_demo_db(cfg)
    except Exception as e:
        args.output.write_text(perf.format_skipped(
            title=args.title, dialect=dialect_str,
            reason=f"could not connect: {e!r}",
        ))
        print(f"[dump_top_queries] connect failed: {e!r}", file=sys.stderr)
        return 0

    try:
        try:
            rows = perf.fetch_top_queries(
                conn, cfg.dialect, like_pattern=args.like, top=args.top,
            )
        except Exception as e:
            args.output.write_text(perf.format_skipped(
                title=args.title, dialect=dialect_str,
                reason=(
                    f"stats view unavailable: {type(e).__name__}: {e}. "
                    f"Pre-req for postgres: ``CREATE EXTENSION "
                    f"pg_stat_statements;``. For oracle: SELECT on "
                    f"``v$sqlstats``."
                ),
            ))
            print(f"[dump_top_queries] query failed: {e!r}", file=sys.stderr)
            return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass

    try:
        args.output.write_text(perf.format_top_queries_markdown(
            title=args.title, dialect=dialect_str,
            like_pattern=args.like, rows=rows,
        ))
    except Exception as e:
        args.output.write_text(perf.format_skipped(
            title=args.title, dialect=dialect_str,
            reason=f"format failed: {type(e).__name__}: {e}",
        ))
        print(f"[dump_top_queries] format failed: {e!r}", file=sys.stderr)
        return 0
    print(
        f"[dump_top_queries] wrote {len(rows)} rows to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
