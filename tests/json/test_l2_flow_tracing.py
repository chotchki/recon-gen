"""Tests for the L2 Flow Tracing app — phase M.3.4 skeleton.

The L2 Flow Tracing app is the second L2-fed app (after L1 dashboard).
Its job is to make every L2 primitive observable on a runtime
dashboard so analysts (and integrators) can spot 'L2 hygiene'
problems — declared rails with zero activity, declared chains with
broken parent-child firing, declared LimitSchedules that no flow
ever exercises.

M.3.4 ships the skeleton: 4 sheets (Getting Started + Rails +
Chains + L2 Exceptions), description-driven prose on Getting
Started, placeholder TextBox content on the other three sheets.
M.3.5+ populates each tab with its real visuals + datasets.

Tests here cover:

- Build pipeline shape (cfg + l2_instance plumb through).
- Analysis + Dashboard emit cleanly with the M.2d.3 prefix pattern.
- Default L2 instance auto-loads the persona-neutral spec_example
  fixture (M.3.2 repoint — production library code carries no
  Sasquatch flavor).
- 4 sheets in display order match the M.3.4 spec.
- Getting Started welcome uses ``l2_instance.description`` as the
  body (description-driven prose contract).
- M.3.4 CLI smoke: ``recon-gen generate l2-flow-tracing``
  writes the expected files.
- ``--all`` includes l2-flow-tracing in the bundle.
- Per-instance prefix isolation: changing the L2 instance changes
  the analysis ID + dashboard ID middle segment.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from recon_gen.common.l2 import default_l2_instance
from recon_gen.apps.l2_flow_tracing.app import (
    _CHAINS_NAME,
    _GETTING_STARTED_NAME,
    _GETTING_STARTED_TITLE,
    _L2_EXCEPTIONS_NAME,
    _RAILS_NAME,
    _TRANSFER_TEMPLATES_NAME,
    build_l2_flow_tracing_app,
)
from recon_gen.apps.l2_flow_tracing.datasets import (
    DS_CHAIN_INSTANCES,
    DS_META_VALUES,
    DS_POSTINGS,
    DS_TT_INSTANCES,
    DS_TT_LEGS,
    DS_UNIFIED_L2_EXCEPTIONS,
)
from recon_gen.cli import main
from recon_gen.cli._helpers import APPS
from recon_gen.common.l2 import load_instance
from recon_gen.common.models import DataSet
from recon_gen.common.sheets.app_info import (
    APP_INFO_SHEET_NAME,
    DS_APP_INFO_LIVENESS,
    DS_APP_INFO_MATVIEWS,
)
from recon_gen.common.tree import App, Sheet, TextBox
from recon_gen.common.tree.controls import (
    FilterControlLike, ParameterControlLike,
)
from tests._test_helpers import make_test_config


_CFG = make_test_config()


SASQUATCH_PR_YAML = (
    Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"
)


# *ControlLike Protocols don't expose ``title`` (every concrete subtype
# does). This helper narrows at the test layer — same pattern as
# ``test_l1_dashboard.py::_control_title``.
def _control_title(c: ParameterControlLike | FilterControlLike) -> str:
    return getattr(c, "title")


def _typed_column(col: Any) -> Any:
    """Narrow a ColumnRef (str | CalcField | Column) to its typed form.
    Bare strings are rejected here because every Table on the L2FT app
    uses ``ds["col"]`` references (no ``allow_bare_strings=True`` opt-in)."""
    from recon_gen.common.tree.datasets import Column
    assert isinstance(col, Column), (
        f"expected Column ref; got {type(col).__name__}"
    )
    return col


def _string_ds_params(params: list[Any]) -> dict[str, Any]:
    """Index the StringDatasetParameter on each DatasetParameter by Name —
    the dominant pattern in this file. Skips DateTime / Integer / Decimal
    variants (Phase BM added DateTimeDatasetParameter entries for the
    per-sheet date pickers); callers that want those can grep the same
    ``params`` list for the matching variant attribute."""
    out: dict[str, Any] = {}
    for p in params:
        sdp = p.StringDatasetParameter
        if sdp is None:
            continue
        out[sdp.Name] = sdp
    return out


def _ds_sql(aws_ds: DataSet) -> str:
    """Pull the CustomSql.SqlQuery off the first PhysicalTableMap entry
    of a built dataset, with the None-narrow built in. Every dataset we
    build here is CustomSql-shaped (``build_dataset`` always sets it),
    so the assert is a defensive type-narrow, not a behavioral guard."""
    cs = list(aws_ds.PhysicalTableMap.values())[0].CustomSql
    assert cs is not None
    return cs.SqlQuery


def _ds_columns(aws_ds: DataSet) -> list[Any]:
    """Pull the CustomSql.Columns off the first PhysicalTableMap entry —
    same None-narrow shape as ``_ds_sql``."""
    cs = list(aws_ds.PhysicalTableMap.values())[0].CustomSql
    assert cs is not None
    return list(cs.Columns)


def _sheet_by_name(app: App, name: str) -> Sheet:
    """Look up a Sheet by display name. Position-agnostic so sheet
    insertion order can be reshuffled without re-keying these tests."""
    assert app.analysis is not None
    for s in app.analysis.sheets:
        if s.name == name:
            return s
    raise AssertionError(
        f"sheet {name!r} missing — found {[s.name for s in app.analysis.sheets]}"
    )


# -- Build pipeline ----------------------------------------------------------


def test_build_with_default_loads_spec_example() -> None:
    """No kwarg → auto-load the persona-neutral spec_example L2 fixture
    (M.3.2 repointed default; production library code carries no
    implicit Sasquatch flavor)."""
    app = build_l2_flow_tracing_app(_CFG)
    assert app is not None
    assert app.name == "l2-flow-tracing"


def test_build_with_explicit_l2_instance_uses_caller_value() -> None:
    """Caller-supplied instance overrides the default."""
    explicit = default_l2_instance()
    app = build_l2_flow_tracing_app(_CFG, l2_instance=explicit)
    assert app is not None


def test_build_signature_l2_instance_is_kwarg_only() -> None:
    """Same convention as build_l1_dashboard_app: positional callers
    keep working without passing l2_instance; tests + alternative-persona
    deployments override via the kwarg."""
    sig = inspect.signature(build_l2_flow_tracing_app)
    p = sig.parameters.get("l2_instance")
    assert p is not None
    assert p.kind == inspect.Parameter.KEYWORD_ONLY
    assert p.default is None
    annot_str = str(p.annotation)
    assert "L2Instance" in annot_str


# -- Analysis + Dashboard registration ---------------------------------------


def test_analysis_registered_with_deployment_aware_name() -> None:
    """Z.C — the Analysis title surfaces ``cfg.deployment_name`` so
    multi-deploy QS accounts are distinguishable in the UI."""
    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    assert _CFG.deployment_name in app.analysis.name
    assert _GETTING_STARTED_TITLE in app.analysis.name


def test_dashboard_registered() -> None:
    app = build_l2_flow_tracing_app(_CFG)
    assert app.dashboard is not None


def test_emit_analysis_and_dashboard_succeed() -> None:
    """Tree validation passes — no orphan refs / shape errors."""
    app = build_l2_flow_tracing_app(_CFG)
    analysis = app.emit_analysis()
    dashboard = app.emit_dashboard()
    assert analysis is not None
    assert dashboard is not None


def test_analysis_id_uses_deployment_prefix() -> None:
    """Z.C — `<deployment_name>-l2-flow-tracing-analysis`. Default
    deployment_name is whatever ``make_test_config`` defaulted to
    (``recon-test``)."""
    app = build_l2_flow_tracing_app(_CFG)
    analysis = app.emit_analysis()
    assert analysis.AnalysisId == (
        f"{_CFG.deployment_name}-l2-flow-tracing-analysis"
    )


def test_dashboard_id_uses_deployment_prefix() -> None:
    app = build_l2_flow_tracing_app(_CFG)
    dashboard = app.emit_dashboard()
    assert dashboard.DashboardId == (
        f"{_CFG.deployment_name}-l2-flow-tracing"
    )


def test_per_deployment_prefix_isolates_resource_ids() -> None:
    """Z.C — two cfgs with distinct deployment_name → two non-colliding
    analysis IDs. Prevents multi-deploy collisions in the same QS account
    (replaces the prior per-L2-instance prefix isolation; deployments are
    now the per-tenant axis on cfg, not on the L2 yaml)."""
    cfg_a = make_test_config(deployment_name="recon-spec")
    cfg_b = make_test_config(deployment_name="recon-sasq")
    a_app = build_l2_flow_tracing_app(cfg_a, l2_instance=default_l2_instance())
    b_app = build_l2_flow_tracing_app(
        cfg_b, l2_instance=load_instance(SASQUATCH_PR_YAML),
    )
    a_id = a_app.emit_analysis().AnalysisId
    b_id = b_app.emit_analysis().AnalysisId
    assert a_id != b_id
    assert "recon-spec" in a_id
    assert "recon-sasq" in b_id


# -- Sheet structure (M.3.4 — 4 sheets) --------------------------------------


def test_six_sheets_in_display_order() -> None:
    """M.3.10f: Getting Started + Rails + Chains + Transfer Templates +
    L2 Exceptions. Position-stable — the order matches the L2-primitive
    type progression (the per-Rail explorer, the cross-Rail chain,
    the multi-Rail bundled Transfer, then hygiene exceptions). M.4.4.5
    appended the App Info ("i") canary as the last sheet."""
    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    assert [s.name for s in app.analysis.sheets] == [
        _GETTING_STARTED_NAME, _RAILS_NAME, _CHAINS_NAME,
        _TRANSFER_TEMPLATES_NAME, _L2_EXCEPTIONS_NAME, APP_INFO_SHEET_NAME,
    ]


def test_every_sheet_has_a_description() -> None:
    """Subtitle text drives the per-sheet prose — every sheet must
    have one (description-driven-prose contract from M.2a.7)."""
    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    for s in app.analysis.sheets:
        assert s.description, f"sheet {s.name!r} missing description"


def test_dataset_count_matches_populated_sheets() -> None:
    """M.3.10l stabilized at 6 fixed datasets per L2 instance:
    postings + meta-values (Rails), chain-instances (Chains),
    tt-instances + tt-legs (Transfer Templates), unified-exceptions
    (L2 Exceptions). M.3.10l replaced the 6 separate L2 exception
    datasets with one UNION-ALL dataset (mirrors L1's Today's
    Exceptions pattern: KPI + bar chart + unified detail table)."""
    app = build_l2_flow_tracing_app(_CFG)
    # M.4.4.5 added 2 App Info datasets to every shipped app.
    assert len(app.datasets) == 8
    assert {d.identifier for d in app.datasets} == {
        DS_POSTINGS,
        DS_META_VALUES,
        DS_CHAIN_INSTANCES,
        DS_TT_INSTANCES,
        DS_TT_LEGS,
        DS_UNIFIED_L2_EXCEPTIONS,
        DS_APP_INFO_LIVENESS,
        DS_APP_INFO_MATVIEWS,
    }


# -- Getting Started — description-driven prose (M.2a.2 contract) ------------


def test_getting_started_welcome_uses_l2_instance_description() -> None:
    """The welcome body comes from ``l2_instance.description``, NOT a
    hardcoded persona string. Switching L2 instance switches the
    prose — same contract the L1 dashboard's Getting Started follows."""
    app = build_l2_flow_tracing_app(_CFG)
    gs = _sheet_by_name(app, _GETTING_STARTED_NAME)
    welcome_xml = gs.text_boxes[0].content
    # Default L2 instance is spec_example — its description is what shows.
    assert "Generic SPEC-shaped instance" in welcome_xml


