"""Tree-walker tests for the M.4.4.5 App Info ("i") canary sheet.

Every shipped L3 dashboard MUST end with a sheet named "i" that
contains the App Info liveness KPI. This is the convention that
collapses the QuickSight spinner-footgun ladder (CLAUDE.md ops
footgun) to a single glance.

Walks each app's emitted tree rather than asserting hardcoded sheet
counts — failures point at the offending app and explain WHY the
constraint matters.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from recon_gen.apps.executives.app import build_executives_app
from recon_gen.apps.investigation.app import build_investigation_app
from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
from recon_gen.apps.l2_flow_tracing.app import (
    build_l2_flow_tracing_app,
)
from recon_gen.common.config import Config
from recon_gen.common.tree import App
from tests._test_helpers import make_test_config
from recon_gen.common.sheets.app_info import (
    APP_INFO_SHEET_NAME,
    app_info_liveness_id,
    app_info_matviews_id,
)


# BO.5 — per-app identifier prefixes match the ``app_segment`` passed to
# ``build_liveness_dataset`` / ``build_matview_status_dataset`` by each
# app's ``build_all_*_datasets``. The map drives the per-builder
# parametrize so the harness asserts the right identifier on each app.
_APP_SEGMENTS: dict[str, str] = {
    "build_l1_dashboard_app": "l1",
    "build_l2_flow_tracing_app": "l2ft",
    "build_investigation_app": "inv",
    "build_executives_app": "exec",
}


class _AppBuilder(Protocol):
    def __call__(self, cfg: Config) -> App: ...
    __name__: str


_CFG = make_test_config(aws_region="us-east-2")


SHIPPED_APP_BUILDERS = [
    pytest.param(build_l1_dashboard_app, id="l1-dashboard"),
    pytest.param(build_l2_flow_tracing_app, id="l2-flow-tracing"),
    pytest.param(build_investigation_app, id="investigation"),
    pytest.param(build_executives_app, id="executives"),
]


@pytest.mark.parametrize("builder", SHIPPED_APP_BUILDERS)
def test_last_sheet_is_app_info(builder: _AppBuilder) -> None:
    """The last sheet on every shipped app must be the "i" canary.

    Diagnostic value: when a sheet renders blank in QS, the operator
    glances at "i". If "i" renders, QS is healthy and the empty
    visual is a data/SQL issue. If "i" is also blank, QS itself is
    broken (the CLAUDE.md spinner-forever footgun).
    """
    app = builder(_CFG)
    assert app.analysis is not None, f"{app.name} has no analysis"
    sheets = app.analysis.sheets
    assert sheets[-1].name == APP_INFO_SHEET_NAME, (
        f"{app.name}'s last sheet is {sheets[-1].name!r}, not "
        f"{APP_INFO_SHEET_NAME!r}. Add the App Info sheet via "
        f"common/sheets/app_info.py — it MUST be the last sheet."
    )


@pytest.mark.parametrize("builder", SHIPPED_APP_BUILDERS)
def test_app_info_sheet_carries_liveness_kpi(builder: _AppBuilder) -> None:
    """The "i" sheet must contain a KPI sourced from the liveness
    dataset — that's what makes it a meaningful diagnostic canary
    rather than just a label."""
    app = builder(_CFG)
    assert app.analysis is not None, f"{app.name} has no analysis"
    info_sheet = app.analysis.sheets[-1]
    visual_kinds = {type(v).__name__ for v in info_sheet.visuals}
    assert "KPI" in visual_kinds, (
        f"{app.name}'s App Info sheet has no KPI; visuals: "
        f"{visual_kinds}. The liveness KPI is the canary signal."
    )
    # Confirm the liveness dataset is one of the dataset refs the
    # KPI's measures resolve to.
    kpi = next(v for v in info_sheet.visuals if type(v).__name__ == "KPI")
    kpi_dataset_ids = {ds.identifier for ds in kpi.datasets()}
    expected_id = app_info_liveness_id(_APP_SEGMENTS[builder.__name__])
    assert expected_id in kpi_dataset_ids, (
        f"{app.name}'s App Info KPI doesn't read from "
        f"{expected_id!r}; reads from {kpi_dataset_ids}."
    )


@pytest.mark.parametrize("builder", SHIPPED_APP_BUILDERS)
def test_app_info_datasets_declared(builder: _AppBuilder) -> None:
    """Both App Info datasets (liveness + matview status) must be
    declared on the App so deploy ships them. BO.5 — identifiers are
    per-app-segmented so the four-app App2 server's process-global SQL
    registry can hold all four simultaneously without overwriting."""
    app = builder(_CFG)
    declared = {ds.identifier for ds in app.datasets}
    segment = _APP_SEGMENTS[builder.__name__]
    liveness_id = app_info_liveness_id(segment)
    matviews_id = app_info_matviews_id(segment)
    assert liveness_id in declared, (
        f"{app.name} is missing {liveness_id!r} — the "
        f"liveness KPI dataset isn't registered."
    )
    assert matviews_id in declared, (
        f"{app.name} is missing {matviews_id!r} — the "
        f"matview status table dataset isn't registered."
    )


def test_liveness_sql_resolves_per_dialect() -> None:
    """P.9c: the Liveness KPI's SQL is dialect-aware.

    Postgres branch queries ``information_schema.tables`` (Postgres
    catalog convention); Oracle branch queries ``USER_TABLES``
    (Oracle catalog convention). Earlier versions hardcoded the
    Postgres SQL on both paths, which silently broke the KPI on
    Oracle deployments — QuickSight rendered the visual as blank
    because the underlying CustomSQL failed at parse time.
    """
    from recon_gen.common.sheets.app_info import build_liveness_dataset
    from recon_gen.common.sql import Dialect
    import dataclasses

    pg_cfg = dataclasses.replace(_CFG, dialect=Dialect.POSTGRES)
    oracle_cfg = dataclasses.replace(_CFG, dialect=Dialect.ORACLE)

    pg = build_liveness_dataset(pg_cfg, app_segment="l1")
    oracle = build_liveness_dataset(oracle_cfg, app_segment="l1")

    pg_sql = pg.PhysicalTableMap["app-info-liveness"].CustomSql.SqlQuery  # type: ignore[union-attr]: liveness physical table is always CustomSql by construction
    oracle_sql = oracle.PhysicalTableMap["app-info-liveness"].CustomSql.SqlQuery  # type: ignore[union-attr]: liveness physical table is always CustomSql by construction

    assert "information_schema" in pg_sql
    assert "table_schema" in pg_sql
    assert "USER_TABLES" in oracle_sql
    assert "information_schema" not in oracle_sql, (
        "Oracle branch must not reference Postgres catalog views"
    )


def test_matview_status_sql_omits_postgres_only_casts() -> None:
    """P.9c: the Matview Status SQL has no ``::text`` / ``::integer``
    casts. The column types are pinned by ``MATVIEW_STATUS_CONTRACT``,
    so the casts were no-ops on Postgres and silently broke the
    dataset on Oracle (Oracle uses ``CAST(x AS NUMBER)`` syntax,
    not ``x::integer``).
    """
    from recon_gen.common.sheets.app_info import _matview_status_sql
    from recon_gen.common.sql import Dialect

    sql_with_views = _matview_status_sql(
        [("matview_a", "business_day"), ("matview_b", None)],
        Dialect.POSTGRES,
    )
    sql_empty = _matview_status_sql([], Dialect.POSTGRES)

    for sql in (sql_with_views, sql_empty):
        assert "::text" not in sql, (
            f"Postgres-only ``::text`` cast leaked into matview-status "
            f"SQL: {sql!r}"
        )
        assert "::integer" not in sql, (
            f"Postgres-only ``::integer`` cast leaked into matview-status "
            f"SQL: {sql!r}"
        )


@pytest.mark.parametrize("builder", SHIPPED_APP_BUILDERS)
def test_app_info_deploy_stamp_reads_dialect_and_prefix_from_cfg(builder: _AppBuilder) -> None:
    """AO.C1 lock — the App Info deploy-stamp TextBox must render
    ``dialect: <cfg.dialect>`` + ``prefix: <cfg.deployment_name>``,
    NOT the literals "sqlite" / "dev" / any other hardcoded value.

    Background (cold-read feedback #10): a judge looking at the dev
    capture saw ``dialect: sqlite`` / ``prefix: dev`` and flagged it —
    fine for the dev shot, but the renderer MUST pull from cfg so a
    production deploy stamps the real engine + the real deployment
    name. Lock test runs two cfg shapes through each shipped app and
    asserts the stamp content reflects the cfg.
    """
    from recon_gen.common.sql.dialect import Dialect

    for cfg in [
        make_test_config(
            aws_region="us-east-2",
            dialect=Dialect.POSTGRES,
            deployment_name="recon-prod",
        ),
        make_test_config(
            aws_region="us-east-2",
            dialect=Dialect.ORACLE,
            deployment_name="recon-staging",
        ),
    ]:
        app = builder(cfg)
        analysis = app.emit_analysis()
        # Find the App Info sheet ("i") and pull its deploy-stamp text box.
        assert analysis.Definition is not None, "analysis missing Definition"
        assert analysis.Definition.Sheets is not None, "analysis missing Sheets"
        info_sheet = next(
            s for s in analysis.Definition.Sheets
            if s.Name == APP_INFO_SHEET_NAME
        )
        # The deploy stamp TextBox carries inline content with the
        # dialect + prefix lines.
        deploy_stamp_text = None
        for tb in info_sheet.TextBoxes or []:
            if tb.SheetTextBoxId == "app-info-deploy-stamp":
                deploy_stamp_text = tb.Content
                break
        assert deploy_stamp_text is not None, (
            f"{builder.__name__}: App Info sheet must carry the "
            f"`app-info-deploy-stamp` TextBox"
        )
        # Lock the cfg-driven shape: stamp content includes the literal
        # cfg.dialect.value and cfg.deployment_name strings.
        assert f"dialect: {cfg.dialect.value}" in deploy_stamp_text, (
            f"{builder.__name__}: deploy stamp must read "
            f"`dialect: {cfg.dialect.value}` (got hardcode or wrong "
            f"dialect)"
        )
        assert f"prefix: {cfg.deployment_name}" in deploy_stamp_text, (
            f"{builder.__name__}: deploy stamp must read "
            f"`prefix: {cfg.deployment_name}` (got hardcode or wrong "
            f"prefix)"
        )


def test_no_two_apps_share_an_app_info_data_set_id() -> None:
    """Each shipped app's App Info datasets carry a per-app segment in
    their AWS DataSetId so deploying app A doesn't delete-then-create
    app B's App Info dataset out from under it (M.4.4.7).

    The Dataset's tree-internal ``identifier`` (used as
    ``DataSetIdentifier`` in the analysis JSON) stays shared across
    apps — that's analysis-internal and each app's analysis has its
    own ``DataSetIdentifierDeclaration`` mapping to its own ARN. What
    must NOT collide is the AWS-side resource ID, which we derive
    here by parsing each Dataset's ARN trailing segment.
    """
    from recon_gen.common.sheets.app_info import (
        build_liveness_dataset, build_matview_status_dataset,
    )

    aws_ids: dict[str, set[str]] = {}
    for app_segment in ("l1", "exec", "inv", "l2ft"):
        liveness = build_liveness_dataset(_CFG, app_segment=app_segment)
        matviews = build_matview_status_dataset(
            _CFG, app_segment=app_segment, view_specs=[],
        )
        aws_ids[app_segment] = {liveness.DataSetId, matviews.DataSetId}

    # Pairwise: every app's ID set must be disjoint from every other.
    seen_ids: set[str] = set()
    for app_segment, ids in aws_ids.items():
        overlap = ids & seen_ids
        assert not overlap, (
            f"app {app_segment!r} App Info DataSetIds {overlap!r} "
            f"already used by an earlier app — deploy <single-app> "
            f"would collide. Update the app_segment passed to "
            f"build_liveness_dataset / build_matview_status_dataset."
        )
        seen_ids |= ids
