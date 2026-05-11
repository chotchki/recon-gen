"""Y.6 perf measurement — direct dataset SQL timing on Aurora PG.

Walks every dataset registered by the four bundled apps, executes its
SQL with default parameter values against the seeded database, captures
per-query rows + duration. Writes a markdown summary.

Run on post-Y first (current main), then stash + checkout pre-Y commit
(0417eca), re-run, diff. The Phase Y headline: % reduction in
rows-on-the-wire per dataset.

Usage:
    .venv/bin/python spike/y6/measure.py --label post-y -o runs/y6/post-y.md

The SQL we execute is the App2 registered variant (has :date_from /
:date_to binds + the same <<$paramName>> substitutions QS uses). We
substitute defaults via the existing _sql_executor pipeline so the
output mirrors what App2 would actually run.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import psycopg

# Project imports
from quicksight_gen.common.config import Config
from quicksight_gen.common.sql import Dialect
from quicksight_gen.common.dataset_contract import get_sql, get_dataset_params
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.html._sql_executor import execute_visual_sql

# Apps — importing each registers its datasets + SQL via build_*_app(cfg)
from quicksight_gen.apps.l1_dashboard.app import build_l1_dashboard_app
from quicksight_gen.apps.l2_flow_tracing.app import build_l2_flow_tracing_app
from quicksight_gen.apps.executives.app import build_executives_app
from quicksight_gen.apps.investigation.app import build_investigation_app


L2_YAML = Path("tests/l2/sasquatch_pr.yaml")
DB_URL = os.environ.get(
    "QS_GEN_DEMO_DATABASE_URL",
    "postgresql://postgres:itGKQHRaSocmIwEReXyZ@"
    "database-2.cluster-cup0y2gmc2hu.us-east-1.rds.amazonaws.com:5432/postgres",
)


def _make_cfg() -> Config:
    """Build a Config that points at Aurora PG with sasquatch_pr prefix.

    Datasource ARN is faked (we never deploy to AWS — only run SQL
    against the DB directly).
    """
    return Config(
        aws_account_id="470656905821",
        aws_region="us-east-1",
        dialect=Dialect.POSTGRES,
        demo_database_url=DB_URL,
        datasource_arn=(
            "arn:aws:quicksight:us-east-1:470656905821:datasource/"
            "qs-gen-postgres-sasquatch_pr-postgres-demo"
        ),
        l2_instance_prefix="sasquatch_pr",
    )


def _build_all_apps() -> list[tuple[str, object]]:
    """Construct all four apps so their datasets register SQL.

    Each app's `build_all_datasets()` MUST be called before the App
    tree is constructed — that's what registers the SQL strings used by
    `get_sql(visual_identifier)`. The L1 + L2FT app builders do this
    internally; Investigation + Executives expect the CLI driver to
    call it (see cli/_app_builders.py::_generate_investigation), so we
    mirror that here.

    Returns a list of (app_name, app) for the iteration step.
    """
    cfg = _make_cfg()
    l2 = load_instance(L2_YAML)

    # Inv + Exec need explicit dataset registration (CLI does this).
    from quicksight_gen.apps.investigation.datasets import (
        build_all_datasets as _inv_ds,
    )
    from quicksight_gen.apps.executives.datasets import (
        build_all_datasets as _exec_ds,
    )
    _inv_ds(cfg, l2)
    _exec_ds(cfg)

    return [
        ("L1", build_l1_dashboard_app(cfg, l2_instance=l2)),
        ("L2FT", build_l2_flow_tracing_app(cfg, l2_instance=l2)),
        ("Inv", build_investigation_app(cfg, l2_instance=l2)),
        ("Exec", build_executives_app(cfg, l2_instance=l2)),
    ]


def _connection_factory():
    return psycopg.connect(DB_URL, connect_timeout=15)


def _measure_dataset(
    visual_identifier: str,
    url_params: dict | None = None,
) -> tuple[int, float, str | None]:
    """Run the registered SQL for ``visual_identifier`` once.

    ``url_params``: dict[str, list[str]] of URL-style filter values.
    None / empty triggers dataset-param defaults (fresh-page-load
    behavior — wide open, no narrowing).

    Returns (rows_returned, duration_seconds, error_or_None).
    """
    try:
        sql = get_sql(visual_identifier)
        params = get_dataset_params(visual_identifier)
    except KeyError as e:
        return (0, 0.0, f"no SQL registered: {e}")

    t0 = time.perf_counter()
    try:
        rows, _cols = execute_visual_sql(
            _connection_factory,
            sql,
            url_params=url_params or {},
            dialect=Dialect.POSTGRES,
            dataset_parameters=params,
        )
    except Exception as e:
        return (0, time.perf_counter() - t0, f"{type(e).__name__}: {e}")
    duration = time.perf_counter() - t0
    return (len(rows), duration, None)


def _scenario_params(scenario: str) -> dict[str, list[str]]:
    """Build url_params for a named measurement scenario.

    - 'wide-open': no params; dataset-param defaults (sentinel matches)
      apply. Mimics pre-Y behavior (and post-Y on first dashboard load).
    - 'narrow-7d': :date_from = today-7d, :date_to = today. Demonstrates
      Y's date pushdown win for analyst-narrowed views.
    """
    from datetime import date, timedelta
    if scenario == "wide-open":
        return {}
    if scenario == "narrow-7d":
        today = date.today()
        return {
            "date_from": [(today - timedelta(days=7)).isoformat()],
            "date_to": [today.isoformat()],
        }
    raise ValueError(f"unknown scenario: {scenario}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Y.6 perf measurement")
    parser.add_argument("--label", required=True, help="e.g. post-y / pre-y")
    parser.add_argument("-o", "--output", required=True, help="output md path")
    parser.add_argument(
        "--scenario", default="wide-open",
        choices=["wide-open", "narrow-7d"],
        help=(
            "wide-open = no params (sentinel defaults; pre-Y-equivalent "
            "rows fetched). narrow-7d = date_from/date_to set to last "
            "week (Y's date pushdown narrows at DB)."
        ),
    )
    parser.add_argument(
        "--repeat", type=int, default=3,
        help="run each dataset N times; report median (default 3)",
    )
    args = parser.parse_args()

    url_params = _scenario_params(args.scenario)
    apps = _build_all_apps()
    out: list[str] = []
    out.append(f"# Y.6 perf measurement — {args.label}\n")
    out.append(f"DB: `{DB_URL.split('@')[-1]}`  ")
    out.append(f"Scenario: `{args.scenario}` — params: `{url_params or '(empty / sentinel defaults)'}`  ")
    out.append(f"Repeats per dataset: {args.repeat} (median reported)\n")

    grand_total_rows = 0
    grand_total_ms = 0.0
    skipped_count = 0

    for app_name, app in apps:
        out.append(f"\n## {app_name}\n")
        out.append("| Dataset | Rows | Median ms | Notes |")
        out.append("|---|---:|---:|---|")
        for ds_node in sorted(app.datasets, key=lambda d: d.identifier):
            vid = str(ds_node.identifier)
            samples = []
            error = None
            rows = 0
            for _ in range(args.repeat):
                r, d, e = _measure_dataset(vid, url_params=url_params)
                if e:
                    error = e
                    break
                rows = r
                samples.append(d * 1000)  # ms
            if error:
                out.append(f"| `{vid}` | — | — | ⚠ {error} |")
                skipped_count += 1
                continue
            samples.sort()
            median_ms = samples[len(samples) // 2]
            grand_total_rows += rows
            grand_total_ms += median_ms
            note = ""
            if rows >= 50000:
                note = "⚠ large"
            out.append(
                f"| `{vid}` | {rows:,} | {median_ms:,.1f} | {note} |"
            )
            print(f"  {vid}: {rows:,} rows, {median_ms:.1f} ms")

    out.append(f"\n## Totals\n")
    out.append(f"- **Total rows fetched**: {grand_total_rows:,}")
    out.append(f"- **Total time**: {grand_total_ms:,.1f} ms ({grand_total_ms/1000:.2f}s)")
    out.append(f"- **Datasets measured**: {sum(1 for ln in out if ln.startswith('| `'))}")
    out.append(f"- **Skipped (errors)**: {skipped_count}")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text("\n".join(out))
    print(f"\nwrote {args.output}")
    print(f"grand-total rows={grand_total_rows:,} time={grand_total_ms:,.1f}ms")


if __name__ == "__main__":
    main()
