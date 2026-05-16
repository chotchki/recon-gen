"""Loader tests for the inline ``theme:`` block on ``L2Instance`` (N.1.h).

Per N.1, every L2 instance carries its own brand inline. The loader
parses the block into a ``ThemePreset``; missing/null returns ``None``
(app falls back to the registry default). All color fields validate
against the standard ``#RRGGBB`` hex regex.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from quicksight_gen.common.l2 import L2LoaderError, ThemePreset, load_instance


# A minimal happy-path L2 YAML — single account + zero rails — used as
# the substrate that every theme test prepends a theme block onto.
_BASE_INSTANCE_YAML = dedent("""\
    accounts:
      - id: int-1
        role: Internal
        scope: internal
""")


# A complete theme block — exercises every field the dataclass carries,
# so individual tests can mutate one field at a time.
_FULL_THEME_BLOCK = dedent("""\
    theme:
      theme_name: "Test Theme"
      version_description: "N.1 inline-theme test fixture"
      analysis_name_prefix: "Demo"
      data_colors:
        - "#2D6A4F"
        - "#C49A2A"
        - "#5C4033"
      empty_fill_color: "#D6D6CE"
      gradient: ["#C5DDD3", "#1B4332"]
      primary_bg: "#FFFFFF"
      secondary_bg: "#FAF6F1"
      primary_fg: "#3D3D3A"
      secondary_fg: "#52796F"
      accent: "#2D6A4F"
      accent_fg: "#FFFFFF"
      link_tint: "#E8F1EB"
      danger: "#B71C1C"
      danger_fg: "#FFFFFF"
      warning: "#BF6D0A"
      warning_fg: "#FFFFFF"
      success: "#2D6A4F"
      success_fg: "#FFFFFF"
      dimension: "#52796F"
      dimension_fg: "#FFFFFF"
      measure: "#1B4332"
      measure_fg: "#FFFFFF"
