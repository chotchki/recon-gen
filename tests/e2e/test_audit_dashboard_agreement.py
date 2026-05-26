"""U.8.b / X.2.j.B — 4-way cross-tool agreement test (release gate).

Per-invariant contract — the chain

    scenario_plants  ⊆  direct_matview_SELECT  ==  QS  ==  App2
                                   (==  PDF, drift only)

four renderers (the audit PDF, the deployed QuickSight dashboard, the
self-hosted App 2 HTMX dashboard) plus a direct ``SELECT`` against the
L1-invariant matview as the ground-truth anchor, all reading the *same*
seeded DB. For the flat-shape invariants (drift / overdraft /
limit_breach) the agreement tightens to row-identity — the *set* of
natural-key tuples, not just the count. Catches "every renderer
individually passed but they disagree on a violation row".

Parametrized over both dialects (U.8.b.5): each invariant×dialect cell
seeds the dialect-specific DB, renders the audit PDF against it, queries
the matview straight off it, walks the QS dashboard deployed under that
dialect's resource prefix, and serves the App 2 L1 tree against it. The
contract shape was locked by the X.2.j.0 spike — see
``docs/audits/x_2_j_agreement_spike.md``.

**Per-leg degradation, not per-test skip (X.2.j.C):** a missing prereq
disables one *leg*, not the whole test:
  - ``RECON_GEN_E2E=1`` gates the whole module (the existing browser-test
    gate).
  - ``run/config.<dialect>.yaml`` absent / no ``demo_database_url`` →
    that dialect cell skips (the other dialect still runs).
  - The L1 dashboard not deployed for this dialect (a SQLite cell has
    none) OR ``RECON_E2E_USER_ARN`` unset → the QS leg yields ``None``; the
    test runs as a 3-way ``scenario ⊆ direct == App2`` + PDF. CI deploys
    ``spec_example`` first via ``recon-gen json apply --execute -c
    run/config.<dialect>.yaml --l2 tests/l2/spec_example.yaml``; the QS
    leg parametrizes to PG only there.
  - ``supersession`` has no clean matview (the dashboard's "Transactions
    Audit" table + the audit PDF each query their own shape over the
    base tables) — it gets no direct-SQL anchor; the renderers must
    still agree with *each other*.

The scenario seed runs against the real ``demo_database_url`` —
DESTRUCTIVE for the ``spec_example_*`` prefix on each dialect's DB.
Other prefixes (the operator's actual data) are untouched because the
schema apply only drops + recreates the prefixed objects.

Anchors on ``date.today()`` (not the M.2a.8 hash-lock 2030 date):
the stuck_pending / stuck_unbundled matviews compute age via
``CURRENT_TIMESTAMP - posting``, so plants pinned to a far-future
date land in the SQL future and never satisfy the age threshold.
Anchoring on real today keeps plants visible across all 6
invariants on both dialects.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from recon_gen.cli import main
from recon_gen.common.db import connect_demo_db
from recon_gen.common.intervals import DateInterval
from recon_gen.common.l2 import load_instance
from recon_gen.common.sql import Dialect

from tests.audit._dashboard_extract import (
    count_l1_invariant_rows,
    key_columns_for,
    l1_invariant_row_keys,
    l1_invariant_rows_seen,
)
from tests.audit._matview_extract import (
    count_l1_invariant_matview_rows,
    l1_invariant_matview_row_keys,
)
from tests.audit._pdf_extract import count_invariant_table_rows
from tests.audit._scenario_expectations import expected_audit_counts
from tests.e2e._drivers import App2Driver, QsEmbedDriver


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
    # Y.7-followup — pin every test in this module onto a single
    # pytest-xdist worker. ``tests/conftest.py::pytest_configure`` bumps
    # the xdist dist mode to ``loadgroup`` when xdist is active so this
    # marker takes effect. Reason: ``seeded_audit`` is module-scoped
    # but xdist re-runs module-scoped fixtures once per worker; the
    # fixture re-applies the dialect schema (DROP + CREATE every prefixed
    # object), and on Oracle — where DDL auto-commits, so there's no
    # transactional isolation between concurrent workers — two workers
    # racing the same ``CREATE TABLE`` produce ORA-00955 ("name already
    # used"). Grouping forces the fixture to run exactly once. Bonus: no
    # redundant N× re-seed of the (slow) Aurora schema.
    pytest.mark.xdist_group("audit_dashboard_agreement_seed"),
]


# Y.2.gate.m.4.c — per-cell dialect-mismatch skip. When the runner
# dispatches this test inside a per-cell ``lo``-target variant (e.g.,
# ``sp_pg_lo``), the cell's ``RECON_GEN_DEMO_DATABASE_URL`` env override
# points at ONE container — the matching dialect's. The Oracle
# parametrization in that cell would have ``load_config`` route to a
# PG container with ``cfg.dialect=oracle`` (env URL wins; cfg yaml's
# dialect doesn't), driving downstream connection failures. Skip the
# mismatched parametrization; the sibling ``sp_or_lo`` cell runs the
# Oracle agreement check against its own container. ``aw`` cells (no
# env URL override) run both parametrizations against the operator's
# external Aurora + Oracle.
def _env_demo_url_dialect() -> str | None:
    from recon_gen.common.env_keys import RECON_GEN_DEMO_DATABASE_URL
    env_url = RECON_GEN_DEMO_DATABASE_URL.get_or_none()
    if env_url is None:
        return None
    if env_url.startswith(("postgres", "postgresql")):
        return "postgres"
    if env_url.startswith(("oracle", "oracle+oracledb")):
        return "oracle"
    return None


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE_BUNDLED = _FIXTURES / "spec_example.yaml"


def _l2_yaml_for_test() -> Path:
    """m.4.f — honor the runner's per-cell synthesized yaml when set.

    Z.C — the Y.2.gate.m runner used to set
    ``RECON_GEN_TEST_L2_INSTANCE`` to a per-cell synthesized yaml whose
    dropped ``instance`` field encoded the cell code (e.g.,
    ``sp_pg_aw``). With the field gone, the DB-table prefix lives on
    cfg.db_table_prefix and the QS-resource prefix lives on
    cfg.deployment_name; this fixture only resolves the L2 yaml
    itself.

    Outside the runner (operator running pytest directly), the env
    var is unset; fall back to the bundled spec_example fixture.
    """
    from recon_gen.common.env_keys import RECON_GEN_TEST_L2_INSTANCE
    env_val = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if env_val:
        return Path(env_val)
    return _SPEC_EXAMPLE_BUNDLED

# Anchor on real today so the stuck_* matviews' CURRENT_TIMESTAMP
# filter sees plants in the past. days_ago offsets stay deterministic;
# only the absolute calendar date varies. The audit period [_TODAY - 7,
# _TODAY - 1] then contains the plant effective dates by construction.
_TODAY = date.today()  # typing-smell: ignore[test-module-nondeterminism]: stuck_* matviews use CURRENT_TIMESTAMP — plants must be in the past relative to NOW (see WHY block above)
# BC.4d — typed window. `trailing_days_ending_yesterday(_TODAY, 7)` yields
# `[_TODAY - 7, _TODAY - 1]` — same shape as the prior tuple, now with the
# audit-window convention encoded in the constructor name. Threaded into
# `apply_db_seed(plant_window=_PERIOD)` so the L1-invariant spine
# generators land plants on `_PERIOD.end` (the most-recently-closed
# auditable day) by construction — kills the chronic v11.10.0 off-by-one
# (plant was landing on `_TODAY`, outside `[_TODAY - 7, _TODAY - 1]`).
_PERIOD: DateInterval = DateInterval.trailing_days_ending_yesterday(_TODAY, 7)


# U.8.b.5 — Per-dialect config files. The matrix runs one cell per
# (invariant, dialect); each dialect's cell loads its own config so
# the seed + audit PDF render hit the dialect-specific datasource and
# the dashboard walk lands on the dialect-prefixed L1 dashboard.
# Operator runs the matrix via separate ``-c run/config.{postgres,oracle}.yaml``
# invocations (CI matrix); locally a single dialect run is fine —
# missing-config-file dialects skip cleanly.
_DIALECT_CONFIG_PATHS: dict[str, Path] = {
    "postgres": Path("run/config.postgres.yaml"),
    "oracle": Path("run/config.oracle.yaml"),
}


@pytest.fixture(scope="module", params=["postgres", "oracle"])
def dialect_cfg(request):
    """Per-dialect (cfg, cfg_path, dialect_enum) — module-scoped.

    Skips cleanly when the dialect's config file is absent OR when
    its ``demo_database_url`` is unset, so an operator with only one
    dialect set up locally still gets the other dialect's cells to
    pass through as ``skipped`` rather than fail. CI matrix runs both
    dialects in parallel via separate ``-c run/config.{postgres,oracle}.yaml``
    invocations; each invocation drives just one dialect and the
    other is irrelevant for that cell.

    AB.7.1a — ``RECON_GEN_CONFIG`` env override IS now honored when its
    ``dialect:`` field matches the parametrize dialect. release.yml +
    runner ``aw``-target cells generate a synthesized cfg at
    ``/tmp/release-e2e.yaml`` (or per-cell prefix) and set
    ``RECON_GEN_CONFIG`` to point at it; the canonical
    ``run/config.<dialect>.yaml`` path is gitignored so CI runners
    that clone fresh never have it — silently skipping the 4-way
    agreement test (the canonical release gate). Honoring the env
    override fixes the silent-skip. Mismatched dialects still fall
    through to the hardcoded path so the sibling dialect's cell
    sees its own canonical cfg.
    """
    from recon_gen.common.config import load_config
    from recon_gen.common.env_keys import RECON_GEN_CONFIG as _RGC

    dialect_name: str = request.param
    # m.4.c — per-cell dialect-mismatch skip. See docstring on
    # `_env_demo_url_dialect` above.
    env_url_dialect = _env_demo_url_dialect()
    if env_url_dialect is not None and env_url_dialect != dialect_name:
        pytest.skip(
            f"runner cell's RECON_GEN_DEMO_DATABASE_URL implies "
            f"dialect={env_url_dialect!r}; this {dialect_name!r} "
            f"parametrization would route to the wrong DB. The sibling "
            f"sp_{env_url_dialect[:2]}_lo cell handles {dialect_name!r}."
        )
    # Y.2.browser.triage — `aw`-target analogue of the skip above. An `aw`
    # cell doesn't set RECON_GEN_DEMO_DATABASE_URL (it uses the operator's
    # external Aurora/Oracle), but the runner DOES inject RECON_GEN_CONFIG =
    # the cell's *dialect* cfg (`run/config.postgres.yaml` for the pg cell).
    # That cell only seeded that one dialect's DB with the cell-prefixed
    # tables, so the other dialect's parametrization would `ORA-00942` /
    # UndefinedTable. Skip it; the sibling `sp_<other>_aw` cell runs that
    # dialect's agreement check against its own seeded DB. When RECON_GEN_CONFIG
    # is unset (operator running pytest directly with both DBs seeded), the
    # both-dialects flow is unchanged.
    from recon_gen.common.env_keys import RECON_GEN_CONFIG
    qs_gen_cfg = RECON_GEN_CONFIG.get_or_none()  # Path | None — coercer=Path
    if qs_gen_cfg is not None:
        low = str(qs_gen_cfg).lower()
        cfg_dialect = (
            "postgres" if "postgres" in low
            else "oracle" if "oracle" in low
            else None
        )
        if cfg_dialect is not None and cfg_dialect != dialect_name:
            pytest.skip(
                f"runner cell's RECON_GEN_CONFIG={qs_gen_cfg!r} implies "
                f"dialect={cfg_dialect!r}; this {dialect_name!r} cell would "
                f"walk tables it never seeded. The sibling sp_{cfg_dialect[:2]}_<tgt> "
                f"cell handles {dialect_name!r}."
            )
    # AB.7.1a — prefer the runtime `RECON_GEN_CONFIG` cfg when its
    # `dialect:` field matches the parametrize dialect. release.yml +
    # `aw`-target runner cells generate a synthesized cfg outside the
    # gitignored `run/` dir and set `RECON_GEN_CONFIG` to it; the
    # canonical path lookup would silently skip every release-gate
    # 4-way agreement run otherwise.
    cfg_path: Path | None = None
    env_cfg_path = _RGC.get_or_none()
    if env_cfg_path is not None:
        env_cfg_path_p = Path(env_cfg_path)
        if env_cfg_path_p.exists():
            try:
                env_loaded = load_config(str(env_cfg_path_p))
            except Exception:  # noqa: BLE001 — defensive; fall through
                env_loaded = None
            if (
                env_loaded is not None
                and env_loaded.dialect.value == dialect_name
            ):
                cfg_path = env_cfg_path_p
    if cfg_path is None:
        cfg_path = _DIALECT_CONFIG_PATHS[dialect_name]
    if not cfg_path.exists():
        pytest.skip(
            f"{cfg_path} not present — {dialect_name} dialect cell "
            f"skipped. The other dialect still runs; CI runs each "
            f"dialect cell with its own ``-c run/config.{dialect_name}.yaml`` "
            f"or via `RECON_GEN_CONFIG=<path>` when the canonical path "
            f"is gitignored (release.yml + `aw`-target cells)."
        )
    loaded = load_config(str(cfg_path))
    if loaded.demo_database_url is None:
        pytest.skip(
            f"{cfg_path} has no demo_database_url — {dialect_name} "
            f"dialect cell skipped. The three-way agreement test "
            f"needs a seedable DB to plant the scenario."
        )
    dialect_enum = (
        Dialect.ORACLE if dialect_name == "oracle" else Dialect.POSTGRES
    )
    if loaded.dialect is not dialect_enum:
        pytest.skip(
            f"{cfg_path} declares dialect={loaded.dialect.value} but "
            f"the matrix expects dialect={dialect_name}. Fix the YAML "
            f"or rename the file so the matrix loads it under the "
            f"right cell."
        )
    return (loaded, cfg_path, dialect_enum)


@pytest.fixture(scope="module")
def per_dialect_cfg(dialect_cfg):
    """The loaded ``Config`` for this dialect cell.

    Distinct from the conftest session-scoped ``cfg`` fixture (which
    loads whatever config file matches first); this one is matrix-
    parametrized. The seeded_audit / embed_url / dashboard-id
    derivations downstream consume THIS, not the conftest one.
    """
    return dialect_cfg[0]


@pytest.fixture(scope="module")
def per_dialect_account_id(per_dialect_cfg) -> str:
    return per_dialect_cfg.aws_account_id


@pytest.fixture(scope="module")
def per_dialect_region(per_dialect_cfg) -> str:
    return per_dialect_cfg.aws_region


@pytest.fixture(scope="module")
def per_dialect_qs_client(per_dialect_region):
    """Boto3 QuickSight client for this dialect's dashboard region.

    Module-scoped — one client per (region, dialect). Cheaper than
    function-scoped + harmless to share across the parametrized
    invariants in this module.
    """
    import boto3
    return boto3.client("quicksight", region_name=per_dialect_region)


@pytest.fixture(scope="module")
def per_dialect_l1_dashboard_id(per_dialect_cfg) -> str:
    """L1 dashboard ID under this dialect's deployment_name prefix.

    Z.C — derives the same way the L1 app's deploy does:
    ``<deployment_name>-l1-dashboard`` (collapsed from the prior
    M.2d.3 two-segment ``<resource_prefix>-<l2_prefix>-`` shape).
    """
    return per_dialect_cfg.prefixed("l1-dashboard")


@pytest.fixture(scope="module")
def seeded_audit(dialect_cfg, tmp_path_factory):
    """Seed dialect-specific DB with the spec_example scenario, render
    audit PDF against the same DB.

    Module-scoped — the seed + render is the expensive setup; both
    the dashboard walk and the PDF extraction reuse the same
    artifacts. Returns ``(pdf_path, scenario)``.
    """
    from tests.e2e._seed_helpers import apply_db_seed

    cfg, cfg_path, dialect = dialect_cfg

    instance = load_instance(_l2_yaml_for_test())
    conn = connect_demo_db(cfg)
    try:
        scenario = apply_db_seed(
            conn, instance,
            prefix=cfg.db_table_prefix,
            mode="l1_invariants",
            today=_TODAY,
            plant_window=_PERIOD,
            dialect=dialect,
            include_baseline=False,
        )
    finally:
        conn.close()

    out = tmp_path_factory.mktemp("audit-pdf") / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(cfg_path),
            "--l2", str(_l2_yaml_for_test()),
            "--period", f"{_PERIOD.start.isoformat()}..{_PERIOD.end.isoformat()}",
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output
    return (out, scenario)


@pytest.fixture
def per_dialect_qs_driver(
    request,
    per_dialect_cfg,
    per_dialect_region,
    per_dialect_account_id,
    per_dialect_l1_dashboard_id,
    per_dialect_qs_client,
):  # type: ignore[no-untyped-def]: return-type annotation would force a QsEmbedDriver import at module scope
    """Function-scoped ``QsEmbedDriver`` aimed at this dialect's L1
    dashboard. Embed URLs are single-use, so the driver gets a fresh
    page per test.

    Pre-flight: confirm the dashboard actually exists before opening
    the embed URL. ``QsEmbedDriver.open()`` (via
    ``generate_dashboard_embed_url``) happily produces a URL that
    points at a non-existent dashboard, which then loads as an empty
    QS error page that times out the "wait for sheet tabs" gate with
    a confusing 30s timeout. A proactive ``describe_dashboard`` check
    turns that into an immediate skip with the actual deploy command
    to fix it.

    Tall viewport (1600×4000) so stacked KPI + chart + table layouts
    (Pending Aging / Unbundled Aging / Supersession Audit) keep the
    detail table inside the initial render area; QS lazy-renders below
    the fold and ``table_row_count`` (the page-size-bump path) needs
    the .grid-container close enough to the viewport to scroll into.

    **Yields ``None`` (does NOT skip the test) when the QS leg can't run**
    — ``RECON_E2E_USER_ARN`` unset (the runner derives it from
    ``cfg.auth.aws_profile``; export it for a direct ``pytest`` run), or
    the L1 dashboard isn't deployed for this dialect (a SQLite cell has
    no QS dashboard by construction). The 4-way test then runs the other
    legs — direct-SQL + App2 + PDF — as a clean 3-way. (X.2.j.C: per-leg
    skipping, not per-test.)
    """
    try:
        per_dialect_qs_client.describe_dashboard(
            AwsAccountId=per_dialect_account_id,
            DashboardId=per_dialect_l1_dashboard_id,
        )
    except per_dialect_qs_client.exceptions.ResourceNotFoundException:
        # Not deployed for this dialect (e.g. SQLite — no QS datasource).
        # The other legs still run; CI deploys spec_example before this
        # test via `recon-gen json apply -c <dialect-config> --execute
        # --l2 tests/l2/spec_example.yaml`.
        yield None
        return
    # AA.H.12 — shared lifecycle. yield-None policy (NOT skip) when QS
    # is unavailable so the 4-way test still runs the other legs
    # (direct SQL + App2 + PDF) as a clean 3-way. Tall viewport keeps
    # the stacked detail tables (Pending Aging / Unbundled Aging /
    # Supersession Audit) in the initial render area. AA.H.10 capture
    # hook is wired by the lifecycle primitive — pre-AA.H.10 this
    # fixture silently dropped artifacts (today's chain's 4 audit-
    # agreement failures had no DOM / screenshot / trace).
    from tests.e2e._drivers._lifecycle import qs_driver_or_none

    with qs_driver_or_none(
        request,
        cfg=per_dialect_cfg,
        account_id=per_dialect_account_id,
        region=per_dialect_region,
        viewport=(1600, 4000),
    ) as driver:
        yield driver  # may be None if get_user_arn failed


@pytest.fixture(scope="module")
def per_dialect_matview_prefix(per_dialect_cfg) -> str:
    """The matview-name prefix this dialect cell's DB was seeded with —
    ``cfg.db_table_prefix`` (Z.C: replaces the prior
    ``L2Instance.instance`` field, which doubled as the DB-table prefix).
    The L1-invariant matviews are ``<prefix>_drift`` /
    ``<prefix>_overdraft`` / ``<prefix>_limit_breach`` / etc. — the
    direct-SQL anchor (X.2.j.B.2) queries those straight off the DB."""
    return per_dialect_cfg.db_table_prefix


@pytest.fixture
def per_dialect_conn(per_dialect_cfg):  # type: ignore[no-untyped-def]: yields a per-driver DB connection (psycopg/oracledb/sqlite3) — no shared type
    """Function-scoped raw DB connection to this dialect cell's seeded DB.

    The direct-SQL anchor (the 5th leg in ``scenario ⊆ direct_SQL ==
    PDF == QS == App2``) needs to ``SELECT`` from the matviews; the
    ``seeded_audit`` fixture's own conn is closed by the time the asserts
    run, so this opens a fresh one per test (cheap — a handful of
    ``count(*)`` / key-set queries)."""
    conn = connect_demo_db(per_dialect_cfg)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def per_dialect_app2_results(per_dialect_cfg, seeded_audit):  # type: ignore[no-untyped-def]: returns a dict; annotating would force the imports below to module scope
    """The App2 leg's data, read once up-front (X.2.j.B.1).

    Spins this dialect cell's L1 dashboard tree via ``App2Driver.serving``
    against the seeded DB, walks every L1-invariant sheet, collects each
    one's row count (+ DOM-window size + natural-key set for the
    flat-shape invariants), then **tears the App2 server + browser down
    before returning** — so the per-test ``per_dialect_qs_driver`` (a
    *second* Playwright sync context) doesn't collide with App2's. Sync
    Playwright is one-context-per-thread: two open at once → "Playwright
    Sync API inside the asyncio loop". The original module-scoped
    *live-driver* fixture held App2's context open across all 6 tests,
    which blew up the QS leg on a real deploy (the leg auto-skipped
    locally — no deployed dashboard — so it slipped through; the Aurora
    cell caught it). Reading the App2 data up-front and yielding a plain
    dict sidesteps it entirely.

    Per-invariant entry: ``{"count": int}`` for the divergent-shape ones,
    plus ``"seen": int`` (the DOM-visible row count, for the truncation
    guard) and ``"keys": set`` (the natural-key set, for row-identity)
    for the flat-shape ones.

    Depends on ``seeded_audit`` so the seed lands before the reads.
    Module-scoped — the seed + the 6-sheet walk is the expensive setup.
    Built the same way ``cli/_html_serve.py::build_real_app`` does (register L1
    datasets → build tree → live-DB ``(visual, options)`` fetcher pair off
    one pool; ``make_live_db_fetchers_for_app`` — the *plural*, since the
    L1 dashboard has dataset-sourced dropdowns).
    """
    _ = seeded_audit  # ordering dep only — see docstring
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.apps.l1_dashboard.datasets import (
        build_all_l1_dashboard_datasets,
    )
    from tests.e2e._harness_html2 import make_live_db_fetchers_for_app

    instance = load_instance(_l2_yaml_for_test())
    build_all_l1_dashboard_datasets(per_dialect_cfg, instance)
    tree_app = build_l1_dashboard_app(per_dialect_cfg, l2_instance=instance)
    if tree_app.analysis is None:
        tree_app.emit_analysis()
    visual_fetcher, options_fetcher = make_live_db_fetchers_for_app(
        tree_app=tree_app, cfg=per_dialect_cfg,
    )
    results: dict[str, dict[str, object]] = {}
    with App2Driver.serving(
        cfg=per_dialect_cfg,
        tree_app=tree_app, sheet=tree_app.analysis.sheets[0],
        data_fetcher=visual_fetcher, options_fetcher=options_fetcher,
        dashboard_id="l1", dashboard_title="L1 Dashboard",
    ) as driver:
        driver.open("l1")
        for inv in _ALL_INVARIANTS:
            entry: dict[str, object] = {
                "count": count_l1_invariant_rows(driver, inv, _PERIOD),
            }
            if inv in _FLAT_SHAPE:
                entry["seen"] = l1_invariant_rows_seen(driver, inv, _PERIOD)
                entry["keys"] = l1_invariant_row_keys(driver, inv, _PERIOD)
            results[inv] = entry
    # App2 server + browser torn down by the ``with`` exit — the returned
    # dict holds everything the per-test asserts need; no live driver
    # lingers to collide with ``per_dialect_qs_driver``.
    return results


# Flat-shape invariants — one matview row per (account, day[, transfer_type]),
# and the dashboard table + audit PDF section show that same flat row set.
# For these the 4-way assert tightens to row-identity (the natural-key
# set), not just a count. The rest (stuck_* / supersession) are
# divergent-shape — the PDF aggregates into roll-up tables while the QS +
# App2 detail tables show raw matview rows — so those stay count-level
# (the renderers still must agree with the matview ground truth on the
# count; only the PDF count diverges, and only `pdf_count >= expected`
# is asserted for it there).
_FLAT_SHAPE: frozenset[str] = frozenset({"drift", "overdraft", "limit_breach"})


# All 6 L1 invariants. The current-state invariants
# (stuck_pending / stuck_unbundled / supersession) used to xfail with
# "No data found for visual" — root-caused to the L1 dashboard
# applying its `[today-7, today]` default date scope to matviews that
# have no date column on the audit-PDF side, dropping rows whose
# `posting` was outside the window even though the matview held them.
# Fixed by removing the date-scope FilterGroups from the current-state
# sheets (stucks are stuck until cleared regardless of analyst's
# period of interest); the audit PDF and dashboard now agree by
# construction.
_ALL_INVARIANTS: tuple[str, ...] = (
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
)


@pytest.mark.parametrize("invariant", _ALL_INVARIANTS)
def test_invariant_four_way_agreement(
    seeded_audit,
    per_dialect_qs_driver,
    per_dialect_app2_results,
    per_dialect_l1_dashboard_id,
    per_dialect_conn,
    per_dialect_matview_prefix,
    per_dialect_cfg,
    invariant,
):
    """Per-invariant 4-renderer agreement (X.2.j.B): the chain

        scenario_plants  ⊆  direct_matview_SELECT  ==  QS  ==  App2
                                       (==  PDF, flat-shape only)

    plus, for the flat-shape invariants (drift / overdraft / limit_breach),
    row-identity — the *set* of natural-key tuples, not just the count, so
    "same count, different rows" can't slip through.

    Anchors (5 of them; the 4 *renderers* + the scenario lower bound):

    - **scenario plants** (`expected_audit_counts`) — a *lower bound*.
      It's `⊆`, not `==`: a planted scenario can produce incidental
      same-class rows as a side-effect (the X.2.j.0 spike saw
      `drift_count=1` planted but 2 in the matview).
    - **direct matview SELECT** (`_matview_extract`) — the ground truth.
      Every renderer reads this matview; a renderer that shows a
      different count/row-set than a direct `SELECT` is buggy.
    - **audit PDF** (`_pdf_extract`) — count-only (the heuristic text
      parse gives a count, not row keys). `pdf >= expected` for every
      invariant; `pdf == direct` only for `drift` (the PDF section is a
      flat one-row-per-matview-row table there; for the others the PDF
      aggregates into parent-per-row + child-grouped roll-ups, so its
      count legitimately differs from the matview's).
    - **QS dashboard** (`_dashboard_extract` via `QsEmbedDriver`) — count
      via the page-size-bump path; row-keys via `table_rows` (the
      plants-only seed keeps these tables tiny, so the DOM window holds
      the full set — asserted via `l1_invariant_rows_seen` before
      trusting the key set).
    - **App2 dashboard** (`_dashboard_extract` via `App2Driver`) — same
      verbs (both speak `DashboardDriver`); count via `.table-pager-range`,
      row-keys via `table_rows`.

    Row-identity scope (X.2.j.B.3): flat-shape gets `scenario_keys ⊆
    direct_keys == App2_keys`, and additionally `== QS_keys` for `drift`
    (the QS Drift table's `business_day_start` column projection is the
    one the X.2.j.0 spike concretely verified; extending the QS-side key
    comparison to overdraft / limit_breach is `X.2.j.B.3-followon` — it
    needs a deployed dashboard to confirm those tables' day-column
    projection). Divergent-shape (stuck_* / supersession) stays
    count-level: `direct == QS == App2` and `… >= expected`.

    Skips degrade per-leg, not per-test: a missing QS deploy skips the QS
    *driver* fixture only — the direct-SQL + App2 + PDF legs still run
    (so e.g. a SQLite cell, which has no QS dashboard, becomes a clean
    3-way `scenario ⊆ direct == PDF* == App2`).
    """
    from tests.audit._matview_extract import MATVIEW_ANCHORED

    pdf_path, scenario = seeded_audit
    expected_obj = expected_audit_counts(scenario, _PERIOD)
    expected: int = getattr(expected_obj, f"{invariant}_count")
    is_flat = invariant in _FLAT_SHAPE

    # --- the ground-truth anchor: a direct SELECT against the matview ---
    # Flat-shape matviews carry a day column → narrow to the audit period;
    # stuck_* matviews have none → unfiltered. ``supersession`` has no
    # clean matview (the dashboard's "Transactions Audit" table + the
    # audit PDF each query their own shape over the base tables), so it
    # gets no direct-SQL anchor — the renderers must still agree with
    # each other (asserted below).
    direct_count: int | None = None
    if invariant in MATVIEW_ANCHORED:
        period_for_matview = _PERIOD if is_flat else None
        direct_count = count_l1_invariant_matview_rows(
            per_dialect_conn, per_dialect_matview_prefix, invariant,
            period_for_matview, per_dialect_cfg.dialect,
        )

    # --- the PDF + the two dashboard renderers ---
    pdf_count = count_invariant_table_rows(pdf_path, invariant)
    app2 = per_dialect_app2_results[invariant]
    app2_count = int(app2["count"])  # type: ignore[call-overload]: dict value is `object`; it's an int by construction in the fixture
    # The QS leg is optional — the fixture yields ``None`` when QS isn't
    # available for this dialect (SQLite cell, or RECON_E2E_USER_ARN unset).
    # The QS driver fixture's tall viewport keeps the detail table inside
    # the initial render area; ``count_l1_invariant_rows`` then handles
    # the per-invariant sheet switch + date-filter application + page-
    # size-bump-aware total via the ``DashboardDriver`` verbs.
    qs_count: int | None = None
    if per_dialect_qs_driver is not None:
        per_dialect_qs_driver.open(per_dialect_l1_dashboard_id)
        qs_count = count_l1_invariant_rows(
            per_dialect_qs_driver, invariant, _PERIOD,
        )

    # --- producer-side lower bounds (a failure here = the plant didn't
    # reach the matview, or a renderer dropped a row the matview holds) ---
    if direct_count is not None:
        assert direct_count >= expected, (
            f"Producer-side regression ({invariant}): scenario planted "
            f"{expected} rows but the {per_dialect_matview_prefix}_{invariant} "
            f"matview holds only {direct_count} for the period. Plant didn't "
            f"reach the matview, or the matview SQL drifted from the plant."
        )
    assert pdf_count >= expected, (
        f"Producer-side regression ({invariant}): scenario planted "
        f"{expected} rows but the PDF shows only {pdf_count}. Plant "
        f"didn't reach the matview, or the audit query / PDF render "
        f"dropped the row."
    )
    assert app2_count >= expected, (
        f"Producer-side regression ({invariant}): scenario planted "
        f"{expected} rows but the App2 dashboard shows only {app2_count}."
    )
    if qs_count is not None:
        assert qs_count >= expected, (
            f"Producer-side regression ({invariant}): scenario planted "
            f"{expected} rows but the QS dashboard shows only {qs_count}."
        )

    # --- the renderers agree (with the matview ground truth, and with
    # each other) ---
    if direct_count is not None:
        # QS + App2 each read the same matview; both detail tables show
        # its raw rows, so both counts must equal a direct SELECT.
        if qs_count is not None:
            assert qs_count == direct_count, (
                f"Renderer disagrees with the matview ({invariant}, QS): the "
                f"dashboard shows {qs_count} rows, a direct SELECT against "
                f"{per_dialect_matview_prefix}_{invariant} shows {direct_count}. "
                f"Same period ({_PERIOD.start}–{_PERIOD.end}), same matview."
            )
        assert app2_count == direct_count, (
            f"Renderer disagrees with the matview ({invariant}, App2): the "
            f"dashboard shows {app2_count} rows, a direct SELECT shows "
            f"{direct_count}. Same period, same matview."
        )
    elif qs_count is not None:
        # supersession — no matview anchor, but the two renderers query
        # the same base-table shape, so they must still agree.
        assert qs_count == app2_count, (
            f"Renderers disagree ({invariant}): QS shows {qs_count} rows, "
            f"App2 shows {app2_count}. Same base tables, same query shape."
        )
    # The PDF section is a flat one-row-per-matview-row table only for
    # ``drift``; for the others it aggregates (parent-per-row + child-
    # grouped roll-ups; supersession is a count-by-table+category roll-up)
    # so its count legitimately differs from the matview's — `pdf >=
    # expected` above is the meaningful PDF check there.
    if invariant == "drift" and direct_count is not None:
        assert pdf_count == direct_count, (
            f"Credibility contract broken ({invariant}): the audit PDF "
            f"shows {pdf_count} rows, the matview holds {direct_count}. "
            f"Same period ({_PERIOD.start}–{_PERIOD.end}). The regulator-facing "
            f"PDF and the live matview disagree."
        )

    if not is_flat:
        return

    # --- row-identity for the flat-shape invariants (X.2.j.B.3) ---
    # ``table_rows`` returns the renderer's DOM-visible window — confirm
    # it wasn't truncated before trusting the key sets. (Plants-only seed
    # keeps these tables tiny; a denser seed that grows them past the
    # window trips this loudly rather than passing a partial comparison.)
    # App2's ``seen`` / ``keys`` were captured up-front by
    # ``per_dialect_app2_results`` (the App2 server + browser are torn
    # down by the time this test runs — see that fixture's docstring).
    app2_seen = int(app2["seen"])  # type: ignore[call-overload]: dict value is `object`; int by construction (flat-shape entry)
    assert app2_seen == direct_count, (
        f"App2 table window truncated ({invariant}): {app2_seen} of "
        f"{direct_count} rows visible — the row-identity comparison would "
        f"be partial. (A denser seed needs a read-all path.)"
    )

    key_cols = key_columns_for(invariant)
    direct_keys = l1_invariant_matview_row_keys(
        per_dialect_conn, per_dialect_matview_prefix, invariant,
        _PERIOD, per_dialect_cfg.dialect,
    )
    app2_keys = app2["keys"]  # set[tuple[...]] by construction (flat-shape entry)
    scenario_keys = set(getattr(expected_obj, f"{invariant}_account_days"))

    assert scenario_keys <= direct_keys, (
        f"Planted {invariant} rows missing from the matview "
        f"({per_dialect_matview_prefix}_{invariant}): "
        f"{sorted(scenario_keys - direct_keys)} planted but absent. "
        f"Plant→matview gap."
    )
    assert direct_keys == app2_keys, (
        f"App2 disagrees with the matview on which {invariant} rows "
        f"({key_cols}): matview has {sorted(direct_keys)}, App2 shows "
        f"{sorted(app2_keys)}."
    )
    if invariant == "drift" and per_dialect_qs_driver is not None:
        # The QS Drift table's ``business_day_start`` projection is
        # spike-verified; extending the QS-side row-identity comparison
        # to overdraft / limit_breach is X.2.j.B.3-followon (needs a
        # deployed dashboard to confirm those tables' day-column
        # projection — the QS count + the App2 row-identity already cover
        # the bulk).
        qs_seen = l1_invariant_rows_seen(
            per_dialect_qs_driver, invariant, _PERIOD,
        )
        assert qs_seen == direct_count, (
            f"QS table window truncated ({invariant}): {qs_seen} of "
            f"{direct_count} rows visible."
        )
        qs_keys = l1_invariant_row_keys(
            per_dialect_qs_driver, invariant, _PERIOD,
        )
        assert direct_keys == qs_keys, (
            f"QS disagrees with the matview on which {invariant} rows "
            f"({key_cols}): matview has {sorted(direct_keys)}, QS shows "
            f"{sorted(qs_keys)}."
        )
