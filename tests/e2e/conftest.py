"""Shared fixtures for end-to-end tests.

All e2e tests are skipped unless QS_GEN_E2E=1 is set. This keeps
`pytest` fast and free of AWS dependencies by default.

Required env vars (or config.yaml):
    QS_GEN_AWS_ACCOUNT_ID
    QS_GEN_AWS_REGION
    QS_GEN_DATASOURCE_ARN (or QS_GEN_DEMO_DATABASE_URL)

Optional env vars for tuning:
    QS_E2E_PAGE_TIMEOUT   — page load timeout in ms (default 30000)
    QS_E2E_VISUAL_TIMEOUT — per-visual render timeout in ms (default 10000)
    QS_E2E_IDENTITY_REGION — QuickSight identity region (default us-east-1)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quicksight_gen.common.env_keys import (
    EnvVarInvalid,
    QS_E2E_IDENTITY_REGION,
    QS_E2E_PAGE_TIMEOUT,
    QS_E2E_VISUAL_TIMEOUT,
    QS_GEN_CONFIG,
    QS_GEN_E2E,
    QS_GEN_RUN_DIR,
    QS_GEN_TEST_L2_INSTANCE,
)


def pytest_collection_modifyitems(config, items):
    """Skip all e2e tests unless QS_GEN_E2E=1."""
    if QS_GEN_E2E.get_or_none():
        return
    skip = pytest.mark.skip(reason="e2e tests disabled (set QS_GEN_E2E=1)")
    for item in items:
        if "e2e" in str(item.fspath):
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Expose per-phase test outcome to fixtures via item.rep_<phase>.

    M.4.1.f's harness fixtures consult ``item.rep_call.failed`` during
    teardown to decide whether to dump the failure triage manifest.
    Standard pytest idiom.
    """
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------

PAGE_TIMEOUT = QS_E2E_PAGE_TIMEOUT.get_or_none() or 30000
VISUAL_TIMEOUT = QS_E2E_VISUAL_TIMEOUT.get_or_none() or 10000
IDENTITY_REGION = QS_E2E_IDENTITY_REGION.get_or_none() or "us-east-1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cfg():
    """Load project config — checks the legacy single-file location, then
    the per-dialect copies (Phase P), then env vars.

    The candidate order favors the explicit single-file config before
    falling back to the dialect-specific files. Override with the
    ``QS_GEN_CONFIG`` env var when both per-dialect files exist and
    you need to pin to one.
    """
    from quicksight_gen.common.config import load_config

    # Soft-fall: registry's must_be_file validator would raise on a
    # bad pin; the discovery loop below has fallback candidates.
    try:
        explicit = QS_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit = None
    if explicit is not None:
        return load_config(str(explicit))

    candidates = (
        Path("config.yaml"),
        Path("run/config.yaml"),
        Path("run/config.postgres.yaml"),
        Path("run/config.oracle.yaml"),
    )
    for candidate in candidates:
        if candidate.exists():
            return load_config(str(candidate))
    return load_config(None)


@pytest.fixture(scope="session")
def account_id(cfg) -> str:
    return cfg.aws_account_id


@pytest.fixture(scope="session")
def region(cfg) -> str:
    return cfg.aws_region


@pytest.fixture(scope="session")
def resource_prefix(cfg) -> str:
    return cfg.resource_prefix


@pytest.fixture(scope="session")
def qs_client(region):
    """Boto3 QuickSight client for the dashboard region."""
    import boto3
    return boto3.client("quicksight", region_name=region)


@pytest.fixture
def qs_driver(cfg, region, account_id):  # type: ignore[no-untyped-def]: return-type annotation would force a QsEmbedDriver import at module scope
    """X.2.q — ``QsEmbedDriver`` over a fresh WebKit page, for browser
    e2e tests that drive a deployed QuickSight dashboard through the
    ``DashboardDriver`` protocol (``open(dashboard_id)`` mints the embed
    URL). Skips cleanly when ``QS_E2E_USER_ARN`` is unset (the runner
    derives it from ``cfg.auth.aws_profile``; export it for a direct
    ``pytest`` run). Function-scoped — embed URLs are single-use.
    """
    from quicksight_gen.common.browser.helpers import get_user_arn
    from tests.e2e._drivers import QsEmbedDriver

    try:
        get_user_arn()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with QsEmbedDriver.embed(
        aws_account_id=account_id, aws_region=region,
    ) as d:
        yield d


