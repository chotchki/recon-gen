"""Unit tests for the L.1.10.6 kitchen-sink app.

These tests confirm the kitchen-sink builds + emits cleanly + actually
contains every typed L.1 primitive at least once. Real e2e (deploy +
TreeValidator browser walk) lands when L.2's tree-to-files bridging
exists; until then these tests guard the "the kitchen-sink is
complete coverage" property at the unit level.

If a future commit adds a new typed primitive (say a new Visual
subtype) and forgets to wire it into the kitchen-sink, the
"every primitive present" assertions here fail loud.
"""

from __future__ import annotations

import pytest

from tests._test_helpers import make_test_config
from tests.e2e._kitchen_app import build_kitchen_app


_CFG = make_test_config()


@pytest.fixture
def kitchen_app():
    return build_kitchen_app(_CFG)


@pytest.fixture
def emitted(kitchen_app):
    """The full models.Analysis instance from the kitchen-sink."""
    return kitchen_app.emit_analysis()


@pytest.fixture
def emitted_dashboard(kitchen_app):
    return kitchen_app.emit_dashboard()


class TestKitchenAppBuilds:
    def test_emit_analysis_succeeds(self, kitchen_app):
        """Validates the App's resolve_auto_ids + dataset / calc-field /
        drill-destination checks all pass on the kitchen-sink. If a
        typed primitive added later breaks one of these, this test
        fires."""
        kitchen_app.emit_analysis()  # doesn't raise

    def test_emit_dashboard_succeeds(self, kitchen_app):
        kitchen_app.emit_dashboard()  # doesn't raise


class TestEveryVisualKindPresent:
    """Walking the emitted Analysis must surface every typed Visual
    subtype at least once."""

    def _visual_kinds(self, emitted) -> set[str]:
        kinds: set[str] = set()
        for sheet in emitted.Definition.Sheets:
            for visual in (sheet.Visuals or []):
                if visual.KPIVisual is not None:
                    kinds.add("kpi")
                if visual.TableVisual is not None:
                    kinds.add("table")
                if visual.BarChartVisual is not None:
                    kinds.add("bar")
                if visual.SankeyDiagramVisual is not None:
                    kinds.add("sankey")
        return kinds

    def test_all_four_visual_kinds_present(self, emitted):
        assert self._visual_kinds(emitted) >= {"kpi", "table", "bar", "sankey"}


class TestEveryFilterKindPresent:
    def _filter_kinds(self, emitted) -> set[str]:
        kinds: set[str] = set()
        for fg in emitted.Definition.FilterGroups or []:
            for f in fg.Filters or []:
                if f.CategoryFilter is not None:
                    kinds.add("category")
                if f.NumericRangeFilter is not None:
                    kinds.add("numeric_range")
                if f.TimeRangeFilter is not None:
                    kinds.add("time_range")
        return kinds

    def test_all_three_filter_kinds_present(self, emitted):
        assert self._filter_kinds(emitted) >= {
            "category", "numeric_range", "time_range",
        }


class TestEveryParameterKindPresent:
    def _param_kinds(self, emitted) -> set[str]:
        kinds: set[str] = set()
        for p in emitted.Definition.ParameterDeclarations or []:
            if p.StringParameterDeclaration is not None:
                kinds.add("string")
            if p.IntegerParameterDeclaration is not None:
                kinds.add("integer")
            if p.DateTimeParameterDeclaration is not None:
                kinds.add("datetime")
        return kinds

    def test_all_three_parameter_kinds_present(self, emitted):
        assert self._param_kinds(emitted) >= {"string", "integer", "datetime"}


class TestEveryControlKindPresent:
    def _control_kinds(self, emitted) -> tuple[set[str], set[str]]:
        param_kinds: set[str] = set()
        filter_kinds: set[str] = set()
        for sheet in emitted.Definition.Sheets:
            for ctrl in (sheet.ParameterControls or []):
                if ctrl.Dropdown is not None:
                    param_kinds.add("dropdown")
                if ctrl.Slider is not None:
                    param_kinds.add("slider")
                if ctrl.DateTimePicker is not None:
                    param_kinds.add("datetime")
            for ctrl in (sheet.FilterControls or []):
                if ctrl.Dropdown is not None:
                    filter_kinds.add("dropdown")
                if ctrl.Slider is not None:
                    filter_kinds.add("slider")
                if ctrl.DateTimePicker is not None:
                    filter_kinds.add("datetime")
                if ctrl.CrossSheet is not None:
                    filter_kinds.add("crosssheet")
        return param_kinds, filter_kinds

    def test_every_parameter_control_kind_present(self, emitted):
        param_kinds, _ = self._control_kinds(emitted)
        assert param_kinds >= {"dropdown", "slider", "datetime"}

    def test_every_filter_control_kind_present(self, emitted):
        _, filter_kinds = self._control_kinds(emitted)
        assert filter_kinds >= {"dropdown", "slider", "datetime", "crosssheet"}


