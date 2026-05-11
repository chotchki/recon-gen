"""Unit tests for ``TreeValidator``'s pure-Python parts (dispatch
logic, failure collection, tree walking).

The full integration value comes when run against a deployed
dashboard via a ``DashboardDriver`` (the ``test_*_sheet_visuals.py``
e2e tests do that). These unit tests exercise the dispatch +
failure-handling surface with a ``MagicMock`` stand-in for the driver,
without needing a deploy.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests._test_helpers import make_test_config
from quicksight_gen.common.ids import SheetId, VisualId
from quicksight_gen.common.tree import (
    KPI,
    Analysis,
    App,
    BarChart,
    Dataset,
    Dim,
    Measure,
    Sankey,
    Sheet,
    Table,
)
from tests.e2e.tree_validator import TreeValidator, ValidationFailure


_TEST_CFG = make_test_config()


_DS = Dataset(identifier="ds", arn="arn:test:ds")


def _make_app() -> App:
    """Minimal App with a sheet + one of each visual kind."""
    app = App(name="test", cfg=_TEST_CFG)
    app.add_dataset(_DS)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="t", name="T",
    ))
    sheet = analysis.add_sheet(Sheet(
        sheet_id=SheetId("s-1"), name="Sheet One",
        title="Sheet One", description="test",
    ))
    row = sheet.layout.row(height=6)
    row.add_kpi(width=8, title="Total", values=[Measure.sum(_DS, "amount")], subtitle="t")
    row.add_table(
        width=8, title="Detail", group_by=[Dim(_DS, "id")], values=[],
        subtitle="t",
    )
    row.add_bar_chart(
        width=8, title="Distribution", category=[Dim(_DS, "cat")], values=[],
        subtitle="t",
    )
    row.add_sankey(
        width=12,
        title="Flow",
        source=Dim(_DS, "source"),
        target=Dim(_DS, "target"),
        weight=Measure.sum(_DS, "weight"),
        subtitle="t",
    )
    return app


class TestFailureCollection:
    def test_no_failures_doesnt_raise(self):
        v = TreeValidator(_make_app(), driver=MagicMock())
        # No _fail() calls — raise_if_failed is a no-op
        v.raise_if_failed()  # doesn't raise

    def test_single_failure_raises_with_message(self):
        v = TreeValidator(_make_app(), driver=MagicMock())
        v._fail("Sheet 'X'", "missing visual 'Y'")
        with pytest.raises(AssertionError, match="missing visual 'Y'"):
            v.raise_if_failed()

    def test_multiple_failures_all_surface_at_once(self):
        v = TreeValidator(_make_app(), driver=MagicMock())
        v._fail("Sheet 'A'", "first")
        v._fail("Sheet 'B'", "second")
        v._fail("Sheet 'C'", "third")
        with pytest.raises(AssertionError) as exc_info:
            v.raise_if_failed()
        msg = str(exc_info.value)
        assert "first" in msg
        assert "second" in msg
        assert "third" in msg

    def test_validation_failure_dataclass_fields(self):
        f = ValidationFailure(where="Sheet 'X'", message="m")
        assert f.where == "Sheet 'X'"
        assert f.message == "m"


class TestPerKindDispatch:
    def test_kind_specific_method_invoked(self):
        v = TreeValidator(_make_app(), driver=MagicMock())
        sheet = v.app.analysis.sheets[0]
        kpi = sheet.visuals[0]
        # Wrap _validate_kpi with a tracker — confirm dispatch hits it.
        called = []
        v._validate_kpi = lambda s, x: called.append(x)
        v.validate_visual(sheet, kpi)
        assert called == [kpi]

    def test_unknown_kind_falls_through(self):
        """A visual without _AUTO_KIND (e.g. a hypothetical untyped node)
        doesn't crash — dispatch is best-effort."""
        v = TreeValidator(_make_app(), driver=MagicMock())
        sheet = v.app.analysis.sheets[0]
        fake_visual = MagicMock(spec=[])  # no _AUTO_KIND attr
        # Doesn't raise, doesn't add a failure
        v.validate_visual(sheet, fake_visual)
        assert v.failures == []


class TestStructureDispatch:
    """validate_structure walks the App's sheets in registration order
    and exercises every visual's per-kind hook."""

    def test_no_analysis_fails_loud(self):
        app = App(name="empty", cfg=_TEST_CFG)
        v = TreeValidator(app, driver=MagicMock())
        with pytest.raises(AssertionError, match="App has no Analysis"):
            v.validate_structure()
