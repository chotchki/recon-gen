"""App Info ("i") sheet — diagnostic canary on every shipped dashboard.

Every L3 dashboard's last sheet is named "i" (App Info). It carries
three things:

1. **Liveness KPI** — counts user-visible tables (Postgres:
   ``information_schema.tables`` filtered to ``public``; Oracle:
   ``USER_TABLES``). Real query, hits the database, never QS-cached
   (Direct Query). KPI shows a number → QS rendering pipeline
   works. KPI blank → QS itself is broken.
2. **Per-matview row count table** — caller-supplied list of matview
   names UNION'd into one dataset. Freshly-loaded matviews showing 0
   means the ETL hasn't refreshed them.
3. **Deploy stamp text box** — git short SHA + ISO timestamp baked
   at generate time so a viewer can tell which build of the dashboard
   they're looking at.

Diagnostic value: collapses the QS spinner-footgun ladder (Aurora
returns rows → describe_data_set CREATION_SUCCESSFUL → fresh incognito
→ assume QS broken; CLAUDE.md ops footgun) to a single glance at "i".

Usage from an app's `build_*_app(cfg, ...)`:

```python
from quicksight_gen.common.sheets.app_info import (
    APP_INFO_SHEET_NAME, APP_INFO_SHEET_TITLE, APP_INFO_SHEET_DESCRIPTION,
    DS_APP_INFO_LIVENESS, DS_APP_INFO_MATVIEWS,
    build_liveness_dataset, build_matview_status_dataset,
    populate_app_info_sheet,
)

# In _l1_datasets (or equivalent):
liveness_aws = build_liveness_dataset(cfg, app_segment="l1")
matviews_aws = build_matview_status_dataset(
    cfg, app_segment="l1",
    view_specs=[
        (f"{l2_prefix}_drift", "business_day_end"),
        (f"{l2_prefix}_overdraft", "business_day_end"),
        ...,
    ],
)
liveness_ds = Dataset(identifier=DS_APP_INFO_LIVENESS,
                     arn=cfg.dataset_arn(liveness_aws.DataSetId))
matviews_ds = Dataset(identifier=DS_APP_INFO_MATVIEWS,
                     arn=cfg.dataset_arn(matviews_aws.DataSetId))

# As LAST sheet on the analysis:
app_info_sheet = analysis.add_sheet(Sheet(
    sheet_id=SheetId("<app>-sheet-app-info"),
    name=APP_INFO_SHEET_NAME,
    title=APP_INFO_SHEET_TITLE,
    description=APP_INFO_SHEET_DESCRIPTION,
))
populate_app_info_sheet(
    cfg, app_info_sheet,
    liveness_ds=liveness_ds, matview_status_ds=matviews_ds,
    theme=theme,
)
```
"""

from __future__ import annotations

import datetime as _dt
import subprocess

from quicksight_gen.common import rich_text as rt
from quicksight_gen.common.config import Config
from quicksight_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    build_dataset,
)
from quicksight_gen.common.models import DataSet
from quicksight_gen.common.l2 import ThemePreset
from quicksight_gen.common.sql import Dialect, dual_from
from quicksight_gen.common.tree.datasets import Dataset
from quicksight_gen.common.tree.structure import Sheet
from quicksight_gen.common.tree.text_boxes import TextBox


APP_INFO_SHEET_NAME = "Info"  # Renamed from "i" — testing whether QS hides single-char tab names
APP_INFO_SHEET_TITLE = "App Info"
APP_INFO_SHEET_DESCRIPTION = (
    "Diagnostic canary. The Liveness KPI runs a real query against "
    "the database — if it shows a number, the QuickSight rendering "
    "pipeline is healthy and any blank visual on another sheet "
    "indicates a data or SQL issue. If the KPI is blank, QuickSight "
    "itself is broken."
)


# Visual identifiers — same string used by every app, registered once
# per process via the contract registry's identity-equality check.
DS_APP_INFO_LIVENESS = "app-info-liveness-ds"
DS_APP_INFO_MATVIEWS = "app-info-matviews-ds"


# Module-level contract instances — must be the same object every time
# `build_dataset()` is called, otherwise the registry rejects the
# second call with a different-instance error. Module-level singletons
# satisfy that.
LIVENESS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("table_count", "INTEGER"),
])


