"""Theme presets — registry default + L2 resolver.

Per N.1.g, the registry holds ONLY the ``default`` preset (a neutral
blue/grey professional palette). Per-instance brand palettes
(formerly ``sasquatch-bank``, ``sasquatch-bank-investigation``) moved
to inline ``theme:`` blocks on the L2 YAML — apps consume the L2
theme via ``resolve_l2_theme(l2_instance)``.

The ``ThemePreset`` dataclass itself lives in ``common/l2/theme.py``
— theme is an L2 model concept; this module re-exports it for
back-compat and owns ``DEFAULT_PRESET`` (the in-canvas-accent fallback
the renderers resolve colors from). The QS ``Theme`` resource builder
that once lived here is gone with the QS emitter (DW phase).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from recon_gen.common.l2.theme import ThemePreset

__all__ = [
    "DEFAULT_PRESET",
    "ThemePreset",
    "resolve_l2_theme",
]


if TYPE_CHECKING:
    from recon_gen.common.l2 import L2Instance


# ---------------------------------------------------------------------------
# Default preset — blues and greys
# ---------------------------------------------------------------------------

# Primary blues (dark → light)
_NAVY = "#1B2A4A"
_DARK_BLUE = "#2E5090"
_MEDIUM_BLUE = "#4A7DC7"
_LIGHT_BLUE = "#7BAAF7"
_PALE_BLUE = "#C5DAF7"

# Greys
_CHARCOAL = "#2D2D2D"
_DARK_GREY = "#4A4A4A"
_MEDIUM_GREY = "#8C8C8C"
_LIGHT_GREY = "#D9D9D9"
_OFF_WHITE = "#F5F6FA"
_WHITE = "#FFFFFF"

# Semantic
_SUCCESS_GREEN = "#2E7D32"
_WARNING_AMBER = "#E65100"
_DANGER_RED = "#C62828"

DEFAULT_PRESET = ThemePreset(
    theme_name="Recon Gen Theme",
    version_description="Auto-generated dashboard theme",
    analysis_name_prefix=None,
    data_colors=[
        _DARK_BLUE,
        "#E07B39",       # warm orange contrast
        "#3A9E6F",       # teal green
        _MEDIUM_BLUE,
        "#8E5EA2",       # muted purple
        "#E6B422",       # gold
        "#4BC0C0",       # cyan
        _MEDIUM_GREY,    # neutral fallback
    ],
    empty_fill_color=_LIGHT_GREY,
    gradient=[_PALE_BLUE, _DARK_BLUE],
    primary_bg=_WHITE,
    secondary_bg=_OFF_WHITE,
    primary_fg=_CHARCOAL,
    secondary_fg=_DARK_GREY,
    accent=_DARK_BLUE,
    accent_fg=_WHITE,
    link_tint="#E8EFF9",
    danger=_DANGER_RED,
    danger_fg=_WHITE,
    warning=_WARNING_AMBER,
    warning_fg=_WHITE,
    success=_SUCCESS_GREEN,
    success_fg=_WHITE,
    dimension=_MEDIUM_BLUE,
    dimension_fg=_WHITE,
    measure=_NAVY,
    measure_fg=_WHITE,
)


def resolve_l2_theme(l2_instance: "L2Instance | None") -> ThemePreset | None:
    """Pick the theme to render with for an L2-fed app (N.1 / N.4.k).

    Returns the L2 instance's inline theme block when present (the
    N.1 path); ``None`` otherwise — the silent-fallback contract
    (N.4.k). Callers that consume the return for accent colors (e.g.,
    Getting Started rich text) should fall through to
    ``DEFAULT_PRESET.accent`` so on-canvas colors stay sensible when
    no L2 theme is declared. ``None`` means the renderer uses its own
    neutral default (App2) — there's no QS Theme resource any more.
    """
    if l2_instance is not None and l2_instance.theme is not None:
        return l2_instance.theme
    return None
