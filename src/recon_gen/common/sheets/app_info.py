"""App Info ("i") sheet — diagnostic canary on every shipped dashboard.

Every L3 dashboard's last sheet is named "i" (App Info). It carries
three things:

1. **Liveness KPI** — counts user-visible tables (Postgres:
   ``information_schema.tables`` filtered to ``public``; Oracle:
   ``USER_TABLES``). Real query, hits the database every time
   (Direct Query, no caching). KPI shows a number → the render
   pipeline works. KPI blank → the renderer itself is broken.
2. **Per-matview row count table** — caller-supplied list of matview
   names UNION'd into one dataset. Freshly-loaded matviews showing 0
   means the ETL hasn't refreshed them.
3. **Deploy stamp text box** — git short SHA + ISO timestamp baked
   at generate time so a viewer can tell which build of the dashboard
   they're looking at.

Diagnostic value: collapses the "did it render or is the DB empty?"
question to a single glance at "i" — a number means the renderer +
the data path are both healthy.

Usage from an app's `build_*_app(cfg, ...)`:

```python
from recon_gen.common.sheets.app_info import (
    APP_INFO_SHEET_NAME, APP_INFO_SHEET_TITLE, APP_INFO_SHEET_DESCRIPTION,
    app_info_liveness_id, app_info_matviews_id,
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
liveness_ds = Dataset(identifier=app_info_liveness_id("l1"))
matviews_ds = Dataset(identifier=app_info_matviews_id("l1"))

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
import importlib
import subprocess
from typing import cast

from recon_gen.common import rich_text as rt
from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import (
    ColumnSpec,
    DatasetContract,
    BuiltDataset,
    build_dataset,
)
from recon_gen.common.l2 import ThemePreset
from recon_gen.common.l2.primitives import L2Instance, resolve_cadence
from recon_gen.common.sql import Dialect, dual_from
from recon_gen.common.tree.datasets import Dataset
from recon_gen.common.tree.structure import Sheet
from recon_gen.common.tree.text_boxes import TextBox


APP_INFO_SHEET_NAME = "Info"  # Renamed from "i" — testing whether QS hides single-char tab names
APP_INFO_SHEET_TITLE = "App Info"
APP_INFO_SHEET_DESCRIPTION = (
    "Diagnostic canary. The Liveness KPI runs a real query against "
    "the database — if it shows a number, the QuickSight rendering "
    "pipeline is healthy and any blank visual on another sheet "
    "indicates a data or SQL issue. If the KPI is blank, QuickSight "
    "itself is broken."
)


# Visual identifiers — per-app-segmented (BO.5). Pre-BO.5 these were
# shared ``"app-info-liveness-ds"`` / ``"app-info-matviews-ds"`` strings
# across all four apps. The shared name was fine for QS deploys (each
# analysis's ``DataSetIdentifierDeclaration`` maps the same logical name
# to a different per-app ARN) but corrupted App2: the process-global
# ``_SQL_REGISTRY`` is keyed by ``visual_identifier``, so when the
# ``dashboards --app all`` server registered all four apps' datasets in
# sequence, whichever app ran LAST silently overwrote the others. The
# operator saw the same Executives-only 2-base-table panel on every
# dashboard. Cold-read F7 flagged this byte-identity. Per-segment IDs
# let the registry hold all four simultaneously.
def app_info_liveness_id(app_segment: str) -> str:
    """Return the per-app liveness-dataset visual_identifier."""
    return f"{app_segment}-app-info-liveness-ds"


def app_info_matviews_id(app_segment: str) -> str:
    """Return the per-app matview-status-dataset visual_identifier."""
    return f"{app_segment}-app-info-matviews-ds"


def app_info_latest_balance_day_id(app_segment: str) -> str:
    """DK.5.kpi — per-app data-anchor visual_identifier.

    Mirrors ``app_info_liveness_id``'s per-segment scheme so all four
    apps' Latest Balance Day datasets coexist in the App2 process-global
    SQL registry without overwriting each other (same registry-collision
    risk BO.5 fixed for the liveness + matviews datasets).
    """
    return f"{app_segment}-app-info-latest-balance-day-ds"


# Visual titles — exported so tests can import them rather than inline
# the literal (which silently rots when the title changes; v11.22.3's
# BH.18 cold-read rename caught test_qs_table_rows_well_formed flat).
APP_INFO_LIVENESS_TITLE = "Liveness"
APP_INFO_MATVIEW_STATUS_TITLE = "Matview Status — sources this app reads from"
APP_INFO_LATEST_BALANCE_DAY_TITLE = "Latest Balance Day"


# Module-level contract instances — must be the same object every time
# `build_dataset()` is called, otherwise the registry rejects the
# second call with a different-instance error. Module-level singletons
# satisfy that.
LIVENESS_CONTRACT = DatasetContract(columns=[
    ColumnSpec("table_count", "INTEGER"),
])


LATEST_BALANCE_DAY_CONTRACT = DatasetContract(columns=[
    ColumnSpec("data_anchor", "DATETIME"),
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
    if dialect is Dialect.DUCKDB:
        return (
            "SELECT COUNT(*) AS table_count "
            "FROM information_schema.tables "
            "WHERE table_schema = 'main'"
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
    parts: list[str] = []
    for name, date_col in view_specs:
        date_expr = f"MAX({date_col})" if date_col else "NULL"
        parts.append(
            f"SELECT '{name}' AS view_name, "
            f"COUNT(*) AS row_count, "
            f"{date_expr} AS latest_date FROM {name}"
        )
    return "\nUNION ALL\n".join(parts)


def build_latest_balance_day_dataset(
    cfg: Config, *, app_segment: str,
) -> BuiltDataset:
    """DK.5.kpi — Latest Balance Day KPI dataset.

    Real-query against the DK.1 singleton matview ``<prefix>_data_anchor``.
    Returns one row with the most recent moment the feed has data for
    (the same value DK.4 exports as ``RECON_GEN_AS_OF_ANCHOR`` at
    json-apply time, but live-queried per dashboard load so any post-
    deploy matview refresh moves the KPI forward without re-deploying).

    Operators read the KPI alongside the deploy-stamp ``as_of (at emit)``
    bullet (DK.5.bullets): when the KPI lags the bullet by more than a
    feed-cycle, the ETL is stale (and the dashboards are showing
    last-loaded data, not "today"). No alarm styling per DK.0 — data lag
    is normal in real systems.
    """
    sql = (
        f"SELECT data_anchor "
        f"FROM {cfg.db.table_prefix}_data_anchor "
        f"LIMIT 1"
    )
    return build_dataset(
        cfg,
        cfg.aws.prefixed(f"{app_segment}-app-info-latest-balance-day-dataset"),
        "App Info -- Latest Balance Day",
        "app-info-latest-balance-day",
        sql,
        LATEST_BALANCE_DAY_CONTRACT,
        visual_identifier=app_info_latest_balance_day_id(app_segment),
    )


def build_liveness_dataset(cfg: Config, *, app_segment: str) -> BuiltDataset:
    """Trivial liveness query against the database catalog.

    Postgres queries ``information_schema.tables``; Oracle queries
    ``USER_TABLES``. Returns one row with the user-visible-table count.
    Per-dialect SQL resolved from ``cfg.db.dialect`` (P.9c — earlier
    versions hardcoded the Postgres SQL on both dialects, which
    silently broke the KPI on Oracle).

    ``app_segment``: short kebab-case tag identifying which app owns
    this Dataset (e.g., ``"l1"``, ``"exec"``, ``"inv"``, ``"l2ft"``).
    Becomes part of the AWS DataSetId so each app gets its own
    physical dataset and ``deploy <single-app>`` doesn't delete-then-
    create another app's App Info dataset out from under it (M.4.4.7).
    BO.5 — also drives the ``visual_identifier`` (via
    ``app_info_liveness_id``) so all four apps' liveness datasets
    coexist in the App2 process-global SQL registry without overwriting
    each other.
    """
    return build_dataset(
        cfg,
        cfg.aws.prefixed(f"{app_segment}-app-info-liveness-dataset"),
        "App Info -- Liveness",  # ASCII-only — testing QS em-dash hypothesis
        "app-info-liveness",
        _liveness_sql(cfg.db.dialect),
        LIVENESS_CONTRACT,
        visual_identifier=app_info_liveness_id(app_segment),
    )


def build_matview_status_dataset(
    cfg: Config, *, app_segment: str, view_specs: list[ViewSpec],
) -> BuiltDataset:
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
        cfg.aws.prefixed(f"{app_segment}-app-info-matviews-dataset"),
        "App Info -- Matview Status",  # ASCII-only
        "app-info-matviews",
        _matview_status_sql(view_specs, cfg.db.dialect),
        MATVIEW_STATUS_CONTRACT,
        visual_identifier=app_info_matviews_id(app_segment),
    )


def _git_short_sha() -> str:
    """Best-effort git short SHA. Returns ``"unknown"`` if all paths fail.

    Resolution order (CY.1):

    1. ``recon_gen._build_info.__build_info__["git_sha"]`` — baked at
       wheel-build time by ``build_hook.BuildPyWithBuildInfo``. This is
       the canonical path for any pip / uv install; the SHA reflects the
       commit the wheel was built from.
    2. Runtime ``git rev-parse --short HEAD`` — fallback for dev venvs
       that imported ``recon_gen`` before the build hook ran (uncached
       editable install bootstrap). Best-effort: writes the result to
       ``_build_info.py`` so subsequent imports hit step 1 instead.
    3. ``"unknown"`` — neither a wheel-baked module nor a git checkout
       under cwd. Surfaces in the deploy stamp; not load-bearing.

    Intentionally swallows errors — the deploy stamp is informational
    and shouldn't block dashboard generation."""
    # Step 1: wheel-baked module. ``importlib.import_module`` avoids
    # the static-import pyright error since ``_build_info.py`` is
    # generated at build time and may not exist at type-check time.
    try:
        _mod = importlib.import_module("recon_gen._build_info")
        info_dict = cast(dict[str, str], getattr(_mod, "__build_info__", {}))
        baked: str = info_dict.get("git_sha", "")
        if baked and baked != "unknown":
            return baked
    except ImportError:
        pass

    # Step 2: runtime git rev-parse, best-effort. Also persist to
    # ``_build_info.py`` so later imports in the same venv skip this branch.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                _persist_build_info_best_effort(sha)
                return sha
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass

    return "unknown"


