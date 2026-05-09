"""U.8.b — Three-way agreement test (release gate, both dialects).

Per-invariant contract for U.8.b's release gate:
    expected (from scenario) == PDF (extractor) == dashboard (Playwright)

Parametrized over both dialects (U.8.b.5): each invariant×dialect
cell seeds the dialect-specific DB, renders the audit PDF against
that DB, and walks the dashboard deployed under that dialect's
resource prefix.

Prerequisites (test skips per-dialect if missing):
  - ``QS_GEN_E2E=1`` (matches existing browser-test gate)
  - ``run/config.<dialect>.yaml`` exists with ``demo_database_url``
    set; missing config OR missing DB URL skips that dialect cell
    cleanly (the other dialect still runs)
  - The L1 dashboard for the default L2 instance (``spec_example``)
    is already deployed under the dialect's resource prefix:
        Postgres: ``qs-gen-postgres-spec_example-l1-dashboard``
        Oracle:   ``qs-gen-oracle-spec_example-l1-dashboard``
    A missing dashboard skips the dialect's cells with the deploy
    command needed to fix it. ``json apply --execute -c
    run/config.<dialect>.yaml`` against ``spec_example`` is the
    canonical pre-step; CI runs both cells in parallel via
    separate ``-c run/config.{postgres,oracle}.yaml`` invocations.

The scenario seed runs against the real ``demo_database_url`` —
DESTRUCTIVE for the ``spec_example_*`` prefix on each dialect's
DB. Other prefixes (the operator's actual data) are untouched
because the schema apply only drops + recreates the prefixed
objects.

Anchors on ``date.today()`` (not the M.2a.8 hash-lock 2030 date):
the stuck_pending / stuck_unbundled matviews compute age via
``CURRENT_TIMESTAMP - posting``, so plants pinned to a far-future
date land in the SQL future and never satisfy the age threshold.
Anchoring on real today keeps plants visible across all 6
invariants on both dialects.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from quicksight_gen.cli import main
from quicksight_gen.common.browser.helpers import (
    generate_dashboard_embed_url,
    wait_for_dashboard_loaded,
    webkit_page,
)
from quicksight_gen.common.db import connect_demo_db
from quicksight_gen.common.l2 import load_instance
from quicksight_gen.common.sql import Dialect

from tests.audit._dashboard_extract import count_l1_invariant_rows
from tests.audit._pdf_extract import count_invariant_table_rows
from tests.audit._scenario_expectations import expected_audit_counts


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser,
]


# Y.2.gate.m.4.c — per-cell dialect-mismatch skip. When the runner
# dispatches this test inside a per-cell ``lo``-target variant (e.g.,
# ``sp_pg_lo``), the cell's ``QS_GEN_DEMO_DATABASE_URL`` env override
# points at ONE container — the matching dialect's. The Oracle
# parametrization in that cell would have ``load_config`` route to a
# PG container with ``cfg.dialect=oracle`` (env URL wins; cfg yaml's
# dialect doesn't), driving downstream connection failures. Skip the
# mismatched parametrization; the sibling ``sp_or_lo`` cell runs the
# Oracle agreement check against its own container. ``aw`` cells (no
# env URL override) run both parametrizations against the operator's
# external Aurora + Oracle.
def _env_demo_url_dialect() -> str | None:
    from quicksight_gen.common.env_keys import QS_GEN_DEMO_DATABASE_URL
    env_url = QS_GEN_DEMO_DATABASE_URL.get_or_none()
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

    The Y.2.gate.m runner sets ``QS_GEN_TEST_L2_INSTANCE`` to a
    per-cell synthesized yaml whose ``instance`` field is the cell
    code (e.g., ``sp_pg_aw``). DB tables seeded under that prefix.
    Computed dashboard ID also derives from that instance via
    ``cfg.with_l2_instance_prefix(instance.instance)``.

    Outside the runner (operator running pytest directly), the env
    var is unset; fall back to the bundled spec_example fixture
    (the historical operator-driven shape).
    """
    from quicksight_gen.common.env_keys import QS_GEN_TEST_L2_INSTANCE
    env_val = QS_GEN_TEST_L2_INSTANCE.get_or_none()
    if env_val:
        return Path(env_val)
    return _SPEC_EXAMPLE_BUNDLED