def _resolve_test_l2_instance():  # type: ignore[no-untyped-def]: return-type annotation would force an L2Instance import at module scope, slowing collection
    """Resolve the L2 instance the e2e tests should mirror.

    Honors ``QS_GEN_TEST_L2_INSTANCE`` (the runner / release.yml inject
    it per-variant / per-release); falls back to the bundled
    ``default_l2_instance()`` (`spec_example`) when unset.

    Used by both the ``*_l2_prefix`` fixtures (for ID-string
    construction) and the ``*_app`` fixtures (so the tree the test
    walks has the same L2 prefix as the deployed resources).
    """
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.common.l2 import load_instance

    override = QS_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


@pytest.fixture(scope="session")
def inv_l2_prefix() -> str:
    """The default L2 instance's prefix — the middle segment of every
    Investigation resource ID under N.3.f (Investigation became L2-fed,
    same default-institution YAML the L1 dashboard uses)."""
    return str(_resolve_test_l2_instance().instance)


@pytest.fixture(scope="session")
def inv_dashboard_id(resource_prefix, inv_l2_prefix) -> str:
    return f"{resource_prefix}-{inv_l2_prefix}-investigation-dashboard"


@pytest.fixture(scope="session")
def inv_analysis_id(resource_prefix, inv_l2_prefix) -> str:
    return f"{resource_prefix}-{inv_l2_prefix}-investigation-analysis"


@pytest.fixture(scope="session")
def inv_dataset_ids(inv_app) -> list[str]:
    """Investigation dataset IDs derived from the tree.

    Drift-resistant: the App's registered datasets ARE the source of
    truth, no parallel hand-list to keep in sync. v8.8.0a23 hotfix
    pivot — the prior hand-listed form silently miscounted when Y.2.g
    added 3 new L1 companions; switched all three apps' fixtures to
    ``[ds.arn.rsplit('/', 1)[-1] for ds in <app>.datasets]`` so the
    next dataset addition Just Works.
    """
    return [ds.arn.rsplit("/", 1)[-1] for ds in inv_app.datasets]


@pytest.fixture(scope="session")
def exec_l2_prefix() -> str:
    """The default L2 instance's prefix — the middle segment of every
    Executives resource ID under N.4.b (Executives became L2-fed,
    same default-institution YAML the L1 dashboard uses)."""
    return str(_resolve_test_l2_instance().instance)


@pytest.fixture(scope="session")
def exec_dashboard_id(resource_prefix, exec_l2_prefix) -> str:
    return f"{resource_prefix}-{exec_l2_prefix}-executives-dashboard"


@pytest.fixture(scope="session")
def exec_analysis_id(resource_prefix, exec_l2_prefix) -> str:
    return f"{resource_prefix}-{exec_l2_prefix}-executives-analysis"


@pytest.fixture(scope="session")
def exec_dataset_ids(exec_app) -> list[str]:
    """Executives dataset IDs derived from the tree (drift-resistant)."""
    return [ds.arn.rsplit("/", 1)[-1] for ds in exec_app.datasets]


# -- L1 dashboard fixtures (M.2c) --------------------------------------------
#
# IDs derive from the resource_prefix + the L2 instance's prefix per the
# M.2d.3 convention: `<resource_prefix>-<l2_prefix>-l1-<thing>`. The L2
# prefix is queried from the same default L2 instance the
# CLI/build_l1_dashboard_app uses, so no hardcoded "sasquatch_ar"
# string lives in the e2e fixtures.


@pytest.fixture(scope="session")
def l1_l2_prefix() -> str:
    """The default L2 instance's prefix — the middle segment of every
    L1 resource ID per M.2d.3."""
    return str(_resolve_test_l2_instance().instance)


@pytest.fixture(scope="session")
def l1_dashboard_id(resource_prefix, l1_l2_prefix) -> str:
    return f"{resource_prefix}-{l1_l2_prefix}-l1-dashboard"


@pytest.fixture(scope="session")
def l1_analysis_id(resource_prefix, l1_l2_prefix) -> str:
    return f"{resource_prefix}-{l1_l2_prefix}-l1-dashboard-analysis"


@pytest.fixture(scope="session")
def l1_dataset_ids(l1_app) -> list[str]:
    """L1 dashboard dataset IDs derived from the tree (drift-resistant).

    Switched from the M.2c.1 hand-listed form after the v8.8.0a23
    hotfix: Y.2.g.0 added 3 new L1 companion datasets and the prior
    hand-list silently miscounted, taking down the e2e gate. Tree-walk
    is the source of truth — the next dataset addition Just Works.
    """
    return [ds.arn.rsplit("/", 1)[-1] for ds in l1_app.datasets]


