"""Unit tests for the L.1 tree primitives in ``common/tree.py``.

L.1.2 coverage: structural types (App / Dashboard / Analysis / Sheet),
GridSlot placement validation, emit() round-trip into models.py.

L.1.3+ coverage joins as each sub-step lands.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._test_helpers import make_test_config
from recon_gen.apps.l2_flow_tracing.datasets import META_VALUE_PLACEHOLDER_SENTINEL
from recon_gen.common.ids import (
    FilterGroupId,
    ParameterName,
    SheetId,
    VisualId,
)
from recon_gen.common.models import DateTimeDefaultValues
from recon_gen.common.tree import (
    AUTO,
    KPI,
    Analysis,
    App,
    CalcField,
    CategoryFilter,
    Dataset,
    DateTimeParam,
    Dim,
    FilterGroup,
    FilterLike,
    IntegerParam,
    Measure,
    auto_id,
    NumericRangeFilter,
    Sheet,
    StringParam,
    Table,
    TimeRangeFilter,
    VisualLike,
)


# Module-level Dataset fixtures used across the L.1.3 / L.1.6 tests.
# Real apps use a per-app dataset registry on the App; tests use these
# stand-ins. The identifiers ("ds", "ds-foo", "ds-anomalies") match
# the strings the pre-L.1.7 tests passed.
_DS = Dataset(identifier="ds", arn="arn:aws:quicksight:::dataset/ds")
_DS_FOO = Dataset(identifier="ds-foo", arn="arn:aws:quicksight:::dataset/ds-foo")
_DS_ANOMALIES = Dataset(
    identifier="ds-anomalies", arn="arn:aws:quicksight:::dataset/ds-anomalies",
)


_TEST_CFG = make_test_config()


# ---------------------------------------------------------------------------
# Sheet
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

class TestAnalysis:
    def test_add_sheet_rejects_duplicate_id(self):
        analysis = Analysis(analysis_id_suffix="test-analysis", name="Test")
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-dup"),
            name="A", title="A", description="test",
        ))
        with pytest.raises(ValueError, match="already on this Analysis"):
            analysis.add_sheet(Sheet(
                sheet_id=SheetId("sheet-dup"),
                name="B", title="B", description="test",
            ))

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class TestApp:
    def _make_app_with_one_sheet(self) -> App:
        app = App(name="test-app", cfg=_TEST_CFG, allow_bare_strings=True)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="test-analysis",
            name="Test Analysis",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-1"),
            name="A", title="A", description="test",
        ))
        sheet.layout.row(height=18).add_kpi(
            width=36, visual_id=VisualId("v-1"), title="One",
                subtitle="t",
        )
        return app

    def test_validate_without_analysis_raises(self):
        app = App(name="test-app", cfg=_TEST_CFG, allow_bare_strings=True)
        with pytest.raises(ValueError, match="set_analysis"):
            app.validate()

    # L.1.21 — analysis-mismatch test deleted: app.create_dashboard()
    # uses the App's already-set analysis by construction, so the
    # mismatch bug class is structurally impossible.

    def test_create_dashboard_returns_registered_dashboard(self):
        app = self._make_app_with_one_sheet()
        ret = app.create_dashboard(
            dashboard_id_suffix="test-dashboard",
            name="Test Dashboard",
        )
        assert ret is app.dashboard

# ---------------------------------------------------------------------------
# L.1.3 — Field-well wrappers (Dim, Measure)
# ---------------------------------------------------------------------------

class TestDim:
    # Q.1.a.7 — currency=True on a numerical Dim emits the same USD
    # CurrencyDisplayFormatConfiguration that Measure.currency uses, so
    # row-level money columns in tables format consistently with KPIs.
    def test_currency_rejects_categorical_dim(self):
        # Money never makes sense on a categorical or date axis — wiring
        # currency=True on a non-numerical Dim is a typo, not an
        # ergonomic shorthand. Fail loud at construction (Dim.__post_init__).
        with pytest.raises(ValueError, match="kind='numerical'"):
            Dim(
                dataset=_DS_FOO, column="account_name", kind="categorical",
                field_id="f-bad", currency=True,
            )


class TestMeasure:
    # Q.1.a — currency=True wires a USD CurrencyDisplayFormatConfiguration
    # onto the underlying NumericalMeasureField. Default (no flag) emits no
    # FormatConfiguration at all so existing measures stay byte-identical.
    def test_currency_rejects_count_aggregations(self):
        """count / distinct_count are categorical (return row counts,
        never money) — currency=True is an author bug, fail loud at
        construction (Measure.__post_init__)."""
        with pytest.raises(ValueError, match="numerical aggregations"):
            Measure(
                dataset=_DS_FOO, column="account_id", kind="count",
                field_id="f-1", currency=True,
            )

    def test_v11_24_1_rejects_numerical_aggregation_over_datetime_column(self):
        """v11.24.1 regression guard: ``Measure.{sum,max,min,average}``
        over a DATETIME-declared column has to fail validation. QS
        rejected ``NumericalMeasureField`` over non-INTEGER/DECIMAL
        columns at analysis-create time; v11.24.0's BO.12 "Latest Leg"
        KPI bound ``postings["posting"].max()`` over a DATETIME column
        and took out the L2 Flow Tracing deploy in CI. Post-DW the check
        lives in ``Measure.validate_column_type()`` (walked by
        ``App.validate()``) instead of the deleted emit path.

        The check leans on a registered ``DatasetContract`` declaring
        the column's type; without that ground truth it stays
        permissive (CalcField refs, missing contracts, missing columns
        all skip). This test uses a fresh test-local identifier so the
        registration doesn't pollute other tests' ``ds-foo`` state."""
        from recon_gen.common.dataset_contract import (
            ColumnSpec, DatasetContract, register_contract,
        )

        ds = Dataset(
            identifier="ds-v11241-guard-test",
            arn="arn:aws:quicksight:::dataset/ds-v11241-guard-test",
        )
        register_contract("ds-v11241-guard-test", DatasetContract(columns=[
            ColumnSpec("posting", "DATETIME"),
            ColumnSpec("amount", "DECIMAL"),
        ]))

        # The numeric column validates cleanly under every numerical kind.
        for kind in ("sum", "max", "min", "average"):
            getattr(Measure, kind)(
                dataset=ds, field_id=f"f-ok-{kind}", column="amount",
            ).validate_column_type()  # doesn't raise

        # The DATETIME column trips the v11.24.1 guard on every numerical
        # kind. Pinning all four because QS rejected the same field-well
        # shape regardless of which aggregation it carries.
        for kind in ("sum", "max", "min", "average"):
            bad = getattr(Measure, kind)(
                dataset=ds, field_id=f"f-bad-{kind}", column="posting",
            )
            with pytest.raises(
                AssertionError,
                match=r"INTEGER or DECIMAL columns, but 'posting' is declared",
            ):
                bad.validate_column_type()

        # distinct_count is categorical — accepts any column type — so the
        # v11.24.1 guard MUST NOT fire even pointed at the DATETIME column.
        Measure(
            dataset=ds, column="posting", kind="distinct_count",
            field_id="f-cat-distinct-count",
        ).validate_column_type()  # doesn't raise (no-op for categorical)


# ---------------------------------------------------------------------------
# L.1.3 — Typed Visual subtypes
# ---------------------------------------------------------------------------

class TestKPIVisual:
    def test_subtitle_required_non_blank(self):
        # b.15.invariant.sheet-description: subtitle is required + non-blank.
        # The constructor catches both omission (TypeError from the
        # dataclass) and a blank string (ValueError from __post_init__).
        with pytest.raises(ValueError, match="subtitle is required"):
            KPI(
                visual_id=VisualId("v-kpi"),
                title="Total",
                subtitle="",
                values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
            )

    def test_satisfies_visual_like_protocol(self):
        kpi = KPI(visual_id=VisualId("v-kpi"), title="Test", subtitle="t")
        assert isinstance(kpi, VisualLike)

    def test_bk_2_value_zero_indicator_rejects_multi_value_kpis(self):
        """BK.2 — the binary zero indicator is only meaningful for a
        single-value KPI. Multi-value KPIs would need per-value icons;
        that's not supported, fail loud at construction."""
        from recon_gen.common.tree import KPIValueZeroIndicator
        with pytest.raises(ValueError, match="single-value KPIs"):
            KPI(
                visual_id=VisualId("v-kpi"),
                title="Multi-value test",  # typing-smell: ignore[no-inline-production-constants]: arbitrary test label; the KPI never emits — construction raises
                subtitle="Healthy = $0",
                values=[
                    Measure.max(_DS_FOO, "drift_a", field_id="f-a"),
                    Measure.max(_DS_FOO, "drift_b", field_id="f-b"),
                ],
                value_zero_indicator=KPIValueZeroIndicator(),
            )

    def test_bk_2_kpi_hex_colors_are_uppercase(self):
        """v11.24.x BK.2 spike deploy probe (2026-05-29) caught QS's
        ``CreateAnalysis`` validation rejecting lowercase hex on the
        ``KPIConditionalFormatting.PrimaryValue.Icon.CustomCondition``
        Color field:

            Member must satisfy regular expression pattern:
            ^#[A-F0-9]{6}$

        Pin the indicator's color constants to the uppercase form so a
        future re-theme that types ``#15803d`` again fails at unit time
        rather than at the deploy gate."""
        import re
        from recon_gen.common.tree.visuals import (
            _KPI_BROKEN_COLOR_HEX,
            _KPI_HEALTHY_COLOR_HEX,
        )
        pattern = re.compile(r"^#[A-F0-9]{6}$")
        assert pattern.match(_KPI_HEALTHY_COLOR_HEX), (
            f"Healthy color {_KPI_HEALTHY_COLOR_HEX!r} must match QS's "
            f"uppercase-hex constraint ^#[A-F0-9]{{6}}$ — lowercase "
            f"hex fails CreateAnalysis."
        )
        assert pattern.match(_KPI_BROKEN_COLOR_HEX), (
            f"Broken color {_KPI_BROKEN_COLOR_HEX!r} must match QS's "
            f"uppercase-hex constraint ^#[A-F0-9]{{6}}$ — lowercase "
            f"hex fails CreateAnalysis."
        )

    def test_bk_9_value_sign_indicator_blocks_mixed_indicators(self):
        """BK.2 + BK.9 — both indicators on the same KPI raises at
        construction. They emit competing ConditionalFormattingOptions
        and QS picks the first match, so mixing produces an undefined
        render. Fail loud rather than silent."""
        from recon_gen.common.tree import (
            KPIValueSignIndicator, KPIValueZeroIndicator,
        )
        with pytest.raises(ValueError, match="pick ONE"):
            KPI(
                visual_id=VisualId("v-kpi"),
                title="Mixed-indicator test",  # typing-smell: ignore[no-inline-production-constants]: arbitrary test label; the KPI never emits — construction raises
                subtitle="should fail at __post_init__",
                values=[Measure.sum(_DS_FOO, "amount", field_id="f-a")],
                value_zero_indicator=KPIValueZeroIndicator(),
                value_sign_indicator=KPIValueSignIndicator(),
            )

    def test_cf_x_value_threshold_banding_rejects_red_le_amber(self):
        """CF.X-infra — red_at must be strictly greater than amber_at.
        Equal or reversed thresholds collapse the amber band to zero
        width and the 3-state contract degenerates to binary."""
        from recon_gen.common.tree import KPIValueThresholdBanding
        with pytest.raises(ValueError, match="strictly greater"):
            KPIValueThresholdBanding(amber_at=5, red_at=5)
        with pytest.raises(ValueError, match="strictly greater"):
            KPIValueThresholdBanding(amber_at=10, red_at=3)

    def test_cf_x_value_threshold_banding_blocks_mixed_with_zero(self):
        """CF.X-infra — threshold_banding + zero_indicator on the same
        KPI raises at construction. 3-way mutex (zero/sign/threshold)
        prevents undefined renders where QS picks the first matching
        ConditionalFormatting option from a stacked set."""
        from recon_gen.common.tree import (
            KPIValueThresholdBanding, KPIValueZeroIndicator,
        )
        with pytest.raises(ValueError, match="pick ONE"):
            KPI(
                visual_id=VisualId("v-kpi"),
                title="Mixed indicator test",  # typing-smell: ignore[no-inline-production-constants]: arbitrary test label; KPI never emits — construction raises
                subtitle="should fail at __post_init__",
                values=[Measure.sum(_DS_FOO, "n", field_id="f-n")],
                value_zero_indicator=KPIValueZeroIndicator(),
                value_threshold_banding=KPIValueThresholdBanding(
                    amber_at=1, red_at=20,
                ),
            )


