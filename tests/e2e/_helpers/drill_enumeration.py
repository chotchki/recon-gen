"""DL.1 — Cross-sheet drill enumeration helper.

Walks a built ``App`` tree and yields a ``DrillSite`` per cross-sheet
``Drill`` action found on every visual. DL.2 (the parametrized e2e
gate) consumes this leaf to drive content + picker-value assertions;
DL.3 / DL.4 fix the bugs the gate surfaces.

Same-sheet drills (the walk-the-flow / re-render-around-new-anchor
pattern, ``drill.target_sheet`` resolves to the visual's owning sheet
via ``App.resolve_auto_ids``) are deliberately excluded — they don't
cross a sheet boundary so the destination content + picker-binding
contract DL.2 enforces doesn't apply to them.

The walker is intentionally tolerant of visuals without ``actions``
(e.g. ``KPI`` — the QuickSight model doesn't expose Actions on KPI
visuals) — it ``getattr``-s the field with an empty default and skips
silently rather than crashing the enumeration.

``CrossAppDrill`` (CF.X-infra — drills that target a DIFFERENT app's
dashboard, not just another sheet inside the owning app's tree) is
also excluded; those drills are content-asserted by the cross-app drill
gate (separate concern from cross-sheet drills within one app's tree).
"""

from __future__ import annotations

from typing import Iterator, NamedTuple

from recon_gen.common.tree import App, Drill, Sheet
from recon_gen.common.tree.visuals import VisualLike


class DrillSite(NamedTuple):
    """One cross-sheet drill on one source visual.

    The four fields together uniquely identify the drill within the
    app — same source sheet + visual can carry multiple drills (one
    on ``DATA_POINT_CLICK``, one on ``DATA_POINT_MENU``), and ``drill``
    is the disambiguator.

    Holds object refs (not IDs) so consumers can interrogate the
    underlying ``Drill.writes`` for picker-value assertions and the
    destination ``Sheet`` for content assertions without a second
    lookup through ``analysis.find_sheet(...)``.
    """
    src_sheet: Sheet
    src_visual: VisualLike
    drill: Drill
    dst_sheet: Sheet


def iter_cross_sheet_drills(app: App) -> Iterator[DrillSite]:
    """Yield a ``DrillSite`` for every cross-sheet ``Drill`` in ``app``.

    Resolves auto-IDs first (idempotent — safe to call on a fully-
    resolved tree) so ``drill.target_sheet`` is guaranteed to be a
    ``Sheet`` object ref rather than the ``AUTO`` sentinel. Visuals
    without an ``actions`` attribute (``KPI``) and visuals whose
    ``actions`` list is empty are silently skipped.

    Filters to cross-sheet drills only — same-sheet drills (where the
    drill's resolved ``target_sheet`` is the visual's owning sheet)
    are excluded since they don't cross a sheet boundary.

    Non-``Drill`` actions (``CrossAppDrill``, future action subtypes)
    are skipped via ``isinstance``; this helper is for the within-app
    cross-sheet drill contract only.
    """
    # Idempotent — back-fills same-sheet drills' target_sheet so the
    # cross-sheet filter below can compare ``drill.target_sheet`` to
    # the owning sheet without hitting the AUTO sentinel.
    app.resolve_auto_ids()
    if app.analysis is None:
        return
    for src_sheet in app.analysis.sheets:
        for src_visual in src_sheet.visuals:
            actions = getattr(src_visual, "actions", ())
            for action in actions:
                if not isinstance(action, Drill):
                    continue
                dst_sheet = action.target_sheet
                # After resolve_auto_ids, target_sheet is always a Sheet
                # object ref — but narrow defensively to satisfy pyright
                # (the field type is ``Sheet | AutoResolved``).
                if not isinstance(dst_sheet, Sheet):
                    continue
                if dst_sheet.sheet_id == src_sheet.sheet_id:
                    # Same-sheet drill — out of scope for the cross-
                    # sheet content+picker-value gate.
                    continue
                yield DrillSite(
                    src_sheet=src_sheet,
                    src_visual=src_visual,
                    drill=action,
                    dst_sheet=dst_sheet,
                )
