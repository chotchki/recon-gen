"""QuickSight theme — registry default + builder.

Per N.1.g, the registry holds ONLY the ``default`` preset (a neutral
blue/grey professional palette). Per-instance brand palettes
(formerly ``sasquatch-bank``, ``sasquatch-bank-investigation``) moved
to inline ``theme:`` blocks on the L2 YAML — apps consume the L2
theme via ``resolve_l2_theme(l2_instance)``.

The ``ThemePreset`` dataclass itself lives in ``common/l2/theme.py``
— theme is an L2 model concept; this module re-exports for back-compat
and provides the ``build_theme(cfg)`` QuickSight Theme constructor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from quicksight_gen.common.config import Config
from quicksight_gen.common.l2.theme import ThemePreset
from quicksight_gen.common.models import (
    DataColorPalette,
    FontFamily,
    Gutter,
    Margin,
    ResourcePermission,
    SheetStyle,
    Theme,
    ThemeConfiguration,
    Tile,
    TileBorder,
    TileLayout,
    Typography,
    UIColorPalette,
)

__all__ = [
    "DEFAULT_PRESET",
    "ThemePreset",
    "build_theme",
    "resolve_l2_theme",
]


if TYPE_CHECKING:
    from quicksight_gen.common.l2 import L2Instance


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
    theme_name="QuickSight Gen Theme",
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
    no L2 theme is declared. The dashboard-level fallback is AWS
    QuickSight's CLASSIC theme (no Theme resource emitted by
    ``build_theme`` when ``None``).
    """
    if l2_instance is not None and l2_instance.theme is not None:
        return l2_instance.theme
    return None


# ---------------------------------------------------------------------------
# Theme builder
# ---------------------------------------------------------------------------

def build_theme(cfg: Config, theme: ThemePreset | None) -> Theme | None:
    """Build the QuickSight Theme resource for ``theme`` (N.4.k).

    Returns ``None`` when ``theme`` is ``None`` — the silent-fallback
    contract: an L2 instance with no inline ``theme:`` block deploys
    against AWS QuickSight's CLASSIC theme without emitting a custom
    Theme resource. The CLI skips ``theme.json`` write + skips the
    deploy step in that case.

    When ``theme`` is set, builds a complete Theme honoring permissions
    + tags from ``cfg``.
    """
    if theme is None:
        return None
    preset = theme
    theme_id = cfg.prefixed("theme")

    permissions = None
    if cfg.principal_arns:
        theme_actions = [
            "quicksight:DescribeTheme",
            "quicksight:DescribeThemeAlias",
            "quicksight:DescribeThemePermissions",
            "quicksight:ListThemeAliases",
            "quicksight:ListThemeVersions",
            "quicksight:UpdateTheme",
            "quicksight:UpdateThemeAlias",
            "quicksight:UpdateThemePermissions",
            "quicksight:DeleteTheme",
            "quicksight:DeleteThemeAlias",
            "quicksight:CreateThemeAlias",
        ]
        permissions = [
            ResourcePermission(Principal=arn, Actions=theme_actions)
            for arn in cfg.principal_arns
        ]

    return Theme(
        AwsAccountId=cfg.aws_account_id,
        ThemeId=theme_id,
        Name=preset.theme_name,
        BaseThemeId="CLASSIC",
        Tags=cfg.tags(),
        Configuration=ThemeConfiguration(
            DataColorPalette=DataColorPalette(
                Colors=preset.data_colors,
                EmptyFillColor=preset.empty_fill_color,
                MinMaxGradient=preset.gradient,
            ),
            UIColorPalette=UIColorPalette(
                PrimaryBackground=preset.primary_bg,
                SecondaryBackground=preset.secondary_bg,
                PrimaryForeground=preset.primary_fg,
                SecondaryForeground=preset.secondary_fg,
                Accent=preset.accent,
                AccentForeground=preset.accent_fg,
                Danger=preset.danger,
                DangerForeground=preset.danger_fg,
                Warning=preset.warning,
                WarningForeground=preset.warning_fg,
                Success=preset.success,
                SuccessForeground=preset.success_fg,
                Dimension=preset.dimension,
                DimensionForeground=preset.dimension_fg,
                Measure=preset.measure,
                MeasureForeground=preset.measure_fg,
            ),
            Sheet=SheetStyle(
                Tile=Tile(
                    Border=TileBorder(Show=True),
                ),
                TileLayout=TileLayout(
                    Gutter=Gutter(Show=True),
                    Margin=Margin(Show=True),
                ),
            ),
            Typography=Typography(
                FontFamilies=[
                    FontFamily(FontFamily="Amazon Ember"),
                    FontFamily(FontFamily="sans-serif"),
                ],
            ),
        ),
        Permissions=permissions,
        VersionDescription=preset.version_description,
    )
