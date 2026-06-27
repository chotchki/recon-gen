"""Shared fixtures for end-to-end tests.

DJ.1 (2026-06-15) — the RECON_GEN_E2E env-var gate retired. e2e tests
now collect by default. Session-scoped fixtures (qs_deployed,
pg_container_url, dex_container_url) handle their own AWS / cfg-shape
skips per POLICY 1 (CI ≡ local, no env-var divergence).

Required cfg.yaml fields for the QS-touching path:
    aws.account_id
    aws.region
    auth.aws.quicksight_user_arn (or derived via aws.profile + STS)

Optional env vars for tuning:
    RECON_E2E_PAGE_TIMEOUT   — page load timeout in ms (default 30000)
    RECON_E2E_VISUAL_TIMEOUT — per-visual render timeout in ms (default 10000)
    RECON_E2E_IDENTITY_REGION — QuickSight identity region (default us-east-1)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from recon_gen.common.config import Config
from recon_gen.common.env_keys import (
    EnvVarInvalid,
    RECON_E2E_IDENTITY_REGION,
    RECON_E2E_PAGE_TIMEOUT,
    RECON_E2E_VISUAL_TIMEOUT,
    RECON_GEN_CONFIG,
    RECON_GEN_QS_CONFIG,
    RECON_GEN_RUN_DIR,
    RECON_GEN_SKIP_QS_DEPLOY,
    RECON_GEN_TEST_L2_INSTANCE,
)

if TYPE_CHECKING:
    # BE.7.B — type-only imports for boto3-stubs annotations. Lazy
    # import to avoid pulling mypy-boto3 modules at test-collection
    # time (they're [dev] deps; not present in production wheel).
    # BE.7.C.2 slice 1 — type-only imports for the App/L2Instance/
    # driver fixtures. Quoted-string annotations keep the test
    # process light at collection time.
    from mypy_boto3_quicksight.client import QuickSightClient

    from recon_gen.common.l2 import L2Instance
    from recon_gen.common.tree import App
    from tests.e2e._drivers import DashboardDriver, QsEmbedDriver


#: Fixture names that imply an AWS dependency. If NONE of the
#: collected tests in a session pulls one of these into its fixture
#: closure, the session doesn't need AWS — the session-scope autouse
#: fixtures (`_refresh_matviews_once_per_session`, `_qs_pre_warm_dashboards`)
#: + `cfg`'s `_pin_cfg_to_kv_as_of` step skip out before burning ~30s
#: each on TCP connect timeouts to an unreachable Aurora / expired
#: AWS creds. Surfaced by BV.3.3.c.bug1 triage: the sqlite-only
#: `test_bv33c_full_registry_walk_sqlite` wasted ~90s/run on these
#: leaks before its actual test logic fired.
_AWS_DEPENDENT_FIXTURE_NAMES: frozenset[str] = frozenset({
    "qs_client", "qs_driver", "qs_user_arn", "account_id",
    "l1_dashboard_id", "inv_dashboard_id", "exec_dashboard_id",
    "l2ft_dashboard_id",
})


def _session_needs_aws(session: pytest.Session | None) -> bool:
    """Returns True if any collected test in the session pulls an
    AWS-dependent fixture into its closure. Cached per-session on a
    `_recon_aws_required` attribute. Defaults to True when session is
    None (direct-fixture-request path, no collection info yet) — the
    safe choice is to fire AWS work; the gate's purpose is avoiding
    waste, not gating correctness."""
    if session is None:
        return True
    cached = getattr(session, "_recon_aws_required", None)
    if cached is not None:
        return bool(cached)
    needs_aws = False
    for item in getattr(session, "items", ()):
        fixtureinfo = getattr(item, "_fixtureinfo", None)
        if fixtureinfo is None:
            continue
        closure = getattr(fixtureinfo, "names_closure", ()) or ()
        if any(name in _AWS_DEPENDENT_FIXTURE_NAMES for name in closure):
            needs_aws = True
            break
    session._recon_aws_required = needs_aws  # type: ignore[attr-defined]: cache attribute attached to session at runtime per BV.3.3.f gate
    return needs_aws


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None],
) -> Iterator[None]:
    """Expose per-phase test outcome to fixtures via item.rep_<phase>.

    M.4.1.f's harness fixtures consult ``item.rep_call.failed`` during
    teardown to decide whether to dump the failure triage manifest.
    Standard pytest idiom.
    """
    del call  # unused; required by the pytest hook signature
    outcome = yield  # pyright: ignore[reportUnknownVariableType]: pytest hookwrapper yield is Generator[None, _Result, None] — _Result private
    rep: Any = outcome.get_result()  # type: ignore[attr-defined]: third-party stub or test scaffolding cascade
    setattr(item, f"rep_{rep.when!s}", rep)  # pyright: ignore[reportUnknownMemberType]: outcome.get_result() return is Any-cascaded


# ---------------------------------------------------------------------------
# Timeout configuration
# ---------------------------------------------------------------------------

PAGE_TIMEOUT = RECON_E2E_PAGE_TIMEOUT.get_or_none() or 30000
VISUAL_TIMEOUT = RECON_E2E_VISUAL_TIMEOUT.get_or_none() or 10000
IDENTITY_REGION = RECON_E2E_IDENTITY_REGION.get_or_none() or "us-east-1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _load_session_cfg(request: pytest.FixtureRequest) -> Config:
    """CB.17.d — cfg-loading helper extracted from the `cfg` fixture body.

    Tier conftests (``tests/e2e/{db,app2,qs_api,qs_browser}/conftest.py``)
    override the ``cfg`` fixture to substitute the session-scoped
    container URL when the runner hasn't injected
    ``RECON_GEN_DEMO_DATABASE_URL`` via env (the legacy ``cmd_up_to`` cell
    loop path). Each override calls this helper to get the canonical
    base cfg, then layers the substitution.

    Pulled out (vs an in-fixture override) because pytest fixture
    overrides that name a parent's fixture-name in their deps create a
    resolution cycle.
    """
    from recon_gen.common.config import load_config

    # Soft-fall: registry's must_be_file validator would raise on a
    # bad pin; the discovery loop below has fallback candidates.
    try:
        explicit = RECON_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        explicit = None
    if explicit is not None:
        loaded = load_config(str(explicit))
    else:
        candidates = (
            Path("config.yaml"),
            Path("run/config.yaml"),
            Path("run/config.postgres.yaml"),
            Path("run/config.oracle.yaml"),
        )
        loaded = None
        for candidate in candidates:
            if candidate.exists():
                loaded = load_config(str(candidate))
                break
        if loaded is None:
            loaded = load_config(None)
    # BV.3.3.f — `_pin_cfg_to_kv_as_of` connects to the cfg-pinned demo
    # DB (which in production is Aurora). When this session contains no
    # AWS-dependent tests, skip the pin to avoid a ~30s TCP-connect
    # timeout. Falls through unpinned — sqlite/local-PG tests bring
    # their own DB via tmp_path or docker.
    if not _session_needs_aws(request.session):
        return loaded
    return _pin_cfg_to_kv_as_of(loaded)


def _substitute_container_url(  # pyright: ignore[reportUnusedFunction]: re-exported via `from tests.e2e.conftest import _substitute_container_url` in the four tier conftests (db/app2/qs_api/qs_browser) — pyright doesn't follow that cross-module path
    loaded: Config,
    request: pytest.FixtureRequest,
) -> Config:
    """CB.17.d — swap ``cfg.db.url`` for the matching
    session-scoped container URL.

    Honors the legacy ``cmd_up_to`` cell-loop path: when
    ``RECON_GEN_DEMO_DATABASE_URL`` env is set (the runner's
    ``setup_variant`` injected the per-cell URL), ``Config``'s
    env-override has already substituted the cell URL into
    ``loaded.db.url`` — return unchanged. When unset
    (thin path), substitute the container URL by dialect.

    Container fixtures are resolved via ``request.getfixturevalue``
    so ONLY the matching dialect's container spins. A static signature
    dep on both ``pg_container_url`` + ``oracle_container_url`` would
    force Oracle to spin even in a POSTGRES-only session — Oracle 19c
    is slow + heavy + can fail to boot on a resource-constrained host,
    so lazy dispatch matters.

    DuckDB passthrough: the yaml URL (``duckdb:///...``) is file-based
    and neither container fixture fires.
    """
    import dataclasses  # noqa: PLC0415

    from recon_gen.common.env_keys import RECON_GEN_DEMO_DATABASE_URL  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    if RECON_GEN_DEMO_DATABASE_URL.get_or_none():
        return loaded
    if loaded.db.dialect is Dialect.POSTGRES:
        pg_url = cast(str, request.getfixturevalue("pg_container_url"))
        return dataclasses.replace(loaded, db=dataclasses.replace(loaded.db, url=pg_url))
    if loaded.db.dialect is Dialect.ORACLE:
        oracle_url = cast(str, request.getfixturevalue("oracle_container_url"))
        return dataclasses.replace(loaded, db=dataclasses.replace(loaded.db, url=oracle_url))
    return loaded


@pytest.fixture(scope="session")
def cfg(request: pytest.FixtureRequest) -> Config:
    """Load project config — checks the legacy single-file location, then
    the per-dialect copies (Phase P), then env vars.

    The candidate order favors the explicit single-file config before
    falling back to the dialect-specific files. Override with the
    ``RECON_GEN_CONFIG`` env var when both per-dialect files exist and
    you need to pin to one.

    BG.3 / BG.6 anchor-drift fix: after loading, pin
    ``test_generator.end_date`` to the demo DB's ``<prefix>_config_kv``
    ``as_of`` row (stamped at ``data apply`` time). See
    ``_load_session_cfg`` docstring for the helper-extract rationale
    (CB.17.d strangler).
    """
    return _load_session_cfg(request)


# ---------------------------------------------------------------------------
# CB.17.b — cfg bridge from the shared-container fixtures
#
# Goal: produce a `Config` whose ``demo_database_url`` is sourced from the
# matching shared container fixture, with everything else (deployment_name,
# aws_account_id, theme, auth) inherited from the yaml-loaded `cfg`. Test
# opts in by declaring `cfg_with_container_url` instead of `cfg` in its
# signature.
#
# CB.17.d will migrate ``isolated_cfg`` to consume this directly so
# prefix-per-worker isolation plugs straight into the shared session
# container. Additive today.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cfg_with_container_url(
    cfg: Config,
    pg_container_url: str,
    oracle_container_url: str,
) -> Config:
    """Yield `cfg` with `demo_database_url` swapped for the matching
    shared-container fixture's URL.

    Dispatch by ``cfg.db.dialect``: POSTGRES → pg_container_url; ORACLE →
    oracle_container_url; DUCKDB → passthrough (file-based; the yaml URL
    is authoritative).

    Both container fixtures are pulled in unconditionally so this fixture
    works regardless of which dialect lands. The env-URL fast path on the
    underlying fixtures (set ``RECON_GEN_DEMO_DATABASE_URL_PG`` / ``_OR``)
    means non-needed containers don't actually spin Docker.
    """
    import dataclasses  # noqa: PLC0415

    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    if cfg.db.dialect is Dialect.POSTGRES:
        return dataclasses.replace(cfg, db=dataclasses.replace(cfg.db, url=pg_container_url))
    if cfg.db.dialect is Dialect.ORACLE:
        return dataclasses.replace(cfg, db=dataclasses.replace(cfg.db, url=oracle_container_url))
    # DuckDB falls through; the yaml-loaded URL is the source.
    return cfg


def _pin_cfg_to_kv_as_of(cfg: Config) -> Config:
    """Pin ``cfg.test.generator.end_date`` to the demo DB's kv ``as_of``
    row when reachable. See ``cfg`` fixture docstring for the why.

    Idempotent: already-pinned ``end_date`` round-trips unchanged when
    kv.as_of agrees. Silently falls through (returns ``cfg`` unmodified)
    when the DB isn't reachable or the kv row is absent — offline emit
    paths must not crash here.
    """
    import dataclasses
    import sys

    try:
        from recon_gen.common.db import connect_demo_db
        from recon_gen.common.l2.config_table import get_as_of
        conn = connect_demo_db(cfg)
    except Exception as exc:
        print(
            f"[cfg.pin_to_kv_as_of] DB unreachable ({exc!r}); falling "
            f"through to cfg.test.generator.end_date={cfg.test.generator.end_date!r}",
            file=sys.stderr,
        )
        return cfg

    try:
        as_of = get_as_of(conn, prefix=cfg.db.table_prefix)
    except Exception as exc:
        print(
            f"[cfg.pin_to_kv_as_of] kv.as_of read failed ({exc!r}); "
            f"falling through to cfg.test.generator.end_date="
            f"{cfg.test.generator.end_date!r}",
            file=sys.stderr,
        )
        return cfg
    finally:
        try:
            conn.close()
        except Exception:
            pass

    pinned_tg = dataclasses.replace(cfg.test.generator, end_date=as_of.date())
    return dataclasses.replace(cfg, test=dataclasses.replace(cfg.test, generator=pinned_tg))


@pytest.fixture(scope="session")
def account_id(cfg: Config) -> str:
    return cfg.aws.account_id


@pytest.fixture(scope="session")
def region(cfg: Config) -> str:
    return cfg.aws.region


@pytest.fixture(scope="session")
def deployment_name(cfg: Config) -> str:
    """Z.C — replaces the prior ``resource_prefix`` fixture; the
    deployment_name IS the single per-deploy QS-resource-ID prefix."""
    return cfg.aws.deployment_name


@pytest.fixture(scope="session")
def qs_client(region: str) -> "QuickSightClient":
    """Boto3 QuickSight client for the dashboard region.

    Return type is the boto3-stubs ``QuickSightClient`` TypedClient so
    pyright resolves the rich AWS-API shapes downstream — e.g.
    ``qs_client.describe_dashboard_definition(...)`` returns the
    proper ``DescribeDashboardDefinitionResponseTypeDef`` instead of
    ``Unknown``. BE.7.B annotation pass (2026-05-26): without this,
    every dashboard-definition consumer cascades into reportUnknown*
    noise.
    """
    import importlib.util
    if importlib.util.find_spec("boto3") is None:
        pytest.skip(  # DV.6 — QS needs the optional [quicksight] extra
            "boto3 not installed — QuickSight tests need recon-gen[quicksight]"
        )
    import boto3
    return boto3.client("quicksight", region_name=region)  # pyright: ignore[reportUnknownMemberType]: boto3.client dynamic service overload


@pytest.fixture
def qs_driver(
    request: pytest.FixtureRequest,
    cfg: Config,
    region: str,
    account_id: str,
) -> Iterator["QsEmbedDriver"]:
    """X.2.q — ``QsEmbedDriver`` over a fresh WebKit page, for browser
    e2e tests that drive a deployed QuickSight dashboard through the
    ``DashboardDriver`` protocol (``open(dashboard_id)`` mints the embed
    URL). Skips cleanly when ``RECON_E2E_USER_ARN`` is unset (the runner
    derives it from ``cfg.auth.aws.profile``; export it for a direct
    ``pytest`` run). Function-scoped — embed URLs are single-use.

    AA.H.12 — thin wrapper around ``qs_driver_or_none`` (the shared
    lifecycle primitive that bundles get_user_arn gate + embed +
    capture hook). This fixture's only distinguishing policy: skip the
    test when QS is unavailable (single-renderer tests can't run
    without it).
    """
    import importlib.util
    if importlib.util.find_spec("boto3") is None:
        pytest.skip(  # DV.6 — QS needs the optional [quicksight] extra
            "boto3 not installed — QuickSight tests need recon-gen[quicksight]"
        )
    from tests.e2e._drivers._lifecycle import qs_driver_or_none

    with qs_driver_or_none(
        request, cfg=cfg, account_id=account_id, region=region,
    ) as driver:
        if driver is None:
            pytest.skip("RECON_E2E_USER_ARN unavailable — cannot derive QS user ARN")
        yield driver


def _resolve_test_l2_instance() -> "L2Instance":
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
def l2(cfg: Config) -> "L2Instance":
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
def _refresh_matviews_once_per_session(  # pyright: ignore[reportUnusedFunction]: pytest autouse fixture — invoked by pytest via name, not directly accessed
    request: pytest.FixtureRequest, cfg: Config, l2: "L2Instance",
) -> None:
    """AA.A.qs-triage.5.followon — refresh deployed-DB matviews once per
    test session so picker tests + agreement tests always see live data.

    The picker tests (``test_l1_additive_pickers.py``) read the live
    ``<prefix>_current_daily_balances`` and ``<prefix>_l1_exceptions``
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
    # BV.3.3.f — skip when no AWS-dependent test runs this session.
    # The cfg's demo DB (Aurora in production) is irrelevant for
    # sqlite/local-PG tests that bring their own DB; the connect-
    # timeout otherwise burns ~30s per session.
    if not _session_needs_aws(request.session):
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
            l2, prefix=cfg.db.table_prefix, dialect=cfg.db.dialect,
        )
        with conn.cursor() as cur:
            execute_script(cur, sql, dialect=cfg.db.dialect)
        conn.commit()
        print(
            f"runner: matview-refresh fixture refreshed "
            f"{cfg.db.table_prefix}_* matviews on {cfg.db.dialect.name}"
        )
    except Exception as exc:
        print(f"runner: matview-refresh fixture FAILED ({exc!r}) — continuing")
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def inv_dashboard_id(deployment_name: str) -> str:
    """Z.C — single-prefix ``<deployment_name>-investigation-dashboard``
    (was M.2d.3's two-segment ``<resource_prefix>-<l2_prefix>-...``)."""
    return f"{deployment_name}-investigation-dashboard"


