"""Tests for the Investigation app.

K.4.2 shipped the skeleton (4 sheets, no datasets / filters / visuals).
K.4.3 lands the Recipient Fanout sheet — recipient-fanout dataset +
contract, two filter groups (window date-range + threshold on the
analysis-level distinct-sender calc field), an integer parameter +
slider control, three KPIs, and a recipient-grain ranked table.
K.4.4 lands the Volume Anomalies sheet — pair-grain matview-backed
dataset, two filter groups (window date-range + σ threshold on z_score,
the latter scoped SELECTED_VISUALS to exclude the distribution chart),
an integer σ parameter + slider, and three visuals (KPI + distribution
bar + flagged table).
K.4.5 lands the Money Trail sheet — matview-backed money-trail dataset
sourced from the recursive-CTE walk over ``parent_transfer_id``, three
filter groups (chain-root EQUALS via parameter-bound CategoryFilter,
max-hops on ``depth``, min-hop-amount on ``hop_amount`` — all scoped
ALL_VISUALS), three new parameters + controls (string root, integer
max-hops slider, integer min-amount slider), and a Sankey diagram +
hop-by-hop detail table side-by-side.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from recon_gen.apps.investigation.app import (
    build_analysis,
    build_investigation_dashboard,
)
from recon_gen.apps.investigation.constants import (
    CF_INV_ANETWORK_COUNTERPARTY_DISPLAY,
    CF_INV_ANETWORK_IS_INBOUND_EDGE,
    CF_INV_ANETWORK_IS_OUTBOUND_EDGE,
    CF_INV_FANOUT_DISTINCT_SENDERS,
    DS_INV_ACCOUNT_NETWORK,
    DS_INV_ANETWORK_ACCOUNTS,
    DS_INV_MONEY_TRAIL,
    DS_INV_MONEY_TRAIL_ROOTS,
    DS_INV_RECIPIENT_FANOUT,
    DS_INV_VOLUME_ANOMALIES,
    DS_INV_VOLUME_ANOMALIES_DISTRIBUTION,
    FG_INV_ANETWORK_INBOUND,
    FG_INV_ANETWORK_OUTBOUND,
    FG_INV_ANOMALIES_WINDOW,
    FG_INV_FANOUT_WINDOW,
    FG_INV_MONEY_TRAIL_WINDOW,
    P_INV_ANETWORK_ANCHOR,
    P_INV_ANETWORK_MIN_AMOUNT,
    P_INV_ANOMALIES_SIGMA,
    P_INV_FANOUT_THRESHOLD,
    P_INV_MONEY_TRAIL_MAX_HOPS,
    P_INV_MONEY_TRAIL_MIN_AMOUNT,
    P_INV_MONEY_TRAIL_ROOT,
    SHEET_INV_ACCOUNT_NETWORK,
    SHEET_INV_ANOMALIES,
    SHEET_INV_FANOUT,
    SHEET_INV_GETTING_STARTED,
    SHEET_INV_MONEY_TRAIL,
)
from recon_gen.apps.investigation.datasets import (
    MONEY_TRAIL_CONTRACT,
    MONEY_TRAIL_ROOTS_CONTRACT,
    RECIPIENT_FANOUT_CONTRACT,
    VOLUME_ANOMALIES_CONTRACT,
    build_all_datasets,
)
from recon_gen.cli import main
from recon_gen.common.models import (
    IntegerDatasetParameterDefaultValues,
    SheetVisualScopingConfiguration,
)
from tests._test_helpers import make_test_config


# N.3.f: Investigation is now L2-fed and requires the cfg to carry
# the matching DB-table prefix so its dataset SQL renders the right
# matview names. Z.C — db_table_prefix replaces the prior auto-stamped
# l2_instance_prefix; pin to spec_example since
# ``build_investigation_app`` defaults to the spec_example L2 fixture.
_TEST_CFG = make_test_config(db_table_prefix="spec_example")

# Investigation's ``build_all_datasets`` requires an L2Instance for
# the App Info matview names (P.9f.f — dropped silent fallback). Tests
# pass the spec_example default so prefix derivation matches _TEST_CFG.
from recon_gen.common.l2 import default_l2_instance  # noqa: E402

_TEST_L2 = default_l2_instance()


# L.2.13 — Persona-defaults that the imperative ``filters.py`` carried as
# named constants. Inlined as literals here so the assertions describe the
# persona's intended UX (slider runs 1–20, sigma 1–4, etc.) instead of
# tautologically re-checking that the same constant flows through. The
# tree's ``apps/investigation/app.py`` keeps its own private copies; if
# either side drifts, the assertions in this file fail loudly.
SLIDER_MIN = 1
SLIDER_MAX = 20
DEFAULT_FANOUT_THRESHOLD = 5
SIGMA_SLIDER_MIN = 1
SIGMA_SLIDER_MAX = 4
DEFAULT_ANOMALIES_SIGMA = 2
HOPS_SLIDER_MIN = 1
HOPS_SLIDER_MAX = 10
DEFAULT_MONEY_TRAIL_MAX_HOPS = 5
AMOUNT_SLIDER_MIN = 0
AMOUNT_SLIDER_MAX = 1000
DEFAULT_MONEY_TRAIL_MIN_AMOUNT = 0


def _filter_groups(cfg: Config = _TEST_CFG) -> list:
    """Walk the tree's emitted filter groups (post-resolve)."""
    return build_analysis(cfg).Definition.FilterGroups


def _parameter_declarations(cfg: Config = _TEST_CFG) -> list:
    """Walk the tree's emitted parameter declarations (post-resolve)."""
    return build_analysis(cfg).Definition.ParameterDeclarations


def _sheet_by_id(sheet_id: str, cfg: Config = _TEST_CFG):
    """Find an emitted Sheet by its `SheetId`."""
    return next(
        s for s in build_analysis(cfg).Definition.Sheets
        if s.SheetId == sheet_id
    )


def _filter_controls(sheet_id: str, cfg: Config = _TEST_CFG) -> list:
    return _sheet_by_id(sheet_id, cfg).FilterControls or []


def _parameter_controls(sheet_id: str, cfg: Config = _TEST_CFG) -> list:
    return _sheet_by_id(sheet_id, cfg).ParameterControls or []


def _visual_id_by_title(sheet, title: str) -> str:
    """Find a visual's auto-generated ID by walking the sheet's emitted
    Visuals list and matching on title. Visual_ids are auto-derived
    post-L.1.21; titles are the stable identifier for tests that pin
    individual visuals.
    """
    for v in sheet.Visuals:
        for inner_name in (
            "KPIVisual", "TableVisual", "BarChartVisual",
            "SankeyDiagramVisual", "PieChartVisual",
        ):
            inner = getattr(v, inner_name, None)
            if inner is None:
                continue
            inner_title = inner.Title.FormatText.get("PlainText")
            if inner_title == title:
                return inner.VisualId
    raise AssertionError(f"No visual on sheet matches title={title!r}")


def _visual_kinds(sheet) -> list[str]:
    """Return the kind ('KPIVisual', 'TableVisual', ...) of each visual
    on the sheet in order. Used in lieu of explicit visual_ids for
    "this sheet has [KPI, BarChart, Table] in this order" structure
    checks (visual_ids are auto-generated post-L.1.21)."""
    kinds: list[str] = []
    for v in sheet.Visuals:
        for inner_name in (
            "KPIVisual", "TableVisual", "BarChartVisual",
            "SankeyDiagramVisual", "PieChartVisual",
        ):
            if getattr(v, inner_name, None) is not None:
                kinds.append(inner_name)
                break
    return kinds


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------

def test_analysis_has_six_sheets_in_expected_order():
    """5 content sheets + the M.4.4.5 App Info ("i") sheet last."""
    from recon_gen.apps.investigation.constants import SHEET_INV_APP_INFO

    analysis = build_analysis(_TEST_CFG)
    sheet_ids = [s.SheetId for s in analysis.Definition.Sheets]
    assert sheet_ids == [
        SHEET_INV_GETTING_STARTED,
        SHEET_INV_FANOUT,
        SHEET_INV_ANOMALIES,
        SHEET_INV_MONEY_TRAIL,
        SHEET_INV_ACCOUNT_NETWORK,
        SHEET_INV_APP_INFO,
    ]


def test_analysis_name_carries_deployment_name():
    # Z.C — every L2-fed app's analysis name follows the
    # ``Name (deployment_name)`` shape so multi-deploy QS accounts are
    # visually distinguishable in the dashboard list.
    analysis = build_analysis(_TEST_CFG)
    assert analysis.Name == f"Investigation ({_TEST_CFG.deployment_name})"


def test_dashboard_mirrors_analysis_definition():
    analysis = build_analysis(_TEST_CFG)
    dashboard = build_investigation_dashboard(_TEST_CFG)
    # Both wrap the same definition builder, so sheet counts align.
    assert len(dashboard.Definition.Sheets) == len(analysis.Definition.Sheets)
    assert dashboard.DashboardId == _TEST_CFG.prefixed("investigation-dashboard")


def test_every_sheet_has_a_description():
    """Plain-language description per sheet — enforced across all apps."""
    analysis = build_analysis(_TEST_CFG)
    for sheet in analysis.Definition.Sheets:
        assert sheet.Description, f"{sheet.SheetId} is missing a description"