class TestTableVisual:
    def test_metadata_popup_defaults_false(self):
        """CY.4 — ``metadata_popup`` defaults to False so the existing
        Tables don't suddenly demand a ``metadata`` contract column."""
        table = Table(
            visual_id=VisualId("v-tbl"),
            title="Detail",
            subtitle="t",
            columns=[Dim(dataset=_DS, field_id="f-id", column="id")],
        )
        assert table.metadata_popup is False

    def test_metadata_popup_requires_metadata_in_contract(self):
        """CY.4 — wiring ``metadata_popup=True`` on a Table whose bound
        dataset contract lacks ``"metadata"`` must fail at construction
        (Rust-style invariant — see the memory note).

        Uses a fresh isolated dataset + contract registration so the
        check finds the bound contract but reads no ``metadata`` column.
        """
        from recon_gen.common.dataset_contract import (
            ColumnSpec,
            DatasetContract,
            isolated_dataset_registries,
            register_contract,
        )

        with isolated_dataset_registries():
            ds = Dataset(
                identifier="cy4-no-meta-ds",
                arn="arn:aws:quicksight:::dataset/cy4-no-meta-ds",
            )
            register_contract(
                ds.identifier,
                DatasetContract(columns=[
                    ColumnSpec("id", "STRING"),
                    ColumnSpec("amount", "DECIMAL"),
                ]),
            )
            with pytest.raises(ValueError, match="metadata_popup=True"):
                Table(
                    visual_id=VisualId("v-tbl"),
                    title="Detail",
                    subtitle="t",
                    columns=[Dim(dataset=ds, field_id="f-id", column="id")],
                    metadata_popup=True,
                )

    def test_metadata_popup_accepts_contract_with_metadata(self):
        """CY.4 — when the contract DOES declare ``metadata``,
        construction succeeds. The renderer is then licensed to stamp
        ``data-metadata-popup="1"`` on the visual section."""
        from recon_gen.common.dataset_contract import (
            ColumnSpec,
            DatasetContract,
            isolated_dataset_registries,
            register_contract,
        )

        with isolated_dataset_registries():
            ds = Dataset(
                identifier="cy4-with-meta-ds",
                arn="arn:aws:quicksight:::dataset/cy4-with-meta-ds",
            )
            register_contract(
                ds.identifier,
                DatasetContract(columns=[
                    ColumnSpec("id", "STRING"),
                    ColumnSpec("metadata", "STRING"),
                ]),
            )
            table = Table(
                visual_id=VisualId("v-tbl"),
                title="Detail",
                subtitle="t",
                columns=[Dim(dataset=ds, field_id="f-id", column="id")],
                metadata_popup=True,
            )
            assert table.metadata_popup is True

    # Phase DA — Drillable type-system gate. The gate at
    # ``Table.__post_init__`` walks ``conditional_formatting × actions``
    # and asserts every ``Drillable.on.column`` has at least one Drill
    # writing from it. Catches the operator-flagged bug class at the
    # apps/<app>/app.py wiring site instead of letting QS emit a visual
    # cue with no actual drill behind it.

    def test_drillable_without_matching_drill_raises(self):
        """Phase DA — `Drillable(on=col)` with NO Drill writing from
        that column on the same Table is a type error; the gate at
        construction raises with a diagnostic listing every Drill on
        the Table and the columns each one writes from."""
        from recon_gen.common.tree import Drillable

        ds = Dataset(
            identifier="da-gate-ds",
            arn="arn:aws:quicksight:::dataset/da-gate-ds",
        )
        col_id = Dim(dataset=ds, field_id="f-id", column="id")
        with pytest.raises(
            ValueError, match=r"Drillable\(on='id'\) is in conditional_formatting",
        ):
            Table(
                visual_id=VisualId("v-tbl"),
                title="Detail",
                subtitle="t",
                columns=[col_id],
                # No actions=[] at all — Drillable has no Drill to back it.
                conditional_formatting=[Drillable(on=col_id, color="#000000")],
            )

    def test_drillable_with_drill_on_other_column_raises(self):
        """The gate is column-specific: a Drill on a different column
        than the Drillable.on doesn't satisfy the invariant. Catches the
        off-by-one column mistake (analyst put the visual cue on column
        A but wired the drill to write from column B — the cell B looks
        clickable but A's click does nothing)."""
        from recon_gen.common.tree import Drill, Drillable, DrillParam

        ds = Dataset(
            identifier="da-gate-mismatch-ds",
            arn="arn:aws:quicksight:::dataset/da-gate-mismatch-ds",
        )
        col_a = Dim(dataset=ds, field_id="f-a", column="a")
        col_b = Dim(dataset=ds, field_id="f-b", column="b")
        # Param shape doesn't matter for the gate — the gate only walks
        # column names, not types.
        from recon_gen.common.drill import ColumnShape

        param_b = DrillParam(name=ParameterName("pB"), shape=ColumnShape.ACCOUNT_ID)
        with pytest.raises(ValueError, match=r"Drillable\(on='a'\)"):
            Table(
                visual_id=VisualId("v-tbl"),
                title="Detail",
                subtitle="t",
                columns=[col_a, col_b],
                # Drill writes from `b` but the Drillable is on `a`.
                actions=[Drill(
                    writes=[(param_b, col_b)],
                    name="View B downstream",
                    trigger="DATA_POINT_MENU",
                    action_id="act-1",
                    target_sheet=AUTO,
                )],
                conditional_formatting=[Drillable(on=col_a, color="#000000")],
            )

    def test_drillable_emit_uses_uppercase_hex_for_qs_validation(self):
        """Phase DA — QS `backgroundColor.solid.color` enforces
        `^#[A-F0-9]{6}$` (UPPERCASE hex only). The auto-derived tint
        from `_tint_hex` must round-trip uppercase or QS rejects the
        analysis at create_analysis time with a ValidationException.
        CI 27439942692 caught the original lowercase-hex regression."""
        from recon_gen.common.tree.formatting import _tint_hex

        # Lowercase input should still produce uppercase output — authors
        # may write theme.accent either case; output must satisfy QS's
        # pattern regardless.
        for accent_in in ("#1f77b4", "#1F77B4", "#0b5394"):
            tint = _tint_hex(accent_in)
            assert tint.startswith("#")
            hex_body = tint[1:]
            assert len(hex_body) == 6
            assert hex_body == hex_body.upper(), (
                f"_tint_hex({accent_in!r}) returned {tint!r}; QS pattern "
                f"^#[A-F0-9]{{6}}$ requires uppercase."
            )

    def test_drillable_with_matching_drill_passes(self):
        """Happy path: when at least one Drill on the same Table writes
        from `Drillable.on.column`, construction succeeds. Mirrors the
        shape of every Class C wire + Class D add landed in DA.4."""
        from recon_gen.common.tree import Drill, Drillable, DrillParam

        ds = Dataset(
            identifier="da-gate-pass-ds",
            arn="arn:aws:quicksight:::dataset/da-gate-pass-ds",
        )
        col_account = Dim(dataset=ds, field_id="f-acct", column="account_id")
        from recon_gen.common.drill import ColumnShape

        param_account = DrillParam(
            name=ParameterName("pAcct"), shape=ColumnShape.ACCOUNT_ID,
        )
        table = Table(
            visual_id=VisualId("v-tbl"),
            title="Detail",
            subtitle="t",
            columns=[col_account],
            actions=[Drill(
                writes=[(param_account, col_account)],
                name="View Daily Statement",
                trigger="DATA_POINT_MENU",
                action_id="act-1",
                target_sheet=AUTO,
            )],
            conditional_formatting=[Drillable(on=col_account, color="#000000")],
        )
        assert table.conditional_formatting is not None
        assert len(table.conditional_formatting) == 1