def _liveness_sql(dialect: Dialect) -> str:
    """Trivial liveness query — counts user-visible tables.

    Postgres reads ``information_schema.tables`` filtered to the
    ``public`` schema (where the L2 schema emit lands by default).
    Oracle has no ``information_schema``; the equivalent is
    ``USER_TABLES`` (the connecting user's tables in the user's
    default schema, which is also where the L2 schema emit lands).
    SQLite has no ``information_schema`` either; the equivalent is
    the ``sqlite_master`` table (built-in, queryable via
    ``WHERE type='table'`` to filter out indexes/views).

    Either way the query is a one-row health check. The exact count
    isn't load-bearing — only that the query returns *something*
    proves the QS → datasource → DB round-trip works.
    """
    if dialect is Dialect.POSTGRES:
        return (
            "SELECT COUNT(*) AS table_count "
            "FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    if dialect is Dialect.SQLITE:
        return (
            "SELECT COUNT(*) AS table_count "
            "FROM sqlite_master "
            "WHERE type='table'"
        )
    return "SELECT COUNT(*) AS table_count FROM USER_TABLES"


MATVIEW_STATUS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("view_name", "STRING"),
    ColumnSpec("row_count", "INTEGER"),
    # V.3 — `latest_date` is MAX(<date_col>) for the row's table/matview.
    # Operators detect stale matviews by eye: if the base tables'
    # latest_date moves forward but a matview's stays behind, the
    # matview hasn't been refreshed since the last ETL load. NULL when
    # the caller passed no date column (matviews without a natural
    # date dimension, e.g. inv_money_trail_edges).
    ColumnSpec("latest_date", "DATETIME"),
])


# (table_or_view_name, date_column_or_None) — V.3 spec shape.
ViewSpec = tuple[str, str | None]


def _matview_status_sql(
    view_specs: list[ViewSpec], dialect: Dialect,
) -> str:
    """Build a UNION ALL query: one row per (table | matview) with its
    row count + most-recent date.

    Each spec is ``(name, date_col)``. When ``date_col`` is set, the
    row carries ``MAX(<date_col>) AS latest_date``; when None, the
    row carries ``NULL AS latest_date`` (for matviews without a
    natural date dimension).

    Empty ``view_specs`` returns a single placeholder row so the
    dataset always has rows — keeps the table from rendering blank
    on apps with zero monitored matviews (Executives today). The
    placeholder needs ``FROM dual`` on Oracle (constant SELECT
    requires a FROM clause); on Postgres it stays bare.

    No casts — the column types are pinned by
    ``MATVIEW_STATUS_CONTRACT``, so the literal-type inference is a
    no-op as far as QuickSight sees. Earlier ``::text`` / ``::integer``
    casts were Postgres-only syntax and silently broke the Oracle
    dataset (P.9c).
    """
    if not view_specs:
        return (
            "SELECT '(no matviews registered)' AS view_name, "
            f"0 AS row_count, NULL AS latest_date{dual_from(dialect)}"
        )
    parts = []
    for name, date_col in view_specs:
        date_expr = f"MAX({date_col})" if date_col else "NULL"
        parts.append(
            f"SELECT '{name}' AS view_name, "
            f"COUNT(*) AS row_count, "
            f"{date_expr} AS latest_date FROM {name}"
        )
    return "\nUNION ALL\n".join(parts)


def build_liveness_dataset(cfg: Config, *, app_segment: str) -> DataSet:
    """Trivial liveness query against the database catalog.

    Postgres queries ``information_schema.tables``; Oracle queries
    ``USER_TABLES``. Returns one row with the user-visible-table count.
    Per-dialect SQL resolved from ``cfg.dialect`` (P.9c — earlier
    versions hardcoded the Postgres SQL on both dialects, which
    silently broke the KPI on Oracle).

    ``app_segment``: short kebab-case tag identifying which app owns
    this Dataset (e.g., ``"l1"``, ``"exec"``, ``"inv"``, ``"l2ft"``).
    Becomes part of the AWS DataSetId so each app gets its own
    physical dataset and ``deploy <single-app>`` doesn't delete-then-
    create another app's App Info dataset out from under it (M.4.4.7).
    The visual_identifier (``DS_APP_INFO_LIVENESS``) stays shared
    because it's analysis-internal — every app's analysis JSON has
    its own ``DataSetIdentifierDeclaration`` mapping the same logical
    name to its own per-app ARN.
    """
    return build_dataset(
        cfg,
        cfg.prefixed(f"{app_segment}-app-info-liveness-dataset"),
        "App Info -- Liveness",  # ASCII-only — testing QS em-dash hypothesis
        "app-info-liveness",
        _liveness_sql(cfg.dialect),
        LIVENESS_CONTRACT,
        visual_identifier=DS_APP_INFO_LIVENESS,
    )