def test_analysis_serializes_to_aws_json():
    """to_aws_json() must succeed end-to-end — no None-strip crashes.

    5 content sheets + the M.4.4.5 App Info ("i") sheet = 6 total."""
    j = build_analysis(_TEST_CFG).to_aws_json()
    assert j["AnalysisId"] == _TEST_CFG.prefixed("investigation-analysis")
    assert len(j["Definition"]["Sheets"]) == 6


# ---------------------------------------------------------------------------
# K.4.3 — Recipient Fanout dataset
# ---------------------------------------------------------------------------

def test_investigation_datasets_in_expected_order():
    """K.4.3 dataset first, K.4.4 matview-backed dataset second,
    Y.1.b.companion distribution dataset third (no σ pushdown — for
    the unfiltered distribution chart), K.4.5 money-trail matview
    dataset fourth, Y.2.a.companion roots dataset fifth (no parameter
    pushdown — feeds only the chain-root dropdown), K.4.8
    account-network wrapper sixth, K.4.8k narrow accounts dataset
    seventh. M.4.4.5 appended the 2 App Info datasets last. Order
    matters — analysis.py's DataSetIdentifierDeclarations zip relies
    on it."""
    datasets = build_all_datasets(_TEST_CFG, _TEST_L2)
    assert len(datasets) == 9
    assert datasets[0].DataSetId == _TEST_CFG.prefixed("inv-recipient-fanout-dataset")
    assert datasets[1].DataSetId == _TEST_CFG.prefixed("inv-volume-anomalies-dataset")
    assert datasets[2].DataSetId == _TEST_CFG.prefixed("inv-volume-anomalies-distribution-dataset")
    assert datasets[3].DataSetId == _TEST_CFG.prefixed("inv-money-trail-dataset")
    assert datasets[4].DataSetId == _TEST_CFG.prefixed("inv-money-trail-roots-dataset")
    assert datasets[5].DataSetId == _TEST_CFG.prefixed("inv-account-network-dataset")
    assert datasets[6].DataSetId == _TEST_CFG.prefixed("inv-anetwork-accounts-dataset")
    assert datasets[7].DataSetId == _TEST_CFG.prefixed("inv-app-info-liveness-dataset")
    assert datasets[8].DataSetId == _TEST_CFG.prefixed("inv-app-info-matviews-dataset")


def test_investigation_datasets_declared_in_analysis():
    """7 content datasets + the 2 M.4.4.5 App Info datasets.
    Y.1.b.companion added DS_INV_VOLUME_ANOMALIES_DISTRIBUTION;
    Y.2.a.companion added DS_INV_MONEY_TRAIL_ROOTS."""
    from recon_gen.common.sheets.app_info import (
        DS_APP_INFO_LIVENESS, DS_APP_INFO_MATVIEWS,
    )

    analysis = build_analysis(_TEST_CFG)
    decls = analysis.Definition.DataSetIdentifierDeclarations
    assert [d.Identifier for d in decls] == [
        DS_INV_RECIPIENT_FANOUT,
        DS_INV_VOLUME_ANOMALIES,
        DS_INV_VOLUME_ANOMALIES_DISTRIBUTION,
        DS_INV_MONEY_TRAIL,
        DS_INV_MONEY_TRAIL_ROOTS,
        DS_INV_ACCOUNT_NETWORK,
        DS_INV_ANETWORK_ACCOUNTS,
        DS_APP_INFO_LIVENESS,
        DS_APP_INFO_MATVIEWS,
    ]


def test_recipient_fanout_contract_columns():
    """Contract names every column the SQL projects — required for the
    threshold calc field and the table's group-by to resolve."""
    names = RECIPIENT_FANOUT_CONTRACT.column_names
    assert "recipient_account_id" in names
    assert "sender_account_id" in names
    assert "transfer_id" in names
    assert "posted_at" in names
    assert "amount" in names


def test_recipient_fanout_sql_filters_recipient_to_leaf_internal_accounts():
    """N.4.o v6 column rename: the v5 ``account_type IN ('dda',
    'merchant_dda')`` filter became the leaf-internal predicate
    (``account_scope = 'internal' AND account_parent_role IS NOT NULL``)
    — administrative sweeps land in singleton control accounts, those
    have ``parent_role IS NULL`` and get filtered out, so the fanout
    signal stays focused on real customer recipients."""
    ds = build_all_datasets(_TEST_CFG, _TEST_L2)[0]
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
    assert "t.account_scope = 'internal'" in sql
    assert "t.account_parent_role IS NOT NULL" in sql


# ---------------------------------------------------------------------------
# K.4.3 — Filter groups + parameter
# ---------------------------------------------------------------------------

def test_filter_groups_in_expected_order():
    """K.4.3 fanout window filter (Y.3.a dropped FG_INV_FANOUT_THRESHOLD
    — distinct_senders is now a dataset window column with the
    threshold pushed into dataset SQL via
    ``<<$pInvFanoutThreshold>>``), K.4.4 anomalies window filter
    (Y.1.d dropped FG_INV_ANOMALIES_SIGMA — σ now lives in the
    dataset SQL via ``<<$pInvAnomaliesSigma>>``), then the K.4.5
    money-trail window date-range filter (Y.2.a dropped the three
    parameter-bound K.4.5 FGs — root / hops / amount now live in the
    money-trail dataset SQL), then two K.4.8 account-network
    directional filter groups (Y.2.b dropped the broad-anchor +
    min-amount FGs — those now live in the account-network dataset
    SQL; the inbound/outbound FGs remain to partition the
    pre-narrowed anchor-touching set per Sankey). Order is stable so
    the deployed Definition diff is readable."""
    groups = _filter_groups()
    ids = [g.FilterGroupId for g in groups]
    assert ids == [
        FG_INV_FANOUT_WINDOW,
        FG_INV_ANOMALIES_WINDOW,
        FG_INV_MONEY_TRAIL_WINDOW,  # Q.1.b
        # Y.2.b dropped FG_INV_ANETWORK_ANCHOR (broad anchor narrow now
        # in dataset SQL) + FG_INV_ANETWORK_AMOUNT (min-amount cutoff
        # too); the directional FGs remain to partition the
        # pre-narrowed anchor-touching set per Sankey.
        FG_INV_ANETWORK_INBOUND,
        FG_INV_ANETWORK_OUTBOUND,
    ]


def test_fanout_threshold_pushed_into_dataset_sql():
    """Y.3.a — the threshold lives in the dataset SQL as a window-column
    pushdown (`WHERE distinct_senders >= <<$pInvFanoutThreshold>>`)
    with a `MappedDataSetParameters` bridge; replaces the pre-Y.3
    analysis-level `NumericRangeFilter` on the calc field that QS
    applied but App2 didn't."""
    from recon_gen.apps.investigation.datasets import (
        build_recipient_fanout_dataset,
    )
    from recon_gen.common.dataset_contract import get_sql

    ds = build_recipient_fanout_dataset(_TEST_CFG)
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
    assert (
        f"WHERE dpr.distinct_senders >= <<${P_INV_FANOUT_THRESHOLD}>>"
        in sql
    ), "Y.3.a — threshold WHERE missing from QS-side dataset SQL"
    # PG doesn't support COUNT(DISTINCT) OVER, so distinct_senders is
    # computed via a `distinct_per_recipient` GROUP BY CTE that JOINs
    # back to the per-leg `joined` rows. Same shape on Oracle + SQLite.
    assert "COUNT(DISTINCT sender_account_id) AS distinct_senders" in sql, (
        "Y.3.a — distinct_senders GROUP BY missing"
    )
    assert "JOIN distinct_per_recipient dpr" in sql, (
        "Y.3.a — distinct_per_recipient JOIN missing"
    )
    # App2-side SQL is registered too (same string when no app2_sql=).
    app2_sql = get_sql("inv-recipient-fanout-ds")
    assert (
        f"WHERE dpr.distinct_senders >= <<${P_INV_FANOUT_THRESHOLD}>>"
        in app2_sql
    )


def test_window_filter_is_a_time_range_on_posted_at():
    groups = {g.FilterGroupId: g for g in _filter_groups()}
    window = groups[FG_INV_FANOUT_WINDOW]
    trf = window.Filters[0].TimeRangeFilter
    assert trf is not None
    assert trf.Column.ColumnName == "posted_at"
    assert trf.Column.DataSetIdentifier == DS_INV_RECIPIENT_FANOUT