class TestSheetAcceptsTypedVisuals:
    """Layout DSL constructors return typed visual subtypes (KPI / Table
    / BarChart / Sankey) — generic `add_*` preserves the concrete
    subtype, the visual is registered + placed atomically."""

    def test_layout_add_kpi_returns_concrete_subtype(self):
        """Layout DSL preserves the caller's concrete subtype — the
        returned ref still types as KPI, not the widened VisualLike
        Protocol."""
        sheet = Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        )
        kpi: KPI = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-kpi"), title="Test",
                subtitle="t",
        )
        # If the generic worked, kpi is still a KPI — accessing
        # KPI-only attributes shouldn't widen.
        assert kpi.title == "Test"


# ---------------------------------------------------------------------------
# L.1.4 — Parameter declarations
# ---------------------------------------------------------------------------

class TestDateTimeParam:
    def test_accepts_none_time_granularity(self):
        # time_granularity is optional; default is required (M.4.4.10d).
        p = DateTimeParam(
            name=ParameterName("pDate"),
            default=DateTimeDefaultValues(StaticValues=["2030-01-01"]),
        )
        assert p.time_granularity is None


class TestAnalysisAddParameter:
    def test_add_parameter_returns_concrete_subtype(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        sigma: IntegerParam = analysis.add_parameter(IntegerParam(
            name=ParameterName("pSigma"), default=[2],
        ))
        # Concrete subtype preserved through the generic.
        assert sigma.default == [2]

    def test_duplicate_parameter_name_raises(self):
        """Same-name shadow bug class: two declarations sharing a Name
        silently let one win at deploy time. Caught at construction."""
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        analysis.add_parameter(IntegerParam(name=ParameterName("pDup"), default=[1]))
        with pytest.raises(ValueError, match="already declared"):
            analysis.add_parameter(StringParam(name=ParameterName("pDup")))

# ---------------------------------------------------------------------------
# L.1.5 — FilterGroup with object-ref scope + scope-on-same-sheet validation
# ---------------------------------------------------------------------------

def _category_filter(
    filter_id: str, dataset: Dataset, column: str,
) -> CategoryFilter:
    """Test-only typed CategoryFilter constructor — keeps the test
    focus on scope validation, not Filter construction details."""
    return CategoryFilter.with_values(
        filter_id=filter_id,
        dataset=dataset,
        column=column,
        values=["yes"],
    )


class TestFilterGroupScope:
    def _make_sheet_with_visuals(
        self, sheet_id: str, *visual_ids: str,  # typing-smell: ignore[bare-str-id]: sheet_id comes from callers as raw analyst string
    ) -> tuple[Sheet, list[KPI]]:
        sheet = Sheet(
            sheet_id=SheetId(sheet_id),
            name="Test", title="Test", description="test",
        )
        row = sheet.layout.row(height=6)
        visuals: list[KPI] = []
        for vid in visual_ids:
            v = row.add_kpi(width=6, visual_id=VisualId(vid), title=vid, subtitle="t")
            visuals.append(v)
        return sheet, visuals

    def test_scope_visuals_validates_visual_is_on_sheet(self):
        """Wrong-sheet bug: scope_visuals raises if any visual isn't
        registered on the given sheet. Catches the bug class at the
        wiring line."""
        _sheet_a, [v_a] = self._make_sheet_with_visuals("sheet-a", "v-a")
        sheet_b, [_v_b] = self._make_sheet_with_visuals("sheet-b", "v-b")

        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        with pytest.raises(ValueError, match="isn't registered on sheet"):
            # Trying to scope a visual from sheet-a onto sheet-b
            sheet_b.scope(fg, [v_a])

    def test_scope_visuals_with_correct_visuals_succeeds(self):
        sheet, [v1, v2] = self._make_sheet_with_visuals(
            "sheet-test", "v-1", "v-2",
        )
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        ret = sheet.scope(fg, [v1, v2])
        assert ret is fg  # chains
        assert len(fg._scope_entries) == 1

    def test_validate_scope_without_scope_raises(self):
        """A FilterGroup with no scope configured wouldn't apply to
        anything at render time — validate_scope() fails it loud rather
        than letting an empty-scope group through (the check App.validate()
        walks; was a FilterGroup.emit() check pre-DW)."""
        fg = FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        )
        with pytest.raises(ValueError, match="has no scope"):
            fg.validate_scope()

