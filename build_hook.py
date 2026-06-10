"""Build-time hook — bake git SHA + timestamp + build kind into the wheel.

CY.1 — solves a recurring "git: unknown" footgun in the App Info deploy
stamp. ``recon_gen.common.sheets.app_info._git_short_sha()`` historically
shelled out to ``git rev-parse --short HEAD`` at *generate* time. From a
venv site-packages install (the demo path; also any customer install),
cwd isn't a git checkout → the SHA renders "unknown" on every dashboard
generated from that wheel. Fix: stamp the SHA in at *build* time so the
wheel itself carries the provenance.

Mechanism: a setuptools ``build_py`` cmdclass subclass writes
``src/recon_gen/_build_info.py`` BEFORE the package copy step, so the
generated file ends up inside the wheel like any other source file.
Wired via ``[tool.setuptools.cmdclass]`` in ``pyproject.toml``.

The hook is best-effort:
- ``git_sha`` falls back to ``"unknown"`` when the build env isn't a
  git checkout (e.g., building from an sdist tarball).
- ``build_kind`` is ``"release"`` when the current HEAD is exactly a
  tag matching ``v<__version__>``; otherwise ``"dev"``.
- ``built_at`` is the build wall-clock time in UTC ISO-8601 (seconds).

Editable installs (``uv sync`` / ``pip install -e .``) trigger
``build_py develop`` which uses this same cmdclass — so the dev venv
gets a real SHA after the first ``uv sync``. The runtime fallback in
``_git_short_sha()`` still covers the bootstrap window before the
first sync.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
from pathlib import Path


# Setuptools is only present in the isolated PEP 517 build env — not
# the runtime venv. The pure functions below
# (``compute_build_info`` / ``render_build_info_module`` /
# ``write_build_info``) intentionally don't import setuptools, so the
# unit tests can import this module from the runtime venv. Only the
# cmdclass at the bottom touches setuptools, and we do that import
# lazily inside ``_make_cmdclass``.

_BUILD_INFO_REL_PATH = Path("src") / "recon_gen" / "_build_info.py"


def _run_git(args: list[str], cwd: Path) -> str | None:
    """Run a git command from ``cwd``; return stripped stdout or None."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _read_version(repo_root: Path) -> str | None:
    """Parse ``__version__`` from ``src/recon_gen/__init__.py`` without
    importing the package (avoids forcing the build env to have all
    runtime deps). The init file has a one-line literal assignment."""
    init_path = repo_root / "src" / "recon_gen" / "__init__.py"
    try:
        text = init_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("__version__"):
            # ``__version__ = "13.12.0"``
            parts = stripped.split("=", 1)
            if len(parts) != 2:
                continue
            rhs = parts[1].strip().strip("\"'")
            return rhs or None
    return None


def compute_build_info(repo_root: Path) -> dict[str, str]:
    """Compute the build-info dict written into ``_build_info.py``.

    Pure function so the unit tests can drive it without touching
    setuptools' command machinery.
    """
    git_sha = _run_git(["rev-parse", "--short", "HEAD"], repo_root) or "unknown"
    built_at = _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")

    # build_kind = "release" only when HEAD is exactly v<version>.
    build_kind = "dev"
    version = _read_version(repo_root)
    if version is not None and git_sha != "unknown":
        # ``git tag --points-at HEAD`` lists every tag pointing at HEAD;
        # release iff one of them is exactly ``v<version>``.
        tags_at_head = _run_git(["tag", "--points-at", "HEAD"], repo_root) or ""
        for tag in tags_at_head.splitlines():
            if tag.strip() == f"v{version}":
                build_kind = "release"
                break

    return {
        "git_sha": git_sha,
        "built_at": built_at,
        "build_kind": build_kind,
    }


def render_build_info_module(info: dict[str, str]) -> str:
    """Render the ``_build_info.py`` module text for the given dict.

    Kept pure for testability; the cmdclass writes the result to disk.
    """
    return (
        '"""Build-time-stamped metadata. AUTO-GENERATED — do not edit.\n'
        "\n"
        "Written by ``build_hook.BuildPyWithBuildInfo`` during\n"
        "``python -m build`` / ``uv build`` / ``pip install -e .``. Carries\n"
        "the git short SHA, build wall-clock, and build kind so the App Info\n"
        "deploy stamp can read provenance baked into the wheel itself rather\n"
        "than shelling out to ``git`` at generate time (which fails from a\n"
        "non-checkout cwd — the original CY.1 bug).\n"
        "\n"
        "Editable installs trigger the same hook; if this file is missing\n"
        "at import time (someone hand-installed without running the build),\n"
        "``common/sheets/app_info._git_short_sha()`` falls back to a runtime\n"
        '``git rev-parse`` call.\n'
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "__build_info__: dict[str, str] = {\n"
        f'    "git_sha": {info["git_sha"]!r},\n'
        f'    "built_at": {info["built_at"]!r},\n'
        f'    "build_kind": {info["build_kind"]!r},\n'
        "}\n"
    )


def write_build_info(repo_root: Path) -> Path:
    """Write ``src/recon_gen/_build_info.py`` for the source tree rooted
    at ``repo_root``. Returns the path written.

    Idempotent — overwriting is fine; the module is gitignored.
    """
    info = compute_build_info(repo_root)
    target = repo_root / _BUILD_INFO_REL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_build_info_module(info), encoding="utf-8")
    return target


def _make_cmdclass() -> type:
    """Build the ``BuildPyWithBuildInfo`` class lazily so importing this
    module from a runtime venv (no setuptools installed) doesn't crash.

    The actual class is constructed only when setuptools is on PATH —
    which is exactly the PEP 517 build env case. Returning ``object``
    when setuptools is absent lets `_make_cmdclass()()` instantiate
    something inert, but in practice the cmdclass is only resolved by
    setuptools' own dispatch, so the absent-setuptools branch never
    fires at runtime.
    """
    from setuptools.command.build_py import build_py as _build_py_cls

    class BuildPyWithBuildInfo(_build_py_cls):
        """``build_py`` cmdclass that stamps ``_build_info.py`` before copy.

        Setuptools invokes ``run()`` before walking the package tree to
        copy sources into ``build/lib/``. Writing the file at the top of
        ``run()`` ensures the wheel includes the stamped module.
        """

        def run(self) -> None:  # type: ignore[override]
            # ``self.distribution.src_root`` is None in the common case;
            # the repo root is the cwd setuptools was invoked from.
            repo_root = Path(os.getcwd()).resolve()
            write_build_info(repo_root)
            super().run()

    return BuildPyWithBuildInfo


# Setuptools resolves ``[tool.setuptools.cmdclass] build_py =
# "build_hook.BuildPyWithBuildInfo"`` by importing this module and
# looking up the named attribute. Build it eagerly when setuptools is
# present (PEP 517 build env) and skip when absent (runtime venv import
# from unit tests). The runtime fall-through is None; nothing at
# runtime references the symbol.
try:
    BuildPyWithBuildInfo = _make_cmdclass()
except ImportError:
    BuildPyWithBuildInfo = None  # type: ignore[assignment]: runtime-only fallback
