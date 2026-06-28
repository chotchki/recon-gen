"""Theme palette as an L2 model concept.

Lives under ``common/l2/`` because — per N.1 — every L2 instance carries
its own brand inline in the YAML, so the ``ThemePreset`` dataclass is a
piece of the L2 model rather than an app-shared registry concept.

``common/theme.py`` re-exports ``ThemePreset`` from here for back-compat
and owns the single ``DEFAULT_PRESET`` fallback used when an L2 instance
omits its inline ``theme:`` block (N.4.l dropped the lookup-by-name
registry; only the one default fallback remains).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemePreset:
    """Everything that varies between theme variants."""

    theme_name: str
    version_description: str
    analysis_name_prefix: str | None  # None → use default analysis names

    # Data colour palette
    data_colors: list[str]
    empty_fill_color: str
    gradient: list[str]  # [light, dark] for min/max

    # UI colour palette
    primary_bg: str
    secondary_bg: str
    primary_fg: str
    secondary_fg: str
    accent: str
    accent_fg: str
    # Pale-accent cell tint used as the background for table cells whose
    # click target is a DATA_POINT_MENU (right-click) rather than a direct
    # left-click drill.
    link_tint: str
    danger: str
    danger_fg: str
    warning: str
    warning_fg: str
    success: str
    success_fg: str
    dimension: str
    dimension_fg: str
    measure: str
    measure_fg: str

    # Optional brand assets for the docs site (mkdocs-material).
    # Either field accepts a URL (``http://`` / ``https://`` / protocol-
    # relative ``//``) or an absolute file path. Absolute paths get
    # copied into the docs build at render time; URLs pass through.
    # ``None`` falls back to whatever ``mkdocs.yml`` declares — for the
    # canonical site that's the SNB mark, so spec_example renders with
    # SNB branding unless the integrator overrides here.
    logo: str | None = None
    favicon: str | None = None
