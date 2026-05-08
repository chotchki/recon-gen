"""End-to-end harness — `tests/test_harness_end_to_end.py` (M.4.1).

One harness parameterized over every `L2_INSTANCES` matrix entry. Each
per-instance test executes the full chain — load YAML → emit_schema to
a fresh per-test DB prefix → auto-scenario + emit_seed → DB apply →
matview refresh → generate l1-dashboard + generate l2-flow-tracing →
deploy both to QS → Playwright asserts planted scenarios surface as
visible rows on the right sheets — and dumps a triage manifest on
failure.

This file lives under ``tests/e2e/`` so it inherits the
``QS_GEN_E2E=1`` skip gate from ``tests/e2e/conftest.py`` (no need to
re-implement). Per-test fixtures are scoped here (independent of the
shared session-scoped ``cfg`` fixture, which loads production
``config.yaml`` and is the wrong shape for a per-test ephemeral
deploy).

**M.4.1.a — Shared fixtures + cleanup scaffolding (this commit).**
Lands the fixture skeleton + per-test isolation + tag-filter cleanup
for every QS resource the harness deploys + DB schema drop. One smoke
test exercises the wiring without yet doing the deploy / Playwright
half. M.4.1.b–h fill in the actual harness body.

Per-test isolation strategy:
- Each test gets a unique short UID (``harness_uid`` fixture).
- The L2 instance is cloned with a derived prefix
  ``e2e_<original_instance>_<uid>`` so concurrent tests can't collide
  on shared schema names OR shared QS resource IDs.
- ``Config.extra_tags`` carries ``TestUid: <uid>`` + ``Harness: e2e``;
  ``Config.tags()`` already propagates these onto every QS resource.
- Teardown sweeps QS resources by ``TestUid`` tag (via
  ``_harness_cleanup.sweep_qs_resources_by_tag``) + drops the DB
  schema by ``e2e_*_<uid>`` prefix discovery (via
  ``_harness_cleanup.drop_prefixed_schema``).

**Parallelism (M.4.1.g).** The per-test isolation is xdist-safe by
construction — every QS resource carries a unique ``TestUid`` tag
and every DB object carries a unique prefix, so concurrent tests on
the same QS account + Aurora cluster cannot collide. Run via the
existing ``./run_e2e.sh --parallel N`` switch (default N=4 from the
PR/AR/Inv/Exec e2e suite); for the harness alone, ``-n 3`` saturates
the current ``L2_INSTANCES`` matrix. Each test deploys 14 datasets +
2 dashboards, so xdist=3 puts ~42 ``create_data_set`` calls in flight
— boto3's standard retry mode handles transient ``ThrottlingException``
without harness-side intervention.

**Flake hardening (M.4.1.g).** The smoke tests below wrap their
Playwright bodies in ``run_dashboard_check_with_retry`` from
``_harness_browser.py`` — one retry-with-fresh-embed-URL on
Playwright timeout, addressing the CLAUDE.md "QuickSight spinner
forever" footgun. AssertionError from the assertion helpers
propagates immediately (real planted-row-missing failures are NOT
retried — that would mask regressions).

Required env vars beyond the existing e2e set (see
``tests/e2e/conftest.py`` for the base set):
- ``QS_GEN_DEMO_DATABASE_URL`` — psycopg2 DSN for the harness's DB
  apply step. The existing conftest already reads this for
  ``cfg.demo_database_url`` derivation, so any environment running
  the production e2e suite already has it set.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pytest

# Pull in the cleanup helpers from the sibling module (test-only path,
# not pip-installable from this layout, so import via sys.path).
sys.path.insert(0, str(Path(__file__).parent))
from _harness_cleanup import (  # noqa: E402
    drop_prefixed_schema,
    sweep_qs_resources_by_tag,
)
from _harness_seed import (  # noqa: E402
    apply_db_seed,
    build_planted_manifest,
)
from _harness_deploy import (  # noqa: E402
    HARNESS_APPS,
    build_embed_urls,
    extract_dashboard_ids,
    generate_apps,
)
from _harness_browser import run_dashboard_check_with_retry  # noqa: E402
from _harness_failure_dump import dump_failure_manifest  # noqa: E402
from _harness_l1_assertions import (  # noqa: E402
    assert_l1_matview_rows_present,
    assert_l1_plants_visible,
    assert_todays_exceptions_kpi_matches,
    widen_l1_date_range,
)
from _harness_l2ft_assertions import (  # noqa: E402
    assert_l2_exceptions_kpi_renders,
    assert_l2ft_matview_rows_present,
)
from _harness_inv_assertions import (  # noqa: E402
    assert_inv_matviews_queryable,
    assert_inv_planted_rows_visible,
)
from _harness_exec_assertions import (  # noqa: E402
    assert_exec_base_tables_queryable,
)

# L2_INSTANCES matrix: re-use the exact list `test_l2_seed_contract.py`
# uses so adding a new YAML there parameterizes the harness too.
sys.path.insert(0, str(Path(__file__).parent.parent / "data"))
from test_l2_seed_contract import L2_INSTANCES  # noqa: E402

from quicksight_gen.common.config import Config
from quicksight_gen.common.env_keys import (
    QS_E2E_PAGE_TIMEOUT,
    QS_E2E_VISUAL_TIMEOUT,
)
from quicksight_gen.common.l2 import L2Instance, load_instance
from quicksight_gen.common.l2.primitives import Identifier


# ---------------------------------------------------------------------------
# Per-test isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def harness_uid() -> str:
    """Short unique string per test (suitable for DB prefix + QS tags).

    Constraints:
    - Stays under the L2 ``InstancePrefix`` 30-char cap (F5) — the prefix
      becomes ``e2e_<instance>_<uid>``, so the uid plus instance plus
      ``e2e_`` overhead must fit. 8 chars of UUID hex leaves room for
      L2 instance names up to ~17 chars (``sasquatch_pr`` = 12, fits).
    - Starts with a hex char (always [0-9a-f]) so the resulting prefix
      passes the loader's ``^[a-z][a-z0-9_]*$`` regex.
    """
    return uuid.uuid4().hex[:8]


@pytest.fixture(params=L2_INSTANCES)
def harness_l2(request, harness_uid: str) -> L2Instance:
    """Load the L2 YAML, then clone with an ephemeral per-test prefix.

    ``emit_schema`` reads the prefix from ``instance.instance`` directly
    (no separate ``prefix=`` arg), so the only way to give a test its
    own prefix is to clone the L2 instance with a different ``instance``
    field. The original on-disk YAML stays unmodified.

    The cloned instance also gets ``validate=False`` skipped — the
    original was already validated at load. Re-running the validator
    on the clone would be a wasted cycle.
    """
    yaml_path: Path = request.param
    original = load_instance(yaml_path)
    ephemeral_prefix = Identifier(f"e2e_{original.instance}_{harness_uid}")
    return dataclasses.replace(original, instance=ephemeral_prefix)


@pytest.fixture
def harness_cfg(cfg: Config, harness_l2: L2Instance, harness_uid: str) -> Config:
    """Per-test ``Config`` derived from the session-scoped ``cfg`` fixture
    with TestUid-tagged ``extra_tags`` + per-test ``l2_instance_prefix``.

    Inherits from ``cfg`` (which loads ``run/config.yaml`` per the
    existing e2e convention in ``conftest.py``) so the harness picks
    up the same ``principal_arns`` (otherwise the deployed dashboards
    wouldn't grant view permission to the embed user) +
    ``aws_account_id`` / ``aws_region`` / ``datasource_arn`` (or
    ``demo_database_url``-derived equivalent).

    Override surface (per-test):
    - ``extra_tags``: M.4.1.a tag-injection point. ``TestUid`` enables
      the per-test tag-filter sweep at teardown; ``Harness`` is a
      coarse marker for "this came from the e2e harness, not the
      production deploy" (useful in QS console searches).
    - ``l2_instance_prefix``: per-test ephemeral prefix
      (``e2e_<instance>_<uid>``) so concurrent tests can't collide
      on shared schema names OR shared QS resource IDs.

    Note that ``dataclasses.replace`` re-runs ``__post_init__``, but
    the datasource_arn auto-derivation is a no-op when the original
    cfg already had it set — so the per-test cfg points at the same
    pre-existing datasource the production deploy uses.
    """
    # Clear datasource_arn so Config.__post_init__ re-derives it from
    # the per-test l2_instance_prefix. Result: each test gets its own
    # `qs-gen-<per-test-prefix>-demo-datasource` ARN, which the harness
    # also creates fresh in generate_apps (M.4.1 option 2 — per-test
    # datasource decouples the harness from any QS-side caching tied
    # to the shared production datasource).
    return dataclasses.replace(
        cfg,
        datasource_arn=None,
        extra_tags={
            **cfg.extra_tags,
            "TestUid": harness_uid,
            "Harness": "e2e",
        },
        l2_instance_prefix=str(harness_l2.instance),
    )


@pytest.fixture
def harness_db_conn(harness_cfg: Config):
    """DB-API 2.0 connection to the demo DB, with cold-start warmup.

    Branches on ``harness_cfg.dialect`` via ``common/db.connect_demo_db``
    (P.9d): psycopg2 for Postgres, oracledb for Oracle. Connection is
    fixture-scoped (per-test) so each test gets its own connection —
    concurrent tests don't share a connection that one test's teardown
    might close out from under another. Yields the connection; teardown
    drops every prefixed object the test created.

    Aurora cold-start: the existing operational footgun (CLAUDE.md) is
    that Aurora Serverless V1's idle pause causes the first query to
    fail. Issue ``SELECT 1 FROM dual`` (Oracle) / ``SELECT 1`` (Postgres)
    immediately after connecting so the cold-start hit lands on the
    warmup, not the first real ``emit_schema`` apply.
    """
    if harness_cfg.demo_database_url is None:
        pytest.skip("QS_GEN_DEMO_DATABASE_URL not set")
    from quicksight_gen.common.db import connect_demo_db
    from quicksight_gen.common.sql import Dialect
    try:
        conn = connect_demo_db(harness_cfg)
    except ImportError as exc:
        pytest.skip(str(exc))
    warmup_sql = (
        "SELECT 1 FROM dual"
        if harness_cfg.dialect is Dialect.ORACLE
        else "SELECT 1"
    )
    try:
        with conn.cursor() as cur:
            cur.execute(warmup_sql)
            cur.fetchone()
        yield conn
    finally:
        # Always attempt schema cleanup — even if the test failed mid-way
        # the prefixed objects (if any made it in) need to come out so
        # the next test doesn't see leftover state.
        try:
            drop_prefixed_schema(
                conn, str(harness_cfg.l2_instance_prefix),
                dialect=harness_cfg.dialect,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort teardown
            print(
                f"[harness] DB schema teardown failed for prefix "
                f"{harness_cfg.l2_instance_prefix!r}: {exc}",
                file=sys.stderr,
            )
        conn.close()


@pytest.fixture
def harness_seeded(
    harness_db_conn: Any, harness_l2: L2Instance, harness_cfg: Config,
):
    """Apply schema + seed + matview refresh; return the per-test
    handle the M.4.1.b–e harness body consumes (M.4.1.b).

    Three DB-side steps run via ``_harness_seed.apply_db_seed``:
    emit_schema → emit_seed (mode='l1_plus_broad' so both L1 SHOULD
    plants and broad-mode rail firings land) → refresh_matviews_sql.
    Each commits independently so a mid-flow failure leaves the DB in
    a known state for ``harness_db_conn``'s teardown to drop cleanly.

    **today=date.today()** (M.4.1.k bug fix) — overrides
    ``DEFAULT_HARNESS_TODAY``. The L1 invariant matviews
    (``stuck_pending`` / ``stuck_unbundled``) compute their key
    predicates against ``CURRENT_TIMESTAMP`` (real wall-clock NOW), so
    plants anchored to a fixed-future ``today`` (the default) yield
    NEGATIVE age values that never exceed any positive max-pending /
    max-unbundled threshold — those plants silently never land in
    the matviews. The seed-hash-determinism rationale that pins
    DEFAULT_HARNESS_TODAY at 2030-01-01 (M.2a.8) is for
    ``test_l2_seed_contract.py``'s hash-lock — the harness asserts
    against deployed-dashboard rendering, not seed-byte-equality, so
    using the wall clock is correct here.

    Returns a dict with the per-test handle downstream Playwright
    assertions (M.4.1.d/e) consume:
      - ``instance``: the per-test L2Instance (cloned with the
        ephemeral prefix)
      - ``prefix``: the same prefix as a string, for SQL-emit /
        boto3 ID derivation convenience
      - ``today``: the wall-clock date used as the plant anchor;
        the L1 widen helper needs this so its filter window matches
        the planted ages
      - ``planted_manifest``: dict of plant-kind → list-of-row-finder
        dicts (see ``_harness_seed.build_planted_manifest`` for the
        shape; M.4.1.f's failure dump consumes the same dict)
    """
    today = date.today()
    scenario = apply_db_seed(
        harness_db_conn, harness_l2, today=today,
        dialect=harness_cfg.dialect,
    )
    return {
        "instance": harness_l2,
        "prefix": str(harness_l2.instance),
        "today": today,
        "planted_manifest": build_planted_manifest(scenario),
    }


@pytest.fixture
def harness_deployed(
    tmp_path: Path,
    harness_seeded: dict[str, Any],
    harness_db_conn: Any,
    harness_cfg: Config,
    harness_qs_cleanup: None,
) -> dict[str, Any]:
    """Generate + deploy both apps + build embed URLs (M.4.1.c).

    Pulls together the M.4.1.b harness_seeded handle and the
    M.4.1.a harness_qs_cleanup teardown, then runs the deploy chain:

    1. ``generate_apps`` writes theme + per-app analysis/dashboard +
       per-dataset JSON to ``tmp_path``. Datasource skipped (uses
       cfg.datasource_arn from env).
    2. ``common.deploy.deploy()`` does the delete-then-create dance
       on QS for both apps' resources, waits for
       ``CREATION_SUCCESSFUL``. The harness's ``cfg.tags()`` carry
       the M.4.1.a TestUid + Harness tags so every created resource
       is reapable by the cleanup fixture's tag-filter sweep.
    3. ``extract_dashboard_ids`` reads the dashboard JSONs back to
       recover the QS DashboardId per app.
    4. ``build_embed_urls`` generates one signed embed URL per
       dashboard via the identity-region QS client.

    Depends on ``harness_qs_cleanup`` (the underscore-prefixed
    pytest declaration is fine — pytest evaluates fixture dependencies
    by name, so requesting ``harness_qs_cleanup`` ensures the
    teardown runs after this test exits) so even a mid-deploy
    failure gets reaped.

    Returns ``{instance, prefix, planted_manifest, dashboard_ids,
    embed_urls}`` for the M.4.1.d/.e Playwright assertions.
    """
    from quicksight_gen.common.deploy import deploy as deploy_apps

    out_dir = tmp_path / "harness_out"
    instance = harness_seeded["instance"]

    # 1. Generate JSON for both apps.
    generate_apps(harness_cfg, instance, out_dir)

    # 2. Deploy via the existing CLI deploy (delete-then-create +
    # CREATION_SUCCESSFUL wait). Returns 0 on success.
    rc = deploy_apps(harness_cfg, out_dir, list(HARNESS_APPS))
    if rc != 0:
        raise RuntimeError(
            f"harness deploy failed for prefix "
            f"{harness_cfg.l2_instance_prefix!r}; see deploy stdout"
        )

    # 3. Recover dashboard IDs from the JSON the deploy step sent.
    dashboard_ids = extract_dashboard_ids(out_dir)

    # 4. Build per-test embed URLs. The helper builds a boto3 QS
    # client in the dashboard region internally — embed URLs MUST
    # be signed by the dashboard-region client (M.4.1.i pin).
    embed_urls = build_embed_urls(
        aws_account_id=harness_cfg.aws_account_id,
        aws_region=harness_cfg.aws_region,
        dashboard_ids=dashboard_ids,
    )

    # 5. Pre-warm Aurora for this per-test prefix BEFORE QuickSight
    # asks. The session-scoped warm_aurora fixture (conftest.py) only
    # warms the production prefix; each per-test prefix's view DAG
    # needs its own first-touch to compile. Without this, the first
    # dashboard render times out waiting for Aurora to compile the
    # L1 invariant view chain (drift / overdraft / breach / pending /
    # unbundled through current_transactions + current_daily_balances)
    # — observed during the first AWS-side harness dry-run.
    _prewarm_db_for_prefix(harness_db_conn, harness_seeded["prefix"])

    return {
        "instance": instance,
        "prefix": harness_seeded["prefix"],
        "today": harness_seeded["today"],
        "planted_manifest": harness_seeded["planted_manifest"],
        "dashboard_ids": dashboard_ids,
        "embed_urls": embed_urls,
    }


def _prewarm_db_for_prefix(db_conn: Any, prefix: str) -> None:
    """Issue ``SELECT 1 FROM <prefix>_<view> LIMIT 1`` against every
    L1 invariant view + matview + base table, post-deploy / pre-render.

    Why: the session-scoped ``warm_aurora`` fixture in conftest.py only
    warms the production prefix's tables. Each per-test prefix's view
    DAG pays Aurora's full per-prefix view-compilation cost on its
    first SELECT — typically 10–30s for the L1 invariant chain.
    Doing the warmup HERE (post-deploy, pre-render) gets the cache
    hot before QuickSight asks for the same data over the embed URL.
    Without it, the first dashboard render times out waiting on the
    compile.

    Best-effort: per-query failure rolls back the txn (otherwise
    psycopg2 leaves it aborted and every subsequent query errors)
    and continues — missing views (older fixtures) are tolerated.
    """
    objects = (
        # Base tables.
        "transactions", "daily_balances",
        # current_* matviews — the rest of the L1 chain reads from these.
        "current_transactions", "current_daily_balances",
        # L1 invariant matviews — what the dashboard SQL hits.
        "drift", "ledger_drift", "overdraft", "limit_breach",
        "stuck_pending", "stuck_unbundled",
        # Dashboard-shape matview.
        "todays_exceptions",
    )
    for obj in objects:
        full = f"{prefix}_{obj}"
        try:
            with db_conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {full} LIMIT 1")
                cur.fetchall()
            db_conn.commit()
        except Exception:  # noqa: BLE001 — best-effort
            try:
                db_conn.rollback()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture
def harness_qs_cleanup(harness_cfg: Config, harness_uid: str):
    """Yield, then sweep every QS resource carrying ``TestUid: <uid>``.

    Pure-teardown fixture — the actual deploy + Playwright assertions
    happen inside the test body. This fixture's only job is to
    guarantee that whatever the test deployed (or partially deployed
    before failing) gets reaped.

    Lazy boto3 import keeps the harness file loadable in environments
    without boto3 installed (e.g. the CI lint pass).
    """
    yield  # test runs
    try:
        import boto3
    except ImportError:
        # Test environment doesn't have boto3 — nothing to sweep.
        return
    qs = boto3.client("quicksight", region_name=harness_cfg.aws_region)
    counts = sweep_qs_resources_by_tag(
        qs,
        harness_cfg.aws_account_id,
        tag_key="TestUid",
        tag_value=harness_uid,
    )
    print(
        f"[harness] swept QS resources for TestUid={harness_uid}: {counts}",
        file=sys.stderr,
    )


@pytest.fixture
def harness_failure_dump(
    request: pytest.FixtureRequest,
    harness_l2: L2Instance,
    harness_seeded: dict[str, Any],
    harness_deployed: dict[str, Any],
    harness_db_conn: Any,
):
    """Dump a triage manifest under ``tests/e2e/failures/`` if the test
    body raises (M.4.1.f).

    Reads ``request.node.rep_call`` — the per-phase test outcome the
    ``pytest_runtest_makereport`` hook in ``conftest.py`` exposes.
    Only fires when the call phase failed; clean tests leave no file.

    All four optional sections (``dashboard_ids``, ``embed_urls``,
    ``db_conn``, ``exception_text``) are best-effort:
    - If the failure happened *before* ``harness_deployed`` resolved
      (e.g. the seed step blew up), this fixture would not run because
      pytest only injects fixtures whose own setup succeeded. The
      ``harness_seeded``-only fallback is covered by the M.4.1.b smoke
      test's own try/except, not here.
    - ``rep_call.longreprtext`` is pytest's full traceback string —
      the same blob you see in the terminal on a failure.

    Pairs with ``harness_qs_cleanup`` (tag-filter sweep) — the cleanup
    runs after the dump, so you can ``ls tests/e2e/failures/`` even
    after the QS resources are reaped.
    """
    yield
    rep = getattr(request.node, "rep_call", None)
    if rep is None or not rep.failed:
        return
    failure_dir = Path(__file__).parent / "failures"
    out_path = dump_failure_manifest(
        failure_dir,
        test_id=request.node.nodeid,
        instance=harness_l2,
        planted_manifest=harness_seeded["planted_manifest"],
        dashboard_ids=harness_deployed.get("dashboard_ids"),
        embed_urls=harness_deployed.get("embed_urls"),
        db_conn=harness_db_conn,
        exception_text=rep.longreprtext or None,
    )
    print(
        f"[harness] failure manifest written: {out_path}",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Smoke test — fixtures wire up + tag injection lands
# ---------------------------------------------------------------------------


def test_harness_fixtures_wire_up(
    harness_uid: str,
    harness_l2: L2Instance,
    harness_cfg: Config,
) -> None:
    """The fixture chain assembles correctly: per-test UID, L2 instance
    cloned with an ``e2e_*_<uid>`` prefix, and Config carrying the
    expected ``TestUid`` + ``Harness`` extra tags so ``cfg.tags()``
    propagates them onto every deployed QS resource.

    No DB or AWS work — just verifies the per-test isolation contract.
    M.4.1.b–h add the actual deploy + Playwright assertions on top.
    """
    # Per-test UID is a short hex string.
    assert harness_uid
    assert all(c in "0123456789abcdef" for c in harness_uid)

    # L2 instance was cloned with an ephemeral prefix.
    assert str(harness_l2.instance).startswith("e2e_")
    assert harness_l2.instance.endswith(f"_{harness_uid}")

    # Config carries the harness tags AND propagates them via tags().
    assert harness_cfg.extra_tags == {
        "TestUid": harness_uid,
        "Harness": "e2e",
    }
    assert harness_cfg.l2_instance_prefix == str(harness_l2.instance)

    tag_dict = {t.Key: t.Value for t in harness_cfg.tags()}
    assert tag_dict["ManagedBy"] == "quicksight-gen"
    assert tag_dict["L2Instance"] == str(harness_l2.instance)
    assert tag_dict["TestUid"] == harness_uid
    assert tag_dict["Harness"] == "e2e"


# ---------------------------------------------------------------------------
# M.4.1.b smoke — DB-side seed fixture wires up + planted_manifest lands
# ---------------------------------------------------------------------------


def test_harness_seeded_fixture_lands_with_manifest(
    harness_seeded: dict[str, Any],
    harness_l2: L2Instance,
    harness_db_conn: Any,
) -> None:
    """The full DB-side fixture chain (schema → seed → matview refresh)
    runs cleanly and returns a usable handle for downstream M.4.1.c–e
    fixtures.

    Sanity-checks performed on the deployed DB state:
    1. The base table ``<prefix>_transactions`` exists and has rows
       (broad mode + L1 invariant plants both populate it).
    2. The ``<prefix>_current_transactions`` matview is fresh (rows
       there match the base table; if the refresh step were skipped
       the matview would be empty).
    3. The planted_manifest contains the expected plant kinds for
       l1_plus_broad mode (rail_firing_plants + transfer_template_plants
       at minimum, since these are the broad-layer plants every
       L2 instance with at least one Rail produces).

    Skipped under default pytest (no QS_GEN_E2E); requires Aurora
    via QS_GEN_DEMO_DATABASE_URL.
    """
    instance = harness_seeded["instance"]
    prefix = harness_seeded["prefix"]
    manifest = harness_seeded["planted_manifest"]

    # Sanity: instance handle round-trips.
    assert instance is harness_l2
    assert prefix == str(harness_l2.instance)

    # DB has the prefixed base table with rows.
    with harness_db_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {prefix}_transactions")
        n_base = cur.fetchone()[0]
    assert n_base > 0, (
        f"{prefix}_transactions has no rows after apply_db_seed; "
        f"either seed planted nothing or schema apply silently failed"
    )

    # current_transactions matview is fresh — has rows, but NOT
    # necessarily equal to base count. The matview is "latest entry
    # per id" (M.1a.9 supersession contract); the base table can carry
    # multiple entry rows per id when SupersedeReason updates fire.
    # Equality would only hold for fixtures that plant zero
    # supersessions; sasquatch_pr broad-mode plants several. The
    # right contract is: matview > 0 (refresh fired) AND <= base
    # (matview is a subset).
    with harness_db_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {prefix}_current_transactions")
        n_current = cur.fetchone()[0]
    assert n_current > 0, (
        f"{prefix}_current_transactions has no rows after apply_db_seed; "
        f"refresh_matviews_sql step missed"
    )
    assert n_current <= n_base, (
        f"{prefix}_current_transactions has {n_current} rows but base "
        f"only has {n_base}; matview must be a subset of base"
    )

    # Manifest carries every plant-kind key the builder declares,
    # and at least the broad-layer kinds are non-empty (every L2
    # instance with rails produces rail_firing_plants in
    # l1_plus_broad mode).
    expected_kinds = {
        "drift_plants",
        "overdraft_plants",
        "limit_breach_plants",
        "stuck_pending_plants",
        "stuck_unbundled_plants",
        "supersession_plants",
        "transfer_template_plants",
        "rail_firing_plants",
        "inv_fanout_plants",  # N.4.h — Investigation pair_rolling + money_trail
    }
    assert set(manifest.keys()) == expected_kinds
    assert len(manifest["rail_firing_plants"]) > 0, (
        "broad mode should plant rail firings for every L2 instance "
        "with at least one Rail whose roles materialize"
    )


# ---------------------------------------------------------------------------
# M.4.1.c smoke — deploy fixture lands both apps + builds embed URLs
# ---------------------------------------------------------------------------


def test_harness_deployed_fixture_lands_with_embed_urls(
    harness_deployed: dict[str, Any],
    cfg,
) -> None:
    """The full deploy chain (generate → deploy → extract IDs →
    build embed URLs) runs cleanly and returns one URL per app.

    Sanity-checks the M.4.1.d/.e contract: both apps' dashboard ids
    + embed URLs are present and well-shaped. The planted_manifest
    + instance + prefix from M.4.1.b survive through the chain.

    Skipped under default pytest (no QS_GEN_E2E); requires QS account
    + region + datasource ARN env vars set, and Aurora available
    (transitively via M.4.1.b's harness_seeded dependency).
    """
    # Both apps deployed.
    dashboard_ids = harness_deployed["dashboard_ids"]
    assert set(dashboard_ids.keys()) == set(HARNESS_APPS)

    # Each dashboard ID carries the per-test L2 prefix (M.2d.3).
    # Resource-prefix is read from cfg so per-dialect copies of
    # config.yaml (Phase P) — e.g. ``qs-gen-postgres`` /
    # ``qs-gen-oracle`` — work without hardcoding ``qs-gen``.
    prefix = harness_deployed["prefix"]
    expected_id_prefix = f"{cfg.resource_prefix}-{prefix}-"
    for app_name, dashboard_id in dashboard_ids.items():
        assert dashboard_id.startswith(expected_id_prefix), (
            f"{app_name} dashboard_id {dashboard_id!r} doesn't carry "
            f"per-test prefix {expected_id_prefix!r} — check cfg flow"
        )

    # Embed URLs match the dashboard ids 1:1 and look like signed URLs.
    embed_urls = harness_deployed["embed_urls"]
    assert set(embed_urls.keys()) == set(HARNESS_APPS)
    for app_name, url in embed_urls.items():
        assert url.startswith("https://"), (
            f"{app_name} embed URL {url!r} doesn't look like a signed URL"
        )

    # M.4.1.b handles still present (so M.4.1.d/.e can iterate plants).
    assert harness_deployed["instance"] is not None
    assert harness_deployed["prefix"] == prefix
    assert "rail_firing_plants" in harness_deployed["planted_manifest"]


# ---------------------------------------------------------------------------
# M.4.1.d smoke — Playwright walks the L1 dashboard + asserts plants
# ---------------------------------------------------------------------------


def test_harness_l1_planted_scenarios_visible(
    harness_cfg: Config,
    harness_db_conn: Any,
    harness_deployed: dict[str, Any],
    harness_failure_dump: None,
) -> None:
    """Two-layer verification: matview rows present (psycopg2, fast)
    THEN dashboard renders the planted scenarios (Playwright, slow).

    **Layer 1 — matview rows present** (M.4.1.k):
        Direct psycopg2 query against ``<prefix>_<matview>`` for each
        plant kind. <1s per query; if a planted account doesn't appear
        the failure points straight at the seed → matview-refresh
        pipeline. Runs first so a regression at this layer fails
        fast WITHOUT paying the Playwright + visual-timeout cost.

    **Layer 2 — dashboard render** (M.4.1.d):
        Open the deployed L1 dashboard via Playwright; for each plant
        kind, navigate to its corresponding sheet and verify the
        planted account_id surfaces. Catches bugs that pass Layer 1
        but break in the dashboard layer (dataset SQL filters,
        visual config, sheet-level filter scoping, QS rendering).

    Per-plant-kind assertions (Layer 2):
    - DriftPlant → Drift sheet shows account_id
    - OverdraftPlant → Overdraft sheet shows account_id
    - LimitBreachPlant → Limit Breach sheet shows account_id
    - StuckPendingPlant → Pending Aging shows account_id
    - StuckUnbundledPlant → Unbundled Aging shows account_id
    - SupersessionPlant → Supersession Audit shows account_id

    Plus Today's Exceptions KPI rollup: the open-violations count
    matches the sum of planted SHOULD-violation scenarios across
    drift / overdraft / breach / pending / unbundled.

    Wraps the page lifecycle in ``run_dashboard_check_with_retry``
    (M.4.1.g) — one retry-with-fresh-embed-URL on Playwright timeout
    handles the QS spinner-forever flake (CLAUDE.md operational
    footgun). AssertionError from the assertion helpers propagates
    immediately — real planted-row-missing failures are NOT retried.

    Skipped under default pytest. Per the existing browser-e2e
    convention, requires Playwright + WebKit installed (via
    ``playwright install webkit`` once per environment).
    """
    manifest = harness_deployed["planted_manifest"]
    prefix = harness_deployed["prefix"]
    dashboard_id = harness_deployed["dashboard_ids"]["l1-dashboard"]
    page_timeout = QS_E2E_PAGE_TIMEOUT.get_or_none() or 30000
    visual_timeout = QS_E2E_VISUAL_TIMEOUT.get_or_none() or 30000

    # Layer 1: matview-row-presence. Fast, deterministic, points at
    # the seed/matview layer if it fails. NOT xfailed — this is the
    # primary regression net the harness provides.
    assert_l1_matview_rows_present(
        harness_db_conn, prefix, manifest, dialect=harness_cfg.dialect,
    )

    # Layer 1b (N.3.l-bis): Investigation matview schema-health check —
    # the prefixed Inv matviews exist and emit cleanly against the v6
    # base tables. Catches the v5/v6 column-name regression class
    # surfaced in N.3.b.
    assert_inv_matviews_queryable(harness_db_conn, prefix)

    # Layer 1b' (N.4.h): Investigation plant-row visibility — every
    # planted ``InvFanoutPlant`` (sender, recipient) edge surfaces in
    # both Inv matviews. Catches seed→matview-refresh regressions for
    # the Inv path the same way Layer 1 does for L1 invariants.
    assert_inv_planted_rows_visible(
        harness_db_conn, prefix, manifest, dialect=harness_cfg.dialect,
    )

    # Layer 1c (N.4.g): Executives base-table schema-health check.
    # Executives reads only from <prefix>_transactions +
    # <prefix>_daily_balances (no app-specific matviews). Same
    # column-rename-regression net as Layer 1b but applied at the
    # base-table level since there's no Exec matview to expand.
    assert_exec_base_tables_queryable(harness_db_conn, prefix)

    def _check_l1(page: Any) -> None:
        # Widen the universal date filter (M.2b.1) so plants outside
        # the default 7-day rolling window still surface. The harness
        # uses today=date.today() for the seed (M.4.1.k bug fix —
        # otherwise stuck_pending/stuck_unbundled matviews compute
        # negative ages and never match). Window must comfortably span
        # max(days_ago across the manifest) — the cap-aware stuck_*
        # plants from M.4.4.13 can sit at days_ago=cap+7 (e.g., P31D
        # cap → 38 days), exceeding any fixed default.
        max_days_ago = max(
            (
                int(plant["days_ago"])
                for plants in manifest.values()
                for plant in plants
                if isinstance(plant, dict) and "days_ago" in plant
            ),
            default=30,
        )
        widen_l1_date_range(
            page,
            today=harness_deployed["today"],
            days_back=max_days_ago + 7,  # buffer past the deepest plant
            timeout_ms=visual_timeout,
        )
        assert_l1_plants_visible(
            page, manifest, timeout_ms=visual_timeout,
        )
        # M.4.4.12 — KPI assertion compares dashboard render to matview
        # COUNT(*) directly. Manifest-based expected derivation can't
        # model broad-mode rail_firing plants whose legs surface in
        # stuck_pending / stuck_unbundled.
        assert_todays_exceptions_kpi_matches(
            page, harness_db_conn, prefix, timeout_ms=visual_timeout,
        )

    # Tall viewport so stacked tables don't sit below the fold during
    # the per-sheet walk — same pattern as the existing L1 browser
    # tests. Layer 2 xfail wrapper REMOVED (M.4.4.12) — the persistent
    # render failures turned out to be two real bugs: (1) the harness
    # asserted on a KPI title that didn't exist on the dashboard
    # ("Open Exceptions Today" → "Open Exceptions"); (2) the
    # `todays_exceptions` matview was missing the stuck_pending /
    # stuck_unbundled UNION branches that the harness expected to
    # roll up. Both fixed; assertion failures now propagate as real
    # FAILED so future regressions don't get masked.
    #
    # Known flake (PLAN backlog "Sasquatch L1 dashboard render flake"):
    # the sasquatch_pr variant occasionally fails Layer 2 on Limit
    # Breach with `account_id 'cust-0001-snb' not visible` — Layer 1
    # passes (matview row present), one retry already baked in via
    # ``run_dashboard_check_with_retry``, second attempt also misses.
    # Spec_example + fuzz variants of the same test pass on the same
    # run, so the flake is data-shape-specific (the sasquatch_pr seed
    # has more transactions, the L1 dashboard's per-sheet
    # transfer_type dropdown may default-narrow before the table
    # loads). Investigate when re-prioritized; do NOT xfail.
    run_dashboard_check_with_retry(
        aws_account_id=harness_cfg.aws_account_id,
        aws_region=harness_cfg.aws_region,
        dashboard_id=dashboard_id,
        operation=_check_l1,
        page_timeout_ms=page_timeout,
        viewport=(1600, 4000),
        screenshot_dir=Path(__file__).parent / "failures",
    )


# ---------------------------------------------------------------------------
# M.4.1.e smoke — Playwright walks the L2 Flow Tracing dashboard
# ---------------------------------------------------------------------------


def test_harness_l2ft_planted_scenarios_visible(
    harness_cfg: Config,
    harness_db_conn,
    harness_deployed: dict[str, Any],
    harness_failure_dump: None,
) -> None:
    """Verify L2-side planted scenarios surface in both the matview
    layer (Layer 1) and the dashboard render (Layer 2).

    Layer 1 — ``assert_l2ft_matview_rows_present`` queries
    ``<prefix>_current_transactions`` directly for each planted
    rail_name + template_name. Fast deterministic regression net for
    the seed → matview-refresh pipeline.

    Layer 2 — ``assert_l2_exceptions_kpi_renders`` opens the deployed
    L2 Flow Tracing dashboard and asserts the L2 Exceptions KPI
    renders an integer (proves the unified-exceptions dataset SQL ran
    cleanly against the per-test prefix).

    P.9f.f — The Rails-sheet plant-visibility check used to be a
    Layer-2 sheet-text scrape. It broke on sasquatch_pr where 57+
    standalone rail firings on a single Rails sheet pushed all but
    the alphabetically-earliest below QS table virtualization's
    ~10-row DOM fold (CLAUDE.md "E2E Test Conventions"). Mirroring
    L1's M.4.1.k pattern (matview check + KPI count), the matview
    query is the regression net; the dashboard render is sanity-
    checked via the KPI.

    Wrapped in ``run_dashboard_check_with_retry`` (M.4.1.g) for the
    same spinner-forever-flake reasons as the L1 smoke test above.

    Skipped under default pytest (no QS_GEN_E2E).
    """
    manifest = harness_deployed["planted_manifest"]
    prefix = harness_deployed["prefix"]
    dashboard_id = harness_deployed["dashboard_ids"]["l2-flow-tracing"]
    page_timeout = QS_E2E_PAGE_TIMEOUT.get_or_none() or 30000
    visual_timeout = QS_E2E_VISUAL_TIMEOUT.get_or_none() or 30000

    # Layer 1: matview-row-presence. Fast, deterministic, points at
    # the seed/matview layer if it fails. Replaces the dashboard
    # text-scrape Layer 2 that broke on virtualization (P.9f.f).
    assert_l2ft_matview_rows_present(
        harness_db_conn, prefix, manifest, dialect=harness_cfg.dialect,
    )

    def _check_l2ft(page: Any) -> None:
        assert_l2_exceptions_kpi_renders(
            page, timeout_ms=visual_timeout,
        )

    run_dashboard_check_with_retry(
        aws_account_id=harness_cfg.aws_account_id,
        aws_region=harness_cfg.aws_region,
        dashboard_id=dashboard_id,
        operation=_check_l2ft,
        page_timeout_ms=page_timeout,
        viewport=(1600, 4000),
        screenshot_dir=Path(__file__).parent / "failures",
    )