@pytest.fixture(scope="session")
def inv_analysis_id(deployment_name: str) -> str:
    return f"{deployment_name}-investigation-analysis"


@pytest.fixture(scope="session")
def inv_dataset_ids(inv_app: "App") -> list[str]:
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
def exec_dashboard_id(deployment_name: str) -> str:
    """Z.C — single-prefix; see ``inv_dashboard_id`` rationale."""
    return f"{deployment_name}-executives-dashboard"


@pytest.fixture(scope="session")
def exec_analysis_id(deployment_name: str) -> str:
    return f"{deployment_name}-executives-analysis"


@pytest.fixture(scope="session")
def exec_dataset_ids(exec_app: "App") -> list[str]:
    """Executives dataset IDs derived from the tree (drift-resistant)."""
    return [ds.arn.rsplit("/", 1)[-1] for ds in exec_app.datasets]


# -- L1 dashboard fixtures (M.2c) --------------------------------------------
#
# Z.C — IDs are now `<deployment_name>-l1-<thing>`; the prior M.2d.3
# two-segment form (`<resource_prefix>-<l2_prefix>-...`) collapsed to
# one segment when deployment_name absorbed both roles.


@pytest.fixture(scope="session")
def l1_dashboard_id(deployment_name: str) -> str:
    return f"{deployment_name}-l1-dashboard"


