# pyright: reportArgumentType=false
# BF.4/F: `_ALL_INVARIANTS` is `tuple[str, ...]` but matview-extract /
# pdf-extract helpers take narrower L1Invariant / Invariant Literals.
# Runtime correctness is enforced by the helpers' own dict lookups.
"""CB.7 (refactored 2026-06-02) — DB-tier producer: direct matview
SELECT + PDF row count per L1 invariant.

Merged from CB.5-era split into `test_audit_invariants_direct.py` +
`test_audit_invariants_pdf.py`; that decomposition put two writer
files into one `AGREEMENT_AUDIT` scope, which is the broken shape
documented in `tests/_marks.py::IsolationScope`. One producer file per
scope is the canonical pattern.

Per-dialect (postgres / oracle) parametrized. Per cell:

1. Loads the per-dialect cfg + seeds the DB
   (`apply_db_seed(mode="l1_invariants")` plants the scenario into the
   matviews).
2. Renders the audit PDF (`recon-gen audit apply --execute`) ONCE
   against the seeded DB (`audit_pdf` fixture; depends on `seeded_db`).
3. Two parametrized test functions assert producer-side lower bounds
   for each L1 invariant + write the artifacts the cross-renderer
   validator reads:
   - `test_l1_invariant_direct_extract` — direct matview SELECT, writes
     `<inv>_direct_rows.json` + `<inv>_direct_meta.json`
   - `test_l1_invariant_pdf_count` — PDF row count, writes
     `<inv>_pdf_counts.json`

The high-watermark validator in `tests/e2e/qs_browser/` reads these
alongside the App2 / QS artifacts and applies the agreement chain:

    scenario_plants ⊆ direct == QS == App2  (== PDF, drift only)

PDF count == direct only for `drift` (the PDF section is a flat
one-row-per-matview-row table there); the other invariants aggregate
into roll-up tables and the PDF count legitimately differs from the
matview count, so `pdf >= expected` is the meaningful PDF check.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import pytest
from click.testing import CliRunner

from recon_gen.cli import main
from recon_gen.common.db import connect_demo_db
from recon_gen.common.l2 import load_instance

from tests.audit._matview_extract import (
    MATVIEW_ANCHORED,
    count_l1_invariant_matview_rows,
    l1_invariant_matview_row_keys,
)
from tests.audit._pdf_extract import count_invariant_table_rows
from tests.audit._scenario_expectations import expected_audit_counts
from tests.e2e._agreement import write_rendered_rows
from tests.e2e._agreement_helpers import (
    ALL_L1_INVARIANTS,
    FLAT_SHAPE_INVARIANTS,
    audit_window,
    l2_yaml_for_test,
    load_dialect_cfg,
    today_anchor,
)

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.intervals import DateInterval
    from recon_gen.common.l2.seed import ScenarioPlant
    from recon_gen.common.sql import Dialect


from tests._marks import IsolationScope, isolation_producer  # noqa: E402

pytestmark = [
    pytest.mark.e2e,
    isolation_producer(IsolationScope.AGREEMENT_AUDIT),
    # Pin all tests in this writer file to ONE xdist worker so the
    # module-scope `seeded_db` + `audit_pdf` fixtures cache once per
    # (worker, dialect cell) and all tests share the same DB state.
    # Without this, `-n auto` scatters the 12+ parametrized cells
    # across workers; each worker reseeds the same scope-keyed prefix
    # → PG schema-create race.
    pytest.mark.xdist_group(IsolationScope.AGREEMENT_AUDIT.value),
]


_TODAY = today_anchor()
_PERIOD = audit_window(_TODAY)


@pytest.fixture(scope="module", params=["postgres", "oracle"])
def dialect_cfg(
    request: pytest.FixtureRequest,
) -> "tuple[Config, Path, Dialect]":
    """Per-dialect (cfg, cfg_path, dialect_enum). Skips cleanly on
    missing config / dialect mismatch / no demo_database_url."""
    return load_dialect_cfg(request.param)


@pytest.fixture(scope="module")
def dialect_isolated_cfg(
    request: pytest.FixtureRequest,
    dialect_cfg: "tuple[Config, Path, Dialect]",
    tmp_path_factory: pytest.TempPathFactory,
) -> "tuple[Config, Path, Dialect]":
    """Per-(module, worker, dialect) isolated cfg.

    Provider-marked isolation primitive for dialect-parametrized writer
    fixtures. The plain `isolated_cfg` fixture in
    `tests/e2e/db/conftest.py` serves the single-cfg case; this variant
    is its parallel for `dialect_cfg`-driven tests.

    CB.7 followup — uses `_resolve_isolation_suffix` so the scope-pinned
    suffix from `@isolation_producer(IsolationScope.AGREEMENT_AUDIT)`
    wins over the per-test hash. The app2 + qs_browser tier consumers
    mirror this fixture against the same scope, so all tiers read/write
    the same `<base>_x_aa` prefix.
    """
    from tests.e2e._isolation import _isolate_cfg, _resolve_isolation_suffix

    cfg, cfg_path, dialect = dialect_cfg
    suffix, _is_scope_pinned = _resolve_isolation_suffix(request, cfg)
    isolated_cfg = _isolate_cfg(
        cfg, suffix=suffix, tmp_path_factory=tmp_path_factory,
    )
    return isolated_cfg, cfg_path, dialect


@pytest.fixture(scope="module")
def seeded_db(
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
) -> "ScenarioPlant":
    """Seed dialect-specific DB with the spec_example scenario.

    Module-scoped — one seed per (file, dialect, xdist worker).
    Requests `dialect_isolated_cfg` (provider-marked isolation): the
    seed lands in a per-worker prefix, eliminating concurrent-DROP+CREATE
    races without needing `xdist_group` pinning.
    """
    from tests.e2e._seed_helpers import apply_db_seed

    cfg, _cfg_path, dialect = dialect_isolated_cfg
    instance = load_instance(l2_yaml_for_test())
    conn = connect_demo_db(cfg)
    try:
        scenario = apply_db_seed(
            conn, instance,
            prefix=cfg.db.table_prefix,
            mode="l1_invariants",
            today=_TODAY,
            plant_window=_PERIOD,
            dialect=dialect,
            include_baseline=False,
        )
    finally:
        conn.close()
    return scenario


@pytest.fixture(scope="module")
def audit_pdf(
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
    seeded_db: "ScenarioPlant",
    tmp_path_factory: pytest.TempPathFactory,
) -> "tuple[Path, ScenarioPlant]":
    """Render the audit PDF against the seeded DB. Module-scoped —
    one render per (file, dialect, xdist worker).

    Depends on `seeded_db` so the PDF includes the scenario plants;
    the isolated prefix + deployment name thread through to the CLI
    subprocess via env so it queries the same per-worker tables.
    """
    cfg, cfg_path, _dialect = dialect_isolated_cfg

    out = tmp_path_factory.mktemp("audit-pdf") / "report.pdf"
    cli_runner = CliRunner()
    result = cli_runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(cfg_path),
            "--l2", str(l2_yaml_for_test()),
            "--period",
            f"{_PERIOD.start.isoformat()}..{_PERIOD.end.isoformat()}",
            "-o", str(out),
            "--execute",
        ],
        env={
            "RECON_GEN_DB_TABLE_PREFIX": cfg.db.table_prefix,
            "RECON_GEN_DEPLOYMENT_NAME": cfg.aws.deployment_name,
        },
    )
    assert result.exit_code == 0, result.output
    return (out, seeded_db)


@pytest.fixture
def conn(dialect_isolated_cfg: "tuple[Config, Path, Dialect]") -> "Iterator[Any]":
    """Per-dialect raw DB connection. Thin wrapper over `connect_demo_db`
    because the test parametrizes over dialect and pytest doesn't let a
    file's local fixture override the canonical `db_conn` fixture (which
    takes the single `isolated_cfg`, not `dialect_isolated_cfg`)."""
    cfg, _, _ = dialect_isolated_cfg
    c = connect_demo_db(cfg)
    try:
        yield c
    finally:
        c.close()


def _serialize_keys(keys: "set[tuple[Any, ...]]") -> list[list[Any]]:
    """JSON-serialize a set of natural-key tuples → list of lists.

    `date` cells are stringified (ISO) so json.dumps doesn't crash;
    consumers re-parse them back via the same ISO shape. Sort for
    deterministic artifact bytes — diffing two runs of the same cell
    surfaces real drift, not key-order noise.
    """
    return sorted([_normalise_for_json(list(t)) for t in keys])


def _normalise_for_json(row: list[Any]) -> list[Any]:
    """Map non-JSON-native cells (date / datetime) to ISO strings."""
    from datetime import date, datetime
    out: list[Any] = []
    for cell in row:
        if isinstance(cell, datetime):
            out.append(cell.date().isoformat())
        elif isinstance(cell, date):
            out.append(cell.isoformat())
        else:
            out.append(cell)
    return out


# ---------------------------------------------------------------------
# Direct matview SELECT — assertion shape from pre-CB.7
#                          test_audit_invariants_direct.py
# ---------------------------------------------------------------------


@pytest.mark.parametrize("invariant", ALL_L1_INVARIANTS)
def test_l1_invariant_direct_extract(
    seeded_db: "ScenarioPlant",
    conn: Any,
    dialect_isolated_cfg: "tuple[Config, Path, Dialect]",
    invariant: str,
) -> None:
    """Direct matview SELECT for one L1 invariant; writes the artifact
    the validator reads.

    Producer-side assertion: the matview holds at least the planted
    count (`>= expected`). Catches "plant didn't reach the matview"
    locally rather than detaching the agreement comparison.

    `supersession` has no clean matview anchor (the dashboard's
    "Transactions Audit" + the audit PDF each query their own shape
    over the base tables) — for that one the artifact carries an
    empty rows list + `direct_count: null`. The validator handles the
    null shape by skipping the renderer⋈matview leg for supersession.
    """
    cfg, _cfg_path, dialect = dialect_isolated_cfg
    expected_obj = expected_audit_counts(seeded_db, _PERIOD)
    expected: int = getattr(expected_obj, f"{invariant}_count")
    is_flat = invariant in FLAT_SHAPE_INVARIANTS
    prefix = cfg.db.table_prefix

    direct_count: int | None = None
    direct_keys: set[tuple[Any, ...]] = set()
    if invariant in MATVIEW_ANCHORED:
        period_for_matview: DateInterval | None = (
            _PERIOD if is_flat else None
        )
        direct_count = count_l1_invariant_matview_rows(
            conn, prefix, invariant, period_for_matview, dialect,
        )
        if is_flat:
            direct_keys = l1_invariant_matview_row_keys(
                conn, prefix, invariant, _PERIOD, dialect,
            )

    if direct_count is not None:
        assert direct_count >= expected, (
            f"Producer-side regression ({invariant}): scenario "
            f"planted {expected} rows but the {prefix}_{invariant} "
            f"matview holds only {direct_count} for the period. "
            f"Plant didn't reach the matview, or the matview SQL "
            f"drifted from the plant."
        )

    payload: list[dict[str, Any]] = []
    for key_tuple in _serialize_keys(direct_keys):
        payload.append({"natural_key": key_tuple})
    write_rendered_rows("db", f"{invariant}_direct_rows", payload)

    write_rendered_rows("db", f"{invariant}_direct_meta", [
        {
            "direct_count": direct_count,
            "expected_count": expected,
            "is_flat": is_flat,
            "anchored": invariant in MATVIEW_ANCHORED,
        },
    ])


# ---------------------------------------------------------------------
# PDF count — assertion shape from pre-CB.7 test_audit_invariants_pdf.py
# ---------------------------------------------------------------------


@pytest.mark.parametrize("invariant", ALL_L1_INVARIANTS)
def test_l1_invariant_pdf_count(
    audit_pdf: "tuple[Path, ScenarioPlant]",
    invariant: str,
) -> None:
    """PDF row count for one L1 invariant; writes the artifact the
    validator reads.

    Producer-side assertion: `pdf_count >= expected`. Catches "plant
    didn't reach the PDF" locally rather than detaching the agreement
    comparison.
    """
    pdf_path, scenario = audit_pdf
    expected_obj = expected_audit_counts(scenario, _PERIOD)
    expected: int = getattr(expected_obj, f"{invariant}_count")
    pdf_count = count_invariant_table_rows(pdf_path, invariant)

    assert pdf_count >= expected, (
        f"Producer-side regression ({invariant}): scenario planted "
        f"{expected} rows but the PDF shows only {pdf_count}. Plant "
        f"didn't reach the matview, or the audit query / PDF render "
        f"dropped the row."
    )

    write_rendered_rows("db", f"{invariant}_pdf_counts", [
        {"pdf_count": pdf_count, "expected_count": expected},
    ])