# -- L2 Flow Tracing dashboard fixtures --------------------------------------
#
# IDs derive from the resource_prefix + the L2 instance's prefix per the
# M.2d.3 convention. L2FT's dashboard ID lacks the trailing ``-dashboard``
# segment that L1 / Inv / Exec carry — the App's name is the suffix.


@pytest.fixture(scope="session")
def l2ft_l2_prefix() -> str:
    """The default L2 instance's prefix — the middle segment of every
    L2FT resource ID. Matches the same default the L2FT CLI uses
    (``spec_example``)."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.common.l2 import load_instance

    override = QS_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return str(load_instance(override).instance)
    return str(default_l2_instance().instance)


@pytest.fixture(scope="session")
def l2ft_l2_instance():
    """The loaded ``L2Instance`` the e2e session targets — same resolution
    as `l2ft_l2_prefix`, but the object, not just the prefix string."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.common.l2 import load_instance

    override = QS_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


# ---------------------------------------------------------------------------
# L2FT optional-feature guard (Y.2.browser.triage).
#
# The only thing a *valid* L2 YAML requires is a single rail (which implies
# at least one account). Everything else — chains, transfer templates,
# arbitrary metadata cascades, … — is optional. So an L2FT browser test that
# exercises a deployed-matview surface keyed off an optional feature should
# `pytest.skip` cleanly when the L2 targeted by this session doesn't declare
# that feature (spec_example declares zero chains; a fuzz seed may declare
# neither). The no-feature case rendering clean — empty table, vacuous
# dropdown, no QS error overlay — is already covered by the L2FT render
# tests, so no coverage is lost.
#
# Note: a non-empty *declared* list is necessary but not sufficient for the
# matview to have rows — e.g. spec_example declares a transfer template but
# the baseline/plant seed fires no instances of it. Tests therefore ALSO
# keep their downstream "table started empty → skip"; this just fast-exits
# the obvious `declared zero` case (and documents the principle).
_L2FT_FEATURE_DECLARED = {
    "chains": "declared_chain_parents",
    "templates": "declared_template_names",
}


def require_l2ft_feature(l2_instance, feature: str) -> None:
    """`pytest.skip` if ``l2_instance`` declares zero of ``feature``
    (``"chains"`` | ``"templates"``). Call from an autouse fixture in an
    L2FT browser test module that only applies when that feature exists."""
    from quicksight_gen.apps.l2_flow_tracing import datasets as _l2ft_ds

    fn_name = _L2FT_FEATURE_DECLARED[feature]
    declared = getattr(_l2ft_ds, fn_name)(l2_instance)
    if not declared:
        pytest.skip(
            f"deployed L2 declares no {feature} — the L2FT {feature} "
            f"narrow-doesn't-empty guard has nothing to exercise (the "
            f"{feature} sheet rendering clean for an empty L2 is covered "
            f"by the render tests)."
        )


@pytest.fixture(scope="session")
def l2ft_dashboard_id(resource_prefix, l2ft_l2_prefix) -> str:
    return f"{resource_prefix}-{l2ft_l2_prefix}-l2-flow-tracing"


@pytest.fixture(scope="session")
def l2ft_analysis_id(resource_prefix, l2ft_l2_prefix) -> str:
    return f"{resource_prefix}-{l2ft_l2_prefix}-l2-flow-tracing-analysis"


# ---------------------------------------------------------------------------
# Tree-built App fixtures (L.11)
#
# Session-scoped because the tree is pure, in-memory, and identical for
# every test that consumes it. Tests walk these to derive expected sheet
# names / visual titles / filter group ids / parameter names — the tree
# is the source of truth, not a parallel hand-maintained list.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def inv_app(cfg):
    """Tree-built Investigation App (post-emit, auto-IDs resolved).

    Honors ``QS_GEN_TEST_L2_INSTANCE`` so the tree's dataset ARNs
    match the deployed resources' L2 prefix (release.yml's per-tag
    ``rel_<tag>``, the runner's variant ``sp_pg_aw``, etc.). Without
    this the hotfix-v8.8.0a23 derived-fixture pivot would have been
    a step backward — IDs would drift on every non-default L2 run.
    """
    from quicksight_gen.apps.investigation.app import build_investigation_app

    app = build_investigation_app(
        cfg, l2_instance=_resolve_test_l2_instance(),
    )
    app.emit_analysis()
    return app