@pytest.fixture(scope="session")
def l1_analysis_id(deployment_name: str) -> str:
    return f"{deployment_name}-l1-dashboard-analysis"


@pytest.fixture(scope="session")
def l1_dataset_ids(l1_app: "App") -> list[str]:
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
def l2ft_l2_instance() -> "L2Instance":
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


def require_l2ft_feature(l2_instance: "L2Instance", feature: str) -> None:
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
def l2ft_dashboard_id(deployment_name: str) -> str:
    return f"{deployment_name}-l2-flow-tracing"


@pytest.fixture(scope="session")
def l2ft_analysis_id(deployment_name: str) -> str:
    return f"{deployment_name}-l2-flow-tracing-analysis"


@pytest.fixture(scope="session", autouse=True)
def qs_deployed(  # pyright: ignore[reportUnusedFunction]: pytest autouse fixture — invoked by pytest via name, not directly accessed
    request: pytest.FixtureRequest,
    cfg: Config,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """DI phase — idempotent QS deploy. Fires once per pytest session;
    cross-xdist-worker rendezvous via a filesystem sentinel + FileLock
    (mirrors ``tests/conftest.py::_install_pgcrypto_under_filelock``).

    POLICY 1 (single source of truth): both ``./run_tests.sh up_to=*``
    and ``./run_tests.sh triage`` reach QS deploy through this fixture
    — neither orchestrator dispatches deploy directly. ``cmd_up_to``
    retired the ``deploy`` chain layer; the qs_api + qs_browser layers'
    pytest invocations transitively fire this fixture at session start.

    Always-apply (NOT detect-then-apply). The ``recon-gen json apply
    --execute`` body is delete-then-create end-to-end
    (``common/deploy.py:380``); that covers fresh QS, healthy QS, and
    half-failed CREATION_FAILED partial state with one body. The
    sentinel only prevents re-firing within the same pytest session.

    Gate ordering (collection-time skip cascade):

    1. ``RECON_GEN_SKIP_QS_DEPLOY`` set → return (operator escape
       hatch; "I deployed manually 30s ago, just run the test").
    2. ``_session_needs_aws(session)`` returns False → return (db-tier
       and app2-tier sessions inherit this conftest but their fixture
       closures don't touch AWS).

    Wall cost: ~30-60s for full QS delete+create on Sasquatch with
    four apps. Single fire per session under 16-worker xdist (vs 16×
    if not gated).
    """
    if RECON_GEN_SKIP_QS_DEPLOY.get_or_none():
        return
    if not _session_needs_aws(request.session):
        return
    # DV.6 — boto3 ships only with the optional [quicksight] extra. On a
    # no-QS install the deploy can't run, so return cleanly: the session's
    # non-QS tiers pass and the [qs] params skip via the qs_driver gate.
    # QS is opt-in now, so this is the designed skip, not a POLICY-2 defer.
    import importlib.util
    if importlib.util.find_spec("boto3") is None:
        return

    # xdist rendezvous — mirrors ``_install_pgcrypto_under_filelock``
    # (``tests/conftest.py:1098``). All workers race here; first to
    # acquire the lock deploys, others see the sentinel and bail.
    from filelock import FileLock  # noqa: PLC0415 — lazy

    root_tmp = tmp_path_factory.getbasetemp().parent
    sentinel = root_tmp / "qs-deployed.sentinel"
    lock_path = str(sentinel) + ".lock"
    # Match the qs_browser layer's hang threshold so the lock acquire
    # can't outlast the watchdog.
    with FileLock(lock_path, timeout=900):
        if sentinel.is_file():
            return

        # Output dir under the runner's run_dir when available; else tmp.
        run_dir_env = RECON_GEN_RUN_DIR.get_or_none()
        out_dir = (
            Path(run_dir_env) / "deploy" / "out"
            if run_dir_env
            else tmp_path_factory.mktemp("qs-deploy-out")
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        # Resolve the cfg the deploy should use. ``RECON_GEN_QS_CONFIG``
        # (hotchkiss.io URL) wins when present — that's what the chain
        # + triage container-spin both export. Else fall back to the
        # operator-authored cfg already loaded into ``cfg``.
        qs_cfg_str = RECON_GEN_QS_CONFIG.get_or_none()
        if qs_cfg_str is not None:
            deploy_cfg_path = str(qs_cfg_str)
        else:
            cfg_str = RECON_GEN_CONFIG.get_or_none()
            if cfg_str is None:
                pytest.fail(
                    "qs_deployed: no cfg available — set "
                    "RECON_GEN_CONFIG or RECON_GEN_QS_CONFIG. The "
                    "runner exports these via _setup_thin_chain_"
                    "environment; bare pytest needs an export."
                )
            deploy_cfg_path = str(cfg_str)
        _ = cfg  # consumed only so the session-cfg gate fires; deploy uses the path

        l2_path = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
        if l2_path is None:
            pytest.fail(
                "qs_deployed: RECON_GEN_TEST_L2_INSTANCE unset; "
                "deploy needs an L2. The runner exports it via "
                "_setup_thin_chain_environment; bare pytest needs "
                "an export."
            )

        # Subprocess invocation (parity with ``_build_deploy_command``).
        # Subprocess form gives the fixture a real returncode + stderr
        # to surface; in-process would couple this conftest to the
        # full ``recon-gen json apply`` body (click decorators, deploy
        # helper, app-builder fan-out) which isn't worth the ~1s
        # subprocess overhead.
        import shutil
        import subprocess
        recon_gen_bin = shutil.which("recon-gen")  # typing-smell: ignore[no-inline-production-constants]: literal binary name, not the MANAGED_TAG_VALUE — a rename of the CLI bin would surface here loudly via PATH-not-found, which is exactly what we want
        if recon_gen_bin is None:
            # ``.venv/bin/recon-gen`` is the canonical path when no
            # PATH-installed binary exists (matches the runner's
            # ``_VENV_BIN / "recon-gen"`` pattern).
            from pathlib import Path as _P  # noqa: PLC0415 — lazy
            repo_root = _P(__file__).resolve().parents[2]
            recon_gen_bin = str(repo_root / ".venv" / "bin" / "recon-gen")  # typing-smell: ignore[no-inline-production-constants]: literal binary name, not the MANAGED_TAG_VALUE — sibling of the shutil.which lookup above
        argv = [
            recon_gen_bin, "json", "apply",
            "--execute",
            "-c", deploy_cfg_path,
            "--l2", str(l2_path),
            "-o", str(out_dir),
        ]
        # Strip RECON_GEN_DEMO_DATABASE_URL{,_PG,_OR} from the
        # subprocess env. Cmd_triage / cmd_up_to's container spin
        # sets these to the LOCAL (127.0.0.1 / localhost) URL so the
        # in-process pytest db/app2-tier tests can reach the
        # testcontainers PG. The QS-side cfg (qs.yaml) carries the
        # hotchkiss.io URL so QS in us-east-1 can route to the dev
        # box. `recon-gen json apply`'s cfg loader applies env
        # overrides AFTER cfg-from-file → the inherited localhost
        # env wins over the QS cfg → `CreateDataSource` fails with
        # "Unable to route to the host address localhost". The chain
        # handled this via env-pop in `cmd_up_to`'s deploy step
        # dispatch; the fixture refactor missed carrying that over.
        # POLICY 1: single source of truth → fixture owns the env
        # surgery instead of duplicating the logic in cmd_triage.
        deploy_env = os.environ.copy()
        for k in (
            "RECON_GEN_DEMO_DATABASE_URL",
            "RECON_GEN_DEMO_DATABASE_URL_PG",
            "RECON_GEN_DEMO_DATABASE_URL_OR",
        ):
            deploy_env.pop(k, None)
        print(f"qs_deployed: invoking {' '.join(argv)}")
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv, capture_output=True, text=True, check=False, env=deploy_env,
        )
        if result.returncode != 0:
            # On failure DON'T write the sentinel — next session re-
            # deploys (delete-then-create handles partial state).
            pytest.fail(
                f"qs_deployed: deploy subprocess failed rc="
                f"{result.returncode}\nstdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

        sentinel.touch()
        print(
            f"qs_deployed: deploy complete (cfg={deploy_cfg_path}) "
            f"-> sentinel {sentinel}"
        )


@pytest.fixture(scope="session", autouse=True)
def _qs_pre_warm_dashboards(  # pyright: ignore[reportUnusedFunction]: pytest autouse fixture — invoked by pytest via name, not directly accessed
    request: pytest.FixtureRequest,
    qs_deployed: None,
) -> None:
    """BL.3 follow-on (Task #466 mitigation): pre-warm each deployed
    dashboard via ``describe_dashboard_definition`` ONCE at session
    start. Forces QS to load the full analysis definition into its
    cache before the first browser-tier test fires.

    Why: the Sasquatch L1 dashboard render flake intermittently
    surfaces as "visual empty on first render after fresh deploy" —
    QS-side cache staleness, not data. Touching the definition once
    per dashboard at session start gives QS time to materialize
    before tests start asserting. Cheap (one API call per
    dashboard); only fires when ``QS_GEN_E2E=1`` (else skipped) so
    unit / non-e2e sessions don't pay the cost.

    Combined with the runner's bumped ``--reruns-delay`` (10s →
    60s; ``_dev/runner.py``), this should cut the bedrock flake
    rate substantially without requiring per-test retries.

    Phase BM (2026-05-28): take ``deployment_name`` directly and
    compute each ``<deployment>-<app>-dashboard`` id locally instead
    of depending on the per-app dashboard_id fixtures. The
    ``inv_dashboard_id`` fixture is module-scope-overridable in the
    per-renderer agreement producers under ``tests/e2e/qs_browser/``
    (the isolated cfg per BL.0), which collides with this session-
    scope fixture's resolution and raises ``ScopeMismatch`` on
    collection. Computing the IDs here sidesteps the override without
    breaking the BL.0 isolation.

    DI phase — declares ``qs_deployed: None`` to force pre-warm to
    run AFTER the QS deploy fixture lands; without this dep, both
    session-autouse fixtures order arbitrarily and pre-warm could
    fire against a stale (or missing) dashboard set.
    """
    del qs_deployed  # consumed via the param ordering dep only
    # BV.3.3.f — skip when no AWS-dependent test runs this session.
    # Lazy-request the AWS fixtures from inside the body so this
    # autouse fixture's own parameter list doesn't contaminate the
    # closure check (declaring qs_client/account_id as params would
    # make every e2e test pull them transitively and the gate would
    # always evaluate True).
    if not _session_needs_aws(request.session):
        return
    cfg = cast("Config", request.getfixturevalue("cfg"))
    qs_client = cast("QuickSightClient", request.getfixturevalue("qs_client"))
    account_id = cast(str, request.getfixturevalue("account_id"))
    deployment_name = cast(str, request.getfixturevalue("deployment_name"))
    if not cfg.aws.account_id or not account_id:
        return
    dashboard_ids = (
        ("l1", f"{deployment_name}-l1-dashboard"),
        ("inv", f"{deployment_name}-investigation-dashboard"),
        ("exec", f"{deployment_name}-executives-dashboard"),
        ("l2ft", f"{deployment_name}-l2-flow-tracing"),
    )
    for label, dashboard_id in dashboard_ids:
        try:
            qs_client.describe_dashboard_definition(
                AwsAccountId=account_id, DashboardId=dashboard_id,
            )
        except qs_client.exceptions.ResourceNotFoundException:
            # Not every L2 instance deploys every dashboard (e.g.
            # the isolated_inv test cfg may only deploy
            # investigation); skip silently.
            continue
        except Exception as exc:  # noqa: BLE001
            # Pre-warm is best-effort; log the failure but don't
            # gate the session on it.
            print(
                f"qs-prewarm[{label} {dashboard_id}] non-fatal: "
                f"{type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# Tree-built App fixtures (L.11)
#
# Session-scoped because the tree is pure, in-memory, and identical for
# every test that consumes it. Tests walk these to derive expected sheet
# names / visual titles / filter group ids / parameter names — the tree
# is the source of truth, not a parallel hand-maintained list.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def inv_app(cfg: Config) -> "App":
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
def exec_app(cfg: Config) -> "App":
    """Tree-built Executives App (post-emit, auto-IDs resolved).
    See ``inv_app`` for the L2-instance-honoring rationale."""
    from recon_gen.apps.executives.app import build_executives_app

    app = build_executives_app(
        cfg, l2_instance=_resolve_test_l2_instance(),
    )
    app.emit_analysis()
    return app


@pytest.fixture(scope="session")
def l1_app(cfg: Config) -> "App":
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
def l2ft_app(cfg: Config) -> "App":
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
#     `<app>_app` tree, reading the same DB (`cfg.db.url`) via
#     `make_live_db_fetcher_for_app` — the "output" slot of the
#     `scenario → DB → output` pipeline. `dashboard_arg` is the local
#     slug. Skips when `cfg.db.url` is unset.
#
# Function-scoped: the QS embed URL is single-use; the App 2 server spins
# in ~1–2 s, acceptable. See docs/audits/x_2_u_parametrized_driver_spike.md.


# AA.H.10 — moved to tests/e2e/_capture.py so the QS-driver
# fixtures (qs_driver here, _parametrized_dashboard_driver here) can
# all import a single hook. Originally lived inline here and was
# wired only into _parametrized_dashboard_driver — qs_driver
# silently dropped failure-capture artifacts.
from tests.e2e._capture import maybe_capture_on_failure as _maybe_capture_on_failure  # noqa: E402


def _parametrized_dashboard_driver(
    request: pytest.FixtureRequest,
    *,
    cfg: Config,
    app: "App",
    short: str,
) -> Iterator[tuple["DashboardDriver", str]]:
    # DW.6 — app2-only. The historical `[qs, app2]` parametrize collapsed
    # to `[app2]` when QuickSight was removed; App2 is the sole renderer.
    # The test↔driver split STAYS ([[feedback_keep_test_driver_split]]):
    # tests still speak `DashboardDriver` verbs through `App2Driver`,
    # never raw Playwright. The fixtures keep a single-element
    # `params=["app2"]` so the `[app2]` callspec id survives (any
    # nodeid references stay valid).
    if not cfg.db.url:
        pytest.skip("no cfg.db.url — the app2 leg reads the live DB")
    from tests.e2e._drivers import App2Driver
    from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

    assert app.analysis is not None
    data_fetcher, options_search_fetcher = make_live_db_fetchers_for_app(
        tree_app=app, cfg=cfg,
    )
    with App2Driver.serving(
        cfg=cfg,
        tree_app=app, sheet=app.analysis.sheets[0],
        data_fetcher=data_fetcher, options_search_fetcher=options_search_fetcher,
        dashboard_id=short, dashboard_title=f"{short} (live)",
    ) as driver:
        yield driver, short
        # AA.H.6 — failure-capture hook (screenshot/dom/console/etc.).
        _maybe_capture_on_failure(request, driver)


@pytest.fixture(params=["app2"])
def l1_dashboard_driver(
    request: pytest.FixtureRequest,
    cfg: Config,
    l1_app: "App",
) -> Iterator[tuple["DashboardDriver", str]]:
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, app=l1_app, short="l1",
    )


@pytest.fixture(params=["app2"])
def inv_dashboard_driver(
    request: pytest.FixtureRequest,
    cfg: Config,
    inv_app: "App",
) -> Iterator[tuple["DashboardDriver", str]]:
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, app=inv_app, short="inv",
    )


@pytest.fixture(params=["app2"])
def exec_dashboard_driver(
    request: pytest.FixtureRequest,
    cfg: Config,
    exec_app: "App",
) -> Iterator[tuple["DashboardDriver", str]]:
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, app=exec_app, short="exec",
    )