# Anchor on real today so the stuck_* matviews' CURRENT_TIMESTAMP
# filter sees plants in the past. days_ago offsets stay deterministic;
# only the absolute calendar date varies. The audit period [_TODAY - 7,
# _TODAY - 1] then contains the plant effective dates by construction.
_TODAY = date.today()  # typing-smell: ignore[test-module-nondeterminism]: stuck_* matviews use CURRENT_TIMESTAMP — plants must be in the past relative to NOW (see WHY block above)
_PERIOD: tuple[date, date] = (
    _TODAY - timedelta(days=7),
    _TODAY - timedelta(days=1),
)


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

    The ``QS_GEN_CONFIG`` env override is intentionally ignored here —
    a per-dialect matrix needs both files visible by their canonical
    paths. Operators wanting to point at a non-canonical config should
    edit ``_DIALECT_CONFIG_PATHS`` for that test run.
    """
    from quicksight_gen.common.config import load_config

    dialect_name: str = request.param
    # m.4.c — per-cell dialect-mismatch skip. See docstring on
    # `_env_demo_url_dialect` above.
    env_url_dialect = _env_demo_url_dialect()
    if env_url_dialect is not None and env_url_dialect != dialect_name:
        pytest.skip(
            f"runner cell's QS_GEN_DEMO_DATABASE_URL implies "
            f"dialect={env_url_dialect!r}; this {dialect_name!r} "
            f"parametrization would route to the wrong DB. The sibling "
            f"sp_{env_url_dialect[:2]}_lo cell handles {dialect_name!r}."
        )
    cfg_path = _DIALECT_CONFIG_PATHS[dialect_name]
    if not cfg_path.exists():
        pytest.skip(
            f"{cfg_path} not present — {dialect_name} dialect cell "
            f"skipped. The other dialect still runs; CI runs each "
            f"dialect cell with its own ``-c run/config.{dialect_name}.yaml``."
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
    """L1 dashboard ID under this dialect's resource prefix.

    Derives the same way the L1 app's deploy does: ``<resource_prefix>-
    <l2_prefix>-l1-dashboard``. l2_prefix is taken from the bundled
    ``spec_example`` (the default L2 instance) since this test is
    pinned to that instance.
    """
    instance = load_instance(_l2_yaml_for_test())
    cfg_with_prefix = (
        per_dialect_cfg
        if per_dialect_cfg.l2_instance_prefix is not None
        else per_dialect_cfg.with_l2_instance_prefix(str(instance.instance))
    )
    return cfg_with_prefix.prefixed("l1-dashboard")


@pytest.fixture(scope="module")
def seeded_audit(dialect_cfg, tmp_path_factory):
    """Seed dialect-specific DB with the spec_example scenario, render
    audit PDF against the same DB.

    Module-scoped — the seed + render is the expensive setup; both
    the dashboard walk and the PDF extraction reuse the same
    artifacts. Returns ``(pdf_path, scenario)``.
    """
    from tests.e2e._harness_seed import apply_db_seed

    cfg, cfg_path, dialect = dialect_cfg

    instance = load_instance(_l2_yaml_for_test())
    conn = connect_demo_db(cfg)
    try:
        scenario = apply_db_seed(
            conn, instance,
            mode="l1_invariants",
            today=_TODAY,
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
            "--from", _PERIOD[0].isoformat(),
            "--to", _PERIOD[1].isoformat(),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output
    return (out, scenario)


@pytest.fixture
def embed_url(
    per_dialect_region,
    per_dialect_account_id,
    per_dialect_l1_dashboard_id,
    per_dialect_qs_client,
) -> str:
    """Function-scoped — embed URLs are single-use, fresh per test.

    Pre-flight: confirm the dashboard actually exists before
    generating the embed URL. ``generate_dashboard_embed_url``
    happily returns a URL pointing at a non-existent dashboard,
    which then loads as an empty QS error page that times out the
    "wait for sheet tabs" gate with a confusing 30s timeout. A
    proactive ``describe_dashboard`` check turns that into an
    immediate skip with the actual deploy command to fix it.
    """
    try:
        per_dialect_qs_client.describe_dashboard(
            AwsAccountId=per_dialect_account_id,
            DashboardId=per_dialect_l1_dashboard_id,
        )
    except per_dialect_qs_client.exceptions.ResourceNotFoundException:
        pytest.skip(
            f"L1 dashboard {per_dialect_l1_dashboard_id!r} not deployed "
            f"in account {per_dialect_account_id} region "
            f"{per_dialect_region}. Deploy it first: `quicksight-gen "
            f"json apply -c <dialect-config> --execute --l2 "
            f"tests/l2/spec_example.yaml`, then re-run this test."
        )
    return generate_dashboard_embed_url(
        aws_account_id=per_dialect_account_id,
        aws_region=per_dialect_region,
        dashboard_id=per_dialect_l1_dashboard_id,
    )


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
def test_invariant_three_way_agreement(
    seeded_audit, embed_url, page_timeout, visual_timeout,
    invariant,
):
    """Per-invariant: PDF count and dashboard count both >= expected
    count, AND PDF count == dashboard count.

    Three asserts so a failure points at WHICH side broke:
      - expected > PDF: producer-side regression (SQL / matview /
        PDF rendering pipeline drifted from what the plant emitted)
      - expected > dashboard: same producer side, different output
        target (the dashboard reads the same matview, so unless QS
        is doing something exotic the numbers should match)
      - PDF != dashboard: the credibility contract broke directly —
        regulator and operator are seeing different numbers for the
        same matview + period

    NOTE on the strict ``PDF == dashboard`` assert: this works for
    drift because the PDF section and the dashboard's "Leaf Account
    Drift" table both show every drift matview row in one flat
    table. For other invariants the shapes diverge — the PDF
    aggregates parent + child accounts into a parent-per-row table
    + a "Child Accounts Grouped by Parent Role" table while the
    dashboard typically shows raw matview rows on its detail
    table. Where these counts diverge, it's a data-shape contract
    mismatch worth investigating, not necessarily a bug. U.8.b.4
    runs all 6 to surface every shape mismatch as concrete data;
    U.8.b's future work then either aligns the shapes or shifts
    the assert to row-identity matching (account_id + day) instead
    of count.
    """
    pdf_path, scenario = seeded_audit
    expected = getattr(
        expected_audit_counts(scenario, _PERIOD), f"{invariant}_count",
    )
    pdf_count = count_invariant_table_rows(pdf_path, invariant)

    # 1600×4000 — tall viewport so stacked KPI + chart + table layouts
    # (Pending Aging / Unbundled Aging / Supersession Audit) keep the
    # detail table inside the initial render area. QS lazy-renders
    # below-the-fold visuals; without the tall viewport,
    # count_table_total_rows times out waiting for cells that never
    # mount. Same pattern as the M.4.1.k harness.
    with webkit_page(headless=True, viewport=(1600, 4000)) as page:
        page.goto(embed_url, timeout=page_timeout)
        wait_for_dashboard_loaded(page, timeout_ms=page_timeout)
        dashboard_count = count_l1_invariant_rows(
            page, invariant, _PERIOD, timeout_ms=visual_timeout,
        )

    assert pdf_count >= expected, (
        f"Producer-side regression ({invariant}): scenario planted "
        f"{expected} rows but PDF shows only {pdf_count}. Plant "
        f"didn't reach the matview, or audit query / PDF render "
        f"dropped the row."
    )
    assert dashboard_count >= expected, (
        f"Producer-side regression ({invariant}): scenario planted "
        f"{expected} rows but dashboard shows only {dashboard_count}. "
        f"Plant didn't reach the matview."
    )
    assert dashboard_count == pdf_count, (
        f"Credibility contract broken ({invariant}): dashboard "
        f"shows {dashboard_count} rows, PDF shows {pdf_count}. "
        f"Same period ({_PERIOD[0]}–{_PERIOD[1]}), same matview, "
        f"different counts."
    )