def test_parameter_declarations_carry_both_thresholds():
    """Seven parameters: K.4.3 fanout threshold, K.4.4 sigma threshold,
    K.4.5 money-trail root (string) + max-hops + min-amount (integers),
    K.4.8 account-network anchor (string) + min-amount (integer)."""
    decls = _parameter_declarations()
    assert len(decls) == 7
    int_by_name = {
        d.IntegerParameterDeclaration.Name: d.IntegerParameterDeclaration
        for d in decls if d.IntegerParameterDeclaration
    }
    assert int_by_name[P_INV_FANOUT_THRESHOLD].DefaultValues == {
        "StaticValues": [DEFAULT_FANOUT_THRESHOLD],
    }
    assert int_by_name[P_INV_ANOMALIES_SIGMA].DefaultValues == {
        "StaticValues": [DEFAULT_ANOMALIES_SIGMA],
    }
    assert int_by_name[P_INV_MONEY_TRAIL_MAX_HOPS].DefaultValues == {
        "StaticValues": [DEFAULT_MONEY_TRAIL_MAX_HOPS],
    }
    assert int_by_name[P_INV_MONEY_TRAIL_MIN_AMOUNT].DefaultValues == {
        "StaticValues": [DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
    }
    # K.4.8 anchor amount slider reuses Money Trail's default of 0.
    assert int_by_name[P_INV_ANETWORK_MIN_AMOUNT].DefaultValues == {
        "StaticValues": [DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
    }
    str_by_name = {
        d.StringParameterDeclaration.Name: d.StringParameterDeclaration
        for d in decls if d.StringParameterDeclaration
    }
    # No default — the dropdown auto-populates from the matview's
    # distinct root_transfer_id values.
    assert str_by_name[P_INV_MONEY_TRAIL_ROOT].DefaultValues == {
        "StaticValues": [],
    }
    # No default — analyst picks the anchor on first render.
    assert str_by_name[P_INV_ANETWORK_ANCHOR].DefaultValues == {
        "StaticValues": [],
    }


def test_fanout_sheet_carries_window_filter_and_threshold_slider():
    fc = _filter_controls(SHEET_INV_FANOUT)
    pc = _parameter_controls(SHEET_INV_FANOUT)
    assert len(fc) == 1
    assert fc[0].DateTimePicker is not None  # date range widget
    assert len(pc) == 1
    slider = pc[0].Slider
    assert slider is not None
    assert slider.SourceParameterName == P_INV_FANOUT_THRESHOLD
    assert slider.MinimumValue == SLIDER_MIN
    assert slider.MaximumValue == SLIDER_MAX
    assert slider.StepSize == 1


# ---------------------------------------------------------------------------
# K.4.3 — Calc field
# ---------------------------------------------------------------------------

def test_distinct_sender_calc_field_dropped_in_y3a():
    """Y.3.a — distinct_senders is now a real dataset window column, no
    longer an analysis-level CalcField. Test guards against the calc
    field accidentally coming back via copy-paste."""
    analysis = build_analysis(_TEST_CFG)
    cf_names = {
        cf["Name"] for cf in analysis.Definition.CalculatedFields or []
    }
    assert CF_INV_FANOUT_DISTINCT_SENDERS not in cf_names, (
        "Y.3.a — recipient_distinct_sender_count should be a dataset "
        "column, not a CalcField"
    )


# ---------------------------------------------------------------------------
# K.4.3 — Recipient Fanout sheet visuals + layout
# ---------------------------------------------------------------------------

def test_fanout_sheet_has_three_kpis_and_one_table():
    analysis = build_analysis(_TEST_CFG)
    fanout = next(
        s for s in analysis.Definition.Sheets if s.SheetId == SHEET_INV_FANOUT
    )
    assert fanout.Visuals is not None
    # Three KPIs followed by one Table (visual_ids are auto-generated
    # post-L.1.21; titles are the stable identifier for asserting order).
    titles = [
        (v.KPIVisual.Title.FormatText["PlainText"] if v.KPIVisual else
         v.TableVisual.Title.FormatText["PlainText"] if v.TableVisual else None)
        for v in fanout.Visuals
    ]
    assert titles == [
        "Qualifying Recipients",
        "Distinct Senders",
        "Total Inbound",
        "Recipient Fanout — Ranked",
    ]


def test_fanout_table_aggregates_to_recipient_grain():
    analysis = build_analysis(_TEST_CFG)
    fanout = next(
        s for s in analysis.Definition.Sheets if s.SheetId == SHEET_INV_FANOUT
    )
    table = next(v.TableVisual for v in fanout.Visuals if v.TableVisual)
    field_wells = table.ChartConfiguration.FieldWells
    # Aggregated, not unaggregated — table groups by recipient identity.
    assert field_wells.TableAggregatedFieldWells is not None
    group_by_cols = [
        d.CategoricalDimensionField.Column.ColumnName
        for d in field_wells.TableAggregatedFieldWells.GroupBy
        if d.CategoricalDimensionField
    ]
    assert group_by_cols == [
        "recipient_account_id",
        "recipient_account_name",
        "recipient_account_type",
    ]


def test_fanout_sheet_serializes_to_aws_json():
    """End-to-end serialization sanity: filters, calc fields, params,
    visuals, and layout all surface without dataclass-shape errors."""
    j = build_analysis(_TEST_CFG).to_aws_json()
    fanout = next(
        s for s in j["Definition"]["Sheets"] if s["SheetId"] == SHEET_INV_FANOUT
    )
    assert len(fanout["Visuals"]) == 4
    assert len(fanout["FilterControls"]) == 1
    assert len(fanout["ParameterControls"]) == 1
    # Top-level: 5 filter groups (Y.1.d dropped FG_INV_ANOMALIES_SIGMA
    # — σ now lives in dataset SQL; Y.2.a dropped the 3 parameter-bound
    # FGs for money-trail root/hops/amount; Y.2.b dropped the broad
    # anchor + min-amount account-network FGs; Y.3.a dropped
    # FG_INV_FANOUT_THRESHOLD — distinct_senders is now a window column
    # in dataset SQL, threshold pushed via <<$pInvFanoutThreshold>>
    # — leaving 1 fanout window + 1 anomalies window + 1 money-trail
    # window + 2 account network directional (inbound/outbound)).
    # 0 calc fields after Y.3.a + Y.3.b: Y.3.a dropped fanout
    # distinct_senders calc; Y.3.b dropped is_inbound_edge +
    # is_outbound_edge + counterparty_display — all four are now
    # dataset columns.
    # 7 parameters (fanout threshold + sigma + money-trail
    # root/hops/amount + account-network anchor/min-amount) — unchanged
    # by Y.3.a/b since the slider/dropdown params still drive controls.
    assert len(j["Definition"]["FilterGroups"]) == 5
    cfs = j["Definition"].get("CalculatedFields") or []
    assert len(cfs) == 0
    assert len(j["Definition"]["ParameterDeclarations"]) == 7


# ---------------------------------------------------------------------------
# K.4.4 — Volume Anomalies dataset + matview wiring
# ---------------------------------------------------------------------------

def test_volume_anomalies_contract_exposes_z_score_and_bucket():
    names = VOLUME_ANOMALIES_CONTRACT.column_names
    # Pair identity
    assert "sender_account_id" in names
    assert "recipient_account_id" in names
    # Window bounds
    assert "window_start" in names
    assert "window_end" in names
    # Aggregates + population stats
    assert "window_sum" in names
    assert "transfer_count" in names
    assert "pop_mean" in names
    assert "pop_stddev" in names
    # Anomaly scoring
    assert "z_score" in names
    assert "z_bucket" in names


def test_volume_anomalies_dataset_reads_from_matview():
    """Dataset is a thin SELECT over the per-instance matview — no inline
    windowing or population-stat math at dataset time. The whole point of
    the matview is to keep that work out of QuickSight Direct Query.

    N.3.d: matview name is per-instance prefixed.
    """
    datasets = build_all_datasets(_TEST_CFG, _TEST_L2)
    anomalies = datasets[1]
    sql = next(iter(anomalies.PhysicalTableMap.values())).CustomSql.SqlQuery
    assert "FROM spec_example_inv_pair_rolling_anomalies" in sql
    # Don't reach back into transactions / daily_balances at dataset load.
    # (The prefixed base table name is NOT in this dataset's SQL — it
    # only references the matview which itself wraps the base table.)
    assert "spec_example_transactions" not in sql
    assert "OVER" not in sql
    # AO.1.impl — the dataset projection now lists ``pop_stddev`` as a
    # column (the SELECT * was expanded for the cents→dollars wrap on
    # window_sum / pop_mean / pop_stddev). The original intent of this
    # gate was "no STDDEV() function CALL at dataset time" — narrow the
    # match accordingly so a bare column reference doesn't false-fail.
    assert "STDDEV_SAMP(" not in sql.upper()
    assert "STDDEV(" not in sql.upper()


# ---------------------------------------------------------------------------
# K.4.4 — Anomalies filter groups + parameter
# ---------------------------------------------------------------------------

def test_anomalies_window_filter_is_a_time_range_on_window_end():
    groups = {g.FilterGroupId: g for g in _filter_groups()}
    window = groups[FG_INV_ANOMALIES_WINDOW]
    trf = window.Filters[0].TimeRangeFilter
    assert trf is not None
    assert trf.Column.ColumnName == "window_end"
    assert trf.Column.DataSetIdentifier == DS_INV_VOLUME_ANOMALIES


def test_sigma_pushdown_lives_in_dataset_sql_not_filter_group():
    """Y.1.b — σ filter is in the dataset SQL via ``<<$pInvAnomaliesSigma>>``;
    the analysis-level FG_INV_ANOMALIES_SIGMA FilterGroup is removed.
    Both QS (literal substitution) and App2 (bind translation) read
    the same SQL. Drop in the FilterGroups dict confirms the analysis
    no longer carries the filter at the group level."""
    groups = {g.FilterGroupId: g for g in _filter_groups()}
    assert "fg-inv-anomalies-sigma" not in groups, (
        "σ filter should live in dataset SQL post-Y.1, not as a "
        "FilterGroup on the analysis."
    )


def test_sigma_pushdown_dataset_carries_integer_dataset_parameter():
    """The Volume Anomalies dataset declares ``pInvAnomaliesSigma`` as
    an IntegerDatasetParameter so QS knows where to substitute the
    ``<<$pInvAnomaliesSigma>>`` placeholder in the dataset SQL."""
    from recon_gen.apps.investigation.datasets import (
        build_volume_anomalies_dataset,
    )
    ds = build_volume_anomalies_dataset(_TEST_CFG)
    assert ds.DatasetParameters is not None
    assert len(ds.DatasetParameters) == 1
    integer_param = ds.DatasetParameters[0].IntegerDatasetParameter
    assert integer_param is not None
    assert integer_param.Name == "pInvAnomaliesSigma"
    assert integer_param.ValueType == "SINGLE_VALUED"
    assert integer_param.DefaultValues is not None
    assert integer_param.DefaultValues.StaticValues == [2]


def test_sigma_pushdown_sql_contains_qs_placeholder():
    """The SQL itself carries the QS-style ``<<$pInvAnomaliesSigma>>``
    placeholder. App2's executor preprocesses this to
    ``:param_pInvAnomaliesSigma`` at query time; QS substitutes the
    literal value at query time. Both sides one SQL truth."""
    from recon_gen.apps.investigation.datasets import (
        build_volume_anomalies_dataset,
    )
    ds = build_volume_anomalies_dataset(_TEST_CFG)
    sql = ds.PhysicalTableMap["inv-volume-anomalies"].CustomSql.SqlQuery
    assert "<<$pInvAnomaliesSigma>>" in sql
    assert "z_score >=" in sql


def test_distribution_chart_binds_to_companion_dataset_unfiltered():
    """Y.1.b.companion — the distribution bar chart MUST point at
    DS_INV_VOLUME_ANOMALIES_DISTRIBUTION (no σ pushdown) so it shows
    the full population shape regardless of where the σ slider sits.
    KPI + Table point at DS_INV_VOLUME_ANOMALIES (with σ pushdown).
    This test is the SELECTED_VISUALS workaround proof — it locks
    the per-dataset binding that replaces the pre-Y per-FilterGroup
    scope."""
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ANOMALIES
    )
    # Walk every visual on the sheet; collect dataset bindings.
    # Distribution chart is a BarChart titled "Pair-Window σ Distribution"
    # — find it and assert its dataset.
    bar_visuals = [
        v.BarChartVisual for v in sheet.Visuals
        if v.BarChartVisual is not None
    ]
    dist = next(
        b for b in bar_visuals
        if b.Title.FormatText["PlainText"] == "Pair-Window σ Distribution"
    )
    fw = dist.ChartConfiguration.FieldWells.BarChartAggregatedFieldWells
    bar_ds_ids = {
        cat.CategoricalDimensionField.Column.DataSetIdentifier
        for cat in (fw.Category or [])
    }
    assert bar_ds_ids == {DS_INV_VOLUME_ANOMALIES_DISTRIBUTION}


def test_sigma_param_bridges_to_dataset_param_via_mapping():
    """Y.1.c — the analysis-level pInvAnomaliesSigma parameter
    declaration carries a MappedDataSetParameters entry pointing at
    DS_INV_VOLUME_ANOMALIES + dataset-param-name "pInvAnomaliesSigma".
    QS uses this mapping to substitute the analysis param's value
    into the dataset SQL's <<$pInvAnomaliesSigma>> placeholder."""
    analysis = build_analysis(_TEST_CFG)
    integer_decls = [
        p.IntegerParameterDeclaration
        for p in analysis.Definition.ParameterDeclarations
        if p.IntegerParameterDeclaration is not None
    ]
    sigma_decl = next(
        d for d in integer_decls if d.Name == P_INV_ANOMALIES_SIGMA
    )
    assert sigma_decl.MappedDataSetParameters is not None
    assert len(sigma_decl.MappedDataSetParameters) == 1
    mapping = sigma_decl.MappedDataSetParameters[0]
    assert mapping.DataSetIdentifier == DS_INV_VOLUME_ANOMALIES
    assert mapping.DataSetParameterName == "pInvAnomaliesSigma"


def test_anomalies_window_filter_is_all_visuals_scope():
    """Window filter applies to every visual on the sheet — both the
    KPI/table and the distribution chart should respect the date range."""
    groups = {g.FilterGroupId: g for g in _filter_groups()}
    window = groups[FG_INV_ANOMALIES_WINDOW]
    sheet_scopes = (
        window.ScopeConfiguration.SelectedSheets.SheetVisualScopingConfigurations
    )
    assert len(sheet_scopes) == 1
    assert sheet_scopes[0].Scope == SheetVisualScopingConfiguration.ALL_VISUALS


def test_anomalies_sheet_carries_window_filter_and_sigma_slider():
    fc = _filter_controls(SHEET_INV_ANOMALIES)
    pc = _parameter_controls(SHEET_INV_ANOMALIES)
    assert len(fc) == 1
    assert fc[0].DateTimePicker is not None
    assert len(pc) == 1
    slider = pc[0].Slider
    assert slider is not None
    assert slider.SourceParameterName == P_INV_ANOMALIES_SIGMA
    assert slider.MinimumValue == SIGMA_SLIDER_MIN
    assert slider.MaximumValue == SIGMA_SLIDER_MAX
    assert slider.StepSize == 1


# ---------------------------------------------------------------------------
# K.4.4 — Volume Anomalies sheet visuals + layout
# ---------------------------------------------------------------------------

def test_anomalies_sheet_has_kpi_distribution_and_table():
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ANOMALIES
    )
    assert sheet.Visuals is not None
    # KPI flagged-count, σ distribution bar chart, ranked table — in
    # that order. Visual_ids are auto-derived (L.1.21); kind ordering
    # is the stable structural assertion.
    assert _visual_kinds(sheet) == ["KPIVisual", "BarChartVisual", "TableVisual"]


