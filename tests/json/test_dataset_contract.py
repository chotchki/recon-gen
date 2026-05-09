"""Tests for dataset column contracts.

Validates that every dataset builder produces a DataSet whose InputColumn
list matches its declared DatasetContract. Trimmed to Investigation-only
after M.4.3 + M.4.4 deleted the AR + PR apps.
"""

from __future__ import annotations

import pytest

from quicksight_gen.common.config import Config
from tests._test_helpers import make_test_config
from quicksight_gen.common.dataset_contract import ColumnSpec, DatasetContract
from quicksight_gen.apps.investigation import datasets as inv_datasets


@pytest.fixture()
def cfg() -> Config:
    # N.3.f: Investigation builders require an L2 instance prefix.
    return make_test_config(
        aws_region="us-east-2",
        l2_instance_prefix="spec_example",
    )


def _extract_column_names(dataset) -> list[str]:
    """Pull the InputColumn names out of a built DataSet."""
    for physical in dataset.PhysicalTableMap.values():
        return [c.Name for c in physical.CustomSql.Columns]
    raise AssertionError("No PhysicalTable found")


# ---------------------------------------------------------------------------
# Investigation contracts
# ---------------------------------------------------------------------------

INV_BUILDERS_AND_CONTRACTS = [
    (inv_datasets.build_recipient_fanout_dataset,
     inv_datasets.RECIPIENT_FANOUT_CONTRACT),
    (inv_datasets.build_volume_anomalies_dataset,
     inv_datasets.VOLUME_ANOMALIES_CONTRACT),
    (inv_datasets.build_money_trail_dataset,
     inv_datasets.MONEY_TRAIL_CONTRACT),
]


class TestInvContracts:
    @pytest.mark.parametrize(
        "builder,contract",
        INV_BUILDERS_AND_CONTRACTS,
        ids=[c.columns[0].name for _, c in INV_BUILDERS_AND_CONTRACTS],
    )
    def test_columns_match_contract(self, cfg, builder, contract):
        ds = builder(cfg)
        actual = _extract_column_names(ds)
        assert actual == contract.column_names


# ---------------------------------------------------------------------------
# Contract basics
# ---------------------------------------------------------------------------

class TestDatasetContract:
    def test_column_names_property(self):
        c = DatasetContract(columns=[
            ColumnSpec("a", "STRING"),
            ColumnSpec("b", "DECIMAL"),
        ])
        assert c.column_names == ["a", "b"]

    def test_to_input_columns_types(self):
        c = DatasetContract(columns=[
            ColumnSpec("x", "INTEGER"),
        ])
        cols = c.to_input_columns()
        assert len(cols) == 1
        assert cols[0].Name == "x"
        assert cols[0].Type == "INTEGER"


# ---------------------------------------------------------------------------
# Q.1.a.8 — Oracle case-fold wrapper
# ---------------------------------------------------------------------------

class TestOracleLowercaseAliasWrapper:
    """Oracle case-folds unquoted identifiers to UPPERCASE; QuickSight
    quotes lowercase column names from the Columns declaration when
    building visual queries. Without a wrapper, every Oracle visual
    fails with ``ORA-00904: "col": invalid identifier``. ``build_dataset``
    transparently wraps the SQL with ``SELECT qs_inner."COL" AS "col" ...``
    on Oracle so QS's quoted-lowercase lookup resolves.
    """

    def _oracle_cfg(self) -> Config:
        from quicksight_gen.common.sql import Dialect
        return make_test_config(
            aws_region="us-east-2",
            l2_instance_prefix="spec_example",
            dialect=Dialect.ORACLE,
        )

    def _pg_cfg(self) -> Config:
        return make_test_config(
            aws_region="us-east-2",
            l2_instance_prefix="spec_example",
        )

    def _build(self, cfg: Config, sql: str) -> str:
        from quicksight_gen.common.dataset_contract import build_dataset
        contract = DatasetContract(columns=[
            ColumnSpec("account_id", "STRING"),
            ColumnSpec("amount", "DECIMAL"),
        ])
        ds = build_dataset(
            cfg, dataset_id="probe-ds", name="Probe",
            table_key="probe", sql=sql,
            contract=contract,
            visual_identifier=f"probe-vi-{id(contract)}",  # unique per call
        )
        for physical in ds.PhysicalTableMap.values():
            return physical.CustomSql.SqlQuery
        raise AssertionError("no PhysicalTable")

    def test_oracle_wraps_sql_with_lowercase_aliases(self):
        wrapped = self._build(
            self._oracle_cfg(),
            "SELECT * FROM spec_example_drift",
        )
        assert 'qs_inner."ACCOUNT_ID" AS "account_id"' in wrapped
        assert 'qs_inner."AMOUNT" AS "amount"' in wrapped
        assert "FROM (\nSELECT * FROM spec_example_drift\n) qs_inner" in wrapped

    def test_postgres_passes_sql_through_unchanged(self):
        sql = "SELECT * FROM spec_example_drift"
        emitted = self._build(self._pg_cfg(), sql)
        assert emitted == sql

    def test_oracle_wrapper_alias_avoids_leading_underscore(self):
        # Oracle ORA-00911: identifiers must start with a letter, so
        # an alias like ``_qs`` would fail at parse time. The chosen
        # ``qs_inner`` alias starts with a letter and is unlikely to
        # collide with user column names. This test pins that
        # invariant so a future "rename to _outer" refactor can't
        # silently break Oracle.
        wrapped = self._build(
            self._oracle_cfg(),
            "SELECT 1 AS account_id, 2 AS amount FROM dual",
        )
        assert " _qs" not in wrapped
        assert "qs_inner" in wrapped