def test_getting_started_welcome_falls_back_when_l2_description_missing() -> None:
    """If the L2 instance has no top-level description, surface a
    hint to fill it rather than rendering blank — quicker debug."""
    from dataclasses import replace
    explicit = default_l2_instance()
    minimal = replace(explicit, description=None)
    app = build_l2_flow_tracing_app(_CFG, l2_instance=minimal)
    gs = _sheet_by_name(app, _GETTING_STARTED_NAME)
    assert "L2 instance description missing" in gs.text_boxes[0].content


def test_getting_started_title_is_constant_ui_vocabulary() -> None:
    """The title 'L2 Flow Tracing' is constant UI vocabulary (NOT
    pulled from L2). Per the M.2a.4 design note: titles stay
    hardcoded, subtitles + bodies pull from L2 descriptions."""
    app = build_l2_flow_tracing_app(_CFG)
    gs = _sheet_by_name(app, _GETTING_STARTED_NAME)
    assert _GETTING_STARTED_TITLE in gs.text_boxes[0].content


# -- Placeholder sheets — substep pointers (removed when populated) ----------


def test_no_remaining_placeholder_sheets() -> None:
    """M.3.7 lands the last populator (L2 Exceptions). No sheet
    should retain the M.3.4 'skeleton' placeholder marker — every
    sheet has its real visuals + prose now."""
    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    for s in app.analysis.sheets:
        body_blob = "".join(tb.content for tb in s.text_boxes)
        assert "Skeleton at M.3.4" not in body_blob, (
            f"sheet {s.name!r} still carries the M.3.4 placeholder marker"
        )


# -- CLI plumbing ------------------------------------------------------------


def test_l2_flow_tracing_in_apps_tuple() -> None:
    """The shared APPS tuple drives ``json apply``'s bundled emit and
    every cleanup/probe walk. Missing here means the L2 flow tracing
    JSON would silently disappear from the output set."""
    assert "l2-flow-tracing" in APPS