def test_distribution_chart_categorises_by_z_bucket():
    """Distribution chart's X-axis is the z-bucket dimension (e.g.
    '0-1 sigma', '1-2 sigma', ...). The Y-axis counts pair-window rows."""
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ANOMALIES
    )
    chart = next(v.BarChartVisual for v in sheet.Visuals if v.BarChartVisual)
    fields = chart.ChartConfiguration.FieldWells.BarChartAggregatedFieldWells
    cat_cols = [
        d.CategoricalDimensionField.Column.ColumnName
        for d in fields.Category if d.CategoricalDimensionField
    ]
    assert cat_cols == ["z_bucket"]
    assert len(fields.Values) == 1


def test_anomalies_table_sorted_by_z_score_desc():
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ANOMALIES
    )
    table = next(v.TableVisual for v in sheet.Visuals if v.TableVisual)
    sort = table.ChartConfiguration.SortConfiguration["RowSort"][0]["FieldSort"]
    # Field-ids are auto-derived (L.1.16). Look up the z_score field's
    # auto-id by walking the table's Values list and matching column name.
    z_score_field_id = next(
        v.NumericalMeasureField.FieldId
        for v in table.ChartConfiguration.FieldWells.TableAggregatedFieldWells.Values
        if v.NumericalMeasureField
        and v.NumericalMeasureField.Column.ColumnName == "z_score"
    )
    assert sort["FieldId"] == z_score_field_id
    assert sort["Direction"] == "DESC"


# ---------------------------------------------------------------------------
# K.4.5 — Money Trail dataset + matview wiring
# ---------------------------------------------------------------------------

def test_money_trail_contract_exposes_chain_columns():
    """Contract names every column the matview projects — root /
    transfer / depth + denormalized source + target account fields,
    hop_amount, posted_at, transfer_type."""
    names = MONEY_TRAIL_CONTRACT.column_names
    # Chain identity
    assert "root_transfer_id" in names
    assert "transfer_id" in names
    assert "depth" in names
    # Source leg
    assert "source_account_id" in names
    assert "source_account_name" in names
    assert "source_account_type" in names
    # Target leg
    assert "target_account_id" in names
    assert "target_account_name" in names
    assert "target_account_type" in names
    # Edge measures + hop metadata
    assert "hop_amount" in names
    assert "posted_at" in names
    assert "rail_name" in names
    # K.4.8f walking-friendly display labels: name (id) — both human-
    # readable AND uniquely keyed.
    assert "source_display" in names
    assert "target_display" in names


def test_money_trail_dataset_reads_from_matview_with_pushdown_where():
    """Dataset is a thin SELECT over the per-instance matview with the
    Y.2.a parameter pushdowns baked into the WHERE — recursive walk +
    leg join happens at refresh time, not dataset load. The whole
    point of the matview is to keep the WITH RECURSIVE out of
    QuickSight Direct Query.

    Y.2.a — SQL substitutes ``<<$pInvMoneyTrailRoot>>`` /
    ``<<$pInvMoneyTrailMaxHops>>`` / ``<<$pInvMoneyTrailMinAmount>>``
    at query time so the database does the chain narrow + depth cap
    + amount cutoff before rows cross the wire.
    """
    datasets = build_all_datasets(_TEST_CFG, _TEST_L2)
    money_trail = datasets[3]  # Y.1.b.companion shifted index by +1
    sql = next(iter(money_trail.PhysicalTableMap.values())).CustomSql.SqlQuery
    assert "FROM spec_example_inv_money_trail_edges" in sql
    # Don't reach back into the prefixed base table at dataset load.
    assert "spec_example_transactions" not in sql
    assert "RECURSIVE" not in sql.upper()
    # Y.2.a — the three pushdowns substitute literals at query time.
    assert (
        "e.root_transfer_id = <<$pInvMoneyTrailRoot>>" in sql
    )
    assert "e.depth <= <<$pInvMoneyTrailMaxHops>>" in sql
    assert "e.hop_amount >= <<$pInvMoneyTrailMinAmount>>" in sql