def _persist_build_info_best_effort(sha: str) -> None:
    """Write a ``_build_info.py`` alongside this package so subsequent
    ``_git_short_sha()`` calls in the same venv short-circuit on step 1.

    Best-effort: silently swallows any I/O error. The dev-venv case
    where this fires happens once per venv install; the file then
    persists until the next ``uv sync``.
    """
    try:
        import recon_gen  # local to avoid an import cycle at module load
        from pathlib import Path
        target = Path(recon_gen.__file__).parent / "_build_info.py"
        if target.exists():
            return  # don't trample a wheel-baked file
        ts = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
        content = (
            '"""Build-time-stamped metadata. AUTO-GENERATED — do not edit.\n'
            "\n"
            "Written at runtime by ``_git_short_sha()`` because the wheel\n"
            "build hook didn't run (likely an editable install pre-build,\n"
            "or a hand-installed sdist). The wheel-build path (CY.1)\n"
            "writes the same file via ``build_hook.BuildPyWithBuildInfo``.\n"
            '"""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "__build_info__: dict[str, str] = {\n"
            f'    "git_sha": {sha!r},\n'
            f'    "built_at": {ts!r},\n'
            '    "build_kind": "dev",\n'
            "}\n"
        )
        target.write_text(content, encoding="utf-8")
    except (OSError, ImportError):
        pass


