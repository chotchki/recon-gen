"""BU.1.8 — Typed pipeline overlay primitive tests.

Pins the surface that replaces the `cfg.test.generator.scope =
"uncovered_rails"` indirection: typed `OverlayLayer` + named
`PipelineOverlays` flows (ETL_DEBUG / TRAINER_CLEAN / LOCKED_SEED).
"""

from __future__ import annotations

from recon_gen.common.l2.pipeline_overlays import (
    ETL_DEBUG,
    L1_INVARIANT_PLANTS,
    L2_DEMO_GAP_OVERLAY,
    LOCKED_SEED,
    TRAINER_CLEAN,
    OverlayLayer,
    PipelineOverlays,
)


def test_overlay_layer_is_frozen_slots() -> None:
    """OverlayLayer is frozen+slots so mutating a singleton at runtime
    raises rather than silently swapping behavior."""
    assert L1_INVARIANT_PLANTS.__class__ is OverlayLayer
    assert OverlayLayer.__dataclass_params__.frozen is True  # type: ignore[attr-defined]: dataclass private attribute pyright doesn't know about — runtime is the contract here
    assert "__slots__" in OverlayLayer.__dict__


def test_etl_debug_runs_baseline_plus_both_overlays() -> None:
    """ETL_DEBUG = baseline + L1 plants + L2 BTa.8 overlay. Operator
    wants demo noise on /etl/triage + /etl/run Coverage."""
    assert ETL_DEBUG.layers == (L1_INVARIANT_PLANTS, L2_DEMO_GAP_OVERLAY)
    assert ETL_DEBUG.names() == ("l1_invariant_plants", "l2_demo_gap_overlay")


def test_trainer_clean_runs_baseline_only() -> None:
    """TRAINER_CLEAN = baseline only. Premise: operator plants exactly
    one scenario via the Trainer and sees ONLY it on the tour."""
    assert TRAINER_CLEAN.layers == ()
    assert TRAINER_CLEAN.names() == ()


def test_locked_seed_matches_full_seed_sql_contract() -> None:
    """LOCKED_SEED = baseline + L1 plants, NO L2 overlay. Preserves
    byte-identity with `build_full_seed_sql` (which predates the BTa.8
    L2 overlay)."""
    assert LOCKED_SEED.layers == (L1_INVARIANT_PLANTS,)
    assert LOCKED_SEED.names() == ("l1_invariant_plants",)


def test_overlay_layer_apply_is_async_callable() -> None:
    """`apply` must be awaitable — deploy_pipeline awaits it inline."""
    import inspect

    assert inspect.iscoroutinefunction(L1_INVARIANT_PLANTS.apply)
    assert inspect.iscoroutinefunction(L2_DEMO_GAP_OVERLAY.apply)


def test_pipeline_overlays_layers_default_is_empty_tuple() -> None:
    """Default PipelineOverlays() = no layers; equivalent to TRAINER_CLEAN."""
    empty = PipelineOverlays()
    assert empty.layers == ()
    assert empty == TRAINER_CLEAN


def test_layer_names_are_unique_within_flow() -> None:
    """Within one flow, no two layers share a name (the name is the
    Lock 9 anti-drift key for DeploySummary.events filtering)."""
    for flow in (ETL_DEBUG, TRAINER_CLEAN, LOCKED_SEED):
        names = flow.names()
        assert len(names) == len(set(names)), f"duplicate layer name in {flow}"