def test_money_trail_dataset_declares_three_pushdown_parameters():
    """Y.2.a — dataset carries StringDatasetParameter for the chain
    root + IntegerDatasetParameter for max_hops and min_amount; QS
    bridges each from its analysis-level twin via
    MappedDataSetParameters declared in
    ``apps/investigation/app.py``."""
    datasets = build_all_datasets(_TEST_CFG, _TEST_L2)
    money_trail = datasets[3]
    params = money_trail.DatasetParameters or []
    by_name = {}
    for dp in params:
        if dp.StringDatasetParameter is not None:
            by_name[dp.StringDatasetParameter.Name] = dp.StringDatasetParameter
        if dp.IntegerDatasetParameter is not None:
            by_name[dp.IntegerDatasetParameter.Name] = (
                dp.IntegerDatasetParameter
            )
    assert set(by_name.keys()) == {
        str(P_INV_MONEY_TRAIL_ROOT),
        str(P_INV_MONEY_TRAIL_MAX_HOPS),
        str(P_INV_MONEY_TRAIL_MIN_AMOUNT),
    }
    # All three are SINGLE_VALUED — no multi-select on the dropdown
    # or sliders. (Multi-valued + text-field is the L2FT-cascade
    # footgun Y.1.m blocked at construction time.)
    for p in by_name.values():
        assert p.ValueType == "SINGLE_VALUED"
    # Slider-bound params carry their analysis-level defaults so the
    # initial-paint substitution matches what the slider widget shows.
    assert by_name[str(P_INV_MONEY_TRAIL_MAX_HOPS)].DefaultValues == (
        IntegerDatasetParameterDefaultValues(
            StaticValues=[DEFAULT_MONEY_TRAIL_MAX_HOPS],
        )
    )
    assert by_name[str(P_INV_MONEY_TRAIL_MIN_AMOUNT)].DefaultValues == (
        IntegerDatasetParameterDefaultValues(
            StaticValues=[DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
        )
    )
    # Root dataset parameter has a sentinel default that matches no
    # row in the matview — initial paint of Sankey + table is empty
    # until the dropdown commits a real chain root.
    root_default = by_name[str(P_INV_MONEY_TRAIL_ROOT)].DefaultValues
    assert root_default is not None
    assert root_default.StaticValues is not None
    assert len(root_default.StaticValues) == 1
    assert "no_chain_selected" in root_default.StaticValues[0]


def test_money_trail_analysis_params_bridge_to_dataset_params():
    """Y.2.a — each analysis-level parameter declares a
    MappedDataSetParameter pointing at the money-trail dataset's
    same-named parameter. QS resolves <<$pInvMoneyTrail*>> in the
    dataset SQL by walking the bridge."""
    decls = _parameter_declarations()
    by_name = {}
    for d in decls:
        if d.IntegerParameterDeclaration:
            by_name[d.IntegerParameterDeclaration.Name] = (
                d.IntegerParameterDeclaration
            )
        if d.StringParameterDeclaration:
            by_name[d.StringParameterDeclaration.Name] = (
                d.StringParameterDeclaration
            )
    for pname in (
        P_INV_MONEY_TRAIL_ROOT,
        P_INV_MONEY_TRAIL_MAX_HOPS,
        P_INV_MONEY_TRAIL_MIN_AMOUNT,
    ):
        decl = by_name[pname]
        bridges = decl.MappedDataSetParameters or []
        assert len(bridges) == 1, (
            f"{pname} should bridge to one dataset parameter; "
            f"got {bridges}"
        )
        assert bridges[0].DataSetIdentifier == DS_INV_MONEY_TRAIL
        assert bridges[0].DataSetParameterName == str(pname)


def test_money_trail_roots_companion_dataset_is_unfiltered():
    """Y.2.a.companion — the roots companion wraps the same matview
    without any pushdown parameters. The dropdown's option fetch
    reads from this dataset so it sees every chain in the matview;
    if it pointed at the parameter-bearing money-trail dataset the
    SELECT DISTINCT root_transfer_id query would inherit the WHERE
    clause and only return the sentinel-default match (i.e. nothing).
    """
    datasets = build_all_datasets(_TEST_CFG, _TEST_L2)
    roots = datasets[4]  # immediately after the parameter-bearing dataset
    assert roots.DataSetId == _TEST_CFG.prefixed(
        "inv-money-trail-roots-dataset",
    )
    sql = next(iter(roots.PhysicalTableMap.values())).CustomSql.SqlQuery
    assert "SELECT DISTINCT root_transfer_id" in sql
    assert "FROM spec_example_inv_money_trail_edges" in sql
    # Critical: NO pushdown parameters here — the dropdown's option
    # fetch must see every chain.
    assert "<<$" not in sql
    assert not (roots.DatasetParameters or [])


def test_money_trail_roots_contract_is_single_column():
    """Y.2.a.companion — the roots dataset projects exactly one
    column (``root_transfer_id``) the dropdown reads via
    LinkedValues."""
    assert MONEY_TRAIL_ROOTS_CONTRACT.column_names == ["root_transfer_id"]


def test_money_trail_root_dropdown_links_to_companion_dataset():
    """Y.2.a — dropdown auto-populates from the unfiltered companion
    (DS_INV_MONEY_TRAIL_ROOTS), not the parameter-bearing money-trail
    dataset. Reading from the parameter-bearing one would inherit the
    <<$pInvMoneyTrailRoot>> WHERE clause and starve the dropdown."""
    pc = _parameter_controls(SHEET_INV_MONEY_TRAIL)
    # 3 controls: root dropdown, hops slider, amount slider.
    assert len(pc) == 3
    dropdown = pc[0].Dropdown
    assert dropdown is not None
    assert dropdown.SourceParameterName == P_INV_MONEY_TRAIL_ROOT
    assert dropdown.Type == "SINGLE_SELECT"
    link = dropdown.SelectableValues["LinkToDataSetColumn"]
    assert link["DataSetIdentifier"] == DS_INV_MONEY_TRAIL_ROOTS
    assert link["ColumnName"] == "root_transfer_id"


def test_money_trail_sliders_bind_to_their_parameters():
    """Hops slider + amount slider both wired to their respective
    parameters with the documented bounds."""
    pc = _parameter_controls(SHEET_INV_MONEY_TRAIL)
    hops_slider = pc[1].Slider
    assert hops_slider is not None
    assert hops_slider.SourceParameterName == P_INV_MONEY_TRAIL_MAX_HOPS
    assert hops_slider.MinimumValue == HOPS_SLIDER_MIN
    assert hops_slider.MaximumValue == HOPS_SLIDER_MAX
    assert hops_slider.StepSize == 1

    amount_slider = pc[2].Slider
    assert amount_slider is not None
    assert amount_slider.SourceParameterName == P_INV_MONEY_TRAIL_MIN_AMOUNT
    assert amount_slider.MinimumValue == AMOUNT_SLIDER_MIN
    assert amount_slider.MaximumValue == AMOUNT_SLIDER_MAX
    # Step 10 because $-units rounded to dollars; 1-step would feel
    # uselessly granular over a $0–$1000 slider range.
    assert amount_slider.StepSize == 10


def test_money_trail_sheet_has_one_date_range_filter_control():
    """Q.1.b — Money Trail ships one filter-bound DATE_RANGE picker
    (`Date Range`) plus the three parameter-driven controls (chain
    root dropdown, max-hops slider, min-amount slider). The date-range
    picker is the only FilterControl on the sheet; before Q.1.b it had
    none."""
    fc = _filter_controls(SHEET_INV_MONEY_TRAIL)
    assert len(fc) == 1
    titles = [c.DateTimePicker.Title for c in fc if c.DateTimePicker is not None]
    assert titles == ["Date Range"]


# ---------------------------------------------------------------------------
# K.4.5 — Money Trail sheet visuals + layout
# ---------------------------------------------------------------------------

def test_money_trail_sheet_has_sankey_and_table():
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_MONEY_TRAIL
    )
    assert sheet.Visuals is not None
    assert _visual_kinds(sheet) == ["SankeyDiagramVisual", "TableVisual"]


