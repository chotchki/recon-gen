"""CR.16-followup unit gate — every dataset's SQL must project every
column declared in its contract.

WHY: CR.3 added ``transfer_id`` to ``L1_EXCEPTIONS_CONTRACT`` AND to
the source matview, but missed the dataset SQL projection in between.
The qs_inner Oracle wrapper expects ``TRANSFER_ID`` from the inner
SELECT — and got ``ORA-00904: invalid identifier`` at query time
instead of at unit-tier. PG / DuckDB versions failed silently
(playwright wait_for_selector timeout on the rendered table).

This test walks every dataset returned by the 4 app builders, parses
each ``BuiltDataset.sql`` for top-level SELECT-list identifiers
(word-boundary regex over the projection section before the first
top-level FROM), and asserts every declared column name appears as a
top-level identifier. Catches the projection-vs-contract drift class.

NOT a SQL parser — assumes builders emit reasonably simple SQL where
contract column names appear verbatim in the SELECT list (either bare
or via ``... AS <col>`` alias). Sub-CTE columns that match a contract
name by accident pass; this is fine because every actual production
case has the column AS the top-level alias too.
"""

from __future__ import annotations

import re

import pytest

from recon_gen.apps.executives.datasets import (
    build_all_datasets as build_exec_datasets,
)
from recon_gen.apps.investigation.datasets import (
    build_all_datasets as build_inv_datasets,
)
from recon_gen.apps.l1_dashboard.datasets import build_all_l1_dashboard_datasets
from recon_gen.apps.l2_flow_tracing.datasets import (
    build_all_l2_flow_tracing_datasets,
)
from recon_gen.common.dataset_contract import BuiltDataset
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from tests._test_helpers import make_test_config


def _strip_comments_and_strings(sql: str) -> str:
    """Strip SQL ``--`` line comments + single-quoted string literals
    so identifier scanning isn't fooled by ``-- transfer_id`` in a
    comment or ``'transfer_id'`` in a sentinel literal."""
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def _all_datasets() -> list[BuiltDataset]:
    cfg = make_test_config(db_table_prefix=DEFAULT_PREFIX)
    inst = default_l2_instance()
    out: list[BuiltDataset] = []
    out.extend(build_all_l1_dashboard_datasets(cfg, inst))
    out.extend(build_all_l2_flow_tracing_datasets(cfg, inst))
    out.extend(build_inv_datasets(cfg, inst))
    out.extend(build_exec_datasets(cfg))
    return out


@pytest.mark.parametrize("ds", _all_datasets(), ids=lambda d: d.DataSetId)
def test_every_custom_sql_projects_every_contract_column(
    ds: BuiltDataset,
) -> None:
    """For each dataset, every contract column name must appear as a
    word in the registered SQL.

    A missing column trips the Oracle qs_inner wrapper at query time
    (``ORA-00904: invalid identifier`` on the wrap's lowercase alias);
    on PG / DuckDB it manifests as a column-not-found error or a
    silent empty render. Either way, the dataset is broken.

    The check intentionally tolerates ``AS <col>`` aliases AND bare
    references — both shapes leave the column name appearing as a
    word in the SQL. Identifiers that only appear inside string
    literals or ``--`` comments are stripped before scanning.
    """
    body = _strip_comments_and_strings(ds.sql)
    missing: list[str] = []
    for col in ds.contract.columns:
        if not re.search(rf"\b{re.escape(col.name)}\b", body):
            missing.append(col.name)
    assert not missing, (
        f"{ds.DataSetId}: declared columns absent from SqlQuery: "
        f"{missing}. The qs_inner Oracle wrapper would emit "
        f'ORA-00904 for each missing column. SQL excerpt: '
        f"{ds.sql[:400]!r}..."
    )
