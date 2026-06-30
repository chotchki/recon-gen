"""DZ.5 — pre-built ``/docs`` mount for studio / dashboards.

``_resolve_handbook_docs_dir`` decides what to serve at ``/docs``:
a pre-built site dir (``--docs-dir`` / ``RECON_GEN_DOCS_SITE_DIR``) with
no build, or a build-on-launch tempdir. The pre-built path is what keeps
the heavy mkdocs build off the launchd demo host's launch path. These
cover the override resolution + the loud-fail on a missing build output;
the build-on-launch branch is exercised by the docs-build smoke tests.

A pre-built result is identifiable structurally: ``docs_dir`` is the
exact dir passed in and ``docs_tmp is None``. The build branch instead
returns a tempdir handle, so ``docs_tmp is None`` alone proves no build
ran.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest

from recon_gen.cli._html_serve import _resolve_handbook_docs_dir


def _make_site(tmp_path: Path, name: str = "site") -> Path:
    """A minimal "built" mkdocs site — just an index.html."""
    site = tmp_path / name
    site.mkdir()
    (site / "index.html").write_text("<html>built</html>")
    return site


def test_prebuilt_dir_is_served_without_building(tmp_path: Path) -> None:
    site = _make_site(tmp_path)
    docs_dir, docs_tmp = _resolve_handbook_docs_dir(
        embed_docs=True,
        docs_site_dir=str(site),
        # A real L2 path is present, yet no build happens — the override wins.
        l2_instance_path=Path("would-build-if-reached.yaml"),
    )
    assert docs_dir == site
    assert docs_tmp is None  # no tempdir → no build ran


def test_prebuilt_overrides_no_docs(tmp_path: Path) -> None:
    """An explicit --docs-dir is a positive request; it beats --no-docs."""
    site = _make_site(tmp_path)
    docs_dir, docs_tmp = _resolve_handbook_docs_dir(
        embed_docs=False, docs_site_dir=str(site), l2_instance_path=None,
    )
    assert docs_dir == site
    assert docs_tmp is None


def test_prebuilt_dir_without_index_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(click.UsageError, match="no index.html"):
        _resolve_handbook_docs_dir(
            embed_docs=True, docs_site_dir=str(empty), l2_instance_path=None,
        )


def test_env_fallback_when_flag_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = _make_site(tmp_path)
    monkeypatch.setenv("RECON_GEN_DOCS_SITE_DIR", str(site))  # typing-smell: ignore[envvar-bypass]: setting the var IS the thing under test (the env-fallback path); the helper reads it via the registry
    docs_dir, _ = _resolve_handbook_docs_dir(
        embed_docs=True, docs_site_dir=None, l2_instance_path=None,
    )
    assert docs_dir == site


def test_cli_flag_wins_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    flag_site = _make_site(tmp_path, "flag")
    env_site = _make_site(tmp_path, "env")
    monkeypatch.setenv("RECON_GEN_DOCS_SITE_DIR", str(env_site))  # typing-smell: ignore[envvar-bypass]: test sets the var to prove the CLI flag wins over it; the helper reads it through the registry
    docs_dir, _ = _resolve_handbook_docs_dir(
        embed_docs=True, docs_site_dir=str(flag_site), l2_instance_path=None,
    )
    assert docs_dir == flag_site


def test_no_override_and_no_docs_skips_build(tmp_path: Path) -> None:
    docs_dir, docs_tmp = _resolve_handbook_docs_dir(
        embed_docs=False,
        docs_site_dir=None,
        l2_instance_path=tmp_path / "some.yaml",
    )
    assert docs_dir is None
    assert docs_tmp is None