def test_money_trail_sankey_field_wells_use_account_names_and_sum_hop_amount():
    """Sankey ribbons go from source_account_name → target_account_name,
    weighted by SUM(hop_amount). Account names (not IDs) so Sankey labels
    read as banking entities, not opaque identifiers."""
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_MONEY_TRAIL
    )
    sankey = next(
        v.SankeyDiagramVisual for v in sheet.Visuals if v.SankeyDiagramVisual
    )
    fw = sankey.ChartConfiguration.FieldWells.SankeyDiagramAggregatedFieldWells
    src = [
        d.CategoricalDimensionField.Column.ColumnName
        for d in fw.Source if d.CategoricalDimensionField
    ]
    dst = [
        d.CategoricalDimensionField.Column.ColumnName
        for d in fw.Destination if d.CategoricalDimensionField
    ]
    assert src == ["source_account_name"]
    assert dst == ["target_account_name"]
    weight = fw.Weight[0].NumericalMeasureField
    assert weight.Column.ColumnName == "hop_amount"
    assert weight.AggregationFunction.SimpleNumericalAggregation == "SUM"


def test_money_trail_sankey_sort_weight_desc_with_node_cap():
    """WeightSort DESC so the heaviest ribbons render first; both
    items-limits set to the node cap with OtherCategories=INCLUDE so we
    don't silently drop edges past the cap (a real chain may have many
    siblings at the same depth)."""
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_MONEY_TRAIL
    )
    sankey = next(
        v.SankeyDiagramVisual for v in sheet.Visuals if v.SankeyDiagramVisual
    )
    sort = sankey.ChartConfiguration.SortConfiguration
    assert sort.WeightSort[0]["FieldSort"]["Direction"] == "DESC"
    assert sort.SourceItemsLimit["OtherCategories"] == "INCLUDE"
    assert sort.DestinationItemsLimit["OtherCategories"] == "INCLUDE"
    # Both caps match (50) — using the same constant so the diagram is
    # symmetric between source-side and destination-side density.
    assert (
        sort.SourceItemsLimit["ItemsLimit"]
        == sort.DestinationItemsLimit["ItemsLimit"]
    )


def test_money_trail_table_sorted_by_depth_asc_with_full_chain_grain():
    """Table aggregates to (depth, transfer_id, transfer_type, source,
    target, posted_at) so each row corresponds to one hop; sorted depth
    ASC so chains read top-to-bottom from root → leaf."""
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_MONEY_TRAIL
    )
    table = next(v.TableVisual for v in sheet.Visuals if v.TableVisual)
    fields = table.ChartConfiguration.FieldWells.TableAggregatedFieldWells
    group_by_cols = []
    for d in fields.GroupBy:
        if d.CategoricalDimensionField:
            group_by_cols.append(d.CategoricalDimensionField.Column.ColumnName)
        elif d.DateDimensionField:
            group_by_cols.append(d.DateDimensionField.Column.ColumnName)
        elif d.NumericalDimensionField:
            group_by_cols.append(d.NumericalDimensionField.Column.ColumnName)
    assert group_by_cols == [
        "depth",
        "transfer_id",
        "rail_name",
        "source_account_name",
        "target_account_name",
        "posted_at",
    ]
    sort = table.ChartConfiguration.SortConfiguration["RowSort"][0]["FieldSort"]
    # Field-ids are auto-derived (L.1.16). Look up the depth field's
    # auto-id by walking the table's GroupBy and matching column name.
    depth_field_id = next(
        d.NumericalDimensionField.FieldId
        for d in table.ChartConfiguration.FieldWells.TableAggregatedFieldWells.GroupBy
        if d.NumericalDimensionField
        and d.NumericalDimensionField.Column.ColumnName == "depth"
    )
    assert sort["FieldId"] == depth_field_id
    assert sort["Direction"] == "ASC"


def test_money_trail_sheet_serializes_to_aws_json():
    """End-to-end serialization of the new Sankey dataclass surfaces
    cleanly through to_aws_json — no None-strip crashes, no missing
    keys."""
    j = build_analysis(_TEST_CFG).to_aws_json()
    sheet = next(
        s for s in j["Definition"]["Sheets"]
        if s["SheetId"] == SHEET_INV_MONEY_TRAIL
    )
    assert len(sheet["Visuals"]) == 2
    # 3 parameter controls (root dropdown + 2 sliders) + 1 filter
    # control (Q.1.b — DATE_RANGE picker on `posted_at`).
    assert len(sheet.get("FilterControls", [])) == 1
    assert len(sheet["ParameterControls"]) == 3
    # Sankey visual surfaces with its dataclass key. Visual_id is
    # auto-derived as a UUID v5 from the position slug (M.4.4.10c);
    # just confirm the wrapper key exists with a UUID-shape value.
    import re as _re
    sankey = next(
        v for v in sheet["Visuals"] if "SankeyDiagramVisual" in v
    )
    vid = sankey["SankeyDiagramVisual"]["VisualId"]
    assert _re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        vid,
    ), f"VisualId {vid!r} should be UUID-shape"


# ---------------------------------------------------------------------------
# K.4.8 — Account Network sheet
# ---------------------------------------------------------------------------

def test_account_network_dataset_reuses_money_trail_matview_with_pushdown_where():
    """K.4.8 wraps the same matview as K.4.5 — second dataset
    registration so account-centric filters live independently. SQL
    adds the source_display / target_display walking labels.

    Y.2.b — also pushes the broad anchor narrow + min-amount cutoff
    into the WHERE: ``WHERE (source_display = <<$pInvANetworkAnchor>>
    OR target_display = <<$pInvANetworkAnchor>>) AND hop_amount >=
    <<$pInvANetworkMinAmount>>``. Pre-narrows to anchor-touching edges
    above the slider's threshold so the wire transfer is a fraction
    of the matview, even before the directional FGs partition into
    per-Sankey direction.
    """
    # Index 5 post-Y.2.a (Y.1.b.companion + Y.2.a.companion shifted +2).
    ds = build_all_datasets(_TEST_CFG, _TEST_L2)[5]
    assert ds.DataSetId == _TEST_CFG.prefixed("inv-account-network-dataset")
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
    # N.3.d: matview name is per-instance prefixed.
    assert "FROM spec_example_inv_money_trail_edges" in sql
    assert "AS source_display" in sql
    assert "AS target_display" in sql
    # Y.2.b — pushdown predicates substitute literals at query time.
    assert "source_display = <<$pInvANetworkAnchor>>" in sql
    assert "target_display = <<$pInvANetworkAnchor>>" in sql
    assert "hop_amount >= <<$pInvANetworkMinAmount>>" in sql


def test_account_network_dataset_declares_two_pushdown_parameters():
    """Y.2.b — dataset carries StringDatasetParameter for the anchor
    + IntegerDatasetParameter for min-amount; QS bridges each from
    its analysis-level twin via MappedDataSetParameters declared in
    ``apps/investigation/app.py``."""
    ds = build_all_datasets(_TEST_CFG, _TEST_L2)[5]
    params = ds.DatasetParameters or []
    by_name = {}
    for dp in params:
        if dp.StringDatasetParameter is not None:
            by_name[dp.StringDatasetParameter.Name] = dp.StringDatasetParameter
        if dp.IntegerDatasetParameter is not None:
            by_name[dp.IntegerDatasetParameter.Name] = (
                dp.IntegerDatasetParameter
            )
    assert set(by_name.keys()) == {
        str(P_INV_ANETWORK_ANCHOR),
        str(P_INV_ANETWORK_MIN_AMOUNT),
    }
    # SINGLE_VALUED on both — anchor dropdown + min-amount slider both
    # commit single values.
    for p in by_name.values():
        assert p.ValueType == "SINGLE_VALUED"
    # Min-amount default mirrors the Money Trail amount slider (0).
    assert by_name[str(P_INV_ANETWORK_MIN_AMOUNT)].DefaultValues == (
        IntegerDatasetParameterDefaultValues(
            StaticValues=[DEFAULT_MONEY_TRAIL_MIN_AMOUNT],
        )
    )
    # Anchor dataset parameter has a sentinel default that matches no
    # source_display / target_display in the matview — initial paint
    # of the Sankeys + table is empty until the dropdown commits a
    # real anchor.
    anchor_default = by_name[str(P_INV_ANETWORK_ANCHOR)].DefaultValues
    assert anchor_default is not None
    assert anchor_default.StaticValues is not None
    assert len(anchor_default.StaticValues) == 1
    assert "no_anchor_selected" in anchor_default.StaticValues[0]


def test_account_network_analysis_params_bridge_to_dataset_params():
    """Y.2.b — both analysis-level parameters declare a
    MappedDataSetParameter pointing at the account-network dataset's
    same-named parameter. QS resolves <<$pInvANetwork*>> in the
    dataset SQL by walking the bridge."""
    decls = _parameter_declarations()
    by_name = {}
    for d in decls:
        if d.IntegerParameterDeclaration:
            by_name[d.IntegerParameterDeclaration.Name] = (
                d.IntegerParameterDeclaration
            )
        if d.StringParameterDeclaration:
            by_name[d.StringParameterDeclaration.Name] = (
                d.StringParameterDeclaration
            )
    for pname in (P_INV_ANETWORK_ANCHOR, P_INV_ANETWORK_MIN_AMOUNT):
        decl = by_name[pname]
        bridges = decl.MappedDataSetParameters or []
        assert len(bridges) == 1, (
            f"{pname} should bridge to one dataset parameter; "
            f"got {bridges}"
        )
        assert bridges[0].DataSetIdentifier == DS_INV_ACCOUNT_NETWORK
        assert bridges[0].DataSetParameterName == str(pname)