def _deploy_stamp() -> tuple[str, str, str]:
    """Return ``(recon_gen_version, git_short_sha, iso_timestamp)``
    baked at generate time. The version is the package's ``__version__``
    string so a viewer can spot a stale dashboard against a newer CLI
    (V.3.a — version-mismatch detection)."""
    from recon_gen import __version__
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
# DK.5.kpi — single-value KPI row height (Latest Balance Day). Matches
# the existing TABLE_HEIGHT for visual balance with the row above it
# (Liveness KPI + Matview Status table); QS KPI cards size their text
# to fill the available height, so a TABLE_HEIGHT row reads as one
# prominent big-number visual at full-width.
_KPI_HEIGHT = 12


def _cadence_summary_line(l2_instance: L2Instance) -> str:
    """CL.9 — count internal entities by ``balance_cadence`` and
    render a one-line stat for the App Info deploy stamp.

    Counts both singleton accounts AND templates (the template
    cadence fans out to every materialized instance per CL.2 Lock
    4). External-scope accounts are excluded — they don't emit
    balance rows at all. ``resolve_cadence`` applies the None →
    sparse default so the line reflects the runtime behavior, not
    the literal YAML.
    """
    sparse_n = 0
    explicit_n = 0
    for a in l2_instance.accounts:
        if str(a.scope) != "internal":
            continue
        if resolve_cadence(a) == "explicit_daily":
            explicit_n += 1
        else:
            sparse_n += 1
    for t in l2_instance.account_templates:
        if str(t.scope) != "internal":
            continue
        if resolve_cadence(t) == "explicit_daily":
            explicit_n += 1
        else:
            sparse_n += 1
    return f"cadence: {sparse_n} sparse, {explicit_n} explicit_daily"


