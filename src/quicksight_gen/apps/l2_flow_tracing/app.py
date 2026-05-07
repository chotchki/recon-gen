"""L2 Flow Tracing — exercise every L2 primitive on a runtime dashboard.

M.3.4 ships the skeleton: 4 sheets (Getting Started + Rails + Chains +
L2 Exceptions), description-driven prose on Getting Started, placeholder
prose on the other three. M.3.5+ populates each tab with its real
visuals + datasets.

The app is L2-instance-fed via the same M.2d.3 prefix pattern the L1
dashboard uses: ``cfg.l2_instance_prefix`` is auto-derived from the L2
instance's ``instance`` field at build time, so dashboard ID, analysis
ID, dataset IDs, and tag-based cleanup all key off the per-instance
prefix without callers needing to pre-stamp the field.

Build pipeline::

    build_l2_flow_tracing_app(cfg, *, l2_instance=None) -> App

Default L2 instance is the persona-neutral ``spec_example.yaml``
(M.3.2 repointed away from sasquatch_ar so production library code
carries no implicit Sasquatch flavor); callers MAY override
(tests, alternative-persona deployments) via the kwarg.

Substep landmarks (each tab gets its own substep):

- M.3.4 — package skeleton + Analysis + Dashboard + 4 placeholder sheets (this commit)
- M.3.5 — Rails tab — per-Rail row table with declared + runtime columns
- M.3.6 — Chains tab — Sankey + parent-firing-count edges
- M.3.7 — L2 Exceptions tab — 6 KPI + drill sections
- M.3.8 — Auto metadata-driven filter dropdowns
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from quicksight_gen.apps.l2_flow_tracing.datasets import (
    DS_CHAIN_INSTANCES,
    DS_META_VALUES,
    DS_POSTINGS,
    DS_TT_INSTANCES,
    DS_TT_LEGS,
    DS_UNIFIED_L2_EXCEPTIONS,
    META_KEY_ALL_SENTINEL,
    META_MATCH_PASSTHROUGH_SENTINEL,
    META_VALUE_PLACEHOLDER_SENTINEL,
    build_all_l2_flow_tracing_datasets,
    bundle_status_values,
    chain_completion_status_values,
    declared_chain_parents,
    declared_metadata_keys,
    declared_rail_names,
    declared_template_names,
    transaction_status_values,
    tt_completion_status_values,
)
from quicksight_gen.common import rich_text as rt
from quicksight_gen.common.config import Config
from quicksight_gen.common.dataset_contract import ColumnShape
from quicksight_gen.common.ids import ParameterName, SheetId
from quicksight_gen.common.l2 import L2Instance, load_instance
from quicksight_gen.common.models import DateTimeDefaultValues
from quicksight_gen.common.sheets.app_info import (
    APP_INFO_SHEET_DESCRIPTION,
    APP_INFO_SHEET_NAME,
    APP_INFO_SHEET_TITLE,
    DS_APP_INFO_LIVENESS,
    DS_APP_INFO_MATVIEWS,
    populate_app_info_sheet,
)
from quicksight_gen.common.l2 import ThemePreset
from quicksight_gen.common.theme import resolve_l2_theme
from quicksight_gen.common.tree import (
    Analysis,
    App,
    CalcField,
    CategoryFilter,
    CellAccentText,
    Dataset,
    DateTimeParam,
    Drill,
    DrillParam,
    DrillResetSentinel,
    FilterGroup,
    Sheet,
    StaticValues,
    StringParam,
    TextBox,
    TimeRangeFilter,
)


# Sheet IDs — inlined per the greenfield-app convention (no constants.py
# until / unless URL stability forces it).
SHEET_GETTING_STARTED = SheetId("l2ft-sheet-getting-started")
SHEET_RAILS = SheetId("l2ft-sheet-rails")
SHEET_CHAINS = SheetId("l2ft-sheet-chains")
SHEET_TRANSFER_TEMPLATES = SheetId("l2ft-sheet-transfer-templates")
SHEET_L2_EXCEPTIONS = SheetId("l2ft-sheet-l2-exceptions")
SHEET_APP_INFO = SheetId("l2ft-sheet-app-info")  # M.4.4.5


# M.3.10m — drill from the L2 Exceptions table to the per-rail or
# per-chain explorer. Two parameters mirror the L1 dashboard's
# sentinel-pattern drill machinery: default '__ALL__' acts as a
# "no narrowing" pass-through; a real value sets the destination
# sheet's filter to that one rail / chain parent. The 2 drills
# (DATA_POINT_MENU triggers) appear as right-click menu items on
# every row of the L2 Exceptions table.
P_L2FT_RAIL_DRILL = ParameterName("pL2ftRailDrill")
P_L2FT_CHAIN_DRILL = ParameterName("pL2ftChainDrill")
_DRILL_RESET_SENTINEL = "__ALL__"
_DP_RAIL_DRILL = DrillParam(P_L2FT_RAIL_DRILL, ColumnShape.L2_DECLARED_NAME)
_DP_CHAIN_DRILL = DrillParam(
    P_L2FT_CHAIN_DRILL, ColumnShape.L2_DECLARED_NAME,
)
_L2FT_DRILL_RESET_PARAMS = (_DP_RAIL_DRILL, _DP_CHAIN_DRILL)


_GETTING_STARTED_NAME = "Getting Started"
_GETTING_STARTED_TITLE = "L2 Flow Tracing"
_GETTING_STARTED_DESCRIPTION = (
    "What this dashboard is. The L1 dashboard answers 'are my postings "
    "internally consistent?' One step up: the L2 Flow Tracing dashboard "
    "answers 'is my L2 declaration alive?' — every Rail, every Chain, "
    "every TransferTemplate, every LimitSchedule the L2 instance "
    "declares should produce activity in the runtime data. When it "
    "doesn't, that's an L2 hygiene problem, not an L1 ledger problem."
)


_RAILS_NAME = "Rails"
_RAILS_TITLE = "Rails — Transactions Explorer"
_RAILS_DESCRIPTION = (
    "Filter the postings ledger by date range, rail, status, bundle "
    "status, and (cascading) metadata key + value. Pick a Metadata Key "
    "to populate the Value dropdown; pick one or more Values to narrow "
    "the table to legs carrying that metadata."
)


_CHAINS_NAME = "Chains"
_CHAINS_TITLE = "Chains — Per-Instance Explorer"
_CHAINS_DESCRIPTION = (
    "Filter declared chain firings by date range, chain (parent rail / "
    "template name), completion status, and (cascading) metadata key + "
    "value. One row per parent transfer firing; completion_status reads "
    "'Completed' when every Required child declared for the parent fired "
    "against this transfer_id, 'Incomplete' if any required child is "
    "missing, 'No Required Children' when only optional / XOR-group "
    "children are declared."
)


_TRANSFER_TEMPLATES_NAME = "Transfer Templates"
_TRANSFER_TEMPLATES_TITLE = "Transfer Templates — Multi-Leg Flow"
_TRANSFER_TEMPLATES_DESCRIPTION = (
    "Visualize the multi-leg flow of declared TransferTemplates: each "
    "shared Transfer's debit legs flow into the template (middle node), "
    "credit legs flow out to their destination accounts. Filter by date, "
    "template, net status (Balanced / Imbalanced — checks the "
    "ExpectedNet invariant), and (cascading) metadata key + value. The "
    "Sankey shows the flow shape; the Table below shows per-instance "
    "balance detail."
)


_L2_EXCEPTIONS_NAME = "L2 Exceptions"
_L2_EXCEPTIONS_TITLE = "L2 Hygiene Exceptions"
_L2_EXCEPTIONS_DESCRIPTION = (
    "All six L2 hygiene checks unified into one row-per-violation "
    "view. KPI = total open violations; bar chart breaks down by "
    "check_type so you see which check kind dominates today; the "
    "detail table sorts by magnitude (descending) so the worst "
    "offenders surface first. Each check_type captures a "
    "'declaration vs runtime' mismatch the L1 dashboard doesn't "
    "catch — Chain Orphans, Unmatched Transfer Type, Dead Rails, "
    "Dead Bundles Activity, Dead Metadata Declarations, Dead Limit "
    "Schedules."
)


def _analysis_name(cfg: Config, l2_instance: L2Instance) -> str:
    """Title shown in QuickSight — matches L1's `Name (prefix)` shape so
    the two apps' QS asset names are visually consistent in the
    dashboard list."""
    return f"L2 Flow Tracing ({l2_instance.instance})"


def build_l2_flow_tracing_app(
    cfg: Config,
    *,
    l2_instance: L2Instance | None = None,
) -> App:
    """Construct the L2 Flow Tracing App as a tree.

    M.3.4: registers Analysis + Dashboard + 4 placeholder sheets
    (Getting Started + Rails + Chains + L2 Exceptions). No datasets,
    no visuals beyond the description prose. M.3.5+ populates each
    placeholder one substep at a time.

    Dashboard ID convention: ``<resource_prefix>-<l2_prefix>-l2-flow-tracing``
    (M.2d.3) — same prefix pattern the L1 dashboard uses, so N apps
    can deploy against the same L2 instance AND the same app can deploy
    against N L2 instances without QS resource collisions. Auto-derives
    ``cfg.l2_instance_prefix`` from ``l2_instance.instance`` if the
    caller hasn't pre-stamped it.
    """
    if l2_instance is None:
        l2_instance = _default_l2_instance()

    if cfg.l2_instance_prefix is None:
        cfg = cfg.with_l2_instance_prefix(str(l2_instance.instance))

    # N.1.f / N.4.k — resolve theme once from the L2 instance, coerced
    # to the registry default for in-canvas accent colors when the
    # instance declares no inline ``theme:`` block. The CLI uses the
    # un-coerced ``resolve_l2_theme`` return to decide whether to
    # deploy a custom Theme resource (silent-fallback to AWS CLASSIC).
    from quicksight_gen.common.theme import DEFAULT_PRESET
    theme = resolve_l2_theme(l2_instance) or DEFAULT_PRESET

    app = App(name="l2-flow-tracing", cfg=cfg)
    analysis = app.set_analysis(Analysis(
        analysis_id_suffix="l2-flow-tracing-analysis",
        name=_analysis_name(cfg, l2_instance),
    ))

    # Tree Dataset refs keyed by visual_identifier — populators pull
    # by stable name. The CLI writes the AWS-shape DataSets separately
    # (this is the L1 dashboard's split-of-concern pattern).
    datasets = _l2ft_datasets(cfg, l2_instance)
    for ds in datasets.values():
        app.add_dataset(ds)

    getting_started = analysis.add_sheet(Sheet(
        sheet_id=SHEET_GETTING_STARTED,
        name=_GETTING_STARTED_NAME,
        title=_GETTING_STARTED_TITLE,
        description=_GETTING_STARTED_DESCRIPTION,
    ))
    rails_sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_RAILS,
        name=_RAILS_NAME,
        title=_RAILS_TITLE,
        description=_RAILS_DESCRIPTION,
    ))
    chains_sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_CHAINS,
        name=_CHAINS_NAME,
        title=_CHAINS_TITLE,
        description=_CHAINS_DESCRIPTION,
    ))
    transfer_templates_sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_TRANSFER_TEMPLATES,
        name=_TRANSFER_TEMPLATES_NAME,
        title=_TRANSFER_TEMPLATES_TITLE,
        description=_TRANSFER_TEMPLATES_DESCRIPTION,
    ))
    l2_exceptions_sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_L2_EXCEPTIONS,
        name=_L2_EXCEPTIONS_NAME,
        title=_L2_EXCEPTIONS_TITLE,
        description=_L2_EXCEPTIONS_DESCRIPTION,
    ))

    _populate_getting_started(
        cfg, getting_started, l2_instance, theme=theme,
    )
    _populate_rails_sheet(
        cfg, rails_sheet,
        analysis=analysis, datasets=datasets, l2_instance=l2_instance,
    )
    _populate_chains_sheet(
        cfg, chains_sheet,
        analysis=analysis, datasets=datasets, l2_instance=l2_instance,
    )
    _populate_transfer_templates_sheet(
        cfg, transfer_templates_sheet,
        analysis=analysis, datasets=datasets, l2_instance=l2_instance,
        theme=theme,
    )
    # M.3.10m — declare the 2 drill parameters + sentinel-pattern
    # filter groups on the destination sheets (Rails / Chains) BEFORE
    # populating the L2 Exceptions sheet, since the Exceptions
    # populator wires drill actions referencing both params.
    _wire_l2ft_drill_filter_groups(
        analysis,
        datasets=datasets,
        rails_sheet=rails_sheet,
        chains_sheet=chains_sheet,
    )
    _populate_l2_exceptions_sheet(
        cfg, l2_exceptions_sheet,
        datasets=datasets,
        rails_sheet=rails_sheet,
        chains_sheet=chains_sheet,
        theme=theme,
    )

    # M.4.4.5 — App Info ("i") sheet, ALWAYS LAST. Diagnostic canary;
    # see common/sheets/app_info.py. Datasets registered via
    # `_l2ft_datasets` above (single source of truth across the
    # tree-ref + JSON-write flows).
    app_info_sheet = analysis.add_sheet(Sheet(
        sheet_id=SHEET_APP_INFO,
        name=APP_INFO_SHEET_NAME,
        title=APP_INFO_SHEET_TITLE,
        description=APP_INFO_SHEET_DESCRIPTION,
    ))
    populate_app_info_sheet(
        cfg, app_info_sheet,
        liveness_ds=datasets[DS_APP_INFO_LIVENESS],
        matview_status_ds=datasets[DS_APP_INFO_MATVIEWS],
        theme=theme,
    )

    app.create_dashboard(
        dashboard_id_suffix="l2-flow-tracing",
        name=_analysis_name(cfg, l2_instance),
    )
    return app


def _l2ft_datasets(
    cfg: Config, l2_instance: L2Instance,
) -> dict[str, Dataset]:
    """Build every L2 Flow Tracing dataset and return tree-ref Datasets
    keyed by visual_identifier.

    Each AWS DataSet's ``DataSetId`` becomes the tree Dataset's ARN
    path component; the visual identifier (the key passed to
    `build_dataset()`) becomes the tree Dataset's ``identifier`` field.
    The contract is registered as a side-effect of `build_dataset()`,
    so subsequent ``ds["col"]`` accesses validate.

    Mirrors `apps/l1_dashboard/app.py::_l1_datasets` pattern — the CLI
    writes the AWS shapes; this builds the typed tree refs for visual
    wiring on the App.
    """
    aws_datasets = build_all_l2_flow_tracing_datasets(cfg, l2_instance)
    # Order matches `build_all_l2_flow_tracing_datasets`. M.3.10c
    # dropped DS_RAILS + the 28 per-key dropdowns; replaced with
    # DS_POSTINGS + DS_META_VALUES driving the cascade. M.3.10d
    # swapped DS_CHAINS (aggregated edges) for DS_CHAIN_INSTANCES.
    # M.3.10f added DS_TT_INSTANCES + DS_TT_LEGS for the Transfer
    # Templates sheet. M.3.10l replaced the 6 separate L2 exception
    # datasets with one DS_UNIFIED_L2_EXCEPTIONS (mirrors L1's
    # todays-exceptions pattern).
    visual_ids = [
        DS_POSTINGS,
        DS_META_VALUES,
        DS_CHAIN_INSTANCES,
        DS_TT_INSTANCES,
        DS_TT_LEGS,
        DS_UNIFIED_L2_EXCEPTIONS,
        DS_APP_INFO_LIVENESS, DS_APP_INFO_MATVIEWS,  # M.4.4.5
    ]
    return {
        vid: Dataset(identifier=vid, arn=cfg.dataset_arn(aws.DataSetId))
        for vid, aws in zip(visual_ids, aws_datasets)
    }


def _default_l2_instance() -> L2Instance:
    """Persona-neutral default (M.3.2 — same as L1 dashboard's default).

    Loaded lazily from ``tests/l2/spec_example.yaml`` so the import graph
    doesn't pull the YAML at module load. Production callers always pass
    their own ``l2_instance``.
    """
    from pathlib import Path
    spec_yaml = Path(__file__).resolve().parents[3].parent / "tests" / "l2" / "spec_example.yaml"
    return load_instance(spec_yaml)


def _populate_getting_started(
    cfg: Config,
    sheet: Sheet,
    l2_instance: L2Instance,
    *,
    theme: ThemePreset,
) -> None:
    """Render the Getting Started sheet using the L2 instance's prose.

    Description-driven: welcome body comes from ``l2_instance.description``
    (NOT a hardcoded persona string). Switching L2 instance switches
    the prose — same contract the L1 dashboard's Getting Started follows.
    """
    accent = theme.accent

    # Q.1.c — collapse YAML literal-block whitespace (literal `|` block
    # scalars preserve hard newlines, which QuickSight's text-box
    # renderer drops without inserting word breaks → adjacent words
    # glom together). " ".join(text.split()) reflows the description
    # as a single paragraph; if multi-paragraph descriptions land
    # later, switch to a paragraph-aware reflow that preserves blank
    # lines as <br/><br/>.
    raw_body = (
        l2_instance.description
        if l2_instance.description
        else "(L2 instance description missing — fill the top-level "
             "`description` field in the L2 YAML.)"
    )
    welcome_body = " ".join(raw_body.split())

    sheet.layout.row(height=8).add_text_box(
        TextBox(
            text_box_id="l2ft-gs-welcome",
            content=rt.text_box(
                rt.inline(
                    _GETTING_STARTED_TITLE,
                    font_size="36px",
                    color=accent,
                ),
                rt.BR, rt.BR,
                rt.markdown(_GETTING_STARTED_DESCRIPTION),
                rt.BR, rt.BR,
                rt.subheading("L2 Instance", color=accent),
                rt.BR,
                rt.markdown(welcome_body),
            ),
        ),
        width=36,
    )


# Date-filter defaults are intentionally "all time" so a freshly-loaded
# Rails tab renders all postings — the date pickers are for narrowing,
# not for a default scope. The L1 dashboard's rolling-7-day default
# DOESN'T fit here for two reasons: (1) the demo seed plants synthetic
# postings dated 2029-11 to 2030-01-01 (deliberately decoupled from
# wall-clock), so a "now-relative" default would exclude every demo row;
# (2) Rails is an explorer tab — the analyst comes in not knowing what
# range to look at, and an unconstrained default lets them see what's
# there before narrowing. Switch to RollingDate when the L2 instance
# carries production data with current timestamps.
_DATE_START_STATIC = "1900-01-01T00:00:00.000Z"
_DATE_END_STATIC = "2099-12-31T23:59:59.999Z"


def _populate_param_filter_dropdown(
    *,
    sheet: Sheet,
    analysis: Analysis,
    dataset: Dataset,
    fg_id: str,
    filter_id: str,
    param_name: str,
    col: str,
    title: str,
    all_values: list[str],
    cross_dataset: Literal["SINGLE_DATASET", "ALL_DATASETS"] = "SINGLE_DATASET",
) -> None:
    """X.1.g — replacement for the FilterDropdown(empty CategoryFilter)
    pattern that pre-X.1.g triggered QS's lazy
    ``tenK-sample-values-V2`` fetch (the cold-CI 404 source).

    Wires three things in lock-step:

    1. A multi-valued ``StringParam`` whose default is the full list
       of declared values (so "no narrowing" is the analyst's starting
       state — every row matches because every value is selected).
    2. A ``ParameterDropdown(MULTI_SELECT, StaticValues)`` that lets
       the analyst deselect values to narrow.
    3. A parameter-bound ``CategoryFilter`` scoped to ``sheet`` that
       does ``column EQUALS pXxx`` (interpreted as IN-style for
       multi-valued params).

    ``all_values`` is closed-set: callers pass either an L2-derived
    list (rail / chain / template names) or a hardcoded enum
    (transaction status / bundle status / completion status). Either
    way, the universe is bounded at deploy time → no runtime fetch.

    ``cross_dataset`` defaults ``SINGLE_DATASET``; pass ``ALL_DATASETS``
    when the parameter should narrow several joined datasets that share
    the same column name (the Templates sheet's ``template_name`` /
    ``completion_status`` filters across tt-instances + tt-legs).
    """
    p = analysis.add_parameter(StringParam(
        name=ParameterName(param_name),
        multi_valued=True,
        default=list(all_values),
    ))
    fg = analysis.add_filter_group(FilterGroup(
        filter_group_id=fg_id,  # type: ignore[arg-type]
        cross_dataset=cross_dataset,
        filters=[CategoryFilter.with_parameter(
            filter_id=filter_id,
            dataset=dataset,
            column=dataset[col],
            parameter=p,
        )],
    ))
    fg.scope_sheet(sheet)
    sheet.add_parameter_dropdown(
        parameter=p,
        title=title,
        type="MULTI_SELECT",
        selectable_values=StaticValues(values=list(all_values)),
    )


def _populate_rails_sheet(
    cfg: Config,
    sheet: Sheet,
    *,
    analysis: Analysis,
    datasets: dict[str, Dataset],
    l2_instance: L2Instance,
) -> None:
    """Rails sheet — interactive transactions explorer (M.3.10c rewrite).

    Six controls in the sheet's filter bar drive a transactions Table:

    1. **Date From** + **Date To** — bind to ``pL2ftDateStart`` /
       ``pL2ftDateEnd``; ``TimeRangeFilter`` on ``posting``.
    2. **Rail** — multi-select ``CategoryFilter`` on ``rail_name``.
    3. **Status** — multi-select ``CategoryFilter`` on ``status``.
    4. **Bundle** — multi-select ``CategoryFilter`` on the calc'd
       ``bundle_status`` ('Bundled' / 'Unbundled').
    5. **Metadata Key** — single-select ``ParameterDropdown`` with
       ``StaticValues`` (the L2's declared keys + ``__ALL__``
       sentinel). Bound to ``pL2ftMetaKey``, mapped to ``pKey`` on
       the postings dataset (so its ``<<$pKey>>`` substitution
       narrows the table).
    6. **Metadata Value** — multi-select ``ParameterDropdown`` with
       ``LinkedValues`` from the meta-values dataset. Bound to
       ``pL2ftMetaValue``, mapped to ``pValues`` on the postings
       dataset. ``CascadingControlConfiguration`` on this control
       points at the meta-values dataset's ``metadata_key`` column,
       so QS column-match-filters its rows by the Key dropdown's
       selection — which narrows the Value dropdown's options.

    Two distinct mechanisms working together:

    - Postings table filtering: dataset parameters
      (``<<$pKey>>`` / ``<<$pValues>>``) substituted into a JSONPath
      ``IN (...)`` predicate at query time.
    - Value dropdown options narrowing: column-match cascade against
      the long-form ``(metadata_key, metadata_value)`` meta-values
      dataset. (Earlier attempt to drive this via dataset parameters
      alone failed — QS's cascade is column-match, not parameter-
      driven re-query. See M.3.10c memory.)

    The declared-rails table that lived here pre-M.3.10c moved to
    a future Docs tab; the runtime postings explorer is the focus
    here.
    """
    ds_postings = datasets[DS_POSTINGS]

    # 1+2. Date range — params + TimeRangeFilter scoped to this sheet.
    date_start = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pL2ftDateStart"),
        time_granularity="DAY",
        default=DateTimeDefaultValues(StaticValues=[_DATE_START_STATIC]),
    ))
    date_end = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pL2ftDateEnd"),
        time_granularity="DAY",
        default=DateTimeDefaultValues(StaticValues=[_DATE_END_STATIC]),
    ))
    fg_date = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-rails-date",  # type: ignore[arg-type]
        cross_dataset="SINGLE_DATASET",
        filters=[TimeRangeFilter(
            filter_id="filter-l2ft-rails-date",
            dataset=ds_postings,
            column=ds_postings["posting"],
            null_option="NON_NULLS_ONLY",
            time_granularity="DAY",
            minimum={"Parameter": "pL2ftDateStart"},
            maximum={"Parameter": "pL2ftDateEnd"},
        )],
    ))
    fg_date.scope_sheet(sheet)
    sheet.add_parameter_datetime_picker(parameter=date_start, title="Date From")
    sheet.add_parameter_datetime_picker(parameter=date_end, title="Date To")

    # 3-5. Three "default-all multi-select" CategoryFilter dropdowns
    # (rail / status / bundle status). X.1.g — restructured from
    # ``FilterDropdown(CategoryFilter(values=[], FILTER_ALL_VALUES))``
    # to ``ParameterDropdown(StaticValues) + CategoryFilter.with_parameter``.
    # The previous shape relied on QS's lazy ``tenK-sample-values-V2``
    # fetch to populate dropdown options at render time; that endpoint
    # 404s on cold per-CI-run dashboards (3 of the 4 X.1.a-traced
    # 404s came from these dropdowns). The new shape sources options
    # from StaticValues at deploy time and the parameter's default
    # spans every value, so "no narrowing" is the analyst's starting
    # state without QS having to query anything.
    _populate_param_filter_dropdown(
        sheet=sheet, analysis=analysis, dataset=ds_postings,
        fg_id="fg-l2ft-rails-rail", filter_id="filter-l2ft-rails-rail",
        param_name="pL2ftRail", col="rail_name", title="Rail",
        all_values=declared_rail_names(l2_instance),
    )
    _populate_param_filter_dropdown(
        sheet=sheet, analysis=analysis, dataset=ds_postings,
        fg_id="fg-l2ft-rails-status", filter_id="filter-l2ft-rails-status",
        param_name="pL2ftStatus", col="status", title="Status",
        all_values=transaction_status_values(),
    )
    _populate_param_filter_dropdown(
        sheet=sheet, analysis=analysis, dataset=ds_postings,
        fg_id="fg-l2ft-rails-bundle", filter_id="filter-l2ft-rails-bundle",
        param_name="pL2ftBundle", col="bundle_status", title="Bundle",
        all_values=bundle_status_values(),
    )

    # 6. Metadata cascade — the M.3.10c novelty.
    #
    # Key: single-select StaticValues from the L2 walk + sentinel.
    # Bound to pL2ftMetaKey, which maps to `pKey` on BOTH the
    # postings dataset (controls the WHERE clause) and the
    # meta-values dataset (controls which key's values populate the
    # Value dropdown).
    p_meta_key = analysis.add_parameter(StringParam(
        name=ParameterName("pL2ftMetaKey"),
        default=[META_KEY_ALL_SENTINEL],
        multi_valued=False,
        # Bridge to the postings dataset only — meta-values now uses
        # QS's native column-match cascade (driven by the Value
        # dropdown's CascadingControlConfiguration, not by SQL
        # substitution on the meta-values dataset).
        mapped_dataset_params=[
            (ds_postings, "pKey"),
        ],
    ))
    # Value: single-string text-field input bound to pL2ftMetaValue.
    #
    # Y.1.p: bridge to (postings, pValues) dropped — pValues no longer
    # exists at the dataset level. The analysis-level CategoryFilter
    # below uses pL2ftMetaValue directly to gate rows on the dataset's
    # `_meta_match_value` projection. Default is the passthrough
    # sentinel (matches every row's projection when pKey is __ALL__);
    # any user-typed value narrows to legs whose metadata key=value
    # pair matches the chosen key.
    p_meta_value = analysis.add_parameter(StringParam(
        name=ParameterName("pL2ftMetaValue"),
        default=[META_MATCH_PASSTHROUGH_SENTINEL],
        multi_valued=False,
    ))
    declared_keys = declared_metadata_keys(l2_instance)
    key_dropdown = sheet.add_parameter_dropdown(
        parameter=p_meta_key,
        title="Metadata Key",
        type="SINGLE_SELECT",
        # Sentinel first so it's the visible default; declared keys
        # follow in sorted order.
        selectable_values=StaticValues(
            values=[META_KEY_ALL_SENTINEL] + declared_keys,
        ),
    )
    # X.1.b — Free-text input (was LinkedValues dropdown). The
    # LinkedValues path triggered QS's lazy "sample values" fetch on
    # cold per-CI-run dashboards, throwing
    # ``[pageerror] Sample values not found`` and stranding the
    # Transactions table empty. Text input has no equivalent fetch —
    # the analyst types the literal value to filter on.
    sheet.add_parameter_text_field(
        parameter=p_meta_value,
        title="Metadata Value",
    )

    # Y.1.p — Analysis-level CategoryFilter that gates rows on the
    # dataset-projected `_meta_match_value` column AND wakes the
    # MappedDataSetParameters bridge for `pKey` (per the AWS docs:
    # cross-layer parameter substitution requires an analysis-level
    # filter referencing a parameter; otherwise URL-stamped values
    # reach widget state but not the dataset substitution layer).
    #
    # When pKey = '__ALL__' (default), the dataset projects the
    # passthrough sentinel for every row, and pL2ftMetaValue's default
    # IS the same sentinel — every row matches → unfiltered.
    # When user types a real value, the filter narrows to rows where
    # `_meta_match_value` (the user-picked key's JSON_VALUE) equals
    # the typed value.
    fg_meta = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-meta-cascade",  # type: ignore[arg-type]
        cross_dataset="SINGLE_DATASET",
        filters=[CategoryFilter.with_parameter(
            filter_id="filter-l2ft-meta-cascade",
            dataset=ds_postings,
            column=ds_postings["_meta_match_value"],
            parameter=p_meta_value,
        )],
    ))
    fg_meta.scope_sheet(sheet)

    # Transactions table — the postings dataset's SQL handles the
    # metadata-cascade WHERE clause via dataset parameters; the four
    # category filters narrow further.
    sheet.layout.row(height=21).add_table(
        width=36,
        title="Transactions",
        subtitle=(
            "One row per leg matching all the filters above. With no "
            "Metadata Key picked, every leg in the date window appears; "
            "picking a Key + one or more Values narrows to legs whose "
            "metadata carries that key=value pair."
        ),
        columns=[
            ds_postings["posting"].date(),
            ds_postings["rail_name"].dim(),
            ds_postings["transfer_id"].dim(),
            ds_postings["account_name"].dim(),
            ds_postings["amount_money"].numerical(currency=True),
            ds_postings["amount_direction"].dim(),
            ds_postings["status"].dim(),
            ds_postings["bundle_status"].dim(),
            ds_postings["transfer_parent_id"].dim(),
        ],
    )


def _populate_chains_sheet(
    cfg: Config,
    sheet: Sheet,
    *,
    analysis: Analysis,
    datasets: dict[str, Dataset],
    l2_instance: L2Instance,
) -> None:
    """Chains sheet — per-instance explorer (M.3.10d rewrite).

    Six controls in the sheet's filter bar drive a chain-instances
    Table:

    1. **Date From** + **Date To** — bind to ``pL2ftChainsDateStart``
       / ``pL2ftChainsDateEnd``; ``TimeRangeFilter`` on
       ``parent_posting``.
    2. **Chain** — multi-select ``CategoryFilter`` on
       ``parent_chain_name``.
    3. **Completion** — multi-select ``CategoryFilter`` on
       ``completion_status`` (Completed / Incomplete / No Required
       Children).
    4. **Metadata Key** — single-select ``ParameterDropdown`` with
       ``StaticValues`` (the L2's declared keys + ``__ALL__``
       sentinel). Mapped to ``pKey`` on the chain-instances dataset.
    5. **Metadata Value** — multi-select ``ParameterDropdown`` with
       ``LinkedValues`` from the meta-values dataset (shared with
       Rails). Mapped to ``pValues`` on the chain-instances dataset.
       ``CascadingControlConfiguration`` on this control points at
       the meta-values dataset's ``metadata_key`` column for the
       column-match cascade (same mechanism Rails uses).

    Visualization choice: Chains is a *runtime causality* concept
    (parent transfer fires → child transfer should fire later),
    not a multi-leg flow graph — Sankey does not read naturally.
    Per-firing Table is the right shape for now; revisit if a
    better visual primitive emerges. Multi-leg flow visualization
    belongs on TransferTemplates (which have explicit leg topology),
    if/when an L2 Templates explorer surface is added.
    """
    ds_chain_instances = datasets[DS_CHAIN_INSTANCES]

    # 1+2. Date range — params + TimeRangeFilter scoped to this sheet.
    # Separate from Rails' date params so the analyst's chains-window
    # selection doesn't perturb the rails view (and vice versa).
    date_start = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pL2ftChainsDateStart"),
        time_granularity="DAY",
        default=DateTimeDefaultValues(StaticValues=[_DATE_START_STATIC]),
    ))
    date_end = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pL2ftChainsDateEnd"),
        time_granularity="DAY",
        default=DateTimeDefaultValues(StaticValues=[_DATE_END_STATIC]),
    ))
    fg_date = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-chains-date",  # type: ignore[arg-type]
        cross_dataset="SINGLE_DATASET",
        filters=[TimeRangeFilter(
            filter_id="filter-l2ft-chains-date",
            dataset=ds_chain_instances,
            column=ds_chain_instances["parent_posting"],
            null_option="NON_NULLS_ONLY",
            time_granularity="DAY",
            minimum={"Parameter": "pL2ftChainsDateStart"},
            maximum={"Parameter": "pL2ftChainsDateEnd"},
        )],
    ))
    fg_date.scope_sheet(sheet)
    sheet.add_parameter_datetime_picker(parameter=date_start, title="Date From")
    sheet.add_parameter_datetime_picker(parameter=date_end, title="Date To")

    # 3+4. Chain + Completion — X.1.g — parameter-bound CategoryFilters
    # (was FilterDropdown(empty)+FILTER_ALL_VALUES, which forced QS to
    # lazy-fetch dropdown options at render time).
    _populate_param_filter_dropdown(
        sheet=sheet, analysis=analysis, dataset=ds_chain_instances,
        fg_id="fg-l2ft-chains-chain",
        filter_id="filter-l2ft-chains-chain",
        param_name="pL2ftChainsChain",
        col="parent_chain_name", title="Chain",
        all_values=declared_chain_parents(l2_instance),
    )
    _populate_param_filter_dropdown(
        sheet=sheet, analysis=analysis, dataset=ds_chain_instances,
        fg_id="fg-l2ft-chains-completion",
        filter_id="filter-l2ft-chains-completion",
        param_name="pL2ftChainsCompletion",
        col="completion_status", title="Completion",
        all_values=chain_completion_status_values(),
    )

    # 5+6. Metadata cascade — same mechanism as Rails (M.3.10c memory):
    # SQL substitution on the chain-instances dataset for the table's
    # WHERE clause + column-match CascadingControlConfiguration on the
    # Value dropdown for option-narrowing. Separate analysis params
    # from Rails so per-sheet selection doesn't bleed across tabs.
    p_meta_key = analysis.add_parameter(StringParam(
        name=ParameterName("pL2ftChainsMetaKey"),
        default=[META_KEY_ALL_SENTINEL],
        multi_valued=False,
        mapped_dataset_params=[
            (ds_chain_instances, "pKey"),
        ],
    ))
    # Y.1.p: pValues bridge dropped, default flipped to passthrough
    # sentinel — see Rails sheet for the AWS-pattern diagnostic.
    p_meta_value = analysis.add_parameter(StringParam(
        name=ParameterName("pL2ftChainsMetaValue"),
        default=[META_MATCH_PASSTHROUGH_SENTINEL],
        multi_valued=False,
    ))
    declared_keys = declared_metadata_keys(l2_instance)
    key_dropdown = sheet.add_parameter_dropdown(
        parameter=p_meta_key,
        title="Metadata Key",
        type="SINGLE_SELECT",
        selectable_values=StaticValues(
            values=[META_KEY_ALL_SENTINEL] + declared_keys,
        ),
    )
    # X.1.b — Free-text input (see Rails sheet for rationale).
    sheet.add_parameter_text_field(
        parameter=p_meta_value,
        title="Metadata Value",
    )
    # Y.1.p — analysis-level filter that wakes the pKey bridge AND
    # gates rows on `_meta_match_value`. See Rails sheet for the full
    # explanation.
    fg_meta_chains = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-chains-meta-cascade",  # type: ignore[arg-type]
        cross_dataset="SINGLE_DATASET",
        filters=[CategoryFilter.with_parameter(
            filter_id="filter-l2ft-chains-meta-cascade",
            dataset=ds_chain_instances,
            column=ds_chain_instances["_meta_match_value"],
            parameter=p_meta_value,
        )],
    ))
    fg_meta_chains.scope_sheet(sheet)

    sheet.layout.row(height=21).add_table(
        width=36,
        title="Chain Instances",
        subtitle=(
            "One row per parent transfer firing. completion_status reads "
            "'Completed' iff every Required child declared for the parent "
            "fired against this transfer_id; 'Incomplete' if any required "
            "child is missing. With no Metadata Key picked, every firing "
            "in the date window appears."
        ),
        columns=[
            ds_chain_instances["parent_posting"].date(),
            ds_chain_instances["parent_chain_name"].dim(),
            ds_chain_instances["parent_transfer_id"].dim(),
            ds_chain_instances["completion_status"].dim(),
            ds_chain_instances["required_fired"].numerical(),
            ds_chain_instances["required_total"].numerical(),
            ds_chain_instances["parent_amount_money"].numerical(currency=True),
            ds_chain_instances["parent_status"].dim(),
        ],
    )


def _populate_transfer_templates_sheet(
    cfg: Config,
    sheet: Sheet,
    *,
    analysis: Analysis,
    datasets: dict[str, Dataset],
    l2_instance: L2Instance,
    theme: ThemePreset,
) -> None:
    """Transfer Templates sheet — multi-leg flow Sankey + per-instance
    detail Table (M.3.10f).

    Two visuals stacked: Sankey (multi-leg flow through declared
    templates) and Table (per-shared-Transfer balance detail).

    Filter bar (six controls):

    1. **Date From** + **Date To** — bind to ``pL2ftTtDateStart`` /
       ``pL2ftTtDateEnd``; ``TimeRangeFilter`` on ``posting``.
       ``cross_dataset='ALL_DATASETS'`` so the filter narrows BOTH
       tt-instances + tt-legs (which both carry the column).
    2. **Template** — multi-select ``CategoryFilter`` on
       ``template_name``. Same ``ALL_DATASETS`` shape.
    3. **Completion** — multi-select ``CategoryFilter`` on
       ``completion_status`` (Complete / Imbalanced / Orphaned) —
       tt-instances only (per-firing balance + chain-completion
       check). Filter narrows the table to bundles by their L1 +
       L2 outcome.
    4. **Metadata Key** — single-select ``ParameterDropdown`` with
       ``StaticValues`` (the L2's declared keys + ``__ALL__``
       sentinel). Mapped to ``pKey`` on BOTH tt-instances + tt-legs
       (so the cascade narrows both visuals via SQL substitution).
    5. **Metadata Value** — multi-select ``ParameterDropdown`` with
       ``LinkedValues`` from the meta-values dataset (shared with
       Rails / Chains). Mapped to ``pValues`` on BOTH datasets.
       ``CascadingControlConfiguration`` on this control points at
       the meta-values dataset's ``metadata_key`` column for the
       column-match cascade.

    Sankey reads as: debit accounts → template → credit accounts.
    Each shared Transfer's debit legs flow into the template middle
    node, credit legs flow out. Picking a single Template collapses
    the Sankey to that one template's flow shape.
    """
    ds_tt_instances = datasets[DS_TT_INSTANCES]
    ds_tt_legs = datasets[DS_TT_LEGS]

    # 1+2. Date range. ALL_DATASETS so tt-legs narrows in lockstep.
    date_start = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pL2ftTtDateStart"),
        time_granularity="DAY",
        default=DateTimeDefaultValues(StaticValues=[_DATE_START_STATIC]),
    ))
    date_end = analysis.add_parameter(DateTimeParam(
        name=ParameterName("pL2ftTtDateEnd"),
        time_granularity="DAY",
        default=DateTimeDefaultValues(StaticValues=[_DATE_END_STATIC]),
    ))
    fg_date = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-tt-date",  # type: ignore[arg-type]
        cross_dataset="ALL_DATASETS",
        filters=[TimeRangeFilter(
            filter_id="filter-l2ft-tt-date",
            dataset=ds_tt_instances,
            column=ds_tt_instances["posting"],
            null_option="NON_NULLS_ONLY",
            time_granularity="DAY",
            minimum={"Parameter": "pL2ftTtDateStart"},
            maximum={"Parameter": "pL2ftTtDateEnd"},
        )],
    ))
    fg_date.scope_sheet(sheet)
    sheet.add_parameter_datetime_picker(parameter=date_start, title="Date From")
    sheet.add_parameter_datetime_picker(parameter=date_end, title="Date To")

    # 3+4. Template + Completion — X.1.g — parameter-bound
    # CategoryFilters with StaticValues source. Both keep ALL_DATASETS
    # so tt-legs narrows in lockstep with tt-instances; M.3.10k
    # denormalizes the same column names onto tt-legs to make this
    # work.
    _populate_param_filter_dropdown(
        sheet=sheet, analysis=analysis, dataset=ds_tt_instances,
        fg_id="fg-l2ft-tt-template",
        filter_id="filter-l2ft-tt-template",
        param_name="pL2ftTtTemplate",
        col="template_name", title="Template",
        all_values=declared_template_names(l2_instance),
        cross_dataset="ALL_DATASETS",
    )
    _populate_param_filter_dropdown(
        sheet=sheet, analysis=analysis, dataset=ds_tt_instances,
        fg_id="fg-l2ft-tt-completion",
        filter_id="filter-l2ft-tt-completion",
        param_name="pL2ftTtCompletion",
        col="completion_status", title="Completion",
        all_values=tt_completion_status_values(),
        cross_dataset="ALL_DATASETS",
    )

    # 5+6. Metadata cascade — same mechanism as Rails / Chains.
    # mapped_dataset_params lists BOTH tt-instances + tt-legs so the
    # cascade narrows the Sankey + Table together.
    p_meta_key = analysis.add_parameter(StringParam(
        name=ParameterName("pL2ftTtMetaKey"),
        default=[META_KEY_ALL_SENTINEL],
        multi_valued=False,
        mapped_dataset_params=[
            (ds_tt_instances, "pKey"),
            (ds_tt_legs, "pKey"),
        ],
    ))
    # Y.1.p: pValues bridges dropped, default flipped to passthrough
    # sentinel — see Rails sheet for the AWS-pattern diagnostic.
    p_meta_value = analysis.add_parameter(StringParam(
        name=ParameterName("pL2ftTtMetaValue"),
        default=[META_MATCH_PASSTHROUGH_SENTINEL],
        multi_valued=False,
    ))
    declared_keys = declared_metadata_keys(l2_instance)
    key_dropdown = sheet.add_parameter_dropdown(
        parameter=p_meta_key,
        title="Metadata Key",
        type="SINGLE_SELECT",
        selectable_values=StaticValues(
            values=[META_KEY_ALL_SENTINEL] + declared_keys,
        ),
    )
    # X.1.b — Free-text input (see Rails sheet for rationale).
    sheet.add_parameter_text_field(
        parameter=p_meta_value,
        title="Metadata Value",
    )
    # Y.1.p — analysis-level filter wakes the pKey bridge AND gates rows
    # on `_meta_match_value`. ALL_DATASETS scope so QS applies the
    # filter to every dataset on the sheet that has the column —
    # tt-instances (Sankey) AND tt-legs (table) both project
    # `_meta_match_value`, so the single filter narrows them together
    # via column-name match. See Rails sheet for the full explanation.
    #
    # QS API constraint: a FilterGroup cannot contain multiple Filters
    # spanning different DataSets — even under ALL_DATASETS the filter
    # is declared against ONE dataset and the cross-dataset scope is
    # how QS extends to siblings with the same column name.
    fg_meta_tt = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-tt-meta-cascade",  # type: ignore[arg-type]
        cross_dataset="ALL_DATASETS",
        filters=[CategoryFilter.with_parameter(
            filter_id="filter-l2ft-tt-meta-cascade",
            dataset=ds_tt_instances,
            column=ds_tt_instances["_meta_match_value"],
            parameter=p_meta_value,
        )],
    ))
    fg_meta_tt.scope_sheet(sheet)

    # Edge legend. QuickSight Sankey doesn't support data-driven
    # ribbon colors (colors auto-assign per source/target node by the
    # theme), so the matched-vs-orphan distinction is encoded in the
    # NODE NAMES — orphan edges land on a "(orphan)" suffixed node.
    # This text box spells out the three edge kinds the analyst will
    # see in the Sankey below.
    accent = theme.accent
    sheet.layout.row(height=3).add_text_box(
        TextBox(
            text_box_id="l2ft-tt-sankey-legend",
            content=rt.text_box(
                rt.subheading("Edge legend", color=accent),
                rt.bullets_raw([
                    rt.inline("Account ↔ Template", color=accent)
                    + ": the template's own legs (debit on source, "
                    "credit on destination).",
                    rt.inline("Template → <child rail>", color=accent)
                    + ": declared chain child that fired (matched edge).",
                    rt.inline(
                        "Template → <child rail> (orphan)", color=accent,
                    )
                    + ": declared chain child that didn't fire "
                    "(orphan edge — the missing-link signal).",
                ]),
            ),
        ),
        width=36,
    )

    # Sankey — multi-leg flow. flow_source / flow_target derive from
    # amount_direction (debit account → template → credit account).
    # Width = SUM(amount_abs).
    sheet.layout.row(height=12).add_sankey(
        width=36,
        title="Multi-Leg Flow — Account → Template → Account",
        subtitle=(
            "Width = total absolute amount through the edge in the "
            "filtered window. Pick a single Template to see just that "
            "template's flow shape. Ribbon colors are QuickSight's "
            "auto-assignment per source node — the matched-vs-orphan "
            "distinction is in the node names (see legend above)."
        ),
        source=ds_tt_legs["flow_source"].dim(),
        target=ds_tt_legs["flow_target"].dim(),
        weight=ds_tt_legs["amount_abs"].sum(currency=True),
    )

    sheet.layout.row(height=12).add_table(
        width=36,
        title="Template Instances",
        subtitle=(
            "One row per shared Transfer. completion_status combines "
            "the L1 balance check (legs sum to expected_net within "
            "$0.01) with the L2 chain-completion check (every Required "
            "child fired AND every XOR group has exactly one fired): "
            "'Complete' / 'Imbalanced' (L1 break) / 'Orphaned' (L2 "
            "chain break)."
        ),
        columns=[
            ds_tt_instances["posting"].date(),
            ds_tt_instances["template_name"].dim(),
            ds_tt_instances["transfer_id"].dim(),
            ds_tt_instances["completion_status"].dim(),
            ds_tt_instances["actual_net"].numerical(currency=True),
            ds_tt_instances["expected_net"].numerical(currency=True),
            ds_tt_instances["net_diff"].numerical(currency=True),
            ds_tt_instances["leg_count"].numerical(),
        ],
    )


def _wire_l2ft_drill_filter_groups(
    analysis: Analysis,
    *,
    datasets: dict[str, Dataset],
    rails_sheet: Sheet,
    chains_sheet: Sheet,
) -> None:
    """Declare the 2 L2 Exceptions drill parameters + their sentinel-
    pattern destination filter groups (M.3.10m).

    Mirrors the L1 dashboard's drill machinery (see
    ``_wire_drill_filter_groups`` in ``apps/l1_dashboard/app.py``):

    - 2 parameters with default ``__ALL__`` — when un-narrowed the
      sentinel short-circuits the calc field to ``'PASS'`` so the
      destination shows everything; on a real drill the calc returns
      ``'PASS'`` only for matching rows.
    - 2 calc fields (sentinel-or-match expression) — one on the
      postings dataset for Rails, one on the chain-instances dataset
      for Chains.
    - 2 filter groups, each ``CategoryFilter.with_literal(value='PASS')``
      against the calc field, scoped to the destination sheet.

    The literal filter pattern sidesteps QS's parameter-bound
    CategoryFilter empty-string narrowing bug — same workaround AR
    uses; same workaround L1 uses.
    """
    analysis.add_parameter(StringParam(
        name=P_L2FT_RAIL_DRILL,
        default=[_DRILL_RESET_SENTINEL],
    ))
    analysis.add_parameter(StringParam(
        name=P_L2FT_CHAIN_DRILL,
        default=[_DRILL_RESET_SENTINEL],
    ))

    ds_postings = datasets[DS_POSTINGS]
    ds_chain_instances = datasets[DS_CHAIN_INSTANCES]

    rail_calc = analysis.add_calc_field(CalcField(
        name="_drill_pass_pL2ftRailDrill_on_postings",
        dataset=ds_postings,
        expression=(
            f"ifelse(${{{P_L2FT_RAIL_DRILL}}} = '{_DRILL_RESET_SENTINEL}', "
            f"'PASS', "
            f"ifelse({{rail_name}} = ${{{P_L2FT_RAIL_DRILL}}}, "
            f"'PASS', 'FAIL'))"
        ),
    ))
    fg_rail = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-drill-rail-on-postings",  # type: ignore[arg-type]
        cross_dataset="SINGLE_DATASET",
        filters=[CategoryFilter.with_literal(
            filter_id="filter-fg-l2ft-drill-rail-on-postings",
            dataset=ds_postings,
            column=rail_calc,
            value="PASS",
            null_option="NON_NULLS_ONLY",
        )],
    ))
    fg_rail.scope_sheet(rails_sheet)

    chain_calc = analysis.add_calc_field(CalcField(
        name="_drill_pass_pL2ftChainDrill_on_chain_instances",
        dataset=ds_chain_instances,
        expression=(
            f"ifelse(${{{P_L2FT_CHAIN_DRILL}}} = '{_DRILL_RESET_SENTINEL}', "
            f"'PASS', "
            f"ifelse({{parent_chain_name}} = ${{{P_L2FT_CHAIN_DRILL}}}, "
            f"'PASS', 'FAIL'))"
        ),
    ))
    fg_chain = analysis.add_filter_group(FilterGroup(
        filter_group_id="fg-l2ft-drill-chain-on-instances",  # type: ignore[arg-type]
        cross_dataset="SINGLE_DATASET",
        filters=[CategoryFilter.with_literal(
            filter_id="filter-fg-l2ft-drill-chain-on-instances",
            dataset=ds_chain_instances,
            column=chain_calc,
            value="PASS",
            null_option="NON_NULLS_ONLY",
        )],
    ))
    fg_chain.scope_sheet(chains_sheet)


def _l2ft_drill(
    *,
    target_sheet: Sheet,
    name: str,
    writes: list,
    trigger: str = "DATA_POINT_MENU",
) -> Drill:
    """L2FT cross-sheet drill helper. Mirrors L1's `_l1_drill`: any
    drill param the caller doesn't write gets a DrillResetSentinel
    so a prior drill's value can't leak across navigations.
    """
    written = {param.name for param, _ in writes}
    full_writes = list(writes)
    for param in _L2FT_DRILL_RESET_PARAMS:
        if param.name not in written:
            full_writes.append((param, DrillResetSentinel()))
    return Drill(
        target_sheet=target_sheet,
        writes=full_writes,
        name=name,
        trigger=trigger,  # type: ignore[arg-type]
    )


def _populate_l2_exceptions_sheet(
    cfg: Config,
    sheet: Sheet,
    *,
    datasets: dict[str, Dataset],
    rails_sheet: Sheet,
    chains_sheet: Sheet,
    theme: ThemePreset,
) -> None:
    """L2 Exceptions sheet — unified violation view (M.3.10l rewrite
    of M.3.7).

    Mirrors L1's Today's Exceptions pattern: one KPI (total count),
    one bar chart (by check_type), one detail table (sorted by
    magnitude DESC). All six L2 hygiene checks (Chain Orphans,
    Unmatched Transfer Type, Dead Rails, Dead Bundles Activity,
    Dead Metadata, Dead Limit Schedules) UNION into one
    `unified-exceptions` dataset; the `check_type` discriminator
    column drives the bar chart breakout + the table's left-most
    grouping column.

    Pre-M.3.10l this sheet had 6 vertically-stacked sections
    (header text-box + 2 KPIs + table per check) that totaled ~144
    rows of vertical scroll; the unified view fits in one screen and
    matches the L1 dashboard's familiar shape.
    """
    accent = theme.accent

    del accent  # unused after the M.3.10l rewrite — kept the lookup
                # so a future legend / KPI tint can pick it up cheaply.
    ds = datasets[DS_UNIFIED_L2_EXCEPTIONS]

    # Row 1 — KPI (narrow, left) + bar chart (wide, right). KPI sits
    # next to the bar chart so the headline number reads alongside
    # the breakdown rather than dominating its own row.
    top_row = sheet.layout.row(height=10)
    top_row.add_kpi(
        width=12,
        title="Open L2 Violations",
        subtitle=(
            "Total count across all six L2 hygiene checks."
        ),
        values=[ds["check_type"].count()],
    )
    top_row.add_bar_chart(
        width=24,
        title="L2 Violations by Check Type",
        subtitle=(
            "Count per L2 hygiene check. A spike in one kind points "
            "at a recurring class of declaration-vs-runtime drift to "
            "investigate first."
        ),
        category=[ds["check_type"].dim()],
        values=[ds["check_type"].count()],
        category_label="Check Type",
        value_label="Open Violations",
        orientation="HORIZONTAL",
    )

    # Row 2 — detail table. Right-click any row's entity_a to drill
    # into the source. Both menu items appear on every row regardless
    # of check_type; pick the one that matches the row's subject
    # (e.g., "View in Rails" for Dead Rails / Dead Metadata; "View
    # in Chains" for Chain Orphans). Rows whose entity_a isn't a rail
    # or chain parent (Unmatched Transfer Type, Dead Limit Schedules)
    # land an empty destination — clear "this drill doesn't apply"
    # signal.
    magnitude_col = ds["magnitude"].numerical(currency=True)
    entity_a_col = ds["entity_a"].dim()
    sheet.layout.row(height=14).add_table(
        width=36,
        title="L2 Violation Detail",
        subtitle=(
            "Every row is one detected L2 violation. Sorted by "
            "magnitude (largest first). Right-click any row to drill "
            "into Rails (entity_a → Rail filter) or Chains (entity_a → "
            "Chain filter). Read entity_a / entity_b / detail in the "
            "context of the row's check_type — see the sheet "
            "description above for which fields each check populates."
        ),
        columns=[
            ds["check_type"].dim(),
            entity_a_col,
            ds["entity_b"].dim(),
            ds["detail"].dim(),
            magnitude_col,
        ],
        sort_by=(magnitude_col, "DESC"),
        actions=[
            _l2ft_drill(
                target_sheet=rails_sheet,
                name="View in Rails (filter rail_name to entity_a)",
                writes=[(_DP_RAIL_DRILL, entity_a_col)],
                trigger="DATA_POINT_MENU",
            ),
            _l2ft_drill(
                target_sheet=chains_sheet,
                name="View in Chains (filter parent_chain_name to entity_a)",
                writes=[(_DP_CHAIN_DRILL, entity_a_col)],
                trigger="DATA_POINT_MENU",
            ),
        ],
    )


def _populate_placeholder(
    cfg: Config,
    sheet: Sheet,
    *,
    title: str,
    body: str,
    substep: str,
    text_box_id: str,
    theme: ThemePreset,
) -> None:
    """Stub a placeholder sheet with the tab description + a 'lands at <substep>'
    note. Removed when the substep populator replaces this call."""
    accent = theme.accent
    sheet.layout.row(height=8).add_text_box(
        TextBox(
            text_box_id=text_box_id,
            content=rt.text_box(
                rt.inline(title, font_size="24px", color=accent),
                rt.BR, rt.BR,
                rt.markdown(body),
                rt.BR, rt.BR,
                rt.markdown(
                    f"(Skeleton at M.3.4 — visuals + datasets land at {substep}.)"
                ),
            ),
        ),
        width=36,
    )


# ---------------------------------------------------------------------------
# CLI / external-caller shims. Mirror the L1 dashboard signature so the CLI
# can plumb through generically.
# ---------------------------------------------------------------------------


def build_analysis(
    cfg: Config,
    *,
    l2_instance: L2Instance | None = None,
):
    """Build the complete L2 Flow Tracing Analysis resource via the tree."""
    return build_l2_flow_tracing_app(cfg, l2_instance=l2_instance).emit_analysis()


def build_l2_flow_tracing_dashboard(
    cfg: Config,
    *,
    l2_instance: L2Instance | None = None,
):
    """Build the L2 Flow Tracing Dashboard resource via the tree."""
    return build_l2_flow_tracing_app(cfg, l2_instance=l2_instance).emit_dashboard()