def test_cli_json_apply_l2_instance_flag(tmp_path: Path) -> None:
    """Z.C — `--l2 PATH` selects the L2 topology. The generated
    dataset filenames carry the cfg's deployment_name as the single
    prefix segment (collapsed from M.2d.3's
    `<resource_prefix>-<l2_prefix>-...` shape — the L2-instance
    segment is gone; deployment_name is operator-set per cfg)."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: recon-l2ft-l2flag\n"
        "db_table_prefix: sasquatch_pr\n"
        "datasource_arn: 'arn:aws:quicksight:us-west-2:111122223333:datasource/test-ds'\n"
    )
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(
        main, [
            "json", "apply",
            "-c", str(cfg_path),
            "-o", str(out_dir),
            "--l2", str(SASQUATCH_PR_YAML),
        ],
    )
    assert result.exit_code == 0, result.output
    # Dataset filenames carry the deployment_name from cfg, not the
    # L2 yaml stem.
    chain_inst = (
        out_dir / "datasets"
        / "recon-l2ft-l2flag-l2ft-chain-instances-dataset.json"
    )
    assert chain_inst.exists()


def test_cli_json_apply_l2_flow_tracing_writes_files(tmp_path: Path) -> None:
    """CLI smoke: ``recon-gen json apply`` writes theme + analysis
    + dashboard + every dataset under datasets/ for L2 flow tracing.
    M.3.10c — postings + meta-values replace the M.3.5 rails dataset
    + the M.3.8 per-key dropdown fan-out."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        # Z.C — required cfg fields.
        "deployment_name: recon-l2ft-cli\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: 'arn:aws:quicksight:us-west-2:111122223333:datasource/test-ds'\n"
    )
    out_dir = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(
        main, [
            "json", "apply",
            "-c", str(cfg_path),
            "-o", str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "theme.json").exists()
    assert (out_dir / "l2-flow-tracing-analysis.json").exists()
    assert (out_dir / "l2-flow-tracing-dashboard.json").exists()
    # Z.C — dataset JSONs use the deployment_name single-prefix shape
    # (was `qs-gen-<l2_prefix>-l2ft-...`).
    assert (
        out_dir / "datasets" / "recon-l2ft-cli-l2ft-postings-dataset.json"
    ).exists()
    assert (
        out_dir / "datasets"
        / "recon-l2ft-cli-l2ft-meta-values-dataset.json"
    ).exists()


# -- Rails sheet (M.3.10c — postings explorer + cascade) --------------------


def test_rails_sheet_has_a_table_visual() -> None:
    """The Rails sheet hosts the transactions Table (postings dataset)."""
    from recon_gen.common.tree import Table
    app = build_l2_flow_tracing_app(_CFG)
    rails = _sheet_by_name(app, _RAILS_NAME)
    table_visuals = [v for v in rails.visuals if isinstance(v, Table)]
    assert len(table_visuals) == 1


def test_rails_table_sources_from_postings_dataset() -> None:
    """The transactions Table reads from the postings dataset (not the
    M.3.5 declared-rails aggregate that moved to a future Docs tab)."""
    from recon_gen.common.tree import Table
    from recon_gen.common.tree.datasets import Column
    app = build_l2_flow_tracing_app(_CFG)
    rails = _sheet_by_name(app, _RAILS_NAME)
    table = next(v for v in rails.visuals if isinstance(v, Table))
    table_dataset_ids: set[str] = set()
    for c in table.columns:
        col = c.column
        # Bare-string ColumnRef has no dataset — skip; the table doesn't
        # opt into ``allow_bare_strings``, so this branch never fires in
        # practice (defensive).
        if isinstance(col, Column):
            table_dataset_ids.add(col.dataset.identifier)
    assert table_dataset_ids == {DS_POSTINGS}


def test_rails_sheet_has_seven_filter_controls() -> None:
    """X.1.g — the filter bar carries seven parameter-driven controls:
    2 date pickers + rail / status / bundle ParameterDropdowns +
    cascade key + value. Pre-X.1.g the rail / status / bundle trio were
    FilterDropdowns bound to FILTER_ALL_VALUES CategoryFilters; the
    rewrite moved them onto parameter-bound CategoryFilters with
    StaticValues source so QS doesn't lazy-fetch dropdown options."""
    app = build_l2_flow_tracing_app(_CFG)
    rails = _sheet_by_name(app, _RAILS_NAME)
    # 7 parameter controls (date×2 + rail + status + bundle + meta-key
    # + meta-value)
    assert len(rails.parameter_controls) == 7
    # 0 filter controls — every control is now parameter-driven.
    assert len(rails.filter_controls) == 0


def test_rails_sheet_parameter_controls_titled_for_analyst() -> None:
    """The analyst-facing titles match the filter-bar UX spec —
    catches accidental retitling."""
    app = build_l2_flow_tracing_app(_CFG)
    rails = _sheet_by_name(app, _RAILS_NAME)
    titles = {_control_title(ctrl) for ctrl in rails.parameter_controls}
    assert {
        "Date From", "Date To", "Rail", "Status", "Bundle",
        "Metadata Key", "Metadata Value",
    } <= titles


# -- Chains sheet (M.3.6) ----------------------------------------------------


def _chains_dataset_sql_against(yaml_path: Path) -> str:
    """Pull the SQL string out of the Chains dataset against a chosen
    L2 instance. Used by tests that want to assert non-empty CTEs
    (sasquatch_pr.yaml has 6 chains; spec_example has 1). The
    empty-chains CTE path is exercised separately with a synthesized
    chains-stripped instance.

    Z.C — the dataset SQL's matview names come from cfg.db_table_prefix
    (was previously stamped from the L2's `instance` field). Pin the
    cfg's prefix to the yaml stem so the assertions still find
    `<yaml_stem>_current_transactions` in the rendered SQL.
    """
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_chains_dataset,
    )
    from dataclasses import replace
    inst = load_instance(yaml_path)
    cfg = replace(_CFG, db_table_prefix=yaml_path.stem)
    aws_ds = build_chains_dataset(cfg, inst)
    table = list(aws_ds.PhysicalTableMap.values())[0]
    assert table.CustomSql is not None
    return table.CustomSql.SqlQuery


def test_chains_dataset_targets_prefixed_current_transactions() -> None:
    """Chains runtime joins reference the prefixed current_transactions
    matview — `<prefix>_current_transactions`."""
    sql = _chains_dataset_sql_against(SASQUATCH_PR_YAML)
    assert "FROM sasquatch_pr_current_transactions" in sql


def test_chains_dataset_inlines_l2_chain_entries() -> None:
    """The declared edges CTE inlines every ChainEntry as a SQL
    string-literal SELECT row joined by N-1 UNION ALLs.
    sasquatch_pr.yaml has 6 chains; the empty-chains path is
    exercised in another test with a synthesized instance."""
    inst = load_instance(SASQUATCH_PR_YAML)
    sql = _chains_dataset_sql_against(SASQUATCH_PR_YAML)
    assert "WITH declared AS" in sql
    # Z.A: each Chain row contributes one CTE row per child; assert
    # both endpoints serialize.
    total_rows = 0
    for c in inst.chains:
        assert f"'{c.parent}'" in sql
        for child in c.children:
            assert f"'{child.name}'" in sql
        total_rows += len(c.children)
    # UNION ALL count = (per-child rows) - 1.
    assert sql.count("UNION ALL") == max(0, total_rows - 1)


def test_chains_dataset_emits_required_optional_labels() -> None:
    """The 'required' column in the dataset is emitted as the
    display-friendly 'Required' / 'Optional' labels (not boolean
    literals) so the visual reads cleanly. Z.A: singleton-children
    rows render as 'Required'; multi-children siblings render as
    'Optional'."""
    sql = _chains_dataset_sql_against(SASQUATCH_PR_YAML)
    inst = load_instance(SASQUATCH_PR_YAML)
    has_singleton = any(len(c.children) == 1 for c in inst.chains)
    has_multi = any(len(c.children) >= 2 for c in inst.chains)
    if has_singleton:
        assert "'Required'" in sql
    if has_multi:
        assert "'Optional'" in sql


def test_chains_dataset_xor_group_emits_null_for_singleton_rows() -> None:
    """Z.A: singleton-children Chain rows serialize NULL in the
    xor_group slot (no XOR alternation); multi-children rows
    serialize the row's composite key as the group identifier.
    Visuals can then treat NULL as 'no XOR group' explicitly."""
    inst = load_instance(SASQUATCH_PR_YAML)
    has_singleton = any(len(c.children) == 1 for c in inst.chains)
    assert has_singleton, "test fixture lost its singleton-children rows"
    sql = _chains_dataset_sql_against(SASQUATCH_PR_YAML)
    # At least one CTE row must have NULL in the xor_group slot.
    assert "NULL AS xor_group" in sql


def test_chains_dataset_orphan_rate_clamps_at_zero() -> None:
    """Orphan count uses GREATEST(...,  0) so child-fires-more-than-
    parent doesn't go negative — non-intuitive in a Sankey legend."""
    sql = _chains_dataset_sql_against(SASQUATCH_PR_YAML)
    assert "GREATEST(e.parent_firing_count - e.child_firing_count, 0)" in sql


def test_chains_dataset_orphan_rate_avoids_divide_by_zero() -> None:
    """Dead parent (zero firings) → orphan_rate of 0 instead of NaN
    or a divide-by-zero error. CASE guards the division."""
    sql = _chains_dataset_sql_against(SASQUATCH_PR_YAML)
    assert "WHEN e.parent_firing_count > 0" in sql
    assert "ELSE 0" in sql


def test_chains_dataset_contract_columns_match_builder() -> None:
    """Contract columns and SQL projection match — visual ds["col"]
    references resolve cleanly."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        CHAINS_CONTRACT, build_chains_dataset,
    )
    aws_ds = build_chains_dataset(_CFG, default_l2_instance())
    cols = {
        c.Name for c in _ds_columns(aws_ds)
    }
    expected = {c.name for c in CHAINS_CONTRACT.columns}
    assert cols == expected


def test_chains_dataset_handles_empty_chains_list() -> None:
    """An L2 instance with zero chains exercises the empty-CTE path
    (WHERE 1=0) — the SQL stays valid and the visual harmless. (Every
    bundled L2 declares at least one chain now, so synthesize the
    no-chains case by stripping ``chains`` off a loaded instance.)"""
    from dataclasses import replace

    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_chains_dataset,
    )
    no_chains = replace(default_l2_instance(), chains=())
    aws_ds = build_chains_dataset(_CFG, no_chains)
    sql = _ds_sql(aws_ds)
    assert "WHERE 1=0" in sql
    assert "WITH declared AS" in sql


def test_chains_dataset_id_uses_deployment_prefix() -> None:
    """Z.C — dataset ID is prefixed by `cfg.deployment_name`. Was
    previously the M.2d.3 two-segment shape with the L2 instance
    prefix as the middle segment; now collapsed to one prefix."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_chains_dataset,
    )
    from dataclasses import replace
    cfg = replace(
        _CFG,
        deployment_name="recon-sasq",
        db_table_prefix="sasquatch_pr",
    )
    ds = build_chains_dataset(cfg, load_instance(SASQUATCH_PR_YAML))
    assert ds.DataSetId == "recon-sasq-l2ft-chains-dataset"


# -- Chains sheet — M.3.10d per-instance explorer ---------------------------