def _resolve_as_of_at_emit(cfg: Config) -> tuple[str, str]:
    """DK.5.bullets — resolve (value, source-label) for the Info-sheet
    deploy-stamp at emit time.

    Priority mirrors :func:`recon_gen.common.config.TestGeneratorConfig.as_of_frame`
    + the DK.4 ``_maybe_export_data_anchor`` wire shape:

    1. ``cfg.test.generator.end_date`` is set in yaml — operator pinned.
       Returns (date.isoformat(), "cfg.test.generator.end_date").
    2. ``RECON_GEN_AS_OF_ANCHOR_SOURCE`` env == "data_anchor" — DK.4
       auto-exported from the ``<prefix>_data_anchor`` matview. Returns
       (env value, "data-derived (data_anchor matview)").
    3. ``RECON_GEN_AS_OF_ANCHOR`` env is set with no DK.4-source marker
       — operator pinned manually (runner / shell export). Returns
       (env value, "RECON_GEN_AS_OF_ANCHOR env").
    4. Else — ``AsOfFrame.live()`` falls through to ``date.today()`` at
       dataset-emit time. The DK.3 deprecation comment marks this branch
       for post-DK.4 removal; in practice it only fires if DK.4's
       matview query returned None (cold DB / connect failure). Returns
       (today's date, "live (wall-clock fallback)").

    The Info-sheet renders these two strings as bullets; downstream
    Latest Balance Day KPI (DK.5.kpi) is a separate live-query against
    the matview for ETL-cadence freshness comparison.
    """
    if cfg.test.generator.end_date is not None:
        return (cfg.test.generator.end_date.isoformat(),
                "cfg.test.generator.end_date")
    from recon_gen.common.env_keys import (  # noqa: PLC0415
        RECON_GEN_AS_OF_ANCHOR,
        RECON_GEN_AS_OF_ANCHOR_SOURCE,
    )
    anchor = RECON_GEN_AS_OF_ANCHOR.get_or_none()
    source = RECON_GEN_AS_OF_ANCHOR_SOURCE.get_or_none()
    if anchor is not None and source == "data_anchor":
        return (anchor.isoformat(),
                "data-derived (data_anchor matview)")
    if anchor is not None:
        return (anchor.isoformat(), "RECON_GEN_AS_OF_ANCHOR env")
    # Live(wall-clock) — DK.3-deprecated path. Read via _as_of_today so
    # the value at emit matches what AsOfFrame.live() would produce.
    from recon_gen.common.as_of_frame import _as_of_today  # noqa: PLC0415
    return (_as_of_today().isoformat(), "live (wall-clock fallback)")


