"""Typed registry of deliberate App2-vs-QuickSight parity breaks.

Each entry in :data:`PARITY_BREAKS` documents a place where the App2
renderer deliberately exceeds (or routes around) what the QS embed can
host. Per CN.0 Lock CN-5: the registry is the single declaration; the
quirks log + the handbook's per-sheet "QS parity notes" section both
read from this module.

Public surface:

- :class:`ParitySeverity` — enhancement / workaround / hard_divergence
- :class:`QSParityBreak` — frozen dataclass
- :data:`PARITY_BREAKS` — the immutable population
"""

from __future__ import annotations

from .breaks import PARITY_BREAKS, ParitySeverity, QSParityBreak
from .emitter import render_handbook_section, render_markdown


__all__ = [
    "PARITY_BREAKS",
    "ParitySeverity",
    "QSParityBreak",
    "render_handbook_section",
    "render_markdown",
]