def test_chains_sheet_is_a_single_table() -> None:
    """M.3.10d: Chains sheet is the per-parent-firing explorer — one
    Table backed by chain-instances. The pre-M.3.10d Sankey + edge
    detail moved to the M.7 docs render of declared topology.
    A brief M.3.10e Sankey-on-edges experiment (chain-edges dataset)
    was reverted: chains is a runtime causality concept, not a
    multi-leg flow graph — Sankey doesn't read naturally for it.
    Multi-leg Sankey visualization belongs on TransferTemplates."""
    from recon_gen.common.tree import Table
    app = build_l2_flow_tracing_app(_CFG)
    chains = _sheet_by_name(app, _CHAINS_NAME)
    table_visuals = [v for v in chains.visuals if isinstance(v, Table)]
    assert len(table_visuals) == 1
    # Table reads from chain-instances, not the aggregate chains dataset.
    table = table_visuals[0]
    assert _typed_column(table.columns[0].column).dataset.identifier == DS_CHAIN_INSTANCES


def test_chains_table_carries_completion_status_column() -> None:
    """The completion_status column is the answer the explorer is
    built around — without it the Completion filter has nothing to
    visibly affect."""
    from recon_gen.common.tree import Table
    app = build_l2_flow_tracing_app(_CFG)
    chains = _sheet_by_name(app, _CHAINS_NAME)
    table = next(v for v in chains.visuals if isinstance(v, Table))
    cols = {_typed_column(c.column).name for c in table.columns}
    assert "completion_status" in cols
    assert "parent_chain_name" in cols
    assert "parent_transfer_id" in cols


def test_chains_sheet_has_six_filter_controls() -> None:
    """Filter bar shape matches Rails: 2 datetime pickers, 2 filter
    dropdowns (Chain + Completion), 2 parameter dropdowns (Metadata
    Key + Metadata Value)."""
    app = build_l2_flow_tracing_app(_CFG)
    chains = _sheet_by_name(app, _CHAINS_NAME)
    titles: list[str] = (
        [_control_title(c) for c in chains.parameter_controls]
        + [_control_title(c) for c in chains.filter_controls]
    )
    assert set(titles) == {
        "Date From", "Date To", "Chain", "Completion",
        "Metadata Key", "Metadata Value",
    }


def test_chains_metadata_params_are_chain_scoped() -> None:
    """Chains uses its own pL2ftChainsMeta{Key,Value} params — separate
    from Rails' pL2ftMeta{Key,Value} so per-sheet selection doesn't
    bleed across tabs."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        P_L2FT_CHAINS_DATE_END,
        P_L2FT_CHAINS_DATE_START,
    )

    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    param_names = {str(p.name) for p in app.analysis.parameters}
    assert "pL2ftChainsMetaKey" in param_names
    assert "pL2ftChainsMetaValue" in param_names
    # And the date params are independent too — chains has its own.
    assert P_L2FT_CHAINS_DATE_START in param_names
    assert P_L2FT_CHAINS_DATE_END in param_names


def test_chain_instances_dataset_declares_cascade_and_pushdown_parameters() -> None:
    """Y.2.d + AA.A.3 — four dataset params: the metadata cascade pair
    (pKey / pValues) plus the chain / completion pushdown pair
    (pL2ftChainsChain / pL2ftChainsCompletion). AA.A.3 flipped both
    pushdown params from MULTI to SINGLE per the drill-to-one default;
    both now default to ``[L2FT_ALL_SENTINEL]`` and the SQL
    ``_match_all_in_clause`` turns the sentinel into "match all" on
    load. An instance with zero chains lands on the same sentinel
    default — harmless, the chain-instances table is empty anyway."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        L2FT_ALL_SENTINEL, build_chain_instances_dataset,
        declared_chain_parents,
    )
    # Instance WITH chains.
    inst = load_instance(SASQUATCH_PR_YAML)
    assert declared_chain_parents(inst)  # sasquatch_pr declares chains
    from recon_gen.apps.l2_flow_tracing.datasets import (
        P_L2FT_CHAINS_DATE_END,
        P_L2FT_CHAINS_DATE_START,
    )

    params = build_chain_instances_dataset(_CFG, inst).DatasetParameters
    # Phase BM — 4 pre-BM (pKey/pValues + pL2ftChainsChain/Completion)
    # + 2 BM date params (pL2ftChainsDateStart/End) = 6.
    assert params is not None and len(params) == 6
    by_name = _string_ds_params(params)
    assert by_name["pKey"].ValueType == "SINGLE_VALUED"
    assert by_name["pValues"].ValueType == "SINGLE_VALUED"
    assert by_name["pL2ftChainsChain"].ValueType == "SINGLE_VALUED"
    assert by_name["pL2ftChainsChain"].DefaultValues.StaticValues == [L2FT_ALL_SENTINEL]
    assert by_name["pL2ftChainsCompletion"].ValueType == "SINGLE_VALUED"
    assert by_name["pL2ftChainsCompletion"].DefaultValues.StaticValues == [
        L2FT_ALL_SENTINEL,
    ]
    # BM date params declared (defaults asserted via DateTime variant).
    date_param_names = {
        p.DateTimeDatasetParameter.Name
        for p in params if p.DateTimeDatasetParameter is not None
    }
    assert P_L2FT_CHAINS_DATE_START in date_param_names
    assert P_L2FT_CHAINS_DATE_END in date_param_names
    # Instance WITHOUT chains → same sentinel default.
    from dataclasses import replace
    no_chains = replace(load_instance(SASQUATCH_PR_YAML), chains=())
    assert not declared_chain_parents(no_chains)
    nc_params = build_chain_instances_dataset(_CFG, no_chains).DatasetParameters
    assert nc_params is not None
    nc_by_name = _string_ds_params(nc_params)
    assert nc_by_name["pL2ftChainsChain"].DefaultValues.StaticValues == [
        L2FT_ALL_SENTINEL,
    ]