class TestStaticAndLinkedDropdownValues:
    """Both StaticValues and LinkedValues SelectableValues shapes appear."""

    def test_both_selectable_value_kinds_present(self, emitted):
        seen_static = False
        seen_linked = False
        for sheet in emitted.Definition.Sheets:
            for ctrl in (sheet.ParameterControls or []):
                if ctrl.Dropdown is None or ctrl.Dropdown.SelectableValues is None:
                    continue
                sv = ctrl.Dropdown.SelectableValues
                if "Values" in sv:
                    seen_static = True
                if "LinkToDataSetColumn" in sv:
                    seen_linked = True
        assert seen_static, "kitchen-sink missing a StaticValues dropdown"
        assert seen_linked, "kitchen-sink missing a LinkedValues dropdown"


class TestDrillActionsPresent:
    """Every triggerable visual kind that supports Actions has at least
    one drill wired to a non-self destination."""

    def _drills(self, emitted) -> list[tuple[str, str, str]]:
        """(visual_kind, action_name, target_sheet_id) triples."""
        drills: list[tuple[str, str, str]] = []
        for sheet in emitted.Definition.Sheets:
            for visual in (sheet.Visuals or []):
                for kind, vis in (
                    ("table", visual.TableVisual),
                    ("bar", visual.BarChartVisual),
                    ("sankey", visual.SankeyDiagramVisual),
                ):
                    if vis is None or not vis.Actions:
                        continue
                    for a in vis.Actions:
                        nav = a.ActionOperations[0].NavigationOperation
                        target = nav.LocalNavigationConfiguration.TargetSheetId
                        drills.append((kind, a.Name, target))
        return drills

    def test_drill_actions_on_table_bar_sankey(self, emitted):
        kinds = {kind for kind, _, _ in self._drills(emitted)}
        assert kinds >= {"table", "bar", "sankey"}, (
            f"Expected drill actions on table + bar + sankey; got {kinds}"
        )

    def test_drill_targets_resolve_to_real_sheet(self, emitted):
        sheet_ids = {s.SheetId for s in emitted.Definition.Sheets}
        for kind, name, target in self._drills(emitted):
            assert target in sheet_ids, (
                f"Drill {name!r} on {kind} → unknown sheet {target!r}"
            )

    def test_kpi_has_no_actions(self, emitted):
        """KPI doesn't carry Actions in the QuickSight model — typed
        KPI subtype omits the field. If anyone ever adds it, this
        test reminds them to verify the model supports it."""
        for sheet in emitted.Definition.Sheets:
            for visual in (sheet.Visuals or []):
                if visual.KPIVisual is not None:
                    # KPIVisual model should not have Actions attr at all
                    assert not hasattr(visual.KPIVisual, "Actions") or (
                        getattr(visual.KPIVisual, "Actions", None) is None
                    )


class TestCalcFieldsAndDatasets:
    def test_calc_field_present(self, emitted):
        names = [
            c["Name"] for c in (emitted.Definition.CalculatedFields or [])
        ]
        assert "is_above_threshold" in names

    def test_both_datasets_declared(self, emitted):
        ids = {
            d.Identifier
            for d in emitted.Definition.DataSetIdentifierDeclarations
        }
        assert ids >= {"kitchen-main-ds", "kitchen-categories-ds"}

    def test_dependency_graph_includes_both_datasets(self, kitchen_app):
        """LinkedValues + visual + calc field + filter all reference
        datasets — App.dataset_dependencies should surface both."""
        deps = kitchen_app.dataset_dependencies()
        ids = {d.identifier for d in deps}
        assert ids >= {"kitchen-main-ds", "kitchen-categories-ds"}


# ---------------------------------------------------------------------------
# L.1.11 — JSON emission round-trip
# ---------------------------------------------------------------------------

import json