def build_matview_status_dataset(
    cfg: Config, *, app_segment: str, view_specs: list[ViewSpec],
) -> DataSet:
    """Per-matview row count + most-recent date table.

    ``view_specs`` is a list of ``(name, date_col)`` tuples — the
    fully-qualified matview/table names to monitor + the column the
    "most recent" timestamp comes from. Pass ``date_col=None`` for
    tables without a natural date dimension; the latest_date column
    will render NULL for that row.

    Caller decides which (matview, date_col) pairs matter for this
    app — typically the L1 invariant matviews + the base tables
    (``<prefix>_transactions``, ``<prefix>_daily_balances``) so the
    operator can spot stale matviews against fresh ETL loads at a
    glance on the App Info sheet.

    ``app_segment``: see ``build_liveness_dataset``.
    """
    return build_dataset(
        cfg,
        cfg.prefixed(f"{app_segment}-app-info-matviews-dataset"),
        "App Info -- Matview Status",  # ASCII-only
        "app-info-matviews",
        _matview_status_sql(view_specs, cfg.dialect),
        MATVIEW_STATUS_CONTRACT,
        visual_identifier=DS_APP_INFO_MATVIEWS,
    )


def _git_short_sha() -> str:
    """Best-effort git short SHA at generate time. Returns ``"unknown"``
    if not in a repo or git unavailable.

    Intentionally swallows errors — the deploy stamp is informational
    and shouldn't block dashboard generation if the build environment
    lacks git (e.g., a wheel install on a server without source)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return "unknown"


def _deploy_stamp() -> tuple[str, str, str]:
    """Return ``(quicksight_gen_version, git_short_sha, iso_timestamp)``
    baked at generate time. The version is the package's ``__version__``
    string so a viewer can spot a stale dashboard against a newer CLI
    (V.3.a — version-mismatch detection)."""
    from quicksight_gen import __version__
    return (
        __version__,
        _git_short_sha(),
        _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
    )


# Layout constants — match the L1 dashboard's grid scale (36-col grid).
_FULL = 36
_HALF = 18
_TABLE_HEIGHT = 12
_TEXT_HEIGHT = 6


def populate_app_info_sheet(
    cfg: Config,
    sheet: Sheet,
    *,
    liveness_ds: Dataset,
    matview_status_ds: Dataset,
    theme: ThemePreset,
) -> None:
    """Populate the "i" sheet with three visuals (KPI + table + text box).

    Caller is responsible for registering the datasets on the App and
    for adding ``sheet`` to the Analysis as the LAST sheet (this helper
    doesn't enforce position because ``analysis.add_sheet`` order is
    the position).
    """
    accent = theme.accent
    version, sha, ts = _deploy_stamp()
    dialect = cfg.dialect.value
    prefix = cfg.deployment_name

    # Row 1: liveness KPI (left half) + matview status table (right half).
    top = sheet.layout.row(height=_TABLE_HEIGHT)
    top.add_kpi(
        width=_HALF,
        title="Liveness",
        subtitle=(
            "Count of public-schema tables. Real query against the "
            "database via Direct Query -- if this shows a number, "
            "QuickSight's rendering pipeline is healthy. Blank means "
            "QuickSight itself is broken (not the data, not the SQL)."
        ),
        values=[liveness_ds["table_count"].sum()],
    )
    top.add_table(
        width=_HALF,
        title="Matview Status",
        subtitle=(
            "Row counts + most-recent date per matview (and base "
            "tables for comparison). Freshly-loaded matviews showing "
            "0 mean the ETL has not refreshed them yet. If a base "
            "table's `latest_date` moves past a matview's "
            "`latest_date`, the matview is stale relative to fresh "
            "ETL data — re-run `quicksight-gen data refresh --execute`."
        ),
        columns=[
            matview_status_ds["view_name"].dim(),
            matview_status_ds["row_count"].numerical(),
            matview_status_ds["latest_date"].date(),
        ],
    )

    # Row 2: deploy stamp text box.
    sheet.layout.row(height=_TEXT_HEIGHT).add_text_box(
        TextBox(
            text_box_id="app-info-deploy-stamp",
            content=rt.text_box(
                rt.subheading("Deploy Stamp", color=accent),
                rt.bullets([
                    f"quicksight-gen: v{version}",
                    f"git: {sha}",
                    f"generated: {ts}",
                    f"dialect: {dialect}",
                    f"prefix: {prefix}",
                ]),
            ),
        ),
        width=_FULL,
    )