def test_chain_instances_dataset_pushes_chain_completion_into_sql() -> None:
    """Y.2.d + AA.A.3 — chain + completion narrow inside the
    chain-instances dataset SQL via single-valued `<<$param>>`
    substitution (post-AA.A.3 SINGLE_SELECT form); the projection wraps
    in a subquery so the CASE-aliased `completion_status` is visible to
    the outer WHERE. Metadata cascade on `parent_metadata` stays inner."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_chain_instances_dataset,
    )
    sql = _ds_sql(
        build_chain_instances_dataset(_CFG, load_instance(SASQUATCH_PR_YAML))
    )
    assert "parent_chain_name = <<$pL2ftChainsChain>>" in sql
    assert "completion_status = <<$pL2ftChainsCompletion>>" in sql
    # AA.A.3 — chain predicate is the SINGLE_VALUED sentinel-guarded form
    # (`('__l2ft_all__' = <<$p>> OR parent_chain_name = <<$p>>)`) in the
    # OUTER WHERE over the CASE-aliased subquery.
    assert (
        ") chain_instances\nWHERE ('__l2ft_all__' = <<$pL2ftChainsChain>>"
        in sql
    )
    # Metadata cascade still present on parent_metadata, inside the subquery.
    assert "JSON_VALUE(parent_metadata," in sql


def test_chains_pushdown_params_bridge_to_chain_instances_dataset() -> None:
    """Y.2.d — the Chain / Completion analysis params each bridge to
    their namesake dataset parameter on the chain-instances dataset
    (and nothing else); no `fg-l2ft-chains-{chain,completion}`
    FilterGroups remain."""
    from recon_gen.common.tree import StringParam

    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    for pname in ("pL2ftChainsChain", "pL2ftChainsCompletion"):
        p = next(p for p in app.analysis.parameters if str(p.name) == pname)
        assert isinstance(p, StringParam)
        assert p.mapped_dataset_params is not None
        assert {
            (ds.identifier, name) for ds, name in p.mapped_dataset_params
        } == {(DS_CHAIN_INSTANCES, pname)}
    fg_ids = {str(fg.filter_group_id) for fg in app.analysis.filter_groups}
    assert not (fg_ids & {"fg-l2ft-chains-chain", "fg-l2ft-chains-completion"})


def test_metadata_value_control_is_text_field() -> None:
    """X.1.b regression guard — the Metadata Value control MUST be a
    ``ParameterTextField``, NOT a ``ParameterDropdown``.

    Pre-X.1.b the Value dropdown carried a ``LinkedValues`` source on
    the meta-values dataset. QS's lazy "sample values" fetch on cold
    per-CI-run dashboards threw ``[pageerror] Sample values not
    found`` in the JS console, which stranded the Transactions table
    empty despite the matview being populated (~530 rows). Free-text
    input has no sample-values fetch path — sidesteps the failure
    entirely. Tradeoff: analyst types the literal value to filter on
    (no enumeration of valid options) — acceptable since the
    LinkedValues cascade was already dropped in v8.6.5 (the
    Value dropdown showed every declared value regardless of picked
    Key anyway).

    Asserted on every L2FT sheet that exposed the metadata cascade
    so a future regression to ParameterDropdown can't silently
    re-introduce the cold-deploy failure mode.
    """
    from recon_gen.common.tree import ParameterTextField
    app = build_l2_flow_tracing_app(_CFG)
    found_at_least_one = False
    for sheet_name in ("Rails", "Chains", "Transfer Templates"):
        sheet = _sheet_by_name(app, sheet_name)
        try:
            value_ctrl = next(
                c for c in sheet.parameter_controls
                if _control_title(c) == "Metadata Value"
            )
        except StopIteration:
            continue
        found_at_least_one = True
        assert isinstance(value_ctrl, ParameterTextField), (
            f"{sheet_name} sheet: Metadata Value control is "
            f"{type(value_ctrl).__name__}, expected ParameterTextField. "
            f"Reverting to ParameterDropdown re-introduces the X.1.b "
            f"'Sample values not found' failure on cold per-CI-run "
            f"deploys."
        )
    assert found_at_least_one, (
        "No Metadata Value control found on Rails / Chains / "
        "Transfer Templates — the test can't have regressed silently."
    )


def test_tt_datasets_declare_cascade_and_pushdown_parameters() -> None:
    """Y.2.e + AA.A.3 — both TransferTemplates datasets (tt-instances +
    tt-legs) carry the same 4 params: the metadata cascade pair (pKey /
    pValues) plus the template / completion pushdown pair (both
    SINGLE_VALUED post-AA.A.3). Template / completion both default to
    ``[L2FT_ALL_SENTINEL]`` turned into "match all" by the SQL
    ``_match_all_in_clause``. An instance with zero templates lands on
    the same sentinel default."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        L2FT_ALL_SENTINEL,
        P_L2FT_TT_DATE_END,
        P_L2FT_TT_DATE_START,
        build_tt_instances_dataset,
        build_tt_legs_dataset, declared_template_names,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    for build in (build_tt_instances_dataset, build_tt_legs_dataset):
        params = build(_CFG, inst).DatasetParameters
        # Phase BM — 4 pre-BM (pKey/pValues + Template/Completion) + 2
        # BM date params (pL2ftTtDateStart/End) = 6.
        assert params is not None and len(params) == 6, build.__name__
        by_name = _string_ds_params(params)
        assert by_name["pKey"].ValueType == "SINGLE_VALUED"
        assert by_name["pValues"].ValueType == "SINGLE_VALUED"
        assert by_name["pL2ftTtTemplate"].ValueType == "SINGLE_VALUED"
        assert by_name["pL2ftTtTemplate"].DefaultValues.StaticValues == [
            L2FT_ALL_SENTINEL,
        ]
        assert by_name["pL2ftTtCompletion"].ValueType == "SINGLE_VALUED"
        assert by_name["pL2ftTtCompletion"].DefaultValues.StaticValues == [
            L2FT_ALL_SENTINEL,
        ]
        date_param_names = {
            p.DateTimeDatasetParameter.Name
            for p in params if p.DateTimeDatasetParameter is not None
        }
        assert P_L2FT_TT_DATE_START in date_param_names, build.__name__
        assert P_L2FT_TT_DATE_END in date_param_names, build.__name__
    # Empty-templates instance → same sentinel default.
    from dataclasses import replace
    no_tt = replace(inst, transfer_templates=[])
    assert not declared_template_names(no_tt)
    params = build_tt_instances_dataset(_CFG, no_tt).DatasetParameters
    assert params is not None
    by_name = _string_ds_params(params)
    assert by_name["pL2ftTtTemplate"].DefaultValues.StaticValues == [
        L2FT_ALL_SENTINEL,
    ]


def test_tt_datasets_push_template_completion_into_sql() -> None:
    """Y.2.e + AA.A.3 — both TT datasets narrow on template / completion
    inside the dataset SQL via single-valued `<<$param>>` substitution
    (post-AA.A.3 SINGLE_SELECT form); the final SELECT (and the UNION-ALL
    for tt-legs) wraps in a subquery so the CASE-aliased
    `completion_status` is visible to the outer WHERE. Metadata cascade
    stays inner."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_tt_instances_dataset, build_tt_legs_dataset,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    inst_sql = _ds_sql(build_tt_instances_dataset(_CFG, inst))
    legs_sql = _ds_sql(build_tt_legs_dataset(_CFG, inst))
    for sql, alias in ((inst_sql, "tt_instances"), (legs_sql, "tt_legs")):
        # AA.A.3 — template predicate is the SINGLE_VALUED sentinel-guarded
        # form in the OUTER WHERE over the CASE-aliased subquery.
        assert (
            f") {alias}\nWHERE ('__l2ft_all__' = <<$pL2ftTtTemplate>>"
            in sql
        )
        assert "template_name = <<$pL2ftTtTemplate>>" in sql
        assert "completion_status = <<$pL2ftTtCompletion>>" in sql
        # Metadata cascade still present, inside the subquery.
        assert "<<$pKey>>" in sql and "<<$pValues>>" in sql
    # tt-legs keeps the two-branch UNION ALL inside the wrapper.
    assert legs_sql.count("UNION ALL") >= 1
    assert "FROM template_legs" in legs_sql and "FROM chain_edges" in legs_sql


def test_tt_pushdown_params_bridge_to_both_datasets() -> None:
    """Y.2.e — the Template / Completion analysis params each bridge to
    their namesake param on BOTH tt-instances AND tt-legs (so the Table
    and the Sankey narrow together); no `fg-l2ft-tt-{template,completion}`
    FilterGroups remain."""
    from recon_gen.common.tree import StringParam

    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    for pname in ("pL2ftTtTemplate", "pL2ftTtCompletion"):
        p = next(p for p in app.analysis.parameters if str(p.name) == pname)
        assert isinstance(p, StringParam)
        assert p.mapped_dataset_params is not None
        assert {
            (ds.identifier, name) for ds, name in p.mapped_dataset_params
        } == {(DS_TT_INSTANCES, pname), (DS_TT_LEGS, pname)}
    fg_ids = {str(fg.filter_group_id) for fg in app.analysis.filter_groups}
    assert not (fg_ids & {"fg-l2ft-tt-template", "fg-l2ft-tt-completion"})


# -- L2 Exceptions sheet (M.3.7) ---------------------------------------------


_EXC_DATASETS = (
    ("l2ft-exc-chain-orphans-ds", "build_exc_chain_orphans_dataset"),
    ("l2ft-exc-unmatched-rail-name-ds",
     "build_exc_unmatched_rail_name_dataset"),
    ("l2ft-exc-dead-rails-ds", "build_exc_dead_rails_dataset"),
    ("l2ft-exc-dead-bundles-activity-ds",
     "build_exc_dead_bundles_activity_dataset"),
    ("l2ft-exc-dead-metadata-ds", "build_exc_dead_metadata_dataset"),
    ("l2ft-exc-dead-limit-schedules-ds",
     "build_exc_dead_limit_schedules_dataset"),
)


def _exc_dataset_sql(builder_name: str, yaml_path: Path) -> str:
    """Z.C — pin cfg.db_table_prefix to the yaml stem so the rendered
    SQL references `<yaml_stem>_current_transactions`."""
    import recon_gen.apps.l2_flow_tracing.datasets as ds_mod
    from dataclasses import replace
    inst = load_instance(yaml_path)
    cfg = replace(_CFG, db_table_prefix=yaml_path.stem)
    builder = getattr(ds_mod, builder_name)
    aws_ds: DataSet = builder(cfg, inst)
    return _ds_sql(aws_ds)


@pytest.mark.parametrize("ds_id,builder_name", _EXC_DATASETS)
def test_exc_dataset_targets_prefixed_current_transactions(
    ds_id: str, builder_name: str,
) -> None:
    """Every L2 Exceptions dataset queries `<prefix>_current_transactions`
    so the supersession-aware ('latest entry per id') view drives the
    runtime side. The CTE may also reference the prefix; the broader
    check is that the target table name appears at least once."""
    sql = _exc_dataset_sql(builder_name, SASQUATCH_PR_YAML)
    assert "sasquatch_pr_current_transactions" in sql, (
        f"{builder_name} doesn't reference the prefixed transactions matview"
    )


@pytest.mark.parametrize("ds_id,builder_name", _EXC_DATASETS)
def test_exc_dataset_id_uses_deployment_prefix(
    ds_id: str, builder_name: str,
) -> None:
    """Z.C — every exception dataset's ID is prefixed by
    ``cfg.deployment_name`` so multi-deploy collisions don't happen
    in the same QS account."""
    import recon_gen.apps.l2_flow_tracing.datasets as ds_mod
    from dataclasses import replace
    cfg = replace(
        _CFG,
        deployment_name="recon-sasq",
        db_table_prefix="sasquatch_pr",
    )
    builder = getattr(ds_mod, builder_name)
    aws_ds = builder(cfg, load_instance(SASQUATCH_PR_YAML))
    assert aws_ds.DataSetId.startswith("recon-sasq-l2ft-exc-"), (
        f"{builder_name} dataset ID lacks prefix: {aws_ds.DataSetId}"
    )


def test_exc_chain_orphans_filters_required_only() -> None:
    """L2.1 surfaces ONLY required orphans. Optional chain entries
    with unmatched children are by-design (XOR groups, optional
    follow-ons) — they don't constitute violations."""
    sql = _exc_dataset_sql(
        "build_exc_chain_orphans_dataset", SASQUATCH_PR_YAML,
    )
    assert "WHERE e.required = 'Required'" in sql