class TestAnalysisAddFilterGroup:
    def test_add_filter_group_returns_ref(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-test"),
            name="Test", title="Test", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-1"), title="Test",
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-test"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [kpi])
        assert fg in analysis.filter_groups

    def test_duplicate_filter_group_id_raises(self):
        analysis = Analysis(analysis_id_suffix="test", name="Test")
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-dup"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        with pytest.raises(ValueError, match="already on this Analysis"):
            analysis.add_filter_group(FilterGroup(
                filter_group_id=FilterGroupId("fg-dup"),
                filters=[_category_filter("f-2", _DS_FOO, "col_b")],
            ))

class TestFilterGroupCompositionWithApp:
    """Cross-check: the wrong-sheet bug class is caught even when
    FilterGroups go through the full App.validate path.

    The L.1.5 check-in moment — the load-bearing object-ref scope
    validation works end-to-end."""

    def test_wrong_sheet_visual_caught_at_scope_call(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="test", name="Test",
        ))
        sheet_a = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-a"),
            name="A", title="A", description="test",
        ))
        v_a = sheet_a.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v-a"), title="A",
                subtitle="t",
        )
        sheet_b = analysis.add_sheet(Sheet(
            sheet_id=SheetId("sheet-b"),
            name="B", title="B", description="test",
        ))
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-cross"),
            filters=[_category_filter("f-1", _DS, "col")],
        ))
        # Try to scope sheet-A's visual onto sheet-B → caught here.
        with pytest.raises(ValueError, match="isn't registered on sheet"):
            sheet_b.scope(fg, [v_a])

# ---------------------------------------------------------------------------
# L.1.6 — Typed Filter wrappers
# ---------------------------------------------------------------------------

class TestTypedCategoryFilter:
    def test_satisfies_filter_like_protocol(self):
        f = CategoryFilter.with_values(
            filter_id="f-1", dataset=_DS, column="col_a", values=["x"],
        )
        assert isinstance(f, FilterLike)

    # L.1.22 — `test_neither_values_nor_parameter_rejected` and
    # `test_both_values_and_parameter_rejected` deleted: the discriminated
    # `binding` field is one of `_ValuesBinding` or `_ParameterBinding`,
    # so neither/both cases are structurally impossible.


class TestTypedNumericRangeFilter:
    # L.1.22 — `test_both_minimum_value_and_parameter_rejected` and
    # `test_both_maximum_value_and_parameter_rejected` deleted: each
    # `Bound` variant carries exactly one piece of data (a value OR a
    # parameter), so both-set cases are structurally impossible.

    def test_satisfies_filter_like_protocol(self):
        f = NumericRangeFilter(
            filter_id="f-1", dataset=_DS, column="amount",
        )
        assert isinstance(f, FilterLike)


class TestTypedTimeRangeFilter:
    def test_satisfies_filter_like_protocol(self):
        f = TimeRangeFilter(
            filter_id="f-1", dataset=_DS, column="posted_at",
        )
        assert isinstance(f, FilterLike)

# ---------------------------------------------------------------------------
# L.1.7 — Dataset tree nodes + dependency graph
# ---------------------------------------------------------------------------

class TestDataset:
    def test_dataset_is_hashable(self):
        """Dataset is the dependency-graph KEY — must be hashable so
        visuals/filters' refs can be collected into set[Dataset]."""
        a = Dataset(identifier="a", arn="arn:a")
        b = Dataset(identifier="b", arn="arn:b")
        s = {a, b, a}
        assert len(s) == 2

    def test_getitem_unknown_column_raises(self):
        """L.1.18 — ``ds["typo"]`` against a contract-registered Dataset
        raises KeyError at the wiring site. The L.1.17 typed-Column path
        depends on this; without it, the typo would survive to the emit
        validator."""
        from recon_gen.common.dataset_contract import (
            ColumnSpec,
            DatasetContract,
            register_contract,
        )
        ds = Dataset(identifier="ds-with-contract", arn="arn:x")
        register_contract(ds.identifier, DatasetContract(columns=[
            ColumnSpec(name="amount", type="DECIMAL"),
        ]))
        # Known column passes through
        assert ds["amount"].name == "amount"
        # Unknown column raises at the wiring site
        with pytest.raises(KeyError, match="typo_column"):
            ds["typo_column"]


