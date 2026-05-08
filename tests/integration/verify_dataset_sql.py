"""Dataset CustomSQL parse/execute smoke per dialect (P.9f.e).

Walks every emitted dataset across all 4 shipped apps for both shipped
L2 instances, substitutes ``<<$paramName>>`` QS placeholders with their
declared default values, wraps the query in
``SELECT * FROM (<sql>) sub WHERE 1=0``, and executes against the live
demo DB. SUCCESS = SQL parses + binds + plans (zero rows expected, the
``WHERE 1=0`` short-circuit). FAIL = the kind of error that would
otherwise only surface when QuickSight tries to render a visual.

Why this exists (P.9f.e): the existing `verify_demo_apply.py` runs
matview row counts (`SELECT COUNT(*) FROM <matview>`), and the schema
emit snapshot tests check DDL shape. Neither parses or executes the
DATASET CustomSQL — that surface is only exercised when QS renders a
visual. Result: PG-only SQL bugs (JSON_VALUE path concat, ORA-00923
syntax) only surfaced at browser-test time after a 30+ min deploy +
e2e cell, and the failure traceback pointed at a Playwright timeout
rather than the underlying SQL error. This script catches them in
seconds, against the same DB, with the actual database error in the
output.

Designed for the same use-shape as `verify_demo_apply.py`: CLI script,
not pytest-collected. Living under `tests/integration/` keeps it next
to the existing post-demo-apply verifier without coupling it to
pytest fixtures.

Usage::

    # postgres × spec_example (P.9f.e default)
    python tests/integration/verify_dataset_sql.py \\
        --config run/config.postgres.yaml \\
        --l2-instance tests/l2/spec_example.yaml

    # oracle × sasquatch_pr — the new gate that should have caught
    # P.9f.b + P.9f.c long before the browser-tests
    python tests/integration/verify_dataset_sql.py \\
        --config run/config.oracle.yaml \\
        --l2-instance tests/l2/sasquatch_pr.yaml

Exits 0 on success; exits 1 with per-failed-dataset preview + the DB
error message on any failure.

Pre-requisite: ``demo apply`` must have already run against the same
config + L2 (so the prefixed schema + matviews exist). Without that,
every dataset query fails with "table does not exist" and tells you
nothing about the SQL itself. The script doesn't auto-apply because
it should be runnable post-demo-apply without re-seeding.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from quicksight_gen.apps.executives.datasets import (
    build_all_datasets as build_exec_datasets,
)
from quicksight_gen.apps.investigation.datasets import (
    build_all_datasets as build_inv_datasets,
)
from quicksight_gen.apps.l1_dashboard.datasets import (
    build_all_l1_dashboard_datasets,
)
from quicksight_gen.apps.l2_flow_tracing.datasets import (
    build_all_l2_flow_tracing_datasets,
)
from quicksight_gen.common.config import load_config
from quicksight_gen.common.db import connect_demo_db
from quicksight_gen.common.l2 import load_instance
from quicksight_gen.common.models import (
    DataSet,
    DatasetParameter,
)
from quicksight_gen.common.sql import Dialect


# ---------------------------------------------------------------------------
# Parameter substitution
# ---------------------------------------------------------------------------
#
# QuickSight wire format: ``<<$paramName>>`` is replaced literally with
# the parameter's value before the SQL hits the database. For testing,
# we substitute the same way using each parameter's declared
# ``DefaultValues.StaticValues``.
#
# Per-type formatting:
# - SINGLE_VALUED string  → ``'value'``     (single-quoted)
# - MULTI_VALUED string   → ``'a','b','c'`` (comma-separated, used in IN)
# - SINGLE_VALUED int     → ``42``
# - MULTI_VALUED int      → ``1,2,3``
# - SINGLE_VALUED decimal → ``3.14``
# - MULTI_VALUED decimal  → ``1.1,2.2``
# - DateTime              → ``'2030-01-01T00:00:00'`` (treat as string)


def _format_value(value: Any) -> str:
    """SQL-literal-format a Python value (matches the same shape QS
    sends — single-quoted strings, bare numerics)."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    # String / datetime: wrap in single quotes and escape internal quotes.
    return "'" + str(value).replace("'", "''") + "'"


def _resolve_default(param: DatasetParameter) -> tuple[str, list[Any]] | None:
    """Return (param_name, default_static_values) or None if no default
    is declared. Walks the discriminated union to find which sub-type
    the DatasetParameter carries."""
    for sub in (
        param.StringDatasetParameter,
        param.IntegerDatasetParameter,
        param.DecimalDatasetParameter,
        param.DateTimeDatasetParameter,
    ):
        if sub is None:
            continue
        defaults = sub.DefaultValues
        if defaults is None or not defaults.StaticValues:
            return None
        return sub.Name, list(defaults.StaticValues)
    return None


