# pyright: reportOptionalMemberAccess=false, reportOptionalIterable=false
# BF.4/F: kitchen-sink tests build App via API that sets .analysis post-construction.
# The tree's App.analysis stays Optional in the type model; tests rely on the
# post-build invariant.
"""Unit tests for the L.1.10.6 kitchen-sink app.

These tests confirm the kitchen-sink builds + validates cleanly +
actually contains every typed L.1 primitive at least once. Real e2e
(deploy + TreeValidator browser walk) lands when L.2's tree-to-files
bridging exists; until then these tests guard the "the kitchen-sink is
complete coverage" property at the unit level.

If a future commit adds a new typed primitive (say a new Visual
subtype) and forgets to wire it into the kitchen-sink, the
"every primitive present" assertions here fail loud.

Phase DW.1 (QuickSight removal): the tests walk the TREE object graph
(``App`` / ``Analysis`` / typed ``Visual`` / ``Filter`` / ``Control``
nodes) rather than the emitted ``models.Analysis`` QS-API model. The
QuickSight-API emitters are being deleted; the tree IS the source of
truth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from recon_gen.common.tree import (
    BarChart,
    CategoryFilter,
    DateTimeParam,
    Drill,
    FilterCrossSheet,
    FilterDateTimePicker,
    FilterDropdown,
    FilterSlider,
    IntegerParam,
    KPI,
    LinkedValues,
    NumericRangeFilter,
    ParameterDateTimePicker,
    ParameterDropdown,
    ParameterSlider,
    Sankey,
    Sheet,
    StaticValues,
    StringParam,
    Table,
    TimeRangeFilter,
)
from tests._test_helpers import make_test_config
from tests.e2e._kitchen_app import build_kitchen_app

if TYPE_CHECKING:
    from recon_gen.common.tree import Analysis as _TreeAnalysis
    from recon_gen.common.tree import App as _App


_CFG = make_test_config()


@pytest.fixture
def kitchen_app() -> "_App":
    return build_kitchen_app(_CFG)


@pytest.fixture
def emitted(kitchen_app: "_App") -> "_TreeAnalysis":
    """The kitchen-sink Analysis tree node, IDs resolved.

    Phase DW.1: was the emitted ``models.Analysis``; now the tree
    ``Analysis`` walked directly. ``resolve_auto_ids`` fills visual ids
    + back-fills same-sheet drill targets (AUTO → the owning ``Sheet``)
    so the drill-walk tests see resolved ``Sheet`` refs.
    """
    kitchen_app.resolve_auto_ids()
    assert kitchen_app.analysis is not None
    return kitchen_app.analysis


class TestKitchenAppBuilds:
    def test_resolve_and_validation_pass(self, kitchen_app: "_App") -> None:
        """The full tree-validation walk passes on the kitchen-sink. If a
        typed primitive added later breaks ANY invariant — orphan dataset/
        calc-field/parameter ref, unsettable filter param, dangling drill
        target, an App2-parity gap, or a bare-string column — this fires.

        Phase DW.1: was the QS-API emit path (the emitter ran resolve +
        the validators before building QS-API JSON). The QS emitter is
        being deleted; the validation walk now lives on App.validate(),
        which runs the identical renderer-agnostic checks off the tree."""
        kitchen_app.validate()  # full walk; raises on the first violation

    def test_dashboard_registered(self, kitchen_app: "_App") -> None:
        """The kitchen-sink publishes a Dashboard over the SAME validated
        Analysis tree node (an object ref, not a re-emitted copy)."""
        assert kitchen_app.dashboard is not None
        assert kitchen_app.dashboard.analysis is kitchen_app.analysis


class TestEveryVisualKindPresent:
    """Walking the Analysis tree must surface every typed Visual
    subtype at least once."""

    def _visual_kinds(self, emitted: "_TreeAnalysis") -> set[str]:
        kinds: set[str] = set()
        for sheet in emitted.sheets:
            for visual in sheet.visuals:
                if isinstance(visual, KPI):
                    kinds.add("kpi")
                elif isinstance(visual, Table):
                    kinds.add("table")
                elif isinstance(visual, BarChart):
                    kinds.add("bar")
                elif isinstance(visual, Sankey):
                    kinds.add("sankey")
        return kinds

    def test_all_four_visual_kinds_present(self, emitted: "_TreeAnalysis") -> None:
        assert self._visual_kinds(emitted) >= {"kpi", "table", "bar", "sankey"}


class TestEveryFilterKindPresent:
    def _filter_kinds(self, emitted: "_TreeAnalysis") -> set[str]:
        kinds: set[str] = set()
        for fg in emitted.filter_groups:
            for f in fg.filters:
                if isinstance(f, CategoryFilter):
                    kinds.add("category")
                elif isinstance(f, NumericRangeFilter):
                    kinds.add("numeric_range")
                elif isinstance(f, TimeRangeFilter):
                    kinds.add("time_range")
        return kinds

    def test_all_three_filter_kinds_present(self, emitted: "_TreeAnalysis") -> None:
        assert self._filter_kinds(emitted) >= {
            "category", "numeric_range", "time_range",
        }


class TestEveryParameterKindPresent:
    def _param_kinds(self, emitted: "_TreeAnalysis") -> set[str]:
        kinds: set[str] = set()
        for p in emitted.parameters:
            if isinstance(p, StringParam):
                kinds.add("string")
            elif isinstance(p, IntegerParam):
                kinds.add("integer")
            elif isinstance(p, DateTimeParam):
                kinds.add("datetime")
        return kinds

    def test_all_three_parameter_kinds_present(self, emitted: "_TreeAnalysis") -> None:
        assert self._param_kinds(emitted) >= {"string", "integer", "datetime"}


class TestEveryControlKindPresent:
    def _control_kinds(self, emitted: "_TreeAnalysis") -> tuple[set[str], set[str]]:
        param_kinds: set[str] = set()
        filter_kinds: set[str] = set()
        for sheet in emitted.sheets:
            for pctrl in sheet.parameter_controls:
                if isinstance(pctrl, ParameterDropdown):
                    param_kinds.add("dropdown")
                elif isinstance(pctrl, ParameterSlider):
                    param_kinds.add("slider")
                elif isinstance(pctrl, ParameterDateTimePicker):
                    param_kinds.add("datetime")
            for fctrl in sheet.filter_controls:
                if isinstance(fctrl, FilterDropdown):
                    filter_kinds.add("dropdown")
                elif isinstance(fctrl, FilterSlider):
                    filter_kinds.add("slider")
                elif isinstance(fctrl, FilterDateTimePicker):
                    filter_kinds.add("datetime")
                elif isinstance(fctrl, FilterCrossSheet):
                    filter_kinds.add("crosssheet")
        return param_kinds, filter_kinds

    def test_every_parameter_control_kind_present(self, emitted: "_TreeAnalysis") -> None:
        param_kinds, _ = self._control_kinds(emitted)
        assert param_kinds >= {"dropdown", "slider", "datetime"}

    def test_every_filter_control_kind_present(self, emitted: "_TreeAnalysis") -> None:
        _, filter_kinds = self._control_kinds(emitted)
        assert filter_kinds >= {"dropdown", "slider", "datetime", "crosssheet"}


class TestStaticAndLinkedDropdownValues:
    """Both StaticValues and LinkedValues SelectableValues shapes appear
    on the parameter dropdowns."""

    def test_both_selectable_value_kinds_present(self, emitted: "_TreeAnalysis") -> None:
        seen_static = False
        seen_linked = False
        for sheet in emitted.sheets:
            for pctrl in sheet.parameter_controls:
                if not isinstance(pctrl, ParameterDropdown):
                    continue
                if isinstance(pctrl.selectable_values, StaticValues):
                    seen_static = True
                if isinstance(pctrl.selectable_values, LinkedValues):
                    seen_linked = True
        assert seen_static, "kitchen-sink missing a StaticValues dropdown"
        assert seen_linked, "kitchen-sink missing a LinkedValues dropdown"


class TestDrillActionsPresent:
    """Every triggerable visual kind that supports Actions has at least
    one drill wired to a destination Sheet."""

    def _drills(self, emitted: "_TreeAnalysis") -> list[tuple[str, str, str]]:
        """(visual_kind, drill_name, target_sheet_id) triples.

        ``emitted`` has had ``resolve_auto_ids`` run (the fixture does
        it), so same-sheet drills' AUTO targets are back-filled to a
        concrete ``Sheet`` and ``drill.target_sheet`` narrows cleanly."""
        drills: list[tuple[str, str, str]] = []
        kind_map: list[tuple[type, str]] = [
            (Table, "table"), (BarChart, "bar"), (Sankey, "sankey"),
        ]
        for sheet in emitted.sheets:
            for visual in sheet.visuals:
                kind = next(
                    (k for cls, k in kind_map if isinstance(visual, cls)), None,
                )
                if kind is None:
                    continue
                for a in getattr(visual, "actions", []):
                    if not isinstance(a, Drill):
                        continue
                    target = a.target_sheet
                    # After resolve_auto_ids every drill target is a real
                    # Sheet — assert rather than skip, so an unresolved
                    # (AUTO) or foreign target fails loudly instead of
                    # silently vanishing from the drill set.
                    assert isinstance(target, Sheet), (
                        f"Drill {a.name!r} on {kind} has an unresolved "
                        f"target_sheet ({target!r})"
                    )
                    drills.append((kind, a.name, target.sheet_id))
        return drills

    def test_drill_actions_on_table_bar_sankey(self, emitted: "_TreeAnalysis") -> None:
        kinds = {kind for kind, _, _ in self._drills(emitted)}
        assert kinds >= {"table", "bar", "sankey"}, (
            f"Expected drill actions on table + bar + sankey; got {kinds}"
        )

    def test_drill_targets_resolve_to_real_sheet(self, emitted: "_TreeAnalysis") -> None:
        sheet_ids = {s.sheet_id for s in emitted.sheets}
        for kind, name, target in self._drills(emitted):
            assert target in sheet_ids, (
                f"Drill {name!r} on {kind} → unknown sheet {target!r}"
            )

    def test_kpi_has_no_actions(self, emitted: "_TreeAnalysis") -> None:
        """The typed KPI subtype omits the ``actions`` field entirely —
        KPI carries no drill actions in the tree model (mirroring the
        QuickSight KPIVisual, which had no Actions). If anyone ever adds
        it, this test reminds them to verify the renderer supports it."""
        for sheet in emitted.sheets:
            for visual in sheet.visuals:
                if isinstance(visual, KPI):
                    assert not hasattr(visual, "actions")


class TestCalcFieldsAndDatasets:
    def test_calc_field_present(self, emitted: "_TreeAnalysis") -> None:
        names = [c.name for c in emitted.calc_fields]
        assert "is_above_threshold" in names

    def test_both_datasets_declared(self, kitchen_app: "_App") -> None:
        """The datasets actually referenced by the tree — the same set
        the emit declared as ``DataSetIdentifierDeclarations`` (registered
        ∩ referenced) — include both kitchen datasets."""
        deps = kitchen_app.dataset_dependencies()
        declared = {ds.identifier for ds in kitchen_app.datasets if ds in deps}
        assert declared >= {"kitchen-main-ds", "kitchen-categories-ds"}

    def test_dependency_graph_includes_both_datasets(self, kitchen_app: "_App") -> None:
        """LinkedValues + visual + calc field + filter all reference
        datasets — App.dataset_dependencies should surface both."""
        deps = kitchen_app.dataset_dependencies()
        ids = {d.identifier for d in deps}
        assert ids >= {"kitchen-main-ds", "kitchen-categories-ds"}


# ---------------------------------------------------------------------------
# L.1.11 — Definition sections populated (was: JSON emission round-trip)
# ---------------------------------------------------------------------------


class TestDefinitionSectionsPopulated:
    """The kitchen-sink populates every Definition section.

    Phase DW.1 dropped the two QS-API serialization round-trip tests —
    they exercised the QS emitter + None-strip serialization, which is
    being deleted and has no tree equivalent. The structural "every
    section is populated" assertion survives, walked off the tree."""

    def test_kitchen_sink_populates_every_definition_section(
        self, kitchen_app: "_App",
    ) -> None:
        assert kitchen_app.analysis is not None
        # Top-level identity the emit used to stamp (AnalysisId / Name) —
        # reconstructable without emit.
        assert kitchen_app.cfg.aws.prefixed(
            kitchen_app.analysis.analysis_id_suffix,
        )
        assert kitchen_app.analysis.name
        # Every Definition section the kitchen-sink populates is non-empty.
        assert kitchen_app.dataset_dependencies()
        assert kitchen_app.analysis.sheets
        assert kitchen_app.analysis.filter_groups
        assert kitchen_app.analysis.calc_fields
        assert kitchen_app.analysis.parameters


