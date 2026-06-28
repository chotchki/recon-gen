"""K.2 drill-source-shape validators.

Two homes are pinned here:

- ``field_source`` (``common/drill.py``) — ``TypeError`` when the source
  column has no ``ColumnShape`` tag in its DatasetContract (drill-
  ineligible). Still the lowest-level shape resolver; unchanged by DW.
- ``Drill.resolve_source_shapes`` (``common/tree/actions.py``) — the
  drill-param wiring invariants, walked by ``App.validate()`` on EVERY
  renderer. Raises on empty writes, duplicate parameters, and the K.2
  shape mismatch (a DATETIME source coerced into an ACCOUNT_ID string
  param matched zero rows and read as missing data, not broken wiring).

Pre-DW these guards lived in the QS-emit helper ``set_drill_parameters``
(reached via ``cross_sheet_drill``). DW.8.1.c deleted that emitter, so
the invariant relocated to the validate() walk where App2 picks it up
too — these tests moved with it, constructing a bare ``Drill`` and
calling ``resolve_source_shapes()`` directly. The App-level integration
(auto-id assignment, unregistered-sheet rejection, calc-field-without-
shape) is exercised by ``test_tree.py::TestDrillEmit``.
"""

from __future__ import annotations

import pytest

from recon_gen.common.dataset_contract import (
    ColumnShape,
    ColumnSpec,
    DatasetContract,
    register_contract,
)
from recon_gen.common.drill import (
    DrillParam,
    DrillResetSentinel,
    DrillSourceField,
    field_source,
)
from recon_gen.common.ids import ParameterName
from recon_gen.common.tree.actions import Drill


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
# Drill.resolve_source_shapes — empty writes / duplicate writes / shape
# mismatch / sentinel + subtype widening. Walked by App.validate() on
# every renderer; here we call it on a bare Drill so the guard is loud at
# the unit level without standing up a full App. Explicit DrillSourceField
# writes pass through _resolve_drill_source unchanged, so no auto-id
# resolution is needed for these construction-shape checks.
# ---------------------------------------------------------------------------

class TestDrillResolveSourceShapes:
    def _param(self, name: str = "pX", shape: ColumnShape = ColumnShape.ACCOUNT_ID) -> DrillParam:
        return DrillParam(name=ParameterName(name), shape=shape)

    def test_empty_writes_rejected(self):
        drill = Drill(writes=[], name="empty drill")
        with pytest.raises(ValueError, match="no parameter writes"):
            drill.resolve_source_shapes()

    def test_duplicate_parameter_writes_rejected(self):
        p = self._param("pAccount", ColumnShape.ACCOUNT_ID)
        src1 = DrillSourceField(field_id="f-1", shape=ColumnShape.ACCOUNT_ID)
        src2 = DrillSourceField(field_id="f-2", shape=ColumnShape.ACCOUNT_ID)
        drill = Drill(writes=[(p, src1), (p, src2)], name="dup drill")
        with pytest.raises(ValueError, match="Duplicate drill parameter"):
            drill.resolve_source_shapes()

    def test_shape_mismatch_rejected(self):
        """The K.2 bug class — DATETIME_DAY source into an ACCOUNT_ID
        param. Both look like 'STRING' to AWS but the textual encodings
        don't line up; the destination filter silently produces zero
        rows. The validate() walk refuses this wiring."""
        p = self._param("pAccount", ColumnShape.ACCOUNT_ID)
        wrong = DrillSourceField(field_id="f-date", shape=ColumnShape.DATETIME_DAY)
        drill = Drill(writes=[(p, wrong)], name="mismatch drill")
        with pytest.raises(TypeError, match="Drill source shape mismatch"):
            drill.resolve_source_shapes()

    def test_subtype_widens_to_account_id(self):
        """ACCOUNT_ID accepts SUBLEDGER_ACCOUNT_ID and LEDGER_ACCOUNT_ID
        per ColumnShape.can_assign_to — sub/ledger IDs are valid account
        IDs in the lookup. Confirms the widening still works (no
        accidental over-tightening) — resolve_source_shapes must NOT
        raise."""
        p = self._param("pAccount", ColumnShape.ACCOUNT_ID)
        sub = DrillSourceField(
            field_id="f-sub", shape=ColumnShape.SUBLEDGER_ACCOUNT_ID,
        )
        Drill(writes=[(p, sub)], name="widen drill").resolve_source_shapes()

    def test_reset_sentinel_always_compatible(self):
        """DrillResetSentinel writes a literal sentinel string regardless
        of param shape — skips the shape comparison, so resolve_source_shapes
        must NOT raise."""
        p = self._param("pAnything", ColumnShape.ACCOUNT_ID)
        Drill(
            writes=[(p, DrillResetSentinel())], name="reset drill",
        ).resolve_source_shapes()
