"""X.2.s.2 regression — the rendered handbook site is theme-driven, not
hard-coded to the bundled SNB palette.

``docs/stylesheets/site.css`` declares neutral ``--qs-*`` design tokens
and maps Material's ``--md-*`` brand vars onto them; ``main.py``'s
mkdocs-macros entry writes a generated ``stylesheets/_l2_theme.css`` that
overrides the ``--qs-*`` tokens from the active L2 instance's ``theme:``
block (loaded after ``site.css`` via ``extra_css``, so it wins). This
test builds the site through the ``docs apply`` CLI — both ``--portable``
and the default pretty-URL build — against an L2 fixture carrying a
distinctive accent and asserts:

1. the built ``stylesheets/_l2_theme.css`` carries that accent on a
   ``--qs-*`` token,
2. ``index.html`` links it (so the override actually loads), and
3. the built ``stylesheets/site.css`` is persona-neutral — references
   ``var(--qs-accent)`` and contains no ``--snb-`` / the old SNB hexes.

The fast wire-shape coverage of ``_apply_l2_theme_css`` itself (the
exact CSS it emits for an arbitrary ThemePreset) lives in
``tests/unit/test_main_macros.py``; this is the build-pipeline check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from click.testing import CliRunner

from quicksight_gen.cli import main as cli_root

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC_EXAMPLE = _REPO_ROOT / "tests" / "l2" / "spec_example.yaml"

# Distinctive enough that "this hex is in the output" can't be a
# coincidence with the neutral DEFAULT_PRESET fallback.
_ACCENT = "#FF00CC"
# An SNB-palette hex that MUST NOT appear in the rendered site.css after
# X.2.s.2 (it was `--snb-valley-deep`).
_OLD_SNB_HEX = "2B4A2E"


def _require_mkdocs() -> None:
    try:
        import mkdocs  # noqa: F401
    except ImportError:  # pragma: no cover — env-specific
        pytest.skip("mkdocs not installed")


@pytest.fixture
def themed_l2(tmp_path: Path) -> Path:
    """A copy of the bundled spec_example L2 with a bright-pink accent."""
    import yaml

    inst = yaml.safe_load(_SPEC_EXAMPLE.read_text())
    # Z.C — top-level `instance:` key is no longer accepted on L2 yamls;
    # the deployment identifiers live on cfg.yaml. Strip it if the bundled
    # spec_example happens to still carry one (defensive).
    inst.pop("instance", None)
    inst["theme"]["accent"] = _ACCENT
    inst["theme"]["accent_fg"] = "#FFFFFF"
    inst["theme"]["dimension"] = "#FF66DD"
    inst["theme"]["measure"] = "#990077"
    inst["theme"]["secondary_bg"] = "#FFF0FA"
    inst["theme"]["link_tint"] = "#FFE0F5"
    path = tmp_path / "themed_l2.yaml"
    path.write_text(yaml.safe_dump(inst, sort_keys=False))
    return path


@pytest.mark.parametrize("portable", [True, False], ids=["portable", "default"])
def test_docs_build_carries_l2_theme_and_site_css_is_persona_neutral(
    tmp_path: Path, themed_l2: Path, portable: bool,
) -> None:
    _require_mkdocs()
    out = tmp_path / ("site-portable" if portable else "site-default")
    argv = ["docs", "apply", "--no-strict", "-o", str(out), "--l2", str(themed_l2)]
    if portable:
        argv.append("--portable")
    result = CliRunner().invoke(cli_root, argv)
    assert result.exit_code == 0, result.output

    l2_css = (out / "stylesheets" / "_l2_theme.css").read_text()
    assert "--qs-accent" in l2_css, l2_css
    assert _ACCENT in l2_css, l2_css

    index = (out / "index.html").read_text(errors="replace")
    assert "stylesheets/_l2_theme.css" in index

    site_css = (out / "stylesheets" / "site.css").read_text()
    assert "var(--qs-accent)" in site_css
    assert "--snb-" not in site_css, "site.css must not hard-code the SNB palette"
    assert _OLD_SNB_HEX not in site_css.upper()