def test_anchor_calc_field_dropped_after_y2b():
    """Y.2.b — ``is_anchor_edge`` calc field removed: the broad
    anchor narrow now lives in ds_anet's SQL (every row is_anchor_edge
    by construction). Y.3.b dropped the rest of the Account Network
    calc fields too — CalculatedFields may be empty / None entirely."""
    analysis = build_analysis(_TEST_CFG)
    cfs = analysis.Definition.CalculatedFields or []
    cf_names = {cf["Name"] for cf in cfs}
    assert "is_anchor_edge" not in cf_names


def test_anetwork_inbound_filter_is_inbound_sankey_only():
    """K.4.8i: inbound filter scoped to the inbound Sankey only."""
    analysis = build_analysis(_TEST_CFG)
    groups = {g.FilterGroupId: g for g in analysis.Definition.FilterGroups}
    sc = groups[FG_INV_ANETWORK_INBOUND].ScopeConfiguration
    configs = sc.SelectedSheets.SheetVisualScopingConfigurations
    assert len(configs) == 1
    assert configs[0].SheetId == SHEET_INV_ACCOUNT_NETWORK
    assert configs[0].Scope == SheetVisualScopingConfiguration.SELECTED_VISUALS
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ACCOUNT_NETWORK
    )
    assert configs[0].VisualIds == [
        _visual_id_by_title(sheet, "Inbound — counterparties → anchor"),
    ]


def test_anetwork_outbound_filter_is_outbound_sankey_only():
    """K.4.8i: outbound filter scoped to the outbound Sankey only."""
    analysis = build_analysis(_TEST_CFG)
    groups = {g.FilterGroupId: g for g in analysis.Definition.FilterGroups}
    sc = groups[FG_INV_ANETWORK_OUTBOUND].ScopeConfiguration
    configs = sc.SelectedSheets.SheetVisualScopingConfigurations
    assert len(configs) == 1
    assert configs[0].SheetId == SHEET_INV_ACCOUNT_NETWORK
    assert configs[0].Scope == SheetVisualScopingConfiguration.SELECTED_VISUALS
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ACCOUNT_NETWORK
    )
    assert configs[0].VisualIds == [
        _visual_id_by_title(sheet, "Outbound — anchor → counterparties"),
    ]


def test_anetwork_directional_filters_are_category_filters_on_calc_fields():
    """K.4.8i: each directional Sankey's filter is a CategoryFilter
    matching the calc field to 'yes' — the standard pattern for using
    a calc field as a boolean filter."""
    groups = {g.FilterGroupId: g for g in _filter_groups()}
    for fg_id, expected_col in (
        (FG_INV_ANETWORK_INBOUND, CF_INV_ANETWORK_IS_INBOUND_EDGE),
        (FG_INV_ANETWORK_OUTBOUND, CF_INV_ANETWORK_IS_OUTBOUND_EDGE),
    ):
        cf = groups[fg_id].Filters[0].CategoryFilter
        assert cf is not None
        assert cf.Column.ColumnName == expected_col
        assert cf.Column.DataSetIdentifier == DS_INV_ACCOUNT_NETWORK
        flc = cf.Configuration.FilterListConfiguration
        assert flc["MatchOperator"] == "CONTAINS"
        assert flc["CategoryValues"] == ["yes"]


def test_anetwork_anchor_dropdown_links_to_narrow_accounts_dataset():
    """K.4.8k — dropdown auto-populates from the narrow accounts
    dataset's distinct ``source_display`` values, NOT the main Account
    Network dataset. The narrow dataset pushes DISTINCT inside its
    SELECT so PG dedupes (id, name) pairs before computing the concat;
    pointing the dropdown at the main wrapper forces O(matview rows)
    work and times out as the matview grows. SelectAll stays HIDDEN
    so QuickSight lands on the first row instead of an empty/All
    state that would render two blank Sankeys."""
    pc = _parameter_controls(SHEET_INV_ACCOUNT_NETWORK)
    # 2 controls: anchor dropdown, min-amount slider.
    assert len(pc) == 2
    dropdown = pc[0].Dropdown
    assert dropdown is not None
    assert dropdown.SourceParameterName == P_INV_ANETWORK_ANCHOR
    assert dropdown.Type == "SINGLE_SELECT"
    link = dropdown.SelectableValues["LinkToDataSetColumn"]
    assert link["DataSetIdentifier"] == DS_INV_ANETWORK_ACCOUNTS
    assert link["ColumnName"] == "source_display"
    assert dropdown.DisplayOptions == {
        "SelectAllOptions": {"Visibility": "HIDDEN"},
    }


def test_anetwork_amount_slider_binds_to_parameter():
    pc = _parameter_controls(SHEET_INV_ACCOUNT_NETWORK)
    amount_slider = pc[1].Slider
    assert amount_slider is not None
    assert amount_slider.SourceParameterName == P_INV_ANETWORK_MIN_AMOUNT
    assert amount_slider.MinimumValue == AMOUNT_SLIDER_MIN
    assert amount_slider.MaximumValue == AMOUNT_SLIDER_MAX
    assert amount_slider.StepSize == 10


def test_account_network_sheet_has_no_filter_controls():
    """All filters parameter-bound; ParameterControls only."""
    fc = _filter_controls(SHEET_INV_ACCOUNT_NETWORK)
    assert fc == []


def test_account_network_sheet_has_two_sankeys_and_table():
    """K.4.8i: layout is inbound Sankey | outbound Sankey side-by-side
    on top, full-width touching-edges table below. The anchor visually
    meets in the middle of the row."""
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ACCOUNT_NETWORK
    )
    assert sheet.Visuals is not None
    assert _visual_kinds(sheet) == [
        "SankeyDiagramVisual", "SankeyDiagramVisual", "TableVisual",
    ]


def test_account_network_sankeys_field_wells_use_account_names_and_sum_hop_amount():
    """K.4.8i: both directional Sankeys carry the same field-well shape
    (source_display → target_display, weight = SUM(hop_amount)),
    sourced from the K.4.8 dataset wrapper. Direction encoding lives
    in the per-Sankey filter, not the field wells."""
    inbound, outbound, _ = _account_network_visuals()
    for sankey in (inbound, outbound):
        fw = sankey.ChartConfiguration.FieldWells.SankeyDiagramAggregatedFieldWells
        src = [
            d.CategoricalDimensionField.Column.ColumnName
            for d in fw.Source if d.CategoricalDimensionField
        ]
        dst = [
            d.CategoricalDimensionField.Column.ColumnName
            for d in fw.Destination if d.CategoricalDimensionField
        ]
        # K.4.8f switched the field wells from raw _name to _display so a
        # Sankey click delivers the exact value the dropdown stores.
        assert src == ["source_display"]
        assert dst == ["target_display"]
        weight = fw.Weight[0].NumericalMeasureField
        assert weight.Column.ColumnName == "hop_amount"
        assert weight.AggregationFunction.SimpleNumericalAggregation == "SUM"
        # Confirm sankey is sourced from the K.4.8 dataset, not K.4.5.
        assert fw.Source[0].CategoricalDimensionField.Column.DataSetIdentifier == (
            DS_INV_ACCOUNT_NETWORK
        )


def test_account_network_sheet_serializes_to_aws_json():
    j = build_analysis(_TEST_CFG).to_aws_json()
    sheet = next(
        s for s in j["Definition"]["Sheets"]
        if s["SheetId"] == SHEET_INV_ACCOUNT_NETWORK
    )
    # K.4.8i: 3 visuals — inbound Sankey | outbound Sankey | table.
    assert len(sheet["Visuals"]) == 3
    assert sheet.get("FilterControls", []) == []
    # 2 parameter controls (anchor dropdown + amount slider).
    assert len(sheet["ParameterControls"]) == 2


def _account_network_visuals():
    """Helper: returns (inbound_sankey, outbound_sankey, table) from
    the deployed Account Network sheet — mirrors the K.4.8i layout.
    Visual_ids are auto-derived (L.1.21); look up by title."""
    analysis = build_analysis(_TEST_CFG)
    sheet = next(
        s for s in analysis.Definition.Sheets
        if s.SheetId == SHEET_INV_ACCOUNT_NETWORK
    )
    sankeys_by_title = {}
    for v in sheet.Visuals:
        if v.SankeyDiagramVisual:
            title = v.SankeyDiagramVisual.Title.FormatText["PlainText"]
            sankeys_by_title[title] = v.SankeyDiagramVisual
    inbound = sankeys_by_title["Inbound — counterparties → anchor"]
    outbound = sankeys_by_title["Outbound — anchor → counterparties"]
    table = next(
        v.TableVisual for v in sheet.Visuals if v.TableVisual
    )
    return inbound, outbound, table


def _sankey_field_id_for_column(sankey, role: str, column_name: str) -> str:
    """Look up the auto-derived field_id of a Sankey leaf by role +
    column. Field-ids are auto-derived (L.1.16) so tests resolve them
    via column-name lookup rather than hardcoded strings."""
    field_wells = sankey.ChartConfiguration.FieldWells.SankeyDiagramAggregatedFieldWells
    slot_attr = {"source": "Source", "target": "Destination"}[role]
    leaves = getattr(field_wells, slot_attr) or []
    for leaf in leaves:
        if leaf.CategoricalDimensionField:
            if leaf.CategoricalDimensionField.Column.ColumnName == column_name:
                return leaf.CategoricalDimensionField.FieldId
    raise AssertionError(
        f"No Sankey {role} field with column {column_name!r}"
    )