class TestAppDatasetRegistry:
    def test_add_dataset_returns_ref(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        ds = app.add_dataset(_DS_FOO)
        assert ds is _DS_FOO
        assert _DS_FOO in app.datasets

    def test_duplicate_dataset_identifier_rejected(self):
        """Same shadow-bug class as duplicate parameters: two registrations
        sharing an identifier silently let one win at deploy."""
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(Dataset(identifier="ds-x", arn="arn:1"))
        with pytest.raises(ValueError, match="already registered"):
            app.add_dataset(Dataset(identifier="ds-x", arn="arn:2"))


class TestAppDatasetDependencies:
    """Walking the tree to extract the precise dataset dependency graph
    is the L.1.7 deployment-side-effect payoff. Selective deploy +
    matview REFRESH ordering both consume this graph."""

    def test_empty_when_no_analysis(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        assert app.dataset_dependencies() == set()

    def test_collects_from_visuals(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        app.add_dataset(_DS_ANOMALIES)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-1"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        row.add_kpi(
            width=12, visual_id=VisualId("v-foo"), title="From foo",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        row.add_kpi(
            width=12, visual_id=VisualId("v-anom"), title="From anomalies",
            values=[Measure.count(_DS_ANOMALIES, "id")],
                subtitle="t",
        )
        deps = app.dataset_dependencies()
        assert deps == {_DS_FOO, _DS_ANOMALIES}

    def test_collects_from_filter_groups(self):
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-1"), name="S", title="S", description="test",
        ))
        # No values; visual itself doesn't reference _DS_FOO.
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-1"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [kpi])
        # Dependency comes via the filter group, not the visual.
        assert app.dataset_dependencies() == {_DS_FOO}

    def test_validate_rejects_unregistered_dataset(self):
        """The load-bearing validation: if a visual or filter references
        a Dataset that wasn't registered on the App, validate() raises
        with the offending identifier(s)."""
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        # _DS_FOO is NOT registered on this app
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        with pytest.raises(ValueError, match="references unregistered datasets"):
            app.validate()

class TestValidateFilterParamSettability:
    """Catches the v8.3.3 Daily Statement bug class at App.emit time:
    CategoryFilter / TimeEqualityFilter / NumericRangeFilter that bind
    a parameter the analyst can't set (no control + no default)."""

    def _scaffold(self, *, with_default: bool, with_control: bool) -> App:
        from recon_gen.common.tree import (
            FilterGroup, StaticValues,
        )
        app = App(name="test", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        param = analysis.add_parameter(StringParam(
            name=ParameterName("pAccount"),
            default=["acct-1"] if with_default else [],
        ))
        if with_control:
            sheet.add_parameter_dropdown(
                parameter=param, title="Account",
                selectable_values=StaticValues(values=["acct-1", "acct-2"]),
            )
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-cat"),
            cross_dataset="SINGLE_DATASET",
            filters=[CategoryFilter.with_parameter(
                filter_id="filter-cat",
                dataset=_DS_FOO,
                column=_DS_FOO["account"],
                parameter=param,
            )],
        )).scope_sheet(sheet)
        return app

    def test_param_with_no_control_and_no_default_raises(self):
        """The v8.3.3 footgun: analyst sees an empty dropdown (or no
        widget at all), parameter stays unset, every visual on the
        sheet renders blank."""
        app = self._scaffold(with_default=False, with_control=False)
        with pytest.raises(ValueError, match="unsettable"):
            app.validate()

    def test_param_with_control_passes(self):
        """A dropdown — even just a static-values one — gives the
        analyst a way to pick. Settable, no error."""
        app = self._scaffold(with_default=False, with_control=True)
        app.validate()  # doesn't raise

    def test_param_with_default_only_passes(self):
        """Drill-target params (set programmatically by Drill writes,
        no UI control) lean on a default sentinel — that's a valid
        settable shape."""
        app = self._scaffold(with_default=True, with_control=False)
        app.validate()  # doesn't raise

# ---------------------------------------------------------------------------
# L.1.8 — CalcField tree nodes
# ---------------------------------------------------------------------------

# Module-level CalcField fixture for the L.1.8 tests. Real apps construct
# CalcField nodes inside per-app builders; tests use a stand-in.
_CALC_IS_ANCHOR = None  # populated lazily inside tests so it can carry _DS_FOO


def _make_is_anchor() -> CalcField:
    """A test-only calc field on _DS_FOO."""
    return CalcField(
        name="is_anchor_edge",
        dataset=_DS_FOO,
        expression="ifelse({source} = ${pAnchor}, 'yes', 'no')",
    )


class TestCalcField:
    def test_calc_field_is_hashable(self):
        a = CalcField(name="a", dataset=_DS_FOO, expression="1")
        b = CalcField(name="b", dataset=_DS_FOO, expression="2")
        assert len({a, b, a}) == 2


class TestAnalysisAddCalcField:
    def test_add_calc_field_returns_ref(self):
        analysis = Analysis(analysis_id_suffix="t", name="T")
        cf = analysis.add_calc_field(CalcField(
            name="my_calc", dataset=_DS_FOO, expression="1 + 1",
        ))
        assert cf in analysis.calc_fields

    def test_duplicate_name_rejected(self):
        analysis = Analysis(analysis_id_suffix="t", name="T")
        analysis.add_calc_field(CalcField(
            name="dup", dataset=_DS_FOO, expression="1",
        ))
        with pytest.raises(ValueError, match="already on this Analysis"):
            analysis.add_calc_field(CalcField(
                name="dup", dataset=_DS_FOO, expression="2",
            ))

class TestAppCalcFieldDependencies:
    """The L.1.8 dependency-graph extension: walk the tree to find
    every CalcField a visual or filter actually references."""

    def test_calc_fields_referenced_includes_visual_refs(self):
        cf = _make_is_anchor()
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        analysis.add_calc_field(cf)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.count(_DS_FOO, cf)],
                subtitle="t",
        )
        # Tree walks the visual and finds the calc field ref.
        assert analysis.calc_fields_referenced() == {cf}

    def test_calc_fields_referenced_includes_filter_refs(self):
        cf = _make_is_anchor()
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        analysis.add_calc_field(cf)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
                subtitle="t",
        )
        analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg"),
            filters=[CategoryFilter.with_values(
                filter_id="f-1", dataset=_DS_FOO, column=cf, values=["yes"],
            )],
        ))
        sheet.scope(analysis.filter_groups[-1], [kpi])
        assert analysis.calc_fields_referenced() == {cf}

    def test_validate_rejects_unregistered_calc_field(self):
        """The wrong-calc-field bug class — passing a CalcField that
        isn't registered on the Analysis. validate() raises with
        the offending name."""
        cf = _make_is_anchor()  # NOT registered on the analysis
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        # Skip add_calc_field — the calc field is referenced but unregistered.
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.count(_DS_FOO, cf)],
                subtitle="t",
        )
        with pytest.raises(ValueError, match="references unregistered calc fields"):
            app.validate()

    def test_calc_field_dataset_in_dependency_graph(self):
        """A registered CalcField's Dataset participates in the App's
        dataset_dependencies — declaring a calc field on dataset D
        establishes D as a dep even when no visual touches D's columns."""
        cf = CalcField(
            name="standalone_calc", dataset=_DS_ANOMALIES, expression="1",
        )
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        app.add_dataset(_DS_ANOMALIES)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="t", name="T",
        ))
        analysis.add_calc_field(cf)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        # KPI references _DS_FOO directly; calc field references _DS_ANOMALIES
        sheet.layout.row(height=6).add_kpi(
            width=12, visual_id=VisualId("v"), title="V",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f-val")],
                subtitle="t",
        )
        deps = app.dataset_dependencies()
        # Both datasets show up — _DS_FOO from the visual, _DS_ANOMALIES
        # from the registered calc field.
        assert deps == {_DS_FOO, _DS_ANOMALIES}


# ---------------------------------------------------------------------------
# L.1.8.5 — Auto-IDs for internal IDs + tree-query helpers
# ---------------------------------------------------------------------------

