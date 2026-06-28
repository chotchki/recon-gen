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

DW.1 (QuickSight removal): the analysis-shape assertions below walk the
TREE object graph (``build_investigation_app(...).analysis``) rather
than the emitted ``models.Analysis`` — "Tree IS the source of truth".
The dataset-builder assertions still go through ``build_all_datasets``
(the dataset emit path stays). The QuickSight-API serializers (the
analysis / dashboard / None-strip JSON emitters) are being retired in
DW.8; nothing here calls them.
"""

from __future__ import annotations

import re


from recon_gen.apps.investigation.app import build_investigation_app
from recon_gen.apps.investigation.constants import (
    CF_INV_ANETWORK_COUNTERPARTY_DISPLAY,
    CF_INV_ANETWORK_IS_INBOUND_EDGE,
    CF_INV_ANETWORK_IS_OUTBOUND_EDGE,
    CF_INV_FANOUT_DISTINCT_SENDERS,
    DS_INV_ACCOUNT_NETWORK,
    DS_INV_ACCOUNT_NETWORK_INBOUND,
    DS_INV_ACCOUNT_NETWORK_OUTBOUND,
    DS_INV_ANETWORK_ACCOUNTS,
    DS_INV_MONEY_TRAIL,
    DS_INV_MONEY_TRAIL_ROOTS,
    DS_INV_RECIPIENT_FANOUT,
    DS_INV_VOLUME_ANOMALIES,
    DS_INV_VOLUME_ANOMALIES_DISTRIBUTION,
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
    build_all_datasets,  # pyright: ignore[reportUnknownVariableType]: L2Instance import alias reads as Unknown here despite datasets.py being typed
)
from recon_gen.common.config import Config
from recon_gen.common.spine._emit_helpers import DEFAULT_PREFIX
from recon_gen.common.dataset_contract import BuiltDataset
from recon_gen.common.models import (
    IntegerDatasetParameter,
    IntegerDatasetParameterDefaultValues,
    StringDatasetParameter,
)
from recon_gen.common.tree import (
    Analysis,
    App,
    BarChart,
    Dim,
    Drill,
    FilterDateTimePicker,
    FilterControlLike,
    FilterGroup,
    IntegerParam,
    LinkedValues,
    Measure,
    ParameterControlLike,
    ParameterDeclLike,
    ParameterDropdown,
    ParameterSlider,
    Sankey,
    Sheet,
    StringParam,
    Table,
    TimeRangeFilter,
)
from recon_gen.common.tree.calc_fields import resolve_column
from recon_gen.common.tree.fields import ROW_ONE_CALC_PREFIX
from tests._test_helpers import make_test_config


# N.3.f: Investigation is now L2-fed and requires the cfg to carry
# the matching DB-table prefix so its dataset SQL renders the right
# matview names. Z.C — db_table_prefix replaces the prior auto-stamped
# l2_instance_prefix; pin to spec_example since
# ``build_investigation_app`` defaults to the spec_example L2 fixture.
_TEST_CFG = make_test_config(db_table_prefix=DEFAULT_PREFIX)

# Investigation's ``build_all_datasets`` requires an L2Instance for
# the App Info matview names (P.9f.f — dropped silent fallback). Tests
# pass the spec_example default so prefix derivation matches _TEST_CFG.
from recon_gen.common.l2 import L2Instance, default_l2_instance  # noqa: E402

_TEST_L2: L2Instance = default_l2_instance()


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
# Same inline-the-literal convention: the app's ``_SANKEY_NODE_CAP``
# caps source+destination nodes per Sankey. Mirrored here so a drift in
# either side trips the chain-Sankey + account-network-Sankey node-cap
# assertions.
SANKEY_NODE_CAP = 50


# ---------------------------------------------------------------------------
# Tree-walk helpers — every analysis-shape test reads the App tree
# (post-resolve), not an emitted model. ``build_investigation_app`` wires
# the same tree the deploy path emits; ``resolve_auto_ids`` fills the
# auto IDs (visual_id / field_id / control_id / drill target_sheet) so
# the reads below see resolved values.
# ---------------------------------------------------------------------------

def _app(cfg: Config = _TEST_CFG) -> App:
    app = build_investigation_app(cfg)
    app.resolve_auto_ids()
    return app


def _analysis(cfg: Config = _TEST_CFG) -> Analysis:
    app = _app(cfg)
    assert app.analysis is not None, "Investigation App must carry an Analysis"
    return app.analysis


def _filter_groups(cfg: Config = _TEST_CFG) -> list[FilterGroup]:
    """Walk the tree's filter groups (post-resolve)."""
    return _analysis(cfg).filter_groups


def _parameter_declarations(cfg: Config = _TEST_CFG) -> list[ParameterDeclLike]:
    """Walk the tree's parameter declarations (post-resolve)."""
    return _analysis(cfg).parameters


def _sheet_by_id(sheet_id: str, cfg: Config = _TEST_CFG) -> Sheet:  # typing-smell: ignore[bare-str-id]: sheet_id comes from callers as raw analyst string
    """Find a tree Sheet by its `sheet_id`."""
    return next(s for s in _analysis(cfg).sheets if s.sheet_id == sheet_id)


def _filter_controls(
    sheet_id: str, cfg: Config = _TEST_CFG,  # typing-smell: ignore[bare-str-id]: sheet_id comes from callers as raw analyst string
) -> list[FilterControlLike]:
    return _sheet_by_id(sheet_id, cfg).filter_controls


def _parameter_controls(
    sheet_id: str, cfg: Config = _TEST_CFG,  # typing-smell: ignore[bare-str-id]: sheet_id comes from callers as raw analyst string
) -> list[ParameterControlLike]:
    return _sheet_by_id(sheet_id, cfg).parameter_controls