""")


def _write(tmp_path: Path, body: str) -> Path:
    """Write a YAML file under tmp_path and return the path."""
    p = tmp_path / "instance.yaml"
    p.write_text(body)
    return p


# -- Happy paths --------------------------------------------------------------


def test_theme_omitted_returns_none(tmp_path: Path) -> None:
    """No ``theme:`` block → instance.theme is None (registry fallback)."""
    p = _write(tmp_path, _BASE_INSTANCE_YAML)
    inst = load_instance(p)
    assert inst.theme is None


def test_theme_null_returns_none(tmp_path: Path) -> None:
    """Explicit ``theme: null`` is the same as omitting the block."""
    p = _write(tmp_path, _BASE_INSTANCE_YAML + "theme: null\n")
    inst = load_instance(p)
    assert inst.theme is None


def test_theme_full_block_round_trips(tmp_path: Path) -> None:
    """Every field on the full theme block lands on the dataclass."""
    p = _write(tmp_path, _BASE_INSTANCE_YAML + _FULL_THEME_BLOCK)
    inst = load_instance(p)
    assert isinstance(inst.theme, ThemePreset)
    t = inst.theme
    assert t.theme_name == "Test Theme"
    assert t.version_description == "N.1 inline-theme test fixture"
    assert t.analysis_name_prefix == "Demo"
    assert t.data_colors == ["#2D6A4F", "#C49A2A", "#5C4033"]
    assert t.gradient == ["#C5DDD3", "#1B4332"]
    assert t.empty_fill_color == "#D6D6CE"
    assert t.primary_bg == "#FFFFFF"
    assert t.accent == "#2D6A4F"
    assert t.measure == "#1B4332"
    # Spot-check the rest — full field set already enforced by the YAML
    # _require() calls; here we just prove the kwargs landed.
    assert t.danger == "#B71C1C"
    assert t.success_fg == "#FFFFFF"


def test_theme_analysis_name_prefix_omitted_is_none(tmp_path: Path) -> None:
    """``analysis_name_prefix`` is optional; missing → None on dataclass."""
    block = _FULL_THEME_BLOCK.replace(
        '  analysis_name_prefix: "Demo"\n', "",
    )
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    inst = load_instance(p)
    assert inst.theme is not None
    assert inst.theme.analysis_name_prefix is None


def test_theme_analysis_name_prefix_explicit_null(tmp_path: Path) -> None:
    """Explicit ``analysis_name_prefix: null`` is the same as omitting it."""
    block = _FULL_THEME_BLOCK.replace(
        '  analysis_name_prefix: "Demo"\n',
        "  analysis_name_prefix: null\n",
    )
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    inst = load_instance(p)
    assert inst.theme is not None
    assert inst.theme.analysis_name_prefix is None


# -- Required-field rejections ------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "theme_name",
        "version_description",
        "data_colors",
        "empty_fill_color",
        "gradient",
        "primary_bg",
        "secondary_bg",
        "primary_fg",
        "secondary_fg",
        "accent",
        "accent_fg",
        "link_tint",
        "danger",
        "danger_fg",
        "warning",
        "warning_fg",
        "success",
        "success_fg",
        "dimension",
        "dimension_fg",
        "measure",
        "measure_fg",
    ],
)
def test_theme_required_fields_rejected_when_missing(
    field: str, tmp_path: Path,
) -> None:
    """Every required field surfaces a friendly error citing its path."""
    # Drop the line(s) starting with the field name. ``data_colors``
    # spans multiple YAML lines; remove until the next top-level field
    # (2-space indent for keys post-dedent; deeper indent for child rows).
    block_lines = _FULL_THEME_BLOCK.splitlines(keepends=True)
    out_lines: list[str] = []
    skip = False
    for line in block_lines:
        if skip:
            stripped = line.lstrip(" ")
            depth = len(line) - len(stripped)
            if depth >= 4:
                continue
            skip = False
        if line.startswith(f"  {field}:"):
            skip = True
            continue
        out_lines.append(line)
    mutated_block = "".join(out_lines)
    p = _write(tmp_path, _BASE_INSTANCE_YAML + mutated_block)
    with pytest.raises(L2LoaderError, match=field):
        load_instance(p)


# -- Hex format rejections ----------------------------------------------------


@pytest.mark.parametrize(
    "bad_color",
    [
        "#FFF",          # too short
        "#FFFFFFFF",     # too long
        "FFFFFF",        # missing hash
        "#GGGGGG",       # non-hex chars
        "blue",          # name not hex
        "",              # empty
        "#1B2A4",        # 5 chars after hash
    ],
)
def test_theme_color_field_rejects_bad_hex(
    bad_color: str, tmp_path: Path,
) -> None:
    block = _FULL_THEME_BLOCK.replace(
        'accent: "#2D6A4F"', f'accent: "{bad_color}"',
    )
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    with pytest.raises(L2LoaderError, match="accent"):
        load_instance(p)


def test_theme_color_field_rejects_non_string(tmp_path: Path) -> None:
    block = _FULL_THEME_BLOCK.replace(
        'accent: "#2D6A4F"', "accent: 123456",
    )
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    with pytest.raises(L2LoaderError, match="accent"):
        load_instance(p)


# -- Shape rejections ---------------------------------------------------------


def test_theme_data_colors_empty_rejected(tmp_path: Path) -> None:
    block = dedent("""\
        theme:
          theme_name: "x"
          version_description: "x"
          data_colors: []
          empty_fill_color: "#FFFFFF"
          gradient: ["#FFFFFF", "#000000"]
          primary_bg: "#FFFFFF"
          secondary_bg: "#FFFFFF"
          primary_fg: "#000000"
          secondary_fg: "#000000"
          accent: "#000000"
          accent_fg: "#FFFFFF"
          link_tint: "#FFFFFF"
          danger: "#FF0000"
          danger_fg: "#FFFFFF"
          warning: "#FF8800"
          warning_fg: "#FFFFFF"
          success: "#008800"
          success_fg: "#FFFFFF"
          dimension: "#000000"
          dimension_fg: "#FFFFFF"
          measure: "#000000"
          measure_fg: "#FFFFFF"
    """)
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    with pytest.raises(L2LoaderError, match="data_colors.*at least one"):
        load_instance(p)


@pytest.mark.parametrize("count", [0, 1, 3, 4])
def test_theme_gradient_must_be_exactly_two(count: int, tmp_path: Path) -> None:
    items = ", ".join(f'"#{i:06X}"' for i in range(count))
    block = _FULL_THEME_BLOCK.replace(
        'gradient: ["#C5DDD3", "#1B4332"]', f"gradient: [{items}]",
    )
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    with pytest.raises(L2LoaderError, match="exactly 2"):
        load_instance(p)


def test_theme_top_level_not_mapping_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, _BASE_INSTANCE_YAML + 'theme: "not-a-mapping"\n')
    with pytest.raises(L2LoaderError, match="theme"):
        load_instance(p)


# -- Optional brand assets (logo + favicon) — Phase O ------------------------


def test_theme_logo_and_favicon_default_to_none(tmp_path: Path) -> None:
    """Without ``logo:`` / ``favicon:`` keys, both fields land as None."""
    p = _write(tmp_path, _BASE_INSTANCE_YAML + _FULL_THEME_BLOCK)
    inst = load_instance(p)
    assert inst.theme is not None
    assert inst.theme.logo is None
    assert inst.theme.favicon is None


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/logo.svg",
        "http://example.com/logo.png",
        "//cdn.example.com/logo.svg",
        "/absolute/path/to/logo.svg",
        "/Users/me/branding/favicon.ico",
    ],
)
def test_theme_logo_accepts_url_or_absolute_path(
    tmp_path: Path, value: str,
) -> None:
    block = _FULL_THEME_BLOCK + f'  logo: "{value}"\n'
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    inst = load_instance(p)
    assert inst.theme is not None
    assert inst.theme.logo == value


def test_theme_favicon_round_trips(tmp_path: Path) -> None:
    block = (
        _FULL_THEME_BLOCK
        + '  favicon: "https://example.com/favicon.ico"\n'
    )
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    inst = load_instance(p)
    assert inst.theme is not None
    assert inst.theme.favicon == "https://example.com/favicon.ico"


@pytest.mark.parametrize(
    "value, relative_segments",
    [
        ("img/snb-mark.svg",        ("img", "snb-mark.svg")),
        ("./logo.svg",              ("logo.svg",)),
        ("../branding/logo.svg",    ("..", "branding", "logo.svg")),
        ("logo.svg",                ("logo.svg",)),
    ],
)
def test_theme_logo_relative_paths_resolve_against_yaml_dir(
    tmp_path: Path, value: str, relative_segments: tuple[str, ...],
) -> None:
    """v8.6.10 — relative paths resolve against the YAML's directory.

    Pre-v8.6.10 they were rejected outright (the resolution base was
    ambiguous). The loader now knows the YAML path so it can resolve
    against the file's parent at load time, storing an absolute path
    on the dataclass (the same shape the docs build's
    ``_apply_brand_asset_override`` already handled).
    """
    block = _FULL_THEME_BLOCK + f'  logo: "{value}"\n'
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    inst = load_instance(p)
    assert inst.theme is not None
    expected = (tmp_path.joinpath(*relative_segments)).resolve()
    assert inst.theme.logo == str(expected)


def test_theme_logo_non_string_rejected(tmp_path: Path) -> None:
    block = _FULL_THEME_BLOCK + "  logo: 42\n"
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    with pytest.raises(L2LoaderError, match="must be a string"):
        load_instance(p)


def test_theme_logo_empty_string_treated_as_none(tmp_path: Path) -> None:
    """Whitespace-only / empty value → None (no override)."""
    block = _FULL_THEME_BLOCK + '  logo: ""\n'
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    inst = load_instance(p)
    assert inst.theme is not None
    assert inst.theme.logo is None


def test_theme_explicit_null_logo_is_none(tmp_path: Path) -> None:
    block = _FULL_THEME_BLOCK + "  logo: null\n"
    p = _write(tmp_path, _BASE_INSTANCE_YAML + block)
    inst = load_instance(p)
    assert inst.theme is not None
    assert inst.theme.logo is None
