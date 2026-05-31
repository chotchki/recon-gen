"""BU.1.8 — Typed pipeline overlay primitives.

Replaces the round-1 indirection ``cfg.test_generator.scope = "uncovered_rails"``
(which means "after wipe + covered={} → baseline only") with a
typed surface that says what it means at the wiring site.

Two demo flows share the wipe+regenerate pipeline but differ on
which overlay layers fire afterward:

- **ETL_DEBUG** (``/etl/run`` Refresh Data) — baseline + L1
  invariant plants (drift/overdraft/etc) + BTa.8 L2 demo-gap
  overlay. Operator wants noise to debug against.
- **TRAINER_CLEAN** (``/training/reset``) — baseline only. No
  overlays. Operator plants exactly one scenario via the Trainer
  + sees ONLY it surface on the dashboard tour.
- **LOCKED_SEED** (used by tests + CLI data apply) — baseline + L1
  plants, no L2 overlay. Matches `build_full_seed_sql` byte-output
  contract.

Each overlay layer is a typed ``OverlayLayer`` dataclass carrying
its own ``apply`` function — adding a new overlay (e.g. a future
Trainer-mode "amplify L1 plants by 5×" overlay) = one new
singleton, no dispatch table to update. Mirrors the BU.0 Lock 7
PlantKindEntry "single source of truth" pattern but for the
deploy-pipeline post-baseline layers.

The current scope-string surface (`cfg.test_generator.scope`)
stays for CLI data apply backward compat; deploy_pipeline's
Studio-facing entry points switch to ``overlays=`` typed kwarg.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2 import L2Instance


# -- Overlay-layer protocol ------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlayContext:
    """The runtime context handed to each overlay layer's ``apply``.

    Carries the cfg + L2 instance + the shared event-emitter the
    deploy-pipeline tees through (so layer events land in the same
    DeploySummary.events stream as the baseline steps).
    """

    cfg: "Config"
    instance: "L2Instance"
    dev_log: Callable[[Mapping[str, object]], Awaitable[None]] | None


@dataclass(frozen=True, slots=True)
class OverlayLayer:
    """One typed overlay the deploy pipeline applies after baseline.

    Each layer carries its own ``apply`` so adding a new layer =
    one singleton declaration + no dispatch-table changes. The
    layer ``name`` is the canonical key surfaced in
    ``DeploySummary.events`` + Lock 9 anti-drift tests.
    """

    name: str
    description: str
    apply: Callable[[OverlayContext], Awaitable[None]]


# -- Canonical overlay singletons -----------------------------------------


async def _apply_l1_invariant_plants(ctx: OverlayContext) -> None:
    """Re-emit + execute the L1-invariant plant overlay (drift,
    overdraft, stuck_pending, etc).

    Implementation: compose the same scenario the CLI's `data apply`
    composes, then call `emit_seed` (plants-only path — no baseline
    re-emit) + execute the SQL. Sits on top of whatever
    `emit_baseline_seed` just wrote.
    """
    import asyncio  # noqa: PLC0415
    from recon_gen.cli._helpers import build_default_scenario  # pyright: ignore[reportUnknownVariableType]  # WHY: cli/_helpers has pending untyped-def waiver
    from recon_gen.common.db import connect_demo_db, execute_script  # noqa: PLC0415
    from recon_gen.common.l2.seed import emit_seed  # noqa: PLC0415

    if ctx.dev_log is not None:
        await ctx.dev_log({
            "event": "deploy:overlay:l1_invariant_plants:start",
        })
    scenario = build_default_scenario(  # pyright: ignore[reportUnknownVariableType]: cli/_helpers has untyped-def waiver — propagates to callers until that module is annotated
        ctx.instance,
        anchor=ctx.cfg.test_generator.end_date,
        plants=ctx.cfg.test_generator.plants or None,
    )
    sql = emit_seed(  # pyright: ignore[reportUnknownArgumentType]  # WHY: scenario carries the helper-waiver
        ctx.instance, scenario,
        prefix=ctx.cfg.db_table_prefix,
        dialect=ctx.cfg.dialect,
    )

    def _run() -> None:
        conn = connect_demo_db(ctx.cfg)
        try:
            cur = conn.cursor()
            try:
                execute_script(cur, sql, dialect=ctx.cfg.dialect)
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)
    if ctx.dev_log is not None:
        await ctx.dev_log({
            "event": "deploy:overlay:l1_invariant_plants:done",
        })


async def _apply_l2_demo_gap_overlay(ctx: OverlayContext) -> None:
    """Re-emit + execute the BTa.8 L2 demo-gap overlay (phantom
    rail/template/missing-metadata + uncovered rail/template
    DELETEs)."""
    import asyncio  # noqa: PLC0415
    from recon_gen.common.db import connect_demo_db, execute_script  # noqa: PLC0415
    from recon_gen.common.l2.demo_etl_gaps import (  # noqa: PLC0415
        emit_demo_etl_gap_sql,
    )

    if ctx.dev_log is not None:
        await ctx.dev_log({
            "event": "deploy:overlay:l2_demo_gap:start",
        })
    anchor = datetime.now()  # typing-smell: ignore[no-datetime-now]: overlay's wall-clock anchor matches the etl_run POST behavior so freshly-planted gaps fall in the operator's default date window
    sql = emit_demo_etl_gap_sql(
        ctx.instance,
        prefix=ctx.cfg.db_table_prefix,
        dialect=ctx.cfg.dialect,
        anchor=anchor,
    )

    def _run() -> None:
        conn = connect_demo_db(ctx.cfg)
        try:
            cur = conn.cursor()
            try:
                execute_script(cur, sql, dialect=ctx.cfg.dialect)
                conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    await asyncio.to_thread(_run)
    if ctx.dev_log is not None:
        await ctx.dev_log({
            "event": "deploy:overlay:l2_demo_gap:done",
        })


L1_INVARIANT_PLANTS: Final[OverlayLayer] = OverlayLayer(
    name="l1_invariant_plants",
    description=(
        "Re-emit the L1 invariant violation plants (drift, "
        "overdraft, stuck_pending, etc) so the L1 Dashboard's "
        "exception sheets have demo content."
    ),
    apply=_apply_l1_invariant_plants,
)


L2_DEMO_GAP_OVERLAY: Final[OverlayLayer] = OverlayLayer(
    name="l2_demo_gap_overlay",
    description=(
        "Re-emit the BTa.8 phantom rail / phantom template / "
        "missing-metadata INSERTs + uncovered rail/template "
        "DELETEs so /etl/triage + /etl/run Coverage have demo "
        "content."
    ),
    apply=_apply_l2_demo_gap_overlay,
)


# -- PipelineOverlays + named flows ---------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineOverlays:
    """The tuple of overlay layers the deploy pipeline applies
    after baseline. Order matters — layers fire in tuple order so
    a later layer sees the state earlier layers left behind."""

    layers: tuple[OverlayLayer, ...] = ()

    def names(self) -> tuple[str, ...]:
        """Tuple of layer names for diagnostic logging."""
        return tuple(l.name for l in self.layers)


# Canonical pre-composed flows. Callers pick one of these instead
# of assembling tuples ad-hoc — keeps the demo-flow vocabulary
# centralized + makes "what does Studio do here?" a single grep.

ETL_DEBUG: Final[PipelineOverlays] = PipelineOverlays(
    layers=(L1_INVARIANT_PLANTS, L2_DEMO_GAP_OVERLAY),
)
"""``/etl/run`` Refresh Data — baseline + L1 plants + L2 BTa.8 overlay.
Operator wants noise to debug against."""


TRAINER_CLEAN: Final[PipelineOverlays] = PipelineOverlays(layers=())
"""``/training/reset`` — baseline only. Operator plants exactly
one scenario via the Trainer + sees ONLY it surface."""


LOCKED_SEED: Final[PipelineOverlays] = PipelineOverlays(
    layers=(L1_INVARIANT_PLANTS,),
)
"""Tests + CLI data apply — baseline + L1 plants, no L2 overlay.
Preserves byte-identity with `build_full_seed_sql` output (which
predates the L2 BTa.8 overlay)."""