class TestAutoVisualIds:
    """L.1.8.5: typed Visual subtypes get auto-IDs from their position in
    the tree when the user doesn't pass one explicitly."""

    def test_kpi_without_visual_id_gets_auto_id_at_emit(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-test"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Flagged",
            values=[Measure.count(_DS_FOO, "id")],
                subtitle="t",
        )
        # visual_id defaults to AUTO until App.resolve_auto_ids() fills it
        assert kpi.visual_id is AUTO
        app.validate()
        # Now resolved
        assert kpi.visual_id == auto_id("v-kpi-s0-0")

    def test_explicit_visual_id_preserved(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            visual_id=VisualId("v-special"),
            title="Special",
                subtitle="t",
        )
        app.validate()
        assert kpi.visual_id == "v-special"

    def test_mixed_explicit_and_auto(self):
        """Explicit IDs interleave with auto-IDs without conflict —
        auto-IDs use the position-indexed scheme, explicit ones pass
        through unchanged."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        kpi_a = row.add_kpi(width=12, title="A", subtitle="t")
        kpi_b = row.add_kpi(width=12, title="B", visual_id=VisualId("v-special"), subtitle="t")
        kpi_c = row.add_kpi(width=12, title="C", subtitle="t")
        app.validate()
        assert kpi_a.visual_id == auto_id("v-kpi-s0-0")
        assert kpi_b.visual_id == "v-special"
        assert kpi_c.visual_id == auto_id("v-kpi-s0-2")

    def test_kind_prefix_distinguishes_visual_types(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        kpi = row.add_kpi(width=8, title="K", subtitle="t")
        table = row.add_table(width=8, title="T", group_by=[], values=[], subtitle="t")
        bar = row.add_bar_chart(width=8, title="B", category=[], values=[], subtitle="t")
        sankey = row.add_sankey(
            width=8, title="S",
            source=Dim(_DS_FOO, "src"),
            target=Dim(_DS_FOO, "tgt"),
            weight=Measure.sum(_DS_FOO, "amount"),
                subtitle="t",
        )
        app.validate()
        assert kpi.visual_id == auto_id("v-kpi-s0-0")
        assert table.visual_id == auto_id("v-table-s0-1")
        assert bar.visual_id == auto_id("v-bar-s0-2")
        assert sankey.visual_id == auto_id("v-sankey-s0-3")

    def test_visual_id_is_sheet_scoped(self):
        """First visual on first sheet vs first visual on second sheet —
        position resets per sheet, scope encoded in the ID prefix."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet_a = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-a"), name="A", title="A", description="test",
        ))
        sheet_b = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-b"), name="B", title="B", description="test",
        ))
        kpi_a = sheet_a.layout.row(height=6).add_kpi(width=12, title="A0", subtitle="t")
        kpi_b = sheet_b.layout.row(height=6).add_kpi(width=12, title="B0", subtitle="t")
        app.validate()
        assert kpi_a.visual_id == auto_id("v-kpi-s0-0")
        assert kpi_b.visual_id == auto_id("v-kpi-s1-0")


class TestAutoFilterGroupIds:
    def test_filter_group_without_id_gets_auto_id(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(
            filters=[_category_filter("f-1", _DS_FOO, "col")],
        ))
        sheet.scope(fg, [kpi])
        assert fg.filter_group_id is AUTO
        app.validate()
        assert fg.filter_group_id == auto_id("fg-0")

    def test_explicit_filter_group_id_preserved(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-special"),
            filters=[_category_filter("f-1", _DS_FOO, "col")],
        ))
        sheet.scope(fg, [kpi])
        app.validate()
        assert fg.filter_group_id == "fg-special"