@pytest.fixture(scope="session")
def exec_app(cfg):
    """Tree-built Executives App (post-emit, auto-IDs resolved).
    See ``inv_app`` for the L2-instance-honoring rationale."""
    from quicksight_gen.apps.executives.app import build_executives_app

    app = build_executives_app(
        cfg, l2_instance=_resolve_test_l2_instance(),
    )
    app.emit_analysis()
    return app


@pytest.fixture(scope="session")
def l1_app(cfg):
    """Tree-built L1 Reconciliation Dashboard App.

    Honors ``QS_GEN_TEST_L2_INSTANCE`` — the same L2 the CLI's
    ``json apply`` was driven with for the deployed resources. Tree
    shape (and dataset ARNs) thus match the deployed shape exactly,
    making derived ``l1_dataset_ids`` ↔ deployed-DataSetId comparisons
    trivially correct. Post-emit so auto-IDs are resolved.
    """
    from quicksight_gen.apps.l1_dashboard.app import build_l1_dashboard_app

    app = build_l1_dashboard_app(
        cfg, l2_instance=_resolve_test_l2_instance(),
    )
    app.emit_analysis()
    return app


@pytest.fixture(scope="session")
def l2ft_app(cfg):
    """Tree-built L2 Flow Tracing App (post-emit, auto-IDs resolved).
    See ``inv_app`` for the L2-instance-honoring rationale.
    ``build_l2_flow_tracing_app`` registers its datasets' CustomSQL +
    contracts internally (``build_all_l2_flow_tracing_datasets``)."""
    from quicksight_gen.apps.l2_flow_tracing.app import (
        build_l2_flow_tracing_app,
    )

    app = build_l2_flow_tracing_app(
        cfg, l2_instance=_resolve_test_l2_instance(),
    )
    app.emit_analysis()
    return app


# ---------------------------------------------------------------------------
# Parametrized [qs, app2] driver fixtures (X.2.u)
# ---------------------------------------------------------------------------
#
# One body × two renderers. Each `<app>_dashboard_driver` fixture is
# parametrized over `["qs", "app2"]` and yields `(driver, dashboard_arg)`:
#
#   - `qs`   — drives the *deployed* dashboard (`<resource_prefix>-<l2>-
#     <app>-...`), real data via the QS datasource. `dashboard_arg` is
#     the deployed dashboard ID. Skips when `QS_E2E_USER_ARN` is unset
#     (no embed signer) or the dashboard isn't deployed.
#   - `app2` — drives a *locally-spun* App 2 server built from the same
#     `<app>_app` tree, reading the same DB (`cfg.demo_database_url`) via
#     `make_live_db_fetcher_for_app` — the "output" slot of the
#     `scenario → DB → output` pipeline. `dashboard_arg` is the local
#     slug. Skips when `cfg.demo_database_url` is unset.
#
# Function-scoped: the QS embed URL is single-use; the App 2 server spins
# in ~1–2 s, acceptable. See docs/audits/x_2_u_parametrized_driver_spike.md.


def _parametrized_dashboard_driver(  # type: ignore[no-untyped-def]: return-type annotation would force a driver import at module scope
    request, *, cfg, region, account_id, dashboard_id, app, short,
):
    if request.param == "qs":
        from quicksight_gen.common.browser.helpers import get_user_arn
        from tests.e2e._drivers import QsEmbedDriver

        try:
            get_user_arn()
        except RuntimeError as exc:
            pytest.skip(str(exc))
        import boto3

        qs = boto3.client("quicksight", region_name=region)
        try:
            qs.describe_dashboard(
                AwsAccountId=account_id, DashboardId=dashboard_id,
            )
        except qs.exceptions.ResourceNotFoundException:
            pytest.skip(
                f"dashboard {dashboard_id!r} not deployed in "
                f"{account_id}/{region} — deploy it first"
            )
        with QsEmbedDriver.embed(
            aws_account_id=account_id, aws_region=region,
        ) as driver:
            yield driver, dashboard_id
    else:  # app2
        if not getattr(cfg, "demo_database_url", None):
            pytest.skip(
                "no cfg.demo_database_url — the app2 leg reads the same DB "
                "the deployed dashboard does"
            )
        from tests.e2e._drivers import App2Driver
        from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

        assert app.analysis is not None
        data_fetcher, options_fetcher = make_live_db_fetchers_for_app(
            tree_app=app, cfg=cfg,
        )
        with App2Driver.serving(
            tree_app=app, sheet=app.analysis.sheets[0],
            data_fetcher=data_fetcher, options_fetcher=options_fetcher,
            dashboard_id=short, dashboard_title=f"{short} (live)",
        ) as driver:
            yield driver, short


