"""Unit tests for ``recon_gen/main.py``'s mkdocs-macros helpers.

The macros entry point (``define_env``) is exercised end-to-end by the
docs build smoke test. These tests cover the small, pure helpers
(``_apply_l2_theme_css``) that mutate the build's CSS surface based on
the active L2 instance's theme block.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from recon_gen.common.l2 import load_instance
from recon_gen.main import _apply_l2_theme_css

if TYPE_CHECKING:
    from recon_gen.common.l2 import ThemePreset


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "tests" / "l2" / "spec_example.yaml"
)


@pytest.fixture
def spec_example_theme() -> "ThemePreset":
    """Load the bundled spec_example L2 — its ``theme:`` block is the
    canonical test fixture for the docs theming code path."""
    inst = load_instance(_SPEC_EXAMPLE)
    assert inst.theme is not None
    return inst.theme


def test_writes_css_with_l2_accent_palette(
    tmp_path: Path, spec_example_theme: "ThemePreset",
) -> None:
    extra_css: list[object] = []
    _apply_l2_theme_css(
        docs_dir=tmp_path, extra_css=extra_css, theme=spec_example_theme,
    )
    css_path = tmp_path / "stylesheets" / "_l2_theme.css"
    assert css_path.is_file()

    css = css_path.read_text()
    accent = spec_example_theme.accent
    accent_fg = spec_example_theme.accent_fg
    # The shim overrides the --qs-* design tokens site.css declares;
    # site.css maps Material's --md-* brand vars onto those, so the L2
    # accent reaches Material's chrome AND the .snb-* / .qs-* rules.
    assert "--qs-accent:" in css and accent in css
    assert "--qs-accent-fg:" in css and accent_fg in css
    # It overrides the --qs-* tokens, not Material's --md-* directly
    # (the pre-X.2.s.2 shape that left site.css's rules hard-coded).
    assert "--md-primary-fg-color" not in css


def test_registers_css_in_extra_css(
    tmp_path: Path, spec_example_theme: "ThemePreset",
) -> None:
    extra_css: list[object] = []
    _apply_l2_theme_css(
        docs_dir=tmp_path, extra_css=extra_css, theme=spec_example_theme,
    )
    assert "stylesheets/_l2_theme.css" in extra_css


def test_idempotent_on_repeat_apply(
    tmp_path: Path, spec_example_theme: "ThemePreset",
) -> None:
    """Calling twice (e.g. mkdocs serve auto-reload) must not double-
    register the stylesheet — Material would load it twice and the
    cascade flagged duplicate ``:root`` blocks would fight each other
    on later edits."""
    extra_css: list[object] = []
    _apply_l2_theme_css(
        docs_dir=tmp_path, extra_css=extra_css, theme=spec_example_theme,
    )
    _apply_l2_theme_css(
        docs_dir=tmp_path, extra_css=extra_css, theme=spec_example_theme,
    )
    assert extra_css.count("stylesheets/_l2_theme.css") == 1


def test_underscore_prefix_keeps_asset_out_of_git(
    tmp_path: Path, spec_example_theme: "ThemePreset",
) -> None:
    """The generated CSS is build output, not source. Same convention as
    the logo / favicon copies (``img/_l2_logo.<ext>``). The
    ``.gitignore`` rule that ignores ``stylesheets/_l2_*`` keeps it
    untracked even though it lives under ``docs/``."""
    extra_css: list[object] = []
    _apply_l2_theme_css(
        docs_dir=tmp_path, extra_css=extra_css, theme=spec_example_theme,
    )
    assert (tmp_path / "stylesheets" / "_l2_theme.css").name.startswith("_l2_")


def test_uses_alternate_theme_palette(
    tmp_path: Path, spec_example_theme: "ThemePreset",
) -> None:
    """The CSS reflects whatever palette is on the passed-in theme —
    not a hardcoded constant. Synthesizes a theme with a recognizable
    bright-pink accent so the assertion can't be coincidence."""
    custom = replace(
        spec_example_theme, accent="#ff00aa", accent_fg="#ffffff",
    )
    extra_css: list[object] = []
    _apply_l2_theme_css(
        docs_dir=tmp_path, extra_css=extra_css, theme=custom,
    )
    css = (tmp_path / "stylesheets" / "_l2_theme.css").read_text()
    assert "#ff00aa" in css
    assert "#ffffff" in css
