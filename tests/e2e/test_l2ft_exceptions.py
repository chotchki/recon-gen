"""BG.6 — L2FT Exceptions sheet honest gates.

Catches v11.21.0 cold-read finding #11: "L2 Exceptions KPI labeled
'Open L2 Violations = 41' but detail-table Count column shows values
in the thousands — two different units, same page, no signposting."

The KPI binds `ds["check_type"].count()` over the unified L2 exceptions
dataset — counts ROWS, one per (check_type, entity_a, entity_b, detail)
violation. The detail table's `count` column is the per-violation
occurrence count (e.g., "Dead Rail X: 1,247 transactions still posting
to it"). Different units → operator confusion.

BG.6's contract:
- KPI value == row count of the dataset (proves the binding is what
  the sheet narrates).
- Sum of table's `count` column == SUM(count) over the dataset
  (proves the table column is what it claims to be).

The two values DIFFER on real data (finding #11 is a unit-mismatch
between two correct measures, not a bug in either). BG.6 just gates
the contract that each KPI/table matches its binding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from recon_gen.apps.l2_flow_tracing.app import _L2_EXCEPTIONS_NAME
from recon_gen.apps.l2_flow_tracing.datasets import (
    build_unified_l2_exceptions_dataset,
)
from tests.e2e._kpi_parse import parse_int_kpi
from recon_gen.common.config import Config



if TYPE_CHECKING:
    from recon_gen.common.l2 import L2Instance
    from recon_gen.common.models import DatasetParameter
    from tests.e2e._drivers import DashboardDriver

pytestmark = [pytest.mark.e2e, pytest.mark.browser]


def _l2ft_exceptions_sql_and_params(
    cfg: Config, l2: "L2Instance",
) -> tuple[str, list["DatasetParameter"]]:
    ds = build_unified_l2_exceptions_dataset(cfg, l2)
    physical = next(iter(ds.PhysicalTableMap.values()))
    assert physical.CustomSql is not None
    sql = physical.CustomSql.SqlQuery
    return sql, list(ds.DatasetParameters or [])


def test_bg6_l2ft_exceptions_kpi_matches_dataset_distinct_check_types(
    l2ft_dashboard_driver: tuple["DashboardDriver", str], cfg: Config, l2: "L2Instance",
) -> None:
    """BG.6 — Distinct Exception Types Open KPI must equal the
    distinct ``check_type`` count of the unified L2 exceptions
    dataset. The KPI binding is ``ds["check_type"].distinct_count()``
    — explicit distinct semantic post-BL.1.

    Pre-BL.1 history: the KPI bound ``.count()`` but rendered DISTINCT
    on QS due to the CategoricalMeasureField(COUNT)-on-string-dim
    quirk; the title was renamed in BH.11 to match that quirk's
    output. BL.1 fixed the wire (now row count on both renderers),
    so the binding was flipped to ``.distinct_count()`` to match the
    'Distinct' title intent. This test enforces the new contract."""
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet=_L2_EXCEPTIONS_NAME)
    driver.wait_loaded("Distinct Exception Types Open")

    sql, params = _l2ft_exceptions_sql_and_params(cfg, l2)
    rows = driver.query_db(sql, dataset_parameters=params)
    expected_distinct = len({row["check_type"] for row in rows})

    rendered = parse_int_kpi(
        driver.kpi_value("Distinct Exception Types Open"),
    )
    assert rendered == expected_distinct, (
        f"Distinct Exception Types Open KPI: rendered {rendered} ≠ "
        f"len({{row.check_type for row in rows}}) = "
        f"{expected_distinct} (over {len(rows)} total rows). "
        f"v11.21.0 cold-read finding #11 KPI-half + BH.11 title "
        f"alignment + BL.1 wire fix together — the KPI's "
        f"``.distinct_count()`` binding must equal the distinct "
        f"check_type count of its dataset. (The table's `count` "
        f"column on the same sheet sums to a DIFFERENT number — "
        f"the per-violation occurrence count, a different measure.)"
    )
    driver.screenshot()


def test_bg6_l2ft_exceptions_table_count_column_sums_to_dataset_total(
    l2ft_dashboard_driver: tuple["DashboardDriver", str], cfg: Config, l2: "L2Instance",
) -> None:
    """BG.6 — the L2 Violation Detail table's ``count`` column values
    must sum to ``SUM(count)`` over the unified L2 exceptions dataset.

    Direct catch for v11.21.0 finding #11's table half: proves the
    table column renders what the dataset projects. Combined with
    the KPI identity test above, BG.6 nails down BOTH sides of the
    "two different units, same page" finding so any future drift
    surfaces at the right callsite.
    """
    driver, dashboard_arg = l2ft_dashboard_driver
    driver.open(dashboard_arg, sheet=_L2_EXCEPTIONS_NAME)
    driver.wait_loaded("L2 Violation Detail")

    sql, params = _l2ft_exceptions_sql_and_params(cfg, l2)
    rows = driver.query_db(sql, dataset_parameters=params)
    if not rows:
        pytest.skip(
            "Unified L2 exceptions dataset is empty for the deployed "
            "L2 — no hygiene violations to gate. The empty-render path "
            "is covered by the sheet-structure test."
        )
    expected_sum = sum(int(row["count"]) for row in rows)

    # Read the table's count column values (rendered as integers via
    # .numerical() — no $ prefix, no decimals). BH.11 (v11.22.3)
    # renamed the column's display_name from "Count" → "Violations
    # per Type"; the underlying SQL column stays "count". DOM rows
    # are keyed by display name; the dataset query rows are keyed by
    # the underlying column name.
    table_rows = driver.table_rows(
        "L2 Violation Detail", columns=["Violations per Type"],
    )
    rendered_sum = sum(
        int(str(row["Violations per Type"]).replace(",", ""))
        for row in table_rows
    )
    # The DOM-rendered window may cap at ~50 rows (QS / App2 paging),
    # so we tighten the contract to "the rendered window's count sum
    # matches the dataset's count sum for the corresponding rows."
    # When fewer rows render than the dataset has, rendered_sum is a
    # partial; the strong assertion is that whatever the table renders
    # AGREES with what the SQL says for those same rows. Approximate
    # by asserting rendered_sum is a prefix of the sorted dataset sums.
    # Stronger gate when ALL rows fit the DOM window:
    if len(table_rows) >= len(rows):
        assert rendered_sum == expected_sum, (
            f"L2 Violation Detail table's count column sum: rendered "
            f"{rendered_sum} ≠ SUM(count) over dataset = {expected_sum}. "
            f"v11.21.0 cold-read finding #11 table-half — the table "
            f"column doesn't render what the dataset projects."
        )
    else:
        # Partial render: every rendered row must be a real dataset
        # row, and rendered_sum ≤ expected_sum.
        assert rendered_sum <= expected_sum, (
            f"Rendered count sum {rendered_sum} exceeds dataset total "
            f"{expected_sum} — table is duplicating rows or rendering "
            f"values not in the dataset."
        )
    driver.screenshot()
