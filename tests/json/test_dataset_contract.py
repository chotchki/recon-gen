"""Tests for dataset column contracts.

Covers the ``DatasetContract`` primitives + the Oracle case-fold SQL
wrapper. The per-builder "columns match contract" gate retired with the
QS emitter (DW.8.1.b): build_dataset no longer emits an InputColumn list,
and the genuine concern — every contract column appears in the dataset
SQL — is owned by ``test_dataset_sql_contract_projection`` (which sweeps
every builder in the apps' ``build_all_*`` sets).
"""

from __future__ import annotations

from recon_gen.common.config import Config, DbConfig
from tests._test_helpers import make_test_config
from recon_gen.common.dataset_contract import ColumnSpec, DatasetContract
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX


# ---------------------------------------------------------------------------
# Contract basics
# ---------------------------------------------------------------------------

class TestDatasetContract:
    def test_column_names_property(self) -> None:
        c = DatasetContract(columns=[
            ColumnSpec("a", "STRING"),
            ColumnSpec("b", "DECIMAL"),
        ])
        assert c.column_names == ["a", "b"]

    def test_to_input_columns_types(self) -> None:
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
        from recon_gen.common.sql import Dialect
        return make_test_config(
            aws_region="us-east-2",
            db=DbConfig(table_prefix=DEFAULT_PREFIX, dialect=Dialect.ORACLE),
        )

    def _pg_cfg(self) -> Config:
        return make_test_config(
            aws_region="us-east-2",
            db=DbConfig(table_prefix=DEFAULT_PREFIX),
        )

    def _build(self, cfg: Config, sql: str) -> str:
        from recon_gen.common.dataset_contract import build_dataset
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
        return ds.sql

    def test_oracle_wraps_sql_with_lowercase_aliases(self) -> None:
        wrapped = self._build(
            self._oracle_cfg(),
            "SELECT * FROM spec_example_drift",
        )
        assert 'qs_inner."ACCOUNT_ID" AS "account_id"' in wrapped
        assert 'qs_inner."AMOUNT" AS "amount"' in wrapped
        assert "FROM (\nSELECT * FROM spec_example_drift\n) qs_inner" in wrapped

    def test_postgres_passes_sql_through_unchanged(self) -> None:
        sql = "SELECT * FROM spec_example_drift"
        emitted = self._build(self._pg_cfg(), sql)
        assert emitted == sql

    def test_oracle_wrapper_alias_avoids_leading_underscore(self) -> None:
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