class TestEmissionRoundTrip:
    """Confirms the kitchen-sink Analysis serializes through the
    full to_aws_json + json.dumps + json.loads loop without information
    loss. Catches non-JSON-safe values, missing fields, etc."""

    def test_analysis_to_aws_json_dumps_and_parses(self, emitted):
        j = emitted.to_aws_json()
        serialized = json.dumps(j)
        parsed = json.loads(serialized)
        assert parsed == j  # no information loss through json round-trip

    def test_dashboard_to_aws_json_dumps_and_parses(self, emitted_dashboard):
        j = emitted_dashboard.to_aws_json()
        serialized = json.dumps(j)
        parsed = json.loads(serialized)
        assert parsed == j

    def test_emitted_analysis_has_expected_top_level_fields(self, emitted):
        j = emitted.to_aws_json()
        assert "AwsAccountId" in j
        assert "AnalysisId" in j
        assert "Name" in j
        assert "Definition" in j
        defn = j["Definition"]
        # Every Definition section the kitchen-sink populates is present
        assert defn["DataSetIdentifierDeclarations"]
        assert defn["Sheets"]
        assert defn["FilterGroups"]
        assert defn["CalculatedFields"]
        assert defn["ParameterDeclarations"]


# ---------------------------------------------------------------------------
# L.1.12 — Validation hooks audit
# ---------------------------------------------------------------------------

from quicksight_gen.common.config import Config as _Cfg
from quicksight_gen.common.tree import (
    KPI as _KPI,
    Analysis as _An,
    App as _A,
    Dataset as _DS,
    IntegerParam as _IP,
    Measure as _M,
    NumericRangeFilter as _NRF,
    ParameterSlider as _PS,
    Sheet as _Sh,
)
from quicksight_gen.common.ids import (
    ParameterName as _PN,
    SheetId as _SId,
)


class TestValidationHooksAudit:
    """Exercises every validation rule documented in
    common/tree/__init__.py. If a rule fires for the wrong reason
    (or stops firing), the failure surfaces here."""

    _CFG = _Cfg(
        aws_account_id="111122223333",
        aws_region="us-west-2",
        datasource_arn=(
            "arn:aws:quicksight:us-west-2:111122223333:datasource/test-ds"
        ),
    )
    _DS_X = _DS(identifier="ds-x", arn="arn:aws:quicksight:::dataset/x")

    def _app(self) -> _A:
        app = _A(name="t", cfg=self._CFG)
        app.add_dataset(self._DS_X)
        analysis = app.set_analysis(_An(analysis_id_suffix="t", name="T"))
        return app

    # L.1.21 — `test_place_rejects_duplicate_visual` deleted: the layout
    # DSL constructs + places a visual atomically (`row.add_kpi(width=,
    # ...)`), so there's no way to ask for a second placement. The
    # duplicate-placement bug class is structurally impossible.

    def test_unregistered_parameter_in_control_caught(self):
        app = self._app()
        # Parameter NOT registered on the analysis
        rogue_param = _IP(name=_PN("pRogue"), default=[1])
        sheet = app.analysis.add_sheet(_Sh(
            sheet_id=_SId("s"), name="S", title="S", description="test",
        ))
        sheet.add_parameter_slider(
            parameter=rogue_param,
            title="Rogue",
            minimum_value=0, maximum_value=10, step_size=1,
        )
        with pytest.raises(
            ValueError, match="parameter references that aren't registered",
        ):
            app.emit_analysis()

    def test_unregistered_parameter_in_numeric_filter_caught(self):
        from quicksight_gen.common.tree import FilterGroup as _FG
        app = self._app()
        rogue_param = _IP(name=_PN("pRogue"), default=[1])
        sheet = app.analysis.add_sheet(_Sh(
            sheet_id=_SId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, title="K", values=[],
                subtitle="t",
        )
        from quicksight_gen.common.tree import ParameterBound as _PB
        fg = app.analysis.add_filter_group(_FG(filters=[
            _NRF(
                dataset=self._DS_X, column="amount",
                minimum=_PB(rogue_param),
            ),
        ]))
        sheet.scope(fg, [kpi])
        with pytest.raises(
            ValueError, match="parameter references that aren't registered",
        ):
            app.emit_analysis()

    def test_registered_parameter_passes(self):
        """Sanity check the validation isn't too tight — registered
        parameters work fine."""
        app = self._app()
        sigma = app.analysis.add_parameter(
            _IP(name=_PN("pSigma"), default=[2]),
        )
        sheet = app.analysis.add_sheet(_Sh(
            sheet_id=_SId("s"), name="S", title="S", description="test",
        ))
        sheet.add_parameter_slider(
            parameter=sigma,
            title="σ",
            minimum_value=0, maximum_value=10, step_size=1,
        )
        app.emit_analysis()  # doesn't raise