# ---------------------------------------------------------------------------
# L.1.12 — Validation hooks audit
# ---------------------------------------------------------------------------

from recon_gen.common.config import AwsConfig, Config as _Cfg, DbConfig
from recon_gen.common.tree import (
    Analysis as _An,
    App as _A,
    Dataset as _DS,
    IntegerParam as _IP,
    NumericRangeFilter as _NRF,
    Sheet as _Sh,
)
from recon_gen.common.ids import (
    ParameterName as _PN,
    SheetId as _SId,
)


class TestValidationHooksAudit:
    """Exercises every validation rule documented in
    common/tree/__init__.py. If a rule fires for the wrong reason
    (or stops firing), the failure surfaces here.

    Phase DW.1: the rules used to run as a bundle inside the QS-API
    emit path (just before it built QS-API JSON). The emitter is being
    deleted; the validation walks are renderer-agnostic, so these tests
    drive ``_validate_parameter_references`` directly after
    ``resolve_auto_ids`` instead of going through emit."""

    # Z.C — deployment_name + db_table_prefix are required cfg fields.
    _CFG = _Cfg(
        aws=AwsConfig(deployment_name="recon-kitchen"),
        db=DbConfig(table_prefix="kitchen"),
    )
    _DS_X = _DS(identifier="ds-x")

    def _app(self) -> _A:
        app = _A(name="t", cfg=self._CFG)
        app.add_dataset(self._DS_X)
        app.set_analysis(_An(analysis_id_suffix="t", name="T"))
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
        app.resolve_auto_ids()
        with pytest.raises(
            ValueError, match="parameter references that aren't registered",
        ):
            app._validate_parameter_references()

    def test_unregistered_parameter_in_numeric_filter_caught(self):
        from recon_gen.common.tree import FilterGroup as _FG
        app = self._app()
        rogue_param = _IP(name=_PN("pRogue"), default=[1])
        sheet = app.analysis.add_sheet(_Sh(
            sheet_id=_SId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, title="K", values=[],
                subtitle="t",
        )
        from recon_gen.common.tree import ParameterBound as _PB
        fg = app.analysis.add_filter_group(_FG(filters=[
            _NRF(
                dataset=self._DS_X, column="amount",
                minimum=_PB(rogue_param),
            ),
        ]))
        sheet.scope(fg, [kpi])
        app.resolve_auto_ids()
        with pytest.raises(
            ValueError, match="parameter references that aren't registered",
        ):
            app._validate_parameter_references()

    def test_registered_parameter_passes(self):
        """Sanity check the validation isn't too tight — registered
        parameters pass the parameter-reference validation."""
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
        app.resolve_auto_ids()
        app._validate_parameter_references()  # doesn't raise