def populate_app_info_sheet(
    cfg: Config,
    sheet: Sheet,
    *,
    liveness_ds: Dataset,
    matview_status_ds: Dataset,
    theme: ThemePreset,
    l2_instance: L2Instance | None = None,
    latest_balance_day_ds: Dataset | None = None,
) -> None:
    """Populate the "i" sheet with three visuals (KPI + table + text box).

    Caller is responsible for registering the datasets on the App and
    for adding ``sheet`` to the Analysis as the LAST sheet (this helper
    doesn't enforce position because ``analysis.add_sheet`` order is
    the position).

    CL.9 — pass ``l2_instance`` to append a per-deploy
    ``cadence: N sparse, M explicit_daily`` line to the deploy
    stamp text box. Counts internal singleton + template entities;
    template counts apply per-template (a 1-template fan-out
    counts as 1, not as N materialized instances) so the line
    reflects the *declaration* shape, not the runtime row count.
    """
    accent = theme.accent
    version, sha, ts = _deploy_stamp()
    dialect = cfg.db.dialect.value
    prefix = cfg.aws.deployment_name

    # Row 1: liveness KPI (left half) + matview status table (right half).
    top = sheet.layout.row(height=_TABLE_HEIGHT)
    top.add_kpi(
        width=_HALF,
        title=APP_INFO_LIVENESS_TITLE,
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
        title=APP_INFO_MATVIEW_STATUS_TITLE,
        subtitle=(
            "Row counts + most-recent date for the matviews + base "
            "tables **this dashboard depends on directly**. Per-app "
            "scope by design — Executives reads only 2 base tables; "
            "L1 reads ~12 matviews. For total deploy freshness, check "
            "every app's App Info sheet. Freshly-loaded matviews "
            "showing 0 = ETL hasn't refreshed them yet. If a base "
            "table's `latest_date` moves past a matview's, the matview "
            "is stale — re-run `recon-gen data refresh --execute`."
        ),
        columns=[
            matview_status_ds["view_name"].dim(),
            matview_status_ds["row_count"].numerical(),
            matview_status_ds["latest_date"].date(),
        ],
    )

    # DK.5.kpi — Latest Balance Day single-cell table: live query against
    # the DK.1 singleton data_anchor matview. Pairs with the deploy-stamp
    # ``as_of (at emit)`` bullet — if the table value lags the bullet,
    # the feed has aged since deploy. Neutral subtitle per DK.0 lock:
    # data lag is normal in real systems; observability not alarm.
    #
    # Implemented as a Table (not a KPI) because QS's KPI visual only
    # accepts numerical measures; data_anchor is a DATETIME column and
    # the natural display is the date itself, not a count of it. The
    # 1-row × 1-column table reads like a labeled big-number cell.
    # Skipped when caller didn't pass the dataset (back-compat shim).
    if latest_balance_day_ds is not None:
        middle = sheet.layout.row(height=_KPI_HEIGHT)
        middle.add_table(
            width=_FULL,
            title=APP_INFO_LATEST_BALANCE_DAY_TITLE,
            subtitle=(
                "Most recent emitted balance day from the feed — live "
                "query against `<prefix>_data_anchor` (DK.1). When this "
                "lags the `as_of (at emit)` bullet below, the ETL has "
                "aged since the last `recon-gen json apply`; dashboards "
                "are still showing the deploy-time anchor, not today."
            ),
            columns=[latest_balance_day_ds["data_anchor"].date()],
        )

    # Row 2: deploy stamp text box. CY.2 — DuckDB is the legit local-prod
    # path post-CA (project_duckdb_local_default_post_ca) and the Mac mini
    # demos run DuckDB by design. The dialect line renders bare regardless
    # of dialect; "dev vs release" provenance now flows through CY.1's
    # ``__build_info__.build_kind`` baked into the wheel, not by inferring
    # build kind from dialect.
    dialect_line = f"dialect: {dialect}"
    # DK.5.bullets — as_of resolution surface. Operators reading the
    # deploy stamp need to know (a) which calendar day the dashboards
    # default to, and (b) where that value came from. The four
    # operator-meaningful sources:
    #
    #   - "cfg.test.generator.end_date"     — pinned in yaml
    #   - "RECON_GEN_AS_OF_ANCHOR env"      — pinned manually via env
    #   - "data-derived (data_anchor matview)"
    #                                       — DK.4 auto-export from feed
    #   - "live (wall-clock fallback)"     — DK.3-deprecated; should not
    #                                          fire in prod post-DK.4
    #
    # Tracked via `cfg.test.generator.end_date` + RECON_GEN_AS_OF_ANCHOR
    # + RECON_GEN_AS_OF_ANCHOR_SOURCE env. The deploy stamp is baked at
    # emit time so the value is locked once; live-changing data freshness
    # lives in the "Latest Balance Day" KPI (DK.5.kpi).
    as_of_value, as_of_source = _resolve_as_of_at_emit(cfg)
    bullet_lines = [
        f"recon-gen: v{version}",
        f"git: {sha}",
        f"generated: {ts}",
        dialect_line,
        f"prefix: {prefix}",
        f"as_of (at emit): {as_of_value}",
        f"as_of source: {as_of_source}",
    ]
    if l2_instance is not None:
        bullet_lines.append(_cadence_summary_line(l2_instance))
    sheet.layout.row(height=_TEXT_HEIGHT).add_text_box(
        TextBox(
            text_box_id="app-info-deploy-stamp",
            content=rt.text_box(
                rt.subheading("Deploy Stamp", color=accent),
                rt.bullets(bullet_lines),
            ),
        ),
        width=_FULL,
    )