def _custom_sql(ds: BuiltDataset) -> str:
    """Pull the registered SQL from a built dataset.

    ``build_dataset`` registers each dataset's SQL under its
    visual_identifier; ``BuiltDataset.sql`` resolves it. Every dataset
    builds exactly one physical table, so there's a single SQL per
    dataset (the pre-DW.8.1.b per-table-key lookup was redundant).
    """
    return ds.sql


def _visual_kinds(sheet: Sheet) -> list[str]:
    """Return the tree-type name ('KPI', 'Table', 'BarChart', 'Sankey')
    of each visual on the sheet in order. Used in lieu of explicit
    visual_ids for "this sheet has [KPI, BarChart, Table] in this order"
    structure checks (visual_ids are auto-generated post-L.1.21)."""
    return [type(v).__name__ for v in sheet.visuals]


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------

def test_analysis_has_six_sheets_in_expected_order():
    """5 content sheets + the M.4.4.5 App Info ("i") sheet last."""
    from recon_gen.apps.investigation.constants import SHEET_INV_APP_INFO

    analysis = _analysis()
    sheet_ids = [s.sheet_id for s in analysis.sheets]
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
    analysis = _analysis()
    assert analysis.name == f"Investigation ({_TEST_CFG.aws.deployment_name})"


def test_dashboard_mirrors_analysis_definition():
    app = _app()
    assert app.analysis is not None
    assert app.dashboard is not None
    # Dashboard carries the SAME Analysis tree node it publishes, so the
    # identity check IS the mirror signal — a len==len on the same object
    # would be tautological.
    assert app.dashboard.analysis is app.analysis
    assert app.cfg.aws.prefixed(
        app.dashboard.dashboard_id_suffix,
    ) == _TEST_CFG.aws.prefixed("investigation-dashboard")


def test_every_sheet_has_a_description():
    """Plain-language description per sheet — enforced across all apps."""
    for sheet in _analysis().sheets:
        assert sheet.description, f"{sheet.sheet_id} is missing a description"


def test_analysis_id_suffix_and_sheet_count():
    """Structural carry-over from the retired emit-serialization
    test: the analysis ID reproduces the deployment-prefixed
    ``investigation-analysis`` id, and the sheet count is exactly 6
    (5 content sheets + the App Info "i" sheet)."""
    app = _app()
    assert app.analysis is not None
    assert app.cfg.aws.prefixed(
        app.analysis.analysis_id_suffix,
    ) == _TEST_CFG.aws.prefixed("investigation-analysis")
    assert len(app.analysis.sheets) == 6


# ---------------------------------------------------------------------------
# K.4.3 — Recipient Fanout dataset
# ---------------------------------------------------------------------------

def test_investigation_datasets_in_expected_order():
    """K.4.3 dataset first, K.4.4 matview-backed dataset second,
    Y.1.b.companion distribution dataset third (no σ pushdown — for
    the unfiltered distribution chart), K.4.5 money-trail matview
    dataset fourth, Y.2.a.companion roots dataset fifth (no parameter
    pushdown — feeds only the chain-root dropdown), K.4.8
    account-network wrapper sixth, BO.2 inbound + outbound directional
    siblings seventh + eighth (one Sankey each), K.4.8k narrow
    accounts dataset ninth. M.4.4.5 appended the 2 App Info datasets
    last. Order matters — analysis.py's DataSetIdentifierDeclarations
    zip relies on it."""
    datasets = build_all_datasets(_TEST_CFG, _TEST_L2)
    assert len(datasets) == 12
    assert datasets[0].DataSetId == _TEST_CFG.aws.prefixed("inv-recipient-fanout-dataset")
    assert datasets[1].DataSetId == _TEST_CFG.aws.prefixed("inv-volume-anomalies-dataset")
    assert datasets[2].DataSetId == _TEST_CFG.aws.prefixed("inv-volume-anomalies-distribution-dataset")
    assert datasets[3].DataSetId == _TEST_CFG.aws.prefixed("inv-money-trail-dataset")
    assert datasets[4].DataSetId == _TEST_CFG.aws.prefixed("inv-money-trail-roots-dataset")
    assert datasets[5].DataSetId == _TEST_CFG.aws.prefixed("inv-account-network-dataset")
    assert datasets[6].DataSetId == _TEST_CFG.aws.prefixed("inv-account-network-inbound-dataset")
    assert datasets[7].DataSetId == _TEST_CFG.aws.prefixed("inv-account-network-outbound-dataset")
    assert datasets[8].DataSetId == _TEST_CFG.aws.prefixed("inv-anetwork-accounts-dataset")
    assert datasets[9].DataSetId == _TEST_CFG.aws.prefixed("inv-app-info-liveness-dataset")
    assert datasets[10].DataSetId == _TEST_CFG.aws.prefixed("inv-app-info-matviews-dataset")
    assert datasets[11].DataSetId == _TEST_CFG.aws.prefixed("inv-app-info-latest-balance-day-dataset")


