# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false, reportUnknownMemberType=false
# BF.4/F: App.analysis is Optional in the tree type, but every L2 Flow Tracing
# build sets it via populate_app_info_sheet / build_l2_flow_tracing_app — these
# tests can rely on it being non-None. ParameterControlLike protocol omits
# title/selectable_values which exist on every concrete subtype; the tests
# check the concrete shape, not the protocol.
"""L2 Flow Tracing app rendered against every L2 instance (M.3.9).

The L2 Flow Tracing dashboard's value claim is that it adapts to any
L2 instance with no per-instance code — sasquatch_pr's chain topology
should render the same shape as spec_example's (different content, same
structure), and a fuzz-generated L2 should render too. This file
parameterizes the structural assertions over ``L2_INSTANCES`` (the same
matrix ``test_l2_seed_contract.py`` uses), so adding a new YAML there
extends coverage here automatically.

What's checked across every L2 instance:

- The 4-sheet skeleton renders unchanged (Getting Started / Rails /
  Chains / L2 Exceptions in display order).
- Per-sheet visual counts: Rails has 1 Table; Chains has 1 Sankey + 1
  Table; L2 Exceptions has 12 KPIs + 6 Tables.
- Dataset count is exactly ``8 + N`` where N = the L2's distinct
  metadata key count. The fixed 8 are core (Rails + Chains + 6
  exceptions); the N is the per-instance dropdown source fan-out.
- Per-instance prefix on every dataset ID + analysis ID + dashboard
  ID, so multi-instance deploys don't collide in QuickSight.
- The metadata-driven dropdowns scale exactly with the L2's declared
  keys; an instance with 5 keys gets 5 dropdowns + 5 parameters; an
  instance with 28 gets 28 + 28.
- ``emit_analysis()`` + ``emit_dashboard()`` both succeed (full tree
  validation pass) — catches "L2 instance X has a shape that breaks
  the tree validator" regressions early.

Aurora deploy verification is M.3.10 — this file is unit-test only.
"""

from __future__ import annotations

from collections import Counter

import pytest

from recon_gen.apps.l2_flow_tracing.app import (
    _CHAINS_NAME,
    _GETTING_STARTED_NAME,
    _L2_EXCEPTIONS_NAME,
    _RAILS_NAME,
    _TRANSFER_TEMPLATES_NAME,
    build_l2_flow_tracing_app,
)
from recon_gen.apps.l2_flow_tracing.datasets import (
    declared_metadata_keys,
)
from recon_gen.common.l2 import L2Instance, load_instance
from recon_gen.common.sheets.app_info import APP_INFO_SHEET_NAME

# Reuse the matrix definition from the seed-contract test so every
# substep that adds an L2 instance to ``L2_INSTANCES`` automatically
# extends the M.3.9 verification surface here.
from tests._test_helpers import make_test_config
from tests.data.test_l2_seed_contract import L2_INSTANCES


_CFG = make_test_config()


@pytest.fixture(params=L2_INSTANCES)
def l2_instance(request: pytest.FixtureRequest) -> L2Instance:
    """Load each parameterized L2 instance once per test."""
    return load_instance(request.param)


# -- Sheet structure invariants ----------------------------------------------


def test_six_sheets_in_display_order(l2_instance: L2Instance) -> None:
    """Same 6-sheet shape across every L2 instance — switching the L2
    doesn't reshuffle the dashboard. M.4.4.5 appended the App Info
    ("i") canary as the always-last sheet."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    assert [s.name for s in app.analysis.sheets] == [
        _GETTING_STARTED_NAME, _RAILS_NAME, _CHAINS_NAME,
        _TRANSFER_TEMPLATES_NAME, _L2_EXCEPTIONS_NAME, APP_INFO_SHEET_NAME,
    ]


def test_rails_sheet_visuals_invariant(l2_instance: L2Instance) -> None:
    """Rails sheet has the 2 BO.12 orientation KPIs (Legs in Window +
    Largest Leg) + 1 Table visual. The "Latest Leg" KPI the cold-read
    asked for can't render as a typed KPI Measure because QS rejects
    NumericalMeasureField over a DATETIME column at analysis-create
    time — caught by the v11.24.0 CI deploy probe and pruned. The
    Table's Posting column carries the same freshness signal."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    rails = next(s for s in app.analysis.sheets if s.name == _RAILS_NAME)
    counts = Counter(type(v).__name__ for v in rails.visuals)
    assert counts == Counter({"KPI": 2, "Table": 1})