@pytest.fixture(params=["app2"])
def l2ft_dashboard_driver(
    request: pytest.FixtureRequest,
    cfg: Config,
    l2ft_app: "App",
) -> Iterator[tuple["DashboardDriver", str]]:
    yield from _parametrized_dashboard_driver(
        request, cfg=cfg, app=l2ft_app, short="l2ft",
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
def warm_aurora(cfg: Config) -> None:
    """Pre-warm Aurora before any e2e visual hits the dashboard."""
    if not cfg.db.url:
        return
    try:
        import psycopg
    except ImportError:
        return
    try:
        conn = psycopg.connect(cfg.db.url, connect_timeout=60)
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
def capture_top_queries(
    cfg: Config, request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Session-end perf-snapshot hook.

    CB.17.j — snapshots EACH dialect-container that the session
    actually touched, not just the one matching cfg.db.dialect. The
    ``pg_container_url`` / ``oracle_container_url`` fixtures set
    ``RECON_GEN_DEMO_DATABASE_URL_PG`` / ``_OR`` to the yielded URL
    when they fire; teardown iterates those env vars and writes a
    per-container ``$RECON_GEN_RUN_DIR/db/<dialect>/top-queries.md``.
    Falls back to the cfg.db.dialect path when neither env var is set
    (DuckDB-only or no-container session).
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

    # CB.17.j — fan out over each container the session touched.
    # Module-level imports keep these in scope here; only the perf
    # helpers + connect_demo_db are lazy.
    from recon_gen._dev.perf import (  # noqa: PLC0415
        fetch_top_queries,
        format_skipped,
        format_top_queries_markdown,
    )
    from recon_gen.common.db import connect_demo_db  # noqa: PLC0415
    from recon_gen.common.env_keys import (  # noqa: PLC0415
        RECON_GEN_DEMO_DATABASE_URL_OR,
        RECON_GEN_DEMO_DATABASE_URL_PG,
    )
    from recon_gen.common.sql import Dialect  # noqa: PLC0415
    import dataclasses as _dc  # noqa: PLC0415

    # ``like_pattern`` narrows the stats-view scan to OUR queries.
    # cfg.db.table_prefix is the canonical root every isolated test
    # suffixes (`qsgen_postgres_<hash>`), so a LIKE on the bare prefix
    # matches both the base AND every isolated-test variant.
    #
    # CB.17.o — CI materializes per-dialect cfgs with dialect-suffixed
    # prefixes (`qs_ci_<run>_pg` vs `qs_ci_<run>_or`); cfg-discovery
    # returns only the PG cfg in a one-cfg-per-session model. Pre-
    # CB.17.o we used that single prefix to probe BOTH containers, so
    # Oracle's report came back empty (the queries it actually ran
    # used `_or`-suffixed names). Swap the trailing dialect suffix
    # per-container below; non-CI patterns (e.g. local `spec_example`)
    # pass through unchanged.
    base_pattern = cfg.db.table_prefix or "spec_example"

    def _swap_dialect_suffix(pattern: str, dialect: Dialect) -> str:
        target_suffix = {
            Dialect.POSTGRES: "_pg",
            Dialect.ORACLE: "_or",
            Dialect.DUCKDB: "_du",
        }[dialect]
        for known in ("_pg", "_or", "_du"):
            if pattern.endswith(known):
                return pattern[: -len(known)] + target_suffix
        return pattern

    def _snapshot(
        target_dir: Path, dialect: Dialect, url: str,
    ) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "top-queries.md"
        title = f"Top expensive queries ({dialect.value})"
        like_pattern = _swap_dialect_suffix(base_pattern, dialect)
        # Build a minimal cfg pointing at this container.
        cfg_snap = _dc.replace(cfg, db=_dc.replace(cfg.db, url=url, dialect=dialect))
        try:
            conn = connect_demo_db(cfg_snap)
        except Exception as exc:
            try:
                target.write_text(
                    format_skipped(
                        title=title, dialect=dialect.value,
                        reason=(
                            f"could not connect ({type(exc).__name__}): "
                            f"{exc}"
                        ),
                    ),
                )
            except OSError:
                pass
            return
        try:
            try:
                rows = fetch_top_queries(
                    conn, dialect, like_pattern=like_pattern, top=50,
                )
            except Exception as exc:
                try:
                    target.write_text(
                        format_skipped(
                            title=title, dialect=dialect.value,
                            reason=(
                                f"stats view unavailable: "
                                f"{type(exc).__name__}: {exc}. Pre-req "
                                f"for postgres: ``CREATE EXTENSION "
                                f"pg_stat_statements`` + "
                                f"`shared_preload_libraries`. For "
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
                    title=title, dialect=dialect.value,
                    like_pattern=like_pattern, rows=rows,
                ),
            )
        except OSError:
            pass

    pg_url = RECON_GEN_DEMO_DATABASE_URL_PG.get_or_none()
    or_url = RECON_GEN_DEMO_DATABASE_URL_OR.get_or_none()
    snapshot_count = 0
    if pg_url:
        _snapshot(Path(run_dir) / "db" / "postgres", Dialect.POSTGRES, pg_url)
        snapshot_count += 1
    if or_url:
        _snapshot(Path(run_dir) / "db" / "oracle", Dialect.ORACLE, or_url)
        snapshot_count += 1
    if snapshot_count > 0:
        return

    # Fallback: neither container fixture was used. Keep the legacy
    # cfg.db.dialect-only path for DuckDB-only or no-container sessions.
    if not cfg.db.url:
        return

    from recon_gen._dev.perf import (
        dialect_name,
        fetch_top_queries,
        format_skipped,
        format_top_queries_markdown,
    )
    from recon_gen.common.db import connect_demo_db
    from recon_gen.common.sql import Dialect

    dialect_str = dialect_name(cfg.db.dialect)
    target_dir = Path(run_dir) / "db" / dialect_str
    target = target_dir / "top-queries.md"
    title = f"Top expensive queries ({dialect_str})"

    # SQLite has no stats view — write a clean skipped marker and stop.
    if cfg.db.dialect is Dialect.DUCKDB:
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

    # Z.C — the substring filter is just cfg.db.table_prefix (the DB-
    # table-name prefix every emitted matview / table carries). Falls
    # back to the demo prefix only if cfg somehow has no value.
    like_pattern = cfg.db.table_prefix or "spec_example"

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
                conn, cfg.db.dialect, like_pattern=like_pattern, top=50,
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
