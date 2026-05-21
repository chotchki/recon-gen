"""Shared fixtures for end-to-end tests.

All e2e tests are skipped unless RECON_GEN_E2E=1 is set. This keeps
`pytest` fast and free of AWS dependencies by default.

Required env vars (or config.yaml):
    RECON_GEN_AWS_ACCOUNT_ID
    RECON_GEN_AWS_REGION
    RECON_GEN_DATASOURCE_ARN (or RECON_GEN_DEMO_DATABASE_URL)

Optional env vars for tuning:
    RECON_E2E_PAGE_TIMEOUT   — page load timeout in ms (default 30000)
    RECON_E2E_VISUAL_TIMEOUT — per-visual render timeout in ms (default 10000)
    RECON_E2E_IDENTITY_REGION — QuickSight identity region (default us-east-1)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_E2E_IDENTITY_REGION,
    RECON_E2E_PAGE_TIMEOUT,
    RECON_E2E_VISUAL_TIMEOUT,
    RECON_GEN_CONFIG,
    RECON_GEN_E2E,
    RECON_GEN_RUN_DIR,
    RECON_GEN_TEST_L2_INSTANCE,
)


def pytest_collection_modifyitems(config, items):
    """Skip all e2e tests unless RECON_GEN_E2E=1."""
    if RECON_GEN_E2E.get_or_none():
        return
    skip = pytest.mark.skip(reason="e2e tests disabled (set RECON_GEN_E2E=1)")
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

PAGE_TIMEOUT = RECON_E2E_PAGE_TIMEOUT.get_or_none() or 30000
VISUAL_TIMEOUT = RECON_E2E_VISUAL_TIMEOUT.get_or_none() or 10000
IDENTITY_REGION = RECON_E2E_IDENTITY_REGION.get_or_none() or "us-east-1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def cfg():
    """Load project config — checks the legacy single-file location, then
    the per-dialect copies (Phase P), then env vars.

    The candidate order favors the explicit single-file config before
    falling back to the dialect-specific files. Override with the
    ``RECON_GEN_CONFIG`` env var when both per-dialect files exist and
    you need to pin to one.
    """
    from recon_gen.common.config import load_config

    # Soft-fall: registry's must_be_file validator would raise on a
    # bad pin; the discovery loop below has fallback candidates.
    try:
        explicit = RECON_GEN_CONFIG.get_or_none()
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
def deployment_name(cfg) -> str:
    """Z.C — replaces the prior ``resource_prefix`` fixture; the
    deployment_name IS the single per-deploy QS-resource-ID prefix."""
    return cfg.deployment_name


@pytest.fixture(scope="session")
def qs_client(region):
    """Boto3 QuickSight client for the dashboard region."""
    import boto3
    return boto3.client("quicksight", region_name=region)


@pytest.fixture
def qs_driver(request, cfg, region, account_id):  # type: ignore[no-untyped-def]: return-type annotation would force a QsEmbedDriver import at module scope
    """X.2.q — ``QsEmbedDriver`` over a fresh WebKit page, for browser
    e2e tests that drive a deployed QuickSight dashboard through the
    ``DashboardDriver`` protocol (``open(dashboard_id)`` mints the embed
    URL). Skips cleanly when ``RECON_E2E_USER_ARN`` is unset (the runner
    derives it from ``cfg.auth.aws_profile``; export it for a direct
    ``pytest`` run). Function-scoped — embed URLs are single-use.

    AA.H.12 — thin wrapper around ``qs_driver_or_none`` (the shared
    lifecycle primitive that bundles get_user_arn gate + embed +
    capture hook). This fixture's only distinguishing policy: skip the
    test when QS is unavailable (single-renderer tests can't run
    without it).
    """
    from tests.e2e._drivers._lifecycle import qs_driver_or_none

    with qs_driver_or_none(
        request, account_id=account_id, region=region,
    ) as driver:
        if driver is None:
            pytest.skip("RECON_E2E_USER_ARN unavailable — cannot derive QS user ARN")
        yield driver


def _resolve_test_l2_instance():  # type: ignore[no-untyped-def]: return-type annotation would force an L2Instance import at module scope, slowing collection
    """Resolve the L2 instance the e2e tests should mirror.

    Honors ``RECON_GEN_TEST_L2_INSTANCE`` (the runner / release.yml inject
    it per-variant / per-release); falls back to the bundled
    ``default_l2_instance()`` (`spec_example`) when unset.

    Used by both the ``*_l2_prefix`` fixtures (for ID-string
    construction) and the ``*_app`` fixtures (so the tree the test
    walks has the same L2 prefix as the deployed resources).
    """
    from recon_gen.common.l2 import default_l2_instance
    from recon_gen.common.l2 import load_instance

    override = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


@pytest.fixture(scope="session")
def l2(cfg):  # type: ignore[no-untyped-def]: return-type annotation would force an L2Instance import at module scope
    """Session-scoped L2Instance matching what the deploy used.

    Mirrors the L2 ``json apply`` / ``data apply`` were driven with for
    the deployed resources. Tests that need to call production code
    that takes ``(cfg, l2)`` (e.g. ``apps/l1_dashboard/datasets.py``
    builders, used by ``tests/e2e/_picker_anchor.py``) depend on this
    fixture so they see the same L2 the deployed dashboard sees.

    AA.A.9 — added when ``fetch_anchor_row`` needed an L2 to call the
    dataset builder. Honors ``RECON_GEN_TEST_L2_INSTANCE`` via
    ``_resolve_test_l2_instance`` (same path the ``*_app`` fixtures
    use).
    """
    del cfg  # required as a fixture dep so collection order is stable
    return _resolve_test_l2_instance()


@pytest.fixture(scope="session", autouse=True)
def _refresh_matviews_once_per_session(cfg, l2):  # type: ignore[no-untyped-def]: see ``l2`` comment
    """AA.A.qs-triage.5.followon — refresh deployed-DB matviews once per
    test session so picker tests + agreement tests always see live data.

    The picker tests (``test_l1_additive_pickers.py``) read the live
    ``<prefix>_current_daily_balances`` and ``<prefix>_todays_exceptions``
    matviews to (a) build the Account dropdown's option universe and
    (b) source the anchor row the test pivots on. If a prior session
    left those matviews stale (e.g. an ``exceptions_only``-scope run
    that the picker test then reads as the demo seed), every dropdown
    sees ~2 accounts and the test fails on a row-survival assertion.

    The chain runner's ``seed_variant`` already runs ``data refresh``
    after ``data apply`` (per runner.py:2565), so this fixture is a
    safety net for direct ``pytest`` invocations + post-CLI-iteration
    flows where the operator ran ``data apply --execute`` without a
    matching ``data refresh --execute``.

    Idempotent (refresh on top of fresh matviews is a no-op cost-wise).
    Best-effort: any failure (no DB cfg, connection refused, missing
    matviews) is logged and the session continues — the tests will
    report their own DB-state-derived failures.
    """
    if not RECON_GEN_E2E.get_or_none():
        return
    # Under the runner, seed_variant already ran `data refresh`, so this is
    # redundant — and scope="session" means once PER XDIST WORKER, so N
    # desynchronized workers fire concurrent REFRESH MATERIALIZED VIEW
    # (AccessExclusiveLock) on the one shared deployed DB while reader tests
    # (e.g. audit apply) hold AccessShareLock, acquired in the opposite order
    # → DeadlockDetected (sibling of the 9f54b4d flake). RECON_GEN_RUN_DIR is
    # set iff we're under the runner → skip; direct pytest keeps the refresh.
    try:
        under_runner = RECON_GEN_RUN_DIR.get_or_none() is not None
    except EnvVarInvalid:
        under_runner = False
    if under_runner:
        return
    try:
        from recon_gen.common.db import connect_demo_db, execute_script
        from recon_gen.common.l2.schema import refresh_matviews_sql
    except ImportError as exc:
        print(f"runner: matview-refresh fixture skipped (import: {exc!r})")
        return
    try:
        conn = connect_demo_db(cfg)
    except Exception as exc:
        print(f"runner: matview-refresh fixture skipped (connect: {exc!r})")
        return
    try:
        sql = refresh_matviews_sql(
            l2, prefix=cfg.db_table_prefix, dialect=cfg.dialect,
        )
        with conn.cursor() as cur:
            execute_script(cur, sql, dialect=cfg.dialect)
        conn.commit()
        print(
            f"runner: matview-refresh fixture refreshed "
            f"{cfg.db_table_prefix}_* matviews on {cfg.dialect.name}"
        )
    except Exception as exc:
        print(f"runner: matview-refresh fixture FAILED ({exc!r}) — continuing")
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def inv_dashboard_id(deployment_name) -> str:
    """Z.C — single-prefix ``<deployment_name>-investigation-dashboard``
    (was M.2d.3's two-segment ``<resource_prefix>-<l2_prefix>-...``)."""
    return f"{deployment_name}-investigation-dashboard"


@pytest.fixture(scope="session")
def inv_analysis_id(deployment_name) -> str:
    return f"{deployment_name}-investigation-analysis"


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
def exec_dashboard_id(deployment_name) -> str:
    """Z.C — single-prefix; see ``inv_dashboard_id`` rationale."""
    return f"{deployment_name}-executives-dashboard"


@pytest.fixture(scope="session")
def exec_analysis_id(deployment_name) -> str:
    return f"{deployment_name}-executives-analysis"


@pytest.fixture(scope="session")
def exec_dataset_ids(exec_app) -> list[str]:
    """Executives dataset IDs derived from the tree (drift-resistant)."""
    return [ds.arn.rsplit("/", 1)[-1] for ds in exec_app.datasets]


# -- L1 dashboard fixtures (M.2c) --------------------------------------------
#
# Z.C — IDs are now `<deployment_name>-l1-<thing>`; the prior M.2d.3
# two-segment form (`<resource_prefix>-<l2_prefix>-...`) collapsed to
# one segment when deployment_name absorbed both roles.


@pytest.fixture(scope="session")
def l1_dashboard_id(deployment_name) -> str:
    return f"{deployment_name}-l1-dashboard"


@pytest.fixture(scope="session")
def l1_analysis_id(deployment_name) -> str:
    return f"{deployment_name}-l1-dashboard-analysis"


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
# Z.C — IDs are now `<deployment_name>-l2-flow-tracing[-analysis]`. L2FT's
# dashboard ID lacks the trailing ``-dashboard`` segment that L1 / Inv /
# Exec carry — the App's name is the suffix.


@pytest.fixture(scope="session")
def l2ft_l2_instance():
    """The loaded ``L2Instance`` the e2e session targets — same resolution
    as `l2ft_l2_prefix`, but the object, not just the prefix string."""
    from recon_gen.common.l2 import default_l2_instance
    from recon_gen.common.l2 import load_instance

    override = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
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
# that feature (spec_example declares both chains and templates; a fuzz seed
# or operator-supplied L2 may declare neither). The no-feature case rendering
# clean — empty table, vacuous dropdown, no QS error overlay — is already
# covered by the L2FT render tests, so no coverage is lost.
#
# Note: a non-empty *declared* list is necessary but not sufficient for the
# matview to have rows — a fuzz seed could declare a transfer template the
# auto-scenario can't materialize a firing for. Tests therefore ALSO keep
# their downstream "table started empty → skip"; this just fast-exits the
# obvious `declared zero` case (and documents the principle).
_L2FT_FEATURE_DECLARED = {
    "chains": "declared_chain_parents",
    "templates": "declared_template_names",
}


def require_l2ft_feature(l2_instance, feature: str) -> None:
    """`pytest.skip` if ``l2_instance`` declares zero of ``feature``
    (``"chains"`` | ``"templates"``). Call from an autouse fixture in an
    L2FT browser test module that only applies when that feature exists."""
    from recon_gen.apps.l2_flow_tracing import datasets as _l2ft_ds

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
def l2ft_dashboard_id(deployment_name) -> str:
    return f"{deployment_name}-l2-flow-tracing"


@pytest.fixture(scope="session")
def l2ft_analysis_id(deployment_name) -> str:
    return f"{deployment_name}-l2-flow-tracing-analysis"


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

    Honors ``RECON_GEN_TEST_L2_INSTANCE`` so the tree's dataset ARNs
    match the deployed resources' L2 prefix (release.yml's per-tag
    ``rel_<tag>``, the runner's variant ``sp_pg_aw``, etc.). Without
    this the hotfix-v8.8.0a23 derived-fixture pivot would have been
    a step backward — IDs would drift on every non-default L2 run.
    """
    from recon_gen.apps.investigation.app import build_investigation_app

    app = build_investigation_app(
        cfg, l2_instance=_resolve_test_l2_instance(),
    )
    app.emit_analysis()
    return app


@pytest.fixture(scope="session")
def exec_app(cfg):
    """Tree-built Executives App (post-emit, auto-IDs resolved).
    See ``inv_app`` for the L2-instance-honoring rationale."""
    from recon_gen.apps.executives.app import build_executives_app

    app = build_executives_app(
        cfg, l2_instance=_resolve_test_l2_instance(),
    )
    app.emit_analysis()
    return app


@pytest.fixture(scope="session")
def l1_app(cfg):
    """Tree-built L1 Reconciliation Dashboard App.

    Honors ``RECON_GEN_TEST_L2_INSTANCE`` — the same L2 the CLI's
    ``json apply`` was driven with for the deployed resources. Tree
    shape (and dataset ARNs) thus match the deployed shape exactly,
    making derived ``l1_dataset_ids`` ↔ deployed-DataSetId comparisons
    trivially correct. Post-emit so auto-IDs are resolved.
    """
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app

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
    from recon_gen.apps.l2_flow_tracing.app import (
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
#   - `qs`   — drives the *deployed* dashboard (`<deployment_name>-
#     <app>-...`), real data via the QS datasource. `dashboard_arg` is
#     the deployed dashboard ID. Skips when `RECON_E2E_USER_ARN` is unset
#     (no embed signer) or the dashboard isn't deployed.
#   - `app2` — drives a *locally-spun* App 2 server built from the same
#     `<app>_app` tree, reading the same DB (`cfg.demo_database_url`) via
#     `make_live_db_fetcher_for_app` — the "output" slot of the
#     `scenario → DB → output` pipeline. `dashboard_arg` is the local
#     slug. Skips when `cfg.demo_database_url` is unset.
#
# Function-scoped: the QS embed URL is single-use; the App 2 server spins
# in ~1–2 s, acceptable. See docs/audits/x_2_u_parametrized_driver_spike.md.


# AA.H.10 — moved to tests/e2e/_capture.py so the three QS-driver
# fixtures (qs_driver here, _parametrized_dashboard_driver here,
# per_dialect_qs_driver in test_audit_dashboard_agreement.py) can all
# import a single hook. Originally lived inline here and was wired
# only into _parametrized_dashboard_driver — the other two fixtures
# silently dropped failure-capture artifacts. Today's chain
# (20260516T203824Z) lost all 4 audit-agreement failures' artifacts
# for exactly that reason.
from tests.e2e._capture import maybe_capture_on_failure as _maybe_capture_on_failure  # noqa: E402


def _parametrized_dashboard_driver(  # type: ignore[no-untyped-def]: return-type annotation would force a driver import at module scope
    request, *, cfg, region, account_id, dashboard_id, app, short,
):
    if request.param == "qs":
        import boto3

        from tests.e2e._drivers._lifecycle import qs_driver_or_none

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
        # AA.H.12 — shared lifecycle: get_user_arn gate + QsEmbedDriver
        # embed + AA.H.10 capture-hook. Skip-on-None policy because
        # the [qs, app2] parametrize already covers the App2 leg
        # separately; the qs branch needs a real QS embed.
        with qs_driver_or_none(
            request, account_id=account_id, region=region,
        ) as driver:
            if driver is None:
                pytest.skip("RECON_E2E_USER_ARN unavailable — cannot derive QS user ARN")
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
            # AA.H.6 — see QS branch above.
            _maybe_capture_on_failure(request, driver)


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
# ``$RECON_GEN_RUN_DIR/db/<dialect>/top-queries.md`` at session
# teardown. ``Y.2.gate.f.4`` deletes the standalone script + the
# CI workflow steps that called it.
#
# Sidecar contract (matches c.2 / c.12): no-op when env unset, errors
# swallowed so the perf snapshot never breaks a passing test session.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def capture_top_queries(cfg, request):
    """Session-end perf-snapshot hook.

    Yields immediately; on teardown, if ``RECON_GEN_RUN_DIR`` is set AND
    the cfg has a demo_database_url, connects, runs the dialect's
    stats-view query, and writes a markdown table. SQLite is silently
    skipped (no equivalent stats view).

    The like-pattern defaults to the cfg's ``db_table_prefix`` (Z.C —
    was previously ``cfg.l2_instance_prefix`` or the loaded L2's
    ``instance`` field; both are gone). Falls back to ``spec_example``
    when cfg.db_table_prefix is somehow absent. That keeps multi-tenant
    shared-DB output narrowed to OUR queries.
    """
    yield

    # Sidecar contract — swallow EnvVarInvalid (a misconfigured env
    # var must not break a passing test session's teardown).
    try:
        run_dir_path = RECON_GEN_RUN_DIR.get_or_none()
    except EnvVarInvalid:
        return
    if run_dir_path is None:
        return
    run_dir = str(run_dir_path)
    if not cfg.demo_database_url:
        return

    from recon_gen._dev.perf import (
        dialect_name,
        fetch_top_queries,
        format_skipped,
        format_top_queries_markdown,
    )
    from recon_gen.common.db import connect_demo_db
    from recon_gen.common.sql import Dialect

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

    # Z.C — the substring filter is just cfg.db_table_prefix (the DB-
    # table-name prefix every emitted matview / table carries). Falls
    # back to the demo prefix only if cfg somehow has no value.
    like_pattern = cfg.db_table_prefix or "spec_example"

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