@pytest.fixture(params=["qs", "app2"])
def l1_dashboard_driver(request, cfg, region, account_id, l1_dashboard_id, l1_app):  # type: ignore[no-untyped-def]: return-type annotation would force a driver import at module scope
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, region=region, account_id=account_id,
        dashboard_id=l1_dashboard_id, app=l1_app, short="l1",
    )


@pytest.fixture(params=["qs", "app2"])
def inv_dashboard_driver(request, cfg, region, account_id, inv_dashboard_id, inv_app):  # type: ignore[no-untyped-def]: return-type annotation would force a driver import at module scope
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, region=region, account_id=account_id,
        dashboard_id=inv_dashboard_id, app=inv_app, short="inv",
    )


@pytest.fixture(params=["qs", "app2"])
def exec_dashboard_driver(request, cfg, region, account_id, exec_dashboard_id, exec_app):  # type: ignore[no-untyped-def]: return-type annotation would force a driver import at module scope
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, region=region, account_id=account_id,
        dashboard_id=exec_dashboard_id, app=exec_app, short="exec",
    )


@pytest.fixture(params=["qs", "app2"])
def l2ft_dashboard_driver(request, cfg, region, account_id, l2ft_dashboard_id, l2ft_app):  # type: ignore[no-untyped-def]: return-type annotation would force a driver import at module scope
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, region=region, account_id=account_id,
        dashboard_id=l2ft_dashboard_id, app=l2ft_app, short="l2ft",
    )


@pytest.fixture(scope="session")
def page_timeout() -> int:
    return PAGE_TIMEOUT


@pytest.fixture(scope="session")
def visual_timeout() -> int:
    return VISUAL_TIMEOUT


# Aurora Serverless v2 scales to zero when idle. The first SELECT after
# a cold start can take 20-30s while the cluster warms up — long enough
# that browser e2e helpers that wait ~30s for visuals to hydrate will
# time out on the first sheet they touch. Warm the cluster once at session
# start by issuing the heaviest queries directly via psycopg2, so the
# subsequent dashboard renders hit a hot cluster. Pairs with the retry
# wrapper in browser_helpers.py for ad-hoc reruns where this fixture
# isn't covering us.
_WARMUP_QUERIES = (
    "SELECT 1",
    "SELECT COUNT(*) FROM transactions",
    "SELECT COUNT(*) FROM daily_balances",
    "SELECT COUNT(*) FROM ar_subledger_balance_drift",
    "SELECT COUNT(*) FROM ar_ledger_balance_drift",
    "SELECT COUNT(*) FROM ar_transfer_summary",
    "SELECT COUNT(*) FROM ar_subledger_overdraft",
    "SELECT COUNT(*) FROM ar_subledger_limit_breach",
    "SELECT COUNT(*) FROM ar_expected_zero_eod_rollup",
    "SELECT COUNT(*) FROM ar_two_sided_post_mismatch_rollup",
    "SELECT COUNT(*) FROM ar_balance_drift_timelines_rollup",
    "SELECT COUNT(*) FROM ar_unified_exceptions",
    # Investigation matviews — heavier to refresh than to read but the
    # first SELECT after Aurora cold-starts still pays the warm-up tax.
    "SELECT COUNT(*) FROM inv_pair_rolling_anomalies",
    "SELECT COUNT(*) FROM inv_money_trail_edges",
    # M.2c.1 — L1 invariant views per the M.1a.7 schema, prefixed by
    # the canonical Sasquatch AR L2 instance the L1 dashboard targets
    # by default. F12 cold-start tax applies to the first SELECT against
    # each prefixed table; warm them up here so the dashboard's first
    # render hits a hot cluster.
    "SELECT COUNT(*) FROM sasquatch_ar_current_transactions",
    "SELECT COUNT(*) FROM sasquatch_ar_current_daily_balances",
    "SELECT COUNT(*) FROM sasquatch_ar_drift",
    "SELECT COUNT(*) FROM sasquatch_ar_ledger_drift",
    "SELECT COUNT(*) FROM sasquatch_ar_overdraft",
    "SELECT COUNT(*) FROM sasquatch_ar_expected_eod_balance_breach",
    "SELECT COUNT(*) FROM sasquatch_ar_limit_breach",
)