def test_chains_sheet_visuals_invariant(l2_instance: L2Instance) -> None:
    """Chains sheet (M.3.10d) is the per-instance explorer — exactly 1
    Table backed by the chain-instances dataset, six controls in the
    sheet's filter bar, and zero TextBox / Sankey leftover from the
    pre-M.3.10d declared-topology view (which moved to the M.7 docs
    render)."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    chains = next(s for s in app.analysis.sheets if s.name == _CHAINS_NAME)
    counts = Counter(type(v).__name__ for v in chains.visuals)
    assert counts == Counter(["Table"])


def test_l2_exceptions_sheet_visuals_invariant_M3_10l(
    l2_instance: L2Instance,
) -> None:
    """M.3.10l: L2 Exceptions sheet collapses to 1 KPI + 1 BarChart +
    1 Table backed by the unified-exceptions dataset (mirrors L1's
    Today's Exceptions). Pre-M.3.10l shape was 12 KPIs + 6 Tables
    across 6 vertical sections."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    exc = next(s for s in app.analysis.sheets if s.name == _L2_EXCEPTIONS_NAME)
    counts = Counter(type(v).__name__ for v in exc.visuals)
    assert counts == Counter(["KPI", "BarChart", "Table"])


# -- Dataset count + ID prefix invariants -----------------------------------


def test_dataset_count_is_eight_per_instance(
    l2_instance: L2Instance,
) -> None:
    """6 content datasets (M.3.10l) + 2 App Info datasets (M.4.4.5).

    Content: postings + meta-values (Rails cascade), chain-instances
    (Chains), tt-instances + tt-legs (Transfer Templates), unified-
    exceptions (L2 Exceptions). App Info: liveness + matview status."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    assert len(app.datasets) == 8


def test_every_dataset_id_carries_deployment_prefix(
    l2_instance: L2Instance,
) -> None:
    """Z.C — every dataset ID is prefixed by ``cfg.deployment_name``
    so multiple deploys (dev/staging/prod, or co-tenanted L2s with
    distinct cfg.yaml) don't collide in the same QS account. Mirrors
    `test_l1_dashboard_structure.py`'s prefix check."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    expected_prefix = f"{_CFG.deployment_name}-"
    for ds in app.datasets:
        # The arn carries the dataset ID; pull it out of the ARN's
        # `:dataset/<id>` suffix.
        ds_id = ds.arn.rsplit("/", 1)[-1]
        assert ds_id.startswith(expected_prefix), (
            f"dataset {ds.identifier!r} ID {ds_id!r} doesn't carry "
            f"the deployment prefix {expected_prefix!r}"
        )


def test_analysis_and_dashboard_ids_carry_deployment_prefix(
    l2_instance: L2Instance,
) -> None:
    """Mirror — analysis + dashboard IDs both use the same deployment
    prefix shape so deploys don't collide and cleanup-by-tag scopes
    correctly."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    analysis = app.emit_analysis()
    dashboard = app.emit_dashboard()
    expected_prefix = f"{_CFG.deployment_name}-"
    assert analysis.AnalysisId.startswith(expected_prefix)
    assert dashboard.DashboardId.startswith(expected_prefix)


def test_emit_analysis_and_dashboard_succeed(
    l2_instance: L2Instance,
) -> None:
    """Full tree validation passes for every L2 instance — catches
    'this YAML produces a shape the validator rejects' regressions."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    assert app.emit_analysis() is not None
    assert app.emit_dashboard() is not None


# -- Metadata cascade — fixed shape per L2 (M.3.10c) ------------------------


def test_metadata_cascade_has_two_params_per_instance(
    l2_instance: L2Instance,
) -> None:
    """M.3.10c reframes the metadata surface: instead of N dropdowns
    (one per key), one Key + one Value cascade pair on the Rails
    sheet. Always exactly 2 metadata params (`pL2ftMetaKey` +
    `pL2ftMetaValue`) regardless of declared-key count."""
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    meta_params = [
        p for p in app.analysis.parameters
        if str(p.name).startswith("pL2ftMeta")
    ]
    assert len(meta_params) == 2
    assert {str(p.name) for p in meta_params} == {
        "pL2ftMetaKey", "pL2ftMetaValue",
    }


def test_metadata_key_dropdown_options_scale_with_declared_keys(
    l2_instance: L2Instance,
) -> None:
    """The Key dropdown's StaticValues includes the sentinel +
    every declared metadata key — that scales per-instance. An
    instance with N keys produces N+1 dropdown options."""
    from recon_gen.apps.l2_flow_tracing.datasets import (
        META_KEY_ALL_SENTINEL,
    )
    from recon_gen.common.tree import StaticValues
    app = build_l2_flow_tracing_app(_CFG, l2_instance=l2_instance)
    n_keys = len(declared_metadata_keys(l2_instance))
    rails = next(s for s in app.analysis.sheets if s.name == _RAILS_NAME)
    key_ctrl = next(
        c for c in rails.parameter_controls if c.title == "Metadata Key"
    )
    assert isinstance(key_ctrl.selectable_values, StaticValues)
    assert key_ctrl.selectable_values.values[0] == META_KEY_ALL_SENTINEL
    assert len(key_ctrl.selectable_values.values) == n_keys + 1


# -- Cross-instance differentiation ------------------------------------------


def test_deployments_produce_different_dataset_id_namespaces() -> None:
    """Sanity: building the same app against two distinct cfgs (different
    ``deployment_name``) produces non-overlapping dataset ID sets — so a
    multi-deploy QuickSight account can host both without collision.

    Z.C — the per-deployment namespace lives on cfg.deployment_name (was
    previously auto-stamped from ``L2Instance.instance``), so the test
    swaps the cfg per build, not the L2 instance. Two integrators
    pointing at the same L2 yaml MUST still get isolated namespaces by
    setting different deployment_names in their cfg.yamls."""
    from pathlib import Path
    from recon_gen.common.l2 import load_instance

    sasq = load_instance(
        Path(__file__).parent.parent / "l2" / "sasquatch_pr.yaml"
    )
    cfg_a = make_test_config(deployment_name="recon-deploy-a")
    cfg_b = make_test_config(deployment_name="recon-deploy-b")
    a_app = build_l2_flow_tracing_app(cfg_a, l2_instance=sasq)
    b_app = build_l2_flow_tracing_app(cfg_b, l2_instance=sasq)

    a_ds_ids = {ds.arn.rsplit("/", 1)[-1] for ds in a_app.datasets}
    b_ds_ids = {ds.arn.rsplit("/", 1)[-1] for ds in b_app.datasets}
    assert a_ds_ids.isdisjoint(b_ds_ids), (
        "Deployment-prefix isolation broken — dataset IDs overlap "
        "between two cfgs with distinct deployment_name values"
    )
