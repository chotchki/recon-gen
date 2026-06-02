# pyright: reportArgumentType=false
# BF.4/F: `_ALL_INVARIANTS` is `tuple[str, ...]` but pdf-extract takes
# `Invariant` Literal. Runtime correctness enforced by dict lookups.
"""CB.5 stage 2 — DB-tier producer: PDF count per L1 invariant.

One producer test per L1 invariant. Each producer:

1. Loads per-dialect cfg + seeds the DB.
2. Renders the audit PDF (`recon-gen audit apply --execute`) against
   the seeded DB.
3. Extracts the per-invariant table row count from the PDF (heuristic
   text parse via pypdf).
4. Writes the count as a JSON artifact (`db/<inv>_pdf_counts.json`).
5. Asserts the producer-side lower bound (`pdf_count >= expected`).

The validator reads this alongside the direct-matview / App2 / QS
artifacts and applies the agreement chain:

    scenario_plants ⊆ direct == QS == App2  (== PDF, drift only)

PDF count == direct only for `drift` (the PDF section is a flat
one-row-per-matview-row table there); the other invariants aggregate
into roll-up tables and the PDF count legitimately differs from the
matview count, so `pdf >= expected` is the meaningful PDF check.

Same module-scoped seed as `test_audit_invariants_direct.py` — under
the runner's xdist_group both files share one DB seed per cell (the
xdist_group marker pins them to one worker). Outside the runner the
seed runs once per file; the DB-side schema apply is DROP+CREATE so
re-seeding the same DB twice in a row is idempotent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from recon_gen.cli import main
from recon_gen.common.db import connect_demo_db
from recon_gen.common.l2 import load_instance

from tests.audit._pdf_extract import count_invariant_table_rows
from tests.audit._scenario_expectations import expected_audit_counts
from tests.e2e._agreement import write_rendered_rows
from tests.e2e._agreement_helpers import (
    ALL_L1_INVARIANTS,
    audit_window,
    l2_yaml_for_test,
    load_dialect_cfg,
    today_anchor,
)

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2.seed import ScenarioPlant
    from recon_gen.common.sql import Dialect


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.xdist_group("audit_dashboard_agreement_seed"),
]


_TODAY = today_anchor()
_PERIOD = audit_window(_TODAY)


@pytest.fixture(scope="module", params=["postgres", "oracle"])
def dialect_cfg(
    request: pytest.FixtureRequest,
) -> "tuple[Config, Path, Dialect]":
    return load_dialect_cfg(request.param)


@pytest.fixture(scope="module")
def seeded_audit(
    dialect_cfg: "tuple[Config, Path, Dialect]",
    tmp_path_factory: pytest.TempPathFactory,
) -> "tuple[Path, ScenarioPlant]":
    """Seed DB + render audit PDF. Module-scoped — both setup steps
    are expensive and shared across the 6 invariant cells."""
    from tests.e2e._seed_helpers import apply_db_seed

    cfg, cfg_path, dialect = dialect_cfg
    instance = load_instance(l2_yaml_for_test())
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
    )
    assert result.exit_code == 0, result.output
    return (out, scenario)


@pytest.mark.parametrize("invariant", ALL_L1_INVARIANTS)
def test_l1_invariant_pdf_count(
    seeded_audit: "tuple[Path, ScenarioPlant]",
    invariant: str,
) -> None:
    """PDF row count for one L1 invariant; writes the artifact the
    validator reads.

    Producer-side assertion: `pdf_count >= expected`. Catches "plant
    didn't reach the PDF" locally rather than detaching the agreement
    comparison.
    """
    pdf_path, scenario = seeded_audit
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