def test_investigation_datasets_declared_in_analysis():
    """9 content datasets + the 3 M.4.4.5 + DK.5.kpi App Info datasets.
    Y.1.b.companion added DS_INV_VOLUME_ANOMALIES_DISTRIBUTION;
    Y.2.a.companion added DS_INV_MONEY_TRAIL_ROOTS;
    BO.2 added DS_INV_ACCOUNT_NETWORK_INBOUND + _OUTBOUND;
    DK.5.kpi added the latest-balance-day data_anchor reader.

    The deployed ``DataSetIdentifierDeclarations`` are exactly the
    registered datasets that the tree references, in registration
    order — reproduced here off the tree without an emit round-trip."""
    from recon_gen.common.sheets.app_info import (
        app_info_latest_balance_day_id,
        app_info_liveness_id, app_info_matviews_id,
    )

    app = _app()
    referenced = app.dataset_dependencies()
    declared = [ds.identifier for ds in app.datasets if ds in referenced]
    # BO.5 — App Info datasets carry per-app identifiers.
    assert declared == [
        DS_INV_RECIPIENT_FANOUT,
        DS_INV_VOLUME_ANOMALIES,
        DS_INV_VOLUME_ANOMALIES_DISTRIBUTION,
        DS_INV_MONEY_TRAIL,
        DS_INV_MONEY_TRAIL_ROOTS,
        DS_INV_ACCOUNT_NETWORK,
        DS_INV_ACCOUNT_NETWORK_INBOUND,
        DS_INV_ACCOUNT_NETWORK_OUTBOUND,
        DS_INV_ANETWORK_ACCOUNTS,
        app_info_liveness_id("inv"),
        app_info_matviews_id("inv"),
        app_info_latest_balance_day_id("inv"),
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
    sql = _custom_sql(ds)
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
    ids = [g.filter_group_id for g in _filter_groups()]
    assert ids == [
        FG_INV_FANOUT_WINDOW,
        FG_INV_ANOMALIES_WINDOW,
        FG_INV_MONEY_TRAIL_WINDOW,  # Q.1.b
        # Y.2.b dropped FG_INV_ANETWORK_ANCHOR / FG_INV_ANETWORK_AMOUNT
        # (broad anchor narrow + min-amount cutoff pushed into dataset
        # SQL). BO.2 dropped FG_INV_ANETWORK_INBOUND / _OUTBOUND too:
        # the direction predicate now lives in the directional dataset
        # SQL (``target_display = anchor`` for inbound, ``source = anchor``
        # for outbound). The remaining three filter groups are all
        # universal date-window filters.
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
    sql = _custom_sql(ds)
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
    app2_sql = get_sql(DS_INV_RECIPIENT_FANOUT)
    assert (
        f"WHERE dpr.distinct_senders >= <<${P_INV_FANOUT_THRESHOLD}>>"
        in app2_sql
    )


def test_window_filter_is_a_time_range_on_posted_at():
    groups = {g.filter_group_id: g for g in _filter_groups()}
    window = groups[FG_INV_FANOUT_WINDOW]
    trf = window.filters[0]
    assert isinstance(trf, TimeRangeFilter)
    assert resolve_column(trf.column) == "posted_at"
    assert trf.dataset.identifier == DS_INV_RECIPIENT_FANOUT


def test_parameter_declarations_carry_both_thresholds():
    """Seven parameters: K.4.3 fanout threshold, K.4.4 sigma threshold,
    K.4.5 money-trail root (string) + max-hops + min-amount (integers),
    K.4.8 account-network anchor (string) + min-amount (integer)."""
    decls = _parameter_declarations()
    assert len(decls) == 7
    int_by_name = {p.name: p for p in decls if isinstance(p, IntegerParam)}
    assert int_by_name[P_INV_FANOUT_THRESHOLD].default == [DEFAULT_FANOUT_THRESHOLD]
    assert int_by_name[P_INV_ANOMALIES_SIGMA].default == [DEFAULT_ANOMALIES_SIGMA]
    assert int_by_name[P_INV_MONEY_TRAIL_MAX_HOPS].default == [
        DEFAULT_MONEY_TRAIL_MAX_HOPS,
    ]
    assert int_by_name[P_INV_MONEY_TRAIL_MIN_AMOUNT].default == [
        DEFAULT_MONEY_TRAIL_MIN_AMOUNT,
    ]
    # K.4.8 anchor amount slider reuses Money Trail's default of 0.
    assert int_by_name[P_INV_ANETWORK_MIN_AMOUNT].default == [
        DEFAULT_MONEY_TRAIL_MIN_AMOUNT,
    ]
    str_by_name = {p.name: p for p in decls if isinstance(p, StringParam)}
    # No default — the dropdown auto-populates from the matview's
    # distinct root_transfer_id values.
    assert str_by_name[P_INV_MONEY_TRAIL_ROOT].default == []
    # No default — analyst picks the anchor on first render.
    assert str_by_name[P_INV_ANETWORK_ANCHOR].default == []


def test_fanout_sheet_carries_window_filter_and_threshold_slider():
    fc = _filter_controls(SHEET_INV_FANOUT)
    pc = _parameter_controls(SHEET_INV_FANOUT)
    assert len(fc) == 1
    assert isinstance(fc[0], FilterDateTimePicker)  # date range widget
    assert len(pc) == 1
    slider = pc[0]
    assert isinstance(slider, ParameterSlider)
    assert slider.parameter.name == P_INV_FANOUT_THRESHOLD
    assert slider.minimum_value == SLIDER_MIN
    assert slider.maximum_value == SLIDER_MAX
    assert slider.step_size == 1


# ---------------------------------------------------------------------------
# K.4.3 — Calc field
# ---------------------------------------------------------------------------

def test_distinct_sender_calc_field_dropped_in_y3a():
    """Y.3.a — distinct_senders is now a real dataset window column, no
    longer an analysis-level CalcField. Test guards against the calc
    field accidentally coming back via copy-paste."""
    cf_names = {c.name for c in _analysis().calc_fields}
    assert CF_INV_FANOUT_DISTINCT_SENDERS not in cf_names, (
        "Y.3.a — recipient_distinct_sender_count should be a dataset "
        "column, not a CalcField"
    )


# ---------------------------------------------------------------------------
# K.4.3 — Recipient Fanout sheet visuals + layout
# ---------------------------------------------------------------------------

def test_fanout_sheet_has_three_kpis_and_one_table():
    fanout = _sheet_by_id(SHEET_INV_FANOUT)
    # Three KPIs followed by one Table (visual_ids are auto-generated
    # post-L.1.21; titles are the stable identifier for asserting order).
    titles = [getattr(v, "title") for v in fanout.visuals]
    # BO.7 — KPI is now "Distinct Senders (Union)" to disambiguate from
    # the per-recipient table column "Senders Feeding This Recipient".
    assert titles == [
        "Qualifying Recipients",
        "Distinct Senders (Union)",
        "Total Inbound",
        "Recipient Fanout — Ranked",
    ]


def test_fanout_table_aggregates_to_recipient_grain():
    fanout = _sheet_by_id(SHEET_INV_FANOUT)
    table = next(v for v in fanout.visuals if isinstance(v, Table))
    # Aggregated, not unaggregated — table groups by recipient identity
    # (group_by populated, no raw `columns`).
    assert table.group_by
    assert not table.columns
    group_by_cols = [resolve_column(d.column) for d in table.group_by]
    assert group_by_cols == [
        "recipient_account_id",
        "recipient_account_name",
        "recipient_account_type",
    ]


def test_fanout_sheet_structure():
    """Structural carry-over from the retired emit-serialization
    test: 4 visuals, 1 filter control, 1 parameter control, 3
    filter groups, 7 parameters, and ZERO hand-authored calc fields.

    0 hand-authored calc fields after Y.3.a + Y.3.b: Y.3.a dropped
    fanout distinct_senders calc; Y.3.b dropped is_inbound_edge +
    is_outbound_edge + counterparty_display — all four are now dataset
    columns. BL.1 (2026-05-27): every Dataset referenced by a
    kind="count" Measure gets an auto-registered ``_row_one_<dataset>``
    CalcField (literal 1 per row, backs NumericalMeasureField(SUM)
    row-count semantic). Those are the only calc fields the analysis
    carries — every hand-authored one is gone."""
    analysis = _analysis()
    fanout = next(
        s for s in analysis.sheets if s.sheet_id == SHEET_INV_FANOUT
    )
    assert len(fanout.visuals) == 4
    assert len(fanout.filter_controls) == 1
    assert len(fanout.parameter_controls) == 1
    # 3 filter groups (1 fanout window + 1 anomalies window + 1
    # money-trail window — every per-parameter / per-direction FG was
    # pushed into dataset SQL across Y.1.d / Y.2.a / Y.2.b / Y.3.a / BO.2).
    assert len(analysis.filter_groups) == 3
    hand_authored = [
        c for c in analysis.calc_fields
        if isinstance(c.name, str) and not c.name.startswith(ROW_ONE_CALC_PREFIX)
    ]
    assert hand_authored == []
    assert len(analysis.parameters) == 7


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
    sql = _custom_sql(anomalies)
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
    groups = {g.filter_group_id: g for g in _filter_groups()}
    window = groups[FG_INV_ANOMALIES_WINDOW]
    trf = window.filters[0]
    assert isinstance(trf, TimeRangeFilter)
    assert resolve_column(trf.column) == "window_end"
    assert trf.dataset.identifier == DS_INV_VOLUME_ANOMALIES


def test_sigma_pushdown_lives_in_dataset_sql_not_filter_group():
    """Y.1.b — σ filter is in the dataset SQL via ``<<$pInvAnomaliesSigma>>``;
    the analysis-level FG_INV_ANOMALIES_SIGMA FilterGroup is removed.
    Both QS (literal substitution) and App2 (bind translation) read
    the same SQL. Drop in the FilterGroups set confirms the analysis
    no longer carries the filter at the group level."""
    fg_ids = {g.filter_group_id for g in _filter_groups()}
    assert "fg-inv-anomalies-sigma" not in fg_ids, (
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
    assert len(ds.dataset_params) == 1
    integer_param = ds.dataset_params[0].IntegerDatasetParameter
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
    sql = _custom_sql(ds)
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
    sheet = _sheet_by_id(SHEET_INV_ANOMALIES)
    # Distribution chart is the BarChart titled "Pair-Window σ
    # Distribution" — find it and assert its category dataset binding.
    dist = next(
        v for v in sheet.visuals
        if isinstance(v, BarChart) and v.title == "Pair-Window σ Distribution"
    )
    bar_ds_ids = {d.dataset.identifier for d in dist.category}
    assert bar_ds_ids == {DS_INV_VOLUME_ANOMALIES_DISTRIBUTION}


def test_sigma_param_bridges_to_dataset_param_via_mapping():
    """Y.1.c — the analysis-level pInvAnomaliesSigma parameter
    declaration carries a MappedDataSetParameters entry pointing at
    DS_INV_VOLUME_ANOMALIES + dataset-param-name "pInvAnomaliesSigma".
    QS uses this mapping to substitute the analysis param's value
    into the dataset SQL's <<$pInvAnomaliesSigma>> placeholder."""
    sigma = next(
        p for p in _analysis().parameters if p.name == P_INV_ANOMALIES_SIGMA
    )
    assert isinstance(sigma, IntegerParam)
    assert sigma.mapped_dataset_params is not None
    assert len(sigma.mapped_dataset_params) == 1
    ds, dataset_param_name = sigma.mapped_dataset_params[0]
    assert ds.identifier == DS_INV_VOLUME_ANOMALIES
    assert dataset_param_name == "pInvAnomaliesSigma"


def test_anomalies_window_filter_is_all_visuals_scope():
    """Window filter applies to every visual on the sheet — both the
    KPI/table and the distribution chart should respect the date range.
    ``scope_sheet`` records a single ``(sheet, None)`` entry, where the
    ``None`` visual list IS the ALL_VISUALS scope."""
    groups = {g.filter_group_id: g for g in _filter_groups()}
    window = groups[FG_INV_ANOMALIES_WINDOW]
    entries = window._scope_entries
    assert len(entries) == 1
    scoped_sheet, scoped_visuals = entries[0]
    assert scoped_sheet.sheet_id == SHEET_INV_ANOMALIES
    assert scoped_visuals is None  # None = ALL_VISUALS


def test_anomalies_sheet_carries_window_filter_and_sigma_slider():
    fc = _filter_controls(SHEET_INV_ANOMALIES)
    pc = _parameter_controls(SHEET_INV_ANOMALIES)
    assert len(fc) == 1
    assert isinstance(fc[0], FilterDateTimePicker)
    assert len(pc) == 1
    slider = pc[0]
    assert isinstance(slider, ParameterSlider)
    assert slider.parameter.name == P_INV_ANOMALIES_SIGMA
    assert slider.minimum_value == SIGMA_SLIDER_MIN
    assert slider.maximum_value == SIGMA_SLIDER_MAX
    assert slider.step_size == 1


# ---------------------------------------------------------------------------
# K.4.4 — Volume Anomalies sheet visuals + layout
# ---------------------------------------------------------------------------

def test_anomalies_sheet_has_kpi_distribution_and_table():
    sheet = _sheet_by_id(SHEET_INV_ANOMALIES)
    # KPI flagged-count, σ distribution bar chart, ranked table — in
    # that order. Visual_ids are auto-derived (L.1.21); kind ordering
    # is the stable structural assertion.
    assert _visual_kinds(sheet) == ["KPI", "BarChart", "Table"]


def test_distribution_chart_categorises_by_z_bucket():
    """Distribution chart's X-axis is the z-bucket dimension (e.g.
    '0-1 sigma', '1-2 sigma', ...). The Y-axis counts pair-window rows."""
    sheet = _sheet_by_id(SHEET_INV_ANOMALIES)
    chart = next(v for v in sheet.visuals if isinstance(v, BarChart))
    cat_cols = [resolve_column(d.column) for d in chart.category]
    assert cat_cols == ["z_bucket"]
    assert len(chart.values) == 1


def test_anomalies_table_sorted_by_z_score_desc():
    sheet = _sheet_by_id(SHEET_INV_ANOMALIES)
    table = next(v for v in sheet.visuals if isinstance(v, Table))
    sb = table.sort_by
    assert sb is not None and not isinstance(sb, list)
    ref, direction = sb
    assert direction == "DESC"
    # Sorted by the z_score measure — and that same measure is one of
    # the table's values (the sort field has to project on the visual).
    assert isinstance(ref, Measure)
    assert resolve_column(ref.column) == "z_score"
    assert ref in table.values


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
    sql = _custom_sql(money_trail)
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
    params = money_trail.dataset_params
    by_name: dict[str, StringDatasetParameter | IntegerDatasetParameter] = {}
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
    root_param = by_name[str(P_INV_MONEY_TRAIL_ROOT)]
    assert isinstance(root_param, StringDatasetParameter)
    root_default = root_param.DefaultValues
    assert root_default is not None
    assert root_default.StaticValues is not None
    assert len(root_default.StaticValues) == 1
    assert "no_chain_selected" in root_default.StaticValues[0]


def test_money_trail_analysis_params_bridge_to_dataset_params():
    """Y.2.a — each analysis-level parameter declares a
    MappedDataSetParameter pointing at the money-trail dataset's
    same-named parameter. QS resolves <<$pInvMoneyTrail*>> in the
    dataset SQL by walking the bridge."""
    by_name = {p.name: p for p in _analysis().parameters}
    for pname in (
        P_INV_MONEY_TRAIL_ROOT,
        P_INV_MONEY_TRAIL_MAX_HOPS,
        P_INV_MONEY_TRAIL_MIN_AMOUNT,
    ):
        decl = by_name[pname]
        assert isinstance(decl, (StringParam, IntegerParam))
        bridges = decl.mapped_dataset_params or []
        assert len(bridges) == 1, (
            f"{pname} should bridge to one dataset parameter; "
            f"got {bridges}"
        )
        bridge_ds, bridge_name = bridges[0]
        assert bridge_ds.identifier == DS_INV_MONEY_TRAIL
        assert bridge_name == str(pname)


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
    assert roots.DataSetId == _TEST_CFG.aws.prefixed(
        "inv-money-trail-roots-dataset",
    )
    sql = _custom_sql(roots)
    # BQ.7 — outer SELECT is bare `SELECT root_transfer_id` (no DISTINCT)
    # because the inner GROUP BY already dedupes. The ORDER BY chain_total
    # DESC inside the subquery is the BQ.7 contract: QS's auto-pick-first
    # default lands on the largest chain.
    assert "SELECT root_transfer_id" in sql
    assert "ORDER BY SUM(hop_amount) DESC" in sql
    assert "FROM spec_example_inv_money_trail_edges" in sql
    # Critical: NO pushdown parameters here — the dropdown's option
    # fetch must see every chain.
    assert "<<$" not in sql
    assert not (roots.dataset_params)


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
    dropdown = pc[0]
    assert isinstance(dropdown, ParameterDropdown)
    assert dropdown.parameter.name == P_INV_MONEY_TRAIL_ROOT
    assert dropdown.type == "SINGLE_SELECT"
    assert isinstance(dropdown.selectable_values, LinkedValues)
    assert dropdown.selectable_values.dataset.identifier == DS_INV_MONEY_TRAIL_ROOTS
    assert dropdown.selectable_values.column_name == "root_transfer_id"


def test_money_trail_sliders_bind_to_their_parameters():
    """Hops slider + amount slider both wired to their respective
    parameters with the documented bounds."""
    pc = _parameter_controls(SHEET_INV_MONEY_TRAIL)
    hops_slider = pc[1]
    assert isinstance(hops_slider, ParameterSlider)
    assert hops_slider.parameter.name == P_INV_MONEY_TRAIL_MAX_HOPS
    assert hops_slider.minimum_value == HOPS_SLIDER_MIN
    assert hops_slider.maximum_value == HOPS_SLIDER_MAX
    assert hops_slider.step_size == 1

    amount_slider = pc[2]
    assert isinstance(amount_slider, ParameterSlider)
    assert amount_slider.parameter.name == P_INV_MONEY_TRAIL_MIN_AMOUNT
    assert amount_slider.minimum_value == AMOUNT_SLIDER_MIN
    assert amount_slider.maximum_value == AMOUNT_SLIDER_MAX
    # Step 10 because $-units rounded to dollars; 1-step would feel
    # uselessly granular over a $0–$1000 slider range.
    assert amount_slider.step_size == 10


def test_money_trail_sheet_has_one_date_range_filter_control():
    """Q.1.b — Money Trail ships one filter-bound DATE_RANGE picker
    (`Date Range`) plus the three parameter-driven controls (chain
    root dropdown, max-hops slider, min-amount slider). The date-range
    picker is the only FilterControl on the sheet; before Q.1.b it had
    none."""
    fc = _filter_controls(SHEET_INV_MONEY_TRAIL)
    assert len(fc) == 1
    titles = [c.title for c in fc if isinstance(c, FilterDateTimePicker)]
    assert titles == ["Date Range"]


# ---------------------------------------------------------------------------
# K.4.5 — Money Trail sheet visuals + layout
# ---------------------------------------------------------------------------

def test_money_trail_sheet_has_sankey_and_table():
    sheet = _sheet_by_id(SHEET_INV_MONEY_TRAIL)
    assert _visual_kinds(sheet) == ["Sankey", "Table"]


def test_money_trail_sankey_field_wells_use_account_names_and_sum_hop_amount():
    """Sankey ribbons go from source_account_name → target_account_name,
    weighted by SUM(hop_amount). Account names (not IDs) so Sankey labels
    read as banking entities, not opaque identifiers."""
    sheet = _sheet_by_id(SHEET_INV_MONEY_TRAIL)
    sankey = next(v for v in sheet.visuals if isinstance(v, Sankey))
    assert sankey.source is not None
    assert sankey.target is not None
    assert sankey.weight is not None
    assert resolve_column(sankey.source.column) == "source_account_name"
    assert resolve_column(sankey.target.column) == "target_account_name"
    assert resolve_column(sankey.weight.column) == "hop_amount"
    assert sankey.weight.kind == "sum"


def test_money_trail_sankey_sort_weight_desc_with_node_cap():
    """WeightSort DESC so the heaviest ribbons render first; both
    items-limits set to the node cap with OtherCategories=INCLUDE so we
    don't silently drop edges past the cap (a real chain may have many
    siblings at the same depth).

    On the tree, ``weight`` drives the (emit-fixed) DESC weight sort and
    a single ``items_limit`` drives both the source + destination caps
    (emit pins OtherCategories=INCLUDE + symmetry). The tree facts that
    DRIVE those emit constants are the weight + items_limit fields."""
    sheet = _sheet_by_id(SHEET_INV_MONEY_TRAIL)
    sankey = next(v for v in sheet.visuals if isinstance(v, Sankey))
    assert sankey.weight is not None  # backs the DESC WeightSort
    assert sankey.items_limit == SANKEY_NODE_CAP


def test_money_trail_table_sorted_by_depth_asc_with_full_chain_grain():
    """Table aggregates to (depth, transfer_id, transfer_type, source,
    target, posted_at) so each row corresponds to one hop; sorted depth
    ASC so chains read top-to-bottom from root → leaf."""
    sheet = _sheet_by_id(SHEET_INV_MONEY_TRAIL)
    table = next(v for v in sheet.visuals if isinstance(v, Table))
    group_by_cols = [resolve_column(d.column) for d in table.group_by]
    assert group_by_cols == [
        "depth",
        "transfer_id",
        "rail_name",
        "source_account_name",
        "target_account_name",
        "posted_at",
    ]
    sb = table.sort_by
    assert sb is not None and not isinstance(sb, list)
    ref, direction = sb
    assert direction == "ASC"
    # Sorted by the depth dim — which is itself one of the group_by cols.
    assert isinstance(ref, Dim)
    assert resolve_column(ref.column) == "depth"
    assert ref in table.group_by


def test_money_trail_sheet_structure():
    """Structural carry-over from the retired emit-serialization
    test: 2 visuals (Sankey + table), 1 filter control (Q.1.b
    DATE_RANGE picker), 3 parameter controls (root dropdown + 2 sliders),
    and a resolved UUID-shaped visual_id on the Sankey (auto-derived as a
    UUID v5 from the position slug, M.4.4.10c)."""
    sheet = _sheet_by_id(SHEET_INV_MONEY_TRAIL)
    assert len(sheet.visuals) == 2
    assert len(sheet.filter_controls) == 1
    assert len(sheet.parameter_controls) == 3
    sankey = next(v for v in sheet.visuals if isinstance(v, Sankey))
    vid = sankey.visual_id
    assert isinstance(vid, str)  # resolved (not the AUTO sentinel)
    assert re.match(
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
    assert ds.DataSetId == _TEST_CFG.aws.prefixed("inv-account-network-dataset")
    sql = _custom_sql(ds)
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
    params = ds.dataset_params
    by_name: dict[str, StringDatasetParameter | IntegerDatasetParameter] = {}
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
    anchor_param = by_name[str(P_INV_ANETWORK_ANCHOR)]
    assert isinstance(anchor_param, StringDatasetParameter)
    anchor_default = anchor_param.DefaultValues
    assert anchor_default is not None
    assert anchor_default.StaticValues is not None
    assert len(anchor_default.StaticValues) == 1
    assert "no_anchor_selected" in anchor_default.StaticValues[0]


def test_account_network_analysis_params_bridge_to_dataset_params():
    """Y.2.b + BO.2 — both analysis-level parameters declare a
    MappedDataSetParameter pointing at each of the three account-network
    datasets' same-named parameters: the bidirectional dataset (Table)
    and the two directional siblings (one Sankey each). QS resolves
    <<$pInvANetwork*>> in each dataset SQL by walking the bridge so all
    three render off a single anchor pick."""
    by_name = {p.name: p for p in _analysis().parameters}
    expected_bridges = {
        DS_INV_ACCOUNT_NETWORK,
        DS_INV_ACCOUNT_NETWORK_INBOUND,
        DS_INV_ACCOUNT_NETWORK_OUTBOUND,
    }
    for pname in (P_INV_ANETWORK_ANCHOR, P_INV_ANETWORK_MIN_AMOUNT):
        decl = by_name[pname]
        assert isinstance(decl, (StringParam, IntegerParam))
        bridges = decl.mapped_dataset_params or []
        assert {ds.identifier for ds, _ in bridges} == expected_bridges, (
            f"{pname} should bridge to all three account-network "
            f"datasets; got {bridges}"
        )
        for _ds, name in bridges:
            assert name == str(pname)


def test_anchor_calc_field_dropped_after_y2b():
    """Y.2.b — ``is_anchor_edge`` calc field removed: the broad
    anchor narrow now lives in ds_anet's SQL (every row is_anchor_edge
    by construction). Y.3.b dropped the rest of the Account Network
    calc fields too — CalculatedFields may be empty / only row-ones."""
    cf_names = {c.name for c in _analysis().calc_fields}
    assert "is_anchor_edge" not in cf_names


def test_bo_2_sankeys_source_from_directional_datasets():
    """BO.2 — each Sankey reads its directional sibling dataset, NOT
    the bidirectional ds_anet. Pre-BO.2 both Sankeys shared ds_anet
    and were narrowed by visual-scoped FilterGroups; App2 silently
    dropped that scoping, both Sankeys received bidirectional rows,
    and d3-sankey crashed on the resulting cycles → blank canvas.
    Pinning the dataset binding here means a stray rewire back to
    ds_anet would fail at unit time, not at the cold-read."""
    inbound, outbound, table = _account_network_visuals()
    # Inbound Sankey: source + target + weight all from the inbound dataset.
    assert inbound.source is not None
    assert inbound.source.dataset.identifier == DS_INV_ACCOUNT_NETWORK_INBOUND
    # Outbound Sankey: same, against the outbound dataset.
    assert outbound.source is not None
    assert outbound.source.dataset.identifier == DS_INV_ACCOUNT_NETWORK_OUTBOUND
    # Touching-Edges Table keeps the bidirectional dataset (it shows
    # both directions by design).
    assert table.group_by
    assert table.group_by[0].dataset.identifier == DS_INV_ACCOUNT_NETWORK


def test_bo_2_directional_datasets_apply_direction_predicate_in_sql():
    """BO.2 — the inbound dataset SQL narrows to ``target_display = anchor``
    only; the outbound to ``source_display = anchor`` only. Catches an
    accidental refactor that broadens either side back to the
    bidirectional predicate."""
    from recon_gen.apps.investigation.datasets import (
        build_account_network_inbound_dataset,
        build_account_network_outbound_dataset,
    )
    inbound = build_account_network_inbound_dataset(_TEST_CFG)
    outbound = build_account_network_outbound_dataset(_TEST_CFG)
    # ``PhysicalTableMap`` carries the CustomSql; pull it out via the
    # existing ``_custom_sql`` helper which already handles the unwrap +
    # None-guards.
    inb_sql = _custom_sql(inbound)
    out_sql = _custom_sql(outbound)
    assert "target_display = <<$pInvANetworkAnchor>>" in inb_sql
    # Inbound should NOT carry the OR-broadened bidirectional predicate.
    assert "source_display = <<$pInvANetworkAnchor>>\n    OR" not in inb_sql
    assert "source_display = <<$pInvANetworkAnchor>>" in out_sql
    assert "target_display = <<$pInvANetworkAnchor>>\n    OR" not in out_sql
    # Both still apply the min-amount cutoff.
    for sql in (inb_sql, out_sql):
        assert "hop_amount >= <<$pInvANetworkMinAmount>>" in sql


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
    dropdown = pc[0]
    assert isinstance(dropdown, ParameterDropdown)
    assert dropdown.parameter.name == P_INV_ANETWORK_ANCHOR
    assert dropdown.type == "SINGLE_SELECT"
    assert isinstance(dropdown.selectable_values, LinkedValues)
    assert dropdown.selectable_values.dataset.identifier == DS_INV_ANETWORK_ACCOUNTS
    assert dropdown.selectable_values.column_name == "source_display"
    assert dropdown.hidden_select_all is True


def test_anetwork_amount_slider_binds_to_parameter():
    pc = _parameter_controls(SHEET_INV_ACCOUNT_NETWORK)
    amount_slider = pc[1]
    assert isinstance(amount_slider, ParameterSlider)
    assert amount_slider.parameter.name == P_INV_ANETWORK_MIN_AMOUNT
    assert amount_slider.minimum_value == AMOUNT_SLIDER_MIN
    assert amount_slider.maximum_value == AMOUNT_SLIDER_MAX
    assert amount_slider.step_size == 10


def test_account_network_sheet_has_no_filter_controls():
    """All filters parameter-bound; ParameterControls only."""
    fc = _filter_controls(SHEET_INV_ACCOUNT_NETWORK)
    assert fc == []


def test_account_network_sheet_has_two_sankeys_and_table():
    """K.4.8i: layout is inbound Sankey | outbound Sankey side-by-side
    on top, full-width touching-edges table below. The anchor visually
    meets in the middle of the row."""
    sheet = _sheet_by_id(SHEET_INV_ACCOUNT_NETWORK)
    assert _visual_kinds(sheet) == ["Sankey", "Sankey", "Table"]


def test_account_network_sankeys_field_wells_use_account_names_and_sum_hop_amount():
    """K.4.8i: both directional Sankeys carry the same field-well shape
    (source_display → target_display, weight = SUM(hop_amount)). BO.2
    splits the dataset binding by direction — column names + aggregation
    stay identical, only the underlying dataset identifier differs.
    Per-Sankey dataset identity is pinned by
    ``test_bo_2_sankeys_source_from_directional_datasets``."""
    inbound, outbound, _ = _account_network_visuals()
    for sankey in (inbound, outbound):
        assert sankey.source is not None
        assert sankey.target is not None
        assert sankey.weight is not None
        # K.4.8f switched the field wells from raw _name to _display so a
        # Sankey click delivers the exact value the dropdown stores.
        assert resolve_column(sankey.source.column) == "source_display"
        assert resolve_column(sankey.target.column) == "target_display"
        assert resolve_column(sankey.weight.column) == "hop_amount"
        assert sankey.weight.kind == "sum"


def test_account_network_sheet_structure():
    """Structural carry-over from the retired emit-serialization
    test: 3 visuals (inbound Sankey | outbound Sankey | table), no
    filter controls, 2 parameter controls (anchor dropdown + amount
    slider)."""
    sheet = _sheet_by_id(SHEET_INV_ACCOUNT_NETWORK)
    assert len(sheet.visuals) == 3
    assert sheet.filter_controls == []
    assert len(sheet.parameter_controls) == 2


def _account_network_visuals() -> tuple[Sankey, Sankey, Table]:
    """Helper: returns (inbound_sankey, outbound_sankey, table) from
    the Account Network sheet — mirrors the K.4.8i layout. Visual_ids
    are auto-derived (L.1.21); look up by title."""
    sheet = _sheet_by_id(SHEET_INV_ACCOUNT_NETWORK)
    sankeys_by_title = {
        v.title: v for v in sheet.visuals if isinstance(v, Sankey)
    }
    inbound = sankeys_by_title["Inbound — counterparties → anchor"]
    outbound = sankeys_by_title["Outbound — anchor → counterparties"]
    table = next(v for v in sheet.visuals if isinstance(v, Table))
    return inbound, outbound, table


def test_anetwork_inbound_sankey_left_click_walks_to_source_counterparty():
    """K.4.8i: inbound Sankey wires a single DATA_POINT_CLICK (left-
    click) action that reads the SOURCE field — the counterparty
    side when the target is the anchor — and writes it into the
    anchor parameter."""
    inbound, _, _ = _account_network_visuals()
    drills = [a for a in inbound.actions if isinstance(a, Drill)]
    assert len(drills) == 1
    walk = drills[0]
    assert walk.name == "Walk to this counterparty"
    assert walk.trigger == "DATA_POINT_CLICK"
    # Same-sheet walk — target_sheet back-fills to the owning sheet.
    assert isinstance(walk.target_sheet, Sheet)
    assert walk.target_sheet.sheet_id == SHEET_INV_ACCOUNT_NETWORK
    assert len(walk.writes) == 1
    param, src = walk.writes[0]
    assert param.name == P_INV_ANETWORK_ANCHOR
    # The drill reads the Sankey's own source field (source_display).
    assert src is inbound.source
    assert isinstance(src, Dim)
    assert resolve_column(src.column) == "source_display"


def test_anetwork_outbound_sankey_left_click_walks_to_target_counterparty():
    """K.4.8i: outbound Sankey wires a single DATA_POINT_CLICK (left-
    click) action that reads the TARGET field — the counterparty
    side when the source is the anchor — and writes it into the
    anchor parameter."""
    _, outbound, _ = _account_network_visuals()
    drills = [a for a in outbound.actions if isinstance(a, Drill)]
    assert len(drills) == 1
    walk = drills[0]
    assert walk.name == "Walk to this counterparty"
    assert walk.trigger == "DATA_POINT_CLICK"
    assert isinstance(walk.target_sheet, Sheet)
    assert walk.target_sheet.sheet_id == SHEET_INV_ACCOUNT_NETWORK
    assert len(walk.writes) == 1
    param, src = walk.writes[0]
    assert param.name == P_INV_ANETWORK_ANCHOR
    # The drill reads the Sankey's own target field (target_display).
    assert src is outbound.target
    assert isinstance(src, Dim)
    assert resolve_column(src.column) == "target_display"


def test_anetwork_table_wires_single_counterparty_walk_action():
    """K.4.8f-3: Table carries a single, unambiguous "Walk to other
    account on this edge" action that SourceFields off the
    counterparty_display column — that column always projects the side
    that ISN'T the current anchor, so the walk can never be a no-op."""
    _, _, table = _account_network_visuals()
    drills = [a for a in table.actions if isinstance(a, Drill)]
    assert len(drills) == 1
    walk = drills[0]
    assert walk.name == "Walk to other account on this edge"
    assert walk.trigger == "DATA_POINT_MENU"
    assert len(walk.writes) == 1
    param, src = walk.writes[0]
    assert param.name == P_INV_ANETWORK_ANCHOR
    assert isinstance(src, Dim)
    assert resolve_column(src.column) == CF_INV_ANETWORK_COUNTERPARTY_DISPLAY
    # The drill source IS one of the table's group_by columns.
    assert src in table.group_by


def test_anetwork_table_columns_use_display_strings():
    """Table source / target columns are the display strings AND the
    counterparty_display column is exposed so the single-action walk
    has a SourceField to read off."""
    _, _, table = _account_network_visuals()
    cols = [resolve_column(d.column) for d in table.group_by]
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
    assert CF_INV_ANETWORK_IS_INBOUND_EDGE in cols
    assert CF_INV_ANETWORK_IS_OUTBOUND_EDGE in cols
    assert CF_INV_ANETWORK_COUNTERPARTY_DISPLAY in cols

    # 2. Dataset SQL has the CASE expressions referencing the anchor.
    ds = build_account_network_dataset(_TEST_CFG)
    sql = _custom_sql(ds)
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
    cf_names = {c.name for c in _analysis().calc_fields}
    assert CF_INV_ANETWORK_IS_INBOUND_EDGE not in cf_names
    assert CF_INV_ANETWORK_IS_OUTBOUND_EDGE not in cf_names
    assert CF_INV_ANETWORK_COUNTERPARTY_DISPLAY not in cf_names


def test_money_trail_root_dropdown_hides_select_all():
    """K.4.8f: Money Trail chain-root dropdown also hides SelectAll —
    a Sankey with no chain root selected renders blank, so 'All' is
    misleading. SelectAll HIDDEN forces QS to land on the first row."""
    pc = _parameter_controls(SHEET_INV_MONEY_TRAIL)
    dropdown = pc[0]
    assert isinstance(dropdown, ParameterDropdown)
    assert dropdown.parameter.name == P_INV_MONEY_TRAIL_ROOT
    assert dropdown.hidden_select_all is True


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


