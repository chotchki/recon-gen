"""CY.4 — verify the L1 Transactions + Daily Statement Transactions
contracts carry the new ``metadata`` column and the dataset SQL
projects it.

The cross-cutting projection gate (``test_dataset_sql_contract_projection``)
already enforces "every contract column appears in the SQL", so a
missing projection breaks that test. This file adds a focused gate
that the column was named **metadata** specifically — covers the
spec contract (App2's renderer will look up ``metadata`` by that
exact name when wiring the per-row popup).
"""

from __future__ import annotations

import pytest

from recon_gen.apps.l1_dashboard.datasets import (
    DAILY_STATEMENT_TRANSACTIONS_CONTRACT,
    DS_DAILY_STATEMENT_TRANSACTIONS,
    DS_TRANSACTIONS,
    TRANSACTIONS_CONTRACT,
    build_all_l1_dashboard_datasets,
)
from recon_gen.common.dataset_contract import DatasetContract, get_sql
from recon_gen.common.l2 import default_l2_instance
from tests._test_helpers import make_test_config


@pytest.fixture(scope="module")
def built_l1() -> None:
    """Build the L1 datasets once so the SQL / contract registries are
    populated for the per-test ``get_sql`` lookups below."""
    cfg = make_test_config()
    inst = default_l2_instance()
    build_all_l1_dashboard_datasets(cfg, inst)


def _column_names(contract: DatasetContract) -> list[str]:
    return [c.name for c in contract.columns]


class TestTransactionsContractMetadata:
    def test_contract_carries_metadata_text(self) -> None:
        """CY.4 — ``TRANSACTIONS_CONTRACT`` declares a ``metadata``
        column typed ``STRING`` (the AWS-coarse equivalent of ``TEXT``;
        the rest of the codebase uses ``STRING`` for text columns)."""
        names = _column_names(TRANSACTIONS_CONTRACT)
        assert "metadata" in names, (
            f"TRANSACTIONS_CONTRACT must declare 'metadata' for "
            f"Table.metadata_popup=True wiring; got {names}"
        )
        col = next(c for c in TRANSACTIONS_CONTRACT.columns if c.name == "metadata")
        assert col.type == "STRING"

    def test_sql_projects_metadata(self, built_l1: None) -> None:
        """CY.4 — the dataset SQL projects ``metadata`` as a top-level
        column. The cross-cutting projection gate covers the generic
        every-column case; this asserts the column name explicitly
        (insurance against a stealth rename)."""
        del built_l1
        sql = get_sql(DS_TRANSACTIONS)
        # The wrap may rename via ``AS metadata`` (Oracle alias wrap)
        # or leave it bare; both shapes leave the bare identifier in
        # the SQL string.
        assert "metadata" in sql.lower(), (
            f"L1 Transactions dataset SQL must project 'metadata'. "
            f"SQL excerpt: {sql[:400]!r}..."
        )


class TestDailyStatementTransactionsContractMetadata:
    def test_contract_carries_metadata_text(self) -> None:
        """CY.4 — ``DAILY_STATEMENT_TRANSACTIONS_CONTRACT`` declares a
        ``metadata`` column typed ``STRING``."""
        names = _column_names(DAILY_STATEMENT_TRANSACTIONS_CONTRACT)
        assert "metadata" in names, (
            f"DAILY_STATEMENT_TRANSACTIONS_CONTRACT must declare "
            f"'metadata' for Table.metadata_popup=True wiring; got "
            f"{names}"
        )
        col = next(
            c for c in DAILY_STATEMENT_TRANSACTIONS_CONTRACT.columns
            if c.name == "metadata"
        )
        assert col.type == "STRING"

    def test_sql_projects_metadata(self, built_l1: None) -> None:
        """CY.4 — Daily Statement Transactions SQL projects
        ``metadata``."""
        del built_l1
        sql = get_sql(DS_DAILY_STATEMENT_TRANSACTIONS)
        assert "metadata" in sql.lower(), (
            f"Daily Statement Transactions dataset SQL must project "
            f"'metadata'. SQL excerpt: {sql[:400]!r}..."
        )