@pytest.fixture(scope="session", autouse=True)
def warm_aurora(cfg):
    """Pre-warm Aurora before any e2e visual hits the dashboard."""
    if not cfg.demo_database_url:
        return
    try:
        import psycopg
    except ImportError:
        return
    try:
        conn = psycopg.connect(cfg.demo_database_url, connect_timeout=60)
    except Exception:
        return
    try:
        with conn.cursor() as cur:
            for sql in _WARMUP_QUERIES:
                try:
                    cur.execute(sql)
                    cur.fetchall()
                except Exception:
                    pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Y.2.gate.c.10 — Top-queries auto-capture
#
# Replaces ``scripts/dump_top_queries.py`` (W.8a) for the in-process
# path: instead of a CI-step shellout, every e2e session that hits a
# DB writes its own perf snapshot to
# ``$QS_GEN_RUN_DIR/db/<dialect>/top-queries.md`` at session
# teardown. ``Y.2.gate.f.4`` deletes the standalone script + the
# CI workflow steps that called it.
#
# Sidecar contract (matches c.2 / c.12): no-op when env unset, errors
# swallowed so the perf snapshot never breaks a passing test session.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def capture_top_queries(cfg, request):
    """Session-end perf-snapshot hook.

    Yields immediately; on teardown, if ``QS_GEN_RUN_DIR`` is set AND
    the cfg has a demo_database_url, connects, runs the dialect's
    stats-view query, and writes a markdown table. SQLite is silently
    skipped (no equivalent stats view).

    The like-pattern defaults to the L2 instance prefix the test
    session targeted (``QS_GEN_TEST_L2_INSTANCE`` env override → its
    instance prefix; else ``cfg.l2_instance_prefix`` if set; else the
    string ``spec_example`` for the demo). That keeps multi-tenant
    shared-DB output narrowed to OUR queries.
    """
    yield

    # Sidecar contract — swallow EnvVarInvalid (a misconfigured env
    # var must not break a passing test session's teardown).
    try:
        run_dir_path = QS_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        return
    if run_dir_path is None:
        return
    run_dir = str(run_dir_path)
    if not cfg.demo_database_url:
        return

    from quicksight_gen._dev.perf import (
        dialect_name,
        fetch_top_queries,
        format_skipped,
        format_top_queries_markdown,
    )
    from quicksight_gen.common.db import connect_demo_db
    from quicksight_gen.common.sql import Dialect

    dialect_str = dialect_name(cfg.dialect)
    target_dir = Path(run_dir) / "db" / dialect_str
    target = target_dir / "top-queries.md"
    title = f"Top expensive queries ({dialect_str})"

    # SQLite has no stats view — write a clean skipped marker and stop.
    if cfg.dialect is Dialect.SQLITE:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(
                format_skipped(
                    title=title,
                    dialect=dialect_str,
                    reason="SQLite has no equivalent of pg_stat_statements / v$sqlstats.",
                ),
            )
        except OSError:
            pass
        return

    # Pick the substring filter — prefer the explicit L2 instance the
    # test session targeted, else the cfg's stamp, else the demo prefix.
    like_pattern = "spec_example"
    override = QS_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        try:
            from quicksight_gen.common.l2 import load_instance
            like_pattern = str(load_instance(override).instance)
        except Exception:
            pass
    elif cfg.l2_instance_prefix:
        like_pattern = str(cfg.l2_instance_prefix)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    try:
        conn = connect_demo_db(cfg)
    except Exception as exc:
        try:
            target.write_text(
                format_skipped(
                    title=title,
                    dialect=dialect_str,
                    reason=f"could not connect: {exc!r}",
                ),
            )
        except OSError:
            pass
        return

    try:
        try:
            rows = fetch_top_queries(
                conn, cfg.dialect, like_pattern=like_pattern, top=50,
            )
        except Exception as exc:
            try:
                target.write_text(
                    format_skipped(
                        title=title,
                        dialect=dialect_str,
                        reason=(
                            f"stats view unavailable: {type(exc).__name__}: "
                            f"{exc}. Pre-req for postgres: ``CREATE "
                            f"EXTENSION pg_stat_statements;``. For "
                            f"oracle: SELECT on ``v$sqlstats``."
                        ),
                    ),
                )
            except OSError:
                pass
            return
    finally:
        try:
            conn.close()
        except Exception:
            pass

    try:
        target.write_text(
            format_top_queries_markdown(
                title=title,
                dialect=dialect_str,
                like_pattern=like_pattern,
                rows=rows,
            ),
        )
    except OSError:
        pass