def _table_groupby_field_id_for_column(table, column_name: str) -> str:
    """Look up the auto-derived field_id of a Table GroupBy leaf by
    column name."""
    field_wells = table.ChartConfiguration.FieldWells.TableAggregatedFieldWells
    for leaf in field_wells.GroupBy or []:
        for sub in (
            leaf.CategoricalDimensionField,
            leaf.DateDimensionField,
            leaf.NumericalDimensionField,
        ):
            if sub and sub.Column.ColumnName == column_name:
                return sub.FieldId
    raise AssertionError(
        f"No Table GroupBy field with column {column_name!r}"
    )


def test_anetwork_inbound_sankey_left_click_walks_to_source_counterparty():
    """K.4.8i: inbound Sankey wires a single DATA_POINT_CLICK (left-
    click) action that reads the SOURCE field — the counterparty
    side when the target is the anchor — and writes it into the
    anchor parameter."""
    inbound, _, _ = _account_network_visuals()
    actions = inbound.Actions
    assert actions is not None
    assert len(actions) == 1
    walk = actions[0]
    assert walk.Name == "Walk to this counterparty"
    assert walk.Trigger == "DATA_POINT_CLICK"
    nav = walk.ActionOperations[0].NavigationOperation
    assert nav is not None
    assert nav.LocalNavigationConfiguration.TargetSheetId == (
        SHEET_INV_ACCOUNT_NETWORK
    )
    set_params = walk.ActionOperations[1].SetParametersOperation
    cfg = set_params.ParameterValueConfigurations
    assert len(cfg) == 1
    assert cfg[0]["DestinationParameterName"] == P_INV_ANETWORK_ANCHOR
    assert cfg[0]["Value"]["SourceField"] == _sankey_field_id_for_column(
        inbound, "source", "source_display",
    )


def test_anetwork_outbound_sankey_left_click_walks_to_target_counterparty():
    """K.4.8i: outbound Sankey wires a single DATA_POINT_CLICK (left-
    click) action that reads the TARGET field — the counterparty
    side when the source is the anchor — and writes it into the
    anchor parameter."""
    _, outbound, _ = _account_network_visuals()
    actions = outbound.Actions
    assert actions is not None
    assert len(actions) == 1
    walk = actions[0]
    assert walk.Name == "Walk to this counterparty"
    assert walk.Trigger == "DATA_POINT_CLICK"
    nav = walk.ActionOperations[0].NavigationOperation
    assert nav is not None
    assert nav.LocalNavigationConfiguration.TargetSheetId == (
        SHEET_INV_ACCOUNT_NETWORK
    )
    set_params = walk.ActionOperations[1].SetParametersOperation
    cfg = set_params.ParameterValueConfigurations
    assert len(cfg) == 1
    assert cfg[0]["DestinationParameterName"] == P_INV_ANETWORK_ANCHOR
    assert cfg[0]["Value"]["SourceField"] == _sankey_field_id_for_column(
        outbound, "target", "target_display",
    )


def test_anetwork_table_wires_single_counterparty_walk_action():
    """K.4.8f-3: Table carries a single, unambiguous "Walk to other
    account on this edge" action that SourceFields off the analysis-
    level counterparty_display calc field — that field always projects
    the side that ISN'T the current anchor, so the walk can never be a
    no-op."""
    _, _, table = _account_network_visuals()
    actions = table.Actions
    assert actions is not None
    assert len(actions) == 1
    walk = actions[0]
    assert walk.Name == "Walk to other account on this edge"
    assert walk.Trigger == "DATA_POINT_MENU"
    set_params = walk.ActionOperations[1].SetParametersOperation
    cfg = set_params.ParameterValueConfigurations
    assert len(cfg) == 1
    assert cfg[0]["DestinationParameterName"] == P_INV_ANETWORK_ANCHOR
    assert cfg[0]["Value"]["SourceField"] == _table_groupby_field_id_for_column(
        table, CF_INV_ANETWORK_COUNTERPARTY_DISPLAY,
    )


def test_anetwork_table_columns_use_display_strings():
    """Table source / target columns are the display strings AND the
    counterparty_display calc field is exposed as a column so the
    single-action walk has a SourceField to read off."""
    _, _, table = _account_network_visuals()
    fields = table.ChartConfiguration.FieldWells.TableAggregatedFieldWells
    cols = []
    for d in fields.GroupBy:
        if d.CategoricalDimensionField:
            cols.append(d.CategoricalDimensionField.Column.ColumnName)
    assert "source_display" in cols
    assert "target_display" in cols
    assert CF_INV_ANETWORK_COUNTERPARTY_DISPLAY in cols
    # And the raw _name columns are gone — display replaces them.
    assert "source_account_name" not in cols
    assert "target_account_name" not in cols


def test_anetwork_calc_fields_pushed_into_dataset_sql():
    """Y.3.b — is_inbound_edge / is_outbound_edge / counterparty_display
    are now computed in the dataset SQL via CASE expressions over
    ``<<$pInvANetworkAnchor>>`` and projected as real columns. Pre-Y.3
    they were analysis-level CalcFields; pushdown means QS + App2 see
    one shape and the Sankey direction filters can target real columns."""
    from recon_gen.apps.investigation.datasets import (
        ACCOUNT_NETWORK_CONTRACT,
        build_account_network_dataset,
    )

    # 1. Contract carries the three new columns.
    cols = ACCOUNT_NETWORK_CONTRACT.column_names
    assert "is_inbound_edge" in cols
    assert "is_outbound_edge" in cols
    assert "counterparty_display" in cols

    # 2. Dataset SQL has the CASE expressions referencing the anchor.
    ds = build_account_network_dataset(_TEST_CFG)
    sql = next(iter(ds.PhysicalTableMap.values())).CustomSql.SqlQuery
    anchor = f"<<${P_INV_ANETWORK_ANCHOR}>>"
    assert (
        f"CASE WHEN target_display = {anchor} "
        f"THEN 'yes' ELSE 'no' END AS is_inbound_edge" in sql
    )
    assert (
        f"CASE WHEN source_display = {anchor} "
        f"THEN 'yes' ELSE 'no' END AS is_outbound_edge" in sql
    )
    assert (
        f"CASE WHEN source_display = {anchor} "
        f"THEN target_display ELSE source_display END "
        f"AS counterparty_display" in sql
    )

    # 3. CalcFields no longer carry these names.
    analysis = build_analysis(_TEST_CFG)
    cf_names = {
        cf["Name"] for cf in analysis.Definition.CalculatedFields or []
    }
    assert CF_INV_ANETWORK_IS_INBOUND_EDGE not in cf_names
    assert CF_INV_ANETWORK_IS_OUTBOUND_EDGE not in cf_names
    assert CF_INV_ANETWORK_COUNTERPARTY_DISPLAY not in cf_names


def test_money_trail_root_dropdown_hides_select_all():
    """K.4.8f: Money Trail chain-root dropdown also hides SelectAll —
    a Sankey with no chain root selected renders blank, so 'All' is
    misleading. SelectAll HIDDEN forces QS to land on the first row."""
    pc = _parameter_controls(SHEET_INV_MONEY_TRAIL)
    dropdown = pc[0].Dropdown
    assert dropdown is not None
    assert dropdown.SourceParameterName == P_INV_MONEY_TRAIL_ROOT
    assert dropdown.DisplayOptions == {
        "SelectAllOptions": {"Visibility": "HIDDEN"},
    }


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _write_min_config(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.yaml"
    # Z.C — required cfg fields.
    cfg_path.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: recon-inv-cli\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: 'arn:aws:quicksight:us-west-2:111122223333:datasource/x'\n",
        encoding="utf-8",
    )
    return cfg_path


def test_json_apply_writes_investigation_files(tmp_path: Path):
    """Q.3.a: ``json apply`` is the bundled emit verb; investigation
    JSON files (analysis, dashboard, theme, recipient-fanout dataset)
    land in the output dir."""
    cfg_path = _write_min_config(tmp_path)
    out_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["json", "apply", "-c", str(cfg_path), "-o", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "theme.json").is_file()
    assert (out_dir / "investigation-analysis.json").is_file()
    assert (out_dir / "investigation-dashboard.json").is_file()
    # K.4.3 — recipient-fanout dataset JSON must be written too.
    # Z.C — deployment_name from _write_min_config (recon-inv-cli) is
    # the single ID prefix.
    fanout_ds = out_dir / "datasets" / (
        "recon-inv-cli-inv-recipient-fanout-dataset.json"
    )
    assert fanout_ds.is_file()


def test_json_apply_writes_investigation_app_jsons(tmp_path: Path):
    """Q.3.a: same `json apply` verb covers every app — re-asserts
    investigation lands in the bundled emit alongside the others."""
    cfg_path = _write_min_config(tmp_path)
    out_dir = tmp_path / "out-all"
    runner = CliRunner()
    result = runner.invoke(
        main, ["json", "apply", "-c", str(cfg_path), "-o", str(out_dir)],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "investigation-analysis.json").is_file()