def _substitute_qs_params(
    sql: str, params: list[DatasetParameter] | None,
) -> str:
    """Replace every ``<<$name>>`` placeholder with the parameter's
    default-value SQL literal.

    SINGLE_VALUED → one value (quoted if string).
    MULTI_VALUED  → comma-joined (used in ``IN (<<$x>>)`` patterns).

    Placeholders without a matching declared parameter are left
    unchanged — the database error will tell you which one. (Better
    than substituting a guess: a missing-parameter regression
    surfaces with the original placeholder visible.)
    """
    if not params:
        return sql
    for param in params:
        resolved = _resolve_default(param)
        if resolved is None:
            continue
        name, values = resolved
        replacement = ", ".join(_format_value(v) for v in values)
        # Match ``<<$name>>`` exactly — escape any regex special chars
        # in the name (parameters typically carry letter+digit names so
        # this is mostly a defensive measure).
        pattern = re.compile(re.escape(f"<<${name}>>"))
        sql = pattern.sub(replacement, sql)
    return sql


# ---------------------------------------------------------------------------
# Smoke wrapper
# ---------------------------------------------------------------------------
#
# ``SELECT * FROM (<sql>) sub WHERE 1=0`` parses + binds + plans on
# both Postgres and Oracle, returns zero rows, executes in the same
# planning-budget the real visual would. Cross-dialect identical — no
# need to branch on dialect for the wrapper itself.


def _wrap_smoke(sql: str) -> str:
    """Wrap a CustomSQL in a ``WHERE 1=0`` envelope so it parses +
    binds without returning data."""
    return f"SELECT * FROM (\n{sql}\n) sub WHERE 1=0"


# ---------------------------------------------------------------------------
# Per-dataset smoke
# ---------------------------------------------------------------------------


def _custom_sql(ds: DataSet) -> tuple[str, str | None]:
    """Pull the CustomSQL out of a DataSet's PhysicalTableMap.

    Every dataset built by `build_dataset()` has exactly one entry in
    PhysicalTableMap with a CustomSql (vs RelationalTable / S3Source).
    Returns (sql_query, table_key) — table_key surfaces in the failure
    message so a triager can find which dataset's builder emitted it.
    """
    for table_key, physical in ds.PhysicalTableMap.items():
        if physical.CustomSql is not None:
            return physical.CustomSql.SqlQuery, table_key
    raise AssertionError(
        f"Dataset {ds.DataSetId!r} has no CustomSql in PhysicalTableMap "
        f"— this verifier only handles CustomSql datasets."
    )


def _smoke_one(
    conn: Any, ds: DataSet, *, verbose: bool = False,
) -> tuple[bool, str]:
    """Smoke-test one dataset. Returns (success, message).

    Rolls back the connection's transaction on any error so subsequent
    dataset checks can run cleanly. Without this, Postgres aborts the
    transaction on the first SQL error and rejects every following
    statement with ``InFailedSqlTransaction`` — masking real per-
    dataset bugs behind a cascade of bookkeeping errors.
    """
    raw_sql, table_key = _custom_sql(ds)
    sub_sql = _substitute_qs_params(raw_sql, ds.DatasetParameters)
    smoke_sql = _wrap_smoke(sub_sql)
    try:
        with conn.cursor() as cur:
            cur.execute(smoke_sql)
    except Exception as e:  # noqa: BLE001 — capture every DB error class
        # Rollback so the next dataset's query runs against a clean
        # transaction state. Both psycopg2 + oracledb support rollback
        # via the connection-level method.
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — best-effort
            pass
        # First 800 chars of the wrapped SQL is enough for triage; the
        # database error message + dataset id pinpoints the rest.
        preview = smoke_sql[:800]
        return False, (
            f"  {ds.DataSetId} ({table_key}): {type(e).__name__}\n"
            f"    {e}\n"
            f"    SQL preview:\n      "
            + preview.replace("\n", "\n      ")
        )
    if verbose:
        return True, f"  {ds.DataSetId} ({table_key}): OK"
    return True, ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True,
        help="Path to a config YAML (e.g. run/config.postgres.yaml).",
    )
    parser.add_argument(
        "--l2-instance", required=True,
        help="Path to an L2 instance YAML "
             "(e.g. tests/l2/spec_example.yaml).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print every dataset's pass status, not just failures.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    l2 = load_instance(Path(args.l2_instance))
    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2.instance))

    # Build every app's datasets. Each app's bundle is independent so
    # we just chain them. L1 / L2FT / Inv all thread the L2 instance;
    # Exec is the only one that takes only ``cfg`` (its App Info matview
    # list is empty — Exec reads only base tables).
    all_datasets: list[DataSet] = (
        build_all_l1_dashboard_datasets(cfg, l2)
        + build_all_l2_flow_tracing_datasets(cfg, l2)
        + build_inv_datasets(cfg, l2)
        + build_exec_datasets(cfg)
    )

    print(
        f"Connecting to {cfg.demo_database_url.split('@')[-1]} "  # type: ignore[union-attr]: demo_database_url is required for this integration script
        f"({cfg.dialect.value}) — {len(all_datasets)} datasets to smoke."
    )
    conn = connect_demo_db(cfg)
    failures: list[str] = []
    passes = 0
    try:
        for ds in all_datasets:
            ok, msg = _smoke_one(conn, ds, verbose=args.verbose)
            if ok:
                passes += 1
                if msg:
                    print(msg)
            else:
                failures.append(msg)
                print(msg)
    finally:
        conn.close()

    total = len(all_datasets)
    print()
    if failures:
        print(
            f"FAIL: {len(failures)}/{total} datasets failed to "
            f"parse/execute against {cfg.dialect.value}."
        )
        return 1
    print(
        f"OK: all {total} datasets parsed + executed cleanly "
        f"against {cfg.dialect.value}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