class TestTreeQueryHelpers:
    """The L.1.8.5 introspection API. e2e tests + the dependency-graph
    walk consume these instead of importing per-app constants."""

    def _make_app(self) -> tuple[App, Sheet, KPI, Table, FilterGroup]:
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-anet"),
            name="Account Network", title="Account Network", description="test",
        ))
        row = sheet.layout.row(height=6)
        kpi = row.add_kpi(width=12, title="Flagged Pair-Windows", subtitle="t")
        table = row.add_table(
            width=24, title="Account Network — Touching Edges",
            group_by=[], values=[],
                subtitle="t",
        )
        fg = analysis.add_filter_group(FilterGroup(
            filter_group_id=FilterGroupId("fg-anchor"),
            filters=[_category_filter("f-1", _DS_FOO, "col_a")],
        ))
        sheet.scope(fg, [table])
        return app, sheet, kpi, table, fg

    def test_app_find_sheet_by_name(self):
        app, sheet, _, _, _ = self._make_app()
        found = app.find_sheet(name="Account Network")
        assert found is sheet

    def test_app_find_sheet_by_sheet_id(self):
        app, sheet, _, _, _ = self._make_app()
        found = app.find_sheet(sheet_id=SheetId("s-anet"))
        assert found is sheet

    def test_app_find_sheet_no_match_raises(self):
        app, _, _, _, _ = self._make_app()
        with pytest.raises(ValueError, match="No sheet"):
            app.find_sheet(name="Nonexistent")

    def test_sheet_find_visual_by_title(self):
        _app, sheet, kpi, _, _ = self._make_app()
        found = sheet.find_visual(title="Flagged Pair-Windows")
        assert found is kpi

    def test_sheet_find_visual_by_partial_title(self):
        _app, sheet, _, table, _ = self._make_app()
        found = sheet.find_visual(title_contains="Touching Edges")
        assert found is table

    def test_sheet_find_visual_no_match_raises(self):
        _app, sheet, _, _, _ = self._make_app()
        with pytest.raises(ValueError, match="No visual"):
            sheet.find_visual(title="Doesn't Exist")

    def test_sheet_find_visual_multiple_matches_raises(self):
        """When the criteria are ambiguous, the helper raises rather
        than returning a non-deterministic match."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        row = sheet.layout.row(height=6)
        row.add_kpi(width=12, title="Same Title", subtitle="t")
        row.add_kpi(width=12, title="Same Title", subtitle="t")
        with pytest.raises(ValueError, match="Multiple visuals"):
            sheet.find_visual(title="Same Title")

    def test_analysis_find_filter_group_by_id(self):
        app, _, _, _, fg = self._make_app()
        assert app.analysis is not None
        found = app.analysis.find_filter_group(filter_group_id=FilterGroupId("fg-anchor"))
        assert found is fg

    def test_analysis_find_calc_field_by_name(self):
        cf = CalcField(name="my_calc", dataset=_DS_FOO, expression="1")
        analysis = Analysis(analysis_id_suffix="t", name="T")
        analysis.add_calc_field(cf)
        found = analysis.find_calc_field(name="my_calc")
        assert found is cf

    def test_analysis_find_filter_group_no_match_raises(self):
        """L.1.18 — finder raises rather than returning None on a miss."""
        app, _, _, _, _ = self._make_app()
        assert app.analysis is not None
        with pytest.raises(ValueError, match="No filter group"):
            app.analysis.find_filter_group(
                filter_group_id=FilterGroupId("fg-nonexistent"),
            )

    def test_analysis_find_calc_field_no_match_raises(self):
        """L.1.18 — finder raises rather than returning None on a miss."""
        analysis = Analysis(analysis_id_suffix="t", name="T")
        with pytest.raises(ValueError, match="No calc field"):
            analysis.find_calc_field(name="nonexistent")

    def test_analysis_find_sheet_multi_match_raises(self):
        """L.1.18 — when both name= and sheet_id= match a different sheet,
        the helper detects the ambiguous result rather than picking one."""
        analysis = Analysis(analysis_id_suffix="t", name="T")
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-1"), name="A", title="A", description="test",
        ))
        analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-2"), name="B", title="B", description="test",
        ))
        # name="A" matches s-1; sheet_id="s-2" matches s-2 → 2 matches.
        with pytest.raises(ValueError, match="Multiple sheets"):
            analysis.find_sheet(name="A", sheet_id=SheetId("s-2"))


# ---------------------------------------------------------------------------
# L.1.9 — Typed FilterControl + ParameterControl variants
# ---------------------------------------------------------------------------

from recon_gen.common.tree import (
    LinkedValues,
    ParameterTextField,
)


class TestLinkedValues:
    """L.1.22 — factory methods normalize the two construction forms.
    The standalone constructor takes the canonical (dataset, column_name)
    pair; the dual-form `__post_init__` validation has been replaced by
    factory methods that produce the canonical pair."""

    def test_from_column_derives_dataset_from_column(self):
        from recon_gen.common.dataset_contract import (
            ColumnSpec,
            DatasetContract,
            register_contract,
        )
        ds_a = Dataset(identifier="lv-fromcol-a", arn="arn:a")
        register_contract(ds_a.identifier, DatasetContract(columns=[
            ColumnSpec(name="col", type="STRING"),
        ]))
        lv = LinkedValues.from_column(ds_a["col"])
        assert lv.dataset is ds_a
        assert lv.column_name == "col"

    def test_from_string_takes_explicit_dataset(self):
        ds = Dataset(identifier="lv-fromstr", arn="arn:s")
        lv = LinkedValues.from_string(dataset=ds, column_name="bare_col")
        assert lv.dataset is ds
        assert lv.column_name == "bare_col"


class TestParameterDropdown:
    def test_linked_values_dataset_in_dependency_graph(self):
        """A ParameterDropdown's LinkedValues dataset must be registered
        on the App — same enforcement the visuals get."""
        anchor = StringParam(name=ParameterName("pAnchor"))
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        # Don't register _DS_FOO — should raise.
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        analysis.add_parameter(anchor)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.add_parameter_dropdown(parameter=anchor,
            title="Anchor",
            selectable_values=LinkedValues.from_string(dataset=_DS_FOO, column_name="d"),
        )
        with pytest.raises(ValueError, match="references unregistered datasets"):
            app.validate()


class TestParameterTextField:
    def test_rejects_multi_valued_string_param(self):
        """Y.1.m: text-field bound to multi_valued=True is the broken
        L2FT cascade combination — silently reverts the parameter to
        default on non-empty commit. Construction must fail."""
        p = StringParam(
            name=ParameterName("pValues"),
            default=[META_VALUE_PLACEHOLDER_SENTINEL],
            multi_valued=True,
        )
        with pytest.raises(ValueError, match="multi-valued parameter"):
            ParameterTextField(
                parameter=p, title="Value", control_id="pc-test",
            )


class TestFilterDropdown:
    def test_emits_with_auto_filter_id(self):
        """Filter wrapper's auto-ID resolves to a string — the dropdown
        reads it via the object ref. Tests the L.1.8.5 + L.1.9
        interaction."""
        f = CategoryFilter.with_values(
            dataset=_DS_FOO, column="col", values=["yes"],
        )  # no filter_id — auto
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(filters=[f]))
        sheet.scope(fg, [kpi])
        sheet.add_filter_dropdown(filter=f, title="A")
        app.validate()
        # Auto-IDs resolved
        assert f.filter_id == auto_id("f-category-fg0-0")
        # The dropdown picked it up
        ctrl_emitted = sheet.filter_controls[0].emit()
        assert ctrl_emitted.Dropdown is not None
        assert ctrl_emitted.Dropdown.SourceFilterId == auto_id("f-category-fg0-0")


class TestControlAutoIds:
    """L.1.9 + L.1.8.5: control IDs auto-generate at emit time."""

    def test_parameter_control_auto_id(self):
        sigma = IntegerParam(name=ParameterName("pSigma"), default=[2])
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        analysis.add_parameter(sigma)
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        ctrl = sheet.add_parameter_slider(parameter=sigma, title="σ",
            minimum_value=1, maximum_value=4, step_size=1,
        )
        assert ctrl.control_id is AUTO
        app.validate()
        assert ctrl.control_id == auto_id("pc-slider-s0-0")

    def test_filter_control_auto_id(self):
        f = CategoryFilter.with_values(
            filter_id="filter-x", dataset=_DS_FOO,
            column="col", values=["yes"],
        )
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(width=12, title="K", subtitle="t")
        fg = analysis.add_filter_group(FilterGroup(filters=[f]))
        sheet.scope(fg, [kpi])
        ctrl = sheet.add_filter_dropdown(filter=f, title="X")
        assert ctrl.control_id is AUTO
        app.validate()
        assert ctrl.control_id == auto_id("fc-dropdown-s0-0")


# ---------------------------------------------------------------------------
# L.1.10 — Typed Drill action
# ---------------------------------------------------------------------------

from recon_gen.common.dataset_contract import ColumnShape
from recon_gen.common.tree import Drill as TreeDrill
from recon_gen.common.tree import (
    DrillParam as TreeDrillParam,
)
from recon_gen.common.tree import (
    DrillSourceField as TreeDrillSourceField,
)


class TestDrillEmit:
    def _setup(self) -> tuple[App, Sheet, Sheet, Table]:
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        analysis.add_parameter(StringParam(
            name=ParameterName("pAnchor"),
        ))
        src_sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-source"),
            name="Source", title="Source", description="test",
        ))
        dest_sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-dest"),
            name="Dest", title="Dest", description="test",
        ))
        # Set up a table on the source sheet that has a drill action
        # targeting the dest sheet.
        table = src_sheet.layout.row(height=18).add_table(
            width=36,
            title="Source Table",
            group_by=[Dim(dataset=_DS_FOO, field_id="f-acct", column="display")],
            values=[],
            actions=[TreeDrill(
                target_sheet=dest_sheet,  # OBJECT REF
                writes=[(
                    TreeDrillParam(ParameterName("pAnchor"), ColumnShape.ACCOUNT_DISPLAY),
                    TreeDrillSourceField(field_id="f-acct", shape=ColumnShape.ACCOUNT_DISPLAY),
                )],
                name="Walk to anchor",
                trigger="DATA_POINT_MENU",
            )],
                subtitle="t",
        )
        return app, src_sheet, dest_sheet, table

    def test_drill_action_id_auto_assigned(self):
        app, _, _, table = self._setup()
        action = table.actions[0]
        assert action.action_id is AUTO
        app.validate()
        # auto-IDed: act-s{sheet_idx}-v{visual_idx}-{action_idx}
        assert action.action_id == auto_id("act-s0-v0-0")

    def test_drill_target_sheet_must_be_registered(self):
        """Drill into a sheet that isn't on the analysis raises at
        emit time. Catches the wrong-sheet bug class — the typed
        ref means the Sheet must be a real, registered Sheet object."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        src_sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s-src"),
            name="Source", title="Source", description="test",
        ))
        # An UNregistered sheet — never goes through analysis.add_sheet
        rogue_sheet = Sheet(
            sheet_id=SheetId("s-rogue"),
            name="Rogue", title="Rogue", description="test",
        )
        src_sheet.layout.row(height=18).add_table(
            width=36, title="X", group_by=[], values=[],
            actions=[TreeDrill(
                target_sheet=rogue_sheet,  # not on the analysis!
                writes=[(
                    TreeDrillParam(ParameterName("pX"), ColumnShape.ACCOUNT_ID),
                    TreeDrillSourceField(field_id="f", shape=ColumnShape.ACCOUNT_ID),
                )],
                name="Bad drill",
            )],
                subtitle="t",
        )
        with pytest.raises(ValueError, match="drill actions targeting sheets"):
            app.validate()

    def test_drill_source_calc_field_without_shape_raises(self):
        """L.1.18 — _resolve_drill_source raises TypeError when a Drill
        write reads a CalcField that has no ``shape`` tag. Catches the
        K.2-style "what shape is this column?" bug class for calc fields."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        # CalcField without a shape — drill source can't type-check.
        unshaped = analysis.add_calc_field(CalcField(
            name="counterparty", dataset=_DS_FOO, expression="ifelse(...)",
            # shape= intentionally omitted
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        # Same Dim instance is referenced in both group_by (so the
        # resolver assigns its field_id) and the drill's writes (so the
        # source-shape lookup hits the unshaped CalcField).
        unshaped_dim = Dim(_DS_FOO, unshaped)
        sheet.layout.row(height=18).add_table(
            width=36, title="X",
            group_by=[unshaped_dim],
            values=[],
            actions=[TreeDrill(
                target_sheet=sheet,  # same-sheet
                writes=[(
                    TreeDrillParam(ParameterName("pX"), ColumnShape.ACCOUNT_ID),
                    unshaped_dim,  # uses the shapeless calc
                )],
                name="Drill on calc",
            )],
                subtitle="t",
        )
        with pytest.raises(TypeError, match="has no ``shape`` tag"):
            app.validate()

    def test_explicit_action_id_preserved(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(analysis_id_suffix="t", name="T"))
        src = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        table = src.layout.row(height=18).add_table(
            width=36, title="T", group_by=[], values=[],
            actions=[TreeDrill(
                target_sheet=src,  # same sheet — also valid
                writes=[(
                    TreeDrillParam(ParameterName("pX"), ColumnShape.ACCOUNT_ID),
                    TreeDrillSourceField(field_id="f", shape=ColumnShape.ACCOUNT_ID),
                )],
                name="Drill",
                action_id="my-explicit-id",
            )],
                subtitle="t",
        )
        app.validate()
        assert table.actions[0].action_id == "my-explicit-id"


# ---------------------------------------------------------------------------
# L.1.17 — unvalidated column refs raise unless explicitly allowed
# ---------------------------------------------------------------------------

class TestUnvalidatedColumnsRaiseByDefault:
    """``allow_bare_strings=False`` is the App's default. Two unvalidated
    column-ref forms raise at emit:

    1. **Bare string** — ``Dim(ds, "amount")`` — literal string that
       skips the contract check entirely.
    2. **Unvalidated Column** — ``ds["amount"]`` against a dataset
       with no registered ``DatasetContract``. ``Dataset.__getitem__``
       can't validate when no contract exists, so it returns a Column
       without checking. The walker catches this so the silent-pass
       path becomes a loud raise.

    The validated path: ``ds["amount"]`` against a dataset whose
    contract IS registered. ``Dataset.__getitem__`` raises ``KeyError``
    at the wiring site on typo.

    The escape hatch (``allow_bare_strings=True``) covers test fixtures
    that don't register a ``DatasetContract``.
    """

    def _build_app_with_bare_string_dim(self, **app_kwargs: Any) -> App:
        """Build a minimal App that references a column via a bare str."""
        app = App(name="t", cfg=_TEST_CFG, **app_kwargs)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            subtitle="t",
            values=[Measure.sum(_DS_FOO, "amount")],  # bare string
        )
        return app

    def test_default_app_raises_on_bare_string_column(self):
        app = self._build_app_with_bare_string_dim()  # default allow=False
        with pytest.raises(ValueError, match="unvalidated column refs"):
            app.validate()

    def test_explicit_allow_bypasses_check(self):
        """Tests + datasets without a contract opt into the bare-string
        form via ``allow_bare_strings=True``."""
        app = self._build_app_with_bare_string_dim(allow_bare_strings=True)
        # Should not raise.
        app.validate()

    def test_error_message_lists_offending_column(self):
        app = self._build_app_with_bare_string_dim()
        with pytest.raises(ValueError) as exc_info:
            app.validate()
        message = str(exc_info.value)
        # The bad column name + the visual id appear in the message
        # so the developer can fix at the right call site.
        assert "amount" in message
        # Mentions the typed alternative the user should reach for.
        assert "ds[\"" in message

    def test_bare_string_in_filter_column_also_raises(self):
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f")],
                subtitle="t",
        )
        analysis.add_filter_group(FilterGroup(
            filters=[CategoryFilter.with_values(
                dataset=_DS_FOO,
                column="category",  # bare string
                values=["a"],
            )],
        ))
        sheet.scope(analysis.filter_groups[-1], [kpi])
        # Flip to default-strict for the assertion run.
        app.allow_bare_strings = False
        with pytest.raises(ValueError, match="unvalidated column refs"):
            app.validate()

    def test_unvalidated_column_ref_raises(self):
        """``ds["col"]`` on a dataset without a registered DatasetContract
        is the OTHER escape hatch — Column produced but never validated.
        The walker catches it at emit unless ``allow_bare_strings=True``.
        """
        app = App(name="t", cfg=_TEST_CFG)  # default allow=False
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        # _DS_FOO has no registered DatasetContract. ds["amount"] returns
        # a Column without validation — the walker should catch it.
        sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[_DS_FOO["amount"].sum()],
                subtitle="t",
        )
        with pytest.raises(ValueError) as exc_info:
            app.validate()
        message = str(exc_info.value)
        assert "no registered DatasetContract" in message
        assert _DS_FOO.identifier in message
        assert "amount" in message

    def test_unvalidated_column_in_filter_also_raises(self):
        """The same gap applies to filter columns — ds["col"] on a
        contract-less dataset slips through unless caught here."""
        app = App(name="t", cfg=_TEST_CFG)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        kpi = sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[Measure.sum(_DS_FOO, "amount", field_id="f")],
                subtitle="t",
        )
        # Wrap KPI in a FilterGroup to keep the visuals validation
        # path away from the value-leaf bare string.
        # The relevant unvalidated path is the filter's column.
        analysis.add_filter_group(FilterGroup(
            filters=[CategoryFilter.with_values(
                dataset=_DS_FOO,
                column=_DS_FOO["category"],  # unvalidated Column
                values=["a"],
            )],
        ))
        sheet.scope(analysis.filter_groups[-1], [kpi])
        # Allow bare strings so the KPI's bare-string measure column
        # doesn't trip the check; this isolates the filter's
        # unvalidated Column path. (In real production code both
        # checks should fire — the test isolates them so each path is
        # exercised independently.)
        app.allow_bare_strings = True
        # Filter's Column path bypasses the bare-string check too,
        # though — so we need the strict mode to catch it.
        app.allow_bare_strings = False
        # Drop the bare-string KPI value so only the filter path is bad.
        kpi.values = [_DS_FOO["amount"].sum()]
        with pytest.raises(ValueError) as exc_info:
            app.validate()
        message = str(exc_info.value)
        assert "no registered DatasetContract" in message
        # Both unvalidated columns surface (the KPI value AND the
        # filter column) — the message names both.
        assert "amount" in message
        assert "category" in message

    def test_explicit_allow_bypasses_unvalidated_column_too(self):
        """``allow_bare_strings=True`` covers BOTH unvalidated forms —
        the bare-string path and the contract-less Column path."""
        app = App(name="t", cfg=_TEST_CFG, allow_bare_strings=True)
        app.add_dataset(_DS_FOO)
        analysis = app.set_analysis(Analysis(
            analysis_id_suffix="a", name="A",
        ))
        sheet = analysis.add_sheet(Sheet(
            sheet_id=SheetId("s"), name="S", title="S", description="test",
        ))
        sheet.layout.row(height=6).add_kpi(
            width=12,
            title="Total",
            values=[_DS_FOO["amount"].sum()],
                subtitle="t",
        )
        # Should not raise.
        app.validate()
