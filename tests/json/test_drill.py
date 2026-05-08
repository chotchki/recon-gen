"""K.2 drill-helper validators (``common/drill.py``).

Five runtime-raised guards covered here, all per the L.1.18 audit
inventory:

- ``field_source`` — ``TypeError`` when the source column has no
  ``ColumnShape`` tag in its DatasetContract (drill-ineligible).
- ``set_drill_parameters`` — ``ValueError`` on empty writes (a no-op
  SetParametersOperation is almost always a wiring bug).
- ``set_drill_parameters`` — ``ValueError`` on duplicate parameter
  writes (each param can be written at most once per action).
- ``set_drill_parameters`` — ``TypeError`` on shape mismatch (the K.2
  bug class — DATETIME source into a SINGLE_VALUED string param).
- ``set_drill_parameters`` — ``TypeError`` on unsupported value type
  (defensive Union exhaustiveness for the ``DrillWriteValue`` Union).

The corresponding tree-side ``Drill`` wrapper inherits these
guarantees through ``cross_sheet_drill``; the tree tests in
``test_tree.py::TestDrillEmit`` exercise the integration. The tests
here pin the underlying ``common/drill.py`` API directly so a refactor
that removes a guard is loud at the helper itself.
"""

from __future__ import annotations

import pytest

from quicksight_gen.common.dataset_contract import (
    ColumnShape,
    ColumnSpec,
    DatasetContract,
    register_contract,
)
from quicksight_gen.common.drill import (
    DrillParam,
    DrillResetSentinel,
    DrillSourceField,
    field_source,
    set_drill_parameters,
)
from quicksight_gen.common.ids import ParameterName


# ---------------------------------------------------------------------------
# field_source — must reject columns the contract didn't tag with a shape.
# ---------------------------------------------------------------------------

class TestFieldSourceShapeRequired:
    def test_unshaped_column_raises_type_error(self):
        ds = "test-drill-unshaped"
        register_contract(ds, DatasetContract(columns=[
            ColumnSpec(name="amount", type="DECIMAL"),
            # shape= intentionally omitted — column is not drill-eligible
        ]))
        with pytest.raises(TypeError, match="not drill-eligible"):
            field_source(field_id="f-1", dataset_id=ds, column_name="amount")

    def test_shaped_column_resolves(self):
        ds = "test-drill-shaped"
        register_contract(ds, DatasetContract(columns=[
            ColumnSpec(name="account_id", type="STRING", shape=ColumnShape.ACCOUNT_ID),
        ]))
        src = field_source(field_id="f-1", dataset_id=ds, column_name="account_id")
        assert isinstance(src, DrillSourceField)
        assert src.shape is ColumnShape.ACCOUNT_ID


# ---------------------------------------------------------------------------
# set_drill_parameters — empty writes / duplicate writes / shape mismatch.
# ---------------------------------------------------------------------------

class TestSetDrillParametersValidators:
    def _param(self, name: str = "pX", shape: ColumnShape = ColumnShape.ACCOUNT_ID) -> DrillParam:
        return DrillParam(name=ParameterName(name), shape=shape)

    def test_empty_writes_rejected(self):
        with pytest.raises(ValueError, match="at least one write"):
            set_drill_parameters()

    def test_duplicate_parameter_writes_rejected(self):
        p = self._param("pAccount", ColumnShape.ACCOUNT_ID)
        src1 = DrillSourceField(field_id="f-1", shape=ColumnShape.ACCOUNT_ID)
        src2 = DrillSourceField(field_id="f-2", shape=ColumnShape.ACCOUNT_ID)
        with pytest.raises(ValueError, match="Duplicate drill parameter"):
            set_drill_parameters((p, src1), (p, src2))

    def test_shape_mismatch_rejected(self):
        """The K.2 bug class — DATETIME_DAY source into an ACCOUNT_ID
        param. Both look like 'STRING' to AWS but the textual encodings
        don't line up; the destination filter silently produces zero
        rows. The typed wrapper refuses this wiring at the call site."""
        p = self._param("pAccount", ColumnShape.ACCOUNT_ID)
        wrong = DrillSourceField(field_id="f-date", shape=ColumnShape.DATETIME_DAY)
        with pytest.raises(TypeError, match="Drill source shape mismatch"):
            set_drill_parameters((p, wrong))

    def test_subtype_widens_to_account_id(self):
        """ACCOUNT_ID accepts SUBLEDGER_ACCOUNT_ID and LEDGER_ACCOUNT_ID
        per ColumnShape.can_assign_to — sub/ledger IDs are valid account
        IDs in the lookup. Confirms the widening still works (no
        accidental over-tightening)."""
        p = self._param("pAccount", ColumnShape.ACCOUNT_ID)
        sub = DrillSourceField(
            field_id="f-sub", shape=ColumnShape.SUBLEDGER_ACCOUNT_ID,
        )
        op = set_drill_parameters((p, sub))
        assert op.ParameterValueConfigurations[0]["DestinationParameterName"] == "pAccount"

    def test_reset_sentinel_always_compatible(self):
        """DrillResetSentinel writes a literal sentinel string regardless
        of param shape — passes shape-check by definition."""
        p = self._param("pAnything", ColumnShape.ACCOUNT_ID)
        op = set_drill_parameters((p, DrillResetSentinel()))
        cfg = op.ParameterValueConfigurations[0]
        assert cfg["DestinationParameterName"] == "pAnything"
        assert "CustomValuesConfiguration" in cfg["Value"]

    def test_unsupported_value_type_rejected(self):
        """Defensive Union exhaustiveness — a write value that's neither
        DrillSourceField nor DrillResetSentinel raises TypeError.
        Type-system contract: this should be unreachable through
        well-typed code; the runtime guard catches the bypass."""
        p = self._param("pX")
        with pytest.raises(TypeError, match="Unsupported drill write value"):
            set_drill_parameters((p, "bare string"))  # type: ignore[arg-type]: deliberately invalid arg for the negative-path test