def test_exc_unmatched_rail_name_excludes_declared() -> None:
    """L2.2 LEFT JOINs on declared rails and filters to the unmatched
    side (NULL after join). All declared rail names appear as
    SELECT-literal rows in the declared_types CTE."""
    sql = _exc_dataset_sql(
        "build_exc_unmatched_rail_name_dataset", SASQUATCH_PR_YAML,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    declared_types = {str(r.name) for r in inst.rails}
    for t in declared_types:
        assert f"'{t}'" in sql
    assert "LEFT JOIN declared_types" in sql
    assert "WHERE d.rail_name IS NULL" in sql


def test_exc_dead_rails_filters_zero_postings_only() -> None:
    """L2.3 filters to ``COALESCE(r.total_postings, 0) = 0``. A LEFT
    JOIN preserves Rails with no matching runtime activity at all."""
    sql = _exc_dataset_sql(
        "build_exc_dead_rails_dataset", SASQUATCH_PR_YAML,
    )
    assert "COALESCE(r.total_postings, 0) = 0" in sql


def test_exc_dead_bundles_activity_checks_both_attributions() -> None:
    """L2.4: bundles_activity refs name a Rail.name (post-Z.B); the
    SQL's NOT EXISTS checks the rail_name match to surface zero-runtime
    bundles_activity targets."""
    sql = _exc_dataset_sql(
        "build_exc_dead_bundles_activity_dataset", SASQUATCH_PR_YAML,
    )
    assert "t.rail_name = db.bundle_target" in sql
    assert "t.rail_name = db.bundle_target" in sql


def test_exc_dead_metadata_uses_static_json_paths() -> None:
    """L2.5 emits one NOT EXISTS fragment per (rail, metadata_key)
    with a static `$.<key>` JSONPath — keeps the SQL portable per
    the project's no-JSONB constraint (PG's JSON_VALUE prefers
    constant paths)."""
    sql = _exc_dataset_sql(
        "build_exc_dead_metadata_dataset", SASQUATCH_PR_YAML,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    declared_keys = {
        (str(r.name), str(k))
        for r in inst.rails for k in r.metadata_keys
    }
    if declared_keys:
        # At least one fragment per declared (rail, key) — checks
        # the literal '$.key' substring shows up.
        for _, key in declared_keys:
            assert f"'$.{key}'" in sql
        assert sql.count("JSON_VALUE(t.metadata,") == len(declared_keys)


def test_exc_dead_limit_schedules_filters_outbound_debit() -> None:
    """L2.6 only counts a LimitSchedule cell as 'used' if there's
    outbound DEBIT flow against the parent_role + rail_name (Z.B
    subsumed transfer_type into rail). A cap on inbound flow doesn't
    make sense; matching credit-only flow would give a false 'alive'
    signal."""
    sql = _exc_dataset_sql(
        "build_exc_dead_limit_schedules_dataset", SASQUATCH_PR_YAML,
    )
    assert "AND t.amount_direction = 'Debit'" in sql


@pytest.mark.parametrize("ds_id,builder_name", _EXC_DATASETS)
def test_exc_dataset_contract_columns_match_builder(
    ds_id: str, builder_name: str,
) -> None:
    """Every exception dataset's contract columns match its SQL
    projection — visual ds["col"] references resolve cleanly."""
    import recon_gen.apps.l2_flow_tracing.datasets as ds_mod
    contract_name_map = {
        "l2ft-exc-chain-orphans-ds": "EXC_CHAIN_ORPHANS_CONTRACT",
        "l2ft-exc-unmatched-rail-name-ds":
            "EXC_UNMATCHED_RAIL_NAME_CONTRACT",
        "l2ft-exc-dead-rails-ds": "EXC_DEAD_RAILS_CONTRACT",
        "l2ft-exc-dead-bundles-activity-ds":
            "EXC_DEAD_BUNDLES_ACTIVITY_CONTRACT",
        "l2ft-exc-dead-metadata-ds": "EXC_DEAD_METADATA_CONTRACT",
        "l2ft-exc-dead-limit-schedules-ds":
            "EXC_DEAD_LIMIT_SCHEDULES_CONTRACT",
    }
    contract = getattr(ds_mod, contract_name_map[ds_id])
    builder = getattr(ds_mod, builder_name)
    aws_ds = builder(_CFG, load_instance(SASQUATCH_PR_YAML))
    cols = {
        c.Name for c in _ds_columns(aws_ds)
    }
    expected = {c.name for c in contract.columns}
    assert cols == expected


def test_exceptions_sheet_unified_shape() -> None:
    """M.3.10l: L2 Exceptions sheet is a single KPI + bar chart +
    detail table backed by one unified-exceptions dataset (mirrors
    L1's Today's Exceptions). The pre-M.3.10l 6-sections × (2 KPI +
    1 Table) layout (12 KPIs + 6 Tables ~= 144 rows of vertical
    scroll) collapses to one screen-sized view."""
    from collections import Counter
    app = build_l2_flow_tracing_app(_CFG)
    exc = _sheet_by_name(app, _L2_EXCEPTIONS_NAME)
    counts = Counter(type(v).__name__ for v in exc.visuals)
    assert counts == Counter(["KPI", "BarChart", "Table"]), (
        f"unexpected visual mix: {counts}"
    )


def test_exceptions_sheet_visuals_read_unified_dataset() -> None:
    """Every visual on the L2 Exceptions sheet reads from the unified
    dataset — catches accidental wiring back to a sub-dataset that
    isn't in the deployed dataset list anymore."""
    from recon_gen.common.tree import BarChart, KPI, Table
    app = build_l2_flow_tracing_app(_CFG)
    exc = _sheet_by_name(app, _L2_EXCEPTIONS_NAME)
    expected_ds = "l2ft-unified-exceptions-ds"
    for v in exc.visuals:
        if isinstance(v, KPI):
            assert _typed_column(v.values[0].column).dataset.identifier == expected_ds
        elif isinstance(v, BarChart):
            assert _typed_column(v.category[0].column).dataset.identifier == expected_ds
        elif isinstance(v, Table):
            assert _typed_column(v.columns[0].column).dataset.identifier == expected_ds


# -- AA.C.6 hygiene-exceptions panel pin (mirrors AA.C.3.f's L1 check) ------


def _l2ft_text_box_by_id(sheet: Sheet, text_box_id: str) -> TextBox:
    """Lookup helper — find one TextBox on ``sheet`` by its id. Mirrors
    ``tests/json/test_l1_dashboard.py::_text_box_by_id`` for the L2FT
    sheets (AA.C.4 added the bottom hygiene panel TextBox, so the
    sheet no longer has a single-TextBox shape)."""
    for tb in sheet.text_boxes:
        if tb.text_box_id == text_box_id:
            return tb
    raise AssertionError(
        f"no TextBox with id {text_box_id!r}; sheet has "
        f"{[tb.text_box_id for tb in sheet.text_boxes]!r}"
    )


def test_aa_c_4_l2ft_exceptions_sheet_carries_hygiene_panel() -> None:
    """AA.C.4: the L2 Hygiene Exceptions sheet has a bottom panel
    sourced from ``src/recon_gen/docs/L2FT_Exceptions.md``. Unlike
    L1 (which lands one panel per invariant kind on its dedicated
    sheet), the L2FT side rolls every check kind onto the one unified
    sheet — so the panel is the bullet-roll-up shape, not a stack of
    per-kind panels. This test pins both the presence + the
    every-kind-appears contract at JSON-emit level."""
    inst = load_instance(SASQUATCH_PR_YAML)
    app = build_l2_flow_tracing_app(_CFG, l2_instance=inst)
    sheet = _sheet_by_name(app, _L2_EXCEPTIONS_NAME)
    panel = _l2ft_text_box_by_id(sheet, "l2ft-hygiene-panel")
    # Roll-up framing — the operator should know at a glance why this
    # sheet exists.
    assert "L2-to-runtime correspondence" in panel.content, (
        "L2FT panel must explain the L2-to-runtime framing — the "
        "panel_markdown intro paragraph that calls it out is missing."
    )
    # Every authored check kind must appear in the panel — drops a
    # bullet per kind so the roll-up reads as a catalog. The titles
    # match the unified dataset's ``check_type`` literals exactly
    # (verified separately in test_unified_exceptions_dataset_unions_...).
    for kind_title in (
        "Chain Orphans",
        "Unmatched Rail Name",
        "Dead Rails",
        "Dead Bundles Activity",
        "Dead Metadata Declarations",
        "Dead Limit Schedules",
    ):
        assert kind_title in panel.content, (
            f"L2FT panel missing roll-up bullet for "
            f"{kind_title!r} — every check kind in the unified "
            f"dataset must surface in the panel for parity."
        )


def test_unified_exceptions_dataset_unions_all_six_check_types() -> None:
    """The unified dataset's SQL UNIONs all 6 check_type literals so
    every L2 hygiene check feeds the same KPI / bar / table. Catches
    accidental drops of a check branch from the UNION."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_unified_l2_exceptions_dataset,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    aws_ds = build_unified_l2_exceptions_dataset(_CFG, inst)
    sql = _ds_sql(aws_ds)
    for check_type in (
        "Chain Orphans",
        # Z.B (2026-05-15) — was "Unmatched Transfer Type"; renamed to
        # match the Z.B rail-as-type subsumption (AA.C.4 follow-up).
        "Unmatched Rail Name",
        "Dead Rails",
        "Dead Bundles Activity",
        "Dead Metadata Declarations",
        "Dead Limit Schedules",
    ):
        assert f"'{check_type}'" in sql, (
            f"check_type {check_type!r} missing from unified SQL"
        )


# -- Metadata-cascade source-of-truth (kept from M.3.8) ----------------------


def test_declared_metadata_keys_walks_union_across_rails() -> None:
    """`declared_metadata_keys` returns the sorted union of every
    Rail's `metadata_keys`. Drives the cascade Key dropdown's
    StaticValues — single source of truth so a key declared on a
    rail surfaces in the cascade dropdown."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        declared_metadata_keys,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    keys = declared_metadata_keys(inst)
    expected = sorted({
        str(k) for r in inst.rails for k in r.metadata_keys
    })
    assert keys == expected
    # Sorted (deterministic across runs).
    assert keys == sorted(keys)


# -- Postings + meta-values cascade datasets (M.3.10c) ----------------------


def test_postings_dataset_targets_prefixed_current_transactions() -> None:
    """Postings reads from `<prefix>_current_transactions` (Z.C —
    where prefix = cfg.db_table_prefix)."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_postings_dataset,
    )
    from dataclasses import replace
    inst = load_instance(SASQUATCH_PR_YAML)
    cfg = replace(_CFG, db_table_prefix="sasquatch_pr")
    aws_ds = build_postings_dataset(cfg, inst)
    sql = _ds_sql(aws_ds)
    assert "FROM sasquatch_pr_current_transactions" in sql


def test_postings_dataset_uses_cascade_substitution() -> None:
    """Cascade WHERE clause has the sentinel short-circuit + per-key
    branches with literal JSON paths (P.9f.b — Oracle JSON_VALUE
    rejects runtime-concatenated paths so we emit one branch per
    declared metadata key)."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_postings_dataset, META_KEY_ALL_SENTINEL,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    sql = _ds_sql(build_postings_dataset(_CFG, inst))
    assert f"<<$pKey>> = '{META_KEY_ALL_SENTINEL}'" in sql
    # Spot-check one declared key picks the literal-path branch shape.
    assert (
        "<<$pKey>> = 'customer_id' AND "
        "JSON_VALUE(metadata, '$.customer_id') IN (<<$pValues>>)"
    ) in sql


def test_postings_dataset_declares_cascade_and_pushdown_parameters() -> None:
    """Five dataset parameters: the metadata cascade pair (pKey /
    pValues, both SINGLE_VALUED per Y.1.m) plus the AA.A.3 pushdown
    trio (pL2ftRail / pL2ftStatus / pL2ftBundle, all SINGLE_VALUED
    post-AA.A.3). All three pushdown params default to the 1-element
    ``[L2FT_ALL_SENTINEL]`` (the SQL guard turns it into "match all");
    pre-AA.A.3 status / bundle had value-list defaults and rail had the
    sentinel-list default (X.2.t.2). AA.A.3 unified all three on the
    SINGLE shape per the drill-to-one default."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        L2FT_ALL_SENTINEL,
        P_L2FT_RAILS_DATE_END,
        P_L2FT_RAILS_DATE_START,
        build_postings_dataset, META_KEY_ALL_SENTINEL,
        META_VALUE_PLACEHOLDER_SENTINEL,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    aws_ds = build_postings_dataset(_CFG, inst)
    params = aws_ds.DatasetParameters
    # Phase BM — 5 pre-BM (pKey/pValues + Rail/Status/Bundle) + 2 BM
    # date params (pL2ftDateStart/End) = 7.
    assert params is not None and len(params) == 7
    date_param_names = {
        p.DateTimeDatasetParameter.Name
        for p in params if p.DateTimeDatasetParameter is not None
    }
    assert P_L2FT_RAILS_DATE_START in date_param_names
    assert P_L2FT_RAILS_DATE_END in date_param_names
    by_name = _string_ds_params(params)
    # Metadata cascade pair.
    assert by_name["pKey"].ValueType == "SINGLE_VALUED"
    # Y.1.m: SINGLE_VALUED (was MULTI_VALUED until the cascade
    # diagnosis revealed the text-field control couldn't commit
    # non-empty values to multi-valued params).
    assert by_name["pValues"].ValueType == "SINGLE_VALUED"
    assert by_name["pKey"].DefaultValues.StaticValues == [META_KEY_ALL_SENTINEL]
    assert by_name["pValues"].DefaultValues.StaticValues == [
        META_VALUE_PLACEHOLDER_SENTINEL,
    ]
    # AA.A.3 — rail / status / bundle all SINGLE_VALUED with the
    # L2FT_ALL_SENTINEL default.
    assert by_name["pL2ftRail"].ValueType == "SINGLE_VALUED"
    assert by_name["pL2ftRail"].DefaultValues.StaticValues == [L2FT_ALL_SENTINEL]
    assert by_name["pL2ftStatus"].ValueType == "SINGLE_VALUED"
    assert by_name["pL2ftStatus"].DefaultValues.StaticValues == [L2FT_ALL_SENTINEL]
    assert by_name["pL2ftBundle"].ValueType == "SINGLE_VALUED"
    assert by_name["pL2ftBundle"].DefaultValues.StaticValues == [L2FT_ALL_SENTINEL]


def test_postings_dataset_pushes_rail_status_bundle_into_sql() -> None:
    """Y.2.c + AA.A.3 — the Rails sheet's three pushdown dropdowns
    narrow inside the postings dataset SQL (single-valued ``<<$param>>``
    substitution post-AA.A.3), not via analysis-level CategoryFilters.
    The projection wraps in a subquery so the CASE-aliased ``status`` /
    ``bundle_status`` are visible to the outer WHERE."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_postings_dataset,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    sql = _ds_sql(build_postings_dataset(_CFG, inst))
    assert "rail_name = <<$pL2ftRail>>" in sql
    assert "status = <<$pL2ftStatus>>" in sql
    assert "bundle_status = <<$pL2ftBundle>>" in sql
    # The trio sits in the OUTER WHERE over a subquery (CASE-aliases);
    # rail_name uses the AA.A.3 SINGLE_VALUED sentinel-guarded form.
    assert ") postings\nWHERE ('__l2ft_all__' = <<$pL2ftRail>>" in sql


def test_rails_pushdown_params_bridge_to_postings_dataset() -> None:
    """Y.2.c — the Rail / Status / Bundle analysis params each bridge
    to their namesake dataset parameter on the postings dataset (and
    nothing else), and there are no ``fg-l2ft-rails-{rail,status,bundle}``
    FilterGroups left."""
    from recon_gen.common.tree import StringParam

    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    for pname, dsname in (
        ("pL2ftRail", "pL2ftRail"),
        ("pL2ftStatus", "pL2ftStatus"),
        ("pL2ftBundle", "pL2ftBundle"),
    ):
        p = next(p for p in app.analysis.parameters if str(p.name) == pname)
        assert isinstance(p, StringParam)
        assert p.mapped_dataset_params is not None
        assert {
            (ds.identifier, name) for ds, name in p.mapped_dataset_params
        } == {(DS_POSTINGS, dsname)}
    fg_ids = {
        str(fg.filter_group_id) for fg in app.analysis.filter_groups
    }
    assert not (fg_ids & {
        "fg-l2ft-rails-rail", "fg-l2ft-rails-status", "fg-l2ft-rails-bundle",
    })


def test_meta_values_dataset_is_long_form_with_metadata_key_column() -> None:
    """Meta-values dataset projects (metadata_key, metadata_value) for every
    declared key via UNION ALL — QS's CascadingControlConfiguration filters
    by metadata_key column-match (NOT by dataset-parameter substitution,
    which doesn't trigger a re-query at the QS widget level)."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_meta_values_dataset,
    )
    inst = load_instance(SASQUATCH_PR_YAML)
    aws_ds = build_meta_values_dataset(_CFG, inst)
    sql = _ds_sql(aws_ds)
    # Long-form: UNION ALL one branch per declared key.
    assert "UNION ALL" in sql
    assert "AS metadata_key" in sql
    assert "AS metadata_value" in sql
    # No dataset parameters — cascade is column-match driven.
    assert aws_ds.DatasetParameters is None or aws_ds.DatasetParameters == []


def test_meta_key_param_maps_to_postings_only() -> None:
    """The Key analysis-param's MappedDataSetParameters bridge only the
    postings dataset (so its `<<$pKey>>` substitution narrows the
    transactions table). The meta-values dataset doesn't take a `pKey`
    parameter — QS's CascadingControlConfiguration filters its rows by
    metadata_key column-match instead."""
    from recon_gen.common.tree import StringParam

    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    p_key = next(
        p for p in app.analysis.parameters
        if str(p.name) == "pL2ftMetaKey"
    )
    assert isinstance(p_key, StringParam)
    assert p_key.mapped_dataset_params is not None
    mapped_pairs = {
        (ds.identifier, name) for ds, name in p_key.mapped_dataset_params
    }
    assert mapped_pairs == {
        (DS_POSTINGS, "pKey"),
    }


def test_meta_value_param_maps_to_postings_only() -> None:
    """The Value analysis-param maps to `pValues` on the postings
    dataset only — meta-values doesn't need the value back since it
    drives the dropdown's selectable_values, not its own filter."""
    from recon_gen.common.tree import StringParam

    app = build_l2_flow_tracing_app(_CFG)
    assert app.analysis is not None
    p_val = next(
        p for p in app.analysis.parameters
        if str(p.name) == "pL2ftMetaValue"
    )
    assert isinstance(p_val, StringParam)
    # Y.1.m: single-valued (was multi_valued=True until the cascade
    # diagnosis revealed text-field controls can't commit non-empty
    # values to multi-valued params — analyst now filters one value at
    # a time on this sheet).
    assert p_val.multi_valued is False
    assert p_val.mapped_dataset_params is not None
    assert len(p_val.mapped_dataset_params) == 1
    ds, name = p_val.mapped_dataset_params[0]
    assert ds.identifier == DS_POSTINGS
    assert name == "pValues"


def test_meta_key_dropdown_includes_sentinel_plus_declared_keys() -> None:
    """Key dropdown shows the `__ALL__` sentinel first (default state
    = no metadata filter) followed by every declared key."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        META_KEY_ALL_SENTINEL, declared_metadata_keys,
    )
    from recon_gen.common.tree import ParameterDropdown, StaticValues
    app = build_l2_flow_tracing_app(_CFG)
    rails = _sheet_by_name(app, _RAILS_NAME)
    key_ctrl = next(
        c for c in rails.parameter_controls
        if _control_title(c) == "Metadata Key"
    )
    assert isinstance(key_ctrl, ParameterDropdown)
    assert isinstance(key_ctrl.selectable_values, StaticValues)
    expected = [META_KEY_ALL_SENTINEL] + declared_metadata_keys(default_l2_instance())
    assert key_ctrl.selectable_values.values == expected


def test_meta_value_control_is_bound_to_pl2ftmetavalue_param() -> None:
    """X.1.b — Value control's bound parameter is ``pL2ftMetaValue``,
    which maps to the postings dataset's ``pValues``. The text-field
    shape (post-X.1.b) writes the typed value directly to the
    parameter; pre-X.1.b a LinkedValues dropdown sourced its options
    from the meta-values dataset. Either way the bound parameter is
    the same — this test catches a wiring bug where the control gets
    bound to the wrong parameter."""
    from recon_gen.common.tree import ParameterTextField

    app = build_l2_flow_tracing_app(_CFG)
    rails = _sheet_by_name(app, _RAILS_NAME)
    val_ctrl = next(
        c for c in rails.parameter_controls
        if _control_title(c) == "Metadata Value"
    )
    assert isinstance(val_ctrl, ParameterTextField)
    assert val_ctrl.parameter.name == "pL2ftMetaValue"


# -- P.4.b dialect-aware empty-fallback branches -----------------------------


def test_unified_l2_exceptions_empty_metadata_branch_is_dialect_aware() -> None:
    """P.4.b — when no Rail declares a metadata_key, the Dead Metadata
    UNION branch (and every other empty-CTE fallback) emits a typed-
    NULL row. PG emits ``NULL::text`` (lowercase from typed_null);
    Oracle emits ``CAST(NULL AS CLOB)``. PG case is lowercase per the
    helper's literal-passthrough — Postgres type names are case-
    insensitive so the SQL is functionally identical to the legacy
    uppercase ``NULL::TEXT``."""
    from dataclasses import replace
    from recon_gen.apps.l2_flow_tracing.datasets import (
        build_unified_l2_exceptions_dataset,
    )
    from recon_gen.common.l2 import L2Instance
    from recon_gen.common.sql import Dialect

    # Empty-rails instance — every CTE helper hits its fallback branch.
    # Z.C — L2Instance no longer carries an `instance` field.
    empty = L2Instance(
        accounts=(), account_templates=(),
        rails=(), transfer_templates=(), chains=(),
        limit_schedules=(),
    )
    cfg_pg = replace(_CFG, dialect=Dialect.POSTGRES)
    cfg_or = replace(_CFG, dialect=Dialect.ORACLE)

    sql_pg = _ds_sql(build_unified_l2_exceptions_dataset(cfg_pg, empty))
    sql_or = _ds_sql(build_unified_l2_exceptions_dataset(cfg_or, empty))

    # Empty-fallback NULLs use bounded VARCHAR(4000) so they UNION
    # cleanly with the real branches' string columns on both dialects
    # (Oracle CLOB can't be UNIONed with VARCHAR2 — switched in P.9f.b).
    assert "NULL::varchar(4000)" in sql_pg
    # Oracle output must not carry any PG-style ``::`` cast.
    assert "::varchar(4000)" not in sql_or
    assert "::VARCHAR(4000)" not in sql_or
    assert "CAST(NULL AS VARCHAR2(4000))" in sql_or
